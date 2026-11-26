"""Word timings for a song's lyrics, from its separated lead vocal (docs/KARAOKE.md). Run by Flask's karaoke worker in
the karaoke venv (deployment host.karaoke_python), never inside Flask.

    python tools/karaoke/align.py --vocal vocals.wav --lines lines.json --shift 0.5 > words.json

LRCLIB gives when each line starts; this finds when each WORD is sung. For each line it takes the vocal from just
before the line's start to the next line's start and force-aligns the line's words to it with torchaudio's MMS_FA
(a wav2vec2 CTC model; downloaded once, ~1.2 GB). ``--shift`` is the line times' offset against the audio (the
worker's automatic shift): the audio is read at line time + shift and the word times are written back in line time,
so the display uses one clock for both.

Prints {"lines": [[{"t0", "t1"} | null per space-separated token of the line's text] | null per line], "seconds"}.
A token without letters ("—", "(x2)") gets null; a line that won't align gets null (the display then sweeps it evenly).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

import numpy as np

SR = 16000
PAD_BEFORE_S = 0.4
PAD_AFTER_S = 0.6
MAX_LINE_S = 15.0


def load_mono_16k(path: str) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True, timeout=120)
    return np.frombuffer(p.stdout, dtype=np.float32)


def word_of(token: str) -> str:
    """'(Ooh,' -> 'ooh'; "don't" -> "don't"; '—' -> ''."""
    return re.sub(r"[^a-z']", "", token.lower()).strip("'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True)
    ap.add_argument("--lines", required=True, help="JSON file: [{start, end, text}]")
    ap.add_argument("--shift", type=float, default=0.0)
    a = ap.parse_args()

    import torch
    import torchaudio

    t_start = time.perf_counter()
    lines = json.load(open(a.lines))
    audio = load_mono_16k(a.vocal)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=False).to(dev).eval()
    tokenizer, aligner = bundle.get_tokenizer(), bundle.get_aligner()
    dictionary = bundle.get_dict(star=None)

    out: list = []
    for i, ln in enumerate(lines):
        tokens = (ln.get("text") or "").split()
        words = [word_of(t) for t in tokens]
        words = ["".join(ch for ch in w if ch in dictionary) for w in words]
        if ln.get("start") is None or not any(words):
            out.append(None)
            continue
        # The line's own span (its display end stops at the next line, or sooner across an instrumental gap): a
        # window reaching into a long gap lets the aligner smear the words across it.
        end = ln.get("end") if ln.get("end") is not None else ln["start"] + MAX_LINE_S
        t0 = max(0.0, ln["start"] + a.shift - PAD_BEFORE_S)
        t1 = min(end, ln["start"] + MAX_LINE_S) + a.shift + PAD_AFTER_S
        seg = audio[int(t0 * SR): int(t1 * SR)]
        present = [w for w in words if w]
        try:
            with torch.inference_mode():
                emission, _ = model(torch.from_numpy(seg.copy()).unsqueeze(0).to(dev))
            spans = aligner(emission[0], tokenizer(present))
        except Exception:  # noqa: BLE001  (too short for its words, or nothing to hear): sweep it evenly
            out.append(None)
            continue
        ratio = seg.shape[0] / emission.shape[1] / SR
        timed = iter([(t0 + s[0].start * ratio - a.shift, t0 + s[-1].end * ratio - a.shift) for s in spans])
        row = []
        for w in words:
            if w:
                s, e = next(timed)
                row.append({"t0": round(s, 3), "t1": round(e, 3)})
            else:
                row.append(None)
        got = [x for x in row if x]
        sane = got and got[0]["t0"] >= ln["start"] - PAD_BEFORE_S - 0.01 and got[-1]["t1"] <= end + PAD_AFTER_S + 0.01
        out.append(row if sane else None)
    print(json.dumps({"lines": out, "seconds": round(time.perf_counter() - t_start, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
