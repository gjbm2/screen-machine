"""Revolve: a spin-through vortex.  The frame twists (harder at the core), zooms in and barrel-bends under
temporal motion blur; the scenes swap inside the spin, an optional palette-tinted white-out flash lands at the
peak (progress 0.5), and the new picture unwinds back to rest.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Revolve_Left.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Swirl.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/DreamyZoom.glsl
Author: bread (Revolve_Left); Sergey Kosarevsky (Swirl, http://www.linderdaum.com, ported to gl-transitions by gre);
        Zeh Fernando (DreamyZoom: the zoom-rotate-into-a-flash idea)
License: MIT   (each file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Revolve_Left is the core -- its envelope, edge spin + core twist profile
  pow(1 - r/0.96, 1.55), zoom 1 + (peak - 1) e^0.85, barrel 1 + barrel e rr^2 2.8, switch window, edge shadow and
  (1 - |x|)^2 weighted temporal taps are kept; Swirl.glsl's radial core twist is the same idea and is what the
  `swirl` knob scales; DreamyZoom's additive white-out at the midpoint becomes the `flash` knob, here a screen
  blend whose colour is the shared cosine palette (white at the vortex core, tinted spiral arms outward) with a
  radial falloff so the whole frame never slams to flat white.  Changes: the envelope is made symmetric about
  progress 0.5 (rise 0.03->0.5, fall 0.5->0.97, original 0.10->0.43->0.72) and the switch window is centred on 0.5
  (0.40->0.60, original 0.30->0.50) so a binder can land the swap + flash peak on a downbeat; 17 temporal taps
  become 7 (or 1 when the blur span is ~0: the span is gated by smoothstep(0.06, 0.25, e) so the tap count never
  pops), and both images are sampled only for taps inside the switch window; per-tap envelope values are
  computed on the host; out-of-frame samples mirror-repeat instead of clamping (no smeared edge streaks when a
  16:9 frame spins); `direction` is continuous: its sign is the spin sense (-1 = the original's default) and its
  magnitude scales the spin from 35 % to 100 %, so no other knob is ever dead; frames are y-down pixel arrays
  (the uv math is done in height-normalised y-up coordinates so the spin sense matches the GLSL); endpoints are
  forced (progress <= 0 copies a, >= 1 copies b).)

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

Binder: style wild, 2 beats, starting on a downbeat with the peak (progress 0.5) on the next downbeat-ish beat;
linear easing keeps the peak exactly at mid-duration (any symmetric easing does too).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

NT = 7          # temporal taps when the blur is on
TS = 8          # floats per tap record: weight, edge angle, core angle, 1/scale, barrel k, reveal, pad, pad

_SRC = HELPERS + PALETTE_CUDA + r"""
// Reflect v into [0, n) (period 2n): spun-out corners show a mirrored continuation, not a smeared edge.
__device__ __forceinline__ float mirror_c(float v, float n) {
    const float p = 2.f * n;
    v -= p * floorf(v / p);
    return v < n ? v : p - v;
}
__device__ __forceinline__ float4 bilin_m(const uchar4* __restrict__ img, float fx, float fy, int W, int H) {
    return bilin(img, mirror_c(fx, (float)W), mirror_c(fy, (float)H), W, H);
}

extern "C" __global__
void revolve(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const int W, const int H, const float* __restrict__ T, const int nt,
             const float cx, const float cy, const float shade, const float flash, const float fspin,
             const float drift, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fH = (float)H, invH = 1.f / fH;
    // height-normalised, y-up offset from the swirl centre (Revolve_Left's p = (uv - center) * (ratio, 1))
    const float px = (x + 0.5f - cx) * invH, py = (cy - (y + 0.5f)) * invH;
    const float r2 = px * px + py * py, r = sqrtf(r2);
    const float prof = __powf(fmaxf(1.f - r * (1.f / 0.96f), 0.f), 1.55f);   // core twist profile
    float ax = 0.f, ay = 0.f, az = 0.f;
    for (int i = 0; i < nt; ++i) {
        const float* t = T + i * 8;
        float s, c;
        __sincosf(-(t[1] + t[2] * prof), &s, &c);                           // rotate2(p, -visibleAngle)
        const float is = t[3];
        const float k = is * (1.f + t[4] * r2 * is * is);                    // zoom, then barrel on |p / sc|
        const float qx = (c * px - s * py) * k, qy = (s * px + c * py) * k;
        const float sx = cx + qx * fH, sy = cy - qy * fH;
        const float rv = t[5];
        float4 col;
        if (rv <= 0.f) col = bilin_m(a, sx, sy, W, H);
        else if (rv >= 1.f) col = bilin_m(b, sx, sy, W, H);
        else col = mix4(bilin_m(a, sx, sy, W, H), bilin_m(b, sx, sy, W, H), rv);
        const float w = t[0];
        ax += col.x * w; ay += col.y * w; az += col.z * w;
    }
    // edge shadow about the frame centre (Revolve_Left's vignette)
    const float vx = (x + 0.5f - 0.5f * W) * invH, vy = (y + 0.5f - 0.5f * fH) * invH;
    const float vg = 1.f - shade * smooth(0.35f, 0.95f, sqrtf(vx * vx + vy * vy));
    ax *= vg; ay *= vg; az *= vg;
    if (flash > 0.f) {
        // palette-tinted white-out: white at the vortex core, tinted spiral arms outward, radial falloff
        const float ir2 = 1.f / fmaxf(r2, 1e-8f);
        const float c2 = (px * px - py * py) * ir2, s2 = 2.f * px * py * ir2;   // cos/sin of 2 theta
        float sk, ck;
        __sincosf(fspin * r, &sk, &ck);
        const float arm = c2 * ck - s2 * sk;                                    // cos(2 theta + fspin r)
        const float3 pc = pal(0.42f * r + 0.10f * arm + drift, P);
        const float wc = 0.35f + 0.65f * (1.f - smooth(0.f, 0.6f, r));          // whiteness: core -> rim
        const float f = flash * (1.f - 0.45f * smooth(0.1f, 1.f, r));
        const float fr = f * (pc.x + (1.f - pc.x) * wc);
        const float fg = f * (pc.y + (1.f - pc.y) * wc);
        const float fb = f * (pc.z + (1.f - pc.z) * wc);
        ax = 255.f - (255.f - ax) * (1.f - fr);                                 // screen blend
        ay = 255.f - (255.f - ay) * (1.f - fg);
        az = 255.f - (255.f - az) * (1.f - fb);
    }
    out[y * W + x] = pack(make_float4(ax, ay, az, 255.f));
}
"""


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _envelope(t: float) -> float:
    """Revolve_Left's rise * fall, made symmetric about 0.5 (peak 1 at progress 0.5, 0 outside 0.03..0.97)."""
    return _ease((t - 0.03) / 0.47) * (1.0 - _ease((t - 0.5) / 0.47))


def _flash_env(t: float) -> float:
    """Flash envelope: an accelerating build into progress 0.5 and a slower release after it."""
    u = (t - 0.5) / (0.16 if t < 0.5 else 0.24)
    v = max(0.0, 1.0 - u * u)
    return v * v * (v if t < 0.5 else 1.0)


class Revolve(Transition):
    name = "revolve"
    label = "Revolve"
    description = ("A spin-through vortex: the frame twists hardest at the core, zooms and barrel-bends under motion "
                   "blur, swaps scenes inside the spin with an optional tinted flash, then unwinds.")
    default_duration_s = 1.0
    params = {
        "max_rotation": num(0.0, 3.0, 1.95, "Spin (whole frame)", unit="rad", tier="regional"),
        "swirl": num(0.0, 4.0, 2.85, "Core twist", tier="regional", basic=True),
        "peak_zoom": num(1.0, 3.0, 2.22, "Peak zoom", unit="×", tier="regional"),
        "barrel": num(0.0, 0.6, 0.38, "Barrel bend", tier="regional"),
        "motion_blur": num(0.0, 1.0, 1.0, "Motion blur", tier="regional"),
        "direction": num(-1.0, 1.0, -1.0, "Direction (sign = sense, size = spin)", tier="regional"),
        "flash": num(0.0, 1.0, 0.55, "Peak flash", tier="regional", basic=True),
        "palette": enum(list(PALETTES), "neon", "Flash palette", tier="regional"),
        "center_x": num(0.0, 1.0, 0.46, "Centre x", tier="regional"),
        "center_y": num(0.0, 1.0, 0.48, "Centre y", tier="regional"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("revolve")
        self._T = cp.zeros(NT * TS, cp.float32)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        self._sync_palette()
        self._host = np.zeros(NT * TS, np.float32)
        xs = (np.arange(NT) - (NT - 1) / 2) / ((NT - 1) / 2 + 0.5)          # tap offsets in (-1, 1)
        self._xs = xs
        self._ws = (1.0 - np.abs(xs)) ** 2 + 0.02                             # Revolve_Left's (1 - |x|)^2 weights

    def _sync_palette(self) -> None:
        name = self.params["palette"]
        if name != self._pal:                                                 # regional: follow palette changes
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def _tap(self, t: float, sgn: float, p: dict) -> tuple:
        e = _envelope(t)
        sc = 1.0 + (p["peak_zoom"] - 1.0) * e ** 0.85
        reveal = _ease((t - 0.40) / 0.20)
        return (sgn * p["max_rotation"] * e, sgn * p["swirl"] * e, 1.0 / sc, p["barrel"] * e * 2.8, reveal)

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        t = float(progress)
        d = float(p["direction"])
        sgn = math.copysign(0.35 + 0.65 * abs(d), d if d != 0.0 else 1.0)
        e = _envelope(t)
        span = 0.06 * float(p["motion_blur"]) * e * _ease((e - 0.06) / 0.19)   # taps fade in with the envelope
        H = self._host
        H[:] = 0.0
        if span * (abs(p["max_rotation"]) + p["swirl"] + p["peak_zoom"]) < 2e-4:
            nt = 1
            H[0] = 1.0
            H[1:6] = self._tap(t, sgn, p)
        else:
            nt = NT
            wsum = float(self._ws.sum())
            for i in range(NT):
                ti = min(max(t + float(self._xs[i]) * span, 0.0), 1.0)
                H[i * TS] = self._ws[i] / wsum
                H[i * TS + 1:i * TS + 6] = self._tap(ti, sgn, p)
        self._T.set(H)
        self._sync_palette()
        fl = float(p["flash"]) * _flash_env(t)
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), self._T, np.int32(nt),
                   np.float32(p["center_x"] * w), np.float32(p["center_y"] * h), np.float32(0.16 * e),
                   np.float32(min(fl, 1.0)), np.float32(-6.0 * sgn),
                   np.float32(0.35 * t), self._P))                                # palette drifts through the spin
