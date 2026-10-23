"""mic.py: the mic on demand (README S5.16). Who wants it, Mute, which streams capture (the Mic choice and its
fallback), what the receiver and the rings are told, the words, persistence and the routes. Fakes only: no sockets
but aiohttp's test server, every file in tmp."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from show_service import mic as M
from show_service.inputs import Inputs

SHOW = """
senders:
  kiosk: {hosts: [k], port: 6980, streams: [room-mic, room-piano], label: Drawing room}
  pc22: {hosts: [p], port: 6984, streams: [pc22-mic], label: PC22}
  test-audio: {hosts: [localhost], port: 6982, streams: [test-audio], on_demand: true, room_sound: false}
"""


def health() -> dict:
    return {"active_audio": None,
            "senders": {"kiosk": {"streams": ["room-mic", "room-piano"], "on_demand": False},
                        "pc22": {"streams": ["pc22-mic"], "on_demand": False},
                        "test-audio": {"streams": ["test-audio"], "on_demand": True}},
            "streams": {"room-mic": {"kind": "audio", "state": "off", "test": False},
                        "pc22-mic": {"kind": "audio", "state": "off", "test": False},
                        "room-piano": {"kind": "midi", "state": "streaming", "test": False}}}


class FakeRx:
    def __init__(self) -> None:
        self.calls: list = []
        self.choice = None

    def set_off_streams(self, names, covered=None):
        self.calls.append((sorted(names), sorted(covered or ())))

    def set_choice(self, s):
        self.choice = s

    def set_device_hints(self, h):
        pass


class FakeRings:
    def __init__(self) -> None:
        self.offs: list = []

    def set_off(self, streams):
        self.offs.append(sorted(streams))


class Env:
    def __init__(self, tmp_path, *, mic_file=None) -> None:
        self.now = 100.0
        self.h = health()
        show = tmp_path / "show.yaml"
        show.write_text(SHOW)
        self.inputs = Inputs(lambda: self.h, show_file=show, state_file=tmp_path / "input.json")
        self.rx = FakeRx()
        self.inputs.attach(self.rx)
        self.file = mic_file or tmp_path / "mic.json"
        self.mic = M.Mic(self.inputs, lambda: self.h, state_file=self.file, mono=lambda: self.now)
        self.auto = None
        self.room = None
        self.notes: list = []
        self.mic.set_auto(lambda: self.auto)
        self.mic.set_room(lambda: self.room)
        self.mic.set_notify(lambda k, p: self.notes.append(k))
        self.rings = FakeRings()
        self.mic.attach(self.rx)
        self.mic.set_rings(self.rings)

    def state(self, stream: str, state: str) -> None:
        self.h["streams"][stream]["state"] = state


ALL = ["pc22-mic", "room-mic", "room-piano", "test-audio"]


def test_off_until_something_needs_it_and_the_receiver_is_told_once(tmp_path):
    e = Env(tmp_path)
    b = e.mic.state()
    assert b == {"on": False, "muted": False, "wanted": False, "switch": False, "why": "Off: nothing needs it",
                 "claims": [], "mode": False, "streams": {"capturing": [], "off": ["room-mic", "pc22-mic"]}, "error": None}
    assert e.rx.calls == [(["pc22-mic", "room-mic"], ALL)]          # MIDI and test streams are never in "off"
    assert e.rings.offs[-1] == ["pc22-mic", "room-mic"]
    for _ in range(5):
        e.now += 0.5
        e.mic.evaluate()
    assert len(e.rx.calls) == 1                                    # only changes reach the receiver


def test_the_mode_claim_turns_it_on_and_mute_always_wins(tmp_path):
    e = Env(tmp_path)
    e.room = {"current": "party", "target": None, "modes": {"party": {"label": "Party"}}}
    b = e.mic.put(mode=True)
    assert b["on"] is True and b["wanted"] is True and b["why"] == "Listening: Party follows the music"
    assert b["claims"] == [{"by": "mode", "label": "Party"}] and b["mode"] is True
    assert b["streams"] == {"capturing": ["room-mic", "pc22-mic"], "off": []}       # no choice: every room mic
    assert e.rx.calls[-1] == ([], ALL) and e.rings.offs[-1] == [] and "card" in e.notes
    b = e.mic.put(muted=True)
    assert b["on"] is False and b["why"] == "Switched off: Party can't follow the music"
    assert e.rx.calls[-1][0] == ["pc22-mic", "room-mic"]
    e.room = None                                                  # no room view: "this mode"
    assert e.mic.evaluate()["why"] == "Switched off: this mode can't follow the music"
    b = e.mic.put(mode=False)
    assert b["why"] == "Switched off" and b["wanted"] is False and b["claims"] == []
    b = e.mic.put(muted=False)
    assert b["why"] == "Off: nothing needs it"
    assert json.loads(e.file.read_text()) == {"v": 1, "muted": False, "mode": False, "switch": False}


def test_auto_on_wants_it_live(tmp_path):
    e = Env(tmp_path)
    e.auto = True
    b = e.mic.evaluate()
    assert b["on"] is True and b["why"] == "Listening for Auto" and b["claims"] == [{"by": "auto", "label": "Auto"}]
    e.room = {"current": "piano", "target": "party", "modes": {"party": {"label": "Party"}, "piano": {"label": "P"}}}
    b = e.mic.put(mode=True)
    assert b["why"] == "Listening for Auto and Party"               # a transition: the mode being entered
    assert [c["by"] for c in b["claims"]] == ["auto", "mode"]
    b = e.mic.put(muted=True)
    assert b["why"] == "Switched off: Auto and Party can't hear the room"
    e.mic.put(mode=False)
    assert e.mic.evaluate()["why"] == "Switched off: Auto can't hear the room"
    e.mic.put(muted=False)
    e.auto = None                                                  # unknown (the room not reached yet): no claim
    assert e.mic.evaluate()["on"] is False
    e.auto = "yes"                                                 # not a bool: no claim
    assert e.mic.evaluate()["on"] is False


def test_a_mic_choice_captures_alone_and_falls_back_after_10_s(tmp_path):
    e = Env(tmp_path)
    e.inputs.put("pc22-mic")
    e.mic.put(mode=True)
    assert e.mic.state()["streams"] == {"capturing": ["pc22-mic"], "off": ["room-mic"]}
    assert e.rx.calls[-1][0] == ["room-mic"]
    e.now += 9.0
    assert e.mic.evaluate()["streams"]["capturing"] == ["pc22-mic"]
    e.now += 1.5                                                   # not live for 10 s: every room mic
    b = e.mic.evaluate()
    assert b["streams"] == {"capturing": ["room-mic", "pc22-mic"], "off": []} and e.mic.fallback is True
    e.state("pc22-mic", "streaming")                               # live: alone again
    assert e.mic.evaluate()["streams"]["capturing"] == ["pc22-mic"] and e.mic.fallback is False
    e.now += 8.0
    e.state("pc22-mic", "down")                                    # it goes away: 10 s from when it was last live
    assert e.mic.evaluate()["streams"]["capturing"] == ["pc22-mic"]
    e.now += 2.5
    assert e.mic.evaluate()["streams"]["capturing"] == ["room-mic", "pc22-mic"]
    e.state("pc22-mic", "quiet")                                   # quiet counts as live
    assert e.mic.evaluate()["streams"]["capturing"] == ["pc22-mic"]
    e.inputs.put("room-mic")                                       # a new choice: its own 10 s
    e.now += 1.0
    assert e.mic.evaluate()["streams"] == {"capturing": ["room-mic"], "off": ["pc22-mic"]}
    e.inputs.put(None)                                             # automatic: all of them
    assert e.mic.evaluate()["streams"]["capturing"] == ["room-mic", "pc22-mic"]


def test_a_choice_that_is_not_an_input_captures_every_room_mic(tmp_path):
    e = Env(tmp_path)
    e.inputs._stream = "gone-mic"                                  # (its sender was removed from show.yaml)
    e.mic.put(mode=True)
    assert e.mic.state()["streams"]["capturing"] == ["room-mic", "pc22-mic"]


def test_a_stream_claim_captures_its_stream_as_well_and_mute_wins(tmp_path):
    e = Env(tmp_path)
    e.inputs.put("pc22-mic")
    b = e.mic.claim_stream("room-mic", by="test", label="Test piano", on=True)
    assert b["on"] is True and b["wanted"] is True and b["why"] == "Listening for Test piano"
    assert b["claims"] == [{"by": "test", "label": "Test piano", "stream": "room-mic"}]
    assert b["streams"] == {"capturing": ["room-mic"], "off": ["pc22-mic"]}     # only it: nothing else wants the mic
    assert e.rx.calls[-1][0] == ["pc22-mic"] and e.inputs.choice()["stream"] == "pc22-mic"   # the choice stays
    e.auto = True                                                  # Auto too: the choice, and the claim as well
    b = e.mic.evaluate()
    assert b["streams"] == {"capturing": ["pc22-mic", "room-mic"], "off": []} and b["why"] == "Listening for Auto"
    assert [c["by"] for c in b["claims"]] == ["auto", "test"]
    b = e.mic.put(muted=True)                                      # Mute wins over a claim too
    assert b["on"] is False and b["streams"]["capturing"] == [] and e.rx.calls[-1][0] == ["pc22-mic", "room-mic"]
    e.auto = None
    assert e.mic.evaluate()["why"] == "Switched off: Test piano can't hear the room"
    e.mic.put(muted=False)
    b = e.mic.claim_stream("room-mic", by="test", label="Test piano", on=False)
    assert b["on"] is False and b["claims"] == [] and b["why"] == "Off: nothing needs it"
    b = e.mic.claim_stream("test-audio", by="test", label="Test piano", on=True)   # not a room mic: nothing captures
    assert b["on"] is False and b["streams"]["capturing"] == []
    assert b["why"] == "Off: Test piano wants a mic the room doesn't have"


def test_the_mic_turning_on_restarts_the_choice_clock(tmp_path):
    e = Env(tmp_path)
    e.inputs.put("pc22-mic")
    e.now += 60.0                                                  # off for a minute: no fallback while off
    e.mic.put(mode=True)
    assert e.mic.state()["streams"]["capturing"] == ["pc22-mic"]


def test_persistence_and_a_bad_file(tmp_path):
    e = Env(tmp_path)
    e.mic.put(muted=True, mode=True)
    again = Env(tmp_path, mic_file=e.file)
    assert again.mic.muted is True and again.mic.mode is True and again.mic.state()["on"] is False
    assert again.rx.calls[0][0] == ["pc22-mic", "room-mic"]       # muted from the first decision on
    for bad in ("{torn", json.dumps([1]), json.dumps({"v": 1, "muted": "yes"})):
        e.file.write_text(bad)
        b = Env(tmp_path, mic_file=e.file).mic
        assert b.muted is False and b.mode is False and b.error and b.state()["error"] == b.error
    ok = Env(tmp_path, mic_file=e.file)
    ok.mic.put(muted=False)                                        # a good save clears the error
    assert ok.mic.error is None and json.loads(e.file.read_text())["v"] == 1


def test_a_failing_receiver_or_rings_never_raise(tmp_path):
    e = Env(tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("boom")
    e.rx.set_off_streams = boom
    e.rings.set_off = boom
    assert e.mic.put(mode=True)["on"] is True                      # logged, counted, still decided
    e.mic.set_auto(boom)
    e.mic.set_room(boom)
    assert e.mic.evaluate()["on"] is True


def test_put_validation():
    m = M.Mic(None, lambda: {})
    with pytest.raises(ValueError):
        m.put()
    with pytest.raises(ValueError):
        m.put(muted="yes")
    with pytest.raises(ValueError):
        m.put(mode=1)


def test_why_words():
    w = M.why_words
    assert w(True, False, True, False, None) == "Listening for Auto"
    assert w(True, False, False, True, "Party") == "Listening: Party follows the music"
    assert w(True, False, True, True, None) == "Listening for Auto and this mode"
    assert w(False, False, False, False, None) == "Off: nothing needs it"
    assert w(False, True, False, False, None) == "Switched off"
    assert w(True, False, False, False, None, side=["Test piano"]) == "Listening for Test piano"


def test_the_thread_wakes_at_once(tmp_path):
    e = Env(tmp_path)
    e.mic.start()
    try:
        n = e.mic.evaluations
        e.auto = True
        e.mic.wake()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and e.mic.state()["on"] is not True:
            time.sleep(0.01)
        assert e.mic.state()["on"] is True and e.mic.evaluations > n
    finally:
        e.mic.stop()
    assert e.mic._thread is None


def test_routes(tmp_path):
    asyncio.run(_routes(Env(tmp_path)))


async def _routes(e):
    app = web.Application()
    e.mic.routes(app)
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/api/v1/mic")
        b = await r.json()
        assert r.status == 200 and b["on"] is False and set(b) == {"on", "muted", "wanted", "switch", "why", "claims",
                                                                  "mode", "streams", "error"}
        r = await c.put("/api/v1/mic", json={"mode": True})
        assert r.status == 200 and (await r.json())["on"] is True
        r = await c.put("/api/v1/mic", json={"muted": True})
        b = await r.json()
        assert r.status == 200 and b["on"] is False and b["why"] == "Switched off: this mode can't follow the music"
        for bad in ({}, {"muted": "yes"}, {"mode": None}, [1]):
            r = await c.put("/api/v1/mic", json=bad)
            assert r.status == 400 and set(await r.json()) == {"error", "detail"}
        r = await c.put("/api/v1/mic", data=b"not json")
        assert r.status == 400


def test_inputs_that_fail_turn_every_possible_mic_off(tmp_path):
    e = Env(tmp_path)
    e.mic.put(mode=True)
    assert e.rx.calls[-1][0] == []

    def boom(h=None):
        raise RuntimeError("show.yaml")
    e.inputs.inputs = boom
    b = e.mic.evaluate()
    assert b["on"] is False and e.rx.calls[-1][0] == ["pc22-mic", "room-mic"]     # never MIDI or a test stream
    assert b["wanted"] is True and "can't be listed" in b["why"]
    assert e.rings.offs[-1] == ["pc22-mic", "room-mic"]


def test_the_room_mic_switch_turns_it_on_by_itself(tmp_path):
    """Owner, 24 Oct: "I turned it on (so it delivers music data) but it's refusing to enable unless I turn auto on"."""
    e = Env(tmp_path)
    b = e.mic.put(switch=True)
    assert b["on"] is True and b["switch"] is True and b["muted"] is False and b["why"] == "Listening: switched on"
    assert b["claims"] == [{"by": "switch", "label": "Room mic switch"}]
    assert b["streams"]["capturing"] == ["room-mic", "pc22-mic"]
    b = e.mic.put(switch=False)
    assert b["on"] is False and b["muted"] is True and b["switch"] is False and b["claims"] == []
    assert json.loads(e.file.read_text()) == {"v": 1, "muted": True, "mode": False, "switch": False}


def test_an_old_unmuted_file_reads_as_switched_on(tmp_path):
    (tmp_path / "mic.json").write_text(json.dumps({"v": 1, "muted": False, "mode": False}))
    e = Env(tmp_path)
    assert e.mic.switch is True
