"""Zoom punch: rush into the old picture (radial motion blur), swap at the peak, and settle out of the new one."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void zoom(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
          const int W, const int H, const float p, const float depth, const float cx, const float cy,
          const float blur, const int taps)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float k = depth - 1.f;
    const float sa = 1.f + k * smooth(0.f, 0.6f, p);          // scale on the way in (old picture)
    const float sb = 1.f + k * (1.f - smooth(0.4f, 1.f, p));  // and on the way out (new picture)
    const float wb = smooth(0.42f, 0.58f, p);
    const float bl = blur * sinf(3.14159265f * p);            // streak length peaks at the swap
    const float ox = cx * W, oy = cy * H;
    const float dx = x + 0.5f - ox, dy = y + 0.5f - oy;
    float4 acc = make_float4(0.f, 0.f, 0.f, 255.f);
    for (int i = 0; i < taps; ++i) {
        const float f = 1.f - bl * 0.12f * (taps > 1 ? (float)i / (taps - 1) : 0.f);
        float4 c = make_float4(0.f, 0.f, 0.f, 255.f);
        if (wb < 1.f) { const float s = 1.f / (sa * f); c = bilin(a, ox + dx * s, oy + dy * s, W, H); }
        if (wb > 0.f) {
            const float s = 1.f / (sb * f);
            const float4 cb = bilin(b, ox + dx * s, oy + dy * s, W, H);
            c = wb < 1.f ? mix4(c, cb, wb) : cb;
        }
        acc.x += c.x; acc.y += c.y; acc.z += c.z;
    }
    const float inv = 1.f / taps;
    out[y * W + x] = pack(make_float4(acc.x * inv, acc.y * inv, acc.z * inv, 255.f));
}
"""


class Zoom(Transition):
    name = "zoom"
    label = "Zoom punch"
    description = "Rush into the old picture, swap at the peak, settle back out of the new one."
    default_duration_s = 1.2
    params = {
        "depth": {"type": "number", "minimum": 1.0, "maximum": 8.0, "default": 3.0,
                  "x-ui": {"label": "Zoom depth", "step": 0.1, "unit": "×", "widget": "slider"}},
        "center_x": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
                     "x-ui": {"label": "Centre x", "step": 0.01, "widget": "slider"}},
        "center_y": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.5,
                     "x-ui": {"label": "Centre y", "step": 0.01, "widget": "slider"}},
        "blur": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7,
                 "x-ui": {"label": "Motion blur", "step": 0.01, "widget": "slider"}},
        "taps": {"type": "integer", "minimum": 1, "maximum": 16, "default": 8,
                 "x-ui": {"label": "Blur samples", "widget": "slider"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("zoom")

    def render(self, progress: float, a, b, out) -> None:
        p = self.params
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(progress),
                   np.float32(p["depth"]), np.float32(p["center_x"]), np.float32(p["center_y"]),
                   np.float32(p["blur"]), np.int32(p["taps"])))
