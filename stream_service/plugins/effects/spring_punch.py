"""Spring Punch: a damped-spring beat response.  Each kick punches the zoom in, jolts the frame along a direction
and squashes / stretches it; the springs overshoot and settle like a rubber band, with an optional motion blur
(radial for the zoom, directional for the jolt) and palette-tinted ghost trails while the picture is moving.
One effect covers zoom-bounce, screen jolt and rubber-band bounce.

Source: Original (inspired by https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Bounce.fs)
Author: smfx (original damped-spring physics)
License: MIT (original)
license_class: permissive

How it works: three under-damped springs live on the host (zoom, a 2D jolt, squash), integrated with semi-implicit
Euler, substepped to at most 1/120 s with dt clamped to 0.1 (dt = 0 means no step; dt > 0.5 s resets them).  The
beat never moves a position: every rising increment of ``pulse`` adds an impulse to the spring *velocities*, the
jolt impulse along ``jolt_direction`` (which can turn by ``jolt_turn`` on each detected beat onset; the 2D spring
keeps the motion continuous when it does).  The squash axis follows the jolt direction through a short ease.
The picture transform is one affine map per frame (zoom x squash about the centre, plus the jolt offset); the
motion blur re-evaluates the springs a few milliseconds back along their velocity and averages up to 6 taps,
with per-channel normalised palette weights so a static picture stays exactly itself.  Overscan follows the
absolute displacement (an exact corner test per tap, plus an envelope that anticipates the rebound swing), so no
edges ever show.  amount scales the whole response through m = smoothstep(amount): 0 is an exact copy.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

NT = 6     # max blur taps
TF = 12    # floats per tap: m00 m01 m10 m11 tx ty wr wg wb pad pad pad

_SRC = HELPERS + PALETTE_CUDA + r"""
#define TF 12
extern "C" __global__
void spring_punch(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                  const float* __restrict__ T, const int n)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float qx = x + 0.5f - cx, qy = y + 0.5f - cy;
    float ar = 0.f, ag = 0.f, ab = 0.f, wr = 0.f, wg = 0.f, wb = 0.f;
    for (int k = 0; k < n && k < 6; ++k) {
        const float* t = T + k * TF;
        const float sx = cx + t[0] * qx + t[1] * qy + t[4];
        const float sy = cy + t[2] * qx + t[3] * qy + t[5];
        const float4 c = bilin(src, sx, sy, W, H);
        ar += t[6] * c.x; ag += t[7] * c.y; ab += t[8] * c.z;
        wr += t[6]; wg += t[7]; wb += t[8];
    }
    out[y * W + x] = pack(make_float4(ar / fmaxf(wr, 1e-4f), ag / fmaxf(wg, 1e-4f), ab / fmaxf(wb, 1e-4f), 255.f));
}
"""


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _smooth01(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _pal(t: float, P: np.ndarray) -> tuple[float, float, float]:
    """Host copy of the shared cosine palette (sRGB 0..1)."""
    tau = 2.0 * math.pi
    return tuple(_clamp(P[i] + P[3 + i] * math.cos(tau * (P[6 + i] * t + P[9 + i])), 0.0, 1.0) for i in range(3))


_PAL = palette_params("neon", 1.0, 0.05)
_PAL["palette"] = enum(PALETTES, "neon", "Trail palette", tier="regional")


class SpringPunch(Effect):
    name = "spring_punch"
    label = "Spring Punch"
    description = ("Damped-spring beat response: each kick punches the zoom, jolts and squashes the frame, which "
                   "overshoots and settles like a rubber band, with optional motion blur.")
    stage = "motion"
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["displacement", "analytic"]}
    params = {
        **standard_effect_params(),
        "zoom_mix": num(0.0, 1.0, 0.6, "Zoom punch", tier="regional", basic=True),
        "jolt_mix": num(0.0, 1.0, 0.45, "Screen jolt", tier="regional"),
        "squash_mix": num(0.0, 1.0, 0.4, "Squash & stretch", tier="regional"),
        "stiffness": num(2.0, 12.0, 4.5, "Spring stiffness", unit="Hz", step=0.1, tier="regional"),
        "damping": num(0.05, 1.0, 0.25, "Damping (wobble ↔ dead)", tier="regional"),
        "jolt_direction": num(0.0, 360.0, 90.0, "Jolt direction (0 right, 90 down)", unit="°", step=1.0,
                              tier="regional"),
        "jolt_turn": num(0.0, 180.0, 0.0, "Turn per beat", unit="°", step=1.0, tier="regional"),
        "blur": num(0.0, 1.0, 0.35, "Motion blur", tier="regional"),
        "trail_tint": num(0.0, 1.0, 0.0, "Trail colour", tier="regional"),
        **_PAL,
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("spring_punch")
        self._T_d = cp.zeros(NT * TF, dtype=cp.float32)
        self._T_h = np.zeros(NT * TF, dtype=np.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._reset()
        self._prev = 0.0
        self._rising = False
        self._since = 1.0
        self._turn = 0.0                           # accumulated per-beat turn (rad)
        self._ax = np.array([0.0, 1.0])            # squash axis (unit), eased toward the jolt direction
        self._drift = 0.0

    def _reset(self) -> None:
        self._z = np.zeros(2)                       # zoom spring: x, v
        self._j = np.zeros(4)                       # jolt spring: x, y, vx, vy
        self._s = np.zeros(2)                       # squash spring: x, v
        self._ov = 1.0                              # overscan envelope

    # -- physics ---------------------------------------------------------------------------------------------
    def _step(self, p: dict, dt: float) -> None:
        pulse = _clamp(float(p["pulse"]), 0.0, 1.0)
        dp = max(0.0, pulse - self._prev)
        rising = pulse - self._prev > 0.02
        self._since += dt
        if rising and not self._rising and self._since > 0.12:      # beat onset: turn the jolt direction
            self._turn = (self._turn + math.radians(float(p["jolt_turn"]))) % (2.0 * math.pi)
            self._since = 0.0
        self._rising = rising
        self._prev = pulse
        th = math.radians(float(p["jolt_direction"])) + self._turn
        dx, dy = math.cos(th), math.sin(th)
        f = float(p["stiffness"])
        zeta = _clamp(float(p["damping"]), 0.02, 1.5)
        w = 2.0 * math.pi * f
        ws = w * 1.3                                                  # squash rings a touch faster: rubbery
        K = 1.4                                                       # impulse gain: first swing peaks ~1
        if dp > 0.0:
            self._z[1] += w * K * dp
            self._j[2] += w * K * dp * dx
            self._j[3] += w * K * dp * dy
            self._s[1] += ws * K * dp
        # squash axis eases toward the jolt direction (no snap when jolt_turn rotates it)
        a = 1.0 - math.exp(-dt / 0.08)
        ax = self._ax + (np.array([dx, dy]) - self._ax) * a
        nrm = float(np.hypot(ax[0], ax[1]))
        self._ax = ax / nrm if nrm > 1e-6 else np.array([dx, dy])
        nsub = max(1, int(math.ceil(dt * 120.0)))
        h = dt / nsub
        z, j, s = self._z, self._j, self._s
        for _ in range(nsub):                                         # semi-implicit Euler
            z[1] += (-w * w * z[0] - 2.0 * zeta * w * z[1]) * h
            z[0] += z[1] * h
            j[2] += (-w * w * j[0] - 2.0 * zeta * w * j[2]) * h
            j[3] += (-w * w * j[1] - 2.0 * zeta * w * j[3]) * h
            j[0] += j[2] * h
            j[1] += j[3] * h
            s[1] += (-ws * ws * s[0] - 2.0 * zeta * ws * s[1]) * h
            s[0] += s[1] * h
        # safety bounds (resonant beats at very low damping)
        z[0] = _clamp(z[0], -2.5, 2.5); z[1] = _clamp(z[1], -3.0 * w, 3.0 * w)
        s[0] = _clamp(s[0], -2.5, 2.5); s[1] = _clamp(s[1], -3.0 * ws, 3.0 * ws)
        for i in (0, 1):
            j[i] = _clamp(j[i], -2.5, 2.5)
            j[i + 2] = _clamp(j[i + 2], -3.0 * w, 3.0 * w)

    # -- geometry --------------------------------------------------------------------------------------------
    def _affine(self, xz: float, jx: float, jy: float, xs: float, g: tuple) -> tuple:
        """Inverse map (output -> source, about the centre) for one spring state, before overscan."""
        zmax, jpx, emax = g
        Z = max(0.5, 1.0 + zmax * xz)
        e = _clamp(emax * xs, -0.7, 0.7)
        a0, a1 = float(self._ax[0]), float(self._ax[1])
        il, im = math.exp(e), math.exp(-e)        # inverse of (squash e^-e along axis, stretch e^e across)
        m00 = (il * a0 * a0 + im * (1.0 - a0 * a0)) / Z
        m01 = ((il - im) * a0 * a1) / Z
        m11 = (il * a1 * a1 + im * (1.0 - a1 * a1)) / Z
        return m00, m01, m01, m11, jpx * jx, jpx * jy

    def _need(self, M: tuple, W: int, H: int) -> float:
        m00, m01, m10, m11, ox, oy = M
        hw, hh = 0.5 * W, 0.5 * H
        r = 1.0
        for sx in (-hw, hw):
            for sy in (-hh, hh):
                qx, qy = sx - ox, sy - oy
                r = max(r, abs(m00 * qx + m01 * qy) / hw, abs(m10 * qx + m11 * qy) / hh)
        return r

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        if dt > 0.5:                                                  # long gap: settle, re-arm the edge detector
            self._reset()
            self._prev = _clamp(float(p["pulse"]), 0.0, 1.0)
        dts = _clamp(dt, 0.0, 0.1)
        if dts > 0.0:
            self._step(p, dts)
            self._drift = (self._drift + float(p["drift_speed"]) * dts) % 1.0
            tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
            if not np.array_equal(tgt, self._pal):
                self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
                if np.max(np.abs(tgt - self._pal)) < 1e-4:
                    self._pal = tgt

        amount = _clamp(float(p["amount"]), 0.0, 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        m = _smooth01(amount)
        g = (m * float(p["zoom_mix"]) * 0.22,                         # zoom per unit swing
             m * float(p["jolt_mix"]) * 0.075 * H,                    # jolt px per unit swing
             m * float(p["squash_mix"]) * 0.16)                       # log-squash per unit swing
        f = float(p["stiffness"])
        zeta = _clamp(float(p["damping"]), 0.02, 1.5)
        w = 2.0 * math.pi * f
        ws = 1.3 * w
        z, j, s = self._z, self._j, self._s

        # blur taps: the springs re-evaluated back along their velocity over a short shutter
        shutter = 0.022 * _clamp(float(p["blur"]), 0.0, 1.0)
        states = []
        for k in range(NT):
            tau = shutter * k / (NT - 1)
            states.append((z[0] - z[1] * tau, j[0] - j[2] * tau, j[1] - j[3] * tau, s[0] - s[1] * tau))
        maps = [self._affine(*st, g) for st in states]

        # overscan: exact corner need for every tap, plus an envelope anticipating the rebound swing
        need = max(self._need(M, W, H) for M in maps)
        r = math.exp(-zeta * math.pi / math.sqrt(max(1.0 - zeta * zeta, 1e-3))) if zeta < 1.0 else 0.0
        Az = math.hypot(z[0], z[1] / w)
        Aj = math.hypot(math.hypot(j[0], j[1]), math.hypot(j[2], j[3]) / w)
        As = math.hypot(s[0], s[1] / ws)
        ant = ((1.0 + 2.0 * g[1] * Aj * r / H) * math.exp(g[2] * As * r) / max(0.5, 1.0 - g[0] * Az * r))
        target = max(need, ant)
        if dts > 0.0:
            tc = 0.04 if target > self._ov else max(0.25, 1.5 / max(zeta * w, 1e-3))
            self._ov += (target - self._ov) * (1.0 - math.exp(-dts / tc))
        ov = max(self._ov, need, 1.0)

        # tap count: one tap unless the shutter smears a corner by more than a pixel
        n = 1
        if shutter > 0.0:
            a, b = maps[0], maps[-1]
            smear = 0.0
            for sx in (-0.5 * W, 0.5 * W):
                for sy in (-0.5 * H, 0.5 * H):
                    ux = (a[0] - b[0]) * sx + (a[1] - b[1]) * sy - (a[0] * a[4] + a[1] * a[5]) + (b[0] * b[4] + b[1] * b[5])
                    uy = (a[2] - b[2]) * sx + (a[3] - b[3]) * sy - (a[2] * a[4] + a[3] * a[5]) + (b[2] * b[4] + b[3] * b[5])
                    smear = max(smear, math.hypot(ux, uy) / ov)
            if smear > 1.0:
                n = NT
        tint = _clamp(float(p["trail_tint"]), 0.0, 1.0)
        per = float(p["palette_period"])
        T = self._T_h
        T[:] = 0.0
        for k in range(n):
            m00, m01, m10, m11, ox, oy = maps[k]
            m00, m01, m10, m11 = m00 / ov, m01 / ov, m10 / ov, m11 / ov
            o = k * TF
            T[o + 0], T[o + 1], T[o + 2], T[o + 3] = m00, m01, m10, m11
            T[o + 4] = -(m00 * ox + m01 * oy)
            T[o + 5] = -(m10 * ox + m11 * oy)
            gk = k / (NT - 1)
            wk = 1.0 - 0.55 * gk                                     # older taps fainter
            if tint > 0.0 and n > 1:
                c = _pal(self._drift + per * (0.15 + 0.6 * gk), self._pal)
                tk = tint * (0.35 + 0.65 * gk)
                T[o + 6] = wk * ((1.0 - tk) + tk * (0.1 + 1.8 * c[0]))
                T[o + 7] = wk * ((1.0 - tk) + tk * (0.1 + 1.8 * c[1]))
                T[o + 8] = wk * ((1.0 - tk) + tk * (0.1 + 1.8 * c[2]))
            else:
                T[o + 6] = T[o + 7] = T[o + 8] = wk
        self._T_d.set(T)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._T_d, np.int32(n)))
