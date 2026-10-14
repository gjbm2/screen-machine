"""Cloud flyover: a glide just above a sunlit cumulus deck, eroded mountain peaks breaking through it.

Source: Original (inspired by https://github.com/SebLague/Clouds , https://iquilezles.org/articles/morenoise/ ,
        https://iquilezles.org/articles/fbm/)
Author: smfx (original; techniques from Sebastian Lague's Clouds, MIT, and Inigo Quilez article snippets, MIT; iq's 'Clouds' artwork is idea only)
License: MIT
license_class: permissive

Written from scratch for this renderer.  Techniques: slab-bounded volumetric clouds from a precomputed tileable
64^3 noise volume (perlin-worley shape channel + three worley/value detail channels, manual trilinear wrap
fetch), a weather map for large-scale breaks, height-profile rounded cumulus tops, detail erosion (wispy base,
billowy tops), Beer's law with a multiple-scattering floor, a powder term, a two-lobe Henyey-Greenstein phase,
a 3-tap light march towards the sun and blue-noise start jitter (Lague / Schneider style).  The terrain is a
1024^2 tileable heightfield precomputed with iq's derivative ("eroded") fbm from FFT band-limited octaves, plus a
rotated detail layer; it is ray-marched above the deck, checked inside the cloud slab and marched again below
the deck only through holes.  The sky is a small analytic atmosphere (zenith/horizon/sun-glow by sun elevation,
Kasten-Young airmass for the sun colour).  Every texture period divides 128 world units (integer shear and
rotation lattices), so the camera position and the wind offsets wrap invisibly.

Perf: the heaviest scene in the set; default render_scale 0.38 (40 cloud steps, 3 light taps, <= 64 relaxed
terrain steps above the deck and 32 below it).  Lower render_scale if the bench is over budget.

Macros: energy -> flight speed, billow advection and evolution speed, coverage and density contrast (density
gain, erosion), sun intensity and camera swoop amplitude (all via a slewed energy).  pulse -> a silver-lining rim
flash, a cloud-density "breath" (gain) and a flight-rate kick (rates/gains only).  speed scales flight, wander,
swoop and advection; drift moves the time-of-day hue of the palette through the sky and the rim light (tint
amount = sky_tint); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

TILE = 128.0          # world period of everything (terrain tile); camera x/z and wind wrap at this
NV = 64               # noise volume size (voxels per axis)
NH = 1024             # terrain texture size (texels per axis); 8 texels per world unit
THICK = 1.6           # cloud slab thickness (world units)
CLEAR = 0.5           # camera clearance over terrain
_ALT_LVLS = np.linspace(0.50, 0.96, 65)     # terrain quantile of the deck base, cloud_altitude 0 -> 1

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 cloud base | 29 cloud top | 30 coverage
#  31 density gain | 32 erosion | 33..35 sun dir | 36..38 sun colour | 39..41 zenith | 42..44 horizon
#  45..47 sun glow | 48 palette tint | 49 haze | 50 terrain height scale | 51 terrain detail amp | 52 terrain max
#  53,54 shape wind | 55 shape evolution | 56,57 detail wind | 58 detail evolution | 59 rim gain
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define TFAR 95.f
#define TCL 80.f
#define NCS 40
#define SIGK 7.5f

__device__ __forceinline__ float lum(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
// Henyey-Greenstein, normalised so isotropic = 1
__device__ __forceinline__ float hg_n(float mu, float g) {
    const float g2 = g * g;
    const float den = fmaxf(1.f + g2 - 2.f * g * mu, 1e-4f);
    return (1.f - g2) * rsqrtf(den * den * den);
}

// ---- manual trilinear fetch from the 64^3 tileable noise volume (u in texels, wraps); layout (z*64+y)*64+x
__device__ __forceinline__ float4 u4f(uchar4 c) { return make_float4(c.x, c.y, c.z, c.w); }
__device__ __forceinline__ float4 lerp4(float4 a, float4 b, float t) {
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t);
}
__device__ float4 tex3w(const uchar4* __restrict__ T, float ux, float uy, float uz) {
    ux -= 0.5f; uy -= 0.5f; uz -= 0.5f;
    const float fx = floorf(ux), fy = floorf(uy), fz = floorf(uz);
    const float tx = ux - fx, ty = uy - fy, tz = uz - fz;
    const int x0 = ((int)fx) & 63, y0 = ((int)fy) & 63, z0 = ((int)fz) & 63;
    const int x1 = (x0 + 1) & 63, y1 = (y0 + 1) & 63, z1 = (z0 + 1) & 63;
    const int r00 = (z0 * 64 + y0) * 64, r01 = (z0 * 64 + y1) * 64, r10 = (z1 * 64 + y0) * 64, r11 = (z1 * 64 + y1) * 64;
    const float4 a = lerp4(u4f(T[r00 + x0]), u4f(T[r00 + x1]), tx);
    const float4 b = lerp4(u4f(T[r01 + x0]), u4f(T[r01 + x1]), tx);
    const float4 c = lerp4(u4f(T[r10 + x0]), u4f(T[r10 + x1]), tx);
    const float4 d = lerp4(u4f(T[r11 + x0]), u4f(T[r11 + x1]), tx);
    const float4 r = lerp4(lerp4(a, b, ty), lerp4(c, d, ty), tz);
    const float k = 1.f / 255.f;
    return make_float4(r.x * k, r.y * k, r.z * k, r.w * k);
}
__device__ float tex3x(const uchar4* __restrict__ T, float ux, float uy, float uz) {     // channel x only
    ux -= 0.5f; uy -= 0.5f; uz -= 0.5f;
    const float fx = floorf(ux), fy = floorf(uy), fz = floorf(uz);
    const float tx = ux - fx, ty = uy - fy, tz = uz - fz;
    const int x0 = ((int)fx) & 63, y0 = ((int)fy) & 63, z0 = ((int)fz) & 63;
    const int x1 = (x0 + 1) & 63, y1 = (y0 + 1) & 63, z1 = (z0 + 1) & 63;
    const int r00 = (z0 * 64 + y0) * 64, r01 = (z0 * 64 + y1) * 64, r10 = (z1 * 64 + y0) * 64, r11 = (z1 * 64 + y1) * 64;
    const float a = mix((float)T[r00 + x0].x, (float)T[r00 + x1].x, tx);
    const float b = mix((float)T[r01 + x0].x, (float)T[r01 + x1].x, tx);
    const float c = mix((float)T[r10 + x0].x, (float)T[r10 + x1].x, tx);
    const float d = mix((float)T[r11 + x0].x, (float)T[r11 + x1].x, tx);
    return mix(mix(a, b, ty), mix(c, d, ty), tz) * (1.f / 255.f);
}
// ---- bilinear wrap fetch from the 1024^2 terrain texture (x: shaped height 0..1, y: raw eroded fbm 0..1)
__device__ float2 tex2w(const float2* __restrict__ Ht, float ux, float uy) {
    ux -= 0.5f; uy -= 0.5f;
    const float fx = floorf(ux), fy = floorf(uy);
    const float tx = ux - fx, ty = uy - fy;
    const int x0 = ((int)fx) & 1023, y0 = ((int)fy) & 1023;
    const int x1 = (x0 + 1) & 1023, y1 = (y0 + 1) & 1023;
    const float2 a = Ht[y0 * 1024 + x0], b = Ht[y0 * 1024 + x1], c = Ht[y1 * 1024 + x0], d = Ht[y1 * 1024 + x1];
    return f2(mix(mix(a.x, b.x, tx), mix(c.x, d.x, tx), ty), mix(mix(a.y, b.y, tx), mix(c.y, d.y, tx), ty));
}
// terrain height: base tile (8 texels/unit) + a 5x rotated detail layer (integer lattice -> period 128)
__device__ __forceinline__ float ter_h(float x, float z, const float2* __restrict__ Ht, const float* __restrict__ P) {
    const float b = tex2w(Ht, x * 8.f, z * 8.f).x;
    const float d = tex2w(Ht, (4.f * x + 3.f * z) * 8.f + 301.f, (4.f * z - 3.f * x) * 8.f + 87.f).y;
    return P[50] * b + P[51] * (d - 0.5f) * (0.3f + b);
}

// ---- cloud density (0..~1.5) at p; det adds the erosion detail
__device__ float cloud_d(float3 p, const float* __restrict__ P, const uchar4* __restrict__ T3,
                         const float2* __restrict__ Ht, bool det) {
    const float h01 = (p.y - P[28]) / (P[29] - P[28]);
    if (h01 <= 0.f || h01 >= 1.f) return 0.f;
    const float qx = p.x + P[53], qz = p.z + P[54];
    // weather map (the terrain's raw fbm, rotated 90 deg at 2x): large breaks and solid banks
    const float wm = tex2w(Ht, qz * 16.f + 517.f, qx * -16.f + 233.f).y;
    const float cov = saturate(P[30] * (0.4f + 1.2f * smoothstep(0.32f, 0.68f, wm)));
    // height profile: soft base, rounded cumulus tops (only dense cores reach the top)
    const float hp = smoothstep(0.f, 0.14f, h01) * (1.f - smoothstep(0.38f, 1.f, h01));
    // shape: 16-unit period, sheared through the volume so neighbouring tiles see different slices
    const float s = tex3x(T3, qx * 4.f, (p.y + P[55] + 0.375f * qx + 0.625f * qz) * 4.f, qz * 4.f);
    float d = saturate((s * hp - (1.f - cov)) / fmaxf(cov, 0.05f));
    if (d <= 0.f) return 0.f;
    if (det) {
        const float dx = p.x + P[56], dz = p.z + P[57];
        const float4 n = tex3w(T3, dx * 16.f, (p.y + P[58] + 0.375f * dx + 0.625f * dz) * 16.f, dz * 16.f);
        float dn = 0.625f * n.y + 0.25f * n.z + 0.125f * n.w;
        dn = mix(1.f - dn, dn, saturate(h01 * 2.5f));          // wispy base, billowy tops
        const float er = P[32] * dn;
        d = saturate((d - er) / (1.f - er));
    }
    return d * P[31];
}
// 3-tap optical depth towards the sun (shape only)
__device__ __forceinline__ float light_od(float3 p, float3 sd, const float* __restrict__ P,
                                          const uchar4* __restrict__ T3, const float2* __restrict__ Ht) {
    const float L = 0.9f;
    return L * (0.24f * cloud_d(p + sd * (0.12f * L), P, T3, Ht, false)
              + 0.36f * cloud_d(p + sd * (0.42f * L), P, T3, Ht, false)
              + 0.50f * cloud_d(p + sd * (0.88f * L), P, T3, Ht, false));
}

// ---- analytic sky: zenith -> horizon by elevation, sun glow, aureole, palette tint (luminance matched)
__device__ float3 sky_col(float3 d, float3 sd, float3 sunc, const float* __restrict__ P) {
    const float hy = saturate(d.y);
    const float g = 1.f - hy, g2 = g * g, g4 = g2 * g2;
    float3 c = mix(load3(P + 39), load3(P + 42), g4);
    const float mu = dot(d, sd);
    const float m = saturate(mu * 0.5f + 0.5f), m2 = m * m, m4 = m2 * m2;
    c += load3(P + 45) * (m4 * m2 * (0.35f + 0.65f * g4));
    const float s = saturate(mu), s2 = s * s, s4 = s2 * s2, s8 = s4 * s4, s16 = s8 * s8, s32 = s16 * s16;
    c += sunc * (0.05f * s32 * s32 + 0.015f * s8);
    const float3 pc = srgb_to_linear(pal(hy * P[25] + P[26], P + 13));
    return mix(c, pc * (lum(c) / fmaxf(lum(pc), 0.03f)), P[48]);
}

// relaxed heightfield march over [ta, tb]; returns the hit t or -1
__device__ float ter_march(float3 ro, float3 rd, float ta, float tb, int n,
                           const float2* __restrict__ Ht, const float* __restrict__ P) {
    float t = ta, tp = ta, gp = 0.f;
    for (int i = 0; i < n; ++i) {
        if (t > tb) return -1.f;
        const float3 p = ro + rd * t;
        const float gap = p.y - ter_h(p.x, p.z, Ht, P);
        if (gap < 0.0015f * t + 0.002f) {
            if (gap < 0.f && i > 0) return tp + (t - tp) * gp / fmaxf(gp - gap, 1e-5f);
            return t;
        }
        tp = t; gp = gap;
        t += fmaxf(gap * 0.55f, 0.02f + 0.012f * t);
    }
    return -1.f;
}
// ray segment where lo <= y <= hi, clipped to [0, tmax]; empty when t1 <= t0
__device__ __forceinline__ float2 yband(float3 ro, float3 rd, float lo, float hi, float tmax) {
    if (fabsf(rd.y) < 1e-5f) return (ro.y >= lo && ro.y <= hi) ? f2(0.f, tmax) : f2(1.f, 0.f);
    const float i1 = (lo - ro.y) / rd.y, i2 = (hi - ro.y) / rd.y;
    return f2(fmaxf(fminf(i1, i2), 0.f), fminf(fmaxf(i1, i2), tmax));
}

SDF_KERNEL
void cloud_flyover(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                   const uchar4* __restrict__ T3, const float2* __restrict__ Ht, const float* __restrict__ BN)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const float3 sd = load3(P + 33), sunc = load3(P + 36);
    const float cb = P[28], ct = P[29], hmax = P[52], haze = P[49];
    const float jit = BN[(y & 63) * 64 + (x & 63)];
    const float mu = dot(rd, sd);
    const float sunl = lum(sunc);

    const float3 hazec = sky_col(normalize(f3(rd.x, 0.03f + 0.2f * fmaxf(rd.y, 0.f), rd.z)), sd, sunc, P);
    const float3 ambc = sky_col(f3(0.f, 1.f, 0.f), sd, sunc, P) * 0.75f + sky_col(normalize(f3(-sd.x, 0.3f, -sd.z)), sd, sunc, P) * 0.45f;
    const float3 pr = srgb_to_linear(pal(P[26] + 0.5f, P + 13));
    const float3 rimc = mix(sunc * (1.f / fmaxf(sunl, 0.05f)), pr * (1.f / fmaxf(lum(pr), 0.05f)), P[48]);
    const float ph = 0.55f * hg_n(mu, 0.72f) + 0.45f * hg_n(mu, -0.18f);
    const float sm = saturate(mu), sm2 = sm * sm;
    const float fw = sm2 * sm2 * sm;                              // forward lobe for the silver lining
    const float3 deckc = sunc * (0.45f * fminf(ph, 2.5f)) + ambc * 0.6f;

    // ---- A: peaks above the deck top
    float tter = -1.f;
    {
        const float2 s = yband(ro, rd, ct, hmax, TFAR);
        if (s.y > s.x) tter = ter_march(ro, rd, s.x, s.y, 64, Ht, P);
    }
    // ---- B: the cloud slab (terrain checked per sample)
    float T = 1.f, tws = 0.f, ws = 0.f;
    float3 cc = f3(0.f);
    {
        const float2 s = yband(ro, rd, cb, ct, TCL);
        float s0 = s.x, s1 = s.y;
        if (tter > 0.f) s1 = fminf(s1, tter);
        if (s1 > s0) {
            float t = s0 + (0.04f + 0.035f * s0) * jit;
            float dsp = fmaxf(t - s0, 1e-3f);
            bool capped = true;
            for (int i = 0; i < NCS; ++i) {
                if (t >= s1) { capped = false; break; }
                const float ds = 0.04f + 0.035f * t;
                const float3 p = ro + rd * t;
                if (p.y < ter_h(p.x, p.z, Ht, P)) {
                    float lo = t - dsp, hi = t;
                    for (int k = 0; k < 5; ++k) {
                        const float mid = 0.5f * (lo + hi);
                        const float3 q = ro + rd * mid;
                        if (q.y < ter_h(q.x, q.z, Ht, P)) hi = mid; else lo = mid;
                    }
                    tter = hi; capped = false; break;
                }
                const float d = cloud_d(p, P, T3, Ht, true);
                if (d > 0.003f) {
                    const float h01 = saturate((p.y - cb) / (ct - cb));
                    const float od = light_od(p, sd, P, T3, Ht);
                    const float bl = fmaxf(__expf(-od * SIGK), 0.7f * __expf(-0.25f * od * SIGK));
                    const float pw = mix(1.f - __expf(-d * SIGK * 0.8f), 1.f, 0.35f + 0.65f * sm);
                    float3 Ls = sunc * (ph * bl * pw) + ambc * (0.3f + 0.7f * h01);
                    Ls += rimc * (P[59] * sunl * bl * __expf(-d * SIGK * 0.6f) * (0.2f + 2.f * fw));
                    const float a = 1.f - __expf(-d * SIGK * ds);
                    cc += Ls * (T * a);
                    tws += t * T * a; ws += T * a;
                    T *= 1.f - a;
                    if (T < 0.02f) { capped = false; break; }
                }
                dsp = ds;
                t += ds;
            }
            if (capped && t < s1 && T >= 0.02f) {          // far deck beyond the step budget: lit deck colour
                cc += deckc * T; tws += t * T; ws += T; T = 0.f;
            }
        }
    }
    // ---- C: below the deck, only where light still gets through
    if (tter < 0.f && T >= 0.02f && rd.y < 0.f) {
        const float2 s = yband(ro, rd, -2.f, cb, TFAR);
        if (s.y > s.x) tter = ter_march(ro, rd, s.x, s.y, 32, Ht, P);
    }

    float3 back;
    if (tter > 0.f && T >= 0.02f) {
        const float3 pos = ro + rd * tter;
        const float e = fmaxf(0.03f, tter * pix * 2.f);
        const float hx0 = ter_h(pos.x - e, pos.z, Ht, P), hx1 = ter_h(pos.x + e, pos.z, Ht, P);
        const float hz0 = ter_h(pos.x, pos.z - e, Ht, P), hz1 = ter_h(pos.x, pos.z + e, Ht, P);
        const float3 n = normalize(f3(hx0 - hx1, 2.f * e, hz0 - hz1));
        const float cover = smoothstep(ct + 0.2f, cb, pos.y);        // 0 above the deck .. 1 under it
        float sh = 1.f;
        if (cover < 0.99f && tter < 50.f) {
            float st = 0.06f;
            for (int k = 0; k < 10; ++k) {
                const float3 q = pos + sd * st;
                if (q.y > hmax) break;
                sh = fminf(sh, 9.f * (q.y - ter_h(q.x, q.z, Ht, P)) / st);
                if (sh <= 0.f) break;
                st *= 1.55f;
            }
            sh = saturate(sh);
        }
        const float snow = smoothstep(0.5f, 0.78f, n.y) * smoothstep(ct - 0.4f, ct + 1.f, pos.y);
        const float3 rock = mix(f3(0.06f, 0.052f, 0.048f), f3(0.17f, 0.15f, 0.13f), saturate(pos.y / P[50]));
        const float3 alb = mix(rock, f3(0.8f, 0.83f, 0.88f), snow);
        const float dif = saturate(dot(n, sd)) * sh * (1.f - 0.85f * cover);
        const float3 amb = ambc * ((0.45f + 0.55f * n.y) * (1.f - 0.6f * cover));
        const float3 bounce = deckc * (0.3f * saturate(0.6f - 0.6f * n.y) * (1.f - cover));
        back = alb * (sunc * dif + amb + bounce);
        const float fre = 1.f - saturate(dot(n, -rd));
        back += rimc * (P[59] * 0.1f * fre * fre * fre * saturate(dot(n, sd) + 0.3f) * sh * sunl * (1.f - cover));
        back = mix(back, hazec, 1.f - __expf(-haze * tter));
    } else if (rd.y < 0.f) {
        back = hazec;
    } else {
        const float s2 = sm2 * sm2, s8 = s2 * s2 * s2 * s2, s32 = s8 * s8 * s8 * s8, s128 = s32 * s32 * s32 * s32;
        back = sky_col(rd, sd, sunc, P) + sunc * (s128 * s128 * s128 * 18.f);   // sun disc
    }
    if (ws > 1e-4f) cc = mix(cc, hazec * (1.f - T), 1.f - __expf(-haze * tws / ws));
    float3 col = cc + back * T;
    col = vignette(col, x, y, W, H, 0.22f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""

# ------------------------------------------------------------------------------------------------ host precompute
_CACHE: dict = {}


def _rank01(a: np.ndarray) -> np.ndarray:
    flat = a.ravel()
    r = np.empty(flat.size, np.float32)
    r[np.argsort(flat, kind="stable")] = np.linspace(0.0, 1.0, flat.size, dtype=np.float32)
    return r.reshape(a.shape)


def _norm01(a: np.ndarray) -> np.ndarray:
    lo, hi = float(a.min()), float(a.max())
    return ((a - lo) / max(hi - lo, 1e-9)).astype(np.float32)


def _ring(k: np.ndarray, f: float, width: float = 0.45) -> np.ndarray:
    r = np.exp(-0.5 * (np.log2(np.maximum(k, 1e-6) / f) / width) ** 2)
    r[k == 0] = 0.0
    return r


def _worley3(n: int, c: int, rng) -> np.ndarray:
    """Tileable inverted worley F1 on an n^3 grid with c cells per axis (c divides n; 1 at feature points)."""
    s = n // c
    feat = rng.random((c, c, c, 3), dtype=np.float32)
    loc = (np.arange(s, dtype=np.float32) + 0.5) / s                 # voxel position inside its cell
    lz, ly, lx = loc.reshape(1, s, 1, 1, 1, 1), loc.reshape(1, 1, 1, s, 1, 1), loc.reshape(1, 1, 1, 1, 1, s)
    best = np.full((c, s, c, s, c, s), 1e9, np.float32)
    for oz in (-1, 0, 1):
        for oy in (-1, 0, 1):
            for ox in (-1, 0, 1):
                f = np.roll(feat, shift=(-oz, -oy, -ox), axis=(0, 1, 2))     # f[i] = feat[i + o] (wraps)
                fz = (oz + f[..., 0]).reshape(c, 1, c, 1, c, 1)
                fy = (oy + f[..., 1]).reshape(c, 1, c, 1, c, 1)
                fx = (ox + f[..., 2]).reshape(c, 1, c, 1, c, 1)
                np.minimum(best, (fz - lz) ** 2 + (fy - ly) ** 2 + (fx - lx) ** 2, out=best)
    return (1.0 - np.clip(np.sqrt(best), 0.0, 1.0)).reshape(n, n, n)


def _build_volume(seed: int) -> np.ndarray:
    """(64, 64, 64, 4) uint8: R perlin-worley shape (rank-equalised), G/B worley fbm detail, A value fbm."""
    n = NV
    rng = np.random.default_rng(seed * 7919 + 13)
    kz = np.fft.fftfreq(n) * n
    kx = np.fft.rfftfreq(n) * n
    K = np.sqrt(kz[:, None, None] ** 2 + kz[None, :, None] ** 2 + kx[None, None, :] ** 2)
    W1 = np.fft.rfftn(rng.standard_normal((n, n, n)))
    W2 = np.fft.rfftn(rng.standard_normal((n, n, n)))

    def band(Wn, f):
        v = np.fft.irfftn(Wn * _ring(K, f), s=(n, n, n))
        return v / (v.std() + 1e-9)

    pn = _norm01(0.55 * band(W1, 4) + 0.3 * band(W1, 8) + 0.15 * band(W1, 16))
    w = {c: _worley3(n, c, rng) for c in (4, 8, 16, 32)}
    w16b, w32b = _worley3(n, 16, rng), _worley3(n, 32, rng)
    wf1 = 0.625 * w[4] + 0.25 * w[8] + 0.125 * w[16]
    R = _rank01(pn * (0.35 + 0.65 * wf1) + 0.3 * wf1)
    G = _norm01(0.625 * w[8] + 0.25 * w[16] + 0.125 * w[32])
    B = _norm01(0.625 * w16b + 0.375 * w32b)
    A = _norm01(0.6 * band(W2, 16) + 0.4 * band(W2, 32))
    vol = np.stack([R, G, B, A], axis=-1)
    return np.clip(np.round(vol * 255.0), 0, 255).astype(np.uint8)


def _build_terrain(seed: int) -> tuple[np.ndarray, np.ndarray]:
    """(1024, 1024, 2) float32 [shaped height, raw eroded fbm] and the deck-top quantile table."""
    n = NH
    rng = np.random.default_rng(seed * 104729 + 7)
    Wn = np.fft.rfft2(rng.standard_normal((n, n)))
    kz = (np.fft.fftfreq(n) * n)[:, None]
    kx = (np.fft.rfftfreq(n) * n)[None, :]
    K = np.sqrt(kz ** 2 + kx ** 2)
    a = np.zeros((n, n), np.float64)
    dx = np.zeros((n, n), np.float64)
    dz = np.zeros((n, n), np.float64)
    b = 1.0
    for f in (3, 6, 12, 24, 48, 96):                      # iq's derivative fbm on band-limited octaves
        S = Wn * _ring(K, f, 0.4)
        v = np.fft.irfft2(S, s=(n, n))
        sd = v.std() + 1e-12
        ddx = np.fft.irfft2(S * (2j * np.pi * kx), s=(n, n))
        ddz = np.fft.irfft2(S * (2j * np.pi * kz), s=(n, n))
        ds = 0.45 / (0.5 * (ddx.std() + ddz.std()) + 1e-12)
        dx += ddx * ds
        dz += ddz * ds
        a += b * (0.5 + 0.2 * v / sd) / (1.0 + dx * dx + dz * dz)
        b *= 0.5
    raw = _norm01(a)
    shaped = (raw ** 2.4).astype(np.float32)
    qt = np.quantile(shaped[::4, ::4], _ALT_LVLS).astype(np.float64)
    return np.stack([shaped, raw], axis=-1).astype(np.float32), qt


def _blue_noise(seed: int, n: int = 64) -> np.ndarray:
    rng = np.random.default_rng(seed * 31 + 5)
    F = np.fft.fft2(rng.standard_normal((n, n)))
    k = np.sqrt(np.fft.fftfreq(n)[:, None] ** 2 + np.fft.fftfreq(n)[None, :] ** 2)
    F *= np.clip(k / 0.5, 0.0, 1.0) ** 2
    return _rank01(np.fft.ifft2(F).real)


def _assets(seed: int):
    key = int(seed) & 0x7FFFFFFF
    got = _CACHE.get(key)
    if got is None:
        vol = _build_volume(key % 100003)
        hmap, qt = _build_terrain(key % 100019)
        got = (vol, hmap, qt, _blue_noise(key % 1009))
        if len(_CACHE) > 3:
            _CACHE.clear()
        _CACHE[key] = got
    return got


def _sun_colour(el: float) -> tuple[float, float, float]:
    """Sun transmittance through a simple atmosphere (Kasten-Young airmass)."""
    el_deg = math.degrees(el)
    m = 1.0 / (math.sin(max(el, 0.0)) + 0.50572 * (max(el_deg, 0.0) + 6.07995) ** -1.6364)
    return tuple(math.exp(-bk * m) for bk in (0.045, 0.10, 0.23))


class CloudFlyover(Source):
    name = "cloud_flyover"
    label = "Cloud flyover"
    description = ("A majestic glide just above a sunlit cumulus deck while eroded mountain peaks break through it, "
                   "volumetric clouds under an analytic dawn-to-dusk sky.")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.38, period=0.8, drift=0.02),
        "coverage": num(0.15, 0.95, 0.6, "Cloud coverage", tier="regional", basic=True),
        "cloud_altitude": num(0.0, 1.0, 0.5, "Cloud altitude (fewer peaks)", tier="regional"),
        "sun_elevation": num(0.0, 1.0, 0.12, "Time of day (dawn -> noon -> dusk)", tier="regional"),
        "terrain_roughness": num(0.0, 1.0, 0.5, "Terrain roughness", tier="regional"),
        "camera_height": num(0.2, 3.0, 0.8, "Camera height over the deck", tier="regional"),
        "sky_tint": num(0.0, 1.0, 0.4, "Palette tint (sky and rim)", tier="regional"),
        "haze": num(0.0, 1.0, 0.35, "Haze", tier="regional"),
        "rim_flash": num(0.0, 1.0, 0.6, "Silver lining (beat flash)", tier="regional"),
        "flight_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("cloud_flyover")
        vol, hmap, qt, bn = _assets(int(params["seed"]))
        self.hmap = hmap
        self.qt = qt
        self.T3 = cp.asarray(vol)
        self.Ht = cp.asarray(hmap)
        self.BN = cp.asarray(bn)
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        rng = np.random.default_rng(int(params["seed"]) + 4242)
        self.px = float(rng.uniform(0.0, TILE))
        self.pz = float(rng.uniform(0.0, TILE))
        self.cph = float(rng.uniform(0.0, 60.0))      # heading-wander clock
        self.sw = float(rng.uniform(0.0, 2.0 * math.pi))
        self.wind = [float(rng.uniform(0.0, TILE)), float(rng.uniform(0.0, TILE))]
        self.dwind = [float(rng.uniform(0.0, TILE)), float(rng.uniform(0.0, TILE))]
        self.evo = float(rng.uniform(0.0, 16.0))
        self.devo = float(rng.uniform(0.0, 4.0))
        self.drift = 0.0
        self.e_s = None
        self.hd = 0.0
        self.avoid = 0.0
        self.roll = 0.0
        self.eye_y = None
        self.pal = None

    # host replica of the kernel's ter_h (bilinear wrap), for camera clearance and steering
    def _tex2(self, ch: int, ux: float, uy: float) -> float:
        ux -= 0.5
        uy -= 0.5
        fx, fy = math.floor(ux), math.floor(uy)
        tx, ty = ux - fx, uy - fy
        x0, y0 = int(fx) & (NH - 1), int(fy) & (NH - 1)
        x1, y1 = (x0 + 1) & (NH - 1), (y0 + 1) & (NH - 1)
        H = self.hmap
        a, b = float(H[y0, x0, ch]), float(H[y0, x1, ch])
        c, d = float(H[y1, x0, ch]), float(H[y1, x1, ch])
        return (a + (b - a) * tx) * (1.0 - ty) + (c + (d - c) * tx) * ty

    def _ter_h(self, x: float, z: float, hs: float, da: float) -> float:
        b = self._tex2(0, x * 8.0, z * 8.0)
        d = self._tex2(1, (4.0 * x + 3.0 * z) * 8.0 + 301.0, (4.0 * z - 3.0 * x) * 8.0 + 87.0)
        return hs * b + da * (d - 0.5) * (0.3 + b)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = smoothstep(0.05, 0.9, e)
        a_e = 1.0 - math.exp(-dt / 1.0) if dt > 0 else 1.0
        self.e_s = ep if self.e_s is None else self.e_s + (ep - self.e_s) * a_e
        es = self.e_s
        k = pulse * (0.35 + 0.65 * e)

        # ---- terrain and deck geometry (regional, eased by the param easer)
        rough = float(p["terrain_roughness"])
        hs = 7.0 * (0.8 + 0.4 * rough)
        da = 0.2 + 1.0 * rough
        hmax = hs + 0.5 * da * 1.3 + 0.05
        cb = hs * float(np.interp(float(p["cloud_altitude"]), np.linspace(0.0, 1.0, len(self.qt)), self.qt))
        ct = cb + THICK                                   # the deck sits above most terrain; peaks pierce it

        # ---- flight: wandering heading that steers away from the higher side, pulse kicks the rate
        v = float(p["flight_speed"]) * spd * (0.45 + 2.0 * es) * (1.0 + 0.9 * k)
        self.cph += spd * (0.35 + 0.6 * es) * dt
        c = self.cph
        wander = 0.32 * math.sin(0.13 * c) + 0.16 * math.sin(0.051 * c + 1.3)
        look = 5.0 + 1.5 * v
        hl = self._ter_h(self.px + math.sin(self.hd - 0.5) * look, self.pz + math.cos(self.hd - 0.5) * look, hs, da)
        hr = self._ter_h(self.px + math.sin(self.hd + 0.5) * look, self.pz + math.cos(self.hd + 0.5) * look, hs, da)
        avoid_t = max(-0.6, min(0.6, 0.16 * (hl - hr)))
        self.avoid += (avoid_t - self.avoid) * (1.0 - math.exp(-dt / 1.2) if dt > 0 else 1.0)
        hd_t = max(-1.0, min(1.0, wander + self.avoid))
        hd_prev = self.hd
        self.hd += (hd_t - self.hd) * (1.0 - math.exp(-dt / 0.8) if dt > 0 else 1.0)
        h_rate = (self.hd - hd_prev) / dt if dt > 0 else 0.0
        sh, chd = math.sin(self.hd), math.cos(self.hd)
        self.px = (self.px + sh * v * dt) % TILE
        self.pz = (self.pz + chd * v * dt) % TILE

        # ---- altitude: deck height + energy swoop, lifted over peaks ahead (fast up, slow down)
        self.sw = wrap(self.sw + spd * (0.22 + 0.3 * es) * dt, 2.0 * math.pi * 16.0)
        camh = float(p["camera_height"])
        amp = (0.05 + 0.4 * es) * (0.6 + 0.4 * min(camh, 1.5))
        base_y = ct + max(0.12, camh + amp * math.sin(self.sw))
        ahead = max(self._ter_h(self.px + sh * dd, self.pz + chd * dd, hs, da) for dd in (0.0, 1.5, 3.0, 5.0, 8.0))
        target = max(base_y, ahead + CLEAR)
        if self.eye_y is None or dt <= 0:
            self.eye_y = target if self.eye_y is None else self.eye_y
        else:
            tau = 0.35 if target > self.eye_y else 1.4
            self.eye_y += (target - self.eye_y) * (1.0 - math.exp(-dt / tau))
        lift = max(0.0, self.eye_y - base_y)
        roll_t = max(-0.12, min(0.12, -1.4 * h_rate)) + (0.01 + 0.03 * es) * math.sin(0.5 * self.sw + 0.7)
        self.roll += (roll_t - self.roll) * (1.0 - math.exp(-dt / 0.6) if dt > 0 else 1.0)
        pitch = -0.07 - 0.04 * min(camh, 3.0) - 0.035 * min(lift, 2.0) + 0.025 * math.sin(self.sw + 0.4)
        fwd = (sh * math.cos(pitch), math.sin(pitch), chd * math.cos(pitch))
        eye = (self.px, self.eye_y, self.pz)
        cam = camera_pack(eye, (eye[0] + fwd[0], eye[1] + fwd[1], eye[2] + fwd[2]), fov_deg=55.0, roll=self.roll)

        # ---- clouds: advection + evolution (energy), coverage/density contrast, pulse density breath
        wr = spd * (0.12 + 0.5 * es)
        self.wind[0] = (self.wind[0] + 0.85 * wr * dt) % TILE
        self.wind[1] = (self.wind[1] - 0.35 * wr * dt) % TILE
        self.dwind[0] = (self.dwind[0] + 1.35 * wr * dt) % TILE
        self.dwind[1] = (self.dwind[1] - 0.2 * wr * dt) % TILE
        self.evo = (self.evo + spd * (0.03 + 0.1 * es) * dt) % 16.0
        self.devo = (self.devo + spd * (0.06 + 0.22 * es) * dt) % 4.0
        cov = min(0.98, max(0.05, float(p["coverage"]) * (0.85 + 0.3 * es)))
        dens = (0.55 + 0.9 * es ** 1.3) * (1.0 + 0.35 * k)
        erosion = 0.18 + 0.3 * es

        # ---- sun and sky by time of day
        tod = float(p["sun_elevation"])
        el = math.radians(2.5 + 55.0 * math.sin(math.pi * min(max(tod, 0.0), 1.0)) ** 1.3)
        az = -0.7 + 1.4 * tod
        sd = (math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az))
        day = smoothstep(math.radians(3.0), math.radians(40.0), el)
        sun_i = 3.2 * (0.8 + 0.45 * es)
        sunt = _sun_colour(el)
        sunc = tuple(s * sun_i for s in sunt)
        zen = tuple((lo + (hi - lo) * day) * (0.6 + 0.4 * day) for lo, hi in
                    zip((0.05, 0.085, 0.24), (0.09, 0.21, 0.52)))
        hor = tuple((lo + (hi - lo) * day) * (0.6 + 0.4 * day) for lo, hi in
                    zip((0.85, 0.55, 0.40), (0.55, 0.68, 0.86)))
        glow = tuple(s * (0.05 + 0.3 * (1.0 - day)) for s in sunc)

        # ---- palette (session palette wins; eased so palette switches glide)
        rows = self.session_palette()
        tgt = (np.asarray(rows, np.float64).reshape(12) if rows is not None
               else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if self.pal is None or dt <= 0:
            self.pal = tgt.copy() if self.pal is None else self.pal
        else:
            self.pal += (tgt - self.pal) * (1.0 - math.exp(-dt / 1.0))
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = self.pal
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (0.95 + 0.1 * es)
        Ph[28] = cb
        Ph[29] = ct
        Ph[30] = cov
        Ph[31] = dens
        Ph[32] = erosion
        Ph[33:36] = sd
        Ph[36:39] = sunc
        Ph[39:42] = zen
        Ph[42:45] = hor
        Ph[45:48] = glow
        Ph[48] = p["sky_tint"]
        hz = float(p["haze"])
        Ph[49] = 0.002 + 0.03 * hz * hz
        Ph[50] = hs
        Ph[51] = da
        Ph[52] = hmax
        Ph[53] = self.wind[0]
        Ph[54] = self.wind[1]
        Ph[55] = self.evo
        Ph[56] = self.dwind[0]
        Ph[57] = self.dwind[1]
        Ph[58] = self.devo
        Ph[59] = float(p["rim_flash"]) * (0.5 + 2.2 * k)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P,
                                           self.T3, self.Ht, self.BN))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.T3 = None
        self.Ht = None
        self.BN = None
