"""A karaoke song's beat grid from its backing track (docs/AD_MUSIC_FIT_REVIEW_2026-11.md §4 item 1).

The song is a file before it plays, so its beats are known exactly. Beat This! (the show's own ONNX export,
``beat-this-small0``, as the ML worker's beats task: show_service/ml/models.py) runs over the whole backing track in
30 s windows with a second's overlap kept from each side, and its "minimal" peaks give every beat and downbeat. The
show's clock then plays this grid along the karaoke player's position (show_service/karaoke_feed.py ``FileGrid``)
instead of re-finding the beat from the room mic: on the beat from the first bar, no octave flips.

    beats.json = {"version", "model", "beats": [s], "downbeats": [s], "bpm", "meter", "source": "instrumental"}

Run in the ML venv (onnxruntime), on the CPU at nice 19 with one thread (about 1-3 s a 30 s window):

    ~/.venvs/sm-show-ml/bin/python -I tools/karaoke/beats.py <song_dir>        # one song
    ~/.venvs/sm-show-ml/bin/python -I tools/karaoke/beats.py --all            # every library song without one
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "1")
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

VERSION = 1
MODEL = "beat-this-small0"
CHUNK = 1500            # frames (30 s at 50 fps): the window the live worker runs, and Beat This!'s own training length
BORDER = 50             # frames kept from each side of a window's join (1 s): the model is weaker at its edges
LEVEL_DBFS = -20.0      # the whole song's RMS goes here first (the live task levels each window the same way)
SONGS = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "karaoke" / "songs"
MODELS = pathlib.Path.home() / ".cache" / "screen-machine" / "models"


def decode(path: pathlib.Path, fs: int) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(fs), "-f", "f32le", "-"],
                       capture_output=True, check=True, timeout=300)
    return np.frombuffer(p.stdout, np.float32).copy()


def logits(model, spect: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Beat and downbeat logits for the whole song, window by window (CHUNK frames, BORDER kept off each join)."""
    T = len(spect)
    beat, down = np.full(T, -10.0, np.float32), np.full(T, -10.0, np.float32)
    names = [o.name for o in model.sess.get_outputs()]
    outs = model.facts.get("outputs", {})
    want = [outs.get("beat", names[0]), outs.get("downbeat", names[-1])]
    step = CHUNK - 2 * BORDER
    s = 0
    while True:
        e = min(T, s + CHUNK)
        b, d = model.sess.run(want, {model.input_name: spect[s:e][None]})
        b, d = np.asarray(b)[0], np.asarray(d)[0]
        k0 = 0 if s == 0 else BORDER
        k1 = (e - s) if e >= T else (e - s) - BORDER
        beat[s + k0:s + k1], down[s + k0:s + k1] = b[k0:k1], d[k0:k1]
        if e >= T:
            break
        s += step
    return beat, down


def meter_of(beats: np.ndarray, downs: np.ndarray) -> int:
    """The beats in a bar: the most common count between consecutive downbeats (3 or 4 mostly); 4 when unsure."""
    if len(downs) < 3:
        return 4
    idx = np.searchsorted(beats, downs)
    gaps = np.diff(idx)
    gaps = gaps[(gaps >= 2) & (gaps <= 7)]
    if not len(gaps):
        return 4
    vals, n = np.unique(gaps, return_counts=True)
    return int(vals[int(np.argmax(n))])


def analyse(song_dir: pathlib.Path, models_dir: pathlib.Path = MODELS) -> dict:
    from show_service.ml import models as M
    index = json.loads((models_dir / "index.json").read_text())
    model = M.Model(MODEL, models_dir, index)
    inp = model.facts.get("input", {})
    fs = int(inp.get("fs", 22050))
    x = decode(song_dir / "instrumental.m4a", fs)
    x = M.to_level(x, LEVEL_DBFS, 40.0)
    spect = M.beat_logmel(x, fs=fs, n_fft=int(inp.get("n_fft", 1024)), hop=int(inp.get("hop", 441)),
                          f_min=float(inp.get("f_min", 30.0)), f_max=float(inp.get("f_max", 11000.0)),
                          n_mels=int(inp.get("n_mels", 128)), log_multiplier=float(inp.get("log_multiplier", 1000.0)))
    fps = float(inp.get("fps", fs / int(inp.get("hop", 441))))
    b, d = logits(model, spect)
    beats, downs = M.minimal_peaks(b, d, fps)
    ibi = np.diff(beats)
    bpm = float(60.0 / np.median(ibi)) if len(ibi) else 0.0
    return {"version": VERSION, "model": MODEL, "source": "instrumental",
            "beats": [round(float(t), 3) for t in beats], "downbeats": [round(float(t), 3) for t in downs],
            "bpm": round(bpm, 2), "meter": meter_of(beats, downs), "made_at": time.time()}


def write(song_dir: pathlib.Path, models_dir: pathlib.Path = MODELS) -> dict:
    d = analyse(song_dir, models_dir)
    tmp = song_dir / "beats.tmp.json"
    tmp.write_text(json.dumps(d))
    os.replace(tmp, song_dir / "beats.json")
    return d


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("song_dir", nargs="?", type=pathlib.Path)
    ap.add_argument("--all", action="store_true", help="every library song with a backing track and no beats.json")
    ap.add_argument("--force", action="store_true", help="redo songs that already have one")
    ap.add_argument("--models", type=pathlib.Path, default=MODELS)
    a = ap.parse_args()
    try:
        os.nice(19)
    except OSError:
        pass
    dirs = sorted(p for p in SONGS.iterdir() if p.is_dir()) if a.all else [a.song_dir] if a.song_dir else []
    if not dirs:
        ap.error("a song folder, or --all")
    bad = 0
    for p in dirs:
        if not (p / "instrumental.m4a").is_file():
            continue
        if (p / "beats.json").is_file() and not a.force:
            continue
        t0 = time.time()
        try:
            d = write(p, a.models)
            print(f"{p.name}: {len(d['beats'])} beats, {d['bpm']} bpm, {d['meter']}/4, {time.time() - t0:.1f} s",
                  flush=True)
        except Exception as e:  # noqa: BLE001  one song's fault never stops the batch
            bad += 1
            print(f"{p.name}: failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    return 1 if bad and not a.all else 0


if __name__ == "__main__":
    sys.exit(main())
