"""Truchet flow: a neon Truchet maze that morphs from C64 '10 PRINT' diagonals into quarter-circle arcs and
ribbons, with light comets flowing along the connected paths and tiles flipping over in waves on the beat.

Source: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/c64-truchet/fragment-0.hlsl
        (Shadertoy https://www.shadertoy.com/view/ftGSWz; licence metadata:
        https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/c64-truchet/metadata.json;
        idea: https://en.wikipedia.org/wiki/Truchet_tiles)
Author: Mårten Rånge (mrange), "C64 truchet pattern"; extensions by smfx
License: CC0-1.0
license_class: permissive
    Licence as seen 2026-10-14: the shader's own header reads "// CC0 - 10 PRINT CHR$(205.5+RND(1)); : GOTO 10",
    and the gallery entry's metadata.json says "license-expressions": ["CC0-1.0"], "authors": ["Mårten Rånge"].
    (The gallery repository as a whole is MIT, Copyright (c) 2022 Mårten Rånge; the per-shader CC0 applies here.)
    The original's fract(sin(dot(p, (127.1, 311.7))) * 43758.5453) cell hash is replaced by the lab's integer
    hash1 (helpers in smfx/_lib/cuda.py).
Ported from HLSL by smfx (ideas/changes: the original is one layer of random 45-degree segments (the 10 PRINT
    coin toss per cell), a 3x3 neighbour SDF search, a 0.1 line offset, aa smoothstep and a slow Lissajous
    pan/zoom.  smfx keeps the per-cell hashed diagonal SDF with round caps and the pan/zoom idea, and adds: a
    continuous geometric morph of each segment into a pair of quarter-circle arcs (circle arcs of shrinking
    radius whose ends slide from the corners to the edge midpoints) and on into 3-lane ribbons, with plateaus at
    the three canonical looks; a checkerboard-parity flow orientation plus a parity path coordinate so light
    comets (spaced two tiles apart) run continuously along every closed path; per-tile 90-degree flips (a hashed
    flip clock plus beat-spawned radial flip waves, folded into a per-tile state torus when they retire); a
    half-size sub-tile maze drawn inside alternate regions; two-tone region tint; neon tube shading with a hot
    core, glow and bloom; cosine-palette colouring and the smfx macro interface.)

Layers per pixel (tile units, y up): parent maze evaluated over the 2x2 cells around the nearest grid vertex
(exact bounded arc/segment distances, so rotating tiles and round caps never clip), then the child maze at twice
the frequency, masked to one region parity of the parent.  A tiny pre-pass kernel computes each visible tile's
rotation state (quarter turns Q incl. in-flight fraction, and spin direction) so the pixel kernel only loads it.

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> comet flow rate x(0.4+1.2e'), random flip rate x(0.35+1.3e'),
glow/bloom amplitude, comet tail length and brightness, sub-tile weight (+0.3 e'^1.5, slewed over 2 s), camera
drift rate and the share of tiles each flip wave turns.  pulse (k = pulse (0.3 + 0.7 e)) -> a rate kick into the
comet flow phase and a comet brightness flash of at most +35 % (flow_kick), and on each rising edge (fire > 0.6
after < 0.3, >= 150 ms apart) a radial flip wave whose front advances at its own rate and turns a share
(wave_kick) of the tiles it passes by 90 degrees over ~0.25 s.  speed scales every clock; palette indexes a slow
spatial field (continuous along every path) + drift; comets take a hue offset of the same palette.
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

TORUS = 256            # tile hashes / flip state repeat every 256 tiles (far larger than any view)
MAX_WAVES = 8
T1_W, T1_H = 96, 64    # tile-state buffer (max view at 40 tiles/height incl. zoom breathing + margin)
NP = 64                # kernel param floats
NTP = 8 + 6 * MAX_WAVES
SQ2 = math.sqrt(2.0)
DRIFT_WRAP = 20.0      # every palette frequency is a multiple of 0.05, so a wrap at 20 is invisible

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TORUS 256
__device__ __forceinline__ float2 rot2(float2 p, float c, float s) {
    return make_float2(c * p.x - s * p.y, s * p.x + c * p.y);
}

// ---------------------------------------------------------------- per-tile rotation state (pre-pass)
// TP: [0] tx0 [1] ty0 [2] flip phase (wraps at 4) [3] flip width (fraction of a flip period)
//     [4] n waves [8 + 6 i]: origin x, origin y, radius, front width, share of tiles, hash seed
extern "C" __global__
void tiles(float2* __restrict__ T, const int TW, const int TH, const float* __restrict__ TP,
           const float* __restrict__ state, const unsigned int seed, const int waves)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= TW || j >= TH) return;
    const int cx = (int)TP[0] + i, cy = (int)TP[1] + j;
    const unsigned int ux = (unsigned int)(cx & (TORUS - 1)), uy = (unsigned int)(cy & (TORUS - 1));
    float Q = hash1(ux, uy, seed) < 0.5f ? 0.f : 1.f;                  // the 10 PRINT coin toss
    const float dir = hash1(ux, uy, seed + 1u) < 0.5f ? -1.f : 1.f;     // which way this tile spins
    const float xph = TP[2] + hash1(ux, uy, seed + 2u);                 // random flip clock
    const float k = floorf(xph);
    Q += k + smooth(0.f, TP[3], xph - k);
    if (waves) {
        Q += state[uy * TORUS + ux];
        const int nw = (int)TP[4];
        for (int w = 0; w < nw; ++w) {
            const float* Wv = TP + 8 + 6 * w;
            const float dx = cx + 0.5f - Wv[0], dy = cy + 0.5f - Wv[1];
            const float pr = (Wv[2] - sqrtf(dx * dx + dy * dy)) / Wv[3];
            if (pr > 0.f && hash1(ux, uy, (unsigned int)Wv[5]) < Wv[4]) Q += smooth(0.f, 1.f, pr);
        }
    }
    T[j * TW + i] = make_float2(Q, dir);
}

// A retired flip wave becomes permanent: +1 quarter turn for the share of tiles it flipped.
extern "C" __global__
void fold(float* __restrict__ state, const unsigned int wseed, const float share)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= TORUS || j >= TORUS) return;
    if (hash1((unsigned int)i, (unsigned int)j, wseed) < share) {
        const float v = state[j * TORUS + i] + 1.f;
        state[j * TORUS + i] = v >= 4.f ? v - 4.f : v;
    }
}

// ---------------------------------------------------------------- per-pixel maze
// G (arc geometry, canonical "/" tile): [0] R [1] c (centre at (-c, c) and (c, -c)) [2] alpha [3] cos a
//   [4] sin a [5] lane offset [6] side-lane weight [7] arc length (tile units) [8] neighbour reach
// L (layer): [0] half width [1] glow width [2] flow phase centre lane [3] flow phase side lanes
//   [4] comet tail power [5] pixel size (tile units) [6] line-thin compensation [7] comet halo width
struct Acc { float line, core, dash, glow, halo; };

// Stroke coverage, tube core, comet and comet halo for one lane at distance d.  s is the parity path
// coordinate (continuous mod 2 along every path), so comets are spaced two tiles apart.
__device__ __forceinline__ void lane_hit(float d, float wgt, float s, float ph, const float* __restrict__ L,
                                         float fade, float hs, Acc& a)
{
    const float cov = clamp01((L[0] - d) / L[5] + 0.5f) * wgt;
    const float xs = 0.5f * s - ph;
    const float x = xs - floorf(xs);                                     // -> 1 at a comet head
    const float body = __powf(fmaxf(x, 1e-6f), L[4]) * fade * wgt;
    const float head = 1.f - smooth(1.f - hs, 1.f, x);                   // anti-aliased head
    const float h = fmaxf(d - L[0], 0.f) / L[7];
    const float rr = d / fmaxf(L[0], 1e-6f);
    a.line = fmaxf(a.line, cov * L[6]);
    a.core = fmaxf(a.core, cov * L[6] * clamp01(1.f - rr * rr));
    a.dash = fmaxf(a.dash, cov * body * head);
    a.halo = fmaxf(a.halo, body * body * head * __expf(-h * h));         // soft bloom around the comet
}

// Rotate a tile-local point into the canonical "/" frame of a tile with Q quarter turns (fraction in flight).
__device__ __forceinline__ float2 canon(float2 p, float Q, float dir, int& o, float& fr)
{
    const float qf = floorf(Q);
    fr = Q - qf;
    o = ((int)qf) & 1;
    if (fr > 0.f) {                                                      // undo the in-flight turn
        float s, c;
        __sincosf(-dir * fr * 1.5707963f, &s, &c);
        p = rot2(p, c, s);
    }
    if (o) p = make_float2(p.y, -p.x);                                   // "\" = "/" turned 90 degrees
    return p;
}

// The two strands of a canonical "/" tile (arcs around TL and BR; the diagonal at G[0] -> large, ribbons when
// G[6] > 0) at local point p.  even: the arcs' corners have even parity (flow runs CCW around them); par: the
// tile's checkerboard parity (path coordinate offset).  Returns whether p lies inside either arc's circle.
template <bool GLOW>
__device__ bool strands(float2 p, int even, float par, float fade, float phx, const float* __restrict__ G,
                        const float* __restrict__ L, Acc& a)
{
    const float hs = fminf(fmaxf(0.01f, 0.75f * L[5] / G[7]), 0.2f);    // ~1.5 px head in path units
    const float R = G[0], lo = G[5], sw = G[6];
    const float reach = L[0] + 2.2f * fmaxf(L[7], L[5]);
    bool inside = false;
    #pragma unroll
    for (int k = 0; k < 2; ++k) {
        const float sg = k == 0 ? 1.f : -1.f;
        const float2 m = make_float2(0.70710678f * sg, -0.70710678f * sg);
        const float2 q = make_float2(p.x + G[1] * sg, p.y - G[1] * sg);  // p - C, C = (-c, c) * sg
        const float lq = sqrtf(q.x * q.x + q.y * q.y);
        inside = inside || (lq < R);
        const float cr = m.x * q.y - m.y * q.x, dt = m.x * q.x + m.y * q.y;   // CCW from the mid-direction
        const bool inw = dt > 0.f && fabsf(cr) <= G[4] * lq;            // within the arc's angular extent
        const float sa = cr >= 0.f ? G[4] : -G[4];
        const float2 e = make_float2(G[3] * m.x - sa * m.y, G[3] * m.y + sa * m.x);   // near end direction
        const float d0 = inw ? fabsf(lq - R) : sqrtf((q.x - R * e.x) * (q.x - R * e.x) + (q.y - R * e.y) * (q.y - R * e.y));
        float d1 = 1e9f, d2 = 1e9f;
        if (sw > 0.f) {                                                  // ribbon side lanes
            const float Ra = R - lo, Rb = R + lo;
            d1 = inw ? fabsf(lq - Ra) : sqrtf((q.x - Ra * e.x) * (q.x - Ra * e.x) + (q.y - Ra * e.y) * (q.y - Ra * e.y));
            d2 = inw ? fabsf(lq - Rb) : sqrtf((q.x - Rb * e.x) * (q.x - Rb * e.x) + (q.y - Rb * e.y) * (q.y - Rb * e.y));
        }
        // nearest lane only (strokes never overlap once the lanes are apart; fading side lanes lose ties)
        const float ds = fminf(d1, d2);
        const bool side = ds + 0.3f * (1.f - sw) < d0;
        const float dn = side ? ds : d0;
        if (GLOW) a.glow = fminf(a.glow, fminf(d0, ds));                // nearest distance; glow taken at the end
        if (dn < reach) {
            const float uc = clamp01(0.5f + atan2f(cr, dt) / (2.f * G[2]));
            const float u = even ? uc : 1.f - uc;                        // flow: CCW around even corners
            lane_hit(dn, side ? sw : 1.f, u + par, (side ? L[3] : L[2]) + phx, L, fade, hs, a);
        }
    }
    return inside;
}

// P: [0,1] camera centre (parent tiles) [2] parent tiles per pixel [3] child weight [4] glow amp
//    [5] comet gain [6] line brightness [7] region tint [8] palette period [9] drift [10] gain
//    [11] screen units per tile (colour field) [12] vignette [13] child footprint fade
//    [14..15] parent buffer origin [16] child flip phase [17] child flip width [18] comet halo gain
//    [19] comet hue offset [20..28] G [29] turning-tile poke-out [30] tube core whiteness [31] bloom amp
//    [32] child brightness [36..43] L child [44..55] palette [56..63] L parent
extern "C" __global__
void truchet(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
             const float2* __restrict__ T1, const int TW, const int TH, const unsigned int cseed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sx = x + 0.5f - 0.5f * W, sy = 0.5f * H - (y + 0.5f);
    const float wx = P[0] + sx * P[2], wy = P[1] + sy * P[2];
    const float* G = P + 20;
    const float* L = P + 56;
    // comet phase offset: a slow continuous field, so comets stay continuous along every path but are not in
    // lockstep across the screen
    const float phx = 0.11f * wx + 0.07f * wy + 0.55f * __sinf(0.23f * wx + 0.8f * __sinf(0.19f * wy));
    // parent maze: the 2x2 cells around the nearest grid vertex (rotating tiles and round caps never clip)
    Acc A = {0.f, 0.f, 0.f, 1e9f, 0.f};
    const float fx = floorf(wx), fy = floorf(wy);
    const int ix = (int)fx, iy = (int)fy;
    const int bx = (wx - fx < 0.5f) ? ix - 1 : ix, by = (wy - fy < 0.5f) ? iy - 1 : iy;
    const int tx0 = (int)P[14], ty0 = (int)P[15];
    float region = 0.f, own_fade = 1.f;
    float2 own_p = make_float2(0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 2; ++j) {
        #pragma unroll
        for (int i = 0; i < 2; ++i) {
            const int cx = bx + i, cy = by + j;
            const bool own = cx == ix && cy == iy;
            const int ti = min(max(cx - tx0, 0), TW - 1), tj = min(max(cy - ty0, 0), TH - 1);
            const float2 Qd = T1[tj * TW + ti];
            if (!own) {                                                  // skip neighbours out of reach
                const float ox = fmaxf(fmaxf((float)cx - wx, wx - (float)(cx + 1)), 0.f);
                const float oy = fmaxf(fmaxf((float)cy - wy, wy - (float)(cy + 1)), 0.f);
                const float rn = G[8] + (Qd.x != floorf(Qd.x) ? P[29] : 0.f);   // turning tiles poke out
                if (ox * ox + oy * oy > rn * rn) continue;
            }
            int o; float fr;
            const float2 p = canon(make_float2(wx - (float)cx - 0.5f, wy - (float)cy - 0.5f), Qd.x, Qd.y, o, fr);
            const int even = ((cx + cy + 1 + o) & 1) == 0;               // parity of the arcs' corners
            const float fade = 1.f - __sinf(3.14159265f * fr);           // comets dim while a tile turns
            const bool inside = strands<true>(p, even, (float)((cx + cy) & 1), fade, phx, G, L, A);
            if (own) {
                // two-tone regions: quarter disks hold the arcs' corner parity, the band the other one; after
                // a quarter turn the same geometry carries the opposite parity, so crossfade through the turn
                const float pre = inside ? (float)(1 - even) : (float)even;
                region = pre + (1.f - 2.f * pre) * smooth(0.f, 1.f, fr);
                own_p = p;
                own_fade = fade;
            }
        }
    }
    // sub-tile maze: the own tile's half-size tiles in its canonical frame (so they turn with it), drawn inside
    // alternate regions only; own flip clock, no waves.  Thin lines: the pixel's own sub-tile is enough.
    Acc C = {0.f, 0.f, 0.f, 1e9f, 0.f};
    const float cw = P[3] * P[13] * region;
    if (cw > 0.003f) {
        const unsigned int ux = (unsigned int)(ix & (TORUS - 1)), uy = (unsigned int)(iy & (TORUS - 1));
        const float2 q = make_float2(2.f * own_p.x + 1.f, 2.f * own_p.y + 1.f);
        const int i = q.x < 1.f ? 0 : 1, j = q.y < 1.f ? 0 : 1;
        const unsigned int hx = ux * 2u + i, hy = uy * 2u + j;
        float Q = hash1(hx, hy, cseed) < 0.5f ? 0.f : 1.f;
        const float dir = hash1(hx, hy, cseed + 1u) < 0.5f ? -1.f : 1.f;
        const float xph = P[16] + hash1(hx, hy, cseed + 2u);
        const float k = floorf(xph);
        Q += k + smooth(0.f, P[17], xph - k);
        int o; float fr;
        const float2 p = canon(make_float2(q.x - i - 0.5f, q.y - j - 0.5f), Q, dir, o, fr);
        const int even = ((i + j + 1 + o) & 1) == 0;
        strands<false>(p, even, (float)((i + j) & 1), own_fade * (1.f - __sinf(3.14159265f * fr)), 2.f * phx,
                       G, P + 36, C);
    }
    // colour: a slow field over the plane (in screen units) so hue follows each path as it wanders
    const float u = wx * P[11], v = wy * P[11];
    const float F = 0.42f * (0.8f * u + 0.6f * v) + 0.22f * __sinf(1.7f * u + 1.9f * __sinf(1.1f * v + 0.4f));
    const float idx = F * P[8] + P[9];
    const float* PAL = P + 44;
    const float3 cl = pal(idx, PAL);
    const float3 cc = pal(idx + P[19], PAL);
    const float3 cb = pal(idx + 0.08f, PAL);
    // background: near-black, the parent's two-tone regions tinted in the local hue
    const float tb = 0.012f + P[7] * region;
    float r = cb.x * tb, g = cb.y * tb, b = cb.z * tb;
    // sub-tile maze: same hue family, paler and dimmer, its own comets
    const float ck = cw * P[32] * (0.55f * C.line + 0.25f * C.core + 0.9f * C.dash + 0.3f * C.halo);
    r += (0.8f * cb.x + 0.1f) * ck; g += (0.8f * cb.y + 0.1f) * ck; b += (0.8f * cb.z + 0.1f) * ck;
    // glow + wide bloom under the lines
    const float gd = fmaxf(A.glow - L[0], 0.f);
    const float ge = __expf(-gd / L[1]);
    const float gl = P[4] * ge * ge + P[31] * __expf(-gd / (4.f * L[1]));
    r += cl.x * gl; g += cl.y * gl; b += cl.z * gl;
    // neon tubes over (hot core towards white), then comets (head towards white) and their bloom
    const float lb = P[6];
    const float wc = P[30] * A.core;
    r = r * (1.f - A.line) + lb * (A.line * cl.x + wc * (1.f - cl.x));
    g = g * (1.f - A.line) + lb * (A.line * cl.y + wc * (1.f - cl.y));
    b = b * (1.f - A.line) + lb * (A.line * cl.z + wc * (1.f - cl.z));
    const float dk = P[5] * A.dash;
    const float wh = 0.15f + 0.75f * A.dash * A.dash * A.dash;
    const float hk = P[18] * A.halo;
    r += (cc.x * (1.f - wh) + wh) * dk + cc.x * hk;
    g += (cc.y * (1.f - wh) + wh) * dk + cc.y * hk;
    b += (cc.z * (1.f - wh) + wh) * dk + cc.z * hk;
    // vignette + gain
    const float ex = sx / (0.5f * H), ey = sy / (0.5f * H);
    const float vg = P[10] * (1.f - P[12] * (ex * ex + ey * ey) * 0.25f);
    out[y * W + x] = make_uchar4((unsigned char)(255.f * clamp01(r * vg) + 0.5f),
                                 (unsigned char)(255.f * clamp01(g * vg) + 0.5f),
                                 (unsigned char)(255.f * clamp01(b * vg) + 0.5f), 255);
}
"""


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _sstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def style_axes(style: float) -> tuple[float, float]:
    """tile_style -> (sp: diagonal->arcs, lam: arcs->ribbons), with plateaus at the three canonical looks
    (0-0.1 diagonal, ~0.42-0.58 quarter arcs, 0.92-1 ribbons) so most settings land on a connected maze."""
    return _sstep(0.1, 0.42, style), _sstep(0.58, 0.92, style)


def arc_geometry(sp: float, lam: float) -> list[float]:
    """Canonical "/" tile: two circle arcs (around TL and BR) that are the straight diagonal at sp = 0, quarter
    circles of radius 0.5 at sp = 1, plus 3-lane ribbons by lam.  Returns G[0..8] with G[8] = how far a turning
    tile's content can poke out of its cell (the caller adds stroke and glow reach)."""
    kappa = max(2.0 * sp ** 1.3, 1.0 / 400.0)
    R = 1.0 / kappa
    delta = (SQ2 / 2.0 - 0.5) * sp                       # arc's closest approach to the tile centre
    c = (R + delta) / SQ2
    yl = c - math.sqrt(max(R * R - (c - 0.5) ** 2, 0.0))  # where the arc leaves through the left edge
    ex, ey = -0.5 + c, yl - c
    ca = (ex - ey) / SQ2 / max(math.hypot(ex, ey), 1e-9)
    alpha = math.acos(min(max(ca, -1.0), 1.0))
    lanes = 0.15 * _sstep(0.0, 1.0, lam)
    side_w = _sstep(0.0, 0.35, lam)
    poke = math.sqrt(0.25 + max(yl * yl, lanes * lanes)) - 0.5
    return [R, c, alpha, math.cos(alpha), math.sin(alpha), lanes, side_w, R * 2.0 * alpha, poke]


class TruchetFlow(Source):
    name = "truchet_flow"
    label = "Truchet flow"
    description = ("A neon Truchet maze morphing from C64 '10 PRINT' diagonals into arcs and ribbons, with light "
                   "flowing along the paths and tiles flipping over in waves on the beat.")
    params = {
        "tile_style": num(0.0, 1.0, 0.5, "Tile style (diagonal / arcs / ribbons)", basic=True),
        "tile_scale": num(6.0, 40.0, 14.0, "Tiles per screen height", step=0.5, basic=True),
        "line_width": num(0.02, 0.2, 0.08, "Line width", step=0.005),
        "multiscale": num(0.0, 1.0, 0.3, "Sub-tiles (half-size layer)"),
        "flip_rate": num(0.0, 30.0, 3.0, "Tile flips", step=0.5, unit="/tile/min"),
        "glow": num(0.0, 2.0, 0.8, "Glow"),
        "flow_kick": num(0.0, 1.0, 0.6, "Beat kick: light flow"),
        "wave_kick": num(0.0, 1.0, 0.3, "Beat kick: flip wave (share of tiles)"),
        **standard_scene_params(palette="neon", render_scale=0.75, period=1.0, drift=0.03),
        "flow_speed": num(0.0, 4.0, 1.0, "Light flow speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self.k_tiles = mod.get_function("tiles")
        self.k_fold = mod.get_function("fold")
        self.k_main = mod.get_function("truchet")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        self.TP1 = cp.zeros(NTP, cp.float32)
        self.TPh = np.zeros(NTP, np.float32)
        self.T1 = cp.zeros((T1_H, T1_W, 2), cp.float32)
        self.state = cp.zeros((TORUS, TORUS), cp.float32)
        seed = int(params["seed"])
        self.seed = seed & 0xFFFFFF
        self.rng = np.random.default_rng(seed)
        self.cam_ph = float(self.rng.uniform(0.0, 2.0 * math.pi))   # Lissajous pan (host only, never wrapped)
        self.zoom_ph = float(self.rng.uniform(0.0, 2.0 * math.pi))
        self.flow_c = 0.0                 # comet phases (wrap at 1: one comet per two tiles per lane)
        self.flow_s = 0.0
        self.flow_k = 0.0
        self.flip1 = 0.0                  # flip clocks (wrap at 4 quarter turns)
        self.flip2 = 0.0
        self.drift = 0.0
        self.e_slow = None
        self.t = 0.0
        self.armed = True
        self.last_spawn = -1e9
        self.waves: list[dict] = []
        self.wave_width = 1.0
        self.cx = self.cy = 0.0

    # ------------------------------------------------------------------ host side
    def _view(self, tph: float):
        """Visible parent-tile window (x0, y0, x1, y1) around the camera for tiles-per-height tph."""
        hw = 0.5 * tph * self.ctx.width / self.ctx.height
        hh = 0.5 * tph
        return self.cx - hw, self.cy - hh, self.cx + hw, self.cy + hh

    def _waves_step(self, p: dict, ep: float, pulse: float, spd: float, dt: float, tph: float) -> None:
        # rising-edge detector with hysteresis (fire > 0.6 after dropping < 0.3, >= 150 ms apart)
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.t - self.last_spawn >= 0.15:
            self.armed = False
            self.last_spawn = self.t
            share = float(p["wave_kick"]) * (0.4 + 0.6 * ep)
            if share > 0.005 and len(self.waves) < MAX_WAVES:
                x0, y0, x1, y1 = self._view(tph)
                ox = self.cx + 0.4 * (x1 - x0) * float(self.rng.uniform(-1.0, 1.0))
                oy = self.cy + 0.4 * (y1 - y0) * float(self.rng.uniform(-1.0, 1.0))
                self.waves.append({"ox": ox, "oy": oy, "r": 0.0, "share": share,
                                   "seed": int(self.rng.integers(1, 1 << 24))})
        # advance fronts: ~0.8 screen heights per second (in tiles), a little faster with energy
        v = 0.8 * tph * (0.75 + 0.5 * ep) * spd
        width = max(0.6, 0.25 * 0.8 * tph)                # one tile turns over ~0.25 s at nominal speed
        x0, y0, x1, y1 = self._view(tph)
        keep = []
        for w in self.waves:
            w["r"] += v * dt
            far = max(math.hypot(cx - w["ox"], cy - w["oy"]) for cx in (x0 - 2, x1 + 2) for cy in (y0 - 2, y1 + 2))
            if w["r"] - width > far:                      # every visible tile has finished turning: fold it in
                launch_2d(self.k_fold, TORUS, TORUS, (self.state, np.uint32(w["seed"]), np.float32(w["share"])))
            else:
                keep.append(w)
        self.waves = keep
        self.wave_width = width

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.t += dt
        ep = _sstep(0.1, 0.85, e)
        a2 = 1.0 - math.exp(-dt / 2.0) if dt > 0 else 1.0
        self.e_slow = ep if self.e_slow is None else self.e_slow + (ep - self.e_slow) * a2
        k = pulse * (0.3 + 0.7 * e)
        fk = float(p["flow_kick"])

        # ---- clocks (rates only; kicks add to rates).  Flow phase unit = one comet spacing (two tiles of path).
        flow = 0.5 * 1.1 * float(p["flow_speed"]) * (0.4 + 1.2 * ep) * (1.0 + 2.5 * fk * k) * spd * dt
        self.flow_c = _wrap(self.flow_c + flow, 1.0)
        self.flow_s = _wrap(self.flow_s + 0.72 * flow, 1.0)
        self.flow_k = _wrap(self.flow_k + 1.35 * flow, 1.0)
        fr = float(p["flip_rate"]) / 60.0
        flip = fr * (0.35 + 1.3 * ep) * spd * dt
        self.flip1 = _wrap(self.flip1 + flip, 4.0)
        self.flip2 = _wrap(self.flip2 + 0.8 * flip, 4.0)
        fw = min(max(0.25 * fr, 0.005), 0.5)               # a flip takes ~0.25 s at the nominal rate
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, DRIFT_WRAP)
        self.cam_ph += 0.035 * float(p["tile_scale"]) / 60.0 * (0.5 + 0.8 * ep) * spd * dt
        self.zoom_ph = _wrap(self.zoom_ph + 0.11 * spd * dt, 2.0 * math.pi)
        A = 60.0
        self.cx = A * math.sin(self.cam_ph)
        self.cy = A * math.sin(SQ2 / 2.0 * self.cam_ph + 1.3)
        tph = float(p["tile_scale"]) * (1.0 + 0.05 * math.sin(self.zoom_ph))
        self._waves_step(p, ep, pulse, spd, dt, tph)

        # ---- tile pre-pass: each visible tile's quarter turns (coin toss + flip clock + folded and live waves)
        x0, y0, x1, y1 = self._view(tph)
        tx0, ty0 = math.floor(x0) - 2, math.floor(y0) - 2
        TPh = self.TPh
        TPh[:] = 0
        TPh[0], TPh[1], TPh[2], TPh[3] = tx0, ty0, self.flip1, fw
        TPh[4] = len(self.waves)
        for i, w in enumerate(self.waves):
            TPh[8 + 6 * i: 14 + 6 * i] = (w["ox"], w["oy"], w["r"], self.wave_width, w["share"], w["seed"])
        self.TP1.set(TPh)
        launch_2d(self.k_tiles, T1_W, T1_H, (self.T1, np.int32(T1_W), np.int32(T1_H), self.TP1, self.state,
                                             np.uint32(self.seed), np.int32(1)))

        # ---- per-pixel params
        sp, lam = style_axes(float(p["tile_style"]))
        G = arc_geometry(sp, lam)
        tpp = tph / self.h                                 # parent tiles per internal pixel
        hw = 0.5 * float(p["line_width"])
        hw = hw + (min(hw, 0.05) - hw) * lam               # ribbons: lanes must stay apart
        glow = float(p["glow"])
        fl = 1.0 + 0.35 * fk * k                           # comet flash (<= +35 %)
        Ph = self.Ph
        Ph[:] = 0
        Ph[0], Ph[1], Ph[2] = self.cx, self.cy, tpp
        Ph[3] = min(1.0, float(p["multiscale"]) + 0.3 * self.e_slow ** 1.5)
        Ph[4] = glow * (0.5 + 0.6 * ep) * 0.55
        Ph[31] = glow * (0.5 + 0.6 * ep) * 0.07
        Ph[5] = (0.85 + 0.6 * ep) * fl
        Ph[18] = 0.5 * (0.85 + 0.6 * ep) * fl * (0.4 + 0.6 * min(glow, 1.0))
        Ph[19] = 0.16
        Ph[6] = 0.62
        Ph[30] = 0.45
        Ph[7] = 0.07 * sp * (0.7 + 0.3 * lam)
        Ph[8] = float(p["palette_period"])
        Ph[9] = self.drift
        Ph[10] = float(p["intensity"])
        Ph[11] = 1.0 / max(tph, 1e-3)
        Ph[12] = 0.35
        child_px = 0.5 / tpp                               # child tile size in internal pixels
        Ph[13] = _sstep(5.0, 11.0, child_px)
        Ph[14], Ph[15], Ph[16] = tx0, ty0, self.flip2
        Ph[17] = min(max(0.2 * fr, 0.005), 0.5)
        Ph[32] = 0.8
        gw = 0.04 + 0.07 * glow
        w_par = max(hw, 0.6 * tpp)
        Ph[29] = G[8]
        G[8] = w_par + max(2.0 * gw, 2.2 * (w_par + 0.03), 1.5 * tpp)    # neighbour cells beyond this: skipped
        Ph[20:29] = G
        tail = 6.5 - 3.5 * ep                              # longer comet tails with energy
        for base, w_half, pix, phc, phs in ((56, hw, tpp, self.flow_c, self.flow_s),
                                            (36, 0.7 * hw, 2.0 * tpp, self.flow_k, self.flow_k)):
            w_eff = max(w_half, 0.6 * pix)                 # never thinner than ~1 px: dim instead of shimmer
            Ph[base + 0] = w_eff
            Ph[base + 1] = gw
            Ph[base + 2] = phc
            Ph[base + 3] = phs
            Ph[base + 4] = tail
            Ph[base + 5] = pix
            Ph[base + 6] = w_half / w_eff
            Ph[base + 7] = w_eff + 0.03
        Ph[44:56] = palette_coeffs(p["palette"])
        self.P.set(Ph)
        launch_2d(self.k_main, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.T1,
                                                np.int32(T1_W), np.int32(T1_H), np.uint32(self.seed ^ 0x5A5A5)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.T1 = self.state = self.P = self.TP1 = None
