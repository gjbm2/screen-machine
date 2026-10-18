#!/usr/bin/env python3
"""Pseudo ground truth for music that has no beat annotations (the test tracks): madmom's offline downbeat tracker
(RNNDownBeatProcessor + DBNDownBeatTrackingProcessor, bidirectional, the whole excerpt at once), written in the
dataset layout so run_batch.py can replay them. It is a strong offline tracker, not the truth: use it to check the
harness and to compare the live stack's runs with each other, and the real datasets to judge the stack.

  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python tools/room/beat_eval/pseudo_gt.py \\
      [--music ~/.cache/screen-machine/test-music] [--out ~/.cache/screen-machine/beat-eval/pseudo-gt] [--dur-s 120]

Writes <out>/beats/<id>.beats ("<time_s>\\t<pos>") and <out>/index.json (audio = the original file, dur_s = the
excerpt annotated, so the replay covers the same span). Tracks already done are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import time

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):  # one BLAS thread: the host renders 4K
    os.environ.setdefault(_k, "1")

import numpy as np  # noqa: E402

MUSIC = pathlib.Path("~/.cache/screen-machine/test-music").expanduser()
OUT = pathlib.Path("~/.cache/screen-machine/beat-eval/pseudo-gt").expanduser()


def decode44(path: pathlib.Path, dur_s: float) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-t", f"{dur_s:.3f}", "-f", "f32le",
                        "-ac", "1", "-ar", "44100", "-"], capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace")[-300:])
    return np.frombuffer(p.stdout, "<f4").copy()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--music", type=pathlib.Path, default=MUSIC, help="a folder with index.json {id: {file, genre}}")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("--dur-s", type=float, default=120.0, help="annotate (and later replay) this much of each track")
    a = ap.parse_args()
    from madmom.audio.signal import Signal
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor

    music, out = a.music.expanduser(), a.out.expanduser()
    src = json.loads((music / "index.json").read_text())
    (out / "beats").mkdir(parents=True, exist_ok=True)
    rnn = RNNDownBeatProcessor()
    dbn = DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
    tracks = []
    for tid, e in src.items():
        if not isinstance(e, dict) or not e.get("file"):
            continue
        audio = music / e["file"]
        dur = min(a.dur_s, float(e.get("duration_s") or a.dur_s))
        bp = out / "beats" / f"{tid}.beats"
        if not bp.exists():
            t0 = time.perf_counter()
            x = decode44(audio, dur)
            act = rnn(Signal(x, sample_rate=44100, num_channels=1))
            beats = dbn(act)
            bp.write_text("".join(f"{t:.4f}\t{int(p)}\n" for t, p in beats))
            print(f"{tid}: {len(beats)} beats in {dur:.0f} s, {time.perf_counter() - t0:.1f} s")
        tracks.append({"id": tid, "audio": str(audio), "beats": f"beats/{tid}.beats", "genre": e.get("genre", ""),
                       "dataset": "test-music", "dur_s": dur, "pseudo_gt": "madmom DBNDownBeatTracker (offline)"})
    (out / "index.json").write_text(json.dumps({"name": "test-music", "tracks": tracks}, indent=1) + "\n")
    print(f"wrote {out}/index.json ({len(tracks)} tracks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
