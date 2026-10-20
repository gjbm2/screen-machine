"""Member value schemas: the renderer catalog's JSON Schema subset (LIVE_V1 §1.1), so ParamForm renders both.

Keywords: type (integer | number | string | boolean | object), minimum, maximum, enum, default, title, description.
UI hints in "x-ui": label, step, unit, widget (slider | number | select | checkbox | color), plus for room targets:
  presets: {"raised": 0, "lowered": 100}     quick-set buttons that insert a value
  labels:  {"art_mode": "Frame Art Mode"}    display names for enum values
  ends:    {"min": "Raised", "max": "Lowered"}  names for a slider's two ends
  switch:  {"on": v1, "off": v2}               draw an on/off switch that writes these values
  unset_label: "Any time"                      what leaving it unset means
  requires: "restore"                          shown only while that sibling option is on
and on a target's value schema, for its row options (entry_schema):
  default_policy: "ignore"                     the policy a row that names none gets (e.g. music: never fought)
  store_if_labels: [{"value": [patterns], "label": "Only if music was playing"}]   choices for store_if
An empty schema {} accepts any JSON value (e.g. screens, whose live spec has its own editor).
"""

from __future__ import annotations

from typing import Any

_TYPES = {"integer": int, "number": (int, float), "string": str, "boolean": bool, "object": dict, "array": list}


def check_value(value: Any, schema: dict) -> str | None:
    """None if `value` fits the schema, otherwise a short reason."""
    if not schema:
        return None
    t = schema.get("type")
    if t:
        py = _TYPES.get(t)
        if py is None:
            return f"unsupported schema type {t!r}"
        if isinstance(value, bool) and t in ("integer", "number"):
            return f"expected {t}, got {value!r}"
        if t == "integer" and isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, py):
            return f"expected {t}, got {value!r}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{value!r} is not one of {schema['enum']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"{value} is below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"{value} is above maximum {schema['maximum']}"
    return None


def enum_schema(values: list[str], label: str, labels: dict[str, str] | None = None, default: str | None = None,
                widget: str | None = None) -> dict:
    """widget only when asked: without one the page picks (a few choices become buttons)."""
    ui: dict = {"label": label}
    if widget:
        ui["widget"] = widget
    if labels:
        ui["labels"] = labels
    return {"type": "string", "enum": list(values), "default": default or values[0], "x-ui": ui}


def range_schema(lo: int, hi: int, label: str, unit: str = "", step: int = 1, default: int | None = None,
                 presets: dict[str, int] | None = None, ends: tuple[str, str] | None = None) -> dict:
    """`ends` names the slider's two ends, e.g. ("Raised", "Lowered") for a blind where 0 is raised."""
    ui: dict = {"label": label, "widget": "slider", "step": step}
    if unit:
        ui["unit"] = unit
    if presets:
        ui["presets"] = presets
    if ends:
        ui["ends"] = {"min": ends[0], "max": ends[1]}
    return {"type": "integer", "minimum": lo, "maximum": hi, "default": lo if default is None else default, "x-ui": ui}


ICONS = ("home", "music", "piano", "party", "disco", "film", "moon", "sun", "sparkles", "power")   # names; the page draws them
PUT_BACK_PRESETS = {"1 hour": 3600, "3 hours": 10800, "12 hours": 43200}
AUTOPILOT_KEYS = ("on", "quiet_hours", "min_dwell_s", "max_switches", "backoff_s", "hold", "chips")
MODE_AUTOPILOT_KEYS = ("enter_when", "otherwise_suggest_when", "from", "leave_when", "leave_to", "skip")
AUTOPILOT_LABELS = {"quiet_hours": "Quiet hours", "min_dwell_s": "Stay in a mode at least",
                    "max_switches": "Most switches", "backoff_s": "Wait after a failed switch",
                    "hold": "Hold after a choice by hand", "chips": "Chips"}


def _age(label: str, default: float | None = None, **ui) -> dict:
    """An age limit in seconds: presets, or unset for any age."""
    ui = {"label": label, "widget": "select", "unit": "s", "presets": dict(PUT_BACK_PRESETS), "unset_label": "Any time",
          **ui}
    return {"type": "number", "minimum": 1, **({"default": default} if default else {}), "x-ui": ui}


def store_if_choices(value_schema: dict | None) -> list[dict]:
    """[{value: [patterns], label}] from a value schema's x-ui.store_if_labels (a list of those, or {pattern: label})."""
    raw = ((value_schema or {}).get("x-ui") or {}).get("store_if_labels")
    if isinstance(raw, dict):
        return [{"value": [k], "label": str(v)} for k, v in raw.items()]
    out = []
    for c in raw if isinstance(raw, list) else []:
        if isinstance(c, dict) and "value" in c:
            out.append({"value": c["value"] if isinstance(c["value"], list) else [c["value"]],
                        "label": str(c.get("label", c["value"]))})
    return out


def default_policy(value_schema: dict | None) -> str | None:
    """The policy a row names none of gets for this target (x-ui.default_policy), if the adapter says."""
    p = ((value_schema or {}).get("x-ui") or {}).get("default_policy")
    return p if p in ("respect", "enforce", "ignore") else None


def entry_schema(value_schema: dict | None = None, put_back_max_age_s: float | None = None) -> dict:
    """A state row's options (c8): what the Room page draws beside a row's value. Per target, a value schema's
    default_policy and store_if_labels hints shape policy and store_if."""
    off = default_policy(value_schema)
    off = off if off in ("respect", "ignore") else "respect"
    choices = store_if_choices(value_schema)
    store_if: dict = {"type": "array", "x-ui": {"label": "Remember it only if", "requires": "restore",
                                                "unset_label": "Always"}}
    if choices:
        store_if.update(enum=[c["value"] for c in choices])
        store_if["x-ui"].update(widget="select", enum_labels=[c["label"] for c in choices])
    else:
        store_if["x-ui"]["advanced"] = True
    return {"type": "object", "properties": {
        "restore": {"type": "boolean", "default": False, "x-ui": {
            "label": "Put back afterwards", "widget": "checkbox",
            "description": "Remember what it was before this mode and put it back when the room leaves it, "
                           "unless the next mode sets it itself"}},
        "max_age_s": _age("Only if it was changed within", put_back_max_age_s, requires="restore"),
        "store_if": store_if,
        "policy": {"type": "string", "enum": [off, "enforce"], "default": off, "x-ui": {
            "label": "Keep it there", "widget": "checkbox", "switch": {"on": "enforce", "off": off},
            "description": "If someone moves it while this mode is on, move it back once"}},
        "opts": {"type": "object", "x-ui": {"label": "Device hints", "advanced": True}},
    }, "x-ui": {"label": "Options"}}


def mode_autopilot_schema(modes: list[str], situations: list[dict] | None = None) -> dict:
    """A mode's Auto block (docs/SHOW_ENGINE.md §8.9). Conditions are show_brain's situations, not checked here;
    `situations` (the adapters' vocabulary) rides on each condition as x-ui.situations for the Auto editor."""
    names = {"type": "string", "enum": list(modes)}
    cond = {"type": "object", "x-ui": {"widget": "json", **({"situations": situations} if situations is not None else {})}}
    return {"type": "object", "x-ui": {"label": "Auto"}, "properties": {
        "enter_when": {**cond, "x-ui": {**cond["x-ui"], "label": "Switch to it when"}},
        "otherwise_suggest_when": {**cond, "x-ui": {**cond["x-ui"], "label": "Suggest it when"}},
        "from": {"type": "array", "items": names, "x-ui": {"label": "Only from", "unset_label": "Any mode"}},
        "leave_when": {**cond, "x-ui": {**cond["x-ui"], "label": "Leave it when"}},
        "leave_to": {"type": "string", "enum": ["previous", *modes],
                     "x-ui": {"label": "Then go to", "labels": {"previous": "The mode before"}}},
        "skip": {"type": "array", "x-ui": {"label": "Leave alone when Auto switches", "widget": "targets"}}}}


def mode_schema(modes: list[str], situations: list[dict] | None = None) -> dict:
    return {"type": "object", "properties": {
        "label": {"type": "string", "x-ui": {"label": "Name"}},
        "description": {"type": "string", "x-ui": {"label": "Description", "widget": "textarea"}},
        "icon": {"type": "string", "enum": list(ICONS), "x-ui": {"label": "Icon", "widget": "icon"}},
        "autopilot": mode_autopilot_schema(modes, situations)}}


def room_schema(modes: list[str], put_back_max_age_s: float | None = None) -> dict:
    return {"type": "object", "properties": {
        "default": {"type": "string", "enum": list(modes), "x-ui": {"label": "Starts in"}},
        "put_back_max_age_s": _age("Put back only if it was changed within", put_back_max_age_s,
                                   description="For rows with Put back afterwards that don't say"),
        "autopilot": {"type": "object", "x-ui": {"label": "Auto"}, "properties": {   # show_brain reads the rest
            "on": {"type": "boolean", "default": False, "x-ui": {"label": "Auto", "widget": "checkbox"}},
            **{k: {"x-ui": {"label": label, "advanced": True}} for k, label in AUTOPILOT_LABELS.items()}}}}}


def room_config_schema(target_schemas: dict[str, dict], groups: list[str]) -> dict:
    """A JSON Schema for the whole room config (ROOM_STATE_MODEL §2.4), for a raw-JSON editor (autocomplete, inline
    checks). Keys starting with "_" are comments everywhere.

    Per-target value schemas sit under $defs; each mode's state accepts any selector key, so it can't bind a value
    to one target's schema, and it can't tell an options-only patch from one with nothing to patch. validate() does
    both and returns JSON pointers.
    """
    comment = {"^_": {}}

    def obj(properties: dict, **extra) -> dict:
        return {"type": "object", "properties": properties, "patternProperties": comment,
                "additionalProperties": False, **extra}

    def has(*keys: str) -> dict:
        return {"anyOf": [{"required": [k]} for k in keys]}
    slot = {"type": "string", "minLength": 1}
    common = {"policy": {"enum": ["respect", "enforce", "ignore"]}, "opts": {"type": "object"}}
    age = {"type": "number", "exclusiveMinimum": 0}
    retrieve_only = {"else": {}, "max_age_s": age}
    put_back = {"restore": {"type": "boolean"}, "store_if": {"type": "array"}, "max_age_s": age}
    entry = {"oneOf": [
        {"type": "null", "description": "unmanaged (drop an inherited entry)"},
        obj({"retrieve": slot, **common, **retrieve_only}, required=["retrieve"],
            description="put the stored value back, else `else`"),
        {**obj({"set": {}, "store": slot, **common, **put_back},
               description="set (restore: put it back afterwards), or store then set, or store only"),
         **has("set", "store"), "not": {"required": ["retrieve"]},
         "dependentRequired": {"max_age_s": ["restore"], "restore": ["set"]}},
        {**obj({**common, **retrieve_only}, description="options only: patch the inherited entry"),
         **has(*common, *retrieve_only), "not": has("set", "store", "retrieve")},
        {"not": {"anyOf": [{"type": "null"}, {"type": "object", **has("set", "store", "retrieve", *common,
                                                                       *retrieve_only, *put_back)}]},
         "description": "a plain value: set it"},
    ]}
    selector = {"anyOf": [{"type": "string", "minLength": 1}, {"type": "array", "items": {"type": "string"}}]}
    signal = obj({"signal": {"type": "string", "pattern": r"^[^.\s]+(\.[^.\s]+)+$",
                             "description": "<subsystem>.<action> or <subsystem>.<member>.<action>"},
                  "args": {"type": "object"}}, required=["signal"])
    on_fail = {"anyOf": [{"enum": ["continue", "abort"]},
                         obj({"abort_on": selector, "rollback": {"type": "boolean"},
                              "allow_skipped": {"type": "boolean"}}, required=["abort_on"])]}
    step = {"anyOf": [
        selector,                                                              # "rest" is a string step too
        obj({"targets": selector, "on_fail": on_fail}, required=["targets"]),
        obj({"delay": {"type": "number", "minimum": 0}}, required=["delay"]),
        signal,
    ]}
    mode_ref = {"type": "string", "description": "a mode name, @name (it and its descendants) or *"}
    ends = {"anyOf": [mode_ref, {"type": "array", "items": mode_ref}]}
    cond = {"type": "object", "minProperties": 1}           # {selector: value pattern | {from?, to?}}
    names = {"anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]}
    skip = {"type": "array", "items": {"anyOf": [{"type": "string", "minLength": 1},
                                                 {"type": "object", "additionalProperties": obj(
                                                     {"from": {}}, required=["from"])}]}}
    autopilot = obj({"on": {"type": "boolean"}, **{k: {} for k in AUTOPILOT_KEYS if k != "on"}})
    mode_autopilot = obj({**{k: {} for k in MODE_AUTOPILOT_KEYS}, "from": names, "leave_to": {"type": "string"},
                          "skip": skip})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Room state config",
        **obj({
            "default": {"type": "string"},
            "description": {"type": "string"},
            "put_back_max_age_s": age,
            "autopilot": autopilot,
            "labels": {"type": "object", "additionalProperties": {"type": "string"},
                       "propertyNames": {"anyOf": [{"enum": sorted(target_schemas)}, {"pattern": "^_"}]}},
            "groups": {"type": "object", "patternProperties": comment, "additionalProperties": selector},
            "modes": {"type": "object", "patternProperties": comment, "additionalProperties": obj({
                "label": {"type": "string"}, "description": {"type": "string"},
                "icon": {"enum": list(ICONS)}, "autopilot": mode_autopilot,
                "extends": {"type": "string"}, "abstract": {"type": "boolean"},
                "state": {"type": "object", "patternProperties": comment, "additionalProperties": entry},
                "on_enter": {"type": "array", "items": signal}, "on_exit": {"type": "array", "items": signal}})},
            "transitions": {"type": "array", "items": obj({"from": ends, "to": ends, "description": {"type": "string"},
                                                           "when": {"anyOf": [cond, {"type": "array", "items": cond}]},
                                                           "steps": {"type": "array", "items": step}})},
        }, required=["modes", "transitions"]),
        "$defs": {t: s for t, s in sorted(target_schemas.items())},     # value schema per target
        "x-groups": sorted(groups),
    }
