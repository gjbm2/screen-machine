"""Trauma shake: Eiserloh's trauma camera shake.  Beats add trauma, trauma decays, the picture shakes by trauma^2.

Source: https://github.com/bevyengine/bevy/blob/main/examples/camera/2d_screen_shake.rs
        https://www.youtube.com/watch?v=tu-Qe66AvtY (Squirrel Eiserloh, "Math for Game Programmers: Juicing Your
        Cameras With Math", GDC 2016)
        https://godotshaders.com/shader/screen-shake/
        https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Shake.fs
Author: Bevy contributors (2d_screen_shake.rs, after Squirrel Eiserloh, GDC 2016); Near (godotshaders Screen Shake);
        VIDVOX (ISF Shake)
License: MIT OR Apache-2.0 (Bevy, taken as MIT) + MIT
license_class: permissive
Ported from Rust/GLSL by smfx (ideas/changes: the Bevy example's trauma model (trauma decays, shake = trauma^2 x
smooth noise on x, y and roll) moved to a host-side state machine driven by the beat envelope instead of key
presses; trauma decays exponentially and is floored by a constant idle trauma; the noise is a 3-octave 1D gradient
fbm of an *integrated* noise time (rate = frequency, a little faster when trauma is high), never per-frame white
noise as in the ISF original; a horizontal/vertical axis bias; a static overscan zoom that grows with amount so the
edges stay covered (mirror addressing hides the rare extreme); an optional 4-tap motion blur that interpolates
between last frame's and this frame's camera transform, so roll blurs along its arc as well).

Realtime mapping: trauma = max(trauma * exp(-decay * dt), pulse, idle_trauma).  Shake scale s = m * trauma^2 with
m = smoothstep(amount), so amount 0 is an exact copy (early out) and the 0->1 fade is a smooth morph from still to
full shake.  dt = 0 freezes the motion (and the blur collapses to one sharp tap).  One 4K pass, 1 bilinear tap
(4 with motion blur).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

TAPS = 4
_SRC = HELPERS + r"""
// Reflect a continuous pixel coordinate into [0, n] (hides the rare frame where the shake outruns the overscan).
__device__ __forceinline__ float mirror_c(float v, float n) {
    const float p = 2.f * n;
    v = fmodf(fabsf(v), p);
    return v > n ? p - v : v;
}

// P: TAPS affine camera transforms (a, b, c, d, tx, ty) mapping an output offset from the centre to a source offset.
extern "C" __global__
void trauma_shake(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                  const float* __restrict__ P, const int taps)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
    float r = 0.f, g = 0.f, b = 0.f;
#pragma unroll
    for (int k = 0; k < 4; ++k) {
        if (k < taps) {
            const float* T = P + 6 * k;
            const float sx = mirror_c(cx + T[0] * dx + T[1] * dy + T[4], (float)W);
            const float sy = mirror_c(cy + T[2] * dx + T[3] * dy + T[5], (float)H);
            const float4 c = bilin(src, sx, sy, W, H);
            r += c.x; g += c.y; b += c.z;
        }
    }
    const float inv = 1.f / (float)taps;
    out[y * W + x] = pack(make_float4(r * inv, g * inv, b * inv, 255.f));
}
"""

_OCT = ((1.0, 1.0), (2.03, 0.5), (4.11, 0.25))
_NORM = 1.0 / sum(a for _, a in _OCT)
_SOFT = 1.0 / math.tanh(1.8)


def _smoothstep01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class TraumaShake(Effect):
    name = "trauma_shake"
    stage = "motion"
    label = "Camera shake"
    description = ("Eiserloh trauma camera shake: beats add trauma that decays; the picture shakes by trauma squared "
                   "(smooth x/y/roll noise) with overscan and optional motion blur.")
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["displacement", "noise"],
            "moods": ["aggressive", "tense", "euphoric"], "style": "wild", "energy": [0.5, 1.0]}
    params = {
        **standard_effect_params(),
        # the beat envelope feeds the trauma peak directly: keep it raw (easing would flatten the hits)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "frequency": num(1.0, 40.0, 10.0, "Shake frequency", step=0.5, unit="Hz", tier="regional", basic=True),
        "max_offset": num(0.0, 0.1, 0.03, "Max offset (of height)", step=0.002, tier="regional"),
        "max_roll": num(0.0, 8.0, 1.5, "Max roll", step=0.1, unit="°", tier="regional"),
        "decay": num(0.2, 8.0, 1.8, "Trauma decay", step=0.05, unit="/s", tier="regional"),
        "idle_trauma": num(0.0, 0.3, 0.12, "Idle trauma (base shake)", step=0.01, tier="regional"),
        "axis_bias": num(-1.0, 1.0, 0.0, "Axis bias (horizontal .. vertical)", step=0.05, tier="regional"),
        "motion_blur": num(0.0, 1.0, 0.35, "Motion blur", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("trauma_shake")
        self._P_h = np.zeros(6 * TAPS, dtype=np.float32)
        self._P_d = cp.zeros(6 * TAPS, dtype=cp.float32)
        rng = np.random.default_rng(0x7A5E)
        # 1D gradient-noise lattices, one per channel (x, y, roll)
        self._grad = [list(rng.uniform(-1.0, 1.0, 256)) for _ in range(3)]
        self._nt = float(rng.uniform(0.0, 64.0))     # integrated noise time (lattice units)
        self._trauma = 0.8                           # primed: the shake reads the moment it is faded in
        self._s = self._trauma ** 2

    # ------------------------------------------------------------------ host noise
    def _gnoise(self, t: float, ch: int) -> float:
        """1D gradient noise in [-1, 1], C2 (quintic fade)."""
        i = math.floor(t)
        f = t - i
        g = self._grad[ch]
        g0, g1 = g[int(i) & 255], g[(int(i) + 1) & 255]
        u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0)
        return 2.0 * ((1.0 - u) * g0 * f + u * g1 * (f - 1.0))

    def _fbm(self, t: float, ch: int) -> float:
        """3-octave fbm, soft-clipped so it is bounded by 1 but still uses its range."""
        v = 0.0
        for k, (fr, a) in enumerate(_OCT):
            v += a * self._gnoise(t * fr + 17.3 * k, ch)
        return math.tanh(1.8 * v * _NORM) * _SOFT

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        raw_dt = float(dt)
        dt = min(max(raw_dt, 0.0), 0.1)
        idle = float(p["idle_trauma"])
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        nt0, s0 = self._nt, self._s
        if raw_dt > 0.5:                             # long gap: restart from the idle shake, no blur smear
            self._trauma = idle
        self._trauma = min(1.0, max(self._trauma * math.exp(-float(p["decay"]) * dt), pulse, idle))
        tr = self._trauma
        # the shake quickens a little with trauma (a rate, so nothing jumps); dt = 0 freezes it
        self._nt += float(p["frequency"]) * (0.75 + 0.5 * tr) * dt
        self._s = tr * tr
        if raw_dt > 0.5:
            nt0, s0 = self._nt, self._s

        amount = float(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smoothstep01(amount)
        W, H = self.ctx.width, self.ctx.height
        b = float(p["axis_bias"])
        wx, wy = min(1.0, 1.0 - b), min(1.0, 1.0 + b)
        off = float(p["max_offset"]) * H * m
        roll = math.radians(float(p["max_roll"])) * m

        # static overscan (grows with amount, not with each hit): cover the worst case, 85 % of the way
        ox_max, oy_max = off * wx, off * wy
        ca, sa = math.cos(roll), math.sin(roll)
        zx = (ca * 0.5 * W + sa * 0.5 * H) / max(0.5 * W - ox_max, 1.0)
        zy = (sa * 0.5 * W + ca * 0.5 * H) / max(0.5 * H - oy_max, 1.0)
        inv_z = 1.0 / (1.0 + 0.85 * (max(zx, zy, 1.0) - 1.0))

        shutter = float(p["motion_blur"])
        taps = TAPS if (shutter > 0.01 and self._nt != nt0) else 1
        for k in range(taps):
            f = (k / (TAPS - 1)) * shutter if taps > 1 else 0.0   # 0 = now, 1 = last frame
            nt = self._nt + (nt0 - self._nt) * f
            s = self._s + (s0 - self._s) * f
            ox = s * off * wx * self._fbm(nt, 0)
            oy = s * off * wy * self._fbm(nt + 31.7, 1)
            a = s * roll * self._fbm(nt * 0.8 + 63.1, 2)
            c, sn = math.cos(a) * inv_z, math.sin(a) * inv_z
            self._P_h[6 * k:6 * k + 6] = (c, -sn, sn, c, ox, oy)
        self._P_d.set(self._P_h)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._P_d, np.int32(taps)))
