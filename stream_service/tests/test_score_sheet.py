"""sheet.py: a whole piece, not in real time -> one MEI document Verovio lays out in pages (the fallback 4/4 clock
here: the analysis worker is the live score's, tested there)."""
from __future__ import annotations

import pytest

from stream_service.piano_replay import NOTE_OFF, NOTE_ON
from stream_service.score.quantize import Bar
from stream_service.score.sheet import _dynamics, score_doc, transcribe

MAJOR = (0, 2, 4, 5, 7, 9, 11)


def scale_events(tonic: int = 60, octaves: int = 2, beat: float = 0.5, loops: int = 3, gap: float = 6.0) -> list:
    """A major scale up and down in quarters, ``loops`` times with gaps long enough to end a phrase."""
    up = [tonic + 12 * (i // 7) + MAJOR[i % 7] for i in range(7 * octaves + 1)]
    run = up + up[-2::-1]
    ev, t = [], 0.0
    for _ in range(loops):
        for i, p in enumerate(run):
            on = t + i * beat
            ev += [(on, NOTE_ON, p, 70), (on + beat * 0.9, NOTE_OFF, p, 40), (on, NOTE_ON, p - 24, 60),
                   (on + beat * 0.9, NOTE_OFF, p - 24, 40)]
        t += len(run) * beat + gap
    return sorted(ev)


def test_whole_piece_to_pages() -> None:
    bars = transcribe(scale_events(), [])
    music = [b for b, _ in bars if not b.marker]
    assert len(music) >= 3 * 7 and sum(b.marker for b, _ in bars) == 3      # three phrases, each ended
    mei = score_doc(bars, title="Scales & arpeggios", composer="Test")
    assert mei.count("<measure ") == len(music) and mei.count("<sb/>") == 2 and "Scales &amp; arpeggios" in mei
    verovio = pytest.importorskip("verovio")
    from stream_service.score.sheet import pages
    got = pages(mei, 420, 760, 30)
    assert len(got) >= 2 and all(p.startswith("<svg") for p in got)
    tk = verovio.toolkit()
    assert tk.loadData(mei)


def test_dynamics_hold() -> None:
    def bar(d):
        return Bar(index=0, staves=([], []), t_start=0, t_end=1, dynamic=d)
    seq = [bar(x) for x in ("p", "pp", "p", None, "f", None, None, "mf")]
    assert _dynamics(seq) == ["p", None, None, None, "f", None, None, "mf"]
