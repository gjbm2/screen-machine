"""Glitch: torn horizontal slices jump sideways with an RGB split, flickering between the two pictures."""
from __future__ import annotations

import numpy as np

from ...gpu import raw_module
from ..base import Transition

from .._cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void glitch(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
            const int W, const int H, const float p, const unsigned int bucket, const int slices,
            const float intensity, const float split, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float amt = intensity * powf(sinf(3.14159265f * p), 0.7f);
    // slice height varies per time bucket; the slice grid is offset per bucket too
    const float sh = (float)H / slices * (0.35f + 1.3f * hash1(bucket, 7u, seed));
    const unsigned int band = (unsigned int)((y + hash1(bucket, 9u, seed) * sh) / sh);
    const float r1 = hash1(band, bucket, seed), r2 = hash1(band, bucket, seed + 1u);
    const float r3 = hash1(band, bucket, seed + 2u);
    const bool torn = r1 < amt;
    const bool use_b = torn ? (r2 < p) : (p >= 0.5f);
    const uchar4* __restrict__ src = use_b ? b : a;
    int sx = x;
    float sp = 0.f;
    if (torn) {
        sx = x + (int)((r3 - 0.5f) * 0.3f * W * amt);
        sx = ((sx % W) + W) % W;
        sp = split * amt * 0.012f * W * (r2 < 0.5f ? 1.f : -1.f);
    }
    const float4 cr = ld(src, ((sx + (int)sp) % W + W) % W, y, W, H);
    const float4 cg = ld(src, sx, y, W, H);
    const float4 cb = ld(src, ((sx - (int)sp) % W + W) % W, y, W, H);
    float4 c = make_float4(cr.x, cg.y, cb.z, 255.f);
    if (torn && r3 > 0.93f) {                                  // a few slices blow out to white noise
        const float n = hash1(x, y, bucket) * 255.f * amt;
        c = make_float4(c.x * 0.4f + n, c.y * 0.4f + n, c.z * 0.4f + n, 255.f);
    }
    out[y * W + x] = pack(c);
}
"""


class Glitch(Transition):
    name = "glitch"
    label = "Glitch"
    description = "Torn, jumping slices with an RGB split, flickering from the old picture to the new."
    default_duration_s = 0.8
    pure_endpoints = True
    params = {
        "slices": {"type": "integer", "minimum": 4, "maximum": 200, "default": 28,
                   "x-ui": {"label": "Slices", "widget": "slider"}},
        "intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.85,
                      "x-ui": {"label": "Intensity", "step": 0.01, "widget": "slider"}},
        "rgb_split": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7,
                      "x-ui": {"label": "RGB split", "step": 0.01, "widget": "slider"}},
        "rate_hz": {"type": "number", "minimum": 1.0, "maximum": 60.0, "default": 15.0,
                    "x-ui": {"label": "Jitter rate", "step": 1, "unit": "Hz", "widget": "slider"}},
        "seed": {"type": "integer", "minimum": -1, "maximum": 2**31 - 1, "default": -1,
                 "x-ui": {"label": "Seed (-1 random)"}},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        s = self.params["seed"]
        self._seed = np.uint32(np.random.default_rng().integers(0, 2**31) if s < 0 else s)
        self._k = raw_module(_SRC).get_function("glitch")

    def render(self, progress: float, a, b, out) -> None:
        p = self.params
        bucket = np.uint32(int(progress * self.duration_s * p["rate_hz"]))
        launch_2d(self._k, self.ctx.width, self.ctx.height,
                  (a, b, out, np.int32(self.ctx.width), np.int32(self.ctx.height), np.float32(progress), bucket,
                   np.int32(p["slices"]), np.float32(p["intensity"]), np.float32(p["rgb_split"]), self._seed))
