"""stream_service/score: the quantiser, MEI writer and (when Verovio is installed) the engraving worker."""
from __future__ import annotations

import math

import numpy as np
import pytest

from stream_service.score.mei import signature, spell, system
from stream_service.score.quantize import BAR, ScoreBuilder, estimate_period, split_value


def test_split_value_pieces_are_notatable_and_sum():
    for pos in range(BAR):
        for dur in range(1, BAR - pos + 1):
            parts = split_value(pos, dur)
            assert sum(parts) == dur and all(p in (1, 2, 3, 4, 6, 8, 12, 16) for p in parts)


def test_spelling_follows_the_key():
    assert spell(61, 2, signature(2))[:2] == ("c", 1)          # C# in D major
    assert spell(70, -1, signature(-1))[:2] == ("b", -1)       # Bb in F major
    assert spell(66, -3, signature(-3))[:2] == ("g", -1)       # chromatic: flats in flat keys
    assert spell(60, 0, signature(0)) == ("c", 0, 4)


def test_period_estimate_folds_into_the_readable_octave():
    on = [i * 0.25 for i in range(16)]                         # 16ths at 60 bpm
    assert 0.45 <= estimate_period(on) < 0.9


def test_builder_writes_bars_from_steady_playing():
    sb = ScoreBuilder()
    bars, t = [], 0.0
    beat = 0.5                                                 # 120 bpm: a C major arpeggio in eighths
    for i in range(64):
        on = i * beat / 2
        while t < on:
            bars += sb.poll(t)
            t += 0.02
        p = (60, 64, 67, 72)[i % 4]
        sb.note_on(p, 0.6, on)
        sb.note_off(p, on + beat / 2 * 0.9)
    while t < 40:
        bars += sb.poll(t)
        t += 0.05
    assert len(bars) >= 6 and bars[-1].phrase_end
    treble = bars[2].staves[0]
    assert sum(e.dur for e in treble) == BAR
    assert sum(1 for e in treble if e.pitches) == 8            # eight eighths
    assert sb.key()[2] == 0                                    # C major: no sharps or flats


def test_mei_engraves(tmp_path):
    verovio = pytest.importorskip("verovio")
    pytest.importorskip("resvg_py")
    from stream_service.score.engrave import engrave
    sb = ScoreBuilder()
    for i in range(24):
        sb.note_on(60 + (i % 5) * 2, 0.5, i * 0.3)
        sb.note_off(60 + (i % 5) * 2, i * 0.3 + 0.25)
    bars = sb.poll(30.0)
    tk = verovio.toolkit()
    meta, ink = engrave(tk, {"id": 1, "mei": system(bars[:2], 4, 0), "width": 800})
    assert meta["ok"] and len(ink) == meta["w"] * meta["h"] and len(meta["measures"]) == 4
    assert all(m["x1"] > m["x0"] and len(m["staves"]) == 2 for m in meta["measures"])


def test_bars_engrave_at_one_staff_size():
    verovio = pytest.importorskip("verovio")
    pytest.importorskip("resvg_py")
    from stream_service.score.engrave import engrave
    from stream_service.score.mei import bar_doc, header_doc
    sb = ScoreBuilder()
    for i in range(40):
        p = (48, 60, 64, 67, 72, 76)[i % 6]
        sb.note_on(p, 0.6, i * 0.25)
        sb.note_off(p, i * 0.25 + 0.2)
    bars = [b for b in sb.poll(30.0) if not b.marker]
    tk = verovio.toolkit()
    spaces = []
    for doc in [header_doc(2, meter=True)] + [bar_doc(b, 2) for b in bars[:3]]:
        meta, ink = engrave(tk, {"id": 1, "mei": doc, "space_px": 12})
        top, bottom = meta["measures"][0]["staves"][0]
        spaces.append((bottom - top) / 4)
    assert all(abs(s - 12) < 0.3 for s in spaces), spaces


# -- metres other than 4/4, and the analysis path --------------------------------------------------------------------
def test_split_value_in_every_metre():
    for beats, unit in ((2, 4), (3, 4), (4, 4), (2, 6), (3, 6), (4, 6)):
        bar = beats * unit
        for pos in range(bar):
            for dur in range(1, bar - pos + 1):
                parts = split_value(pos, dur, beats, unit)
                assert sum(parts) == dur and all(p in (1, 2, 3, 4, 6, 8, 12, 16, 24) for p in parts), (beats, unit)
    assert split_value(0, 12, 3, 4) == [12]                    # 3/4: a dotted half fills the bar
    assert split_value(0, 12, 2, 6) == [12]                    # 6/8: so does a dotted half
    assert split_value(0, 6, 2, 6) == [6] and split_value(2, 4, 2, 6) == [4]


def test_beat_grid_maps_and_merges():
    from stream_service.score.quantize import BeatGrid
    g = BeatGrid([1.0, 1.5, 2.0, 2.5], 1)
    assert g.beat(1.5) == 0 and g.beat(1.75) == 0.5 and g.beat(3.0) == 3.0 and g.beat(0.5) == -2.0
    assert abs(g.time(g.beat(2.2)) - 2.2) < 1e-9
    g.merge([2.48, 3.02, 3.5, 4.5], keep_beat=1)               # keep up to beat 1 (2.0), then theirs; 4.5 fills 4.0
    assert g.t[:3] == [1.0, 1.5, 2.0] and g.t[3] == 2.48 and 4.0 in [round(x, 6) for x in g.t]
    g.extend_before(0.2)
    assert g.t[0] <= 0.2 and g.beat(2.0) == 1.0


def _waltz(bars=16, beat=0.5, t0=0.3):
    """Oom-pah-pah: a low bass note on 1, a chord on 2 and 3; the tune moves on 1."""
    notes = []
    for k in range(bars):
        b = t0 + k * 3 * beat
        root = (48, 43, 45, 41)[k % 4]
        notes.append([root - 12 if k % 2 else root, b, 3 * beat * 0.9, 90])
        notes.append([(76, 74, 72, 77)[k % 4], b, 2 * beat, 80])
        for j in (1, 2):
            for p in (root + 4, root + 7, root + 12):
                notes.append([p, b + j * beat, beat * 0.4, 55])
    return np.array(sorted(notes, key=lambda r: r[1]), float)


def test_metre_finds_a_waltz_and_its_downbeats():
    from stream_service.score.metre import MetreModel
    ns = _waltz()
    beats = np.arange(0.3, 0.3 + 48 * 0.5, 0.5)
    m = MetreModel()(ns, beats)
    assert m is not None and m.beats_per_bar == 3 and m.phase == 0 and not m.compound, m


def test_builder_writes_the_analysed_metre():
    sb = ScoreBuilder()
    sb.use_analysis(True)
    ns = _waltz(bars=12)
    ids = [sb.note_on(int(p), v / 127, t) for p, t, d, v in ns]
    for (p, t, d, v), i in zip(ns, ids):
        sb.note_off(int(p), t + d)
    now = float(ns[-1, 1]) + 1.0
    assert sb.poll(now) == []                                   # no metre yet: nothing written
    req = sb.analysis_request(now)
    assert req is not None and len(req["notes"]) == len(ns)
    beats = [round(x - req["t0"], 4) for x in np.arange(0.3, 0.3 + 36 * 0.5, 0.5)]
    sb.on_analysis({"ok": True, "t0": req["t0"], "phrase": req["phrase"], "beats": beats,
                    "metre": {"beats": 3, "phase": 0, "compound": False, "conf": 0.9},
                    "key_fifths": -1, "hands": {str(i): 1 if p < 60 else 0 for i, (p, *_r) in zip(ids, ns)},
                    "last_onset": req["notes"][-1][1]})
    bars = [b for b in sb.poll(now) if not b.marker]
    assert len(bars) >= 8 and all(b.units == 12 and b.meter == (3, 4) for b in bars)
    assert bars[1].staves[1][0].pitches and bars[1].staves[1][0].start == 0    # the bass note on beat 1
    assert sb.key()[2] == -1
    from stream_service.score.mei import bar_doc, header_doc
    assert 'meter.count="3" meter.unit="4"' in bar_doc(bars[1], -1)
    assert 'meter.count="6" meter.unit="8"' in header_doc(0, meter=True, sig=(6, 8))


def test_turned_evidence_rewrites_the_phrase():
    sb = ScoreBuilder()
    sb.use_analysis(True)
    ns = _waltz(bars=12)
    for p, t, d, v in ns:
        sb.note_on(int(p), v / 127, t)
        sb.note_off(int(p), t + d)
    now = float(ns[-1, 1]) + 1.0
    req = sb.analysis_request(now)
    beats = [round(x - req["t0"], 4) for x in np.arange(0.3, 0.3 + 36 * 0.5, 0.5)]
    base = {"ok": True, "t0": req["t0"], "phrase": req["phrase"], "beats": beats, "key_fifths": 0,
            "last_onset": req["notes"][-1][1]}
    four = {"beats": 4, "phase": 0, "compound": False, "conf": 0.8, "compound_p": 0.1,
            "probs": {"2": 0.1, "3": 0.1, "4": 0.8}, "phases": {"2": 0, "3": 0, "4": 0}}
    three = {**four, "beats": 3, "probs": {"2": 0.02, "3": 0.96, "4": 0.02}}
    sb.on_analysis({**base, "metre": four})
    first = [b for b in sb.poll(now) if not b.marker]
    assert first and all(b.beats == 4 for b in first)
    for _ in range(4):
        sb.on_analysis({**base, "metre": three})
    again = sb.poll(now)
    assert again[0].rewrite and again[0].marker
    bars = [b for b in again if not b.marker]
    assert bars and all(b.beats == 3 for b in bars) and bars[0].index == first[0].index


# -- motion (score/motion.py): the live score's glides, springs, pen and layout plans ------------------------------
def test_spring_reaches_its_target_without_overshoot_and_on_arrays():
    from stream_service.score.motion import spring_step
    for dt in (1 / 60, 1 / 30, 0.1, 0.5):
        x, v, xs = 0.0, 0.0, []
        for _ in range(int(6 / dt) + 2):
            x, v = spring_step(x, v, 100.0, 6.0, dt)
            xs.append(x)
            assert x <= 100.0 + 1e-9 and math.isfinite(x)
        assert abs(xs[-1] - 100.0) < 0.5 and all(b >= a - 1e-9 for a, b in zip(xs, xs[1:]))
    xa, va = spring_step(np.zeros(3), np.zeros(3), np.array([1.0, 2.0, 3.0]), 6.0, 1 / 30)
    xb, _ = spring_step(0.0, 0.0, 2.0, 6.0, 1 / 30)
    assert xa.shape == (3,) and abs(xa[1] - xb) < 1e-12


def test_glide_lands_continuously_and_starts_gently():
    from stream_service.score.motion import Glide
    dt, D, T = 1 / 30, 347.0, 1.6
    g = Glide(0.0)
    g.to(D, T)
    first = g.step(dt)
    assert abs(first) < 1e-3 * D
    for _ in range(int(T / dt) + 2):
        g.step(dt)
    assert g.x == D and g.v == 0.0 and g.a == 0.0
    # a re-target half way: x, v and a carry on continuously
    g = Glide(0.0)
    g.to(D, T)
    xs, vs, accs = [], [], []
    for i in range(120):
        if i == int(T / 2 / dt):
            g.to(2 * D, T)
        g.step(dt)
        xs.append(g.x), vs.append(g.v), accs.append(g.a)
    dv = np.abs(np.diff(vs)).max() * 1.0
    da = np.abs(np.diff(accs)).max()
    assert np.abs(np.diff(xs)).max() < 1.2 * max(vs) * dt + 1e-6
    assert dv < 1.2 * max(abs(a) for a in accs) * dt + 1e-6 and da < 0.25 * max(abs(a) for a in accs)
    assert abs(xs[-1] - 2 * D) < 1e-6


def test_pen_follows_the_frontier_within_its_caps():
    from stream_service.score.motion import PEN_A, PEN_D, Pen
    dt, bar_s = 1 / 30, 1.3
    R = 1 / bar_s
    pen, F, t = Pen(), 0.0, 0.0
    moving = steps = 0
    nxt, k = 1.0, 0
    for i in range(int(40 / dt)):
        t = i * dt
        while t >= nxt:                                  # a bar each bar_s, every fifth time a burst of three
            k += 1
            F += 3.0 if k % 5 == 0 else 1.0
            nxt += bar_s * (3 if k % 5 == 0 else 1)
        v0, p0 = pen.v, pen.p
        pen.step(F, R, dt)
        assert pen.p <= F + 1e-12
        assert pen.v - v0 <= PEN_A * R * dt + 1e-9 and v0 - pen.v <= PEN_D * R * dt + 1e-9
        if t > 3.0:
            steps += 1
            moving += pen.p > p0 + 1e-6
    assert moving > 0.9 * steps, (moving, steps)
    for i in range(int(3 * bar_s / dt)):
        pen.step(F, R, dt)
    assert F - pen.p < 0.05


def test_line_plan_slots_fill_the_line_and_spill_shifts_later_lines():
    from stream_service.score.motion import MAX_LINES, LinePlan
    lp = LinePlan()
    cw, pad = 1804.0, 14.0
    for hdr in (126.0, 90.0, 90.0):
        j = lp.plan_next(cw, hdr, 15 * 14.0, pad)
        L = lp.line(j)
        assert abs(L.hdr_w + L.n * L.bw + pad - cw) < 1e-6 and 2 <= L.n <= 8
    for k in range(lp.lines[-1].k0 + lp.lines[-1].n):
        j = lp.line_of(k)
        L = lp.line(j)
        assert L.k0 <= k < L.k0 + L.n
        assert j == lp.line_of(k - 1) or (j == lp.line_of(k - 1) + 1 and k == L.k0) or k == 0
    k0s = [L.k0 for L in lp.lines]
    k = lp.lines[0].k0 + lp.lines[0].n - 1                # the first line's last bar doesn't fit
    assert lp.spill(k) == 1
    assert lp.lines[0].closed and lp.lines[0].n == k and [L.k0 for L in lp.lines[1:]] == [x - 1 for x in k0s[1:]]
    assert lp.line_of(k) == 1 and lp.spill(lp.lines[1].k0) == 0
    for _ in range(20):
        lp.plan_next(cw, 90.0, 15 * 14.0, pad)
    assert len(lp.lines) <= MAX_LINES


def test_ruler_freezes_inserts_and_stays_monotonic():
    from stream_service.score.motion import Ruler
    r = Ruler(100.0, 40.0, 200.0)
    assert r.freeze(0, 210.0) and not r.freeze(0, 999.0) and r.w[0] == 210.0
    r.freeze(1, 220.0)
    before = [r.u0(k) for k in range(5)]
    r.insert(2, 30.0)
    after = [r.u0(k) for k in range(5)]
    assert after[:2] == before[:2] and all(abs(a - b - 30.0) < 1e-9 for a, b in zip(after[2:], before[2:]))
    r.place(1, 250.0)
    assert r.w[1] == 220.0 and r.width(1) == 250.0
    us = [r.u(p) for p in np.linspace(-1.0, 6.0, 300)]
    assert all(b >= a - 1e-9 for a, b in zip(us, us[1:])) and r.u(-1.0) == 100.0 and r.u(0.0) == 140.0


def test_lane_map_squeezes_smoothly():
    from stream_service.score.motion import lane_map
    f, c = lane_map(50.0, 300.0, 400.0)
    assert f == 50.0 and c == 1.0
    D, Lw = 1200.0, 400.0
    d = np.linspace(0.0, D, 2001)
    f, c = lane_map(d, D, Lw)
    assert abs(f[0]) < 1e-12 and abs(f[-1] - Lw) < 1e-6 and 0 < c < 1
    assert abs((f[1] - f[0]) / (d[1] - d[0]) - 1.0) < 0.01 and np.all(np.diff(f) > 0)
    for knee in (0.0, 200.0, 1e9):                             # a wider knee (fast scroll) keeps the same contract
        f, c = lane_map(d, D, Lw, knee=knee)
        assert abs(f[0]) < 1e-12 and abs(f[-1] - Lw) < 1e-6 and 0 < c < 1 and np.all(np.diff(f) > 0)
        assert abs((f[1] - f[0]) / (d[1] - d[0]) - 1.0) < 0.01


def test_ring_slices_cover_exactly():
    from stream_service.score.motion import ring_slices
    TW = 64
    for u in range(-70, 140, 7):
        for w in (1, 5, 30, 64):
            sl = ring_slices(u, w, TW)
            assert 1 <= len(sl) <= 2 and sum(s[2] for s in sl) == w
            cols = [(d + i) for d, _o, n in sl for i in range(n)]
            assert cols == [(u + i) % TW for i in range(w)]
            assert [o for _d, o, _n in sl] == [0] + ([sl[0][2]] if len(sl) == 2 else [])


def test_where_and_position_agree_with_the_written_bars():
    sb = ScoreBuilder()
    sb.use_analysis(True)
    ns = _waltz(bars=12)
    ids = [sb.note_on(int(p), v / 127, t) for p, t, d, v in ns]
    for (p, t, d, v), i in zip(ns, ids):
        sb.note_off(int(p), t + d)
    now = float(ns[-1, 1]) + 1.0
    assert sb.position(now) is None
    w0 = sb.where(float(ns[5, 1]))
    assert w0 is None or w0[2]                                  # before the metre: on the clock, provisional
    req = sb.analysis_request(now)
    beats = [round(x - req["t0"], 4) for x in np.arange(0.3, 0.3 + 36 * 0.5, 0.5)]
    sb.on_analysis({"ok": True, "t0": req["t0"], "phrase": req["phrase"], "beats": beats,
                    "metre": {"beats": 3, "phase": 0, "compound": False, "conf": 0.9},
                    "key_fifths": -1, "hands": {}, "last_onset": req["notes"][-1][1]})
    t = float(ns[30, 1])
    assert abs(sb.position(t) - (sb.bars_ahead(t) + sb.next_bar)) < 1e-9
    where = {i: sb.where(t_on) for (p, t_on, d, v), i in zip(ns, ids)}
    assert all(w is not None and not w[2] for w in where.values())
    assert abs(sb.bar_seconds() - 1.5) < 1e-6
    bars = [b for b in sb.poll(now) if not b.marker]
    assert [b.pos for b in bars] == list(range(len(bars)))
    seen = 0
    for b in bars:
        for staff in b.staves:
            for ev in staff:
                for nid, tin in zip(ev.ids, ev.tie_in):
                    if not tin:                                 # the note's onset: where() said this bar and 16th
                        assert where[nid][0] == b.pos and abs(where[nid][1] * b.units - ev.start) < 1e-9
                        seen += 1
    assert seen > 0.6 * len(ns)                                 # (the newest bars wait for the analysis)


def test_bar_pos_restarts_after_a_rewrite():
    sb = ScoreBuilder()
    sb.use_analysis(True)
    ns = _waltz(bars=12)
    for p, t, d, v in ns:
        sb.note_on(int(p), v / 127, t)
        sb.note_off(int(p), t + d)
    now = float(ns[-1, 1]) + 1.0
    req = sb.analysis_request(now)
    beats = [round(x - req["t0"], 4) for x in np.arange(0.3, 0.3 + 36 * 0.5, 0.5)]
    base = {"ok": True, "t0": req["t0"], "phrase": req["phrase"], "beats": beats, "key_fifths": 0,
            "last_onset": req["notes"][-1][1]}
    four = {"beats": 4, "phase": 0, "compound": False, "conf": 0.8, "compound_p": 0.1,
            "probs": {"2": 0.1, "3": 0.1, "4": 0.8}, "phases": {"2": 0, "3": 0, "4": 0}}
    sb.on_analysis({**base, "metre": four})
    first = [b for b in sb.poll(now) if not b.marker]
    assert [b.pos for b in first] == list(range(len(first)))
    for _ in range(4):
        sb.on_analysis({**base, "metre": {**four, "beats": 3, "probs": {"2": 0.02, "3": 0.96, "4": 0.02}}})
    again = [b for b in sb.poll(now) if not b.marker]
    assert again and again[0].pos == 0 and [b.pos for b in again] == list(range(len(again)))


def _verovio_bars():
    pytest.importorskip("verovio")
    pytest.importorskip("resvg_py")
    from stream_service.score.quantize import Bar, Event

    def run(n, base, bar=16, ids=1000):
        d = bar // n
        return [Event(i * d, d, [base + (i * 3) % 7], [ids + i], [False], [False]) for i in range(n)]
    return {
        "sparse": Bar(1, ([Event(0, 16, [72], [1], [False], [False])],
                          [Event(0, 16, [48], [2], [False], [False])]), 0, 1),
        "mid": Bar(2, (run(8, 72), run(4, 48, ids=2000)), 0, 1),
        "dense": Bar(3, (run(16, 74), run(8, 45, ids=2000)), 0, 1),
        "waltz_sparse": Bar(4, ([Event(0, 12, [72], [5], [False], [False])],
                                [Event(0, 12, [48], [6], [False], [False])]), 0, 1, beats=3),
        "waltz_mid": Bar(5, (run(6, 72, 12), run(3, 48, 12, ids=2000)), 0, 1, beats=3),
    }


def test_fit_px_justifies_a_bar_to_its_slot():
    verovio = pytest.importorskip("verovio")
    from stream_service.score.engrave import engrave
    from stream_service.score.mei import bar_doc
    bars = _verovio_bars()
    tk = verovio.toolkit()
    sp = 14.0
    for name in ("sparse", "mid", "waltz_sparse", "waltz_mid"):
        b = bars[name]
        for slot in (24, 30):
            meta, ink = engrave(tk, {"id": 1, "mei": bar_doc(b, -2), "space_px": sp, "fit_px": slot * sp})
            m = meta["measures"][0]
            got = m["x1"] + 1 - (m["x0"] + 0.35 * sp)
            assert meta["ok"] and len(ink) == meta["w"] * meta["h"]
            assert abs(got - meta["fit"]) <= 0.5 * sp, (name, slot, got / sp, meta["fit"] / sp)
            if meta["natural"] * 0.85 <= slot * sp:
                assert abs(got - slot * sp) <= 0.5 * sp, (name, slot, got / sp)
            notes = {i for st in b.staves for ev in st for i in ev.ids}
            assert {h[0] for h in meta["heads"]} == notes
            assert all(m["x0"] < h[1] < m["x1"] for h in meta["heads"])
    meta, _ = engrave(tk, {"id": 1, "mei": bar_doc(bars["dense"], 0), "space_px": sp, "fit_px": 16 * sp})
    m = meta["measures"][0]
    assert m["x1"] + 1 - (m["x0"] + 0.35 * sp) >= 0.85 * meta["natural"] - 0.5 * sp
    plain, _ = engrave(tk, {"id": 1, "mei": bar_doc(bars["mid"], 0), "space_px": sp})
    assert "heads" not in plain and "natural" not in plain


def test_change_doc_engraves():
    verovio = pytest.importorskip("verovio")
    pytest.importorskip("resvg_py")
    from stream_service.score.engrave import engrave
    from stream_service.score.mei import change_doc
    tk = verovio.toolkit()
    for doc in (change_doc(3, (3, 4)), change_doc(-2), change_doc(0, (6, 8))):
        meta, ink = engrave(tk, {"id": 1, "mei": doc, "space_px": 14.0})
        assert meta["ok"] and meta["measures"] and len(ink) == meta["w"] * meta["h"]
