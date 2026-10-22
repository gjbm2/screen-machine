"""Room config (ROOM_STATE_MODEL §2): groups, labels, modes and transition rules, as JSON (room_service keeps it in
config/room/modes.json; example_room.json is the spec's complete example). Keys starting with "_" are comments.

Selectors name targets, in state keys, group values and steps (specificity in brackets):
  "blinds.ch02"              one target (3)
  "privacy_blinds"           a group (2); a group's value is a selector or a list of them, never another group
  "blinds", "sonos.lounge"   a prefix at a dot boundary: every target under it (1)
  "page.*.timed_reload"      a wildcard (fnmatch; * can cross dots) (1)
  ["a", "b"]                 a list, each item on its own terms
Within one mode the more specific selector wins for a target; two of equal specificity with different entries are
an error. JSON key order never matters. A selector that matches no target is an error unless its subsystem is a
placeholder: an adapter flagged `placeholder`, or a subsystem no adapter provides (it becomes one). Dotted names the
config uses for a placeholder that its device config doesn't list ("plug.dmx_power") become its members.

Entries:
  value | {"set": v}                     hold v
  {"store": slot, "set": v}              save the current value in slot (unless it holds one), then hold v
  {"store": slot}                        save only
  {"retrieve": slot, "else": v|"leave"}  put the saved value back and clear it; if nothing usable is saved, hold `else`
  null                                   unmanaged: drop an inherited entry
  options: "policy" (respect | enforce | ignore; default respect, ignore for a retrieve), "opts" (adapter hints),
           and on retrieve only "max_age_s" (and "else")
Through extends, per target: (1) a value or {"set"} replaces the value and the options it names, keeping the rest of
an inherited set or store entry; over a retrieve it becomes a plain set entry; (2) an object with store or retrieve
replaces the entry; (3) an object with only options patches it; (4) null drops it. Every set and else value goes
through its adapter's normalize() at load, so entries hold canonical values.

Transition rules: {"from": sel, "to": sel, "steps": [...]}; the first match wins. sel is a mode name, "@name" (that
mode and every mode built on it), "*" (anything, including no mode yet), or a list of these. A "* -> *" rule is
required. Steps: a selector or a list of them (parallel); {"targets": sel, "on_fail": "continue" | "abort" |
{"abort_on": sel, "rollback"?: true, "allow_skipped"?: false}}; {"delay": s}; {"signal": "subsystem[.member].action",
"args"?: {}}; "rest" (every other pending target; appended if the rule leaves it out). Modes' on_enter / on_exit are
lists of signals; they are not inherited (they fire at the mode's family edge).

RoomConfig(...) raises ConfigError on the first problem; validate(...) returns every problem as
[{"path": <JSON pointer into the config>, "message": ...}] for inline errors in an editor; lint() returns warnings.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .contract import Adapter, Placeholder

LEAVE = "leave"
_UNSET = object()
VERBS = {"set", "store", "retrieve"}
OPTIONS = {"policy", "opts", "else", "max_age_s"}
RETRIEVE_ONLY = {"else", "max_age_s"}
TOP_KEYS = {"default", "labels", "groups", "modes", "transitions", "description"}
MODE_KEYS = {"label", "extends", "abstract", "state", "on_enter", "on_exit", "description"}
RULE_KEYS = {"from", "to", "steps", "description"}
POLICIES = {"respect", "enforce", "ignore"}
WILDCARD = set("*?[")
SIGNAL = re.compile(r"[^.\s]+(\.[^.\s]+)+")
REMOVED = {
    "class": "'class' is gone: in a transition rule '@<mode>' means that mode and every mode that extends it",
    "sequence": "v1's 'sequence' is gone: order the room with 'transitions' (ROOM_STATE_MODEL §2.4)",
    "after": "v1's 'after' is gone: order the room with 'transitions' (ROOM_STATE_MODEL §2.4)",
}


class ConfigError(ValueError):
    pass


def ptr(*parts: Any) -> str:
    """JSON pointer (RFC 6901) from path parts."""
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def revision(raw: dict) -> str:
    """A stable id for a config's content: the editor's etag for conflicting saves. Key order is kept (an editor
    shows it), so reordering counts as a change."""
    return hashlib.sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest()[:12]


def _integer_like(key: str) -> bool:
    """JavaScript objects move integer-like keys to the front, which would reorder entries in a browser editor."""
    return key.isdigit()


def _own(d: Any) -> dict:
    """Without the "_" comment keys ({} for anything that isn't an object)."""
    return {k: v for k, v in d.items() if not str(k).startswith("_")} if isinstance(d, dict) else {}


@dataclass(frozen=True)
class Entry:
    set: Any = _UNSET                # canonical (normalized at load)
    store: str | None = None
    retrieve: str | None = None
    else_: Any = LEAVE
    policy: str = "respect"
    max_age_s: float | None = None
    opts: dict = field(default_factory=dict)

    @property
    def sets(self) -> bool:
        return self.set is not _UNSET

    def verb(self) -> str:
        if self.retrieve:
            return "retrieve"
        if self.store:
            return "store+set" if self.sets else "store"
        return "set"


@dataclass
class Step:
    kind: str                        # "targets" | "delay" | "signal" | "rest"
    targets: list[str] = field(default_factory=list)
    seconds: float = 0.0
    signal: str = ""                 # the whole name, "sonos.lounge.pause"
    subsystem: str = ""
    member: str | None = None        # "lounge"; None for "<subsystem>.<action>"
    action: str = ""                 # "pause"
    args: dict = field(default_factory=dict)
    abort_on: set[str] = field(default_factory=set)   # guarded targets: abort if one isn't ok
    rollback: bool = True            # after an abort, go back to the last completed mode
    allow_skipped: bool = False      # a skipped guarded target (placeholder, dry run) doesn't abort
    n: int = 0                       # position in its rule, from 1 (0 for a mode's signals)


@dataclass
class Mode:
    name: str
    label: str
    abstract: bool
    extends: str | None
    family: list[str]                # its ancestors and itself, outermost first: ["house", "live", "disco"]
    state: dict[str, Entry]          # concrete target -> entry, after extends, in target order
    origin: dict[str, tuple[str, str]]   # concrete target -> (mode, selector) its entry came from, for editors
    on_enter: list[Step]             # its own signals only
    on_exit: list[Step]
    descendants: set[str] = field(default_factory=set)   # itself and every mode built on it: what "@name" matches


@dataclass
class Rule:
    from_: list[str]                 # "*", "@name" or mode names
    to: list[str]
    steps: list[Step]                # always holds a "rest" step

    @property
    def catch_all(self) -> bool:
        return "*" in self.from_ and "*" in self.to


class RoomConfig:
    def __init__(self, raw: dict, adapters: Iterable[Adapter], collect: bool = False) -> None:
        """`adapters`: one per subsystem, placeholders included; normalize() and value checks come from them."""
        if not isinstance(raw, dict):
            raise ConfigError("/: the config must be a JSON object")
        self.raw, self.collect = raw, collect
        self.errors: list[dict] = []
        self.revision = revision(raw)
        self.adapters: dict[str, Adapter] = {a.subsystem: a for a in adapters}
        self.members = {s: list(a.members) for s, a in self.adapters.items()}
        self.targets = [f"{s}.{m}" for s, ms in self.members.items() for m in ms]
        self._auto: set[str] = set()                      # subsystems that became placeholders here
        self._auto_at: dict[str, str] = {}                # ... and the JSON pointer where each was first named
        self._keys(raw, TOP_KEYS, "")
        raw_groups = self._object(raw.get("groups"), ptr("groups"))
        valid = []
        for name in raw_groups:
            if _integer_like(name):
                self._fail(ptr("groups", name), "a group name can't be a number")
            elif "." in name or name in self.adapters or name in ("rest", "*"):
                self._fail(ptr("groups", name), "a group name can't contain '.', shadow a subsystem, or be 'rest' or '*'")
            else:
                valid.append(name)
        self._group_names = set(valid)                   # known before any is expanded, so groups can't nest
        self._placeholder_members(raw)
        self.groups: dict[str, list[str]] = {}
        for name in valid:
            self.groups[name] = self._expand(raw_groups[name], ptr("groups", name), in_group=True)
        self.modes: dict[str, Mode] = {}
        self._specs: dict[str, dict[str, dict]] = {}     # mode -> target -> the entry's keys, after extends
        self._own_state: dict[str, dict] = {}            # mode -> its own state, as written
        self._raw_modes = self._object(raw.get("modes"), ptr("modes"))
        for name in self._raw_modes:
            if _integer_like(name):
                self._fail(ptr("modes", name), "a mode name can't be a number")
            else:
                self._resolve(name, [])
        for m in self.modes.values():
            for name in m.family:
                self.modes[name].descendants.add(m.name)
        d = raw.get("default") or next(iter(self.modes), None)
        self.default = d if isinstance(d, str) else None
        if not self.is_concrete(self.default):
            self._fail(ptr("default"), f"default mode {d!r} is missing or abstract")
        rules = raw.get("transitions", [])
        if not isinstance(rules, list):
            self._fail(ptr("transitions"), "transitions must be a list of rules")
            rules = []
        self.transitions = [self._parse_rule(i, r) for i, r in enumerate(rules)]
        if not any(r.catch_all for r in self.transitions):
            self._fail(ptr("transitions"),
                       "a '* -> *' rule is required (put it last), so the fallback order is written down")
        self._check_slots()
        self.labels: dict[str, str] = {}
        for t, label in self._object(raw.get("labels"), ptr("labels")).items():
            if t in self.targets or ("." in t and self._is_placeholder(t.split(".", 1)[0])):
                self.labels[t] = str(label)
            else:
                self._fail(ptr("labels", t), f"unknown target {t!r}")

    def _fail(self, path: str, message: str) -> None:
        if not self.collect:
            raise ConfigError(f"{path or '/'}: {message}")
        self.errors.append({"path": path, "message": message})

    def _keys(self, d: dict, allowed: set[str], path: str) -> None:
        for k in sorted(d.keys() - allowed):
            if not str(k).startswith("_"):
                self._fail(path + ptr(k), REMOVED.get(k, f"unknown key {k!r}"))

    def _object(self, value: Any, path: str) -> dict:
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._fail(path, "must be an object")
            return {}
        return _own(value)

    # selectors ------------------------------------------------------------------------------------
    def _is_placeholder(self, sub: str) -> bool:
        a = self.adapters.get(sub)
        return a is None or a.placeholder

    def _placeholder(self, sub: str, path: str = "") -> None:
        """A subsystem no adapter provides becomes a placeholder; the members config names are its members."""
        if sub not in self.adapters:
            self.adapters[sub] = Placeholder(sub)
            self.members[sub] = self.adapters[sub].members
            self._auto.add(sub)
        if sub in self._auto and path and not self._auto_at.get(sub):
            self._auto_at[sub] = path

    def _select(self, sel: Any, path: str, in_group: bool = False) -> list[tuple[str, int]]:
        """(target, specificity) for every target `sel` names."""
        if isinstance(sel, list):
            return [hit for i, s in enumerate(sel) for hit in self._select(s, path + ptr(i), in_group)]
        if not isinstance(sel, str) or not sel:
            self._fail(path, f"a selector is a string or a list of strings, got {sel!r}")
            return []
        if sel == "rest":
            self._fail(path, "'rest' is reserved: it is a step of its own")
            return []
        if sel in self._group_names:
            if in_group:
                self._fail(path, f"a group can't contain another group ({sel!r})")
                return []
            return [(t, 2) for t in self.groups.get(sel, [])]
        wild = bool(WILDCARD & set(sel))
        if wild:
            hits = [(t, 1) for t in self.targets if fnmatch.fnmatchcase(t, sel)]
        elif sel in self.targets:
            return [(sel, 3)]
        else:
            hits = [(t, 1) for t in self.targets if t.startswith(sel + ".")]
        if hits:
            return hits
        sub = sel.split(".", 1)[0]
        if WILDCARD & set(sub) or not self._is_placeholder(sub):
            self._fail(path, f"selector {sel!r} matches no target")
            return []
        self._placeholder(sub, path)                      # accepted; it names nothing (yet)
        return []

    def _placeholder_members(self, raw: dict) -> None:
        """Members of placeholders that no device config lists, from the dotted names the config uses
        ("plug.dmx_power"). A name that is a prefix of another ("sonos.lounge") stays a prefix."""
        names: dict[str, str] = {}                       # dotted name -> where it was first named

        def walk(sel: Any, path: str) -> None:
            if isinstance(sel, list):
                for i, s in enumerate(sel):
                    walk(s, path + ptr(i))
            elif (isinstance(sel, str) and "." in sel and not WILDCARD & set(sel)
                  and sel.split(".", 1)[0] not in self._group_names and self._is_placeholder(sel.split(".", 1)[0])):
                names.setdefault(sel, path)
        for g, sel in _own(raw.get("groups")).items():
            walk(sel, ptr("groups", g))
        for name, m in _own(raw.get("modes")).items():
            for k in _own(m.get("state") if isinstance(m, dict) else None):
                walk(k, ptr("modes", name, "state", k))
        for i, r in enumerate(raw.get("transitions") if isinstance(raw.get("transitions"), list) else []):
            steps = r.get("steps") if isinstance(r, dict) else None
            for j, s in enumerate(steps if isinstance(steps, list) else []):
                at = ptr("transitions", i, "steps", j)
                if isinstance(s, dict):
                    walk(s.get("targets"), at + "/targets")
                    walk(s["on_fail"].get("abort_on") if isinstance(s.get("on_fail"), dict) else None, at + "/on_fail/abort_on")
                else:
                    walk(s, at)
        for n, at in names.items():
            if n not in self.targets and not any(t.startswith(n + ".") for t in self.targets + list(names)):
                sub, member = n.split(".", 1)
                self._placeholder(sub, at)
                self.members[sub].append(member)
                self.targets.append(n)

    def _expand(self, sel: Any, path: str, in_group: bool = False) -> list[str]:
        return list(dict.fromkeys(t for t, _ in self._select(sel, path, in_group)))

    # entries --------------------------------------------------------------------------------------
    def _spec(self, raw: Any, path: str) -> dict | None:
        """One state entry as the keys it names ({"set": v} for a plain value), or None if it is invalid."""
        if not (isinstance(raw, dict) and raw.keys() & (VERBS | OPTIONS)):
            return {"set": raw}
        d = _own(raw)
        problems = []
        if d.keys() - VERBS - OPTIONS:
            problems.append((path, f"unknown keys {sorted(d.keys() - VERBS - OPTIONS)}"))
        if "retrieve" in d and d.keys() & {"set", "store"}:
            problems.append((path, "retrieve can't be combined with set or store"))
        for key in sorted(d.keys() & RETRIEVE_ONLY):
            if d.keys() & {"set", "store"}:
                problems.append((f"{path}/{key}", f"{key} is allowed on retrieve only"))
        for key in ("store", "retrieve"):
            if key in d and not (isinstance(d[key], str) and d[key]):
                problems.append((f"{path}/{key}", f"{key} needs a slot name"))
        if "policy" in d and d["policy"] not in POLICIES:
            problems.append((f"{path}/policy", f"policy must be one of {sorted(POLICIES)}"))
        if "max_age_s" in d and (isinstance(d["max_age_s"], bool) or not isinstance(d["max_age_s"], (int, float))
                                 or d["max_age_s"] <= 0):
            problems.append((f"{path}/max_age_s", "max_age_s must be a positive number"))
        if "opts" in d and not isinstance(d["opts"], dict):
            problems.append((f"{path}/opts", "opts must be an object"))
        for p, message in problems:
            self._fail(p, message)
        return None if problems else d

    def _normalize(self, t: str, value: Any) -> Any:
        sub, member = t.split(".", 1)
        return self.adapters[sub].normalize(member, value)

    def _own_entries(self, name: str, state: dict) -> dict[str, list[str]]:
        """selector -> the targets it wins in one mode's own state: the most specific selector wins, and two equally
        specific ones with different entries are an error."""
        hits: dict[str, list[tuple[int, str]]] = {}
        for sel in state:
            path = ptr("modes", name, "state", sel)
            if _integer_like(sel):
                self._fail(path, "a selector can't be a number")
                continue
            for t, specificity in self._select(sel, path):
                hits.setdefault(t, []).append((specificity, sel))
        out: dict[str, list[str]] = {}
        conflicts: dict[tuple[str, str], list[str]] = {}
        for t, hs in hits.items():
            top = max(s for s, _ in hs)
            sels = list(dict.fromkeys(sel for s, sel in hs if s == top))
            for other in sels[1:]:
                if state[other] != state[sels[0]]:
                    conflicts.setdefault((sels[0], other), []).append(t)
            out.setdefault(sels[0], []).append(t)
        for (a, b), ts in conflicts.items():
            self._fail(ptr("modes", name, "state", b),
                       f"{', '.join(ts)}: named by {a!r} and {b!r}, equally specific, with different entries")
        return out

    # modes ----------------------------------------------------------------------------------------
    def _resolve(self, name: str, chain: list[str]) -> Mode | None:
        if name in self.modes:
            return self.modes[name]
        if name in chain:
            self._fail(ptr("modes", chain[-1], "extends"), f"extends cycle: {' -> '.join(chain + [name])}")
            return None
        raw = self._raw_modes.get(name)
        if not isinstance(raw, dict):
            self._fail(ptr("modes", chain[-1], "extends") if chain else ptr("modes", name),
                       f"extends unknown mode {name!r}" if chain else "a mode must be an object")
            return None
        self._keys(raw, MODE_KEYS, ptr("modes", name))
        ext = raw.get("extends")
        if ext is not None and not isinstance(ext, str):
            self._fail(ptr("modes", name, "extends"), "extends names one mode")
            ext = None
        parent = self._resolve(ext, chain + [name]) if ext else None
        specs = dict(self._specs.get(parent.name, {})) if parent else {}
        origin = dict(parent.origin) if parent else {}
        state = self._own_state[name] = self._object(raw.get("state"), ptr("modes", name, "state"))
        winners = self._own_entries(name, state)
        for sel in state.keys() - winners.keys():         # a selector a more specific one beats is still checked
            if state[sel] is not None and not _integer_like(sel):
                self._spec(state[sel], ptr("modes", name, "state", sel))
        for sel, targets in winners.items():
            path, val = ptr("modes", name, "state", sel), state[sel]
            if val is None:                               # merge rule 4
                for t in targets:
                    specs.pop(t, None)
                    origin.pop(t, None)
                continue
            spec = self._spec(val, path)
            if spec is None:
                continue
            verb_form = isinstance(val, dict) and bool(val.keys() & (VERBS | OPTIONS))
            reported: set[str] = set()                    # one error per path, naming the first target
            for t in targets:
                child, key, problem = dict(spec), "set", None
                try:
                    for key in ("set", "else"):
                        if key in child and not (key == "else" and child[key] == LEAVE):
                            child[key] = self._normalize(t, child[key])
                except Exception as e:                    # normalize() raises ValueError for a bad value
                    problem = (f"{path}/{key}" if verb_form or key == "else" else path, f"{t}: {e}")
                else:
                    merged = _merge(specs.get(t), child)
                    if not merged.keys() & VERBS:
                        problem = (path, f"{t}: needs one of set, store, retrieve (there is no inherited entry to patch)")
                    elif merged.keys() & RETRIEVE_ONLY and "retrieve" not in merged:
                        problem = (path, f"{t}: {', '.join(sorted(merged.keys() & RETRIEVE_ONLY))} allowed on retrieve only")
                    else:
                        specs[t], origin[t] = merged, (name, sel)
                if problem and problem[0] not in reported:
                    reported.add(problem[0])
                    self._fail(*problem)
        self._specs[name] = specs
        signals = {}
        for key in ("on_enter", "on_exit"):
            items = raw.get(key) or []
            if not isinstance(items, list):
                self._fail(ptr("modes", name, key), f"{key} must be a list of signals")
                items = []
            signals[key] = [s for i, x in enumerate(items) if (s := self._parse_signal(x, ptr("modes", name, key, i)))]
        mode = Mode(name, str(raw.get("label", name)), bool(raw.get("abstract")), ext,
                    (parent.family if parent else []) + [name],
                    {t: _entry(specs[t]) for t in self.targets if t in specs},
                    {t: origin[t] for t in self.targets if t in origin},
                    signals["on_enter"], signals["on_exit"])
        self.modes[name] = mode
        return mode

    def mode(self, name: str) -> Mode:
        """A mode that can be requested; ConfigError for an unknown or abstract one."""
        m = self.modes.get(name)
        if m is None or m.abstract:
            raise ConfigError(f"no such mode {name!r}" if m is None else f"{name!r} is abstract")
        return m

    def is_concrete(self, name: str | None) -> bool:
        return name in self.modes and not self.modes[name].abstract

    def concrete(self) -> list[str]:
        return [n for n, m in self.modes.items() if not m.abstract]

    def uses(self, slot: str, t: str) -> bool:
        """Some mode stores into or retrieves (slot, t)."""
        return (slot, t) in self._uses

    def _check_slots(self) -> None:
        """Every (slot, target) some mode retrieves must be stored by some mode: catches slot-name typos."""
        stored = {(e.store, t) for m in self.modes.values() for t, e in m.state.items() if e.store}
        self._uses = stored | {(e.retrieve, t) for m in self.modes.values() for t, e in m.state.items() if e.retrieve}
        for m in self.modes.values():
            state = self._own_state[m.name]
            missing: dict[str, list[str]] = {}
            for t, e in m.state.items():
                mode, sel = m.origin[t]
                if e.retrieve and (e.retrieve, t) not in stored and mode == m.name and "retrieve" in state[sel]:
                    missing.setdefault(sel, []).append(t)
            for sel, ts in missing.items():
                self._fail(ptr("modes", m.name, "state", sel, "retrieve"),
                           f"no mode stores {', '.join(ts)} into slot {m.state[ts[0]].retrieve!r}")

    # transitions ----------------------------------------------------------------------------------
    def _parse_signal(self, raw: Any, path: str, n: int = 0) -> Step | None:
        if not isinstance(raw, dict) or "signal" not in raw:
            self._fail(path, 'expected a signal: {"signal": "<subsystem>[.<member>].<action>", "args": {...}}')
            return None
        d = _own(raw)
        self._keys(d, {"signal", "args"}, path)
        sig, args = d["signal"], d.get("args", {})
        if not isinstance(sig, str) or not SIGNAL.fullmatch(sig):
            self._fail(f"{path}/signal", "signal must be '<subsystem>.<action>' or '<subsystem>.<member>.<action>'")
            return None
        if not isinstance(args, dict):
            self._fail(f"{path}/args", "args must be an object")
            return None
        sub, rest = sig.split(".", 1)                     # the first dot ends the subsystem, the last starts the action
        member, _, action = rest.rpartition(".")
        self._placeholder(sub, f"{path}/signal")
        return Step("signal", signal=sig, subsystem=sub, member=member or None, action=action, args=args, n=n)

    def _parse_step(self, raw: Any, path: str, n: int) -> Step | None:
        if raw == "rest":
            return Step("rest", n=n)
        if isinstance(raw, (str, list)):
            return Step("targets", self._expand(raw, path), n=n)
        if isinstance(raw, dict):
            d = _own(raw)
            if "signal" in d:
                return self._parse_signal(raw, path, n)
            if "delay" in d:
                self._keys(d, {"delay"}, path)
                s = d["delay"]
                if isinstance(s, bool) or not isinstance(s, (int, float)) or s < 0:
                    self._fail(f"{path}/delay", "delay must be a number of seconds")
                    return None
                return Step("delay", seconds=float(s), n=n)
            if "targets" in d:
                self._keys(d, {"targets", "on_fail"}, path)
                targets = self._expand(d["targets"], f"{path}/targets")
                return Step("targets", targets, n=n, **self._on_fail(d.get("on_fail", "continue"), targets,
                                                                    f"{path}/on_fail"))
            if any(isinstance(v, dict) and "after" in v for v in d.values()):
                self._fail(path, REMOVED["after"])
                return None
        self._fail(path, f"can't parse step {raw!r}")
        return None

    def _on_fail(self, raw: Any, targets: list[str], path: str) -> dict:
        if raw == "continue":
            return {}
        if raw == "abort":
            return {"abort_on": set(targets)}
        if not (isinstance(raw, dict) and "abort_on" in raw):
            self._fail(path, 'on_fail must be "continue", "abort" or '
                             '{"abort_on": <selector>, "rollback"?, "allow_skipped"?}')
            return {}
        d = _own(raw)
        self._keys(d, {"abort_on", "rollback", "allow_skipped"}, path)
        guarded = set(self._expand(d["abort_on"], f"{path}/abort_on"))
        if guarded - set(targets):
            self._fail(f"{path}/abort_on",
                       f"abort_on names targets this step doesn't send: {sorted(guarded - set(targets))}")
        out: dict[str, Any] = {"abort_on": guarded & set(targets)}
        for key, default in (("rollback", True), ("allow_skipped", False)):
            out[key] = d.get(key, default)
            if not isinstance(out[key], bool):
                self._fail(f"{path}/{key}", f"{key} must be true or false")
                out[key] = default
        return out

    def _endpoint(self, s: Any, path: str) -> bool:
        if s == "*":
            return True
        name = s[1:] if isinstance(s, str) and s.startswith("@") else s
        if not isinstance(name, str) or name not in self.modes:
            self._fail(path, f"unknown mode {s!r}")
            return False
        if name == s and self.modes[name].abstract:
            self._fail(path, f"{s!r} is abstract and never entered: did you mean '@{s}'?")
            return False
        return True

    def _parse_rule(self, i: int, raw: Any) -> Rule:
        path = ptr("transitions", i)
        if not isinstance(raw, dict):
            self._fail(path, "a transition rule must be an object")
            return Rule([], [], [])
        d = _own(raw)
        self._keys(d, RULE_KEYS, path)
        ends: dict[str, list[str]] = {}
        for key in ("from", "to"):
            sel = d.get(key, "*")
            items = sel if isinstance(sel, list) else [sel]
            ends[key] = [s for j, s in enumerate(items)
                         if self._endpoint(s, path + ptr(key) + (ptr(j) if isinstance(sel, list) else ""))]
        raw_steps = d.get("steps", [])
        if not isinstance(raw_steps, list):
            self._fail(f"{path}/steps", "steps must be a list")
            raw_steps = []
        steps = [s for j, x in enumerate(raw_steps) if (s := self._parse_step(x, path + ptr("steps", j), j + 1))]
        if not any(s.kind == "rest" for s in steps):
            steps.append(Step("rest", n=len(raw_steps) + 1))
        return Rule(ends["from"], ends["to"], steps)

    def _matches(self, sels: list[str], mode: str | None) -> bool:
        for s in sels:
            if s == "*":
                return True                               # also "no mode yet" (first boot)
            if mode is not None and (mode in self.modes[s[1:]].descendants if s[0] == "@" else s == mode):
                return True
        return False

    def transition_for(self, from_modes: Any, to_mode: str) -> int | None:
        """Index of the first rule whose `to` matches `to_mode` and whose `from` matches any of `from_modes`
        ({current} | via, §4.3; a single name, or None for no mode yet, also works)."""
        froms = list(from_modes) if isinstance(from_modes, (list, tuple, set, frozenset)) else [from_modes]
        for i, r in enumerate(self.transitions):
            if self._matches(r.to, to_mode) and any(self._matches(r.from_, f) for f in froms):
                return i
        return None

    def plan_steps(self, from_modes: Any, to_mode: str) -> list[Step]:
        """The matching rule's steps, "rest" included."""
        i = self.transition_for(from_modes, to_mode)
        return list(self.transitions[i].steps) if i is not None else [Step("rest", n=1)]

    def rules(self) -> list[dict]:
        """Which rule (index) runs for every pair of concrete modes, including first boot (from None)."""
        names = self.concrete()
        return [{"from": f, "to": t, "rule": self.transition_for(f, t)} for f in [None] + names for t in names if f != t]

    # warnings -------------------------------------------------------------------------------------
    def lint(self) -> list[dict]:
        """Things that are valid but probably mistakes, as [{"path", "code", "message"}] (§2.5)."""
        out: list[dict] = []

        def warn(path: str, code: str, message: str) -> None:
            out.append({"path": path, "code": code, "message": message})
        stored: dict[str, list[str]] = {}
        for m in self.modes.values():
            for t, e in m.state.items():
                if e.store and t not in stored.setdefault(e.store, []):
                    stored[e.store].append(t)
        for slot, ts in stored.items():
            never = [t for t in ts if not any(m.state.get(t) and m.state[t].retrieve == slot for m in self.modes.values())]
            if never:
                warn("", "slot_never_retrieved", f"slot {slot!r}: no mode retrieves {', '.join(never)}")
        names = self.concrete()
        pairs = [(f, t) for f in [None] + names for t in names]
        used = {self.transition_for(f, t) for f, t in pairs}
        for i in range(len(self.transitions)):
            if i not in used:
                warn(ptr("transitions", i), "rule_unused", "an earlier rule always matches first")
        covered = {n for r in self.transitions if not r.catch_all for s in r.from_ + r.to if s != "*"
                   for n in (self.modes[s[1:]].descendants if s[0] == "@" else {s})}
        fallen: dict[int, list[str]] = {}
        for f, t in pairs:
            i = self.transition_for(f, t)
            if f is not None and f != t and i is not None and self.transitions[i].catch_all and {f, t} & covered:
                fallen.setdefault(i, []).append(f"{f} -> {t}")
        for i, ps in fallen.items():
            warn(ptr("transitions", i), "falls_to_catch_all",
                 f"{', '.join(ps)} use the catch-all, though a side has rules of its own")
        for m in self.modes.values():
            parent = self.modes.get(m.extends) if m.extends else None
            if parent is None:
                continue
            family_stores = {e.store for n in m.family for e in self.modes[n].state.values() if e.store}
            hits: dict[tuple[str, str], list[str]] = {}
            for t, e in m.state.items():
                p = parent.state.get(t)
                if m.origin[t][0] == m.name and e.sets and not e.store and p and p.retrieve in family_stores:
                    hits.setdefault((m.origin[t][1], p.retrieve), []).append(t)
            for (sel, slot), ts in hits.items():
                warn(ptr("modes", m.name, "state", sel), "set_over_retrieve",
                     f"{', '.join(ts)}: retrieved from {slot!r} by a base but set here without storing it first, so the "
                     f"value from before the episode won't come back; write {{\"store\": \"{slot}\", \"set\": ...}}")
        for i, r in enumerate(self.transitions):
            for s in r.steps:
                weak = sorted(t for t in s.abort_on if self._inactive(t))
                if weak:
                    warn(ptr("transitions", i, "steps", s.n - 1, "on_fail"), "guard_inactive",
                         f"{', '.join(weak)}: placeholder or dry run, so this guard "
                         + ("is not enforced (allow_skipped)" if s.allow_skipped else "aborts whenever it has to send"))
        in_state = {t.split(".", 1)[0] for m in self.modes.values() for t in m.state}
        for sub in sorted(self._auto):                     # named in config, but no adapter or device config knows it
            warn(self._auto_at.get(sub, ""), "unknown_subsystem",
                 f"{sub!r} has no adapter or device config, so it is a placeholder and nothing is sent to it "
                 "(a misspelt group or subsystem name?)")
        for sub, a in self.adapters.items():
            if (a.placeholder and sub not in self._auto and sub in in_state
                    and getattr(a, "values", None) is None):
                warn("", "placeholder_values_unchecked",
                     f"{sub!r}: a placeholder with no values in its device config; its values aren't checked")
        return out

    def _inactive(self, t: str) -> bool:
        a = self.adapters.get(t.split(".", 1)[0])
        return a is None or a.placeholder or a.dry_run


def _merge(parent: dict | None, child: dict) -> dict:
    """Merge rules 1-3 for one target (§2.4); rule 4 (null drops) is the caller's."""
    if child.keys() & {"store", "retrieve"}:
        return child                                      # 2: replaces the inherited entry completely
    if "set" in child and (parent is None or "retrieve" in parent):
        return child                                      # 1 over a retrieve: a plain set entry, its options dropped
    return {**(parent or {}), **child}                    # 1 over a set or store entry, and 3: patch what it names


def _entry(d: dict) -> Entry:
    return Entry(set=d.get("set", _UNSET), store=d.get("store"), retrieve=d.get("retrieve"),
                 else_=d.get("else", LEAVE), policy=d.get("policy", "ignore" if "retrieve" in d else "respect"),
                 max_age_s=d.get("max_age_s"), opts=d.get("opts", {}))


def sets_value(mode: Mode, t: str) -> Any:
    """The value `mode` sets `t` to (a plain value, set, or store+set); None for a retrieve, a store only or no entry."""
    e = mode.state.get(t)
    return e.set if e is not None and e.sets else None


def mode_signals(cfg: RoomConfig, name: str, key: str) -> list[Step]:
    """A mode's own on_enter or on_exit signals; [] for a mode `cfg` no longer has."""
    m = cfg.modes.get(name)
    return list(getattr(m, key)) if m else []


def validate(raw: Any, adapters: Iterable[Adapter], warnings: bool = False) -> list[dict]:
    """Every problem in a config, as [{"path": JSON pointer, "message": ...}]: no errors means it can be saved.
    With warnings=True, lint() warnings follow, as {"path", "message", "code", "level": "warning"}, for the editor."""
    if not isinstance(raw, dict):
        return [{"path": "", "message": "the config must be a JSON object"}]
    try:
        cfg = RoomConfig(raw, adapters, collect=True)
    except Exception as e:                                # a shape the collector didn't anticipate
        return [{"path": "", "message": f"invalid config: {e!r}"}]
    out = list(cfg.errors)
    if warnings and not cfg.errors:
        out += [{**w, "level": "warning"} for w in cfg.lint()]
    return out
