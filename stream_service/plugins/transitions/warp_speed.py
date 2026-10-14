"""Warp speed: a hyperspace jump.  The old picture stretches into radial star streaks that grow exponentially
(a CrossZoom radial blur whose spread accelerates), with per-angle streak noise so the streaks read as discrete
lines, a blue-white shift, a slight chromatic split and a field of flying star lines; it peaks in a DreamyZoom-style
white flash at progress 0.5, then decelerates into the new picture as the streaks settle.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/CrossZoom.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/DreamyZoom.glsl
Author: rectalogic (gl-transitions CrossZoom); Zeh Fernando (DreamyZoom); streak additions by smfx
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors")
license_class: permissive
Ported from GLSL by smfx (ideas/changes: CrossZoom's 40-tap blur toward a sliding centre becomes a 16-tap radial
  streak from a fixed vanishing point (center_x, center_y), sampled from a half-res FrameMips level with a static
  per-pixel jitter, a head-biased power-2 mean so highlights read as bright lines, and per-channel tap weights
  (red at the head, blue at the tail) for the chromatic split; its sinusoidal strength becomes an exponential
  build (old picture) and decay (new picture) with per-angle noise on the streak length; DreamyZoom's rotation,
  scale and additive white flash become the spin and zoom punch knobs and a screen-blended flash that blooms from
  the vanishing point, with a short crossfade under it instead of a hard swap; new: a palette-tinted blue-white
  shift and a layer of star lines that fly outward in log-radius, both weighted by the streak envelope so the
  endpoints are pure.)

Timing: the flash peaks at progress 0.5; choose a duration of 1-2 bars so it lands on the downbeat.

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params
from stream_service.plugins._lib.sdf3d import SDF3D

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define WS_TAPS 16
#define WS_LMIN (-3.9f)          // log(r / Rmax) where stars are born (2% of the corner radius)

// One input, warped: rotate + zoom about the vanishing point (DreamyZoom), a sharp full-res sample with a small
// radial R/B split, and a 16-tap radial streak toward the centre from the half-res mip (CrossZoom), blended in as
// the streak grows.  dx, dy: pixel offset from the centre; L: streak length as a fraction of the radius.
__device__ float3 ws_image(const Tex& T, const float dx, const float dy, const float Cx, const float Cy,
                           const float cr, const float sr, const float iz, const float L, const float cs,
                           const float chroma, const float jit, const float iW, const float iH)
{
    const float qx = (cr * dx - sr * dy) * iz, qy = (sr * dx + cr * dy) * iz;
    float3 sharp = tex_sample0(T, (Cx + qx) * iW, (Cy + qy) * iH);
    if (cs > 1e-5f) {
        const float kr = 1.f - cs, kb = 1.f + cs;
        sharp.x = tex_sample0(T, (Cx + qx * kr) * iW, (Cy + qy * kr) * iH).x;
        sharp.z = tex_sample0(T, (Cx + qx * kb) * iW, (Cy + qy * kb) * iH).z;
    }
    const float lpx = L * sqrtf(qx * qx + qy * qy);
    if (lpx < 0.75f) return sharp;
    float ax = 0.f, ay = 0.f, az = 0.f, wr = 0.f, wg = 0.f, wb = 0.f;
    #pragma unroll
    for (int i = 0; i < WS_TAPS; ++i) {
        const float t = ((float)i + jit) * (1.f / WS_TAPS);
        const float k = 1.f - t * L;                              // inward from the pixel: the trail
        const float3 c = tex_rgb_at(T.t1, (Cx + qx * k) * iW, (Cy + qy * k) * iH);
        const float w = 0.2f + (1.f - t) * (1.f - t);             // head-biased (the pixel end)
        const float ch = chroma * (1.f - 2.f * t);                // red leads at the head, blue trails
        const float w_r = w * (1.f + ch), w_b = w * (1.f - ch);
        ax += w_r * c.x * c.x; ay += w * c.y * c.y; az += w_b * c.z * c.z;
        wr += w_r; wg += w; wb += w_b;
    }
    const float3 streak = make_float3(sqrtf(ax / fmaxf(wr, 1e-4f)), sqrtf(ay / fmaxf(wg, 1e-4f)),
                                      sqrtf(az / fmaxf(wb, 1e-4f)));
    const float bl = smooth(0.75f, 10.f, lpx);
    return sharp + (streak - sharp) * bl;
}

// Per-angle streak noise (0..1): hashed bins around the circle, smoothly interpolated.
__device__ __forceinline__ float ws_angle_noise(const float ang01, const int NB, const unsigned int seed) {
    const float a = ang01 * NB;
    const float fb = floorf(a);
    const int b0 = ((int)fb % NB + NB) % NB, b1 = (b0 + 1) % NB;
    float f = a - fb;
    f = f * f * (3.f - 2.f * f);
    const float n = hash1(b0, seed, 11u) * (1.f - f) + hash1(b1, seed, 11u) * f;
    return n * n;
}

// One layer of star lines: one candidate per angular bin, flying outward in log-radius.
__device__ float3 ws_stars(const float ang01, const float r, const float rn, const float lr, const int NB,
                           const unsigned int seed, const float prob, const float travel, const float Ls,
                           const float span, const float* __restrict__ P, const float pidx, const float period)
{
    const float a = ang01 * NB;
    const float fb = floorf(a);
    const int bin = ((int)fb % NB + NB) % NB;
    const float fr = a - fb;
    if (hash1(bin, seed, 1u) > prob) return make_float3(0.f, 0.f, 0.f);
    const float hs = hash1(bin, seed, 3u);                           // speed / brightness
    const float lane = 0.2f + 0.6f * hash1(bin, seed, 4u);          // where in the bin the line runs
    const float binw = 6.2831853f * r / NB;                           // bin width in pixels at this radius
    const float dpx = fabsf(fr - lane) * binw;
    const float wpx = 0.9f + 2.2f * rn;                               // lines thicken toward the edge
    float line = clamp01(1.f - dpx / wpx);
    line *= line * fminf(1.f, binw / (3.f * wpx));                    // merge into a glow near the centre
    if (line <= 0.f) return make_float3(0.f, 0.f, 0.f);
    const float spd = 0.6f + 0.8f * hs;
    const float z = travel * spd + hash1(bin, seed, 2u);
    const float lh = WS_LMIN + span * (z - floorf(z));                // head position (log radius)
    const float len = Ls * spd;
    const float f = (lr - (lh - len)) / fmaxf(len, 1e-3f);
    if (f <= 0.f || f >= 1.f) return make_float3(0.f, 0.f, 0.f);
    const float inten = f * f * line * smooth(WS_LMIN, WS_LMIN + 0.8f, lh) * (0.45f + 0.55f * hs);
    const float3 pc = pal(hash1(bin, seed, 5u) * period + pidx, P);
    const float hot = f * f * f * f;                                  // white-hot heads
    return (pc + (make_float3(1.f, 1.f, 1.f) - pc) * hot) * inten;
}

SDF_KERNEL
void warp_speed(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b),
                const float* __restrict__ P, const float* __restrict__ K, const int NB, const int NBn,
                const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    // K: 0 Cx 1 Cy 2 1/Rmax 3 wb 4 Sa 5 Sb 6 S 7 streak_len 8 chroma 9 zoom 10 spin_a 11 spin_b 12 roll
    //    13 travel 14 Ls 15 span 16 star_gain 17 star_prob 18 blue_shift 19 flash 20 drift 21 period 22 noise_amt
    const float Cx = K[0], Cy = K[1], iR = K[2], wbm = K[3], Sa = K[4], Sb = K[5], S = K[6];
    const float iW = 1.f / w, iH = 1.f / h;
    const float dx = x + 0.5f - Cx, dy = y + 0.5f - Cy;
    const float r = sqrtf(dx * dx + dy * dy), rn = r * iR;
    const float ang01 = atan2f(dy, dx) * 0.15915494f + 0.5f;
    const float jit = hash1(x, y, seed ^ 0x5bd1e995u);
    const float n = ws_angle_noise(ang01, NBn, seed);
    const float Lth = 1.f + K[22] * (0.25f + 1.25f * n - 1.f);          // per-angle streak length factor
    const float chroma = K[8];

    float3 col;
    float3 ca = make_float3(0.f, 0.f, 0.f), cb = ca;
    if (wbm < 1.f) {
        const float La = fminf(K[7] * Sa * Lth, 0.97f);
        float sr, cr; __sincosf(K[10], &sr, &cr);
        ca = ws_image(TA, dx, dy, Cx, Cy, cr, sr, __expf(-K[9] * Sa), La, chroma * 0.015f * Sa, chroma * Sa,
                      jit, iW, iH);
    }
    if (wbm > 0.f) {
        const float Lb = fminf(K[7] * Sb * Lth, 0.97f);
        float sr, cr; __sincosf(K[11], &sr, &cr);
        cb = ws_image(TB, dx, dy, Cx, Cy, cr, sr, __expf(-K[9] * Sb), Lb, chroma * 0.015f * Sb, chroma * Sb,
                      jit, iW, iH);
    }
    col = ca + (cb - ca) * wbm;

    // blue-white shift toward the palette tint (normalised so its brightest channel is 1)
    float3 tint = pal(K[20], P);
    tint = tint * (1.f / fmaxf(fmaxf(tint.x, fmaxf(tint.y, tint.z)), 0.2f));
    const float kb = K[18] * powf(S, 0.8f);
    if (kb > 0.f) {
        const float l = 0.2126f * col.x + 0.7152f * col.y + 0.0722f * col.z;
        const float3 tgt = (tint * 0.45f + make_float3(0.55f, 0.55f, 0.55f)) * (l * 1.15f + 0.04f);
        col = col + (tgt - col) * kb;
    }

    // star lines (screen blend), two interleaved layers
    if (K[16] > 0.f && r > 0.5f) {
        const float lr = __logf(fmaxf(rn, 1e-4f));
        const float ar = ang01 + K[12] - floorf(ang01 + K[12]);
        float3 st = ws_stars(ar, r, rn, lr, NB, seed, K[17], K[13], K[14], K[15], P, K[20], K[21]);
        const float ar2 = ar + 0.37f - floorf(ar + 0.37f);
        st = st + ws_stars(ar2, r, rn, lr, (NB * 3) / 5 + 7, seed + 7919u, K[17], K[13] * 1.13f, K[14] * 0.85f,
                           K[15], P, K[20] + 0.33f, K[21]) * 0.8f;
        st = st * K[16];
        col.x = 1.f - (1.f - clamp01(col.x)) * (1.f - clamp01(st.x));
        col.y = 1.f - (1.f - clamp01(col.y)) * (1.f - clamp01(st.y));
        col.z = 1.f - (1.f - clamp01(col.z)) * (1.f - clamp01(st.z));
    }

    // DreamyZoom-style flash, blooming from the vanishing point (screen blend)
    if (K[19] > 0.f) {
        const float F = clamp01(K[19] * (1.3f - 0.45f * rn));
        const float3 fc = make_float3(1.f, 1.f, 1.f) * 0.85f + tint * 0.15f;
        col.x = 1.f - (1.f - clamp01(col.x)) * (1.f - F * fc.x);
        col.y = 1.f - (1.f - clamp01(col.y)) * (1.f - F * fc.y);
        col.z = 1.f - (1.f - clamp01(col.z)) * (1.f - F * fc.z);
    }
    out[y * w + x] = make_uchar4((unsigned char)(clamp01(col.x) * 255.f + 0.5f),
                                 (unsigned char)(clamp01(col.y) * 255.f + 0.5f),
                                 (unsigned char)(clamp01(col.z) * 255.f + 0.5f), 255);
}
"""

_G = 6.0                                   # exponential build steepness (2^(G u))
_E = 2.0 ** _G - 1.0
_STAR_BASE = 0.35                          # star travel (in spans) per unit progress at rest
_STAR_GAIN = 8.0                           # extra travel per unit progress at full warp


def _ramp(u: float) -> float:
    """Exponential build 0 -> 1 over u in [0, 1] (CrossZoom's exponential ease, gentler)."""
    u = min(max(u, 0.0), 1.0)
    return (2.0 ** (_G * u) - 1.0) / _E


def _ramp_int(u: float) -> float:
    """Integral of _ramp from 0 to u."""
    u = min(max(u, 0.0), 1.0)
    return ((2.0 ** (_G * u) - 1.0) / (_G * math.log(2.0)) - u) / _E


class WarpSpeed(Transition):
    name = "warp_speed"
    label = "Warp speed"
    description = ("A hyperspace jump: the old picture stretches into radial star streaks that accelerate into a "
                   "white flash, then the new one decelerates into place as the streaks settle.")
    default_duration_s = 2.0
    params = {
        "streak_length": num(0.15, 0.95, 0.65, "Streak length", tier="regional", basic=True),
        "center_x": num(0.2, 0.8, 0.5, "Centre x", tier="regional"),
        "center_y": num(0.2, 0.8, 0.5, "Centre y", tier="regional"),
        "star_density": num(0.0, 1.0, 0.55, "Star density", tier="regional"),
        "flash": num(0.0, 1.0, 0.85, "Flash", tier="regional"),
        "chroma": num(0.0, 1.0, 0.5, "Chromatic split", tier="regional"),
        "spin": num(-30.0, 30.0, 6.0, "Spin", step=0.5, unit="°", tier="regional"),
        "zoom": num(0.0, 1.5, 0.35, "Zoom punch", tier="regional"),
        "blue_shift": num(0.0, 1.0, 0.6, "Blue-white shift", tier="regional"),
        **palette_params("ice", period=0.6, drift=0.05),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("warp_speed")
        self._mips_a = FrameMips(ctx, levels=1, blur_level=0)
        self._mips_b = FrameMips(ctx, levels=1, blur_level=0)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._K = cp.zeros(24, dtype=cp.float32)
        self._Kh = np.zeros(24, np.float32)
        self._pal_name = None

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ice")
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
        flash = float(prm["flash"])
        dens = float(prm["star_density"])
        sl = float(prm["streak_length"])

        # envelopes: old picture builds exponentially to the flash, new one decays from it
        sa = _ramp(2.0 * p)
        sb = _ramp(2.0 * (1.0 - p))
        s = min(sa, sb)
        xw = 0.03 + 0.15 * (1.0 - flash)                          # swap crossfade, hidden under the flash
        wb = min(max((p - (0.5 - xw)) / (2.0 * xw), 0.0), 1.0)
        wb = wb * wb * (3.0 - 2.0 * wb)

        # star travel = integral of (base + gain * S) over progress (closed form), in log-radius spans
        if p <= 0.5:
            j = 0.5 * _ramp_int(2.0 * p)
        else:
            j = 0.5 * _ramp_int(1.0) + 0.5 * (_ramp_int(1.0) - _ramp_int(2.0 * (1.0 - p)))
        travel = _STAR_BASE * p + _STAR_GAIN * j
        ls_max = 0.05 + 1.7 * sl
        ls = 0.05 + 1.7 * sl * s
        span = 3.9 + 0.1 + ls_max * 1.4 + 0.05               # head runs past the corner until the tail has left

        spin = math.radians(float(prm["spin"]))
        cx, cy = float(prm["center_x"]) * w, float(prm["center_y"]) * h
        rmax = max(math.hypot(x - cx, y - cy) for x in (0.0, w) for y in (0.0, h))
        fw = 0.07 + 0.06 * flash
        flash_k = flash * math.exp(-((p - 0.5) / fw) ** 2) * math.sin(math.pi * p)
        star_gain = (0.35 + 0.9 * dens) * min(max(s / 0.3, 0.0), 1.0) ** 2 * (3.0 - 2.0 * min(max(s / 0.3, 0.0), 1.0))
        if dens <= 0.0:
            star_gain = 0.0
        drift = float(prm["drift_speed"]) * p * self.duration_s
        drift -= math.floor(drift)

        K = self._Kh
        K[:] = (cx, cy, 1.0 / max(rmax, 1.0), wb, sa, sb, s, sl, float(prm["chroma"]), float(prm["zoom"]),
                spin * sa, -spin * sb, spin * (2.0 * p - 1.0) / (2.0 * math.pi), travel, ls, span,
                star_gain, 0.25 + 0.7 * dens, float(prm["blue_shift"]), flash_k, drift,
                float(prm["palette_period"]), 0.85, 0.0)
        self._K.set(K)

        nb = int(220 + 1100 * dens)
        nbn = int(90 + 260 * dens)
        if wb < 1.0:
            self._mips_a.build(a)
        if wb > 0.0:
            self._mips_b.build(b)
        launch_2d(self._k, w, h,
                  (out, np.int32(w), np.int32(h), *self._mips_a.args(a), *self._mips_b.args(b),
                   self._P, self._K, np.int32(nb), np.int32(nbn), np.uint32(int(prm["seed"]) & 0xFFFFFFFF)))
