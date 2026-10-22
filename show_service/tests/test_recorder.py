"""The piano recorder (show_service/recorder.py): the take from synthetic VBAN-like on_midi calls, the SMF it writes,
rename/delete, the routes, and the __main__ tap. tmp folders only; nothing is played or sent."""
from __future__ import annotations

import asyncio
import json
import random
import re
import threading

import mido
import pytest

from show_service import api as apimod
from show_service import recorder as recmod
from show_service import vban
from show_service.recorder import Recorder

SENDER_OFF = 5000.0             # host time - the kiosk's clock (its CLOCK_MONOTONIC started 5000 s after ours)
SETTINGS = {"situations": {"midi": {"streams": ["room-piano"], "channels": [1]}},
            "senders": {"kiosk": {"hosts": ["192.0.2.10"], "streams": ["room-mic", "room-piano"]}}}


class Clock:
    def __init__(self, t: float = 10000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def ev_on(t: float, p: int, v: int = 80, ch: int = 1) -> list[int]:
    return [int(round(t * 1e9)), 0x90 | (ch - 1), p, v]


def ev_off(t: float, p: int, v: int = 0, ch: int = 1, zero_on: bool = False) -> list[int]:
    return [int(round(t * 1e9)), (0x90 if zero_on else 0x80) | (ch - 1), p, 0 if zero_on else v]


def ev_cc(t: float, n: int, v: int, ch: int = 1) -> list[int]:
    return [int(round(t * 1e9)), 0xB0 | (ch - 1), n, v]


class Kiosk:
    """The kiosk sender as the recorder sees it: numbered batches with the last two as prev, states, per stream; times
    are the sender's clock (host - off); ``to_host`` maps them back with some jitter (the RemoteClock's)."""

    def __init__(self, rec: Recorder, clock: Clock, *, stream: str = "room-piano", sess: str = "kiosk-a",
                 off: float = SENDER_OFF, jitter_s: float = 0.0) -> None:
        self.rec, self.clock, self.stream, self.sess, self.off = rec, clock, stream, sess, off
        self.jitter_s = jitter_s
        self.rng = random.Random(7)
        self.seq = 0
        self.sent: list[tuple[int, list]] = []

    def to_host(self, t_ns: int) -> float:
        return t_ns / 1e9 + self.off + (self.rng.uniform(-self.jitter_s, self.jitter_s) if self.jitter_s else 0.0)

    def batch(self, ev: list, *, drop: bool = False, resend: bool = False) -> None:
        """``ev`` in host seconds (converted to the sender's clock here)."""
        ev = [[e[0] - int(round(self.off * 1e9)), e[1], e[2], e[3]] for e in ev]
        if not resend:
            self.seq += 1
            self.sent.append((self.seq, ev))
        seq, ev = self.sent[-1]
        prev = [(s, e) for s, e in reversed(self.sent[-3:-1])]
        if drop:
            return
        b = vban.parse_midi_events(vban.midi_events_obj(seq, ev, prev, sess=self.sess))
        arrival = max(e[0] for e in ev) / 1e9 + self.off + 0.004
        self.clock.t = max(self.clock.t, arrival)
        self.rec.on_midi(self.stream, b, arrival, self.to_host)

    def state(self, t_host: float, keys: dict[int, int] | None = None, cc64: int = 0) -> None:
        k = [0] * 88
        for p, v in (keys or {}).items():
            k[p - 21] = v
        t_ns = int(round((t_host - self.off) * 1e9))
        s = vban.parse_midi_state(vban.midi_state_obj(self.seq, k, cc64, 0, 0, t_ns, sess=self.sess))
        self.clock.t = max(self.clock.t, t_host + 0.004)
        self.rec.on_midi(self.stream, s, t_host + 0.004, self.to_host)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    rec = Recorder(None, folder=tmp_path / "recs", clock=clock, wall=lambda: 1_792_665_000.0 + clock.t)
    rec.configure(SETTINGS)
    rec.start()
    return rec, clock, tmp_path / "recs"


def read_smf(path) -> tuple[mido.MidiFile, list[tuple[float, mido.Message]]]:
    mid = mido.MidiFile(str(path))
    out, ticks = [], 0
    for msg in mid.tracks[0]:
        ticks += msg.time
        out.append((ticks / (recmod.PPQ * 2.0), msg))       # 120 bpm: 1920 ticks a second
    return mid, out


def stop(rec: Recorder) -> dict:
    got = rec.record(False)
    assert isinstance(got, dict)                           # off any loop: inline
    return got


# -- the take and the file ---------------------------------------------------------------------------------------------
def test_notes_and_pedals_round_trip_within_1_ms_on_the_senders_own_spacing(rig):
    rec, clock, folder = rig
    k = Kiosk(rec, clock, jitter_s=0.008)                  # to_host jitters 8 ms: the take must not
    assert rec.record(True)["recording"]["state"] == "recording"
    t0 = clock.t
    k.batch([ev_on(t0 + 1.0, 60, 80), ev_on(t0 + 1.0, 64, 70)])           # a chord: one stamp
    k.batch([ev_cc(t0 + 1.2, 64, 64)])                                       # half pedal (raw, proportional)
    k.batch([ev_off(t0 + 1.5, 60, 40)])                                      # a note-off velocity
    k.batch([ev_off(t0 + 1.6, 64, zero_on=True)])                            # note-on velocity 0 = note-off
    k.batch([ev_cc(t0 + 2.0, 64, 0), ev_on(t0 + 2.2503, 67, 101)])
    k.state(t0 + 2.4, keys={67: 101})
    k.batch([ev_off(t0 + 2.75, 67, 12)])
    v = rec.view()
    assert v["recording"]["state"] == "recording" and v["recording"]["notes"] == 3
    clock.t = t0 + 4.0
    out = stop(rec)
    last = out["last"]
    assert out["recording"]["state"] == "off" and last["notes"] == 3
    assert re.match(r"^\d{8}-\d{6}-[0-9a-f]{4}$", last["id"]) and last["title"].startswith("Take - ")
    assert [r["id"] for r in out["recordings"]] == [last["id"]]
    assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$", last["recorded_at"])
    mid, msgs = read_smf(folder / f"{last['id']}.mid")
    assert mid.type == 0 and mid.ticks_per_beat == 960
    metas = [m for _, m in msgs if m.is_meta]
    assert metas[0].type == "track_name" and metas[0].name == last["title"]
    assert metas[1].type == "set_tempo" and metas[1].tempo == 500000 and metas[-1].type == "end_of_track"
    ev = [(t, m) for t, m in msgs if not m.is_meta]
    want = [(1.0, "note_on", 60, 80), (1.0, "note_on", 64, 70), (1.2, "control_change", 64, 64),
            (1.5, "note_off", 60, 40), (1.6, "note_off", 64, 0), (2.0, "control_change", 64, 0),
            (2.2503, "note_on", 67, 101), (2.75, "note_off", 67, 12)]
    assert len(ev) == len(want)
    for (t, m), (wt, wtype, a, b) in zip(ev, want):
        assert m.type == wtype and m.channel == 0
        assert (m.note, m.velocity) == (a, b) if wtype != "control_change" else (m.control, m.value) == (a, b)
        assert abs(t - (wt - 1.0 + recmod.LEAD_IN_S)) < 0.001, (m, t, wt)        # trimmed to 0.5 s of lead-in
    assert abs(msgs[-1][0] - (2.75 - 1.0 + 0.5 + recmod.TAIL_S)) < 0.001
    meta = json.loads((folder / f"{last['id']}.json").read_text())
    assert meta["stream"] == "room-piano" and meta["sender"] == "kiosk" and meta["channels"] == [1]
    assert meta["duration_s"] == pytest.approx(0.5 + 1.75 + 1.0, abs=0.002) == last["duration_s"]
    assert abs(meta["host_t0"] - (t0 + 0.5)) < 0.02                      # the first packet's map (jitter <= 8 ms)
    assert meta["wall_start"] == pytest.approx(1_792_665_000.0 + meta["host_t0"], abs=1e-3)
    assert meta["lost"] == 0 and meta["sessions"] == 1


def test_only_listed_streams_and_channels_never_tests_nor_before_record(rig):
    rec, clock, folder = rig
    k = Kiosk(rec, clock)
    t0 = clock.t
    k.batch([ev_on(t0 - 0.5, 50, 60)])                     # before Record: not taken (nothing records yet)
    rec.record(True)
    clock.t = t0 + 0.01
    rec.record(True)                                       # already recording: the same take
    k.batch([ev_off(t0 - 0.1, 50), ev_on(t0 + 0.3, 62, 70)])   # its prev carries the pre-Record note-on
    k.batch([ev_on(t0 + 0.5, 30, 50, ch=2), ev_cc(t0 + 0.5, 64, 127, ch=2)])   # the adsilent's playback channel
    Kiosk(rec, clock, stream="test-piano", sess="t").batch([ev_on(t0 + 0.6, 72, 90)])
    Kiosk(rec, clock, stream="other-piano", sess="o").batch([ev_on(t0 + 0.6, 74, 90)])
    k.batch([ev_off(t0 + 0.9, 62)])
    clock.t = t0 + 1.5
    last = stop(rec)["last"]
    _, msgs = read_smf(folder / f"{last['id']}.mid")
    ev = [m for _, m in msgs if not m.is_meta]
    assert [(m.type, m.note) for m in ev] == [("note_on", 62), ("note_off", 62)] and last["notes"] == 1
    snap = rec.snapshot()
    assert snap["recording"]["state"] == "off" and snap["streams"] == ["room-piano"] and snap["channels"] == [1]


def test_unset_streams_take_every_live_stream_and_all_channels(tmp_path):
    clock = Clock()
    rec = Recorder(None, folder=tmp_path, clock=clock)
    rec.configure({"situations": {"midi": {"streams": ["test-piano"]}}})    # a test stream listed: still never
    assert rec.snapshot()["streams"] == [] and rec.snapshot()["channels"] is None
    rec.configure({})
    rec.record(True)
    t0 = clock.t
    Kiosk(rec, clock, stream="test-piano", sess="t").batch([ev_on(t0 + 0.1, 72, 90)])
    Kiosk(rec, clock, stream="room-piano", sess="r").batch([ev_on(t0 + 0.2, 40, 90, ch=2)])
    clock.t = t0 + 1.0
    last = stop(rec)["last"]
    _, msgs = read_smf(tmp_path / f"{last['id']}.mid")
    ev = [m for _, m in msgs if not m.is_meta]
    assert [(m.type, m.note, m.channel) for m in ev] == [("note_on", 40, 1), ("note_off", 40, 1)]


def test_a_gap_is_healed_from_prev_duplicates_dropped_and_the_rest_counted_lost(rig):
    rec, clock, folder = rig
    k = Kiosk(rec, clock)
    rec.record(True)
    t0 = clock.t
    k.batch([ev_on(t0 + 0.1, 60)])
    k.batch([ev_off(t0 + 0.2, 60)], drop=True)             # lost on the wire ...
    k.batch([ev_on(t0 + 0.3, 62)])                          # ... healed from this one's prev
    k.batch([ev_on(t0 + 0.3, 62)], resend=True)             # a duplicate (the receiver passes them through)
    for i in range(4):                                       # 4 batches lost: 2 healed by the next prev, 2 lost
        k.batch([ev_on(t0 + 0.4 + 0.05 * i, 64 + i)], drop=True)
    k.batch([ev_off(t0 + 0.7, 62)])
    rec.view()
    snap = rec.snapshot()["take"]
    assert snap["healed"] == 3 and snap["lost"] == 2 and snap["dupes"] == 1
    clock.t = t0 + 1.0
    last = stop(rec)["last"]
    _, msgs = read_smf(folder / f"{last['id']}.mid")
    ev = [(m.type, m.note) for _, m in msgs if not m.is_meta]
    assert ev[:3] == [("note_on", 60), ("note_off", 60), ("note_on", 62)]
    assert ("note_on", 66) in ev and ("note_on", 67) in ev and ("note_on", 64) not in ev
    assert ("note_off", 66) in ev and ("note_off", 67) in ev               # still held at Stop: closed
    meta = json.loads((folder / f"{last['id']}.json").read_text())
    assert meta["lost"] == 2 and meta["healed"] == 3 and last["notes"] == 4


def test_a_session_change_is_stitched_with_to_host_and_closes_the_old_sessions_notes(rig):
    rec, clock, folder = rig
    a = Kiosk(rec, clock, sess="kiosk-a")
    rec.record(True)
    t0 = clock.t
    a.batch([ev_on(t0 + 0.2, 60), ev_cc(t0 + 0.2, 64, 127)])
    a.state(t0 + 0.6, keys={60: 80}, cc64=127)               # the box goes away mid-note
    b = Kiosk(rec, clock, sess="kiosk-b", off=SENDER_OFF + 123.0)   # re-plugged: a new sess, its clock restarted
    b.batch([ev_on(t0 + 3.0, 64), ev_off(t0 + 3.4, 64)])
    clock.t = t0 + 4.0
    last = stop(rec)["last"]
    _, msgs = read_smf(folder / f"{last['id']}.mid")
    ev = [(round(t, 3), m.type, getattr(m, "note", getattr(m, "control", None)), getattr(m, "velocity",
           getattr(m, "value", None))) for t, m in msgs if not m.is_meta]
    lead = recmod.LEAD_IN_S
    assert ev == [(lead, "note_on", 60, 80), (lead, "control_change", 64, 127),
                  (lead + 0.4, "note_off", 60, 0), (lead + 0.4, "control_change", 64, 0),   # at a's last time
                  (lead + 2.8, "note_on", 64, 80), (lead + 3.2, "note_off", 64, 0)]
    assert json.loads((folder / f"{last['id']}.json").read_text())["sessions"] == 2


def test_states_close_notes_that_are_up_never_open_and_stop_closes_the_rest(rig):
    rec, clock, folder = rig
    k = Kiosk(rec, clock)
    rec.record(True)
    t0 = clock.t
    k.batch([ev_on(t0 + 0.1, 60), ev_on(t0 + 0.1, 72), ev_cc(t0 + 0.1, 64, 90), ev_cc(t0 + 0.1, 67, 30)])
    k.batch([ev_off(t0 + 0.3, 60)], drop=True)              # its off is lost and nothing carries it
    k.state(t0 + 0.5, keys={72: 80, 70: 99}, cc64=90)       # 60 is up: closed here; 70 down: never opened
    clock.t = t0 + 2.0
    last = stop(rec)["last"]
    _, msgs = read_smf(folder / f"{last['id']}.mid")
    lead = recmod.LEAD_IN_S
    ev = [(round(t, 3), m.type, getattr(m, "note", getattr(m, "control", None)), getattr(m, "velocity",
           getattr(m, "value", None))) for t, m in msgs if not m.is_meta]
    assert (lead + 0.4, "note_off", 60, 0) in ev
    assert not any(e[2] == 70 for e in ev if e[1].startswith("note"))
    stop_t = lead + 1.9                                       # Stop at t0 + 2.0, the first event at t0 + 0.1
    assert (stop_t, "note_off", 72, 0) in ev and (stop_t, "control_change", 64, 0) in ev
    assert (stop_t, "control_change", 67, 0) in ev
    assert last["notes"] == 2


def test_an_empty_take_is_not_saved(rig):
    rec, clock, folder = rig
    assert stop(rec)["last"] is None                         # nothing recording
    rec.record(True)
    Kiosk(rec, clock).batch([ev_cc(clock.t + 0.1, 64, 127)])   # a pedal alone is not a take
    clock.t += 1.0
    out = stop(rec)
    assert out["last"] is None and out["recordings"] == [] and not folder.exists()


def test_the_hot_repeat_cap_is_applied_when_writing(rig):
    rec, clock, folder = rig
    k = Kiosk(rec, clock)
    rec.record(True)
    t0 = clock.t
    k.batch([ev_on(t0 + 0.1, 60, 40), ev_off(t0 + 0.15, 60)])
    k.batch([ev_on(t0 + 0.3, 60, 127), ev_off(t0 + 0.32, 60)])       # within 0.25 s: 40 + 32
    k.batch([ev_on(t0 + 0.4, 60, 127), ev_off(t0 + 0.42, 60)])       # within 0.25 s of the capped 72: 104
    k.batch([ev_on(t0 + 1.0, 60, 127)])                               # 0.6 s later: as played
    clock.t = t0 + 1.5
    last = stop(rec)["last"]
    _, msgs = read_smf(folder / f"{last['id']}.mid")
    assert [m.velocity for _, m in msgs if m.type == "note_on"] == [40, 72, 104, 127]


def test_rename_delete_and_the_file(rig):
    rec, clock, folder = rig
    rec.record(True)
    Kiosk(rec, clock).batch([ev_on(clock.t + 0.1, 60), ev_off(clock.t + 0.5, 60)])
    clock.t += 1.0
    first = stop(rec)["last"]
    clock.t += 70.0                                          # a minute later: a second take, listed first
    rec.record(True)
    Kiosk(rec, clock, sess="k2").batch([ev_on(clock.t + 0.1, 62)])
    clock.t += 1.0
    second = stop(rec)["last"]
    assert [r["id"] for r in rec.view()["recordings"]] == [second["id"], first["id"]]
    row = rec.rename(first["id"], "  Chopin   — the good one ")
    assert row["title"] == "Chopin — the good one" and row["id"] == first["id"]
    mid, _ = read_smf(folder / f"{first['id']}.mid")
    assert mid.tracks[0][0].name == "Chopin ? the good one"              # latin-1 in the file; exact in the JSON
    assert json.loads((folder / f"{first['id']}.json").read_text())["title"] == "Chopin — the good one"
    assert rec.view()["recordings"][1]["title"] == "Chopin — the good one"
    with pytest.raises(ValueError, match="bad_title"):
        rec.rename(first["id"], "   ")
    with pytest.raises(ValueError, match="bad_title"):
        rec.rename(first["id"], "x" * 121)
    with pytest.raises(LookupError):
        rec.rename("20990101-000000-abcd", "x")
    with pytest.raises(LookupError):
        rec.file_of("../../etc/passwd")
    path, title = rec.file_of(second["id"])
    assert path == folder / f"{second['id']}.mid" and title == second["title"]
    assert rec.delete(second["id"]) == {"deleted": second["id"]}
    assert not (folder / f"{second['id']}.mid").exists() and not (folder / f"{second['id']}.json").exists()
    with pytest.raises(LookupError):
        rec.delete(second["id"])
    assert [r["id"] for r in rec.view()["recordings"]] == [first["id"]]
    assert sorted(p.name for p in folder.iterdir()) == sorted([f"{first['id']}.mid", f"{first['id']}.json"])


def test_a_take_running_at_shutdown_is_saved(rig):
    rec, clock, folder = rig
    rec.record(True)
    Kiosk(rec, clock).batch([ev_on(clock.t + 0.1, 60)])
    clock.t += 1.0
    rec.stop()
    rows = rec.view()["recordings"]
    assert len(rows) == 1 and rows[0]["notes"] == 1 and rec.view()["recording"]["state"] == "off"


def test_the_folder_comes_from_show_yaml(tmp_path):
    rec = Recorder(None, folder=tmp_path / "default")
    rec.configure({"tests": {"piano": {"recordings": str(tmp_path / "mine")}}})
    assert rec.folder == tmp_path / "mine"
    assert recmod.recordings_folder({"tests": {"piano": {"recordings": "~/x"}}}).is_absolute()
    assert recmod.recordings_folder({}) == recmod.RECORDINGS_DIR
    rec.configure({})
    assert rec.folder == tmp_path / "default"


def test_the_show_rx_tap_is_one_append(rig):
    import time as _time
    rec, clock, _ = rig
    b = vban.parse_midi_events(vban.midi_events_obj(1, [[1, 0x90, 60, 80]], sess="s"))
    t = _time.perf_counter()
    for _ in range(2000):
        rec.on_midi("room-piano", b, 1.0, lambda t_ns: 2.0)         # not recording: returns at once
    idle_us = (_time.perf_counter() - t) / 2000 * 1e6
    rec.record(True)
    t = _time.perf_counter()
    for _ in range(2000):
        rec.on_midi("room-piano", b, 1.0, lambda t_ns: 2.0)
    busy_us = (_time.perf_counter() - t) / 2000 * 1e6
    print(f"recorder tap: {idle_us:.2f} us idle, {busy_us:.2f} us recording (a batch)")
    assert len(rec._take.q) == 2000 and idle_us < 100 and busy_us < 100     # README budget 20 us; slack for load
    rec.view()                                                       # drained on the brain side
    assert len(rec._take.q) == 0


def test_stop_on_a_loop_saves_in_a_worker_thread(rig):
    rec, clock, folder = rig
    rec.record(True)
    Kiosk(rec, clock).batch([ev_on(clock.t + 0.1, 60)])
    clock.t += 1.0
    threads: list[str] = []
    orig = Recorder._write

    def spy(job):
        threads.append(threading.current_thread().name)
        orig(job)
    rec._write = spy                                                  # type: ignore[method-assign]

    async def go():
        got = rec.record(False)
        assert asyncio.iscoroutine(got)
        return await got
    out = asyncio.run(go())
    assert out["last"]["notes"] == 1 and threads and threads[0] != threading.current_thread().name
    assert rec.snapshot()["saving"] == 0


# -- the routes ------------------------------------------------------------------------------------------------------------
def test_the_recording_routes(tmp_path):
    from show_service.tests.test_api import FakeBrain, body, make_api, run, served
    clock = Clock()
    rec = Recorder(None, folder=tmp_path / "recs", clock=clock)
    rec.configure(SETTINGS)
    brain = FakeBrain([rec])
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                b = await body(await cl.get("/api/v1/piano/recordings"))
                assert b == {"recording": {"state": "off", "since_s": None, "notes": 0, "duration_s": 0.0},
                             "recordings": []}
                r = await cl.post("/api/v1/piano/record", json={"on": "yes"})
                assert r.status == 400 and (await body(r))["error"] == "bad_request"
                b = await body(await cl.post("/api/v1/piano/record", json={"on": True}))
                assert b["recording"]["state"] == "recording" and "last" not in b
                k = Kiosk(rec, clock)
                k.batch([ev_on(clock.t + 0.1, 60), ev_off(clock.t + 0.6, 60, 30)])
                b = await body(await cl.get("/api/v1/piano/recordings"))
                assert b["recording"]["notes"] == 1
                clock.t += 1.0
                b = await body(await cl.post("/api/v1/piano/record", json={"on": False}))
                last = b["last"]
                assert last["notes"] == 1 and b["recordings"] == [last] and b["recording"]["state"] == "off"
                r = await cl.get(f"/api/v1/piano/recordings/{last['id']}.mid")
                assert r.status == 200 and r.content_type == "audio/midi"
                cd = r.headers["Content-Disposition"]
                assert cd.startswith("attachment; filename=\"Take - ") and ".mid\"" in cd and "filename*=UTF-8''" in cd
                data = await r.read()
                assert data[:4] == b"MThd" and data == (tmp_path / "recs" / f"{last['id']}.mid").read_bytes()
                r = await cl.patch(f"/api/v1/piano/recordings/{last['id']}", json={"title": "Évening étude"})
                b = await body(r)
                assert r.status == 200 and b["title"] == "Évening étude" and b["id"] == last["id"]
                r = await cl.get(f"/api/v1/piano/recordings/{last['id']}.mid")
                assert "filename*=UTF-8''%C3%89vening%20%C3%A9tude.mid" in r.headers["Content-Disposition"]
                r = await cl.patch(f"/api/v1/piano/recordings/{last['id']}", json={"title": ""})
                assert r.status == 400 and (await body(r))["error"] == "bad_title"
                r = await cl.patch("/api/v1/piano/recordings/20990101-000000-abcd", json={"title": "x"})
                assert r.status == 404 and (await body(r))["error"] == "unknown_recording"
                r = await cl.get("/api/v1/piano/recordings/20990101-000000-abcd.mid")
                assert r.status == 404
                b = await body(await cl.delete(f"/api/v1/piano/recordings/{last['id']}"))
                assert b == {"deleted": last["id"]}
                r = await cl.delete(f"/api/v1/piano/recordings/{last['id']}")
                assert r.status == 200 and (await body(r))["deleted"] == last["id"]     # already gone: no error
                b = await body(await cl.get("/api/v1/piano/recordings"))
                assert b["recordings"] == []
                b = await body(await cl.post("/api/v1/piano/record", json={"on": False}))
                assert b["last"] is None
        run(go())
    finally:
        brain.close()
    assert ("GET", "/api/v1/piano/recordings/{id}.mid") in apimod.ROUTES


def test_the_routes_without_a_recorder_degrade():
    from show_service.tests.test_api import FakeBrain, body, make_api, run, served
    brain = FakeBrain([])
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                b = await body(await cl.get("/api/v1/piano/recordings"))
                assert b["recordings"] == [] and b["recording"]["state"] == "off" and b["detail"]
                b = await body(await cl.post("/api/v1/piano/record", json={"on": True}))
                assert b["recording"]["state"] == "off" and b["detail"]
                r = await cl.delete("/api/v1/piano/recordings/x")
                assert r.status == 503
        run(go())
    finally:
        brain.close()


# -- __main__'s tap ---------------------------------------------------------------------------------------------------------
def test_main_taps_the_recorder_after_notetap_and_a_failure_never_breaks_on_midi(monkeypatch, tmp_path):
    from show_service import __main__ as main_mod
    from show_service.tests.test_main import _brain_opts, _with_seams, install_brain_fakes, install_fakes
    install_fakes(monkeypatch)
    _with_seams(monkeypatch)
    install_brain_fakes(monkeypatch)
    opts = _brain_opts(tmp_path)
    (tmp_path / "show.yaml").write_text(json.dumps({"version": 1, "tests": {"piano": {"recordings": str(
        tmp_path / "recs")}}, "situations": {"midi": {"streams": ["room-piano"], "channels": [1]}}}))
    show = main_mod.Show(opts)
    show.start()
    try:
        rec = show.recorder
        assert isinstance(rec, Recorder) and show.brain.components["recorder"] is rec and rec.folder == tmp_path / "recs"
        assert show.modules["recorder"] == "ok"

        async def call(fn, *a):
            return await show.brain.call(fn, *a, timeout=10.0)
        assert asyncio.run(call(rec.record, True))["recording"]["state"] == "recording"
        b = vban.parse_midi_events(vban.midi_events_obj(1, [[int(1e9), 0x90, 60, 80]], sess="w"))
        now = rec.clock()
        show.rx.on_midi("room-piano", b, now, lambda t_ns: now + 0.1)
        assert show.tap.msgs[-1] == ("room-piano", b) and show.pianos["room-piano"].msgs[-1][0] is b
        out = asyncio.run(call(rec.record, False))
        assert out["last"]["notes"] == 1 and (tmp_path / "recs" / f"{out['last']['id']}.mid").exists()

        class Boom:
            def on_midi(self, *a):
                raise RuntimeError("tap")
        show.recorder = Boom()
        show.rx.on_midi("room-piano", "batch-x", 1.0, lambda t_ns: 2.5)
        assert show._g["recorder"].errors == 1 and show.pianos["room-piano"].msgs[-1] == ("batch-x", 2.5)
        assert show._g["on_midi"].errors == 0
    finally:
        show.stop()
