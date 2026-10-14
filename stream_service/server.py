"""aiohttp app: REST API, events WebSocket, media WebSockets and the dev page (LIVE_V1 §1).

Auth: every /api/* route (and the dev /dev/synth proxy) needs ``Authorization: Bearer <token>``; the events socket
takes ``?token=``; a media socket takes its session's ``?k=<key>`` (wrong key -> close 4403).  /dev, /dev/art/*,
/static/* and /healthz are open (the dev page asks for the token and keeps it in localStorage).

Additive (LIVE_V1 1.1): POST /api/sessions/{id}/cue, GET /api/sessions/{id}/inputs, the catalog's api / vocab /
palettes / signals / errors blocks, the show-bus client task (--show-bus) and, dev only, GET /dev/art/{screen}.png:
the screen's current artwork (Flask's output/<screen>.jpg, read-only) letterboxed to a profile's size, and
GET/POST /dev/synth: a proxy to show-synth's control API (http://H:P/v1/control for --show-bus ws://H:P/v1/show).
"""
from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import pathlib
import time
from typing import Any, Optional

import aiohttp
from aiohttp import WSCloseCode, web
from yarl import URL

from . import __version__, palettes
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
TOKEN = web.AppKey("token", str)
TASKS = web.AppKey("tasks", list)
ART_DIR = web.AppKey("art_dir", object)
SHOW_BUS = web.AppKey("show_bus", object)
SYNTH_URL = web.AppKey("synth_url", object)
SYNTH_TIMEOUT_S = 3.0
LIBRARY = web.AppKey("library", Library)
PREVIEWS = web.AppKey("previews", Previews)
RATINGS = web.AppKey("ratings", Ratings)
RESCAN_S = 20.0                     # live plug-in discovery: new / changed / removed plug-in modules
DEV_ART_SCREENS = ("north-screen", "south-screen", "devtest")


FEATURES = ("cue", "auto", "direction", "palette", "inputs", "bindings", "effects", "fx_bus", "fx_auto",
            "plugin_errors", "gpu_guard")
HTTP_ERROR_CODES = {400: "bad_request", 404: "not_found", 405: "method_not_allowed", 413: "too_large"}


def err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def _token_ok(request: web.Request, given: Optional[str]) -> bool:
    return given is not None and hmac.compare_digest(given.encode(), request.app[TOKEN].encode())


def _guarded(path: str) -> bool:
    """Bearer-token routes with JSON errors: the API and the dev show-synth proxy."""
    return path.startswith("/api/") or path in ("/dev/synth", "/dev/rescan", "/dev/lab/rescan", "/dev/preview", "/dev/rate")


LOCAL_CONTROL = web.AppKey("local_control", bool)
_LOOPBACK = ("127.0.0.1", "::1")


def _control_path(path: str) -> bool:
    """Control routes (/api/*, /dev*). /healthz, /media/* (the TVs' and previews' video) and /static/* stay open."""
    return path.startswith("/api/") or path == "/dev" or path.startswith("/dev/")


@web.middleware
async def auth_and_errors(request: web.Request, handler):
    path = request.path
    # --local-control: control only from this machine (the Screen Machine app's /renderer/* pass-through on the
    # LAN door :8700); people never address the renderer directly.  No password either way.
    if request.app.get(LOCAL_CONTROL) and _control_path(path) and request.remote not in _LOOPBACK:
        return err(403, "local_only", "open the renderer through the Screen Machine app: "
                                      "http://192.168.1.205:8700/renderer/dev")
    # No password (owner, 14 Oct): a test rig in a private house. A bearer token from older clients is ignored.
    try:
        resp = await handler(request)
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


# -- handlers -------------------------------------------------------------------
async def healthz(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": __version__})


async def dev_page(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(STATIC / "dev.html")


async def catalog(request: web.Request) -> web.Response:
    presets = request.app[PRESETS]
    presets.reload_if_changed()
    out = request.app[REGISTRY].catalog()
    out["profiles"] = {name: p.describe() for name, p in PROFILES.items()}
    out["presets"] = presets.catalog()
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
    return web.json_response(_session(request).summary())


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


def synth_control_url(show_bus: Optional[str]) -> Optional[str]:
    """show-synth's control API next to its bus: ws://H:P/v1/show -> http://H:P/v1/control (wss -> https)."""
    if not show_bus:
        return None
    u = URL(show_bus)
    if not u.is_absolute():
        return None
    return str(u.with_scheme("https" if u.scheme == "wss" else "http").with_path("/v1/control"))


async def dev_synth(request: web.Request) -> web.Response:
    """Dev only: GET / POST show-synth's runtime control (tempo, ramp, song, pulses, next section) through this
    origin, so the console needs no CORS.  The bearer token is checked here and never forwarded."""
    url = request.app[SYNTH_URL]
    if url is None:
        return err(404, "not_found", "no --show-bus: start stream_service with --show-bus "
                                     "ws://127.0.0.1:8711/v1/show (show-synth) to control the simulated music")
    kw: dict = {}
    if request.method == "POST":
        body = await read_json(request, allow_empty=False)
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        kw["json"] = body
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=SYNTH_TIMEOUT_S)) as http:
            async with http.request(request.method, url, **kw) as r:
                status, text = r.status, await r.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        return err(502, "synth_unreachable", f"show-synth control at {url} did not answer ({type(e).__name__})")
    try:
        data = json.loads(text)
    except ValueError:
        return err(502, "not_synth", f"{url} answered HTTP {status} without JSON: is the show bus show-synth?")
    return web.json_response(data, status=status)


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
def make_app(*, token: str, config_dir: pathlib.Path, public_base: Optional[str] = None,
             max_sessions: int = 4, registry: Optional[Registry] = None, show_bus: Optional[str] = None,
             show_bus_token_file: Optional[str] = None, latency_file: Optional[pathlib.Path] = None,
             art_dir: Optional[pathlib.Path] = None, local_control: bool = False) -> web.Application:
    from .plugins import default_registry

    registry = registry or default_registry()
    presets = Presets(config_dir, registry)
    events = EventHub()
    inputs = ShowInputs(show_bus) if show_bus else None
    mgr = SessionManager(registry=registry, presets=presets, events=events, max_sessions=max_sessions,
                         public_base=public_base, inputs=inputs, latency_file=latency_file)
    app = web.Application(middlewares=[auth_and_errors], client_max_size=MAX_BODY)
    app[TOKEN] = token
    app[REGISTRY] = registry
    app[PRESETS] = presets
    app[EVENTS] = events
    app[MANAGER] = mgr
    app[ART_DIR] = pathlib.Path(art_dir) if art_dir else None
    app[SHOW_BUS] = inputs
    app[SYNTH_URL] = synth_control_url(show_bus)
    app[LIBRARY] = mgr.library = Library(registry, presets, mgr)
    app[PREVIEWS] = Previews(mgr, presets)
    app[RATINGS] = Ratings(pathlib.Path(config_dir) / "ratings.json")
    app[LOCAL_CONTROL] = local_control
    r = app.router
    r.add_get("/healthz", healthz)
    r.add_get("/dev", dev_page)
    r.add_get("/dev/art/{screen}.png", dev_art)
    r.add_get("/dev/synth", dev_synth)
    r.add_post("/dev/synth", dev_synth)
    r.add_get("/dev/library", dev_library)
    r.add_get("/dev/thumb/{name}.png", dev_thumb)
    r.add_get("/dev/preview/{name}.mp4", dev_preview)
    r.add_post("/dev/preview", dev_live_preview)
    r.add_delete("/dev/preview", dev_live_preview_stop)
    r.add_get("/dev/ratings", dev_ratings)
    r.add_post("/dev/rate", dev_rate)
    r.add_get("/dev/plugins", dev_plugin_status)
    r.add_post("/dev/rescan", dev_rescan)
    r.add_get("/dev/lab/status", dev_plugin_status)      # the console's plug-in status footer (library.js)
    r.add_post("/dev/lab/rescan", dev_rescan)
    r.add_get("/api/catalog", catalog)
    r.add_post("/api/sessions", create_session)
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
        if inputs is not None:
            from . import showbus

            app[TASKS].append(asyncio.create_task(showbus.run(show_bus, inputs, show_bus_token_file)))

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
