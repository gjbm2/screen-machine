"""Score ribbon: the music laid out as notation targets (pure Python, no GPU): the room's bars and its unwritten notes in,
heads, stems, beams, ties, rests, bar lines and signatures out, each with a stable key so the scene can move one object
per note from its first frame to its last.

Source: Original.  Author: smfx.  License: MIT (this file).  license_class: permissive

The rules are the engraved score's own: spelling, accidentals (once per letter and octave in a bar, a natural cancels),
octave lines and the way values split and tie are mei.py's and quantize.py's (mei.spell, mei.signature, mei.ottava,
ScoreBuilder._voice and split_value), so a note drawn here before its bar is written already looks the way the bar
will write it.

Horizontal position is proportional to musical time: x = (slot - q0) * Q sp (a slot is a 16th; q0 the phrase's
first note), plus G sp at every bar line, plus a push where marks would collide (an accidental right after a 16th,
a dot, a second in a chord): pushes accumulate, so a mark never moves anything to its left.

Three regions of a phrase, all on the same x:
  * written   bars the room has written (Bar events, rests, beams, ties): final;
  * metred    bars not written yet, the metre known: the unwritten notes run through _voice exactly as the room's
              next poll would (no rests yet);
  * open      no metre yet: chords on the provisional clock (or free time), each value its legato duration in the
              largest notatable value that fits; no bar lines, beams or ties.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Optional

from stream_service.score import mei
from stream_service.score.quantize import NOTATABLE, ScoreBuilder, split_value

# geometry (staff spaces)
Q16 = 1.65                       # a 16th's width
BAR_G = 1.0                      # added at every bar line
GAP = 0.22                       # the least white between two marks on a staff
BL_SPACE = 1.16                  # a bar line's room: gap, the line, gap
BL_OFF = 0.55                    # the bar line sits this far before its downbeat's leftmost mark
HEAD_W, WHOLE_W = 1.256, 1.62
STEM_W = 0.12
STEM_LEN = 3.5
ACC_GAP = 0.18
DOT_GAP, DOT_W = 0.32, 0.42
OTT_ROOM = 3.0                   # an octave mark's width ('8va' at 0.8: 2.1 sp) and white: a run starting nearer the
                                 # last mark on its staff than this goes on under that mark
FLAG_W = 1.0
T1, T2 = 6.0, 17.5               # the treble and bass staves' top lines, row-local
TOP_D = (38, 26)                 # F5 and A3: each staff's top line as a diatonic step (octave * 7 + letter)
MID_D = (34, 22)                 # B4 and D3: the middle lines
LETTERS = "cdefgab"
ACC_GLYPH = {1: "accidentalSharp", -1: "accidentalFlat", 0: "accidentalNatural", 2: "accidentalDoubleSharp",
             -2: "accidentalDoubleFlat"}
ACC_W = {1: 0.788, -1: 0.792, 0: 0.628, 2: 1.028, -2: 1.552}
HEAD_OF = {1: "noteheadBlack", 2: "noteheadBlack", 3: "noteheadBlack", 4: "noteheadBlack", 6: "noteheadBlack",
           8: "noteheadHalf", 12: "noteheadHalf", 16: "noteheadWhole", 24: "noteheadWhole"}
REST_OF = {1: "rest16th", 2: "rest8th", 3: "rest8th", 4: "restQuarter", 6: "restQuarter", 8: "restHalf",
           12: "restHalf", 16: "restWhole", 24: "restWhole"}
REST_W = {"rest16th": 1.3, "rest8th": 1.1, "restQuarter": 1.22, "restHalf": 1.2, "restWhole": 1.2}
DOTTED = {3, 6, 12, 24}
FLAGS = {1: 2, 2: 1, 3: 1}
TS_W = 1.8                       # a time signature digit's width
KS_STEP = 0.95                   # key signature accidentals' spacing
# key signature positions (diatonic steps) per staff, in signature order
KS_SHARPS = ((38, 35, 39, 36, 33, 37, 34), (24, 21, 25, 22, 19, 23, 20))
KS_FLATS = ((34, 37, 33, 36, 32, 35, 31), (20, 23, 19, 22, 18, 21, 17))


def step_y(d: int, staff: int) -> float:
    """A diatonic step's y (sp, row-local) on a staff."""
    return (T1 if staff == 0 else T2) + (TOP_D[staff] - d) * 0.5


def largest_value(dur: float) -> int:
    v = 1
    for n in NOTATABLE:
        if n <= dur + 1e-6:
            v = n
    return v


@dataclass
class Chord:
    """One chord (or rest) in one staff at one slot: what the layout draws there."""
    staff: int
    slot: float
    dur: int
    heads: list = field(default_factory=list)        # [nid, piece, d, shown alt | None, tie_out, side]
    rest: bool = False
    up: bool = True
    flags: int = 0
    beam: Optional[tuple] = None                    # the beam group's key
    ott: int = 0
    whole_bar: bool = False
    hidden: bool = False                            # a rest not drawn yet (its bar isn't written) but spaced
    bar_lo: float = 0.0                             # a whole-bar rest: its bar's slots
    bar_hi: float = 0.0
    lead: int = 0                                   # index into heads of the stem's head
    acc_cols: list = field(default_factory=list)    # per head: accidental x offset (sp, from the column)
    left: float = 0.0                               # extents from the column's x (sp)
    right: float = HEAD_W


@dataclass
class Out:
    """Targets keyed by stable keys.  x in sp from the phrase's anchor, y row-local sp."""
    heads: dict = field(default_factory=dict)       # key -> (x, y, glyph, staff, nid, piece, acc glyph|None, acc_dx,
                                                    #         dot (dx, dy)|None, ledger staff)
    stems: dict = field(default_factory=dict)       # key -> (head keys, lead key, up, length, flags, beam key|None)
    beams: dict = field(default_factory=dict)       # key -> (stem keys, level, beamlet dir 0|-1|+1)
    ties: dict = field(default_factory=dict)        # key -> (from head, to head, up)
    rests: dict = field(default_factory=dict)       # key -> (x, y, glyph, dot)
    bars: dict = field(default_factory=dict)        # key -> (x, kind)
    sigs: dict = field(default_factory=dict)        # key -> (x, y, glyph)
    otts: dict = field(default_factory=dict)        # key -> (head key, glyph, above)
    end_x: float = 0.0                              # the right edge of the last mark
    end_slot: float = 0.0


class Spacer:
    """Columns in slot order -> x (sp), pushing where marks would collide; resumable (the written bars' state is
    kept and the provisional region continues from it).

    With its layout's ``warp`` (slots, x, tail width: a monotone piecewise-linear map through where the phrase's
    notes are drawn now, refitted when the reading changes) a slot's x is the warp's, and a push is local: it moves
    the colliding column only, so nothing anchored after it moves."""

    def __init__(self, q0: float, units: Optional[int], Q: float = Q16, owner=None) -> None:
        self.q0, self.units, self.Q = q0, units, Q
        self.owner = owner
        self.push = 0.0
        self.edge = [-1e9, -1e9]                    # per staff: the last mark's right edge
        self.edge_slot = [None, None]
        self.last_x = 0.0
        self.first_left: Optional[float] = None
        self.ott = [0, 0]                           # per staff: the last chord's octave line (0 none), ...
        self.ott_m = [0, 0]                         # ... and the last shown mark's, at x
        self.ott_x = [-1e9, -1e9]

    def copy(self) -> "Spacer":
        s = Spacer(self.q0, self.units, self.Q, self.owner)
        s.push, s.edge, s.edge_slot, s.last_x = self.push, list(self.edge), list(self.edge_slot), self.last_x
        s.first_left = self.first_left
        s.ott, s.ott_m, s.ott_x = list(self.ott), list(self.ott_m), list(self.ott_x)
        return s

    def nb(self, p: float) -> int:
        if not self.units:
            return 0
        return int(math.floor((p + 1e-6) / self.units)) - int(math.floor((self.q0 + 1e-6) / self.units))

    def warp(self):
        return None if self.owner is None else self.owner.warp

    def W(self, p: float) -> float:
        """A slot's x without the bar gaps: the warp's, else proportional from the phrase's first note."""
        w = self.warp()
        if w is None:
            return (p - self.q0) * self.Q
        ws, wx, qt = w
        if p >= ws[-1]:
            return wx[-1] + (p - ws[-1]) * qt
        if p <= ws[0]:
            return wx[0] + (p - ws[0]) * qt
        j = bisect.bisect_right(ws, p) - 1
        return wx[j] + (wx[j + 1] - wx[j]) * (p - ws[j]) / (ws[j + 1] - ws[j])

    def base(self, p: float) -> float:
        return self.W(p) + BAR_G * self.nb(p)

    def off(self) -> float:
        """What earlier pushes add to a column: proportional, all of them; warped, none (the warp runs through where
        the notes are drawn, pushes included: adding them again would move the phrase on at every refit)."""
        return self.push if self.warp() is None else 0.0

    def place(self, p: float, ext: dict) -> float:
        """A column at slot p with {staff: (left, right)} -> its x."""
        x = self.base(p) + self.off()
        if self.first_left is None:
            self.first_left = max((lr[0] for lr in ext.values()), default=0.0)
        need = 0.0
        for st, (left, _right) in ext.items():
            prev = self.edge_slot[st]
            room = GAP
            if prev is not None and self.nb(p) > self.nb(prev):
                room = BL_SPACE
            need = max(need, self.edge[st] + room + left - x)
        if need > 0.0:
            if self.warp() is None:
                self.push += need                       # proportional: what follows moves on with it
            x += need                                   # warped: only this column moves
        for st, (_left, right) in ext.items():
            self.edge[st] = max(self.edge[st], x + right)
            self.edge_slot[st] = p
        self.last_x = max(self.last_x, x)
        return x

    def barline(self, k: int, left: float) -> float:
        """Bar k's line (its downbeat column's leftmost mark at ``left`` sp from the column, 0 if none)."""
        x = self.base(k * self.units) + self.off()
        return max(x - left - BL_OFF, max(self.edge) + 0.45)


# -- spelling, chords ------------------------------------------------------------------------------------------------
def spell_chord(ch: Chord, pitches: list, fifths: int, sig: dict, state: dict) -> None:
    """Heads (sorted low to high) with steps, shown accidentals (the bar's state, per staff), seconds, stem."""
    shift = mei.ottava(list(pitches), ch.staff)
    ch.ott = shift
    for h in ch.heads:
        letter, alt, octave = mei.spell(h[0] - 12 * shift, fifths, sig)
        cur = state.get((letter, octave), sig[letter])
        state[(letter, octave)] = alt
        h[2] = octave * 7 + LETTERS.index(letter)
        h[3] = alt if alt != cur else None


def finish_chord(ch: Chord, up: Optional[bool] = None) -> None:
    """Stem direction, seconds on the far side, accidental columns, extents."""
    hs = sorted(ch.heads, key=lambda h: h[2])
    ch.heads = hs
    ds = [h[2] for h in hs]
    mid = MID_D[ch.staff]
    if up is None:
        up = (mid - ds[0]) > (ds[-1] - mid)
    ch.up = up
    for h in hs:
        h[5] = 0
    if up:
        for i in range(1, len(hs)):
            if ds[i] - ds[i - 1] <= 1 and hs[i - 1][5] == 0:
                hs[i][5] = 1
        ch.lead = 0
    else:
        for i in range(len(hs) - 2, -1, -1):
            if ds[i + 1] - ds[i] <= 1 and hs[i + 1][5] == 0:
                hs[i][5] = -1
        ch.lead = len(hs) - 1
    hw = WHOLE_W if ch.dur >= 16 else HEAD_W
    left = hw - 0.06 if any(h[5] < 0 for h in hs) else 0.0
    right = hw + (hw - 0.06 if any(h[5] > 0 for h in hs) else 0.0)
    # accidentals: top down, each in the first column where it clears the others there by 6 steps
    cols: list[list] = []                           # [width, [steps]]
    place = [None] * len(hs)
    for i in range(len(hs) - 1, -1, -1):
        a = hs[i][3]
        if a is None:
            continue
        for ci, c in enumerate(cols):
            if all(abs(hs[i][2] - d) >= 6 for d in c[1]):
                c[1].append(hs[i][2])
                c[0] = max(c[0], ACC_W[a])
                place[i] = ci
                break
        else:
            cols.append([ACC_W[a], [hs[i][2]]])
            place[i] = len(cols) - 1
    offs, x = [], -left - ACC_GAP
    for c in cols:
        offs.append(x)
        x -= c[0] + 0.12
    ch.acc_cols = [None if place[i] is None else offs[place[i]] - ACC_W[hs[i][3]] for i in range(len(hs))]
    if cols:
        left = -x - 0.12
    if ch.dur in DOTTED:
        right += DOT_GAP + DOT_W
    if ch.flags and up and ch.beam is None:
        right = max(right, hw - STEM_W + FLAG_W)
    ch.left, ch.right = left, right


def stem_up_group(chords: list) -> bool:
    """A beam group's direction: away from the note furthest from the middle line."""
    below = max(MID_D[c.staff] - min(h[2] for h in c.heads) for c in chords)
    above = max(max(h[2] for h in c.heads) - MID_D[c.staff] for c in chords)
    return below > above


# -- the phrase's layout -------------------------------------------------------------------------------------------------
class PhraseLayout:
    """One phrase's notation.  ``write(bar)`` lays a written bar out for good (in order); ``provisional(...)``
    lays out what isn't written yet from the state the written bars ended in.  ``targets()`` merges them."""

    def __init__(self, serial: int, Q: float = Q16) -> None:
        self.serial = serial
        self.Q = Q
        self.q0: Optional[float] = None
        self.units: Optional[int] = None
        self.unit = 4
        self.beats = 4
        self.fifths: Optional[int] = None
        self.written = Out()
        self.wspacer: Optional[Spacer] = None
        self.pieces: dict = {}                     # nid -> pieces written so far
        self.wbars = 0                             # bars written
        self.last_bar_k: Optional[int] = None
        self.wend_slot = None
        self.prov = Out()
        self.closed = False
        self.touts: dict = {}                      # (nid, piece) -> it ties on
        self.bars_written: list = []
        self.written_keys: set = set()
        self.last_out: Optional[Out] = None
        self.first_left = 0.0
        self.prov_push = 0.0
        self.warp = None                           # (slots, x, tail width): see Spacer
        self.warp_ver = 0
        self.local_wkey = False                    # reuse a bar's layout while the warp around it is unchanged
        self.slot_of: dict = {}                    # nid -> the 16th its first piece was last laid out at
        self.side_of: dict = {}                    # nid -> its head's offset from its column (sp)
        self.written_slot: dict = {}               # nid -> its written 16th
        self._final = False

    # -- written bars ------------------------------------------------------------------------------------------------
    def reset_written(self) -> None:
        self.bars_written = []
        self.written_slot = {}
        self.touts = {}
        self.written_keys = set()
        self.written = Out()
        self.wspacer = None
        self.pieces = {}
        self.wbars = 0
        self.last_bar_k = None
        self.wend_slot = None

    def write(self, bar, fifths: int, q0: float) -> None:
        if self.wspacer is None:
            self.units, self.unit, self.beats = bar.units, bar.unit, bar.beats
            self.q0 = q0
            self.fifths = fifths
            self.wspacer = Spacer(q0, bar.units, self.Q, self)
        one = Out()
        self.bars_written.append(bar)
        self._bars([(bar.pos, bar.staves)], one, self.wspacer, self.pieces, rests=True, beams=True, final=True,
                   touts=self.touts)
        for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
            getattr(self.written, name).update(getattr(one, name))
            self.written_keys.update(getattr(one, name))
        self.last_out = one
        self.wbars += 1
        self.written.end_x = max(self.wspacer.edge)
        self.last_bar_k = bar.pos
        self.wend_slot = (bar.pos + 1) * bar.units
        return one

    def rewrite_all(self, fifths: int, q0: float) -> "Out":
        """The written bars laid out again (a new 16th width): every written target, together."""
        bars = list(self.bars_written)
        self.reset_written()
        out = Out()
        for bar in bars:
            one = self.write(bar, fifths, q0)
            for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
                getattr(out, name).update(getattr(one, name))
        out.end_x = self.written.end_x
        return out

    def final_bar(self) -> Optional[tuple]:
        """The phrase's closing double bar: (key, x) after its last written bar."""
        if self.wspacer is None or self.last_bar_k is None:
            return None
        return ("l", self.serial, "end"), self.wspacer.barline(self.last_bar_k + 1, 0.0) + 0.3

    # -- the unwritten notes -----------------------------------------------------------------------------------------
    def provisional(self, bars: list, open_chords: Optional[list], fifths: int, q0: float,
                    units: Optional[int], unit: int, beats: int, fresh: bool = False) -> None:
        """``bars``: [(k, staves)] metred provisional bars (ScoreBuilder._voice events); ``open_chords``:
        [(staff, slot, dur, [(pitch, nid)])] when the metre isn't known."""
        out = Out()
        pieces = dict(self.pieces)
        touts = dict(self.touts)
        if self.wspacer is not None:
            sp = self.wspacer.copy()
            fifths = self.fifths
            if fresh and self.warp is not None:                  # (a cut: the bars before it aren't laid out
                sp.edge, sp.edge_slot = [-1e9, -1e9], [None, None]   # again; a warped x doesn't need them)
                sp.ott, sp.ott_m, sp.ott_x = [0, 0], [0, 0], [-1e9, -1e9]
        else:
            self.q0, self.units, self.unit, self.beats = q0, units, unit, beats
            sp = Spacer(q0, units, self.Q, self)
        if bars:
            self._bars(bars, out, sp, pieces, rests=False, beams=True, final=False, fifths=fifths, touts=touts)
        if open_chords:
            self._open(open_chords, out, sp, pieces, fifths)
        out.end_x = max(sp.edge) if max(sp.edge) > -1e8 else 0.0
        self.first_left = sp.first_left or 0.0
        self.prov_push = sp.push
        self.prov_spacer = sp
        self.prov = out
        self.prov_fifths = fifths

    def targets(self) -> Out:
        o = Out()
        for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
            d = dict(getattr(self.written, name))
            d.update(getattr(self.prov, name))
            setattr(o, name, d)
        o.end_x = max(self.written.end_x, self.prov.end_x)
        return o

    # -- laying out events -------------------------------------------------------------------------------------------
    def _bars(self, bars: list, out: Out, sp: Spacer, pieces: dict, *, rests: bool, beams: bool, final: bool,
              fifths: Optional[int] = None, touts: Optional[dict] = None) -> None:
        f = self.fifths if fifths is None else fifths
        f = 0 if f is None else f
        sig = mei.signature(f)
        U = self.unit
        self._final = final
        units = sp.units
        q0 = sp.q0
        memo = None if final else self.__dict__.setdefault("memo", {})
        for k, staves in bars:
            mkey = None
            if memo is not None:
                # a bar laid out before, from the same events and the same state, is the same: reuse it
                ids = sorted({i for st in staves for e in st for i in e.ids})
                mkey = (k, units, U, self._mkw(k, units), f, rests,
                       tuple(tuple((e.start, e.dur, tuple(e.pitches), tuple(e.ids), tuple(e.tie_in), tuple(e.tie_out))
                                   for e in st) for st in staves),
                       (sp.q0, sp.push, tuple(sp.edge), tuple(sp.edge_slot), sp.first_left, tuple(sp.ott),
                        tuple(sp.ott_m), tuple(sp.ott_x)),
                       tuple((i, pieces.get(i, 0), touts.get((i, pieces.get(i, 0) - 1)) if touts is not None else None)
                             for i in ids))
                hit = memo.get(mkey)
                self.memo_n = getattr(self, "memo_n", [0, 0])
                self.memo_n[0 if hit is not None else 1] += 1
                if hit is not None:
                    bar_out, sp_state, p_upd, t_upd = hit
                    for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
                        getattr(out, name).update(getattr(bar_out, name))
                    out.end_x = max(out.end_x, bar_out.end_x)
                    sp.push, sp.edge, sp.edge_slot, sp.last_x, sp.first_left = (sp_state[0], list(sp_state[1]),
                                                                              list(sp_state[2]), sp_state[3],
                                                                              sp_state[4])
                    sp.ott, sp.ott_m, sp.ott_x = list(sp_state[5]), list(sp_state[6]), list(sp_state[7])
                    pieces.update(p_upd)
                    if touts is not None:
                        touts.update(t_upd)
                    for key_ in bar_out.heads:
                        if key_[3] == 0:
                            self.slot_of[key_[2]] = bar_out.heads[key_][10]
                    continue
                p_before = {i: pieces.get(i) for i in ids}
                dest, out_main = Out(), out
                out = dest
            lo = k * units
            cols: dict = {}                                  # slot -> [chords]
            for st in (0, 1):
                state: dict = {}
                evs = staves[st]
                chords = []
                notes_here = any(e.pitches for e in evs)
                for e in evs:
                    p = lo + e.start
                    if not e.pitches:
                        if p + e.dur <= q0 + 1e-6:
                            continue
                        ch = Chord(st, p, e.dur, rest=True, hidden=not rests)
                        if not notes_here and e.start == 0 and e.dur == units:
                            ch.whole_bar, ch.bar_lo, ch.bar_hi = True, lo, lo + units
                        chords.append(ch)
                        continue
                    ch = Chord(st, p, e.dur)
                    for pitch, nid, tin, tout in sorted(zip(e.pitches, e.ids, e.tie_in, e.tie_out)):
                        k_piece = pieces.get(nid, 0)
                        pieces[nid] = k_piece + 1
                        ch.heads.append([pitch, (nid, k_piece, tin), 0, None, tout, 0])
                    ch.flags = FLAGS.get(e.dur, 0)
                    spell_chord(ch, e.pitches, f, sig, state)
                    chords.append(ch)
                # beams: eighths and shorter within a beat, rests break them
                groups, run = [], []
                for ch in chords:
                    beamable = not ch.rest and ch.dur in (1, 2, 3)
                    if beamable and run and int((run[-1].slot - lo) // U) != int((ch.slot - lo) // U):
                        groups.append(run)
                        run = []
                    if beamable:
                        run.append(ch)
                    else:
                        if run:
                            groups.append(run)
                        run = []
                if run:
                    groups.append(run)
                for g in groups:
                    if len(g) >= 2 and beams:
                        key = ("b", self.serial, g[0].heads[0][1][0], g[0].heads[0][1][1])
                        for ch in g:
                            ch.beam = key
                        up = stem_up_group(g)
                        for ch in g:
                            finish_chord(ch, up)
                for ch in chords:
                    if not ch.rest and ch.beam is None:
                        finish_chord(ch)
                    if ch.rest:
                        g = REST_OF[ch.dur]
                        ch.left, ch.right = 0.0, REST_W[g] + (DOT_GAP + DOT_W if ch.dur in DOTTED else 0.0)
                    cols.setdefault(ch.slot, []).append(ch)
                for g in groups:
                    if len(g) >= 2 and beams:
                        self._beam(g, out)
            self._place(cols, out, sp, k, touts)
            if mkey is not None:
                p_upd = {i: pieces[i] for i in ids if pieces.get(i) != p_before[i]}
                t_upd = {kk: v for kk, v in (touts or {}).items() if kk[0] in p_before}
                if len(memo) > 256:
                    memo.clear()
                memo[mkey] = (dest, (sp.push, tuple(sp.edge), tuple(sp.edge_slot), sp.last_x, sp.first_left,
                                     tuple(sp.ott), tuple(sp.ott_m), tuple(sp.ott_x)), p_upd, t_upd)
                for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
                    getattr(out_main, name).update(getattr(dest, name))
                out_main.end_x = max(out_main.end_x, dest.end_x)
                out = out_main
        out.end_slot = max(out.end_slot, (bars[-1][0] + 1) * units if bars else 0)

    def _mkw(self, k: int, units) -> tuple:
        """The warp as bar k's layout sees it: the whole warp's version, or (local_wkey: a warp that only ever gains
        points as notes arrive, span: room) its points around the bar, and its tail width only where the bar
        reaches past them."""
        w = self.warp
        if not self.local_wkey or w is None or not units:
            return (self.Q, self.warp_ver)
        ws, wx, qt = w
        lo, hi = k * units, (k + 1) * units                     # (and the point either side: what W reads there)
        i0 = max(0, bisect.bisect_left(ws, lo) - 1)
        i1 = min(len(ws), bisect.bisect_right(ws, hi) + 1)
        key = (tuple(ws[i0:i1]), tuple(wx[i0:i1]))
        if hi >= ws[-1] or lo <= ws[0]:
            key += (qt,)
        return key

    def _open(self, chords_in: list, out: Out, sp: Spacer, pieces: dict, fifths: int) -> None:
        sig = mei.signature(fifths)
        cols: dict = {}
        states = ({}, {})
        state_bar = [None, None]
        for st, slot, dur, notes in sorted(chords_in, key=lambda c: (c[1], c[0])):
            pb = int(slot // 16)
            if state_bar[st] != pb:
                states[st].clear()
                state_bar[st] = pb
            ch = Chord(st, slot, largest_value(dur))
            for pitch, nid in sorted(notes):
                k_piece = pieces.get(nid, 0)
                pieces[nid] = k_piece + 1
                ch.heads.append([pitch, (nid, k_piece, False), 0, None, False, 0])
            ch.flags = FLAGS.get(ch.dur, 0)
            spell_chord(ch, [p for p, _ in notes], fifths, sig, states[st])
            finish_chord(ch)
            cols.setdefault(slot, []).append(ch)
        self._final = False
        self._place(cols, out, sp, None, None)

    def _beam(self, g: list, out: Out) -> None:
        key = g[0].beam
        stems = [("s", self.serial) + g_ch_key(c) for c in g]
        out.beams[key + (0,)] = (stems, 0, 0)
        # the 16th level: runs of sixteenths beamed together, a lone one gets a beamlet
        i = 0
        n = len(g)
        while i < n:
            if g[i].dur != 1:
                i += 1
                continue
            j = i
            while j + 1 < n and g[j + 1].dur == 1:
                j += 1
            if j > i:
                out.beams[key + (1, i)] = (stems[i:j + 1], 1, 0)
            else:
                d = 1 if i == 0 else (-1 if i == n - 1 or g[i - 1].dur == 3 else 1)
                out.beams[key + (1, i)] = ([stems[i]], 1, d)
            i = j + 1

    def _place(self, cols: dict, out: Out, sp: Spacer, k: Optional[int], touts: Optional[dict]) -> None:
        """Columns in slot order: x, then every mark's target; bar k's line at its start."""
        Q = self.Q
        units = sp.units
        lo = k * units if (k is not None and units) else None
        key = ("l", self.serial, k)
        need_bl = lo is not None and lo > sp.q0 + 1e-6
        for slot in sorted(cols):
            chs = cols[slot]
            ext: dict = {}
            for ch in chs:
                l0, r0 = ext.get(ch.staff, (0.0, 0.0))
                ext[ch.staff] = (max(l0, ch.left), max(r0, ch.right))
            if need_bl and abs(slot - lo) < 1e-6:
                x = sp.place(slot, ext)
                out.bars[key] = (x - max(ch.left for ch in chs) - BL_OFF, 0)
                need_bl = False
            else:
                if need_bl:
                    out.bars[key] = (sp.barline(k, 0.0), 0)
                    need_bl = False
                x = sp.place(slot, ext)
            for ch in chs:
                self._marks(ch, x, out, Q, touts, sp)
        if need_bl:
            out.bars[key] = (sp.barline(k, 0.0), 0)
        out.end_x = max(out.end_x, max(sp.edge))

    def _marks(self, ch: Chord, x: float, out: Out, Q: float, touts: Optional[dict] = None,
               sp: Optional[Spacer] = None) -> None:
        s = self.serial
        st = ch.staff
        top = T1 if st == 0 else T2
        if ch.rest:
            if ch.hidden:
                return
            g = REST_OF[ch.dur]
            if ch.whole_bar:
                xm = 0.5 * (x + x + (ch.bar_hi - ch.bar_lo) * Q) - 0.6
                out.rests[("r", s, ch.slot, st)] = (xm, top + 1.0, "restWhole", False)
                return
            y = top + (1.0 if g == "restWhole" else 2.0)
            out.rests[("r", s, ch.slot, st)] = (x, y, g, ch.dur in DOTTED)
            return
        hw = WHOLE_W if ch.dur >= 16 else HEAD_W
        glyph = HEAD_OF[ch.dur]
        keys = []
        right = max(h[5] for h in ch.heads)
        for i, h in enumerate(ch.heads):
            nid, piece, tin = h[1]
            key = ("h", s, nid, piece)
            keys.append(key)
            hx = x + h[5] * (hw - 0.06)
            y = step_y(h[2], st)
            acc = None if h[3] is None else ACC_GLYPH[h[3]]
            acc_dx = ch.acc_cols[i] if h[3] is not None else 0.0
            dot = None
            if ch.dur in DOTTED:
                on_line = (TOP_D[st] - h[2]) % 2 == 0
                dot_col = x + hw + right * (hw - 0.06) + DOT_GAP
                dot = (dot_col - hx, -0.5 if on_line else 0.0)
            out.heads[key] = (hx, y, glyph, st, nid, piece, acc, acc_dx - h[5] * (hw - 0.06), dot, st, ch.slot)
            if piece == 0:
                self.slot_of[nid] = ch.slot
                self.side_of[nid] = hx - x                 # a head beside the stem (a second): off its column
                if self._final:
                    self.written_slot[nid] = ch.slot
            if tin and piece > 0 and (touts is None or touts.get((nid, piece - 1))):
                up_tie = self._tie_up(ch, i)
                out.ties[("t", s, nid, piece)] = (("h", s, nid, piece - 1), key, up_tie)
            if touts is not None:
                touts[(nid, piece)] = bool(h[4])
        if ch.dur < 16:
            ys = [step_y(h[2], st) for h in ch.heads]
            ymid = top + 2.0
            if ch.up:
                L = max(STEM_LEN, min(ys) - ymid + 0.0)
            else:
                L = max(STEM_LEN, ymid - max(ys))
            if ch.flags == 2 and ch.beam is None:
                L += 0.4
            out.stems[("s", s) + g_ch_key(ch)] = (keys, keys[ch.lead], ch.up, L, ch.flags if ch.beam is None else 0,
                                                  ch.beam)
        # one octave mark a run of chords under the same line (rests don't break it), never two overlapping
        show = bool(ch.ott)
        if sp is not None:
            if ch.ott and (sp.ott[st] == ch.ott or (sp.ott_m[st] == ch.ott and x - sp.ott_x[st] < OTT_ROOM)):
                show = False
            sp.ott[st] = ch.ott
            if show:
                sp.ott_m[st], sp.ott_x[st] = ch.ott, x
        if show:
            name = {1: "ottavaAlta", 2: "quindicesima", -1: "ottavaBassaVb", -2: "quindicesima"}[ch.ott]
            out.otts[("o", s) + g_ch_key(ch)] = (keys[-1] if ch.ott > 0 else keys[0], name, ch.ott > 0)

    @staticmethod
    def _tie_up(ch: Chord, i: int) -> bool:
        n = len(ch.heads)
        if n == 1:
            return not ch.up
        if i < n // 2:
            return False
        if i >= (n + 1) // 2:
            return True
        return not ch.up


def g_ch_key(ch: Chord) -> tuple:
    """A chord's key: its lowest head's note and piece (stable while the chord keeps it)."""
    nid, piece, _tin = ch.heads[0][1]
    return (nid, piece)


# -- the unwritten notes, as the room's next poll would write them -------------------------------------------------------
def metred_bars(b: ScoreBuilder, notes: list, staffs: list, k0: int, now: float, tl=None) -> list:
    """[(k, staves)] for bars k0.. up to the last one any of ``notes`` reaches (ScoreBuilder._build without its
    side effects: the staves are given, nothing is forgotten).  A note still held grows only as far as its first
    piece (where its value would split and tie): its tied pieces come once, when it is released, instead of growing
    and shrinking with every late release."""
    tl = tl or b.timeline
    B, U = (b.beats, b.unit) if b.grid is not None else (4, 4)
    bar_u = B * U
    se = []
    k_last = k0 - 1
    for n, st in zip(notes, staffs):
        s = b._q(tl.beat(n.t_on))
        if n.t_off is not None:
            e = b._q(tl.beat(n.t_off))
        else:
            e = b._q(tl.beat(now))
            first = split_value(s % bar_u, 4 * bar_u, B, U)[0]
            e = min(e, s + first)
        e = max(e, s + 1)
        se.append((s, e, n, st))
        if e > k0 * bar_u:
            k_last = max(k_last, (e - 1) // bar_u)
    out = []
    for k in range(k0, k_last + 1):
        lo, hi = k * bar_u, (k + 1) * bar_u
        segs: tuple = ([], [])
        after: list = [None, None]
        for s, e, n, st in se:
            if s >= hi:
                if s < hi + U and (after[st] is None or s - lo < after[st]):
                    after[st] = s - lo
                continue
            if e <= lo:
                continue
            segs[st].append((max(s, lo) - lo, min(e, hi) - lo, n.pitch, n.id, s < lo, e > hi))
        if not segs[0] and not segs[1]:
            continue
        out.append((k, tuple(ScoreBuilder._voice(sorted(sg), B, U, after[i]) for i, sg in enumerate(segs))))
    return out


def open_chords(notes: list, staffs: list, slots: list, ends: list, unit: int = 4) -> list:
    """[(staff, slot, dur, [(pitch, nid)])]: chords per staff, each lasting to the next onset in its staff when the gap
    is short (as _voice joins them), else to its own end."""
    out = []
    for st in (0, 1):
        items = sorted(((s, e, n) for s, e, n, t in zip(slots, ends, notes, staffs) if t == st),
                       key=lambda it: (it[0], it[2].pitch, it[2].id))
        chords: list = []
        for s, e, n in items:
            if chords and abs(s - chords[-1][0]) < 0.5:
                chords[-1][2].append((n.pitch, n.id))
                chords[-1][1] = max(chords[-1][1], e)
            else:
                chords.append([s, e, [(n.pitch, n.id)]])
        for i, (s, e, ns) in enumerate(chords):
            nxt = chords[i + 1][0] if i + 1 < len(chords) else None
            end = e if nxt is None else min(e, nxt)
            if nxt is not None and end < nxt and (nxt - s <= unit or nxt - end < unit / 2):
                end = nxt
            out.append((st, s, max(1.0, end - s), ns))
    return out


def signature_marks(serial: int, x_first: float, first_left: float, fifths: int, show_key: bool,
                    meter: Optional[tuple]) -> dict:
    """The phrase's opening: key signature (if it changed) and time signature, right-aligned before its first note."""
    out = {}
    x = x_first - first_left - 0.9
    if meter is not None:
        num, den = meter
        digits_n, digits_d = str(num), str(den)
        w = TS_W * max(len(digits_n), len(digits_d))
        x0 = x - w
        for st, top in ((0, T1), (1, T2)):
            for row, digits, dy in ((0, digits_n, 1.0), (1, digits_d, 3.0)):
                wd = TS_W * len(digits)
                for i, ch in enumerate(digits):
                    out[("ts", serial, st, row, i)] = (x0 + (w - wd) / 2 + i * TS_W, top + dy, f"timeSig{ch}")
        x = x0 - 0.6
    if show_key and fifths:
        n = abs(fifths)
        pos = KS_SHARPS if fifths > 0 else KS_FLATS
        g = "accidentalSharp" if fifths > 0 else "accidentalFlat"
        x0 = x - n * KS_STEP
        for st in (0, 1):
            for i in range(n):
                out[("ks", serial, st, i)] = (x0 + i * KS_STEP, step_y(pos[st][i], st), g)
    return out
