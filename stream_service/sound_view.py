"""The sound view: what the piano and the room sound like, as scenes see it (owner, 15 Oct 2026).

The Steinway's adsilent MIDI and a room mic replace Maestro (docs/SHOW_ENGINE.md).  show_service publishes the
notes, harmony and spectrum on the show bus (§11.1); this module turns them into one host-side frame per render
frame.  The renderer then uploads it for scenes that declare ``listens = ("notes", "spectrum", "chroma")``
(``self.sound``: ``.frame`` is the :class:`SoundFrame`, plus ``notes_gpu`` etc., wired by the session).

Bus messages read (every ``t_ms`` is host CLOCK_MONOTONIC ms of the *sound*, not of arrival; ``seq`` is one space
shared by notes and pedals within a ``sess``):

  music.note      {seq, t_ms, on: bool, pitch, vel 0-127, source?, sess?}      (vel already hot-note capped)
  music.pedal     {seq, t_ms, pedal: damper|soft|sostenuto, value 0-1}
  music.notes     {seq, t_ms, keys: [[pitch, vel, t_on_ms], ...], pedals: {...}, present, source, sess}
                  the keys held down at t_ms (2-10 Hz): heals a lost note-on or note-off
  music.harmony   {chroma12, key, mode, tension, chord_change, register, spread}   (10 Hz, passed through)
  music.features  {..., spectrum32}                                                (25 Hz; smoothed, tau >= 300 ms)
  show.state      global.music.chroma12 / spectrum32, used only when the music.* types are absent
Anything else is ignored; a malformed message is dropped and counted (``frame.bad``).

The note model (per MIDI pitch; evaluated at the time ``update`` is given, the photon time):
  * A strike sets the level to ``vel ** VEL_GAMMA`` (instantly: scenes shape the bloom with AGE_ON) and it decays
    with the pitch's sustain time ``tau_sus`` (A0 ~5 s, halving every two octaves, floor 0.3 s).
  * A key that is up and not held by a pedal damps with ``DAMP_S + (tau_sus - DAMP_S) * damper**2``: the
    proportional damper lengthens every fade, fully down it sustains.  Keys from UNDAMPED_FROM up have no dampers
    (as on a grand) and always ring out.
  * Sostenuto latches the keys held when it goes down, until it comes up.
  * Events usually arrive after the time they describe (the photon time is ~0.3 s ahead of the sound), so each
    is applied with a correction for the time since it happened: the result matches a model that knew it in time.
    Note and pedal messages stamped after the model time wait for it, in order (up to FUTURE_MAX_MS ahead).
  * A re-strike takes the louder of the ringing note and the new strike.  A strike more than HOT_JUMP above the
    same key's previous strike within HOT_REPEAT_S is capped (show_rt caps already; this is a guard).
  * Stale feed: ``present`` goes False after STALE_S without a music.* message; keys still down after STUCK_S are
    released (the feed died mid-note).  A new ``sess`` releases every held key; its first snapshot restores them.

Threads: ``on_message`` may be called from any thread (it only appends to a deque); ``update`` runs on one
session's render thread, once per frame, and owns every piece of model state.  ``tap`` is for the loop thread
(stream_service/piano.py's signals): a copy of the latest levels and the onsets since the previous tap.
One SoundView per session, because each screen has its own photon time.
"""
from __future__ import annotations

import collections
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

log = logging.getLogger("stream_service.sound_view")

N_PITCH = 128
LEVEL, VEL, AGE_ON, AGE_OFF = 0, 1, 2, 3              # columns of SoundFrame.notes
DAMPER, SOFT, SOSTENUTO = 0, 1, 2                     # SoundFrame.pedals
PEDALS = ("damper", "soft", "sostenuto")
SPECTRUM_BANDS = 32
HISTORY_ROWS = 256

VEL_GAMMA = 1.2
TAU_A0_S = 5.0                 # sustain time constant at A0 ...
TAU_HALVING_SEMIS = 24.0       # ... halving every two octaves
TAU_MIN_S = 0.3
DAMP_S = 0.12                  # a released key with the damper up
UNDAMPED_FROM = 89             # F6: the top keys of a grand have no dampers
SOSTENUTO_ON = 0.5
HOT_REPEAT_S, HOT_JUMP = 0.25, 0.25
SILENT_LEVEL = 1e-4
STALE_S = 5.0
STUCK_S = 10.0
FUTURE_MAX_MS = 2000.0         # an event stamped further ahead than this is applied at once (a clock fault)
TAP_ONSETS_MAX = 512
MSG_QUEUE_MAX = 4096

_MUSIC_TYPES = ("music.note", "music.pedal", "music.notes")


def tau_sustain(pitch: np.ndarray | int) -> np.ndarray | float:
    """Sustain time constant (s) of a struck key held down."""
    p = np.asarray(pitch, dtype=np.float64)
    return np.maximum(TAU_MIN_S, TAU_A0_S * np.power(0.5, (p - 21.0) / TAU_HALVING_SEMIS))


TAU_SUS = np.asarray(tau_sustain(np.arange(N_PITCH)), dtype=np.float64)
_UNDAMPED = np.arange(N_PITCH) >= UNDAMPED_FROM


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _pitch(v: Any) -> Optional[int]:
    n = _num(v)
    if n is None or n != int(n) or not 0 <= n < N_PITCH:
        return None
    return int(n)


def _unit(v: Any) -> Optional[float]:
    n = _num(v)
    return None if n is None else min(1.0, max(0.0, n))


@dataclass
class SoundFrame:
    """One render frame of sound, refreshed in place by :meth:`SoundView.update`.  Read-only for scenes."""
    t_ms: float = 0.0
    notes: np.ndarray = field(default_factory=lambda: np.zeros((N_PITCH, 4), np.float32))
    onsets: list = field(default_factory=list)         # [(pitch, vel 0-1, age_s)] new since the previous update
    releases: list = field(default_factory=list)       # [(pitch, vel_off 0-1, age_s)]
    pedals: np.ndarray = field(default_factory=lambda: np.zeros(3, np.float32))
    chroma: np.ndarray = field(default_factory=lambda: np.zeros(12, np.float32))
    key: Optional[str] = None
    mode: Optional[str] = None
    tension: Optional[float] = None
    chord_change: Optional[float] = None
    register: Optional[float] = None
    spread: Optional[float] = None
    spectrum: np.ndarray = field(default_factory=lambda: np.zeros(SPECTRUM_BANDS, np.float32))
    history: np.ndarray = field(default_factory=lambda: np.zeros((HISTORY_ROWS, SPECTRUM_BANDS), np.float32))
    history_row: int = -1                              # the newest row of ``history`` (-1: none yet)
    present: bool = False                              # notes sounding, or a music.* message within STALE_S
    source: Optional[str] = None                       # "live" | "test" | None
    seq: int = -1
    lost: int = 0                                      # seq gaps seen (a snapshot heals them)
    capped: int = 0                                    # hot notes capped by the guard
    bad: int = 0                                       # malformed messages dropped

    def __post_init__(self) -> None:
        self.notes[:, AGE_ON] = np.inf
        self.notes[:, AGE_OFF] = np.inf


@dataclass
class NoteTap:
    """What piano.py's signals read each tick (loop thread)."""
    online: bool
    amps: np.ndarray                                   # float32 [128], the latest LEVEL column (a copy)
    pedal: float                                       # damper 0-1
    onsets: list                                       # [(t_s, pitch, vel 0-1)] since the previous tap
    t_last: Optional[float]                            # the last note or pedal event (s), or None


class SoundView:
    """One session's sound model.  ``on_message`` from any thread, ``update(t_ms)`` on the render thread."""

    def __init__(self) -> None:
        self._inbox: collections.deque = collections.deque(maxlen=MSG_QUEUE_MAX)
        self._pending: collections.deque = collections.deque()
        self.frame = SoundFrame()
        self.t: Optional[float] = None                 # model time (ms) of the last update
        self.level = np.zeros(N_PITCH, np.float64)
        self.vel = np.zeros(N_PITCH, np.float64)
        self.t_on = np.full(N_PITCH, np.nan)
        self.t_off = np.full(N_PITCH, np.nan)
        self.vel_off = np.zeros(N_PITCH, np.float64)
        self.held = np.zeros(N_PITCH, bool)
        self.sost = np.zeros(N_PITCH, bool)
        self.pedal = [0.0, 0.0, 0.0]
        self.sess: Optional[str] = None
        self.seq = -1
        self.t_msg: Optional[float] = None             # arrival-side clock: model time of the last music.* message
        self.t_event: Optional[float] = None           # sound time (ms) of the last note or pedal event
        self.harmony_t: Optional[float] = None
        self.features_t: Optional[float] = None
        self._tau = np.empty(N_PITCH, np.float64)
        self._buf = np.empty(N_PITCH, np.float64)
        self._amps = [np.zeros(N_PITCH, np.float32) for _ in range(3)]
        self._amps_pub = 0
        self._tap_onsets: collections.deque = collections.deque(maxlen=TAP_ONSETS_MAX)

    # -- any thread ------------------------------------------------------------------------------------------------
    def on_message(self, msg: dict) -> None:
        """Queue a bus message (cheap; parsed on the render thread)."""
        self._inbox.append(msg)

    def tap(self) -> NoteTap:
        """The latest levels and the onsets since the previous call (for the loop thread)."""
        onsets = []
        while True:
            try:
                onsets.append(self._tap_onsets.popleft())
            except IndexError:
                break
        t_msg, t_now = self.t_msg, self.t
        online = t_msg is not None and t_now is not None and t_now - t_msg <= STALE_S * 1000.0
        t_event = self.t_event
        return NoteTap(online=online, amps=self._amps[self._amps_pub].copy(), pedal=self.pedal[DAMPER],
                       onsets=onsets, t_last=None if t_event is None else t_event / 1000.0)

    # -- render thread ---------------------------------------------------------------------------------------------
    def update(self, t_ms: float) -> SoundFrame:
        """Advance to ``t_ms`` (host monotonic ms; pass the photon time), apply queued messages, refresh the frame."""
        f = self.frame
        f.onsets = []
        f.releases = []
        if self.t is None:
            self.t = t_ms
        elif t_ms > self.t:
            self._decay((t_ms - self.t) / 1000.0)
            self.t = t_ms
        while True:
            try:
                msg = self._inbox.popleft()
            except IndexError:
                break
            if isinstance(msg, dict) and msg.get("type") in _MUSIC_TYPES:
                self._pending.append(msg)              # notes and pedals keep their order and wait for their time
            else:
                self._try_apply(msg)
        while self._pending:
            data = self._pending[0].get("data")
            t_ev = _num(data.get("t_ms")) if isinstance(data, dict) else None
            if t_ev is not None and self.t < t_ev <= self.t + FUTURE_MAX_MS:
                break
            self._try_apply(self._pending.popleft())
        self._housekeep()
        self._publish()
        return f

    # -- the model -------------------------------------------------------------------------------------------------
    def _try_apply(self, msg: Any) -> None:
        try:
            self._apply(msg)
        except (TypeError, ValueError, KeyError, AttributeError, IndexError):
            self.frame.bad += 1

    def _tau_released(self, damper: float) -> np.ndarray:
        """Decay time constants of keys that are up and not latched, for a damper position."""
        return DAMP_S + (TAU_SUS - DAMP_S) * (damper * damper)

    def _sustained(self) -> np.ndarray:
        return self.held | self.sost | _UNDAMPED

    def _decay(self, dt: float) -> None:
        d = self.pedal[DAMPER]
        np.subtract(TAU_SUS, DAMP_S, out=self._tau)
        self._tau *= d * d
        self._tau += DAMP_S
        np.copyto(self._tau, TAU_SUS, where=self._sustained())
        np.divide(-dt, self._tau, out=self._buf)
        np.exp(self._buf, out=self._buf)
        self.level *= self._buf
        self.level[self.level < SILENT_LEVEL] = 0.0

    def _since(self, t_ev: float) -> float:
        """Seconds from an event's sound time to the model time (0 for an event in the model's future)."""
        return max(0.0, (self.t - t_ev) / 1000.0)

    def _retau(self, mask: np.ndarray, tau_old: np.ndarray | float, tau_new: np.ndarray | float, t_ev: float) -> None:
        """Re-decay ``mask``'s levels as if their time constant had changed at ``t_ev`` (sound time)."""
        s = self._since(t_ev)
        if s <= 0.0 or not mask.any():
            return
        k = np.exp(-s * (1.0 / np.asarray(tau_new) - 1.0 / np.asarray(tau_old)))
        self.level[mask] = np.minimum(1.0, self.level[mask] * (k[mask] if np.ndim(k) else k))

    def _apply(self, msg: dict) -> None:
        mtype = msg.get("type")
        data = msg.get("data")
        if mtype == "show.state":
            self._show_state(data or {})
            return
        if mtype == "music.harmony":
            self._harmony(data)
            return
        if mtype == "music.features":
            self._features(data.get("spectrum32"))
            return
        if mtype not in _MUSIC_TYPES:
            return
        if not isinstance(data, dict):
            raise TypeError("data")
        sess = data.get("sess")
        if sess is not None and sess != self.sess:
            self._new_session(str(sess))
        seq = data.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            if mtype == "music.notes":
                if seq < self.seq:
                    return                             # older than events already applied
                if self.seq >= 0:
                    self.frame.lost += seq - self.seq  # events it reflects that never arrived
            elif seq <= self.seq:
                return                                 # a duplicate
            elif self.seq >= 0 and seq > self.seq + 1:
                self.frame.lost += seq - self.seq - 1
            self.seq = max(self.seq, seq)
        self.t_msg = self.t
        src = data.get("source")
        if src in ("live", "test"):
            self.frame.source = src
        t_ev = _num(data.get("t_ms"))
        if t_ev is None:
            raise ValueError("t_ms")
        if mtype == "music.note":
            p = _pitch(data.get("pitch"))
            vel = _num(data.get("vel"))
            if p is None or vel is None:
                raise ValueError("pitch/vel")
            if data.get("on") is True:
                self._strike(p, min(1.0, max(0.0, vel / 127.0)), t_ev)
            else:
                self._release(p, min(1.0, max(0.0, vel / 127.0)), t_ev)
        elif mtype == "music.pedal":
            name, value = data.get("pedal"), _unit(data.get("value"))
            if name not in PEDALS or value is None:
                raise ValueError("pedal")
            self._set_pedal(PEDALS.index(name), value, t_ev)
        else:
            self._snapshot(data, t_ev)

    def _new_session(self, sess: str) -> None:
        if self.sess is not None:
            for p in np.flatnonzero(self.held):
                self._release(int(p), 0.0, self.t)
        self.sess, self.seq = sess, -1

    def _strike(self, p: int, vel: float, t_ev: float) -> None:
        last = self.t_on[p]
        if not math.isnan(last) and 0.0 <= (t_ev - last) / 1000.0 < HOT_REPEAT_S and vel > self.vel[p] + HOT_JUMP:
            vel = self.vel[p] + HOT_JUMP
            self.frame.capped += 1
        s = self._since(t_ev)
        self.level[p] = max(self.level[p], vel ** VEL_GAMMA * math.exp(-s / TAU_SUS[p]))
        self.vel[p], self.t_on[p], self.t_off[p], self.vel_off[p] = vel, t_ev, np.nan, 0.0
        self.held[p] = True
        self.t_event = t_ev if self.t_event is None else max(self.t_event, t_ev)
        self.frame.onsets.append((p, vel, s))
        self._tap_onsets.append((t_ev / 1000.0, p, vel))

    def _release(self, p: int, vel_off: float, t_ev: float) -> None:
        if not self.held[p]:
            return
        self.held[p] = False
        self.t_off[p], self.vel_off[p] = t_ev, vel_off
        if not (self.sost[p] or _UNDAMPED[p]):
            mask = np.zeros(N_PITCH, bool)
            mask[p] = True
            self._retau(mask, TAU_SUS, self._tau_released(self.pedal[DAMPER]), t_ev)
        self.t_event = t_ev if self.t_event is None else max(self.t_event, t_ev)
        self.frame.releases.append((p, vel_off, self._since(t_ev)))

    def _set_pedal(self, which: int, value: float, t_ev: float) -> None:
        old = self.pedal[which]
        if value == old:
            return
        self.pedal[which] = value
        self.t_event = t_ev if self.t_event is None else max(self.t_event, t_ev)
        free = ~self._sustained()                      # released, unlatched, damped keys
        if which == DAMPER:
            self._retau(free, self._tau_released(old), self._tau_released(value), t_ev)
        elif which == SOSTENUTO:
            if old < SOSTENUTO_ON <= value:
                self.sost[:] = self.held
            elif value < SOSTENUTO_ON <= old:
                dropped = self.sost & ~self.held & ~_UNDAMPED
                self.sost[:] = False
                self._retau(dropped, TAU_SUS, self._tau_released(self.pedal[DAMPER]), t_ev)

    def _snapshot(self, data: dict, t_ev: float) -> None:
        keys = data.get("keys")
        if not isinstance(keys, list):
            raise TypeError("keys")
        down: dict[int, tuple[float, float]] = {}
        for k in keys:
            p = _pitch(k[0])
            vel, t_on = _num(k[1]), _num(k[2])
            if p is None or vel is None or t_on is None:
                raise ValueError("key")
            down[p] = (min(1.0, max(0.0, vel / 127.0)), t_on)
        pedals = data.get("pedals") or {}
        for i, name in enumerate(PEDALS):
            v = _unit(pedals.get(name))
            if v is not None and abs(v - self.pedal[i]) > 0.01:
                self._set_pedal(i, v, t_ev)
        for p in np.flatnonzero(self.held):
            if int(p) not in down:
                self._release(int(p), 0.0, t_ev)
        for p, (vel, t_on) in down.items():
            if not self.held[p] or math.isnan(self.t_on[p]) or t_on > self.t_on[p] + 1.0:
                self._strike(p, vel, t_on)

    def _harmony(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise TypeError("data")
        f = self.frame
        chroma = data.get("chroma12")
        if isinstance(chroma, list) and len(chroma) == 12:
            f.chroma[:] = [(_unit(c) or 0.0) for c in chroma]
        f.key = data.get("key") if isinstance(data.get("key"), str) else None
        f.mode = data.get("mode") if isinstance(data.get("mode"), str) else None
        f.tension, f.chord_change = _unit(data.get("tension")), _unit(data.get("chord_change"))
        f.register, f.spread = _unit(data.get("register")), _unit(data.get("spread"))
        self.harmony_t = self.t

    def _features(self, spectrum: Any, *, fallback: bool = False) -> None:
        if not (isinstance(spectrum, list) and len(spectrum) == SPECTRUM_BANDS):
            return
        if fallback and self.features_t is not None and self.t - self.features_t <= STALE_S * 1000.0:
            return
        f = self.frame
        f.spectrum[:] = [(_unit(v) or 0.0) for v in spectrum]
        f.history_row = (f.history_row + 1) % HISTORY_ROWS
        f.history[f.history_row] = f.spectrum
        if not fallback:
            self.features_t = self.t

    def _show_state(self, data: dict) -> None:
        music = (data.get("global") or {}).get("music") or {}
        if not isinstance(music, dict):
            return
        fresh = self.harmony_t is not None and self.t - self.harmony_t <= STALE_S * 1000.0
        chroma = music.get("chroma12")
        if not fresh and isinstance(chroma, list) and len(chroma) == 12:
            self.frame.chroma[:] = [(_unit(c) or 0.0) for c in chroma]
        self._features(music.get("spectrum32"), fallback=True)

    def _housekeep(self) -> None:
        silent_ms = None if self.t_msg is None else self.t - self.t_msg
        if silent_ms is not None and silent_ms > STUCK_S * 1000.0 and self.held.any():
            for p in np.flatnonzero(self.held):
                self._release(int(p), 0.0, self.t)
            self.sost[:] = False
        if self.harmony_t is not None and self.t - self.harmony_t > STALE_S * 1000.0:
            f = self.frame
            f.key = f.mode = f.tension = f.chord_change = f.register = f.spread = None
            f.chroma[:] = 0.0
            self.harmony_t = None

    def _publish(self) -> None:
        f, t = self.frame, self.t
        f.t_ms = t
        n = f.notes
        n[:, LEVEL] = self.level
        n[:, VEL] = self.vel
        struck = ~np.isnan(self.t_on)
        n[:, AGE_ON] = np.inf
        n[struck, AGE_ON] = np.maximum(0.0, (t - self.t_on[struck]) / 1000.0)
        n[:, AGE_OFF] = np.inf
        n[self.held, AGE_OFF] = -1.0
        up = struck & ~self.held & ~np.isnan(self.t_off)
        n[up, AGE_OFF] = np.maximum(0.0, (t - self.t_off[up]) / 1000.0)
        f.pedals[:] = self.pedal
        f.seq = self.seq
        recent = self.t_msg is not None and t - self.t_msg <= STALE_S * 1000.0
        f.present = bool(recent or self.level.max() > 0.02)
        nxt = (self._amps_pub + 1) % 3
        self._amps[nxt][:] = self.level
        self._amps_pub = nxt


from .piano_replay import TestPiano  # noqa: E402  (re-exported: the test source that emits these messages)

__all__ = ["SoundView", "SoundFrame", "NoteTap", "TestPiano", "N_PITCH", "LEVEL", "VEL", "AGE_ON", "AGE_OFF",
           "DAMPER", "SOFT", "SOSTENUTO", "PEDALS", "SPECTRUM_BANDS", "HISTORY_ROWS", "tau_sustain"]
