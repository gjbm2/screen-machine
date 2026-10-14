"""ASCII Terminal: the picture rebuilt from monospaced glyphs.  A cell pass averages each cell's colour and luma and
picks a glyph from a density ramp in an original 8x8 bitmap atlas baked in setup; edge cells get directional
| / - \\ glyphs from an orientation vote over the cell's Sobel gradients.  Colour morphs source -> mono green
phosphor -> palette.

Source: https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_ASCII.fx
        https://github.com/GarrettGunnell/AcerolaFX/blob/main/LICENSE.md
        https://github.com/pmndrs/postprocessing/blob/main/src/effects/ASCIIEffect.js
Author: Garrett Gunnell (AcerolaFX ASCII); Raoul van Ruschen (pmndrs ASCIIEffect); original glyph atlas by smfx
License: MIT + Zlib
license_class: permissive
Ported from HLSL/GLSL by smfx (ideas/changes: kept from the sources: a downsampled cell pass that picks a fill glyph
from a luminance ramp, edge cells overriding the fill with a directional glyph chosen by a vote over the cell's
Sobel angles (AcerolaFX), exposure on the luma before quantising, and glyph colour from the cell colour.  New: the
glyph atlas is an original 8x8 bitmap set drawn here (no font or texture is copied), with several equal-density
variants per ramp level; the vote is a structure tensor (gradient outer products summed over the cell), so the
winning orientation is weighted by edge strength and gated by coherence; the cell grid is centred and its size
eases by cross-blending the two neighbouring integer pixel sizes (cells never crawl); colour is a continuous
source -> mono phosphor -> cosine-palette morph with integrated drift; glyphs are sampled bilinear-and-sharpened
so they stay crisp at any cell size.)

amount 0 copies the input.  Rising amount (m = smoothstep) fades the glyph layer in, grows the cells from 60%
size to cell_size and lowers the source share under the glyphs to `background`, so the image crystallises into
text.  pulse kicks the exposure (glyphs step up ~1-2 density levels) and starts a decaying scramble that swaps
glyphs for others of equal density, keyed to an integrated phase (never the frame count).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

# ----------------------------------------------------------------------------- original 8x8 glyph atlas
# '#' = ink.  Fill glyphs keep a blank right column and bottom row (character spacing); edge glyphs run to the cell
# border so neighbouring edge cells join into continuous strokes.
_G = {
    "space": ["........"] * 8,
    "dot": ["........", "........", "........", "........", "........", "...##...", "...##...", "........"],
    "comma": ["........", "........", "........", "........", "........", "...##...", "...##...", "..##...."],
    "quote": ["...##...", "...##...", "..##....", "........", "........", "........", "........", "........"],
    "tick": ["..##....", "...##...", "........", "........", "........", "........", "........", "........"],
    "colon": ["........", "...##...", "...##...", "........", "........", "...##...", "...##...", "........"],
    "minus": ["........", "........", "........", ".######.", "........", "........", "........", "........"],
    "caret": ["...#....", "..#.#...", ".#...#..", "........", "........", "........", "........", "........"],
    "tilde": ["........", "........", "........", ".##...#.", "#..#.#..", "....#...", "........", "........"],
    "semi": ["........", "...##...", "...##...", "........", "........", "...##...", "...##...", "..##...."],
    "equal": ["........", "........", ".######.", "........", ".######.", "........", "........", "........"],
    "plus": ["........", "...#....", "...#....", ".#####..", "...#....", "...#....", "........", "........"],
    "lt": ["....##..", "...##...", "..##....", ".##.....", "..##....", "...##...", "....##..", "........"],
    "gt": [".##.....", "..##....", "...##...", "....##..", "...##...", "..##....", ".##.....", "........"],
    "r": ["........", "........", ".#.##...", ".##..#..", ".#......", ".#......", ".#......", "........"],
    "i": ["...#....", "........", "..##....", "...#....", "...#....", "...#....", "..###...", "........"],
    "l": ["..##....", "...#....", "...#....", "...#....", "...#....", "...#....", "..###...", "........"],
    "star": ["........", ".#.#.#..", "..###...", ".#####..", "..###...", ".#.#.#..", "........", "........"],
    "c": ["........", "........", "..###...", ".#...#..", ".#......", ".#...#..", "..###...", "........"],
    "v": ["........", "........", ".#...#..", ".#...#..", ".#...#..", "..#.#...", "...#....", "........"],
    "x": ["........", "........", ".#...#..", "..#.#...", "...#....", "..#.#...", ".#...#..", "........"],
    "z": ["........", "........", ".#####..", "....#...", "...#....", "..#.....", ".#####..", "........"],
    "qmark": ["..###...", ".#...#..", ".....#..", "....#...", "...#....", "........", "...#....", "........"],
    "o": ["........", "........", "..###...", ".#...#..", ".#...#..", ".#...#..", "..###...", "........"],
    "n": ["........", "........", ".####...", ".#...#..", ".#...#..", ".#...#..", ".#...#..", "........"],
    "u": ["........", "........", ".#...#..", ".#...#..", ".#...#..", ".#...#..", "..####..", "........"],
    "k": [".#......", ".#......", ".#..#...", ".#.#....", ".##.....", ".#.#....", ".#..#...", "........"],
    "s": ["........", "........", "..####..", ".#......", "..###...", ".....#..", ".####...", "........"],
    "e": ["........", "........", "..###...", ".#...#..", ".#####..", ".#......", "..###...", "........"],
    "a": ["........", "........", "..###...", ".....#..", "..####..", ".#...#..", "..####..", "........"],
    "h": [".#......", ".#......", ".####...", ".#...#..", ".#...#..", ".#...#..", ".#...#..", "........"],
    "P": [".####...", ".#...#..", ".#...#..", ".####...", ".#......", ".#......", ".#......", "........"],
    "O": ["..###...", ".#...#..", ".#...#..", ".#...#..", ".#...#..", ".#...#..", "..###...", "........"],
    "dollar": ["...#....", "..####..", ".#.#....", "..###...", "...#.#..", ".####...", "...#....", "........"],
    "amp": ["..##....", ".#..#...", ".#.#....", "..#.....", ".#.#.#..", ".#..#...", "..##.#..", "........"],
    "pct": ["##...#..", "##..#...", "...#....", "..#.....", ".#..##..", "#...##..", "........", "........"],
    "hash": ["..#.#...", "..#.#...", ".#####..", "..#.#...", ".#####..", "..#.#...", "..#.#...", "........"],
    "at": ["..###...", ".#...#..", ".#.###..", ".#.#.#..", ".#.###..", ".#......", "..####..", "........"],
    "B": [".####...", ".#...#..", ".#...#..", ".####...", ".#...#..", ".#...#..", ".####...", "........"],
    "eight": ["..###...", ".#...#..", ".#...#..", "..###...", ".#...#..", ".#...#..", "..###...", "........"],
    "zero": ["..###...", ".#...#..", ".#..##..", ".#.#.#..", ".##..#..", ".#...#..", "..###...", "........"],
    "N": [".#...#..", ".##..#..", ".#.#.#..", ".#..##..", ".#...#..", ".#...#..", ".#...#..", "........"],
    "M": [".#...#..", ".##.##..", ".#.#.#..", ".#.#.#..", ".#...#..", ".#...#..", ".#...#..", "........"],
    "W": [".#...#..", ".#...#..", ".#...#..", ".#.#.#..", ".#.#.#..", ".##.##..", ".#...#..", "........"],
    "shade1": ["#...#...", "..#...#.", "#...#...", "..#...#.", "#...#...", "..#...#.", "#...#...", "..#...#."],
    "shade2": ["#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#", "#.#.#.#.", ".#.#.#.#"],
    "boldhash": [".##.##..", ".##.##..", "#######.", ".##.##..", "#######.", ".##.##..", ".##.##..", "........"],
    "boldM": ["##...##.", "###.###.", "#######.", "##.#.##.", "##...##.", "##...##.", "##...##.", "........"],
    "shade3": [".###.###", "###.###.", ".###.###", "###.###.", ".###.###", "###.###.", ".###.###", "###.###."],
    "block": ["#######.", "#######.", "#######.", "#######.", "#######.", "#######.", "#######.", "........"],
    # edge glyphs (orientation buckets 0..3: - \ | /), 2-3 px strokes running to the cell border
    "e_dash": ["........", "........", "........", "########", "########", "........", "........", "........"],
    "e_bslash": ["##......", "###.....", ".###....", "..###...", "...###..", "....###.", ".....###", "......##"],
    "e_bar": ["...##...", "...##...", "...##...", "...##...", "...##...", "...##...", "...##...", "...##..."],
    "e_slash": ["......##", ".....###", "....###.", "...###..", "..###...", ".###....", "###.....", "##......"],
}
# Density ramp: level -> equal-density variants (the first is the canonical ramp glyph).
_LEVELS = [
    ["space"],
    ["dot", "tick", "quote", "comma"],
    ["colon", "minus", "caret", "tilde"],
    ["plus", "r", "i", "v", "x", "qmark", "semi", "l"],
    ["o", "c", "equal", "n", "u", "k", "s", "z"],
    ["a", "e", "h", "pct", "lt", "gt", "P", "amp"],
    ["O", "star", "shade1", "dollar", "eight", "N", "M", "W"],
    ["hash", "at", "B", "zero"],
    ["shade2", "boldhash", "boldM"],
    ["block", "shade3"],
]
_EDGES = ["e_dash", "e_bslash", "e_bar", "e_slash"]
NLEV, NV = len(_LEVELS), 8


def _atlas():
    names = list(dict.fromkeys([g for lv in _LEVELS for g in lv] + _EDGES))
    A = np.zeros((len(names), 8, 8), np.float32)
    for k, nm in enumerate(names):
        rows = _G[nm]
        assert len(rows) == 8 and all(len(r) == 8 for r in rows), nm
        A[k] = np.array([[1.0 if ch == "#" else 0.0 for ch in r] for r in rows], np.float32)
    idx = {nm: k for k, nm in enumerate(names)}
    vt = np.zeros((NLEV, NV), np.int32)
    vn = np.zeros(NLEV, np.int32)
    for lv, gl in enumerate(_LEVELS):
        vn[lv] = min(len(gl), NV)
        for v in range(NV):
            vt[lv, v] = idx[gl[v % len(gl)]]
    return A, vt, vn, idx[_EDGES[0]]


# P layout: 0..11 palette | 12 drift | 13 period | 14 exposure | 15 level kick | 16 scramble fraction
#           17 scramble phase | 18 variety | 19 edge gain | 20 ws | 21 wm | 22 wp | 23 glyph opacity
#           24 source share under glyphs | 25 glyph gain | 26 grid-1 weight | 27 unused | 28 cx | 29 cy
NP = 32

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NLEV @NLEV@
#define NV @NV@
#define EDGE0 @EDGE0@
#define EDGE_T 0.07f

__device__ __forceinline__ float4 q4(const float4* __restrict__ b, float fx, float fy, const int w, const int h) {
    fx = fminf(fmaxf(fx - 0.5f, 0.f), (float)(w - 1));
    fy = fminf(fmaxf(fy - 0.5f, 0.f), (float)(h - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const float4 a = b[y0 * w + x0], c = b[y0 * w + x1], d = b[y1 * w + x0], e = b[y1 * w + x1];
    return make_float4(v * (u * a.x + tx * c.x) + ty * (u * d.x + tx * e.x),
                       v * (u * a.y + tx * c.y) + ty * (u * d.y + tx * e.y),
                       v * (u * a.z + tx * c.z) + ty * (u * d.z + tx * e.z),
                       v * (u * a.w + tx * c.w) + ty * (u * d.w + tx * e.w));
}
__device__ __forceinline__ float3 f3(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

// Quarter-res box downsample (mean of each 4x4 block), rgb 0..1 + luma in w.
extern "C" __global__
void asc_down(const uchar4* __restrict__ src, float4* __restrict__ Q, const int W, const int H, const int w4, const int h4)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w4 || y >= h4) return;
    const float bx = 4.f * x, by = 4.f * y;
    const float4 a = bilin(src, bx + 1.f, by + 1.f, W, H), b = bilin(src, bx + 3.f, by + 1.f, W, H);
    const float4 c = bilin(src, bx + 1.f, by + 3.f, W, H), d = bilin(src, bx + 3.f, by + 3.f, W, H);
    const float k = 1.f / (4.f * 255.f);
    const float r = (a.x + b.x + c.x + d.x) * k, g = (a.y + b.y + c.y + d.y) * k, bl = (a.z + b.z + c.z + d.z) * k;
    Q[y * w4 + x] = make_float4(r, g, bl, 0.2126f * r + 0.7152f * g + 0.0722f * bl);
}

// Sobel on the quarter-res luma -> gradient (gx, gy) in luma per quarter texel.
extern "C" __global__
void asc_grad(const float4* __restrict__ Q, float2* __restrict__ G, const int w4, const int h4)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w4 || y >= h4) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, w4 - 1), ym = max(y - 1, 0), yp = min(y + 1, h4 - 1);
    const float a = Q[ym * w4 + xm].w, b = Q[ym * w4 + x].w, c = Q[ym * w4 + xp].w;
    const float d = Q[y * w4 + xm].w,                         f = Q[y * w4 + xp].w;
    const float g = Q[yp * w4 + xm].w, h = Q[yp * w4 + x].w, i = Q[yp * w4 + xp].w;
    G[y * w4 + x] = make_float2(0.25f * ((c + 2.f * f + i) - (a + 2.f * d + g)),
                                0.25f * ((g + 2.f * h + i) - (a + 2.f * b + c)));
}

// Cell pass for one integer grid (cell n px, centred): average colour/luma (4x4 taps), structure-tensor edge vote,
// glyph choice (ramp level + equal-density variant / scramble, or edge glyph) and the inked + backing colours.
extern "C" __global__
void asc_cells(const float4* __restrict__ Q, const float2* __restrict__ G, const int w4, const int h4,
               const float n, const int hc, const int hr, const int cols, const int rows,
               const float* __restrict__ P, const int* __restrict__ VT, const int* __restrict__ VN,
               const unsigned int seed, float4* __restrict__ C0, float4* __restrict__ C1)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= cols || y >= rows) return;
    const int i = x - hc, j = y - hr;
    const float pcx = P[28] + i * n, pcy = P[29] + j * n;
    float r = 0.f, g = 0.f, b = 0.f, jxx = 0.f, jyy = 0.f, jxy = 0.f;
    #pragma unroll
    for (int bb = 0; bb < 4; ++bb) {
        #pragma unroll
        for (int aa = 0; aa < 4; ++aa) {
            const float px = (pcx + ((aa + 0.5f) * 0.25f - 0.5f) * n) * 0.25f;
            const float py = (pcy + ((bb + 0.5f) * 0.25f - 0.5f) * n) * 0.25f;
            const float4 v = q4(Q, px, py, w4, h4);
            r += v.x; g += v.y; b += v.z;
            const float fx = fminf(fmaxf(px - 0.5f, 0.f), (float)(w4 - 1)), fy = fminf(fmaxf(py - 0.5f, 0.f), (float)(h4 - 1));
            const float2 gr = G[min((int)(fy + 0.5f), h4 - 1) * w4 + min((int)(fx + 0.5f), w4 - 1)];
            jxx += gr.x * gr.x; jyy += gr.y * gr.y; jxy += gr.x * gr.y;
        }
    }
    r *= 0.0625f; g *= 0.0625f; b *= 0.0625f; jxx *= 0.0625f; jyy *= 0.0625f; jxy *= 0.0625f;
    const float L = 0.2126f * r + 0.7152f * g + 0.0722f * b;
    const float Le = clamp01(L * P[14]);

    // fill glyph: ramp level (+ beat exposure kick), stable per-cell variety, phase-keyed scramble
    const float lf = Le * (NLEV - 0.02f) + P[15];
    const int lev = min(max((int)floorf(lf), 0), NLEV - 1);
    const int nv = max(VN[lev], 1);
    const unsigned int ui = (unsigned int)(i + 65536), uj = (unsigned int)(j + 65536);
    int var = 0;
    if (hash1(ui, uj, seed) < P[18]) var = (int)(hash1(ui, uj, seed + 977u + 31u * lev) * nv);
    const float ph = P[17] + 3.f * hash1(ui, uj, seed + 5u);
    const unsigned int k = (unsigned int)floorf(ph);
    if (hash1(ui * 7u + k * 131u, uj, seed + 9u) < P[16]) var = (int)(hash1(ui, uj + 7919u * k, seed + 3u) * nv);
    var = min(max(var, 0), nv - 1);
    int glyph = VT[lev * NV + var];

    // edge glyph from the structure tensor: strength x coherence, orientation of the edge tangent
    const float tr = jxx + jyy;
    const float ds = sqrtf((jxx - jyy) * (jxx - jyy) + 4.f * jxy * jxy);
    const float E = sqrtf(tr) * ds / (tr + 1e-6f);
    if (P[19] > 0.f && E * P[19] > EDGE_T && Le > 0.06f) {
        const float th = 0.5f * atan2f(2.f * jxy, jxx - jyy + 1e-9f) + 1.5707963f;
        glyph = EDGE0 + (((int)floorf(th * 1.2732395f + 0.5f)) & 3);
    }

    // colours: source (brightened cell colour) -> mono phosphor -> palette
    const float mx = fmaxf(r, fmaxf(g, b));
    const float bn = 1.f + (fminf(1.f / fmaxf(mx, 0.12f), 5.f) - 1.f) * 0.7f;
    const float3 cs = f3(fminf(r * bn, 1.f), fminf(g * bn, 1.f), fminf(b * bn, 1.f));
    const float pb = 0.42f + 0.58f * sqrtf(Le);
    const float3 cm = f3(0.30f * pb, 1.00f * pb, 0.46f * pb);
    const float yy = (float)j / (float)max(hr, 1);
    float3 cpl = pal(Le * P[13] + P[12] + 0.18f * yy, P);
    const float pl = lum3(cpl);
    const float pk = fminf(2.5f, fmaxf(1.f, 0.55f / fmaxf(pl, 1e-3f)));
    cpl = f3(fminf(cpl.x * pk, 1.f), fminf(cpl.y * pk, 1.f), fminf(cpl.z * pk, 1.f));
    const float ws = P[20], wm = P[21], wp = P[22], gn = P[25];
    float3 ink = f3(ws * cs.x + wm * cm.x + wp * cpl.x, ws * cs.y + wm * cm.y + wp * cpl.y, ws * cs.z + wm * cm.z + wp * cpl.z);
    if (glyph >= EDGE0) { ink.x = fminf(ink.x * 1.12f + 0.04f, 1.f); ink.y = fminf(ink.y * 1.12f + 0.04f, 1.f); ink.z = fminf(ink.z * 1.12f + 0.04f, 1.f); }
    ink = f3(fminf(ink.x * gn, 1.f), fminf(ink.y * gn, 1.f), fminf(ink.z * gn, 1.f));
    // dark backing: deep palette tint (deep phosphor green in mono)
    const float3 dp = pal(P[12] + 0.5f + 0.35f * Le * P[13] + 0.12f * yy, P);
    const float3 dk = f3(0.10f * dp.x * (1.f - wm) + 0.012f * wm, 0.10f * dp.y * (1.f - wm) + 0.045f * wm,
                         0.10f * dp.z * (1.f - wm) + 0.022f * wm);
    C0[y * cols + x] = make_float4(ink.x, ink.y, ink.z, (float)glyph);
    C1[y * cols + x] = make_float4(dk.x, dk.y, dk.z, Le);
}

// Glyph coverage at bitmap coords (bx, by) in 0..8: bilinear (clamped) then sharpened to ~1 screen px of AA.
__device__ __forceinline__ float gcov(const float* __restrict__ A, const int gl, float bx, float by, const float gp) {
    bx = fminf(fmaxf(bx - 0.5f, 0.f), 7.f); by = fminf(fmaxf(by - 0.5f, 0.f), 7.f);
    const int x0 = (int)bx, y0 = (int)by, x1 = min(x0 + 1, 7), y1 = min(y0 + 1, 7);
    const float tx = bx - x0, ty = by - y0;
    const float* a = A + gl * 64;
    const float v = (1.f - ty) * ((1.f - tx) * a[y0 * 8 + x0] + tx * a[y0 * 8 + x1])
                  + ty * ((1.f - tx) * a[y1 * 8 + x0] + tx * a[y1 * 8 + x1]);
    return clamp01((v - 0.5f) * fmaxf(gp, 1.f) + 0.5f);
}

// 4K composite: both integer grids (cross-blended), glyph ink over (source share + dark backing).
extern "C" __global__
void asc_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const float* __restrict__ A,
              const int W, const int H, const float* __restrict__ P,
              const float4* __restrict__ C0a, const float4* __restrict__ C1a, const float na, const int hca, const int hra,
              const int colsa, const int rowsa,
              const float4* __restrict__ C0b, const float4* __restrict__ C1b, const float nb, const int hcb, const int hrb,
              const int colsb, const int rowsb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s8 = src[y * W + x];
    const float sr = s8.x * (1.f / 255.f), sg = s8.y * (1.f / 255.f), sb = s8.z * (1.f / 255.f);
    const float go = P[23], bg = P[24], w1 = P[26];
    float ar = 0.f, ag = 0.f, ab = 0.f;
    #pragma unroll
    for (int q = 0; q < 2; ++q) {
        const float w = q == 0 ? 1.f - w1 : w1;
        if (w <= 0.f) continue;
        const float n = q == 0 ? na : nb;
        const int hc = q == 0 ? hca : hcb, hr = q == 0 ? hra : hrb;
        const int cols = q == 0 ? colsa : colsb, rows = q == 0 ? rowsa : rowsb;
        const float4* C0 = q == 0 ? C0a : C0b;
        const float4* C1 = q == 0 ? C1a : C1b;
        const float u = (x + 0.5f - P[28]) / n + 0.5f, v = (y + 0.5f - P[29]) / n + 0.5f;
        const float fi = floorf(u), fj = floorf(v);
        const int ci = min(max((int)fi + hc, 0), cols - 1), cj = min(max((int)fj + hr, 0), rows - 1);
        const float4 c0 = C0[cj * cols + ci], c1 = C1[cj * cols + ci];
        const float cov = gcov(A, (int)(c0.w + 0.5f), (u - fi) * 8.f, (v - fj) * 8.f, n * 0.125f) * go;
        const float br = sr * bg + c1.x * (1.f - bg), bgc = sg * bg + c1.y * (1.f - bg), bb = sb * bg + c1.z * (1.f - bg);
        ar += w * (br + (c0.x - br) * cov);
        ag += w * (bgc + (c0.y - bgc) * cov);
        ab += w * (bb + (c0.z - bb) * cov);
    }
    out[y * W + x] = pack(make_float4(255.f * ar, 255.f * ag, 255.f * ab, 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class AsciiMode(Effect):
    name = "ascii_mode"
    label = "ASCII Terminal"
    stage = "stylise"
    description = ("The picture rebuilt from monospaced glyphs: a density ramp of an original bitmap font, directional "
                   "| / - \\ glyphs on edges, coloured from the source, green phosphor or a drifting palette; "
                   "glyphs jump a level and scramble on the beat.")
    params = {
        **standard_effect_params(),
        **palette_params("neon", 1.0, 0.04),
        "cell_size": num(6.0, 32.0, 12.0, "Cell size", step=0.5, unit="px", tier="regional", basic=True),
        "edge_glyphs": num(0.0, 1.0, 0.7, "Edge glyphs | / - \\", tier="regional"),
        "colour_mode": num(0.0, 1.0, 0.0, "Colour (source -> phosphor -> palette)", tier="regional", basic=True),
        "background": num(0.0, 1.0, 0.12, "Source under glyphs", tier="regional"),
        "exposure": num(0.5, 3.0, 1.25, "Exposure", step=0.05, tier="regional"),
        "variety": num(0.0, 1.0, 0.35, "Glyph variety", tier="regional"),
        "shimmer": num(0.0, 1.0, 0.12, "Idle glyph shimmer", tier="regional"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat exposure kick", step=0.05, tier="regional"),
        "scramble": num(0.0, 2.0, 1.0, "Beat glyph scramble", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        A, vt, vn, edge0 = _atlas()
        mod = raw_module(_SRC.replace("@NLEV@", str(NLEV)).replace("@NV@", str(NV)).replace("@EDGE0@", str(edge0)))
        self._kd = mod.get_function("asc_down")
        self._kg = mod.get_function("asc_grad")
        self._kc = mod.get_function("asc_cells")
        self._kp = mod.get_function("asc_comp")
        W, H = ctx.width, ctx.height
        self.w4, self.h4 = max(1, (W + 3) // 4), max(1, (H + 3) // 4)
        self.Q = cp.zeros((self.h4, self.w4, 4), cp.float32)
        self.G = cp.zeros((self.h4, self.w4, 2), cp.float32)
        self.A = cp.asarray(A.reshape(-1))
        self.VT = cp.asarray(vt.reshape(-1))
        self.VN = cp.asarray(vn)
        self.nmin = max(4, int(math.floor(4.0 * H / 1080.0)))
        self.hcm = int(math.ceil(0.5 * W / self.nmin)) + 1
        self.hrm = int(math.ceil(0.5 * H / self.nmin)) + 1
        cells = (2 * self.hcm + 1) * (2 * self.hrm + 1)
        self.C0 = cp.zeros((2, cells, 4), cp.float32)
        self.C1 = cp.zeros((2, cells, 4), cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.seed = np.uint32((int(params.get("seed", 1)) * 2654435761) & 0xFFFFFFFF) if "seed" in params else np.uint32(1013)
        self.drift = 0.0
        self.sphase = 0.0
        self.scr = 0.0
        self.pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)

        # host-integrated phases: the beat adds to rates, never to positions
        self.scr = max(pulse, self.scr * math.exp(-dt / 0.45)) if dt > 0 else max(self.scr, pulse)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * (1.0 + 0.8 * pulse) * dt, 10.0)
        self.sphase = math.fmod(self.sphase + (1.6 + 9.0 * self.scr) * dt, 4096.0)
        pc = np.asarray(palette_coeffs(p["palette"]), np.float32)
        ap = 1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0
        self.pal = pc if self.pal is None else self.pal + (pc - self.pal) * ap

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        m = _smooth(0.0, 1.0, a)
        W, H = self.ctx.width, self.ctx.height
        seff = max(float(p["cell_size"]) * H / 1080.0 * (0.6 + 0.4 * m), float(self.nmin))
        n0 = max(int(math.floor(seff)), self.nmin)
        w1 = _smooth(0.3, 0.7, seff - n0)
        cm = min(max(float(p["colour_mode"]), 0.0), 1.0)
        if cm < 0.5:
            ws, wm, wp = 1.0 - 2.0 * cm, 2.0 * cm, 0.0
        else:
            ws, wm, wp = 0.0, 2.0 - 2.0 * cm, 2.0 * cm - 1.0

        hp = self.host
        hp[0:12] = self.pal
        hp[12] = self.drift
        hp[13] = float(p["palette_period"])
        hp[14] = float(p["exposure"])
        hp[15] = 1.6 * float(p["beat_kick"]) * pulse * m
        hp[16] = min(1.0, 0.35 * float(p["shimmer"]) + 0.6 * float(p["scramble"]) * self.scr) * m
        hp[17] = self.sphase
        hp[18] = float(p["variety"])
        hp[19] = float(p["edge_glyphs"])
        hp[20], hp[21], hp[22] = ws, wm, wp
        hp[23] = min(1.0, 3.0 * m)
        hp[24] = 1.0 + (float(p["background"]) - 1.0) * min(1.0, 1.6 * m)
        hp[25] = 1.0 + 0.3 * float(p["beat_kick"]) * pulse * m
        hp[26] = w1
        hp[28] = 0.5 * W
        hp[29] = 0.5 * H
        self.P.set(hp)

        iW, iH, w4, h4 = np.int32(W), np.int32(H), np.int32(self.w4), np.int32(self.h4)
        launch_2d(self._kd, self.w4, self.h4, (src, self.Q, iW, iH, w4, h4))
        launch_2d(self._kg, self.w4, self.h4, (self.Q, self.G, w4, h4))
        grids = []
        for q, n in enumerate((n0, n0 + 1)):
            hc = min(int(math.ceil(0.5 * W / n)) + 1, self.hcm)
            hr = min(int(math.ceil(0.5 * H / n)) + 1, self.hrm)
            cols, rows = 2 * hc + 1, 2 * hr + 1
            grids.append((np.float32(n), np.int32(hc), np.int32(hr), np.int32(cols), np.int32(rows)))
            if (q == 0 and w1 < 1.0) or (q == 1 and w1 > 0.0):
                launch_2d(self._kc, cols, rows,
                          (self.Q, self.G, w4, h4, np.float32(n), np.int32(hc), np.int32(hr), np.int32(cols),
                           np.int32(rows), self.P, self.VT, self.VN, self.seed, self.C0[q], self.C1[q]))
        ga, gb = grids
        launch_2d(self._kp, W, H, (src, out, self.A, iW, iH, self.P, self.C0[0], self.C1[0], *ga,
                                   self.C0[1], self.C1[1], *gb))
