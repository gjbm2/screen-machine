"""Curl flow: a million particles riding a divergence-free curl-noise wind, drawn as silky glowing streamlines.

Source: Original (inspired by https://www.cs.ubc.ca/~rbridson/docs/bridson-siggraph2007-curlnoise.pdf)
Author: smfx.  Algorithm: Robert Bridson, Jim Hourihan, Marcus Nordenstam, "Curl-noise for procedural fluid
        flow", SIGGRAPH 2007 (velocity = curl of a noise potential, so the flow is divergence-free); the
        time-varying potential uses rotating-gradient "flow noise" (Perlin & Neyret, SIGGRAPH 2001 sketch).
        Implemented from the papers' maths; no code copied.
License: MIT
license_class: permissive

Model: a scalar potential psi(x, y, t) is 1-3 octaves of 2D gradient noise whose lattice gradients spin over time
(each corner at -2, -1, +1 or +2 turns per phase turn, so the host phase wraps seamlessly) and whose domain drifts
slowly.  Velocity = (d psi/dy, -d psi/dx), from analytic noise derivatives (one noise eval per octave), so the
particles neither bunch nor thin out.  On top: an optional central vortex (Lamb-Oseen-like swirl) plus a gentle
attractor (``vortex_mix``), and on the beat a uniform expansion (v += b * p: a radial outward breath whose
dilution is uniform, so it never leaves a hole in the middle).

Drawing: 1M particles (float SoA, launched 2D as 1024 x 1024) live in the disc that covers the screen and
respawn by in-kernel hash when they age out or leave it.  Each frame a particle splats its path segment
(1-6 samples, deposit proportional to path length, so fast currents glow and slow eddies dim) into a float2
accumulator (weight, weight * colour index) with float atomicAdd, once per symmetry copy.  ``symmetry`` s adds
rotational copies at 2 pi i / s (the last one fading in with the fractional part) and, from s = 1 to 2, their
mirror twins, so every integer s >= 2 is an exact dihedral kaleidoscope D_s and 1 is free flow; the active
particle fraction falls as copies are added, so the cost stays flat.  A tone pass decays the accumulator
(the trails), diffuses it a little (``particle_softness``: old trail turns to soft smoke), colours it with the
shared palette (index = velocity direction, as a triangle wave so there is no seam, x period + drift), tonemaps
(1 - exp) and ``present()`` scales it to the output.  The accumulator is sized from ``render_scale`` x 4K, so the
look is identical at every output size.  The exposure divides by the trail's expected steady-state mass
(tracked on the host), so the first frames and trail-length changes keep their brightness.

Macros (e' = smoothstep(0.1, 0.85, energy)):
  energy -> advection rate x(0.4 + 1.2e'), noise evolution rate x(0.4 + 1.2e') and domain drift, octave blend
            +0.5 e'^1.5 (slewed 2 s), active particle density 0.55 + 0.45 e'^1.5 (slewed), brightness
            x(0.6 + 0.4e'), vortex strength x(0.6 + 0.8e').
  pulse  -> k = pulse * (0.3 + 0.7e): a radial outward impulse (an expansion velocity added to the field,
            ``burst_kick``) that decays with the envelope, and a <= +35% brightness flash on the fresh trail
            heads only (the old trails stay put).
  speed  -> sim time: advection, noise clock, drift, particle ageing and trail decay all scale with it.
  palette, palette_period, drift_speed -> pal(direction * period + drift), particles dim with age.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import present

PW, PH = 1024, 1024             # particle grid (N = 1M)
N_PART = PW * PH
MAX_COPIES = 16
OPTIONS = ("--std=c++14", "--use_fast_math")
EXPOSURE = 0.55

# P layout (floats)
#  0 noise freq F | 1 octave-2 weight | 2 octave-3 weight | 3 curl wind speed (units/s) | 4 vortex swirl
#  5 attractor speed | 6 beat expansion rate | 7 spin phase (turns, wrapped 1) | 8..13 per-octave domain offsets
#  14 sim step h (s) | 15 substeps | 16 deposit gain | 17 active fraction | 18 flash | 19 disc radius
#  20 aspect | 21 copies | 22 deposit floor | 23 sim dt (s) | 24 1 / nominal path (px) | 25, 26 colour weights
#  27 curl weight | 28..30 group norm (base, centre excess, centre falloff) | 32..95 copies: (cos, sin, mirror sign, weight) x 16
#  96 exposure | 97 decay | 98 display blur | 99 trail diffusion | 100 period | 101 drift | 102..113 palette
#  114 previous decay | 115 mass x pixels | 116 mass | 117 exposure EMA rate | 118 burst expansion this frame
_SRC = HELPERS + PALETTE_CUDA + r"""
#define TAU 6.2831853f
__device__ __forceinline__ unsigned int lat_hash(int ix, int iy, unsigned int seed) {
    return hash_u((unsigned int)(ix & 4095) * 0x9E3779B1U ^ hash_u((unsigned int)(iy & 4095) * 0x85EBCA77U ^ seed));
}
// gradient of 2D gradient noise whose lattice gradients spin: corner angle = hash + spin * ph (turns),
// spin in {-2,-1,1,2} x octave multiplier, so a host phase wrapped at 1 turn never jumps.
__device__ __forceinline__ float2 corner_grad(int ix, int iy, unsigned int seed, float ph) {
    const unsigned int h = lat_hash(ix, iy, seed);
    const float spin = (float)((int)(h & 3u) - 2 + ((h & 3u) >= 2u ? 1 : 0));    // -2, -1, 1, 2
    float a = (h >> 8) * (1.f / 16777216.f) + spin * ph;
    a = a - floorf(a);
    float s, c;
    __sincosf(TAU * a, &s, &c);
    return make_float2(c, s);
}
__device__ float3 gnoise_grad(float qx, float qy, unsigned int seed, float ph) {
    const float fx = floorf(qx), fy = floorf(qy);
    const int ix = (int)fx, iy = (int)fy;
    const float tx = qx - fx, ty = qy - fy;
    const float ux = tx * tx * tx * (tx * (tx * 6.f - 15.f) + 10.f);
    const float uy = ty * ty * ty * (ty * (ty * 6.f - 15.f) + 10.f);
    const float dux = 30.f * tx * tx * (tx * (tx - 2.f) + 1.f);
    const float duy = 30.f * ty * ty * (ty * (ty - 2.f) + 1.f);
    const float2 ga = corner_grad(ix, iy, seed, ph), gb = corner_grad(ix + 1, iy, seed, ph);
    const float2 gc = corner_grad(ix, iy + 1, seed, ph), gd = corner_grad(ix + 1, iy + 1, seed, ph);
    const float va = ga.x * tx + ga.y * ty, vb = gb.x * (tx - 1.f) + gb.y * ty;
    const float vc = gc.x * tx + gc.y * (ty - 1.f), vd = gd.x * (tx - 1.f) + gd.y * (ty - 1.f);
    const float k = va - vb - vc + vd;
    const float dx = ga.x + ux * (gb.x - ga.x) + uy * (gc.x - ga.x) + ux * uy * (ga.x - gb.x - gc.x + gd.x)
                     + dux * (vb - va + uy * k);
    const float dy = ga.y + ux * (gb.y - ga.y) + uy * (gc.y - ga.y) + ux * uy * (ga.y - gb.y - gc.y + gd.y)
                     + duy * (vc - va + ux * k);
    return make_float3(dx, dy, va + ux * (vb - va) + uy * (vc - va) + ux * uy * k);
}
// velocity (units/s, y-down screen units: y in [-1, 1] spans the height) and, in .z, the stream function psi
// (normalised, ~[-0.8, 0.8]): it is constant along streamlines, so colouring by it paints ribbons along the flow.
// Symmetry: psi = sum over the copy group g (rotations R_i, mirror twins R_i M) of w_g psi0(g p), so the flow is
// exactly D_s-symmetric at integer s and the particles' splat copies land on real streamlines.
__device__ float3 flow_vel(float x, float y, const float* __restrict__ P, unsigned int seed) {
    const float F = P[0], ph = P[7];
    // octave o: frequency F * m_o, domain rotated by r_o, velocity weight a_o (psi amplitude a_o / (F m_o))
    const float M[3] = {1.f, 2.13f, 4.37f};
    const float CR[3] = {1.f, 0.7648f, -0.3233f}, SR[3] = {0.f, 0.6442f, 0.9463f};
    const float A[3] = {1.f, 0.62f, 0.40f};
    const int nc = (int)P[21];
    float gxs = 0.f, gys = 0.f, psi = 0.f;
    for (int c = 0; c < nc; ++c) {
        const float* C = P + 32 + 4 * c;
        const float mx = x * C[2];
        const float qx = C[0] * mx - C[1] * y, qy = C[1] * mx + C[0] * y;      // q = R M p
        float ax = 0.f, ay = 0.f;
        #pragma unroll
        for (int o = 0; o < 3; ++o) {
            const float w = o == 0 ? 1.f : P[o];
            if (w <= 0.f) continue;
            const float f = F * M[o];
            const float rx = CR[o] * qx - SR[o] * qy, ry = SR[o] * qx + CR[o] * qy;
            const float3 g = gnoise_grad(rx * f + P[8 + 2 * o], ry * f + P[9 + 2 * o], seed + 1013u * o,
                                         ph * (float)(o + 1));
            ax += w * A[o] * (CR[o] * g.x + SR[o] * g.y);                        // d psi0 / dq
            ay += w * A[o] * (-SR[o] * g.x + CR[o] * g.y);
            psi += C[3] * w * A[o] * g.z / M[o];
        }
        // d psi0(R M p) / dp = M R^T (d psi0 / dq)
        gxs += C[3] * C[2] * (C[0] * ax + C[1] * ay);
        gys += C[3] * (-C[1] * ax + C[0] * ay);
    }
    // normalise the group sum: sum w^2 far out (copies uncorrelated), (sum w)^2 near the centre where the copies
    // sit within a noise cell of each other (else the middle spins and glows far hotter than the rest)
    const float r2 = x * x + y * y, r = sqrtf(r2);
    const float nrm = P[28] * rsqrtf(sqrtf(1.f + P[29] * __expf(-r2 * P[30])));
    const float vs = P[3] * P[27] * nrm / (1.f + 0.3f * (P[1] + P[2]));
    float vx = gys * vs, vy = -gxs * vs;
    psi *= nrm;
    const float sw = P[4] / (r2 + 0.06f);                 // central vortex (smooth core)
    vx += -y * sw; vy += x * sw;
    psi += 0.45f * P[4] / fmaxf(P[3], 1e-4f) * __logf(r2 + 0.06f);  // the vortex's own stream function
    const float at = P[5] / (r + 0.2f);                   // attractor (inward)
    vx -= x * at; vy -= y * at;
    return make_float3(vx, vy, psi);
}
__device__ __forceinline__ float splat(float* __restrict__ acc, float x, float y, float w, float wi,
                                       const float* __restrict__ P, int W, int H) {
    const float hh = 0.5f * H, cx = 0.5f * W;
    const int nc = (int)P[21];
    float sum = 0.f;
    for (int c = 0; c < nc; ++c) {
        const float* C = P + 32 + 4 * c;
        const float mx = x * C[2];
        const float qx = C[0] * mx - C[1] * y, qy = C[1] * mx + C[0] * y;
        const int ix = (int)floorf(cx + qx * hh), iy = (int)floorf(hh + qy * hh);
        if ((unsigned int)ix >= (unsigned int)W || (unsigned int)iy >= (unsigned int)H) continue;
        const int k = 2 * (iy * W + ix);
        atomicAdd(acc + k, w * C[3]);
        atomicAdd(acc + k + 1, wi * C[3]);
        sum += w * C[3];
    }
    return sum;
}
extern "C" __global__
void curl_step(float* __restrict__ PX, float* __restrict__ PY, float* __restrict__ AGE, float* __restrict__ LIFE,
               float* __restrict__ acc, float* __restrict__ S, const int W, const int H, const int NW,
               const float* __restrict__ P, const unsigned int seed, const unsigned int frame)
{
    // NW is a multiple of 32 and the grid is exact, so every lane is a particle (the warp sum needs all lanes)
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    const int i = gy * NW + gx;
    float x = PX[i], y = PY[i], age = AGE[i], life = LIFE[i];
    const unsigned int u = (unsigned int)i;
    const float R = P[19];
    // active fraction (density): inactive particles only age (the flow keeps the density uniform, so they are
    // already well placed when they fade back in); the soft band is relative so tiny fractions still fade
    const float hact = hash1(u, 77u, seed);
    const float act0 = clamp01((P[17] - hact) / (0.2f * P[17] + 1e-6f));
    const float x0 = x, y0 = y;
    const int ns = act0 > 0.f ? (int)P[15] : 0;
    const float h = P[14];
    float3 v = make_float3(0.f, 0.f, 0.f);
    for (int s = 0; s < ns; ++s) {                       // advect (Euler substeps; one per frame normally)
        v = flow_vel(x, y, P, seed);
        x += v.x * h; y += v.y * h;
    }
    age += P[23];
    const float r2 = x * x + y * y;
    float act = act0;
    if (age > life || r2 > R * R || (P[5] > 0.f && r2 < 0.0025f)) {     // respawn uniformly in the disc
        const float rr = R * sqrtf(hash1(u, frame, seed + 7u));
        float sa, ca;
        __sincosf(TAU * hash1(u, frame, seed + 8u), &sa, &ca);
        PX[i] = rr * ca; PY[i] = rr * sa;
        AGE[i] = 0.f;
        LIFE[i] = 3.f + 6.f * hash1(u, frame, seed + 9u);
        act = 0.f;                                       // no streak across the screen
    } else {
        // beat burst (radial outward impulse): the particle and its trail expand together (the tone pass
        // resamples the trails by the same 1 + P[118]), so the streaks breathe outwards without kinks
        if (ns > 0) { PX[i] = x * (1.f + P[118]); PY[i] = y * (1.f + P[118]); }
        AGE[i] = age;
    }
    float dep = 0.f;
    if (act > 0.f) {
        const float fade = smooth(0.f, 0.5f, age) * smooth(life, life - 1.2f, age)
                           * (0.55f + 0.45f * smooth(life, 0.f, age));        // fade in, fade out, dim with age
        if (fade > 0.f) {
            const float hb = hash1(u, 91u, seed);
            const float hb2 = hb * hb, hb4 = hb2 * hb2, hb8 = hb4 * hb4;
            const float bright = 0.25f + 3.f * hb8 * hb2;      // heavy tail: a few % carry bright filaments
            const float hh = 0.5f * H;
            if (ns == 0) v = flow_vel(x, y, P, seed);         // first frame: a dot worth one nominal step
            const float fx = v.x, fy = v.y;
            const float Lpx = ns == 0 ? sqrtf(fx * fx + fy * fy) * P[23] * hh
                                      : sqrtf((x - x0) * (x - x0) + (y - y0) * (y - y0)) * hh;
            const float Lseg = Lpx;
            // colour index: stream function (ribbons along the flow) + direction of travel as a triangle wave
            // (no seam) + a small per-particle offset
            const float ang = atan2f(fy, fx + 1e-9f);
            const float idx = v.z * P[25] + fabsf(ang) * (1.f / 3.14159265f) * P[26] + 0.08f * hash1(u, 93u, seed);
            // fast currents glow, slow eddies fall dark: deposit ~ path length x relative speed^1.5 (+ a floor)
            const float vrel = fminf(Lpx * P[24], 2.5f);
            const float total = P[16] * bright * fade * act * P[18] * (Lpx * vrel * sqrtf(vrel) + P[22]);
            const int n = min(max((int)ceilf(Lseg), 1), 6);
            const float w = total / n;
            for (int j = 0; j < n; ++j) {
                const float t = (j + 0.5f) / n;
                dep += splat(acc, x0 + (x - x0) * t, y0 + (y - y0) * t, w, w * idx, P, W, H);
            }
        }
    }
    // the accumulator's mass, tracked on the GPU (warp sums -> one atomic each) for the exposure
    #pragma unroll
    for (int o = 16; o > 0; o >>= 1) dep += __shfl_down_sync(0xffffffffu, dep, o);
    if ((threadIdx.x & 31) == 0 && dep > 0.f) atomicAdd(S, dep);
}
// exposure (one thread): S[0] new deposits, S[1] accumulator mass, S[2] slow EMA of the deposit rate per pixel
// per sim second, S[3] exposure.  The EMA (~1 s) passes beat flashes through but normalises density, symmetry,
// speed and trail length; the first frame initialises it, so there is no fade-in.
extern "C" __global__
void curl_stats(float* __restrict__ S, const float* __restrict__ P)
{
    if (threadIdx.x != 0 || blockIdx.x != 0) return;
    const float tot = S[1] * P[114] + S[0];
    S[1] = tot; S[0] = 0.f;
    const float rate = tot / fmaxf(P[115], 1e-9f);
    S[2] = S[2] > 0.f ? S[2] + (rate - S[2]) * P[117] : rate;
    S[3] = P[96] / __powf(fmaxf(S[2] * P[116], 1e-6f), 0.8f);
}
// tone: display = blur-mixed accumulator -> palette -> 1 - exp tonemap; next accumulator = diffused * decay
extern "C" __global__
void curl_tone(const float* __restrict__ A, float* __restrict__ B, uchar4* __restrict__ img, const int W,
               const int H, const float* __restrict__ P, const float* __restrict__ S)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, W - 1), ym = max(y - 1, 0), yp = min(y + 1, H - 1);
    const float2* A2 = (const float2*)A;
    const float2 c = A2[y * W + x];
    float2 b;
    {
        const float2 n0 = A2[ym * W + xm], n1 = A2[ym * W + x], n2 = A2[ym * W + xp];
        const float2 n3 = A2[y * W + xm], n5 = A2[y * W + xp];
        const float2 n6 = A2[yp * W + xm], n7 = A2[yp * W + x], n8 = A2[yp * W + xp];
        b.x = (4.f * c.x + 2.f * (n1.x + n3.x + n5.x + n7.x) + n0.x + n2.x + n6.x + n8.x) * (1.f / 16.f);
        b.y = (4.f * c.y + 2.f * (n1.y + n3.y + n5.y + n7.y) + n0.y + n2.y + n6.y + n8.y) * (1.f / 16.f);
    }
    const float kd = P[98], kf = P[99], dec = P[97];
    const float dx = c.x + (b.x - c.x) * kd, dy = c.y + (b.y - c.y) * kd;
    float2 src = c;
    if (P[118] > 0.f) {
        // beat burst: the trails expand with the particles (sampled from slightly nearer the centre), so the
        // impulse breathes the whole picture outwards instead of kinking every streak
        const float k = 1.f / (1.f + P[118]);
        const float fx = 0.5f * W + (x + 0.5f - 0.5f * W) * k - 0.5f, fy = 0.5f * H + (y + 0.5f - 0.5f * H) * k - 0.5f;
        const int x0 = min(max((int)floorf(fx), 0), W - 2), y0 = min(max((int)floorf(fy), 0), H - 2);
        const float tx = clamp01(fx - x0), ty = clamp01(fy - y0);
        const float2 a00 = A2[y0 * W + x0], a10 = A2[y0 * W + x0 + 1];
        const float2 a01 = A2[(y0 + 1) * W + x0], a11 = A2[(y0 + 1) * W + x0 + 1];
        src.x = (1.f - ty) * ((1.f - tx) * a00.x + tx * a10.x) + ty * ((1.f - tx) * a01.x + tx * a11.x);
        src.y = (1.f - ty) * ((1.f - tx) * a00.y + tx * a10.y) + ty * ((1.f - tx) * a01.y + tx * a11.y);
    }
    // convex mix of the (burst-resampled) trail and its local blur: both operators have gain <= 1 at every
    // frequency, so the trail buffer can never ring up (src + kf (b - c) with src != c is unstable at Nyquist)
    ((float2*)B)[y * W + x] = make_float2(((1.f - kf) * src.x + kf * b.x) * dec, ((1.f - kf) * src.y + kf * b.y) * dec);
    const float I = dx * S[3];
    const float idx = dy / fmaxf(dx, 1e-12f);
    const float3 s = pal(idx * P[100] + P[101], P + 102);
    // sRGB -> linear (approx), light, 1 - exp tonemap, back to sRGB
    const float lr = s.x * s.x * (0.25f + 0.75f * s.x), lg = s.y * s.y * (0.25f + 0.75f * s.y);
    const float lb = s.z * s.z * (0.25f + 0.75f * s.z);
    const float base = 0.02f;                             // keep hue in deep blacks (no grey)
    const float r = 1.f - __expf(-(lr + base) * I), g = 1.f - __expf(-(lg + base) * I);
    const float bl = 1.f - __expf(-(lb + base) * I);
    img[y * W + x] = make_uchar4((unsigned char)(255.f * __powf(clamp01(r), 0.4545f) + 0.5f),
                                 (unsigned char)(255.f * __powf(clamp01(g), 0.4545f) + 0.5f),
                                 (unsigned char)(255.f * __powf(clamp01(bl), 0.4545f) + 0.5f), 255);
}
"""


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class CurlParticles(Source):
    name = "curl_particles"
    label = "Curl flow"
    description = "A million particles ride a divergence-free curl-noise wind as silky glowing streamlines."
    params = {
        "trail_length": num(0.0, 1.0, 0.75, "Trail length", tier="regional", basic=True),
        "symmetry": num(1.0, 8.0, 1.0, "Symmetry (mirrored copies)", step=0.05, tier="regional", basic=True),
        "flow_scale": num(0.3, 4.0, 1.1, "Flow scale (eddies per screen)", step=0.05, tier="regional"),
        "flow_complexity": num(1.0, 3.0, 1.6, "Flow complexity (octaves)", step=0.05, tier="regional"),
        "density": num(0.0, 1.0, 0.2, "Particle density", tier="regional"),
        "vortex_mix": num(0.0, 1.0, 0.0, "Central vortex", tier="regional"),
        "particle_softness": num(0.0, 1.0, 0.35, "Softness (smoky trails)", tier="regional"),
        "pulse_kick": num(0.0, 2.0, 1.0, "Beat response (lurch + burst)", step=0.05, tier="regional"),
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        rs = float(params["render_scale"])
        # accumulator sized from the 4K reference (so the look does not depend on the output size)
        self.ah = max(64, int(round(2160 * rs / 2)) * 2)
        self.aw = max(64, int(round(self.ah * ctx.width / ctx.height / 2)) * 2)
        self.aspect = self.aw / self.ah
        self.R = math.sqrt(self.aspect ** 2 + 1.0) * 1.02       # particle disc: covers the screen at any rotation
        rng = np.random.default_rng(int(params["seed"]))
        r = self.R * np.sqrt(rng.uniform(0.0, 1.0, N_PART))
        a = rng.uniform(0.0, 2.0 * math.pi, N_PART)
        life = rng.uniform(3.0, 9.0, N_PART)
        self.px = cp.asarray((r * np.cos(a)).astype(np.float32))
        self.py = cp.asarray((r * np.sin(a)).astype(np.float32))
        self.life = cp.asarray(life.astype(np.float32))
        self.age = cp.asarray((life * rng.uniform(0.0, 1.0, N_PART)).astype(np.float32))
        self.accA = cp.zeros(self.aw * self.ah * 2, cp.float32)
        self.accB = cp.zeros(self.aw * self.ah * 2, cp.float32)
        self.img = cp.zeros((self.ah, self.aw, 4), cp.uint8)
        self.S = cp.zeros(8, cp.float32)
        mod = raw_module(_SRC, OPTIONS)
        self.k_step = mod.get_function("curl_step")
        self.k_stats = mod.get_function("curl_stats")
        self.k_tone = mod.get_function("curl_tone")
        self.P = cp.zeros(128, cp.float32)
        self.Ph = np.zeros(128, np.float32)
        self.spin = 0.0                    # gradient spin phase (turns, wrapped at 1)
        self.off = np.array(rng.uniform(0, 64, 6), np.float64)    # per-octave domain drift (lattice units)
        self.drift = 0.0
        self.mass = 0.0                    # trail mass for a unit deposit rate (sim seconds)
        self.decay_prev = 1.0              # decay the last tone pass applied
        self.e_slow = None
        self.frame = 0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        ep = _smoothstep(0.1, 0.85, e)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * (0.3 + 0.7 * e)
        spd = max(float(p["speed"]), 0.0)
        first = dt <= 0.0
        dts = (1.0 / 60.0 if first else dt) * spd          # sim seconds this frame
        # density / complexity follow a slewed energy (no sudden re-structuring)
        a = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 1.0
        self.e_slow = ep if self.e_slow is None else self.e_slow + (ep - self.e_slow) * a
        es = self.e_slow
        rate = 0.4 + 1.2 * ep
        g = float(p["pulse_kick"])
        lurch = 1.2 * g * k                                  # beat: advection rate kick (lurch, then ease out)
        F = float(p["flow_scale"])
        if not first:
            self.spin = _wrap(self.spin + 0.045 * rate * (1.0 + 0.5 * lurch) * dts, 1.0)
            for o, (dx, dy) in enumerate(((0.05, 0.03), (-0.04, 0.05), (0.03, -0.06))):
                self.off[2 * o] += dx * rate * dts * F * (1 + o)
                self.off[2 * o + 1] += dy * rate * dts * F * (1 + o)
            self.drift = _wrap(self.drift + float(p["drift_speed"]) * dts, 1000.0)
        # substeps: one per 1/60 s of sim time, at most 3 (the first frame only deposits)
        ns = 0 if first else min(3, max(1, int(math.ceil(dts * 60.0 - 1e-3))))
        h = dts / ns if ns else 0.0
        cx = float(p["flow_complexity"]) + 0.5 * es ** 1.5
        w2 = min(max(cx - 1.0, 0.0), 1.0)
        w3 = min(max(cx - 2.0, 0.0), 1.0)
        vm = float(p["vortex_mix"])
        vamp = 0.6 + 0.8 * ep
        # symmetry group: rotations at 2 pi i / s (the last fading in with the fractional part) and, over s in
        # [1, 2], their mirror twins: integer s >= 2 is exactly D_s, s = 1 is free flow
        s = float(p["symmetry"])
        n = max(1, int(math.ceil(s - 1e-4)))
        m = min(max(s - 1.0, 0.0), 1.0)
        copies = []
        for i in range(n):
            wi = 1.0 if i < n - 1 else s - (n - 1)
            ang = 2.0 * math.pi * i / s
            c, sn = math.cos(ang), math.sin(ang)
            copies.append((c, sn, 1.0, wi))
            if m > 1e-4:
                copies.append((c, sn, -1.0, wi * m))
        copies = copies[:MAX_COPIES]
        wsum = sum(c[3] for c in copies)
        dens = 0.55 + 0.45 * es ** 1.5
        fd = 0.03 + 0.97 * float(p["density"]) ** 2
        active = fd * dens / wsum ** 0.85                   # more copies -> fewer particles: flat cost
        # trails: decay time constant in sim seconds; mass = the trail's integral for a unit deposit rate
        tau = 0.08 * 25.0 ** float(p["trail_length"])
        decay = math.exp(-dts * rate / tau)                  # per unit of flow: trails keep their length
        self.mass = self.mass * self.decay_prev + dts
        soft = float(p["particle_softness"])
        kf = 1.0 - (1.0 - 0.5 * soft) ** (dts * 60.0)       # trail diffusion per frame (smoke)
        kd = 0.15 + 0.75 * soft                              # display blur
        P = self.Ph
        P[:] = 0.0
        P[0] = F
        P[1], P[2] = w2, w3
        adv = rate * (1.0 + lurch)
        P[3] = 0.34 * adv                                    # curl wind speed (units/s)
        P[4] = 0.24 * vm * vamp * adv                        # central vortex swirl
        P[5] = 0.05 * vm * adv                               # attractor
        P[6] = 0.3 * g * k                                   # beat: radial outward impulse (expansion rate)
        P[7] = self.spin
        P[8:14] = np.mod(self.off, 4096.0)                   # the lattice hash is 4096-periodic: seamless
        P[14] = h
        P[15] = ns
        P[16] = (540.0 / self.ah) / fd ** 0.8                # deposit gain (path length is in pixels)
        P[17] = active
        P[18] = 1.0 + 0.35 * min(g, 1.0) * k                 # beat flash on the fresh trail heads only
        P[19] = self.R
        P[20] = self.aspect
        P[21] = len(copies)
        P[22] = 1.5 * dts * self.ah / 1080.0                 # deposit floor (px-equivalent per frame)
        P[23] = dts
        P[24] = 1.0 / max(0.34 * adv * dts * self.ah * 0.5, 1e-6) if dts > 0 else 0.0    # 1 / nominal path (px)
        P[25], P[26] = 1.1, 0.15                             # colour index: stream function + direction
        P[27] = 1.0 - 0.55 * vm                              # the curl wind calms as the vortex takes over
        s2 = sum(c[3] ** 2 for c in copies)
        P[28] = 1.0 / math.sqrt(s2)
        P[29] = wsum ** 2 / s2 - 1.0
        P[30] = 5.0 * (2.0 * math.sin(math.pi / max(len(copies), 2)) * F) ** 2
        for i, cpy in enumerate(copies):
            P[32 + 4 * i: 36 + 4 * i] = cpy
        P[96] = EXPOSURE * float(p["intensity"]) * (0.6 + 0.4 * ep)
        P[97] = decay
        P[98] = kd
        P[99] = kf
        P[100] = float(p["palette_period"])
        P[101] = self.drift
        P[102:114] = palette_coeffs(p["palette"])
        P[114] = self.decay_prev
        P[115] = self.mass * self.aw * self.ah
        P[116] = self.mass
        P[117] = 1.0 - math.exp(-dt / 1.0) if dt > 0 else 1.0
        P[118] = P[6] * dts                                  # burst: trail expansion this frame
        self.P.set(P)
        self.decay_prev = decay
        self.frame = (self.frame + 1) & 0x7FFFFFFF
        launch_2d(self.k_step, PW, PH, (self.px, self.py, self.age, self.life, self.accA, self.S, np.int32(self.aw),
                                        np.int32(self.ah), np.int32(PW), self.P, np.uint32(int(p["seed"])),
                                        np.uint32(self.frame)))
        self.k_stats((1, 1, 1), (32, 1, 1), (self.S, self.P))
        launch_2d(self.k_tone, self.aw, self.ah, (self.accA, self.accB, self.img, np.int32(self.aw),
                                                 np.int32(self.ah), self.P, self.S))
        self.accA, self.accB = self.accB, self.accA
        present(self.ctx, self.img, out)
