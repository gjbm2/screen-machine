"""A song's shape from its audio: how its intensity rises and falls (its swells, its climax), 0..1 every HOP_S.

Owner, 26 Nov: "a.d. really failed to 'feel' the swell and then climax of 'My Way'".  For a karaoke song the audio
is in the library (its backing and lead vocal: together the original mix), so its shape is measured, not guessed
from a room mic: short-term loudness (EBU R128, 3 s) every HOP_S and spectral flux (how busy it is), each
normalised within the song (its 5th..98th percentile -> 0..1), mixed LOUD_W / FLUX_W, smoothed over SMOOTH_S and
normalised again; the climax is the CLIMAX_S window where it is highest.

Used by show_service's karaoke feed (the director follows it) and tools/room/shape_eval (the eval's reference), so
both read the same curve.  numpy + pyebur128 + ffmpeg only.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Optional

import numpy as np

FS = 48000
HOP_S = 0.5
SMOOTH_S = 8.0
CLIMAX_S = 20.0
LOUD_W, FLUX_W = 0.75, 0.25
VERSION = 1


def decode_mix(song_dir: pathlib.Path, fs: int = FS) -> np.ndarray:
    """The song's backing + lead vocal, summed, mono float32 at ``fs``."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(song_dir / "instrumental.m4a"), "-i",
                        str(song_dir / "vocals.m4a"), "-filter_complex", "amix=inputs=2:normalize=0", "-ac", "1",
                        "-ar", str(fs), "-f", "f32le", "-"], capture_output=True, check=True, timeout=300)
    return np.frombuffer(p.stdout, np.float32).copy()


def _norm(x: np.ndarray) -> np.ndarray:
    lo, hi = np.percentile(x, 5), np.percentile(x, 98)
    return np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0)


def _smooth(x: np.ndarray, s: float) -> np.ndarray:
    k = max(1, int(round(s / HOP_S)))
    w = np.hanning(2 * k + 1)
    w /= w.sum()
    return np.convolve(np.pad(x, k, mode="edge"), w, mode="valid")


def measure(pcm: np.ndarray, fs: int = FS) -> dict:
    """-> {"hop": HOP_S, "v": [0..1 every hop], "loud": [LUFS short-term], "climax": [t0, t1], "version"}."""
    import pyebur128
    st = pyebur128.R128State(1, fs, pyebur128.MeasurementMode.MODE_S)
    hop = int(HOP_S * fs)
    loud, flux = [], []
    win = np.hanning(2048).astype(np.float32)
    prev = None
    for i in range(0, len(pcm) - hop, hop):
        chunk = np.ascontiguousarray(pcm[i:i + hop], dtype=np.float64)
        st.add_frames(chunk, len(chunk))
        loud.append(max(float(pyebur128.get_loudness_shortterm(st)), -70.0))
        f = 0.0
        for j in range(0, len(chunk) - 2048, 1024):
            m = np.abs(np.fft.rfft(chunk[j:j + 2048] * win))
            if prev is not None:
                f += float(np.maximum(m - prev, 0.0).sum())
            prev = m
        flux.append(f)
    if len(loud) < 8:
        return {"hop": HOP_S, "v": [], "loud": [], "climax": None, "version": VERSION}
    loud_a, flux_a = np.asarray(loud), np.log1p(np.asarray(flux))
    ref = _norm(_smooth(LOUD_W * _norm(loud_a) + FLUX_W * _norm(flux_a), SMOOTH_S))
    k = min(len(ref), int(CLIMAX_S / HOP_S))
    i0 = int(np.argmax(np.convolve(ref, np.ones(k) / k, mode="valid")))
    return {"hop": HOP_S, "v": [round(float(x), 3) for x in ref], "loud": [round(float(x), 1) for x in loud_a],
            "climax": [i0 * HOP_S, i0 * HOP_S + CLIMAX_S], "version": VERSION}


def at(shape: Optional[dict], t: float) -> Optional[float]:
    """The shape's value at song time ``t`` (s), linearly between hops; None without a shape."""
    if not shape or not shape.get("v"):
        return None
    v, hop = shape["v"], float(shape.get("hop") or HOP_S)
    x = min(max(t / hop, 0.0), len(v) - 1.0)
    i = int(x)
    j = min(i + 1, len(v) - 1)
    return float(v[i] + (v[j] - v[i]) * (x - i))


def load_or_measure(song_dir: pathlib.Path, cache: pathlib.Path) -> Optional[dict]:
    """The song's shape, measured once and kept in ``cache`` (JSON); None when its audio can't be read."""
    try:
        d = json.loads(cache.read_text())
        if d.get("version") == VERSION:
            return d
    except (OSError, ValueError):
        pass
    if not (song_dir / "instrumental.m4a").is_file() or not (song_dir / "vocals.m4a").is_file():
        return None
    d = measure(decode_mix(song_dir))
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, cache)
    return d
