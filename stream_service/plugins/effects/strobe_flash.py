"""Strobe flash: the drop accent.  The beat envelope drives a shaped, photosensitivity-capped mix toward a white
flash, a palette-coloured flash, an invert or a solarise, optionally with a staggered RGB strobe and localised to
highlights/edges.

Source: https://github.com/Vidvox/ISF-Files/blob/master/ISF/Strobe.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/RGB%20Strobe.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Solarize.fs
        https://github.com/jberg/butterchurn/blob/master/src/rendering/shaders/comp.js
Author: VIDVOX (ISF Strobe, RGB Strobe, Solarize); Jordan Berg (butterchurn comp invert/solarize)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the ISF strobes toggle a whole frame on a timer; here the toggle is replaced
by the host beat envelope through an attack/decay shape curve, and the four looks (white, palette screen-flash,
invert, butterchurn-style 4v(1-v) solarise) sit on one continuous `mode` axis so it can ease between them; RGB Strobe's
per-channel rates become a per-channel time delay + decay exponent on the envelope (channel_stagger); a 2 px
Sobel-ish edge + highlight mask localises the flash (spatial) to keep HEVC happy; a host-side limiter allows at most
3 flash onsets per second (WCAG 2.3.1) and the kernel caps each pixel's flash luminance delta at flash_ceiling.)

Realtime: amount -> flash gain min(1, 1.5 * amount) plus a steady baseline look (amount * base_mix; 0 copies src
exactly); pulse -> the flash mix follows the beat envelope (raw, un-eased) through envelope_shape; beats also lurch
the palette drift.
Regional: mode (basic; 0 white, 0.33 palette, 0.66 invert, 1 solarise), flash_ceiling, envelope_shape,
channel_stagger, spatial, palette_index, base_mix, palette / palette_period / drift_speed.
"""
from __future__ import annotations

import collections

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NPRM = 24           # 0..11 palette | 12 index+drift | 13 period | 14 mode | 15 base mix | 16..18 flash rgb
                    # | 19 ceiling | 20 spatial
MIN_GAP_S = 0.34    # WCAG 2.3.1: at most 3 flashes per second
STAGGER_S = 0.075   # per-channel delay at channel_stagger 1 (G = 1x, B = 2x)

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float lum3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }

extern "C" __global__
void strobe_flash(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                  const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 s4 = ld(src, x, y, W, H);
    const float3 v = make_float3(s4.x * (1.f / 255.f), s4.y * (1.f / 255.f), s4.z * (1.f / 255.f));
    const float L = lum3(v);
    const float mode = P[14], mb = P[15], ceil_ = P[19], spatial = P[20];

    // Localisation mask: highlights or edges (2 px taps keep the flash grain >= 2 px for the encoder).
    float wsp = 1.f;
    if (spatial > 1e-3f) {
        const float gx = luma(ld(src, x + 2, y, W, H)) - luma(ld(src, x - 2, y, W, H));
        const float gy = luma(ld(src, x, y + 2, W, H)) - luma(ld(src, x, y - 2, W, H));
        const float edge = smooth(0.03f, 0.22f, sqrtf(gx * gx + gy * gy));
        const float hi = smooth(0.45f, 0.9f, L);
        wsp = 1.f - spatial * (1.f - fmaxf(edge, hi));
    }

    // The four looks on one continuous axis (tent weights at 0, 1/3, 2/3, 1).
    const float m = clamp01(mode) * 3.f;
    const float w0 = clamp01(1.f - fabsf(m)), w1 = clamp01(1.f - fabsf(m - 1.f));
    const float w2 = clamp01(1.f - fabsf(m - 2.f)), w3 = clamp01(1.f - fabsf(m - 3.f));
    const float3 pc = pal(P[12] + P[13] * (0.6f * L + 0.4f * (float)x / (float)W), P);
    float3 T;
    {
        // white flash
        float tx = w0, ty = w0, tz = w0;
        // palette flash: screen the palette colour over the frame, so structure survives under the colour
        tx += w1 * (1.f - (1.f - v.x) * (1.f - pc.x));
        ty += w1 * (1.f - (1.f - v.y) * (1.f - pc.y));
        tz += w1 * (1.f - (1.f - v.z) * (1.f - pc.z));
        // invert
        tx += w2 * (1.f - v.x); ty += w2 * (1.f - v.y); tz += w2 * (1.f - v.z);
        // solarise (butterchurn comp: 4v(1-v)), lightly tinted by the palette
        const float sx = 4.f * v.x * (1.f - v.x), sy = 4.f * v.y * (1.f - v.y), sz = 4.f * v.z * (1.f - v.z);
        tx += w3 * fminf(sx * (0.75f + 0.5f * pc.x), 1.f);
        ty += w3 * fminf(sy * (0.75f + 0.5f * pc.y), 1.f);
        tz += w3 * fminf(sz * (0.75f + 0.5f * pc.z), 1.f);
        T = make_float3(tx, ty, tz);
    }

    // Steady baseline look (not a flash: eases with amount), then the flash on top of it.
    const float b = mb * wsp;
    const float3 base = make_float3(v.x + (T.x - v.x) * b, v.y + (T.y - v.y) * b, v.z + (T.z - v.z) * b);
    float3 d = make_float3((T.x - base.x) * P[16] * wsp, (T.y - base.y) * P[17] * wsp, (T.z - base.z) * P[18] * wsp);

    // Photosensitivity cap on the flash component: luminance delta (and saturated-channel swing) <= ceiling.
    const float mag = fmaxf(fabsf(lum3(d)), 0.6f * fmaxf(fabsf(d.x), fmaxf(fabsf(d.y), fabsf(d.z))));
    if (mag > ceil_) {
        const float f = ceil_ / (mag + 1e-6f);
        d.x *= f; d.y *= f; d.z *= f;
    }
    out[y * W + x] = pack(make_float4((base.x + d.x) * 255.f, (base.y + d.y) * 255.f, (base.z + d.z) * 255.f, 255.f));
}
"""


class StrobeFlash(Effect):
    name = "strobe_flash"
    label = "Strobe flash"
    stage = "stylise"
    description = ("Drop accent: the beat flashes the frame white, palette-coloured, inverted or solarised, with an "
                   "optional staggered RGB strobe; capped for photosensitivity (max 3 flashes/s).")
    params = {
        **standard_effect_params(),
        "mode": num(0.0, 1.0, 0.33, "Look (white / palette / invert / solarise)", tier="regional", basic=True),
        "flash_ceiling": num(0.1, 0.6, 0.45, "Flash ceiling (max luminance jump)", tier="regional"),
        "envelope_shape": num(0.5, 3.0, 1.4, "Envelope shape (<1 full, >1 punchy)", step=0.05, tier="regional"),
        "channel_stagger": num(0.0, 1.0, 0.35, "RGB stagger", tier="regional"),
        "spatial": num(0.0, 1.0, 0.3, "Localise (0 full frame, 1 highlights/edges)", tier="regional"),
        "palette_index": num(0.0, 1.0, 0.0, "Palette position", tier="regional"),
        "base_mix": num(0.0, 0.5, 0.15, "Baseline look between beats", tier="regional"),
        **palette_params("neon", period=0.6, drift=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("strobe_flash")
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._prm_h = np.zeros(NPRM, dtype=np.float32)
        self._pal_name = None
        self._reset()

    def _reset(self) -> None:
        self._drift = 0.0
        self._clock = 0.0
        self._env = 0.0            # limited envelope
        self._prev_k = 0.0
        self._rising = False       # inside an accepted rising stroke
        self._last_fire = -1e9
        self._hist: collections.deque = collections.deque(maxlen=64)

    def _limited_envelope(self, k: float) -> float:
        """Follow the beat envelope, but let a NEW rise start only >= MIN_GAP_S after the last accepted onset."""
        if k > self._env + 1e-4:
            new_stroke = not self._rising and k > self._prev_k + 0.02
            if self._rising:
                self._env = k
            elif new_stroke and self._clock - self._last_fire >= MIN_GAP_S:
                self._rising = True
                self._last_fire = self._clock
                self._env = k
            # else: blocked (too soon after the last flash, or jitter); hold, then decay with the source below
        else:
            self._env = k
            self._rising = False
        if k < self._prev_k - 1e-4:
            self._rising = False
        self._prev_k = k
        return self._env

    def _delayed(self, delay: float) -> float:
        if delay <= 1e-4 or not self._hist:
            return self._env
        t = self._clock - delay
        prev = None
        for (tc, e) in reversed(self._hist):
            if tc <= t:
                if prev is None:
                    return e
                t1, e1 = prev
                f = (t - tc) / max(t1 - tc, 1e-6)
                return e + (e1 - e) * f
            prev = (tc, e)
        return self._hist[0][1]

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        amount = float(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            self._reset()
            return
        dt = max(float(dt), 0.0)
        self._clock += dt
        # The flash follows the beat envelope directly (the easer's 0.3 s lag would smear it).
        k = float(self.easer.target.get("pulse", p["pulse"])) if self.easer is not None else float(p["pulse"])
        k = min(max(k, 0.0), 1.0)
        env = self._limited_envelope(k)
        self._hist.append((self._clock, env))

        shape = float(p["envelope_shape"])
        stag = float(p["channel_stagger"])
        gain = min(1.0, 1.5 * amount)
        flash = []
        for c in range(3):
            e = self._delayed(c * stag * STAGGER_S)
            flash.append(gain * (max(e, 0.0) ** (shape * (1.0 + 0.8 * c * stag))))

        # Colour drift lurches forward on the beat (rate kick, never a hue jump).
        self._drift = (self._drift + p["drift_speed"] * (1.0 + 2.0 * env) * dt) % 1.0

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._prm_h[:12] = palette_coeffs(p["palette"])
        self._prm_h[12] = (p["palette_index"] + self._drift) % 1.0
        self._prm_h[13] = p["palette_period"]
        self._prm_h[14] = p["mode"]
        self._prm_h[15] = amount * p["base_mix"]
        self._prm_h[16:19] = flash
        self._prm_h[19] = p["flash_ceiling"]
        self._prm_h[20] = p["spatial"]
        self._prm.set(self._prm_h)

        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._prm))
