"""Preset tags, the art-direction vocabulary and the matching rule (LIVE_V1 1.1 §1.8, R11, R12).

The art director names descriptors, never scenes: scene and transition presets carry ``tags`` and the director
weights its picks by how well they match the session's ``direction``.  A tag key is spelt exactly like the
direction key it matches:

  scene preset:      tags: {energy: [lo, hi], moods: [...], genre: [...], genre_fit: {word: 0..1},
                            palette_family: [...], valence: 0..1, organic: 0..1, density: 0..1, weight: 1.0}
  transition preset: tags: {transition_style: [gentle|lively|wild, ...], energy: [lo, hi], moods: [...],
                            genre_fit: {...}, weight: 1.0}

Weight = tags.weight x one factor per descriptor (each in (0, 1], so the weight is never 0):
  * energy: 1 inside the preset's [lo, hi], falling linearly to ENERGY_FLOOR over ENERGY_FALLOFF outside it;
  * genre: GENRE_FLOOR + (1 - GENRE_FLOOR) x fit^GENRE_GAMMA, where fit is the preset's ``genre_fit`` for the direction's
    ``genres`` {word: p} (a p-weighted mean) or its one ``genre`` word (a preset with only a ``genre`` word list:
    1 if listed, else 0);
  * valence / organic / density (both sides 0..1): 1 - AXIS_SLOPE x |direction - preset|;
  * any other word descriptor (moods, transition_style, palette_family, style): 1 on overlap, else MISMATCH
    (transitions' moods: TRANSITION_MISMATCH, softer);
  * music_moods / music_style (the music's words where a mode set its own, music_direction.effective_direction):
    the same overlap test against the preset's moods / style or transition_style, but a miss costs only
    BLEND_MISMATCH: the mode's words lead, the music's shade them.
An empty or null direction value, or a key the preset doesn't tag, counts as matched (factor 1).  The 17 Oct
retagging pass (config/live/tags.generated.json, tag_overlay.py) gives every preset real energy bands, moods,
genre_fit and the axes.  Other tag keys (e.g. ``heavy: true``) are free-form and only used by pool filters
(``{"tags": {...}}``: scalars by equality, lists by overlap; a key the preset doesn't tag matches, so
``{heavy: false}`` excludes only presets tagged heavy).

The vocabulary (moods, genre, transition_style, palette_family) is ``config/live/vocab.yaml``, hot-reloaded and
served in the catalog.  A tag value outside it logs a warning and is listed in the catalog's ``errors``; the
preset still loads.
"""
from __future__ import annotations

import logging
import math
import pathlib
from typing import Any, Iterable, Optional

import yaml

log = logging.getLogger("stream_service.tags")

VOCAB_FILE = "vocab.yaml"
VOCAB_KEYS = ("moods", "genre", "transition_style", "palette_family")
DEFAULT_VOCAB = {"moods": ["calm", "mellow", "dreamy", "warm", "cool", "bright", "dark", "euphoric", "tense",
                           "playful", "hypnotic", "aggressive"],
                 "genre": ["pop", "techno", "house", "jazz", "ambient", "classical", "rock", "soul"],
                 "transition_style": ["gentle", "lively", "wild"],
                 "palette_family": ["warm", "cool", "vivid", "neutral"]}
SCENE_DESCRIPTORS = ("energy", "moods", "genre", "palette_family", "valence", "organic", "density", "music_moods")
TRANSITION_DESCRIPTORS = ("energy", "transition_style", "moods", "genre", "music_moods", "music_style")
MISMATCH = 0.3                   # a word descriptor with no overlap
BLEND_MISMATCH = 0.6             # the music's words under a mode's own (music_moods, music_style)
TRANSITION_MISMATCH = {"moods": 0.6, "music_moods": 0.8}     # a transition's moods count, but less than a scene's
BLEND_TAG = {"music_moods": ("moods",), "music_style": ("transition_style", "style")}   # the tag each one reads
ENERGY_FALLOFF = 0.3             # outside a preset's energy band the factor falls linearly over this distance ...
ENERGY_FLOOR = 0.1               # ... to this
GENRE_FLOOR = 0.05               # a preset that would jar with the music's genre (fit 0); fit 1 = full weight
GENRE_GAMMA = 3.0                # fit 0.7 ("good") = 0.38, 0.5 = 0.17: a natural fit leads (17 Oct sims: ambient vs
                                 # techno picks overlap 0.40, vs 0.71 linear; the stars still lead within a genre)
AXES = ("valence", "organic", "density")
AXIS_SLOPE = 0.6                 # opposite ends of an axis: 0.4


class TagError(ValueError):
    pass


def _range01(v: Any, field: str) -> list[float]:
    if not (isinstance(v, (list, tuple)) and len(v) == 2 and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v)):
        raise TagError(f"{field} must be [lo, hi]")
    lo, hi = (min(1.0, max(0.0, float(x))) for x in v)
    if lo > hi:
        raise TagError(f"{field}: lo > hi")
    return [lo, hi]


def _words(v: Any, field: str) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not (isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)):
        raise TagError(f"{field} must be a word or a list of words")
    return [str(x) for x in v]


def validate_tags(tags: Any) -> dict:
    """Normalise a preset's ``tags``: descriptor keys are checked, other keys kept as given."""
    if not isinstance(tags, dict):
        raise TagError("tags must be a mapping")
    out = dict(tags)
    if "energy" in out:
        out["energy"] = _range01(out["energy"], "tags.energy")
    for key in VOCAB_KEYS:
        if key in out:
            out[key] = _words(out[key], f"tags.{key}")
    w = out.get("weight", 1.0)
    if isinstance(w, bool) or not isinstance(w, (int, float)) or not math.isfinite(w) or w <= 0:
        raise TagError("tags.weight must be a number > 0")
    out["weight"] = float(w)
    return out


def unknown_words(tags: dict, vocab: dict) -> list[str]:
    return [f"{k}:{w}" for k in VOCAB_KEYS for w in tags.get(k, []) if w not in vocab.get(k, [])]


class Vocab:
    """config/live/vocab.yaml (hot-reloaded); the built-in default when the file is absent."""

    def __init__(self, config_dir: pathlib.Path):
        self.path = pathlib.Path(config_dir) / VOCAB_FILE
        self.words: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_VOCAB.items()}
        self._mtime: Optional[int] = -1
        self.reload_if_changed()

    def reload_if_changed(self) -> bool:
        try:
            mt: Optional[int] = self.path.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt == self._mtime:
            return False
        self._mtime = mt
        if mt is None:
            self.words = {k: list(v) for k, v in DEFAULT_VOCAB.items()}
            return True
        try:
            raw = yaml.safe_load(self.path.read_text()) or {}
            if not isinstance(raw, dict):
                raise TagError("top level must be a mapping")
            self.words = {k: _words(raw.get(k, DEFAULT_VOCAB[k]), k) for k in VOCAB_KEYS}
            log.info("loaded vocab from %s", self.path)
        except (OSError, yaml.YAMLError, TagError) as e:
            log.error("cannot read %s: %s (keeping the previous vocab)", self.path, e)
        return True


# -- matching ------------------------------------------------------------------------
def descriptor_matches(key: str, want: Any, have: Any) -> bool:
    if have is None or want is None or want == []:
        return True
    if key == "energy":
        lo, hi = have
        return lo <= float(want) <= hi
    want_set = {want} if isinstance(want, str) else set(want)
    have_set = {have} if isinstance(have, str) else set(have)     # a scalar tag (e.g. style: "gentle") is one word
    return bool(want_set & have_set)


def _num01(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return min(1.0, max(0.0, float(v)))


def genre_fit(want: Any, tags: dict) -> Optional[float]:
    """How well a preset suits the music's genre, 0..1: ``want`` is one word or {word: p}; None when either side
    is untagged (or the map is empty)."""
    if not want:
        return None
    fit, words = tags.get("genre_fit"), tags.get("genre")
    if not fit and not words:
        return None
    if isinstance(want, str):
        dist = {want: 1.0}
    elif isinstance(want, dict):
        dist = {str(w): p for w, p in ((w, _num01(p)) for w, p in want.items()) if p}
    else:
        return None
    tot = sum(dist.values())
    if tot <= 0:
        return None
    if isinstance(fit, dict) and fit:
        return sum(p * (_num01(fit.get(w)) or 0.0) for w, p in dist.items()) / tot
    have = {words} if isinstance(words, str) else set(words or ())
    return sum(p for w, p in dist.items() if w in have) / tot


def descriptor_factor(key: str, direction: dict, tags: dict, mismatch: float = MISMATCH) -> float:
    """One descriptor's factor in (0, 1] (see the module docstring)."""
    if key == "genre":
        f = genre_fit(direction.get("genres") or direction.get("genre"), tags)
        return 1.0 if f is None else GENRE_FLOOR + (1.0 - GENRE_FLOOR) * f ** GENRE_GAMMA
    want = direction.get(key)
    if key in BLEND_TAG:
        have = next((tags[t] for t in BLEND_TAG[key] if tags.get(t) is not None), None)
        if have is None or want is None or want == []:
            return 1.0
        return 1.0 if descriptor_matches(key, want, have) else mismatch
    have = tags.get(key)
    if have is None or want is None or want == []:
        return 1.0
    if key == "energy":
        e = _num01(want)
        if e is None:
            return 1.0
        lo, hi = have
        d = lo - e if e < lo else e - hi if e > hi else 0.0
        return 1.0 if d <= 0.0 else max(ENERGY_FLOOR, 1.0 - d / ENERGY_FALLOFF)
    if key in AXES:
        a, b = _num01(want), _num01(have)
        return 1.0 if a is None or b is None else 1.0 - AXIS_SLOPE * abs(a - b)
    return 1.0 if descriptor_matches(key, want, have) else mismatch


def match_weight(tags: dict, direction: dict, keys: Iterable[str],
                 mismatch: Optional[dict] = None) -> float:
    w = tags.get("weight", 1.0)
    for k in keys:
        miss = (mismatch or {}).get(k, BLEND_MISMATCH if k in BLEND_TAG else MISMATCH)
        w *= descriptor_factor(k, direction, tags, miss)
    return max(w, 1e-6)


def scene_weight(tags: dict, direction: dict) -> float:
    return match_weight(tags, direction, SCENE_DESCRIPTORS)


def transition_weight(tags: dict, direction: dict) -> float:
    return match_weight(tags, direction, TRANSITION_DESCRIPTORS, TRANSITION_MISMATCH)


def matches_filter(tags: dict, flt: dict) -> bool:
    """A pool filter ``{"tags": {...}}``: scalars by equality, lists by overlap, untagged keys match."""
    for key, want in flt.items():
        have = tags.get(key)
        if have is None:
            continue
        if isinstance(have, list):
            if not ({want} if isinstance(want, str) else set(want if isinstance(want, list) else [want])) & set(have):
                return False
        elif have != want:
            return False
    return True
