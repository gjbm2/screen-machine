"""Mode zones (deployments/README.md, "Rooms with their own modes"): which coordinator owns which target.

A deployment has one SITE zone: the deployment's room/modes.json, <state_dir>/state.json, top-level status.mode, Auto,
the feel, and PUT /api/v1/mode with no room. Until a room in rooms.json sets "own_modes": true, that is the only zone
and room_service builds exactly the objects it always did (one RoomConfigFile, one Coordinator, the raw adapters).

A room with "own_modes": true is a ROOM zone:
- its own modes file (room/modes.<room>.json, the modes.json schema), Coordinator and state, under
  <state_dir>/rooms/<room>/ (state.json and config-history/);
- its scope: the union of the room's '<room>:*' groups (room_config.derive_room_groups) minus SITE_ONLY; a target that
  another room also claims, or a light the zone takes through the whole "light" subsystem, stays with the site (a
  conflict: status.mode.rooms_error says so);
- it sees the shared adapters through ScopedAdapter proxies, so a bare "screens" in its file means its own screens; the
  site sees every target no zone owns. The raw adapter list (svc.adapters) is what the remotes API, the DMX API and the
  member watchers use;
- its switches are "room_mode.transition" events (data.room), so a consumer of the site's "mode.transition" can never
  mistake them.

The layout (which rooms are zones) is read once at start; a rooms.json edit that changes it says "restart room_service
(Flask) to apply" in rooms_error. Scopes are re-planned when rooms.json or an adapter's members change.
"""

from __future__ import annotations

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .room_config import LIGHT, _rooms, _strings, derive_room_groups

if TYPE_CHECKING:
    from .room_config import RoomConfigFile
    from .roomstate.contract import Adapter
    from .roomstate.coordinator import Coordinator

log = logging.getLogger("room_service.zones")

SITE = "site"
SITE_NAME = "the site"
# Targets only the site's modes may set, whatever a room lists: the feel, Auto, the log and the one Hue/lamps level.
SITE_ONLY = ("show.main", "autopilot.*", "log.*", "brightness.lamps")
RESTART = "restart room_service (Flask) to apply"


def site_only(t: str) -> bool:
    return any(fnmatch.fnmatchcase(t, p) for p in SITE_ONLY)


def layout_of(rooms: Any) -> dict[str, dict[str, Any]]:
    """The rooms with "own_modes": true, in file order: {room: {"name", "screens", "show"}} (smconfig.own_mode_rooms'
    rule, on an already parsed rooms.json). Only the literal true counts."""
    if not isinstance(rooms, dict):
        return {}
    return {rid: {"name": str(spec.get("name") or rid),
                  "screens": [s for s in spec.get("screens") or [] if isinstance(s, str)]
                  if isinstance(spec.get("screens"), list) else [],
                  "show": spec.get("show") is not False}
            for rid, spec in _rooms(rooms) if spec.get("own_modes") is True}


def read_rooms(path: str | Path | None) -> dict:
    """rooms.json parsed, or {} when it is missing or unreadable (the site then has no zones)."""
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


class ScopedAdapter:
    """One zone's view of a shared adapter: `members` (a live property) is the inner adapter's members whose
    '<subsystem>.<member>' is in the zone's scope, dynamic ones included; everything else (read, apply, signal, schema,
    normalize, words, absent, kind, poll_s, ...) is the inner adapter's. No lock of its own: contract rule 8 has each
    adapter serialise its own device, and the per-screen subsystems work per controller."""

    def __init__(self, inner: "Adapter", in_scope: Callable[[str], bool], zone_name: str = "") -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_in_scope", in_scope)
        object.__setattr__(self, "_zone_name", zone_name)

    @property
    def inner(self) -> "Adapter":
        return self._inner

    @property
    def members(self) -> list[str]:
        sub = self._inner.subsystem
        return [m for m in self._inner.members if self._in_scope(f"{sub}.{m}")]

    def absent(self, rest: str) -> str | None:
        """The inner adapter's reason; and the bare subsystem names nothing (a warning, never an error) when this zone
        has none of a subsystem the site has."""
        why = self._inner.absent(rest)
        if why is None and rest == "" and self._inner.members and not self.members:
            return f"{self._zone_name or 'this zone'} has no {self._inner.subsystem} of its own"
        return why

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._inner, name, value)

    def __repr__(self) -> str:
        return f"ScopedAdapter({self._inner.subsystem}, {self._zone_name or '?'})"


def raw_targets(adapters: Iterable["Adapter"]) -> list[str]:
    return [f"{a.subsystem}.{m}" for a in adapters for m in a.members]


def plan_scopes(rooms: dict, adapters: Iterable["Adapter"],
                zone_ids: Iterable[str]) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    """(scopes, conflicts): each zone's targets, from derive_room_groups over the raw adapters, minus SITE_ONLY; and
    {target: [rooms]} for each target a zone lists that stays with the site because another room lists it too, or
    because the zone takes it through the whole "light" subsystem."""
    adapters = list(adapters)
    groups, _ = derive_room_groups(rooms, adapters)
    by_room: dict[str, set[str]] = {}
    for g, ts in groups.items():
        by_room.setdefault(g.split(":", 1)[0], set()).update(ts)
    claims: dict[str, set[str]] = {}
    for room, ts in by_room.items():
        for t in ts:
            claims.setdefault(t, set()).add(room)
    specs = dict(_rooms(rooms))
    scopes: dict[str, set[str]] = {}
    conflicts: dict[str, list[str]] = {}
    for z in zone_ids:
        spec = specs.get(z) or {}
        whole_light = any(s == LIGHT for k, items in spec.items()
                          if not k.startswith("_") and isinstance(items, list) for s in _strings(items))
        mine = {t for t in by_room.get(z, set()) if not site_only(t)}
        bad = {t for t in mine if claims.get(t, set()) - {z} or (whole_light and t.startswith(LIGHT + "."))}
        for t in sorted(bad):
            conflicts.setdefault(t, sorted(claims.get(t, set()) | {z}))
        scopes[z] = mine - bad
    return scopes, conflicts


class ZonePlan:
    """The zone layout (read once at start) and the current scopes (re-planned when rooms.json or members change)."""

    def __init__(self, layout: dict[str, dict[str, Any]], adapters: list["Adapter"]) -> None:
        self.layout = layout
        self.adapters = adapters                    # the raw list (svc.adapters); swap_adapters replaces it
        self.scopes: dict[str, set[str]] = {z: set() for z in layout}
        self.conflicts: dict[str, list[str]] = {}
        self.owned: set[str] = set()                # every target some zone owns
        self._said: str | None = None

    def name(self, zone: str | None) -> str:
        return self.layout[zone]["name"] if zone in self.layout else SITE_NAME

    def replan(self, rooms: dict) -> None:
        scopes, self.conflicts = plan_scopes(rooms, self.adapters, self.layout)
        for z in self.layout:                       # in place: the ScopedAdapters' closures read these sets
            self.scopes[z].clear()
            self.scopes[z].update(scopes.get(z, set()))
        self.owned = set().union(*self.scopes.values()) if self.scopes else set()

    def in_site(self, t: str) -> bool:
        return t not in self.owned

    def in_zone(self, zone: str) -> Callable[[str], bool]:
        scope = self.scopes[zone]
        return lambda t: t in scope

    def owner(self, t: str) -> str | None:
        """The zone id that owns t, else None (the site)."""
        if t in self.owned:
            return next(z for z, s in self.scopes.items() if t in s)
        return None

    def moved_from(self, zone: str | None) -> Callable[[str], str | None]:
        """For a zone's Coordinator.prune_outside: a target's new owner's name when an adapter has the target now and
        another zone owns it, else None. A target no adapter has yet (a light the zone lists that show_service hasn't
        registered) is kept: its owner isn't known, and it may be this zone's a moment later."""
        known = set(raw_targets(self.adapters))

        def owner_of(t: str) -> str | None:
            if t not in known:
                return None
            o = self.owner(t)
            return self.name(o) if o != zone else None
        return owner_of

    def owners_for(self, zone: str | None) -> Callable[[], dict[str, str]]:
        """For a zone's RoomConfig: {target outside its scope: its owner's name}, so naming one says whose it is."""
        def owners() -> dict[str, str]:
            out = {}
            for t in raw_targets(self.adapters):
                o = self.owner(t)
                if o != zone:
                    out[t] = self.name(o)
            return out
        return owners

    def problem(self, rooms: dict) -> str | None:
        """rooms_error: a layout change since start (needs a restart), else the conflicts that stayed with the site."""
        now = layout_of(rooms)
        if list(now) != list(self.layout):
            said = (f"rooms.json changed which rooms have their own modes (now: {', '.join(now) or 'none'}; "
                    f"running: {', '.join(self.layout) or 'none'}): {RESTART}")
        elif self.conflicts:
            said = "; ".join(f"{t} is listed by {', '.join(rs)}, so it stays with the site's modes"
                             for t, rs in sorted(self.conflicts.items()))
        else:
            said = None
        if said and said != self._said:
            log.warning("%s", said)
        self._said = said
        return said


@dataclass
class Zone:
    id: str                                         # "site" or the room id
    room: str | None                                # None for the site
    name: str
    screens: list[str]
    room_config: "RoomConfigFile"
    coordinator: "Coordinator"
    show: bool = True
    scope: set[str] | None = None                   # None: the site (everything no zone owns)

    @property
    def is_site(self) -> bool:
        return self.room is None


@dataclass
class ZoneSet:
    """The site zone and the room zones. for_room: the zone for an own_modes room, the site for no room or any other
    known room, KeyError for a room rooms.json doesn't have. for_target: the zone whose scope holds the target, else
    the site."""
    site: Zone
    zones: dict[str, Zone] = field(default_factory=dict)
    known_rooms: Callable[[], Iterable[str]] = lambda: ()
    plan: ZonePlan | None = None

    @property
    def single(self) -> bool:
        return not self.zones

    def all(self) -> list[Zone]:
        return [self.site, *self.zones.values()]

    def for_room(self, room: str | None) -> Zone:
        if room is None or room == "":
            return self.site
        if room in self.zones:
            return self.zones[room]
        if room in set(self.known_rooms()):
            return self.site
        raise KeyError(room)

    def for_target(self, t: str) -> Zone:
        if self.zones and self.plan is not None:
            z = self.plan.owner(t)
            if z in self.zones:
                return self.zones[z]
        return self.site
