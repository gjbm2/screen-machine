"""Neon contours: topographic neon.  The scene's luma is treated as a height map; its iso-bands
fract(lum * N - phase) are drawn as anti-aliased glowing neon tubes coloured by the shared palette, over a
darkened, desaturated source.  The phase integrates, so the contour lines march continuously up the brightness
hills, even on a static still.

Source: Original (inspired by https://github.com/Vidvox/ISF-Files/blob/master/ISF/Ridgelines.fs)
Author: smfx (original; ISF Ridgelines by VIDVOX, MIT, loose stylistic reference)
License: MIT
license_class: permissive
(Written from scratch as CUDA kernels; no code taken from the reference.  Pipeline: 4K src -> half-res luma (exact
2x2 box) -> two rounds of separable 9-tap Gaussian (5 bilinear taps) whose spacing is the smoothness knob -> a
half-res float4 field (L, dL/dx, dL/dy) -> one full-res composite that bilinearly samples the field and turns the
distance-to-nearest-iso-line (|f - round(f)| / |grad f|, i.e. the gradient magnitude of the blurred luma stands in
for fwidth) into a white-hot core, a coloured tube body and an analytic exponential halo, screened over the dimmed
source in gamma-2 linear light.  Where lines would pack tighter than their own width the pattern fades to its
average coverage (no moire); in flat areas with no slope the lines fade out.)

Realtime: amount -> source darkening/desaturation and line fade-in together, all through m = smoothstep(amount)
(amount 0 copies src exactly); pulse -> glow-radius, thickness and brightness kick plus a flow-rate kick (the lines
rush along their flow direction, then glide; integrated on the host so nothing jumps); flow_speed -> contour march
rate; drift_speed -> colour drift rate.
Regional: contour_count (basic), line_width, glow, base_dim, source_colour_mix, colour_by (0 height-tinted,
1 every line its own colour, travelling with the line), smoothness, pulse_kick, palette, palette_period.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

# prm: 0..11 palette | 12 drift | 13 period | 14 N | 15 phase frac | 16 kofs (line-id offset mod 7) | 17 colour_by
#      18 half-width px | 19 halo radius px | 20 core gain | 21 halo gain | 22 hot gain | 23 src colour mix
#      24 dim | 25 desat | 26 half-res px per out px | 27 line fade (m) | 28 flat g0 | 29 flat g1
NPRM = 32

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lumL(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

__device__ float f1bl(const float* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}

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

// 4K src -> half-res luma (one bilinear tap at the 2x2 corner = exact box at 2x; approx box otherwise).
extern "C" __global__
void nc_luma(const uchar4* __restrict__ src, float* __restrict__ L, const int W, const int H, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)W / w, sy = (float)H / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy;
    const float ox = 0.25f * sx, oy = 0.25f * sy;
    float l = luma(bilin(src, cx - ox, cy - oy, W, H)) + luma(bilin(src, cx + ox, cy - oy, W, H))
            + luma(bilin(src, cx - ox, cy + oy, W, H)) + luma(bilin(src, cx + ox, cy + oy, W, H));
    L[y * w + x] = 0.25f * l;
}

// Separable 9-tap Gaussian as 5 bilinear taps, spacing (dx, dy) in texels.
extern "C" __global__
void nc_blur(const float* __restrict__ s, float* __restrict__ d, const int w, const int h, const float dx, const float dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float cx = x + 0.5f, cy = y + 0.5f;
    const float o1 = 1.3846154f, o2 = 3.2307692f, w0 = 0.2270270f, w1 = 0.3162162f, w2 = 0.0702703f;
    d[y * w + x] = w0 * s[y * w + x]
                 + w1 * (f1bl(s, cx + dx * o1, cy + dy * o1, w, h) + f1bl(s, cx - dx * o1, cy - dy * o1, w, h))
                 + w2 * (f1bl(s, cx + dx * o2, cy + dy * o2, w, h) + f1bl(s, cx - dx * o2, cy - dy * o2, w, h));
}

// Blurred luma -> field (L, dL/dx, dL/dy) per half-res texel (central differences over +-1.5 texels).
extern "C" __global__
void nc_field(const float* __restrict__ L, float4* __restrict__ F, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float cx = x + 0.5f, cy = y + 0.5f;
    const float gx = (f1bl(L, cx + 1.5f, cy, w, h) - f1bl(L, cx - 1.5f, cy, w, h)) * (1.f / 3.f);
    const float gy = (f1bl(L, cx, cy + 1.5f, w, h) - f1bl(L, cx, cy - 1.5f, w, h)) * (1.f / 3.f);
    F[y * w + x] = make_float4(L[y * w + x], gx, gy, 0.f);
}

// Full-res composite: neon tubes on the iso-lines of the blurred luma, screened over the dimmed source.
extern "C" __global__
void nc_comp(const uchar4* __restrict__ src, const float4* __restrict__ F, uchar4* __restrict__ out,
             const int W, const int H, const int w, const int h, const float* __restrict__ prm)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float sc = prm[26];
    const float4 q = f4bl(F, (x + 0.5f) * sc, (y + 0.5f) * sc, w, h);
    const float N = prm[14];
    const float g = sqrtf(q.y * q.y + q.z * q.z) + 1e-9f;          // luma slope per half-res texel
    const float gf = fmaxf(N * g * sc, 1e-7f);                       // contour units per output px
    const float f = q.x * N - prm[15];
    const float kf = floorf(f + 0.5f);                              // nearest line (wrapped index)
    const float d = fabsf(f - kf) / gf;                             // output px to that line
    const float spc = 1.f / gf;                                     // line spacing, output px

    const float wl = prm[18];
    const float body = 1.f - smooth(wl - 0.75f, wl + 0.75f, d);
    const float hz = d / fmaxf(0.45f * wl, 0.35f);
    const float hot = __expf(-hz * hz);
    const float halo = __expf(-d / prm[19]);
    // packed tighter than the tube itself -> fade to average coverage (no moire)
    const float sep = smooth(2.2f * wl + 1.f, 5.5f * wl + 3.f, spc);
    const float cov = fminf(2.f * wl / spc, 1.f);
    const float flat = smooth(prm[28], prm[29], g);                 // no slope, no contour
    const float lf = flat * prm[27];
    const float iBody = lf * (sep * body + (1.f - sep) * 0.35f * cov);
    const float iHot = lf * sep * hot;
    const float iHalo = lf * (sep * halo + (1.f - sep) * 0.25f * cov);

    const uchar4 p = src[y * W + x];
    float3 s = make_float3(p.x * (1.f / 255.f), p.y * (1.f / 255.f), p.z * (1.f / 255.f));
    s.x *= s.x; s.y *= s.y; s.z *= s.z;                             // gamma-2 linear

    float3 col = make_float3(0.f, 0.f, 0.f);
    if (iBody + iHalo + iHot > 1e-5f) {
        // height tint: the luma level this line sits at (continuous as it marches)
        const float lev = (kf + prm[15]) / fmaxf(N, 1e-3f);
        float3 ca = pal(lev * prm[13] + prm[12], prm);
        // per-line colour: identity mod 7 (kofs keeps it attached to the line through the phase wrap)
        const float kid = kf - prm[16];
        float3 cb = pal(kid * (3.f / 7.f) + prm[12], prm);
        const float cbw = prm[17];
        col = make_float3(ca.x + (cb.x - ca.x) * cbw, ca.y + (cb.y - ca.y) * cbw, ca.z + (cb.z - ca.z) * cbw);
        col.x *= col.x; col.y *= col.y; col.z *= col.z;
        float pm = fmaxf(col.x, fmaxf(col.y, col.z));
        float nr = 1.f / fmaxf(pm, 0.12f);
        col.x *= nr; col.y *= nr; col.z *= nr;
        // optional: pull the tube colour toward the (saturated) source colour under it
        const float sm = prm[23];
        if (sm > 1e-4f) {
            const float sl = lumL(s);
            float3 ss = make_float3(sl + (s.x - sl) * 1.6f, sl + (s.y - sl) * 1.6f, sl + (s.z - sl) * 1.6f);
            ss.x = fmaxf(ss.x, 0.f); ss.y = fmaxf(ss.y, 0.f); ss.z = fmaxf(ss.z, 0.f);
            const float smx = fmaxf(ss.x, fmaxf(ss.y, ss.z));
            const float sn = 1.f / fmaxf(smx, 0.05f);
            col.x += (ss.x * sn - col.x) * sm; col.y += (ss.y * sn - col.y) * sm; col.z += (ss.z * sn - col.z) * sm;
        }
    }

    // darkened, desaturated base
    const float l = lumL(s);
    const float dim = prm[24], ds = prm[25];
    s.x = (l + (s.x - l) * ds) * dim; s.y = (l + (s.y - l) * ds) * dim; s.z = (l + (s.z - l) * ds) * dim;

    const float cg = prm[20] * iBody + prm[21] * iHalo;
    const float hg = prm[22] * iHot;
    const float ax = col.x * cg + hg, ay = col.y * cg + hg, az = col.z * cg + hg;
    const float bx = 1.f - __expf(-fmaxf(ax, 0.f)), by = 1.f - __expf(-fmaxf(ay, 0.f)), bz = 1.f - __expf(-fmaxf(az, 0.f));
    s.x = s.x + bx - s.x * bx; s.y = s.y + by - s.y * by; s.z = s.z + bz - s.z * bz;
    out[y * W + x] = pack(make_float4(sqrtf(fmaxf(s.x, 0.f)) * 255.f, sqrtf(fmaxf(s.y, 0.f)) * 255.f,
                                      sqrtf(fmaxf(s.z, 0.f)) * 255.f, 255.f));
}
"""


def _sstep(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class NeonContours(Effect):
    name = "neon_contours"
    stage = "stylise"
    label = "Neon contours"
    description = ("Topographic neon: the scene's brightness contours become glowing palette-coloured tubes over a "
                   "darkened source, marching continuously up the brightness hills.")
    params = {
        **standard_effect_params(),
        **palette_params("neon", 1.5, 0.04),
        "contour_count": num(4.0, 40.0, 14.0, "Contour lines", step=0.5, basic=True),
        "line_width": num(0.0, 1.0, 0.35, "Line width"),
        "glow": num(0.0, 1.0, 0.5, "Glow"),
        "base_dim": num(0.0, 1.0, 0.7, "Darken source"),
        "flow_speed": num(-2.0, 2.0, 0.35, "Flow speed (lines per second)", unit="/s", tier="realtime", bind="rate"),
        "source_colour_mix": num(0.0, 1.0, 0.0, "Source colour in lines"),
        "colour_by": num(0.0, 1.0, 0.0, "Colour by (0 height, 1 each line)"),
        "smoothness": num(0.0, 1.0, 0.5, "Smoothness (luma blur)"),
        "pulse_kick": num(0.0, 2.0, 1.0, "Beat kick (glow, thickness, flow rush)", step=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kl = mod.get_function("nc_luma")
        self._kb = mod.get_function("nc_blur")
        self._kf = mod.get_function("nc_field")
        self._kc = mod.get_function("nc_comp")
        W, H = ctx.width, ctx.height
        self._w1, self._h1 = max(1, (W + 1) // 2), max(1, (H + 1) // 2)
        self._L = cp.zeros((self._h1, self._w1), dtype=cp.float32)
        self._T = cp.zeros((self._h1, self._w1), dtype=cp.float32)
        self._F = cp.zeros((self._h1, self._w1, 4), dtype=cp.float32)
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._prm_h = np.zeros(NPRM, dtype=np.float32)
        self._pal_name = None
        self._drift = 0.0
        self._phase = 0.0       # contour march phase in line units (wrapped mod 7 * 1000 to stay precise)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        k = min(max(p["pulse"], 0.0), 1.0)
        kick = p["pulse_kick"]
        # integrate colour drift and the contour march (beat = flow-rate kick: rush, then glide)
        self._drift = (self._drift + p["drift_speed"] * dt) % 1.0
        fs = p["flow_speed"]
        sgn = 1.0 if fs >= 0.0 else -1.0
        self._phase = (self._phase + (fs + sgn * 2.5 * kick * k) * dt) % 7000.0
        amt = min(max(p["amount"], 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _sstep(amt)
        km = k * m

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._prm_h[:12] = palette_coeffs(p["palette"])
        W, H = self.ctx.width, self.ctx.height
        w1, h1 = self._w1, self._h1
        res = H / 2160.0                                   # size knobs are in 4K px
        ph = self._phase
        fl = math.floor(ph)
        hw = (0.7 + 3.6 * p["line_width"]) * res * (1.0 + 0.6 * kick * km)
        gr = (2.0 + 26.0 * p["glow"]) * res * (1.0 + 0.7 * kick * km)
        flash = 1.0 + 0.35 * min(kick, 1.5) * km
        bd = p["base_dim"] * m

        h_ = self._prm_h
        h_[12] = self._drift
        h_[13] = p["palette_period"]
        h_[14] = p["contour_count"]
        h_[15] = ph - fl
        h_[16] = float(int(fl) % 7)
        h_[17] = p["colour_by"]
        h_[18] = max(hw, 0.3)
        h_[19] = max(gr, 0.5)
        h_[20] = 2.4 * flash
        h_[21] = (0.25 + 1.1 * p["glow"]) * flash
        h_[22] = 0.9 * flash
        h_[23] = p["source_colour_mix"]
        h_[24] = 1.0 - 0.9 * bd
        h_[25] = 1.0 - 0.7 * bd
        h_[26] = w1 / W
        h_[27] = m
        rs = 1920.0 / w1                                   # slope thresholds are per 4K half-res texel
        h_[28] = 2.0e-5 * rs
        h_[29] = 1.6e-4 * rs
        self._prm.set(h_)

        i32, f32 = np.int32, np.float32
        launch_2d(self._kl, w1, h1, (src, self._L, i32(W), i32(H), i32(w1), i32(h1)))
        sp = (0.8 + 2.2 * p["smoothness"]) * (w1 / 1920.0)
        sp = max(sp, 0.5)
        for _ in range(2):
            launch_2d(self._kb, w1, h1, (self._L, self._T, i32(w1), i32(h1), f32(sp), f32(0.0)))
            launch_2d(self._kb, w1, h1, (self._T, self._L, i32(w1), i32(h1), f32(0.0), f32(sp)))
        launch_2d(self._kf, w1, h1, (self._L, self._F, i32(w1), i32(h1)))
        launch_2d(self._kc, W, H, (src, self._F, out, i32(W), i32(H), i32(w1), i32(h1), self._prm))
