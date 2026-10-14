"""Flash: a beat-driven light flash (towards white or a tint), the classic strobe-on-the-kick look.

The flash level is ``amount x (glow + strength x pulse)``: with a decaying beat envelope it is a musical
flash, never a hard strobe.  amount 0 = identity.

Source: Original
Author: screen-machine renderer
License: MIT
license_class: permissive
"""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from .._cuda import HELPERS, launch_2d
from ..base import Effect, color_to_rgb, effect_params

_SRC = HELPERS + r"""
extern "C" __global__
void flash(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H, const float k,
           const float tr, const float tg, const float tb, const float screen)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 p = src[y * W + x];
    const float4 c = make_float4(p.x, p.y, p.z, 255.f);
    // screen blend keeps the picture readable; additive (screen = 0) is the harder look
    const float4 add = make_float4(c.x + k * tr, c.y + k * tg, c.z + k * tb, 255.f);
    const float4 scr = make_float4(c.x + k * tr * (1.f - c.x / 255.f), c.y + k * tg * (1.f - c.y / 255.f),
                                   c.z + k * tb * (1.f - c.z / 255.f), 255.f);
    out[y * W + x] = pack(mix4(add, scr, screen));
}
"""


class Flash(Effect):
    name = "flash"
    label = "Beat flash"
    description = "Brightens the picture on every beat (towards white or a tint); a soft glow between beats."
    stage = "light"
    tags = {"family": ["light"], "dims": ["2d"], "technique": ["light"]}
    params = {
        **effect_params(0.6),
        "strength": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.45, "x-tier": "regional",
                     "x-ui": {"label": "Flash strength", "step": 0.01, "widget": "slider", "basic": True}},
        "glow": {"type": "number", "minimum": 0.0, "maximum": 0.5, "default": 0.0, "x-tier": "regional",
                 "x-ui": {"label": "Glow between beats", "step": 0.01, "widget": "slider"}},
        "tint": {"type": "string", "format": "color", "default": "#ffffff",
                 "x-ui": {"label": "Tint", "widget": "color"}},
        "screen": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.8, "x-tier": "regional",
                   "x-ui": {"label": "Soft (screen) vs hard (add)", "step": 0.01, "widget": "slider"}},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("flash")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        k = p["amount"] * min(1.0, p["glow"] + p["strength"] * p["pulse"])
        r, g, b = color_to_rgb(p["tint"])
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (src, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(k),
                   np.float32(r), np.float32(g), np.float32(b), np.float32(p["screen"])))
