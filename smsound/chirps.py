"""The measurement of an output's delay: coded chirps, timed with the room mic (docs/SOUND.md). Pure functions; Flask's
routes/sound/calibrate.py runs a calibration with them. First built for karaoke (25 Nov):

* Ten chirps, each its own (CHIRPS: rising or falling sweeps over different bands; owner, 25 Nov: "wouldn't it be
  better if they were separately coded ... so you could be confident matching on longer latencies"), at uneven times
  (OFFSETS_S), after a quiet noise bed that wakes the speaker (an idle Sonos buffers more as it wakes: 25 Nov, 987 ms
  on a first run where songs ran 410 ms behind).
* Each chirp is found by its own template alone over the whole window (MIN_DELAY_S..MAX_DELAY_S), so each gives its
  own reading and a lost chirp costs one; the first SETTLE chirps are left out (the speaker settling).
* Not the music itself: matching what the mic hears against a backing track locked onto the wrong beat of a steady
  song (25 Nov, Disco 2000: 1787 ms for 410).

The display page builds the same chirps (src/shared/sound/chirps.ts); ``render`` builds them as a file (Sonos).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

OFFSETS_S = (0.0, 0.62, 1.71, 2.35, 3.58, 4.16, 5.47, 6.05, 7.33, 7.94)   # uneven: one lag fits
LEAD_S = 4.0            # the windows poll /now once a second, then the noise bed wakes the speaker this long
SETTLE = 3              # the first chirps, left out of the result while the speaker settles
MAX_DELAY_S = 3.0       # the longest delay looked for (Bluetooth 0.1-0.5 s; a Sonos 0.4-1 s; AirPlay ~2 s)
MIN_DELAY_S = -0.1
TAIL_S = MAX_DELAY_S + 0.4
MIC_PATH_S = 0.005      # capture -> show_service arrival (the agent, measured 16 Oct: 5 ms median)
CHIRP_S = 0.04
#: Ten different chirps (Hz from, Hz to): rising and falling, over different bands, so no two look alike.
CHIRPS = [(600, 1500), (4800, 2000), (1000, 3200), (3600, 900), (1800, 5200),
          (2600, 700), (700, 2400), (5400, 3000), (1300, 4200), (3000, 1100)]
WAVE_N = 2048
MIC_WAIT_S = 6.0        # for the mic to come on and its samples to flow
MIN_PEAK_RATIO = 6.0    # the train's correlation peak over the median of the rest
MAX_SPREAD_MS = 25.0
MIN_AGREE = 5           # settled chirps within the spread of the median


def chirp(sr: int, f0: float = 900.0, f1: float = 4500.0, dur_s: float = CHIRP_S) -> np.ndarray:
    """The calibration chirp (the display page builds the same): a linear sweep f0 -> f1 under a Hann window."""
    n = max(8, int(round(dur_s * sr)))
    t = np.arange(n) / sr
    ph = 2 * math.pi * (f0 * t + (f1 - f0) * t * t / (2 * dur_s))
    return (np.sin(ph) * np.hanning(n)).astype(np.float64)


def assemble(frames: list[tuple[float, np.ndarray]], sr: int) -> tuple[float, np.ndarray]:
    """The levels frames ((capture time of the window's last sample, samples)) as one signal: (time of sample 0,
    samples). Overlaps are the same samples; a gap stays zero."""
    if not frames:
        return 0.0, np.zeros(0)
    idx0 = min(round(t * sr) - len(w) + 1 for t, w in frames)
    idx1 = max(round(t * sr) for t, w in frames)
    out = np.zeros(idx1 - idx0 + 1)
    for t, w in frames:
        end = round(t * sr) - idx0
        out[end - len(w) + 1:end + 1] = w
    return idx0 / sr, out


def chirp_specs(n: int) -> list[dict]:
    """The chirps for ``n`` clicks, as /now carries them to the output windows: [{f0, f1, dur_s}]."""
    return [{"f0": float(CHIRPS[k % len(CHIRPS)][0]), "f1": float(CHIRPS[k % len(CHIRPS)][1]), "dur_s": CHIRP_S}
            for k in range(n)]


def measure(t0: float, sig: np.ndarray, sr: int, clicks: list[float], specs: Optional[list[dict]] = None, *,
            min_delay_s: float = MIN_DELAY_S, max_delay_s: float = MAX_DELAY_S) -> dict:
    """The delay of the chirps (``clicks``: times on t0's clock; ``specs``: each one's sweep) in ``sig`` (from t0):
    each chirp is looked for with its own template over the whole window (min_delay_s..max_delay_s) and gives its own
    reading; the readings' median is the delay. {ok, delay_ms, spread_ms, agree, of, peak_ratio, why}."""
    specs = specs or chirp_specs(len(clicks))
    if len(sig) < sr:
        return {"ok": False, "why": "The mic sent almost nothing."}
    sig = sig - np.mean(sig)
    nfft = 1 << (len(sig) + int(CHIRP_S * sr) * 2 - 1).bit_length()
    S = np.fft.rfft(sig, nfft)
    lags = np.arange(int(min_delay_s * sr), int(max_delay_s * sr))
    per, ratios = [], []
    for ck, sp in zip(clicks, specs):
        c = chirp(sr, sp["f0"], sp["f1"], sp["dur_s"])
        env = np.abs(np.fft.irfft(S * np.conj(np.fft.rfft(c, nfft)), nfft)[:len(sig)])
        ii = int(round((ck - t0) * sr)) + lags
        ok = (ii >= 0) & (ii < len(env))
        if ok.sum() < len(lags) // 2:
            continue
        win = env[ii[ok]]
        k = int(np.argmax(win))
        ratio = float(win[k] / (np.median(win) + 1e-12))
        ratios.append(ratio)
        if ratio >= MIN_PEAK_RATIO:
            per.append(lags[ok][k] / sr)
    ratio = float(np.median(ratios)) if ratios else 0.0
    if not per:
        return {"ok": False, "of": len(clicks), "peak_ratio": round(ratio, 1),
                "why": "No chirps were heard: is the screen's page open (reload it), and the speaker on and loud enough?"}
    readings = [round(x * 1000) for x in per]
    use = per[SETTLE:] if len(per) >= SETTLE + MIN_AGREE else per          # the speaker settled
    med = float(np.median(use))
    spread = float(np.median(np.abs(np.array(use) - med))) * 1000
    agree = int(sum(abs(p - med) * 1000 <= max(10.0, 3 * spread) for p in use))
    res = {"delay_ms": round(med * 1000), "spread_ms": round(spread, 1), "agree": agree, "used": len(use),
           "of": len(clicks), "peak_ratio": round(ratio, 1), "readings_ms": readings}
    if agree < min(MIN_AGREE, len(use)) or len(use) < 4:
        return {**res, "ok": False, "why": f"Only {agree} of {len(clicks)} chirps heard clearly: try again with the room quieter, or the speaker louder."}
    if spread > MAX_SPREAD_MS:
        return {**res, "ok": False, "why": f"The chirps came back unevenly (±{spread:.0f} ms): try again."}
    return {**res, "ok": True, "why": None}


BED_LEVEL = 0.01        # the noise bed before the chirps: about -45 dBFS
CHIRP_LEVEL = 0.9


def render(sr: int, offsets_s: list[float], specs: list[dict], *, bed_s: float = 0.0, tail_s: float = 0.5,
           seed: int = 7) -> np.ndarray:
    """The chirps as one mono signal (a Sonos plays it as a file): a noise bed from 0, the chirps at
    ``bed_s + offsets_s[k]``, then ``tail_s`` of bed. Float in -1..1."""
    n = int(round((bed_s + max(offsets_s) + 0.2 + tail_s) * sr))
    rng = np.random.default_rng(seed)
    out = (rng.uniform(-1.0, 1.0, n) * BED_LEVEL).astype(np.float64)
    for o, sp in zip(offsets_s, specs):
        c = chirp(sr, sp["f0"], sp["f1"], sp["dur_s"]) * CHIRP_LEVEL
        i = int(round((bed_s + o) * sr))
        out[i:i + len(c)] += c[: max(0, n - i)]
    return np.clip(out, -1.0, 1.0)
