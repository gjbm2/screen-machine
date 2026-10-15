"""HTTP API auth, request validation, catalog and the token file (no GPU sessions are started)."""
from __future__ import annotations

import pathlib

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from stream_service.media import MediaHub
from stream_service.server import MANAGER, make_app

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"
TOKEN = "ab" * 32


@pytest.fixture
async def client():
    app = make_app(config_dir=REPO_CONFIG)
    async with TestClient(TestServer(app)) as c:
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}

ROUTES = [("GET", "/api/catalog"), ("GET", "/api/sessions"), ("POST", "/api/sessions"), ("GET", "/api/state"),
          ("GET", "/api/sessions/s-1"), ("PATCH", "/api/sessions/s-1"), ("DELETE", "/api/sessions/s-1"),
          ("POST", "/api/sessions/s-1/go"), ("POST", "/api/sessions/s-1/exit"), ("POST", "/api/sessions/s-1/renew"),
          ("GET", "/api/sessions/s-1/frames")]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN},
                                     {"Authorization": f"Basic {TOKEN}"}])
async def test_api_needs_no_password(client, method, path, headers):
    r = await client.request(method, path, headers=headers)
    assert r.status != 401      # no password (owner, 14 Oct); any Authorization header is ignored


@pytest.mark.asyncio
async def test_open_routes(client):
    r = await client.get("/healthz")
    assert r.status == 200 and (await r.json())["ok"] is True
    r = await client.get("/dev")
    assert r.status == 200 and "LivePlayer" in await r.text()
    r = await client.get("/static/live-player.js")
    assert r.status == 200


@pytest.mark.asyncio
async def test_catalog(client):
    r = await client.get("/api/catalog", headers=AUTH)
    assert r.status == 200
    cat = await r.json()
    assert set(cat) >= {"sources", "transitions", "filters", "profiles", "presets"}
    assert set(cat["profiles"]) == {"hevc-2160p60", "hevc-2160p30", "hevc-1080p60", "h264-720p30", "av1-720p30"}
    p = cat["profiles"]["hevc-2160p60"]
    assert (p["codec"], p["w"], p["h"], p["fps"], p["bitrate"], p["gop_s"]) == ("hevc", 3840, 2160, 60, "50M", 1.0)
    assert cat["presets"]["scenes"]["fractal-zoom"]["source"] == "fractal_zoom"
    assert cat["presets"]["transitions"]["crossfade-3s"]["transition"] == "crossfade"
    assert r.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize("body,code", [
    ({"screen": "devtest", "anchor": {"rgb": [0, 0, 0]}}, "bad_request"),                       # no scene
    ({"screen": "devtest", "scene": "nope", "anchor": {"rgb": [0, 0, 0]}}, "unknown_preset"),
    ({"screen": "devtest", "scene": {"preset": "fractal-zoom", "params": {"speed": 1}},
      "anchor": {"rgb": [0, 0, 0]}}, "unknown_param"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 999]}}, "bad_still"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"png_b64": "!!"}}, "bad_still"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"png_b64": "aGVsbG8="}}, "bad_image"),
    ({"screen": "devtest", "scene": "fractal-zoom", "profile": "8k", "anchor": {"rgb": [0, 0, 0]}},
     "unknown_profile"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 0]}, "bogus": 1}, "bad_request"),
    ({"screen": "Dev Test!", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 0]}}, "bad_request"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 0]},
      "transitions": {"in": {"transition": "nope"}}}, "unknown_transition"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 0]}, "width": 2160}, "bad_request"),
    ({"screen": "devtest", "scene": "fractal-zoom", "anchor": {"rgb": [0, 0, 0]}, "profile": "h264-720p30",
      "width": 2160, "height": 3840}, "bad_request"),                                     # over the profile's pixels
])
async def test_create_validation(client, body, code):
    r = await client.post("/api/sessions", json=body, headers=AUTH)
    assert r.status == 400, await r.text()
    assert (await r.json())["error"] == code


@pytest.mark.asyncio
async def test_bad_json_and_unknown_session(client):
    r = await client.post("/api/sessions", data=b"{not json", headers={**AUTH, "Content-Type": "application/json"})
    assert r.status == 400 and (await r.json())["error"] == "bad_json"
    for method, path in [("GET", "/api/sessions/s-404"), ("POST", "/api/sessions/s-404/go"),
                         ("DELETE", "/api/sessions/s-404")]:
        r = await client.request(method, path, headers=AUTH, json={})
        assert r.status == 404 and (await r.json())["error"] == "not_found"


@pytest.mark.asyncio
async def test_state_lists_gpus(client):
    r = await client.get("/api/state", headers=AUTH)
    st = await r.json()
    assert st["sessions"] == []
    assert all({"index", "name", "mem_used_mb"} <= set(g) for g in st["gpus"])


@pytest.mark.asyncio
async def test_events_socket_token(client):
    (await client.ws_connect("/api/events?token=wrong")).close   # no password: any token (or none) is fine
    ws = await client.ws_connect(f"/api/events?token={TOKEN}")
    await ws.send_str("ignored")             # client messages are ignored
    client.app[MANAGER].events.emit({"type": "session.state", "session": "s-x", "screen": "devtest",
                                     "state": "holding"})
    msg = await ws.receive_json(timeout=2)
    assert msg["state"] == "holding"
    await ws.close()


class FakeSession:
    def __init__(self):
        self.id = "s-fake"
        self.key = "right-key"
        self.media = MediaHub(self.id)
        self.state = "holding"

    async def end(self, reason, detail=None):
        self.state = "ended"


@pytest.mark.asyncio
async def test_media_socket_key(client):
    client.app[MANAGER].sessions["s-fake"] = FakeSession()
    ws = await client.ws_connect("/media/s-fake?k=wrong-key")
    msg = await ws.receive(timeout=2)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 4403
    ws = await client.ws_connect("/media/s-fake")
    msg = await ws.receive(timeout=2)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 4403
    ws = await client.ws_connect("/media/s-none?k=x")
    msg = await ws.receive(timeout=2)
    assert msg.type == aiohttp.WSMsgType.CLOSE and msg.data == 4404
    # the right key gets config + init once known, then frames from a keyframe, then eos
    fake = client.app[MANAGER].sessions["s-fake"]
    ws = await client.ws_connect("/media/s-fake?k=right-key")
    fake.media.set_config({"type": "config", "codec": "h264"}, b"\x01init")
    fake.media.on_frame(0, False, b"\x02\x00\x00\x00\x00\x00delta")   # skipped: no keyframe yet
    fake.media.on_frame(1, True, b"\x02\x01\x00\x00\x00\x01key")
    assert (await ws.receive_json(timeout=2))["session"] == "s-fake"
    assert await ws.receive_bytes(timeout=2) == b"\x01init"
    assert await ws.receive_bytes(timeout=2) == b"\x02\x01\x00\x00\x00\x01key"
    fake.media.end()
    assert (await ws.receive_json(timeout=2)) == {"type": "eos"}
    msg = await ws.receive(timeout=2)
    assert msg.type == aiohttp.WSMsgType.CLOSE


@pytest.mark.asyncio
async def test_local_control_answers_control_only_from_this_machine():
    from stream_service.server import make_app
    app = make_app(config_dir=REPO_CONFIG, local_control=True)
    async with TestClient(TestServer(app)) as c:      # the test client connects from 127.0.0.1: allowed
        assert (await c.get("/api/catalog")).status == 200
        assert (await c.get("/healthz")).status == 200
    from stream_service.server import _control_path
    assert _control_path("/api/sessions") and _control_path("/dev") and _control_path("/dev/library")
    assert not _control_path("/healthz") and not _control_path("/media/s-1") and not _control_path("/static/x.js")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_local_control_sends_other_machines_to_the_app(method):
    """A browser (GET) or curl -I (HEAD) on :8702/dev from the LAN is redirected to the app's console; the API
    answers 403 local_only."""
    from unittest import mock
    from aiohttp.test_utils import make_mocked_request
    from stream_service.server import auth_and_errors, LOCAL_CONTROL, APP_URL
    app = web.Application()
    app[LOCAL_CONTROL] = True
    app[APP_URL] = "http://app.example:8700/renderer"
    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda k, d=None: ("192.168.1.50", 50000) if k == "peername" else d

    async def handler(request):
        return web.Response(text="ok")
    with pytest.raises(web.HTTPFound) as e:
        await auth_and_errors(make_mocked_request(method, "/dev", app=app, transport=transport), handler)
    assert e.value.location == "http://app.example:8700/renderer/dev"
    resp = await auth_and_errors(make_mocked_request(method, "/api/catalog", app=app, transport=transport), handler)
    assert resp.status == 403


@pytest.mark.asyncio
async def test_the_console_page_always_opens_through_the_app():
    """Even from this machine (e.g. localhost:8702/dev on the PC): the console needs room's API on its origin."""
    from stream_service.server import make_app
    app = make_app(config_dir=REPO_CONFIG, local_control=True, app_url="http://lan:8700/renderer")
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/dev?attach=x", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "http://lan:8700/renderer/dev?attach=x"
        r = await c.get("/dev", headers={"X-Forwarded-Prefix": "/renderer"}, allow_redirects=False)
        assert r.status == 200                                      # through the app's pass-through
        assert (await c.get("/dev/library", allow_redirects=False)).status == 200

