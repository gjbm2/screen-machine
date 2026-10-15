"""The test piano: replays piano playing as show-bus messages, so the room can be exercised from another room.

``TestPiano`` emits exactly the bus shapes the real piano produces through show_service (docs/SHOW_ENGINE.md
§11.1; parsed by sound_view.py): ``music.note``, ``music.pedal`` and a ``music.notes`` snapshot every SNAPSHOT_S,
all with ``source: "test"`` and a fresh ``sess``.  The renderer can run it in-process until show_service hosts it
as stream ``test-piano``; :meth:`TestPiano.midi` gives the same performance as raw MIDI for that sender.

Material: a ``.mid`` file (``TestPiano.from_file``; type 0/1, tempo map, running status; every channel is the one
keyboard; CC64/66/67 are the pedals), or a built-in phrase (``PHRASES``):
  sweep        every key A0..C8 once (does each key land where it should?)
  scale        two octaves of C major up and down, with a crescendo
  arpeggios    I-vi-IV-V, the damper changed every bar
  chords       slow pedalled maj7 chords under a sparse melody (Satie-like)
  repeats      fast repeated notes with a spike in velocity (the hot-note guard)
  sostenuto    a low chord latched by the sostenuto under a staccato tune
  medley       all of the above in turn (the default)

    piano = TestPiano("medley", loop=True)
    for msg in piano.poll(now_ms):        # call often (every frame or every few ms)
        view.on_message(msg)
"""
from __future__ import annotations

import pathlib
import struct
import uuid
from typing import Iterable, Optional

SNAPSHOT_S = 0.2
LOOP_GAP_S = 3.0
NOTE_ON, NOTE_OFF, CC = 0x90, 0x80, 0xB0
PEDAL_CC = {64: "damper", 66: "sostenuto", 67: "soft"}

Event = tuple[float, int, int, int]           # (t_s from the start, status, data1, data2): channel 0


# -- phrases ----------------------------------------------------------------------------------------------------------
class _Score:
    def __init__(self, bpm: float = 96.0) -> None:
        self.beat = 60.0 / bpm
        self.ev: list[Event] = []

    def note(self, at: float, pitch: int, beats: float, vel: int) -> None:
        t = at * self.beat
        self.ev.append((t, NOTE_ON, pitch, max(1, min(127, vel))))
        self.ev.append((t + beats * self.beat * 0.95, NOTE_OFF, pitch, 40))

    def pedal(self, at: float, cc: int, value: int) -> None:
        self.ev.append((at * self.beat, CC, cc, value))


def _sweep() -> list[Event]:
    s = _Score(240)
    for i, p in enumerate(range(21, 109)):
        s.note(i * 0.5, p, 0.5, 70)
    return s.ev


def _scale() -> list[Event]:
    s = _Score(110)
    steps = [0, 2, 4, 5, 7, 9, 11]
    up = [48 + 12 * (i // 7) + steps[i % 7] for i in range(15)]
    run = up + up[-2::-1]
    for i, p in enumerate(run):
        s.note(i * 0.5, p, 0.5, 40 + int(70 * i / len(run)))
    return s.ev


def _arpeggios() -> list[Event]:
    s = _Score(84)
    chords = [(48, [0, 4, 7, 12]), (45, [0, 3, 7, 12]), (41, [0, 4, 7, 12]), (43, [0, 4, 7, 10])]
    for bar, (root, shape) in enumerate(chords * 2):
        at = bar * 4.0
        s.pedal(at, 64, 0)
        s.pedal(at + 0.1, 64, 127)
        for k, iv in enumerate(shape * 2):
            s.note(at + k * 0.5, root + iv + (12 if k >= 4 else 0), 0.5, 55 + 10 * (k % 4 == 0))
    s.pedal(len(chords) * 2 * 4.0, 64, 0)
    return s.ev


def _chords() -> list[Event]:
    s = _Score(66)
    voicings = [[43, 55, 59, 62, 66], [38, 54, 57, 61, 64]]        # Gmaj7 / Dmaj7
    melody = [(1.0, 78), (2.0, 76), (3.0, 74), (5.0, 81), (6.0, 79), (7.0, 78)]
    for bar in range(4):
        at = bar * 8.0
        s.pedal(at, 64, 0)
        s.pedal(at + 0.15, 64, 110)
        for p in voicings[bar % 2]:
            s.note(at, p, 7.5, 48)
        for off, p in melody:
            s.note(at + off, p, 0.9, 62)
    s.pedal(32.0, 64, 0)
    return s.ev


def _repeats() -> list[Event]:
    s = _Score(120)
    for i in range(24):
        vel = 115 if i in (9, 17) else 60                          # two hot notes
        s.note(i * 0.25, 67, 0.2, vel)
    return s.ev


def _sostenuto() -> list[Event]:
    s = _Score(100)
    for p in (36, 43, 48):
        s.note(0.0, p, 1.0, 70)
    s.pedal(0.5, 66, 127)                                          # latch the chord while it is held
    tune = [72, 74, 76, 79, 76, 74, 72, 71, 72, 79, 77, 76]
    for i, p in enumerate(tune):
        s.note(2.0 + i * 0.5, p, 0.25, 64)
    s.pedal(9.0, 66, 0)
    return s.ev


_BUILDERS = {"sweep": _sweep, "scale": _scale, "arpeggios": _arpeggios, "chords": _chords,
             "repeats": _repeats, "sostenuto": _sostenuto}
PHRASES = tuple(_BUILDERS) + ("medley",)
PHRASE_LABELS = {                              # what the Room page calls them, in its order
    "medley": "A bit of everything",
    "chords": "Slow pedalled chords",
    "arpeggios": "Flowing arpeggios",
    "scale": "Scales",
    "sostenuto": "Held bass, tune above",
    "repeats": "Fast repeated notes",
    "sweep": "Every key, low to high",
}
LIBRARY_DIR = pathlib.Path(__file__).resolve().parents[1] / "config" / "live" / "test-piano"


def library(root: pathlib.Path = LIBRARY_DIR) -> list[dict]:
    """The pieces a test piano can play: the built-in phrases, then every .mid in ``root`` (by file name)."""
    out = [{"id": k, "label": v, "kind": "phrase"} for k, v in PHRASE_LABELS.items()]
    try:
        files = sorted(p for p in root.iterdir() if p.suffix.lower() in (".mid", ".midi") and p.is_file())
    except OSError:
        files = []
    for p in files:
        out.append({"id": f"file:{p.name}", "label": p.stem.replace("_", " ").replace("-", " ").strip().capitalize(),
                    "kind": "file"})
    return out


def resolve(piece: str, root: pathlib.Path = LIBRARY_DIR) -> "TestPiano":
    """A looping TestPiano for a piece id from :func:`library` (ValueError if unknown)."""
    if piece.startswith("file:"):
        name = piece[5:]
        path = root / name
        if "/" in name or "\\" in name or name.startswith(".") or not path.is_file():
            raise ValueError(f"no such piece: {piece!r}")
        return TestPiano.from_file(path, loop=True)
    if piece not in PHRASES:
        raise ValueError(f"no such piece: {piece!r}")
    return TestPiano(piece, loop=True)


def phrase(name: str) -> list[Event]:
    """A built-in phrase as MIDI events sorted by time."""
    if name == "medley":
        out: list[Event] = []
        t0 = 0.0
        for n in ("chords", "arpeggios", "scale", "sostenuto", "repeats", "sweep"):
            ev = _BUILDERS[n]()
            out += [(t0 + t, st, a, b) for t, st, a, b in ev]
            t0 += max(t for t, *_ in ev) + 2.0
        return sorted(out, key=lambda e: e[0])
    if name not in _BUILDERS:
        raise ValueError(f"unknown phrase {name!r}; one of {', '.join(PHRASES)}")
    return sorted(_BUILDERS[name](), key=lambda e: e[0])


# -- standard MIDI files ------------------------------------------------------------------------------------------------
def _varlen(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def read_midi(data: bytes) -> list[Event]:
    """Parse a standard MIDI file into note and pedal events (seconds from the start; every channel as one)."""
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen, = struct.unpack(">I", data[4:8])
    _fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        fps, tpf = 256 - (division >> 8), division & 0xFF
        ticks_per_s, tpq = fps * tpf, None
    else:
        ticks_per_s, tpq = None, division
    raw: list[tuple[int, int, int, int, int]] = []    # (tick, order, status, d1, d2); status 0xFF51 = tempo
    tempos: list[tuple[int, int]] = []
    i = 8 + hlen
    order = 0
    for _ in range(ntrks):
        if data[i:i + 4] != b"MTrk":
            raise ValueError("bad track")
        tlen, = struct.unpack(">I", data[i + 4:i + 8])
        j, end, tick, running = i + 8, i + 8 + tlen, 0, 0
        while j < end:
            delta, j = _varlen(data, j)
            tick += delta
            st = data[j]
            if st == 0xFF:
                kind = data[j + 1]
                ln, j = _varlen(data, j + 2)
                if kind == 0x51 and ln == 3:
                    tempos.append((tick, int.from_bytes(data[j:j + 3], "big")))
                j += ln
                continue
            if st in (0xF0, 0xF7):
                ln, j = _varlen(data, j + 1)
                j += ln
                continue
            if st & 0x80:
                running = st
                j += 1
            st = running
            kind = st & 0xF0
            if kind in (0xC0, 0xD0):
                j += 1
                continue
            d1, d2 = data[j], data[j + 1]
            j += 2
            if kind == NOTE_ON and d2 == 0:
                kind = NOTE_OFF
            if kind in (NOTE_ON, NOTE_OFF) or (kind == CC and d1 in PEDAL_CC):
                raw.append((tick, order, kind, d1, d2))
                order += 1
        i = end
    raw.sort()
    tempos.sort()

    def seconds(tick: int) -> float:
        if ticks_per_s:
            return tick / ticks_per_s
        t, last_tick, us = 0.0, 0, 500000
        for at, tempo in tempos:
            if at >= tick:
                break
            t += (at - last_tick) * us / tpq / 1e6
            last_tick, us = at, tempo
        return t + (tick - last_tick) * us / tpq / 1e6

    return [(seconds(tick), st, d1, d2) for tick, _o, st, d1, d2 in raw]


# -- the player -------------------------------------------------------------------------------------------------------
class TestPiano:
    """Plays MIDI events in real time as show-bus messages.  Not thread-safe: one caller polls it."""

    __test__ = False                                   # not a pytest class

    def __init__(self, material: str | Iterable[Event] = "medley", *, loop: bool = False,
                 start_ms: Optional[float] = None, speed: float = 1.0) -> None:
        self.events: list[Event] = phrase(material) if isinstance(material, str) else sorted(material)
        if not self.events:
            raise ValueError("nothing to play")
        self.loop, self.speed = loop, speed
        self.length_s = max(t for t, *_ in self.events)
        self.sess = uuid.uuid4().hex[:8]
        self.seq = 0
        self.start_ms = start_ms
        self.i = 0
        self.held: dict[int, tuple[int, float]] = {}  # pitch -> (vel, t_on_ms)
        self.pedals = {"damper": 0.0, "soft": 0.0, "sostenuto": 0.0}
        self.next_snap_ms: Optional[float] = None
        self.done = False

    @classmethod
    def from_file(cls, path: str | pathlib.Path, **kw) -> "TestPiano":
        return cls(read_midi(pathlib.Path(path).read_bytes()), **kw)

    def midi(self) -> list[Event]:
        """The performance as raw MIDI events (t_s, status, data1, data2), for a VBAN sender."""
        return list(self.events)

    def _msg(self, mtype: str, data: dict) -> dict:
        return {"type": mtype, "data": {**data, "source": "test", "sess": self.sess}}

    def _snapshot(self, t_ms: float) -> dict:
        return self._msg("music.notes", {"seq": self.seq, "t_ms": t_ms, "present": True,
                                         "keys": [[p, v, t] for p, (v, t) in sorted(self.held.items())],
                                         "pedals": dict(self.pedals)})

    def poll(self, now_ms: float) -> list[dict]:
        """The messages due by ``now_ms`` (host monotonic ms), in order."""
        if self.start_ms is None:
            self.start_ms = now_ms
        if self.next_snap_ms is None:
            self.next_snap_ms = self.start_ms
        out: list[dict] = []
        while not self.done:
            if self.i >= len(self.events):
                if not self.loop:
                    self.done = True
                    break
                self.start_ms += (self.length_s + LOOP_GAP_S) * 1000.0 / self.speed
                self.i = 0
            t_s, st, d1, d2 = self.events[self.i]
            t_ms = self.start_ms + t_s * 1000.0 / self.speed
            if t_ms > now_ms:
                break
            self.i += 1
            if st == NOTE_ON:
                self.seq += 1
                self.held[d1] = (d2, t_ms)
                out.append(self._msg("music.note", {"seq": self.seq, "t_ms": t_ms, "on": True, "pitch": d1,
                                                    "vel": d2}))
            elif st == NOTE_OFF:
                if self.held.pop(d1, None) is None:
                    continue
                self.seq += 1
                out.append(self._msg("music.note", {"seq": self.seq, "t_ms": t_ms, "on": False, "pitch": d1,
                                                    "vel": d2}))
            elif st == CC:
                name = PEDAL_CC[d1]
                self.seq += 1
                self.pedals[name] = d2 / 127.0
                out.append(self._msg("music.pedal", {"seq": self.seq, "t_ms": t_ms, "pedal": name,
                                                     "value": d2 / 127.0}))
        if now_ms >= self.next_snap_ms:
            out.append(self._snapshot(now_ms))
            self.next_snap_ms = now_ms + SNAPSHOT_S * 1000.0
        return out
