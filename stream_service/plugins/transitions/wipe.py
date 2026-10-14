"""Wipes: iris (circle), clock (radial sweep), diamond, or a straight edge at any angle, with a soft edge."""
from __future__ import annotations

import math

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

SHAPES = ("iris", "clock", "diamond", "linear")

_SRC = HELPERS + r"""
extern "C" __global__
void wipe(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
          const int W, const int H, const float p, const int shape, const float soft,
          const float cx, const float cy, const float ux, const float uy, const float lo, const float span)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = (x + 0.5f) / H, py = (y + 0.5f) / H;     // height-normalised so circles stay round
    const float dx = px - cx * W / H, dy = py - cy;
    float d;                                                  // 0 = first revealed, 1 = last
    if (shape == 0) {                                         // iris: distance / farthest corner
        const float fx = fmaxf(cx, 1.f - cx) * W / H, fy = fmaxf(cy, 1.f - cy);
        d = sqrtf(dx * dx + dy * dy) / sqrtf(fx * fx + fy * fy);
    } else if (shape == 1) {                                  // clock: angle from 12 o'clock, clockwise
        d = (atan2f(dx, -dy) + 3.14159265f) / 6.2831853f;
        d = d - floorf(d + 0.5f) + 0.5f;                     // start at 12
    } else if (shape == 2) {
        const float fx = fmaxf(cx, 1.f - cx) * W / H, fy = fmaxf(cy, 1.f - cy);
        d = (fabsf(dx) + fabsf(dy)) / (fx + fy);
    } else {
        d = ((px * ux + py * uy) - lo) / span;               // projection onto the wipe direction
    }
    const float e = p * (1.f + soft);                         // front moves 0 -> 1 + soft
    const float w = 1.f - smooth(e - soft, e, d);
    out[y * W + x] = pack(mix4(ld(a, x, y, W, H), ld(b, x, y, W, H), w));
}
"""


class Wipe(Transition):
    name = "wipe"
    label = "Wipe"
    description = "Reveal the new picture with an iris, a clock sweep, a diamond or a straight edge."
    default_duration_s = 1.5
    params = {
        "shape": {"type": "string", "enum": list(SHAPES), "default": "iris",
                  "x-ui": {"label": "Shape", "widget": "select"}},
        "softness": {"type": "number", "minimum": 0.0, "maximum": 0.5, "default": 0.04,
                     "x-ui": {"label": "Edge softness", "step": 0.005, "widget": "slider"}},
        "center_x": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
                     "x-ui": {"label": "Centre x", "step": 0.01, "widget": "slider"}},
        "center_y": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
                     "x-ui": {"label": "Centre y", "step": 0.01, "widget": "slider"}},
        "angle": {"type": "number", "minimum": -180.0, "maximum": 180.0, "default": 0.0,
                  "x-ui": {"label": "Angle (linear)", "step": 1, "unit": "°", "widget": "slider"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("wipe")

    def render(self, progress: float, a, b, out) -> None:
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        ang = math.radians(p["angle"])
        ux, uy = math.cos(ang), math.sin(ang)                 # 0 deg: left -> right
        corners = [cx * ux + cy * uy for cx in (0.0, w / h) for cy in (0.0, 1.0)]
        lo, span = min(corners), max(corners) - min(corners)
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(progress), np.int32(SHAPES.index(p["shape"])),
                   np.float32(p["softness"]), np.float32(p["center_x"]), np.float32(p["center_y"]),
                   np.float32(ux), np.float32(uy), np.float32(lo), np.float32(span)))
