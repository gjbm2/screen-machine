"""Chroma: radial RGB split (lens-style chromatic aberration) that punches on the beat.  Reference effect."""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + r"""
extern "C" __global__
void chroma(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H, const float k)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H, dx = x + 0.5f - cx, dy = y + 0.5f - cy;
    const float r = bilin(src, cx + dx * (1.f + k), cy + dy * (1.f + k), W, H).x;
    const float g = ld(src, x, y, W, H).y;
    const float b = bilin(src, cx + dx * (1.f - k), cy + dy * (1.f - k), W, H).z;
    out[y * W + x] = pack(make_float4(r, g, b, 255.f));
}
"""


class Chroma(Effect):
    name = "chroma"
    label = "Chroma split"
    description = "Radial RGB split, like a cheap lens; widens with amount and punches on the beat."
    params = {
        **standard_effect_params(),
        "spread": num(0.0, 0.05, 0.008, "Split at amount 1", step=0.001, tier="regional"),
        "punch": num(0.0, 4.0, 1.5, "Beat punch", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("chroma")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        k = p["spread"] * p["amount"] * (1.0 + p["punch"] * p["pulse"])
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (src, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(k)))
