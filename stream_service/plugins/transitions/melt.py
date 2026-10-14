"""Screen melt (Doom, 1993): the old picture slides down in narrow columns, each starting a little after its
neighbour (a random walk of start delays), falling under gravity to reveal the new one behind it."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void melt(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
          const float* __restrict__ delay, const int W, const int H, const float p, const int col_px,
          const float spread, const float shade)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float t = clamp01((p - delay[x / col_px]) / (1.f - spread));
    const float off = H * (0.12f * t + 0.88f * t * t);          // a small push, then gravity
    const int ys = y - (int)off;
    if (ys < 0) {                                                // uncovered: the new picture
        float4 c = ld(b, x, y, W, H);
        const float d = (off - y) / (0.04f * H);                 // a soft shadow under the falling column
        const float k = 1.f - shade * (1.f - clamp01(d)) * (t < 1.f ? 1.f : 0.f);
        out[y * W + x] = pack(make_float4(c.x * k, c.y * k, c.z * k, 255.f));
    } else {
        out[y * W + x] = a[ys * W + x];
    }
}
"""


class Melt(Transition):
    name = "melt"
    label = "Screen melt"
    description = "The old picture melts down the screen in columns (Doom-style), revealing the new one."
    default_duration_s = 2.0
    params = {
        "column_px": {"type": "integer", "minimum": 1, "maximum": 128, "default": 8,
                      "x-ui": {"label": "Column width", "unit": "px", "widget": "slider"}},
        "spread": {"type": "number", "minimum": 0.0, "maximum": 0.8, "default": 0.35,
                   "x-ui": {"label": "Ragged start", "step": 0.01, "widget": "slider"}},
        "roughness": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.25,
                      "x-ui": {"label": "Neighbour jitter", "step": 0.01, "widget": "slider"}},
        "shade": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.35,
                  "x-ui": {"label": "Shadow", "step": 0.01, "widget": "slider"}},
        "seed": {"type": "integer", "minimum": -1, "maximum": 2**31 - 1, "default": -1,
                 "x-ui": {"label": "Seed (-1 random)"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        p = self.params
        cols = (ctx.width + p["column_px"] - 1) // p["column_px"]
        rng = np.random.default_rng(None if p["seed"] < 0 else p["seed"])
        # Doom's walk: each column's delay is its neighbour's plus a small random step, clamped to [0, spread].
        step = p["spread"] * p["roughness"] * 0.25
        d = np.empty(cols, np.float32)
        d[0] = rng.uniform(0, p["spread"])
        for i in range(1, cols):
            d[i] = min(max(d[i - 1] + rng.choice((-1.0, 0.0, 1.0)) * step, 0.0), p["spread"])
        self._delay = cp.asarray(d)
        self._k = raw_module(_SRC).get_function("melt")

    def render(self, progress: float, a, b, out) -> None:
        p = self.params
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, self._delay, np.int32(self.ctx.width), np.int32(self.ctx.height),
                   np.float32(progress), np.int32(p["column_px"]), np.float32(p["spread"]),
                   np.float32(p["shade"])))
