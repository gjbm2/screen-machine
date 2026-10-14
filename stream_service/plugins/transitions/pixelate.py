"""Pixelate: the old picture breaks into growing blocks, swaps at the coarsest point, and resolves into the new."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
__device__ __forceinline__ float4 block_px(const uchar4* __restrict__ img, int x, int y, int bs, int W, int H) {
    if (bs <= 1) return ld(img, x, y, W, H);
    const int bx = (x / bs) * bs + bs / 2, by = (y / bs) * bs + bs / 2;   // sample each block's centre
    return ld(img, bx, by, W, H);
}
extern "C" __global__
void pixelate(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
              const int W, const int H, const int bs_a, const int bs_b, const float wb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float4 c;
    if (wb <= 0.f) c = block_px(a, x, y, bs_a, W, H);
    else if (wb >= 1.f) c = block_px(b, x, y, bs_b, W, H);
    else c = mix4(block_px(a, x, y, bs_a, W, H), block_px(b, x, y, bs_b, W, H), wb);
    out[y * W + x] = pack(c);
}
"""


class Pixelate(Transition):
    name = "pixelate"
    label = "Pixelate"
    description = "Break the old picture into blocks, swap at the coarsest point, resolve into the new one."
    default_duration_s = 1.5
    params = {
        "max_block": {"type": "number", "minimum": 0.005, "maximum": 0.5, "default": 0.06,
                      "x-ui": {"label": "Coarsest block", "step": 0.005, "unit": "× height", "widget": "slider"}},
        "steps": {"type": "integer", "minimum": 0, "maximum": 64, "default": 12,
                  "x-ui": {"label": "Steps (0 = smooth)", "widget": "slider"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("pixelate")

    def _block(self, t: float) -> int:
        """t in [0, 1]: 0 = sharp, 1 = coarsest; geometric growth so it reads as even."""
        p = self.params
        steps = p["steps"]
        if steps:
            t = round(t * steps) / steps
        big = max(2.0, p["max_block"] * self.ctx.height)
        return max(1, int(round(big ** t)))

    def render(self, progress: float, a, b, out) -> None:
        def sm(e0, e1, x):
            t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
            return t * t * (3 - 2 * t)
        bs_a = self._block(min(progress / 0.5, 1.0))
        bs_b = self._block(min((1.0 - progress) / 0.5, 1.0))
        wb = sm(0.45, 0.55, progress)
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.int32(bs_a), np.int32(bs_b),
                   np.float32(wb)))
