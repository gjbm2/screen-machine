"""Generic pass-through: /renderer/<path> -> <stream_service url>/<path>, and /show/<path> -> <show_service url>/<path>
(c8's show engine on 127.0.0.1:8703, its card and API; /rs/show/* over HTTPS, Caddy strips /rs).

One route, any method, HTTP and WebSocket, so stream_service's own pages and API (/renderer/dev, /renderer/dev/library,
/renderer/dev/thumb/*, /renderer/dev/preview/*, /renderer/dev/rate, /renderer/api/*, /renderer/api/events,
/renderer/static/*) are reachable on room_service's origin. No auth (owner ruling, 14 Oct). api.py's middlewares
apply as to every route (the Host check; the Origin check is theirs for /api/* only); room_service's CORS policy
(web_origins) is applied to the proxied responses here, since they are streamed.

* HTTP: the method, query string, headers (less hop-by-hop ones) and body (streamed) go up; the status, headers and
  body (streamed, still encoded: no decompression, no rewriting) come back. Redirects are not followed; a Location
  that names the upstream is mapped under /renderer. Timeouts: connect 5 s, response headers 60 s; a body with a
  Content-Length may idle 60 s between chunks; a body without one (a stream) is not timed.
* WebSocket: an upgrade on /renderer/<path> opens ws://<stream_service>/<path>?<query> with the same subprotocols and
  relays text and binary frames both ways until either side closes; the close code is passed on. No timeouts.
* Errors (before any byte is relayed): 502 stream_unreachable, 504 stream_timeout; an upstream's own status (a 404,
  a refused upgrade) is relayed as it is.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import WSMsgType, hdrs, web
from multidict import CIMultiDict
from yarl import URL

if TYPE_CHECKING:
    from .service import RoomService

log = logging.getLogger("room_service.renderer_proxy")

PREFIX = "/renderer"
CONNECT_TIMEOUT_S = 5.0
HEADERS_TIMEOUT_S = 60.0          # a session create (~20 MB body) or a /dev/preview start can take a while
IDLE_READ_S = 60.0                # between body chunks, for bodies with a Content-Length only
WS_HANDSHAKE_S = 10.0
WS_CLIENT_MAX_MSG = 16 * 1024 * 1024
# No total and no read timeout: streamed bodies and sockets run as long as they run.
STREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=None, sock_connect=CONNECT_TIMEOUT_S, sock_read=None)
WS_TIMEOUT = aiohttp.ClientWSTimeout(ws_receive=None, ws_close=5.0)

HOP_BY_HOP = frozenset({"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
                        "te", "trailer", "transfer-encoding", "upgrade"})
REQ_DROP = HOP_BY_HOP | {"host", "expect"}
WS_REQ_DROP = REQ_DROP | {"content-length", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
                          "sec-websocket-protocol", "sec-websocket-accept"}
# Date / Server are aiohttp's own; CORS is room_service's policy, applied below. The upstream's Expose-Headers is
# relayed: it grants nothing by itself (only room's Allow-Origin does), and it lets an allowed page read a header the
# upstream names (show_service's X-Piano-Instrument on /show/api/v1/piano/live.mp3, 23 Oct).
RESP_DROP = HOP_BY_HOP | {"date", "server", "access-control-allow-origin", "access-control-allow-credentials",
                          "access-control-allow-methods", "access-control-allow-headers", "access-control-max-age"}
# aiohttp adds these when the caller doesn't: send only what the client sent (an Accept-Encoding the client never
# asked for would come back compressed, and the body is relayed as it is).
CLIENT_AUTO_HEADERS = ("Accept-Encoding", "User-Agent", "Content-Type")
# Close codes an endpoint may send (RFC 6455 §7.4); 1005/1006/1015 only describe what happened.
SENDABLE_CLOSE = frozenset({1000, 1001, 1002, 1003, *range(1007, 1015)})


def _err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def _filtered(headers: Any, drop: frozenset[str]) -> CIMultiDict[str]:
    """Headers less `drop` and anything the Connection header names (hop-by-hop by declaration)."""
    named = {t.strip().lower() for v in headers.getall(hdrs.CONNECTION, ()) for t in v.split(",")}
    return CIMultiDict((k, v) for k, v in headers.items() if k.lower() not in drop and k.lower() not in named)


def _close_code(code: int | None) -> int:
    if code in SENDABLE_CLOSE or (code is not None and 3000 <= code <= 4999):
        return code
    return 1011 if code == 1006 else 1000


class RendererProxy:
    """One pass-through: `prefix` on room_service's origin -> `base()` upstream (the renderer by default)."""

    def __init__(self, svc: "RoomService", prefix: str = PREFIX, base: Any = None, name: str = "stream_service") -> None:
        self.svc = svc
        self.prefix = prefix
        self.base = base or (lambda: str(svc.cfg.stream_url))
        self.name = name
        self.code = name.split("_")[0]          # error codes: stream_unreachable, show_timeout, ...
        self._own: aiohttp.ClientSession | None = None
        self._sockets: set[tuple[web.WebSocketResponse, aiohttp.ClientWebSocketResponse]] = set()

    # -- plumbing ------------------------------------------------------------
    def session(self) -> aiohttp.ClientSession:
        """room_service's shared ClientSession (made in RoomService.start); a private one only if there is none."""
        s = getattr(self.svc, "http", None)
        if s is not None and not s.closed:
            return s
        if self._own is None or self._own.closed:
            self._own = aiohttp.ClientSession()
        return self._own

    def upstream(self, request: web.Request, ws: bool = False) -> URL:
        base = self.base().rstrip("/")
        if ws:
            base = "wss" + base[5:] if base.startswith("https") else "ws" + base[4:] if base.startswith("http") else base
        tail = request.rel_url.raw_path[len(self.prefix):] or "/"
        qs = request.rel_url.raw_query_string
        return URL(base + tail + ("?" + qs if qs else ""), encoded=True)

    def request_headers(self, request: web.Request, drop: frozenset[str]) -> CIMultiDict[str]:
        h = _filtered(request.headers, drop)
        prior, peer = request.headers.get("X-Forwarded-For"), request.remote
        if peer:
            h["X-Forwarded-For"] = f"{prior}, {peer}" if prior else peer
        h["X-Forwarded-Host"] = request.host
        h["X-Forwarded-Proto"] = request.scheme
        h["X-Forwarded-Prefix"] = self.prefix
        return h

    def cors(self, request: web.Request, headers: CIMultiDict[str]) -> None:
        """api.py's cors_mw can't touch a response that is already sent, so apply its rule here."""
        origin = request.headers.get("Origin")
        if origin and origin in getattr(self.svc.cfg, "web_origins", ()):
            headers["Access-Control-Allow-Origin"] = origin
            vary = headers.get("Vary")
            headers["Vary"] = f"{vary}, Origin" if vary else "Origin"

    def location(self, value: str) -> str:
        base = self.base().rstrip("/")
        if value == base or value.startswith(base + "/"):
            return self.prefix + (value[len(base):] or "/")
        if value.startswith("/") and not value.startswith("//"):
            return self.prefix + value
        return value

    # -- handlers --------------------------------------------------------------
    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.method == "GET" and request.headers.get(hdrs.UPGRADE, "").lower() == "websocket":
            return await self.ws(request)
        return await self.http(request)

    async def http(self, request: web.Request) -> web.StreamResponse:
        url = self.upstream(request)
        try:
            async with asyncio.timeout(HEADERS_TIMEOUT_S):
                up = await self.session().request(
                    request.method, url, headers=self.request_headers(request, REQ_DROP),
                    data=request.content if request.body_exists else None, allow_redirects=False,
                    auto_decompress=False, skip_auto_headers=CLIENT_AUTO_HEADERS, timeout=STREAM_TIMEOUT)
        except TimeoutError:
            return _err(504, f"{self.code}_timeout", f"{self.name} did not answer {request.method} {url.path} in time")
        except aiohttp.ClientError as e:
            return _err(502, f"{self.code}_unreachable", f"{self.name} at {self.base()}: {type(e).__name__}")
        async with up:
            resp = web.StreamResponse(status=up.status, reason=up.reason)
            for k, v in _filtered(up.headers, RESP_DROP).items():
                resp.headers.add(k, self.location(v) if k.lower() == "location" else v)
            self.cors(request, resp.headers)
            timed = hdrs.CONTENT_LENGTH in up.headers
            try:
                await resp.prepare(request)
                while True:
                    if timed:
                        async with asyncio.timeout(IDLE_READ_S):
                            chunk = await up.content.readany()
                    else:
                        chunk = await up.content.readany()
                    if not chunk:
                        break
                    await resp.write(chunk)
                await resp.write_eof()
            except ConnectionResetError:
                pass                                    # the client went away; the upstream response is closed
            except (aiohttp.ClientError, TimeoutError) as e:
                # The upstream broke mid-body: the status is sent, so cut the client's connection (a truncated body
                # must not look complete).
                log.info("renderer proxy: %s %s cut mid-body (%s)", request.method, url.path, type(e).__name__)
                if request.transport is not None:
                    request.transport.close()
        return resp

    async def ws(self, request: web.Request) -> web.StreamResponse:
        url = self.upstream(request, ws=True)
        protocols = [p.strip() for v in request.headers.getall(hdrs.SEC_WEBSOCKET_PROTOCOL, ())
                     for p in v.split(",") if p.strip()]
        try:
            async with asyncio.timeout(WS_HANDSHAKE_S):
                up = await self.session().ws_connect(
                    url, headers=self.request_headers(request, WS_REQ_DROP), protocols=protocols, autoping=True,
                    heartbeat=None, compress=0, max_msg_size=0, timeout=WS_TIMEOUT)
        except aiohttp.WSServerHandshakeError as e:
            return _err(e.status or 502, "upstream_refused", f"stream_service answered the upgrade with {e.status}")
        except TimeoutError:
            return _err(504, f"{self.code}_timeout", f"{self.name} did not accept the socket {url.path} in time")
        except aiohttp.ClientError as e:
            return _err(502, f"{self.code}_unreachable", f"{self.name} at {self.base()}: {type(e).__name__}")
        ws = web.WebSocketResponse(protocols=(up.protocol,) if up.protocol else (), autoping=True, heartbeat=None,
                                   compress=False, max_msg_size=WS_CLIENT_MAX_MSG)
        try:
            await ws.prepare(request)
        except BaseException:
            await up.close()
            raise
        pair = (ws, up)
        self._sockets.add(pair)
        try:
            down_t = asyncio.create_task(self._pump(up, ws))     # stream_service -> client
            up_t = asyncio.create_task(self._pump(ws, up))       # client -> stream_service
            done, pending = await asyncio.wait({down_t, up_t}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            first = done.pop()
            code, reason = (None, "") if first.cancelled() or first.exception() else first.result()
            other = ws if first is down_t else up                 # the side that did not close first
            await other.close(code=_close_code(code), message=reason.encode()[:123])
        finally:
            self._sockets.discard(pair)
            await up.close()
            await ws.close()
        return ws

    @staticmethod
    async def _pump(src: Any, dst: Any) -> tuple[int | None, str]:
        """Relay data frames src -> dst until src closes; the close code and reason it gave (pings are autoping's)."""
        while True:
            msg = await src.receive()
            if msg.type is WSMsgType.TEXT:
                await dst.send_str(msg.data)
            elif msg.type is WSMsgType.BINARY:
                await dst.send_bytes(msg.data)
            elif msg.type is WSMsgType.CLOSE:
                return msg.data, msg.extra or ""
            elif msg.type in (WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                return src.close_code, ""

    # -- lifecycle -------------------------------------------------------------
    async def close_sockets(self, app: web.Application) -> None:
        """On shutdown: say 'going away' to proxied sockets so aiohttp's graceful stop doesn't wait on them."""
        for ws, up in list(self._sockets):
            await ws.close(code=1001)
            await up.close(code=1001)

    async def close(self, app: web.Application) -> None:
        if self._own is not None:
            await self._own.close()


def register(app: web.Application, svc: "RoomService") -> None:
    proxies = [RendererProxy(svc),
               RendererProxy(svc, "/show", lambda: str(getattr(svc.cfg, "show_url", "http://127.0.0.1:8703")),
                             "show_service")]
    for proxy in proxies:
        app.router.add_route("*", proxy.prefix + "/{tail:.*}", proxy.handle)
        app.on_shutdown.append(proxy.close_sockets)
        app.on_cleanup.append(proxy.close)
