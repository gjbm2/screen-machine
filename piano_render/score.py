"""MIDI file -> Performance: timed key and pedal events, plus per-block pedal/damper state for the realism stage.

Events are (t_s, kind, num, value):
  ('on',  key, velocity 1..127)          note-on
  ('off', key, release velocity 1..127)  note-off; a note-on with velocity 0, or a note-off with velocity 0
                                          (no release velocity recorded), counts as 64, the MIDI default
  ('cc',  64|66|67, 0..127)              damper / sostenuto / soft pedal, raw and continuous
Only keys 21..108 (A0..C8) are kept; other notes are counted in Performance.dropped. Other CCs, pitch bend and
program changes are dropped (a piano has three pedals). All kept channels feed one keyboard (show_service's rule);
``channels`` names the channels to keep, 1-16 (None = all). Times come from mido's tempo map, so a type-1 file
with tempo changes on any track is timed exactly. Events at the same time keep the file's order (an off before a
re-strike at the same tick stays before it).

Performance.controls(rate_hz) samples the keyboard and pedal state on a grid t_i = i / rate_hz: row i is the state
after every event at t <= t_i. The damper and sostenuto rules are show_service/midi.py's (constants copied below, so
what you hear decays the way the screens draw it); the damper lift follows the half-pedal curve from the realism
research (dead zone over the first quarter of travel, fully up at three quarters).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, NamedTuple, Optional, Sequence

import numpy as np

KEY_LO, KEY_HI = 21, 108                  # A0 .. C8
N_KEYS = KEY_HI - KEY_LO + 1              # 88
PEDAL_CCS = (64, 66, 67)                  # damper, sostenuto, soft (una corda)
DEFAULT_RELEASE_VELOCITY = 64

# --- Copied from show_service/midi.py (:33-39 docstring, :86-106 constants); do not import show_service. ---------
# Amplitude model shared by the director and the screens:
#   * a strike sets max(ringing level, (vel / 127) ** 1.2 * (1 - SOFT_SCALE * soft));
#   * held keys, keys latched by the sostenuto, and keys from MIDI 89 up (no dampers) ring with
#     tau_sus = max(0.3, 5 * 0.5 ** ((pitch - 21) / 24)) s;
#   * other released keys damp with 0.12 + (tau_sus - 0.12) * damper ** 2 (the damper is proportional);
#   * the sostenuto latches the keys down when it crosses up through 0.5 and lets them go when it falls below 0.5.
VEL_GAMMA = 1.2
TAU_A0_S = 5.0                 # sustain time constant at A0 ...
TAU_HALVING_SEMIS = 24.0       # ... halving every two octaves
TAU_MIN_S = 0.3
DAMP_S = 0.12                  # a released key with the damper up
UNDAMPED_FROM = 89             # F6 up: no dampers on a grand
SOSTENUTO_ON = 0.5
SOFT_SCALE = 0.25              # a strike with the soft pedal fully down sounds 25 % quieter
_N_PITCH = 128
TAU_SUS_128 = np.maximum(TAU_MIN_S, TAU_A0_S * np.power(0.5, (np.arange(_N_PITCH) - 21.0) / TAU_HALVING_SEMIS))
# --- end of the copy -------------------------------------------------------------------------------------------

KEYS = np.arange(KEY_LO, KEY_HI + 1)
TAU_SUS = TAU_SUS_128[KEY_LO:KEY_HI + 1]          # (88,) seconds
UNDAMPED = KEYS >= UNDAMPED_FROM                  # (88,) bool

# Half-pedal: damper lift d = clip((cc/127 - 0.25) / 0.5, 0, 1) (realism research, item 2).
DAMPER_DEAD = 0.25
DAMPER_SPAN = 0.5


def damper_lift(cc: "float | np.ndarray") -> "float | np.ndarray":
    """CC64 value(s) 0..127 -> damper lift 0..1 (0 = dampers on the strings)."""
    return np.clip((np.asarray(cc, dtype=np.float64) / 127.0 - DAMPER_DEAD) / DAMPER_SPAN, 0.0, 1.0)


class Event(NamedTuple):
    t_s: float
    kind: str          # 'on' | 'off' | 'cc'
    num: int           # MIDI key (21..108) or CC number (64 | 66 | 67)
    value: int         # velocity, release velocity, or CC value

    @property
    def key(self) -> int:
        return self.num


@dataclass
class Note:
    key: int
    t_on: float
    t_off: float       # inf if never released
    velocity: int
    release_velocity: int


@dataclass
class Controls:
    """Per-block keyboard and pedal state; row i is the state at t[i] = i / rate_hz."""
    rate_hz: float
    t: np.ndarray            # (n,) seconds
    key_down: np.ndarray     # (n, 88) bool: the key is physically down
    damper_lift: np.ndarray  # (n,) 0..1 from CC64 (half-pedal curve)
    sostenuto: np.ndarray    # (n, 88) bool: latched by the sostenuto
    una_corda: np.ndarray    # (n,) 0..1 = CC67 / 127
    undamped: np.ndarray     # (88,) bool: no damper (MIDI >= 89)
    struck: np.ndarray       # (n, 88) float32 seconds since the key's last note-on (inf = not struck yet)

    @property
    def n(self) -> int:
        return len(self.t)

    def free(self) -> np.ndarray:
        """(n, 88) bool: strings with no damper on them regardless of the pedal (down, latched, or undamped)."""
        return self.key_down | self.sostenuto | self.undamped[None, :]

    def damping_tau(self) -> np.ndarray:
        """(n, 88) float32 decay time constant in seconds, show_service/midi.py's rule: free strings ring with
        TAU_SUS, the rest with DAMP_S + (TAU_SUS - DAMP_S) * damper ** 2 (damper = this module's lift)."""
        d2 = (self.damper_lift ** 2)[:, None]
        damped = DAMP_S + (TAU_SUS[None, :] - DAMP_S) * d2
        return np.where(self.free(), TAU_SUS[None, :], damped).astype(np.float32)


@dataclass
class Performance:
    events: list[Event]
    path: Optional[str] = None
    file_length_s: float = 0.0          # mido's length (to the end-of-track meta)
    dropped: int = 0                    # notes outside 21..108
    channels: Optional[tuple[int, ...]] = None
    _notes: Optional[list[Note]] = field(default=None, repr=False)

    @property
    def duration_s(self) -> float:
        """Time of the last event (0 if none)."""
        return self.events[-1].t_s if self.events else 0.0

    @property
    def first_note_s(self) -> Optional[float]:
        for e in self.events:
            if e.kind == "on":
                return e.t_s
        return None

    def notes(self) -> list[Note]:
        """Note spans (last event wins per key: a re-strike without a note-off ends the previous note)."""
        if self._notes is None:
            out: list[Note] = []
            open_: dict[int, Note] = {}
            for e in self.events:
                if e.kind == "on":
                    if e.num in open_:
                        open_[e.num].t_off = e.t_s
                    n = Note(e.num, e.t_s, math.inf, e.value, DEFAULT_RELEASE_VELOCITY)
                    open_[e.num] = n
                    out.append(n)
                elif e.kind == "off" and e.num in open_:
                    n = open_.pop(e.num)
                    n.t_off, n.release_velocity = e.t_s, e.value
            self._notes = out
        return self._notes

    def controls(self, rate_hz: float, duration_s: Optional[float] = None) -> Controls:
        """Sample the state on t_i = i / rate_hz for t_i in [0, duration_s] (default: duration_s of the piece);
        pass the audio length to cover a render's tail. A key is down from its note-on to its note-off (last event
        wins); the sostenuto latches the keys down at the moment CC66 rises through 64 and releases them when it
        falls below 64; the damper lift and una corda hold their last CC value."""
        if rate_hz <= 0:
            raise ValueError("rate_hz must be > 0")
        total = self.duration_s if duration_s is None else max(0.0, float(duration_s))
        n = int(math.floor(round(total * rate_hz, 9))) + 1
        t = np.arange(n, dtype=np.float64) / rate_hz
        key_down = np.zeros((n, N_KEYS), bool)
        sost = np.zeros((n, N_KEYS), bool)
        damper_cc = np.zeros(n, np.float64)
        soft_cc = np.zeros(n, np.float64)
        last_on = np.full((n, N_KEYS), -np.inf, np.float32)

        cur_down = np.zeros(N_KEYS, bool)
        cur_sost = np.zeros(N_KEYS, bool)
        cur_last = np.full(N_KEYS, -np.inf)
        cur_damper = 0.0
        cur_soft = 0.0
        sost_on = False
        row = 0

        def fill(upto: int) -> None:
            nonlocal row
            upto = min(upto, n)
            if upto > row:
                key_down[row:upto] = cur_down
                sost[row:upto] = cur_sost
                damper_cc[row:upto] = cur_damper
                soft_cc[row:upto] = cur_soft
                last_on[row:upto] = cur_last
                row = upto

        for e in self.events:
            first_row = int(math.ceil(round(e.t_s * rate_hz, 9)))   # the first row with t_i >= e.t_s
            fill(first_row)
            if row >= n:
                break
            if e.kind == "on":
                k = e.num - KEY_LO
                cur_down[k] = True
                cur_last[k] = e.t_s
            elif e.kind == "off":
                cur_down[e.num - KEY_LO] = False
            elif e.num == 64:
                cur_damper = float(e.value)
            elif e.num == 67:
                cur_soft = float(e.value)
            elif e.num == 66:
                now_on = e.value / 127.0 >= SOSTENUTO_ON
                if now_on and not sost_on:
                    cur_sost = cur_down.copy()
                elif not now_on and sost_on:
                    cur_sost = np.zeros(N_KEYS, bool)
                sost_on = now_on
        fill(n)

        struck = np.subtract(t.astype(np.float32)[:, None], last_on, out=last_on)   # inf where never struck
        return Controls(
            rate_hz=float(rate_hz),
            t=t,
            key_down=key_down,
            damper_lift=damper_lift(damper_cc),
            sostenuto=sost,
            una_corda=soft_cc / 127.0,
            undamped=UNDAMPED.copy(),
            struck=struck,
        )


def load_midi(path: "str | Path", channels: Optional[Iterable[int]] = None) -> Performance:
    """Read a .mid file into a Performance (module docstring). ``channels``: 1-16, None = all."""
    import mido

    keep = None if channels is None else {int(c) for c in channels}
    if keep is not None and not all(1 <= c <= 16 for c in keep):
        raise ValueError("channels are 1-16")
    mf = mido.MidiFile(str(path))
    events: list[Event] = []
    dropped = 0
    t = 0.0
    for msg in mf:                         # merged tracks, msg.time = delta seconds (tempo map applied)
        t += msg.time
        if msg.is_meta or not hasattr(msg, "channel"):
            continue
        if keep is not None and (msg.channel + 1) not in keep:
            continue
        typ = msg.type
        if typ in ("note_on", "note_off"):
            if not KEY_LO <= msg.note <= KEY_HI:
                if typ == "note_on" and msg.velocity > 0:
                    dropped += 1
                continue
            if typ == "note_on" and msg.velocity > 0:
                events.append(Event(t, "on", msg.note, int(msg.velocity)))
            else:
                v = msg.velocity if typ == "note_off" and msg.velocity > 0 else DEFAULT_RELEASE_VELOCITY
                events.append(Event(t, "off", msg.note, int(v)))
        elif typ == "control_change" and msg.control in PEDAL_CCS:
            events.append(Event(t, "cc", msg.control, int(msg.value)))
    # mido yields in time order already; a stable sort guards hand-built files and keeps same-time file order.
    events.sort(key=lambda e: e.t_s)
    return Performance(events=events, path=str(path), file_length_s=float(mf.length), dropped=dropped,
                       channels=None if keep is None else tuple(sorted(keep)))


def from_events(events: Sequence[tuple], path: Optional[str] = None) -> Performance:
    """A Performance from (t_s, kind, num, value) tuples (tests, live recordings already in memory)."""
    evs = [Event(float(e[0]), str(e[1]), int(e[2]), int(e[3])) for e in events]
    for e in evs:
        if e.kind not in ("on", "off", "cc"):
            raise ValueError(f"bad event kind {e.kind!r}")
    evs.sort(key=lambda e: e.t_s)
    return Performance(events=evs, path=path, file_length_s=evs[-1].t_s if evs else 0.0)
