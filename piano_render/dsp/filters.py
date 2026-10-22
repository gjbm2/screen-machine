"""Filters: RBJ-cookbook biquads (Bristow-Johnson) and Butterworth sections; all causal (sosfilt), so nothing
moves earlier in time.

Instrument "eq" bands, applied in order:
  {"type": "highshelf"|"lowshelf", "f": Hz, "gain_db": dB, "q": 0.707}   (shelf slope from q)
  {"type": "peak", "f": Hz, "gain_db": dB, "q": 1.0}
  {"type": "highpass"|"lowpass", "f": Hz, "q": 0.707}
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.signal import butter, sosfilt


def biquad_sos(kind: str, f: float, sr: float, gain_db: float = 0.0, q: float = 0.7071) -> np.ndarray:
    """One RBJ biquad as a (1, 6) second-order section."""
    if not 0 < f < sr / 2:
        raise ValueError(f"eq frequency {f} Hz outside (0, {sr / 2})")
    q = float(q) if q else 0.7071
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * f / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "peak":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif kind in ("highshelf", "lowshelf"):
        sq = 2 * math.sqrt(A) * alpha
        if kind == "lowshelf":
            b = [A * ((A + 1) - (A - 1) * cw + sq), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sq)]
            a = [(A + 1) + (A - 1) * cw + sq, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sq]
        else:
            b = [A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sq)]
            a = [(A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq]
    elif kind == "highpass":
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif kind == "lowpass":
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise ValueError(f"unknown eq type {kind!r}")
    b = np.asarray(b) / a[0]
    a = np.asarray(a) / a[0]
    return np.concatenate([b, a])[None, :]


def eq_sos(eq: Optional[list], sr: float) -> Optional[np.ndarray]:
    secs = [biquad_sos(str(band.get("type")), float(band["f"]), sr, float(band.get("gain_db", 0.0)),
                       float(band.get("q", 0.7071)))
            for band in (eq or [])]
    return np.concatenate(secs, axis=0) if secs else None


def apply_sos(audio: np.ndarray, sos: Optional[np.ndarray]) -> np.ndarray:
    """Filter each column of (N, C) audio (or a 1-D signal); float32 out."""
    a = np.asarray(audio)
    if sos is None:
        return a
    if a.ndim == 1:
        return sosfilt(sos, a).astype(np.float32)
    out = np.empty(a.shape, np.float32)
    for c in range(a.shape[1]):
        out[:, c] = sosfilt(sos, a[:, c])
    return out


def apply_eq(audio: np.ndarray, sr: float, eq: Optional[list]) -> np.ndarray:
    """The instrument's "eq" list (module docstring); unchanged (same object) when the list is empty."""
    return apply_sos(audio, eq_sos(eq, sr))


CHUNK = 1 << 16


def apply_sos_inplace(buf: np.ndarray, n: int, sos: Optional[np.ndarray], chunk: int = CHUNK) -> None:
    """Filter buf[:n] (each column) in place, chunk by chunk with the filter state carried over: the same result
    as one sosfilt, without full-length temporaries (fresh pages cost ~20 us each on this box)."""
    if sos is None or n <= 0:
        return
    cols = buf.shape[1] if buf.ndim == 2 else 1
    for c in range(cols):
        zi = np.zeros((sos.shape[0], 2))
        for s in range(0, n, chunk):
            e = min(n, s + chunk)
            if buf.ndim == 2:
                y, zi = sosfilt(sos, buf[s:e, c], zi=zi)
                buf[s:e, c] = y
            else:
                y, zi = sosfilt(sos, buf[s:e], zi=zi)
                buf[s:e] = y


def butter_sos(order: int, f, sr: float, kind: str) -> np.ndarray:
    return butter(order, f, btype=kind, fs=sr, output="sos")
