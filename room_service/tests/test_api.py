"""HTTP API, CORS, auth, events WS and token handling (LIVE_V1 §3)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import time

from room_service.util import REDACT, RedactFilter, load_or_create_token

from .conftest import ROOM_TOKEN, Harness
from .fakes import CATALOG, PNG_SIG, FakePage

ORIGIN = "http://localhost:5174"          # one of the default web_origins (config.py)


async def test_healthz_needs_no_auth(harness: Harness):
    st, body = await harness.api("GET", "/healthz", token=None)
    assert st == 200 and body["ok"] is True and body["version"]


async def test_operate_is_open_and_tokens_are_ignored(harness: Harness):
    """Owner ruling, 14 Oct: no auth anywhere in the AV stack. No token is needed, and a token a client
    still sends (right, wrong or not a bearer) is ignored."""
    h = harness
    cases = [("GET", "/api/v1/status", None, 200), ("GET", "/api/v1/modes", None, 200),
             ("PUT", "/api/v1/mode", {"mode": "gallery"}, 202),
             ("PUT", "/api/v1/screens/devtest", {"desired": "art"}, 202),
             ("GET", "/api/v1/catalog", None, 200),
             ("GET", "/api/v1/screens/devtest/snapshots/anchor.png", None, 404),   # none yet
             ("PATCH", "/api/v1/screens/devtest/live", {"params": {"zoom_rate": 1}}, 409)]
    for method, path, body, want in cases:
        st, _ = await h.api(method, path, body, token=None)
        assert st == want, (method, path, st)
        st, _ = await h.api(method, path, body, token="x" * 64)     # a wrong token is ignored
        assert st == want, (method, path, st)
        st, _ = await h.api(method, path, body)                      # so is the old right one
        assert st == want, (method, path, st)
    async with h.http.get(h.url + "/api/v1/status", headers={"Authorization": "Basic Zm9vOmJhcg=="}) as r:
        assert r.status == 200
    st, _ = await h.api("GET", "/api/v1/status?token=wrong", token=None)
    assert st == 200
    for url in ("/api/v1/events", "/api/v1/events?token=wrong"):    # open, any token ignored
        ws = await h.http.ws_connect(h.url + url)
        first = await ws.receive_json(timeout=2)
        assert first["type"] == "status"
        await ws.close()
    async with h.http.get(h.url + "/api/v1/screens/devtest/snapshots/anchor.png?token=wrong") as r:
        assert r.status == 404                                       # none yet; the token doesn't matter


async def test_host_and_origin_checks(harness: Harness):
    """LIVE_V1 §3 browser protection: Host must be ours (DNS rebinding); an Origin on /api/*
    must be an allowed web origin or our own. No Origin (curl, LAN scripts) is fine."""
    h = harness
    for path in ("/api/v1/status", "/healthz", "/v1/screen/devtest?role=kiosk"):
        async with h.http.get(h.url + path, headers={"Host": "evil.example"}) as r:
            assert r.status == 403 and (await r.json())["error"] == "host_not_allowed", path
    async with h.http.get(h.url + "/api/v1/status", headers={"Host": "localhost:8700"}) as r:
        assert r.status == 200                                     # an allowed name, any port
    for method, path, body in [("GET", "/api/v1/status", None), ("PUT", "/api/v1/mode", {"mode": "gallery"}),
                               ("GET", "/api/v1/events", None)]:
        st, res = await h.api(method, path, body, token=None, headers={"Origin": "http://evil.example"})
        assert st == 403 and res["error"] == "origin_not_allowed", path
    st, _ = await h.api("PUT", "/api/v1/mode", {"mode": "gallery"}, token=None, headers={"Origin": ORIGIN})
    assert st == 202
    own = h.url                                                     # http://127.0.0.1:<port>
    st, _ = await h.api("GET", "/api/v1/status", token=None, headers={"Origin": own})
    assert st == 200
    # The kiosk control socket checks Origin too (DCL v3 §7): a page from anywhere else is
    # refused; the kiosk page's own origin (the TVs load it from the WAN address) must be listed
    # in web_origins or kiosk_origins. No Origin (not a browser) is fine.
    async with h.http.get(h.url + "/v1/screen/devtest?role=kiosk",
                          headers={"Origin": "https://wan.example", "Connection": "Upgrade",
                                   "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
                                   "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}) as r:
        assert r.status == 403
    page = FakePage(h.url, "devtest", "kiosk")
    page._origin = ORIGIN                             # a configured web origin
    await page.connect()
    h.pages.append(page)
    await page.wait_for("welcome")


async def test_cors_preflight_and_headers(harness: Harness):
    h = harness
    async with h.http.options(h.url + "/api/v1/screens/devtest", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "authorization, content-type"}) as r:
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Origin"] == ORIGIN
        assert "PUT" in r.headers["Access-Control-Allow-Methods"]
        assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
        assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]
    async with h.http.options(h.url + "/api/v1/status", headers={
            "Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}) as r:
        assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
    st, _ = await h.api("GET", "/api/v1/status", headers={"Origin": ORIGIN})
    async with h.http.get(h.url + "/api/v1/status", headers={"Origin": ORIGIN,
                                                            "Authorization": f"Bearer {ROOM_TOKEN}"}) as r:
        assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == ORIGIN
    async with h.http.get(h.url + "/api/v1/status", headers={"Origin": ORIGIN, "Authorization": "Bearer nope"}) as r:
        assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == ORIGIN   # tokens are ignored
    async with h.http.get(h.url + "/api/v1/screens/nope/snapshots/anchor.png",
                          headers={"Origin": ORIGIN, "Authorization": f"Bearer {ROOM_TOKEN}"}) as r:
        assert r.status == 404 and r.headers["Access-Control-Allow-Origin"] == ORIGIN, \
            "errors carry CORS headers too, so the browser can read them"


async def test_status_shape(harness: Harness):
    h = harness
    await h.page()
    st, s = await h.api("GET", "/api/v1/status")
    assert st == 200
    assert set(s) >= {"mode", "screens", "stream_service", "subsystems"}
    assert s["mode"]["current"] == "gallery" and s["mode"]["target"] is None and s["mode"]["transition"] is None
    assert set(s["screens"]) == {"devtest", "north-screen", "south-screen"}
    d = s["screens"]["devtest"]
    assert d["desired"] == "art" and d["last_error"] is None
    assert set(d["observed"]) >= {"layer", "phase", "session", "page_connected", "page_role", "kiosk_state",
                                  "last_ack_at"}
    assert d["observed"]["layer"] == "art" and d["observed"]["phase"] == "idle"
    assert d["observed"]["page_connected"] is True and d["observed"]["page_role"] == "kiosk"
    assert set(d["mask"]) >= {"brightness", "warm_hex", "warm_alpha", "suspended"}   # + the sensor-ingest fields
    assert s["stream_service"] == {"reachable": True, "url": h.stream.url, "last_error": None,
                                   "owned_sessions": [], "foreign_sessions": []}
    # The room state model (ROOM_STATE_MODEL §7.1): subsystems with members; placeholders unavailable.
    assert s["subsystems"]["mirror_ball"]["available"] is False and s["subsystems"]["mirror_ball"]["believed"] is None
    assert s["subsystems"]["screens"]["kind"] == "level"
    assert s["subsystems"]["screens"]["members"]["devtest"]["believed"] == "art"
    assert {"stored", "config"} <= set(s) and s["mode"]["last_transition"]["to"] == "gallery"


async def test_modes_endpoint(harness: Harness):
    st, body = await harness.api("GET", "/api/v1/modes")
    assert st == 200 and set(body["modes"]) == {"gallery", "live-test", "show"}
    m = body["modes"]["live-test"]
    assert set(m) == {"label", "description", "icon", "voice", "state", "targets", "on_enter", "on_exit"}
    assert m["voice"] == ""                                               # no Alexa name configured
    assert m["state"]["screens"]["devtest"]["live"]["profile"] == "h264-720p30"
    assert m["state"]["screens"]["north-screen"] == "art"                 # inherited from gallery
    assert body["modes"]["show"]["state"]["sensor_mask"] == "suspended"   # extends resolved
    assert body["modes"]["show"]["on_enter"] == ["log.info"]


async def test_bad_requests(harness: Harness):
    h = harness
    st, body = await h.api("PUT", "/api/v1/mode", {"mode": "nope"})
    assert st == 404 and body["error"] == "unknown_mode"
    st, body = await h.api("PUT", "/api/v1/mode", {"nomode": 1})
    assert st == 400
    st, body = await h.api("PUT", "/api/v1/screens/nope", {"desired": "art"})
    assert st == 404 and body["error"] == "unknown_screen"
    st, body = await h.api("PUT", "/api/v1/screens/devtest", {"desired": "sideways"})
    assert st == 400 and body["error"] == "bad_desired"
    st, body = await h.api("PUT", "/api/v1/screens/devtest", {"desired": {"live": {}}})
    assert st == 400
    async with h.http.put(h.url + "/api/v1/mode", data=b"not json",
                          headers={"Authorization": f"Bearer {ROOM_TOKEN}"}) as r:
        assert r.status == 400 and (await r.json())["error"] == "bad_json"


async def test_desired_forms_are_normalised(harness: Harness):
    h = harness
    st, body = await h.api("PUT", "/api/v1/screens/devtest",
                           {"desired": {"live": "fractal-zoom", "in": "xfade", "out": "xfade"}})
    assert st == 202
    assert body["desired"] == {"live": {"scene": "fractal-zoom", "transitions": {"in": "xfade", "out": "xfade"}}}
    st, body = await h.api("PUT", "/api/v1/screens/devtest",
                           {"desired": {"live": {"scene": {"source": "fractal_zoom", "params": {"zoom_rate": 1}},
                                                 "params": {"zoom_rate": 2}}}})
    assert st == 202 and body["desired"]["live"]["params"] == {"zoom_rate": 2}


async def test_catalog_passthrough(harness: Harness):
    st, body = await harness.api("GET", "/api/v1/catalog")
    assert st == 200 and body == CATALOG


async def test_catalog_when_stream_down(harness: Harness):
    h = harness
    await h.stream.stop()
    h.svc.stream._catalog = None
    st, body = await h.api("GET", "/api/v1/catalog")
    assert st == 502 and body["error"] == "stream_unreachable"


async def test_snapshots(harness: Harness):
    h = harness
    st, body = await h.api("GET", "/api/v1/screens/devtest/snapshots/anchor.png")
    assert st == 404 and body["error"] == "no_snapshot"
    await h.go_live()
    st, png = await h.api("GET", "/api/v1/screens/devtest/snapshots/anchor.png")
    assert st == 200 and png.startswith(PNG_SIG)
    async with h.http.get(h.url + f"/api/v1/screens/devtest/snapshots/anchor.png?token={ROOM_TOKEN}") as r:
        assert r.status == 200 and r.content_type == "image/png"     # for <img src>
    st, _ = await h.api("GET", "/api/v1/screens/devtest/snapshots/target.png")
    assert st == 404
    st, _ = await h.api("GET", "/api/v1/screens/devtest/snapshots/other.png")
    assert st == 404
    # Snapshots survive a restart (kept in the state dir).
    await h.restart_room()
    st, png2 = await h.api("GET", "/api/v1/screens/devtest/snapshots/anchor.png")
    assert st == 200 and png2 == png


async def test_events_ws(harness: Harness):
    h = harness
    ws = await h.http.ws_connect(h.url + f"/api/v1/events?token={ROOM_TOKEN}")
    first = await ws.receive_json(timeout=2)
    assert first["type"] == "status" and first["data"]["mode"]["current"] == "gallery"
    page = await h.page()
    await h.go_live(page)
    seen: dict[str, list] = {}
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            m = await ws.receive_json(timeout=0.2)
        except asyncio.TimeoutError:
            continue
        seen.setdefault(m["type"], []).append(m["data"])
    await ws.close()
    phases = [d["phase"] for d in seen.get("screen.phase", [])]
    assert phases[:4] == ["freezing", "snapshot", "starting", "attaching"], phases
    assert {d["type"] for d in seen.get("stream", [])} >= {"session.ready", "session.state"}
    assert seen.get("screen.mask") and seen["screen.mask"][0]["screen"] == "devtest"
    assert seen.get("status") and seen.get("log")


async def test_status_events_rate_limited(harness: Harness):
    h = harness
    ws = await h.http.ws_connect(h.url + f"/api/v1/events?token={ROOM_TOKEN}")
    await ws.receive_json(timeout=2)            # the connect-time status
    t0 = time.monotonic()
    n = 0

    async def churn() -> None:
        while time.monotonic() - t0 < 1.0:
            h.svc.events.mark_dirty()
            await asyncio.sleep(0.005)

    task = asyncio.get_running_loop().create_task(churn())
    while time.monotonic() - t0 < 1.0:
        try:
            m = await ws.receive_json(timeout=0.1)
        except asyncio.TimeoutError:
            continue
        if m["type"] == "status":
            n += 1
    await task
    await ws.close()
    assert 2 <= n <= 5, f"{n} status pushes in 1 s (limit 4/s)"


def test_token_file_created_0600_and_reused(tmp_path):
    p = tmp_path / "sub" / "room_service.token"
    tok = load_or_create_token(p)
    assert len(tok) == 64 and int(tok, 16) >= 0
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert load_or_create_token(p) == tok
    os.chmod(p, 0o644)
    assert load_or_create_token(p) == tok and stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_tokens_are_redacted_from_logs(caplog):
    secret = "ab" * 32
    REDACT.add(secret)
    logger = logging.getLogger("room_service.test")
    logger.addFilter(RedactFilter())
    with caplog.at_level(logging.INFO, logger="room_service.test"):
        logger.info("connecting to ws://x/api/events?token=%s", secret)
    assert secret not in caplog.text and "***" in caplog.text


async def test_room_token_never_in_status_or_logs(harness: Harness, caplog):
    h = harness
    with caplog.at_level(logging.DEBUG):
        await h.go_live()
        st, s = await h.api("GET", "/api/v1/status")
    blob = json.dumps(s)
    assert ROOM_TOKEN not in blob and h.stream.token not in blob
    assert ROOM_TOKEN not in caplog.text and h.stream.token not in caplog.text
