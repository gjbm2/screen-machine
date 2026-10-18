"""light_scenes: the lamps' scene catalogue and how the director picks one (docs/SHOW_ENGINE.md §9.1 "Light scenes";
README S5.24). The scenes builder's (18 Oct).

A **light scene** is a named way for the lamps to move: a list of look-engine layers (show.yaml ``looks.layers``
entries, the same keys and words) and, optionally, hits, tagged on the screens' preset scales so the director picks the
lamps' movement the way the renderer picks a screen scene. config/live/light_scenes.yaml (hot-reloaded)::

  version: 1
  scenes:
    <name>:
      label: "Slow breathe"                       # page words
      layers: [<a looks.layers entry>, ...]       # [] = steady (no movement)
      hits: {<looks.hits keys>, drop: {depth, decay_ms}}    # optional; none = no flashes. ``drop``: a flash at each
                                                  # drop line the director hears (LookEngine.trigger_hit)
      caps: {flash_hz_max: <n>}                   # optional: this scene's own looks.caps (unset: looks.caps')
      tags: {energy: [lo, hi], moods: [...], genre_fit: {word: 0..1, "*": 0..1}, valence: 0..1, organic: 0..1,
             density: 0..1, weight: 1.0}

The look engine (looks.py) compiles every scene against the fixture registry; the director (director.py) picks one at
each look that lands, a song change and a person's Next look, and names it in room.look (``light_scene``,
``light_scene_why``). No catalogue, or none of its scenes usable: ``light_scene`` null, and the lamps run show.yaml's
``looks.layers`` as before.

**Scoring** is the renderer's own (stream_service/tags.py, loaded **by file path** with importlib, never registered
in sys.modules: show_service never imports the stream_service package): ``scene_weight(tags, room.direction)``, one
factor per descriptor (energy band, moods, genre fit, valence, organic, density), each in (0, 1]. ``genre_fit`` may
carry ``"*"``: the fit of every vocab genre word the scene doesn't list (a word missing from the map would otherwise
count as fit 0, the floor). **The pick** is a weighted random draw (the caller's seeded ``random.Random``) over the
catalogue, never the scene showing now while another one is there.

**Loading.** One ``Catalogue`` per process (``shared()``), so the director and the rig's look engine read the same
scenes: whoever polls (show-brain: the director every RELOAD_S, the engine at each configure) re-reads the file when its
mtime or size changes, and every subscriber (``subscribe``) hears about a change on that thread. A file that doesn't
read keeps the last good scenes (``error`` says why); a scene that can't be used is left out with a problem line, the
rest load. Tag words outside config/live/vocab.yaml are listed as problems; the scene still loads (tags.py's rule).

Nothing here names a scene, mode, vibe, fixture or genre: every name lives in the YAML.
"""

from __future__ import annotations

import hashlib
import importlib.util
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

log = logging.getLogger("show_service.light_scenes")

LIGHT_SCENES_FILE = contract.REPO_ROOT / "config" / "live" / "light_scenes.yaml"   # the scenes builder's
TAGS_FILE = contract.REPO_ROOT / "stream_service" / "tags.py"   # 04's: the matching rule, read by file path
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")    # a scene name (fixtures.py's id rule, without dots)
SCENE_KEYS = frozenset({"label", "layers", "hits", "caps", "tags"})
TOP_KEYS = frozenset({"version", "scenes"})
GENRE_FILL = "*"                                 # genre_fit's fill for every vocab genre word the scene doesn't list
AXES = ("valence", "organic", "density")         # tags.py's axes (each 0..1)
WHY_MOODS = 2                                    # at most this many mood words in a pick's why
WHY_GENRE_FIT = 0.5                              # the music's genre is named in the why when the scene fits it this well


def _num01(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return min(1.0, max(0.0, float(v)))


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
class Catalogue:
    """config/live/light_scenes.yaml (module docstring). ``poll`` from one thread at a time (show-brain); ``scenes``,
    ``revision`` and the readers from any thread (swapped by reference)."""

    def __init__(self, path: pathlib.Path = LIGHT_SCENES_FILE, *, tags_path: pathlib.Path = TAGS_FILE,
                 vocab_path: pathlib.Path = contract.VOCAB_FILE) -> None:
        self.path = pathlib.Path(path)
        self.vocab_path = pathlib.Path(vocab_path)
        self._tags = _TagsModule(tags_path)
        self.scenes: dict[str, dict] = {}            # name -> {"label", "layers", "hits", "caps", "tags"}
        self.revision = ""
        self.error: Optional[str] = None
        self.problems: list[str] = []
        self._sig: Any = ()                          # () = never polled (None: the file isn't there)
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
        """Re-read the file (and the vocab) when either changed. True when the scenes in use changed."""
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
        sig, vsig = self._stat(self.path), self._stat(self.vocab_path)
        if not force and sig == self._sig and vsig == self._vsig:
            return False
        self._sig, self._vsig = sig, vsig
        if sig is None:
            self.error = f"{self.path.name}: not there (no light scenes: the lamps run show.yaml's looks.layers)"
            return self._swap({}, [])
        try:
            raw = yaml.safe_load(self.path.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError("the top level isn't a mapping")
            scenes, probs = self._parse(raw)
        except Exception as e:                           # keep the last good scenes
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
            log.warning("light scenes: %s (keeping the last good scenes)", self.error)
            return False
        self.error = None
        return self._swap(scenes, probs)

    def _swap(self, scenes: dict, probs: list[str]) -> bool:
        rev = hashlib.sha1(json.dumps(scenes, sort_keys=True, default=str).encode()).hexdigest()[:12] if scenes else ""
        self.problems = probs
        if rev == self.revision:
            return False
        self.scenes, self.revision = scenes, rev
        log.info("light scenes: %d loaded from %s (revision %s)", len(scenes), self.path, rev or "none")
        return True

    def _vocab(self, tags: Any) -> dict[str, list[str]]:
        try:
            return dict(tags.Vocab(self.vocab_path.parent).words)
        except Exception:                                # tags.py's own default vocab then
            return {k: list(v) for k, v in getattr(tags, "DEFAULT_VOCAB", {}).items()}

    def _parse(self, raw: dict) -> tuple[dict, list[str]]:
        probs: list[str] = []
        for k in raw:
            if k not in TOP_KEYS:
                probs.append(f"{k}: not a key light_scenes.yaml has: ignored")
        sec = raw.get("scenes")
        if sec is None:
            return {}, probs
        if not isinstance(sec, dict):
            raise ValueError("scenes isn't a mapping")
        tags = self._tags.get()
        if tags is None and self._tags.error:
            probs.append(f"tags: {self._tags.error}: every scene weighs the same")
        vocab = self._vocab(tags) if tags is not None else {}
        out: dict[str, dict] = {}
        for name, sc in sec.items():
            where = f"scenes.{name}"
            if not isinstance(name, str) or not NAME_RE.match(name):
                probs.append(f"{where}: a name is lower case letters, digits, - and _: left out")
                continue
            if not isinstance(sc, dict):
                probs.append(f"{where}: not a mapping: left out")
                continue
            for k in sc:
                if k not in SCENE_KEYS:
                    probs.append(f"{where}.{k}: not a scene key: ignored")
            layers = sc.get("layers", [])
            if not isinstance(layers, list) or not all(isinstance(x, dict) for x in layers):
                probs.append(f"{where}.layers: not a list of layers: left out")
                continue
            hits, caps = sc.get("hits"), sc.get("caps")
            if hits is not None and not isinstance(hits, dict):
                probs.append(f"{where}.hits: not a mapping: left out")
                continue
            if caps is not None and not isinstance(caps, dict):
                probs.append(f"{where}.caps: not a mapping: left out")
                continue
            label = sc.get("label")
            if label is not None and not isinstance(label, str):
                probs.append(f"{where}.label: not words: the name is used")
                label = None
            t = self._tags_of(sc.get("tags"), tags, vocab, where, probs)
            if t is None:
                continue
            out[name] = {"label": label or name.replace("-", " ").replace("_", " "), "layers": layers,
                         "hits": hits, "caps": caps, "tags": t}
        return out, probs

    @staticmethod
    def _tags_of(raw: Any, tags: Any, vocab: dict, where: str, probs: list[str]) -> Optional[dict]:
        if raw is None:
            return {"weight": 1.0}
        if not isinstance(raw, dict):
            probs.append(f"{where}.tags: not a mapping: left out")
            return None
        t = dict(raw)
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

    def weight(self, name: str, direction: Optional[Mapping[str, Any]]) -> float:
        """tags.scene_weight of ``name``'s tags against ``direction`` (room.direction); 1.0 when tags.py isn't
        loaded."""
        sc = self.scenes.get(name)
        if sc is None:
            return 0.0
        tags = self._tags.mod
        if tags is None:
            return float(sc["tags"].get("weight", 1.0))
        try:
            return float(tags.scene_weight(sc["tags"], dict(direction or {})))
        except Exception:                                # a direction field of an odd shape: degrade to the tag weight
            log.debug("light scenes: scoring %s failed", name, exc_info=True)
            return float(sc["tags"].get("weight", 1.0))

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
        """A weighted random draw over the catalogue (``rng.random()``), never ``avoid`` while another scene is there:
        (name, why, {name: weight}), or None with no scenes."""
        names = list(self.scenes)
        if not names:
            return None
        if avoid in names and len(names) > 1:
            names.remove(avoid)
        ws = {n: max(1e-6, self.weight(n, direction)) for n in names}
        x = rng.random() * sum(ws.values())
        got = names[-1]
        for n in names:
            x -= ws[n]
            if x < 0.0:
                got = n
                break
        return got, self.why(got, direction), ws

    def view(self) -> dict:
        """For snapshots: what is loaded and why not."""
        return {"path": str(self.path), "revision": self.revision, "scenes": {n: s["label"] for n, s in
                                                                              self.scenes.items()},
                "error": self.error, "tags_error": self._tags.error, "problems": list(self.problems)}


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
    """The process's one catalogue of config/live/light_scenes.yaml (module docstring: the director and the rig's look
    engine read the same scenes). Created on first use; polled by its readers."""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = Catalogue()
        return _SHARED
