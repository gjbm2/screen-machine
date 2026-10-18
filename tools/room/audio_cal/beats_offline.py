#!/usr/bin/env python3
"""Offline beat trackers for the audio calibration (analyse.py runs this in a subprocess, one env per tracker).

Two trackers give the pseudo ground truth of a calibration original, and tell us what survives in the recording:
  madmom     madmom's offline RNNBeatProcessor (8 bidirectional LSTMs, 100 fps activation) + DBNBeatTrackingProcessor.
             Runs in show_service's env (~/.venvs/sm-show, madmom from git), read-only.
  madmom_online  the causal side, as the live clock hears music: the first --nets (2) online nets and the DBN's
             online mode. Both madmom modes scale the input to a fixed RMS first (madmom is not level-invariant).
  beatthis   Beat This! (small0), the ONNX export the ML worker runs (show_service/ml/models.py: the same log-mel front
             end, ``beat_logmel``, and the same "minimal" peak picking, ``minimal_peaks``), here on a whole track: the
             upstream split/aggregate (1500-frame chunks, 6-frame borders, earlier chunks win the overlap).
             Runs in the ML worker's env (~/.venvs/sm-show-ml), read-only, one onnxruntime thread.
  dsphits    not a beat tracker: show_service's own dsp.Features fed the recording in 128-frame blocks (as show-rx
             feeds a VBAN stream), collecting every music.hit (band, time). analyse.py pairs them with the hits the
             live bus logged, which maps the bus's host time onto the recording exactly (the same detector on the
             same audio). Runs in show_service's env, read-only; 48 kHz input.

Audio goes in as float32 mono .npy files at the tracker's own rate (madmom 44.1 kHz, Beat This! 22.05 kHz; analyse.py
resamples with soxr), so neither env needs soundfile or scipy. Results come back as one JSON file:
  {"tracker": ..., "files": {"<npy path>": {"beats": [s...], "downbeats": [s...] (beatthis), "act": "<npy path>",
   "fps": 100 | 50, "seconds": <compute time>}} , "errors": {...}}
  dsphits: {"tracker": "dsphits", "files": {"<npy path>": {"hits": {"<band>": [s...]}, "seconds": ...}}, "errors"}
``act`` is the frame activation (madmom: beat probability; Beat This!: beat logits), saved next to the input.

Usage (analyse.py does this; run it under the CPU lock, nice 19):
  ~/.venvs/sm-show/bin/python beats_offline.py madmom --out res.json a.npy b.npy
  ~/.venvs/sm-show/bin/python beats_offline.py madmom_online --nets 2 --out res.json a.npy b.npy
  ~/.venvs/sm-show-ml/bin/python beats_offline.py beatthis --out res.json --models-dir ~/.cache/screen-machine/models a.npy
  ~/.venvs/sm-show/bin/python beats_offline.py dsphits --out res.json rec-48000.npy
"""
import argparse
import json
import os
import pathlib
import sys
import time

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "1")      # the host renders 4K: one core, never a pool

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]

MADMOM_FS = 44100
MADMOM_RMS_DBFS = -14.0         # madmom's input level offline (about a mastered track's RMS)
MADMOM_RMS_ONLINE_DBFS = -12.0  # ... and online (screen-machine-26's reference runs, 18 Oct)
DSP_FS = 48000
DSP_BLOCK = 128         # a VBAN packet (mono INT16): what show-rx hands dsp
BEATTHIS_FS = 22050
CHUNK = 1500            # Beat This!'s training excerpt, frames at 50 fps (index.json facts.trained_chunk_frames)
BORDER = 6              # frames dropped at each chunk edge (upstream inference default)


def run_madmom(paths: list[str], online: bool = False, nets: int = 8) -> dict:
    """madmom's RNN beat activation + DBN tracker. Offline: 8 bidirectional nets and the offline DBN (the pseudo
    ground truth). ``online``: the causal nets (the first ``nets`` of BEATS_LSTM; the live clock runs 2) and the
    DBN's online mode, as the live clock hears music. The input is first scaled to a fixed RMS (MADMOM_RMS_DBFS
    offline, MADMOM_RMS_ONLINE_DBFS online: madmom's log spectrogram is not level-invariant, and the clock has its
    own AGC), so a quiet mic and a mastered file are compared on their content."""
    from madmom.audio.signal import Signal
    from madmom.features.beats import DBNBeatTrackingProcessor, RNNBeatProcessor
    if online:
        from madmom.models import BEATS_LSTM
        rnn = RNNBeatProcessor(online=True, nn_files=BEATS_LSTM[:max(1, nets)])
    else:
        rnn = RNNBeatProcessor()                 # offline: 8 BLSTM nets, averaged
    rms_db = MADMOM_RMS_ONLINE_DBFS if online else MADMOM_RMS_DBFS
    out, errors = {}, {}
    for p in paths:
        t0 = time.process_time()
        try:
            x = np.load(p).astype(np.float32)
            rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
            if rms > 0:
                x = (x * (10 ** (rms_db / 20) / rms)).astype(np.float32)
            act = rnn(Signal(x, sample_rate=MADMOM_FS, num_channels=1))
            if online:
                dbn = DBNBeatTrackingProcessor(fps=100, min_bpm=45, max_bpm=200, online=True)
                beats = np.atleast_1d(dbn.process_online(act, reset=True))
            else:
                beats = DBNBeatTrackingProcessor(fps=100)(act)      # madmom's DBNBeatTracker defaults (55-215 bpm)
            tag = "madmom_online_act" if online else "madmom_act"
            act_path = str(pathlib.Path(p).with_suffix("")) + f".{tag}.npy"
            np.save(act_path, np.asarray(act, dtype=np.float32))
            out[p] = {"beats": [float(b) for b in beats], "act": act_path, "fps": 100,
                      "seconds": round(time.process_time() - t0, 2)}
        except Exception as e:                    # one bad file never loses the others
            errors[p] = f"{type(e).__name__}: {e}"[:300]
    return {"tracker": "madmom_online" if online else "madmom", "nets": nets if online else 8, "files": out,
            "errors": errors}


def run_dsphits(paths: list[str]) -> dict:
    """show_service.dsp.Features on each file (48 kHz mono), 128-frame blocks, sample n at n / 48000 s: every
    music.hit as {band: [time_s, ...]} (dsp's t_ms is the crossing sample's acoustic time)."""
    sys.path.insert(0, str(REPO))
    from show_service import contract, dsp
    out, errors = {}, {}
    for p in paths:
        t0 = time.process_time()
        hits: dict = {}

        def pub(msg, hits=hits):
            if msg.get("type") == contract.MUSIC_HIT:
                d = msg["data"]
                hits.setdefault(str(d["band"]), []).append(round(float(d["t_ms"]) / 1000.0, 6))
        try:
            x = np.load(p).astype(np.float32)
            x = np.round(np.clip(x, -1.0, 32767 / 32768) * 32768.0) / 32768.0     # the VBAN wire's INT16 steps
            f = dsp.Features(publish=pub)
            for n0 in range(0, len(x), DSP_BLOCK):
                blk = x[n0:n0 + DSP_BLOCK].reshape(-1, 1)
                f.process("cal", n0, n0 / DSP_FS, blk, DSP_FS, contract.AUDIO_SEGMENT if n0 == 0 else 0)
            out[p] = {"hits": hits, "seconds": round(time.process_time() - t0, 2)}
        except Exception as e:
            errors[p] = f"{type(e).__name__}: {e}"[:300]
    return {"tracker": "dsphits", "files": out, "errors": errors}


def _split_starts(n: int) -> np.ndarray:
    """Upstream beat_this.inference.split_piece: chunk starts (may be negative), the last moved to the end."""
    step = CHUNK - 2 * BORDER
    starts = np.arange(-BORDER, n - BORDER, step)
    if n > step:
        starts[-1] = n - (CHUNK - BORDER)
    return starts


def beatthis_logits(sess, in_name: str, out_names: list[str], spect: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Whole-track logits: each chunk zero-padded as upstream, its borders cut, earlier chunks overwrite later ones."""
    n = len(spect)
    beat = np.full(n, -1000.0, dtype=np.float32)
    down = np.full(n, -1000.0, dtype=np.float32)
    for s in reversed(_split_starts(n).tolist()):
        a, b = max(s, 0), min(s + CHUNK, n)
        left, right = max(0, -s), max(0, min(BORDER, s + CHUNK - n))
        seg = np.pad(spect[a:b], ((left, right), (0, 0)))
        ob, od = sess.run(out_names, {in_name: seg[None].astype(np.float32)})
        ob, od = np.asarray(ob)[0][BORDER:-BORDER], np.asarray(od)[0][BORDER:-BORDER]
        lo = s + BORDER
        m = max(0, min(len(ob), n - lo))
        beat[lo:lo + m] = ob[:m]
        down[lo:lo + m] = od[:m]
    return beat, down


def run_beatthis(paths: list[str], models_dir: pathlib.Path, model: str) -> dict:
    sys.path.insert(0, str(REPO))
    import onnxruntime as ort
    from show_service.ml.models import beat_logmel, minimal_peaks
    index = json.loads((models_dir / "index.json").read_text(encoding="utf-8"))
    entry = index[model]
    facts = entry.get("facts", {})
    inp = facts.get("input", {})
    fs = int(inp.get("fs", BEATTHIS_FS))
    if fs != BEATTHIS_FS:
        raise SystemExit(f"{model} wants {fs} Hz; analyse.py feeds {BEATTHIS_FS}")
    mel_kw = dict(fs=fs, n_fft=int(inp.get("n_fft", 1024)), hop=int(inp.get("hop", 441)),
                  f_min=float(inp.get("f_min", 30.0)), f_max=float(inp.get("f_max", 11000.0)),
                  n_mels=int(inp.get("n_mels", 128)), log_multiplier=float(inp.get("log_multiplier", 1000.0)))
    fps = float(inp.get("fps", fs / mel_kw["hop"]))
    o = ort.SessionOptions()
    o.intra_op_num_threads = 1
    o.inter_op_num_threads = 1
    o.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    o.log_severity_level = 3
    o.enable_cpu_mem_arena = False                # as the worker: don't keep the peak
    sess = ort.InferenceSession(str(models_dir / entry["file"]), o, providers=["CPUExecutionProvider"])
    in_name = inp.get("name") or sess.get_inputs()[0].name
    names = [x.name for x in sess.get_outputs()]
    outs = facts.get("outputs", {})
    out_names = [outs.get("beat", names[0]), outs.get("downbeat", names[-1])]
    out, errors = {}, {}
    for p in paths:
        t0 = time.process_time()
        try:
            x = np.load(p).astype(np.float32)
            spect = beat_logmel(x, **mel_kw)
            beat, down = beatthis_logits(sess, in_name, out_names, spect)
            beats, downs = minimal_peaks(beat, down, fps)
            act_path = str(pathlib.Path(p).with_suffix("")) + ".beatthis_logits.npy"
            np.save(act_path, np.stack([beat, down]).astype(np.float32))
            out[p] = {"beats": [float(b) for b in beats], "downbeats": [float(d) for d in downs], "act": act_path,
                      "fps": fps, "seconds": round(time.process_time() - t0, 2)}
        except Exception as e:
            errors[p] = f"{type(e).__name__}: {e}"[:300]
    return {"tracker": "beatthis", "model": model, "files": out, "errors": errors}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tracker", choices=("madmom", "madmom_online", "beatthis", "dsphits"))
    ap.add_argument("--nets", type=int, default=2, help="madmom_online: how many causal nets (the live clock: 2)")
    ap.add_argument("inputs", nargs="+", help="float32 mono .npy files at the tracker's rate")
    ap.add_argument("--out", required=True)
    ap.add_argument("--models-dir", default=os.path.expanduser("~/.cache/screen-machine/models"))
    ap.add_argument("--model", default="beat-this-small0")
    a = ap.parse_args()
    if a.tracker == "madmom":
        res = run_madmom(a.inputs)
    elif a.tracker == "madmom_online":
        res = run_madmom(a.inputs, online=True, nets=a.nets)
    elif a.tracker == "dsphits":
        res = run_dsphits(a.inputs)
    else:
        res = run_beatthis(a.inputs, pathlib.Path(os.path.expanduser(a.models_dir)), a.model)
    pathlib.Path(a.out).write_text(json.dumps(res), encoding="utf-8")


if __name__ == "__main__":
    main()
