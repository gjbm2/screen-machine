"""Kuwahara: a moving oil painting.  A generalised / anisotropic Kuwahara filter flattens any input into
brush-stroke regions with preserved edges, stretched along the image's own structure, with boosted colour,
a palette glaze and a little impasto relief on the stroke edges.

Source: https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_KuwaharaFilter.fx
        (licence: https://github.com/GarrettGunnell/AcerolaFX/blob/main/LICENSE.md)
        inspiration: https://godotshaders.com/shader/generalized-kuwahara/ (Firerabbit's Generalized Kuwahara port)
Author: Garrett Gunnell / Acerola (AcerolaFX KuwaharaFilter); Firerabbit (godotshaders Generalized Kuwahara port);
        after Kyprianidis, Kang & Doellner (anisotropic Kuwahara with polynomial sector weights)
License: MIT
license_class: permissive
Ported from HLSL (ReShade FX) by smfx (ideas/changes: rewritten as CUDA kernels running at an internal render
scale; the 8-sector polynomial weights are evaluated once on the host for a FIXED grid of taps in normalised
ellipse space (they depend only on the tap, not the pixel), so each pixel just scales/rotates that grid by its
own ellipse and bilinearly samples - brush size, anisotropy and orientation are therefore fully continuous
(no integer kernel radius, no popping while params ease) and the cost is constant; the structure tensor
(Sobel, 5x5 Gaussian at 2 px spacing, eigen-analysis) only runs when anisotropy > 0; Acerola's Unity-version
"hardness" scale on the sector variance is exposed; the sector weight 1/(1 + (h*1000*sigma^2)^(q/2)) is
evaluated in log2 space with a clamped exponent so it never overflows to 0/0; post-filter saturation boost, a
Pegtop soft-light palette glaze pal(luma * period + drift) and an impasto emboss from the filtered luma
gradient; bilinear upsample and mix back over src at 4K.)

Realtime: amount -> mix over src (full by ~0.45) plus effective brush radius (40% -> 100%); amount 0 copies src.
          pulse -> hardness drop (sectors blend, strokes soften and bloom; soften_kick) plus a small brush-size
          swell (brush_kick).  Both are amplitudes, eased by the beat envelope itself.
Regional: brush_size (basic), sharpness, hardness, anisotropy, saturation_boost, glaze_mix, impasto, palette.
Quality:  render_scale (0.25-0.5 recommended; buffers sized at setup, reallocated once if the target changes).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

GRID_N = 4            # tap grid (2N+1)^2 in normalised ellipse space; taps inside the unit disc (49) are kept
ZETA = 0.45           # polynomial sector offset (Acerola: 2 / radius, radius ~4-5)
ZERO_CROSS = 0.58     # sector overlap (Acerola default)
TAP_STRIDE = 10       # ux, uy, w0..w7 (weights pre-normalised per sector)


def _tap_table() -> np.ndarray:
    """Taps (u in the unit disc) with Acerola's 8-sector polynomial weights x gaussian, normalised per sector."""
    eta = (ZETA + math.cos(ZERO_CROSS)) / (math.sin(ZERO_CROSS) ** 2)
    rows = []
    for j in range(-GRID_N, GRID_N + 1):
        for i in range(-GRID_N, GRID_N + 1):
            ux, uy = i / GRID_N, j / GRID_N
            vx, vy = 0.5 * ux, 0.5 * uy
            if vx * vx + vy * vy > 0.25 + 1e-9:
                continue
            w = [0.0] * 8
            vxx, vyy = ZETA - eta * vx * vx, ZETA - eta * vy * vy
            w[0] = max(0.0, vy + vxx) ** 2
            w[2] = max(0.0, -vx + vyy) ** 2
            w[4] = max(0.0, -vy + vxx) ** 2
            w[6] = max(0.0, vx + vyy) ** 2
            rx, ry = math.sqrt(0.5) * (vx - vy), math.sqrt(0.5) * (vx + vy)
            vxx, vyy = ZETA - eta * rx * rx, ZETA - eta * ry * ry
            w[1] = max(0.0, ry + vxx) ** 2
            w[3] = max(0.0, -rx + vyy) ** 2
            w[5] = max(0.0, -ry + vxx) ** 2
            w[7] = max(0.0, rx + vyy) ** 2
            s = sum(w)
            if s <= 1e-9:
                continue
            g = math.exp(-3.125 * (rx * rx + ry * ry)) / s
            rows.append([ux, uy] + [wk * g for wk in w])
    t = np.asarray(rows, dtype=np.float64)
    norm = t[:, 2:].sum(axis=0)
    t[:, 2:] /= np.maximum(norm, 1e-12)
    return t.astype(np.float32)


_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

// 4K src -> internal (w, h) uchar4: 4 bilinear taps over the footprint (cheap area filter).
extern "C" __global__
void kw_down(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int W, const int H, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)W / w, sy = (float)H / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy, ox = 0.25f * sx, oy = 0.25f * sy;
    const float4 a = bilin(src, cx - ox, cy - oy, W, H), b = bilin(src, cx + ox, cy - oy, W, H);
    const float4 c = bilin(src, cx - ox, cy + oy, W, H), d = bilin(src, cx + ox, cy + oy, W, H);
    dst[y * w + x] = pack(make_float4(0.25f * (a.x + b.x + c.x + d.x), 0.25f * (a.y + b.y + c.y + d.y),
                                      0.25f * (a.z + b.z + c.z + d.z), 255.f));
}

// Structure tensor (Sobel on RGB): T = (Sx.Sx, Sy.Sy, Sx.Sy).
extern "C" __global__
void kw_tensor(const uchar4* __restrict__ img, float4* __restrict__ T, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float4 p00 = ld(img, x - 1, y - 1, w, h), p10 = ld(img, x, y - 1, w, h), p20 = ld(img, x + 1, y - 1, w, h);
    const float4 p01 = ld(img, x - 1, y, w, h),                                    p21 = ld(img, x + 1, y, w, h);
    const float4 p02 = ld(img, x - 1, y + 1, w, h), p12 = ld(img, x, y + 1, w, h), p22 = ld(img, x + 1, y + 1, w, h);
    const float s = 1.f / (4.f * 255.f);
    const float3 Sx = make_float3(((p00.x + 2.f * p01.x + p02.x) - (p20.x + 2.f * p21.x + p22.x)) * s,
                                  ((p00.y + 2.f * p01.y + p02.y) - (p20.y + 2.f * p21.y + p22.y)) * s,
                                  ((p00.z + 2.f * p01.z + p02.z) - (p20.z + 2.f * p21.z + p22.z)) * s);
    const float3 Sy = make_float3(((p00.x + 2.f * p10.x + p20.x) - (p02.x + 2.f * p12.x + p22.x)) * s,
                                  ((p00.y + 2.f * p10.y + p20.y) - (p02.y + 2.f * p12.y + p22.y)) * s,
                                  ((p00.z + 2.f * p10.z + p20.z) - (p02.z + 2.f * p12.z + p22.z)) * s);
    T[y * w + x] = make_float4(Sx.x * Sx.x + Sx.y * Sx.y + Sx.z * Sx.z, Sy.x * Sy.x + Sy.y * Sy.y + Sy.z * Sy.z,
                               Sx.x * Sy.x + Sx.y * Sy.y + Sx.z * Sy.z, 0.f);
}

// Gaussian-smoothed tensor (5x5 taps at 2 px spacing) -> F = (tx, ty, anisotropy A, 0); t = stroke direction.
extern "C" __global__
void kw_tfm(const float4* __restrict__ T, float4* __restrict__ F, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float E = 0.f, G = 0.f, Fz = 0.f, ws = 0.f;
    #pragma unroll
    for (int j = -2; j <= 2; ++j) {
        #pragma unroll
        for (int i = -2; i <= 2; ++i) {
            const int xx = min(max(x + 2 * i, 0), w - 1), yy = min(max(y + 2 * j, 0), h - 1);
            const float g = __expf(-(float)(i * i + j * j) * 0.25f);
            const float4 t = T[yy * w + xx];
            E += t.x * g; G += t.y * g; Fz += t.z * g; ws += g;
        }
    }
    E /= ws; G /= ws; Fz /= ws;
    const float disc = sqrtf(fmaxf((G - E) * (G - E) + 4.f * Fz * Fz, 0.f));
    const float l1 = 0.5f * (G + E + disc), l2 = 0.5f * (G + E - disc);
    float vx = l1 - E, vy = -Fz;
    const float len = sqrtf(vx * vx + vy * vy);
    if (len > 1e-9f) { vx /= len; vy /= len; } else { vx = 0.f; vy = 1.f; }
    const float A = (l1 + l2 > 1e-9f) ? (l1 - l2) / (l1 + l2) : 0.f;
    F[y * w + x] = make_float4(vx, vy, fminf(fmaxf(A, 0.f), 1.f), 0.f);
}

// The filter: per pixel, scale/rotate the fixed tap grid by its ellipse, accumulate 8 sector means/variances
// (weights precomputed per tap), then blend the sector means by 1 / (1 + (hard * sigma^2)^(q/2)).
extern "C" __global__
void kw_filter(const uchar4* __restrict__ img, const float4* __restrict__ F, uchar4* __restrict__ dst,
               const int w, const int h, const float* __restrict__ taps, const int ntap, const float R,
               const float aniso, const float hard, const float q)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float tx = 1.f, ty = 0.f, a = R, b = R;
    if (aniso > 0.f) {
        const float4 f = F[y * w + x];
        tx = f.x; ty = f.y;
        const float e = f.z * aniso;
        a = R * (1.f + e); b = R / (1.f + e);
    }
    float mr[8], mg[8], mb[8], sr[8], sg[8], sb[8];
    #pragma unroll
    for (int k = 0; k < 8; ++k) { mr[k] = mg[k] = mb[k] = sr[k] = sg[k] = sb[k] = 0.f; }
    const float cx = x + 0.5f, cy = y + 0.5f;
    for (int i = 0; i < ntap; ++i) {
        const float* __restrict__ T = taps + i * 10;
        const float ux = T[0] * a, uy = T[1] * b;
        const float4 c4 = bilin(img, cx + tx * ux - ty * uy, cy + ty * ux + tx * uy, w, h);
        const float r = c4.x * (1.f / 255.f), g = c4.y * (1.f / 255.f), bl = c4.z * (1.f / 255.f);
        const float rr = r * r, gg = g * g, bb = bl * bl;
        #pragma unroll
        for (int k = 0; k < 8; ++k) {
            const float wk = T[2 + k];
            mr[k] += r * wk; mg[k] += g * wk; mb[k] += bl * wk;
            sr[k] += rr * wk; sg[k] += gg * wk; sb[k] += bb * wk;
        }
    }
    float orr = 0.f, og = 0.f, ob = 0.f, ow = 0.f;
    const float hq = 0.5f * q;
    #pragma unroll
    for (int k = 0; k < 8; ++k) {
        const float s2 = fabsf(sr[k] - mr[k] * mr[k]) + fabsf(sg[k] - mg[k] * mg[k]) + fabsf(sb[k] - mb[k] * mb[k]);
        const float e = fminf(hq * __log2f(fmaxf(hard * s2, 1e-20f)), 100.f);
        const float wk = 1.f / (1.f + exp2f(e));
        orr += mr[k] * wk; og += mg[k] * wk; ob += mb[k] * wk; ow += wk;
    }
    const float inv = 255.f / fmaxf(ow, 1e-30f);
    dst[y * w + x] = pack(make_float4(orr * inv, og * inv, ob * inv, 255.f));
}

// Finish at internal res: saturation boost, soft-light palette glaze, impasto emboss on the stroke edges.
// prm: 0..11 palette | 12 drift | 13 period | 14 glaze | 15 sat gain | 16 impasto
extern "C" __global__
void kw_finish(const uchar4* __restrict__ img, uchar4* __restrict__ dst, const int w, const int h,
               const float* __restrict__ prm)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float4 p = ld(img, x, y, w, h);
    float3 c = make_float3(p.x * (1.f / 255.f), p.y * (1.f / 255.f), p.z * (1.f / 255.f));
    const float l = lum3(c);
    // saturation boost
    const float sg = prm[15];
    c.x = clamp01(l + (c.x - l) * sg); c.y = clamp01(l + (c.y - l) * sg); c.z = clamp01(l + (c.z - l) * sg);
    // palette glaze (Pegtop soft light, luma-indexed so each flat region takes one drifting hue)
    const float gm = prm[14];
    if (gm > 0.f) {
        const float3 pc = pal(l * prm[13] + prm[12], prm);
        const float3 s = make_float3((1.f - 2.f * pc.x) * c.x * c.x + 2.f * pc.x * c.x,
                                     (1.f - 2.f * pc.y) * c.y * c.y + 2.f * pc.y * c.y,
                                     (1.f - 2.f * pc.z) * c.z * c.z + 2.f * pc.z * c.z);
        c.x += (s.x - c.x) * gm; c.y += (s.y - c.y) * gm; c.z += (s.z - c.z) * gm;
    }
    // impasto: paint height ~ luma; light from the top-left
    const float im = prm[16];
    if (im > 0.f) {
        const float gx = luma(ld(img, x + 1, y, w, h)) - luma(ld(img, x - 1, y, w, h));
        const float gy = luma(ld(img, x, y + 1, w, h)) - luma(ld(img, x, y - 1, w, h));
        const float sh = 1.f + im * fminf(fmaxf((gx + gy) * 0.9f, -0.35f), 0.35f);
        c.x *= sh; c.y *= sh; c.z *= sh;
    }
    dst[y * w + x] = pack(make_float4(c.x * 255.f, c.y * 255.f, c.z * 255.f, 255.f));
}

// 4K composite: bilinear upsample of the painted buffer, mixed over src.
extern "C" __global__
void kw_comp(const uchar4* __restrict__ src, const uchar4* __restrict__ pnt, uchar4* __restrict__ out,
             const int W, const int H, const int w, const int h, const float mixv)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 s = ld(src, x, y, W, H);
    const float4 p = bilin(pnt, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    out[y * W + x] = pack(mix4(s, p, mixv));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Kuwahara(Effect):
    name = "kuwahara"
    stage = "stylise"
    label = "Oil paint"
    description = ("A moving oil painting: a generalised/anisotropic Kuwahara filter flattens any scene into "
                   "edge-preserving brush strokes with boosted colour, a drifting palette glaze and impasto relief.")
    params = {
        **standard_effect_params(),
        **palette_params("dusk", 1.0, 0.03),
        "brush_size": num(2.0, 8.0, 4.5, "Brush size", unit="px", step=0.1, tier="regional", basic=True),
        "sharpness": num(0.0, 1.0, 0.45, "Stroke sharpness", tier="regional"),
        "hardness": num(0.0, 1.0, 0.55, "Hardness (edge decisiveness)", tier="regional"),
        "anisotropy": num(0.0, 1.0, 0.7, "Anisotropy (strokes follow edges)", tier="regional"),
        "saturation_boost": num(0.0, 1.0, 0.4, "Colour boost", tier="regional"),
        "glaze_mix": num(0.0, 1.0, 0.25, "Palette glaze", tier="regional"),
        "impasto": num(0.0, 1.0, 0.35, "Impasto relief", tier="regional"),
        "soften_kick": num(0.0, 2.0, 0.8, "Beat soften (hardness drop)", step=0.05, tier="regional"),
        "brush_kick": num(0.0, 2.0, 0.5, "Beat brush swell", step=0.05, tier="regional"),
        "render_scale": num(0.25, 1.0, 0.4, "Render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kdown = mod.get_function("kw_down")
        self._ktensor = mod.get_function("kw_tensor")
        self._ktfm = mod.get_function("kw_tfm")
        self._kfilter = mod.get_function("kw_filter")
        self._kfinish = mod.get_function("kw_finish")
        self._kcomp = mod.get_function("kw_comp")
        taps = _tap_table()
        self._ntap = int(taps.shape[0])
        self._taps = cp.asarray(taps.reshape(-1))
        self._prm_h = np.zeros(17, dtype=np.float32)
        self._prm = cp.zeros(17, dtype=cp.float32)
        self._pal_name = None
        self._drift = 0.0
        self._scale = None
        self._alloc(self._target_scale())

    def _target_scale(self) -> float:
        s = self.easer.target.get("render_scale", self.params["render_scale"]["default"])
        return min(max(float(s), 0.25), 1.0)

    def _alloc(self, scale: float) -> None:
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        self._w = max(16, int(round(W * scale / 2)) * 2)
        self._h = max(16, int(round(H * scale / 2)) * 2)
        shp = (self._h, self._w, 4)
        self._a = cp.zeros(shp, dtype=cp.uint8)
        self._b = cp.zeros(shp, dtype=cp.uint8)
        self._c = cp.zeros(shp, dtype=cp.uint8)
        self._t = cp.zeros(shp, dtype=cp.float32)
        self._f = cp.zeros(shp, dtype=cp.float32)
        self._scale = scale

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        self._drift = (self._drift + p["drift_speed"] * dt) % 1000.0
        amt = min(max(float(p["amount"]), 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        ts = self._target_scale()
        if ts != self._scale:
            self._alloc(ts)

        k = min(max(float(p["pulse"]), 0.0), 1.0)
        mixv = _smooth(0.0, 0.45, amt)
        rfac = 0.4 + 0.6 * _smooth(0.0, 0.8, amt)
        R = float(p["brush_size"]) * rfac * (1.0 + 0.35 * float(p["brush_kick"]) * k)
        q = 2.0 + 14.0 * float(p["sharpness"])
        hard = 1000.0 * 10.0 ** (-0.5 + 2.5 * float(p["hardness"]))
        hard *= 2.0 ** (-3.0 * float(p["soften_kick"]) * k)        # beat: sectors blend, strokes soften/bloom
        aniso = float(p["anisotropy"])

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._prm_h[:12] = palette_coeffs(p["palette"])
        self._prm_h[12] = self._drift
        self._prm_h[13] = p["palette_period"]
        self._prm_h[14] = p["glaze_mix"]
        self._prm_h[15] = 1.0 + 0.9 * float(p["saturation_boost"])
        self._prm_h[16] = p["impasto"]
        self._prm.set(self._prm_h)

        W, H, w, h = self.ctx.width, self.ctx.height, self._w, self._h
        iw, ih = np.int32(w), np.int32(h)
        launch_2d(self._kdown, w, h, (src, self._a, np.int32(W), np.int32(H), iw, ih))
        if aniso > 1e-3:
            launch_2d(self._ktensor, w, h, (self._a, self._t, iw, ih))
            launch_2d(self._ktfm, w, h, (self._t, self._f, iw, ih))
        launch_2d(self._kfilter, w, h, (self._a, self._f, self._b, iw, ih, self._taps, np.int32(self._ntap),
                                        np.float32(R), np.float32(aniso if aniso > 1e-3 else 0.0),
                                        np.float32(hard), np.float32(q)))
        launch_2d(self._kfinish, w, h, (self._b, self._c, iw, ih, self._prm))
        launch_2d(self._kcomp, W, H, (src, self._c, out, np.int32(W), np.int32(H), iw, ih, np.float32(mixv)))
