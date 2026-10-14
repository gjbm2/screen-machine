"""Prism columns: the picture splits into vertical triangular (or square) prisms that turn in a staggered wave.
Face 1 carries the old picture, face 2 the new one, and the third face is a palette-lit glass / neon / colour face
that flashes past mid-turn.  Lambert relighting plus a rim light, neon creases and a specular sweep; with a few
columns and z separation it becomes a "prism split" while the camera pulls back and swings.

Source: Original (inspired by https://paulbourke.net/stereoscopy/transitions/ , Paul Bourke's "Cylinder rotations")
Author: smfx (original; inspired by Paul Bourke's 'Cylinder rotations')
License: MIT (original)
license_class: permissive

Technique: full-res analytic ray casts against convex prisms (n side planes + 2 caps per column, own column plus
the neighbours a ray can reach inside the z slab), frame3d FrameMips for both inputs (trilinear faces, blurred
backdrop), 4 rotated-grid sub-rays only on moving silhouettes / creases.  Endpoints: every column is at rest and
the camera is the frame3d flat camera, so progress 0 / 1 are exact (plus early-out copies).
Timing: 1-2 bars; column k starts at stagger * order(k).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, camera_orbit, flat_distance
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params
from stream_service.plugins._lib.sdf3d import SDF3D

_MAXC = 48
_CF = 8          # floats per column
_KN = 32         # header floats
_FOV = 40.0

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define PC_CF 8
#define PC_MAXSCAN 12

// K: 0 N  1 n sides  2 s (column width)  3 r_in  4 R_circ  5 A  6 zlo  7 zhi  8 p  9 g = sin(pi p)
//    10 lighting  11 specular  12 edge_glow  13 third style  14 period  15 drift  16..23 rest face normal (sin, cos)
//    24 via third face  25 camera bump  26 spec sweep azimuth  27 resolution scale (h / 2160)
// C (per column): 0 cos th  1 sin th  2 axis x  3 axis z  4 scale  5 motion weight mk  6 dir  7 hue offset
struct PHit { float t; int k; int face; float3 pl; };

__device__ __forceinline__ float3 pc_screen(float3 a, float3 b) {
    a = saturate(a); b = saturate(b);
    return f3(1.f) - (f3(1.f) - a) * (f3(1.f) - b);
}

// Nearest prism hit along the ray (world t), or k = -1.
__device__ PHit pc_trace(const float3 ro, const float3 rd, const float* __restrict__ K, const float* __restrict__ C) {
    PHit best; best.t = 1e30f; best.k = -1; best.face = -1; best.pl = f3(0.f);
    const int N = (int)K[0], n = (int)K[1];
    const float s = K[2], rin = K[3], Rc = K[4], A = K[5];
    const float dz = rd.z < -1e-6f ? rd.z : -1e-6f;
    const float t0 = (K[7] - ro.z) / dz, t1 = (K[6] - ro.z) / dz;          // enter / leave the z slab
    if (t1 <= 0.f) return best;
    const float xa = ro.x + rd.x * fmaxf(t0, 0.f), xb = ro.x + rd.x * t1;
    const float xmn = fminf(xa, xb) - Rc, xmx = fmaxf(xa, xb) + Rc;
    const int k0 = max(0, (int)floorf((xmn + A) / s));
    const int k1 = min(min(N - 1, (int)floorf((xmx + A) / s)), k0 + PC_MAXSCAN - 1);
    for (int k = k0; k <= k1; ++k) {
        const float* c = C + k * PC_CF;
        const float ct = c[0], st = c[1], sc = c[4], isc = 1.f / sc;
        const float ox = ro.x - c[2], oz = ro.z - c[3];
        const float3 lo = f3((ct * ox - st * oz) * isc, ro.y * isc, (st * ox + ct * oz) * isc);
        const float3 ld = f3(ct * rd.x - st * rd.z, rd.y, st * rd.x + ct * rd.z);
        float tn = -1e30f, tf = 1e30f;
        int fn = -1;
        bool ok = true;
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            if (i < n) {
                const float sn = K[16 + 2 * i], cn = K[17 + 2 * i];
                const float den = sn * ld.x + cn * ld.z, num = rin - (sn * lo.x + cn * lo.z);
                if (fabsf(den) < 1e-9f) { if (num < 0.f) ok = false; }
                else {
                    const float tt = num / den;
                    if (den < 0.f) { if (tt > tn) { tn = tt; fn = i; } }
                    else tf = fminf(tf, tt);
                }
            }
        }
        if (fabsf(ld.y) > 1e-9f) {
            const float ta = (1.f - lo.y) / ld.y, tb = (-1.f - lo.y) / ld.y;
            const float te = fminf(ta, tb), tx = fmaxf(ta, tb);
            if (te > tn) { tn = te; fn = ld.y > 0.f ? n + 1 : n; }
            tf = fminf(tf, tx);
        } else if (fabsf(lo.y) > 1.f) ok = false;
        if (!ok || tn > tf || tn <= 0.f) continue;
        const float tw = tn * sc;
        if (tw < best.t) { best.t = tw; best.k = k; best.face = fn; best.pl = lo + ld * tn; }
    }
    return best;
}

// 0 = old picture, 1 = new picture, 2 = third face (glass / neon / palette), 3 = cap
__device__ __forceinline__ int pc_content(int face, int n, float dir, bool via) {
    if (face >= n) return 3;
    const int step = dir > 0.f ? face : (n - face) % n;
    if (step == 0) return 0;
    if (via) return step == 2 ? 1 : 2;
    return step == 1 ? 1 : 2;
}

__device__ float3 pc_background(const Tex& TA, const Tex& TB, float su, float sv, const float* __restrict__ K,
                                const float* __restrict__ P) {
    const float3 ba = bg_blur(TA, su, sv, 1.06f, 0.30f, 0.9f), bb = bg_blur(TB, su, sv, 1.06f, 0.30f, 0.9f);
    float3 c = mix(ba, bb, K[8]);
    const float3 glow = pal(K[15] + K[14] * (0.6f * su + 0.15f), P);
    return c + glow * (0.18f * K[9] * (1.f - 0.7f * fabsf(sv - 0.5f) * 2.f));
}

__device__ float3 pc_shade(const PHit& h, const float3 rd, const Cam& cam, const float pa, const float su,
                           const float sv, const Tex& TA, const Tex& TB, const float* __restrict__ K,
                           const float* __restrict__ C, const float* __restrict__ P, float& epx) {
    epx = 1e9f;
    if (h.k < 0) return pc_background(TA, TB, su, sv, K, P);
    const float* c = C + h.k * PC_CF;
    const int n = (int)K[1], N = (int)K[0];
    const float s = K[2], ct = c[0], st = c[1], sc = c[4], mk = c[5];
    const int content = pc_content(h.face, n, c[6], K[24] > 0.5f);
    float3 nw, tw;
    float uf;
    if (h.face < n) {
        const float sn = K[16 + 2 * h.face], cn = K[17 + 2 * h.face];
        const float sw = sn * ct + cn * st, cw = cn * ct - sn * st;          // sin / cos(phi + theta)
        nw = f3(sw, 0.f, cw); tw = f3(cw, 0.f, -sw);
        uf = 0.5f + (h.pl.x * cn - h.pl.z * sn) / s;
    } else {
        nw = f3(0.f, h.face == n ? 1.f : -1.f, 0.f); tw = f3(ct, 0.f, -st);
        uf = 0.5f + h.pl.x / s;
    }
    const float v = 0.5f - 0.5f * h.pl.y;
    const float ndv = fabsf(dot(rd, nw));
    const float fp = fmaxf(h.t * pa, 1e-9f);
    const float tr = dot(tw, rd);
    const float px_h = fminf(uf, 1.f - uf) * s * sc * sqrtf(fmaxf(1.f - tr * tr, 0.f)) / fp;
    const float px_v = (1.f - fabsf(h.pl.y)) * sc * sqrtf(fmaxf(1.f - rd.y * rd.y, 0.f)) / fp;
    if (h.face >= n) epx = 0.f;
    else if (mk > 1e-3f) epx = fminf(px_h, px_v);
    else {
        if ((h.k == 0 && uf < 0.5f) || (h.k == N - 1 && uf > 0.5f)) epx = px_h;
        if (K[25] > 1e-4f) epx = fminf(epx, px_v);
    }

    const float3 L = f3(-0.4685f, 0.3956f, 0.7898f);                           // key light, upper left front
    const float3 V = -rd;
    const float colu = ((float)h.k + 0.5f) / (float)N;
    const float ci = K[15] + K[14] * (0.75f * colu + 0.2f * v) + c[7];
    const float3 pc = pal(ci, P);
    const float hitz = cam.eye.z + rd.z * h.t;
    const float rs = K[27];
    float3 col;
    if (content <= 1) {
        const Tex& T = content == 0 ? TA : TB;
        const float u = ((float)h.k + uf) / (float)N;
        const float lod = tex_lod(h.t, pa, dot(rd, cam.fwd), ndv, frame_texel(T) * sc, -0.25f);
        col = tex_sample(T, u, v, lod);
        col = light_frame(col, nw, L, cam.fwd, 0.28f, K[10]);
        col = col * clamp(1.f + 0.55f * hitz, 0.35f, 1.12f);
        if (mk > 0.f) {
            const float rim = 1.f - ndv, rim3 = rim * rim * rim;
            float ss, cs; __sincosf(K[26], &ss, &cs);
            const float3 Ls = normalize(f3(ss, 0.3f, cs));
            const float sp = spec_blinn(nw, V, Ls, 56.f) * K[11] * 1.3f;
            const float eg = K[12] * exp2f(-px_h / (rs * (2.f + 4.f * K[12])));
            const float3 add = (pc * (rim3 * 0.9f * K[12] + eg * 1.4f) + mix(f3(1.f), pc, 0.25f) * sp) * mk;
            col = pc_screen(col, add);
        }
    } else if (content == 2) {
        const float sty = K[13];
        const float wg = saturate(1.f - 2.f * sty), wn = saturate(1.f - fabsf(2.f * sty - 1.f)),
                    wp = saturate(2.f * sty - 1.f);
        const float fr = fresnel_schlick(ndv, 0.05f);
        const float sb = env_softbox(reflect(rd, nw));
        const float lam = saturate(dot(nw, L));
        float3 acc = f3(0.f);
        if (wg > 0.f) {                                                         // glass: refracted blurred room
            const float qx = su + 0.09f * nw.x, qy = sv + 0.03f * (v - 0.5f);
            const float3 behind = mix(tex_blur(TA, qx, qy), tex_blur(TB, qx, qy), K[8]);
            float3 gl = behind * 0.62f + pc * 0.24f;
            const float streak = __expf(-fabsf(uf + 0.55f * v - (-0.4f + 1.9f * K[8])) * 9.f);
            gl = gl + f3(1.f) * (fr * 0.55f + sb * 0.6f * (0.3f + fr) + 0.35f * streak);
            acc = acc + gl * wg;
        }
        if (wn > 0.f) {                                                         // neon: dark glass, glowing tubes
            const float bpx = fminf(px_h, px_v);
            const float glow = exp2f(-bpx / (rs * 6.f)) + 0.35f * exp2f(-bpx / (rs * 40.f));
            const float fb = v * 5.f + 2.f * K[15] + 1.7f * colu;
            const float dl = fabsf(fb - floorf(fb) - 0.5f);
            float bar = 1.f - 2.f * dl; bar *= bar; bar *= bar; bar *= bar; bar *= bar;   // ^16
            float3 ne = pc * (0.06f + 1.6f * glow + 0.75f * bar) + f3(0.5f * glow * glow + 0.3f * sb);
            acc = acc + ne * wn;
        }
        if (wp > 0.f) {                                                         // palette: lit solid colour
            const float3 pc2 = pal(ci + 0.35f * v + 0.15f * uf, P);
            acc = acc + (pc2 * (0.5f + 0.5f * lam) + f3(sb * 0.45f + fr * 0.3f)) * wp;
        }
        col = acc * (1.f + 0.35f * mk);
        const float eg = K[12] * exp2f(-px_h / (rs * (2.f + 4.f * K[12])));
        col = pc_screen(col, pc * (eg * 1.2f * mk));
    } else {
        col = pc * 0.14f + f3(0.03f);
    }
    return col;
}

SDF_KERNEL
void prism_columns(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b),
                   const float* __restrict__ CAM, const float* __restrict__ P, const float* __restrict__ K,
                   const float* __restrict__ C)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    const Cam cam = cam_load(CAM);
    const float pa = pixel_angle(cam, h);
    const float px = x + 0.5f, py = y + 0.5f;
    const float iw = 1.f / w, ih = 1.f / h;
    const float3 rd = cam_dir(cam, px, py, w, h);
    float epx;
    float3 col = pc_shade(pc_trace(cam.eye, rd, K, C), rd, cam, pa, px * iw, py * ih, TA, TB, K, C, P, epx);
    if (epx < 1.f) {                                    // moving silhouette / crease: 4 rotated-grid sub-rays
        float3 acc = f3(0.f);
        #pragma unroll 1
        for (int i = 0; i < 4; ++i) {
            const float sx = px + (i & 1 ? 0.375f : -0.125f) - 0.25f * (float)(i >> 1);
            const float sy = py - 0.375f + 0.25f * (float)i;
            const float3 d = cam_dir(cam, sx, sy, w, h);
            float e2;
            acc = acc + pc_shade(pc_trace(cam.eye, d, K, C), d, cam, pa, sx * iw, sy * ih, TA, TB, K, C, P, e2);
        }
        col = acc * 0.25f;
    }
    store_rgb(out, y * w + x, col);
}
"""


class PrismColumns(Transition):
    name = "prism_columns"
    label = "Prism columns"
    description = ("The picture splits into vertical prisms that turn in a staggered wave, flashing a glass / neon "
                   "face past mid-turn before the new picture lands.")
    default_duration_s = 2.0
    params = {
        "columns": num(3, _MAXC, 8, "Columns", tier="regional", integer=True, basic=True),
        "stagger": num(0.0, 0.85, 0.5, "Stagger", tier="regional"),
        "wave_shape": num(0.0, 1.0, 0.0, "Wave (sweep .. centre-out)", tier="regional"),
        "separation_z": num(0.0, 1.0, 0.35, "Z separation", tier="regional"),
        "gap": num(0.0, 1.0, 0.6, "Gap", tier="regional"),
        "third_face_style": num(0.0, 1.0, 0.5, "Third face (glass .. neon .. palette)", tier="regional",
                                basic=True),
        "specular": num(0.0, 1.0, 0.6, "Specular sweep", tier="regional"),
        "edge_glow": num(0.0, 1.0, 0.55, "Edge glow", tier="regional"),
        "camera": num(0.0, 1.0, 0.4, "Camera move", tier="regional"),
        "snap": num(0.0, 1.0, 0.5, "Snap (wind-up / overshoot)", tier="regional"),
        "sides": num(3, 4, 3, "Sides", tier="structural", integer=True),
        "route": enum(["via_third", "direct"], "via_third", "Turn", tier="structural"),
        "direction": enum(["right", "left", "alternate"], "right", "Direction", tier="structural"),
        **palette_params("neon", period=0.7, drift=0.08),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("prism_columns")
        self._mips_a = FrameMips(ctx, levels=4)
        self._mips_b = FrameMips(ctx, levels=4)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._cam = cp.zeros(16, dtype=cp.float32)
        self._camh = np.zeros(16, np.float32)
        self._K = cp.zeros(_KN, dtype=cp.float32)
        self._Kh = np.zeros(_KN, np.float32)
        self._C = cp.zeros(_MAXC * _CF, dtype=cp.float32)
        self._Ch = np.zeros((_MAXC, _CF), np.float32)
        seed = int(self.params.get("seed", 1)) & 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        self._jit = rng.random(_MAXC)
        self._zr = rng.random(_MAXC)
        self._hue = (rng.random(_MAXC) - 0.5) * 0.08
        self._flip = bool(rng.random() < 0.5)
        self._ysign = 1.0 if rng.random() < 0.5 else -1.0
        self._drift0 = float(rng.random())
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
        A = w / h

        N = int(min(max(int(round(float(prm["columns"]))), 3), _MAXC))
        n = 4 if int(round(float(prm["sides"]))) >= 4 else 3
        via = str(prm.get("route", "via_third")) != "direct"
        delta = 2.0 * math.pi / n
        theta_total = (2 if via else 1) * delta
        s = 2.0 * A / N
        rin = s / (2.0 * math.tan(math.pi / n))
        rc = s / (2.0 * math.sin(math.pi / n))

        # staggered per-column progress
        stagger = min(max(float(prm["stagger"]), 0.0), 0.85)
        ws = min(max(float(prm["wave_shape"]), 0.0), 1.0)
        k = np.arange(N, dtype=np.float64)
        lin = k / (N - 1)
        if self._flip:
            lin = 1.0 - lin
        order = (1.0 - ws) * lin + ws * np.abs(2.0 * k / (N - 1) - 1.0)
        order = np.clip(order + 0.12 * (self._jit[:N] - 0.5), 0.0, 1.0)
        lp = np.clip((p - stagger * order) / (1.0 - stagger), 0.0, 1.0)
        e0 = lp * lp * lp * (lp * (lp * 6.0 - 15.0) + 10.0)                  # smootherstep
        snap = float(prm["snap"])
        e = e0 + snap * 0.35 * np.sin(math.pi * lp) ** 3 * (lp - 0.35)        # wind-up then overshoot
        mk = np.sin(math.pi * e0)

        dmode = str(prm.get("direction", "right"))
        if dmode == "left":
            dirs = -np.ones(N)
        elif dmode == "alternate":
            dirs = np.where(np.arange(N) % 2 == 0, 1.0, -1.0)
        else:
            dirs = np.ones(N)
        th = dirs * theta_total * e
        gap = float(prm["gap"])
        sc = 1.0 - gap * (1.0 - math.sin(math.pi / n) + 0.08) * mk
        sep = float(prm["separation_z"])
        zpat = np.where(np.arange(N) % 2 == 0, 0.55, -1.0) * (0.6 + 0.4 * self._zr[:N])
        zc = -rin + sep * 0.9 * mk * zpat
        xc = -A + (k + 0.5) * s

        C = self._Ch
        C[:N, 0] = np.cos(th)
        C[:N, 1] = np.sin(th)
        C[:N, 2] = xc
        C[:N, 3] = zc
        C[:N, 4] = sc
        C[:N, 5] = mk
        C[:N, 6] = dirs
        C[:N, 7] = self._hue[:N]
        self._C.set(C.reshape(-1))

        zlo = float(np.min(zc - rc * sc)) - 1e-3
        zhi = float(np.max(zc + rc * sc)) + 1e-3

        # camera: flat at both ends, pulls back and swings mid-way
        g = math.sin(math.pi * p)
        bump = g * g
        cam_amt = float(prm["camera"])
        D = flat_distance(_FOV)
        cam = camera_orbit((0.0, 0.0, 0.0), D * (1.0 + 0.6 * cam_amt * bump), yaw=0.30 * cam_amt * bump * self._ysign,
                           pitch=0.10 * cam_amt * bump, fov_deg=_FOV)
        self._camh[:13] = cam
        self._cam.set(self._camh)

        drift = float(prm["drift_speed"]) * p * self.duration_s + self._drift0
        drift -= math.floor(drift)

        K = self._Kh
        K[:] = 0.0
        K[0:16] = (N, n, s, rin, rc, A, zlo, zhi, p, g, 0.85, float(prm["specular"]), float(prm["edge_glow"]),
                   float(prm["third_face_style"]), float(prm["palette_period"]), drift)
        for i in range(n):
            phi = -i * delta
            K[16 + 2 * i] = math.sin(phi)
            K[17 + 2 * i] = math.cos(phi)
        K[24] = 1.0 if via else 0.0
        K[25] = bump * cam_amt
        K[26] = -1.0 + 2.0 * p
        K[27] = h / 2160.0
        self._K.set(K)

        self._mips_a.build(a)
        self._mips_b.build(b)
        launch_2d(self._k, w, h,
                  (out, np.int32(w), np.int32(h), *self._mips_a.args(a), *self._mips_b.args(b),
                   self._cam, self._P, self._K, self._C))
