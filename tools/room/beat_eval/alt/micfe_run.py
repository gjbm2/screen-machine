#!/usr/bin/env python3
"""Candidate "micfe" runner: run_stack.py's live-stack replay with the mic-compensation front end (micfe.py) on the
rhythm lane, tracked by our clock or by madmom's stock online tracker (2-net LSTM + online DBN).

  ~/.venvs/sm-show/bin/python tools/room/beat_eval/alt/micfe_run.py AUDIO [run_stack.py options]
      [--fe VARIANT | --fe-json '{...}'] [--fe-model model.json] [--fe-dsp] [--tracker clock|dbn]
      [--act exact|batch] [--audio-cache DIR]
  run_batch.py ... --runner tools/room/beat_eval/alt/micfe_run.py -- --fe coheq --tracker clock

Everything run_stack.py does stays as it is (decode, the room model, the INT16 wire, 128-frame blocks, host time,
dsp, the clock, the flashes, the JSON score.py reads); this only changes what the rhythm lane hears:
- ``--fe``: a micfe.VARIANTS name (``none`` = run_stack.py exactly), or ``--fe-json`` a config. Its per-mic
  parameters come from ``--fe-model`` (default: run_stack's ``--degrade`` model, else the pc22-mic calibration in
  config/room/audio_cal.json): the front end is keyed by the stream's calibration, as the gate offset is. In
  condition A (clean audio) that is the pc22 front end on audio that never went through that mic: the "does it hurt
  a good signal" check.
- The front end runs causally over the stream (micfe.selftest: any block cut gives the same samples), so it is
  computed once over the whole stream here and the clock gets each block's slice, with its time base moved back by
  the front end's measured onset latency (``--fe-latency-ms``, default micfe.measure_latency; 0-1 ms so far).
- dsp (the loudness gate, the hits that reach Clock.observe_hit) hears the raw stream, so the calibrated gate
  (-48.64) means what it means live; ``--fe-dsp`` gives dsp the front end's output too.
- ``--tracker clock`` (default): show_service's Clock. ``--act exact`` (default) runs madmom inside the clock per hop,
  as live; ``--act batch`` computes the same activation once over the lane (the clock's AGC copied exactly, soxr,
  madmom's causal frames) and hands the clock that frame per hop: about 3x cheaper, for screening. It differs from
  exact only after the clock resets madmom (a quiet room ending, a hole), where exact starts the LSTM clean.
- ``--tracker dbn``: madmom's stock online tracker on the same lane activation: RNNBeatProcessor(online=True), the
  first 2 nets, + DBNBeatTrackingProcessor(online=True, 45-200 bpm) (its forward filter, beats reported as
  process_online reports them). Published as a clock line: a body at each DBN beat (anchor = the beat frame less
  DBN_LAG_S, 12.5 ms, calibrated on the exact synthetic loops; bpm = the DBN state's interval), arriving when the
  frame's audio has arrived (+ rx latency), so flashes are the line's causal predictions, as for our clock. Its conf: the forward posterior's mass within +-6 % of a period of its
  best phase (P(the beat phase is right)), with the clock's gate rule (beat on 0.70 / off 0.60, dmx 0.45 / 0.35,
  2-bar dwell). No dsp, no quiet gate.
- ``--audio-cache DIR``: keeps each track's room-model output (int16 .npy keyed by audio, seed, model, snr,
  pre-roll), so reruns skip roomsim's subprocess. The audio is identical either way.

Output: run_stack.py's JSON, plus ``candidate`` (name "micfe", variant, front-end config and description, its
fingerprint, tracker, act mode, front-end cost per audio second measured streaming 128-frame blocks, the
tracker's cost) and ``stack.candidate`` (the same id, so score.py's stack NOTE tells candidates apart).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import sys
import time
from typing import Optional

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import run_stack as rs  # noqa: E402  (imports show_service; sm-show's env)

import numpy as np  # noqa: E402

import micfe  # noqa: E402
from show_service import clock as clockmod  # noqa: E402
from show_service import contract  # noqa: E402

REPO = HERE.parents[3]
AUDIO_CAL = REPO / "config" / "room" / "audio_cal.json"
STREAM_CAL = "pc22-mic"
DBN_LAG_S = 0.0125              # the DBN's beat frame vs the sound: -5 / -10 ms offsets at the clock's 20 ms on the exact
                                # synthetic loops (beat-eval/synth house120, hiphop92), so 12.5 ms
_ORIG = {"Replay": rs.Replay, "degrade": rs.degrade, "fp": rs.stack_fingerprint}   # main() patches these per call
_LAT: dict = {}                 # fe id -> measured latency (s), and -> streaming cost (ms/s): once per process
_COST: dict = {}


class _NoAct:
    """A stand-in activation for the DBN's unused Clock (no madmom load)."""
    name, lag_s, floor = "none", 0.0, 0.0

    def process_hop(self, hop):
        return 0.0

    def reset(self):
        pass


def default_model() -> Optional[str]:
    try:
        s = json.loads(AUDIO_CAL.read_text())["streams"][STREAM_CAL]
        return str(REPO / s["model"])
    except (OSError, ValueError, KeyError):
        return None


# -- the lane activation, computed once (--act batch, and the DBN) -----------------------------------------------------
def clock_agc(y: np.ndarray, fs: int = rs.FS) -> np.ndarray:
    """Clock._lane_in's AGC, exactly (AGC_CHUNK steps aligned on n from 0; a sample takes the chunks-before gain)."""
    y = np.asarray(y, np.float32)
    out = np.empty_like(y)
    C = clockmod.AGC_CHUNK
    dt = C / fs
    k1, k2, k3 = 1.0 - math.exp(-dt / 0.4), 1.0 - math.exp(-dt / 10.0), 1.0 - math.exp(-dt / 3.0)
    init, acc_p, acc_t, pw, db = False, 0.0, 0.0, 0.0, 0.0
    g = np.float32(1.0)
    T = clockmod.AGC_TARGET_DB
    for i in range(0, len(y), C):
        seg = y[i:i + C]
        out[i:i + C] = seg * g
        if len(seg) < C:
            break
        p = float(np.dot(seg, seg)) / C
        if not init:
            if 10.0 * math.log10(p + 1e-20) >= -65.0:
                acc_p += p * dt
                acc_t += dt
                if acc_t >= 0.5:
                    pw = acc_p / acc_t
                    db = min(36.0, max(-20.0, T - 10.0 * math.log10(pw + 1e-20)))
                    init = True
        else:
            pw += (p - pw) * k1
            lvl = 10.0 * math.log10(pw + 1e-20)
            if lvl >= -65.0:
                want = min(36.0, max(-20.0, T - lvl))
                err = want - db
                db += err * (k2 if err > 0 else k3)
        g = np.float32(10.0 ** (db / 20.0))
    return out


def lane_activation(y48: np.ndarray, nets: int = 2) -> np.ndarray:
    """madmom's online activation over the clock's lane: AGC, soxr HQ to 44.1 kHz, causal 2048 frames ending at
    sample i x 441 (A[i] is the frame the clock computes when its lane has i x 441 samples)."""
    import warnings
    import soxr
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from madmom.audio.signal import Signal
        from madmom.features.beats import RNNBeatProcessor
        from madmom.models import BEATS_LSTM
    lane = soxr.resample(clock_agc(y48), rs.FS, clockmod.FS, quality="HQ").astype(np.float32)
    rnn = RNNBeatProcessor(online=True, nn_files=list(BEATS_LSTM[:nets]), origin="online", fps=clockmod.FPS)
    a = np.asarray(rnn(Signal(lane, sample_rate=clockmod.FS)), np.float64).reshape(-1)
    return np.maximum(a, 0.0)


# -- the clock with the front end --------------------------------------------------------------------------------------
class FEReplay(rs.Replay):
    """run_stack.Replay, the clock lane fed the front end's output (and dsp too with fe_dsp)."""

    fe: micfe.FrontEnd
    lat_s = 0.0
    act_mode = "exact"
    nets = 2
    fe_dsp = False

    def run(self, y, ml_events, lost=None):
        self.raw = y
        t = time.perf_counter()
        self.y_fe = self.fe.process(y) if not self.fe.identity else np.asarray(y, np.float32)
        self.wall_fe = time.perf_counter() - t
        self.wall_act = 0.0
        if self.act_mode == "batch":
            t = time.perf_counter()
            A = lane_activation(self.y_fe, self.nets)
            self.wall_act = time.perf_counter() - t
            c = self.clock

            def _inline(hop, t_raw, _c=c, _A=A):
                i = _c._m_done // clockmod.HOP
                return _c._frame(float(_A[i]) if i < len(_A) else 0.0, t_raw)
            c._inline = _inline
        super().run(y, ml_events, lost)

    def block(self, n0, blk, flags, arrive=None):
        t0 = rs.T0 + n0 / rs.FS
        self.now = t0 + len(blk) / rs.FS + self.rx_latency_s if arrive is None else arrive
        pcm = blk.reshape(-1, 1)
        pcm_c = pcm if flags & contract.AUDIO_GAP else self.y_fe[n0:n0 + len(blk)].reshape(-1, 1)
        tc = t0 - self.lat_s
        f = self.features
        try:
            if self.fe_dsp:
                f.process(rs.STREAM, n0, tc, pcm_c, rs.FS, flags)
            else:
                f.process(rs.STREAM, n0, t0, pcm, rs.FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["features.process"] += 1
        try:
            lf = f.latest()
            self.clock.set_level(lf.get("gate") if isinstance(lf, dict) else None, tc)
            self.clock.process(rs.STREAM, n0, tc, pcm_c, rs.FS, flags)
        except Exception:                                    # noqa: BLE001
            self.errors["clock.process"] += 1
        self.clock.wait_beat()


# -- madmom's stock online tracker, published as a clock line ----------------------------------------------------------
class DBNReplay(rs.Replay):
    """The DBN on the lane activation (module docstring); dsp and the clock are not run."""

    fe: micfe.FrontEnd
    lat_s = 0.0
    nets = 2
    PHASE_TOL = 0.06
    CHUNK = 500

    def __init__(self, *args, **kw):
        mk = clockmod.make_activation
        clockmod.make_activation = lambda *a, **k: _NoAct()
        try:
            super().__init__(*args, **kw)
        finally:
            clockmod.make_activation = mk

    def run(self, y, ml_events, lost=None):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from madmom.features.beats import DBNBeatTrackingProcessor
        self.raw = y
        t = time.perf_counter()
        y_fe = self.fe.process(y) if not self.fe.identity else np.asarray(y, np.float32)
        self.wall_fe = time.perf_counter() - t
        t = time.perf_counter()
        A = lane_activation(y_fe, self.nets)
        self.wall_act = time.perf_counter() - t
        t = time.perf_counter()
        fps = clockmod.FPS
        dbn = DBNBeatTrackingProcessor(fps=fps, min_bpm=45, max_bpm=200, online=True)
        dbn.reset()
        st, om, hmm = dbn.st, dbn.om, dbn.hmm
        pos_s = np.asarray(st.state_positions, float)
        int_s = np.asarray(st.state_intervals, float)
        last_beat, k = 0.0, -1
        gate, gate_t, gate_up = "none", -1e9, False
        bpm_pub = 0.0
        last_key = None
        anchor = None
        conf_s = 0.0
        dwell = 0.0
        import scipy.sparse as spm
        NB = 100                                            # phase bins for the conf (1 % of a period each)
        pbin = np.floor(pos_s * NB).astype(int) % NB
        S = spm.csr_matrix((np.ones(len(pbin)), (np.arange(len(pbin)), pbin)), shape=(len(pbin), NB))
        tolb = int(round(self.PHASE_TOL * NB))
        for c0 in range(0, len(A), self.CHUNK):
            a = A[c0:c0 + self.CHUNK].astype(np.float32)
            fwd = hmm.forward(a, reset=(c0 == 0))
            states = np.argmax(fwd, axis=1)
            beats = om.pointers[states] == 1
            H_ = np.asarray(S.T.dot(fwd.T).T)               # (frames, NB): the posterior's mass per phase bin
            H3 = np.concatenate([H_, H_, H_], axis=1)
            cs = np.concatenate([np.zeros((len(a), 1)), np.cumsum(H3, axis=1)], axis=1)
            b0 = pbin[states] + NB
            rows = np.arange(len(a))
            confs = cs[rows, b0 + tolb + 1] - cs[rows, b0 - tolb]
            for j in range(len(a)):
                i = c0 + j
                s = states[j]
                conf = float(confs[j])
                conf_s = conf if i == 0 else 0.9 * conf_s + 0.1 * conf
                t_frame = i / fps
                h = rs.T0 + t_frame + self.rx_latency_s
                new_beat = False
                if beats[j]:
                    cur = (i) / fps
                    if cur >= last_beat + 60.0 / dbn.max_bpm:
                        last_beat = cur
                        new_beat = True
                if new_beat:
                    k += 1
                    bpm_pub = 60.0 * fps / int_s[s]
                    anchor = rs.T0 + t_frame - DBN_LAG_S - self.lat_s
                # the gate: the clock's hysteresis and a 2-bar dwell
                want = gate
                if conf_s >= clockmod.BEAT_ON or (gate == "beat" and conf_s >= clockmod.BEAT_OFF):
                    want = "beat"
                elif conf_s >= clockmod.DMX_ON or (gate in ("beat", "dmx") and conf_s >= clockmod.DMX_OFF):
                    want = "dmx"
                else:
                    want = "none"
                if anchor is None:
                    want = "none"
                if want != gate:
                    dwell = 8 * 60.0 / bpm_pub if bpm_pub > 0 else 0.0
                    if h - gate_t >= dwell or (want == "none" and anchor is None):
                        gate, gate_t = want, h
                state = "searching" if anchor is None else ("locked" if gate != "none" else "holding")
                key = (k, round(bpm_pub, 3), gate, state, round(conf_s * 50))
                if anchor is not None and (key != last_key):
                    last_key = key
                    self.n_published += 1
                    self.bodies.append((h, {"t_ms": h * 1000.0, "anchor_ms": anchor * 1000.0, "bpm": bpm_pub,
                                            "beat_at_anchor": k, "beats_per_bar": 4, "bar_anchor_beat": 0,
                                            "conf": round(conf_s, 3), "bar_conf": 0.0, "state": state, "gate": gate,
                                            "epoch": 0, "stability": None}))
        self.wall_dbn = time.perf_counter() - t
        self.now = rs.T0 + len(y) / rs.FS + self.rx_latency_s


# -- main --------------------------------------------------------------------------------------------------------------
def _cached_degrade(cache: pathlib.Path):
    orig = _ORIG["degrade"]

    def degrade(x, model, seed, snr_db):
        key = hashlib.sha256(json.dumps([hashlib.sha256(np.ascontiguousarray(x).tobytes()).hexdigest(),
                                         str(model), os.path.getmtime(model), seed, snr_db]).encode()).hexdigest()[:20]
        p = cache / f"{key}.npy"
        try:
            return np.load(p).astype(np.float32) / 32768.0
        except (OSError, ValueError):
            pass
        y = orig(x, model, seed, snr_db)
        cache.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp.npy")
        np.save(tmp, np.clip(np.round(y * 32768.0), -32768, 32767).astype(np.int16))
        tmp.replace(p)
        return y
    return degrade


def stream_cost(fe: micfe.FrontEnd, y: np.ndarray, seconds: float = 10.0) -> float:
    """ms of front end per second of audio, streaming 128-frame blocks over the stream's first ``seconds``."""
    return micfe.stream_cost(fe, np.asarray(y[:int(seconds * rs.FS)], np.float32), rs.BLOCK)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--fe", default="none")
    ap.add_argument("--fe-json", default=None)
    ap.add_argument("--fe-model", default=None)
    ap.add_argument("--fe-latency-ms", type=float, default=None)
    ap.add_argument("--fe-dsp", action="store_true")
    ap.add_argument("--tracker", choices=("clock", "dbn"), default="clock")
    ap.add_argument("--act", choices=("exact", "batch"), default="exact")
    ap.add_argument("--nets", type=int, default=2)
    ap.add_argument("--audio-cache", type=pathlib.Path, default=None)
    ap.add_argument("--help-micfe", action="store_true")
    a, rest = ap.parse_known_args(argv)
    if a.help_micfe:
        print(__doc__)
        return 0
    if "--degrade" in rest and a.fe_model is None:
        a.fe_model = rest[rest.index("--degrade") + 1]
    model = a.fe_model or default_model()
    cfg = json.loads(a.fe_json) if a.fe_json else micfe.VARIANTS[a.fe]
    variant = "custom" if a.fe_json else a.fe
    mic = micfe.mic_params(model) if model else None
    fe = micfe.FrontEnd(cfg, mic)
    fe_id = micfe.fingerprint(cfg, mic)
    if a.fe_latency_ms is not None:
        lat = a.fe_latency_ms / 1000.0
    else:
        if fe_id not in _LAT:
            _LAT[fe_id] = 0.0 if fe.identity else micfe.measure_latency(fe)
        lat = _LAT[fe_id]
    fe.reset()
    cand = {"name": "micfe", "variant": variant, "fe_id": fe_id, "tracker": a.tracker,
            "act": a.act if a.tracker == "clock" else "batch", "nets": a.nets, "fe_dsp": a.fe_dsp,
            "fe_model": model, "fe_latency_ms": round(lat * 1000, 2), "fe": fe.describe()}
    cand["id"] = "c" + hashlib.sha256(json.dumps({k: cand[k] for k in ("fe_id", "tracker", "act", "nets", "fe_dsp")},
                                                 sort_keys=True).encode()).hexdigest()[:10]
    holder: dict = {}

    if a.tracker == "clock":
        class R(FEReplay):
            pass
    else:
        class R(DBNReplay):
            pass
    R.fe, R.lat_s, R.nets = fe, lat, a.nets
    if a.tracker == "clock":
        R.act_mode, R.fe_dsp = a.act, a.fe_dsp
    rs.Replay, rs.degrade, rs.stack_fingerprint = _ORIG["Replay"], _ORIG["degrade"], _ORIG["fp"]
    orig_init = R.__init__

    def init(self, *args, **kw):
        orig_init(self, *args, **kw)
        holder["rp"] = self
    R.__init__ = init
    rs.Replay = R
    if a.audio_cache:
        rs.degrade = _cached_degrade(a.audio_cache.expanduser())
    fp = _ORIG["fp"]

    def stack_fp():
        d = fp()
        d["candidate"] = cand["id"]
        if a.tracker == "dbn":
            d["id"] = "dbn-" + d["id"]
        return d
    rs.stack_fingerprint = stack_fp
    rc = rs.main(rest)
    out = pathlib.Path(rest[rest.index("--out") + 1]) if "--out" in rest else None
    rp = holder.get("rp")
    if out is None:
        audio = next(x for x in rest if not x.startswith("-"))
        tid = rest[rest.index("--id") + 1] if "--id" in rest else pathlib.Path(audio).stem
        out = rs.OUT_DIR / f"{tid}.json"
    if rc == 0 and out.exists() and rp is not None:
        r = json.loads(out.read_text())
        audio_s = float(r.get("audio_s") or 0.0) or 1.0
        if fe_id not in _COST:
            _COST[fe_id] = round(stream_cost(fe, np.asarray(getattr(rp, "raw", np.zeros(0)), np.float32)), 3)
        cand["fe_cost_ms_per_s"] = _COST[fe_id]
        cand["wall_s"] = {"fe": round(getattr(rp, "wall_fe", 0.0), 3), "act_batch": round(getattr(rp, "wall_act", 0.0), 3),
                          "dbn": round(getattr(rp, "wall_dbn", 0.0), 3)}
        cand["tracker_ms_per_s"] = round(1000.0 * (r["wall_s"]["replay"] if a.tracker == "clock"
                                                   else getattr(rp, "wall_dbn", 0.0)) / audio_s, 2)
        r["candidate"] = cand
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(r, separators=(",", ":"), default=float))
        tmp.replace(out)
        print(f"  micfe: {variant} ({cand['id']}), tracker {a.tracker}/{cand['act']}, fe {cand['fe_cost_ms_per_s']} "
              f"ms per audio s, latency {cand['fe_latency_ms']} ms")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
