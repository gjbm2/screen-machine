"""Score ribbon: primitive records, vectorised (numpy in, ribbon_kernel's NF-float records out, boxes filled in).

Source: Original.  Author: smfx.  License: MIT (this file).  license_class: permissive

Every builder takes equal-length arrays (or scalars) and returns a (k, NF) float32 block.  Boxes are in the record's
own space (tape px for row -1, screen px for a header row) and row-local y, padded by the halo's reach for a mark
that can glow and by 1.5 px for the edge ramp.  A mark's colour is its note's (``col``: pitch class, strike and
release times, velocity, halo weight; the kernel colours it each frame), or plain ink (pitch class -1).
"""
from __future__ import annotations

import numpy as np

try:
    from . import ribbon_glyphs as RG, ribbon_kernel as RK
except ImportError:
    import ribbon_glyphs as RG
    import ribbon_kernel as RK

NF = RK.NF
# glyph boxes in sp (y down), from the font's metrics, padded a little (outlines can overshoot the box)
GBOX = np.array([RG.box(n) for n, _c in RG.GLYPHS], np.float32) + np.array([-0.08, -0.08, 0.08, 0.08], np.float32)
INK = np.array([-1.0, -1e9, 1e9, 0.0, 0.0], np.float32)


def _block(n: int, kind: int, row, alpha, col) -> np.ndarray:
    R = np.zeros((n, NF), np.float32)
    R[:, 0] = kind
    R[:, 1] = row
    R[:, 9] = alpha
    R[:, 10:15] = INK if col is None else col
    return R


def _pad(R: np.ndarray, halo_px: float):
    if halo_px <= 0.0:
        return 1.5
    return 1.5 + np.where((R[:, 14] > 0.0) & (R[:, 10] >= 0.0), 3.0 * halo_px, 0.0)


def glyph(x, y, g, sx, sy=None, *, gb=-1, mt=0.0, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x = np.atleast_1d(np.asarray(x, np.float64))
    R = _block(len(x), RK.GLYPH, row, alpha, col)
    R[:, 2] = x
    R[:, 3] = y
    R[:, 4] = sx
    R[:, 5] = sx if sy is None else sy
    R[:, 6] = g
    R[:, 7] = gb
    R[:, 8] = mt
    gi = R[:, 6].astype(np.int32)
    bx = GBOX[gi]
    if not (np.isscalar(gb) and gb < 0):
        gbi = R[:, 7].astype(np.int32)
        has = gbi >= 0
        if has.any():
            bb = GBOX[np.maximum(gbi, 0)]
            bx = np.where(has[:, None], np.concatenate([np.minimum(bx[:, :2], bb[:, :2]),
                                                        np.maximum(bx[:, 2:], bb[:, 2:])], 1), bx)
    sxa, sya = R[:, 4], R[:, 5]
    p = _pad(R, halo_px)
    R[:, 20] = R[:, 2] + bx[:, 0] * sxa - p
    R[:, 22] = R[:, 2] + bx[:, 2] * sxa + p
    R[:, 21] = R[:, 3] + bx[:, 1] * sya - p
    R[:, 23] = R[:, 3] + bx[:, 3] * sya + p
    return R


def box(x0, y0, x1, y1, *, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x0 = np.atleast_1d(np.asarray(x0, np.float64))
    R = _block(len(x0), RK.BOX, row, alpha, col)
    R[:, 2], R[:, 3], R[:, 15], R[:, 16] = x0, y0, x1, y1
    p = _pad(R, halo_px)
    R[:, 20] = R[:, 2] - p
    R[:, 21] = R[:, 3] - p
    R[:, 22] = R[:, 15] + p
    R[:, 23] = R[:, 16] + p
    return R


def beam(x0, y0, x1, slope, half, *, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x0 = np.atleast_1d(np.asarray(x0, np.float64))
    R = _block(len(x0), RK.BEAM, row, alpha, col)
    R[:, 2], R[:, 3], R[:, 15], R[:, 16], R[:, 17] = x0, y0, x1, slope, half
    y1 = R[:, 3] + R[:, 16] * (R[:, 15] - R[:, 2])
    p = _pad(R, halo_px) + R[:, 17]
    R[:, 20] = R[:, 2] - p
    R[:, 22] = R[:, 15] + p
    R[:, 21] = np.minimum(R[:, 3], y1) - p
    R[:, 23] = np.maximum(R[:, 3], y1) + p
    return R


def tie(x0, y0, x1, y1, sag, thick, *, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x0 = np.atleast_1d(np.asarray(x0, np.float64))
    R = _block(len(x0), RK.TIE, row, alpha, col)
    R[:, 2], R[:, 3], R[:, 15], R[:, 16], R[:, 17], R[:, 18] = x0, y0, x1, y1, sag, thick
    p = _pad(R, halo_px)
    lo = np.minimum(R[:, 3], R[:, 16])
    hi = np.maximum(R[:, 3], R[:, 16])
    R[:, 20] = np.minimum(R[:, 2], R[:, 15]) - p
    R[:, 22] = np.maximum(R[:, 2], R[:, 15]) + p
    R[:, 21] = np.minimum(lo, lo + R[:, 17]) - p
    R[:, 23] = np.maximum(hi, hi + R[:, 17]) + p
    return R


def ledgers(x0, y0, x1, step, count, half, *, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x0 = np.atleast_1d(np.asarray(x0, np.float64))
    R = _block(len(x0), RK.LEDGERS, row, alpha, col)
    R[:, 2], R[:, 3], R[:, 15], R[:, 16], R[:, 17], R[:, 18] = x0, y0, x1, step, count, half
    p = _pad(R, halo_px) + R[:, 18]
    yl = R[:, 3] + R[:, 16] * np.maximum(R[:, 17] - 1, 0)
    R[:, 20] = R[:, 2] - p
    R[:, 22] = R[:, 15] + p
    R[:, 21] = np.minimum(R[:, 3], yl) - p
    R[:, 23] = np.maximum(R[:, 3], yl) + p
    return R


def dot(x, y, r, *, row=-1, alpha=1.0, col=None, halo_px=0.0):
    x = np.atleast_1d(np.asarray(x, np.float64))
    R = _block(len(x), RK.DOT, row, alpha, col)
    R[:, 2], R[:, 3], R[:, 15] = x, y, r
    p = _pad(R, halo_px) + R[:, 15]
    R[:, 20], R[:, 22] = R[:, 2] - p, R[:, 2] + p
    R[:, 21], R[:, 23] = R[:, 3] - p, R[:, 3] + p
    return R


class Batch:
    """Glyph inputs gathered from every kind of mark, built in one call."""

    def __init__(self) -> None:
        self.parts: list = []

    def add(self, x, y, g, sx, sy, gb, mt, alpha, col) -> None:
        n = len(x)
        if n == 0:
            return

        def full(v, dt=np.float32):
            return np.full(n, v, dt) if np.isscalar(v) else np.asarray(v)
        self.parts.append((np.asarray(x, np.float64), full(y, np.float64), full(g), full(sx), full(sy), full(gb),
                           full(mt), full(alpha), INK[None, :].repeat(n, 0) if col is None else col))

    def build(self, halo_px: float) -> np.ndarray | None:
        if not self.parts:
            return None
        cols = [np.concatenate([p[k] for p in self.parts]) for k in range(9)]
        x, y, g, sx, sy, gb, mt, alpha, col = cols
        return glyph(x, y, g, sx, sy, gb=gb, mt=mt, alpha=alpha, col=col, halo_px=halo_px)
