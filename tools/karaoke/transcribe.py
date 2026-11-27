"""What is actually sung, and when: Whisper on a song's separated lead vocal (docs/KARAOKE.md). Run by Flask's karaoke
worker in the karaoke venv (deployment host.karaoke_python), never inside Flask.

    python tools/karaoke/transcribe.py --vocal vocals.wav [--model turbo] > sung.json

Prints (last line) {"words": [{"w", "t0", "t1"}], "seconds", "model"}: every word Whisper hears, in song seconds.
The worker matches the lyric candidates' words against these to choose the lyrics that fit this recording and to
time each line to when it is really sung (routes/karaoke/lyrics.py fit_lines). Models are cached in
~/.cache/screen-machine/karaoke-models/whisper.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocal", required=True)
    ap.add_argument("--model", default="turbo")
    ap.add_argument("--language", default=None)
    a = ap.parse_args()

    import torch
    import gpu_pace  # the worker slows it while something plays live (routes/karaoke/pace.py)
    gpu_pace.install()
    import whisper

    t0 = time.perf_counter()
    root = os.path.expanduser("~/.cache/screen-machine/karaoke-models/whisper")
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model(a.model, device=dev, download_root=root)
    res = model.transcribe(a.vocal, word_timestamps=True, language=a.language, condition_on_previous_text=False,
                           fp16=dev == "cuda", no_speech_threshold=0.5, verbose=None)
    words = [{"w": w["word"].strip(), "t0": round(float(w["start"]), 3), "t1": round(float(w["end"]), 3)}
             for seg in res.get("segments", []) for w in seg.get("words", []) if w.get("word", "").strip()]
    print(json.dumps({"words": words, "language": res.get("language"), "model": a.model,
                      "seconds": round(time.perf_counter() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
