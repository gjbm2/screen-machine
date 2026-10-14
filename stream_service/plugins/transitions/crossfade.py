"""Crossfade: a per-channel blend in sRGB-encoded space, like CSS opacity (so it matches a page fade)."""
from __future__ import annotations

from ..base import Transition


class Crossfade(Transition):
    name = "crossfade"
    label = "Crossfade"
    description = "Blend from one picture to the other (sRGB-space mix, like CSS opacity)."
    default_duration_s = 3.0

    def render(self, progress: float, a, b, out) -> None:
        self.ctx.kernels.crossfade(a, b, out, progress)
