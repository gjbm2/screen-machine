#!/usr/bin/env python3
"""Candidate beat clock: madmom's STOCK online beat tracker, published as our clock line with a gate confidence.

What it is
----------
madmom's own online pipeline: ``RNNBeatProcessor(online=True)`` (2, 4 or all 8 of BEATS_LSTM, averaged) feeding
``DBNBeatTrackingProcessor(online=True)`` (the forward algorithm over Krebs/Böck/Widmer's beat state space, lambda
100, observation lambda 16, 45-200 bpm; a beat is reported when the forward path's best state enters the beat range,
at most one per 60/max_bpm s). What madmom does not give us, and this adds:
- **the front end as live**: the frames come from show_service.clock's own rhythm lane (mono, the slow bounded AGC,
  soxr to 44.1 kHz, 441-sample hops, the quiet gate that skips madmom while the room is quiet, the LSTM reset when
  it ends). ``DbnClock`` subclasses ``Clock`` and replaces only what happens to each frame (``_frame``) and what is
  published (``_data``), so run_stack.py replays it exactly as it replays our clock.
- **a published clock line** (bpm, anchor, beat count; ``music.clock``'s fields): re-anchored on every DBN beat, the
  period an LS fit over the last ``fit_n`` (8) beats (the DBN's integer-frame intervals jitter by a frame; the
  posterior-mean interval when there are too few beats or the fit disagrees with the DBN by > 6 %), the count kept
  across missed beats; a beat more than 1/4 beat off the line re-phases it (``epoch`` + 1). ``lag_ms`` 15 (the flashes'
  median offset was -11 ms clean and +0 ms in the room model at madmom's 20).
- **a confidence for the gate** from what the DBN knows (``conf_features``, 22 of them): the hit rate of its beats
  on the line (last 4 / 8 / 16 predicted beats), the beats on this line, the posterior's tempo mass (within 5 % of the
  best interval), its phase concentration there, the octave mass, the line's LS residual and its tempo agreement with
  the posterior, the predictive log-likelihood against a beatless model (``llr``, over 1 / 4 / 8 s: the best single
  feature, AUC 0.80), the activation's salience and contrast at the beats, our clock's tempogram clarity at the line's
  tempo, the tempo itself (log2 bpm/120 and its square), the line's wobble and drift. ``conf`` is a logistic of those
  (``CONF_W`` = madmom_dbn_conf.json, per net count, fitted on the DEV subset by dbn_sweep.py ``calib``: P(the line's
  next beat lands within 70 ms of a real one); a 25 ms label made the room's conf collapse, its jitter is wider), or
  a hand-set product (``conf_mode hand``). The gate is ours (hysteresis, 2-bar dwell, none while searching or quiet),
  at beat on 0.80 / off 0.60 and dmx 0.60 / 0.35 in the tuned variants (0.70 let rubato piano through).
- **re-seed rules**, all optional, none needed on the DEV subset: ``quiet_reset`` (the forward state starts over when
  the quiet gate ends, as the LSTM does; on by default), ``eps`` (each frame mixes eps of the uniform distribution
  back in), the ``challenger`` (a fresh forward pass every ``ch_every_s`` while not gated "beat"; after ``ch_min_s`` it
  replaces the incumbent if its predictive log-likelihood since its start is ``ch_margin`` nats better), a tempo prior
  for the start (``prior_oct``), dsp's kick hits as extra evidence (``hits_w``), an activation normaliser
  (``act_norm``). The stock DBN does not stick on room sound the way our clock does: on the real calibration recording
  (182 s of real room sound, then the track) it gates nothing in the room sound and locks as the music starts.

The HMM is rebuilt here in numpy (``BeatHMM``: madmom's BeatStateSpace, BeatTransitionModel and
RNNBeatTrackingObservationModel; ``selftest`` checks it reports madmom's beats frame for frame), so the tracker also
runs where madmom isn't installed (the sweeps run in ~/.venvs/sm-roundtrip); its forward step is a shift plus one
(intervals x intervals) product. The whole tracker costs 80-100 us a frame (8-10 ms per audio second, ~1 % of a
core); madmom's LSTMs are the cost (dbn_bench.py).

Commands (``~/.venvs/sm-show/bin/python``; heavy ones under ``flock ~/.cache/screen-machine/cpu.lock nice -n 19``)
-------------------------------------------------------------------------------------------------------------------
  run AUDIO [run_stack.py options] [--variant NAME] [--set k=v ...] [--dump PATH] [--record-all]
      run_stack.py with DbnClock in place of Clock: the same input, room model, pre-roll, time base, flashes and JSON
      (plus ``candidate``; ``stack.id`` is the candidate's id). ``--record-all`` runs all 8 nets and keeps each one's
      output in a dump (the clock itself uses the mean of the first ``nets``), so every tracker setting, for 2, 4 or
      8 nets, replays from it (dbn_sweep.py).
  replay DUMP_DIR OUT_DIR [--variant NAME] [--set k=v ...]      (any env with numpy; seconds a track)
      re-runs the tracker on each dump's frames (``<result>.dump.npz`` next to its JSON) and writes the JSON a
      ``run`` would have written with those settings (checked: identical flashes, also with hits, eps, challenger).
  batch --cond C,A --subset SUBSET.json --out 'DIR-{cond}' [--group-s S] [-- run options]
      the baseline's conditions (baseline.py's A clean, C room model + calibrated gate; --seed 0 --track-seeds,
      260 s cap, its pre-roll), one process and result per track, resumable, score.py per dir; ``--group-s 0`` is
      run_batch.py --runner this file (one CPU-lock slice a track), ``--group-s S`` shares slices of ~S s.
  selftest                                         (sm-show) BeatHMM against madmom; flashes against run_stack.
For the held-out measure (the baseline's subset, conditions A and C, the tuned variant):
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/alt/madmom_dbn.py batch --cond C,A \
      --subset ~/.cache/screen-machine/beat-eval/baseline-subset.json \
      --out '~/.cache/screen-machine/beat-eval/alt-madmom_dbn/test-{cond}' --group-s 120 -- --variant best
  ~/.venvs/sm-roundtrip/bin/python tools/room/beat_eval/score.py ~/.cache/screen-machine/beat-eval/alt-madmom_dbn/test-C
(or run_batch.py SUBSET --seed 0 --track-seeds --dur-s 260 --no-slow-lane [--degrade M --gate-ref-lufs L] --out DIR
--runner tools/room/beat_eval/alt/madmom_dbn.py -- --variant best). Add ``--record-all`` to replay other settings.

Measured on the DEV subset (dev-subset.json, 51 tracks, disjoint from the baseline's; 19 Oct; steady-beat means per
track as baseline.py reports them; the conf was fitted on these same tracks, so this is in-sample for the conf)
------------------------------------------------------------------------------------------------------------------
| variant (dbn_sweep.py eval) | cond | F | cover | prec (pooled) | wrong | tLock | locked | expressive false |
|---|---|---|---|---|---|---|---|---|
| our clock (clockvar's base replays) | A | 0.70 | 0.52 | 0.88 (0.93) | 0.027 | 8.6 s | 0.73 | 0 |
| stock2 (hand conf)   | A | 0.79 | 0.67 | 0.87 (0.90) | 0.015 | 6.1 s | 0.80 | 2 of 5 |
| stock8 (hand conf)   | A | 0.80 | 0.69 | 0.88 (0.86) | 0.018 | 7.0 s | 0.85 | 1 |
| best (8 nets)        | A | 0.80 | 0.75 | 0.88 (0.88) | 0.016 | 4.2 s | 0.85 | 0 |
| best2 (2 nets)       | A | 0.80 | 0.72 | 0.88 (0.91) | 0.002 | 4.4 s | 0.80 | 1 |
| cover (8, gate 0.70) | A | 0.80 | 0.79 | 0.88 (0.87) | 0.028 | 3.8 s | 0.83 | 2 |
| our clock            | C | 0.31 | 0.03 | 0.86 (0.96) | 0.125 | 25.6 s | 0.13 | 0 |
| stock2 (hand conf)   | C | 0.46 | 0.23 | 0.76 (0.89) | 0.129 | 15.2 s | 0.50 | 3 |
| stock8 (hand conf)   | C | 0.51 | 0.24 | 0.87 (0.86) | 0.049 | 14.4 s | 0.63 | 3 |
| best (8 nets)        | C | 0.51 | 0.16 | 0.84 (0.90) | 0.044 | 18.1 s | 0.46 | 0 |
| best2 (2 nets)       | C | 0.46 | 0.12 | 0.84 (0.90) | 0.094 | 18.0 s | 0.33 | 0 |
| cover (8, gate 0.70) | C | 0.51 | 0.23 | 0.88 (0.89) | 0.034 | 14.3 s | 0.65 | 2 |
In the room model a perfect gate on this line could cover only 0.49 of the beats (8 nets; 0.42 with 2): any flash
within 70 ms. No re-seed rule moved C (challenger, eps 1e-6, quiet_reset off: within 0.01-0.03), nor kick hits,
an activation normaliser, the DBN's lambdas (30/300, 8/32) or a start prior. max_bpm 160 lifted clean precision to
0.94 (the clean misses are slow songs locked at double tempo) but let rubato piano gate (Beethoven 40 % at 0.38).
The real calibration recording (rec.wav, 182 s of real room sound then the track; not a DEV track), ``best``: no
beat-gated flash in the room sound, then coverage 0.96, precision 0.99, 87 % within 25 ms; music alone, lock 3.3 s.
CPU (dbn_bench.py, one thread, niced under the 4K render, ms of CPU per audio second): madmom 2 nets 70-85, 8 nets
175-204 (x2.4-2.5), this tracker 8, our whole clock 99-100, this whole clock 112-116 (2 nets) / 230-260 (8 nets).
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
from typing import Any, Optional

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):  # as deploy/sm-show.env; the host renders 4K
    os.environ.setdefault(_k, "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
BEAT_EVAL = HERE.parent
REPO = HERE.parents[3]
T0 = 1000.0                     # run_stack.T0
FPS = 100.0
REPHASE_SKIP = 0.35             # show_service.artnet_out.REPHASE_SKIP (selftest checks it)
CANDIDATE = "madmom-dbn"

# -- the tracker's settings ---------------------------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "nets": 2,                  # how many of madmom's 8 BEATS_LSTM nets are averaged (the first n)
    "min_bpm": 45.0, "max_bpm": 200.0,  # our clock's range (madmom's own default is 55..215)
    "transition_lambda": 100.0, "observation_lambda": 16,      # madmom's defaults
    "lag_ms": 20.0,             # the activation's lag (clock.MadmomActivation.lag_s): frame time - this = beat time
    "fit_n": 8,                 # the line's period: LS over the last fit_n beats on it
    "fit_tol": 0.06,            # ... unless it disagrees with the DBN's posterior-mean period by more than this
    "phase_tol": 0.25,          # a DBN beat within this many beats of the line's is on it; else a re-phase
    "conf_mode": "logit",       # logit (CONF_W) | hand
    "conf_file": "",            # a JSON of logistic weights (dbn_sweep.py calib --out-w) in place of CONF_W
    "conf_cap_n": 4,            # conf <= conf_cap until this many beats are on the line (our clock's "confirmed")
    "conf_cap": 0.3,
    "beat_on": 0.70, "beat_off": 0.60, "dmx_on": 0.45, "dmx_off": 0.35, "dwell_bars": 2,
    "beats_per_bar": 4,
    "quiet_reset": True,        # the forward state starts over when the quiet gate ends (the LSTM does anyway)
    "act_reset": True,          # ... and whenever the LSTM is reset (a segment, a hole)
    "eps": 0.0,                 # uniform mass mixed back in each frame (0 = stock)
    "ch_every_s": 0.0,          # challenger: start one this often while not gated "beat" (0 = off)
    "ch_min_s": 6.0, "ch_max_s": 12.0, "ch_margin": 8.0,
    "ch_always": False,         # ... also while gated "beat"
    "tg": True,                 # run our clock's tempogram (clock.Tempogram on clock._Peaks' input) for two features
    "prior_oct": 0.0,           # the forward state's start: log-normal over tempo around 120 bpm, this sigma in octaves
                                # (0 = madmom's uniform start)
    "act_norm": 0.0,            # > 0: the activation over its own recent peak level (max-held, tau 10 s, floor 0.05)
                                # x this, capped at 0.95, before the DBN (0 = madmom's raw activation)
    "hits_w": 0.0,              # dsp's sub/bass hits as extra beat evidence: a' = 1 - (1 - a)(1 - hits_w x strength)
    "hits_win_ms": 15.0,        # ... a hit within this of the frame's time (only hits already heard count)
}
# variants by name (dbn_sweep.py measured them on the DEV subset, 19 Oct; the module docstring has the numbers)
_TUNED = {"lag_ms": 15.0, "conf_cap_n": 2, "beat_on": 0.8, "beat_off": 0.6, "dmx_on": 0.6, "dmx_off": 0.35}
VARIANTS: dict[str, dict] = {
    "stock2": {"nets": 2, "conf_mode": "hand"},                 # madmom's tracker as it comes + the hand-set conf
    "stock8": {"nets": 8, "conf_mode": "hand"},
    "best": {"nets": 8, **_TUNED},                              # the one to measure: 8 nets, the fitted conf, gate 0.8
    "best2": {"nets": 2, **_TUNED},                             # the same on today's 2 nets (no extra CPU)
    "best4": {"nets": 4, **_TUNED},
    "cover": {"nets": 8, **_TUNED, "beat_on": 0.7},             # more coverage in the room, but rubato piano gates
}

# conf = sigmoid(b + w . x) on conf_features' x (dbn_sweep.py calib on the DEV subset; None until fitted: hand)
# by the net count (the activation's spread differs): madmom_dbn_conf.json (dbn_sweep.py calib --label 70 on the DEV
# subset, A + C, 19 Oct); conf = P(the line's next beat within 70 ms of a real one). Missing: conf_mode hand.
try:
    CONF_W: dict[str, dict] = json.loads((HERE / "madmom_dbn_conf.json").read_text())["by_nets"]
except (OSError, ValueError, KeyError):
    CONF_W = {}
FEATURES = ("h8", "h4", "m_tempo", "r_phase", "m_oct", "resid", "n_line", "sal", "contrast", "lb", "lb2", "a_mean",
            "llr", "tg_clar", "tg_rel", "llr8", "llr1", "n_log", "h16", "t_agree", "ibi_cv", "t_drift")


def settings(variant: Optional[str] = None, sets: Optional[list[str]] = None) -> dict:
    c = dict(DEFAULTS)
    if variant:
        if variant not in VARIANTS:
            raise SystemExit(f"unknown variant {variant!r} (known: {', '.join(VARIANTS)})")
        c.update(VARIANTS[variant])
    for kv in sets or []:
        k, _, v = kv.partition("=")
        k = k.strip()
        if k not in DEFAULTS:
            raise SystemExit(f"--set {k}: not a setting ({', '.join(DEFAULTS)})")
        d = DEFAULTS[k]
        c[k] = (v.strip().lower() in ("1", "true", "on", "yes")) if isinstance(d, bool) else type(d)(v)
    return c


def _conf_w(cfg: dict) -> Optional[dict]:
    """The conf's logistic weights for these settings: ``conf_file``, else CONF_W for the net count, else None."""
    if cfg.get("conf_file"):
        return json.loads(pathlib.Path(cfg["conf_file"]).expanduser().read_text())
    return CONF_W.get(str(int(cfg["nets"])))


def fingerprint(cfg: dict) -> dict:
    """The candidate's id: its settings, the conf weights and this file (so a code change reads as another stack)."""
    src = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:12]
    w = _conf_w(cfg)
    if cfg.get("conf_file"):
        src += "+" + hashlib.sha256(json.dumps(w, sort_keys=True).encode()).hexdigest()[:8]
    blob = json.dumps({"cfg": cfg, "w": w, "src": src}, sort_keys=True, default=str)
    return {"name": CANDIDATE, "settings": cfg, "conf_w": w is not None and cfg["conf_mode"] == "logit", "src": src,
            "id": "c" + hashlib.sha256(blob.encode()).hexdigest()[:10]}


# -- madmom's beat HMM, in numpy -----------------------------------------------------------------------------------------
class BeatHMM:
    """madmom's BeatStateSpace + BeatTransitionModel + RNNBeatTrackingObservationModel (features/beats_hmm.py), the
    forward step as madmom's HiddenMarkovModel.forward computes it (selftest compares the two)."""

    def __init__(self, min_bpm: float, max_bpm: float, fps: float, transition_lambda: float,
                 observation_lambda: int) -> None:
        lo, hi = 60.0 * fps / max_bpm, 60.0 * fps / min_bpm
        iv = np.arange(np.round(lo), np.round(hi) + 1).astype(int)
        self.iv = iv
        self.N = int(iv.sum())
        self.first = np.cumsum(np.r_[0, iv[:-1]]).astype(int)
        self.last = (np.cumsum(iv) - 1).astype(int)
        self.pos = np.concatenate([np.linspace(0, 1, i, endpoint=False) for i in iv])
        self.sint = np.repeat(iv, iv)
        self.sidx = np.repeat(np.arange(len(iv)), iv)          # each state's interval index
        ratio = iv[None, :].astype(float) / iv[:, None].astype(float)    # [from, to]
        P = np.exp(-float(transition_lambda) * np.abs(ratio - 1.0))
        P[P <= np.spacing(1)] = 0.0
        P /= P.sum(axis=1, keepdims=True)
        self.P = np.ascontiguousarray(P)                        # tempo transitions at the beat boundary
        self.beat = self.pos < 1.0 / float(observation_lambda)
        self.beat_idx = np.nonzero(self.beat)[0]
        self.nb_c = 1.0 / (float(observation_lambda) - 1.0)
        self.cos = np.cos(2 * np.pi * self.pos)
        self.sin = np.sin(2 * np.pi * self.pos)

    def uniform(self) -> np.ndarray:
        return np.full(self.N, 1.0 / self.N)

    def prior(self, sigma_oct: float, bpm0: float = 120.0, fps: float = FPS) -> np.ndarray:
        """A start over tempo: log-normal around ``bpm0`` (sigma in octaves), uniform over each beat's positions."""
        if sigma_oct <= 0.0:
            return self.uniform()
        bpm = 60.0 * fps / self.sint
        w = np.exp(-0.5 * (np.log2(bpm / bpm0) / sigma_oct) ** 2) / self.sint
        return w / w.sum()

    def step(self, p: np.ndarray, a: float) -> tuple[np.ndarray, float]:
        """One forward step: the new normalised forward variable and log of its normaliser (the predictive
        likelihood of ``a``). ``p`` is not modified."""
        q = np.empty(self.N)
        q[1:] = p[:-1]                                          # within a beat: position + 1
        q[self.first] = p[self.last] @ self.P                   # the beat boundary: any tempo, exponentially
        nb = (1.0 - a) * self.nb_c                              # the observation: (1 - a) / (lambda - 1) off the beat,
        q *= nb
        if nb > 0.0:
            q[self.beat_idx] *= a / nb                          # ... a in the beat range
        c = float(q.sum())
        if not (c > 0.0 and math.isfinite(c)):
            return self.uniform(), -50.0
        q *= 1.0 / c
        return q, math.log(c)


# -- the activation: all 8 nets once, any first n of them ----------------------------------------------------------------
class MultiNetActivation:
    """clock.MadmomActivation (the same frames, buffer and reset), but the ensemble's nets are kept apart
    (``post_processor=None``): ``process_hop`` returns the mean of the first ``nets`` (what madmom's ensemble of those
    nets returns, to float32 rounding), and ``last`` holds every net's output, so a dump replays any n. With
    ``record_all`` all 8 run; otherwise only the first ``nets`` (the cost of the candidate itself)."""
    name = "madmom"
    lag_s = 0.020
    floor = 0.03
    FRAME = 2048

    def __init__(self, nets: int = 2, record_all: bool = False) -> None:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from madmom.audio.signal import Signal
            from madmom.features.beats import RNNBeatProcessor
            from madmom.models import BEATS_LSTM
        self.nets = max(1, min(int(nets), len(BEATS_LSTM)))
        files = list(BEATS_LSTM) if record_all else list(BEATS_LSTM[:self.nets])
        self.n_run = len(files)
        self._Signal = Signal
        self._proc = RNNBeatProcessor(post_processor=None, online=True, nn_files=files, origin="stream",
                                      num_frames=1, fps=FPS)
        self._buf = np.zeros(self.FRAME, np.float32)
        self._reset_next = True
        self.calls = 0
        self.fresh = False
        self.last = np.zeros(self.n_run, np.float32)
        self.process_hop(np.zeros(441, np.float32))
        self.calls = 0
        self.reset()

    def process_hop(self, hop: np.ndarray) -> float:
        b = self._buf
        b[:-441] = b[441:]
        b[-441:] = hop
        outs = self._proc(self._Signal(b, sample_rate=44100), reset=self._reset_next)
        self.fresh = self._reset_next
        self._reset_next = False
        v = np.array([float(np.asarray(o).reshape(-1)[-1]) for o in outs], np.float32)
        self.last = v
        self.calls += 1
        return max(0.0, float(np.mean(v[:self.nets], dtype=np.float32)))

    def reset(self) -> None:
        self._buf[:] = 0.0
        self._reset_next = True


# -- the tracker ----------------------------------------------------------------------------------------------------------
_GATE_RANK = {"none": 0, "dmx": 1, "beat": 2}
LINE_KEYS = ("bpm", "anchor_ms", "beat_at_anchor", "conf", "state", "gate", "epoch")   # run_stack._LINE_KEYS' share


def _sig(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, x))))


class DbnTracker:
    """Frames in (activation, time), a published line out. Pure (numpy only): the online clock (DbnClock) and the
    offline replay drive the same object the same way, so a dump reproduces a run."""

    def __init__(self, cfg: Optional[dict] = None, *, trace: bool = False) -> None:
        self.cfg = c = dict(DEFAULTS, **(cfg or {}))
        self.hmm = BeatHMM(c["min_bpm"], c["max_bpm"], FPS, c["transition_lambda"], int(c["observation_lambda"]))
        self.lag_s = float(c["lag_ms"]) / 1000.0
        self.min_ibi = 60.0 / float(c["max_bpm"])
        self._log_ol = math.log(float(c["observation_lambda"]))
        self._p0 = self.hmm.prior(float(c["prior_oct"]))
        self.W = _conf_w(c)
        self._tg = self._pk = None
        if c["tg"]:                                             # our clock's tempogram: a second opinion on the tempo
            sys.path.insert(0, str(REPO)) if str(REPO) not in sys.path else None
            from show_service import clock as clockmod
            self._tg = clockmod.Tempogram(bpm_hi=clockmod.GUARD_BPM)
            self._pk = clockmod._Peaks()
            self._tg_floor = clockmod.MadmomActivation.floor
        self.trace_on = trace
        self.trace: list[tuple] = []           # (t, k, x...) at each beat on the line (dbn_sweep.py calib)
        self.counts = collections.Counter()
        self._act: collections.deque = collections.deque(maxlen=int(4 * FPS))   # (t, a): the last 4 s
        self._llr: collections.deque = collections.deque(maxlen=int(4 * FPS))   # log(16 c): vs a beatless model
        self._llr_sum8 = 0.0                                                     # ... an 8 s running sum of it
        self._llr8: collections.deque = collections.deque()
        self._hitq: collections.deque = collections.deque(maxlen=64)             # (t, strength): dsp's low hits
        self._an_L = 0.05
        self._an_dec = math.exp(-1.0 / (10.0 * FPS))
        self.quiet = False
        self.epoch = 0
        self.k_pub = 0                          # beats published so far (the count carries over re-phases)
        self._line: Optional[tuple[int, float, float]] = None     # (k, t_k, T)
        self._hist: collections.deque = collections.deque(maxlen=max(3, int(c["fit_n"])))
        self._hits: collections.deque = collections.deque(maxlen=16)
        self._k_judged = -1
        self._n_line = 0                        # beats on the line since it started or re-phased
        self._ibis: collections.deque = collections.deque(maxlen=16)   # the line's beat-to-beat periods (rubato)
        self._sal: collections.deque = collections.deque(maxlen=8)      # activation peak at each beat (deferred)
        self._sal_wait: collections.deque = collections.deque()         # beat times waiting for their window
        self._x: dict = {}
        self.conf = 0.0
        self.gate = "none"
        self._gate_t = -1e9
        self._gate_up = True
        self.state = "searching"
        self.t_last = -1e9
        self.hmm_reset()

    # -- the forward state
    def hmm_reset(self) -> None:
        self.p = self._p0.copy()
        self.last_beat = -1e9
        self._ch: Optional[dict] = None
        self._ch_last = -1e9
        self.counts["hmm_resets"] += 1

    def set_quiet(self, quiet: bool, t: float) -> None:
        if quiet == self.quiet:
            return
        self.quiet = quiet
        if quiet:
            self.counts["quiet_starts"] += 1
            self.gate, self._gate_t, self._gate_up = "none", t, False
        else:
            self.counts["quiet_ends"] += 1
            if self.cfg["quiet_reset"]:
                self.hmm_reset()
        self._update_state()

    def key(self) -> tuple:
        """Changes whenever the published body's line or labels would (cheap: every frame)."""
        return (self._line, round(self.conf, 4), self.state, "none" if self.quiet else self.gate, self.epoch)

    # -- one frame
    def frame(self, a: float, t_raw: float, *, silent: bool = False, fresh: bool = False) -> None:
        """Activation ``a`` of the hop whose last sample sounds at host time ``t_raw``. ``silent``: no activation (the
        quiet room, a skipped hole): the forward state waits. ``fresh``: the LSTM was reset before this hop."""
        t = t_raw - self.lag_s
        self.t_last = t
        if fresh and self.cfg["act_reset"]:
            self.hmm_reset()
        if silent:
            self.counts["silent_frames"] += 1
            self._judge(t)
            return
        self.counts["frames"] += 1
        if self.cfg["hits_w"] > 0.0 and self._hitq:
            w = self.cfg["hits_win_ms"] / 1000.0
            k = max((st for (th, st) in self._hitq if abs(th - t) <= w), default=0.0)
            if k > 0.0:
                a = 1.0 - (1.0 - a) * (1.0 - min(1.0, self.cfg["hits_w"] * k))
                self.counts["hit_frames"] += 1
        if self.cfg["act_norm"] > 0.0:
            self._an_L = max(float(a), self._an_L * self._an_dec, 0.05)
            a = min(0.95, float(a) * self.cfg["act_norm"] / self._an_L)
        a = min(1.0 - 1e-6, max(1e-6, float(a)))
        self._act.append((t, a))
        self._salience(t)
        if self._tg is not None:
            v, _ = self._pk.push(a, t, self._tg_floor)
            self._tg.push(v, t)
        p, ll = self.hmm.step(self.p, a)
        lr = ll + self._log_ol                                  # vs a beatless model's 1 / observation_lambda
        self._llr.append(lr)
        self._llr8.append(lr)
        self._llr_sum8 += lr
        if len(self._llr8) > 8 * FPS:
            self._llr_sum8 -= self._llr8.popleft()
        c = self.cfg
        if c["eps"] > 0.0:
            p *= 1.0 - c["eps"]
            p += c["eps"] / self.hmm.N
        self.p = p
        self._challenger(a, ll, t)
        s = int(np.argmax(self.p))
        if self.hmm.beat[s] and t >= self.last_beat + self.min_ibi:     # madmom's online rule
            self.last_beat = t
            self.counts["dbn_beats"] += 1
            self._on_beat(t, s)
        self._judge(t)

    def hit(self, t_s: float, strength: float) -> None:
        """A dsp sub/bass hit (acoustic host time), heard before the next frame."""
        self._hitq.append((float(t_s), float(strength)))

    def _challenger(self, a: float, ll: float, t: float) -> None:
        c = self.cfg
        if c["ch_every_s"] <= 0.0:
            return
        ch = self._ch
        if ch is None:
            if t - self._ch_last >= c["ch_every_s"] and (self.gate != "beat" or c["ch_always"]):
                self._ch = {"p": self._p0.copy(), "t0": t, "ll": 0.0, "inc": 0.0}
                self._ch_last = t
                self.counts["ch_starts"] += 1
            return
        q, llc = self.hmm.step(ch["p"], a)
        if c["eps"] > 0.0:
            q *= 1.0 - c["eps"]
            q += c["eps"] / self.hmm.N
        ch["p"], ch["ll"], ch["inc"] = q, ch["ll"] + llc, ch["inc"] + ll
        age = t - ch["t0"]
        if age >= c["ch_min_s"] and ch["ll"] - ch["inc"] > c["ch_margin"]:
            self.p = ch["p"]
            self._ch = None
            self.counts["ch_swaps"] += 1
        elif age >= c["ch_max_s"]:
            self._ch = None

    # -- the line
    def _T_dbn(self, s: int) -> float:
        """The posterior-mean period (s) over the intervals within 10 % of the best state's."""
        P = np.add.reduceat(self.p, self.hmm.first)
        iv = self.hmm.iv
        i0 = iv[self.hmm.sidx[s]]
        m = np.abs(iv / i0 - 1.0) <= 0.10
        w = P[m]
        return float((w * iv[m]).sum() / max(1e-12, w.sum())) / FPS

    def _on_beat(self, t: float, s: int) -> None:
        c = self.cfg
        Td = self._T_dbn(s)
        if self._line is None:
            k = self.k_pub
            self._hist.clear()
            self._hist.append((k, t))
            self._line = (k, t, Td)
            self._k_judged = k
            self._hits.append(1.0)
            self._n_line = 1
            self.counts["lines"] += 1
        else:
            k0, t0, T = self._line
            x = k0 + (t - t0) / T
            k = int(round(x))
            if k > self._k_judged - 1 and abs(x - k) <= c["phase_tol"] and k > self._hist[-1][0] \
                    and abs(T / Td - 1.0) <= 0.15:
                for _ in range(self._k_judged + 1, k):          # beats the DBN skipped
                    self._hits.append(0.0)
                if k > self._k_judged:
                    self._hits.append(1.0)
                self._k_judged = max(self._k_judged, k)
                kp, tp = self._hist[-1]
                self._ibis.append((t - tp) / max(1, k - kp))
                self._hist.append((k, t))
                self._n_line += 1
            else:                                               # off the line: re-phase onto the DBN's beat
                k = max(self._hist[-1][0] + 1, int(math.floor(x + 0.5)))
                self._hits.append(0.0)
                self._hist.clear()
                self._hist.append((k, t))
                self._k_judged = k
                self._n_line = 1
                self.epoch += 1
                self.counts["rephases"] += 1
        T = Td
        k_a, t_a = k, t
        resid = math.nan
        if len(self._hist) >= 3:
            kk = np.array([h[0] for h in self._hist], float)
            tt = np.array([h[1] for h in self._hist], float)
            A = np.vstack([np.ones_like(kk), kk - kk[-1]]).T
            (b0, b1), *_ = np.linalg.lstsq(A, tt, rcond=None)
            if b1 > 0 and abs(b1 / Td - 1.0) <= c["fit_tol"]:
                T, t_a = float(b1), float(b0)
                resid = float(np.sqrt(np.mean((tt - A @ np.array([b0, b1])) ** 2))) / T
            else:
                self.counts["fit_rejects"] += 1
        self._line = (k_a, t_a, T)
        self.k_pub = k_a
        self._sal_wait.append(t)
        self._conf_update(t, s, resid)

    def _judge(self, t: float) -> None:
        """Predicted beats that passed without a DBN beat are misses (conf falls through a breakdown)."""
        if self._line is None:
            return
        k0, t0, T = self._line
        changed = False
        while t > t0 + (self._k_judged + 1 - k0) * T + 0.35 * T:
            self._k_judged += 1
            self._hits.append(0.0)
            changed = True
        if changed:
            self._conf_update(t, None, math.nan)

    def _salience(self, t: float) -> None:
        """Each beat's activation peak (within +-70 ms), once that window has been heard."""
        while self._sal_wait and t >= self._sal_wait[0] + 0.07:
            tb = self._sal_wait.popleft()
            v = [a for (ta, a) in self._act if abs(ta - tb) <= 0.07]
            self._sal.append(max(v) if v else 0.0)

    # -- conf, state, gate
    def conf_features(self, s: Optional[int], resid: float) -> dict:
        x = dict(self._x)
        h = list(self._hits)
        x["h16"] = float(np.mean(h)) if h else 0.0
        x["h8"] = float(np.mean(h[-8:])) if h else 0.0
        x["h4"] = float(np.mean(h[-4:])) if h else 0.0
        if s is not None:                                       # at a beat: the posterior's view
            hm = self.hmm
            P = np.add.reduceat(self.p, hm.first)
            iv = hm.iv
            i0 = iv[hm.sidx[s]]
            near = np.abs(iv / i0 - 1.0) <= 0.05
            x["m_tempo"] = float(P[near].sum())
            oc = (np.abs(iv / (2 * i0) - 1.0) <= 0.05) | (np.abs(iv / (0.5 * i0) - 1.0) <= 0.05)
            x["m_oct"] = float(P[oc].sum())
            ms = near[hm.sidx]
            w = self.p[ms]
            sw = float(w.sum())
            x["r_phase"] = float(math.hypot(float(w @ hm.cos[ms]), float(w @ hm.sin[ms])) / sw) if sw > 0 else 0.0
            x["resid"] = min(0.2, resid) if math.isfinite(resid) else 0.2
            lb = math.log2(max(1e-6, 60.0 / self._line[2]) / 120.0) if self._line else 0.0
            x["lb"], x["lb2"] = lb, lb * lb
            x["n_line"] = min(16, self._n_line) / 16.0
            sal = list(self._sal)
            x["sal"] = float(np.mean(sal)) if sal else 0.0
            act = [a for (_, a) in self._act]
            x["a_mean"] = float(np.mean(act)) if act else 0.0
            x["contrast"] = x["sal"] / (x["sal"] + x["a_mean"] + 1e-6)
            x["llr"] = float(np.mean(self._llr)) if self._llr else 0.0     # nats a frame the DBN predicts better
            x["llr8"] = self._llr_sum8 / len(self._llr8) if self._llr8 else 0.0
            x["llr1"] = float(np.mean(list(self._llr)[-100:])) if self._llr else 0.0
            x["n_log"] = math.log2(1.0 + self._n_line) / 6.0
            x["t_agree"] = min(0.2, abs(self._line[2] / self._T_dbn(s) - 1.0)) if self._line else 0.2
            ib = np.array(self._ibis) if len(self._ibis) >= 4 else None
            x["ibi_cv"] = min(0.3, float(ib.std() / ib.mean())) if ib is not None else 0.3        # tempo wobble
            x["t_drift"] = min(0.3, abs(float(ib[-4:].mean() / ib[:4].mean()) - 1.0)) if ib is not None and \
                len(ib) >= 8 else 0.3                                                              # ... and drift
            x["tg_clar"] = x["tg_rel"] = 0.0
            if self._tg is not None and self._line is not None and self._tg.filled >= 100:
                X = self._tg.spectrum()
                bpm = 60.0 / self._line[2]
                cl = max(self._tg.clarity(X, bpm), self._tg.clarity(X, 2 * bpm), self._tg.clarity(X, bpm / 2))
                x0 = X[0].real
                best = float(np.abs(X[1:]).max() / x0) if x0 > 1e-6 else 0.0
                x["tg_clar"] = self._tg.clarity(X, bpm)
                x["tg_rel"] = cl / best if best > 0 else 0.0
            for k in FEATURES:
                x.setdefault(k, 0.0)
            self._x = x
        return x

    def _conf_update(self, t: float, s: Optional[int], resid: float) -> None:
        c = self.cfg
        x = self.conf_features(s, resid)
        if not x.get("m_tempo") and s is None and not self._x:
            return
        W = self.W
        if c["conf_mode"] == "logit" and W is not None:
            z = W["b"] + sum(W["w"][k] * (x[k] - W["mu"][k]) / W["sd"][k] for k in W["w"])
            conf = _sig(z)
        else:                                                   # hand-set: hits x tempo certainty x phase x fit
            conf = x["h8"] * math.sqrt(max(0.0, x["m_tempo"])) * x["r_phase"] * max(0.0, 1.0 - x["resid"] / 0.1)
        if self._n_line < c["conf_cap_n"]:
            conf = min(conf, c["conf_cap"])
        self.conf = min(0.99, max(0.0, conf))
        if self.trace_on and s is not None:
            self.trace.append((t, *self._line, *(x[k] for k in FEATURES)))     # (t, k, t_k, T, x...)
        self._update_state()
        self._update_gate(t)

    def _update_state(self) -> None:
        if self._line is None:
            self.state = "searching"
        elif self.quiet:
            self.state = "holding"
        elif sum(list(self._hits)[-4:]) >= 2.0:
            self.state = "locked"
        else:
            self.state = "holding"

    def _update_gate(self, t: float) -> None:
        """clock.Clock._update_gate, on this conf."""
        c = self.cfg
        cf, g = self.conf, self.gate
        if self.state == "searching" or self.quiet:
            new = "none"
        elif g == "none":
            new = "beat" if cf >= c["beat_on"] else ("dmx" if cf >= c["dmx_on"] else "none")
        elif g == "dmx":
            new = "beat" if cf >= c["beat_on"] else ("none" if cf < c["dmx_off"] else "dmx")
        else:
            new = "beat" if cf >= c["beat_off"] else ("dmx" if cf >= c["dmx_off"] else "none")
        if new == g:
            return
        up = _GATE_RANK[new] > _GATE_RANK[g]
        T = self._line[2] if self._line else 0.5
        recent = t - self._gate_t < c["dwell_bars"] * c["beats_per_bar"] * T
        if recent and not (new == "none" and (self.state == "searching" or self.quiet)) and \
                (not up or not self._gate_up):
            return
        self.gate, self._gate_t, self._gate_up = new, t, up

    # -- the published body (contract.ClockData's fields)
    def body(self, now_s: float) -> dict:
        ln = self._line
        return {"bpm": round(60.0 / ln[2], 6) if ln else 0.0, "anchor_ms": ln[1] * 1000.0 if ln else 0.0,
                "beat_at_anchor": ln[0] if ln else 0, "beats_per_bar": int(self.cfg["beats_per_bar"]),
                "bar_anchor_beat": 0, "phrase_bars": 8, "conf": round(self.conf, 4), "bar_conf": 0.0,
                "phrase_conf": 0.0, "state": self.state, "gate": "none" if self.quiet else self.gate,
                "epoch": self.epoch, "stability": round(float(self._x.get("m_tempo", 0.0)), 4),
                "source": f"madmom-dbn{self.cfg['nets']}", "t_ms": now_s * 1000.0}

    def stats(self) -> dict:
        return {"candidate": CANDIDATE, **dict(self.counts), "state": self.state, "gate": self.gate,
                "conf": round(self.conf, 3), "bpm": round(60.0 / self._line[2], 3) if self._line else 0.0,
                "epoch": self.epoch}


# -- the online clock (show_service.clock.Clock with this tracker) -------------------------------------------------------
_INSTANCES: list = []


def make_clock_class():
    """DbnClock, built on the show_service.clock.Clock found at call time (imported here: only sm-show has madmom)."""
    from show_service import clock as clockmod

    class DbnClock(clockmod.Clock):
        """clock.Clock's front end (AGC, soxr, hops, the quiet gate, the activation, its resets) with DbnTracker as
        everything after the activation. ``dump`` (a list) collects what a replay needs: every frame and every quiet
        change, with the host time it happened at (``host_now``: run_stack's Replay.now)."""

        def __init__(self, publish=None, *, cfg: dict, record_all: bool = False, dump: Optional[dict] = None,
                     host_now=None, **kw) -> None:
            act = MultiNetActivation(int(cfg["nets"]), record_all)
            self.trk = DbnTracker(cfg)
            self._dump = dump
            self._host_now = host_now or (lambda: math.nan)
            self._calls_seen = 0
            kw.pop("beat_thread", None)
            super().__init__(publish, activation=act, beat_thread=False, beat_proc=False, **kw)
            self._calls_seen = act.calls

        def _frame(self, a: float, t_raw: float) -> bool:
            act = self.act
            silent = act.calls == self._calls_seen
            self._calls_seen = act.calls
            fresh = bool(act.fresh) and not silent
            if self._dump is not None:
                self._dump["frames"].append((self._host_now(), t_raw, silent, fresh))
                self._dump["acts"].append(act.last if not silent else np.zeros(act.n_run, np.float32))
            k0 = self.trk.key()
            self.trk.frame(a, t_raw, silent=silent, fresh=fresh)
            self._t_known = t_raw - act.lag_s
            self._counts["frames"] += 1
            if self._no_audio:
                self._no_audio = False
            return self.trk.key() != k0

        def observe_hit(self, t_s: float, band: str, strength: float = 1.0) -> None:
            super().observe_hit(t_s, band, strength)
            if band in ("sub", "bass"):
                self.trk.hit(t_s, strength)
            if self._dump is not None and band in ("sub", "bass"):          # dsp's low hits (a later variant's input)
                self._dump["hits"].append((self._host_now(), float(t_s), 0.0 if band == "sub" else 1.0,
                                           float(strength), len(self._dump["frames"])))

        def set_level(self, gate, t_s: float) -> None:
            was = self._quiet
            super().set_level(gate, t_s)                        # emits through _data on a change
            if self._quiet != was:
                self.trk.set_quiet(self._quiet, t_s)
                if self._dump is not None:
                    self._dump["events"].append((self._host_now(), t_s, bool(self._quiet), len(self._dump["frames"])))
                self._emit(t_s)

        def _update_state(self) -> None:
            pass

        def _data(self, now_s: float):
            self.trk.set_quiet(self._quiet, now_s)
            return self.trk.body(now_s)

        def stats(self) -> dict:
            s = super().stats()
            s.update(self.trk.stats())
            s["source"] = f"madmom-dbn{self.trk.cfg['nets']}"
            return s

    return DbnClock


# -- the published line -> flashes (run_stack.flashes, on (h, body) pairs) -----------------------------------------------
def flashes(bodies: list[tuple[float, dict]], h_end: float) -> list[tuple]:
    """run_stack.flashes, copied so the replay runs without show_service's env (selftest compares the two)."""
    out: list[tuple] = []
    last: Optional[float] = None
    for i, (h, b) in enumerate(bodies):
        h1 = bodies[i + 1][0] if i + 1 < len(bodies) else h_end
        bpm = float(b.get("bpm") or 0.0)
        if bpm <= 0 or h1 <= h:
            continue
        P = 60.0 / bpm
        a = float(b["anchor_ms"]) / 1000.0
        k0 = int(b["beat_at_anchor"])
        k = math.ceil(k0 + (h - a) / P - 1e-9)
        while True:
            t = a + (k - k0) * P
            if t >= h1:
                break
            if t >= h and (last is None or t > last + REPHASE_SKIP * P):
                out.append((t, k, b["gate"], b["conf"], b["state"], b["epoch"], bpm))
                last = t
            k += 1
    return out


def r6(v: Any) -> Any:
    return round(v, 6) if isinstance(v, float) else v


# -- replay from a dump --------------------------------------------------------------------------------------------------
def load_dump(path: pathlib.Path) -> dict:
    z = np.load(path, allow_pickle=False)
    d = {k: z[k] for k in z.files}
    d["meta"] = json.loads(str(d["meta"]))
    return d


def replay_dump(d: dict, cfg: dict, *, trace: bool = False) -> tuple[list[tuple[float, dict]], DbnTracker, float]:
    """The bodies a ``run`` with ``cfg`` would have published (the same frames, quiet changes and host times), the
    tracker, and the tracker's CPU seconds."""
    trk = DbnTracker(cfg, trace=trace)
    nets = int(cfg["nets"])
    acts = d["acts"]
    if acts.shape[1] < nets:
        raise SystemExit(f"the dump holds {acts.shape[1]} nets' outputs; {nets} asked for (run it with --record-all)")
    a_mean = acts[:, :nets].mean(axis=1, dtype=np.float32)
    h_arr, t_arr, sil, fresh = d["h"], d["t_raw"], d["silent"], d["fresh"]
    ev = d["events"]                                            # (h, t, quiet, frame index it came before)
    hits = d["hits"] if "hits" in d else np.zeros((0, 5))       # (h, t, band, strength, frame index)
    hi = 0
    bodies: list[tuple[float, dict]] = []
    last_key = None

    def emit(h: float, now_s: float) -> None:
        nonlocal last_key
        b = trk.body(now_s)
        key = tuple(b[k] for k in LINE_KEYS) + (b["bar_conf"],)
        if key != last_key:
            last_key = key
            bodies.append((h, b))

    emit(T0, T0)                                                # the constructor's first heartbeat
    ei = 0
    c0 = time.process_time()
    for i in range(len(t_arr)):
        while ei < len(ev) and int(ev[ei][3]) <= i:
            trk.set_quiet(bool(ev[ei][2]), float(ev[ei][1]))
            emit(float(ev[ei][0]), float(ev[ei][1]))
            ei += 1
        while hi < len(hits) and int(hits[hi][4]) <= i:
            trk.hit(float(hits[hi][1]), float(hits[hi][3]))
            hi += 1
        k0 = trk.key()
        trk.frame(float(max(0.0, a_mean[i])), float(t_arr[i]), silent=bool(sil[i]), fresh=bool(fresh[i]))
        if trk.key() != k0:
            emit(float(h_arr[i]), float(t_arr[i]))
    while ei < len(ev):
        trk.set_quiet(bool(ev[ei][2]), float(ev[ei][1]))
        emit(float(ev[ei][0]), float(ev[ei][1]))
        ei += 1
    return bodies, trk, time.process_time() - c0


def result_from(base: dict, d: dict, cfg: dict, bodies: list, trk: DbnTracker, cpu_s: float) -> dict:
    """The base run's result with this variant's bodies, flashes, stats and fingerprint."""
    zero = float(d["meta"]["zero"])
    h_end = float(d["meta"]["h_end"])
    fl = flashes(bodies, h_end)

    def rel(v: float) -> float:
        return round(v - zero, 6)

    rows = [[rel(h), rel(b["t_ms"] / 1000.0), rel(b["anchor_ms"] / 1000.0) if b["bpm"] else None, r6(b["bpm"]),
             b["beat_at_anchor"], b["beats_per_bar"], b["bar_anchor_beat"], b["conf"], b["bar_conf"], b["state"],
             b["gate"], b["epoch"], b["stability"]] for h, b in bodies]
    r = dict(base)
    r["bodies"] = rows
    r["published"] = len(rows)
    r["flashes"] = [[rel(f[0]), *f[1:6], r6(f[6])] for f in fl]
    r["stats"] = {**trk.stats(), "tracker_cpu_s": round(cpu_s, 3)}
    fp = fingerprint(cfg)
    r["candidate"] = {**fp, "replayed_from": base.get("candidate", {}).get("id")}
    r["stack"] = {**(base.get("stack") or {}), "id": fp["id"], "candidate": fp["name"]}
    return r


def cmd_replay(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="madmom_dbn.py replay")
    ap.add_argument("dumps", type=pathlib.Path)
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--set", action="append", default=[], dest="sets")
    a = ap.parse_args(argv)
    cfg = settings(a.variant, a.sets)
    a.out.mkdir(parents=True, exist_ok=True)
    n = 0
    for dp in sorted(a.dumps.glob("*.dump.npz")):
        base_p = dp.with_name(dp.name[:-len(".dump.npz")] + ".json")
        if not base_p.exists():
            continue
        base = json.loads(base_p.read_text())
        d = load_dump(dp)
        bodies, trk, cpu = replay_dump(d, cfg)
        r = result_from(base, d, cfg, bodies, trk, cpu)
        (a.out / base_p.name).write_text(json.dumps(r, separators=(",", ":"), default=float))
        n += 1
    print(f"{n} tracks -> {a.out} ({fingerprint(cfg)['id']})")
    return 0


# -- run: run_stack.py with DbnClock -------------------------------------------------------------------------------------
def cmd_run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="madmom_dbn.py run", add_help=False)
    ap.add_argument("--variant", default=None)
    ap.add_argument("--set", action="append", default=[], dest="sets")
    ap.add_argument("--record-all", action="store_true", help="run all 8 nets and keep each one's output in the dump")
    ap.add_argument("--dump", default="auto", help="the dump's path ('auto': <out>.dump.npz with --record-all; 'off')")
    ours, rest = ap.parse_known_args(argv)
    cfg = settings(ours.variant, ours.sets)
    sys.path.insert(0, str(BEAT_EVAL))
    import run_stack                                            # noqa: E402  (sm-show: show_service as deployed)
    from show_service import clock as clockmod
    DbnClock = make_clock_class()
    want_dump = ours.dump != "off" and (ours.dump != "auto" or ours.record_all)
    dump: Optional[dict] = {"frames": [], "acts": [], "events": [], "hits": []} if want_dump else None
    holder: dict = {}

    def factory(publish=None, **kw):
        rp = getattr(publish, "__self__", None)
        clk = DbnClock(publish, cfg=cfg, record_all=ours.record_all, dump=dump,
                       host_now=(lambda: rp.now) if rp is not None else None, **kw)
        holder["clk"], holder["rp"] = clk, rp
        _INSTANCES.append(clk)
        return clk

    clockmod.Clock = factory                                    # run_stack.Replay builds clockmod.Clock(...)
    base_fp = run_stack.stack_fingerprint
    fp = fingerprint(cfg)

    def stack_fp() -> dict:
        s = base_fp()
        s["show_stack_id"] = s.get("id")
        s["id"], s["candidate"] = fp["id"], fp["name"]
        return s

    run_stack.stack_fingerprint = stack_fp
    rc = run_stack.main(rest)
    out = _out_path(rest, run_stack)
    if rc == 0 and out is not None and out.exists():
        r = json.loads(out.read_text())
        clk = holder.get("clk")
        r["candidate"] = {**fp, "record_all": ours.record_all,
                          "nets_run": clk.act.n_run if clk is not None else None}
        if dump is not None and clk is not None:
            zero = _zero(r)
            dp = pathlib.Path(ours.dump) if ours.dump not in ("auto", "on") else \
                out.with_name(out.name[:-len(".json")] + ".dump.npz")
            fr = dump["frames"]
            meta = {"zero": zero, "h_end": holder["rp"].now if holder.get("rp") is not None else math.nan,
                    "nets_run": clk.act.n_run, "cfg": cfg, "id": r.get("id"), "audio_s": r.get("audio_s")}
            np.savez_compressed(dp, h=np.array([f[0] for f in fr], np.float64),
                                t_raw=np.array([f[1] for f in fr], np.float64),
                                silent=np.array([f[2] for f in fr], bool), fresh=np.array([f[3] for f in fr], bool),
                                acts=np.array(dump["acts"], np.float32).reshape(len(fr), -1),
                                events=np.array(dump["events"], np.float64).reshape(-1, 4),
                                hits=np.array(dump["hits"], np.float64).reshape(-1, 5),
                                meta=np.array(json.dumps(meta)))
            r["candidate"]["dump"] = str(dp)
        out.write_text(json.dumps(r, separators=(",", ":"), default=float))
    return rc


def _out_path(rest: list[str], run_stack) -> Optional[pathlib.Path]:
    for i, v in enumerate(rest):
        if v == "--out" and i + 1 < len(rest):
            return pathlib.Path(rest[i + 1])
        if v.startswith("--out="):
            return pathlib.Path(v.split("=", 1)[1])
    aud = next((v for v in rest if not v.startswith("-")), None)
    tid = None
    for i, v in enumerate(rest):
        if v == "--id" and i + 1 < len(rest):
            tid = rest[i + 1]
    if aud is None:
        return None
    return run_stack.OUT_DIR / f"{tid or pathlib.Path(aud).stem}.json"


def _zero(r: dict) -> float:
    """run_stack's zero: the host time of the original's first sample."""
    return T0 + float(r.get("preroll_s") or 0.0) + float((r.get("degrade") or {}).get("delay_s") or 0.0)


# -- selftest ------------------------------------------------------------------------------------------------------------
def cmd_selftest(argv: list[str]) -> int:
    """BeatHMM + the online rule against madmom's DBNBeatTrackingProcessor(online=True), frame by frame, on a real
    activation (a synthetic kick loop through madmom's 2 nets), for two settings; and REPHASE_SKIP / flashes against
    show_service and run_stack."""
    import warnings
    warnings.simplefilter("ignore")
    from madmom.features.beats import DBNBeatTrackingProcessor
    sys.path.insert(0, str(BEAT_EVAL))
    import run_stack
    from show_service.artnet_out import REPHASE_SKIP as RS
    assert RS == REPHASE_SKIP, (RS, REPHASE_SKIP)
    act = MultiNetActivation(2)
    rng = np.random.default_rng(3)
    fs = 44100
    n_hops = 1500
    t = np.arange(n_hops * 441) / fs
    bpm = 123.0
    ph = (t * bpm / 60.0) % 1.0
    x = (0.5 * np.sin(2 * np.pi * 55 * t) * np.exp(-ph * 12) + 0.03 * rng.standard_normal(len(t))
         + 0.1 * np.sin(2 * np.pi * 3000 * t) * np.exp(-((ph + 0.5) % 1.0) * 30)).astype(np.float32)
    acts = np.array([act.process_hop(x[i * 441:(i + 1) * 441]) for i in range(n_hops)])
    ok = True
    for mn, mx, tl in ((45.0, 200.0, 100.0), (55.0, 215.0, 30.0)):
        mm = DBNBeatTrackingProcessor(min_bpm=mn, max_bpm=mx, transition_lambda=tl, fps=100, online=True)
        mm.reset()
        ref = []
        for i, a in enumerate(acts):
            b = mm.process_online(np.array([a]), reset=False)
            ref += list(b)
        hm = BeatHMM(mn, mx, FPS, tl, 16)
        p = hm.uniform()
        mine, last = [], 0.0
        for i, a in enumerate(acts):
            p, _ = hm.step(p, float(a))
            s = int(np.argmax(p))
            cur = i / FPS
            if hm.beat[s] and cur >= last + 60.0 / mx:
                last = cur
                mine.append(cur)
        same = len(ref) == len(mine) and np.allclose(ref, mine)
        ok &= same
        print(f"BeatHMM vs madmom ({mn:g}-{mx:g} bpm, lambda {tl:g}): madmom {len(ref)} beats, ours {len(mine)}: "
              f"{'identical' if same else 'DIFFERENT'}; median IBI {np.median(np.diff(ref)) if len(ref) > 1 else 0:.3f}"
              f" s ({60 / bpm:.3f})")
    bodies = [(1000.0, {"bpm": 120.0, "anchor_ms": 1000250.0, "beat_at_anchor": 5, "gate": "beat", "conf": 0.8,
                        "state": "locked", "epoch": 0}),
              (1002.1, {"bpm": 121.0, "anchor_ms": 1002260.0, "beat_at_anchor": 9, "gate": "dmx", "conf": 0.5,
                        "state": "locked", "epoch": 1})]
    same_fl = flashes(bodies, 1005.0) == run_stack.flashes(bodies, 1005.0)
    ok &= same_fl
    print(f"flashes == run_stack.flashes: {same_fl}")
    return 0 if ok else 1


def cmd_batch(argv: list[str]) -> int:
    """This runner over a subset under baseline.py's conditions (A, C, ...), with the baseline's per-track seeds
    (--seed 0 --track-seeds), 260 s cap, pre-roll and gate. ``--cond C,A`` runs both per track; ``--out`` may hold
    ``{cond}``. With ``--group-s 0`` it is run_batch.py --runner (one CPU-lock slice a track); with ``--group-s S``
    consecutive tracks (and conditions) share one slice of about S s of expected run time (the lock is contended:
    each acquisition waited 4-6 minutes on 19 Oct), each track still its own process and result, resumable, then
    score.py per results dir."""
    ap = argparse.ArgumentParser(prog="madmom_dbn.py batch")
    ap.add_argument("--cond", required=True, help="baseline.py's condition letters, comma-separated (A clean, C room)")
    ap.add_argument("--subset", type=pathlib.Path, default=pathlib.Path("~/.cache/screen-machine/beat-eval/dev-subset.json"))
    ap.add_argument("--out", required=True, help="results dir; '{cond}' in it is replaced by the condition")
    ap.add_argument("--datasets", default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dur-cap", type=float, default=260.0)
    ap.add_argument("--group-s", type=float, default=0.0, help="expected run seconds per CPU-lock slice (0: per track)")
    ap.add_argument("--cost", type=float, default=0.55, help="expected run s per audio s (--record-all: ~0.55)")
    cut = argv.index("--") if "--" in argv else len(argv)
    a = ap.parse_args(argv[:cut])
    extra = argv[cut + 1:]
    sys.path.insert(0, str(BEAT_EVAL))
    import fcntl
    import subprocess
    import baseline                                             # noqa: E402  (stdlib + run_batch)
    import run_batch                                            # noqa: E402
    conds = [c.strip() for c in a.cond.split(",") if c.strip()]
    specs = baseline.conditions()
    outs = {c: pathlib.Path(a.out.replace("{cond}", c)).expanduser() for c in conds}
    rt_py = pathlib.Path("~/.venvs/sm-roundtrip/bin/python").expanduser()
    if a.group_s <= 0:
        rc = 0
        for c in conds:
            cmd = [str(rt_py), str(BEAT_EVAL / "run_batch.py"), str(a.subset.expanduser()), "--seed", str(a.seed),
                   "--track-seeds", "--out", str(outs[c]), "--dur-s", f"{a.dur_cap:g}", "--runner",
                   str(pathlib.Path(__file__).resolve()), *specs[c]["args"]]
            if a.datasets:
                cmd += ["--datasets", a.datasets]
            cmd += ["--", *extra]
            _batch_meta(outs[c], c, specs[c], cmd)
            print(" ".join(cmd), flush=True)
            rc = rc or subprocess.run(cmd, cwd=str(REPO)).returncode
        return rc
    _, entries = run_batch.load_index(a.subset.expanduser())
    entries = [e for e in entries if not e["duplicate"]]
    if a.datasets:
        entries = [e for e in entries if e["dataset"] in a.datasets.split(",")]
    jobs = []
    for e in entries:
        for c in conds:
            res = outs[c] / f"{run_batch.safe(e['dataset'])}__{run_batch.safe(e['id'])}.json"
            if res.exists() or not e["audio"] or not pathlib.Path(e["audio"]).exists():
                continue
            seed = run_batch.track_seed(a.seed, e)
            cmd = ["nice", "-n", "19", str(run_batch.SHOW_PY), str(pathlib.Path(__file__).resolve()), "run",
                   str(e["audio"]), "--out", str(res), "--id", e["id"], "--dataset", e["dataset"], "--genre",
                   e["genre"], "--seed", str(seed), "--dur-s", f"{a.dur_cap:g}", *specs[c]["args"]]
            if e["beats"] and pathlib.Path(e["beats"]).exists():
                cmd += ["--beats", str(e["beats"])]
            cmd += extra
            pre = 30.0 if "--degrade" in specs[c]["args"] else 0.0
            est = (min(float(e.get("duration_s") or 30.0), a.dur_cap) + pre) * a.cost
            jobs.append((c, e, res, seed, cmd, est))
    for c in conds:
        _batch_meta(outs[c], c, specs[c], ["madmom_dbn.py batch", *argv])
    print(f"{len(jobs)} runs to do ({', '.join(conds)}), slices of ~{a.group_s:g} s", flush=True)
    i = 0
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    while i < len(jobs):
        group, tot = [], 0.0
        while i < len(jobs) and (not group or tot + jobs[i][5] <= a.group_s):
            group.append(jobs[i])
            tot += jobs[i][5]
            i += 1
        t_wait = time.time()
        with open(run_batch.LOCK, "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            waited = time.time() - t_wait
            for c, e, res, seed, cmd, est in group:
                t0 = time.time()
                p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, env=env)
                t1 = time.time()
                rec = {"id": e["id"], "dataset": e["dataset"], "genre": e["genre"], "seed": seed, "rc": p.returncode,
                       "wall_s": round(t1 - t0, 1), "lock_wait_s": round(waited, 1), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                waited = 0.0
                if p.returncode == 0 and res.exists():
                    r = json.loads(res.read_text())
                    run_s = t1 - float(r.get("t_start_unix") or t0)
                    rec.update(audio_s=r.get("audio_s"), run_s=round(run_s, 1),
                               cost=round(run_s / max(1e-9, float(r.get("audio_s") or 0)), 3), rt=r.get("rt"),
                               stack=(r.get("stack") or {}).get("id"))
                else:
                    rec["stderr"] = p.stderr[-600:]
                with (outs[c] / "batch.jsonl").open("a") as f:
                    f.write(json.dumps(rec) + "\n")
                said = [x.strip() for x in p.stdout.splitlines() if x.strip().startswith(("end:", "vs GT"))]
                print(f"[{c}] {e['dataset']}/{e['id']}: rc {p.returncode}, {rec.get('run_s', rec['wall_s'])} s run, "
                      f"{rec['lock_wait_s']} s lock wait" + "".join(f"\n    {x}" for x in said), flush=True)
    for c in conds:
        subprocess.run([str(rt_py), str(BEAT_EVAL / "score.py"), str(outs[c]), "--tracks", "off"], cwd=str(REPO),
                       env=env, capture_output=True)
    return 0


def _batch_meta(out: pathlib.Path, c: str, spec: dict, cmd: list) -> None:
    out.mkdir(parents=True, exist_ok=True)
    meta = out / "candidate.json"
    if not meta.exists():
        meta.write_text(json.dumps({"condition": c, **spec, "cmd": cmd}, indent=1))


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmds = {"run": cmd_run, "replay": cmd_replay, "selftest": cmd_selftest, "batch": cmd_batch}
    if argv and argv[0] in cmds:
        return cmds[argv[0]](argv[1:])
    if argv and not argv[0].startswith("-") and pathlib.Path(argv[0]).exists():
        return cmd_run(argv)                                    # run_batch.py --runner: AUDIO first
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
