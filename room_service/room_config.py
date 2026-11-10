"""The room's modes file (the deployment's room/modes.json, ROOM_STATE_MODEL §2): load, watch, save.

- room_service is the only writer. A save is validated first, refused on a stale `base_revision`, written atomically
  and order-preserving (key order matters: it is part of the revision), and the previous file is kept in
  <state_dir>/config-history/ (the last 50).
- An edit made on disk is picked up by the watcher. A bad file keeps the previous config and reports the error; with
  no previous config (first boot) the room runs a one-mode fallback and PUT /mode answers 404 for real modes.
- JSON is the format; YAML also loads (it is a superset), so an old-style file still parses before it is rewritten.
- The site file rooms.json (the deployment's) says which room holds what. Each room gives read-only groups, holding only
  settable targets an adapter has (derive_groups); a bad rooms.json keeps the last good groups.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
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


LIGHT = "light"                       # the DMX lights' subsystem (adapters/lights.py): rooms share them out (C1)
SCREEN_SUBS = ("screens", "page", "diag")   # the per-screen subsystems: a room listing screens has a group of each


def _rooms(rooms: dict) -> list[tuple[str, dict]]:
    return [(r, spec) for r, spec in rooms.items() if not r.startswith("_") and isinstance(spec, dict)]


def _strings(item: Any) -> list[str]:
    if isinstance(item, str):
        return [item]
    if isinstance(item, dict):
        return [s for v in item.values() for s in _strings(v)]
    if isinstance(item, list):
        return [s for v in item for s in _strings(v)]
    return []


def light_rooms(rooms: dict, lights: Iterable[str],
                claims_of: Callable[[str], list[str]] | None = None) -> dict[str, str | None]:
    """{rig light id: the room it is in, or None (the "Other" section)}: the one rule (contract C1) the Room page,
    /lights, the Light setup page and the room groups all apply, so a light never shows in two rooms.
      1. "light.<id>" in a room's lists puts that light in that room (the first room in file order, if two do);
      2. else "light" in a room's lists takes every light no room lists by id (the first such room, if two do);
      3. else, when rooms.json has exactly one room, the light is in it;
      4. else it is in no room (None).
    claims_of(item): the light ids a list item names by id (default: "light.<id>" itself); room_groups also counts a
    bare id that is a light's."""
    lights = list(lights)
    known = set(lights)
    claims_of = claims_of or (lambda s: [s.split(".", 1)[1]] if s.startswith(LIGHT + ".") else [])
    claimed: dict[str, str] = {}
    bare: list[str] = []
    valid = _rooms(rooms)
    for room, spec in valid:
        for key, items in spec.items():
            if key.startswith("_") or not isinstance(items, list):
                continue
            for s in _strings(items):
                if s == LIGHT:
                    if room not in bare:
                        bare.append(room)
                    continue
                for lid in claims_of(s):
                    if lid in known:
                        claimed.setdefault(lid, room)
    only = valid[0][0] if len(valid) == 1 else None
    return {lid: claimed.get(lid) or (bare[0] if bare else only) for lid in lights}


def derive_room_groups(rooms: dict, adapters: Iterable[Adapter]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """(groups, notes): room_groups' groups, and for each that is empty here, why ("Drawing room has no live
    screens"), for the model's lint where a mode uses it."""
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
            return [f"{s}.{m}" for s, m in members if s == item]    # ("light": shared out by light_rooms below)
        return [f"{s}.{m}" for s, m in members if m == item or m.startswith(item + ".")]

    owner = light_rooms(rooms, [m for s, m in members if s == LIGHT],
                        lambda s: [t.split(".", 1)[1] for t in resolve(s) if t.startswith(LIGHT + ".")])

    def mine(room: str, ts: list[str]) -> list[str]:
        """A room's targets, without the DMX lights that are another room's (C1)."""
        return [t for t in ts if not t.startswith(LIGHT + ".") or owner.get(t.split(".", 1)[1]) == room]

    out: dict[str, list[str]] = {}
    notes: dict[str, str] = {}

    def add(name: str, ts: list[str]) -> None:
        g = out.setdefault(name, [])
        g += [t for t in ts if t not in g]
    for room, spec in _rooms(rooms):
        name = str(spec.get("name") or room)
        for key, items in spec.items():
            if key.startswith("_") or not isinstance(items, list):
                continue
            ts = mine(room, resolve(items))
            if key not in subs:                   # "screens": "<room>:screens" is the screens subsystem's part only
                add(f"{room}:{key}", ts)          # (defined even when empty: a known group that names nothing)
                if not ts:
                    notes[f"{room}:{key}"] = f"{name} has no {key} here"
            for t in ts:
                add(f"{room}:{t.split('.', 1)[0]}", [t])
            if key in subs and not ts:
                out.setdefault(f"{room}:{key}", [])
                notes.setdefault(f"{room}:{key}", f"{name} has no {key} here")
        for t in mine(room, resolve(room)):
            add(f"{room}:{t.split('.', 1)[0]}", [t])
        theirs = [f"{LIGHT}.{m}" for s, m in members if s == LIGHT and owner.get(m) == room]
        if theirs:                                # its share of the DMX lights, listed or not (C1 rule 3)
            add(f"{room}:{LIGHT}", theirs)
        if isinstance(spec.get("screens"), list):  # a room with screens: its screens, page and diag groups exist
            for sub in (x for x in SCREEN_SUBS if x in subs):
                if not out.get(f"{room}:{sub}"):
                    out.setdefault(f"{room}:{sub}", [])
                    notes[f"{room}:{sub}"] = f"{name} has no live screens"
    return out, notes


def room_groups(rooms: dict, adapters: Iterable[Adapter]) -> dict[str, list[str]]:
    """Groups from rooms.json, no table of kinds: for every list in a room, "<room>:<list key>" holds what its items
    name (unless the key is a subsystem's name), and "<room>:<subsystem>" what each subsystem has there. An item is a
    target ("hue.ne"), an object of them (an effect's {power, settings}), a subsystem's name (all its members), or a
    bare id: every member that is that id or starts with "<id>." in any subsystem (screen "north-screen" ->
    screens.north-screen, tv.north-screen, page.north-screen.timed_reload; blind "ch02" -> blinds.ch02). Members keyed
    by the room itself (Sonos zone "drawing-room") join "<room>:<subsystem>". Ids nothing has (a sensor) and read-only
    members are left out.
    The DMX lights ("light", show_service's registered lights) are shared out by light_rooms (contract C1): "light"
    in a room is the lights no other room lists by id, and a light in no room's lists is the only room's when there is
    one, so "<room>:light" never holds another room's light.
    Every list key gives "<room>:<key>" (or "<room>:<subsystem>" for a subsystem's name) even when nothing resolves,
    and a room listing screens has "<room>:screens", ":page" and ":diag": a known group that names nothing, never a
    misspelt-name placeholder (derive_room_groups' notes say why it is empty)."""
    return derive_room_groups(rooms, adapters)[0]


class RoomConfigFile:
    def __init__(self, path: str | Path, adapters: Iterable[Adapter], state_dir: str | Path,
                 rooms_path: str | Path | None = None, scope_room: str | None = None,
                 owners: Callable[[], dict[str, str]] | None = None,
                 rooms_hook: Callable[[dict], str | None] | None = None) -> None:
        """Mode zones (zones.py): `scope_room` and `owners` go to every RoomConfig built here (a room zone's aliases;
        whose a target outside this zone is). `rooms_hook(rooms)` runs on each good read of rooms.json, before the
        groups are derived (the zones re-plan their scopes); what it returns is rooms_error (None: no problem)."""
        self.path = Path(path)
        self.adapters = list(adapters)
        self.history = Path(state_dir) / "config-history"
        self.rooms_path = Path(rooms_path) if rooms_path else None
        self.scope_room = scope_room
        self.owners = owners
        self.rooms_hook = rooms_hook
        self.raw: dict = FALLBACK
        self.error: str | None = None
        self.rooms_error: str | None = None
        self.groups: dict[str, list[str]] = {}          # derived from rooms.json
        self.group_notes: dict[str, str] = {}           # ... and why each empty one is empty
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
        problem = self.rooms_hook(rooms) if self.rooms_hook is not None else None
        out, self.group_notes = derive_room_groups(rooms, self.adapters)
        self.groups, self.rooms_error = out, problem
        return out

    def _zone_kw(self) -> dict:
        """RoomConfig's zone arguments: none at all for a deployment without zones."""
        kw: dict = {}
        if self.scope_room is not None:
            kw["scope_room"] = self.scope_room
        if self.owners is not None:
            kw["owners"] = self.owners()
        return kw

    def build(self, raw: Any) -> RoomConfig:
        """A RoomConfig from `raw` with this file's adapters, groups and zone (POST /room/plan's draft)."""
        return RoomConfig(raw, self.adapters, extra_groups=self.groups, group_notes=self.group_notes, **self._zone_kw())

    def load(self) -> RoomConfig:
        """The config from disk; on any problem, the previous one (or the fallback) with `error` set."""
        groups = self.derive_groups()
        try:
            self._mtime = self.path.stat().st_mtime
            raw = yaml.safe_load(self.path.read_text()) or {}
            cfg = RoomConfig(raw, self.adapters, extra_groups=groups, group_notes=self.group_notes, **self._zone_kw())
        except Exception as e:                    # missing, unparseable or invalid: keep what we had
            self.error = f"{self.path.name} not loaded: {e}"
            log.error("%s", self.error)
            try:
                return RoomConfig(self.raw, self.adapters, extra_groups=groups, group_notes=self.group_notes,
                                  **self._zone_kw())
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
        items = validate(raw, self.adapters, warnings=True, extra_groups=self.groups, group_notes=self.group_notes,
                         **self._zone_kw())
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
        cfg = self.build(raw)
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


def validate_site_modes(raw: Any, rooms: dict | None, destinations: list[dict] | None,
                        warnings: bool = False, scope_room: str | None = None) -> list[dict]:
    """A deployment's modes.json checked the way room_service loads it, without starting anything (smconfig check):
    the screens, page and diag subsystems stand in for the live screens of `destinations` (those with a live block),
    every destination known by id (an art-only screen named in a mode names nothing, with a warning; an id the site
    doesn't have is an error), the room groups come from `rooms`, and every other subsystem is a placeholder.
    With rooms that have their own modes (zones.py), the file is checked in its zone's scope: `scope_room` for an
    own_modes room's room/modes.<room>.json, else the site's (what no zone owns).
    Returns validate()'s items: errors, then warnings when asked."""
    from .adapters.room import DiagAdapter, PageAdapter      # noqa: PLC0415 - no import cost for the service path
    from .adapters.screens import ScreensAdapter             # noqa: PLC0415
    dests = [d for d in destinations or [] if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"]]
    known = {d["id"]: str(d.get("name") or d["id"]) for d in dests}
    live = {d["id"]: None for d in dests if isinstance(d.get("live"), dict)}
    with tempfile.TemporaryDirectory() as tmp:               # the adapters' state files: never read here
        adapters: list[Adapter] = [ScreensAdapter(live, known), PageAdapter(live, tmp, known=known),
                                   DiagAdapter(live, tmp, known=known)]
        from .zones import ScopedAdapter, ZonePlan, layout_of    # noqa: PLC0415 - zones imports this module
        layout = layout_of(rooms or {})
        zone_kw: dict = {}
        if layout:                                           # a site with zones: this file's scope only
            plan = ZonePlan(layout, adapters)
            plan.replan(rooms or {})
            zone = scope_room if scope_room in layout else None
            in_scope = plan.in_zone(zone) if zone else plan.in_site
            owners = plan.owners_for(zone)()
            adapters = [ScopedAdapter(a, in_scope, plan.name(zone)) for a in adapters]   # type: ignore[misc]
            zone_kw = {"owners": owners, **({"scope_room": zone} if zone else {})}
        groups, notes = derive_room_groups(rooms or {}, adapters)
        return validate(raw, adapters, warnings=warnings, extra_groups=groups, group_notes=notes, **zone_kw)
