"""rooms.json -> '<room>:<kind>' groups for modes.json (the owner's one site config), and the shipped files together."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from room_service.adapters import build_adapters
from room_service.room_config import RoomConfigFile
from room_service.roomstate.fakes import FakeLevel

from .test_adapters import Ctl

REPO = Path(__file__).resolve().parents[3]


def adapters(tmp_path):
    controllers = {s: Ctl(s) for s in ("north-screen", "south-screen", "devtest")}
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    return build_adapters(controllers, mask, lambda lvl, msg: None, tmp_path, sonos=False, show=False,
                          blinds=FakeLevel("blinds", {f"ch{n:02d}": 0 for n in range(2, 15)}))


def test_the_shipped_modes_and_rooms_load_together(tmp_path):
    rc = RoomConfigFile(REPO / "config" / "room" / "modes.json", adapters(tmp_path), tmp_path,
                        rooms_path=REPO / "routes" / "data" / "rooms.json")
    cfg = rc.load()
    assert rc.error is None and rc.rooms_error is None, (rc.error, rc.rooms_error)
    assert cfg.groups["drawing-room:blinds"] == ["blinds.ch02", "blinds.ch03", "blinds.ch14"]
    assert cfg.groups["drawing-room:screens"] == ["screens.north-screen", "screens.south-screen"]
    assert cfg.groups["drawing-room:tv"] == ["tv.north-screen", "tv.south-screen"]
    assert "lab:tv" not in cfg.groups and cfg.groups["lab:screens"] == ["screens.devtest"]   # devtest has no TV
    assert cfg.modes["disco"].state["blinds.ch03"].set == 100 and cfg.modes["disco"].state["blinds.ch03"].restore
    assert "blinds.ch03" not in cfg.modes["gallery"].state                # put back by leaving disco, not by a retrieve
    assert rc.check(rc.raw)["ok"]


def test_rooms_groups_follow_the_file_and_survive_a_bad_edit(tmp_path):
    rooms = tmp_path / "rooms.json"
    rooms.write_text(json.dumps({"_comment": "x", "drawing-room": {"blinds": ["ch02", "ch05"], "lights": ["hue.ne"]}}))
    modes = tmp_path / "modes.json"
    modes.write_text(json.dumps({"default": "a", "modes": {"a": {"state": {"drawing-room:blinds": 100}}},
                                 "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}]}))
    rc = RoomConfigFile(modes, adapters(tmp_path), tmp_path, rooms_path=rooms)
    cfg = rc.load()
    assert cfg.groups["drawing-room:blinds"] == ["blinds.ch02", "blinds.ch05"]
    assert "drawing-room:hue" not in cfg.groups                          # lights wait for a Hue adapter
    rooms.write_text("{ not json")
    cfg = rc.load()
    assert rc.rooms_error and cfg.groups["drawing-room:blinds"] == ["blinds.ch02", "blinds.ch05"]   # last good
    assert any(w["code"] == "rooms_json" for w in rc.check(rc.raw)["warnings"])
    clash = {**json.loads(modes.read_text()), "groups": {"drawing-room:blinds": ["blinds.ch09"]}}
    assert not rc.check(clash)["ok"] and rc.check(clash)["errors"][0]["path"] == "/groups/drawing-room:blinds"


def test_every_rooms_list_becomes_groups_without_a_table_of_kinds():
    """c8 review #6: any list key, items as targets, objects of targets or bare ids; the room's own zone; nothing
    read-only, nothing an adapter doesn't have."""
    from room_service.room_config import room_groups
    sonos = FakeLevel("sonos", {"den.volume": 20, "den.playback": "paused", "den.now_playing": {}})
    sonos.value_schema = lambda m: {"readOnly": True} if m.endswith("now_playing") else {}
    hue = FakeLevel("hue", {"a": "off", "b": "off"})
    tv = FakeLevel("tv", {"s1": "art_mode"})
    screens = FakeLevel("screens", {"s1": "art", "s2": "art"})
    ball = FakeLevel("ball", {"main": "stopped", "motor.speed": 1})
    rooms = {"_comment": "x", "den": {"name": "Den", "screens": ["s1", "s2"], "lights": ["hue.a", "hue.b"],
                                      "sensors": ["den-sensor"], "effects": [{"power": "ball.main", "settings": ["ball.motor.speed"]}]}}
    g = room_groups(rooms, [sonos, hue, tv, screens, ball])
    assert g == {"den:screens": ["screens.s1", "screens.s2"], "den:tv": ["tv.s1"],
                 "den:lights": ["hue.a", "hue.b"], "den:hue": ["hue.a", "hue.b"],
                 "den:effects": ["ball.main", "ball.motor.speed"], "den:ball": ["ball.main", "ball.motor.speed"],
                 "den:sonos": ["sonos.den.volume", "sonos.den.playback"]}


def test_describe_orders_subsystems_as_rooms_json_lists_them_settings_last(tmp_path):
    """2f: the page's order comes from config (no table of names): rooms.json's list order, settings last."""
    from room_service.roomstate.coordinator import Coordinator
    ad = adapters(tmp_path)
    for c in ad[0].c.values():                                          # no renderer catalog here
        c.mgr = SimpleNamespace(stream=SimpleNamespace(cached_catalog=lambda: None))
    rc = RoomConfigFile(REPO / "config" / "room" / "modes.json", ad, tmp_path,
                        rooms_path=REPO / "routes" / "data" / "rooms.json")
    c = Coordinator(rc.load(), rc.adapters, tmp_path / "state.json")
    d = c.describe()
    order = d["subsystem_order"]
    assert order.index("screens") < order.index("tv") < order.index("blinds")
    settings = [s for s in order if d["subsystems"][s]["category"] == "setting"]
    assert settings and order[-len(settings):] == settings and d["subsystems"]["screens"]["order"] == 0
