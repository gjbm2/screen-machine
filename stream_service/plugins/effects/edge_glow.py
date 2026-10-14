"""Edge glow: Tron-style neon outlines.  Scene edges (Sobel on luma) light up in drifting palette colours with a
hot core and a soft glow, over a dimmed source, like LED strips laid along the geometry.

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/SobelOperatorShader.js
        (licence: https://github.com/mrdoob/three.js/blob/dev/LICENSE, "The MIT License")
        https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_EdgeDetect.fx
        (licence: https://github.com/GarrettGunnell/AcerolaFX/blob/main/LICENSE, MIT)
Author: three.js authors (SobelOperatorShader); Garrett Gunnell (AcerolaFX EdgeDetect)
License: MIT
license_class: permissive
Ported from GLSL/HLSL by smfx (ideas/changes: rewritten as CUDA kernels; luma is taken at half res (1920x1080 at
4K) through a 5-tap pre-blur whose footprint is the line_width knob; the 3x3 Sobel runs on that with a matching
bilinear step; AcerolaFX-style soft threshold on the gradient magnitude; each edge pixel is coloured by the shared
cosine palette, indexed by a morph between the doubled gradient orientation (so both sides of a line agree) and the
local luma, plus a gentle screen-space ramp and the drift phase; the glow is a two-level (quarter + eighth res)
separable 9-tap Gaussian (5 bilinear taps per pass); the 4K composite screens the hot-cored lines and glow over a
dimmed, slightly desaturated source in gamma-2 linear light.)

Realtime: amount -> edge brightness and source dimming together (amount 0 copies src exactly);
pulse -> glow radius + glow/line brightness kick (glow_kick) and a palette drift-rate lurch (drift_kick,
integrated on the host, so the hue never jumps); drift_speed -> colour drift rate.
Regional: edge_threshold, line_width, source_dim (basic), colour_by (0 edge angle, 1 luma), glow_radius,
glow_kick, drift_kick, palette, palette_period.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NPRM = 24   # 0..11 palette | 12 drift | 13 period | 14 colour_by | 15 t0 | 16 t1 | 17 sobel step

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lumL(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

// Bilinear sample of a float (1-channel) buffer at continuous pixel coords (centres at i + 0.5), clamped.
__device__ float f1bl(const float* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

// Bilinear sample of a float4 buffer.
__device__ float4 f4bl(const float4* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float4 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}

// 4K src -> half-res luma with a 5-tap pre-blur of footprint o (4K px): line_width.
extern "C" __global__
void eg_luma(const uchar4* __restrict__ src, float* __restrict__ L, const int W, const int H,
             const int w, const int h, const float o)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float cx = (x + 0.5f) * W / w, cy = (y + 0.5f) * H / h;
    float l = 0.36f * luma(bilin(src, cx, cy, W, H));
    l += 0.16f * luma(bilin(src, cx - o, cy - o, W, H));
    l += 0.16f * luma(bilin(src, cx + o, cy - o, W, H));
    l += 0.16f * luma(bilin(src, cx - o, cy + o, W, H));
    l += 0.16f * luma(bilin(src, cx + o, cy + o, W, H));
    L[y * w + x] = l;
}

// Sobel on half-res luma -> coloured edge E (gamma-2 linear rgb * intensity, w = intensity).
extern "C" __global__
void eg_sobel(const float* __restrict__ L, float4* __restrict__ E, const int w, const int h,
              const float* __restrict__ prm)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float st = prm[17];
    const float cx = x + 0.5f, cy = y + 0.5f;
    const float a = f1bl(L, cx - st, cy - st, w, h), b = f1bl(L, cx, cy - st, w, h), c = f1bl(L, cx + st, cy - st, w, h);
    const float d = f1bl(L, cx - st, cy, w, h),                                       f = f1bl(L, cx + st, cy, w, h);
    const float g = f1bl(L, cx - st, cy + st, w, h), hh = f1bl(L, cx, cy + st, w, h), i = f1bl(L, cx + st, cy + st, w, h);
    const float gx = (c + 2.f * f + i) - (a + 2.f * d + g);
    const float gy = (g + 2.f * hh + i) - (a + 2.f * b + c);
    const float m = sqrtf(gx * gx + gy * gy + 1e-12f) * 0.25f;      // ~ luma step across the edge
    const float t0 = prm[15], t1 = prm[16];
    const float inten = smooth(t0, t1, m) * (0.65f + 0.35f * smooth(t0, t0 + 0.35f, m));
    if (inten <= 1e-4f) { E[y * w + x] = make_float4(0.f, 0.f, 0.f, 0.f); return; }
    // doubled-angle orientation (0..1): both sides of a thin line get the same colour
    const float o = atan2f(2.f * gx * gy, gx * gx - gy * gy + 1e-12f) * 0.15915494f;
    const float ia = 0.5f + 0.5f * __sinf(6.2831853f * (o + 0.125f));
    const float il = 0.0625f * (a + b + c + d + f + g + hh + i) + 0.5f * L[y * w + x];   // local luma
    const float u = cx / w, v = cy / h;
    const float idx = (ia + (il - ia) * prm[14] + 0.3f * (0.7f * u + 0.3f * v)) * prm[13] + prm[12];
    float3 col = pal(idx, prm);
    col.x *= col.x; col.y *= col.y; col.z *= col.z;                  // sRGB-ish -> gamma-2 linear
    const float pm = fmaxf(col.x, fmaxf(col.y, col.z));
    const float nrm = 1.f / fmaxf(pm, 0.15f);                        // full-brightness LED colour
    E[y * w + x] = make_float4(col.x * nrm * inten, col.y * nrm * inten, col.z * nrm * inten, inten);
}

// 2x downsample (4 bilinear taps = 4x4 box) of a float4 buffer.
extern "C" __global__
void eg_down(const float4* __restrict__ s, float4* __restrict__ d, const int sw, const int sh, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)sw / w, sy = (float)sh / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy, ox = 0.5f * sx, oy = 0.5f * sy;
    const float4 a = f4bl(s, cx - ox, cy - oy, sw, sh), b = f4bl(s, cx + ox, cy - oy, sw, sh);
    const float4 c = f4bl(s, cx - ox, cy + oy, sw, sh), e = f4bl(s, cx + ox, cy + oy, sw, sh);
    d[y * w + x] = make_float4(0.25f * (a.x + b.x + c.x + e.x), 0.25f * (a.y + b.y + c.y + e.y),
                               0.25f * (a.z + b.z + c.z + e.z), 0.25f * (a.w + b.w + c.w + e.w));
}

// Separable 9-tap Gaussian as 5 bilinear taps, spacing s (texels), direction (dx, dy).
extern "C" __global__
void eg_blur(const float4* __restrict__ s, float4* __restrict__ d, const int w, const int h,
             const float dx, const float dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float cx = x + 0.5f, cy = y + 0.5f;
    const float o1 = 1.3846154f, o2 = 3.2307692f, w0 = 0.2270270f, w1 = 0.3162162f, w2 = 0.0702703f;
    const float4 c0 = s[y * w + x];
    const float4 p1 = f4bl(s, cx + dx * o1, cy + dy * o1, w, h), m1 = f4bl(s, cx - dx * o1, cy - dy * o1, w, h);
    const float4 p2 = f4bl(s, cx + dx * o2, cy + dy * o2, w, h), m2 = f4bl(s, cx - dx * o2, cy - dy * o2, w, h);
    d[y * w + x] = make_float4(w0 * c0.x + w1 * (p1.x + m1.x) + w2 * (p2.x + m2.x),
                               w0 * c0.y + w1 * (p1.y + m1.y) + w2 * (p2.y + m2.y),
                               w0 * c0.z + w1 * (p1.z + m1.z) + w2 * (p2.z + m2.z),
                               w0 * c0.w + w1 * (p1.w + m1.w) + w2 * (p2.w + m2.w));
}

// 4K composite: screen the hot-cored lines + two-level glow over the dimmed, desaturated source (gamma-2 linear).
extern "C" __global__
void eg_comp(const uchar4* __restrict__ src, const float4* __restrict__ E, const float4* __restrict__ G2,
             const float4* __restrict__ G3, uchar4* __restrict__ out, const int W, const int H,
             const int w1, const int h1, const int w2, const int h2, const int w3, const int h3,
             const float dim, const float desat, const float cg, const float g2, const float g3, const float hot)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u = (x + 0.5f) / W, v = (y + 0.5f) / H;
    const float4 e = f4bl(E, u * w1, v * h1, w1, h1);
    const float4 q2 = f4bl(G2, u * w2, v * h2, w2, h2);
    const float4 q3 = f4bl(G3, u * w3, v * h3, w3, h3);
    const float wc = hot * e.w * e.w;                                 // white-hot core on strong edges
    const float ax = cg * (e.x + wc) + g2 * q2.x + g3 * q3.x;
    const float ay = cg * (e.y + wc) + g2 * q2.y + g3 * q3.y;
    const float az = cg * (e.z + wc) + g2 * q2.z + g3 * q3.z;
    const uchar4 p = src[y * W + x];
    float3 s = make_float3(p.x * (1.f / 255.f), p.y * (1.f / 255.f), p.z * (1.f / 255.f));
    s.x *= s.x; s.y *= s.y; s.z *= s.z;
    const float l = lumL(s);
    s.x = (l + (s.x - l) * desat) * dim; s.y = (l + (s.y - l) * desat) * dim; s.z = (l + (s.z - l) * desat) * dim;
    const float bx = 1.f - __expf(-fmaxf(ax, 0.f)), by = 1.f - __expf(-fmaxf(ay, 0.f)), bz = 1.f - __expf(-fmaxf(az, 0.f));
    s.x = s.x + bx - s.x * bx; s.y = s.y + by - s.y * by; s.z = s.z + bz - s.z * bz;
    out[y * W + x] = pack(make_float4(sqrtf(fmaxf(s.x, 0.f)) * 255.f, sqrtf(fmaxf(s.y, 0.f)) * 255.f,
                                      sqrtf(fmaxf(s.z, 0.f)) * 255.f, 255.f));
}
"""


class EdgeGlow(Effect):
    name = "edge_glow"
    stage = "stylise"
    label = "Edge glow"
    description = "Tron-style neon outlines: scene edges light up in drifting palette colours over a dimmed source."
    params = {
        **standard_effect_params(),
        **palette_params("neon", 1.0, 0.05),
        "edge_threshold": num(0.0, 1.0, 0.3, "Edge threshold"),
        "line_width": num(0.0, 2.0, 0.6, "Line width (pre-blur)", step=0.05),
        "source_dim": num(0.0, 1.0, 0.7, "Source dimming", basic=True),
        "colour_by": num(0.0, 1.0, 0.25, "Colour by (0 edge angle, 1 luma)"),
        "glow_radius": num(0.0, 1.0, 0.5, "Glow radius"),
        "glow_kick": num(0.0, 2.0, 0.8, "Beat kick (glow radius + brightness)", step=0.05),
        "drift_kick": num(0.0, 2.0, 0.6, "Beat kick (colour drift lurch)", step=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kl = mod.get_function("eg_luma")
        self._ks = mod.get_function("eg_sobel")
        self._kd = mod.get_function("eg_down")
        self._kb = mod.get_function("eg_blur")
        self._kc = mod.get_function("eg_comp")
        W, H = ctx.width, ctx.height
        self._s1 = (max(1, (W + 1) // 2), max(1, (H + 1) // 2))
        self._s2 = (max(1, (self._s1[0] + 1) // 2), max(1, (self._s1[1] + 1) // 2))
        self._s3 = (max(1, (self._s2[0] + 1) // 2), max(1, (self._s2[1] + 1) // 2))
        (w1, h1), (w2, h2), (w3, h3) = self._s1, self._s2, self._s3
        self._L = cp.zeros((h1, w1), dtype=cp.float32)
        self._E = cp.zeros((h1, w1, 4), dtype=cp.float32)
        self._G2 = cp.zeros((h2, w2, 4), dtype=cp.float32)
        self._T2 = cp.zeros((h2, w2, 4), dtype=cp.float32)
        self._G3 = cp.zeros((h3, w3, 4), dtype=cp.float32)
        self._T3 = cp.zeros((h3, w3, 4), dtype=cp.float32)
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._prm_h = np.zeros(NPRM, dtype=np.float32)
        self._pal_name = None
        self._drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        k = min(max(p["pulse"], 0.0), 1.0)
        # colour drift, with a beat lurch in the *rate* (integrated, so the hue never jumps)
        self._drift = (self._drift + p["drift_speed"] * (1.0 + 8.0 * p["drift_kick"] * k) * dt) % 1.0
        amt = min(max(p["amount"], 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._prm_h[:12] = palette_coeffs(p["palette"])
        thr = p["edge_threshold"]
        lw = p["line_width"]
        t0 = 0.012 + 0.22 * thr ** 1.3
        self._prm_h[12] = self._drift
        self._prm_h[13] = p["palette_period"]
        self._prm_h[14] = p["colour_by"]
        self._prm_h[15] = t0
        self._prm_h[16] = t0 + 0.035 + 0.08 * thr
        self._prm_h[17] = 0.75 + 0.6 * lw
        self._prm.set(self._prm_h)

        W, H = self.ctx.width, self.ctx.height
        (w1, h1), (w2, h2), (w3, h3) = self._s1, self._s2, self._s3
        i32, f32 = np.int32, np.float32
        launch_2d(self._kl, w1, h1, (src, self._L, i32(W), i32(H), i32(w1), i32(h1), f32(1.0 + 2.0 * lw)))
        launch_2d(self._ks, w1, h1, (self._L, self._E, i32(w1), i32(h1), self._prm))
        launch_2d(self._kd, w2, h2, (self._E, self._G2, i32(w1), i32(h1), i32(w2), i32(h2)))
        launch_2d(self._kd, w3, h3, (self._G2, self._G3, i32(w2), i32(h2), i32(w3), i32(h3)))
        # glow radius: blur spacing + weight of the wide (eighth-res) level; the beat widens it
        gk = p["glow_kick"]
        r = min(p["glow_radius"] + 0.5 * gk * math.sqrt(k), 1.0)
        sp = 1.0 + 1.0 * r
        for G, T, (w, h) in ((self._G2, self._T2, (w2, h2)), (self._G3, self._T3, (w3, h3))):
            launch_2d(self._kb, w, h, (G, T, i32(w), i32(h), f32(sp), f32(0.0)))
            launch_2d(self._kb, w, h, (T, G, i32(w), i32(h), f32(0.0), f32(sp)))

        flash = 1.0 + 0.35 * min(gk, 1.0) * k                    # at most +35% on the lines
        gflash = 1.0 + 0.5 * gk * k                              # glow swells a little more
        cg = 2.2 * amt * flash
        g2 = 3.0 * amt * (1.0 - 0.55 * r) * gflash
        g3 = 4.5 * amt * (0.25 + 0.95 * r) * gflash
        sd = p["source_dim"] * amt
        dim = 1.0 - 0.94 * sd
        desat = 1.0 - 0.45 * sd
        launch_2d(self._kc, W, H, (src, self._E, self._G2, self._G3, out, i32(W), i32(H), i32(w1), i32(h1),
                                   i32(w2), i32(h2), i32(w3), i32(h3), f32(dim), f32(desat), f32(cg), f32(g2),
                                   f32(g3), f32(0.35)))
