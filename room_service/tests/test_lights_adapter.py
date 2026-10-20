"""light adapter (the DMX lights registered with show_service, contract C) against a fake show_service on an ephemeral
127.0.0.1 port: members from the rig (lamps on Art-Net outputs only) and from the state-dir cache while show_service
is down, watch_members' callback (and its nudge from a new rig revision), read, apply (the complete mapping, read
back; unreachable/no route/5xx unconfirmed, 400 and a removed light failed and never retried), identify, normalize;
the room config (a light a mode names but show_service doesn't have names nothing; rooms.json's "light" item covers
every light), registration, a mode putting the lights back, and room_service rebuilding its config when a light is
registered. Never touches the real show_service (:8703) or the room API (:8700)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

from room_service.adapters import build_adapters
from room_service.adapters.lights import LightsAdapter, ShowUnreachable, rig_lights, rig_revision
from room_service.room_config import RoomConfigFile, room_groups
from room_service.roomstate.contract import Placeholder
from room_service.roomstate.fakes import FakeLevel
from room_service.roomstate.model import ConfigError, RoomConfig

from .conftest import Harness
from .roomstate.harness import boot, example, fake_adapters, go, make_room

REPO = Path(__file__).resolve().parents[2]
OUTPUTS = {"hue": {"id": "hue", "driver": "hue"}, "screens": {"id": "screens", "driver": "showbus"},
           "dmx-a": {"id": "dmx-a", "driver": "artnet"}}


def row(fid: str, label: str, kind: str = "lamp", output: str = "dmx-a", **kw) -> dict:
    return {"id": fid, "label": label, "kind": kind, "type": "generic-rgbw", "output": output, **kw}


class FakeRig:
    """show_service's rig routes as contract A specifies them: GET /api/v1/rig (per-fixture rows), GET/POST
    /api/v1/rig/follow (a POST replaces the mapping: the strictest reading, so a partial POST would show), and
    identify. `status` makes a route answer something else."""

    def __init__(self) -> None:
        self.rows = [row("screen.north", "North Frame", kind="screen", output="screens"),
                     row("hue.ne", "NE lamp", output="hue"),
                     row("par1", "Bar left"), row("par2", "Bar right")]
        self.revision = "rev-1"
        self.follow: dict = {"par1": "off", "par2": "dmx"}
        self.follow_revision: str | None = None   # a follow answer that names the rig's revision
        self.posts: list[dict] = []
        self.identified: list[tuple[str, dict]] = []
        self.identify_answer: dict = {"ok": True}
        self.status = {"rig": 200, "follow": 200, "post": 200}
        self.ignore_posts = False
        self.server: TestServer | None = None
        self.url = ""

    async def get_rig(self, request: web.Request) -> web.Response:
        if self.status["rig"] != 200:
            return web.json_response({"error": "starting"}, status=self.status["rig"])
        return web.json_response({"enabled": True, "registry": {"revision": self.revision}, "outputs": OUTPUTS,
                                  "fixtures": self.rows})

    async def get_follow(self, request: web.Request) -> web.Response:
        if self.status["follow"] != 200:
            return web.json_response({"error": "starting"}, status=self.status["follow"])
        body = {"fixtures": self.follow}
        if self.follow_revision is not None:
            body["revision"] = self.follow_revision
        return web.json_response(body)

    async def post_follow(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.posts.append(body)
        if self.status["post"] == 400:
            return web.json_response({"error": "bad_follow", "detail": "values are dmx, off or {hex, bri}"}, status=400)
        if self.status["post"] != 200:
            return web.json_response({"error": "busy"}, status=self.status["post"])
        if not self.ignore_posts:
            self.follow = dict(body["fixtures"])
        return web.json_response({"accepted": sorted(body["fixtures"])})

    async def post_identify(self, request: web.Request) -> web.Response:
        self.identified.append((request.match_info["id"], await request.json()))
        return web.json_response(self.identify_answer)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/v1/rig", self.get_rig)
        app.router.add_get("/api/v1/rig/follow", self.get_follow)
        app.router.add_post("/api/v1/rig/follow", self.post_follow)
        app.router.add_post("/api/v1/rig/fixtures/{id}/identify", self.post_identify)
        self.server = TestServer(app, host="127.0.0.1")
        await self.server.start_server()
        self.url = str(self.server.make_url("")).rstrip("/")


@pytest.fixture
async def rig():
    fake = FakeRig()
    await fake.start()
    yield fake
    await fake.server.close()


def nowhere() -> str:
    return f"http://127.0.0.1:{unused_port()}"                 # nothing listens there


async def ready(rig: FakeRig, tmp_path) -> LightsAdapter:
    a = LightsAdapter(rig.url, cache_file=tmp_path / "lights_rig.json")
    assert await a.refresh_members()
    return a


# -- members ----------------------------------------------------------------------------------------------------------

def test_the_rigs_dmx_lamps_are_the_members_in_its_order():
    body = {"outputs": OUTPUTS, "fixtures": [row("screen.north", "N", kind="screen", output="screens"),
                                             row("hue.ne", "NE", output="hue"), row("b", "B"), row("a", "A"),
                                             row("odd one", "space in its id"), row("lamp-x", "own driver",
                                                                                    output="x", driver="artnet")]}
    assert list(rig_lights(body)) == ["b", "a", "lamp-x"]
    # the registry view's fixtures (an object by id), drivers from its outputs, when the answer has no rows
    view = {"registry_view": {"revision": "r9", "outputs": OUTPUTS,
                              "fixtures": {"hue.ne": {"kind": "lamp", "output": "hue"},
                                           "par9": {"kind": "lamp", "output": "dmx-a", "label": "Nine"}}}}
    assert list(rig_lights(view)) == ["par9"] and rig_revision(view) == "r9"
    assert rig_lights({"enabled": False}) == {} and rig_lights(None) == {}


async def test_members_come_from_the_rig_and_survive_a_restart_while_show_is_down(rig, tmp_path):
    a = LightsAdapter(rig.url, cache_file=tmp_path / "lights_rig.json")
    assert a.members == [] and not a.placeholder
    assert await a.refresh_members() is True
    assert a.members == ["par1", "par2"] and a.label("par1") == "Bar left" and a.revision == "rev-1"
    assert await a.refresh_members() is False                               # unchanged
    rig.rows[2]["label"] = "Bar, left"
    assert await a.refresh_members() is False and a.label("par1") == "Bar, left"   # labels alone: no rebuild
    b = LightsAdapter(nowhere(), cache_file=tmp_path / "lights_rig.json")   # room_service restarted, show_service down
    assert b.members == ["par1", "par2"] and b.label("par1") == "Bar, left" and b.revision == "rev-1"
    with pytest.raises(ShowUnreachable):
        await b.refresh_members()
    assert b.members == ["par1", "par2"]                                    # kept
    rig.status["rig"] = 503
    with pytest.raises(ShowUnreachable):
        await a.refresh_members()
    assert a.members == ["par1", "par2"]


async def test_watch_members_calls_back_on_a_change_and_a_new_revision_nudges_it(rig, tmp_path):
    a = LightsAdapter(rig.url, cache_file=tmp_path / "lights_rig.json", refresh_s=30.0)
    seen: list[str] = []
    task = asyncio.create_task(a.watch_members(seen.append))
    try:
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen == ["light"] and a.members == ["par1", "par2"]
        rig.rows.append(row("wash", "Wash"))                                # registered on /lights
        rig.revision = rig.follow_revision = "rev-2"
        await a.read(["par1"])                                              # sees rev-2: re-reads the rig now, not in 30 s
        for _ in range(200):
            if len(seen) == 2:
                break
            await asyncio.sleep(0.01)
        assert seen == ["light", "light"] and a.members == ["par1", "par2", "wash"]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# -- values -----------------------------------------------------------------------------------------------------------

def test_normalize_and_words():
    a = LightsAdapter(nowhere())
    assert a.normalize("p", "show") == "dmx" and a.normalize("p", "off") == "off"
    assert a.normalize("p", {"hex": "#FF8800"}) == {"hex": "#ff8800", "bri": 100}
    assert a.normalize("p", {"hex": "#ff8800", "bri": 40.4}) == {"hex": "#ff8800", "bri": 40}
    assert a.normalize("p", {"on": False, "hex": "#ff8800"}) == "off"
    for bad in ("on", 3, {"bri": 50}, {"hex": "orange"}, {"hex": "#ff8800", "bri": 101}, {"hex": "#ff8800", "xy": [0, 0]}):
        with pytest.raises(ValueError):
            a.normalize("p", bad)
    assert a.words("p", "dmx") == "Follows the music" and a.words("p", "off") == "Off"
    assert a.words("p", {"hex": "#2f6bff", "bri": 60}) == "On, 60%, blue"
    s = a.value_schema("p")
    assert [b.get("const") for b in s["oneOf"]] == ["dmx", "off", None] and s["x-ui"]["labels"]["dmx"] == "Follows the music"
    json.dumps(s)


async def test_read_gives_the_canonical_values(rig, tmp_path):
    a = await ready(rig, tmp_path)
    rig.follow = {"par1": "dmx", "par2": {"hex": "#FF0000", "bri": 30}, "gone": "off"}
    got = await a.read(["par1", "par2", "gone"])
    assert {m: r.value for m, r in got.items()} == {"par1": "dmx", "par2": {"hex": "#ff0000", "bri": 30}}
    rig.follow = {"par1": "sideways"}                                       # not a value of ours; par2 missing
    got = await a.read(["par1", "par2"])
    assert got["par1"].value is None and got["par2"].value is None          # unknown, not a guess
    rig.status["follow"] = 500
    with pytest.raises(ShowUnreachable):
        await a.read(["par1"])
    b = LightsAdapter(nowhere(), cache_file=tmp_path / "lights_rig.json")
    with pytest.raises(ShowUnreachable):
        await b.read(["par1"])


# -- apply ------------------------------------------------------------------------------------------------------------

async def test_apply_posts_the_complete_mapping_and_reads_it_back(rig, tmp_path):
    a = await ready(rig, tmp_path)
    rig.follow = {"par1": "off", "par2": "dmx", "hue.ne": "dmx"}            # show_service's own entries are kept
    out = await a.apply({"par1": {"hex": "#ffc367", "bri": 60}}, {})
    assert out.status == "ok" and out.members["par1"].status == "ok", out
    assert rig.posts == [{"fixtures": {"par1": {"hex": "#ffc367", "bri": 60}, "par2": "dmx", "hue.ne": "dmx"}}]
    assert (await a.read(["par1"]))["par1"].value == {"hex": "#ffc367", "bri": 60}
    rig.ignore_posts = True                                                 # accepted but not kept: not proven
    out = await a.apply({"par2": "off"}, {})
    assert out.status == "unconfirmed" and "reads 'dmx'" in out.members["par2"].detail


async def test_show_service_down_or_without_the_route_is_unconfirmed_never_failed(rig, tmp_path):
    a = await ready(rig, tmp_path)
    for status in (404, 500, 503):
        rig.status["post"] = status
        out = await a.apply({"par1": "dmx"}, {})
        assert out.status == "unconfirmed" and out.retry and out.members["par1"].status == "unconfirmed", status
    b = LightsAdapter(nowhere(), cache_file=tmp_path / "lights_rig.json")
    out = await b.apply({"par1": "dmx", "par2": "off"}, {})
    assert out.status == "unconfirmed" and out.retry
    assert {m: o.status for m, o in out.members.items()} == {"par1": "unconfirmed", "par2": "unconfirmed"}
    rig.status.update(post=200, follow=503)                                 # can't read theirs: nothing is sent
    posts = len(rig.posts)
    out = await a.apply({"par1": "dmx"}, {})
    assert out.status == "unconfirmed" and out.retry and len(rig.posts) == posts


async def test_a_refused_body_and_a_removed_light_fail_and_are_never_retried(rig, tmp_path):
    a = await ready(rig, tmp_path)
    rig.status["post"] = 400
    out = await a.apply({"par1": "dmx"}, {})
    assert out.status == "failed" and not out.retry and "bad_follow" in out.members["par1"].detail
    rig.status["post"] = 200
    out = await a.apply({"par1": "dmx", "gone": "dmx"}, {})
    assert out.members["par1"].status == "ok"
    assert out.members["gone"].status == "failed" and not out.members["gone"].retry
    assert out.status == "failed" and not out.retry
    assert "gone" not in rig.posts[-1]["fixtures"]


async def test_identify_goes_to_show_service(rig, tmp_path):
    a = await ready(rig, tmp_path)
    assert (await a.signal("par1", "identify", {})).status == "ok"
    assert (await a.signal("par2", "identify", {"seconds": 5})).status == "ok"
    assert rig.identified == [("par1", {}), ("par2", {"seconds": 5})]
    rig.identify_answer = {"ok": False, "why": "its output isn't running"}
    o = await a.signal("par1", "identify", {})
    assert o.status == "failed" and o.detail == "its output isn't running" and not o.retry
    assert (await a.signal("nope", "identify", {})).status == "failed"
    assert (await LightsAdapter(nowhere()).signal(None, "identify", {})).status == "failed"


# -- the room config ----------------------------------------------------------------------------------------------------

def modes(state: dict, labels: dict | None = None) -> dict:
    return {"default": "a", "labels": labels or {}, "modes": {"a": {"state": state}},
            "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}]}


def cached(tmp_path, *ids: str) -> LightsAdapter:
    """A LightsAdapter whose lights come from the cache (show_service never asked)."""
    f = tmp_path / "lights_rig.json"
    f.write_text(json.dumps({"revision": "r", "lights": [{"id": i, "label": i.upper()} for i in ids]}))
    return LightsAdapter(nowhere(), cache_file=f)


def test_a_light_a_mode_names_but_show_service_lacks_names_nothing(tmp_path):
    none = cached(tmp_path)
    cfg = RoomConfig(modes({"light": {"set": "dmx", "restore": True}, "light.gone": "off"}, {"light.gone": "Gone"}),
                     [none])
    assert cfg.modes["a"].state == {}                                       # loads; nothing to drive yet
    two = cached(tmp_path, "par1", "par2")
    cfg = RoomConfig(modes({"light": {"set": "dmx", "restore": True}, "light.gone": "off"}), [two])
    assert sorted(cfg.modes["a"].state) == ["light.par1", "light.par2"]
    assert cfg.modes["a"].state["light.par1"].set == "dmx" and cfg.modes["a"].state["light.par1"].restore
    with pytest.raises(ConfigError, match="matches no target"):            # other adapters keep the strict rule
        RoomConfig(modes({"hue.gone": "off"}), [FakeLevel("hue", {"ne": "off"}), two])


def test_rooms_json_light_item_covers_every_light(tmp_path):
    hue = FakeLevel("hue", {"a": "off", "b": "off"})
    g = room_groups({"den": {"lights": ["hue.a", "light"]}}, [hue, cached(tmp_path, "par1", "par2")])
    assert g["den:lights"] == ["hue.a", "light.par1", "light.par2"]
    assert g["den:light"] == ["light.par1", "light.par2"] and g["den:hue"] == ["hue.a"]
    g = room_groups({"den": {"lights": ["hue.a", "light"]}}, [hue, Placeholder("light", [])])
    assert g == {"den:lights": ["hue.a"], "den:hue": ["hue.a"]}


def test_the_shipped_rooms_and_modes_load_with_registered_lights(tmp_path):
    from .roomstate.test_adapters import Ctl
    controllers = {s: Ctl(s) for s in ("north-screen", "south-screen", "devtest")}
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    ad = build_adapters(controllers, mask, lambda lvl, msg: None, tmp_path, sonos=False, show=False, remotes=[],
                        blinds=FakeLevel("blinds", {f"ch{n:02d}": 0 for n in range(2, 15)}),
                        lights=cached(tmp_path, "par1", "par2"))
    rc = RoomConfigFile(REPO / "config" / "room" / "modes.json", ad, tmp_path,
                        rooms_path=REPO / "routes" / "data" / "rooms.json")
    cfg = rc.load()
    assert rc.error is None and rc.rooms_error is None, (rc.error, rc.rooms_error)
    assert cfg.groups["drawing-room:light"] == ["light.par1", "light.par2"]
    assert {"light.par1", "light.par2"} <= set(cfg.groups["drawing-room:lights"])
    assert rc.check(rc.raw)["ok"]


def test_build_adapters_registers_lights_or_a_placeholder_for_tests(tmp_path):
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    blinds = FakeLevel("blinds", {"ch02": 0})
    built = {a.subsystem: a for a in build_adapters({}, mask, lambda l, m: None, tmp_path, blinds=blinds, sonos=False,
                                                      remotes=[])}
    assert isinstance(built["light"], LightsAdapter) and built["light"].cache_file == tmp_path / "lights_rig.json"
    assert built["light"].subsystem_label == "Lights" and built["light"].category == "device"
    for kw in ({"show": False}, {"lights": False}):
        off = {a.subsystem: a for a in build_adapters({}, mask, lambda l, m: None, tmp_path, blinds=blinds,
                                                        sonos=False, remotes=[], **kw)}
        assert isinstance(off["light"], Placeholder) and off["light"].members == [], kw


# -- a mode, and room_service rebuilding its config -----------------------------------------------------------------------

async def test_a_mode_makes_the_lights_follow_the_music_and_the_room_puts_them_back(rig, tmp_path):
    rig.follow = {"par1": "off", "par2": {"hex": "#ffc367", "bri": 60}}
    a = fake_adapters()
    a.light = await ready(rig, tmp_path)

    def with_lights(raw):
        raw["modes"]["disco"]["state"]["light"] = {"set": "dmx", "restore": True}
    r = make_room(tmp_path, example(with_lights), adapters=a)
    await boot(r)
    await r.c._refresh(["light.par1", "light.par2"])                       # the room's belief before the switch
    tr = await go(r, "disco")
    rows = {x["subsystem"]: x["status"] for x in tr["steps"] if x["subsystem"].startswith("light.")}
    assert rows == {"light.par1": "ok", "light.par2": "ok"}, tr     # (the example's other fakes are timing-bound)
    assert rig.follow == {"par1": "dmx", "par2": "dmx"}
    await go(r, "normal")                                                   # put back afterwards
    assert rig.follow == {"par1": "off", "par2": {"hex": "#ffc367", "bri": 60}}
    d = r.c.describe()
    assert d["targets"]["light.par1"]["label"] == "Bar left" and d["subsystems"]["light"]["label"] == "Lights"
    assert d["modes"]["disco"]["entries"]["light.par2"]["words"] == "Follows the music"


async def test_room_service_rebuilds_its_config_when_a_light_is_registered(rig, tmp_path):
    rig.rows = [r for r in rig.rows if r["kind"] != "lamp" or r["output"] != "dmx-a"]   # no DMX lights yet
    h = Harness(tmp_path)
    h.overrides = {"service": {"rooms_file": str(REPO / "routes" / "data" / "rooms.json")}}
    h.extra_adapters = [LightsAdapter(rig.url, cache_file=tmp_path / "lights_rig.json", refresh_s=0.05)]
    await h.start()
    try:
        svc = h.svc
        assert "light.wash" not in svc.coordinator.cfg.targets
        rig.rows.append(row("wash", "Wash"))                                # registered on /lights
        await h.wait(lambda: "light.wash" in svc.coordinator.cfg.targets, 3.0, "light.wash in the room config")
        assert svc.room_config.groups["drawing-room:light"] == ["light.wash"]
        assert "light.wash" in svc.coordinator.describe()["targets"]
        st, body = await h.api("PUT", "/api/v1/room/targets", {"values": {"light.wash": "dmx"}})
        assert st == 202 and body["accepted"] == ["light.wash"]
        await h.wait(lambda: rig.follow.get("wash") == "dmx", 3.0, "wash follows the music")
        rig.rows.pop()                                                      # removed on /lights
        await h.wait(lambda: "light.wash" not in svc.coordinator.cfg.targets, 3.0, "light.wash gone")
        assert svc.room_config.error is None
    finally:
        await h.stop()
