"""room_service HTTP API (LIVE_V1 §3) and the aiohttp application.

Auth follows the owner's ruling in LIVE_V1 §3 ("operate open, edit gated"):

* **Open on the LAN, no token:** status, modes, catalog, snapshots, `PUT /api/v1/mode`,
  `PUT /api/v1/screens/{id}`, `PATCH /api/v1/screens/{id}/live`, the events WS, `/healthz`.
* **Admin token** (`Authorization: Bearer <room token>`) for anything that edits config: the
  `/api/v1/room/*` editor routes in room_api.py (`ADMIN_ROUTES`; `hold`, `assume` and draft plans
  are checked in their handlers).
* Clients that send the token keep working; a token that is sent but wrong is a 401, so
  a stale saved token is noticed rather than silently ignored. `?token=` is read only on
  the events WS and the snapshot PNGs (an <img> can't send a header).
* **Browser protection:** the `Host` header must name an allowed host (`allowed_hosts`,
  port ignored; defends against DNS rebinding), and on `/api/*` a request that carries an
  `Origin` must come from an allowed web origin or room_service's own origin. Requests
  without an Origin (curl, scripts on the LAN) are allowed.
* CORS for the configured web origins, with OPTIONS preflight.
* `/v1/screen/<id>` is the display pages' control socket (LIVE_V1 §2). Its Origin is the
  kiosk page's own (often the WAN address), so it isn't origin-checked; kiosk/lab clients
  need the screen's control key once one is configured (pages.py).
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING, Any

from aiohttp import web
from aiohttp.abc import AbstractAccessLogger

from . import __version__, room_api
from .mask_source import handle_ingest
from .roomstate.coordinator import Refused
from .roomstate.model import ConfigError
from .screens import normalize_desired
from .stream_client import StreamError, StreamUnreachable

if TYPE_CHECKING:
    from .service import RoomService

log = logging.getLogger("room_service.api")
access_log = logging.getLogger("room_service.access")

QUERY_TOKEN_PATHS = ("/api/v1/events",)
# Canonical paths of routes that need the admin token (config and stored-value edits).
ADMIN_ROUTES: frozenset[str] = room_api.ROOM_ADMIN_ROUTES
LIVE_PATCH_KEYS = {"scene", "params", "transition"}
MAX_FOR_S = 24 * 3600                       # PUT /screens/{id} {desired, for_s}: at most a day


def err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


class SafeAccessLogger(AbstractAccessLogger):
    """Access log without query strings (they can carry ?token=)."""

    def log(self, request: web.BaseRequest, response: web.StreamResponse, time: float) -> None:
        if request.path == "/healthz":
            return
        self.logger.info("%s %s %s %s %.1fms", request.remote, request.method, request.path,
                         response.status, time * 1000)


def build_app(svc: "RoomService") -> web.Application:
    cfg = svc.cfg

    def presented_token(request: web.Request) -> str | None:
        auth = request.headers.get("Authorization")
        if auth is not None:
            return auth[7:].strip() if auth.startswith("Bearer ") else ""
        if request.path in QUERY_TOKEN_PATHS or (request.path.endswith(".png") and request.method == "GET"):
            return request.query.get("token")
        return None

    def token_valid(tok: str | None) -> bool:
        # Owner ruling, 14 Oct: no auth anywhere in the AV stack (LAN test rig). Everything is open; a token a
        # client still sends is ignored. Admin permissioning can come back later if the owner asks for it.
        return True

    def host_ok(request: web.Request) -> bool:
        host = (request.headers.get("Host") or "").strip().lower()
        if host.startswith("["):                       # [::1]:3001
            name = host[1:].split("]", 1)[0]
        else:
            name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return name in cfg.allowed_hosts

    def origin_ok(request: web.Request, origin: str) -> bool:
        if origin in cfg.web_origins:
            return True
        # room_service's own origin (e.g. a panel served from this port later, AV_PROGRAMME §5).
        return origin.lower() == f"{request.scheme}://{request.host}".lower()

    @web.middleware
    async def guard_mw(request: web.Request, handler: Any) -> web.StreamResponse:
        if not host_ok(request):
            return err(403, "host_not_allowed", "the Host header must name this server (LIVE_V1 §3)")
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin")
            if origin is not None and not origin_ok(request, origin):
                return err(403, "origin_not_allowed", origin)
            tok = presented_token(request)
            if tok is not None and not token_valid(tok):
                return err(401, "unauthorized", "wrong bearer token")
            route = request.match_info.route.resource
            if (route is not None and route.canonical in ADMIN_ROUTES) and not token_valid(tok):
                return err(401, "unauthorized", "this call needs the room admin token")
        return await handler(request)

    @web.middleware
    async def cors_mw(request: web.Request, handler: Any) -> web.StreamResponse:
        origin = request.headers.get("Origin")
        # /healthz answers any page: the app's home page probes it to show "Room controls" only on the LAN.
        allowed = origin is not None and (origin in cfg.web_origins or request.path == "/healthz")
        if request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
            if not allowed:
                return err(403, "origin_not_allowed", origin or "")
            hdrs = {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "GET, PUT, PATCH, POST, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin"}
            if "Access-Control-Request-Private-Network" in request.headers:
                hdrs["Access-Control-Allow-Private-Network"] = "true"   # Chrome PNA: public page -> LAN door
            return web.Response(status=204, headers=hdrs)
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
        if allowed and not resp.prepared:
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
        if isinstance(resp, web.HTTPException):
            raise resp
        return resp

    async def read_json(request: web.Request) -> Any:
        try:
            return await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "bad_json", "detail": "body is not JSON"}),
                                     content_type="application/json")

    # -- handlers ------------------------------------------------------------
    async def healthz(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": __version__})

    async def status(request: web.Request) -> web.Response:
        return web.json_response(svc.status(), dumps=lambda o: json.dumps(o, default=str))

    async def modes(request: web.Request) -> web.Response:
        return web.json_response(svc.modes_view(), dumps=lambda o: json.dumps(o, default=str))

    async def put_mode(request: web.Request) -> web.Response:
        body = await read_json(request)
        name = body.get("mode") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name:
            return err(400, "bad_request", "body must be {mode: <name>}")
        skip, expect = body.get("skip"), body.get("expect")
        def skip_item(x) -> bool:
            return isinstance(x, str) or (isinstance(x, dict) and bool(x) and all(
                isinstance(k, str) and isinstance(v, dict) and set(v) == {"from"} for k, v in x.items()))
        if (skip is not None and not (isinstance(skip, list) and all(skip_item(x) for x in skip))) or (
                expect is not None and not isinstance(expect, dict)):
            return err(400, "bad_request", 'skip is a list of selectors or {selector: {"from": value}}; '
                                           "expect is {current?, last_transition_id?}")
        try:
            tid = await svc.coordinator.request_mode(name, source=str(body.get("source", "web")),
                                                     reason=body.get("reason"), expect=expect, skip=skip)
        except ConfigError:
            return err(404, "unknown_mode", name)
        except ValueError as e:
            return err(400, "bad_skip", str(e))
        except Refused as e:
            return room_api.refused(e)
        tr = svc.coordinator.s["transition"] or {}
        return web.json_response({"transition_id": tid, "reapply": bool(tr.get("reapply"))}, status=202)

    def screen_or_404(request: web.Request):
        sid = request.match_info["id"]
        c = svc.screens.get(sid)
        if c is None:
            raise web.HTTPNotFound(text=json.dumps({"error": "unknown_screen", "detail": sid}),
                                   content_type="application/json")
        return c

    async def put_screen(request: web.Request) -> web.Response:
        c = screen_or_404(request)
        body = await read_json(request)
        if not isinstance(body, dict) or "desired" not in body:
            return err(400, "bad_request", "body must be {desired: 'art' | {live: {...}}}")
        try:
            d = normalize_desired(body["desired"])
        except ValueError as e:
            return err(400, "bad_desired", str(e))
        if d != "art" and not c.cfg.live_enabled:
            return err(409, "live_disabled", f"live is disabled for {c.id}")
        # for_s: after that many seconds, if desired is still this value, it goes back to what it was before
        # (server-side, persisted: a closed tab never leaves a TV live). A PUT without for_s cancels the timer.
        for_s = body.get("for_s")
        if for_s is not None and (isinstance(for_s, bool) or not isinstance(for_s, (int, float))
                                  or not math.isfinite(for_s) or not 0 < for_s <= MAX_FOR_S):
            return err(400, "bad_for_s", f"for_s must be a number of seconds, 0 < for_s <= {MAX_FOR_S}")
        # A timed change over a timed change still goes back to the value from before the first one.
        revert = c.expiry["revert"] if c.expiry else c.desired
        try:                                       # a change by hand, under the current mode's policy (§4.11)
            await svc.coordinator.hand_set(f"screens.{c.id}", d, source="web")
        except ValueError as e:
            return err(400, "bad_desired", str(e))
        except Refused as e:
            return room_api.refused(e)
        if for_s is None:
            c.cancel_expiry("a PUT without for_s")
            return web.json_response({"screen": c.id, "desired": d}, status=202)
        c.set_expiry(d, float(for_s), revert)
        return web.json_response({"screen": c.id, "desired": d, "for_s": for_s,
                                  "desired_expires_in_s": c.expires_in_s(), "desired_expires_to": revert},
                                 status=202)

    async def patch_live(request: web.Request) -> web.Response:
        c = screen_or_404(request)
        body = await read_json(request)
        if not isinstance(body, dict) or not body or set(body) - LIVE_PATCH_KEYS:
            return err(400, "bad_request", f"body may contain only {sorted(LIVE_PATCH_KEYS)}")
        try:
            res = await c.patch_live(body)
        except StreamError as e:
            if e.code == "not_live":
                return err(409, "not_live", e.detail)
            if isinstance(e, StreamUnreachable):
                return err(502, "stream_unreachable", e.detail)
            return err(e.status if e.status and 400 <= e.status < 500 else 502, e.code, e.detail)
        svc.events.mark_dirty()                    # reconcile reads the new spec back; drift under respect
        return web.json_response({"screen": c.id, "desired": c.desired, "result": res})

    async def catalog(request: web.Request) -> web.Response:
        try:
            cat = await svc.stream.catalog(max_age_s=5.0)
        except StreamError as e:
            return err(502, "stream_unreachable" if isinstance(e, StreamUnreachable) else e.code, e.detail)
        return web.json_response(cat)

    async def snapshot_png(request: web.Request) -> web.Response:
        c = screen_or_404(request)
        kind = request.match_info["kind"]
        png = c.anchor_png if kind == "anchor" else c.target_png
        if png is None:
            return err(404, "no_snapshot", f"no {kind} snapshot for {c.id} yet")
        return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "no-store"})

    async def events_ws(request: web.Request) -> web.StreamResponse:
        return await svc.events.handle(request)

    async def page_ws(request: web.Request) -> web.StreamResponse:
        return await svc.pages.handle(request)

    async def ingest_ws(request: web.Request) -> web.StreamResponse:
        return await handle_ingest(svc.mask, request)      # LIVE_V1 §2.2b; key-gated (mask_source.py)

    app = web.Application(middlewares=[cors_mw, guard_mw], client_max_size=1024 * 1024)
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/api/v1/modes", modes)
    app.router.add_put("/api/v1/mode", put_mode)
    app.router.add_put("/api/v1/screens/{id}", put_screen)
    app.router.add_patch("/api/v1/screens/{id}/live", patch_live)
    app.router.add_get("/api/v1/catalog", catalog)
    app.router.add_get(r"/api/v1/screens/{id}/snapshots/{kind:(anchor|target)}.png", snapshot_png)
    app.router.add_get("/api/v1/events", events_ws)
    app.router.add_get("/v1/screen/{screen}", page_ws)
    app.router.add_get("/v1/ingest/lux", ingest_ws)
    room_api.register(app, svc, lambda r: token_valid(presented_token(r)))   # /api/v1/room/* (ROOM_STATE_MODEL §7.3)
    from . import live_api; live_api.register(app, svc)   # noqa: E702  live controls: library, cue, look, fx
    from . import live_art; live_art.register(app, svc)   # noqa: E702  /api/v1/screens/{id}/art.png (live_art scene)
    from . import remotes_api; remotes_api.register(app, svc)   # noqa: E702  RM4 Pro remotes: learn, test, reset
    from . import renderer_proxy; renderer_proxy.register(app, svc)   # noqa: E702  /renderer/* -> stream_service (HTTP+WS)
    from .dmx import api as dmx_api; dmx_api.register(app, svc, lambda r: token_valid(presented_token(r)))  # noqa: E702
    return app
