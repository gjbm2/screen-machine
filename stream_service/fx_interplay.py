"""Scenes and effects together (docs/SCENE_FX_INTERPLAY.md): gentle multipliers on the FX director's pick weight and a
ceiling on an effect's level, from:
  * what the effect does to the picture: its role (config/live/effect_roles.generated.json: grade, light, texture,
    reshape, trails, corrupt, reframe, camera, add; fx_interplay.yaml role_overrides win) and how strongly it
    changes the picture (look_stats.generated.json effects.<preset>.change_pct, measured);
  * what the scene on screen is like (look_stats.generated.json scenes.<preset>: measured busy / motion /
    brightness rank, symmetry, palette-driven; fx_interplay.yaml scene_props: plug-ins whose content must stay
    legible, that already trail or already glitch);
  * the other effects already on the bus (pairs);
  * the music: which roles suit its genre (roles_by_music, p-weighted over direction.genres / genre) and how much
    the effects may take over (load: a budget from direction.energy plus a genre bias, part of it used by the
    scene's busyness and by the other effects' strength).
Every factor stays within multiplier_range and a missing number means no opinion (1): nothing is ever excluded, a
poor fit is just less likely or plays at a lower level.  All the numbers are in config/live/fx_interplay.yaml,
hot-reloaded (checked at most once a second) like the other config/live files.

Pure arithmetic and file reads, no GPU: the FX director calls it on the loop thread.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

log = logging.getLogger("stream_service.fx_interplay")

CONFIG_FILE = "fx_interplay.yaml"
ROLES_FILE = "effect_roles.generated.json"
STATS_FILE = "look_stats.generated.json"
ROLES = ("grade", "light", "texture", "reshape", "trails", "corrupt", "reframe", "camera", "add")
SCENE_PROPS = ("busy", "motion", "brightness", "symmetric", "self_coloured", "legible", "own_trails", "own_glitch")
CHECK_S = 1.0
DEFAULTS: dict = {"level_min": 0.35, "multiplier_range": [0.25, 1.5], "tag_prior": 1.0, "refit_below": 0.5,
                  "symmetric_from": [0.3, 0.9], "scene_props": {}, "roles": {}, "pairs": {}, "role_overrides": {},
                  "load": {}, "genre_groups": {}, "roles_by_music": {}}


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _lerp_pair(spec: Any, x: float) -> Optional[float]:
    """[at 0, at 1] or [at 0, at 0.5, at 1] -> the value at x (linear between); a plain number -> itself."""
    if isinstance(spec, (list, tuple)) and len(spec) in (2, 3) and all(_num(v) is not None for v in spec):
        xs = (0.0, 1.0) if len(spec) == 2 else (0.0, 0.5, 1.0)
        return _piecewise(list(zip(xs, spec)), _c01(x))
    return _num(spec)


def _piecewise(points: Any, x: float) -> Optional[float]:
    """[[x0, y0], [x1, y1], ...] (x ascending) -> y at x, held flat beyond the ends."""
    pts = [(float(a), float(b)) for a, b in (points or []) if _num(a) is not None and _num(b) is not None]
    if not pts:
        return None
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / max(1e-9, x1 - x0)
    return pts[-1][1]


@dataclass
class Fit:
    mult: float = 1.0                     # x the pick weight
    level: float = 1.0                    # the slot's level ceiling (x amount)
    role: Optional[str] = None
    parts: dict = field(default_factory=dict)
    capped_by: Optional[str] = None       # "budget" | "scene" | None

    def why(self) -> str:
        bits = [self.role or "role ?"]
        bits += [f"{k} x{v:.2f}" for k, v in self.parts.items() if abs(v - 1.0) >= 0.05]
        if self.level < 0.999:
            bits.append(f"level {self.level:.2f} ({self.capped_by})")
        return ", ".join(bits)


class Interplay:
    """The three files of one config dir, re-read when they change."""

    def __init__(self, config_dir: pathlib.Path):
        self.dir = pathlib.Path(config_dir)
        self.cfg: dict = dict(DEFAULTS)
        self.roles: dict[str, dict] = {}
        self.scenes: dict[str, dict] = {}
        self.effects: dict[str, dict] = {}
        self.errors: list[str] = []
        self._mt: dict[str, Optional[int]] = {}
        self._checked = -math.inf
        self.reload_if_changed(force=True)

    # -- files ------------------------------------------------------------------------------------------------------
    def _mtime(self, name: str) -> Optional[int]:
        try:
            return (self.dir / name).stat().st_mtime_ns
        except OSError:
            return None

    def reload_if_changed(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._checked < CHECK_S:
            return False
        self._checked = now
        changed = False
        for name in (CONFIG_FILE, ROLES_FILE, STATS_FILE):
            mt = self._mtime(name)
            if mt == self._mt.get(name, -1) and not force:
                continue
            self._mt[name] = mt
            changed = True
            try:
                if name == CONFIG_FILE:
                    raw = yaml.safe_load((self.dir / name).read_text()) if mt is not None else {}
                    if raw is not None and not isinstance(raw, dict):
                        raise ValueError("top level must be a mapping")
                    self.cfg = {**DEFAULTS, **(raw or {})}
                elif name == ROLES_FILE:
                    raw = json.loads((self.dir / name).read_text()) if mt is not None else {}
                    self.roles = dict((raw or {}).get("roles") or {})
                else:
                    raw = json.loads((self.dir / name).read_text()) if mt is not None else {}
                    self.scenes = {k: v for k, v in ((raw or {}).get("scenes") or {}).items() if "error" not in v}
                    self.effects = {k: v for k, v in ((raw or {}).get("effects") or {}).items() if "error" not in v}
                self.errors = [e for e in self.errors if not e.startswith(name)]
            except (OSError, ValueError, yaml.YAMLError) as e:
                log.error("cannot read %s: %s (keeping the previous one)", self.dir / name, e)
                self.errors.append(f"{name}: {e}")
        if changed:
            log.info("interplay: %d effect roles, %d scene and %d effect measurements", len(self.roles),
                     len(self.scenes), len(self.effects))
        return changed

    # -- facts --------------------------------------------------------------------------------------------------------
    def role(self, plugin: Optional[str]) -> Optional[str]:
        ov = (self.cfg.get("role_overrides") or {}).get(plugin or "")
        if ov in ROLES:
            return ov
        r = (self.roles.get(plugin or "") or {}).get("role")
        return r if r in ROLES else None

    def scene_props(self, name: Optional[str], entry: Optional[dict]) -> dict[str, float]:
        """The scene's properties, 0..1 (absent = unknown)."""
        out: dict[str, float] = {}
        m = self.scenes.get(name or "") or {}
        for k, src in (("busy", "busy_pct"), ("motion", "motion_pct"), ("brightness", "brightness_pct")):
            v = _num(m.get(src))
            if v is not None:
                out[k] = _c01(v)
        sym = _num(m.get("symmetric"))
        if sym is not None:
            lo, hi = self.cfg.get("symmetric_from") or DEFAULTS["symmetric_from"]
            out["symmetric"] = _c01((sym - float(lo)) / max(1e-6, float(hi) - float(lo)))
        if isinstance(m.get("palette_driven"), bool):
            out["self_coloured"] = 0.0 if m["palette_driven"] else 1.0
        plugin = (entry or {}).get("source") or m.get("plugin")
        sp = self.cfg.get("scene_props") or {}
        for k in ("legible", "own_trails", "own_glitch"):
            out[k] = 1.0 if plugin in (sp.get(k) or []) else 0.0
        return out

    def strength(self, preset: Optional[str]) -> Optional[float]:
        return _num((self.effects.get(preset or "") or {}).get("change_pct"))

    def groups(self, direction: dict) -> dict[str, float]:
        """direction.genres {word: p} (else its genre word) -> genre groups, p-weighted (a word in two groups splits)."""
        g = direction.get("genres") or ({direction["genre"]: 1.0} if direction.get("genre") else {})
        table = self.cfg.get("genre_groups") or {}
        out: dict[str, float] = {}
        for w, p in (g or {}).items():
            p = _num(p) or 0.0
            hit = [k for k, ws in table.items() if w in (ws or [])]
            for k in hit:
                out[k] = out.get(k, 0.0) + p / len(hit)
        return out

    # -- the fit ------------------------------------------------------------------------------------------------------
    def _clamp(self, x: float) -> float:
        lo, hi = self.cfg.get("multiplier_range") or DEFAULTS["multiplier_range"]
        return min(float(hi), max(float(lo), x))

    def context(self, scene: tuple[Optional[str], Optional[dict]], others: list[tuple[Optional[str], Optional[str]]],
                direction: dict) -> "Ctx":
        """Everything that doesn't depend on the candidate, once per pick: ``scene`` (name, entry) on screen,
        ``others`` (preset, plug-in) of the other effects on the bus, ``direction`` the session's effective one."""
        self.reload_if_changed()
        cfg = self.cfg
        props = self.scene_props(*scene)
        groups = self.groups(direction)
        tot = sum(groups.values())
        other_roles = [self.role(pl) for _, pl in others]
        pairs = cfg.get("pairs") or {}
        rows = cfg.get("roles_by_music") or {}
        per_role: dict[str, tuple[float, float, float]] = {}
        for role in ROLES:
            m_scene = 1.0
            for prop, spec in ((cfg.get("roles") or {}).get(role) or {}).items():
                if prop in props:
                    v = _lerp_pair(spec, props[prop])
                    if v is not None:
                        m_scene *= v
            m_pair = 1.0
            for r2 in other_roles:
                spec = pairs.get(f"{role}+{r2}", pairs.get(f"{r2}+{role}")) if r2 else None
                if isinstance(spec, dict):
                    v = (sum(p * float(spec.get(k, spec.get("default", 1.0))) for k, p in groups.items()) / tot
                         if tot > 0 else _num(spec.get("default")))
                else:
                    v = _num(spec)
                if v is not None:
                    m_pair *= v
            if tot > 0:
                m_music = sum(p * float((rows.get(k) or rows.get("default") or {}).get(role, 1.0))
                              for k, p in groups.items()) / tot
            else:
                m_music = float((rows.get("default") or {}).get(role, 1.0))
            per_role[role] = (self._clamp(m_scene), self._clamp(m_pair), self._clamp(m_music))
        load = cfg.get("load") or {}
        e = _num(direction.get("energy"))
        budget = _piecewise(load.get("by_energy"), e if e is not None else 0.5)
        if budget is not None and tot > 0:
            bias = load.get("genre_bias") or {}
            budget += sum(p * float(bias.get(k, 0.0)) for k, p in groups.items()) / tot
        used = float(load.get("scene", 0.5)) * props.get("busy", 0.5)
        used += sum(self.strength(o) or 0.5 for o, _ in others)
        return Ctx(per_role, budget, used, float(cfg.get("level_min", DEFAULTS["level_min"])))

    def fit_in(self, ctx: "Ctx", preset: str, entry: dict) -> Fit:
        """One candidate effect against a pick's context."""
        role = self.role(entry.get("effect"))
        f = Fit(role=role)
        if role is None:
            return f
        m_scene, m_pair, m_music = ctx.per_role[role]
        f.parts = {"scene": m_scene, "pair": m_pair, "music": m_music}
        f.mult = m_scene * m_pair * m_music
        lv, by = 1.0, None
        strength = self.strength(preset)
        if ctx.budget is not None and strength is not None:
            room = (ctx.budget - ctx.used) / max(0.1, strength)
            if room < 1.0:
                lv, by = room, "budget"
        if m_scene < lv:
            lv, by = m_scene, "scene"
        f.level = round(max(ctx.level_min, min(1.0, lv)), 3)
        f.capped_by = by if f.level < 0.999 else None
        return f

    def fit(self, preset: str, entry: dict, scene: tuple[Optional[str], Optional[dict]],
            others: list[tuple[Optional[str], Optional[str]]], direction: dict) -> Fit:
        """One candidate on its own (context + fit_in)."""
        return self.fit_in(self.context(scene, others, direction), preset, entry)


@dataclass
class Ctx:
    per_role: dict          # role -> (scene, pair, music) multipliers
    budget: Optional[float]
    used: float
    level_min: float


_SHARED: dict[str, Interplay] = {}


def shared(config_dir: pathlib.Path) -> Interplay:
    """One Interplay per config dir (all sessions read the same files)."""
    k = str(pathlib.Path(config_dir).resolve())
    if k not in _SHARED:
        _SHARED[k] = Interplay(pathlib.Path(config_dir))
    return _SHARED[k]
