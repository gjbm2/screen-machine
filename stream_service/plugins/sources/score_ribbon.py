"""Score ribbon: the piano's playing as a live score that draws every mark itself, one object per note from the frame
it arrives to the frame it scrolls away.

Source: Original.  The notation rules are the room's own (stream_service/score: quantize.py's values and ties,
        mei.py's spelling, accidentals, octave lines and beaming); the glyphs are the Leipzig music font as Verovio
        ships it (SIL OFL 1.1), read at setup into a signed-distance atlas (ribbon_glyphs.py).  No engraver runs.
Author: smfx
License: MIT (this file)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b) through the room's one transcription
(score/room.py's RemoteRoom: the room process's ScoreBuilder and PM2S analysis; the scene reads its bars and a replica
of its state).

On screen: two grand-staff systems, one tape folded in two (``rows``), moving right to left at the music's pace with
a soft playhead about 83 % across the lower system.  Read it like a page: the upper system, then the lower up to the
playhead; music leaving the lower system's left edge comes back in at the upper system's right.  Each system keeps a
header (brace, clefs, key and time in force there).  Horizontal position is proportional to musical time: a phrase's
16th is as wide as keeps the paper moving at ``pace`` staff spaces a second (within what heads need and what keeps
enough music on screen), a bar line adds one, marks that would collide push what follows.  So where a note is born
is where it stays: a note's x is its 16th on the best beat grid the room has, its raw time before there is one, and
when a better clock takes over (the room's beat clock, the analysed grid, a rewrite) the width is refitted so the
notes drawn so far move least.

One process for a note's whole life (owner, 21 Oct): on the first frame after it arrives a note is notation (head,
stem, ledger lines, accidental) at its place, popping in with a halo in its colour (the palette around the circle of
fifths) while it sounds.  As certainty arrives it grows and moves, never replaced: the head opens or closes and gains
a dot as it is held and released, flags as its value is known, beams join it to its beat once the beat grid is
known, a tied piece grows out of it when its value crosses a beat or a bar line, it slides to a new 16th when two
successive estimates agree (the analysis replies about every second), hops staves when the hands are known, and
settles to the room's written bar (about 3 s after playing) with a small arc if it moves.  Rests appear as bars are
written, bar lines drop in like gates when the metre lands, the time signature flips in at the phrase's start and
the key signature pops in.  A rewritten phrase (the metre turned) re-slides into its new bars; nothing vanishes.

Macros: energy -> the notes' halo and how long they keep their colour, and the playhead's glow; pulse (bind note_on)
-> a halo kick; speed -> the page's breath; intensity -> output gain; palette / period / drift colour the sounding
notes.  The heavy lifting is on the GPU (ribbon_stage.py): the host lays notes out only when the music changes and the
marks animate in closed form on the GPU each frame (one CUDA graph a frame).  Looks: paper (dark ink on
warm paper) and night (gold on blue-black).  Degrade, never refuse: no room process -> the staves breathe and wait;
no analysis -> the room's beat clock in 4/4; no clock yet -> free time at the last phrase's pace.
"""
from __future__ import annotations

import logging
import math
import os
import pathlib
import sys
import time

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

_HERE = pathlib.Path(__file__).resolve().parent
if (_HERE / "ribbon_layout.py").exists():          # scratch and tests: the helpers next to this file
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    import ribbon_glyphs as RG  # noqa: E402
    import ribbon_kernel as RK  # noqa: E402
    import ribbon_layout as RL  # noqa: E402
    import ribbon_paint as RP  # noqa: E402
    import ribbon_rec as RR  # noqa: E402
    import ribbon_stage as RS  # noqa: E402
else:                                              # deployed: the helpers in stream_service/score
    from stream_service.score import (ribbon_glyphs as RG, ribbon_kernel as RK, ribbon_layout as RL,
                                      ribbon_paint as RP, ribbon_rec as RR, ribbon_stage as RS)

log = logging.getLogger("stream_service.plugins.score_ribbon")

LOOKS = ("paper", "night")
ROOM_KEY = "room"
X_PLAY = 0.83                      # the playhead, of the width
LAG0, LAG_A = 0.35, 0.12           # the notes' arrival lag (s): its seed, and how fast the estimate follows
P_SEED = 0.6                       # free time's beat before any phrase has had one (s)
S_OMEGA, S_AMAX_SP = 1.6, 60.0     # the paper: spring (rad/s) with tempo feed-forward, acceleration cap (sp/s^2)
V_IDLE_SP = 0.25                   # the paper's creep with nothing playing (sp/s)
AHEAD_BEATS = 2.0                  # with nothing sounding the paper runs on at most this far past the last sound
PHRASE_GAP_SP = 6.0                # between a phrase's last mark and the next one's first (plus its signature)
FRESH_S, REL_TAU = 0.6, 0.6        # a note's halo fades over this; its colour cools after release with this tau
Q_MIN, Q_MAX = 1.35, 4.0           # a 16th's width (sp): no tighter than heads allow, no wider than shows enough music
WARP_MIN = 0.6                     # a re-read may squeeze drawn notes to this many sp a 16th (pushes keep them apart)
WARP_TAIL = 12.0                   # new notes go on at the slope of the last this many 16ths drawn


class _Note:
    __slots__ = ("nid", "pitch", "vel", "t_on", "t_off", "born", "phrase")

    def __init__(self, nid, pitch, vel, t_on, t_off, born, phrase):
        self.nid, self.pitch, self.vel, self.t_on, self.t_off, self.born, self.phrase = (nid, pitch, vel, t_on, t_off,
                                                                                         born, phrase)


class _Phrase:
    def __init__(self, serial: int, A: float, Q: float) -> None:
        self.serial = serial
        self.A = A                                  # tape px of the phrase's first note
        self.pl = RL.PhraseLayout(serial, Q)
        self.notes: dict = {}                       # nid -> _Note
        self.t_first = None
        self.ref = None                             # the first note's id
        self.closed = False
        self.prov_owned: set = set()
        self.mapkey = None
        self.mapver = 0
        self.Q = None                               # a 16th's width (sp), set as each new clock takes over
        self.warp_dirty = True                      # the reading changed: refit the warp before the next layout
        self.q_mode = None
        self.fifths = None                          # frozen at the first written bar
        self.meter = None
        self.slot_s = P_SEED / 4.0                  # seconds a 16th (free time before the clock starts)
        self.t_last = None                          # the last sound (onset, or release) in the phrase
        self.end_x = 0.0                            # sp: the right edge of its last mark


class ScoreRibbon(Source):
    name = "score_ribbon"
    label = "Score ribbon"
    description = ("The piano's playing as a live score on a moving ribbon of two systems, every note notation from "
                   "the frame it sounds, growing and settling into place.")
    listens = ("notes",)
    ROOM_LOCAL = False                              # tests: an in-process room fed from this scene's own sound

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=1.0, period=1.0, drift=0.01),
            "look": enum(LOOKS, "paper", "Look", tier="structural", basic=True),
            "rows": num(1, 3, 2, "Systems", tier="structural", integer=True),
            "staff_size": num(0.7, 1.4, 1.0, "Staff size", tier="structural"),
            "pace": num(6.0, 20.0, 12.0, "Pace", unit="sp/s", tier="structural"),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "flourish": num(0.0, 1.0, 0.7, "Flourish"),
            "playhead": num(0.0, 1.0, 0.35, "Playhead glow"),
        }

    # -- setup ------------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.rs = float(params["render_scale"])
        self.w, self.h = internal_size(ctx, self.rs)
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        self.night = params.get("look") == "night"
        self.R = int(params.get("rows", 2))
        size = float(params["staff_size"])
        self.pace = float(params.get("pace", 12.0))
        self.Q = RL.Q16
        R, w, h = self.R, self.w, self.h
        # the systems: as large as the screen allows up to the default staff space
        sp = 0.013 * h * size
        fit = 0.92 * h / (R * 25.0 - 1.0)
        self.sp = sp = round(min(sp, fit), 2)
        self.pitch_px = 8 * round(25 * sp / 8)
        self.rowh = 24 * sp
        self.y0 = 8 * round((h - (R * self.pitch_px - sp)) / 16)
        self.XM = round(0.016 * w)
        self.XL = self.XM + 13.0 * sp
        self.XL_t = self.XL
        self.WV = w - self.XL - self.XM
        self.painter = RP.Painter(ctx, w, h, self.night, int(params["seed"]), raw_module, launch_2d, HELPERS)
        self.stage = RS.Stage(sp, float(params.get("flourish", 0.7)))
        self.stage.gpu_setup(cp, raw_module, HELPERS, R, self.painter.NC, launch_2d)
        self.Ph = np.zeros(RK.NP, np.float32)
        # the room's one transcription
        self.room = None
        try:
            if self.ROOM_LOCAL:
                from stream_service.score.room import RoomScore
                self.room = RoomScore.join(ROOM_KEY + "-local-%d" % id(self), self,
                                           analysis=bool(os.environ.get("RIBBON_ANALYSIS")))
                self.builder = self.room.builder
            else:
                from stream_service.score.room import RemoteRoom
                self.room = RemoteRoom.join(ROOM_KEY, self)
                self.builder = self.room.builder
        except Exception as e:                                   # degrade: staves, no notes
            log.warning("score_ribbon: no room transcription (%s)", e)
            self.builder = None
        self.clock = 0.0
        self.t_sound = 0.0
        self.t_base = None
        self.breath = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.lag = LAG0
        self.S = None                                            # the paper (tape px at the lower row's left)
        self.V = 0.0
        self.U = None                                            # the playhead's tape target
        self.v_ff = 0.0
        self.phrases: list[_Phrase] = []
        self.ph: _Phrase | None = None                          # the open phrase
        self.serial = 0
        self.notes: dict = {}                                    # nid -> _Note (the open phrase's and recent)
        self.dirty = False
        self.dirty_now = False
        self.last_pass = -1.0
        self.regrid = False
        self.last_rev = 0
        self.last_pass_slot = None
        self.key_cache = (None, 0)
        self.hdr = [dict(fifths=0, meter=None, t_key=-9.0, t_meter=-9.0) for _ in range(R)]
        self.stats = {"births": 0, "passes": 0, "bars": 0, "rewrites": 0, "phrases": 0}
        self.events: list = []                                   # (scene clock, kind, data) for the harness
        self.record_events = False
        self._batch = None
        self._refit = None
        self._snap = False                                       # a join: put the paper at 'now' once laid out
        self._joining = False
        self._join_outs: list = []

    # -- the room ---------------------------------------------------------------------------------------------------
    def _drain(self, frame, t: float) -> None:
        room = self.room
        if room is None:
            return
        inbox = getattr(room, "_inbox", None)
        if inbox is not None:                                    # RemoteRoom: message by message, bars then state
            with room._lock:
                msgs = list(inbox)
                inbox.clear()
            # the first content after setup may be a phrase well under way (the room hands a screen that joins
            # mid-phrase its bars so far): its notes aren't fresh, and the paper starts at 'now', not at its start
            self._joining = self.S is None and any(m.get("bars") for m in msgs)
            for m in msgs:
                self._rx = m.get("_rx")                  # (the harness stamps arrivals; production messages don't)
                bars_m = list(m.get("bars") or ())
                for j, bar in enumerate(bars_m):
                    self._batch = bars_m[j:]
                    self._on_bar(bar)
                if m.get("view") is not None:
                    room.builder.apply(m["view"])
                    self._on_view()
                room.rev = int(m.get("rev", room.rev))
                self._do_refit()
            rev = room.rev
        else:                                                    # an in-process room (tests)
            bars, rev = room.tick(self, frame, t)
            for j, bar in enumerate(bars):
                self._batch = bars[j:]
                self._on_bar(bar)
            self._on_view()
            self._do_refit()
        if rev != self.last_rev:
            self.last_rev = rev
            self.dirty = True

    def _new_phrase(self, t_on: float | None) -> _Phrase:
        sp = self.sp
        prev = self.phrases[-1] if self.phrases else None
        if self.S is None:
            A = 0.0
            self.S = A - (X_PLAY * self.w - self.XL)
            self._snap = True                    # nothing drawn yet: the paper goes straight to 'now' after layout
        else:
            v = self.pace * sp
            A = self.S + (X_PLAY * self.w - self.XL)
            if t_on is not None:
                A += (t_on - (self.t_sound - self.lag)) * v
        if prev is not None:
            A = max(A, prev.A + (prev.end_x + PHRASE_GAP_SP) * sp)
        self.serial += 1
        ph = _Phrase(self.serial, A, self.Q)
        if prev is not None:
            ph.slot_s = prev.slot_s
        self.phrases.append(ph)
        self.ph = ph
        self.stats["phrases"] += 1
        self._event("phrase", {"serial": ph.serial, "A": A})
        return ph

    def _on_view(self) -> None:
        b = self.builder
        ph = self.ph
        t = self.t_sound
        changed = False
        for n in b.notes:
            m = self.notes.get(n.id)
            if m is None:
                if ph is None or ph.closed:
                    ph = self._new_phrase(n.t_on)
                m = _Note(n.id, n.pitch, n.vel, n.t_on, n.t_off, self.clock, ph)
                self.notes[n.id] = m
                ph.notes[n.id] = m
                if ph.t_first is None or n.t_on < ph.t_first:
                    ph.t_first = n.t_on
                    if ph.ref is None or ph.pl.wbars == 0:          # (a phrase joined mid-way keeps its bars'
                        ph.ref = n.id                                # first note)
                lagv = t - n.t_on
                if 0.0 <= lagv < 2.0 and not self._joining:         # notes from before a join aren't lag
                    self.lag += (lagv - self.lag) * LAG_A
                self.stats["births"] += 1
                self.dirty_now = True
                self._event("birth", {"nid": n.id, "pitch": n.pitch, "t_on": n.t_on, "lag": lagv,
                                      "rx": getattr(self, "_rx", None), "wall": getattr(self, "_wall", None)})
                changed = True
                ph.t_last = max(ph.t_last or n.t_on, n.t_on)
            elif m.t_off != n.t_off:
                m.t_off = n.t_off
                if n.t_off is not None and m.phrase is not None:
                    m.phrase.t_last = max(m.phrase.t_last or n.t_off, n.t_off)
                changed = True
        if ph is not None and not ph.closed:
            key = self._mapkey()
            if key != ph.mapkey:
                mode = key[0]
                if ph.mapkey is not None and mode != ph.mapkey[0]:
                    self.regrid = True
                ph.mapkey = key
                ph.mapver += 1
                ph.warp_dirty = True
                changed = True
        if changed:
            self.dirty = True

    def _mapkey(self) -> tuple:
        b = self.builder
        if b.grid is not None:
            return ("grid", len(b.grid.t), b.grid.idx0, b.grid.t[-1], self.last_rev, b.beats, b.unit)
        if b.clock.running:
            return ("clock", b.clock.P, b.clock.t0, b.clock.b0)
        return ("free",)

    def _on_bar(self, bar) -> None:
        ph = self.ph
        if bar.marker:
            if bar.rewrite:
                if ph is not None and not ph.closed:
                    pl = ph.pl
                    ph.prov_owned |= pl.written_keys            # what the new bars don't write again goes
                    pl.reset_written()
                    self._join_outs = [(p_, o) for p_, o in self._join_outs if p_ is not ph]   # (not yet shown)
                    self.regrid = True
                    self.dirty = True
                    self.stats["rewrites"] += 1
                    self._event("rewrite", {"serial": ph.serial})
                return
            if ph is not None and not ph.closed:
                self._close(ph)
            return
        if ph is None or ph.closed:
            ph = self._new_phrase(None)
        pl = ph.pl
        for st in bar.staves:                                   # notes the scene never saw (joined mid-phrase)
            for e in st:
                for p, i in zip(e.pitches, e.ids):
                    if i not in self.notes:
                        m = _Note(i, p, 0.6, None, None, self.clock, ph)
                        self.notes[i] = m
                        ph.notes[i] = m
                        if ph.ref is None:
                            ph.ref = i
        if pl.wbars == 0:
            q0 = self._q0_written(ph, bar)
            # the first bar of a phrase (or of its rewrite) can come with the metre, before the grid reaches this
            # scene: a 16th's width that keeps the bar's furthest note where it is drawn now, else the bar's own pace
            pairs, seen = [], set()
            for bb in getattr(self, "_batch", None) or [bar]:  # this bar and the rest of its batch (a rewrite
                if bb.marker:                                  # sends the phrase's bars at once): the furthest
                    break                                      # note there is the one kept in place
                for st_ in bb.staves:
                    for e in st_:
                        for i_, tin in zip(e.ids, e.tie_in):
                            if not tin and i_ not in seen:
                                seen.add(i_)
                                pairs.append((i_, bb.pos * bb.units + e.start))
            d_new = (bar.t_end - bar.t_start) / max(1, bar.units)
            mode = "grid" if self.builder.analysed else "clock"
            self._set_q(ph, (mode, bar.units, bar.unit), pairs, q0, bar.units, d_new)
            self._refit = ph
            if ph.fifths is None:
                ph.fifths = self._fifths()
            ph.meter = bar.meter
        ov = {}
        for bb in getattr(self, "_batch", None) or [bar]:        # this bar's notes at their written 16ths
            if bb.marker:
                break
            for st_ in bb.staves:
                for e in st_:
                    for i_, tin in zip(e.ids, e.tie_in):
                        if not tin and i_ not in ov:
                            ov[i_] = bb.pos * bb.units + e.start
        d_bar = (bar.t_end - bar.t_start) / max(1, bar.units)
        self._fit_warp(ph, ov, q0 if pl.wbars == 0 else pl.q0, bar.units, d_bar if d_bar > 0 else None)
        out = pl.write(bar, ph.fifths, q0 if pl.wbars == 0 else pl.q0)
        self.stats["bars"] += 1
        self._event("bar", {"serial": ph.serial, "pos": bar.pos, "t_start": bar.t_start, "t_end": bar.t_end,
                            "units": bar.units})
        if self._joining:                          # a join: laid out now, put on stage once the paper is placed
            self._join_outs.append((ph, out))
        else:
            self._note_info_sync(ph, out, final=True)
        ph.end_x = max(ph.end_x, pl.written.end_x)
        self.dirty = True

    def _flush_join(self) -> None:
        """A join's written bars on stage in one go, only what the paper shows (the rest would be culled at once):
        a phrase a minute long joined mid-way costs one sync, not one a bar."""
        outs, self._join_outs = self._join_outs, []
        sp = self.sp
        for ph in {id(p_): p_ for p_, _o in outs}.values():
            merged = RL.Out()
            for p_, o in outs:
                if p_ is ph:
                    for name in ("heads", "stems", "beams", "ties", "rests", "bars", "sigs", "otts"):
                        getattr(merged, name).update(getattr(o, name))
            x_min = ((self.S if self.S is not None else 0.0) - (self.R - 1) * self.WV - 8 * sp - ph.A) / sp
            for name in ("heads", "rests", "bars", "sigs"):
                d = getattr(merged, name)
                setattr(merged, name, {k: v for k, v in d.items() if v[0] >= x_min})
            self._note_info_sync(ph, merged, final=True)

    def _q0_written(self, ph: _Phrase, bar) -> float:
        first = None
        ref_slot = None
        for st in bar.staves:
            for e in st:
                if not e.pitches:
                    continue
                p = bar.pos * bar.units + e.start
                first = p if first is None else min(first, p)
                if ph.ref in e.ids and ref_slot is None:
                    ref_slot = p
        if ref_slot is not None:
            return float(ref_slot)
        if ph.pl.q0 is not None:
            return float(ph.pl.q0)
        return float(first if first is not None else bar.pos * bar.units)

    def _close(self, ph: _Phrase) -> None:
        ph.closed = True
        fb = ph.pl.final_bar()
        if fb is not None:
            key, x = fb
            out = RL.Out()
            out.bars[key] = (x, 1)
            self.stage.sync(ph.serial, out, ph.A, self.t_sound, final=True, mapver=ph.mapver, regrid=False,
                            owned=set(), note_info={})
            ph.end_x = max(ph.end_x, x + 1.0)
        if self.ph is ph:
            self.ph = None
        self._event("phrase_end", {"serial": ph.serial})

    def _fifths(self) -> int:
        b = self.builder
        k = (b.ver, b.key_fifths)
        if self.key_cache[0] != k:
            self.key_cache = (k, b.key()[2])
        return self.key_cache[1]

    # -- the open phrase, laid out ----------------------------------------------------------------------------------
    def _slot_fn(self, ph: _Phrase):
        """(mode, snapped slot of a time, continuous slot of a time, unit) for the phrase's current mapping."""
        b = self.builder
        if b.grid is not None:
            g, u = b.grid, b.unit
            T = g.t
            ibis = sorted(y - x for x, y in zip(T[-9:], T[-8:]) if y > x)
            ibi = ibis[len(ibis) // 2] if ibis else (T[-1] - T[-2])
            last = len(T) - 1 - g.idx0

            def beat(t):                                   # past the newest analysed beat: the recent tempo, not
                return g.beat(t) if t <= T[-1] else last + (t - T[-1]) / ibi    # its last (weakest) interval
            return "grid", (lambda t: b._q(beat(t))), (lambda t: beat(t) * u), u
        if b.clock.running:
            c = b.clock
            return "clock", (lambda t: b._q(c.beat(t))), (lambda t: c.beat(t) * 4.0), 4
        t0, d = ph.t_first if ph.t_first is not None else self.t_sound, ph.slot_s

        def free(t):
            return (t - t0) / d
        return "free", free, free, 4

    def _do_refit(self) -> None:
        """After a phrase's first written bars (its metre, or a rewrite) and the state they came with: the width that
        moves all its notes least, written and not yet written together; the bars laid out again if it changed (the
        same frame: their notes simply aim elsewhere)."""
        ph, self._refit = getattr(self, "_refit", None), None
        b = self.builder
        if ph is None or ph.closed or ph is not self.ph or b is None or not ph.pl.bars_written:
            return
        pl = ph.pl
        pairs, seen = [], set()
        for bar in pl.bars_written:
            for st_ in bar.staves:
                for e in st_:
                    for i_, tin in zip(e.ids, e.tie_in):
                        if not tin and i_ not in seen:
                            seen.add(i_)
                            pairs.append((i_, bar.pos * bar.units + e.start))
        if b.grid is not None or b.clock.running:
            _m, qf, _c, _u = self._slot_fn(ph)
            pairs += [(n.id, float(qf(n.t_on))) for n in b.notes if n.id in ph.notes and n.id not in seen]
        if self._fit_warp(ph, dict(pairs), pl.q0, pl.units):
            out = pl.rewrite_all(ph.fifths if ph.fifths is not None else 0, pl.q0)
            self._note_info_sync(ph, out, final=True)
            ph.end_x = max(ph.end_x, pl.written.end_x)
            self.dirty_now = True

    def _sixteenth_s(self, ph: _Phrase):
        """Seconds a 16th under the room's newest reading (None before a clock)."""
        b = self.builder
        if b is None:
            return None
        if b.grid is not None and len(b.grid.t) >= 2:
            gt = b.grid.t
            ibis = sorted(y - x for x, y in zip(gt[-9:], gt[-8:]) if y > x)
            return (ibis[len(ibis) // 2] if ibis else gt[-1] - gt[-2]) / b.unit
        if b.clock.running:
            return b.clock.P / 4.0
        return None

    def _fit_warp(self, ph: _Phrase, override: dict, q0: float, units, d: float | None = None) -> bool:
        """The phrase's warp: each note drawn so far anchored where it is drawn now, at its 16th under the newest
        reading (``override``: nid -> 16th; else its written 16th).  A rewrite, a
        better clock or a re-read then moves the bar lines, beams and values around the notes, not the notes (only
        those whose order changed: a note re-read past its neighbours, two that became a chord).  New notes go on
        from the last ones at their recent width.  False (no warp yet) with fewer than two notes drawn."""
        ph.warp_dirty = False
        pl = ph.pl
        Hd = self.stage.T["heads"]
        xr = self.stage.x_ref - ph.A
        sp = self.sp
        pts = []
        ser = ph.serial
        ws_ = pl.written_slot
        # only 16ths of the newest reading (the override, else written): a note it hasn't placed yet keeps its
        # place until it does
        for nid, slot in list(override.items()) + [(k, v) for k, v in ws_.items() if k not in override]:
            i = Hd.idx.get(("h", ser, nid, 0))
            if i is None or Hd.dying[i]:
                continue
            x = (float(Hd.a[i, RS.H_X1]) + xr) / sp - pl.side_of.get(nid, 0.0)     # its column, not its head
            if units:
                x -= RL.BAR_G * (math.floor((slot + 1e-6) / units) - math.floor((q0 + 1e-6) / units))
            pts.append((float(slot), x))
        if len(pts) < 2:
            return False
        pts.sort()
        ws, wx, cnt = [], [], []
        for sl, x in pts:                                      # one point a 16th (a chord: its mean)
            if ws and abs(sl - ws[-1]) < 1e-6:
                cnt[-1] += 1
                wx[-1] += (x - wx[-1]) / cnt[-1]
            else:
                ws.append(sl)
                wx.append(x)
                cnt.append(1)
        for k in range(1, len(ws)):                            # monotone, never squeezed past WARP_MIN
            wx[k] = max(wx[k], wx[k - 1] + WARP_MIN * (ws[k] - ws[k - 1]))
        # new notes (and 'now') go on at the width that keeps the paper's pace under the newest reading: steady, so
        # the playhead's target doesn't jump with each refit (the drawn notes' own local width swings after rewrites)
        if d is None:
            d = self._sixteenth_s(ph)
        if d:
            qt = self.pace * d
        else:
            qt = None
            for k in range(len(ws) - 2, -1, -1):
                if ws[-1] - ws[k] >= WARP_TAIL:
                    qt = (wx[-1] - wx[k]) / (ws[-1] - ws[k])
                    break
            if qt is None:
                qt = ph.Q or self.Q
        qt = min(Q_MAX, max(Q_MIN, qt))
        if len(ws) < 2:
            return False
        pl.warp = (ws, wx, qt)
        pl.warp_ver += 1
        ph.Q = pl.Q = qt
        self.regrid = True
        self._event("warp", {"n": len(ws), "Q": qt})
        return True

    def _set_q(self, ph: _Phrase, qkey: tuple, pairs: list, q0: float, units, d: float) -> None:
        """A 16th's width for a new mapping (a better clock, a turned metre, a rewrite): the width that moves the
        notes drawn so far least (least squares through the phrase's anchor, over each note's x now against its 16th
        under the new mapping), so the change re-slides notes locally instead of stretching the phrase; with nothing
        drawn yet, the pace (``d``: seconds a 16th)."""
        Hd = self.stage.T["heads"]
        num = den = 0.0
        n = 0
        for nid, slot in pairs:
            i = Hd.idx.get(("h", ph.serial, nid, 0))
            ds = slot - q0
            if i is None or Hd.dying[i] or ds < 1.0:
                continue
            x_sp = (float(Hd.a[i, RS.H_X1]) + self.stage.x_ref - ph.A) / self.sp
            if units:
                x_sp -= RL.BAR_G * (math.floor((slot + 1e-6) / units) - math.floor((q0 + 1e-6) / units))
            if x_sp <= 0.0:
                continue
            num += x_sp * ds
            den += ds * ds
            n += 1
        Q = num / den if n >= 2 and den > 0 else self.pace * d
        free = qkey[0] == "free"
        ph.Q = ph.pl.Q = Q if free else min(Q_MAX, max(Q_MIN, Q))
        ph.q_mode = qkey
        self.regrid = True
        self._event("q", {"key": list(qkey), "Q": ph.Q, "kept": n})

    def _pass(self) -> None:
        """Lay out the open phrase's unwritten notes and apply them."""
        ph = self.ph
        b = self.builder
        if ph is None or ph.closed or b is None:
            return
        self.stats["passes"] += 1
        pl = ph.pl
        now = self.t_sound - self.lag                      # what the bus has told us so far: held notes grow to here
        notes = [n for n in b.notes if n.id in ph.notes]
        mode, qf, _cf, unit = self._slot_fn(ph)
        metred = mode == "grid" or (mode == "clock" and not b.analysed) or pl.wbars > 0
        fifths = ph.fifths if ph.fifths is not None else self._fifths()
        ref = ph.notes.get(ph.ref)
        if pl.wspacer is not None:
            q0 = pl.q0
        elif ref is not None and ref.t_on is not None:
            q0 = float(qf(ref.t_on))
        else:
            q0 = float(qf(notes[0].t_on)) if notes else 0.0
        # a 16th's width: as each better clock takes over (free time, the room's beat clock, the analysed grid) the
        # phrase keeps the paper's pace, so the notes stay about where they are (the grid often counts the beat at
        # half or twice the clock's rate: the notes would all slide two-fold)
        qkey = (mode, b.beats * b.unit if b.grid is not None else 16, b.unit if b.grid is not None else 4)
        if ph.warp_dirty or qkey != ph.q_mode:
            units_l = (b.beats * b.unit if b.grid is not None else 16) if metred and mode != "free" else None
            if self._fit_warp(ph, {n.id: float(qf(n.t_on)) for n in notes if n.id not in pl.written_slot}, q0,
                              units_l):
                ph.q_mode = qkey
        if pl.wspacer is None and qkey != ph.q_mode:
            if mode == "free":
                d = ph.slot_s
            elif mode == "clock":
                d = b.clock.P / 4.0
            else:
                gt = b.grid.t
                ibis = sorted(y - x for x, y in zip(gt[-9:], gt[-8:]) if y > x)
                d = (ibis[len(ibis) // 2] if ibis else 0.5) / b.unit
            self._set_q(ph, qkey, [(n.id, float(qf(n.t_on))) for n in notes], q0,
                        qkey[1] if metred and mode != "free" else None, d)
        bars, chords = [], None
        if notes:
            staffs = b.staves(notes)
            if metred and mode != "free":
                tl = b.grid if b.grid is not None else b.clock
                if b.grid is not None:
                    _m, _qf, cf, _u = self._slot_fn(ph)

                    class _TL:                             # the grid, extrapolated as _slot_fn does
                        @staticmethod
                        def beat(t, cf=cf, u=b.unit):
                            return cf(t) / u
                    tl = _TL
                bars = RL.metred_bars(b, notes, staffs, b.next_bar, now, tl)
            else:
                slots = [float(qf(n.t_on)) for n in notes]
                ends = [float(qf(n.t_off if n.t_off is not None else now)) for n in notes]
                ends = [max(e, s + (1 if mode != "free" else 0.25)) for s, e in zip(slots, ends)]
                chords = RL.open_chords(notes, staffs, slots, ends, unit)
        units = (b.beats * b.unit if b.grid is not None else 16) if metred else None
        beats = b.beats if b.grid is not None else 4
        pl.provisional(bars, chords, fifths, q0, units, unit if b.grid is not None else 4, beats)
        self._event("pass", {"mode": mode, "q0": q0, "Q": ph.Q, "wbars": pl.wbars, "nb": b.next_bar,
                             "units": units, "n": len(notes)})
        out = pl.prov
        # the phrase's pace (free time for the next phrase starts from it)
        if b.grid is not None and len(b.grid.t) >= 2:
            ph.slot_s = min(0.4, max(0.05, (b.grid.t[-1] - b.grid.t[-2]) / b.unit))
        elif b.clock.running:
            ph.slot_s = b.clock.P / 4.0
        # the phrase's opening signature, once the metre is known (the key only where it changes)
        sigs = {}
        if metred:
            meter = ph.meter or self._meter_now() or (4, 4)
            k = self.phrases.index(ph)
            prev = self.phrases[k - 1] if k > 0 else None
            show_key = prev is not None and prev.fifths is not None and prev.fifths != fifths
            sigs = RL.signature_marks(ph.serial, 0.0, pl.first_left, fifths, show_key, meter)
        out.sigs = sigs
        self._note_info_sync(ph, out, final=False)
        ph.end_x = max(ph.end_x, out.end_x, pl.written.end_x)
        self.regrid = False

    def _note_info_sync(self, ph: _Phrase, out, final: bool) -> None:
        info = {nid: (m.pitch, m.t_on, m.t_off) for nid, m in ph.notes.items()}
        if final:
            self.stage.sync(ph.serial, out, ph.A, self.t_sound, final=True, mapver=ph.mapver, regrid=self.regrid,
                            owned=set(), note_info=info, reuse=ph.prov_owned - ph.pl.written_keys)
            ph.prov_owned -= set(out.heads) | set(out.stems) | set(out.beams) | set(out.ties) | set(out.rests) \
                | set(out.bars)
        else:
            owned = ph.prov_owned - ph.pl.written_keys
            ph.prov_owned = self.stage.sync(ph.serial, out, ph.A, self.t_sound, final=False, mapver=ph.mapver,
                                            regrid=self.regrid, owned=owned, note_info=info)
        Hd = self.stage.T["heads"]
        st = self.stage
        for key in out.heads:
            i = Hd.idx.get(key)
            if i is not None:
                m = ph.notes.get(key[2])
                if m is not None and m.t_on is not None:
                    Hd.a[i, RS.H_TON] = st.tt(m.t_on)
                    Hd.a[i, RS.H_TOFF] = st.tt(m.t_off) if m.t_off is not None else 1e9
                    Hd.a[i, RS.H_VEL] = m.vel

    # -- the paper -----------------------------------------------------------------------------------------------------
    def _playhead_target(self, ahead: float = 0.0) -> float | None:
        """The tape px where the playhead should be now (the open phrase's 'now', less the arrival lag), or
        ``ahead`` seconds from now."""
        ph = self.ph
        b = self.builder
        if ph is None or ph.closed or b is None or ph.pl.q0 is None:
            return None
        pl = ph.pl
        _mode, _qf, cf, unit = self._slot_fn(ph)
        t_n = self.t_sound - self.lag + ahead
        held = any(n.t_off is None for n in b.notes if n.id in ph.notes)
        if not held and ph.t_last is not None:
            t_n = min(t_n, ph.t_last + AHEAD_BEATS * 4 * ph.slot_s)
        slot = cf(t_n)
        sp_ = pl.prov_spacer if getattr(pl, "prov_spacer", None) is not None else None
        if sp_ is None:
            x = (slot - pl.q0) * (ph.Q or self.Q)
        else:
            x = sp_.base(slot) + sp_.push
        return ph.A + x * self.sp

    def _paper(self, dt: float) -> None:
        if self.S is None:
            return
        D = X_PLAY * self.w - self.XL
        U = self._playhead_target()
        if U is None:
            U = (self.U if self.U is not None else self.S + D) + V_IDLE_SP * self.sp * dt
        if self.U is not None and dt > 0:
            v = (U - self.U) / dt
            self.v_ff += (min(max(v, 0.0), 40 * self.sp) - self.v_ff) * (1.0 - math.exp(-dt / 0.4))
        self.U = U
        if dt <= 0:
            return
        target = U - D
        w = S_OMEGA
        a = w * w * (target - self.S) + 2.0 * w * (self.v_ff - self.V)
        amax = S_AMAX_SP * self.sp
        a = min(max(a, -amax), amax)
        self.V = max(0.0, self.V + a * dt)
        self.S += self.V * dt

    # -- the systems' headers -----------------------------------------------------------------------------------------
    def _headers(self) -> np.ndarray:
        sp, R = self.sp, self.R
        t1, t2 = RL.T1 * sp, RL.T2 * sp
        out = []
        XM = self.XM
        GI = RG.GI
        keys = []
        for r in range(R):
            u = self.S - (R - 1 - r) * self.WV if self.S is not None else 0.0
            fifths, meter = 0, None
            for ph in self.phrases:
                if ph.A - 4 * sp <= u or ph is self.phrases[0]:
                    is_open = ph is self.ph and not ph.closed
                    fifths = ph.fifths if ph.fifths is not None else (self._fifths() if is_open else 0)
                    meter = ph.meter or (self._meter_now() if is_open else None)
            hs = self.hdr[r]
            if fifths != hs["fifths"]:
                hs["fifths"], hs["t_key"] = fifths, self.clock
            if meter != hs["meter"]:
                hs["meter"], hs["t_meter"] = meter, self.clock
            busy = self.clock - max(hs["t_key"], hs["t_meter"]) < 0.8
            ck = (fifths, meter, round(self.XM, 1))
            keys.append((ck if not busy else self.clock))
            if not busy and hs.get("ck") == ck:
                out.append(hs["rec"])
                self._hdr_w = max(getattr(self, "_hdr_w", 0.0), hs["w"])
                continue
            first = len(out)
            # one call for the row's glyphs: brace, clefs, key, time (pops and flaps while they change)
            gx, gy, gg, gsx, gsy = [XM, XM + 1.6 * sp, XM + 1.6 * sp], [t2 + 4.15 * sp, t1 + 3 * sp, t2 + sp], \
                [GI["brace"], GI["gClef"], GI["fClef"]], [2.6 * sp, sp, sp], [(t2 + 4.3 * sp - t1) / 4.0, sp, sp]
            x = XM + 5.0 * sp
            n = abs(fifths)
            if n:
                pos = RL.KS_SHARPS if fifths > 0 else RL.KS_FLATS
                g = GI["accidentalSharp" if fifths > 0 else "accidentalFlat"]
                for st in (0, 1):
                    for i in range(n):
                        k = min(1.0, max(0.0, (self.clock - hs["t_key"] - 0.06 * i) / 0.18))
                        s_ = k * (1.0 + 0.25 * math.sin(math.pi * k))
                        if s_ <= 0.01:
                            continue
                        gx.append(x + i * RL.KS_STEP * sp + (1 - s_) * 0.4 * sp)
                        gy.append(RL.step_y(pos[st][i], st) * sp)
                        gg.append(g)
                        gsx.append(s_ * sp)
                        gsy.append(s_ * sp)
                x += n * RL.KS_STEP * sp + 0.5 * sp
            if meter is not None:
                f = min(1.0, max(0.0, (self.clock - hs["t_meter"]) / 0.25))
                for st, top in ((0, t1), (1, t2)):
                    for row_i, digits, dy in ((0, str(meter[0]), 1.0), (1, str(meter[1]), 3.0)):
                        for i, ch in enumerate(digits):
                            gx.append(x + i * RL.TS_W * sp)
                            gy.append(top + dy * sp)
                            gg.append(GI[f"timeSig{ch}"])
                            gsx.append(sp)
                            gsy.append(max(f, 0.02) * sp)
                x += 2 * RL.TS_W * sp
            out.append(RR.glyph(np.array(gx), np.array(gy), np.array(gg), np.array(gsx), np.array(gsy), row=r))
            out.append(RR.box(XM + 0.95 * sp, t1, XM + 1.11 * sp, t2 + 4 * sp, row=r))
            self._hdr_w = max(getattr(self, "_hdr_w", 0.0), x - XM)
            hs["rec"] = np.concatenate(out[first:])
            hs["ck"] = ck
            hs["w"] = x - XM
        return (np.concatenate(out) if out else np.zeros((0, RR.NF), np.float32)), tuple(keys)

    def _meter_now(self):
        b = self.builder
        if b is None:
            return None
        if b.grid is not None:
            return (b.beats * 3, 8) if b.unit == 6 else (b.beats, 4)
        if b.clock.running and not b.analysed:
            return (4, 4)
        return None

    # -- render ----------------------------------------------------------------------------------------------------
    def _event(self, kind: str, data: dict) -> None:
        if self.record_events:
            self.events.append((self.t_sound - (self.t_base or 0.0), self.t_sound, kind, data))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._p = p
        dt = min(max(dt, 0.0), 0.1)
        e = self._e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        self.clock += dt
        self.breath += 0.25 * spd * dt
        self.drift += float(p["drift_speed"]) * spd * dt
        a = 1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0
        self.kick += (pulse - self.kick) * a
        self.pal = np.asarray(palette_coeffs(p["palette"]) if p.get("palette") else palette_coeffs("ember"),
                              np.float64)
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        self.t_sound = float(f.t_ms) / 1000.0 if f is not None else self.t_sound + dt
        if self.t_base is None:
            self.t_base = self.stage.t_ref = self.t_sound
        tm = self.tm = {}
        _t0 = time.perf_counter()
        self._wall = time.monotonic()
        if self.builder is not None:
            self._drain(f, self.t_sound)
            ph = self.ph
            if ph is not None and not ph.closed and not self.dirty:
                # a held note's value grows: lay out again when 'now' crosses a 16th
                if any(n.t_off is None for n in self.builder.notes):
                    _m, qf, _c, _u = self._slot_fn(ph)
                    s = int(math.floor(qf(self.t_sound)))
                    if s != self.last_pass_slot:
                        self.last_pass_slot = s
                        self.dirty = True
            _t1 = time.perf_counter()
            tm["drain"] = _t1 - _t0
            # a birth is laid out at once; anything else at most every 50 ms (a burst of releases and replies
            # is one pass)
            if self.dirty_now or (self.dirty and self.clock - self.last_pass >= 0.05):
                self.dirty = self.dirty_now = False
                self.last_pass = self.clock
                try:
                    self._pass()
                except Exception:                             # degrade: what is on stage stays; log once a phrase
                    if not getattr(self.ph, "logged", False):
                        log.exception("score_ribbon: layout pass failed")
                        if self.ph is not None:
                            self.ph.logged = True
            tm["pass"] = time.perf_counter() - _t1
        if self._snap and self.ph is not None:
            U = self._playhead_target()
            if U is not None:                                    # (a join: the scene's first content), moving at
                U2 = self._playhead_target(0.25)                 # the music's pace from the first frame
                v = max(0.0, (U2 - U) / 0.25) if U2 is not None else 0.0
                self.S = U - (X_PLAY * self.w - self.XL)
                self.U, self.V, self.v_ff = U, v, v
                self._snap = False
                self._event("snap", {"S": self.S})
        if self._join_outs:
            self._flush_join()
        self._joining = False
        _t1 = time.perf_counter()
        self._paper(dt)
        S = self.S if self.S is not None else 0.0
        st = self.stage
        st.flourish = float(p["flourish"])
        if S - st.x_ref > 1.0e5:                              # keep float32 positions near the tape's origin
            st.rebase_x(S - st.x_ref)
        cull = S - (self.R - 1) * self.WV - 8 * self.sp
        _t2 = time.perf_counter()
        st.step(self.t_sound, cull)                          # housekeeping: the dead released, the culled
        _t3 = time.perf_counter()
        tm["paper"], tm["step"] = _t2 - _t1, _t3 - _t2
        for ph in list(self.phrases):                            # phrases scrolled away
            if ph.closed and ph.A + (ph.end_x + 2) * self.sp < cull:
                st.drop_phrase(ph.serial)
                self.phrases.remove(ph)
                for nid in list(ph.notes):
                    self.notes.pop(nid, None)
        # the header's width (its key signature) sets where the tape begins
        hdr, hkey = self._headers()
        if hkey != getattr(self, "_hkey", None):
            st.set_header(hdr)
            self._hkey = hkey
        need = self.XM + max(13.0 * self.sp, getattr(self, "_hdr_w", 0.0) + 1.5 * self.sp)
        self._hdr_w = 0.0
        self.XL_t = need
        self.XL += (self.XL_t - self.XL) * (1.0 - math.exp(-dt / 0.4)) if dt > 0 else 0.0
        self.WV = self.w - self.XL - self.XM
        tm["headers"] = time.perf_counter() - _t3
        Ph = self.Ph
        sp = self.sp
        Ph[RK.U_W], Ph[RK.U_H], Ph[RK.U_NIGHT], Ph[RK.U_SP] = self.w, self.h, int(self.night), sp
        Ph[RK.U_R], Ph[RK.U_Y0], Ph[RK.U_PITCH], Ph[RK.U_XL], Ph[RK.U_WV] = (self.R, self.y0, self.pitch_px, self.XL,
                                                                           self.WV)
        Ph[RK.U_SX0], Ph[RK.U_SX1], Ph[RK.U_TH] = self.XM + 0.95 * sp, self.w - self.XM, 0.13 * sp
        Ph[RK.U_T1], Ph[RK.U_T2], Ph[RK.U_FOLD] = RL.T1 * sp, RL.T2 * sp, 1.5 * sp
        Ph[RK.U_GAIN] = float(p["intensity"])
        Ph[RK.U_BREATH] = 1.0 + 0.012 * math.sin(self.breath)
        Ph[RK.U_PX] = X_PLAY * self.w
        Ph[RK.U_PA] = float(p["playhead"]) * (0.35 if self.night else 0.25) * (0.7 + 0.3 * e)
        Ph[RK.U_KICK] = self.kick
        pal = self.pal
        Ph[RK.U_PR:RK.U_PB + 1] = np.clip(pal[0:3] + pal[3:6] * np.cos(2.0 * np.pi * (pal[6:9] * (self.drift + 0.1)
                                                                                     + pal[9:12])), 0, 1)
        Ph[RK.U_HALO_R], Ph[RK.U_NC], Ph[RK.U_BW], Ph[RK.U_ROWH] = 0.32 * sp, self.painter.NC, RK.BIN_W, self.rowh
        Ph[RK.U_STAFF_A] = 0.8 if self.night else 0.85
        Ph[RK.U_PAL:RK.U_PAL + 12] = pal
        Ph[RK.U_TNOW] = st.tt(self.t_sound)
        Ph[RK.U_PERIOD] = float(p["palette_period"])
        Ph[RK.U_DRIFT] = self.drift % 10.0
        Ph[RK.U_GLOW] = float(p["note_glow"])
        Ph[RK.U_ENERGY] = e
        Ph[RK.U_FRESH], Ph[RK.U_RELTAU], Ph[RK.U_MIXMAX] = FRESH_S * (0.7 + 0.6 * e), REL_TAU * (0.5 + e), 0.85
        target = out if self.direct else self.buf
        _t6 = time.perf_counter()
        self.painter.draw(target, Ph, st, S, self.t_sound)
        tm["paint"] = time.perf_counter() - _t6
        if not self.direct:
            present(self.ctx, self.buf, out)

    def close(self) -> None:
        room, self.room = getattr(self, "room", None), None
        if room is not None:
            try:
                room.leave(self)
            except Exception:
                pass
        if getattr(self, "painter", None) is not None:
            self.painter.close()
            self.painter = None
        self.stage = None
        self.buf = None
