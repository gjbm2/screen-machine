"""inputs.py: the Room page's Mic choice (the list from show.yaml + health, the choice, persistence, the routes)."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from show_service.inputs import Inputs

SHOW = """
senders:
  kiosk: {hosts: [k], port: 6980, streams: [room-mic, room-piano], label: Drawing room}
  pc22: {hosts: [p], port: 6984, streams: [pc22-mic]}
  test-audio: {hosts: [localhost], port: 6982, streams: [test-audio], on_demand: true, room_sound: false}
  radio: {hosts: [r], port: 6990, streams: [radio-in], room_sound: false}
"""


def health(active="pc22-mic"):
    return {
        "active_audio": active,
        "senders": {"kiosk": {"streams": ["room-mic", "room-piano"], "on_demand": False},
                    "pc22": {"streams": ["pc22-mic"], "on_demand": False},
                    "test-audio": {"streams": ["test-audio"], "on_demand": True},
                    "radio": {"streams": ["radio-in"], "on_demand": False}},
        "streams": {"pc22-mic": {"kind": "audio", "state": "streaming", "test": False,
                                 "sender": {"dev": "Dell", "devices": ["Dell", "Insta360 Link"]}},
                    "room-piano": {"kind": "midi", "state": "streaming", "test": False},
                    "test-audio": {"kind": "audio", "state": "streaming", "test": True}}}


class FakeRx:
    def __init__(self):
        self.choice, self.hints = "unset", None

    def set_choice(self, s):
        self.choice = s

    def set_device_hints(self, h):
        self.hints = dict(h)


@pytest.fixture
def make(tmp_path):
    show = tmp_path / "show.yaml"
    show.write_text(SHOW)

    def _make(h=None):
        return Inputs(lambda: h if h is not None else health(), show_file=show, state_file=tmp_path / "input.json")
    return _make


def test_lists_room_sound_inputs_only(make):
    got = make().inputs()
    assert [(i["stream"], i["sender"], i["label"], i["state"]) for i in got] == [
        ("room-mic", "kiosk", "Drawing room", "down"), ("pc22-mic", "pc22", "pc22", "streaming")]
    assert got[1]["device"] == "Dell" and got[1]["devices"] == ["Dell", "Insta360 Link"] and got[0]["devices"] == []


def test_choice_applies_persists_and_reloads(make, tmp_path):
    inp, rx = make(), FakeRx()
    inp.attach(rx)
    assert rx.choice is None and rx.hints == {} and inp.choice() is None
    body = inp.put("pc22-mic", "Insta360 Link")
    assert body["choice"] == {"stream": "pc22-mic", "device": "Insta360 Link"} and body["active"] == "pc22-mic"
    assert rx.choice == "pc22-mic" and rx.hints == {"pc22-mic": "Insta360 Link"}
    assert json.loads((tmp_path / "input.json").read_text())["stream"] == "pc22-mic"
    again, rx2 = make(), FakeRx()
    again.attach(rx2)
    assert rx2.choice == "pc22-mic" and rx2.hints == {"pc22-mic": "Insta360 Link"}
    again.put("room-mic")                                     # another stream; pc22-mic keeps its device
    assert rx2.choice == "room-mic" and rx2.hints == {"pc22-mic": "Insta360 Link"}
    again.put("pc22-mic", None)                               # the sender's default
    assert rx2.hints == {}
    again.put(None)                                           # automatic
    assert rx2.choice is None and again.choice() is None


def test_bad_choices_and_a_bad_file(make, tmp_path):
    inp = make()
    for bad in ("room-piano", "test-audio", "radio-in", "nope", 3):
        with pytest.raises(ValueError):
            inp.put(bad)
    with pytest.raises(ValueError):
        inp.put("pc22-mic", "x" * 129)
    (tmp_path / "input.json").write_text("{torn")
    assert make().choice() is None and make().error


def test_mic_for_the_card(make):
    inp = make()
    assert inp.mic() == {"stream": "pc22-mic", "device": "Dell", "chosen": False}
    inp.put("pc22-mic")
    assert inp.mic()["chosen"] is True
    assert make(health(active=None)).mic() is None


def test_routes(make):
    asyncio.run(_routes(make()))


async def _routes(inp):
    app = web.Application()
    inp.routes(app)
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/api/v1/inputs")
        assert r.status == 200 and (await r.json())["choice"] is None
        r = await c.put("/api/v1/input", json={"stream": "pc22-mic", "device": "Dell"})
        assert r.status == 200 and (await r.json())["choice"] == {"stream": "pc22-mic", "device": "Dell"}
        r = await c.put("/api/v1/input", json={"stream": "room-piano"})
        assert r.status == 400 and "isn't one of the room's inputs" in (await r.json())["detail"]
        r = await c.put("/api/v1/input", json={"device": "Dell"})
        assert r.status == 400
