"""Demo fire: the classic chunky demoscene fire, a heat field rising from emitters, cooling and flickering.

Source: Original (inspired by https://fabiensanglard.net/doom_fire_psx/ and https://en.wikipedia.org/wiki/Demo_effect)
Author: smfx (original; algorithm per Fabien Sanglard's 'How DOOM fire was done'; xscreensaver xflame is a permissive reference only)
License: MIT
license_class: permissive

Written from scratch from the published description of the PSX DOOM fire (no code copied):
  * a heat grid (about 1/4 of the internal resolution: 480x270 at render_scale 0.5, 960x540 at 1.0); every
    simulation step each cell takes the heat of the cell one row below, picked with a random -1/0/+1 column
    jitter (the DOOM trick, done as a gather so it runs in parallel), minus a random cooling amount;
  * on top of the classic recipe: a cooling map (2-octave value noise) that rides upward with the flames, so a
    parcel of fire keeps its cooling rate and forms distinct tongues (Hugo Elias' cooling-map idea); a sheared
    wind and a smooth turbulence field that push the gather sideways; and a spark layer (comets with short
    tails, two rows per step, carried sideways by their own smooth swirl) seeded at the flame tips;
  * emitters re-inject heat each step with max(): a bottom line (modulated along x so tongues form), a ring,
    and ten drifting embers.  ``emitter_shape`` 0 -> 1 -> 2 cross-fades line -> ring -> embers with
    continuous weights;
  * all randomness is hashed in-kernel from (cell, step counter); the step counter advances by an accumulator
    (steps/s = rise speed x speed x bellows), never by the frame count, so frame skips don't change the look.

Display (internal buffer at render_scale, then present()): ``chunkiness`` morphs a smooth tent-filtered
bilinear read of the grid (0) into block-averaged pixel-art blocks of 1 -> 3.2 cells anchored at the bottom
centre, with heat quantised to 12 bands like an indexed palette (1).  Colour is a black-body ramp per channel
whose hue comes from the palette: C = pal(heat x period + drift) (dark phases folded to the bright side,
normalised); channels strong in C light first, weaker ones later, so ember gives black -> red -> orange ->
yellow -> white, ice gives blue gas, acid/forest chemical green, dusk/candy purple-pink.  A 1/4-res blur of
the heat adds a glow halo (screen blend) and the sparks are screened on top.

Macros: energy -> emitter heat (0.9 + 0.3 e), rise speed (steps/s x (0.25 + 0.45 e)), turbulence
(x (0.3 + 0.9 e)), flame height (less cooling, x (0.62 + 0.62 e)), spark rate (0.25 + 1.2 e^1.3), wind gusts,
glow and the drift of the base modulation and embers.  pulse (bellows) -> emitter heat (+0.45), upward
velocity (steps/s x (1 + 0.9 pulse)), a little extra flame height, extra sparks and a small brightness punch.
speed scales the substep accumulator and every phase (0 freezes the fire).  intensity is output gain.
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

NEMB = 10            # drifting embers
NP = 40 + 4 * NEMB   # P floats
MAX_SUB = 8          # max simulation steps per frame

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 emitter heat | 15 mean cooling per step | 16 wind (cells/step)
#  17 turbulence amp (cells/step) | 18 turbulence freq | 19 turbulence x-scroll | 20 cooling-map freq
#  21 base modulation freq | 22 base modulation time | 23 w line | 24 w ring | 25 w embers
#  26 ring cx | 27 ring cy | 28 ring radius | 29 ring band | 30 spark keep | 31 spark death p | 32 spark spawn p
#  33 block size (cells) | 34 chunky weight | 35 quantise weight | 36 output gain | 37 glow gain
#  38 spark gain | 39 spark swirl freq | 40.. embers (x, y, r, heat) in cells
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NEMB %(nemb)d
__device__ __forceinline__ float2 ld2(const float2* __restrict__ F, int x, int y, const int GW, const int GH) {
    if (y >= GH) return make_float2(0.f, 0.f);
    x = min(max(x, 0), GW - 1); y = max(y, 0);
    return F[y * GW + x];
}
// one row, linear in x (fractional sideways gather)
__device__ __forceinline__ float2 rowlerp(const float2* __restrict__ F, float sx, int y, const int GW, const int GH) {
    const float fx = floorf(sx);
    const int x0 = (int)fx;
    const float t = sx - fx;
    const float2 a = ld2(F, x0, y, GW, GH), b = ld2(F, x0 + 1, y, GW, GH);
    return make_float2(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
}
// bilinear read of the grid, cell centres at i + 0.5, clamped at the edges
__device__ __forceinline__ float2 samp(const float2* __restrict__ F, const int GW, const int GH, float cx, float cy) {
    cx -= 0.5f; cy -= 0.5f;
    const float fx = floorf(cx), fy = floorf(cy);
    const int x0 = (int)fx, y0 = (int)fy;
    const float tx = cx - fx, ty = cy - fy;
    const int xa = min(max(x0, 0), GW - 1), xb = min(max(x0 + 1, 0), GW - 1);
    const int ya = min(max(y0, 0), GH - 1), yb = min(max(y0 + 1, 0), GH - 1);
    const float2 a = F[ya * GW + xa], b = F[ya * GW + xb], c = F[yb * GW + xa], d = F[yb * GW + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float2(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y));
}
__device__ __forceinline__ float gsamp(const float* __restrict__ G, const int gw, const int gh, float cx, float cy) {
    cx -= 0.5f; cy -= 0.5f;
    const float fx = floorf(cx), fy = floorf(cy);
    const int x0 = (int)fx, y0 = (int)fy;
    const float tx = cx - fx, ty = cy - fy;
    const int xa = min(max(x0, 0), gw - 1), xb = min(max(x0 + 1, 0), gw - 1);
    const int ya = min(max(y0, 0), gh - 1), yb = min(max(y0 + 1, 0), gh - 1);
    const float u = 1.f - tx, v = 1.f - ty;
    return v * (u * G[ya * gw + xa] + tx * G[ya * gw + xb]) + ty * (u * G[yb * gw + xa] + tx * G[yb * gw + xb]);
}
// palette hue kept bright: dark phases fold to 2a - pal(t), then normalised so the max channel is 1
__device__ __forceinline__ float3 hue_pal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.05f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
// black-body style ramp: a channel strong in the hue lights first and saturates early, weak ones come in late
__device__ __forceinline__ float ramp(float h, float c) {
    const float w = 1.f - clamp01(c);
    const float t0 = 0.62f * w, t1 = 0.5f + 0.55f * w;
    const float x = clamp01((h - t0) / (t1 - t0));
    return x * sqrtf(x);
}
__device__ __forceinline__ float3 firecol(float h, const float* __restrict__ P) {
    const float3 C = hue_pal(P[13] + h * P[12], P);
    return make_float3(ramp(h, C.x), ramp(h, C.y), ramp(h, C.z));
}
__device__ __forceinline__ float3 screen3(float3 a, float3 b) {
    b.x = clamp01(b.x); b.y = clamp01(b.y); b.z = clamp01(b.z);
    return make_float3(a.x + b.x - a.x * b.x, a.y + b.y - a.y * b.y, a.z + b.z - a.z * b.z);
}

extern "C" __global__
void fire_step(const float2* __restrict__ A, float2* __restrict__ Bo, const int GW, const int GH,
               const float* __restrict__ P, const unsigned int step, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GW || y >= GH) return;
    const unsigned int idx = (unsigned int)(y * GW + x);
    const float yo = (float)(step & 0xFFFFFu);
    const float fy = (float)y + yo;                       // map coordinates ride up with the flames
    const float hfrac = 1.f - ((float)y + 0.5f) / (float)GH;

    // ---- heat: rise one row with DOOM column jitter, sheared wind and turbulence, then cool
    const float jit = floorf(hash1(idx, step, 1u) * 3.f) - 1.f;
    const float wind = P[16] * (0.3f + 0.7f * hfrac);
    const float tn = vnoise((float)x * P[18] + P[19], fy * P[18], seed + 101u) - 0.5f;
    const float2 up = rowlerp(A, (float)x + jit - wind + P[17] * 2.f * tn, y + 1, GW, GH);
    const float cn = 0.65f * vnoise((float)x * P[20], fy * P[20], seed + 202u)
                   + 0.35f * vnoise((float)x * P[20] * 2.3f + 17.f, fy * P[20] * 2.3f, seed + 303u);
    const float cm = 0.4f + 1.2f * cn;
    float h = fmaxf(up.x - P[15] * cm * 2.f * hash1(idx, step, 2u), 0.f);

    // ---- emitters (max-injected): bottom line, ring, drifting embers
    float E = 0.f;
    if (P[23] > 0.f && y >= GH - 2) {
        const float m = 0.55f + 0.75f * vnoise((float)x * P[21] + P[22], 3.1f + 0.37f * P[22], seed + 404u);
        E = P[23] * P[14] * fminf(m, 1.2f);
    }
    if (P[24] > 0.f) {
        const float dx = (float)x + 0.5f - P[26], dy = (float)y + 0.5f - P[27];
        const float r = sqrtf(dx * dx + dy * dy) + 1e-4f;
        const float band = 1.f - smooth(0.4f * P[29], P[29], fabsf(r - P[28]));
        if (band > 0.f) {
            const float m = 0.55f + 0.75f * vnoise(2.2f * dx / r + P[22], 2.2f * dy / r + 0.37f * P[22], seed + 505u);
            E = fmaxf(E, P[24] * P[14] * band * fminf(m, 1.2f));
        }
    }
    if (P[25] > 0.f) {
        for (int i = 0; i < NEMB; ++i) {
            const float* e = P + 40 + 4 * i;
            const float dx = (float)x + 0.5f - e[0], dy = (float)y + 0.5f - e[1];
            const float q = 1.f - (dx * dx + dy * dy) / (e[2] * e[2] + 1e-4f);
            if (q > 0.f) E = fmaxf(E, P[25] * e[3] * sqrtf(q));
        }
    }
    h = fmaxf(h, E);

    // ---- sparks: two rows per step, a coherent sideways swirl, a short tail, random burn-out
    const float sw = vnoise((float)x * P[39] + 1.7f * P[19], ((float)y + 2.f * yo) * P[39], seed + 606u) - 0.5f;
    const int so = (int)rintf(sw * 3.2f - 1.5f * wind);
    const float s2 = ld2(A, x + so, y + 2, GW, GH).y, s1 = ld2(A, x + so, y + 1, GW, GH).y;
    float s = fmaxf(fmaxf(s2, 0.55f * s1) * P[30], 0.3f * A[idx].y);
    if (hash1(idx, step, 3u) < P[31]) s = 0.f;
    if (h > 0.3f && h < 0.75f && hash1(idx, step, 4u) < P[32]) s = fmaxf(s, 0.7f + 0.3f * hash1(idx, step, 5u));
    if (s < 0.01f) s = 0.f;
    Bo[idx] = make_float2(h, s);
}

extern "C" __global__
void fire_glow(const float2* __restrict__ F, float* __restrict__ G, const int GW, const int GH, const int gw,
               const int gh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    float s = 0.f;
    #pragma unroll
    for (int b = 0; b < 4; ++b) {
        const int yy = min(4 * j + b, GH - 1);
        #pragma unroll
        for (int a = 0; a < 4; ++a) s += fminf(F[yy * GW + min(4 * i + a, GW - 1)].x, 1.2f);
    }
    G[j * gw + i] = s * (1.f / 16.f);
}

extern "C" __global__
void fire_draw(uchar4* __restrict__ out, const float2* __restrict__ F, const float* __restrict__ G, const int W,
               const int H, const int GW, const int GH, const int gw, const int gh, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float gx = (x + 0.5f) * (float)GW / (float)W, gy = (y + 0.5f) * (float)GH / (float)H;
    // smooth: tent-filtered bilinear
    const float2 a0 = samp(F, GW, GH, gx - 0.5f, gy - 0.5f), a1 = samp(F, GW, GH, gx + 0.5f, gy - 0.5f);
    const float2 a2 = samp(F, GW, GH, gx - 0.5f, gy + 0.5f), a3 = samp(F, GW, GH, gx + 0.5f, gy + 0.5f);
    float hv = 0.25f * (a0.x + a1.x + a2.x + a3.x), sv = 0.25f * (a0.y + a1.y + a2.y + a3.y);
    const float wc = P[34];
    if (wc > 0.f) {                                        // chunky: block-averaged cells, anchored bottom centre
        const float B = P[33], q = 0.25f * B;
        const float bx = 0.5f * GW + (floorf((gx - 0.5f * GW) / B) + 0.5f) * B;
        const float by = (float)GH - (floorf(((float)GH - gy) / B) + 0.5f) * B;
        const float2 b0 = samp(F, GW, GH, bx - q, by - q), b1 = samp(F, GW, GH, bx + q, by - q);
        const float2 b2 = samp(F, GW, GH, bx - q, by + q), b3 = samp(F, GW, GH, bx + q, by + q);
        const float hb = 0.25f * (b0.x + b1.x + b2.x + b3.x);
        const float sb = 0.25f * (b0.y + b1.y + b2.y + b3.y) * (1.f + 0.6f * (B - 1.f));
        hv += (hb - hv) * wc;
        sv += (sb - sv) * wc;
    }
    if (P[35] > 0.f) {                                     // indexed-palette banding
        const float hq = floorf(hv * 12.f + 0.5f) * (1.f / 12.f);
        hv += (hq - hv) * P[35];
    }
    float3 c = firecol(hv, P);
    // glow halo from the 1/4-res heat
    const float hg = gsamp(G, gw, gh, gx * 0.25f, gy * 0.25f);
    const float3 gc = firecol(fminf(hg, 1.f) * 0.85f, P);
    c = screen3(c, make_float3(gc.x * P[37], gc.y * P[37], gc.z * P[37]));
    // sparks
    const float s = fminf(sv * P[38], 1.5f);
    if (s > 0.003f) {
        const float3 sc = firecol(0.78f + 0.3f * fminf(s, 1.f), P);
        c = screen3(c, make_float3(sc.x * s, sc.y * s, sc.z * s));
    }
    const float g = 255.f * P[36];
    out[y * W + x] = pack(make_float4(c.x * g, c.y * g, c.z * g, 255.f));
}
""" % {"nemb": NEMB}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _clamp01(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def _ss(e0: float, e1: float, x: float) -> float:
    t = _clamp01((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class DemoFire(Source):
    name = "demo_fire"
    label = "Demo fire"
    description = ("Classic chunky demoscene fire: a heat field rising from a line, a ring or drifting embers, "
                   "flickering with sideways jitter and sparks through a black-body palette.")
    tags = {"family": "fire", "dims": "2d", "toolkit": "none", "technique": ["simulation", "retro"],
            "moods": ["fiery", "retro", "aggressive"], "style": "wild", "energy": [0.3, 1.0]}
    params = _order({
        "cooling": num(0.0, 1.0, 0.4, "Cooling (tall -> short flames)", tier="regional", basic=True),
        "emitter_shape": num(0.0, 2.0, 0.0, "Emitter (line -> ring -> embers)", tier="regional"),
        "chunkiness": num(0.0, 1.0, 0.55, "Chunkiness (smooth -> pixel-art)", tier="regional"),
        "wind_shear": num(-1.0, 1.0, 0.15, "Wind shear", tier="regional"),
        "turbulence": num(0.0, 1.0, 0.4, "Turbulence", tier="regional"),
        "spark_density": num(0.0, 1.0, 0.4, "Sparks", tier="regional"),
        **standard_scene_params(palette="ember", render_scale=0.5, period=0.35, drift=0.02),
    }, ["cooling", "emitter_shape", "chunkiness", "wind_shear", "turbulence", "spark_density",
        "energy", "pulse", "palette", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw, self.gh = max(64, self.w // 4), max(36, self.h // 4)
        self.gw4, self.gh4 = max(2, self.gw // 4), max(2, self.gh // 4)
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.F = [cp.zeros((self.gh, self.gw, 2), cp.float32) for _ in range(2)]
        self.G = cp.zeros((self.gh4, self.gw4), cp.float32)
        self.cur = 0
        mod = raw_module(_SRC, _OPTIONS)
        self.k_step = mod.get_function("fire_step")
        self.k_glow = mod.get_function("fire_glow")
        self.k_draw = mod.get_function("fire_draw")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.seed_u = int(rng.integers(1, 2**30))
        # embers: per-seed drift frequencies, phases, sizes and flicker
        self.e_fx = rng.uniform(0.13, 0.32, NEMB)
        self.e_fy = rng.uniform(0.10, 0.26, NEMB)
        self.e_px = rng.uniform(0, 2 * math.pi, NEMB)
        self.e_py = rng.uniform(0, 2 * math.pi, NEMB)
        self.e_sz = rng.uniform(0.0, 1.0, NEMB)
        self.e_fl = rng.uniform(1.5, 3.5, NEMB)
        self.e_yb = rng.uniform(0.42, 0.74, NEMB)
        self.drift = 0.0
        self.tx = float(rng.uniform(0, 64))
        self.et = float(rng.uniform(0, 64))
        self.emb_t = 0.0
        self.gust = float(rng.uniform(0, 2 * math.pi))
        self.acc = 0.0
        self.step = 0
        # burn in so the scene arrives already ablaze
        self._update(params, 0.0)
        self._advance(int(self.gh * 1.2))

    # ------------------------------------------------------------------ host maths
    def _update(self, p: dict, dt: float) -> int:
        dt = min(max(dt, 0.0), 0.1)
        e = _clamp01(float(p["energy"]))
        k = _clamp01(float(p["pulse"]))
        spd = max(float(p["speed"]), 0.0)
        gw, gh = self.gw, self.gh

        # host-integrated phases (kicks add to rates only)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        self.tx = _wrap(self.tx + 0.22 * (0.5 + e) * spd * dt, 4096.0)
        self.et = _wrap(self.et + 0.3 * (0.4 + e) * spd * dt, 4096.0)
        self.emb_t += (0.12 + 0.3 * e) * spd * dt
        self.gust = _wrap(self.gust + 0.35 * spd * dt, 2000 * math.pi)

        # simulation steps via an accumulator (rise speed x speed x bellows)
        rate = gh * (0.25 + 0.45 * e) * spd * (1.0 + 0.9 * k)
        self.acc += rate * dt
        n = int(self.acc)
        self.acc -= n
        n = min(n, MAX_SUB)

        P = self.Ph
        P[0:12] = palette_coeffs(p["palette"])
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = 0.9 + 0.3 * e + 0.45 * k
        flame = (0.95 - 0.7 * _clamp01(float(p["cooling"]))) * (0.62 + 0.62 * e) * (1.0 + 0.3 * k)
        P[15] = 1.0 / (max(flame, 0.05) * gh)
        gust = (0.08 + 0.17 * e) * (0.6 * math.sin(self.gust) + 0.4 * math.sin(2.3 * self.gust + 1.0))
        P[16] = 0.55 * float(p["wind_shear"]) + gust
        P[17] = float(p["turbulence"]) * (0.3 + 0.9 * e) * 1.3
        P[18] = 9.0 / gh
        P[19] = self.tx
        P[20] = 11.0 / gh
        P[21] = 6.0 / gh
        P[22] = self.et
        s = min(max(float(p["emitter_shape"]), 0.0), 2.0)
        P[23] = _clamp01(1.6 * (1.0 - s))
        P[24] = _clamp01(1.6 * (1.0 - abs(s - 1.0)))
        P[25] = _clamp01(1.6 * (s - 1.0))
        P[26], P[27] = 0.5 * gw, 0.58 * gh
        P[28] = 0.27 * gh
        P[29] = max(1.5, gh / 110.0)
        P[30] = 0.975
        P[31] = 0.006
        P[32] = (float(p["spark_density"]) * 1.2e-4 * (0.25 + 1.2 * e ** 1.3) * (1.0 + 1.5 * k)
                 * (480.0 * 270.0) / (gw * gh))
        ch = _clamp01(float(p["chunkiness"]))
        P[33] = 1.0 + 2.2 * ch
        P[34] = _ss(0.0, 0.3, ch)
        P[35] = 0.85 * _ss(0.3, 1.0, ch)
        P[36] = float(p["intensity"]) * (1.0 + 0.12 * k)
        P[37] = 0.3 + 0.25 * e
        P[38] = 1.3
        P[39] = 22.0 / gh
        # embers: slow Lissajous drift, flickering heat
        T = self.emb_t
        heat = P[14]
        for i in range(NEMB):
            ex = gw * (0.08 + 0.84 * (i + 0.5) / NEMB) + gw * 0.07 * math.sin(self.e_fx[i] * T + self.e_px[i])
            ey = gh * (self.e_yb[i] + 0.16 * math.sin(self.e_fy[i] * T + self.e_py[i]))
            er = gh * (0.022 + 0.02 * self.e_sz[i])
            eh = heat * (0.85 + 0.15 * math.sin(self.e_fl[i] * T * 3.0 + self.e_px[i]))
            P[40 + 4 * i: 44 + 4 * i] = (ex, ey, er, eh)
        self.P.set(P)
        return n

    def _advance(self, n: int) -> None:
        for _ in range(n):
            a, b = self.F[self.cur], self.F[1 - self.cur]
            launch_2d(self.k_step, self.gw, self.gh, (a, b, np.int32(self.gw), np.int32(self.gh), self.P,
                                                      np.uint32(self.step & 0x3FFFFFFF), np.uint32(self.seed_u)))
            self.cur ^= 1
            self.step += 1

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._advance(self._update(p, dt))
        F = self.F[self.cur]
        launch_2d(self.k_glow, self.gw4, self.gh4, (F, self.G, np.int32(self.gw), np.int32(self.gh),
                                                   np.int32(self.gw4), np.int32(self.gh4)))
        launch_2d(self.k_draw, self.w, self.h, (self.buf, F, self.G, np.int32(self.w), np.int32(self.h),
                                                np.int32(self.gw), np.int32(self.gh), np.int32(self.gw4),
                                                np.int32(self.gh4), self.P))
        present(self.ctx, self.buf, out)
