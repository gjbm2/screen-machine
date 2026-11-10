"""The "room" capability off (a deployment without room_service): routes/room_client refuses every call without
opening a socket and says why; the scheduler's room actions log that reason; Alexa's prompt skips the room."""
import socket
from datetime import datetime

import pytest
import requests

import smconfig
from routes import room_client


@pytest.fixture
def room_off(monkeypatch):
    """smconfig says room is off; any socket or requests call fails the test."""
    real = smconfig.enabled
    monkeypatch.setattr(smconfig, "enabled", lambda cap: False if cap == "room" else real(cap))
    opened = []

    def no_net(*a, **k):
        opened.append(a)
        raise AssertionError(f"network call with room off: {a!r}")

    monkeypatch.setattr(socket, "create_connection", no_net)
    monkeypatch.setattr(socket.socket, "connect", no_net)
    monkeypatch.setattr(requests, "get", no_net)
    monkeypatch.setattr(requests, "request", no_net)
    monkeypatch.setattr(room_client, "_modes_cache", {"t": -1e9, "modes": {}, "full": {}})
    monkeypatch.setattr(room_client, "_lib_cache", {"t": -1e9, "lib": None, "rt": -1e9, "ratings": {}})
    return opened


def test_reads_return_the_down_shape_without_a_socket(room_off):
    assert room_client.room_on() is False
    assert room_client.status() is None
    assert room_client.modes() == {} and room_client.modes_cached() == {} and room_client.voice_modes() == {}
    assert room_client.auto() is None
    assert room_client.library() == {} and room_client.find("effects", "fractal") is None
    assert room_client.live_targets("north-screen") == [] and room_client.effects("north-screen") == []
    f = room_client.room_facts()
    assert f["mode"] is None and f["auto"] is None and f["screens"] == {}
    ls = room_client.listening_state()
    assert ls["auto"] is None and ls["ai"] is None and ls["mic"]["on"] is None
    assert room_off == []


def test_writes_are_refused_with_the_reason(room_off):
    off = (False, "room is off in this deployment")
    assert room_client.ROOM_OFF == off[1]
    assert room_client.set_mode("party", source="voice") == off
    assert room_client.set_auto(True) == off
    assert room_client.set_listening(False) == off
    assert room_client.cue_scene("north-screen", "auto") == off
    assert room_client.set_effects("north-screen", []) == off
    assert room_client.pick_scene("north-screen", "x") == off
    assert room_client.next_scene("north-screen") == off
    assert room_off == []


def test_scheduler_room_actions_log_the_refusal(room_off):
    from routes import scheduler_handlers as H
    ctx, out, now = {"vars": {"room_mode": "stale"}}, [], datetime.now()
    assert H.handle_set_mode({"action": "set_mode", "mode": "Party"}, ctx, now, out, "north-screen") is False
    assert H.handle_set_auto({"action": "set_auto", "on": True}, ctx, now, out, "north-screen") is False
    assert H.handle_room_state({"action": "room_state"}, ctx, now, out, "north-screen") is False
    assert ctx["vars"]["room_mode"] is None and ctx["vars"]["room_auto"] is None and ctx["vars"]["screen_layer"] is None
    text = "\n".join(out)
    assert text.count("room is off in this deployment") == 3, text
    assert room_off == []


def test_room_on_in_finsbury():
    assert room_client.room_on() is True


def test_alexa_skips_the_room_and_refuses_room_intents(room_off, monkeypatch):
    """The prompt gets no room facts (nothing asked of room_service) and a room intent gets a spoken refusal."""
    from app import app
    import routes.openai
    from routes import alexa
    prompts = []

    def fake_prompt(user_prompt, system_prompt, schema, task=None):
        prompts.append(system_prompt)
        return {"intent": "set_mode", "data": {"mode": "party"}, "response_ssml": "<speak>ok</speak>"}

    monkeypatch.setattr(routes.openai, "openai_prompt", fake_prompt)
    monkeypatch.setattr(room_client, "set_mode", lambda *a, **k: pytest.fail("set_mode called with room off"))
    data = {"request": {"type": "IntentRequest", "intent": {"name": "unspecified",
                                                            "slots": {"utterance": {"value": "party mode"}}}}}
    with app.test_request_context("/api/alexa", method="POST"):
        out = alexa.process(data)
    assert prompts and "(none: the room is off here)" in prompts[0]
    assert "room controls are switched off" in str(out)
    assert room_off == []
