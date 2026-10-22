"""Mastering: measure, set the level, write the file. No compression, no limiter, no added delay.

  * integrated loudness: ITU-R BS.1770-4 (K-weighting, 400 ms blocks every 100 ms, -70 LUFS absolute gate,
    -10 LU relative gate); the K filter is libebur128's design, which is the spec's coefficients at 48 kHz and the
    same response at other rates;
  * sample peak, and a true-peak estimate from 4x polyphase oversampling (scipy resample_poly);
  * gain: the instrument's gain_db, then, only if the result would exceed the ceiling (-1 dBTP), less gain so it
    doesn't; a plain scalar gain, so sample 0 stays MIDI time 0;
  * FLAC, 24-bit, 48 kHz (soundfile).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np

# scipy.signal is imported inside the functions that use it: importing it costs about 1.8 s, and a cached start
# (piano_render.session -> render cache hit) imports this module without measuring anything.

CEILING_DBTP = -1.0
CEILING_MARGIN_DB = 0.01        # headroom for the 24-bit rounding
ABS_GATE_LUFS = -70.0
REL_GATE_LU = -10.0
BLOCK_S = 0.4
STEP_S = 0.1
OVERSAMPLE = 4


def _2d(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio)
    return a[:, None] if a.ndim == 1 else a


def k_weighting_sos(sr: float) -> np.ndarray:
    """(2, 6) SOS: the BS.1770 pre-filter (high shelf) then the RLB high-pass."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = math.tan(math.pi * f0 / sr)
    Vh = 10.0 ** (G / 20.0)
    Vb = Vh ** 0.4996667741545416
    a0 = 1.0 + K / Q + K * K
    shelf = [(Vh + Vb * K / Q + K * K) / a0, 2.0 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0,
             1.0, 2.0 * (K * K - 1.0) / a0, (1.0 - K / Q + K * K) / a0]
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = math.tan(math.pi * f0 / sr)
    d = 1.0 + K / Q + K * K
    hp = [1.0, -2.0, 1.0, 1.0, 2.0 * (K * K - 1.0) / d, (1.0 - K / Q + K * K) / d]
    return np.array([shelf, hp], dtype=np.float64)


def integrated_loudness(audio: np.ndarray, sr: float) -> float:
    """BS.1770-4 integrated loudness in LUFS (-inf if nothing passes the gates or the audio is < 400 ms)."""
    x = _2d(audio)
    step = int(round(STEP_S * sr))
    per_block = int(round(BLOCK_S / STEP_S))
    nseg = x.shape[0] // step
    if nseg < per_block:
        return float("-inf")
    from scipy.signal import sosfilt
    sos = k_weighting_sos(sr)
    seg = np.zeros(nseg, np.float64)
    for c in range(x.shape[1]):                       # channel weights 1.0 (L, R, C)
        y = sosfilt(sos, x[:, c].astype(np.float64))
        seg += np.square(y[:nseg * step]).reshape(nseg, step).sum(axis=1)
        del y
    csum = np.concatenate([[0.0], np.cumsum(seg)])
    z = (csum[per_block:] - csum[:-per_block]) / (per_block * step)     # mean square per 400 ms block
    with np.errstate(divide="ignore"):
        lj = -0.691 + 10.0 * np.log10(z)
    keep = lj > ABS_GATE_LUFS
    if not keep.any():
        return float("-inf")
    rel = -0.691 + 10.0 * math.log10(z[keep].mean()) + REL_GATE_LU
    keep &= lj > rel
    if not keep.any():
        return float("-inf")
    return float(-0.691 + 10.0 * math.log10(z[keep].mean()))


def sample_peak(audio: np.ndarray) -> float:
    a = np.asarray(audio)
    return float(np.max(np.abs(a))) if a.size else 0.0


_L1_CACHE: dict[int, float] = {}


def _phase_l1(oversample: int) -> float:
    """Max over phases of sum |h| for resample_poly's interpolation filter: no interpolated sample can exceed this
    times the largest input sample within the filter's reach (used to skip chunks that can't hold the peak)."""
    if oversample not in _L1_CACHE:
        from scipy.signal import firwin
        half = 10 * oversample
        h = firwin(2 * half + 1, 1.0 / oversample, window=("kaiser", 5.0)) * oversample
        _L1_CACHE[oversample] = float(max(np.abs(h[p::oversample]).sum() for p in range(oversample))) * 1.001
    return _L1_CACHE[oversample]


def true_peak(audio: np.ndarray, oversample: int = OVERSAMPLE, chunk: int = 1 << 16, margin: int = 64) -> float:
    """Linear true-peak estimate: max |x| after 4x polyphase oversampling (scipy resample_poly). Exact for that
    filter, but only chunks whose own peak (times the filter's gain bound) could beat the best so far are
    oversampled, loudest first, so a long file costs a few chunks instead of all of them."""
    x = _2d(audio)
    n = x.shape[0]
    best = sample_peak(x)
    if n == 0 or best == 0.0:
        return best
    from scipy.signal import resample_poly
    bound = _phase_l1(oversample)
    starts = np.arange(0, n, chunk)
    for c in range(x.shape[1]):
        ch = x[:, c]
        cp = np.maximum.reduceat(np.abs(ch), starts)
        reach = cp.copy()                              # the margin reaches into the neighbouring chunks
        reach[1:] = np.maximum(reach[1:], cp[:-1])
        reach[:-1] = np.maximum(reach[:-1], cp[1:])
        for j in np.argsort(-reach, kind="stable"):
            if reach[j] * bound <= best:
                break
            s = int(starts[j])
            a, b = max(0, s - margin), min(n, s + chunk + margin)
            y = resample_poly(ch[a:b].astype(np.float64), oversample, 1)
            lo, hi = (s - a) * oversample, (min(n, s + chunk) - a) * oversample
            if hi > lo:
                best = max(best, float(np.max(np.abs(y[lo:hi]))))
    return best


def db(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else float("-inf")


@dataclass
class MasterStats:
    lufs_in: Optional[float]
    lufs: Optional[float]
    sample_peak_dbfs: Optional[float]
    true_peak_dbtp: Optional[float]
    gain_db: float              # gain actually applied
    requested_gain_db: float    # the instrument's gain_db
    limited: bool               # True if the ceiling cut the gain

    def asdict(self) -> dict:
        return asdict(self)


def _finite(v: float) -> Optional[float]:
    return round(v, 3) if v is not None and math.isfinite(v) else None


def process(audio: np.ndarray, sr: int, gain_db: float = 0.0, ceiling_dbtp: float = CEILING_DBTP
            ) -> tuple[np.ndarray, MasterStats]:
    """Apply gain_db, cut it only as far as needed for true peak <= ceiling. Returns (float32 audio, stats)."""
    x = np.asarray(audio, dtype=np.float32)
    lufs_in = integrated_loudness(x, sr)
    tp_in = db(true_peak(x))
    sp_in = db(sample_peak(x))
    g = float(gain_db or 0.0)
    limited = False
    if math.isfinite(tp_in) and tp_in + g > ceiling_dbtp - CEILING_MARGIN_DB:
        g = ceiling_dbtp - CEILING_MARGIN_DB - tp_in
        limited = True
    out = x if g == 0.0 else (x * np.float32(10.0 ** (g / 20.0)))
    stats = MasterStats(lufs_in=_finite(lufs_in), lufs=_finite(lufs_in + g), sample_peak_dbfs=_finite(sp_in + g),
                        true_peak_dbtp=_finite(tp_in + g), gain_db=round(g, 3),
                        requested_gain_db=float(gain_db or 0.0), limited=limited)
    return out, stats


def write_flac(path: str, audio: np.ndarray, sr: int = 48000) -> None:
    import soundfile as sf
    sf.write(path, np.asarray(audio, np.float32), int(sr), format="FLAC", subtype="PCM_24")
