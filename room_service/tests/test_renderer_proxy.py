"""renderer_proxy.py: /renderer/* -> stream_service, HTTP and WebSocket (kept light)."""

from __future__ import annotations

from types import SimpleNamespace

import aiohttp
import pytest_asyncio
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from room_service import renderer_proxy

from .conftest import STREAM_TOKEN

BLOB = bytes(range(256)) * 300            # 76.8 KB, every byte value


async def _echo(request: web.Request) -> web.Response:
    body = await request.read()
    return web.json_response({"method": request.method, "path": request.path, "query": dict(request.query),
                              "len": len(body), "ctype": request.headers.get("Content-Type"),
                              "prefix": request.headers.get("X-Forwarded-Prefix"),
                              "ae": request.headers.get("Accept-Encoding")},
                             headers={"Cache-Control": "no-store"})


async def _blob(request: web.Request) -> web.Response:
    body = await request.read()
    return web.Response(body=body[::-1], content_type="application/octet-stream")


async def _missing(request: web.Request) -> web.Response:
    return web.json_response({"error": "unknown_session", "detail": request.match_info["sid"]}, status=404)


async def _stream(request: web.Request) -> web.StreamResponse:
    resp = web.StreamResponse(headers={"Content-Type": "video/mp4", "Cache-Control": "max-age=300"})
    await resp.prepare(request)
    for i in range(3):
        await resp.write(b"chunk%d;" % i)
    await resp.write_eof()
    return resp


async def _ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_str("hello " + request.query.get("who", "?"))
    async for msg in ws:
        if msg.type is WSMsgType.TEXT:
            if msg.data == "bye":
                await ws.close(code=4001, message=b"bye")
                break
            await ws.send_str(msg.data.upper())
        elif msg.type is WSMsgType.BINARY:
            await ws.send_bytes(msg.data[::-1])
    return ws


class Rig:
    def __init__(self) -> None:
        self.upstream: TestServer | None = None
        self.proxy: TestServer | None = None
        self.http: aiohttp.ClientSession | None = None

    async def start(self) -> "Rig":
        up = web.Application(client_max_size=64 * 1024 * 1024)
        up.router.add_route("*", "/api/echo", _echo)
        up.router.add_post("/dev/blob", _blob)
        up.router.add_get("/api/sessions/{sid}", _missing)
        up.router.add_get("/dev/preview/{name}.mp4", _stream)
        up.router.add_get("/api/events", _ws)
        self.upstream = TestServer(up, host="127.0.0.1")
        await self.upstream.start_server()
        self.http = aiohttp.ClientSession()
        base = str(self.upstream.make_url("")).rstrip("/")
        svc = SimpleNamespace(cfg=SimpleNamespace(stream_url=base, show_url=base,      # one fake upstream for both
                                                  web_origins=["http://room.example"]),
                              http=self.http)
        app = web.Application()
        renderer_proxy.register(app, svc)
        self.proxy = TestServer(app, host="127.0.0.1")
        await self.proxy.start_server()
        return self

    def url(self, path: str) -> str:
        return str(self.proxy.make_url(path))

    async def stop(self) -> None:
        await self.proxy.close()
        await self.upstream.close()
        await self.http.close()


@pytest_asyncio.fixture
async def rig():
    r = await Rig().start()
    try:
        yield r
    finally:
        await r.stop()


async def test_get_and_post_with_query(rig: Rig) -> None:
    async with rig.http.get(rig.url("/renderer/api/echo?a=1&b=x%20y"), skip_auto_headers=["Accept-Encoding"]) as r:
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
        assert await r.json() == {"method": "GET", "path": "/api/echo", "query": {"a": "1", "b": "x y"}, "len": 0,
                                  "ctype": None, "prefix": "/renderer", "ae": None}   # nothing the client didn't send
    async with rig.http.post(rig.url("/renderer/api/echo?k=v"), json={"x": 1},
                             headers={"Origin": "http://room.example"}) as r:
        assert r.status == 200 and r.headers["Access-Control-Allow-Origin"] == "http://room.example"
        j = await r.json()
        assert (j["method"], j["query"], j["len"], j["ctype"]) == ("POST", {"k": "v"}, 8, "application/json")


async def test_binary_body_both_ways(rig: Rig) -> None:
    async with rig.http.post(rig.url("/renderer/dev/blob"), data=BLOB,
                             headers={"Content-Type": "application/octet-stream"}) as r:
        assert r.status == 200 and r.headers["Content-Type"] == "application/octet-stream"
        assert int(r.headers["Content-Length"]) == len(BLOB)
        assert await r.read() == BLOB[::-1]


async def test_404_and_stream_pass_through(rig: Rig) -> None:
    async with rig.http.get(rig.url("/renderer/api/sessions/nope")) as r:
        assert r.status == 404 and await r.json() == {"error": "unknown_session", "detail": "nope"}
    async with rig.http.get(rig.url("/renderer/no/such/route")) as r:
        assert r.status == 404                               # upstream's own 404
    async with rig.http.get(rig.url("/renderer/dev/preview/fire.mp4")) as r:
        assert r.status == 200 and r.headers["Content-Type"] == "video/mp4"
        assert r.headers["Cache-Control"] == "max-age=300"
        assert await r.read() == b"chunk0;chunk1;chunk2;"


async def test_websocket_text_and_binary(rig: Rig) -> None:
    async with rig.http.ws_connect(rig.url("/renderer/api/events?who=room")) as ws:
        assert (await ws.receive_str(timeout=2)) == "hello room"
        await ws.send_str("abc")
        assert (await ws.receive_str(timeout=2)) == "ABC"
        await ws.send_bytes(b"\x00\x01\xff")
        assert (await ws.receive_bytes(timeout=2)) == b"\xff\x01\x00"
        await ws.send_str("bye")                             # upstream closes with 4001: passed on
        msg = await ws.receive(timeout=2)
        assert msg.type is WSMsgType.CLOSE and msg.data == 4001
    async with rig.http.get(rig.url("/renderer/api/nosocket"), headers={"Upgrade": "websocket"}) as r:
        assert r.status in (400, 404)                        # a refused upgrade is relayed, not a 500


async def test_unreachable_is_502(rig: Rig) -> None:
    await rig.upstream.close()
    async with rig.http.get(rig.url("/renderer/api/echo")) as r:
        assert r.status == 502 and (await r.json())["error"] == "stream_unreachable"


async def test_hooked_into_room_app(harness) -> None:
    """api.build_app mounts it: the harness's fake stream_service (which wants its token) answers through it."""
    st, body = await harness.api("GET", "/renderer/api/catalog", token=STREAM_TOKEN)
    assert st == 200 and isinstance(body, dict) and body


async def test_show_pass_through(rig: Rig) -> None:
    """/show/* -> show_service (c8's card and API), the same pass-through with its own prefix."""
    async with rig.http.post(rig.url("/show/api/echo?x=1"), json={"dir": 1}) as r:
        body = await r.json()
        assert r.status == 200 and body["path"] == "/api/echo" and body["prefix"] == "/show" and body["method"] == "POST"
    rig_down = SimpleNamespace(cfg=SimpleNamespace(stream_url="http://127.0.0.1:9", show_url="http://127.0.0.1:9",
                                                   web_origins=[]), http=rig.http)
    app = web.Application()
    renderer_proxy.register(app, rig_down)
    srv = TestServer(app, host="127.0.0.1")
    await srv.start_server()
    try:
        async with rig.http.get(str(srv.make_url("/show/api/v1/card"))) as r:
            assert r.status == 502 and (await r.json())["error"] == "show_unreachable"
    finally:
        await srv.close()
