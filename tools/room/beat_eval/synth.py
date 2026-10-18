#!/usr/bin/env python3
"""Synthetic drum loops with exact beat annotations, for checking that run_stack.py replays the live beat stack
faithfully: the live clock locks on these within a few seconds (show_service/tests/test_clock.py's checkpoints), so a
replay that doesn't lock on them is wrong, not the clock.

Patterns (each 64 s, 48 kHz, peak -3 dBFS, a little noise so nothing is digital silence):
  house120    4/4 at 120 bpm: kick on every beat, clap on 2 and 4, open hat on the off-beat 8ths, soft 16th closed
              hats, an off-beat bass line and a soft pad (the e2e test's kind of loop).
  hiphop92    4/4 at 92 bpm: boom-bap kick (1, the "and" of 2, 3 and its "a"), snare on 2 and 4, hats on every 8th.
              The activation pulses at 184: the octave test (the clock must stay at 92).
  click100    a plain 1 kHz click on every beat at 100 bpm, the downbeat louder.

Writes the dataset layout tools/room/beat_eval uses (README "Datasets"): <out>/audio/<id>.wav (PCM16 mono),
<out>/beats/<id>.beats ("<time_s>\\t<pos>" lines, pos 1 = the downbeat) and <out>/index.json.

  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/synth.py [--out ~/.cache/screen-machine/beat-eval/synth]
Needs numpy only.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import wave

import numpy as np

FS = 48000
SECONDS = 64.0
OUT = pathlib.Path("~/.cache/screen-machine/beat-eval/synth").expanduser()


# -- instruments (the shapes of show_service/tests/test_clock.py's drum(), a little richer) --------------------------
def _add(x: np.ndarray, t: float, s: np.ndarray) -> None:
    i = int(round(t * FS))
    if i >= len(x) or i < 0:
        return
    e = min(len(x), i + len(s))
    x[i:e] += s[:e - i]


def kick(rng: np.random.Generator, amp: float = 0.7) -> np.ndarray:
    m = np.arange(int(0.25 * FS))
    f = 48 + 90 * np.exp(-m / (0.025 * FS))
    s = amp * np.exp(-m / (0.08 * FS)) * np.sin(2 * np.pi * np.cumsum(f) / FS)
    c = int(0.004 * FS)
    s[:c] += amp * 0.4 * rng.standard_normal(c) * np.exp(-np.arange(c) / (0.001 * FS))
    return s


def snare(rng: np.random.Generator, amp: float = 0.4) -> np.ndarray:
    m = np.arange(int(0.18 * FS))
    return amp * np.exp(-m / (0.04 * FS)) * (0.7 * rng.standard_normal(len(m)) + 0.3 * np.sin(2 * np.pi * 190 * m / FS))


def clap(rng: np.random.Generator, amp: float = 0.35) -> np.ndarray:
    m = np.arange(int(0.15 * FS))
    env = np.zeros(len(m))
    for d in (0.0, 0.008, 0.016):                  # three quick bursts, then the tail
        k = int(d * FS)
        env[k:] += np.exp(-(m[k:] - k) / (0.006 * FS))
    env += 0.5 * np.exp(-m / (0.05 * FS))
    n = rng.standard_normal(len(m))
    n = np.diff(n, prepend=0.0)                    # brighter
    return amp * env * n / 2


def hat(rng: np.random.Generator, amp: float = 0.15, open_: bool = False) -> np.ndarray:
    m = np.arange(int((0.18 if open_ else 0.04) * FS))
    n = np.diff(rng.standard_normal(len(m) + 1))
    return amp * np.exp(-m / ((0.06 if open_ else 0.008) * FS)) * n / 2


def bass(f0: float, dur: float, amp: float = 0.25) -> np.ndarray:
    m = np.arange(int(dur * FS))
    s = amp * (np.sin(2 * np.pi * f0 * m / FS) + 0.3 * np.sin(4 * np.pi * f0 * m / FS))
    a = int(0.005 * FS)
    s[:a] *= np.linspace(0, 1, a)
    s *= np.exp(-m / (0.3 * FS))
    return s


def pad(x: np.ndarray, t0: float, t1: float, roots: list[float], bar_s: float, amp: float = 0.06) -> None:
    """Soft sustained chords, one per bar (no attacks to speak of)."""
    t = t0
    k = 0
    while t < t1:
        f = roots[k % len(roots)]
        m = np.arange(int(min(bar_s, t1 - t) * FS))
        env = np.minimum(1.0, m / (0.3 * FS)) * np.minimum(1.0, (len(m) - m) / (0.3 * FS))
        s = sum(np.sin(2 * np.pi * f * r * m / FS + r) for r in (1.0, 1.26, 1.5, 2.0)) / 4
        _add(x, t, amp * env * s)
        t += bar_s
        k += 1


def click(amp: float) -> np.ndarray:
    m = np.arange(int(0.01 * FS))
    return amp * np.exp(-m / (0.002 * FS)) * np.sin(2 * np.pi * 1000 * m / FS)


# -- patterns ----------------------------------------------------------------------------------------------------------
def house120(seed: int = 1) -> tuple[np.ndarray, list[tuple[float, int]]]:
    rng = np.random.default_rng(seed)
    bpm, t0 = 120.0, 0.5
    T = 60.0 / bpm
    x = np.zeros(int(SECONDS * FS))
    gt = []
    roots = [55.0, 43.65, 49.0, 41.2]
    k = 0
    while t0 + k * T < SECONDS - 0.5:
        t = t0 + k * T
        gt.append((t, k % 4 + 1))
        _add(x, t, kick(rng))
        if k % 2:
            _add(x, t, clap(rng))
        _add(x, t + T / 2, hat(rng, 0.16, open_=True))
        for q in (0.25, 0.75):
            _add(x, t + q * T, hat(rng, 0.06))
        _add(x, t + T / 2, bass(roots[(k // 4) % 4], T / 2 * 0.9))
        k += 1
    pad(x, t0, SECONDS - 0.5, [220.0, 174.6, 196.0, 164.8], 4 * T)
    return x, gt


def hiphop92(seed: int = 2) -> tuple[np.ndarray, list[tuple[float, int]]]:
    rng = np.random.default_rng(seed)
    bpm, t0 = 92.0, 0.4
    T = 60.0 / bpm
    x = np.zeros(int(SECONDS * FS))
    gt = []
    k = 0
    while t0 + k * T < SECONDS - 0.5:
        t = t0 + k * T
        pos = k % 4
        gt.append((t, pos + 1))
        if pos == 0:
            _add(x, t, kick(rng, 0.75))
        if pos == 1:
            _add(x, t + T / 2, kick(rng, 0.55))          # the "and" of 2
        if pos == 2:
            _add(x, t, kick(rng, 0.7))
            _add(x, t + 0.75 * T, kick(rng, 0.45))       # the "a" of 3
        if pos in (1, 3):
            _add(x, t, snare(rng, 0.5))
        _add(x, t, hat(rng, 0.14))
        _add(x, t + T / 2, hat(rng, 0.11))
        k += 1
    pad(x, t0, SECONDS - 0.5, [196.0, 233.1, 174.6, 220.0], 4 * T, amp=0.05)
    return x, gt


def click100(seed: int = 3) -> tuple[np.ndarray, list[tuple[float, int]]]:
    bpm, t0 = 100.0, 0.6
    T = 60.0 / bpm
    x = np.zeros(int(SECONDS * FS))
    gt = []
    k = 0
    while t0 + k * T < SECONDS - 0.5:
        t = t0 + k * T
        gt.append((t, k % 4 + 1))
        _add(x, t, click(0.6 if k % 4 == 0 else 0.4))
        k += 1
    return x, gt


PATTERNS = {"house120": (house120, "house", 120.0), "hiphop92": (hiphop92, "hip-hop", 92.0),
            "click100": (click100, "click", 100.0)}


def finish(x: np.ndarray, seed: int) -> np.ndarray:
    """Peak -3 dBFS plus noise at about -70 dBFS RMS: never digital silence."""
    x = x / max(1e-9, float(np.max(np.abs(x)))) * 10 ** (-3 / 20)
    return x + 3e-4 * np.random.default_rng(seed + 100).standard_normal(len(x))


def write_wav(path: pathlib.Path, x: np.ndarray) -> None:
    pcm = np.clip(np.rint(x * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm.tobytes())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    a = ap.parse_args()
    out = a.out.expanduser()
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "beats").mkdir(parents=True, exist_ok=True)
    tracks = []
    for i, (tid, (fn, genre, bpm)) in enumerate(PATTERNS.items()):
        x, gt = fn()
        write_wav(out / "audio" / f"{tid}.wav", finish(x, i))
        (out / "beats" / f"{tid}.beats").write_text("".join(f"{t:.6f}\t{p}\n" for t, p in gt))
        tracks.append({"id": tid, "audio": f"audio/{tid}.wav", "beats": f"beats/{tid}.beats", "genre": genre,
                       "bpm": bpm, "duration_s": SECONDS, "dataset": "synth"})
        print(f"{tid}: {len(gt)} beats at {bpm:g} bpm")
    (out / "index.json").write_text(json.dumps({"name": "synth", "tracks": tracks}, indent=1) + "\n")
    print(f"wrote {out}/index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
