"""Realism stage: the engine's raw render -> a piano in a room.

apply(audio, sr, perf, inst, room, progress=None, engine=None, stats=None) -> (M, 2) float32, M >= len(audio):
sample 0 stays MIDI time 0; the end grows by the resonance and room tails (trimmed where they fall below
-120 dBFS). Stages, in order (each documented in its piano_render/dsp module):
  1. detune     (tone.py)      extra engine renders at each "detune_cents" offset, mixed at 1/sqrt(voices)
  2. una corda  (tone.py)      CC67: smoothed high shelf (-4 dB above 1.8 kHz) and -2.5 dB at full pedal
                                ("una_corda": false turns it off)
  3. EQ         (filters.py)   the instrument's "eq" list
  4. width      (tone.py)      mid/side, side x "width"
  5. resonance  (resonance.py) the sympathetic / sustain-pedal bank at "resonance_db" (null = off), tuned to the
                                instrument's measured partials (analysis.py, cached per instrument), panned by key
                                ("resonance_pan", default 0.3)
  6. room       (room.py)      convolution with the room's IR (true stereo) or its synthetic IR, at "wet_db"
The dry path is causal filtering only: nothing moves earlier, nothing is delayed beyond the filters' own phase.

apply_dry(audio, sr, inst): what a render with realism off gets: the instrument's EQ only (its gain_db is the
master stage's).

stats (optional dict) receives each stage's timings and settings. Everything after the detune works in place on one
preallocated buffer, chunk by chunk: a fresh full-length array costs about 0.5 s of page faults on this box.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import numpy as np

# The DSP modules (scipy, numba) are imported where they are used, not here: the render cache's key reads VERSION,
# and a cached start (piano_render.session -> render cache hit) must not pay for scipy.signal (about 1.8 s).
log = logging.getLogger("piano_render.realism")

_REEXPORTS = {"apply_eq": "filters", "apply_sos_inplace": "filters", "biquad_sos": "filters", "eq_sos": "filters",
              "apply_width": "tone"}
__all__ = ["VERSION", "apply", "apply_dry", *_REEXPORTS]


def __getattr__(name: str):
    """The re-exported DSP helpers (realism.apply_eq etc.), imported on first use."""
    if name in _REEXPORTS:
        import importlib
        return getattr(importlib.import_module(f"{__package__}.dsp.{_REEXPORTS[name]}"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

VERSION = "realism-1"     # part of the render cache key: change it whenever the output changes
END_FLOOR = 10 ** (-120 / 20)
Progress = Optional[Callable[[float, float], None]]

# Share of the realism stage's progress per step (for the progress file only).
_SPANS = {"detune": (0.0, 0.3), "analysis": (0.3, 0.4), "resonance": (0.4, 0.8), "room": (0.8, 1.0)}


class _Prog:
    def __init__(self, cb: Progress, total: float):
        self.cb, self.total = cb, max(total, 1e-9)

    def step(self, name: str) -> Optional[Callable[[float, float], None]]:
        if self.cb is None:
            return None
        a, b = _SPANS[name]

        def f(done: float, tot: float) -> None:
            frac = min(1.0, max(0.0, done / tot)) if tot else 1.0
            self.cb((a + (b - a) * frac) * self.total, self.total)
        return f

    def at(self, name: str) -> None:
        if self.cb is not None:
            self.cb(_SPANS[name][0] * self.total, self.total)


def apply_dry(audio: np.ndarray, sr: int, inst: dict) -> np.ndarray:
    """Realism off: the instrument's EQ only (the input itself when there is none)."""
    from .dsp.filters import apply_sos_inplace, eq_sos
    audio = np.asarray(audio, np.float32)
    sos = eq_sos(inst.get("eq") or [], sr)
    if sos is None:
        return np.ascontiguousarray(audio)
    out = np.array(audio, np.float32, copy=True)
    apply_sos_inplace(out, out.shape[0], sos)
    return out


def apply(audio: np.ndarray, sr: int, perf, inst: dict, room: Optional[dict], progress: Progress = None,
          engine=None, stats: Optional[dict] = None) -> np.ndarray:
    from .dsp import room as _room
    from .dsp import tone as _tone
    from .dsp.filters import apply_sos_inplace, eq_sos
    st = stats if stats is not None else {}
    st["version"] = VERSION
    audio = np.asarray(audio, np.float32)
    n0 = audio.shape[0]
    prog = _Prog(progress, n0 / sr)
    t_all = time.process_time()

    # 1. detuned copies
    cents = [float(c) for c in (inst.get("detune_cents") or []) if float(c) != 0.0]
    dry = audio
    if cents:
        if engine is None or perf is None:
            st["detune"] = {"skipped": "no engine to render the detuned copies"}
        else:
            c0 = time.process_time()
            voices = [audio]
            for i, c in enumerate(cents):
                cb = prog.step("detune")
                sub = (lambda d, t, i=i: cb((i + d / t if t else i + 1) / len(cents), 1.0)) if cb else None
                voices.append(np.asarray(engine.render(perf, sr=sr, progress=sub, pitch_cents=c), np.float32))
            dry = _tone.mix_voices(voices)
            del voices
            st["detune"] = {"cents": cents, "voices": len(cents) + 1, "cpu_s": round(time.process_time() - c0, 3)}

    # One buffer for everything that follows (fresh full-length arrays cost ~0.5 s of page faults each here),
    # with room for the bank's ringing and the room's tail.
    rdb = inst.get("resonance_db")
    use_bank = rdb is not None and perf is not None
    from .dsp import resonance as _res
    room_n = 0
    if room:
        try:
            room_n = _room.ir_len(room, sr)
        except (_room.RoomError, OSError, RuntimeError) as e:
            log.warning("room %s skipped: %s", room.get("id"), e)
            st["room"] = {"room": room.get("id"), "skipped": str(e)}
            room = None
    bank_n = int(np.ceil(_res.TAIL_MAX_S * sr)) + 4 * _res.BLOCK if use_bank else 0
    n = dry.shape[0]
    sig = np.zeros((n + bank_n + room_n, 2), np.float32)
    sig[:n] = dry
    del dry

    # controls at the resonance bank's block rate, covering every tail (shared by una corda and the bank)
    ctl = None
    if perf is not None:
        ctl = perf.controls(_res.FS_BANK / _res.BLOCK, duration_s=n / sr + _res.TAIL_MAX_S + 1.0)

    # 2. una corda
    if ctl is not None and inst.get("una_corda", True) is not False:
        c0 = time.process_time()
        uc_max = float(ctl.una_corda.max()) if ctl.n else 0.0
        info = _tone.una_corda_inplace(sig, n, sr, ctl.t, ctl.una_corda) if uc_max > 0 else {"segments": 0}
        st["una_corda"] = dict(info, max=round(uc_max, 3),
                               used_fraction=round(float((ctl.una_corda > 0).mean()), 3) if ctl.n else 0.0,
                               cpu_s=round(time.process_time() - c0, 3))

    # 3. EQ and 4. width
    c0 = time.process_time()
    apply_sos_inplace(sig, n, eq_sos(inst.get("eq") or [], sr))
    _tone.width_inplace(sig, n, inst.get("width", 1.0))
    st["eq_width_cpu_s"] = round(time.process_time() - c0, 3)

    # 5. resonance
    if use_bank:
        try:
            from .dsp import analysis as _an
            c0 = time.process_time()
            prog.at("analysis")
            tuning = _an.tuning_for(inst, engine, sr, prog.step("analysis"))
            st["analysis"] = dict(tuning.info, source=tuning.source, measured_keys=tuning.measured,
                                  cpu_s=round(time.process_time() - c0, 3))
            n, rst = _res.run_into(sig, n, sr, perf, float(rdb), tuning,
                                   pan_width=float(inst.get("resonance_pan", _res.PAN_WIDTH)),
                                   progress=prog.step("resonance"), ctl=ctl)
            st["resonance"] = dict(rst, resonance_db=float(rdb))
        except ImportError as e:            # numba missing: the rest still renders
            log.warning("resonance bank skipped: %s", e)
            st["resonance"] = {"skipped": f"import failed: {e}"}
    del ctl

    # 6. room
    if room:
        c0 = time.process_time()
        prog.at("room")
        n, wst = _room.wet_into(sig, n, room, sr, prog.step("room"))
        st["room"] = dict(wst, cpu_s=round(time.process_time() - c0, 3))

    # trim what the tails added once they are below -120 dBFS (never shorter than the input)
    end = n
    if n > n0:
        tail = sig[n0:n]
        loud = np.flatnonzero((tail.max(axis=1) > END_FLOOR) | (tail.min(axis=1) < -END_FLOOR))
        end = n0 + (int(loud[-1]) + 1 if loud.size else 0)
    st["added_tail_s"] = round((end - n0) / sr, 3)
    st["cpu_s"] = round(time.process_time() - t_all, 3)
    if progress is not None:
        progress(n0 / sr, n0 / sr)
    return sig[:end]
