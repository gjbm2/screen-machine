"""Live with no page (the session runs whether or not a page shows it) and PUT /screens/{id} {desired, for_s}."""

from __future__ import annotations

import asyncio
import json
import time

from .conftest import LIVE, Harness


def creates(h: Harness) -> list:
    return [c for c in h.stream.calls if c[0] == "POST" and c[1] == "/api/sessions"]


async def put(h: Harness, desired, **extra):
    return await h.api("PUT", "/api/v1/screens/devtest", {"desired": desired, **extra})


async def live_headless(h: Harness) -> str:
    st, _ = await put(h, LIVE)
    assert st == 202
    c = h.screen()
    await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 3, "live with no page")
    return c.session["id"]


async def test_headless_anchor_is_a_snapshot_of_the_current_art(harness: Harness):
    h = harness
    h.flask.outputs["devtest.jpg"] = time.time() - 60
    h.flask.outputs["devtest.mp4"] = time.time() - 3600              # older: the jpg is what's on show
    await live_headless(h)
    state = h.snap.calls[-1][1]
    assert state["base_url"].startswith("/output/devtest.jpg?t=") and state["kind"] == "image"
    assert "png_b64" in creates(h)[0][2]["anchor"]
    st, s = await h.api("GET", "/api/v1/status")
    assert s["screens"]["devtest"]["observed"]["headless"] is True


async def test_headless_anchor_is_black_when_the_snapshot_fails(harness: Harness):
    h = harness
    h.flask.outputs["devtest.jpg"] = time.time()
    h.snap.fail = "art-only page broken"
    await live_headless(h)
    assert creates(h)[0][2]["anchor"] == {"rgb": [0, 0, 0]}


async def test_headless_exit_just_ends_the_session(harness: Harness):
    h = harness
    sid = await live_headless(h)
    st, _ = await put(h, "art")
    c = h.screen()
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "back to art")
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}") and not h.stream.calls_of("POST", f"/api/sessions/{sid}/exit")
    await asyncio.sleep(0.2)                         # the session.ended that the DELETE causes is no fallback
    assert not c.fallbacks and c.last_error is None and c.phase == "idle"


async def test_viewer_joining_picks_up_live(harness: Harness):
    h = harness
    sid = await live_headless(h)
    viewer = await h.page(role="viewer")
    assert viewer.welcome["live"]["session"] == sid and viewer.welcome["live"]["attach"]["url"]
    assert h.screen()._headless()                    # a viewer doesn't make it page-backed


async def test_joined_page_gets_the_seamless_exit(harness: Harness):
    h = harness
    sid = await live_headless(h)
    page = await h.page()
    assert page.welcome_attached == sid
    await page.kiosk_state()                          # layer live on our session: joined
    c = h.screen()
    await h.wait(lambda: not c._headless(), 2, "joined")
    await put(h, "art")
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert [d for d in page.of("freeze") if d["reason"] == "exit"] and page.of("live.leave")


async def test_page_that_cannot_join_gets_the_full_entry(harness: Harness):
    h = harness
    sid = await live_headless(h)
    page = await h.page()
    await page.ack(kind="live_failed", session=sid, reason="warmup-timeout")
    c = h.screen()
    await h.wait(lambda: c.layer == "live" and c.session and c.session["id"] != sid and not c._headless(), 3,
                 "re-entered with the page")
    assert page.of("freeze") and sid not in h.stream.sessions


async def test_restart_adopts_a_headless_session_at_once(harness: Harness):
    h = harness
    sid = await live_headless(h)
    await h.restart_room()
    c = h.screen()
    await h.wait(lambda: c.layer == "live" and c.session and c.session["id"] == sid and c._adopt is None, 0.8,
                 "adopted without waiting for a page")
    assert not h.stream.calls_of("DELETE", f"/api/sessions/{sid}")


# ---------------------------------------------------------------------------
# for_s
# ---------------------------------------------------------------------------

async def test_for_s_goes_back_on_time(harness: Harness):
    h = harness
    st, body = await put(h, LIVE, for_s=0.6)
    assert st == 202 and body["desired_expires_to"] == "art" and 0 < body["desired_expires_in_s"] <= 0.6
    c = h.screen()
    await h.wait(lambda: c.layer == "live", 3, "live")
    st, s = await h.api("GET", "/api/v1/status")
    assert s["screens"]["devtest"]["desired_expires_in_s"] is not None
    t0 = time.monotonic()
    await h.wait(lambda: c.desired == "art", 2, "for_s expired")
    assert time.monotonic() - t0 < 1.0
    await h.wait(lambda: c.session is None, 3, "session ended")
    st, s = await h.api("GET", "/api/v1/status")
    assert s["screens"]["devtest"]["desired_expires_in_s"] is None


async def test_put_without_for_s_cancels_the_timer(harness: Harness):
    h = harness
    await put(h, LIVE, for_s=0.4)
    await put(h, LIVE)
    assert h.screen().expiry is None
    await asyncio.sleep(0.7)
    assert h.screen().desired != "art"


async def test_timed_over_timed_still_restores_the_original(harness: Harness):
    h = harness
    await put(h, LIVE, for_s=30)
    other = {"live": {**LIVE["live"], "scene": "warp-stack"}}
    st, body = await put(h, other, for_s=0.4)
    assert body["desired_expires_to"] == "art"
    await h.wait(lambda: h.screen().desired == "art", 2, "back to the original")


async def test_for_s_is_validated(harness: Harness):
    for bad in (0, -1, "10", True, 10 ** 9):
        st, body = await put(harness, LIVE, for_s=bad)
        assert st == 400 and body["error"] == "bad_for_s", bad


async def test_for_s_deadline_survives_a_restart(harness: Harness):
    h = harness
    await put(h, LIVE, for_s=30)
    await asyncio.sleep(0.1)
    saved = json.loads((h.state_dir / "screens.json").read_text())["screens"]["devtest"]["expiry"]
    assert saved["revert"] == "art" and saved["deadline"] > time.time() + 25
    await h.restart_room()
    left = h.screen().expires_in_s()
    assert left is not None and 25 < left <= 30
    # A deadline that passed while the service was down is honoured just after the start.
    h.screen().expiry["deadline"] = time.time() - 5
    h.screen()._arm_expiry(min_delay=0.1)
    await h.wait(lambda: h.screen().desired == "art", 2, "reverted after restart")
