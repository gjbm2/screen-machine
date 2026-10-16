"""stream_service/score: the quantiser, MEI writer and (when Verovio is installed) the engraving worker."""
from __future__ import annotations

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
