"""Globe: the picture curls up into a lit, spinning world with a glowing atmosphere, floating over a dimmed copy of itself.

Source: Original (inspired by https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Sphere%20Map.fs ;
        licence https://github.com/Vidvox/ISF-Files/blob/master/LICENSE)
Author: VIDVOX (ISF Sphere Map, mapping reference); curvature morph, rotation and lighting by smfx
License: MIT
license_class: permissive

amount is curvature.  At amount 0 the frame is the flat sheet at the screen plane (copied, exact identity).  As
amount rises the sheet bends into a ball of radius 1 / kappa that stays tangent to the screen plane at the centre,
so small kappa is nearly flat and the image centre never moves.  The texture mapping is arc length on the sphere
(longitude x R, latitude x R), which is the flat mapping in the limit; it blends to a true equirectangular wrap at
full amount (``mirror``: the picture once across the front hemisphere and mirrored round the back, seamless;
``single``: the picture once round the whole globe).  The ball shrinks to ``size`` (diameter in screen heights),
the axis tilts, it spins (frame3d.HomingAngle, so it springs home as amount falls), and it is relit by a key light
from the upper left (terminator crescent, Blinn highlight, a Fresnel gloss of the blurred "room"), wrapped in a
palette-coloured rim atmosphere (inner Fresnel rim + outer halo, brighter on the lit limb), casting a soft shadow
on a dimmed, blurred copy of the source with a sparse star field.  Pulse kicks the size (a decaying swell), the spin
rate and the atmosphere / highlight gain.  Single full-res analytic pass (ray vs sphere in a form that stays exact
for the huge radii near amount 0) plus FrameMips.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import (FRAME3D, FRAME3D_OPTIONS, FrameMips, HomingAngle, camera_flat,
                                                 flat_distance, smoothstep01)
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D, rot_matrix

FOV = 40.0
NP = 64
# P layout (floats)
#  0..12 camera | 13..21 globe axis frame M (world = M local) | 22..24 centre | 25 R | 26 cq = |eye - c|^2 - R^2
#  27 m | 28 su | 29 sv | 30 u scroll | 31 mirror | 32 light k | 33 atmosphere gain | 34 backdrop dim
#  35 backdrop zoom | 36 vignette | 37 shadow | 38,39 shadow offset px | 40 stars | 41 drift | 42 palette period
#  43..45 key dir | 46 spec gain | 47 lod bias | 48..59 palette
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_M3 13
#define P_C 22
#define P_R 25
#define P_CQ 26
#define P_MORPH 27
#define P_SU 28
#define P_SV 29
#define P_SCROLL 30
#define P_MIRROR 31
#define P_LIGHT 32
#define P_ATM 33
#define P_BGDIM 34
#define P_BGZOOM 35
#define P_VIG 36
#define P_SHADOW 37
#define P_SHOFF 38
#define P_STARS 40
#define P_DRIFT 41
#define P_PERIOD 42
#define P_KEY 43
#define P_SPEC 46
#define P_BIAS 47
#define P_PAL 48

__device__ __forceinline__ float3 gw_screen(float3 a, float3 b) {
    b = saturate(b);
    return f3(1.f) - (f3(1.f) - a) * (f3(1.f) - b);
}
// Signed distance (world units at the globe, + inside) of a ray from the globe's silhouette.  q = eye - c,
// b = dot(q, rd), cq = |q|^2 - R^2 from the host in double precision: no cancellation even for R ~ 1e7.
__device__ __forceinline__ float gw_sil(float b, float cq, float R) {
    const float disc = b * b - cq;
    const float dp = sqrtf(fmaxf(R * R - disc, 0.f));
    return disc / (R + dp);
}
// Atmosphere colour: a smooth (seam-free) palette field over the globe's outward direction.
__device__ __forceinline__ float3 gw_atm_col(float3 n, const float* __restrict__ P) {
    return pal(P[P_DRIFT] + P[P_PERIOD] * (0.24f * n.y + 0.11f * n.x), P + P_PAL);
}

SDF_KERNEL
void globe_wrap(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const float px = x + 0.5f, py = y + 0.5f;
    const float pa = pixel_angle(cam, h);
    const float3 rd = cam_dir(cam, px, py, w, h);
    const float3 c = load3(P + P_C);
    const float R = P[P_R], cq = P[P_CQ];
    const float3 q = cam.eye - c;
    const float b = dot(q, rd);
    const float sil = gw_sil(b, cq, R);
    const float fp = pa * sqrtf(fmaxf(cq, 1e-8f));                   // world size of a pixel at the limb
    const float cover = b < 0.f ? saturate(sil / fp + 0.5f) : 0.f;
    const float3 L = load3(P + P_KEY);
    const float atm = P[P_ATM];

    // ---------------------------------------------------------------- backdrop, stars, shadow, outer halo
    float3 col = f3(0.f);
    if (cover < 1.f) {
        col = bg_blur(T, px / w, py / h, P[P_BGZOOM], P[P_BGDIM], P[P_VIG]);
        const float sa = P[P_STARS];
        if (sa > 0.f) {
            const float cs = fmaxf(3.f, (float)h * (1.f / 220.f));        // star cell (px)
            const float gx = px / cs, gy = py / cs, fx = floorf(gx), fy = floorf(gy);
            const unsigned int ix = (unsigned int)(int)fx, iy = (unsigned int)(int)fy;
            const float sel = hash1(ix, iy, 0x51u);
            if (sel < 0.075f) {
                const float ox = 0.2f + 0.6f * hash1(ix, iy, 0x52u), oy = 0.2f + 0.6f * hash1(ix, iy, 0x53u);
                const float dx = (gx - fx - ox) * cs, dy = (gy - fy - oy) * cs;
                const float mag = hash1(ix, iy, 0x54u);
                const float rad = fmaxf(0.7f, (float)h * (1.f / 2700.f)) * (0.7f + 0.8f * mag);
                const float br = __expf(-(dx * dx + dy * dy) / (rad * rad)) * (0.18f + 0.82f * mag * mag * mag);
                const float3 tint = mix(f3(1.f), pal(P[P_DRIFT] + 3.7f * sel, P + P_PAL), 0.35f);
                col = gw_screen(col, tint * (br * sa));
            }
        }
        if (P[P_SHADOW] > 0.f) {                                        // soft drop shadow, away from the key
            const float3 rs = cam_dir(cam, px - P[P_SHOFF], py - P[P_SHOFF + 1], w, h);
            const float ss = gw_sil(dot(q, rs), cq, R);
            col = col * (1.f - P[P_SHADOW] * smoothstep(-0.45f * R, 0.3f * R, ss));
        }
        if (atm > 0.f) {
            const float e = fmaxf(-sil, 0.f) / R;
            const float3 nc = normalize(q - rd * b);                     // closest approach direction
            const float lit = 0.3f + 0.7f * saturate(dot(nc, L) * 0.8f + 0.45f);
            const float g = atm * lit * (0.55f * __expf(-9.f * e) + 0.45f * __expf(-34.f * e));
            col = gw_screen(col, gw_atm_col(nc, P) * g);
        }
    }

    // ---------------------------------------------------------------- the globe
    if (cover > 0.f) {
        const float disc = fmaxf(b * b - cq, 0.f);
        const float t = cq / fmaxf(-b + sqrtf(disc), 1e-12f);           // stable near root (grazing at the AA rim)
        const float3 pc = q + rd * t;
        const float3 n = pc * rsqrtf(fmaxf(dot(pc, pc), 1e-30f));
        const Mat3 M = mat3_load(P + P_M3);
        const float3 l = mul_t(M, n);
        const float phi = atan2f(l.x, l.z);
        const float sl = clamp(l.y, -1.f, 1.f);
        const float lat = asinf(sl);
        const float su = P[P_SU], sv = P[P_SV];
        float uu = 0.5f + phi * su + P[P_SCROLL];
        const float vv = uv_mirror(0.5f - lat * sv);
        uu = P[P_MIRROR] > 0.5f ? uv_mirror(uu) : uv_repeat(uu);
        const float cl = fmaxf(sqrtf(fmaxf(1.f - sl * sl, 0.f)), 0.02f);   // cos(latitude): pole pinch -> blur
        const float texel = R * sqrtf(cl / (su * sv * (float)W * (float)H));
        const float cosa = dot(rd, cam.fwd);
        const float ndv = fmaxf(-dot(rd, n), 0.f);
        const float lod = tex_lod(t, pa, cosa, ndv, texel, P[P_BIAS]);
        float3 cc = tex_sample(T, uu, vv, lod);

        const float k = P[P_LIGHT];
        if (k > 0.f) {
            cc = light_frame(cc, n, L, cam.fwd, 0.12f, k);
            const float F = fresnel_schlick(ndv, 0.04f);
            cc = cc + env_frame(T, reflect(rd, n)) * (F * 0.45f * k);
            cc = gw_screen(cc, f3(spec_blinn(n, -rd, L, 42.f) * P[P_SPEC]));
        }
        if (atm > 0.f) {
            const float lit = 0.3f + 0.7f * saturate(dot(n, L) * 0.8f + 0.45f);
            const float r1 = 1.f - ndv, rim = r1 * r1 * r1;
            cc = gw_screen(cc, gw_atm_col(n, P) * (atm * lit * rim));
        }
        col = cover < 1.f ? mix(col, cc, cover) : cc;
    }
    store_rgb(out, y * w + x, col);
}
"""


def _rot(axis, ang):
    return np.asarray(rot_matrix(axis, ang), np.float64).reshape(3, 3)


class GlobeWrap(Effect):
    name = "globe_wrap"
    stage = "projection"
    label = "Globe"
    description = "The picture curls into a lit, spinning world with a glowing atmosphere over a dimmed copy of itself."
    params = {
        **standard_effect_params(),
        "size": num(0.3, 0.9, 0.66, "Globe size", tier="regional", basic=True),
        "tilt": num(0.0, 40.0, 18.0, "Axis tilt", unit="°", step=0.5, tier="regional"),
        "spin_speed": paced(num(-0.3, 0.3, 0.035, "Spin", unit="turns/s", step=0.005, tier="regional")),
        "kick": num(0.0, 0.8, 0.22, "Beat spin kick", unit="turns/s", step=0.01, tier="regional"),
        "lighting": num(0.0, 1.0, 0.7, "Lighting", tier="regional"),
        "atmosphere": num(0.0, 1.0, 0.6, "Atmosphere glow", tier="regional"),
        "background": num(0.0, 1.0, 0.45, "Background (black .. dimmed source)", tier="regional"),
        "stars": num(0.0, 1.0, 0.45, "Stars", tier="regional"),
        "wrap": enum(["mirror", "single"], "mirror", "Wrap"),
        **palette_params("ice", period=1.0, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("globe_wrap")
        self.mips = FrameMips(ctx, levels=4, blur_sigma=2.5)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.spin = HomingAngle(period=2.0 * math.pi, spring=4.0)
        self.drift = 0.0
        self.ph1 = 0.0
        self.ph2 = 2.1
        self.A = ctx.width / ctx.height
        self.cam = camera_flat(FOV)
        self._pal_name = None
        self._pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        m = smoothstep01(a)
        gate = smoothstep01((a - 0.15) / 0.55)
        spin = float(p["spin_speed"])
        sgn = 1.0 if spin >= 0 else -1.0
        theta = self.spin.step(dt, 2.0 * math.pi * (spin + sgn * float(p["kick"]) * pulse * m), gate)
        # 20 is a whole number of periods of every palette's cosines (c in {0, .5, .7, .8, 1, 2}): seamless wrap
        self.drift = (self.drift + float(p["drift_speed"]) * dt) % 20.0
        self.ph1 = math.fmod(self.ph1 + 0.31 * dt, 2.0 * math.pi)
        self.ph2 = math.fmod(self.ph2 + 0.19 * dt, 2.0 * math.pi)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        A = self.A
        W, H = self.ctx.width, self.ctx.height
        D = flat_distance(FOV)
        tan_half = 1.0 / D
        mirror = p["wrap"] == "mirror"

        # final radius from the on-screen diameter (screen heights), swelled by the beat
        size = float(p["size"]) * (1.0 + 0.085 * pulse * m)
        tana = size * tan_half
        sina = tana / math.sqrt(1.0 + tana * tana)
        r_end = D * sina / (1.0 - sina)
        R = r_end / max(m, 1e-6)                                    # curvature kappa = m / r_end
        bob_y = 0.035 * r_end * m * math.sin(self.ph1)
        bob_x = 0.02 * r_end * m * math.sin(self.ph2)
        c = (bob_x, bob_y, -R)                                      # tangent to the screen plane (z = 0)
        qx, qy, qz = -c[0], -c[1], D + R
        cq = qx * qx + qy * qy + D * D + 2.0 * D * R               # |eye - c|^2 - R^2 without cancellation

        su_end = (1.0 / math.pi) if mirror else (0.5 / math.pi)
        sv_end = 1.0 / math.pi
        su = R / (2.0 * A) * (1.0 - m) + su_end * m                 # arc length (flat limit) -> equirect
        sv = R / 2.0 * (1.0 - m) + sv_end * m
        period_u = 2.0 if mirror else 1.0
        scroll = (-(theta / (2.0 * math.pi)) * period_u) % period_u

        tilt = math.radians(float(p["tilt"])) * m
        wob = 0.06 * m * math.sin(self.ph2 * 1.3)
        M = _rot((0.0, 0.0, 1.0), -(0.8 * tilt + wob)) @ _rot((1.0, 0.0, 0.0), 0.55 * tilt)

        # apparent radius now (pixels) for the shadow offset
        qn = math.sqrt(qx * qx + qy * qy + qz * qz)
        s_now = min(R / qn, 0.9999)
        r_px = min(s_now / math.sqrt(1.0 - s_now * s_now) / tan_half * H * 0.5, 4.0 * H)

        light = float(p["lighting"]) * m
        bgv = float(p["background"])
        key = np.asarray([-0.5, 0.45, 0.74])
        key /= np.linalg.norm(key)
        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._pal = palette_coeffs(p["palette"])

        hst = self.host
        hst[0:13] = self.cam
        hst[13:22] = M.reshape(-1)
        hst[22:25] = c
        hst[25] = R
        hst[26] = cq
        hst[27] = m
        hst[28] = su
        hst[29] = sv
        hst[30] = scroll
        hst[31] = 1.0 if mirror else 0.0
        hst[32] = light
        hst[33] = float(p["atmosphere"]) * m * (1.0 + 0.6 * pulse)
        hst[34] = 1.0 + (0.6 * bgv - 1.0) * m
        hst[35] = 1.0 + 0.12 * m
        hst[36] = 0.9 * m
        hst[37] = 0.6 * float(p["lighting"]) * m
        hst[38] = 0.16 * r_px
        hst[39] = 0.12 * r_px
        hst[40] = float(p["stars"]) * m * (1.0 - 0.5 * bgv)
        hst[41] = self.drift
        hst[42] = float(p["palette_period"])
        hst[43:46] = key
        hst[46] = 0.6 * light * (1.0 + 0.5 * pulse)
        hst[47] = -0.25
        hst[48:60] = self._pal
        self.P.set(hst)
        self.mips.build(src)
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P))
