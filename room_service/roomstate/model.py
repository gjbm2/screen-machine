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
placeholder: an adapter flagged `placeholder`, or a subsystem no adapter provides (it becomes one); or its adapter's
members come and go at runtime (`dynamic_members`: a light not registered yet, or removed, names nothing). Dotted names the
config uses for a placeholder that its device config doesn't list ("plug.dmx_power") become its members.

Entries:
  value | {"set": v}                     hold v
  {"store": slot, "set": v}              save the current value in slot (unless it holds one), then hold v
  {"store": slot}                        save only
  {"retrieve": slot, "else": v|"leave"}  put the saved value back and clear it; if nothing usable is saved, hold `else`
  null                                   unmanaged: drop an inherited entry
  options: "policy" (respect | enforce | ignore; default respect, ignore for a retrieve), "opts" (adapter hints),
           "skip_if" ({target: [value patterns]}: left alone while one matches), and on retrieve only "max_age_s"
           (and "else")
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

from .schema import AUTOPILOT_KEYS, ICONS, MODE_AUTOPILOT_KEYS, ROOM_PAGE_PLACES, default_policy
from .contract import Adapter, Placeholder

LEAVE = "leave"
_UNSET = object()
VERBS = {"set", "store", "retrieve"}
OPTIONS = {"policy", "opts", "else", "max_age_s", "store_if", "restore", "skip_if"}
# skip_if ("Leave it as it is while", owner 22 Oct: Piano leaves Sonos alone while our own piano sound plays on it, by
# config): {"<target or selector>": [value patterns], ...}, on any entry. When the mode is entered, or a step would
# send the row, and any named target's believed value matches one of its patterns, the row is left out of that entry:
# not stored, not set, a skipped row saying why; a re-assert or retry waits while it holds. An unknown value never holds.
RESTORE_SLOT = "_restore"
# restore (owner, 16 Oct: "any mode ... can STORE setting x and then RESTORE it after", generic, no named slots):
# {"set": v, "restore": true} stores the target's current value on entry (if nothing is stored for it yet), sets v, and
# the next mode puts it back on leaving, unless that mode sets the target itself (then it owns it) or also restores it
# (then the original value is kept for later). max_age_s on such an entry: an older stored value isn't put back.
RETRIEVE_ONLY = {"else", "max_age_s"}
# store_if: value patterns (as in a rule's `when`); store only when the current value matches one, e.g.
# ["favourite", "uri"] stores Sonos music only while something is playing, so a retrieve then has nothing to undo.
TOP_KEYS = {"default", "labels", "groups", "modes", "transitions", "description", "autopilot", "put_back_max_age_s"}
MODE_KEYS = {"label", "extends", "abstract", "state", "on_enter", "on_exit", "description", "autopilot", "icon", "voice",
             "on_room_page"}
# "on_room_page": where the mode's tile sits on the Room page, "main" (the mode row, the default) or "more" (behind
# the row's "More modes" chip). A plain per-mode field (owner, 24 Oct), read by the page only.
# "voice": the mode's short name for Alexa (owner 18 Oct; routes/alexa.py feeds only these to its AI step); "" = none.
VOICE_MAX = 40
# "autopilot" (show_brain's Auto, docs/SHOW_ENGINE.md §8.9): read by show_brain, carried here as an object, unchecked
# until its schema settles.
RULE_KEYS = {"from", "to", "when", "steps", "description"}
_ANY = object()


def value_matches(pattern: Any, value: Any) -> bool:
    """A value pattern (`when`, store_if, skip_if, Auto's skip): equal, "*" for anything, a key of an object value
    ("live" matches {"live": {...}}), a string with * ? [ matching a string value (fnmatch, case-sensitive), or an
    object whose every key's pattern matches that key of an object value, by these same rules
    ({"uri": "http://*/piano/*"} matches {"uri": "http://host/piano/x.flac", "volume": 40})."""
    if pattern == "*" or pattern == value:
        return True
    if isinstance(pattern, str):
        if isinstance(value, dict):
            return pattern in value
        return isinstance(value, str) and bool(WILDCARD & set(pattern)) and fnmatch.fnmatchcase(value, pattern)
    if isinstance(pattern, dict) and pattern and isinstance(value, dict):
        return all(k in value and value_matches(p, value[k]) for k, p in pattern.items())
    return False


def pattern_problem(pattern: Any) -> str | None:
    """Why a value pattern can't be used, or None. An object pattern names at least one key (an empty one would say
    nothing about the value), and each of its keys' patterns is one too; anything else JSON is a pattern."""
    if isinstance(pattern, dict):
        if not pattern:
            return "bad value pattern: an empty {} names nothing to look for"
        for k, p in pattern.items():
            if not isinstance(k, str):
                return f"bad value pattern: {k!r} is not a field name"
            if why := pattern_problem(p):
                return why
        return None
    if pattern is None or isinstance(pattern, (str, int, float, bool, list)):
        return None
    return f"bad value pattern: {pattern!r} is not a JSON value"
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
    store_if: tuple = ()             # value patterns: store only a current value that matches one (empty: always)
    restore: bool = False            # "put back afterwards": stored in RESTORE_SLOT on entry, put back on leaving
    skip_if: dict = field(default_factory=dict)   # "leave it as it is while", as written: {selector: [patterns]}
    skip_targets: tuple = ()         # ... resolved: ((target, (patterns, ...)), ...); empty: always applies

    @property
    def sets(self) -> bool:
        return self.set is not _UNSET

    def verb(self) -> str:
        if self.restore:
            return "set+restore"
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
    description: str = ""
    icon: str | None = None
    voice: str = ""
    on_room_page: str = "main"


@dataclass
class Rule:
    from_: list[str]                 # "*", "@name" or mode names
    to: list[str]
    steps: list[Step]                # always holds a "rest" step
    # `when`: alternatives (any one holds), each a list of (targets, from pattern, to pattern) that must all hold for
    # some changing target. A change is (believed value, desired value) for a target the transition will touch.
    when: list[list[tuple[list[str], Any, Any]]] = field(default_factory=list)

    @property
    def catch_all(self) -> bool:
        return "*" in self.from_ and "*" in self.to and not self.when

    def applies(self, changes: dict[str, tuple[Any, Any]] | None) -> bool:
        if not self.when:
            return True
        if changes is None:                               # no state to judge by (the rules() matrix): never
            return False
        return any(all(any(t in changes and (fp is _ANY or value_matches(fp, changes[t][0]))
                           and (tp is _ANY or value_matches(tp, changes[t][1])) for t in ts)
                       for ts, fp, tp in alt)
                   for alt in self.when)


class RoomConfig:
    def __init__(self, raw: dict, adapters: Iterable[Adapter], collect: bool = False,
                 extra_groups: dict[str, list[str]] | None = None) -> None:
        """`adapters`: one per subsystem, placeholders included; normalize() and value checks come from them.
        `extra_groups`: groups defined outside this file (room_service derives "<room>:<kind>" from rooms.json); they
        don't count towards the revision, and a group of the same name in the file is an error."""
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
        pb = raw.get("put_back_max_age_s")
        if pb is not None and (isinstance(pb, bool) or not isinstance(pb, (int, float)) or pb <= 0):
            self._fail(ptr("put_back_max_age_s"), "put_back_max_age_s must be a positive number")
            pb = None
        self.put_back_max_age_s: float | None = pb
        raw_groups = self._object(raw.get("groups"), ptr("groups"))
        valid = []
        for name in raw_groups:
            if _integer_like(name):
                self._fail(ptr("groups", name), "a group name can't be a number")
            elif "." in name or name in self.adapters or name in ("rest", "*"):
                self._fail(ptr("groups", name), "a group name can't contain '.', shadow a subsystem, or be 'rest' or '*'")
            else:
                valid.append(name)
        self.derived: dict[str, list[str]] = dict(extra_groups or {})
        for name in self.derived:
            if name in raw_groups:
                self._fail(ptr("groups", name), f"{name!r} is defined by rooms.json; rename this group")
                valid.remove(name)
        self._group_names = set(valid) | set(self.derived)  # known before any is expanded, so groups can't nest
        self._placeholder_members(raw)
        self.groups: dict[str, list[str]] = {}
        for name, ts in self.derived.items():             # exact targets, already checked against the adapters
            self.groups[name] = [t for t in ts if t in self.targets]
        for name in valid:
            self.groups[name] = self._expand(raw_groups[name], ptr("groups", name), in_group=True)
        self._autopilot_refs: list[tuple[str, dict]] = []
        self._check_autopilot(raw.get("autopilot"), ptr("autopilot"), AUTOPILOT_KEYS)
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
        self._check_autopilot_refs()
        self.labels: dict[str, str] = {}
        for t, label in self._object(raw.get("labels"), ptr("labels")).items():
            if t in self.targets or ("." in t and (self._is_placeholder(t.split(".", 1)[0])
                                                   or self._dynamic(t.split(".", 1)[0]))):
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

    def _dynamic(self, sub: str) -> bool:
        """An adapter whose members come and go at runtime (`dynamic_members`: light, show_service's registered lights):
        a selector naming one it doesn't have (yet, or any more) names nothing, never an error."""
        return bool(getattr(self.adapters.get(sub), "dynamic_members", False))

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
        if WILDCARD & set(sub) or not (self._is_placeholder(sub) or self._dynamic(sub)):
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

    def _condition_targets(self, sel: Any) -> list[str] | None:
        """The targets a skip_if name picks: a target, a group, a prefix or a wildcard, as a state key does. [] for a
        name under a placeholder or dynamic subsystem that names nothing (yet); None when it names nothing real (a
        typo: never a new placeholder)."""
        if not isinstance(sel, str) or not sel:
            return None
        if sel in self._group_names:
            return list(self.groups.get(sel, []))
        if WILDCARD & set(sel):
            hits = [t for t in self.targets if fnmatch.fnmatchcase(t, sel)]
        else:
            hits = [sel] if sel in self.targets else [t for t in self.targets if t.startswith(sel + ".")]
        sub = sel.split(".", 1)[0]
        if hits or (not WILDCARD & set(sub) and sub in self.adapters
                    and (self.adapters[sub].placeholder or self._dynamic(sub))):
            return hits
        return None

    def _skip_targets(self, d: dict) -> tuple:
        """An entry's skip_if as ((target, (patterns, ...)), ...), each target once, its patterns merged."""
        out: dict[str, list] = {}
        for sel, pats in _own(d.get("skip_if")).items():
            for t in self._condition_targets(sel) or []:
                have = out.setdefault(t, [])
                have += [p for p in pats if p not in have]
        return tuple((t, tuple(ps)) for t, ps in out.items())

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
        if "restore" in d and (not isinstance(d["restore"], bool) or d.keys() & {"store", "retrieve"}
                               or (d["restore"] and "set" not in d)):
            problems.append((f"{path}/restore", "restore is true or false, on an entry that sets a value "
                                                "(it replaces store and retrieve)"))
        for key in ("store", "retrieve"):
            if isinstance(d.get(key), str) and d[key].startswith("_"):
                problems.append((f"{path}/{key}", "slot names starting with '_' are reserved"))
        for key in sorted(d.keys() & RETRIEVE_ONLY):
            if key == "max_age_s" and d.get("restore"):
                continue                                  # the age limit on putting it back
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
        if "store_if" in d and (not isinstance(d["store_if"], list) or not (d.keys() & {"store", "restore"})):
            problems.append((f"{path}/store_if", "store_if is a list of value patterns, on an entry that stores"))
        elif "store_if" in d:
            problems += [(f"{path}/store_if/{i}", why) for i, p in enumerate(d["store_if"]) if (why := pattern_problem(p))]
        if "skip_if" in d:
            si = d["skip_if"]
            if not (isinstance(si, dict) and _own(si) and all(isinstance(p, list) and p for p in _own(si).values())):
                problems.append((f"{path}/skip_if", "skip_if is an object of {name: [value patterns]}"))
            else:
                for sel, pats in _own(si).items():
                    at = f"{path}/skip_if" + ptr(sel)
                    if self._condition_targets(sel) is None:
                        problems.append((at, f"skip_if names an unknown target: {sel}"))
                    problems += [(at + ptr(i), why) for i, p in enumerate(pats) if (why := pattern_problem(p))]
        if "opts" in d and not isinstance(d["opts"], dict):
            problems.append((f"{path}/opts", "opts must be an object"))
        for p, message in problems:
            self._fail(p, message)
        return None if problems else d

    def _normalize(self, t: str, value: Any) -> Any:
        sub, member = t.split(".", 1)
        return self.adapters[sub].normalize(member, value)

    def _default_policy(self, t: str) -> str | None:
        sub, member = t.split(".", 1)
        try:
            return default_policy(self.adapters[sub].value_schema(member))
        except Exception:                 # a schema the adapter can't give yet: the plain default
            return None

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
                    elif ((merged.keys() & RETRIEVE_ONLY) - ({"max_age_s"} if merged.get("restore") else set())
                          and "retrieve" not in merged):
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
        icon = raw.get("icon")
        if icon is not None and icon not in ICONS:
            self._fail(ptr("modes", name, "icon"), f"icon must be one of {list(ICONS)}")
            icon = None
        self._check_autopilot(raw.get("autopilot"), ptr("modes", name, "autopilot"), MODE_AUTOPILOT_KEYS)
        place = raw.get("on_room_page", "main")
        if place not in ROOM_PAGE_PLACES:
            self._fail(ptr("modes", name, "on_room_page"), f"on_room_page must be one of {list(ROOM_PAGE_PLACES)}")
            place = "main"
        voice = raw.get("voice")
        if voice is not None and not (isinstance(voice, str) and len(voice.strip()) <= VOICE_MAX):
            self._fail(ptr("modes", name, "voice"), f"voice is a short name (up to {VOICE_MAX} letters)")
            voice = None
        mode = Mode(name, str(raw.get("label", name)), bool(raw.get("abstract")), ext,
                    (parent.family if parent else []) + [name],
                    {t: _entry(specs[t], self._default_policy(t), self._skip_targets(specs[t]))
                     for t in self.targets if t in specs},
                    {t: origin[t] for t in self.targets if t in origin},
                    signals["on_enter"], signals["on_exit"],
                    description=str(raw.get("description") or ""), icon=icon,
                    voice=" ".join(str(voice or "").split()), on_room_page=place)
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
        return Rule(ends["from"], ends["to"], steps, self._parse_when(d.get("when"), path + "/when"))

    def _parse_when(self, raw: Any, path: str) -> list[list[tuple[list[str], Any, Any]]]:
        """`when`: {selector: cond, ...} (all must hold) or a list of those (any one). A cond is a value pattern (the
        target's new value) or {"from"?: pattern, "to"?: pattern}. Patterns: a value, "*", or an object key ("live")."""
        if raw is None:
            return []
        alts = raw if isinstance(raw, list) else [raw]
        out = []
        for i, alt in enumerate(alts):
            at = path + (ptr(i) if isinstance(raw, list) else "")
            if not isinstance(alt, dict) or not alt:
                self._fail(at, "when: an object of {selector: condition}, or a list of them")
                continue
            conds = []
            for sel, cond in _own(alt).items():
                cpath = at + ptr(sel)
                ts = self._expand(sel, cpath)
                if isinstance(cond, dict):
                    if not cond or set(cond) - {"from", "to"}:
                        self._fail(cpath, "a condition is a value, or {\"from\"?: ..., \"to\"?: ...}")
                        continue
                    bad = [(k, why) for k in ("from", "to") if k in cond and (why := pattern_problem(cond[k]))]
                    for k, why in bad:
                        self._fail(cpath + ptr(k), why)
                    if bad:
                        continue
                    conds.append((ts, cond.get("from", _ANY), cond.get("to", _ANY)))
                else:
                    if why := pattern_problem(cond):
                        self._fail(cpath, why)
                        continue
                    conds.append((ts, _ANY, cond))
            if conds:
                out.append(conds)
        return out

    def _matches(self, sels: list[str], mode: str | None) -> bool:
        for s in sels:
            if s == "*":
                return True                               # also "no mode yet" (first boot)
            if mode is not None and (mode in self.modes[s[1:]].descendants if s[0] == "@" else s == mode):
                return True
        return False

    def transition_for(self, from_modes: Any, to_mode: str,
                       changes: dict[str, tuple[Any, Any]] | None = None) -> int | None:
        """Index of the first rule whose `to` matches `to_mode`, whose `from` matches any of `from_modes`
        ({current} | via, §4.3; a single name, or None for no mode yet, also works) and whose `when` holds for
        `changes` (target -> (believed, desired) for what the transition touches)."""
        froms = list(from_modes) if isinstance(from_modes, (list, tuple, set, frozenset)) else [from_modes]
        for i, r in enumerate(self.transitions):
            if self._matches(r.to, to_mode) and any(self._matches(r.from_, f) for f in froms) and r.applies(changes):
                return i
        return None

    def plan_steps(self, from_modes: Any, to_mode: str, changes: dict[str, tuple[Any, Any]] | None = None) -> list[Step]:
        """The matching rule's steps, "rest" included."""
        i = self.transition_for(from_modes, to_mode, changes)
        return list(self.transitions[i].steps) if i is not None else [Step("rest", n=1)]

    def rules(self) -> list[dict]:
        """Which rule (index) runs for every pair of concrete modes, including first boot (from None)."""
        names = self.concrete()
        return [{"from": f, "to": t, "rule": self.transition_for(f, t)} for f in [None] + names for t in names if f != t]

    # warnings -------------------------------------------------------------------------------------
    def _check_autopilot(self, ap: Any, path: str, keys: tuple) -> None:
        """Auto's blocks (show_brain, SHOW_ENGINE §8.9): known keys, modes that exist, real skip selectors. The
        situation words inside conditions are show_brain's vocabulary and aren't checked here."""
        if ap is None:
            return
        if not isinstance(ap, dict):
            self._fail(path, "autopilot must be an object")
            return
        for k in sorted(set(_own(ap)) - set(keys)):
            self._fail(f"{path}/{k}", f"unknown key {k!r}")
        self._autopilot_refs.append((path, ap))

    def _check_autopilot_refs(self) -> None:
        """After every mode is known: modes named by Auto exist; skip names real targets."""
        for path, ap in self._autopilot_refs:
            for key in ("from", "leave_to"):
                names = ap.get(key)
                for i, n in enumerate(names if isinstance(names, list) else [names] if names else []):
                    if not (self.is_concrete(n) or (key == "leave_to" and n == "previous")):
                        self._fail(f"{path}/{key}" + (ptr(i) if isinstance(names, list) else ""), f"unknown mode {n!r}")
            for i, item in enumerate(ap.get("skip") or []):
                at = f"{path}/skip/{i}"
                if isinstance(item, str):
                    self._expand(item, at)
                elif isinstance(item, dict) and item:
                    for sel, cond in item.items():
                        self._expand(sel, at + ptr(sel))
                        if not (isinstance(cond, dict) and set(cond) <= {"from"}):
                            self._fail(at + ptr(sel), 'a conditional skip is {"<selector>": {"from": <value>}}')
                else:
                    self._fail(at, "skip items are selectors or {selector: {from: value}}")

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
            if slot == RESTORE_SLOT:
                continue                                  # put back by whichever mode comes next: never named
            never = [t for t in ts if not any(m.state.get(t) and m.state[t].retrieve == slot for m in self.modes.values())]
            if never:
                warn("", "slot_never_retrieved", f"slot {slot!r}: no mode retrieves {', '.join(never)}")
        names = self.concrete()
        pairs = [(f, t) for f in [None] + names for t in names]
        used = {self.transition_for(f, t) for f, t in pairs}
        for i in range(len(self.transitions)):
            if i not in used and not self.transitions[i].when:   # a `when` rule depends on state, not on names
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
        for name, m in self.modes.items():
            raw = _own(self.raw.get("modes", {})).get(name) or {}
            if raw.get("extends") or raw.get("abstract"):
                warn(ptr("modes", name), "legacy_inheritance", f"{name!r} uses extends/abstract")
            for sel, val in _own(raw.get("state") if isinstance(raw, dict) else None).items():
                if isinstance(val, dict) and (val.get("retrieve") or val.get("store")):
                    warn(ptr("modes", name, "state", sel), "legacy_slot", f"{sel}: store/retrieve slots")
        return out

    def _inactive(self, t: str) -> bool:
        a = self.adapters.get(t.split(".", 1)[0])
        return a is None or a.placeholder or a.dry_run


def _merge(parent: dict | None, child: dict) -> dict:
    """Merge rules 1-3 for one target (§2.4); rule 4 (null drops) is the caller's."""
    if child.keys() & {"store", "retrieve", "restore"}:
        return child                                      # 2: replaces the inherited entry completely
    if "set" in child and (parent is None or "retrieve" in parent):
        return child                                      # 1 over a retrieve: a plain set entry, its options dropped
    return {**(parent or {}), **child}                    # 1 over a set or store entry, and 3: patch what it names


def _entry(d: dict, default_policy: str | None = None, skip_targets: tuple = ()) -> Entry:
    """default_policy: the target's own (its value schema's x-ui.default_policy), for a row that names none.
    skip_targets: d's skip_if resolved to targets (RoomConfig._skip_targets)."""
    restore = bool(d.get("restore"))
    return Entry(set=d.get("set", _UNSET), store=RESTORE_SLOT if restore else d.get("store"), retrieve=d.get("retrieve"),
                 else_=d.get("else", LEAVE),
                 policy=d.get("policy", "ignore" if "retrieve" in d else default_policy or "respect"),
                 max_age_s=d.get("max_age_s"), opts=d.get("opts", {}), store_if=tuple(d.get("store_if", ())),
                 restore=restore, skip_if=_own(d.get("skip_if")), skip_targets=tuple(skip_targets))


def sets_value(mode: Mode, t: str) -> Any:
    """The value `mode` sets `t` to (a plain value, set, or store+set); None for a retrieve, a store only or no entry."""
    e = mode.state.get(t)
    return e.set if e is not None and e.sets else None


def mode_signals(cfg: RoomConfig, name: str, key: str) -> list[Step]:
    """A mode's own on_enter or on_exit signals; [] for a mode `cfg` no longer has."""
    m = cfg.modes.get(name)
    return list(getattr(m, key)) if m else []


def validate(raw: Any, adapters: Iterable[Adapter], warnings: bool = False,
             extra_groups: dict[str, list[str]] | None = None) -> list[dict]:
    """Every problem in a config, as [{"path": JSON pointer, "message": ...}]: no errors means it can be saved.
    With warnings=True, lint() warnings follow, as {"path", "message", "code", "level": "warning"}, for the editor."""
    if not isinstance(raw, dict):
        return [explain({"path": "", "message": "the config must be a JSON object"})]
    try:
        cfg = RoomConfig(raw, adapters, collect=True, extra_groups=extra_groups)
    except Exception as e:                                # a shape the collector didn't anticipate
        return [explain({"path": "", "message": f"invalid config: {e!r}"})]
    out = [explain(e) for e in cfg.errors]
    if warnings and not cfg.errors:
        out += [explain({**w, "level": "warning"}) for w in cfg.lint()]
    return out


# page words for problems (c8 review #4): what the Room page shows. Never mentions slots, retrieve, store_if or pointers.
_ERRORS = [   # (substring of the internal message, code, text; {sel} = the row's selector, {detail} = after the colon)
    ("matches no target", "no_such_target", "This picks something that doesn't exist: {sel}"),
    ("restore is true or false", "put_back_needs_value", "Put back afterwards needs a value to set first"),
    ("reserved", "reserved_name", "Names starting with _ are reserved"),
    ("needs one of set, store, retrieve", "no_value", "This row needs a value"),
    ("store_if is a list", "bad_remember_if", "'Remember it only if' needs choices, on a row that puts back"),
    ("skip_if is an object", "bad_leave_alone_while",
     "'Leave it as it is while' needs a setting and the values to look for"),
    ("skip_if names an unknown target", "bad_leave_alone_while",
     "'Leave it as it is while' names an unknown setting: {detail}"),
    ("bad value pattern", "bad_pattern", "A value to look for can't be used: {detail}"),
    ("policy must be", "bad_policy", "'Keep it there' can only be on or off"),
    ("max_age_s must be", "bad_age", "'Only if it was changed within' needs a positive number of seconds"),
    ("put_back_max_age_s must be", "bad_age", "The default put-back time needs a positive number of seconds"),
    ("rule is required", "no_catch_all", "The ordering rules need a last 'everything else' rule"),
    ("missing or abstract", "bad_default", "The starting mode doesn't exist"),
    ("unknown mode", "no_such_mode", "This names a mode that doesn't exist"),
    ("icon must be", "bad_icon", "That icon isn't one of the room's icons"),
    ("on_room_page must be", "bad_room_page", "The tile can only be in the mode row or under More modes"),
    ("unknown key", "unknown_setting", "This has a setting the room doesn't know: {detail}"),
    ("is not one of", "bad_value", "That isn't one of its choices: {detail}"),
]
_WARNINGS = {
    "guard_inactive": "{who} aren't wired up yet, so the screens can light up before they're done",
    "rule_unused": "An earlier ordering rule always wins, so this one never runs",
    "falls_to_catch_all": "Some switches use the 'everything else' order",
    "unknown_subsystem": "This names something the room doesn't know (a misspelt name?)",
    "placeholder_values_unchecked": "This device isn't set up yet, so its choices can't be checked",
    "legacy_slot": "This row uses the old remember/put-back pairs; switch on 'Put back afterwards' instead",
    "legacy_inheritance": "This mode is built on another mode; modes are flat now, so copy its rows in",
    "slot_never_retrieved": "This row remembers a value that nothing puts back",
    "slot_never_stored": "This row puts back a value that nothing remembers",
    "set_over_retrieve": "This row sets a value without putting the old one back afterwards",
    "rooms_json": "The rooms file couldn't be read; the last good room groups are in use",
}
_JARGON = (("store_if", "remember-only-if"), ("retrieve", "put back"), ("restore", "put back"), (" slot", ""),
           ("JSON pointer", ""), ("selector", "name"))


def _unescape(part: str) -> str:
    return part.replace("~1", "/").replace("~0", "~")


def explain(item: dict) -> dict:
    """An error or warning with {mode, target, code, text} added, in the Room page's words; `path` stays for placing it."""
    parts = [_unescape(p) for p in item.get("path", "").split("/")[1:]]
    mode = parts[1] if len(parts) > 1 and parts[0] == "modes" else None
    target = parts[3] if len(parts) > 3 and parts[0] == "modes" and parts[2] == "state" else None
    msg = item.get("message", "")
    detail = msg.split(": ", 1)[1] if ": " in msg else msg
    if item.get("code") in _WARNINGS:
        code, text = item["code"], _WARNINGS[item["code"]]
        if code == "guard_inactive":
            names = msg.split(":", 1)[0]
            text = ("The blinds aren't wired up yet, so the screens can light up before the blinds are down"
                    if "blinds." in names else text.format(who=names))
    else:
        code, text = item.get("code") or "invalid", None
        for needle, c, t in _ERRORS:
            if needle in msg:
                code, text = c, t.format(sel=target or detail, detail=detail)
                break
        if text is None:
            text = msg
    for a, b in _JARGON:
        text = text.replace(a, b)
    return {**item, "mode": mode, "target": target, "code": code, "text": text.strip()}
