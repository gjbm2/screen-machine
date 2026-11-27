"""The careful pass over a song's lyrics (docs/KARAOKE.md "Lyrics off? The careful pass"). Run by Flask's karaoke
worker in the karaoke venv (deployment host.karaoke_python), never inside Flask, when someone refers a song whose
lyrics were badly timed.

    python tools/karaoke/refine.py --dir <song dir> --lines lines.json [--model turbo] > refine.json

The first pass hears only the separated LEAD vocal and times each line on its own, in a window round where it was
thought to be. That fails where the lead isn't what is sung: 27 Nov, Hey Jude's four-minute "na na na" coda is the
backing choir (in the backing track), so Whisper heard nothing there, and each coda line was timed in the wrong place.
This pass:

1. Rebuilds the song (backing + lead stems summed) and takes EVERY voice out of it (BS-Roformer, the all-voices
   model the worker already uses for duets): the choir, the backing vocals and the lead together.
2. Whisper hears that voices stem as well (word timestamps).
3. Force-aligns the WHOLE lyrics to the whole voices stem at once (MMS_FA, emissions in 20 s chunks, one CTC path
   over the song, a "*" before every line to soak up what isn't in the lyrics: ad libs, a guitar solo). Every line
   is placed in order against the others, so a repeat can't land on the wrong chorus, and a line no one was heard
   singing still finds its words.

``lines.json`` is [{text}] in order. Prints (last line) {"heard": [{w, t0, t1}], "lines": [[{t0, t1, score} | null
per space-separated token] | null per line], "seconds"}: score is the mean log-probability of the word's
characters on the path (0 best; under about -3 the model didn't really hear it).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 16000
ALL_VOICES_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
CHUNK_S = 20.0
CONTEXT_S = 2.0


def word_of(token: str) -> str:
    return re.sub(r"[^a-z']", "", token.lower()).strip("'")


def load_mono_16k(path: Path) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True, timeout=300)
    return np.frombuffer(p.stdout, dtype=np.float32)


def voices_stem(d: Path, work: Path) -> Path:
    """Every voice of the song, as a WAV in ``work`` (made once per careful pass)."""
    mix = work / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "instrumental.m4a"), "-i", str(d / "vocals.m4a"),
                    "-filter_complex", "amix=inputs=2:normalize=0", "-ac", "2", "-ar", "44100", str(mix)],
                   check=True, capture_output=True, timeout=300)
    out = work / "sep"
    out.mkdir()
    p = subprocess.run([sys.executable, str(HERE / "separate.py"), "--in", str(mix), "--out-dir", str(out),
                        "--model", ALL_VOICES_MODEL], capture_output=True, text=True, timeout=900)
    last = (p.stdout or "").strip().splitlines()[-1:] or [""]
    if p.returncode != 0 or not last[0].startswith("{") or not json.loads(last[0]).get("ok"):
        raise RuntimeError("separating every voice failed: " + (((p.stderr or "").strip().splitlines() or [last[0]])[-1])[:300])
    mix.unlink(missing_ok=True)
    return out / "vocals.wav"


def hear(wav: Path, model_name: str) -> list[dict]:
    import torch
    import whisper
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model(model_name, device=dev,
                               download_root=os.path.expanduser("~/.cache/screen-machine/karaoke-models/whisper"))
    res = model.transcribe(str(wav), word_timestamps=True, condition_on_previous_text=False, fp16=dev == "cuda",
                           no_speech_threshold=0.5, verbose=None)
    del model
    torch.cuda.empty_cache()
    return [{"w": w["word"].strip(), "t0": round(float(w["start"]), 3), "t1": round(float(w["end"]), 3)}
            for seg in res.get("segments", []) for w in seg.get("words", []) if w.get("word", "").strip()]


def align_song(audio: np.ndarray, texts: list[str]) -> list:
    """One CTC path for the whole lyrics over the whole song (MMS_FA with the star token)."""
    import torch
    import torchaudio
    import torchaudio.functional as F

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    bundle = torchaudio.pipelines.MMS_FA
    model = bundle.get_model(with_star=True).to(dev).eval()
    dictionary = bundle.get_dict(star="*")
    # Emissions chunk by chunk (the whole song at once won't fit: attention over every frame), each with context
    # either side that is then cut away.
    n, step, ctx = len(audio), int(CHUNK_S * SR), int(CONTEXT_S * SR)
    parts = []
    with torch.inference_mode():
        for a in range(0, n, step):
            lo, hi = max(0, a - ctx), min(n, a + step + ctx)
            em, _ = model(torch.from_numpy(audio[lo:hi].copy()).unsqueeze(0).to(dev))
            fps = em.shape[1] / ((hi - lo) / SR)
            c0 = int(round((a - lo) / SR * fps))
            c1 = c0 + int(round((min(n, a + step) - a) / SR * fps))
            parts.append(em[0, c0:c1].float().cpu())
    emission = torch.cat(parts).log_softmax(-1) if parts else None
    del model
    if dev == "cuda":
        torch.cuda.empty_cache()
    sec_per_frame = n / SR / emission.shape[0]

    # The token sequence: per line, a star (anything not in the lyrics before it), then each word's characters.
    star = dictionary["*"]
    seq: list[int] = []
    owners: list[tuple[int, int] | None] = []      # per token: (line, token index in the line) or None (a star)
    for li, text in enumerate(texts):
        seq.append(star)
        owners.append(None)
        for ti, tok in enumerate(text.split()):
            w = "".join(ch for ch in word_of(tok) if ch in dictionary)
            for ch in w:
                seq.append(dictionary[ch])
                owners.append((li, ti))
    seq.append(star)
    owners.append(None)
    blank = 0
    targets = torch.tensor([seq], dtype=torch.int32)
    # CTC needs a frame per token plus a blank between repeats: a song with more letters than frames can't align.
    if emission.shape[0] < len(seq) * 2:
        return [None] * len(texts)
    ali, scores = F.forced_align(emission.unsqueeze(0), targets, blank=blank)
    toks = F.merge_tokens(ali[0], scores[0])           # one span per target token, in order
    spans: dict[tuple[int, int], list] = {}
    for k, t in enumerate(toks):
        if k >= len(owners) or owners[k] is None:
            continue
        s = spans.setdefault(owners[k], [t.start, t.end, 0.0, 0])
        s[1], s[2], s[3] = t.end, s[2] + float(t.score), s[3] + 1
    out: list = []
    for li, text in enumerate(texts):
        row = []
        for ti, _ in enumerate(text.split()):
            s = spans.get((li, ti))
            row.append(None if s is None else {"t0": round(s[0] * sec_per_frame, 3),
                                               "t1": round(s[1] * sec_per_frame, 3),
                                               "score": round(s[2] / max(1, s[3]), 2)})
        out.append(row if any(row) else None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--lines", required=True)
    ap.add_argument("--model", default="turbo")
    ap.add_argument("--keep", help="keep the voices stem here (for looking into a song)")
    a = ap.parse_args()
    t0 = time.perf_counter()
    d = Path(a.dir)
    texts = [str(x.get("text") or "") for x in json.load(open(a.lines))]
    work = Path(tempfile.mkdtemp(prefix="tmp-refine-", dir=d))     # tmp*: the worker clears it if cut short
    try:
        voices = voices_stem(d, work)
        if a.keep:
            shutil.copy(voices, a.keep)
        heard = hear(voices, a.model)
        lines = align_song(load_mono_16k(voices), texts) if texts else []
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(json.dumps({"heard": heard, "lines": lines, "seconds": round(time.perf_counter() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
