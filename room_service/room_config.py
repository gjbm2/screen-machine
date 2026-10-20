"""The room's modes file (config/room/modes.json, ROOM_STATE_MODEL §2): load, watch, save.

- room_service is the only writer. A save is validated first, refused on a stale `base_revision`, written atomically
  and order-preserving (key order matters: it is part of the revision), and the previous file is kept in
  <state_dir>/config-history/ (the last 50).
- An edit made on disk is picked up by the watcher. A bad file keeps the previous config and reports the error; with
  no previous config (first boot) the room runs a one-mode fallback and PUT /mode answers 404 for real modes.
- JSON is the format; YAML also loads (it is a superset), so an old-style file still parses before it is rewritten.
- The site file routes/data/rooms.json says which room holds what. Each room gives read-only groups, holding only
  settable targets an adapter has (derive_groups); a bad rooms.json keeps the last good groups.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from .roomstate.contract import Adapter
from .roomstate.model import RoomConfig, revision, validate

log = logging.getLogger("room_service.room_config")

FALLBACK = {"default": "idle",
            "modes": {"idle": {"label": "No modes (config error)"}},
            "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}]}
HISTORY_KEEP = 50


class ConfigConflict(Exception):
    def __init__(self, current: str) -> None:
        super().__init__(f"the config changed since revision was read (now {current})")
        self.current = current


class ConfigInvalid(Exception):
    def __init__(self, errors: list[dict]) -> None:
        super().__init__(f"{len(errors)} problem(s)")
        self.errors = errors


def _settable(a: Adapter, member: str) -> bool:
    try:
        return not a.value_schema(member).get("readOnly")
    except Exception:
        return True


def room_groups(rooms: dict, adapters: Iterable[Adapter]) -> dict[str, list[str]]:
    """Groups from rooms.json, no table of kinds: for every list in a room, "<room>:<list key>" holds what its items
    name (unless the key is a subsystem's name), and "<room>:<subsystem>" what each subsystem has there. An item is a target ("hue.ne"), an object of them
    (an effect's {power, settings}), a subsystem's name (all its members: "light", the lights registered with
    show_service, whatever they are now), or a bare id: every member that is that id or starts with "<id>." in any
    subsystem (screen "north-screen" -> screens.north-screen, tv.north-screen, page.north-screen.timed_reload;
    blind "ch02" -> blinds.ch02). Members keyed by the room itself (Sonos zone "drawing-room") join
    "<room>:<subsystem>". Ids nothing has (a sensor) and read-only members are left out."""
    subs = {a.subsystem: a for a in adapters if a.kind != "action"}
    members = [(s, m) for s, a in subs.items() for m in a.members if _settable(a, m)]
    targets = {f"{s}.{m}" for s, m in members}

    def resolve(item: Any) -> list[str]:
        if isinstance(item, dict):
            return [t for v in item.values() for t in resolve(v)]
        if isinstance(item, list):
            return [t for v in item for t in resolve(v)]
        if not isinstance(item, str):
            return []
        if item in targets:
            return [item]
        if item in subs:                          # a subsystem by name: all its members, as they come and go
            return [f"{s}.{m}" for s, m in members if s == item]    # ("light": every registered DMX light)
        return [f"{s}.{m}" for s, m in members if m == item or m.startswith(item + ".")]

    out: dict[str, list[str]] = {}

    def add(name: str, ts: list[str]) -> None:
        g = out.setdefault(name, [])
        g += [t for t in ts if t not in g]
    for room, spec in rooms.items():
        if room.startswith("_") or not isinstance(spec, dict):
            continue
        for key, items in spec.items():
            if key.startswith("_") or not isinstance(items, list):
                continue
            ts = resolve(items)
            if ts and key not in subs:            # "screens": "<room>:screens" is the screens subsystem's part only
                add(f"{room}:{key}", ts)
            for t in ts:
                add(f"{room}:{t.split('.', 1)[0]}", [t])
        for t in resolve(room):
            add(f"{room}:{t.split('.', 1)[0]}", [t])
    return out


class RoomConfigFile:
    def __init__(self, path: str | Path, adapters: Iterable[Adapter], state_dir: str | Path,
                 rooms_path: str | Path | None = None) -> None:
        self.path = Path(path)
        self.adapters = list(adapters)
        self.history = Path(state_dir) / "config-history"
        self.rooms_path = Path(rooms_path) if rooms_path else None
        self.raw: dict = FALLBACK
        self.error: str | None = None
        self.rooms_error: str | None = None
        self.groups: dict[str, list[str]] = {}          # derived from rooms.json
        self._mtime: float | None = None
        self._rooms_mtime: float | None = None

    def _mtimes(self) -> tuple[float | None, float | None]:
        def m(p: Path | None) -> float | None:
            try:
                return p.stat().st_mtime if p else None
            except FileNotFoundError:
                return None
        return m(self.path), m(self.rooms_path)

    def derive_groups(self) -> dict[str, list[str]]:
        """'<room>:<kind>' groups from rooms.json; on a problem, the last good ones, with rooms_error set."""
        if self.rooms_path is None:
            return self.groups
        self._rooms_mtime = self._mtimes()[1]
        try:
            rooms = json.loads(self.rooms_path.read_text())
            if not isinstance(rooms, dict):
                raise ValueError("the top level must be an object of rooms")
        except (OSError, ValueError) as e:
            self.rooms_error = f"{self.rooms_path.name} not read: {e}"
            log.error("%s", self.rooms_error)
            return self.groups
        out = room_groups(rooms, self.adapters)
        self.groups, self.rooms_error = out, None
        return out

    def load(self) -> RoomConfig:
        """The config from disk; on any problem, the previous one (or the fallback) with `error` set."""
        groups = self.derive_groups()
        try:
            self._mtime = self.path.stat().st_mtime
            raw = yaml.safe_load(self.path.read_text()) or {}
            cfg = RoomConfig(raw, self.adapters, extra_groups=groups)
        except Exception as e:                    # missing, unparseable or invalid: keep what we had
            self.error = f"{self.path.name} not loaded: {e}"
            log.error("%s", self.error)
            try:
                return RoomConfig(self.raw, self.adapters, extra_groups=groups)
            except Exception:
                return RoomConfig(FALLBACK, self.adapters)
        self.raw, self.error = raw, None
        return cfg

    @property
    def revision(self) -> str:
        return revision(self.raw)

    def changed_on_disk(self) -> bool:
        now = self._mtimes()
        return (now[0] is not None and now[0] != self._mtime) or now[1] != self._rooms_mtime

    def check(self, raw: Any) -> dict:
        """POST /room/config/validate: errors (blocking) and warnings, each with a JSON pointer."""
        items = validate(raw, self.adapters, warnings=True, extra_groups=self.groups)
        errors = [i for i in items if i.get("level") != "warning"]
        warnings = [i for i in items if i.get("level") == "warning"]
        if self.rooms_error:
            warnings.append({"path": "", "code": "rooms_json", "level": "warning",
                             "message": f"{self.rooms_error}; using the last good room groups"})
        return {"ok": not errors, "revision": revision(raw) if isinstance(raw, dict) else None,
                "errors": errors, "warnings": warnings}

    def save(self, raw: Any, base_revision: str | None) -> RoomConfig:
        result = self.check(raw)
        if not result["ok"]:
            raise ConfigInvalid(result["errors"])
        if base_revision != self.revision:
            raise ConfigConflict(self.revision)
        cfg = RoomConfig(raw, self.adapters, extra_groups=self.groups)
        if self.path.exists():
            self.history.mkdir(mode=0o700, parents=True, exist_ok=True)
            (self.history / f"{int(time.time())}-{self.revision}.json").write_text(self.path.read_text())
            for old in sorted(self.history.glob("*.json"))[:-HISTORY_KEEP]:
                old.unlink(missing_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")   # no sort_keys: order is meaning
        os.replace(tmp, self.path)
        self.raw, self.error, self._mtime = raw, None, self.path.stat().st_mtime
        return cfg

    async def watch(self, on_change: Callable[[RoomConfig, str | None], None], every_s: float = 2.0) -> None:
        """Poll the file's mtime; call on_change(config, error) when someone edits it on disk."""
        while True:
            await asyncio.sleep(every_s)
            if self.changed_on_disk():
                cfg = self.load()
                on_change(cfg, self.error)
