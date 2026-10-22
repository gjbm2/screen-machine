"""Member value schemas: the renderer catalog's JSON Schema subset (LIVE_V1 §1.1), so ParamForm renders both.

Keywords: type (integer | number | string | boolean | object), minimum, maximum, enum, default, title, description.
UI hints in "x-ui": label, step, unit, widget (slider | number | select | checkbox | color), plus for room targets:
  presets: {"raised": 0, "lowered": 100}     quick-set buttons that insert a value
  labels:  {"art_mode": "Frame Art Mode"}    display names for enum values
  ends:    {"min": "Raised", "max": "Lowered"}  names for a slider's two ends
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


def enum_schema(values: list[str], label: str, labels: dict[str, str] | None = None, default: str | None = None) -> dict:
    ui = {"label": label, "widget": "select"}
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
    retrieve_only = {"else": {}, "max_age_s": {"type": "number", "exclusiveMinimum": 0}}
    entry = {"oneOf": [
        {"type": "null", "description": "unmanaged (drop an inherited entry)"},
        obj({"retrieve": slot, **common, **retrieve_only}, required=["retrieve"],
            description="put the stored value back, else `else`"),
        {**obj({"set": {}, "store": slot, **common}, description="set, or store then set, or store only"),
         **has("set", "store"), "not": {"required": ["retrieve"]}},
        {**obj({**common, **retrieve_only}, description="options only: patch the inherited entry"),
         **has(*common, *retrieve_only), "not": has("set", "store", "retrieve")},
        {"not": {"anyOf": [{"type": "null"}, {"type": "object", **has("set", "store", "retrieve", *common,
                                                                       *retrieve_only)}]},
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
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Room state config",
        **obj({
            "default": {"type": "string"},
            "description": {"type": "string"},
            "labels": {"type": "object", "additionalProperties": {"type": "string"},
                       "propertyNames": {"anyOf": [{"enum": sorted(target_schemas)}, {"pattern": "^_"}]}},
            "groups": {"type": "object", "patternProperties": comment, "additionalProperties": selector},
            "modes": {"type": "object", "patternProperties": comment, "additionalProperties": obj({
                "label": {"type": "string"}, "description": {"type": "string"},
                "extends": {"type": "string"}, "abstract": {"type": "boolean"},
                "state": {"type": "object", "patternProperties": comment, "additionalProperties": entry},
                "on_enter": {"type": "array", "items": signal}, "on_exit": {"type": "array", "items": signal}})},
            "transitions": {"type": "array", "items": obj({"from": ends, "to": ends, "description": {"type": "string"},
                                                           "steps": {"type": "array", "items": step}})},
        }, required=["modes", "transitions"]),
        "$defs": {t: s for t, s in sorted(target_schemas.items())},     # value schema per target
        "x-groups": sorted(groups),
    }
