"""Live controls for the Room page: the renderer's breadth, through room_service (LIVE_V1 1.1 §1.6-§1.11, FX bus).

The page never talks to stream_service; these routes pass its session-level API through for the screen's *current*
live session (found the way ScreenController.patch_live finds it). Open on the LAN like the other operate routes;
api.py's Host / Origin / CORS middlewares apply to them as to every route on the app.

  GET   /api/v1/library                 UI-ready catalog summary (scenes, transitions, effects, palettes, vocab)
  GET   /api/v1/thumb/{name}.png        a plug-in's thumbnail (stream_service /dev/thumb), small LRU, max-age 300
  GET   /api/v1/screens/{id}/look       the live session's look: scene, auto, direction, palette, fx, cue
  PATCH /api/v1/screens/{id}/look       {auto?, direction?, palette?, palette_transition_s?, direction_ease_s?,
                                         beat_follow?} -> PATCH /api/sessions/{sid}; `direction` is merged over the
                                         current one (the renderer replaces it whole); `auto: true|false` is short
                                         for {enabled: ...}
  POST  /api/v1/screens/{id}/cue        {to, transition?, at?} -> POST /api/sessions/{sid}/cue {to, via, at,
                                         source: "panel"}
  GET   /api/v1/screens/{id}/fx         the FX bus (GET /api/sessions/{sid}/fx)
  PUT   /api/v1/screens/{id}/fx         {stack, fade_s?, master?, if_rev?} -> PUT /api/sessions/{sid}/fx
  GET   /api/v1/screens/{id}/inputs     the show-bus monitor (GET /api/sessions/{sid}/inputs), read-only

Errors are {error, detail}: 404 unknown_screen, 409 not_live (no current session, or it has ended), 400 bad_request /
bad_name, 502 stream_unreachable; stream_service's own 4xx errors are relayed with their status.

A scene change on glass (a cue from here, or the renderer's auto director) is recorded in the screen's desired live
spec, as PATCH /live already does for a scene, so a re-entry, a parameter edit and the coordinator's readback see the
scene that is on screen. The library and /api/v1/catalog caches drop on stream_service's `catalog.changed`.
"""

from __future__ import annotations

import asyncio
import collections
import copy
import logging
import re
import time
from typing import TYPE_CHECKING, Any

import aiohttp
from aiohttp import web

from .screens import spec_name
from .stream_client import StreamError, StreamUnreachable

if TYPE_CHECKING:
    from .service import RoomService

log = logging.getLogger("room_service.live_api")

THUMB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
THUMB_LRU_MAX = 300                 # ~7 KB each
THUMB_MISS_S = 60.0                 # a plug-in with no thumbnail is not asked again for this long
THUMB_UPSTREAM = 4                  # concurrent /dev/thumb fetches (each may be a PIL crop of a big sheet)
LIBRARY_TTL_S = 30.0                # thumbnails appear as the fxtest harness writes them
LOOK_KEYS = {"auto", "direction", "palette", "palette_transition_s", "direction_ease_s", "beat_follow"}
CUE_KEYS = {"to", "transition", "at", "idempotency_key"}
FX_KEYS = {"stack", "fade_s", "master", "if_rev"}
LOOK_OUT = ("session", "state", "scene", "current_scene", "scene_params", "transitions", "auto", "direction",
            "palette", "palette_transition_s", "beat_follow", "masters", "fx", "cue", "current_scene_effects",
            "lead_ms", "direction_effective", "music_fields", "music")
IMPORTED_PREFIXES = ("glt_", "isf_", "st_")       # shader ports (stream_service/library.py origin_of)

# As built in stream_service/director.py (the catalog does not carry them).
DIRECTION = {
    "numbers": {"energy": "how lively the picks are", "pace": "scene speed (0.5x .. 2x)",
                "intensity": "how far music-driven motion swings", "change_rate": "how often auto changes scene"},
    "defaults": {"energy": 0.5, "pace": 0.5, "intensity": 1.0, "change_rate": 0.5, "moods": [], "genre": None,
                 "transition_style": None, "palette_family": None, "source": None, "note": None,
                 "fx": {"density": 0.5, "rate": 0.5, "stages": None, "moods": None}},
}
AUTO = {
    "defaults": {"enabled": True, "pool": "all", "transitions": "all", "dwell_s": None, "snap": "beat",
                 "follow_dmx": ["effect", "variant"], "avoid_repeat": 2, "seed": None,
                 "fx": {"enabled": False, "pool": "all", "max": 2}},
    "snap": ["now", "beat"], "follow_dmx": ["effect", "variant"], "fx_max": 4,
    "dwell_s": {"min": 2.0, "max": 3600.0, "by_change_rate": {"0": [120, 240], "0.5": [42, 85], "1": [15, 30]}},
    "pool_forms": ["all", "[preset names]", "{tags: {key: value}}"],
}


LIVE_API = web.AppKey("live_api", dict)


class _Fail(Exception):
    def __init__(self, status: int, code: str, detail: str = "") -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


def _err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def _relay(e: StreamError) -> _Fail:
    """A stream_service error for the browser: its 4xx as given; a vanished session is not_live."""
    if isinstance(e, StreamUnreachable):
        return _Fail(502, "stream_unreachable", e.detail)
    if e.status == 404:
        return _Fail(409, "not_live", "the live session has ended")
    if e.status == 401:                       # our stream token, not the browser's: never prompt the page
        return _Fail(502, "stream_unauthorized", "stream_service refused room_service's token")
    if e.status and 400 <= e.status < 600:
        return _Fail(e.status, e.code, e.detail)
    return _Fail(502, e.code or "stream_error", e.detail)


def _basic_props(schema: Any) -> dict:
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    return {k: p for k, p in props.items()
            if isinstance(p, dict) and isinstance(p.get("x-ui"), dict) and p["x-ui"].get("basic") is True}


def _origin(plugin: Any) -> str:
    return "imported" if isinstance(plugin, str) and plugin.startswith(IMPORTED_PREFIXES) else "core"


def _sort_key(item: dict) -> tuple:
    return (item["origin"] != "core", re.sub(r"^[^A-Za-z0-9]+", "", item["label"]).lower())


def _plain(v: Any) -> bool:
    return not (isinstance(v, dict) and ("bind" in v or "signal" in v))


def build_library(cat: dict, thumbs: dict[str, str] | None) -> dict:
    """The catalog, cut down to what a phone picker needs. `thumbs`: plug-in -> room thumb URL (None: guess)."""
    def thumb(plugin: Any) -> str | None:
        if not isinstance(plugin, str) or not THUMB_NAME_RE.match(plugin):
            return None
        if thumbs is None:
            return f"/api/v1/thumb/{plugin}.png"
        return thumbs.get(plugin)

    sources = {s["name"]: s for s in cat.get("sources") or [] if isinstance(s, dict) and s.get("name")}
    effects = {s["name"]: s for s in cat.get("effects") or [] if isinstance(s, dict) and s.get("name")}
    transitions = {s["name"]: s for s in cat.get("transitions") or [] if isinstance(s, dict) and s.get("name")}
    presets = cat.get("presets") or {}

    src_out = {}
    for name, s in sources.items():
        if s.get("internal"):
            continue
        schema = s.get("schema") or {}
        props = schema.get("properties") or {}
        # as built, x-palette marks the `palette` property (the proposal puts it at the schema's top level)
        pal = bool(schema.get("x-palette")) or any(isinstance(p, dict) and p.get("x-palette") for p in props.values())
        src_out[name] = {"label": s.get("label") or name, "description": s.get("description"),
                         "basic": _basic_props(schema), "palette": pal, "thumb": thumb(name)}

    scenes = []
    for name, p in (presets.get("scenes") or {}).items():
        src = p.get("source")
        if src not in src_out:
            continue
        tags = p.get("tags") or {}
        params = p.get("params") or {}
        basic = {k: params[k] for k in src_out[src]["basic"] if k in params and _plain(params[k])}
        scenes.append({"name": name, "label": p.get("label") or name, "source": src,
                       "tags": dict(tags),       # every facet (modes incl. the owner's Library tags, moods, ...)
                       "in_auto": bool(tags) and tags.get("auto", True) is not False,   # director.resolve_pool
                       "origin": _origin(src), "basic": basic, "thumb": src_out[src]["thumb"]})
    scenes.sort(key=_sort_key)

    trans = []
    for name, p in (presets.get("transitions") or {}).items():
        plugin = p.get("transition")
        cls = transitions.get(plugin) or {}
        params = p.get("params") or {}
        dur = params.get("duration_s")
        if dur is None:
            dur = (((cls.get("schema") or {}).get("properties") or {}).get("duration_s") or {}).get("default")
        tags = p.get("tags") or {}
        trans.append({"name": name, "label": p.get("label") or name, "transition": plugin,
                      "duration_s": dur, "out_ok": plugin == "crossfade" and isinstance(dur, (int, float)) and dur >= 0.5,
                      "tags": {k: tags[k] for k in ("transition_style", "energy", "family", "moods") if k in tags},
                      "origin": _origin(plugin), "thumb": thumb(plugin)})
    trans.sort(key=lambda x: (x["name"] != "cut", *_sort_key(x)))

    fx = []
    for name, p in (presets.get("effects") or {}).items():
        plugin = p.get("effect")
        cls = effects.get(plugin)
        if cls is None or cls.get("internal"):
            continue
        tags = p.get("tags") or {}
        fx.append({"name": name, "label": p.get("label") or name, "effect": plugin,
                   "stage": p.get("stage") or cls.get("stage"),
                   "tags": {k: tags[k] for k in ("family", "moods", "energy", "style") if k in tags},
                   "in_auto": bool(tags) and tags.get("auto", True) is not False,
                   "origin": _origin(plugin), "basic": list(_basic_props(cls.get("schema"))), "thumb": thumb(plugin)})
    fx.sort(key=_sort_key)

    pals = []
    for name, p in (cat.get("palettes") or {}).items():
        t = p.get("tags") or {}
        pals.append({"name": name, "label": p.get("label") or name, "swatch": p.get("swatch") or [],
                     "family": t.get("palette_family") or t.get("family"), "moods": t.get("moods") or []})

    def facet(items: list[dict], key: str) -> list[dict]:
        c: collections.Counter = collections.Counter()
        for it in items:
            v = it["tags"].get(key)
            for x in (v if isinstance(v, list) else [v] if isinstance(v, str) else []):
                c[x] += 1
        return [{"value": k, "count": n} for k, n in c.most_common()]

    api = cat.get("api") or {}
    plugins = cat.get("plugins") or {}
    return {
        "rev": f"{plugins.get('rev', '?')}.{len(scenes)}.{len(trans)}.{len(fx)}",
        "generated_at": time.time(),
        "features": api.get("features") or [],
        "counts": {"scenes": len(scenes), "transitions": len(trans), "effects": len(fx), "palettes": len(pals)},
        "sources": src_out,
        "scenes": scenes,
        "transitions": trans,
        "effects": fx,
        "palettes": pals,
        "stages": cat.get("stages") or [],
        "facets": {"scenes": {"family": facet(scenes, "family"), "moods": facet(scenes, "moods")},
                   "effects": {"stage": [{"value": k, "count": n} for k, n in
                                         collections.Counter(e["stage"] for e in fx if e["stage"]).most_common()]},
                   "transitions": {"transition_style": facet(trans, "transition_style")}},
        "vocab": cat.get("vocab") or {},
        "direction": DIRECTION,
        "auto": AUTO,
        "fx_bus": {"max_slots": 6, "page_max": 4, "fade_s": {"default": 2.0, "max": 30.0}},
        "thumbs": "library" if thumbs is not None else "guessed",
    }


def register(app: web.Application, svc: "RoomService") -> None:
    state: dict[str, Any] = {"library": None, "library_at": 0.0, "hooked": False}
    lib_lock = asyncio.Lock()
    thumbs: collections.OrderedDict[tuple[str, str], tuple[bytes, str]] = collections.OrderedDict()
    misses: dict[str, float] = {}
    upstream: dict[str, asyncio.Semaphore] = {}

    # -- caches and the stream events hook --------------------------------------------------
    def invalidate(why: str) -> None:
        state["library"] = None
        misses.clear()
        stream = getattr(svc, "stream", None)
        if stream is not None and hasattr(stream, "_catalog_at"):
            stream._catalog_at = float("-inf")         # /api/v1/catalog re-reads on its next call
        log.info("live catalog caches dropped (%s)", why)

    def on_event(evt: dict) -> None:
        typ = evt.get("type")
        if typ == "catalog.changed":
            invalidate(f"catalog.changed rev {evt.get('rev')}")
        elif typ == "session.transition" and evt.get("kind") == "scene" and evt.get("phase") == "started":
            follow_scene(evt)

    def on_conn(up: bool) -> None:
        if up:
            invalidate("stream_service (re)connected")

    async def hook(app_: web.Application) -> None:
        async def attach() -> None:
            for _ in range(1200):                      # RoomService.start() builds svc.stream after on_startup runs
                s = getattr(svc, "stream", None)
                if s is not None:
                    s.on_event(on_event)
                    s.on_connection(on_conn)
                    state["hooked"] = True
                    return
                await asyncio.sleep(0.05)
            log.warning("live_api: svc.stream never appeared; catalog.changed is not followed")

        state["task"] = asyncio.get_running_loop().create_task(attach())

    async def unhook(app_: web.Application) -> None:
        t = state.get("task")
        if t is not None:
            t.cancel()

    app.on_startup.append(hook)
    app.on_cleanup.append(unhook)

    # -- helpers ------------------------------------------------------------------------------
    def controller(request: web.Request):
        sid = request.match_info["id"]
        screens = getattr(svc, "screens", None)
        c = screens.get(sid) if screens is not None else None
        if c is None:
            raise _Fail(404, "unknown_screen", sid)
        return c

    def live_session(c, strict: bool = True) -> str:
        """The screen's current live session id, as ScreenController.patch_live decides it."""
        sess = getattr(c, "session", None)
        busy = strict and getattr(c, "_handshake", None) is not None
        if not sess or not sess.get("id") or getattr(c, "layer", None) != "live" or c.desired == "art" or busy:
            raise _Fail(409, "not_live", f"{c.id} is not live" + (" (a cut is under way)" if busy and sess else ""))
        return str(sess["id"])

    async def call(method: str, path: str, body: Any = None) -> Any:
        try:
            return await svc.stream._req(method, path, body)
        except StreamError as e:
            raise _relay(e) from None

    async def read_body(request: web.Request) -> dict:
        try:
            b = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise _Fail(400, "bad_json", "body is not JSON") from None
        if not isinstance(b, dict):
            raise _Fail(400, "bad_request", "body must be a JSON object")
        return b

    def scene_spec_for(name: str) -> Any:
        cat = svc.stream.cached_catalog() or {}
        scenes = (cat.get("presets") or {}).get("scenes") or {}
        return name if name in scenes or not scenes else {"source": name}

    def record_scene(c, sid: str, spec: Any) -> bool:
        """The screen now shows `spec` (a cue or the auto director): record it as PATCH /live does for a scene.
        Left alone while a change of desired is still on its way (desired != what the session was given)."""
        sess, d = getattr(c, "session", None), c.desired
        if not sess or sess.get("id") != sid or not isinstance(d, dict) or not isinstance(d.get("live"), dict):
            return False
        if sess.get("live") is not None and sess.get("live") != d["live"]:
            return False
        if getattr(c, "_handshake", None) is not None:
            return False
        live = copy.deepcopy(d["live"])
        live["scene"] = copy.deepcopy(spec)
        live.pop("params", None)
        if live == d["live"]:
            return False
        c.desired = {"live": live}
        sess["live"] = copy.deepcopy(live)
        try:
            c._changed()
        except Exception:                               # persistence trouble must not break the cue
            log.exception("recording %s's scene failed", c.id)
        return True

    def follow_scene(evt: dict) -> None:
        screens = getattr(svc, "screens", None)
        c = screens.get(str(evt.get("screen"))) if screens is not None else None
        name = evt.get("scene")
        if c is None or not isinstance(name, str) or not name or evt.get("cause") == "patch":
            return                                      # PATCH /live records its own scene
        d = c.desired
        if isinstance(d, dict) and isinstance(d.get("live"), dict) and spec_name(d["live"].get("scene")) == name:
            return                                      # already recorded (e.g. by the cue that started it)
        if record_scene(c, str(evt.get("session")), scene_spec_for(name)):
            log.info("%s: scene on glass is now %s (%s); desired follows", c.id, name, evt.get("cause") or "?")

    def look_view(c, summ: dict | None) -> dict:
        summ = summ or {}
        return {"screen": c.id, **{k: summ.get(k) for k in LOOK_OUT}}

    def handler(fn):
        async def wrapped(request: web.Request) -> web.StreamResponse:
            try:
                return await fn(request)
            except _Fail as e:
                return _err(e.status, e.code, e.detail)
        return wrapped

    # -- library ------------------------------------------------------------------------------
    async def thumb_index() -> dict[str, str] | None:
        """plug-in -> /api/v1/thumb/<name>.png?v=<mtime> from stream_service's dev library; None if it has none."""
        try:
            lib = await svc.stream._req("GET", "/dev/library", timeout=10.0)
        except StreamError as e:
            log.info("no /dev/library from stream_service (%s); thumbnails are guessed", e)
            return None
        out: dict[str, str] = {}
        for it in (lib or {}).get("items") or []:
            name, t = it.get("name"), it.get("thumb")
            if isinstance(name, str) and isinstance(t, str) and THUMB_NAME_RE.match(name):
                v = t.split("?v=", 1)[1] if "?v=" in t else ""
                out[name] = f"/api/v1/thumb/{name}.png" + (f"?v={v}" if v.isdigit() else "")
        return out

    @handler
    async def library(request: web.Request) -> web.StreamResponse:
        async with lib_lock:
            lib = state["library"]
            if lib is None or time.monotonic() - state["library_at"] > LIBRARY_TTL_S:
                try:
                    cat = await svc.stream.catalog(max_age_s=LIBRARY_TTL_S)
                except StreamError as e:
                    raise _relay(e) from None
                lib = await asyncio.get_running_loop().run_in_executor(None, build_library, cat, await thumb_index())
                state["library"], state["library_at"] = lib, time.monotonic()
        resp = web.json_response(lib, headers={"Cache-Control": "no-cache"})
        resp.enable_compression()
        return resp

    @handler
    async def thumb(request: web.Request) -> web.StreamResponse:
        name = request.match_info["name"]
        if not THUMB_NAME_RE.match(name):
            raise _Fail(400, "bad_name", "a thumbnail name is a plug-in name: letters, digits, _ and -")
        v = request.query.get("v", "")
        if v and not v.isdigit():
            raise _Fail(400, "bad_name", "v must be digits")
        key = (name, v)
        hit = thumbs.get(key)
        if hit is None:
            if time.monotonic() - misses.get(name, -1e9) < THUMB_MISS_S:
                raise _Fail(404, "no_thumb", name)
            sem = upstream.setdefault("sem", asyncio.Semaphore(THUMB_UPSTREAM))
            async with sem:
                hit = thumbs.get(key)
                if hit is None:
                    hit = await fetch_thumb(name)
                    if hit is None:
                        misses[name] = time.monotonic()
                        raise _Fail(404, "no_thumb", name)
                    thumbs[key] = hit
                    while len(thumbs) > THUMB_LRU_MAX:
                        thumbs.popitem(last=False)
        thumbs.move_to_end(key)
        body, ctype = hit
        return web.Response(body=body, content_type=ctype, headers={"Cache-Control": "max-age=300"})

    async def fetch_thumb(name: str) -> tuple[bytes, str] | None:
        stream = svc.stream
        tok = stream._get_token()
        if not tok:
            raise _Fail(502, "stream_unreachable", "no stream_service token")
        try:
            async with stream.http.get(f"{stream.url}/dev/thumb/{name}.png",
                                       headers={"Authorization": f"Bearer {tok}"},
                                       timeout=aiohttp.ClientTimeout(total=10.0)) as r:
                if r.status == 404:
                    return None
                if r.status != 200:
                    raise _Fail(502, "stream_error", f"/dev/thumb answered HTTP {r.status}")
                data = await r.read()
                ctype = (r.content_type or "image/jpeg").split(";")[0]
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise _Fail(502, "stream_unreachable", type(e).__name__) from None
        if not ctype.startswith("image/") or len(data) > 2 * 1024 * 1024:
            raise _Fail(502, "stream_error", "not a thumbnail")
        return data, ctype

    # -- the live session ---------------------------------------------------------------------
    @handler
    async def get_look(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        sid = live_session(c, strict=False)
        return web.json_response(look_view(c, await call("GET", f"/api/sessions/{sid}")))

    @handler
    async def patch_look(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        body = await read_body(request)
        # `source`: who asked (show_service sends "show_service"; the page "web" by default). Never sent to the
        # renderer; it rides on the room's screen.look event, so the show's director can tell a hand pick (which holds)
        # from its own palette changes (c8, 16 Oct).
        source = body.pop("source", None) if isinstance(body, dict) else None
        # `scope`: "screen" = this session only, never mirrored to the room (a control on a screen card); "room" or
        # absent = the renderer's room rule (a room-level control). Passed through to the renderer (04, 17 Oct).
        scope = body.pop("scope", None) if isinstance(body, dict) else None
        if scope is not None and scope not in ("screen", "room"):
            raise _Fail(400, "bad_scope", "scope is screen or room")
        if source is not None and not (isinstance(source, str) and 0 < len(source) <= 64):
            raise _Fail(400, "bad_source", "source is a short string")
        if not body or set(body) - LOOK_KEYS:
            raise _Fail(400, "bad_request", f"body may contain only {sorted(LOOK_KEYS | {'source', 'scope'})}")
        sid = live_session(c)
        out = dict(body)
        if isinstance(out.get("auto"), bool):
            out["auto"] = {"enabled": out["auto"]}
        if isinstance(out.get("direction"), dict):
            cur = (await call("GET", f"/api/sessions/{sid}") or {}).get("direction") or {}
            merged = {**cur, **out["direction"]}
            if isinstance(cur.get("fx"), dict) and isinstance(out["direction"].get("fx"), dict):
                merged["fx"] = {**cur["fx"], **out["direction"]["fx"]}
            out["direction"] = merged
        if scope is not None:
            out["scope"] = scope
        summ = await call("PATCH", f"/api/sessions/{sid}", out)
        evt = {"screen": c.id, "session": sid, "source": source or "web", "changed": sorted(body)}
        if scope is not None:
            evt["scope"] = scope
        if "palette" in body:
            evt["palette"] = body["palette"]
        events = getattr(svc, "events", None)
        if events is not None:
            events.emit("screen.look", evt)
        return web.json_response(look_view(c, summ))

    @handler
    async def cue(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        body = await read_body(request)
        if set(body) - CUE_KEYS:
            raise _Fail(400, "bad_request", f"body may contain only {sorted(CUE_KEYS)}")
        to = body.get("to")
        if not (isinstance(to, str) and to) and not isinstance(to, dict):
            raise _Fail(400, "bad_to", "to is a scene preset name, a scene spec or \"auto\"")
        sid = live_session(c)
        out: dict[str, Any] = {"to": to, "source": "panel"}
        tr = body.get("transition")
        if tr not in (None, "", "default"):
            out["via"] = tr
        for k in ("at", "idempotency_key"):
            if body.get(k) is not None:
                out[k] = body[k]
        res = await call("POST", f"/api/sessions/{sid}/cue", out) or {}
        recorded = False
        if to != "auto" and not res.get("skipped"):
            recorded = record_scene(c, sid, to)
        return web.json_response({"screen": c.id, **res, "recorded": recorded}, status=202)

    @handler
    async def get_fx(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        sid = live_session(c, strict=False)
        return web.json_response({"screen": c.id, **(await call("GET", f"/api/sessions/{sid}/fx") or {})})

    @handler
    async def put_fx(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        body = await read_body(request)
        if set(body) - FX_KEYS or not isinstance(body.get("stack"), list):
            raise _Fail(400, "bad_request", f"body is {{stack: [...], {', '.join(sorted(FX_KEYS - {'stack'}))}?}}")
        sid = live_session(c)
        return web.json_response({"screen": c.id, **(await call("PUT", f"/api/sessions/{sid}/fx", body) or {})})

    @handler
    async def get_inputs(request: web.Request) -> web.StreamResponse:
        c = controller(request)
        sid = live_session(c, strict=False)
        return web.json_response({"screen": c.id, **(await call("GET", f"/api/sessions/{sid}/inputs") or {})})

    r = app.router
    r.add_get("/api/v1/library", library)
    r.add_get("/api/v1/thumb/{name}.png", thumb)
    r.add_get("/api/v1/screens/{id}/look", get_look)
    r.add_patch("/api/v1/screens/{id}/look", patch_look)
    r.add_post("/api/v1/screens/{id}/cue", cue)
    r.add_get("/api/v1/screens/{id}/fx", get_fx)
    r.add_put("/api/v1/screens/{id}/fx", put_fx)
    r.add_get("/api/v1/screens/{id}/inputs", get_inputs)
    app[LIVE_API] = {"invalidate": invalidate, "state": state, "thumbs": thumbs}   # for tests / diagnostics
