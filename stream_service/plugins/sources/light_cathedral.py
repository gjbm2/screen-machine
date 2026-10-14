"""Light cathedral: a procession down an endless gothic nave of stained glass, haze and coloured light shafts.

Source: Original (inspired by https://en.wikipedia.org/wiki/Gothic_architecture and
        https://iquilezles.org/articles/distfunctions/ ; SDF building blocks from the smfx sdf3d toolkit)
Author: smfx (original; sdf3d toolkit primitives)
License: MIT (original)
license_class: permissive

A Sainte-Chapelle-like nave raymarched with the sdf3d toolkit.  Bays repeat along z (pMod); each bay is a groin
vault (the union of a longitudinal and a transverse pointed tunnel) with transverse, wall, diagonal and ridge ribs
that run down into clustered piers, and a central boss.  The side walls, the floor and the far west wall are
analytic planes (never in the SDF, so grazing rays never crawl).  Each bay holds a pair of tall stained-glass
lancets with medallions, quarries and lead lines under a quatrefoil oculus; the west wall carries a spinning
twelve-fold rose window over a row of lancets.  The west wall stays a fixed distance ahead of the camera, so the
rose hangs at the end of the procession.

Light: a low sun shines through the lancets on both sides.  Its coloured shafts, and the rose's shafts running
back down the nave, are integrated through incense haze with a 12-step jittered march.  Each sample projects
analytically onto the window planes (no shadow rays).  The same projection paints coloured pools on the floor
and the piers, and the polished floor mirrors the glass analytically.

Macros: energy -> shaft intensity, haze density, procession speed, tracery/rib neon glow (rising steeply above
~0.4), camera sway, window brightness.  pulse -> rose-window flare, rose-shaft and boss flare, a kick to the
rose spin and procession rates (rates only).  speed scales every rate; drift rotates the stained-glass palette;
intensity is output gain.
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

BAY = 4.4            # bay length along the nave (fixed, so the procession never slides when the nave reshapes)
RECESS = 0.75        # window wall plane sits this far behind the pier line
D_END = 46.0         # west wall (rose window) distance ahead of the camera
NBAY = 64            # bay-colour hash period (camera z wraps at NBAY * BAY: seamless)
YW0 = 1.5            # window sill height
NP = 96

# P layout (floats)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define I_HW 28
#define I_XW 29
#define I_YS 30
#define I_C 31
#define I_R 32
#define I_H 33
#define I_B 34
#define I_CT 35
#define I_RT 36
#define I_COL 37
#define I_ZE 38
#define I_YR 39
#define I_RR 40
#define I_SPIN 41
#define I_CLR 42
#define I_HAZE 43
#define I_NEON 45
#define I_ROSE 46
#define I_LS1 47
#define I_LS2 50
#define I_LR 53
#define I_S1 56
#define I_S2 57
#define I_HPH 58
#define I_PULSE 59
#define I_WIN 60
#define I_SEED 61
#define I_CLOUD 62
#define I_AW 63
#define I_RO 64
#define I_YW0 65
#define I_CL 66
#define I_RL 67
#define I_LSPR 68
#define I_VO 69
#define I_ROC 70
#define I_ELS 71
#define I_AMB 72
#define I_STEP 73
#define I_SR 74
#define I_ROSEB 75

__device__ __forceinline__ float gridline(float x, float s) { return s * (0.5f - fabsf(fract(x / s) - 0.5f)); }
__device__ __forceinline__ float3 jewel(float3 c) { return c / (vmax(c) + 0.12f); }

// ------------------------------------------------------------------------------------------------ geometry
__device__ __forceinline__ float pillar_d(float px, float pz, float rb, float y, const float* __restrict__ P) {
    const float s = P[I_COL], ys = P[I_YS];
    const float rc = mix(0.58f, 0.36f, s);
    float d = rb - rc;
    const float ax = fabsf(px), az = fabsf(pz);
    const float r4 = 0.2f * smoothstep(0.f, 0.5f, s) - 0.06f * smoothstep(0.5f, 1.f, s);
    const float r8 = 0.13f * smoothstep(0.45f, 1.f, s);
    const float rs = rc + 0.02f;
    if (r4 > 0.004f) d = fminf(d, fminf(length(f2(ax - rs, az)), length(f2(ax, az - rs))) - r4);
    if (r8 > 0.004f) d = fminf(d, length(f2(ax - 0.7071f * rs, az - 0.7071f * rs)) - r8);
    const float oct = fmaxf(fmaxf(ax, az), (ax + az) * 0.70710678f);
    d = fminf(d, fmaxf(oct - 0.84f, y - 0.55f));                                  // plinth
    d = fminf(d, fmaxf(oct - 0.72f, y - 0.9f));                                   // second step
    d = fminf(d, fmaxf(oct - (rc + 0.34f), fabsf(y - (ys - 0.22f)) - 0.2f));     // capital
    d = fminf(d, fmaxf(rb - (rc + 0.18f), fabsf(y - (ys - 0.58f)) - 0.07f));     // necking ring
    return fmaxf(d, y - ys);
}

// id: 1 vault, 2 pier, 3 rib, 4 boss.  Floor, side walls and west wall are analytic (not here).
__device__ float scene(float3 p, const float* __restrict__ P, int& id) {
    const float B = P[I_B], ys = P[I_YS], hw = P[I_HW];
    const float qz = p.z - B * floorf(p.z / B + 0.5f);            // pier-centred
    const float qz2 = p.z - B * (floorf(p.z / B) + 0.5f);         // bay-centred
    const float ax = fabsf(p.x), yy = p.y - ys;
    const float dl = length(f2(ax + P[I_C], yy)) - P[I_R];       // longitudinal pointed tunnel (raw)
    const float dt = length(f2(fabsf(qz2) + P[I_CT], yy)) - P[I_RT];   // transverse tunnel (raw)
    float d = fmaxf(fmaxf(-dl, -dt), -yy);                         // solid = above springing, outside both
    id = 1;
    const float px = ax - hw;
    const float rb = length(f2(px, qz));
    if (rb - 1.0f < d) {
        const float dp = pillar_d(px, qz, rb, p.y, P);
        if (dp < d) { d = dp; id = 2; }
    }
    const float s = P[I_COL];
    const float rc = mix(0.58f, 0.36f, s);
    const float lift = smoothstep(ys - 0.3f, ys + 2.0f, p.y);
    const float off = mix(rc + 0.03f, 0.10f, lift);
    const float RR = mix(mix(0.07f, 0.15f, s), 0.15f, lift);
    float dr = 1e9f;
    if (fabsf(qz) - 0.2f < d) {                                   // transverse rib + its shaft down the pier
        const float dlm = length(f2(ax + P[I_C], fmaxf(yy, 0.f))) - P[I_R];
        dr = length(f2(dlm + off, qz)) - RR;
    }
    if (fabsf(px) - 0.2f < d) {                                   // wall rib on the pier line + side shafts
        const float dtm = length(f2(fabsf(qz2) + P[I_CT], fmaxf(yy, 0.f))) - P[I_RT];
        dr = fminf(dr, length(f2(dtm + off, px)) - RR * 0.9f);
    }
    if (yy > -0.2f) {
        dr = fminf(dr, fmaxf(length(f2((dl - dt) * 0.5f, fminf(dl, dt) + 0.1f)) - 0.13f, -yy));  // diagonals
        dr = fminf(dr, length(f2(p.x, dl + 0.08f)) - 0.105f);                                     // ridge
    }
    if (dr < d) { d = dr; id = 3; }
    const float db = length(f3(p.x, p.y - (ys + P[I_H] - 0.2f), qz2)) - 0.3f;
    if (db < d) { d = db; id = 4; }
    return d;
}

// ------------------------------------------------------------------------------------------------ glass
// sun through bay b on side sd (hash-varied, drifting with the cloud phase)
__device__ __forceinline__ float bay_sun(int bay, float sd, const float* __restrict__ P) {
    const float hb = hash1((unsigned int)(bay & 63), sd > 0.f ? 7u : 13u, (unsigned int)P[I_SEED]);
    return 0.22f + 0.78f * smoothstep(0.3f, 0.92f, 0.5f + 0.5f * sinf(SDF_TAU * hb + P[I_CLOUD]));
}

// Side-wall window at bay-local (u, v) (u = 0 at bay centre, v = height): linear glass transmission colour
// (0 on stone).  fine: lead lines and crisp edges (fw = pixel footprint); otherwise soft (shafts, reflections).
__device__ float3 side_window(float u, float v, int bay, float sd, const float* __restrict__ P, float fw,
                              bool fine, float& bars) {
    bars = 0.f;
    const float ys = P[I_YS], aw = P[I_AW], yw0 = P[I_YW0];
    const float au = fabsf(u);
    const float sd_out = fmaxf(fmaxf(length(f2(au + P[I_CT], fmaxf(v - ys, 0.f))) - P[I_RO], au - aw), yw0 - v);
    if (sd_out > 0.25f) return f3(0.f);
    const float lu = au - 0.5f * aw;
    const float sd_l = fmaxf(length(f2(fabsf(lu) + P[I_CL], fmaxf(v - P[I_LSPR], 0.f))) - P[I_RL],
                             yw0 + 0.22f - v);
    const float vo = v - P[I_VO];
    const float sd_o = length(f2(u, vo)) - P[I_ROC];
    const float bw = 0.06f;
    const float tr = fminf(fminf(fabsf(sd_l), fabsf(sd_o)), fabsf(sd_out)) - bw;     // < 0 on a stone bar
    bars = (1.f - smoothstep(-fw, fw, tr)) * (1.f - smoothstep(-fw, fw, sd_out - bw));
    const float g = 1.f - smoothstep(-fw, fw, fmaxf(sd_out, -tr));
    if (g <= 0.f) return f3(0.f);
    const unsigned int sb = (unsigned int)(bay & 63) * 4u + (sd > 0.f ? 1u : 0u);
    float off, cellh, lead = 1e9f, vary = 1.f;
    if (sd_o < 0.f) {                                            // quatrefoil oculus
        const float rr = (sd_o + P[I_ROC]) / P[I_ROC];
        const bool inner = rr < 0.42f;
        const unsigned int sec = (u > 0.f ? 1u : 0u) + (vo > 0.f ? 2u : 0u);
        off = inner ? 0.62f : 0.30f + 0.14f * (float)(sec & 1u);
        cellh = hash1(sb, 71u + (inner ? 0u : 1u + sec), 3u);
        if (fine) lead = fminf(fabsf(rr - 0.42f) * P[I_ROC], fminf(fabsf(u), fabsf(vo)));
    } else if (sd_l < 0.f) {                                     // lancet: medallions over diamond quarries
        const float vv = v - yw0;
        const float mp = 2.1f;
        const float mi = floorf(vv / mp);
        const float mv = vv - (mi + 0.5f) * mp;
        const float rm = 0.78f * (0.5f * aw - 0.03f);
        const float dm = length(f2(lu, mv)) - rm;
        const unsigned int li = (u > 0.f ? 1u : 0u), mu = (unsigned int)(int)mi & 255u;
        if (dm < 0.f) {
            const unsigned int sec = (lu > 0.f ? 1u : 0u) + (mv > 0.f ? 2u : 0u);
            off = 0.5f;
            cellh = hash1(sb, mu * 8u + li * 4u + sec, 5u);
            if (fine) lead = fminf(fabsf(dm), fminf(fabsf(lu), fabsf(mv)));
        } else {
            off = 0.f;
            cellh = 0.4f * hash1(sb, mu * 8u + li, 9u);
            if (fine) {
                const float a = (lu + vv) * 0.70710678f, b = (lu - vv) * 0.70710678f;
                lead = fminf(fminf(fabsf(dm), gridline(vv, 0.7f)), fminf(gridline(a, 0.3f), gridline(b, 0.3f)));
                vary = 0.8f + 0.4f * hash1((unsigned int)(int)floorf(a / 0.3f + 512.f),
                                           (unsigned int)(int)floorf(b / 0.3f + 512.f), sb);
            }
        }
    } else {                                                     // spandrel glass in the window head
        off = 0.82f;
        cellh = hash1(sb, 333u, 1u);
        if (fine) lead = fminf(gridline(v, 0.45f), gridline(u, 0.45f));
    }
    const float clr = P[I_CLR];
    const float bh = hash1(sb, 9u, (unsigned int)P[I_SEED]);
    const float idx = P[26] + P[25] * (clr * (off + 0.35f * cellh + 0.25f * bh) + 0.012f * v);
    float3 c = jewel(srgb_to_linear(pal(idx, P + 13)));
    if (fine) {
        const float fade = saturate(1.f - fw * 10.f);
        const float lm = (1.f - smoothstep(0.016f, 0.016f + fw, lead)) * fade;
        c = c * ((1.f - 0.9f * lm) * mix(0.82f, vary, fade));
    }
    return c * g;
}

// Rose window centred at (0, 0), radius I_RR, spinning by I_SPIN.
__device__ float3 rose_window(float u, float v, const float* __restrict__ P, float fw, bool fine, float& bars) {
    bars = 0.f;
    const float Rr = P[I_RR];
    const float r = length(f2(u, v));
    if (r > Rr + 0.3f) return f3(0.f);
    const float rr = r / Rr;
    const float th = atan2f(v, u) - P[I_SPIN];
    const float sw = SDF_TAU / 12.f;
    const float ks = floorf(th / sw);
    const float a = th - (ks + 0.5f) * sw;
    float sa, ca;
    __sincosf(a, &sa, &ca);
    const float d_rings = fminf(fminf(fabsf(r - 0.2f * Rr), fabsf(r - 0.56f * Rr)), fabsf(r - Rr));
    const float d_spoke = rr > 0.2f ? r * sinf(fmaxf(0.5f * sw - fabsf(a), 0.f)) : 1e9f;
    const float d_lobe = length(f2(r * ca - 0.78f * Rr, r * sa)) - 0.165f * Rr;
    const float d_pet = length(f2(r * ca - 0.38f * Rr, r * sa)) - 0.11f * Rr;
    const float bw = 0.032f * Rr;
    const float tr = fminf(fminf(d_rings, d_spoke), fminf(fabsf(d_lobe), fabsf(d_pet))) - bw;
    const float sd_out = r - Rr;
    bars = (1.f - smoothstep(-fw, fw, tr)) * (1.f - smoothstep(-fw, fw, sd_out - bw));
    const float g = 1.f - smoothstep(-fw, fw, fmaxf(sd_out, -tr));
    if (g <= 0.f) return f3(0.f);
    const float ring = rr < 0.2f ? 0.f : (rr < 0.56f ? 1.f : 2.f);
    const bool inside = (ring == 2.f && d_lobe < 0.f) || (ring == 1.f && d_pet < 0.f);
    const float par = (float)((int)ks & 1);
    const float off = 0.3f * ring + (inside ? 0.45f : 0.f) + 0.15f * par;
    const float idx = P[26] + 0.33f + P[25] * (P[I_CLR] * off + 0.03f * ring);
    float3 c = jewel(srgb_to_linear(pal(idx, P + 13)));
    if (fine) {
        float lead;
        if (inside) lead = fabsf(r * sa);
        else if (ring == 0.f) {
            float ss, cs;
            __sincosf(P[I_SPIN], &ss, &cs);
            lead = fminf(fabsf(u * cs + v * ss), fabsf(v * cs - u * ss));
        } else lead = gridline(r, 0.11f * Rr);
        const float fade = saturate(1.f - fw * 10.f);
        c = c * (1.f - 0.85f * (1.f - smoothstep(0.016f, 0.016f + fw, lead)) * fade);
    }
    return c * g;
}

// Row of five lancets under the rose on the west wall (u = x, v = y).
__device__ float3 end_lancets(float u, float v, const float* __restrict__ P, float fw, bool fine, float& bars) {
    bars = 0.f;
    const float hw = P[I_HW];
    const float xx = u + hw;
    if (xx < 0.f || xx > 2.f * hw || v > P[I_YR]) return f3(0.f);
    const float cw = 0.4f * hw;
    const float li = floorf(xx / cw);
    const float lu = xx - (li + 0.5f) * cw;
    const float wl = 0.3f * cw, cl = 0.8f * wl, Rl = wl + cl;
    const float sd = fmaxf(length(f2(fabsf(lu) + cl, fmaxf(v - P[I_ELS], 0.f))) - Rl, P[I_YW0] - v);
    const float tr = fabsf(sd) - 0.05f;
    bars = (1.f - smoothstep(-fw, fw, tr));
    const float g = 1.f - smoothstep(-fw, fw, fmaxf(sd, -tr));
    if (g <= 0.f) return f3(0.f);
    const float pv = floorf((v - P[I_YW0]) / 0.9f);
    const float idx = P[26] + P[25] * (0.12f + P[I_CLR] * (0.55f + 0.3f * hash1((unsigned int)(int)li, 41u, 2u)
                                                          + 0.22f * hash1((unsigned int)(int)li, (unsigned int)(int)pv, 6u)));
    float3 c = jewel(srgb_to_linear(pal(idx, P + 13)));
    if (fine) {
        const float fade = saturate(1.f - fw * 10.f);
        const float lead = fminf(gridline(v - P[I_YW0], 0.45f), fabsf(lu));
        c = c * (1.f - 0.85f * (1.f - smoothstep(0.016f, 0.016f + fw, lead)) * fade);
    }
    return c * g;
}

// Light arriving at p along direction L (towards the source) through the side window at x = xs (soft glass).
__device__ __forceinline__ float3 side_light(float3 p, float3 L, float xs, const float* __restrict__ P) {
    const float t = (xs - p.x) / L.x;
    if (t < 0.f) return f3(0.f);
    const float3 q = p + L * t;
    const float B = P[I_B];
    const float bf = floorf(q.z / B);
    const int bay = (int)bf;
    float bars;
    const float3 c = side_window(q.z - (bf + 0.5f) * B, q.y, bay, xs, P, 0.14f, false, bars);
    return c * bay_sun(bay, xs, P);
}
__device__ __forceinline__ float3 rose_light(float3 p, const float* __restrict__ P) {
    const float3 L = load3(P + I_LR);
    const float t = (P[I_ZE] - p.z) / L.z;
    const float3 q = p + L * t;
    float bars;
    return rose_window(q.x, q.y - P[I_YR], P, 0.22f, false, bars) * __expf(-0.03f * t);
}
// coloured pools of sunlight on a surface (no occlusion: an analytic window projection)
__device__ __forceinline__ float3 pools(float3 pos, float3 n, const float* __restrict__ P) {
    float3 c = f3(0.f);
    const float3 L1 = load3(P + I_LS1), L2 = load3(P + I_LS2);
    const float d1 = dot(n, L1), d2 = dot(n, L2);
    if (d1 > 0.f) c += side_light(pos, L1, P[I_XW], P) * (d1 * P[I_S1]);
    if (d2 > 0.f) c += side_light(pos, L2, -P[I_XW], P) * (d2 * P[I_S2]);
    return c * 1.9f;
}
// what the polished floor mirrors: the nearest window plane along r (soft glass, no occlusion)
__device__ float3 floor_env(float3 o, float3 r, const float* __restrict__ P) {
    const float xw = P[I_XW];
    const float ts = fabsf(r.x) > 1e-4f ? (copysignf(xw, r.x) - o.x) / r.x : 1e9f;
    const float te = r.z > 1e-4f ? (P[I_ZE] - o.z) / r.z : 1e9f;
    float bars;
    if (ts < te) {
        const float3 q = o + r * ts;
        const float B = P[I_B];
        const float bf = floorf(q.z / B);
        const int bay = (int)bf;
        const float sd = r.x > 0.f ? 1.f : -1.f;
        return side_window(q.z - (bf + 0.5f) * B, q.y, bay, sd, P, 0.12f, false, bars)
               * (P[I_WIN] * (sd > 0.f ? 1.f : 0.7f) * (0.45f + 0.55f * bay_sun(bay, sd, P)));
    }
    if (te < 1e8f) {
        const float3 q = o + r * te;
        return rose_window(q.x, q.y - P[I_YR], P, 0.15f, false, bars) * P[I_ROSE]
             + end_lancets(q.x, q.y, P, 0.12f, false, bars) * (P[I_WIN] * 0.8f);
    }
    return f3(0.f);
}

SDF_KERNEL
void light_cathedral(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    int idd;
    auto map = [&](float3 p) { return scene(p, P, idd); };

    const float xw = P[I_XW], ze = P[I_ZE];
    const float tfl = rd.y < -1e-5f ? -ro.y / rd.y : 1e9f;
    const float tsd = fabsf(rd.x) > 1e-5f ? (copysignf(xw, rd.x) - ro.x) / rd.x : 1e9f;
    const float ten = rd.z > 1e-5f ? (ze - ro.z) / rd.z : 1e9f;
    const float tmax = fminf(fminf(tfl, tsd), fminf(ten, 90.f));
    const Hit h = march(map, ro, rd, 0.05f, tmax, 64, 0.8f * pix, P[I_STEP]);
    const bool hit_obj = h.hit || (h.steps >= 64 && h.t < tmax);
    const float t = hit_obj ? h.t : tmax;

    const float drift = P[26], period = P[25], pulse = P[I_PULSE], neon = P[I_NEON];
    const float* PAL = P + 13;
    const float3 tint = jewel(srgb_to_linear(pal(drift + 0.3f * period, PAL)));
    const float3 amb = mix(f3(0.62f, 0.6f, 0.57f), tint, 0.5f) * P[I_AMB];
    const float3 pos = ro + rd * t;
    float3 col = f3(0.f);
    if (hit_obj) {
        int id = 1;
        scene(pos, P, id);
        const float3 n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 1e-3f));
        const float ao = calc_ao(map, pos, n, 0.45f, 3);
        const float3 alb = id == 3 ? f3(0.26f, 0.24f, 0.21f) : f3(0.2f, 0.18f, 0.155f);
        const float3 lit = amb * ((0.75f - 0.35f * n.y) * (0.35f + 0.65f * ao)) + pools(pos, n, P) * (0.5f + 0.5f * ao);
        col = alb * lit;
        if (id >= 3) {
            const float3 nc = jewel(srgb_to_linear(pal(drift + 0.55f + 0.025f * period * pos.y, PAL)));
            const float fre = 1.f - fabsf(dot(n, rd));
            col += nc * (id == 3 ? neon * (2.0f + 1.2f * fre)
                                 : 0.2f + 2.6f * neon + 1.1f * pulse);
        }
    } else if (t == tfl) {                                        // polished marble floor
        const float fw = fmaxf(t * pix / fmaxf(-rd.y, 0.03f), 1e-3f);
        const float tile = 1.1f;
        const float cx = floorf(pos.x / tile), cz = floorf(pos.z / tile);
        const float chk = fmodf(fabsf(cx + cz), 2.f);
        const float ctr = saturate(1.f - fw * 1.5f);
        const float seam = (1.f - smoothstep(0.012f, 0.012f + fw, fminf(gridline(pos.x, tile), gridline(pos.z, tile)))) * ctr;
        const float3 alb = f3(0.13f, 0.12f, 0.11f) * (mix(0.62f, chk > 0.5f ? 0.35f : 1.f, ctr) * (1.f - 0.6f * seam));
        const float3 n = f3(0.f, 1.f, 0.f);
        const float ao = calc_ao(map, pos, n, 0.6f, 2);
        col = alb * (amb * (0.6f * ao) + pools(pos, n, P) * (0.6f + 0.4f * ao));
        const float c0 = saturate(-rd.y);
        const float fre = 0.05f + 0.55f * powf(1.f - c0, 5.f);
        col += floor_env(pos, f3(rd.x, -rd.y, rd.z), P) * (fre * (0.85f - 0.4f * seam) * (0.5f + 0.5f * ao));
    } else if (t == tsd) {                                        // side wall: stained glass lancets
        const float sd = rd.x > 0.f ? 1.f : -1.f;
        const float fw = fmaxf(t * pix / fmaxf(fabsf(rd.x), 0.06f), 1e-3f);
        const float B = P[I_B];
        const float bf = floorf(pos.z / B);
        const int bay = (int)bf;
        float bars;
        const float3 g = side_window(pos.z - (bf + 0.5f) * B, pos.y, bay, sd, P, fw, true, bars);
        const float sun = bay_sun(bay, sd, P);
        col = g * (P[I_WIN] * (sd > 0.f ? 1.f : 0.7f) * (0.45f + 0.55f * sun));
        col += f3(0.2f, 0.18f, 0.155f) * amb * 0.8f;
        const float3 nc = jewel(srgb_to_linear(pal(drift + 0.55f + 0.025f * period * pos.y, PAL)));
        col += nc * (bars * neon * 2.4f);
    } else if (t == ten) {                                        // west wall: rose over lancets
        const float fw = fmaxf(t * pix / fmaxf(rd.z, 0.1f), 1e-3f);
        float b1, b2;
        col = rose_window(pos.x, pos.y - P[I_YR], P, fw, true, b1) * P[I_ROSE]
            + end_lancets(pos.x, pos.y, P, fw, true, b2) * (P[I_WIN] * 0.8f);
        col += f3(0.2f, 0.18f, 0.155f) * amb * 0.8f;
        const float3 nc = jewel(srgb_to_linear(pal(drift + 0.55f + 0.025f * period * pos.y, PAL)));
        col += nc * ((b1 + b2) * neon * 2.4f);
    }

    // ---- incense haze: coloured shafts from both window walls and the rose (analytic window projection)
    const float3 L1 = load3(P + I_LS1), L2 = load3(P + I_LS2), Lr = load3(P + I_LR);
    const float ph1 = 0.55f + 0.9f * powf(saturate(dot(rd, L1)), 3.f);
    const float ph2 = 0.55f + 0.9f * powf(saturate(dot(rd, L2)), 3.f);
    const float phr = 0.45f + 1.6f * powf(saturate(dot(rd, Lr)), 12.f);
    const float jit = fract(52.9829189f * fract(0.06711056f * (float)x + 0.00583715f * (float)y));
    const float tm = fminf(t, 70.f);
    const float hph = P[I_HPH], dens0 = P[I_HAZE], top = P[I_YS] + P[I_H];
    const float s1 = P[I_S1] * ph1 * 4.f, s2 = P[I_S2] * ph2 * 4.f, sr = P[I_SR] * phr;
    float3 acc = f3(0.f);
    float T = 1.f;
    const int NS = 12;
    for (int i = 0; i < NS; ++i) {
        const float f0 = (float)i / NS, f1 = (float)(i + 1) / NS, fm = ((float)i + jit) / NS;
        const float ti = tm * fm * fm, dti = tm * (f1 * f1 - f0 * f0);
        const float3 p = ro + rd * ti;
        const float nz = sinf(0.31f * p.x + 0.8f * sinf(0.17f * p.z + hph)) * sinf(0.23f * p.y - 0.5f * hph + 0.29f * p.z);
        const float dens = dens0 * (0.65f + 0.35f * nz) * (1.15f - 0.5f * saturate(p.y / top));
        float3 Ls = side_light(p, L1, xw, P) * s1 + side_light(p, L2, -xw, P) * s2;
        Ls += rose_light(p, P) * sr;
        acc += Ls * (T * dens * dti);
        T *= __expf(-dens * dti);
    }
    const float3 fogc = mix(f3(0.5f), tint, 0.5f) * (0.02f + 0.02f * P[I_AMB]);
    col = col * T + acc + fogc * (1.f - T);

    // ---- rose glare (over everything, like bloom), flaring on the beat
    if (rd.z > 1e-3f) {
        const float3 Q = ro + rd * ((ze - ro.z) / rd.z);
        const float dq = length(f2(Q.x, Q.y - P[I_YR])) / P[I_RR];
        const float gl = 1.f / (1.f + 2.5f * dq * dq);
        const float3 rc = jewel(srgb_to_linear(pal(drift + 0.33f + 0.3f * period * P[I_CLR], PAL)));
        col += rc * (gl * gl * P[I_ROSEB]);
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""


class LightCathedral(Source):
    name = "light_cathedral"
    label = "Light cathedral"
    description = ("Process down an endless gothic nave: ribbed vaults, clustered piers, tall stained-glass lancets and a "
                   "spinning rose window throwing coloured shafts through incense haze; the tracery glows neon at "
                   "high energy.")
    tags = {"family": "architecture", "dims": "3d", "toolkit": "sdf3d", "technique": ["raymarch", "sdf", "volumetric"],
            "moods": ["elegant", "euphoric", "dark"], "style": "lively", "energy": [0.2, 1.0]}
    params = {
        **standard_scene_params(palette="rainbow", render_scale=0.45, period=1.0, drift=0.02),
        "window_colourfulness": num(0.0, 1.0, 0.7, "Window colourfulness", tier="regional", basic=True),
        "nave_width": num(5.0, 11.0, 7.6, "Nave width", tier="regional"),
        "arch_pointiness": num(0.0, 1.0, 0.6, "Arch pointiness (round -> lancet)", tier="regional"),
        "haze_density": num(0.0, 1.0, 0.5, "Incense haze", tier="regional"),
        "column_style": num(0.0, 1.0, 0.65, "Piers (drum -> clustered shafts)", tier="regional"),
        "sun_height": num(0.0, 1.0, 0.45, "Light angle (low -> high)", tier="regional"),
        "tracery_glow": num(0.0, 2.0, 1.0, "Neon tracery (rises with energy)", tier="regional"),
        "procession_speed": num(0.0, 4.0, 1.0, "Procession speed", unit="×", tier="realtime", bind="rate"),
        "rose_spin": num(0.0, 4.0, 1.0, "Rose spin", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("light_cathedral")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        seed = int(params["seed"])
        rng = np.random.default_rng(seed)
        self.seed_f = float(seed % 4093)
        self.z = float(rng.uniform(0.0, NBAY * BAY))   # procession distance
        self.cam_ph = float(rng.uniform(0.0, 50.0))     # camera sway (host-only)
        self.spin = 0.0
        self.sun_ph = float(rng.uniform(0.0, 2 * math.pi))
        self.cloud = float(rng.uniform(0.0, 2 * math.pi))
        self.hph = 0.0
        self.drift = 0.0
        self.e_s = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (pulse kicks rates only)
        self.z += float(p["procession_speed"]) * spd * (0.3 + 1.2 * e + 0.8 * pulse) * dt
        self.cam_ph += spd * (0.05 + 0.10 * es) * dt
        self.spin = wrap(self.spin + float(p["rose_spin"]) * spd * (0.04 + 0.12 * e + 0.5 * pulse) * dt, 2 * math.pi)
        self.sun_ph = wrap(self.sun_ph + spd * 0.025 * dt, 2 * math.pi)
        self.cloud = wrap(self.cloud + spd * (0.06 + 0.14 * e) * dt, 2 * math.pi)
        self.hph = wrap(self.hph + spd * (0.08 + 0.2 * e) * dt, 20 * math.pi)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- nave geometry (all eased regional params)
        hw = 0.5 * float(p["nave_width"])
        kpt = float(p["arch_pointiness"])
        c = hw * 1.25 * kpt
        R = hw + c
        Hh = math.sqrt(max(R * R - c * c, 1e-6))
        ys = 1.7 * hw + 1.0
        at = 0.5 * BAY
        ct = max((Hh * Hh - at * at) / (2.0 * at), 0.0)
        Rt = at + ct
        xw = hw + RECESS
        aw = at - 0.5
        Ro = Rt - 0.3
        roc = 0.46 * aw
        apex_o = ys + math.sqrt(max(Ro * Ro - ct * ct, 0.0))
        v_o = ys + math.sqrt(max(Ro * Ro - (roc + 0.12 + ct) ** 2, 0.0)) - 0.1 * roc
        v_o = min(max(v_o, ys), apex_o - roc - 0.15)
        wl = 0.5 * aw - 0.03
        cl = 0.8 * wl
        Rl = wl + cl
        hl = math.sqrt(Rl * Rl - cl * cl)
        lspr = max(v_o - roc - 0.12 - hl, YW0 + 1.5)
        rr = 0.84 * hw
        y_r = ys + 0.18 * Hh
        ecw = 0.4 * hw
        ewl = 0.3 * ecw
        ecl = 0.8 * ewl
        eRl = ewl + ecl
        els = max(y_r - rr - 0.5 - math.sqrt(eRl * eRl - ecl * ecl), YW0 + 0.8)

        # ---- camera: a slow procession down the aisle, gazing up into the vault
        sway = 0.25 + 0.6 * es
        cp_ = self.cam_ph
        zw = wrap(self.z, NBAY * BAY)
        ex = sway * min(0.3 * hw, 1.0) * math.sin(cp_)
        ey = 1.8 + 0.35 * math.sin(0.7 * cp_ + 1.0)
        yaw = 0.12 * sway * math.sin(0.6 * cp_ + 0.5)
        pitch = 0.2 + 0.1 * math.sin(0.45 * cp_ + 2.0)
        eye = (ex, ey, zw)
        tgt = (ex + math.sin(yaw), ey + math.tan(pitch), zw + math.cos(yaw))
        cam = camera_pack(eye, tgt, fov_deg=62.0, roll=0.012 * math.sin(0.33 * cp_))

        # ---- light
        sy = 0.35 + 1.15 * float(p["sun_height"])
        l1 = np.array([1.0, sy, 0.18 + 0.22 * math.sin(self.sun_ph)])
        l2 = np.array([-1.0, 0.9 * sy, -0.12 + 0.2 * math.sin(2 * self.sun_ph + 2.0)])
        l1 /= np.linalg.norm(l1)
        l2 /= np.linalg.norm(l2)
        lr = np.array([0.0, (y_r - ey) / D_END, 1.0])
        lr /= np.linalg.norm(lr)
        sg = 0.45 + 0.85 * e
        neon = float(p["tracery_glow"]) * (0.06 + 0.94 * _smooth(0.35, 1.0, e) ** 1.5) * (1.0 + 0.3 * pulse)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = hw
        Ph[29] = xw
        Ph[30] = ys
        Ph[31] = c
        Ph[32] = R
        Ph[33] = Hh
        Ph[34] = BAY
        Ph[35] = ct
        Ph[36] = Rt
        Ph[37] = p["column_style"]
        Ph[38] = zw + D_END
        Ph[39] = y_r
        Ph[40] = rr
        Ph[41] = self.spin
        Ph[42] = p["window_colourfulness"]
        Ph[43] = 0.004 + 0.05 * float(p["haze_density"]) * (0.55 + 0.6 * e ** 1.3)
        Ph[45] = neon
        Ph[46] = 2.4 * (0.8 + 0.4 * e) * (1.0 + 1.3 * pulse)
        Ph[47:50] = l1
        Ph[50:53] = l2
        Ph[53:56] = lr
        Ph[56] = sg
        Ph[57] = 0.6 * sg
        Ph[58] = self.hph
        Ph[59] = pulse
        Ph[60] = 1.9 * (0.85 + 0.3 * e)
        Ph[61] = self.seed_f
        Ph[62] = self.cloud
        Ph[63] = aw
        Ph[64] = Ro
        Ph[65] = YW0
        Ph[66] = cl
        Ph[67] = Rl
        Ph[68] = lspr
        Ph[69] = v_o
        Ph[70] = roc
        Ph[71] = els
        Ph[72] = 0.10 + 0.05 * e
        Ph[73] = 0.75
        Ph[74] = 1.4 * sg * (1.0 + 1.0 * pulse)
        Ph[75] = 0.05 + 0.12 * pulse
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)
