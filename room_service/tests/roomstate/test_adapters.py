"""room_service's own adapters (screens shim, mask, page, log, placeholders) against stand-in controllers: they don't
crash, they map the controller's states as §5.4 says, and the spec's example config loads against the real set."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from room_service.adapters import build_adapters
from room_service.adapters.room import PageAdapter, SensorMaskAdapter
from room_service.adapters.screens import ScreensAdapter
from room_service.roomstate.fakes import FakeLevel
from room_service.roomstate.model import RoomConfig, validate

EXAMPLE = json.loads((Path(__file__).parents[2] / "roomstate" / "example_room.json").read_text())
LIVE = {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}}}


class Changed:
    def __init__(self) -> None:
        self.ev = asyncio.Event()

    async def wait(self, timeout: float) -> None:
        await asyncio.sleep(0)

    def set(self) -> None:
        pass


class Ctl:
    """Just enough of ScreenController."""

    def __init__(self, sid: str) -> None:
        self.id, self.desired, self.phase, self.layer, self.session = sid, "art", "idle", "art", None
        self._handshake = self._adopt = None
        self.timed_reload, self.kiosk_state, self.changed, self.sent = True, None, Changed(), []
        self.settle = ("ok", "live")

    def set_desired(self, value, source="manual"):
        self.desired = value
        if value != "art":
            self.layer, self.session = "live", {"live": value["live"]}
        else:
            self.layer, self.session = "art", None
        return value

    async def wait_settled(self, target, timeout):
        return self.settle

    def set_timed_reload(self, value):
        self.timed_reload = value
        return 0                                           # no page connected

    def page_timed_reload(self):
        return None

    def _page(self):
        return None


def test_screens_shim_reports_glass_and_never_retries_a_controller_that_gave_up():
    async def body():
        c = {"devtest": Ctl("devtest")}
        a = ScreensAdapter(c)
        assert (await a.read(["devtest"]))["devtest"].value == "art"
        out = await a.apply({"devtest": LIVE})
        assert out.status == "ok" and (await a.read(["devtest"]))["devtest"].value == LIVE
        c["devtest"].phase = "failed"
        assert (await a.read(["devtest"]))["devtest"].value == "failed"
        c["devtest"].settle = ("failed", "live_disabled")          # live is off for the screen (S1: never parked)
        out = await a.apply({"devtest": LIVE})
        assert out.status == "failed" and out.retry is False
        c["devtest"].settle = ("cancelled", "desired changed meanwhile")
        assert (await a.apply({"devtest": "art"})).status == "unconfirmed"
    asyncio.run(body())


def test_mask_and_page_persist_what_they_applied(tmp_path):
    async def body():
        mask = SimpleNamespace(suspended=False)
        mask.set_suspended = lambda v: setattr(mask, "suspended", v)
        m = SensorMaskAdapter(mask, tmp_path)
        await m.apply({"main": "suspended"})
        mask2 = SimpleNamespace(suspended=False)
        mask2.set_suspended = lambda v: setattr(mask2, "suspended", v)
        SensorMaskAdapter(mask2, tmp_path).restore()                   # after a restart mid-show
        assert mask2.suspended is True
        c = {"north-screen": Ctl("north-screen")}
        p = PageAdapter(c, tmp_path)
        out = await p.apply({"north-screen.timed_reload": False})           # S5: no page is no failure
        assert out.status == "ok" and "when it opens" in out.members["north-screen.timed_reload"].detail
        c2 = {"north-screen": Ctl("north-screen")}
        PageAdapter(c2, tmp_path).restore()
        assert c2["north-screen"].timed_reload is False
    asyncio.run(body())


def test_a_held_mask_survives_a_restart_and_the_welcome_carries_it(tmp_path):
    """LM-1: suspended saves the held value per screen; after a restart the TV page's welcome gets it back, for screens
    still configured with mask_push only. Following saves nothing to seed."""
    from room_service.mask import MaskPusher, MaskValue
    screens = {"north-screen": SimpleNamespace(mask_push=True), "devtest": SimpleNamespace(mask_push=False)}
    cfg = SimpleNamespace(mask=SimpleNamespace(transition_ms=200), screen=screens.get)

    async def body():
        m1 = MaskPusher(cfg, None, None, is_frozen=lambda s: False)
        m1.pushed = {"north-screen": MaskValue(0.4, "#FFC973", 0.1), "devtest": MaskValue(0.9, "#FFFFFF", 0.0)}
        m1.pushed_source = {"north-screen": "sensor"}
        await SensorMaskAdapter(m1, tmp_path).apply({"main": "suspended"})
        m2 = MaskPusher(cfg, None, None, is_frozen=lambda s: False)
        m2.pushed_source = {}
        SensorMaskAdapter(m2, tmp_path).restore()
        assert m2.suspended is True and m2.pushed_source == {"north-screen": "sensor"} and "devtest" not in m2.pushed
        assert m2.welcome_mask("north-screen") == {"rev": 0, "brightness": 0.4, "warm_hex": "#FFC973",
                                                   "warm_alpha": 0.1, "transition_ms": 200}
        await SensorMaskAdapter(m1, tmp_path).apply({"main": "follow"})
        m3 = MaskPusher(cfg, None, None, is_frozen=lambda s: False)
        SensorMaskAdapter(m3, tmp_path).restore()
        assert m3.suspended is False and m3.pushed == {}
    asyncio.run(body())


def test_the_example_config_loads_against_room_services_adapters(tmp_path):
    controllers = {s: Ctl(s) for s in ("north-screen", "south-screen", "devtest")}
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    blinds = FakeLevel("blinds", {f"ch{n:02d}": 0 for n in range(2, 15)})
    adapters = build_adapters(controllers, mask, lambda lvl, msg: None, tmp_path, blinds=blinds, sonos=False,
                              show=False)            # never the production show_service on :8703
    assert validate(EXAMPLE, adapters) == []
    cfg = RoomConfig(EXAMPLE, adapters)
    assert {"tv.north-screen", "mirror_ball.main", "plug.dmx_power", "page.devtest.timed_reload"} <= set(cfg.targets)
    codes = {w["code"] for w in validate(EXAMPLE, adapters, warnings=True)}
    assert "unknown_subsystem" not in codes                            # every subsystem the example names is known


def test_the_music_choice_shows_only_when_the_renderer_offers_it():
    """owner via b8, 17 Oct: how the heard music combines with a mode's look, per mode, in config (direction.music).
    The words are the renderer's (catalog vocab and vocab_labels), none written here."""
    def schema(cat):
        c = Ctl("north-screen")
        c.mgr = SimpleNamespace(stream=SimpleNamespace(cached_catalog=lambda: cat))
        live = ScreensAdapter({"north-screen": c}).value_schema("north-screen")["oneOf"][1]
        return live["properties"]["live"]["properties"]["direction"]["properties"]
    assert "music" not in schema({"vocab": {"moods": ["calm"]}})
    m = schema({"vocab": {"music": ["fill", "blend", "replace", "ignore"]},
                "vocab_labels": {"music": {"blend": "Mix in what's heard"}}})["music"]
    assert m["enum"] == ["fill", "blend", "replace", "ignore", None] and m["x-ui"]["labels"]["blend"] == "Mix in what's heard"


def test_diag_switches_the_live_details_panel_per_screen_and_survives_a_restart(tmp_path):
    """e1, 17 Oct (docs/LIVE_DIAGNOSTICS.md): diag.<screen> through ScreenController.set_hud(), saved in diag.json."""
    from room_service.adapters.room import DiagAdapter

    class HudCtl(Ctl):
        def __init__(self, sid):
            super().__init__(sid)
            self.hud = False

        def set_hud(self, v):
            self.hud = v

    async def body():
        old = {"north-screen": Ctl("north-screen")}                          # before 2f's set_hud: kept, unconfirmed
        a = DiagAdapter(old, tmp_path)
        assert a.value_schema("north-screen")["default"] is False and a.category == "setting"
        out = await a.apply({"north-screen": True})
        assert out.status == "unconfirmed" and (await a.read(["north-screen"]))["north-screen"].value is True
        new = {"north-screen": HudCtl("north-screen")}
        b = DiagAdapter(new, tmp_path)
        b.restore()                                                          # a restart puts it back
        assert new["north-screen"].hud is True
        assert (await b.apply({"north-screen": False})).status == "ok" and new["north-screen"].hud is False
        assert json.loads((tmp_path / "diag.json").read_text()) == {"north-screen": False}
        assert "diag" in {x.subsystem for x in build_adapters(new, SimpleNamespace(suspended=False, set_suspended=lambda v: None),
                                                              lambda l, m: None, tmp_path, sonos=False, show=False, remotes=[])}
    asyncio.run(body())
