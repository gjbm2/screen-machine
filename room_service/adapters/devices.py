"""Devices with no working adapter yet (ROOM_STATE_MODEL §5.3): placeholders with their members and values, so the
config can name them, typos are still rejected at load, their rows are skipped and status shows available: false.
Each becomes a real adapter as its hardware is configured (RM4 Pro, a DMX plug, SoCo, TV IP Control writes).

Also the site's room names (routes/data/rooms.json), for adapters that word a member by the room it is in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..roomstate.contract import Placeholder

REPO = Path(__file__).resolve().parents[2]
ROOM_DIR = REPO / "config" / "room"
ROOMS_FILE = REPO / "routes" / "data" / "rooms.json"


def _read(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def first_word(member: str) -> str:
    """"north-screen" -> "North": a member's name in page words."""
    return member.replace(".", "-").split("-")[0].capitalize()


class ConfigDevice(Placeholder):
    """A placeholder worded by its device file: "label" (the subsystem's), "category", "value_labels" and
    "member_label" (a format with {member} and {name}, the member's first word capitalised)."""

    def __init__(self, subsystem: str, raw: dict) -> None:
        super().__init__(subsystem, [str(m) for m in raw.get("members") or []], raw.get("values"))
        self.subsystem_label = raw.get("label") or None
        self.category = str(raw.get("category") or "device")
        self.value_labels = {str(k): str(v) for k, v in (raw.get("value_labels") or {}).items()}
        self.member_label = raw.get("member_label")

    def label(self, member: str) -> str:
        return self.member_label.format(member=member, name=first_word(member)) if self.member_label else member

    def value_schema(self, member: str) -> dict:
        s = super().value_schema(member)
        if s:
            s["x-ui"] = {"label": self.label(member), **({"labels": self.value_labels} if self.value_labels else {})}
        return s


def tv(path: str | Path | None = None) -> ConfigDevice | None:
    """tv.<screen>: the screens that have a TV, from config/room/tv.json; None without the file (no TVs)."""
    raw = _read(Path(path) if path else ROOM_DIR / "tv.json")
    return ConfigDevice("tv", raw) if raw and raw.get("members") else None


_rooms: dict[str, Any] = {"mtime": None, "raw": {}}


def rooms(path: str | Path | None = None) -> dict:
    """rooms.json's rooms (no comments), re-read when it changes; {} when it can't be read."""
    p = Path(path) if path else ROOMS_FILE
    try:
        m = p.stat().st_mtime
    except OSError:
        return {}
    if path is None and _rooms["mtime"] == m:
        return _rooms["raw"]
    raw = {k: v for k, v in (_read(p) or {}).items() if not k.startswith("_") and isinstance(v, dict)}
    if path is None:
        _rooms.update(mtime=m, raw=raw)
    return raw


def room_of(key: str, item: str, path: str | Path | None = None) -> str | None:
    """The name of the room whose `key` list holds `item` ("blinds", "ch02" -> "Drawing room"), else None."""
    for rk, spec in rooms(path).items():
        if item in (spec.get(key) or []):
            return str(spec.get("name") or rk)
    return None


def _sonos_members() -> list[str]:
    try:
        zones = json.loads((ROOM_DIR / "sonos.json").read_text())["zones"]
    except (OSError, ValueError, KeyError):
        return []                                         # no zones configured: nothing to name
    return [f"{z}.{m}" for z in zones for m in ("volume", "playback", "now_playing")]


def _named(p: Placeholder, label: str, **attrs) -> Placeholder:
    p.subsystem_label = label
    for k, v in attrs.items():
        setattr(p, k, v)
    return p


def placeholders() -> list[Placeholder]:
    return [
        *([t] if (t := tv()) else []),                                  # verify-only until writes are approved (Q6)
        _named(Placeholder("mirror_ball", ["main"], ["spinning", "stopped"]), "Glitterball"),   # RM4 Pro (Q3)
        Placeholder("plug", ["dmx_power"], ["on", "off"]),
        _named(Placeholder("sonos", _sonos_members()), "Music", room_group="music"),   # SoCo off or no sonos.json
    ]
