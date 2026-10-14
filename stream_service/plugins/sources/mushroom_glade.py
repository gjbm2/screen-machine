"""Mushroom glade: a slow creep across a night forest floor of bioluminescent mushrooms.

Source: Original (inspired by https://en.wikipedia.org/wiki/Foxfire and https://iquilezles.org/articles/smin/)
Author: smfx (original; iq smin, MIT, via sdf3d)
License: MIT (original)
license_class: permissive

Mushrooms are smooth-min unions of a bent, flared stem and an ellipsoid cap cut flat underneath, scattered one per
pMod cell (2.2 units) with hashed size, height, bend, colour and an optional small child mushroom; the camera's lane
holds only tiny ones.  Each cell is marched with its own SDF and the step is clamped to the cell exit, so jittered
content never overlaps a neighbour.  A light under every cap lights the moss, stems and tree trunks (3x3 cell
gather, windowed so it is seamless) and adds a gaussian halo integrated analytically along the ray (erf) per
visited cell.  Caps glow at the gills (radial lines), the rim (fresnel) and in palette spots.  The ground is an
analytic plane of moss with a mycelium network (warped segments between neighbouring mushroom nodes plus a fine
ridged-noise web) along which light dashes flow.  Tree trunks on a coarse grid fade into exp2 fog.  Spores
(rising motes) and fireflies (wandering, blinking, bokeh discs by distance from a focal plane) are analytic
sprites on world-anchored z slices, depth-tested against the march.

Macros: energy -> camera creep speed, firefly count / wander / blink rate, spore density and drift, glow level,
mycelium flow speed, mushroom breathing.  pulse -> glow gain (1 + 0.35 pulse) and, on each rising edge, a
mycelium wave: a ring that travels outward from a point ahead of the camera, lighting the lines and then each
mushroom in turn as it passes their stems (travelling amplitude, decays).  speed scales creep, spores, fireflies,
flow and breathing; drift rotates the glow palette; intensity is output gain.

The world is periodic in z (880 units; every hash and slice index wraps with it) and the camera position is
uploaded modulo that period, so long runs keep full float precision without any visible jump.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

S_CELL = 2.2            # mushroom cell
ZPER = 400              # mushroom cells per world z period
ZWRAP = S_CELL * ZPER   # 880 world units
MAX_WAVES = 4
PH_PERIOD = 8.0 * math.pi   # wander / blink / breathing phases: kernel frequencies are k/4, k integer

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density
#  29 density threshold | 30 cap shape | 31 glow gain | 32 glow spread | 33 halo amp | 34 flow phase (wraps 1)
#  35 mycelium level | 36 breath phase | 37 firefly threshold | 38 firefly wander phase | 39 flicker phase
#  40 spore threshold | 41 spore rise phase (wraps 4) | 42 spore sway phase | 43 pulse | 44 focus distance
#  45 seed | 46..61 waves (ox, oz, radius, amp) x4 | 62 spore brightness | 63 firefly brightness | 64 moon
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define S_CELL (%(scell)ff)
#define T_CELL (4.f * S_CELL)
#define ZPER %(zper)d
#define TPER (ZPER / 4)
#define MTOP 1.85f
#define YH 2.9f
#define TFAR 30.f
#define MAX_STEPS 84
#define FF_DZ (0.5f * S_CELL)
#define FF_PER (2 * ZPER)
#define FF_N 9
#define FF_CX 1.4f
#define FF_CY 1.15f
#define SP_DZ (0.25f * S_CELL)
#define SP_PER (4 * ZPER)
#define SP_N 14
#define SP_C 0.32f
#define SP_ROWS 9

__device__ __forceinline__ int imod(int a, int n) { const int m = a %% n; return m < 0 ? m + n : m; }
__device__ __forceinline__ unsigned int key3(int a, int b, unsigned int salt, unsigned int seed) {
    return hash_u((unsigned int)(a + 65536) * 0x9E3779B1U ^ hash_u((unsigned int)b * 0x85EBCA77U ^ hash_u(salt * 0x27d4eb2dU + seed)));
}
struct Rng {
    unsigned int s;
    __device__ __forceinline__ float next() { s = hash_u(s + 0x9E3779B9U); return (s >> 8) * (1.f / 16777216.f); }
};
// value noise periodic in z (lattice index wraps at nz) so the world stays periodic
__device__ float pvnoise(float x, float z, int nz, unsigned int seed) {
    const float fx = floorf(x), fz = floorf(z);
    const int ix = (int)fx, iz = (int)fz;
    float tx = x - fx, tz = z - fz;
    tx = tx * tx * (3.f - 2.f * tx); tz = tz * tz * (3.f - 2.f * tz);
    const unsigned int z0 = (unsigned int)imod(iz, nz), z1 = (unsigned int)imod(iz + 1, nz);
    const float a = hash1(ix + 70000, z0, seed), b = hash1(ix + 70001, z0, seed);
    const float c = hash1(ix + 70000, z1, seed), d = hash1(ix + 70001, z1, seed);
    return (1.f - tz) * ((1.f - tx) * a + tx * b) + tz * ((1.f - tx) * c + tx * d);
}

// ------------------------------------------------------------------ mushrooms
struct Mush {
    float2 base;                 // stem base (x, z)
    float h, R, k, cut, sr;      // stem height, cap radius, cap height ratio, underside cut, stem radius (grown)
    float2 bend;                 // cap offset from the stem base (grown)
    float g;                     // growth 0..1 (0 = absent)
    float hue, ph;
    float2 cbase; float cs, cg;  // child: base, relative scale, growth (0 = none)
};
__device__ Mush get_mush(int cx, int cz, const float* __restrict__ P) {
    Mush m;
    Rng r; r.s = key3(cx, imod(cz, ZPER), 1u, (unsigned int)P[45]);
    const float h0 = r.next(), h1 = r.next(), h2 = r.next(), h3 = r.next(), h4 = r.next(), h5 = r.next();
    const float h6 = r.next(), h7 = r.next(), h8 = r.next(), h9 = r.next(), h10 = r.next();
    const float shape = P[30];
    const bool lane = (cx == 0);
    const float dens = lane ? P[29] * 0.8f : P[29];
    const float g = smooth(dens, dens - 0.12f, h0);
    const float s = mix(0.55f, 1.15f, h1) * (lane ? 0.36f : 1.f);
    const float R1 = s * 0.4f * (1.f + 0.3f * shape);
    const float H1 = s * mix(0.55f, 1.05f, h2);
    const float2 bend1 = f2(h4 - 0.5f, h5 - 0.5f) * (0.3f * H1);
    const bool child = !lane && h6 < 0.65f;
    const float cs = mix(0.32f, 0.5f, h7);
    const float ca = h8 * SDF_TAU;
    const float cd = 0.55f * R1 + 0.16f;
    float ext = R1 + fmaxf(fabsf(bend1.x), fabsf(bend1.y));
    if (child) ext = fmaxf(ext, cd + cs * R1 + 0.05f);
    const float jl = fmaxf(0.5f * S_CELL - ext - 0.08f, 0.f);
    float2 c = f2(cx * S_CELL, cz * S_CELL);
    if (lane) c.x = (h3 < 0.5f ? -1.f : 1.f) * (0.64f + 0.16f * h9);
    else c.x += (2.f * h9 - 1.f) * jl;
    c.y += (2.f * h10 - 1.f) * jl;
    m.base = c;
    m.g = g;
    m.h = fmaxf(H1 * g, 1e-3f);
    m.R = fmaxf(R1 * g, 1e-3f);
    m.k = mix(0.85f, 0.3f, shape) * mix(0.85f, 1.15f, h5);
    m.cut = m.R * m.k * mix(0.42f, 0.08f, shape);
    m.sr = fmaxf(m.R * mix(0.2f, 0.13f, shape), 0.01f * g + 1e-3f);
    m.bend = bend1 * g;
    m.hue = 0.65f * h3 + 0.35f * h7;
    m.ph = h9;
    m.cbase = c + f2(cosf(ca), sinf(ca)) * (cd * g);
    m.cs = cs;
    m.cg = child ? g : 0.f;
    return m;
}
__device__ __forceinline__ float sd_shroom(float3 q, float h, float R, float k, float cut, float sr, float2 bend) {
    const float yy = saturate(q.y / h);
    const float b2 = yy * yy;
    float r = sr * (1.15f - 0.3f * yy);
    const float fl = saturate(1.f - q.y / (0.2f * h));
    r += sr * 1.2f * fl * fl;                                        // flared foot
    float ds = length(f2(q.x - bend.x * b2, q.z - bend.y * b2)) - r;
    ds = fmaxf(ds, q.y - h);
    const float3 qc = q - f3(bend.x, h, bend.y);
    float dc = sdEllipsoid(qc, f3(R, R * k, R));
    dc = smax(dc, -qc.y - cut, 0.02f + 0.05f * R);                   // flat gill plate underneath
    return smin(ds, dc, 0.04f + 0.1f * R);                           // stem melts into the cap
}
__device__ __forceinline__ float child_d(float3 p, const Mush& m) {
    const float3 q = f3(p.x - m.cbase.x, p.y, p.z - m.cbase.y);
    const float cs = m.cs;
    return sd_shroom(q, m.h * cs * 1.2f, m.R * cs, m.k * 1.1f, m.cut * cs, m.sr * cs * 1.3f, m.bend * (-0.5f * cs));
}
__device__ __forceinline__ float main_d(float3 p, const Mush& m) {
    const float3 q = f3(p.x - m.base.x, p.y, p.z - m.base.y);
    return sd_shroom(q, m.h, m.R, m.k, m.cut, m.sr, m.bend);
}
__device__ float mush_d(float3 p, const Mush& m) {
    if (m.g < 0.02f) return 1e9f;
    const float top = m.h + m.R * m.k + 0.02f;
    const float bm = fmaxf(length(f2(p.x - m.base.x - m.bend.x, p.z - m.base.y - m.bend.y)) - m.R - 0.1f - 0.5f * length(m.bend), p.y - top);
    float d = bm > 0.25f ? bm : main_d(p, m);
    if (m.cg > 0.02f) {
        const float bc = fmaxf(length(f2(p.x - m.cbase.x, p.z - m.cbase.y)) - m.R * m.cs - 0.1f, p.y - top * m.cs * 1.25f);
        d = fminf(d, bc > 0.25f ? bc : child_d(p, m));
    }
    return d;
}
__device__ __forceinline__ float wave_at(float2 xz, const float* __restrict__ P, float width) {
    float w = 0.f;
    const float iw = 1.f / width;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float* wv = P + 46 + 4 * i;
        if (wv[3] > 0.003f) {
            const float d = (length(xz - f2(wv[0], wv[1])) - wv[2]) * iw;
            w += wv[3] * expf(-d * d);
        }
    }
    return w;
}
__device__ __forceinline__ float mush_boost(const Mush& m, const float* __restrict__ P) {
    const float f = (2.f + floorf(m.ph * 5.f)) * 0.25f;
    const float br = 0.78f + 0.22f * sinf(f * P[36] + SDF_TAU * m.ph);
    return br * (1.f + 2.6f * wave_at(m.base, P, 0.75f));
}
__device__ __forceinline__ float3 mush_col(const Mush& m, const float* __restrict__ P, float off) {
    return srgb_to_linear(pal(m.hue * 0.45f * P[25] + P[26] + off, P + 13));
}
__device__ __forceinline__ float3 mush_light_pos(const Mush& m) {
    return f3(m.base.x + m.bend.x, m.h - m.cut - 0.03f, m.base.y + m.bend.y);
}
// light from the 3x3 cells of mushrooms around pos (windowed by distance so it is seamless), + stem-contact AO
__device__ float3 glade_light(float3 pos, float3 n, const float* __restrict__ P, float& ao) {
    const int cx0 = (int)floorf(pos.x / S_CELL + 0.5f), cz0 = (int)floorf(pos.z / S_CELL + 0.5f);
    float3 acc = f3(0.f);
    ao = 1.f;
    const float spread = P[32];
    #pragma unroll 1
    for (int j = 0; j < 9; ++j) {
        const Mush m = get_mush(cx0 + j %% 3 - 1, cz0 + j / 3 - 1, P);
        if (m.g < 0.02f) continue;
        const float3 l = mush_light_pos(m) - pos;
        const float d2 = dot2(l) + 1e-3f;
        const float dxz = length(f2(l.x, l.z));
        const float wnd = 1.f - smoothstep(0.5f * S_CELL, 1.0f * S_CELL, dxz);
        if (wnd <= 0.f) continue;
        const float rho = m.R * (0.6f + 1.9f * spread) + 0.05f;
        const float ndl = saturate(dot(n, l) * rsqrtf(d2) * 0.85f + 0.15f);
        const float I = m.R * m.R * 11.f * ndl / (1.f + d2 / (rho * rho)) * wnd * mush_boost(m, P);
        acc += mush_col(m, P, 0.f) * I;
        const float ds = length(f2(pos.x - m.base.x, pos.z - m.base.y));
        ao *= mix(0.45f, 1.f, saturate(smoothstep(m.sr * 0.8f, m.sr * 4.f + 0.12f, ds) + saturate(pos.y * 3.f)));
    }
    ao = fmaxf(ao, 0.f);
    return acc * P[31];
}

// ------------------------------------------------------------------ trunks
struct Trunk { float2 c; float r, on, hs; };
__device__ Trunk get_trunk(int cx, int cz, const float* __restrict__ P) {
    Trunk t;
    Rng r; r.s = key3(cx, imod(cz, TPER), 7u, (unsigned int)P[45]);
    const float h0 = r.next(), h1 = r.next(), h2 = r.next(), h3 = r.next();
    t.on = (cx != 0 && h0 < 0.72f) ? 1.f : 0.f;
    t.r = 0.32f + 0.45f * h1;
    const float jl = 0.5f * T_CELL - 1.7f * t.r - 0.3f;
    t.c = f2(cx * T_CELL + (2.f * h2 - 1.f) * jl, cz * T_CELL + (2.f * h3 - 1.f) * jl);
    t.hs = h0;
    return t;
}
__device__ __forceinline__ float trunk_d(float3 p, const Trunk& t) {
    if (t.on < 0.5f) return 1e9f;
    const float fl = 1.f + 0.6f * expf(-fmaxf(p.y, 0.f) * 1.8f);
    return length(f2(p.x - t.c.x, p.z - t.c.y)) - t.r * fl;
}

// exit distance along rd from p out of the square cell (cx, cz) of size s in xz
__device__ __forceinline__ float cell_exit(float3 p, float3 rd, int cx, int cz, float s) {
    float tx = 1e9f, tz = 1e9f;
    if (rd.x > 1e-6f) tx = ((cx + 0.5f) * s - p.x) / rd.x; else if (rd.x < -1e-6f) tx = ((cx - 0.5f) * s - p.x) / rd.x;
    if (rd.z > 1e-6f) tz = ((cz + 0.5f) * s - p.z) / rd.z; else if (rd.z < -1e-6f) tz = ((cz - 0.5f) * s - p.z) / rd.z;
    return fmaxf(fminf(tx, tz), 0.f);
}
// normalised gaussian halo integral along the ray over [ta, tb] (a full pass through the centre = 1)
__device__ __forceinline__ float halo_seg(float3 ro, float3 rd, float3 L, float sig, float ta, float tb, float fogd) {
    if (tb <= ta) return 0.f;
    const float3 w = ro - L;
    const float b0 = dot(w, rd);
    const float c = fmaxf(dot2(w) - b0 * b0, 0.f);
    const float is = 1.f / sig;
    const float v = expf(-c * is * is) * 0.5f * (erff((tb + b0) * is) - erff((ta + b0) * is));
    const float tc = clamp(-b0, ta, tb);
    return v * expf(-fogd * fogd * tc * tc);
}

SDF_KERNEL
void mushroom_glade(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float fogd = P[28], drift = P[26];
    const float* PAL = P + 13;

    const float tg = rd.y < -1e-4f ? -ro.y / rd.y : 1e9f;
    const float tmax = fminf(TFAR, tg);

    // ---------------- march: mushroom grid (cached per cell, halo per visited cell) + trunk grid
    float t = 0.03f;
    int mcx = 0, mcz = 0, tcx = 0, tcz = 0;
    bool mvalid = false, mtrack = true, tvalid = false;
    Mush M; Trunk TR;
    float m_tin = 0.f, m_tout = 0.f, t_tout = 0.f, hsig = 1.f;
    float3 hL = f3(0.f), hcol = f3(0.f), halo = f3(0.f);
    const float spread = P[32], hamp = P[33];
    int id = 0;
    for (int i = 0; i < MAX_STEPS; ++i) {
        if (t >= tmax) break;
        const float3 p = ro + rd * t;
        float step = 1e9f, dmin = 1e9f;
        int idc = 0;
        if (mtrack) {
            if (p.y > YH && rd.y >= 0.f) {
                if (mvalid) halo += hcol * halo_seg(ro, rd, hL, hsig, m_tin, m_tout, fogd);
                mvalid = false; mtrack = false;
            } else {
                const int cx = (int)floorf(p.x / S_CELL + 0.5f), cz = (int)floorf(p.z / S_CELL + 0.5f);
                if (!mvalid || cx != mcx || cz != mcz) {
                    if (mvalid) halo += hcol * halo_seg(ro, rd, hL, hsig, m_tin, m_tout, fogd);
                    M = get_mush(cx, cz, P); mcx = cx; mcz = cz; mvalid = true;
                    m_tin = t; m_tout = t + cell_exit(p, rd, cx, cz, S_CELL);
                    hL = mush_light_pos(M);
                    hsig = M.R * (0.32f + 0.38f * spread) + 0.02f;
                    hcol = M.g > 0.02f ? mush_col(M, P, 0.f) * (hamp * M.g * mush_boost(M, P)) : f3(0.f);
                }
                const float dm = p.y < MTOP + 0.3f ? mush_d(p, M) : p.y - MTOP;
                if (dm < dmin) { dmin = dm; idc = 1; }
                step = fminf(dm * 0.8f, m_tout - t + 2e-3f);
            }
        }
        {
            const int cx = (int)floorf(p.x / T_CELL + 0.5f), cz = (int)floorf(p.z / T_CELL + 0.5f);
            if (!tvalid || cx != tcx || cz != tcz) {
                TR = get_trunk(cx, cz, P); tcx = cx; tcz = cz; tvalid = true;
                t_tout = t + cell_exit(p, rd, cx, cz, T_CELL);
            }
            const float dt_ = trunk_d(p, TR);
            if (dt_ < dmin) { dmin = dt_; idc = 2; }
            step = fminf(step, fminf(dt_ * 0.8f, t_tout - t + 2e-3f));
        }
        if (dmin < 0.6f * pix * t + 2e-4f) { id = idc; break; }
        t += fmaxf(step, 1e-3f);
    }
    float tend;
    if (id) tend = t;
    else if (tg <= TFAR) { id = 3; tend = tg; }         // ground (also when the march ran out of steps)
    else tend = fminf(t, TFAR);
    if (mvalid) halo += hcol * halo_seg(ro, rd, hL, hsig, m_tin, fminf(m_tout, tend), fogd);

    // ---------------- shading
    const float3 pf = srgb_to_linear(pal(drift + 0.62f, PAL));
    const float3 fogc = f3(0.004f, 0.008f, 0.014f) + mix(pf, f3(dot(pf, f3(0.333f))), 0.35f) * 0.018f;
    const float3 moon = f3(0.020f, 0.028f, 0.045f) * P[64];
    float3 col = fogc * (1.05f - 0.75f * saturate(rd.y * 1.6f));
    const float3 pos = ro + rd * tend;
    if (id == 1) {
        auto mapm = [&](float3 q) { return mush_d(q, M); };
        const float3 n = calc_normal(mapm, pos, fmaxf(0.5f * pix * tend, 4e-4f));
        const bool ch = M.cg > 0.02f && child_d(pos, M) < main_d(pos, M);
        const float sc = ch ? M.cs : 1.f;
        const float2 b = ch ? M.cbase : M.base;
        const float hh = ch ? M.h * sc * 1.2f : M.h, RR = M.R * sc, kk = ch ? M.k * 1.1f : M.k, cut = M.cut * sc;
        const float2 bend = ch ? M.bend * (-0.5f * sc) : M.bend;
        const float3 q = f3(pos.x - b.x, pos.y, pos.z - b.y);
        const float3 qc = q - f3(bend.x, hh, bend.y);
        const float capw = smoothstep(-cut - 0.05f * RR - 0.015f, -cut + 0.01f, qc.y);
        const float under = capw * smoothstep(-0.25f, -0.65f, n.y);
        const float rr = length(f2(qc.x, qc.z));
        const float fw = pix * tend;
        // gills: radial lines on the plate, averaged out when finer than the pixel footprint
        const float NG = 26.f;
        float gill = 0.5f + 0.5f * cosf(atan2f(qc.z, qc.x) * NG);
        gill = gill * gill * gill;
        gill = mix(gill, 0.3125f, smoothstep(1.2f, 3.f, NG * fw / fmaxf(SDF_TAU * rr, 1e-4f) * 4.f));
        gill *= smoothstep(0.12f * RR, 0.35f * RR, rr) * (0.55f + 0.45f * smoothstep(RR, 0.4f * RR, rr));
        // spots on the cap top
        const float3 sd = normalize(f3(qc.x / RR, qc.y / (RR * kk), qc.z / RR)) * 3.3f;
        const float3 sc3 = floor(sd);
        const float3 jit = hash33(sc3 + f3(M.ph * 17.f + (ch ? 5.f : 0.f))) - f3(0.5f);
        const float3 sf = sd - sc3 - f3(0.5f) - jit * 0.3f;
        float spot = (1.f - smoothstep(0.15f, 0.23f + fw * 3.f / RR, length(sf))) * step(0.45f, hash13(sc3 + f3(3.1f + M.ph)));
        spot *= capw * (1.f - under) * smoothstep(-0.2f, 0.25f, qc.y / (RR * kk));
        const float fres = 1.f - saturate(dot(n, -rd));
        const float3 gc = mush_col(M, P, 0.f), gc2 = mush_col(M, P, 0.33f);
        const float G = P[31] * mush_boost(M, P) * (ch ? 0.85f : 1.f) * fminf(M.g * 1.5f, 1.f);
        const float yy = saturate(q.y / hh);
        const float3 e_stem = gc * (0.06f + 0.4f * yy * yy * yy);
        const float3 e_top = gc * (0.08f + 0.9f * fres * fres * fres) + gc2 * (spot * 1.8f);
        const float3 e_und = gc * (0.5f + 2.4f * gill);
        const float3 emis = mix(e_stem, mix(e_top, e_und, under), capw) * G;
        const float3 alb = mix(f3(0.30f, 0.28f, 0.25f) * 0.35f + gc * 0.03f, gc * 0.12f + f3(0.02f), capw);
        float ao;
        const float3 li = glade_light(pos + n * 0.02f, n, P, ao);
        col = alb * (li + moon * (0.4f + 0.6f * saturate(n.y)) + f3(0.004f)) + emis;
    } else if (id == 2) {
        auto mapt = [&](float3 q) { return trunk_d(q, TR); };
        const float3 n = calc_normal(mapt, pos, fmaxf(0.5f * pix * tend, 1e-3f));
        const float2 rel = f2(pos.x - TR.c.x, pos.z - TR.c.y);
        const float bark = noise3(f3(rel.x * 3.5f, pos.y * 0.55f, rel.y * 3.5f), 11u + (unsigned int)(TR.hs * 1000.f));
        const float3 alb = f3(0.040f, 0.034f, 0.030f) * (0.45f + 0.9f * bark);
        float ao;
        const float3 li = glade_light(pos + n * 0.05f, n, P, ao);
        col = alb * (li * 1.4f + moon * (0.5f + 0.5f * saturate(n.y + 0.3f)) + f3(0.003f));
        col += fogc * 0.15f * (1.f - saturate(pos.y * 0.15f));
    } else if (id == 3) {
        const float3 n = f3(0.f, 1.f, 0.f);
        const unsigned int sd0 = (unsigned int)P[45];
        const float moss = 0.6f * pvnoise(pos.x * 1.75f, pos.z * 1.75f, (int)(ZPER * S_CELL * 1.75f + 0.5f), sd0 + 3u)
                         + 0.4f * pvnoise(pos.x * 6.f, pos.z * 6.f, (int)(ZPER * S_CELL * 6.f + 0.5f), sd0 + 5u);
        const float3 alb = mix(f3(0.012f, 0.022f, 0.012f), f3(0.05f, 0.08f, 0.035f), moss);
        float ao;
        const float3 li = glade_light(pos, n, P, ao);
        col = alb * (li + moon * 0.8f + f3(0.004f)) * ao;
        // mycelium: warped segments between the node of this cell and its 4 neighbours
        const int cx0 = (int)floorf(pos.x / S_CELL + 0.5f), cz0 = (int)floorf(pos.z / S_CELL + 0.5f);
        const float2 wp = f2(pos.x, pos.z) + f2(pvnoise(pos.x * 1.25f, pos.z * 1.25f, 1100, sd0 + 9u) - 0.5f,
                                                pvnoise(pos.x * 1.25f + 31.7f, pos.z * 1.25f, 1100, sd0 + 13u) - 0.5f) * 0.4f;
        const float fwg = fminf(pix * tend / fmaxf(-rd.y, 0.06f), 0.1f);
        const float lw = 0.012f + 0.5f * fwg;
        const float lnorm = 0.012f / lw;
        const float2 c0 = get_mush(cx0, cz0, P).base;
        float e = 0.f;
        #pragma unroll 1
        for (int j = 0; j < 4; ++j) {
            const int dx = j == 0 ? 1 : (j == 1 ? -1 : 0), dz = j == 2 ? 1 : (j == 3 ? -1 : 0);
            const float2 c1 = get_mush(cx0 + dx, cz0 + dz, P).base;
            const int ex = dx < 0 ? cx0 - 1 : cx0, ez = dz < 0 ? cz0 - 1 : cz0;
            const float eh = hash1((unsigned int)(ex + 65536), (unsigned int)imod(ez, ZPER), (dz != 0 ? 77u : 55u) + sd0);
            if (eh > 0.78f) continue;
            const float2 a = (dx + dz) > 0 ? c0 : c1, bb = (dx + dz) > 0 ? c1 : c0;
            const float2 ab = bb - a, ap = wp - a;
            const float len = length(ab);
            const float s = saturate(dot(ap, ab) / fmaxf(dot(ab, ab), 1e-6f));
            const float d = length(ap - ab * s);
            const float core = expf(-square(d / lw)) * lnorm;
            const float gl = expf(-d / (6.f * lw)) * lnorm;
            const float dash = powf(fract(s * len * 0.8f - P[34] + eh * 7.f), 8.f);
            e += core * (0.35f + 1.8f * dash) + gl * 0.05f * (1.f + 2.f * dash);
        }
        float fil = 1.f - fabsf(2.f * pvnoise(wp.x * 2.25f, wp.y * 2.25f, 1980, sd0 + 21u) - 1.f);
        fil = powf(fil, 40.f) * 0.05f * (1.f - smoothstep(0.02f, 0.06f, fwg));
        const float wv = wave_at(f2(pos.x, pos.z), P, 0.45f);
        const float3 mc = mix(srgb_to_linear(pal(drift + 0.5f + 0.1f * P[25], PAL)), f3(0.7f, 0.85f, 1.f), 0.3f);
        col += mc * ((e + fil) * P[35] * (1.f + 3.f * wv) * P[31] * 0.4f + wv * 0.03f);
    }
    col = fog_exp2(col, fogc, tend, fogd);
    col += halo;

    // ---------------- spores: rising motes on world z slices
    if (rd.z > 0.08f && P[40] > 0.002f) {
        const int k0 = (int)floorf(ro.z / SP_DZ) + 1;
        const unsigned int sd1 = (unsigned int)P[45] + 0x51ed27u;
        #pragma unroll 1
        for (int i = 0; i < SP_N; ++i) {
            const int k = k0 + i;
            const float ts = (k * SP_DZ - ro.z) / rd.z;
            if (ts > tend) break;
            const float3 ps = ro + rd * ts;
            if (ps.y < 0.f || ps.y > SP_ROWS * SP_C) continue;
            const int ix = (int)floorf(ps.x / SP_C), iy = (int)floorf(ps.y / SP_C);
            Rng r; r.s = key3(ix, iy * SP_PER + imod(k, SP_PER), 3u, sd1);
            const float hp = r.next();
            const float w = smooth(P[40], P[40] - 0.08f, hp);
            if (w <= 0.f) continue;
            const float h1 = r.next(), h2 = r.next(), h3 = r.next(), h4 = r.next(), h5 = r.next(), h6 = r.next();
            const float fy = fract(h1 + P[41] * (1.f + floorf(h2 * 4.f)) * 0.25f);
            const float fs = (2.f + floorf(h4 * 5.f)) * 0.25f;
            const float2 c = f2((ix + 0.5f + 0.25f * (2.f * h3 - 1.f)) * SP_C + 0.08f * SP_C * sinf(fs * P[42] + SDF_TAU * h4),
                                (iy + 0.1f + 0.8f * fy) * SP_C);
            const float rs = 0.006f + 0.006f * h5;
            const float re = fmaxf(rs, 1.1f * pix * ts);
            const float d2 = dot2(f2(ps.x - c.x, ps.y - c.y)) / (re * re);
            if (d2 > 9.f) continue;
            const float env = sinf(SDF_PI * fy) * smoothstep(0.35f, 0.9f, ts) * expf(-fogd * fogd * ts * ts);
            const float3 scol = mix(f3(0.75f, 0.85f, 1.f), srgb_to_linear(pal(drift + 0.3f + 0.25f * h6, PAL)), 0.55f);
            col += scol * (P[62] * square(rs / re) * expf(-1.5f * d2) * env * w);
        }
    }
    // ---------------- fireflies: wandering, blinking, bokeh by distance from the focal plane
    if (rd.z > 0.08f && P[37] > 0.002f) {
        const int k0 = (int)floorf(ro.z / FF_DZ) + 1;
        const unsigned int sd2 = (unsigned int)P[45] + 0x2f6b1u;
        const float F = P[44];
        const float sH = H / 1080.f;
        #pragma unroll 1
        for (int i = 0; i < FF_N; ++i) {
            const int k = k0 + i;
            const float ts = (k * FF_DZ - ro.z) / rd.z;
            if (ts > tend) break;
            const float3 ps = ro + rd * ts;
            if (ps.y < 0.f || ps.y > 2.f * FF_CY) continue;
            const int ix = (int)floorf(ps.x / FF_CX), iy = (int)floorf(ps.y / FF_CY);
            Rng r; r.s = key3(ix, iy * FF_PER + imod(k, FF_PER), 5u, sd2);
            const float hp = r.next();
            const float w = smooth(P[37], P[37] - 0.1f, hp);
            if (w <= 0.f) continue;
            const float h1 = r.next(), h2 = r.next(), h3 = r.next(), h4 = r.next(), h5 = r.next(), h6 = r.next();
            const float f1 = (2.f + floorf(h1 * 5.f)) * 0.25f, f2_ = (2.f + floorf(h5 * 5.f)) * 0.25f;
            const float f3_ = (3.f + floorf(h6 * 5.f)) * 0.25f;
            const float ax = 0.5f * FF_CX - 0.3f, ay = 0.5f * FF_CY - 0.3f;
            const float2 c = f2((ix + 0.5f) * FF_CX + ax * sinf(f1 * P[38] + SDF_TAU * h2),
                                (iy + 0.5f) * FF_CY + ay * sinf(f2_ * P[38] + SDF_TAU * h3));
            const float pw = pix * ts;
            const float r0 = 0.011f;
            const float coc = fminf(8.f * fabsf(1.f - F / ts), 34.f) * sH;
            const float rb = fmaxf(fmaxf(r0, coc * pw), 1.2f * pw);
            const float u = length(f2(ps.x - c.x, ps.y - c.y)) / rb;
            if (u > 4.f) continue;
            const float blur = smoothstep(1.3f, 3.f, rb / r0);
            const float disc = 1.f - smoothstep(1.f - 1.5f * pw / rb, 1.f, u);
            const float prof = mix(expf(-2.5f * u * u), disc * (0.75f + 0.3f * u * u), blur) + 0.16f * expf(-0.35f * u * u);
            const float edge = fminf(fminf(ps.x - ix * FF_CX, (ix + 1) * FF_CX - ps.x), fminf(ps.y - iy * FF_CY, (iy + 1) * FF_CY - ps.y));
            const float bl = 0.5f + 0.5f * sinf(f3_ * P[39] + SDF_TAU * h4);
            const float blink = 0.1f + 0.9f * bl * bl * bl * bl;
            const float I = P[63] * fmaxf(square(r0 / rb), 0.1f);
            const float env = smoothstep(0.0f, 0.1f, edge) * smoothstep(0.25f, 0.7f, ts) * expf(-fogd * fogd * ts * ts);
            const float3 fcol = mix(f3(1.f, 0.86f, 0.32f), srgb_to_linear(pal(drift + 0.12f + 0.2f * h4, PAL)), 0.35f);
            col += fcol * (I * blink * w * env * prof);
        }
    }
    col = vignette(col, x, y, W, H, 0.32f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"scell": S_CELL, "zper": ZPER}


class MushroomGlade(Source):
    name = "mushroom_glade"
    label = "Mushroom glade"
    description = ("Creep through a night forest floor of bioluminescent mushrooms: glowing gills and spots, "
                   "pulsing mycelium, drifting spores and blinking firefly bokeh in the fog (3D raymarched).")
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.02),
        "mushroom_density": num(0.0, 1.0, 0.65, "Mushroom density", tier="regional", basic=True),
        "cap_shape": num(0.0, 1.0, 0.35, "Cap shape (bell -> parasol)", tier="regional"),
        "glow_spread": num(0.0, 1.0, 0.5, "Glow spread", tier="regional"),
        "fog": num(0.0, 1.0, 0.45, "Fog", tier="regional"),
        "firefly_count": num(0.0, 1.0, 0.5, "Fireflies", tier="regional"),
        "mycelium": num(0.0, 2.0, 1.0, "Mycelium glow", tier="regional"),
        "creep_speed": num(0.0, 3.0, 1.0, "Camera creep", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("mushroom_glade")
        self.P = cp.zeros(72, cp.float32)
        self.Ph = np.zeros(72, np.float32)
        self.rng = np.random.default_rng(int(params["seed"]) ^ 0x6D757368)
        self.z = 0.0            # camera creep distance (float64, uploaded modulo ZWRAP)
        self.cam_ph = 0.0       # camera sway
        self.flow = 0.0         # mycelium dash phase (wraps 1)
        self.breath = 0.0       # mushroom breathing (wraps PH_PERIOD)
        self.ff_ph = 0.0        # firefly wander (wraps PH_PERIOD)
        self.fl_ph = 0.0        # firefly flicker (wraps PH_PERIOD)
        self.sp_rise = 0.0      # spore rise (wraps 4)
        self.sp_sway = 0.0      # spore sway (wraps PH_PERIOD)
        self.drift = 0.0
        self.e_s = None
        self.prev_pulse = 0.0
        self.since_wave = 1.0
        self.waves = []         # [ox, oz (unwrapped), radius, amp, age]
        self.wave_slot = 0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (pulse only adds to rates and gains)
        self.z += p["creep_speed"] * spd * (0.10 + 0.38 * es ** 1.2 + 0.12 * pulse) * dt
        self.cam_ph += spd * (0.05 + 0.10 * es) * dt
        self.flow = wrap(self.flow + spd * (0.25 + 1.1 * e + 0.8 * pulse) * dt, 1.0)
        self.breath = wrap(self.breath + spd * (0.35 + 1.2 * es) * dt, PH_PERIOD)
        self.ff_ph = wrap(self.ff_ph + spd * (0.18 + 0.75 * es) * dt, PH_PERIOD)
        self.fl_ph = wrap(self.fl_ph + spd * (0.9 + 2.2 * es) * dt, PH_PERIOD)
        self.sp_rise = wrap(self.sp_rise + spd * (0.10 + 0.40 * es) * dt, 4.0)
        self.sp_sway = wrap(self.sp_sway + spd * (0.30 + 0.8 * es) * dt, PH_PERIOD)
        self.drift = wrap(self.drift + p["drift_speed"] * spd * dt, 1000.0)

        # ---- camera: creep along the lane at mushroom height, gentle sway
        zoff = math.floor(self.z / ZWRAP) * ZWRAP
        cz = self.z - zoff
        cph = self.cam_ph
        sway = 0.6 + 0.4 * es
        ex = 0.26 * sway * math.sin(cph)
        ey = 0.82 + 0.09 * math.sin(0.71 * cph + 1.0)
        eye = (ex, ey, cz)
        tgt = (0.55 * math.sin(0.6 * cph + 0.5), 0.48 + 0.1 * math.sin(0.45 * cph + 2.0), cz + 4.0)
        roll = 0.025 * math.sin(0.37 * cph + 0.3) * sway
        cam = camera_pack(eye, tgt, fov_deg=58.0, roll=roll)

        # ---- mycelium waves: spawned on the beat's rising edge, travel outward, decay
        self.since_wave += dt
        if pulse - self.prev_pulse > 0.15 and self.since_wave > 0.12:
            ox = ex + float(self.rng.normal()) * 0.9
            oz = self.z + 2.0 + float(self.rng.uniform(0.0, 3.5))
            w = [ox, oz, 0.0, min(1.0, 0.45 + 0.7 * pulse), 0.0]
            if len(self.waves) < MAX_WAVES:
                self.waves.append(w)
            else:
                self.waves[self.wave_slot] = w
                self.wave_slot = (self.wave_slot + 1) % MAX_WAVES
            self.since_wave = 0.0
        self.prev_pulse = pulse
        v = 2.4 + 1.6 * es
        for wv in self.waves:
            wv[2] += v * dt
            wv[4] += dt

        dens = float(p["mushroom_density"])
        Ph = self.Ph
        Ph[:] = 0.0
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = p["intensity"] * 1.25
        Ph[28] = 0.025 + 0.11 * float(p["fog"])
        Ph[29] = 0.28 + 0.75 * dens
        Ph[30] = p["cap_shape"]
        Ph[31] = (0.45 + 0.55 * e) * (1.0 + 0.35 * pulse)
        Ph[32] = p["glow_spread"]
        Ph[33] = 0.42 * (0.45 + 0.55 * e) * (1.0 + 0.35 * pulse) * (0.6 + 0.8 * float(p["glow_spread"]))
        Ph[34] = self.flow
        Ph[35] = p["mycelium"]
        Ph[36] = self.breath
        Ph[37] = float(p["firefly_count"]) * (0.15 + 0.6 * es ** 1.2)
        Ph[38] = self.ff_ph
        Ph[39] = self.fl_ph
        Ph[40] = 0.06 + 0.22 * es
        Ph[41] = self.sp_rise
        Ph[42] = self.sp_sway
        Ph[43] = pulse
        Ph[44] = 3.2
        Ph[45] = float(int(p["seed"]) % 65521)
        for i, wv in enumerate(self.waves[:MAX_WAVES]):
            amp = wv[3] * math.exp(-wv[4] / 1.5)
            Ph[46 + 4 * i: 50 + 4 * i] = (wv[0], wv[1] - zoff, wv[2], amp if amp > 0.004 else 0.0)
        Ph[62] = 1.0 * (0.5 + 0.5 * e)
        Ph[63] = 3.2 * (0.6 + 0.4 * e) * (1.0 + 0.25 * pulse)
        Ph[64] = 1.0
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
