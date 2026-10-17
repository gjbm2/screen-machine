"""Scene, transition and effect presets (config/live/*.yaml + plug-in presets, hot-reloaded) and spec resolution
(LIVE_V1 §1.1).

Spec forms accepted wherever a scene, transition or effect is named:
  "fractal-zoom"                                         a preset name
  {"preset": "fractal-zoom", "params": {...overrides}}
  {"source": "fractal_zoom", "params": {...}}            scene
  {"transition": "crossfade", "params": {...}}           transition
  {"effect": "chroma", "params": {...}, "bindings"?: {...}}   effect (EFFECTS_LAYER_DESIGN D.1)
Unknown parameters -> ParamError (400); out-of-range numbers are clamped.

Scene params (preset or override) may hold bindings instead of values (modulation.py); they are split off into
``SceneSpec.bindings`` and the params keep plain values.  A scene spec (or a scene preset) may carry
``effects``: a list of up to 4 effect specs attached to the scene (they transition with it); a spec's list
replaces the preset's (``[]`` removes it).  When a spec names a bare plug-in (or a preset that does not set a
param), the class's ``default_bindings`` apply (effects: ``pulse <- beat``).
A preset may carry ``tags`` (tags.py): the director picks by them.  Invalid tags are dropped with a log line,
and tag words outside config/live/vocab.yaml are listed in ``errors``; the preset itself stays usable by name.

Every registered plug-in is usable: its own ``plugins/<kind>/<id>.presets.yaml`` presets (marked ``auto``), or
an automatic preset with its defaults when neither that file nor config/live names it.  File presets
(config/live) win a name clash.  ``Presets(..., plugin_presets=False)`` loads config/live only (tests).  The set
is rebuilt when a file changes or the registry's ``rev`` moves (live plug-in discovery).
"""
from __future__ import annotations

import logging
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

from . import binding as binding_v2
from .mappings import Mappings
from .music_feed import ResponseFile
from .modulation import parse_binding, split_bindings
from .piano import PIANO_SIGNALS
from .plugins import Registry
from .plugins.base import ParamError, is_bindable
from .tag_overlay import TagOverlay
from .tags import TagError, Vocab, unknown_words, validate_tags

log = logging.getLogger("stream_service.presets")
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)     # libyaml: ~20x faster over ~1000 preset files


def _yaml(text: str):
    return yaml.load(text, Loader=_Loader)

SCENES_FILE = "scenes.yaml"
TRANSITIONS_FILE = "transitions.yaml"
EFFECTS_FILE = "effects.yaml"
MAX_SCENE_EFFECTS = 4
KINDS = ("scene", "transition", "effect")
KIND_KEY = {"scene": "source", "transition": "transition", "effect": "effect"}
KIND_DIR = {"scene": "sources", "transition": "transitions", "effect": "effects"}
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parent / "plugins"


@dataclass(frozen=True)
class EffectSpec:
    effect: str
    params: dict = field(hash=False)       # full, validated, plain values only
    preset: Optional[str] = None
    label: str = ""
    bindings: dict = field(default_factory=dict, hash=False)

    @property
    def name(self) -> str:
        return self.preset or self.effect

    def as_spec(self) -> dict:
        """A spec that resolves back to this one (params with bindings inline)."""
        params = {**{k: v for k, v in self.params.items() if k not in self.bindings}, **self.bindings}
        return {"preset": self.preset, "params": params} if self.preset else {"effect": self.effect, "params": params}


@dataclass(frozen=True)
class SceneSpec:
    source: str
    params: dict = field(hash=False)       # full, validated (defaults filled in); plain values only
    preset: Optional[str] = None
    label: str = ""
    bindings: dict = field(default_factory=dict, hash=False)   # param -> binding (modulation.py)
    effects: tuple = field(default=(), hash=False)            # EffectSpec, attached to the scene

    @property
    def name(self) -> str:
        return self.preset or self.source


@dataclass(frozen=True)
class TransitionSpec:
    transition: str
    params: dict = field(hash=False)
    preset: Optional[str] = None
    label: str = ""
    bindings: dict = field(default_factory=dict, hash=False)   # cue-mode music mappings (sampled at prepare)

    @property
    def name(self) -> str:
        return self.preset or self.transition


def default_bindings_for(cls: type, props: dict, set_keys: set) -> dict:
    """The class's default bindings for params a spec did not set (as a value or a binding)."""
    out = {}
    for key, b in (getattr(cls, "default_bindings", None) or {}).items():
        prop = props.get(key)
        if key in set_keys or prop is None or not is_bindable(prop):
            continue
        try:
            out[key] = parse_binding(key, prop, b)
        except ParamError as e:
            log.warning("%s: default binding %s ignored: %s", getattr(cls, "name", cls), key, e.detail)
    return out


class Presets:
    # (kind, plug-in) -> (cls, presets-file mtime, named by config/live, entries, errors); shared by instances
    # (entries are never edited after they are built)
    _pcache: dict[tuple[str, str], tuple] = {}

    def __init__(self, config_dir: pathlib.Path, registry: Registry, *, plugin_presets: bool = True):
        self.dir = pathlib.Path(config_dir)
        self.registry = registry
        self.plugin_presets = plugin_presets
        self.scenes: dict[str, dict] = {}        # name -> {label, source, params (as declared), tags?, effects?}
        self.transitions: dict[str, dict] = {}   # name -> {label, transition, params, tags?}
        self.effects: dict[str, dict] = {}       # name -> {label, effect, params, tags?, stage}
        self._file: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self._mtimes: dict[str, Optional[float]] = {}
        self._plugin_mtimes: tuple = ()
        self._reg_rev = -1
        self._reload_lock = threading.Lock()
        self.errors: list[str] = []
        self.tag_errors: dict[str, list[str]] = {k: [] for k in KINDS}
        self.plugin_errors: list[str] = []
        self.rev = 0
        self.vocab = Vocab(self.dir)
        self.maps = Mappings(self.dir)           # per-item music mappings (mappings.py)
        self.music_response = ResponseFile(self.dir)   # how hard the room's sound drives them (music_feed.py)
        self.overlay = TagOverlay(self.dir)       # reviewed tags over the plug-ins' own (tag_overlay.py)
        self.overlay_errors: list[str] = []
        self.reload_if_changed(force=True)

    # -- loading ------------------------------------------------------------
    def _mtime(self, name: str) -> Optional[float]:
        try:
            return (self.dir / name).stat().st_mtime_ns
        except OSError:
            return None

    def reload_if_changed(self, force: bool = False, plugin_files: bool = False, wait: bool = False) -> bool:
        """Cheap (called per request and every second): config/live files, the vocab and the registry's rev.
        ``plugin_files``: also look for changed ``<id>.presets.yaml`` files (the server's plug-in rescan, on a
        worker thread with ``wait``).  One reload at a time; a caller that does not wait skips while another
        reload runs (the tables it reads are swapped whole, never edited in place)."""
        if not self._reload_lock.acquire(blocking=wait or force):
            return False
        try:
            return self._reload(force, plugin_files)
        finally:
            self._reload_lock.release()

    def _reload(self, force: bool, plugin_files: bool) -> bool:
        self.maps.reload_if_changed(force)        # its own rev: the preset tables do not depend on it
        self.music_response.reload_if_changed(force)   # read where it is used (MusicFeed.response)
        changed = self.vocab.reload_if_changed()
        if self.overlay.reload_if_changed(force):
            changed = True
        sig = self._plugin_sig() if plugin_files or force else (self.registry.rev,) + self._plugin_mtimes[1:]
        plugins_moved = sig != self._plugin_mtimes
        reload_files = force or changed or self.registry.rev != self._reg_rev   # file presets name plug-ins
        for fname, kind in ((EFFECTS_FILE, "effect"), (SCENES_FILE, "scene"), (TRANSITIONS_FILE, "transition")):
            mt = self._mtime(fname)
            if not reload_files and mt == self._mtimes.get(fname):
                continue
            self._mtimes[fname] = mt
            loaded = self._load(fname, kind)
            if loaded is None:
                continue  # parse error: keep the last good set
            if loaded != self._file[kind]:
                changed = True
                log.info("loaded %d %s presets from %s", len(loaded), kind, self.dir / fname)
            self._file[kind] = loaded
        self._reg_rev = self.registry.rev
        if changed or force or plugins_moved:
            self._plugin_mtimes = sig
            self._rebuild()
            changed = True
        return changed

    def _rebuild(self) -> None:
        merged, errs, probs = {}, [], []
        for kind in KINDS:
            out = self._plugin_entries(kind, errs) if self.plugin_presets else {}
            out.update(self._file[kind])           # a config/live preset wins a name clash
            merged[kind] = {n: self._overlaid(kind, n, e, n in self._file[kind], probs) for n, e in out.items()}
        self.scenes, self.transitions, self.effects = merged["scene"], merged["transition"], merged["effect"]
        self.plugin_errors = errs
        self.overlay_errors = self.overlay.errors + probs
        self.rev += 1

    def _overlaid(self, kind: str, name: str, entry: dict, authored: bool, probs: list[str]) -> dict:
        """A preset with the reviewed tags layered on (tag_overlay.py; a config/live preset's own fields win)."""
        tags, p = self.overlay.apply(kind, name, entry.get(KIND_KEY[kind]), entry.get("tags"), self.vocab.words,
                                     authored)
        probs += p
        return entry if tags is entry.get("tags") else {**entry, "tags": tags}

    # -- plug-in presets: <id>.presets.yaml next to the plug-in, else an automatic one ----------------------
    _SIG_MAP = {"energy": "activity_slow", "activity": "activity", "beat": "beat", "pulse": "pulse", "level": "level",
                "pan": "pan_slow", "tilt": "tilt_slow", "zoom": "zoom", "motion": "motion", "drive": "drive",
                "punch": "punch", "tempo": "tempo", "section": "section",
                **{s: s for s in PIANO_SIGNALS}}           # note_on, note_rate, velocity, ... (piano.py)

    @staticmethod
    def _plugin_files() -> list[pathlib.Path]:
        out: list[pathlib.Path] = []
        for d in KIND_DIR.values():
            out += sorted((PLUGIN_ROOT / d).glob("*.presets.yaml"))
        return out

    def _plugin_sig(self) -> tuple:
        if not self.plugin_presets:
            return (self.registry.rev,)
        out = []
        for f in self._plugin_files():
            try:
                out.append((f.name, f.stat().st_mtime_ns))
            except OSError:
                pass
        return (self.registry.rev, hash(tuple(out)))

    def _binding(self, prop: dict, value, b: dict):
        """Plug-in preset binding ({bind: energy} / {kick: beat, amount: a}) -> engine form {bind, range}."""
        lo, hi = prop.get("minimum", 0.0), prop.get("maximum", 1.0)
        sig = b.get("kick") if "kick" in b else b.get("bind")
        if sig not in self._SIG_MAP:
            raise ParamError("bad_param", f"unknown signal {sig!r} (known: {sorted(self._SIG_MAP)})")
        if "kick" in b:
            base = value if isinstance(value, (int, float)) else prop.get("default", lo)
            return {"bind": self._SIG_MAP[sig], "range": [base, min(hi, base + float(b.get("amount", 1.0)) * (hi - lo))]}
        return {"bind": self._SIG_MAP[sig], "range": list(b.get("range", [lo, hi]))}

    @staticmethod
    def _generic(b: dict) -> bool:
        """A plug-in preset's catch-all binding ({bind: energy} over the whole range, {kick: beat, amount: 1}): the
        per-item mappings replace it."""
        return ("bind" in b and "range" not in b) or ("kick" in b and float(b.get("amount", 1.0)) == 1.0)

    @staticmethod
    def _auto_tags(tags: dict, kind: str, cls: type) -> dict:
        t = dict(tags or {})
        if kind == "transition" and "style" in t and "transition_style" not in t:
            s = t.pop("style")
            t["transition_style"] = s if isinstance(s, list) else [s]
        if kind == "scene":
            t.pop("style", None)
        if kind == "effect":
            t.setdefault("family", getattr(cls, "stage", "stylise"))
        t.setdefault("energy", [0.0, 1.0])
        t.setdefault("weight", 0.5)
        return t

    def _plugin_entries(self, kind: str, errors: list) -> dict[str, dict]:
        """Every plug-in's presets of one kind.  Cached per plug-in by (class, presets-file mtime), so a rebuild
        after a rescan only re-validates what changed (~1000 plug-ins)."""
        reg = self.registry.table({"scene": "source"}.get(kind, kind))
        key = KIND_KEY[kind]
        have = {v.get(key) for v in self._file[kind].values()}
        out: dict[str, dict] = {}
        for name in sorted(reg):
            cls = reg[name]
            if getattr(cls, "internal", False):
                continue
            path = PLUGIN_ROOT / KIND_DIR[kind] / f"{name}.presets.yaml"
            try:
                ymt = path.stat().st_mtime_ns
            except OSError:
                ymt = None
            named = name in have
            hit = self._pcache.get((kind, name))
            if hit is not None and hit[0] is cls and hit[1] == ymt and hit[2] == named:
                entries, errs = hit[3], hit[4]
            else:
                entries, errs = self._one_plugin(kind, name, cls, path if ymt is not None else None, named)
                self._pcache[(kind, name)] = (cls, ymt, named, entries, errs)
            errors += errs
            for pname, entry in entries.items():
                if pname not in out and pname not in self._file[kind]:
                    out[pname] = entry
        return out

    def _one_plugin(self, kind: str, name: str, cls: type, path: Optional[pathlib.Path],
                    named: bool) -> tuple[dict[str, dict], list[str]]:
        key = KIND_KEY[kind]
        props = cls.param_props()
        errs: list[str] = []
        entries: Any = {}
        if path is not None:
            try:
                entries = _yaml(path.read_text()) or {}
                if not isinstance(entries, dict):
                    raise ValueError("top level must be a mapping")
            except (OSError, yaml.YAMLError, ValueError) as e:
                log.warning("plug-in presets %s unreadable: %s", path, e)
                errs.append(f"{path.name}: unreadable: {e}")
                entries = {}
        if not entries and not named:   # no preset anywhere: an automatic one with the defaults
            auto: dict = {"label": cls.label or name, key: name, "params": {}}
            if kind != "transition":
                auto["bindings"] = {p: b for p, b in (("energy", {"bind": "energy"}),
                                                      ("pulse", {"kick": "beat", "amount": 1.0}))
                                    if p in props and props[p].get("x-bindable")}
            auto["tags"] = {"energy": [0.0, 1.0], "weight": 0.5,
                            **({"transition_style": ["lively"]} if kind == "transition" else {})}
            entries = {name.replace("_", "-"): auto}
        out: dict[str, dict] = {}
        for pname, e in entries.items():
            pname = str(pname)
            if not isinstance(e, dict):
                continue
            try:
                params = dict(e.get("params") or {})
                generic = []
                for k, b in (e.get("bindings") or {}).items():
                    if k in props and isinstance(b, dict):
                        try:
                            params[k] = self._binding(props[k], params.get(k), b)
                        except ParamError as be:
                            errs.append(f"{kind} preset {pname}: binding {k} skipped: {be.detail}")
                            continue
                        if self._generic(b):
                            generic.append(k)
                if kind == "transition":
                    cls.validate(params)
                else:
                    cls.validate(split_bindings(props, params)[0])
                entry = {"label": str(e.get("label") or pname), key: name, "params": params, "auto": True,
                         **({"generic": generic} if generic else {})}
                if kind == "effect":
                    entry["stage"] = getattr(cls, "stage", "stylise")
                try:
                    entry["tags"] = validate_tags(self._auto_tags(e.get("tags"), kind, cls))
                except TagError as te:
                    log.info("%s plug-in preset %r: tags dropped: %s", kind, pname, te)
                out[pname] = entry
            except (ParamError, TypeError, ValueError) as err:
                log.warning("%s plug-in preset %r skipped: %s", kind, pname, getattr(err, "detail", err))
                errs.append(f"{kind} preset {pname}: skipped: {getattr(err, 'detail', err)}")
        return out, errs

    # -- config/live files ------------------------------------------------------
    def _validate_entry(self, kind: str, name: str, entry: Any) -> dict:
        key = KIND_KEY[kind]
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str):
            raise ParamError("bad_spec", f"needs a '{key}' name")
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            raise ParamError("bad_param", "params must be an object")
        out = {"label": str(entry.get("label") or name), key: entry[key], "params": params}
        if kind == "scene":
            cls = self.registry.source(entry[key])
            cls.validate(split_bindings(cls.param_props(), params)[0])
            if entry.get("effects") is not None:
                fx = entry["effects"]
                if not isinstance(fx, list):
                    raise ParamError("bad_spec", "effects must be a list of effect specs")
                if len(fx) > MAX_SCENE_EFFECTS:
                    raise ParamError("too_many_effects", f"at most {MAX_SCENE_EFFECTS} effects per scene")
                out["effects"] = list(fx)
        elif kind == "transition":
            self.registry.transition(entry[key]).validate(params)
        else:
            cls = self.registry.effect(entry[key])
            cls.validate(split_bindings(cls.param_props(), params)[0])
            out["stage"] = getattr(cls, "stage", "stylise")
        if isinstance(entry.get("description"), str):
            out["description"] = entry["description"]
        return out

    def _load(self, fname: str, kind: str) -> Optional[dict[str, dict]]:
        path = self.dir / fname
        if not path.exists():
            return {}
        try:
            raw = _yaml(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            log.error("cannot read %s: %s (keeping the previous presets)", path, e)
            self.errors.append(f"{fname}: {e}")
            return None
        if not isinstance(raw, dict):
            log.error("%s: top level must be a mapping of preset name -> preset", path)
            return None
        out: dict[str, dict] = {}
        tag_errors: list[str] = []
        for name, entry in raw.items():
            try:
                out[str(name)] = item = self._validate_entry(kind, str(name), entry)
                if entry.get("tags") is not None:
                    try:
                        item["tags"] = tags = validate_tags(entry["tags"])
                        bad = unknown_words(tags, self.vocab.words)
                        if bad:
                            log.warning("%s preset %r: tag words not in the vocab: %s", kind, name, bad)
                            tag_errors.append(f"{kind} preset {name}: tag words not in vocab.yaml: {bad}")
                    except TagError as e:
                        log.error("%s preset %r in %s: tags dropped: %s", kind, name, path, e)
                        tag_errors.append(f"{kind} preset {name}: tags dropped: {e}")
            except ParamError as e:
                log.error("%s preset %r in %s skipped: %s", kind, name, path, e.detail)
        self.tag_errors[kind] = tag_errors
        return out

    def file_plugins(self, kind: str) -> set[str]:
        """The plug-ins config/live presets of ``kind`` name (e.g. the transitions worth warming up)."""
        return {e.get(KIND_KEY[kind]) for e in self._file[kind].values()}

    # -- catalog ------------------------------------------------------------
    def catalog(self) -> dict:
        return {"scenes": {k: dict(v) for k, v in self.scenes.items()},
                "transitions": {k: dict(v) for k, v in self.transitions.items()},
                "effects": {k: dict(v) for k, v in self.effects.items()}}

    def catalog_errors(self) -> list[str]:
        ov = self.overlay_errors
        ov = ov[:40] + [f"... and {len(ov) - 40} more tag overlay problems"] if len(ov) > 40 else ov
        return self.tag_errors["scene"] + self.tag_errors["transition"] + self.tag_errors["effect"] + \
            list(self.plugin_errors) + list(ov)

    def scene_restore(self, spec: SceneSpec) -> tuple[dict, dict]:
        """(plain params, bindings) a scene's preset gives, for PATCH {k: null} (R13); ({}, {}) for a plug-in."""
        entry = self.scenes.get(spec.preset) if spec.preset else None
        if entry is None or entry["source"] != spec.source:
            return {}, {}
        cls = self.registry.source(spec.source)
        props = cls.param_props()
        plain, bindings = split_bindings(props, entry["params"])
        bindings = self._layer("scene", cls, props, spec.preset, entry, bindings,
                               default_bindings_for(cls, props, set(entry["params"])))
        return cls.validate(plain), bindings

    # -- the music mappings over a preset's own bindings (mappings.py) ----------------------------------------
    def _layer(self, kind: str, cls: type, props: dict, preset: Optional[str], entry: Optional[dict],
               authored: dict, defaults: dict) -> dict:
        """Class defaults + generic preset bindings < generated < authored < the owner's edits (the spec's own
        bindings go on top, in the caller).  A band map becomes a range from the preset's tags.energy."""
        follow, gen, user = self.maps.layers(kind, cls.name, preset, props)
        if not follow:
            return {}
        generic = set((entry or {}).get("generic") or ())
        out = dict(defaults)
        out.update({k: v for k, v in authored.items() if k in generic})
        for layer, then in ((gen, {k: v for k, v in authored.items() if k not in generic}), (user, None)):
            for k, v in layer.items():
                if v is None:
                    out.pop(k, None)
                else:
                    out[k] = v
            if then:
                out.update(then)
        tags = ((entry or {}).get("tags") or {}).get("energy") or [0.0, 1.0]
        for k, b in list(out.items()):
            m = b.get("map") if binding_v2.is_v2(b) else None
            if m and "band" in m:
                prop = props[k]
                lo_p, hi_p = prop.get("minimum", 0.0), prop.get("maximum", 1.0)
                pad = m["band"] * (hi_p - lo_p)
                lo, hi = max(lo_p, lo_p + tags[0] * (hi_p - lo_p) - pad), min(hi_p, lo_p + tags[1] * (hi_p - lo_p) + pad)
                out[k] = {**b, "map": {"range": [lo, hi]}}
        return out

    # -- resolution -----------------------------------------------------------
    @staticmethod
    def _split(spec: Any, key: str, extra_keys: tuple[str, ...] = ()) -> tuple[Optional[str], Optional[str], dict]:
        """-> (preset, plugin name, override params)."""
        if isinstance(spec, str):
            return spec, None, {}
        if not isinstance(spec, dict):
            raise ParamError("bad_spec", f"a spec must be a preset name or an object, not {type(spec).__name__}")
        extra = set(spec) - {"preset", key, "params", *extra_keys}
        if extra:
            raise ParamError("bad_spec", f"unknown spec keys {sorted(extra)}")
        preset, plugin = spec.get("preset"), spec.get(key)
        if (preset is None) == (plugin is None):
            raise ParamError("bad_spec", f"a spec needs exactly one of 'preset' or '{key}'")
        if not isinstance(preset or plugin, str):
            raise ParamError("bad_spec", "the preset / plug-in name must be a string")
        params = spec.get("params") or {}
        if not isinstance(params, dict):
            raise ParamError("bad_param", "params must be an object")
        return preset, plugin, params

    def resolve_scene(self, spec: Any) -> SceneSpec:
        preset, source, overrides = self._split(spec, "source", ("effects",))
        fx_given = isinstance(spec, dict) and "effects" in spec
        if preset is not None:
            entry = self.scenes.get(preset)
            if entry is None:
                raise ParamError("unknown_preset", f"unknown scene preset {preset!r} (known: {sorted(self.scenes)})")
            cls = self.registry.source(entry["source"])
            props = cls.param_props()
            plain, bindings = split_bindings(props, entry["params"])
            base = cls.validate(plain)
            bindings = self._layer("scene", cls, props, preset, entry, bindings,
                                   default_bindings_for(cls, props, set(entry["params"])))
            plain, bindings = split_bindings(props, overrides, bindings)
            fx = spec["effects"] if fx_given else entry.get("effects") or []
            return SceneSpec(cls.name, cls.validate(plain, base=base), preset, entry["label"], bindings,
                             self.resolve_effects(fx))
        cls = self.registry.source(source)
        props = cls.param_props()
        bindings = self._layer("scene", cls, props, None, None, {}, default_bindings_for(cls, props, set()))
        plain, bindings = split_bindings(props, overrides, bindings)
        fx = (spec.get("effects") or []) if fx_given else []
        return SceneSpec(cls.name, cls.validate(plain), None, cls.label or cls.name, bindings,
                         self.resolve_effects(fx))

    def resolve_effects(self, specs: Any) -> tuple:
        if specs is None:
            return ()
        if not isinstance(specs, list):
            raise ParamError("bad_spec", "effects must be a list of effect specs")
        if len(specs) > MAX_SCENE_EFFECTS:
            raise ParamError("too_many_effects", f"at most {MAX_SCENE_EFFECTS} effects per scene")
        return tuple(self.resolve_effect(s) for s in specs)

    def resolve_effect(self, spec: Any) -> EffectSpec:
        """"preset" | {"preset", "params"?, "bindings"?} | {"effect", "params"?, "bindings"?}  (D.1)."""
        inline: dict = {}
        if isinstance(spec, dict) and "bindings" in spec:
            inline = spec["bindings"] or {}
            if not isinstance(inline, dict) or not all(isinstance(v, dict) for v in inline.values()):
                raise ParamError("bad_spec", "bindings must be {param: {bind, range, rest?}}")
            both = set(inline) & set(spec.get("params") or {})
            if both:
                raise ParamError("bad_spec", f"{sorted(both)} given both in params and in bindings")
            spec = {k: v for k, v in spec.items() if k != "bindings"}
        preset, name, overrides = self._split(spec, "effect")
        overrides = {**overrides, **inline}
        if preset is not None:
            entry = self.effects.get(preset)
            if entry is None:
                raise ParamError("unknown_preset", f"unknown effect preset {preset!r} (known: {sorted(self.effects)})")
            cls = self.registry.effect(entry["effect"])
            props = cls.param_props()
            plain, bindings = split_bindings(props, entry["params"])
            base = cls.validate(plain)
            bindings = self._layer("effect", cls, props, preset, entry, bindings,
                                   default_bindings_for(cls, props, set(entry["params"])))
            plain, bindings = split_bindings(props, overrides, bindings)
            params = cls.validate(plain, base=base)
            params.update({k: b["rest"] for k, b in bindings.items()
                           if "rest" in b and k not in plain and k not in entry["params"]})
            return EffectSpec(cls.name, params, preset, entry["label"], bindings)
        cls = self.registry.effect(name)
        props = cls.param_props()
        bindings = self._layer("effect", cls, props, None, None, {}, default_bindings_for(cls, props, set()))
        plain, bindings = split_bindings(props, overrides, bindings)
        params = cls.validate(plain)
        params.update({k: b["rest"] for k, b in bindings.items() if "rest" in b and k not in plain})
        return EffectSpec(cls.name, params, None, getattr(cls, "label", "") or cls.name, bindings)

    def resolve_transition(self, spec: Any) -> TransitionSpec:
        preset, name, overrides = self._split(spec, "transition")
        if preset is not None:
            entry = self.transitions.get(preset)
            if entry is None:
                raise ParamError("unknown_preset",
                                 f"unknown transition preset {preset!r} (known: {sorted(self.transitions)})")
            cls = self.registry.transition(entry["transition"])
            base = cls.validate(entry["params"])
            props = cls.param_props()
            maps = {k: b for k, b in self._layer("transition", cls, props, preset, entry, {}, {}).items()
                    if k not in overrides}
            return TransitionSpec(cls.name, cls.validate(overrides, base=base), preset, entry["label"], maps)
        cls = self.registry.transition(name)
        props = cls.param_props()
        maps = {k: b for k, b in self._layer("transition", cls, props, None, None, {}, {}).items() if k not in overrides}
        return TransitionSpec(cls.name, cls.validate(overrides), None, cls.label or cls.name, maps)
