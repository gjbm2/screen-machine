"""Palette map: recolours any scene into the shared cosine palette so the screens match the lights.  One knob
(map_mix) runs from a gentle drifting hue rotation that keeps the source colours to a full gradient map (luma to
palette): duotone, tritone or thermal.

Source: https://github.com/Vidvox/ISF-Files/blob/master/ISF/Duotone.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Trio%20Tone.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Multi%20Hue%20Shift.fs
        https://github.com/evanw/glfx.js/blob/master/src/filters/adjust/huesaturation.js
        https://github.com/jberg/butterchurn (hue_shader)
Author: VIDVOX (ISF Duotone, Trio Tone, Multi Hue Shift); Evan Wallace (glfx.js hueSaturation);
        Jordan Berg (butterchurn hue_shader); smfx shared cosine palette (Inigo Quilez form)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: one CUDA pass in YIQ on the sRGB values, as glfx/butterchurn do.
Hue path: the source chroma (I,Q) is rotated by a host-integrated drift angle plus a shadow/highlight split
(band_offset), so source colours keep their relationships while the whole frame drifts.  Map path (Duotone /
Trio Tone generalised): luma -> contrast S-curve -> soft posterise -> index = drift + period * luma + split, coloured
by pal(); output keeps mostly the source luma (structure survives) with the palette's chroma, and keep_saturation
lets already-saturated source pixels keep their own (rotated) chroma.  The two paths blend continuously by map_mix.
A highlight "stop" pulls toward a tint colour (tint_mix; the binder can feed Maestro's rgb).  Chroma is gamut-fitted
toward the pixel luma instead of clipped per channel, so hues hold in highlights.  Palette switches and tint changes
are eased on the host (coefficients lerp over ~1 s), so nothing jumps.)

Realtime: amount -> mix with src (0 copies src exactly); pulse -> a drift-rate lurch (integrated, never a hue jump)
plus a saturation punch (<= +35% chroma); drift_speed (rate); tint_mix (level) + tint colour.
Regional: map_mix (basic), palette, palette_period, contrast, keep_saturation, band_offset, posterize, drift_kick,
sat_kick.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

# 0..11 palette | 12 drift | 13 period | 14 map_mix | 15 contrast | 16 keep_sat | 17 band_offset | 18 posterize
# 19 tint_mix | 20 tint I | 21 tint Q | 22 chroma gain (pulse punch) | 23 amount
NPRM = 24

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float yiq_y(float r, float g, float b) { return 0.299f * r + 0.587f * g + 0.114f * b; }
__device__ __forceinline__ float yiq_i(float r, float g, float b) { return 0.596f * r - 0.274f * g - 0.322f * b; }
__device__ __forceinline__ float yiq_q(float r, float g, float b) { return 0.211f * r - 0.523f * g + 0.312f * b; }

// YIQ -> RGB with the chroma scaled down (towards the grey of the same Y) until every channel fits 0..1.
__device__ float3 yiq_fit(float Y, float I, float Q) {
    Y = clamp01(Y);
    const float dr = 0.956f * I + 0.621f * Q;
    const float dg = -0.272f * I - 0.647f * Q;
    const float db = -1.106f * I + 1.703f * Q;
    float s = 1.f;
    if (dr > 1e-6f) s = fminf(s, (1.f - Y) / dr); else if (dr < -1e-6f) s = fminf(s, Y / -dr);
    if (dg > 1e-6f) s = fminf(s, (1.f - Y) / dg); else if (dg < -1e-6f) s = fminf(s, Y / -dg);
    if (db > 1e-6f) s = fminf(s, (1.f - Y) / db); else if (db < -1e-6f) s = fminf(s, Y / -db);
    s = fmaxf(s, 0.f);
    return make_float3(clamp01(Y + s * dr), clamp01(Y + s * dg), clamp01(Y + s * db));
}

extern "C" __global__
void palette_map(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                 const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s4 = src[y * W + x];
    const float r = s4.x * (1.f / 255.f), g = s4.y * (1.f / 255.f), b = s4.z * (1.f / 255.f);

    const float drift = P[12], period = P[13], mmix = P[14], contrast = P[15], keep = P[16];
    const float boff = P[17], post = P[18], tmix = P[19], cgain = P[22], amount = P[23];

    const float Ys = yiq_y(r, g, b), Is = yiq_i(r, g, b), Qs = yiq_q(r, g, b);
    const float csat = sqrtf(Is * Is + Qs * Qs);
    const float w = smooth(0.03f, 0.18f, csat);               // 0 on greys, 1 on clearly coloured pixels

    // luma: contrast S-curve (power about mid-grey; 1 = identity), then soft 4-level posterise
    const float d = 2.f * Ys - 1.f;
    float l = 0.5f + 0.5f * copysignf(__powf(fmaxf(fabsf(d), 1e-6f), 1.f / contrast), d);
    l = clamp01(l);
    {
        const float f = l * 3.f, fi = floorf(f);
        const float st = fminf((fi + smooth(0.32f, 0.68f, f - fi)) * (1.f / 3.f), 1.f);
        l += (st - l) * post;
    }

    // shadow / highlight split: shadows move by -boff, highlights by +boff, mids stay put
    const float split = boff * (smooth(0.45f, 1.f, l) - (1.f - smooth(0.f, 0.55f, l)));

    // palette colour at this luma (the gradient-map stop)
    const float3 pc = pal(drift + period * l + split, P);
    const float Yp = yiq_y(pc.x, pc.y, pc.z), Ip = yiq_i(pc.x, pc.y, pc.z), Qp = yiq_q(pc.x, pc.y, pc.z);

    // source chroma rotated by the drift + split angle
    float sn, cs;
    __sincosf(6.2831853f * (drift + split), &sn, &cs);
    const float Ir = Is * cs - Qs * sn, Qr = Is * sn + Qs * cs;

    // hue path: rotated source colours; lower keep -> saturation punch and palette tint on neutrals
    const float hsat = 1.f + 0.35f * (1.f - keep);
    const float ng = (1.f - keep) * (1.f - w) * 0.6f;
    const float Ih = Ir * hsat + Ip * ng, Qh = Qr * hsat + Qp * ng;

    // map path: palette chroma, saturated source pixels keep their own (rotated) chroma by keep
    const float kw = keep * w;
    const float Im = Ip + (Ir - Ip) * kw, Qm = Qp + (Qr - Qp) * kw;
    const float Ym = l + (Yp - l) * 0.3f;

    float Y = l + (Ym - l) * mmix;
    float I = (Ih + (Im - Ih) * mmix) * cgain;
    float Q = (Qh + (Qm - Qh) * mmix) * cgain;

    // tint stop: highlights pull toward the tint chroma (luma kept)
    const float tw = tmix * smooth(0.2f, 0.85f, Y);
    I += (P[20] - I) * tw;
    Q += (P[21] - Q) * tw;

    const float3 c = yiq_fit(Y, I, Q);
    const float rr = r + (c.x - r) * amount, gg = g + (c.y - g) * amount, bb = b + (c.z - b) * amount;
    out[y * W + x] = pack(make_float4(rr * 255.f, gg * 255.f, bb * 255.f, 255.f));
}
"""


def _hex_rgb(v) -> tuple[float, float, float]:
    try:
        s = str(v).lstrip("#")
        return int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0
    except (ValueError, IndexError):
        return 1.0, 0.69, 0.44


class PaletteMap(Effect):
    name = "palette_map"
    stage = "stylise"
    label = "Palette map"
    description = ("Recolours anything into the shared palette: from a gentle drifting hue rotation that keeps the "
                   "source colours to a full duotone / tritone / thermal gradient map.")
    params = {
        **standard_effect_params(),
        **palette_params("dusk", 0.6, 0.03),
        "map_mix": num(0.0, 1.0, 0.65, "Gradient map (0 hue rotate only)", basic=True),
        "contrast": num(0.5, 2.0, 1.2, "Contrast curve", step=0.05),
        "keep_saturation": num(0.0, 1.0, 0.35, "Keep source colour"),
        "band_offset": num(-0.5, 0.5, 0.15, "Shadow / highlight hue split"),
        "posterize": num(0.0, 1.0, 0.0, "Posterise (soft 4 bands)"),
        "drift_kick": num(0.0, 10.0, 3.0, "Beat drift lurch", step=0.1),
        "sat_kick": num(0.0, 1.0, 0.75, "Beat saturation punch"),
        "tint_mix": num(0.0, 1.0, 0.0, "Tint highlights (light colour)", tier="realtime", bind="level"),
        "tint": {"type": "string", "format": "color", "default": "#ffb070",
                 "x-ui": {"label": "Tint colour", "widget": "color"}, "x-tier": "realtime"},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("palette_map")
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._h = np.zeros(NPRM, dtype=np.float32)
        p = self.easer.values()
        self._pal = np.asarray(palette_coeffs(p["palette"]), dtype=np.float64)
        self._tint = np.asarray(_hex_rgb(p["tint"]), dtype=np.float64)
        self._drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        k = min(max(float(p["pulse"]), 0.0), 1.0)

        # integrated drift; the beat lurches the rate (also when the base rate is ~0), never the hue itself
        base = float(p["drift_speed"])
        lurch = float(p["drift_kick"]) * k
        rate = base * (1.0 + lurch) + math.copysign(0.02 * lurch, base if base != 0.0 else 1.0)
        self._drift = (self._drift + rate * dt) % 1.0

        # ease palette coefficients and tint colour (enum / colour params switch at once in the easer)
        a_pal = 1.0 - math.exp(-dt / 0.9) if dt > 0 else 0.0
        self._pal += (np.asarray(palette_coeffs(p["palette"]), dtype=np.float64) - self._pal) * a_pal
        a_t = 1.0 - math.exp(-dt / 0.35) if dt > 0 else 0.0
        self._tint += (np.asarray(_hex_rgb(p["tint"]), dtype=np.float64) - self._tint) * a_t

        amt = min(max(float(p["amount"]), 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        tr, tg, tb = self._tint
        h = self._h
        h[0:12] = self._pal
        h[12] = self._drift
        h[13] = p["palette_period"]
        h[14] = min(max(p["map_mix"], 0.0), 1.0)
        h[15] = min(max(p["contrast"], 0.5), 2.0)
        h[16] = min(max(p["keep_saturation"], 0.0), 1.0)
        h[17] = p["band_offset"]
        h[18] = min(max(p["posterize"], 0.0), 1.0)
        h[19] = min(max(p["tint_mix"], 0.0), 1.0)
        h[20] = 0.596 * tr - 0.274 * tg - 0.322 * tb
        h[21] = 0.211 * tr - 0.523 * tg + 0.312 * tb
        h[22] = 1.0 + 0.45 * float(p["sat_kick"]) * k
        h[23] = amt
        self._prm.set(h)

        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._prm))

    def close(self) -> None:
        self._prm = None
