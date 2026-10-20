"""Snapshotter against the static art-only fixture, in the cached headless Chrome for Testing.

Skipped if the cached browser isn't there. Uses a small viewport for speed, plus one
full 3840x2160 shot.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from room_service.config import DEFAULT_CHROME, config_from_dict
from room_service.snapshot import PlaywrightSnapshotter, SnapshotError

FIXTURES = Path(__file__).parent / "fixtures"
CHROME = Path(DEFAULT_CHROME).expanduser()

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(not CHROME.exists(), reason="cached Chrome for Testing not found")]


def solid_png(rgb: tuple[int, int, int], size=(320, 180)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, format="PNG")
    return buf.getvalue()


def make_clip(path: Path) -> None:
    """2 s clip: red for 1 s, then blue."""
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1:r=30",
                    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1:r=30",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(path)], check=True)


@pytest.fixture
async def art_server(tmp_path):
    (tmp_path / "red.png").write_bytes(solid_png((255, 0, 0)))
    (tmp_path / "wide.png").write_bytes(solid_png((0, 255, 0), size=(400, 100)))
    if shutil.which("ffmpeg"):
        make_clip(tmp_path / "clip.mp4")

    async def display(request: web.Request) -> web.FileResponse:
        assert request.query.get("artonly") == "1"
        return web.FileResponse(FIXTURES / "artonly.html")

    app = web.Application()
    app.router.add_get("/display/{screen}", display)
    app.router.add_static("/output/", tmp_path)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield str(server.make_url("")).rstrip("/")
    await server.close()


# Correctness tests use a generous budget so a busy shared host (other agents' builds) can't
# fail them; the tests about timing pass the production 3 s (or less) explicitly.
def make_snapper(base: str, w: int = 640, h: int = 360, timeout_s: float = 15.0) -> PlaywrightSnapshotter:
    cfg = config_from_dict({"art_page_base": base, "flask": {"url": base},
                            "snapshot": {"viewport_w": w, "viewport_h": h, "timeout_s": timeout_s,
                                         "page_load_timeout_s": 15}})
    return PlaywrightSnapshotter(cfg)


def state(url: str, brightness: float = 1.0, **kw) -> dict:
    s = {"base_url": url, "kind": "image", "clip_t": None,
         "mask": {"brightness": brightness, "warm_hex": "#FFFFFF", "warm_alpha": 0.0},
         "flags": {"nomask": False, "nocolour": False, "fit": "fit"}, "viewport": {"w": 3840, "h": 2160}}
    s.update(kw)
    return s


def px(png: bytes, x: float, y: float) -> tuple[int, int, int]:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    return im.getpixel((int(x * (im.width - 1)), int(y * (im.height - 1))))


def close(a, b, tol=3) -> bool:
    return all(abs(p - q) <= tol for p, q in zip(a, b))


async def test_renders_state_with_mask_and_reuses_the_page(art_server):
    snap = make_snapper(art_server)
    try:
        t0 = time.monotonic()
        png = await snap.snapshot("devtest", state("/output/red.png", brightness=1.0))
        first_ms = (time.monotonic() - t0) * 1000
        assert png.startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(png)).size == (640, 360)
        assert close(px(png, 0.5, 0.5), (255, 0, 0))
        # Brightness 0.5 -> a 50 % black layer over the red.
        png = await snap.snapshot("devtest", state("/output/red.png", brightness=0.5))
        r, g, b = px(png, 0.5, 0.5)
        assert 120 <= r <= 135 and g < 5 and b < 5
        # Warm layer (multiply) with nocolour honoured.
        warm = {"brightness": 1.0, "warm_hex": "#000000", "warm_alpha": 1.0}
        png = await snap.snapshot("devtest", state("/output/red.png", mask=warm))
        assert close(px(png, 0.5, 0.5), (0, 0, 0))
        png = await snap.snapshot("devtest", state("/output/red.png", mask=warm,
                                                   flags={"nomask": False, "nocolour": True, "fit": "fit"}))
        assert close(px(png, 0.5, 0.5), (255, 0, 0))
        # fit (letterbox) vs fill (cover) for a 4:1 image.
        png = await snap.snapshot("devtest", state("/output/wide.png"))
        assert close(px(png, 0.5, 0.05), (0, 0, 0)) and close(px(png, 0.5, 0.5), (0, 255, 0))
        png = await snap.snapshot("devtest", state("/output/wide.png",
                                                   flags={"nomask": False, "nocolour": False, "fit": "fill"}))
        assert close(px(png, 0.5, 0.05), (0, 255, 0))
        st = snap.status()
        assert st["pages"] == ["devtest"] and st["count"] == 6 and st["browser"] is True
        assert st["last_ms"]["devtest"] < first_ms, "the warm page is reused"
    finally:
        await snap.close()


async def test_full_4k_snapshot(art_server):
    snap = make_snapper(art_server, 3840, 2160, timeout_s=3.0)
    try:
        # Like the service: the page is loaded (ensure_ready) before the 3 s snapshot budget.
        await snap.ensure_ready("devtest")
        await snap.snapshot("devtest", state("/output/red.png"))       # warm
        t0 = time.monotonic()
        png = await snap.snapshot("devtest", state("/output/red.png", brightness=0.8))
        ms = (time.monotonic() - t0) * 1000
        assert Image.open(io.BytesIO(png)).size == (3840, 2160)
        assert ms < 3000, f"4K snapshot took {ms:.0f} ms"
        print(f"\n4K snapshot: {ms:.0f} ms, {len(png) / 1024:.0f} KiB")
    finally:
        await snap.close()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
async def test_clip_base_is_extracted_with_ffmpeg(art_server):
    snap = make_snapper(art_server)
    try:
        await snap.ensure_ready("devtest")          # page load is outside the snapshot budget
        png = await snap.snapshot("devtest", state("/output/clip.mp4", kind="video", clip_t=0.5))
        assert close(px(png, 0.5, 0.5), (254, 0, 0), tol=12), px(png, 0.5, 0.5)
        png = await snap.snapshot("devtest", state("/output/clip.mp4", kind="video", clip_t=1.5))
        r, g, b = px(png, 0.5, 0.5)
        assert b > 200 and r < 30, (r, g, b)
        assert not snap._frames, "extracted frames are not kept"
    finally:
        await snap.close()


async def warm(snap: PlaywrightSnapshotter, *screens: str) -> None:
    """What the service does at startup: open the pages before the first handshake."""
    await snap.start(list(screens))
    await asyncio.gather(*snap._warm_tasks.values())


async def test_timeout_and_recovery(art_server):
    snap = make_snapper(art_server, timeout_s=1.0)
    try:
        await warm(snap, "devtest")
        await snap.snapshot("devtest", state("/output/red.png"))
        t0 = time.monotonic()
        with pytest.raises(SnapshotError, match="timed out"):
            await snap.snapshot("devtest", state("/output/hang"))
        assert time.monotonic() - t0 < 1.5
        # The wedged page is replaced in the background; the next snapshot works.
        await asyncio.gather(*snap._warm_tasks.values())
        png = await snap.snapshot("devtest", state("/output/red.png", brightness=0.5))
        assert px(png, 0.5, 0.5)[0] in range(120, 136)
    finally:
        await snap.close()


async def test_crashed_or_closed_page_is_recreated(art_server):
    snap = make_snapper(art_server)
    try:
        await snap.ensure_ready("devtest")
        await snap.snapshot("devtest", state("/output/red.png"))
        await snap._pages["devtest"].close()          # simulate a dead page
        # As in the service: ensure_ready (before the freeze, outside the 3 s budget) rebuilds it.
        await snap.ensure_ready("devtest")
        png = await snap.snapshot("devtest", state("/output/red.png"))
        assert close(px(png, 0.5, 0.5), (255, 0, 0))
        # One page per screen.
        await snap.ensure_ready("north-screen")
        await snap.snapshot("north-screen", state("/output/red.png"))
        assert snap.status()["pages"] == ["devtest", "north-screen"]
    finally:
        await snap.close()


async def test_missing_image_is_an_error(art_server):
    snap = make_snapper(art_server)
    try:
        with pytest.raises(SnapshotError):
            await snap.snapshot("devtest", state("/output/nope.png"))
    finally:
        await snap.close()


async def test_ensure_ready_and_prefetch(art_server):
    snap = make_snapper(art_server, timeout_s=1.0)
    try:
        t0 = time.monotonic()
        await snap.ensure_ready("devtest")     # cold: browser launch + page load, no 1 s limit
        cold_ms = (time.monotonic() - t0) * 1000
        snap.prefetch("devtest", state("/output/red.png"))
        await asyncio.sleep(0.05)
        async with snap._lock("devtest"):      # wait for the prefetch to finish
            pass
        assert await snap._pages["devtest"].evaluate("window.__smRenderCount") == 1
        png = await snap.snapshot("devtest", state("/output/red.png"))
        assert close(px(png, 0.5, 0.5), (255, 0, 0))
        print(f"\ncold page load {cold_ms:.0f} ms")
    finally:
        await snap.close()
