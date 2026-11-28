"""Each lyric line's voice, as heard on the separated lead vocal: for telling a duet's two singers apart
(routes/karaoke/duet.py; docs/KARAOKE.md "Duets"). Run by Flask's karaoke worker in the karaoke venv.

    python tools/karaoke/voices.py --dir <song dir> --lines lines.json > voices.json

``lines.json`` is the song's lyric lines ([{start, end, words: [{t0, t1} | null]}]). Per line, over the sung words
only (the line's span when it has no word times):

  midi    the median pitch of its loud frames (YIN), as a MIDI note number; a man and a woman are most often
          an octave or so apart
  voiced  the share of its frames with a pitch
  emb     a speaker embedding (WeSpeaker ResNet34, VoxCeleb, 256 numbers, unit length): who it sounds like,
          whatever the register. 28 Nov, Shallow: Gaga sings the second verse in Cooper's register (pitch can't
          tell them apart), but the two verses' lines are ~0.5 alike within a verse and ~0.15 across.

Prints (last line) {"lines": [{midi, voiced, emb} | null per line], "seconds"}. null: too little sung to say.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

SR = 16000
HOP = 320                              # 20 ms pitch frames
MIN_SUNG_S = 0.8                       # a line sung for less than this: no voice to tell
MODEL_URL = "https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/resolve/main/voxceleb_resnet34_LM.onnx"
MODEL = Path(os.path.expanduser("~/.cache/screen-machine/karaoke-models/speaker/voxceleb_resnet34_LM.onnx"))
MODEL_MIN_BYTES = 20_000_000


def load_mono_16k(path: Path) -> np.ndarray:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, check=True, timeout=300)
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def model_path() -> Path:
    """The speaker model, fetched once (26 MB)."""
    if not MODEL.is_file() or MODEL.stat().st_size < MODEL_MIN_BYTES:
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        tmp = MODEL.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        if tmp.stat().st_size < MODEL_MIN_BYTES:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("the speaker model didn't download")
        tmp.replace(MODEL)
    return MODEL


def spans_of(line: dict) -> list[tuple[float, float]]:
    ws = [(float(w["t0"]), float(w["t1"])) for w in (line.get("words") or ())
          if isinstance(w, dict) and w.get("t0") is not None and w.get("t1") is not None]
    if not ws and line.get("start") is not None and line.get("end") is not None:
        ws = [(float(line["start"]), float(line["end"]))]
    return [(a, max(b, a + 0.05)) for a, b in ws]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--lines", required=True)
    a = ap.parse_args()
    t_start = time.perf_counter()
    import librosa
    import onnxruntime as ort
    import torch
    import torchaudio.compliance.kaldi as kaldi

    torch.set_num_threads(1)           # 28 Nov: on every core, its fbank took 55 of 65 s spinning (12 min of CPU)
    lines = json.load(open(a.lines))
    y = load_mono_16k(Path(a.dir) / "vocals.m4a")
    so = ort.SessionOptions()
    so.log_severity_level = 3
    so.intra_op_num_threads = 2
    sess = ort.InferenceSession(str(model_path()), so, providers=["CPUExecutionProvider"])

    def embed(x: np.ndarray) -> np.ndarray:
        w = torch.from_numpy(x).unsqueeze(0) * 32768.0
        f = kaldi.fbank(w, num_mel_bins=80, frame_length=25, frame_shift=10, dither=0.0, sample_frequency=SR,
                        window_type="hamming")
        f = f - f.mean(0)
        e = sess.run(None, {"feats": f.unsqueeze(0).numpy()})[0][0]
        return e / (np.linalg.norm(e) + 1e-9)

    out: list = []
    for line in lines:
        segs = [y[int(s * SR):int(e * SR)] for s, e in spans_of(line)]
        seg = np.concatenate(segs) if segs else np.zeros(0, np.float32)
        if len(seg) < MIN_SUNG_S * SR or float(np.sqrt(np.mean(seg ** 2))) < 1e-4:
            out.append(None)
            continue
        # YIN (pYIN took ~3 min a song, 28 Nov), its frames kept where the voice is loud enough to have a pitch
        f0 = librosa.yin(seg, fmin=70, fmax=1000, sr=SR, frame_length=1024, hop_length=HOP)
        rms = librosa.feature.rms(y=seg, frame_length=1024, hop_length=HOP)[0][:len(f0)]
        loud = rms > max(1e-4, 0.25 * float(np.percentile(rms, 90)))
        hz = f0[:len(rms)][loud]
        out.append({"midi": round(float(np.median(librosa.hz_to_midi(hz))), 2) if len(hz) >= 5 else None,
                    "voiced": round(len(hz) / max(1, len(f0)), 2),
                    "emb": [round(float(v), 4) for v in embed(seg)]})
    print(json.dumps({"lines": out, "seconds": round(time.perf_counter() - t_start, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
