"""Split-Tone Grade: a cinematic colour grade driven by the palette axis.  Lift, gamma and gain tints come from the
shared cosine palette at three spread indices (shadows, mids, highlights) that drift together.  One knob
(tone_mode) morphs from a subtle grade (e.g. teal shadows / warm highlights) through a duotone to a tritone
gradient map, always keeping the source luma (so local contrast and structure survive).  Grade-first, unlike
palette_map.

Source: https://github.com/Vidvox/ISF-Files/blob/master/ISF/Duotone.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Trio%20Tone.fs
        https://github.com/BradLarson/GPUImage (GPUImageFalseColorFilter)
        https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_ColorCorrection.fx
Author: zoidberg (ISF Duotone); VIDVOX (ISF Trio Tone); Brad Larson et al. (GPUImage FalseColor);
        Garrett Gunnell (AcerolaFX ColorCorrection)
License: MIT + BSD-3-Clause
license_class: permissive
Ported from GLSL by smfx (ideas/changes: one CUDA pass on the sRGB values.  Grade path = per-channel
lift / gamma / gain (ASC-CDL style) whose tints are the zero-luma chroma of three palette stops, so a stop tints
without brightening.  Duotone / Trio Tone / FalseColor become luma -> 2- or 3-stop palette gradient.  The three
paths blend continuously by tone_mode, then the colour is re-lit to the (contrast-curved) source luma with the
W3C SetLum/ClipColor pair, so hues hold in highlights and structure is untouched.  Balance shifts the tint weight
between lift and gain, and the gradient pivot.  Palette switches ease on the host; drift is host-integrated.)

Realtime: amount (m = smoothstep(amount) mixes graded over src; 0 copies src), pulse (contrast + highlight gain
punch and a highlight-tint flash, all scaled by m via the mix), drift_speed (rate).
Regional: tone_mode (basic), palette, palette_spread (basic), hue, contrast, saturation, balance, tint_strength,
punch.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

# 0..8 stop colours S,M,H | 9..17 stop chroma dS,dM,dH | 18 kS lift | 19 kM gamma | 20 kH gain
# 21 a (grade->duo) | 22 b (duo->tri) | 23 contrast | 24 balance exponent | 25 saturation | 26 luma gain (pulse)
# 27 highlight flash | 28 matte | 29 m (amount)
NPRM = 32

_SRC = HELPERS + r"""
__device__ __forceinline__ float lum3(float r, float g, float b) { return 0.2126f * r + 0.7152f * g + 0.0722f * b; }

// S-curve about mid-grey: slope c at the pivot; c = 1 is the identity.
__device__ __forceinline__ float scurve(float l, float c) {
    l = clamp01(l);
    return l < 0.5f ? 0.5f * __powf(fmaxf(2.f * l, 1e-6f), c)
                    : 1.f - 0.5f * __powf(fmaxf(2.f * (1.f - l), 1e-6f), c);
}

// one channel of lift / gamma / gain
__device__ __forceinline__ float lgg(float x, float lift, float gam, float gain) {
    float y = fmaxf(x * gain + lift * (1.f - x), 0.f);
    return __powf(fmaxf(y, 1e-6f), 1.f / gam);
}

extern "C" __global__
void split_tone_grade(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                      const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 s4 = src[y * W + x];
    const float r = s4.x * (1.f / 255.f), g = s4.y * (1.f / 255.f), b = s4.z * (1.f / 255.f);
    const float Y = lum3(r, g, b);

    const float kS = P[18], kM = P[19], kH = P[20], ta = P[21], tb = P[22];
    const float con = P[23], bexp = P[24], sat = P[25], lgain = P[26], flash = P[27], matte = P[28], m = P[29];

    // target luma: contrast S-curve, beat gain (soft, never clips white), matte lift of the blacks
    float Yt = scurve(Y, con);
    Yt = clamp01(Yt + lgain * Yt * (1.f - Yt) * 2.f);
    Yt = Yt + matte * (1.f - Yt);

    // grade path: per-channel lift / gamma / gain tinted by the zero-luma chroma of the three stops
    float gr = lgg(r, 2.f * kS * P[9],  fmaxf(0.3f, 1.f + 2.f * kM * P[12]), 1.f + 2.f * kH * P[15]);
    float gg = lgg(g, 2.f * kS * P[10], fmaxf(0.3f, 1.f + 2.f * kM * P[13]), 1.f + 2.f * kH * P[16]);
    float gb = lgg(b, 2.f * kS * P[11], fmaxf(0.3f, 1.f + 2.f * kM * P[14]), 1.f + 2.f * kH * P[17]);

    // gradient position (balance bends it toward shadows or highlights)
    const float t = __powf(fmaxf(Yt, 1e-5f), bexp);

    // duotone: shadows stop -> highlights stop
    const float dr = P[0] + (P[6] - P[0]) * t, dg = P[1] + (P[7] - P[1]) * t, db = P[2] + (P[8] - P[2]) * t;

    // tritone: shadows -> mids -> highlights
    const float ws = 1.f - smooth(0.f, 0.5f, t), wh = smooth(0.5f, 1.f, t), wm = 1.f - ws - wh;
    const float tr = P[0] * ws + P[3] * wm + P[6] * wh;
    const float tg = P[1] * ws + P[4] * wm + P[7] * wh;
    const float tbb = P[2] * ws + P[5] * wm + P[8] * wh;

    // morph grade -> duotone -> tritone
    float cr = gr + (dr - gr) * ta, cg = gg + (dg - gg) * ta, cb = gb + (db - gb) * ta;
    cr += (tr - cr) * tb; cg += (tg - cg) * tb; cb += (tbb - cb) * tb;

    // beat flash: highlights pull toward the highlight stop's chroma
    const float fw = flash * smooth(0.3f, 1.f, Yt);
    cr += fw * P[15]; cg += fw * P[16]; cb += fw * P[17];

    // saturation about the colour's own luma
    {
        const float lc = lum3(cr, cg, cb);
        cr = lc + (cr - lc) * sat; cg = lc + (cg - lc) * sat; cb = lc + (cb - lc) * sat;
    }

    // SetLum(c, Yt) + ClipColor: keep the target luma, fit chroma into gamut toward grey
    {
        const float d = Yt - lum3(cr, cg, cb);
        cr += d; cg += d; cb += d;
        const float l = lum3(cr, cg, cb);
        const float n = fminf(cr, fminf(cg, cb)), xx = fmaxf(cr, fmaxf(cg, cb));
        if (n < 0.f) {
            const float s = l / fmaxf(l - n, 1e-6f);
            cr = l + (cr - l) * s; cg = l + (cg - l) * s; cb = l + (cb - l) * s;
        }
        if (xx > 1.f) {
            const float s = (1.f - l) / fmaxf(xx - l, 1e-6f);
            cr = l + (cr - l) * s; cg = l + (cg - l) * s; cb = l + (cb - l) * s;
        }
    }

    const float o_r = r + (clamp01(cr) - r) * m, o_g = g + (clamp01(cg) - g) * m, o_b = b + (clamp01(cb) - b) * m;
    out[y * W + x] = pack(make_float4(o_r * 255.f, o_g * 255.f, o_b * 255.f, 255.f));
}
"""

_LW = np.array([0.2126, 0.7152, 0.0722], dtype=np.float64)


def _pal_host(t: float, P: np.ndarray) -> np.ndarray:
    """Host twin of PALETTE_CUDA pal(): a + b cos(2 pi (c t + d)), clamped."""
    return np.clip(P[0:3] + P[3:6] * np.cos(2.0 * math.pi * (P[6:9] * t + P[9:12])), 0.0, 1.0)


def _sstep(e0: float, e1: float, x: float) -> float:
    u = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def _palette_params() -> dict:
    d = palette_params("dusk", 1.0, 0.02)
    d.pop("palette_period", None)          # the stop spacing is palette_spread
    return d


class SplitToneGrade(Effect):
    name = "split_tone_grade"
    stage = "colour"
    label = "Split-tone grade"
    description = ("Cinematic grade from the palette: lift / gamma / gain tints for shadows, mids and highlights, "
                   "morphing from a subtle teal-orange style grade through duotone to a tritone gradient map, "
                   "keeping the picture's light and contrast.")
    tags = {"family": ["colour"], "dims": ["2d"], "technique": ["colour"]}
    params = {
        **standard_effect_params(),
        **_palette_params(),
        "tone_mode": num(0.0, 1.0, 0.3, "Tone mode (grade .. duotone .. tritone)", basic=True),
        "palette_spread": num(-0.5, 0.5, 0.2, "Palette spread (shadow / highlight hue gap)", basic=True),
        "hue": num(0.0, 1.0, 0.8, "Palette position"),
        "contrast": num(0.6, 2.0, 1.15, "Contrast", step=0.05),
        "saturation": num(0.0, 2.0, 1.1, "Saturation"),
        "balance": num(-1.0, 1.0, 0.0, "Balance (shadows .. highlights)"),
        "tint_strength": num(0.0, 1.0, 0.6, "Grade tint strength"),
        "punch": num(0.0, 2.0, 0.8, "Beat punch", step=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("split_tone_grade")
        self._prm = cp.zeros(NPRM, dtype=cp.float32)
        self._h = np.zeros(NPRM, dtype=np.float32)
        p = self.easer.values()
        self._pal = np.asarray(palette_coeffs(p["palette"]), dtype=np.float64)
        self._drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        k = min(max(float(p["pulse"]), 0.0), 1.0)

        # host-integrated drift, wrapped; palette coefficients ease (the enum itself switches at once)
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0
        a_pal = 1.0 - math.exp(-dt / 0.9) if dt > 0 else 0.0
        self._pal += (np.asarray(palette_coeffs(p["palette"]), dtype=np.float64) - self._pal) * a_pal

        amt = min(max(float(p["amount"]), 0.0), 1.0)
        if amt <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = amt * amt * (3.0 - 2.0 * amt)

        base = float(p["hue"]) + self._drift
        spread = float(p["palette_spread"])
        stops = [_pal_host(base - spread, self._pal), _pal_host(base, self._pal), _pal_host(base + spread, self._pal)]

        ts = min(max(float(p["tint_strength"]), 0.0), 1.0)
        bal = min(max(float(p["balance"]), -1.0), 1.0)
        tm = min(max(float(p["tone_mode"]), 0.0), 1.0)
        punch = max(float(p["punch"]), 0.0)
        tb = _sstep(0.5, 1.0, tm)

        h = self._h
        for i, c in enumerate(stops):
            h[3 * i:3 * i + 3] = c
            h[9 + 3 * i:12 + 3 * i] = c - float(np.dot(c, _LW))
        h[18] = 0.6 * ts * (1.0 - 0.75 * bal)
        h[19] = 0.5 * ts
        h[20] = 0.6 * ts * (1.0 + 0.75 * bal)
        h[21] = _sstep(0.0, 0.5, tm)
        h[22] = tb
        h[23] = min(max(float(p["contrast"]), 0.6), 2.0) * (1.0 + 0.35 * punch * k)
        h[24] = 2.0 ** (-1.2 * bal)
        h[25] = min(max(float(p["saturation"]), 0.0), 2.0)
        h[26] = 0.22 * punch * k
        h[27] = 0.9 * punch * k
        h[28] = 0.045 * ts * (1.0 - tb)
        h[29] = m
        self._prm.set(h)

        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._prm))

    def close(self) -> None:
        self._prm = None
