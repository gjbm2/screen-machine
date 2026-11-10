"""Flask-side fixes from the topology audit (10 Nov 2026): a screen's light curve without lat/lon (F1), a site with no
intensity.json (F2), the auto hints on every room with screens (F5), Alexa's "change the scene" moving each room once
(F7) and its targets when the asking device has no closest screen (F8), the e-ink tags on a site without
show_service (F10), and the latency calibration on a site without a kiosk (E11). No network, no live services."""
import asyncio
import json
import os
import sys

import pytest

import smconfig

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# -- F1 / F2: the mask route ----------------------------------------------------------------------------------------
@pytest.fixture
def client():
    from app import app
    app.config["TESTING"] = True
    return app.test_client()


def _dests(monkeypatch, dests):
    import routes.display as display
    import routes.utils as rutils
    fake = lambda *a, **k: dests                      # noqa: E731
    monkeypatch.setattr(display, "_load_json_once", fake)
    monkeypatch.setattr(rutils, "_load_json_once", fake)


def test_mask_without_lat_lon_uses_the_deployment_location(client, monkeypatch):
    _dests(monkeypatch, [{"id": "vj", "intensity_cfg": {"adjust": True}}])
    r = client.get("/api/vj/mask")
    assert r.status_code == 200
    assert 0.0 <= r.get_json()["brightness"] <= 1.0
    from routes.display import mask_latlon
    import config
    assert mask_latlon({"adjust": True}) == (config.INTENSITY_CFG["default_lat"], config.INTENSITY_CFG["default_lon"])
    assert mask_latlon({"lat": 1, "lon": 2}) == (1.0, 2.0)


def test_mask_with_no_intensity_json(client, monkeypatch):
    from routes.lightsensor import intensity_mapper
    monkeypatch.setattr(intensity_mapper, "mapping_data", None)
    monkeypatch.setattr(intensity_mapper, "check_for_updates", lambda: None)
    _dests(monkeypatch, [{"id": "vj", "intensity_cfg": {"adjust": True, "lat": 51.5, "lon": -0.1}}])
    r = client.get("/api/vj/mask")
    assert r.status_code == 200 and "brightness" in r.get_json()
    assert intensity_mapper.get_intensity("living-room", 100.0) is None
    assert intensity_mapper.get_target_group("living-room") is None
    assert client.get("/api/lightsensor/intensity-settings").get_json() == {"sensor_mappings": {}}


# -- F5: the auto hints ---------------------------------------------------------------------------------------------
def test_hints_show_on_the_named_room_else_every_room_with_screens(tmp_path, monkeypatch):
    from routes import room_hints
    f = tmp_path / "rooms.json"
    f.write_text(json.dumps({"_comment": "x", "garden": {"lights": ["light"]}, "front": {"screens": ["fs"]},
                             "back": {"screens": ["bs"]}}))
    monkeypatch.setattr(room_hints, "ROOMS", f)
    assert room_hints.hint_screens({"room": "back"}) == ["bs"]           # finsbury names drawing-room
    assert room_hints.hint_screens({"room": ["front", "back"]}) == ["fs", "bs"]
    assert room_hints.hint_screens({}) == ["fs", "bs"]                   # probe-s4: default_room garden has none
    assert room_hints.hint_screens({"room": "garden"}) == []


def test_finsbury_hints_stay_on_the_drawing_room():
    from routes import room_hints
    assert room_hints.hint_screens(room_hints.load_config()) == ["north-screen", "south-screen"]


# -- F7 / F8: Alexa's room intents ----------------------------------------------------------------------------------
STATUS = {"rooms": [{"id": "drawing-room", "screens": ["north-screen", "south-screen"]},
                    {"id": "lab", "screens": ["devtest"]}],
          "screens": {"north-screen": {"observed": {"layer": "live"}}, "south-screen": {"observed": {"layer": "live"}},
                      "devtest": {"observed": {"layer": "live"}}}}


@pytest.fixture
def room(monkeypatch):
    from routes import room_client as rc
    calls = []
    monkeypatch.setattr(rc, "status", lambda: json.loads(json.dumps(STATUS)))
    monkeypatch.setattr(rc, "room_facts", lambda: {"mode": "party"})

    def send(method, path, body, timeout=None):
        calls.append((method, path, body))
        return True, ""
    monkeypatch.setattr(rc, "_send", send)
    rc._paused.clear()
    return rc, calls


def test_change_the_scene_moves_each_room_once(room):
    rc, calls = room
    rc._paused.update({"south-screen": "party"})               # a voice pick paused south in this mode
    assert rc.next_scene_rooms(["north-screen", "south-screen", "devtest"]) == []
    cues = [(p, b) for m, p, b in calls if p.endswith("/cue")]
    assert cues == [("/api/v1/screens/north-screen/cue", {"to": "auto"}),
                    ("/api/v1/screens/devtest/cue", {"to": "auto"})]
    assert ("PATCH", "/api/v1/screens/south-screen/look", {"auto": True, "source": "voice"}) in calls
    assert rc.rooms_of(["south-screen", "devtest", "north-screen", "loose"]) == [
        ["south-screen", "north-screen"], ["devtest"], ["loose"]]


def test_live_targets_closest_else_default_screens_else_default_room(room, monkeypatch):
    rc, _ = room
    assert rc.live_targets("devtest") == ["devtest"]
    assert rc.live_targets(["north-screen", "south-screen"]) == ["north-screen", "south-screen"]
    assert rc.live_targets(None) == ["north-screen", "south-screen"]      # finsbury's default room, not the lab too
    st = json.loads(json.dumps(STATUS))
    st["screens"]["north-screen"]["observed"]["layer"] = st["screens"]["south-screen"]["observed"]["layer"] = "art"
    monkeypatch.setattr(rc, "status", lambda: st)
    assert rc.live_targets("north-screen") == ["devtest"]                # nothing live near or at home: what is live


def test_alexa_acts_on_the_default_screens_when_no_device_is_mapped():
    from routes.alexa import alexa_room_screens
    subs = {"ALEXA_DEFAULT_SCREENS": ["north-screen", "south-screen"]}
    assert alexa_room_screens("devtest", subs) == "devtest"
    assert alexa_room_screens(None, subs) == ["north-screen", "south-screen"]
    assert alexa_room_screens(None, {}) == []


# -- F10: the e-ink tags without show_service ----------------------------------------------------------------------
def test_dot_never_asks_show_when_show_is_off(monkeypatch):
    import dot_service.__main__ as dot
    real = smconfig.enabled
    monkeypatch.setattr(smconfig, "enabled", lambda c: False if c == "show" else real(c))
    asked = []
    o = dot.DotService.__new__(dot.DotService)
    o.load_config = lambda: None
    o.modes_at, o.modes, o.tags = 1e18, {}, {}

    async def get(url):
        asked.append(url)
        if dot.SHOW in url:
            raise AssertionError(f"asked show: {url}")
        return {"rooms": [], "screens": {}}

    async def soft(url, default=None):
        asked.append(url)
        return default
    o.get, o.soft = get, soft
    asyncio.run(o.tick())
    assert asked == [f"{dot.ROOM}/api/v1/status"]
    assert dot.show_on() is False


# -- E11: latency calibration without a kiosk -----------------------------------------------------------------------
def test_calibration_screen_choice():
    sys.path.insert(0, os.path.join(REPO, "tools", "room", "latency_cal"))
    try:
        import calibrate
    finally:
        sys.path.pop(0)
    assert calibrate.CDP_PORTS == {"north": 9222, "south": 9223}          # finsbury's kiosk
    assert calibrate.pick_screen("south") == "south"
    assert calibrate.pick_screen(None, {"vj": 9222}) == "vj"
    with pytest.raises(SystemExit, match="Pass --screen north or south"):
        calibrate.pick_screen(None)
    with pytest.raises(SystemExit, match="No kiosk screens"):
        calibrate.pick_screen(None, {})
