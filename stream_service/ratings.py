"""Owner ratings for the /dev Library: stars, reject, personal tags and saved looks per item, in config/live/ratings.json.

  GET  /dev/ratings                     -> {"ratings": {key: {"stars"?, "reject"?, "tags"?, "looks"?, "at"}}}
  POST /dev/rate {"key", "stars"?, "reject"?, "tags"?, "looks"?}  -> {"key", "entry"}

A look is a parameter set the owner found works well for that item: {"name", "params": {param: value}}; ``looks``
replaces the whole list (the page sends it back with one added / removed).

``key`` is the Library item key (``scene/<name>``, ``transition/<name>``, ``effect/<name>``).  Fields left out keep
their value; stars 0 clears them; an entry with nothing left is removed.  Written atomically (tmp + rename) so a
crash never leaves half a file; a small file, re-read when it changes on disk (e.g. edited by hand).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import re
import threading
import time
from typing import Any

from .plugins.base import ParamError

KEY_RE = re.compile(r"^(scene|transition|effect)/[A-Za-z0-9@._#~-]{1,160}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
MAX_TAGS = 20
MAX_LOOKS = 24
PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
LOOK_NAME_RE = re.compile(r"^[^<>\x00-\x1f]{1,40}$")

# How much the auto-directors favour an item by the owner's rating: it multiplies the direction / tag match (and
# novelty) weight of every preset of that plug-in.  Unrated is neutral, 3 stars and up are favoured, 1-2 stars are
# rare, and a reject is never auto-picked (the vetted pool drops it).  Manual cues and pushes ignore ratings.
STAR_WEIGHT = {0: 1.0, 1: 0.05, 2: 0.3, 3: 1.5, 4: 4.0, 5: 8.0}
# The table is config: rating_weights.json beside ratings.json ({"stars": {"unrated": w, "1": w, ... "5": w}}),
# hot-reloaded; a missing file or a bad value keeps the default above for that entry.
WEIGHTS_FILE = "rating_weights.json"
KIND_KEY = {"source": "scene", "scene": "scene", "transition": "transition", "effect": "effect"}
CHECK_S = 1.0       # look at the file's mtime at most this often (the directors ask per candidate, hundreds a pass)


def parse_star_weights(raw: Any) -> dict[int, float]:
    """``{"stars": {"unrated" | "0".."5": w >= 0}}`` -> the full table (defaults for anything missing or bad)."""
    out = dict(STAR_WEIGHT)
    stars = raw.get("stars") if isinstance(raw, dict) else None
    if not isinstance(stars, dict):
        return out
    for k, v in stars.items():
        n = 0 if k == "unrated" else int(k) if isinstance(k, str) and k.isdigit() else None
        if n in out and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0:
            out[n] = float(v)
    return out


class Ratings:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.mtime = None
        self.weights_path = self.path.parent / WEIGHTS_FILE
        self.weights_mtime = None
        self.star_weight: dict[int, float] = dict(STAR_WEIGHT)
        self._weights: dict[str, float] = {}
        self._weights_at = None             # the (ratings, table) mtimes the weights were built from
        self._checked = None                # monotonic time of the last mtime check
        self._load()

    def _load_table(self) -> None:
        try:
            m = self.weights_path.stat().st_mtime
        except OSError:
            self.star_weight, self.weights_mtime = dict(STAR_WEIGHT), None
            return
        if m == self.weights_mtime:
            return
        try:
            self.star_weight = parse_star_weights(json.loads(self.weights_path.read_text()))
        except (OSError, ValueError):
            pass   # keep the last good table
        self.weights_mtime = m

    def _load(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._checked is not None and now - self._checked < CHECK_S:
            return
        self._checked = now
        self._load_table()
        try:
            m = self.path.stat().st_mtime
        except OSError:
            self.data, self.mtime = {}, None
            return
        if m == self.mtime:
            return
        try:
            raw = json.loads(self.path.read_text())
            self.data = {k: v for k, v in (raw.get("ratings") or {}).items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass   # keep the last good copy
        self.mtime = m

    def weight(self, kind: str, plugin: str | None) -> float:
        """The auto-directors' rating weight for a plug-in (the star table; 0.0 for a reject; unrated: the table's
        0).  Cheap: one stat per call, rebuilt only when either file changed."""
        if not plugin:
            return 1.0
        with self.lock:
            self._load()
            sw = self.star_weight
            at = (self.mtime, self.weights_mtime)
            if self._weights_at != at:
                self._weights = {k: 0.0 if v.get("reject") else sw.get(int(v.get("stars") or 0), sw[0])
                                 for k, v in self.data.items()}
                self._weights_at = at
            return self._weights.get(f"{KIND_KEY.get(kind, kind)}/{plugin}", sw[0])

    def all(self) -> dict:
        with self.lock:
            self._load()
            return {k: dict(v) for k, v in self.data.items()}

    def set(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        unknown = set(body) - {"key", "stars", "reject", "tags", "looks"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        key = body.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ParamError("bad_request", "key must be a Library item key like 'scene/<name>'")
        with self.lock:
            self._load(force=True)
            e = dict(self.data.get(key) or {})
            if "stars" in body:
                s = body["stars"]
                if isinstance(s, bool) or not isinstance(s, int) or not 0 <= s <= 5:
                    raise ParamError("bad_request", "stars must be an integer 0-5 (0 clears)")
                if s:
                    e["stars"] = s
                    e.pop("reject", None)          # a rating overrides a reject
                else:
                    e.pop("stars", None)
            if "reject" in body:
                if not isinstance(body["reject"], bool):
                    raise ParamError("bad_request", "reject must be true or false")
                if body["reject"]:
                    e["reject"] = True
                    e.pop("stars", None)
                else:
                    e.pop("reject", None)
            if "tags" in body:
                tags = body["tags"]
                if not isinstance(tags, list) or len(tags) > MAX_TAGS or \
                        not all(isinstance(t, str) and TAG_RE.match(t.strip()) for t in tags):
                    raise ParamError("bad_request", f"tags must be up to {MAX_TAGS} short words (letters, digits, space, - _)")
                clean = sorted({t.strip().lower() for t in tags})
                if clean:
                    e["tags"] = clean
                else:
                    e.pop("tags", None)
            if "looks" in body:
                looks = _looks(body["looks"])
                if looks:
                    e["looks"] = looks
                else:
                    e.pop("looks", None)
            e.pop("at", None)
            if e:
                e["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                self.data[key] = e
            else:
                self.data.pop(key, None)
            self._write()
            self._weights_at = None                    # rebuilt from the new data on the next weight()
            try:
                self.mtime = self.path.stat().st_mtime
            except OSError:
                pass
            return {"key": key, "entry": self.data.get(key)}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ratings": dict(sorted(self.data.items()))}, indent=1, sort_keys=True) + "\n")
        tmp.replace(self.path)
        self.mtime = self.path.stat().st_mtime
        self._weights_at = None             # a change here re-weights on the next pick


def _looks(v: Any) -> list:
    """Validate a list of saved looks: [{"name", "params": {name: number | bool | short string}}]."""
    def bad():
        return ParamError("bad_request", f"looks must be up to {MAX_LOOKS} {{name, params}} objects "
                                         "(params: up to 128 plain values)")
    if not isinstance(v, list) or len(v) > MAX_LOOKS:
        raise bad()
    out = []
    for look in v:
        if not isinstance(look, dict) or set(look) - {"name", "params"}:
            raise bad()
        name, params = look.get("name"), look.get("params")
        if not isinstance(name, str) or not LOOK_NAME_RE.match(name.strip()) or not isinstance(params, dict) or len(params) > 128:
            raise bad()
        for k, x in params.items():
            ok = isinstance(x, bool) or (isinstance(x, (int, float)) and math.isfinite(x)) or (isinstance(x, str) and len(x) <= 64)
            if not PARAM_RE.match(k) or not ok:
                raise bad()
        out.append({"name": name.strip(), "params": dict(params)})
    return out
