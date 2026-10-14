"""Fractal flame: Electric-Sheep-style IFS flames, iterated by persistent GPU walkers into a log-density histogram.

Source: Original (inspired by https://flam3.com/flame_draves.pdf)
Author: smfx (algorithm: Scott Draves & Erik Reckase, "The Fractal Flame Algorithm")
License: MIT
license_class: permissive

Written from the published paper only (variations V0 linear, V1 sinusoidal, V2 spherical, V3 swirl, V4 horseshoe,
V5 polar, V13 julia; the colour coordinate blended along the orbit; rotation/reflection symmetry transforms with
colour speed 0; log-density display with gamma and vibrancy).  No flam3 / Apophysis code (GPL) was read, copied or
ported; the genome, the morphing and the GPU walker scheme are smfx's own.

How it renders: a few hundred thousand persistent "walkers" (x, y, colour) live on the GPU.  Each frame every walker
takes a fixed number of chaos-game steps through the current transforms and splats every point (r, g, b, count)
into a float32 histogram with atomicAdd.  One per-pixel pass then tonemaps the histogram (log density -> gamma /
vibrancy -> highlight roll-off) and decays it by pow(d, dt*60) for persistence.  The walkers never restart, so the
flame stays converged while its transforms morph; walkers that escape (NaN / huge) respawn and skip a few
iterations (a "fuse").  The histogram is normalised by the number of samples it holds, so brightness is the same
at any fps, walker count or persistence.

Genome: 3 base transforms + 1 "complexity" transform whose weight fades in with energy.  Each transform is an
affine R(theta) * diag(s, s * aspect) * R(phi) + t whose angles (and the translation's bearing) rotate at
host-integrated rates, followed by a blend of variations from three curated sets that ``variation_mix`` morphs
across (0 sinusoidal/spherical -> 0.5 swirl/horseshoe -> 1 julia/polar), plus a linear part.  ``symmetry`` is a
dihedral order 1-8 (D1 = bilateral, one mirror): an in-loop rotate(+mirror) transform (colour speed 0) and a final
symmetrising plot; the fractional order crossfades walkers between floor(order) and floor(order)+1 by a per-walker
hash, so the knob is continuous.  Framing is automatic and slewed (2 s): the camera follows the centroid of the
symmetrised flame and scales its 90th-percentile radius to the frame, so morphs never drift off screen.

Macros (e' = smoothstep(0.1, 0.85, energy)):
  energy -> affine rotation (morph) rate x(0.4 + 1.2e'), exposure (veils come up), variation-weight wobble
            amplitude, complexity-transform weight (e'^1.5, slewed over 2 s), persistence a little shorter (crisper),
            camera spin rate.
  pulse  -> k = pulse * (0.3 + 0.7e), attack softened over ~50 ms.  ``glint_kick`` x k: a flame-only exposure punch
            (<= +30%), and on each beat (host rising-edge detector) the next transform "glints": its samples flare
            (gain) and its nonlinear variation weights swell x(1 + 0.5 k), so one family of filaments blooms out and
            settles.  Plus a lurch of the morph-rate phase.  Rates/amplitudes/gains only: nothing jumps.
  speed  -> every phase.  palette -> colour = pal(c * period + drift) per sample, c the orbit colour coordinate.
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

N_TF = 4             # 3 base transforms + 1 complexity transform (weight fades in with energy)
TF = 16              # floats per transform in P
P_LEN = 32 + TF * N_TF
WX = 256             # walker grid width (walkers are a WX x WY 2D array for launch_2d)
ITERS = 16           # chaos-game steps per walker per frame
WPP = 1.0 / 16.0     # walkers per internal pixel
FUSE = 12            # iterations a respawned / re-grouped walker skips before it plots
BASE_MORPH = 0.12    # rad/s of the affine angles at morph_speed 1, energy ~0.5
BASE_SPIN = 0.025    # rad/s camera spin
NL_BASE = 1.0        # nonlinear variation amplitude
LIN_EXTRA = 0.05     # linear part on top of (1 - nonlinear)
TR_LO, TR_HI = 0.60, 1.30   # translation radius at spread 0 / 1
EXPO_REF = 60.0      # density (x mean) that maps to alpha 1 at exposure 1
PSYM = 0.12          # in-loop symmetry transform probability
SC_LO, SC_HI = 0.40, 0.62   # affine scale range
ASP_LO, ASP_HI = 0.12, 0.50 # anisotropy (minor/major) at spread 1
FIT_R = 0.46         # auto-framing: the 90th-percentile orbit radius maps to this fraction of the height
FIT_TAU = 2.0        # s, auto-framing slew
FIT_PCT = 0.9
DE_COUNT = 6.0       # density estimation: samples at which a pixel takes half the 3x3 blur
FLOOR_LO, FLOOR_HI = 0.3, 2.0   # noise floor (samples): alpha fades in across this range
CPLX_LO, CPLX_HI = 0.15, 1.0  # complexity-transform weight at energy 0 / 1
WOB_LO, WOB_HI = 0.12, 0.34    # variation wobble amplitude at energy 0 / 1
KICK_ATTACK = 0.05   # s, attack softening of the beat kick
FLASH = 0.15         # flame-only exposure punch per unit glint_kick at full pulse (<= +30%)
GLINT = 2.0          # beat glint: extra gain on the glinting transform's samples per unit glint_kick
MORPH_KICK = 1.5     # beat lurch of the morph rate
NL_KICK = 0.5        # beat swell of the nonlinear variation weights (x(1 + NL_KICK k))
MIRROR1 = 1.0        # order 1 is D1 (bilateral: one mirror), not the asymmetric flame
GAMMA_T = 0.25       # gamma threshold: linear toe below this alpha
DE_NEED = 24.0       # density estimation: samples an estimate should rest on before the next-wider level is dropped

# P layout (floats)
#  0 n_tf | 1 p_sym (in-loop symmetry transform probability) | 2 N0 | 3 N1 | 4 frac (walkers using N1)
#  5 cam cos*zoom | 6 cam sin*zoom | 7 cx px | 8 cy px | 9 palette period | 10 drift | 11 glint transform
#  12..23 palette | 24..27 cumulative transform weights (normalised) | 28 glint extra gain
#  32 + 16 i: a b c d e f (x' = a x + b y + c, y' = d x + e y + f), colour, colour speed,
#             w_linear w_sinusoidal w_spherical w_swirl w_horseshoe w_polar w_julia, unused
_SRC = HELPERS + PALETTE_CUDA + r"""
#define P_LEN %(plen)d
#define FUSE %(fuse)d

__device__ __forceinline__ float rnd(unsigned int& s) {
    s = s * 1664525u + 1013904223u;
    return (hash_u(s) >> 8) * (1.f / 16777216.f);
}

extern "C" __global__
void flame_iter(float4* __restrict__ walk, unsigned int* __restrict__ rng, float* __restrict__ hist,
                const float* __restrict__ P, unsigned int* __restrict__ bins, const float* __restrict__ fitv,
                float* __restrict__ cen, const int NWX, const int W, const int H, const int iters)
{
    __shared__ float S[P_LEN];
    __shared__ unsigned int B[64];
    __shared__ float C[3];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < P_LEN; i += blockDim.x * blockDim.y) S[i] = P[i];
    if (tid < 64) B[tid] = 0u;
    if (tid < 3) C[tid] = 0.f;
    __syncthreads();
    const float fs = expf(fitv[0]);
    const float cx = fitv[1], cy = fitv[2];                 // slewed centroid of the symmetrised flame
    const float ca0 = S[5] * fs, sa0 = S[6] * fs;
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int id = y * NWX + x;

    float4 w = walk[id];
    unsigned int s = rng[id];
    float px = w.x, py = w.y, c = w.z;
    int tag = (int)w.w;
    int fuse = tag & 31;
    const int N = (hash1((unsigned int)id, 977u) < S[4]) ? (int)S[3] : (int)S[2];
    if ((tag >> 5) != N) fuse = FUSE;                       // changed symmetry group: re-converge before plotting
    const int ntf = (int)S[0];
    const bool symm = N > 1 || S[29] > 0.5f;              // order 1 = D1: a single mirror
    const float psym = symm ? S[1] : 0.f;
    const float invN = 6.2831853f / (float)N;
    const int gi = (int)S[11];
    const float gx = S[28];
    int lastT = 0;
    float lqx = 0.f, lqy = 0.f;                             // last plotted (symmetrised) point, for framing

    for (int it = 0; it < iters; ++it) {
        const float u = rnd(s);
        if (u < psym) {                                     // symmetry transform: rotate by 2 pi k / N (+ mirror)
            const int k = 1 + min((int)(rnd(s) * (N - 1)), N - 2);
            float sa, ca;
            __sincosf(invN * k, &sa, &ca);
            const float nx = ca * px - sa * py, ny = sa * px + ca * py;
            px = nx; py = (rnd(s) < 0.5f) ? -ny : ny;
        } else {
            const float u2 = (u - psym) / (1.f - psym);
            int i = 0;
            while (i < ntf - 1 && u2 >= S[24 + i]) ++i;
            const float* T = S + 32 + 16 * i;
            const float ax = T[0] * px + T[1] * py + T[2], ay = T[3] * px + T[4] * py + T[5];
            const float r2 = ax * ax + ay * ay + 1e-12f;
            float nx = T[8] * ax, ny = T[8] * ay;           // V0 linear
            if (T[9] != 0.f) { nx += T[9] * __sinf(ax); ny += T[9] * __sinf(ay); }            // V1 sinusoidal
            if (T[10] != 0.f) { const float ir = 1.f / r2; nx += T[10] * ax * ir; ny += T[10] * ay * ir; }  // V2 spherical
            if (T[11] != 0.f) {                                                                 // V3 swirl
                float sr, cr;
                __sincosf(r2, &sr, &cr);
                nx += T[11] * (ax * sr - ay * cr); ny += T[11] * (ax * cr + ay * sr);
            }
            const float r = sqrtf(r2);
            if (T[12] != 0.f) {                                                                 // V4 horseshoe
                const float ir = 1.f / r;
                nx += T[12] * (ax - ay) * (ax + ay) * ir; ny += T[12] * 2.f * ax * ay * ir;
            }
            if (T[13] != 0.f || T[14] != 0.f) {
                const float th = atan2f(ax, ay);            // the paper's theta = arctan(x / y)
                nx += T[13] * th * 0.31830989f; ny += T[13] * (r - 1.f);                       // V5 polar
                if (T[14] != 0.f) {                                                             // V13 julia
                    const float sq = sqrtf(r);
                    const float a = 0.5f * th + (rnd(s) < 0.5f ? 0.f : 3.14159265f);
                    float sa, ca;
                    __sincosf(a, &sa, &ca);
                    nx += T[14] * sq * ca; ny += T[14] * sq * sa;
                }
            }
            px = nx; py = ny;
            c += (T[6] - c) * T[7];
            lastT = i;
        }
        if (!(fabsf(px) < 1e4f && fabsf(py) < 1e4f)) {      // escaped or NaN: respawn and re-converge
            px = 2.f * rnd(s) - 1.f; py = 2.f * rnd(s) - 1.f; c = rnd(s); fuse = FUSE;
            continue;
        }
        if (fuse > 0) { --fuse; continue; }
        // final: symmetrising plot (a random element of the dihedral group) then the camera
        float qx = px, qy = py;
        if (symm) {
            const int k = min((int)(rnd(s) * N), N - 1);
            float sa, ca;
            __sincosf(invN * k, &sa, &ca);
            const float tx = ca * qx - sa * qy, ty = sa * qx + ca * qy;
            qx = tx; qy = (rnd(s) < 0.5f) ? -ty : ty;
        }
        qx -= cx; qy -= cy;
        lqx = qx; lqy = qy;
        const float sx = S[7] + ca0 * qx - sa0 * qy;
        const float sy = S[8] - (sa0 * qx + ca0 * qy);
        if (sx >= 0.f && sy >= 0.f && sx < (float)W && sy < (float)H) {
            const float3 col = pal(c * S[9] + S[10], S + 12);
            float* hp = hist + 4 * ((int)sy * W + (int)sx);
            const float gm = (lastT == gi) ? 1.f + gx : 1.f;     // beat glint: that transform's filaments flare
            atomicAdd(hp, col.x * gm); atomicAdd(hp + 1, col.y * gm); atomicAdd(hp + 2, col.z * gm);
            atomicAdd(hp + 3, 1.f);
        }
    }
    walk[id] = make_float4(px, py, c, (float)(N * 32 + fuse));
    rng[id] = s;
    if (fuse == 0) {                                        // framing statistics: log2 radius, 4 bins / octave
        const int b = (int)floorf((log2f(sqrtf(lqx * lqx + lqy * lqy) + 1e-9f) + 7.f) * 4.f);
        atomicAdd(&B[min(max(b, 0), 63)], 1u);
        // centroid of the symmetrised flame: a group >= 2 is centred on the origin; order 1 (mirror or none) is not
        if (N == 1) {
            atomicAdd(&C[0], fminf(fmaxf(px, -8.f), 8.f));
            if (!symm) atomicAdd(&C[1], fminf(fmaxf(py, -8.f), 8.f));
        }
        atomicAdd(&C[2], 1.f);
    }
    __syncthreads();
    if (tid < 64 && B[tid]) atomicAdd(&bins[tid], B[tid]);
    if (tid < 3 && C[tid] != 0.f) atomicAdd(&cen[tid], C[tid]);
}

// Auto-framing: the FIT_PCT radius percentile of the walkers -> slewed log scale (one thread; clears the bins).
extern "C" __global__
void flame_fit(unsigned int* __restrict__ bins, float* __restrict__ fitv, float* __restrict__ cen, const float base,
               const float af, const float pct)
{
    if (threadIdx.x != 0 || threadIdx.y != 0) return;
    if (cen[2] > 64.f && af > 0.f) {                        // follow the centroid (slewed like the scale)
        const float ic = 1.f / cen[2];
        fitv[1] += (cen[0] * ic - fitv[1]) * af;
        fitv[2] += (cen[1] * ic - fitv[2]) * af;
    }
    cen[0] = cen[1] = cen[2] = 0.f;
    unsigned int tot = 0u;
    for (int b = 0; b < 64; ++b) tot += bins[b];
    if (tot > 64u && af > 0.f) {
        const float want = pct * (float)tot;
        float acc = 0.f, l2 = 0.f;
        for (int b = 0; b < 64; ++b) {
            const float nb = (float)bins[b];
            if (acc + nb >= want) { l2 = (b + (want - acc) / fmaxf(nb, 1.f)) * 0.25f - 7.f; break; }
            acc += nb;
        }
        const float tgt = fminf(fmaxf(base - 0.6931472f * l2, base - 4.f), base + 4.f);
        fitv[0] += (tgt - fitv[0]) * af;
    }
    for (int b = 0; b < 64; ++b) bins[b] = 0u;
}

// Q: [0] k2 (1 / mean count) [1] k1 (exposure) [2] 1/gamma [3] vibrancy [4] gain [5] decay [6] highlight
//    [7] density-estimation count (a pixel with this many samples takes half the 3x3 blur)
//    [8], [9] noise floor: alpha fades in between these sample counts [10] samples an estimate needs
//    [11] gamma threshold (linear toe below it)
__device__ __forceinline__ float4 hl(const float4* __restrict__ h, int x, int y, int W, int H) {
    x = min(max(x, 0), W - 1); y = min(max(y, 0), H - 1);
    return h[y * W + x];
}
// 2x2 box downsample (average) of a float4 histogram: the density pyramid for adaptive density estimation
extern "C" __global__
void flame_down(const float4* __restrict__ src, float4* __restrict__ dst, const int w, const int h,
                const int W, const int H)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float4 a = hl(src, 2 * x, 2 * y, W, H), b = hl(src, 2 * x + 1, 2 * y, W, H);
    const float4 c = hl(src, 2 * x, 2 * y + 1, W, H), d = hl(src, 2 * x + 1, 2 * y + 1, W, H);
    dst[y * w + x] = make_float4(0.25f * (a.x + b.x + c.x + d.x), 0.25f * (a.y + b.y + c.y + d.y),
                                 0.25f * (a.z + b.z + c.z + d.z), 0.25f * (a.w + b.w + c.w + d.w));
}
// bilinear sample of a pyramid level at continuous texel coords
__device__ float4 hbil(const float4* __restrict__ m, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const float4 a = hl(m, x0, y0, w, h), b = hl(m, x0 + 1, y0, w, h);
    const float4 c = hl(m, x0, y0 + 1, w, h), d = hl(m, x0 + 1, y0 + 1, w, h);
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}
// gamma with a linear toe below T (no infinite slope at 0: faint veils stay dark and smooth, not boosted grain)
__device__ __forceinline__ float gpow(float x, float ig, float T) {
    return x >= T ? powf(x, ig) : x * powf(T, ig - 1.f);
}
__device__ __forceinline__ float4 lerp4(float4 a, float4 b, float t) {
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t);
}

extern "C" __global__
void flame_tone(const float4* __restrict__ hist, float4* __restrict__ hout, uchar4* __restrict__ out,
                const int W, const int H, const float* __restrict__ Q,
                const float4* __restrict__ m1, const float4* __restrict__ m2)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const float4 h0 = hist[i];
    // density estimation (lite): sparse pixels take a 3x3 tent blur, so thin veils read as silk, not speckle
    float4 h = h0;
    const float b = 1.f / (1.f + h0.w / Q[7]);
    if (b > 0.02f) {
        float4 a = make_float4(4.f * h0.x, 4.f * h0.y, 4.f * h0.z, 4.f * h0.w);
        #pragma unroll
        for (int j = -1; j <= 1; ++j)
            #pragma unroll
            for (int k = -1; k <= 1; ++k) {
                if (j == 0 && k == 0) continue;
                const float wgt = (j == 0 || k == 0) ? 2.f : 1.f;
                const float4 n = hl(hist, x + k, y + j, W, H);
                a.x += wgt * n.x; a.y += wgt * n.y; a.z += wgt * n.z; a.w += wgt * n.w;
            }
        const float u = b * (1.f / 16.f), v = 1.f - b;
        h = make_float4(v * h0.x + u * a.x, v * h0.y + u * a.y, v * h0.z + u * a.z, v * h0.w + u * a.w);
        // adaptive widening: where even the 3x3 estimate rests on few samples, take the 2x / 4x pyramid levels
        const float s0 = 4.f * h.w / Q[10];
        const float b1 = 1.f / (1.f + s0 * s0);
        if (b1 > 0.02f) {
            const int w1 = (W + 1) / 2, h1 = (H + 1) / 2, w2 = (w1 + 1) / 2, h2 = (h1 + 1) / 2;
            const float4 e1 = hbil(m1, 0.5f * (x + 0.5f), 0.5f * (y + 0.5f), w1, h1);
            const float s1 = 16.f * e1.w / Q[10];
            const float b2 = 1.f / (1.f + s1 * s1);
            const float4 e2 = hbil(m2, 0.25f * (x + 0.5f), 0.25f * (y + 0.5f), w2, h2);
            h = lerp4(h, lerp4(e1, e2, b2), b1);
        }
    }
    float r = 0.f, g = 0.f, bl = 0.f;
    if (h.w > 1e-4f) {
        // log-density, faded out below a few samples (sparse sampling noise would read as grain / spike HEVC)
        const float alpha = Q[1] * log1pf(h.w * Q[0]) * smooth(Q[8], Q[9], h.w);
        const float inv = 1.f / h.w;
        const float cr = h.x * inv, cg = h.y * inv, cb = h.z * inv;    // mean palette colour of the pixel
        const float ig = Q[2], vib = Q[3];
        const float T = Q[11];
        const float ag = gpow(alpha, ig, T);
        r = vib * cr * ag + (1.f - vib) * gpow(cr * alpha, ig, T);
        g = vib * cg * ag + (1.f - vib) * gpow(cg * alpha, ig, T);
        bl = vib * cb * ag + (1.f - vib) * gpow(cb * alpha, ig, T);
        r *= Q[4]; g *= Q[4]; bl *= Q[4];
        const float m = fmaxf(r, fmaxf(g, bl));
        if (m > 1.f) {                                                 // hot cores: keep hue, roll into white
            const float k = 1.f / m, wht = Q[6] * (1.f - k);
            r = r * k + (1.f - r * k) * wht; g = g * k + (1.f - g * k) * wht; bl = bl * k + (1.f - bl * k) * wht;
        }
    }
    out[i] = pack(make_float4(255.f * r, 255.f * g, 255.f * bl, 255.f));
    const float d = Q[5];                                              // decay into the other histogram buffer
    float4 hd = make_float4(h0.x * d, h0.y * d, h0.z * d, h0.w * d);
    if (hd.w < 1e-3f) hd = make_float4(0.f, 0.f, 0.f, 0.f);           // no denormal tails
    hout[i] = hd;
}
""" % {"plen": P_LEN, "fuse": FUSE}


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class FractalFlame(Source):
    name = "fractal_flame"
    label = "Fractal flame"
    description = "Electric-Sheep-style IFS flames: luminous filaments and veils, log-density tonemapped, slowly " \
                  "morphing under dihedral symmetry."
    params = {
        "variation_mix": num(0.0, 1.0, 0.5, "Variations (sine/sphere > swirl/horseshoe > julia/polar)",
                             basic=True),
        "symmetry": num(1.0, 8.0, 3.0, "Symmetry (dihedral order)", step=0.05, basic=True),
        "transform_spread": num(0.0, 1.0, 0.5, "Transform spread"),
        "zoom": num(0.5, 3.0, 1.0, "Zoom", unit="×"),
        "gamma": num(1.5, 4.0, 2.6, "Gamma"),
        "vibrancy": num(0.0, 1.0, 0.8, "Vibrancy"),
        "persistence": num(0.05, 2.0, 0.35, "Persistence", unit="s"),
        "glint_kick": num(0.0, 2.0, 1.0, "Beat glint"),
        "morph_speed": num(0.0, 4.0, 1.0, "Morph speed", unit="×", tier="realtime", bind="rate"),
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.hist = cp.zeros((self.h, self.w, 4), cp.float32)     # ping-pong: tone reads one, writes decayed
        self.hist2 = cp.zeros((self.h, self.w, 4), cp.float32)
        # walkers: ~1/8 per internal pixel (16 steps each -> ~2 samples / pixel / frame), WX x WY with WY % 8 == 0
        nw = min(max(int(self.w * self.h * WPP), 4096), 262144)
        self.wy = max(8, (nw // WX) // 8 * 8)
        self.nw = WX * self.wy
        rng = np.random.default_rng(int(params["seed"]))
        walk = np.zeros((self.wy, WX, 4), np.float32)
        walk[..., 0:2] = rng.uniform(-1.0, 1.0, (self.wy, WX, 2))
        walk[..., 2] = rng.uniform(0.0, 1.0, (self.wy, WX))
        walk[..., 3] = 1 * 32 + FUSE                        # group 1, fused
        self.walk = cp.asarray(walk)
        self.rng = cp.asarray(rng.integers(1, 2**32 - 1, (self.wy, WX), dtype=np.uint64).astype(np.uint32))
        mod = raw_module(_SRC)
        self.k_iter = mod.get_function("flame_iter")
        self.k_tone = mod.get_function("flame_tone")
        self.k_down = mod.get_function("flame_down")
        self.w1, self.h1 = (self.w + 1) // 2, (self.h + 1) // 2
        self.w2, self.h2 = (self.w1 + 1) // 2, (self.h1 + 1) // 2
        self.m1 = cp.zeros((self.h1, self.w1, 4), cp.float32)
        self.m2 = cp.zeros((self.h2, self.w2, 4), cp.float32)
        self.P = cp.zeros(P_LEN, cp.float32)
        self.Ph = np.zeros(P_LEN, np.float32)
        self.Q = cp.zeros(12, cp.float32)
        # genome (seeded): per transform base angles and their relative rates, scales, aspect, translation, colour
        n = N_TF
        sign = lambda: rng.choice([-1.0, 1.0], n)        # noqa: E731
        self.g = {
            "th0": rng.uniform(0, 2 * math.pi, n), "om": rng.uniform(0.6, 1.4, n) * sign(),
            "ph0": rng.uniform(0, 2 * math.pi, n), "nu": rng.uniform(0.3, 0.8, n) * sign(),
            "ps0": 2 * math.pi * np.arange(n) / 3 + rng.uniform(-0.4, 0.4, n), "pt": rng.uniform(0.15, 0.4, n) * sign(),
            "sc": rng.uniform(SC_LO, SC_HI, n), "asp": rng.uniform(ASP_LO, ASP_HI, n), "tr": rng.uniform(0.75, 1.15, n),
            "ratio": rng.uniform(0.25, 0.75, n), "wf": rng.uniform(0.6, 1.4, n), "wp": rng.uniform(0, 2 * math.pi, n),
            "col": rng.permutation(np.array([0.0, 0.36, 0.72, 1.0])), "wt": rng.uniform(0.7, 1.0, n),
        }
        self.morph = 0.0      # host-integrated morph phase (affine angles = th0 + om * morph); float64, unwrapped
        self.wob = 0.0        # variation wobble phase
        self.spin = 0.0       # camera spin angle
        self.drift = 0.0
        self.cplx = None      # slewed complexity (energy^1.5)
        self.ks = None        # attack-softened pulse
        self.brng = np.random.default_rng(int(params["seed"]) + 11)
        self.t, self.t_beat, self.armed, self.glint = 0.0, -1.0, True, 0
        self.hacc = 0.0       # frames' worth of samples the (decaying) histogram holds
        self.bins = cp.zeros(64, cp.uint32)                 # radius histogram (log2, 4 bins / octave) for framing
        self.fitv = cp.asarray(np.array([math.log(FIT_R * self.h), 0.0, 0.0], np.float32))  # log scale, centre x/y
        self.cen = cp.zeros(3, cp.float32)                  # centroid sums (x, y, count) for framing
        self.k_fit = mod.get_function("flame_fit")
        self.first = True

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ee = _smoothstep(0.1, 0.85, e)
        k_raw = pulse * (0.3 + 0.7 * e)
        # soften the beat's instant attack over ~2-3 frames (decay follows the envelope), so the swell reads as a
        # lurch, not a cut
        if self.ks is None or k_raw <= self.ks or dt <= 0:
            self.ks = k_raw
        else:
            self.ks += (k_raw - self.ks) * (1.0 - math.exp(-dt / KICK_ATTACK))
        k = self.ks
        gk = float(p["glint_kick"])
        # beat detector (rising edge with hysteresis, >= 150 ms apart): each beat glints the next transform
        self.t += dt
        if self.armed and pulse > 0.6 and self.t - self.t_beat >= 0.15:
            self.armed, self.t_beat = False, self.t
            self.glint = (self.glint + 1 + int(self.brng.integers(0, 2))) % 3
        elif pulse < 0.3:
            self.armed = True
        target = ee ** 1.5
        a = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 1.0
        self.cplx = target if self.cplx is None else self.cplx + (target - self.cplx) * a
        rate = 0.4 + 1.2 * ee

        # ---- host-integrated phases (kicks lurch rates only)
        self.morph += BASE_MORPH * float(p["morph_speed"]) * spd * rate * (1.0 + MORPH_KICK * k) * dt
        self.wob += (0.25 + 0.45 * ee) * spd * dt
        self.spin += BASE_SPIN * spd * rate * dt
        self.drift = math.fmod(self.drift + p["drift_speed"] * spd * dt, 1000.0)

        g = self.g
        spread = float(p["transform_spread"])
        m = float(p["variation_mix"])
        hA, hB, hC = max(0.0, 1.0 - 2.0 * m), 1.0 - abs(2.0 * m - 1.0), max(0.0, 2.0 * m - 1.0)
        wob_amp = WOB_LO + (WOB_HI - WOB_LO) * ee
        nl0 = NL_BASE
        swell = NL_KICK * float(p["glint_kick"]) * k
        Ph = self.Ph
        weights = np.array(g["wt"], np.float64)
        weights[N_TF - 1] *= CPLX_LO + (CPLX_HI - CPLX_LO) * self.cplx       # the complexity transform fades in with energy
        cum = np.cumsum(weights) / weights.sum()
        for i in range(N_TF):
            th = g["th0"][i] + g["om"][i] * self.morph
            ph = g["ph0"][i] + g["nu"][i] * self.morph
            s = g["sc"][i]
            asp = 1.0 - spread * (1.0 - g["asp"][i])
            ct, st, cph, sph = math.cos(th), math.sin(th), math.cos(ph), math.sin(ph)
            # R(th) diag(s, s*asp) R(ph)
            m00, m01 = s * cph, -s * sph
            m10, m11 = s * asp * sph, s * asp * cph
            A = ct * m00 - st * m10
            B = ct * m01 - st * m11
            D = st * m00 + ct * m10
            E = st * m01 + ct * m11
            tb = g["ps0"][i] + g["pt"][i] * self.morph
            tr = g["tr"][i] * (TR_LO + (TR_HI - TR_LO) * spread)
            o = 32 + TF * i
            Ph[o:o + 6] = (A, B, tr * math.cos(tb), D, E, tr * math.sin(tb))
            Ph[o + 6] = g["col"][i]
            Ph[o + 7] = 0.5
            wob = math.sin(self.wob * g["wf"][i] + g["wp"][i])
            r = min(max(g["ratio"][i] + 0.3 * wob_amp * wob, 0.05), 0.95)
            nl = nl0 * (1.0 + wob_amp * 0.5 * math.sin(0.73 * self.wob * g["wf"][i] + 2.0 * g["wp"][i]))
            if i == self.glint:
                nl *= 1.0 + swell
            Ph[o + 8] = max(0.0, 1.0 - nl) + LIN_EXTRA          # linear
            Ph[o + 9] = nl * hA * r                        # sinusoidal
            Ph[o + 10] = nl * hA * (1.0 - r) * 0.6         # spherical
            Ph[o + 11] = nl * hB * r                       # swirl
            Ph[o + 12] = nl * hB * (1.0 - r)               # horseshoe
            Ph[o + 13] = nl * hC * (1.0 - r) * 0.7         # polar
            Ph[o + 14] = nl * hC * r                       # julia
        sym = float(p["symmetry"])
        n0 = int(math.floor(sym))
        frac = sym - n0
        if n0 >= 8:
            n0, frac = 8, 0.0
        # auto-framing happens on the GPU (flame_fit): a slewed log scale from the walkers' radius percentile
        scale = float(p["zoom"])
        Ph[0] = N_TF
        Ph[1] = PSYM
        Ph[2] = n0
        Ph[3] = n0 + 1
        Ph[4] = frac
        Ph[5] = scale * math.cos(self.spin)
        Ph[6] = scale * math.sin(self.spin)
        Ph[7] = 0.5 * self.w
        Ph[8] = 0.5 * self.h
        Ph[9] = p["palette_period"]
        Ph[10] = self.drift
        Ph[12:24] = palette_coeffs(p["palette"])
        Ph[24:24 + N_TF] = cum
        Ph[11] = self.glint
        Ph[28] = GLINT * gk * k
        Ph[29] = MIRROR1
        self.P.set(Ph)
        fit_base = np.float32(math.log(FIT_R * self.h))
        if self.first:                  # converge the walkers and the framing before anything is plotted
            self.first = False
            for _ in range(2):          # (the second fit measures the radius about the centroid the first found)
                launch_2d(self.k_iter, WX, self.wy, (self.walk, self.rng, self.hist, self.P, self.bins, self.fitv,
                                                     self.cen, np.int32(WX), np.int32(0), np.int32(0), np.int32(ITERS)))
                launch_2d(self.k_fit, 1, 1, (self.bins, self.fitv, self.cen, fit_base, np.float32(1.0),
                                             np.float32(FIT_PCT)))
        launch_2d(self.k_iter, WX, self.wy, (self.walk, self.rng, self.hist, self.P, self.bins, self.fitv, self.cen,
                                             np.int32(WX), np.int32(self.w), np.int32(self.h), np.int32(ITERS)))
        af = 1.0 - math.exp(-dt / FIT_TAU) if dt > 0 else 0.0
        launch_2d(self.k_fit, 1, 1, (self.bins, self.fitv, self.cen, fit_base, np.float32(af), np.float32(FIT_PCT)))

        # ---- tonemap + decay
        tau = float(p["persistence"]) * (1.15 - 0.4 * ee)
        decay = math.exp(-dt / tau) if dt > 0 else 1.0
        self.hacc += 1.0
        mean = max(self.nw * ITERS * self.hacc / (self.w * self.h), 1e-6)
        expo = (0.75 + 0.45 * ee)
        flash = 1.0 + FLASH * gk * k
        Q = np.array([1.0 / mean, expo / math.log1p(EXPO_REF), 1.0 / float(p["gamma"]), p["vibrancy"],
                      float(p["intensity"]) * flash, decay, 0.35, DE_COUNT,
                      FLOOR_LO, FLOOR_HI, DE_NEED, GAMMA_T], np.float32)
        self.Q.set(Q)
        launch_2d(self.k_down, self.w1, self.h1, (self.hist, self.m1, np.int32(self.w1), np.int32(self.h1),
                                                  np.int32(self.w), np.int32(self.h)))
        launch_2d(self.k_down, self.w2, self.h2, (self.m1, self.m2, np.int32(self.w2), np.int32(self.h2),
                                                  np.int32(self.w1), np.int32(self.h1)))
        launch_2d(self.k_tone, self.w, self.h, (self.hist, self.hist2, self.buf, np.int32(self.w), np.int32(self.h),
                                                self.Q, self.m1, self.m2))
        self.hist, self.hist2 = self.hist2, self.hist
        self.hacc *= decay
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.hist = self.hist2 = self.m1 = self.m2 = self.walk = self.rng = self.bins = self.fitv = self.cen = None
        self.P = self.Q = None
