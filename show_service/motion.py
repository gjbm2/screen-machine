"""How a moving light's pan and tilt are drawn: the mathematics of the motion stage (docs/LIGHTS.md §5.5).

DMX gives a moving head a position, never a speed (owner, 27 Nov: "is the dmx specifying positional outcomes or
acceleration / velocity?"): what is sent, frame by frame, *is* the movement. This module makes every path that is sent
one a head can follow, and one that reads as deliberate, by construction and not by each pattern's good manners:

1. **A rate limiter, both axes together** (``approach``). The command goes toward its target along the straight line
   in (pan, tilt), as fast as the axis with the furthest to go (for its speed) allows. A move's two axes leave and
   arrive together, so a beam travels one clean arc and never a dog-leg, and neither axis outruns the light.
2. **Two moving averages**, ``ramp_s`` then ``jerk_s`` long (``Smoother``; the FIR cascade of Biagiotti and
   Melchiorri, "FIR filters for online trajectory planning with time- and frequency-domain specifications", 2012).
   The average of a speed-limited command over ramp_s can only change speed so fast: a jump comes out as the
   trapezoid, reaching full speed in ramp_s. Averaged again over jerk_s, its acceleration can only change so fast: the
   trapezoid's corners become S-curves, so a move eases away and eases in. An average never overshoots and never
   leaves the span of what it averaged, and a corner in a path (a sweep's turn, a hop, a square wave) comes out as a
   blend that long: how tightly a path may curve at a speed is set by the same two numbers.

So for any target at all, whatever a scene asks, with A = v_max / ramp_s:

    a move from rest, or to rest:   |speed| <= v_max    |acceleration| <= A      |jerk| <= A / jerk_s
    anything else (a turn-about):   |speed| <= v_max    |acceleration| <= 2 A    |jerk| <= 4 A / jerk_s

(From rest to full speed takes ramp_s + jerk_s: show.yaml's lights.motion.accel_s, split by its ease. A
full-speed turn-about takes that long too.) A move of d degrees from rest to rest takes exactly ``d / v_max + ramp_s + jerk_s`` (``travel_s``: the one number
every pre-roll uses, so a beam still lands on its line). A path that keeps moving comes out ``lag_s`` late (patterns
are drawn that much ahead, so they stay on the beat) and a little smaller the faster it turns back on itself
(``kept``: the pattern fit slows a pattern that would lose its shape).

Pure functions and one small class: no engine, no clock, no I/O. Angles in degrees, times in seconds.
"""
from __future__ import annotations

import math
from collections import deque
from itertools import islice
from typing import Sequence

#: An arrived command is put exactly on its target within this (floating-point dust from the averages).
SNAP_DEG = 1e-9


def travel_s(d_pan: float, d_tilt: float, v_max: Sequence[float], ramp_s: float, jerk_s: float) -> float:
    """How long a move of (d_pan, d_tilt) degrees takes from rest to rest: the slower axis at its top speed, plus the
    two averages. 0 for no move."""
    lin = 0.0
    for d, v in ((d_pan, v_max[0]), (d_tilt, v_max[1])):
        if d and v > 0.0:
            lin = max(lin, abs(d) / v)
    return lin + max(0.0, ramp_s) + max(0.0, jerk_s) if lin > 0.0 else 0.0


def lag_s(ramp_s: float, jerk_s: float) -> float:
    """How far behind its target a steadily moving command runs (each average delays by half its length)."""
    return 0.5 * (max(0.0, ramp_s) + max(0.0, jerk_s))


def approach(cur: Sequence[float], tgt: Sequence[float], v_max: Sequence[float], dt: float) -> tuple[float, float]:
    """One frame of the rate limiter: from ``cur`` toward ``tgt`` along the straight line between them, both axes
    scaled alike so neither passes its top speed. A target within this frame's reach is taken as it is."""
    dp, dq = tgt[0] - cur[0], tgt[1] - cur[1]
    need = 0.0
    for d, v in ((dp, v_max[0]), (dq, v_max[1])):
        if d:
            reach = v * dt
            need = max(need, abs(d) / reach if reach > 0.0 else math.inf)
    if need <= 1.0:
        return float(tgt[0]), float(tgt[1])
    if not math.isfinite(need):
        return float(cur[0]), float(cur[1])
    return cur[0] + dp / need, cur[1] + dq / need


class _Box:
    """The average of a two-axis signal over its last ``tau`` seconds. The signal is its samples joined by straight
    lines, and stood at its first sample before that."""
    __slots__ = ("q",)

    def __init__(self) -> None:
        self.q: deque = deque()

    def reset(self, t: float, p: Sequence[float]) -> None:
        self.q.clear()
        self.q.append((t, float(p[0]), float(p[1])))

    def push(self, t: float, p: Sequence[float], tau: float) -> tuple[float, float]:
        q = self.q
        if q and t <= q[-1][0]:                                  # no time has passed: the newest sample, again
            q[-1] = (q[-1][0], float(p[0]), float(p[1]))
        else:
            q.append((t, float(p[0]), float(p[1])))
        if tau <= 0.0:
            while len(q) > 1:
                q.popleft()
            return q[-1][1], q[-1][2]
        t0 = q[-1][0] - tau
        while len(q) >= 2 and q[1][0] <= t0:                     # keep one sample at or before the window's start
            q.popleft()
        ta, xa, ya = q[0]
        ax = ay = 0.0
        if ta > t0:                                              # before its first sample: stood there
            ax, ay = xa * (ta - t0), ya * (ta - t0)
        elif len(q) >= 2:                                        # the window opens between the first two samples
            tb, xb, yb = q[1]
            f = (t0 - ta) / (tb - ta)
            ta, xa, ya = t0, xa + (xb - xa) * f, ya + (yb - ya) * f
        for tb, xb, yb in islice(q, 1, None):
            w = tb - ta
            ax += 0.5 * (xa + xb) * w
            ay += 0.5 * (ya + yb) * w
            ta, xa, ya = tb, xb, yb
        return ax / tau, ay / tau


class Smoother:
    """One light's motion stage: the rate limiter and the two averages. ``step`` each frame with the time since the
    last one; ``out`` is the (pan, tilt) to send, ``raw`` the rate limiter's own point (where the move has got to
    before it is eased)."""
    __slots__ = ("raw", "out", "t", "_a", "_j")

    def __init__(self, at: Sequence[float]) -> None:
        self._a, self._j = _Box(), _Box()
        self.t = 0.0
        self.raw = self.out = (float(at[0]), float(at[1]))
        self.reset(at)

    def reset(self, at: Sequence[float]) -> None:
        """Standing still at ``at`` (the start, or after something else placed the light)."""
        self.raw = self.out = (float(at[0]), float(at[1]))
        self._a.reset(self.t, self.raw)
        self._j.reset(self.t, self.raw)

    def step(self, dt: float, target: Sequence[float], v_max: Sequence[float], ramp_s: float, jerk_s: float
             ) -> tuple[float, float]:
        if dt > 0.0:
            self.t += dt
            self.raw = approach(self.raw, target, v_max, dt)
        y = self._a.push(self.t, self.raw, ramp_s)
        z = self._j.push(self.t, y, jerk_s)
        if self.raw[0] == target[0] and self.raw[1] == target[1] and \
                abs(z[0] - target[0]) <= SNAP_DEG and abs(z[1] - target[1]) <= SNAP_DEG:
            z = (float(target[0]), float(target[1]))             # arrived: exactly on it
        self.out = z
        return z


def _cyclic_average(xs: Sequence[float], width: float) -> list[float]:
    """A closed path's samples (evenly spaced, the last joining the first) averaged over ``width`` samples behind
    each one, the path taken as straight lines between samples."""
    n = len(xs)
    if width <= 0.0 or n < 2:
        return list(xs)
    if width >= n:                                               # longer than the whole cycle: its mean
        m = sum(xs) / n
        return [m] * n
    pre = [0.0] * (n + 1)                                        # the integral up to each sample
    for k in range(n):
        pre[k + 1] = pre[k] + 0.5 * (xs[k] + xs[(k + 1) % n])
    total = pre[n]

    def integral(u: float) -> float:                             # up to any place on the (repeating) path
        laps = math.floor(u / n)
        u -= laps * n
        k = min(n - 1, int(u))
        f = u - k
        a, b = xs[k], xs[(k + 1) % n]
        return laps * total + pre[k] + f * a + 0.5 * f * f * (b - a)
    return [(integral(k) - integral(k - width)) / width for k in range(n)]


def kept(points: Sequence[Sequence[float]], period_s: float, ramp_s: float, jerk_s: float) -> float:
    """How much of a repeating path's size survives the two averages: 1 = all of it, 0.5 = it is drawn half as big.
    ``points``: (pan, tilt) evenly spaced over one period, the last the same place as the first. The smaller share of
    the two axes (an axis that hardly moves doesn't count). A path that doesn't close is not judged (1)."""
    n = len(points) - 1
    if n < 4 or period_s <= 0.0 or (ramp_s <= 0.0 and jerk_s <= 0.0):
        return 1.0
    worst = 1.0
    span = [max(p[i] for p in points) - min(p[i] for p in points) for i in (0, 1)]
    for i in (0, 1):
        if span[i] <= 1e-6 or span[i] < 0.05 * max(span):
            continue
        if abs(points[0][i] - points[n][i]) > 0.02 * span[i]:
            return 1.0                                           # an open path (a wander): nothing to hold it to
        xs = [p[i] for p in points[:n]]
        step = period_s / n
        ys = _cyclic_average(_cyclic_average(xs, max(0.0, ramp_s) / step), max(0.0, jerk_s) / step)
        worst = min(worst, (max(ys) - min(ys)) / span[i])
    return worst
