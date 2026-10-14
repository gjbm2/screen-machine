"""Neon signs: a wall of text-free neon tube signs (rings, hearts, shooting stars, chase arrows, lightning bolts,
waves, a palm against a sunset, a cocktail glass, an eye, a crescent moon) glowing on brick, tile or a dark wall.

Source: Original (inspired by https://en.wikipedia.org/wiki/Neon_sign)
Author: smfx (original)
License: MIT
license_class: permissive

Written from scratch.  Every sign is a set of 2D distance-to-stroke fields (segments, arcs, rings, closed polylines and
the outlines of union/difference shapes), split into a main tube, an accent tube (its own colour and animation:
rotating spokes, a beating inner heart, chasing chevrons, scrolling waves, a sunset, an olive, a wandering iris,
twinkling sparkles) and an optional framing tube.  Signs live on a square cell grid (one per cell, cell centres on
integers so a centred sign sits in the middle); each pixel evaluates only the 4 cells whose centres surround it, and a
window that falls to 0 at one cell from the centre keeps glow continuous as the evaluated set changes.

Each tube: anti-aliased glass body with a rounded profile, a white-hot core, a coloured halo (exp falloff, size knob),
a wider wash that lights the wall (albedo x light, so mortar and grout stay dark) and a tighter glossy reflection that
only glazed tile shows.  Unlit signs show their tinted glass tubes catching the light of their neighbours.  The wall
morphs brick (running bond, per-brick colour, burnt bricks, grit, bevel, recessed mortar) -> white subway tile (glaze,
bright bevel, dark grout, gloss) -> dark plaster.  Linear light, exp tone map, gamma 2.2.

Flicker comes from a hash of integrated time, smoothed (1-D value noise, lattice periodic in 4096 so the host wraps the
phases seamlessly): a buzz (small dips), warm-up stutter while a sign fades on, and failing tubes (a fraction set by
flicker_amount) that drop out and stutter for a few seconds at a time, either the whole sign or just its accent.

layout_symmetry 0 -> 1: random jitter, tilt and size -> an orderly grid mirrored about the centre column; each cell
switches to its mirror twin at its own threshold, blinking off for an instant as it does (no visible jump).

Macros: energy -> fraction of lit signs (slewed, so signs fade on with warm-up stutter), chase speed (0.3 -> 1.8
groups/s) and chase depth, flicker rates (buzz, stutter, failing), halo size and gain, wall wash, sign animation rate,
camera sway.  pulse (k = pulse (0.4 + 0.6 e)) -> flashes the current chase group (the signs on the diagonal the chase is
on), +25 % halo, and kicks the chase and animation rates.  speed scales every rate; palette: each sign picks
pal(hash * period + drift), its accent and frame sit at fixed offsets along the palette; intensity is output gain.
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

NT = 10                                   # sign designs

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 cell size (world) | 15 symmetry | 16 tube radius | 17 aa (world/px)
#  18 halo width | 19 wash width | 20 halo gain | 21 wash gain | 22 wall material | 23 buzz time | 24 stutter time
#  25 failing time | 26 look time | 27 anim cycles | 28 chase phase | 29 chase depth | 30 flash gain | 31 lit frac
#  32 flicker amount | 33 failing fraction | 34 frame probability | 35 sway x | 36 sway y | 37 intensity | 38 ambient
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NT %(nt)d
#define TAU 6.2831853f

__constant__ float ARROW[14] = {-0.06f, 0.1f, 0.16f, 0.1f, 0.16f, 0.3f, 0.5f, 0.f, 0.16f, -0.3f, 0.16f, -0.1f,
                                -0.06f, -0.1f};
__constant__ float BOLT[12] = {0.12f, 0.5f, -0.26f, -0.04f, -0.02f, -0.04f, -0.14f, -0.5f, 0.26f, 0.08f,
                               0.02f, 0.08f};
// palm fronds: initial heading, bend radius, sweep, bend direction (+1 clockwise)
__constant__ float FROND[20] = {0.9f, 0.23f, 2.3f, 1.f,   0.2f, 0.28f, 1.6f, 1.f,   2.2415927f, 0.23f, 2.3f, -1.f,
                                2.9415927f, 0.28f, 1.6f, -1.f,   1.35f, 0.36f, 0.65f, 1.f};

__device__ __forceinline__ float sseg(float px, float py, float ax, float ay, float bx, float by) {
    const float pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
    const float h = clamp01((pax * bax + pay * bay) / (bax * bax + bay * bay));
    const float dx = pax - bax * h, dy = pay - bay * h;
    return sqrtf(dx * dx + dy * dy);
}
__device__ __forceinline__ float sring(float px, float py, float cx, float cy, float r) {
    const float dx = px - cx, dy = py - cy;
    return fabsf(sqrtf(dx * dx + dy * dy) - r);
}
// arc of radius r about (cx, cy), centred on angle ac with half-aperture ha (0 < ha < pi)
__device__ float sarc(float px, float py, float cx, float cy, float r, float ac, float ha) {
    float s, c, sh, ch;
    __sincosf(ac, &s, &c);
    __sincosf(ha, &sh, &ch);
    const float x = px - cx, y = py - cy;
    const float xr = x * c + y * s, yr = fabsf(-x * s + y * c);
    if (xr * sh > yr * ch) return fabsf(sqrtf(xr * xr + yr * yr) - r);
    const float dx = xr - r * ch, dy = yr - r * sh;
    return sqrtf(dx * dx + dy * dy);
}
__device__ float spoly(float px, float py, const float* v, int n) {
    float d = 1e9f;
    for (int i = 0; i < n; ++i) {
        const int j = (i + 1 == n) ? 0 : i + 1;
        d = fminf(d, sseg(px, py, v[2 * i], v[2 * i + 1], v[2 * j], v[2 * j + 1]));
    }
    return d;
}
__device__ __forceinline__ float sbox(float px, float py, float bx, float by) {
    const float qx = fabsf(px) - bx, qy = fabsf(py) - by;
    const float ox = fmaxf(qx, 0.f), oy = fmaxf(qy, 0.f);
    return sqrtf(ox * ox + oy * oy) + fminf(fmaxf(qx, qy), 0.f);
}
// signed heart: a diamond (square at 45 deg) plus two discs on its upper edges; ~[-0.51, 0.51] x [-0.47, 0.47]
__device__ float heart(float x, float y) {
    const float yy = y + 0.044f;
    float d = sbox((x + yy) * 0.70710678f, (yy - x) * 0.70710678f, 0.3f, 0.3f);
    const float dx = fabsf(x) - 0.212f, dy = y - 0.168f;
    return fminf(d, sqrtf(dx * dx + dy * dy) - 0.3f);
}
// five-pointed star outline (outer radius R, inner r) by folding into one mirrored sector
__device__ float star5(float x, float y, float R, float r) {
    float a = atan2f(x, y);
    const float sec = 1.2566371f;
    a = fabsf(a - sec * floorf(a / sec + 0.5f));
    const float l = sqrtf(x * x + y * y);
    float s, c;
    __sincosf(a, &s, &c);
    return sseg(l * s, l * c, 0.f, R, r * 0.58778525f, r * 0.80901699f);
}
__device__ __forceinline__ float beat(float f, float p) {        // smooth periodic bump, f in cycles
    return powf(0.5f + 0.5f * __cosf(TAU * f), p);
}

// (main, accent, accent gain) stroke distances in sign-local units (sign spans ~[-0.5, 0.5]^2, y up).
// ac: the sign's animation phase in cycles (only integer multiples are used, so wrapping it is seamless)
__device__ float3 sign_sd(int type, float x, float y, float ac, float lx, float ly) {
    float dm = 1e3f, da = 1e3f, ga = 1.f;
    switch (type) {
    case 0: {                                                     // ring: two rings + rotating spokes
        const float l = sqrtf(x * x + y * y);
        dm = fminf(fabsf(l - 0.46f), fabsf(l - 0.13f));
        const float sec = TAU / 12.f;
        float a = atan2f(y, x) + 2.f * ac * sec;
        a -= sec * floorf(a / sec + 0.5f);
        float s, c;
        __sincosf(a, &s, &c);
        da = sseg(l * c, l * s, 0.22f, 0.f, 0.36f, 0.f);
        break; }
    case 1: {                                                     // heart, beating inner heart
        dm = fabsf(heart(x * (1.f / 0.92f), y * (1.f / 0.92f))) * 0.92f;
        da = fabsf(heart(x * (1.f / 0.54f), (y + 0.035f) * (1.f / 0.54f))) * 0.54f;
        ga = 0.25f + 0.75f * beat(ac * 2.f, 4.f);
        break; }
    case 2: {                                                     // shooting star
        dm = star5(x - 0.14f, y - 0.13f, 0.34f, 0.15f);
        da = fminf(sseg(x, y, -0.1f, 0.02f, -0.46f, -0.25f), sseg(x, y, -0.24f, 0.12f, -0.5f, -0.075f));
        ga = 0.45f + 0.55f * beat(ac * 3.f, 2.f);
        break; }
    case 3: {                                                     // arrow + chasing chevrons
        dm = spoly(x, y, ARROW, 7);
        float best = 1e3f, bi = 0.f;
        #pragma unroll
        for (int i = 0; i < 3; ++i) {
            const float x0 = -0.46f + 0.14f * i;
            const float d = fminf(sseg(x, y, x0 - 0.07f, 0.17f, x0 + 0.03f, 0.f),
                                  sseg(x, y, x0 - 0.07f, -0.17f, x0 + 0.03f, 0.f));
            if (d < best) { best = d; bi = (float)i; }
        }
        da = best;
        ga = 0.1f + 0.9f * beat(ac * 2.f - bi * (1.f / 3.f), 6.f);
        break; }
    case 4: {                                                     // lightning bolt with an inner spark line
        dm = spoly(x, y, BOLT, 6);
        da = fminf(sseg(x, y, 0.07f, 0.34f, -0.1f, 0.04f), sseg(x, y, -0.08f, -0.3f, 0.12f, 0.02f));
        ga = 0.2f + 0.8f * beat(ac * 4.f, 8.f);
        break; }
    case 5: {                                                     // three scrolling waves
        float d0 = 1e3f, d1 = 1e3f;
        const float A = 0.075f, k = 11.f;
        const float xc = fminf(fmaxf(x, -0.44f), 0.44f);
        #pragma unroll
        for (int i = 0; i < 3; ++i) {
            const float yc = 0.27f * (i - 1);
            float s, c;
            __sincosf(k * xc - TAU * ac * (i == 1 ? -1.f : 1.f) + 1.7f * i, &s, &c);
            const float dy = (y - yc - A * s) * rsqrtf(1.f + A * A * k * k * c * c), dx = x - xc;
            const float d = sqrtf(dx * dx + dy * dy);
            if (i == 1) d1 = d; else d0 = fminf(d0, d);
        }
        dm = d0; da = d1;
        break; }
    case 6: {                                                     // palm against a striped sunset
        da = fminf(sarc(x, y, 0.06f, -0.2f, 0.36f, 1.5707963f, 1.5707963f), sseg(x, y, -0.46f, -0.2f, 0.48f, -0.2f));
        da = fminf(da, fminf(sseg(x, y, -0.22f, -0.1f, 0.34f, -0.1f), sseg(x, y, -0.16f, 0.f, 0.28f, 0.f)));
        const float cx = -0.06f, cy = 0.24f;
        float d = fminf(sseg(x, y, -0.2f, -0.48f, -0.14f, -0.12f), sseg(x, y, -0.14f, -0.12f, cx, cy));
        #pragma unroll
        for (int i = 0; i < 5; ++i) {
            const float R = FROND[4 * i + 1], sw = FROND[4 * i + 2], sg = FROND[4 * i + 3];
            const float phi = FROND[4 * i] + 0.06f * __sinf(TAU * ac + 1.3f * i);
            float s, c;
            __sincosf(phi, &s, &c);
            const float th0 = phi + sg * 1.5707963f;
            d = fminf(d, sarc(x, y, cx + sg * R * s, cy - sg * R * c, R, th0 - sg * 0.5f * sw, 0.5f * sw));
        }
        dm = d;
        break; }
    case 7: {                                                     // cocktail glass, olive on a pick
        dm = fminf(fminf(sseg(x, y, -0.36f, 0.4f, 0.36f, 0.4f), sseg(x, y, -0.36f, 0.4f, 0.f, 0.02f)),
                   fminf(sseg(x, y, 0.36f, 0.4f, 0.f, 0.02f), sseg(x, y, 0.f, 0.02f, 0.f, -0.4f)));
        dm = fminf(dm, sseg(x, y, -0.2f, -0.42f, 0.2f, -0.42f));
        da = fminf(sring(x, y, 0.02f, 0.15f, 0.06f), sseg(x, y, -0.02f, 0.11f, 0.2f, 0.52f));
        da = fminf(da, sseg(x, y, -0.18f, 0.27f, 0.18f, 0.27f));
        ga = 0.55f + 0.45f * beat(ac, 2.f);
        break; }
    case 8: {                                                     // eye with lashes, wandering iris
        dm = fminf(sarc(x, y, 0.f, -0.306f, 0.586f, 1.5707963f, 1.0218f),
                   sarc(x, y, 0.f, 0.306f, 0.586f, -1.5707963f, 1.0218f));
        #pragma unroll
        for (int i = 0; i < 5; ++i) {
            float s, c;
            __sincosf(1.5707963f + 0.27f * (i - 2), &s, &c);
            dm = fminf(dm, sseg(x, y, 0.64f * c, -0.306f + 0.64f * s, 0.75f * c, -0.306f + 0.75f * s));
        }
        const float ex = x - lx, ey = y - ly, el = sqrtf(ex * ex + ey * ey);
        da = fminf(fabsf(el - 0.16f), fmaxf(el - 0.045f, 0.f));
        break; }
    default: {                                                    // crescent moon + twinkling sparkles
        const float c1 = sqrtf(x * x + y * y) - 0.42f;
        const float dx = x - 0.2f, dy = y - 0.13f;
        dm = fabsf(fmaxf(c1, -(sqrtf(dx * dx + dy * dy) - 0.35f)));
        float best = 1e3f, bi = 0.f;
        #pragma unroll
        for (int i = 0; i < 3; ++i) {
            const float sx = i == 0 ? 0.28f : (i == 1 ? 0.45f : 0.1f);
            const float sy = i == 0 ? 0.22f : (i == 1 ? 0.42f : 0.0f);
            const float r = i == 0 ? 0.085f : (i == 1 ? 0.06f : 0.05f);
            const float d = fminf(sseg(x, y, sx - r, sy, sx + r, sy), sseg(x, y, sx, sy - r, sx, sy + r));
            if (d < best) { best = d; bi = (float)i; }
        }
        da = best;
        ga = 0.2f + 0.8f * beat(ac * 2.f + bi * 0.33f, 3.f);
        break; }
    }
    return make_float3(dm, da, ga);
}

// smooth 1-D value noise along integrated time t, lattice periodic in 4096 (the host wraps t at 4096)
__device__ __forceinline__ float n1(float t, unsigned int id, unsigned int salt, float sharp) {
    const float f = floorf(t);
    const int i = (int)f;
    float a = t - f;
    a = sharp > 0.f ? smooth(0.5f - 0.5f * sharp, 0.5f + 0.5f * sharp, a) : a * a * (3.f - 2.f * a);
    const float h0 = hash1((unsigned int)i & 4095u, id, salt), h1 = hash1((unsigned int)(i + 1) & 4095u, id, salt);
    return h0 + (h1 - h0) * a;
}

// vivid neon colour from the palette (dark phases fold to the bright side), returned in linear light
__device__ float3 neon_col(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.25f);
    c.x *= k; c.y *= k; c.z *= k;
    const float l = (c.x + c.y + c.z) * 0.33333f;
    c.x = clamp01(l + 1.3f * (c.x - l)); c.y = clamp01(l + 1.3f * (c.y - l)); c.z = clamp01(l + 1.3f * (c.z - l));
    return make_float3(c.x * c.x, c.y * c.y, c.z * c.z);
}

struct Acc { float3 E, L, S, tint; float cov, prof; };

__device__ __forceinline__ void tube(Acc& A, float d, float3 c, float B, float win, float vis,
                                     const float* __restrict__ P) {
    const float tw = P[16], aa = P[17];
    const float m = smooth(tw + aa, tw - aa, d) * vis;
    const float x = fminf(d / tw, 1.f);
    const float prof = sqrtf(fmaxf(1.f - x * x, 0.f));
    if (m > A.cov) { A.cov = m; A.tint = c; A.prof = prof; }
    if (B <= 1e-4f) return;
    const float core = __expf(-x * x * 5.f) * m;
    const float halo = __expf(-d / P[18]) * win;
    const float wash = __expf(-d / P[19]) * win;
    const float gl = __expf(-d / (2.5f * P[18])) * win;
    const float eb = B * (1.5f * prof * m + P[20] * halo);
    const float wc = 1.1f * B * core;
    A.E.x += c.x * eb + wc * (0.6f + 0.4f * c.x);
    A.E.y += c.y * eb + wc * (0.6f + 0.4f * c.y);
    A.E.z += c.z * eb + wc * (0.6f + 0.4f * c.z);
    const float wl = B * wash * P[21];
    A.L.x += c.x * wl; A.L.y += c.y * wl; A.L.z += c.z * wl;
    A.S.x += c.x * B * gl; A.S.y += c.y * B * gl; A.S.z += c.z * B * gl;
}

// running-bond pattern: distance to the nearest joint (world units) and the cell's integer coords
__device__ __forceinline__ float bond(float u, float v, float bw, float bh, int& ci, int& ri) {
    const float ry = v / bh, row = floorf(ry);
    const float rx = u / bw + 0.5f * (row - 2.f * floorf(row * 0.5f));
    const float col = floorf(rx);
    const float fx = (rx - col) * bw, fy = (ry - row) * bh;
    ci = (int)col; ri = (int)row;
    return fminf(fminf(fx, bw - fx), fminf(fy, bh - fy));
}

// wall albedo (linear) and gloss: brick (0) -> subway tile (1) -> dark plaster (2)
__device__ float3 wall_albedo(float u, float v, float mat, unsigned int seed, float& gloss) {
    const float wb = 1.f - smooth(0.f, 1.f, mat), wd = smooth(1.f, 2.f, mat), wt = fmaxf(1.f - wb - wd, 0.f);
    float3 a = make_float3(0.f, 0.f, 0.f);
    gloss = 0.f;
    if (wb > 0.f) {
        const float mw = 0.0085f;
        int ci, ri;
        const float e = bond(u, v, 0.25f, 0.083f, ci, ri);
        const float mo = 1.f - smooth(0.6f * mw, mw, e);
        const unsigned int hc = (unsigned int)(ci + 65536), hr = (unsigned int)(ri + 65536);
        const float hb = hash1(hc, hr, seed + 21u), hb2 = hash1(hc, hr, seed + 22u);
        const float grit = (0.72f + 0.56f * vnoise(u * 55.f, v * 55.f, seed + 31u))
                         * (0.8f + 0.4f * vnoise(u * 14.f, v * 14.f, seed + 32u));
        const float s = (0.55f + 0.7f * hb) * grit * (0.72f + 0.28f * smooth(mw, 2.6f * mw, e));
        const float burnt = hb2 < 0.22f ? 1.f : 0.f;
        const float br = (0.20f + (0.09f - 0.20f) * burnt) * s, bg = (0.075f + (0.045f - 0.075f) * burnt) * s;
        const float bb = (0.05f + (0.042f - 0.05f) * burnt) * s;
        const float mg = 0.045f * (0.8f + 0.4f * grit);
        a.x += wb * (br + (mg - br) * mo);
        a.y += wb * (bg + (0.95f * mg - bg) * mo);
        a.z += wb * (bb + (0.85f * mg - bb) * mo);
        gloss += wb * 0.03f * (1.f - mo);
    }
    if (wt > 0.f) {
        const float gw = 0.0055f;
        int ci, ri;
        const float e = bond(u, v, 0.15f, 0.075f, ci, ri);
        const float mo = 1.f - smooth(0.6f * gw, gw, e);
        const unsigned int hc = (unsigned int)(ci + 65536), hr = (unsigned int)(ri + 65536);
        const float hb = hash1(hc, hr, seed + 23u), hb2 = hash1(hc, hr, seed + 24u);
        const float rim = 1.f + 0.35f * (1.f - smooth(gw, 2.8f * gw, e));
        const float s = (0.9f + 0.15f * hb) * rim * (0.95f + 0.1f * vnoise(u * 40.f, v * 40.f, seed + 33u));
        a.x += wt * (0.42f * s + (0.025f - 0.42f * s) * mo);
        a.y += wt * (0.44f * s + (0.025f - 0.44f * s) * mo);
        a.z += wt * (0.43f * s + (0.027f - 0.43f * s) * mo);
        gloss += wt * (1.f - mo) * (0.3f + 0.3f * hb2) * rim;
    }
    if (wd > 0.f) {
        const float f = 0.55f + 0.9f * fbm(u * 5.f, v * 5.f, seed + 41u);
        a.x += wd * 0.022f * f; a.y += wd * 0.021f * f; a.z += wd * 0.025f * f;
        gloss += wd * 0.06f;
    }
    return a;
}

extern "C" __global__
void neon_signs(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh + P[35], v = (hh - y - 0.5f) / hh + P[36];
    const float cs = P[14], sym = P[15], fr = 1.f - sym;
    const float qx = u / cs, qy = v / cs;
    const int bx = (int)floorf(qx), by = (int)floorf(qy);
    Acc A;
    A.E = A.L = A.S = A.tint = make_float3(0.f, 0.f, 0.f);
    A.cov = 0.f; A.prof = 0.f;
    const float fa = P[32];

    for (int k = 0; k < 4; ++k) {
        const int ci = bx + (k & 1), cj = by + (k >> 1);
        const float dcx = qx - ci, dcy = qy - cj;
        const float win = smooth(1.f, 0.78f, fmaxf(fabsf(dcx), fabsf(dcy)));
        if (win <= 0.f) continue;
        const unsigned int uj = (unsigned int)(cj + 4096);
        // mirror switch: each cell becomes its mirror twin at its own threshold, blinking off as it does
        const float hm = 0.05f + 0.9f * hash1((unsigned int)(ci + 4096), uj, seed + 7u);
        const bool tw = hm < sym;
        const int ie = tw ? abs(ci) : ci;
        const float fl = (tw && ci < 0) ? -1.f : 1.f;
        const float vis = smooth(0.f, 0.035f, fabsf(sym - hm));
        const unsigned int ui = (unsigned int)(ie + 4096), sid = ui * 7919u + uj;
        const float h1 = hash1(ui, uj, seed + 1u), h2 = hash1(ui, uj, seed + 2u), h3 = hash1(ui, uj, seed + 3u);
        const float h4 = hash1(ui, uj, seed + 4u), h5 = hash1(ui, uj, seed + 5u), h6 = hash1(ui, uj, seed + 6u);
        const float h7 = hash1(ui, uj, seed + 8u), h8 = hash1(ui, uj, seed + 9u), h9 = hash1(ui, uj, seed + 10u);
        const int type = min((int)(h1 * NT), NT - 1);
        // placement (jitter, tilt and size variety fade out with symmetry)
        const float jx = (h3 - 0.5f) * 0.2f * fr * fl, jy = (h4 - 0.5f) * 0.14f * fr;
        const float rot = (h5 - 0.5f) * 0.5f * fr * fl;
        const float sz = cs * 0.72f * (1.f + (h6 - 0.5f) * 0.28f * fr);
        const float wx = (dcx - jx) * cs, wy = (dcy - jy) * cs;
        float sr, cr;
        __sincosf(rot, &sr, &cr);
        const float px = fl * (wx * cr + wy * sr) / sz, py = (-wx * sr + wy * cr) / sz;

        // brightness: lit fraction (fade with warm-up stutter), buzz, chase group, beat flash, failing tubes
        const float lv = smooth(h7 - 0.06f, h7 + 0.06f, P[31]);
        float B = 0.f, fm = 1.f;
        if (lv > 0.f) {
            const float warm = 1.f - (1.f - lv) * (0.55f + 0.45f * fa)
                                     * smooth(0.3f, 0.7f, n1(2.f * P[24] + h1 * 97.f, sid, 4u, 0.f));
            const float buzz = 1.f - fa * 0.28f * smooth(0.5f, 1.f, n1(P[23] + h2 * 131.f, sid, 1u, 0.f));
            const int g = ((abs(ci) + cj) %% 4 + 4) %% 4;
            float dd = P[28] - (float)g;
            dd -= 4.f * floorf(dd * 0.25f + 0.5f);
            const float bump = __expf(-dd * dd * 2.5f);
            B = lv * warm * buzz * vis * (1.f + P[29] * (bump - 0.3f)) * (1.f + P[30] * bump);
            if (h8 < P[33]) {
                const float drop = smooth(0.56f, 0.68f, n1(P[25] + h3 * 61.f, sid, 2u, 0.f));
                const float st = smooth(0.35f, 0.65f, n1(P[24] + h4 * 23.f, sid, 3u, 0.f));
                fm = 1.f - drop * (0.94f - 0.9f * st * st);
            }
        }
        const bool fail_all = h9 < 0.5f;
        float ac = P[27] + h5;
        const float lx = 0.11f * (2.f * n1(P[26] + h6 * 37.f, sid, 5u, 0.4f) - 1.f);
        const float ly = 0.05f * (2.f * n1(P[26] + h2 * 53.f, sid, 6u, 0.4f) - 1.f);
        const float3 sd = sign_sd(type, px, py, ac, lx, ly);

        const float t0 = h2 * P[12] + P[13];
        const float Bm = fail_all ? B * fm : B;
        tube(A, sd.x * sz, neon_col(t0, P), Bm, win, vis, P);
        if (sd.y < 50.f) tube(A, sd.y * sz, neon_col(t0 + 0.22f + 0.3f * h3, P), B * fm * sd.z, win, vis, P);
        if (h9 * 2.f - (fail_all ? 0.f : 1.f) < P[34]) {
            const float df = fabsf(sbox(px, py, 0.46f, 0.44f) - 0.12f) * sz;
            tube(A, df, neon_col(t0 + 0.55f, P), Bm, win, vis, P);
        }
    }

    float gloss;
    const float3 alb = wall_albedo(u, v, P[22], seed, gloss);
    const float amb = P[38] * (1.f + 0.3f * v);
    float cx = alb.x * (0.85f * amb + A.L.x) + gloss * A.S.x;
    float cy = alb.y * (0.92f * amb + A.L.y) + gloss * A.S.y;
    float cz = alb.z * (1.10f * amb + A.L.z) + gloss * A.S.z;
    if (A.cov > 0.f) {                                            // glass tubes (seen when unlit)
        const float gp = 0.35f + 0.65f * A.prof, hl = 0.02f * powf(A.prof, 8.f);
        const float gx = (0.1f + 0.3f * A.tint.x) * (5.f * amb + 0.9f * A.L.x) * gp + hl;
        const float gy = (0.1f + 0.3f * A.tint.y) * (5.f * amb + 0.9f * A.L.y) * gp + hl;
        const float gz = (0.1f + 0.3f * A.tint.z) * (5.f * amb + 0.9f * A.L.z) * gp + hl;
        const float m = 0.92f * A.cov;
        cx += (gx - cx) * m; cy += (gy - cy) * m; cz += (gz - cz) * m;
    }
    cx += A.E.x; cy += A.E.y; cz += A.E.z;
    const float r2 = ((x + 0.5f) / W - 0.5f) * ((x + 0.5f) / W - 0.5f) + ((y + 0.5f) / H - 0.5f) * ((y + 0.5f) / H - 0.5f);
    const float vg = 1.f - 0.55f * r2;
    const float g = 255.f * P[37];
    out[y * W + x] = pack(make_float4(g * __powf(1.f - __expf(-cx * vg * 1.1f), 0.4545f),
                                      g * __powf(1.f - __expf(-cy * vg * 1.1f), 0.4545f),
                                      g * __powf(1.f - __expf(-cz * vg * 1.1f), 0.4545f), 255.f));
}
""" % {"nt": NT}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class NeonSigns(Source):
    name = "neon_signs"
    label = "Neon signs"
    description = ("A wall of text-free neon tube signs (rings, hearts, stars, arrows, bolts, waves, a palm, a "
                   "cocktail, an eye, a moon) with hot cores, coloured halos and glow on brick or tile, warming up, "
                   "buzzing, chasing and flashing on the beat.")
    tags = {"family": "retro", "dims": "2d", "toolkit": "none", "technique": ["sdf", "light", "retro"],
            "moods": ["retro", "playful", "warm"], "style": "lively", "energy": [0.2, 0.9]}
    params = _order({
        "sign_density": num(0.0, 1.0, 0.45, "Sign density (few big -> many small)", tier="regional", basic=True),
        "wall_material": num(0.0, 2.0, 0.0, "Wall (brick -> tile -> dark)", tier="regional"),
        "tube_thickness": num(0.0, 1.0, 0.45, "Tube thickness", tier="regional"),
        "halo_size": num(0.0, 1.0, 0.5, "Halo size", tier="regional"),
        "flicker_amount": num(0.0, 1.0, 0.4, "Flicker and failing tubes", tier="regional"),
        "layout_symmetry": num(0.0, 1.0, 0.2, "Layout (scattered -> mirrored grid)", tier="regional"),
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.02),
    }, ["sign_density", "wall_material", "tube_thickness", "halo_size", "flicker_amount", "layout_symmetry",
        "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k_draw = raw_module(_SRC, _OPTIONS).get_function("neon_signs")
        self.Ph = np.zeros(40, np.float32)
        self.P = cp.zeros(40, cp.float32)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed)
        self.tb, self.tw, self.tf, self.tl = (float(v) for v in rng.uniform(0, 4096, 4))
        self.ac = float(rng.uniform(0, 1))
        self.chase = float(rng.uniform(0, 4))
        self.sw1, self.sw2 = (float(v) for v in rng.uniform(0, 2 * math.pi, 2))
        self.drift = 0.0
        self.lit = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        k = pulse * (0.4 + 0.6 * e)
        fa = min(max(float(p["flicker_amount"]), 0.0), 1.0)

        # lit fraction, slewed so signs fade on (with warm-up stutter) rather than pop
        target = 0.28 + 0.8 * _ss(0.05, 0.9, e)
        if self.lit is None or dt <= 0:
            self.lit = target if self.lit is None else self.lit
        else:
            self.lit += (target - self.lit) * (1.0 - math.exp(-dt / 0.9))

        # host-integrated phases (kicks add to rates only)
        sd = spd * dt
        self.tb = _wrap(self.tb + (3.0 + 9.0 * e) * sd, 4096.0)
        self.tw = _wrap(self.tw + (6.0 + 12.0 * e) * sd, 4096.0)
        self.tf = _wrap(self.tf + (0.2 + 0.4 * e) * sd, 4096.0)
        self.tl = _wrap(self.tl + 0.5 * (0.6 + 0.8 * e) * sd, 4096.0)
        self.ac = _wrap(self.ac + (0.2 + 0.45 * e) * (1.0 + 0.6 * k) * sd, 1.0)
        self.chase = _wrap(self.chase + (0.3 + 1.5 * e) * (1.0 + 0.8 * k) * sd, 4.0)
        self.sw1 = _wrap(self.sw1 + 2 * math.pi * 0.043 * sd, 2 * math.pi)
        self.sw2 = _wrap(self.sw2 + 2 * math.pi * 0.031 * sd, 2 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * sd, 10.0)

        cs = 1.0 + (0.4 - 1.0) * min(max(float(p["sign_density"]), 0.0), 1.0)
        rel = cs / 0.73
        tt = min(max(float(p["tube_thickness"]), 0.0), 1.0)
        hs = min(max(float(p["halo_size"]), 0.0), 1.0)

        P = self.Ph
        sp = self.session_palette()
        P[0:12] = (np.asarray(sp, np.float32).reshape(-1)[:12] if sp is not None else palette_coeffs(p["palette"]))
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = cs
        P[15] = min(max(float(p["layout_symmetry"]), 0.0), 1.0)
        P[16] = (0.006 + 0.014 * tt) * math.sqrt(rel)
        P[17] = 0.75 * 2.0 / self.h
        P[18] = (0.012 + 0.05 * hs) * (0.85 + 0.3 * e) * math.sqrt(rel)
        P[19] = (0.07 + 0.16 * hs) * rel
        P[20] = (0.45 + 0.35 * e) * (1.0 + 0.25 * k)
        P[21] = 0.55 + 0.25 * e
        P[22] = min(max(float(p["wall_material"]), 0.0), 2.0)
        P[23], P[24], P[25], P[26] = self.tb, self.tw, self.tf, self.tl
        P[27] = self.ac
        P[28] = self.chase
        P[29] = 0.12 + 0.4 * e
        P[30] = 1.8 * k
        P[31] = self.lit
        P[32] = fa
        P[33] = 0.04 + 0.25 * fa
        P[34] = 0.35
        amp = 0.5 + e
        P[35] = 0.02 * amp * math.sin(self.sw1)
        P[36] = 0.012 * amp * math.sin(self.sw2)
        P[37] = float(p["intensity"])
        P[38] = 0.018
        self.P.set(P)

        launch_2d(self.k_draw, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P,
                                                np.uint32(self.seed)))
        present(self.ctx, self.buf, out)
