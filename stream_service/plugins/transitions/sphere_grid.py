"""Sphere grid: the picture breaks into a grid of balls.  Each ball's front hemisphere carries its patch of the old
picture and its back hemisphere the new one.  The balls inflate out of flat tiles, roll 180 degrees in a staggered
(ordered, random or radial) order while bobbing toward the camera, then deflate back into the new picture.  A 1x1
grid is the "globe roll": the old picture wraps onto a planet that spins away as the new one comes round.

Source: Original (inspired by https://paulbourke.net/stereoscopy/transitions/ -- Paul Bourke's "Large ball
        rotations" and "Relocation balls")
Author: smfx (original; inspired by Paul Bourke's 'Large ball rotations' and 'Relocation balls')
License: MIT (original)
license_class: permissive

How it works (all analytic, full res, frame3d toolkit): the flat camera looks at the frame plane; each cell holds an
ellipsoid whose x/y radii morph from the cell's half size to a ball radius and whose depth grows with the
inflation, rotated about an in-plane roll axis (Rodrigues) and lifted toward the camera by the bob.  The front
decal is the unrotated surface position (so an inflating ball shows exactly what is behind it), the back decal is
that position mirrored about the roll axis (exactly where it lands after 180 degrees), so the deflating ball
dissolves seamlessly into the new picture.  Each pixel ray-tests its own cell plus the 8 neighbours (bobbing balls
overlap), composites the nearest two with analytic silhouette coverage, and shows the flat mix(a, b) at that
cell's switching progress in the gaps, with soft drop shadows from the bobbing balls and a palette-tinted stage
dim (weighted sin^2(pi p)).  Balls get Lambert relighting, Blinn specular, a Fresnel reflection of the blurred
input, a palette rim light and a glowing palette seam on the equator where the two pictures meet.  Inflation is 0
at both ends of every cell's local progress, so the cells tile exactly and the endpoints are pure.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, flat_distance
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params
from stream_service.plugins._lib.sdf3d import SDF3D

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
// K: 0 progress 1 stagger 2 pattern (0 ordered .. 1 random .. 2 radial) 3 inflate 4 bob (x ball radius)
//    5 roll-axis angle (rad, 0 = vertical axis) 6 axis spread 7 gloss 8 seam glow 9 gap dim 10 drift
//    11 palette period 12 D (flat camera distance) 13 tan_half 14 ball radius / cell half size
//    15 stage dim (gap_dim * sin^2(pi p)) 16 search-plane height
#define SG_PI 3.14159265f

struct SgBall { float3 C, S, iS; float ux, uy, ct, st, k, sw, hue; };

// Rodrigues rotation about the in-plane axis u = (ux, uy, 0) by +-theta.
__device__ __forceinline__ float3 sg_rot(const SgBall& B, float3 v, float sgn) {
    const float3 u = f3(B.ux, B.uy, 0.f);
    const float ud = B.ux * v.x + B.uy * v.y;
    return v * B.ct + cross(u, v) * (sgn * B.st) + u * (ud * (1.f - B.ct));
}

__device__ SgBall sg_ball(int i, int j, int nc, int nr, float A, const float* __restrict__ K, unsigned int seed) {
    SgBall B;
    const float cw = 2.f * A / (float)nc, ch = 2.f / (float)nr, hx = 0.5f * cw, hy = 0.5f * ch;
    const float cx = -A + ((float)i + 0.5f) * cw, cy = 1.f - ((float)j + 0.5f) * ch;
    // stagger delay: ordered (diagonal sweep) .. random .. radial (centre out)
    const float dOrd = (nc + nr > 2) ? (float)(i + j) / (float)(nc + nr - 2) : 0.f;
    const float dRnd = hash1((unsigned)i, (unsigned)j, seed);
    const float rmax = sqrtf((A - hx) * (A - hx) + (1.f - hy) * (1.f - hy));
    const float dRad = rmax > 1e-4f ? sqrtf(cx * cx + cy * cy) / rmax : 0.f;
    const float pat = K[2];
    const float d = pat <= 1.f ? mix(dOrd, dRnd, pat) : mix(dRnd, dRad, pat - 1.f);
    const float stg = K[1];
    const float s = saturate((K[0] - stg * d) / fmaxf(1.f - stg, 1e-3f));
    // local life: inflate [0, .28], roll [.16, .84], deflate [.72, 1]
    const float k = smoothstep(0.f, 0.28f, s) * (1.f - smoothstep(0.72f, 1.f, s));
    const float infl = K[3] * k;
    const float R = K[14] * fminf(hx, hy);
    B.S = f3(mix(hx, R, infl), mix(hy, R, infl), fmaxf(infl * R, 1e-4f));
    B.iS = f3(1.f / B.S.x, 1.f / B.S.y, 1.f / B.S.z);
    const float sb = __sinf(SG_PI * s);
    B.C = f3(cx, cy, K[4] * R * sb * sb);
    sincosf(SG_PI * smoothstep(0.16f, 0.84f, s), &B.st, &B.ct);
    const float phi = K[5] + K[6] * (hash1((unsigned)i, (unsigned)j, seed + 7u) - 0.5f) * SG_PI;
    sincosf(phi, &B.ux, &B.uy);
    B.k = k;
    B.sw = smoothstep(0.42f, 0.58f, s);
    B.hue = hash1((unsigned)i, (unsigned)j, seed + 3u);
    return B;
}

// Ray vs the ball: distance t (world) and analytic ~1 px silhouette coverage.
__device__ __forceinline__ bool sg_test(const SgBall& B, float3 ro, float3 rd, float pa, float& t, float& cov) {
    if (B.k <= 1e-4f) return false;
    const float3 o = sg_rot(B, ro - B.C, -1.f) * B.iS, d = sg_rot(B, rd, -1.f) * B.iS;
    const float a = fmaxf(dot(d, d), 1e-12f), b = dot(o, d);
    const float tc = -b / a;
    if (tc <= 0.f) return false;
    const float oo = dot(o, o);
    const float dp = sqrtf(fmaxf(oo - b * b / a, 0.f));
    if (dp > 1.8f) return false;
    const float3 e1 = sg_rot(B, f3(1.f, 0.f, 0.f), -1.f) * B.iS, e2 = sg_rot(B, f3(0.f, 1.f, 0.f), -1.f) * B.iS;
    const float fp = pa * tc * sqrtf(length(e1) * length(e2));
    cov = saturate((1.f - dp) / fmaxf(fp, 1e-6f) + 0.5f);
    if (cov <= 0.f) return false;
    const float disc = b * b - a * (oo - 1.f);
    t = disc > 0.f ? (-b - sqrtf(disc)) / a : tc;
    return t > 0.f;
}

__device__ __forceinline__ float3 sg_screen(float3 c, float3 add) {
    return f3(1.f) - (f3(1.f) - saturate(c)) * (f3(1.f) - saturate(add));
}

__device__ float3 sg_shade(const SgBall& B, float3 ro, float3 rd, float t, const Tex& TA, const Tex& TB, float A,
                           float pa, float3 fwd, const float* __restrict__ K, const float* __restrict__ P) {
    const float3 o = sg_rot(B, ro - B.C, -1.f) * B.iS, d = sg_rot(B, rd, -1.f) * B.iS;
    const float3 q = normalize(o + d * t);                        // unit-sphere point (clamped in the AA fringe)
    float3 n = normalize(sg_rot(B, q * B.iS, 1.f));               // ellipsoid normal, world
    if (dot(n, rd) > 0.f) n = -n;
    const float3 sq = q * B.S;                                     // unrotated surface offset from the centre
    const float ndv = fmaxf(-dot(rd, n), 0.f);
    const float lod = tex_lod(t, pa, dot(rd, fwd), ndv, frame_texel(TA), -0.25f);
    const float ia = 0.5f / A;
    // seam between the hemispheres, ~1 px on screen
    const float hw = fminf(fmaxf(0.75f * pa * t * B.iS.z / fmaxf(ndv, 0.2f), 1e-3f), 0.3f);
    const float wf = smoothstep(-hw, hw, q.z);
    float3 c = f3(0.f);
    if (wf > 0.f) {                                               // front: old picture, where the surface stands
        c = tex_sample(TA, (B.C.x + sq.x + A) * ia, 0.5f * (1.f - (B.C.y + sq.y)), lod) * wf;
    }
    if (wf < 1.f) {                                               // back: new picture, where it lands after 180 deg
        const float du = sq.x * B.ux + sq.y * B.uy;
        const float bx = 2.f * du * B.ux - sq.x, by = 2.f * du * B.uy - sq.y;
        c = c + tex_sample(TB, (B.C.x + bx + A) * ia, 0.5f * (1.f - (B.C.y + by)), lod) * (1.f - wf);
    }
    const float lk = B.k, g = K[7];
    const float3 L = normalize(f3(-0.5f, 0.65f, 0.6f));
    const float3 v = -rd;
    c = light_frame(c, n, L, fwd, 0.3f, 0.85f * lk);
    const float limb = 1.f - ndv;
    c = c * (1.f - 0.35f * lk * limb * limb);
    // glossy Fresnel reflection of "the room" (the blurred input on the visible side)
    const float F = fresnel_schlick(ndv, 0.04f);
    const float3 env = wf > 0.5f ? env_frame(TA, reflect(rd, n)) : env_frame(TB, reflect(rd, n));
    c = mix(c, env * 1.15f, saturate(F * g * lk * 0.85f));
    // specular, palette rim and the glowing equator seam (screen-blended light)
    const float sp = spec_blinn(n, v, L, 10.f + 150.f * g * g) * (0.15f + 0.85f * g) * lk;
    const float3 rimc = pal(B.hue * K[11] + K[10] + 0.33f, P);
    const float rim = limb * limb * limb * lk * (0.25f + 0.6f * g);
    const float sw = 0.06f + hw;
    const float seam = K[8] * lk * __expf(-(q.z * q.z) / (sw * sw));
    const float3 seamc = pal(B.hue * K[11] + K[10], P);
    const float3 add = f3(sp) * (f3(0.85f) + rimc * 0.15f) + rimc * rim + seamc * (1.4f * seam);
    return sg_screen(c, add);
}

SDF_KERNEL
void sphere_grid(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b),
                 const float* __restrict__ P, const float* __restrict__ K, const int nc, const int nr,
                 const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    const float A = (float)w / (float)h;
    const Cam cam = cam_flat(K[13]);
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 rd = cam_dir(cam, px, py, w, h);
    const float pa = pixel_angle(cam, h);
    const float X0 = (2.f * px / (float)w - 1.f) * A, Y0 = 1.f - 2.f * py / (float)h;   // frame-plane point
    const int io = min(max((int)floorf((X0 + A) / (2.f * A) * nc), 0), nc - 1);
    const int jo = min(max((int)floorf(0.5f * (1.f - Y0) * nr), 0), nr - 1);
    const float ks = (K[12] - K[16]) / K[12];                     // search at the mean bob height
    const int ic = min(max((int)floorf((X0 * ks + A) / (2.f * A) * nc), 0), nc - 1);
    const int jc = min(max((int)floorf(0.5f * (1.f - Y0 * ks) * nr), 0), nr - 1);

    float t1 = 1e30f, c1 = 0.f, h1 = 0.f, t2 = 1e30f, c2 = 0.f, h2 = 0.f;
    int i1 = -1, j1 = -1, i2 = -1, j2 = -1;
    float keep = 1.f;                                              // prod(1 - shadow_i) on the gap
    float sw_own = -1.f;
    for (int dj = -1; dj <= 1; ++dj) {
        const int j = jc + dj;
        if (j < 0 || j >= nr) continue;
        for (int di = -1; di <= 1; ++di) {
            const int i = ic + di;
            if (i < 0 || i >= nc) continue;
            const SgBall B = sg_ball(i, j, nc, nr, A, K, seed);
            if (i == io && j == jo) sw_own = B.sw;
            if (B.k <= 1e-4f) continue;
            // soft drop shadow / contact occlusion on the frame plane (light from upper left)
            const float Rb = 0.5f * (B.S.x + B.S.y);
            const float sx = B.C.x + 0.83f * B.C.z, sy = B.C.y - 1.08f * B.C.z;
            const float dd = sqrtf((X0 - sx) * (X0 - sx) + (Y0 - sy) * (Y0 - sy)) / Rb;
            const float shd = B.k * smoothstep(1.9f + 0.4f * B.C.z / Rb, 0.7f, dd);
            keep *= 1.f - 0.92f * shd;
            float t, cv;
            if (sg_test(B, cam.eye, rd, pa, t, cv)) {
                if (t < t1) {
                    t2 = t1; c2 = c1; h2 = h1; i2 = i1; j2 = j1;
                    t1 = t; c1 = cv; h1 = 0.92f * shd; i1 = i; j1 = j;
                } else if (t < t2) {
                    t2 = t; c2 = cv; h2 = 0.92f * shd; i2 = i; j2 = j;
                }
            }
        }
    }
    if (sw_own < 0.f) sw_own = sg_ball(io, jo, nc, nr, A, K, seed).sw;

    // the gap: flat mix(a, b) at the cell's switching progress, shadowed, under a palette-tinted stage dim
    const float3 fa = tex_fetch(TA, x, y), fb = tex_fetch(TB, x, y);
    const float3 flat = fa + (fb - fa) * sw_own;
    float3 tint = pal(K[10] + 0.25f * (px / w) + 0.35f * (py / h), P);
    tint = tint * (1.f / fmaxf(vmax(tint), 0.2f));
    const float gd = K[9], sd = K[15];
    #define SG_GAP(c, oc) (mix((c) * (1.f - 0.85f * gd * (oc)), (c) * (1.f - 0.85f * gd * (oc)) * tint, 0.7f * sd) \
                           * (1.f - 0.55f * sd))
    float3 col = SG_GAP(flat, 1.f - keep);
    const float3 fwd = cam.fwd;
    if (i2 >= 0 && c1 < 1.f) {
        const SgBall B = sg_ball(i2, j2, nc, nr, A, K, seed);
        const float3 s = sg_shade(B, cam.eye, rd, t2, TA, TB, A, pa, fwd, K, P);
        const float oc = 1.f - keep / fmaxf(1.f - h2, 1e-3f);
        col = mix(col, mix(SG_GAP(s, oc), s, B.k), c2);
    }
    if (i1 >= 0) {
        const SgBall B = sg_ball(i1, j1, nc, nr, A, K, seed);
        const float3 s = sg_shade(B, cam.eye, rd, t1, TA, TB, A, pa, fwd, K, P);
        const float oc = 1.f - keep / fmaxf(1.f - h1, 1e-3f);
        col = mix(col, mix(SG_GAP(s, oc), s, B.k), c1);
    }
    #undef SG_GAP
    store_rgb(out, y * w + x, col);
}
"""

_FOV = 40.0
_BALL = 0.94                     # ball radius / cell half size (balls nearly touch; corners stay as gaps)


class SphereGrid(Transition):
    name = "sphere_grid"
    label = "Sphere grid"
    description = ("The picture breaks into a grid of glossy balls carrying the old picture on their fronts and the "
                   "new one on their backs; they inflate, roll 180 degrees in a staggered wave while bobbing toward "
                   "you, then deflate into the new picture.  One cell = a globe roll.")
    default_duration_s = 3.0
    params = {
        "cells": num(1, 24, 6, "Cells (rows; 1 = globe)", tier="regional", integer=True, basic=True),
        "inflate": num(0.1, 1.0, 1.0, "Inflate (coin .. ball)", tier="regional"),
        "stagger_pattern": num(0.0, 2.0, 0.6, "Order: ordered .. random .. radial", tier="regional", basic=True),
        "stagger": num(0.0, 0.85, 0.55, "Stagger spread", tier="regional"),
        "z_bob": num(0.0, 1.0, 0.5, "Bob toward camera", tier="regional"),
        "spin_axis_angle": num(0.0, 180.0, 20.0, "Roll axis (0 = spin sideways)", step=1.0, unit="°",
                               tier="regional"),
        "axis_spread": num(0.0, 1.0, 0.3, "Roll-axis variety", tier="regional"),
        "gloss": num(0.0, 1.0, 0.6, "Gloss", tier="regional"),
        "seam_glow": num(0.0, 1.0, 0.5, "Equator glow", tier="regional"),
        "gap_dim": num(0.0, 1.0, 0.6, "Gap / stage dim", tier="regional"),
        **palette_params("neon", period=0.7, drift=0.06),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("sphere_grid")
        self._mips_a = FrameMips(ctx, levels=4)
        self._mips_b = FrameMips(ctx, levels=4)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._K = cp.zeros(20, dtype=cp.float32)
        self._Kh = np.zeros(20, np.float32)
        self._pal_name = None

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "neon")
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        p = float(progress)
        if p <= 1e-4:
            cp.copyto(out, a)
            return
        if p >= 0.9999:
            cp.copyto(out, b)
            return
        self._sync_palette()
        prm = self.params
        w, h = self.ctx.width, self.ctx.height
        rows = min(max(int(round(float(prm["cells"]))), 1), 24)
        cols = 1 if rows == 1 else max(1, int(round(rows * w / h)))
        D = flat_distance(_FOV)
        tan_half = math.tan(math.radians(_FOV) * 0.5)
        bob = 1.4 * float(prm["z_bob"])
        r0 = _BALL * min((w / h) / cols, 1.0 / rows)
        drift = float(prm["drift_speed"]) * p * self.duration_s
        drift -= math.floor(drift)
        stage = float(prm["gap_dim"]) * math.sin(math.pi * p) ** 2

        K = self._Kh
        K[:17] = (p, float(prm["stagger"]), float(prm["stagger_pattern"]), float(prm["inflate"]), bob,
                  math.radians(float(prm["spin_axis_angle"])), float(prm["axis_spread"]), float(prm["gloss"]),
                  float(prm["seam_glow"]), float(prm["gap_dim"]), drift, float(prm["palette_period"]), D, tan_half,
                  _BALL, stage, 0.5 * bob * r0)
        self._K.set(K)
        self._mips_a.build(a)
        self._mips_b.build(b)
        launch_2d(self._k, w, h,
                  (out, np.int32(w), np.int32(h), *self._mips_a.args(a), *self._mips_b.args(b),
                   self._P, self._K, np.int32(cols), np.int32(rows), np.uint32(int(prm["seed"]) & 0xFFFFFFFF)))
