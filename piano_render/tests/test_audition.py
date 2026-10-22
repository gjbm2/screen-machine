"""piano_render.audition: the excerpt cutter (snapping, the pedal carried in, the ring-out at the end, timing kept to
0.1 ms), the tail trim and the 16-bit export. No rendering, no sound."""

import numpy as np
import pytest

from piano_render import audition as A
from piano_render import score

from .conftest import SR, write_midi


def _piece(tmp_path):
    ev = [(1.0, "cc", 64, 100), (1.5, "cc", 67, 50),
          (2.0, "on", 40, 70), (2.5, "off", 40, 64)]                 # a phrase long before the start: dropped
    t = [5.0, 6.0]                                                   # 2 s gap before 8.0: the start snaps there
    t += [8.0 + 0.2 * i for i in range(101)]                         # 8.0 .. 28.0 every 0.2 s
    t += [29.0, 29.2, 29.4]                                          # 1 s gap before 29.0: the end snaps there
    for x in t:
        if x == 28.0:
            continue                                                 # the held note below starts at 28.0 instead
        ev += [(x, "on", 60, 80), (x + 0.1, "off", 60, 64)]
    ev += [(7.8, "cc", 64, 127)]                                     # a pedal change in the lead-in: kept
    ev += [(28.0, "on", 48, 60), (29.5, "off", 48, 64)]              # still down at the cut: released at the end
    ev += [(28.5, "cc", 66, 127)]                                    # sostenuto down at the cut: lifted at the end
    return write_midi(tmp_path / "piece.mid", ev)


def test_cut_excerpt_snaps_carries_pedals_and_rings_out(tmp_path):
    src = _piece(tmp_path)
    info = A.cut_excerpt(src, tmp_path / "x.mid", 8.5, 29.0)
    assert info["first_note_s"] == 8.0 and info["from_s"] == pytest.approx(8.0 - A.LEAD_S)
    assert info["cut_at_s"] == 29.0 and info["start_gap_s"] == 2.0 and info["end_gap_s"] == pytest.approx(1.0)
    assert info["carried_in"] == {64: 100, 67: 50}
    w0 = info["from_s"]
    tend = 29.0 - w0 + A.HOLD_S
    assert info["excerpt_s"] == pytest.approx(tend)
    evs = score.load_midi(tmp_path / "x.mid").events
    # pedals carried in at 0, in CC order
    assert [(e.t_s, e.num, e.value) for e in evs[:2]] == [(0.0, 64, 100), (0.0, 67, 50)]
    ons = [e for e in evs if e.kind == "on"]
    assert ons[0].t_s == pytest.approx(A.LEAD_S, abs=1e-4) and ons[0].num == 60
    assert not any(e.num == 40 for e in evs if e.kind in ("on", "off"))
    assert any(e.kind == "cc" and e.num == 64 and e.value == 127 and e.t_s == pytest.approx(7.8 - w0, abs=1e-4)
               for e in evs)
    # every kept event at its own time minus the start (0.1 ms ticks)
    orig = {(round(e.t_s, 4), e.kind, e.num) for e in score.load_midi(src).events}
    for e in evs:
        if 0 < e.t_s < tend - 1e-6:
            assert (round(e.t_s + w0, 4), e.kind, e.num) in orig, e
    # nothing from the cut on, then the ring-out: the held key released and every pedal lifted HOLD_S later
    assert not any(e.kind == "on" and e.t_s + w0 >= 29.0 - 1e-6 for e in evs)
    end = [e for e in evs if e.t_s == pytest.approx(tend, abs=1e-4)]
    assert ("off", 48) in {(e.kind, e.num) for e in end}
    assert {(e.num, e.value) for e in end if e.kind == "cc"} == {(64, 0), (66, 0), (67, 0)}
    assert max(e.t_s for e in evs) == pytest.approx(tend, abs=1e-4)
    # the same cut again leaves the file alone (the render cache keys on its bytes)
    before = (tmp_path / "x.mid").stat().st_mtime_ns
    A.cut_excerpt(src, tmp_path / "x.mid", 8.5, 29.0)
    assert (tmp_path / "x.mid").stat().st_mtime_ns == before


def test_cut_drops_notes_in_the_lead_in(tmp_path):
    # dense playing: the largest gap near the target (0.2 s at 10.0) is shorter than the 0.3 s lead-in, so two
    # notes start inside the lead-in; they (and their note-offs) belong to the phrase before and are dropped
    ev = []
    for x in [9.0 + 0.1 * i for i in range(9)] + [10.0 + 0.1 * i for i in range(300)]:
        k = 60 + round(x * 10) % 12
        ev += [(round(x, 3), "on", k, 80), (round(x, 3) + 0.25, "off", k, 64)]
    src = write_midi(tmp_path / "dense.mid", ev)
    info = A.cut_excerpt(src, tmp_path / "d.mid", 10.0, 35.0)
    assert info["first_note_s"] == 10.0 and info["from_s"] == pytest.approx(9.7)
    evs = score.load_midi(tmp_path / "d.mid").events
    ons = [e for e in evs if e.kind == "on"]
    assert ons[0].t_s == pytest.approx(0.3, abs=1e-4)
    keys_down = set()
    for e in evs:                                    # every note-off belongs to a note-on in the excerpt
        if e.kind == "on":
            keys_down.add(e.num)
        elif e.kind == "off":
            assert e.num in keys_down
            keys_down.discard(e.num)


def test_cut_from_the_start_keeps_the_pieces_timing(tmp_path):
    src = _piece(tmp_path)
    info = A.cut_excerpt(src, tmp_path / "y.mid", 0, 29.0)
    assert info["from_s"] == 0.0 and info["first_note_s"] == 2.0 and info["carried_in"] == {}
    a = [(round(e.t_s, 4), e.kind, e.num, e.value) for e in score.load_midi(src).events if e.t_s < 29.0 - 1e-6]
    b = [(round(e.t_s, 4), e.kind, e.num, e.value) for e in score.load_midi(tmp_path / "y.mid").events
         if e.t_s < 29.0 - 1e-6]
    assert a == b
    # no note near the end target: everything to the piece's end, then the ring-out
    info = A.cut_excerpt(src, tmp_path / "z.mid", 0, 500.0)
    assert info["cut_at_s"] is None and info["excerpt_s"] == pytest.approx(29.5 + A.HOLD_S)


def test_trim_tail_and_export(tmp_path):
    t = np.arange(4 * SR) / SR
    x = np.zeros((6 * SR, 2), np.float32)
    x[:4 * SR] = (0.3 * np.sin(2 * np.pi * 440 * t))[:, None]
    x[4 * SR:] = 1e-5                                                # near-silent hiss: -100 dBFS
    y, trimmed = A.trim_tail(x, SR)
    assert y.shape[0] == 4 * SR + SR // 2 and trimmed == pytest.approx(1.5)
    assert abs(y[-1, 0]) < 1e-6 and np.array_equal(y[:3 * SR], x[:3 * SR])
    import soundfile as sf
    src, dst = tmp_path / "in.flac", tmp_path / "out.flac"
    sf.write(str(src), x, SR, subtype="PCM_24")
    A.export_flac(src, dst, title="T", artist="A", tracknumber=101, comment="c")
    f = sf.SoundFile(str(dst))
    assert (f.subtype, f.samplerate, f.title, f.artist, f.album, f.tracknumber) == \
        ("PCM_16", SR, "T", "A", A.ALBUM, "101")
    z = f.read(dtype="float32", always_2d=True)
    m = A.measure(z, SR, 0.0, 4.0)
    assert m["clipped_samples"] == 0 and m["longest_quiet_s"] == 0.0 and m["duration_s"] == 4.5
    assert m["sp"] == pytest.approx(20 * np.log10(0.3), abs=0.05)
    assert not [p for p in tmp_path.iterdir() if ".tmp" in p.name]
    # reproducible: the dither is seeded by the file name
    for d in ("d1", "d2"):
        (tmp_path / d).mkdir()
        A.export_flac(src, tmp_path / d / "same.flac", title="T", artist="A")
    assert np.array_equal(sf.read(str(tmp_path / "d1/same.flac"))[0], sf.read(str(tmp_path / "d2/same.flac"))[0])
