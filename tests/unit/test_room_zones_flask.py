"""Flask's room callers with rooms that have their own modes (rooms.json "own_modes": true; room_service status
"zones"): routes/room_client sends room only for a zone, Alexa routes a mode by the asker's zone, then the site, then
the one zone that has it; the scheduler asks its destination's zone; the events bridge gives a zone's screens their
zone's mode; the hints skip zone rooms. With no zones, no request ever carries a room."""
import json
from datetime import datetime

import pytest

from routes import room_client
from routes.room_events_bridge import diff

SITE_MODES = {"gallery": {"label": "Normal", "voice": "normal"}, "party": {"label": "Party", "voice": "party"},
              "ambient": {"label": "Ambient", "voice": "ambient"}}
LAB_MODES = {"gallery": {"label": "Normal", "voice": "normal"},
             "live-test": {"label": "Live test (devtest)", "voice": "live test"}}
BENCH_MODES = {"bench": {"label": "Bench", "voice": "bench"}, "live-test": {"label": "Live test", "voice": "live test"}}
LAB = {"room": "lab", "name": "Lab", "screens": ["devtest"], "show": True,
       "mode": {"current": "gallery", "last_transition": {"to": "gallery", "source": "web"}}}


def status(zones=None, site="gallery"):
    st = {"mode": {"current": site, "last_transition": {"to": site, "source": "web"}},
          "rooms": [{"id": "drawing-room", "screens": ["north-screen", "south-screen"]},
                    {"id": "lab", "screens": ["devtest"]}],
          "subsystems": {"autopilot": {"believed": "off"}},
          "screens": {s: {"desired": "art", "observed": {"layer": "art"}}
                      for s in ("north-screen", "south-screen", "devtest")}}
    if zones is not None:
        st["zones"] = zones
    return st


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code, self.ok, self.text = body, code, code < 400, json.dumps(body)

    def json(self):
        return self.body


class FakeRoom:
    """room_service's HTTP door, faked at requests: GET status/modes, PUT mode; records every call."""

    def __init__(self, st):
        self.st, self.gets, self.puts = st, [], []
        self.modes = {None: SITE_MODES, "lab": LAB_MODES, "bench": BENCH_MODES}

    def get(self, url, timeout=None):
        self.gets.append(url)
        path = url.split("8700", 1)[-1] if "8700" in url else url
        if path.endswith("/api/v1/status"):
            return Resp(self.st)
        if "/api/v1/modes" in path:
            room = path.split("?room=", 1)[1] if "?room=" in path else None
            if room not in self.modes:
                return Resp({"error": "unknown_room"}, 404)
            return Resp({"modes": self.modes[room]})
        return Resp({"error": "not_found"}, 404)                  # show_service's reads: not part of these tests

    def request(self, method, url, json=None, timeout=None):
        self.puts.append((method, url.rsplit("/api/v1", 1)[-1], json))
        return Resp({"transition_id": "t1"}, 202)


@pytest.fixture
def room(monkeypatch):
    def install(st):
        fake = FakeRoom(st)
        monkeypatch.setattr(room_client, "room_on", lambda: True)
        monkeypatch.setattr(room_client.requests, "get", fake.get)
        monkeypatch.setattr(room_client.requests, "request", fake.request)
        monkeypatch.setattr(room_client, "_modes_cache", {"t": -1e9, "modes": {}, "full": {}})
        monkeypatch.setattr(room_client, "_zones_cache", {"t": -1e9, "zones": []})
        monkeypatch.setattr(room_client, "_zone_modes", {})
        monkeypatch.setattr(room_client, "_has_zone_rooms", lambda: "zones" in st)   # rooms.json as the status says
        return fake
    return install


def test_no_zones_never_sends_a_room(room):
    from routes import scheduler_handlers as H
    fake = room(status())
    assert room_client.zones() == [] and room_client.zone_of_screen("devtest") is None
    assert room_client.set_mode("party", source="voice") == (True, "party")
    ctx, out, now = {"vars": {}}, [], datetime.now()
    for dest in ("north-screen", "devtest"):
        H.handle_set_mode({"action": "set_mode", "mode": "Normal"}, ctx, now, out, dest)
    H.handle_room_state({"action": "room_state"}, ctx, now, out, "devtest")
    assert ctx["vars"]["room_mode"] == "gallery"
    assert room_client.voice_names() == ["normal", "party", "ambient"]
    assert [p[2] for p in fake.puts] == [{"mode": "party", "source": "voice"},
                                         {"mode": "gallery", "source": "schedule:north-screen"},
                                         {"mode": "gallery", "source": "schedule:devtest"}]
    assert not any("room=" in g for g in fake.gets)
    assert "zones" not in room_client.room_facts(status())


def test_set_mode_for_a_zone_sends_its_room(room):
    fake = room(status([LAB]))
    assert room_client.set_mode("Live test", source="voice", room="lab") == (True, "live-test")
    assert fake.puts[-1][2] == {"mode": "live-test", "source": "voice", "room": "lab"}
    assert any(g.endswith("/api/v1/modes?room=lab") for g in fake.gets)
    assert room_client.set_mode("Party", source="voice", room="lab") == (False, "unknown mode 'Party' in lab")
    assert room_client.modes("lab") == {"gallery": "Normal", "live-test": "Live test (devtest)"}
    assert room_client.modes("nope") == {}                       # an unknown room: room_service's 404


def test_zone_helpers_read_status_zones(room):
    room(status([LAB]))
    assert [z["room"] for z in room_client.zones()] == ["lab"]
    assert room_client.zone_of("lab")["name"] == "Lab" and room_client.zone_of("drawing-room") is None
    assert room_client.zone_of_screen("devtest") == "lab" and room_client.zone_of_screen("north-screen") is None
    assert room_client.zone_modes() == {"lab": {"gallery": "Normal", "live-test": "Live test (devtest)"}}
    assert room_client.voice_names() == ["normal", "party", "ambient", "live test"]


def test_voice_mode_resolves_by_the_askers_zone_then_the_site_then_one_zone(room):
    room(status([LAB]))
    r = room_client.resolve_voice_mode
    assert r("normal", "devtest") == ("gallery", "lab")          # the asker's zone first
    assert r("normal", "north-screen") == ("gallery", None)      # the site
    assert r("normal", ["north-screen", "south-screen"]) == ("gallery", None)
    assert r("live test", "north-screen") == ("live-test", "lab")   # the one zone that has it
    assert r("live test", None) == ("live-test", "lab")
    assert r("party", "devtest") == ("party", None)              # not the zone's: the site's
    assert r("nope", "devtest") == (None, None)


def test_a_name_two_other_zones_share_is_not_guessed(room):
    bench = {**LAB, "room": "bench", "name": "Bench", "screens": ["bench-screen"]}
    room(status([LAB, bench]))
    assert room_client.resolve_voice_mode("live test", "north-screen") == (None, None)
    assert room_client.resolve_voice_mode("live test", "bench-screen") == ("live-test", "bench")


def test_scheduler_asks_its_destinations_zone(room):
    from routes import scheduler_handlers as H
    lab = {**LAB, "mode": {"current": "live-test", "last_transition": {"to": "live-test", "source": "voice"}}}
    fake = room(status([lab]))
    ctx, out, now = {"vars": {}}, [], datetime.now()
    H.handle_set_mode({"action": "set_mode", "mode": "Normal"}, ctx, now, out, "devtest")
    H.handle_set_mode({"action": "set_mode", "mode": "Party"}, ctx, now, out, "north-screen")
    assert [p[2] for p in fake.puts] == [{"mode": "gallery", "source": "schedule:devtest", "room": "lab"},
                                         {"mode": "party", "source": "schedule:north-screen"}]
    H.handle_room_state({"action": "room_state"}, ctx, now, out, "devtest")
    assert ctx["vars"]["room_mode"] == "live-test"
    H.handle_room_state({"action": "room_state"}, ctx, now, out, "north-screen")
    assert ctx["vars"]["room_mode"] == "gallery"


def test_bridge_sends_a_zones_mode_only_to_its_screens():
    labels = {k: v["label"] for k, v in SITE_MODES.items()}
    zl = {"lab": {k: v["label"] for k, v in LAB_MODES.items()}}
    a = room_client.room_facts(status([LAB]))
    assert a["zones"] == {"lab": {"mode": "gallery", "mode_source": "web", "screens": ["devtest"]}}
    first = [(s, p) for s, k, p in diff(None, a, labels, zl) if k == "_room_mode"]
    assert [(s, p.get("room")) for s, p in first] == [("north-screen", None), ("south-screen", None), ("devtest", "lab")]
    # the site switches: the zone's screen hears nothing
    b = room_client.room_facts(status([LAB], site="party"))
    assert [s for s, k, _ in diff(a, b, labels, zl) if k == "_room_mode"] == ["north-screen", "south-screen"]
    # the zone switches: only its screen hears, with the zone's label and room
    lab = {**LAB, "mode": {"current": "live-test", "last_transition": {"to": "live-test", "source": "voice"}}}
    c = room_client.room_facts(status([lab], site="party"))
    assert diff(b, c, labels, zl) == [("devtest", "_room_mode", {
        "mode": "live-test", "label": "Live test (devtest)", "from": "gallery", "source": "voice", "initial": False,
        "room": "lab"})]


def test_hints_skip_rooms_with_their_own_modes(tmp_path, monkeypatch):
    from routes import room_hints
    rooms = tmp_path / "rooms.json"
    rooms.write_text(json.dumps({"_c": "x", "drawing-room": {"screens": ["north-screen", "south-screen"]},
                                 "lab": {"screens": ["devtest"], "own_modes": True}}))
    monkeypatch.setattr(room_hints, "ROOMS", rooms)
    assert room_hints.hint_screens({}) == ["north-screen", "south-screen"]
    assert room_hints.hint_screens({"room": ["drawing-room", "lab"]}) == ["north-screen", "south-screen"]
    assert room_hints.hint_screens({"room": "lab"}) == []
    rooms.write_text(json.dumps({"drawing-room": {"screens": ["north-screen"]}, "lab": {"screens": ["devtest"]}}))
    assert room_hints.hint_screens({}) == ["north-screen", "devtest"]        # no zones: as before


def test_alexa_routes_a_mode_to_the_zone_that_has_it(room, monkeypatch):
    from app import app
    import routes.openai
    from routes import alexa
    fake = room(status([LAB]))
    asked = {"mode": "live test"}

    def fake_prompt(user_prompt, system_prompt, schema, task=None):
        assert "live test" in system_prompt                         # the zone's voice names reach the prompt
        return {"intent": "set_mode", "data": dict(asked), "response_ssml": "<speak>ok</speak>"}

    monkeypatch.setattr(routes.openai, "openai_prompt", fake_prompt)
    data = {"request": {"type": "IntentRequest", "intent": {"name": "unspecified",
                                                            "slots": {"utterance": {"value": "live test"}}}}}
    with app.test_request_context("/api/alexa", method="POST"):
        alexa.process(data)
    assert fake.puts[-1][1] == "/mode" and fake.puts[-1][2]["room"] == "lab"
    assert fake.puts[-1][2]["mode"] == "live-test"
    asked["mode"] = "party"
    with app.test_request_context("/api/alexa", method="POST"):
        alexa.process(data)
    assert fake.puts[-1][2] == {"mode": "party", "source": "voice", "reason": "Alexa: live test"}


REAL_HAS_ZONE_ROOMS = room_client._has_zone_rooms


def test_no_own_modes_room_means_no_status_read(room, tmp_path, monkeypatch):
    """Alexa's set_mode and prompt, and the scheduler, ask zones(): with no own_modes room in rooms.json that is []
    at once, with no GET /api/v1/status (a hung room_service delays them no more than before). Opting a room in is
    seen on the next call (rooms.json's mtime)."""
    import smconfig
    from routes import scheduler_handlers as H
    dep = tmp_path / "t"
    dep.mkdir()
    (dep / "deployment.yaml").write_text("id: t\n")
    rooms = {"drawing-room": {"screens": ["north-screen"]}, "lab": {"screens": ["devtest"]}}
    (dep / "rooms.json").write_text(json.dumps(rooms))
    monkeypatch.setattr(smconfig, "DEPLOYMENTS_DIR", tmp_path)
    monkeypatch.setenv("SM_DEPLOYMENT", "t")
    monkeypatch.delenv("ROOM_SERVICE_URL", raising=False)
    fake = room(status([LAB]))                       # even a room_service that says zones isn't asked
    monkeypatch.setattr(room_client, "_has_zone_rooms", REAL_HAS_ZONE_ROOMS)
    monkeypatch.setattr(room_client, "_local_zones", {"key": None, "has": False})
    assert room_client.zones() == [] and room_client.zone_of_screen("devtest") is None
    assert room_client.voice_names() == ["normal", "party", "ambient"]
    H.handle_set_mode({"action": "set_mode", "mode": "Normal"}, {"vars": {}}, datetime.now(), [], "devtest")
    assert not any(g.endswith("/api/v1/status") for g in fake.gets), fake.gets
    assert fake.puts[-1][2] == {"mode": "gallery", "source": "schedule:devtest"}
    rooms["lab"]["own_modes"] = True
    (dep / "rooms.json").write_text(json.dumps(rooms, indent=1))
    assert [z["room"] for z in room_client.zones()] == ["lab"]
    assert any(g.endswith("/api/v1/status") for g in fake.gets)
