"""show-synth runtime control: tempo changes and ramps keep the beat phase, the truth record stays exact, the
control endpoint, and stream_service's /dev/synth proxy (no GPU)."""
from __future__ import annotations

import io
import json
import pathlib
import random
import socket

import pytest
from aiohttp.test_utils import TestClient, TestServer

from stream_service.inputs import FixtureSignals
from stream_service.server import make_app, synth_control_url
from stream_service.tools.show_synth import Song, Synth

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
TOKEN = "cd" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
RATE_S = 0.025


def song(style="pop", bpm=124.0) -> Song:
    return Song(style, bpm, 7, t0=0.0)


def beat_times(sg: Song, n: int) -> list[float]:
    return [sg.beat_time(k) for k in range(n)]


# -- tempo: steps and ramps -------------------------------------------------------------------------------------
def test_bpm_change_keeps_the_beat_phase():
    sg = song(bpm=124)
    tc = 10.3                                                # mid-beat
    before = sg.tempo_map.pos(tc)
    sg.set_bpm(tc, 140)
    assert sg.tempo_map.pos(tc) == pytest.approx(before, abs=1e-9)        # no jump at the change
    assert sg.tempo_map.pos(tc + 1e-6) - before == pytest.approx(1e-6 * 140 / 60, rel=1e-3)
    assert sg.bpm == 140 and sg.spb == pytest.approx(60 / 140)
    ts = beat_times(sg, 40)
    d = [b - a for a, b in zip(ts, ts[1:])]
    k = int(before)                                          # the beat that straddles the change
    assert all(x == pytest.approx(60 / 124) for x in d[:k])
    assert all(x == pytest.approx(60 / 140) for x in d[k + 1:])
    assert 60 / 140 < d[k] < 60 / 124                        # part old tempo, part new
    for i in range(40):                                      # beat_time inverts the beat position
        assert sg.tempo_map.pos(ts[i]) == pytest.approx(i, abs=1e-9)
    # the old constant-tempo view still holds after the change: beat k at t0 + k * spb
    assert sg.beat_time(30) == pytest.approx(sg.t0 + 30 * sg.spb)


def test_ramp_integrates_the_beat_position():
    sg = song(bpm=124)
    t_a, over = 5.0, 8.0
    sg.ramp_to(t_a, 90, over)
    assert sg.tempo_map.tempo(t_a) == pytest.approx(124) and sg.tempo_map.tempo(t_a + over / 2) == pytest.approx(107)
    assert sg.tempo_map.tempo(t_a + over + 1) == pytest.approx(90) and sg.bpm == 90
    assert sg.tempo_map.ramp(t_a + 2)["remaining_s"] == pytest.approx(6) and sg.tempo_map.ramp(t_a + over) is None
    # the position is continuous and its slope is the instantaneous tempo (no jump anywhere)
    t, prev = 0.0, sg.tempo_map.pos(0.0)
    while t < 20:
        t += 0.001
        p = sg.tempo_map.pos(t)
        assert p > prev
        assert (p - prev) / 0.001 == pytest.approx(sg.tempo_map.tempo(t - 0.0005) / 60, rel=1e-3)
        prev = p
    # the exact integral: 124 -> 90 over 8 s is (124 + 90) / 2 / 60 * 8 beats
    assert sg.tempo_map.pos(t_a + over) - sg.tempo_map.pos(t_a) == pytest.approx(107 / 60 * over)
    ts = beat_times(sg, 60)
    d = [b - a for a, b in zip(ts, ts[1:])]
    assert all(b >= a - 1e-12 for a, b in zip(d, d[1:]))    # slowing down: beat intervals never shrink
    assert min(d) == pytest.approx(60 / 124) and max(d) == pytest.approx(60 / 90)
    for i in range(60):
        assert sg.tempo_map.pos(ts[i]) == pytest.approx(i, abs=1e-9)


def test_ramp_interrupted_by_a_step_and_by_another_ramp():
    sg = song(bpm=124)
    sg.ramp_to(5.0, 174, 20)
    p = sg.tempo_map.pos(9.0)
    mid = sg.tempo_map.tempo(9.0)
    sg.ramp_to(9.0, 60, 4)                                   # a new ramp starts from the tempo it reached
    assert sg.tempo_map.pos(9.0) == pytest.approx(p) and sg.tempo_map.tempo(9.0) == pytest.approx(mid)
    p = sg.tempo_map.pos(11.0)
    sg.set_bpm(11.0, 100)                                    # a step cancels the ramp
    assert sg.tempo_map.pos(11.0) == pytest.approx(p) and sg.tempo_map.ramp(11.5) is None
    ts = beat_times(sg, 50)
    assert all(b > a for a, b in zip(ts, ts[1:]))
    assert sg.tempo_map.tempo(30) == 100


def test_truth_record_beats_stay_exact_across_changes():
    sg = song(bpm=124)
    rec = io.StringIO()
    synth = Synth(sg, record=rec)
    rnd = random.Random(4)
    starts = {}                                              # beat -> first frame that saw it
    n, t = 0, 0.0
    while t < 40:
        n += 1
        t = n * RATE_S + rnd.uniform(-0.003, 0.003)
        if n == 400:
            synth.song.set_bpm(t, 150)
        if n == 800:
            synth.song.ramp_to(t, 80, 6)
        if n == 1200:
            synth.song.set_bpm(t, 128)
        synth.tick(t)
        starts.setdefault(sg.where(t)[0], t)
    beats = [json.loads(x) for x in rec.getvalue().splitlines() if '"beat"' in x and '"section"' not in x]
    times = [b["t_ms"] / 1000 for b in beats]
    assert [b["beat"] for b in beats] == list(range(beats[0]["beat"], beats[0]["beat"] + len(beats)))
    assert all(b > a for a, b in zip(times, times[1:]))
    for b, tb in zip(beats, times):                          # each recorded beat lies in the frame interval
        assert starts[b["beat"]] - RATE_S - 0.007 <= tb <= starts[b["beat"]] + 1e-9, b


def test_song_switch_lands_on_the_next_bar_and_next_section_jumps():
    sg = song("pop", 124)
    t = 10.0                                                 # bar 5, mid-bar
    beat, _, bar, sec, _, _ = sg.where(t)
    assert sec.name == "intro"
    at_bar = sg.switch(t, "techno")
    assert at_bar == bar + 1 and sg.style == "pop"           # not yet
    t_bar = sg.beat_time(4 * at_bar)
    assert sg.state(t_bar - 0.01)["_truth"]["song"] == "pop"
    tr = sg.state(t_bar + 0.01)["_truth"]
    assert tr["song"] == "techno" and tr["section"] == "build" and tr["bar"] == at_bar
    assert sg.bpm == 124                                     # the tempo is kept
    assert sg.next_section(t_bar + 1) == "peak"
    assert sg.state(t_bar + 1.1)["_truth"]["section"] == "peak"
    for _ in range(3):
        sg.next_section(t_bar + 2)
    assert sg.where(t_bar + 2)[3].name == "build"            # wraps to the top of the plan


def test_pulses_off_is_a_breakdown():
    sg = song("pop", 124)
    t = sg.beat_time(8) + 0.01                               # just after a kick
    on = sg.state(t)["fixtures"]["screen.north"]
    sg.pulses = False
    off = sg.state(t)["fixtures"]["screen.north"]
    assert on["pulse"] > 0.3 and off["pulse"] == 0.0 and off["level"] < on["level"]


def test_beats_come_back_on_the_next_beat():
    """The end of a breakdown never starts mid-beat: a part-decayed pulse there is an off-beat edge."""
    sg = song("pop", 124)
    pulse = lambda t: sg.state(t)["fixtures"]["screen.north"]["pulse"]   # noqa: E731
    sg.control(sg.beat_time(8) + 0.01, {"pulses": False})
    assert pulse(sg.beat_time(9) + 0.01) == 0.0
    t_back = sg.beat_time(20) + 0.25 * sg.spb                # beats back a quarter beat in ...
    assert sg.control(t_back, {"pulses": True}) == {"pulses": True} and sg.status(t_back)["pulses"]
    assert pulse(t_back) == 0.0 and pulse(sg.beat_time(21) - 0.01) == 0.0      # ... silent to the next beat
    assert pulse(sg.beat_time(21) + 0.01) > 0.3
    sg.control(t_back + 1, {"pulses": False})
    assert pulse(sg.beat_time(24) + 0.01) == 0.0


# -- the follower against the changes (what the console shows) ----------------------------------------------------
def follow(sg: Song, seconds: float, change=None, at: float = 20.0):
    """Feed show-synth frames into a follower; apply change(song, t) at ``at``.  -> [(t, locked, bpm)]."""
    fs = FixtureSignals("screen.north")
    rnd = random.Random(1)
    out, n, t, done = [], 0, 0.0, False
    while t < seconds:
        n += 1
        t = n * RATE_S + rnd.uniform(-0.003, 0.003)
        if change and not done and t >= at:
            change(sg, t)
            done = True
        st = sg.state(t)
        fs.update(t, t, st["fixtures"]["screen.north"], st["activity"])
        snap = fs.snapshot
        out.append((t, snap.locked(t), 60 / snap.period if snap.period else None))
    return out


def on_tempo_from(trace, bpm, after, tol=0.015):
    """The first time after ``after`` from which the follower stays locked within ``tol`` (relative) of bpm to the
    end (the console's "on tempo"; 40 Hz frames quantise intervals, so the estimate jitters ~1 % at 170 bpm)."""
    first = None
    for t, locked, b in trace:
        if t < after:
            continue
        if locked and b is not None and abs(b - bpm) <= tol * bpm:
            first = t if first is None else first
        else:
            first = None
    return first


@pytest.mark.parametrize("style,b0,b1", [("pop", 124, 140), ("pop", 124, 90), ("pop", 90, 124), ("pop", 124, 174),
                                         ("pop", 90, 140), ("pop", 124, 110), ("jazz", 90, 124), ("jazz", 124, 100)])
def test_follower_relocks_after_a_bpm_step(style, b0, b1):
    """On tempo within 8 beats of the new tempo (jazz, whose beat 2 has no hit: 10).  It was ~16 before the
    follower re-fitted its grid: 4 misses to unlock, 5 new intervals to flip the median, then 6 of 8 hits."""
    trace = follow(song(style, b0), 45, lambda s, t: s.set_bpm(t, b1))
    t_on = on_tempo_from(trace, b1, 20)
    beats = None if t_on is None else (t_on - 20) * b1 / 60
    assert beats is not None and beats <= (10 if style == "jazz" else 8), beats


def test_follower_follows_a_ramp_and_unlocks_in_a_breakdown():
    trace = follow(song("pop", 124), 50, lambda s, t: s.ramp_to(t, 90, 16))
    t_on = on_tempo_from(trace, 90, 36)
    assert t_on is not None and t_on - 36 <= 8, t_on
    sg = song("pop", 124)
    trace = follow(sg, 30, lambda s, t: setattr(s, "pulses", False))
    assert trace[700][1] is True                              # locked before (t ~ 17.5 s)
    gone = next(t for t, locked, _ in trace if t > 20 and not locked)
    assert gone - 20 <= 2.0                                   # unlocked within max(2.5 P, 1.5 s) + a frame
    sg = song("pop", 124)
    trace = follow(sg, 50, lambda s, t: setattr(s, "pulses", False), at=15)
    assert not any(locked for t, locked, _ in trace if 18 < t < 25)


# -- the control endpoint -------------------------------------------------------------------------------------------
@pytest.fixture
async def synth_client():
    rec = io.StringIO()
    synth = Synth(Song("pop", 124, 7), record=rec)
    async with TestClient(TestServer(synth.app())) as c:
        c.rec, c.synth = rec, synth
        yield c


@pytest.mark.asyncio
async def test_control_get_and_post(synth_client):
    c = synth_client
    st = await (await c.get("/v1/control")).json()
    assert {"song", "bpm", "ramp", "pulses", "section", "bar"} <= set(st)
    assert st["song"] == "pop" and st["bpm"] == pytest.approx(124, abs=0.01) and st["ramp"] is None and st["pulses"]
    r = await c.post("/v1/control", json={"bpm": 140})
    st = await r.json()
    assert r.status == 200 and st["bpm"] == 140 and st["target_bpm"] == 140
    st = await (await c.post("/v1/control", json={"ramp": {"to_bpm": 90, "over_s": 30}})).json()
    assert st["ramp"]["to_bpm"] == 90 and st["ramp"]["from_bpm"] == pytest.approx(140, abs=0.1)
    assert st["target_bpm"] == 90 and 139 < st["bpm"] <= 140
    st = await (await c.post("/v1/control", json={"song": "jazz", "pulses": False})).json()
    assert st["song"] == "pop" and st["pending_song"]["song"] == "jazz" and st["pulses"] is False
    st = await (await c.post("/v1/control", json={"next_section": True})).json()
    assert st["section"] == "verse"
    events = [json.loads(x) for x in c.rec.getvalue().splitlines()]
    assert [e for e in events if e["event"] == "control"][0]["bpm"] == 140
    assert {"song", "at_bar", "pulses"} <= set([e for e in events if e["event"] == "control"][2])


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"bpm": 39}, {"bpm": 201}, {"bpm": "120"}, {"bpm": True}, {"bpm": 120, "x": 1},
                                  {"ramp": {"to_bpm": 90}}, {"ramp": {"to_bpm": 90, "over_s": 0}},
                                  {"ramp": {"to_bpm": 300, "over_s": 5}}, {"bpm": 100, "ramp": {"to_bpm": 90, "over_s": 5}},
                                  {"song": "polka"}, {"pulses": "no"}, {"next_section": 1}, [1, 2]])
async def test_control_rejects_bad_bodies(synth_client, body):
    r = await synth_client.post("/v1/control", json=body)
    assert r.status == 400 and (await r.json())["error"] == "bad_request"
    st = await (await synth_client.get("/v1/control")).json()
    assert st["bpm"] == pytest.approx(124, abs=0.01) and st["pulses"] and st["pending_song"] is None   # untouched


@pytest.mark.asyncio
async def test_control_rejects_non_json(synth_client):
    r = await synth_client.post("/v1/control", data="bpm=140", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status == 415
    r = await synth_client.post("/v1/control", data='{"bpm": 140}', headers={"Content-Type": "text/plain"})
    assert r.status == 415                                     # a cross-site form / no-preflight POST can't drive it
    r = await synth_client.post("/v1/control", data="{nope", headers={"Content-Type": "application/json"})
    assert r.status == 400 and (await r.json())["error"] == "bad_json"


# -- stream_service's /dev/synth proxy --------------------------------------------------------------------------------
def test_synth_control_url():
    assert synth_control_url("ws://127.0.0.1:8711/v1/show") == "http://127.0.0.1:8711/v1/control"
    assert synth_control_url("wss://host:9/v1/show") == "https://host:9/v1/control"
    assert synth_control_url(None) is None and synth_control_url("") is None


@pytest.mark.asyncio
async def test_dev_synth_without_a_show_bus():
    async with TestClient(TestServer(make_app(token=TOKEN, config_dir=REPO_CONFIG))) as c:
        for method in ("GET", "POST"):
            r = await c.request(method, "/dev/synth", json={"bpm": 120})
            assert r.status == 401 and (await r.json())["error"] == "unauthorized"
        r = await c.get("/dev/synth", headers=AUTH)
        body = await r.json()
        assert r.status == 404 and "--show-bus" in body["detail"]


@pytest.mark.asyncio
async def test_dev_synth_proxies_to_show_synth():
    synth = Synth(Song("pop", 124, 7))
    async with TestServer(synth.app()) as ss:
        bus = f"ws://127.0.0.1:{ss.port}/v1/show"
        async with TestClient(TestServer(make_app(token=TOKEN, config_dir=REPO_CONFIG, show_bus=bus))) as c:
            r = await c.get("/dev/synth", headers={"Authorization": "Bearer wrong"})
            assert r.status == 401
            r = await c.get("/dev/synth", headers=AUTH)
            assert r.status == 200 and (await r.json())["song"] == "pop"
            assert r.headers["Cache-Control"] == "no-store"
            r = await c.post("/dev/synth", json={"bpm": 150}, headers=AUTH)
            assert r.status == 200 and (await r.json())["bpm"] == 150
            assert synth.song.bpm == 150
            r = await c.post("/dev/synth", json={"bpm": 999}, headers=AUTH)          # the synth's 400 passes through
            assert r.status == 400 and "40-200" in (await r.json())["detail"]
            r = await c.post("/dev/synth", data=b"{nope", headers={**AUTH, "Content-Type": "application/json"})
            assert r.status == 400 and (await r.json())["error"] == "bad_json"
            r = await c.post("/dev/synth", json=[1], headers=AUTH)
            assert r.status == 400


@pytest.mark.asyncio
async def test_dev_synth_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = make_app(token=TOKEN, config_dir=REPO_CONFIG, show_bus=f"ws://127.0.0.1:{port}/v1/show")
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/dev/synth", headers=AUTH)
        assert r.status == 502 and (await r.json())["error"] == "synth_unreachable"
