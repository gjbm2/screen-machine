"""Cut: show ``a`` until the transition ends, then ``b``.  With the default duration 0 it switches on the
transition's first frame."""
from __future__ import annotations

from ..base import Transition


class Cut(Transition):
    name = "cut"
    label = "Cut"
    description = "Hard cut (after an optional delay of duration_s)."
    default_duration_s = 0.0

    def needs(self, progress: float) -> tuple[bool, bool]:
        return progress < 1.0, progress >= 1.0   # only `a` until the end: nothing to blend

    def render(self, progress: float, a, b, out) -> None:
        out[...] = b if progress >= 1.0 else a
