"""Screens subsystem: the AV_PROGRAMME §3.3 handshakes against a fake page and stream_service."""

from __future__ import annotations

import asyncio
import base64
import json
import time

from .conftest import LIVE, Harness
from .fakes import FakePage, PNG_SIG


def msgs(page, *types):
    return [(t, d) for t, d in page.received if not types or t in types]


async def test_full_entry_and_exit(harness: Harness):
    h = harness
    page = await h.page()
    assert page.welcome["policy"] == {"live_enabled": True, "mask_push": True, "timed_reload": True}
    assert page.welcome["live"] is None and page.welcome["screen"] == "devtest"

    await h.go_live(page)
    await page.wait_for("unfreeze")
    c = h.screen()
    sid = c.session["id"]
    # Entry, in order: freeze -> (ack) -> live.attach -> (ack visible) -> go -> unfreeze.
    order = [t for t, _ in msgs(page, "freeze", "live.attach", "unfreeze")]
    assert order == ["freeze", "live.attach", "unfreeze"]
    freeze = page.of("freeze")[0]
    assert freeze["reason"] == "enter"
    assert page.of("unfreeze")[0]["fid"] == freeze["fid"]
    attach = page.of("live.attach")[0]
    assert attach["session"] == sid and attach["url"].startswith("ws://") and attach["fps"] == 30
    # The anchor is the snapshot of exactly the state the page acked, in the live render's shape (no art file
    # here, so viewport None: the display's own size, never the window's).
    assert h.snap.calls[0] == ("devtest", {**page.state, "viewport": {"w": None, "h": None}})
    post = h.stream.calls_of("POST", "/api/sessions")[0][2]
    assert base64.b64decode(post["anchor"]["png_b64"]).startswith(PNG_SIG)
    assert post["screen"] == "devtest" and post["gpu"] == 0 and post["profile"] == "h264-720p30"
    assert post["scene"] == "fractal-zoom" and post["transitions"] == {"in": "xfade", "out": "xfade"}
    assert post["ttl_s"] == 120 and post["idempotency_key"].startswith("devtest-")
    assert h.stream.calls_of("POST", f"/api/sessions/{sid}/go")
    await h.wait_phase("live")                    # after the fake's "in done" event
    assert c.anchor_png.startswith(PNG_SIG)

    # Exit.
    n = len(page.received)
    page.state = {**page.state, "base_url": "/output/devtest.jpg?v=2"}
    st, body = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    assert st == 202 and body["desired"] == "art"
    await h.wait(lambda: c.session is None and c.phase == "idle", 3.0, "exit done")
    await page.wait_for("unfreeze", after=n)
    after = page.received[n:]
    assert [t for t, _ in after if t in ("freeze", "live.leave", "unfreeze")] == ["freeze", "live.leave", "unfreeze"]
    assert [d for t, d in after if t == "freeze"][0]["reason"] == "exit"
    assert [d for t, d in after if t == "live.leave"][0] == {"session": sid, "hold_pts": 12.5}
    exit_call = h.stream.calls_of("POST", f"/api/sessions/{sid}/exit")[0][2]
    assert base64.b64decode(exit_call["target"]["png_b64"]).startswith(PNG_SIG)
    assert h.snap.calls[-1][1]["base_url"] == "/output/devtest.jpg?v=2"
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    assert c.layer == "art" and page.layer == "art" and page.frozen is None
    assert c.target_png.startswith(PNG_SIG) and c.last_error is None
    assert "live.abort" not in page.types()


# ---------------------------------------------------------------------------
# Entry timeouts and failures: each aborts cleanly and leaves the screen in art
# ---------------------------------------------------------------------------

def creates(h: Harness) -> list:
    return [c for c in h.stream.calls if c[0] == "POST" and c[1] == "/api/sessions"]


async def assert_clean_entry_abort(h: Harness, page, *, attached: bool, session_created: bool):
    c = h.screen()
    assert c.layer == "art" and c.session is None and c.frozen_fid is None
    freezes = page.of("freeze")
    assert freezes, "a freeze was sent"
    unfreeze = await page.wait_for("unfreeze")
    assert unfreeze["fid"] == freezes[0]["fid"]
    if attached:
        await page.wait_for("live.abort")
    else:
        assert not page.of("live.abort")
    assert bool(creates(h)) == session_created
    if session_created and h.stream.create_status == 201:
        assert h.stream.calls_of("DELETE", "/api/sessions/")
        assert not h.stream.sessions, "no session left behind"


async def put_live(h: Harness, desired=None):
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": desired or LIVE})
    assert st == 202


async def wait_retry(h: Harness):
    """A failed go-live: the screen is back in art with a retry scheduled (never parked, S1)."""
    c = h.screen()
    await h.wait(lambda: c.phase == "idle" and c.detail.startswith("retry in") and c.last_error, 3,
                 f"a retry scheduled (now {c.phase} {c.detail})")


async def test_entry_timeout_no_frozen_ack(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    page.ack_frozen = False
    t0 = time.monotonic()
    await put_live(h)
    await wait_retry(h)
    assert 0.35 < time.monotonic() - t0 < 1.5
    await assert_clean_entry_abort(h, page, attached=False, session_created=False)
    assert "ack frozen" in h.screen().last_error and not h.snap.calls


async def test_entry_snapshot_timeout(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    h.snap.delay = 1.0
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=False, session_created=False)
    assert "snapshot" in h.screen().last_error


async def test_a_snapshot_that_ignores_its_cancel_cannot_hold_the_handshake(make_harness):
    """23 Oct: south's exit sat in phase "snapshot" for 2+ min, no error: a call that swallowed its cancel held the
    wait_for.  The handshake abandons it at the budget and goes on."""
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    h.snap.deaf = asyncio.Event()
    t0 = time.monotonic()
    await put_live(h)
    await wait_retry(h)
    assert time.monotonic() - t0 < 3.0
    await assert_clean_entry_abort(h, page, attached=False, session_created=False)
    assert "snapshot" in h.screen().last_error
    h.snap.deaf.set()


async def test_entry_snapshot_error(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    h.snap.fail = "page crashed"
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=False, session_created=False)


async def test_entry_session_ready_timeout(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    h.stream.emit_ready = False
    page = await h.page()
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=False, session_created=True)
    assert "session.ready" in h.screen().last_error


async def test_entry_create_session_error(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    h.stream.create_status = 500
    page = await h.page()
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=False, session_created=True)
    assert "create session" in h.screen().last_error


async def test_entry_live_visible_timeout(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    page.ack_live = False
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=True, session_created=True)
    assert not h.stream.calls_of("POST", "/api/sessions/s-0001/go")
    assert "live_visible" in h.screen().last_error


async def test_entry_page_live_failed(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    page = await h.page()
    page.live_failed = True
    t0 = time.monotonic()
    await put_live(h)
    await wait_retry(h)
    assert time.monotonic() - t0 < 0.4, "live_failed aborts at once, not at the timeout"
    await assert_clean_entry_abort(h, page, attached=True, session_created=True)
    assert "live_failed" in h.screen().last_error


async def test_entry_go_refused(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    h.stream.go_status = 409
    page = await h.page()
    await put_live(h)
    await wait_retry(h)
    await assert_clean_entry_abort(h, page, attached=True, session_created=True)
    assert page.layer == "art", "the page was hard-cut back to art (it had shown the anchor)"


async def test_entry_failure_retries_with_backoff_and_never_parks(harness: Harness):
    """S1: failed go-lives never park the screen: it retries on the backoff (topping out, never giving up) and goes
    live by itself once the page answers. No new request needed."""
    h = harness
    page = await h.page()
    page.ack_frozen = False
    await put_live(h)
    c = h.screen()
    await h.wait(lambda: len(page.of("freeze")) >= 5, 8, "five tries")
    assert c.phase != "failed" and "entry" in c.last_error
    assert "failed" not in c.snapshot_state()
    page.ack_frozen = True
    await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 5, "live by itself")


async def test_page_gone_during_entry_aborts(harness: Harness):
    h = harness
    page = await h.page()
    page.ack_live = False
    await put_live(h)
    await h.wait_phase("attaching")
    sid = h.screen().session["id"]
    await page.close()
    await h.wait(lambda: h.stream.calls_of("DELETE", f"/api/sessions/{sid}"), 2, "session dropped")
    # Live renders whether or not a page shows it: with no page left it goes on without one (a new session).
    c = h.screen()
    await h.wait(lambda: c.layer == "live" and c.session and c.session["id"] != sid, 3, "live with no page")
    assert c.session.get("headless") and sid not in h.stream.sessions


# ---------------------------------------------------------------------------
# Exit timeouts: abort = live.abort (hard cut), DELETE, unfreeze; screen ends in art
# ---------------------------------------------------------------------------

async def exit_and_wait(h: Harness):
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    assert st == 202
    await h.wait(lambda: h.screen().session is None and h.screen().phase == "idle", 3, "exit settled")


async def assert_clean_exit_abort(h: Harness, page, sid: str):
    c = h.screen()
    assert c.layer == "art" and c.session is None and c.frozen_fid is None
    await page.wait_for("live.abort", lambda d: d["session"] == sid)
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    exit_freeze = [d for d in page.of("freeze") if d["reason"] == "exit"][0]
    await page.wait_for("unfreeze", lambda d: d["fid"] == exit_freeze["fid"])
    assert page.layer == "art"


async def test_exit_timeout_no_frozen_ack(harness: Harness):
    h = harness
    page = await h.go_live()
    sid = h.screen().session["id"]
    page.ack_frozen = False
    await exit_and_wait(h)
    await assert_clean_exit_abort(h, page, sid)
    assert "ack frozen" in h.screen().last_error
    assert not h.stream.calls_of("POST", f"/api/sessions/{sid}/exit")


async def test_exit_timeout_no_held(harness: Harness):
    h = harness
    h.stream.emit_held = False
    page = await h.go_live()
    sid = h.screen().session["id"]
    t0 = time.monotonic()
    await exit_and_wait(h)
    assert time.monotonic() - t0 >= 0.45          # out transition (0.1 s) + held_extra (0.4 s)
    await assert_clean_exit_abort(h, page, sid)
    assert "session.held" in h.screen().last_error and not page.of("live.leave")


async def test_exit_timeout_no_art_visible(harness: Harness):
    h = harness
    page = await h.go_live()
    sid = h.screen().session["id"]
    page.ack_art = False
    await exit_and_wait(h)
    assert page.of("live.leave")
    await assert_clean_exit_abort(h, page, sid)
    assert "art_visible" in h.screen().last_error


async def test_exit_when_page_already_in_art(harness: Harness):
    h = harness
    h.stream.held_delay = 0.3
    page = await h.go_live()
    sid = h.screen().session["id"]
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait_phase("exiting")
    # The page stalls while the out transition runs: the exit just finishes.
    page.layer, page.session = "art", None
    await page.ack(kind="art_visible", session=sid, media_time=5.0, reason="stall")
    await h.wait(lambda: h.screen().session is None and h.screen().phase == "idle", 3)
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}") and h.screen().layer == "art"
    assert not page.of("live.leave")


# ---------------------------------------------------------------------------
# Fallbacks: stall / stream error while live -> re-entry with backoff; never parked
# ---------------------------------------------------------------------------

async def test_stall_reentry_with_backoff(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    sid1 = c.session["id"]
    await h.wait_phase("live")
    t0 = time.monotonic()
    await page.stall()
    await h.wait(lambda: c.session is not None and c.session["id"] != sid1 and c.layer == "live"
                 and c.phase in ("entering", "live"), 3, "re-entered on a new session")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.2, f"first backoff step is 0.2 s (got {elapsed:.3f})"
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid1}")
    assert page.of("freeze")[-1]["reason"] == "reanchor"
    assert "live.abort" not in page.types(), "the page cut itself; no abort needed"
    assert c.last_error is None and len(c.fallbacks) == 1

    # The second fallback waits for the second backoff step.
    sid2 = c.session["id"]
    await h.wait_phase("live")
    t0 = time.monotonic()
    await page.stall()
    await h.wait(lambda: c.session is not None and c.session["id"] != sid2 and c.layer == "live", 3)
    assert time.monotonic() - t0 >= 0.4


async def test_fallbacks_follow_the_backoff_and_never_park(harness: Harness):
    """S2, degrade, never refuse (owner): every fallback re-enters on the backoff (its last step repeats). No limit,
    no slow mode, never parked."""
    h = harness
    page = await h.go_live()
    c = h.screen()
    for i in range(5):
        await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 4, f"live #{i}")
        sid = c.session["id"]
        await page.stall()
        await h.wait(lambda: c.session is None or c.session["id"] != sid, 2)
    await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 4, "live again after 5 fallbacks")
    assert len(c.fallbacks) == 5 and c.phase != "failed"


async def test_a_renderer_restart_is_not_a_fallback(harness: Harness):
    """A deploy ends every session with "service shutdown": not counted, no backoff, straight back."""
    h = harness
    await h.go_live()
    c = h.screen()
    await h.wait(lambda: c.layer == "live", 4, "live")
    before = len(c.fallbacks)
    await c._handle_fallback("stream session.ended: service shutdown")
    assert len(c.fallbacks) == before and c.phase == "idle" and "restarting" in (c.detail or "")
    await h.wait(lambda: c.layer == "live", 5, "back live")


async def test_stream_error_while_live(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    h.stream.fail_session(sid)
    await page.wait_for("live.abort", lambda d: d["session"] == sid)
    await h.wait(lambda: c.session is not None and c.session["id"] != sid and c.layer == "live", 3)
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    assert page.of("freeze")[-1]["reason"] == "reanchor"


async def test_stream_service_lost_waits_then_recovers(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    assert c.session is not None
    port = h.stream.server.port
    await h.stream.stop()                     # stream_service dies
    await h.wait(lambda: not h.svc.stream.reachable, 3)
    await page.stall()                        # the page notices no frames
    await h.wait(lambda: c.session is None, 2)
    await asyncio.sleep(0.5)
    assert c.phase == "idle" and "stream_service" in c.detail
    # It comes back on the same port; live returns by itself.
    from .fakes import FakeStreamService
    from .conftest import STREAM_TOKEN
    h.stream = FakeStreamService(STREAM_TOKEN)
    await h.stream.start(port=port)
    await h.wait(lambda: c.layer == "live" and c.session is not None and c.session["id"] in h.stream.sessions,
                 10, "live again after stream_service restart")


# ---------------------------------------------------------------------------
# Desired changes mid-handshake
# ---------------------------------------------------------------------------

async def test_switch_to_art_mid_entry_aborts_promptly(harness: Harness):
    h = harness
    page = await h.page()
    page.ack_live = False                      # entry parks in "attaching"
    await put_live(h)
    await h.wait_phase("attaching")
    t0 = time.monotonic()
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: h.screen().session is None and h.screen().phase == "idle", 2)
    assert time.monotonic() - t0 < 0.3, "aborted promptly, not at the 0.4 s timeout"
    c = h.screen()
    assert c.layer == "art" and c.frozen_fid is None and c.last_error is None
    await page.wait_for("live.abort")
    await page.wait_for("unfreeze")
    assert not h.stream.calls_of("POST", "/api/sessions/s-0001/go") and not h.stream.sessions
    assert not [d for d in page.of("freeze") if d["reason"] == "exit"], "no exit handshake needed"
    await asyncio.sleep(0.5)
    assert len(page.of("freeze")) == 1, "and no retry"


async def test_switch_to_art_mid_freeze(harness: Harness):
    h = harness
    page = await h.page()
    page.frozen_delay = 0.2
    await put_live(h)
    await h.wait_phase("freezing")
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: h.screen().phase == "idle" and h.screen().frozen_fid is None, 2)
    assert not creates(h)
    await page.wait_for("unfreeze")


async def test_live_requested_mid_exit_is_queued(harness: Harness):
    h = harness
    h.stream.held_delay = 0.2
    page = await h.go_live()
    c = h.screen()
    sid1 = c.session["id"]
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait_phase("exiting")
    await put_live(h)                          # changes its mind during the exit
    await h.wait(lambda: c.session is not None and c.session["id"] != sid1 and c.layer == "live", 3)
    # The exit ran to completion first (leave, DELETE) and then a fresh entry.
    assert page.of("live.leave") and h.stream.calls_of("DELETE", f"/api/sessions/{sid1}")
    reasons = [d["reason"] for d in page.of("freeze")]
    assert reasons == ["enter", "exit", "enter"]


async def test_scene_change_while_live_patches_in_stream(harness: Harness):
    h = harness
    await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    other = {"live": {**LIVE["live"], "scene": "other-scene"}}
    await put_live(h, other)
    await h.wait(lambda: h.stream.calls_of("PATCH", f"/api/sessions/{sid}"), 2)
    body = h.stream.calls_of("PATCH", f"/api/sessions/{sid}")[0][2]
    assert body == {"scene": "other-scene", "transition": "xfade"}
    assert c.session["id"] == sid and len(creates(h)) == 1
    # A profile change can't be patched: exit + entry on a new session.
    await put_live(h, {"live": {**other["live"], "profile": "hevc-2160p60"}})
    await h.wait(lambda: c.session is not None and c.session["id"] != sid and c.layer == "live", 3)
    assert creates(h)[-1][2]["profile"] == "hevc-2160p60"


async def test_patch_live_api(harness: Harness):
    h = harness
    st, body = await h.api("PATCH", "/api/v1/screens/devtest/live", {"params": {"zoom_rate": 0.5}})
    assert st == 409 and body["error"] == "not_live"
    await h.go_live()
    sid = h.screen().session["id"]
    st, body = await h.api("PATCH", "/api/v1/screens/devtest/live", {"params": {"zoom_rate": 0.5}})
    assert st == 200, body
    assert h.stream.calls_of("PATCH", f"/api/sessions/{sid}")[-1][2] == {"params": {"zoom_rate": 0.5}}
    assert h.screen().desired["live"]["params"] == {"zoom_rate": 0.5}
    st, body = await h.api("PATCH", "/api/v1/screens/devtest/live", {"bogus": 1})
    assert st == 400
    await asyncio.sleep(0.2)
    assert len(creates(h)) == 1, "no reconcile churn after a PATCH"


# ---------------------------------------------------------------------------
# Pages: reconnects, reloads, roles
# ---------------------------------------------------------------------------

async def test_page_reload_while_live_joins_the_running_session(harness: Harness):
    # Show continuity (owner, 14 Oct): a reload must not restart the show. The new page load joins the running
    # session (attach in the welcome), with no re-entry, no new session and no freeze.
    h = harness
    page = await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    await page.close()
    page2 = await h.page()                     # a new page_load_id: it loaded in art
    assert page2.welcome["live"]["session"] == sid and page2.welcome["live"]["attach"]
    await page2.kiosk_state(layer="live", session=sid)
    await asyncio.sleep(0.3)
    assert c.session["id"] == sid and c.layer == "live" and not c._headless()
    assert len(creates(h)) == 1 and not h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    assert not page2.of("freeze")
    assert len(c.fallbacks) == 0, "a reload is not counted as a fallback"


async def test_reentry_carries_the_look_over(harness: Harness):
    # Show continuity: a fallback re-creates the session; the new one starts from the old one's look.
    h = harness
    page = await h.go_live()
    c = h.screen()
    sid1 = c.session["id"]
    await h.wait_phase("live")
    h.stream.sessions[sid1].update(
        direction={"energy": 0.9}, current_scene="warp-kaleido",
        auto={"enabled": True, "pool": "all", "armed": True, "fx": {"enabled": True, "max": 2, "armed": False}},
        fx={"stack": [{"id": "fx-1", "preset": "bloom-soft", "level": 0.4, "origin": "manual"},
                      {"id": "fx-2", "effect": "chroma", "origin": "auto"}]})
    await page.stall()
    await h.wait(lambda: c.session is not None and c.session["id"] != sid1, 3, "re-entered on a new session")
    body = creates(h)[-1][2]
    assert body["direction"] == {"energy": 0.9} and body["scene"] == "warp-kaleido"
    assert body["auto"] == {"enabled": True, "pool": "all", "fx": {"enabled": True, "max": 2}}
    await h.wait(lambda: h.stream.calls_of("PUT", f"/api/sessions/{c.session['id']}/fx"), 3, "fx carried")
    assert h.stream.calls_of("PUT", f"/api/sessions/{c.session['id']}/fx")[0][2] == {
        "stack": [{"preset": "bloom-soft", "level": 0.4}], "fade_s": 0}


async def test_page_socket_blip_keeps_live(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    await h.wait_phase("live")
    await page.close()
    page2 = await h.page(page_load_id=page.page_load_id)   # same page, new socket
    assert page2.welcome["live"]["session"] == sid
    # No attach in welcome.live: a page still playing doesn't need it, and one that isn't
    # must not cut into the running scene (see the next test).
    assert page2.welcome["live"]["attach"] is None
    await page2.kiosk_state(layer="live", session=sid)
    await asyncio.sleep(0.3)
    assert c.session["id"] == sid and c.layer == "live" and c.phase == "live"
    assert len(creates(h)) == 1 and not page2.of("freeze")


async def test_socket_blip_after_a_lost_stall_ack_reenters_with_a_matched_cut(harness: Harness):
    """The page fell back to art while its socket was down (its art_visible ack was lost) and
    reconnects as the same page. welcome must not make it attach the running session (that
    would be an unmatched cut into mid-scene); its kiosk.state says art, and re-entry runs
    through the full freeze -> anchor handshake."""
    h = harness
    page = await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    await h.wait_phase("live")
    await page.close()
    page2 = FakePage(h.url, page_load_id=page.page_load_id)   # same page_load_id, but in art now
    await page2.connect()
    h.pages.append(page2)
    await page2.wait_for("welcome")
    assert page2.welcome_attached is None and page2.layer == "art", "welcome made the page attach"
    await page2.kiosk_state(layer="art")
    freeze = await page2.wait_for("freeze", timeout=3)
    assert freeze["reason"] == "reanchor"
    attach = await page2.wait_for("live.attach", timeout=3)
    assert attach["session"] != sid
    await h.wait(lambda: c.layer == "live" and c.session and c.session["id"] == attach["session"], 3,
                 "re-entered on a new session")
    assert sid not in h.stream.sessions


async def test_page_gone_too_long_keeps_rendering(make_harness):
    h = await make_harness(timeouts={"adopt_wait_s": 0.5})
    page = await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    await page.close()
    await h.wait(lambda: c._headless(), 3, "session runs on without the page")
    assert not h.stream.calls_of("DELETE", f"/api/sessions/{sid}") and c.layer == "live"
    # A page that comes later joins the running session: attach in the welcome, no new session.
    n = len(creates(h))
    page2 = await h.page()
    assert page2.welcome["live"]["session"] == sid and page2.welcome["live"]["attach"]["url"]
    await page2.kiosk_state()
    await h.wait(lambda: not c._headless(), 2, "joined")
    assert len(creates(h)) == n and not page2.of("freeze") and c.session["id"] == sid


async def test_no_page_goes_live_and_a_page_joins(harness: Harness):
    h = harness
    await put_live(h)
    c = h.screen()
    await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 3, "live with no page")
    sid = c.session["id"]
    assert c.session.get("headless") and len(creates(h)) == 1
    assert creates(h)[0][2]["anchor"] == {"rgb": [0, 0, 0]}      # no current art known: black
    assert h.stream.calls_of("POST", f"/api/sessions/{sid}/go")
    page = await h.page()
    assert page.welcome["live"]["session"] == sid and page.welcome["live"]["attach"]["url"]
    await page.ack(kind="live_visible", session=sid, media_time=0.0)
    await h.wait(lambda: not c._headless(), 2, "joined")
    assert not page.of("freeze") and len(creates(h)) == 1


async def test_viewers_get_mask_but_no_control(harness: Harness):
    h = harness
    viewer = await h.page(role="viewer")
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["observed"]["page_connected"] is False
    assert body["screens"]["devtest"]["observed"]["viewers"] == 1
    await h.go_live()
    # A viewer is a subscriber (pub/sub): it is attached to the show, but gets no handshakes.
    assert not [t for t in viewer.types() if t in ("freeze", "unfreeze")]
    assert viewer.welcome["live"] is None
    await viewer.wait_for("live.attach", lambda d: d["session"] == h.screen().session["id"])
    # A viewer's acks are ignored.
    await viewer.ack(kind="art_visible", session=h.screen().session["id"], reason="stall")
    await asyncio.sleep(0.2)
    assert h.screen().layer == "live"


async def test_duplicate_glass_clients_warn_and_newest_is_driven(harness: Harness):
    h = harness
    ws_events = await h.http.ws_connect(h.url + "/api/v1/events?token=" + "r" * 64)
    p1 = await h.page()
    p2 = await h.page()
    logs = []
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not logs:
        msg = await ws_events.receive(timeout=2)
        d = msg.json()
        if d["type"] == "log" and "glass clients" in d["data"]["msg"]:
            logs.append(d)
    await ws_events.close()
    assert logs and logs[0]["data"]["level"] == "warning"
    await h.go_live(p2)
    assert p2.of("freeze") and not p1.of("freeze")
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["observed"]["glass_clients"] == 2


async def test_kiosk_state_reaches_status(harness: Harness):
    h = harness
    page = await h.page(role="lab")
    await page.kiosk_state()
    await h.wait(lambda: h.screen().kiosk_state is not None, 2)
    st, body = await h.api("GET", "/api/v1/status")
    obs = body["screens"]["devtest"]["observed"]
    assert obs["page_role"] == "lab" and obs["page_connected"] is True
    assert obs["kiosk_state"]["layer"] == "art" and obs["kiosk_state"]["base_url"] == page.state["base_url"]


async def test_kiosk_state_art_while_live_is_a_fallback(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    await page.kiosk_state(layer="art")
    await h.wait(lambda: c.session is None or c.session["id"] != sid, 2)
    assert h.stream.calls_of("DELETE", f"/api/sessions/{sid}") and len(c.fallbacks) == 1


async def test_dead_page_socket_is_closed(harness: Harness):
    h = harness
    page = await h.page(hb_interval=100)        # never sends hb
    await h.wait(lambda: page.ws.closed, 3, "server closed the silent socket")
    assert page.ws.close_code == 4001


async def test_server_sends_hb_when_idle(harness: Harness):
    h = harness
    page = await h.page()
    hb = []
    orig = page._handle

    async def spy(t, d):
        if t == "hb":
            hb.append(time.monotonic())
        await orig(t, d)

    page._handle = spy
    await asyncio.sleep(1.0)
    assert len(hb) >= 2                         # page_hb_idle_s = 0.3 in tests


async def test_unknown_screen_and_bad_role(harness: Harness):
    h = harness
    # Unknown screen: the upgrade is accepted and closed with 4404 (a refused upgrade would
    # look like a network failure, 1006, to the page).
    ws = await h.http.ws_connect(h.url + "/v1/screen/nope?role=kiosk")
    await ws.receive(timeout=2)
    assert ws.close_code == 4404
    await ws.close()
    async with h.http.get(h.url + "/v1/screen/devtest?role=boss") as r:
        assert r.status == 400


async def test_live_disabled_screen_refused(harness: Harness):
    h = harness
    st, body = await h.api("PUT", "/api/v1/screens/north-screen", {"desired": LIVE})
    assert st == 409 and body["error"] == "live_disabled"
    page = await h.page(screen="north-screen")
    assert page.welcome["policy"] == {"live_enabled": False, "mask_push": False, "timed_reload": True}
    assert page.welcome["mask"] is None


# ---------------------------------------------------------------------------
# Restart: re-read desired state and the sessions; adopt or delete orphans
# ---------------------------------------------------------------------------

async def test_restart_adopts_live_session(harness: Harness):
    h = harness
    page = await h.go_live()
    await h.wait_phase("live")
    sid = h.screen().session["id"]
    await page.close()
    await h.restart_room()                     # room_service restarts; the session keeps running
    c = h.screen()
    assert c.desired == LIVE or c.desired["live"]["scene"] == "fractal-zoom"
    page2 = await h.page(page_load_id=page.page_load_id)   # the same page reconnects, still playing
    page2.layer, page2.session = "live", sid
    assert page2.welcome["live"]["session"] == sid
    await page2.kiosk_state()
    await h.wait(lambda: c.phase == "live" and c.session and c.session["id"] == sid, 3, "adopted")
    assert not h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    assert len(creates(h)) == 1 and not page2.of("freeze")
    # And it can still leave live normally.
    await exit_and_wait(h)
    assert page2.of("live.leave") and h.stream.calls_of("DELETE", f"/api/sessions/{sid}")


async def test_restart_deletes_orphan_when_page_shows_art(harness: Harness):
    h = harness
    page = await h.go_live()
    sid = h.screen().session["id"]
    await page.close()
    await h.restart_room()
    page2 = await h.page()                     # a reloaded page: art
    await page2.kiosk_state(layer="art")
    c = h.screen()
    await h.wait(lambda: h.stream.calls_of("DELETE", f"/api/sessions/{sid}"), 3, "orphan deleted")
    # desired is still live: a fresh entry follows.
    await h.wait(lambda: c.layer == "live" and c.session and c.session["id"] != sid, 3)


async def test_restart_no_page_drops_sessions_after_wait(harness: Harness):
    h = harness
    page = await h.go_live()
    sid = h.screen().session["id"]
    await page.close()
    await h.restart_room()
    await h.wait(lambda: h.stream.calls_of("DELETE", f"/api/sessions/{sid}"), 3, "dropped after adopt_wait_s")
    # The old session is let go. (desired is still live, so a new headless session may already be up: live renders
    # whether or not a page shows it. Asserting "no session" right after the DELETE raced that re-entry.)
    c = h.screen()
    await h.wait(lambda: c.session is None or c.session["id"] != sid, 2, "old session let go")
    assert c.session is None or c.session.get("headless")


async def test_restart_while_art_deletes_stray_sessions(harness: Harness):
    h = harness
    await h.page()
    # A session of ours the room no longer uses (a crash mid-entry), and others' sessions
    # (owner tags, DCL v3 §4.7.6): only ours is ever deleted; the others are reported.
    h.stream.sessions["s-stray"] = {"session": "s-stray", "screen": "devtest", "state": "holding"}
    h.svc.screens.own("s-stray", "devtest")
    h.stream.sessions["s-theirs"] = {"session": "s-theirs", "screen": "devtest", "state": "holding"}
    h.stream.sessions["s-alien"] = {"session": "s-alien", "screen": "nowhere", "state": "live"}
    await h.restart_room()
    page2 = await h.page()
    await page2.kiosk_state(layer="art")
    await h.wait(lambda: "s-stray" not in h.stream.sessions, 3, "our stray deleted")
    assert {"s-theirs", "s-alien"} <= set(h.stream.sessions), "other instances' sessions are left alone"
    st, s = await h.api("GET", "/api/v1/status")
    assert "s-theirs" in json.dumps(s["stream_service"].get("foreign_sessions"))


# ---------------------------------------------------------------------------
# Snapshotter readiness and prefetch
# ---------------------------------------------------------------------------

async def test_cold_snapshotter_is_waited_for_before_the_freeze(harness: Harness):
    h = harness
    h.snap.ready_delay = 1.0                   # longer than the 0.4 s snapshot budget
    page = await h.page()
    t0 = time.monotonic()
    await put_live(h)
    await asyncio.sleep(0.5)
    assert not page.of("freeze"), "nothing is frozen while the art-only page loads"
    assert h.screen().detail == "waiting for the snapshotter"
    await h.wait(lambda: h.screen().layer == "live", 3)
    assert time.monotonic() - t0 >= 1.0 and h.screen().last_error is None


async def test_snapshotter_unavailable_fails_entry_without_freezing(make_harness):
    h = await make_harness(timeouts={"backoff_s": [30.0]})
    h.snap.not_ready = "chrome missing"
    page = await h.page()
    await put_live(h)
    await wait_retry(h)
    assert not page.of("freeze") and "snapshotter" in h.screen().last_error


async def test_kiosk_state_prefetches_new_stills(harness: Harness):
    h = harness
    page = await h.page()
    await page.kiosk_state()
    await page.kiosk_state()                   # same still: once
    page.state = {**page.state, "base_url": "/output/devtest.jpg?v=9"}
    await page.kiosk_state()
    await h.wait(lambda: len(h.snap.prefetched) == 2, 2)
    assert [s["base_url"] for _, s in h.snap.prefetched] == ["/output/devtest.jpg?v=1", "/output/devtest.jpg?v=9"]
    assert h.snap.prefetched[0][1]["flags"] == {"nomask": False, "nocolour": False, "fit": "fit"}
    north = await h.page(screen="north-screen")          # live disabled: no point
    await north.kiosk_state()
    await asyncio.sleep(0.2)
    assert all(scr == "devtest" for scr, _ in h.snap.prefetched)


async def test_restart_mid_entry_releases_freeze_and_sends_go(make_harness):
    # adopt_wait_s 3: the page must reconnect and report within it; 1 s was tight on a busy host.
    h = await make_harness(timeouts={"adopt_wait_s": 3.0})
    page = await h.page()
    page.ack_live = False                      # parks in attaching (frozen, session holding)
    await put_live(h)
    await h.wait_phase("attaching")
    sid = h.screen().session["id"]
    fid = h.screen().frozen_fid
    await h.restart_room()                     # the room goes away mid-handshake (page stays)
    assert not h.stream.calls_of("DELETE"), "the restart itself aborts nothing"
    page2 = await h.page(page_load_id=page.page_load_id)
    # The stale freeze is released only after adoption has resolved (DCL v3 §4.7.6).
    await asyncio.sleep(0.2)
    assert not page2.of("unfreeze"), "released before adoption resolved"
    # The page had cut to the anchor just before the restart: it shows the holding session.
    page2.layer, page2.session = "live", sid
    await page2.kiosk_state()
    await h.wait(lambda: h.stream.calls_of("POST", f"/api/sessions/{sid}/go"), 3, "go sent after adoption")
    c = h.screen()
    await h.wait(lambda: c.phase in ("entering", "live") and c.session["id"] == sid, 3)
    await page2.wait_for("unfreeze", lambda d: d["fid"] == fid)


async def test_one_stream_death_is_one_fallback(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    page.ack_art = False                       # no abort ack: the fallback waits abort_ack_s
    h.stream.emit({"type": "session.state", "session": sid, "screen": "devtest", "state": "ended"})
    await asyncio.sleep(0.05)
    h.stream.emit({"type": "session.ended", "session": sid, "screen": "devtest", "reason": "error",
                   "detail": "service shutdown"})
    h.stream.emit({"type": "session.error", "session": sid, "screen": "devtest", "detail": "late"})
    await h.wait(lambda: c.session is not None and c.session["id"] != sid and c.layer == "live", 3)
    assert len(c.fallbacks) == 1


async def test_restart_same_page_adopts_without_waiting_for_kiosk_state(make_harness):
    h = await make_harness(timeouts={"adopt_wait_s": 5.0})
    page = await h.go_live()
    await h.wait_phase("live")
    sid = h.screen().session["id"]
    await h.restart_room()
    t0 = time.monotonic()
    page2 = await h.page(page_load_id=page.page_load_id)     # no kiosk.state sent
    c = h.screen()
    await h.wait(lambda: c.phase == "live" and "adopted" in c.detail, 2, "adopted from the hello")
    assert time.monotonic() - t0 < 1.0 and c.session["id"] == sid
    assert not h.stream.calls_of("DELETE")
    # kiosk.state still overrides: the page actually shows art -> a fallback and a fresh entry.
    await page2.kiosk_state(layer="art")
    await h.wait(lambda: c.session is not None and c.session["id"] != sid and c.layer == "live", 4)


# ---------------------------------------------------------------------------
# Review fixes
# ---------------------------------------------------------------------------

async def test_patch_in_flight_does_not_undo_a_switch_to_art(harness: Harness):
    """A live PATCH takes a round trip; a PUT art meanwhile must win (it used to be
    overwritten by the PATCH's result, sending the screen straight back live)."""
    h = harness
    await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    h.stream.patch_delay = 0.3
    patch = asyncio.get_running_loop().create_task(
        h.api("PATCH", "/api/v1/screens/devtest/live", {"params": {"zoom_rate": 1.5}}))
    await asyncio.sleep(0.05)
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    assert st == 202
    st, _ = await patch
    assert st == 200
    assert c.desired == "art"
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    await asyncio.sleep(0.4)
    assert c.desired == "art" and c.layer == "art" and len(creates(h)) == 1, "came back live"


async def test_orphan_sweep_deletes_stray_sessions_but_not_ours(harness: Harness):
    h = harness
    await h.go_live()
    c = h.screen()
    sid = c.session["id"]
    h.stream.sessions["s-stray"] = {"session": "s-stray", "screen": "devtest", "state": "holding",
                                    "profile": "h264-720p30", "created_at": 0, "scene": "x"}
    h.svc.screens.own("s-stray", "devtest")         # ours, but no controller uses it (failed DELETE)
    h.stream.sessions["s-theirs"] = {"session": "s-theirs", "screen": "devtest", "state": "holding",
                                     "profile": "h264-720p30", "created_at": 0, "scene": "x"}
    h.stream.sessions["s-other"] = {"session": "s-other", "screen": "not-ours", "state": "holding",
                                    "profile": "h264-720p30", "created_at": 0, "scene": "x"}
    h.svc.screens.request_sweep(0)
    await h.wait(lambda: "s-stray" not in h.stream.sessions, 3, "stray deleted")
    assert sid in h.stream.sessions and c.session["id"] == sid and c.layer == "live"
    assert {"s-theirs", "s-other"} <= set(h.stream.sessions), "only sessions we created are swept"


async def test_slow_create_times_out_counts_and_its_late_session_is_swept(make_harness):
    """POST /api/sessions is bounded (the page is frozen meanwhile). A create that completes
    server-side after we gave up is swept rather than left on the GPU until its TTL."""
    h = await make_harness(timeouts={"create_session_s": 0.2})
    page = await h.page()
    c = h.screen()
    h.stream.create_delay = 0.6
    await put_live(h)
    await page.wait_for("unfreeze", timeout=3)
    await h.wait(lambda: c._entry_failures == 1, 2, "a timeout with stream_service up counts")
    assert c.layer == "art" and c.session is None
    h.stream.create_delay = 0.0
    await h.wait(lambda: c.layer == "live" and c.phase in ("entering", "live"), 5, "retry went live")
    await h.wait(lambda: list(h.stream.sessions) == [c.session["id"]], 3, "only the live session left")


async def test_mode_sets_page_timed_reload_policy(make_harness, tmp_path):
    (tmp_path / "modes.yaml").write_text("""
default: gallery
modes:
  gallery:
    state:
      screens.devtest: art
      page.devtest.timed_reload: true
  live-test:
    state:
      screens.devtest: {live: {scene: fractal-zoom, transitions: {in: xfade, out: xfade}, profile: h264-720p30}}
      page.devtest.timed_reload: false
transitions:
  - {from: "*", to: "*", steps: [rest]}
""")
    h = await make_harness()
    page = await h.page()
    assert page.welcome["policy"]["timed_reload"] is True

    async def report_policy_when_sent() -> None:
        n = 0
        while True:
            if len(page.policy_seen) > n:
                n = len(page.policy_seen)
                await page.kiosk_state()          # the page reports the policy it applied
            await asyncio.sleep(0.02)

    reporter = asyncio.get_running_loop().create_task(report_policy_when_sent())
    try:
        st, body = await h.api("PUT", "/api/v1/mode", {"mode": "live-test"})
        assert st == 202
        await h.wait(lambda: h.svc.coordinator.s["mode"]["current"] == "live-test", 5, "live-test")
        tr = h.svc.coordinator.s["last_transition"]
        step = [s for s in tr["steps"] if s["subsystem"] == "page.devtest.timed_reload"][0]
        assert step["status"] == "ok", step
        assert page.policy_seen[-1] == {"live_enabled": True, "mask_push": True, "timed_reload": False}
        st, s = await h.api("GET", "/api/v1/status")
        assert s["screens"]["devtest"]["policy"]["timed_reload"] is False
        assert s["screens"]["devtest"]["policy"]["timed_reload_applied"] is False
        assert s["subsystems"]["page"]["members"]["devtest.timed_reload"]["believed"] is False
        late = await h.page(role="viewer")
        assert late.welcome["policy"]["timed_reload"] is False      # welcome carries the value
        # Survives a restart.
        reporter.cancel()
        await h.restart_room()
        again = await h.page()
        assert again.welcome["policy"]["timed_reload"] is False
    finally:
        reporter.cancel()


async def test_a_slow_page_report_holds_no_screens_entry(make_harness, tmp_path):
    """20 Oct: the policy went to north, south and devtest one after another, each waiting for the page's next
    kiosk.state (every 5 s), and only then did the screens start: 9 s lost. Now every page is told at once, the screens
    step starts at once (each page gets its policy before its own freeze), and the reports are proved behind it."""
    live = {"live": {"scene": "fractal-zoom", "transitions": {"in": "xfade", "out": "xfade"}, "profile": "h264-720p30"}}
    (tmp_path / "modes.yaml").write_text(f"""
default: gallery
modes:
  gallery:
    state:
      screens.devtest: art
      screens.north-screen: art
      page.devtest.timed_reload: true
      page.north-screen.timed_reload: true
  live-test:
    state:
      screens.devtest: {json.dumps(live)}
      screens.north-screen: {json.dumps(live)}
      page.devtest.timed_reload: false
      page.north-screen.timed_reload: false
transitions:
  - {{from: "*", to: "*", steps: [page, screens, rest]}}
""")
    on = {"gpu": 0, "live_enabled": True, "mask_push": True}
    h = await make_harness(timeouts={"policy_ack_s": 5.0},
                           screens={"devtest": on, "north-screen": on,
                                    "south-screen": {"gpu": 1, "live_enabled": False, "mask_push": False}})
    dev, north = await h.page(), await h.page("north-screen")
    await h.wait(lambda: h.svc.coordinator.s["mode"]["current"] == "gallery", 3, "gallery")
    for p in (dev, north):
        await p.kiosk_state()                    # they report a policy: the adapter waits for the next report
    await h.wait(lambda: all(h.screen(s).kiosk_state for s in ("devtest", "north-screen")), 2, "reports in")
    north_reported = asyncio.Event()

    async def report(page, delay, done=None) -> None:
        await page.wait_for("policy", lambda d: d.get("timed_reload") is False, timeout=5)
        await asyncio.sleep(delay)
        await page.kiosk_state()
        if done:
            done.set()
    loop = asyncio.get_running_loop()
    reporters = [loop.create_task(report(dev, 0.0)), loop.create_task(report(north, 2.5, north_reported))]
    try:
        st, _ = await h.api("PUT", "/api/v1/mode", {"mode": "live-test"})
        assert st == 202
        await dev.wait_for("freeze", timeout=2.0)
        await north.wait_for("freeze", timeout=2.0)
        assert not north_reported.is_set()       # both entries started while north's report was still due
        for p in (dev, north):                   # each page had its policy before its own freeze
            types = p.types()
            assert types.index("policy") < types.index("freeze"), types
        await h.wait(lambda: h.svc.coordinator.s["mode"]["current"] == "live-test", 8, "live-test")
        tr = h.svc.coordinator.s["last_transition"]
        assert north_reported.is_set() and tr["status"] == "ok", tr
        assert {r["status"] for r in tr["steps"] if r["subsystem"].startswith("page.")} == {"ok"}
    finally:
        for t in reporters:
            t.cancel()


async def test_warm_hint_at_entry_start(harness: Harness):
    """POST /api/workers/warm goes as the entry starts, not awaited: the screen's render worker cold-starts during the
    freeze and the snapshot, not after the session create."""
    h = harness
    page = await h.page()
    page.frozen_delay = 0.3
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": LIVE})
    assert st == 202
    await page.wait_for("freeze", timeout=2)
    await h.wait(lambda: h.stream.warms, 0.25, "warm hint")
    assert h.stream.warms[0][1] == {"screen": "devtest", "gpu": 0}
    assert not h.stream.calls_of("POST", "/api/sessions")      # still frozen: no session yet
    await h.wait(lambda: h.screen().layer == "live", 3, "live")


async def test_a_failed_warm_hint_never_holds_the_entry(harness: Harness):
    h = harness
    h.stream.warm_status = 500
    await h.go_live()
    assert h.screen().phase in ("entering", "live") and h.stream.warms


async def test_unfreeze_lost_with_the_socket_is_sent_on_reconnect(harness: Harness):
    """The page's socket drops mid-entry and the entry aborts: its unfreeze had nowhere to
    go. The page (still frozen) reconnects and gets it right after its welcome, rather
    than sitting frozen until its own 30 s safety timeout."""
    h = harness
    page = await h.page()
    page.ack_live = False
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await put_live(h)
    await h.wait_phase("attaching")
    fid, sid = h.screen().frozen_fid, h.screen().session["id"]
    await page.close()
    await h.wait(lambda: sid not in h.stream.sessions and h.screen().frozen_fid is None, 2, "entry aborted")
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})   # don't go on live without the page
    await h.wait(lambda: h.screen().session is None and h.screen()._handshake is None, 2, "back to art")
    page2 = await h.page(page_load_id=page.page_load_id)
    unf = await page2.wait_for("unfreeze", lambda d: d["fid"] == fid, timeout=2)
    assert unf["fid"] == fid
    types = page2.types()
    assert types.index("welcome") < types.index("unfreeze"), types


async def test_identical_art_state_reuses_the_snapshot(harness: Harness):
    """The snapshot is a pure function of the ArtState: an exit whose acked state is exactly
    the entry's (held mask, same still) reuses the anchor PNG instead of a 1-3 s 4K capture."""
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    assert len(h.snap.calls) == 1
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert len(h.snap.calls) == 1, "identical state: no second capture"
    exit_call = [x for x in h.stream.calls if x[1].endswith("/exit")][0][2]
    post = h.stream.calls_of("POST", "/api/sessions")[0][2]
    assert exit_call["target"]["png_b64"] == post["anchor"]["png_b64"]
    assert "live.leave" in page.types() and "live.abort" not in page.types()
    # A different mask is a different state: captured again.
    page.state = {**page.state, "mask": {**page.state["mask"], "brightness": 0.42}}
    await h.go_live(page)
    assert len(h.snap.calls) == 2


async def test_exit_snapshot_failure_is_retried_before_a_hard_cut(harness: Harness):
    h = harness
    page = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    page.state = {**page.state, "base_url": "/output/devtest.jpg?v=9"}     # needs a fresh capture
    h.snap.fail_times = 1
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert "live.leave" in page.types() and "live.abort" not in page.types(), "matched cut after one retry"
    # Failing twice: the hard cut is the last resort.
    page.state = {**page.state, "base_url": "/output/devtest.jpg?v=10"}
    await h.go_live(page)
    await h.wait_phase("live")
    page.state = {**page.state, "base_url": "/output/devtest.jpg?v=11"}
    h.snap.fail_times = 2
    n = len(page.received)
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert [t for t, _ in page.received[n:] if t in ("live.leave", "live.abort")] == ["live.abort"]


async def test_a_new_mode_does_not_carry_the_old_direction(harness: Harness):
    """2b's audit (17 Oct): a re-created session within CARRY_S carries palette and FX, but not a different mode's
    direction (the same live value, a reload or fallback, still carries it)."""
    c = harness.screen()
    old_live = {"auto": True, "direction": {"energy": 0.9, "moods": ["euphoric"]}}
    c._carry = {"at": c._now(), "look": {"direction": {"energy": 0.9, "moods": ["euphoric"]}, "palette": "ember"},
                "fx": [], "live": old_live}
    out, _, _ = await c._session_look({"auto": True})
    assert "direction" not in out and out["palette"] == "ember"
    out, _, _ = await c._session_look(old_live)
    assert out["direction"] == {"energy": 0.9, "moods": ["euphoric"]}


async def test_live_diagnostics_hud(harness: Harness):
    """docs/LIVE_DIAGNOSTICS.md: diag.<screen> -> set_hud: on the next create while in art, PATCHed while live."""
    h = harness
    c = h.screen()
    c.set_hud(True)
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["hud"] == {"wanted": True, "applied": False, "failed": None}
    await h.go_live()
    create = [b for m, p, b in h.stream.calls if m == "POST" and p == "/api/sessions"][-1]
    assert create.get("hud") is True
    sid = c.session["id"]
    c.set_hud(False)
    await h.wait(lambda: any(m == "PATCH" and p == f"/api/sessions/{sid}" and (b or {}).get("hud") is False
                             for m, p, b in h.stream.calls), 3, "hud off PATCHed")
    st, body = await h.api("GET", "/api/v1/status")
    assert body["screens"]["devtest"]["hud"]["wanted"] is False
