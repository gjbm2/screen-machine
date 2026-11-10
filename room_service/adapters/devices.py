"""Devices with no working adapter yet (ROOM_STATE_MODEL §5.3): placeholders with their members and values, so the
config can name them, typos are still rejected at load, their rows are skipped and status shows available: false.
Each becomes a real adapter as its hardware is configured (RM4 Pro, a DMX plug, SoCo, TV IP Control writes).

Also the site's room names (the deployment's rooms.json), for adapters that word a member by the room it is in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import smconfig

from ..roomstate.contract import Placeholder

REPO = Path(__file__).resolve().parents[2]
ROOM_DIR = smconfig.path("room")              # the deployment's room/: tv.json, sonos.json
ROOMS_FILE = smconfig.path("rooms.json")


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
    """tv.<screen>: the screens that have a TV, from the deployment's room/tv.json; None without the file (no TVs)."""
    raw = _read(Path(path) if path else ROOM_DIR.joinpath("tv.json"))
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
        zones = json.loads(ROOM_DIR.joinpath("sonos.json").read_text())["zones"]
    except (OSError, ValueError, KeyError):
        return []                                         # no zones configured: nothing to name
    return [f"{z}.{m}" for z in zones for m in ("volume", "playback", "now_playing")]


def _named(p: Placeholder, label: str, **attrs) -> Placeholder:
    p.subsystem_label = label
    for k, v in attrs.items():
        setattr(p, k, v)
    return p


def _rf_has(subsystem: str) -> bool:
    """A device file in the deployment's room/rf/ is `subsystem` (an unreadable file counts if its text names it,
    so a broken glitterball.json still leaves the placeholder its modes need)."""
    from .broadlink_rf import RF_DIR                      # lazy: tests monkeypatch it
    try:
        files = sorted(Path(RF_DIR).glob("*.json"))
    except OSError:
        return False
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        try:
            raw = json.loads(text)
        except ValueError:
            if f'"{subsystem}"' in text:
                return True
            continue
        if isinstance(raw, dict) and raw.get("subsystem") == subsystem:
            return True
    return False


def placeholders() -> list[Placeholder]:
    """Only what this deployment has: the glitterball when its room/rf/ has a mirror_ball file (the RM4 Pro adapter
    replaces it), the DMX power plug with the dmx capability on, Sonos (empty without sonos.json), the TVs (tv.json)."""
    return [
        *([t] if (t := tv()) else []),                                  # verify-only until writes are approved (Q6)
        *([_named(Placeholder("mirror_ball", ["main"], ["spinning", "stopped"]), "Glitterball")]   # RM4 Pro (Q3)
          if _rf_has("mirror_ball") else []),
        *([Placeholder("plug", ["dmx_power"], ["on", "off"])] if smconfig.enabled("dmx") else []),
        _named(Placeholder("sonos", _sonos_members()), "Music", room_group="music"),   # SoCo off or no sonos.json
    ]
