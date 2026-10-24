"""Punch-in (show_service/punch.py) and Stop for a piece (recorder.py): a take replaces one section of a piece's MIDI
as a new recording; the piece's own file is never touched. tmp folders only; nothing is played or sent."""
from __future__ import annotations

import asyncio
import json

import mido
import pytest

from show_service import punch
from show_service.recorder import Recorder
from show_service.tests.test_recorder import SETTINGS, Clock, Kiosk, ev_cc, ev_off, ev_on

PPQ = 480


def piece(notes, *, pedals=(), tempos=((0, 500_000),), ppq=PPQ, title="Étude") -> bytes:
    """An SMF type 1 piece: ``notes`` [(on_tick, off_tick, pitch, vel)], ``pedals`` [(tick, cc, value)],
    ``tempos`` [(tick, us a beat)], a 3/4 time signature."""
    import io
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq)
    meta = mido.MidiTrack()
    abs_meta = [(0, mido.MetaMessage("track_name", name=title)), (0, mido.MetaMessage("time_signature", numerator=3,
                                                                                         denominator=4))]
    abs_meta += [(t, mido.MetaMessage("set_tempo", tempo=us)) for t, us in tempos]
    prev = 0
    for t, m in sorted(abs_meta, key=lambda x: x[0]):
        meta.append(m.copy(time=t - prev))
        prev = t
    mid.tracks.append(meta)
    tr = mido.MidiTrack()
    ev = []
    for on, off, p, v in notes:
        ev.append((on, 1, mido.Message("note_on", note=p, velocity=v)))
        ev.append((off, 0, mido.Message("note_off", note=p, velocity=0)))
    for t, cc, v in pedals:
        ev.append((t, 0, mido.Message("control_change", control=cc, value=v)))
    prev = 0
    for t, _o, m in sorted(ev, key=lambda x: (x[0], x[1])):
        tr.append(m.copy(time=t - prev))
        prev = t
    mid.tracks.append(tr)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def read(data: bytes):
    """(ppq, [(tick, msg)] of the one track, notes [(on_tick, off_tick, pitch, vel)], seconds of a tick fn)."""
    import io
    mid = mido.MidiFile(file=io.BytesIO(data))
    assert mid.type == 0 and len(mid.tracks) == 1
    out, t = [], 0
    for m in mid.tracks[0]:
        t += m.time
        out.append((t, m))
    held: dict = {}
    notes = []
    for t, m in out:
        if m.type == "note_on" and m.velocity > 0:
            held.setdefault(m.note, []).append((t, m.velocity))
        elif m.type in ("note_off", "note_on"):
            on, v = held[m.note].pop(0)
            notes.append((on, t, m.note, v))
    assert not any(held.values()), "a note is never released"
    return mid.ticks_per_beat, out, sorted(notes)


def take(onsets, *, at=5000.0, length=0.2, pitch0=72, vel=90, pedals=()):
    """Recorder-style events (host seconds): a note at each onset (seconds after ``at``)."""
    ev, n = [], 0
    for i, s in enumerate(onsets):
        n += 1
        ev.append((at + s, n, 0x90, pitch0 + i, vel))
        n += 1
        ev.append((at + s + length, n, 0x80, pitch0 + i, 0))
    for s, cc, v in pedals:
        n += 1
        ev.append((at + s, n, 0xB0, cc, v))
    return sorted(ev)


# a beat = 0.5 s (480 ticks): 12 beats, a note on each; bars of 3 beats start at 0, 1.5, 3.0, 4.5 s
BEATS = [(i * PPQ, i * PPQ + 400, 48 + i, 60) for i in range(12)]


def test_the_section_is_replaced_and_everything_else_keeps_its_ticks():
    data, rep = punch.splice(piece(BEATS), take([0.0, 0.7, 1.4]), 1.5, 3.0, "Étude (bars 2-2 re-played)")
    ppq, msgs, notes = read(data)
    assert ppq == PPQ
    kept = [n for n in notes if n[2] < 72]
    assert kept == [n for n in BEATS if not 3 <= BEATS.index(n) <= 5]          # same ticks, same everything
    new = [n for n in notes if n[2] >= 72]
    # first onset on the section's first (1.5 s = tick 1440), last on its last (2.5 s = 2400): stretched 1/1.4
    assert [n[0] for n in new] == [1440, 1920, 2400] and [n[3] for n in new] == [90, 90, 90]
    assert rep["replaced"] == 3 and rep["played"] == 3 and rep["left_out"] == 0
    assert rep["stretch"] == pytest.approx(1.0 / 1.4, abs=1e-3) and rep["notes"] == 12
    metas = [m for _t, m in msgs if m.is_meta]
    assert metas[0].type == "track_name" and metas[0].name == "Étude (bars 2-2 re-played)"
    assert [m.type for m in metas].count("track_name") == 1
    assert any(m.type == "time_signature" and m.numerator == 3 for m in metas)


def test_a_bars_first_note_rounded_on_the_way_is_inside_and_the_next_bars_is_not():
    data, rep = punch.splice(piece(BEATS), take([0.0, 1.0]), 1.5004, 3.0004)    # the bar map's 1 ms rounding
    _ppq, _msgs, notes = read(data)
    assert rep["replaced"] == 3
    assert 51 not in [n[2] for n in notes] and 54 in [n[2] for n in notes]


def test_the_tempo_map_places_the_take_and_the_rest_keeps_its_grid():
    # 120 bpm, then 60 bpm from beat 4 (tick 1920 = 2.0 s): beat 4 at 2.0 s, beat 5 at 3.0 s, beat 6 at 4.0 s ...
    tempos = ((0, 500_000), (4 * PPQ, 1_000_000))
    notes = [(i * PPQ, i * PPQ + 100, 48 + i, 60) for i in range(9)]
    data, rep = punch.splice(piece(notes, tempos=tempos), take([0.0, 3.0]), 2.0, 4.0)
    _ppq, msgs, out = read(data)
    assert [m.tempo for _t, m in msgs if m.type == "set_tempo"] == [500_000, 1_000_000]
    new = [n for n in out if n[2] >= 72]
    # the section's onsets are beats 4 and 5 (2.0 and 3.0 s): the take's 0 and 3 s land on their ticks
    assert [n[0] for n in new] == [4 * PPQ, 5 * PPQ] and rep["stretch"] == pytest.approx(1 / 3, abs=1e-3)
    assert [n for n in out if n[2] < 72] == [n for n in notes if n[2] not in (52, 53)]


def test_a_note_held_into_the_section_is_released_there_and_pedals_are_put_back():
    notes = [(0, 4 * PPQ, 40, 50)] + BEATS[3:9]                       # held to 2.0 s, into the section at 1.5 s
    pedals = [(0, 64, 127), (int(2.2 * 2 * PPQ), 64, 0), (int(2.6 * 2 * PPQ), 64, 100)]   # down; up 2.2 s; down 2.6 s
    data, rep = punch.splice(piece(notes, pedals=pedals), take([0.0, 1.0], pedals=[(0.5, 64, 90), (0.9, 64, 0)]),
                             1.5, 3.0)
    _ppq, msgs, out = read(data)
    # the section's start less SLACK_S: 1.495 s = tick 1435
    assert [n for n in out if n[2] == 40] == [(0, 1435, 40, 50)]       # released at the section's start
    cc = [(t, m.value) for t, m in msgs if m.type == "control_change" and m.control == 64]
    # down; lifted at the start; the take's own (2.0 s, 2.4 s); the piece's value at the end (its 100 from 2.6 s)
    assert cc == [(0, 127), (1435, 0), (1920, 90), (2304, 0), (2875, 100)]
    assert rep["replaced"] == 3 and rep["played"] == 2 and rep["stretch"] == 1.0


def test_releases_past_the_end_are_cut_and_notes_far_past_it_left_out():
    # one bar (3 onsets in 1.0 s) played as 3 onsets over 9 s, then a 4th at 20 s: the stretch stops at 1/3
    data, rep = punch.splice(piece(BEATS), take([0.0, 1.0, 9.0, 20.0], length=5.0), 1.5, 3.0)
    _ppq, _msgs, out = read(data)
    # 0 -> 1.5 s, 1 -> 1.833 s; 9 and 20 land past 3.0 s; releases cut at 2.995 s (tick 2875)
    assert [n for n in out if n[2] >= 72] == [(1440, 2875, 72, 90), (1760, 2875, 73, 90)]
    assert rep["stretch"] == pytest.approx(1 / 3, abs=1e-3) and rep["left_out"] == 2
    assert 54 in [n[2] for n in out]                                    # the next bar is still there


def test_one_note_takes_and_empty_sections_shift_only():
    data, rep = punch.splice(piece(BEATS), take([0.0]), 6.5, 8.0)       # past the piece's end: an empty section
    _ppq, _msgs, out = read(data)
    assert rep["replaced"] == 0 and rep["stretch"] == 1.0
    assert [n for n in out if n[2] >= 72] == [(6240, 6432, 72, 90)] and len(out) == 13


def test_a_re_strike_in_the_take_ends_the_note_before_it():
    ev = [(5000.0, 1, 0x90, 72, 80), (5000.4, 2, 0x90, 72, 70), (5000.8, 3, 0x80, 72, 0)]
    data, rep = punch.splice(piece(BEATS), ev, 1.5, 3.0)
    _ppq, _msgs, out = read(data)                                       # read() fails on a note never released
    assert [n[0] for n in out if n[2] == 72] == [1440, 2400] and rep["played"] == 2


def test_bad_sections_and_files():
    for a, b in ((-1, 2), (2, 2), (3, 1), (float("nan"), 1), ("1", 2), (True, 2)):
        with pytest.raises(ValueError, match="bad_section"):
            punch.splice(piece(BEATS), take([0.0]), a, b)
    with pytest.raises(ValueError, match="bad_midi"):
        punch.splice(b"not midi", take([0.0]), 1, 2)
    with pytest.raises(ValueError, match="no_notes"):
        punch.splice(piece(BEATS), [(5000.0, 1, 0xB0, 64, 127)], 1, 2)


# -- Stop for a piece (recorder.py) -------------------------------------------------------------------------------------
def make_rec(tmp_path, piece_bytes: bytes):
    """A recorder with one recording already there (rec:<rid>, "Étude" by Chopin) to play sections of."""
    clock = Clock()
    folder = tmp_path / "recs"
    folder.mkdir()
    rid = "20261024-100000-abcd"
    (folder / f"{rid}.mid").write_bytes(piece_bytes)
    (folder / f"{rid}.json").write_text(json.dumps({"v": 1, "id": rid, "title": "Étude", "composer": "Chopin",
                                                    "genre": "classical", "recorded_at": "2026-10-24T10:00:00+01:00",
                                                    "duration_s": 6.0, "notes": 12}))
    rec = Recorder(None, folder=folder, clock=clock, wall=lambda: 1_792_665_000.0 + clock.t)
    rec.configure(SETTINGS)
    rec.start()
    return rec, clock, folder, rid


def play(rec, clock, onsets):
    rec.record(True)
    k = Kiosk(rec, clock)
    t0 = clock.t
    for s in onsets:
        k.batch([ev_on(t0 + 1 + s, 72, 90)])
        k.batch([ev_off(t0 + 1 + s + 0.2, 72)])
    clock.t = t0 + 2 + max(onsets)


def test_stop_with_a_section_keeps_a_new_version_and_never_touches_the_piece(tmp_path):
    orig = piece(BEATS)
    rec, clock, folder, rid = make_rec(tmp_path, orig)
    play(rec, clock, [0.0, 0.5, 1.0])
    out = rec.record(False, {"piece": f"rec:{rid}", "section": {"from_s": 1.5, "to_s": 3.0, "from_bar": 2,
                                                               "to_bar": 2}})
    assert out["made"] == "version" and out["version_of"] == f"rec:{rid}" and "error" not in out
    last = out["last"]
    assert last["id"] != rid and last["title"] == "Étude (bar 2 re-played)" and last["composer"] == "Chopin"
    assert last["version_of"] == f"rec:{rid}" and last["notes"] == 12
    assert (folder / f"{rid}.mid").read_bytes() == orig                 # the original, as it was
    _ppq, _msgs, notes = read((folder / f"{last['id']}.mid").read_bytes())
    assert [n[0] for n in notes if n[2] == 72] == [1440, 1920, 2400]
    meta = json.loads((folder / f"{last['id']}.json").read_text())
    assert meta["section"] == {"from_s": 1.5, "to_s": 3.0, "from_bar": 2, "to_bar": 2}
    assert meta["punch"]["replaced"] == 3 and meta["take"]["notes"] == 3 and meta["genre"] == "classical"
    assert {r["id"] for r in out["recordings"]} == {last["id"], rid}
    # a version of the version: the title doesn't pile up
    play(rec, clock, [0.0, 1.0])
    out2 = rec.record(False, {"piece": f"rec:{last['id']}", "section": {"from_s": 3.0, "to_s": 4.5, "from_bar": 3,
                                                                       "to_bar": 4}})
    assert out2["last"]["title"] == "Étude (bars 3-4 re-played)"


def test_stop_for_a_piece_without_a_section_is_a_take_named_after_it(tmp_path):
    rec, clock, folder, rid = make_rec(tmp_path, piece(BEATS))
    play(rec, clock, [0.0, 0.5])
    out = rec.record(False, {"piece": f"rec:{rid}"})
    last = out["last"]
    assert out["made"] == "take" and out["of"] == f"rec:{rid}" and last["of"] == f"rec:{rid}"
    assert last["title"].startswith("Étude (take, ") and last["composer"] == "Chopin" and last["notes"] == 2


def test_a_piece_that_cant_be_read_keeps_the_take_on_its_own(tmp_path):
    rec, clock, folder, rid = make_rec(tmp_path, b"MThd broken")
    play(rec, clock, [0.0, 0.5])
    out = rec.record(False, {"piece": f"rec:{rid}", "section": {"from_s": 1.5, "to_s": 3.0}})
    assert out["made"] == "take" and "kept on its own" in out["error"] and out["last"]["notes"] == 2
    play(rec, clock, [0.0])
    out = rec.record(False, {"piece": "lib:not-there", "section": {"from_s": 1.5, "to_s": 3.0}})
    assert out["made"] == "take" and "couldn't be found" in out["error"]


def test_a_bad_section_is_refused_before_the_take_stops(tmp_path):
    rec, clock, folder, rid = make_rec(tmp_path, piece(BEATS))
    play(rec, clock, [0.0])
    with pytest.raises(ValueError, match="bad_section"):
        rec.record(False, {"piece": f"rec:{rid}", "section": {"from_s": 3.0, "to_s": 1.0}})
    with pytest.raises(ValueError, match="bad_piece"):
        rec.record(False, {"piece": 7})
    assert rec.view()["recording"]["state"] == "recording"


def test_the_record_route_takes_a_piece_and_a_section(tmp_path):
    from show_service.tests.test_api import FakeBrain, body, make_api, run, served
    rec, clock, folder, rid = make_rec(tmp_path, piece(BEATS))
    brain = FakeBrain([rec])
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                await cl.post("/api/v1/piano/record", json={"on": True})
                k = Kiosk(rec, clock)
                k.batch([ev_on(clock.t + 0.1, 72), ev_off(clock.t + 0.3, 72), ev_cc(clock.t + 0.2, 64, 0)])
                clock.t += 1.0
                r = await cl.post("/api/v1/piano/record", json={"on": False, "piece": f"rec:{rid}",
                                                                "section": {"from_s": 2, "to_s": 1}})
                assert r.status == 400 and (await body(r))["error"] == "bad_section"
                b = await body(await cl.post("/api/v1/piano/record", json={
                    "on": False, "piece": f"rec:{rid}", "section": {"from_s": 1.5, "to_s": 3.0, "from_bar": 2}}))
                assert b["made"] == "version" and b["last"]["version_of"] == f"rec:{rid}"
        run(go())
    finally:
        brain.close()


def test_stop_on_a_loop_splices_in_a_worker_thread(tmp_path):
    rec, clock, folder, rid = make_rec(tmp_path, piece(BEATS))
    play(rec, clock, [0.0, 0.5, 1.0])

    async def go():
        got = rec.record(False, {"piece": f"rec:{rid}", "section": {"from_s": 1.5, "to_s": 3.0}})
        assert asyncio.iscoroutine(got)
        return await got
    out = asyncio.run(go())
    assert out["made"] == "version" and out["last"]["title"] == "Étude (a section re-played)"
