"""Handheld drift: a handheld / steadicam float.  Slow multi-octave noise drift in pan, tilt, roll and zoom
(breathing), occasional smooth eased operator reframes, and a gentle footstep bob on the beat.

Source: Original (inspired by https://github.com/bevyengine/bevy/blob/main/examples/camera/2d_screen_shake.rs)
Author: smfx (original; the noise-driven camera idea is shared with Bevy's MIT OR Apache-2.0 example)
License: MIT (original)
license_class: permissive

How it works: every motion channel is host-side 1D gradient-noise fbm (4 octaves, C2 quintic fade) of an
*integrated* noise time (rate = drift_speed), so changing the speed never jumps the picture.  Zoom breathing is a
slow integrated sine with a little noise on top, and only ever zooms in (it never uncovers an edge).  Operator
reframes fire from an integrated counter (reframe_rate per minute): each one picks the next pre-seeded framing
(pan, tilt, slight zoom and roll) and travels there along a smootherstep path, followed by an under-damped
spring so the "operator" lands with a soft settle.  The beat (pulse) drives a spring-damped vertical footstep dip
that decays on its own.  Springs are semi-implicit Euler, substepped to <= 1/120 s, dt clamped to 0.1 s.

Realtime mapping: m = smoothstep(amount) scales every displacement, the roll, the breathing, the bob and the
overscan zoom, so amount 0 is an exact copy (early out) and 0 -> 1 is a smooth morph from still to fully filmed.
Overscan grows with the worst-case displacement (85 % covered; mirror addressing hides the rare extreme).
Sampling is a single sub-pixel bilinear tap (never snapped), so stills float without crawling.  dt = 0 freezes.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + r"""
// Reflect a continuous pixel coordinate into [0, n] (hides the rare frame where the drift outruns the overscan).
__device__ __forceinline__ float hd_mirror(float v, float n) {
    const float p = 2.f * n;
    v = fmodf(fabsf(v), p);
    return v > n ? p - v : v;
}

// P: affine camera transform (a, b, c, d, tx, ty) mapping an output offset from the centre to a source offset.
extern "C" __global__
void handheld_drift(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                    const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float dx = x + 0.5f - cx, dy = y + 0.5f - cy;
    const float sx = hd_mirror(cx + P[0] * dx + P[1] * dy + P[4], (float)W);
    const float sy = hd_mirror(cy + P[2] * dx + P[3] * dy + P[5], (float)H);
    out[y * W + x] = pack(bilin(src, sx, sy, W, H));
}
"""

# fbm octaves (frequency multiplier, amplitude): a slow float, a sway, and a faint hand tremble on top
_OCT = ((1.0, 1.0), (2.13, 0.42), (4.37, 0.16), (9.1, 0.05))
_NORM = 1.0 / sum(a for _, a in _OCT)
_SOFT = 1.0 / math.tanh(1.6)

BASE_RATE = 0.16        # lattice units / s for the lowest octave at drift_speed 1 (~6 s per wander)
BREATH_HZ = 0.17        # zoom breathing cycle at drift_speed 1
MAX_SHIFT = 0.035       # drift translation at drift_amount 1 (fraction of height)
REFRAME_SHIFT = 0.03    # reframe pan/tilt at drift_amount 1 (fraction of height)
REFRAME_ZOOM = 0.03     # extra zoom-in range of a reframe at drift_amount 1
REFRAME_ROLL = 0.35     # reframe roll as a fraction of the roll knob
N_TARGETS = 64
SUBSTEP = 1.0 / 120.0


def _smoothstep01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _smootherstep01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * x * (x * (x * 6.0 - 15.0) + 10.0)


class HandheldDrift(Effect):
    name = "handheld_drift"
    stage = "motion"
    label = "Handheld camera"
    description = ("Handheld / steadicam float: slow noise drift in pan, tilt, roll and zoom breathing, occasional "
                   "eased operator reframes and a gentle footstep bob on the beat. Makes stills feel filmed.")
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["noise", "displacement"],
            "moods": ["calm", "mellow", "dreamy"], "style": "gentle", "energy": [0.0, 0.6]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives the footstep spring directly: keep it raw (the spring does the smoothing)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "drift_amount": num(0.0, 1.0, 0.45, "Drift amount", step=0.01, tier="regional", basic=True),
        "drift_speed": num(0.0, 4.0, 1.0, "Drift speed", step=0.05, unit="×", tier="realtime", bind="rate"),
        "roll": num(0.0, 4.0, 0.7, "Roll", step=0.05, unit="°", tier="regional"),
        "zoom_breath": num(0.0, 0.08, 0.018, "Zoom breathing", step=0.002, tier="regional"),
        "reframe_rate": num(0.0, 12.0, 3.0, "Operator reframes", step=0.25, unit="/min", tier="regional"),
        "step_bob": num(0.0, 0.03, 0.008, "Footstep bob (of height)", step=0.001, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("handheld_drift")
        self._P_h = np.zeros(6, dtype=np.float32)
        self._P_d = cp.zeros(6, dtype=cp.float32)
        rng = np.random.default_rng(0x4A1D)
        # 1D gradient-noise lattices, one per channel (x, y, roll, breath wobble)
        self._grad = [list(rng.uniform(-1.0, 1.0, 256)) for _ in range(4)]
        self._nt = float(rng.uniform(0.0, 64.0))          # integrated noise time (lattice units)
        self._bphase = float(rng.uniform(0.0, 2.0 * math.pi))
        # operator framings: (pan, tilt, zoom-in 0..1, roll) in normalised units; alternate sides so moves read
        tx = rng.uniform(0.35, 1.0, N_TARGETS) * np.where(np.arange(N_TARGETS) % 2 == 0, 1.0, -1.0)
        ty = rng.uniform(-0.7, 0.7, N_TARGETS)
        tz = rng.uniform(0.0, 1.0, N_TARGETS)
        tr = rng.uniform(-1.0, 1.0, N_TARGETS)
        self._targets = [(float(a), float(b), float(c), float(d)) for a, b, c, d in zip(tx, ty, tz, tr)]
        self._ti = 0
        self._rc = 0.6                                     # integrated reframe counter (a reframe at each integer)
        self._from = (0.0, 0.0, 0.3, 0.0)
        self._to = self._targets[0]
        self._mv = 0.35                                    # primed mid-move: the operator is already settling
        # spring followers: reframe (x, y, zoom, roll) and the footstep bob; [pos, vel] each
        self._rf = [[self._from[i] + (self._to[i] - self._from[i]) * _smootherstep01(self._mv), 0.0]
                    for i in range(4)]
        self._bob = [0.0, 0.0]

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
        """4-octave fbm, soft-clipped so it is bounded by 1 but still uses its range."""
        v = 0.0
        for k, (fr, a) in enumerate(_OCT):
            v += a * self._gnoise(t * fr + 17.3 * k, ch)
        return math.tanh(1.6 * v * _NORM) * _SOFT

    # ------------------------------------------------------------------ state
    def _advance(self, dt: float, p: dict) -> None:
        spd = max(float(p["drift_speed"]), 0.0)
        self._nt += BASE_RATE * spd * dt               # host fp64 noise time: no wrap (octaves are not 256-periodic)
        self._bphase = (self._bphase + 2.0 * math.pi * BREATH_HZ * spd * dt) % (2.0 * math.pi)
        # reframe events from an integrated counter
        self._rc += float(p["reframe_rate"]) / 60.0 * dt
        if self._rc >= 1.0:
            self._rc -= math.floor(self._rc)
            self._from = tuple(s[0] for s in self._rf)
            self._ti = (self._ti + 1) % N_TARGETS
            self._to = self._targets[self._ti]
            self._mv = 0.0
        dur = 2.6 / min(max(spd, 0.35), 3.0)
        self._mv = min(self._mv + dt / dur, 1.0)
        u = _smootherstep01(self._mv)
        goal = [self._from[i] + (self._to[i] - self._from[i]) * u for i in range(4)]
        bob_goal = min(max(float(p["pulse"]), 0.0), 1.0)
        # springs: reframe follower (soft landing) and footstep (quick dip, slight rebound)
        n = max(1, int(math.ceil(dt / SUBSTEP)))
        h = dt / n
        w_r, z_r = 5.0, 0.72
        w_b, z_b = 16.0, 0.55
        for _ in range(n):
            for i in range(4):
                s = self._rf[i]
                s[1] += h * (w_r * w_r * (goal[i] - s[0]) - 2.0 * z_r * w_r * s[1])
                s[0] += h * s[1]
            b = self._bob
            b[1] += h * (w_b * w_b * (bob_goal - b[0]) - 2.0 * z_b * w_b * b[1])
            b[0] += h * b[1]
        self._bob[0] = min(max(self._bob[0], -0.5), 1.5)

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        raw_dt = float(dt)
        dt = min(max(raw_dt, 0.0), 0.1)
        if raw_dt > 0.5:                                   # long gap: let the bob rest, keep the framing
            self._bob = [0.0, 0.0]
            for s in self._rf:
                s[1] = 0.0
        if dt > 0.0:
            self._advance(dt, p)

        amount = float(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smoothstep01(amount)
        W, H = self.ctx.width, self.ctx.height
        da = float(p["drift_amount"])
        shift = MAX_SHIFT * da * H * m
        rf_shift = REFRAME_SHIFT * da * H * m
        roll_max = math.radians(float(p["roll"])) * m
        breath = float(p["zoom_breath"]) * m
        rf_zoom = REFRAME_ZOOM * da * m
        bob_px = float(p["step_bob"]) * H * m

        # drift channels
        nt = self._nt
        ox = shift * self._fbm(nt, 0)
        oy = 0.75 * shift * self._fbm(nt * 0.9 + 31.7, 1)
        roll = 0.7 * roll_max * self._fbm(nt * 0.8 + 63.1, 2)
        br = 0.5 + 0.5 * (0.75 * math.sin(self._bphase) + 0.25 * self._fbm(nt * 0.5 + 97.9, 3))
        zoom_in = 1.0 + breath * min(max(br, 0.0), 1.0)
        # operator reframe (spring-followed)
        rx, ry, rz, rr = (s[0] for s in self._rf)
        ox += rf_shift * rx
        oy += 0.6 * rf_shift * ry
        zoom_in *= 1.0 + rf_zoom * min(max(rz, 0.0), 1.2)
        roll += REFRAME_ROLL * roll_max * rr
        # footstep: the camera dips, the picture rises a touch
        oy += bob_px * self._bob[0]

        # static overscan (grows with amount and drift, not with each move): cover the worst case, 85 % of the way
        ox_max = shift + rf_shift
        oy_max = 0.75 * shift + 0.6 * rf_shift + 1.2 * bob_px
        rmax = (0.7 + REFRAME_ROLL) * roll_max
        ca, sa = math.cos(rmax), math.sin(rmax)
        zx = (ca * 0.5 * W + sa * 0.5 * H) / max(0.5 * W - ox_max, 1.0)
        zy = (sa * 0.5 * W + ca * 0.5 * H) / max(0.5 * H - oy_max, 1.0)
        inv_z = 1.0 / ((1.0 + 0.85 * (max(zx, zy, 1.0) - 1.0)) * zoom_in)

        c, sn = math.cos(roll) * inv_z, math.sin(roll) * inv_z
        self._P_h[:] = (c, -sn, sn, c, ox, oy)
        self._P_d.set(self._P_h)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._P_d))
