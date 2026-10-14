"""Shake: a camera shake / vibration on the whole picture, swaying gently and kicking on the beat.

The offset is smooth noise in time (sums of incommensurate sines on an integrated phase), scaled by
``amount x (sway + punch x pulse)``, so it is bounded, decays with the beat envelope and never jitters frame to
frame.  The picture is zoomed in just enough to keep the edges covered.  amount 0 = identity.

Source: Original
Author: screen-machine renderer
License: MIT
license_class: permissive
"""
from __future__ import annotations

import math

import numpy as np

from ...gpu import raw_module
from .._cuda import HELPERS, launch_2d
from ..base import Effect, effect_params

_SRC = HELPERS + r"""
extern "C" __global__
void shake(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
           const float ox, const float oy, const float c, const float s, const float inv_zoom)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float dx = (x + 0.5f - cx) * inv_zoom, dy = (y + 0.5f - cy) * inv_zoom;
    const float sx = cx + c * dx - s * dy + ox, sy = cy + s * dx + c * dy + oy;
    out[y * W + x] = pack(bilin(src, sx, sy, W, H));
}
"""


def _wobble(t: float, k: int) -> float:
    """Smooth noise in about [-1, 1] (three incommensurate sines, a different set per axis)."""
    a = k * 1.7
    return (0.55 * math.sin(2 * math.pi * (1.00 * t) + a) + 0.30 * math.sin(2 * math.pi * (2.31 * t) + 2 * a)
            + 0.15 * math.sin(2 * math.pi * (4.07 * t) + 3 * a))


class Shake(Effect):
    name = "shake"
    label = "Shake"
    description = "Camera shake: a gentle sway that kicks on the beat; bounded and smooth, edges stay covered."
    stage = "motion"
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["displacement"]}
    params = {
        **effect_params(0.6),
        "max_shift": {"type": "number", "minimum": 0.0, "maximum": 0.06, "default": 0.012, "x-tier": "regional",
                      "x-ui": {"label": "Max shift (of width)", "step": 0.001, "widget": "slider", "basic": True}},
        "max_rotate": {"type": "number", "minimum": 0.0, "maximum": 6.0, "default": 0.8, "x-tier": "regional",
                       "x-ui": {"label": "Max rotation", "unit": "°", "step": 0.1, "widget": "slider"}},
        "sway": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.2, "x-tier": "regional",
                 "x-ui": {"label": "Idle sway", "step": 0.01, "widget": "slider"}},
        "punch": {"type": "number", "minimum": 0.0, "maximum": 3.0, "default": 1.0, "x-tier": "regional",
                  "x-ui": {"label": "Beat punch", "step": 0.05, "widget": "slider"}},
        "frequency": {"type": "number", "minimum": 0.2, "maximum": 12.0, "default": 3.0, "x-tier": "regional",
                      "x-ui": {"label": "Shake rate", "unit": "Hz", "step": 0.1, "widget": "slider"}},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("shake")
        self._phase = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        drive = p["amount"] * min(1.5, p["sway"] + p["punch"] * p["pulse"])
        # the rate rises with the kick, so a beat reads as a jolt without the position jumping
        self._phase += dt * p["frequency"] * (1.0 + 0.6 * p["pulse"])
        w, h = self.ctx.width, self.ctx.height
        shift = drive * p["max_shift"] * w
        ox, oy = shift * _wobble(self._phase, 0), shift * 0.75 * _wobble(self._phase, 1)
        ang = math.radians(drive * p["max_rotate"]) * _wobble(self._phase, 2)
        # zoom so the shifted / rotated picture still covers the frame
        need = (abs(ox) + abs(oy)) / (0.5 * min(w, h)) + abs(math.sin(ang)) * (w / h)
        zoom = 1.0 + need
        launch_2d(self._k, w, h, (src, out, np.int32(w), np.int32(h), np.float32(ox), np.float32(oy),
                                  np.float32(math.cos(ang)), np.float32(math.sin(ang)), np.float32(1.0 / zoom)))
