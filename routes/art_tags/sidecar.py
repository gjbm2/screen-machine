"""Art tags in the image's sidecar JSON (contract §1): per-file locks, an atomic read-modify-write that keeps every other
key exactly, the effective tag list, and a person's add/remove edits (docs/ART_TAGS.md)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from routes.utils import sidecar_path
from routes.art_tags.lexicon import FAVOURITE_WORDS, normalise

REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_OUTPUT = REPO_ROOT / "output"           # = routes.bucketer.BASE_OUTPUT (Flask runs from the repo); tests patch it
MEDIA_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}     # = routes.bucketer.ALLOWED_EXT
VIDEO_EXT = {".mp4", ".mov"}
TAGGING_V = 1
ADDED_MAX = 40               # words a person may add to one picture

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def output_base() -> Path:
    return BASE_OUTPUT


def media_path(bucket: str, filename: str) -> Path:
    return output_base() / bucket / filename


def lock_for(path: Path) -> threading.Lock:
    """One lock per sidecar, for the life of the process (a few thousand files at most)."""
    key = str(path)
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


def read(media: Path) -> dict:
    """The sidecar as a dict: {} when there is none. ValueError when it exists but isn't a JSON object (it is then
    never overwritten: its prompt would be lost)."""
    p = sidecar_path(media)
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{p.name} is not a JSON object")
    return data


def _write(p: Path, data: dict) -> None:
    # A temp file in the same folder, then os.replace: the renderer and the bucket API never see half a file.
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)


def update(media: Path, fn: Callable[[dict], Optional[dict]]) -> dict:
    """Read-modify-write the sidecar under its lock. ``fn`` changes the dict in place (or returns a new one); every
    key it doesn't touch is written back as it was."""
    p = sidecar_path(media)
    with lock_for(p):
        data = read(media)
        out = fn(data)
        data = data if out is None else out
        _write(p, data)
        return data


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dedupe(ws: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for w in ws:
        n = normalise(w)
        if n and n not in out:
            out.append(n)
    return out


def ai_words(tagging: dict) -> list[str]:
    """The AI's lozenge words in order: subject, medium, moods."""
    ai = (tagging or {}).get("ai") or {}
    return _dedupe(list(ai.get("subject") or []) + [ai.get("medium")] + list(ai.get("moods") or []))


def effective_tags(tagging: Optional[dict]) -> list[str]:
    """Contract §1: the AI's subject words, the medium, the moods, then ``added``; ``removed`` dropped; normalised,
    de-duplicated, in that order."""
    t = tagging or {}
    removed = set(_dedupe(t.get("removed") or []))
    return [w for w in _dedupe(ai_words(t) + list(t.get("added") or [])) if w not in removed]


def _tagging(d: dict) -> dict:
    t = d.get("tagging")
    if not isinstance(t, dict):
        t = {}
    t.setdefault("v", TAGGING_V)
    t["added"] = _dedupe(t.get("added") or [])
    t["removed"] = _dedupe(t.get("removed") or [])
    return t


def apply_ai(d: dict, ai: dict, model: Optional[str], cost_usd: Optional[float], lexicon_v: int) -> dict:
    """The AI's answer into the sidecar dict: status done; a person's added/removed kept (a retag too)."""
    old = _tagging(d)
    t = {"v": TAGGING_V, "status": "done", "at": now_iso(), "model": model,
         "cost_usd": round(float(cost_usd or 0.0), 6), "lexicon": lexicon_v, "ai": ai,
         "added": old["added"], "removed": old["removed"]}
    d["tagging"] = t
    d["tags"] = effective_tags(t)
    return d


def apply_failure(d: dict, error: str, attempts: int) -> dict:
    """Given up after max_attempts: status failed with the error. An earlier AI answer (a retag that failed) and a
    person's edits stay, so the lozenges don't vanish."""
    t = _tagging(d)
    t.update({"status": "failed", "at": now_iso(), "error": str(error)[:300], "attempts": int(attempts)})
    d["tagging"] = t
    d["tags"] = effective_tags(t)
    return d


def apply_edits(d: dict, add: Iterable[Any] = (), remove: Iterable[Any] = ()) -> dict:
    """A person's edit (contract §2). Adding a word in ``removed`` takes it out of ``removed`` (an AI word comes back);
    any other new word goes to ``added``. Removing an AI word puts it in ``removed``; removing an added word drops it.
    Favourite words are ignored: favourites live in bucket.json (the UI calls the favourite endpoint)."""
    t = _tagging(d)
    ai = ai_words(t)
    for w in _dedupe(add):
        if w in FAVOURITE_WORDS:
            continue
        if w in t["removed"]:
            t["removed"].remove(w)
        if w not in ai and w not in t["added"] and len(t["added"]) < ADDED_MAX:
            t["added"].append(w)
    for w in _dedupe(remove):
        if w in FAVOURITE_WORDS:
            continue
        if w in t["added"]:
            t["added"].remove(w)
        if w in ai and w not in t["removed"]:
            t["removed"].append(w)
    d["tagging"] = t
    d["tags"] = effective_tags(t)
    return d


def status_of(d: dict) -> Optional[str]:
    t = d.get("tagging")
    return t.get("status") if isinstance(t, dict) else None
