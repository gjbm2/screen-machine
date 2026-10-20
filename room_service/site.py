"""The site's rooms (routes/data/rooms.json) as the Room page reads them.

[{id, name, screens, lights, blinds, sensors, effects}] in file order, skipping "_" keys, each list [] when absent.
effects are devices the Room page gives a card: {"power": target, "settings": [targets]} (a bare target string is
{"power": it, "settings": []}); the card's words come from the targets' schemas (GET /api/v1/room/describe).
Re-read when the file's mtime changes; a bad file keeps the last good list. (room_config.py derives the
"<room>:<kind>" mode groups from the same file.)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("room_service.site")

KINDS = ("screens", "lights", "blinds", "sensors")      # id lists; effects are objects (_effect)


def _effect(e) -> dict | None:
    if isinstance(e, str):
        return {"power": e, "settings": []}
    if isinstance(e, dict) and isinstance(e.get("power"), str):
        return {"power": e["power"], "settings": [s for s in e.get("settings") or [] if isinstance(s, str)]}
    return None


class SiteRooms:
    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.rooms: list[dict] = []
        self.error: str | None = None
        self._mtime: float | None = None

    def view(self) -> list[dict]:
        if self.path is None:
            return self.rooms
        try:
            m = self.path.stat().st_mtime
        except OSError as e:
            self.error = f"{self.path.name}: {e.strerror or e}"
            return self.rooms
        if m == self._mtime:
            return self.rooms
        self._mtime = m
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError("the top level must be an object of rooms")
        except (OSError, ValueError) as e:
            self.error = f"{self.path.name} not read: {e}"
            log.error("%s", self.error)
            return self.rooms
        out = []
        for rid, spec in raw.items():
            if rid.startswith("_") or not isinstance(spec, dict):
                continue
            room = {"id": rid, "name": str(spec.get("name") or rid)}
            for k in KINDS:
                ids = spec.get(k)
                room[k] = [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else []
            room["effects"] = [x for x in map(_effect, spec.get("effects") or []) if x]
            out.append(room)
        self.rooms, self.error = out, None
        return out
