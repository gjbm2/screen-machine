"""Luma reveal: the new picture appears through its own brightest parts first (or darkest, inverted)."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void luma_reveal(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                 const int W, const int H, const float p, const float soft, const int invert, const int key_a)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 ca = ld(a, x, y, W, H), cb = ld(b, x, y, W, H);
    float l = luma(key_a ? ca : cb);
    if (invert) l = 1.f - l;
    const float th = 1.f - p * (1.f + soft);                  // 1 -> -soft
    out[y * W + x] = pack(mix4(ca, cb, smooth(th, th + soft, l)));
}
"""


class Luma(Transition):
    name = "luma"
    label = "Luma reveal"
    description = "The new picture shows through its brightest parts first, then everything else."
    default_duration_s = 2.5
    params = {
        "softness": {"type": "number", "minimum": 0.01, "maximum": 1.0, "default": 0.2,
                     "x-ui": {"label": "Softness", "step": 0.01, "widget": "slider"}},
        "invert": {"type": "boolean", "default": False, "x-ui": {"label": "Darkest first"}},
        "key": {"type": "string", "enum": ["new", "old"], "default": "new",
                "x-ui": {"label": "Key from", "widget": "select"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("luma_reveal")

    def render(self, progress: float, a, b, out) -> None:
        p = self.params
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(progress),
                   np.float32(p["softness"]), np.int32(p["invert"]), np.int32(p["key"] == "old")))
