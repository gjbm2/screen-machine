#!/usr/bin/env python3
"""Shared plumbing for candidate beat trackers that are NOT show_service's Clock (alt/beatnet_stack.py first): the
replay's input exactly as run_stack.py makes it, dsp's loudness gate, and a published clock line + beat gate made the
way Clock makes them, so a candidate only has to supply an estimator (a beat time, a period) and a confidence.

Why: score.py judges run_stack.py's JSON (published bodies, causal flashes with their gate and conf). A candidate
with its own estimator must hear the same audio (same decode, room model draw, pre-roll, VBAN INT16 wire, block
timing) and publish the same kind of line, or its numbers don't compare with the baseline's. Import it from a
candidate's runner (any venv that can import show_service: numpy, scipy, pyebur128, pyyaml):

- ``add_stack_args(ap)``: run_stack.py's command line (run_batch.py passes these to every runner).
- ``prepare(a)``: decode -> gain -> pre-roll -> room model (through ~/.venvs/sm-roundtrip, the very path the
  baseline's replays took: sm-show has no soundfile) -> mono -> INT16 wire -> packet-loss mask. Returns the stream
  at 48 kHz and its timeline (``zero``: host time of the original's first sample).
- ``dsp_trace(...)``: dsp.Features over the received blocks with run_stack's block/tick timing; per block the gate
  Clock.set_level would get, and run_stack's 2 Hz feature rows.
- ``Quiet``: Clock.set_level's quiet rule (under 0.02 for 3 s, until back over 0.05).
- ``LineClock``: the published line and gate (clock.py section 6, "_update_published" and "_update_gate"):
  while the gate is "beat" phase errors are slewed as a temporary tempo change (half the error per beat, at most
  4 %), below it the line jumps to the estimator, the count never jumps by more than half a beat (``epoch`` counts
  jumps), the anchor is an integer beat near now; gate hysteresis beat 0.70/0.60, dmx 0.45/0.35, 2-bar dwell, none
  at once while searching or quiet. Bodies are kept as run_stack keeps them (a heartbeat that repeats the previous
  line and labels is left out).
- ``result(...)``: run_stack.py's JSON (run_stack.flashes, the same fields), plus ``candidate`` and ``cpu``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys
from typing import Any, Callable, Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import numpy as np  # noqa: E402

import run_stack  # noqa: E402  (imports show_service as deployed)
from run_stack import BLOCK, FS, T0  # noqa: E402
from show_service import clock as clockmod  # noqa: E402
from show_service import contract, dsp  # noqa: E402

BEAT_ON, BEAT_OFF = clockmod.BEAT_ON, clockmod.BEAT_OFF
DMX_ON, DMX_OFF = clockmod.DMX_ON, clockmod.DMX_OFF
DWELL_BARS = clockmod.DWELL_BARS
SLEW_BEATS, SLEW_MAX = clockmod.SLEW_BEATS, clockmod.SLEW_MAX
QUIET_GATE_OFF, QUIET_GATE_ON, QUIET_AFTER_S = clockmod.QUIET_GATE_OFF, clockmod.QUIET_GATE_ON, clockmod.QUIET_AFTER_S
GATE_RANK = {"none": 0, "dmx": 1, "beat": 2}
LINE_KEYS = ("bpm", "anchor_ms", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "conf", "state", "gate", "epoch")


# -- the command line --------------------------------------------------------------------------------------------------
def add_stack_args(ap) -> None:
    """run_stack.py's options (the ones a candidate honours; the slow-lane ones are taken and ignored)."""
    ap.add_argument("audio", type=pathlib.Path)
    ap.add_argument("--beats", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    ap.add_argument("--id")
    ap.add_argument("--dataset", default="")
    ap.add_argument("--genre", default="")
    ap.add_argument("--start-s", type=float, default=0.0)
    ap.add_argument("--dur-s", type=float, default=None)
    ap.add_argument("--gain-db", type=float, default=0.0)
    ap.add_argument("--degrade", type=pathlib.Path)
    ap.add_argument("--snr-db", type=float, default=None)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--align-xcorr", action="store_true")
    ap.add_argument("--preroll-s", type=float, default=None)
    ap.add_argument("--rx-latency-ms", type=float, default=run_stack.RX_LATENCY_MS)
    ap.add_argument("--loss-pct", type=float, default=0.0)
    ap.add_argument("--loss-burst", type=float, default=1.0)
    ap.add_argument("--gate-ref-lufs", type=float, default=contract.GATE_REF_DEFAULT_LUFS)
    ap.add_argument("--all-bodies", action="store_true")
    # taken for run_batch.py compatibility; a candidate has no slow lane and no show-beat thread
    ap.add_argument("--no-slow-lane", action="store_true")
    ap.add_argument("--beat-thread", default="off")
    ap.add_argument("--slow-cost-s", default=None)
    ap.add_argument("--slow-phase-s", type=float, default=None)
    ap.add_argument("--slow-every-s", type=float, default=None)


def input_key(a) -> dict:
    """Everything that changes the stream (and dsp's gate) a candidate hears: part of a candidate's cache key."""
    deg = None
    if a.degrade:
        p = pathlib.Path(a.degrade).resolve()
        deg = {"model": str(p), "sha": hashlib.sha256(p.read_bytes()).hexdigest()[:12]}
    return {"audio": str(pathlib.Path(a.audio).resolve()), "start_s": a.start_s, "dur_s": a.dur_s,
            "gain_db": a.gain_db, "degrade": deg, "snr_db": a.snr_db, "seed": a.seed if a.degrade or a.loss_pct else 0,
            "align_xcorr": bool(a.align_xcorr), "preroll_s": a.preroll_s, "rx_latency_ms": a.rx_latency_ms,
            "loss_pct": a.loss_pct, "loss_burst": a.loss_burst, "gate_ref_lufs": a.gate_ref_lufs,
            "roomsim_sha": hashlib.sha256((run_stack.AUDIO_CAL / "roomsim.py").read_bytes()).hexdigest()[:12]}


# -- the input, as run_stack.main makes it -----------------------------------------------------------------------------
def _degrade_like_baseline(x: np.ndarray, model: pathlib.Path, seed: int, snr: Optional[float]) -> np.ndarray:
    """run_stack.degrade through its sm-roundtrip fallback (the path the baseline took, since sm-show has no
    soundfile): with soundfile hidden here, the in-process import fails the same way."""
    saved = sys.modules.get("soundfile", None)
    had = "soundfile" in sys.modules
    sys.modules["soundfile"] = None                       # type: ignore[assignment]
    try:
        return run_stack.degrade(x, model, seed, snr)
    finally:
        if had:
            sys.modules["soundfile"] = saved              # type: ignore[assignment]
        else:
            sys.modules.pop("soundfile", None)


def prepare(a) -> dict:
    x = run_stack.decode(a.audio, a.start_s, a.dur_s)
    if a.gain_db:
        x = x * np.float32(10 ** (a.gain_db / 20))
    track_s = x.shape[0] / FS
    preroll_s = a.preroll_s if a.preroll_s is not None else (30.0 if a.degrade else 0.0)
    pre = int(round(preroll_s * FS))
    deg = None
    if a.degrade:
        xp = np.concatenate([np.zeros((pre, x.shape[1]), np.float32), x]) if pre else x
        snr = a.snr_db + 10 * math.log10(len(x) / len(xp)) if a.snr_db is not None else None
        y = _degrade_like_baseline(xp, a.degrade, a.seed, snr)
        xc = run_stack.estimate_delay(run_stack.to_mono(xp)[pre:], y[pre:])
        delay = xc if a.align_xcorr else 0.0
        deg = {"model": str(a.degrade), "snr_db": a.snr_db, "snr_db_passed": snr, "seed": a.seed,
               "delay_s_xcorr": round(xc, 5), "delay_s": round(delay, 5)}
    else:
        y = run_stack.to_mono(x)
        if pre:
            y = np.concatenate([np.zeros(pre, np.float32), y])
        delay = 0.0
    y = run_stack.int16_wire(y)
    lost = run_stack.loss_mask(len(y), a.loss_pct, a.loss_burst, a.seed + 31)
    return {"y": y, "track_s": track_s, "preroll_s": preroll_s, "zero": T0 + preroll_s + delay, "degrade": deg,
            "lost": lost}


# run_stack options that change the stream and dsp's gate: alt/clockvar_replay.py's cache key (read-only reuse)
_CV_CACHE = pathlib.Path("~/.cache/screen-machine/beat-eval/clockvar-cache").expanduser()
_CV_KEY_OPTS = {"--beats", "--id", "--dataset", "--start-s", "--dur-s", "--gain-db", "--degrade", "--snr-db", "--seed",
                "--align-xcorr", "--preroll-s", "--rx-latency-ms", "--loss-pct", "--loss-burst", "--gate-ref-lufs"}
_CV_FLAGS = {"--align-xcorr", "--no-slow-lane", "--all-bodies"}


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def from_clockvar_cache(argv: list[str], a) -> Optional[tuple[dict, np.ndarray, list]]:
    """(prepare()'s dict, dsp's gate per block, the 2 Hz feature rows) from alt/clockvar_replay.py's recording of the same run_stack argv,
    if there is one made by the same stack (its stream is run_stack's own INT16 wire and its gates the values
    Clock.set_level got: what prepare() + dsp_trace() compute, so this only saves the CPU). None otherwise."""
    opts: dict = {"audio": None}
    i = 0
    while i < len(argv):
        x = argv[i]
        if x.startswith("--"):
            k, _, v = x.partition("=")
            if k in _CV_FLAGS:
                val, i = True, i + 1
            elif v:
                val, i = v, i + 1
            else:
                val, i = (argv[i + 1] if i + 1 < len(argv) else None), i + 2
            if k in _CV_KEY_OPTS:
                opts[k] = str(pathlib.Path(val).resolve()) if k in ("--degrade", "--beats") else val
        else:
            if opts["audio"] is None:
                opts["audio"] = str(pathlib.Path(x).resolve())
            i += 1
    if opts["audio"] is None:
        return None
    key = hashlib.sha256(json.dumps(opts, sort_keys=True).encode()).hexdigest()[:12]
    tid = opts.get("--id") or pathlib.Path(opts["audio"]).stem
    p = _CV_CACHE / f"{_safe(opts.get('--dataset') or '-')}__{_safe(tid)}" / f"{key}.npz"
    if not p.exists():
        return None
    try:
        z = np.load(p)
        meta = json.loads(str(z["meta"]))
        stack = (meta.get("stack") or {}).get("files") or {}
        now = _fingerprint().get("files") or {}
        if any(stack.get(f) != now.get(f) for f in ("show_service/dsp.py", "show_service/vban.py",
                                                    "show_service/contract.py")):
            return None                                   # dsp (the gate) or the wire changed since it was recorded
        y = (z["y"].astype(np.float64) / 32768.0).astype(np.float32)
        gates = z["gates"].astype(np.float32)
        feat = z["feat"]
        rows = [[float(r[0])] + [None if (r[4] < 0.5 or not math.isfinite(v)) else float(v) for v in r[1:4]]
                for r in feat]
    except (OSError, ValueError, KeyError, IndexError):
        return None
    track_s = int(meta["x_shape"][0]) / FS
    preroll_s = a.preroll_s if a.preroll_s is not None else (30.0 if a.degrade else 0.0)
    deg = None
    delay = 0.0
    if a.degrade:
        xc = float(meta.get("xc") or 0.0)
        n_x = int(meta["x_shape"][0])
        pre = int(round(preroll_s * FS))
        snr = a.snr_db + 10 * math.log10(n_x / (n_x + pre)) if a.snr_db is not None else None
        delay = xc if a.align_xcorr else 0.0
        deg = {"model": str(a.degrade), "snr_db": a.snr_db, "snr_db_passed": snr, "seed": a.seed,
               "delay_s_xcorr": round(xc, 5), "delay_s": round(delay, 5)}
    lost = run_stack.loss_mask(len(y), a.loss_pct, a.loss_burst, a.seed + 31)
    nb = -(-len(y) // BLOCK)
    if len(gates) != nb and not (lost is not None and lost.any()):
        return None
    return ({"y": y, "track_s": track_s, "preroll_s": preroll_s, "zero": T0 + preroll_s + delay, "degrade": deg,
             "lost": lost, "from": str(p)}, gates, rows)


def received(y: np.ndarray, lost: Optional[np.ndarray], rx_latency_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """What show-rx hands on: the stream with each lost packet zero-filled, each 128-frame block's arrival (host s;
    a hole's zero-fill arrives with the next real block) and its flags."""
    n = len(y)
    nb = -(-n // BLOCK)
    ends = np.minimum((np.arange(nb) + 1) * BLOCK, n)
    arrive = T0 + ends / FS + rx_latency_s
    flags = np.zeros(nb, np.int32)
    flags[0] = contract.AUDIO_SEGMENT
    yr = y
    if lost is not None and lost.any():
        yr = y.copy()
        for j in np.nonzero(lost)[0]:
            yr[j * BLOCK:(j + 1) * BLOCK] = 0.0
            flags[j] = contract.AUDIO_GAP
        nxt_arr = None
        for j in range(nb - 1, -1, -1):                   # a hole's zero-fill arrives with the next real block
            if lost[j]:
                if nxt_arr is not None:
                    arrive[j] = nxt_arr
            else:
                nxt_arr = arrive[j]
        for j in range(1, nb):
            if not lost[j] and lost[j - 1]:
                flags[j] |= contract.AUDIO_RESUME
    return yr, arrive, flags


def dsp_trace(yr: np.ndarray, arrive: np.ndarray, flags: np.ndarray, gate_ref_lufs: float,
              feature_every_s: float = 0.5) -> dict:
    """dsp.Features over the received blocks, ticked every TICK_S of host time in between, as run_stack.Replay
    does: per block the gate the clock's set_level gets (features.latest().gate after that block), and the 2 Hz
    rows [h, gate, lufs_s, energy] (host time)."""
    f = dsp.Features(publish=lambda m: None)
    f.gate_ref_lufs = float(gate_ref_lufs)
    f.clock_fn = lambda: None
    nb = len(arrive)
    gate = np.full(nb, np.nan, np.float32)
    rows: list[list] = []
    tick_i, next_tick, next_feat = 0, T0, T0
    for j in range(nb):
        while next_tick <= arrive[j]:
            f.tick(next_tick)
            if next_tick >= next_feat:
                lf = f.latest()
                if isinstance(lf, dict):
                    rows.append([next_tick, lf.get("gate"), lf.get("lufs_s"), lf.get("energy")])
                next_feat += feature_every_s
            tick_i += 1
            next_tick = T0 + tick_i * contract.TICK_S
        n0 = j * BLOCK
        blk = yr[n0:n0 + BLOCK]
        try:
            f.process(run_stack.STREAM, n0, T0 + n0 / FS, blk.reshape(-1, 1), FS, int(flags[j]))
        except Exception:                                   # noqa: BLE001  __main__'s guard
            pass
        lf = f.latest()
        g = lf.get("gate") if isinstance(lf, dict) else None
        gate[j] = np.nan if g is None else float(g)
    return {"gate": gate, "rows": rows, "stats": f.stats()}


class Quiet:
    """Clock.set_level's rule: quiet under QUIET_GATE_OFF for QUIET_AFTER_S, until back over QUIET_GATE_ON."""

    def __init__(self) -> None:
        self.quiet = False
        self._below: Optional[float] = None
        self.starts = self.ends = 0

    def level(self, g: float, t: float) -> Optional[str]:
        """'start'/'end' on a change, else None."""
        if g is None or not math.isfinite(g):
            return None
        if self.quiet:
            if g >= QUIET_GATE_ON:
                self.quiet, self._below = False, None
                self.ends += 1
                return "end"
            return None
        if g >= QUIET_GATE_OFF:
            self._below = None
            return None
        if self._below is None:
            self._below = t
        elif t - self._below >= QUIET_AFTER_S:
            self.quiet = True
            self.starts += 1
            return "start"
        return None


# -- the published line and the gate -----------------------------------------------------------------------------------
class LineClock:
    """Clock's published line, gate and bodies around a candidate's estimator. Times are host seconds on the
    acoustic timeline (as Clock's: a beat time is when it sounds)."""

    def __init__(self, bpb: int = 4, all_bodies: bool = False) -> None:
        self.bpb = bpb
        self.all_bodies = all_bodies
        self.pub_ok = False
        self.pub_k, self.pub_t, self.pub_bpm = 0, 0.0, 0.0
        self.gate, self.gate_t, self.gate_up = "none", -math.inf, False
        self.state = "searching"
        self.conf = 0.0
        self.epoch = 0
        self.est_t: Optional[float] = None               # an estimator beat time
        self.est_P = 0.5
        self.bodies: list[tuple[float, dict]] = []
        self._last_key: Optional[tuple] = None
        self.published = 0
        self.counts = {"jumps": 0, "slews": 0, "gate_changes": 0}

    # the lines
    def pub_beat(self, t: float) -> float:
        return self.pub_k + (t - self.pub_t) * self.pub_bpm / 60.0

    def est_beat(self, t: float) -> float:
        return (t - self.est_t) / self.est_P

    def _next_est(self, t: float) -> float:
        """The estimator's first beat after t."""
        return self.est_t + math.floor((t - self.est_t) / self.est_P + 1.0) * self.est_P

    def set_estimator(self, t_beat: float, period: float) -> None:
        self.est_t, self.est_P = float(t_beat), float(period)

    def jump(self, t: float) -> None:
        """The published line becomes the estimator's (anchored at its next beat), the count kept within half a
        beat."""
        if self.est_t is None:
            return
        tn = self._next_est(t)
        k = int(round(self.pub_beat(tn))) if self.pub_ok else 0
        self.pub_k, self.pub_t, self.pub_bpm = k, tn, 60.0 / self.est_P
        self.pub_ok = True
        self.epoch += 1
        self.counts["jumps"] += 1

    def update(self, t: float) -> None:
        """After a judged beat, at time t (now, as heard): slew (gate "beat") or jump to the estimator."""
        if self.est_t is None:
            return
        bpm_e = 60.0 / self.est_P
        if not self.pub_ok:
            self.jump(t)
            return
        pb, eb = self.pub_beat(t), self.est_beat(t)
        off = int(round(pb - eb))
        d = eb + off - pb
        if self.gate == "beat":
            if abs(d) < 4e-4 and abs(self.pub_bpm / bpm_e - 1.0) < 2e-4:
                tn = self._next_est(t)
                self.pub_k, self.pub_t, self.pub_bpm = int(round(self.pub_beat(tn))), tn, bpm_e
            else:
                bpm = bpm_e * (1.0 + min(SLEW_MAX, max(-SLEW_MAX, d / SLEW_BEATS)))
                ka = math.floor(pb)
                self.pub_k, self.pub_t, self.pub_bpm = ka, t - (pb - ka) * 60.0 / bpm, bpm
                self.counts["slews"] += 1
        else:
            tn = self._next_est(t)
            k = int(round(self.est_beat(tn))) + off
            self.pub_k, self.pub_t, self.pub_bpm = k, tn, bpm_e
            if abs(d) > 0.01:
                self.epoch += 1
                self.counts["jumps"] += 1

    # the gate
    def update_gate(self, t: float, quiet: bool) -> None:
        c, g = self.conf, self.gate
        if self.state == "searching" or quiet:
            new = "none"
        elif g == "none":
            new = "beat" if c >= BEAT_ON else ("dmx" if c >= DMX_ON else "none")
        elif g == "dmx":
            new = "beat" if c >= BEAT_ON else ("none" if c < DMX_OFF else "dmx")
        else:
            new = "beat" if c >= BEAT_OFF else ("dmx" if c >= DMX_OFF else "none")
        if new == g:
            return
        up = GATE_RANK[new] > GATE_RANK[g]
        period = 60.0 / self.pub_bpm if self.pub_ok and self.pub_bpm > 0 else self.est_P
        recent = t - self.gate_t < DWELL_BARS * self.bpb * period
        if recent and not (new == "none" and (self.state == "searching" or quiet)) and (not up or not self.gate_up):
            return
        self.gate, self.gate_t, self.gate_up = new, t, up
        self.counts["gate_changes"] += 1

    # the bodies
    def body(self, h: float) -> dict:
        ok = self.pub_ok
        return {"bpm": self.pub_bpm if ok else 0.0, "anchor_ms": self.pub_t * 1000.0 if ok else 0.0,
                "beat_at_anchor": self.pub_k if ok else 0, "beats_per_bar": self.bpb, "bar_anchor_beat": 0,
                "phrase_bars": 4, "conf": round(self.conf, 4), "bar_conf": 0.0, "phrase_conf": 0.0,
                "state": self.state, "gate": self.gate, "epoch": self.epoch, "stability": 0.0, "t_ms": h * 1000.0}

    def publish(self, h: float) -> None:
        d = self.body(h)
        self.published += 1
        key = tuple(d.get(k) for k in LINE_KEYS)
        if key != self._last_key or self.all_bodies:
            self._last_key = key
            self.bodies.append((h, d))


# -- the result --------------------------------------------------------------------------------------------------------
_FP: dict = {}


def _fingerprint() -> dict:
    """run_stack.stack_fingerprint, once per process (a sweep builds many results)."""
    if not _FP:
        _FP.update(run_stack.stack_fingerprint())
    return _FP


def file_sha(*paths: pathlib.Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(pathlib.Path(p).read_bytes())
    return h.hexdigest()[:10]


def result(a, prep: dict, lc: LineClock, h_end: float, *, candidate: dict, stack_id: str, stats: dict,
           feat_rows: list[list], features_stats: dict, wall: dict, cpu: dict, t_start_unix: float,
           extra: Optional[dict] = None) -> dict:
    zero = prep["zero"]

    def rel(v: float) -> float:
        return round(v - zero, 6)

    r6 = run_stack.r6
    fl = run_stack.flashes(lc.bodies, h_end)
    rows = [[rel(h), rel(b["t_ms"] / 1000.0), rel(b["anchor_ms"] / 1000.0) if b["bpm"] else None, r6(b["bpm"]),
             b["beat_at_anchor"], b["beats_per_bar"], b["bar_anchor_beat"], b["conf"], b["bar_conf"], b["state"],
             b["gate"], b["epoch"], b["stability"]] for h, b in lc.bodies]
    frows = [[rel(f[0]), *f[1:6], r6(f[6])] for f in fl]
    audio_s = len(prep["y"]) / FS
    st = dict(_fingerprint())
    st["base_id"] = st["id"]
    st["id"] = stack_id                                   # score.py's NOTE then tells candidates apart
    st["candidate"] = candidate.get("name")
    out = {
        "version": 1, "id": a.id or a.audio.stem, "dataset": a.dataset, "genre": a.genre,
        "audio": str(a.audio), "beats": str(a.beats) if a.beats else None,
        "start_s": a.start_s, "track_s": round(prep["track_s"], 4), "preroll_s": prep["preroll_s"],
        "options": {"rx_latency_ms": a.rx_latency_ms, "gate_ref_lufs": a.gate_ref_lufs, "beat_thread": False,
                    "loss_pct": a.loss_pct, "loss_burst": a.loss_burst,
                    "lost_packets": int(prep["lost"].sum()) if prep["lost"] is not None else 0,
                    "gain_db": a.gain_db, "block": BLOCK, "tick_s": contract.TICK_S},
        "degrade": prep["degrade"],
        "body_fields": list(run_stack.BODY_FIELDS), "bodies": rows, "published": lc.published,
        "flash_fields": list(run_stack.FLASH_FIELDS), "flashes": frows,
        "feature_fields": ["t", "gate", "lufs_s", "energy", "agc_db", "quiet"],
        "features": [[rel(r[0]), *(round(v, 4) if isinstance(v, float) else v for v in r[1:])] for r in feat_rows],
        "slow_lane": {"on": False, "runs": [], "applied": 0, "confirmed": 0},
        "stats": stats, "features_stats": features_stats, "hits": {}, "errors": {},
        "stack": st, "candidate": candidate, "cpu": cpu, "t_start_unix": round(t_start_unix, 3),
        "audio_s": round(audio_s, 3), "wall_s": {k: round(v, 3) for k, v in wall.items()},
        "rt": round(wall.get("replay", wall.get("total", 0.0)) / max(1e-9, audio_s), 4),
    }
    if extra:
        out.update(extra)
    return out


def write(out: pathlib.Path, res: dict) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(res, separators=(",", ":"), default=float))
    tmp.replace(out)
