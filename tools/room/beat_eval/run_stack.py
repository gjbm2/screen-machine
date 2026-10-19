#!/usr/bin/env python3
"""Offline replay of show_service's live beat stack on one audio file: what the room's lights would have flashed.

The room audio calibration (tools/room/audio_cal) measures how the room, the PA and the mic degrade music; this
replays annotated music, clean or through that room model, through our CURRENT beat stack, so score.py can say
whether the clock still finds the beats. Run it with show_service's own env, so it imports show_service exactly as
deployed (nothing here re-implements the stack):

  ~/.venvs/sm-show/bin/python tools/room/beat_eval/run_stack.py AUDIO [--beats GT.beats] [--out OUT.json]
      [--degrade tools/room/audio_cal/runs/<run>/model/model.json [--snr-db N] [--seed N]] [--no-slow-lane]

Heavy: wrap it as ``flock ~/.cache/screen-machine/cpu.lock nice -n 19 ...`` (run_batch.py does, one track at a time).

The wiring, as show_service/__main__.py does it live
---------------------------------------------------
- **Input.** ffmpeg decodes AUDIO at 48 kHz (its own channels). ``--degrade`` first runs the room model
  (tools/room/audio_cal/roomsim.py ``apply``: mono 48 kHz as the mic chain delivers it); without it the channels are
  averaged, as a mono mic would. Then INT16 steps through vban.encode_pcm/decode_pcm (the VBAN wire), and 128-frame
  blocks (the senders' packet). ``--preroll-s`` puts room tone (digital silence, or the room model's noise with
  ``--degrade``) before the music, as a mic that has been open a while hears: dsp's gate learns the input's noise
  floor from its first 30 s, and the slow lane needs 30 s of the segment.
- **Time.** Stream sample n sounds at host time T0 + n / 48000 (the receiver's t0_host_s, acoustic). Block j arrives
  at its last sample's time + ``--rx-latency-ms`` (the mic-to-receiver delay), and ``on_tick`` runs every TICK_S
  (10 ms) of host time in between, so a body published at host time h knows only audio up to h - latency, as live.
- **Per block** (``Show.on_audio``): ``Features.process``; then ``Clock.set_level(features.latest().gate)`` and
  ``Clock.process`` with the same block. Features' ``music.hit`` bodies go to ``Clock.observe_hit`` (``Show.
  _features_publish``); ``Features.clock_fn = Clock.snapshot``; ``Features.gate_ref_lufs`` is the vibe's
  loudness_ref_lufs (``--gate-ref-lufs``, the dsp default -30 when no vibe sets one).
- **Per tick** (``Show.on_tick``): ``Features.tick`` then ``Clock.tick``.
- **Packet loss** (``--loss-pct``, default 0; ``--loss-burst`` packets per hole on average): lost packets become what
  the receiver makes of a hole up to 250 ms, one zero block flagged AUDIO_GAP and the next real block AUDIO_RESUME
  (delivered with it), and the slow lane skips a window holding a gap, as the worker does (its turn is used up).
- **The clock** runs madmom inline by default. Live it runs on show-beat (deploy/sm-show.env ``SHOW_BEAT_THREAD=on``,
  18 Oct); the frames are the same either way (clock.py, test_clock_thread.py), and on house120 the flashed beats
  were identical to the microsecond, while the thread hand-offs cost a quarter more wall time. ``--beat-thread on``
  runs show-beat, with ``wait_beat()`` after each block (an unpaced feed). Live, show-beat's results can drain up to
  ~150 ms late under load (p95 18 Oct); here they drain at once (inline) or at the next block or tick.
- **The slow lane** (unless ``--no-slow-lane``): the ML worker's ``beats`` task (Beat This!, ONNX) as it runs live
  (show.yaml ``ml.tasks.beats``): a turn every ``every_s`` (10 s) on the worker's grid (its phase is random, from
  ``--seed``, or ``--slow-phase-s``), on the newest ``window_s`` (30 s) of the segment that has arrived by then
  (MLFeed's 50 ms frames), none until the segment holds 30 s, none on a silent window (``ml.silence_dbfs``). The
  pre-pass (slow_lane.py, in ~/.venvs/sm-show-ml) computes each line; each reaches ``MLLink._on_line`` (so
  ``beat_vote`` and the apply rule: agree_runs, cooldown, then ``Clock.apply_integer_correction``) at the turn's
  time + ``--slow-cost-s`` (3.7 s: the live worker's p50 under the 4K render, 18 Oct; ``measured`` uses this
  run's own cost, and drops a run over the task's budget as the worker does).

Output (JSON; every time in seconds from the track's first sample, on the original's timeline)
---------------------------------------------------------------------------------------------
- ``bodies``: every published music.clock body whose line or labels changed (a 10 Hz heartbeat that repeats the
  previous one is left out; ``--all-bodies`` keeps them, ``published`` counts them all), as rows of ``body_fields``: ``h`` the host time it was published (when it took effect),
  ``t`` its t_ms, ``anchor`` its anchor_ms, then bpm, beat_at_anchor, beats_per_bar, bar_anchor_beat, conf,
  bar_conf, state, gate, epoch, stability. beat(t) = beat_at_anchor + (t - anchor) x bpm / 60.
- ``flashes``: the integer-beat crossings of the published line, causal: each crossing comes from the body in force
  at that moment, and a crossing within REPHASE_SKIP (0.35) of a period after the last one is the same beat
  re-phased (artnet_out's rule). Rows of ``flash_fields`` (t, k, gate, conf, state, epoch, bpm). A consumer that
  needs the beat gate keeps only gate "beat"; the DMX swell keeps "dmx" too.
- ``features``: at 2 Hz, dsp's gate, short-term loudness and energy, and the clock's own AGC gain (dB) and quiet flag
  (why the clock went quiet, or heard the music over-gained, if it did).
- ``slow_lane``: each turn (time, window, arrival, the line's bpm and beat count, the vote and whether it applied).
- ``stats``: Clock.stats() at the end (seeds, octave changes, corrections ...), ``features_stats``, hits per band.
- ``wall_s`` per stage, ``audio_s``, and ``rt`` (the replay's wall time per second of audio); ``t_start_unix`` (the
  process's start, before its imports: run_batch.py's lock wait vs run time).
- ``stack``: git HEAD, the stack files that differ from it and a short hash of each (show_service is edited in place
  by other sessions; ``stack.id`` hashes the files only, so two results compare like with like when it matches).
With ``--degrade``: roomsim keeps the input's timeline (it cuts the IR's pre-samples and never applies the system
delay), so the annotations stay valid as they are. The delay a cross-correlation with the original finds is still
reported (``degrade.delay_s_xcorr``, a check that it is about 0); ``--align-xcorr`` takes it out of the time base.
``--snr-db`` is meant over the music: roomsim measures it over its whole output, so with a pre-roll it is passed
down raised by 10 log10(music / (music + pre-roll)) (the pre-roll's room output is silence before its noise).
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional

T_START_UNIX = time.time()      # before the heavy imports: run_batch.py splits its wall time into lock wait and run
REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS"):  # one BLAS thread, as deploy/sm-show.env runs show_service
    os.environ.setdefault(_k, "1")

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from show_service import clock as clockmod  # noqa: E402
from show_service import contract, dsp, mllink, vban  # noqa: E402
from show_service.artnet_out import REPHASE_SKIP  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
AUDIO_CAL = REPO / "tools" / "room" / "audio_cal"
SHOW_YAML = REPO / "config" / "room" / "show.yaml"
SHOW_ENV = REPO / "deploy" / "sm-show.env"
ML_PYTHON = pathlib.Path("~/.venvs/sm-show-ml/bin/python").expanduser()
RT_PYTHON = pathlib.Path("~/.venvs/sm-roundtrip/bin/python").expanduser()
OUT_DIR = pathlib.Path("~/.cache/screen-machine/beat-eval/runs").expanduser()

FS = 48000
BLOCK = 128                     # the senders' packet (vban.frames_per_packet for mono INT16)
T0 = 1000.0                     # host seconds of stream sample 0 (any value; nothing reads the wall clock)
STREAM = "replay"
FS_ML = 16000
ML_FRAME = contract.ML_FRAME_SAMPLES     # MLFeed's frame (50 ms at 16 kHz): the worker's ring grows by these
SLOW_COST_S = 3.7               # the live beats task's p50 cost, 18 Oct (3.67 s; p95 3.81) under the 4K render
RX_LATENCY_MS = 30.0            # mic to show-rx: capture buffer (pc22-mic in_lat_ms 22) + network; a starting point
FEATURE_EVERY_S = 0.5
BODY_FIELDS = ("h", "t", "anchor", "bpm", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "conf", "bar_conf",
               "state", "gate", "epoch", "stability")
_LINE_KEYS = ("bpm", "anchor_ms", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "phrase_bars", "conf",
              "bar_conf", "state", "gate", "epoch")
FLASH_FIELDS = ("t", "k", "gate", "conf", "state", "epoch", "bpm")


# -- input -------------------------------------------------------------------------------------------------------------
def decode(path: pathlib.Path, start_s: float = 0.0, dur_s: Optional[float] = None) -> np.ndarray:
    """float32 (n, channels) at 48 kHz (1 or 2 channels) via ffmpeg."""
    ch = 1
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
        ch = max(1, min(2, int((p.stdout.strip().splitlines() or ["1"])[0])))
    except (ValueError, OSError, subprocess.SubprocessError):
        ch = 1
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start_s:
        cmd += ["-ss", f"{start_s:.3f}"]
    cmd += ["-i", str(path)]
    if dur_s:
        cmd += ["-t", f"{dur_s:.3f}"]
    cmd += ["-f", "f32le", "-ac", str(ch), "-ar", str(FS), "-"]
    p = subprocess.run(cmd, capture_output=True, timeout=600)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg could not decode {path}: {p.stderr.decode(errors='replace')[-400:]}")
    return np.frombuffer(p.stdout, "<f4").reshape(-1, ch).astype(np.float32)


def to_mono(x: np.ndarray) -> np.ndarray:
    return (x[:, 0] if x.shape[1] == 1 else x.mean(axis=1)).astype(np.float32)


def int16_wire(x: np.ndarray) -> np.ndarray:
    """The VBAN INT16 round trip, with the sender's and receiver's own codecs."""
    x = np.asarray(x, np.float32).reshape(-1, 1)
    return vban.decode_pcm(vban.encode_pcm(x, vban.INT16), vban.INT16, x.shape[0], 1).reshape(-1)


def degrade(x: np.ndarray, model: pathlib.Path, seed: int, snr_db: Optional[float]) -> np.ndarray:
    """tools/room/audio_cal/roomsim.py ``apply`` -> mono 48 kHz. In-process when it imports here; sm-show has no
    soundfile, so otherwise in ~/.venvs/sm-roundtrip through .npy files."""
    path = AUDIO_CAL / "roomsim.py"
    if not path.exists():
        raise SystemExit(f"--degrade: {path} isn't there yet (the analysis builder writes it)")
    if not model.exists():
        raise SystemExit(f"--degrade: {model} not found")
    try:
        spec = importlib.util.spec_from_file_location("roomsim", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                                   # type: ignore[union-attr]
        y, fs = mod.apply(x, FS, str(model), seed=seed, snr_db=snr_db)
    except ImportError:
        with tempfile.TemporaryDirectory(prefix="beat-eval-") as td:
            src, dst = pathlib.Path(td) / "x.npy", pathlib.Path(td) / "y.npy"
            np.save(src, x)
            code = ("import importlib.util as u, json, sys, numpy as np\n"
                    "s = u.spec_from_file_location('roomsim', sys.argv[1]); m = u.module_from_spec(s)\n"
                    "s.loader.exec_module(m)\n"
                    "y, fs = m.apply(np.load(sys.argv[2]), 48000, sys.argv[3], **json.loads(sys.argv[4]))\n"
                    "np.save(sys.argv[5], np.asarray(y, np.float32)); print(int(fs))\n")
            p = subprocess.run([str(RT_PYTHON), "-c", code, str(path), str(src), str(model),
                                json.dumps({"seed": seed, "snr_db": snr_db}), str(dst)],
                               capture_output=True, text=True)
            if p.returncode != 0:
                raise SystemExit(f"roomsim failed: {p.stderr[-800:]}")
            y, fs = np.load(dst), int(p.stdout.strip().splitlines()[-1])
    if int(fs) != FS:
        raise SystemExit(f"roomsim returned {fs} Hz, expected {FS}")
    y = np.asarray(y, np.float32)
    return y if y.ndim == 1 else to_mono(y)


def estimate_delay(ref: np.ndarray, y: np.ndarray, max_s: float = 60.0) -> float:
    """Seconds by which y lags ref (cross-correlation at 8 kHz over the first ``max_s``, lags -50..+500 ms)."""
    import soxr
    a = soxr.resample(ref[:int(max_s * FS)], FS, 8000)
    b = soxr.resample(y[:int(max_s * FS)], FS, 8000)
    n = min(len(a), len(b))
    if n < 8000:
        return 0.0
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    m = 1 << int(math.ceil(math.log2(2 * n)))
    c = np.fft.irfft(np.conj(np.fft.rfft(a, m)) * np.fft.rfft(b, m), m)
    lags = np.r_[np.arange(0, int(0.5 * 8000)), np.arange(-int(0.05 * 8000), 0)]
    i = int(np.argmax(np.abs(c[lags])))
    return float(lags[i]) / 8000.0


# -- settings ----------------------------------------------------------------------------------------------------------
def show_settings() -> dict:
    try:
        d = yaml.safe_load(SHOW_YAML.read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


STACK_FILES = ("show_service/clock.py", "show_service/beatdbn.py", "show_service/beatdbn_conf.json",
               "show_service/beatproc.py", "show_service/dsp.py", "show_service/mllink.py", "show_service/contract.py",
               "show_service/vban.py", "show_service/artnet_out.py", "show_service/ml/models.py",
               "show_service/ml/worker.py", "config/room/show.yaml")


def stack_fingerprint() -> dict:
    """Which stack this replay ran: git HEAD, whether the stack's files differ from it (other sessions edit
    show_service in place), and a short hash of each, so results from different days can be told apart."""
    import hashlib
    out: dict = {"files": {}}
    for f in STACK_FILES:
        try:
            out["files"][f] = hashlib.sha256((REPO / f).read_bytes()).hexdigest()[:12]
        except OSError:
            out["files"][f] = None
    try:
        out["head"] = subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], cwd=str(REPO), capture_output=True,
                                     text=True, timeout=10).stdout.strip() or None
        p = subprocess.run(["git", "status", "--porcelain", "--", *STACK_FILES], cwd=str(REPO), capture_output=True,
                           text=True, timeout=10)
        out["dirty"] = sorted(line[3:] for line in p.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError):
        out["head"], out["dirty"] = None, None
    # the id is the files' content only: a commit elsewhere in the repo doesn't change the stack
    out["id"] = "s" + hashlib.sha256(json.dumps(out["files"], sort_keys=True).encode()).hexdigest()[:10]
    return out


def beat_thread_default() -> bool:
    """deploy/sm-show.env's SHOW_BEAT_THREAD (what production runs)."""
    try:
        for line in SHOW_ENV.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SHOW_BEAT_THREAD":
                return v.strip().strip('"').lower() in ("on", "1", "true", "yes")
    except OSError:
        pass
    return False


# -- packet loss -------------------------------------------------------------------------------------------------------
def loss_mask(n48: int, pct: float, burst: float, seed: int) -> Optional[np.ndarray]:
    """Lost packets (a two-state Markov chain: ``pct`` % lost, ``burst`` packets per hole on average), never the
    first, and no hole past the receiver's 250 ms zero-fill (a longer one would be a new segment)."""
    if pct <= 0:
        return None
    n = -(-n48 // BLOCK)
    p = min(0.95, pct / 100.0)
    burst = max(1.0, burst)
    go_bad = p / (burst * (1.0 - p))
    rng = np.random.default_rng(seed)
    u = rng.random(n)
    lost = np.zeros(n, bool)
    bad, run = False, 0
    fill_max = int(0.25 * FS / BLOCK)
    for j in range(1, n):
        bad = (u[j] >= 1.0 / burst) if bad else (u[j] < go_bad)
        run = run + 1 if bad else 0
        if run > fill_max:
            bad, run = False, 0
        lost[j] = bad
    return lost


def unclean_16k(lost: Optional[np.ndarray]) -> list[tuple[int, int]]:
    """The holes as 16 kHz sample ranges (the ML feed's GAP frames)."""
    if lost is None or not lost.any():
        return []
    d = np.diff(np.r_[0, lost.astype(np.int8), 0])
    starts, ends = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
    k = FS_ML / FS * BLOCK
    return [(int(a * k), int(math.ceil(b * k))) for a, b in zip(starts, ends)]


# -- the slow lane's schedule and pre-pass -----------------------------------------------------------------------------
def slow_schedule(n48: int, beats_cfg: dict, phase_s: float, rx_latency_s: float) -> list[dict]:
    """The worker's beats turns over a stream of ``n48`` samples: grid point g (stream seconds), the window [a, b) in
    16 kHz samples (the newest window_s that has arrived by g: whole MLFeed frames), or none (not 30 s in yet)."""
    every = float(beats_cfg.get("every_s", 10))
    need = int(round(float(beats_cfg.get("window_s", 30)) * FS_ML))
    n16 = int(n48 * FS_ML / FS)
    end_s = n48 / FS + rx_latency_s
    runs, k, g = [], 0, phase_s
    while g <= end_s + 1e-9:
        heard = (g - rx_latency_s) * FS_ML               # 16 kHz samples heard by g
        b = min(int(heard // ML_FRAME) * ML_FRAME, n16 // ML_FRAME * ML_FRAME)
        if b >= need and (not runs or b > runs[-1]["b"]):            # the segment holds 30 s; new audio since
            runs.append({"k": k, "g": g, "a": b - need, "b": b})
        k += 1
        g = phase_s + k * every
    return runs


def slow_prepass(y: np.ndarray, runs: list[dict], ml: dict, beats_cfg: dict) -> tuple[list[dict], dict]:
    if not runs:
        return [], {"load_s": 0.0}
    if not ML_PYTHON.exists():
        raise SystemExit(f"the slow lane needs {ML_PYTHON} (or pass --no-slow-lane)")
    with tempfile.TemporaryDirectory(prefix="beat-eval-") as td:
        audio, spec_p, out_p = (pathlib.Path(td) / n for n in ("y.npy", "spec.json", "out.json"))
        np.save(audio, y.astype(np.float32))
        spec = {"audio": str(audio), "t0": T0, "stream": STREAM,
                "models_dir": str(pathlib.Path(ml.get("models_dir") or "~/.cache/screen-machine/models").expanduser()),
                "model": beats_cfg.get("model", "beat-this-small0"), "window_s": float(beats_cfg.get("window_s", 30)),
                "interior_margin_s": float(beats_cfg.get("interior_margin_s", 3)),
                "silence_dbfs": float(ml.get("silence_dbfs", -70)), "runs": runs}
        spec_p.write_text(json.dumps(spec))
        env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", CUDA_VISIBLE_DEVICES="")
        p = subprocess.run([str(ML_PYTHON), str(HERE / "slow_lane.py"), str(spec_p), str(out_p)], cwd=str(REPO),
                           capture_output=True, text=True, env=env)
        if p.returncode != 0:
            raise SystemExit(f"slow_lane.py failed:\n{p.stderr[-1500:]}")
        res = json.loads(out_p.read_text())
    return res["runs"], {"load_s": res.get("load_s"), "model": res.get("model")}


# -- the replay --------------------------------------------------------------------------------------------------------
class Replay:
    """One stream through Features -> Clock (+ MLLink's beat votes), on simulated host time."""

    def __init__(self, *, beat_thread: bool, gate_ref_lufs: float, rx_latency_s: float, settings: dict,
                 all_bodies: bool = False) -> None:
        self.now = T0
        self.all_bodies = all_bodies
        self.bodies: list[tuple[float, dict]] = []
        self._last_key: Optional[tuple] = None
        self.n_published = 0
        self.hits = collections.Counter()
        self.feat_rows: list[list] = []
        self.errors = collections.Counter()
        self.rx_latency_s = rx_latency_s
        self.clock = clockmod.Clock(publish=self._clock_pub, beat_thread=beat_thread)
        self.features = dsp.Features(publish=self._features_pub)
        self.features.gate_ref_lufs = float(gate_ref_lufs)
        self.features.clock_fn = self.clock.snapshot
        ctx = contract.BrainContext(publish=lambda m: None, clock=self.clock.snapshot,
                                    clock_correct=self.clock.apply_integer_correction)
        self.link = mllink.MLLink(ctx, mono=lambda: self.now, log_path=pathlib.Path(os.devnull))
        self.link.configure(settings)                       # show.yaml's ml.beat_votes (and the worker keys, unsent)
        self.votes: list[tuple[float, dict]] = []

    # the publishes (show-rx)
    def _clock_pub(self, msg: dict) -> None:
        d = msg["data"]
        self.n_published += 1
        key = tuple(d.get(k) for k in _LINE_KEYS)
        if key != self._last_key or self.all_bodies:
            self._last_key = key
            self.bodies.append((self.now, d))

    def _features_pub(self, msg: dict) -> None:
        if msg.get("type") == contract.MUSIC_HIT:
            d = msg["data"]
            self.hits[d["band"]] += 1
            try:
                self.clock.observe_hit(d["t_ms"] / 1000.0, d["band"], d["strength"])
            except Exception:                                # noqa: BLE001  __main__'s guard
                self.errors["clock.observe_hit"] += 1

    def tick(self, h: float) -> None:
        self.now = h
        try:
            self.features.tick(h)
        except Exception:                                    # noqa: BLE001
            self.errors["features.tick"] += 1
        try:
            self.clock.tick(h)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.tick"] += 1

    def block(self, n0: int, blk: np.ndarray, flags: int, arrive: Optional[float] = None) -> None:
        t0 = T0 + n0 / FS
        self.now = t0 + len(blk) / FS + self.rx_latency_s if arrive is None else arrive
        pcm = blk.reshape(-1, 1)
        f = self.features
        try:
            f.process(STREAM, n0, t0, pcm, FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["features.process"] += 1
        try:
            lf = f.latest()
            self.clock.set_level(lf.get("gate") if isinstance(lf, dict) else None, t0)
            self.clock.process(STREAM, n0, t0, pcm, FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.process"] += 1
        self.clock.wait_beat()

    def ml_line(self, h: float, line: dict) -> None:
        self.now = h
        try:
            self.link._on_line(line)                         # beat_vote + the apply rule -> clock_correct
            self.votes.append((h, dict(self.link.view().get("vote") or {})))
        except Exception:                                    # noqa: BLE001
            self.errors["ml.vote"] += 1

    def sample_features(self) -> None:
        lf = self.features.latest()
        if isinstance(lf, dict):
            c = self.clock                                   # the clock's own AGC and quiet flag (read only)
            self.feat_rows.append([self.now, lf.get("gate"), lf.get("lufs_s"), lf.get("energy"),
                                   round(float(getattr(c, "_agc_db", math.nan)), 2), bool(getattr(c, "_quiet", False))])

    def run(self, y: np.ndarray, ml_events: list[tuple[float, dict]], lost: Optional[np.ndarray] = None) -> None:
        """Every block, tick and ML line in host-time order. ``lost[j]``: packet j never came (a hole)."""
        hole0: Optional[int] = None
        tick_i = 0
        next_tick = T0
        next_feat = T0
        ev = sorted(ml_events, key=lambda e: e[0])
        ei = 0
        n = len(y)
        for j, n0 in enumerate(range(0, n, BLOCK)):
            blk = y[n0:n0 + BLOCK]
            arrive = T0 + (n0 + len(blk)) / FS + self.rx_latency_s
            while True:
                t_ml = ev[ei][0] if ei < len(ev) else math.inf
                if next_tick <= arrive and next_tick <= t_ml:
                    self.tick(next_tick)
                    if next_tick >= next_feat:
                        self.sample_features()
                        next_feat += FEATURE_EVERY_S
                    tick_i += 1
                    next_tick = T0 + tick_i * contract.TICK_S
                elif t_ml <= arrive:
                    self.ml_line(t_ml, ev[ei][1])
                    ei += 1
                else:
                    break
            if lost is not None and lost[j]:
                hole0 = n0 if hole0 is None else hole0
                continue
            flags = contract.AUDIO_SEGMENT if j == 0 else 0
            if hole0 is not None:                            # the receiver's zero-fill, then the resume
                self.block(hole0, np.zeros(n0 - hole0, np.float32), contract.AUDIO_GAP, arrive=arrive)
                hole0, flags = None, contract.AUDIO_RESUME
            self.block(n0, blk, flags)
        end = T0 + n / FS + self.rx_latency_s
        while next_tick <= end:                              # the ticks up to the last block's arrival
            self.tick(next_tick)
            tick_i += 1
            next_tick = T0 + tick_i * contract.TICK_S
        self.now = end
        self.clock.stop()


def flashes(bodies: list[tuple[float, dict]], h_end: float) -> list[tuple]:
    """Integer-beat crossings of the published line, each from the body in force at that moment (module docstring)."""
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


# -- main --------------------------------------------------------------------------------------------------------------
def r6(v: Any) -> Any:
    return round(v, 6) if isinstance(v, float) else v


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", type=pathlib.Path)
    ap.add_argument("--beats", type=pathlib.Path, help="ground truth (<time_s>\\t<pos> lines): recorded in the output "
                                                      "for score.py, plus a quick hit-rate here")
    ap.add_argument("--out", type=pathlib.Path, help=f"result JSON (default {OUT_DIR}/<stem>.json)")
    ap.add_argument("--id", help="track id (default: the file's stem)")
    ap.add_argument("--dataset", default="")
    ap.add_argument("--genre", default="")
    ap.add_argument("--start-s", type=float, default=0.0, help="decode from here (the time base starts here)")
    ap.add_argument("--dur-s", type=float, default=None, help="decode at most this much")
    ap.add_argument("--gain-db", type=float, default=0.0, help="gain on the input before anything else")
    ap.add_argument("--degrade", type=pathlib.Path, help="a room model (tools/room/audio_cal/runs/<run>/model/model.json)")
    ap.add_argument("--snr-db", type=float, default=None, help="the room model's noise level (default: as measured)")
    ap.add_argument("--seed", type=int, default=0, help="the room model's noise and the slow lane's grid phase")
    ap.add_argument("--align-xcorr", action="store_true",
                    help="with --degrade: take the cross-correlation delay out of the times (roomsim keeps the timeline)")
    ap.add_argument("--preroll-s", type=float, default=None,
                    help="room tone before the music (default 30 with --degrade, else 0)")
    ap.add_argument("--rx-latency-ms", type=float, default=RX_LATENCY_MS)
    ap.add_argument("--loss-pct", type=float, default=0.0, help="packets lost on the way (%%; default 0)")
    ap.add_argument("--loss-burst", type=float, default=1.0, help="lost packets per hole on average (default 1)")
    ap.add_argument("--gate-ref-lufs", type=float, default=contract.GATE_REF_DEFAULT_LUFS)
    ap.add_argument("--beat-thread", choices=("on", "off", "env"), default="off",
                    help="madmom on show-beat (default off: the same frames, faster offline; env: deploy/sm-show.env)")
    ap.add_argument("--no-slow-lane", action="store_true", help="no Beat This! votes")
    ap.add_argument("--all-bodies", action="store_true", help="keep the 10 Hz heartbeats that repeat a body")
    ap.add_argument("--slow-cost-s", default=str(SLOW_COST_S),
                    help=f"a beats result's delay after its turn (default {SLOW_COST_S}; 'measured': this run's)")
    ap.add_argument("--slow-phase-s", type=float, default=None, help="the worker grid's phase (default: from --seed)")
    ap.add_argument("--slow-every-s", type=float, default=None,
                    help="the beats task's interval (default show.yaml's every_s, 10; the worker's CPU guard stretches "
                         "it x1.5 steps under load: 15 s live at 16:35 on 18 Oct)")
    a = ap.parse_args(argv)

    wall: dict[str, float] = {}
    t_all = time.perf_counter()
    tid = a.id or a.audio.stem
    settings = show_settings()
    ml = settings.get("ml") if isinstance(settings.get("ml"), dict) else {}
    beats_cfg = dict(((ml.get("tasks") or {}).get("beats") or {}))
    if a.slow_every_s:
        beats_cfg["every_s"] = a.slow_every_s
    beat_thread = beat_thread_default() if a.beat_thread == "env" else a.beat_thread == "on"

    t = time.perf_counter()
    x = decode(a.audio, a.start_s, a.dur_s)
    if a.gain_db:
        x = x * np.float32(10 ** (a.gain_db / 20))
    track_s = x.shape[0] / FS
    preroll_s = a.preroll_s if a.preroll_s is not None else (30.0 if a.degrade else 0.0)
    pre = int(round(preroll_s * FS))
    wall["decode"] = time.perf_counter() - t

    deg = None
    t = time.perf_counter()
    if a.degrade:
        xp = np.concatenate([np.zeros((pre, x.shape[1]), np.float32), x]) if pre else x
        snr = a.snr_db + 10 * math.log10(len(x) / len(xp)) if a.snr_db is not None else None
        y = degrade(xp, a.degrade, a.seed, snr)
        xc = estimate_delay(to_mono(xp)[pre:], y[pre:])
        delay = xc if a.align_xcorr else 0.0
        deg = {"model": str(a.degrade), "snr_db": a.snr_db, "snr_db_passed": snr, "seed": a.seed,
               "delay_s_xcorr": round(xc, 5), "delay_s": round(delay, 5)}
    else:
        y = to_mono(x)
        if pre:
            y = np.concatenate([np.zeros(pre, np.float32), y])
        delay = 0.0
    y = int16_wire(y)
    wall["degrade"] = time.perf_counter() - t
    zero = T0 + preroll_s + delay                        # host time of the original's first sample
    lost = loss_mask(len(y), a.loss_pct, a.loss_burst, a.seed + 31)
    rx_lat = a.rx_latency_ms / 1000.0

    # the slow lane: its turns, then the pre-pass
    slow_rows: list[dict] = []
    ml_events: list[tuple[float, dict]] = []
    slow_info: dict = {"on": not a.no_slow_lane}
    t = time.perf_counter()
    if not a.no_slow_lane:
        every = float(beats_cfg.get("every_s", 10))
        phase = (a.slow_phase_s if a.slow_phase_s is not None
                 else float(np.random.default_rng(a.seed + 7919).uniform(0, every)))
        runs = slow_schedule(len(y), beats_cfg, phase, rx_lat)
        holes = unclean_16k(lost)
        dirty = [r for r in runs if any(h0 < r["b"] and h1 > r["a"] for h0, h1 in holes)]
        for r in dirty:                                  # the worker waits for clean audio: no line this turn
            r["unclean"] = True
        clean = [r for r in runs if not r.get("unclean")]
        res_c, info = slow_prepass(y, clean, ml, beats_cfg)
        by_k = {r["k"]: o for r, o in zip(clean, res_c)}
        res = [by_k.get(r["k"], {"silent": None, "cost_ms": None, "line": None, "unclean": True}) for r in runs]
        measured = str(a.slow_cost_s).strip().lower() == "measured"
        budget_ms = float(beats_cfg.get("budget_ms", 5500))
        skip_next = False
        for r, out in zip(runs, res):
            row = {"k": r["k"], "g": T0 + r["g"], "a": r["a"], "b": r["b"], "silent": out["silent"],
                   "cost_ms": out["cost_ms"], "line": out["line"], "arrive": None,
                   "dropped": "a gap in the window" if r.get("unclean") else None}
            if out["line"] is not None:
                if measured and skip_next:                   # an overrun skips the task's next turn
                    row["dropped"], skip_next = "skipped after an overrun", False
                elif measured and out["cost_ms"] > budget_ms:
                    row["dropped"], skip_next = "over budget", True
                else:
                    cost_s = out["cost_ms"] / 1000.0 if measured else float(a.slow_cost_s)
                    row["arrive"] = T0 + r["g"] + cost_s
                    ml_events.append((row["arrive"], out["line"]))
            slow_rows.append(row)
        slow_info.update(info, phase_s=round(phase, 3), cost_s=a.slow_cost_s, every_s=every,
                         window_s=float(beats_cfg.get("window_s", 30)))
    wall["slow_prepass"] = time.perf_counter() - t

    t = time.perf_counter()
    rp = Replay(beat_thread=beat_thread, gate_ref_lufs=a.gate_ref_lufs, rx_latency_s=rx_lat, settings=settings,
                all_bodies=a.all_bodies)
    rp.run(y, ml_events, lost)
    wall["replay"] = time.perf_counter() - t
    h_end = rp.now
    fl = flashes(rp.bodies, h_end)

    def rel(v: float) -> float:
        return round(v - zero, 6)

    votes_at = {round(h, 6): v for h, v in rp.votes}
    for row in slow_rows:
        line = row.pop("line")
        row["window"] = [rel(line["window_t0"]), rel(line["window_t1"])] if line else None
        row["bt_bpm"] = round(line["bpm"], 3) if line and line.get("bpm") else None
        row["bt_beats"] = len(line["beats"]) if line else None
        row["vote"] = votes_at.get(round(row["arrive"], 6)) if row["arrive"] is not None else None
        if row["vote"]:
            row["vote"] = {k: (rel(v) if k in ("window_t0", "window_t1") else v) for k, v in row["vote"].items()}
        row["g"] = rel(row["g"])
        row["arrive"] = rel(row["arrive"]) if row["arrive"] is not None else None
    slow_info["applied"], slow_info["confirmed"] = rp.link.applied, rp.link.confirmed

    rows = [[rel(h), rel(b["t_ms"] / 1000.0), rel(b["anchor_ms"] / 1000.0) if b["bpm"] else None, r6(b["bpm"]),
             b["beat_at_anchor"], b["beats_per_bar"], b["bar_anchor_beat"], b["conf"], b["bar_conf"], b["state"],
             b["gate"], b["epoch"], b["stability"]] for h, b in rp.bodies]
    frows = [[rel(f[0]), *f[1:6], r6(f[6])] for f in fl]
    wall["total"] = time.perf_counter() - t_all
    audio_s = len(y) / FS
    result = {
        "version": 1, "id": tid, "dataset": a.dataset, "genre": a.genre,
        "audio": str(a.audio), "beats": str(a.beats) if a.beats else None,
        "start_s": a.start_s, "track_s": round(track_s, 4), "preroll_s": preroll_s,
        "options": {"rx_latency_ms": a.rx_latency_ms, "gate_ref_lufs": a.gate_ref_lufs, "beat_thread": beat_thread,
                    "loss_pct": a.loss_pct, "loss_burst": a.loss_burst,
                    "lost_packets": int(lost.sum()) if lost is not None else 0,
                    "gain_db": a.gain_db, "block": BLOCK, "tick_s": contract.TICK_S},
        "degrade": deg,
        "body_fields": list(BODY_FIELDS), "bodies": rows, "published": rp.n_published,
        "flash_fields": list(FLASH_FIELDS), "flashes": frows,
        "feature_fields": ["t", "gate", "lufs_s", "energy", "agc_db", "quiet"],
        "features": [[rel(r[0]), *(round(v, 4) if isinstance(v, float) else v for v in r[1:])] for r in rp.feat_rows],
        "slow_lane": {**slow_info, "runs": slow_rows},
        "stats": rp.clock.stats(), "features_stats": rp.features.stats(), "hits": dict(rp.hits),
        "errors": dict(rp.errors),
        "stack": stack_fingerprint(), "t_start_unix": round(T_START_UNIX, 3),
        "audio_s": round(audio_s, 3), "wall_s": {k: round(v, 3) for k, v in wall.items()},
        "rt": round(wall["replay"] / max(1e-9, audio_s), 4),
    }
    out = a.out or (OUT_DIR / f"{tid}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(result, separators=(",", ":"), default=float))
    tmp.replace(out)
    summary(result, a.beats)
    print(f"-> {out}")
    return 0


def read_beats(path: pathlib.Path) -> np.ndarray:
    ts = []
    for line in path.read_text().splitlines():
        parts = line.replace(",", " ").split()
        if parts:
            try:
                ts.append(float(parts[0]))
            except ValueError:
                continue
    return np.asarray(sorted(ts))


def summary(r: dict, beats: Optional[pathlib.Path]) -> None:
    fl = r["flashes"]
    gates = collections.Counter(f[2] for f in fl)
    last = r["bodies"][-1] if r["bodies"] else None
    first_beat = next((b[0] for b in r["bodies"] if b[10] == "beat"), None)
    st = r["stats"]
    sl = r["slow_lane"]
    print(f"{r['id']}: {r['track_s']:.1f} s (+{r['preroll_s']:g} s pre-roll), replay {r['wall_s']['replay']:.1f} s "
          f"(rt {r['rt']:.3f}), total {r['wall_s']['total']:.1f} s; slow pre-pass {r['wall_s']['slow_prepass']:.1f} s")
    if last:
        print(f"  end: {last[3]:.2f} bpm, {last[9]}, gate {last[10]}, conf {last[7]}; first gate=beat at "
              f"{first_beat if first_beat is None else round(first_beat, 2)} s; seeds {st.get('seeds', 0)}, "
              f"octave changes {st.get('octave_changes', 0)}, cold searches {st.get('cold_searches', 0)}")
    print(f"  flashes: {dict(gates)}; slow lane: {'off' if not sl['on'] else str(len(sl['runs'])) + ' turns, ' + str(sl.get('applied', 0)) + ' applied, ' + str(sl.get('confirmed', 0)) + ' confirmed'}")
    if r["errors"]:
        print(f"  ERRORS: {r['errors']}")
    if beats and beats.exists():
        gt = read_beats(beats)
        gt = gt[(gt >= 0) & (gt <= r["track_s"])]
        ft = np.array([f[0] for f in fl])
        fg = np.array([f[0] for f in fl if f[2] == "beat"])
        def hit(ref, est, tol):
            if not len(ref) or not len(est):
                return 0.0
            i = np.clip(np.searchsorted(est, ref), 1, len(est) - 1) if len(est) > 1 else np.zeros(len(ref), int)
            d = np.minimum(np.abs(est[i] - ref), np.abs(est[np.maximum(i - 1, 0)] - ref))
            return float(np.mean(d <= tol))
        print(f"  vs GT ({len(gt)} beats): any flash within 70 ms {hit(gt, ft, 0.07):.2f}, 25 ms {hit(gt, ft, 0.025):.2f}; "
              f"gated within 70 ms {hit(gt, fg, 0.07):.2f}")


if __name__ == "__main__":
    raise SystemExit(main())
