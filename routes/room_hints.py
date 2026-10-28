"""Auto-off hints on a room's screens (owner, 28 Oct): while Auto is off and the room hears something a mode is made
for (Sonos playing, the piano played over MIDI), the room's screens say how to turn Auto on or which mode to pick.

Config: config/room/auto_hints.json (re-read when it changes). Each hint names one show_service situations word
(GET /show/api/v1/situations readings, the word's default params), how long it must hold (``for_s``, from the
reading's ``since_s``), the modes the room may be in for it (``from``) and the modes it offers; the first hint that
holds wins. A hint shows for ``show_s`` when it starts holding, again after ``again_after_s`` while it still holds,
and is taken down at once when it stops (Auto on, a mode picked, the music stopped). The overlay is
routes/overlays/overlay_auto_hint.html.j2 through routes.display.send_overlay. It only reads: nothing here changes the
room. Bounded; never raises into Flask. AUTO_HINTS=0 turns it off.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from utils.logger import debug, error, info

from routes import room_client

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "room" / "auto_hints.json"
ROOMS = ROOT / "routes" / "data" / "rooms.json"
TEMPLATE = "overlay_auto_hint.html.j2"
CLEAR_HTML = "<div></div>"

_thread: Optional[threading.Thread] = None
_cfg: dict = {"mtime": None, "cfg": {}}


def _num(v: Any, default: float = 0.0) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return default
    return float(v)


def load_config(path: Path = CONFIG) -> dict:
    """auto_hints.json, re-read when its mtime changes; the last good one on a bad edit, {} if there never was one."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return _cfg["cfg"]
    if mt != _cfg["mtime"]:
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                _cfg["cfg"] = cfg
        except (OSError, ValueError) as e:
            error(f"[room hints] {path.name}: {e}")
        _cfg["mtime"] = mt
    return _cfg["cfg"]


def room_screens(room: str) -> list[str]:
    try:
        d = json.loads(ROOMS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    s = (d.get(room) or {}).get("screens") if isinstance(d, dict) else None
    return [x for x in s if isinstance(x, str)] if isinstance(s, list) else []


def pick(cfg: dict, auto: Any, current: Any, readings: dict) -> Optional[dict]:
    """The hint that holds now, or None. Only while Auto is known to be off and the room is in a known mode."""
    if auto is not False or not isinstance(current, str):
        return None
    for h in cfg.get("hints") or []:
        if not isinstance(h, dict) or not isinstance(h.get("id"), str):
            continue
        offer = [m for m in h.get("modes") or [] if isinstance(m, str)]
        if not offer or current in offer:
            continue
        frm = h.get("from")
        if isinstance(frm, list) and frm and current not in frm:
            continue
        r = readings.get(h.get("word")) or {}
        if r.get("value") is True and _num(r.get("since_s")) >= _num(h.get("for_s")):
            return h
    return None


class Hints:
    """What is on the screens: one hint at a time, by id; ``now`` is monotonic seconds."""

    def __init__(self, send=None) -> None:
        self.send = send or _send
        self.showing: Optional[tuple[str, float]] = None      # (hint id, shown at)
        self.last_shown: dict[str, float] = {}

    def step(self, cfg: dict, hint: Optional[dict], screens: list[str], now: float) -> None:
        show_s, again_s = _num(cfg.get("show_s"), 60.0), _num(cfg.get("again_after_s"), 1800.0)
        up = self.showing if self.showing and now - self.showing[1] < show_s else None
        if up and (hint is None or hint["id"] != up[0]):
            self.send(None, screens, 0)                         # it stopped holding: take it down now
            info(f"[room hints] {up[0]} taken down")
            up = None
        self.showing = up
        if hint is None or up is not None:
            return
        last = self.last_shown.get(hint["id"])
        if last is not None and now - last < again_s:
            return
        self.send(hint, screens, show_s)
        self.showing = (hint["id"], now)
        self.last_shown[hint["id"]] = now
        info(f"[room hints] {hint['id']} on {', '.join(screens)}")


_qr: dict = {}


def _room_page() -> tuple[str, str]:
    """(the Room page's URL, its QR code as a data URL)."""
    base = (os.getenv("VITE_APP_LAN_URL") or os.getenv("VITE_URL") or "").rstrip("/")
    url = f"{base}/room"
    if _qr.get("url") != url:
        from routes.utils import qr_data_url
        _qr.update(url=url, img=qr_data_url(url))
    return url, _qr["img"]


def _send(hint: Optional[dict], screens: list[str], show_s: float) -> None:
    from routes.display import send_overlay
    if not screens:
        return
    if hint is None:
        send_overlay(html=CLEAR_HTML, screens=screens, duration=1000, clear=True)
        return
    full = room_client._modes_full()
    modes = [{"label": (full.get(m) or {}).get("label") or m, "voice": (full.get(m) or {}).get("voice") or ""}
             for m in hint["modes"]]
    url, qr = _room_page()
    send_overlay(html=TEMPLATE, screens=screens, duration=int(show_s * 1000), clear=True,
                 substitutions={"TITLE": hint.get("title") or "", "LINE": hint.get("line") or "", "MODES": modes,
                                "ROOM_URL": url.split("://", 1)[-1], "ROOM_QR": qr,
                                "DURATION": int(show_s)})


def _tick(state: Hints) -> float:
    cfg = load_config()
    ap = room_client._get("/show/api/v1/autopilot", timeout=3.0)
    sit = room_client._get("/show/api/v1/situations", timeout=3.0)
    room = (ap or {}).get("room") or {}
    if isinstance(ap, dict) and room.get("transition_running"):
        return _num(cfg.get("check_s"), 3.0)                  # a mode change runs: wait for where it lands
    hint = None
    if isinstance(ap, dict) and isinstance(sit, dict):
        hint = pick(cfg, ap.get("on"), room.get("current"), sit.get("readings") or {})
    state.step(cfg, hint, room_screens(str(cfg.get("room") or "")), time.monotonic())
    return _num(cfg.get("check_s"), 3.0)


def start() -> None:
    """Start the hint loop on its own daemon thread (once; AUTO_HINTS=0: never)."""
    global _thread
    if os.environ.get("AUTO_HINTS", "1").strip().lower() in ("0", "false", "off", "no"):
        info("[room hints] off (AUTO_HINTS=0)")
        return
    if _thread is not None and _thread.is_alive():
        return

    def main() -> None:
        state = Hints()
        time.sleep(20.0)                                        # room_service and show come up first
        while True:
            try:
                wait = _tick(state)
            except Exception as e:                              # never into Flask; try again next round
                debug(f"[room hints] {type(e).__name__}: {e}")
                wait = 10.0
            time.sleep(max(1.0, wait))

    _thread = threading.Thread(target=main, name="room-hints", daemon=True)
    _thread.start()
