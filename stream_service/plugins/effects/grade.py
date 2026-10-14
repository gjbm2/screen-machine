"""Grade: a colour grade (saturation, contrast, warmth, lift) mixed in by amount.

Source: Original
Author: screen-machine renderer
License: MIT
license_class: permissive
"""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from .._cuda import HELPERS, launch_2d
from ..base import Effect, effect_params

_SRC = HELPERS + r"""
extern "C" __global__
void grade(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H, const float amount,
           const float sat, const float contrast, const float warmth, const float lift, const float gain)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const uchar4 p = src[y * W + x];
    float r = p.x / 255.f, g = p.y / 255.f, b = p.z / 255.f;
    const float l = 0.2126f * r + 0.7152f * g + 0.0722f * b;
    r = l + sat * (r - l); g = l + sat * (g - l); b = l + sat * (b - l);
    r = 0.5f + contrast * (r - 0.5f); g = 0.5f + contrast * (g - 0.5f); b = 0.5f + contrast * (b - 0.5f);
    r += 0.12f * warmth; b -= 0.12f * warmth; g += 0.02f * warmth;
    r = lift + (1.f - lift) * r * gain; g = lift + (1.f - lift) * g * gain; b = lift + (1.f - lift) * b * gain;
    const float4 q = make_float4(255.f * clamp01(r), 255.f * clamp01(g), 255.f * clamp01(b), 255.f);
    out[y * W + x] = pack(mix4(make_float4(p.x, p.y, p.z, 255.f), q, amount));
}
"""


class Grade(Effect):
    name = "grade"
    label = "Colour grade"
    description = "A film-style grade: saturation, contrast, warmth and lift, faded in by amount."
    stage = "colour"
    tags = {"family": ["colour"], "dims": ["2d"], "technique": ["colour"]}
    params = {
        **effect_params(1.0),
        "saturation": {"type": "number", "minimum": 0.0, "maximum": 2.5, "default": 1.2, "x-tier": "regional",
                       "x-ui": {"label": "Saturation", "step": 0.01, "widget": "slider", "basic": True}},
        "contrast": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.1, "x-tier": "regional",
                     "x-ui": {"label": "Contrast", "step": 0.01, "widget": "slider"}},
        "warmth": {"type": "number", "minimum": -1.0, "maximum": 1.0, "default": 0.0, "x-tier": "regional",
                   "x-ui": {"label": "Warmth", "step": 0.01, "widget": "slider", "basic": True}},
        "lift": {"type": "number", "minimum": 0.0, "maximum": 0.3, "default": 0.0, "x-tier": "regional",
                 "x-ui": {"label": "Lift (blacks)", "step": 0.005, "widget": "slider"}},
        "flash": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0, "x-tier": "regional",
                  "x-ui": {"label": "Beat brightness", "step": 0.01, "widget": "slider"}},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("grade")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        gain = 1.0 + 0.35 * p["flash"] * p["pulse"]
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (src, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(p["amount"]),
                   np.float32(p["saturation"]), np.float32(p["contrast"]), np.float32(p["warmth"]),
                   np.float32(p["lift"]), np.float32(gain)))
