"""stream_service/score: the quantiser, MEI writer and (when Verovio is installed) the engraving worker."""
from __future__ import annotations

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
