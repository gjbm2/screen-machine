"""sound_view.py's note model and piano_replay.py's test piano (no GPU)."""
from __future__ import annotations

import math
import struct
import time

import numpy as np
import pytest

from stream_service.piano_replay import PHRASES, TestPiano, phrase, read_midi
from stream_service.sound_view import (AGE_OFF, AGE_ON, DAMP_S, DAMPER, LEVEL, STALE_S, STUCK_S, TAU_SUS,
                                       VEL, VEL_GAMMA, SoundView)

T0 = 1_000_000.0


def note(seq, t, pitch, on=True, vel=100, sess="s1"):
    return {"type": "music.note", "data": {"seq": seq, "t_ms": t, "on": on, "pitch": pitch, "vel": vel,
                                           "source": "test", "sess": sess}}


def pedal(seq, t, name, value, sess="s1"):
    return {"type": "music.pedal", "data": {"seq": seq, "t_ms": t, "pedal": name, "value": value, "sess": sess}}


def snap(seq, t, keys, pedals=None, sess="s1"):
    return {"type": "music.notes", "data": {"seq": seq, "t_ms": t, "keys": keys, "present": True,
                                            "pedals": pedals or {}, "source": "live", "sess": sess}}


def feed(view, *msgs):
    for m in msgs:
        view.on_message(m)


def run(view, t_from, t_to, step=1000 / 60):
    t = t_from
    while t < t_to:
        view.update(t)
        t += step
    return view.update(t_to)


def g(vel=100):
    return (vel / 127) ** VEL_GAMMA


def test_held_note_decays_with_its_sustain_time():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 60))
    f = v.update(T0)
    assert f.notes[60, LEVEL] == pytest.approx(g(), rel=1e-6)
    assert f.notes[60, AGE_OFF] == -1.0 and f.notes[60, VEL] == pytest.approx(100 / 127)
    f = run(v, T0, T0 + 1000)
    assert f.notes[60, LEVEL] == pytest.approx(g() * math.exp(-1.0 / TAU_SUS[60]), rel=1e-4)
    assert f.notes[60, AGE_ON] == pytest.approx(1.0)
    assert f.notes[61, AGE_ON] == np.inf and f.notes[61, LEVEL] == 0.0


def test_release_damps_fast_unless_the_damper_is_down():
    for damper, tau in ((0.0, DAMP_S), (1.0, TAU_SUS[60])):
        v = SoundView()
        v.update(T0)
        feed(v, pedal(1, T0, "damper", damper), note(2, T0, 60), note(3, T0 + 500, 60, on=False, vel=30))
        f = run(v, T0, T0 + 800)
        expect = g() * math.exp(-0.5 / TAU_SUS[60]) * math.exp(-0.3 / tau)
        assert f.notes[60, LEVEL] == pytest.approx(expect, rel=1e-3)
        assert f.notes[60, AGE_OFF] == pytest.approx(0.3)


def test_half_pedal_is_in_between():
    lv = []
    for damper in (0.0, 0.5, 1.0):
        v = SoundView()
        v.update(T0)
        feed(v, pedal(1, T0, "damper", damper), note(2, T0, 48), note(3, T0 + 100, 48, on=False))
        lv.append(run(v, T0, T0 + 600).notes[48, LEVEL])
    assert lv[0] < lv[1] < lv[2]


def test_top_keys_have_no_dampers():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 100), note(2, T0 + 100, 100, on=False), note(3, T0, 80), note(4, T0 + 100, 80, on=False))
    f = run(v, T0, T0 + 600)
    assert f.notes[100, LEVEL] == pytest.approx(g() * math.exp(-0.6 / TAU_SUS[100]), rel=1e-3)
    assert f.notes[80, LEVEL] < 0.05 * f.notes[100, LEVEL]


def test_sostenuto_latches_only_the_keys_held_when_it_goes_down():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 36), pedal(2, T0 + 100, "sostenuto", 1.0), note(3, T0 + 200, 36, on=False),
         note(4, T0 + 300, 72), note(5, T0 + 400, 72, on=False))
    f = run(v, T0, T0 + 1500)
    assert f.notes[36, LEVEL] == pytest.approx(g() * math.exp(-1.5 / TAU_SUS[36]), rel=1e-3)
    assert f.notes[72, LEVEL] == 0.0                           # damped long ago
    feed(v, pedal(6, T0 + 1500, "sostenuto", 0.0))
    f = run(v, T0 + 1500, T0 + 2500)
    assert f.notes[36, LEVEL] < 1e-3


def test_late_events_match_events_known_in_time():
    """A release applied 300 ms after it happened ends where a model told on time would be."""
    on_time, late = SoundView(), SoundView()
    for v in (on_time, late):
        v.update(T0)
        feed(v, note(1, T0, 60))
    run(on_time, T0, T0 + 200)
    feed(on_time, note(2, T0 + 200, 60, on=False))
    a = run(on_time, T0 + 200, T0 + 900)
    run(late, T0, T0 + 500)
    feed(late, note(2, T0 + 200, 60, on=False))
    b = run(late, T0 + 500, T0 + 900)
    assert b.notes[60, LEVEL] == pytest.approx(a.notes[60, LEVEL], rel=1e-3)


def test_onsets_and_releases_are_reported_once():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0 - 50, 64, vel=127), note(2, T0 - 20, 64, on=False, vel=50))
    f = v.update(T0)
    assert [(p, round(vel, 3)) for p, vel, _ in f.onsets] == [(64, 1.0)]
    assert f.onsets[0][2] == pytest.approx(0.05)
    assert [p for p, *_ in f.releases] == [64]
    assert v.update(T0 + 16).onsets == [] and v.frame.releases == []


def test_snapshot_heals_a_lost_note_off_and_a_lost_note_on():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 60), note(2, T0, 64))
    v.update(T0 + 100)
    feed(v, snap(5, T0 + 150, [[64, 100, T0], [67, 90, T0 + 120]]))    # 60 went up, 67 went down: both lost
    f = v.update(T0 + 200)
    assert not v.held[60] and v.held[64] and v.held[67]
    assert f.notes[67, LEVEL] > 0 and f.notes[67, AGE_ON] == pytest.approx(0.08)
    assert f.lost == 3 and [p for p, *_ in f.onsets] == [67]       # seqs 3-5 never arrived


def test_stale_snapshot_and_duplicates_are_ignored():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 60), note(2, T0 + 10, 60, on=False), snap(1, T0 + 5, [[60, 100, T0]]), note(2, T0, 61))
    v.update(T0 + 50)
    assert not v.held[60] and not v.held[61]


def test_a_new_session_releases_held_keys():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 60), note(1, T0 + 100, 62, sess="s2"))
    v.update(T0 + 200)
    assert not v.held[60] and v.held[62] and v.seq == 1


def test_present_goes_stale_and_stuck_keys_are_released():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 21))
    assert v.update(T0 + 10).present
    f = run(v, T0, T0 + (STALE_S + 2) * 1000, step=100)
    assert v.held[21]                                          # still held: under STUCK_S
    assert f.notes[21, LEVEL] > 0.02 and f.present             # A0 still rings, so present
    f = run(v, T0 + (STALE_S + 2) * 1000, T0 + (STUCK_S + 1) * 1000, step=100)
    assert not v.held[21]
    f = run(v, T0 + (STUCK_S + 1) * 1000, T0 + (STUCK_S + 4) * 1000, step=100)
    assert not f.present


def test_hot_note_guard():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 67, vel=50), note(2, T0 + 100, 67, on=False), note(3, T0 + 120, 67, vel=127))
    f = v.update(T0 + 150)
    assert f.notes[67, VEL] == pytest.approx(50 / 127 + 0.25) and f.capped == 1


def test_harmony_features_and_fallbacks():
    v = SoundView()
    v.update(T0)
    spec = [i / 31 for i in range(32)]
    feed(v, {"type": "music.harmony", "data": {"chroma12": [1] + [0] * 11, "key": "C", "mode": "major",
                                                "tension": 0.2, "chord_change": 0.9, "register": 0.5,
                                                "spread": 0.3}},
         {"type": "music.features", "data": {"spectrum32": spec}})
    f = v.update(T0 + 10)
    assert f.key == "C" and f.chroma[0] == 1.0 and f.tension == pytest.approx(0.2)
    assert f.history_row == 0 and f.history[0, 31] == pytest.approx(1.0)
    feed(v, {"type": "show.state", "data": {"global": {"music": {"chroma12": [0, 1] + [0] * 10,
                                                                 "spectrum32": [0.0] * 32}}}})
    f = v.update(T0 + 20)
    assert f.chroma[0] == 1.0 and f.history_row == 0          # fresh music.* types win
    f = run(v, T0 + 20, T0 + (STALE_S + 1) * 1000, step=200)
    assert f.key is None and f.chroma.sum() == 0.0


def test_malformed_messages_are_counted_not_raised():
    v = SoundView()
    v.update(T0)
    feed(v, {"type": "music.note", "data": {"seq": 1, "t_ms": "x", "pitch": 60, "vel": 1, "on": True}},
         {"type": "music.note", "data": {"seq": 2, "t_ms": T0, "pitch": 200, "vel": 1, "on": True}},
         {"type": "music.pedal", "data": {"seq": 3, "t_ms": T0, "pedal": "una corda", "value": 1}},
         {"type": "music.notes", "data": {"seq": 4, "t_ms": T0, "keys": [[1]]}},
         {"type": "music.features", "data": None}, {"type": "whatever"}, {})
    f = v.update(T0 + 10)
    assert f.bad == 5 and not v.held.any()


def test_tap_for_the_loop_thread():
    v = SoundView()
    v.update(T0)
    feed(v, note(1, T0, 60, vel=127), pedal(2, T0, "damper", 0.7))
    v.update(T0 + 100)
    tap = v.tap()
    assert tap.online and tap.pedal == pytest.approx(0.7)
    assert [(p, round(vel, 2)) for _, p, vel in tap.onsets] == [(60, 1.0)]
    assert tap.onsets[0][0] == pytest.approx(T0 / 1000) and tap.t_last == pytest.approx(T0 / 1000)
    assert tap.amps[60] == pytest.approx(v.frame.notes[60, LEVEL])
    assert v.tap().onsets == []


def test_update_is_cheap():
    v = SoundView()
    piano = TestPiano("medley", start_ms=T0)
    t, n, worst = T0, 0, 0.0
    while t < T0 + 20_000:
        feed(v, *piano.poll(t))
        a = time.perf_counter()
        v.update(t)
        worst = max(worst, time.perf_counter() - a)
        t += 1000 / 60
        n += 1
    assert worst < 0.005                                       # well under a millisecond in practice


# -- the test piano -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", PHRASES)
def test_phrases_are_well_formed(name):
    ev = phrase(name)
    assert ev and all(0 <= t for t, *_ in ev) and ev == sorted(ev, key=lambda e: e[0])
    ons = sum(1 for _, st, *_ in ev if st == 0x90)
    offs = sum(1 for _, st, *_ in ev if st == 0x80)
    assert ons == offs


def test_test_piano_drives_the_view():
    v = SoundView()
    piano = TestPiano("chords", start_ms=T0)
    t, onsets, seqs = T0, 0, []
    while not piano.done:
        for m in piano.poll(t):
            if m["type"] != "music.notes":
                seqs.append(m["data"]["seq"])
            assert m["data"]["source"] == "test" and m["data"]["sess"] == piano.sess
            v.on_message(m)
        f = v.update(t)
        onsets += len(f.onsets)
        t += 1000 / 60
    assert seqs == list(range(1, len(seqs) + 1))
    assert onsets == sum(1 for _, st, *_ in phrase("chords") if st == 0x90)
    assert v.pedal[DAMPER] == 0.0 and v.frame.lost == 0 and v.frame.bad == 0


def test_test_piano_loops():
    piano = TestPiano("repeats", start_ms=T0, loop=True)
    n = sum(len([m for m in piano.poll(t) if m["type"] == "music.note" and m["data"]["on"]])
            for t in np.arange(T0, T0 + (2 * piano.length_s + 3.0) * 1000 + 100, 10))   # two passes
    assert n == 48 and not piano.done


def _smf(tpq=480, tempo_us=500000):
    def var(n):
        out = [n & 0x7F]
        n >>= 7
        while n:
            out.insert(0, (n & 0x7F) | 0x80)
            n >>= 7
        return bytes(out)
    trk = (var(0) + b"\xff\x51\x03" + tempo_us.to_bytes(3, "big")
           + var(0) + b"\x90\x3c\x50"            # C4 on
           + var(tpq) + b"\x40\x00"               # running status: E4 vel 0 (= off)... pitch 0x40
           + var(0) + b"\xb0\x40\x7f"             # damper down
           + var(tpq) + b"\x80\x3c\x20"           # C4 off
           + var(0) + b"\xc0\x05"                 # a program change (skipped)
           + var(0) + b"\xff\x2f\x00")
    return b"MThd" + struct.pack(">IHHH", 6, 0, 1, tpq) + b"MTrk" + struct.pack(">I", len(trk)) + trk


def test_read_midi():
    ev = read_midi(_smf(tempo_us=1_000_000))                     # 60 bpm: a quarter is 1 s
    assert ev == [(0.0, 0x90, 60, 80), (1.0, 0x80, 64, 0), (1.0, 0xB0, 64, 127), (2.0, 0x80, 60, 32)]
    with pytest.raises(ValueError):
        read_midi(b"RIFF....")


def test_library_lists_phrases_then_files(tmp_path):
    from stream_service.piano_replay import PHRASE_LABELS, library, resolve
    (tmp_path / "gymnopedie_no_1.mid").write_bytes(_smf())
    (tmp_path / "notes.txt").write_text("x")
    lib = library(tmp_path)
    assert [x["id"] for x in lib][:len(PHRASE_LABELS)] == list(PHRASE_LABELS)
    assert lib[-1] == {"id": "file:gymnopedie_no_1.mid", "label": "Gymnopedie no 1", "kind": "file"}
    assert resolve("file:gymnopedie_no_1.mid", tmp_path).loop
    assert resolve("chords", tmp_path).events
    for bad in ("file:../etc/passwd", "file:nope.mid", "jazz"):
        with pytest.raises(ValueError):
            resolve(bad, tmp_path)


def test_owner_library_tags_put_scenes_in_sets():
    from stream_service.server import with_owner_sets
    cat = {"scenes": {"a-1": {"source": "a", "tags": {"modes": ["piano"]}}, "b-1": {"source": "b", "tags": None},
                      "c-1": {"source": "c", "tags": {"moods": ["calm"]}}}, "effects": {}}
    out = with_owner_sets(cat, {"scene/b": {"tags": ["Piano", "late night"]}, "scene/a": {"tags": ["piano"]},
                                "effect/x": {"tags": ["piano"]}, "scene/c": {"stars": 4}})
    assert out["scenes"]["a-1"]["tags"]["modes"] == ["piano"]
    assert out["scenes"]["b-1"]["tags"]["modes"] == ["piano", "late night"]
    assert out["scenes"]["c-1"]["tags"] == {"moods": ["calm"]}
    assert cat["scenes"]["b-1"]["tags"] is None                    # the presets themselves are untouched
    assert with_owner_sets(cat, {}) is cat
