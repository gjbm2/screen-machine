"""Afterimage trails: long exposure, light painting and ghosting in a 16-bit history buffer (in place, no zoom).

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/AfterimageShader.js
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Long%20Exposure.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Comet%20Tails.fs
Author: three.js authors (AfterimageShader, inspired by Bruno Imbrizi); VIDVOX (ISF Long Exposure, Comet Tails, Ghosting)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: AfterimageShader's ``max(new, old * damp * when_gt(old, 0.1))`` is one end
of a continuous blend axis whose other end is the exponential average of ISF Long Exposure / Ghosting
(``mix(new, old, k)``); Comet Tails' idea that bright history discharges slowly and dim history quickly becomes a
soft brightness gate between two persistences; damp is frame-rate independent (d ** (dt * 60)); the history is a
16-bit unorm buffer so dim tails fade smoothly to true black instead of stalling on 8-bit steps; new: the trail
(history minus the live picture) hue-rotates a little every frame for rainbow tails and is tinted through the
shared cosine palette indexed by its own brightness plus a drifting phase, so heads and tails take different
palette colours; tint and hue drift only touch the visible trail, so a still image passes through unchanged).

Realtime: amount 0 -> out == src exactly (the history is reset to src on re-entry, so a fade-in starts clean);
amount scales persistence and trail weight through smoothstep.  pulse spikes persistence and the trail gain (on
the output only, so the history never runs away).  dt == 0 -> no decay and no accumulation; dt > 0.5 s -> reset.
On a content cut the old trails simply fade; the stored history is clamped to [0, 1].
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

TAU = 2.0 * math.pi
NP = 36
# P layout (floats)
#  0 k (per-step persistence, bright) | 1 k_dim (dim persistence) | 2 threshold | 3 blend (0 max .. 1 average)
#  4 trail gain | 5 tint | 6 palette period | 7 drift | 8 hue weight | 9..17 hue rotation 3x3
#  18 reset flag | 19 write flag | 20..31 palette coeffs
_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float mx3(float3 c) { return fmaxf(fmaxf(c.x, c.y), c.z); }

extern "C" __global__
void afterimage_ghost(const uchar4* __restrict__ src, ushort4* __restrict__ hist, uchar4* __restrict__ out,
                      const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const uchar4 sp = src[i];
    const float3 s = make_float3(sp.x * (1.f / 255.f), sp.y * (1.f / 255.f), sp.z * (1.f / 255.f));
    float3 h;
    if (P[18] > 0.5f) {
        h = s;
    } else {
        const ushort4 hp = hist[i];
        h = make_float3(hp.x * (1.f / 65535.f), hp.y * (1.f / 65535.f), hp.z * (1.f / 65535.f));
    }

    // hue-rotate only where the history differs from the live picture (the trail), so stills stay true
    const float dif = fmaxf(fmaxf(fabsf(h.x - s.x), fabsf(h.y - s.y)), fabsf(h.z - s.z));
    const float wh = P[8] * smooth(0.015f, 0.12f, dif);
    if (wh > 0.f) {
        const float3 r = make_float3(P[9] * h.x + P[10] * h.y + P[11] * h.z,
                                     P[12] * h.x + P[13] * h.y + P[14] * h.z,
                                     P[15] * h.x + P[16] * h.y + P[17] * h.z);
        h.x = fmaxf(h.x + (r.x - h.x) * wh, 0.f);
        h.y = fmaxf(h.y + (r.y - h.y) * wh, 0.f);
        h.z = fmaxf(h.z + (r.z - h.z) * wh, 0.f);
    }

    // brightness-gated persistence (Comet Tails): bright history discharges slowly, dim history quickly
    const float lh = mx3(h);
    const float g = smooth(P[2] - 0.1f, P[2] + 0.05f, lh);
    const float k = P[1] + (P[0] - P[1]) * g;

    // blend axis: 0 = max(src, hist * k) (comet trails / light painting), 1 = mix(src, hist, k) (ghosting)
    const float b = P[3];
    float3 n;
    n.x = fmaxf(s.x, h.x * k); n.x += (s.x + (h.x - s.x) * k - n.x) * b;
    n.y = fmaxf(s.y, h.y * k); n.y += (s.y + (h.y - s.y) * k - n.y) * b;
    n.z = fmaxf(s.z, h.z * k); n.z += (s.z + (h.z - s.z) * k - n.z) * b;
    n.x = clamp01(n.x); n.y = clamp01(n.y); n.z = clamp01(n.z);

    if (P[19] > 0.5f)
        hist[i] = make_ushort4((unsigned short)(n.x * 65535.f + 0.5f), (unsigned short)(n.y * 65535.f + 0.5f),
                               (unsigned short)(n.z * 65535.f + 0.5f), 65535);

    // visible trail = what the history adds over the live picture; tint + gain the bright part only
    const float3 pos = make_float3(fmaxf(n.x - s.x, 0.f), fmaxf(n.y - s.y, 0.f), fmaxf(n.z - s.z, 0.f));
    const float3 neg = make_float3(fminf(n.x - s.x, 0.f), fminf(n.y - s.y, 0.f), fminf(n.z - s.z, 0.f));
    const float V = mx3(pos);
    const float3 pc = pal(lh * P[6] + P[7], P + 20);
    const float pm = V / fmaxf(mx3(pc), 0.05f);
    const float tn = P[5], gn = P[4];
    const float tx = (pos.x + (pc.x * pm - pos.x) * tn) * gn;
    const float ty = (pos.y + (pc.y * pm - pos.y) * tn) * gn;
    const float tz = (pos.z + (pc.z * pm - pos.z) * tn) * gn;
    // the excess beyond the plain trail (gain, tint) is screen-added so bright stills do not clip
    const float bx = s.x + neg.x + fminf(tx, pos.x), by = s.y + neg.y + fminf(ty, pos.y), bz = s.z + neg.z + fminf(tz, pos.z);
    const float ex = fmaxf(tx - pos.x, 0.f), ey = fmaxf(ty - pos.y, 0.f), ez = fmaxf(tz - pos.z, 0.f);
    out[i] = pack(make_float4(255.f * (1.f - (1.f - clamp01(bx)) * (1.f - clamp01(ex))),
                              255.f * (1.f - (1.f - clamp01(by)) * (1.f - clamp01(ey))),
                              255.f * (1.f - (1.f - clamp01(bz)) * (1.f - clamp01(ez))), 255.f));
}
"""


def _hue_matrix(angle: float) -> list[float]:
    c, s = math.cos(angle), math.sin(angle)
    t, r = (1.0 - c) / 3.0, math.sqrt(1.0 / 3.0) * s
    return [c + t, t - r, t + r,
            t + r, c + t, t - r,
            t - r, t + r, c + t]


def _ss(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class AfterimageGhost(Effect):
    name = "afterimage_ghost"
    stage = "feedback"
    label = "Afterimage trails"
    description = ("Long-exposure light painting and ghosting: bright things leave comet tails or soft ghosts "
                   "in place, which fade, hue-rotate into rainbow tails and take on palette colour.")
    _pal = palette_params("neon", 1.4, 0.05)
    params = {
        **standard_effect_params(),
        "persistence": num(0.0, 1.0, 0.65, "Trail length", tier="regional", basic=True),
        "bright_threshold": num(0.0, 0.8, 0.12, "Brightness gate", tier="regional"),
        "blend": num(0.0, 1.0, 0.15, "Blend (comet max / ghost average)", tier="regional"),
        "trail_hue_drift": num(-0.5, 0.5, 0.12, "Trail hue drift", unit="turns/s", step=0.005, tier="regional"),
        "trail_tint": num(0.0, 1.0, 0.45, "Trail palette tint", tier="regional"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat kick (persistence + glow)", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": _pal["palette_period"],
        "drift_speed": _pal["drift_speed"],
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("afterimage_ghost")
        W, H = ctx.width, ctx.height
        self.hist = cp.zeros((H, W, 4), cp.uint16)
        self.need_reset = True
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.drift = 0.0
        self.pal_name = None
        self.coeffs = palette_coeffs("neon")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(float(p["amount"]), 0.0), 1.0)
        kp = min(max(float(p["pulse"]), 0.0), 1.0)
        dt = max(float(dt), 0.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * min(dt, 0.1), 100.0)

        if a <= 1e-4:
            cp.copyto(out, src)
            self.need_reset = True
            return
        if dt > 0.5:
            self.need_reset = True

        m = _ss(a)
        kick = float(p["beat_kick"]) * kp * m
        dt60 = min(dt * 60.0, 6.0)
        step = dt60 if dt60 > 0.0 else 1.0       # dt == 0: show the current blend, but neither decay nor write
        # persistence: half-life 1.5 .. 90 frames at 60 fps; amount lengthens it, the beat spikes it
        half = 1.5 * 60.0 ** float(p["persistence"])
        half *= (0.15 + 0.85 * m) * (1.0 + 1.2 * kick)
        d = min(0.5 ** (1.0 / max(half, 0.3)), 0.995)
        w = min(1.0, 3.0 * m)
        k = (d ** step) * w
        k_dim = (d ** (4.0 * step)) * w * 0.85
        gain = 1.0 + 0.9 * kick

        if p["palette"] != self.pal_name:
            self.pal_name = p["palette"]
            self.coeffs = palette_coeffs(self.pal_name)
        h = self.host
        h[0], h[1] = k, k_dim
        h[2] = float(p["bright_threshold"])
        h[3] = float(p["blend"])
        h[4] = gain
        h[5] = float(p["trail_tint"]) * m
        h[6] = float(p["palette_period"])
        h[7] = self.drift
        h[8] = m
        h[9:18] = _hue_matrix(TAU * float(p["trail_hue_drift"]) * min(dt, 0.1))
        h[18] = 1.0 if self.need_reset else 0.0
        h[19] = 1.0 if (dt > 0.0 or self.need_reset) else 0.0
        h[20:32] = self.coeffs
        self.P.set(h)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (src, self.hist, out, np.int32(W), np.int32(H), self.P))
        self.need_reset = False

    def close(self) -> None:
        self.hist = None
