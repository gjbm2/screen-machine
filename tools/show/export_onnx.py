#!/usr/bin/env python3
"""Export a model that has no published ONNX file, once, in a scratch venv (show_service/README.md S4.4 "Exports").

    flock /tmp/claude-1000/sm-build.lock nice -n 19 /tmp/claude-1000/onnx-export/venv/bin/python \\
        tools/show/export_onnx.py beat-this-small0 [--dir D] [--measure-16k] [--no-install]

The scratch venv (never ~/.venvs/sm-show-ml; torch CPU wheels only, from the CPU index):

    python3.12 -m venv /tmp/claude-1000/onnx-export/venv
    /tmp/claude-1000/onnx-export/venv/bin/pip install --index-url https://download.pytorch.org/whl/cpu \\
        --extra-index-url https://pypi.org/simple "torch==2.14.1+cpu" "torchaudio==2.11.0+cpu" "beat-this==1.1.0" \\
        "rotary-embedding-torch==0.9.1" "einops==0.8.2" "numpy==2.5.4" "soxr==1.1.0" "onnxruntime==1.31.0" onnx

What it does for ``beat-this-*`` (Beat This!, beat-this==1.1.0, MIT):
1. downloads the upstream checkpoint (fetch_models.py's ``upstream``) and checks its sha256;
2. exports the network (log-mel frames in, beat and downbeat logits out) with the TorchScript exporter, opset 17, a
   dynamic batch and time axis (torch.export's decomposition fails on this model in torch 2.14);
3. checks the ONNX against torch on three 30 s clips of the test music: max abs difference <= 1e-3 on the logits;
4. checks the worker's numpy front end (``show_service.ml.models.beat_logmel``) against beat_this's ``LogMelSpect``
   and the numpy "minimal" post-processing against beat_this's ``Postprocessor``;
5. writes ``D/<file>`` and its index.json entry (``source: "export"``, the upstream checkpoint's URL and sha256).

``--measure-16k``: Beat This! hears 22.05 kHz; the worker's feed is 16 kHz. Beat and downbeat F-measure (70 ms) of
the 16 kHz-limited input against full-band input, on every test track (README S4.4: the numbers go in "As built").
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import numpy as np  # noqa: E402

from show_service.ml import models as M  # noqa: E402  (numpy-only at import)

_spec = importlib.util.spec_from_file_location("fetch_models", REPO / "tools" / "show" / "fetch_models.py")
FM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FM)

SCRATCH = pathlib.Path("/tmp/claude-1000/onnx-export")
MUSIC = pathlib.Path.home() / ".cache" / "screen-machine" / "test-music"
CLIPS = (("house-galeria-disco-memorias", 60), ("jazz-doxent-1973", 40), ("rock-josh-woodward-airplane-mode", 50))
BEAT_FACTS = {"kind": "beat_tracker",
              "input": {"name": "spect", "type": "beat_this_logmel", "fs": 22050, "n_fft": 1024, "hop": 441,
                        "f_min": 30.0, "f_max": 11000.0, "n_mels": 128, "log_multiplier": 1000.0, "fps": 50},
              "outputs": {"beat": "beat", "downbeat": "downbeat"}, "output_is": "logits", "postprocess": "minimal",
              "trained_chunk_frames": 1500}


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def decode(path: pathlib.Path, fs: int, start: float, dur: float) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", str(path),
                          "-f", "f32le", "-ac", "1", "-ar", str(fs), "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4").copy()


def track_path(tid: str) -> pathlib.Path:
    idx = json.loads((MUSIC / "index.json").read_text())
    return MUSIC / idx[tid]["file"]


def fmeasure(ref: np.ndarray, est: np.ndarray, tol: float = 0.07) -> float:
    """Beat F-measure (mir_eval's rule: one-to-one matches within +-tol)."""
    if len(ref) == 0 and len(est) == 0:
        return 1.0
    if len(ref) == 0 or len(est) == 0:
        return 0.0
    used, hits = set(), 0
    for r in ref:
        d = np.abs(est - r)
        for j in np.argsort(d):
            if d[j] > tol:
                break
            if j not in used:
                used.add(j)
                hits += 1
                break
    p, r = hits / len(est), hits / len(ref)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def get_ckpt(entry: dict) -> pathlib.Path:
    up = entry["upstream"]
    dest = SCRATCH / "ckpt" / pathlib.Path(up["url"]).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or sha256_file(dest) != up["sha256"]:
        req = urllib.request.Request(up["url"], headers={"User-Agent": FM.USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest.with_suffix(".part"), "wb") as f:
            f.write(r.read())
        os.replace(dest.with_suffix(".part"), dest)
    got = sha256_file(dest)
    if got != up["sha256"]:
        raise SystemExit(f"checkpoint sha256 {got} is not the pinned {up['sha256']}")
    return dest


def export_beat_this(name: str, dest_dir: pathlib.Path, install: bool, measure: bool) -> int:
    import onnxruntime as ort
    import torch
    from beat_this.inference import load_model
    from beat_this.model.postprocessor import Postprocessor
    from beat_this.preprocessing import LogMelSpect
    torch.set_num_threads(1)

    entry = FM.BY_NAME[name]
    ckpt = get_ckpt(entry)
    model = load_model(str(ckpt), "cpu")

    class Net(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, spect):
            o = self.m(spect)
            return o["beat"], o["downbeat"]

    net = Net(model).eval()
    out_tmp = SCRATCH / f"{entry['file']}.tmp"
    t0 = time.time()
    torch.onnx.export(net, (torch.randn(1, 1500, 128),), str(out_tmp), dynamo=False, opset_version=17,
                      input_names=["spect"], output_names=["beat", "downbeat"],
                      dynamic_axes={"spect": {0: "batch", 1: "frames"}, "beat": {0: "batch", 1: "frames"},
                                    "downbeat": {0: "batch", 1: "frames"}})
    print(f"exported {name} in {time.time() - t0:.1f} s ({out_tmp.stat().st_size / 1e6:.1f} MB)")

    so = ort.SessionOptions()
    so.intra_op_num_threads = so.inter_op_num_threads = 1
    sess = ort.InferenceSession(str(out_tmp), so, providers=["CPUExecutionProvider"])
    lms = LogMelSpect()
    post = Postprocessor(type="minimal")
    worst = worst_mel = 0.0
    for tid, start in CLIPS:
        x = decode(track_path(tid), 22050, start, 30.0)
        with torch.inference_mode():
            spect_t = lms(torch.from_numpy(x))
            b_t, d_t = net(spect_t[None])
        spect_np = M.beat_logmel(x, **{k: v for k, v in BEAT_FACTS["input"].items()
                                       if k in ("fs", "n_fft", "hop", "f_min", "f_max", "n_mels", "log_multiplier")})
        mel_diff = float(np.abs(spect_np - spect_t.numpy()).max())
        t1 = time.perf_counter()
        b_o, d_o = sess.run(None, {"spect": spect_t.numpy()[None]})
        cost = (time.perf_counter() - t1) * 1000
        diff = max(float(np.abs(b_o - b_t.numpy()).max()), float(np.abs(d_o - d_t.numpy()).max()))
        ref_b, ref_d = post(b_t[0], d_t[0])
        b_np, d_np = M.minimal_peaks(b_o[0], d_o[0], 50.0)
        b_full, _ = M.minimal_peaks(*[a[0] for a in sess.run(None, {"spect": spect_np[None]})], 50.0)
        same = np.array_equal(np.round(np.asarray(ref_b), 6), np.round(b_np, 6)) and \
            np.array_equal(np.round(np.asarray(ref_d), 6), np.round(d_np, 6))
        print(f"  {tid} @{start}s: onnx vs torch max |d| {diff:.2e}; numpy mel vs LogMelSpect max |d| "
              f"{mel_diff:.2e}; numpy post == reference: {same}; numpy front end F {fmeasure(np.asarray(ref_b), b_full):.3f}; "
              f"onnx {cost:.0f} ms (1 thread)")
        worst, worst_mel = max(worst, diff), max(worst_mel, mel_diff)
        if not same:
            raise SystemExit("the numpy post-processing differs from beat_this's")
    if worst > 1e-3:
        raise SystemExit(f"ONNX differs from torch by {worst:.2e} (> 1e-3): not installed")

    if measure:
        measure_16k(sess)

    if install:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / entry["file"]
        os.replace(out_tmp, dest)
        index = FM.read_index(dest_dir / "index.json")
        index[name] = {"file": entry["file"], "sha256": sha256_file(dest), "bytes": dest.stat().st_size,
                       "license": entry["license"], "license_url": entry["license_url"], "page": entry["page"],
                       "credit": entry["credit"], "task": entry["task"], "source": "export",
                       "upstream": entry["upstream"],
                       "export": {"tool": "tools/show/export_onnx.py", "beat_this": "1.1.0",
                                  "torch": torch.__version__, "opset": 17,
                                  "check": f"onnx vs torch max |d| {worst:.1e} on 3 clips; numpy mel max |d| "
                                           f"{worst_mel:.1e}"},
                       "facts": BEAT_FACTS, "fetched_at": FM._now()}
        FM.write_index(dest_dir, index)
        print(f"installed {dest} and its index entry")
    return 0


def measure_16k(sess) -> None:
    import soxr
    idx = json.loads((MUSIC / "index.json").read_text())
    rows, fb_all, fd_all = [], [], []
    for tid, e in sorted(idx.items()):
        dur = float(e.get("duration_s") or 120)
        for start in (30.0, max(60.0, dur / 2), max(90.0, dur - 60)):
            if start + 30 > dur:
                continue
            full = decode(MUSIC / e["file"], 22050, start, 30.0)
            lim = soxr.resample(decode(MUSIC / e["file"], 16000, start, 30.0), 16000, 22050, quality="HQ")
            res = []
            for x in (full, lim):
                b, d = sess.run(None, {"spect": M.beat_logmel(x)[None]})
                res.append(M.minimal_peaks(b[0], d[0], 50.0))
            (bf, df), (bl, dl) = res
            inner = lambda a: a[(a >= 3) & (a <= 27)]          # noqa: E731  the interior, as the worker reports
            fb, fd = fmeasure(inner(bf), inner(bl)), fmeasure(inner(df), inner(dl))
            fb_all.append(fb)
            fd_all.append(fd)
            rows.append((tid, start, len(inner(bf)), fb, fd))
    for tid, start, n, fb, fd in rows:
        print(f"  16k vs full: {tid:<38} @{start:5.0f}s beats {n:3d}  F(beat) {fb:.3f}  F(downbeat) {fd:.3f}")
    print(f"16k-limited vs full-band, {len(rows)} windows: beat F mean {np.mean(fb_all):.3f} (min {np.min(fb_all):.3f}),"
          f" downbeat F mean {np.mean(fd_all):.3f} (min {np.min(fd_all):.3f})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", choices=[e["name"] for e in FM.CATALOGUE if e["source"] == "export"])
    ap.add_argument("--dir", default=str(FM.DEFAULT_DIR))
    ap.add_argument("--measure-16k", action="store_true")
    ap.add_argument("--no-install", action="store_true", help="check only; don't write D")
    a = ap.parse_args(argv)
    if a.name.startswith("beat-this-"):
        return export_beat_this(a.name, pathlib.Path(a.dir).expanduser(), not a.no_install, a.measure_16k)
    return 2


if __name__ == "__main__":
    sys.exit(main())
