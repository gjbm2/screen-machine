"""Per-screen stream switches: config/live/stream_switches.json (4K60 plan changes 2 and 3), re-read when it changes.

    {"default": {"encoder": "ffmpeg"}, "screens": {"south-screen": {"encoder": "nvenc", "workers": "proc"}}}

``encoder``: ``nvenc`` (GPU-resident, encode/nvenc.py) or ``ffmpeg`` (raw NV12 piped into ffmpeg's hevc_nvenc, the
fallback).  ``workers``: ``inproc`` (the render engine in the service process, as before) or ``proc`` (the screen's
sessions render in a worker process of their own, workers.py).  ``fps`` (plan change 5): ``30`` or ``60``, the
screen's live frame rate: a session's profile becomes its sibling at that rate (profiles.at_fps: hevc-2160p30 <->
hevc-2160p60; a profile with no sibling keeps its own).  ``degrade``: ``on`` (the session's degradation controller,
degrade.py) or ``off`` (it is not made; the scene's own rate divider and the repeat rule stay as before).  A screen's
values override the default's.  Read when
a session is created (the file's mtime is checked each time), so a flip applies at that screen's next session, with
no restart.  No file: ffmpeg and inproc everywhere; an unreadable or malformed one keeps the last good copy; an unknown
value takes the default.  Each problem is logged once.  Plain file, no auth.
"""
from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, Optional

log = logging.getLogger("stream_service.switches")

FILE = "stream_switches.json"
DEFAULT_DIR = pathlib.Path(__file__).resolve().parent.parent / "config" / "live"
ENCODERS = ("nvenc", "ffmpeg")
WORKERS = ("inproc", "proc")
FPS = (30, 60)
DEGRADE = ("on", "off")
DEFAULTS = {"encoder": "ffmpeg", "workers": "inproc", "fps": 30, "degrade": "on"}
KNOWN = {"encoder": ENCODERS, "workers": WORKERS, "fps": FPS, "degrade": DEGRADE}


def _parse(raw: Any) -> tuple[dict, dict, list[str]]:
    """-> (default values, {screen: values}, problems).  Only dicts of known keys with known values are kept."""
    errs: list[str] = []

    def values(v: Any, where: str) -> dict:
        if not isinstance(v, dict):
            errs.append(f"{where}: not an object")
            return {}
        out = {}
        for k, x in v.items():
            if not isinstance(x, bool) and x in KNOWN.get(k, ()):
                out[k] = x
            elif k in KNOWN:
                errs.append(f"{where}.{k}: {x!r} ignored ({k} must be one of {list(KNOWN[k])})")
            else:
                errs.append(f"{where}.{k}: unknown switch ignored (known: {sorted(KNOWN)})")
        return out

    if not isinstance(raw, dict):
        raise ValueError("not a JSON object")
    default = values(raw.get("default", {}), "default")
    screens_raw = raw.get("screens", {})
    if not isinstance(screens_raw, dict):
        errs.append("screens: not an object")
        screens_raw = {}
    screens = {str(s): values(v, f"screens.{s}") for s, v in screens_raw.items()}
    return default, screens, errs


class StreamSwitches:
    def __init__(self, path: Optional[Any] = None):
        self.path = pathlib.Path(path) if path is not None else DEFAULT_DIR / FILE
        self.default: dict = {}
        self.screens: dict[str, dict] = {}
        self.errors: list[str] = []
        self._mt: Any = -1

    @classmethod
    def in_dir(cls, config_dir: Optional[Any]) -> "StreamSwitches":
        return cls(pathlib.Path(config_dir if config_dir is not None else DEFAULT_DIR) / FILE)

    def reload_if_changed(self) -> bool:
        try:
            mt = self.path.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt == self._mt:
            return False
        self._mt = mt
        if mt is None:
            self.default, self.screens, self.errors = {}, {}, []
            return True
        try:
            self.default, self.screens, errs = _parse(json.loads(self.path.read_text()))
        except (OSError, ValueError) as e:
            log.error("%s unreadable (%s): keeping the last good copy", self.path, e)
            self.errors = [str(e)]
            return False
        for e in errs:
            log.warning("%s: %s", self.path.name, e)
        self.errors = errs
        log.info("stream switches: default %s, screens %s", self.default, self.screens)
        return True

    def for_screen(self, screen: str) -> dict:
        self.reload_if_changed()
        return {**DEFAULTS, **self.default, **self.screens.get(screen, {})}

    def encoder(self, screen: str) -> str:
        return self.for_screen(screen)["encoder"]

    def workers(self, screen: str) -> str:
        return self.for_screen(screen)["workers"]

    def fps(self, screen: str) -> int:
        return int(self.for_screen(screen)["fps"])

    def degrade(self, screen: str) -> str:
        return self.for_screen(screen)["degrade"]
