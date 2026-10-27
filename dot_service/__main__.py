"""dot_service: the e-ink tags (MindReset Dot. Quote/0) that show a room's state at a glance.

Every POLL_S it reads room_service (:8700: mode, screens, Sonos), show_service (:8703: Auto, mic, AI listening, the
music player, what the room hears, palette, feel, tempo, key), the renderer (:8702: the live scene) and the room's
scheduler variables (Flask :5000: today's art), draws each tag's card (render.py) and sends it through Dot.'s cloud
Image API (the device has no LAN API).

When it sends:
- status (mode, Auto, mic, AI, live) changed: as soon as it has held SETTLE_S;
- anything else: once per refresh period, refresh_s.live while a screen of the room is live, else
  refresh_s.normal; the card is redrawn then even if nothing changed, so its clock says how fresh it is.
The tag's own wake interval follows the same period (Dot. Settings API), and its sleep window is the config's.
Sends are at least MIN_GAP_S apart.

Config: config/room/dot.json (hot-reloaded). API key: ~/.config/screen-machine/dot.json {"api_key"}.
HTTP on 127.0.0.1 (DOT_PORT): /healthz, /api/v1/status, /preview/<device>.png, POST /api/v1/push (send now).
Run by scripts/ctl (screen session sm-dot, deploy/sm-dot.env).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
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
STREAM = os.environ.get("DOT_STREAM_URL", "http://127.0.0.1:8702")
APP = os.environ.get("DOT_APP_URL", "http://127.0.0.1:5000")
OWN_STREAM = "/show/api/v1/piano/stream/"   # Sonos playing the music player's own stream: the player names it

POLL_S = 2.0
SETTLE_S = 3.0          # a status change must hold this long (a mode switch passes through states)
MIN_GAP_S = 15.0        # e-ink refresh flashes; coalesce bursts
RETRY_S = 30.0
DEVICE_STATUS_S = 300.0
SLOW_S = 60.0           # scheduler variables, mode labels
CATALOG_S = 600.0       # scene labels
CONFIDENT = 0.4         # tempo and key shown only above this confidence
DEFAULT_REFRESH = {"live": 60, "normal": 900}

log = logging.getLogger("dot_service")


def first_quoted(text: str) -> str:
    """The first 'quoted' phrase in text ("" if none), without trailing punctuation inside the quotes."""
    m = re.search(r"[‘'\"“]([^'’\"”]{3,80}?)[,.;:]?[’'\"”]", text)
    return m.group(1).strip() if m else ""


def mmss(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def hms_s(v: str) -> Optional[float]:
    """Sonos "0:03:12" -> 192.0."""
    try:
        h, m, s = (int(x) for x in str(v).split(":"))
        return float(h * 3600 + m * 60 + s)
    except ValueError:
        return None


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
        self.settings_sent: Optional[dict] = None

    @property
    def device(self) -> str:
        return self.spec["device"]

    def refresh_s(self, live: bool) -> float:
        r = {**DEFAULT_REFRESH, **(self.spec.get("refresh_s") or {})}
        return float(r["live" if live else "normal"])


def status_of(c: Card) -> tuple:
    return (c.mode, c.changing_from, c.auto, c.mic, c.ai, c.live)


class DotService:
    def __init__(self) -> None:
        self.cfg_mtime = 0.0
        self.tags: dict[str, Tag] = {}
        self.http: Optional[aiohttp.ClientSession] = None
        self.modes: dict = {}
        self.modes_at = 0.0
        self.catalog: dict = {}
        self.catalog_at = 0.0
        self.sched: dict[str, tuple[float, dict]] = {}
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
                t.spec, t.sent, t.settings_sent = s, None, None   # changed spec: send again
            self.tags[s["device"]] = t
        log.info("config: %d tag(s)", len(self.tags))

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {json.loads(KEY_FILE.read_text())['api_key']}"}

    # ---- sources
    async def get(self, url: str) -> Any:
        assert self.http
        async with self.http.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
            r.raise_for_status()
            return await r.json()

    async def soft(self, url: str, default: Any = None) -> Any:
        """A source the card can do without (the renderer is on-demand, Flask may restart)."""
        try:
            return await self.get(url)
        except Exception as e:
            log.debug("%s: %s", url, e)
            return default

    async def sched_vars(self, room: str) -> dict:
        at, values = self.sched.get(room, (0.0, {}))
        if time.time() - at > SLOW_S:
            sched = await self.soft(f"{APP}/api/rooms/{room}/schedule")
            if sched:
                values = {v["name"]: v.get("value") for v in sched.get("variables", [])}
            self.sched[room] = (time.time(), values)
        return values

    async def scene_labels(self) -> dict:
        if time.time() - self.catalog_at > CATALOG_S:
            cat = await self.soft(f"{STREAM}/api/catalog")
            if cat:
                self.catalog = {k: (v.get("label") or k) for k, v in (cat.get("presets", {}).get("scenes") or {}).items()}
            self.catalog_at = time.time()
        return self.catalog

    async def card_for(self, tag: Tag, src: dict) -> Card:
        status, auto, player, show, card, look, listening = (
            src[k] for k in ("status", "auto", "player", "show", "card", "look", "listening"))
        room_id = tag.spec["room"]
        room = next((r for r in status.get("rooms", []) if r.get("id") == room_id), {})
        screens = room.get("screens", [])

        labels = {k: (v.get("label") or k) for k, v in self.modes.get("modes", {}).items()}
        mode = status.get("mode", {})
        current, target = mode.get("current") or "", mode.get("target")
        shown = labels.get(target or current, target or current)
        changing_from = labels.get(current, current) if target and target != current else ""
        live = any(((status.get("screens", {}).get(s) or {}).get("observed") or {}).get("layer") == "live"
                   for s in screens)

        mic = (show or {}).get("mic") or {}
        mic_on = bool(mic.get("on"))
        ai_on = bool((listening or {}).get("on"))
        note = f"Mic: {mic['why'][:1].lower()}{mic['why'][1:]}" if mic_on and mic.get("why") else ""

        # What is playing: the music player, else the room's Sonos (unless it plays the player's own stream).
        title = by = left = ""
        progress: Optional[float] = None
        paused = False
        cur = player.get("current") or {}
        if player.get("state") in ("playing", "paused", "preparing") and cur.get("title"):
            title, by, paused = cur["title"], cur.get("composer") or "", player["state"] == "paused"
            el, du = player.get("elapsed_s"), player.get("duration_s") or cur.get("duration_s")
            if el is not None and du:
                left, progress = f"{mmss(du - el)} left", el / du
        else:
            np = ((status.get("subsystems", {}).get("sonos", {}).get("believed") or {})
                  .get(f"{room_id}.now_playing") or {})
            if np.get("state") in ("playing", "paused") and np.get("title") \
                    and OWN_STREAM not in (np.get("track_id") or ""):
                title, by, paused = np["title"], np.get("artist") or "", np["state"] == "paused"
                el, du = hms_s(np.get("position", "")), hms_s(np.get("duration", ""))
                if el is not None and du:
                    left, progress = f"{mmss(du - el)} left", el / du

        # Live: the scene on the room's screens and the room's feel.
        scene = feel = hearing = ""
        if live:
            names = await self.scene_labels()
            sessions = ((await self.soft(f"{STREAM}/api/sessions", {})) or {}).get("sessions", [])
            on = []
            for s in sessions:
                if s.get("screen") in screens and s.get("state") != "ended":
                    n = s.get("current_scene") or s.get("scene")
                    if n and names.get(n, n) not in on:
                        on.append(names.get(n, n))
            scene = " / ".join(on)
            bits = []
            vibe = ((card or {}).get("vibe") or {}).get("label")
            pal = (card or {}).get("palette") or {}
            pal_label = next((c.get("label") for c in pal.get("choices", []) if c.get("name") == pal.get("current")),
                             pal.get("current"))
            moods = ", ".join((((look or {}).get("direction") or {}).get("moods") or [])[:2])
            clock = ((show or {}).get("clock") or {}).get("clock") or {}
            harmony = (show or {}).get("harmony") or {}
            for b in (vibe, pal_label, moods,
                      f"{round(clock['bpm'])} bpm" if clock.get("bpm") and (clock.get("conf") or 0) >= CONFIDENT else "",
                      f"{harmony['key']} {harmony.get('mode', '')}".strip()
                      if harmony.get("key") and (harmony.get("conf") or 0) >= CONFIDENT else ""):
                if b:
                    bits.append(str(b))
            feel = " · ".join(bits)
            hearing = (((card or {}).get("hearing") or {}).get("text")) or ""

        # Today's art, from the room's scheduler variables.
        theme = style = ""
        art = tag.spec.get("art") or {}
        if art:
            values = await self.sched_vars(room_id)
            t = art.get("theme") or {}
            theme = str(values.get(t.get("variable")) or "")
            if theme and t.get("quoted"):
                theme = first_quoted(theme) or str(values.get(t.get("else")) or "")
            style = str(values.get(art.get("style")) or "")

        hint = {} if live else (tag.spec.get("hint") or {})
        return Card(room=room.get("name") or room_id, mode=shown, auto=bool(auto.get("on")), mic=mic_on, ai=ai_on,
                    live=live, changing_from=changing_from, title=title, by=by, left=left, progress=progress,
                    paused=paused, hearing=hearing, scene=scene, feel=feel, theme=theme.strip(),
                    style=style.strip(), note=note, say=hint.get("say", ""), tap=hint.get("tap", ""))

    # ---- send
    async def send(self, tag: Tag, card: Card) -> None:
        assert self.http
        card = dataclasses.replace(card, stamp=time.strftime("%H:%M"))
        buf = io.BytesIO()
        render(card).save(buf, "PNG")
        png = buf.getvalue()
        body = {"refreshNow": True, "image": base64.b64encode(png).decode(), "border": 0,
                "ditherType": "NONE", "taskAlias": card.room}
        if tag.spec.get("link"):
            body["link"] = tag.spec["link"]
        async with self.http.post(f"{DOT_API}/device/{tag.device}/image", json=body, headers=self.headers(),
                                  timeout=aiohttp.ClientTimeout(total=20)) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}: {text[:200]}")
        tag.png, tag.sent, tag.sent_at, tag.last_error = png, card, time.time(), ""
        log.info("%s sent: %s", tag.device, card)

    async def apply_settings(self, tag: Tag, live: bool) -> None:
        """The tag's wake interval follows the refresh period; its sleep window is the config's."""
        assert self.http
        ms = int(tag.refresh_s(live) * 1000)
        want: dict = {"interval": {"powerMs": ms, "batteryMs": ms}}
        sl = tag.spec.get("sleep")
        if sl:
            want["sleep"] = {"enabled": True, "start": sl["start"], "end": sl["end"]}
        if want == tag.settings_sent:
            return
        async with self.http.post(f"{DOT_API}/device/{tag.device}/settings", json=want, headers=self.headers(),
                                  timeout=aiohttp.ClientTimeout(total=20)) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"settings HTTP {r.status}: {text[:200]}")
        tag.settings_sent = want
        log.info("%s settings: %s", tag.device, want)

    async def device_status(self, tag: Tag) -> None:
        assert self.http
        try:
            async with self.http.get(f"{DOT_API}/device/{tag.device}/status", headers=self.headers(),
                                     timeout=aiohttp.ClientTimeout(total=20)) as r:
                tag.device_status = (await r.json()).get("status")
        except Exception as e:
            log.debug("device status %s: %s", tag.device, e)

    # ---- loop
    async def tick(self) -> None:
        self.load_config()
        if time.time() - self.modes_at > SLOW_S:
            self.modes = await self.get(f"{ROOM}/api/v1/modes")
            self.modes_at = time.time()
        keys = ("status", "auto", "player", "show", "card", "look", "listening")
        vals = await asyncio.gather(
            self.get(f"{ROOM}/api/v1/status"), self.get(f"{SHOW}/api/v1/autopilot"),
            self.get(f"{SHOW}/api/v1/player"), self.soft(f"{SHOW}/api/v1/state", {}),
            self.soft(f"{SHOW}/api/v1/card", {}), self.soft(f"{SHOW}/api/v1/look", {}),
            self.soft(f"{SHOW}/api/v1/listening", {}))
        src = dict(zip(keys, vals))
        now = time.time()
        for tag in list(self.tags.values()):
            card = await self.card_for(tag, src)
            if card != tag.card:
                urgent_change = tag.card is None or status_of(card) != status_of(tag.card)
                tag.card = card
                if urgent_change:
                    tag.card_since = now
            if not tag.png:
                buf = io.BytesIO()
                render(card).save(buf, "PNG")
                tag.png = buf.getvalue()
            try:
                await self.apply_settings(tag, card.live)
            except Exception as e:
                log.warning("%s %s", tag.device, e)
            urgent = tag.sent is None or status_of(card) != status_of(tag.sent)
            due = (now - tag.sent_at >= MIN_GAP_S and now - tag.last_error_at >= RETRY_S and (
                (urgent and now - tag.card_since >= SETTLE_S) or now - tag.sent_at >= tag.refresh_s(card.live)))
            if due:
                try:
                    await self.send(tag, card)
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
                "card": dataclasses.asdict(t.card) if t.card else None,
                "sent_age_s": round(now - t.sent_at) if t.sent_at else None,
                "refresh_s": t.refresh_s(bool(t.card and t.card.live)),
                "settings": t.settings_sent,
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
