"""Inside the Cube: the camera stands at the centre of a box room.  The old picture fills the wall in front at
progress 0; the camera yaws 90 degrees (or pitches, or turns 180 degrees through two walls) to face the wall that
carries the new picture.  The room's seams glow in the palette with light flowing along them, a dolly-zoom FOV
pump at mid-turn adds vertigo (the focus wall holds its size while the room stretches around it), the side walls
carry dimmed mirrored copies (continuous across every corner) and the floor and ceiling fold the walls into a
tinted kaleidoscope.  The opposite of the batch-1 cube, seen from outside.

Source: Original (inspired by https://github.com/gl-transitions/gl-transitions/blob/master/transitions/cube.glsl)
Author: smfx (original; projection maths referenced from gre's gl-transitions cube.glsl, MIT)
License: MIT (original)
license_class: permissive

Geometry (frame3d toolkit, analytic, full res): canonical turn space X = the axis the camera turns towards,
Y = the cross axis, Z = backwards; the camera starts looking down -Z.  Yaw: room half sizes (A, 1, A) with
tan(fov/2) = 1/A (a 90-degree horizontal view: every side wall fills the screen exactly from the centre).  Pitch:
(1, A, 1) with tan(fov/2) = 1 (90-degree vertical view).  So the front wall is the flat identity at progress 0 and
the destination wall at progress 1.  FOV pump, bank, seam glow, corner shading and vignette are all weighted by
sin(pi p).  Seams are 4x supersampled (only pixels within ~1 px of an edge pay for it).

Binder: style lively, 1-2 bars; the vertigo peak lands at progress 0.5.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, flag, num, palette_params
from stream_service.plugins._lib.sdf3d import SDF3D

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
// P layout (see CubeInside.render)
#define CI_EYE 0
#define CI_FWD 3
#define CI_TV 6
#define CI_TAN 9
#define CI_CB 10
#define CI_SB 11
#define CI_HT 12
#define CI_HY 13
#define CI_SGN 14
#define CI_ENV 15
#define CI_GLOW 16
#define CI_WDIM 17
#define CI_CDIM 18
#define CI_DRIFT 19
#define CI_PERIOD 20
#define CI_FLOW 21
#define CI_CORE 22
#define CI_HALO 23
#define CI_AO 24
#define CI_VIG 25
#define CI_PAL 26

// Sample image T at wall coords s (along the turn, -1..1) and c (across, -1..1): the screen mapping of the
// current axis/direction, so a wall seen square-on from the room centre reads exactly as the frame.
__device__ __forceinline__ float3 ci_tex(const Tex& T, float s, float c, float lod, int pitch, float sgn) {
    const float sx = pitch ? c : sgn * s;
    const float sy = pitch ? sgn * s : c;
    return tex_sample(T, 0.5f + 0.5f * sx, 0.5f - 0.5f * sy, lod);
}

// Content of turn-plane wall k (0 front = a, then in turn order).  Side walls are mirrored along s, so every
// corner is continuous; with two turns the side wall fades from a mirrored copy of a to one of b.
__device__ float3 ci_wall(const Tex& TA, const Tex& TB, int k, float s, float c, float lod, int pitch, float sgn,
                          int turns, float dim) {
    if (k == 0) return ci_tex(TA, s, c, lod, pitch, sgn);
    if (turns == 1) {
        if (k == 1) return ci_tex(TB, s, c, lod, pitch, sgn);
        if (k == 2) return ci_tex(TB, -s, c, lod, pitch, sgn) * dim;
        return ci_tex(TA, -s, c, lod, pitch, sgn) * dim;
    }
    if (k == 2) return ci_tex(TB, s, c, lod, pitch, sgn);
    const float wb = smoothstep(-0.45f, 0.45f, k == 1 ? s : -s);
    float3 r = f3(0.f);
    if (wb < 0.999f) r = ci_tex(TA, -s, c, lod, pitch, sgn) * (1.f - wb);
    if (wb > 0.001f) r = r + ci_tex(TB, -s, c, lod, pitch, sgn) * wb;
    return r * dim;
}

// Floor / ceiling: fold the nearest turn-plane wall across its seam (mitre-blended at the diagonals).
__device__ float3 ci_cap_one(const Tex& TA, const Tex& TB, float3 p, bool useZ, float hT, float hY, float lod,
                             int pitch, float sgn, int turns, float dim) {
    int k; float s, e;
    if (useZ) { k = p.z < 0.f ? 0 : 2; s = (k == 0 ? p.x : -p.x) / hT; e = hT - fabsf(p.z); }
    else      { k = p.x > 0.f ? 1 : 3; s = (k == 1 ? p.z : -p.z) / hT; e = hT - fabsf(p.x); }
    float c = (p.y > 0.f ? 1.f : -1.f) * (1.f - e / hY);
    c = 2.f * uv_mirror(0.5f + 0.5f * c) - 1.f;                              // kaleidoscope fold
    return ci_wall(TA, TB, k, s, c, lod, pitch, sgn, turns, dim);
}

// One camera ray through screen pixel (px, py): returns the colour and the distance to the nearest seam in pixels.
__device__ float3 ci_shade(const Tex& TA, const Tex& TB, const float* __restrict__ P, float px, float py, int w,
                           int h, int pitch, int turns, float& edge_px) {
    const float3 eye = f3(P[CI_EYE], P[CI_EYE + 1], P[CI_EYE + 2]);
    const float3 fwd = f3(P[CI_FWD], P[CI_FWD + 1], P[CI_FWD + 2]);
    const float3 tv = f3(P[CI_TV], P[CI_TV + 1], P[CI_TV + 2]);
    const float tanh_ = P[CI_TAN], cb = P[CI_CB], sb = P[CI_SB], hT = P[CI_HT], hY = P[CI_HY], sgn = P[CI_SGN];
    const float sc = tanh_ / (0.5f * h);
    const float sx = (px - 0.5f * w) * sc, sy = (0.5f * h - py) * sc;
    const float rx = cb * sx - sb * sy, ry = sb * sx + cb * sy;            // bank (roll)
    float3 rd = pitch ? fwd + tv * (sgn * ry) + f3(0.f, rx, 0.f) : fwd + tv * (sgn * rx) + f3(0.f, ry, 0.f);
    rd = rd * rsqrtf(dot(rd, rd));

    // exit wall of the room box (the eye is always inside)
    float dx = rd.x, dy = rd.y, dz = rd.z;
    if (fabsf(dx) < 1e-8f) dx = 1e-8f;
    if (fabsf(dy) < 1e-8f) dy = 1e-8f;
    if (fabsf(dz) < 1e-8f) dz = 1e-8f;
    const float tX = ((dx > 0.f ? hT : -hT) - eye.x) / dx;
    const float tY = ((dy > 0.f ? hY : -hY) - eye.y) / dy;
    const float tZ = ((dz > 0.f ? hT : -hT) - eye.z) / dz;
    int axis; float t;
    if (tX <= tY && tX <= tZ) { axis = 0; t = tX; } else if (tY <= tZ) { axis = 1; t = tY; } else { axis = 2; t = tZ; }
    const float3 p = eye + rd * t;

    const float pa = 2.f * tanh_ / (float)h;
    const float cosa = dot(rd, fwd);
    const float ndv = fabsf(axis == 0 ? rd.x : (axis == 1 ? rd.y : rd.z));
    const float lod = tex_lod(t, pa, cosa, ndv, 2.f / (float)TA.H, -0.25f);
    const float wdim = 1.f - P[CI_WDIM];

    float3 col;
    float d1, d2, al1, al2;
    if (axis == 1) {                                                       // floor / ceiling
        const float ax = fabsf(p.x), az = fabsf(p.z);
        const float band = 0.035f * hT, dd = az - ax;
        const float cdim = wdim * (1.f - P[CI_CDIM]);
        const float lc = lod + 1.5f;
        if (dd > band) col = ci_cap_one(TA, TB, p, true, hT, hY, lc, pitch, sgn, turns, cdim);
        else if (dd < -band) col = ci_cap_one(TA, TB, p, false, hT, hY, lc, pitch, sgn, turns, cdim);
        else {
            const float wz = smoothstep(-band, band, dd);
            col = mix(ci_cap_one(TA, TB, p, false, hT, hY, lc, pitch, sgn, turns, cdim),
                      ci_cap_one(TA, TB, p, true, hT, hY, lc, pitch, sgn, turns, cdim), wz);
        }
        const float3 tint = pal(P[CI_DRIFT] + 0.5f + 0.18f * P[CI_PERIOD] * (p.x - p.z) / hT, P + CI_PAL);
        col = col * (f3(0.55f) + tint * 0.9f) + tint * (0.05f * P[CI_ENV]);
        d1 = hT - ax; d2 = hT - az; al1 = p.z / hT; al2 = p.x / hT;
    } else {
        int k; float s;
        if (axis == 0) { k = p.x > 0.f ? 1 : 3; s = (k == 1 ? p.z : -p.z) / hT; d2 = hT - fabsf(p.z); al1 = p.z / hT; }
        else           { k = p.z < 0.f ? 0 : 2; s = (k == 0 ? p.x : -p.x) / hT; d2 = hT - fabsf(p.x); al1 = p.x / hT; }
        d1 = hY - fabsf(p.y); al2 = p.y / hY;
        col = ci_wall(TA, TB, k, s, p.y / hY, lod, pitch, sgn, turns, wdim);
    }
    const float d = fmaxf(fminf(d1, d2), 0.f);
    const float along = d1 < d2 ? al1 : al2;
    const float fp = fmaxf(t * pa, 1e-7f);
    edge_px = d * fmaxf(ndv, 0.05f) / fp;

    // corner shading: the room reads as a room (weighted by sin(pi p))
    const float r0 = 0.3f * fminf(hT, hY);
    col = col * (1.f - P[CI_AO] * __expf(-d / r0));

    // seams: a crisp core (screen px) + a soft halo of light spilling onto the walls (world units), flowing
    const float g = P[CI_GLOW];
    if (g > 0.f) {
        const float core = __expf(-edge_px * edge_px * P[CI_CORE]);
        const float halo = __expf(-d * P[CI_HALO]);
        const float flow = 0.68f + 0.32f * __sinf(F3D_TAU * (1.25f * along - P[CI_FLOW]));
        const float idx = P[CI_PERIOD] * (0.3f * (p.x - p.z) / hT + 0.2f * p.y / hY) + P[CI_DRIFT];
        const float3 gc = pal(idx, P + CI_PAL);
        const float k = g * (core + 0.45f * halo) * flow;
        const float3 add = f3(saturate(gc.x * k), saturate(gc.y * k), saturate(gc.z * k));
        col = f3(1.f) - (f3(1.f) - f3(saturate(col.x), saturate(col.y), saturate(col.z))) * (f3(1.f) - add);
    }
    return col;
}

SDF_KERNEL
void cube_inside(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b),
                 const float* __restrict__ P, const int pitch, const int turns)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    const float px = x + 0.5f, py = y + 0.5f;
    float ep;
    float3 col = ci_shade(TA, TB, P, px, py, w, h, pitch, turns, ep);
    if (ep < 1.25f) {                                                      // seam: 4 rotated-grid sub-rays
        const float ox[4] = {-0.125f, 0.375f, 0.125f, -0.375f}, oy[4] = {-0.375f, -0.125f, 0.375f, 0.125f};
        float3 acc = f3(0.f);
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            float e2;
            acc = acc + ci_shade(TA, TB, P, px + ox[i], py + oy[i], w, h, pitch, turns, e2);
        }
        col = acc * 0.25f;
    }
    col = col * vignette_k(px / w, py / h, P[CI_VIG]);
    store_rgb(out, y * w + x, col);
}
"""

_N_P = 40


class CubeInside(Transition):
    name = "cube_inside"
    label = "Inside the cube"
    description = ("The camera stands inside a box room and turns from the wall carrying the old picture to the "
                   "wall carrying the new one: glowing palette seams, a dolly-zoom vertigo pump at mid-turn and "
                   "mirrored copies on the side walls, floor and ceiling.")
    tags = {"family": "3d", "dims": "3d", "toolkit": "frame3d", "technique": ["projection", "analytic"],
            "moods": ["hypnotic", "tense"], "style": "lively", "energy": [0.3, 1.0]}
    default_duration_s = 2.0
    params = {
        "fov_pump": num(-0.4, 1.2, 0.55, "FOV pump (dolly zoom)", tier="regional", basic=True),
        "seam_glow": num(0.0, 1.0, 0.6, "Seam glow", tier="regional"),
        "wall_dim": num(0.0, 0.9, 0.35, "Side wall dim", tier="regional"),
        "cap_dim": num(0.0, 0.9, 0.3, "Floor/ceiling dim", tier="regional"),
        "bank": num(-20.0, 20.0, 5.0, "Bank at mid-turn", unit="°", step=0.5, tier="regional"),
        "vignette": num(0.0, 1.0, 0.45, "Vignette", tier="regional"),
        **palette_params("neon", period=0.8, drift=0.08),
        "axis": enum(["yaw", "pitch"], "yaw", "Turn axis", tier="structural"),
        "turns": num(1, 2, 1, "Quarter turns (1 = 90°, 2 = 180° through two walls)", tier="structural",
                     integer=True),
        "reverse": flag(False, "Reverse direction (left / down)", tier="structural"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("cube_inside")
        self._mips_a = FrameMips(ctx, levels=4, blur_level=0)
        self._mips_b = FrameMips(ctx, levels=4, blur_level=0)
        self._P = cp.zeros(_N_P, dtype=cp.float32)
        self._Ph = np.zeros(_N_P, np.float32)
        self._pal_name = None
        self._pal = np.zeros(12, np.float32)

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        p = float(progress)
        if p <= 1e-4:
            cp.copyto(out, a)
            return
        if p >= 0.9999:
            cp.copyto(out, b)
            return
        prm = self.params
        name = prm.get("palette", "neon")
        if name != self._pal_name:
            self._pal[:] = np.asarray(palette_coeffs(name), np.float32)
            self._pal_name = name
        w, h = self.ctx.width, self.ctx.height
        A = w / h
        pitch = 1 if prm.get("axis", "yaw") == "pitch" else 0
        turns = 2 if int(round(float(prm.get("turns", 1)))) >= 2 else 1
        sgn = -1.0 if prm.get("reverse", False) else 1.0
        if pitch:
            hT, hY, tan0 = 1.0, A, 1.0
        else:
            hT, hY, tan0 = A, 1.0, 1.0 / A
        env = math.sin(math.pi * p)

        # camera: turn by quarter turns, dolly-zoom about the wall point straight ahead
        th = turns * 0.5 * math.pi * p
        st, ct = math.sin(th), math.cos(th)
        fwd = (st, 0.0, -ct)
        tv = (ct, 0.0, st)
        k = max(float(prm["fov_pump"]) * env, -0.6)
        tan1 = tan0 * (1.0 + k)
        dist = hT / max(abs(st), abs(ct), 1e-6)                    # centre -> wall straight ahead
        back = dist * k / (1.0 + k)
        eye = (-fwd[0] * back, 0.0, -fwd[2] * back)
        phi = math.radians(float(prm["bank"])) * env * sgn
        cb, sb = math.cos(phi), math.sin(phi)

        secs = p * self.duration_s
        drift = float(prm["drift_speed"]) * secs + 0.35 * p
        drift -= math.floor(drift)
        flow = 0.9 * secs + 0.5 * p
        flow -= math.floor(flow)
        glow = float(prm["seam_glow"])
        genv = glow * env ** 0.7
        core_px = (1.0 + 2.2 * glow) * h / 1080.0
        halo_w = 0.05 + 0.12 * glow

        P = self._Ph
        P[0:3] = eye
        P[3:6] = fwd
        P[6:9] = tv
        P[9] = tan1
        P[10], P[11] = cb, sb
        P[12], P[13] = hT, hY
        P[14] = sgn
        P[15] = env
        P[16] = 1.15 * genv
        P[17] = float(prm["wall_dim"])
        P[18] = float(prm["cap_dim"])
        P[19] = drift
        P[20] = float(prm["palette_period"])
        P[21] = flow
        P[22] = 1.0 / (core_px * core_px)
        P[23] = 1.0 / halo_w
        P[24] = 0.4 * env
        P[25] = float(prm["vignette"]) * 1.2 * env
        P[26:38] = self._pal
        self._P.set(P)

        self._mips_a.build(a)
        self._mips_b.build(b)
        launch_2d(self._k, w, h,
                  (out, np.int32(w), np.int32(h), *self._mips_a.args(a), *self._mips_b.args(b),
                   self._P, np.int32(pitch), np.int32(turns)))
