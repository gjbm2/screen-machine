"""Tone stages on the dry instrument: detuned copies, una corda, mid/side width.

Una corda (soft pedal, CC67; research r4 item 8). u = CC67 / 127, smoothed with a 30 ms one-pole, drives a
time-varying high shelf (UC_SHELF_DB at full pedal above UC_SHELF_HZ) and a level drop (UC_LEVEL_DB at full pedal)
on the dry mix:  y = g_lv(u) * (x - (1 - g_hs(u)) * (x - LP(x))),  LP = 2nd-order Butterworth at UC_SHELF_HZ.
With u = 0 that is exactly x, so a piece without the soft pedal passes through untouched (and costs nothing). It
acts on everything sounding while the pedal is down, not per strike: pianists hold the soft pedal across a phrase,
so the two differ only at the edges. A velocity reduction before the engine (softer layers) was not added: it
would stack on this level drop, and no installed SFZ reads CC67 itself (checked 22 Oct), so this is the only
soft-pedal effect. An instrument whose engine models CC67 itself sets "una_corda": false.

Detune (honky-tonk; r4 item 10): the engine renders the performance again at each detune_cents offset (pitch only,
timing untouched), and the copies are summed with the original at 1/sqrt(voices) each (-3 dB for two voices,
-4.8 dB for three, r4's gains), with no time offset between them (an offset would comb-filter the attacks).

Width: mid/side, side x width (1 = unchanged, 0 = mono).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.signal import lfilter, sosfilt

from .filters import butter_sos

UC_SHELF_DB = -4.0
UC_SHELF_HZ = 1800.0
UC_LEVEL_DB = -2.5
UC_SMOOTH_S = 0.03


def smooth_rows(values: np.ndarray, rate_hz: float, tau_s: float = UC_SMOOTH_S) -> np.ndarray:
    """A control sampled at rate_hz (rows held between samples), smoothed by a one-pole of time constant tau_s."""
    v = np.asarray(values, np.float64)
    if v.size == 0:
        return v
    a = 1.0 - math.exp(-1.0 / (tau_s * rate_hz))
    out, _ = lfilter([a], [1.0, -(1.0 - a)], v, zi=np.array([(1 - a) * v[0]]))
    return out


def _segments(active: np.ndarray) -> list[tuple[int, int]]:
    """[start, stop) runs of True."""
    if not active.any():
        return []
    d = np.diff(np.concatenate([[0], active.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def una_corda(audio: np.ndarray, sr: float, t: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Soft-pedal shaping on a copy (module docstring); the input itself when the pedal is never pressed."""
    u = np.asarray(u, np.float64)
    if audio.size == 0 or u.size < 2 or float(u.max()) <= 0.0:
        return audio
    out = np.array(audio, np.float32, copy=True)
    una_corda_inplace(out, out.shape[0], sr, t, u)
    return out


def una_corda_inplace(buf: np.ndarray, n: int, sr: float, t: np.ndarray, u: np.ndarray, chunk: int = 1 << 16) -> dict:
    """Soft-pedal shaping of buf[:n] in place. t, u = control rows (uniform times, s) and CC67/127. Only the
    stretches where the smoothed pedal is above 1e-4 are touched (the filter state is primed on the 20 ms before
    each), chunk by chunk."""
    u = np.asarray(u, np.float64)
    if n <= 0 or u.size < 2 or float(u.max()) <= 0.0:
        return {"segments": 0}
    t = np.asarray(t, np.float64)
    rate = 1.0 / float(t[1] - t[0])
    us = np.clip(smooth_rows(u, rate), 0.0, 1.0)
    act = us > 1e-4
    act[1:] |= act[:-1]                     # one row either side, so interpolation never jumps
    act[:-1] |= act[1:]
    sos = butter_sos(2, UC_SHELF_HZ, sr, "lowpass")
    k_cut = np.float32(UC_SHELF_DB * math.log(10) / 20)
    k_lv = np.float32(UC_LEVEL_DB * math.log(10) / 20)
    pre_n = int(0.02 * sr)
    segs = _segments(act)
    for r0, r1 in segs:
        s0 = int(math.floor(t[r0] * sr))
        s1 = min(n, int(math.ceil(t[min(r1, len(t) - 1)] * sr)) + 1)
        if s1 <= s0 or s0 >= n:
            continue
        tt, uu = t[r0:r1 + 1], us[r0:r1 + 1]
        pre = max(0, s0 - pre_n)
        zi = []
        for c in range(buf.shape[1]):
            z0 = np.zeros((sos.shape[0], 2))
            if s0 > pre:
                _, z0 = sosfilt(sos, buf[pre:s0, c], zi=z0)
            zi.append(z0)
        for a in range(s0, s1, chunk):
            b = min(s1, a + chunk)
            useg = np.interp(np.arange(a, b) / sr, tt, uu).astype(np.float32)
            g_cut = 1.0 - np.exp(useg * k_cut)                       # 1 - g_hs
            g_lv = np.exp(useg * k_lv)
            for c in range(buf.shape[1]):
                x = buf[a:b, c].astype(np.float32)
                lp, zi[c] = sosfilt(sos, x, zi=zi[c])
                buf[a:b, c] = g_lv * (x - g_cut * (x - lp.astype(np.float32)))
    return {"segments": len(segs)}


def apply_width(audio: np.ndarray, width: Optional[float]) -> np.ndarray:
    if width is None or float(width) == 1.0 or audio.ndim != 2 or audio.shape[1] != 2:
        return audio
    w = float(width)
    mid = 0.5 * (audio[:, 0] + audio[:, 1])
    side = 0.5 * (audio[:, 0] - audio[:, 1]) * w
    return np.stack([mid + side, mid - side], axis=1).astype(np.float32)


def width_inplace(buf: np.ndarray, n: int, width: Optional[float], chunk: int = 1 << 16) -> None:
    if width is None or float(width) == 1.0 or buf.ndim != 2 or buf.shape[1] != 2:
        return
    w = np.float32(width)
    for a in range(0, n, chunk):
        b = min(n, a + chunk)
        mid = 0.5 * (buf[a:b, 0] + buf[a:b, 1])
        side = 0.5 * (buf[a:b, 0] - buf[a:b, 1]) * w
        buf[a:b, 0] = mid + side
        buf[a:b, 1] = mid - side


def mix_voices(voices: list) -> np.ndarray:
    """Sum (N_i, 2) renders aligned at sample 0, each at 1/sqrt(len(voices)); the longest sets the length."""
    if len(voices) == 1:
        return np.asarray(voices[0], np.float32)
    n = max(v.shape[0] for v in voices)
    out = np.zeros((n, 2), np.float32)
    for v in voices:
        out[:v.shape[0]] += v
    out *= np.float32(1.0 / math.sqrt(len(voices)))
    return out
