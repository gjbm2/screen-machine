"""Ripple tank: oscillating dippers, a plane-wave bar, a double slit, a glass lens and an elliptical mirror on a
damped 2D wave-equation grid, rendered as lit water, glowing interference fringes or false colour.

Source: Original (inspired by https://en.wikipedia.org/wiki/Ripple_tank and https://en.wikipedia.org/wiki/Wave_equation)
Author: smfx (original; damped wave equation; xscreensaver ripples and interference are permissive references)
License: MIT (original)
license_class: permissive

Written from scratch (no code copied).  Simulation on a fixed ~960x540 grid (540 rows, columns follow the output
aspect), independent of the output size:
  * leapfrog damped wave equation, two height buffers updated in place:
        h' = h + keep (h - h_prev) + c2 * lap(h) + A sin(phi) + B cos(phi) + drops
    c2 <= 0.45 (CFL-safe for the 5-point stencil); ``keep`` = 1 - (damping + an absorbing sponge that ramps up
    over the last 36 cells at the edges); walls multiply h' by (1 - wall), which makes them reflecting (Dirichlet);
    the lens is a region of lower c2 (wave speed x0.67, so it refracts and focuses).
  * every source is a soft additive gaussian dipper (sigma 2.2 cells) with its own phase offset theta, folded per
    frame into a per-cell quadrature pair (A, B) = sum w g (cos theta, sin theta); the line source is a gaussian
    bar.  A ``mask`` kernel rebuilds (c2, keep, A, B) and the wall field once per frame from the eased params, so
    every regional knob morphs continuously (weights fade, walls go through a soft absorbing state mid-ease).
  * the grid steps at 140 x (0.7 + 0.6 e) x speed steps per second (accumulated on the host; <= 8 per frame) and
    the frame shows the state interpolated between the last two steps, so the waves glide without step judder.
  * a time-averaged intensity I = EMA(h^2) gives the interference envelope (bright antinodal fringes, dark nodal
    lines); its spatial RMS (block reduction + atomicAdd, one-thread EMA on the device, no host round trip) is the
    auto exposure, measured per unit source amplitude so energy still reads as amplitude.

Render (at render_scale; 1.0 by default, the field is band-limited so bilinear lookup of the per-cell prep is
smooth): heights, slopes and the curvature are normalised by the exposure and by the wavelength, then
  * water: a palette-tinted tank floor (two octaves of value noise) seen through the surface (refraction by the
    slope), bright caustic crests from the curvature, a tinted crest glow and a specular glint;
  * fringes: dark tank, the envelope as glowing bands, travelling crests modulating them, white-hot antinodes;
  * false colour: signed height through the palette (+ and - lobes on opposite sides of the palette).
Walls are drawn as dark slabs with a lit rim, the lens as a faint glass outline, the dippers as glowing points.

Regional: ``source_layout`` (basic) cross-fades 2 points -> ring -> line + double slit by continuous source and
wall weights; ``render_style`` (basic) water -> fringes -> false colour; ``wavelength``; ``damping``;
``barrier_geometry`` open -> glass lens -> elliptical mirror; ``symmetry`` (ring dipper count, fractional = the
last one fading, and the N-fold symmetry of beat drops); ``twist`` (phase step between dippers: steers the
point-pair beams, turns the ring focus into a rotating spiral); ``drop_kick``.

Macros: energy -> source amplitude (0.45 + 0.55 e), frequency (wavelength x (1.15 - 0.4 e)), sim rate
x(0.7 + 0.6 e), active source count (an outer pair and an inner ring fade in), a slow beam-steer wander, ring
rotation, fringe gain and brightness.  pulse -> on a host-detected rising edge, N-fold symmetric drops (velocity
impulses) splash into the tank; continuously a source-amplitude kick x(1 + 0.8 k) and a +12 % brightness lift
(k = pulse (0.35 + 0.65 e)).  speed scales the source phases, the step rate and every wander (0 freezes the tank);
palette index = amplitude x period + drift; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

GRID_H = 540                     # simulation rows (columns follow the output aspect)
C2 = 0.45                        # (c dt / dx)^2, CFL-safe for the 5-point stencil (< 0.5)
MAXS = 32                        # max point sources
MAXD = 12                        # max drops per beat
MAX_STEPS = 8                    # sim steps per rendered frame
SRC_SIGMA = 2.2                  # dipper radius (cells)
DROP_SIGMA = 5.0                 # beat drop radius (cells)
SPONGE_W = 36.0                  # absorbing edge width (cells)
NP = 256                         # parameter block (floats)

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 c2 base | 15 base damping | 16 cells per unit | 17 aspect
#  18 wP | 19 wR | 20 wL (layout weights) | 21 wLens | 22 wMirror | 23 point forcing amp | 24 line forcing amp
#  25 line x | 26 source sigma (cells) | 27 slit wall x | 28 slit half separation | 29 slit half width
#  30 wall half thickness | 31 lens centre u | 32 lens disc radius | 33 lens disc offset | 34 lens strength
#  35 ellipse a | 36 ellipse b | 37 n sources | 38 sponge width | 39 sponge max | 40 n drops | 41 drop amp
#  42 drop sigma (cells) | 43 forcing unit (glow normaliser) | 44..67 drops (u, v) x 12
#  68 wWater | 69 wFringe | 70 wFalse | 71 display scale | 72 wavelength (cells) | 73 interpolation
#  74 output gain | 75 intensity EMA alpha per step | 76 exposure EMA alpha | 77 source amplitude (energy)
#  78 crest glow | 79 fringe gain | 80 cell count | 81 source glow gain
#  128 + 4k: source k (u, v, weight, phase offset)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAXS %(maxs)d
#define MAXD %(maxd)d

// a palette colour kept bright for emissive light: a dark phase folds towards the opposite side of the palette
// (2a - c), then the strongest channel is lifted towards 1
__device__ __forceinline__ float3 bpal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.1f, 0.42f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.55f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}

__device__ __forceinline__ float4 samp4(const float4* __restrict__ F, int gw, int gh, float fx, float fy) {
    fx -= 0.5f; fy -= 0.5f;
    const int i0 = min(max((int)floorf(fx), 0), gw - 2), j0 = min(max((int)floorf(fy), 0), gh - 2);
    const float tx = clamp01(fx - i0), ty = clamp01(fy - j0);
    const float4 a = F[j0 * gw + i0], b = F[j0 * gw + i0 + 1], c = F[(j0 + 1) * gw + i0], d = F[(j0 + 1) * gw + i0 + 1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}

// per frame: medium (c2, keep), forcing quadrature (A, B) and the wall field, from the eased params
extern "C" __global__
void rt_mask(float4* __restrict__ M, float* __restrict__ WL, const int GW, const int GH, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= GW || j >= GH) return;
    const float cpu = P[16];
    const float u = (i + 0.5f - 0.5f * GW) / cpu, v = (j + 0.5f - 0.5f * GH) / cpu;
    const float sg = P[26], s2 = 2.f * sg * sg;
    // dippers
    float A = 0.f, B = 0.f;
    const int ns = min((int)P[37], MAXS);
    for (int k = 0; k < ns; ++k) {
        const float* S = P + 128 + 4 * k;
        if (S[2] <= 0.f) continue;
        const float du = (u - S[0]) * cpu, dv = (v - S[1]) * cpu;
        const float r2 = du * du + dv * dv;
        if (r2 > 18.f * s2) continue;
        const float g = S[2] * __expf(-r2 / s2);
        float sn, cs;
        __sincosf(S[3], &sn, &cs);
        A += g * cs;
        B += g * sn;
    }
    A *= P[23];
    B *= P[23];
    if (P[24] > 0.f) {                                     // plane-wave bar
        const float dx = (u - P[25]) * cpu;
        A += P[24] * __expf(-dx * dx / s2);
    }
    // walls: the double-slit barrier (line layout) and the elliptical mirror
    float wall = 0.f;
    if (P[20] > 0.f) {
        const float dx = (fabsf(u - P[27]) - P[30]) * cpu;
        const float dy = (fabsf(fabsf(v) - P[28]) - P[29]) * cpu;
        wall = P[20] * (1.f - smooth(-0.8f, 0.8f, dx)) * smooth(-0.8f, 0.8f, dy);
    }
    if (P[22] > 0.f) {
        const float a = P[35], b = P[36];
        const float q = sqrtf(u * u / (a * a) + v * v / (b * b));
        const float sd = (q - 1.f) * fminf(a, b) * cpu;
        wall = fmaxf(wall, P[22] * smooth(-1.f, 1.f, sd));
    }
    // glass lens: slower medium inside the intersection of two discs
    float c2 = P[14];
    if (P[21] > 0.f) {
        const float du = u - P[31];
        const float d1 = sqrtf((du - P[33]) * (du - P[33]) + v * v) - P[32];
        const float d2 = sqrtf((du + P[33]) * (du + P[33]) + v * v) - P[32];
        const float inside = 1.f - smooth(-1.5f, 1.5f, fmaxf(d1, d2) * cpu);
        c2 *= 1.f - P[34] * P[21] * inside;
    }
    // absorbing sponge at the grid edges
    const float de = (float)min(min(i, GW - 1 - i), min(j, GH - 1 - j));
    const float t = fmaxf(1.f - de / P[38], 0.f);
    const float keep = 1.f - P[15] - P[39] * t * t;
    const int idx = j * GW + i;
    M[idx] = make_float4(c2, keep, A, B);
    WL[idx] = wall;
}

// one leapfrog step: reads Hc (current), overwrites Hp (previous) with the next state
extern "C" __global__
void rt_step(const float* __restrict__ Hc, float* __restrict__ Hp, float* __restrict__ I,
             const float4* __restrict__ M, const float* __restrict__ WL, const int GW, const int GH,
             const float* __restrict__ P, const float sphi, const float cphi, const float dropamp)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= GW || j >= GH) return;
    const int idx = j * GW + i;
    const float h = Hc[idx];
    const float l = Hc[j * GW + max(i - 1, 0)], r = Hc[j * GW + min(i + 1, GW - 1)];
    const float up = Hc[max(j - 1, 0) * GW + i], dn = Hc[min(j + 1, GH - 1) * GW + i];
    const float4 m = M[idx];
    float hn = h + m.y * (h - Hp[idx]) + m.x * (l + r + up + dn - 4.f * h) + m.z * sphi + m.w * cphi;
    if (dropamp != 0.f) {                                  // beat drops: a velocity impulse at N-fold points
        const float cpu = P[16];
        const float u = (i + 0.5f - 0.5f * GW) / cpu, v = (j + 0.5f - 0.5f * GH) / cpu;
        const float is2 = cpu * cpu / (2.f * P[42] * P[42]);
        const int nd = min((int)P[40], MAXD);
        float s = 0.f;
        for (int k = 0; k < nd; ++k) {
            const float du = u - P[44 + 2 * k], dv = v - P[45 + 2 * k];
            s += __expf(-(du * du + dv * dv) * is2);
        }
        hn += dropamp * s;
    }
    hn *= (1.f - 0.25f * (1.f - m.y)) * (1.f - WL[idx]);  // a little height damping (kills DC drift), walls
    hn = fminf(fmaxf(hn, -60.f), 60.f);                    // also heals a NaN
    Hp[idx] = hn;
    const float ii = I[idx];
    I[idx] = ii + P[75] * (hn * hn - ii);
}

// per frame: interpolated, exposure- and wavelength-normalised height, slopes, curvature + envelope/walls/glow
extern "C" __global__
void rt_prep(const float* __restrict__ Hc, const float* __restrict__ Hp, const float* __restrict__ I,
             const float4* __restrict__ M, const float* __restrict__ WL, float4* __restrict__ G,
             float4* __restrict__ D, const int GW, const int GH, const float* __restrict__ P,
             const float* __restrict__ NORM)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= GW || j >= GH) return;
    const float a = P[73];
    const int i0 = max(i - 1, 0), i1 = min(i + 1, GW - 1), j0 = max(j - 1, 0), j1 = min(j + 1, GH - 1);
#define HI(ii, jj) (Hp[(jj) * GW + (ii)] + a * (Hc[(jj) * GW + (ii)] - Hp[(jj) * GW + (ii)]))
    const float h = HI(i, j), l = HI(i0, j), r = HI(i1, j), up = HI(i, j0), dn = HI(i, j1);
#undef HI
    const int idx = j * GW + i;
    const float g = P[71] / fmaxf(NORM[0], 1e-7f);
    const float k = P[72] * 0.15915494f;                   // wavelength / 2 pi
    G[idx] = make_float4(h * g, 0.5f * (r - l) * g * k, 0.5f * (dn - up) * g * k, (l + r + up + dn - 4.f * h) * g * k * k);
    const float4 m = M[idx];
    const float lens = clamp01((P[14] - m.x) / (P[14] * P[34] + 1e-6f));
    const float glow = sqrtf(m.z * m.z + m.w * m.w) / (P[43] + 1e-9f);
    D[idx] = make_float4(sqrtf(2.f * fmaxf(I[idx], 0.f)) * g, WL[idx], lens, glow);
}

extern "C" __global__
void rt_reduce(const float* __restrict__ I, const int N, float* __restrict__ S)
{
    __shared__ float sh[256];
    float s = 0.f;
    for (int k = blockIdx.x * blockDim.x + threadIdx.x; k < N; k += gridDim.x * blockDim.x) s += I[k];
    sh[threadIdx.x] = s;
    __syncthreads();
    for (int o = 128; o > 0; o >>= 1) {
        if (threadIdx.x < o) sh[threadIdx.x] += sh[threadIdx.x + o];
        __syncthreads();
    }
    if (threadIdx.x == 0) atomicAdd(S, sh[0]);
}

// auto exposure: RMS height per unit source amplitude, eased (device-side, no host round trip)
extern "C" __global__
void rt_final(float* __restrict__ S, float* __restrict__ NORM, const float* __restrict__ P)
{
    if (threadIdx.x != 0 || blockIdx.x != 0) return;
    const float rms = sqrtf(fmaxf(S[0], 0.f) / P[80]);
    const float ref = fmaxf(rms / fmaxf(P[77], 1e-3f), 1e-7f);
    const float n = NORM[0];
    NORM[0] = (n > 0.f && n < 1e30f) ? n + (ref - n) * P[76] : ref;
    S[0] = 0.f;
}

extern "C" __global__
void rt_render(uchar4* __restrict__ out, const float4* __restrict__ G, const float4* __restrict__ D,
               const int GW, const int GH, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fx = (x + 0.5f) * (float)GW / (float)W, fy = (y + 0.5f) * (float)GH / (float)H;
    const float4 g = samp4(G, GW, GH, fx, fy);
    const float4 d = samp4(D, GW, GH, fx, fy);
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (y + 0.5f - hh) / hh;
    const float* PAL = P;
    const float period = P[12], drift = P[13];
    const float hn = g.x, sx = g.y, sy = g.z, lp = g.w;
    const float env = fminf(d.x, 6.f);
    float3 c = make_float3(0.f, 0.f, 0.f);
    // --- lit water: refracted tank floor, caustic crests, crest glow, specular glint
    if (P[68] > 0.001f) {
        const float qx = u + 0.03f * sx, qy = v + 0.03f * sy;
        const float fl = 0.62f * vnoise(qx * 2.3f + 11.f, qy * 2.3f + 5.f, 7u) + 0.38f * vnoise(qx * 5.3f, qy * 5.3f, 9u);
        const float3 fc = pal(drift + period * (0.25f * fl + 0.3f * env), PAL);
        const float cau = tanhf(fmaxf(-lp, 0.f) * 1.25f);
        const float shd = tanhf(fmaxf(lp, 0.f) * 0.9f);
        const float lit = 0.3f + 0.28f * fl + 0.95f * cau - 0.2f * shd;
        float nx = -0.65f * sx, ny = -0.65f * sy;
        const float nl = rsqrtf(nx * nx + ny * ny + 1.f);
        // half vector of light (-0.45, -0.55, 0.70) and view (0, 0, 1)
        const float spd = (nx * -0.2415f + ny * -0.2952f + 0.9244f) * nl;
        const float sp = __powf(fmaxf(spd, 0.f), 70.f);
        const float3 hc = bpal(drift + 0.5f + 0.35f * period * fminf(env, 2.f), PAL);
        const float cg = P[78] * cau * cau;
        const float w = P[68];
        c.x += w * (fc.x * lit * 0.85f + hc.x * cg + 1.05f * sp);
        c.y += w * (fc.y * lit * 0.85f + hc.y * cg + 1.05f * sp);
        c.z += w * (fc.z * lit * 0.85f + hc.z * cg + 1.0f * sp);
    }
    // --- interference fringes: glowing envelope bands, travelling crests, white-hot antinodes
    if (P[69] > 0.001f) {
        const float e2 = env * P[79];
        const float band = 1.f - __expf(-2.f * e2 * e2);
        const float r = fminf(fmaxf(hn / (env + 0.06f), -1.f), 1.f);
        const float b = band * (0.4f + 0.6f * (0.5f + 0.5f * r));
        const float3 fc = bpal(drift + 0.45f * period * fminf(env, 2.5f), PAL);
        const float3 bg = pal(drift + 0.5f, PAL);
        const float wh = 0.45f * b * b * b;
        const float w = P[69];
        c.x += w * (0.035f * bg.x + fc.x * b * 1.05f + wh);
        c.y += w * (0.035f * bg.y + fc.y * b * 1.05f + wh);
        c.z += w * (0.035f * bg.z + fc.z * b * 1.05f + wh);
    }
    // --- false colour: signed height through the palette
    if (P[70] > 0.001f) {
        const float th = tanhf(1.2f * hn);
        const float3 fc = pal(drift + 0.25f + 0.45f * period * th, PAL);
        const float b = 0.08f + 0.92f * __powf(fabsf(th), 0.75f);
        const float w = P[70] * b;
        c.x += w * fc.x;
        c.y += w * fc.y;
        c.z += w * fc.z;
    }
    // lens: faint glass outline + a touch of lift
    const float lens = d.z;
    if (lens > 0.002f) {
        const float rim = 4.f * lens * (1.f - lens);
        const float lift = 1.f + 0.12f * lens;
        c.x = c.x * lift + 0.22f * rim;
        c.y = c.y * lift + 0.24f * rim;
        c.z = c.z * lift + 0.27f * rim;
    }
    // walls: dark slabs with a lit rim
    const float wa = clamp01(d.y);
    if (wa > 0.002f) {
        const float3 wc = pal(drift + 0.62f, PAL);
        const float3 rc = bpal(drift + 0.3f, PAL);
        const float rim = 4.f * wa * (1.f - wa);
        c.x = c.x * (1.f - wa) + wa * (0.05f + 0.1f * wc.x) + 0.55f * rim * rc.x;
        c.y = c.y * (1.f - wa) + wa * (0.05f + 0.1f * wc.y) + 0.55f * rim * rc.y;
        c.z = c.z * (1.f - wa) + wa * (0.06f + 0.1f * wc.z) + 0.55f * rim * rc.z;
    }
    // dippers
    const float gl = fminf(d.w, 1.6f) * P[81];
    if (gl > 0.002f) {
        const float3 sc = bpal(drift + 0.15f, PAL);
        c.x += gl * (0.5f * sc.x + 0.35f);
        c.y += gl * (0.5f * sc.y + 0.35f);
        c.z += gl * (0.5f * sc.z + 0.35f);
    }
    const float vg = 1.f - 0.22f * (u * u + v * v) / (1.f + (float)W * W / ((float)H * H));
    const float gg = 255.f * P[74] * vg;
    out[y * W + x] = pack(make_float4(c.x * gg, c.y * gg, c.z * gg, 255.f));
}
""" % {"maxs": MAXS, "maxd": MAXD}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _tri3(s: float) -> tuple[float, float, float]:
    """Continuous 0..1 -> weights of three anchors (0, 0.5, 1) with a short dwell on each."""
    a = 1.0 - _ss(0.1, 0.42, s)
    c = _ss(0.58, 0.9, s)
    return a, max(1.0 - a - c, 0.0), c


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class RippleTank(Source):
    name = "ripple_tank"
    label = "Ripple tank"
    description = ("A physics ripple tank: dippers, a wave bar through a double slit, a glass lens and an "
                   "elliptical mirror on a live wave-equation grid, as lit water, glowing fringes or false colour.")
    params = _order({
        "source_layout": num(0.0, 1.0, 0.0, "Sources (2 points -> ring -> line + slits)", tier="regional",
                             basic=True),
        "render_style": num(0.0, 1.0, 0.0, "Look (water -> fringes -> false colour)", tier="regional", basic=True),
        "wavelength": num(0.0, 1.0, 0.35, "Wavelength", tier="regional"),
        "damping": num(0.0, 1.0, 0.3, "Damping", tier="regional"),
        "barrier_geometry": num(0.0, 1.0, 0.0, "Barrier (open -> lens -> mirror walls)", tier="regional"),
        "symmetry": num(2.0, 9.0, 5.0, "Symmetry (ring dippers, drop copies)", tier="regional"),
        "twist": num(0.0, 2.0, 0.0, "Phase twist (steer / spiral)", tier="regional"),
        "drop_kick": num(0.0, 2.0, 1.0, "Beat drops", tier="regional"),
        **standard_scene_params(palette="ocean", render_scale=1.0, period=1.0, drift=0.02),
    }, ["source_layout", "render_style", "wavelength", "damping", "barrier_geometry", "symmetry", "twist",
        "drop_kick", "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.gh = GRID_H
        self.gw = max(64, int(round(GRID_H * ctx.width / ctx.height / 2)) * 2)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        f32 = cp.float32
        self.ha = cp.zeros((self.gh, self.gw), f32)
        self.hb = cp.zeros((self.gh, self.gw), f32)
        self.inten = cp.zeros((self.gh, self.gw), f32)
        self.mask = cp.zeros((self.gh, self.gw, 4), f32)
        self.walls = cp.zeros((self.gh, self.gw), f32)
        self.gbuf = cp.zeros((self.gh, self.gw, 4), f32)
        self.dbuf = cp.zeros((self.gh, self.gw, 4), f32)
        self.ssum = cp.zeros(1, f32)
        self.norm = cp.zeros(1, f32)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_mask = mod.get_function("rt_mask")
        self.k_step = mod.get_function("rt_step")
        self.k_prep = mod.get_function("rt_prep")
        self.k_reduce = mod.get_function("rt_reduce")
        self.k_final = mod.get_function("rt_final")
        self.k_render = mod.get_function("rt_render")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, f32)
        self.rng = np.random.default_rng(int(params["seed"]))
        self.sphase = 0.0
        self.wander = float(self.rng.uniform(0, 2 * math.pi))
        self.ring_rot = float(self.rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.acc = 0.0
        self.t = 0.0
        self.prev_pulse = 0.0
        self.last_drop = -1.0
        self.pending_drop = 0.0
        self.drop_ref = 0.0
        # prewarm: fill the tank so the scene arrives formed and the exposure has settled
        for _ in range(170):
            self._advance(params, 1.0 / 60.0, prime=True)
        self.drop_ref = max(float(self.norm.get()[0]), 1e-7)     # setup only: one read-back for the drop scale

    # ------------------------------------------------------------------ host maths + simulation
    def _advance(self, p: dict, dt: float, prime: bool = False) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        k = pulse * (0.35 + 0.65 * e)
        asp = self.gw / self.gh
        sym = min(max(float(p["symmetry"]), 2.0), 9.0)
        twist = float(p["twist"])

        # integrated phases (rates only; pulse never moves a position)
        self.t += dt
        self.wander = _wrap(self.wander + 2 * math.pi * 0.045 * (0.5 + e) * spd * dt, 2 * math.pi)
        self.ring_rot = _wrap(self.ring_rot + 2 * math.pi * 0.012 * (0.4 + 0.8 * e) * spd * dt, 2 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)

        lam = (20.0 + 70.0 * float(p["wavelength"])) * (1.15 - 0.4 * e)          # cells
        omega = 2 * math.pi * math.sqrt(C2) / lam                                 # rad per step
        if prime:
            n = 7
            self.acc = 0.0
        else:
            self.acc += 140.0 * (0.7 + 0.6 * e) * spd * dt
            n = int(self.acc)
            self.acc -= n
            if n > MAX_STEPS:
                n = MAX_STEPS
            self.acc = min(max(self.acc, 0.0), 0.999)

        # beat drops on a rising edge (spawn only; amplitude scales with the beat and energy)
        if not prime:
            if pulse - self.prev_pulse > 0.15 and self.t - self.last_drop > 0.12 and float(p["drop_kick"]) > 0:
                self.last_drop = self.t
                nd = int(min(max(round(sym), 1), MAXD))
                r = float(self.rng.uniform(0.18, 0.72))
                a0 = float(self.rng.uniform(0, 2 * math.pi))
                for i in range(MAXD):
                    if i < nd:
                        a = a0 + 2 * math.pi * i / nd
                        self.Ph[44 + 2 * i] = r * math.cos(a)
                        self.Ph[45 + 2 * i] = r * math.sin(a)
                self.Ph[40] = nd
                self.pending_drop = (float(p["drop_kick"]) * (0.6 + 0.4 * pulse) * (0.6 + 0.6 * e)
                                     * 3.2 * self.drop_ref * (0.45 + 0.55 * e))
            self.prev_pulse = pulse

        P = self.Ph
        pal_rows = self.session_palette()
        P[0:12] = (np.asarray(pal_rows, np.float32).reshape(12) if pal_rows is not None
                   else palette_coeffs(p["palette"]))
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = C2
        damp = float(p["damping"])
        P[15] = 0.0003 + 0.006 * damp * damp
        cpu = self.gh / 2.0
        P[16] = cpu
        P[17] = asp
        wP, wR, wL = _tri3(min(max(float(p["source_layout"]), 0.0), 1.0))
        _, wLens, wMir = _tri3(min(max(float(p["barrier_geometry"]), 0.0), 1.0))
        P[18], P[19], P[20], P[21], P[22] = wP, wR, wL, wLens, wMir
        amp_e = 0.45 + 0.55 * e
        unit = 0.02 * amp_e
        forcing = unit * (1.0 + 0.8 * k)
        P[23] = forcing
        P[24] = forcing * 0.35 * wL
        P[25] = -asp + 0.32
        P[26] = SRC_SIGMA
        P[27], P[28], P[29], P[30] = -0.55, 0.17, 0.028, 0.018
        P[31], P[32], P[33], P[34] = 0.55, 1.05, 0.85, 0.55
        P[35], P[36] = asp - 0.14, 0.86
        P[38], P[39] = SPONGE_W, 0.16
        P[42] = DROP_SIGMA
        P[43] = unit

        # sources (u, v, weight, phase offset)
        src = []
        steer = twist * math.pi + (0.35 + 0.5 * e) * math.sin(self.wander)
        if wP > 0.001:
            ext = _ss(0.25, 0.75, e)
            x0 = -0.85
            for kk, y0, w in ((-0.5, -0.21, 1.0), (0.5, 0.21, 1.0), (-1.5, -0.63, ext), (1.5, 0.63, ext)):
                if w > 0.001:
                    src.append((x0, y0, wP * w, kk * steer))
        if wR > 0.001:
            inner = _ss(0.4, 0.85, e)
            nfull = int(math.floor(sym))
            frac = sym - nfull
            ncount = nfull + (1 if frac > 0.001 else 0)
            for i in range(ncount):
                w = 1.0 if i < nfull else frac
                a = self.ring_rot + 2 * math.pi * i / sym
                th = twist * 2 * math.pi * i / sym
                src.append((0.72 * math.cos(a), 0.72 * math.sin(a), wR * w, th))
                if inner > 0.001:
                    ai = a + math.pi / sym
                    src.append((0.3 * math.cos(ai), 0.3 * math.sin(ai), wR * w * inner * 0.8, th + math.pi))
        src = src[:MAXS]
        for i, (su, sv, sw, th) in enumerate(src):
            P[128 + 4 * i: 132 + 4 * i] = (su, sv, sw, _wrap(th, 2 * math.pi))
        P[37] = len(src)

        # display
        P[68], P[69], P[70] = _tri3(min(max(float(p["render_style"]), 0.0), 1.0))
        P[71] = 0.5
        P[72] = lam
        P[73] = self.acc
        P[74] = float(p["intensity"]) * (0.82 + 0.3 * e) * (1.0 + 0.12 * k)
        P[75] = 0.012
        P[76] = 0.25 if prime else (1.0 - math.exp(-dt / 2.5) if dt > 0 else 0.0)
        P[77] = amp_e
        P[78] = 0.35 + 0.35 * e
        P[79] = 0.9 + 0.5 * e
        P[80] = float(self.gw * self.gh)
        P[81] = 0.75 * (1.0 + 0.5 * k)
        self.P.set(P)

        gw, gh = np.int32(self.gw), np.int32(self.gh)
        launch_2d(self.k_mask, self.gw, self.gh, (self.mask, self.walls, gw, gh, self.P))
        for s in range(n):
            ph = self.sphase + s * omega
            drop = np.float32(self.pending_drop if s == 0 else 0.0)
            launch_2d(self.k_step, self.gw, self.gh,
                      (self.ha, self.hb, self.inten, self.mask, self.walls, gw, gh, self.P,
                       np.float32(math.sin(ph)), np.float32(math.cos(ph)), drop))
            self.ha, self.hb = self.hb, self.ha          # ha = newest, hb = previous
        if n > 0:
            self.pending_drop = 0.0
        self.sphase = _wrap(self.sphase + n * omega, 2 * math.pi)
        self.k_reduce((64, 1, 1), (256, 1, 1), (self.inten, np.int32(self.gw * self.gh), self.ssum))
        self.k_final((1, 1, 1), (32, 1, 1), (self.ssum, self.norm, self.P))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._advance(p, dt)
        gw, gh = np.int32(self.gw), np.int32(self.gh)
        launch_2d(self.k_prep, self.gw, self.gh, (self.ha, self.hb, self.inten, self.mask, self.walls, self.gbuf,
                                                  self.dbuf, gw, gh, self.P, self.norm))
        dst = out if self.direct else self.buf
        launch_2d(self.k_render, self.w, self.h, (dst, self.gbuf, self.dbuf, gw, gh, np.int32(self.w),
                                                  np.int32(self.h), self.P))
        if not self.direct:
            present(self.ctx, self.buf, out)
