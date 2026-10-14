"""Render-scale helper for scenes: render into an internal (h, w) buffer, then bilinear-upsample to the output."""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d

_SRC = HELPERS + r"""
extern "C" __global__
void upsample(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int w, const int h, const int W, const int H)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    dst[y * W + x] = pack(bilin(src, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h));
}
"""


def internal_size(ctx, render_scale: float) -> tuple[int, int]:
    return (max(16, int(round(ctx.width * render_scale / 2)) * 2), max(16, int(round(ctx.height * render_scale / 2)) * 2))


def present(ctx, buf, out) -> None:
    """Copy or upsample the internal buffer ``buf`` (h, w, 4) into ``out`` (H, W, 4)."""
    h, w = buf.shape[:2]
    if (w, h) == (ctx.width, ctx.height):
        ctx.cp.copyto(out, buf)
        return
    k = raw_module(_SRC).get_function("upsample")
    launch_2d(k, ctx.width, ctx.height, (buf, out, np.int32(w), np.int32(h), np.int32(ctx.width), np.int32(ctx.height)))
