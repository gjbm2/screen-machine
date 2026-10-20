"""/api/v1/room/*: the room state model's editor and operator calls (ROOM_STATE_MODEL §7.3, aa's editor contract).

Auth: since the owner's ruling of 14 Oct ("no auth anywhere in the AV stack", api.py token_valid) every call here is
open. The split is kept, so admin gating can come back if the owner asks: ROOM_ADMIN_ROUTES and the is_admin checks
for `hold` and draft plans would be enforced again by api.py's token check.
Errors: {"error", "detail"}, plus "errors" ([{path, message}]) for an invalid config.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from .room_config import ConfigConflict, ConfigInvalid
from .roomstate.coordinator import Refused
from .roomstate.model import ConfigError, RoomConfig

if TYPE_CHECKING:
    from .service import RoomService

ROOM_ADMIN_ROUTES = frozenset({
    "/api/v1/room/config", "/api/v1/room/config/validate", "/api/v1/room/assume",
    "/api/v1/room/stored/{slot}/{target}", "/api/v1/room/stored/{slot}", "/api/v1/room/stored/{slot}/{target}/restore",
})


def err(status: int, code: str, detail: str = "", **extra: Any) -> web.Response:
    return web.json_response({"error": code, "detail": detail, **extra}, status=status)


def refused(e: Refused) -> web.Response:
    return err(400 if e.code == "bad_source" else 409, e.code, str(e))


def register(app: web.Application, svc: "RoomService", is_admin: Callable[[web.Request], bool]) -> None:
    async def body(request: web.Request) -> dict:
        try:
            b = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise web.HTTPBadRequest(text=json.dumps({"error": "bad_json", "detail": "body is not JSON"}),
                                     content_type="application/json")
        if not isinstance(b, dict):
            raise web.HTTPBadRequest(text=json.dumps({"error": "bad_request", "detail": "body must be an object"}),
                                     content_type="application/json")
        return b

    def admin_only() -> web.Response:
        return err(401, "unauthorized", "this call needs the room admin token")

    async def describe(request: web.Request) -> web.Response:
        try:                                   # the screens schema's vocab comes from the renderer's catalog (cached 30 s)
            await svc.stream.catalog()
        except Exception:
            pass                               # the renderer is away: the enums stay empty, the rest is served
        return web.json_response(svc.coordinator.describe(), dumps=lambda o: json.dumps(o, default=str))

    async def plan(request: web.Request) -> web.Response:
        b = await body(request)
        draft = None
        if "config" in b:
            if not is_admin(request):
                return admin_only()
            check = svc.room_config.check(b["config"])
            if not check["ok"]:
                return err(400, "invalid_config", "the draft has errors", errors=check["errors"])
            draft = RoomConfig(b["config"], svc.room_config.adapters, extra_groups=svc.room_config.groups)
        try:
            p = await svc.coordinator.plan(str(b.get("mode", "")), refresh=bool(b.get("refresh", True)), config=draft)
        except ConfigError as e:
            return err(404, "unknown_mode", str(e))
        return web.json_response(p, dumps=lambda o: json.dumps(o, default=str))

    async def put_targets(request: web.Request) -> web.Response:
        b = await body(request)
        values, hold = b.get("values"), bool(b.get("hold", False))
        if not isinstance(values, dict) or not values:
            return err(400, "bad_request", "body must be {values: {target: value}, hold?}")
        if hold and not is_admin(request):
            return admin_only()
        accepted = []
        for t, v in values.items():
            try:
                await svc.coordinator.hand_set(t, v, hold=hold, source=str(b.get("source", "web")))
            except KeyError:
                return err(404, "unknown_target", t, accepted=accepted)
            except ValueError as e:
                return err(400, "bad_value", f"{t}: {e}", accepted=accepted)
            except Refused as e:
                return refused(e)
            accepted.append(t)
        return web.json_response({"accepted": accepted}, status=202)

    async def assume(request: web.Request) -> web.Response:
        b = await body(request)
        try:
            svc.coordinator.assume(str(b.get("target")), b.get("value"), cause=str(b.get("cause", "owner (web)")))
        except KeyError:
            return err(404, "unknown_target", str(b.get("target")))
        except ValueError as e:
            return err(400, "bad_value", str(e))
        except Refused as e:
            return refused(e)
        return web.json_response({"target": b.get("target"), "value": b.get("value")})

    async def get_config(request: web.Request) -> web.Response:
        rc = svc.room_config
        return web.json_response({"config": rc.raw, "revision": rc.revision, "path": str(rc.path), "error": rc.error})

    async def validate_config(request: web.Request) -> web.Response:
        b = await body(request)
        return web.json_response(svc.room_config.check(b.get("config")))

    async def put_config(request: web.Request) -> web.Response:
        b = await body(request)
        if "base_revision" not in b:
            return err(428, "revision_required", "send the revision you edited (GET /api/v1/room/config)")
        try:
            modes = svc.room_config.save(b.get("config"), b["base_revision"])
        except ConfigInvalid as e:
            return err(400, "invalid_config", str(e), errors=e.errors)
        except ConfigConflict as e:
            return err(409, "revision_conflict", str(e), current_revision=e.current)
        out = svc.coordinator.reload(modes, cause=str(b.get("note") or "saved from the Room page"))
        svc.events.log_event("info", f"modes.json saved (revision {modes.revision})")
        svc.events.mark_dirty()
        return web.json_response({"revision": modes.revision, **out})

    async def stored_edit(request: web.Request) -> web.Response:
        b = await body(request)
        slot, t = request.match_info["slot"], request.match_info["target"]
        try:
            svc.coordinator.stored_edit(slot, t, b.get("value"), cause=str(b.get("cause", "owner (web)")))
        except KeyError:
            return err(404, "unknown_target", t)
        except ValueError as e:
            return err(400, "bad_value", str(e))
        except Refused as e:
            return err(404, e.code, str(e)) if e.code == "unknown_stored" else refused(e)
        return web.json_response({"slot": slot, "target": t, "value": b.get("value")})

    async def stored_forget(request: web.Request) -> web.Response:
        slot, t = request.match_info["slot"], request.match_info.get("target")
        try:
            svc.coordinator.stored_forget(slot, t, cause="owner (web)")
        except Refused as e:
            return refused(e)
        return web.json_response({"slot": slot, "target": t})

    async def stored_restore(request: web.Request) -> web.Response:
        slot, t = request.match_info["slot"], request.match_info["target"]
        try:
            out = await svc.coordinator.stored_restore(slot, t, cause="owner (web)")
        except Refused as e:
            return err(404, e.code, str(e)) if e.code == "unknown_stored" else refused(e)
        return web.json_response({"slot": slot, "target": t, "status": out.status, "detail": out.detail})

    r = app.router
    r.add_get("/api/v1/room/describe", describe)
    r.add_post("/api/v1/room/plan", plan)
    r.add_put("/api/v1/room/targets", put_targets)
    r.add_post("/api/v1/room/assume", assume)
    r.add_get("/api/v1/room/config", get_config)
    r.add_put("/api/v1/room/config", put_config)
    r.add_post("/api/v1/room/config/validate", validate_config)
    r.add_put("/api/v1/room/stored/{slot}/{target}", stored_edit)
    r.add_delete("/api/v1/room/stored/{slot}/{target}", stored_forget)
    r.add_delete("/api/v1/room/stored/{slot}", stored_forget)
    r.add_post("/api/v1/room/stored/{slot}/{target}/restore", stored_restore)
