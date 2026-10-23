"""sheet.py: a whole piece, not in real time -> one MEI document Verovio lays out in pages and prints to a PDF.  The
quick pass: a sequenced file read off its own grid (sheet_grid), a performance's beat tracked (sheet_beats); the live
builder's own loop (transcribe, its fallback clock here: the networks are the live score's, tested there)."""
from __future__ import annotations

import pytest

from stream_service.piano_replay import NOTE_OFF, NOTE_ON
from stream_service.score.quantize import Bar
import random
import struct

from stream_service.score import sheet_beats, sheet_grid
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
    bars = transcribe(scale_events())
    music = [b for b, _ in bars if not b.marker]
    assert len(music) >= 3 * 7 and sum(b.marker for b, _ in bars) == 3      # three phrases, each ended
    mei = score_doc(bars, title="Scales & arpeggios", composer="Test")
    assert mei.count("<measure ") == len(music) and mei.count("<sb/>") == 2 and "Scales &amp; arpeggios" in mei
    verovio = pytest.importorskip("verovio")
    from stream_service.score.sheet_pages import pages
    got = pages(mei, 420, 760, 30)
    assert len(got) >= 2 and all(p.startswith("<svg") for p in got)
    tk = verovio.toolkit()
    assert tk.loadData(mei)
    pytest.importorskip("playwright")
    from stream_service.score.sheet_pages import pdf
    data = pdf(mei, "a4", title="Scales")
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page") - data.count(b"/Type /Pages") >= 1


def test_dynamics_hold() -> None:
    def bar(d):
        return Bar(index=0, staves=([], []), t_start=0, t_end=1, dynamic=d)
    seq = [bar(x) for x in ("p", "pp", "p", None, "f", None, None, "mf")]
    assert _dynamics(seq) == ["p", None, None, None, "f", None, None, "mf"]


def smf(tracks: list, ppq: int = 480, meta: bytes = b"") -> bytes:
    """A type-1 MIDI file: ``tracks`` of [(tick, pitch, length ticks, velocity)], ``meta`` events at tick 0 on the
    first."""
    def vl(v):
        out = [v & 0x7F]
        while v > 0x7F:
            v >>= 7
            out.insert(0, (v & 0x7F) | 0x80)
        return bytes(out)
    chunks = []
    for k, notes in enumerate(tracks):
        ev = sorted([(t, 1, bytes([0x90, p, v])) for t, p, n, v in notes]
                    + [(t + n, 0, bytes([0x80, p, 0])) for t, p, n, v in notes])
        body, last = (meta if k == 0 else b""), 0
        for t, _o, b in ev:
            body += vl(t - last) + b
            last = t
        body += b"\x00\xff\x2f\x00"
        chunks.append(b"MTrk" + struct.pack(">I", len(body)) + body)
    return b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), ppq) + b"".join(chunks)


def waltz(bars: int = 16, ppq: int = 480, jitter: float = 0.0) -> list:
    """G major 3/4: bass G2/D3 on each downbeat, chords on 2 and 3 (left hand), a melody in quarters (right)."""
    rng = random.Random(1)
    j = lambda: int(rng.gauss(0, jitter * ppq)) if jitter else 0            # noqa: E731
    lh, rh = [], []
    mel = [67, 71, 74, 72, 71, 69, 67, 66, 67, 69, 71, 72]
    for b in range(bars):
        t = b * 3 * ppq
        lh.append((max(0, t + j()), 43 if b % 2 == 0 else 50, ppq, 96))
        for beat in (1, 2):
            for p in (59, 62):
                lh.append((t + beat * ppq + j(), p, ppq // 2, 52))
        for beat in range(3):
            rh.append((max(0, t + beat * ppq + j()), mel[(3 * b + beat) % len(mel)], ppq - 20, 70))
    return [rh, lh]


def test_sequenced_file_read_off_its_grid() -> None:
    meta = b"\x00\xff\x58\x04\x03\x02\x18\x08" + b"\x00\xff\x59\x02\x01\x00"     # 3/4, G major
    mf = sheet_grid.read_ticks(smf(waltz()))
    assert sheet_grid.sequenced(mf)
    mf = sheet_grid.read_ticks(smf(waltz(), meta=meta))
    bars = sheet_grid.bars(mf)
    assert len(bars) == 16 and {b.meter for b, _ in bars} == {(3, 4)} and {f for _, f in bars} == {1}
    first = bars[0][0]
    assert [e.pitches for e in first.staves[0]] == [[67], [71], [74]]          # the melody on the treble staff
    assert first.staves[1][0].pitches == [43]                                  # the bass on the bass staff


def test_performance_beat_tracked() -> None:
    mf = sheet_grid.read_ticks(smf(waltz(32, jitter=0.03)))
    assert not sheet_grid.sequenced(mf)
    bars = sheet_beats.bars(mf)
    music = [b for b, _ in bars]
    assert {b.meter for b in music} == {(3, 4)} and 30 <= len(music) <= 34
    downbeat_bass = sum(1 for b in music if b.staves[1] and b.staves[1][0].pitches and
                        b.staves[1][0].start == 0 and min(b.staves[1][0].pitches) in (43, 50))
    assert downbeat_bass >= 0.8 * len(music)                                   # the bar lines on the downbeats
