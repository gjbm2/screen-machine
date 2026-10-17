"""How the live score moves: pure python and numpy (no GPU), so it's tested on its own.

* ``smoothstep``; ``spring_step``: the exact critically damped step toward a fixed target (floats or arrays).
* ``Glide``: a quintic (minimum-jerk) move to a target over T seconds, re-planned from wherever it is: position,
  speed and acceleration stay continuous through a re-plan, so a page turn starting mid-turn never jolts.
* ``Pen``: the reveal front, in bars (bar index plus fraction).  It runs toward the frontier F (the bars resolved
  so far) at the music's pace, faster with a backlog (up to PEN_MAX bars a bar), its acceleration and braking
  capped and its speed held to a stopping distance, so it never slams into F.
* ``LinePlan`` (page): the lines of a phrase, planned before their bars are played: as many equal slots as the
  natural bar width allows, a bar that doesn't fit spilling the rest of its line onto the next.
* ``Ruler`` (scroll): positions along the tape: per bar a slot width (frozen once its bar is near, never changed
  after), the actual engraved width once placed, and inline parts (key / time changes) inserted before a bar.
* ``lane_map``: the notes ahead of the pen squeezed into the lane when the playing runs far ahead of the writing.
* ``ring_slices``: a span of a ring buffer as one or two straight pieces.
"""
from __future__ import annotations

import math

import numpy as np

PEN_K = 0.9                       # the pen runs a bar a bar with this much backlog ...
PEN_MAX = 4.0                     # ... and at most this many bars a bar
PEN_TAU = 0.35                    # speeding up: toward the wanted speed with this time constant ...
PEN_A = 2.0                       # ... at most this many bars a bar per second
PEN_D = 6.0                       # braking: at most this
MAX_LINES = 8                     # lines planned at once


def smoothstep(a, b, x):
    """0 at a, 1 at b (a > b runs the other way), Hermite between; floats or arrays."""
    if a == b:
        return (np.asarray(x) >= a).astype(float) if isinstance(x, np.ndarray) else float(x >= a)
    if isinstance(x, np.ndarray):
        t = np.clip((x - a) / (b - a), 0.0, 1.0)
    else:
        t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3.0 - 2.0 * t)


def spring_step(x, v, g, w, dt):
    """One exact step of a critically damped spring (natural frequency w) toward the constant target g."""
    e = x - g
    k = math.exp(-w * dt)
    c = (v + w * e) * dt
    return g + (e + c) * k, (v - w * c) * k


class Glide:
    """A quintic move from the current (x, v, a) to (target, 0, 0) over T seconds; ``to`` re-plans mid-move."""

    def __init__(self, x: float = 0.0) -> None:
        self.x, self.v, self.a = float(x), 0.0, 0.0
        self.target = float(x)
        self._c = None
        self._tau = self._T = 0.0

    @property
    def moving(self) -> bool:
        return self._c is not None

    def snap(self, x1: float | None = None) -> None:
        self.x = self.target = float(self.target if x1 is None else x1)
        self.v = self.a = 0.0
        self._c = None

    def to(self, x1: float, T: float) -> None:
        x1 = float(x1)
        if T <= 1e-6:
            self.snap(x1)
            return
        D, v, a = x1 - self.x, self.v, self.a
        self.target = x1
        self._c = (self.x, v, 0.5 * a,
                   (20 * D - 12 * v * T - 3 * a * T * T) / (2 * T ** 3),
                   (-30 * D + 16 * v * T + 3 * a * T * T) / (2 * T ** 4),
                   (12 * D - 6 * v * T - a * T * T) / (2 * T ** 5))
        self._tau, self._T = 0.0, float(T)

    def step(self, dt: float) -> float:
        if self._c is None:
            return self.x
        self._tau += dt
        if self._tau >= self._T:
            self.snap(self.target)
            return self.x
        t = self._tau
        c0, c1, c2, c3, c4, c5 = self._c
        self.x = c0 + t * (c1 + t * (c2 + t * (c3 + t * (c4 + t * c5))))
        self.v = c1 + t * (2 * c2 + t * (3 * c3 + t * (4 * c4 + t * 5 * c5)))
        self.a = 2 * c2 + t * (6 * c3 + t * (12 * c4 + t * 20 * c5))
        return self.x


class Pen:
    """The reveal front in bars.  ``step(F, R, dt)``: F the frontier (bars resolved from 0), R = 1 / bar seconds."""

    def __init__(self, p: float = 0.0) -> None:
        self.p, self.v, self.active = float(p), 0.0, 0.0

    def step(self, F: float, R: float, dt: float, vcap: float | None = None, fade_s: float = 0.6,
             acap: float | None = None) -> float:
        """``vcap``: a speed limit (bars/s); ``acap``: an acceleration limit (bars/s^2) on top of PEN_A and PEN_D
        (the page keeps the pen's px acceleration bounded when bars are short)."""
        if dt <= 0.0:
            return self.p
        R = max(R, 1e-3)
        b = max(0.0, F - self.p)
        up, dn = PEN_A * R, PEN_D * R
        if acap is not None:
            up, dn = min(up, acap), min(dn, acap)
        want = R * min(PEN_MAX, b / PEN_K, 0.7 * math.sqrt(2.0 * dn * b) / R)
        if want > self.v:
            self.v += min(max((want - self.v) * (1.0 - math.exp(-dt / PEN_TAU)), 0.0), up * dt)
        else:
            self.v = max(want, self.v - dn * dt)
        if vcap is not None:
            self.v = min(self.v, max(0.0, vcap))
        self.p = min(max(F, self.p), self.p + self.v * dt)
        on = self.v > 0.02 * R
        tau = 0.15 if on else max(0.05, fade_s)
        self.active += ((1.0 if on else 0.0) - self.active) * (1.0 - math.exp(-dt / tau))
        return self.p


class PlanLine:
    __slots__ = ("k0", "n", "bw", "hdr_w", "E", "closed")

    def __init__(self, k0: int, n: int, bw: float, hdr_w: float) -> None:
        self.k0, self.n, self.bw, self.hdr_w = k0, n, bw, hdr_w
        self.E = 0.0                       # the placed bars' width beyond their slots (px; signed)
        self.closed = False                # closed short (a spill, or the phrase ended)

    @property
    def end(self) -> float:
        return self.hdr_w + self.n * self.bw + self.E


class LinePlan:
    """A phrase's lines (index j from 0), each ``n`` equal slots of ``bw`` px after a header ``hdr_w`` px wide."""

    def __init__(self) -> None:
        self.lines: list[PlanLine] = []
        self.base = 0                      # the index of lines[0] (older lines are forgotten past MAX_LINES)

    @property
    def last(self) -> int:
        return self.base + len(self.lines) - 1

    def line(self, j: int) -> PlanLine | None:
        i = j - self.base
        return self.lines[i] if 0 <= i < len(self.lines) else None

    def covers(self, k: int) -> bool:
        return bool(self.lines) and k < self.lines[-1].k0 + self.lines[-1].n

    def line_of(self, k: float) -> int:
        """The line holding phrase bar k (the last line past the plan, the first before it)."""
        for i in range(len(self.lines) - 1, -1, -1):
            if self.lines[i].k0 <= k:
                return self.base + i
        return self.base

    def slot_x0(self, j: int, k: int) -> float:
        L = self.line(j)
        return L.hdr_w + (k - L.k0) * L.bw + L.E

    @staticmethod
    def size(cw: float, hdr_w: float, nat_px: float, pad: float, want: float, n_min: int, n_max: int):
        cap = max(1.0, cw - pad - hdr_w)
        n = int(min(n_max, max(n_min, math.floor(cap / max(1.0, want * nat_px)))))
        return n, cap / n

    def plan_next(self, cw: float, hdr_w: float, nat_px: float, pad: float = 0.0, want: float = 1.15,
                  n_min: int = 2, n_max: int = 8) -> int:
        """Plan the next line: slots for bars about ``nat_px`` wide naturally (``want`` of it each)."""
        n, bw = self.size(cw, hdr_w, nat_px, pad, want, n_min, n_max)
        k0 = self.lines[-1].k0 + self.lines[-1].n if self.lines else 0
        self.lines.append(PlanLine(k0, n, bw, hdr_w))
        if len(self.lines) > MAX_LINES:
            self.lines.pop(0)
            self.base += 1
        return self.last

    def resize(self, j: int, cw: float, hdr_w: float, pad: float = 0.0) -> None:
        """The header's real width (before any of the line's bars were asked for): the same slots, re-sized."""
        L = self.line(j)
        if L is not None:
            L.hdr_w = hdr_w
            L.bw = max(1.0, cw - pad - hdr_w) / L.n

    def spill(self, k: int) -> int:
        """Bar k doesn't fit its line: the line closes before it and every later line starts that much earlier.
        Returns the bars moved (0: k is its line's first bar)."""
        j = self.line_of(k)
        L = self.line(j)
        if L is None or k <= L.k0:
            return 0
        s = L.k0 + L.n - k
        if s <= 0:
            return 0
        L.n = k - L.k0
        L.closed = True
        for M in self.lines[j - self.base + 1:]:
            M.k0 -= s
        return s

    def close(self, k_end: int) -> None:
        """The phrase ended with bar k_end - 1: its line closes there and lines planned after it are dropped."""
        j = self.line_of(max(0, k_end - 1))
        L = self.line(j)
        if L is None:
            return
        L.n = max(0, k_end - L.k0)
        L.closed = True
        del self.lines[j - self.base + 1:]


class Ruler:
    """Tape positions (px) of a phrase: inline part [U_p, U_p + w_inl), then bars from U_b0 = U_p + w_inl.  Bar k is
    act[k] wide once placed, else its frozen slot w[k], else ``w_prov``; ins[k] is an inline part before bar k."""

    def __init__(self, U_p: float, w_inl: float, w_prov: float) -> None:
        self.U_p, self.w_inl, self.w_prov = float(U_p), float(w_inl), float(w_prov)
        self.w: list = []                  # frozen slot widths (None: not yet)
        self.act: list = []                # placed widths (None: not yet)
        self.ins: list = []                # inline insert before bar k (px)
        self._cum = np.zeros(1)
        self._dirty = True

    @property
    def U_b0(self) -> float:
        return self.U_p + self.w_inl

    def _grow(self, k: int) -> None:
        while len(self.w) <= k:
            self.w.append(None)
            self.act.append(None)
            self.ins.append(0.0)

    def frozen(self, k: int) -> bool:
        return 0 <= k < len(self.w) and self.w[k] is not None

    def width(self, k: int) -> float:
        if 0 <= k < len(self.w):
            if self.act[k] is not None:
                return self.act[k]
            if self.w[k] is not None:
                return self.w[k]
        return self.w_prov

    def set_inline(self, w_inl: float) -> None:
        self.w_inl = float(w_inl)

    def set_prov(self, w_prov: float) -> None:
        if abs(w_prov - self.w_prov) > 1e-9:
            self.w_prov = float(w_prov)
            self._dirty = True

    def freeze(self, k: int, w: float) -> bool:
        self._grow(k)
        if self.w[k] is not None:
            return False
        self.w[k] = float(w)
        self._dirty = True
        return True

    def insert(self, k: int, width: float) -> None:
        self._grow(k)
        self.ins[k] += float(width)
        self._dirty = True

    def set_insert(self, k: int, width: float) -> None:
        """An inline part's real width (it was inserted with an estimate)."""
        self._grow(k)
        self.ins[k] = float(width)
        self._dirty = True

    def place(self, k: int, width: float) -> None:
        self._grow(k)
        self.act[k] = float(width)
        self._dirty = True

    def _update(self) -> None:
        if self._dirty:
            n = len(self.w)
            wid = np.array([self.width(k) for k in range(n)], float) if n else np.zeros(0)
            ins = np.array(self.ins, float) if n else np.zeros(0)
            c = np.zeros(n + 1)
            if n:
                c[1:] = np.cumsum(wid + ins)
            self._cum, self._ins = c, ins
            self._dirty = False

    def u0(self, k: int) -> float:
        """The left edge of bar k (after its inline insert)."""
        if k < 0:
            return self.U_p
        self._update()
        n = len(self.w)
        if k < n:
            return self.U_b0 + self._cum[k] + self._ins[k]
        return self.U_b0 + self._cum[n] + (k - n) * self.w_prov

    def u(self, p: float) -> float:
        """Position p in bars (the inline part is bar -1; an insert is crossed at the start of its bar) -> tape px."""
        if p < 0.0:
            return self.U_p + max(0.0, p + 1.0) * self.w_inl
        k = int(math.floor(p))
        ins = self.ins[k] if k < len(self.ins) else 0.0
        return self.u0(k) - ins + (p - k) * (ins + self.width(k))

    def end(self) -> float:
        """The right edge of the last bar with a slot."""
        return self.u0(len(self.w))


def lane_map(d, D: float, Lw: float, knee: float | None = None):
    """Distance ahead of the pen d -> screen distance f, so the newest note (D ahead) lands at the lane's end Lw;
    the identity near the pen (f(0) = 0, f'(0) = 1), squeezed by c beyond a knee (0.3 Lw, or ``knee`` up to
    0.9 Lw: a fast scroll wants a wider one).  Returns (f, c)."""
    if D <= Lw:
        return d, 1.0
    k = min(0.3 * Lw if knee is None else min(0.9 * Lw, max(0.3 * Lw, knee)), 0.5 * D)
    c = (Lw - 0.5 * k) / (D - 0.5 * k)
    if isinstance(d, np.ndarray):
        f = np.where(d <= k, d - (1.0 - c) * d * d / (2.0 * k), 0.5 * k * (1.0 + c) + c * (d - k))
    else:
        f = d - (1.0 - c) * d * d / (2.0 * k) if d <= k else 0.5 * k * (1.0 + c) + c * (d - k)
    return f, c


def ring_slices(u: int, w: int, TW: int) -> list[tuple[int, int, int]]:
    """[u, u + w) on a ring of TW columns as (dst column, source offset, width) pieces: one, or two at the seam."""
    u, w = int(u), int(min(w, TW))
    if w <= 0:
        return []
    s = u % TW
    if s + w <= TW:
        return [(s, 0, w)]
    a = TW - s
    return [(s, 0, a), (0, a, w - a)]
