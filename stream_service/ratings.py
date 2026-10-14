"""Owner ratings for the /dev Library: stars, reject and personal tags per item, in config/live/ratings.json.

  GET  /dev/ratings                     -> {"ratings": {key: {"stars"?, "reject"?, "tags"?, "at"}}}
  POST /dev/rate {"key", "stars"?, "reject"?, "tags"?}  (bearer token) -> {"key", "entry"}

``key`` is the Library item key (``scene/<name>``, ``transition/<name>``, ``effect/<name>``).  Fields left out keep
their value; stars 0 clears them; an entry with nothing left is removed.  Written atomically (tmp + rename) so a
crash never leaves half a file; a small file, re-read when it changes on disk (e.g. edited by hand).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import threading
from typing import Any

from .plugins.base import ParamError

KEY_RE = re.compile(r"^(scene|transition|effect)/[A-Za-z0-9@._#~-]{1,160}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")
MAX_TAGS = 20


class Ratings:
    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.lock = threading.Lock()
        self.data: dict[str, dict] = {}
        self.mtime = None
        self._load()

    def _load(self) -> None:
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

    def all(self) -> dict:
        with self.lock:
            self._load()
            return {k: dict(v) for k, v in self.data.items()}

    def set(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        unknown = set(body) - {"key", "stars", "reject", "tags"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        key = body.get("key")
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ParamError("bad_request", "key must be a Library item key like 'scene/<name>'")
        with self.lock:
            self._load()
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
            e.pop("at", None)
            if e:
                e["at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                self.data[key] = e
            else:
                self.data.pop(key, None)
            self._write()
            return {"key": key, "entry": self.data.get(key)}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ratings": dict(sorted(self.data.items()))}, indent=1, sort_keys=True) + "\n")
        tmp.replace(self.path)
        self.mtime = self.path.stat().st_mtime
