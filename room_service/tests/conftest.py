"""Test harness: a real RoomService (in-process, random port) wired to fakes."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any, Callable

import json

import aiohttp
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from room_service.config import config_from_dict
from room_service.roomstate.fakes import FakeLevel
from room_service.service import RoomService

from .fakes import FakeFlask, FakePage, FakeSnapshotter, FakeStreamService

ROOM_TOKEN = "r" * 64
STREAM_TOKEN = "s" * 64

# Handshake timeouts scaled down ~10x so the timeout paths run in well under a second.
FAST_TIMEOUTS = {
    "freeze_ack_s": 0.4, "snapshot_s": 0.4, "session_ready_s": 0.4, "live_visible_s": 0.4,
    "held_extra_s": 0.4, "art_visible_extra_s": 0.4, "entering_extra_s": 0.3, "abort_ack_s": 0.2,
    "backoff_s": [0.2, 0.4, 0.6, 0.8], "fallback_window_s": 600, "stable_reset_s": 60, "adopt_wait_s": 1.0,
    "page_hb_idle_s": 0.3, "page_dead_s": 1.5, "page_hello_s": 2.0,
    "step_timeout_s": 5.0, "after_timeout_s": 5.0, "http_s": 3.0, "default_transition_s": 0.1,
    "create_session_s": 3.0, "policy_ack_s": 1.0, "sweep_delay_s": 0.2, "settle_before_go_s": 0.05,
}

LIVE = {"live": {"scene": "fractal-zoom", "transitions": {"in": "xfade", "out": "xfade"}, "profile": "h264-720p30"}}

MODES_YAML = """
# ROOM_STATE_MODEL v1.1 grammar (YAML loads as well as JSON).
default: gallery
modes:
  gallery:
    label: Gallery
    state:
      screens: art
      sensor_mask: follow
  live-test:
    label: Live test
    extends: gallery
    state:
      screens.devtest: {live: {scene: fractal-zoom, transitions: {in: xfade, out: xfade}, profile: h264-720p30}}
  show:
    label: Show
    extends: live-test
    state:
      sensor_mask: suspended
      mirror_ball: spinning
    on_enter:
      - {signal: log.info, args: {msg: "show on"}}
transitions:
  - {from: "*", to: "*", steps: [rest]}
"""


@pytest.fixture(autouse=True)
def _no_site_destinations(tmp_path_factory, monkeypatch):
    """Hermetic screens: a config without service.destinations_file reads none (not the repo's real
    publish-destinations.json), so screens come from the test's own `screens:` block."""
    import room_service.config as rc
    monkeypatch.setattr(rc, "DEFAULT_DESTINATIONS", tmp_path_factory.mktemp("site") / "no-destinations.json")


class Harness:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.state_dir = tmp / "state"
        self.http: aiohttp.ClientSession | None = None
        self.stream = FakeStreamService(STREAM_TOKEN)
        self.flask = FakeFlask()
        self.snap = FakeSnapshotter()
        self.svc: RoomService | None = None
        self.server: TestServer | None = None
        self.pages: list[FakePage] = []
        self.overrides: dict[str, Any] = {}
        self.extra_adapters: list = []         # replace built adapters by subsystem (room_service.roomstate)

    def config(self) -> Any:
        (self.tmp / "stream.token").write_text(STREAM_TOKEN + "\n")
        os.chmod(self.tmp / "stream.token", 0o600)
        modes = self.tmp / "modes.yaml"
        if not modes.exists():
            modes.write_text(MODES_YAML)
        dests = self.tmp / "publish-destinations.json"      # make_harness(destinations=[...]): the site's screens
        if "destinations" in self.overrides:
            dests.write_text(json.dumps(self.overrides["destinations"]))
        raw = {
            "stream_service": {"url": self.stream.url, "token_file": str(self.tmp / "stream.token")},
            "flask": {"url": self.flask.url, "ws": self.flask.ws_url},
            "art_page_base": "http://127.0.0.1:1",
            "mask": {"deadband_brightness": 0.005, "deadband_warm_alpha": 0.002, "transition_ms": 200,
                     # Tests run faster than the production 1 req/s/screen (test_mask checks that).
                     "backstop_s": 1.0, "min_fetch_interval_s": 0.05, **self.overrides.get("mask", {})},
            "screens": self.overrides.get("screens", {
                "devtest": {"gpu": 0, "live_enabled": True, "mask_push": True},
                "north-screen": {"gpu": 0, "live_enabled": False, "mask_push": False},
                "south-screen": {"gpu": 1, "live_enabled": False, "mask_push": False}}),
            "timeouts": {**FAST_TIMEOUTS, **self.overrides.get("timeouts", {})},
            "sonos": {"enabled": False},          # never reach the real Sonos from a test
            "service": {"state_dir": str(self.state_dir), "token_file": str(self.tmp / "room.token"),
                        "page_keys_file": str(self.tmp / "page_keys.json"),
                        "modes_file": str(modes), "destinations_file": str(dests),
                        "output_dir": str(self.tmp / "output"),   # never the repo's real art
                        **self.overrides.get("service", {})},
        }
        return config_from_dict(raw)

    async def start(self) -> "Harness":
        self.http = aiohttp.ClientSession()
        await self.stream.start()
        await self.flask.start()
        await self.start_room()
        return self

    async def start_room(self) -> None:
        # Fake hub blinds: tests never reach the real Bloc hub on the LAN. No RM4 Pro remotes either: the glitterball
        # stays the built-in placeholder, whatever codes the repo's config/room/rf/glitterball.json has. No real
        # show_service either: show.main is a placeholder.
        blinds = FakeLevel("blinds", {f"ch{n:02d}": 0 for n in range(2, 15)}, tolerance=3)
        self.svc = RoomService(self.config(), snapshotter=self.snap, token=ROOM_TOKEN,
                               extra_adapters=self.extra_adapters, blinds=blinds, remotes=[], show=False)
        self.server = TestServer(self.svc.app, host="127.0.0.1")
        await self.server.start_server()
        await self.wait(lambda: self.svc.stream.reachable, 3.0, "stream events connected")

    async def stop_room(self) -> None:
        if self.server is not None:
            await self.server.close()
            self.server = None

    async def restart_room(self) -> None:
        await self.stop_room()
        await self.start_room()

    async def stop(self) -> None:
        for p in self.pages:
            await p.close()
        await self.stop_room()
        await self.stream.stop()
        await self.flask.stop()
        if self.http:
            await self.http.close()

    @property
    def url(self) -> str:
        return str(self.server.make_url("")).rstrip("/")

    async def page(self, screen: str = "devtest", role: str = "kiosk", **kw: Any) -> FakePage:
        p = FakePage(self.url, screen, role, **kw)
        await p.connect()
        self.pages.append(p)
        if p.send_hello:
            await p.wait_for("welcome")
        return p

    async def api(self, method: str, path: str, json: Any = None, token: str | None = ROOM_TOKEN,
                  headers: dict | None = None) -> tuple[int, Any]:
        h = dict(headers or {})
        if token:
            h["Authorization"] = f"Bearer {token}"
        async with self.http.request(method, self.url + path, json=json, headers=h) as r:
            ctype = r.headers.get("Content-Type", "")
            if "json" in ctype:
                return r.status, await r.json()
            return r.status, await r.read()

    def screen(self, sid: str = "devtest"):
        return self.svc.screens[sid]

    async def wait(self, pred: Callable[[], bool], timeout: float = 3.0, what: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while not pred():
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {what}")
            await asyncio.sleep(0.01)

    async def wait_phase(self, phase: str, sid: str = "devtest", timeout: float = 3.0) -> None:
        c = self.screen(sid)
        await self.wait(lambda: c.phase == phase, timeout, f"{sid} phase {phase} (now {c.phase} {c.detail})")

    async def go_live(self, page: FakePage | None = None, desired: dict | None = None) -> FakePage:
        page = page or await self.page()
        st, _ = await self.api("PUT", "/api/v1/screens/devtest", {"desired": desired or LIVE})
        assert st == 202
        await self.wait(lambda: self.screen().layer == "live" and self.screen().phase in ("entering", "live"),
                        3.0, "devtest live")
        return page


@pytest_asyncio.fixture
async def harness(tmp_path: Path):
    h = Harness(tmp_path)
    await h.start()
    try:
        yield h
    finally:
        await h.stop()


@pytest_asyncio.fixture
async def make_harness(tmp_path: Path):
    """For tests that tweak config before the room starts: `h = await make_harness(timeouts={...})`."""
    made: list[Harness] = []

    async def _make(**overrides: Any) -> Harness:
        h = Harness(tmp_path)
        h.overrides = overrides
        made.append(h)
        await h.start()
        return h

    try:
        yield _make
    finally:
        for h in made:
            await h.stop()
