"""Fakes for room_service tests: stream_service, display pages, Flask, the snapshotter.

No real devices, ports or browsers are touched (except test_snapshot.py, which drives a
headless Chrome against a static fixture page).
"""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import time
import uuid
from typing import Any, Callable

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

PNG_SIG = b"\x89PNG\r\n\x1a\n"

CATALOG = {
    "sources": [{"name": "fractal_zoom", "label": "Fractal zoom", "internal": False,
                 "schema": {"type": "object", "properties": {"zoom_rate": {"type": "number", "default": 0.25}}}},
                {"name": "image", "label": "Image", "internal": True, "schema": {}}],
    "transitions": [{"name": "crossfade", "label": "Crossfade",
                     "schema": {"type": "object", "properties": {"duration_s": {"type": "number", "default": 3.0}}}},
                    {"name": "cut", "label": "Cut", "schema": {}}],
    "filters": [],
    "profiles": {"hevc-2160p60": {"codec": "hevc", "w": 3840, "h": 2160, "fps": 60},
                 "h264-720p30": {"codec": "h264", "w": 1280, "h": 720, "fps": 30}},
    "presets": {
        "scenes": {"fractal-zoom": {"label": "Fractal zoom", "source": "fractal_zoom", "params": {"zoom_rate": 0.25}},
                   "other-scene": {"label": "Other", "source": "fractal_zoom", "params": {"zoom_rate": 1.0}}},
        "transitions": {"xfade": {"label": "Fast crossfade", "transition": "crossfade",
                                  "params": {"duration_s": 0.1, "easing": "linear"}},
                        "crossfade-3s": {"label": "Crossfade 3 s", "transition": "crossfade",
                                         "params": {"duration_s": 3.0, "easing": "ease-in-out"}}},
    },
}


async def _serve(app: web.Application) -> TestServer:
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    return server


# ---------------------------------------------------------------------------
# stream_service
# ---------------------------------------------------------------------------

class FakeStreamService:
    def __init__(self, token: str) -> None:
        self.token = token
        self.sessions: dict[str, dict] = {}
        self.idem: dict[str, str] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.warms: list[tuple[float, dict]] = []   # POST /api/workers/warm (loop time, body); not in calls
        self.warm_status = 200
        self.event_clients: set[web.WebSocketResponse] = set()
        self.ready_delay = 0.02
        self.emit_ready = True
        self.in_done_delay = 0.03
        self.held_delay = 0.03
        self.emit_held = True
        self.create_status = 201
        self.create_delay = 0.0          # seconds before the create responds (the session exists meanwhile)
        self.patch_delay = 0.0
        self.go_status = 200
        self.hold_pts = 12.5
        self.server: TestServer | None = None
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()

    @property
    def url(self) -> str:
        return str(self.server.make_url("")).rstrip("/")

    async def start(self, port: int | None = None) -> str:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get("/api/catalog", self._catalog)
        app.router.add_get("/api/sessions", self._list)
        app.router.add_post("/api/sessions", self._create)
        app.router.add_get("/api/sessions/{id}", self._get)
        app.router.add_post("/api/sessions/{id}/go", self._go)
        app.router.add_post("/api/sessions/{id}/exit", self._exit)
        app.router.add_post("/api/sessions/{id}/renew", self._renew)
        app.router.add_patch("/api/sessions/{id}", self._patch)
        app.router.add_put("/api/sessions/{id}/fx", self._renew)        # FX stack: recorded, accepted
        app.router.add_post("/api/sessions/{id}/cue", self._renew)      # a cue (e.g. to: auto): recorded, accepted
        app.router.add_delete("/api/sessions/{id}", self._delete)
        app.router.add_get("/api/state", self._state)
        app.router.add_post("/api/workers/warm", self._warm)
        app.router.add_get("/api/events", self._events)
        self.server = TestServer(app, host="127.0.0.1", port=port)
        await self.server.start_server()
        return self.url

    async def stop(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        for ws in list(self.event_clients):
            await ws.close()
        if self.server:
            await self.server.close()

    async def drop_events(self) -> None:
        """Close the events sockets (room_service reconnects)."""
        for ws in list(self.event_clients):
            await ws.close()

    # helpers -------------------------------------------------------------
    def _later(self, delay: float, fn: Callable[[], None]) -> None:
        async def go() -> None:
            await asyncio.sleep(delay)
            fn()
        t = asyncio.get_running_loop().create_task(go())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def emit(self, evt: dict) -> None:
        text = json.dumps(evt)
        for ws in list(self.event_clients):
            if not ws.closed:
                asyncio.get_running_loop().create_task(ws.send_str(text))

    def _evt(self, sid: str, type_: str, **kw: Any) -> None:
        s = self.sessions.get(sid)
        self.emit({"type": type_, "session": sid, "screen": s["screen"] if s else kw.pop("screen", None), **kw})

    def _set_state(self, sid: str, state: str) -> None:
        if sid in self.sessions:
            self.sessions[sid]["state"] = state
            self._evt(sid, "session.state", state=state)

    def fail_session(self, sid: str, detail: str = "encoder exited rc=1") -> None:
        self._evt(sid, "session.error", detail=detail)
        self._set_state(sid, "failed")

    def calls_of(self, method: str, prefix: str = "") -> list[tuple[str, str, Any]]:
        return [c for c in self.calls if c[0] == method and c[1].startswith(prefix)]

    def _auth(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            raise web.HTTPUnauthorized(text='{"error":"unauthorized"}', content_type="application/json")

    async def _body(self, request: web.Request) -> Any:
        self._auth(request)
        body = await request.json() if request.can_read_body else None
        self.calls.append((request.method, request.path, body))
        return body

    def _summary(self, s: dict) -> dict:
        out = {k: s.get(k) for k in ("session", "screen", "state", "profile", "created_at", "scene")}
        out.update({k: s[k] for k in ("auto", "direction", "palette", "current_scene", "fx") if k in s})   # a look
        return out

    # handlers ------------------------------------------------------------
    async def _catalog(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(CATALOG)

    async def _list(self, request: web.Request) -> web.Response:
        await self._body(request)
        return web.json_response({"sessions": [self._summary(s) for s in self.sessions.values()]})

    async def _get(self, request: web.Request) -> web.Response:
        await self._body(request)
        s = self.sessions.get(request.match_info["id"])
        if not s:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(self._summary(s))

    async def _create(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        if self.create_status != 201:
            return web.json_response({"error": "boom", "detail": "fake create failure"}, status=self.create_status)
        key = body.get("idempotency_key")
        if key and key in self.idem and self.idem[key] in self.sessions:   # as stream_service does
            s = self.sessions[self.idem[key]]
            return web.json_response({"session": s["session"], "state": s["state"], "attach": self._attach(s["session"])},
                                     status=200)
        sid = f"s-{next(self._ids):04d}"
        if key:
            self.idem[key] = sid
        self.sessions[sid] = {"session": sid, "screen": body["screen"], "state": "starting",
                              "profile": body.get("profile"), "created_at": time.time(),
                              "scene": body.get("scene"), "body": body}
        if self.create_delay:
            await asyncio.sleep(self.create_delay)   # like a slow GPU start: listed, not answered yet
        if self.emit_ready:
            def ready() -> None:
                if sid in self.sessions:
                    self._set_state(sid, "holding")
                    self._evt(sid, "session.ready")
            self._later(self.ready_delay, ready)
        return web.json_response({"session": sid, "state": "starting", "attach": self._attach(sid)}, status=201)

    @staticmethod
    def _attach(sid: str) -> dict:
        return {"url": f"ws://127.0.0.1:1/media/{sid}?k=key", "codec": "h264", "w": 1280, "h": 720,
                "fps": 30, "target_buffer_ms": 300}

    async def _go(self, request: web.Request) -> web.Response:
        await self._body(request)
        sid = request.match_info["id"]
        s = self.sessions.get(sid)
        if not s:
            return web.json_response({"error": "not_found"}, status=404)
        if self.go_status != 200 or s["state"] != "holding":
            return web.json_response({"error": "not_holding", "detail": s["state"]}, status=409)
        self._set_state(sid, "entering")
        self._evt(sid, "session.transition", kind="in", phase="started", pts=1.0)

        def done() -> None:
            if sid in self.sessions and self.sessions[sid]["state"] == "entering":
                self._evt(sid, "session.transition", kind="in", phase="done", pts=4.0)
                self._set_state(sid, "live")
        self._later(self.in_done_delay, done)
        return web.json_response({})

    async def _exit(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        sid = request.match_info["id"]
        s = self.sessions.get(sid)
        if not s:
            return web.json_response({"error": "not_found"}, status=404)
        s["exit_body"] = body
        self._set_state(sid, "exiting")
        self._evt(sid, "session.transition", kind="out", phase="started", pts=10.0)
        if self.emit_held:
            def held() -> None:
                if sid in self.sessions:
                    self._evt(sid, "session.transition", kind="out", phase="done", pts=self.hold_pts)
                    self._set_state(sid, "held")
                    self._evt(sid, "session.held", hold_pts=self.hold_pts)
            self._later(self.held_delay, held)
        return web.json_response({}, status=202)

    async def _renew(self, request: web.Request) -> web.Response:
        await self._body(request)
        if request.match_info["id"] not in self.sessions:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({})

    async def _patch(self, request: web.Request) -> web.Response:
        await self._body(request)
        if request.match_info["id"] not in self.sessions:
            return web.json_response({"error": "not_found"}, status=404)
        if self.patch_delay:
            await asyncio.sleep(self.patch_delay)   # accepted, but slow to answer
        return web.json_response({"ok": True})

    async def _delete(self, request: web.Request) -> web.Response:
        await self._body(request)
        sid = request.match_info["id"]
        s = self.sessions.pop(sid, None)
        if not s:
            return web.json_response({"error": "not_found"}, status=404)
        self.emit({"type": "session.ended", "session": sid, "screen": s["screen"], "reason": "deleted"})
        return web.Response(status=204)

    async def _warm(self, request: web.Request) -> web.Response:
        self._auth(request)
        body = await request.json()
        self.warms.append((asyncio.get_running_loop().time(), body))
        if self.warm_status != 200:
            return web.json_response({"error": "boom"}, status=self.warm_status)
        return web.json_response({"screen": body.get("screen"), "workers": "proc", "spawned": True})

    async def _state(self, request: web.Request) -> web.Response:
        await self._body(request)
        return web.json_response({"sessions": [self._summary(s) for s in self.sessions.values()], "gpus": []})

    async def _events(self, request: web.Request) -> web.WebSocketResponse:
        if request.query.get("token") != self.token:
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.event_clients.add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            self.event_clients.discard(ws)
        return ws


# ---------------------------------------------------------------------------
# Display page (implements the LIVE_V1 §2.2 page rules the room relies on)
# ---------------------------------------------------------------------------

def art_state(base_url: str = "/output/devtest.jpg?v=1", brightness: float = 0.5) -> dict:
    return {"base_url": base_url, "kind": "image", "clip_t": None,
            "mask": {"brightness": brightness, "warm_hex": "#FFFFFF", "warm_alpha": 0.0},
            "flags": {"nomask": False, "nocolour": False, "fit": "fit"},
            "viewport": {"w": 3840, "h": 2160}}


class FakePage:
    def __init__(self, room_url: str, screen: str = "devtest", role: str = "kiosk", *,
                 page_load_id: str | None = None, hb_interval: float = 0.2, user_agent: str | None = None) -> None:
        self.room_url = room_url.rstrip("/")
        self.screen = screen
        self.role = role
        self.page_load_id = page_load_id or f"p-{uuid.uuid4().hex[:8]}"
        self.hb_interval = hb_interval
        # behaviour switches
        self.ack_frozen = True
        self.frozen_delay = 0.0
        self.ack_live = True
        self.live_delay = 0.01
        self.live_failed = False
        self.ack_art = True
        self.art_delay = 0.01
        self.send_hello = True
        self.key: str | None = None            # sent as hello.key when set
        self.url_key: str | None = None        # sent as ?ctlkey= when set
        self.user_agent = user_agent           # the browser (pages.device_of); None: aiohttp's own, the same for all
        self._origin: str | None = None
        self.policy_seen: list[dict] = []
        self.applied_policy: dict | None = None  # what kiosk_state() reports as applied
        self.report_policy = True                # False: a page build without kiosk.state.policy
        self.state = art_state()
        # observed
        self.received: list[tuple[str, dict]] = []
        self.welcome: dict | None = None
        self.layer = "art"
        self.frozen: str | None = None
        self.session: str | None = None
        self.mask: dict | None = None
        self._http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._tasks: list[asyncio.Task] = []
        self._got = asyncio.Event()
        self.welcome_attached: str | None = None   # a session attached because welcome said so

    async def connect(self) -> "FakePage":
        self._http = aiohttp.ClientSession()
        url = self.room_url.replace("http", "ws", 1) + f"/v1/screen/{self.screen}?role={self.role}"
        if self.url_key:
            url += f"&ctlkey={self.url_key}"
        headers = {"Origin": self._origin} if self._origin else {}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        self.ws = await self._http.ws_connect(url, heartbeat=None, headers=headers or None)
        self._tasks = [asyncio.get_running_loop().create_task(self._reader()),
                       asyncio.get_running_loop().create_task(self._hb())]
        if self.send_hello:
            hello = {"role": self.role, "screen": self.screen, "page_load_id": self.page_load_id,
                     "build": "test", "caps": {"mse_hevc": False, "mse_h264": True, "mse_av1": True},
                     "flags": {"nomask": False, "nocolour": False, "fit": "fit"}}
            if self.key:
                hello["key"] = self.key
            await self.send("hello", hello)
        return self

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.ws is not None:
            await self.ws.close()
        if self._http is not None:
            await self._http.close()

    async def send(self, type_: str, data: dict | None = None) -> None:
        await self.ws.send_str(json.dumps({"v": 1, "type": type_, "data": data or {}}))

    async def ack(self, **data: Any) -> None:
        await self.send("ack", data)

    def _later(self, delay: float, coro_fn: Callable[[], Any]) -> None:
        async def go() -> None:
            if delay:
                await asyncio.sleep(delay)
            await coro_fn()
        self._tasks.append(asyncio.get_running_loop().create_task(go()))

    async def _hb(self) -> None:
        while True:
            await asyncio.sleep(self.hb_interval)
            if self.ws.closed:
                return
            await self.send("hb")

    async def _reader(self) -> None:
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            env = json.loads(msg.data)
            t, d = env["type"], env.get("data") or {}
            if t != "hb":
                self.received.append((t, d))
                self._got.set()
            await self._handle(t, d)

    async def _handle(self, t: str, d: dict) -> None:
        if t == "welcome":
            self.welcome = d
            self.applied_policy = dict(d.get("policy") or {})
            if d.get("mask"):
                self.mask = d["mask"]
            # LIVE_V1 §2.2: attach from welcome.live only if not already playing it.
            live = d.get("live") or {}
            att = live.get("attach") or {}
            if att.get("url") and self.session != live.get("session"):
                self.session = live.get("session")
                self.layer = "live"
                self.welcome_attached = live.get("session")
        elif t == "policy":
            self.policy_seen.append(d)
            self.applied_policy = {**(self.applied_policy or {}), **d}
        elif t == "mask.set":
            self.mask = d
        elif t == "freeze":
            self.frozen = d["fid"]
            if self.ack_frozen:
                fid, state = d["fid"], copy.deepcopy(self.state)
                self._later(self.frozen_delay, lambda: self.ack(kind="frozen", fid=fid, state=state))
        elif t == "unfreeze":
            if self.frozen == d.get("fid"):
                self.frozen = None
        elif t == "live.attach":
            sid = d["session"]
            self.session = sid
            if self.live_failed:
                self._later(self.live_delay, lambda: self.ack(kind="live_failed", session=sid, reason="warmup-timeout"))
            elif self.ack_live:
                async def visible() -> None:
                    if self.session == sid:
                        self.layer = "live"
                        await self.ack(kind="live_visible", session=sid, media_time=0.0)
                self._later(self.live_delay, visible)
        elif t == "live.leave":
            sid = d["session"]
            if self.ack_art:
                async def art() -> None:
                    if self.session == sid:
                        self.layer, self.session = "art", None
                        await self.ack(kind="art_visible", session=sid, media_time=d.get("hold_pts"), reason="exit")
                self._later(self.art_delay, art)
        elif t == "live.abort":
            sid = d.get("session")
            was_live = self.layer == "live" and self.session == sid
            if self.session == sid:
                self.layer, self.session = "art", None
            if was_live:
                await self.ack(kind="art_visible", session=sid, media_time=None, reason="abort")

    # test actions ----------------------------------------------------------
    async def stall(self) -> None:
        sid = self.session
        self.layer, self.session = "art", None
        await self.ack(kind="art_visible", session=sid, media_time=3.0, reason="stall")

    async def kiosk_state(self, layer: str | None = None, session: str | None = None) -> None:
        layer = layer or self.layer
        sid = session if session is not None else self.session
        extra = {"policy": self.applied_policy} if self.report_policy else {}
        await self.send("kiosk.state", {
            **extra, "layer": layer, "frozen": self.frozen is not None, "base_url": self.state["base_url"],
            "mask": self.state["mask"],
            "live": ({"session": sid, "fps": 30, "presented": 100, "dropped": 0, "stalls": 0,
                      "buffered_ms": 300, "edge_jumps": 0} if layer == "live" else None)})

    def types(self) -> list[str]:
        return [t for t, _ in self.received]

    def of(self, type_: str) -> list[dict]:
        return [d for t, d in self.received if t == type_]

    async def wait_for(self, type_: str, pred: Callable[[dict], bool] | None = None,
                       timeout: float = 3.0, after: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for t, d in self.received[after:]:
                if t == type_ and (pred is None or pred(d)):
                    return d
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"page never got {type_} (got {self.types()})")
            self._got.clear()
            try:
                await asyncio.wait_for(self._got.wait(), min(remaining, 0.1))
            except asyncio.TimeoutError:
                pass


# ---------------------------------------------------------------------------
# Flask (GET /api/<id>/mask and the :8765 WS)
# ---------------------------------------------------------------------------

class FakeFlask:
    def __init__(self) -> None:
        self.masks: dict[str, dict] = {}
        self.default_mask = {"brightness": 0.5, "warm_hex": "#FFFFFF", "warm_alpha": 0.0}
        self.requests: list[tuple[float, str]] = []
        self.fail = False
        self.ws_clients: set[web.WebSocketResponse] = set()
        self.client_frames: list[Any] = []     # anything a client ever sent us (must stay empty)
        self.ws_connects = 0
        self.ping_clients = False
        self.pongs = 0
        self.http_server: TestServer | None = None
        self.ws_server: TestServer | None = None
        self.outputs: dict[str, float] = {}     # /output/<name> -> mtime (HEAD only: the headless anchor's art)

    @property
    def url(self) -> str:
        return str(self.http_server.make_url("")).rstrip("/")

    @property
    def ws_url(self) -> str:
        return str(self.ws_server.make_url("/")).replace("http", "ws", 1)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/{screen}/mask", self._mask)
        app.router.add_route("HEAD", "/output/{name}", self._output)
        self.http_server = await _serve(app)
        wapp = web.Application()
        wapp.router.add_get("/", self._ws)
        self.ws_server = await _serve(wapp)

    async def stop(self) -> None:
        for ws in list(self.ws_clients):
            await ws.close()
        for s in (self.http_server, self.ws_server):
            if s:
                await s.close()

    def set_mask(self, screen: str, **kw: Any) -> None:
        self.masks[screen] = {**self.masks.get(screen, self.default_mask), **kw}

    async def _mask(self, request: web.Request) -> web.Response:
        screen = request.match_info["screen"]
        self.requests.append((time.monotonic(), screen))
        if self.fail:
            return web.Response(status=500, text="boom")
        return web.json_response({**self.masks.get(screen, self.default_mask), "timestamp": "x"})

    async def _output(self, request: web.Request) -> web.Response:
        mt = self.outputs.get(request.match_info["name"])
        if mt is None:
            return web.Response(status=404)
        from email.utils import formatdate
        return web.Response(status=200, headers={"Last-Modified": formatdate(mt, usegmt=True)})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(autoping=False)   # see every frame, including pongs
        await ws.prepare(request)
        self.ws_clients.add(ws)
        self.ws_connects += 1
        pinger = None
        if self.ping_clients:
            async def ping() -> None:
                while not ws.closed:
                    await ws.ping()
                    await asyncio.sleep(0.1)
            pinger = asyncio.get_running_loop().create_task(ping())
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.PONG:
                    self.pongs += 1
                elif msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY, aiohttp.WSMsgType.PING):
                    self.client_frames.append((msg.type, msg.data))
        finally:
            if pinger:
                pinger.cancel()
            self.ws_clients.discard(ws)
        return ws

    async def broadcast(self, obj: Any) -> None:
        for ws in list(self.ws_clients):
            await ws.send_str(json.dumps(obj))

    async def lux(self, lux: float = 12.0) -> None:
        await self.broadcast({"sensor_name": "living-room", "lux": lux})

    async def drop_ws(self) -> None:
        for ws in list(self.ws_clients):
            await ws.close()


# ---------------------------------------------------------------------------
# Snapshotter
# ---------------------------------------------------------------------------

class FakeSnapshotter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.prefetched: list[tuple[str, dict]] = []
        self.delay = 0.0
        self.ready_delay = 0.0
        self.fail: str | None = None
        self.fail_times = 0                # > 0: fail this many calls (with `fail` or a default), then work
        self.not_ready: str | None = None

    async def start(self, screens: list[str]) -> None:
        return None

    async def ensure_ready(self, screen: str) -> None:
        if self.ready_delay:
            await asyncio.sleep(self.ready_delay)
        if self.not_ready:
            from room_service.snapshot import SnapshotError
            raise SnapshotError(self.not_ready)

    def prefetch(self, screen: str, state: dict) -> None:
        self.prefetched.append((screen, copy.deepcopy(state)))

    async def snapshot(self, screen: str, state: dict) -> bytes:
        self.calls.append((screen, copy.deepcopy(state)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail or self.fail_times > 0:
            self.fail_times = max(0, self.fail_times - 1)
            from room_service.snapshot import SnapshotError
            raise SnapshotError(self.fail or "fake snapshot failure")
        return PNG_SIG + f"{screen}:{len(self.calls)}".encode()

    async def close(self) -> None:
        return None

    def status(self) -> dict:
        return {"fake": True, "count": len(self.calls)}
