import math

import numpy as np
import pytest

from piano_render import score


def test_events_sorted_and_normalised(midi):
    path = midi([
        (0.5, "on", 60, 100),
        (0.2, "cc", 64, 90),
        (0.9, "on", 60, 0),         # note-on velocity 0 = note-off, release velocity 64
        (1.0, "on", 62, 80),
        (1.4, "off", 62, 30),       # real release velocity kept
        (1.5, "on", 110, 70),       # outside 21..108: dropped
        (1.6, "cc", 7, 100),        # not a pedal: dropped
        (1.7, "cc", 67, 64),
        (1.8, "cc", 66, 127),
        (2.0, "on", 64, 50),
        (2.5, "off", 64, 0),        # note-off velocity 0 = not recorded: 64
    ])
    perf = score.load_midi(path)
    ts = [e.t_s for e in perf.events]
    assert ts == sorted(ts)
    kinds = [(e.kind, e.num, e.value) for e in perf.events]
    assert kinds == [("cc", 64, 90), ("on", 60, 100), ("off", 60, 64), ("on", 62, 80), ("off", 62, 30),
                     ("cc", 67, 64), ("cc", 66, 127), ("on", 64, 50), ("off", 64, 64)]
    assert perf.dropped == 1
    assert perf.duration_s == pytest.approx(2.5)
    assert perf.first_note_s == pytest.approx(0.5)
    assert perf.events[0].t_s == pytest.approx(0.2, abs=1e-9)
    notes = perf.notes()
    assert [(n.key, round(n.t_on, 3), round(n.t_off, 3), n.velocity, n.release_velocity) for n in notes] == [
        (60, 0.5, 0.9, 100, 64), (62, 1.0, 1.4, 80, 30), (64, 2.0, 2.5, 50, 64)]


def test_channel_filter(tmp_path):
    import mido
    mf = mido.MidiFile(type=0, ticks_per_beat=1000)
    tr = mido.MidiTrack()
    mf.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=0))
    tr.append(mido.Message("note_on", note=60, velocity=90, channel=0, time=100))
    tr.append(mido.Message("note_on", note=72, velocity=90, channel=1, time=100))
    p = tmp_path / "ch.mid"
    mf.save(str(p))
    assert [e.num for e in score.load_midi(p).events] == [60, 72]
    assert [e.num for e in score.load_midi(p, channels=[1]).events] == [60]
    assert [e.num for e in score.load_midi(p, channels=[2]).events] == [72]
    with pytest.raises(ValueError):
        score.load_midi(p, channels=[0])


def test_damper_lift_curve():
    assert score.damper_lift(0) == 0.0
    assert score.damper_lift(31) == pytest.approx(0.0, abs=0.01)      # dead zone: first quarter of travel
    assert score.damper_lift(63.5) == pytest.approx(0.5, abs=1e-9)
    assert score.damper_lift(95.25) == pytest.approx(1.0)
    assert score.damper_lift(127) == 1.0


def test_controls_state_on_the_grid():
    perf = score.from_events([
        (0.10, "on", 60, 100), (0.30, "cc", 64, 127), (0.50, "off", 60, 64),
        (0.60, "on", 62, 90), (0.70, "cc", 66, 100),          # sostenuto latches 62 (down), not 60 (up)
        (0.80, "off", 62, 64), (0.85, "on", 64, 80),           # 64 struck after the latch: not latched
        (0.90, "cc", 67, 127), (1.00, "cc", 66, 10), (1.10, "cc", 64, 0),
    ])
    c = perf.controls(100.0, duration_s=1.5)
    assert c.n == 151 and c.t[-1] == pytest.approx(1.5)
    k = lambda key: key - score.KEY_LO  # noqa: E731
    row = lambda t: int(round(t * 100))  # noqa: E731
    assert not c.key_down[row(0.09), k(60)] and c.key_down[row(0.10), k(60)]     # an event at t_i counts at row i
    assert c.key_down[row(0.49), k(60)] and not c.key_down[row(0.50), k(60)]
    assert c.damper_lift[row(0.29)] == 0.0 and c.damper_lift[row(0.30)] == 1.0
    assert c.damper_lift[row(1.10)] == 0.0
    assert c.sostenuto[row(0.75), k(62)] and not c.sostenuto[row(0.75), k(60)]
    assert c.sostenuto[row(0.95), k(62)] and not c.sostenuto[row(0.95), k(64)]   # held by the latch after release
    assert not c.sostenuto[row(1.0), k(62)]                                       # CC66 fell below 64
    assert c.una_corda[row(0.89)] == 0.0 and c.una_corda[row(0.90)] == pytest.approx(1.0)
    assert c.undamped.sum() == 108 - 89 + 1 and c.undamped[k(89)] and not c.undamped[k(88)]
    assert math.isinf(c.struck[row(0.05), k(60)])
    assert c.struck[row(0.40), k(60)] == pytest.approx(0.30, abs=1e-5)
    assert c.struck.dtype == np.float32
    free = c.free()
    assert free[row(0.95), k(62)] and free[row(0.95), k(100)] and not free[row(0.95), k(60)]
    tau = c.damping_tau()
    # damper fully up at 0.95 s: a released, unlatched key rings like a free one
    assert tau[row(0.95), k(60)] == pytest.approx(score.TAU_SUS[k(60)], rel=1e-6)
    # damper down at 1.2 s: 60 damps with DAMP_S; the undamped treble still rings
    assert tau[row(1.2), k(60)] == pytest.approx(score.DAMP_S, rel=1e-6)
    assert tau[row(1.2), k(100)] == pytest.approx(score.TAU_SUS[k(100)], rel=1e-6)


def test_constants_match_show_service():
    # the copy must stay the same numbers as show_service/midi.py (read as text: show_service is not imported)
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "show_service" / "midi.py")
    if not src.exists():
        pytest.skip("show_service not present")
    text = src.read_text()
    for name in ("VEL_GAMMA", "TAU_A0_S", "TAU_HALVING_SEMIS", "TAU_MIN_S", "DAMP_S", "UNDAMPED_FROM",
                 "SOSTENUTO_ON", "SOFT_SCALE"):
        line = next(ln for ln in text.splitlines() if ln.startswith(name + " ") or ln.startswith(name + "="))
        value = float(line.split("=", 1)[1].split("#")[0])
        assert getattr(score, name) == value, name


def test_test_piano_files_load():
    from pathlib import Path
    d = Path(__file__).resolve().parents[2] / "config" / "live" / "test-piano"
    mids = sorted(d.glob("*.mid"))
    if not mids:
        pytest.skip("no test pieces")
    perf = score.load_midi(next(m for m in mids if "Nocturne" in m.name) if any("Nocturne" in m.name for m in mids)
                           else mids[0])
    assert perf.duration_s > 30
    assert any(e.kind == "cc" and e.num == 64 and 0 < e.value < 127 for e in perf.events)   # continuous pedal
    c = perf.controls(50.0)
    assert c.key_down.any() and (c.damper_lift > 0).any()
