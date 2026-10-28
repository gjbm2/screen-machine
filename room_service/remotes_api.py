"""Routes for the devices worked by the RM4 Pro's remotes (adapters/broadlink_rf.py, rf_light.py): add, remove, learn
and test them in Settings › Lights (setup lives in Settings; the main pages operate). Config-driven: a device exists
once it is added (its file in config/room/rf/, a card in its room in rooms.json), made from a device type
(config/room/rf/types/). Open, like the rest of the AV stack (owner, 14 Oct). Registered from api.py.

  GET    /api/v1/remotes                 every device added: its type, room, buttons (learned or not), the learn in
                                         progress, and a light's record of what it is doing; plus the types and RM4 Pros
  POST   /api/v1/remotes                 {"model", "label", "room", "host"?} add one (host: the RM4 Pro the others use)
  DELETE /api/v1/remotes/{id}            remove it (its file, codes and card). 409 while a mode still sets it
  POST   /api/v1/remotes/{id}/learn      {"button", "sweep"?: true} learn one button (RF: the frequency found once is
                                         reused unless sweep). 409 while the RM4 Pro is learning something else
  GET    /api/v1/remotes/{id}/learn      that learn's progress: {phase, words, running, ...}
  DELETE /api/v1/remotes/{id}/learn      stop it
  POST   /api/v1/remotes/{id}/send       {"button", "times"?} transmit a learned code (a light's record follows it)
  POST   /api/v1/remotes/{id}/reset      a light's reset: on, white, down to the floor, off
  PUT    /api/v1/remotes/{id}/colours    {"colours": {id: name}} rename a light's colours (their ids stay)
  PUT    /api/v1/remotes/{id}/clean      {"clean": {"frames", "gap_ms"} | null} how each RF press is sent: a clean
                                         packet of `frames` copies of the code (broadlink_rf.clean_rf), or as learned
  PUT    /api/v1/remotes/{id}/state      {"values": {target: value}} put the room's record right when it is out of step
                                         with the appliance (owner, 28 Oct): nothing is sent. Its view's "state" rows
                                         list every target with its schema and what the room believes now
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

import json
from pathlib import Path

from .adapters import broadlink_rf
from .adapters.broadlink_rf import LearnJob, Remote, RemoteSettingsAdapter, RFError
from .roomstate.coordinator import Refused

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

    def rooms_path() -> Path | None:
        return getattr(getattr(svc, "room_config", None), "rooms_path", None)

    def rooms() -> dict:
        p = rooms_path()
        try:
            return json.loads(p.read_text()) if p else {}
        except (OSError, ValueError):
            return {}

    def edit_rooms(fn) -> None:
        p = rooms_path()
        if not p:
            return
        raw = json.loads(p.read_text())
        fn(raw)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(p)

    def card(adapters: list) -> dict:
        """The device's card on the Room page: its power target, then every setting target."""
        first, settings = adapters[0], []
        power = f"{first.subsystem}.{first.members[0]}"
        for a in adapters:
            settings += [f"{a.subsystem}.{m}" for m in a.members if f"{a.subsystem}.{m}" != power]
        return {"power": power, "settings": settings}

    def room_of(adapters: list) -> str | None:
        subs = {a.subsystem for a in adapters}
        for rid, room in rooms().items():
            for e in (room.get("effects") or []) if isinstance(room, dict) else []:
                t = e.get("power") if isinstance(e, dict) else e
                if isinstance(t, str) and t.split(".", 1)[0] in subs:
                    return rid
        return None

    def believed(a, m: str):
        """What the room holds for one member: a light's or a setting's own record, else the room's belief."""
        if a is light_of([a]):
            return a.now.get(m)
        if isinstance(a, RemoteSettingsAdapter):
            return a.remote.sent.get(m)
        return ((svc.coordinator.s.get("believed") or {}).get(f"{a.subsystem}.{m}") or {}).get("value")

    def state_rows(adapters: list) -> list[dict]:
        return [{"target": f"{a.subsystem}.{m}", "label": a.label(m), "schema": a.value_schema(m), "value": believed(a, m)}
                for a in adapters for m in a.members]

    def view(r: Remote, adapters: list) -> dict:
        f, light = r.file, light_of(adapters)
        return {"id": r.id, "label": str(f.raw.get("label") or r.id), "model": f.raw.get("model"),
                "room": room_of(adapters), "medium": f.medium, "host": r.rm.host,
                "freq_mhz": f.freq_mhz, "subsystems": [a.subsystem for a in adapters],
                "light": light is not None, "steps": light.steps if light else None,
                "repeat": [str(b) for b in (f.raw.get("repeat") or [])], "gap_s": float(f.raw.get("gap_s", 0.3)),
                "clean": f.raw.get("clean") if isinstance(f.raw.get("clean"), dict) else None,
                "buttons": r.button_rows(), "job": r.job.view() if r.job else None,
                "state": light.state() if light else None, "state_rows": state_rows(adapters)}

    async def body(request: web.Request) -> dict:
        try:
            b = await request.json()
        except Exception:
            b = None
        return b if isinstance(b, dict) else {}

    async def list_remotes(request: web.Request) -> web.Response:
        have = remotes()
        types = [{"id": k, "label": str(t.get("type_label") or k), "description": t.get("description"),
                  "medium": t.get("medium") or "rf"} for k, t in broadlink_rf.device_types().items()]
        rooms_list = [{"id": k, "name": str(v.get("name") or k)} for k, v in rooms().items()
                      if not k.startswith("_") and isinstance(v, dict)]
        return web.json_response({"remotes": [view(r, a) for r, a in have.values()], "types": types,
                                  "hosts": sorted({r.rm.host for r, _ in have.values() if r.rm.host}),
                                  "rooms": rooms_list})

    async def add(request: web.Request) -> web.Response:
        b = await body(request)
        model, label, room = str(b.get("model") or ""), str(b.get("label") or "").strip(), str(b.get("room") or "")
        have = remotes()
        hosts = sorted({r.rm.host for r, _ in have.values() if r.rm.host})
        host = str(b.get("host") or (hosts[0] if len(hosts) == 1 else "")).strip()
        if model not in broadlink_rf.device_types():
            return _err(400, "bad_model", f"no device type {model!r}")
        if not host:
            return _err(400, "no_host", "which RM4 Pro sends its codes? (its address on the house network)")
        r_all = rooms()
        if room not in r_all or room.startswith("_"):
            return _err(400, "bad_room", f"no room {room!r}")
        path = broadlink_rf.new_device(model, label, host)
        rms = {r.rm.host: r.rm for r, _ in have.values()}
        state_dir = getattr(getattr(svc, "cfg", None), "state_dir", None)
        added = broadlink_rf.from_config(path, state_dir=state_dir, rms=rms)
        if not added:
            path.unlink(missing_ok=True)
            return _err(500, "not_loaded", f"{path.name} didn't load; see the room log")
        taken = set(svc.coordinator.adapters)
        clash = [a.subsystem for a in added if a.subsystem in taken]
        if clash:
            path.unlink(missing_ok=True)
            return _err(409, "taken", f"the room already has {', '.join(clash)}")
        edit_rooms(lambda raw: raw[room].setdefault("effects", []).append(card(added)))
        svc.swap_adapters(added, set(), cause=f"{path.stem} added in Settings")
        rid = path.stem
        got = remotes().get(rid)
        return web.json_response(view(*got) if got else {"id": rid}, status=201)

    async def remove(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, adapters = got
        subs = {a.subsystem for a in adapters}
        cfg = svc.coordinator.cfg
        using = sorted({m.label or name for name, m in cfg.modes.items()
                        if any(t.split(".", 1)[0] in subs for t in m.state)})
        if using:
            return _err(409, "in_modes", f"{', '.join(using)} still set it: take it out of those modes first")
        if r.job and r.job.running:
            r.job.stop()

        def drop(raw: dict) -> None:
            for room in raw.values():
                if isinstance(room, dict) and isinstance(room.get("effects"), list):
                    room["effects"] = [e for e in room["effects"]
                                       if str(e.get("power") if isinstance(e, dict) else e).split(".", 1)[0] not in subs]
        edit_rooms(drop)
        svc.swap_adapters([], subs, cause=f"{r.id} removed in Settings")
        r.file.path.unlink(missing_ok=True)
        return web.json_response({"removed": r.id})

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

    async def set_state(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, adapters = got
        values = (await body(request)).get("values")
        if not isinstance(values, dict) or not values:
            return _err(400, "bad_body", '{"values": {target: value}}')
        by_sub = {a.subsystem: a for a in adapters}
        touched = set()
        try:
            for t, v in values.items():
                sub, _, m = str(t).partition(".")
                a = by_sub.get(sub)
                if a is None or m not in a.members:
                    return _err(400, "bad_target", f"{t} isn't one of {r.id}'s")
                v = a.normalize(m, v)
                if a is light_of([a]):
                    a.now[m] = v
                    a.kept.pop(m, None)
                    a._save()
                elif isinstance(a, RemoteSettingsAdapter):
                    r.remember(m, v)
                else:
                    svc.coordinator.assume(t, v, cause="put right on the light setup page")
                touched.add(sub)
        except ValueError as e:
            return _err(400, "bad_value", str(e))
        except Refused as e:
            return _err(409, e.code, str(e))
        for sub in touched:                      # the room reads the corrected record now, not in 5 s
            if by_sub[sub].kind == "level":
                await svc.coordinator.reconcile_tick(sub)
        svc.events.mark_dirty()
        return web.json_response(view(r, adapters))

    async def set_clean(request: web.Request) -> web.Response:
        got = find(request)
        if isinstance(got, web.Response):
            return got
        r, adapters = got
        c = (await body(request)).get("clean")
        if c is not None and not (isinstance(c, dict) and 1 <= int(c.get("frames", 0)) <= 30):
            return _err(400, "bad_clean", '{"clean": {"frames": 1-30, "gap_ms"?: 120} | null}')

        def put(raw: dict) -> None:
            if c is None:
                raw.pop("clean", None)
            else:
                raw["clean"] = {"frames": int(c["frames"]), "gap_ms": float(c.get("gap_ms", 120))}
        r.file.update(put)
        r.file.fresh()
        return web.json_response(view(r, adapters))

    a = app.router
    a.add_put("/api/v1/remotes/{id}/state", set_state)
    a.add_put("/api/v1/remotes/{id}/clean", set_clean)
    a.add_get("/api/v1/remotes", list_remotes)
    a.add_post("/api/v1/remotes", add)
    a.add_delete("/api/v1/remotes/{id}", remove)
    a.add_post("/api/v1/remotes/{id}/learn", learn)
    a.add_get("/api/v1/remotes/{id}/learn", learn_status)
    a.add_delete("/api/v1/remotes/{id}/learn", learn_stop)
    a.add_post("/api/v1/remotes/{id}/send", send)
    a.add_post("/api/v1/remotes/{id}/reset", reset)
    a.add_put("/api/v1/remotes/{id}/colours", colours)
