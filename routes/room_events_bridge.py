"""Room -> scheduler: room_service's changes become scheduler events, so each screen's schedule can act on them
(owner, 17 Oct: "mode change sends events, scheduler acts on the event"; nothing is hard-coded here).

It is a client of room_service's published events socket (WS /api/v1/events, room_in_app's LAN door): each
`status` (coalesced, at most 4 a second) is reduced to a few facts (routes/room_client.room_facts) and, when one
changes, thrown to the screens' schedulers (routes/scheduler_utils.throw_event):

  _room_mode     to every room screen   payload {mode, label, from, source, initial}
  _room_auto     to every room screen   payload {on, from, initial}
  _screen_layer  to that screen         payload {screen, layer: "art" | "live", from, initial}

With mode zones (rooms.json "own_modes": true; room_service status "zones"), a zone's screens get _room_mode from
their zone's own mode (the payload adds "room"), and the site's mode goes only to the other screens. With none (every
deployment until a room opts in), everything is as before.

`source` says who changed the mode ("web", "autopilot", "schedule:<screen>", "voice", ...), so a schedule can ignore
its own changes. On every (re)connect the current facts go out once with initial: true, so a schedule always learns
where it stands. A schedule reads the payload as {{ _event.payload.<field> }} (e.g. set_var screen_layer). Bounded:
a lost socket retries with backoff (2 s .. 30 s); it never raises into Flask. ROOM_EVENTS_BRIDGE=0 turns it off.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Optional

from utils.logger import debug, error, info

from routes import room_client

TTL = "60s"
_thread: Optional[threading.Thread] = None


def diff(prev: Optional[dict], facts: dict, labels: Optional[dict] = None,
         zone_labels: Optional[dict] = None) -> list[tuple[str, str, dict]]:
    """The events (scope, key, payload) that take a schedule from ``prev`` to ``facts`` (room_client.room_facts);
    ``prev`` None: everything known, as initial. With mode zones (facts "zones"), a zone's screens get their zone's
    mode (labelled from ``zone_labels[room]``) and the site's mode goes only to the other screens."""
    out: list[tuple[str, str, dict]] = []
    initial = prev is None
    prev = prev or {}
    screens = sorted((facts.get("screens") or {}).keys())
    labels = labels or {}
    zones = facts.get("zones") or {}
    pzones = prev.get("zones") or {}
    zoned = {sid: room for room, z in zones.items() for sid in z.get("screens") or []}
    mode, pmode = facts.get("mode"), prev.get("mode")
    if mode is not None and (initial or mode != pmode):
        for sid in screens:
            if sid not in zoned:
                out.append((sid, "_room_mode", {"mode": mode, "label": labels.get(mode, mode), "from": pmode,
                                                "source": facts.get("mode_source"), "initial": initial}))
    for room, z in zones.items():                    # only with zones: each zone's switch, to its own screens
        zmode, zpmode = z.get("mode"), (pzones.get(room) or {}).get("mode")
        if zmode is None or not (initial or room not in pzones or zmode != zpmode):
            continue
        zl = (zone_labels or {}).get(room) or {}
        for sid in screens:
            if zoned.get(sid) == room:
                out.append((sid, "_room_mode", {"mode": zmode, "label": zl.get(zmode, zmode), "from": zpmode,
                                                "source": z.get("mode_source"), "initial": initial,
                                                "room": room}))
    on, pon = facts.get("auto"), prev.get("auto")
    if on is not None and (initial or on != pon):
        for sid in screens:
            out.append((sid, "_room_auto", {"on": on, "from": pon, "initial": initial}))
    for sid in screens:
        layer = ((facts.get("screens") or {}).get(sid) or {}).get("layer")
        player = ((prev.get("screens") or {}).get(sid) or {}).get("layer")
        if layer in ("art", "live") and (initial or layer != player):
            out.append((sid, "_screen_layer", {"screen": sid, "layer": layer, "from": player, "initial": initial}))
    return out


def _throw(events: list[tuple[str, str, dict]]) -> None:
    from routes.scheduler_utils import throw_event
    for scope, key, payload in events:
        try:
            throw_event(scope=scope, key=key, ttl=TTL, payload=payload)
        except Exception as e:                       # one bad scope must not stop the others
            error(f"[room bridge] throw {key} to {scope} failed: {e}")


async def _run() -> None:
    import aiohttp
    url = room_client.ROOM_URL.replace("http", "ws", 1) + "/api/v1/events"
    backoff = 2.0
    while True:
        prev: Optional[dict] = None
        labels = room_client.modes()
        zone_labels: dict[str, dict] = {}            # mode zone -> its labels (rooms.json "own_modes"; none: unused)
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url, heartbeat=30.0, max_msg_size=4 * 1024 * 1024) as ws:
                    info(f"[room bridge] connected to {url}")
                    backoff = 2.0
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            m = json.loads(msg.data)
                        except ValueError:
                            continue
                        if not isinstance(m, dict) or m.get("type") != "status" or not isinstance(m.get("data"), dict):
                            continue
                        facts = room_client.room_facts(m["data"])
                        if facts.get("mode") and facts["mode"] not in labels:
                            labels = room_client.modes() or labels   # modes.json is hot-reloaded: a new mode
                        for room, z in (facts.get("zones") or {}).items():   # mode zones only: their own labels
                            if z.get("mode") and z["mode"] not in zone_labels.get(room, {}):
                                zone_labels[room] = room_client.modes(room) or zone_labels.get(room, {})
                        events = diff(prev, facts, labels, zone_labels)
                        if events:
                            debug(f"[room bridge] {len(events)} event(s): {sorted({k for _, k, _ in events})}")
                            await asyncio.get_running_loop().run_in_executor(None, _throw, events)
                        prev = facts
        except Exception as e:
            debug(f"[room bridge] {url}: {type(e).__name__}: {e}; retrying in {backoff:.0f} s")
        await asyncio.sleep(backoff)
        backoff = min(30.0, backoff * 2.0)


def start_bridge() -> None:
    """Start the bridge on its own daemon thread and event loop (once; ROOM_EVENTS_BRIDGE=0: never)."""
    global _thread
    if os.environ.get("ROOM_EVENTS_BRIDGE", "1").strip().lower() in ("0", "false", "off", "no"):
        info("[room bridge] off (ROOM_EVENTS_BRIDGE=0)")
        return
    if _thread is not None and _thread.is_alive():
        return

    def main() -> None:
        try:
            asyncio.run(_run())
        except Exception as e:                       # never into Flask
            error(f"[room bridge] stopped: {e}")

    _thread = threading.Thread(target=main, name="room-events-bridge", daemon=True)
    _thread.start()
