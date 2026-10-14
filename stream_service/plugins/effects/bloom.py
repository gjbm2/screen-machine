"""Bloom: concert-haze glow.  Highlights bleed into soft, wide halos built from a mip pyramid and breathe with the beat.

Source: https://github.com/pmndrs/postprocessing/blob/main/src/effects/BloomEffect.js
        https://github.com/pmndrs/postprocessing/blob/main/src/passes/MipmapBlurPass.js
        (licence: https://github.com/pmndrs/postprocessing/blob/main/LICENSE.md)
        alt. https://github.com/mrdoob/three.js/blob/dev/examples/jsm/postprocessing/UnrealBloomPass.js (MIT)
Author: Raoul van Rueschen (pmndrs/postprocessing BloomEffect, MipmapBlurPass after Fabrice Piquet); three.js authors
License: Zlib
license_class: permissive
Ported from GLSL by smfx (ideas/changes: rewritten as CUDA kernels; the luminance pass is folded into the first
13-tap (Jimenez/CoD) downsample from 4K to half res with a Karis weight against fireflies and a soft-knee threshold;
5 further 13-tap mips to ~60 px; MipmapBlurPass-style upsample chain mix(level, tent(coarser), radius); each mip
level is tinted toward its own cosine-palette colour (pal(drift + period * level)) so tight glows and wide halos
take different hues that drift; final composite is a soft-clamped screen blend in (gamma-2) linear light.)

Realtime: amount -> bloom gain 0-1.5 with the threshold lowered by up to 0.2 (amount 0 copies src exactly);
pulse -> gain x(1 + glow_kick * pulse) and a wider radius (more of the coarse mips mixed in).
Regional: threshold, knee, radius (basic), tint_mix (0 source colour, 1 palette), saturation, glow_kick, palette.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

LEVELS = 6          # mip0 = half res (1920x1080 at 4K) + 5 further mips (down to ~60x34)
NPRM = 16           # 0..11 palette | 12 drift | 13 period | 14 tint_mix | 15 levels-1
BOOST = 3.0         # the mip average dilutes highlight energy; restore it before the soft-clamped screen blend

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lumL(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

// Bilinear sample of a float4 buffer at continuous pixel coords (pixel centres at i + 0.5), clamped.
__device__ float3 fbl(const float4* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float4 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float3(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z));
}

// One tap of the prefilter: bilinear sRGB sample -> soft-knee threshold (in sRGB luma) -> gamma-2 linear.
__device__ __forceinline__ float3 pre_tap(const uchar4* __restrict__ src, float fx, float fy, int W, int H,
                                          float thr, float knee, float* lw) {
    const float4 s = bilin(src, fx, fy, W, H);
    float3 c = make_float3(s.x * (1.f / 255.f), s.y * (1.f / 255.f), s.z * (1.f / 255.f));
    const float lp = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    const float k = fmaxf(knee, 1e-4f);
    float rq = fminf(fmaxf(lp - thr + k, 0.f), 2.f * k);
    rq = rq * rq / (4.f * k + 1e-4f);
    const float f = fmaxf(rq, lp - thr) / fmaxf(lp, 1e-4f);
    c.x *= f; c.y *= f; c.z *= f;
    c.x *= c.x; c.y *= c.y; c.z *= c.z;                     // sRGB-ish -> linear (gamma 2)
    *lw = 1.f / (1.f + lumL(c));                              // Karis weight (anti-firefly)
    return c;
}

// 13-tap (Jimenez / CoD) downsample + threshold: src (W, H) uint8 sRGB -> m0 (w, h) float linear.
extern "C" __global__
void bloom_prefilter(const uchar4* __restrict__ src, float4* __restrict__ m0, const int W, const int H,
                     const int w, const int h, const float thr, const float knee)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)W / w, sy = (float)H / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy;
    const float ox = 0.5f * sx, oy = 0.5f * sy;               // 1 source px at exact 2x
    const float tx[13] = {0.f, -1.f, 1.f, -1.f, 1.f, -2.f, 0.f, 2.f, -2.f, 2.f, -2.f, 0.f, 2.f};
    const float ty[13] = {0.f, -1.f, -1.f, 1.f, 1.f, -2.f, -2.f, -2.f, 0.f, 0.f, 2.f, 2.f, 2.f};
    const float tw[13] = {0.125f, 0.125f, 0.125f, 0.125f, 0.125f, 0.03125f, 0.0625f, 0.03125f, 0.0625f, 0.0625f,
                          0.03125f, 0.0625f, 0.03125f};
    float3 acc = make_float3(0.f, 0.f, 0.f);
    float wsum = 0.f;
    #pragma unroll
    for (int i = 0; i < 13; ++i) {
        float lw;
        const float3 c = pre_tap(src, cx + tx[i] * ox, cy + ty[i] * oy, W, H, thr, knee, &lw);
        const float wt = tw[i] * lw;
        acc.x += c.x * wt; acc.y += c.y * wt; acc.z += c.z * wt; wsum += wt;
    }
    const float inv = 1.f / fmaxf(wsum, 1e-6f);
    m0[y * w + x] = make_float4(acc.x * inv, acc.y * inv, acc.z * inv, 0.f);
}

// 13-tap downsample of a float mip (sw, sh) -> (w, h).
extern "C" __global__
void bloom_down(const float4* __restrict__ s, float4* __restrict__ d, const int sw, const int sh, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float sx = (float)sw / w, sy = (float)sh / h;
    const float cx = (x + 0.5f) * sx, cy = (y + 0.5f) * sy, ox = 0.5f * sx, oy = 0.5f * sy;
    const float3 e = fbl(s, cx, cy, sw, sh);
    const float3 a = fbl(s, cx - 2.f * ox, cy - 2.f * oy, sw, sh), b = fbl(s, cx, cy - 2.f * oy, sw, sh);
    const float3 c = fbl(s, cx + 2.f * ox, cy - 2.f * oy, sw, sh), dd = fbl(s, cx - 2.f * ox, cy, sw, sh);
    const float3 f = fbl(s, cx + 2.f * ox, cy, sw, sh), g = fbl(s, cx - 2.f * ox, cy + 2.f * oy, sw, sh);
    const float3 hh = fbl(s, cx, cy + 2.f * oy, sw, sh), i = fbl(s, cx + 2.f * ox, cy + 2.f * oy, sw, sh);
    const float3 j = fbl(s, cx - ox, cy - oy, sw, sh), k = fbl(s, cx + ox, cy - oy, sw, sh);
    const float3 l = fbl(s, cx - ox, cy + oy, sw, sh), m = fbl(s, cx + ox, cy + oy, sw, sh);
    float3 r;
    r.x = 0.125f * e.x + 0.03125f * (a.x + c.x + g.x + i.x) + 0.0625f * (b.x + dd.x + f.x + hh.x) + 0.125f * (j.x + k.x + l.x + m.x);
    r.y = 0.125f * e.y + 0.03125f * (a.y + c.y + g.y + i.y) + 0.0625f * (b.y + dd.y + f.y + hh.y) + 0.125f * (j.y + k.y + l.y + m.y);
    r.z = 0.125f * e.z + 0.03125f * (a.z + c.z + g.z + i.z) + 0.0625f * (b.z + dd.z + f.z + hh.z) + 0.125f * (j.z + k.z + l.z + m.z);
    d[y * w + x] = make_float4(r.x, r.y, r.z, 0.f);
}

// Upsample chain step (in place on level k): d = mix(tint_k(d), tent9(coarser), r).  coarse == nullptr at the top.
extern "C" __global__
void bloom_up(float4* __restrict__ d, const float4* __restrict__ cs, const int w, const int h, const int cw,
              const int ch, const int has_coarse, const float r, const float level, const float* __restrict__ prm)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float4 q = d[y * w + x];
    float3 c = make_float3(q.x, q.y, q.z);
    // tint toward this level's palette colour (tight glows and wide halos take different hues)
    const float tmix = prm[14];
    if (tmix > 0.f) {
        const float idx = prm[12] + prm[13] * 0.5f * level / fmaxf(prm[15], 1.f);
        float3 pc = pal(idx, prm);
        pc.x *= pc.x; pc.y *= pc.y; pc.z *= pc.z;
        const float pm = fmaxf(pc.x, fmaxf(pc.y, pc.z));
        const float nrm = 1.f / (0.5f * pm + 0.5f * lumL(pc) + 1e-3f);
        const float l = lumL(c) * nrm;
        c.x += (l * pc.x - c.x) * tmix; c.y += (l * pc.y - c.y) * tmix; c.z += (l * pc.z - c.z) * tmix;
    }
    if (has_coarse) {
        const float sx = (float)cw / w, sy = (float)ch / h;
        const float fx = (x + 0.5f) * sx, fy = (y + 0.5f) * sy, ox = 0.75f, oy = 0.75f;   // in coarse texels
        const float3 a = fbl(cs, fx - ox, fy - oy, cw, ch), b = fbl(cs, fx, fy - oy, cw, ch), e = fbl(cs, fx + ox, fy - oy, cw, ch);
        const float3 f = fbl(cs, fx - ox, fy, cw, ch), g = fbl(cs, fx, fy, cw, ch), hh = fbl(cs, fx + ox, fy, cw, ch);
        const float3 i = fbl(cs, fx - ox, fy + oy, cw, ch), j = fbl(cs, fx, fy + oy, cw, ch), k = fbl(cs, fx + ox, fy + oy, cw, ch);
        float3 t;
        t.x = (4.f * g.x + 2.f * (b.x + f.x + hh.x + j.x) + (a.x + e.x + i.x + k.x)) * (1.f / 16.f);
        t.y = (4.f * g.y + 2.f * (b.y + f.y + hh.y + j.y) + (a.y + e.y + i.y + k.y)) * (1.f / 16.f);
        t.z = (4.f * g.z + 2.f * (b.z + f.z + hh.z + j.z) + (a.z + e.z + i.z + k.z)) * (1.f / 16.f);
        c.x += (t.x - c.x) * r; c.y += (t.y - c.y) * r; c.z += (t.z - c.z) * r;
    }
    d[y * w + x] = make_float4(c.x, c.y, c.z, 0.f);
}

// 4K composite: soft-clamped screen blend of the (saturation-adjusted) bloom over src, in gamma-2 linear light.
extern "C" __global__
void bloom_composite(const uchar4* __restrict__ src, const float4* __restrict__ m0, uchar4* __restrict__ out,
                     const int W, const int H, const int w, const int h, const float gain, const float sat)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float3 b = fbl(m0, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    const float l = lumL(b);
    b.x = fmaxf(l + (b.x - l) * sat, 0.f); b.y = fmaxf(l + (b.y - l) * sat, 0.f); b.z = fmaxf(l + (b.z - l) * sat, 0.f);
    b.x = 1.f - __expf(-gain * b.x); b.y = 1.f - __expf(-gain * b.y); b.z = 1.f - __expf(-gain * b.z);
    const uchar4 p = src[y * W + x];
    float3 s = make_float3(p.x * (1.f / 255.f), p.y * (1.f / 255.f), p.z * (1.f / 255.f));
    s.x *= s.x; s.y *= s.y; s.z *= s.z;
    s.x = s.x + b.x - s.x * b.x; s.y = s.y + b.y - s.y * b.y; s.z = s.z + b.z - s.z * b.z;
    out[y * W + x] = pack(make_float4(sqrtf(s.x) * 255.f, sqrtf(s.y) * 255.f, sqrtf(s.z) * 255.f, 255.f));
}
"""


class Bloom(Effect):
    name = "bloom"
    stage = "stylise"
    label = "Bloom"
    description = "Concert-haze glow: highlights bleed into wide, palette-tinted halos that breathe with the beat."
    params = {
        **standard_effect_params(),
        **palette_params("dusk", 1.0, 0.04),
        "threshold": num(0.0, 1.0, 0.55, "Threshold"),
        "knee": num(0.0, 0.5, 0.2, "Soft knee"),
        "radius": num(0.0, 1.0, 0.6, "Radius (wide halos)", basic=True),
        "tint_mix": num(0.0, 1.0, 0.35, "Palette tint (0 source colour)"),
        "saturation": num(0.0, 1.5, 1.1, "Glow saturation"),
        "glow_kick": num(0.0, 2.0, 0.6, "Beat kick (gain + radius)", step=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kpre = mod.get_function("bloom_prefilter")
        self._kdown = mod.get_function("bloom_down")
        self._kup = mod.get_function("bloom_up")
        self._kcomp = mod.get_function("bloom_composite")
        self._sizes = []
        w, h = max(1, (ctx.width + 1) // 2), max(1, (ctx.height + 1) // 2)
        for _ in range(LEVELS):
            self._sizes.append((w, h))
            w, h = max(1, (w + 1) // 2), max(1, (h + 1) // 2)
        self._mips = [cp.zeros((h_, w_, 4), dtype=cp.float32) for (w_, h_) in self._sizes]
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._prm_h = np.zeros(NPRM, dtype=np.float32)
        self._pal_name = None
        self._drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        self._drift = (self._drift + p["drift_speed"] * dt) % 1.0
        amt = min(max(p["amount"], 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        k = min(max(p["pulse"], 0.0), 1.0)
        kick = p["glow_kick"]
        gain = BOOST * 1.5 * amt * (1.0 + kick * k)
        thr = max(p["threshold"] - 0.2 * amt, 0.0)
        # radius: weight of the coarse mips in the upsample mix; the beat widens it (fuller sqrt envelope)
        r = 0.3 + 0.62 * p["radius"]
        r = min(r + (0.96 - r) * min(0.5 * kick * math.sqrt(k), 1.0), 0.96)

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._prm_h[:12] = palette_coeffs(p["palette"])
        self._prm_h[12] = self._drift
        self._prm_h[13] = p["palette_period"]
        self._prm_h[14] = p["tint_mix"]
        self._prm_h[15] = LEVELS - 1
        self._prm.set(self._prm_h)

        W, H = self.ctx.width, self.ctx.height
        w0, h0 = self._sizes[0]
        launch_2d(self._kpre, w0, h0, (src, self._mips[0], np.int32(W), np.int32(H), np.int32(w0), np.int32(h0),
                                       np.float32(thr), np.float32(p["knee"])))
        for i in range(1, LEVELS):
            (sw, sh), (w, h) = self._sizes[i - 1], self._sizes[i]
            launch_2d(self._kdown, w, h, (self._mips[i - 1], self._mips[i], np.int32(sw), np.int32(sh),
                                          np.int32(w), np.int32(h)))
        for i in range(LEVELS - 1, -1, -1):
            w, h = self._sizes[i]
            top = i == LEVELS - 1
            cw, ch = self._sizes[i] if top else self._sizes[i + 1]
            coarse = self._mips[i] if top else self._mips[i + 1]
            launch_2d(self._kup, w, h, (self._mips[i], coarse, np.int32(w), np.int32(h), np.int32(cw), np.int32(ch),
                                        np.int32(0 if top else 1), np.float32(r), np.float32(i), self._prm))
        launch_2d(self._kcomp, W, H, (src, self._mips[0], out, np.int32(W), np.int32(H), np.int32(w0), np.int32(h0),
                                      np.float32(gain), np.float32(p["saturation"])))
