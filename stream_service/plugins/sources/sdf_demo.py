"""SDF demo: a flight down the aisle of an endless hall of columns, chasing a cluster of smooth-min blobs.

Source: Original (reference scene for smfx/_lib/sdf3d.py; SDF building blocks from hg_sdf and iq, see sdf3d.py)
Author: smfx
License: MIT
license_class: permissive

The 3D toolkit's reference scene.  Columns are a pMod grid (the camera's aisle is left empty) with chamfered
plinths, optional square/twisted section and scrolling helical light bands; the floor has light seams with
streaks running along the aisle; 1-8 blobs melt together with smin and orbit in front of the camera; fog, a
key/fill/rim rig, soft shadows on the floor, AO, ACES tonemap.  The floor is analytic (not in the SDF) and its
mirror reflection is traced analytically (blob spheres + a 2D DDA over the column grid), costing ~0.3 ms.

Macros: energy -> flight speed, blob count / orbit speed / radius / breathing, band and seam brightness and scroll
speed, camera sway and roll, key light.  pulse -> blob swell, band/seam/blob flash, key flash and a kick to the
flight and scroll rates (rates only, so nothing jumps).  speed scales every rate; palette indexes the bands,
seams, blobs and fog; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rig_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

MAX_BLOBS = 8
FLOOR_Y = -1.25
TOP_Y = 5.5
COL_R = 0.5

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog | 29 spacing | 30 shape | 31 twist
#  32 smin k | 33 band phase | 34 band level | 35 seam level | 36 seam phase | 37 blob count (int as float)
#  38 cluster bound radius | 39..41 cluster centre | 42 blob emissive | 43 step scale | 44 pulse
#  45 floor reflectance | 46..62 rig | 64..95 blobs (x, y, z, r) | 96..103 blob palette index
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define FLOOR_Y (%(floor)ff)
#define TOP_Y (%(top)ff)
#define COL_R (%(colr)ff)

__device__ __forceinline__ float column_d(float3 p, const float* __restrict__ P, float3 eye) {
    const float S = P[29];
    float3 q = p;
    const float cx = pMod1(q.x, S);
    pMod1(q.z, S);
    if (cx == 0.f) q.x = fabsf(q.x) - S;                         // the aisle: distance to the next column over
    const float dr = length(f2(q.x, q.z)) - COL_R;
    float d = dr;
    if (P[30] > 0.001f) {                                        // square / twisted section, blended in
        const float3 qt = twistY(q, P[31]);
        d = mix(dr, fBox2(f2(qt.x, qt.z), f2(COL_R * 0.86f)) - 0.04f, P[30]);
    }
    const float lo = p.y - (FLOOR_Y + 0.38f), hi = fabsf(p.y - TOP_Y) - 0.18f;
    if (lo < d + 0.15f)                                          // plinth only where it can matter
        d = fOpUnionChamfer(d, fmaxf(fBox2(f2(q.x, q.z), f2(COL_R * 1.32f)), lo), 0.12f);
    if (hi < d + 0.15f)                                          // capital
        d = fOpUnionChamfer(d, fmaxf(fBox2(f2(q.x, q.z), f2(COL_R * 1.25f)), hi), 0.1f);
    d = fmaxf(d, p.y - TOP_Y - 0.2f);
    return fmaxf(d, 0.9f - length(p - eye));                     // carve a bubble around the camera
}
__device__ __forceinline__ float blobs_d(float3 p, const float* __restrict__ P, float bound_best) {
    const float3 C = load3(P + 39);
    const float db = length(p - C) - P[38];
    if (db > bound_best) return db;                              // early out: whole cluster is further
    const int n = (int)P[37];
    const float k = P[32];
    float d = 1e9f;
    for (int i = 0; i < n; ++i) {
        const float* b = P + 64 + 4 * i;
        d = smin(d, length(p - load3(b)) - b[3], k);
    }
    return d;
}
// id: 1 column, 2 blob.  The floor is NOT in the SDF: it is intersected analytically (grazing rays would
// otherwise crawl along it in tiny steps), so map() only holds columns and blobs.
__device__ __forceinline__ float scene(float3 p, const float* __restrict__ P, float3 eye, int& id) {
    float d = column_d(p, P, eye);
    id = 1;
    const float db = blobs_d(p, P, d);
    if (db < d) { d = db; id = 2; }
    return d;
}

// column light bands (helical, scrolling) and base tint at a column surface point
__device__ __forceinline__ float3 column_light(float3 pos, const float* __restrict__ P, float fwidth, float3& alb) {
    const float S = P[29];
    float3 q = pos;
    const float cx = pMod1(q.x, S), cz = pMod1(q.z, S);
    const float cxx = (cx == 0.f) ? (q.x > 0.f ? 1.f : -1.f) : cx;
    if (cx == 0.f) q.x -= cxx * S;
    const float hsh = hash1((unsigned int)(int)cxx + 911u, (unsigned int)(int)cz + 17u, 3u);
    const float3 qt = twistY(q, P[31]);
    const float ang = atan2f(qt.z, qt.x) * (1.f / SDF_TAU);
    const float u = (pos.y - FLOOR_Y) * 0.9f + ang * 2.f + P[33] * (0.7f + 0.6f * hsh) + hsh;
    const float fu = fract(u);
    const float band = smoothstep(0.0f, 0.06f + fwidth, fu) * (1.f - smoothstep(0.16f, 0.22f + fwidth, fu));
    const float3 bc = srgb_to_linear(pal(hsh * 0.6f + pos.y * 0.07f * P[25] + P[26], P + 13));
    const float body = pos.y > FLOOR_Y + 0.4f && pos.y < TOP_Y - 0.2f ? 1.f : 0.f;
    alb = mix(f3(0.045f), bc * 0.1f, 0.5f);
    return bc * (band * body * P[34]);
}
// palette index of the blob surface at pos (inverse-square weighted over the blobs)
__device__ __forceinline__ float blob_index(float3 pos, const float* __restrict__ P) {
    const int nb = (int)P[37];
    float wsum = 1e-6f, idx = 0.f;
    for (int i = 0; i < nb; ++i) {
        const float* b = P + 64 + 4 * i;
        const float di = fmaxf(length(pos - load3(b)) - b[3], 0.f) + 0.05f;
        const float wi = 1.f / (di * di);
        wsum += wi; idx += wi * P[96 + i];
    }
    return idx / wsum;
}

// Floor reflection, traced analytically (no SDF steps): blobs as spheres, columns as vertical cylinders found by
// a 2D DDA over the column grid in xz.  Returns the reflected colour (or fogc if nothing is hit).
__device__ float3 reflect_trace(float3 o, float3 r, const float* __restrict__ P, float3 fogc, float tbase) {
    const float S = P[29];
    float tb = 1e9f; int ib = -1;
    const int nb = (int)P[37];
    for (int i = 0; i < nb; ++i) {
        const float* b = P + 64 + 4 * i;
        const float2 is = iSphere(o - load3(b), r, b[3]);
        if (is.x > 0.f && is.x < tb) { tb = is.x; ib = i; }
    }
    // columns: DDA through the xz grid (cell c holds a column at c * S, except the aisle cx == 0)
    const float k = sqrtf(r.x * r.x + r.z * r.z);
    float tc = 1e9f; float2 cc = f2(0.f);
    if (k > 1e-4f) {
        const float dx = r.x / k, dz = r.z / k;
        const float smax = fminf((TOP_Y - o.y) / fmaxf(r.y, 1e-3f), 14.f) * k;
        const float R = COL_R * mix(1.f, 0.95f, P[30]);
        int cx = (int)floorf(o.x / S + 0.5f), cz = (int)floorf(o.z / S + 0.5f);
        const int sx = dx > 0.f ? 1 : -1, sz = dz > 0.f ? 1 : -1;
        const float tdx = S / fmaxf(fabsf(dx), 1e-6f), tdz = S / fmaxf(fabsf(dz), 1e-6f);
        float tmx = fabsf(dx) > 1e-6f ? (((float)cx + 0.5f * sx) * S - o.x) / dx : 1e9f;
        float tmz = fabsf(dz) > 1e-6f ? (((float)cz + 0.5f * sz) * S - o.z) / dz : 1e9f;
        for (int i = 0; i < 10; ++i) {
            if (cx != 0) {
                const float ox = o.x - cx * S, oz = o.z - cz * S;
                const float bb = ox * dx + oz * dz, c = ox * ox + oz * oz - R * R, h = bb * bb - c;
                if (h > 0.f) {
                    const float s2 = -bb - sqrtf(h);
                    if (s2 > 0.f && s2 < smax) { tc = s2 / k; cc = f2(cx * S, cz * S); break; }
                }
            }
            float sb;
            if (tmx < tmz) { cx += sx; sb = tmx; tmx += tdx; } else { cz += sz; sb = tmz; tmz += tdz; }
            if (sb > smax) break;
        }
    }
    float3 rc = fogc; float th = 1e9f;
    if (ib >= 0 && tb < tc) {
        th = tb;
        const float* b = P + 64 + 4 * ib;
        const float3 n = normalize(o + r * tb - load3(b));
        const float fre = 1.f - saturate(dot(n, -r));
        const float3 bc = srgb_to_linear(pal(P[96 + ib] * P[25] + 0.35f * fre + P[26], P + 13));
        rc = bc * ((0.35f + P[42] + 0.6f * fre * fre) * (0.45f + 0.55f * saturate(dot(n, load3(P + 46)))));
    } else if (tc < 1e8f) {
        th = tc;
        const float3 ph = o + r * tc;
        float3 alb;
        const float3 em = column_light(ph, P, 0.05f, alb);
        const float3 n = normalize(f3(ph.x - cc.x, 0.f, ph.y * 0.f + ph.z - cc.y));
        const float3 kd = load3(P + 46), kc = load3(P + 49);
        rc = em + alb * (kc * saturate(dot(n, kd)) + f3(0.05f));
    }
    if (th < 1e8f) rc = fog_exp2(rc, fogc, th + tbase, P[28]);
    return rc;
}

SDF_KERNEL_TIGHT                                                // <= 64 regs: 4 blocks/SM, no spills
void sdf_demo(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    int idd;
    auto map = [&](float3 p) { return scene(p, P, ro, idd); };

    const float TFAR = 48.f;
    const float tfloor = rd.y < -1e-4f ? (FLOOR_Y - ro.y) / rd.y : 1e9f;
    float tmax = fminf(TFAR, tfloor);
    if (rd.y > 1e-4f) tmax = fminf(tmax, (TOP_Y + 0.25f - ro.y) / rd.y);
    const Hit h = march(map, ro, rd, 0.05f, tmax, 100, 0.75f * pix, P[43]);
    const bool hit_obj = h.hit || (h.steps >= 100 && h.t < tmax);
    const bool hit_floor = !hit_obj && tfloor <= TFAR;

    const float drift = P[26], period = P[25], pulse = P[44];
    const float* PAL = P + 13;
    const float3 pf = srgb_to_linear(pal(drift + 0.55f, PAL));
    const float3 fogc = mix(pf, f3(dot(pf, f3(0.33f))), 0.55f) * 0.06f + f3(0.004f, 0.005f, 0.009f);
    // void above the columns: fog colour fading to near-black upwards
    float3 col = fogc * (1.f - 0.8f * saturate(rd.y * 2.5f));
    const Rig L = rig_load(P + 46);
    float t = hit_obj ? h.t : (hit_floor ? tfloor : tmax);
    if (hit_obj || hit_floor) {
        const float3 pos = ro + rd * t;
        int id = 0;
        if (hit_obj) scene(pos, P, ro, id);
        float3 n, alb, emis = f3(0.f);
        float sh = 1.f, ao = 1.f, specw = 1.f;
        const float S = P[29];
        if (id == 0) {                                           // floor: polished, seams, shadows, reflection
            n = f3(0.f, 1.f, 0.f);
            float3 q = pos;
            const float cx = pMod1(q.x, S), cz = pMod1(q.z, S);
            const float fw = fmaxf(t * pix / fmaxf(-rd.y, 0.04f), 0.004f);
            const float lw = 0.025f * S;
            const float sx = 1.f - smoothstep(lw, lw + 1.5f * fw, 0.5f * S - fabsf(q.x));   // seams along z
            const float sz = 1.f - smoothstep(lw, lw + 1.5f * fw, 0.5f * S - fabsf(q.z));   // seams along x
            const float lane = cx + (q.x > 0.f ? 0.5f : -0.5f);
            const float streak = powf(fract(pos.z / (4.f * S) - P[36] + hash1((unsigned int)(int)(lane * 2.f), 7u)), 6.f);
            const float3 sc = srgb_to_linear(pal(drift + 0.25f + 0.05f * period * cz, PAL));
            emis = sc * (sx * (0.25f + 2.6f * streak) + sz * 0.22f) * P[35];
            alb = f3(0.018f, 0.019f, 0.022f);
            specw = 1.2f;
            sh = soft_shadow(map, pos + n * 0.02f, L.key_dir, 0.04f, 12.f, 9.f, 14);
            ao = calc_ao(map, pos, n, 0.6f, 3);
            // reflection: a short second march, shaded without normals (emission + flat tint)
            const float fre = 1.f - saturate(-rd.y);
            const float kr = P[45] * (0.25f + 0.75f * fre * fre * fre * fre * fre) * (1.f - 0.6f * sx);
            if (kr > 0.01f) emis += reflect_trace(pos, f3(rd.x, -rd.y, rd.z), P, fogc, t) * kr;
        } else {
            n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 2e-4f));
            if (id == 1) {                                       // column: dark gloss + helical light bands
                emis = column_light(pos, P, fmaxf(t * pix * 0.9f, 0.002f), alb);
                specw = 1.3f;
                ao = calc_ao(map, pos, n, 0.5f, 2);
            } else {                                             // blob: bright palette, blended per blob
                const float idx = blob_index(pos, P);
                const float fre = 1.f - saturate(dot(n, -rd));
                const float3 bcol = srgb_to_linear(pal(idx * period + 0.35f * fre + drift, PAL));
                alb = bcol * 0.9f;
                emis = bcol * (P[42] + 0.6f * fre * fre);
                specw = 1.5f;
                ao = 0.6f + 0.4f * calc_ao(map, pos, n, 0.35f, 2);
            }
        }
        Rig Ls = L; Ls.spec *= specw;
        if (id == 0) Ls.rim_col = Ls.rim_col * 0.12f;           // the reflection already supplies the floor's sheen
        col = shade(Ls, alb, n, rd, sh, ao) + emis;
    }
    // blob halo: closest approach of the ray to each blob (cheap volumetric glow)
    const float3 Cc = load3(P + 39);
    const float tcc = dot(Cc - ro, rd);
    if (dot2(ro + rd * tcc - Cc) < square(P[38] + 1.2f)) {     // skip unless the ray passes near the cluster
        const int nb = (int)P[37];
        float3 halo = f3(0.f);
        for (int i = 0; i < nb; ++i) {
            const float* b = P + 64 + 4 * i;
            const float3 c = load3(b);
            const float tc = dot(c - ro, rd);
            if (tc <= 0.f || tc > t + b[3]) continue;
            const float d2 = dot2(ro + rd * tc - c);
            const float r2 = b[3] * b[3];
            const float g = r2 / (r2 + 2.5f * fmaxf(d2 - r2, 0.f) + 1e-4f);
            halo += srgb_to_linear(pal(P[96 + i] * period + drift, PAL)) * (g * g);
        }
        col += halo * (0.10f + 0.35f * pulse) * P[42] * 2.f;
    }
    col = fog_exp2(col, fogc, t, P[28]);
    col = vignette(col, x, y, W, H, 0.35f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"floor": FLOOR_Y, "top": TOP_Y, "colr": COL_R}


class SdfDemo(Source):
    name = "sdf_demo"
    label = "Pillar hall (SDF demo)"
    description = "Fly down an endless hall of glowing columns chasing a cluster of melting blobs (3D raymarched)."
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.03),
        "column_spacing": num(2.4, 6.0, 3.4, "Column spacing", tier="regional"),
        "column_shape": num(0.0, 1.0, 0.35, "Column shape (round -> square)", tier="regional"),
        "twist": num(0.0, 1.5, 0.35, "Column twist", unit="rad/u", tier="regional"),
        "blob_count": num(1.0, 8.0, 6.0, "Blobs (at full energy)", step=0.1, tier="regional"),
        "blob_smooth": num(0.05, 1.5, 0.7, "Blob melt", tier="regional"),
        "fog": num(0.0, 0.12, 0.045, "Fog", step=0.001, tier="regional"),
        "glow": num(0.0, 3.0, 1.0, "Light bands", tier="regional"),
        "camera_distance": num(2.5, 9.0, 5.0, "Camera distance", tier="regional"),
        "travel_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Blob orbit speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("sdf_demo")
        self.P = cp.zeros(112, cp.float32)
        self.Ph = np.zeros(112, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.blob_rand = rng.uniform(0.0, 1.0, (MAX_BLOBS, 4))
        self.z = 0.0                      # flight distance
        self.orb = 0.0                    # blob orbit phase
        self.band = 0.0                   # band scroll (wraps at 1)
        self.seam = 0.0                   # seam streak phase (wraps at 1)
        self.cam_ph = 0.0                 # camera sway phase
        self.breath = 0.0                 # blob breathing phase
        self.drift = 0.0
        self.e_s = None                   # smoothed energy for geometry-affecting uses

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        # energy drives blob count / radii / camera sway: smooth it a little so per-frame DMX noise never shakes
        a = 1.0 - math.exp(-dt / 0.25) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (kicks add to rates only)
        self.z += p["travel_speed"] * spd * (0.35 + 1.65 * e + 1.2 * pulse) * dt
        # host-only phases (float64, used with non-integer multipliers): never wrapped, so they never jump
        self.orb += p["orbit_speed"] * spd * (0.22 + 1.0 * e + 0.5 * pulse) * dt
        self.band = wrap(self.band + spd * (0.08 + 0.55 * e + 0.6 * pulse) * dt, 1.0)
        self.seam = wrap(self.seam + spd * (0.15 + 0.9 * e + 0.8 * pulse) * dt, 1.0)
        self.cam_ph += spd * (0.10 + 0.22 * e) * dt
        self.breath += spd * (0.6 + 1.6 * e) * dt
        self.drift = wrap(self.drift + p["drift_speed"] * spd * dt, 1000.0)   # GPU-bound: wrap (c * 1000 integral)

        # ---- camera: chase the cluster down the aisle, sway / orbit / roll with energy
        S = float(p["column_spacing"])
        cz = self.z
        C = (0.0, 0.15, cz)
        D = float(p["camera_distance"])
        sway = 0.25 + 0.75 * es
        yaw = 0.42 * sway * math.sin(self.cam_ph)
        eye = (D * math.sin(yaw), 0.45 + 0.35 * math.sin(0.71 * self.cam_ph + 1.0) * sway + 0.04 * D,
               cz - D * math.cos(yaw))
        roll = (0.015 + 0.05 * es) * math.sin(0.53 * self.cam_ph + 0.4)
        cam = camera_pack(eye, (0.0, C[1] - 0.05, cz + 0.6), fov_deg=58.0, roll=roll)

        # ---- blobs (positions relative to the cluster centre; radius 0 -> blob absent)
        n_eff = float(p["blob_count"]) * (0.5 + 0.5 * es)
        n = min(MAX_BLOBS, max(1, int(math.ceil(n_eff - 1e-3))))
        orbit_r = 0.85 + 0.75 * es
        bob = 0.18 + 0.45 * es
        swell = 1.0 + 0.24 * pulse
        blobs = np.zeros((MAX_BLOBS, 4), np.float32)
        bound = 0.0
        for i in range(n):
            r1, r2, r3, r4 = self.blob_rand[i]
            th = self.orb * (0.65 + 0.5 * r1) * (1 if i % 2 == 0 else -1) + 2 * math.pi * (i / max(n, 1) + r2)
            rr = orbit_r * (0.55 + 0.6 * r3)
            fade = min(max(n_eff - i, 0.0), 1.0)
            fade = fade * fade * (3 - 2 * fade)
            rad = (0.32 + 0.22 * r4) * swell * (1.0 + (0.06 + 0.12 * es) * math.sin(self.breath * (0.8 + 0.4 * r1) + 6.28 * r2))
            rad *= fade
            bx = rr * math.cos(th)
            by = C[1] + bob * math.sin(0.8 * th + 6.28 * r4)
            bz = cz + 0.75 * rr * math.sin(th)
            blobs[i] = (bx, by, bz, rad)
            bound = max(bound, math.sqrt(bx * bx + (by - C[1]) ** 2 + (bz - cz) ** 2) + rad)
        bound += 0.05

        # ---- light rig (key flashes on the beat)
        kgain = (0.85 + 0.45 * e) * (1.0 + 0.4 * pulse)
        rig = rig_pack(key_dir=(0.72, 0.55, -0.25), key_col=(1.55 * kgain, 1.40 * kgain, 1.25 * kgain),
                       fill_col=(0.10, 0.12, 0.18), rim_col=(0.35, 0.40, 0.55), amb_col=(0.02, 0.02, 0.025),
                       spec=0.7, shininess=48.0)

        twist = float(p["twist"])
        step_scale = 1.0 / math.sqrt(1.0 + (twist * COL_R * 1.25 * float(p["column_shape"])) ** 2)
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = p["intensity"] * 1.15
        Ph[28] = p["fog"] * (1.15 - 0.35 * e)
        Ph[29] = S
        Ph[30] = p["column_shape"]
        Ph[31] = twist
        Ph[32] = p["blob_smooth"]
        Ph[33] = self.band
        Ph[34] = p["glow"] * (0.3 + 1.1 * e) * (1.0 + 1.6 * pulse)
        Ph[35] = (0.3 + 0.9 * e) * (1.0 + 1.4 * pulse) * min(1.0, 0.4 + 0.6 * p["glow"])
        Ph[36] = self.seam
        Ph[37] = n
        Ph[38] = bound + 0.25 * float(p["blob_smooth"])
        Ph[39:42] = C
        Ph[42] = 0.12 + 0.25 * e + 0.9 * pulse
        Ph[43] = step_scale
        Ph[44] = pulse
        Ph[45] = 0.7
        Ph[46:63] = rig
        Ph[64:96] = blobs.reshape(-1)
        Ph[96:96 + MAX_BLOBS] = [0.11 * i + 0.3 * self.blob_rand[i, 0] for i in range(MAX_BLOBS)]
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
