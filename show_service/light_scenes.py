"""light_scenes: the lights' scenes, effects and transitions, the light groups, and how the director picks for each
group (docs/SHOW_ENGINE.md §9.1 "Light scenes"; README S5.24; docs/LIGHTS.md §6, §7). The scenes builder's (18 Oct),
grown by the catalogue builder (18 Oct: movers, effects, transitions, groups).

A **light scene** is a named way for a group of lights to look and move: a list of look-engine layers (show.yaml
``looks.layers`` entries, plus docs/LIGHTS.md §5.2's layer kinds: position, beam, gobo, prism, colour, colour_wheel,
cct) and, optionally, hits, tagged on the screens' preset scales so the director picks the lights' look the way the
renderer picks a screen scene. Three hot-reloaded files in config/live, read by one ``Catalogue``:

  light_scenes.yaml        scenes.<name>: {label, needs, layers, hits, caps, tags}
  light_effects.yaml       effects.<name>: {label, on, every_bars, chance, length, fade, needs, layers, hits, caps,
                           tags}: overlays the director fires over a group's scene on the music's events (strobe only
                           lives here: a shutter layer in a scene is a problem line and left out)
  light_transitions.yaml   transitions.<name>: {label, kind, beats, seconds, out_s, in_s, wave_beats, dark_move, tags}:
                           how a group crosses to its next scene, at the screens' own moment

Every entry: ``label`` (page words); ``needs: [attr]`` (hard: it fits a group only when one member has every attr);
``tags`` on the screens' scales (energy [lo, hi], vocab moods, ``genre_fit`` with ``"*"`` filling the vocab's other
genre words, valence, organic, density, weight) plus ``flow: {dirs: [FLOW_DIRS], honours: bool}`` (the flows it
suits; ``honours``: its layers bind to the live flow, so it suits any) and, for transitions, ``transition_style``.

**Scoring** is the renderer's own (stream_service/tags.py, loaded **by file path** with importlib, never registered
in sys.modules: show_service never imports the stream_service package): ``scene_weight(tags, room.direction)`` (a
transition's ``transition_weight``), called with the direction's ``flow`` left out, so a later flow descriptor in
tags.py never counts twice. For a group it is multiplied by **cap_fit** = 0.3 + 0.7 x |the expressive attrs the entry
drives n the group's| / |the group's expressive attrs| (contract.EXPRESSIVE_ATTRS; an entry driving none of them
counts intensity; a group with none: 1) and **flow_fit** = 1 when the flow's dir is in the entry's ``flow.dirs`` or it
honours the flow (or is untagged), else FLOW_MISMATCH. **A pick** is a weighted random draw (the caller's seeded
``random.Random``), never the scene showing now while another one fits.

**Groups** (show.yaml ``lights.groups``; ``group_members``): selectors (fixtures.select's keys, the encode builder's
``cap:``, ``side:``, ``height:`` included), first match wins in file order; a lamp no group takes is in the group
``"*"`` ("Other lights"). ``follows: <group>`` runs that group's program. Pure and deterministic: the director and the
look engine both call it, so their membership agrees.

**Loading.** One ``Catalogue`` per process (``shared()``), so the director and the rig's look engine read the same
entries: whoever polls (show-brain: the director every RELOAD_S, the engine at each configure) re-reads a file when its
mtime or size changes (or the vocab's), and every subscriber (``subscribe``) hears about a change on that thread. A file
that doesn't read keeps its last good entries (``errors`` says why); an entry that can't be used is left out with a
problem line, the rest load. A missing effects or transitions file is no entries and a view note (no problem line).
Tag words outside config/live/vocab.yaml are listed as problems; the entry still loads (tags.py's rule).

Nothing here names a scene, effect, transition, group, mode, vibe, fixture or genre: every name lives in the YAML.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import logging
import math
import pathlib
import re
import threading
import weakref
from typing import Any, Callable, Mapping, Optional

import yaml

from . import contract
from . import flow as flowmod

log = logging.getLogger("show_service.light_scenes")

LIGHT_SCENES_FILE = contract.REPO_ROOT / "config" / "live" / "light_scenes.yaml"   # the scenes builder's
LIGHT_EFFECTS_FILE = LIGHT_SCENES_FILE.with_name("light_effects.yaml")             # the catalogue builder's
LIGHT_TRANSITIONS_FILE = LIGHT_SCENES_FILE.with_name("light_transitions.yaml")
TAGS_FILE = contract.REPO_ROOT / "stream_service" / "tags.py"   # 04's: the matching rule, read by file path
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")    # a scene name (fixtures.py's id rule, without dots)
SCENE_KEYS = frozenset({"label", "needs", "layers", "hits", "caps", "tags"})
EFFECT_KEYS = frozenset({"label", "on", "every_bars", "chance", "length", "fade", "needs", "layers", "hits", "caps",
                         "tags"})
TRANSITION_KEYS = frozenset({"label", "kind", "beats", "seconds", "out_s", "in_s", "wave_beats", "dark_move", "tags"})
TRANSITION_NUMBERS = ("beats", "seconds", "out_s", "in_s", "wave_beats")
FLOW_TAG_KEYS = frozenset({"dirs", "honours"})
LENGTH_KEYS = ("bars", "beats", "seconds")
LENGTH_WHILE = "while"                           # an effect held while the phase that fired it lasts
FADE_KEYS = ("in_beats", "out_beats")
DEFAULT_LENGTH = {"bars": 1.0}                   # an effect with no length (listed as a problem): one bar
#: file -> (its top-level section, every top-level key it may have)
PARTS = {"scenes": ("scenes", frozenset({"version", "scenes"})),
         "effects": ("effects", frozenset({"version", "effects"})),
         "transitions": ("transitions", frozenset({"version", "transitions"}))}
TOP_KEYS = PARTS["scenes"][1]
GENRE_FILL = "*"                                 # genre_fit's fill for every vocab genre word the scene doesn't list
AXES = ("valence", "organic", "density")         # tags.py's axes (each 0..1)
WHY_MOODS = 2                                    # at most this many mood words in a pick's why
WHY_GENRE_FIT = 0.5                              # the music's genre is named in the why when the scene fits it this well
CAP_FIT_BASE = 0.3                               # docs/LIGHTS.md §6.1: cap_fit = base + (1 - base) x the share driven
FLOW_MISMATCH = 0.6                              # starting point (§6.1): an entry tagged for other flows than the room's
#: the attrs each layer kind sets (cap_fit reads the expressive ones); a gobo layer's ``wheel`` names its own
KIND_ATTRS: dict[str, tuple[str, ...]] = {
    "intensity": ("intensity",), "position": ("pan", "tilt"), "beam": ("zoom", "focus", "iris", "frost"),
    "gobo": ("gobo",), "prism": ("prism",), "colour": ("colour",), "colour_wheel": ("colour_slot",),
    "cct": ("cct",), "shutter": ("shutter",)}
#: a fixture type's channel attr (contract.CHANNEL_ATTRS) -> what it lets a light show, until fixture_encode.caps is there
CHANNEL_CAPS = {"intensity": "intensity", "emitter": "colour", "wheel": "colour_slot", "shutter": "shutter",
                "pan": "pan", "tilt": "tilt"}
#: an effect's why, by its trigger (contract.LIGHT_EFFECT_TRIGGERS)
TRIGGER_WHY = {"drop": "the drop", "build": "the song builds", "breakdown": "a breakdown", "peak": "the peak",
               "section_change": "a new section", "track_change": "a new song", "outro": "the song ends"}
# -- groups (show.yaml lights; docs/LIGHTS.md §7.1, §12) ----------------------------------------------------------------
OTHER_GROUP = "*"                                # the lamps no group takes
OTHER_LABEL = "Other lights"
GROUP_KEYS = frozenset({"label", "select", "follows", "dark_level"})
DARK_FLOOR = "floor"                             # dark_level: the look's floor
#: show.yaml ``lights`` keys: the catalogue builder's, and the other builders' (read where they are used)
LIGHTS_KEYS = frozenset({"scene_group", "groups", "programs", "flow", "motion", "jog", "calibration"})
#: lights.programs' starting points (docs/LIGHTS.md §12; the file's values win)
PROGRAMS_FALLBACK = {"effects_max": 2, "effect_gap_bars": 4.0}


def _num01(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return min(1.0, max(0.0, float(v)))


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def layer_kind(layer: Mapping[str, Any]) -> Any:
    """A layer's kind: its ``kind``, else its ``attr`` (today's ``attr: intensity``), else intensity."""
    k = layer.get("kind")
    return k if k is not None else layer.get("attr", "intensity")


def drives(entry: Mapping[str, Any]) -> list[str]:
    """The attrs an entry's layers (and hits: intensity) set."""
    out: list[str] = []
    for L in entry.get("layers") or ():
        if not isinstance(L, Mapping) or L.get("enabled", True) is False:
            continue
        kind = layer_kind(L)
        got = (L.get("wheel"),) if kind == "gobo" and isinstance(L.get("wheel"), str) else KIND_ATTRS.get(kind, ())
        out += [a for a in got if a not in out]
    if entry.get("hits") is not None and "intensity" not in out:
        out.append("intensity")
    return out


class _Loader(yaml.SafeLoader):
    """YAML 1.2 booleans: only true and false. YAML 1.1 reads on/off/yes/no as booleans, so an effect's ``on:`` and a
    position layer's ``on:`` (its surface) would read as the key True."""


_Loader.yaml_implicit_resolvers = {k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
                                   for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()}
_Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
                              list("tTfF"))


def load_yaml(text: str) -> Any:
    """The light files' YAML (``_Loader``)."""
    return yaml.load(text, Loader=_Loader)


def _draw(ws: Mapping[str, float], names: list[str], rng: Any) -> str:
    x = rng.random() * sum(ws.values())
    for n in names:
        x -= ws[n]
        if x < 0.0:
            return n
    return names[-1]


# -- tags.py, by file path -----------------------------------------------------------------------------------------------
class _TagsModule:
    """stream_service/tags.py, loaded by file path and re-loaded when it changes (palette.py's rule for palettes.py)."""

    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.mod: Any = None
        self.error: Optional[str] = None
        self._sig: Optional[tuple] = None

    def get(self) -> Any:
        try:
            st = self.path.stat()
            sig = (st.st_mtime_ns, st.st_size)
        except OSError as e:
            self.error = f"{self.path.name}: {e.strerror or e}"
            return self.mod
        if sig == self._sig:
            return self.mod
        self._sig = sig
        try:
            spec = importlib.util.spec_from_file_location("_show_light_scene_tags", self.path)
            if spec is None or spec.loader is None:
                raise ImportError("no loader")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)                 # never registered in sys.modules
            for fn in ("scene_weight", "validate_tags", "genre_fit", "Vocab"):
                if not hasattr(mod, fn):
                    raise ImportError(f"it has no {fn}")
        except Exception as e:                           # degrade: every scene weighs the same
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("light scenes: %s (scenes are drawn evenly until it loads)", self.error)
            return self.mod
        self.error = None
        self.mod = mod
        return mod


# -- the catalogue -------------------------------------------------------------------------------------------------------
class _Part:
    """One of the three files: its signature, its last good entries and what it says about itself."""

    def __init__(self, name: str, path: pathlib.Path) -> None:
        self.name = name
        self.path = pathlib.Path(path)
        self.sig: Any = ()                           # () = never polled (None: the file isn't there)
        self.entries: dict[str, dict] = {}
        self.error: Optional[str] = None
        self.problems: list[str] = []
        self.note: Optional[str] = None


class Catalogue:
    """The three light files (module docstring). ``poll`` from one thread at a time (show-brain); ``scenes``,
    ``effects``, ``transitions``, ``revision`` and the readers from any thread (swapped by reference)."""

    def __init__(self, path: pathlib.Path = LIGHT_SCENES_FILE, *, effects_path: Optional[pathlib.Path] = None,
                 transitions_path: Optional[pathlib.Path] = None, tags_path: pathlib.Path = TAGS_FILE,
                 vocab_path: pathlib.Path = contract.VOCAB_FILE) -> None:
        self.path = pathlib.Path(path)
        self.vocab_path = pathlib.Path(vocab_path)
        self._tags = _TagsModule(tags_path)
        self._parts = {
            "scenes": _Part("scenes", self.path),
            "effects": _Part("effects", effects_path if effects_path is not None
                             else self.path.with_name(LIGHT_EFFECTS_FILE.name)),
            "transitions": _Part("transitions", transitions_path if transitions_path is not None
                                 else self.path.with_name(LIGHT_TRANSITIONS_FILE.name))}
        self.scenes: dict[str, dict] = {}            # name -> {"label", "needs", "layers", "hits", "caps", "tags", ...}
        self.effects: dict[str, dict] = {}
        self.transitions: dict[str, dict] = {}
        self.revision = ""
        self.error: Optional[str] = None             # the scenes file's (README S5.24)
        self.problems: list[str] = []                # all three files', scenes first
        self._vsig: Any = ()
        self._lock = threading.Lock()
        self._subs: list = []

    # -- loading ---------------------------------------------------------------------------------------------------------
    def subscribe(self, fn: Callable[["Catalogue"], None]) -> None:
        """``fn(catalogue)`` after each change, on the polling thread. A bound method is held weakly (an engine that
        is gone drops out); a failing subscriber is logged, never raised."""
        ref = weakref.WeakMethod(fn) if hasattr(fn, "__self__") else (lambda f=fn: f)
        with self._lock:
            self._subs.append(ref)

    def poll(self, force: bool = False) -> bool:
        """Re-read the files (and the vocab) when any changed. True when the entries in use changed."""
        with self._lock:
            changed = self._poll(force)
            subs = list(self._subs) if changed else []
        alive = []
        for ref in subs:
            fn = ref()
            if fn is None:
                continue
            alive.append(ref)
            try:
                fn(self)
            except Exception:                            # a subscriber's bug never stops the poller
                log.exception("light scenes: a subscriber failed")
        if changed:
            with self._lock:
                self._subs = [r for r in self._subs if r() is not None]
        return changed

    def _stat(self, p: pathlib.Path) -> Optional[tuple]:
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _poll(self, force: bool) -> bool:
        vsig = self._stat(self.vocab_path)
        vocab_changed = vsig != self._vsig
        self._vsig = vsig
        todo = []
        for part in self._parts.values():
            sig = self._stat(part.path)
            if force or vocab_changed or sig != part.sig:
                part.sig = sig
                todo.append(part)
        if not todo:
            return False
        tags = self._tags.get()
        vocab = self._vocab(tags) if tags is not None else {}
        for part in todo:
            self._load(part, tags, vocab)
        sc = self._parts["scenes"]
        self.error = sc.error
        probs = []
        if tags is None and self._tags.error:
            probs.append(f"tags: {self._tags.error}: every scene weighs the same")
        for part in self._parts.values():
            probs += part.problems
        self.problems = probs
        return self._swap()

    def _load(self, part: _Part, tags: Any, vocab: dict) -> None:
        if part.sig is None:
            part.entries, part.problems = {}, []
            if part.name == "scenes":
                part.error = f"{part.path.name}: not there (no light scenes: the lamps run show.yaml's looks.layers)"
            else:
                part.error, part.note = None, f"{part.path.name}: not there (no {part.name})"
            return
        try:
            raw = load_yaml(part.path.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError("the top level isn't a mapping")
            entries, probs = self._parse(part.name, raw, tags, vocab)
        except Exception as e:                           # keep the last good entries
            part.error = f"{part.path.name}: {type(e).__name__}: {e}"
            log.warning("light %s: %s (keeping the last good ones)", part.name, part.error)
            return
        part.entries, part.problems, part.error, part.note = entries, probs, None, None

    def _swap(self) -> bool:
        p = self._parts
        doc = {k: p[k].entries for k in PARTS}
        rev = hashlib.sha1(json.dumps(doc, sort_keys=True, default=str).encode()).hexdigest()[:12] \
            if any(doc.values()) else ""
        if rev == self.revision:
            return False
        self.scenes, self.effects, self.transitions = doc["scenes"], doc["effects"], doc["transitions"]
        self.revision = rev
        log.info("light catalogue: %d scenes, %d effects, %d transitions (revision %s)", len(self.scenes),
                 len(self.effects), len(self.transitions), rev or "none")
        return True

    def _vocab(self, tags: Any) -> dict[str, list[str]]:
        try:
            return dict(tags.Vocab(self.vocab_path.parent).words)
        except Exception:                                # tags.py's own default vocab then
            return {k: list(v) for k, v in getattr(tags, "DEFAULT_VOCAB", {}).items()}

    def _parse(self, part: str, raw: dict, tags: Any, vocab: dict) -> tuple[dict, list[str]]:
        probs: list[str] = []
        section, top = PARTS[part]
        fname = self._parts[part].path.name
        for k in raw:
            if k not in top:
                probs.append(f"{k}: not a key {fname} has: ignored")
        sec = raw.get(section)
        if sec is None:
            return {}, probs
        if not isinstance(sec, dict):
            raise ValueError(f"{section} isn't a mapping")
        out: dict[str, dict] = {}
        parse = {"scenes": self._parse_scene, "effects": self._parse_effect,
                 "transitions": self._parse_transition}[part]
        for name, sc in sec.items():
            where = f"{section}.{name}"
            if not isinstance(name, str) or not NAME_RE.match(name):
                probs.append(f"{where}: a name is lower case letters, digits, - and _: left out")
                continue
            if not isinstance(sc, dict):
                probs.append(f"{where}: not a mapping: left out")
                continue
            got = parse(name, sc, where, probs, tags, vocab)
            if got is not None:
                out[name] = got
        return out, probs

    # -- entries --------------------------------------------------------------------------------------------------------
    @staticmethod
    def _keys(sc: dict, keys: frozenset, what: str, where: str, probs: list[str]) -> None:
        for k in sc:
            if k not in keys:
                probs.append(f"{where}.{k}: not {what} key: ignored")

    @staticmethod
    def _label(sc: dict, name: str, where: str, probs: list[str]) -> str:
        label = sc.get("label")
        if label is not None and not isinstance(label, str):
            probs.append(f"{where}.label: not words: the name is used")
            label = None
        return label or name.replace("-", " ").replace("_", " ")

    @staticmethod
    def _needs(v: Any, where: str, probs: list[str]) -> Optional[list[str]]:
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            probs.append(f"{where}.needs: not a list of what a light can do: left out")
            return None
        known = getattr(contract, "ATTRS", None)     # the encode builder's vocabulary (docs/LIGHTS.md §2.1)
        bad = [x for x in v if known and x not in known]
        if bad:
            probs.append(f"{where}.needs: {', '.join(bad)} isn't something a light can do: it fits no light")
        return list(dict.fromkeys(v))

    @staticmethod
    def _layers(raw: Any, where: str, probs: list[str], *, scene: bool) -> Optional[list]:
        """The layers as written, less those that can't be used: an unknown kind, a shutter layer in a scene. The
        rest of a layer's keys are the look engine's to check (its own problem lines)."""
        if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
            probs.append(f"{where}.layers: not a list of layers: left out")
            return None
        out = []
        for i, L in enumerate(raw):
            lw = f"{where}.layers.{L.get('id', i)}"
            kind = layer_kind(L)
            if kind not in contract.LIGHT_LAYER_KINDS:
                probs.append(f"{lw}: kind {kind!r} isn't one of {', '.join(contract.LIGHT_LAYER_KINDS)}: left out")
                continue
            if scene and kind == "shutter":
                probs.append(f"{lw}: a scene never strobes (shutter layers go in light_effects.yaml): left out")
                continue
            fd = L.get("flow_dir")
            if fd is not None and fd not in flowmod.FLOW_DIRS:
                probs.append(f"{lw}.flow_dir: {fd!r} isn't a direction ({', '.join(flowmod.FLOW_DIRS)}): the "
                             f"program's flow is used")
                L = {k: v for k, v in L.items() if k != "flow_dir"}
            out.append(L)
        return out

    @staticmethod
    def _blocks(sc: dict, where: str, probs: list[str]) -> Optional[tuple[Any, Any]]:
        hits, caps = sc.get("hits"), sc.get("caps")
        if hits is not None and not isinstance(hits, dict):
            probs.append(f"{where}.hits: not a mapping: left out")
            return None
        if caps is not None and not isinstance(caps, dict):
            probs.append(f"{where}.caps: not a mapping: left out")
            return None
        return hits, caps

    def _parse_scene(self, name: str, sc: dict, where: str, probs: list[str], tags: Any, vocab: dict
                     ) -> Optional[dict]:
        self._keys(sc, SCENE_KEYS, "a scene", where, probs)
        layers = self._layers(sc.get("layers", []), where, probs, scene=True)
        if layers is None:
            return None
        hc = self._blocks(sc, where, probs)
        needs = self._needs(sc.get("needs"), where, probs)
        if hc is None or needs is None:
            return None
        label = self._label(sc, name, where, probs)
        t = self._tags_of(sc.get("tags"), tags, vocab, where, probs)
        if t is None:
            return None
        e = {"label": label, "needs": needs, "layers": layers, "hits": hc[0], "caps": hc[1], "tags": t}
        e["drives"] = drives(e)
        return e

    def _parse_effect(self, name: str, sc: dict, where: str, probs: list[str], tags: Any, vocab: dict
                      ) -> Optional[dict]:
        self._keys(sc, EFFECT_KEYS, "an effect", where, probs)
        on = sc.get("on")
        if isinstance(on, str):
            on = [on]
        if not isinstance(on, list) or not on:
            probs.append(f"{where}.on: an effect needs on: [{', '.join(contract.LIGHT_EFFECT_TRIGGERS)}]: left out")
            return None
        bad = [w for w in on if w not in contract.LIGHT_EFFECT_TRIGGERS]
        if bad:
            probs.append(f"{where}.on: {', '.join(map(str, bad))} isn't one of "
                         f"{', '.join(contract.LIGHT_EFFECT_TRIGGERS)}: ignored")
        on = [w for w in on if w in contract.LIGHT_EFFECT_TRIGGERS]
        eb = sc.get("every_bars")
        if eb is not None and not (_num(eb) is not None and eb >= 1 and float(eb).is_integer()):
            probs.append(f"{where}.every_bars: {eb!r} isn't a whole number of bars: ignored")
            eb = None
        if "every_bars" in on and eb is None:
            probs.append(f"{where}: on every_bars needs every_bars: <n>: that trigger is ignored")
            on = [w for w in on if w != "every_bars"]
        if not on:
            probs.append(f"{where}.on: no trigger left: left out")
            return None
        ch = sc.get("chance", 1.0)
        if _num01(ch) is None or not 0.0 <= ch <= 1.0:
            probs.append(f"{where}.chance: {ch!r} isn't a number in 0..1: 1 is used")
            ch = 1.0
        length = self._length(sc.get("length"), where, probs)
        fade = {"in_beats": 0.0, "out_beats": 0.0}
        fd = sc.get("fade")
        if fd is not None:
            if isinstance(fd, dict) and all(k in FADE_KEYS and _num(v) is not None and v >= 0 for k, v in fd.items()):
                fade.update({k: float(v) for k, v in fd.items()})
            else:
                probs.append(f"{where}.fade: not {{in_beats, out_beats}} (beats >= 0): no fade")
        layers = self._layers(sc.get("layers", []), where, probs, scene=False)
        if layers is None:
            return None
        hc = self._blocks(sc, where, probs)
        needs = self._needs(sc.get("needs"), where, probs)
        if hc is None or needs is None:
            return None
        if not layers and hc[0] is None:
            probs.append(f"{where}: no layers and no hits: it would do nothing: left out")
            return None
        t = self._tags_of(sc.get("tags"), tags, vocab, where, probs)
        if t is None:
            return None
        e = {"label": self._label(sc, name, where, probs), "on": on, "every_bars": int(eb) if eb else None,
             "chance": float(ch), "length": length, "fade": fade, "needs": needs, "layers": layers, "hits": hc[0],
             "caps": hc[1], "tags": t}
        e["drives"] = drives(e)
        return e

    @staticmethod
    def _length(v: Any, where: str, probs: list[str]) -> Any:
        if v == LENGTH_WHILE:
            return LENGTH_WHILE
        if isinstance(v, dict) and len(v) == 1:
            (k, n), = v.items()
            if k in LENGTH_KEYS and _num(n) is not None and n > 0:
                return {k: float(n)}
        if v is None:
            probs.append(f"{where}.length: none: one bar")
        else:
            probs.append(f"{where}.length: {v!r} isn't while or one of {{bars|beats|seconds: n}}: one bar")
        return dict(DEFAULT_LENGTH)

    def _parse_transition(self, name: str, sc: dict, where: str, probs: list[str], tags: Any, vocab: dict
                          ) -> Optional[dict]:
        self._keys(sc, TRANSITION_KEYS, "a transition", where, probs)
        kind = sc.get("kind")
        if kind not in contract.LIGHT_TRANSITION_KINDS:
            probs.append(f"{where}.kind: {kind!r} isn't one of {', '.join(contract.LIGHT_TRANSITION_KINDS)}: left out")
            return None
        e: dict[str, Any] = {"label": self._label(sc, name, where, probs), "kind": kind}
        for k in TRANSITION_NUMBERS:
            v = sc.get(k)
            if v is not None and not (_num(v) is not None and v >= 0):
                probs.append(f"{where}.{k}: {v!r} isn't a number >= 0: ignored")
                v = None
            e[k] = float(v) if v is not None else None
        dm = sc.get("dark_move", False)
        if not isinstance(dm, bool):
            probs.append(f"{where}.dark_move: {dm!r} isn't true or false: false")
            dm = False
        e["dark_move"] = dm
        t = self._tags_of(sc.get("tags"), tags, vocab, where, probs)
        if t is None:
            return None
        e["tags"] = t
        return e

    @staticmethod
    def _flow_tag(t: dict, where: str, probs: list[str]) -> None:
        f = t.get("flow")
        if f is None:
            return
        if not isinstance(f, dict):
            probs.append(f"{where}.tags.flow: not {{dirs: [...], honours: true|false}}: ignored")
            t.pop("flow")
            return
        for k in f:
            if k not in FLOW_TAG_KEYS:
                probs.append(f"{where}.tags.flow.{k}: not a flow tag key: ignored")
        dirs = f.get("dirs", [])
        if isinstance(dirs, str):
            dirs = [dirs]
        if not isinstance(dirs, list):
            probs.append(f"{where}.tags.flow.dirs: not a list of directions: ignored")
            dirs = []
        bad = [d for d in dirs if d not in flowmod.FLOW_DIRS]
        if bad:
            probs.append(f"{where}.tags.flow.dirs: {', '.join(map(str, bad))} not a direction "
                         f"({', '.join(flowmod.FLOW_DIRS)}): ignored")
        hon = f.get("honours", False)
        if not isinstance(hon, bool):
            probs.append(f"{where}.tags.flow.honours: {hon!r} isn't true or false: false")
            hon = False
        t["flow"] = {"dirs": [d for d in dirs if d in flowmod.FLOW_DIRS], "honours": hon}

    @classmethod
    def _tags_of(cls, raw: Any, tags: Any, vocab: dict, where: str, probs: list[str]) -> Optional[dict]:
        if raw is None:
            return {"weight": 1.0}
        if not isinstance(raw, dict):
            probs.append(f"{where}.tags: not a mapping: left out")
            return None
        t = dict(raw)
        cls._flow_tag(t, where, probs)
        gf = t.get("genre_fit")
        if gf is not None:
            if not isinstance(gf, dict) or not all(_num01(v) is not None for v in gf.values()):
                probs.append(f"{where}.tags.genre_fit: not {{genre word: 0..1}}: ignored")
                t.pop("genre_fit")
            else:
                words = list(vocab.get("genre") or [])
                fill = _num01(gf.get(GENRE_FILL))
                bad = [w for w in gf if w != GENRE_FILL and words and w not in words]
                if bad:
                    probs.append(f"{where}.tags.genre_fit: {', '.join(map(str, bad))} not in vocab.yaml genre")
                fit = {str(w): _num01(v) for w, v in gf.items() if w != GENRE_FILL}
                if fill is not None:
                    fit.update({w: fill for w in words if w not in fit})
                t["genre_fit"] = fit
        for k in AXES:
            if k in t and not (_num01(t[k]) is not None and 0.0 <= t[k] <= 1.0):
                probs.append(f"{where}.tags.{k}: {t[k]!r} isn't a number in 0..1: ignored")
                t.pop(k)
        if tags is None:
            return t
        try:
            t = tags.validate_tags(t)
        except Exception as e:                           # tags.TagError: the scene can't be scored as written
            probs.append(f"{where}.tags: {e}: left out")
            return None
        unknown = tags.unknown_words(t, vocab) if hasattr(tags, "unknown_words") else []
        if unknown:
            probs.append(f"{where}.tags: {', '.join(unknown)} not in vocab.yaml (the scene still loads)")
        return t

    # -- readers (any thread) -----------------------------------------------------------------------------------------
    def label(self, name: Optional[str]) -> Optional[str]:
        sc = self.scenes.get(name) if name else None
        return sc["label"] if sc else None

    def _tag_weight(self, t: dict, direction: Optional[Mapping[str, Any]], *, transition: bool = False) -> float:
        tags = self._tags.mod
        if tags is None:
            return float(t.get("weight", 1.0))
        d = {k: v for k, v in dict(direction or {}).items() if k != "flow"}
        try:
            fn = tags.transition_weight if transition and hasattr(tags, "transition_weight") else tags.scene_weight
            return float(fn(t, d))
        except Exception:                                # a direction field of an odd shape: degrade to the tag weight
            log.debug("light scenes: scoring failed", exc_info=True)
            return float(t.get("weight", 1.0))

    def weight(self, name: str, direction: Optional[Mapping[str, Any]]) -> float:
        """tags.scene_weight of ``name``'s tags against ``direction`` (room.direction); 1.0 when tags.py isn't
        loaded."""
        sc = self.scenes.get(name)
        if sc is None:
            return 0.0
        return self._tag_weight(sc["tags"], direction)

    def why(self, name: str, direction: Optional[Mapping[str, Any]]) -> str:
        """Short words for what in the music the scene fits: the energy when it's inside the scene's band, the moods
        both name, the music's genre when the scene fits it well; "a change" when nothing stands out."""
        sc = self.scenes.get(name)
        if sc is None:
            return ""
        t, d = sc["tags"], dict(direction or {})
        words: list[str] = []
        e = _num01(d.get("energy"))
        band = t.get("energy")
        if e is not None and isinstance(band, (list, tuple)) and len(band) == 2 and band[0] <= e <= band[1]:
            words.append(f"energy {e:.1f}")
        moods = d.get("moods") or []
        have = set(t.get("moods") or [])
        words += [m for m in moods if isinstance(m, str) and m in have][:WHY_MOODS]
        g = _genre_word(d)
        tags = self._tags.mod
        if g is not None and tags is not None:
            try:
                fit = tags.genre_fit(g, t)
            except Exception:
                fit = None
            if fit is not None and fit >= WHY_GENRE_FIT:
                words.append(g)
        return ", ".join(words) if words else "a change"

    def pick(self, direction: Optional[Mapping[str, Any]], rng: Any, *, avoid: Optional[str] = None
             ) -> Optional[tuple[str, str, dict[str, float]]]:
        """A weighted random draw over the scenes (``rng.random()``), never ``avoid`` while another scene is there:
        (name, why, {name: weight}), or None with no scenes. No group: every scene, by its tags alone."""
        names = list(self.scenes)
        if not names:
            return None
        if avoid in names and len(names) > 1:
            names.remove(avoid)
        ws = {n: max(1e-6, self.weight(n, direction)) for n in names}
        got = _draw(ws, names, rng)
        return got, self.why(got, direction), ws

    # -- per group (docs/LIGHTS.md §6.1, §6.5) -------------------------------------------------------------------------
    @staticmethod
    def fits(entry: Mapping[str, Any], group: Mapping[str, Any]) -> bool:
        """``needs`` (hard): one member of the group can show every attr listed. No needs: any group."""
        needs = entry.get("needs") or []
        if not needs:
            return True
        mc = group.get("member_caps")
        if isinstance(mc, Mapping) and mc:
            return any(set(needs) <= set(a or ()) for a in mc.values())
        return bool(group.get("members")) and set(needs) <= set(group.get("caps") or ())

    @staticmethod
    def cap_fit(entry: Mapping[str, Any], group: Optional[Mapping[str, Any]]) -> float:
        if group is None:
            return 1.0
        gexp = set(group.get("caps") or ()) & set(contract.EXPRESSIVE_ATTRS)
        if not gexp:
            return 1.0
        drv = set(entry.get("drives") or ()) & set(contract.EXPRESSIVE_ATTRS) or {"intensity"}
        return CAP_FIT_BASE + (1.0 - CAP_FIT_BASE) * len(drv & gexp) / len(gexp)

    @staticmethod
    def flow_fit(entry: Mapping[str, Any], flow: Any) -> float:
        f = (entry.get("tags") or {}).get("flow")
        d = flow.get("dir") if isinstance(flow, Mapping) else flow
        if not f or not d or f.get("honours") or d in (f.get("dirs") or ()):
            return 1.0
        return FLOW_MISMATCH

    def score(self, entry: Mapping[str, Any], group: Optional[Mapping[str, Any]], direction: Any, flow: Any, *,
              transition: bool = False) -> float:
        """An entry's weight for a group: its tags' weight x cap_fit x flow_fit (module docstring)."""
        w = self._tag_weight(entry.get("tags") or {}, direction, transition=transition)
        return max(1e-6, w * self.cap_fit(entry, group) * self.flow_fit(entry, flow))

    def pick_for(self, group: Mapping[str, Any], direction: Optional[Mapping[str, Any]], flow: Any, rng: Any, *,
                 avoid: Optional[str] = None) -> Optional[tuple[str, str, dict[str, float]]]:
        """A group's scene: a weighted draw over the scenes that fit it, never ``avoid`` (the one it shows) while
        another fits. (name, why, {name: weight}), or None when none fits."""
        names = [n for n, sc in self.scenes.items() if self.fits(sc, group)]
        if not names:
            return None
        if avoid in names and len(names) > 1:
            names.remove(avoid)
        ws = {n: self.score(self.scenes[n], group, direction, flow) for n in names}
        got = _draw(ws, names, rng)
        return got, self.why(got, direction), ws

    def pick_effect(self, group: Mapping[str, Any], trigger: str, direction: Optional[Mapping[str, Any]], flow: Any,
                    rng: Any, *, running: list[str], bar: Optional[int] = None) -> Optional[tuple[str, str]]:
        """An effect for ``trigger`` (contract.LIGHT_EFFECT_TRIGGERS) on a group: a weighted draw over the effects
        that fire on it, fit the group and aren't ``running``; then its ``chance``. ``every_bars``: only those whose
        every_bars divides ``bar``. (name, why) or None."""
        names = []
        for n, fx in self.effects.items():
            if trigger not in fx["on"] or n in running or not self.fits(fx, group):
                continue
            if trigger == "every_bars" and (bar is None or not fx.get("every_bars") or bar % fx["every_bars"]):
                continue
            names.append(n)
        if not names:
            return None
        ws = {n: self.score(self.effects[n], group, direction, flow) for n in names}
        got = _draw(ws, names, rng)
        fx = self.effects[got]
        if fx["chance"] < 1.0 and rng.random() >= fx["chance"]:
            return None
        why = TRIGGER_WHY.get(trigger) or (f"every {fx['every_bars']} bars" if fx.get("every_bars")
                                          else trigger.replace("_", " "))
        return got, why

    def pick_transition(self, kinds: tuple[str, ...], style: Optional[str], direction: Optional[Mapping[str, Any]],
                        flow: Any, rng: Any) -> Optional[str]:
        """A transition of one of ``kinds``, weighted by tags.transition_weight with the feel's ``style`` and by the
        flow; None when there is none of those kinds."""
        names = [n for n, t in self.transitions.items() if t["kind"] in kinds]
        if not names:
            return None
        d = dict(direction or {})
        if style:
            d["transition_style"] = style
        ws = {n: self.score(self.transitions[n], None, d, flow, transition=True) for n in names}
        return _draw(ws, names, rng)

    def view(self) -> dict:
        """For snapshots: what is loaded and why not."""
        p = self._parts
        return {"path": str(self.path), "revision": self.revision,
                "scenes": {n: s["label"] for n, s in self.scenes.items()},
                "effects": {n: s["label"] for n, s in self.effects.items()},
                "transitions": {n: s["label"] for n, s in self.transitions.items()},
                "error": self.error, "errors": {k: v.error for k, v in p.items() if v.error},
                "notes": [v.note for v in p.values() if v.note], "tags_error": self._tags.error,
                "problems": list(self.problems)}


def _genre_word(direction: Mapping[str, Any]) -> Optional[str]:
    """room.direction's leading genre word: ``genre``, else the most likely of ``genres``."""
    g = direction.get("genre")
    if isinstance(g, str) and g:
        return g
    gs = direction.get("genres")
    if isinstance(gs, dict) and gs:
        best = max(gs.items(), key=lambda kv: _num01(kv[1]) or 0.0)
        return str(best[0]) if (_num01(best[1]) or 0.0) > 0.0 else None
    return None


_SHARED: Optional[Catalogue] = None
_SHARED_LOCK = threading.Lock()


def shared() -> Catalogue:
    """The process's one catalogue of config/live/light_*.yaml (module docstring: the director and the rig's look
    engine read the same entries). Created on first use; polled by its readers."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = Catalogue()
        return _SHARED


# -- groups (show.yaml lights; docs/LIGHTS.md §6.5, §7.1) ------------------------------------------------------------------
def parse_lights(raw: Any, probs: list[str]) -> dict:
    """show.yaml ``lights`` -> {"scene_group", "groups": {id: {label, select, follows, dark_level}} (file order),
    "programs": {effects_max, effect_gap_bars}, "flow": flow.parse_settings(...)}. A bad value falls back (no groups:
    every lamp is in "*", which is then the scene group) and is listed. ``motion``, ``jog`` and ``calibration`` are the
    other builders' (read where they are used)."""
    out: dict[str, Any] = {"scene_group": OTHER_GROUP, "groups": {}, "programs": dict(PROGRAMS_FALLBACK)}
    if raw is not None and not isinstance(raw, dict):
        probs.append("lights: not a mapping: no light groups")
        raw = None
    raw = raw or {}
    for k in raw:
        if k not in LIGHTS_KEYS:
            probs.append(f"lights.{k}: not a lights key: ignored")
    out["flow"] = flowmod.parse_settings(raw.get("flow"), probs)
    gs = raw.get("groups")
    groups: dict[str, dict] = {}
    if gs is not None and not isinstance(gs, dict):
        probs.append("lights.groups: not a mapping: no light groups")
    elif gs:
        for gid, g in gs.items():
            w = f"lights.groups.{gid}"
            if not isinstance(gid, str) or not NAME_RE.match(gid):
                probs.append(f"{w}: a group's name is lower case letters, digits, - and _: left out")
                continue
            if not isinstance(g, dict):
                probs.append(f"{w}: not a mapping: left out")
                continue
            for k in g:
                if k not in GROUP_KEYS:
                    probs.append(f"{w}.{k}: not a group key ({', '.join(sorted(GROUP_KEYS))}): ignored")
            sel = g.get("select")
            if not (isinstance(sel, str) and sel.strip()):
                probs.append(f"{w}.select: none: it takes no lights")
                sel = None
            label = g.get("label") if isinstance(g.get("label"), str) and g.get("label") \
                else gid.replace("-", " ").replace("_", " ")
            dl = g.get("dark_level", DARK_FLOOR)
            if not (dl == DARK_FLOOR or (_num(dl) is not None and 0.0 <= dl <= 1.0)):
                probs.append(f"{w}.dark_level: {dl!r} is neither floor nor a number in 0..1: floor")
                dl = DARK_FLOOR
            fo = g.get("follows")
            if fo is not None and not isinstance(fo, str):
                probs.append(f"{w}.follows: {fo!r} isn't a group's name: ignored")
                fo = None
            groups[gid] = {"label": label, "select": sel, "follows": fo,
                           "dark_level": float(dl) if dl != DARK_FLOOR else DARK_FLOOR}
    for gid, g in groups.items():                    # follows: another group, and no loop
        fo, seen = g["follows"], {gid}
        while fo is not None:
            if fo not in groups or fo in seen:
                probs.append(f"lights.groups.{gid}.follows: {g['follows']!r} "
                             f"{'leads round in a loop' if fo in seen else 'is not a group'}: it runs its own program")
                g["follows"] = None
                break
            seen.add(fo)
            fo = groups[fo]["follows"]
    out["groups"] = groups
    sg = raw.get("scene_group")
    if sg is not None:
        if sg in groups or sg == OTHER_GROUP:
            out["scene_group"] = sg
        else:
            probs.append(f"lights.scene_group: {sg!r} isn't a group: the other lights' group is used")
    pr = raw.get("programs")
    if pr is not None:
        if not isinstance(pr, dict):
            probs.append("lights.programs: not a mapping")
        else:
            for k, v in pr.items():
                if k not in PROGRAMS_FALLBACK:
                    probs.append(f"lights.programs.{k}: not a programs key: ignored")
                elif _num(v) is None or v < 0:
                    probs.append(f"lights.programs.{k}: {v!r} isn't a number >= 0")
                else:
                    out["programs"][k] = int(v) if k == "effects_max" else float(v)
    return out


_ENC: Any = None
_SPATIAL_OK: dict[int, bool] = {}


def _encoder() -> Any:
    """fixture_encode (the encode builder's), or None until it is there."""
    global _ENC
    if _ENC is None:
        try:
            from . import fixture_encode as enc
            _ENC = enc if hasattr(enc, "caps") else False
        except Exception:
            _ENC = False
    return _ENC or None


def fixture_attrs(rig: Optional[Mapping[str, Any]], fid: str) -> list[str]:
    """What a fixture can show (contract.ATTRS words): fixture_encode.caps(type, setup)["attrs"], else its type's attrs
    and its channels' (pan, tilt, a colour wheel, a shutter) until the encoder is there."""
    fx = (rig or {}).get("fixtures") or {}
    f = fx.get(fid)
    if not isinstance(f, Mapping):
        return []
    t = ((rig or {}).get("types") or {}).get(f.get("type"))
    if not isinstance(t, Mapping):
        return []
    enc = _encoder()
    if enc is not None:
        try:
            return list(enc.caps(t, f.get("setup") or None).get("attrs") or [])
        except Exception:                                # the encoder's bug: the type's own words then
            log.debug("light groups: fixture_encode.caps failed for %s", fid, exc_info=True)
    out = [a for a in (t.get("attrs") or []) if isinstance(a, str)]
    for ch in t.get("channels") or []:
        a = CHANNEL_CAPS.get(ch.get("attr")) if isinstance(ch, Mapping) else None
        if a and a not in out:
            out.append(a)
    return out


def _select(sel: str, rig: Mapping[str, Any], lamps: Optional[Mapping], spatial: Optional[Callable]) -> list[str]:
    from . import fixtures as fxmod
    fn = fxmod.select
    ok = _SPATIAL_OK.get(id(fn))
    if ok is None:
        try:
            ok = "spatial" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            ok = False
        _SPATIAL_OK[id(fn)] = ok
    if ok:
        return fn(sel, rig, lamps=lamps, spatial=spatial)
    return fn(sel, rig, lamps=lamps)


def group_members(groups_cfg: Optional[Mapping[str, Any]], rig: Optional[Mapping[str, Any]], *,
                  lamps: Optional[Mapping[str, Any]] = None, spatial: Optional[Callable] = None,
                  problems: Optional[list[str]] = None) -> dict[str, contract.GroupInfo]:
    """Every light group's members (docs/LIGHTS.md §6.5): each lamp fixture goes to the FIRST group in file order whose
    selector takes it; the rest are in ``"*"`` ("Other lights", always last, maybe empty). Pure and deterministic.
    ``groups_cfg``: parse_lights' ``groups`` (show.yaml's raw mapping reads the same); ``lamps``: show.yaml lamps (for
    ``role:``); ``spatial``: space.spatial_fn(view) for ``side:`` / ``height:``. A selector that doesn't parse takes
    no lights (a line in ``problems``)."""
    probs = problems if problems is not None else []
    cfg = groups_cfg if isinstance(groups_cfg, Mapping) else {}
    rig = rig if isinstance(rig, Mapping) else {}
    fx = rig.get("fixtures") or {}
    types = rig.get("types") or {}

    def kind(f: Mapping) -> Any:
        t = types.get(f.get("type"))
        return (t.get("kind") if isinstance(t, Mapping) else None) or f.get("kind")

    lamp_ids = [fid for fid, f in fx.items() if isinstance(f, Mapping) and kind(f) == "lamp"]
    lampset = set(lamp_ids)
    attrs = {fid: fixture_attrs(rig, fid) for fid in lamp_ids}
    taken: set[str] = set()
    out: dict[str, contract.GroupInfo] = {}

    def info(gid: str, label: Any, members: list[str], follows: Any, dark: Any) -> contract.GroupInfo:
        caps: list[str] = []
        for m in members:
            caps += [a for a in attrs.get(m, ()) if a not in caps]
        return {"id": gid, "label": label if isinstance(label, str) and label else gid, "members": members,
                "caps": caps, "follows": follows, "dark_level": dark if dark is not None else DARK_FLOOR,
                "member_caps": {m: list(attrs.get(m, ())) for m in members}}

    for gid, g in cfg.items():
        if not isinstance(g, Mapping) or gid == OTHER_GROUP:
            continue
        ids: list[str] = []
        sel = g.get("select")
        if isinstance(sel, str) and sel.strip() and fx:
            try:
                ids = _select(sel, rig, lamps, spatial)
            except ValueError as e:
                probs.append(f"lights.groups.{gid}.select: {e}: it takes no lights")
        members = [fid for fid in ids if fid in lampset and fid not in taken]
        taken.update(members)
        fo = g.get("follows")
        out[str(gid)] = info(str(gid), g.get("label"), members,
                             fo if isinstance(fo, str) and fo in cfg and fo != gid else None, g.get("dark_level"))
    out[OTHER_GROUP] = info(OTHER_GROUP, OTHER_LABEL, [fid for fid in lamp_ids if fid not in taken], None, DARK_FLOOR)
    return out
