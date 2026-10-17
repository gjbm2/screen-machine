"""config/live/tags.generated.json: reviewed tags for scenes, effects and transitions, layered over the plug-ins' own.

The plug-ins' imported tags are mostly regex guesses (17 Oct audit, screen-machine-2b), so a tagging pass writes its
results here, one file for all three kinds, hot-reloaded (mtime) like the mappings, and presets.py layers it:

    plug-in preset tags (the .presets.yaml / automatic ones)
      < overlay "plugins"[kind][<plug-in>]       every preset of that plug-in
      < overlay "presets"[kind][<preset name>]   that preset
    config/live file presets (scenes.yaml, effects.yaml, transitions.yaml) keep their own tags: the overlay only
    fills the fields they do not set.

Field rules, per tag field:
  * a field given replaces the one below it;  ``null`` removes it (an untagged descriptor matches every direction);
  * ``modes`` (set membership: scene_tags pools, the owner's sets) is never replaced: the overlay's words are added;
  * ``genre`` is a word list or a sparse affinity map ``{word: 0..1}``; a map is kept as ``genre_fit`` and ``genre``
    becomes its words with affinity >= GENRE_MIN (what today's scorer matches on, tags.match_weight);
  * keys starting with ``_`` (e.g. ``_why``) are notes: dropped;
  * everything else goes through tags.validate_tags (energy [lo, hi], vocab word lists, weight > 0), and vocab words
    are checked against vocab.yaml (unknown ones dropped and listed); ``auto: false`` keeps an item out of the
    directors' own picks (director.vetted pool, fx_director).

File::

    {"format": 1, "gen": {"rev": "...", "by": "...", "at": "..."},
     "plugins": {"scene": {"<plug-in>": {<tags>}}, "effect": {...}, "transition": {...}},
     "presets": {"scene": {"<preset>": {<tags>}}, "effect": {...}, "transition": {...}}}
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
from typing import Any, Optional

from .tags import VOCAB_KEYS, TagError, validate_tags

log = logging.getLogger(__name__)

OVERLAY_FILE = "tags.generated.json"
KINDS = ("scene", "effect", "transition")
GENRE_MIN = 0.5            # a genre_fit word at or above this counts as the scene's genre for today's scorer
ADDITIVE = {"modes"}


class TagOverlay:
    def __init__(self, config_dir: pathlib.Path):
        self.path = pathlib.Path(config_dir) / OVERLAY_FILE
        self.plugins: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self.presets: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        self.gen: dict = {}
        self.errors: list[str] = []
        self._mtime: Optional[int] = -1

    def reload_if_changed(self, force: bool = False) -> bool:
        """-> True when the overlay changed (presets rebuild).  A broken file keeps the last good one."""
        try:
            mt: Optional[int] = self.path.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt == self._mtime and not force:
            return False
        self._mtime = mt
        if mt is None:
            changed = any(self.plugins.values()) or any(self.presets.values())
            self.plugins, self.presets = {k: {} for k in KINDS}, {k: {} for k in KINDS}
            self.gen, self.errors = {}, []
            return changed
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict) or raw.get("format") != 1:
                raise ValueError('top level must be {"format": 1, ...}')
            plugins, presets = (self._section(raw.get(s), s) for s in ("plugins", "presets"))
        except (OSError, ValueError) as e:
            log.error("cannot read %s: %s (keeping the previous overlay)", self.path, e)
            self.errors = [f"{OVERLAY_FILE}: {e}"]
            return False
        self.plugins, self.presets, self.gen, self.errors = plugins, presets, raw.get("gen") or {}, []
        log.info("loaded tag overlay %s: %s", self.path.name,
                 {k: (len(plugins[k]), len(presets[k])) for k in KINDS})
        return True

    @staticmethod
    def _section(sec: Any, name: str) -> dict[str, dict[str, dict]]:
        out: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
        if sec is None:
            return out
        if not isinstance(sec, dict) or set(sec) - set(KINDS):
            raise ValueError(f"{name} must map {list(KINDS)} to items")
        for kind, items in sec.items():
            if not isinstance(items, dict) or not all(isinstance(v, dict) for v in items.values()):
                raise ValueError(f"{name}.{kind} must map names to tag objects")
            out[kind] = {str(n): dict(v) for n, v in items.items()}
        return out

    def apply(self, kind: str, name: str, plugin: Optional[str], tags: Optional[dict], vocab: dict,
              authored: bool = False) -> tuple[Optional[dict], list[str]]:
        """A preset's tags with the overlay layered on (``authored``: a config/live file preset, whose own fields
        win).  -> (tags or None when there were none and the overlay adds none, problems)."""
        layers = [x for x in (self.plugins.get(kind, {}).get(plugin or ""), self.presets.get(kind, {}).get(name))
                  if x]
        if not layers:
            return tags, []
        out = dict(tags or {})
        own = set(out) if authored else set()
        probs: list[str] = []
        for layer in layers:
            for k, v in layer.items():
                if k.startswith("_") or k in own:
                    continue
                if k in ADDITIVE:
                    have = out.get(k)
                    have = [have] if isinstance(have, str) else list(have or [])
                    add = [v] if isinstance(v, str) else list(v or [])
                    out[k] = have + [w for w in add if w not in have]
                elif v is None:
                    out.pop(k, None)
                    if k == "genre":
                        out.pop("genre_fit", None)
                elif k == "genre" and isinstance(v, dict):
                    fit, bad = _genre_fit(v, vocab.get("genre") or [])
                    probs += [f"{kind} {name}: genre {b}" for b in bad]
                    out["genre_fit"] = fit
                    out["genre"] = [w for w, a in sorted(fit.items(), key=lambda kv: -kv[1]) if a >= GENRE_MIN]
                else:
                    out[k] = v
        for key in VOCAB_KEYS:                       # unknown vocab words: dropped (listed), the rest kept
            if key in out and key not in own:
                words = [out[key]] if isinstance(out[key], str) else out[key]
                if isinstance(words, list) and vocab.get(key):
                    bad = [w for w in words if w not in vocab[key]]
                    if bad:
                        probs.append(f"{kind} {name}: {key} words not in vocab.yaml: {bad}")
                        out[key] = [w for w in words if w in vocab[key]]
        try:
            return validate_tags(out), probs
        except TagError as e:
            return tags, probs + [f"{kind} {name}: overlay tags dropped: {e}"]


def _genre_fit(v: dict, words: list[str]) -> tuple[dict[str, float], list[str]]:
    fit, bad = {}, []
    for w, a in v.items():
        if words and w not in words:
            bad.append(f"{w!r} not in vocab.yaml")
        elif isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a) or not 0.0 <= a <= 1.0:
            bad.append(f"{w!r}: affinity must be 0..1")
        elif a > 0:
            fit[str(w)] = round(float(a), 3)
    return fit, bad
