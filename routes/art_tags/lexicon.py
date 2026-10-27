"""Art tags: the lexicon (config/art_tags.yaml), hot-reloaded by mtime; the tagger's system prompt, its tool schema and
the cleaning of an answer to the lexicon (docs/ART_TAGS.md; owner, 27 Oct 2026)."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
LEXICON_FILE = REPO_ROOT / "config" / "art_tags.yaml"

FACETS = ("subject", "medium", "moods", "palette")
# The facets' rule lines in the prompt (the YAML keeps them as comments, which the parser drops).
RULES = {"subject": "1-2, best first: what the picture is OF",
         "medium": "exactly 1: how it looks made",
         "moods": "1-3, best first: how it feels to look at",
         "palette": "exactly 1: the colour family that dominates"}
SCORES = ("energy", "valence", "organic", "density", "quality", "confidence")
MAX_SUBJECTS, MAX_MOODS = 2, 3
MUSIC_DEFAULT = 0.3          # a group the answer left out: "odd", never a fit by omission
WORD_MAX = 32
LOOK_MAX = 200
TAGGER_DEFAULTS = {"task": "tag", "image_px": 768, "prompt_chars": 700, "min_interval_s": 1.0,
                   "sweep_every_s": 900, "skip_buckets": ["_recent"], "max_attempts": 3}
FAVOURITE_WORDS = {"favourite", "favorite", "★"}   # bucket.json favourites, never a sidecar word

_lock = threading.Lock()
_cache: dict[str, Any] = {"stamp": None, "data": None, "error": None}


def _parse(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("art_tags.yaml: the top level must be a mapping")
    facets = raw.get("facets") or {}
    for f in FACETS:
        if not isinstance(facets.get(f), dict) or not facets[f]:
            raise ValueError(f"art_tags.yaml: facets.{f} must list words")
    groups = raw.get("music_groups") or {}
    if not isinstance(groups, dict) or not groups:
        raise ValueError("art_tags.yaml: music_groups must list groups")
    tagger = dict(TAGGER_DEFAULTS)
    tagger.update(raw.get("tagger") or {})
    return {"version": int(raw.get("version") or 1),
            "facets": {f: {str(w): str(d or "") for w, d in facets[f].items()} for f in FACETS},
            "music_groups": {str(g): str(d or "") for g, d in groups.items()},
            "rubric": str(raw.get("rubric") or ""),
            "tagger": tagger}


def load() -> dict:
    """The lexicon, re-read when the file's mtime or size changes. A bad edit keeps the last good one (logged)."""
    try:
        st = os.stat(LEXICON_FILE)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError as e:
        stamp = None
        err: Optional[Exception] = e
    else:
        err = None
    with _lock:
        if stamp is not None and stamp != _cache["stamp"]:
            try:
                _cache["data"] = _parse(yaml.safe_load(LEXICON_FILE.read_text(encoding="utf-8")))
                _cache["error"] = None
            except Exception as e:                    # noqa: BLE001 - keep the last good file
                err = e
            _cache["stamp"] = stamp
        if err is not None and _cache["error"] != str(err):
            _cache["error"] = str(err)
            from utils.logger import warning
            warning(f"[art_tags] {LEXICON_FILE.name}: {err} (keeping the last good lexicon)")
        if _cache["data"] is None:
            raise ValueError(f"art_tags.yaml could not be read: {_cache['error']}")
        return _cache["data"]


def settings() -> dict:
    """The tagger: block (with defaults)."""
    return dict(load()["tagger"])


def words(facet: str) -> list[str]:
    return list(load()["facets"][facet])


def entries(facet: str) -> list[dict]:
    return [{"word": w, "desc": d} for w, d in load()["facets"][facet].items()]


def groups() -> list[str]:
    return list(load()["music_groups"])


def normalise(word: Any) -> Optional[str]:
    """Contract §1: lower-case, trimmed, spaces (and underscores) to single hyphens, at most 32 characters."""
    if not isinstance(word, str):
        return None
    w = re.sub(r"[\s_]+", "-", word.strip().lower())
    w = re.sub(r"-{2,}", "-", w).strip("-")[:WORD_MAX].strip("-")
    return w or None


def system_prompt() -> str:
    lex = load()
    lines = []
    for f in FACETS:
        lines.append(f"{f} ({RULES[f]}):")
        lines += [f"  - {w}: {d}" for w, d in lex["facets"][f].items()]
    facets = "\n".join(lines)
    grp = "; ".join(f"{g} ({d})" for g, d in lex["music_groups"].items())
    # str.replace, not format: the rubric's own text may hold braces
    return lex["rubric"].replace("{facets}", facets).replace("{groups}", grp).strip()


def tool_schema() -> dict:
    """The forced tool (utils.llm.call_json). Enums come from the lexicon; the music groups are listed but not each
    required, so one missed group never fails (and re-bills) an answer: clean() fills it."""
    lex = load()
    num = {"type": "number", "minimum": 0, "maximum": 1}
    fw = {f: list(lex["facets"][f]) for f in FACETS}
    return {
        "name": "tag_picture",
        "description": "Tag one picture for the living-room art library, using only the given words.",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "array", "items": {"type": "string", "enum": fw["subject"]},
                            "minItems": 1, "maxItems": MAX_SUBJECTS},
                "medium": {"type": "string", "enum": fw["medium"]},
                "moods": {"type": "array", "items": {"type": "string", "enum": fw["moods"]},
                          "minItems": 1, "maxItems": MAX_MOODS},
                "palette": {"type": "string", "enum": fw["palette"]},
                "look": {"type": "string", "description": "At most 18 words."},
                "energy": num, "valence": num, "organic": num, "density": num, "quality": num,
                "music": {"type": "object", "properties": {g: num for g in lex["music_groups"]}},
                "auto_ok": {"type": "boolean"},
                "confidence": num,
            },
            "required": ["subject", "medium", "moods", "palette", "look", "energy", "valence", "organic", "density",
                         "quality", "music", "auto_ok", "confidence"],
        },
    }


def _num(v: Any, default: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return default
    return round(min(1.0, max(0.0, float(v))), 3)


def _list(v: Any) -> list:
    return v if isinstance(v, list) else ([v] if v is not None else [])


def _pick(values: Any, allowed: list[str], n: int) -> list[str]:
    out: list[str] = []
    for x in _list(values):
        w = normalise(x)
        if w in allowed and w not in out:
            out.append(w)
    return out[:n]


def clean(data: Any) -> dict:
    """An answer -> the sidecar's ``ai`` block (contract §1): unknown words dropped, numbers clamped to 0..1, missing
    music groups at MUSIC_DEFAULT, at most 2 subjects and 3 moods. ValueError when nothing usable is left (no subject
    and no mood): the worker counts that as a failed attempt."""
    if not isinstance(data, dict):
        raise ValueError("the answer is not an object")
    lex = load()
    fw = {f: list(lex["facets"][f]) for f in FACETS}
    subject = _pick(data.get("subject"), fw["subject"], MAX_SUBJECTS)
    moods = _pick(data.get("moods"), fw["moods"], MAX_MOODS)
    if not subject and not moods:
        raise ValueError("no subject or mood from the lexicon")
    medium = (_pick(data.get("medium"), fw["medium"], 1) or [None])[0]
    palette = (_pick(data.get("palette"), fw["palette"], 1) or [None])[0]
    look = data.get("look")
    look = re.sub(r"\s+", " ", look).strip()[:LOOK_MAX] if isinstance(look, str) else ""
    music_in = data.get("music") if isinstance(data.get("music"), dict) else {}
    music_in = {normalise(k): v for k, v in music_in.items()}
    ai: dict[str, Any] = {"subject": subject, "medium": medium, "moods": moods, "palette": palette, "look": look}
    for k in SCORES:
        if k != "confidence":
            ai[k] = _num(data.get(k), 0.5)
    ai["music"] = {g: _num(music_in.get(g), MUSIC_DEFAULT) for g in lex["music_groups"]}
    auto_ok = data.get("auto_ok")
    ai["auto_ok"] = auto_ok if isinstance(auto_ok, bool) else True
    ai["confidence"] = _num(data.get("confidence"), 0.5)
    return ai
