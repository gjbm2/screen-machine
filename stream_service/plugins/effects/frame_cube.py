"""Frame cube: the picture pulls back to become the front face of a glossy cube that spins on a reflective stage.

Source: Original (reference projection effect for smfx/_lib/frame3d.py)
Author: smfx
License: MIT
license_class: permissive

amount 0 is the untouched frame (copied).  As amount rises the camera dollies back from the flat pose so the frame
becomes the front face of a box whose side faces are frame-shaped (16:9, so every face around the spin axis shows
the whole picture); the camera rises a little, the cube spins about its vertical axis (regional rate; the beat kicks
the spin rate, so it lurches round on every beat), wobbles on a slowly precessing axis (tumble), and sits over a
dark glossy stage that reflects it, in front of a darkened, blurred copy of the input.  Side faces are mirrored
(seamless edges) or repeated, optionally hue-stepped per face.  Faces are relit (key light, Fresnel sheen of the
blurred "room" and a soft box) with glassy bevel edges that flash on the beat.  As amount falls the spin springs
home (frame3d.HomingAngle) and everything flattens back to the exact input.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import (FRAME3D, FRAME3D_OPTIONS, FrameMips, HomingAngle, box_support_y, camera_orbit,
                               flat_distance, rot_euler, smoothstep01, tex_buffer, tex_object, xform_pack)
from stream_service.plugins._lib.palettes import PALETTE_CUDA
from stream_service.plugins._lib.params import enum, num, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
NP = 48
# P layout (floats)
#  0..12 camera | 13..25 cube xform | 26..28 half extents | 29 m | 30 light k | 31 hue step (turns) | 32 mirror
#  33 backdrop dim | 34 floor y | 35 reflection | 36 edge | 37 lod bias | 38..40 key dir | 41 backdrop zoom
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_XF 13
#define P_HB 26
#define P_M 29
#define P_LIGHT 30
#define P_HUE 31
#define P_MIRROR 32
#define P_BG 33
#define P_FLOOR 34
#define P_REFL 35
#define P_EDGE 36
#define P_BIAS 37
#define P_KEY 38
#define P_BGZOOM 41

// The frame as it appears on one face: the front face is the exact frame; the others are centre-cropped to their
// aspect, mirrored on alternate side faces (seamless edges) and hue-stepped by their position round the cube.
__device__ float3 face_colour(const Tex& T, const FHit& hit, float3 hb, float lod, const float* __restrict__ P, float A,
                              bool rough) {
    const int ar = box_around(hit.face);
    float2 uv = hit.uv;
    if (ar != 0) {
        const float2 fs = box_face_size(hb, hit.face);
        uv = uv_cover(uv, fs.x / fs.y, A);
        if (P[P_MIRROR] > 0.5f) {
            if (ar == 1 || ar == 3) uv.x = 1.f - uv.x;
            if (ar >= 4) uv.y = 1.f - uv.y;
        }
    }
    float3 c = rough ? tex_sample_near(T, uv.x, uv.y, lod) : tex_sample(T, uv.x, uv.y, lod);
    const float hs = P[P_HUE] * (float)ar;
    if (hs != 0.f) c = saturate(hue_rotate(c, hs * F3D_TAU));
    return c;
}

// Pass 1 (quarter res): the backdrop (darkened blurred input) and the stage floor (contact shadow + glossy
// reflection of the cube).  Everything here is low-frequency, so it costs ~1/16 of a full-res pass.
SDF_KERNEL
void frame_cube_bg(uchar4* __restrict__ bgo, const int bw, const int bh, const int bp, const int w, const int h, FRAME_ARGS,
                   const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= bw || y >= bh) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const float A = (float)W / (float)H;
    const float u = (x + 0.5f) / bw, v = (y + 0.5f) / bh;
    const float3 rd = cam_dir(cam, u * w, v * h, w, h);
    float3 bg = bg_blur(T, u, v, P[P_BGZOOM], P[P_BG], 0.9f);
    if (rd.y < -1e-4f) {
        const float tf = (P[P_FLOOR] - cam.eye.y) / rd.y;
        if (tf > 0.f) {
            const Xf X = xf_load(P + P_XF);
            const float3 hb = load3(P + P_HB);
            const float3 pf = cam.eye + rd * tf;
            const float occ = occ_box(xf_inv_point(X, pf), hb, 1.4f);
            float3 fl = bg * (0.55f * (0.3f + 0.7f * occ));
            const float3 rr = f3(rd.x, -rd.y, rd.z);
            float3 oro, ord; xf_ray(X, pf, rr, oro, ord);
            const FHit hr = ihit_box(oro, ord, hb);
            if (fhit_ok(hr)) {
                const float tr = hr.t * X.s;
                const float hy = pf.y + rr.y * tr - P[P_FLOOR];
                const float3 n = xf_dir(X, hr.n);
                const float lod = tex_lod(tf + tr, pixel_angle(cam, h), fmaxf(dot(rd, cam.fwd), 0.05f),
                                          fabsf(dot(rr, n)), frame_texel(T) * hb.y * X.s, 1.5f);
                float3 c = face_colour(T, hr, hb, lod, P, A, true);
                c = light_frame(c, n, load3(P + P_KEY), cam.fwd, 0.2f, P[P_LIGHT]);
                const float a = P[P_REFL] * __expf(-1.6f * hy) * (0.35f + 0.65f * fresnel_schlick(-rd.y, 0.25f));
                fl = mix(fl, c, saturate(a));
            }
            bg = mix(bg, fl, smoothstep(0.f, 0.06f, -rd.y));
        }
    }
    store_rgb_dither(bgo, x, y, bp, bg, 1.f);
}

// Pass 2 (full res): the cube with silhouette AA over the upsampled pass-1 backdrop.  Latency-bound, so all of a
// pixel's fetches (backdrop, 2 mip levels, environment) sit in branch-free code and go out together.
SDF_KERNEL_TIGHT
void frame_cube(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P,
                const unsigned long long bgt)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 bgc = tex_rgb_at(bgt, px / w, py / h);
    const Cam cam = cam_load(P);
    const Xf X = xf_load(P + P_XF);
    const float3 hb = load3(P + P_HB);
    const float pa = pixel_angle(cam, h);
    const float3 rd = cam_dir(cam, px, py, w, h);

    // silhouette AA: classify the centre ray against the grown / shrunk box; only edge pixels pay for 4 sub-rays
    float3 oro, ord; xf_ray(X, cam.eye, rd, oro, ord);
    float cover = box_cover_class(oro, ord, hb, 1.5f * pa * length(oro));
    float3 r = rd;
    if (cover == 0.5f) {
        auto hits = [&](float3 d) {
            float3 a, b; xf_ray(X, cam.eye, d, a, b);
            const float2 tt = iBox(a, b, hb);
            return tt.x <= tt.y && tt.y > 0.f;
        };
        float3 sub = rd;
        cover = aa_cover4(cam, px, py, w, h, hits, sub);
        if (!hits(rd)) { r = sub; xf_ray(X, cam.eye, r, oro, ord); }
    }
    float3 col = bgc;
    if (cover > 0.f) {
        const FHit hit = ihit_box(oro, ord, hb);
        const float k = P[P_LIGHT];
        const float3 n = xf_dir(X, hit.n);
        const float t = hit.t * X.s, cosa = dot(r, cam.fwd), ndv = fabsf(dot(r, n));
        const float lod = tex_lod(t, pa, cosa, ndv, frame_texel(T) * hb.y * X.s, P[P_BIAS]);
        const float3 rr = reflect(r, n);
        const float3 envc = env_frame(T, rr);
        float3 c = face_colour(T, hit, hb, lod, P, (float)W / (float)H, false);
        c = light_frame(c, n, load3(P + P_KEY), cam.fwd, 0.2f, k);
        // glossy coat: Fresnel-weighted reflection of the blurred input ("the room") and a soft box sheen
        c += (envc * 0.6f + f3(env_softbox(rr) * 0.55f)) * (fresnel_schlick(ndv, 0.04f) * k);
        // glassy bevel along the face borders (about 0.12% of the frame height wide, AA by construction)
        const float e = uv_edge_px(hit.uv, box_face_size(hb, hit.face) * X.s, t * pa * fmaxf(cosa, 0.05f));
        const float bev = (1.f - smoothstep(0.f, fmaxf(1.f, 0.0012f * h), e)) * P[P_EDGE];
        c = mix(c, f3(0.92f) + c * 0.2f, saturate(bev));
        col = cover < 1.f ? mix(bgc, c, cover) : c;
    }
    store_rgb(out, y * w + x, col);
}
"""


class FrameCube(Effect):
    name = "frame_cube"
    stage = "projection"
    label = "Frame cube"
    description = "The picture pulls back into the front face of a glossy spinning cube on a reflective stage."
    params = {
        **standard_effect_params(),
        "size": num(0.3, 0.95, 0.6, "Cube size", tier="regional", basic=True),
        "spin": paced(num(-0.5, 0.5, 0.05, "Spin", unit="turns/s", step=0.005, tier="regional", basic=True)),
        "kick": num(0.0, 1.5, 0.45, "Beat spin kick", unit="turns/s", step=0.01, tier="regional"),
        "elevation": num(0.0, 35.0, 14.0, "Camera elevation", unit="°", step=0.5, tier="regional"),
        "tumble": num(0.0, 1.0, 0.3, "Tumble", tier="regional"),
        "face_hue": num(0.0, 0.5, 0.0, "Hue step per face", unit="turn", step=0.005, tier="regional"),
        "light": num(0.0, 1.0, 0.75, "Lighting & gloss", tier="regional"),
        "backdrop": num(0.0, 1.0, 0.4, "Backdrop", tier="regional"),
        "reflection": num(0.0, 1.0, 0.55, "Floor reflection", tier="regional"),
        "faces": enum(["mirror", "repeat"], "mirror", "Side faces"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._k = mod.get_function("frame_cube")
        self._kbg = mod.get_function("frame_cube_bg")
        self.mips = FrameMips(ctx, levels=4, blur_sigma=2.5)
        self.bw, self.bh = max(16, ctx.width // 4), max(16, ctx.height // 4)
        self.bg = tex_buffer(cp, self.bw, self.bh)
        self.bgt = tex_object(self.bg)
        self.bp = np.int32(self.bg.strides[0] // 4)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.spin = HomingAngle(period=2.0 * math.pi, spring=4.0)
        self.ph1 = 0.0
        self.ph2 = 1.3
        self.ph3 = 0.0
        self.sup = 1.0
        self.A = ctx.width / ctx.height

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        gate = smoothstep01((a - 0.1) / 0.5)
        sgn = 1.0 if p["spin"] >= 0 else -1.0
        theta = self.spin.step(dt, 2.0 * math.pi * (p["spin"] + sgn * p["kick"] * pulse), gate)
        self.ph1 = math.fmod(self.ph1 + 0.53 * dt, 2.0 * math.pi)
        self.ph2 = math.fmod(self.ph2 + 0.37 * dt, 2.0 * math.pi)
        self.ph3 = math.fmod(self.ph3 + 0.21 * dt, 2.0 * math.pi)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = smoothstep01(a)
        A = self.A
        hb = (A, 1.0, A)                                   # every face round the y axis is frame-shaped
        tum = p["tumble"] * m
        rot = rot_euler(theta, tum * 0.32 * math.sin(self.ph1), tum * 0.2 * math.sin(self.ph2))
        # floor under the lowest corner seen recently (fast attack, slow release: it never cuts the cube)
        sup = box_support_y(rot, hb)
        self.sup = sup if sup > self.sup else self.sup + (sup - self.sup) * (1.0 - math.exp(-0.5 * dt))
        D = flat_distance(FOV)
        size = (1.0 + (p["size"] - 1.0) * m) * (1.0 + 0.045 * pulse * m)
        dist = A + D / size                                 # front face at D / size from the eye
        el = math.radians(p["elevation"]) * m
        yaw = 0.07 * m * math.sin(self.ph3)
        cam = camera_orbit((0.0, -0.18 * m, -A), dist, yaw, el, 0.0, FOV)
        key = np.asarray([-0.5, 0.62, 0.62])
        key /= np.linalg.norm(key)
        light = p["light"] * m
        h = self.host
        h[0:13] = cam
        h[13:26] = xform_pack(rot, (0.0, 0.0, -A), 1.0)
        h[26:29] = hb
        h[29] = m
        h[30] = light
        h[31] = p["face_hue"] * min(1.0, 2.0 * m)
        h[32] = 1.0 if p["faces"] == "mirror" else 0.0
        h[33] = p["backdrop"]
        h[34] = -(self.sup + 0.12)
        h[35] = p["reflection"] * m
        h[36] = (0.25 + 0.5 * pulse) * p["light"] * m
        h[37] = -0.3
        h[38:41] = key
        h[41] = 1.12
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        margs = self.mips.args(src)
        launch_2d(self._kbg, self.bw, self.bh, (self.bg, np.int32(self.bw), np.int32(self.bh), self.bp, np.int32(W), np.int32(H),
                                                *margs, self.P))
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *margs, self.P, self.bgt))
