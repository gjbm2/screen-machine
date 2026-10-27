"""dot_service: the e-ink tags (MindReset Dot. Quote/0) that show a room's mode, Auto and what is playing.

Every POLL_S it reads room_service (:8700, mode + Sonos), show_service (:8703, Auto + the music player) and the
room's scheduler variables (Flask :5000, the quiet line), draws each tag's card (render.py) and, when the card
changed and has held for SETTLE_S, sends it through Dot.'s cloud Image API (the device has no LAN API). Sends
are at least MIN_GAP_S apart; the latest card always goes out. On USB power the tag shows it within seconds; on
battery at its next wake (its interval is a device setting, Dot. Settings API).

Config: config/room/dot.json (hot-reloaded). API key: ~/.config/screen-machine/dot.json {"api_key"}.
HTTP on 127.0.0.1 (DOT_PORT): /healthz, /api/v1/status, /preview/<device>.png, POST /api/v1/push (send now).
Run by scripts/ctl (screen session sm-dot, deploy/sm-dot.env).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiohttp import web

from .render import Card, render

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "room" / "dot.json"
KEY_FILE = Path.home() / ".config" / "screen-machine" / "dot.json"
DOT_API = "https://dot.mindreset.tech/api/authV2/open"
ROOM = os.environ.get("DOT_ROOM_URL", "http://127.0.0.1:8700")
SHOW = os.environ.get("DOT_SHOW_URL", "http://127.0.0.1:8703")
APP = os.environ.get("DOT_APP_URL", "http://127.0.0.1:5000")
OWN_STREAM = "/show/api/v1/piano/stream/"   # Sonos playing the music player's own stream: the player names it

POLL_S = 2.0
SETTLE_S = 3.0          # a card must hold this long before it is sent (a mode switch passes through states)
MIN_GAP_S = 15.0        # e-ink refresh flashes; coalesce bursts, the latest card still goes out
RETRY_S = 30.0
DEVICE_STATUS_S = 300.0
QUIET_S = 60.0          # scheduler variables change daily

log = logging.getLogger("dot_service")


def first_quoted(text: str) -> str:
    """The first 'quoted' phrase in text ("" if none), without trailing punctuation inside the quotes."""
    m = re.search(r"[‘'\"“]([^'’\"”]{3,80}?)[,.;:]?[’'\"”]", text)
    return m.group(1).strip() if m else ""


class Tag:
    def __init__(self, spec: dict) -> None:
        self.spec = spec
        self.card: Optional[Card] = None
        self.card_since = 0.0
        self.sent: Optional[Card] = None
        self.sent_at = 0.0
        self.png = b""
        self.last_error = ""
        self.last_error_at = 0.0
        self.device_status: Any = None

    @property
    def device(self) -> str:
        return self.spec["device"]


class DotService:
    def __init__(self) -> None:
        self.cfg_mtime = 0.0
        self.tags: dict[str, Tag] = {}
        self.http: Optional[aiohttp.ClientSession] = None
        self.modes: dict = {}
        self.modes_at = 0.0
        self.quiet: dict[str, tuple[float, dict]] = {}
        self.source_error = ""
        self.started = time.time()

    # ---- config
    def load_config(self) -> None:
        try:
            m = CONFIG.stat().st_mtime
        except OSError as e:
            log.warning("no config: %s", e)
            return
        if m == self.cfg_mtime:
            return
        self.cfg_mtime = m
        specs = json.loads(CONFIG.read_text()).get("tags", [])
        old = self.tags
        self.tags = {}
        for s in specs:
            t = old.get(s["device"]) or Tag(s)
            if t.spec != s:
                t.spec, t.sent = s, None   # changed spec: send again
            self.tags[s["device"]] = t
        log.info("config: %d tag(s)", len(self.tags))

    def api_key(self) -> str:
        return json.loads(KEY_FILE.read_text())["api_key"]

    # ---- sources
    async def get(self, url: str) -> Any:
        assert self.http
        async with self.http.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            r.raise_for_status()
            return await r.json()

    async def quiet_vars(self, room: str) -> dict:
        at, values = self.quiet.get(room, (0.0, {}))
        if time.time() - at > QUIET_S:
            try:
                sched = await self.get(f"{APP}/api/rooms/{room}/schedule")
                values = {v["name"]: v.get("value") for v in sched.get("variables", [])}
            except Exception as e:  # keep the last values
                log.debug("schedule %s: %s", room, e)
            self.quiet[room] = (time.time(), values)
        return values

    async def card_for(self, tag: Tag, status: dict, auto: dict, player: dict) -> Card:
        room_id = tag.spec["room"]
        rooms = {r["id"]: r for r in status.get("rooms", [])}
        room_name = rooms.get(room_id, {}).get("name") or room_id

        labels = {k: (v.get("label") or k) for k, v in self.modes.get("modes", {}).items()}
        mode = status.get("mode", {})
        current = mode.get("current") or ""
        target = mode.get("target")
        shown = labels.get(target or current, target or current)
        changing_from = labels.get(current, current) if target and target != current else ""

        title = by = ""
        paused = False
        cur = player.get("current") or {}
        if player.get("state") in ("playing", "paused", "preparing") and cur.get("title"):
            title, by, paused = cur["title"], cur.get("composer") or "", player["state"] == "paused"
        else:
            np = ((status.get("subsystems", {}).get("sonos", {}).get("believed") or {})
                  .get(f"{room_id}.now_playing") or {})
            if np.get("state") in ("playing", "paused") and np.get("title") \
                    and OWN_STREAM not in (np.get("track_id") or ""):
                title, by, paused = np["title"], np.get("artist") or "", np["state"] == "paused"

        quiet_label = quiet_text = ""
        q = tag.spec.get("quiet")
        if q and not title:
            values = await self.quiet_vars(room_id)
            text = str(values.get(q.get("variable")) or "")
            if text and q.get("quoted"):
                text = first_quoted(text) or str(values.get(q.get("else")) or "")
            quiet_label, quiet_text = q.get("label", ""), text.strip()

        return Card(room=room_name, mode=shown, auto=bool(auto.get("on")), changing_from=changing_from,
                    title=title, by=by, paused=paused, quiet_label=quiet_label, quiet_text=quiet_text)

    # ---- send
    async def send(self, tag: Tag, card: Card) -> None:
        assert self.http
        img = render(card)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        tag.png = buf.getvalue()
        body = {"refreshNow": True, "image": base64.b64encode(tag.png).decode(), "border": 0,
                "ditherType": "NONE", "taskAlias": card.room}
        if tag.spec.get("link"):
            body["link"] = tag.spec["link"]
        async with self.http.post(f"{DOT_API}/device/{tag.device}/image", json=body,
                                  headers={"Authorization": f"Bearer {self.api_key()}"},
                                  timeout=aiohttp.ClientTimeout(total=20)) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}: {text[:200]}")
        tag.sent, tag.sent_at, tag.last_error = card, time.time(), ""
        log.info("%s sent: %s", tag.device, card)

    async def device_status(self, tag: Tag) -> None:
        assert self.http
        try:
            async with self.http.get(f"{DOT_API}/device/{tag.device}/status",
                                     headers={"Authorization": f"Bearer {self.api_key()}"},
                                     timeout=aiohttp.ClientTimeout(total=20)) as r:
                tag.device_status = (await r.json()).get("status")
        except Exception as e:
            log.debug("device status %s: %s", tag.device, e)

    # ---- loop
    async def tick(self) -> None:
        self.load_config()
        if time.time() - self.modes_at > 60:
            self.modes = await self.get(f"{ROOM}/api/v1/modes")
            self.modes_at = time.time()
        status, auto, player = await asyncio.gather(
            self.get(f"{ROOM}/api/v1/status"), self.get(f"{SHOW}/api/v1/autopilot"),
            self.get(f"{SHOW}/api/v1/player"))
        now = time.time()
        for tag in list(self.tags.values()):
            card = await self.card_for(tag, status, auto, player)
            if card != tag.card:
                tag.card, tag.card_since = card, now
                if not tag.png or tag.sent is None:
                    buf = io.BytesIO()
                    render(card).save(buf, "PNG")
                    tag.png = buf.getvalue()
            due = (tag.card != tag.sent and now - tag.card_since >= SETTLE_S
                   and now - tag.sent_at >= MIN_GAP_S and now - tag.last_error_at >= RETRY_S)
            if due:
                try:
                    await self.send(tag, tag.card)
                except Exception as e:
                    tag.last_error, tag.last_error_at = str(e), now
                    log.warning("%s send failed: %s", tag.device, e)

    async def run(self) -> None:
        self.http = aiohttp.ClientSession()
        last_dev = 0.0
        while True:
            try:
                await self.tick()
                self.source_error = ""
            except Exception as e:  # a source is down: keep what the tag shows
                if str(e) != self.source_error:
                    log.warning("sources: %s", e)
                self.source_error = str(e)
            if time.time() - last_dev > DEVICE_STATUS_S:
                last_dev = time.time()
                for tag in list(self.tags.values()):
                    await self.device_status(tag)
            await asyncio.sleep(POLL_S)

    def status(self) -> dict:
        now = time.time()
        return {
            "service": "dot_service", "uptime_s": round(now - self.started),
            "source_error": self.source_error or None,
            "tags": [{
                "device": t.device, "room": t.spec.get("room"), "link": t.spec.get("link"),
                "card": t.card.__dict__ if t.card else None,
                "sent_age_s": round(now - t.sent_at) if t.sent_at else None,
                "pending": t.card != t.sent,
                "last_error": t.last_error or None,
                "device_status": t.device_status,
            } for t in self.tags.values()],
        }


def make_app(svc: DotService) -> web.Application:
    async def healthz(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def status(_: web.Request) -> web.Response:
        return web.json_response(svc.status())

    async def preview(request: web.Request) -> web.Response:
        tag = svc.tags.get(request.match_info["device"])
        if not tag or not tag.png:
            raise web.HTTPNotFound()
        return web.Response(body=tag.png, content_type="image/png")

    async def push(_: web.Request) -> web.Response:
        out = {}
        for tag in svc.tags.values():
            if tag.card:
                try:
                    await svc.send(tag, tag.card)
                    out[tag.device] = "sent"
                except Exception as e:
                    out[tag.device] = str(e)
        return web.json_response(out)

    app = web.Application()
    app.router.add_get("/healthz", healthz)
    app.router.add_get("/api/v1/status", status)
    app.router.add_get("/preview/{device}.png", preview)
    app.router.add_post("/api/v1/push", push)
    return app


async def main(port: int, host: str) -> None:
    svc = DotService()
    runner = web.AppRunner(make_app(svc))
    await runner.setup()
    await web.TCPSite(runner, host, port).start()
    log.info("dot_service on %s:%d", host, port)
    await svc.run()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--log-level", default="INFO")
    a = ap.parse_args()
    logging.basicConfig(level=a.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main(a.port, a.host))
