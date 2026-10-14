"""Butterfly wave: a polar 'butterfly' curve (or a hypotrochoid's ring field) scrambles the old picture into wavy,
RGB-separated bands while the new one settles out of the same ripples.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/ButterflyWaveScrawler.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/CrazyParametricFun.glsl
Author: mandubian (both shaders)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the two mandubian shaders are merged into one plug-in with a continuous
  curve_mix (0 = ButterflyWaveScrawler, 1 = CrazyParametricFun) that blends their displacement fields and their
  b-weight curves (progress vs smoothstep(0.2, 1, progress)); their shared knobs are unified -- curve_a/curve_b are
  the hypotrochoid's a/b AND the butterfly's lobe count / lobe weight (Fay's r = e^cos - 2 b cos(a th) + sin^5,
  a = 4, b = 1 is the original), waves sets both the butterfly's band count and the hypotrochoid's ring frequency
  (original amplitude 120 = 4 x waves, used here at 2 x waves so the rings do not strobe frame to frame at 60 fps),
  amplitude is a displacement depth for both (the butterfly's own sin(progress * amplitude) spread stays at its
  default 1); the hypotrochoid's 1/smoothness = 10 gain (a 900x fold that renders as sampling noise) is tamed to a
  structured fold, faded in over the first third of the progress so the old picture does not snap to chaos in one
  frame, and the new picture now also settles out of the ring field (the original showed it undisplaced);
  colour separation applies to both curves; acos arguments are clamped (NaN guard); samples wrap by mirroring
  instead of the texture clamp, so displaced bands show picture, not smeared edge pixels; uv is the
  gl-transitions 0..1, y-up convention on our y-down pixel frames; endpoints are forced on the host.)

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
from stream_service.plugins._lib.params import num

_SRC = HELPERS + r"""
__device__ __forceinline__ float mirror_wrap(float v, float n) {
    float t = fmodf(fabsf(v), 2.f * n);
    return t > n ? 2.f * n - t : t;
}
// gl-transitions uv (0..1, y up) -> our y-down pixel frame, mirrored at the borders, bilinear.
__device__ __forceinline__ float4 samp(const uchar4* __restrict__ img, float u, float v, int W, int H) {
    return bilin(img, mirror_wrap(u * W, (float)W), mirror_wrap((1.f - v) * H, (float)H), W, H);
}

extern "C" __global__
void butterfly_wave(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                    const int W, const int H,
                    const float sP, const float waves, const float lobes, const float lobe_w, const float bnorm,
                    const float fb_from, const float fb_to,      // butterfly displacement gains (old / new)
                    const float rfx, const float rfy,            // hypotrochoid ring frequencies (x, y)
                    const float fc_from, const float fc_to,      // ring displacement gains (old / new)
                    const float sep, const float wmix)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u = (x + 0.5f) / W, v = 1.f - (y + 0.5f) / H;
    // --- ButterflyWaveScrawler: compute(p, progress, centre)
    float db = 0.f;
    if (fb_from != 0.f || fb_to != 0.f) {
        const float ox = fminf(fmaxf(u * sP - 0.5f, -1.f), 1.f);      // acos guard
        const float th = acosf(ox) * waves;
        const float s = __sinf((2.f * th - 3.14159265f) * (1.f / 24.f));
        const float s2 = s * s;
        db = (__expf(__cosf(th)) - 2.f * lobe_w * __cosf(lobes * th) + s2 * s2 * s) * bnorm;
    }
    // --- CrazyParametricFun: dir * (sin(progress * dist * amp * x), sin(progress * dist * amp * y))
    float cx = 0.f, cy = 0.f;
    if (fc_from != 0.f || fc_to != 0.f) {
        const float dx = u - 0.5f, dy = v - 0.5f;
        const float dist = sqrtf(dx * dx + dy * dy);
        cx = dx * __sinf(dist * rfx);
        cy = dy * __sinf(dist * rfy);
    }
    const float ax = fb_from * db + fc_from * cx, ay = fb_from * db + fc_from * cy;   // old picture offset (uv)
    const float bx = fb_to * db + fc_to * cx, by = fb_to * db + fc_to * cy;           // new picture offset (uv)
    const float4 cr = samp(a, u + ax * (1.f - sep), v + ay * (1.f - sep), W, H);
    const float4 cg = samp(a, u + ax, v + ay, W, H);
    const float4 cb = samp(a, u + ax * (1.f + sep), v + ay * (1.f + sep), W, H);
    const float4 cn = samp(b, u + bx, v + by, W, H);
    out[y * W + x] = pack(mix4(make_float4(cr.x, cg.y, cb.z, 255.f), cn, wmix));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class ButterflyWave(Transition):
    name = "butterfly_wave"
    label = "Butterfly wave"
    description = ("Psychedelic chromatic ripples: a butterfly curve (or a hypotrochoid's rings) scrambles the old "
                   "picture into wavy, RGB-split bands as the new one settles.")
    default_duration_s = 1.0
    params = {
        "curve_mix": num(0.0, 1.0, 0.0, "Curve (butterfly -> hypotrochoid)", tier="regional", basic=True),
        "amplitude": num(0.2, 2.0, 1.0, "Amplitude", tier="regional"),
        "waves": num(5.0, 60.0, 30.0, "Waves", step=0.5, tier="regional"),
        "colour_separation": num(0.0, 0.6, 0.3, "Colour separation", tier="regional"),
        "curve_a": num(2.0, 8.0, 4.0, "Curve a (lobes)", step=0.05, tier="regional"),
        "curve_b": num(0.5, 2.5, 1.0, "Curve b (lobe weight / roller)", step=0.05, tier="regional"),
    }

    # hypotrochoid fold gain (the original's 1 / smoothness = 10 is pure sampling noise at 4K)
    _RING_GAIN = 0.06
    _RING_FREQ = 2.0     # ring frequency = _RING_FREQ * waves (original: 120 at waves 30 -> 4)

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("butterfly_wave")

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        P = float(progress)
        inv = 1.0 - P
        m = min(max(float(p["curve_mix"]), 0.0), 1.0)
        amp = float(p["amplitude"])
        A = float(p["curve_a"])
        B = max(float(p["curve_b"]), 1e-3)
        # butterfly: Fay's curve with a lobes of weight b; renormalised so b changes the shape, not the depth
        bnorm = 0.1 * 4.0 / (2.0 + 2.0 * B)
        fb_from, fb_to = (1.0 - m) * amp * P, (1.0 - m) * amp * inv
        # hypotrochoid x(t), y(t) at t = progress (CrazyParametricFun)
        k = A / B - 1.0
        hx = (A - B) * math.cos(P) + B * math.cos(P * k)
        hy = (A - B) * math.sin(P) - B * math.sin(P * k)
        F = self._RING_FREQ * float(p["waves"]) * P
        g = self._RING_GAIN * amp * m
        fc_from = g * _smooth(0.0, 0.35, P)
        fc_to = 0.6 * g * inv * inv
        wmix = (1.0 - m) * P + m * _smooth(0.2, 1.0, P)
        w, h = self.ctx.width, self.ctx.height
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h),
                   np.float32(math.sin(P)), np.float32(p["waves"]), np.float32(A), np.float32(B),
                   np.float32(bnorm), np.float32(fb_from), np.float32(fb_to),
                   np.float32(F * hx), np.float32(F * hy), np.float32(fc_from), np.float32(fc_to),
                   np.float32(p["colour_separation"]), np.float32(wmix)))
