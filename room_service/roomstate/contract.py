"""Adapter contract v1.0 (ROOM_STATE_MODEL §5): aa's v0.2 plus normalize(), canonical reads, the member argument
on signal(), dry_run, read_timeout_s, Outcome.retry, the status vocabulary, cancellation and persistence rules.

A subsystem ("blinds", "tv", "screens", "mirror_ball", "plug", "sonos") has members ("ch02", "north-screen",
"main", "dmx_power", "lounge.volume"). The coordinator addresses one member as a target, "<subsystem>.<member>",
and each target holds one JSON value.

Rules every adapter keeps (§5.2):
  1. read() returns values in the form normalize() produces, so the coordinator can compare them. Extra device
     detail (a session id, a track position) stays out of the value. A read may return a value config can't name
     when that is the truth (the screens' "failed").
  2. read() never moves anything. It raises when the device can't be reached (the belief goes stale); it maps
     "unreachable" to a value only when that is a positive fact (IP Control saying the TV is off).
  3. apply(): per-member results in Outcome.members, the top-level status the worst of them. Level: "ok" means read
     back after the command could have taken effect; when opts["pending"][member] = {from, earlier, earlier_until},
     a reading equal to `from` or an `earlier` value is not proof until earlier_until. Edge: "ok" means it was sent.
  4. Status is only ok | failed | unconfirmed. A timeout or a superseded wait is unconfirmed; an exception counts as
     failed. retry=False when the device side refused on purpose (writes not approved, Hue taken over, a screen
     controller that has given up): the coordinator never retries it.
  5. Applying the same values again is safe (except edge_toggle, which the coordinator never re-sends).
  6. Cancellation stops the wait, not the device: clean up within 3 s and re-raise CancelledError, never swallow it.
     While a device-side process carries on, read() reports moving=True where the device can tell.
  7. opts are hints ({"over_s": 5}, "pending"); ignore what you can't use.
  8. The adapter serialises its own device (the Bloc hub lock, one IP Control connection per TV) and keeps no
     coordinator state.
  9. Device writes only with the owner's approval; a writer that isn't approved yet runs as dry_run.
 10. State that lives in room_service's memory (the mask's hold, the page policy) is persisted by its adapter in its
     own file under ~/.local/state/screen-machine/room/ and restored on start, so read() stays truthful after a
     restart.
 11. normalize() is pure and offline: it checks shape and enums. Anything that needs the device (a Sonos favourite's
     URI) is resolved in apply() and read().
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .schema import check_value

Value = Any                       # JSON: str | int | float | bool | dict | list
Kind = Literal["level", "edge_discrete", "edge_toggle", "action"]
Status = Literal["ok", "failed", "unconfirmed"]


@dataclass
class Reading:
    value: Value | None           # canonical (as normalize() returns it); None = unknown
    moving: bool = False          # mid-change, as far as the device can tell


@dataclass
class Outcome:
    status: Status
    detail: str = ""
    members: dict[str, "Outcome"] = field(default_factory=dict)   # per member, when apply() covered several
    retry: bool = True            # False: refused on purpose; never retried by reconcile


class Adapter:
    """Base class with the contract's defaults. `kind = "action"` is for adapters that only take signals (log)."""

    subsystem: str
    kind: Kind
    members: list[str]
    change_time_s: float = 0.0    # the longest a change takes (blind travel, TV wake)
    poll_s: float | None = None   # level: how often reconcile may read
    poll_inflight_s: float = 0.5  # level: how often to read while a command is in flight
    poll_all: bool = True         # level: reconcile also reads members no mode manages (beliefs only: no drift, no
                                  # enforce), so what the room believes stays true while idle (the page's "now", a
                                  # put-back's starting value). False for an adapter whose reads cost the device
    read_timeout_s: float = 5.0
    prove_behind: bool = False    # level: apply() sends every command before its first wait, on a channel that later
                                  # steps' commands queue behind (a page's control socket), so a rule's next step needn't
                                  # wait for the proof: the coordinator proves it alongside and waits for it at the end.
                                  # Never for a guarded target
    tolerance: float = 0         # numeric values within this are equal (blinds: 3 of 0..100)
    resend: bool = False          # edge_discrete: re-send even when the believed value already matches
    dry_run: bool = False         # reads work; the coordinator sends nothing (rows 'skipped')
    placeholder: bool = False     # no working adapter; members (and values) from device config
    actions: dict[str, dict] = {}  # signal name -> {"label": ..., "schema": <object schema for args>}
    subsystem_label: str | None = None  # page words for the subsystem ("Blinds", "Music"); None: from its name
    category: str = "device"      # "device" (a thing in the room) | "setting" (the room's own behaviour)

    def value_schema(self, member: str) -> dict:
        """JSON Schema (the catalog subset plus x-ui, see schema.py) for one member's value; {} accepts anything.
        The Room page renders editors from it."""
        return {}

    def normalize(self, member: str, value: Value) -> Value:
        """Canonical form of a config value; raise ValueError if it is invalid. Pure and offline: called at load.
        The default checks the value schema and returns the value unchanged."""
        problem = check_value(value, self.value_schema(member))
        if problem:
            raise ValueError(problem)
        return value

    def label(self, member: str) -> str:
        """A display name for the member; the config's "labels" map overrides it."""
        return member

    def show(self, member: str, value: Value) -> str:
        """Short text for a row ("live:fractal-zoom")."""
        return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))

    def words(self, member: str, value: Value) -> str:
        """Page words for a value ("Lowered", "Art mode"): the value schema's own names (schema_words), else show()."""
        try:
            w = schema_words(self.value_schema(member), value)
        except Exception:                     # a member the adapter doesn't know: plain text
            w = None
        return self.show(member, value) if w is None else str(w)

    async def read(self, members: list[str]) -> dict[str, Reading]:
        """Level kinds only. Must not move anything. Raise if the device can't be reached."""
        raise NotImplementedError

    async def apply(self, desired: dict[str, Value], opts: dict) -> Outcome:
        """Drive each member to its value. Return when every member has arrived, failed or timed out."""
        raise NotImplementedError

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        """A one-shot action (Sonos pause, a Maestro cue). Not held state."""
        return Outcome("failed", f"{self.subsystem} has no action {action!r}")

    def vocabulary(self) -> dict[str, list]:
        """Word lists this subsystem gives the Room page's editor beyond its value schemas, from its own cache (no I/O):
        {"situations": [...]} is what Auto's conditions can say. describe() lists them under subsystems.<sub>, and the
        situations also on the mode form's conditions (x-ui.situations)."""
        return {}


def schema_words(schema: dict, value: Value) -> str | None:
    """A value's name from its schema's x-ui: a oneOf branch's label, an enum label, a slider end, a number with its
    unit, a boolean's label. None when the schema doesn't name it."""
    if not isinstance(schema, dict):
        return None
    ui = schema.get("x-ui") or {}
    for branch in schema.get("oneOf") or ():
        if isinstance(branch, dict) and "const" in branch and branch["const"] == value:
            return (branch.get("x-ui") or {}).get("label")
    if isinstance(value, bool):
        return (ui.get("labels") or {}).get(str(value).lower())
    if isinstance(value, str):
        if value in (ui.get("labels") or {}):
            return ui["labels"][value]
        enum, names = schema.get("enum") or [], ui.get("enum_labels") or []
        return names[enum.index(value)] if value in enum and len(names) == len(enum) else None
    if isinstance(value, (int, float)):
        ends = ui.get("ends") or {}
        for end, key in (("min", "minimum"), ("max", "maximum")):
            if end in ends and schema.get(key) == value:
                return ends[end]
        unit = ui.get("unit")
        return None if not unit else f"{value}{unit}" if unit == "%" else f"{value} {unit}"
    return None


class Placeholder(Adapter):
    """A subsystem with no working adapter yet. Members and, optionally, values come from its device config
    (`mirror_ball: {members: [main], values: ["spinning", "stopped"]}`; `values` may also map member -> list), so
    normalize() still rejects a typo. Without values it accepts anything. Its rows are skipped; nothing is sent."""

    kind = "level"
    placeholder = True

    def __init__(self, subsystem: str, members: list[str] | None = None,
                 values: list | dict[str, list] | None = None) -> None:
        self.subsystem, self.members, self.values = subsystem, list(members or []), values

    def value_schema(self, member: str) -> dict:
        values = self.values.get(member) if isinstance(self.values, dict) else self.values
        return {"enum": list(values)} if values is not None else {}
