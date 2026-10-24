"""Punch-in: a take played on the piano replaces one section of a piece's MIDI (owner, 24 Oct: "record should be on
score page as well (if it's 'mine') so user can record and/or record sections"). Pure: bytes in, bytes out; the
recorder (recorder.py) calls :func:`splice` when Stop names a piece and a section, and keeps the result as a NEW
recording (the original file is never touched).

**The section** is ``[from_s, to_s)`` in the piece's own seconds (the score's bar map: GET /api/v1/score/<id>/follow
gives each bar ``[n, t0, t1]``, t0 its first onset, t1 the next bar's), less SLACK_S at both ends so a bar's first
note (rounded to 1 ms on the way) is inside and the next bar's first is not.

**What goes**: every note whose onset is in the section (with its release, wherever that is), and the section's
pedal events (CC 64/66/67). A note struck before the section and still held at its start is released there; a pedal
down at the start is lifted there. **What stays**: everything else, at the same ticks (the file's own PPQ, tempo
map, time and key signatures: a sequenced piece keeps its grid, so its score still reads off it).

**The take** goes in fitted to the section: its first onset on the section's first onset (the section's start when
it had none), its last on the section's last (a linear stretch, at most MAX_STRETCH either way; a take of one onset,
or a section of one, is shifted only). Releases past the section's end are cut there; a note that would start past
it (a take far longer than the section, beyond the stretch) is left out and counted. At the section's end each pedal
goes back to what the piece had there. The take's velocities and pedal values are its own.

Written as SMF type 0 (library._smf0) with ``title`` as the track name (when given). SMPTE files become 120 bpm at
the matching PPQ, as library.clean_midi does.
"""
from __future__ import annotations

import bisect
import math
from typing import Any, Optional

from . import library

SLACK_S = 0.005          # the section's ends, pulled in (a bar's first onset comes rounded to 1 ms)
MAX_STRETCH = 3.0        # the take is fitted at most this much faster or slower than played
MIN_SPAN_S = 0.05        # onsets closer than this: no stretch (shift only)
PEDALS = (64, 66, 67)
_INS, _ORIG, _ON = 0, 1, 2   # order at one tick: inserted releases and pedal resets, then the file's, then take onsets


class TempoMap:
    """Ticks <-> seconds through a file's tempo metas (parse_smf's events); SMPTE (ppq < 0): one fixed rate."""

    def __init__(self, ppq: int, events: list[tuple]) -> None:
        self.ppq = -ppq if ppq < 0 else (ppq or 480)
        self.seg: list[tuple[int, float, float]] = [(0, 0.0, 0.5 / self.ppq)]   # (tick, second, seconds a tick)
        for e in (events if ppq > 0 else ()):
            if e[2] == 0xFF and e[3] == 0x51 and len(e[4]) == 3:
                spt = (int.from_bytes(e[4], "big") or 500_000) / 1e6 / self.ppq
                t0, s0, p0 = self.seg[-1]
                if e[0] == t0:
                    self.seg[-1] = (t0, s0, spt)
                else:
                    self.seg.append((e[0], s0 + (e[0] - t0) * p0, spt))
        self._ticks = [s[0] for s in self.seg]
        self._secs = [s[1] for s in self.seg]

    def sec(self, tick: float) -> float:
        i = max(0, bisect.bisect_right(self._ticks, tick) - 1)
        t0, s0, spt = self.seg[i]
        return s0 + (tick - t0) * spt

    def tick(self, sec: float) -> float:
        """The exact (fractional) tick of a second."""
        i = max(0, bisect.bisect_right(self._secs, sec) - 1)
        t0, s0, spt = self.seg[i]
        return t0 + (sec - s0) / spt


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def check_section(from_s: Any, to_s: Any) -> tuple[float, float]:
    """(from_s, to_s) as floats, or ValueError("bad_section")."""
    if not _finite(from_s) or not _finite(to_s) or from_s < 0 or to_s <= from_s:
        raise ValueError("bad_section: a section is {from_s, to_s} in the piece's seconds, from_s >= 0, to_s > from_s")
    return float(from_s), float(to_s)


def _take_parts(take: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(notes [(on_s, off_s, ch, pitch, vel, off_vel)], pedals [(t_s, ch, cc, value)]) from (t, order, status, d1, d2)
    events in time order; a note never released ends at the take's last event."""
    notes: list[tuple] = []
    pedals: list[tuple] = []
    held: dict[tuple[int, int], list[tuple[float, int]]] = {}
    last = max((e[0] for e in take), default=0.0)
    for t, _o, st, d1, d2 in sorted(take, key=lambda e: (e[0], e[1])):
        kind, ch = st & 0xF0, st & 0x0F
        if kind == 0x90 and d2 > 0:
            q = held.setdefault((ch, d1), [])
            while q:                                     # a re-strike of a held key ends the note before it
                on, vel = q.pop(0)
                notes.append((on, t, ch, d1, vel, 0))
            q.append((t, d2))
        elif kind in (0x80, 0x90):
            q = held.get((ch, d1))
            if q:
                on, vel = q.pop(0)
                notes.append((on, t, ch, d1, vel, d2 if kind == 0x80 else 0))
        elif kind == 0xB0 and d1 in PEDALS:
            pedals.append((t, ch, d1, d2))
    for (ch, p), q in held.items():
        for on, vel in q:
            notes.append((on, last, ch, p, vel, 0))
    notes.sort()
    return notes, pedals


def splice(orig: bytes, take: list[tuple], from_s: Any, to_s: Any, title: Optional[str] = None) -> tuple[bytes, dict]:
    """``orig`` (a standard MIDI file) with ``[from_s, to_s)`` replaced by ``take`` ((t, order, status, d1, d2) in any
    seconds) -> (SMF type 0 bytes, report). The report: ``{"replaced": notes taken out, "played": take notes put in,
    "left_out": take notes past the section, "stretch": the take's time factor, "notes": notes in the result,
    "duration_s": its length, "from_s", "to_s"}``. ValueError("bad_section"), ValueError("bad_midi") (not MIDI),
    ValueError("no_notes") (a take with no note)."""
    from_s, to_s = check_section(from_s, to_s)
    try:
        _fmt, ppq, ev = library.parse_smf(library._unwrap(bytes(orig)))
    except Exception as e:                               # noqa: BLE001  any parse failure is the file's
        raise ValueError("bad_midi: the piece's MIDI couldn't be read") from e
    tnotes, tpedals = _take_parts(take)
    if not tnotes:
        raise ValueError("no_notes: nothing was played")
    tm = TempoMap(ppq, ev)
    smpte = ppq < 0
    lo, hi = max(0.0, from_s - SLACK_S), max(0.0, to_s - SLACK_S)
    f_tick = int(math.floor(tm.tick(lo)))
    t_tick = max(f_tick + 1, int(math.floor(tm.tick(hi))))

    out: list[tuple] = []                                # (tick, cls, order, status, d1, d2 | payload)
    end_tick = 0
    held: dict[tuple[int, int], int] = {}                # the file's notes sounding from before the section
    cut: dict[tuple[int, int], int] = {}                 # ... released at its start: their own releases go
    gone: dict[tuple[int, int], int] = {}                # the section's notes: their releases go too
    after: dict[tuple[int, int], int] = {}               # notes struck after the section, sounding
    last_on: dict[tuple[int, int], int] = {}
    pedal = {cc: 0 for cc in PEDALS}                     # the file's pedal values (per cc; channels merged)
    pedal_ch = {cc: 0 for cc in PEDALS}
    section_onsets: list[float] = []
    replaced = 0
    entered = False

    def enter() -> None:
        """The section starts: what the file holds is released, its pedals lifted."""
        nonlocal entered
        entered = True
        for key, n in held.items():
            if n > 0:
                out.append((max(f_tick, last_on.get(key, 0) + 1), _INS, 0, 0x80 | key[0], key[1], 0))
                cut[key] = cut.get(key, 0) + n
        held.clear()
        for cc in PEDALS:
            if pedal[cc] > 0:
                out.append((f_tick, _INS, 0, 0xB0 | pedal_ch[cc], cc, 0))

    for e in ev:
        tick, order, st = e[0], e[1], e[2]
        end_tick = max(end_tick, tick)
        if st == 0xFF:
            if e[3] == 0x2F or (title and e[3] == 0x03) or (smpte and e[3] == 0x51):
                continue
            out.append((tick, _ORIG, order, 0xFF, e[3], e[4]))
            continue
        s = tm.sec(tick)
        where = "pre" if s < lo else ("in" if s < hi else "post")
        if where != "pre" and not entered:
            enter()
        kind, ch = st & 0xF0, st & 0x0F
        key = (ch, e[3])
        if kind == 0x90 and e[4] > 0:
            if where == "in":
                gone[key] = gone.get(key, 0) + 1
                section_onsets.append(s)
                replaced += 1
                continue
            if where == "pre":
                held[key] = held.get(key, 0) + 1
            else:
                after[key] = after.get(key, 0) + 1
            last_on[key] = tick
            out.append((tick, _ORIG, order, st, e[3], e[4]))
        elif kind in (0x80, 0x90):
            if where == "pre":
                if held.get(key, 0) > 0:
                    held[key] -= 1
                out.append((tick, _ORIG, order, st, e[3], e[4]))
            elif after.get(key, 0) > 0:                  # releases a note struck after the section
                after[key] -= 1
                out.append((tick, _ORIG, order, st, e[3], e[4]))
            elif cut.get(key, 0) > 0:
                cut[key] -= 1
            elif gone.get(key, 0) > 0:
                gone[key] -= 1
            elif where == "post":                        # a stray release after the section: as it was
                out.append((tick, _ORIG, order, st, e[3], e[4]))
        elif kind == 0xB0 and e[3] in PEDALS:
            pedal[e[3]], pedal_ch[e[3]] = e[4], ch
            if where != "in":
                out.append((tick, _ORIG, order, st, e[3], e[4]))
        else:                                            # other controllers, programs, bends: as they were
            out.append((tick, _ORIG, order, st, e[3], e[4]) if kind not in (0xC0, 0xD0)
                       else (tick, _ORIG, order, st, e[3], 0))
    if not entered:
        enter()
    # the file's pedals at the section's end (what was in force just before it): taken back there
    pedal_at_end = {cc: 0 for cc in PEDALS}
    for e in ev:
        if e[2] != 0xFF and e[2] & 0xF0 == 0xB0 and e[3] in PEDALS:
            if tm.sec(e[0]) >= hi:
                break
            pedal_at_end[e[3]] = e[4]

    # the take, fitted to the section
    on_times = [n[0] for n in tnotes]
    a_take, b_take = min(on_times), max(on_times)
    if section_onsets:
        a, b = min(section_onsets), max(section_onsets)
    else:
        a = b = from_s
    stretch = 1.0
    if b - a > MIN_SPAN_S and b_take - a_take > MIN_SPAN_S:
        stretch = min(MAX_STRETCH, max(1.0 / MAX_STRETCH, (b - a) / (b_take - a_take)))

    def at(t: float) -> float:
        return tm.tick(a + (t - a_take) * stretch)

    played = left_out = 0
    seq = 0
    for on, off, ch, p, vel, off_vel in tnotes:
        on_t = max(f_tick, int(round(at(on))))
        if on_t >= t_tick:
            left_out += 1
            continue
        off_t = min(t_tick, max(on_t + 1, int(round(at(off)))))
        seq += 1
        out.append((on_t, _ON, seq, 0x90 | ch, p, vel))
        out.append((off_t, _INS, seq, 0x80 | ch, p, off_vel))
        played += 1
    take_pedal: dict[int, Optional[int]] = {cc: None for cc in PEDALS}
    for t, ch, cc, v in sorted(tpedals):
        tick = min(t_tick - 1, max(f_tick, int(round(at(t)))))
        seq += 1
        out.append((tick, _ORIG, 1 << 30 | seq, 0xB0 | ch, cc, v))
        take_pedal[cc] = v
    for cc in PEDALS:
        now = take_pedal[cc] if take_pedal[cc] is not None else 0
        if pedal_at_end[cc] != now:
            out.append((t_tick, _INS, 0, 0xB0 | pedal_ch[cc], cc, pedal_at_end[cc]))

    out.sort(key=lambda x: (x[0], x[1], x[2]))
    events: list[tuple] = []
    if smpte:
        events.append((0, -2, 0xFF, 0x51, (500_000).to_bytes(3, "big")))
    if title:
        events.append((0, -1, 0xFF, 0x03, title.encode("latin-1", "replace")[:255]))
    notes = 0
    for tick, _c, order, st, d1, d2 in out:
        events.append((tick, order, st, d1, d2))
        if st != 0xFF and st & 0xF0 == 0x90 and d2 > 0:
            notes += 1
    last = max((x[0] for x in out), default=0)
    end_tick = max(end_tick, last)
    data = library._smf0(min(0x7FFF, tm.ppq), events, end_tick)
    report = {"replaced": replaced, "played": played, "left_out": left_out, "stretch": round(stretch, 3),
              "notes": notes, "duration_s": round(tm.sec(last), 3), "from_s": from_s, "to_s": to_s}
    return data, report
