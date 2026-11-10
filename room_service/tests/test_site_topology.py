"""Topology audit, wave 1 (room area): what room_service does on a site unlike finsbury, with finsbury unchanged.

RS-1   a screen the site knows without a live block names nothing (a warning), a typo stays an error, a site with no
       live screen loads its modes (no fallback); validate_site_modes runs the same check without a service.
RS-4 / E7  brightness.lamps: offered only with lamps that follow the music (Hue or show_service's lights), dims
       show_service's lights through its rig level, and says so when there is nothing to dim.
RS-5 / C1  rig lights are shared out between rooms by one rule (light_rooms); a light is never in two rooms.
RS-6   every rooms.json list gives a known group even when empty, with a warning naming the room.
RS-7   an art-only screen's control socket is closed with its own reason.
RS-8   the fake Art-Net gives every patched screen its own values, by order, finsbury's two unchanged.
RS-9   screen, page and diag members are labelled with the screen's name; page/diag/sensor_mask words.
RS-10  a live block with no profile gets one by size at 30 fps, never 4K60.
Never touches the real show_service or the room API."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

import smconfig
from room_service.adapters.lights import LightsAdapter
from room_service.adapters.room import (NO_LAMPS, BrightnessAdapter, DiagAdapter, PageAdapter, SensorMaskAdapter,
                                        screens_follow_a_sensor)
from room_service.adapters.screens import ScreensAdapter
from room_service.config import default_profile, known_screens, screens_from_destinations
from room_service.dmx.fake_artnet import screen_variants
from room_service.room_config import derive_room_groups, light_rooms, room_groups, validate_site_modes
from room_service.roomstate.contract import Placeholder
from room_service.roomstate.fakes import FakeLevel
from room_service.roomstate.model import ConfigError, RoomConfig, validate

from .roomstate.test_adapters import Ctl


def modes(state: dict) -> dict:
    return {"default": "a", "modes": {"a": {"state": state}}, "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}]}


def screen_adapters(tmp_path, live: list[str], known: dict[str, str]):
    c = {s: Ctl(s) for s in live}
    return [ScreensAdapter(c, known), PageAdapter(c, tmp_path, known=known), DiagAdapter(c, tmp_path, known=known)]


KNOWN = {"north-screen": "North screen", "south-screen": "South screen", "devtest": "Dev test"}


# -- RS-1 -------------------------------------------------------------------------------------------------------------

def test_a_known_screen_without_a_live_block_names_nothing_and_says_why(tmp_path):
    ad = screen_adapters(tmp_path, ["north-screen", "south-screen"], KNOWN)
    raw = modes({"screens.devtest": "art", "page.devtest.timed_reload": False, "diag.devtest": True,
                 "screens.north-screen": "art"})
    cfg = RoomConfig(raw, ad)                                    # loads: no fallback
    assert list(cfg.modes["a"].state) == ["screens.north-screen"]
    warns = [w for w in cfg.lint() if w["code"] == "names_nothing"]
    assert {w["path"] for w in warns} == {"/modes/a/state/screens.devtest", "/modes/a/state/page.devtest.timed_reload",
                                          "/modes/a/state/diag.devtest"}
    assert all(w["message"].startswith("Dev test has no live block") for w in warns)
    with pytest.raises(ConfigError, match="matches no target"):  # a typo stays an error
        RoomConfig(modes({"screens.nroth-screen": "art"}), ad)
    with pytest.raises(ConfigError, match="matches no target"):  # so does a member a live screen doesn't have
        RoomConfig(modes({"page.devtest.bogus": True}), ad)


def test_a_site_with_no_live_screen_loads_its_modes(tmp_path):
    ad = screen_adapters(tmp_path, [], {"vj": "VJ screen"})
    cfg = RoomConfig(modes({"screens": "art", "page": True, "diag": False}), ad)
    assert cfg.modes["a"].state == {}
    assert [w["message"] for w in cfg.lint() if w["code"] == "names_nothing"][0].startswith("No screen here can go live")


def test_validate_site_modes_checks_what_room_service_would(tmp_path):
    dep = smconfig.load("finsbury")
    raw = json.loads(dep.path("room", "modes.json").read_text())
    dests, rooms = smconfig.destinations(dep), smconfig.rooms(dep)
    assert validate_site_modes(raw, rooms, dests) == []
    no_devtest = [{k: v for k, v in d.items() if not (d["id"] == "devtest" and k == "live")} for d in dests]
    assert validate_site_modes(raw, rooms, no_devtest) == []      # only warnings now
    assert any(i["code"] == "names_nothing" for i in validate_site_modes(raw, rooms, no_devtest, warnings=True))
    bad = {**raw, "modes": {**raw["modes"], "x": {"state": {"screens.nroth-screen": "art"}}}}
    assert [i["path"] for i in validate_site_modes(bad, rooms, dests)] == ["/modes/x/state/screens.nroth-screen"]
    assert validate(bad, []) == []                                 # what smconfig check ran before: blind to it


# -- RS-4 / E7 ---------------------------------------------------------------------------------------------------------

class FakeShow:
    """show_service's lamps brightness (the contract in BrightnessAdapter's docstring)."""

    def __init__(self, route: bool = True) -> None:
        self.level, self.puts, self.route = 1.0, [], route

    async def get(self, request):
        return web.json_response({"lamps": self.level, "fixtures": 1})

    async def put(self, request):
        body = await request.json()
        v = body.get("lamps")
        if not isinstance(v, (int, float)) or not 0 <= v <= 1:
            return web.json_response({"error": "bad_level"}, status=400)
        self.puts.append(v)
        self.level = float(v)
        return web.json_response({"lamps": self.level, "fixtures": 1})

    async def __aenter__(self):
        app = web.Application()
        if self.route:
            app.router.add_get("/api/v1/brightness", self.get)
            app.router.add_put("/api/v1/brightness", self.put)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")
        return self

    async def __aexit__(self, *a):
        await self.server.close()


def rig(url: str, *ids: str):
    return SimpleNamespace(url=url, members=list(ids))


def test_no_lamps_no_lamps_member(tmp_path):
    a = BrightnessAdapter({}, None, tmp_path)
    assert a.members == ["screens"]
    cfg = RoomConfig(modes({"brightness.lamps": 40, "brightness.screens": 60}), [a])
    assert list(cfg.modes["a"].state) == ["brightness.screens"]
    assert any(w["code"] == "names_nothing" and "no lamps that follow the music" in w["message"] for w in cfg.lint())
    out = asyncio.run(a.apply({"lamps": 40}))
    assert out.status == "unconfirmed" and out.members["lamps"].detail == NO_LAMPS and not out.members["lamps"].retry
    assert BrightnessAdapter({}, None, tmp_path, rig=rig("http://x")).members == ["screens"]   # no lights registered


def test_finsbury_hue_path_is_unchanged_and_sends_nothing_to_show_service(tmp_path):
    hue = SimpleNamespace(level=1.0, patch=SimpleNamespace(hue={}))
    a = BrightnessAdapter({}, hue, tmp_path, rig=rig(f"http://127.0.0.1:{unused_port()}"))   # nothing listens
    assert a.members == ["screens", "lamps"]
    out = asyncio.run(a.apply({"lamps": 35}))
    assert out.status == "ok" and hue.level == 0.35


async def test_lamps_dim_show_services_lights_too(tmp_path):
    async with FakeShow() as show:
        hue = SimpleNamespace(level=1.0, patch=SimpleNamespace(hue={}))
        a = BrightnessAdapter({}, hue, tmp_path, rig=rig(show.url, "par1"))
        out = await a.apply({"lamps": 40})
        assert out.status == "ok" and hue.level == 0.4 and show.puts == [0.4]
        lights_only = BrightnessAdapter({}, None, tmp_path, rig=rig(show.url, "par1"))
        assert lights_only.members == ["screens", "lamps"]
        assert (await lights_only.apply({"lamps": 20})).status == "ok" and show.level == 0.2


async def test_keep_in_step_puts_the_level_back_after_a_show_service_restart(tmp_path):
    async with FakeShow() as show:
        a = BrightnessAdapter({}, None, tmp_path, rig=rig(show.url, "par1"))
        a.RIG_CHECK_S = 0.05
        a.values["lamps"] = 30
        task = asyncio.create_task(a.keep_in_step())
        try:
            for _ in range(100):
                await asyncio.sleep(0.02)
                if show.level == 0.3:
                    break
            assert show.level == 0.3
            show.level = 1.0                                # show_service restarted without it
            for _ in range(100):
                await asyncio.sleep(0.02)
                if show.level == 0.3:
                    break
            assert show.level == 0.3
        finally:
            task.cancel()


async def test_show_service_without_the_route_is_unconfirmed(tmp_path):
    async with FakeShow(route=False) as show:
        a = BrightnessAdapter({}, None, tmp_path, rig=rig(show.url, "par1"))
        out = await a.apply({"lamps": 50})
        assert out.members["lamps"].status == "unconfirmed" and "no brightness route" in out.members["lamps"].detail


def test_build_adapters_gives_brightness_the_lights(tmp_path):
    from room_service.adapters import build_adapters
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    ad = {a.subsystem: a for a in build_adapters({}, mask, lambda *a: None, tmp_path, sonos=False, remotes=[],
                                                  blinds=FakeLevel("blinds", {}))}
    assert isinstance(ad["light"], LightsAdapter) and ad["brightness"].rig is ad["light"]


# -- RS-5 / C1 ---------------------------------------------------------------------------------------------------------

def test_light_rooms_one_rule():
    two = {"a": {"lights": ["light"]}, "b": {"lights": ["light.l2"]}, "c": {"lights": ["light"]}}
    assert light_rooms(two, ["l1", "l2", "l3"]) == {"l1": "a", "l2": "b", "l3": "a"}     # first bare room wins
    assert light_rooms({"only": {"screens": ["x"]}}, ["l1"]) == {"l1": "only"}          # the only room
    assert light_rooms({"a": {"screens": []}, "b": {}}, ["l1"]) == {"l1": None}           # "Other"
    assert light_rooms({"_c": "x", "a": {"lights": ["light.l1"]}, "b": {"lights": ["light.l1"]}}, ["l1"]) == {"l1": "a"}


def test_room_groups_never_put_a_light_in_two_rooms():
    lights = Placeholder("light", ["l1", "l2"])
    g = room_groups({"room-a": {"lights": ["light"]}, "room-b": {"lights": ["light"]},
                     "room-c": {"lights": ["light.l2"]}}, [lights])
    assert g["room-a:light"] == ["light.l1"] and g["room-c:light"] == ["light.l2"]
    assert not g.get("room-b:light") and g["room-b:lights"] == []
    g = room_groups({"studio": {"screens": ["vj"]}}, [lights])                     # one room: its lights, unlisted
    assert g["studio:light"] == ["light.l1", "light.l2"]


def test_finsburys_drawing_room_still_gets_every_light():
    rooms = smconfig.rooms(smconfig.load("finsbury"))
    hue = FakeLevel("hue", {"ne": "off", "nw": "off", "se": "off", "sw": "off"})
    g = room_groups(rooms, [hue, Placeholder("light", ["par1", "par2"])])
    assert g["drawing-room:light"] == ["light.par1", "light.par2"]
    assert g["drawing-room:lights"] == ["hue.ne", "hue.nw", "hue.se", "hue.sw", "light.par1", "light.par2"]
    assert "lab:light" not in g


# -- RS-6 --------------------------------------------------------------------------------------------------------------

def test_an_empty_room_group_is_known_and_named_in_the_warning(tmp_path):
    ad = screen_adapters(tmp_path, [], {"vj": "VJ screen"}) + [FakeLevel("blinds", {})]
    groups, notes = derive_room_groups({"studio": {"name": "Studio", "screens": ["vj"], "blinds": []}}, ad)
    assert groups["studio:screens"] == [] and groups["studio:page"] == [] and groups["studio:blinds"] == []
    assert notes["studio:screens"] == "Studio has no live screens"
    cfg = RoomConfig(modes({"studio:screens": "art", "studio:blinds": 0}), ad, extra_groups=groups, group_notes=notes)
    msgs = [w["message"] for w in cfg.lint()]
    assert any(m.startswith("Studio has no live screens") for m in msgs)
    assert not any(w["code"] == "unknown_subsystem" for w in cfg.lint())


# -- RS-7 --------------------------------------------------------------------------------------------------------------

async def test_an_art_only_screens_socket_is_closed_with_its_own_reason(make_harness):
    h = await make_harness(destinations=[{"id": "the-beast", "name": "The beast"}])
    ws = await h.http.ws_connect(h.url + "/v1/screen/the-beast?role=kiosk")
    msg = await ws.receive(timeout=2)
    assert ws.close_code == 4404 and "art-only" in (msg.extra or "")
    await ws.close()
    ws = await h.http.ws_connect(h.url + "/v1/screen/nope?role=kiosk")
    msg = await ws.receive(timeout=2)
    assert ws.close_code == 4404 and msg.extra == "unknown screen"
    await ws.close()


# -- RS-8 --------------------------------------------------------------------------------------------------------------

def test_fake_artnet_screens_by_order():
    north = {"rgb": [1.0, 0.0, 0.0], "pan": 0.5, "pulse": 0.0}
    south = {"rgb": [0.0, 1.0, 0.0], "pan": -0.5, "pulse": 0.0}
    song = {"screen.north": north, "screen.south": south}
    assert screen_variants(song, ["screen.north", "screen.south"]) == {"screen.north": north, "screen.south": south}
    v = screen_variants(song, ["screen.a", "screen.b", "screen.c"])
    assert v["screen.a"] == north and v["screen.b"] == south
    assert v["screen.c"]["rgb"] != north["rgb"] and v["screen.c"]["pan"] == north["pan"]
    four = screen_variants(song, ["a", "b", "c", "d"])
    assert four["d"]["pan"] == -north["pan"] and four["c"]["rgb"] != four["d"]["rgb"]


# -- RS-9 --------------------------------------------------------------------------------------------------------------

def test_screen_members_are_labelled_with_the_screens_name(tmp_path):
    c = {"vj": SimpleNamespace(cfg=SimpleNamespace(label="VJ screen"),
                               mgr=SimpleNamespace(stream=SimpleNamespace(cached_catalog=lambda: None)))}
    s, p, d = ScreensAdapter(c), PageAdapter(c, tmp_path), DiagAdapter(c, tmp_path)
    assert s.label("vj") == p.label("vj.timed_reload") == d.label("vj") == "VJ screen"
    assert s.value_schema("vj")["x-ui"]["label"] == "VJ screen"


def test_page_and_diag_words_come_from_the_site(tmp_path, monkeypatch):
    lap = smconfig.load("laptop")                    # no wording: the generic word
    monkeypatch.setattr(smconfig, "deployment", lambda: lap)
    p, d = PageAdapter({}, tmp_path), DiagAdapter({}, tmp_path)
    assert (p.subsystem_label, d.value_schema("x")["x-ui"]["label"]) == ("Display pages", "Show details on the display")
    dep = smconfig.load("finsbury")                  # finsbury's wording.screen_device: PC22's words, as before
    monkeypatch.setattr(smconfig, "deployment", lambda: dep)
    p, d = PageAdapter({}, tmp_path), DiagAdapter({}, tmp_path)
    assert p.subsystem_label == "TV pages" and d.value_schema("x")["x-ui"]["label"] == "Show details on the TV"
    assert p.value_schema("x.timed_reload")["x-ui"]["label"] == "Refresh the TV pages now and then"


def test_sensor_mask_words_follow_the_site(tmp_path):
    mask = SimpleNamespace(suspended=False)
    words = lambda a: json.dumps(a.value_schema("main"))                           # noqa: E731
    assert "Follows the room's light" in words(SensorMaskAdapter(mask, tmp_path))
    assert "Follows the time of day" in words(SensorMaskAdapter(mask, tmp_path, sensed=False))
    assert "changes nothing" in json.dumps(SensorMaskAdapter(mask, tmp_path, pushed=False).value_schema("main"))
    fins = smconfig.load("finsbury")
    assert screens_follow_a_sensor(["north-screen"], fins.path("intensity.json"),
                                   fins.path("publish-destinations.json")) is True
    lap = smconfig.load("laptop")
    assert screens_follow_a_sensor(["vj"], lap.path("intensity.json"), lap.path("publish-destinations.json")) is False


# -- RS-10 -------------------------------------------------------------------------------------------------------------

def test_default_profile_by_size_never_4k60(tmp_path):
    assert default_profile(3840, 2160) == "hevc-2160p30"
    assert default_profile(1920, 1080) == default_profile(1080, 1920) == "hevc-1080p30"
    assert default_profile(None, None) == "hevc-2160p30"
    f = tmp_path / "d.json"
    f.write_text(json.dumps([{"id": "big", "maxwidth": 3840, "maxheight": 2160, "live": {"enabled": True}},
                             {"id": "hd", "maxwidth": 1920, "maxheight": 1080, "live": {}},
                             {"id": "named", "maxwidth": 1920, "maxheight": 1080, "live": {"profile": "hevc-2160p60"}},
                             {"id": "art", "name": "Art only"}]))
    s = screens_from_destinations(f)
    assert (s["big"]["profile"], s["hd"]["profile"], s["named"]["profile"]) == ("hevc-2160p30", "hevc-1080p30",
                                                                                "hevc-2160p60")
    assert known_screens(f) == {"big": "big", "hd": "hd", "named": "named", "art": "Art only"}


def test_finsburys_screens_keep_their_profiles():
    s = screens_from_destinations(smconfig.load("finsbury").path("publish-destinations.json"))
    assert {k: v["profile"] for k, v in s.items()} == {"north-screen": "hevc-2160p30", "south-screen": "hevc-2160p30",
                                                         "devtest": "hevc-2160p30"}
