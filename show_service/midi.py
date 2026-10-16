"""Piano MIDI -> the note table, the pedals and music.note / music.pedal / music.notes (docs/SHOW_ENGINE.md §3.5, §11.1).

One :class:`Piano` per MIDI stream (``room-piano``, ``test-piano``), owned by the receive thread ("show-rx"): the
receiver calls :meth:`Piano.on_midi` with each validated vban.MidiBatch / vban.MidiState and ``to_host`` (the
stream's RemoteTimeMap.to_host), and ``__main__``'s on_tick calls :meth:`Piano.tick`. Nothing here locks or does I/O;
``publish`` is the bus's thread-safe seam. Not thread-safe: other threads read only what was published.

Events in. All channels feed one keyboard (the adsilent: ch 1 the keys, ch 2 its own playback), unless
``ignore_channels`` names some (1-16): their events are dropped and counted (``stats()["ignored"]``).
``state_channels`` (1-16; None = all, the default) names the channels a state's ``keys`` and pedal CCs describe:
the kiosk sender (tools/show_sender) puts only channel 1's keys in a state (the keys physically down), so
``__main__`` passes ``(1,)`` and a state never releases a key or resets a pedal that another channel (the box's
playback) set; keys and pedals it synthesises count as covered. ``0x90`` with
velocity 0 is a note-off. CC 64 / 66 / 67 are the damper / sostenuto / soft pedals (contract.PEDAL_CC); everything
else is ignored. An event's time is ``to_host(t_ns)``: the time of the *sound* in host CLOCK_MONOTONIC seconds; NaN
(no map yet) falls back to the packet's arrival.

Wire ``seq`` (vban.midi_seq_verdict, per wire session):
  restart   the first packet, a changed wire ``sess``, or ``seq`` more than 8 behind: every held key is released
            (published under the old bus sess), a new bus ``sess`` = ``f"{stream}-{uuid4().hex[:8]}"`` starts, and
            the packet is taken as the session's first (a batch's ``prev`` oldest first, then its events; a state
            heals the keys);
  old       a duplicate batch, or a stale state: ignored (``stats()["old"]``);
  new       a batch with ``seq > last + 1`` first applies its ``prev`` batches in (last, seq), oldest first, and
            counts what is still missing as ``lost``; a state (``seq >= last``) diffs its 88 ``keys`` and pedal CCs
            against ours and synthesises the note-ons, note-offs and pedal changes at its ``t_ns`` (so a dropped
            note-off never sticks for more than one state, 0.5 s).

Hot-note cap. A re-strike of a key within ``hot_repeat_s`` (0.25 s) of its previous strike is capped at the previous
(capped) velocity + ``hot_jump`` (32): the adsilent v1 reports absurd velocities on fast repeats. Both are class
attributes, tunable per instance. sound_view.py's guard (HOT_REPEAT_S 0.25, HOT_JUMP 0.25 x 127) matches.

Amplitude model (stream_service/sound_view.py's, so the director and the screens agree):
  * a strike sets ``max(ringing level, (vel / 127) ** 1.2 * (1 - soft_scale * soft))`` (soft_scale 0.25: the soft
    pedal shades strikes in :meth:`levels` only, never a published velocity);
  * held keys, keys latched by the sostenuto, and keys from MIDI 89 up (no dampers) ring with
    ``tau_sus = max(0.3, 5 * 0.5 ** ((pitch - 21) / 24))`` s;
  * other released keys damp with ``0.12 + (tau_sus - 0.12) * damper ** 2`` (the damper is proportional);
  * the sostenuto latches the keys down when it crosses up through 0.5 and lets them go when it falls below 0.5.
  The table keeps each key's LEVEL at an anchor time; a tau change (release, damper, sostenuto) re-anchors the keys
  it touches, so :meth:`levels` is exact for any time after the last event.

``table`` (128, 6) float64, columns LEVEL, VEL_ON, T_ON, RELEASED, T_OFF, VEL_OFF: times in host seconds (NaN =
never), RELEASED 0/1 (1 = the key is up), LEVEL the amplitude at the key's last event (anchor).

Publishing (shapes exactly contract.NoteData / PedalData / NotesData; ``t_ms`` = host seconds x 1000, rounded to µs):
  music.note   at once, on every strike and on the release of a held key;
  music.pedal  at once, when a pedal's CC value changes;
  music.notes  on :meth:`tick` while ``present``: every 0.1 s while anything changed in the last 1 s, else every
               0.5 s; and at once when ``present`` flips (either way). A piano that is not present publishes nothing
               more after that flip frame (no keys can be down then), so an idle kiosk sender's 2 Hz states never
               reach the bus. ``keys`` = the keys physically down as [pitch, vel, t_on_ms], where t_on_ms is
               exactly the ``t_ms`` of that key's music.note (stored at the strike, never recomputed); ``t_ms`` =
               max(now, the latest event's t_ms); ``seq`` = the bus seq of the last note/pedal published.
  The bus ``seq`` is ours: +1 per note or pedal event, never reset (one increasing space per sess, and across).
  ``present`` is True while a key or a pedal (> 0.1) is down, or a note/pedal event happened in the last 10 s
  (state-synthesised events count; a state that changes nothing does not, so a resting proportional damper and the
  2 Hz states never hold it up). No packet for 5 s: every held key is released (vel 0, at that tick's time) and
  ``present`` is False.

Voice (one note source on the bus). stream_service/sound_view.py keeps one session: a message with another ``sess``
releases every held key and the next snapshot re-strikes them, so two pianos publishing at once make the renderer
thrash. ``voice`` (default True: a lone Piano publishes) says whether this Piano may publish; ``__main__`` gives it
to one Piano at a time (the present one, live before test). A Piano without the voice keeps its whole table, pedals,
``present`` and harmony levels, and publishes nothing. :meth:`set_voice` (True) first publishes the note/pedal
messages muted since the current packet (or tick) began, under the current sess (so a hand-over inside the packet
that made the piano present loses no strike), then a music.notes snapshot, so the renderer heals to this keyboard
(a new sess: its keys released, ours struck at their own t_on_ms).
"""

from __future__ import annotations

import math
import uuid
from typing import Callable, Collection, Optional, Union

import numpy as np

from . import contract, vban
from .contract import MUSIC_NOTE, MUSIC_NOTES, MUSIC_PEDAL, PEDAL_CC, Publish, Source

N_PITCH = 128
LEVEL, VEL_ON, T_ON, RELEASED, T_OFF, VEL_OFF = range(6)
PEDALS: tuple[str, ...] = ("damper", "soft", "sostenuto")

VEL_GAMMA = 1.2
TAU_A0_S = 5.0                 # sustain time constant at A0 ...
TAU_HALVING_SEMIS = 24.0       # ... halving every two octaves
TAU_MIN_S = 0.3
DAMP_S = 0.12                  # a released key with the damper up
UNDAMPED_FROM = 89             # F6 up: no dampers on a grand
SOSTENUTO_ON = 0.5
SOFT_SCALE = 0.25              # levels() only: a strike with the soft pedal fully down sounds 25 % quieter
HOT_REPEAT_S = 0.25
HOT_JUMP = 32
SILENT_LEVEL = 1e-4

PEDAL_DOWN = 0.1               # a pedal above this holds ``present`` up
PRESENT_HOLD_S = 10.0          # ... as does a note/pedal event within this
STALE_S = 5.0                  # no packet for this long: release everything, present False
NOTES_FAST_S = 0.1             # music.notes interval while something changed in the last CHANGED_S
NOTES_SLOW_S = 0.5
CHANGED_S = 1.0
MUTED_KEEP = 256               # muted messages kept for a hand-over within the same packet (a batch + prev, a state)

TAU_SUS = np.maximum(TAU_MIN_S, TAU_A0_S * np.power(0.5, (np.arange(N_PITCH) - 21.0) / TAU_HALVING_SEMIS))
_UNDAMPED = np.arange(N_PITCH) >= UNDAMPED_FROM
_CC_ORDER = (64, 66, 67)       # how a state lists them: cc64, cc66, cc67


def is_test_stream(name: str) -> bool:
    return name in contract.TEST_STREAMS or name.startswith("test-")


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


class Piano:
    """One MIDI stream's keyboard (module docstring). Owned by show-rx."""

    hot_repeat_s: float = HOT_REPEAT_S
    hot_jump: int = HOT_JUMP
    soft_scale: float = SOFT_SCALE

    def __init__(self, stream: str, publish: Publish, *, source: Optional[Source] = None,
                 ignore_channels: Collection[int] = (), state_channels: Optional[Collection[int]] = None) -> None:
        self.stream = stream
        self.source: Source = source if source is not None else ("test" if is_test_stream(stream) else "live")
        self._publish = publish
        self._ignore = frozenset(int(c) for c in ignore_channels)
        # channels a state describes (None: all); 0 marks a key or pedal a state set itself (always covered)
        self._state_ch = None if state_channels is None else np.array(sorted({0, *(int(c) for c in state_channels)}))
        self._key_ch = np.zeros(N_PITCH, np.int16)       # the channel (1-16) of each key's last strike
        self._cc_ch = {64: 0, 66: 0, 67: 0}              # the channel of each pedal's last change
        self.table = np.zeros((N_PITCH, 6), np.float64)
        self.table[:, T_ON] = np.nan
        self.table[:, T_OFF] = np.nan
        self.table[:, RELEASED] = 1.0
        self.pedals: dict[str, float] = {p: 0.0 for p in PEDALS}
        self.present = False
        self.voice = True                                # may publish (module docstring "Voice")
        self._muted: list[dict] = []                     # messages muted since this packet / tick began
        self.sess = ""                                   # the bus sess ("" until the first packet)
        self.seq = 0                                     # the bus seq of the last note/pedal published
        self._held = np.zeros(N_PITCH, bool)             # physically down (as far as we know)
        self._sost = np.zeros(N_PITCH, bool)             # latched by the sostenuto
        self._anchor = np.zeros(N_PITCH, np.float64)     # host s that LEVEL refers to
        self._t_on_ms = np.full(N_PITCH, np.nan)         # the exact t_ms published at each key's strike
        self._cc = {64: 0, 66: 0, 67: 0}                 # raw pedal CCs
        self._wire_seq: Optional[int] = None
        self._wire_sess: Optional[str] = None
        self._last_rx: Optional[float] = None            # host arrival of the latest packet
        self._t_activity = -math.inf                     # host arrival of the latest note/pedal event
        self._t_change = -math.inf                       # ... of the latest published change
        self._t_notes = -math.inf                        # when music.notes was last published
        self._last_event_ms = -math.inf
        self._arrival = 0.0                              # arrival of the packet being applied
        self._tau = np.empty(N_PITCH, np.float64)        # scratch for levels()
        self._buf = np.empty(N_PITCH, np.float64)
        self._stats = {"batches": 0, "states": 0, "old": 0, "restarts": 0, "events": 0, "lost": 0,
                       "healed_prev": 0, "healed_state": 0, "capped": 0, "stale_releases": 0, "ignored": 0,
                       "muted": 0, "voiced": 0}

    # -- in ---------------------------------------------------------------------------------------------------------
    def on_midi(self, msg: Union[vban.MidiBatch, vban.MidiState], arrival_s: float,
                to_host: Callable[[int], float]) -> None:
        """One validated batch or state from the receiver (contract.OnMidi, minus the stream name)."""
        is_state = isinstance(msg, vban.MidiState)
        self._muted.clear()
        self._last_rx = arrival_s
        self._arrival = arrival_s
        verdict = vban.midi_seq_verdict(msg.seq, msg.sess, self._wire_seq, self._wire_sess, state=is_state)
        if verdict == "old":
            self._stats["old"] += 1
            return
        if verdict == "restart":
            self._restart(msg.sess, arrival_s)
        last = self._wire_seq
        if is_state:
            self._stats["states"] += 1
            self._heal(msg, to_host)                     # type: ignore[arg-type]
            self._wire_seq = msg.seq if last is None else max(last, msg.seq)
            return
        self._stats["batches"] += 1
        batch: vban.MidiBatch = msg                      # type: ignore[assignment]
        if last is None:                                 # a session's first batch: its prev heals what it covers
            for _, ev in sorted(batch.prev, key=lambda p: p[0]):
                self._events(ev, to_host)
        elif batch.seq > last + 1:
            healed = 0
            for s, ev in sorted(batch.prev, key=lambda p: p[0]):
                if last < s < batch.seq:
                    self._events(ev, to_host)
                    healed += 1
            self._stats["healed_prev"] += healed
            self._stats["lost"] += batch.seq - last - 1 - healed
        self._events(batch.ev, to_host)
        self._wire_seq = batch.seq

    def _restart(self, wire_sess: Optional[str], arrival_s: float) -> None:
        if self.sess:
            self._stats["restarts"] += 1
            self._release_all(arrival_s)                 # under the old bus sess
        self.sess = f"{self.stream}-{uuid.uuid4().hex[:8]}"
        self._wire_seq = None
        self._wire_sess = wire_sess

    def _time(self, t_ns: int, to_host: Callable[[int], float]) -> float:
        t = to_host(t_ns)
        return float(t) if _finite(t) else self._arrival

    def _events(self, events: list, to_host: Callable[[int], float]) -> None:
        for t_ns, status, d1, d2 in events:
            if self._ignore and (status & 0x0F) + 1 in self._ignore:
                self._stats["ignored"] += 1
                continue
            kind = status & 0xF0
            if kind == 0x90 and d2 > 0:
                self._stats["events"] += 1
                self._strike(d1, d2, self._time(t_ns, to_host))
                self._key_ch[d1] = (status & 0x0F) + 1
            elif kind in (0x80, 0x90):
                self._stats["events"] += 1
                self._release(d1, d2 if kind == 0x80 else 0, self._time(t_ns, to_host))
            elif kind == 0xB0 and d1 in PEDAL_CC:
                self._stats["events"] += 1
                self._pedal(d1, d2, self._time(t_ns, to_host))
                self._cc_ch[d1] = (status & 0x0F) + 1

    def _heal(self, st: vban.MidiState, to_host: Callable[[int], float]) -> None:
        """Diff a state against ours; synthesise what differs at its t_ns (pedals first, then keys)."""
        t = self._time(st.t_ns, to_host)
        n = 0
        sc = self._state_ch
        for cc, value in zip(_CC_ORDER, (st.cc64, st.cc66, st.cc67)):
            if value != self._cc[cc] and (sc is None or self._cc_ch[cc] in sc):
                self._pedal(cc, value, t)
                self._cc_ch[cc] = 0
                n += 1
        want = np.zeros(N_PITCH, bool)
        vel = np.zeros(N_PITCH, np.int64)
        keys = np.asarray(st.keys, np.int64)
        vel[vban.PIANO_LOW:vban.PIANO_LOW + vban.PIANO_KEYS] = keys
        want[vban.PIANO_LOW:vban.PIANO_LOW + vban.PIANO_KEYS] = keys > 0
        stale = self._held & ~want
        if sc is not None:
            stale &= np.isin(self._key_ch, sc)           # another channel's key (the playback) isn't the state's
        for p in np.flatnonzero(stale):
            self._release(int(p), 0, t)
            n += 1
        for p in np.flatnonzero(want & ~self._held):
            self._strike(int(p), int(vel[p]), t)
            self._key_ch[p] = 0
            n += 1
        self._stats["healed_state"] += n

    # -- the model --------------------------------------------------------------------------------------------------
    def _tau_of(self, p: int) -> float:
        if self._held[p] or self._sost[p] or _UNDAMPED[p]:
            return float(TAU_SUS[p])
        d = self.pedals["damper"]
        return DAMP_S + (float(TAU_SUS[p]) - DAMP_S) * d * d

    def _taus(self, out: np.ndarray) -> np.ndarray:
        d = self.pedals["damper"]
        np.subtract(TAU_SUS, DAMP_S, out=out)
        out *= d * d
        out += DAMP_S
        np.copyto(out, TAU_SUS, where=self._held | self._sost | _UNDAMPED)
        return out

    def _level_at(self, p: int, t: float) -> float:
        lev = self.table[p, LEVEL]
        if lev <= 0.0:
            return 0.0
        return float(lev * math.exp(-max(0.0, t - self._anchor[p]) / self._tau_of(p)))

    def _reanchor(self, mask: np.ndarray, t: float) -> None:
        """Bring ``mask``'s LEVELs to time ``t`` under the current taus (call before a tau changes)."""
        if not mask.any():
            return
        tau = self._taus(self._tau)
        dt = np.maximum(t - self._anchor[mask], 0.0)
        self.table[mask, LEVEL] *= np.exp(-dt / tau[mask])
        self._anchor[mask] = t

    def _strike(self, p: int, vel: int, t: float) -> None:
        row = self.table[p]
        t_prev = row[T_ON]
        if (self.hot_repeat_s > 0 and _finite(t_prev) and 0.0 <= t - t_prev < self.hot_repeat_s
                and vel > row[VEL_ON] + self.hot_jump):
            vel = int(row[VEL_ON] + self.hot_jump)
            self._stats["capped"] += 1
        amp = (vel / 127.0) ** VEL_GAMMA * (1.0 - self.soft_scale * self.pedals["soft"])
        row[LEVEL] = max(self._level_at(p, t), amp)
        self._anchor[p] = t
        row[VEL_ON], row[T_ON], row[RELEASED], row[T_OFF], row[VEL_OFF] = vel, t, 0.0, np.nan, 0.0
        self._held[p] = True
        t_ms = round(t * 1000.0, 3)
        self._t_on_ms[p] = t_ms
        self._emit(MUSIC_NOTE, {"seq": 0, "t_ms": t_ms, "on": True, "pitch": p, "vel": int(vel),
                                "source": self.source, "sess": self.sess}, t_ms)

    def _release(self, p: int, vel_off: int, t: float) -> None:
        if not self._held[p]:
            return
        row = self.table[p]
        row[LEVEL] = self._level_at(p, t)                # re-anchor under the held tau ...
        self._anchor[p] = t
        self._held[p] = False                            # ... before it changes
        row[RELEASED], row[T_OFF], row[VEL_OFF] = 1.0, t, vel_off
        t_ms = round(t * 1000.0, 3)
        self._emit(MUSIC_NOTE, {"seq": 0, "t_ms": t_ms, "on": False, "pitch": p, "vel": int(vel_off),
                                "source": self.source, "sess": self.sess}, t_ms)

    def _pedal(self, cc: int, value: int, t: float) -> None:
        if value == self._cc[cc]:
            return
        name = PEDAL_CC[cc]
        old, new = self.pedals[name], value / 127.0
        if name == "damper":
            self._reanchor(~(self._held | self._sost | _UNDAMPED), t)
        elif name == "sostenuto":
            if old < SOSTENUTO_ON <= new:
                self._sost[:] = self._held               # held keys already ring at tau_sus: no re-anchor
            elif new < SOSTENUTO_ON <= old:
                self._reanchor(self._sost & ~self._held & ~_UNDAMPED, t)
                self._sost[:] = False
        self._cc[cc] = value
        self.pedals[name] = new
        t_ms = round(t * 1000.0, 3)
        self._emit(MUSIC_PEDAL, {"seq": 0, "t_ms": t_ms, "pedal": name, "value": round(new, 4),
                                 "source": self.source, "sess": self.sess}, t_ms)

    def _release_all(self, t: float) -> int:
        held = np.flatnonzero(self._held)
        for p in held:
            self._release(int(p), 0, t)
        return len(held)

    def _emit(self, mtype: str, data: dict, t_ms: float) -> None:
        self.seq += 1
        data["seq"] = self.seq
        self._t_activity = self._t_change = self._arrival
        self._last_event_ms = max(self._last_event_ms, t_ms)
        if self.voice:
            self._publish({"type": mtype, "data": data})
        else:
            self._stats["muted"] += 1
            if len(self._muted) < MUTED_KEEP:
                self._muted.append({"type": mtype, "data": data})

    # -- out --------------------------------------------------------------------------------------------------------
    def present_at(self, now_s: float) -> bool:
        """What ``present`` would be at ``now_s`` (no side effects; ``__main__`` uses it to hand over the voice
        within the packet that made a piano present, rather than at the next tick)."""
        if not self.sess:
            return False
        fresh = self._last_rx is not None and now_s - self._last_rx <= STALE_S
        down = bool(self._held.any()) or any(v > PEDAL_DOWN for v in self.pedals.values())
        return fresh and (down or now_s - self._t_activity < PRESENT_HOLD_S)

    def _refresh(self, now_s: float) -> bool:
        """Staleness and ``present`` at ``now_s``; True if ``present`` flipped."""
        fresh = self._last_rx is not None and now_s - self._last_rx <= STALE_S
        if not fresh and self._held.any():
            self._arrival = now_s
            self._stats["stale_releases"] += self._release_all(now_s)
        present = self.present_at(now_s)
        flipped = present != self.present
        self.present = present
        if flipped:
            self._t_change = now_s
        return flipped

    def tick(self, now_s: float) -> None:
        """Staleness, ``present`` and music.notes (rates in the module docstring; nothing without the voice)."""
        if not self.sess:
            return
        self._muted.clear()
        flipped = self._refresh(now_s)
        if not self.voice:
            return
        every = NOTES_FAST_S if now_s - self._t_change < CHANGED_S else NOTES_SLOW_S
        if flipped or (self.present and now_s - self._t_notes >= every - 1e-6):
            self._t_notes = now_s
            self._publish({"type": MUSIC_NOTES, "data": self.notes(now_s)})

    def set_voice(self, on: bool, now_s: float) -> None:
        """Give (True) or take (False) this Piano's voice on the bus. Gaining it first publishes the note/pedal
        messages muted since the current packet (or tick) began, so the strike that made this piano present goes
        out as its own music.note, then a music.notes snapshot (after the staleness and ``present`` update at
        ``now_s``); the next second runs at the fast rate."""
        on = bool(on)
        if on == self.voice:
            return
        self.voice = on
        if not on or not self.sess:
            self._muted.clear()
            return
        held, self._muted = self._muted, []
        for m in held:
            if m["data"].get("sess") == self.sess:       # not a restart's releases under the old sess
                self._publish(m)
        self._refresh(now_s)
        self._t_change = self._t_notes = now_s
        self._stats["voiced"] += 1
        self._publish({"type": MUSIC_NOTES, "data": self.notes(now_s)})

    def notes(self, now_s: float) -> dict:
        """The music.notes body (contract.NotesData) at host time ``now_s``."""
        keys = [[int(p), int(self.table[p, VEL_ON]), float(self._t_on_ms[p])] for p in np.flatnonzero(self._held)]
        return {"seq": self.seq, "t_ms": round(max(now_s * 1000.0, self._last_event_ms), 3), "keys": keys,
                "pedals": {k: round(v, 4) for k, v in self.pedals.items()}, "present": self.present,
                "source": self.source, "sess": self.sess}

    def levels(self, t_s: float) -> np.ndarray:
        """(128,) sounding amplitude 0..1 of every pitch at host time ``t_s`` (pedal-held and ringing keys
        included; a fresh array)."""
        tau = self._taus(self._tau)
        np.subtract(t_s, self._anchor, out=self._buf)
        np.maximum(self._buf, 0.0, out=self._buf)
        self._buf /= tau
        np.negative(self._buf, out=self._buf)
        np.exp(self._buf, out=self._buf)
        out = self.table[:, LEVEL] * self._buf
        out[out < SILENT_LEVEL] = 0.0
        return out

    def held(self) -> np.ndarray:
        """The pitches physically down (a copy)."""
        return np.flatnonzero(self._held)

    def stats(self) -> dict:
        return {**self._stats, "wire_seq": self._wire_seq, "wire_sess": self._wire_sess, "sess": self.sess,
                "seq": self.seq, "present": self.present, "voice": self.voice, "held": int(self._held.sum())}
