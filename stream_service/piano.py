"""Piano signals for the bindings (owner, 15 Oct 2026: the Steinway's MIDI and a room mic replace Maestro).

Eight 0-1 signals from the sounding notes (the note model is show_service's, seen here through ``Session.sound``,
music_view.py), computed on the loop thread at the session's tick and merged into the render thread's signals like
the music features (modulation.SessionRuntime.extra):

  note_on       trigger: velocity of each note-on, decaying (tau 0.18 s); the render thread evaluates the envelope
                from the last note-on's time, so it is sharp at 30/60 fps.  Lands about one lead late (unpredictable).
  note_rate     how busy the playing is: note-ons per second (EMA 2 s), 1 - exp(-rate / 6)
  velocity      how hard it is played: the note-on velocities (EMA 1.5 s)
  register      where on the keyboard: the sounding notes' mean pitch, A0 0 .. C8 1 (amplitude-weighted)
  spread        how full: half polyphony (10 notes = 1), half range (four octaves = 1)
  pedal         the damper (CC64) 0-1
  tension       dissonance of the sounding pitch classes (semitones and tritones high, fifths low)
  chord_change  trigger: the harmony moved (a new set of pitch classes), decaying (tau 1 s)
The level signals are n/a (None) once nothing has been played for SILENT_S, so a mapping falls back to its other
inputs and then to the preset's value; pedal is n/a only without a piano feed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

PIANO_TRIGGERS = ("note_on", "chord_change")
PIANO_LEVELS = ("note_rate", "velocity", "register", "spread", "pedal", "tension")
PIANO_SIGNALS = PIANO_TRIGGERS + PIANO_LEVELS
NOTE_ON_DECAY_S = 0.18
CHORD_DECAY_S = 1.0
SILENT_S = 8.0
TAU_RATE_S, TAU_VEL_S, TAU_SHAPE_S = 2.0, 1.5, 0.5
RATE_FULL = 6.0                 # note-ons per second for note_rate ~0.63 (1 - 1/e)
SOUNDING = 0.08                 # a note's amplitude (0-1) that counts as sounding
CHORD_MIN_S = 0.25              # chord changes closer than this are one change
LOW, HIGH = 21, 108             # A0 .. C8
# dissonance per interval class (semitones 0-6): unison, m2, M2, m3, M3, P4, tritone
DISSONANCE = (0.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.8)


@dataclass
class NoteState:
    """What the signals read from the note model each tick (filled by an adapter over music_view)."""
    online: bool = False                                  # a piano feed is connected
    amps: Any = None                                      # 128 sounding amplitudes 0-1 (pedal-held included)
    pedal: float = 0.0                                    # CC64 0-1
    onsets: list = field(default_factory=list)            # [(t_s monotonic, pitch, velocity 0-1)] since last tick
    t_last: Optional[float] = None                        # the last note event of any kind (monotonic s)


def _ema(prev: Optional[float], v: float, dt: float, tau: float) -> float:
    if prev is None:
        return v
    return prev + (v - prev) * (1.0 - math.exp(-dt / tau)) if dt > 0 else prev


def tension_of(chroma: list[float]) -> float:
    """Dissonance 0-1 of a 12-bin pitch-class weighting."""
    tot = sum(chroma)
    if tot <= 0.0:
        return 0.0
    acc = 0.0
    for i in range(12):
        ci = chroma[i]
        if ci <= 0.0:
            continue
        for j in range(i + 1, 12):
            cj = chroma[j]
            if cj > 0.0:
                d = j - i
                acc += ci * cj * DISSONANCE[min(d, 12 - d)]
    return min(1.0, 5.0 * acc / (tot * tot))         # C major 0.19, dominant 7th 0.58, dim 7th 0.75, cluster 1


class PianoSignals:
    """One session's piano signals (loop thread): step() each tick -> the extras the render thread merges."""

    def __init__(self) -> None:
        self.t: Optional[float] = None
        self.rate: Optional[float] = None
        self.vel: Optional[float] = None
        self.reg: Optional[float] = None
        self.spr: Optional[float] = None
        self.ten: Optional[float] = None
        self.on_t: Optional[float] = None
        self.on_vel = 0.0
        self.played_t: Optional[float] = None
        self.chord: frozenset = frozenset()
        self.chord_t: Optional[float] = None

    def step(self, now: float, st: Optional[NoteState]) -> dict:
        dt = 0.0 if self.t is None else max(0.0, min(2.0, now - self.t))
        self.t = now
        if st is None or not st.online:
            return {k: None for k in PIANO_LEVELS} | {"note_on_t": None, "note_on_vel": 0.0, "chord_change_t": None}
        n_on = len(st.onsets)
        for t_on, _pitch, vel in st.onsets:
            if self.on_t is None or t_on >= self.on_t:
                self.on_t, self.on_vel = t_on, max(0.0, min(1.0, float(vel)))
            self.vel = float(vel) if self.vel is None else self.vel + (float(vel) - self.vel) * 0.35
        if n_on:
            self.played_t = now
        if dt > 0:
            self.rate = _ema(self.rate, n_on / dt, dt, TAU_RATE_S)
        amps = st.amps if st.amps is not None else ()
        sounding = [(p, float(a)) for p, a in enumerate(amps) if a >= SOUNDING]     # plain floats (amps: numpy)
        if sounding:
            w = sum(a for _, a in sounding)
            mean = sum(p * a for p, a in sounding) / w
            reg = (mean - LOW) / (HIGH - LOW)
            lo, hi = min(p for p, _ in sounding), max(p for p, _ in sounding)
            spr = 0.5 * min(1.0, len(sounding) / 10.0) + 0.5 * min(1.0, (hi - lo) / 48.0)
            chroma = [0.0] * 12
            for p, a in sounding:
                chroma[p % 12] += a
            ten = tension_of(chroma)
            self.reg = _ema(self.reg, max(0.0, min(1.0, reg)), dt, TAU_SHAPE_S)
            self.spr = _ema(self.spr, spr, dt, TAU_SHAPE_S)
            self.ten = _ema(self.ten, ten, dt, TAU_SHAPE_S)
            top = max(chroma)
            pcs = frozenset(i for i, c in enumerate(chroma) if c >= 0.3 * top)
            if len(pcs) >= 2 and pcs != self.chord:
                inter = len(pcs & self.chord) / max(1, len(pcs | self.chord))
                if inter < 0.5 and (self.chord_t is None or now - self.chord_t >= CHORD_MIN_S):
                    self.chord_t = now
                self.chord = pcs
        elif dt > 0:
            self.spr = _ema(self.spr, 0.0, dt, TAU_SHAPE_S) if self.spr is not None else None
            self.ten = _ema(self.ten, 0.0, dt, TAU_SHAPE_S) if self.ten is not None else None
        if dt > 0 and not n_on and self.vel is not None:
            self.vel = _ema(self.vel, 0.0, dt, 4.0)        # it fades when nobody plays
        silent = self.played_t is None or now - self.played_t > SILENT_S
        rate = None if self.rate is None else 1.0 - math.exp(-self.rate / RATE_FULL)
        out = {"note_rate": None if silent else rate, "velocity": None if silent else self.vel,
               "register": None if silent else self.reg, "spread": None if silent else self.spr,
               "tension": None if silent else self.ten, "pedal": max(0.0, min(1.0, float(st.pedal or 0.0))),
               "note_on_t": self.on_t, "note_on_vel": self.on_vel, "chord_change_t": self.chord_t}
        return {k: (None if v is None else round(v, 4) if isinstance(v, float) and not k.endswith("_t") else v)
                for k, v in out.items()}


def envelopes(ex: dict, now: float) -> dict:
    """The two piano triggers at render time ``now`` from their event times in the extras."""
    t, vel, ct = ex.get("note_on_t"), ex.get("note_on_vel") or 0.0, ex.get("chord_change_t")
    return {"note_on": vel * math.exp(-(now - t) / NOTE_ON_DECAY_S) if t is not None and now >= t else 0.0,
            "chord_change": math.exp(-(now - ct) / CHORD_DECAY_S) if ct is not None and now >= ct else 0.0}
