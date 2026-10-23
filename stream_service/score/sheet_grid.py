"""A sequenced MIDI file straight to bars from its own grid (owner, 23 Oct: "it's surely not that computationally
heavy to turn midi into a score").

Most library files are sequenced: every note sits on the file's beat grid (ticks per quarter), with the time and key
signatures in the file and the tempo, rubato included, in its tempo map.  Their bar lines, metre and note values
are read, not guessed: no beat tracking (the live score's PM2S windows cost ~25 ms of CPU a note), milliseconds a
piece.  A performance (the Steinway's recordings, a file played in) has no grid: sheet.py tracks its beats once
over the whole piece instead (sheet_analysis.py).

The notation is the live score's own: quantize.py's bar maths (_voice: chords, rests, ties, values split at the
beat), its dynamics levels and key profiles, mei.py's spelling and engraving.  A staff is one voice (as on the
screens); triplets come out on the 16th grid.  Hands: a two-part file (two tracks or channels) puts its higher part
on the treble staff and its lower on the bass (a note past 3 ledger lines goes to the other staff); one part splits
at middle C.  Key: the file's key signatures; none (or all C) and the notes say otherwise, the Albrecht-Shanahan
profile over the whole piece.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional

from .quantize import (BASS_HIGH, DYNAMICS, KEY_PROFILE, PROFILES, TREBLE_LOW, Bar, ScoreBuilder, _fifths_of)

GRID_DIV = 12                        # the lattice a sequenced onset sits on: a 16th and an eighth-triplet (ppq / 12)
ON_GRID = 0.9                        # this share of onsets on it (a performance: ~25 % by chance)
MIN_NOTES = 8
KEY_MARGIN = 0.15                    # a C-only file is re-keyed when the best key's fit beats C's by this much


@dataclass
class Note:
    on: int                          # ticks
    off: int
    pitch: int
    vel: int
    part: tuple                      # (track, channel)
    id: int = 0


@dataclass
class MidiFile:
    ppq: int
    notes: list = field(default_factory=list)
    tempos: list = field(default_factory=list)        # (tick, microseconds a quarter)
    times: list = field(default_factory=list)         # (tick, numerator, denominator)
    keys: list = field(default_factory=list)          # (tick, sharps (-7..7), minor)
    pedal: list = field(default_factory=list)         # (tick, down)


def _varlen(b: bytes, i: int) -> tuple[int, int]:
    v = 0
    while True:
        c = b[i]
        i += 1
        v = (v << 7) | (c & 0x7F)
        if not c & 0x80:
            return v, i


def read_ticks(data: bytes) -> Optional[MidiFile]:
    """A standard MIDI file in ticks, or None (SMPTE time: no beat grid).  ValueError on a broken file."""
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    hlen, = struct.unpack(">I", data[4:8])
    _fmt, ntrks, division = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        return None
    mf = MidiFile(ppq=division)
    held: dict = {}
    i = 8 + hlen
    for trk in range(ntrks):
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
                body = data[j:j + ln]
                if kind == 0x51 and ln == 3:
                    mf.tempos.append((tick, int.from_bytes(body, "big")))
                elif kind == 0x58 and ln >= 2:
                    mf.times.append((tick, body[0], 2 ** body[1]))
                elif kind == 0x59 and ln >= 2:
                    mf.keys.append((tick, struct.unpack("b", body[:1])[0], bool(body[1])))
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
            kind, ch = st & 0xF0, st & 0x0F
            if kind in (0xC0, 0xD0):
                j += 1
                continue
            d1, d2 = data[j], data[j + 1]
            j += 2
            if kind == 0x90 and d2 > 0:
                held.setdefault((trk, ch, d1), []).append((tick, d2))
            elif kind in (0x80, 0x90):
                ons = held.get((trk, ch, d1))
                if ons:
                    on, vel = ons.pop(0)
                    mf.notes.append(Note(on, max(tick, on + 1), d1, vel, (trk, ch)))
            elif kind == 0xB0 and d1 == 64:
                mf.pedal.append((tick, d2 >= 64))
        i = end
    for (trk, ch, p), ons in held.items():          # never released: to the end of its track
        for on, vel in ons:
            mf.notes.append(Note(on, on + mf.ppq, p, vel, (trk, ch)))
    mf.notes.sort(key=lambda n: (n.on, n.pitch))
    for k, n in enumerate(mf.notes):
        n.id = k + 1
    mf.tempos.sort()
    mf.times.sort()
    mf.keys.sort()
    mf.pedal.sort()
    return mf


def on_grid(mf: MidiFile) -> float:
    """The share of onsets on the file's 16th / triplet lattice."""
    if not mf.notes:
        return 0.0
    step = mf.ppq / GRID_DIV
    tol = max(1.0, mf.ppq / 96)
    hit = sum(1 for n in mf.notes if min(n.on % step, step - n.on % step) <= tol)
    return hit / len(mf.notes)


def sequenced(mf: Optional[MidiFile]) -> bool:
    return mf is not None and len(mf.notes) >= MIN_NOTES and on_grid(mf) >= ON_GRID


def _metre(num: int, den: int) -> tuple[int, int]:
    """A time signature -> (beats, 16ths a beat), as quantize's Bar keeps it (6/8: 2 dotted beats)."""
    if den == 8 and num % 3 == 0:
        return num // 3, 6
    if den == 2:
        return num * 2, 4
    if den == 4:
        return num, 4
    return max(1, num), max(1, 16 // den)


def _key(mf: MidiFile) -> list:
    """[(tick, fifths)]: the file's signatures, or the profile's best key for the whole piece."""
    declared = [(t, sf) for t, sf, _mi in mf.keys if -7 <= sf <= 7]
    w = [0.0] * 12
    for n in mf.notes:
        w[n.pitch % 12] += min(n.off - n.on, 4 * mf.ppq)
    major, minor = PROFILES[KEY_PROFILE]

    def fit(prof, tonic):
        a = [prof[(i - tonic) % 12] for i in range(12)]
        ma, mw = sum(a) / 12, sum(w) / 12
        num = sum((x - ma) * (y - mw) for x, y in zip(a, w))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mw) ** 2 for y in w)) ** 0.5
        return num / den if den else 0.0

    fits = {(t, m): fit(minor if m else major, t) for t in range(12) for m in (False, True)}
    best = max(fits, key=fits.get)
    if any(sf for _t, sf in declared):
        return declared
    c = max(fits[(0, False)], fits[(9, True)])
    return [(0, _fifths_of(*best) if fits[best] - c > KEY_MARGIN or not declared else 0)]


def _staffing(mf: MidiFile) -> dict:
    """Note id -> staff (0 treble, 1 bass)."""
    parts: dict = {}
    for n in mf.notes:
        parts.setdefault(n.part, []).append(n.pitch)
    mean = {p: sum(v) / len(v) for p, v in parts.items()}
    out = {}
    if len(parts) >= 2:
        hi = max(mean, key=mean.get)
        lo = min(mean, key=mean.get)
        for n in mf.notes:
            if n.part == hi:
                st = 0
            elif n.part == lo:
                st = 1
            else:
                st = 0 if mean[n.part] >= 60 else 1
            if st == 0 and n.pitch < TREBLE_LOW:
                st = 1
            elif st == 1 and n.pitch > BASS_HIGH:
                st = 0
            out[n.id] = st
    else:
        for n in mf.notes:
            out[n.id] = 0 if n.pitch >= 60 else 1
    return out


def seconds_of(mf: MidiFile):
    """tick -> seconds through the file's tempo map."""
    tempo = sorted(mf.tempos) or [(0, 500000)]
    ppq = mf.ppq

    def seconds(tick: float) -> float:
        t, last, us = 0.0, 0, 500000
        for at, tp in tempo:
            if at >= tick:
                break
            t += (at - last) * us / ppq / 1e6
            last, us = at, tp
        return t + (tick - last) * us / ppq / 1e6
    return seconds


def write(notes: list, lines: list, staff: dict, keys: list, pedal: list, seconds) -> list:
    """Notes placed on a grid -> [(Bar, fifths)] (sheet.score_doc's input), from the first bar with a note to the last.
    ``notes``: (start, end) in 16ths from the piece's start, pitch, velocity 1-127, id; ``lines``: the bars, (start
    16th, beats, 16ths a beat), in order; ``staff``: id -> 0 treble / 1 bass; ``keys``: [(16th, fifths)] in order;
    ``pedal``: [(16th, down)] in order; ``seconds``: 16th -> s (the bars' times)."""
    if not notes or not lines:
        return []
    notes = sorted(notes, key=lambda n: n[0])
    first = notes[0][0]
    out: list = []
    ki, pi, j, active, down = 0, 0, 0, [], None
    for (lo, B, U) in lines:
        hi = lo + B * U
        if hi <= first:
            continue
        while j < len(notes) and notes[j][0] < hi + U:
            active.append(notes[j])
            j += 1
        segs: tuple = ([], [])
        after: list = [None, None]
        vels = []
        keep = []
        for n in active:
            s, e, pitch, vel, nid = n
            if s >= hi:
                st = staff[nid]
                if after[st] is None or s - lo < after[st]:
                    after[st] = s - lo
                keep.append(n)
                continue
            if e <= lo:
                continue
            keep.append(n)
            segs[staff[nid]].append((max(s, lo) - lo, min(e, hi) - lo, pitch, nid, s < lo, e > hi))
            if lo <= s < hi:
                vels.append(vel / 127.0)
        active = [n for n in keep if n[1] > hi or n[0] >= hi]
        if not any(segs) and j >= len(notes) and not active:
            break
        staves = tuple(ScoreBuilder._voice(sorted(sg), B, U, after[i]) for i, sg in enumerate(segs))
        bar = Bar(index=len(out) + 1, staves=staves, t_start=seconds(lo), t_end=seconds(hi), empty=not any(segs),
                  beats=B, unit=U, pos=len(out))
        if vels:
            m = sum(vels) / len(vels)
            bar.dynamic = next(name for edge, name in DYNAMICS if m < edge)
        while pi < len(pedal) and pedal[pi][0] < hi:
            p, is_down = pedal[pi]
            if is_down != down:
                if p >= lo:
                    bar.pedal.append((p - lo, "down" if is_down else "up"))
                down = is_down
            pi += 1
        while ki + 1 < len(keys) and keys[ki + 1][0] <= lo:
            ki += 1
        out.append((bar, keys[ki][1]))
    while out and out[-1][0].empty:
        out.pop()
    return out


def bars(mf: MidiFile) -> list:
    """A sequenced file (sequenced(mf)) as [(Bar, fifths)], its bar lines from its own time signatures."""
    if not mf.notes:
        return []
    ppq = mf.ppq
    q = lambda tick: int(round(tick * 4 / ppq))          # noqa: E731  ticks -> 16ths from the start
    times = mf.times or [(0, 4, 4)]
    if times[0][0] > 0:
        times = [(0, 4, 4)] + times
    end16 = max(q(n.off) for n in mf.notes)
    lines: list = []                                     # (start 16th, beats, unit)
    for k, (tick, num, den) in enumerate(times):
        B, U = _metre(num, den)
        start = q(tick)
        stop = q(times[k + 1][0]) if k + 1 < len(times) else end16 + B * U
        if lines and start < lines[-1][0] + lines[-1][1] * lines[-1][2]:
            lines.pop()                                  # a change inside a bar: the change wins
        pos = start
        while pos < stop:
            lines.append((pos, B, U))
            pos += B * U
    sec = seconds_of(mf)
    notes = [(q(n.on), max(q(n.off), q(n.on) + 1), n.pitch, n.vel, n.id) for n in mf.notes]
    keys = [(q(t), f) for t, f in _key(mf)]
    pedal = [(q(t), d) for t, d in mf.pedal]
    return write(notes, lines, _staffing(mf), keys, pedal, lambda x: sec(x * ppq / 4))
