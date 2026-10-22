"""The coordinator's persisted state (ROOM_STATE_MODEL §3): one JSON file, version 2, written atomically on change.

room_service keeps it at ~/.local/state/screen-machine/room/state.json, outside the repo, and writes it through
room_service.util.atomic_write_json; this reference does the same steps itself: a temporary file in the same
directory (mode 0600, the directory 0700), fsync, os.replace. A save that would write the same content is skipped.

{
  "version": 2, "saved_at": 1760400131.2,
  "mode": {"current": "normal", "target": "disco", "via": [], "entered": ["house", "normal"], "leaving": ["normal"],
           "since": 1760390000.0},
  "transition": null | {"id", "resumes", "from", "via", "to", "source", "status", "phase", "reapply", "started_at",
                        "detail", "fired": ["step:<action>:<n>"], "steps": [rows, §7.2], "rollback_of"?, "notes"?},
  "last_transition": null | {... the same, plus "finished_at"},
  "believed": {"blinds.ch02": {"value": 0, "confidence": "confirmed|unconfirmed|stale|unknown", "at", "sent_at"?,
                               "inflight"?: {"value", "from", "earlier", "earlier_until", "until"}}},
  "desired":  {"blinds.ch02": {"value": 100, "policy", "slot", "settled", "retry", "reasserted"}},
  "stored":   {"pre_live": {"blinds.ch02": {"value", "at", "confidence", "from_mode", "stored_at", "closed",
                                            "explained"?, "final"?, "orphan"?, "edited_at"?}}},
  "drift":    {"blinds.ch03": {"observed", "desired", "at", "reasserted"?, "overridden"?, "manual"?, "source"?}},
  "config_pending": [], "log": []
}
A missing file gives an empty state (the coordinator boots into the default mode). A file that won't parse, or has
another version (aa's reference wrote 1), is moved aside to state.json.bad-<epoch> and the state starts empty.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

VERSION = 2


def empty() -> dict:
    return {"version": VERSION,
            "mode": {"current": None, "target": None, "via": [], "entered": [], "leaving": [], "since": None},
            "transition": None, "last_transition": None,
            "believed": {}, "desired": {}, "stored": {}, "drift": {}, "config_pending": [], "log": []}


class StateFile:
    def __init__(self, path: str | Path, now: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.now = now
        self.error: str | None = None             # why the file on disk was moved aside, for the coordinator's log
        self._last: str | None = None             # the content last written or loaded, to skip unchanged saves

    def load(self) -> dict:
        try:
            text = self.path.read_text()
        except FileNotFoundError:
            return empty()
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or data.get("version") != VERSION:
                raise ValueError(f"unknown version {data.get('version') if isinstance(data, dict) else None!r}")
        except ValueError as e:                    # JSONDecodeError is a ValueError
            bad = self.path.with_name(f"{self.path.name}.bad-{int(self.now())}")
            os.replace(self.path, bad)
            self.error = f"{self.path.name} moved aside to {bad.name}: {e}"
            return empty()
        data.pop("saved_at", None)
        state = {**empty(), **data}
        state["mode"] = {**empty()["mode"], **data.get("mode", {})}
        self._last = self._body(state)
        return state

    @staticmethod
    def _body(data: dict) -> str:
        return json.dumps(data, separators=(",", ":"), default=str)   # key order is stable; sorting costs ~40%

    def save(self, data: dict) -> bool:
        """Write `data` if it differs from what was last written. True if it wrote."""
        body = self._body(data)
        if body == self._last:
            return False
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:               # compact: an indented dump is ~20x slower (pure-Python encoder)
            f.write(f'{{"saved_at":{self.now()!r},' + body[1:])
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        self._last = body
        return True
