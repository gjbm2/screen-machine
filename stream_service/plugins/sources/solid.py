"""Solid colour (tests, black slates).  The colour is a string parameter, so it is not eased: a live colour
change switches at the next frame (use a scene change with a crossfade for a fade)."""
from __future__ import annotations

from ..base import Source, color_to_rgb


class Solid(Source):
    name = "solid"
    label = "Solid colour"
    description = "A single flat colour."
    params = {
        "rgb": {"type": "string", "format": "color", "default": "#000000",
                "x-ui": {"label": "Colour", "widget": "color"}},
    }

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self.ctx.kernels.fill(out, color_to_rgb(p["rgb"]))

    @property
    def static(self) -> bool:
        return True
