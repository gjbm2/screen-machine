"""Lava lake: inside a volcanic caldera, a lake of black crust plates split by incandescent cracks.

Source: Original (inspired by https://iquilezles.org/articles/voronoilines/ and
        https://iquilezles.org/articles/smoothvoronoi/)
Author: smfx (original; iq's voronoilines and smoothvoronoi article snippets, MIT)
License: MIT (original)
license_class: permissive

The lake is a shallow heightfield: advected, jostling Voronoi plates (3x3 cells, border distance from the two
nearest points, iq's voronoilines idea) with rounded raised crust, marched as a slab (linear search + secant
refine).  Cracks between plates glow on a blackbody ramp taken from pal(); plates carry a fine crackle network
that rides with them, some cells are open molten pools with convection and bubbles that dome up and burst.
Lava fountains (host-managed, up to 6) swell a molten dome, throw a glowing jet and a spray of droplets on
ballistic arcs, and light the crust around them.  The crater wall is an SDF surface of revolution with rugged
noise and strata, lit from below by the lake; smoke is a short volumetric march lit from below; embers are
hashed analytic sprites on 7 world-anchored parallax planes; heat haze bends the camera rays.

Macros: energy -> crust flow speed, crack width and glow, molten fraction, bubble count, ember count and rise
speed, fountain frequency and size, haze strength and camera sway.  pulse -> a lava burst (fountain bulge and
jet amplitude, crack glow) plus an ember spray (a sheet of extra embers lifting off the lake on each beat,
spawned on the host-detected rising edge) and sometimes a new fountain.  speed scales flow, ember and fountain
rates; drift slides the palette back and forth across the blackbody ramp; intensity is output gain.
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

TAU = 2.0 * math.pi
MAX_F = 6            # fountain slots
NLAYER = 7           # ember parallax planes
NDROP = 16           # droplets per fountain
GRAV = 5.0           # heavy, slow lava arcs
FBASE = 96
FSTRIDE = 14
NP = 192
LAKE_R = 34.0
WALL_TOP = 15.0
FLOW_DIR = np.array([0.3, -1.0]) / math.hypot(0.3, 1.0)       # plates drift towards the camera
EMBER_D = [2.2 * 1.5 ** i for i in range(NLAYER)]             # plane distance ahead of the eye
EMBER_CS = [0.024 * d for d in EMBER_D]                       # ember cell size (world units)
EMBER_V = [1.15, 0.85, 1.05, 0.95, 1.1, 0.9, 1.0]             # per-plane rise speed variation

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift shift | 27 gain | 28 1/crust size | 29,30 flow offset
#  31 cell jostle phase | 32 crust height | 33 crack width (cells) | 34 glow gain | 35,36 warp phases
#  37 smoke | 38 smoke wind | 39 smoke rise | 40 haze px | 41 haze phase | 42 sulphur | 43 molten fraction
#  44 bubble phase (fract) | 45 ember density | 46 ember sway | 47 twinkle | 48 fountain light | 49 lake radius
#  50 pulse | 51 crack heat | 53 bubble density | 54 flame phase | 55 wall top | 56..61 spray (age, amp) x3
#  62 ember gain | 64..70 ember rise (cells) | 72..78 ember cell size | 80..86 ember plane z
#  96 + 14 i fountains: fx fz bulge_h bulge_r jet_h jet_r tau v0 bright bound heat_r age seed pad
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MAX_F %(maxf)d
#define NLAYER %(nlayer)d
#define NDROP %(ndrop)d
#define GRAV (%(grav)ff)
#define FBASE %(fbase)d
#define FSTRIDE %(fstride)d

// cheap lattice hash, periodic in 1024 cells (host phases wrap at 1024, so wraps are invisible)
__device__ __forceinline__ unsigned int lh(int ix, int iy, unsigned int seed) {
    return hash_u(((unsigned int)(ix & 1023)) * 0x8da6b343u ^ ((unsigned int)(iy & 1023)) * 0xd8163841u ^ seed);
}
__device__ __forceinline__ float lhf(int ix, int iy, unsigned int seed) {
    return (lh(ix, iy, seed) >> 8) * (1.f / 16777216.f);
}
__device__ float lnoise(float x, float y, unsigned int seed) {
    const float fx = floorf(x), fy = floorf(y);
    const int ix = (int)fx, iy = (int)fy;
    float tx = x - fx, ty = y - fy;
    tx = tx * tx * (3.f - 2.f * tx); ty = ty * ty * (3.f - 2.f * ty);
    const float a = lhf(ix, iy, seed), b = lhf(ix + 1, iy, seed);
    const float c = lhf(ix, iy + 1, seed), d = lhf(ix + 1, iy + 1, seed);
    return mix(mix(a, b, tx), mix(c, d, tx), ty);
}

// Voronoi with jostling points: nearest point (rel = p - centre), its hash, and the distance to the border with the
// second-nearest point (iq's voronoilines bisector formula, single 3x3 pass).
struct Vor { float edge; float f1; float2 rel; unsigned int h; };
__device__ __forceinline__ Vor voronoi(float2 u, float aph, unsigned int seed) {
    const float fx = floorf(u.x), fy = floorf(u.y);
    const int ix = (int)fx, iy = (int)fy;
    const float gx = u.x - fx, gy = u.y - fy;
    float d1 = 1e9f, d2 = 1e9f;
    float2 r1 = f2(0.f), r2 = f2(1.f, 0.f);
    unsigned int h1 = 0u;
    #pragma unroll
    for (int j = -1; j <= 1; ++j) {
        #pragma unroll
        for (int i = -1; i <= 1; ++i) {
            const unsigned int h = lh(ix + i, iy + j, seed);
            const float a1 = aph + 6.2831853f * ((h & 0xffffu) * (1.f / 65536.f));
            const float a2 = aph + 6.2831853f * ((h >> 16) * (1.f / 65536.f));
            const float2 r = f2(i + 0.5f + 0.36f * sinf(a1) - gx, j + 0.5f + 0.36f * sinf(a2) - gy);
            const float d = dot(r, r);
            if (d < d1) { d2 = d1; r2 = r1; d1 = d; r1 = r; h1 = h; }
            else if (d < d2) { d2 = d; r2 = r; }
        }
    }
    Vor v;
    v.edge = dot(0.5f * (r1 + r2), normalize(r2 - r1));
    v.f1 = sqrtf(d1); v.rel = -r1; v.h = h1;
    return v;
}

// world xz -> advected, gently warped plate coordinates (cell units)
__device__ __forceinline__ float2 lake_uv(float2 w, const float* __restrict__ P) {
    float2 q = w;
    q.x += 0.8f * sinf(w.y * 0.13f + P[35]);
    q.y += 0.8f * sinf(w.x * 0.11f + P[36]);
    return q * P[28] - f2(P[29], P[30]);
}
__device__ __forceinline__ float plate_h(const Vor& v, const float* __restrict__ P, float& mol, float& hz) {
    const unsigned int h2 = hash_u(v.h ^ 0x9e3779b9u);
    hz = (h2 & 255u) * (1.f / 255.f);
    const float hm = ((h2 >> 8) & 255u) * (1.f / 255.f);
    mol = smoothstep(hm, hm + 0.1f, P[43]);
    const float cw = P[33];
    const float prof = smoothstep(cw, cw + 0.12f, v.edge);
    return P[32] * (0.62f + 0.38f * hz) * prof * (1.f - 0.22f * fminf(v.f1, 1.f)) * (1.f - 0.85f * mol);
}
__device__ __forceinline__ float lake_h(float2 w, const float* __restrict__ P, unsigned int seed) {
    const Vor v = voronoi(lake_uv(w, P), P[31], seed);
    float mol, hz;
    return plate_h(v, P, mol, hz);
}

// crater wall: a flaring bowl (surface of revolution) with rugged noise, cut off at a ragged rim
__device__ __forceinline__ float wall_d(float3 p, const float* __restrict__ P, unsigned int seed, bool detail) {
    const float r = sqrtf(p.x * p.x + p.z * p.z);
    const float y = fmaxf(p.y, 0.f);
    float rw = P[49] + y * (0.55f + 0.02f * y);
    rw += 3.0f * (lnoise(p.x * 0.09f, p.y * 0.12f, seed + 31u) + lnoise(p.z * 0.09f + 40.f, p.y * 0.12f, seed + 37u) - 1.f);
    rw += 0.3f * sinf(p.y * 1.3f + 0.6f * sinf(p.x * 0.11f + p.z * 0.07f));         // strata ledges
    if (detail)
        rw += 0.55f * (lnoise(p.x * 0.45f, p.y * 0.6f, seed + 41u) + lnoise(p.z * 0.45f + 9.f, p.y * 0.6f, seed + 43u) - 1.f)
            + 0.18f * (lnoise(p.x * 1.4f + p.z * 0.5f, p.y * 1.8f, seed + 45u) - 0.5f);
    const float top = P[55] + 3.f * lnoise(p.x * 0.05f, p.z * 0.05f, seed + 47u);
    return fmaxf((rw - r) * 0.6f, p.y - top);
}

// blackbody-ish emission: hue from the palette ramp (hot end at index 0), steep luminance, white-hot core;
// blue mixes towards burning-sulphur flame
__device__ __forceinline__ float3 heat_col(float T, const float* __restrict__ P, float blue) {
    T = saturate(T);
    const float idx = (1.f - T) * 0.40f * P[25] + P[26];
    float3 c = srgb_to_linear(pal(idx, P + 13));
    const float I = T * T * (0.25f + 2.75f * T);
    const float T2 = T * T, T4 = T2 * T2;
    c = c * I + f3(1.f, 0.82f, 0.55f) * (0.9f * T4 * T4 * T2);
    if (blue > 0.f) c = mix(c, f3(0.10f, 0.32f, 1.15f) * (0.9f * I + 0.1f * T), saturate(blue));
    return c;
}

// smoky sky above the rim, lit orange from below
__device__ __forceinline__ float3 sky_col(float3 rd, float3 glowc, const float* __restrict__ P, unsigned int seed) {
    const float up = fmaxf(rd.y, 0.f);
    const float k = 1.f / (up + 0.12f);
    const float n = 0.65f * lnoise(rd.x * k * 1.6f + P[38] * 2.f, rd.z * k * 1.6f, seed + 71u)
                  + 0.35f * lnoise(rd.x * k * 4.1f - P[38] * 3.f, rd.z * k * 4.1f + 5.f, seed + 73u);
    const float plume = smoothstep(0.3f, 0.85f, n);
    const float hor = expf(-up * 5.f);
    return f3(0.006f, 0.005f, 0.006f) + glowc * (0.05f * hor + 0.10f * plume * (0.3f + 0.7f * hor));
}

__device__ __forceinline__ float smoke_den(float3 p, const float* __restrict__ P, unsigned int seed) {
    const float a = lnoise(p.x * 0.07f + P[38], p.y * 0.09f - P[39], seed + 61u);
    const float b = lnoise(p.z * 0.16f - P[38] * 2.f, p.y * 0.2f - P[39] * 2.f, seed + 67u);
    const float n = saturate((0.62f * a + 0.38f * b - 0.36f) * 2.4f);
    return n * expf(-fmaxf(p.y - 2.f, 0.f) * 0.07f) * smoothstep(-0.3f, 1.2f, p.y);
}

SDF_KERNEL
void lava_lake(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye;

    // ---- heat haze: smooth rising shimmer, strongest low in the frame (over the lake)
    float px = x + 0.5f, py = y + 0.5f;
    {
        const float amp = P[40] * (H / 1080.f) * smoothstep(0.2f, 0.7f, (float)y / H);
        if (amp > 0.f) {
            const float sx = (float)x / H * 7.f, sy = (float)y / H * 5.f;
            px += (lnoise(sx, sy + P[41], seed + 5u) - 0.5f) * amp;
            py += (lnoise(sx + 17.f, sy + P[41], seed + 9u) - 0.5f) * amp;
        }
    }
    const float3 rd = cam_dir(cam, px, py, W, H);
    const float pix = pixel_angle(cam, H);
    const float3 glowc = heat_col(0.72f, P, 0.f) * (P[34] * 0.55f);   // average light of the lake
    const float flamp = 1.f + P[48];                                  // fountains brighten everything
    const float TMAX = 160.f;

    // ---- lake: slab march over the crust heightfield (linear search, secant refine)
    float t_lake = 1e9f;
    if (rd.y < -1e-4f) {
        const float Hm = P[32];
        const float ta = fmaxf((Hm - ro.y) / rd.y, 0.f);
        const float tb = -ro.y / rd.y;
        const float tc = fminf(tb, ta + 12.f);
        const int NS = 14;
        const float dtt = (tc - ta) / NS;
        float3 p0 = ro + rd * ta;
        float dprev = p0.y - lake_h(f2(p0.x, p0.z), P, seed);
        bool found = false;
        for (int i = 1; i <= NS; ++i) {
            const float t = ta + dtt * i;
            const float3 p = ro + rd * t;
            const float d = p.y - lake_h(f2(p.x, p.z), P, seed);
            if (d <= 0.f) {
                float t0 = t - dtt, d0 = fmaxf(dprev, 0.f), t1 = t, d1 = d;
                for (int k = 0; k < 4; ++k) {
                    const float tm = t0 + (t1 - t0) * d0 / fmaxf(d0 - d1, 1e-6f);
                    const float3 pm = ro + rd * tm;
                    const float dm = pm.y - lake_h(f2(pm.x, pm.z), P, seed);
                    if (dm <= 0.f) { t1 = tm; d1 = dm; } else { t0 = tm; d0 = dm; }
                }
                t_lake = t0 + (t1 - t0) * d0 / fmaxf(d0 - d1, 1e-6f);
                found = true;
                break;
            }
            dprev = d;
        }
        if (!found) t_lake = tc;
    }

    // ---- crater wall: sphere-trace from where the ray leaves the inner shore circle
    float t_wall = 1e9f;
    {
        const float Rin = P[49] - 3.5f;
        const float a = rd.x * rd.x + rd.z * rd.z;
        if (a > 1e-6f) {
            const float b = ro.x * rd.x + ro.z * rd.z;
            const float c = ro.x * ro.x + ro.z * ro.z - Rin * Rin;
            const float disc = b * b - a * c;
            const float t0 = disc > 0.f ? fmaxf((-b + sqrtf(disc)) / a, 0.f) : 0.f;
            const float tlim = fminf(t_lake, TMAX);
            float t = t0;
            for (int i = 0; i < 40 && t < tlim; ++i) {
                const float3 p = ro + rd * t;
                const float d = wall_d(p, P, seed, false);
                if (d < 0.0015f * t) { t_wall = t; break; }
                if (rd.y > 0.f && p.y > P[55] + 4.f) break;              // over the rim: sky
                t += d;
            }
        }
    }

    // ---- fountain domes (analytic half-ellipsoids)
    float t_dome = 1e9f; int fdome = -1;
    for (int i = 0; i < MAX_F; ++i) {
        const float* F = P + FBASE + FSTRIDE * i;
        const float bh = F[2];
        if (bh < 0.01f) continue;
        const float br = F[3];
        const float sc = br / bh;
        float3 o = ro - f3(F[0], 0.f, F[1]); o.y *= sc;
        float3 d = rd; d.y *= sc;
        const float a = dot(d, d), b = dot(o, d), c = dot(o, o) - br * br;
        const float disc = b * b - a * c;
        if (disc <= 0.f) continue;
        const float t = (-b - sqrtf(disc)) / a;
        if (t > 0.f && t < t_dome && ro.y + rd.y * t >= -0.01f) { t_dome = t; fdome = i; }
    }

    float t = fminf(fminf(t_lake, t_wall), t_dome);
    int id = -1;                                                         // 0 lake, 1 wall, 2 dome
    if (t < 1e8f) id = (t == t_dome && fdome >= 0) ? 2 : (t == t_wall ? 1 : 0);

    float3 col;
    if (id < 0) {
        col = sky_col(rd, glowc, P, seed) * flamp;
        t = TMAX;
    } else if (id == 0) {
        // ================= lake surface
        const float3 pos = ro + rd * t;
        const float2 u = lake_uv(f2(pos.x, pos.z), P);
        const Vor v = voronoi(u, P[31], seed);
        float mol, hz;
        const float h0 = plate_h(v, P, mol, hz);
        const float e = fmaxf(0.006f, t * pix * 1.2f);
        const float hx = lake_h(f2(pos.x + e, pos.z), P, seed);
        const float hzz = lake_h(f2(pos.x, pos.z + e), P, seed);
        float3 n = normalize(f3(h0 - hx, e, h0 - hzz));
        const float fw = t * pix * P[28] / sqrtf(fmaxf(-rd.y, 0.03f));     // footprint in cells

        // fountains: melt the crust around them and light it
        float fheat = 0.f, flight = 0.f;
        for (int i = 0; i < MAX_F; ++i) {
            const float* F = P + FBASE + FSTRIDE * i;
            const float bri = F[8];
            if (bri <= 0.002f) continue;
            const float dx = pos.x - F[0], dz = pos.z - F[1];
            fheat = fmaxf(fheat, bri * expf(-(dx * dx + dz * dz) / (F[10] * F[10])));
            const float3 Lv = f3(F[0], 0.35f + 0.45f * F[4], F[1]) - pos;
            const float l2 = dot(Lv, Lv);
            flight += bri * (0.4f + F[4]) / (1.f + l2 * 0.35f) * saturate(dot(n, Lv * rsqrtf(l2)));
        }
        fheat = saturate(fheat);
        const float cw = P[33] * (1.f + 1.5f * fheat);
        const float crack = 1.f - smoothstep(cw * 0.3f, cw + fw, v.edge);
        const float core = 1.f - smoothstep(0.f, cw * 0.55f + fw, v.edge);
        const float rim = expf(-fmaxf(v.edge - cw, 0.f) * 11.f) * (1.f - crack);
        // fine crackle in plate-local coordinates (rides with its plate)
        const unsigned int h3 = hash_u(v.h + 0x51ed27u);
        const float2 lu = v.rel * 3.4f + f2((h3 & 1023u) * 0.1f, ((h3 >> 10) & 1023u) * 0.1f);
        const Vor v2 = voronoi(lu, 0.f, seed + 13u);
        const float fw2 = fw * 3.4f;
        const float crackle = (1.f - smoothstep(0.f, 0.045f + fw2, v2.edge)) * saturate(1.f - fw2 * 3.f);
        const float conv = lnoise(u.x * 2.f + P[54], u.y * 2.f, seed + 19u);
        float T = crack * (0.6f + 0.4f * core) * P[51];
        T = fmaxf(T, mol * (0.55f + 0.3f * conv));
        T = fmaxf(T, crackle * (0.22f + 0.3f * hz) * P[51] * (1.f - mol));
        T = fmaxf(T, fheat * (0.68f + 0.27f * conv));

        // bubbles: dome up under a cooler skin, then burst into an expanding ring
        {
            const float wb = saturate(mol + fheat + 0.6f * crack);
            if (wb > 0.02f) {
                const float2 bu = u * 2.f;
                const float bx = floorf(bu.x), by = floorf(bu.y);
                const unsigned int hb = lh((int)bx, (int)by, seed + 77u);
                if ((hb & 255u) * (1.f / 256.f) < P[53]) {
                    const float s = fract(P[44] + ((hb >> 8) & 1023u) * (1.f / 1024.f));
                    const float2 bc = f2(bx + 0.3f + 0.4f * ((hb >> 18) & 63u) * (1.f / 63.f),
                                         by + 0.3f + 0.4f * ((hb >> 24) & 63u) * (1.f / 63.f));
                    const float2 dv = bu - bc;
                    const float db = length(dv);
                    const float fb = fw * 2.f + 0.02f;
                    const float rmax = 0.24f;
                    if (s < 0.8f) {
                        const float rb = rmax * smoothstep(0.f, 0.8f, s);
                        const float inside = 1.f - smoothstep(rb - fb, rb + fb, db);
                        const float ring = expf(-square((db - rb) / (0.035f + fb)));
                        T = mix(T, fmaxf(T, 0.85f), ring * wb);
                        T = mix(T, T * (0.55f + 0.45f * db / fmaxf(rb, 1e-3f)), inside * wb);
                        n = normalize(n + f3(dv.x, 0.f, dv.y) * (inside * wb * 2.5f / fmaxf(rb, 0.02f)));
                    } else {
                        const float k = (s - 0.8f) * 5.f;
                        const float rr = rmax * (1.f + 1.6f * k);
                        const float ring = expf(-square((db - rr) / (0.03f + fb))) * (1.f - k);
                        const float splash = expf(-db * db / 0.004f) * (1.f - k);
                        T = fmaxf(T, (0.9f * ring + splash) * wb);
                    }
                }
            }
        }
        // blue sulphur flames licking along the cracks
        const float fl = P[42] > 0.f
            ? P[42] * smoothstep(0.4f, 0.8f, lnoise(u.x * 2.f, u.y * 2.f - P[54] * 2.f, seed + 23u)) : 0.f;
        const float3 em = heat_col(T, P, fl * crack) * P[34] + heat_col(0.6f, P, 0.f) * (rim * 0.22f * P[34]);
        const float3 alb = f3(0.040f, 0.036f, 0.037f) * (0.65f + 0.7f * hz) * (1.f - saturate(T * 1.5f));
        const float3 irr = glowc * ((0.25f + 0.6f * rim) * flamp) + heat_col(0.85f, P, 0.f) * (flight * 1.6f);
        const float fre = 1.f - saturate(dot(n, -rd));
        const float fre2 = fre * fre;
        const float3 spec = sky_col(reflect(rd, n), glowc, P, seed) * ((0.04f + 0.6f * fre2 * fre2 * fre) * (1.f - saturate(T * 2.f)));
        col = alb * irr + spec + em;
    } else if (id == 1) {
        // ================= crater wall, lit from below by the lake
        const float3 pos = ro + rd * t;
        auto mapd = [&](float3 p) { return wall_d(p, P, seed, true); };
        auto mapc = [&](float3 p) { return wall_d(p, P, seed, false); };
        const float3 n = calc_normal(mapd, pos, fmaxf(pix * t * 0.8f, 0.01f));
        const float r = sqrtf(pos.x * pos.x + pos.z * pos.z) + 1e-3f;
        const float3 ldir = normalize(f3(-pos.x / r, -0.9f, -pos.z / r));
        const float yy = fmaxf(pos.y, 0.f);
        const float strata = 0.5f + 0.5f * sinf(pos.y * 2.1f + 1.5f * lnoise(pos.x * 0.15f, pos.z * 0.15f, seed + 81u));
        const float tex = lnoise(pos.x * 0.8f + pos.z * 0.3f, pos.y * 1.1f, seed + 83u);
        float3 alb = f3(0.075f, 0.062f, 0.058f) * (0.55f + 0.6f * tex) * (0.8f + 0.4f * strata);
        const float dep = smoothstep(0.62f, 0.8f, lnoise(pos.x * 0.35f, pos.y * 0.5f + pos.z * 0.35f, seed + 87u))
                        * smoothstep(2.f, 7.f, yy);
        alb = mix(alb, f3(0.32f, 0.27f, 0.07f), dep * (0.25f + 0.6f * P[42]));     // sulphur deposits
        const float fall = 1.f / (1.f + 0.010f * yy * yy);
        const float wrapl = 0.2f + 0.8f * saturate(dot(n, ldir));
        const float ao = calc_ao(mapc, pos, n, 1.2f, 2);
        const float3 irr = glowc * (2.4f * fall * wrapl * flamp) + f3(0.008f, 0.007f, 0.008f);
        const float3 em = heat_col(0.9f, P, 0.f) * (P[34] * 0.8f * expf(-yy * 3.5f));   // lava lapping at the foot
        col = alb * irr * (0.5f + 0.5f * ao) + em;
    } else {
        // ================= fountain dome: a molten swell shedding rafts of crust
        const float3 pos = ro + rd * t;
        const float* F = P + FBASE + FSTRIDE * fdome;
        const float br = F[3], bh = F[2];
        const float3 q = pos - f3(F[0], 0.f, F[1]);
        const float3 n = normalize(f3(q.x / (br * br), q.y / (bh * bh), q.z / (br * br)));
        const float ex = 3.f / (br * (1.f + 0.35f * fmaxf(F[11], 0.f)));
        const float sk = smoothstep(0.55f, 0.75f, lnoise(q.x * ex + F[0], q.z * ex + F[1], seed + 91u));
        const float rad = length(f2(q.x, q.z)) / br;
        const float T = mix(0.97f, 0.5f, sk * smoothstep(0.15f, 0.6f, rad)) * (0.9f + 0.1f * P[50]);
        const float fre = 1.f - saturate(dot(n, -rd));
        col = heat_col(T, P, 0.f) * (P[34] * 1.1f) + sky_col(reflect(rd, n), glowc, P, seed) * (0.3f * fre * fre);
    }

    // ---- fountain jets and droplets (emissive, depth-tested against the scene)
    for (int i = 0; i < MAX_F; ++i) {
        const float* F = P + FBASE + FSTRIDE * i;
        if (F[8] <= 0.002f) continue;
        const float3 oc = f3(F[0], 0.f, F[1]) - ro;
        const float tcb = dot(oc, rd);
        if (dot(oc, oc) - tcb * tcb > F[9] * F[9]) continue;         // bounding sphere
        const float gain = P[34] * F[8];
        if (F[4] > 0.02f) {                                           // jet: a wobbling, tapering column
            const float a = rd.x * rd.x + rd.z * rd.z;
            const float tc = fmaxf((oc.x * rd.x + oc.z * rd.z) / fmaxf(a, 1e-6f), 0.f);
            if (tc < t + 0.5f) {
                const float yc = ro.y + rd.y * tc;
                if (yc > 0.f) {
                    const float dx = ro.x + rd.x * tc - F[0], dz = ro.z + rd.z * tc - F[1];
                    const float dp = sqrtf(dx * dx + dz * dz);
                    const float hy = saturate(yc / F[4]);
                    const float wob = 0.75f + 0.5f * lnoise(yc * 2.2f - F[11] * 4.f, F[0] * 3.f, seed + 97u);
                    const float rj = F[5] * (1.1f - 0.6f * hy) * wob * (1.f - smoothstep(0.8f * F[4], 1.05f * F[4], yc));
                    if (rj > 1e-3f) {
                        const float core = smoothstep(rj, rj * 0.4f, dp);
                        const float halo = 0.25f * rj * rj / (dp * dp + rj * rj);
                        col += heat_col(0.93f - 0.2f * hy, P, 0.f) * (gain * (1.4f * core + halo));
                    }
                }
            }
        }
        if (F[6] > 0.f) {                                             // droplets on ballistic arcs
            const unsigned int fs = (unsigned int)F[12];
            for (int k = 0; k < NDROP; ++k) {
                const unsigned int hk = hash_u(fs * 0x9e3779b1u + (unsigned int)k * 0x85ebca77u);
                const unsigned int hk2 = hash_u(hk ^ 0x68e31da4u);
                const float tau = F[6] - 0.7f * ((hk2 & 255u) * (1.f / 255.f));
                if (tau <= 0.f) continue;
                const float phi = 6.2831853f * ((hk & 1023u) * (1.f / 1024.f));
                const float th = 0.12f + 0.55f * (((hk >> 10) & 1023u) * (1.f / 1024.f));
                const float sv = F[7] * (0.5f + 0.55f * (((hk >> 20) & 1023u) * (1.f / 1024.f)));
                const float vh = sv * sinf(th), vy = sv * cosf(th);
                const float dy = vy * tau - 0.5f * GRAV * tau * tau;
                if (dy < 0.f) continue;
                const float3 oq = f3(F[0] + vh * cosf(phi) * tau, dy, F[1] + vh * sinf(phi) * tau) - ro;
                const float tq = dot(oq, rd);
                if (tq <= 0.05f || tq > t + 0.3f) continue;
                const float dd2 = fmaxf(dot(oq, oq) - tq * tq, 0.f);
                const float rr = 0.045f + 0.05f * ((hk2 >> 8) & 255u) * (1.f / 255.f) + 0.6f * pix * tq;
                const float r2 = rr * rr;
                const float g = r2 / (dd2 + r2);
                const float core = smoothstep(r2, 0.25f * r2, dd2);
                col += heat_col(0.95f - 0.22f * tau, P, 0.f) * (P[34] * fminf(F[8] * 1.5f, 1.f) * (1.3f * core + 0.3f * g * g));
            }
        }
    }

    // ---- distance haze and volumetric smoke, both lit from below
    col = mix(col, glowc * (0.05f * flamp), 1.f - expf(-fminf(t, TMAX) * 0.006f));
    if (P[37] > 0.001f) {
        const float tend = fminf(t, 80.f);
        const int NSM = 6;
        const float ds = tend / NSM;
        const float j = hash1(x, y, 911u);
        float trans = 1.f;
        float3 insc = f3(0.f);
        for (int i = 0; i < NSM; ++i) {
            const float ts = (i + j) * ds;
            const float3 p = ro + rd * ts;
            const float den = smoke_den(p, P, seed) * P[37];
            if (den <= 0.f) continue;
            const float a = 1.f - expf(-den * ds * 0.06f);
            const float3 lit = glowc * (0.45f * expf(-fmaxf(p.y, 0.f) * 0.12f) * flamp) + f3(0.006f, 0.0055f, 0.006f);
            insc += lit * (trans * a);
            trans *= 1.f - a;
        }
        col = col * trans + insc;
    }

    // ---- rising embers: hashed sprites on world-anchored parallax planes (+ beat sprays lifting off the lake)
    for (int L = 0; L < NLAYER; ++L) {
        if (rd.z < 0.05f) break;
        const float tl = (P[80 + L] - ro.z) / rd.z;
        if (tl <= 0.2f || tl > t) continue;
        const float3 q = ro + rd * tl;
        if (q.y < -0.05f) continue;
        const float cs = P[72 + L];
        const float ux = q.x / cs, uy = q.y / cs - P[64 + L];
        const float cx = floorf(ux), cy = floorf(uy);
        const unsigned int he = lh((int)cx, (int)cy, seed + 101u + 7u * L);
        const unsigned int he2 = hash_u(he ^ 0x2545f491u);
        const float ha = (he & 1023u) * (1.f / 1024.f);
        float wgt = smoothstep(ha - 0.04f, ha, P[45]);
        const int slot = (int)((he >> 10) %% 3u);
        const float front = 0.3f + 3.5f * P[56 + 2 * slot];
        const float sw = P[57 + 2 * slot] * smoothstep(ha - 0.04f, ha, P[45] + 0.45f) * (1.f - smoothstep(front - 0.6f, front, q.y));
        wgt = fmaxf(wgt, sw);
        if (wgt <= 0.002f) continue;
        const float ex = cx + 0.22f + 0.56f * ((he2 & 255u) * (1.f / 255.f))
                       + 0.14f * sinf(P[46] + 6.2831853f * (((he2 >> 8) & 255u) * (1.f / 255.f)));
        const float ey = cy + 0.22f + 0.56f * (((he2 >> 16) & 255u) * (1.f / 255.f));
        const float dx = ux - ex, dy = uy - ey;
        const float sig = 0.07f + 0.05f * (float)(NLAYER - 1 - L) / (NLAYER - 1);
        const float g = expf(-(dx * dx + dy * dy) / (sig * sig));
        if (g < 0.003f) continue;
        const float life = expf(-fmaxf(q.y, 0.f) * 0.16f) * smoothstep(-0.05f, 0.25f, q.y);
        const float tw = 0.65f + 0.35f * sinf(P[47] * (float)(1u + ((he2 >> 24) & 3u)) + 6.2831853f * ha);
        const float Te = (0.55f + 0.4f * (((he >> 20) & 255u) * (1.f / 255.f))) * (0.6f + 0.4f * life);
        col += heat_col(Te, P, 0.f) * (g * wgt * life * tw * P[62]);
    }

    col = vignette(col * P[27], x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""" % {"maxf": MAX_F, "nlayer": NLAYER, "ndrop": NDROP, "grav": GRAV, "fbase": FBASE, "fstride": FSTRIDE}


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class LavaLake(Source):
    name = "lava_lake"
    label = "Lava lake"
    description = ("Inside a volcanic caldera: black crust plates drift on a lake split by incandescent cracks, "
                   "with lava fountains, bursting bubbles, heat haze, rising embers and smoke (3D).")
    tags = {"family": "fire", "dims": "3d", "toolkit": "sdf3d", "technique": ["heightfield", "noise", "particles"],
            "moods": ["fiery", "aggressive", "dark"], "style": "wild", "energy": [0.5, 1.0]}
    params = {
        **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.02),
        "crust_size": num(0.5, 4.0, 1.4, "Crust plate size", unit="m", tier="regional", basic=True),
        "crack_width": num(0.0, 1.0, 0.45, "Crack width", tier="regional"),
        "flow_speed": num(0.0, 3.0, 1.0, "Crust flow speed", unit="×", tier="realtime", bind="rate"),
        "smoke": num(0.0, 1.0, 0.45, "Smoke", tier="regional"),
        "camera_height": num(0.5, 8.0, 1.8, "Camera height", unit="m", tier="regional"),
        "fountains": num(0.0, 2.0, 1.0, "Lava fountains", tier="regional"),
        "sulphur": num(0.0, 1.0, 0.0, "Blue sulphur flames", tier="regional"),
        "heat_haze": num(0.0, 1.0, 0.5, "Heat haze", tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("lava_lake")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        seed = int(params["seed"])
        self.seed_u = np.uint32((seed * 2654435761) & 0xFFFFFFFF)
        self.rng = np.random.default_rng(seed)
        rng = self.rng
        self.flow = rng.uniform(0.0, 64.0, 2)
        self.cell_ph = float(rng.uniform(0.0, TAU))
        self.warp1 = float(rng.uniform(0.0, TAU))
        self.warp2 = float(rng.uniform(0.0, TAU))
        self.wind = float(rng.uniform(0.0, 64.0))
        self.rise = float(rng.uniform(0.0, 64.0))
        self.haze_ph = 0.0
        self.bub = 0.0
        self.flame = 0.0
        self.sway = 0.0
        self.twinkle = 0.0
        self.ember_rise = rng.uniform(0.0, 64.0, NLAYER)
        self.cam_ph = float(rng.uniform(0.0, TAU))
        self.drift = 0.0
        self.e_s = None
        self.prev_pulse = 0.0
        self.since_kick = 1.0
        self.spray_age = np.full(3, 10.0)
        self.spray_amp = np.zeros(3)
        self.spray_slot = 0
        self.fount: list = [None] * MAX_F
        self.f_acc = 0.5
        self.f_seed = int(rng.integers(1, 1 << 20))
        self.since_spawn = 10.0
        self.preseed = True
        self.pal = None

    def close(self) -> None:
        self.buf = None
        self.P = None

    # ------------------------------------------------------------------ fountains
    def _spawn(self, eye, yaw: float, scale: float, age_frac: float = 0.0) -> None:
        free = [i for i, f in enumerate(self.fount) if f is None]
        if not free:
            return
        rng = self.rng
        d = float(rng.uniform(6.0, 24.0))
        lat = float(rng.uniform(-0.5, 0.5)) * d * 0.9
        fx = eye[0] + math.sin(yaw) * d + math.cos(yaw) * lat
        fz = eye[2] + math.cos(yaw) * d - math.sin(yaw) * lat
        r = math.hypot(fx, fz)
        if r > LAKE_R - 5.0:
            fx, fz = fx * (LAKE_R - 5.0) / r, fz * (LAKE_R - 5.0) / r
        dur = float(rng.uniform(3.2, 4.6))
        self.f_seed = (self.f_seed + 7919) % (1 << 22)
        self.fount[free[0]] = {"x": fx, "z": fz, "age": age_frac * dur, "dur": dur,
                               "A": float(rng.uniform(0.65, 1.2)) * scale, "seed": self.f_seed}
        self.since_spawn = 0.0

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = (1.0 - math.exp(-dt / 0.6)) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        e13 = e ** 1.3

        # palette: session palette wins; eased so a change never snaps
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if self.pal is None:
            self.pal = tp.copy()
        elif dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1.0)

        # ---- beat: rising edge -> ember spray slot (+ sometimes a fresh fountain)
        self.since_kick += dt
        kick = pulse - self.prev_pulse > 0.15 and self.since_kick > 0.12
        self.prev_pulse = pulse
        self.spray_age += dt
        self.spray_amp *= math.exp(-dt / 0.8)
        if kick:
            self.since_kick = 0.0
            self.spray_slot = (self.spray_slot + 1) % 3
            self.spray_age[self.spray_slot] = 0.0
            self.spray_amp[self.spray_slot] = min(1.0, 0.35 + 0.65 * pulse)
        spray_total = min(1.0, float(self.spray_amp.sum()))

        # ---- integrate rates (kicks only add to rates)
        S = float(p["crust_size"])
        flow = float(p["flow_speed"]) * spd * (0.07 + 0.23 * e + 0.05 * pulse)
        self.flow = np.array([wrap(v, 1024.0) for v in self.flow + FLOW_DIR * flow * dt])
        self.cell_ph = wrap(self.cell_ph + spd * (0.10 + 0.22 * e) * dt, TAU)
        self.warp1 = wrap(self.warp1 + spd * (0.04 + 0.07 * e) * dt, TAU)
        self.warp2 = wrap(self.warp2 + spd * (0.035 + 0.06 * e) * dt, TAU)
        self.wind = wrap(self.wind + spd * (0.015 + 0.03 * e) * dt, 1024.0)
        self.rise = wrap(self.rise + spd * (0.05 + 0.10 * e) * dt, 1024.0)
        self.haze_ph = wrap(self.haze_ph + spd * (0.7 + 0.8 * e) * dt, 1024.0)
        self.bub = wrap(self.bub + spd * (0.22 + 0.35 * e) * dt, 1.0)
        self.flame = wrap(self.flame + spd * (0.5 + 0.8 * e) * dt, 1024.0)
        self.sway = wrap(self.sway + spd * (0.7 + 0.6 * e) * dt, TAU)
        self.twinkle = wrap(self.twinkle + spd * 5.0 * dt, TAU)
        self.cam_ph += spd * (0.045 + 0.06 * es) * dt
        rise_ms = (0.45 + 0.95 * e + 0.5 * spray_total) * spd
        for L in range(NLAYER):
            self.ember_rise[L] = wrap(self.ember_rise[L] + rise_ms * EMBER_V[L] / EMBER_CS[L] * dt, 1024.0)

        # ---- camera: on the shore looking across the lake at the far crater wall
        ch = float(p["camera_height"])
        sway = 0.45 + 0.55 * es
        cph = self.cam_ph
        ex = 4.0 * sway * math.sin(cph)
        ez = -14.0 + 1.2 * math.sin(0.61 * cph + 1.0)
        ey = ch + 0.18 * sway * math.sin(0.83 * cph + 0.3)
        yaw = 0.2 * sway * math.sin(0.71 * cph + 0.5) - 0.05 * math.sin(cph)
        eye = (ex, ey, ez)
        ty = 0.65 * ch - 2.1 + 0.15 * sway * math.sin(0.53 * cph)
        target = (ex + math.sin(yaw) * 20.0, ty, ez + math.cos(yaw) * 20.0)
        cam = camera_pack(eye, target, fov_deg=48.0, roll=0.012 * sway * math.sin(0.47 * cph))

        # ---- fountains: spawn (energy sets the frequency, beats add bursts), age, pack
        fq = float(p["fountains"])
        self.since_spawn += dt
        if fq > 0.0:
            if self.preseed:
                self._spawn(eye, yaw, 0.7 + 0.5 * e, age_frac=0.3)
                self.f_acc = 0.3
            self.f_acc += fq * (0.05 + 0.4 * e ** 1.5) * spd * dt
            if self.f_acc >= 1.0:
                self.f_acc -= 1.0
                self._spawn(eye, yaw, (0.7 + 0.5 * e) * min(fq, 1.3))
            if kick and self.since_spawn > 1.0 and self.rng.random() < (0.2 + 0.5 * e) * min(fq, 1.0):
                self._spawn(eye, yaw, (0.8 + 0.5 * e) * min(fq, 1.3))
        self.preseed = False

        Ph = self.Ph
        Ph[FBASE:FBASE + FSTRIDE * MAX_F] = 0.0
        flight = 0.0
        for i, f in enumerate(self.fount):
            if f is None:
                continue
            f["age"] += dt * spd
            s = f["age"] / f["dur"]
            if s >= 1.0:
                self.fount[i] = None
                continue
            A = f["A"]
            Ap = A * (1.0 + 0.8 * pulse)
            env = _ss(0.0, 0.12, s) * (1.0 - _ss(0.75, 1.0, s))
            bh = Ap * 0.5 * _ss(0.0, 0.3, s) * (1.0 - _ss(0.42, 0.72, s))
            br = 0.6 + 0.5 * A
            jh = Ap * 2.4 * _ss(0.26, 0.38, s) * (1.0 - _ss(0.5, 0.8, s))
            jr = 0.14 + 0.1 * A
            tau = (s - 0.3) * f["dur"]
            v0 = math.sqrt(2.0 * GRAV * 2.4 * A) * 0.95
            bright = env * A * (1.0 + 0.5 * pulse)
            reach = 1.15 * v0 * v0 / GRAV + br + 0.5
            vmax = max(v0 * v0 / (2.0 * GRAV), jh) + 0.8
            bound = math.sqrt(reach * reach + vmax * vmax)
            Ph[FBASE + FSTRIDE * i:FBASE + FSTRIDE * (i + 1)] = (
                f["x"], f["z"], bh, br, jh, jr, tau, v0, bright, bound, br * 1.7, f["age"], float(f["seed"]), 0.0)
            flight += bright * (0.3 + jh / 2.4)

        # ---- pack
        Ph[0:13] = cam
        Ph[13:25] = self.pal
        Ph[25] = p["palette_period"]
        Ph[26] = 0.11 * math.sin(TAU * self.drift)
        Ph[27] = float(p["intensity"]) * 1.1 * (1.0 + 0.12 * pulse)
        Ph[28] = 1.0 / S
        Ph[29], Ph[30] = self.flow
        Ph[31] = self.cell_ph
        Ph[32] = 0.09 * S + 0.03
        Ph[33] = (0.02 + 0.1 * float(p["crack_width"])) * (0.7 + 0.6 * e)
        Ph[34] = (0.55 + 0.75 * e) * (1.0 + 0.45 * pulse)
        Ph[35] = self.warp1
        Ph[36] = self.warp2
        Ph[37] = float(p["smoke"]) * (0.85 + 0.3 * e)
        Ph[38] = self.wind
        Ph[39] = self.rise
        Ph[40] = float(p["heat_haze"]) * 6.0 * (0.6 + 0.6 * e)
        Ph[41] = self.haze_ph
        Ph[42] = p["sulphur"]
        Ph[43] = 0.03 + 0.12 * e
        Ph[44] = self.bub
        Ph[45] = 0.1 + 0.6 * e13
        Ph[46] = self.sway
        Ph[47] = self.twinkle
        Ph[48] = min(2.0, 0.6 * flight)
        Ph[49] = LAKE_R
        Ph[50] = pulse
        Ph[51] = 0.8 + 0.25 * e
        Ph[53] = 0.15 + 0.4 * e
        Ph[54] = self.flame
        Ph[55] = WALL_TOP
        for k in range(3):
            Ph[56 + 2 * k] = self.spray_age[k]
            Ph[57 + 2 * k] = self.spray_amp[k]
        Ph[62] = (0.8 + 0.5 * e) * (1.0 + 0.6 * spray_total)
        Ph[64:64 + NLAYER] = self.ember_rise
        Ph[72:72 + NLAYER] = EMBER_CS
        Ph[80:80 + NLAYER] = [ez + d for d in EMBER_D]
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.seed_u))
        present(self.ctx, self.buf, out)
