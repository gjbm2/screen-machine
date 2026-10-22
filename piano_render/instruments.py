"""The instrument registry: config/piano/instruments.json -> Registry (paths resolved, availability checked).

Config path: load(path) > $PIANO_RENDER_CONFIG > <repo>/config/piano/instruments.json. "root" and "engines_root"
expand "~"; a relative one is taken against the config file's directory. An instrument's "file" and a room's "ir"
are under root unless absolute. Registry.get(id) returns a copy of the entry with "id", "file" (absolute) and
"room" untouched; Registry.rooms holds the rooms with "ir" absolute.

The "windows-gs" engine stands for today's live GS Wavetable path on PC22: always available, never rendered here.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "config" / "piano" / "instruments.json"
CONFIG_ENV = "PIANO_RENDER_CONFIG"
DEFAULT_ROOT = "~/.local/share/screen-machine/piano/instruments"
DEFAULT_ENGINES_ROOT = "~/.local/share/screen-machine/piano/engines"
WINDOWS_GS = "windows-gs"

INSTRUMENT_DEFAULTS: dict[str, Any] = {
    "order": 100,
    "velocity_gamma": 1.0,
    "gain_db": 0.0,
    "onset_ms": 0.0,
    "room": None,
    "width": 1.0,
    "resonance_db": None,
    "detune_cents": [],
    "eq": [],
}


def config_path(path: "str | os.PathLike | None" = None) -> Path:
    if path:
        return Path(path).expanduser().resolve()
    env = os.environ.get(CONFIG_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_CONFIG


def _resolve_dir(value: Optional[str], default: str, base: Path) -> Path:
    p = Path(os.path.expandvars(value or default)).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _under(root: Path, value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    p = Path(os.path.expandvars(value)).expanduser()
    return str(p if p.is_absolute() else root / p)


class Registry:
    def __init__(self, data: dict, path: Optional[Path] = None):
        self.path = path
        base = path.parent if path else Path.cwd()
        self.data = data
        self.root = _resolve_dir(data.get("root"), DEFAULT_ROOT, base)
        self.engines_root = _resolve_dir(data.get("engines_root"), DEFAULT_ENGINES_ROOT, base)
        self._raw: dict[str, dict] = {k: v for k, v in (data.get("instruments") or {}).items() if isinstance(v, dict)}
        self.rooms: dict[str, dict] = {}
        for rid, room in (data.get("rooms") or {}).items():
            if not isinstance(room, dict):
                continue
            r = copy.deepcopy(room)
            r["id"] = rid
            r["ir"] = _under(self.root, room.get("ir"))
            self.rooms[rid] = r
        default = data.get("default")
        if default not in self._raw:
            avail = self.available()
            default = next((i for i in avail if self._raw[i].get("engine") != WINDOWS_GS), avail[0] if avail else None)
        self.default: Optional[str] = default

    # --- lookup ---------------------------------------------------------------------------------------------
    def ids(self) -> list[str]:
        """Every instrument id, by "order" then id."""
        return sorted(self._raw, key=lambda i: (self._raw[i].get("order", INSTRUMENT_DEFAULTS["order"]), i))

    def raw(self, inst_id: str) -> dict:
        """The entry exactly as configured (what the render cache key hashes)."""
        if inst_id not in self._raw:
            raise KeyError(f"unknown instrument {inst_id!r} (known: {', '.join(self.ids()) or 'none'})")
        return copy.deepcopy(self._raw[inst_id])

    def get(self, inst_id: str) -> dict:
        """The entry with defaults filled in, "id" set and "file" an absolute path (absent for windows-gs)."""
        entry = self.raw(inst_id)
        out = copy.deepcopy(INSTRUMENT_DEFAULTS)
        out.update(entry)
        out["id"] = inst_id
        if entry.get("file"):
            out["file"] = _under(self.root, entry["file"])
        return out

    def room(self, room_id: Optional[str]) -> Optional[dict]:
        if room_id is None:
            return None
        if room_id not in self.rooms:
            raise KeyError(f"unknown room {room_id!r} (known: {', '.join(sorted(self.rooms)) or 'none'})")
        return copy.deepcopy(self.rooms[room_id])

    # --- availability ---------------------------------------------------------------------------------------
    def engine_available(self, engine: str) -> bool:
        if engine == WINDOWS_GS:
            return True
        from . import engines
        return engines.available(engine, self.engines_root)

    def why_unavailable(self, inst_id: str) -> Optional[str]:
        """None if the instrument can be used, else a short reason."""
        inst = self.get(inst_id)
        eng = inst.get("engine")
        if eng == WINDOWS_GS:
            return None
        if not eng:
            return "no engine"
        if not self.engine_available(eng):
            return f"engine {eng} not installed"
        f = inst.get("file")
        if not f:
            return "no file"
        if not Path(f).exists():
            return f"missing {f}"
        return None

    def available(self) -> list[str]:
        """Ids whose files and engine exist (windows-gs always), by order."""
        cache: dict[str, bool] = {}
        out = []
        for i in self.ids():
            inst = self._raw[i]
            eng = inst.get("engine")
            if eng == WINDOWS_GS:
                out.append(i)
                continue
            if not eng or not inst.get("file"):
                continue
            if eng not in cache:
                cache[eng] = self.engine_available(eng)
            if cache[eng] and Path(_under(self.root, inst["file"])).exists():
                out.append(i)
        return out


def load(path: "str | os.PathLike | None" = None) -> Registry:
    """Read the registry (module docstring for the path rules). A missing default config gives an empty registry
    (the fetch step writes it); a missing explicit path is an error."""
    p = config_path(path)
    explicit = bool(path) or bool(os.environ.get(CONFIG_ENV))
    if not p.exists():
        if explicit:
            raise FileNotFoundError(f"piano instruments config not found: {p}")
        return Registry({}, p)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: expected a JSON object")
    return Registry(data, p)


def from_dict(data: dict, base: "str | os.PathLike | None" = None) -> Registry:
    """A Registry from an in-memory dict (tests); relative roots are taken against ``base`` (default cwd)."""
    return Registry(data, Path(base, "instruments.json").resolve() if base else None)
