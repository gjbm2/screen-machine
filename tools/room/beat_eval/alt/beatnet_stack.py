#!/usr/bin/env python3
"""Candidate beat stack: BeatNet (Heydari, Cwitkowitz & Duan, ISMIR 2021) in its online mode, as our published clock.

BeatNet is a causal CRNN (a log-filtered spectrogram and its positive difference, 272 features at 50 fps; a 1-D conv,
a linear layer, a 2-layer unidirectional LSTM; softmax beat / downbeat / no beat) followed by a particle filter on
madmom's bar-pointer state space. Its pip package (1.1.3) ships three trained CRNNs (``--model 1``: GTZAN held out,
so it has seen Ballroom; 2: Ballroom held out; 3: Rock Corpus held out). This replays it on the room's audio exactly
as run_stack.py replays our clock (alt/lineclock.py: the same decode, room model draw, pre-roll, INT16 wire, block
arrival times, dsp's loudness gate and quiet rule) and turns it into our published line, with a confidence for the
beat gate and the clock's hysteresis (lineclock.LineClock). What we found (dev subset, 18-19 Oct): the stock CRNN
does not survive this room (its activation tells beats from off-beats barely better than chance in condition C), a
CRNN fine-tuned on music through the room model does (alt/beatnet_train.py; ``--weights``), and the best line on it
is madmom's online DBN as an exact forward filter plus our own alpha-beta line on the activation peaks.

  ~/.venvs/sm-beat-beatnet/bin/python tools/room/beat_eval/alt/beatnet_stack.py AUDIO [run_stack.py's options]
      [--preset room] [any option below] --out OUT.json

or for a subset under the baseline's conditions, alt/beatnet_batch.py (``run`` per track through run_batch.py, or
``sweep``: variants in one process on the cached stage 1). Heavy: under flock ~/.cache/screen-machine/cpu.lock
nice -n 19 (run_batch.py does that per track). ``--preset room`` is the variant to measure (PRESETS).

The lane, causal as live would run it
-------------------------------------
- The received 48 kHz blocks (holes zero-filled) -> Clock's own AGC (aim -12 dBFS RMS, up 10 s / down 3 s, -20..+36
  dB, frozen under -65 dBFS, stepped per 128 frames on n: BeatNet, like madmom, was trained on mastered audio and
  its log(1 + |X|) features are level-dependent) -> soxr MQ stream to 22.05 kHz (delay-compensated, 14 ms; its
  output counted per block, so each sample is "heard" when the block that completes it arrives).
- BeatNet's features (madmom framing, 1411-sample windows centred on every 441st sample: frame i is the sound at
  i x 20 ms and needs 32 ms after it) and the CRNN. Offline they run over the whole stream at once, which gives the
  same numbers as frame by frame (the conv is per frame, the LSTM causal, the spectral difference looks back only);
  each frame is used only from the host time its last sample arrived (``frame_avail``), never earlier.
  ``--act-chunk N`` models a live port that runs the CRNN every N frames (a frame waits for its chunk).
  ``LiveLane`` is that port in plain numpy, frame by frame (no torch or madmom at run time), checked against the
  batch path (3e-6 on the activations).
- Stage 1 is cached per track and input (``~/.cache/screen-machine/beat-eval/beatnet-cache``): ``in-<key>`` holds
  the stream's model-independent part (dsp's gate, the lane, the features; the stream and gate come from
  alt/clockvar_replay.py's cache when it recorded the same run_stack argv: bit-identical, see lineclock), and
  ``<key>`` the CRNN's activations (per model / weights file): variants of the filter and the line replay in seconds.

The activation: ``--model``, ``--ensemble 123`` (the mean of models), ``--weights`` (a fine-tuned CRNN), ``--act``
max or sum of the beat and downbeat outputs, ``--act-norm`` (the activation over its own causal mean).

The filter (``--infer``)
------------------------
- ``pf`` (``PF``): BeatNet's beat particle filter, vectorised (numpy, same model): motion along the bar pointer,
  madmom's tempo transition at each beat's last state (``--lambda-b`` 60), the information gate (activations under
  ``--ig`` 0.4 read 0.03 and correct nothing), observation = activation on each interval's first (beat) state and
  0.03 elsewhere, ~7 fresh beat-state particles injected on an activation over 0.8, stratified resampling. Two fixes
  to the package: the particle count stays at ``--particles`` (the package appends the injected particles and never
  removes them: its ``np.delete`` result is discarded), and its RNG is seeded per track. BeatNet's own beat
  decisions are kept as ``beatnet_beats`` for reference: reactive (a beat at its own frame, 32 ms + the rx latency
  after it sounded), so the lights could not use them as they are.
- ``hmm`` (``HMM``): madmom's online beat DBN model (DBNBeatTrackingProcessor(online=True): BeatStateSpace,
  BeatTransitionModel ``--lambda-t`` 100, RNNBeatTrackingObservationModel ``--obs-lambda`` 16) as an exact forward
  filter keeping the whole posterior; ``--hmm-fps 100`` steps twice per BeatNet frame (2 % tempo steps).
- Either way, after every frame: the dominant tempo cluster (intervals' histogram smoothed over +-1, its peak
  +-``--cluster-tol`` 4 %), its posterior mass or particle share (``share``), the circular mean of its phases (``R``
  its length) and mean interval: an estimator beat time and period.

The line (``--refine``) and the octave check (``--halve``)
-------------------------------------------------------
- Without ``--refine`` the published estimator is the filter's. The forward filter's tempo wanders between adjacent
  integer intervals (on the real recording 122-129 bpm for a steady 125), so ``--refine`` publishes our own
  alpha-beta line instead (``--refine-alpha`` 0.2, Clock's critically damped beta), fed by each hit's sub-frame
  (parabolic) activation peak, started from the filter's and reset to it when they disagree (phase by more than
  ``--refine-reset``, tempo by more than ``--refine-tempo-tol``): at once (200 ms) while our line misses (<= 2 of
  its last 4 beats hit), else only after ``--refine-hold-s`` of disagreement.
- ``--halve``: through the room the fine-tuned activation often fires on off-beats too, and the filter locks at
  double tempo (a reggae at 79 bpm tracked at 159: every other flash off the beat, precision 0.5). Over the last 16
  judged beats, if the peaks at our line's even and odd beats differ by ``--halve-margin`` (``--halve-fast-margin``
  above ``--halve-fast-bpm``), with the line at ``--halve-min-bpm`` or more, the line halves onto the stronger
  parity; a filter at exactly double our halved line then counts as agreeing. Half tempo still flashes on the beat.

Judging, conf, state, gate
--------------------------
- Each predicted beat (the line's next beat, as predicted from the frame before it) is judged once the frames up
  to ``--hit-win-s`` (0.07) after it are in: a hit if the activation's peak in +-0.07 s is >= ``--hit-thr`` (0.4)
  (``--hit-mode contrast``: and above the off-beat window's peak by ``--hit-margin``); its residual is the peak's
  time minus the prediction.
- conf, "calibrated-ish" as Clock's: P(beat present) x P(|error| < ``--conf-tol-ms``) x clarity [x steadiness]:
  the hit rate over the last 8 predictions; the empirical and Gaussian share of the last 8 hits' residuals within
  the tolerance, blended, shrunk toward 0.35 while there are few; clarity 0.3 + 0.7 x smoothstep(``--share-lo``,
  ``--share-hi``, share); ``--stab-sigma``: x exp(-(s/sigma)^2), s the std of log2(bpm) over the last 8 beats
  (rubato wanders). Then / ``--conf-scale`` (set on dev so the gate's precision stays >= 0.9), capped at 0.3 while
  searching.
- state: searching until 3 of the last 4 predictions hit; locked while >= 2 of 4 do; holding below that (a
  breakdown), locked again at 2 of 4. Quiet (dsp's gate, Clock's rule): gate none at once, locked -> holding, the
  filter coasts (motion only, no correction, no judging).
- After each judged beat the line re-anchors (lineclock: slewed while the gate is "beat", else a jump), and every
  100 ms of host time the gate is updated (beat 0.70/0.60, dmx 0.45/0.35, 2-bar dwell) and a body published (only
  changed ones kept, as run_stack does).

Output: run_stack.py's JSON (score.py scores it unchanged) + ``candidate`` (name, every setting), ``stack.id`` =
"bn" + a hash of this file, lineclock.py and the settings, ``cpu`` (process CPU ms per audio second per stage:
``lane`` = AGC + resampling, ``feat``, ``crnn`` (batch: a live port costs more, see beatnet_batch.py's notes),
``pf`` = the filter, ``line``; ``candidate_ms_per_s`` their sum; dsp and the input prep are not the candidate's:
live already runs them), ``trace`` (each judged beat's terms, for calibrating conf) and ``beatnet_beats``.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import pathlib
import sys
import time
from typing import Optional

T_START_UNIX = time.time()
for _k, _v in (("OMP_NUM_THREADS", "1"), ("OPENBLAS_NUM_THREADS", "1"), ("MKL_NUM_THREADS", "1")):
    os.environ.setdefault(_k, _v)
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import numpy as np  # noqa: E402

import lineclock as lcm  # noqa: E402
from lineclock import BLOCK, FS, T0  # noqa: E402

CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval/beatnet-cache").expanduser()
FS_BN = 22050
HOP_BN = 441                    # 20 ms
WIN_BN = 1411                   # 64 ms, centred
FPS = 50
STAGE1_VERSION = 2
AGC_TARGET_DB = -12.0
AGC_CHUNK = 128


# -- stage 1: the lane, the features, the CRNN -------------------------------------------------------------------------
class AGC:
    """Clock._agc and _lane_in (agc_chunks): one step per 128 frames aligned on n, each sample takes the gain of
    the chunks before its own."""

    def __init__(self, target_db: float = AGC_TARGET_DB) -> None:
        self.target = target_db
        self.db, self.pow, self.init = 0.0, 0.0, False
        self.acc_p = self.acc_t = 0.0
        self.k: tuple = (None, 0.0, 0.0, 0.0)
        self.g = np.float32(1.0)
        self.sp, self.sn = 0.0, 0

    def _step(self, p: float, dt: float) -> None:
        if not self.init:
            if 10.0 * math.log10(p + 1e-20) < -65.0:
                return
            self.acc_p += p * dt
            self.acc_t += dt
            if self.acc_t < 0.5:
                return
            self.pow = self.acc_p / self.acc_t
            self.db = min(36.0, max(-20.0, self.target - 10.0 * math.log10(self.pow + 1e-20)))
            self.init = True
            return
        k = self.k
        if k[0] != dt:
            k = self.k = (dt, 1.0 - math.exp(-dt / 0.4), 1.0 - math.exp(-dt / 10.0), 1.0 - math.exp(-dt / 3.0))
        self.pow += (p - self.pow) * k[1]
        lvl = 10.0 * math.log10(self.pow + 1e-20)
        if lvl < -65.0:
            return
        want = min(36.0, max(-20.0, self.target - lvl))
        err = want - self.db
        self.db += err * (k[2] if err > 0 else k[3])

    def block(self, x: np.ndarray, n0: int) -> np.ndarray:
        out = np.empty_like(x)
        pos, n, frames = 0, n0, len(x)
        while pos < frames:
            take = min(frames - pos, AGC_CHUNK - n % AGC_CHUNK)
            seg = x[pos:pos + take]
            np.multiply(seg, self.g, out=out[pos:pos + take])
            self.sp += float(np.dot(seg, seg))
            self.sn += take
            pos += take
            n += take
            if n % AGC_CHUNK == 0:
                self._step(self.sp / self.sn, self.sn / FS)
                self.sp, self.sn = 0.0, 0
                self.g = np.float32(10.0 ** (self.db / 20.0))
        return out


def lane(yr: np.ndarray, arrive: np.ndarray, quality: str) -> tuple[np.ndarray, np.ndarray, list]:
    """AGC -> soxr stream to 22.05 kHz; (the 22.05 kHz lane, each block's cumulative output count, the AGC gain
    at 2 Hz as [block arrival, dB])."""
    import soxr
    agc = AGC()
    rs = soxr.ResampleStream(FS, FS_BN, 1, dtype="float32", quality=quality)
    outs, counts, agc_rows = [], np.zeros(len(arrive), np.int64), []
    tot, next_row = 0, T0
    for j in range(len(arrive)):
        n0 = j * BLOCK
        g = agc.block(yr[n0:n0 + BLOCK], n0)
        o = rs.resample_chunk(g)
        if len(o):
            outs.append(o)
            tot += len(o)
        counts[j] = tot
        if arrive[j] >= next_row:
            agc_rows.append([float(arrive[j]), round(agc.db, 2)])
            next_row += 0.5
    z = np.concatenate(outs) if outs else np.zeros(0, np.float32)
    return z.astype(np.float32), counts, agc_rows


def frame_avail(counts: np.ndarray, arrive: np.ndarray, n_lane: int) -> np.ndarray:
    """Host time each BeatNet frame can be computed (its window's last sample has come out of the resampler)."""
    nf = max(0, (n_lane - (WIN_BN - WIN_BN // 2)) // HOP_BN + 1)
    need = np.arange(nf) * HOP_BN + (WIN_BN - WIN_BN // 2 - 1)       # the last sample of frame i's window
    j = np.searchsorted(counts, need + 1, side="left")                # first block after which it is out
    ok = j < len(arrive)
    return arrive[np.minimum(j[ok], len(arrive) - 1)]


def load_bnet(model: int, weights: Optional[str] = None):
    import torch
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass
    from BeatNet.model import BDA
    import BeatNet as _bn
    net = BDA(272, 150, 2, "cpu")
    w = pathlib.Path(weights).expanduser() if weights else \
        pathlib.Path(_bn.__file__).parent / "models" / f"model_{model}_weights.pt"
    net.load_state_dict(torch.load(str(w), map_location="cpu"), strict=False)
    net.eval()
    return net


def features(z: np.ndarray) -> np.ndarray:
    from BeatNet.log_spect import LOG_SPECT
    proc = LOG_SPECT(sample_rate=FS_BN, win_length=WIN_BN, hop_size=HOP_BN, n_bands=[24], mode="online")
    return np.ascontiguousarray(proc.process_audio(z).T, dtype=np.float32)       # (frames, 272)


def crnn(net, feats: np.ndarray, stream: bool = False) -> np.ndarray:
    """(frames, 2): beat and downbeat activations. ``stream``: one LSTM step per frame (the live cost)."""
    import torch
    with torch.no_grad():
        net.hidden = torch.zeros(2, 1, net.dim_hd)
        net.cell = torch.zeros(2, 1, net.dim_hd)
        if not stream:
            x = torch.from_numpy(feats).unsqueeze(0)
            p = net.final_pred(net(x)[0]).numpy()
            return np.ascontiguousarray(p[:2, :].T)
        out = np.zeros((len(feats), 2), np.float32)
        for i in range(len(feats)):
            x = torch.from_numpy(feats[i:i + 1]).unsqueeze(0)
            p = net.final_pred(net(x)[0]).numpy()
            out[i] = p[:2, 0]
        return out


class LiveLane:
    """BeatNet's features and CRNN one frame at a time in plain numpy (no torch, no madmom at run time): what a
    live port would run on show-rx or a child, and what ``--live-lane`` measures. The same arithmetic as the batch
    path (madmom's centred framing, Hann window, 1411-point FFT, its 24-per-octave log filterbank 30 Hz - 17 kHz,
    log10(1 + x), the positive difference to the frame ``diff_frames`` back; BDA's conv, max-pool, linear, 2-layer
    LSTM, linear, softmax), checked against it (``check``): float32 noise."""

    def __init__(self, net) -> None:
        from madmom.audio.filters import LogarithmicFilterbank
        from madmom.audio.spectrogram import _diff_frames
        from madmom.audio.stft import fft_frequencies
        self.win = np.hanning(WIN_BN).astype(np.float32)
        nb = WIN_BN >> 1
        self.fb = np.asarray(LogarithmicFilterbank(fft_frequencies(nb, FS_BN), num_bands=24, fmin=30, fmax=17000,
                                                   norm_filters=True), np.float32)
        self.nb = nb
        self.dd = _diff_frames(0.5, HOP_BN, WIN_BN, np.hanning)
        self.hist: collections.deque = collections.deque(maxlen=self.dd)
        sd = {k: v.detach().numpy().astype(np.float32) for k, v in net.state_dict().items()}
        self.cw, self.cb = sd["conv1.weight"][:, 0, :], sd["conv1.bias"]           # (2, 10), (2,)
        self.l0w, self.l0b = sd["linear0.weight"], sd["linear0.bias"]            # (150, 262)
        self.lw = [(sd[f"lstm.weight_ih_l{k}"], sd[f"lstm.weight_hh_l{k}"],
                    sd[f"lstm.bias_ih_l{k}"] + sd[f"lstm.bias_hh_l{k}"]) for k in (0, 1)]
        self.ow, self.ob = sd["linear.weight"], sd["linear.bias"]
        self.H = sd["lstm.weight_hh_l0"].shape[1]
        self.reset()

    def reset(self) -> None:
        self.h = [np.zeros(self.H, np.float32) for _ in range(2)]
        self.c = [np.zeros(self.H, np.float32) for _ in range(2)]
        self.hist.clear()

    def feature(self, frame: np.ndarray) -> np.ndarray:
        """One centred 1411-sample frame -> the 272 features."""
        spec = np.abs(np.fft.fft(frame * self.win)[:self.nb]).astype(np.float32)
        s = np.log10(spec @ self.fb + 1.0)
        d = np.maximum(s - self.hist[0], 0.0) if len(self.hist) == self.dd else np.zeros_like(s)   # madmom: 0 first
        self.hist.append(s)
        return np.concatenate([s, d])

    def net(self, x: np.ndarray) -> np.ndarray:
        """272 features -> (beat, downbeat) probabilities; the LSTM state carries on."""
        win = np.lib.stride_tricks.sliding_window_view(x, self.cw.shape[1])          # (263, 10)
        y = np.maximum(win @ self.cw.T + self.cb, 0.0)                                  # (263, 2)
        n = (y.shape[0] // 2) * 2
        y = y[:n].reshape(-1, 2, 2).max(axis=1).T.reshape(-1)                         # channel-major (262,)
        v = self.l0w @ y + self.l0b
        H = self.H
        for k, (wi, wh, b) in enumerate(self.lw):
            g = wi @ v + wh @ self.h[k] + b
            i, f, gg, o = g[:H], g[H:2 * H], g[2 * H:3 * H], g[3 * H:]
            i, f, o = 1 / (1 + np.exp(-i)), 1 / (1 + np.exp(-f)), 1 / (1 + np.exp(-o))
            self.c[k] = f * self.c[k] + i * np.tanh(gg)
            self.h[k] = o * np.tanh(self.c[k])
            v = self.h[k]
        z = self.ow @ v + self.ob
        e = np.exp(z - z.max())
        p = e / e.sum()
        return p[:2]

    def run(self, z: np.ndarray, nf: int) -> tuple[np.ndarray, np.ndarray]:
        """Frame by frame over the lane: (features, activations)."""
        pad = np.concatenate([np.zeros(WIN_BN // 2, np.float32), z, np.zeros(WIN_BN, np.float32)])
        F = np.zeros((nf, 272), np.float32)
        A = np.zeros((nf, 2), np.float32)
        for i in range(nf):
            fr = pad[i * HOP_BN:i * HOP_BN + WIN_BN]
            F[i] = self.feature(fr)
            A[i] = self.net(F[i])
        return F, A


def stage1(a, cache_on: bool) -> tuple[dict, dict]:
    """The candidate's inputs for one track and condition: cached (npz + json) unless ``--no-cache``. An
    ``--ensemble`` (e.g. 123) averages the single models' activations, each from its own cache entry."""
    if getattr(a, "ensemble", None):
        import copy
        acts, meta0, arrays0 = [], None, None
        cpu_crnn = 0.0
        for m in a.ensemble:
            b = copy.copy(a)
            b.ensemble, b.model = None, int(m)
            arr, meta = stage1(b, cache_on)
            acts.append(arr["act"])
            cpu_crnn += meta["cpu_s"].get("crnn", 0.0)
            if arrays0 is None:
                arrays0, meta0 = dict(arr), json.loads(json.dumps(meta))
        n = min(len(x) for x in acts)
        arrays0["act"] = np.mean([x[:n] for x in acts], axis=0).astype(np.float32)
        arrays0["frame_avail"] = arrays0["frame_avail"][:n]
        meta0["cpu_s"]["crnn"] = cpu_crnn
        return arrays0, meta0
    key_doc = {"v": STAGE1_VERSION, "input": lcm.input_key(a), "model": a.model, "resample": a.resample_quality,
               "crnn_stream": bool(a.crnn_stream)}
    if getattr(a, "weights", None):                       # a fine-tuned CRNN (alt/beatnet_train.py)
        key_doc["weights"] = lcm.file_sha(pathlib.Path(a.weights).expanduser())
    key = hashlib.sha256(json.dumps(key_doc, sort_keys=True).encode()).hexdigest()[:16]
    d = CACHE / _safe(a)
    npz, meta_p = d / f"{key}.npz", d / f"{key}.json"
    if cache_on and npz.exists() and meta_p.exists():
        z = np.load(npz)
        meta = json.loads(meta_p.read_text())
        meta["cache"] = "hit"
        return {k: z[k] for k in z.files}, meta
    cpu, wall = {}, {}

    def lap(name: str, c0: float, w0: float) -> tuple[float, float]:
        c1, w1 = time.process_time(), time.perf_counter()
        cpu[name] = cpu.get(name, 0.0) + (c1 - c0)
        wall[name] = wall.get(name, 0.0) + (w1 - w0)
        return c1, w1

    inp, imeta = _inputs(a, cache_on)                     # the stream, dsp's gate, the lane and the features
    cpu.update(imeta["cpu_s"])
    wall.update(imeta["wall_s"])
    c, w = time.process_time(), time.perf_counter()
    net = load_bnet(a.model, getattr(a, "weights", None))
    c, w = lap("load", c, w)
    act = crnn(net, inp["feats"], stream=bool(a.crnn_stream)).astype(np.float32)
    c, w = lap("crnn", c, w)
    arrays = {k: v for k, v in inp.items() if k != "feats"}
    arrays["act"] = act
    meta = {k: v for k, v in imeta.items() if k not in ("cpu_s", "wall_s", "key", "key_doc", "cache")}
    meta.update({"key": key, "key_doc": key_doc, "input_key": imeta["key"], "cpu_s": cpu, "wall_s": wall,
                 "cache": "miss", "inputs_cache": imeta.get("cache")})
    if cache_on:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp.npz"
        np.savez(tmp, **arrays)
        tmp.replace(npz)
        meta_p.write_text(json.dumps(meta, default=float))
    return arrays, meta


def _inputs(a, cache_on: bool) -> tuple[dict, dict]:
    """Stage 1's model-independent half (the stream, dsp's gate, the lane, BeatNet's features), cached on its own
    (``in-<key>.npz``) so another CRNN (a fine-tune, another model) reruns only the network."""
    key_doc = {"v": STAGE1_VERSION, "what": "inputs", "input": lcm.input_key(a), "resample": a.resample_quality}
    key = hashlib.sha256(json.dumps(key_doc, sort_keys=True).encode()).hexdigest()[:16]
    d = CACHE / _safe(a)
    npz, meta_p = d / f"in-{key}.npz", d / f"in-{key}.json"
    if cache_on and npz.exists() and meta_p.exists():
        z = np.load(npz)
        meta = json.loads(meta_p.read_text())
        meta["cache"] = "hit"
        return {k: z[k] for k in z.files}, meta
    cpu, wall = {}, {}

    def lap(name: str, c0: float, w0: float) -> tuple[float, float]:
        c1, w1 = time.process_time(), time.perf_counter()
        cpu[name] = cpu.get(name, 0.0) + (c1 - c0)
        wall[name] = wall.get(name, 0.0) + (w1 - w0)
        return c1, w1

    c, w = time.process_time(), time.perf_counter()
    cv = lcm.from_clockvar_cache(list(a.argv), a) if getattr(a, "argv", None) and not a.no_reuse else None
    if cv is not None:                                    # the same stream and gate, recorded by alt/clockvar_replay
        prep, gates, frows = cv
        yr, arrive, flags = lcm.received(prep["y"], prep["lost"], a.rx_latency_ms / 1000.0)
        tr = {"gate": gates, "rows": frows, "stats": {"from": prep["from"]}}
        c, w = lap("prep", c, w)
    else:
        prep = lcm.prepare(a)
        yr, arrive, flags = lcm.received(prep["y"], prep["lost"], a.rx_latency_ms / 1000.0)
        c, w = lap("prep", c, w)
        tr = lcm.dsp_trace(yr, arrive, flags, a.gate_ref_lufs)
        c, w = lap("dsp", c, w)
    z, counts, agc_rows = lane(yr, arrive, a.resample_quality)
    c, w = lap("lane", c, w)
    fa = frame_avail(counts, arrive, len(z))
    ft = features(z)[:len(fa)]
    c, w = lap("feat", c, w)
    arrays = {"feats": ft.astype(np.float32), "frame_avail": fa.astype(np.float64), "arrive": arrive.astype(np.float64),
              "gate": tr["gate"].astype(np.float32),
              "feat_rows": np.asarray([[r[0], np.nan if r[1] is None else r[1], np.nan if r[2] is None else r[2],
                                        np.nan if r[3] is None else r[3]] for r in tr["rows"]], np.float64).reshape(-1, 4),
              "agc_rows": np.asarray(agc_rows, np.float64).reshape(-1, 2)}
    audio_s = len(prep["y"]) / FS
    meta = {"key": key, "key_doc": key_doc, "track_s": prep["track_s"], "preroll_s": prep["preroll_s"],
            "zero": prep["zero"], "degrade": prep["degrade"], "n48": len(prep["y"]),
            "lost": int(prep["lost"].sum()) if prep["lost"] is not None else 0, "audio_s": audio_s,
            "cpu_s": cpu, "wall_s": wall, "features_stats": tr["stats"], "cache": "miss"}
    if cache_on:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"in-{key}.tmp.npz"
        np.savez(tmp, **arrays)
        tmp.replace(npz)
        meta_p.write_text(json.dumps(meta, default=float))
    return arrays, meta


def _safe(a) -> str:
    s = f"{a.dataset}__{a.id or a.audio.stem}"
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in s)


# -- stage 2: the particle filter ---------------------------------------------------------------------------------------
class PF:
    """BeatNet's beat particle filter (particle_filtering_cascade's beat half), vectorised; the module docstring."""

    def __init__(self, n: int = 1500, min_bpm: float = 55.0, max_bpm: float = 215.0, num_tempi: int = 300,
                 lambda_b: float = 60.0, ig: float = 0.4, floor: float = 0.03, inject: bool = True, seed: int = 0):
        from madmom.features.beats_hmm import BarStateSpace, BarTransitionModel
        from madmom.ml.hmm import TransitionModel
        st = BarStateSpace(1, 60.0 * FPS / max_bpm, 60.0 * FPS / min_bpm, num_tempi)
        tm = BarTransitionModel(st, lambda_b)
        to, frm, prob = TransitionModel.make_dense(tm.states, tm.pointers, tm.probabilities)
        self.n, self.ig, self.floor, self.inject = n, ig, floor, inject
        self.ns = st.num_states
        self.pos = np.asarray(st.state_positions, float)
        self.iv = np.asarray(st.state_intervals, np.int64)
        self.first = np.asarray(st.first_states[0], np.int64)
        last = np.concatenate([np.atleast_1d(x) for x in st.last_states]).astype(np.int64)
        self.is_first = np.zeros(self.ns, bool)
        self.is_first[self.first] = True
        self.is_last = np.zeros(self.ns, bool)
        self.is_last[last] = True
        self.last_idx = np.full(self.ns, -1, np.int64)
        self.last_idx[last] = np.arange(len(last))
        fpos = {int(s): k for k, s in enumerate(self.first)}
        P = np.zeros((len(last), len(self.first)))
        for t_, f_, p_ in zip(to, frm, prob):
            li = self.last_idx[int(f_)]
            if li >= 0 and int(t_) in fpos:
                P[li, fpos[int(t_)]] += p_
        P /= P.sum(axis=1, keepdims=True)
        self.cdf = np.cumsum(P, axis=1)
        self.cdf[:, -1] = 1.0
        self.first_of_iv = np.zeros(self.iv.max() + 2, np.int64)
        self.first_of_iv[self.iv[self.first]] = self.first
        self.rng = np.random.default_rng(seed)
        self.p = np.sort(self.rng.choice(np.arange(0, self.ns - 1), n, replace=True))
        self.last_beat_t = 0.0
        self.counts = collections.Counter()

    def reset(self) -> None:
        self.p = np.sort(self.rng.choice(np.arange(0, self.ns - 1), self.n, replace=True))

    def stock_beat(self, a_ig: float, t: float) -> bool:
        """BeatNet's own beat rule (before the frame's motion): the median particle within 4 states of its
        interval's beat state, 0.4 x its interval since the last beat, and the activation over 0.4."""
        g = int(np.median(self.p))
        I = int(self.iv[g])
        if g - int(self.first_of_iv[I]) < int(0.07 * FPS) + 1 and t - self.last_beat_t > 0.4 * I / FPS \
                and a_ig > 0.4:
            self.last_beat_t = t
            return True
        return False

    def step(self, a: float, correct: bool = True) -> None:
        p = self.p
        m = self.is_last[p]
        q = p + 1
        if m.any():
            li = self.last_idx[p[m]]
            u = self.rng.random(len(li))
            k = (self.cdf[li] < u[:, None]).sum(axis=1)
            q[m] = self.first[np.minimum(k, len(self.first) - 1)]
        a_ig = a if a >= self.ig else self.floor
        if correct and a_ig > 0.1:
            if a_ig > 0.8 and self.inject:
                q = np.concatenate([q, self.first[np.arange(self.rng.integers(4), len(self.first), 6)]])
                self.counts["injections"] += 1
            w = np.where(self.is_first[q], a_ig, self.floor)
            c = np.cumsum(w)
            c /= c[-1]
            U = (self.rng.random(self.n) + np.arange(self.n)) / self.n
            q = q[np.minimum(np.searchsorted(c, U), len(q) - 1)]
            self.counts["corrections"] += 1
        self.p = q

    def estimate(self, tol: float) -> tuple[float, float, float, float]:
        """(period frames, share of the dominant tempo cluster, R, mean phase in beats) of the cloud now."""
        iv = self.iv[self.p]
        cnt = np.bincount(iv, minlength=self.iv.max() + 2).astype(float)
        sm = cnt.copy()
        sm[1:] += cnt[:-1]
        sm[:-1] += cnt[1:]
        I0 = int(np.argmax(sm))
        c = np.abs(iv - I0) <= max(1.0, tol * I0)
        ph = self.pos[self.p[c]]
        zc = np.exp(2j * np.pi * ph).mean()
        phi = (np.angle(zc) / (2 * np.pi)) % 1.0
        return float(iv[c].mean()), float(c.mean()), float(abs(zc)), float(phi)


class HMM:
    """The exact alternative to the particle filter: madmom's online beat DBN (Böck, Krebs & Widmer 2014;
    DBNBeatTrackingProcessor(online=True)'s model), run as a forward filter on BeatNet's activation, keeping the
    whole posterior. State space: madmom's BeatStateSpace (every integer interval at ``fps`` between the bpm limits,
    a position along each), tempo changes only at beat boundaries (BeatTransitionModel, ``lambda_t``), observation
    madmom's RNNBeatTrackingObservationModel (the first 1/``obs_lambda`` of each beat sees the activation, the rest
    (1 - a) / (lambda - 1)). ``fps`` 100 runs two steps per BeatNet frame (the half-frame step on the mean of this
    frame and the one before: nothing from the future) for 2 % tempo steps instead of 4 %."""

    def __init__(self, min_bpm: float = 55.0, max_bpm: float = 215.0, lambda_t: float = 100.0,
                 obs_lambda: float = 16.0, fps: int = 50, eps: float = 0.0, **_):
        import scipy.sparse as sp
        from madmom.features.beats_hmm import BeatStateSpace, BeatTransitionModel
        st = BeatStateSpace(60.0 * fps / max_bpm, 60.0 * fps / min_bpm)
        tm = BeatTransitionModel(st, lambda_t)
        S = st.num_states
        self.M = sp.csr_matrix((tm.probabilities, tm.states, tm.pointers), shape=(S, S))
        self.fps, self.ratio = fps, max(1, int(round(fps / FPS)))
        self.eps = float(eps)
        self.pos = np.asarray(st.state_positions, float)
        self.iv = np.asarray(st.state_intervals, np.int64)
        self.beat_state = self.pos < 1.0 / obs_lambda
        self.lam = obs_lambda
        self.f = np.full(S, 1.0 / S)
        self.ph = np.exp(2j * np.pi * self.pos)
        self.nb = self.iv.max() + 2
        self.counts = collections.Counter()
        self._prev_a = 0.0

    def reset(self) -> None:
        self.f[:] = 1.0 / len(self.f)

    def _one(self, a: Optional[float]) -> None:
        f = self.M @ self.f
        if a is not None:
            a = min(max(a, 1e-6), 1.0 - 1e-6)
            f *= np.where(self.beat_state, a, (1.0 - a) / (self.lam - 1.0))
        s = f.sum()
        self.f = f / s if s > 0 else np.full(len(f), 1.0 / len(f))
        if self.eps:
            self.f = (1.0 - self.eps) * self.f + self.eps / len(f)

    def step(self, a: float, correct: bool = True) -> None:
        if self.ratio == 2:
            self._one(0.5 * (a + self._prev_a) if correct else None)
        self._one(a if correct else None)
        self._prev_a = a
        self.counts["steps"] += 1

    def estimate(self, tol: float) -> tuple[float, float, float, float]:
        """(period in BeatNet frames, the dominant tempo cluster's posterior mass, R, its mean phase)."""
        pI = np.bincount(self.iv, weights=self.f, minlength=self.nb)
        sm = pI.copy()
        sm[1:] += pI[:-1]
        sm[:-1] += pI[1:]
        I0 = int(np.argmax(sm))
        c = np.abs(self.iv - I0) <= max(1.0, tol * I0)
        w = self.f[c]
        m = w.sum()
        if m <= 0:
            return I0 * FPS / self.fps, 0.0, 0.0, 0.0
        zc = (w * self.ph[c]).sum() / m
        phi = (np.angle(zc) / (2 * np.pi)) % 1.0
        P = float((w * self.iv[c]).sum() / m) * FPS / self.fps
        return P, float(m), float(abs(zc)), float(phi)

    def stock_beat(self, a_ig: float, t: float) -> bool:
        return False


# -- stage 2: the estimator, conf and the line ---------------------------------------------------------------------------
def smoothstep(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def run_filter(a, arrays: dict, meta: dict) -> dict:
    act = arrays["act"]
    fa = arrays["frame_avail"]
    if a.act_chunk > 1:                                   # the CRNN run every N frames live: a frame waits for its chunk
        idx = np.minimum(len(fa) - 1, (np.arange(len(fa)) // a.act_chunk + 1) * a.act_chunk - 1)
        fa = fa[idx]
    arrive = arrays["arrive"]
    gate = arrays["gate"]
    nf = len(fa)
    amax = np.max(act, axis=1)
    if a.infer == "hmm":
        pf = HMM(min_bpm=a.min_bpm, max_bpm=a.max_bpm, lambda_t=a.lambda_t, obs_lambda=a.obs_lambda, fps=a.hmm_fps,
                 eps=a.hmm_eps)
        pf.floor = 0.0
        amax = np.minimum(1.0, act[:, 0] + act[:, 1]) if a.act == "sum" else amax
    else:
        pf = PF(n=a.particles, min_bpm=a.min_bpm, max_bpm=a.max_bpm, lambda_b=a.lambda_b, ig=a.ig,
                inject=not a.no_inject, seed=a.seed)
    if a.act_norm > 0:                                    # contrast: the activation over its own causal mean
        W = max(1, int(round(a.act_norm * FPS)))
        cs = np.concatenate([[0.0], np.cumsum(amax, dtype=np.float64)])
        i = np.arange(len(amax))
        m = (cs[i + 1] - cs[np.maximum(0, i + 1 - W)]) / np.minimum(W, i + 1)
        m = np.minimum(0.95, a.act_norm_k * m)
        amax = np.clip((amax - m) / (1.0 - m), 0.0, 1.0).astype(np.float32)
    lc = lcm.LineClock(bpb=4, all_bodies=a.all_bodies)
    quiet = lcm.Quiet()
    hw = int(round(a.hit_win_s * FPS))
    hits: collections.deque = collections.deque(maxlen=8)
    resid: collections.deque = collections.deque(maxlen=8)
    bpms: collections.deque = collections.deque(maxlen=8)
    par: collections.deque = collections.deque(maxlen=16)        # (beat index, peak, time) for the octave check
    par_n: list = []
    pending: list[float] = []
    next_pred: Optional[float] = None
    last_reg = -math.inf
    trace: list[list] = []
    bn_beats: list[list] = []
    confirmed = False
    stats = collections.Counter()
    share = R = 0.0
    P_fr = 25.0
    hb_i, next_hb = 0, T0
    fi = 0
    cpu_pf = cpu_line = 0.0
    tol_s = a.conf_tol_ms / 1000.0
    last_I = None
    ab_t: Optional[float] = None                          # --refine: our own alpha-beta line on the activation peaks
    ab_P = 0.5
    dis_n = 0
    ab_k = [0]                                            # hits since our line was (re)started (--refine-ls)
    beta = a.refine_alpha ** 2 / (2.0 - a.refine_alpha)

    def judge(tp: float, i_now: int) -> None:
        nonlocal confirmed, ab_t, ab_P
        c0 = int(round((tp - T0) * FPS))
        lo, hi = max(0, c0 - hw), min(i_now, c0 + hw)
        if hi < lo:
            return
        seg = amax[lo:hi + 1]
        k = int(np.argmax(seg))
        v = float(seg[k])
        hit = v >= a.hit_thr
        if a.hit_mode == "contrast":                      # and above the off-beat's own peak: clutter hits both
            cm = int(round((tp - 0.5 * lc.est_P - T0) * FPS))
            mseg = amax[max(0, cm - hw):max(0, min(i_now, cm + hw) + 1)]
            vm = float(mseg.max()) if len(mseg) else 0.0
            hit = hit and v >= vm + a.hit_margin
        r = math.nan
        if hit:
            kk = lo + k
            dt = 0.0
            if 0 < kk < nf - 1 and kk + 1 <= i_now:
                y0, y1, y2 = amax[kk - 1], amax[kk], amax[kk + 1]
                den = y0 - 2 * y1 + y2
                if den < 0:
                    dt = 0.5 * (y0 - y2) / den
            r = (T0 + (kk + dt) / FPS) - tp
            resid.append(r)
            if a.refine and ab_t is not None:
                al, be = a.refine_alpha, beta
                if a.refine_ls:                           # fading-memory gains: a least-squares fit at first
                    k = ab_k[0] + 3
                    al, be = max(al, 2.0 * (2 * k - 1) / (k * (k + 1))), max(be, 6.0 / (k * (k + 1)))
                    ab_k[0] += 1
                ab_t = tp + al * r
                ab_P = min(ab_P * 1.04, max(ab_P / 1.04, ab_P + be * r))
        hits.append(hit)
        if a.halve and a.refine and ab_t is not None:     # the octave check: alternate beats of our line unequal
            par.append((len(par_n), v, tp))
            par_n.append(1)
            if len(par) >= 16 and 60.0 / ab_P >= a.halve_min_bpm:
                ev_ = [x[1] for x in par if x[0] % 2 == 0]
                od_ = [x[1] for x in par if x[0] % 2 == 1]
                me, mo = float(np.mean(ev_)), float(np.mean(od_))
                margin = a.halve_fast_margin if 60.0 / ab_P >= a.halve_fast_bpm else a.halve_margin
                if abs(me - mo) >= margin:                # one parity is the beat, the other the off-beat: halve
                    strong = 0 if me > mo else 1
                    t_strong = max(x[2] for x in par if x[0] % 2 == strong)
                    ab_t, ab_P = t_strong, 2.0 * ab_P
                    par.clear()
                    par_n.clear()
                    stats["halvings"] += 1
        h4 = sum(list(hits)[-4:])
        if lc.state == "searching":
            if len(hits) >= 4 and h4 >= 3:
                lc.state, confirmed = "locked", True
                stats["seeds"] += 1
        elif lc.state == "locked":
            if h4 < 2:
                lc.state = "holding"
        elif lc.state == "holding" and h4 >= 2:
            lc.state = "locked"
        k0 = a.shrink_k
        h8 = sum(hits) / 8.0 if k0 <= 0 else (sum(hits) + 0.5 * k0) / (len(hits) + k0)
        n = len(resid)
        if n:
            rr = np.asarray(resid)
            emp = float(np.mean(np.abs(rr) <= tol_s))
            rms = max(0.003, float(np.sqrt(np.mean(rr * rr))))
            gau = math.erf(tol_s / (rms * math.sqrt(2.0)))
            p_t = 0.5 * (emp + gau)
            p_t = (n * p_t + (8 - n) * 0.35) / 8.0 if k0 <= 0 else (n * p_t + k0 * 0.35) / (n + k0)
        else:
            p_t = 0.35
        clar = 0.3 + 0.7 * smoothstep(a.share_lo, a.share_hi, share)
        bpms.append(60.0 / lc.est_P)
        lb = np.log2(np.asarray(bpms))
        stab = float(np.std(lb)) if len(lb) >= 3 else 0.05      # tempo wander over the last 8 beats (octaves)
        steady = math.exp(-(stab / a.stab_sigma) ** 2) if a.stab_sigma > 0 else 1.0
        conf = h8 * p_t * clar * steady * (1.0 if not a.r_power else R ** a.r_power)
        conf = min(1.0, conf / a.conf_scale)              # calibrated on dev: the gate's 0.70 at raw 0.70 x scale
        if lc.state == "searching":
            conf = min(conf, 0.3)
        lc.conf = conf
        trace.append([round(tp - meta["zero"], 4), int(hit), round(v, 3), None if math.isnan(r) else round(r * 1000, 1),
                      round(h8, 3), round(p_t, 3), round(clar, 3), round(share, 3), round(R, 3),
                      round(60.0 * FPS / P_fr, 2), round(conf, 3), lc.state, round(stab, 4), round(steady, 3)])

    for j in range(len(arrive)):
        h = float(arrive[j])
        while next_hb <= h:                               # 10 Hz: the gate and a heartbeat body
            lc.update_gate(next_hb, quiet.quiet)
            lc.publish(next_hb)
            hb_i += 1
            next_hb = T0 + hb_i * 0.1
        ev = quiet.level(float(gate[j]), T0 + j * BLOCK / FS)
        if ev == "start":
            if lc.state == "locked":
                lc.state = "holding"
            lc.gate, lc.gate_t, lc.gate_up = "none", h, False
            pending.clear()
            lc.publish(h)
        elif ev == "end" and a.reset_on_quiet_end:
            pf.reset()
        while fi < nf and fa[fi] <= h:
            c0 = time.process_time()
            t_i = T0 + fi / FPS
            av = float(amax[fi])
            if pf.stock_beat(av if av >= a.ig else pf.floor, t_i):
                bn_beats.append([round(t_i - meta["zero"], 4), round(h - meta["zero"], 4)])
            pf.step(av, correct=not quiet.quiet)
            P_fr, share, R, phi = pf.estimate(a.cluster_tol)
            c1 = time.process_time()
            cpu_pf += c1 - c0
            P_s = P_fr / FPS
            t_b = t_i - phi * P_s
            if last_I is not None and abs(P_fr / last_I - 1.0) > 0.06:
                r_ = P_fr / last_I
                if abs(r_ - 2.0) < 0.15 or abs(r_ - 0.5) < 0.08:
                    stats["octave_changes"] += 1
                else:
                    stats["tempo_jumps"] += 1
            last_I = P_fr
            if next_pred is not None and t_i >= next_pred - 1e-9 and not quiet.quiet:
                if next_pred - last_reg >= 0.5 * P_s:
                    pending.append(next_pred)
                    last_reg = next_pred
            if a.refine:
                if ab_t is None:
                    ab_t, ab_P = t_b, P_s
                    ab_k[0] = 0
                else:
                    dph = ((t_b - ab_t) / ab_P + 0.5) % 1.0 - 0.5
                    dis = abs(dph) > a.refine_reset or abs(P_s / ab_P - 1.0) > a.refine_tempo_tol
                    if dis and a.halve and stats["halvings"] and abs(2.0 * P_s / ab_P - 1.0) < 0.04:
                        dph2 = ((t_b - ab_t) / P_s + 0.5) % 1.0 - 0.5     # the filter at double ours, in phase
                        dis = abs(dph2) > a.refine_reset
                    dis_n = dis_n + 1 if dis else 0
                    weak = sum(list(hits)[-4:]) <= 2
                    # the filter disagrees: follow it at once (200 ms) while our own line misses, else only once it
                    # has disagreed for --refine-hold-s (a breakdown's guesses don't move a line that hits)
                    if (weak and dis_n >= 10) or dis_n >= a.refine_hold_s * FPS:
                        ab_t, ab_P, dis_n = t_b, P_s, 0
                        ab_k[0] = 0
                        stats["refine_resets"] += 1
                t_b, P_s = ab_t, ab_P
            lc.set_estimator(t_b, P_s)
            next_pred = t_b + math.floor((t_i - t_b) / P_s + 1.0) * P_s
            ready = [tp for tp in pending if tp + a.hit_win_s <= t_i + 1e-9]
            if ready:
                pending = [tp for tp in pending if tp + a.hit_win_s > t_i + 1e-9]
                for tp in ready:
                    judge(tp, fi)
                lc.update(t_i)
                lc.update_gate(h, quiet.quiet)
            elif not lc.pub_ok:
                lc.jump(t_i)
            cpu_line += time.process_time() - c1
            fi += 1
    end = float(arrive[-1]) if len(arrive) else T0
    while next_hb <= end:
        lc.update_gate(next_hb, quiet.quiet)
        lc.publish(next_hb)
        hb_i += 1
        next_hb = T0 + hb_i * 0.1
    stats.update({"quiet_starts": quiet.starts, "quiet_ends": quiet.ends, **pf.counts, **lc.counts})
    return {"lc": lc, "h_end": end, "trace": trace, "bn_beats": bn_beats, "stats": dict(stats), "quiet": quiet,
            "cpu": {"pf": cpu_pf, "line": cpu_line}, "confirmed": confirmed}


# -- main --------------------------------------------------------------------------------------------------------------
MODELS = pathlib.Path("~/.cache/screen-machine/beat-eval/alt-beatnet/models").expanduser()
_LINE = ["--infer", "hmm", "--hmm-fps", "100", "--act", "sum", "--refine", "--refine-ls", "--refine-hold-s", "3",
         "--refine-tempo-tol", "0.08", "--refine-reset", "0.25", "--halve", "--halve-min-bpm", "140",
         "--halve-margin", "0.2", "--halve-fast-bpm", "155", "--halve-fast-margin", "0.1", "--conf-scale", "0.85"]
PRESETS = {
    # BeatNet as shipped: model_1, its particle filter (two fixes), our line and conf on top
    "stock": [],
    # the stock CRNN with the room preset's line (madmom's online DBN as a forward filter, our alpha-beta line with
    # least-squares start, the octave check, conf scaled): what the line does without the fine-tune
    "hmm": _LINE,
    # the variant to measure: the CRNN fine-tuned on the room (alt/beatnet_train.py, run r1: 187 tracks, step 1250)
    # with that line (dev sweeps 1-7, 19 Oct)
    "room": ["--weights", str(MODELS / "bnroom-r1.pt"), *_LINE],
}


def expand_preset(argv: list[str]) -> list[str]:
    """``--preset NAME`` -> its options, in its place (an explicit option after it wins: argparse keeps the last)."""
    argv = list(argv)
    if "--preset" in argv:
        i = argv.index("--preset")
        name = argv[i + 1]
        if name not in PRESETS:
            raise SystemExit(f"--preset: one of {', '.join(PRESETS)}")
        argv = argv[:i] + PRESETS[name] + ["--name", f"beatnet-{name}"] + argv[i + 2:]
    return argv


def add_candidate_args(ap) -> None:
    g = ap.add_argument_group("BeatNet candidate")
    g.add_argument("--model", type=int, default=1, choices=(1, 2, 3), help="BeatNet CRNN (default 1)")
    g.add_argument("--ensemble", default=None, help="average these models' activations (e.g. 123)")
    g.add_argument("--weights", default=None, help="the CRNN's weights from this file instead (beatnet_train.py)")
    g.add_argument("--hmm-eps", type=float, default=0.0,
                   help="hmm: mix this share of a uniform posterior in per step (nothing underflows for good)")
    g.add_argument("--resample-quality", default="MQ", help="soxr quality to 22.05 kHz (default MQ: 14 ms)")
    g.add_argument("--crnn-stream", action="store_true", help="the LSTM frame by frame (the live cost)")
    g.add_argument("--no-cache", action="store_true", help="recompute stage 1 (lane, features, CRNN, dsp)")
    g.add_argument("--no-reuse", action="store_true", help="never take the stream and dsp gate from alt/clockvar_"
                                                          "replay.py's cache (identical; it only saves CPU)")
    g.add_argument("--infer", choices=("pf", "hmm"), default="pf",
                   help="pf: BeatNet's particle filter; hmm: madmom's online DBN model as an exact forward filter")
    g.add_argument("--act", choices=("max", "sum"), default="max", help="hmm: beat activation = max or sum of "
                                                                      "BeatNet's beat and downbeat outputs")
    g.add_argument("--lambda-t", type=float, default=100.0, help="hmm: tempo transition lambda (madmom 100)")
    g.add_argument("--obs-lambda", type=float, default=16.0, help="hmm: observation lambda (madmom 16)")
    g.add_argument("--hmm-fps", type=int, default=50, choices=(50, 100))
    g.add_argument("--act-norm", type=float, default=0.0,
                   help="seconds: replace the activation a by (a - m) / (1 - m), m its causal mean over this long "
                        "(times --act-norm-k): a clutter floor (room sound, the mic's spectral scramble) taken out")
    g.add_argument("--act-norm-k", type=float, default=1.0)
    g.add_argument("--act-chunk", type=int, default=1,
                   help="the CRNN runs on chunks of this many frames live (5 = 100 ms): each frame is usable only "
                        "when its chunk is complete (the latency of a cheaper live port)")
    g.add_argument("--refine", action="store_true", help="publish our own alpha-beta line, fed by the activation "
                                                         "peaks (sub-frame), reset to the filter's when they disagree")
    g.add_argument("--refine-alpha", type=float, default=0.2)
    g.add_argument("--refine-ls", action="store_true",
                   help="after each (re)start, the alpha-beta gains of a least-squares line fit (fading to "
                        "--refine-alpha): the line converges in a few beats instead of a dozen")
    g.add_argument("--refine-tempo-tol", type=float, default=0.04, help="tempo disagreement that counts")
    g.add_argument("--refine-hold-s", type=float, default=0.2,
                   help="a line that still hits follows the filter only after it disagreed this long")
    g.add_argument("--hit-mode", choices=("abs", "contrast"), default="abs",
                   help="contrast: a hit also needs the peak >= the off-beat window's peak + --hit-margin")
    g.add_argument("--hit-margin", type=float, default=0.1)
    g.add_argument("--refine-reset", type=float, default=0.15, help="beats of phase disagreement that reset it")
    g.add_argument("--halve", action="store_true",
                   help="with --refine: halve our line's tempo when its alternate beats' peaks differ by "
                        "--halve-margin over 16 beats (a double-time lock on the off-beats), and don't let a filter at "
                        "double our tempo reset it")
    g.add_argument("--halve-margin", type=float, default=0.2)
    g.add_argument("--halve-min-bpm", type=float, default=110.0)
    g.add_argument("--halve-fast-bpm", type=float, default=1000.0,
                   help="above this tempo the halving needs only --halve-fast-margin (a double-time lock is likelier "
                        "than a real 150+ bpm beat; half tempo still flashes on the beat)")
    g.add_argument("--halve-fast-margin", type=float, default=0.1)
    g.add_argument("--particles", type=int, default=1500)
    g.add_argument("--min-bpm", type=float, default=55.0)
    g.add_argument("--max-bpm", type=float, default=215.0)
    g.add_argument("--lambda-b", type=float, default=60.0)
    g.add_argument("--ig", type=float, default=0.4, help="information gate (activations under it correct nothing)")
    g.add_argument("--no-inject", action="store_true")
    g.add_argument("--cluster-tol", type=float, default=0.04)
    g.add_argument("--hit-thr", type=float, default=0.4)
    g.add_argument("--hit-win-s", type=float, default=0.07)
    g.add_argument("--conf-tol-ms", type=float, default=25.0)
    g.add_argument("--share-lo", type=float, default=0.3)
    g.add_argument("--share-hi", type=float, default=0.8)
    g.add_argument("--r-power", type=float, default=0.0, help="conf x R^this (phase concentration; 0 = off)")
    g.add_argument("--shrink-k", type=float, default=0.0,
                   help="conf's hit rate and timing terms shrunk toward their priors with this many pseudo-beats "
                        "instead of counting the missing ones of 8 as misses (0: Clock-like 8-beat ramp; 3 locks "
                        "faster)")
    g.add_argument("--conf-scale", type=float, default=1.0,
                   help="published conf = min(1, raw / this): the gate (0.70 on, 0.60 off) then opens at raw 0.70 x "
                        "this; set on dev so precision while gated stays >= 0.9")
    g.add_argument("--stab-sigma", type=float, default=0.0,
                   help="conf x exp(-(s / this)^2), s the std of log2(the line's bpm) over the last 8 beats: rubato "
                        "wanders, a steady beat doesn't (0 = off; 0.02 = 1.4 %% of tempo)")
    g.add_argument("--reset-on-quiet-end", action="store_true")
    g.add_argument("--name", default="beatnet", help="the candidate's name in the result")


CAND_KEYS = ("model", "weights", "refine_ls", "ensemble", "hmm_eps", "act_chunk", "act_norm", "act_norm_k", "refine", "refine_alpha", "refine_reset",
             "refine_tempo_tol", "refine_hold_s", "hit_mode", "hit_margin", "stab_sigma", "conf_scale", "shrink_k", "halve", "halve_margin", "halve_min_bpm", "halve_fast_bpm",
             "halve_fast_margin", "resample_quality", "crnn_stream", "infer", "act", "lambda_t", "obs_lambda", "hmm_fps", "particles", "min_bpm", "max_bpm", "lambda_b", "ig",
             "no_inject", "cluster_tol", "hit_thr", "hit_win_s", "conf_tol_ms", "share_lo", "share_hi", "r_power",
             "reset_on_quiet_end")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    lcm.add_stack_args(ap)
    add_candidate_args(ap)
    argv = expand_preset(list(sys.argv[1:] if argv is None else argv))
    a = ap.parse_args(argv)
    a.argv = argv
    t_all = time.perf_counter()
    arrays, meta = stage1(a, cache_on=not a.no_cache)
    res = build_result(a, arrays, meta, t_all=t_all)
    o = a.out or (pathlib.Path("~/.cache/screen-machine/beat-eval/runs").expanduser() / f"{res['id']}-beatnet.json")
    lcm.write(o, res)
    lcm.run_stack.summary(res, a.beats)
    cpu = res["cpu"]
    print(f"  cpu: {cpu['candidate_ms_per_s']:.1f} ms per audio s (lane {cpu.get('lane', 0):.1f}, feat "
          f"{cpu.get('feat', 0):.1f}, crnn {cpu.get('crnn', 0):.1f} {cpu['crnn_mode']}, pf {cpu.get('pf', 0):.1f}, "
          f"line {cpu.get('line', 0):.1f}); stage 1 {meta.get('cache')}")
    print(f"-> {o}")
    return 0


def build_result(a, arrays: dict, meta: dict, t_all: Optional[float] = None) -> dict:
    """Stage 2 (the filter, the line, the gate) on a track's stage 1, as run_stack.py's result JSON."""
    t1 = time.perf_counter()
    t_all = t1 if t_all is None else t_all
    out = run_filter(a, arrays, meta)
    t2 = time.perf_counter()
    lc = out["lc"]
    settings = {k: getattr(a, k) for k in CAND_KEYS}
    candidate = {"name": a.name, "settings": settings, "beatnet": "BeatNet 1.1.3 (pip), CRNN %s" % (a.weights or "model_%s" % (a.ensemble or a.model))}
    sid = "bn" + hashlib.sha256((lcm.file_sha(pathlib.Path(__file__), HERE / "lineclock.py")
                                 + json.dumps(settings, sort_keys=True)).encode()).hexdigest()[:9]
    audio_s = meta["audio_s"]
    c1 = meta["cpu_s"]
    cpu = {k: round(1000.0 * v / audio_s, 3) for k, v in c1.items()}
    cpu.update({k: round(1000.0 * v / audio_s, 3) for k, v in out["cpu"].items()})
    cpu["candidate_ms_per_s"] = round(sum(cpu.get(k, 0.0) for k in ("lane", "feat", "crnn", "pf", "line")), 3)
    cpu["crnn_mode"] = "stream" if a.crnn_stream else "batch"
    cpu["stage1"] = meta.get("cache")
    cpu["note"] = "process CPU ms per audio second (nice 19, 1 torch/BLAS thread); stage-1 numbers from its first run"
    # 2 Hz feature rows as run_stack's: [h, gate, lufs_s, energy, agc_db, quiet]
    fr = arrays["feat_rows"]
    agc = arrays["agc_rows"]
    gt = arrays["gate"]
    arrive = arrays["arrive"]
    q = lcm.Quiet()
    qflag = np.zeros(len(arrive), bool)
    for j in range(len(arrive)):
        q.level(float(gt[j]), T0 + j * BLOCK / FS)
        qflag[j] = q.quiet
    rows = []
    for r in fr:
        ja = min(len(arrive) - 1, int(np.searchsorted(arrive, r[0], side="right")) - 1) if len(arrive) else 0
        ka = int(np.searchsorted(agc[:, 0], r[0], side="right")) - 1 if len(agc) else -1
        rows.append([float(r[0]), None if math.isnan(r[1]) else float(r[1]), None if math.isnan(r[2]) else float(r[2]),
                     None if math.isnan(r[3]) else float(r[3]), float(agc[ka, 1]) if ka >= 0 else 0.0,
                     bool(qflag[max(0, ja)])])
    prep = {"y": np.zeros(meta["n48"], np.int8), "track_s": meta["track_s"], "preroll_s": meta["preroll_s"],
            "zero": meta["zero"], "degrade": meta["degrade"],
            "lost": None if not meta["lost"] else np.ones(meta["lost"], bool)}
    stats = {"seeds": out["stats"].get("seeds", 0), "octave_changes": out["stats"].get("octave_changes", 0),
             "cold_searches": 0, "corr_applied": 0, **out["stats"]}
    wall = {"stage1": t1 - t_all, "replay": t2 - t1, "total": time.perf_counter() - t_all, "slow_prepass": 0.0,
            **{f"stage1_{k}": v for k, v in meta["wall_s"].items()}}
    res = lcm.result(a, prep, lc, out["h_end"], candidate=candidate, stack_id=sid, stats=stats, feat_rows=rows,
                     features_stats=meta.get("features_stats", {}), wall=wall, cpu=cpu, t_start_unix=T_START_UNIX,
                     extra={"trace_fields": ["t_pred", "hit", "act", "resid_ms", "h8", "p_tol", "clar", "share", "R",
                                             "bpm", "conf", "state", "stab", "steady"],
                            "trace": out["trace"], "beatnet_beats": out["bn_beats"]})
    return res


if __name__ == "__main__":
    raise SystemExit(main())
