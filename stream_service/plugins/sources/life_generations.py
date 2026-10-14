"""Life Generations: coloured cellular automata with history (Life, Brian's Brain, Star Wars and other Generations
rules) on a torus, with glowing decay trails through the palette and continual seeding that keeps it alive.

Source: Original (inspired by https://en.wikipedia.org/wiki/Brian%27s_Brain and https://en.wikipedia.org/wiki/Life-like_cellular_automaton)
Author: smfx (original; the rules are mathematics; xscreensaver cloudlife is a permissive reference only)
License: MIT (original)
license_class: permissive

Written from scratch from the rule definitions (nothing copied).

Model: a Generations automaton on a torus of round(3840 / cell_size) x round(2160 / cell_size) cells (960 x 540 at the
default), resolution independent of the output.  Each cell holds (state, age) as uchar2: 0 dead, 1 alive,
2..C-1 dying (refractory: a dying cell neither counts as a neighbour nor can be born).  Rule = birth / survival
neighbour counts + C (C = 2 is a plain Life-like rule).  Ping-pong uint8 state, plus a float2 trail buffer
(intensity, hue) stamped by every live cell and decayed in real time (not per step), so trails are frame-rate and
step-rate independent.

Seeding (all through a dihedral 'fold' of the cell's centred coordinates, so with seed_symmetry the seeds are
exact mirror / quad images and the automaton grows mandalas; 3+ are approximate polar kaleido folds):
  * three seed spots wander on slow Lissajous paths, gated by smooth integrated-time envelopes, and inject soup;
  * a sparse sprinkle perturbs still lifes and oscillators back into activity;
  * a pulse rising edge (host detected, 120 ms refractory) throws a disc burst of live cells at a drifting point.
The random numbers are hashes of (folded cell, step index), the step index being an integrated counter, so nothing
is keyed to the frame count.

Look: per cell an emissive colour (shade pass): live cells = bright pal(age hue x period + drift + spatial hue
field), white-hot when newborn and dimmer as they age (still lifes recede); dying states fade and walk along the
palette; trails fade and walk further along it.  Then a 3x3 tent copy (soft 'cloud' look), two glow levels
(1/4 and 1/16 grid, separable gaussian, torus wrap), and a full-res draw that mixes soft clouds with crisp
rounded LED cells (cell_look), adds the glow, and soft-clips.

Macros: energy -> steps per second (integrated accumulator, a frame skip runs the right number of steps, <= 8 per
frame), seed spot radius and fill rate, sprinkle rate, glow gain and burst size.  pulse -> disc burst on a rising
edge + trail / glow flash (multiplies gains only).  speed scales every rate (0 freezes the automaton; trails still
fade).  palette index = cell age x period + drift.  intensity is output gain.
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

# name: (birth counts, survival counts, C states, initial fill, burst fill, seeding gain)
RULES = {
    "life": ("3", "23", 2, 0.30, 0.42, 1.0),
    "highlife": ("36", "23", 2, 0.30, 0.42, 1.0),
    "day_night": ("3678", "34678", 2, 0.50, 0.55, 1.6),
    "brain": ("2", "", 3, 0.10, 0.24, 0.5),
    "star_wars": ("2", "345", 4, 0.16, 0.30, 0.6),
    "frogs": ("34", "12", 3, 0.20, 0.35, 0.8),
    "spirals": ("234", "2", 5, 0.06, 0.18, 0.35),
    "belzhab": ("23", "23", 8, 0.12, 0.26, 0.5),
    "fireworks": ("13", "2", 21, 0.004, 0.03, 0.08),
}
RULE_NAMES = list(RULES)
MAX_STEPS = 8          # per frame (a long skip drops the excess rather than stalling)
NSPOT = 3

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NSPOT %(nspot)d
// dihedral fold of centred (doubled-unit) coordinates: 0 none, 1 mirror x, 2 mirror x+y, 4 square D4, else polar n-fold
__device__ __forceinline__ void fold2(float& x, float& y, const int n) {
    if (n <= 0) return;
    if (n == 1) { x = fabsf(x); return; }
    if (n == 2) { x = fabsf(x); y = fabsf(y); return; }
    if (n == 4) { const float ax = fabsf(x), ay = fabsf(y); x = fmaxf(ax, ay); y = fminf(ax, ay); return; }
    const float r = sqrtf(x * x + y * y), sec = 6.2831853f / (float)n;
    float a = atan2f(y, x);
    a -= sec * floorf(a / sec);
    if (a > 0.5f * sec) a = sec - a;
    x = r * cosf(a); y = r * sinf(a);
}
__device__ __forceinline__ unsigned int cell_key(float fx, float fy) {
    const int hx = (int)rintf(fx) + 8192, hy = (int)rintf(fy) + 8192;
    return (unsigned int)(hx & 16383) | ((unsigned int)(hy & 16383) << 14);
}
__device__ __forceinline__ float age_hue(int age) { return 0.62f * (1.f - __expf(-(float)age * (1.f / 14.f))); }
// palette colour kept bright for emissive cells: a dark phase folds towards the opposite side of the palette,
// then the max channel is lifted to >= ~0.65
__device__ __forceinline__ float3 bright_pal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.65f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
__device__ __forceinline__ int wrapi(int i, int n) { i %%= n; return i < 0 ? i + n : i; }
__device__ __forceinline__ float4 bilin_wrap(const float4* __restrict__ F, int w, int h, float fx, float fy) {
    const float x0f = floorf(fx), y0f = floorf(fy);
    const float tx = fx - x0f, ty = fy - y0f;
    const int x0 = wrapi((int)x0f, w), y0 = wrapi((int)y0f, h);
    const int x1 = x0 + 1 == w ? 0 : x0 + 1, y1 = y0 + 1 == h ? 0 : y0 + 1;
    const float4 a = F[y0 * w + x0], b = F[y0 * w + x1], c = F[y1 * w + x0], d = F[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}

// one Generations step A -> B, with seeding of dead cells and trail stamping of live ones
extern "C" __global__
void life_step(const uchar2* __restrict__ A, uchar2* __restrict__ B, float2* __restrict__ T, const int gw,
               const int gh, const unsigned int bmask, const unsigned int smask, const int C, const int symn,
               const unsigned int step, const unsigned int salt, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    const int im = i == 0 ? gw - 1 : i - 1, ip = i == gw - 1 ? 0 : i + 1;
    const int jm = (j == 0 ? gh - 1 : j - 1) * gw, jp = (j == gh - 1 ? 0 : j + 1) * gw, jc = j * gw;
    const int n = (A[jm + im].x == 1) + (A[jm + i].x == 1) + (A[jm + ip].x == 1) + (A[jc + im].x == 1)
                + (A[jc + ip].x == 1) + (A[jp + im].x == 1) + (A[jp + i].x == 1) + (A[jp + ip].x == 1);
    const uchar2 c = A[jc + i];
    int s = c.x, age = c.y, ns, nage;
    if (s >= C) s = 0;                                         // a rule change shortened the dying chain
    if (s == 0) {
        if ((bmask >> n) & 1u) { ns = 1; nage = 0; } else { ns = 0; nage = 0; }
    } else if (s == 1) {
        ns = ((smask >> n) & 1u) ? 1 : (C > 2 ? 2 : 0);
        nage = min(age + 1, 255);
    } else {
        ns = s + 1; if (ns >= C) ns = 0;
        nage = min(age + 1, 255);
    }
    if (ns == 0) {                                             // seeding: sprinkle + wandering spots (folded)
        float fx = (float)(2 * i - (gw - 1)), fy = (float)(2 * j - (gh - 1));
        fold2(fx, fy, symn);
        float p = P[26];
        #pragma unroll
        for (int k = 0; k < NSPOT; ++k) {
            const float dx = fx - P[28 + 4 * k], dy = fy - P[29 + 4 * k];
            const float w = 1.f - (dx * dx + dy * dy) * P[30 + 4 * k];
            if (w > 0.f) p += P[31 + 4 * k] * w * w;
        }
        if (p > 0.f && hash1(cell_key(fx, fy), step, salt) < p) { ns = 1; nage = 0; }
    }
    B[jc + i] = make_uchar2((unsigned char)ns, (unsigned char)nage);
    if (ns == 1) T[jc + i] = make_float2(1.f, age_hue(nage));
}

// disc burst (pulse) and the initial fill: in folded coordinates, so symmetric seeds burst symmetrically
extern "C" __global__
void life_burst(uchar2* __restrict__ S, float2* __restrict__ T, const int gw, const int gh, const int symn,
                const float cx, const float cy, const float r, const float prob, const unsigned int key,
                const unsigned int salt)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    float fx = (float)(2 * i - (gw - 1)), fy = (float)(2 * j - (gh - 1));
    fold2(fx, fy, symn);
    const float dx = fx - cx, dy = fy - cy;
    const float q = (dx * dx + dy * dy) / (r * r);
    if (q >= 1.f) return;
    const float p = prob * (0.55f + 0.45f * (1.f - q));
    if (hash1(cell_key(fx, fy), key, salt ^ 0x5bd1e995u) < p) {
        S[j * gw + i] = make_uchar2(1, 0);
        T[j * gw + i] = make_float2(1.f, 0.f);
    }
}

// per-cell emissive colour; decays the trails by this frame's factor
// P: 0..11 palette | 12 period | 13 drift | 14 hue spread | 15 hue field phase | 16 trail decay | 17 trail gain
//    18 flash
extern "C" __global__
void life_shade(const uchar2* __restrict__ S, float2* __restrict__ T, float4* __restrict__ E, const int gw,
                const int gh, const int C, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    const int idx = j * gw + i;
    const float* PAL = P;
    const float per = P[12];
    float2 tr = T[idx];
    tr.x *= P[16];
    T[idx] = tr;
    const float hf = P[14] * 2.f * (vnoise((float)i / gw * 3.f + P[15], (float)j / gh * 2.f - 0.6f * P[15], 41u)
                                    + 0.5f * vnoise((float)i / gw * 7.f - 0.7f * P[15], (float)j / gh * 5.f, 43u)
                                    - 0.75f);
    const float base = P[13] + hf;
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (tr.x > 0.002f) {
        const float3 tc = bright_pal(base + per * (tr.y + 0.4f * (1.f - tr.x)), PAL);
        const float w = P[17] * P[18] * powf(tr.x, 1.6f);
        c = make_float3(tc.x * w, tc.y * w, tc.z * w);
    }
    const uchar2 st = S[idx];
    const int s = st.x, age = st.y;
    if (s == 1) {
        const float3 ac = bright_pal(base + per * age_hue(age), PAL);
        const float bri = 0.6f + 0.4f * __expf(-(float)age * (1.f / 20.f));
        const float wh = 0.45f * __expf(-0.6f * (float)age);
        c.x = c.x * 0.25f + bri * (ac.x + wh);
        c.y = c.y * 0.25f + bri * (ac.y + wh);
        c.z = c.z * 0.25f + bri * (ac.z + wh);
    } else if (s >= 2 && s < C) {
        const float k = (float)(s - 1) / (float)(C - 1);
        const float3 dc = bright_pal(base + per * (age_hue(age) + 0.3f * k), PAL);
        const float bri = 0.85f * powf(1.f - k, 1.2f);
        c.x = c.x * (1.f - bri) + bri * dc.x;
        c.y = c.y * (1.f - bri) + bri * dc.y;
        c.z = c.z * (1.f - bri) + bri * dc.z;
    }
    E[idx] = make_float4(c.x, c.y, c.z, 0.f);
}

// 3x3 tent (the soft 'cloud' look)
extern "C" __global__
void life_soften(const float4* __restrict__ E, float4* __restrict__ O, const int gw, const int gh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    float4 a = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int b = -1; b <= 1; ++b) {
        const int row = wrapi(j + b, gh) * gw;
        #pragma unroll
        for (int q = -1; q <= 1; ++q) {
            const float w = (b == 0 ? 2.f : 1.f) * (q == 0 ? 2.f : 1.f) * (1.f / 16.f);
            const float4 e = E[row + wrapi(i + q, gw)];
            a.x += w * e.x; a.y += w * e.y; a.z += w * e.z;
        }
    }
    O[j * gw + i] = a;
}

// 4x4 box downsample (torus wrap)
extern "C" __global__
void life_down(const float4* __restrict__ S, const int sw, const int sh, float4* __restrict__ D, const int dw,
               const int dh)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= dw || j >= dh) return;
    float4 a = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int b = 0; b < 4; ++b) {
        const int row = ((4 * j + b) %% sh) * sw;
        #pragma unroll
        for (int q = 0; q < 4; ++q) {
            const float4 e = S[row + (4 * i + q) %% sw];
            a.x += e.x; a.y += e.y; a.z += e.z;
        }
    }
    D[j * dw + i] = make_float4(a.x * 0.0625f, a.y * 0.0625f, a.z * 0.0625f, 0.f);
}

// separable 9-tap gaussian (sigma 2), torus wrap
extern "C" __global__
void life_blur(const float4* __restrict__ S, float4* __restrict__ D, const int w, const int h, const int dx,
               const int dy)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= w || j >= h) return;
    const float wt[5] = {0.2042f, 0.1802f, 0.1238f, 0.0663f, 0.0276f};
    float4 a = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int k = -4; k <= 4; ++k) {
        const float4 e = S[wrapi(j + k * dy, h) * w + wrapi(i + k * dx, w)];
        const float g = wt[k < 0 ? -k : k];
        a.x += g * e.x; a.y += g * e.y; a.z += g * e.z;
    }
    D[j * w + i] = a;
}

// final draw at internal res: soft clouds <-> crisp LED cells, plus two glow levels, soft clip
// P: 19 glow1 | 20 glow2 | 21 crisp | 22 cell gap | 23 aa (cells / px) | 24 intensity | 25 mask amount
extern "C" __global__
void life_draw(uchar4* __restrict__ out, const float4* __restrict__ E, const float4* __restrict__ Es, const int gw,
               const int gh, const float4* __restrict__ G1, const int w1, const int h1, const float4* __restrict__ G2,
               const int w2, const int h2, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float gx = (x + 0.5f) * gw / W - 0.5f, gy = (y + 0.5f) * gh / H - 0.5f;
    const float crisp = P[21];
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (crisp < 0.999f) {
        const float4 s = bilin_wrap(Es, gw, gh, gx, gy);
        const float k = 1.f - crisp;
        c.x += k * s.x; c.y += k * s.y; c.z += k * s.z;
    }
    if (crisp > 0.001f) {
        const float cxf = floorf(gx + 0.5f), cyf = floorf(gy + 0.5f);
        const int ci = wrapi((int)cxf, gw), cj = wrapi((int)cyf, gh);
        const float4 e = E[cj * gw + ci];
        const float qx = fabsf(gx + 0.5f - cxf - 0.5f), qy = fabsf(gy + 0.5f - cyf - 0.5f);
        const float half = 0.5f - P[22], rr = 0.4f * half;
        const float ox = fmaxf(qx - (half - rr), 0.f), oy = fmaxf(qy - (half - rr), 0.f);
        const float d = sqrtf(ox * ox + oy * oy) - rr;
        const float m = 1.f - P[25] * smooth(-P[23], P[23], d);
        const float k = crisp * m * (1.f + 0.35f * P[25]);     // crisp dots carry a little extra punch
        c.x += k * e.x; c.y += k * e.y; c.z += k * e.z;
    }
    const float g1x = (gx - 1.5f) * 0.25f, g1y = (gy - 1.5f) * 0.25f;
    const float4 a = bilin_wrap(G1, w1, h1, g1x, g1y);
    const float4 b = bilin_wrap(G2, w2, h2, (g1x - 1.5f) * 0.25f, (g1y - 1.5f) * 0.25f);
    c.x += P[19] * a.x + P[20] * b.x;
    c.y += P[19] * a.y + P[20] * b.y;
    c.z += P[19] * a.z + P[20] * b.z;
    // ambient: a whisper of the palette so empty space is never dead black
    const float3 amb = pal(P[13] + 0.5f, P);
    const float u = (x + 0.5f) / W - 0.5f, v = (y + 0.5f) / H - 0.5f;
    const float vg = 1.f - 0.55f * (u * u + v * v);
    c.x = (c.x + 0.018f * amb.x) * vg * P[24];
    c.y = (c.y + 0.018f * amb.y) * vg * P[24];
    c.z = (c.z + 0.018f * amb.z) * vg * P[24];
    // soft clip above 0.75 (C1 continuous)
    c.x = c.x < 0.75f ? c.x : 0.75f + 0.25f * (1.f - __expf(-(c.x - 0.75f) * 4.f));
    c.y = c.y < 0.75f ? c.y : 0.75f + 0.25f * (1.f - __expf(-(c.y - 0.75f) * 4.f));
    c.z = c.z < 0.75f ? c.z : 0.75f + 0.25f * (1.f - __expf(-(c.z - 0.75f) * 4.f));
    out[y * W + x] = pack(make_float4(c.x * 255.f, c.y * 255.f, c.z * 255.f, 255.f));
}
""" % {"nspot": NSPOT}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _fold(x: float, y: float, n: int) -> tuple[float, float]:
    """Host twin of the device fold2 (centred doubled-unit coordinates)."""
    if n <= 0:
        return x, y
    if n == 1:
        return abs(x), y
    if n == 2:
        return abs(x), abs(y)
    if n == 4:
        ax, ay = abs(x), abs(y)
        return max(ax, ay), min(ax, ay)
    r, sec = math.hypot(x, y), 2 * math.pi / n
    a = math.atan2(y, x)
    a -= sec * math.floor(a / sec)
    if a > 0.5 * sec:
        a = sec - a
    return r * math.cos(a), r * math.sin(a)


def _mask(digits: str) -> int:
    return sum(1 << int(d) for d in set(digits))


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


_cell_size = num(3, 12, 4, "Cell size (4K px)", tier="structural", integer=True)
_cell_size["x-ease"] = False


class LifeGenerations(Source):
    name = "life_generations"
    label = "Life Generations"
    description = ("Coloured cellular automata with history: Life, Brian's Brain, Star Wars and other Generations "
                   "rules with glowing decay trails, kept alive by continual and beat-thrown seeding.")
    params = _order({
        "trail_decay": num(0.0, 1.0, 0.55, "Trails (short -> long)", tier="regional", basic=True),
        "seed_density": num(0.0, 1.0, 0.5, "Seeding", tier="regional"),
        "seed_symmetry": num(0, 8, 0, "Seed symmetry (0 none, 1 mirror, 2 quad, 3-8 kaleido)", tier="regional",
                             integer=True, basic=True),
        "glow": num(0.0, 1.5, 0.6, "Glow", tier="regional"),
        "cell_look": num(0.0, 1.0, 0.65, "Cells (soft clouds -> crisp LEDs)", tier="regional"),
        "colour_spread": num(0.0, 1.0, 0.3, "Colour spread across the field", tier="regional"),
        "rule": enum(RULE_NAMES, "brain", "Rule"),
        "cell_size": _cell_size,
        **standard_scene_params(palette="neon", render_scale=1.0, period=1.0, drift=0.03),
    }, ["trail_decay", "seed_density", "seed_symmetry", "glow", "cell_look", "colour_spread",
        "energy", "pulse", "speed", "intensity", "palette_period", "drift_speed"])

    # ------------------------------------------------------------------ setup / buffers
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_step = mod.get_function("life_step")
        self.k_burst = mod.get_function("life_burst")
        self.k_shade = mod.get_function("life_shade")
        self.k_soft = mod.get_function("life_soften")
        self.k_down = mod.get_function("life_down")
        self.k_blur = mod.get_function("life_blur")
        self.k_draw = mod.get_function("life_draw")
        self.Ph = np.zeros(64, np.float32)
        self.P = cp.zeros(64, cp.float32)
        self.salt = np.uint32((int(params["seed"]) * 2654435761 + 12345) & 0xFFFFFFFF)
        rng = np.random.default_rng(int(params["seed"]))
        self.sph = [float(v) for v in rng.uniform(0, 2 * math.pi, 2 * NSPOT)]      # spot path phases
        self.gph = [math.pi / 2, float(rng.uniform(0, 2 * math.pi)), float(rng.uniform(0, 2 * math.pi))]
        self.bph = float(rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.hph = float(rng.uniform(0, 50))
        self.acc = 0.0
        self.nstep = 0
        self.nburst = 0
        self.prev_pulse = 0.0
        self.t = 0.0
        self.last_burst = -1.0
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))
        self._alloc(int(params["cell_size"]), params)

    def _alloc(self, cs: int, p: dict) -> None:
        cp = self.ctx.cp
        self.cs = cs
        self.gw, self.gh = max(64, int(round(3840 / cs))), max(36, int(round(2160 / cs)))
        self.w1, self.h1 = -(-self.gw // 4), -(-self.gh // 4)
        self.w2, self.h2 = -(-self.w1 // 4), -(-self.h1 // 4)
        self.S = [cp.zeros((self.gh, self.gw, 2), cp.uint8) for _ in range(2)]
        self.cur = 0
        self.T = cp.zeros((self.gh, self.gw, 2), cp.float32)
        self.E = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.Es = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.G1 = [cp.zeros((self.h1, self.w1, 4), cp.float32) for _ in range(2)]
        self.G2 = [cp.zeros((self.h2, self.w2, 4), cp.float32) for _ in range(2)]
        # initial fill (folded, so symmetric seeds start symmetric), then a short pre-run so it arrives formed
        rule = RULES[p["rule"]]
        symn = int(p["seed_symmetry"])
        fill = rule[3] * (0.6 + 0.8 * float(p["seed_density"]))
        launch_2d(self.k_burst, self.gw, self.gh, (self.S[0], self.T, np.int32(self.gw), np.int32(self.gh),
                                                   np.int32(symn), np.float32(0.0), np.float32(0.0),
                                                   np.float32(1e7), np.float32(fill), np.uint32(0x9E37),
                                                   self.salt))
        dt = 1.0 / 30.0
        for _ in range(24):
            self._host(p, dt, prime=True)
            self._steps(p, 1)
            self._shade_launch()

    def close(self) -> None:
        self.S = self.T = self.E = self.Es = self.G1 = self.G2 = None
        self.buf = None
        self.P = None

    # ------------------------------------------------------------------ host maths
    def _host(self, p: dict, dt: float, prime: bool = False) -> float:
        """Integrate phases and fill P; returns the step rate (steps / s)."""
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        k = pulse * (0.35 + 0.65 * e)
        rule = RULES[p["rule"]]
        symn = int(p["seed_symmetry"])
        sd = float(p["seed_density"])

        # palette (session palette wins; eased so a change never snaps)
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        self.hph = _wrap(self.hph + 0.03 * spd * dt, 4096.0)

        # seed spots: slow Lissajous wanderers, gated by smooth envelopes of integrated time
        mv = (0.45 + 0.75 * e) * spd * dt
        rates = (0.11, 0.083, 0.071, 0.097, 0.13, 0.061)
        for i in range(2 * NSPOT):
            self.sph[i] = _wrap(self.sph[i] + rates[i] * 2 * math.pi * mv, 2 * math.pi)
        grates = (0.09, 0.13, 0.07)
        for i in range(NSPOT):
            self.gph[i] = _wrap(self.gph[i] + grates[i] * 2 * math.pi * spd * dt, 2 * math.pi)
        self.bph = _wrap(self.bph + 0.05 * 2 * math.pi * spd * dt, 2 * math.pi)

        P = self.Ph
        P[0:12] = self.pal
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = 0.6 * float(p["colour_spread"])
        P[15] = self.hph
        tau = 0.035 * 70.0 ** float(p["trail_decay"])
        P[16] = math.exp(-dt / tau) if dt > 0 else 1.0
        P[17] = 0.75
        P[18] = 1.0 + 1.1 * k
        g = float(p["glow"]) * (0.5 + 0.5 * e) * (1.0 + 0.9 * k)
        P[19] = 1.15 * g
        P[20] = 0.95 * g
        ppc = self.w / self.gw                                   # internal px per cell
        P[21] = float(p["cell_look"])
        P[22] = 0.12
        P[23] = 0.8 / max(ppc, 1e-3)
        P[24] = float(p["intensity"])
        P[25] = _ss(1.8, 3.5, ppc)
        P[26] = sd * rule[5] * 2.5e-4 * (0.3 + 0.7 * e)
        gain = sd * rule[5] * (0.25 + 0.75 * e ** 1.3) * 0.10
        r = 2.0 * self.gh * (0.03 + 0.035 * e)
        for i in range(NSPOT):
            sx = 0.42 * self.gw * math.sin(self.sph[2 * i])
            sy = 0.40 * self.gh * math.sin(self.sph[2 * i + 1])
            fx, fy = _fold(2.0 * sx, 2.0 * sy, symn)
            act = _ss(0.15, 0.65, 0.5 + 0.5 * math.sin(self.gph[i]))
            P[28 + 4 * i] = fx
            P[29 + 4 * i] = fy
            P[30 + 4 * i] = 1.0 / (r * r)
            P[31 + 4 * i] = gain * act
        self.P.set(P)

        # beat: a disc burst on a host-detected rising edge (120 ms refractory)
        if dt > 0:
            self.t += dt
        if (not prime and dt > 0 and pulse - self.prev_pulse > 0.15 and self.t - self.last_burst > 0.12):
            self.last_burst = self.t
            self.nburst += 1
            th = self.nburst * 2.39996 + self.bph
            rho = 0.3 * self.gh * (0.35 + 0.65 * ((self.nburst * 0.618034) % 1.0))
            bx = 1.5 * rho * math.cos(th) + 0.15 * self.gw * math.sin(self.bph)
            by = rho * math.sin(th) + 0.10 * self.gh * math.cos(0.7 * self.bph + 1.0)
            fx, fy = _fold(2.0 * bx, 2.0 * by, symn)
            br = 2.0 * self.gh * (0.04 + 0.06 * e) * (0.7 + 0.3 * pulse)
            prob = rule[4] * (0.6 + 0.4 * sd)
            S = self.S[self.cur]
            launch_2d(self.k_burst, self.gw, self.gh, (S, self.T, np.int32(self.gw), np.int32(self.gh),
                                                       np.int32(symn), np.float32(fx), np.float32(fy),
                                                       np.float32(br), np.float32(prob),
                                                       np.uint32(self.nburst & 0x7FFFFFFF), self.salt))
        self.prev_pulse = pulse
        return 42.0 * (0.2 + 0.8 * e ** 1.15) * spd

    def _steps(self, p: dict, n: int) -> None:
        b, s, C = RULES[p["rule"]][:3]
        bm, sm = np.uint32(_mask(b)), np.uint32(_mask(s))
        symn = np.int32(int(p["seed_symmetry"]))
        for _ in range(n):
            self.nstep += 1
            a, o = self.S[self.cur], self.S[1 - self.cur]
            launch_2d(self.k_step, self.gw, self.gh, (a, o, self.T, np.int32(self.gw), np.int32(self.gh), bm, sm,
                                                      np.int32(C), symn, np.uint32(self.nstep & 0x7FFFFFFF),
                                                      self.salt, self.P))
            self.cur = 1 - self.cur

    def _shade_launch(self) -> None:
        launch_2d(self.k_shade, self.gw, self.gh, (self.S[self.cur], self.T, self.E, np.int32(self.gw),
                                                   np.int32(self.gh), np.int32(self._C), self.P))

    @property
    def _C(self) -> int:
        return RULES[self.easer.values()["rule"]][2] if self.easer is not None else 2

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        if int(p["cell_size"]) != self.cs:                       # structural: rebuild the torus
            self._alloc(int(p["cell_size"]), p)
        rate = self._host(p, dt)
        if dt > 0:
            self.acc += rate * min(dt, 0.25)
            n = int(self.acc)
            self.acc -= n
            if n:
                self._steps(p, min(n, MAX_STEPS))
        gw, gh = np.int32(self.gw), np.int32(self.gh)
        launch_2d(self.k_shade, self.gw, self.gh, (self.S[self.cur], self.T, self.E, gw, gh,
                                                   np.int32(RULES[p["rule"]][2]), self.P))
        launch_2d(self.k_soft, self.gw, self.gh, (self.E, self.Es, gw, gh))
        w1, h1, w2, h2 = (np.int32(v) for v in (self.w1, self.h1, self.w2, self.h2))
        launch_2d(self.k_down, self.w1, self.h1, (self.E, gw, gh, self.G1[0], w1, h1))
        launch_2d(self.k_blur, self.w1, self.h1, (self.G1[0], self.G1[1], w1, h1, np.int32(1), np.int32(0)))
        launch_2d(self.k_blur, self.w1, self.h1, (self.G1[1], self.G1[0], w1, h1, np.int32(0), np.int32(1)))
        launch_2d(self.k_down, self.w2, self.h2, (self.G1[0], w1, h1, self.G2[0], w2, h2))
        launch_2d(self.k_blur, self.w2, self.h2, (self.G2[0], self.G2[1], w2, h2, np.int32(1), np.int32(0)))
        launch_2d(self.k_blur, self.w2, self.h2, (self.G2[1], self.G2[0], w2, h2, np.int32(0), np.int32(1)))
        launch_2d(self.k_draw, self.w, self.h, (self.buf, self.E, self.Es, gw, gh, self.G1[0], w1, h1,
                                                self.G2[0], w2, h2, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
