"""Calibration flash: full-frame black or white, for timing renderer -> photon (tools/room/latency_cal).

``white`` (0/1) sets the level now. ``at_ns`` schedules a flip: from the first frame rendered at or after that
wall-clock time (``time.time_ns()`` on this host), the scene shows ``then`` instead. Neither is eased, so a
change lands on the next frame. Each flip is logged with its render time. Never picked by a director (its
preset is tagged ``auto: false``); keep flips at least 1 s apart (photosafe)."""
from __future__ import annotations

import logging
import time

from ..base import Source

log = logging.getLogger("stream_service.plugins.cal_flash")

WHITE, BLACK = (255, 255, 255), (0, 0, 0)


class CalFlash(Source):
    name = "cal_flash"
    label = "Calibration flash"
    description = "Full-frame black or white on command, for latency calibration."
    params = {
        "white": {"type": "integer", "minimum": 0, "maximum": 1, "default": 0, "x-ease": False,
                  "x-ui": {"label": "White", "widget": "slider"}},
        "at_ns": {"type": "integer", "minimum": 0, "default": 0, "x-ease": False,
                  "x-ui": {"label": "Flip at (wall-clock ns, 0 = off)"}},
        "then": {"type": "integer", "minimum": 0, "maximum": 1, "default": 1, "x-ease": False,
                 "x-ui": {"label": "Level after the flip", "widget": "slider"}},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self.shown = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        now_ns = time.time_ns()
        white = int(p["then"]) if 0 < int(p["at_ns"]) <= now_ns else int(p["white"])
        if white != self.shown:
            if self.shown is not None:
                log.info("cal_flash flip to %s: render_ns=%d at_ns=%d t_local=%.4f",
                         "white" if white else "black", now_ns, int(p["at_ns"]), t_local)
            self.shown = white
        self.ctx.kernels.fill(out, WHITE if white else BLACK)
