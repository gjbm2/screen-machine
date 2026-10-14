"""Transition easing curves, matching CSS's named timing functions (cubic-bezier)."""
from __future__ import annotations

from functools import lru_cache

# CSS Easing Functions Level 1
_BEZIER = {
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
    "ease-in": (0.42, 0.0, 1.0, 1.0),
    "ease-out": (0.0, 0.0, 0.58, 1.0),
}


def _bezier(p1: float, p2: float, s: float) -> float:
    # one coordinate of a cubic Bezier with P0 = 0 and P3 = 1
    return 3 * p1 * (1 - s) ** 2 * s + 3 * p2 * (1 - s) * s * s + s ** 3


def _bezier_d(p1: float, p2: float, s: float) -> float:
    return 3 * p1 * (1 - s) ** 2 + 6 * (p2 - p1) * (1 - s) * s + 3 * (1 - p2) * s * s


@lru_cache(maxsize=4096)
def _solve(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    # solve x(s) = x for s by Newton, falling back to bisection, then return y(s)
    s = x
    for _ in range(8):
        err = _bezier(x1, x2, s) - x
        if abs(err) < 1e-7:
            return _bezier(y1, y2, s)
        d = _bezier_d(x1, x2, s)
        if abs(d) < 1e-6:
            break
        s -= err / d
    lo, hi = 0.0, 1.0
    s = x
    for _ in range(40):
        v = _bezier(x1, x2, s)
        if abs(v - x) < 1e-7:
            break
        if v < x:
            lo = s
        else:
            hi = s
        s = (lo + hi) / 2
    return _bezier(y1, y2, s)


def ease(name: str, p: float) -> float:
    """Map linear progress p in [0, 1] through the named curve."""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    if name == "linear" or name not in _BEZIER:
        return p
    x1, y1, x2, y2 = _BEZIER[name]
    return _solve(x1, y1, x2, y2, round(p, 6))
