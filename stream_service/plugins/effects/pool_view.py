"""Pool view: look down into a swimming pool whose floor is the picture.

Source: https://github.com/evanw/webgl-water
        http://madebyevan.com/webgl-water/
Author: Evan Wallace (WebGL Water caustics and refraction)
License: MIT
license_class: permissive
Ported to CUDA by smfx (ideas/changes: Wallace's look (a box pool, the floor seen through a refracting wave surface,
caustics from the surface curvature, tiled walls, Fresnel sky reflection and a sun glint) rebuilt analytically on
the frame3d toolkit: no simulation buffer and no caustic mesh.  The surface is frame3d.Water (seeded directional
deep-water waves plus ripple rings spawned on the beat), evaluated per pixel with its full Hessian, so caustics are
1 / |det(I + a H)| of the light map s -> s + a grad h (sharp folds = the bright web) instead of Wallace's
area-ratio mesh; the floor is the input frame (trilinear mips), walls/waterline/coping/deck are procedural tiles;
water colour and absorption come from the shared cosine palette with drift; everything is screen-blended so bright
stills do not clip).

amount 0 is an exact copy.  amount (through m = smoothstep(amount)) ramps the water depth, wave height, caustic
strength, tint, sky glint, camera tilt and pull-back from the frame3d flat camera, so a small amount is clear,
flat, shallow water seen straight down: the crisp input.  The camera is fitted every frame so the coping just
frames the screen at rim 0; rim pulls it back to reveal the tiled coping and deck.  pulse: on a rising edge
(> 0.15, 120 ms refractory) a ripple ring is spawned at a seeded spot (frame3d Water rings), and the envelope
flashes the caustics and the glint and lifts the wave height a little.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import (FRAME3D, FRAME3D_OPTIONS, FrameMips, Water, camera_orbit,
                                                 smoothstep01)
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
NP = 48
# P layout (floats)
#  0..12 camera | 13 m | 14 water level | 15 coping top | 16 wave scale | 17 caustic a per unit depth
#  18 caustic gain | 19 tint | 20 glint | 21 sky | 22 lod bias | 23..25 sun dir | 26 drift | 27 period
#  28 wall tile size | 29 edge AO | 30..41 palette
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_M 13
#define P_ZW 14
#define P_ZT 15
#define P_WS 16
#define P_CA 17
#define P_CK 18
#define P_TINT 19
#define P_GLINT 20
#define P_SKY 21
#define P_BIAS 22
#define P_SUN 23
#define P_DRIFT 26
#define P_PER 27
#define P_TILE 28
#define P_AO 29
#define P_PAL 30

__device__ __forceinline__ float3 pv_screen(float3 a, float3 b) {
    return f3(1.f) - (f3(1.f) - a) * (f3(1.f) - saturate(b));
}

// Surface height and gradient (frame3d Water buffer layout), scaled by sc.
struct PvS { float h; float2 g; };
__device__ PvS pv_surf(const float* __restrict__ Wp, float2 q, float sc) {
    PvS s; s.h = 0.f; s.g = f2(0.f);
    const int nw = min((int)Wp[0], WATER_MAX_WAVES), nr = min((int)Wp[1], WATER_MAX_RINGS);
    const float* w = Wp + WATER_HDR;
    for (int i = 0; i < nw; ++i, w += WAVE_FLOATS) {          // dir.x dir.y k amp phase
        float sn, cs; sincosf(w[2] * (w[0] * q.x + w[1] * q.y) + w[4], &sn, &cs);
        const float ak = w[3] * w[2] * cs;
        s.h += w[3] * sn; s.g.x += ak * w[0]; s.g.y += ak * w[1];
    }
    const float* r = Wp + WATER_HDR + WATER_MAX_WAVES * WAVE_FLOATS;
    for (int j = 0; j < nr; ++j, r += RING_FLOATS) {          // cx cy radius amp k width
        const float ex = q.x - r[0], ey = q.y - r[1], d = sqrtf(ex * ex + ey * ey) + 1e-5f;
        const float sd = d - r[2], iw = 1.f / r[5], e = __expf(-sd * sd * iw * iw);
        if (e < 1e-3f) continue;
        float sn, cs; sincosf(r[4] * sd, &sn, &cs);
        const float dh = r[3] * e * (r[4] * cs - 2.f * sd * iw * iw * sn);
        s.h += r[3] * sn * e; s.g.x += dh * ex / d; s.g.y += dh * ey / d;
    }
    s.h *= sc; s.g = s.g * sc;
    return s;
}

// Surface Hessian (hxx, hxy, hyy), scaled by sc: the caustic map's Jacobian is I + a H.
__device__ float3 pv_hess(const float* __restrict__ Wp, float2 q, float sc) {
    float hxx = 0.f, hxy = 0.f, hyy = 0.f;
    const int nw = min((int)Wp[0], WATER_MAX_WAVES), nr = min((int)Wp[1], WATER_MAX_RINGS);
    const float* w = Wp + WATER_HDR;
    for (int i = 0; i < nw; ++i, w += WAVE_FLOATS) {
        const float sn = __sinf(w[2] * (w[0] * q.x + w[1] * q.y) + w[4]);
        const float c = -w[3] * w[2] * w[2] * sn;
        hxx += c * w[0] * w[0]; hxy += c * w[0] * w[1]; hyy += c * w[1] * w[1];
    }
    const float* r = Wp + WATER_HDR + WATER_MAX_WAVES * WAVE_FLOATS;
    for (int j = 0; j < nr; ++j, r += RING_FLOATS) {
        const float ex = q.x - r[0], ey = q.y - r[1], d = fmaxf(sqrtf(ex * ex + ey * ey), 0.03f);
        const float sd = d - r[2], iw2 = 1.f / (r[5] * r[5]), e = __expf(-sd * sd * iw2);
        if (e < 1e-3f) continue;
        float sn, cs; sincosf(r[4] * sd, &sn, &cs);
        const float k = r[4], a = r[3] * e;
        const float f1 = a * (k * cs - 2.f * sd * iw2 * sn);
        const float f2v = a * (-4.f * sd * iw2 * k * cs + (4.f * sd * sd * iw2 * iw2 - k * k - 2.f * iw2) * sn);
        const float nx = ex / d, ny = ey / d, t = f1 / d;
        hxx += f2v * nx * nx + t * (1.f - nx * nx);
        hxy += (f2v - t) * nx * ny;
        hyy += f2v * ny * ny + t * (1.f - ny * ny);
    }
    return f3(hxx, hxy, hyy) * sc;
}

// Light through the surface lands at s + a grad h(s): intensity 1 / |det(I + a H)| (1 on flat water), clamped.
__device__ __forceinline__ float pv_caustic(float3 Hm, float a) {
    const float det = (1.f + a * Hm.x) * (1.f + a * Hm.z) - a * a * Hm.y * Hm.y;
    return fminf(1.14f / (fabsf(det) + 0.14f), 4.5f);
}

// Exit from the pool box's side walls along d, from a point o inside the opening (t >= 0); xw = an x wall.
__device__ __forceinline__ float pv_exit_t(float3 o, float3 d, float A, bool& xw) {
    const float tx = fabsf(d.x) > 1e-6f ? (copysignf(A, d.x) - o.x) / d.x : 1e9f;
    const float ty = fabsf(d.y) > 1e-6f ? (copysignf(1.f, d.y) - o.y) / d.y : 1e9f;
    xw = tx < ty;
    return fmaxf(fminf(tx, ty), 0.f);
}

// Square tiles with grout on a plane (a, b in world units); fp = world size of a pixel there.
__device__ float3 pv_tiles(float a, float b, float ts, float fp, float3 base, float var, unsigned int salt) {
    const float ua = a / ts, ub = b / ts;
    const float ia = floorf(ua), ib = floorf(ub);
    const float fa = ua - ia, fb = ub - ib;
    const float d = fminf(fminf(fa, 1.f - fa), fminf(fb, 1.f - fb)) * ts;   // to the grout centre line
    const float gw = 0.05f * ts, e = fmaxf(fp, 1e-5f);
    float tile = smoothstep(gw - e, gw + e, d);
    tile = mix(tile, 0.82f, saturate(fp / ts * 1.5f - 0.5f));                // minified: average out
    const float hv = hash1((unsigned int)((int)ia + 65536), (unsigned int)((int)ib + 65536), salt);
    float3 tc = base * (1.f + var * (hv - 0.5f));
    tc = tc * (0.9f + 0.1f * smoothstep(0.f, 0.3f * ts, d));                 // glazed bevel
    const float3 grout = f3(luma3(base) * 0.5f + 0.22f);
    return mix(grout, tc, tile);
}

// The surround at the coping-top plane: bullnose coping stones, then a deck of large tiles fading into the dark.
__device__ float3 pv_deck(float3 p, float A, float fp, const float* __restrict__ P) {
    const float ex = fabsf(p.x) - A, ey = fabsf(p.y) - 1.f;
    const float e = fmaxf(fmaxf(ex, ey), 0.f);
    const float cw = 0.14f;
    const float3 stone = f3(0.90f, 0.87f, 0.80f);
    if (e < cw) {
        const float u = e / cw;
        float sh = 0.55f + 0.45f * smoothstep(0.f, 0.45f, u) + 0.14f * __expf(-(u - 0.24f) * (u - 0.24f) * 80.f);
        sh *= 1.f - 0.2f * smoothstep(0.86f, 1.f, u);
        const float al = ex > ey ? p.y : p.x;
        const float jd = fabsf(fract(al * 2.f + 0.5f) - 0.5f) * 0.5f;
        const float jl = smoothstep(0.004f - fp, 0.004f + fp, jd);
        return stone * (sh * (0.78f + 0.22f * jl));
    }
    const float3 tint = pal(P[P_DRIFT] + 0.62f, P + P_PAL);
    const float3 base = mix(stone * 0.82f, tint, 0.25f);
    const float3 c = pv_tiles(fabsf(p.x) - A - cw, fabsf(p.y) - 1.f - cw, 0.36f, fp, base, 0.12f, 0x2545u);
    return c * (0.45f + 0.55f * __expf(-1.3f * (e - cw)));
}

// Dark glazed waterline tiles on the walls between the coping and the water, with a wet meniscus line.
__device__ float3 pv_band(float a, float z, float zw, float fp, const float* __restrict__ P) {
    const float3 deep = pal(P[P_DRIFT] + 0.45f, P + P_PAL);
    const float3 base = deep * (0.45f / fmaxf(vmax(deep), 0.05f)) + f3(0.04f);
    float3 c = pv_tiles(a, z, P[P_TILE] * 0.7f, fp, base, 0.3f, 0x51edu);
    c += f3(0.5f) * (1.f - smoothstep(0.f, 2.f * fp + 0.004f, z - zw));
    return c;
}

// Pale mosaic on the underwater walls (darker towards the floor).
__device__ float3 pv_wall(float a, float z, float fp, const float* __restrict__ P) {
    const float3 base = mix(f3(0.94f, 0.96f, 0.97f), pal(P[P_DRIFT] + 0.1f, P + P_PAL), 0.45f);
    const float3 c = pv_tiles(a, z, P[P_TILE], fp, base, 0.18f, 0x9e37u);
    return c * (0.62f + 0.2f * smoothstep(0.f, 0.12f, z));
}

SDF_KERNEL
void pool_view(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P,
               const float* __restrict__ Wp)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const float A = (float)W / (float)H;
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 rd = cam_dir(cam, px, py, w, h);
    const float pa = pixel_angle(cam, h);
    const float cosa = fmaxf(dot(rd, cam.fwd), 0.05f);
    const float rz = fminf(rd.z, -1e-3f);
    const float zw = P[P_ZW], zt = P[P_ZT];
    const float kfp = pa * cosa * cosa / fmaxf(-rz, 0.1f);      // world pixel size per unit ray length on a z plane

    // 1. the coping-top plane: outside the opening is the surround
    const float tt = (zt - cam.eye.z) / rz;
    const float3 pt = cam.eye + rd * tt;
    const float fpt = fmaxf(tt * kfp, 1e-6f);
    const float cov_o = saturate(0.5f - fmaxf(fabsf(pt.x) - A, fabsf(pt.y) - 1.f) / fpt);
    float3 col = f3(0.f);
    if (cov_o < 1.f) col = pv_deck(pt, A, fpt, P);
    if (cov_o > 0.f) {
        // 2. down to the water level; between the coping and the water the far walls show their waterline band
        const float tw = (zw - cam.eye.z) / rz;
        const float3 pw = cam.eye + rd * tw;
        const float fpw = fmaxf(tw * kfp, 1e-6f);
        const float cov_w = saturate(0.5f - fmaxf(fabsf(pw.x) - A, fabsf(pw.y) - 1.f) / fpw);
        float3 pc = f3(0.f);
        if (cov_w < 1.f) {
            bool xw;
            const float tb = pv_exit_t(pt, rd, A, xw);
            const float3 q = pt + rd * tb;
            const float fq = (tt + tb) * pa * cosa / fmaxf(fabsf(xw ? rd.x : rd.y), 0.1f);
            pc = pv_band(xw ? q.y : q.x, q.z, zw, fq, P);
        }
        if (cov_w > 0.f) {
            // 3. the water surface (waves + beat rings), refracted view ray
            const float2 qs = f2(clamp(pw.x, -A, A), clamp(pw.y, -1.f, 1.f));
            const PvS S = pv_surf(Wp, qs, P[P_WS]);
            const float3 s = f3(qs, fmaxf(zw + S.h, 0.25f * zw));
            const float3 n = normalize(f3(-S.g.x, -S.g.y, 1.f));
            const float ci = saturate(-dot(rd, n));
            float3 r = refract3(rd, n, 0.75f);
            r.z = fminf(r.z, -0.05f);
            r = normalize(r);
            // 4. under water: the floor (the picture) or a tiled wall
            const float tf = s.z / -r.z;
            bool xw;
            const float tx = pv_exit_t(s, r, A, xw);
            float3 c;
            float L, dep, kw;
            float2 cq;
            if (tx < tf) {
                const float3 q = s + r * tx;
                L = tx; dep = fmaxf(zw - q.z, 0.f); cq = f2(q.x, q.y); kw = 0.6f;
                const float fq = (tw + L) * pa * cosa / fmaxf(fabsf(xw ? r.x : r.y), 0.15f);
                c = pv_wall(xw ? q.y : q.x, q.z, fq, P);
            } else {
                const float3 f = s + r * tf;
                L = tf; dep = s.z; cq = f2(f.x, f.y); kw = 1.f;
                const float lod = tex_lod(tw + L, pa, cosa, fabsf(r.z), frame_texel(T), P[P_BIAS]);
                c = tex_sample(T, 0.5f + 0.5f * f.x / A, 0.5f - 0.5f * f.y, lod);
                const float de = fminf(A - fabsf(f.x), 1.f - fabsf(f.y));
                c = c * (1.f - 0.35f * P[P_AO] * (1.f - smoothstep(0.f, 0.3f * zw + 1e-4f, de)));
            }
            // caustics: dark between the lines, bright (screen) on them
            float3 wc = pal(P[P_DRIFT] + P[P_PER] * (0.35f * L + 0.12f * (cq.x + cq.y)), P + P_PAL);
            wc = wc * (1.f / fmaxf(vmax(wc), 0.05f));
            const float I = pv_caustic(pv_hess(Wp, cq, P[P_WS]), P[P_CA] * dep);
            const float kc = P[P_CK] * kw;
            c = c * (1.f - kc * 0.5f * (1.f - fminf(I, 1.f)));
            c = pv_screen(c, (f3(1.f, 0.97f, 0.88f) * 0.75f + wc * 0.25f) * (fmaxf(I - 1.f, 0.f) * kc * 0.32f));
            // absorption + in-scatter along the underwater path (palette water colour)
            const float tk = P[P_TINT];
            const float3 sig = (f3(1.04f) - wc) * (2.8f * tk) + f3(0.15f * tk);
            const float3 tr = exp(sig * (-L));
            c = c * tr + wc * 0.5f * (f3(1.f) - tr);
            // surface: Fresnel sky reflection and the sun glint
            const float R = fresnel_schlick(ci, 0.02f);
            const float3 rr = reflect(rd, n);
            const float3 sky = mix(f3(0.97f, 0.96f, 0.93f), mix(f3(0.36f, 0.58f, 0.95f), wc * 0.85f, 0.35f),
                                   saturate(rr.z));
            c = mix(c, sky, saturate(R * P[P_SKY]));
            const float hd = saturate(dot(n, normalize(load3(P + P_SUN) - rd)));
            const float g = 3.f * __powf(hd, 900.f) + 0.12f * __powf(hd, 90.f);
            c = pv_screen(c, f3(1.f, 0.96f, 0.86f) * (g * P[P_GLINT]));
            pc = mix(pc, c, cov_w);
        }
        col = mix(col, pc, cov_o);
    }
    store_rgb(out, y * w + x, col);
}
"""


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


class PoolView(Effect):
    name = "pool_view"
    stage = "projection"
    label = "Pool floor"
    description = ("The picture becomes the floor of a swimming pool: refracting waves, dancing caustics, beat "
                   "ripples, a gently tilted camera and a tiled rim.")
    params = {
        **standard_effect_params(),
        "depth": num(0.05, 1.0, 0.55, "Water depth", tier="regional", basic=True),
        "chop": num(0.0, 1.0, 0.5, "Wave chop", tier="regional"),
        "caustics": num(0.0, 1.0, 0.7, "Caustics", tier="regional"),
        "tint": num(0.0, 1.0, 0.45, "Aqua tint", tier="regional"),
        "camera_tilt": num(0.0, 25.0, 12.0, "Camera tilt", unit="°", step=0.5, tier="regional"),
        "rim": num(0.0, 1.0, 0.35, "Pool rim", tier="regional"),
        "glint": num(0.0, 1.0, 0.5, "Sky glint", tier="regional"),
        "flow": num(0.0, 2.0, 0.6, "Wave speed", tier="regional"),
        "ripple": num(0.0, 1.0, 0.6, "Beat ripples", tier="regional"),
        **palette_params("ocean", 1.0, 0.02),
        "seed": num(1, 999, 7, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("pool_view")
        self.mips = FrameMips(ctx, levels=3, blur_level=0)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.A = ctx.width / ctx.height
        p0 = self.easer.values()
        seed = int(p0.get("seed", 7) or 7)
        rng = np.random.default_rng(seed)
        A = self.A
        self.water = Water(ctx, n_waves=7, seed=seed, wavelength=0.45, amplitude=0.012,
                           direction=float(rng.uniform(0.0, 2.0 * math.pi)), spread=2.6, ring_speed=0.8,
                           ring_wavelength=0.16, ring_width=0.2, ring_life=2.8,
                           bounds=(-0.8 * A, -0.8, 0.8 * A, 0.8))
        self.spots = rng.uniform(-0.78, 0.78, (64, 2)) * np.array([A, 1.0])
        self.spot_i = 0
        self.prev_pulse = 0.0
        self.since = 1.0
        self.ph = rng.uniform(0.0, 2.0 * math.pi, 4)
        self.drift = 0.0
        self._pal = np.array(palette_coeffs(p0.get("palette", "ocean")), dtype=np.float64)

    def _fit(self, zt: float, yaw: float, pitch: float, roll: float) -> float:
        """Largest camera distance from (0, 0, zt) at which every screen corner still lands inside the pool opening
        on the coping-top plane (hits scale linearly with distance, so this is closed form)."""
        A = self.A
        c = camera_orbit((0.0, 0.0, zt), 1.0, yaw, pitch, roll, FOV)
        b = (c[0], c[1], c[2] - zt)
        fwd, right, up, th = c[3:6], c[6:9], c[9:12], c[12]
        best = 1e9
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                d = [fwd[i] + right[i] * sx * A * th + up[i] * sy * th for i in range(3)]
                if d[2] >= -1e-6:
                    continue
                vx = b[0] - b[2] * d[0] / d[2]
                vy = b[1] - b[2] * d[1] / d[2]
                if abs(vx) > 1e-9:
                    best = min(best, A / abs(vx))
                if abs(vy) > 1e-9:
                    best = min(best, 1.0 / abs(vy))
        return best

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dts = _clamp(float(dt), 0.0, 0.1)
        a = _clamp(float(p["amount"]), 0.0, 1.0)
        pulse = _clamp(float(p["pulse"]), 0.0, 1.0)
        m = smoothstep01(a)

        # host-integrated motion (always, so a fade-in picks up mid-flow)
        self.ph = np.mod(self.ph + np.array([0.11, 0.077, 0.053, 0.031]) * dts, 2.0 * math.pi)
        self.drift = (self.drift + float(p["drift_speed"]) * dts) % 1.0
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dts > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
        # beat: a ripple ring on each rising edge (refractory 120 ms)
        self.since += dts
        if pulse - self.prev_pulse > 0.15 and self.since > 0.12 and m > 1e-3 and p["ripple"] > 0.0:
            x, y = self.spots[self.spot_i % len(self.spots)]
            self.spot_i += 1
            self.water.spawn(float(x), float(y), 0.0075 * float(p["ripple"]) * (0.6 + 0.4 * pulse))
            self.since = 0.0
        self.prev_pulse = pulse
        self.water.step(dts, speed=0.2 + float(p["flow"]), pulse=0.0, amp_scale=1.0, ring_amp=0.0)

        if a <= 1e-4:
            cp.copyto(out, src)
            return

        # pool + camera
        zw = m * (0.08 + 0.55 * float(p["depth"]))
        zt = zw + 0.05 * m
        pitch = -math.radians(float(p["camera_tilt"])) * m + 0.022 * m * math.sin(self.ph[0])
        yaw = 0.03 * m * math.sin(self.ph[1])
        roll = 0.01 * m * math.sin(self.ph[2])
        dist = self._fit(zt, yaw, pitch, roll) * (1.0 + 0.55 * float(p["rim"]) * m)
        cam = camera_orbit((0.0, 0.0, zt), dist, yaw, pitch, roll, FOV)
        sa = self.ph[3] * 3.0
        sun = np.array([0.38 * math.cos(sa), 0.38 * math.sin(sa) + 0.12, 1.0])
        sun /= np.linalg.norm(sun)

        h = self.host
        h[0:13] = cam
        h[13] = m
        h[14] = zw
        h[15] = zt
        h[16] = m * (0.25 + 1.5 * float(p["chop"])) * (1.0 + 0.15 * pulse)
        h[17] = 0.25 * (0.5 + 3.5 * float(p["caustics"]))
        h[18] = m * float(p["caustics"]) * (1.0 + 0.9 * pulse)
        h[19] = m * float(p["tint"])
        h[20] = m * float(p["glint"]) * (1.0 + 1.2 * pulse)
        h[21] = m * (0.6 + 2.4 * float(p["glint"]))
        h[22] = -0.3
        h[23:26] = sun
        h[26] = self.drift
        h[27] = float(p["palette_period"])
        h[28] = 0.085
        h[29] = m
        h[30:42] = self._pal
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P, self.water.buf))
