"""room_service on the deployments/ layer (smconfig): site files and service addresses from the deployment, the
capabilities it gates (live, dmx, hue, rf, blinds, sonos, show, room) and missing optional site files."""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

import smconfig
from room_service import config as rc
from room_service.adapters import blinds as blinds_mod
from room_service.adapters import broadlink_rf, build_adapters
from room_service.roomstate.contract import Placeholder
from room_service.service import RoomService
from room_service.stream_client import StreamClient, StreamError

from .conftest import ROOM_TOKEN, Harness
from .roomstate.test_adapters import Ctl


def caps_off(monkeypatch, *off: str) -> None:
    """The selected deployment with these capabilities switched off (everything else as the manifest says)."""
    real = smconfig.enabled
    monkeypatch.setattr(smconfig, "enabled", lambda cap: False if cap in off else real(cap))


def adapters(tmp_path, **kw):
    controllers = {s: Ctl(s) for s in ("north-screen", "south-screen", "devtest")}
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    kw.setdefault("show", False)
    return {a.subsystem: a for a in build_adapters(controllers, mask, lambda lvl, msg: None, tmp_path, **kw)}


# -- defaults come from the deployment ---------------------------------------------------------------------------------

def test_site_files_and_addresses_come_from_the_deployment():
    dep = smconfig.deployment()
    assert rc.DEFAULT_CONFIG == dep.path("room", "room.yaml")
    assert rc.DEFAULT_CONFIG.exists()
    c = rc.RoomConfig()
    assert c.rooms_file == dep.path("rooms.json")
    assert c.patch_file == dep.path("lights", "patch.yaml")
    assert c.modes_file == dep.path("room", "modes.json")
    assert c.stream_url == f"http://127.0.0.1:{dep.port('stream')}"
    assert c.show_url == f"http://127.0.0.1:{dep.port('show')}"
    assert c.flask_url == f"http://127.0.0.1:{dep.port('flask')}"
    assert c.flask_ws == f"ws://127.0.0.1:{dep.port('overlay_ws')}/"
    assert c.art_page_base == c.flask_url
    assert c.show_bus_port == dep.port("show_bus")
    assert f"http://localhost:{dep.port('flask')}" in rc.DEFAULT_WEB_ORIGINS
    assert broadlink_rf.RF_DIR == dep.path("room", "rf")
    assert broadlink_rf.TYPES_DIR == smconfig.shared("room", "rf", "types")


def test_the_production_config_resolves_as_before(monkeypatch):
    """finsbury: the values room_service ran with before the move (room.prod.yaml + the old code defaults)."""
    monkeypatch.setattr(rc, "DEFAULT_DESTINATIONS", smconfig.path("publish-destinations.json"))   # the real screens
    c = rc.load_config()
    assert c.config_path == smconfig.path("room", "room.yaml").resolve()
    assert c.modes_file == smconfig.path("room", "modes.json").resolve()      # next to the YAML
    assert c.stream_url == "http://127.0.0.1:8702" and c.show_url == "http://127.0.0.1:8703"
    assert c.flask_url == "http://127.0.0.1:5000" and c.flask_ws == "ws://127.0.0.1:8765/"
    assert c.art_page_base == "http://127.0.0.1:5000" and c.dmx_enabled and c.show_bus_port == 8701
    assert {"north-screen", "south-screen"} <= set(c.screens)


def test_the_yaml_still_overrides_the_addresses():
    c = rc.config_from_dict({"stream_service": {"url": "http://127.0.0.1:9/"}, "show_service": {"url": "http://h:1"},
                             "flask": {"url": "http://f:2", "ws": "ws://f:3/"}, "art_page_base": "http://a:4"})
    assert (c.stream_url, c.show_url, c.flask_url, c.flask_ws, c.art_page_base) == \
        ("http://127.0.0.1:9", "http://h:1", "http://f:2", "ws://f:3/", "http://a:4")


def test_one_show_address_for_the_feel_and_the_lights(tmp_path):
    ad = adapters(tmp_path, show=None, lights=None, show_url="http://127.0.0.1:9", remotes=[], sonos=False,
                  blinds=Placeholder("blinds", []))
    assert ad["show"].url == "http://127.0.0.1:9" and ad["light"].url == "http://127.0.0.1:9"


def test_hue_show_url_patch_wins_then_room_services():
    from room_service.adapters.hue import HueAdapter
    patch = SimpleNamespace(hue={}, of_kind=lambda k: [])
    a = HueAdapter(SimpleNamespace(client=None), patch, client=object(), show_url="http://room:1/")
    assert a.show_url == "http://room:1"
    patch.hue["show_url"] = "http://patch:2"
    assert a.show_url == "http://patch:2"
    assert HueAdapter(SimpleNamespace(client=None), SimpleNamespace(hue={}, of_kind=lambda k: []),
                      client=object()).show_url == smconfig.url("show")


# -- capabilities off --------------------------------------------------------------------------------------------------

def test_sonos_blinds_and_show_off_are_placeholders(tmp_path, monkeypatch):
    caps_off(monkeypatch, "sonos", "blinds", "show")
    ad = adapters(tmp_path, show=None, lights=None, remotes=[])
    assert isinstance(ad["sonos"], Placeholder)
    assert isinstance(ad["blinds"], Placeholder)
    assert set(ad["blinds"].members) == set(json.loads(smconfig.path("room", "rf", "blinds.json").read_text())["blinds"])
    assert isinstance(ad["show"], Placeholder) and isinstance(ad["light"], Placeholder)


def test_rf_off_gives_placeholders_and_never_an_rm4(tmp_path, monkeypatch):
    caps_off(monkeypatch, "rf")
    got = broadlink_rf.from_config(state_dir=tmp_path)
    assert got and all(isinstance(a, Placeholder) for a in got)
    subs = {a.subsystem for a in got}
    assert "mirror_ball" in subs                         # the glitterball still names its subsystem (modes load)


def test_missing_blinds_file_is_the_placeholder_not_a_crash(tmp_path, monkeypatch):
    missing = tmp_path / "no-blinds.json"
    monkeypatch.setattr(blinds_mod, "from_config", lambda path=None, key_path=None: json.loads(missing.read_text()))
    monkeypatch.setattr(blinds_mod, "placeholder_from_config",
                        lambda path=None, _p=blinds_mod.placeholder_from_config: _p(str(missing)))
    ad = adapters(tmp_path, remotes=[], sonos=False)
    assert isinstance(ad["blinds"], Placeholder) and ad["blinds"].members == []


def test_missing_optional_site_files_mean_none(tmp_path, monkeypatch):
    from room_service.adapters import devices, sonos
    monkeypatch.setattr(broadlink_rf, "RF_DIR", tmp_path / "no-rf")
    assert broadlink_rf.from_config(state_dir=tmp_path) == []
    assert sonos.from_config(tmp_path / "no-sonos.json") is None
    assert devices.tv(tmp_path / "no-tv.json") is None
    assert blinds_mod.placeholder_from_config(str(tmp_path / "none.json")).members == []


def test_stream_client_disabled_never_contacts_the_renderer():
    async def go():
        sc = StreamClient("http://127.0.0.1:9", None, http=None, disabled="live is off")
        sc.start()
        assert sc._task is None
        with pytest.raises(StreamError) as e:
            await sc.list_sessions()
        assert e.value.code == "live_off" and e.value.status == 503
    import asyncio
    asyncio.run(go())


class _NoWaitHarness(Harness):
    """A harness whose room does not wait for the renderer's events (live off never connects); dmx=True turns the
    room config's dmx on with the deployment's patch."""

    dmx = False

    def config(self):
        cfg = super().config()
        if self.dmx:
            cfg.dmx_enabled, cfg.show_bus_port = True, 0
        return cfg

    async def start_room(self) -> None:
        from aiohttp.test_utils import TestServer
        from room_service.roomstate.fakes import FakeLevel
        self.svc = RoomService(self.config(), snapshotter=self.snap, token=ROOM_TOKEN, extra_adapters=self.extra_adapters,
                               blinds=FakeLevel("blinds", {"ch02": 0}), remotes=[], show=False)
        self.server = TestServer(self.svc.app, host="127.0.0.1")
        await self.server.start_server()


async def test_live_off_refuses_every_screen_and_leaves_stream_service_alone(tmp_path, monkeypatch):
    caps_off(monkeypatch, "live")
    h = _NoWaitHarness(tmp_path)
    await h.start()
    try:
        assert not h.svc.cfg.screens["devtest"].live_enabled
        assert h.svc.stream._task is None and not h.svc.stream.reachable      # never contacted
        st, body = await h.api("PUT", "/api/v1/screens/devtest", {"desired": {"live": {"scene": "fractal-zoom"}}})
        assert st == 409 and body["error"] == "live_disabled" and "live is off for this deployment" in body["detail"]
        st, body = await h.api("GET", "/renderer/api/state")
        assert st == 503 and body["error"] == "live_off"
        out = await h.svc.coordinator.adapters["screens"].apply({"devtest": {"live": {"scene": "fractal-zoom"}}})
        assert out.status == "failed" and "live is off" in out.detail
    finally:
        await h.stop()


async def test_dmx_off_starts_no_stack_and_hue_stays_a_placeholder(tmp_path, monkeypatch):
    caps_off(monkeypatch, "dmx")
    h = _NoWaitHarness(tmp_path)
    h.dmx = True
    await h.start()
    try:
        assert h.svc.cfg.dmx_enabled and h.svc.dmx is None
        hue = h.svc.coordinator.adapters.get("hue")
        assert hue is None or isinstance(hue, Placeholder)
    finally:
        await h.stop()


async def test_dmx_on_without_a_patch_file_is_no_stack(tmp_path, monkeypatch):
    h = _NoWaitHarness(tmp_path)
    h.dmx = True
    h.overrides = {}
    real = h.config

    def config():
        cfg = real()
        cfg.patch_file = tmp_path / "no-patch.yaml"
        return cfg
    h.config = config
    await h.start()
    try:
        assert h.svc.dmx is None and h.svc.coordinator is not None
    finally:
        await h.stop()


def test_hue_off_builds_no_output(monkeypatch):
    caps_off(monkeypatch, "hue")
    from room_service.dmx import DmxStack
    stack = DmxStack(smconfig.path("lights", "patch.yaml"), show_bus_port=0)
    assert stack.hue is None and "off for this deployment" in (stack.hue_error or "")
    a = stack.adapter(show_url="http://127.0.0.1:9")
    assert a is None or isinstance(a, Placeholder)


async def test_rf_off_refuses_adding_a_device(tmp_path, monkeypatch, make_harness):
    caps_off(monkeypatch, "rf")
    rooms = tmp_path / "rooms.json"
    rooms.write_text(json.dumps({"drawing-room": {"name": "Drawing room", "screens": ["devtest"]}}))
    h = await make_harness(service={"rooms_file": str(rooms)})
    st, body = await h.api("POST", "/api/v1/remotes", {"model": "pinspot", "label": "X", "host": "1.2.3.4",
                                                       "room": "drawing-room"})
    assert st == 409 and body["error"] == "rf_off"


# -- room_in_app: the room capability ----------------------------------------------------------------------------------

def _deployment(tmp_path, monkeypatch, **caps) -> str:
    """A copy of the example deployment under a temp deployments/ dir, selected, with these capabilities."""
    d = tmp_path / "deployments"
    shutil.copytree(smconfig.DEPLOYMENTS_DIR / "example", d / "example")
    mf = d / "example" / "deployment.yaml"
    raw = smconfig.read_data(mf)
    raw["capabilities"].update(caps)
    import yaml
    mf.write_text(yaml.safe_dump(raw))
    monkeypatch.setattr(smconfig, "DEPLOYMENTS_DIR", d)
    monkeypatch.setenv("SM_DEPLOYMENT", "example")
    smconfig._cache.clear()
    return "example"


def test_room_off_is_not_started_in_the_app(tmp_path, monkeypatch, capsys):
    import room_in_app
    _deployment(tmp_path, monkeypatch, room=False)
    monkeypatch.setattr(room_in_app, "_runner", None)
    monkeypatch.delenv("ROOM_IN_APP", raising=False)
    try:
        assert room_in_app.start_in_background() is None
        err = capsys.readouterr().err
        assert "room capability off" in err and room_in_app.runner() is None
    finally:
        smconfig._cache.clear()


def test_no_deployment_is_not_started_in_the_app(monkeypatch, capsys):
    import room_in_app
    monkeypatch.setattr(room_in_app, "_runner", None)
    monkeypatch.delenv("ROOM_IN_APP", raising=False)
    monkeypatch.setenv("SM_DEPLOYMENT", "no-such-deployment")
    assert room_in_app.start_in_background() is None
    assert "no deployment" in capsys.readouterr().err


def test_the_in_app_defaults_are_the_deployments(monkeypatch):
    import room_in_app
    monkeypatch.delenv("ROOM_PORT", raising=False)
    assert room_in_app.default_port() == smconfig.port("room")
    assert room_in_app.default_config() == smconfig.path("room", "room.yaml")
    monkeypatch.setenv("ROOM_PORT", "8799")
    assert room_in_app.default_port() == 8799


def test_a_deployment_without_devices_starts_its_adapters(tmp_path, monkeypatch):
    """The example deployment (no rf/, sonos.json, tv.json, blinds.json or patch; dmx/hue/rf/blinds/sonos off): every
    device subsystem is a placeholder and nothing raises."""
    _deployment(tmp_path, monkeypatch)
    try:
        dep = smconfig.deployment()
        assert not dep.path("room", "rf", "blinds.json").exists()
        monkeypatch.setattr(broadlink_rf, "RF_DIR", dep.path("room", "rf"))
        ad = adapters(tmp_path)
        assert isinstance(ad["blinds"], Placeholder) and ad["blinds"].members == []
        assert isinstance(ad["sonos"], Placeholder)
        assert "mirror_ball" not in ad and "plug" not in ad      # finsbury's glitterball and DMX plug are not here
    finally:
        smconfig._cache.clear()


def test_the_glitterball_and_dmx_plug_placeholders_follow_the_deployment(tmp_path, monkeypatch):
    """finsbury keeps both (its rf/ has a mirror_ball file, dmx is on), even with rf off; a site without them has none."""
    from room_service.adapters import devices
    caps_off(monkeypatch, "rf")
    subs = {p.subsystem: p for p in devices.placeholders()}
    assert subs["mirror_ball"].members == ["main"] and subs["mirror_ball"].subsystem_label == "Glitterball"
    assert subs["plug"].members == ["dmx_power"]
    caps_off(monkeypatch, "rf", "dmx")
    monkeypatch.setattr(broadlink_rf, "RF_DIR", tmp_path / "no-rf")
    subs = {p.subsystem for p in devices.placeholders()}
    assert "mirror_ball" not in subs and "plug" not in subs
    (tmp_path / "no-rf").mkdir()
    (tmp_path / "no-rf" / "ball.json").write_text('{"subsystem": "mirror_ball", broken')   # unreadable still counts
    assert "mirror_ball" in {p.subsystem for p in devices.placeholders()}


async def test_live_off_status_says_why_and_show_off_refuses_the_show_proxy(tmp_path, monkeypatch):
    caps_off(monkeypatch, "live", "show")
    h = _NoWaitHarness(tmp_path)
    await h.start()
    try:
        ss = h.svc.status()["stream_service"]
        assert not ss["reachable"] and "live is off for this deployment" in ss["last_error"]
        st, body = await h.api("GET", "/show/api/v1/status")
        assert st == 503 and body["error"] == "show_off"
    finally:
        await h.stop()


def test_dmx_show_bus_port_defaults_to_the_deployments():
    from room_service.dmx.bus import ShowBus
    from room_service.dmx.patch import load_patch
    from room_service.dmx.artnet import ArtNetReceiver
    patch = load_patch(smconfig.path("lights", "patch.yaml"))
    bus = ShowBus(patch, ArtNetReceiver(patch.artnet.universe, patch.artnet.stale_after_ms))
    assert bus.port == smconfig.port("show_bus")


def test_autopilot_seeds_from_the_rooms_modes_file(tmp_path):
    modes = tmp_path / "modes.json"
    modes.write_text('{"autopilot": {"on": true}}')
    ad = adapters(tmp_path, modes_file=modes)
    assert ad["autopilot"].modes_file == modes and ad["autopilot"].value() == "on"
