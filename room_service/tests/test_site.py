"""One site config: screens from publish-destinations.json ("live" blocks), the render size, rooms in status,
settle before go, and the renderer-busy wording."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from room_service.config import config_from_dict

from .conftest import LIVE, Harness

DESTS = [
    {"id": "_recent", "name": "Recent", "hidden": True},
    {"id": "north-screen", "name": "North screen", "maxwidth": 3840, "maxheight": 2160,
     "live": {"enabled": True, "gpu": 0, "profile": "hevc-2160p30", "mask_push": True, "auto": {"enabled": True}}},
    {"id": "work-laptop", "name": "Work laptop", "maxwidth": 3840, "maxheight": 2160},      # no live block
    {"id": "devtest", "name": "Dev test screen", "maxwidth": 2160, "maxheight": 3840,
     "live": {"enabled": True, "gpu": 1, "profile": "hevc-2160p60", "mask_push": False}},
]
ROOMS = {"_comment": "x",
         "drawing-room": {"name": "Drawing room", "screens": ["north-screen"], "lights": ["hue.ne"]},
         "lab": {"name": "Lab", "screens": ["devtest"]}}


def test_screens_come_from_destinations_with_a_live_block(tmp_path: Path) -> None:
    p = tmp_path / "d.json"
    p.write_text(json.dumps(DESTS))
    cfg = config_from_dict({"service": {"destinations_file": str(p)}})
    assert list(cfg.screens) == ["north-screen", "devtest"]
    d = cfg.screens["devtest"]
    assert (d.name, d.width, d.height, d.gpu, d.profile, d.live_enabled, d.mask_push) == (
        "Dev test screen", 2160, 3840, 1, "hevc-2160p60", True, False)
    # A YAML screens block overrides per screen and per field, and may add screens.
    cfg = config_from_dict({"service": {"destinations_file": str(p)},
                            "screens": {"devtest": {"live_enabled": False}, "extra": {"gpu": 1}}})
    assert cfg.screens["devtest"].live_enabled is False and cfg.screens["devtest"].width == 2160
    assert cfg.screens["devtest"].gpu == 1 and cfg.screens["extra"].label == "extra"
    # No destinations file: the YAML's screens only.
    cfg = config_from_dict({"service": {"destinations_file": str(tmp_path / "none.json")},
                            "screens": {"devtest": {}}})
    assert list(cfg.screens) == ["devtest"] and cfg.screens["devtest"].width is None


def _create_bodies(h: Harness) -> list[dict]:
    return [b for m, path, b in h.stream.calls if m == "POST" and path == "/api/sessions"]


async def test_render_size_names_and_rooms(make_harness, tmp_path: Path) -> None:
    rooms = tmp_path / "rooms.json"
    rooms.write_text(json.dumps(ROOMS))
    h = await make_harness(destinations=DESTS, screens={}, service={"rooms_file": str(rooms)})
    st, s = await h.api("GET", "/api/v1/status")
    assert st == 200 and sorted(s["screens"]) == ["devtest", "north-screen"]
    assert s["screens"]["devtest"]["name"] == "Dev test screen"
    assert s["rooms"] == [
        {"id": "drawing-room", "name": "Drawing room", "screens": ["north-screen"], "lights": ["hue.ne"],
         "blinds": [], "sensors": [], "effects": []},
        {"id": "lab", "name": "Lab", "screens": ["devtest"], "lights": [], "blinds": [], "sensors": [], "effects": []}]
    # No art file behind the page's base_url: the display's own size (maxwidth x maxheight), as is.
    await h.go_live(desired={"live": {**LIVE["live"], "profile": "hevc-2160p60"}})
    body = _create_bodies(h)[-1]
    assert (body["width"], body["height"], body["gpu"]) == (2160, 3840, 1)
    # On a 720p profile: scaled down to the profile's pixel count, still portrait and even.
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: h.screen().phase == "idle" and h.screen().layer == "art", 3, "back to art")
    await h.go_live(page=h.pages[0])
    body = _create_bodies(h)[-1]
    assert (body["width"], body["height"]) == (720, 1280)
    # The art's own shape wins (a landscape still on portrait-bounded devtest), whatever the window's size.
    c = h.screen()
    c.art_shape = (2160, 1215)
    assert c._render_size("hevc-2160p60") == {"width": 3840, "height": 2160}
    assert c._shaped({"base_url": "/output/x.jpg", "viewport": {"w": 1177, "h": 1326}})["viewport"] == {"w": 2160, "h": 1215}
    c.art_shape = None
    assert c._shaped({"base_url": "/output/x.jpg", "viewport": {"w": 1177, "h": 1326}})["viewport"] is None


async def test_art_shape_is_read_from_the_art_file(make_harness, tmp_path: Path) -> None:
    out = tmp_path / "output"
    out.mkdir()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (2160).to_bytes(4, "big") + (1215).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
    (out / "devtest.png").write_bytes(png)
    h = await make_harness(destinations=DESTS, screens={}, service={"output_dir": str(out)})
    c = h.screen()
    assert await c._art_shape_of({"base_url": "/output/devtest.png?t=1"}) == (2160, 1215)
    assert await c._art_shape_of({"base_url": "/output/../secret.png"}) is None
    assert await c._art_shape_of({"base_url": "/output/missing.jpg"}) is None


def test_snapshot_viewport_follows_the_display(tmp_path: Path) -> None:
    from room_service.snapshot import PlaywrightSnapshotter
    p = tmp_path / "d.json"
    p.write_text(json.dumps(DESTS))
    snap = PlaywrightSnapshotter(config_from_dict({"service": {"destinations_file": str(p)},
                                                   "screens": {"lab-only": {}}}))
    assert snap.viewport("devtest") == {"width": 2160, "height": 3840}
    assert snap.viewport("north-screen") == {"width": 3840, "height": 2160}
    assert snap.viewport("lab-only") == {"width": 3840, "height": 2160}     # snapshot.viewport_w x _h
    # The page's reported viewport sets the shape, at the display's pixel count.
    assert snap.viewport("devtest", {"viewport": {"w": 1600, "h": 900}}) == {"width": 3840, "height": 2160}
    assert snap.viewport("devtest", {"viewport": {"w": None, "h": None}}) == {"width": 2160, "height": 3840}


async def test_settle_before_go(make_harness) -> None:
    h = await make_harness(timeouts={"settle_before_go_s": 0.4})
    page = await h.page()
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": LIVE})
    assert st == 202
    c = h.screen()
    await h.wait(lambda: c.layer == "live", 3, "page acked live_visible")
    t0 = time.monotonic()
    gone = lambda: any(m == "POST" and path.endswith("/go") for m, path, _ in h.stream.calls)  # noqa: E731
    await asyncio.sleep(0.2)
    assert not gone() and c.frozen_fid is not None, "go waits, and the anchor stays held meanwhile"
    await h.wait(gone, 2, "go after the settle")
    assert time.monotonic() - t0 >= 0.3
    await page.wait_for("unfreeze", timeout=2)


async def test_create_timeout_says_renderer_busy(make_harness) -> None:
    h = await make_harness(timeouts={"create_session_s": 0.2})
    await h.page()
    c = h.screen()
    h.stream.create_delay = 0.6
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": LIVE})
    await h.wait(lambda: c.last_error is not None, 3, "the create timed out")
    assert "renderer busy (timed out after 0.2 s)" in c.last_error and "TimeoutError" not in c.last_error
    h.stream.create_delay = 0.0
