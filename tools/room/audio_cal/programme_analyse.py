#!/usr/bin/env python3
"""After a Sonos calibration programme: how loud music is at the room's mic, how that grows with the Sonos volume, what
show_service's loudness gate should expect at party volume, and how the live beat clock did on each song.

calibrate.py sonos-run (screen-machine-26) plays a calibration programme (programme.py: programme.flac, manifest.json,
beats/<segment>.beats) through the Drawing Room Sonos at timed volumes, records the room's mic as show_service hears it
(rec.wav, through the levels feed; tapmap.jsonl ties its samples to show_service's clock) and logs the live beat clock
(bus.jsonl, state.jsonl). Given that run directory and the programme, this script works out:

  1. Alignment. analyse.py's ``align`` (band features in 6 s windows against the whole programme, a consensus line,
     then GCC-PHAT per 8 s window) maps programme time to recording time: the offset (the Sonos starting after the
     recorder, plus the acoustic path, which can't be told apart) and the drift (the Sonos' and the mic's sample
     clocks, in ppm). The repeated ladder excerpt can't place itself (four equal matches) and is placed by the line.
     The Sonos' position ticks (run.json sonos.t_start_mono, about +-0.5 s) are the cross-check, and the fallback
     when the alignment fails (then levels only: no beats are scored).
  2. Levels. show_service's own stack replayed offline on the whole recording (beat_eval/run_stack.py, no slow lane,
     the features kept): dsp's lufs_s, the 3 s loudness its gate reads. Per segment, the median of the lufs_s values
     whose 3 s lie inside its measured audio: the segment minus 3 s at each edge, a ladder rung's middle 12 s, a
     silence minus its first second, and never before the volume has settled (the cue as issued, run.json
     sonos.cues, + room_service's 4 s ramp). Beside it, the live lufs_s (bus music.features: dsp as it ran) and
     BS.1770 short-term loudness computed here (not floored at dsp's -70). Room tone: the silences.
  3. Ladder. The rungs' lufs_s against the Sonos volume (the room tone's power taken off), fitted as a line in volume
     and as a line in 20 log10(volume): Sonos' volume curve is neither exactly; the smaller residual wins. Predicted
     at party volume (70) and at the main volume (40, where the music section's own median is the cross-check), with
     an honest uncertainty: the fit's error at that volume (its residual, floored at LADDER_SE_FLOOR_DB, since four
     rungs can fit by luck) and half the two curves' disagreement, which grows with the extrapolation past the top
     rung. Cross-check: each rung's gain, recording over programme on the excerpt's louder 100 ms frames (the room's
     own intermittent sound, talk or steps, lifts a quiet rung's median level, and no room-tone median takes it off);
     half its rise's disagreement with the levels' is added to the uncertainty.
  4. Live clock, per segment. The beats our consumers flashed live: the integer-beat crossings of the published
     music.clock line from bus.jsonl (analyse.py's live_beats: each body predicts until the next, causal), with
     artnet_out's re-phase rule (a crossing within REPHASE_SKIP of a period after the last is that beat re-phased:
     what run_stack.py's flashes do). Host time -> recording sample by a lower-envelope line through tapmap.jsonl
     (``n_end`` against ``a``, the newest packet's arrival: the receiver's own sample-to-host map, show_service/
     timeline.py) and config/room/latency.json's acoustic offset for the stream; then -> programme time. Scored against
     the segment's annotated beats (in programme time; the alignment puts them where the mic heard them) with
     beat_eval/score.py's score_one: F, CMLt, AMLt, hit25, cover, precision while gated, wrong lock, lock time, bpm;
     plus the time to the first gate "beat" and the share of time gated. For the piano, the gated share and the false
     locks; for the silences, any gated flash. The offline replay's flashes are scored the same way (live vs offline
     parity) unless --fast.
  5. Gate values. music_lufs_test = the median lufs_s over the music's steady segments at the main volume (the test
     volume; a piano piece is there to test the clock, not as party music, and stays out); music_lufs_party =
     music_lufs_test + the ladder's rise from the test volume to party volume (anchored on the songs, not the one
     ladder excerpt; the ladder's own level at 70 is reported beside it). Pass 2 (four songs, no ladder) pools pass
     1's songs at the same volume and takes its ladder. What the gate then does is worked out too: show_service's
     offset (music_lufs + 30, clamped), and dsp's floor clamp (the reference never above the 2nd percentile of its
     100 ms levels over 10 minutes + 21 LU), from the recording's own 100 ms levels: the room alone (an evening's
     start) and ten minutes of songs at the test and party volumes. The live gate's
     reference is read back from music.features (gate = smoothstep(ref - 15, ref, lufs_s), inverted), and is what the
     offline replay runs at (--stack-gate auto), so the two clocks see the same gate.
  6. Report: analysis/programme_report.md (plain English for the owner), programme_report.json (every number),
     gate.json, figs/levels.png, clock_u.json (both clocks in programme time: pass 2's before/after re-scores pass 1's
     over the audio both played, e.g. Sweet Dreams' first 60 s of pass 1's 75).

Modes:
  --gate-test-only  after pass 1: prints {"music_lufs_test", "music_lufs_party", "volume_main", "ladder"} on stdout
                    and writes analysis/gate.json; never the config.
  --write-config    after pass 2: also sets config/room/audio_cal.json (or --audio-cal-file PATH) streams.<stream>
                    (run, measured_at, device, summary: music_lufs = the party prediction, ...), merged: every other
                    stream, and every other key of this stream's entry (e.g. a model), stays. A run without a usable
                    ladder takes pass 1's (--pass1, else the newest sibling run of a "pass1-*" programme with a
                    programme_report.json). Refused for a synthetic run's real config, and without a ladder.
  --fast            no offline replay: the levels come from the live bus's music.features (dsp's own lufs_s as it ran;
                    BS.1770 here when the bus has none), and there is no live vs offline parity.
Logs go to stderr; stdout carries only the JSON. From Python: ``analyse_programme(run_dir, programme_dir, ...)``.

Usage (the replay runs about 0.4 x real time on one core: wrap it in the CPU lock at nice 19, or pass --lock):
  flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-roundtrip/bin/python \\
      tools/room/audio_cal/programme_analyse.py <run_dir> \\
      --programme ~/.cache/screen-machine/audio-cal/programmes/<name> \\
      [--gate-test-only | --write-config [--audio-cal-file PATH]] [--fast] [--pass1 <run_dir>] [--stack-gate auto]
Test it without a real run: programme_synth.py (a synthetic programme and a fake run with known truth).
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_k, "1")      # the host renders 4K: one core, never a pool

import argparse
import datetime as dt
import json
import math
import pathlib
import re
import subprocess
import sys
import time

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyse as A  # noqa: E402
import roomsim  # noqa: E402

REPO = HERE.parents[2]
CONFIG_JSON = A.CONFIG_JSON
LATENCY_JSON = REPO / "config" / "room" / "latency.json"
DSP_PY = REPO / "show_service" / "dsp.py"
ARTNET_PY = REPO / "show_service" / "artnet_out.py"
CPU_LOCK = A.CPU_LOCK

FS = 48000
EPS = 1e-20
PARTY_VOLUME = 70             # the owner, 20 Oct: party is about Sonos volume 70
STREAM_DEFAULT = "room-mic"

# levels
ST_WIN_S = 3.0                # dsp's lufs_s: pyebur128 short-term loudness, the last 3 s
EDGE_S = 3.0                  # a music segment's edges left out of its level
LADDER_MID_S = 12.0           # a ladder rung's level: its middle 12 s
SILENCE_EDGE_S = 1.0          # a silence's first second (reverb, the fade) left out of the room tone
RAMP_S = 4.0                  # room_service ramps a volume change over 4 s while playing
SETTLE_S = 0.5
# ladder
LADDER_MIN_SNR_DB = 3.0       # a rung this close to the room tone says nothing about the music's level
LADDER_SE_FLOOR_DB = 0.3      # the fit's error at a volume is never taken as smaller (4 rungs can fit by luck)
# tapmap
TAP_BUCKET_S = 2.0            # the lower envelope: each 2 s bucket's earliest arrival (timeline.WINDOW_S)
TAP_STEP_S = 0.02             # bucket minima jumping this far, and staying: a new piece (a splice, a reconnect)
TAP_RECONNECT_S = 0.5         # the recorder's own clock jumping this far between frames: a reconnect
TAP_STALL_S = 30.0            # a step up then back down to the same line within this long: a stall, not a new map
TAP_SHORT_S = 20.0            # a piece shorter than this takes the longest piece's slope (the clocks' skew)


def _const(path: pathlib.Path, name: str, default: float) -> float:
    """A numeric module constant from show_service's source (read, not imported: this env lacks show_service's
    dependencies), so the analysis follows dsp and artnet_out when they change."""
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return default
    m = re.search(rf"^{name}(?:\s*,\s*\w+)*\s*=\s*([-+0-9.eE]+)", txt, re.M)
    try:
        return float(m.group(1)) if m else default
    except ValueError:
        return default


GATE_SPAN_LU = _const(DSP_PY, "GATE_SPAN_LU", 15.0)
FLOOR_ABOVE_LU = _const(DSP_PY, "FLOOR_ABOVE_LU", 6.0)
FLOOR_BIN_S = _const(DSP_PY, "FLOOR_BIN_S", 0.1)
FLOOR_PCT = _const(DSP_PY, "FLOOR_PCT", 2.0)
FLOOR_MIN_S = _const(DSP_PY, "FLOOR_MIN_S", 30.0)
FLOOR_WINDOW_S = _const(DSP_PY, "FLOOR_WINDOW_S", 600.0)
LUFS_FLOOR = _const(DSP_PY, "LUFS_FLOOR", -70.0)
DBFS_FLOOR = -120.0           # dsp.py's
GATE_REF_DEFAULT = -30.0      # contract.GATE_REF_DEFAULT_LUFS: dsp's own default reference
GATE_OFFSET_MIN, GATE_OFFSET_MAX = -40.0, 20.0       # show_service/config.py clamps a stream's gate offset to these
REPHASE_SKIP = _const(ARTNET_PY, "REPHASE_SKIP", 0.35)

FLASH_FIELDS = ("t", "k", "gate", "conf", "state", "epoch", "bpm")                     # run_stack.py's
BODY_FIELDS = ("h", "t", "anchor", "bpm", "beat_at_anchor", "beats_per_bar", "bar_anchor_beat", "conf", "bar_conf",
               "state", "gate", "epoch", "stability")
SCORE_KEYS = ("f", "cmlt", "amlt", "p", "f_gated", "hit25", "hit25_gated", "hit70_gated", "offset_ms",
              "offset_iqr_ms", "cover_beat", "cover_gated", "prec_beat", "prec_beat_dh", "wrong_lock", "t_lock",
              "t_lock_dh", "acc1", "acc2", "acc1_time", "est_bpm", "gt_bpm", "n_gt", "n_flash", "n_gated")


def log(s: str) -> None:
    print(s, file=sys.stderr, flush=True)


def _num(v):
    """A finite float, or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _med(v) -> float | None:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return float(np.median(v)) if len(v) else None


def _pow(db):
    return 10.0 ** (np.asarray(db, float) / 10.0)


def _dbp(p):
    return 10.0 * np.log10(np.maximum(np.asarray(p, float), EPS))


def smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, float) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def inv_smoothstep(g):
    """t with smoothstep(0, 1, t) = g (0 < g < 1)."""
    g = np.clip(np.asarray(g, float), 0.0, 1.0)
    return 0.5 - np.sin(np.arcsin(1.0 - 2.0 * g) / 3.0)


# ======================================================================================================================
# inputs
# ======================================================================================================================
def read_json(p: pathlib.Path) -> dict:
    try:
        v = json.loads(p.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def load_programme(pdir: pathlib.Path, SC) -> dict:
    """manifest.json, its segments (sorted, each with its annotated beats ``_gt`` in programme time, or None)."""
    man = json.loads((pdir / "manifest.json").read_text(encoding="utf-8"))
    segs = []
    for s in man.get("segments") or []:
        s = dict(s)
        s["t0"], s["t1"] = float(s["t0"]), float(s["t1"])
        gt = None
        if s.get("beats"):
            p = pdir / s["beats"]
            if p.exists():
                gt = SC.read_beats(p)
        s["_gt"] = gt
        segs.append(s)
    segs.sort(key=lambda s: s["t0"])
    return {"dir": pdir, "manifest": man, "segments": segs, "name": man.get("name", pdir.name),
            "wav": pdir / "programme-48k.wav", "volume_main": _num(man.get("volume_main"))}


def seg_label(s: dict) -> str:
    src = s.get("source") or {}
    bits = [s["id"]]
    if s.get("genre"):
        bits.append(str(s["genre"]))
    elif src.get("dataset"):
        bits.append(str(src["dataset"]))
    return " / ".join(bits)


def source_key(s: dict) -> str:
    src = s.get("source") or {}
    if src.get("id") is not None:
        return f"{src.get('dataset', '')}/{src['id']}@{_num(src.get('start_s')) or 0:.1f}"
    return s["id"]


def acoustic_offset_ms(stream: str) -> float:
    """config/room/latency.json ``sources.<stream>.acoustic_offset_ms`` (the receiver adds it to the stream's host
    times, so the clock's times carry it), 0 when absent."""
    v = ((read_json(LATENCY_JSON).get("sources") or {}).get(stream) or {}).get("acoustic_offset_ms")
    return _num(v) or 0.0


# ======================================================================================================================
# show_service's clock <-> recording samples (tapmap.jsonl)
# ======================================================================================================================
def _bucket_minima(x: np.ndarray, d: np.ndarray, full_only: bool = False):
    """Each TAP_BUCKET_S bucket's earliest arrival: (x at it, its d, the bucket's first index). ``full_only``
    leaves out buckets with under half the usual frames (a piece's ragged ends: one frame's minimum is no minimum)."""
    b = np.floor((x - x[0]) / TAP_BUCKET_S).astype(np.int64)
    first = np.flatnonzero(np.r_[True, np.diff(b) > 0])
    ends = np.r_[first[1:], len(x)]
    xm, dm = np.empty(len(first)), np.empty(len(first))
    for k, (i, j) in enumerate(zip(first, ends)):
        m = i + int(np.argmin(d[i:j]))
        xm[k], dm[k] = x[m], d[m]
    if full_only and len(first) > 2:
        cnt = ends - first
        keep = cnt >= 0.5 * np.median(cnt)
        if keep.sum() >= 2:
            return xm[keep], dm[keep], first[keep]
    return xm, dm, first


def _lower_line(x: np.ndarray, d: np.ndarray) -> dict | None:
    """d = p0 + p1 x below the arrivals: a line through the bucket minima, late buckets (a congested stretch) dropped
    and refitted (show_service/timeline.py keeps the lower envelope the same way)."""
    if len(x) < 10:
        return None
    xm, dm, _ = _bucket_minima(x, d, full_only=True)
    if len(xm) < 2:
        return {"p0": float(np.min(d)), "p1": 0.0, "buckets": len(xm), "used": len(xm), "env_rms_ms": 0.0}
    keep = np.ones(len(xm), bool)
    p1, p0 = np.polyfit(xm, dm, 1)
    for _ in range(4):
        r = dm - (p0 + p1 * xm)
        mad = float(np.median(np.abs(r[keep] - np.median(r[keep])))) * 1.4826
        keep = r < max(0.002, 3.0 * mad)
        if keep.sum() < 2:
            keep[:] = True
            break
        p1, p0 = np.polyfit(xm[keep], dm[keep], 1)
    r = dm - (p0 + p1 * xm)
    return {"p0": float(p0), "p1": float(p1), "buckets": int(len(xm)), "used": int(keep.sum()),
            "env_rms_ms": float(np.sqrt(np.mean(r[keep] ** 2)) * 1000.0)}


def _step_cuts(x: np.ndarray, d: np.ndarray) -> list[int]:
    """Frame indices where the lower envelope steps and stays (the recording's samples and the stream's no longer
    advance together: a splice the recorder couldn't size, a reconnect); a step that comes back to the old line
    within TAP_STALL_S is a stall (late packets), not a new map."""
    if len(x) < 10:
        return []
    xm, dm, first = _bucket_minima(x, d)
    cuts = []
    k, k0 = 1, 0                                     # k0: the current piece's first bucket
    while k < len(dm) - 1:
        base = float(np.median(dm[max(k0, k - 3):k]))   # the line so far (one late bucket doesn't move it)
        if abs(dm[k] - base) > TAP_STEP_S and abs(dm[k + 1] - base) > TAP_STEP_S and \
                np.sign(dm[k] - base) == np.sign(dm[k + 1] - base):
            back = np.flatnonzero((xm > xm[k]) & (xm <= xm[k] + TAP_STALL_S) & (np.abs(dm - base) < 0.005))
            if len(back) and dm[k] > base:           # late, then on the old line again: a stall
                k = int(back[0]) + 1
                continue
            cuts.append(int(first[k]))
            k0 = k
            k += 2
            continue
        k += 1
    return cuts


class HostMap:
    """show_service's clock (host seconds, CLOCK_MONOTONIC) <-> recording seconds, from tapmap.jsonl: each levels
    frame's ``n_end`` (recording samples written so far) and ``a`` (the arrival of the newest packet, whose last
    sample that is). Per piece (split at reconnects and lasting steps), the lower envelope d = a - n_end / fs =
    p0 + p1 x: what the receiver's SampleClock maps a sample to (the earliest arrival line, timeline.py), plus the
    stream's acoustic offset (latency.json), which the receiver adds to every mapped time."""

    def __init__(self, run_dir: pathlib.Path, offset_ms: float = 0.0) -> None:
        self.offset_s = offset_ms / 1000.0
        self.pieces: list[dict] = []
        self.info: dict = {"ok": False}
        rows = A.read_jsonl(run_dir / "tapmap.jsonl")
        n, a, rx = [], [], []
        for r in rows:
            if _num(r.get("n_end")) is not None and _num(r.get("a")) is not None:
                n.append(float(r["n_end"]))
                a.append(float(r["a"]))
                rx.append(_num(r.get("rx")) if _num(r.get("rx")) is not None else math.nan)
        if len(n) < 50:
            self.info = {"ok": False, "reason": f"{len(n)} usable rows in tapmap.jsonl"}
            return
        n, a, rx = np.asarray(n), np.asarray(a), np.asarray(rx)
        o = np.argsort(n, kind="stable")
        n, a, rx = n[o], a[o], rx[o]
        x = n / FS
        d = a - x
        cuts = {0, len(x)}
        if np.isfinite(rx).all():
            cuts |= set((np.flatnonzero(np.diff(rx) > TAP_RECONNECT_S) + 1).tolist())
        bounds = sorted(cuts)
        spans = []
        for i0, i1 in zip(bounds[:-1], bounds[1:]):
            sub = [i0] + [i0 + c for c in _step_cuts(x[i0:i1], d[i0:i1])] + [i1]
            spans += list(zip(sub[:-1], sub[1:]))
        for i0, i1 in spans:
            if i1 - i0 < 10:
                continue
            f = _lower_line(x[i0:i1], d[i0:i1])
            if f is None:
                continue
            res = d[i0:i1] - (f["p0"] + f["p1"] * x[i0:i1])
            f.update(x0=float(x[i0]), x1=float(x[i1 - 1]), frames=int(i1 - i0), slope_ppm=f["p1"] * 1e6,
                     late_ms_p50=float(np.median(res) * 1000), late_ms_p95=float(np.percentile(res, 95) * 1000),
                     early_ms_min=float(np.min(res) * 1000))
            self.pieces.append(f)
        if not self.pieces:
            self.info = {"ok": False, "reason": "no piece of tapmap.jsonl long enough to fit"}
            return
        main = max(self.pieces, key=lambda p: p["x1"] - p["x0"])
        for p in self.pieces:        # a short piece can't pin a slope: the clocks' skew is the long piece's
            if p is not main and p["x1"] - p["x0"] < TAP_SHORT_S and main["x1"] - main["x0"] >= TAP_SHORT_S:
                i = (x >= p["x0"]) & (x <= p["x1"])
                xm, dm, _ = _bucket_minima(x[i], d[i])
                p["p1"], p["p0"] = main["p1"], float(np.median(dm - main["p1"] * xm))
                p["slope_ppm"], p["slope_from"] = main["slope_ppm"], "the longest piece"
        self.info = {"ok": True, "frames": int(len(x)), "pieces": len(self.pieces),
                     "span_s": [float(x[0]), float(x[-1])],
                     "frame_interval_ms_p50": float(np.median(np.diff(x)) * 1000),
                     "skew_ppm": main["slope_ppm"], "late_ms_p50": main["late_ms_p50"],
                     "late_ms_p95": main["late_ms_p95"], "envelope_rms_ms": main["env_rms_ms"],
                     "acoustic_offset_ms": offset_ms,
                     "piece_list": [{k: p[k] for k in ("x0", "x1", "frames", "slope_ppm", "env_rms_ms", "late_ms_p50")}
                                    for p in self.pieces]}

    @property
    def ok(self) -> bool:
        return bool(self.info.get("ok"))

    def to_rec(self, h) -> np.ndarray:
        """Host seconds -> recording seconds (each through the piece whose span holds it, else the nearest)."""
        h = np.atleast_1d(np.asarray(h, float)) - self.offset_s
        out = np.full(h.shape, np.nan)
        best = np.full(h.shape, np.inf)
        for p in self.pieces:
            t = (h - p["p0"]) / (1.0 + p["p1"])
            dist = np.maximum(0.0, np.maximum(p["x0"] - t, t - p["x1"]))
            take = dist < best
            out[take], best[take] = t[take], dist[take]
        return out

    def to_host(self, t) -> np.ndarray:
        t = np.atleast_1d(np.asarray(t, float))
        out = np.full(t.shape, np.nan)
        best = np.full(t.shape, np.inf)
        for p in self.pieces:
            dist = np.maximum(0.0, np.maximum(p["x0"] - t, t - p["x1"]))
            take = dist < best
            out[take] = p["p0"] + (1.0 + p["p1"]) * t[take] + self.offset_s
            best[take] = dist[take]
        return out

    def linear(self) -> tuple[float, float]:
        """rec_s = a + b host_s through the longest piece (analyse.stream_health's form)."""
        p = max(self.pieces, key=lambda p: p["x1"] - p["x0"])
        b = 1.0 / (1.0 + p["p1"])
        return -(p["p0"] + self.offset_s) * b, b


# ======================================================================================================================
# the live bus
# ======================================================================================================================
def read_bus(run_dir: pathlib.Path, stream: str) -> dict:
    """bus.jsonl: music.clock bodies [(in force from, ms; body)] sorted (state.jsonl's 1 Hz clock when there are none,
    as analyse.bus_messages does), and ``stream``'s music.features (t_ms, lufs_s, gate).

    When a body takes effect: its ``t_ms`` is the time it describes, which for a body published from an audio block
    is that block's sound, up to the receiver's latency (~30 ms) before it was published; a consumer can only use it
    once published. So each body counts from max(t_ms, rx_ms - the bus's own delivery delay), the delay being the
    5th percentile of rx_ms - t_ms (the tick-published bodies, whose t_ms is their publish time): run_stack.py's
    ``h``, as near as the bus log shows it."""
    clocks, feats, types = [], [], {}
    for r in A.read_jsonl(run_dir / "bus.jsonl"):
        msg = r.get("msg") if isinstance(r.get("msg"), dict) else {}
        typ = str(msg.get("type"))
        types[typ] = types.get(typ, 0) + 1
        data = msg.get("data") if isinstance(msg.get("data"), dict) else None
        if data is None:
            continue
        tv, rx = _num(data.get("t_ms")), _num(r.get("rx_ms"))
        if tv is None:
            tv = rx
        if tv is None:
            continue
        if typ == "music.clock":
            clocks.append((tv, data, rx))
        elif typ == "music.features" and data.get("stream") in (None, "", stream):
            feats.append((tv, _num(data.get("lufs_s")), _num(data.get("gate"))))
    delay = {"p5_ms": None, "p50_ms": None}
    if clocks:
        d = np.array([rx - tv for tv, _, rx in clocks if rx is not None])
        if len(d) >= 10:
            delay = {"p5_ms": float(np.percentile(d, 5)), "p50_ms": float(np.median(d))}
        d5 = delay["p5_ms"]
        clocks = [(max(tv, rx - d5) if (rx is not None and d5 is not None) else tv, body) for tv, body, rx in clocks]
    else:
        clocks = A.bus_messages(run_dir, stream)[0]
    clocks.sort(key=lambda c: c[0])
    f = np.array([[t, np.nan if lv is None else lv, np.nan if g is None else g] for t, lv, g in feats], float)
    return {"clocks": clocks, "features": f.reshape(-1, 3), "types": types, "bus_delay": delay}


def bus_flashes(clocks: list) -> list[tuple]:
    """The beats the live consumers flashed: analyse.live_beats (integer-beat crossings, each body until the next,
    capped at 2 s when the bus went quiet), then artnet_out's re-phase rule as run_stack.flashes applies it.
    -> [(t host s, k, gate, conf, state, epoch, bpm)]."""
    out, last = [], None
    for b in sorted(A.live_beats(clocks), key=lambda b: b["t_ms"]):
        per = 60000.0 / b["bpm"]
        if last is None or b["t_ms"] > last + REPHASE_SKIP * per:
            out.append((b["t_ms"] / 1000.0, b["k"], b["gate"], b["conf"], b["state"], b["epoch"], b["bpm"]))
            last = b["t_ms"]
    return out


def infer_gate_ref(lufs_s: np.ndarray, gate: np.ndarray) -> dict:
    """dsp's effective gate reference read back from its features: gate = smoothstep(ref - 15, ref, lufs_s), so where
    the gate is part open, ref = lufs_s + 15 (1 - t), t = smoothstep's inverse. That is the reference after dsp's
    floor clamp (min(ref, floor + 6 + 15): a quiet mic gets a lower one)."""
    lv, g = np.asarray(lufs_s, float), np.asarray(gate, float)
    m = np.isfinite(lv) & np.isfinite(g) & (g > 0.02) & (g < 0.98)
    if m.sum() < 5:
        return {"n": int(m.sum()), "ref_lufs": None}
    ref = lv[m] + GATE_SPAN_LU * (1.0 - inv_smoothstep(g[m]))
    return {"n": int(m.sum()), "ref_lufs": float(np.median(ref)),
            "ref_lufs_p10_p90": [float(v) for v in np.percentile(ref, [10, 90])]}


# ======================================================================================================================
# 1. alignment
# ======================================================================================================================
def align_run(rec: np.ndarray, orig: np.ndarray, prog: dict, run: dict, hmap: HostMap) -> dict:
    """u = c0 + c1 t (u programme s, t recording s) by analyse.align; the Sonos' own start (position ticks, run.json)
    as the cross-check, and the fallback (levels only) when the alignment fails."""
    prior = None
    t_start = _num((run.get("sonos") or {}).get("t_start_mono"))
    if t_start is not None and hmap.ok:
        prior = float(hmap.to_rec([t_start])[0])
    try:
        al = A.align(rec, orig, log)
        al.pop("x_al", None)
        c0, c1, ok = float(al["c0"]), float(al["c1"]), True
        s = al["summary"]
        summ = {k: s.get(k) for k in ("method", "offset_s", "drift_ppm", "windows", "status", "residual_rms_ms",
                                      "residual_max_ms", "phat_snr_median", "acoustic_lag", "edit_like_windows")}
        summ["band_line"] = (al.get("bands") or {}).get("summary", {}).get("matched")
        matched = al.get("matched") or []
    except RuntimeError as e:
        if prior is None:
            raise
        log(f"align: {e}; falling back on the Sonos' position ticks (about +-0.5 s: levels only)")
        c0, c1, ok = -prior, 1.0, False
        summ = {"method": "the Sonos' position ticks only (about +-0.5 s): levels only, no beats scored",
                "error": str(e)}
        matched = []
    offset = -c0 / c1
    per_seg: dict = {}
    for w in matched:
        if _num(w.get("resid_ms")) is None:
            continue
        u = c0 + c1 * float(w["tc"])
        s = next((s for s in prog["segments"] if s["t0"] <= u < s["t1"]), None)
        if s is None:
            continue
        e = per_seg.setdefault(s["id"], {"windows": 0, "resid_ms_max": 0.0})
        e["windows"] += 1
        e["resid_ms_max"] = max(e["resid_ms_max"], abs(float(w["resid_ms"])))
    out = {"ok": ok, "c0": c0, "c1": c1, "offset_s": offset, "drift_ppm": (1.0 / c1 - 1.0) * 1e6,
           "summary": summ, "per_segment": per_seg,
           "sonos_ticks_offset_s": prior, "ticks_minus_fit_s": (prior - offset) if prior is not None else None,
           "note": "offset = the recording time of programme 0: the Sonos' start after the recorder's plus the "
                   "acoustic path (one number: nothing here sees the Sonos' own output); drift + = the recording "
                   "runs long against the Sonos"}
    log(f"align: offset {offset:.4f} s, drift {out['drift_ppm']:+.1f} ppm ({summ.get('method')})"
        + (f"; the Sonos' ticks say {prior:.3f} s" if prior is not None else ""))
    return out


def to_prog(al: dict, t):
    return al["c0"] + al["c1"] * np.asarray(t, float)


def to_rec_t(al: dict, u):
    return (np.asarray(u, float) - al["c0"]) / al["c1"]


# ======================================================================================================================
# 2. levels
# ======================================================================================================================
def run_replay(rec_path: pathlib.Path, work: pathlib.Path, gate_ref: float, reuse: bool) -> dict | None:
    """run_stack.py on the whole recording in show_service's env: dsp + the clock as deployed, no slow lane, no
    pre-roll, at ``gate_ref``. -> its result (features: [t, gate, lufs_s, ...] at 2 Hz; flashes; bodies), all times
    recording seconds. ``reuse``: the last result for the same recording and gate, if there is one."""
    work.mkdir(parents=True, exist_ok=True)
    out, key_p = work / "stack_rec.json", work / "stack_rec.key.json"
    st = rec_path.stat()
    key = {"rec": str(rec_path), "size": st.st_size, "mtime": st.st_mtime, "gate_ref_lufs": round(gate_ref, 2)}
    if reuse and out.exists() and read_json(key_p) == key:
        log(f"replay: reusing {out}")
        return json.loads(out.read_text(encoding="utf-8"))
    cmd = ["nice", "-n", "19", str(A.PY_SHOW), str(A.RUN_STACK), str(rec_path), "--out", str(out), "--id", "rec",
           "--no-slow-lane", "--preroll-s", "0", f"--gate-ref-lufs={gate_ref:.2f}"]
    env = dict(os.environ)
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[k] = "1"
    t0 = time.time()
    log(f"replay: run_stack.py on {rec_path.name} at gate {gate_ref:.1f} LUFS (about 0.4 x real time)")
    try:
        p = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=6 * 3600, cwd=str(REPO))
    except (OSError, subprocess.SubprocessError) as e:
        log(f"replay failed: {type(e).__name__}: {e}")
        return None
    if p.returncode != 0:
        log(f"replay failed (exit {p.returncode}): {(p.stderr or '')[-600:]}")
        return None
    r = json.loads(out.read_text(encoding="utf-8"))
    key_p.write_text(json.dumps(key), encoding="utf-8")
    log(f"replay: {r.get('audio_s', 0):.0f} s of audio in {time.time() - t0:.0f} s; {len(r.get('flashes', []))} "
        f"flashes")
    return r


def st_loudness(rec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """BS.1770 short-term loudness (K-weighted, 3 s) every 0.5 s: (window end s, LUFS); not floored."""
    z = roomsim.k_weight(rec)
    z *= z
    blk = FS // 2
    nb = len(z) // blk
    s = z[:nb * blk].reshape(nb, blk).sum(axis=1)
    del z
    c = np.concatenate([[0.0], np.cumsum(s)])
    j = np.arange(6, nb + 1)
    return j * 0.5, -0.691 + 10.0 * np.log10((c[j] - c[j - 6]) / (3 * FS) + EPS)


def cue_settles(man: dict, run: dict) -> list[tuple[float, float, float]]:
    """(programme s from which the volume is steady, the cue's programme s, its volume) per cue: when it was issued
    (run.json sonos.cues ``issued_prog_s``, else the manifest's time) + room_service's ramp."""
    logged = {}
    for c in ((run.get("sonos") or {}).get("cues") or []):
        if _num(c.get("t")) is not None and _num(c.get("issued_prog_s")) is not None:
            logged[round(float(c["t"]), 2)] = float(c["issued_prog_s"])
    out = []
    for c in sorted(man.get("volume_cues") or [], key=lambda c: float(c["t"])):
        t = float(c["t"])
        issued = max(t, logged.get(round(t, 2), t))
        out.append((issued + RAMP_S + SETTLE_S, t, float(c["volume"])))
    return out


def level_span(seg: dict, settles) -> tuple[float, float, list[str]]:
    """The audio [a, b] (programme s) that stands for the segment's level: music minus EDGE_S at each edge, a ladder
    rung's middle LADDER_MID_S, a silence minus its first SILENCE_EDGE_S; never before its volume settled."""
    t0, t1, kind = seg["t0"], seg["t1"], seg.get("kind")
    notes = []
    if kind == "silence":
        a, b = t0 + SILENCE_EDGE_S, t1
    elif kind == "ladder":
        mid = (t0 + t1) / 2
        a, b = max(t0, mid - LADDER_MID_S / 2), min(t1, mid + LADDER_MID_S / 2)
    else:
        a, b = t0 + EDGE_S, t1 - EDGE_S
    if kind != "silence":
        for settle, t_cue, _ in settles:
            if t_cue < t1 and settle > a:
                if t_cue > t0:
                    notes.append(f"a volume cue at {t_cue:g} s inside the segment")
                elif settle > t0:
                    notes.append(f"the volume settled only at {settle:.1f} s (cue issued late)")
                a = settle
    return a, b, notes


def window_values(t: np.ndarray, v: np.ndarray, a: float, b: float) -> np.ndarray:
    """The 3 s loudness values (each at its window's end) whose whole window lies in [a, b]."""
    m = (t >= a + ST_WIN_S - 1e-6) & (t <= b + 1e-6) & np.isfinite(v)
    return v[m]


def floor_bins(rec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """dsp's noise-floor bins: the mean square of each FLOOR_BIN_S (100 ms) of the recording, in dB (DBFS_FLOOR for
    digital silence): (bin centre s, dB)."""
    n = int(round(FLOOR_BIN_S * FS))
    c, p = roomsim.frame_power(rec, n, n)
    return c / FS, np.where(p > 1e-12, 10.0 * np.log10(np.maximum(p, 1e-30)), DBFS_FLOOR)


def floor_estimates(bt_u: np.ndarray, bdb: np.ndarray, settles, room_segs, song_segs, rise_db) -> dict:
    """What dsp's floor (the FLOOR_PCT percentile of the 100 ms bins over the last FLOOR_WINDOW_S, at least
    LUFS_FLOOR) will be: with only the room in its window (the evening's start), after FLOOR_WINDOW_S of continuous
    songs at the test volume, and at party volume (the songs' bins raised by the ladder's rise, the room's own power
    kept as it is). The gate's reference never goes above floor + FLOOR_ABOVE_LU + GATE_SPAN_LU."""
    def sel(segs):
        m = np.zeros(len(bt_u), bool)
        for s in segs:
            a, b, _ = level_span(s, settles)
            m |= (bt_u >= a) & (bt_u <= b)
        return bdb[m]
    room, songs = sel(room_segs), sel(song_segs)
    out: dict = {"bins_room": int(len(room)), "bins_songs": int(len(songs)), "pct": FLOOR_PCT,
                 "window_s": FLOOR_WINDOW_S}
    p_room = float(np.median(_pow(room))) if len(room) >= 20 else 0.0
    if len(room) >= 20:
        out["room_db"] = max(LUFS_FLOOR, float(np.percentile(room, FLOOR_PCT)))
    if len(songs) >= 50:
        out["songs_test_db"] = max(LUFS_FLOOR, float(np.percentile(songs, FLOOR_PCT)))
        if rise_db is not None:
            pp = np.maximum(_pow(songs) - p_room, 0.0) * 10 ** (rise_db / 10.0) + p_room
            out["songs_party_db"] = max(LUFS_FLOOR, float(np.percentile(_dbp(pp), FLOOR_PCT)))
    for k in ("room", "songs_test", "songs_party"):
        if out.get(k + "_db") is not None:
            out[k + "_cap_lufs"] = out[k + "_db"] + FLOOR_ABOVE_LU + GATE_SPAN_LU
    return out


def segment_levels(prog: dict, settles, series: dict) -> dict:
    """{seg id: {"span", source: {"median", "n", "p10", "p90"}}} for each series {name: (programme s, LUFS)}."""
    out = {}
    for s in prog["segments"]:
        a, b, notes = level_span(s, settles)
        e = {"span_s": [a, b], "notes": notes}
        for name, (t, v) in series.items():
            if t is None:
                continue
            w = window_values(t, v, a, b)
            e[name] = {"median": _med(w), "n": int(len(w)),
                       "p10": float(np.percentile(w, 10)) if len(w) else None,
                       "p90": float(np.percentile(w, 90)) if len(w) else None}
        out[s["id"]] = e
    return out


# ======================================================================================================================
# 3. the ladder
# ======================================================================================================================
def _fit1(x: np.ndarray, y: np.ndarray) -> dict:
    n = len(x)
    A_ = np.stack([np.ones(n), x], axis=1)
    (a, b), *_ = np.linalg.lstsq(A_, y, rcond=None)
    r = y - (a + b * x)
    sxx = float(np.sum((x - x.mean()) ** 2))
    s = math.sqrt(float(r @ r) / (n - 2)) if n > 2 else None
    return {"a": float(a), "b": float(b), "rms_db": float(np.sqrt(np.mean(r ** 2))), "s_db": s, "n": n,
            "xbar": float(x.mean()), "sxx": sxx, "resid_db": [float(v) for v in r]}


def fit_ladder(points: list[dict], room_tone_lufs: float | None, v_main: float | None,
               v_party: float = PARTY_VOLUME) -> dict:
    """points [{"id", "volume", "lufs"}] (the rungs' medians) -> the two curves, the winner, and the level at the main
    and party volumes with an uncertainty (module docstring, step 3). Levels are music only (the room tone's power
    taken off; a rung within LADDER_MIN_SNR_DB of it is left out), and the predictions add the room tone back."""
    xform = {"linear": lambda v: np.asarray(v, float), "log": lambda v: 20.0 * np.log10(np.asarray(v, float))}
    pts = sorted([p for p in points if _num(p.get("lufs")) is not None and (_num(p.get("volume")) or 0) > 0],
                 key=lambda p: p["volume"])
    out: dict = {"rungs": [], "ok": False, "party_volume": v_party, "volume_main": v_main,
                 "room_tone_lufs": room_tone_lufs}
    v, L, Lm = [], [], []
    for p in pts:
        lv = float(p["lufs"])
        snr = lv - room_tone_lufs if room_tone_lufs is not None else None
        use = snr is None or snr >= LADDER_MIN_SNR_DB
        lm = float(_dbp(_pow(lv) - _pow(room_tone_lufs))) if (use and room_tone_lufs is not None) else lv
        out["rungs"].append({"id": p["id"], "volume": float(p["volume"]), "lufs": lv, "music_lufs": lm if use else None,
                             "over_room_tone_db": snr, "used": bool(use)})
        if use:
            v.append(float(p["volume"]))
            L.append(lv)
            Lm.append(lm)
    if len(set(v)) < 2:
        out["reason"] = f"{len(set(v))} usable rung volume(s): no curve"
        return out
    v, Lm = np.asarray(v), np.asarray(Lm)
    fits = {}
    for name, f in xform.items():
        fit = _fit1(f(v), Lm)
        fits[name] = fit
    best = min(fits, key=lambda k: (fits[k]["rms_db"] + (1e-9 if k == "log" else 0.0)))
    other = "log" if best == "linear" else "linear"

    def at(name, vol):
        f = fits[name]
        x0 = float(xform[name]([vol])[0])
        pred = f["a"] + f["b"] * x0
        s = max(f["s_db"] if f["s_db"] is not None else 0.0, LADDER_SE_FLOOR_DB)
        se = s * math.sqrt(1.0 / f["n"] + ((x0 - f["xbar"]) ** 2 / f["sxx"] if f["sxx"] > 0 else 0.0))
        return pred, se

    def total(music_lufs):
        if room_tone_lufs is None:
            return music_lufs
        return float(_dbp(_pow(music_lufs) + _pow(room_tone_lufs)))

    def predict(vol):
        pb, se = at(best, vol)
        po, _ = at(other, vol)
        u = math.sqrt(se ** 2 + ((pb - po) / 2.0) ** 2)
        return {"volume": vol, "music_lufs": pb, "lufs": total(pb), "u_db": u, "fit_se_db": se,
                "curves_disagree_db": pb - po, "other_curve_lufs": total(po),
                "range_lufs": [total(pb - 2 * u), total(pb + 2 * u)]}

    def rise(v_from, v_to):
        """The level change from v_from to v_to on the winning curve, its uncertainty (the slope's error times the
        step, and half the curves' disagreement on the step)."""
        db = {k: at(k, v_to)[0] - at(k, v_from)[0] for k in fits}
        f = fits[best]
        s = max(f["s_db"] if f["s_db"] is not None else 0.0, LADDER_SE_FLOOR_DB)
        dx = float(xform[best]([v_to])[0] - xform[best]([v_from])[0])
        se_b = s / math.sqrt(f["sxx"]) if f["sxx"] > 0 else 0.0
        u = math.sqrt((se_b * dx) ** 2 + ((db[best] - db[other]) / 2.0) ** 2)
        return {"from": v_from, "to": v_to, "db": db[best], "u_db": u, "other_curve_db": db[other]}

    out.update(ok=True, model=best, fits={k: {kk: fits[k][kk] for kk in ("a", "b", "rms_db", "s_db", "resid_db")}
                                          for k in fits},
               db_per_step=fits["linear"]["b"], db_per_db_volume=fits["log"]["b"],
               volumes=[float(x) for x in sorted(set(v))], top_rung=float(max(v)),
               extrapolation_steps=float(v_party - max(v)),
               model_note={"linear": "level (dB) = a + b x volume: a fixed number of dB per Sonos step",
                           "log": "level (dB) = a + b x 20 log10(volume): amplitude as a power of the volume"}[best],
               party=predict(float(v_party)))
    if v_main is not None:
        out["main"] = predict(float(v_main))
        out["rise_main_to_party"] = rise(float(v_main), float(v_party))
    return out


def rung_gains(rec: np.ndarray, pwav: pathlib.Path, prog: dict, settles, al: dict) -> list[dict]:
    """The ladder's cross-check, robust to the room's own intermittent sound (talk, steps: a median level can't
    take those off, a room-tone median under them): each rung's gain, recording over programme, K-weighted, the
    median of the per-100 ms ratios over the excerpt's louder half of frames (where the music outweighs the room).
    Every rung plays the same excerpt, so the gains' differences are the Sonos' volume curve itself."""
    import soundfile as sf
    out = []
    n = int(round(0.1 * FS))
    pre = FS                                         # a second before the span settles the K-weighting filters
    for s in prog["segments"]:
        if s["kind"] != "ladder" or _num(s.get("volume")) is None:
            continue
        a, b, _ = level_span(s, settles)
        i0, i1 = int(round(a * FS)), int(round(b * FS))
        x, fs = sf.read(str(pwav), start=max(0, i0 - pre), stop=i1, dtype="float64", always_2d=True)
        if fs != FS:
            continue
        x = x.mean(axis=1)
        j0 = int(round(float(to_rec_t(al, a)) * FS))
        if j0 - pre < 0 or j0 + (i1 - i0) > len(rec):
            continue
        y = rec[j0 - pre:j0 + (i1 - i0)]
        k = min(len(x), len(y))
        zx = roomsim.k_weight(x[:k]) ** 2
        zy = roomsim.k_weight(y[:k]) ** 2
        m = (k - pre) // n
        px = zx[pre:pre + m * n].reshape(m, n).mean(axis=1)
        py = zy[pre:pre + m * n].reshape(m, n).mean(axis=1)
        loud = px >= np.median(px)
        ok = loud & (px > 0) & (py > 0)
        if ok.sum() < 10:
            continue
        out.append({"id": s["id"], "volume": float(s["volume"]), "lufs": float(np.median(_dbp(py[ok] / px[ok]))),
                    "frames": int(ok.sum())})
    return out


# ======================================================================================================================
# 4. the clock per segment
# ======================================================================================================================
def clock_on_segment(seg: dict, fl_u: list[tuple], bodies_u: tuple, SC) -> dict:
    """One clock's flashes and published line on one segment (programme time), scored with beat_eval/score.py's
    score_one against the segment's beats (its ``_0`` range: from the segment's start), plus the share of the
    segment the gate said "beat" and the time to its first "beat"."""
    t0, t1 = seg["t0"], seg["t1"]
    dur = t1 - t0
    flashes = [[f[0] - t0, *f[1:]] for f in fl_u if t0 - 0.5 <= f[0] <= t1 + 0.5]
    hu, bb, bg = bodies_u
    out: dict = {"n_flashes": sum(1 for f in flashes if 0 <= f[0] <= dur),
                 "n_gated_flashes": sum(1 for f in flashes if f[2] == "beat" and 0 <= f[0] <= dur)}
    if len(hu):
        ts = np.arange(0.0, dur, 0.05)
        bi = np.searchsorted(hu, ts + t0, side="right") - 1
        g = np.where(bi >= 0, bg[np.maximum(bi, 0)], "none")
        beat, dmx = g == "beat", g == "dmx"
        out["gate_beat_share"] = float(beat.mean()) if len(ts) else None
        out["gate_beat_or_dmx_share"] = float((beat | dmx).mean()) if len(ts) else None
        out["t_first_beat_gate_s"] = float(ts[int(np.argmax(beat))]) if beat.any() else None
        i0 = max(0, int(np.searchsorted(hu, t0, side="right")) - 1)
        i1 = int(np.searchsorted(hu, t1, side="right"))
        rows = [[float(hu[i] - t0), None, None, float(bb[i]), None, None, None, None, None, None, str(bg[i]), None,
                 None] for i in range(i0, i1)]
    else:
        out.update(gate_beat_share=None, gate_beat_or_dmx_share=None, t_first_beat_gate_s=None)
        rows = []
    gt = seg.get("_gt")
    if gt is not None and len(gt) >= 4 and seg.get("kind") != "silence":
        src = seg.get("source") or {}
        r = {"id": seg["id"], "dataset": src.get("dataset") or "", "genre": seg.get("genre") or "", "track_s": dur,
             "flash_fields": list(FLASH_FIELDS), "flashes": flashes, "body_fields": list(BODY_FIELDS),
             "bodies": rows, "stats": {}, "audio_s": dur}
        row = SC.score_one(r, np.asarray(gt, float) - t0)
        for k in SCORE_KEYS:
            out[k] = _num(row.get(f"{k}_0"))
        out["first_beat_s"] = float(gt[(gt >= t0) & (gt <= t1)][0] - t0) if np.any((gt >= t0) & (gt <= t1)) else None
    return out


def clock_flashes_u(flashes_host: list[tuple], hmap: HostMap, al: dict) -> list[tuple]:
    if not flashes_host:
        return []
    u = to_prog(al, hmap.to_rec([f[0] for f in flashes_host]))
    return [(float(uu), *f[1:]) for uu, f in zip(u, flashes_host)]


def clock_bodies_u(clocks: list, hmap: HostMap, al: dict) -> tuple:
    if not clocks:
        return np.zeros(0), np.zeros(0), np.zeros(0, object)
    hu = to_prog(al, hmap.to_rec([c[0] / 1000.0 for c in clocks]))
    bb = np.array([_num(c[1].get("bpm")) or 0.0 for c in clocks])
    bg = np.array([str(c[1].get("gate", "none")) for c in clocks], object)
    o = np.argsort(hu, kind="stable")
    return hu[o], bb[o], bg[o]


def offline_clock_u(res: dict, al: dict) -> tuple[list[tuple], tuple]:
    ff, bf = res["flash_fields"], res["body_fields"]
    ti = ff.index("t")
    fl = [(float(to_prog(al, f[ti])), *[f[ff.index(k)] for k in FLASH_FIELDS[1:]]) for f in res["flashes"]]
    rows = res["bodies"]
    hu = to_prog(al, np.array([b[bf.index("h")] for b in rows], float)) if rows else np.zeros(0)
    bb = np.array([b[bf.index("bpm")] or 0.0 for b in rows], float)
    bg = np.array([str(b[bf.index("gate")]) for b in rows], object)
    return fl, (hu, bb, bg)


def piano_like(seg: dict) -> bool:
    src = seg.get("source") or {}
    return "piano" in str(seg.get("genre") or "").lower() or src.get("dataset") == "asap"


def clocks_json(fl: list[tuple], bodies: tuple) -> dict:
    """One clock in programme time, for a later run's before/after (analysis/clock_u.json)."""
    hu, bb, bg = bodies
    return {"flashes": [[round(float(f[0]), 6), *f[1:]] for f in fl],
            "bodies": [[round(float(h), 6), float(b), str(g)] for h, b, g in zip(hu, bb, bg)]}


def clocks_from_json(d: dict) -> tuple[list[tuple], tuple]:
    fl = [tuple(f) for f in d.get("flashes") or []]
    rows = d.get("bodies") or []
    return fl, (np.array([r[0] for r in rows], float), np.array([r[1] for r in rows], float),
                np.array([r[2] for r in rows], object))


def overlap_segment(seg: dict, o0: float, o1: float) -> dict:
    """The part of ``seg`` that played source seconds [o0, o1), as a segment of its own (programme time), with its
    beats there."""
    src = seg.get("source") or {}
    a = seg["t0"] + (o0 - float(src.get("start_s") or 0.0))
    b = seg["t0"] + (o1 - float(src.get("start_s") or 0.0))
    gt = seg.get("_gt")
    return {**seg, "t0": a, "t1": b, "_gt": gt[(gt >= a) & (gt < b)] if gt is not None else None}


def before_after_rows(prog: dict, live: tuple, p1_prog: dict, p1_live: tuple, SC) -> list[dict]:
    """Each song of this run that pass 1 played too (the same source item, source time overlapping by 15 s or
    more), both clocks scored over the shared audio only (pass 2 plays Sweet Dreams' first 60 s, pass 1 its first
    75)."""
    rows = []
    for s2 in prog["segments"]:
        src2 = s2.get("source") or {}
        if s2["kind"] != "music" or src2.get("id") is None:
            continue
        for s1 in p1_prog["segments"]:
            src1 = s1.get("source") or {}
            if s1["kind"] != "music" or (src1.get("dataset"), src1.get("id")) != (src2.get("dataset"), src2.get("id")):
                continue
            o0 = max(float(src1.get("start_s") or 0), float(src2.get("start_s") or 0))
            o1 = min(float(src1.get("start_s") or 0) + float(src1.get("dur_s") or 0),
                     float(src2.get("start_s") or 0) + float(src2.get("dur_s") or 0))
            if o1 - o0 < 15.0:
                continue
            rows.append({"label": s2["label"], "pass1_id": s1["id"], "source_s": [o0, o1],
                         "before": clock_on_segment(overlap_segment(s1, o0, o1), *p1_live, SC),
                         "after": clock_on_segment(overlap_segment(s2, o0, o1), *live, SC)})
            break
    return rows


# ======================================================================================================================
# config
# ======================================================================================================================
def write_audio_cal(path: pathlib.Path, stream: str, entry: dict) -> dict:
    """Merge ``entry`` (run, measured_at, device, summary) into ``path``'s streams.<stream>: every other stream and
    top-level key stays, and so does every other key of this stream's entry (a model from analyse.py). A file that
    exists but doesn't parse is left alone (raises)."""
    cfg: dict = {}
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))      # a torn or hand-broken file: raise, never clobber
        if not isinstance(cfg, dict):
            raise ValueError(f"{path} is not a JSON object")
    streams = cfg.get("streams") if isinstance(cfg.get("streams"), dict) else {}
    cfg["streams"] = streams
    old = streams.get(stream) if isinstance(streams.get(stream), dict) else {}
    new = dict(old)
    new.update(entry)
    streams[stream] = new
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(A._clean(cfg), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return new


def find_pass1(run_dir: pathlib.Path, stream: str) -> pathlib.Path | None:
    """The newest sibling run (older than this one) of a "pass1-*" programme with a programme_report.json."""
    best = None
    for d in sorted(run_dir.parent.iterdir()):
        if not d.is_dir() or d.resolve() == run_dir.resolve() or d.name >= run_dir.name:
            continue
        rep = read_json(d / "analysis" / "programme_report.json")
        if str((rep.get("programme") or {}).get("name", "")).startswith("pass1-") and \
                (rep.get("run") or {}).get("stream") == stream:
            best = d
    return best


# ======================================================================================================================
# the report
# ======================================================================================================================
def _f1(v, fmt="{:.1f}", none="n/a"):
    return none if v is None else fmt.format(v)


def _pct(v):
    return "n/a" if v is None else f"{100 * v:.0f} %"


def report_md(rep: dict) -> str:
    L: list[str] = []
    run, prog, gv, lad = rep["run"], rep["programme"], rep["gate"], rep.get("ladder") or {}
    lu = rep.get("ladder_used") or {}
    al, lv = rep["alignment"], rep["levels"]
    L.append(f"# Room mic calibration: {prog['name']}")
    L.append("")
    L.append(f"Run `{run['run']}`, stream **{run['stream']}** ({run.get('device') or 'device unknown'}), "
             f"{run.get('started_utc') or ''}. Main (test) volume {gv.get('volume_main')}, party volume "
             f"{gv.get('party_volume')}."
             + (" **Synthetic run** (a test, not the room)." if run.get("synthetic") else ""))
    L.append("")
    L.append("## In short")
    L.append("")
    party, test = gv.get("music_lufs_party"), gv.get("music_lufs_test")
    if party is not None:
        u = gv.get("music_lufs_party_u_db")
        lo, hi = (party - 2 * u, party + 2 * u) if u is not None else (None, None)
        L.append(f"- **Party music (Sonos {gv['party_volume']}) should read about {party:.1f} LUFS at this mic**"
                 + (f" (likely {lo:.1f} to {hi:.1f}: the ladder went up to {lu.get('top_rung'):g}, so "
                    f"{lu.get('extrapolation_steps'):g} steps are extrapolated"
                    + (f"; pass 1's ladder, `{gv['ladder_from']}`" if gv.get("ladder_from") else "") + ")."
                    if u is not None and lu.get("top_rung") is not None else "."))
    else:
        L.append(f"- Party level: not measured ({lad.get('reason') or 'no usable volume ladder'}).")
    if test is not None:
        L.append(f"- At the test volume ({gv['volume_main']:g}) the music read **{test:.1f} LUFS** (dsp's 3 s "
                 f"loudness, median over {gv.get('music_segments_used')} songs"
                 + (f" of both passes: {_f1(gv.get('music_lufs_test_here'))} in this one, "
                    f"{_f1(gv.get('music_lufs_test_pass1'))} in pass 1"
                    if gv.get("music_lufs_test_pooled_with") else "")
                 + f"; songs ranged {_f1(gv.get('music_lufs_test_p10'))} to {_f1(gv.get('music_lufs_test_p90'))}).")
    rt = lv.get("room_tone") or {}
    if rt.get("lufs_bs1770") is not None:
        L.append(f"- The room itself (the silences) reads {rt['lufs_bs1770']:.1f} LUFS"
                 + (f" (dsp sees {rt['lufs_dsp']:.1f}: its floor is {LUFS_FLOOR:.0f})" if rt.get("lufs_dsp") is not None
                    and rt["lufs_dsp"] <= LUFS_FLOOR + 0.5 else "") + ".")
    g = rep.get("gate_setting") or {}
    if g.get("ref_lufs") is not None:
        L.append(f"- **The gate:** with music_lufs = {g['music_lufs']:.1f}, the loudness gate's reference for "
                 f"{run['stream']} becomes {g['ref_lufs']:.1f} LUFS (offset {g['offset_db']:+.1f} dB): it starts "
                 f"opening {GATE_SPAN_LU:g} below that and is fully open at it.")
        fl = g.get("floor") or {}
        caps = [(fl.get("room_cap_lufs"), "with only the room in its last 10 minutes (the evening's start)"),
                (fl.get("songs_party_cap_lufs"), "after 10 minutes of party music")]
        caps = [(c, w) for c, w in caps if c is not None and c < g["ref_lufs"]]
        if caps:
            L.append("- dsp also keeps the reference under its noise floor + %g LU: " % (FLOOR_ABOVE_LU + GATE_SPAN_LU)
                     + "; ".join(f"{c:.1f} LUFS {w}" for c, w in caps) + ", which is lower, so that is the gate.")
        L.append(f"- At party volume the music holds the gate {_pct(g.get('gate_at_party'))} open "
                 f"({_pct(g.get('gate_at_party_start'))} at the evening's start); at the test volume "
                 f"{_pct(g.get('gate_at_test'))}.")
    if rep.get("config_written"):
        L.append(f"- Written to `{rep['config_written']}` (streams.{run['stream']}).")
    lg = rep.get("live_gate") or {}
    if lg.get("ref_lufs") is not None:
        L.append(f"- During this run the live gate's reference was {lg['ref_lufs']:.1f} LUFS (read back from dsp's "
                 f"features).")
    clk = rep.get("clock") or {}
    songs = [s for s in clk.get("segments", []) if s["kind"] == "music" and not s.get("piano") and
             (s.get("live") or {}).get("f") is not None]
    if songs:
        fs = [s["live"]["f"] for s in songs]
        cov = [s["live"].get("cover_beat") or 0.0 for s in songs]
        L.append(f"- **The live beat clock** on {len(songs)} songs: F {np.mean(fs):.2f} on average "
                 f"(best {max(fs):.2f}, worst {min(fs):.2f}); the gate said \"beat\" for {100 * np.mean(cov):.0f} % "
                 f"of the beats.")
    if not al.get("ok"):
        L.append("- **The alignment failed**: the levels use the Sonos' own start (about +-0.5 s); no beats scored.")
    L.append("")
    # levels
    L.append("## Levels per segment")
    L.append("")
    L.append(f"dsp's lufs_s (3 s loudness), the median over each segment's middle; primary source: "
             f"**{lv.get('source')}**.")
    L.append("")
    L.append("| segment | kind | volume | lufs_s (primary) | offline replay | live bus | BS.1770 here |")
    L.append("|---|---|---|---|---|---|---|")
    for s in prog["segments"]:
        e = lv["segments"].get(s["id"]) or {}

        def m(name):
            return _f1((e.get(name) or {}).get("median"))
        L.append(f"| {s['label']} | {s['kind']} | {_f1(s.get('volume'), '{:g}')} | **{m('primary')}** | "
                 f"{m('offline')} | {m('live')} | {m('bs1770')} |")
    L.append("")
    # ladder
    L.append("## Volume ladder")
    L.append("")
    if lad.get("ok"):
        L.append("| volume | lufs_s | music only (room tone off) | over room tone | used |")
        L.append("|---|---|---|---|---|")
        for r in lad["rungs"]:
            L.append(f"| {r['volume']:g} | {r['lufs']:.2f} | {_f1(r['music_lufs'], '{:.2f}')} | "
                     f"{_f1(r['over_room_tone_db'], '{:.1f} dB')} | {'yes' if r['used'] else 'no'} |")
        L.append("")
        fl, fg = lad["fits"]["linear"], lad["fits"]["log"]
        L.append(f"- A line in volume: {fl['b']:.3f} dB per step (residual {fl['rms_db']:.2f} dB rms). A line in "
                 f"log-volume: {fg['b']:.3f} dB per dB of volume (residual {fg['rms_db']:.2f} dB rms). "
                 f"**{lad['model']}** fits better.")
        p = lad["party"]
        L.append(f"- At {p['volume']:g}: {p['lufs']:.1f} LUFS for the ladder's excerpt (+-{p['u_db']:.1f} dB, 1 sigma; "
                 f"the other curve says {p['other_curve_lufs']:.1f}).")
        if lad.get("main"):
            mn = lad["main"]
            L.append(f"- At {mn['volume']:g}: {mn['lufs']:.1f} LUFS on the curve; the songs measured "
                     f"{_f1(gv.get('music_lufs_test'))} there"
                     + (f" ({gv['test_minus_ladder_db']:+.1f} dB: the songs against the ladder's excerpt)."
                        if gv.get("test_minus_ladder_db") is not None else "."))
        bg = lad.get("by_gain")
        if bg:
            L.append(f"- Cross-check, the rungs' gain (recording over programme on the music's louder moments, so the "
                     f"room's own sound can't lift the quiet rungs): {bg['db_per_step']:.3f} dB per step "
                     f"({bg['model']} fits better), "
                     + ", ".join(f"{r['volume']:g}: {r['lufs']:+.2f} dB" for r in bg["rungs"]) + ".")
        if lad.get("rise_main_to_party"):
            rz = lad["rise_main_to_party"]
            L.append(f"- From {rz['from']:g} to {rz['to']:g} the level rises {rz['db']:.1f} dB (+-{rz['u_db']:.1f}; "
                     f"the other curve: {rz['other_curve_db']:.1f}"
                     + (f"; by gain: {rz['by_gain_db']:.1f}" if rz.get("by_gain_db") is not None else "")
                     + "). The party figure is the songs' level at "
                     f"{rz['from']:g} plus this rise. Above {lad['top_rung']:g} nothing was measured: if the Sonos or "
                     f"the mic compresses up there, party music will read lower than this.")
    else:
        L.append(f"No ladder in this run ({lad.get('reason') or 'none in this programme'})."
                 + (f" Pass 1's (`{gv['ladder_from']}`): {lu['db_per_step']:.3f} dB per step, the {lu['model']} curve; "
                    f"from {lu['rise']['from']:g} to {lu['rise']['to']:g} the level rises {lu['rise']['db']:.1f} dB "
                    f"(+-{lu['rise']['u_db']:.1f}), and that rise is what the party figure adds to this run's songs."
                    if gv.get("ladder_from") and lu.get("ok") and lu.get("rise") else ""))
    L.append("")
    # clock
    L.append("## The live beat clock, per segment")
    L.append("")
    if clk.get("available"):
        L.append("Live = what the room's consumers flashed (bus.jsonl); offline = show_service's stack replayed on "
                 "the recording (parity). F, CMLt, AMLt: mir_eval (70 ms); hit25: beats with a flash within 25 ms; "
                 "cover: beats while the gate said \"beat\"; prec: gated flashes on a beat; wrong: gated time locked "
                 "wrong; gate at: seconds from the song's start to the first \"beat\" gate; lock: to 4 correct gated "
                 "flashes in a row.")
        L.append("")
        L.append("| segment | vol | F | CMLt | AMLt | hit25 | cover | prec | wrong | gate at | lock | bpm (true) | "
                 "offline F | offline cover |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for s in clk["segments"]:
            if s["kind"] == "silence":
                continue
            v, o = s.get("live") or {}, s.get("offline") or {}
            L.append(f"| {s['label']} | {_f1(s.get('volume'), '{:g}')} | {_f1(v.get('f'), '{:.2f}')} | "
                     f"{_f1(v.get('cmlt'), '{:.2f}')} | {_f1(v.get('amlt'), '{:.2f}')} | "
                     f"{_f1(v.get('hit25'), '{:.2f}')} | {_f1(v.get('cover_beat'), '{:.2f}')} | "
                     f"{_f1(v.get('prec_beat'), '{:.2f}')} | {_f1(v.get('wrong_lock'), '{:.2f}')} | "
                     f"{_f1(v.get('t_first_beat_gate_s'))} | {_f1(v.get('t_lock'))} | "
                     f"{_f1(v.get('est_bpm'), '{:.0f}')} ({_f1(v.get('gt_bpm'), '{:.0f}')}) | "
                     f"{_f1(o.get('f'), '{:.2f}')} | {_f1(o.get('cover_beat'), '{:.2f}')} |")
        L.append("")
        for s in clk["segments"]:
            if s.get("piano"):
                v = s.get("live") or {}
                n_g = v.get("n_gated_flashes", 0)
                off = None if v.get("prec_beat_dh") is None else 1 - v["prec_beat_dh"]
                L.append(f"- Piano ({s['label']}, rubato: the clock should stay out): the gate said \"beat\" "
                         f"{_pct(v.get('gate_beat_share'))} of the time"
                         + (f", {n_g} gated flashes, {_pct(off)} of them off the beat (false locks), wrong lock "
                            f"{_pct(v.get('wrong_lock'))}." if n_g else ", no gated flashes: no false locks."))
        sil = [s for s in clk["segments"] if s["kind"] == "silence"]
        if sil:
            n = sum((s.get("live") or {}).get("n_gated_flashes", 0) for s in sil)
            share = max(((s.get("live") or {}).get("gate_beat_share") or 0.0) for s in sil)
            L.append(f"- Silences: {n} gated flashes in all; the gate said \"beat\" at most {_pct(share)} of a "
                     f"silence (the clock holding on through the gap).")
        par = clk.get("parity")
        if par:
            L.append(f"- Live vs offline: F differs by {par['f_abs_diff_mean']:.3f} on average (max "
                     f"{par['f_abs_diff_max']:.3f}) over the {par['segments']} segments from programme "
                     f"{par['from_programme_s']:.0f} s (the replay starts cold: dsp learns its floor over "
                     f"{FLOOR_MIN_S:g} s); offline gate {par['offline_gate_ref']:.1f} LUFS.")
    else:
        L.append(f"Not scored: {clk.get('reason')}.")
    L.append("")
    ba = rep.get("before_after")
    if ba:
        L.append(f"## Before and after (pass 1 `{ba['pass1_run']}`, gate {_f1(ba.get('pass1_gate_ref'))} LUFS -> "
                 f"this run, gate {_f1(ba.get('this_gate_ref'))} LUFS)")
        L.append("")
        L.append("The same audio in both passes (the songs both played, over the part both played).")
        L.append("")
        L.append("| song | seconds | F before | F after | cover before | cover after | gate at before "
                 "| gate at after |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in ba["rows"]:
            b, a = r["before"], r["after"]
            L.append(f"| {r['label']} | {r['source_s'][1] - r['source_s'][0]:.0f} | {_f1(b.get('f'), '{:.2f}')} | "
                     f"{_f1(a.get('f'), '{:.2f}')} | "
                     f"{_f1(b.get('cover_beat'), '{:.2f}')} | {_f1(a.get('cover_beat'), '{:.2f}')} | "
                     f"{_f1(b.get('t_first_beat_gate_s'))} | {_f1(a.get('t_first_beat_gate_s'))} |")
        L.append("")
    # alignment
    L.append("## Alignment and clocks")
    L.append("")
    sm = al.get("summary") or {}
    L.append(f"- Programme 0 s is recording {al['offset_s']:.3f} s; the recording runs {al['drift_ppm']:+.1f} ppm "
             f"against the Sonos ({sm.get('method')}; residual {_f1(sm.get('residual_rms_ms'), '{:.2f}')} ms rms, "
             f"max {_f1(sm.get('residual_max_ms'), '{:.2f}')})."
             + (f" The Sonos' position ticks put it at {al['sonos_ticks_offset_s']:.2f} s "
                f"({al['ticks_minus_fit_s']:+.2f} s)." if al.get("sonos_ticks_offset_s") is not None else ""))
    hm = rep.get("host_map") or {}
    if hm.get("ok"):
        L.append(f"- show_service's clock to the recording: {hm['pieces']} piece(s), the stream's clock "
                 f"{hm['skew_ppm']:+.1f} ppm against the host's, arrivals {hm['late_ms_p50']:.1f} ms (p50) and "
                 f"{hm['late_ms_p95']:.1f} ms (p95) after the earliest; latency.json offset "
                 f"{hm['acoustic_offset_ms']:g} ms.")
    else:
        L.append(f"- No host map: {hm.get('reason')}.")
    bd = rep.get("bus_delay") or {}
    if bd.get("p5_ms") is not None:
        L.append(f"- The bus log received clock bodies {bd['p5_ms']:.1f} ms (p5) / {bd['p50_ms']:.1f} ms (p50) after "
                 f"their t_ms; each body counts from its receipt less the p5 (its publication), never before its t_ms.")
    rc = rep.get("recorder") or {}
    if rc:
        L.append(f"- The recorder: {rc.get('gaps', 0)} gaps ({rc.get('gap_s', 0)} s filled), {rc.get('race', 0)} "
                 f"counter races, {rc.get('reconnects', 0)} reconnects.")
    sh = rep.get("stream_health")
    if sh:
        L.append(f"- The live stream while the programme played: {sh.get('lost')} packets lost of "
                 f"{sh.get('packets')}, {sh.get('segments')} new segments.")
    L.append("")
    L.append(f"_Made by tools/room/audio_cal/programme_analyse.py at {rep['analysed_utc']} in "
             f"{rep['wall_s']:.0f} s._")
    return "\n".join(L) + "\n"


def make_fig(path: pathlib.Path, prog: dict, series: dict, lad: dict, gv: dict) -> bool:
    try:
        plt = A._plt()
    except Exception:                                        # noqa: BLE001  no matplotlib: no figure
        return False
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.6), gridspec_kw={"width_ratios": [3, 1]})
    col = {"silence": A.MUTED, "ladder": A.PAL["orange"], "music": A.PAL["blue"]}
    for s in prog["segments"]:
        a1.axvspan(s["t0"], s["t1"], color=col.get(s["kind"], A.PAL["aqua"]), alpha=0.08, lw=0)
    for name, (t, v) in series.items():
        if t is not None and name in ("primary", "bs1770"):
            a1.plot(t, v, lw=1.0 if name == "primary" else 0.7, color=A.INK if name == "primary" else A.PAL["aqua"],
                    label={"primary": "lufs_s (dsp)", "bs1770": "BS.1770 here"}[name])
    a1.set_ylim(bottom=max(a1.get_ylim()[0], LUFS_FLOOR - 15))
    a1.set_xlabel("programme s")
    a1.set_ylabel("LUFS (3 s)")
    a1.set_title("Loudness at the mic")
    a1.legend(loc="lower right")
    if lad.get("ok"):
        vv = np.array([r["volume"] for r in lad["rungs"]])
        ll = np.array([r["lufs"] for r in lad["rungs"]])
        a2.plot(vv, ll, "o", color=A.PAL["orange"], label="rungs")
        xs = np.linspace(max(1.0, vv.min() - 5), lad["party_volume"], 100)
        for k, ls in (("linear", "-"), ("log", "--")):
            f = lad["fits"][k]
            x = xs if k == "linear" else 20 * np.log10(xs)
            a2.plot(xs, f["a"] + f["b"] * x, ls, color=A.INK2, lw=1, label=k)
        p = lad["party"]
        a2.errorbar([p["volume"]], [p["lufs"]], yerr=[2 * p["u_db"]], fmt="s", color=A.PAL["orange"], alpha=0.6,
                    label="excerpt at %g" % p["volume"])
        if gv.get("music_lufs_party") is not None and gv.get("music_lufs_test") is not None:
            a2.errorbar([gv["volume_main"], gv["party_volume"]], [gv["music_lufs_test"], gv["music_lufs_party"]],
                        yerr=[[0, 2 * (gv.get("music_lufs_party_u_db") or 0)]] * 2, fmt="D", color=A.PAL["violet"],
                        label="songs (party)")
        a2.set_xlabel("Sonos volume")
        a2.set_title("Ladder")
        a2.legend(loc="lower right")
    else:
        a2.axis("off")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


# ======================================================================================================================
# the whole analysis
# ======================================================================================================================
def analyse_programme(run_dir, programme_dir, *, write_config: bool = False, audio_cal_file=None, fast: bool = False,
                      pass1=None, stack_gate: str = "auto", party_volume: float = PARTY_VOLUME, reuse: bool = False,
                      cpu_lock: bool = False) -> dict:
    """The analysis (module docstring) -> the gate dict (also analysis/gate.json); the full report goes to
    analysis/programme_report.json and .md. Raises SystemExit with a message when it can't do what was asked."""
    lock = None
    if cpu_lock:
        import fcntl
        CPU_LOCK.parent.mkdir(parents=True, exist_ok=True)
        lock = open(CPU_LOCK, "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        try:
            os.nice(19 - os.nice(0))
        except OSError:
            pass
        return _analyse(pathlib.Path(run_dir).expanduser().resolve(),
                        pathlib.Path(programme_dir).expanduser().resolve(),
                        write_config=write_config, audio_cal_file=audio_cal_file, fast=fast, pass1=pass1,
                        stack_gate=stack_gate, party_volume=party_volume, reuse=reuse)
    finally:
        if lock is not None:
            lock.close()


def _analyse(run_dir: pathlib.Path, pdir: pathlib.Path, *, write_config, audio_cal_file, fast, pass1, stack_gate,
             party_volume, reuse) -> dict:
    t_wall = time.time()
    SC = A._score_module()
    run = read_json(run_dir / "run.json")
    recm = read_json(run_dir / "rec.json")
    stream = run.get("stream") or recm.get("stream") or STREAM_DEFAULT
    prog = load_programme(pdir, SC)
    man = prog["manifest"]
    v_main = prog["volume_main"]
    synthetic = bool(run.get("synthetic"))
    out_dir = run_dir / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    for s in prog["segments"]:
        s["label"] = seg_label(s)
    log(f"programme {prog['name']}: {len(prog['segments'])} segments, {man.get('duration_s', 0) / 60:.1f} min, "
        f"main volume {v_main}; run {run_dir.name} ({stream})")

    # the recording, the programme, the host map
    rec, fs_rec, _ = A.read_audio(run_dir / "rec.wav")
    orig, _, _ = A.read_audio(prog["wav"])
    hmap = HostMap(run_dir, acoustic_offset_ms(stream))
    log(f"host map: {hmap.info.get('pieces', 0)} piece(s), skew {hmap.info.get('skew_ppm', float('nan')):+.1f} ppm"
        if hmap.ok else f"host map: none ({hmap.info.get('reason')})")

    # 1. alignment
    al = align_run(rec, orig, prog, run, hmap)
    del orig

    # 2. levels: the offline replay (unless --fast), the live features, BS.1770 here
    bus = read_bus(run_dir, stream)
    feats = bus["features"]
    live_gate = infer_gate_ref(feats[:, 1], feats[:, 2]) if len(feats) else {"n": 0, "ref_lufs": None}
    if stack_gate == "auto":
        gate_ref = live_gate["ref_lufs"] if live_gate.get("ref_lufs") is not None else GATE_REF_DEFAULT
        gate_why = "the live gate's reference, read back from music.features" if live_gate.get("ref_lufs") is not None \
            else "dsp's default (no live features to read the live gate from)"
    elif stack_gate == "default":
        gate_ref, gate_why = GATE_REF_DEFAULT, "dsp's default"
    else:
        gate_ref, gate_why = float(stack_gate), "given (--stack-gate)"
    gate_ref = round(float(gate_ref), 1)
    res = None
    if not fast:
        res = run_replay(run_dir / "rec.wav", work, gate_ref, reuse)
    series: dict = {}
    if res is not None and res.get("features"):
        ff = res["feature_fields"]
        F = np.array([[r[ff.index("t")], r[ff.index("lufs_s")] if r[ff.index("lufs_s")] is not None else np.nan]
                      for r in res["features"]], float)
        series["offline"] = (to_prog(al, F[:, 0]), F[:, 1])
    if len(feats) and hmap.ok:
        series["live"] = (to_prog(al, hmap.to_rec(feats[:, 0] / 1000.0)), feats[:, 1])
    tb, lb = st_loudness(rec)
    series["bs1770"] = (to_prog(al, tb), lb)
    fb_t, fb_db = floor_bins(rec)
    fb_u = to_prog(al, fb_t)
    settles = cue_settles(man, run)
    try:
        gains = rung_gains(rec, prog["wav"], prog, settles, al)
    except Exception as e:                               # noqa: BLE001  a cross-check never fails the analysis
        log(f"ladder gains: {type(e).__name__}: {e}")
        gains = []
    del rec
    src = "offline" if "offline" in series else ("live" if "live" in series else "bs1770")
    series["primary"] = series[src]
    seg_lv = segment_levels(prog, settles, series)
    sil = [s for s in prog["segments"] if s["kind"] == "silence"]

    def pooled(name, segs):
        vals = [window_values(*series[name], *level_span(s, settles)[:2]) for s in segs] if name in series else []
        vals = [v for v in vals if len(v)]
        return np.concatenate(vals) if vals else np.zeros(0)

    room = {"lufs_dsp": _med(pooled("primary", sil)), "lufs_bs1770": _med(pooled("bs1770", sil)),
            "silences": len(sil),
            "note": "the silences' 3 s loudness: dsp's reading (floored at %g) and BS.1770 here" % LUFS_FLOOR}
    log(f"levels ({src}): room tone {_f1(room['lufs_dsp'])} LUFS (dsp), {_f1(room['lufs_bs1770'])} (BS.1770)")

    # 3. the ladder
    rungs = [{"id": s["id"], "volume": _num(s.get("volume")),
              "lufs": (seg_lv[s["id"]].get("primary") or {}).get("median")}
             for s in prog["segments"] if s["kind"] == "ladder"]
    rt_for_ladder = room["lufs_bs1770"] if room["lufs_bs1770"] is not None else room["lufs_dsp"]
    lad = fit_ladder(rungs, rt_for_ladder, v_main, party_volume) if rungs else \
        {"ok": False, "reason": "the programme has no ladder", "rungs": []}
    lad_g = fit_ladder(gains, None, v_main, party_volume) if len(gains) >= 2 else {"ok": False}
    if lad.get("ok") and lad_g.get("ok"):
        lad["by_gain"] = {"rungs": gains, "model": lad_g["model"], "db_per_step": lad_g["db_per_step"],
                          "db_per_db_volume": lad_g["db_per_db_volume"], "fits": lad_g["fits"],
                          "rise_main_to_party": lad_g.get("rise_main_to_party"),
                          "note": "recording over programme on the excerpt's louder 100 ms frames (K-weighted): "
                                  "the Sonos' curve without the room's own sound; its rise's disagreement with the "
                                  "levels' widens the uncertainty"}
        rz, rg = lad.get("rise_main_to_party"), lad_g.get("rise_main_to_party")
        if rz and rg:
            rz["by_gain_db"] = rg["db"]
            rz["u_levels_only_db"] = rz["u_db"]
            rz["u_db"] = math.sqrt(rz["u_db"] ** 2 + ((rz["db"] - rg["db"]) / 2.0) ** 2)
    if lad.get("ok"):
        log(f"ladder: {lad['model']} ({lad['db_per_step']:.3f} dB/step linear, {lad['db_per_db_volume']:.3f} dB/dB "
            f"log); at {party_volume:g}: {lad['party']['lufs']:.2f} +- {lad['party']['u_db']:.2f} LUFS"
            + (f"; by gain {lad['by_gain']['db_per_step']:.3f} dB/step ({lad['by_gain']['model']})"
               if lad.get("by_gain") else ""))

    # 5. gate values (the songs at the main volume, plus the ladder's rise)
    # party music: the steady songs at the main volume; the piano is in the programme to test the clock (false
    # locks), not as party music, so it stays out (reported as a song of its own)
    steady = [s for s in prog["segments"] if s["kind"] == "music" and s.get("steady", True) and
              not piano_like(s) and v_main is not None and _num(s.get("volume")) == v_main]
    pool = pooled("primary", steady)
    song_meds = [(seg_lv[s["id"]].get("primary") or {}).get("median") for s in steady]
    song_meds = [v for v in song_meds if v is not None]
    test = _med(pool)
    ladder_from = None
    lad_use = lad
    p1_dir = None
    p1_rep: dict = {}
    if pass1 != "none":
        p1_dir = pathlib.Path(pass1).expanduser().resolve() if pass1 else (
            None if prog["name"].startswith("pass1-") else find_pass1(run_dir, stream))
        if p1_dir is not None:
            p1_rep = read_json(p1_dir / "analysis" / "programme_report.json")
            if not p1_rep:
                log(f"pass 1: no analysis/programme_report.json in {p1_dir}")
                p1_dir = None
    if not lad.get("ok") and p1_rep.get("ladder", {}).get("ok"):
        lad_use = p1_rep["ladder"]
        ladder_from = p1_rep["run"]["run"]
        if v_main is not None and lad_use.get("volume_main") != v_main:   # re-predict the rise from this run's volume
            pts = [{"id": r["id"], "volume": r["volume"], "lufs": r["lufs"]} for r in lad_use["rungs"]]
            lad_use = fit_ladder(pts, lad_use.get("room_tone_lufs"), v_main, party_volume)
        log(f"ladder: none usable here; pass 1's ({ladder_from})")
    # pass 2 (fewer songs) pools pass 1's songs at the same volume: the same mic, room and volume
    test_here = test
    p1_vals = np.asarray((p1_rep.get("gate") or {}).get("music_lufs_test_values") or [], float)
    p1_meds = [v for v in (p1_rep.get("gate") or {}).get("music_lufs_test_song_medians") or [] if v is not None]
    pooled_with = None
    if len(p1_vals) and (p1_rep.get("gate") or {}).get("volume_main") == v_main:
        test = _med(np.concatenate([pool, p1_vals]))
        song_meds_all = song_meds + p1_meds
        pooled_with = p1_rep["run"]["run"]
        log(f"music at the main volume: {_f1(test_here, '{:.2f}')} here, "
            f"{_f1((p1_rep.get('gate') or {}).get('music_lufs_test_here'), '{:.2f}')} in pass 1: both, "
            f"{_f1(test, '{:.2f}')}")
    else:
        song_meds_all = song_meds
    party = party_u = None
    rise = lad_use.get("rise_main_to_party") if lad_use.get("ok") else None
    if test is not None and rise:
        party, party_u = test + rise["db"], rise["u_db"]
    gv = {"music_lufs_test": test, "music_lufs_party": party, "music_lufs_party_u_db": party_u,
          "volume_main": v_main, "party_volume": party_volume,
          "music_lufs_test_here": test_here, "music_lufs_test_pass1": (p1_rep.get("gate") or {}).get(
              "music_lufs_test_here") if pooled_with else None, "music_lufs_test_pooled_with": pooled_with,
          "music_lufs_test_p10": float(np.percentile(song_meds_all, 10)) if song_meds_all else None,
          "music_lufs_test_p90": float(np.percentile(song_meds_all, 90)) if song_meds_all else None,
          "music_segments_used": len(song_meds_all), "levels_source": src,
          "music_lufs_test_values": [round(float(v), 2) for v in pool],
          "music_lufs_test_song_medians": song_meds,
          "ladder_lufs_party": (lad_use.get("party") or {}).get("lufs") if lad_use.get("ok") else None,
          "test_minus_ladder_db": (test - lad_use["main"]["lufs"]) if (test is not None and lad_use.get("ok")
                                                                       and lad_use.get("main")) else None,
          "ladder_from": ladder_from,
          "method": "music_lufs_party = music_lufs_test (median dsp lufs_s over the steady songs at the main volume, "
                    "the piano left out%s) + the ladder's rise from the main to the party volume (%s curve%s)"
                    % (", pass 1's songs pooled in" if pooled_with else "", lad_use.get("model"),
                       ", pass 1's ladder" if ladder_from else "")
          if party is not None else None}
    # what the gate becomes with music_lufs = party (show_service/config.py's offset, dsp.py's floor clamp)
    floors = floor_estimates(fb_u, fb_db, settles, sil, steady, rise["db"] if rise else None)
    gset: dict = {"floor": floors}
    if party is not None:
        off = max(GATE_OFFSET_MIN, min(GATE_OFFSET_MAX, party - GATE_REF_DEFAULT))
        ref = GATE_REF_DEFAULT + off

        def gate_at(level, cap):
            eff = min(ref, cap) if cap is not None else ref
            return eff, (float(smoothstep(eff - GATE_SPAN_LU, eff, level)) if level is not None else None)

        e_start, g_start = gate_at(party, floors.get("room_cap_lufs"))
        e_party, g_party = gate_at(party, floors.get("songs_party_cap_lufs", floors.get("room_cap_lufs")))
        e_test, g_test = gate_at(test, floors.get("songs_test_cap_lufs", floors.get("room_cap_lufs")))
        gset.update(music_lufs=party, offset_db=off, ref_lufs=ref, ref_party_start_lufs=e_start,
                    ref_party_lufs=e_party, ref_test_lufs=e_test, gate_at_party_start=g_start, gate_at_party=g_party,
                    gate_at_test=g_test,
                    note="with the mode's loudness_ref_lufs at dsp's default (-30); dsp lowers the reference to its "
                         "floor + %g + %g LU: ref_party_start with only the room in the floor's window, ref_party and "
                         "ref_test after %g s of continuous songs" % (FLOOR_ABOVE_LU, GATE_SPAN_LU, FLOOR_WINDOW_S))
    log(f"gate: music_lufs_test {_f1(test, '{:.2f}')}, party {_f1(party, '{:.2f}')} +- {_f1(party_u, '{:.2f}')}")

    # 4. the clock per segment
    clock: dict = {"available": False}
    if not al["ok"]:
        clock["reason"] = "the alignment failed (the Sonos' ticks are +-0.5 s)"
    elif not bus["clocks"]:
        clock["reason"] = "no music.clock in bus.jsonl or state.jsonl"
    elif not hmap.ok:
        clock["reason"] = f"no host map ({hmap.info.get('reason')})"
    else:
        fl_live = clock_flashes_u(bus_flashes(bus["clocks"]), hmap, al)
        b_live = clock_bodies_u(bus["clocks"], hmap, al)
        fl_off = b_off = None
        if res is not None:
            fl_off, b_off = offline_clock_u(res, al)
        rows = []
        for s in prog["segments"]:
            e = {"id": s["id"], "label": s["label"], "kind": s["kind"], "volume": _num(s.get("volume")),
                 "genre": s.get("genre"), "source_key": source_key(s), "piano": piano_like(s),
                 "align_resid_ms_max": (al["per_segment"].get(s["id"]) or {}).get("resid_ms_max"),
                 "live": clock_on_segment(s, fl_live, b_live, SC)}
            if fl_off is not None:
                e["offline"] = clock_on_segment(s, fl_off, b_off, SC)
            rows.append(e)
        clock = {"available": True, "segments": rows, "live_flashes": len(fl_live), "live_bodies": len(bus["clocks"]),
                 "offline_gate_ref": gate_ref if res is not None else None, "offline_gate_why": gate_why}
        # parity: the replay starts cold (dsp's floor unknown for its first FLOOR_MIN_S), live had run for hours
        warm = float(to_prog(al, FLOOR_MIN_S + 5.0))
        pair = [(r["live"].get("f"), r["offline"].get("f")) for r, s in zip(rows, prog["segments"])
                if r.get("offline") and r["kind"] != "silence" and r["live"].get("f") is not None
                and r["offline"].get("f") is not None and s["t0"] >= warm]
        if pair:
            d = np.abs(np.array([a - b for a, b in pair]))
            clock["parity"] = {"segments": len(pair), "f_abs_diff_mean": float(d.mean()),
                               "f_abs_diff_max": float(d.max()),
                               "offline_gate_ref": gate_ref, "from_programme_s": warm}
        for r in rows:
            if r["kind"] == "music":
                v = r["live"]
                log(f"clock {r['label']}: live F {_f1(v.get('f'), '{:.2f}')}, hit25 {_f1(v.get('hit25'), '{:.2f}')}, "
                    f"cover {_f1(v.get('cover_beat'), '{:.2f}')}, gate at {_f1(v.get('t_first_beat_gate_s'))} s"
                    + (f"; offline F {_f1(r['offline'].get('f'), '{:.2f}')}" if r.get("offline") else ""))

    if clock.get("available"):
        cu = {"run": run.get("run", run_dir.name), "programme": prog["name"], "live": clocks_json(fl_live, b_live)}
        if fl_off is not None:
            cu["offline"] = clocks_json(fl_off, b_off)
        (out_dir / "clock_u.json").write_text(json.dumps(A._clean(cu), separators=(",", ":")), encoding="utf-8")

    # before/after (pass 1 against this run: the songs both played, over the audio both played)
    before_after = None
    p1_clk = read_json(p1_dir / "analysis" / "clock_u.json") if p1_dir is not None else {}
    if clock.get("available") and p1_clk.get("live") and p1_rep.get("programme", {}).get("dir"):
        try:
            p1_prog = load_programme(pathlib.Path(p1_rep["programme"]["dir"]), SC)
            for s in p1_prog["segments"]:
                s["label"] = seg_label(s)
            ba = before_after_rows(prog, (fl_live, b_live), p1_prog, clocks_from_json(p1_clk["live"]), SC)
        except (OSError, ValueError, KeyError) as e:
            log(f"before/after: pass 1's programme unreadable ({type(e).__name__}: {e})")
            ba = []
        if ba:
            before_after = {"pass1_run": p1_rep["run"]["run"], "rows": ba,
                            "pass1_gate_ref": (p1_rep.get("live_gate") or {}).get("ref_lufs"),
                            "this_gate_ref": live_gate.get("ref_lufs")}

    # stream health, the recorder
    sh = None
    if hmap.ok:
        spans = [(float(to_rec_t(al, 0.0)), float(to_rec_t(al, float(man.get("duration_s") or 0.0))))]
        a_, b_ = hmap.linear()
        try:
            sh = A.stream_health(run_dir, a_, b_, spans)
        except Exception as e:                               # noqa: BLE001  a malformed state.jsonl
            log(f"stream health: {type(e).__name__}: {e}")

    ladder_summary = {k: lad_use.get(k) for k in ("ok", "model", "db_per_step", "db_per_db_volume", "volumes",
                                                  "top_rung", "extrapolation_steps", "room_tone_lufs")} \
        if lad_use.get("ok") else {"ok": False, "reason": lad_use.get("reason")}
    if lad_use.get("ok"):
        ladder_summary.update(rungs=[{"volume": r["volume"], "lufs": r["lufs"], "used": r["used"]}
                                     for r in lad_use["rungs"]],
                              party=lad_use["party"], main=lad_use.get("main"), rise=lad_use.get("rise_main_to_party"),
                              source_run=ladder_from or run.get("run", run_dir.name))
    gate_out = {"music_lufs_test": test, "music_lufs_party": party, "volume_main": v_main, "ladder": ladder_summary,
                "music_lufs_party_u_db": party_u, "party_volume": party_volume, "stream": stream,
                "run": run.get("run", run_dir.name), "programme": prog["name"], "levels_source": src,
                "live_gate_ref_lufs": live_gate.get("ref_lufs")}

    # config
    config_written = None
    if write_config:
        target = pathlib.Path(audio_cal_file).expanduser().resolve() if audio_cal_file else CONFIG_JSON
        if synthetic and target == CONFIG_JSON.resolve():
            raise SystemExit("refused: a synthetic run never writes config/room/audio_cal.json (use --audio-cal-file)")
        if party is None:
            raise SystemExit("refused: no party level (no usable ladder here or in pass 1, or no steady music at the "
                             "main volume): config/room/audio_cal.json left as it is")
        summary = {"music_lufs": party,
                   "music_lufs_method": gv["method"],
                   "music_lufs_note": "the music at this mic at party volume as dsp's loudness gate reads it (median "
                                      "lufs_s), predicted from the test volume by the Sonos volume ladder; "
                                      "show_service "
                                      "moves this stream's gate reference by music_lufs - (-30) dB",
                   "music_lufs_u_db": party_u, "music_lufs_test": test, "music_lufs_test_here": test_here,
                   "music_lufs_test_pass1": gv["music_lufs_test_pass1"], "test_volume": v_main,
                   "party_volume": party_volume, "ladder": ladder_summary, "room_tone_lufs": room["lufs_bs1770"],
                   "room_tone_lufs_dsp": room["lufs_dsp"], "levels_source": src, "programme": prog["name"],
                   "ladder_run": ladder_from or run.get("run", run_dir.name),
                   "live_f_mean": float(np.mean([r["live"]["f"] for r in clock["segments"]
                                                 if r["kind"] == "music" and r["live"].get("f") is not None]))
                   if clock.get("available") and any(r["kind"] == "music" and r["live"].get("f") is not None
                                                     for r in clock["segments"]) else None}
        entry = {"run": run.get("run", run_dir.name), "measured_at": run.get("started_utc") or recm.get("started_utc"),
                 "device": run.get("device") or recm.get("device"), "summary": A._clean(summary)}
        write_audio_cal(target, stream, entry)
        config_written = str(target)
        log(f"config: {target} streams.{stream}.summary.music_lufs = {party:.2f}")

    rep = {"version": 1, "analysed_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "run": {"run": run.get("run", run_dir.name), "stream": stream,
                   "device": run.get("device") or recm.get("device"),
                   "started_utc": run.get("started_utc") or recm.get("started_utc"), "synthetic": synthetic,
                   "dir": str(run_dir)},
           "programme": {"name": prog["name"], "dir": str(pdir), "duration_s": man.get("duration_s"),
                         "volume_main": v_main, "volume_cues": man.get("volume_cues"),
                         "segments": [{k: v for k, v in s.items() if not k.startswith("_")} for s in prog["segments"]]},
           "modes": {"write_config": write_config, "fast": fast, "stack_gate": stack_gate},
           "alignment": al, "host_map": hmap.info, "recorder": {k: recm.get(k) for k in ("gaps", "gap_s", "race",
                                                                                        "reconnects", "unmatched",
                                                                                        "other_stream_frames")},
           "stream_health": sh, "live_gate": live_gate, "bus_types": bus["types"], "bus_delay": bus["bus_delay"],
           "levels": {"source": src, "segments": seg_lv, "room_tone": room,
                      "cue_settles": [{"settled_s": a, "cue_s": b, "volume": c} for a, b, c in settles]},
           "ladder": lad, "ladder_used": ladder_summary, "gate": gv, "gate_setting": gset, "clock": clock,
           "before_after": before_after, "pass1_run": str(p1_dir) if p1_dir else None,
           "config_written": config_written, "gate_json": gate_out}
    rep["wall_s"] = time.time() - t_wall
    rep = A._clean(rep)
    (out_dir / "programme_report.json").write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    (out_dir / "gate.json").write_text(json.dumps(A._clean(gate_out), indent=1) + "\n", encoding="utf-8")
    try:
        make_fig(out_dir / "figs" / "levels.png", prog, series, lad_use, gv)
    except Exception as e:                                   # noqa: BLE001  a figure never fails the analysis
        log(f"figure: {type(e).__name__}: {e}")
    (out_dir / "programme_report.md").write_text(report_md(rep), encoding="utf-8")
    log(f"-> {out_dir / 'programme_report.md'} ({rep['wall_s']:.0f} s)")
    return A._clean(gate_out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=pathlib.Path,
                    help="calibrate.py's run directory (rec.wav, tapmap.jsonl, bus.jsonl, ...)")
    ap.add_argument("--programme", type=pathlib.Path, default=None,
                    help="the programme directory (default: run.json programme.dir)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write-config", action="store_true",
                      help="also set config/room/audio_cal.json (or --audio-cal-file) streams.<stream>")
    mode.add_argument("--gate-test-only", action="store_true", help="the gate values only: print, gate.json; no config")
    ap.add_argument("--audio-cal-file", type=pathlib.Path, default=None,
                    help=f"the file --write-config merges into (default {CONFIG_JSON.relative_to(REPO)})")
    ap.add_argument("--fast", action="store_true",
                    help="no offline replay: levels from the live bus (else BS.1770 here); no live vs offline parity")
    ap.add_argument("--pass1", default=None,
                    help="pass 1's run dir (its ladder and before/after); default: the newest sibling pass1-* run; "
                         "'none' for no pass 1")
    ap.add_argument("--stack-gate", default="auto",
                    help="the offline replay's gate reference: auto (the live gate's, read back), default (-30), or "
                         "LUFS")
    ap.add_argument("--party-volume", type=float, default=PARTY_VOLUME)
    ap.add_argument("--reuse", action="store_true", help="reuse the last offline replay of this recording at this gate")
    ap.add_argument("--lock", action="store_true", help="take the CPU lock in-process (never inside an outer flock)")
    a = ap.parse_args(argv)
    pdir = a.programme
    if pdir is None:
        pd = (read_json(a.run_dir / "run.json").get("programme") or {}).get("dir")
        if not pd:
            ap.error("--programme is needed (run.json names none)")
        pdir = pathlib.Path(pd)
    if a.stack_gate not in ("auto", "default"):
        try:
            float(a.stack_gate)
        except ValueError:
            ap.error("--stack-gate: auto, default or a number")
    out = analyse_programme(a.run_dir, pdir, write_config=a.write_config, audio_cal_file=a.audio_cal_file,
                            fast=a.fast, pass1=a.pass1, stack_gate=a.stack_gate, party_volume=a.party_volume,
                            reuse=a.reuse, cpu_lock=a.lock)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
