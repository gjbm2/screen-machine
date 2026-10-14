"""Rutt/Etra scan: the frame redrawn as glowing horizontal scanlines lifted by luminance, on a tilted 3D plane.

Source: Original (inspired by http://v002.info/licenses/, https://github.com/v002/v002-Rutt-Etra,
        https://experiments.withgoogle.com/rutt-etra-izer; after the Rutt/Etra Scan Processor)
Author: smfx (original; after the Rutt/Etra Scan Processor; v002 Rutt-Etra is CC-BY-NC-SA-3.0, idea only)
License: MIT (original)
license_class: permissive

The analog video-synth classic.  The input frame is cut into horizontal scanlines; every line is lifted (world up)
by the luminance under it, so bright parts of the picture rise as ridges, and the plane the lines sit on tilts back
in perspective (with a slow sway about the vertical axis).  Lines are coloured by the source (or by a palette
indexed by luminance: "colour" morphs source .. mono palette), drawn as anti-aliased glowing strokes and combined
with a screen blend on black.  "grid" fades in the mesh's vertical edges (lines .. wireframe grid).

amount: 0 is the untouched frame (copied).  As it rises the line spacing grows from ~1 px (which reads as the image),
the black gaps open, and the height (z_scale) and tilt ramp in with m = smoothstep(amount); the first quarter of
the ramp crossfades from the crisp input.  pulse: kicks the height (and a little glow), decaying with the envelope.

Implementation (two kernels, no splatting, no atomics):
  1. re_lines: one thread per (line, sample): colour (trilinear from FrameMips at a lod matched to the spacing) and a
     smoother luminance per line sample -> a compact (lines x WL) buffer (WL = W / 3: one sample per ~3 px at 4K).
  2. rutt_etra: full-res gather.  Displacement is along world +y and the camera looks straight down -z, so a
     line's screen x depends only on its base point: per pixel the line parameter is solved in closed form (linear
     in the line's height on the frame).  The pixel's ray is intersected with the base plane and the plane lifted by
     the full height: only lines whose base lies between the two can reach the pixel (<= 20 candidates; the height is
     scaled down per pixel, smoothly, where more would be needed).  Each candidate is rejected cheaply from its
     4-sample height range, else the exact screen distance to its 3 nearest polyline segments gives an AA core and a
     Gaussian glow.  Grid edges are the segments joining neighbouring lines at evenly spaced columns.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, flat_distance, rot_euler, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
NP = 48
# P layout (floats)
#  0..2 q = frame centre - eye | 3 pa (tan per px) | 4..6 ex | 7..9 ey | 10..12 en | 13 A | 14 s_w (line spacing,
#  frame units) | 15 jc | 16 zs (height) | 17 hw (core half width, px) | 18 glow amp | 19 1/(2 sigma^2) (px^-2)
#  20 R (px reach) | 21 grid | 22 kc | 23 ncol | 24 lod colour | 25 lod height | 26 colour mix | 27 palette period
#  28 drift | 29 saturation | 30 gain | 31 render weight (vs src) | 32..43 palette
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define RE_KMAX 20

__device__ __forceinline__ float3 re_unpack(unsigned int c) {
    return f3((float)(c & 255u), (float)((c >> 8) & 255u), (float)((c >> 16) & 255u)) * (1.f / 255.f);
}
// Distance from (px, py) to segment a-b (any units); t = parameter of the nearest point.
__device__ __forceinline__ float re_seg(float px, float py, float ax, float ay, float bx, float by, float& t) {
    const float abx = bx - ax, aby = by - ay, apx = px - ax, apy = py - ay;
    t = saturate((apx * abx + apy * aby) / fmaxf(abx * abx + aby * aby, 1e-24f));
    const float dx = apx - abx * t, dy = apy - aby * t;
    return sqrtf(dx * dx + dy * dy);
}
__device__ __forceinline__ float3 re_screen(float3 acc, float3 c) {
    return f3(1.f) - (f3(1.f) - acc) * (f3(1.f) - saturate(c));
}

// Pass 1: per line j, per sample i: luminance (height, smoother lod) + packed line colour.
SDF_KERNEL
void re_lines(uint2* __restrict__ lb, const int WL, const int nl, FRAME_ARGS, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= WL || j >= nl) return;
    TEX_FROM_ARGS;
    const float u = (i + 0.5f) / (float)WL;
    const float v = 0.5f - 0.5f * (P[15] - (float)j) * P[14];
    float3 c = tex_sample(T, u, v, P[24]);
    const float L = saturate(luma3(tex_sample(T, u, v, P[25])));
    const float3 pc = pal(L * P[27] + P[28] + 0.35f * v, P + 32);
    c = saturate(saturation(c, P[29]) * P[30]);
    float3 lc = mix(c, pc * (0.2f + 0.95f * L), P[26]);
    lc = saturate(lc + (f3(1.f) - lc) * (pc * 0.09f));          // faint palette floor: dark areas keep their lines
    const unsigned int pk = (unsigned int)(lc.x * 255.f + 0.5f) | ((unsigned int)(lc.y * 255.f + 0.5f) << 8)
                          | ((unsigned int)(lc.z * 255.f + 0.5f) << 16);
    lb[j * WL + i] = make_uint2(__float_as_uint(L), pk);
}

// Pass 2: full-res gather of the lifted scanlines (and grid edges), screen-blended on black.
SDF_KERNEL
void rutt_etra(uchar4* __restrict__ out, const uchar4* __restrict__ srci, const int w, const int h,
               const uint2* __restrict__ lb, const int WL, const int nl, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float3 q = load3(P), ex = load3(P + 4), ey = load3(P + 7), en = load3(P + 10);
    const float pa = P[3], A = P[13], s_w = P[14], jc = P[15];
    const float hw = P[17], gamp = P[18], i2s = P[19], Rpx = P[20], grid = P[21];
    const float Xp = (x + 0.5f - 0.5f * w) * pa, Yp = (0.5f * h - (y + 0.5f)) * pa;
    const float ipa = 1.f / pa;
    const float3 rd = normalize(f3(Xp, Yp, -1.f));
    float3 acc = f3(0.f);

    // screen position (tan units) of frame point (s, yy) lifted by hh (world up)
    auto proj = [&](float s, float yy, float hh, float& X, float& Y) {
        const float iz = 1.f / fmaxf(-(q.z + s * ex.z + yy * ey.z), 1e-4f);
        X = (q.x + s * ex.x + yy * ey.x) * iz;
        Y = (q.y + s * ex.y + yy * ey.y + hh) * iz;
    };
    auto glow = [&](float dp) { return saturate(hw + 0.5f - dp) + gamp * __expf(-dp * dp * i2s); };

    const float rdn = dot(rd, en);
    if (rdn < -0.02f) {
        const float tb = dot(q, en) / rdn;                       // ray -> base plane
        if (tb > 0.f) {
            const float3 pb = rd * tb - q;
            const float yb = dot(pb, ey);                         // frame y under the pixel
            const float dydz = dot(rd, ey) * en.y / rdn - ey.y;   // d(frame y of reachable lines) / d(height)
            const float mg = Rpx * pa * tb * 1.2f / fmaxf(-rdn, 0.3f);
            float zs = P[16];
            const float room = (float)(RE_KMAX - 1) - 2.f * mg / s_w;
            const float span = fabsf(dydz) * zs / s_w;
            if (span > room) zs *= fmaxf(room, 0.f) / span;        // smooth per-pixel cap: never a gap
            const float yt = yb + zs * dydz;
            const float ylo = fminf(yb, yt) - mg, yhi = fmaxf(yb, yt) + mg;
            const int j0 = max((int)ceilf(jc - yhi / s_w), 0);
            const int j1 = min(min((int)floorf(jc - ylo / s_w), nl - 1), j0 + RE_KMAX - 1);
            const int j1e = grid > 0.f ? min(j1 + 1, nl - 1) : j1;
            // line parameter s (frame x) at this pixel's screen x: linear in the line's frame y
            const float n0 = -(Xp * q.z + q.x), n1 = -(ey.x + Xp * ey.z);
            const float iden = 1.f / (ex.x + Xp * ex.z);
            const float Rt = (Rpx + 2.f) * pa;
            const float uS = 0.5f / A, fWL = (float)WL, sk = 2.f * A / fWL;
            const float kc = P[22];
            const int ncol = (int)P[23];
            for (int j = j0; j <= j1e; ++j) {
                const float yj = (jc - (float)j) * s_w;
                const float wj = smoothstep(0.f, 0.6f * s_w, 1.f - fabsf(yj));
                const float sj = (n0 + yj * n1) * iden;
                const float fi = (0.5f + sj * uS) * fWL - 0.5f;
                const uint2* __restrict__ row = lb + (size_t)j * WL;
                if (j <= j1 && wj > 0.f && fi > -4.f && fi < fWL + 3.f) {
                    const int i1 = (int)floorf(fi);
                    const int k0 = min(max(i1 - 1, 0), WL - 1), k1 = min(max(i1, 0), WL - 1);
                    const int k2 = min(max(i1 + 1, 0), WL - 1), k3 = min(max(i1 + 2, 0), WL - 1);
                    const uint2 a0 = row[k0], a1 = row[k1], a2 = row[k2], a3 = row[k3];
                    const float L0 = __uint_as_float(a0.x), L1 = __uint_as_float(a1.x);
                    const float L2 = __uint_as_float(a2.x), L3 = __uint_as_float(a3.x);
                    const float izc = 1.f / fmaxf(-(q.z + sj * ex.z + yj * ey.z), 1e-4f);
                    const float Yb = (q.y + sj * ex.y + yj * ey.y) * izc;
                    const float lmin = fminf(fminf(L0, L1), fminf(L2, L3)), lmax = fmaxf(fmaxf(L0, L1), fmaxf(L2, L3));
                    if (Yp > Yb + zs * lmin * izc - Rt && Yp < Yb + zs * lmax * izc + Rt) {
                        const float s0 = ((float)k0 + 0.5f) * sk - A, s1 = ((float)k1 + 0.5f) * sk - A;
                        const float s2 = ((float)k2 + 0.5f) * sk - A, s3 = ((float)k3 + 0.5f) * sk - A;
                        float Xa, Ya, Xb, Yb2, Xc, Yc, Xd, Yd;
                        proj(s0, yj, zs * L0, Xa, Ya); proj(s1, yj, zs * L1, Xb, Yb2);
                        proj(s2, yj, zs * L2, Xc, Yc); proj(s3, yj, zs * L3, Xd, Yd);
                        float t0, t1, t2;
                        const float d0 = re_seg(Xp, Yp, Xa, Ya, Xb, Yb2, t0);
                        const float d1 = re_seg(Xp, Yp, Xb, Yb2, Xc, Yc, t1);
                        const float d2 = re_seg(Xp, Yp, Xc, Yc, Xd, Yd, t2);
                        float d = d1, tt = t1;
                        unsigned int ca = a1.y, cb = a2.y;
                        if (d0 < d) { d = d0; tt = t0; ca = a0.y; cb = a1.y; }
                        if (d2 < d) { d = d2; tt = t2; ca = a2.y; cb = a3.y; }
                        const float dp = d * ipa;
                        if (dp < Rpx) acc = re_screen(acc, mix(re_unpack(ca), re_unpack(cb), tt) * (glow(dp) * wj));
                    }
                }
                // grid: the mesh's vertical edges between line j-1 and line j at the two columns around the pixel
                if (grid > 0.f && j > 0) {
                    const float yk = yj + s_w;
                    const float wk = fminf(wj, smoothstep(0.f, 0.6f * s_w, 1.f - fabsf(yk)));
                    const uint2* __restrict__ rowp = row - WL;
                    const int c0 = (int)floorf(kc + sj / s_w);
                    #pragma unroll
                    for (int cc = c0; cc <= c0 + 1; ++cc) {
                        if (cc < 0 || cc >= ncol || wk <= 0.f) continue;
                        const float xk = ((float)cc - kc) * s_w;
                        const float wc = smoothstep(0.f, 0.6f * s_w, A - fabsf(xk));
                        const float fk = (0.5f + xk * uS) * fWL - 0.5f;
                        const float ff = floorf(fk);
                        const int ia = min(max((int)ff, 0), WL - 1), ib = min(max((int)ff + 1, 0), WL - 1);
                        const float tf = saturate(fk - ff);
                        const uint2 p0 = row[ia], p1 = row[ib], r0 = rowp[ia], r1 = rowp[ib];
                        const float hj = zs * mix(__uint_as_float(p0.x), __uint_as_float(p1.x), tf);
                        const float hk = zs * mix(__uint_as_float(r0.x), __uint_as_float(r1.x), tf);
                        float Xa, Ya, Xb, Yb2, te;
                        proj(xk, yj, hj, Xa, Ya); proj(xk, yk, hk, Xb, Yb2);
                        const float de = re_seg(Xp, Yp, Xa, Ya, Xb, Yb2, te) * ipa;
                        if (de < Rpx) {
                            const float3 cj = mix(re_unpack(p0.y), re_unpack(p1.y), tf);
                            const float3 ck = mix(re_unpack(r0.y), re_unpack(r1.y), tf);
                            acc = re_screen(acc, mix(cj, ck, te) * (glow(de) * grid * wc * wk));
                        }
                    }
                }
            }
        }
    }
    float3 c = acc;
    const float wr = P[31];
    if (wr < 1.f) {
        const uchar4 s = srci[y * w + x];
        c = mix(f3((float)s.x, (float)s.y, (float)s.z) * (1.f / 255.f), c, wr);
    }
    store_rgb_dither(out, x, y, w, c, wr);
}
"""


class RuttEtra(Effect):
    name = "rutt_etra"
    stage = "projection"
    label = "Rutt/Etra scan"
    description = ("The frame redrawn as glowing scanlines lifted by luminance on a tilted 3D plane "
                   "(the analog video-synth classic); optional palette colour and grid.")
    params = {
        **standard_effect_params(),
        "line_spacing": num(4.0, 40.0, 10.0, "Line spacing", unit="px", step=0.5, tier="regional", basic=True),
        "z_scale": num(0.0, 0.5, 0.22, "Height (luma lift)", step=0.005, tier="regional"),
        "tilt": num(0.0, 45.0, 30.0, "Tilt", unit="°", step=0.5, tier="regional"),
        "line_glow": num(0.0, 1.0, 0.45, "Line glow", tier="regional"),
        "line_width": num(0.5, 4.0, 1.4, "Line width", unit="px", step=0.05, tier="regional"),
        "colour": num(0.0, 1.0, 0.0, "Colour: source .. palette", tier="regional"),
        "grid": num(0.0, 1.0, 0.0, "Lines .. grid", tier="regional"),
        "sway": num(0.0, 1.0, 0.35, "Sway", tier="regional"),
        "kick": num(0.0, 2.0, 0.8, "Beat height kick", tier="regional"),
        **palette_params("ice", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._kl = mod.get_function("re_lines")
        self._kg = mod.get_function("rutt_etra")
        self.mips = FrameMips(ctx, levels=4)
        self.W, self.H = ctx.width, ctx.height
        self.A = self.W / self.H
        self.WL = max(64, self.W // 3)
        self.NLMAX = self.H + 5
        self.lb = cp.zeros((self.NLMAX, self.WL, 2), cp.uint32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.ph1 = 0.0
        self.ph2 = 1.7
        self.drift = 0.0
        self._pal_name = None
        self._pal = None

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        dtc = min(max(float(dt), 0.0), 0.1)
        self.ph1 = math.fmod(self.ph1 + 2.0 * math.pi * 0.07 * dtc, 2.0 * math.pi)
        self.ph2 = math.fmod(self.ph2 + 2.0 * math.pi * 0.045 * dtc, 2.0 * math.pi)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        W, H, A = self.W, self.H, self.A
        m = smoothstep01(a)
        k = H / 1080.0
        # spacing grows from ~1.5 px (reads as the image) to the knob; gaps open; height and tilt ramp with m
        r_sp = smoothstep01(a / 0.6)
        sp_px = 1.5 + (float(p["line_spacing"]) * k - 1.5) * r_sp
        s_w = max(2.0 * sp_px / H, 2.0 / (self.NLMAX - 3))
        jcn = int(math.floor(1.0 / s_w))
        nl = min(2 * jcn + 1, self.NLMAX)
        kcn = int(math.floor(A / s_w))
        r_gap = smoothstep01((a - 0.06) / 0.5)
        hw_fill = 0.5 * sp_px + 0.35
        hw = hw_fill + (0.5 * float(p["line_width"]) * k - hw_fill) * r_gap
        sigma = (1.5 + 5.5 * float(p["line_glow"])) * k
        gamp = 0.6 * float(p["line_glow"]) * r_gap * (1.0 + 0.5 * pulse * m)
        reach = hw + 1.0 + (2.6 * sigma if gamp > 1e-3 else 0.0)
        zs = float(p["z_scale"]) * m * (1.0 + float(p["kick"]) * pulse)
        sway = float(p["sway"]) * m
        tilt = math.radians(float(p["tilt"])) * m + 0.05 * sway * math.sin(self.ph2)
        yaw = 0.2 * sway * math.sin(self.ph1)
        R = rot_euler(yaw, -tilt, 0.0)
        D = flat_distance(FOV)
        eye = (0.0, 0.3 * zs, D * (1.0 + 0.15 * m))
        lod_c = max(math.log2(max(sp_px, 1.0)) - 1.0, math.log2(W / self.WL) - 0.5, 0.0)
        lod_z = min(lod_c + 0.75, 4.0)
        if p["palette"] != self._pal_name:
            self._pal_name = p["palette"]
            self._pal = palette_coeffs(p["palette"])
        h = self.host
        h[0:3] = (-eye[0], -eye[1], -eye[2])
        h[3] = 2.0 * math.tan(math.radians(FOV) * 0.5) / H
        h[4:7] = (R[0], R[3], R[6])
        h[7:10] = (R[1], R[4], R[7])
        h[10:13] = (R[2], R[5], R[8])
        h[13] = A
        h[14] = s_w
        h[15] = jcn
        h[16] = zs
        h[17] = hw
        h[18] = gamp
        h[19] = 1.0 / (2.0 * sigma * sigma)
        h[20] = reach
        h[21] = float(p["grid"]) * r_gap
        h[22] = kcn
        h[23] = 2 * kcn + 1
        h[24] = lod_c
        h[25] = lod_z
        h[26] = float(p["colour"])
        h[27] = float(p["palette_period"])
        h[28] = self.drift
        h[29] = 1.0 + 0.2 * m
        h[30] = 1.0 + 0.2 * r_gap
        h[31] = smoothstep01(a / 0.25)
        h[32:44] = self._pal
        self.P.set(h)
        self.mips.build(src)
        launch_2d(self._kl, self.WL, nl, (self.lb, np.int32(self.WL), np.int32(nl), *self.mips.args(src), self.P))
        launch_2d(self._kg, W, H, (out, src, np.int32(W), np.int32(H), self.lb, np.int32(self.WL), np.int32(nl), self.P))
