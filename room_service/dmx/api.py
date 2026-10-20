"""Routes for the virtual DMX devices (registered from api.py).

  GET  /api/v1/dmx          the patch (profiles, devices, addresses), Art-Net input, latest decoded values, the show
                            bus and the Hue output. Open, like status.
  WS   /api/v1/dmx/live     the show bus (K3 hello, then show.state per Art-Net packet): role "monitor" for
                            browsers (latest value only). Open, like status.
  GET  /api/v1/hue          the paired bridge and the patched lights as the bridge reports them (cached 5 s). Open.
  GET  /api/v1/fixtures     the Room page's view: one row per virtual device (patch + live DMX + the bridge's reading
                            + the room-state target), plus Art-Net / bridge / bus summaries. Open. Lamps are driven
                            through PUT /api/v1/room/targets {values: {"hue.ne": "dmx" | "off" | {on, bri, hex|xy}}}.
  POST /api/v1/fixtures/{id}/identify   the lamp breathes (bridge identify). Open (operate).
  POST /api/v1/dmx/test     Open. Inject test values as if Maestro had sent them (they count as fresh Art-Net,
                            so a device whose hue target is "dmx" lights up):
                            {"device": "hue.ne", "values": {"rgb": [1, 0, 0]}} or {"channels": {"33": 255}};
                            {"blackout": true} zeroes the universe.
  GET  /api/v1/dmx/fake     Open. The test show (fake_artnet.py: a stand-in for Maestro that sends real Art-Net
                            to our own receiver): running, the song (style, bpm, section), packets, and how many
                            lamps follow it and renderers listen.
  POST /api/v1/dmx/fake     Open (operate). {"action": "start", "song"?: "pop", "bpm"?: 124} | {"action": "stop"} |
                            show_synth's controls while it runs: {"bpm": 130} {"ramp": {"to_bpm": 90, "over_s": 16}}
                            {"song": "techno"} {"pulses": false} {"next_section": true}. 409 when real Art-Net is live.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable

from ..hue.colour import display_hex, rgb_to_hex

from aiohttp import web

from .patch import encode

if TYPE_CHECKING:
    from ..service import RoomService


def _err(status: int, code: str, detail: str = "") -> web.Response:
    return web.json_response({"error": code, "detail": detail}, status=status)


def register(app: web.Application, svc: "RoomService", is_admin: Callable[[web.Request], bool]) -> None:
    cache: dict[str, Any] = {"at": 0.0, "data": None}

    def stack():
        return getattr(svc, "dmx", None)

    async def get_dmx(request: web.Request) -> web.Response:
        s = stack()
        if s is None:
            return web.json_response({"enabled": False})
        return web.json_response({"enabled": True, **s.status()})

    async def get_hue(request: web.Request) -> web.Response:
        s = stack()
        if s is None or s.hue is None:
            return web.json_response({"available": False, "detail": None if s is None else s.hue_error})
        if cache["data"] is None or time.monotonic() - cache["at"] > 5.0:
            out = s.hue
            try:
                inv = await out.client.inventory()
            except Exception as e:
                return _err(502, "bridge_unreachable", str(e))
            patched = {d.extra.get("light"): d for d in s.patch.of_kind("hue")}
            cache["data"] = {
                "bridge": {"id": out.secrets.bridge_id, "host": out.secrets.host, "name": out.secrets.name,
                           "model": out.secrets.model},
                "devices": [{"device": patched[li["id"]].id, "label": patched[li["id"]].label, **li}
                            for li in inv["lights"] if li["id"] in patched],
                "areas": inv["areas"],
                "streamable_lights": len([li for li in inv["lights"] if li["renderer"]])}
            cache["at"] = time.monotonic()
        return web.json_response({"available": True, **cache["data"], "output": s.hue.status()})

    async def post_test(request: web.Request) -> web.Response:
        s = stack()
        if s is None:
            return _err(409, "dmx_disabled")
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "bad_json")
        if not isinstance(body, dict):
            return _err(400, "bad_request", "expected an object")
        if body.get("blackout"):
            s.rx.inject({ch: 0 for ch in range(1, 513)})
            return web.json_response({"ok": True, "channels": 512})
        chans: dict[int, int] = {}
        try:
            if "device" in body:
                dev = s.patch.devices.get(body["device"])
                if dev is None:
                    return _err(404, "unknown_device", str(body["device"]))
                chans.update(encode(dev, body.get("values") or {}))
            for k, v in (body.get("channels") or {}).items():
                if not 1 <= int(k) <= 512:
                    return _err(400, "bad_request", f"channel {k}: 1-512")
                chans[int(k)] = int(v)
        except (TypeError, ValueError) as e:
            return _err(400, "bad_request", str(e))
        if not chans:
            return _err(400, "bad_request", "nothing to set: device+values, channels or blackout")
        s.rx.inject(chans)
        return web.json_response({"ok": True, "channels": {str(k): v for k, v in sorted(chans.items())}})

    lights_cache: dict[str, Any] = {"at": 0.0, "by_id": {}, "error": None}

    async def bridge_lights(s) -> dict[str, dict]:
        if time.monotonic() - lights_cache["at"] > 1.0:
            lights_cache["at"] = time.monotonic()
            try:
                lights_cache["by_id"] = {li["id"]: li for li in await s.hue.client.get("light")}
                lights_cache["error"] = None
            except Exception as e:
                lights_cache["error"] = str(e)
        return lights_cache["by_id"]

    def hue_row(s, dev, room_members: dict, lights: dict) -> dict:
        from ..adapters.hue import _canon_light
        m = dev.id.split(".", 1)[1]
        out, ad = s.hue, s.hue_adapter
        li = lights.get(dev.extra.get("light"))
        rs = room_members.get(m) or {}
        armed = out is not None and dev.id in out.wanted
        state = out.state if out is not None else "unavailable"
        show = ad is not None and ad.mode == "show"         # hue.streamer: show_service streams (SHOW_ENGINE §9.4)
        if show:
            armed = dev.id in ad.show_wanted
            state = ("taken_over" if ad.show_state == "taken_over"
                     else "streaming" if li is not None and li.get("mode") == "streaming" else "waiting_input")
        light = None
        if li is not None:
            canon = _canon_light(li)
            light = {"on": li.get("on", {}).get("on"), "bri": round(li.get("dimming", {}).get("brightness", 0)),
                     "mode": li.get("mode"), "value": canon, "hex": display_hex(canon),
                     "reachable": True}
        if out is None:
            status, words = "unavailable", "Bridge not connected"
        elif armed and state == "streaming":
            status, words = "following", ("Following the music" if show else
                                          "Following the test show" if s.rx.source == "fake" else "Following Maestro")
        elif armed and state == "starting":
            status, words = "armed", "Connecting to the lamps…"
        elif armed and state in ("waiting_input", "idle"):
            status, words = "armed", "Ready: waiting for the music" if show else "Ready: waiting for Maestro"
        elif armed and state == "taken_over" or (li is not None and li.get("mode") == "streaming"):
            status, words = "taken_over", "Taken over by another app"
        elif armed and state == "error":
            status, words = "error", out.detail or "Problem streaming"
        elif li is None:
            status, words = "unreachable", lights_cache["error"] or "Not on the bridge"
        elif not li.get("on", {}).get("on"):
            status, words = "off", "Off"
        else:
            status, words = "house", "House lighting"
        return {"target": f"hue.{m}", "control": "dmx" if armed else "house", "status": status, "status_text": words,
                "light": light, "believed": rs.get("believed"), "desired": rs.get("desired"),
                "policy": rs.get("policy"), "drift": rs.get("drift"),
                "house_value": (ad.house.get(m) if ad is not None else None) or (light or {}).get("value"),
                "writable": ad is not None and not ad.dry_run}

    async def get_fixtures(request: web.Request) -> web.Response:
        s = stack()
        if s is None:
            return web.json_response({"enabled": False, "fixtures": []})
        fixtures, glob = s.patch.decode(s.rx.dmx)
        have_dmx = s.rx.packets > 0
        room = svc.coordinator.status()["subsystems"].get("hue", {}).get("members", {})
        lights = await bridge_lights(s) if s.hue is not None else {}
        renderers = [c.client_id for c in s.bus.clients if c.role == "renderer"] if s.bus else []
        rows = []
        for dev in sorted(s.patch.devices.values(), key=lambda d: d.address):
            vals = fixtures.get(dev.id) if have_dmx else None
            rgb = (vals or {}).get("rgb")
            row = {**dev.describe(), "profile_label": dev.profile.label,
                   "channels": [{"offset": i + 1, "address": dev.address + i, "attr": c.attr, "field": c.field}
                                for i, c in enumerate(dev.profile.channels)],
                   "dmx": vals, "dmx_hex": rgb_to_hex(*rgb) if rgb else None}
            if dev.kind == "hue":
                row["light_id"] = dev.extra.get("light")
                row.update(hue_row(s, dev, room, lights))      # `light` becomes the bridge's reading
            elif dev.kind == "screen":
                row.update(status="bus" if renderers else "no_renderer",
                           status_text="On the show bus" + (f" · {len(renderers)} renderer(s) listening" if renderers
                                                            else " · no renderer listening"))
            rows.append(row)
        h = s.hue
        return web.json_response({
            "enabled": True,
            "artnet": {**s.rx.status(), "global": glob if have_dmx else {}},
            "hue": ({"state": h.state, "detail": h.detail, "area": h.area_id, "bridge": h.secrets.name,
                     "host": h.secrets.host, "model": h.secrets.model, "reachable": h.client.reachable,
                     "error": lights_cache["error"]} if h is not None else {"state": "unavailable",
                                                                                "detail": s.hue_error}),
            "bus": s.bus.status() if s.bus else None,
            "patch_error": s.patch_error,
            "fixtures": rows})

    async def post_identify(request: web.Request) -> web.Response:
        s = stack()
        did = request.match_info["id"]
        if s is None or s.hue_adapter is None or did not in s.patch.devices or not did.startswith("hue."):
            return _err(404, "unknown_fixture", did)
        o = await s.hue_adapter.signal(did.split(".", 1)[1], "identify", {})
        if o.status != "ok":
            return _err(409, "identify_failed", o.detail)
        return web.json_response({"ok": True})

    def fake_view(s) -> dict:
        h = s.hue
        lamps = s.patch.of_kind("hue")
        return {**s.fake.status(),
                "lamps": {"total": len(lamps), "armed": len([d for d in lamps if h is not None and d.id in h.wanted]),
                          "following": len([d for d in lamps if h is not None and h.is_streaming(d.id)]),
                          "state": h.state if h is not None else "unavailable"},
                "renderers": len([c for c in s.bus.clients if c.role == "renderer"]) if s.bus else 0,
                "artnet": {"online": s.rx.online, "source": s.rx.source if s.rx.packets else None,
                           "last_from": s.rx.last_from}}

    async def get_fake(request: web.Request) -> web.Response:
        s = stack()
        if s is None:
            return web.json_response({"available": False})
        return web.json_response({"available": True, **fake_view(s)}, headers={"Cache-Control": "no-store"})

    async def post_fake(request: web.Request) -> web.Response:
        from stream_service.tools.show_synth import BPM_MAX, BPM_MIN
        s = stack()
        if s is None:
            return _err(409, "dmx_disabled")
        try:
            body = await request.json()
        except ValueError:
            return _err(400, "bad_json")
        if not isinstance(body, dict):
            return _err(400, "bad_request", "expected an object")
        action = body.pop("action", None)
        try:
            if action == "start":
                bpm = body.get("bpm")
                if bpm is not None and (isinstance(bpm, bool) or not isinstance(bpm, (int, float))
                                        or not BPM_MIN <= bpm <= BPM_MAX):
                    return _err(400, "bad_request", f"bpm: {BPM_MIN:.0f}-{BPM_MAX:.0f}")
                await s.fake.start(str(body.get("song") or s.fake.last[0]), bpm if bpm is not None else s.fake.last[1])
            elif action == "stop":
                s.fake.stop()
            elif action is None:
                s.fake.control(body)
            else:
                return _err(400, "bad_request", "action: start or stop")
        except RuntimeError as e:
            return _err(409, "artnet_live", str(e))
        except ValueError as e:
            return _err(400, "bad_request", str(e))
        return web.json_response({"available": True, **fake_view(s)})

    app.router.add_get("/api/v1/dmx/fake", get_fake)
    app.router.add_post("/api/v1/dmx/fake", post_fake)
    app.router.add_get("/api/v1/fixtures", get_fixtures)
    app.router.add_post("/api/v1/fixtures/{id}/identify", post_identify)
    async def dmx_live(request: web.Request) -> web.StreamResponse:
        """The show bus for browsers: every decoded fixture per Art-Net packet (send hello role "monitor")."""
        s = stack()
        if s is None or s.bus is None:
            return _err(404, "no_show_bus")
        return await s.bus.handle(request)

    app.router.add_get("/api/v1/dmx/live", dmx_live)
    app.router.add_get("/api/v1/dmx", get_dmx)
    app.router.add_get("/api/v1/hue", get_hue)
    app.router.add_post("/api/v1/dmx/test", post_test)
