"""Routes for the devices worked by the RM4 Pro's remotes (adapters/broadlink_rf.py, rf_light.py): learn and test their
codes from the Lights setup page. Open, like the rest of the AV stack (owner, 14 Oct). Registered from api.py.

  GET    /api/v1/remotes                 every device file: its buttons (learned or not), the learn in progress, and a
                                         light's record of what it is doing
  POST   /api/v1/remotes/{id}/learn      {"button", "sweep"?: true} learn one button (RF: the frequency found once is
                                         reused unless sweep). 409 while the RM4 Pro is learning something else
  GET    /api/v1/remotes/{id}/learn      that learn's progress: {phase, words, running, ...}
  DELETE /api/v1/remotes/{id}/learn      stop it
  POST   /api/v1/remotes/{id}/send       {"button", "times"?} transmit a learned code (a light's record follows it)
  POST   /api/v1/remotes/{id}/reset      a light's reset: on, white, down to the floor, off
  PUT    /api/v1/remotes/{id}/colours    {"colours": {id: name}} rename a light's colours (their ids stay)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .adapters.broadlink_rf import LearnJob, Remote, RFError

if TYPE_CHECKING:
    from .service import RoomService


def _err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def register(app: web.Application, svc: "RoomService") -> None:
    def remotes() -> dict[str, tuple[Remote, list]]:
        out: dict[str, tuple[Remote, list]] = {}
        coord = getattr(svc, "coordinator", None)
        for a in (coord.adapters.values() if coord else ()):
            r = getattr(a, "remote", None)
            if isinstance(r, Remote):
                out.setdefault(r.id, (r, []))[1].append(a)
        return out

    def find(request: web.Request) -> tuple[Remote, list] | web.Response:
        rid = request.match_info["id"]
        got = remotes().get(rid)
        return got or _err(404, "unknown_remote", f"no remote device {rid!r}")

    def light_of(adapters: list):
        from .adapters.rf_light import RFLightAdapter
        return next((a for a in adapters if isinstance(a, RFLightAdapter)), None)

    def view(r: Remote, adapters: list) -> dict:
        f, light = r.file, light_of(adapters)
        return {"id": r.id, "label": str(f.raw.get("label") or r.id), "medium": f.medium, "host": r.rm.host,
                "freq_mhz": f.freq_mhz, "subsystems": [a.subsystem for a in adapters],
                "light": light is not None, "steps": light.steps if light else None,
                "buttons": r.button_rows(), "job": r.job.view() if r.job else None,
                "state": light.state() if light else None}

    async def body(request: web.Request) -> dict:
        try:
            b = await request.json()
        except Exception:
            b = None
        return b if isinstance(b, dict) else {}

    async def list_remotes(request: web.Request) -> web.Response:
        return web.json_response({"remotes": [view(r, a) for r, a in remotes().values()]})

    async def learn(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, _ = got
        b = await body(request)
        button = str(b.get("button") or "").strip()
        if not button or not button.replace("_", "").replace("-", "").isalnum():
            return _err(400, "bad_button", "a button id: letters, digits, - and _")
        busy = next((x for x, _ in remotes().values() if x.rm is r.rm and x.job and x.job.running), None)
        if busy:
            return _err(409, "busy", f"the RM4 Pro is still learning {busy.job.button!r} for {busy.id}")
        r.job = LearnJob(r, button, sweep=bool(b.get("sweep"))).start()
        return web.json_response(r.job.view())

    async def learn_status(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, _ = got
        return web.json_response(r.job.view() if r.job else {"phase": "idle", "running": False})

    async def learn_stop(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, _ = got
        if r.job and r.job.running:
            r.job.stop()
        return web.json_response(r.job.view() if r.job else {"phase": "idle", "running": False})

    async def send(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, adapters = got
        b = await body(request)
        button, times = str(b.get("button") or ""), max(1, min(32, int(b.get("times") or 1)))
        if button not in r.file.codes:
            return _err(404, "not_learned", f"no code learned for {button!r}")
        light = light_of(adapters)
        if light is not None:
            out = await light.signal(None, "press", {"button": button, "times": times})
            return web.json_response({"status": out.status, "detail": out.detail, "state": light.state()},
                                     status=200 if out.status == "ok" else 502)
        try:
            async with r.lock:
                await r.press(button, times)
        except RFError as e:
            return _err(502, "send_failed", str(e))
        for m, spec in r.settings.items():      # a setting's button: the room's record follows it
            for v, bid in (spec.get("buttons") or {}).items():
                if bid == button:
                    r.remember(m, v)
        return web.json_response({"status": "ok", "detail": f"pressed {button}"})

    async def reset(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        light = light_of(got[1])
        if light is None:
            return _err(400, "not_a_light", "only a light has a reset")
        out = await light.reset()
        return web.json_response({"status": out.status, "detail": out.detail, "state": light.state()},
                                 status=200 if out.status == "ok" else 502)

    async def colours(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, adapters = got
        if light_of(adapters) is None:
            return _err(400, "not_a_light", "only a light has colours")
        names = (await body(request)).get("colours")
        if not isinstance(names, dict):
            return _err(400, "bad_body", '{"colours": {id: name}}')
        have = r.file.raw.get("colours") or {}
        clean = {str(k): str(v).strip()[:40] for k, v in names.items() if k in have and str(v).strip()}

        def put(raw: dict) -> None:
            raw.setdefault("colours", {}).update(clean)
        r.file.update(put)
        return web.json_response(view(r, adapters))

    a = app.router
    a.add_get("/api/v1/remotes", list_remotes)
    a.add_post("/api/v1/remotes/{id}/learn", learn)
    a.add_get("/api/v1/remotes/{id}/learn", learn_status)
    a.add_delete("/api/v1/remotes/{id}/learn", learn_stop)
    a.add_post("/api/v1/remotes/{id}/send", send)
    a.add_post("/api/v1/remotes/{id}/reset", reset)
    a.add_put("/api/v1/remotes/{id}/colours", colours)
