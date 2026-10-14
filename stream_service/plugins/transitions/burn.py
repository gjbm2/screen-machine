"""Burn: a noise-shaped front eats the old picture, with a glowing edge and a charred rim ahead of it."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition, color_to_rgb

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void burn(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
          const int W, const int H, const float p, const float scale, const float edge,
          const float gr, const float gg, const float gb, const float glow, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float inv = 1.f / (scale * H);
    const float n = clamp01((fbm(x * inv, y * inv, seed) - 0.18f) / 0.64f);   // stretch fbm to ~[0, 1]
    const float front = p * (1.f + 2.f * edge) - edge;
    const float s = (n - (front - edge)) / (2.f * edge);                       // 0: burnt through, 1: untouched
    if (s <= 0.f) { out[y * W + x] = b[y * W + x]; return; }
    float4 c = ld(a, x, y, W, H);
    if (s >= 1.f) { out[y * W + x] = pack(c); return; }
    if (s < 0.5f) c = ld(b, x, y, W, H);
    else {                                                                      // char the rim ahead of the flame
        const float k = 0.25f + 0.75f * smooth(0.5f, 1.f, s);
        c = make_float4(c.x * k, c.y * k, c.z * k, 255.f);
    }
    const float g = glow * powf(1.f - fabsf(s - 0.5f) * 2.f, 2.f);
    out[y * W + x] = pack(make_float4(c.x + gr * g, c.y + gg * g, c.z + gb * g, 255.f));
}
"""


class Burn(Transition):
    name = "burn"
    label = "Burn"
    description = "A ragged burning edge eats through the old picture to the new one."
    default_duration_s = 2.5
    params = {
        "scale": {"type": "number", "minimum": 0.02, "maximum": 2.0, "default": 0.35,
                  "x-ui": {"label": "Flame size", "step": 0.01, "unit": "× height", "widget": "slider"}},
        "edge": {"type": "number", "minimum": 0.005, "maximum": 0.3, "default": 0.05,
                 "x-ui": {"label": "Edge width", "step": 0.005, "widget": "slider"}},
        "glow_color": {"type": "string", "format": "color", "default": "#ffa040",
                       "x-ui": {"label": "Glow colour", "widget": "color"}},
        "glow": {"type": "number", "minimum": 0.0, "maximum": 2.0, "default": 1.0,
                 "x-ui": {"label": "Glow", "step": 0.05, "widget": "slider"}},
        "seed": {"type": "integer", "minimum": -1, "maximum": 2**31 - 1, "default": -1,
                 "x-ui": {"label": "Seed (-1 random)"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        s = self.params["seed"]
        self._seed = np.uint32(np.random.default_rng().integers(0, 2**31) if s < 0 else s)
        self._glow = color_to_rgb(self.params["glow_color"])
        self._k = raw_module(_SRC).get_function("burn")

    def render(self, progress: float, a, b, out) -> None:
        p, (r, g, bl) = self.params, self._glow
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(progress),
                   np.float32(p["scale"]), np.float32(p["edge"]), np.float32(r), np.float32(g), np.float32(bl),
                   np.float32(p["glow"]), self._seed))
