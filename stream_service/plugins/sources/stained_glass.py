"""Stained glass: animated Voronoi cells with exact borders, leaded and backlit (or molten cracks in cooling lava).

Source: https://www.shadertoy.com/view/ldl3W8 ("Voronoi - distances", Inigo Quilez, 2013)
        https://iquilezles.org/articles/voronoilines/ (the two-pass exact distance-to-border algorithm)
        https://archive.blender.org/lists/bf-blender-cvs/2021-July/161829.html (Blender commit 3848507511a)
Author: Inigo Quilez (Voronoi distance-to-border algorithm); smfx (scene, shading, animation)
License: MIT
license_class: permissive
Licence evidence: Blender's Cycles port of this exact algorithm (intern/cycles/kernel/svm/voronoi.h, fetched
  2026-10-14 from https://raw.githubusercontent.com/blender/blender/main/intern/cycles/kernel/svm/voronoi.h)
  carries the block "SPDX-License-Identifier: MIT / Original code is copyright (c) 2013 Inigo Quilez. ...
  Distance To Edge based on: https://www.iquilezles.org/www/articles/voronoilines/voronoilines.htm,
  https://www.shadertoy.com/view/ldl3W8"; commit 3848507511a ("Cleanup: clarify license and origin of voronoi
  and dithering code") states "Original code is under the MIT License, Copyright (c) 2013 Inigo Quilez."
  MIT notice: Copyright (c) 2013 Inigo Quilez.  Permission is hereby granted, free of charge, to any person
  obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software
  without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute,
  sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do
  so, subject to the following conditions: The above copyright notice and this permission notice shall be
  included in all copies or substantial portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT
  WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.
Ported from GLSL by smfx (ideas/changes: iq's exact two-pass border distance -- a 3x3 nearest-site pass, then a
  5x5 pass around the nearest cell taking the distance to each bisector -- kept faithfully; added: the two
  nearest bisectors' normals for a smooth faceted bevel, integer-frequency Lissajous site orbits so the
  host-wrapped phase never jumps, leaded came / molten-crack border shading, palette-indexed glass, moving
  backlight hotspots, per-cell breathing and beat flares.  hash -> integer hash; y-down pixel frames.)

Look: leaded stained glass backlit by drifting light pools, bevelled cells glinting toward the light; with
``glow`` up (and the ember palette) the lead turns into glowing cracks and the glass into a dark cooling crust.

Macros: energy -> site orbit rate and wander amplitude, pan / backlight / light-direction rates, border glow,
hotspot strength, breathing depth, saturation.  pulse -> border glow flash + halo swell, bevel swell (cells
"inflate"), backlight hotspot kick, a rate kick into the orbit phase, and per-beat cell flares (rising-edge
spawns with their own lifetimes); all scaled by ``beat_kick``.  speed scales every rate; palette indexes the
glass (per-cell hash / smooth gradient / radial, by ``interior``); intensity is output gain.
"""
from __future__ import annotations

import functools
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

TAU = 2.0 * math.pi
N_FLARES = 8
N_HOT = 3

# P layout (floats)
#  0 density | 1,2 pan (cells) | 3 site base amp | 4 site wander amp | 5 orbit phase (wrapped 2pi) | 6 lead half
#  width (cells) | 7 interior | 8 bevel slope | 9 backlight | 10 glow level | 11 halo width (cells) | 12 interior
#  darkening | 13 breath phase (wrapped 2pi) | 14 breath amp | 15 saturation | 16 gain | 17 period | 18 drift
#  19,20 global light dir | 21..32 palette | 33..35 hot colour (linear) | 36..47 hotspots (x, y, r, a) x3
#  48 hotspot ambient | 49 gradient-field phase | 50 texture amount | 51 n flares | 52..83 flares (cx, cy, env, -)
#  84 vignette | 85 hotspot kick | 86 glow hot-colour share | 87 molten crack-core weight | 88 lead width wobble
#  89 glow wobble
_SRC = HELPERS + PALETTE_CUDA + r"""
#define TAU_F 6.2831853f
#define TS 12
__device__ __forceinline__ float s2l(float c) {
    c = clamp01(c); return c <= 0.04045f ? c * (1.f / 12.92f) : __powf((c + 0.055f) * (1.f / 1.055f), 2.4f);
}
__device__ __forceinline__ float l2s(float c) {
    c = clamp01(c); return c <= 0.0031308f ? 12.92f * c : 1.055f * __powf(c, 1.f / 2.4f) - 0.055f;
}
// soft shoulder: linear to 0.7, then eases into 1 (keeps saturated glass saturated, lets hot cores go white)
__device__ __forceinline__ float shoulder(float c) {
    return c < 0.7f ? fmaxf(c, 0.f) : 0.7f + 0.3f * (1.f - __expf(-(c - 0.7f) * (1.f / 0.3f)));
}
__device__ __forceinline__ float3 glass_colour(float idx, const float* __restrict__ P) {
    const float3 cs = pal(idx, P + 21);
    float3 c = make_float3(s2l(cs.x), s2l(cs.y), s2l(cs.z));
    const float lum = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z, s = P[15];
    return make_float3(fmaxf(lum + (c.x - lum) * s, 0.f), fmaxf(lum + (c.y - lum) * s, 0.f),
                       fmaxf(lum + (c.z - lum) * s, 0.f));
}
// Value noise from a 256x256 random table (host numpy, per seed): 4 cached loads instead of 4 integer hashes
// (32-bit integer multiplies are slow on Pascal).
__device__ __forceinline__ float tnoise(const float* __restrict__ NT, float x, float y) {
    const float fx = floorf(x), fy = floorf(y);
    const int ix = (int)fx & 255, iy = (int)fy & 255, ix1 = (ix + 1) & 255, iy1 = (iy + 1) & 255;
    float tx = x - fx, ty = y - fy;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const float a = NT[iy * 256 + ix], b = NT[iy * 256 + ix1], c = NT[iy1 * 256 + ix], d = NT[iy1 * 256 + ix1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
// Site of lattice cell (cx, cy) in cell-local coords: a static jitter (amp B) plus an integer-frequency Lissajous
// wander (amp A) -- integer frequencies so the 2pi-wrapped host phase never shows.  B + A <= 0.5 keeps sites
// inside their cells, which iq's 3x3 / 5x5 exactness relies on.
__device__ __forceinline__ float2 site(int cx, int cy, float B, float A, float ph, unsigned int seed) {
    const unsigned int h = hash_u((unsigned int)cx * 0x9E3779B1u ^ hash_u((unsigned int)cy * 0x85EBCA77u ^ seed));
    const unsigned int h2 = hash_u(h ^ 0x68bc21ebu);
    const float hx = (float)(h & 0xffffu) * (1.f / 65535.f), hy = (float)(h >> 16) * (1.f / 65535.f);
    const float ax = (float)(h2 & 0x3ffu) * (TAU_F / 1024.f), ay = (float)((h2 >> 10) & 0x3ffu) * (TAU_F / 1024.f);
    const float fx = (float)(1u + ((h2 >> 20) & 0xffu) % 3u), fy = (float)(1u + (h2 >> 28) % 3u);
    return make_float2(0.5f + B * (2.f * hx - 1.f) + A * __sinf(fx * ph + ax),
                       0.5f + B * (2.f * hy - 1.f) + A * __sinf(fy * ph + ay));
}

// Pre-pass over the lattice window [ox, ox + GW) x [oy, oy + GH) covering the screen (+ margin): each cell's site
// (S) and its per-pane attributes -- colour (C.xyz), pane density x breathing x flare (C.w), and (flare, texture
// hash, palette index, -) in X -- so the per-pixel search reads 33 cached float2s and the shading one cell.
extern "C" __global__
void cells(float2* __restrict__ S, float4* __restrict__ C, float4* __restrict__ X, const int GW, const int GH,
           const int ox, const int oy, const float* __restrict__ P, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= GW || j >= GH) return;
    const int cx = ox + i, cy = oy + j, k = j * GW + i;
    const float2 o = site(cx, cy, P[3], P[4], P[5], seed);
    S[k] = o;
    const unsigned int h = hash_u((unsigned int)cx * 0x9E3779B1u ^ hash_u((unsigned int)cy * 0x85EBCA77u ^ (seed + 0x2545F491u)));
    const float hc = (float)(h & 0xffffu) * (1.f / 65535.f);
    const float hb = (float)((h >> 16) & 0xffu) * (1.f / 255.f), ht = (float)(h >> 24) * (1.f / 255.f);
    const float sx = (cx + o.x - P[1]) / P[0], sy = (cy + o.y - P[2]) / P[0];      // the site in screen units
    const float G = 0.30f * sx + 0.18f * sy + 0.55f * vnoise(1.3f * sx + P[49], 1.3f * sy - 0.6f * P[49], seed + 3u);
    const float aid = smooth(0.35f, 1.f, P[7]);
    const float idx = P[18] + P[17] * (G + (hc - G) * aid);
    const float3 col = glass_colour(idx, P);
    const float breath = 1.f + P[14] * __sinf((hb < 0.5f ? 1.f : 2.f) * P[13] + TAU_F * hb);
    float fl = 0.f;
    const int nf = (int)P[51];
    for (int q = 0; q < nf; ++q) {
        const float* f = P + 52 + 4 * q;
        if (cx == (int)f[0] && cy == (int)f[1]) fl = fmaxf(fl, f[2]);
    }
    C[k] = make_float4(col.x, col.y, col.z, (0.7f + 0.3f * ht) * breath * (1.f + 1.2f * fl));
    X[k] = make_float4(fl, hc, idx, 0.f);
}

extern "C" __global__
void stained_glass(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                   const float2* __restrict__ S, const float4* __restrict__ C, const float4* __restrict__ X,
                   const float* __restrict__ NT, const int GW, const int GH, const int ox, const int oy,
                   const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const float invH = 1.f / (float)H;
    const float dens = P[0];
    // the block's cells +-3 (the reach of the two passes) staged in shared memory: <= TS x TS sites
    __shared__ float2 T[TS * TS];
    const int bx0 = blockIdx.x * blockDim.x, by0 = blockIdx.y * blockDim.y;
    const int c0x = min(max((int)floorf((bx0 + 0.5f - 0.5f * W) * invH * dens + P[1]) - 3 - ox, 0), GW - 1);
    const int c0y = min(max((int)floorf((by0 + 0.5f - 0.5f * H) * invH * dens + P[2]) - 3 - oy, 0), GH - 1);
    const int c1x = min(max((int)floorf((bx0 + blockDim.x - 0.5f - 0.5f * W) * invH * dens + P[1]) + 3 - ox, 0), GW - 1);
    const int c1y = min(max((int)floorf((by0 + blockDim.y - 0.5f - 0.5f * H) * invH * dens + P[2]) + 3 - oy, 0), GH - 1);
    const int tw = min(c1x - c0x + 1, TS), th = min(c1y - c0y + 1, TS);
    for (int t = threadIdx.y * blockDim.x + threadIdx.x; t < tw * th; t += blockDim.x * blockDim.y)
        T[t] = S[(c0y + t / tw) * GW + c0x + t % tw];
    __syncthreads();
    if (x >= W || y >= H) return;
    const float px = (x + 0.5f - 0.5f * W) * invH, py = (y + 0.5f - 0.5f * H) * invH;
    const float gx = px * dens + P[1], gy = py * dens + P[2];
    const float nx = floorf(gx), ny = floorf(gy);
    const float fx = gx - nx, fy = gy - ny;
    const int lx = min(max((int)nx - ox, 3), GW - 4), ly = min(max((int)ny - oy, 3), GH - 4);   // window-local
    const int tx0 = min(max(lx - c0x, 3), tw - 4), ty0 = min(max(ly - c0y, 3), th - 4);     // tile-local

    // ---- pass 1 (iq): nearest site over the 3x3 neighbourhood
    float d1 = 1e9f, mrx = 0.f, mry = 0.f;
    int mgx = 0, mgy = 0;
    #pragma unroll
    for (int j = -1; j <= 1; ++j)
        #pragma unroll
        for (int i = -1; i <= 1; ++i) {
            const float2 o = T[(ty0 + j) * tw + tx0 + i];
            const float rx = i + o.x - fx, ry = j + o.y - fy;
            const float d = rx * rx + ry * ry;
            if (d < d1) { d1 = d; mrx = rx; mry = ry; mgx = i; mgy = j; }
        }
    // ---- pass 2 (iq): distance to the nearest bisector over the 5x5 around the nearest cell (exact borders).
    //      Also keep the second-nearest bisector, to round the facet ridges of the bevel.
    // (iq's e = dot((mr + r) / 2, normalize(r - mr)); the numerator is (|r|^2 - |mr|^2) / 2 >= 0, so a candidate is
    //  rejected by num^2 >= e2^2 |r - mr|^2 without the rsqrt; normals are rebuilt from the two winners after.)
    const float m2 = mrx * mrx + mry * mry;
    float e1 = 1e9f, e2 = 1e9f, e1s = 1e18f, e2s = 1e18f;
    float d1x = 0.f, d1y = 1.f, d2x = 0.f, d2y = 1.f;
    #pragma unroll
    for (int j = -2; j <= 2; ++j)
        #pragma unroll
        for (int i = -2; i <= 2; ++i) {
            if (i == 0 && j == 0) continue;                      // the nearest site itself
            const int cx = mgx + i, cy = mgy + j;
            const float2 o = T[(ty0 + cy) * tw + tx0 + cx];
            const float rx = cx + o.x - fx, ry = cy + o.y - fy;
            const float dx = rx - mrx, dy = ry - mry, l2 = dx * dx + dy * dy;
            const float num = 0.5f * (rx * rx + ry * ry - m2);
            if (num * num < e2s * l2 && l2 > 1e-8f) {
                const float e = num * rsqrtf(l2);
                if (e < e1) { e2 = e1; e2s = e1s; d2x = d1x; d2y = d1y; e1 = e; e1s = e * e; d1x = dx; d1y = dy; }
                else { e2 = e; e2s = e * e; d2x = dx; d2y = dy; }
            }
        }
    float n1x, n1y, n2x, n2y;
    {
        const float a = rsqrtf(d1x * d1x + d1y * d1y + 1e-12f), b = rsqrtf(d2x * d2x + d2y * d2y + 1e-12f);
        n1x = d1x * a; n1y = d1y * a; n2x = d2x * b; n2y = d2y * b;
    }
    e1 = fmaxf(e1, 0.f);
    {   // round the medial-axis creases (and fillet the lead joints): polynomial smooth-min of the two nearest
        const float k = 0.06f, h = fmaxf(k - fabsf(e2 - e1), 0.f) * (1.f / k);
        e1 = fmaxf(e1 - h * h * k * 0.25f, 0.f);
    }
    const int kc = (ly + mgy) * GW + lx + mgx;
    const float4 cc = C[kc], cx4 = X[kc];
    // bevel normal: nearest bisector's normal, blended with the second one near the facet ridge
    const float wr = 1.f - smooth(0.f, 0.07f, e2 - e1);
    float nbx = n1x + wr * n2x, nby = n1y + wr * n2y;
    {
        const float il = rsqrtf(nbx * nbx + nby * nby + 1e-8f);
        nbx *= il; nby *= il;
    }

    // ---- glass colour: per pane from the pre-pass, or re-indexed per pixel when the radial gradient is on
    const float interior = P[7];
    const float radw = smooth(0.f, 0.5f, interior) * (1.f - smooth(0.5f, 0.9f, interior));
    const float lr = sqrtf(d1);
    const float rn = lr / (lr + e1 + 1e-4f);                        // 0 at the site, 1 at the border
    float3 col = make_float3(cc.x, cc.y, cc.z);
    if (radw > 1e-3f) col = glass_colour(cx4.z + P[17] * 0.5f * radw * rn, P);

    // ---- backlight: even, or drifting light pools (hotspots); also the local light direction toward them
    float hot = P[48], Lx = 0.f, Ly = 0.f;
    #pragma unroll
    for (int i = 0; i < 3; ++i) {
        const float* hs = P + 36 + 4 * i;
        const float dx = hs[0] - px, dy = hs[1] - py, r2 = dx * dx + dy * dy, ir = 1.f / (hs[2] * hs[2]);
        hot += hs[3] * P[85] * __expf(-r2 * ir);
        const float w = hs[3] * __expf(-0.25f * r2 * ir) * rsqrtf(r2 + 1e-4f);
        Lx += dx * w; Ly += dy * w;
    }
    const float bl = P[9];
    const float light = 0.7f + (hot - 0.7f) * bl;
    float Ldx = P[19] * (1.f - bl) + Lx * bl, Ldy = P[20] * (1.f - bl) + Ly * bl;
    {
        const float il = 1.f / fmaxf(sqrtf(Ldx * Ldx + Ldy * Ldy), 0.35f);
        Ldx *= il; Ldy *= il;
    }

    // ---- glass texture (several px at any density) and radial brightness
    const float tex = 1.f + P[50] * (tnoise(NT, gx * 3.1f + 37.f * cx4.y, gy * 3.1f - 23.f * cx4.y) - 0.5f);
    const float radial = 1.f + radw * (0.25f - 0.55f * rn * rn);

    // ---- lead width, wobbling along the line (hand-cut came; irregular molten cracks)
    const float wob = tnoise(NT, gx * 3.3f + 101.3f, gy * 3.3f + 57.7f) - 0.5f;
    const float bw = P[6] * (1.f + P[88] * wob);

    // ---- bevel: the glass slopes down into the lead; emboss toward the light, plus a glint band
    const float eb = fmaxf(e1 - bw, 0.f);
    const float slope = P[8] * (__expf(-eb * 14.f) + 0.2f * __expf(-eb * 3.f));
    const float tx = nbx * slope, ty = nby * slope, tl = tx * Ldx + ty * Ldy;
    const float emb = fmaxf(0.f, 1.f + 0.7f * tl);
    const float ndh = (0.45f * tl + 1.f) * rsqrtf(tx * tx + ty * ty + 1.f) * rsqrtf(0.2025f * (Ldx * Ldx + Ldy * Ldy) + 1.f);
    const float spec = __powf(fmaxf(ndh, 0.f), 60.f) * fminf(P[8], 1.f) * P[12];

    const float gl = light * cc.w * tex * radial * P[12] * emb;
    const float wt = clamp01((light - 0.7f) * 1.5f) * bl;            // pools run warm, shadows cool (sunlight)
    float3 glass = make_float3(col.x * gl * (1.f + 0.12f * wt), col.y * gl, col.z * gl * (1.f - 0.15f * wt + 0.1f * bl * (1.f - wt)));
    const float fl = cx4.x * light * P[12];                     // beat flare: the pane floods with light
    glass.x += fl * (0.25f + 0.75f * col.x); glass.y += fl * (0.25f + 0.75f * col.y); glass.z += fl * (0.25f + 0.75f * col.z);
    const float sp = spec * 0.9f * light;
    glass.x += sp; glass.y += sp * 0.96f; glass.z += sp * 0.9f;

    // ---- lead came (antialiased by pixel footprint) and glowing border / molten crack
    const float fw = dens * invH;
    const float lm = 1.f - smooth(bw - fw, bw + fw, e1);
    const float u = clamp01(e1 / fmaxf(bw, 1e-4f));
    const float core = 1.f - u * u;
    const float leadv = 0.004f + 0.012f * core * light + 0.05f * core * core * fmaxf(0.f, nbx * Ldx + nby * Ldy) * light;
    float3 c = make_float3(glass.x + (leadv - glass.x) * lm, glass.y + (leadv - glass.y) * lm,
                           glass.z + (leadv * 1.08f - glass.z) * lm);
    const float ge = P[10] * (1.f + 0.6f * cx4.x) * (1.f + P[89] * wob);   // hotter where the crack is wider
    if (ge > 0.f) {
        const float halo = 0.8f * __expf(-eb / P[11]) + 0.2f * __expf(-eb / (3.f * P[11]));
        const float inv = 1.f / fmaxf(fmaxf(col.x, fmaxf(col.y, col.z)), 0.35f);
        const float hm = P[86];                                   // hot-colour share (rises with glow)
        const float3 gc = make_float3(col.x * inv + (P[33] - col.x * inv) * hm, col.y * inv + (P[34] - col.y * inv) * hm,
                                      col.z * inv + (P[35] - col.z * inv) * hm);
        const float k = ge * (0.6f * halo * (1.f - lm) + lm * P[87] * (0.3f + 1.5f * core));
        const float wh = ge * ge * lm * core * P[87] * 0.3f;      // white-hot crack core
        c.x += gc.x * k + wh; c.y += gc.y * k + wh * 0.85f; c.z += gc.z * k + wh * 0.6f;
    }

    // ---- gain, vignette, shoulder, encode with +-1 LSB triangular dither
    const float ex = (x + 0.5f) / W - 0.5f, ey = (y + 0.5f) / H - 0.5f;
    const float g = P[16] * (1.f - P[84] * (ex * ex + ey * ey) * 2.f);
    const unsigned int hd = hash_u((unsigned int)x * 0x9E3779B1u ^ (unsigned int)y * 0x85EBCA77u);
    const float d = ((float)(hd & 0xffu) + (float)((hd >> 8) & 0xffu)) * (1.f / 255.f) - 1.f;
    out[y * W + x] = make_uchar4(
        (unsigned char)fminf(fmaxf(l2s(shoulder(c.x * g)) * 255.f + d + 0.5f, 0.f), 255.f),
        (unsigned char)fminf(fmaxf(l2s(shoulder(c.y * g)) * 255.f + d + 0.5f, 0.f), 255.f),
        (unsigned char)fminf(fmaxf(l2s(shoulder(c.z * g)) * 255.f + d + 0.5f, 0.f), 255.f), 255);
}
"""
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


@functools.lru_cache(maxsize=None)
def _hot_colour(name: str) -> tuple[float, float, float]:
    """The palette's bright end (luminance^3-weighted mean over two periods, so drift-invariant for every shared
    palette), linear, max channel 1."""
    a, b, c, d = (np.asarray(v, np.float64) for v in PALETTES[name])
    t = np.arange(128)[:, None] / 64.0
    s = np.clip(a + b * np.cos(TAU * (c * t + d)), 0.0, 1.0)
    lin = np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    w = (lin @ np.array([0.2126, 0.7152, 0.0722])) ** 3 + 1e-6
    h = (lin * w[:, None]).sum(0) / w.sum()
    h = h / max(h.max(), 1e-3)
    return float(h[0]), float(h[1]), float(h[2])


class StainedGlass(Source):
    name = "stained_glass"
    label = "Stained glass"
    description = ("Animated Voronoi glass with exact leaded borders, bevelled and backlit by drifting light; "
                   "or molten cracks in cooling lava.")
    params = {
        "cell_density": num(3.0, 30.0, 9.0, "Cells per screen height", step=0.1, tier="regional", basic=True),
        "interior": num(0.0, 1.0, 0.85, "Glass colour (gradient -> radial -> per pane)", tier="regional", basic=True),
        "jitter": num(0.0, 1.0, 0.85, "Jitter (grid -> organic)", tier="regional"),
        "border_width": num(0.002, 0.03, 0.014, "Lead width", step=0.0005, tier="regional"),
        "bevel": num(0.0, 1.0, 0.5, "Bevel", tier="regional"),
        "backlight": num(0.0, 1.0, 0.8, "Backlight (even -> moving pools)", tier="regional"),
        "glow": num(0.0, 1.0, 0.2, "Border glow (lead -> molten)", tier="regional"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat response", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=0.75, period=1.0, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _OPTIONS)
        self.k = mod.get_function("stained_glass")
        self.k_cells = mod.get_function("cells")
        aspect = self.w / self.h
        self.GW, self.GH = int(math.ceil(aspect * 30.0)) + 12, 30 + 12      # lattice window at max density
        self.S = cp.zeros((self.GH, self.GW, 2), cp.float32)
        self.C = cp.zeros((self.GH, self.GW, 4), cp.float32)
        self.X = cp.zeros((self.GH, self.GW, 4), cp.float32)
        self.P = cp.zeros(96, cp.float32)
        self.Ph = np.zeros(96, np.float32)
        self.rng = np.random.default_rng(int(params["seed"]))
        self.NT = cp.asarray(self.rng.random((256, 256), dtype=np.float32))        # value-noise lattice
        self.hot_rand = self.rng.uniform(0.0, TAU, (N_HOT, 3))
        self.orbit = 0.0          # site orbit phase (kernel, wrapped 2pi)
        self.breath = 0.0         # per-cell breathing phase (kernel, wrapped 2pi)
        self.pan = np.array([0.0, 0.0])
        self.pan_t = float(self.rng.uniform(0, TAU))   # host-only phases (float64, never uploaded raw)
        self.hot_t = 0.0
        self.light_t = float(self.rng.uniform(0, TAU))
        self.field_t = 0.0
        self.drift = 0.0
        self.e_s = None
        self.t = 0.0
        self.armed = True
        self.last_fire = -1.0
        self.pk = 0.0             # pulse with a short attack slew (HEVC-friendly), release follows the envelope
        self.flares: list[list[float]] = []    # [cx, cy, age, strength, life]

    def _spawn(self, p: dict, es: float, k_gain: float) -> None:
        dens = float(p["cell_density"])
        aspect = self.w / self.h
        n = 1 + int(self.rng.random() < es) + int(self.rng.random() < es * es)
        for _ in range(n):
            gx = self.pan[0] + (self.rng.random() - 0.5) * 0.9 * aspect * dens
            gy = self.pan[1] + (self.rng.random() - 0.5) * 0.9 * dens
            self.flares.append([math.floor(gx), math.floor(gy), 0.0, k_gain * (0.55 + 0.45 * self.rng.random()),
                                0.35 + 0.35 * self.rng.random()])
        self.flares = self.flares[-N_FLARES:]

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse_in = min(max(float(p["pulse"]), 0.0), 1.0)
        if dt <= 0.0 or pulse_in <= self.pk:
            self.pk = pulse_in
        else:
            self.pk += (pulse_in - self.pk) * (1.0 - math.exp(-dt / 0.03))
        pulse = self.pk
        spd = float(p["speed"])
        ep = _smoothstep(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.e_s = ep if self.e_s is None else self.e_s + (ep - self.e_s) * a
        es = self.e_s
        kick = float(p["beat_kick"])
        k = pulse * (0.3 + 0.7 * e) * kick
        rate = 0.4 + 1.2 * ep
        self.t += dt

        # ---- integrate rates (the pulse kicks the orbit rate only; nothing positional jumps)
        self.orbit = _wrap(self.orbit + 0.8 * rate * (1.0 + 1.5 * k) * spd * dt, TAU)
        self.breath = _wrap(self.breath + 0.9 * rate * spd * dt, TAU)
        self.pan_t += 0.03 * spd * dt
        ang = 0.7 + 1.1 * math.sin(self.pan_t)
        pr = 0.09 * rate * spd * dt
        self.pan += (pr * math.cos(ang), pr * math.sin(ang))
        self.hot_t += 0.2 * rate * spd * dt
        self.light_t += 0.12 * rate * spd * dt
        self.field_t += 0.03 * rate * spd * dt
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- beat flares: rising edge with hysteresis, >= 150 ms apart; each owns its lifetime
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.t - self.last_fire >= 0.15 and kick > 0.0:
            self.armed = False
            self.last_fire = self.t
            self._spawn(p, es, min(kick, 1.5) * (0.45 + 0.55 * e))
        live = []
        for f in self.flares:
            f[2] += dt
            if f[2] < 4.0 * f[4]:
                live.append(f)
        self.flares = live

        # ---- sites: wander amplitude from energy, the static jitter takes the rest of the cell
        A = 0.10 + 0.17 * es
        B = float(p["jitter"]) * (0.5 - A) * 0.94
        dens = float(p["cell_density"])
        bw = 0.5 * float(p["border_width"]) * dens
        bw = 0.22 * math.tanh(bw / 0.22)
        glow = float(p["glow"])

        Ph = self.Ph
        Ph[:] = 0.0
        Ph[0] = dens
        Ph[1:3] = self.pan
        Ph[3] = B
        Ph[4] = A
        Ph[5] = self.orbit
        Ph[6] = bw
        Ph[7] = p["interior"]
        Ph[8] = float(p["bevel"]) * 1.3 * (1.0 + 0.3 * k)
        Ph[9] = p["backlight"]
        Ph[10] = (glow * (0.55 + 0.9 * es) + 0.10 * es ** 1.5) * (1.0 + 0.9 * k)
        Ph[11] = (0.03 + 0.045 * glow) * (1.0 + 0.5 * k)
        Ph[12] = 1.0 - 0.85 * _smoothstep(0.4, 1.0, glow)
        Ph[13] = self.breath
        Ph[14] = 0.05 + 0.10 * es ** 1.5
        Ph[15] = 1.0 + 0.2 * es
        Ph[16] = p["intensity"]
        Ph[17] = p["palette_period"]
        Ph[18] = self.drift
        Ph[19] = math.cos(self.light_t)
        Ph[20] = math.sin(self.light_t)
        Ph[21:33] = palette_coeffs(p["palette"])
        Ph[33:36] = _hot_colour(p["palette"])
        aspect = self.w / self.h
        for i in range(N_HOT):
            r1, r2, r3 = self.hot_rand[i]
            hx = 0.42 * aspect * math.sin(self.hot_t * (0.53 + 0.19 * i) + r1)
            hy = 0.36 * math.sin(self.hot_t * (0.71 + 0.13 * i) + r2)
            hr = 0.30 + 0.06 * math.sin(self.hot_t * 0.37 + r3)
            Ph[36 + 4 * i: 40 + 4 * i] = (hx, hy, hr, 1.4 * (0.8 + 0.4 * es))
        Ph[48] = 0.06
        Ph[49] = self.field_t
        Ph[50] = 0.22 + 0.45 * _smoothstep(0.4, 1.0, glow)
        Ph[51] = len(self.flares)
        for i, f in enumerate(self.flares):
            env = f[3] * math.exp(-f[2] / f[4])
            Ph[52 + 4 * i: 56 + 4 * i] = (f[0], f[1], env, 0.0)
        Ph[84] = 0.22
        Ph[85] = 1.0 + 0.3 * k
        Ph[86] = 0.15 + 0.85 * _smoothstep(0.45, 1.0, glow)
        Ph[87] = 0.1 + 0.9 * _smoothstep(0.2, 0.9, glow)
        Ph[88] = 0.35 + 1.25 * _smoothstep(0.3, 1.0, glow)
        Ph[89] = 1.2 * _smoothstep(0.3, 1.0, glow)
        self.P.set(Ph)
        seed = np.uint32(int(p["seed"]) & 0xFFFFFFFF)
        ox = int(math.floor(self.pan[0] - 0.5 * aspect * dens)) - 4
        oy = int(math.floor(self.pan[1] - 0.5 * dens)) - 4
        dims = (np.int32(self.GW), np.int32(self.GH), np.int32(ox), np.int32(oy))
        launch_2d(self.k_cells, self.GW, self.GH, (self.S, self.C, self.X, *dims, self.P, seed))
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.S, self.C,
                                           self.X, self.NT, *dims, seed))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
