"""Small statistics helpers."""
from __future__ import annotations

import collections
import math
from typing import Iterable, Optional


def pct(values: Iterable[float], p: float) -> Optional[float]:
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


class Ring:
    """The last ``n`` samples, for percentiles."""

    def __init__(self, n: int):
        self.values: collections.deque[float] = collections.deque(maxlen=n)

    def add(self, v: float) -> None:
        self.values.append(v)

    def summary(self) -> dict:
        vals = list(self.values)
        r = lambda v: None if v is None else round(v, 3)  # noqa: E731
        return {"p50": r(pct(vals, 50)), "p95": r(pct(vals, 95)), "max": r(max(vals) if vals else None),
                "n": len(vals)}
