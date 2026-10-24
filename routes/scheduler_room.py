"""What a room's schedules offer the Room page: its variables and its events.

GET /api/rooms/<room_id>/schedule (scheduler_api.py) answers from build_room_schedule() below, from data only:
no variable or event name is written in code.

- The room's scheduler group: rooms.json's optional "scheduler_group" for the room, else the room id.
- Variables: every export to that group in the exports registry (scheduler/_vars.json), with its value from the
  owner's context, who set it and when, and which destinations import it ("used by").
- Events: the event triggers of the destinations' live schedules (top of each stack). "Room actions" are the
  events of the destinations that export to the group and are not screens of any room (e.g. cloud); each
  screen of the room has its own list.

Public rule, for both: a name starting with "_" is internal and left out (the scheduler's own convention,
e.g. _a_theme, _room_mode), and so is an export_var instruction or event trigger with "room": false. An
optional "description" (both) and "display_name" (event triggers) are shown; otherwise the label is made from
the name ("Develop_theme" -> "Develop theme").

Order of variables: the order the owner's schedule exports them in a run. Starting from its initial actions
and its timed triggers (earliest first), each block's exports count before the events it throws (a thrown
event runs after the block), then the events those throw, and so on; event triggers no timed block reaches
follow in file order.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional


def room_group(room_id: str, rooms: Mapping[str, Any]) -> str:
    """The scheduler group of a room: its optional "scheduler_group", else the room id."""
    room = rooms.get(room_id) if isinstance(rooms, Mapping) else None
    group = room.get("scheduler_group") if isinstance(room, Mapping) else None
    return group if isinstance(group, str) and group.strip() else room_id


def label_for(name: str) -> str:
    """A label from a key: "Develop_theme" -> "Develop theme", "get-inspiration" -> "Get inspiration"."""
    words = " ".join(str(name).replace("_", " ").replace("-", " ").split())
    return words[:1].upper() + words[1:] if words else str(name)


def is_public(name: str, item: Optional[Mapping[str, Any]] = None) -> bool:
    if not isinstance(name, str) or not name or name.startswith("_"):
        return False
    return not (isinstance(item, Mapping) and item.get("room") is False)


def _instructions(node: Any) -> Iterator[Dict[str, Any]]:
    """Every instruction (a dict with an "action") inside node, in file order, nested ones included."""
    if isinstance(node, Mapping):
        if isinstance(node.get("action"), str):
            yield node  # type: ignore[misc]
        for v in node.values():
            yield from _instructions(v)
    elif isinstance(node, list):
        for v in node:
            yield from _instructions(v)


def _timed_blocks(schedule: Mapping[str, Any]) -> List[Any]:
    """The schedule's initial actions, then its timed blocks, earliest time first."""
    out: List[Any] = [schedule.get("initial_actions")]
    timed: List[tuple] = []
    for n, trig in enumerate(schedule.get("triggers") or []):
        if not isinstance(trig, Mapping) or trig.get("type") == "event":
            continue
        for m, sa in enumerate(trig.get("scheduled_actions") or []):
            if isinstance(sa, Mapping):
                timed.append((str(sa.get("time") or "99:99"), n, m, sa.get("trigger_actions")))
    out.extend(b for *_, b in sorted(timed, key=lambda t: t[:3]))
    return out


def _event_triggers(schedule: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [t for t in (schedule.get("triggers") or [])
            if isinstance(t, Mapping) and t.get("type") == "event" and isinstance(t.get("value"), str)]


def export_order(schedule: Mapping[str, Any], group: str) -> List[str]:
    """Names the schedule exports to `group`, in run order (see the module doc)."""
    events = _event_triggers(schedule)
    by_name: Dict[str, List[Mapping[str, Any]]] = {}
    for t in events:
        by_name.setdefault(t["value"], []).append(t)
    seen_events: set = set()
    order: List[str] = []

    def visit(block: Any, queue: deque) -> None:
        for ins in _instructions(block):
            if ins.get("action") == "export_var" and ins.get("scope") == group:
                name = ins.get("var_name")
                if isinstance(name, str) and name not in order:
                    order.append(name)
            elif ins.get("action") == "throw_event" and ins.get("event") in by_name:
                queue.append(ins["event"])

    def run(blocks: Iterable[Any]) -> None:
        for block in blocks:
            queue: deque = deque()
            visit(block, queue)
            while queue:
                ev = queue.popleft()
                if ev in seen_events:
                    continue
                seen_events.add(ev)
                for t in by_name.get(ev, []):
                    visit(t.get("trigger_actions"), queue)

    run(_timed_blocks(schedule))
    run([t.get("trigger_actions") for t in events if t["value"] not in seen_events])
    return order


def export_meta(schedule: Mapping[str, Any], group: str) -> Dict[str, Dict[str, Any]]:
    """name -> the optional room/description/friendly_name from the schedule's export_var instructions to group."""
    meta: Dict[str, Dict[str, Any]] = {}
    for ins in _instructions(schedule):
        if ins.get("action") != "export_var" or ins.get("scope") != group or not isinstance(ins.get("var_name"), str):
            continue
        m = meta.setdefault(ins["var_name"], {})
        for k in ("room", "description", "friendly_name"):
            if k in ins and k not in m:
                m[k] = ins[k]
    return meta


def schedule_events(schedule: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The public events a schedule listens for, in file order, once each."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for t in _event_triggers(schedule or {}):
        key = t["value"]
        if key in seen or not is_public(key, t):
            continue
        seen.add(key)
        label = t.get("display_name")
        desc = t.get("description")
        out.append({
            "event": key,
            "label": label if isinstance(label, str) and label.strip() else label_for(key),
            "description": desc if isinstance(desc, str) and desc.strip() else None,
        })
    return out


def _friendly(value: Any, name: str) -> Optional[str]:
    """A friendly_name worth showing: set, and not just the variable's own name (export_var's default)."""
    return value if isinstance(value, str) and value.strip() and value != name else None


def _top(stacks: Mapping[str, List[Any]], dest: str) -> Optional[Mapping[str, Any]]:
    stack = stacks.get(dest) or []
    return stack[-1] if stack and isinstance(stack[-1], Mapping) else None


def build_room_schedule(room_id: str, rooms: Mapping[str, Any], destinations: List[Mapping[str, Any]],
                        registry: Mapping[str, Any], stacks: Mapping[str, List[Any]],
                        contexts: Mapping[str, List[Any]]) -> Dict[str, Any]:
    """The Room page's view of one room's schedules (see the module doc)."""
    group = room_group(room_id, rooms)
    names = {d.get("id"): (d.get("name") or d.get("id")) for d in destinations if isinstance(d, Mapping)}
    room = rooms.get(room_id) if isinstance(rooms.get(room_id), Mapping) else {}
    screens = [s for s in (room.get("screens") or []) if isinstance(s, str)]
    all_screens = {s for r in rooms.values() if isinstance(r, Mapping) for s in (r.get("screens") or [])}

    entries: Mapping[str, Any] = (registry.get("groups") or {}).get(group) or {}
    imports: Mapping[str, Any] = registry.get("imports") or {}

    # Who exports to the group: the registry's owners, then any schedule with an export_var to it.
    exporters: List[str] = []
    for info in entries.values():
        owner = info.get("owner") if isinstance(info, Mapping) else None
        if isinstance(owner, str) and owner not in exporters:
            exporters.append(owner)
    for dest in list(stacks):
        if dest not in exporters and any(export_order(s, group) for s in (stacks.get(dest) or []) if isinstance(s, Mapping)):
            exporters.append(dest)

    orders: Dict[str, List[str]] = {}
    metas: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for owner in exporters:
        top = _top(stacks, owner)
        orders[owner] = export_order(top, group) if top else []
        metas[owner] = export_meta(top, group) if top else {}

    variables: List[Dict[str, Any]] = []
    for n, (name, info) in enumerate(entries.items()):
        if not isinstance(info, Mapping):
            continue
        owner = info.get("owner")
        meta = metas.get(owner, {}).get(name, {})
        if not is_public(name, meta):
            continue
        ctx = (contexts.get(owner) or [None])[-1] if owner else None
        value = (ctx.get("vars") or {}).get(name) if isinstance(ctx, Mapping) else None
        used_by = []
        for dest, imp in (imports.get(name) or {}).items():
            if not isinstance(imp, Mapping):
                continue
            src, kind = imp.get("source"), imp.get("source_type", "destination")
            if (kind in ("scope", "group") and src == group) or (kind == "destination" and src == owner):
                used_by.append({"id": dest, "name": names.get(dest, dest)})
        set_at, set_by = info.get("timestamp"), names.get(owner, owner)
        edited_at = info.get("edited_at")
        if isinstance(edited_at, str) and (not isinstance(set_at, str) or edited_at > set_at):
            set_at, set_by = edited_at, info.get("edited_by") or "Room page"
        order = orders.get(owner, [])
        variables.append({
            "name": name,
            "label": _friendly(info.get("friendly_name"), name) or _friendly(meta.get("friendly_name"), name) or label_for(name),
            "description": meta.get("description") if isinstance(meta.get("description"), str) else None,
            "value": value,
            "editable": value is None or isinstance(value, (str, int, float, bool)),
            "owner": owner,
            "owner_name": names.get(owner, owner),
            "set_at": set_at,
            "set_by": set_by,
            "used_by": used_by,
            "_sort": (order.index(name) if name in order else len(order) + n),
        })
    variables.sort(key=lambda v: (exporters.index(v["owner"]) if v["owner"] in exporters else 99, v["_sort"]))
    for v in variables:
        del v["_sort"]

    actions: List[Dict[str, Any]] = []
    for dest in exporters:
        if dest in all_screens:
            continue
        for ev in schedule_events(_top(stacks, dest)):
            actions.append({**ev, "scope": dest, "dest": dest, "dest_name": names.get(dest, dest)})

    screen_events = {s: [{**ev, "scope": s} for ev in schedule_events(_top(stacks, s))] for s in screens}

    return {"room": room_id, "group": group, "variables": variables, "actions": actions, "screens": screen_events}
