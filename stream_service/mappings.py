"""Per-item music mappings (owner, 15 Oct 2026): which show signals move which params of each scene, effect and
transition, next to the Library's tags and ratings.

Two files in config/live, keyed like ratings (``scene/<plug-in>``, ``effect/<plug-in>``, ``transition/<plug-in>``):
  mappings.generated.json   written by tools/mapgen.py only (rules, then the LLM review): the starting point
  mappings.json             the owner's edits, through the API only (sparse; hand-editable; never regenerated)

  {"format": 1, "items": {"scene/electric_storm": {
      "follow": true,                         false: no music mapping at all for this item (spec bindings only)
      "bindings": {"turbulence": {...v2...}, "mask": null},     null: no mapping (the plain value holds)
      "presets": {"electric-storm-tempest": {"mask": {...}}},   per-preset sections, over the plug-in level
      "schema_hash": "9f3a1c02", "hero": "bolt_rate", "review": {...}, "rev": 3, "at": "..."}}}

Precedence per param (later wins): class defaults and the plug-in presets' generic bindings ({bind: energy},
{kick: beat, amount: 1}) < generated < bindings a preset authors < the owner's edits < a cue / PATCH spec.
Edits apply at the next resolve (a cue, a pick, an FX add); a session PATCH tries one on screen at once.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import logging
import pathlib
import threading
import time
from typing import Any, Optional

from . import binding
from .plugins.base import ParamError

log = logging.getLogger("stream_service.mappings")

GEN_FILE, USER_FILE = "mappings.generated.json", "mappings.json"
KINDS = {"scene": "source", "effect": "effect", "transition": "transition"}
MAX_LIVE = 24

# Canonical show states for previews and the generator's checks (the fake show's means, 15 Oct design).
CANON = {
    "stale": {},
    "ambient": {"energy": .13, "drive": .16, "punch": .03, "tempo": None, "activity": .12, "activity_slow": .12,
                "level": .35, "level_slow": .35, "motion": .08, "sat": .45, "warmth": .18, "pan_slow": .5,
                "tilt_slow": .5, "zoom_slow": None, "pan": .5, "tilt": .5, "zoom": .5, "beat": 0.0, "pulse": 0.0,
                "section": 0.0, "beat_confidence": .2, "gobo": 1, "energy_slow": .13},
    "jazz": {"energy": .41, "drive": .46, "punch": .22, "tempo": .40, "activity": .42, "activity_slow": .42,
             "level": .55, "level_slow": .55, "motion": .3, "sat": .7, "warmth": .85, "pan_slow": .45,
             "tilt_slow": .55, "zoom_slow": None, "pan": .45, "tilt": .55, "zoom": .5, "beat": 0.0, "pulse": 0.0,
             "section": 0.0, "beat_confidence": .6, "gobo": 2, "energy_slow": .41},
    "chorus": {"energy": .87, "drive": .95, "punch": .92, "tempo": .71, "activity": .85, "activity_slow": .85,
               "level": .9, "level_slow": .9, "motion": .75, "sat": .85, "warmth": .85, "pan_slow": .6,
               "tilt_slow": .4, "zoom_slow": None, "pan": .6, "tilt": .4, "zoom": .5, "beat": 0.0, "pulse": 0.0,
               "section": 0.0, "beat_confidence": 1.0, "gobo": 3, "energy_slow": .87},
    "peak": {"energy": .96, "drive": .99, "punch": 1.0, "tempo": .80, "activity": .95, "activity_slow": .95,
             "level": 1.0, "level_slow": 1.0, "motion": .9, "sat": .8, "warmth": .7, "pan_slow": .7,
             "tilt_slow": .35, "zoom_slow": None, "pan": .7, "tilt": .35, "zoom": .5, "beat": 0.0, "pulse": 0.0,
             "section": 0.0, "beat_confidence": 1.0, "gobo": 4, "energy_slow": .96},
}


def schema_hash(props: dict) -> str:
    rows = sorted((k, p.get("type"), p.get("minimum"), p.get("maximum"), tuple(p.get("enum") or ()),
                   p.get("x-tier"), str(p.get("x-bindable")), bool(p.get("x-glsl"))) for k, p in props.items())
    return hashlib.sha1(repr(rows).encode()).hexdigest()[:8]


def modes_for(name: str, prop: dict, kind: str) -> list[str]:
    """The modes a param may take (binding.parse_v2's tier contract), for the editor."""
    if binding.not_mappable(name, prop):
        return []
    if kind == "transition":
        return ["cue"]
    typ, tier, bindable = prop.get("type"), prop.get("x-tier"), bool(prop.get("x-bindable"))
    if binding.is_palette_param(name, prop):
        return ["live", "cue"]
    if typ not in ("number", "integer") or "enum" in prop:
        return ["cue"] if typ in ("boolean", "string", "integer") or "enum" in prop else []
    if typ == "integer":
        return ["live", "cue"] if bindable else ["cue"]
    if tier == "structural":
        return ["cue"]
    return ["live", "section", "cue"]


def preview(props: dict, bindings: dict, params: dict, key: str = "", period: Optional[float] = None) -> dict:
    """{param: {state: value}} at the canonical states (between beats; "+beat" values where a kick acts)."""
    out: dict = {}
    for p, b in bindings.items():
        if p not in props or not binding.is_v2(b) or "colour" in b or not b.get("on", True):
            continue
        try:
            bd = binding.Bound(p, props[p], b, key)
        except Exception as e:
            out[p] = {"error": str(e)}
            continue
        base = params.get(p, props[p].get("default"))
        row = {}
        for st, sig in CANON.items():
            v = bd.value(sig, None, base, 1.0)
            if bd.quant == "beat" and period and isinstance(v, (int, float)):
                v = max(1, round(v / period)) * period
            row[st] = round(v, 4) if isinstance(v, float) else v
            if bd.kick is not None and sig:
                kv = bd.value({**sig, bd.kick[0]: 1.0}, None, base, 1.0)
                row[st + "+beat"] = round(kv, 4) if isinstance(kv, float) else kv
        out[p] = row
    return out


class Mappings:
    """The two files, re-read when they change (keeping the last good copy on a parse error)."""

    def __init__(self, config_dir: Any):
        self.dir = pathlib.Path(config_dir)
        self.lock = threading.Lock()
        self.gen: dict = {"format": 1, "items": {}}
        self.user: dict = {"format": 1, "items": {}}
        self.errors: list[str] = []
        self.rev = 0
        self._mt: dict[str, Any] = {}
        self._checked = 0.0
        self.reload_if_changed(force=True)

    # -- files ---------------------------------------------------------------------------------------------
    def _read(self, name: str) -> Optional[dict]:
        path = self.dir / name
        if not path.exists():
            return {"format": 1, "items": {}}
        try:
            d = json.loads(path.read_text())
            if not isinstance(d, dict) or not isinstance(d.get("items", {}), dict):
                raise ValueError("needs {\"items\": {key: record}}")
            d.setdefault("items", {})
            return d
        except (OSError, ValueError) as e:
            log.error("mappings: %s unreadable (%s): keeping the last good copy", path, e)
            self.errors = [f"{name}: {e}"]
            return None

    def reload_if_changed(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._checked < 1.0:
            return False
        self._checked = now
        changed = False
        for name, attr in ((GEN_FILE, "gen"), (USER_FILE, "user")):
            try:
                mt = (self.dir / name).stat().st_mtime_ns
            except OSError:
                mt = None
            if not force and mt == self._mt.get(name):
                continue
            self._mt[name] = mt
            d = self._read(name)
            if d is not None:
                setattr(self, attr, d)
                changed = True
        if changed:
            self.rev += 1
            self.errors = [e for e in self.errors if not e.startswith(GEN_FILE) and not e.startswith(USER_FILE)] \
                if all(self._read(n) is not None for n in (GEN_FILE, USER_FILE)) else self.errors
        return changed

    def _write_user(self) -> None:
        path = self.dir / USER_FILE
        tmp = path.with_suffix(".tmp")
        items = dict(sorted(self.user.get("items", {}).items()))
        tmp.write_text(json.dumps({"format": 1, "items": items}, indent=1, sort_keys=True) + "\n")
        tmp.replace(path)
        self._mt[USER_FILE] = path.stat().st_mtime_ns
        self.rev += 1

    # -- layering (presets.py) -----------------------------------------------------------------------------
    def records(self, key: str) -> tuple[dict, dict]:
        return (self.gen.get("items", {}).get(key) or {}), (self.user.get("items", {}).get(key) or {})

    def layers(self, kind: str, plugin: str, preset: Optional[str], props: dict
               ) -> tuple[bool, dict, dict]:
        """-> (follow, generated layer, the owner's layer) for one item and preset: {param: binding | None}.
        Entries that do not validate against the plug-in's params are left out (and listed by GET)."""
        key = f"{kind}/{plugin}"
        gen, user = self.records(key)
        follow = user.get("follow", gen.get("follow", True)) is not False
        out = []
        for rec in (gen, user):
            layer: dict = {}
            for src in (rec.get("bindings") or {}, ((rec.get("presets") or {}).get(preset) or {}) if preset else {}):
                for p, b in src.items():
                    if p not in props:
                        continue
                    if b is None:
                        layer[p] = None
                        continue
                    try:
                        layer[p] = binding.parse_v2(p, props[p], b, kind=kind)
                    except ParamError:
                        continue
            out.append(layer)
        return follow, out[0], out[1]

    # -- the API (server.py) -------------------------------------------------------------------------------
    def problems(self, kind: str, plugin: str, props: dict) -> list[dict]:
        key = f"{kind}/{plugin}"
        out = []
        for who, rec in zip(("generated", "user"), self.records(key)):
            secs = [("", rec.get("bindings") or {})] + [(f"presets.{n}.", s or {}) for n, s in
                                                        (rec.get("presets") or {}).items()]
            for pre, src in secs:
                for p, b in src.items():
                    if b is None:
                        continue
                    if p not in props:
                        out.append({"path": f"{who}.{pre}{p}", "code": "unknown_param", "detail": "stale"})
                        continue
                    try:
                        binding.parse_v2(p, props[p], b, kind=kind)
                    except binding.MappingError as e:
                        out += [{**x, "path": f"{who}.{pre}{x['path']}"} for x in e.problems]
                    except ParamError as e:
                        out.append({"path": f"{who}.{pre}{p}", "code": e.code, "detail": e.detail})
        return out

    def validate_record(self, kind: str, props: dict, body: dict) -> dict:
        """A user record from PUT / PATCH: {follow?, bindings?: {p: v2 | null}, presets?: {name: {p: ...}}}."""
        problems: list[dict] = []
        out: dict = {}
        if "follow" in body:
            if not isinstance(body["follow"], bool):
                problems.append({"path": "follow", "code": "unknown_key", "detail": "follow must be true or false"})
            else:
                out["follow"] = body["follow"]

        def section(src: Any, pre: str) -> dict:
            if not isinstance(src, dict):
                problems.append({"path": pre or "bindings", "code": "unknown_key", "detail": "must be an object"})
                return {}
            sec: dict = {}
            for p, b in src.items():
                if p not in props:
                    problems.append({"path": pre + p, "code": "unknown_param", "detail": f"no param {p!r}"})
                    continue
                if b is None:
                    sec[p] = None
                    continue
                try:
                    sec[p] = binding.parse_v2(p, props[p], b, kind=kind)
                except binding.MappingError as e:
                    problems.extend({**x, "path": pre + x["path"]} for x in e.problems)
            n_live = sum(1 for b in sec.values() if b and b.get("at") != "cue")
            if n_live > MAX_LIVE:
                problems.append({"path": pre or "bindings", "code": "too_many_bindings",
                                 "detail": f"at most {MAX_LIVE} live / section mappings per item"})
            return sec

        if "bindings" in body:
            out["bindings"] = section(body["bindings"], "")
        if "presets" in body:
            pr = body["presets"]
            if not isinstance(pr, dict):
                problems.append({"path": "presets", "code": "unknown_key", "detail": "must be an object"})
            else:
                out["presets"] = {str(n): section(s, f"presets.{n}.") for n, s in pr.items()}
        unknown = set(body) - {"follow", "bindings", "presets", "base_rev", "preset"}
        if unknown:
            problems.append({"path": "", "code": "unknown_key", "detail": f"unknown fields {sorted(unknown)}"})
        if problems:
            raise binding.MappingError(problems)
        return out

    def put_user(self, key: str, rec: dict, base_rev: Any, *, merge: bool, preset: Optional[str] = None) -> dict:
        from .sessions import SessionError
        with self.lock:
            self.reload_if_changed(force=True)
            items = self.user.setdefault("items", {})
            cur = items.get(key) or {}
            if base_rev is not None and base_rev != cur.get("rev", 0):
                raise SessionError(409, "stale_base", f"{key} is at rev {cur.get('rev', 0)}, not {base_rev}")
            if merge:
                new = copy.deepcopy(cur)
                if "follow" in rec:
                    new["follow"] = rec["follow"]
                for p, b in (rec.get("bindings") or {}).items():
                    tgt = new.setdefault("presets", {}).setdefault(preset, {}) if preset else \
                        new.setdefault("bindings", {})
                    tgt[p] = b
                for n, sec in (rec.get("presets") or {}).items():
                    new.setdefault("presets", {}).setdefault(n, {}).update(sec)
            else:
                new = {k: v for k, v in rec.items()}
            new["rev"] = cur.get("rev", 0) + 1
            new["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            items[key] = new
            self._write_user()
            return new

    def drop_default(self, key: str, preset: Optional[str], params: list[str]) -> None:
        """PATCH {p: "default"}: forget the owner's override for these params."""
        with self.lock:
            rec = (self.user.get("items") or {}).get(key)
            if not rec:
                return
            tgt = ((rec.get("presets") or {}).get(preset) if preset else rec.get("bindings")) or {}
            for p in params:
                tgt.pop(p, None)
            self._write_user()

    def delete_user(self, key: str, param: Optional[str] = None, preset: Optional[str] = None) -> bool:
        with self.lock:
            self.reload_if_changed(force=True)
            items = self.user.setdefault("items", {})
            rec = items.get(key)
            if rec is None:
                return False
            if param is None and preset is None:
                items.pop(key)
            elif preset is not None and param is None:
                (rec.get("presets") or {}).pop(preset, None)
            else:
                tgt = ((rec.get("presets") or {}).get(preset) if preset else rec.get("bindings")) or {}
                tgt.pop(param, None)
            self._write_user()
            return True
