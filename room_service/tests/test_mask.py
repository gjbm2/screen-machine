"""Mask pusher: lux trigger + backstop -> GET /api/<id>/mask -> mask.set beyond the deadbands.

The fake :8765 server records every data frame a client sends; it must stay empty.
"""

from __future__ import annotations

import asyncio
import time

from .conftest import LIVE, Harness


async def first_mask(page) -> dict:
    return await page.wait_for("mask.set", timeout=3)


async def no_new_mask(page, n: int, wait: float = 0.35) -> None:
    await asyncio.sleep(wait)
    assert len(page.of("mask.set")) == n, page.of("mask.set")


async def test_lux_trigger_pushes_beyond_deadbands(harness: Harness):
    h = harness
    page = await h.page()
    m = await first_mask(page)               # a first page triggers a fetch at once
    assert m == {"rev": 1, "brightness": 0.5, "warm_hex": "#FFFFFF", "warm_alpha": 0.0, "transition_ms": 200}

    h.flask.set_mask("devtest", brightness=0.503)          # inside deadband_brightness (0.005)
    await h.flask.lux(10)
    await no_new_mask(page, 1)

    h.flask.set_mask("devtest", brightness=0.6)
    t0 = time.monotonic()
    await h.flask.lux(20)
    m = await page.wait_for("mask.set", lambda d: d["rev"] == 2, timeout=2)
    assert time.monotonic() - t0 < 0.5, "pushed on the lux trigger, not the 1 s backstop"
    assert m["brightness"] == 0.6

    h.flask.set_mask("devtest", warm_alpha=0.0015)         # inside deadband_warm_alpha (0.002)
    await h.flask.lux(21)
    await no_new_mask(page, 2)
    h.flask.set_mask("devtest", warm_alpha=0.01)
    await h.flask.lux(22)
    await page.wait_for("mask.set", lambda d: d["rev"] == 3 and d["warm_alpha"] == 0.01)
    h.flask.set_mask("devtest", warm_hex="#ffc973")         # any hex change
    await h.flask.lux(23)
    m = await page.wait_for("mask.set", lambda d: d["rev"] == 4)
    assert m["warm_hex"] == "#FFC973"

    st, body = await h.api("GET", "/api/v1/status")
    # Ingest is on with no keys (owner ruling, 14 Oct), so the arbiter adds its own fields (source, latency...).
    mask = body["screens"]["devtest"]["mask"]
    assert {k: mask.get(k) for k in ("brightness", "warm_hex", "warm_alpha", "suspended")} == {
        "brightness": 0.6, "warm_hex": "#FFC973", "warm_alpha": 0.01, "suspended": False}
    assert body["flask"]["ws_connected"] is True and body["flask"]["lux_frames"] >= 5
    assert h.flask.client_frames == [], "room_service must never send a frame on :8765"


async def test_non_lux_frames_are_ignored(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    n_req = len(h.flask.requests)
    h.flask.set_mask("devtest", brightness=0.9)
    await h.flask.broadcast({"type": "overlay", "html": "<b>hi</b>"})
    await h.flask.broadcast({"job_id": "j1", "comfy": {}})
    await asyncio.sleep(0.3)                  # well inside the 1 s backstop
    assert len(h.flask.requests) == n_req
    assert len(page.of("mask.set")) == 1


async def test_backstop_without_lux(make_harness):
    h = await make_harness(mask={"backstop_s": 0.3})
    page = await h.page()
    await first_mask(page)
    h.flask.set_mask("devtest", brightness=0.2)
    m = await page.wait_for("mask.set", lambda d: d["rev"] == 2, timeout=1.5)
    assert m["brightness"] == 0.2


async def test_coalesces_and_rate_limits(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    t0 = time.monotonic()
    n0 = len(h.flask.requests)
    for i in range(200):                      # a lux storm: ~200 frames in ~1 s
        await h.flask.lux(i)
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.2)
    elapsed = time.monotonic() - t0
    n = len(h.flask.requests) - n0
    assert n <= 10 * elapsed + 2, f"{n} requests in {elapsed:.2f} s (budget 10/s)"
    assert n >= 3, "but it did keep fetching"


async def test_suspended_holds_then_resumes(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    h.svc.mask.set_suspended(True)
    h.flask.set_mask("devtest", brightness=0.1)
    await h.flask.lux(1)
    await no_new_mask(page, 1, 0.4)
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["mask"]["suspended"] is True
    assert body["screens"]["devtest"]["mask"]["brightness"] == 0.5   # the held value
    h.svc.mask.set_suspended(False)
    m = await page.wait_for("mask.set", lambda d: d["rev"] == 2)
    assert m["brightness"] == 0.1


async def test_frozen_screen_holds_mask(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    page.ack_frozen = False                   # the freeze stays open until the 0.4 s timeout
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": LIVE})
    await h.wait(lambda: h.screen().frozen_fid is not None, 2)
    h.flask.set_mask("devtest", brightness=0.3)
    await h.flask.lux(1)
    await asyncio.sleep(0.15)
    assert len(page.of("mask.set")) == 1, "no mask.set while frozen"
    await page.wait_for("unfreeze", timeout=2)
    m = await page.wait_for("mask.set", lambda d: d["rev"] == 2, timeout=2)
    assert m["brightness"] == 0.3
    # It arrived after the unfreeze.
    order = [t for t, _ in page.received if t in ("unfreeze", "mask.set")]
    assert order[-2:] == ["unfreeze", "mask.set"]


async def test_failed_fetch_keeps_last_mask(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    h.flask.fail = True
    await h.flask.lux(1)
    await asyncio.sleep(0.3)
    assert len(page.of("mask.set")) == 1
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["mask"]["brightness"] == 0.5
    assert "HTTP 500" in body["flask"]["last_fetch_error"]
    h.flask.fail = False
    h.flask.set_mask("devtest", brightness=0.7)
    await h.flask.lux(2)
    await page.wait_for("mask.set", lambda d: d["brightness"] == 0.7)


async def test_only_mask_push_screens_with_a_page_are_fetched(harness: Harness):
    h = harness
    await asyncio.sleep(1.2)                  # one backstop with no pages: nothing to fetch
    assert h.flask.requests == []
    north = await h.page(screen="north-screen")
    await h.flask.lux(1)
    await asyncio.sleep(0.3)
    assert not [r for r in h.flask.requests if r[1] == "north-screen"]
    assert north.of("mask.set") == [] and north.welcome["mask"] is None


async def test_welcome_and_viewers_get_the_mask(harness: Harness):
    h = harness
    page = await h.page()
    await first_mask(page)
    viewer = await h.page(role="viewer")
    assert viewer.welcome["mask"]["rev"] == 1 and viewer.welcome["mask"]["brightness"] == 0.5
    h.flask.set_mask("devtest", brightness=0.8)
    await h.flask.lux(1)
    await viewer.wait_for("mask.set", lambda d: d["brightness"] == 0.8)
    await page.wait_for("mask.set", lambda d: d["brightness"] == 0.8)


async def test_flask_ws_reconnects_and_only_pongs(harness: Harness):
    h = harness
    h.flask.ping_clients = True              # applies to the next connection
    await h.flask.drop_ws()
    await h.wait(lambda: h.flask.ws_connects >= 2 and h.svc.mask.ws_connected, 4, "reconnected")
    await asyncio.sleep(0.5)
    assert h.flask.pongs > 0, "Flask's pings are answered (its watchdog needs pongs)"
    assert h.flask.client_frames == [], "but never a data frame or a ping of our own"
    page = await h.page()
    await first_mask(page)
    h.flask.set_mask("devtest", brightness=0.25)
    await h.flask.lux(5)
    await page.wait_for("mask.set", lambda d: d["brightness"] == 0.25)
    assert h.flask.client_frames == []


async def test_production_rate_is_at_most_one_fetch_per_second_per_screen(make_harness):
    """LIVE_V1 §2.2b: each GET advances Flask's shared intensity EMA (the production TVs'
    too), so a lux storm must not raise the rate above 1/s/screen; a trigger inside the
    interval is remembered and served when the interval allows, not dropped."""
    h = await make_harness(mask={"min_fetch_interval_s": 1.0})
    page = await h.page()
    await first_mask(page)                    # a first page is fetched at once
    h.flask.set_mask("devtest", brightness=0.8)
    t0 = time.monotonic()
    pushed_at = None
    while time.monotonic() - t0 < 2.6:        # ~10 Hz lux, as the Yocto can do
        await h.flask.lux(1)
        if pushed_at is None and any(d["brightness"] == 0.8 for d in page.of("mask.set")):
            pushed_at = time.monotonic() - t0
        await asyncio.sleep(0.1)
    reqs = [t for t, s in h.flask.requests if s == "devtest"]
    gaps = [b - a for a, b in zip(reqs, reqs[1:])]
    assert gaps and min(gaps) >= 0.95, gaps
    assert len(reqs) <= 4, f"{len(reqs)} GETs in ~2.6 s"
    assert pushed_at is not None and pushed_at < 1.3, pushed_at
