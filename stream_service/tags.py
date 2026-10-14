"""Preset tags, the art-direction vocabulary and the matching rule (LIVE_V1 1.1 §1.8, R11, R12).

The art director names descriptors, never scenes: scene and transition presets carry ``tags`` and the director
weights its picks by how well they match the session's ``direction``.  A tag key is spelt exactly like the
direction key it matches:

  scene preset:      tags: {energy: [lo, hi], moods: [...], genre: [...], palette_family: [...], weight: 1.0}
  transition preset: tags: {transition_style: [gentle|lively|wild, ...], energy: [lo, hi], weight: 1.0}

One rule: a number matches a ``[lo, hi]`` tag; a word or list matches a tag list on overlap; an empty or null
direction value, or a key the preset doesn't tag, counts as matched.  Weight = tags.weight x the product over
the descriptors of (1 if matched, else 0.3); it is never 0.  Other tag keys (e.g. ``heavy: true``) are free-form
and only used by pool filters (``{"tags": {...}}``: scalars by equality, lists by overlap; a key the preset
doesn't tag matches, so ``{heavy: false}`` excludes only presets tagged heavy).

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
SCENE_DESCRIPTORS = ("energy", "moods", "genre", "palette_family")
TRANSITION_DESCRIPTORS = ("energy", "transition_style")
MISMATCH = 0.3


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


def match_weight(tags: dict, direction: dict, keys: Iterable[str]) -> float:
    w = tags.get("weight", 1.0)
    for k in keys:
        if not descriptor_matches(k, direction.get(k), tags.get(k)):
            w *= MISMATCH
    return max(w, 1e-6)


def scene_weight(tags: dict, direction: dict) -> float:
    return match_weight(tags, direction, SCENE_DESCRIPTORS)


def transition_weight(tags: dict, direction: dict) -> float:
    return match_weight(tags, direction, TRANSITION_DESCRIPTORS)


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
