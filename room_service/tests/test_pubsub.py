"""Display pages are pub/sub (owner, 15 Oct: "it should be pub/sub, no limit to number of subs").

The driven glass page (the TV) runs the handshakes. Every other page of the screen (standby glass, ?kiosk=viewer)
is a subscriber: it is shown what the screen shows (the running live session, else art), and nothing it sends
drives anything."""

from __future__ import annotations

import asyncio

from .conftest import LIVE, Harness
from .fakes import FakePage


def creates(h: Harness) -> list:
    return [c for c in h.stream.calls if c[0] == "POST" and c[1] == "/api/sessions"]


def control(page: FakePage) -> list[str]:
    return [t for t in page.types() if t in ("freeze", "unfreeze")]


async def test_second_glass_page_on_a_live_screen_gets_attach(harness: Harness):
    h = harness
    tv = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    second = await h.page()                          # another page load of /display/devtest
    assert second.welcome["live"]["session"] == sid and second.welcome["live"]["attach"]["url"]
    assert second.welcome_attached == sid
    assert c._driven.page_load_id == tv.page_load_id, "the TV stays driven"
    # It is a subscriber: no handshakes, and its acks change nothing.
    await second.stall()
    await second.kiosk_state(layer="art")
    await asyncio.sleep(0.3)
    assert c.layer == "live" and c.session["id"] == sid and not c.fallbacks
    assert not control(second) and len(creates(h)) == 1


async def test_viewer_before_the_show_gets_attach_at_go_and_the_cut_back_on_exit(harness: Harness):
    h = harness
    viewer = await h.page(role="viewer")
    assert viewer.welcome["live"] is None
    tv = await h.page()
    tv.live_delay = 0.2                              # hold the entry in "attaching" for a moment
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": LIVE})
    assert st == 202
    c = h.screen()
    await h.wait_phase("attaching")
    sid = c.session["id"]
    assert not viewer.of("live.attach"), "subscribers are attached only after go"
    attach = await viewer.wait_for("live.attach")
    assert h.stream.calls_of("POST", f"/api/sessions/{sid}/go")
    assert attach["session"] == sid and attach["url"] == tv.of("live.attach")[0]["url"]
    await h.wait_phase("live")
    # Exit: the TV's handshake is unchanged; the viewer follows its exit cut (live.leave) and then is cut back.
    n = len(tv.received)
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert [t for t in tv.types()[n:] if t in ("freeze", "live.leave", "unfreeze", "live.abort")] == [
        "freeze", "live.leave", "unfreeze"]
    leave = await viewer.wait_for("live.leave")
    assert leave == {"session": sid, "hold_pts": 12.5}
    abort = await viewer.wait_for("live.abort")
    assert abort["session"] == sid
    assert [t for t in viewer.types() if t.startswith("live.")] == ["live.attach", "live.leave", "live.abort"]
    assert not control(viewer) and viewer.layer == "art"


async def test_fallback_replacement_reattaches_subscribers(harness: Harness):
    h = harness
    tv = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid1 = c.session["id"]
    viewer = await h.page(role="viewer")
    standby = await h.page()
    assert viewer.welcome_attached == sid1 and standby.welcome_attached == sid1
    await tv.stall()                                 # the TV cuts to art; the screen re-enters on a new session
    await h.wait(lambda: c.session is not None and c.session["id"] != sid1 and c.layer == "live"
                 and c.phase in ("entering", "live"), 3, "re-entered")
    sid2 = c.session["id"]
    for p in (viewer, standby):
        await p.wait_for("live.abort", lambda d: d["session"] == sid1)
        await p.wait_for("live.attach", lambda d: d["session"] == sid2)
        assert [(t, d["session"]) for t, d in p.received if t in ("live.attach", "live.abort")] == [
            ("live.abort", sid1), ("live.attach", sid2)]
        assert not control(p)
    assert tv.of("freeze")[-1]["reason"] == "reanchor"


async def test_stream_failure_cuts_subscribers_back(harness: Harness):
    h = harness
    await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    viewer = await h.page(role="viewer")
    h.stream.fail_session(sid)
    abort = await viewer.wait_for("live.abort", lambda d: d["session"] == sid)
    assert abort["reason"]


async def test_forty_pages_are_accepted_and_all_get_the_show(harness: Harness):
    h = harness
    viewers = [await h.page(role="viewer") for _ in range(36)]
    tv = await h.page()
    assert all(p.welcome is not None and not p.ws.closed for p in viewers)
    await h.go_live(tv)
    sid = h.screen().session["id"]
    for p in viewers:
        await p.wait_for("live.attach", lambda d: d["session"] == sid)
        assert not control(p)
    glass = [await h.page() for _ in range(4)]       # more /display/devtest loads while live: standby subscribers
    assert all(p.welcome_attached == sid for p in glass)
    st, body = await h.api("GET", "/api/v1/status")
    obs = body["screens"]["devtest"]["observed"]
    assert obs["viewers"] == 36 and obs["glass_clients"] == 5 and obs["page"]["page_load_id"] == tv.page_load_id
    late = await h.page(role="viewer")               # the 42nd page
    assert late.welcome["live"]["session"] == sid


async def test_promoted_standby_already_playing_is_joined_not_reentered(make_harness):
    h = await make_harness(timeouts={"adopt_wait_s": 0.5})
    tv = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    standby = await h.page()
    assert standby.welcome_attached == sid           # it plays the show as a subscriber
    standby.ack_live = False                         # a real page's attach is idempotent: no second live_visible
    n_creates = len(creates(h))
    await tv.close()
    # The TV doesn't come back: the session runs on and the standby page is promoted to drive.
    await h.wait(lambda: c._driven is not None and c._driven.page_load_id == standby.page_load_id, 4, "promoted")
    await standby.kiosk_state(layer="live", session=sid)
    await h.wait(lambda: not c._headless(), 2, "joined")
    assert c.session["id"] == sid and c.layer == "live" and len(creates(h)) == n_creates
    assert not standby.of("freeze") and not h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    assert all(d["session"] == sid for d in standby.of("live.attach"))
    # It now drives: the exit runs its handshake on it.
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")
    assert [d for d in standby.of("freeze") if d["reason"] == "exit"] and standby.of("live.leave")


async def test_subscriber_joining_mid_exit_stays_in_art(harness: Harness):
    h = harness
    h.stream.held_delay = 0.3
    await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    await h.wait_phase("exiting")
    viewer = await h.page(role="viewer")
    assert viewer.welcome["live"] is None and viewer.welcome_attached is None
    await h.wait(lambda: c.session is None and c.phase == "idle", 3, "exit done")


# -- a subscriber that cut to art by itself gets the show back --------------------------------------------------------

PHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
TV_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def attaches(page: FakePage, sid: str) -> list[dict]:
    return [d for d in page.of("live.attach") if d["session"] == sid]


async def test_subscriber_that_stalls_is_offered_the_session_again(harness: Harness, monkeypatch):
    from room_service.screens import ScreenController
    monkeypatch.setattr(ScreenController, "REJOIN_BACKOFF_S", (0.1, 0.2))
    h = harness
    tv = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    viewer = await h.page(role="viewer")
    standby = await h.page()
    assert viewer.welcome["live"]["attach"]["rejoin"] is True and standby.welcome["live"]["attach"]["rejoin"] is True
    for p in (viewer, standby):
        await p.stall()                              # e.g. a phone's tab hidden for > 2 s
        again = await p.wait_for("live.attach", lambda d: d["session"] == sid and d.get("rejoin"), timeout=2)
        assert again["url"] == c.session["attach"]["url"]
    await asyncio.sleep(0.3)
    assert c.layer == "live" and c.session["id"] == sid and not c.fallbacks and len(creates(h)) == 1
    assert not control(viewer) and not control(standby)
    assert not [d for d in tv.of("live.attach") if d.get("rejoin")], "the driven TV never gets rejoin"
    # Each live_visible resets the backoff: a second stall is offered again just as fast.
    await viewer.stall()
    await viewer.wait_for("live.attach", lambda d: d["session"] == sid, timeout=2, after=len(viewer.received))


async def test_a_subscriber_that_cannot_play_is_not_asked_again(harness: Harness, monkeypatch):
    """A page that can't play it at all isn't asked again; one that keeps failing is, on the backoff, without a
    limit (S14)."""
    from room_service.screens import ScreenController
    monkeypatch.setattr(ScreenController, "REJOIN_BACKOFF_S", (0.05,))
    h = harness
    await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    no_codec = await h.page(role="viewer")
    await no_codec.ack(kind="live_failed", session=sid, reason="unsupported-codec")
    slow = await h.page(role="viewer")
    slow.live_failed = True                          # every attach fails (a warm-up timeout): offered again and again
    await slow.ack(kind="live_failed", session=sid, reason="warmup-timeout")
    await asyncio.sleep(1.0)
    assert len(attaches(no_codec, sid)) == 0
    assert len(attaches(slow, sid)) > 10, slow.of("live.attach")
    assert c.layer == "live" and c.session["id"] == sid


async def test_a_phone_page_in_art_does_not_take_the_glass_from_the_tv(harness: Harness):
    h = harness
    # The art on screen is 16:9 (live takes the art's shape, never a window's).
    (h.tmp / "output").mkdir(exist_ok=True)
    (h.tmp / "output" / "devtest.jpg").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (3840).to_bytes(4, "big") + (2160).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    tv = await h.page(user_agent=TV_UA)
    phone = FakePage(h.url, "devtest", "kiosk", user_agent=PHONE_UA)  # a plain /display/devtest on a phone
    phone.state["viewport"] = {"w": 390, "h": 844}
    await phone.connect()
    h.pages.append(phone)
    await phone.wait_for("welcome")
    c = h.screen()
    assert c._driven.page_load_id == tv.page_load_id and phone.welcome["live"] is None
    await h.go_live(tv)
    sid = c.session["id"]
    assert tv.of("freeze") and not phone.of("freeze")
    body = [b for m, p, b in h.stream.calls if m == "POST" and p == "/api/sessions"][-1]
    assert body["width"] > body["height"], "shaped to the TV, not the phone"
    await phone.wait_for("live.attach", lambda d: d["session"] == sid and d.get("rejoin"))
    # The TV reloads (a new page load from the same device, its old socket still open): it takes over.
    await exit_and_wait(h)
    tv2 = await h.page(user_agent=TV_UA)
    assert c._driven is not None and c._driven.page_load_id == tv2.page_load_id


async def test_the_tv_takes_the_glass_back_from_a_phone(make_harness):
    h = await make_harness(timeouts={"adopt_wait_s": 0.5})
    tv = await h.page(user_agent=TV_UA)
    await h.go_live(tv)
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    await tv.close()                                 # the TV drops mid-show
    phone = await h.page(user_agent=PHONE_UA)
    assert c._driven is None, "while live, another device waits on standby for the TV"
    assert phone.welcome["live"]["session"] == sid  # ...and watches the show
    # The TV does not come back in time: the show runs on and the phone is promoted (it already plays it).
    await h.wait(lambda: c._driven is not None and c._driven.page_load_id == phone.page_load_id, 4, "phone promoted")
    await phone.kiosk_state(layer="live", session=sid)
    await h.wait(lambda: not c._headless(), 2, "joined")
    # The TV comes back (a fresh load): it watches as a subscriber, and once it plays the show it takes the glass back.
    tv2 = await h.page(user_agent=TV_UA)
    assert c._driven.page_load_id == phone.page_load_id and tv2.welcome_attached == sid
    await tv2.kiosk_state(layer="live", session=sid)
    await h.wait(lambda: c._driven is not None and c._driven.page_load_id == tv2.page_load_id, 2, "tv reclaims")
    assert c.session["id"] == sid and not c._headless() and len(creates(h)) == 1 and not tv2.of("freeze")
    await exit_and_wait(h)
    assert [d for d in tv2.of("freeze") if d["reason"] == "exit"] and not phone.of("freeze")


async def test_restart_waits_for_the_tv_not_the_first_phone(harness: Harness):
    h = harness
    tv = await h.page(user_agent=TV_UA)
    await h.go_live(tv)
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    await tv.close()
    await h.restart_room()
    c = h.screen()
    phone = await h.page(user_agent=PHONE_UA)        # the phone reconnects first
    assert c._driven is None and phone.welcome["live"] is None
    tv2 = await h.page(user_agent=TV_UA, page_load_id=tv.page_load_id)   # the TV page reconnects, still playing
    tv2.layer, tv2.session = "live", sid
    await tv2.kiosk_state()
    await h.wait(lambda: c.phase == "live" and c.session and c.session["id"] == sid and c._handshake is None, 3)
    assert c._driven.page_load_id == tv.page_load_id
    assert not h.stream.calls_of("DELETE", f"/api/sessions/{sid}")
    # The phone, which connected mid-adoption, is attached once the session is adopted.
    await phone.wait_for("live.attach", lambda d: d["session"] == sid and d.get("rejoin"))


async def test_first_restart_after_the_upgrade_waits_for_the_last_tv_page(harness: Harness):
    """A state file from before glass_device existed: the page load that drove before the restart is the TV."""
    import json
    h = harness
    tv = await h.page(user_agent=TV_UA)
    await h.go_live(tv)
    await h.wait_phase("live")
    sid = h.screen().session["id"]
    await tv.close()
    await h.stop_room()
    f = h.state_dir / "screens.json"
    state = json.loads(f.read_text())
    state["screens"]["devtest"].pop("glass_device")
    f.write_text(json.dumps(state))
    await h.start_room()
    c = h.screen()
    assert c.glass_device is None
    phone = await h.page(user_agent=PHONE_UA)
    assert c._driven is None
    tv2 = await h.page(user_agent=TV_UA, page_load_id=tv.page_load_id)
    tv2.layer, tv2.session = "live", sid
    await tv2.kiosk_state()
    await h.wait(lambda: c.phase == "live" and c.session and c.session["id"] == sid and c._handshake is None, 3)
    assert c._driven.page_load_id == tv.page_load_id and c.glass_device == tv2_device(h, tv2)
    await phone.wait_for("live.attach", lambda d: d["session"] == sid)


def tv2_device(h: Harness, page: FakePage) -> str:
    return next(cl.device for cl in h.svc.pages.hello_clients("devtest") if cl.page_load_id == page.page_load_id)


async def test_restart_dropping_the_session_tells_subscribers(harness: Harness):
    h = harness
    tv = await h.go_live()
    c = h.screen()
    await h.wait_phase("live")
    sid = c.session["id"]
    viewer = await h.page(role="viewer")
    assert viewer.welcome_attached == sid
    await tv.close()
    await viewer.close()
    await h.restart_room()
    v2 = FakePage(h.url, "devtest", "viewer", page_load_id=viewer.page_load_id)
    v2.session, v2.layer = sid, "live"               # its LiveController kept playing through the socket drop
    await v2.connect()
    h.pages.append(v2)
    await v2.wait_for("welcome")
    assert v2.welcome["live"] is None                # mid-adoption: nothing yet (a welcome without live never stops it)
    tv2 = await h.page()                             # the TV page reloaded meanwhile: art
    await tv2.kiosk_state(layer="art")
    await v2.wait_for("live.abort", lambda d: d["session"] == sid)
    assert v2.layer == "art"
    await h.wait(lambda: h.stream.calls_of("DELETE", f"/api/sessions/{sid}"), 3, "dropped")


async def exit_and_wait(h: Harness) -> None:
    st, _ = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "art"})
    assert st == 202
    c = h.screen()
    await h.wait(lambda: c.session is None and c.phase == "idle" and c._handshake is None, 3, "exit done")
