"""Lichtenberg growth: glowing dendrites grown by diffusion-limited aggregation, burnt out and regrown each phrase.

Source: Original (inspired by https://paulbourke.net/fractals/dla/ and https://en.wikipedia.org/wiki/Lichtenberg_figure)
Author: smfx (original; DLA by Witten and Sander, 1981, as described by Paul Bourke; xscreensaver coral is a permissive reference only)
License: MIT
license_class: permissive

Written from scratch (no code copied).  Pipeline, all on the GPU:
  1. walkers: 131072 random walkers on a fixed ~960x540 lattice (one 2D grid of float 'birth times' per layer;
     0 = empty, -1 = an invisible seed cell, > 0 = the layer time the cell stuck).  Walkers launch at a random
     angle about ``launch`` cells from a random cell of the growing figure (an append list of stuck cells),
     then 'walk on circles' while a coarse distance field (2x2-cell blocks, chamfer-relaxed in place every
     frame, seeded analytically) says they are far away, and take unit lattice steps near it.  A walker that
     touches the figure (4-neighbour contact) sticks with the stick probability (branchiness, energy, and an
     optional 6-fold angular preference about the nearest anchor = anisotropy), stamps its birth time and
     relaunches.  A per-frame device quota (atomics) paces the growth so a figure fills the frame over a
     phrase whatever the walker flux: walkers refused by the quota relaunch, which thins the flux uniformly
     and so keeps the DLA statistics.
  2. emit: per lattice cell, colour by age (pal(birth / cycle * span * period + drift + per-cycle hue)), a
     white-hot tip glow for recent growth, a slow dim with age, smooth shimmer, and the beat flash of recent
     tips.  Two layers: the live one, and the previous figure, which burns out like a fuse from its seed to
     its tips (an ember band runs along the branches) while the new figure starts growing: never a hard reset.
  3. glow: a 4-level tent-filtered mip pyramid of the emission; draw = an anti-aliased gaussian core (4x4
     lattice taps) + weighted bilinear mips + a dark palette sky, tone-mapped; then present() upsamples.

Seed layouts (structural): point (centre, jittered per cycle), line (top / bottom edge, alternating),
ring, many (6-8 jittered points).  ``style`` morphs blue lightning (thin, white-hot tips, wide bloom, sky
flash) -> warm coral (fat, dense, low stickiness, wide age gradient) -> white frost (thin, crisp, 6-fold arms).

Macros: energy -> active walkers (0.35 + 0.65 e^1.3), walker steps, growth quota (0.45 + 1.1 e), stick
probability (0.75 + 0.5 e), glow (0.45 + 0.75 e), shimmer rate.  pulse (k = pulse (0.35 + 0.65 e)) -> flash of
recently grown tips (by age), a growth spurt (quota x (1 + 2.5 k), steps x (1 + 0.8 k)), a brightness punch and
a lightning sky flash.  speed scales the cycle clock, walker steps and every rate (0 freezes).  palette by age
+ drift; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

WGW, WGH = 512, 256                      # walkers as a 2D launch grid (131072)
NW = WGW * WGH
CAP = 1 << 19                            # stuck-cell list capacity
SIM_W = 960                              # lattice width (height follows the aspect)
LAYOUTS = ["point", "line", "ring", "many"]
MASS_BASE = {"point": 30000.0, "line": 46000.0, "ring": 42000.0, "many": 42000.0}

# style anchors: lightning (0), coral (0.5), frost (1)
STYLE = {
    #            lightning  coral   frost
    "w_core":    (0.62, 1.25, 0.60),
    "g1":        (0.55, 1.00, 0.45),
    "g2":        (0.90, 0.60, 0.45),
    "g3":        (1.40, 0.35, 0.50),
    "g4":        (2.00, 0.25, 0.65),
    "white_hot": (0.35, 0.04, 0.30),
    "tip_white": (0.85, 0.12, 0.65),
    "tip_tau":   (0.35, 1.60, 0.70),
    "tip_gain":  (1.80, 0.55, 1.10),
    "base":      (0.50, 0.80, 0.62),
    "old_floor": (0.35, 0.80, 0.60),
    "tau_old":   (2.50, 8.00, 5.00),
    "stick":     (1.00, 0.30, 0.90),
    "aniso":     (0.00, 0.00, 0.75),
    "span":      (0.35, 0.90, 0.25),
    "shimmer":   (0.35, 0.08, 0.25),
    "sh_rate":   (2.20, 0.40, 1.00),
    "bg":        (0.05, 0.07, 0.04),
    "sky_flash": (1.00, 0.15, 0.40),
    "launch":    (80.0, 40.0, 70.0),
    "mass":      (1.00, 1.60, 0.90),
    "exposure":  (1.30, 1.10, 1.25),
    "ember":     (1.60, 1.20, 1.00),
}

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 now (live layer) | 15 now (old layer) | 16 live cycle length
#  17 old layer end | 18 burn progress | 19 1/tip tau | 20 tip gain | 21 base | 22 old floor | 23 1/tau old
#  24 flash | 25 span | 26 hue live | 27 hue old | 28 shimmer | 29 shimmer phase | 30 punch | 31 tip whiteness
#  32 ember gain
#  40 core 1/w^2 | 41 core norm | 42..45 glow gains | 46 white hot | 47 exposure | 48 intensity | 49 sky level
#  50 vignette
#  60 stick prob | 61 anisotropy | 62 launch radius | 63 kill radius | 64 layout | 65 n anchors
#  66..89 anchors (x, y) | 90..101 anchor arm angle | 102 line y | 103 ring cx | 104 ring cy | 105 ring R
NP = 128
_SRC = HELPERS + PALETTE_CUDA + r"""
#define WGW %(wgw)d
__device__ __forceinline__ int occ(const float* __restrict__ L, int x, int y, int Gw, int Gh) {
    return (x >= 0 && y >= 0 && x < Gw && y < Gh) ? (L[y * Gw + x] != 0.f) : 0;
}
// emissive palette colour: a dark phase of the cosine palette folds towards the bright side, max channel ~1
__device__ __forceinline__ float3 lpal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.1f, 0.45f, m);
    c.x += w * (clamp01(PAL[0] + 0.6f * fabsf(PAL[3])) - c.x);
    c.y += w * (clamp01(PAL[1] + 0.6f * fabsf(PAL[4])) - c.y);
    c.z += w * (clamp01(PAL[2] + 0.6f * fabsf(PAL[5])) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.5f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
// 6-fold arm preference about the nearest anchor
__device__ float aniso_w(int x, int y, const float* __restrict__ P) {
    const int n = (int)P[65];
    float best = 1e30f, bx = 1.f, by = 0.f, th0 = 0.f;
    for (int k = 0; k < 12; ++k) {
        if (k >= n) break;
        const float dx = x + 0.5f - P[66 + 2 * k], dy = y + 0.5f - P[67 + 2 * k];
        const float d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; bx = dx; by = dy; th0 = P[90 + k]; }
    }
    if (best < 4.f) return 1.f;
    const float c = 0.5f + 0.5f * __cosf(6.f * (atan2f(by, bx) - th0));
    return c * c * c;
}

extern "C" __global__
void lb_seed(float* __restrict__ L, float* __restrict__ D, int* __restrict__ ctr, int* __restrict__ list,
             const int Gw, const int Gh, const int Bw, const int Bh, const float* __restrict__ P, const int cap)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Gw || y >= Gh) return;
    const int layout = (int)P[64], n = (int)P[65];
    const float cx = x + 0.5f, cy = y + 0.5f;
    bool seed = false;
    if (layout == 1) seed = (y == (int)P[102]);
    else if (layout == 2) seed = fabsf(sqrtf((cx - P[103]) * (cx - P[103]) + (cy - P[104]) * (cy - P[104])) - P[105]) < 0.5f;
    else {
        for (int k = 0; k < 12; ++k) {
            if (k >= n) break;
            if (x == (int)P[66 + 2 * k] && y == (int)P[67 + 2 * k]) seed = true;
        }
    }
    L[y * Gw + x] = seed ? -1.f : 0.f;
    if (seed) { const int k = atomicAdd(ctr, 1); if (k < cap) list[k] = x | (y << 16); }
    if ((x & 1) == 0 && (y & 1) == 0) {
        const int bx = x >> 1, by = y >> 1;
        if (bx < Bw && by < Bh) {
            const float px = x + 1.f, py = y + 1.f;          // block centre
            float d;
            if (layout == 1) d = fabsf(py - (P[102] + 0.5f));
            else if (layout == 2) d = fabsf(sqrtf((px - P[103]) * (px - P[103]) + (py - P[104]) * (py - P[104])) - P[105]);
            else {
                d = 1e9f;
                for (int k = 0; k < 12; ++k) {
                    if (k >= n) break;
                    const float dx = px - P[66 + 2 * k], dy = py - P[67 + 2 * k];
                    d = fminf(d, sqrtf(dx * dx + dy * dy));
                }
            }
            D[by * Bw + bx] = fmaxf(d - 1.5f, 0.f);
        }
    }
}

// in-place chamfer relaxation of the block distance field (benign races: every value is an upper bound)
extern "C" __global__
void lb_relax(float* __restrict__ D, const int Bw, const int Bh, int* __restrict__ ctr)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x == 0 && y == 0) ctr[1] = 0;                   // this frame's stick count
    if (x >= Bw || y >= Bh) return;
    const float d = D[y * Bw + x];
    if (d <= 0.f) return;
    float m = d;
    #pragma unroll
    for (int j = -1; j <= 1; ++j) {
        #pragma unroll
        for (int i = -1; i <= 1; ++i) {
            if (i == 0 && j == 0) continue;
            const int xx = x + i, yy = y + j;
            if (xx < 0 || yy < 0 || xx >= Bw || yy >= Bh) continue;
            m = fminf(m, D[yy * Bw + xx] + ((i != 0 && j != 0) ? 2.83f : 2.f));
        }
    }
    if (m < d) D[y * Bw + x] = m;
}

extern "C" __global__
void lb_walk(int2* __restrict__ Wk, float* __restrict__ L, float* __restrict__ D, int* __restrict__ ctr,
             int* __restrict__ list, const int Gw, const int Gh, const int Bw, const int Bh,
             const float* __restrict__ P, const unsigned int salt, const int nsteps, const int nact,
             const int quota, const int mcap, const float now)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    const int i = gy * WGW + gx;
    if (gx >= WGW || i >= nact) return;
    int2 q = Wk[i];
    const float Rl = P[62], Rk = P[63], pstick = P[60], an = P[61];
    const unsigned int ui = (unsigned int)i;
    for (int s = 0; s < nsteps; ++s) {
        const unsigned int rs = salt + (unsigned int)s;
        if (q.x < 0) {                                       // launch near a random cell of the figure
            const int n = min(*((volatile int*)ctr), %(cap)d);
            if (n <= 0) break;
            for (int t = 0; t < 4; ++t) {
                const unsigned int tt = rs * 4u + (unsigned int)t;
                const int c = list[min((int)(hash1(ui, tt, 1u) * n), n - 1)];
                float sn, cs;
                __sincosf(6.2831853f * hash1(ui, tt, 2u), &sn, &cs);
                const float rad = Rl * (0.75f + 0.5f * hash1(ui, tt, 3u));
                const int sx = (c & 0xFFFF) + (int)rintf(rad * cs), sy = (c >> 16) + (int)rintf(rad * sn);
                if (sx < 0 || sy < 0 || sx >= Gw || sy >= Gh) continue;
                if (D[(sy >> 1) * Bw + (sx >> 1)] < 0.5f * rad) continue;
                q = make_int2(sx, sy);
                break;
            }
            continue;
        }
        const float d = D[(q.y >> 1) * Bw + (q.x >> 1)];
        if (d > Rk) { q.x = -1; continue; }
        if (d >= 6.f) {                                      // far: walk on circles
            const float r = fminf(0.85f * d - 2.5f, 64.f);
            float sn, cs;
            __sincosf(6.2831853f * hash1(ui, rs, 4u), &sn, &cs);
            const int nx = q.x + (int)rintf(r * cs), ny = q.y + (int)rintf(r * sn);
            if (nx < 0 || ny < 0 || nx >= Gw || ny >= Gh) { q.x = -1; continue; }
            if (L[ny * Gw + nx] == 0.f) q = make_int2(nx, ny);
            continue;
        }
        if (L[q.y * Gw + q.x] != 0.f) { q.x = -1; continue; }   // swallowed by growth
        const unsigned int h = hash_u(ui * 0x9E3779B1u ^ hash_u(rs * 0x85EBCA77u + 0x632BE5ABu));
        const int dir = (int)(h & 3u);
        const int nx = q.x + (dir == 0) - (dir == 1), ny = q.y + (dir == 2) - (dir == 3);
        if (nx < 0 || ny < 0 || nx >= Gw || ny >= Gh) { q.x = -1; continue; }
        if (L[ny * Gw + nx] != 0.f) continue;
        q = make_int2(nx, ny);
        if (!(occ(L, nx + 1, ny, Gw, Gh) || occ(L, nx - 1, ny, Gw, Gh) || occ(L, nx, ny + 1, Gw, Gh) ||
              occ(L, nx, ny - 1, Gw, Gh))) continue;
        float ps = pstick;
        if (an > 0.f) ps *= (1.f - an) + an * aniso_w(nx, ny, P);
        if ((h >> 8) * (1.f / 16777216.f) >= ps) continue;
        if (atomicAdd(ctr + 1, 1) >= quota || *((volatile int*)ctr) >= mcap) { q.x = -1; continue; }
        L[ny * Gw + nx] = now;
        D[(ny >> 1) * Bw + (nx >> 1)] = 0.f;
        const int k = atomicAdd(ctr, 1);
        if (k < %(cap)d) list[k] = nx | (ny << 16);
        q.x = -1;
    }
    Wk[i] = q;
}

__device__ float3 cell_emit(float v, float now, float Tn, float hue, float burn, float x, float y,
                            const float* __restrict__ P)
{
    const float rec = fmaxf(now - v, 0.f);
    const float u = v / fmaxf(Tn, 1e-3f);
    const float tip = __expf(-rec * P[19]);
    const float agef = P[22] + (1.f - P[22]) * __expf(-rec * P[23]);
    const float fl = P[24] * __expf(-rec * 0.8f);
    float sh = 1.f;
    if (P[28] > 0.f) sh = 1.f + P[28] * (2.f * vnoise(x * 0.06f, y * 0.06f + P[29], 7u) - 1.f);
    float b = (P[21] * agef + P[20] * tip * (1.f + fl) + 0.6f * fl) * sh * P[30];
    float t = u * P[25] * P[12] + P[13] + hue;
    float wt = clamp01(P[31] * tip + 0.35f * fl);
    if (burn >= 0.f) {                                    // fuse: burns from the seed (u = 0) out to the tips
        const float f = -0.08f + 1.2f * burn;
        if (u < f - 0.1f) return make_float3(0.f, 0.f, 0.f);
        const float ember = smooth(f - 0.1f, f - 0.03f, u) * (1.f - smooth(f, f + 0.08f, u));
        b = b * (1.f - 0.45f * burn) * (1.f - smooth(0.82f, 1.f, burn)) + ember * P[32];
        wt = fmaxf(wt, 0.55f * ember);
        t += 0.25f * ember;
    }
    float3 c = lpal(t, P);
    c.x += (1.f - c.x) * wt; c.y += (1.f - c.y) * wt; c.z += (1.f - c.z) * wt;
    return make_float3(c.x * b, c.y * b, c.z * b);
}

extern "C" __global__
void lb_emit(float4* __restrict__ E, const float* __restrict__ La, const float* __restrict__ Lo, const int Gw,
             const int Gh, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Gw || y >= Gh) return;
    const int idx = y * Gw + x;
    const float va = La[idx], vo = Lo[idx];
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (va > 0.f) {
        const float3 e = cell_emit(va, P[14], P[16], P[26], -1.f, (float)x, (float)y, P);
        c.x += e.x; c.y += e.y; c.z += e.z;
    }
    if (vo > 0.f && P[18] < 1.f) {
        const float3 e = cell_emit(vo, P[15], P[17], P[27], P[18], (float)x, (float)y, P);
        c.x += e.x; c.y += e.y; c.z += e.z;
    }
    E[idx] = make_float4(c.x, c.y, c.z, 0.f);
}

// 2x downsample with a separable (1, 3, 3, 1) / 8 tent
extern "C" __global__
void lb_down(const float4* __restrict__ S, const int sw, const int sh, float4* __restrict__ Dst, const int dw,
             const int dh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= dw || y >= dh) return;
    const float w[4] = {0.125f, 0.375f, 0.375f, 0.125f};
    float4 a = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int yy = min(max(2 * y - 1 + j, 0), sh - 1);
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const int xx = min(max(2 * x - 1 + i, 0), sw - 1);
            const float4 s = S[yy * sw + xx];
            const float k = w[i] * w[j];
            a.x += s.x * k; a.y += s.y * k; a.z += s.z * k;
        }
    }
    Dst[y * dw + x] = a;
}

__device__ __forceinline__ float3 bl4(const float4* __restrict__ T, int w, int h, float fx, float fy) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const float4 a = T[ya * w + xa], b = T[ya * w + xb], c = T[yb * w + xa], d = T[yb * w + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float3(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z));
}

extern "C" __global__
void lb_draw(uchar4* __restrict__ out, const int Wi, const int Hi, const float4* __restrict__ E0, const int Gw,
             const int Gh, const float4* __restrict__ E1, const int w1, const int h1,
             const float4* __restrict__ E2, const int w2, const int h2, const float4* __restrict__ E3, const int w3,
             const int h3, const float4* __restrict__ E4, const int w4, const int h4, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wi || y >= Hi) return;
    const float sx = (x + 0.5f) * (float)Gw / (float)Wi, sy = (y + 0.5f) * (float)Gh / (float)Hi;
    const int i0 = (int)floorf(sx - 1.5f), j0 = (int)floorf(sy - 1.5f);
    const float iw2 = P[40];
    float3 core = make_float3(0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        const int yy = j0 + j;
        if (yy < 0 || yy >= Gh) continue;
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const int xx = i0 + i;
            if (xx < 0 || xx >= Gw) continue;
            const float4 e = E0[yy * Gw + xx];
            if (e.x + e.y + e.z <= 0.f) continue;
            const float dx = xx + 0.5f - sx, dy = yy + 0.5f - sy;
            const float k = __expf(-(dx * dx + dy * dy) * iw2);
            core.x += e.x * k; core.y += e.y * k; core.z += e.z * k;
        }
    }
    core.x *= P[41]; core.y *= P[41]; core.z *= P[41];
    const float3 a1 = bl4(E1, w1, h1, sx * 0.5f, sy * 0.5f);
    const float3 a2 = bl4(E2, w2, h2, sx * 0.25f, sy * 0.25f);
    const float3 a3 = bl4(E3, w3, h3, sx * 0.125f, sy * 0.125f);
    const float3 a4 = bl4(E4, w4, h4, sx * 0.0625f, sy * 0.0625f);
    const float hot = fmaxf(core.x, fmaxf(core.y, core.z));
    const float wh = P[46] * hot * hot;
    float3 c;
    c.x = core.x + P[42] * a1.x + P[43] * a2.x + P[44] * a3.x + P[45] * a4.x + wh;
    c.y = core.y + P[42] * a1.y + P[43] * a2.y + P[44] * a3.y + P[45] * a4.y + wh;
    c.z = core.z + P[42] * a1.z + P[43] * a2.z + P[44] * a3.z + P[45] * a4.z + wh;
    // dark palette sky (flashes with the beat in lightning style)
    const float u = (x + 0.5f) / Wi - 0.5f, v = (y + 0.5f) / Hi - 0.5f;
    const float r2 = u * u + v * v;
    const float3 sk = pal(P[13] + 0.55f + 0.2f * v, P);
    const float sl = P[49] * (1.f - 0.9f * r2);
    c.x += sk.x * sl; c.y += sk.y * sl; c.z += sk.z * sl;
    const float ex = P[47];
    const float g = 255.f * P[48] * (1.f - P[50] * r2);
    out[y * Wi + x] = pack(make_float4((1.f - __expf(-c.x * ex)) * g, (1.f - __expf(-c.y * ex)) * g,
                                       (1.f - __expf(-c.z * ex)) * g, 255.f));
}
""" % {"wgw": WGW, "cap": CAP}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _style(s: float) -> dict:
    s = min(max(s, 0.0), 1.0)
    if s <= 0.5:
        i, t = 0, _ss(0.0, 1.0, s / 0.5)
    else:
        i, t = 1, _ss(0.0, 1.0, (s - 0.5) / 0.5)
    return {k: v[i] + (v[i + 1] - v[i]) * t for k, v in STYLE.items()}


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class Lichtenberg(Source):
    name = "lichtenberg"
    label = "Lichtenberg growth"
    description = ("Glowing dendrites grown live by 130k random walkers (diffusion-limited aggregation) from a "
                   "point, an edge, a ring or many seeds; each phrase the figure burns out like a fuse and regrows. "
                   "Blue lightning -> warm coral -> white frost.")
    params = _order({
        "branchiness": num(0.0, 1.0, 0.75, "Branchiness (dense -> lacy)", tier="regional", basic=True),
        "style": num(0.0, 1.0, 0.12, "Style (lightning -> coral -> frost)", tier="regional", basic=True),
        "anisotropy": num(0.0, 1.0, 0.15, "Anisotropy (6-fold arms)", tier="regional"),
        "regrow_cycle_length": num(4.0, 60.0, 15.5, "Regrow cycle", step=0.5, unit="s", tier="regional"),
        "reach": num(0.2, 1.0, 0.7, "Reach (how far each figure grows)", tier="regional"),
        "seed_layout": enum(LAYOUTS, "point", "Seed layout", tier="structural"),
        **standard_scene_params(palette="ice", render_scale=0.5, period=1.0, drift=0.03),
    }, ["branchiness", "style", "anisotropy", "regrow_cycle_length", "reach", "energy", "pulse", "speed",
        "intensity", "palette_period", "drift_speed"])
    tags = {"family": "light", "dims": "2d", "technique": ["simulation", "fractal"]}

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw = SIM_W
        self.gh = max(64, int(round(SIM_W * ctx.height / ctx.width / 2)) * 2)
        self.bw, self.bh = (self.gw + 1) // 2, (self.gh + 1) // 2
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.L = cp.zeros((2, self.gh, self.gw), cp.float32)
        self.D = cp.zeros((self.bh, self.bw), cp.float32)
        self.ctr = cp.zeros(4, cp.int32)
        self.list = cp.zeros(CAP, cp.int32)
        self.walk = cp.full((NW, 2), -1, cp.int32)
        self.mips = [cp.zeros((self.gh, self.gw, 4), cp.float32)]
        mw, mh = self.gw, self.gh
        for _ in range(4):
            mw, mh = (mw + 1) // 2, (mh + 1) // 2
            self.mips.append(cp.zeros((mh, mw, 4), cp.float32))
        mod = raw_module(_SRC, _OPTIONS)
        self.k_seed = mod.get_function("lb_seed")
        self.k_relax = mod.get_function("lb_relax")
        self.k_walk = mod.get_function("lb_walk")
        self.k_emit = mod.get_function("lb_emit")
        self.k_down = mod.get_function("lb_down")
        self.k_draw = mod.get_function("lb_draw")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.seed = int(params["seed"])
        self.layout = str(params["seed_layout"])
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))
        self.drift = 0.0
        self.shim = 0.0
        self.salt = 1
        self.qacc = 0.0
        self.cycle = 0
        self.active = 0
        self.t_live = 0.0                  # live layer age (sim s)
        self.t_old = 0.0                   # old layer age
        self.old_end = 1.0                 # old layer age when it was retired
        self.burn_t = 1.0
        self.burn_len = 1.0                # burn progress = burn_t / burn_len (>= 1: old layer gone)
        self.hue = [0.0, 0.0]
        self.anchor = np.zeros(38, np.float32)   # n, (x, y) x12, theta x12, line y, ring cx, cy, R
        self._start_layer(params)
        # pre-grow the opening figure to ~45% of its cycle so the scene arrives formed
        tc = float(params["regrow_cycle_length"])
        n_pre = 90
        dtp = 0.45 * tc / n_pre
        for _ in range(n_pre):
            self._frame(params, dtp, pre=True)

    # ------------------------------------------------------------------ layers
    def _start_layer(self, p: dict) -> None:
        rng = np.random.default_rng([self.seed & 0x7FFFFFFF, self.cycle])
        gw, gh = self.gw, self.gh
        pts: list[tuple[float, float]] = []
        line_y, ring = 0.0, (0.0, 0.0, 0.0)
        if self.layout == "point":
            pts = [(gw * 0.5 + rng.uniform(-0.14, 0.14) * gh, gh * 0.5 + rng.uniform(-0.1, 0.1) * gh)]
        elif self.layout == "many":
            cells = [(i, j) for i in range(4) for j in range(2)]
            order = rng.permutation(len(cells))[: int(rng.integers(6, 9))]
            for o in order:
                i, j = cells[int(o)]
                pts.append(((i + 0.5 + rng.uniform(-0.32, 0.32)) * gw / 4, (j + 0.5 + rng.uniform(-0.3, 0.3)) * gh / 2))
        elif self.layout == "line":
            line_y = 1.0 if self.cycle % 2 == 0 else float(gh - 2)
            pts = [((k + 0.5 + rng.uniform(-0.25, 0.25)) * gw / 8, line_y + 0.5) for k in range(8)]
        else:
            R = 0.36 * gh
            cx, cy = gw * 0.5, gh * 0.5
            ring = (cx, cy, R)
            a0 = rng.uniform(0, 2 * math.pi)
            pts = [(cx + R * math.cos(a0 + k * 2 * math.pi / 10), cy + R * math.sin(a0 + k * 2 * math.pi / 10))
                   for k in range(10)]
        pts = pts[:12]
        if self.layout in ("point", "many"):            # seeds sit on cell centres
            pts = [(math.floor(min(max(x, 2), gw - 3)) + 0.5, math.floor(min(max(y, 2), gh - 3)) + 0.5) for x, y in pts]
        a = self.anchor
        a[:] = 0.0
        a[0] = len(pts)
        for k, (x, y) in enumerate(pts):
            a[1 + 2 * k], a[2 + 2 * k] = x, y
        a[25:37] = rng.uniform(0.0, 2 * math.pi / 6, 12)
        self.line_y, self.ring = line_y, ring
        self.hue[self.active] = (self.cycle * 0.13) % 1.0
        # seed the live layer: clear it, stamp seeds, analytic distance field, fresh walkers
        self._fill_layout()
        self.P.set(self.Ph)
        self.ctr.fill(0)
        self.walk.fill(-1)
        launch_2d(self.k_seed, self.gw, self.gh, (self.L[self.active], self.D, self.ctr, self.list,
                                                  np.int32(self.gw), np.int32(self.gh), np.int32(self.bw),
                                                  np.int32(self.bh), self.P, np.int32(CAP)))
        self.t_live = 0.0
        self.qacc = 0.0

    def _fill_layout(self) -> None:
        P, a = self.Ph, self.anchor
        P[64] = float(LAYOUTS.index(self.layout))
        P[65] = a[0]
        P[66:90] = a[1:25]
        P[90:102] = a[25:37]
        P[102] = self.line_y
        P[103], P[104], P[105] = self.ring

    # ------------------------------------------------------------------ per frame
    def _frame(self, p: dict, dt: float, pre: bool = False) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = 0.0 if pre else min(max(float(p["pulse"]), 0.0), 1.0)
        spd = 1.0 if pre else max(float(p["speed"]), 0.0)
        ds = dt * spd
        st = _style(float(p["style"]))
        k = pulse * (0.35 + 0.65 * e)
        tc = float(p["regrow_cycle_length"])

        # palette (session palette wins; eased so a change never snaps)
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))

        # clocks
        self.t_live += ds
        self.t_old += ds
        self.burn_t += ds
        if not pre:
            self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        self.shim = _wrap(self.shim + ds * st["sh_rate"] * (0.5 + e), 1000.0)
        if self.t_live >= tc and self.burn_t >= self.burn_len:    # phrase over: retire, burn, regrow
            self.t_old = self.t_live
            self.old_end = max(self.t_live, 1e-3)
            self.burn_t = 0.0
            self.burn_len = min(max(0.5 * tc, 1.5), 8.0)
            self.active ^= 1
            self.cycle += 1
            self._start_layer(p)

        # growth pacing
        stick = min(max((0.03 + 0.97 * float(p["branchiness"]) ** 1.6) * st["stick"] * (0.75 + 0.5 * e), 0.02), 1.0)
        mass = MASS_BASE[self.layout] * (float(p["reach"]) / 0.7) * st["mass"] * (1.0 + 0.8 * (1.0 - stick))
        rate = mass / (0.8 * tc)
        self.qacc += rate * ds * (1.0 if pre else (0.45 + 1.1 * e) * (1.0 + 2.5 * k))
        quota = int(self.qacc)
        self.qacc -= quota
        if pre:
            nsteps = 40
            nact = NW
        else:
            nsteps = int(round((10.0 + 14.0 * e) * (1.0 + 0.8 * k) * ds * 60.0)) if ds > 0 else 0
            nsteps = min(max(nsteps, 1 if ds > 0 else 0), 48)
            nact = int(NW * (0.35 + 0.65 * e ** 1.3))

        P = self.Ph
        P[0:12] = self.pal
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        a = self.active
        P[14] = self.t_live
        P[15] = self.t_old
        P[16] = tc
        P[17] = self.old_end
        P[18] = min(self.burn_t / self.burn_len, 1.0)
        P[19] = 1.0 / st["tip_tau"]
        P[20] = st["tip_gain"] * (0.8 + 0.4 * e)
        P[21] = st["base"]
        P[22] = st["old_floor"]
        P[23] = 1.0 / st["tau_old"]
        P[24] = 2.2 * k
        P[25] = st["span"]
        P[26] = self.hue[a]
        P[27] = self.hue[a ^ 1]
        P[28] = st["shimmer"]
        P[29] = self.shim
        P[30] = 1.0 + 0.25 * k
        P[31] = st["tip_white"]
        P[32] = st["ember"]
        cpp = self.gw / self.w                               # lattice cells per internal pixel
        wc = max(st["w_core"], 0.55 * cpp)
        P[40] = 1.0 / (wc * wc)
        P[41] = 1.0 / (1.772 * wc)
        gg = 0.45 + 0.75 * e
        P[42], P[43], P[44], P[45] = (st["g1"] * gg, st["g2"] * gg, st["g3"] * gg, st["g4"] * gg)
        P[46] = st["white_hot"]
        P[47] = st["exposure"]
        P[48] = float(p["intensity"])
        P[49] = st["bg"] * (1.0 + 2.5 * k * st["sky_flash"])
        P[50] = 0.25
        P[60] = stick
        P[61] = min(float(p["anisotropy"]) + st["aniso"], 0.95)
        P[62] = st["launch"]
        P[63] = 2.6 * st["launch"]
        self._fill_layout()
        self.P.set(P)

        if nsteps > 0:
            for _ in range(2):
                launch_2d(self.k_relax, self.bw, self.bh, (self.D, np.int32(self.bw), np.int32(self.bh), self.ctr))
            mcap = int(min(CAP - 16, 2.5 * mass + 4000))
            launch_2d(self.k_walk, WGW, WGH, (self.walk, self.L[a], self.D, self.ctr, self.list, np.int32(self.gw),
                                              np.int32(self.gh), np.int32(self.bw), np.int32(self.bh), self.P,
                                              np.uint32(self.salt), np.int32(nsteps), np.int32(nact),
                                              np.int32(quota), np.int32(mcap), np.float32(self.t_live + 1e-3)))
            self.salt = (self.salt + nsteps + 1) & 0x3FFFFFFF

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._frame(p, dt)
        a = self.active
        m = self.mips
        launch_2d(self.k_emit, self.gw, self.gh, (m[0], self.L[a], self.L[a ^ 1], np.int32(self.gw),
                                                  np.int32(self.gh), self.P))
        for i in range(4):
            sh, sw = m[i].shape[:2]
            dh, dw = m[i + 1].shape[:2]
            launch_2d(self.k_down, dw, dh, (m[i], np.int32(sw), np.int32(sh), m[i + 1], np.int32(dw), np.int32(dh)))
        dims = [(x.shape[1], x.shape[0]) for x in m]
        launch_2d(self.k_draw, self.w, self.h, (
            self.buf, np.int32(self.w), np.int32(self.h), m[0], np.int32(self.gw), np.int32(self.gh),
            m[1], np.int32(dims[1][0]), np.int32(dims[1][1]), m[2], np.int32(dims[2][0]), np.int32(dims[2][1]),
            m[3], np.int32(dims[3][0]), np.int32(dims[3][1]), m[4], np.int32(dims[4][0]), np.int32(dims[4][1]),
            self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.L = self.D = self.walk = self.list = self.mips = self.buf = None
