"""aiohttp app: REST API, events WebSocket, media WebSockets and the dev page (LIVE_V1 §1).

No password (owner, 14 Oct).  With --local-control, /api/* and /dev* answer only this machine (the app's /renderer/
pass-through); /healthz, /media/* and /static/* stay open.  A media socket takes its session's ``?k=<key>`` (wrong
key -> close 4403).

Additive (LIVE_V1 1.1): POST /api/sessions/{id}/cue, GET /api/sessions/{id}/inputs, the catalog's api / vocab /
palettes / signals / errors blocks, the show-bus client task (--show-bus) and, dev only, GET /dev/art/{screen}.png:
the screen's current artwork (Flask's output/<screen>.jpg, read-only) letterboxed to a profile's size.
"""
from __future__ import annotations

import asyncio
import hmac
import io
import logging
import pathlib
import time
from typing import Any, Optional, Sequence

from aiohttp import web

from . import __version__, binding, mappings as mappings_mod, palettes
from .bodies import parse_body
from .events import EventHub
from .library import Library
from .plugins import Registry
from .plugins.base import STAGES, ParamError
from .presets import Presets
from .preview import PreviewError, Previews
from .ratings import Ratings
from .profiles import PROFILES
from .inputs import ShowInputs, signal_catalog
from .sessions import SessionError, SessionManager

log = logging.getLogger("stream_service.server")

STATIC = pathlib.Path(__file__).resolve().parent / "static"
MAX_BODY = 64 << 20

MANAGER = web.AppKey("manager", SessionManager)
EVENTS = web.AppKey("events", EventHub)
PRESETS = web.AppKey("presets", Presets)
REGISTRY = web.AppKey("registry", Registry)
TASKS = web.AppKey("tasks", list)
ART_DIR = web.AppKey("art_dir", object)
SHOW_BUS = web.AppKey("show_bus", object)
LIBRARY = web.AppKey("library", Library)
PREVIEWS = web.AppKey("previews", Previews)
RATINGS = web.AppKey("ratings", Ratings)
RESCAN_S = 20.0                     # live plug-in discovery: new / changed / removed plug-in modules
DEV_ART_SCREENS = ("north-screen", "south-screen", "devtest")


FEATURES = ("cue", "auto", "direction", "palette", "inputs", "bindings", "effects", "fx_bus", "fx_auto",
            "plugin_errors", "gpu_guard", "music_direction", "rooms", "mappings")
HTTP_ERROR_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "too_large"}


def err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def _guarded(path: str) -> bool:
    """Routes with JSON errors: the API and the dev actions."""
    return path.startswith("/api/") or path in ("/dev/rescan", "/dev/lab/rescan", "/dev/preview", "/dev/rate")


LOCAL_CONTROL = web.AppKey("local_control", bool)
APP_URL = web.AppKey("app_url", str)      # --app-url: where the renderer's console lives in the app (deploy/sm-stream.env)
_LOOPBACK = ("127.0.0.1", "::1")


def _control_path(path: str) -> bool:
    """Control routes (/api/*, /dev*). /healthz, /media/* (the TVs' and previews' video) and /static/* stay open."""
    return path.startswith("/api/") or path == "/dev" or path.startswith("/dev/")


@web.middleware
async def auth_and_errors(request: web.Request, handler):
    path = request.path
    # The console page itself always opens through the app (it needs room's API on its own origin), even from this
    # machine, e.g. http://localhost:8702/dev in a browser on the PC.  The app's pass-through marks its requests.
    app_url = request.app.get(APP_URL, "")
    if app_url and path in ("/dev", "/dev/") and request.method in ("GET", "HEAD") \
            and "X-Forwarded-Prefix" not in request.headers:
        raise web.HTTPFound(app_url + "/dev" + (f"?{request.query_string}" if request.query_string else ""))
    # --local-control: control only from this machine (the Screen Machine app's /renderer/* pass-through on the
    # LAN door :8700); people never address the renderer directly.  No password either way.
    if request.app.get(LOCAL_CONTROL) and _control_path(path) and request.remote not in _LOOPBACK:
        if app_url and request.method in ("GET", "HEAD") and not path.startswith("/api/"):
            raise web.HTTPFound(app_url + path)      # a browser on :8702/dev lands on the app's console (HEAD: curl -I)
        return err(403, "local_only", "open the renderer through the Screen Machine app"
                                      + (f": {app_url}/dev" if app_url else "'s /renderer/dev"))
    # No password (owner, 14 Oct): a test rig in a private house. A bearer token from older clients is ignored.
    try:
        resp = await handler(request)
    except binding.MappingError as e:
        return web.json_response({"error": e.code, "detail": e.detail, "problems": e.problems}, status=400)
    except ParamError as e:
        return err(400, e.code, e.detail)
    except SessionError as e:
        return err(e.status, e.code, e.detail)
    except web.HTTPException as e:
        # aiohttp's own errors (unknown route 404, 405, body over 64 MB 413, a plain GET on a WS route 400)
        # are plain text; LIVE_V1 §0 says API errors are JSON {"error", "detail"}
        if _guarded(path) and e.status >= 400:
            resp = err(e.status, HTTP_ERROR_CODES.get(e.status, "http_error"), e.text or e.reason)
            if "Allow" in e.headers:
                resp.headers["Allow"] = e.headers["Allow"]
            resp.headers["Cache-Control"] = "no-store"
            return resp
        raise
    except Exception as e:  # never leak a traceback to the client; log it
        log.exception("unhandled error in %s %s", request.method, path)
        return err(500, "internal", f"{type(e).__name__}")
    if _guarded(path) or path in ("/dev", "/healthz"):
        resp.headers["Cache-Control"] = "no-store"
    elif path.startswith("/static/"):
        resp.headers.setdefault("Cache-Control", "no-cache")   # dev JS/CSS change often: always revalidate (304)
    return resp


async def read_json(request: web.Request, *, allow_empty: bool = True) -> Any:
    if not request.can_read_body:
        if allow_empty:
            return {}
        raise ParamError("bad_request", "a JSON body is required")
    raw = await request.read()   # over client_max_size: HTTPRequestEntityTooLarge -> 413 (JSON, middleware)
    try:
        # in a worker thread, and without long GIL holds for big base64 PNGs (see bodies.py)
        body = await asyncio.get_running_loop().run_in_executor(None, parse_body, raw)
    except ValueError:
        raise ParamError("bad_json", "the body is not valid JSON") from None
    if body is None and allow_empty:
        return {}
    return body


# -- per-item music mappings (mappings.py, binding.py) ---------------------------------------------------
_MAP_KIND = {"scene": "source", "effect": "effect", "transition": "transition"}


def _map_item(request: web.Request) -> tuple[str, str, type, dict]:
    kind, name = request.match_info["kind"], request.match_info["name"]
    if kind not in _MAP_KIND:
        raise SessionError(404, "unknown_item", f"kind must be one of {sorted(_MAP_KIND)}")
    try:
        cls = request.app[REGISTRY].table(_MAP_KIND[kind])[name]
    except KeyError:
        raise SessionError(404, "unknown_item", f"no {kind} plug-in {name!r}") from None
    return kind, name, cls, cls.param_props()


def _effective(kind: str, layered: tuple) -> dict:
    follow, gen, user = layered
    if not follow:
        return {}
    out = {k: v for k, v in gen.items() if v is not None}
    for k, v in user.items():
        if v is None:
            out.pop(k, None)
        else:
            out[k] = v
    return out


def _mapping_index(app: web.Application, query: Any) -> dict:
    presets = app[PRESETS]
    maps = presets.maps
    reg = app[REGISTRY]
    kinds = [query["kind"]] if query.get("kind") in _MAP_KIND else list(_MAP_KIND)
    q = (query.get("q") or "").lower()
    items: dict = {}
    for kind in kinds:
        for name, cls in sorted(reg.table(_MAP_KIND[kind]).items()):
            if getattr(cls, "internal", False) or (q and q not in name.lower()):
                continue
            key = f"{kind}/{name}"
            gen, user = maps.records(key)
            if query.get("edited") == "1" and not user:
                continue
            flags = (gen.get("review") or {}).get("flags") or []
            if query.get("flagged") == "1" and not flags:
                continue
            props = cls.param_props()
            eff = _effective(kind, maps.layers(kind, name, None, props))
            modes = {"live": 0, "section": 0, "cue": 0}
            sigs: set = set()
            colour = None
            for b in eff.values():
                if "colour" in b:
                    colour = b.get("mix")
                    continue
                modes[b.get("at", "live")] = modes.get(b.get("at", "live"), 0) + 1
                sigs.update(t["sig"] for t in b.get("in") or ())
            items[key] = {"src": "user" if user else "llm" if (gen.get("review") or {}).get("by") == "llm"
                          else "rule" if gen else "class", "edited": bool(user),
                          "follow": user.get("follow", gen.get("follow", True)) is not False, **modes,
                          "colour": colour, "signals": sorted(sigs), "flags": flags,
                          "confidence": (gen.get("review") or {}).get("confidence")}
    return {"rev": maps.rev, "gen": maps.gen.get("gen") or {}, "signals": signal_catalog(),
            "canon_states": list(mappings_mod.CANON), "errors": maps.errors, "items": items}


async def mappings_index(request: web.Request) -> web.Response:
    request.app[PRESETS].reload_if_changed()
    out = await asyncio.get_running_loop().run_in_executor(None, _mapping_index, request.app, dict(request.query))
    return web.json_response(out)


def _mapping_record(app: web.Application, kind: str, name: str, cls: type, props: dict, preset: Optional[str],
                    extra: Optional[dict] = None) -> dict:
    presets = app[PRESETS]
    maps = presets.maps
    key = f"{kind}/{name}"
    table = {"scene": presets.scenes, "effect": presets.effects, "transition": presets.transitions}[kind]
    kkey = _MAP_KIND[kind]
    plist = [{"name": n, "tags": e.get("tags") or {}, "params": e.get("params") or {}}
             for n, e in sorted(table.items()) if e.get(kkey) == name]
    pick = preset or (plist[0]["name"] if plist else None)
    gen, user = maps.records(key)
    spec_in = {"preset": pick} if pick else {kkey: name}
    if kind == "scene":
        spec = presets.resolve_scene(spec_in)
    elif kind == "effect":
        spec = presets.resolve_effect(spec_in)
    else:
        spec = presets.resolve_transition(spec_in)
    eff = dict(spec.bindings)
    if extra:
        eff.update({k: v for k, v in extra.items() if v is not None})
        for k in [k for k, v in extra.items() if v is None]:
            eff.pop(k, None)
    params = {}
    gen_b = gen.get("bindings") or {}
    for p, prop in props.items():
        params[p] = {"schema": prop, "tier": prop.get("x-tier"), "modes": mappings_mod.modes_for(p, prop, kind),
                     **({"fam": gen_b[p].get("fam")} if isinstance(gen_b.get(p), dict) and gen_b[p].get("fam") else {})}
    prev = mappings_mod.preview(props, eff, spec.params, key)
    return {"key": key, "rev": user.get("rev", 0), "schema_hash": mappings_mod.schema_hash(props),
            "preset": pick, "presets": plist, "params": params,
            "generated": {k: gen.get(k) for k in ("bindings", "presets", "review", "follow", "hero", "schema_hash")
                          if k in gen},
            "user": user or None,
            "effective": {p: {"binding": b, "base": spec.params.get(p), "preview": prev.get(p)} for p, b in eff.items()},
            "problems": maps.problems(kind, name, props),
            "stale": (gen.get("schema_hash") not in (None, mappings_mod.schema_hash(props)))}


async def mapping_get(request: web.Request) -> web.Response:
    request.app[PRESETS].reload_if_changed()
    kind, name, cls, props = _map_item(request)
    return web.json_response(_mapping_record(request.app, kind, name, cls, props, request.query.get("preset")))


async def mapping_put(request: web.Request) -> web.Response:
    kind, name, cls, props = _map_item(request)
    body = await read_json(request, allow_empty=False)
    if not isinstance(body, dict):
        raise ParamError("bad_request", "body must be a JSON object")
    maps = request.app[PRESETS].maps
    merge = request.method == "PATCH"
    preset = body.get("preset") if merge else None
    drop = [p for p, b in (body.get("bindings") or {}).items() if b == "default"] if merge else []
    if drop:
        body = {**body, "bindings": {p: b for p, b in body["bindings"].items() if b != "default"}}
    rec = maps.validate_record(kind, props, body)
    key = f"{kind}/{name}"
    maps.put_user(key, rec, body.get("base_rev"), merge=merge, preset=preset)
    if drop:
        maps.drop_default(key, preset, drop)
    request.app[EVENTS].emit({"type": "mappings.changed", "key": key, "rev": maps.rev})
    return web.json_response(_mapping_record(request.app, kind, name, cls, props, preset))


async def mapping_delete(request: web.Request) -> web.Response:
    kind, name, cls, props = _map_item(request)
    maps = request.app[PRESETS].maps
    key = f"{kind}/{name}"
    maps.delete_user(key, request.query.get("param"), request.query.get("preset"))
    request.app[EVENTS].emit({"type": "mappings.changed", "key": key, "rev": maps.rev})
    return web.json_response(_mapping_record(request.app, kind, name, cls, props, request.query.get("preset")))


async def mapping_eval(request: web.Request) -> web.Response:
    """{key, preset?, bindings?} -> the record with those bindings tried over the effective ones (writes nothing)."""
    body = await read_json(request, allow_empty=False)
    key = body.get("key") if isinstance(body, dict) else None
    if not isinstance(key, str) or "/" not in key:
        raise ParamError("bad_request", "key must be like scene/<plug-in>")
    kind, name = key.split("/", 1)
    if kind not in _MAP_KIND or name not in request.app[REGISTRY].table(_MAP_KIND[kind]):
        raise SessionError(404, "unknown_item", f"no {key}")
    cls = request.app[REGISTRY].table(_MAP_KIND[kind])[name]
    props = cls.param_props()
    extra = None
    if body.get("bindings") is not None:
        extra = request.app[PRESETS].maps.validate_record(kind, props, {"bindings": body["bindings"]})["bindings"]
    return web.json_response(_mapping_record(request.app, kind, name, cls, props, body.get("preset"), extra))


# -- handlers -------------------------------------------------------------------
async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": __version__})


async def dev_page(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "dev.html")


def with_owner_sets(cat: dict, ratings: dict) -> dict:
    """The owner's Library tags put scenes in sets (owner, 16 Oct): a scene tagged "piano" in the Library joins its
    presets to ``tags.modes`` beside the presets' own, so it is in every mode whose screens take scene_tags
    ["piano"] (room resolves those against this catalog), and a new tag becomes a set the Room page's mode editor
    offers.  Tags are matched lower-case ("Piano" is the piano set).  The presets themselves are not changed."""
    scenes = cat.get("scenes")
    extra = {k[len("scene/"):]: [str(t).strip().lower() for t in v["tags"]]
             for k, v in ratings.items() if k.startswith("scene/") and v.get("tags")}
    if not isinstance(scenes, dict) or not extra:
        return cat
    out = dict(scenes)
    for name, e in scenes.items():
        add = extra.get(e.get("source")) or extra.get(name)
        if not add:
            continue
        tags = dict(e.get("tags") or {"energy": [0.0, 1.0], "weight": 1.0})
        have = tags.get("modes")
        have = [have] if isinstance(have, str) else list(have or [])
        tags["modes"] = have + [t for t in add if t not in have]
        out[name] = {**e, "tags": tags}
    return {**cat, "scenes": out}


SCENE_TAG_SKIP = {"energy", "weight", "auto", "heavy", "status", "origin", "licence", "license_class"}


def scene_tag_index(presets: dict) -> list[dict]:
    """Every tag word on the scene presets, by facet, with how many presets carry it: the generic vocabulary a
    mode's ``scene_tags`` chooses from (owner, 16 Oct: modes select by tags as config, nothing hard-coded).
    ``modes`` holds the presets' set words plus the owner's Library tags (with_owner_sets)."""
    counts: dict[tuple[str, str], int] = {}
    for p in ((presets.get("scenes") or {}).values()):
        for facet, v in (p.get("tags") or {}).items():
            if facet in SCENE_TAG_SKIP:
                continue
            words = [v] if isinstance(v, str) else v if isinstance(v, list) else []
            for w in {str(x).strip().lower() for x in words if isinstance(x, str) and x.strip()}:
                counts[(facet, w)] = counts.get((facet, w), 0) + 1
    return [{"tag": w, "facet": f, "count": n} for (f, w), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]


async def catalog(request: web.Request) -> web.Response:
    presets = request.app[PRESETS]
    presets.reload_if_changed()
    out = request.app[REGISTRY].catalog()
    out["profiles"] = {name: p.describe() for name, p in PROFILES.items()}
    out["presets"] = with_owner_sets(presets.catalog(), request.app[RATINGS].all())
    out["scene_tags"] = scene_tag_index(out["presets"])
    # additive (LIVE_V1 1.1): feature discovery, vocabulary, palettes, bindable signals, tag problems
    out["api"] = {"contract": "LIVE_V1", "rev": "1.1", "features": list(FEATURES)}
    out["vocab"] = presets.vocab.words
    out["palettes"] = palettes.catalog()
    out["signals"] = signal_catalog()
    out["errors"] = presets.catalog_errors()
    # effects layer (EFFECTS_LAYER_DESIGN D.5) and live plug-in discovery
    out["stages"] = list(STAGES)
    reg = request.app[REGISTRY]
    out["errors"] += [f"plug-in {b['module']} broken: {b['error']}" for b in out["broken"]]
    out["plugins"] = {"rev": reg.rev, "rescanned_at": reg.rescanned_at, "rescan_s": RESCAN_S,
                      "counts": {"sources": len(out["sources"]), "transitions": len(out["transitions"]),
                                 "effects": len(out["effects"]), "broken": len(out["broken"])}}
    # GPU frame-time guard: plug-ins tripped at runtime (process-wide) and calibration entries that are not ok;
    # the directors never pick these on their own
    out["slow"] = request.app[MANAGER].slow_list()
    resp = web.json_response(out)
    resp.enable_compression()
    return resp


# -- dev library: every plug-in with thumbnails, live rescans --------------------------------------------------
async def dev_library(request: web.Request) -> web.Response:
    request.app[PRESETS].reload_if_changed()
    data = await asyncio.get_running_loop().run_in_executor(None, request.app[LIBRARY].index)
    resp = web.json_response(data, headers={"Cache-Control": "no-store"})
    resp.enable_compression()        # ~1000 items: gzip keeps the 20 s refresh light
    return resp


async def dev_thumb(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    data = await asyncio.get_running_loop().run_in_executor(None, request.app[LIBRARY].thumb, name)
    if data is None:
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


async def dev_preview(request: web.Request) -> web.StreamResponse:
    path = Library.preview_path(request.match_info["name"])
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "max-age=60"})


# -- dev live preview: a Library card click plays the item live in one reusable 720p session per tab (preview.py)
async def dev_live_preview(request: web.Request) -> web.Response:
    body = await read_json(request, allow_empty=False)
    pv = request.app[PREVIEWS]
    try:
        if isinstance(body, dict) and body.get("keepalive"):
            return web.json_response(pv.keepalive(body))
        request.app[PRESETS].reload_if_changed()
        return web.json_response(await pv.show(body, request.host))
    except PreviewError as e:
        return err(e.status, e.code, e.detail)


async def dev_live_preview_stop(request: web.Request) -> web.Response:
    return web.json_response(await request.app[PREVIEWS].stop(request.query.get("client", "")))


# -- dev ratings: the owner's stars / reject / tags per Library item (ratings.py, config/live/ratings.json)
async def dev_ratings(request: web.Request) -> web.Response:
    return web.json_response({"ratings": request.app[RATINGS].all()}, headers={"Cache-Control": "no-store"})


async def dev_rate(request: web.Request) -> web.Response:
    body = await read_json(request, allow_empty=False)
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, request.app[RATINGS].set, body))


async def rescan_now(app: web.Application, reason: str) -> dict:
    """Rescan the plug-in packages (worker thread), rebuild the presets, tell the consoles if anything moved."""
    reg, presets = app[REGISTRY], app[PRESETS]
    rev0, prev0 = reg.rev, presets.rev
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, reg.rescan)
    await loop.run_in_executor(None, lambda: presets.reload_if_changed(plugin_files=True, wait=True))
    if reg.rev != rev0 or presets.rev != prev0:
        app[EVENTS].emit({"type": "catalog.changed", "reason": reason, "rev": reg.rev,
                          "added": res["added"], "reloaded": res["reloaded"], "removed": res["removed"],
                          "broken": res["broken"]})
    return res


async def dev_rescan(request: web.Request) -> web.Response:
    return web.json_response(await rescan_now(request.app, "manual"))


async def dev_plugin_status(request: web.Request) -> web.Response:
    reg = request.app[REGISTRY]
    errs = request.app[MANAGER].plugin_errors
    plugins = []
    for mod, info in sorted(reg.modules.items()):
        for kind, name in info.get("names", []):
            rt = errs.get((kind, name)) or {}
            plugins.append({"kind": kind, "name": name, "module": mod, "load": "stale" if mod in reg.broken else "ok",
                            "error": (reg.broken.get(mod) or {}).get("error"),
                            "runtime_errors": {"count": rt.get("count", 0), "last_detail": rt.get("last_detail")}})
    for mod, b in sorted(reg.broken.items()):
        if mod not in reg.modules:
            plugins.append({"kind": b["kind"], "name": b["name"], "module": mod, "load": "error", "error": b["error"]})
    return web.json_response({"rev": reg.rev, "rescanned_at": _iso_ts(reg.rescanned_at), "rescan_s": RESCAN_S,
                              "last_scan": reg.last_scan, "plugins": plugins,
                              "preset_problems": request.app[PRESETS].catalog_errors()})


def _iso_ts(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def plugins_watch(app: web.Application) -> None:
    while True:
        await asyncio.sleep(RESCAN_S)
        try:
            await rescan_now(app, "rescan")
        except Exception:
            log.exception("plug-in rescan failed")


async def create_session(request: web.Request) -> web.Response:
    mgr = request.app[MANAGER]
    request.app[PRESETS].reload_if_changed()
    body = await read_json(request, allow_empty=False)
    s, created = await mgr.create(body, request.host)
    return web.json_response({"session": s.id, "state": s.state, "attach": s.attach}, status=201)


async def list_sessions(request: web.Request) -> web.Response:
    mgr = request.app[MANAGER]
    return web.json_response({"sessions": [s.summary() for s in mgr.sessions.values()]})


def _session(request: web.Request):
    s = request.app[MANAGER].get(request.match_info["sid"])
    s.renew()
    return s


async def get_session(request: web.Request) -> web.Response:
    s = _session(request)
    return web.json_response({**s.summary(), "attach": s.attach})   # so a console can watch room's session


async def patch_session(request: web.Request) -> web.Response:
    s = _session(request)
    body = await read_json(request, allow_empty=False)
    if not isinstance(body, dict):
        raise ParamError("bad_request", "body must be a JSON object")
    request.app[PRESETS].reload_if_changed()
    await s.patch(body)
    return web.json_response(s.summary())


async def go(request: web.Request) -> web.Response:
    s = _session(request)
    res = await s.go()
    return web.json_response({"session": s.id, "state": s.state, **res})


async def exit_(request: web.Request) -> web.Response:
    s = _session(request)
    body = await read_json(request, allow_empty=False)
    if not isinstance(body, dict):
        raise ParamError("bad_request", "body must be a JSON object")
    request.app[PRESETS].reload_if_changed()
    res = await s.exit(body)
    return web.json_response({"session": s.id, "state": s.state, **res}, status=202)


async def cue(request: web.Request) -> web.Response:
    s = _session(request)
    body = await read_json(request, allow_empty=False)
    request.app[PRESETS].reload_if_changed()
    return web.json_response(await s.cue(body), status=202)


async def session_inputs(request: web.Request) -> web.Response:
    return web.json_response(_session(request).inputs())


async def get_fx(request: web.Request) -> web.Response:
    return web.json_response(_session(request).fx.get())


async def put_fx(request: web.Request) -> web.Response:
    s = _session(request)
    body = await read_json(request, allow_empty=False)
    request.app[PRESETS].reload_if_changed()
    return web.json_response(await s.fx.put(body))


async def dev_art(request: web.Request) -> web.Response:
    """Dev only: the screen's current artwork, letterboxed to ?w=&h= (default 1280x720) as PNG."""
    screen = request.match_info["screen"]
    if screen not in DEV_ART_SCREENS:
        raise web.HTTPNotFound()
    try:
        w = min(3840, max(16, int(request.query.get("w", "1280"))))
        h = min(2160, max(16, int(request.query.get("h", "720"))))
    except ValueError:
        raise web.HTTPBadRequest() from None
    art_dir = request.app[ART_DIR]
    path = art_dir / f"{screen}.jpg" if art_dir is not None else None
    if path is None or not path.is_file():
        raise web.HTTPNotFound()

    def render() -> bytes:
        from PIL import Image, ImageOps

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            s = min(w / img.width, h / img.height)
            nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
            canvas = Image.new("RGB", (w, h), (0, 0, 0))
            canvas.paste(img.resize((nw, nh), Image.Resampling.LANCZOS), ((w - nw) // 2, (h - nh) // 2))
        buf = io.BytesIO()
        canvas.save(buf, "PNG", compress_level=1)
        return buf.getvalue()

    data = await asyncio.get_running_loop().run_in_executor(None, render)
    return web.Response(body=data, content_type="image/png", headers={"Cache-Control": "no-store"})


async def sound_test(request: web.Request) -> web.Response:
    """POST /api/sound/test {"on": bool, "piece"?: an id from GET, "phrase"?: "medley", "path"?: a .mid file}: a test piano feeding every session's sound view
    (sound.py) until the real MIDI is in.  409 while sound_view.py is not installed."""
    from . import sound as sound_mod
    body = await read_json(request, allow_empty=False)
    if not isinstance(body, dict) or not isinstance(body.get("on"), bool):
        raise ParamError("bad_request", 'body must be {"on": true|false, "path"?: "file.mid"}')
    if not sound_mod.available():
        return err(409, "no_sound_view", "stream_service/sound_view.py is not installed yet")
    path, phrase, piece = body.get("path"), body.get("phrase"), body.get("piece")
    if any(v is not None and not isinstance(v, str) for v in (path, phrase, piece)):
        raise ParamError("bad_request", "path, phrase and piece must be strings (piece: an id from GET "
                                        "/api/sound/test; phrase: sweep, scale, arpeggios, chords, repeats, "
                                        "sostenuto, medley)")
    try:
        return web.json_response(request.app[MANAGER].test_piano(body["on"], path, phrase, piece))
    except ValueError as e:
        raise ParamError("bad_request", str(e))


async def sound_test_status(request: web.Request) -> web.Response:
    """GET /api/sound/test: {available, running, piece, playing_s, pieces: [{id, label, kind}]} (the Room page's
    Test music card; .mid files dropped in config/live/test-piano/ appear as pieces)."""
    from . import sound as sound_mod
    if not sound_mod.available():
        return web.json_response({"available": False, "running": False, "piece": None, "pieces": []})
    return web.json_response(request.app[MANAGER].test_piano_status())


async def fx_next(request: web.Request) -> web.Response:
    """POST /api/sessions/{sid}/fx/next: change the effect now (swap the oldest auto effect, or add one)."""
    return web.json_response(await _session(request).fx_next())


async def renew(request: web.Request) -> web.Response:
    s = _session(request)
    return web.json_response({"session": s.id, "state": s.state,
                              "expires_in_s": s.summary()["expires_in_s"]})


async def delete_session(request: web.Request) -> web.Response:
    mgr = request.app[MANAGER]
    await mgr.delete(request.match_info["sid"])
    return web.Response(status=204)


async def session_frames(request: web.Request) -> web.Response:
    """Debug (additive): per-frame timing records, for the performance tools."""
    s = _session(request)
    try:
        since = int(request.query.get("since", "-1"))
    except ValueError:
        raise ParamError("bad_request", "since must be an integer") from None
    return web.json_response({"session": s.id, "fps": s.profile.fps, "frames": s.engine.frame_records(since)})


async def state(request: web.Request) -> web.Response:
    mgr = request.app[MANAGER]
    gpus = await asyncio.get_running_loop().run_in_executor(None, mgr.gpu_state)
    return web.json_response({"sessions": [s.summary() for s in mgr.active()], "gpus": gpus})


async def diag(request: web.Request) -> web.Response:
    """Dev diagnostics (additive): host load, GPU / NVENC use, per-session timing over the last 2 s, a verdict."""
    from . import diag as dg

    mgr = request.app[MANAGER]
    loop = asyncio.get_running_loop()
    gpu_list = await loop.run_in_executor(None, dg.gpus, mgr.gpu_names())
    now = time.monotonic()
    out = []
    for s in mgr.active():
        win = s.engine.window_stats(2.0) if hasattr(s.engine, "window_stats") else None
        guard = s.engine.guard_stats() if hasattr(s.engine, "guard_stats") else None
        out.append({"session": s.id, "screen": s.screen, "state": s.state, "gpu": s.gpu, "profile": s.profile.name,
                    "fps": s.profile.fps, "scene": s.current_name, "window": win, "client": dg.client_stats(s, now),
                    "slow_trips": s.slow_trips, "gpu_guard": guard})
    want = request.query.get("session")          # the console's session first: its verdict wins
    out.sort(key=lambda x: x["session"] != want)
    h = dg.host()
    return web.json_response({"host": h, "gpus": gpu_list, "sessions": out, "verdict": dg.verdict(h, gpu_list, out),
                              "plugin_errors": mgr.plugin_error_list(),
                              "slow": {"trips": mgr.slow_trips, "plugins": mgr.slow_runtime_list()}})


async def events_ws(request: web.Request) -> web.StreamResponse:
    mgr = request.app[MANAGER]
    return await request.app[EVENTS].serve(request, mgr.snapshot_events)


async def media_ws(request: web.Request) -> web.StreamResponse:
    mgr = request.app[MANAGER]
    s = mgr.sessions.get(request.match_info["sid"])
    k = request.query.get("k")
    if s is None or k is None or not hmac.compare_digest(k.encode(), s.key.encode()):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=4403 if s is not None else 4404, message=b"forbidden")
        return ws
    return await s.media.serve(request)


async def presets_watch(app: web.Application) -> None:
    presets = app[PRESETS]
    while True:
        await asyncio.sleep(1.0)
        try:
            presets.reload_if_changed()
        except Exception:
            log.exception("preset reload failed")


# -- app ------------------------------------------------------------------------
def make_app(*, config_dir: pathlib.Path, public_base: Optional[str] = None,
             max_sessions: int = 4, registry: Optional[Registry] = None, show_bus: Optional[str] = None,
             music_bus: Sequence[str] = (), show_bus_token_file: Optional[str] = None, latency_file: Optional[pathlib.Path] = None,
             art_dir: Optional[pathlib.Path] = None, local_control: bool = False,
             app_url: str = "", warm: bool = False) -> web.Application:
    from .plugins import default_registry

    registry = registry or default_registry()
    presets = Presets(config_dir, registry)
    events = EventHub()
    inputs = ShowInputs(show_bus) if show_bus else None
    mgr = SessionManager(registry=registry, presets=presets, events=events, max_sessions=max_sessions,
                         public_base=public_base, inputs=inputs, latency_file=latency_file)
    app = web.Application(middlewares=[auth_and_errors], client_max_size=MAX_BODY)
    app[REGISTRY] = registry
    app[PRESETS] = presets
    app[EVENTS] = events
    app[MANAGER] = mgr
    app[ART_DIR] = pathlib.Path(art_dir) if art_dir else None
    app[SHOW_BUS] = inputs
    app[LIBRARY] = mgr.library = Library(registry, presets, mgr)
    app[PREVIEWS] = Previews(mgr, presets)
    app[RATINGS] = mgr.ratings = Ratings(pathlib.Path(config_dir) / "ratings.json")
    app[LOCAL_CONTROL] = local_control
    app[APP_URL] = app_url.rstrip("/")
    r = app.router
    r.add_get("/healthz", healthz)
    r.add_get("/dev", dev_page)
    r.add_get("/dev/art/{screen}.png", dev_art)
    r.add_get("/dev/library", dev_library)
    r.add_get("/dev/thumb/{name}.png", dev_thumb)
    r.add_get("/dev/preview/{name}.mp4", dev_preview)
    r.add_post("/dev/preview", dev_live_preview)
    r.add_delete("/dev/preview", dev_live_preview_stop)
    r.add_get("/dev/ratings", dev_ratings)
    r.add_post("/dev/rate", dev_rate)
    r.add_get("/dev/plugins", dev_plugin_status)
    r.add_post("/dev/rescan", dev_rescan)
    r.add_get("/dev/lab/status", dev_plugin_status)      # the console's Plug-ins section (static/console/hood/plugins.js)
    r.add_post("/dev/lab/rescan", dev_rescan)
    r.add_get("/api/catalog", catalog)
    r.add_get("/api/mappings", mappings_index)
    r.add_post("/api/mappings/eval", mapping_eval)
    r.add_get("/api/mappings/{kind}/{name}", mapping_get)
    r.add_put("/api/mappings/{kind}/{name}", mapping_put)
    r.add_patch("/api/mappings/{kind}/{name}", mapping_put)
    r.add_delete("/api/mappings/{kind}/{name}", mapping_delete)
    r.add_post("/api/sessions", create_session)
    r.add_post("/api/sound/test", sound_test)
    r.add_get("/api/sound/test", sound_test_status)
    r.add_get("/api/sessions", list_sessions)
    r.add_get("/api/sessions/{sid}", get_session)
    r.add_patch("/api/sessions/{sid}", patch_session)
    r.add_delete("/api/sessions/{sid}", delete_session)
    r.add_post("/api/sessions/{sid}/go", go)
    r.add_post("/api/sessions/{sid}/exit", exit_)
    r.add_post("/api/sessions/{sid}/renew", renew)
    r.add_post("/api/sessions/{sid}/cue", cue)
    r.add_get("/api/sessions/{sid}/inputs", session_inputs)
    r.add_get("/api/sessions/{sid}/fx", get_fx)
    r.add_put("/api/sessions/{sid}/fx", put_fx)
    r.add_post("/api/sessions/{sid}/fx/next", fx_next)
    r.add_get("/api/sessions/{sid}/frames", session_frames)
    r.add_get("/api/state", state)
    r.add_get("/api/diag", diag)
    r.add_get("/api/events", events_ws)
    r.add_get("/media/{sid}", media_ws)
    r.add_static("/static/", STATIC, show_index=False)

    async def on_startup(app: web.Application) -> None:
        mgr.loop = asyncio.get_running_loop()
        app[TASKS] = [asyncio.create_task(mgr.reaper()), asyncio.create_task(presets_watch(app)),
                      asyncio.create_task(mgr.ticker()), asyncio.create_task(plugins_watch(app))]
        from .library import vetting

        mgr.loop.run_in_executor(None, vetting)    # the vetted pool's sheet / cost scan, off the loop
        if warm:                                   # --warm: the first live session on a GPU starts fast
            app[TASKS].append(asyncio.create_task(mgr.warm_all()))
        if inputs is not None:
            from . import showbus

            app[TASKS].append(asyncio.create_task(showbus.run(show_bus, inputs, show_bus_token_file,
                                                               sound_sink=mgr.broadcast_sound)))
        for url in music_bus:                      # --music-bus: sound only (show_service), never the fixtures
            from . import showbus

            app[TASKS].append(asyncio.create_task(showbus.run(url, None, sound_sink=mgr.broadcast_sound,
                                                               name="music bus", room_sink=mgr.on_room_message)))

    async def on_shutdown(app: web.Application) -> None:
        for task in app[TASKS]:
            task.cancel()
        await app[PREVIEWS].close()
        await mgr.shutdown()      # session.ended events go out first ...
        await asyncio.sleep(0)
        events.close()            # ... then the events sockets close (else shutdown waits shutdown_timeout)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app
