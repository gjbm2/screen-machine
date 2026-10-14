"""Headless Chrome for Testing plays the dev page's reference player at 720p30 H.264 (MSE, rVFC).

  SM_GPU_TESTS=1 SM_BROWSER_TESTS=1 [SM_TEST_OUT=/dir] pytest stream_service/tests -k chrome

Checks: frames presented over a 10 s window, dropped frames (getVideoPlaybackQuality), playbackRate 1.0,
no edge jumps / stalls, the exit cut fires on the first frame at/after hold_pts, and the colour the page
shows for the held target (a data point for the day-1 colour check).
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
import urllib.parse

import aiohttp
import numpy as np
import pytest

from .conftest import BASE, GPU
from .test_integration_gpu import Events

pytestmark = [pytest.mark.gpu, pytest.mark.browser]

CHROME = pathlib.Path(os.environ.get(
    "SM_CHROME", "~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome")).expanduser()
TARGET = (30, 90, 200)
WINDOW_S = 10.0


async def test_chrome_playback(service, tmp_path):
    from playwright.async_api import async_playwright
    from PIL import Image

    out_dir = pathlib.Path(os.environ.get("SM_TEST_OUT", tmp_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    H = {"Authorization": f"Bearer {service['token']}"}
    async with aiohttp.ClientSession() as http:
        ev = Events(await http.ws_connect(f"{BASE}/api/events?token={service['token']}"))
        body = {"screen": "devtest", "gpu": GPU, "profile": "h264-720p30", "anchor": {"rgb": [40, 40, 40]},
                "scene": "fractal-zoom", "transitions": {"in": "crossfade-1s", "out": "crossfade-1s"}, "ttl_s": 120}
        r = await http.post(f"{BASE}/api/sessions", json=body, headers=H)
        assert r.status == 201, await r.text()
        res = await r.json()
        sid, attach = res["session"], res["attach"]
        await ev.wait(lambda e: e["type"] == "session.ready" and e["session"] == sid, 5.0)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(executable_path=str(CHROME), headless=True,
                                               args=["--autoplay-policy=no-user-gesture-required"])
            try:
                page = await browser.new_page(viewport={"width": 1400, "height": 1000})
                # the dev page reads its token from localStorage: seed it so the catalog-driven forms load too
                await page.add_init_script(f"localStorage.setItem('sm.stream.token', {json.dumps(service['token'])})")
                logs: list[str] = []
                page.on("console", lambda m: logs.append(m.text))
                page.on("pageerror", lambda e: logs.append(f"pageerror {e}"))
                await page.goto(f"{BASE}/dev?attach={urllib.parse.quote(attach['url'], safe='')}&target_ms="
                                f"{attach['target_buffer_ms']}")
                await page.wait_for_function("window.__player && window.__player.stats().presented > 5",
                                             timeout=15000)
                # catalog-driven UI: scene select and the schema-generated parameter form are populated
                await page.wait_for_function("document.querySelectorAll('#sceneParams [data-param]').length > 5",
                                             timeout=5000)
                assert await page.locator("#scene option").count() >= 5
                r = await http.post(f"{BASE}/api/sessions/{sid}/go", json={}, headers=H)
                assert r.status == 200
                await asyncio.sleep(2.0)                       # through the 1 s crossfade
                s0 = await page.evaluate("window.__player.stats()")
                t0 = time.monotonic()
                await asyncio.sleep(WINDOW_S)
                s1 = await page.evaluate("window.__player.stats()")
                dt = time.monotonic() - t0
                await page.screenshot(path=str(out_dir / "chrome_live.png"))

                # exit: the page cuts on the first presented frame with mediaTime >= hold_pts - 0.5/fps
                r = await http.post(f"{BASE}/api/sessions/{sid}/exit", json={"target": {"rgb": list(TARGET)}},
                                    headers=H)
                assert r.status == 202
                held = await ev.wait(lambda e: e["type"] == "session.held" and e["session"] == sid, 5.0)
                mt = await page.evaluate("(pts) => new Promise(r => window.__player.watchHold(pts, r))",
                                         held["hold_pts"])
                await asyncio.sleep(0.5)
                await page.locator("#stage video").screenshot(path=str(out_dir / "chrome_held.png"))
                s2 = await page.evaluate("window.__player.stats()")
            finally:
                await browser.close()
        await http.delete(f"{BASE}/api/sessions/{sid}", headers=H)
        await ev.ws.close()

    presented = s1["presented"] - s0["presented"]
    expected = dt * 30
    dropped = (s1["dropped"] or 0) - (s0["dropped"] or 0)
    total = (s1["total"] or 0) - (s0["total"] or 0)
    held_px = np.asarray(Image.open(out_dir / "chrome_held.png").convert("RGB")).reshape(-1, 3)
    held_rgb = held_px[len(held_px) // 4: 3 * len(held_px) // 4].mean(axis=0)
    print(f"\nchrome: presented {presented}/{expected:.0f} in {dt:.1f} s ({presented / expected:.1%}), "
          f"decoded {total}, dropped {dropped}, buffered {s1['buffered_ms']} ms, rate {s1['playback_rate']}, "
          f"edge jumps {s1['edge_jumps']}, gap skips {s1['gap_skips']}, stalls {s1['stalls']}; exit cut at "
          f"mediaTime {mt:.3f} (hold_pts {held['hold_pts']}); held target shown as "
          f"{tuple(round(v, 1) for v in held_rgb)} vs PNG {TARGET}")
    errors = [m for m in logs if "pageerror" in m]
    assert not errors, errors
    assert s1["playback_rate"] == 1.0
    assert presented >= 0.9 * expected, s1
    assert dropped <= 0.02 * max(total, 1), s1
    assert s1["edge_jumps"] == 0 and s1["stalls"] == 0
    assert mt >= held["hold_pts"] - 0.5 / 30
    assert s2["stalls"] == 0
    # Headless Chrome's software YUV->RGB path decodes B with ~BT.601's Cb gain (measured 2026-10-13: greys
    # exact, B off by up to ~12 on saturated blues; ffmpeg decodes the same stream within +-2).  So only a
    # loose bound here; the real colour check is on the kiosk (VA-API) per AV_PROGRAMME §3.3.
    assert np.all(np.abs(held_rgb - np.array(TARGET)) <= 10), held_rgb
