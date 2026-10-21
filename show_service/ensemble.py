"""ensemble: the room's appliances as one band (docs/ENSEMBLE.md; the show builder's, 22 Oct).

The owner (22 Oct): "more of a conversation between room elements e.g. something might happen on south (left) and
then on north (right) and/or echo between -- likewise the relationship between the hue lights". Every appliance is a
**player**; the director picks an **interplay form** at look changes and routes musical **gestures** between them.

**Players** (``players``; §2): each screen fixture (``kind: screen``), each light group split by side ("Lamps, left" /
"Lamps, right"; unplaced or unsplit members in ``<group>:all``), and a room device (the glitterball) at the centre.
A fixture's ``x`` (-1 left .. +1 right), first rule that answers: the room space (space.lateral over the placed
fixtures, only when they span ``players.min_spread_m``), its ``zone`` through show.yaml ``ensemble.players.zones``,
the screen it hangs at (``screen:``), else 0. Side: x < -0.05 left, > 0.05 right, else centre. Players on one side form
a **team** (``teams``), ordered by the travel order (``flow_order``: the room's left to right, or the flow's).

**Gestures** (§3): accent, downbeat, phrase, fill (the clock's lines, bounded by the feel's ``accents`` word, the
clock's gate and beat_lock), hit (structure's drop line, else a drop heard), section (a look for a section, a song or a
repeat; a breakdown at half strength), swell (a build, up to the drop line), note and chord (piano bursts). Each is
published once as ``room.gesture`` (contract.GestureData) with a peak time per player (``parts``), on show's clock;
a re-send with the same id replaces it, ``parts: []`` calls it off. Spacing, a per-bar count and a strength floor keep
it musical (a hit always plays), each counted in the snapshot: musical spacing in config, never a safety stop.

**Forms** (config/live/interplays.yaml, ``Catalogue``; §4): unison, echo, call_and_response, ping_pong, canon,
solo_and_accompany, contrast, register_split. Hot-reloaded (mtime and size); a file that doesn't read keeps its last
good entries; an entry that can't be used is left out with a problem line. Tags are checked and scored with the light
scenes' own rule (light_scenes.Catalogue._tags_of, .score: tags.py's scene_weight x flow_fit), x each ``boost`` whose
need holds; an entry whose ``needs`` fail is not eligible. No eligible entry, or no file: the built-in ``unison``
(nothing routed: today's room).

**The pick** (``Ensemble``; §4.2): at each look change, when the piano starts or stops, and at a song change; a
weighted draw (the director's seeded rng), the form playing x ``pick.stay``; a new form needs the current one to have
played ``pick.dwell_bars`` (no grid: ``dwell_s``), except at a song change, when the piano starts with a piano form
eligible, or when the current form's needs stop holding. Keep this look holds the form, the roles and the staggers;
gestures go on. Every change goes out ahead as ``next`` (EnsembleNext) at its moment (a look's at_ms, a half-phrase or
phrase line at least the lead ahead), and is folded in once that moment has passed. Every look change sets ``next`` at
its at_ms even when nothing else moves, so a light group's program transition at that at_ms is staggered (§7.2).

**Roles** (even, lead, accompany; show.yaml ``ensemble.roles`` give each its scales), staggers (beats after the room's
moment a scene change lands on a player, capped at ``max_stagger_beats``), contrast's palette role and energy bias,
register_split's register window and canon's clock offset go out per player in EnsembleData (room.look and
room.direction ``ensemble``). The screens never take a scale as a gain (the renderer's rule, §8).

**Degrade.** No ``ensemble:`` block in show.yaml (``parse_settings(None)``): the room plays as today: the built-in
unison, no pick, nothing seeded or published on room.gesture (the director still carries the block). Neutral (no
palette), House lights or Lights low: no seeds are kept and the block is unison with ``source: neutral`` and no line.
Without the room placed or zones, every player is centre and only forms without ``needs: sides`` are eligible.

Pure: no I/O but the catalogue's file, no thread of its own. Every method runs on show-brain except ``block``,
``part_of``, ``scale``, ``lamp_parts`` and ``recent``, which read references swapped there. Nothing here names a
device, group, mode, vibe, scene, form or stream: every name lives in the YAML and the registry.
"""

from __future__ import annotations

import collections
import copy
import hashlib
import json
import logging
import math
import pathlib
import random
import string
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional, Sequence

from . import contract
from . import light_scenes as lscenes

log = logging.getLogger("show_service.ensemble")

LINE_PREFIX = "Playing together: "                 # page words
NEED_SIDES, NEED_GRID, NEED_PIANO = "sides", "grid", "piano"   # an entry's ``needs`` words (and ``boost``'s)
NEEDS: tuple[str, ...] = (NEED_SIDES, NEED_GRID, NEED_PIANO)
TOP_KEYS = frozenset({"version", "interplays"})
ENTRY_KEYS = frozenset({"label", "kind", "line", "params", "routes", "needs", "boost", "tags"})
LINE_FILLERS: tuple[str, ...] = ("order", "first", "second", "lead", "beats", "low", "high")
TEAM_BY: tuple[str, ...] = ("side", "player")
ORDERS: tuple[str, ...] = ("room", "flow", "caller")
SIDE_WORDS: tuple[str, ...] = ("left", "right")
CLOCK_LADDER: tuple[str, ...] = ("phrase", "downbeat", "accent", "fill")   # ACCENT_STYLES[i] allows the first i
BEAT_GATED: tuple[str, ...] = ("accent", "fill")  # these need the clock's gate at beat (downbeat, phrase: dmx)
UNISON_IN: dict[str, tuple[str, ...]] = {         # kinds a form plays on every player (the hit: every form)
    "call_and_response": ("section",), "register_split": ("section",), "solo_and_accompany": ("section", "phrase"),
    "unison": contract.GESTURE_KINDS, "contrast": contract.GESTURE_KINDS}
FILL_ROLL = "fill"                                # rolls player by player along the travel order, whatever team_by
SIDE_EDGE = 0.05                                  # |x| under this is the centre
VIA_RANK = {"space": 0, "zone": 1, "screen": 2, "none": 3}
NO_GRID_BAR_S = 2.0                               # max_per_bar counts per this many seconds without a grid
SEEN_KEEP_S = 30.0                                # clock lines remembered (each line once)
SCHED_KEEP_S = 10.0                               # each player's scheduled parts kept for spacing
LIFE_DECAYS = 8.0                                 # a part is over 8 decays after its hold (contract.gesture_weight)
CALL_OFF_KEEP_S = 5.0                             # a call-off stays on LookData.gestures this long
GRID_SETTLE_S = 2.0                               # the clock's grid moving alone republishes the block at most this
                                                  # often (a confidence flickering at its edge never floods the bus)
LAST_N = 10
REGISTER_A0 = 21                                  # MIDI A0 .. C8: register 0 .. 1
REGISTER_SPAN = 87
REGISTER_WORDS = ((0.4, "low"), (0.6, "middle"), (1.01, "high"))
MAX_SEEDS = 256                                   # queued moments (each routed at the next tick)
LOOK_WHY = {"section": "a new section", "repeat": "a section came back", "track": "a new song"}
LOOK_SECTION = ("section", "repeat", "track")     # a look for these is a section gesture; drop and relax: none
LOOK_NONE = ("drop", "relax")
KIND_WHY = {"accent": "the beat", "downbeat": "a downbeat", "phrase": "a new phrase", "fill": "a fill",
            "hit": "the drop", "section": "a new section", "swell": "the song builds"}
SHAPE_KEYS: tuple[str, ...] = ("attack_ms", "hold_ms", "decay_ms", "depth", "colour_depth", "kick")
SHAPE_SHARES = ("depth", "colour_depth", "kick")

#: show.yaml ``ensemble``'s starting points (docs/ENSEMBLE.md §10): the code's fallbacks. The room fact (which zone is
#: on which side) lives only in show.yaml: here ``zones`` is empty, so without it every player is centre.
ENSEMBLE_FALLBACK: dict[str, Any] = {
    "players": {"zones": {}, "split_groups": True, "min_spread_m": 0.5},
    "pick": {"dwell_bars": 16.0, "dwell_s": 45.0, "stay": 1.5},
    "roles": {"even": {"level": 1.0, "depth": 1.0, "motion": 1.0, "pulse": 1.0, "fx_density": 1.0, "gestures": 1.0},
              "lead": {"level": 1.0, "depth": 1.0, "motion": 1.0, "pulse": 1.0, "fx_density": 1.0, "gestures": 1.0},
              "accompany": {"level": 0.7, "depth": 0.6, "motion": 0.5, "pulse": 0.6, "fx_density": 0.5,
                            "gestures": 0.5}},
    "glide_beats": 2.0, "glide_s": 1.0, "no_grid_beat_s": 0.5, "max_stagger_beats": 16.0,
    "gestures": {
        "horizon_s": 0.25, "min_gap_ms": 180.0, "max_per_bar": 4, "min_strength": 0.05, "max_parts": 8, "keep": 32,
        "fill_energy": 0.6,
        "strength": {"accent": 0.35, "downbeat": 0.55, "phrase": 0.75, "fill": 0.45, "hit": 1.0, "section": 0.7,
                     "swell": 0.5, "note": 0.5, "chord": 0.7},
        "colours": {"accent": None, "downbeat": "accent", "phrase": "highlight", "fill": None, "hit": "highlight",
                    "section": "accent", "swell": "accent", "note": "accent", "chord": "highlight"},
        "shapes": {
            "accent": {"attack_ms": 0.0, "hold_ms": 40.0, "decay_ms": 160.0, "depth": 0.25, "colour_depth": 0.0,
                       "kick": 0.0},
            "downbeat": {"attack_ms": 0.0, "hold_ms": 60.0, "decay_ms": 220.0, "depth": 0.35, "colour_depth": 0.2,
                         "kick": 0.3},
            "phrase": {"attack_ms": 40.0, "hold_ms": 80.0, "decay_ms": 500.0, "depth": 0.45, "colour_depth": 0.4,
                       "kick": 0.5},
            "fill": {"attack_ms": 0.0, "hold_ms": 30.0, "decay_ms": 120.0, "depth": 0.25, "colour_depth": 0.0,
                     "kick": 0.2},
            "hit": {"attack_ms": 0.0, "hold_ms": 80.0, "decay_ms": 400.0, "depth": 1.0, "colour_depth": 0.6,
                    "kick": 1.0},
            "section": {"attack_ms": 80.0, "hold_ms": 120.0, "decay_ms": 900.0, "depth": 0.5, "colour_depth": 0.6,
                        "kick": 0.6},
            "swell": {"attack_ms": 0.0, "hold_ms": 0.0, "decay_ms": 600.0, "depth": 0.5, "colour_depth": 0.3,
                      "kick": 0.0},
            "note": {"attack_ms": 0.0, "hold_ms": 30.0, "decay_ms": 200.0, "depth": 0.3, "colour_depth": 0.3,
                     "kick": 0.0},
            "chord": {"attack_ms": 0.0, "hold_ms": 60.0, "decay_ms": 350.0, "depth": 0.5, "colour_depth": 0.5,
                      "kick": 0.3}},
        "virtual_lamps": {"min_attack_ms": 80.0, "swell_ms": 300.0}},
    "piano": {"burst_ms": 90.0, "max_burst_ms": 250.0, "chord_notes": 3, "min_vel": 20, "vel_curve": 1.5,
              "split": "auto", "split_range": [48, 72], "split_notes": 64},
}

#: kind -> param -> default (docs/ENSEMBLE.md §4.3). Every form has team_by and order; ``pulse`` softens the scene's
#: own unison beat pulse so the form's pattern reads (the table's pulse column). ``split`` None: piano.split.
_COMMON = {"team_by": "side", "order": "room"}
FORM_PARAMS: dict[str, dict[str, Any]] = {
    "unison": {**_COMMON, "pulse": 1.0},
    "echo": {**_COMMON, "order": "flow", "pulse": 0.8, "delay_beats": 1.0, "delay_s": 0.5, "decay": 0.6,
             "repeats": 1, "every_bars": 2, "caller": "first", "clock_offset": False},
    "call_and_response": {**_COMMON, "pulse": 0.8, "answer": "replay", "decay": 0.85, "half_s": 4.0,
                          "every_bars": 1},
    "ping_pong": {**_COMMON, "pulse": 0.4, "on": "beat", "downbeat_boost": 1.5, "scene_offset_beats": 1.0},
    "canon": {**_COMMON, "pulse": 0.7, "offset_beats": 2.0, "offset_s": 1.0, "every_bars": 1, "clock_offset": True},
    "solo_and_accompany": {**_COMMON, "pulse": 1.0, "pass_on": "section", "pass": "next", "accompany_beats": 8.0,
                           "lead_kinds": ["screen", "lights"]},
    "contrast": {**_COMMON, "pulse": 1.0, "palette_roles": ["base", "highlight"], "energy_split": 0.15,
                 "swap_on": "section"},
    "register_split": {**_COMMON, "pulse": 1.0, "low": "left", "split": None, "outside": 0.25},
}
#: the param a form's {beats} words and its stagger come from, with its seconds twin (no grid)
STAGGER_PARAM: dict[str, tuple[str, Optional[str]]] = {
    "echo": ("delay_beats", "delay_s"), "canon": ("offset_beats", "offset_s"),
    "ping_pong": ("scene_offset_beats", None), "solo_and_accompany": ("accompany_beats", None)}

BUILTIN_NAME = "unison"
BUILTIN: dict[str, Any] = {"label": "All at once", "kind": "unison", "line": "all at once",
                           "params": dict(FORM_PARAMS["unison"]), "routes": [], "needs": [], "boost": {},
                           "tags": {"weight": 1.0}}


# -- small helpers --------------------------------------------------------------------------------------------------------
def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _smooth(x: float) -> float:
    x = _c01(x)
    return x * x * (3.0 - 2.0 * x)


def side_of(x: float) -> str:
    return "left" if x < -SIDE_EDGE else "right" if x > SIDE_EDGE else "centre"


def register_of(pitches: Sequence[int]) -> Optional[float]:
    """The mean pitch as 0 (A0) .. 1 (C8)."""
    if not pitches:
        return None
    return round(_c01((sum(pitches) / len(pitches) - REGISTER_A0) / REGISTER_SPAN), 4)


def register_word(r: Optional[float]) -> str:
    if r is None:
        return "middle"
    return next(w for edge, w in REGISTER_WORDS if r < edge)


def _humanise(name: str) -> str:
    s = name.replace("_", " ").replace("-", " ").strip()
    return s[:1].upper() + s[1:]


def _draw(ws: Mapping[str, float], rng: Any) -> str:
    names = list(ws)
    x = rng.random() * sum(ws.values())
    for n in names:
        x -= ws[n]
        if x < 0.0:
            return n
    return names[-1]


def _beat_words(beats: float) -> str:
    if abs(beats - 0.5) < 1e-6:
        return "half a beat"
    if abs(beats - 1.0) < 1e-6:
        return "1 beat"
    return f"{beats:g} beats"


def _second_words(s: float) -> str:
    if abs(s - 0.5) < 1e-6:
        return "half a second"
    if abs(s - 1.0) < 1e-6:
        return "a second"
    return f"{s:g} seconds"


# -- settings (show.yaml ensemble) -----------------------------------------------------------------------------------------
def _check(v: Any, rule: str) -> tuple[bool, Any]:
    """rule: pos (> 0) | pos0 (>= 0) | share (0..1) | int1 (an integer >= 1) | vel (0..127) | bool."""
    if rule == "bool":
        return isinstance(v, bool), v
    n = _num(v)
    if n is None:
        return False, v
    if rule == "pos":
        return n > 0, n
    if rule == "pos0":
        return n >= 0, n
    if rule == "share":
        return 0.0 <= n <= 1.0, n
    if rule == "int1":
        return n >= 1 and n == int(n), int(n)
    if rule == "vel":
        return 0 <= n <= 127 and n == int(n), int(n)
    return False, v


SETTINGS_RULES: dict[str, dict[str, str]] = {
    "players": {"split_groups": "bool", "min_spread_m": "pos0"},
    "pick": {"dwell_bars": "pos", "dwell_s": "pos", "stay": "pos"},
    "": {"glide_beats": "pos0", "glide_s": "pos0", "no_grid_beat_s": "pos", "max_stagger_beats": "pos0"},
    "gestures": {"horizon_s": "pos", "min_gap_ms": "pos0", "max_per_bar": "int1", "min_strength": "share",
                 "max_parts": "int1", "keep": "int1", "fill_energy": "share"},
    "piano": {"burst_ms": "pos", "max_burst_ms": "pos", "chord_notes": "int1", "min_vel": "vel", "vel_curve": "pos",
              "split_notes": "int1"},
}
SECTION_KEYS: dict[str, frozenset] = {
    "": frozenset({"players", "pick", "roles", "glide_beats", "glide_s", "no_grid_beat_s", "max_stagger_beats",
                   "gestures", "piano"}),
    "players": frozenset({"zones", "split_groups", "min_spread_m"}),
    "pick": frozenset(SETTINGS_RULES["pick"]),
    "gestures": frozenset(set(SETTINGS_RULES["gestures"]) | {"strength", "colours", "shapes", "virtual_lamps"}),
    "piano": frozenset(set(SETTINGS_RULES["piano"]) | {"split", "split_range"}),
}


def _section(raw: Mapping, name: str, probs: list[str]) -> Mapping:
    v = raw.get(name)
    if v is None:
        return {}
    if not isinstance(v, Mapping):
        probs.append(f"ensemble.{name}: not a mapping: the starting points are used")
        return {}
    for k in v:
        if k not in SECTION_KEYS[name]:
            probs.append(f"ensemble.{name}.{k}: not an ensemble key: ignored")
    return v


def parse_settings(raw: Any, probs: list[str]) -> dict:
    """show.yaml ``ensemble`` -> the resolved settings (ENSEMBLE_FALLBACK's shape, plus ``on``: False without a
    block, so the room plays as today). A bad value falls back with a line in ``probs``."""
    out = copy.deepcopy(ENSEMBLE_FALLBACK)
    out["on"] = raw is not None
    if raw is None:
        return out
    if not isinstance(raw, Mapping):
        probs.append("ensemble: not a mapping: the room plays all at once")
        out["on"] = False
        return out
    for k in raw:
        if k not in SECTION_KEYS[""]:
            probs.append(f"ensemble.{k}: not an ensemble key: ignored")
    secs = {"": raw}
    for name in ("players", "pick", "gestures", "piano"):
        secs[name] = _section(raw, name, probs)
    for name, rules in SETTINGS_RULES.items():
        src, dst = secs[name], (out if name == "" else out[name])
        for key, rule in rules.items():
            if key not in src:
                continue
            ok, v = _check(src[key], rule)
            if ok:
                dst[key] = v
            else:
                probs.append(f"ensemble.{(name + '.') if name else ''}{key}: {src[key]!r} isn't {_RULE_WORDS[rule]}: "
                             f"{dst[key]!r} used")
    zones = secs["players"].get("zones")
    if zones is not None:
        if isinstance(zones, Mapping) and all(isinstance(k, str) and v in SIDE_WORDS for k, v in zones.items()):
            out["players"]["zones"] = {str(k): str(v) for k, v in zones.items()}
        else:
            probs.append(f"ensemble.players.zones: {zones!r} isn't {{zone: left | right}}: no zones (every player "
                         f"centre)")
    _parse_roles(raw.get("roles"), out, probs)
    g = secs["gestures"]
    for key in ("strength", "colours"):
        v = g.get(key)
        if v is None:
            continue
        if not isinstance(v, Mapping):
            probs.append(f"ensemble.gestures.{key}: not a mapping: the starting points are used")
            continue
        for kind, x in v.items():
            if kind not in contract.GESTURE_KINDS:
                probs.append(f"ensemble.gestures.{key}.{kind}: not a gesture ({', '.join(contract.GESTURE_KINDS)})")
            elif key == "strength" and _check(x, "share")[0]:
                out["gestures"]["strength"][kind] = float(x)
            elif key == "colours" and (x is None or x in contract.PALETTE_ROLES):
                out["gestures"]["colours"][kind] = x
            else:
                probs.append(f"ensemble.gestures.{key}.{kind}: {x!r} isn't "
                             f"{'0..1' if key == 'strength' else 'a palette role or null'}: the starting point used")
    sh = g.get("shapes")
    if sh is not None:
        if not isinstance(sh, Mapping):
            probs.append("ensemble.gestures.shapes: not a mapping: the starting points are used")
        else:
            for kind, s in sh.items():
                where = f"ensemble.gestures.shapes.{kind}"
                if kind not in contract.GESTURE_KINDS:
                    probs.append(f"{where}: not a gesture: ignored")
                    continue
                if not isinstance(s, Mapping) or set(s) != set(SHAPE_KEYS) or not all(
                        _check(s[k], "share" if k in SHAPE_SHARES else "pos0")[0] for k in SHAPE_KEYS):
                    probs.append(f"{where}: needs {', '.join(SHAPE_KEYS)} (times >= 0, the rest 0..1): the starting "
                                 f"point used")
                    continue
                out["gestures"]["shapes"][kind] = {k: float(s[k]) for k in SHAPE_KEYS}
    vl = g.get("virtual_lamps")
    if vl is not None:
        if isinstance(vl, Mapping) and set(vl) <= {"min_attack_ms", "swell_ms"} and all(
                _check(x, "pos0")[0] for x in vl.values()):
            out["gestures"]["virtual_lamps"].update({k: float(x) for k, x in vl.items()})
        else:
            probs.append(f"ensemble.gestures.virtual_lamps: {vl!r} isn't {{min_attack_ms, swell_ms}} (ms >= 0)")
    p = secs["piano"]
    if "split" in p:
        v = p["split"]
        if v == "auto" or (_check(v, "vel")[0]):
            out["piano"]["split"] = v if v == "auto" else int(v)
        else:
            probs.append(f"ensemble.piano.split: {v!r} isn't a MIDI note or auto: auto used")
    if "split_range" in p:
        v = p["split_range"]
        if isinstance(v, list) and len(v) == 2 and all(_check(x, "vel")[0] for x in v) and v[0] <= v[1]:
            out["piano"]["split_range"] = [int(v[0]), int(v[1])]
        else:
            probs.append(f"ensemble.piano.split_range: {v!r} isn't [low, high] MIDI notes: the starting point used")
    return out


_RULE_WORDS = {"pos": "a number > 0", "pos0": "a number >= 0", "share": "a number in 0..1",
               "int1": "a whole number >= 1", "vel": "a whole number in 0..127", "bool": "true or false"}


def _parse_roles(raw: Any, out: dict, probs: list[str]) -> None:
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        probs.append("ensemble.roles: not a mapping: the starting points are used")
        return
    for role, scales in raw.items():
        where = f"ensemble.roles.{role}"
        if role not in contract.ENSEMBLE_ROLES:
            probs.append(f"{where}: not a role ({', '.join(contract.ENSEMBLE_ROLES)}): ignored")
            continue
        if not isinstance(scales, Mapping):
            probs.append(f"{where}: not a mapping: the starting point used")
            continue
        for k, v in scales.items():
            if k not in contract.ROLE_SCALES:
                probs.append(f"{where}.{k}: not a scale ({', '.join(contract.ROLE_SCALES)}): ignored")
            elif _check(v, "share")[0]:
                out["roles"][role][k] = float(v)
            else:
                probs.append(f"{where}.{k}: {v!r} isn't a number in 0..1: the starting point used")


# -- the catalogue (config/live/interplays.yaml) ---------------------------------------------------------------------------
def _p_word(*words: str):
    return lambda v: (v in words, v)


def _p_num(lo: float, hi: float):
    return lambda v: ((_num(v) is not None and lo <= v <= hi), _num(v))


def _p_int(lo: int, hi: int):
    return lambda v: ((_num(v) is not None and v == int(v) and lo <= v <= hi), int(v) if _num(v) is not None else v)


def _p_bool(v: Any) -> tuple[bool, Any]:
    return isinstance(v, bool), v


def _p_kinds(v: Any) -> tuple[bool, Any]:
    ok = isinstance(v, list) and all(x in contract.PLAYER_KINDS for x in v)
    return ok, list(v) if ok else v


def _p_roles2(v: Any) -> tuple[bool, Any]:
    ok = isinstance(v, list) and len(v) == 2 and all(x in contract.PALETTE_ROLES for x in v)
    return ok, list(v) if ok else v


def _p_split(v: Any) -> tuple[bool, Any]:
    return (v is None or v == "auto" or (_num(v) is not None and v == int(v) and 0 <= v <= 127)), v


#: a param's check -> (ok, the value to use). The bounds only reject nonsense; the values are the file's.
PARAM_CHECKS: dict[str, Any] = {
    "team_by": _p_word(*TEAM_BY), "order": _p_word(*ORDERS), "pulse": _p_num(0.0, 1.0),
    "delay_beats": _p_num(0.0, 64.0), "delay_s": _p_num(0.0, 30.0), "decay": _p_num(0.0, 1.0),
    "repeats": _p_int(1, 16), "every_bars": _p_int(1, 64), "caller": _p_word("first", "register"),
    "answer": _p_word("replay", "own"), "half_s": _p_num(0.1, 60.0), "on": _p_word("beat", "half_bar", "bar"),
    "downbeat_boost": _p_num(0.0, 4.0), "scene_offset_beats": _p_num(0.0, 64.0), "offset_beats": _p_num(0.0, 64.0),
    "offset_s": _p_num(0.0, 30.0), "clock_offset": _p_bool, "pass_on": _p_word("section", "phrase", "look"),
    "pass": _p_word("next", "other"), "accompany_beats": _p_num(0.0, 64.0), "lead_kinds": _p_kinds,
    "palette_roles": _p_roles2, "energy_split": _p_num(0.0, 1.0), "swap_on": _p_word("section", "look"),
    "low": _p_word(*SIDE_WORDS), "split": _p_split, "outside": _p_num(0.0, 1.0)}


class Catalogue:
    """config/live/interplays.yaml (module docstring). ``poll`` on show-brain; ``entries``, ``revision`` and
    ``weights`` from any thread (swapped by reference). ``scorer``: the light scenes' catalogue whose tags rule and
    score are used (default light_scenes.shared())."""

    def __init__(self, path: pathlib.Path = contract.INTERPLAYS_FILE, *, scorer: Any = None) -> None:
        self.path = pathlib.Path(path)
        self._scorer = scorer
        self.entries: dict[str, dict] = {}
        self.revision = ""
        self.error: Optional[str] = None
        self.problems: list[str] = []
        self._sig: Any = ()                        # () = never polled; None = not there
        self._vsig: Any = ()

    @property
    def scorer(self) -> Any:
        return self._scorer if self._scorer is not None else lscenes.shared()

    @staticmethod
    def _stat(p: pathlib.Path) -> Optional[tuple]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def poll(self, force: bool = False) -> bool:
        """Re-read the file when it (or the vocab) changed. True when the entries changed."""
        sig = self._stat(self.path)
        vsig = self._stat(pathlib.Path(getattr(self.scorer, "vocab_path", contract.VOCAB_FILE)))
        if not force and sig == self._sig and vsig == self._vsig:
            return False
        self._sig, self._vsig = sig, vsig
        if sig is None:
            self.error, self.problems = f"{self.path.name}: not there (the room plays all at once)", []
            return self._swap({})
        try:
            raw = lscenes.load_yaml(self.path.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError("the top level isn't a mapping")
            entries, probs = self._parse(raw)
        except Exception as e:                       # keep the last good entries
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("interplays: %s (keeping the last good ones)", self.error)
            return False
        self.error, self.problems = None, probs
        return self._swap(entries)

    def _swap(self, entries: dict) -> bool:
        rev = hashlib.sha1(json.dumps(entries, sort_keys=True, default=str).encode()).hexdigest()[:12] \
            if entries else ""
        if rev == self.revision:
            return False
        self.entries, self.revision = entries, rev
        log.info("interplays: %d forms (revision %s)", len(entries), rev or "none")
        return True

    def _parse(self, raw: dict) -> tuple[dict, list[str]]:
        probs: list[str] = []
        for k in raw:
            if k not in TOP_KEYS:
                probs.append(f"{k}: not a key {self.path.name} has: ignored")
        sec = raw.get("interplays")
        if sec is None:
            return {}, probs
        if not isinstance(sec, dict):
            raise ValueError("interplays isn't a mapping")
        sc = self.scorer
        tags = None
        vocab: dict = {}
        try:
            tags = sc._tags.get()                    # the light scenes' own tags rule, never a second copy
            vocab = sc._vocab(tags) if tags is not None else {}
        except Exception:                            # a scorer without tags: the tags load unchecked
            log.debug("interplays: the tags rule isn't there", exc_info=True)
        out: dict[str, dict] = {}
        for name, e in sec.items():
            where = f"interplays.{name}"
            if not isinstance(name, str) or not lscenes.NAME_RE.match(name):
                probs.append(f"{where}: a name is lower case letters, digits, - and _: left out")
                continue
            if not isinstance(e, dict):
                probs.append(f"{where}: not a mapping: left out")
                continue
            got = self._parse_entry(name, e, where, probs, tags, vocab)
            if got is not None:
                out[name] = got
        return out, probs

    @staticmethod
    def _parse_entry(name: str, e: dict, where: str, probs: list[str], tags: Any, vocab: dict) -> Optional[dict]:
        for k in e:
            if k not in ENTRY_KEYS:
                probs.append(f"{where}.{k}: not a form key ({', '.join(sorted(ENTRY_KEYS))}): ignored")
        kind = e.get("kind")
        if kind not in contract.FORM_KINDS:
            probs.append(f"{where}.kind: {kind!r} isn't one of {', '.join(contract.FORM_KINDS)}: left out")
            return None
        label = e.get("label") if isinstance(e.get("label"), str) and e.get("label") else _humanise(name)
        line = e.get("line") if isinstance(e.get("line"), str) and e.get("line") else label.lower()
        try:
            fields = {f for _, f, _, _ in string.Formatter().parse(line) if f is not None}
        except ValueError:
            fields = {"?"}
        bad = sorted(f for f in fields if f not in LINE_FILLERS)
        if bad:
            probs.append(f"{where}.line: {{{', '.join(bad)}}} isn't a filler ({', '.join(LINE_FILLERS)}): the label "
                         f"is used")
            line = label.lower()
        params = dict(FORM_PARAMS[kind])
        raw_p = e.get("params")
        if raw_p is not None and not isinstance(raw_p, dict):
            probs.append(f"{where}.params: not a mapping: left out")
            return None
        for k, v in (raw_p or {}).items():
            if k not in params:
                probs.append(f"{where}.params.{k}: not a {kind} param ({', '.join(sorted(params))}): left out")
                return None
            ok, val = PARAM_CHECKS[k](v)
            if not ok:
                probs.append(f"{where}.params.{k}: {v!r} can't be used: left out")
                return None
            params[k] = val
        routes = e.get("routes", [])
        if not isinstance(routes, list) or not all(r in contract.GESTURE_KINDS for r in routes):
            probs.append(f"{where}.routes: not a list of {', '.join(contract.GESTURE_KINDS)}: left out")
            return None
        needs = e.get("needs", [])
        if not isinstance(needs, list) or not all(n in NEEDS for n in needs):
            probs.append(f"{where}.needs: not a list of {', '.join(NEEDS)}: left out")
            return None
        boost = e.get("boost", {})
        if not isinstance(boost, dict) or not all(k in NEEDS and _num(v) is not None and v > 0
                                                  for k, v in boost.items()):
            probs.append(f"{where}.boost: not {{{' | '.join(NEEDS)}: a number > 0}}: left out")
            return None
        t = lscenes.Catalogue._tags_of(e.get("tags"), tags, vocab, where, probs)
        if t is None:
            return None
        return {"label": label, "kind": kind, "line": line, "params": params, "routes": list(dict.fromkeys(routes)),
                "needs": list(dict.fromkeys(needs)), "boost": {k: float(v) for k, v in boost.items()}, "tags": t}

    def weights(self, direction: Optional[Mapping], flow: Optional[Mapping], facts: Mapping[str, bool]
                ) -> dict[str, float]:
        """{name: weight} over the eligible entries (every ``needs`` word true in ``facts``): the scenes' score
        (tags.py's scene_weight x flow_fit) x each ``boost`` whose need holds."""
        out: dict[str, float] = {}
        sc = self.scorer
        for n, e in self.entries.items():
            if not all(facts.get(x) for x in e["needs"]):
                continue
            try:
                w = float(sc.score({"tags": e["tags"]}, None, direction, flow))
            except Exception:                        # the scorer's own bug: the tag weight
                log.debug("interplays: scoring failed", exc_info=True)
                w = float(e["tags"].get("weight", 1.0))
            for need, f in e["boost"].items():
                if facts.get(need):
                    w *= f
            out[n] = max(1e-6, w)
        return out

    def why(self, name: str, direction: Optional[Mapping]) -> str:
        """The pick's tag words: the energy inside the form's band, the moods both name, the genre it fits well."""
        e = self.entries.get(name)
        if e is None:
            return "nothing else fits"
        t, d = e["tags"], dict(direction or {})
        words: list[str] = []
        en = _num(d.get("energy"))
        band = t.get("energy")
        if en is not None and isinstance(band, (list, tuple)) and len(band) == 2 and band[0] <= en <= band[1]:
            words.append(f"energy {en:.1f}")
        have = set(t.get("moods") or [])
        words += [m for m in d.get("moods") or [] if isinstance(m, str) and m in have][:lscenes.WHY_MOODS]
        g = lscenes._genre_word(d)
        fit = (t.get("genre_fit") or {}).get(g) if g else None
        if fit is not None and fit >= lscenes.WHY_GENRE_FIT:
            words.append(g)
        return ", ".join(words) if words else "a change"

    def view(self) -> dict:
        return {"path": str(self.path), "revision": self.revision, "error": self.error,
                "problems": list(self.problems), "forms": {n: e["label"] for n, e in self.entries.items()}}


# -- players (§2) ------------------------------------------------------------------------------------------------------------
def _kind(rig: Mapping, f: Mapping) -> Any:
    t = (rig.get("types") or {}).get(f.get("type"))
    return (t.get("kind") if isinstance(t, Mapping) else None) or f.get("kind")


def _fixture_x(rig: Mapping, fids: Sequence[str], pc: Mapping, space_mod: Any, view: Any
               ) -> tuple[dict[str, tuple[float, str]], Optional[float]]:
    """fixture id -> (x, via) by §2.2's rules, and the space's scale (max |lateral|, metres) when the space rule is
    in use (None: zones and screens decide)."""
    fx = rig.get("fixtures") or {}
    lat: dict[str, float] = {}
    if space_mod is not None and isinstance(view, Mapping):
        for fid in fids:
            try:
                p = space_mod.position(view, fid)
                if p is not None:
                    lat[fid] = float(space_mod.lateral(view, p))
            except Exception:                        # the space's own bug: no positions
                log.debug("ensemble: space position failed for %s", fid, exc_info=True)
    scale = None
    if lat and max(lat.values()) - min(lat.values()) >= float(pc.get("min_spread_m", 0.0)) and \
            max(abs(v) for v in lat.values()) > 1e-9:
        scale = max(abs(v) for v in lat.values())
    zones = pc.get("zones") or {}
    out: dict[str, tuple[float, str]] = {}
    for fid in fids:
        f = fx.get(fid) if isinstance(fx.get(fid), Mapping) else {}
        if scale is not None and fid in lat:
            out[fid] = (max(-1.0, min(1.0, lat[fid] / scale)), "space")
        elif zones.get(f.get("zone")) in SIDE_WORDS:
            out[fid] = (-1.0 if zones[f["zone"]] == "left" else 1.0, "zone")
    screen_x = {fx[fid].get("screen"): out[fid] for fid in out
                if isinstance(fx.get(fid), Mapping) and _kind(rig, fx[fid]) == "screen" and fx[fid].get("screen")}
    for fid in fids:
        if fid in out:
            continue
        f = fx.get(fid) if isinstance(fx.get(fid), Mapping) else {}
        sx = screen_x.get(f.get("screen")) if f.get("screen") else None
        out[fid] = (sx[0], "screen") if sx is not None else (0.0, "none")
    return out, scale


def players(rig: Any, groups: Any, *, cfg: Mapping, space_mod: Any = None, view: Any = None,
            live_screens: Optional[Sequence[str]] = None, devices: Sequence[Mapping] = ()
            ) -> list[contract.PlayerInfo]:
    """Every player of the room (§2.1): each screen fixture, each light group's members per side, each device
    (``{"id": <room subsystem>, "label"?, "at"?: [x, y, z]}``). Pure; ``cfg`` is the resolved ensemble settings (or its
    ``players`` block). ``live_screens``: the renderer screen names showing live (None: every screen is on)."""
    pc = cfg.get("players", cfg) if isinstance(cfg, Mapping) else {}
    rig = rig if isinstance(rig, Mapping) else {}
    fx = rig.get("fixtures") if isinstance(rig.get("fixtures"), Mapping) else {}
    groups = groups if isinstance(groups, Mapping) else {}
    screen_ids = [fid for fid, f in fx.items() if isinstance(f, Mapping) and _kind(rig, f) == "screen"]
    lamp_ids: list[str] = []
    for g in groups.values():
        lamp_ids += [m for m in (g.get("members") or []) if m not in lamp_ids]
    xs, scale = _fixture_x(rig, screen_ids + lamp_ids, pc, space_mod, view)
    live = set(live_screens) if live_screens is not None else None
    out: list[dict] = []
    for fid in screen_ids:
        f = fx[fid]
        x, via = xs[fid]
        scr = f.get("screen") if isinstance(f.get("screen"), str) else None
        out.append({"id": fid, "kind": "screen", "label": str(f.get("label") or fid), "side": side_of(x),
                    "x": round(x, 4), "order": 0, "via": via, "members": [fid], "group": None, "screen": scr,
                    "on": live is None or scr in live})
    for gid, g in groups.items():
        mem = [m for m in (g.get("members") or []) if m in xs]
        if not mem:
            continue
        label = str(g.get("label") or gid)
        buckets: dict[str, list[str]] = {"left": [], "right": [], "all": []}
        for m in mem:
            s = side_of(xs[m][0]) if pc.get("split_groups", True) else "all"
            buckets[s if s in SIDE_WORDS else "all"].append(m)
        for key, ms in buckets.items():
            if not ms:
                continue
            x = sum(xs[m][0] for m in ms) / len(ms)
            via = min((xs[m][1] for m in ms), key=lambda v: VIA_RANK[v])
            out.append({"id": f"{gid}:{key}", "kind": "lights", "label": f"{label}, {key}" if key != "all" else label,
                        "side": side_of(x), "x": round(x, 4), "order": 0, "via": via, "members": list(ms),
                        "group": str(gid), "screen": None, "on": True})
    for d in devices or ():
        if not isinstance(d, Mapping) or not isinstance(d.get("id"), str):
            continue
        x, via = 0.0, "none"
        at = d.get("at")
        if scale is not None and at is not None and space_mod is not None:
            try:
                x, via = max(-1.0, min(1.0, float(space_mod.lateral(view, at)) / scale)), "space"
            except Exception:
                x, via = 0.0, "none"
        label = d.get("label") if isinstance(d.get("label"), str) and d.get("label") else _humanise(d["id"])
        out.append({"id": f"device:{d['id']}", "kind": "device", "label": label, "side": side_of(x),
                    "x": round(x, 4), "order": 0, "via": via, "members": [], "group": None, "screen": None,
                    "on": True})
    out.sort(key=lambda p: (p["x"], p["id"]))
    for i, p in enumerate(out):
        p["order"] = i
    return out                                        # type: ignore[return-value]


def teams(players: Sequence[Mapping], team_by: str, order: Sequence[str]) -> list[list[str]]:
    """The teams a form plays with (§2.3): ``side`` -> left and right (on players; centre and devices left out),
    ``player`` -> one per player; members and teams in the travel ``order``."""
    pos = {pid: i for i, pid in enumerate(order)}
    on = [p for p in players if p.get("on") and p.get("side") in SIDE_WORDS and p.get("kind") != "device"]
    if team_by == "player":
        ts = [[p["id"]] for p in on]
    else:
        by: dict[str, list[str]] = {}
        for p in on:
            by.setdefault(p["side"], []).append(p["id"])
        ts = [by[s] for s in SIDE_WORDS if s in by]
    for t in ts:
        t.sort(key=lambda i: (pos.get(i, 1 << 20), i))
    ts.sort(key=lambda t: pos.get(t[0], 1 << 20))
    return ts


def flow_order(players: Sequence[Mapping], flow: Optional[Mapping], space_mod: Any = None, view: Any = None
               ) -> list[str]:
    """The order a gesture travels by the room's flow (§2.3): with the room placed, ascending flow_coord of each
    player's centroid; without, right and cw go left to right, left and ccw right to left, any other dir the room's
    order. Centre players and devices are left out."""
    cand = sorted((p for p in players if p.get("side") in SIDE_WORDS and p.get("kind") != "device"),
                  key=lambda p: (p["x"], p["id"]))
    room = [p["id"] for p in cand]
    d = flow.get("dir") if isinstance(flow, Mapping) else None
    if not d:
        return room
    if space_mod is not None and isinstance(view, Mapping) and cand:
        coords: dict[str, float] = {}
        try:
            for p in cand:
                pts = [q for q in (space_mod.position(view, m) for m in p["members"]) if q is not None]
                if not pts:
                    break
                c = [sum(q[i] for q in pts) / len(pts) for i in range(3)]
                coords[p["id"]] = float(space_mod.flow_coord(view, d, flow.get("at_xyz"), c))
        except Exception:                            # the space's own bug: the words below
            log.debug("ensemble: flow_coord failed", exc_info=True)
            coords = {}
        if len(coords) == len(cand):
            return [p["id"] for p in sorted(cand, key=lambda p: (coords[p["id"]], p["x"], p["id"]))]
    if d in ("left", "ccw"):
        return room[::-1]
    return room


# -- moments and forms --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Seed:
    """A moment before the form routes it."""
    key: str
    kind: str
    source: str
    at_s: float
    predicted: bool
    strength: float
    dur_s: float = 0.0
    register: Optional[float] = None
    pitches: tuple[int, ...] = ()
    why: str = ""
    beat: Optional[float] = None
    bar: Optional[int] = None
    vels: tuple[int, ...] = ()                       # note | chord: each pitch's loudest velocity (register_split)


@dataclass
class _Form:
    """A form playing (or about to): its entry and its own state."""
    name: str
    entry: dict
    since_s: float                                   # when it took effect
    source: str                                      # "pick" | "piano" | "default"
    why: str
    lead: int = 0                                    # solo_and_accompany: the lead team's place
    swap: bool = False                               # contrast: the palette roles swapped
    caller0: int = 0                                 # call_and_response: the calling team at phrase0
    phrase0: Optional[int] = None                    # ... the clock's phrase number then (None: set when the grid is)
    new: bool = False                                # a form change (id + 1 when it lands)


class Ensemble:
    """The room's band (module docstring). The director owns one; every method on show-brain except the readers."""

    def __init__(self, cfg: Optional[dict] = None, catalogue: Optional[Catalogue] = None, *,
                 rng: Optional[random.Random] = None) -> None:
        self.cfg: dict = cfg if cfg is not None else parse_settings(None, [])
        self.catalogue = catalogue if catalogue is not None else Catalogue()
        self.problems: list[str] = []
        self._rng = rng if rng is not None else random.Random(0)
        self._players: list[dict] = []
        self._pmap: dict[str, dict] = {}
        self._players_key: Any = None
        self._prev_n = 0                                 # +1 per players change (the layout cache key)
        self._space: tuple[Any, Any] = (None, None)
        self.form = _Form(BUILTIN_NAME, BUILTIN, 0.0, "default", "today's room")
        self._at_s = 0.0                                 # when the current form (or its last moment) took effect
        self._next: Optional[dict] = None                # {"at_s", "form", "why", "look_id"}
        self._prev: Optional[tuple[float, dict]] = None  # (fold time, the players before it): the roles' glide
        self._id = 0
        self._rev = 0
        self._seq = 0
        self._active = False
        self._held = False
        self._piano = False
        self._direction: Optional[Mapping] = None
        self._flow: Optional[Mapping] = None
        self._grid: Any = None
        self._lead = 1.1
        self._now = 0.0
        self._facts: dict[str, bool] = {n: False for n in NEEDS}
        self._seeds: dict[str, Seed] = {}                # queued, by key (a re-queue replaces)
        self._seen: dict[str, float] = {}                # clock line keys -> their at (each line once)
        self._armed: dict[str, float] = {}               # hit:<drop_armed id> -> its line
        self._burst: Optional[dict] = None               # a piano burst being gathered
        self._hist: collections.deque = collections.deque(maxlen=int(self.cfg["piano"]["split_notes"]))
        self._split_cache: Optional[int] = None
        self._piano_n = 0
        self._turn = 0                                   # ping_pong: the next team for a seed that isn't the clock's
        self._sched: dict[str, list[tuple[float, str, Any]]] = {}   # player -> [(at, gesture id, bar key)]
        self._recent: collections.OrderedDict = collections.OrderedDict()   # gesture id -> GestureData (latest)
        self._recent_list: list = []
        self._recent_dirty = False
        self._ends: dict[str, float] = {}                # gesture id -> when it is over (8 decays past its last part)
        self._lamp_parts: dict[str, list] = {}           # gesture id -> [(part end, lamp_parts entry)] for lights
        self._out: list = []                             # call-offs a reset queued (returned by the next tick)
        self._lamps: tuple = ()
        self._block: dict = {}
        self._member_of: dict[str, str] = {}
        self._key: Any = None
        self._gkey: Any = None
        self._gkey_t: Optional[float] = None
        self._layout_cache: dict = {}
        self._weights: dict[str, float] = {}
        self._log: list[dict] = []
        self.counts: dict[str, Any] = {"gestures": 0, "parts": 0, "by_kind": {}, "spaced": 0, "over_bar": 0,
                                       "weak": 0, "trimmed": 0}
        self._last: collections.deque = collections.deque(maxlen=LAST_N)
        self._rebuild(0.0, force=True)

    # -- settings and players ---------------------------------------------------------------------------------------------
    @property
    def on(self) -> bool:
        return bool(self.cfg.get("on"))

    def configure(self, cfg: dict, problems: Sequence[str] = ()) -> None:
        """parse_settings' output (hot-reloaded with show.yaml). Turning off goes back to today's room."""
        was = self.on
        self.cfg = cfg
        self.problems = list(problems)
        n = int(cfg["piano"]["split_notes"])
        if self._hist.maxlen != n:
            self._hist = collections.deque(self._hist, maxlen=n)
        self._split_cache = None
        self._players_key = None                        # the zones may have moved
        if was and not self.on:
            self.reset(self._now, "the ensemble is switched off in show.yaml")
        self._rebuild(self._now, force=True)

    def set_players(self, players: list[contract.PlayerInfo], now_s: float, *, space_mod: Any = None,
                    view: Any = None) -> bool:
        """The room's players now (``players``); True when they changed (rev + 1)."""
        self._space = (space_mod, view)
        key = json.dumps(players, sort_keys=True, default=str)
        if key == self._players_key:
            return False
        self._players_key = key
        self._players = [dict(p) for p in players]
        self._pmap = {p["id"]: p for p in self._players}
        self._prev_n += 1
        self._layout_cache.clear()
        self._rebuild(now_s, force=True)
        return True

    def _layout(self, form: _Form) -> tuple[list[str], list[list[str]]]:
        """(travel order, teams) for a form: its team_by and order, the flow's dir."""
        P = form.entry["params"]
        d = self._flow.get("dir") if isinstance(self._flow, Mapping) else None
        key = (self._prev_n, P.get("team_by"), P.get("order"), d)
        got = self._layout_cache.get(key)
        if got is None:
            if P.get("order") == "flow":
                order = flow_order(self._players, self._flow, *self._space)
            else:
                order = flow_order(self._players, None)
            got = (order, teams(self._players, P.get("team_by", "side"), order))
            if len(self._layout_cache) > 32:
                self._layout_cache.clear()
            self._layout_cache[key] = got
        return got

    def _sides(self) -> bool:
        return len(teams(self._players, "side", flow_order(self._players, None))) >= 2

    # -- the grid --------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _ok(grid: Any) -> bool:
        return grid is not None and bool(getattr(grid, "ok", False)) and bool(getattr(grid, "beat_s", None))

    def _beat_s(self, grid: Any) -> float:
        return float(grid.beat_s) if self._ok(grid) else float(self.cfg["no_grid_beat_s"])

    def _phrase_beats(self, grid: Any) -> int:
        if self._ok(grid):
            return int(grid.bpb) * int(grid.pb)
        return 32

    def _bar_key(self, at: float, grid: Any) -> Any:
        return ("b", grid.bar_at(at + 1e-6)) if self._ok(grid) else ("s", math.floor(at / NO_GRID_BAR_S))

    # -- forms: roles, staggers, parts ---------------------------------------------------------------------------------------
    def _cr_state(self, form: _Form, t: float, grid: Any, n: int) -> tuple[int, int]:
        """call_and_response at ``t``: (the calling team, the half: 0 the call, 1 the answer)."""
        P = form.entry["params"]
        if n < 1:
            return 0, 0
        if self._ok(grid):
            span = self._phrase_beats(grid)
            pos = grid.beat_at(t) - grid.bar_anchor
            ph = math.floor(pos / span + 1e-9)
            half = 0 if pos - ph * span < span / 2.0 - 1e-6 else 1
            if form.phrase0 is None:
                form.phrase0 = ph
            rel = ph - form.phrase0
        else:
            hb = math.floor((t - form.since_s) / float(P["half_s"]) + 1e-9)
            rel, half = hb // 2, hb % 2
        return (form.caller0 + rel) % n, half

    def _half_s(self, form: _Form, grid: Any) -> float:
        if self._ok(grid):
            return self._phrase_beats(grid) / 2.0 * float(grid.beat_s)
        return float(form.entry["params"]["half_s"])

    def _lead_team(self, form: _Form, t: float, grid: Any, n: int) -> Optional[int]:
        kind = form.entry["kind"]
        if n < 2:
            return None
        if kind == "solo_and_accompany":
            return form.lead % n
        if kind == "call_and_response":
            caller, half = self._cr_state(form, t, grid, n)
            return caller if half == 0 else (caller + 1) % n
        return None

    def _split(self, form: _Form) -> int:
        """register_split's line: the form's ``split``, else piano.split; auto: the median of the last notes."""
        v = form.entry["params"].get("split")
        if v is None:
            v = self.cfg["piano"]["split"]
        if v != "auto":
            return int(v)
        if self._split_cache is None:
            lo, hi = self.cfg["piano"]["split_range"]
            if self._hist:
                s = sorted(self._hist)
                med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0
            else:
                med = (lo + hi) / 2.0
            self._split_cache = int(round(max(lo, min(hi, med))))
        return self._split_cache

    def _stagger(self, form: _Form, k: int, t: float, grid: Any, n: int) -> tuple[float, float]:
        """(beats, seconds) a scene change lands after the room's moment on team ``k`` (-1: centre)."""
        if k < 0 or n < 2:
            return 0.0, 0.0
        kind, P = form.entry["kind"], form.entry["params"]
        beats, secs = 0.0, None
        if kind in STAGGER_PARAM:
            bp, sp = STAGGER_PARAM[kind]
            if kind == "solo_and_accompany":
                beats = 0.0 if k == form.lead % n else float(P[bp])
            else:
                beats = k * float(P[bp])
                secs = k * float(P[sp]) if sp else None
        elif kind == "call_and_response":
            caller, _ = self._cr_state(form, t, grid, n)
            if k != caller:
                beats = self._phrase_beats(grid) / 2.0
                secs = float(P["half_s"])
        cap_b = float(self.cfg["max_stagger_beats"])
        beats = min(beats, cap_b)
        if self._ok(grid):
            return round(beats, 4), round(beats * float(grid.beat_s), 4)
        nb = float(self.cfg["no_grid_beat_s"])
        s = secs if secs is not None else beats * nb
        return round(beats, 4), round(min(s, cap_b * nb), 4)

    def _parts_at(self, form: _Form, t: float, grid: Any) -> dict[str, contract.EnsemblePart]:
        """Every player's EnsemblePart under ``form`` at ``t``."""
        kind, P = form.entry["kind"], form.entry["params"]
        _, ts = self._layout(form)
        n = len(ts)
        team_of = {pid: k for k, tm in enumerate(ts) for pid in tm} if n >= 2 else {}
        lead = self._lead_team(form, t, grid, n)
        roles = self.cfg["roles"]
        s_reg = _c01((self._split(form) - REGISTER_A0) / REGISTER_SPAN) if kind == "register_split" else None
        out: dict[str, contract.EnsemblePart] = {}
        for p in self._players:
            k = team_of.get(p["id"], -1)
            role = "even"
            if kind in ("solo_and_accompany", "call_and_response") and lead is not None:
                if k >= 0:
                    role = "lead" if k == lead and (kind != "solo_and_accompany" or p["kind"] in P["lead_kinds"]) \
                        else "accompany"
                elif kind == "solo_and_accompany":
                    role = "lead" if p["kind"] == "device" and "device" in P["lead_kinds"] else "accompany"
            scale = dict(roles[role])
            scale["pulse"] = round(scale["pulse"] * float(P.get("pulse", 1.0)), 4)
            sb, ss = self._stagger(form, k, t, grid, n)
            pr, bias, reg, off = None, 0.0, None, 0.0
            if kind == "contrast" and k >= 0:
                pr = P["palette_roles"][(k + int(form.swap)) % 2]
                if p["kind"] == "screen":
                    bias = round((-0.5 if (k + int(form.swap)) % 2 == 0 else 0.5) * float(P["energy_split"]), 4)
            if kind == "register_split" and p["side"] in SIDE_WORDS and n >= 2 and s_reg is not None:
                reg = [0.0, round(s_reg, 4)] if p["side"] == P["low"] else [round(s_reg, 4), 1.0]
            if kind == "canon" and P["clock_offset"] and k > 0:
                off = round(k * float(P["offset_beats"]), 4)
            elif kind == "echo" and P.get("clock_offset") and k > 0:     # ff, 22 Oct: the same picture, a step behind
                off = round(k * float(P["delay_beats"]), 4)
            out[p["id"]] = {"role": role, "scale": scale, "stagger_beats": sb, "stagger_s": ss, "palette_role": pr,
                            "energy_bias": bias, "register": reg, "clock_offset_beats": off, "team": k,
                            "kind": p["kind"], "side": p["side"], "x": p["x"], "label": p["label"],
                            "screen": p["screen"], "members": list(p["members"])}
        return out

    # -- the line (§9) -------------------------------------------------------------------------------------------------------
    def _team_words(self, tm: Sequence[str], team_by: str) -> tuple[str, str]:
        """(side word, lead words) for a team: "left", "the left side"; one player's team: its label."""
        ps = [self._pmap[i] for i in tm if i in self._pmap]
        side = ps[0]["side"] if ps else "one side"
        if team_by == "player" and ps:
            return side, f"the {ps[0]['label']}"
        return side, f"the {side} side"

    def _line(self, form: _Form, t: float, grid: Any) -> str:
        e, P = form.entry, form.entry["params"]
        order, ts = self._layout(form)
        n = len(ts)
        if e["kind"] == "call_and_response" and n >= 2:
            caller, _ = self._cr_state(form, t, grid, n)
            ts = ts[caller:] + ts[:caller]
        sides = [self._team_words(tm, P.get("team_by", "side"))[0] for tm in ts]
        first = sides[0] if sides else "one side"
        second = sides[1] if len(sides) > 1 else "the other"
        d = self._flow.get("dir") if isinstance(self._flow, Mapping) else None
        if P.get("team_by") == "player" and d in ("cw", "ccw"):
            order_w = "round the room"
        elif len(sides) >= 2:
            order_w = f"{first} to {sides[-1]}"
        else:
            order_w = "across the room"
        lead_w = "one side"
        if e["kind"] == "solo_and_accompany" and n >= 2:
            lead_w = self._team_words(ts[form.lead % n], P.get("team_by", "side"))[1]
        beats_w = ""
        if e["kind"] in STAGGER_PARAM:
            bp, sp = STAGGER_PARAM[e["kind"]]
            beats_w = _beat_words(float(P[bp])) if (self._ok(grid) or sp is None) else _second_words(float(P[sp]))
        low = P.get("low", "left")
        fill = {"order": order_w, "first": first, "second": second, "lead": lead_w, "beats": beats_w, "low": low,
                "high": SIDE_WORDS[1 - SIDE_WORDS.index(low)] if low in SIDE_WORDS else "right"}
        try:
            return LINE_PREFIX + e["line"].format_map(fill)
        except Exception:                            # a template the parser let through: the label
            return LINE_PREFIX + e["label"].lower()

    # -- the block (EnsembleData) ----------------------------------------------------------------------------------------------
    def _glide_ms(self, grid: Any) -> float:
        if self._ok(grid):
            return round(float(self.cfg["glide_beats"]) * float(grid.beat_s) * 1000.0, 1)
        return round(float(self.cfg["glide_s"]) * 1000.0, 1)

    def _rebuild(self, now_s: float, *, force: bool = False) -> bool:
        """Rebuild EnsembleData when anything a reader sees moved (rev + 1). Swapped by reference."""
        grid = self._grid
        f = self.form
        n = len(self._layout(f)[1])
        tkey = self._cr_state(f, now_s, grid, n) if f.entry["kind"] == "call_and_response" else None
        gkey = (self._ok(grid), round(float(grid.beat_s), 2) if self._ok(grid) else None,
                getattr(grid, "bpb", None), getattr(grid, "pb", None))
        if gkey != self._gkey and (force or self._gkey_t is None or now_s - self._gkey_t >= GRID_SETTLE_S):
            self._gkey, self._gkey_t = gkey, now_s
        nx = self._next
        key = (self._active, id(f), f.lead, f.swap, f.caller0, f.phrase0, self._at_s, self._prev_n, tkey, self._gkey,
               (id(nx["form"]), nx["at_s"]) if nx else None, self._held, self._split(f) if f.entry["kind"] ==
               "register_split" or (nx and nx["form"].entry["kind"] == "register_split") else None,
               self._flow.get("dir") if isinstance(self._flow, Mapping) else None)
        if not force and key == self._key:
            return False
        self._key = key
        self._rev += 1
        active = self._active and self.on
        f_show = f if active else _Form(BUILTIN_NAME, BUILTIN, self._at_s, "neutral",
                                        "today's room" if self._active else "no colours, House lights or Lights low")
        players = self._parts_at(f_show, now_s, grid)
        order, _ = self._layout(f_show)
        nxt = None
        if nx is not None and active:
            nf = nx["form"]
            nxt = {"at_ms": round(nx["at_s"] * 1000.0, 1), "form": nf.name, "kind": nf.entry["kind"],
                   "label": nf.entry["label"], "line": self._line(nf, nx["at_s"], grid),
                   "players": self._parts_at(nf, nx["at_s"], grid), "why": nx["why"]}
        if not self._active:
            source = "neutral"
        elif not self.on:
            source = "default"
        elif self._held:
            source = "held"
        else:
            source = f.source
        b: contract.EnsembleData = {
            "id": self._id, "rev": self._rev, "form": f_show.name, "kind": f_show.entry["kind"],
            "label": f_show.entry["label"], "line": self._line(f_show, now_s, grid) if active else None,
            "params": dict(f_show.entry["params"]), "order": list(order), "players": players,
            "screens": {p["screen"]: p["id"] for p in self._players if p["kind"] == "screen" and p["screen"]},
            "since_ms": round(f.since_s * 1000.0, 1), "at_ms": round(self._at_s * 1000.0, 1),
            "glide_ms": self._glide_ms(grid), "next": nxt, "source": source, "why": f_show.why}
        mo: dict[str, str] = {}
        for p in self._players:
            for m in p["members"]:
                mo[m] = p["id"]
        self._block, self._member_of = b, mo
        return True

    def block(self) -> contract.EnsembleData:
        """room.look.ensemble and room.direction.ensemble (swapped by reference each tick it changes)."""
        return self._block

    # -- readers (any thread) ---------------------------------------------------------------------------------------------
    def part_of(self, who: str, t_s: float) -> Optional[contract.EnsemblePart]:
        """A player's part at ``t_s`` (a player id, or one of its member fixture ids): next's once t >= its at."""
        b, mo = self._block, self._member_of
        pid = mo.get(who, who)
        nx = b.get("next")
        if nx is not None and t_s >= nx["at_ms"] / 1000.0 and pid in nx["players"]:
            return nx["players"][pid]
        return (b.get("players") or {}).get(pid)

    def scale(self, who: str, key: str, t_s: float) -> float:
        """A part's ``scale[key]`` at ``t_s``, glided over glide_ms from a change's moment; 1.0 without a part."""
        b, mo, prev = self._block, self._member_of, self._prev
        pid = mo.get(who, who)
        cur = (b.get("players") or {}).get(pid)
        v = float(cur["scale"].get(key, 1.0)) if cur else 1.0
        gl = max(1e-3, float(b.get("glide_ms") or 0.0) / 1000.0)
        nx = b.get("next")
        if nx is not None and t_s >= nx["at_ms"] / 1000.0 and pid in nx["players"]:
            v1 = float(nx["players"][pid]["scale"].get(key, 1.0))
            return v + (v1 - v) * _smooth((t_s - nx["at_ms"] / 1000.0) / gl)
        if prev is not None and t_s < prev[0] + gl and pid in prev[1]:
            v0 = float(prev[1][pid]["scale"].get(key, 1.0))
            return v0 + (v - v0) * _smooth((t_s - prev[0]) / gl)
        return v

    def lamp_parts(self) -> tuple:
        """(at_s, strength, shape, predicted, colour role, dur_s, frozenset(member ids)) for the lights players'
        parts still showing or to come (built each tick, swapped by reference)."""
        return self._lamps

    def recent(self, now_s: float) -> list[contract.GestureData]:
        """LookData.gestures: the recent gestures, newest last (<= gestures.keep), each id's latest whole message."""
        return self._recent_list

    # -- inputs (show-brain) -----------------------------------------------------------------------------------------------
    def _queue(self, s: Seed) -> None:
        if len(self._seeds) >= MAX_SEEDS and s.key not in self._seeds:
            self._seeds.pop(next(iter(self._seeds)))
        self._seeds[s.key] = s

    def _strength(self, kind: str, x: float) -> float:
        return _c01(x) * float(self.cfg["gestures"]["strength"].get(kind, 1.0))

    def _form_at(self, t: float) -> _Form:
        nx = self._next
        return nx["form"] if nx is not None and t >= nx["at_s"] - 1e-6 else self.form

    def _set_next(self, at_s: float, form: _Form, why: str, look_id: Optional[str] = None) -> None:
        self._next = {"at_s": at_s, "form": form, "why": why, "look_id": look_id}
        if at_s <= self._now + 1e-6:
            self._fold(self._now)

    def _fold(self, now_s: float) -> None:
        nx = self._next
        if nx is None or now_s < nx["at_s"] - 1e-6:
            return
        self._prev = (nx["at_s"], (self._block.get("players") or {}) if self._block.get("source") != "neutral"
                      else {})
        f = nx["form"]
        if f.new:
            self._id += 1
            f.new = False
            self._log.append({"change": "form", "form": f.name, "form_kind": f.entry["kind"], "why": nx["why"],
                              "at_ms": round(nx["at_s"] * 1000.0, 1)})
        self.form = f
        self._at_s = nx["at_s"]
        self._next = None

    def _facts_now(self, piano: Optional[bool] = None) -> dict[str, bool]:
        return {NEED_SIDES: self._sides(), NEED_GRID: self._ok(self._grid),
                NEED_PIANO: self._piano if piano is None else bool(piano)}

    def _dwelt(self, at_s: float, grid: Any) -> bool:
        f = self.form
        if f.source == "default":
            return True
        if self._ok(grid):
            return grid.bar_at(at_s + 1e-6) - grid.bar_at(f.since_s + 1e-6) >= float(self.cfg["pick"]["dwell_bars"])
        return at_s - f.since_s >= float(self.cfg["pick"]["dwell_s"])

    def _pick(self, at_s: float, grid: Any, *, bypass: bool, why: str, source: str, piano: Optional[bool] = None
              ) -> Optional[_Form]:
        """A weighted draw at ``at_s`` (None: the form playing stays, or the dwell holds it)."""
        if not bypass and not self._dwelt(at_s, grid):
            return None
        facts = self._facts_now(piano)
        try:
            ws = self.catalogue.weights(self._direction, self._flow, facts)
        except Exception:                            # a broken catalogue never fails the tick
            log.exception("ensemble: weights failed")
            ws = {}
        cur = self.form
        if cur.name in ws:
            ws[cur.name] *= float(self.cfg["pick"]["stay"])
        self._weights = {k: round(v, 4) for k, v in ws.items()}
        if ws:
            name = _draw(ws, self._rng)
            entry = self.catalogue.entries[name]
            words = self.catalogue.why(name, self._direction)
        else:
            name, entry, words = BUILTIN_NAME, BUILTIN, "nothing else fits"
        self._log.append({"change": "pick", "form": name, "was": cur.name, "why": why, "words": words,
                          "weights": dict(self._weights), "facts": facts, "at_ms": round(at_s * 1000.0, 1)})
        if name == cur.name and entry is cur.entry:
            return None
        return _Form(name, entry, at_s, source, words, new=True)

    def on_look(self, now_s: float, at_s: float, *, look_id: str, reason: str, by: str, direction: Any, flow: Any,
                grid: Any, held: bool, piano: bool) -> None:
        """A look change announced for ``at_s``: maybe a new form (a song change bypasses the dwell), the lead
        passing or contrast's swap; the change goes out as ``next`` at at_s (staggers apply there); its gesture is
        queued (section for a section, a repeat or a song; phrase otherwise; none for a drop or relaxing)."""
        if not self.on:
            return
        self._now = now_s
        self._direction, self._flow, self._grid, self._piano = direction, flow, grid, bool(piano)
        nx = self._next
        why = LOOK_WHY.get(reason, "a new look")
        if nx is not None and nx.get("look_id") == look_id:
            nx["at_s"] = at_s                        # a re-announced look: the same decision moves
        else:
            base = nx["form"] if nx is not None and nx["form"].new else self._form_at(at_s)   # a pending pick
            nf = None
            if not held:
                facts = self._facts_now(piano)
                bypass = reason == "track" or not all(facts.get(x) for x in base.entry["needs"])
                nf = self._pick(at_s, grid, bypass=bypass, why=why, source="pick", piano=piano)
            if nf is None:
                nf = replace(base, new=base.new)
                if not held:
                    self._turns_at_look(nf, reason, at_s, grid)
            self._set_next(at_s, nf, why, look_id)
        self._held = held
        if reason not in LOOK_NONE:
            kind = "section" if reason in LOOK_SECTION else "phrase"
            self._queue(Seed(f"look:{look_id}", kind, "look", at_s, at_s > now_s,
                             self._strength(kind, 1.0), why=why))
        self._rebuild(now_s)

    def _turns_at_look(self, f: _Form, reason: str, at_s: float, grid: Any) -> None:
        """At a look change the form keeps: a solo's lead passes (pass_on section | look; a song always), call and
        answer starts again on the first team at a song change, contrast swaps (swap_on section | look)."""
        kind, P = f.entry["kind"], f.entry["params"]
        n = len(self._layout(f)[1])
        section = reason in LOOK_SECTION
        if kind == "solo_and_accompany" and n >= 2 and (reason == "track" or P["pass_on"] == "look"
                                                         or (P["pass_on"] == "section" and section)):
            f.lead = self._pass(f.lead, n, P["pass"])
        elif kind == "call_and_response" and reason == "track":
            f.caller0, f.phrase0 = 0, None
            f.since_s = at_s
        elif kind == "contrast" and (P["swap_on"] == "look" or (P["swap_on"] == "section" and section)):
            f.swap = not f.swap

    def _pass(self, lead: int, n: int, how: str) -> int:
        if how == "other" and n > 2:
            return self._rng.choice([i for i in range(n) if i != lead % n])
        return (lead + 1) % n

    def on_piano(self, present: bool, now_s: float, grid: Any, direction: Any, flow: Any) -> None:
        """The piano starts or stops: a pick now (on the next bar line the lead allows) when a piano form is
        eligible, or when the form playing needs the piano and it stopped."""
        self._piano = bool(present)
        if not self.on or self._held:
            return
        self._now = now_s
        self._direction, self._flow, self._grid = direction, flow, grid
        base = self._form_at(now_s)
        if present:
            ws = self.catalogue.weights(direction, flow, self._facts_now(True))
            es = self.catalogue.entries
            go = any(NEED_PIANO in es[n]["boost"] or NEED_PIANO in es[n]["needs"] for n in ws)
        else:
            go = NEED_PIANO in base.entry["needs"]
        if not go:
            return
        at = now_s + self._lead
        if self._ok(grid):
            at = grid.next_line(at, 1)[0]
        nf = self._pick(at, grid, bypass=True, why="the piano started" if present else "the piano stopped",
                        source="piano", piano=present)
        if nf is not None:
            self._set_next(at, nf, nf.why)
            self._rebuild(now_s)

    def on_event(self, kind: str, ev: Mapping, at_s: float, now_s: float, grid: Any) -> None:
        """structure's drop_armed (a predicted hit; a call-off re-sends it with no parts), drop (a hit heard, unless
        an armed line is within a beat), build (a swell up to the drop line) and breakdown (a section at half)."""
        if not self.on:
            return
        eid = str(ev.get("id") or f"{kind}:{round(at_s, 3)}")
        beat = self._beat_s(grid)
        if kind == "drop_armed":
            key = f"hit:{eid}"
            if "eta_beats" in ev and ev.get("eta_beats") is None:
                self._armed.pop(key, None)
                self._seeds.pop(key, None)
                self._call_off("g:" + key, now_s)
                return
            self._armed[key] = at_s
            self._queue(Seed(key, "hit", "structure", at_s, True, self._strength("hit", _num(ev.get("strength"))
                                                                                  or 1.0), why=KIND_WHY["hit"]))
        elif kind == "drop":
            if any(abs(a - at_s) <= beat + 0.05 for a in self._armed.values()):
                return
            self._queue(Seed(f"hit:{eid}", "hit", "structure", at_s, False,
                             self._strength("hit", _num(ev.get("strength")) or 1.0), why=KIND_WHY["hit"]))
        elif kind == "build":
            eta = _num(ev.get("drop_eta_beats")) if ev.get("drop_eta_beats") is not None else _num(ev.get("eta_beats"))
            if eta is not None and eta > 0:
                dur = eta * beat
            else:
                dur = 4.0 * (float(grid.bar_s) if self._ok(grid) and grid.bar_s else 4.0 * beat)
            self._queue(Seed(f"swell:{eid}", "swell", "structure", at_s, False, self._strength("swell", 1.0),
                             dur_s=max(0.0, dur), why=KIND_WHY["swell"]))
        elif kind == "breakdown":
            self._queue(Seed(f"section:{eid}", "section", "structure", at_s, False, self._strength("section", 0.5),
                             why="a breakdown"))

    def on_notes(self, notes: Sequence[tuple], now_s: float) -> None:
        """NoteTap.raw_since tuples (t_s, stream, channel, pitch, vel): bursts of note-ons (§3.4). A burst opens at
        a note-on at least piano.min_vel, takes every note-on within burst_ms of the previous one for at most
        max_burst_ms, and closes at the first tick after."""
        if not self.on:
            return
        pc = self.cfg["piano"]
        gap, most = float(pc["burst_ms"]) / 1000.0, float(pc["max_burst_ms"]) / 1000.0
        for n in notes or ():
            try:
                t, pitch, vel = float(n[0]), int(n[3]), int(n[4])
            except (TypeError, ValueError, IndexError):
                continue
            if vel <= 0:
                continue
            self._hist.append(pitch)
            self._split_cache = None
            b = self._burst
            if b is not None and t - b["last"] <= gap and t - b["first"] <= most:
                b["struck"].append((pitch, vel))
                b["last"] = t
                continue
            if b is not None:
                self._close_burst()
            if vel >= int(pc["min_vel"]):
                self._burst = {"first": t, "last": t, "struck": [(pitch, vel)]}

    def _close_burst(self) -> None:
        b, self._burst = self._burst, None
        if not b:
            return
        pc = self.cfg["piano"]
        loud: dict[int, int] = {}
        for p, v in b["struck"]:
            loud[p] = max(v, loud.get(p, 0))
        pitches = tuple(sorted(loud))
        kind = "chord" if len(b["struck"]) >= int(pc["chord_notes"]) else "note"
        reg = register_of([p for p, _ in b["struck"]])
        st = self._strength(kind, (max(loud.values()) / 127.0) ** float(pc["vel_curve"]))
        self._piano_n += 1
        self._queue(Seed(f"piano:{self._piano_n}", kind, "piano", b["first"], False, st, register=reg,
                         pitches=pitches, vels=tuple(loud[p] for p in pitches),
                         why=f"a {kind}, {register_word(reg)}"))

    # -- the tick ------------------------------------------------------------------------------------------------------------
    def tick(self, now_s: float, grid: Any, *, active: bool, accents: str, beat_lock: bool, energy: float,
             direction: Any, flow: Any, lead_s: Optional[float] = None, held: bool = False
             ) -> list[contract.GestureData]:
        """Route every queued moment (each through the form in effect at its at), add the clock's lines, fold
        ``next`` in once its moment passed, schedule call-and-answer turns and a solo's phrase passes as ``next``;
        returns the GestureData to publish (new ones and re-sends)."""
        self._now = now_s
        self._grid, self._direction, self._flow = grid, direction, flow
        self._held = bool(held)
        if lead_s is not None:
            self._lead = float(lead_s)
        out, self._out = list(self._out), []
        self._fold(now_s)
        self._active = bool(active)
        if not self.on or not active:
            self._seeds.clear()
            self._burst = None
            self._finish(now_s, out)
            return out
        if self._burst is not None:
            pc = self.cfg["piano"]
            b = self._burst
            if now_s - b["last"] > float(pc["burst_ms"]) / 1000.0 or \
                    now_s - b["first"] > float(pc["max_burst_ms"]) / 1000.0:
                self._close_burst()
        self._clock_seeds(now_s, grid, accents, beat_lock, energy)
        if not held:
            self._schedule_turns(now_s, grid)
        for s in sorted(self._seeds.values(), key=lambda x: x.at_s):
            try:
                g = self._route(s, now_s, grid)
            except Exception:                        # one bad moment never stops the band
                log.exception("ensemble: routing %s failed", s.key)
                g = None
            if g is not None:
                self._remember(g)
                out.append(g)
            if self._out:                            # re-sends a stronger part made (each id's latest wins)
                out += self._out
                self._out = []
        self._seeds.clear()
        self._finish(now_s, out)
        return out

    def _finish(self, now_s: float, out: list) -> None:
        self._prune(now_s)
        self._rebuild(now_s)
        self._lamps = self._lamp_tuple(now_s)

    def _clock_seeds(self, now_s: float, grid: Any, accents: str, beat_lock: bool, energy: float) -> None:
        """Each beat line in [now + lead, now + lead + horizon) once: a phrase line, a fill (a phrase's last bar
        while the energy is up), a downbeat or an accent: the first the feel's accents word, the clock's gate and
        the form in effect there allow."""
        if not (beat_lock and self._ok(grid)):
            return
        idx = contract.ACCENT_STYLES.index(accents) if accents in contract.ACCENT_STYLES else 0
        allowed = [k for k in CLOCK_LADDER[:idx] if grid.gate == "beat" or k not in BEAT_GATED]
        if not allowed:
            return
        gc = self.cfg["gestures"]
        epoch = (grid.body or {}).get("epoch") if isinstance(getattr(grid, "body", None), Mapping) else None
        t_lo = now_s + self._lead
        t_hi = t_lo + float(gc["horizon_s"])
        bpb, span = int(grid.bpb), self._phrase_beats(grid)
        k = math.ceil(grid.beat_at(t_lo) - 1e-9)
        for _ in range(64):
            t = grid.t_of_beat(k)
            if t >= t_hi:
                break
            key = f"beat:{epoch}:{k}"
            if t >= t_lo - 1e-9 and key not in self._seen:
                self._seen[key] = t
                routes = self._form_at(t).entry["routes"]
                rel = int(k - grid.bar_anchor)
                in_bar = rel % bpb
                kind, x = None, 1.0
                if rel % span == 0 and "phrase" in allowed and "phrase" in routes:
                    kind = "phrase"
                elif rel % span >= span - bpb and "fill" in allowed and "fill" in routes and \
                        energy >= float(gc["fill_energy"]):
                    kind, x = "fill", (in_bar + 1) / bpb
                elif in_bar == 0 and "downbeat" in allowed and "downbeat" in routes:
                    kind, x = "downbeat", 0.5 + 0.5 * _c01(energy)
                elif in_bar != 0 and "accent" in allowed and "accent" in routes:
                    kind, x = "accent", 0.5 + 0.5 * _c01(energy)
                if kind is not None:
                    self._queue(Seed(key, kind, "clock", t, True, self._strength(kind, x), why=KIND_WHY[kind],
                                     beat=float(k), bar=grid.bar_at(t + 1e-6)))
            k += 1

    def _schedule_turns(self, now_s: float, grid: Any) -> None:
        """call_and_response's roles swap on each half-phrase line, a solo's lead passes on each phrase line
        (pass_on phrase): as ``next`` at the line, at least the lead ahead (one slot: a pending change goes first)."""
        if self._next is not None:
            return
        f = self.form
        kind, P = f.entry["kind"], f.entry["params"]
        n = len(self._layout(f)[1])
        if n < 2 or kind not in ("call_and_response", "solo_and_accompany"):
            return
        if kind == "solo_and_accompany" and P["pass_on"] != "phrase":
            return
        t0 = now_s + self._lead
        if self._ok(grid):
            step = self._phrase_beats(grid) // (2 if kind == "call_and_response" else 1)
            rel = grid.beat_at(t0) - grid.bar_anchor
            at = grid.t_of_beat(grid.bar_anchor + math.ceil(rel / step - 1e-9) * step)
        else:
            step_s = float(P["half_s"]) if kind == "call_and_response" else 32 * float(self.cfg["no_grid_beat_s"])
            at = f.since_s + math.ceil((t0 - f.since_s) / step_s - 1e-9) * step_s
        if at - now_s > 2.0 * self._lead + float(self.cfg["gestures"]["horizon_s"]):
            return                                   # decided when it is close (one tick past the lead)
        nf = replace(f, new=False)
        if kind == "solo_and_accompany":
            nf.lead = self._pass(f.lead, n, P["pass"])
            why = "the lead passes"
        else:
            why = "call and answer: the other side's turn"
        if self._parts_at(nf, at, grid) == self._parts_at(f, now_s, grid):
            return                                   # a phrase line where the call turns but the roles stay
        self._set_next(at, nf, why)

    # -- routing (§4.3) -------------------------------------------------------------------------------------------------------
    def _route(self, s: Seed, now_s: float, grid: Any) -> Optional[contract.GestureData]:
        form = self._form_at(s.at_s)
        gid = "g:" + s.key
        parts = self._parts_for(s, form, grid) if s.kind in form.entry["routes"] else []
        parts = self._density(parts, gid, s.kind, grid)
        if not parts and gid not in self._recent:
            return None
        self._seq += 1
        g: contract.GestureData = {
            "id": gid, "seq": self._seq, "t_ms": round(now_s * 1000.0, 1), "kind": s.kind, "source": s.source,
            "form": form.name, "strength": round(s.strength, 4),
            "shape": dict(self.cfg["gestures"]["shapes"][s.kind]), "dur_ms": round(s.dur_s * 1000.0, 1),
            "register": s.register, "pitches": list(s.pitches),
            "beat": round(grid.beat_at(s.at_s), 3) if self._ok(grid) else None,
            "parts": [{"player": pid, "at_ms": round(at * 1000.0, 1), "strength": round(st, 4), "gen": gen,
                       "predicted": pred, "colour": col} for pid, at, st, gen, pred, col in parts],
            "why": s.why}
        self.counts["gestures"] += 1
        self.counts["parts"] += len(parts)
        bk = self.counts["by_kind"]
        bk[s.kind] = bk.get(s.kind, 0) + 1
        self._last.append({"id": gid, "kind": s.kind, "why": s.why, "parts": len(parts)})
        return g

    def _parts_for(self, s: Seed, form: _Form, grid: Any) -> list[tuple]:
        """(player, at_s, strength, gen, predicted, colour) for every part of ``s`` under ``form``."""
        kind, P = form.entry["kind"], form.entry["params"]
        order, ts = self._layout(form)
        n = len(ts)
        team_of = {pid: k for k, tm in enumerate(ts) for pid in tm}
        pp = self._parts_at(form, s.at_s, grid)
        col = self.cfg["gestures"]["colours"].get(s.kind)
        on = [p["id"] for p in self._players if p["on"] and p["kind"] != "device"]
        parts: list[tuple] = []

        def add(pid: str, at: float, st: float, gen: int, colour: Optional[str] = None) -> None:
            role_g = float((pp.get(pid) or {}).get("scale", {}).get("gestures", 1.0))
            c = colour if colour is not None else ((pp.get(pid) or {}).get("palette_role") or col)
            parts.append((pid, at, st * role_g, gen, bool(s.predicted or gen > 0), c))

        if s.kind == FILL_ROLL and order:
            beat_in_bar = int(round((s.beat or 0.0) - (grid.bar_anchor if self._ok(grid) else 0))) % \
                (int(grid.bpb) if self._ok(grid) else 4)
            live = [pid for pid in order if pid in self._pmap and self._pmap[pid]["on"]]
            if live:
                add(live[beat_in_bar % len(live)], s.at_s, s.strength, 0)
            return parts
        if s.source == "look":                       # each player's pop lands with its own cut
            for pid in on:
                k = team_of.get(pid, -1) if n >= 2 else -1
                gen = max(0, k)
                st = s.strength * (float(P["decay"]) ** gen if kind == "echo" else 1.0)
                add(pid, s.at_s + float(pp[pid]["stagger_s"]), st, gen)
            return parts
        delay = self._delay(form, grid)
        if s.kind == "hit" or n < 2 or s.kind in UNISON_IN.get(kind, ()):
            for pid in on:
                add(pid, s.at_s, s.strength, 0)
            if s.kind == "hit" and n >= 2 and kind in ("echo", "canon"):
                for j, ti in enumerate(self._visits(kind, P, 0, n)[1:], start=1):
                    st = s.strength * (float(P["decay"]) ** j if kind == "echo" else 1.0)
                    for pid in ts[ti]:
                        add(pid, s.at_s + j * delay, st, j)
            return parts
        if s.kind == "downbeat" and "every_bars" in P and s.bar is not None and s.bar % int(P["every_bars"]):
            return parts
        if kind in ("echo", "canon"):
            caller = 0
            if kind == "echo" and P["caller"] == "register" and s.register is not None:
                low = next((i for i, tm in enumerate(ts) if self._pmap[tm[0]]["side"] == SIDE_WORDS[0]), 0)
                high = next((i for i, tm in enumerate(ts) if i != low), (low + 1) % n)
                split = _c01((self._split(form) - REGISTER_A0) / REGISTER_SPAN)
                caller = low if s.register < split else high
            for j, ti in enumerate(self._visits(kind, P, caller, n)):
                st = s.strength * (float(P["decay"]) ** j if kind == "echo" else 1.0)
                for pid in ts[ti]:
                    add(pid, s.at_s + j * delay, st, j)
        elif kind == "call_and_response":
            caller, half = self._cr_state(form, s.at_s, grid, n)
            answer = (caller + 1) % n
            if half == 0:
                for pid in ts[caller]:
                    add(pid, s.at_s, s.strength, 0)
                if P["answer"] == "replay":
                    for pid in ts[answer]:
                        add(pid, s.at_s + self._half_s(form, grid), s.strength * float(P["decay"]), 1)
            elif P["answer"] == "own":
                for pid in ts[answer]:
                    add(pid, s.at_s, s.strength, 0)
        elif kind == "ping_pong":
            if s.source == "clock" and s.kind in ("accent", "downbeat") and s.beat is not None and self._ok(grid):
                rel = int(round(s.beat - grid.bar_anchor))
                step = {"beat": rel, "half_bar": rel // max(1, int(grid.bpb) // 2),
                        "bar": rel // int(grid.bpb)}[P["on"]]
                ti = step % n
            else:
                ti = self._turn % n
                self._turn += 1
            st = min(1.0, s.strength * (float(P["downbeat_boost"]) if s.kind == "downbeat" else 1.0))
            for pid in ts[ti]:
                add(pid, s.at_s, st, 0)
        elif kind == "solo_and_accompany":
            for pid in ts[form.lead % n]:
                add(pid, s.at_s, s.strength, 0)
        elif kind == "register_split":
            if s.kind in ("note", "chord") and s.pitches:
                split = self._split(form)
                vels = s.vels if len(s.vels) == len(s.pitches) else (127,) * len(s.pitches)
                lo = [v for p, v in zip(s.pitches, vels) if p < split]
                hi = [v for p, v in zip(s.pitches, vels) if p >= split]
                curve = float(self.cfg["piano"]["vel_curve"])
                low_side = P["low"]
                for side, vs in ((low_side, lo), (SIDE_WORDS[1 - SIDE_WORDS.index(low_side)], hi)):
                    if not vs:
                        continue
                    st = self._strength(s.kind, (max(vs) / 127.0) ** curve)
                    for pid in on:
                        if self._pmap[pid]["side"] == side and team_of.get(pid, -1) >= 0:
                            add(pid, s.at_s, st, 0)
            else:
                for pid in on:
                    add(pid, s.at_s, s.strength, 0)
        return parts

    def _delay(self, form: _Form, grid: Any) -> float:
        kind, P = form.entry["kind"], form.entry["params"]
        if kind == "echo":
            return float(P["delay_beats"]) * float(grid.beat_s) if self._ok(grid) else float(P["delay_s"])
        if kind == "canon":
            return float(P["offset_beats"]) * float(grid.beat_s) if self._ok(grid) else float(P["offset_s"])
        return 0.0

    @staticmethod
    def _visits(kind: str, P: Mapping, caller: int, n: int) -> list[int]:
        """The teams a call visits in turn: canon every team once; echo from the caller along the travel order,
        then bouncing back (A B A B ...) for ``repeats`` passes."""
        seq = [(caller + j) % n for j in range(n)]
        if kind != "echo":
            return seq
        walk = list(range(n))
        out = [0]
        i, step = 0, 1
        while len(out) < 1 + int(P["repeats"]) * (n - 1):
            if not 0 <= i + step < n:
                step = -step
            i += step
            out.append(walk[i])
        return [seq[j] for j in out]

    def _density(self, parts: list[tuple], gid: str, kind: str, grid: Any) -> list[tuple]:
        """§3.5: per player in time order: the spacing (min_gap_ms; a hit always plays; of two parts too close the
        stronger shows: a weaker one still to come is taken back from its gesture, which is re-sent), the per-bar
        count, the strength floor; at most max_parts, earliest kept. Each one counted."""
        gc = self.cfg["gestures"]
        for lst in self._sched.values():             # a re-send replaces its own parts
            lst[:] = [e for e in lst if e[1] != gid]
        gap = float(gc["min_gap_ms"]) / 1000.0
        out: list[tuple] = []
        bumped: list[tuple] = []
        for part in sorted(parts, key=lambda p: (p[1], p[0])):
            pid, at, st = part[0], part[1], part[2]
            if len(out) >= int(gc["max_parts"]):
                self.counts["trimmed"] += 1
                continue
            if st < float(gc["min_strength"]):
                self.counts["weak"] += 1
                continue
            lst = self._sched.setdefault(pid, [])
            bk = self._bar_key(at, grid)
            if kind != "hit":
                if any(o[0] == pid and abs(o[1] - at) < gap for o in out):
                    self.counts["spaced"] += 1
                    continue
                near = [e for e in lst if abs(e[0] - at) < gap]
                if near:
                    if not all(st > e[3] + 1e-9 and e[0] > self._now for e in near):
                        self.counts["spaced"] += 1
                        continue
                    bumped += [(e[1], pid, e[0]) for e in near]   # HTP: the stronger shows
                    lst[:] = [e for e in lst if e not in near]
                    self.counts["spaced"] += len(near)
                if sum(1 for e in lst if e[2] == bk) + sum(1 for o in out if o[0] == pid
                                                           and self._bar_key(o[1], grid) == bk) \
                        >= int(gc["max_per_bar"]):
                    self.counts["over_bar"] += 1
                    continue
            out.append(part)
        for part in out:
            self._sched.setdefault(part[0], []).append((part[1], gid, self._bar_key(part[1], grid), part[2]))
        if bumped:
            self._withdraw(bumped)
        return out

    def _withdraw(self, bumped: list[tuple]) -> None:
        """Re-send each gesture that lost a part to a stronger one (its other parts as they were)."""
        gone: dict[str, set] = {}
        for gid, pid, at in bumped:
            gone.setdefault(gid, set()).add((pid, round(at * 1000.0, 1)))
        for gid, drop in gone.items():
            g = self._recent.get(gid)
            if g is None:
                continue
            self._seq += 1
            ng = {**g, "seq": self._seq, "t_ms": round(self._now * 1000.0, 1),
                  "parts": [p for p in g["parts"] if (p["player"], p["at_ms"]) not in drop]}
            self._remember(ng)
            self._out.append(ng)

    def _call_off(self, gid: str, now_s: float) -> None:
        """Re-send a published gesture with ``parts: []``."""
        g = self._recent.get(gid)
        if g is None or not g["parts"]:
            return
        self._seq += 1
        off = {**g, "seq": self._seq, "t_ms": round(now_s * 1000.0, 1), "parts": []}
        for lst in self._sched.values():
            lst[:] = [e for e in lst if e[1] != gid]
        self._remember(off)
        self._out.append(off)

    # -- bookkeeping -----------------------------------------------------------------------------------------------------------
    def _remember(self, g: contract.GestureData) -> None:
        """Keep a gesture's latest message, with when it is over and its lights' parts (worked out once here)."""
        gid = g["id"]
        self._recent.pop(gid, None)
        self._recent[gid] = g
        ends = [self._end_s(g, p) for p in g["parts"]]
        self._ends[gid] = max(ends) if ends else g["t_ms"] / 1000.0 + CALL_OFF_KEEP_S - 1.0
        lamps = []
        for p, end in zip(g["parts"], ends):
            pl = self._pmap.get(p["player"])
            if pl is not None and pl["kind"] == "lights":
                lamps.append((end, (p["at_ms"] / 1000.0, float(p["strength"]), g["shape"], bool(p["predicted"]),
                                    p["colour"], float(g.get("dur_ms") or 0.0) / 1000.0, frozenset(pl["members"]))))
        self._lamp_parts[gid] = lamps
        keep = int(self.cfg["gestures"]["keep"])
        while len(self._recent) > keep:
            old, _ = self._recent.popitem(last=False)
            self._ends.pop(old, None)
            self._lamp_parts.pop(old, None)
        self._recent_dirty = True

    @staticmethod
    def _end_s(g: Mapping, p: Mapping) -> float:
        sh = g.get("shape") or {}
        return (p["at_ms"] + float(g.get("dur_ms") or 0.0) + float(sh.get("hold_ms") or 0.0)
                + LIFE_DECAYS * float(sh.get("decay_ms") or 0.0)) / 1000.0

    def _prune(self, now_s: float) -> None:
        for k in [k for k, t in self._seen.items() if t < now_s - SEEN_KEEP_S]:
            del self._seen[k]
        for k in [k for k, t in self._armed.items() if t < now_s - SEEN_KEEP_S]:
            del self._armed[k]
        for pid in list(self._sched):
            self._sched[pid] = [e for e in self._sched[pid] if e[0] >= now_s - SCHED_KEEP_S]
            if not self._sched[pid]:
                del self._sched[pid]
        for gid in [gid for gid, end in self._ends.items() if end < now_s - 1.0]:
            self._recent.pop(gid, None)
            self._ends.pop(gid, None)
            self._lamp_parts.pop(gid, None)
            self._recent_dirty = True
        if self._recent_dirty:
            self._recent_dirty = False
            self._recent_list = list(self._recent.values())

    def _lamp_tuple(self, now_s: float) -> tuple:
        return tuple(x for lamps in self._lamp_parts.values() for end, x in lamps if end >= now_s - 0.5)

    def next_look(self) -> Optional[str]:
        """The look change ``next`` waits for (its look_id), if it is one."""
        return self._next.get("look_id") if self._next is not None else None

    def cancel_look(self, look_id: str, now_s: float) -> None:
        """A look change that won't land (the director dropped it): its decision goes and its pop is called off."""
        if self._next is not None and self._next.get("look_id") == look_id:
            self._next = None
        self._seeds.pop(f"look:{look_id}", None)
        self._call_off(f"g:look:{look_id}", now_s)
        self._log.append({"change": "cancelled", "look_id": look_id})
        self._rebuild(now_s)

    def drain_log(self) -> list[dict]:
        """Every pick and form change since the last call (the director writes them to director.jsonl)."""
        out, self._log = self._log, []
        return out

    def reset(self, now_s: float, why: str) -> None:
        """Back to today's room (the built-in unison), nothing scheduled: gestures still to come are called off."""
        for gid, g in list(self._recent.items()):
            if g["parts"] and max(p["at_ms"] for p in g["parts"]) / 1000.0 > now_s:
                self._call_off(gid, now_s)
        self.form = _Form(BUILTIN_NAME, BUILTIN, now_s, "default", why)
        self._at_s = now_s
        self._next = None
        self._prev = None
        self._seeds.clear()
        self._armed.clear()
        self._burst = None
        self._sched.clear()
        self._id += 1
        self._log.append({"change": "reset", "why": why})
        self._rebuild(now_s, force=True)

    def view(self, now_s: float) -> dict:
        """The snapshot's ``ensemble`` (§5.6)."""
        b = self._block
        nx = b.get("next")
        return {"form": b.get("form"), "kind": b.get("kind"), "label": b.get("label"), "line": b.get("line"),
                "why": b.get("why"), "source": b.get("source"), "on": self.on,
                "since_s": round(max(0.0, now_s - self.form.since_s), 1),
                "order": list(b.get("order") or []),
                "roles": {pid: p["role"] for pid, p in (b.get("players") or {}).items()},
                "next": ({"at_in_s": round(nx["at_ms"] / 1000.0 - now_s, 2), "form": nx["form"],
                          "roles": {pid: p["role"] for pid, p in nx["players"].items()}} if nx else None),
                "players": [{"id": p["id"], "label": p["label"], "side": p["side"], "via": p["via"], "on": p["on"]}
                            for p in self._players],
                "counts": {**{k: v for k, v in self.counts.items() if k != "by_kind"},
                           "by_kind": dict(self.counts["by_kind"])},
                "last": list(self._last), "weights": dict(self._weights), "problems": list(self.problems),
                "catalogue": {"revision": self.catalogue.revision, "error": self.catalogue.error,
                              "problems": list(self.catalogue.problems)}}
