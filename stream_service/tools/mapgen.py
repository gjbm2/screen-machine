"""The per-item music mappings' starting point: config/live/mappings.generated.json (mappings.py, binding.py).

  python -m stream_service.tools.mapgen rules [--out FILE]     draft every scene / effect / transition from its
                                                               parameter families (deterministic; keeps reviewed
                                                               items whose params have not changed)
  python -m stream_service.tools.mapgen batches OUTDIR [--per N]   review batches for the LLM agents
  python -m stream_service.tools.mapgen merge FILE...          fold reviewed batches in (validated, trace-checked)
  python -m stream_service.tools.mapgen check                  validate the generated file against the plug-ins

No GPU and no session: it reads the plug-in registry and the preset tables only.  Run it niced.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import inspect
import json
import logging
import pathlib
import re
import sys
from typing import Any, Optional

from .. import binding
from ..mappings import CANON, GEN_FILE, preview, schema_hash
from . import mapgen_families as families

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "live"
KINDS = {"scene": ("source", "sources"), "effect": ("effect", "effects"), "transition": ("transition", "transitions")}
MAX_LIVE, MAX_SECTION = 8, 10
FLASH = binding.FLASH_RX
GLSL_PREFIX = ("st_", "isf_", "glt_")
GLSL_LIVE = {"palette_period", "drift_speed"}      # GLSL wrapper params read every frame (glsl/plugins.py)


def ins(*terms: Any) -> list[dict]:
    out = []
    for t in terms:
        sig, w, *extra = t
        out.append({"sig": sig, "w": w, **(extra[0] if extra else {})})
    return out


INV = {"inv": True}
# family -> rule for scenes and effects: mode, inputs, map, curve, ease_s, hero (a beat-kick candidate)
RULES: dict[str, dict] = {
    "macro.energy": dict(at="live", in_=ins(("energy", .85), ("motion", .15)), map={"band": .1}),
    "macro.pulse": dict(at="live", in_=ins(("beat", 1), ("punch", 1, {"floor": .6})), op="mul", map="full"),
    "macro.amount": dict(at="live", in_=ins(("energy", .7), ("activity_slow", .3)), curve="ease",
                         map={"scale": [.55, 1.0]}),
    "colour_cycle_rate": dict(at="live", in_=ins(("energy", .6), ("tempo", .4)), curve="ease",
                              map={"scale": [.7, 1.4]}),
    "motion_rate": dict(at="live", in_=ins(("motion", .4), ("drive", .3), ("tempo", .3)), map={"scale": [.75, 1.35]},
                        hero=True),
    "rotation_rate": dict(at="live", in_=ins(("motion", .6), ("drive", .4)), map={"scale": [.7, 1.45]}, hero=True),
    "zoom_rate": dict(at="live", in_=ins(("drive", .6), ("energy", .4)), map={"scale": [.7, 1.4]}, hero=True),
    "travel_rate": dict(at="live", in_=ins(("tempo", .5), ("drive", .5)), map={"scale": [.75, 1.35]}, hero=True),
    "distortion_amplitude": dict(at="section", in_=ins(("energy", .7), ("drive", .3)), curve="ease",
                                 map={"around": [-.1, .1]}, live_glsl=True),
    "glow_light": dict(at="section", in_=ins(("energy", .6), ("punch", .4)), map={"scale": [.8, 1.25]}),
    "brightness_exposure": dict(at="section", in_=ins(("level_slow", .5), ("energy", .5)), map={"scale": [.9, 1.08]},
                                skip=r"backdrop|background|dim|shading|shade|ambient|ao_|absorb|threshold"),
    "beat_trigger": dict(at="section", in_=ins(("punch", .6), ("drive", .4)), map={"scale": [.4, 1.3]}),
    # never above the authored amount (a feedback at its max never fades): busy music shortens the trails
    "feedback_trail": dict(at="section", in_=ins(("energy", 1, INV)), map={"around": [-.12, 0.0]}),
    "fog_atmosphere": dict(at="section", in_=ins(("energy", 1, INV)), map={"around": [-.12, .12]}, ease=5.0),
    "glitch_noise": dict(at="section", in_=ins(("energy", .6), ("drive", .4)), curve="in",
                         map={"around": [-.3, .15]}),
    "density_count": dict(at="section", in_=ins(("energy", .8), ("drive", .2)), map={"around": [-.1, .15]}),
    "symmetry_count": dict(at="section", in_=ins(("gobo", 1)), map={"around": [-.15, .15]}, ease=6.0),
    "style_morph": dict(at="section", in_=ins(("gobo", 1)), map={"around": [-.2, .2]}, ease=6.0),
    "scale_size": dict(at="section", in_=ins(("zoom_slow", .6), ("energy", .4)), map={"around": [-.1, .1]}),
    "camera_distance": dict(at="section", in_=ins(("zoom_slow", .6, INV), ("energy", .4, INV)),
                            map={"around": [-.1, .1]}),
    "position_xy": dict(at="live", in_=ins(("pan_slow", 1)), map={"around": [-.15, .15]}, ease=1.0, axis=True),
    "camera_angle": dict(at="live", in_=ins(("pan_slow", 1)), map={"around": [-.12, .12]}, ease=1.5, axis=True),
    "light_direction": dict(at="live", in_=ins(("pan_slow", 1)), map={"around": [-.25, .25]}, ease=2.0, axis=True),
    "tint_colour_mix": dict(at="live", in_=ins(("warmth", 1)), map={"around": [-.15, .15]}, ease=3.0,
                            skip=r"^[rgba]_|bias_[rgb]|intensity_[rgb]|xor_|color_base_[rgb]|red_cyan"),
    "saturation": dict(at="section", in_=ins(("energy", .6), ("sat", .4)), map={"around": [-.1, .15]},
                       skip=r"grey|gray"),
    "contrast_tone": dict(at="section", in_=ins(("energy", 1)), map={"around": [-.08, .1]},
                          skip=r"posteri|dither|bit|quantiz|levels|tone_mode|q_level"),
    "line_width": dict(at="section", in_=ins(("punch", .5), ("energy", .5)), map={"around": [-.1, .15]}),
    "snap_character": dict(at="section", in_=ins(("punch", .5), ("energy", .5)), map={"around": [-.2, .2]}),
    "timing": dict(at="cue", in_=ins(("energy", 1, INV)), map={"scale": [.7, 1.4]}, skip=r"steps_per_beat"),
}
# transitions: cue only (sampled when a cut is prepared)
TRANSITION_RULES: dict[str, dict] = {
    "macro.duration": dict(in_=ins(("energy", 1, INV)), map={"scale": [.65, 1.35]}, quant="beat"),
    "position_xy": dict(in_=ins(("pan_slow", 1)), map={"around": [-.3, .3]}, axis=True),
    "direction_angle": dict(in_=ins(("pan_slow", 1)), map={"around": [-.3, .3]}, axis=True),
    "camera_angle": dict(in_=ins(("pan_slow", 1)), map={"around": [-.3, .3]}, axis=True),
    "light_direction": dict(in_=ins(("pan_slow", 1)), map={"around": [-.3, .3]}, axis=True),
    "glow_light": dict(in_=ins(("punch", .5), ("energy", .5)), map={"scale": [.7, 1.4]}),
    "beat_trigger": dict(in_=ins(("punch", .5), ("energy", .5)), map={"scale": [.7, 1.4]}),
    "brightness_exposure": dict(in_=ins(("punch", .5), ("energy", .5)), map={"scale": [.85, 1.15]}),
    "threshold_softness": dict(in_=ins(("energy", 1, INV)), map={"around": [-.25, .25]}),
    "density_count": dict(in_=ins(("energy", 1)), map={"around": [-.1, .15]}),
    "symmetry_count": dict(in_=ins(("energy", 1)), map={"around": [-.1, .15]}),
    "scale_size": dict(in_=ins(("energy", 1)), map={"around": [-.15, .15]}),
}
TRANSITION_NUMERIC = dict(in_=ins(("energy", 1)), map={"around": [-.2, .2]})   # other signal-like families
SIGNAL_FAMS = {"beat_trigger", "brightness_exposure", "camera_angle", "camera_distance", "colour_cycle_rate",
               "contrast_tone", "density_count", "distortion_amplitude", "feedback_trail", "fog_atmosphere",
               "glitch_noise", "glow_light", "light_direction", "line_width", "motion_rate", "rotation_rate",
               "zoom_rate", "travel_rate", "position_xy", "saturation", "scale_size", "snap_character", "style_morph",
               "symmetry_count", "timing", "tint_colour_mix"}
TRANSITION_SKIP = {"seed", "quality", "internal_smoothing", "legacy_input", "phase_offset", "other", "mode_enum",
                   "colour_rgb", "colour_background", "palette", "hue_palette_index", "iterations_detail"}


def _ytoken(name: str) -> bool:
    return bool(re.search(r"(_|^)d?y$|_y_|tilt|pt_y|_y\d*$|elev|phi|height|inclination|pitch", name.lower()))


def _is_glsl(plugin: str, props: dict) -> bool:
    return plugin.startswith(GLSL_PREFIX) or any(p.get("x-glsl") for p in props.values())


def draft_param(kind: str, plugin: str, name: str, prop: dict, glsl: bool) -> Optional[tuple[str, dict]]:
    """-> (family, binding) or None (no mapping) for one param."""
    fam = families.classify(KINDS[kind][1], name, prop)
    if binding.not_mappable(name, prop) and fam not in ("palette",):
        return None
    if name == "palette_mix":
        return None                                 # the colour form on palette raises it
    if binding.is_palette_param(name, prop) and kind != "transition":
        return "palette", {"colour": "room", "mix": .35 if glsl and kind == "scene" else .5,
                           "fam": "palette", "src": "rule"}
    if name == "palette_period" and kind != "transition":
        b = {"at": "section", "in": ins(("energy", 1)), "map": {"scale": [.8, 1.3]}}
        return "palette", _tier(b, prop, name, glsl, "palette")
    typ = prop.get("type")
    if kind == "transition":
        if name == "easing" and "ease-out" in (prop.get("enum") or ()):
            return "macro.easing", {"at": "cue", "in": ins(("energy", 1)),
                                    "steps": [None, None, None, "ease-out"], "fam": "macro.easing", "src": "rule"}
        if typ == "boolean":
            return "toggle", {"at": "cue", "in": ins(("gobo", 1)), "steps": [False, True], "fam": "toggle",
                              "src": "rule"}
        if typ not in ("number", "integer") or "enum" in prop or fam in TRANSITION_SKIP:
            return None
        if name == "duration_s":
            fam = "macro.duration"
        r = TRANSITION_RULES.get(fam) or (TRANSITION_NUMERIC if fam in SIGNAL_FAMS else None)
        if r is None:
            return None
        b = {"at": "cue", "in": _axis(r, name), "map": dict(r["map"]), "fam": fam, "src": "rule"}
        if r.get("quant"):
            b["quant"] = r["quant"]
        return fam, _flash(b, name)
    if typ not in ("number", "integer") or "enum" in prop:
        return None
    r = RULES.get(fam)
    if r is None:
        return None
    if r.get("skip") and re.search(r["skip"], name.lower()):
        return None
    b: dict = {"at": r["at"], "in": _axis(r, name), "op": r.get("op", "mean"), "curve": r.get("curve", "linear")}
    if r["map"] == "full":
        b["map"] = {"range": [prop.get("minimum", 0.0), prop.get("maximum", 1.0)]}
    else:
        b["map"] = dict(r["map"])
    if r.get("ease") is not None:
        b["ease_s"] = r["ease"]
    if r.get("live_glsl") and glsl and prop.get("x-glsl"):
        b.update(at="live", live_ok=True, ease_s=2.5)
    return fam, _flash(_tier(b, prop, name, glsl, fam), name)


def _axis(r: dict, name: str) -> list[dict]:
    terms = [dict(t) for t in r["in_"]]
    if r.get("axis") and _ytoken(name):
        terms = [{**t, "sig": {"pan_slow": "tilt_slow"}.get(t["sig"], t["sig"])} for t in terms]
    return terms


def _tier(b: dict, prop: dict, name: str, glsl: bool, fam: str) -> dict:
    """Fit a rule to the param's tier: structural and plain integers at cue; regional live only when read per frame."""
    typ, tier, bindable = prop.get("type"), prop.get("x-tier"), bool(prop.get("x-bindable"))
    if tier == "structural" or (typ == "integer" and not bindable):
        b = {**b, "at": "cue"}
        b.pop("ease_s", None)
    elif b["at"] == "section" and typ == "integer":
        b = {**b, "at": "cue"}
        b.pop("ease_s", None)
    elif b["at"] == "live" and tier == "regional" and not bindable:
        if glsl and (prop.get("x-glsl") or name in GLSL_LIVE):
            b = {**b, "live_ok": True, "ease_s": max(2.5, b.get("ease_s") or 0.0)}
        else:
            b = {**b, "at": "section"}
            b.pop("ease_s", None)
    b["fam"], b["src"] = fam, "rule"
    return b


def _flash(b: dict, name: str) -> dict:
    """A flash-class param never goes above its base."""
    if FLASH.search(name) and "map" in b:
        m = b["map"]
        if "scale" in m:
            b["map"] = {"scale": [min(m["scale"][0], .6), 1.0]}
        elif "around" in m:
            b["map"] = {"around": [min(m["around"][0], -.1), 0.0]}
    return b


def draft_item(kind: str, plugin: str, cls: type) -> dict:
    props = cls.param_props()
    glsl = _is_glsl(plugin, props)
    bindings: dict = {}
    hero = None
    for name, prop in props.items():
        got = draft_param(kind, plugin, name, prop, glsl)
        if got is None:
            continue
        fam, b = got
        try:
            nb = binding.parse_v2(name, prop, b, kind=kind)
        except binding.MappingError:
            if b.get("at") == "live":           # e.g. a kick or live rule that this param's tier refuses
                b = {k: v for k, v in b.items() if k not in ("kick", "live_ok")}
                b["at"] = "section" if prop.get("type") == "number" and prop.get("x-tier") != "structural" else "cue"
                b.pop("ease_s", None)
                try:
                    nb = binding.parse_v2(name, prop, b, kind=kind)
                except binding.MappingError:
                    continue
            else:
                continue
        if hero is None and kind != "transition" and nb.get("at") == "live" and RULES.get(fam, {}).get("hero"):
            kicked = {**b, "kick": {"sig": "beat", "amt": .08, "by": "punch"}}
            try:
                nb = binding.parse_v2(name, prop, kicked, kind=kind)
                hero = name
            except binding.MappingError:
                pass
        bindings[name] = nb
    # caps: the first MAX_LIVE live and MAX_SECTION section mappings (macros first)
    order = sorted(bindings, key=lambda p: (0 if p in ("energy", "pulse", "amount", "palette") else 1, p))
    n_live = n_sec = 0
    kept = {}
    for p in order:
        b = bindings[p]
        at = b.get("at")
        if at == "live" and "colour" not in b:
            n_live += 1
            if n_live > MAX_LIVE:
                continue
        elif at == "section":
            n_sec += 1
            if n_sec > MAX_SECTION:
                continue
        kept[p] = b
    return {"schema_hash": schema_hash(props), "follow": True, **({"hero": hero} if hero else {}),
            "bindings": dict(sorted(kept.items())), "review": {"by": "rule"}}


def registry():
    from ..plugins import Registry
    return Registry().discover()


def load_gen(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {"format": 1, "items": {}}


def write_gen(path: pathlib.Path, data: dict) -> None:
    """One item per line, sorted: readable diffs."""
    items = data.get("items", {})
    lines = [f"  {json.dumps(k)}: {json.dumps(items[k], sort_keys=True)}" for k in sorted(items)]
    head = {k: v for k, v in data.items() if k != "items"}
    text = "{\n" + "".join(f"  {json.dumps(k)}: {json.dumps(v, sort_keys=True)},\n" for k, v in sorted(head.items()))
    text += '  "items": {\n' + ",\n".join("  " + ln for ln in lines) + "\n  }\n}\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def rules_hash() -> str:
    src = inspect.getsource(sys.modules[__name__]) + inspect.getsource(families)
    return hashlib.sha1(src.encode()).hexdigest()[:10]


def cmd_rules(args) -> None:
    reg = registry()
    path = pathlib.Path(args.out)
    old = load_gen(path)
    items, kept, counts = {}, 0, {"live": 0, "section": 0, "cue": 0, "colour": 0}
    for kind, (rkind, _) in KINDS.items():
        for plugin, cls in sorted(reg.table(rkind).items()):
            if getattr(cls, "internal", False):
                continue
            key = f"{kind}/{plugin}"
            prev = old.get("items", {}).get(key)
            rec = draft_item(kind, plugin, cls)
            if prev and (prev.get("review") or {}).get("by") == "llm" and prev.get("schema_hash") == rec["schema_hash"]:
                rec = prev                            # a reviewed item whose params have not changed
                kept += 1
            items[key] = rec
            for b in rec.get("bindings", {}).values():
                if b and "colour" in b:
                    counts["colour"] += 1
                elif b:
                    counts[b.get("at", "live")] += 1
    data = {"format": 1, "gen": {"rev": "g-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M"),
                                 "rules_hash": rules_hash(), "at": dt.datetime.now(dt.timezone.utc).isoformat(
                                     timespec="seconds")}, "items": items}
    write_gen(path, data)
    print(f"{len(items)} items ({kept} reviewed kept); mappings {counts} -> {path}")


def cmd_check(args) -> None:
    reg = registry()
    data = load_gen(pathlib.Path(args.out))
    bad = 0
    for key, rec in data.get("items", {}).items():
        kind, plugin = key.split("/", 1)
        try:
            cls = reg.table(KINDS[kind][0])[plugin]
        except KeyError:
            print(f"{key}: no such plug-in")
            bad += 1
            continue
        props = cls.param_props()
        for p, b in (rec.get("bindings") or {}).items():
            if b is None:
                continue
            if p not in props:
                print(f"{key}.{p}: no such param")
                bad += 1
                continue
            try:
                binding.parse_v2(p, props[p], b, kind=kind)
            except binding.MappingError as e:
                print(f"{key}.{p}: {e.detail}")
                bad += 1
        for p, row in preview(props, rec.get("bindings") or {}, {}, key).items():
            vals = [v for v in row.values() if isinstance(v, (int, float))]
            lo, hi = props[p].get("minimum"), props[p].get("maximum")
            if any(v != v or (lo is not None and v < lo - 1e-4) or (hi is not None and v > hi + 1e-4) for v in vals):
                print(f"{key}.{p}: preview out of the schema: {row}")
                bad += 1
    print(f"{len(data.get('items', {}))} items, {bad} problems")
    sys.exit(1 if bad else 0)


# -- the LLM review ------------------------------------------------------------------------------------------
def digest(cls: type, limit: int = 150) -> str:
    """The source lines that read params (tagged setup / render), else the shader's uniforms and head."""
    try:
        src = inspect.getsource(cls)
    except (OSError, TypeError):
        src = ""
    glsl = getattr(cls, "GLSL", None)
    if isinstance(glsl, str) and glsl:
        lines = glsl.splitlines()
        names = {(pr.get("x-glsl") or {}).get("u") for pr in cls.param_props().values() if pr.get("x-glsl")}
        names.discard(None)
        rx = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names))) + r")\b") if names else None
        used = [f"{i + 1}: {ln.strip()}" for i, ln in enumerate(lines) if rx and rx.search(ln)]
        uni = {p: (pr.get("x-glsl") or {}).get("u") for p, pr in cls.param_props().items() if pr.get("x-glsl")}
        return "\n".join([f"// param -> uniform: {uni}", "// lines that use those uniforms:"] + used[:limit - 40]
                         + ["// shader head:"] + lines[:40])
    out, where = [], "class"
    for ln in src.splitlines():
        m = re.match(r"\s*def (\w+)", ln)
        if m:
            where = "setup" if m.group(1) in ("setup", "__init__", "_alloc", "_build") else m.group(1)
        if re.search(r"p\[[\"']|self\.params\[|params\[[\"']|p\.get\(", ln):
            out.append(f"[{where}] {ln.strip()}")
    return "\n".join(out[:limit])


def cmd_batches(args) -> None:
    reg = registry()
    data = load_gen(pathlib.Path(args.out))
    outdir = pathlib.Path(args.dir)
    outdir.mkdir(parents=True, exist_ok=True)
    from ..presets import Presets
    presets = Presets(CONFIG, reg)
    tables = {"scene": presets.scenes, "effect": presets.effects, "transition": presets.transitions}
    groups: dict[str, list] = {}
    for kind, (rkind, _) in KINDS.items():
        for plugin, cls in sorted(reg.table(rkind).items()):
            if getattr(cls, "internal", False):
                continue
            key = f"{kind}/{plugin}"
            rec = data.get("items", {}).get(key) or draft_item(kind, plugin, cls)
            if (rec.get("review") or {}).get("by") == "llm" and not args.all:
                continue
            props = cls.param_props()
            kkey = KINDS[kind][0]
            pres = [{"name": n, "tags": e.get("tags") or {}, "params": {k: v for k, v in (e.get("params") or {}).items()
                                                                          if not isinstance(v, dict)}}
                    for n, e in sorted(tables[kind].items()) if e.get(kkey) == plugin][:4]
            item = {"key": key, "schema_hash": rec.get("schema_hash"), "label": getattr(cls, "label", "") or plugin,
                    "description": (getattr(cls, "description", "") or (cls.__doc__ or "").strip())[:400],
                    "stage": getattr(cls, "stage", None),
                    "params": {p: {**{k: v for k, v in pr.items() if k in ("type", "minimum", "maximum", "default",
                                                                          "enum", "x-tier", "x-bindable")},
                                   "label": (pr.get("x-ui") or {}).get("label"),
                                   "modes": __import__("stream_service.mappings", fromlist=["modes_for"]).modes_for(
                                       p, pr, kind),
                                   "x-glsl": bool(pr.get("x-glsl"))}
                               for p, pr in props.items()},
                    "presets": pres, "draft": rec.get("bindings") or {},
                    "draft_preview": preview(props, rec.get("bindings") or {}, {}, key),
                    "source_digest": digest(cls),
                    "thumbnail": str(pathlib.Path.home() / ".cache" / "sm-fxtest" / "out" / plugin / "sheet.png")}
            grp = kind if kind != "scene" else ("scene_glsl" if plugin.startswith(GLSL_PREFIX) else "scene_native")
            groups.setdefault(grp, []).append(item)
    n = 0
    index = []
    for grp, items in groups.items():
        per = args.per
        for i in range(0, len(items), per):
            bid = f"{grp}-{i // per + 1:02d}"
            (outdir / f"{bid}.json").write_text(json.dumps({"batch": bid, "items": items[i:i + per]}, indent=1))
            index.append({"batch": bid, "n": len(items[i:i + per]), "file": str(outdir / f"{bid}.json")})
            n += 1
    (outdir / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{n} batches, {sum(b['n'] for b in index)} items -> {outdir}")


def cmd_merge(args) -> None:
    reg = registry()
    path = pathlib.Path(args.out)
    data = load_gen(path)
    items = data.setdefault("items", {})
    stats = {"items": 0, "kept": 0, "edited": 0, "skipped": 0, "rejected": 0, "bindings_rejected": 0}
    for f in args.files:
        try:
            batch = json.loads(pathlib.Path(f).read_text())
        except (OSError, ValueError) as e:
            print(f"{f}: unreadable: {e}")
            continue
        for it in batch.get("items") or []:
            key = it.get("key")
            if not isinstance(key, str) or "/" not in key:
                stats["rejected"] += 1
                continue
            kind, plugin = key.split("/", 1)
            try:
                cls = reg.table(KINDS[kind][0])[plugin]
            except KeyError:
                stats["rejected"] += 1
                continue
            props = cls.param_props()
            cur = items.get(key) or draft_item(kind, plugin, cls)
            if it.get("schema_hash") not in (None, cur.get("schema_hash")):
                print(f"{key}: schema changed since the batch: kept the draft")
                stats["rejected"] += 1
                continue
            stats["items"] += 1
            rec = dict(cur)
            dec = it.get("decision", "keep")
            new_b = dict(cur.get("bindings") or {})
            flags = list(it.get("flags") or [])
            if dec == "skip":
                rec["follow"] = False
                stats["skipped"] += 1
            for p, b in (it.get("bindings") or {}).items():
                if p not in props:
                    continue
                if b is None:
                    new_b.pop(p, None)
                    continue
                if not isinstance(b, dict):
                    continue
                why = (it.get("why") or {}).get(p)
                b = {**b, "src": "llm", **({"why": str(why)[:140]} if why else {}),
                     "fam": b.get("fam") or (cur.get("bindings", {}).get(p) or {}).get("fam") or "llm"}
                try:
                    nb = binding.parse_v2(p, props[p], b, kind=kind)
                except binding.MappingError as e:
                    stats["bindings_rejected"] += 1
                    print(f"{key}.{p}: rejected: {e.detail}")
                    continue
                ok, why_bad = trace_ok(p, props[p], nb, key)
                if not ok:
                    stats["bindings_rejected"] += 1
                    print(f"{key}.{p}: rejected by the trace check: {why_bad}")
                    continue
                new_b[p] = nb
            changed = sum(1 for p in set(new_b) | set(cur.get("bindings") or {})
                          if new_b.get(p) != (cur.get("bindings") or {}).get(p))
            if changed > 10:
                flags.append("needs_eyes")
            stats["edited" if changed else "kept"] += 1
            if "hero" in it:
                if it["hero"]:
                    rec["hero"] = it["hero"]
                else:
                    rec.pop("hero", None)
            rec["bindings"] = dict(sorted(new_b.items()))
            rec["review"] = {"by": "llm", "batch": batch.get("batch"), "confidence": it.get("confidence"),
                             "flags": sorted(set(flags)), "decision": dec}
            items[key] = rec
    if args.dry_run:
        print(json.dumps({**stats, "dry_run": True}))
        sys.exit(1 if stats["rejected"] or stats["bindings_rejected"] else 0)
    data.setdefault("gen", {})["merged_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    write_gen(path, data)
    print(json.dumps(stats))


def trace_ok(p: str, prop: dict, b: dict, key: str) -> tuple[bool, str]:
    """Idle gives base; values stay in the schema; energy-like inputs make the chorus at least the ambient."""
    row = preview({p: prop}, {p: b}, {}, key).get(p) or {}
    if "error" in row:
        return False, row["error"]
    base = prop.get("default")
    st = row.get("stale")
    if isinstance(base, (int, float)) and isinstance(st, (int, float)) and "range" not in (b.get("map") or {}):
        span = (prop.get("maximum", 1) - prop.get("minimum", 0)) or 1
        if abs(st - base) > 1e-6 * span + (0.5 if prop.get("type") == "integer" else 0):
            return False, f"idle {st} is not the base {base}"
    lo, hi = prop.get("minimum"), prop.get("maximum")
    for v in row.values():
        if isinstance(v, (int, float)) and ((lo is not None and v < lo - 1e-9) or (hi is not None and v > hi + 1e-9)):
            return False, f"{v} is outside [{lo}, {hi}]"
    return True, ""


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.ERROR)
    ap = argparse.ArgumentParser(prog="mapgen")
    ap.add_argument("--out", default=str(CONFIG / GEN_FILE))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("rules")
    sub.add_parser("check")
    b = sub.add_parser("batches")
    b.add_argument("dir")
    b.add_argument("--per", type=int, default=12)
    b.add_argument("--all", action="store_true", help="include items already reviewed")
    m = sub.add_parser("merge")
    m.add_argument("files", nargs="+")
    m.add_argument("--dry-run", action="store_true", help="validate only: print rejections, write nothing")
    args = ap.parse_args(argv)
    {"rules": cmd_rules, "check": cmd_check, "batches": cmd_batches, "merge": cmd_merge}[args.cmd](args)


if __name__ == "__main__":
    main()
