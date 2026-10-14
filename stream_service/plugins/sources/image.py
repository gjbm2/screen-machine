"""Image: holds a still (the entry anchor or the exit target).  Internal: never picked by a person.

Stills arrive as PNG bytes of any size; :func:`load_png` letterboxes a non-matching size onto black (the
display page's background) at the profile size.  The pixels are uploaded once in ``setup`` and copied into
the frame on every render (a device-to-device copy, ~0.2 ms at 4K).
"""
from __future__ import annotations

import io
from typing import Optional

import numpy as np

from ..base import ParamError, Source

MAX_PIXELS = 64_000_000  # a 64 MB request could still decode to something absurd


def load_png(data: bytes, width: int, height: int) -> np.ndarray:
    """PNG bytes -> (H, W, 3) uint8 sRGB at exactly width x height (letterboxed on black if needed)."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        if img.format != "PNG":
            raise ParamError("bad_image", f"expected a PNG, got {img.format}")
        img.load()
    except ParamError:
        raise
    except Exception as e:  # PIL raises many types (UnidentifiedImageError, DecompressionBombError, OSError...)
        raise ParamError("bad_image", f"could not decode the PNG: {type(e).__name__}: {e}") from None
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        # transparent areas show the page's black background
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
        img = Image.alpha_composite(bg, rgba).convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    if img.size != (width, height):
        w0, h0 = img.size
        s = min(width / w0, height / h0)
        nw, nh = max(1, round(w0 * s)), max(1, round(h0 * s))
        scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.paste(scaled, ((width - nw) // 2, (height - nh) // 2))
        img = canvas
    return np.ascontiguousarray(np.asarray(img, dtype=np.uint8))


class ImageSource(Source):
    name = "image"
    label = "Image"
    description = "A still picture: the entry anchor or the exit target (internal)."
    internal = True
    params: dict = {}

    def __init__(self, pixels: Optional[np.ndarray] = None, rgb: Optional[tuple[int, int, int]] = None) -> None:
        super().__init__()
        if (pixels is None) == (rgb is None):
            raise ValueError("ImageSource needs exactly one of pixels or rgb")
        self._pixels = pixels
        self._rgb = rgb
        self.frame = None

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.frame = ctx.new_frame()
        if self._rgb is not None:
            ctx.kernels.fill(self.frame, self._rgb)
        else:
            if self._pixels.shape != (ctx.height, ctx.width, 3):
                raise ValueError(f"image is {self._pixels.shape}, need {(ctx.height, ctx.width, 3)}")
            rgb = cp.asarray(self._pixels)
            ctx.kernels.rgb_to_rgba(rgb, self.frame)
            del rgb
        self._pixels = None  # the host copy is no longer needed

    def render(self, t_local: float, dt: float, out) -> None:
        out[...] = self.frame

    def close(self) -> None:
        self.frame = None

    @property
    def static(self) -> bool:
        return True
