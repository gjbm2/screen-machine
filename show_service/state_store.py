"""state.json: what the brain keeps across a restart (README "Stage 3 > S3.4", docs/SHOW_ENGINE.md §11.2).

Shared plumbing (B3), with real tests (tests/test_state_store.py). Stdlib and ``contract`` only.

* **What persists** is each component's ``persist()``, under its name: the vibe, steps, palette pick, chips (as
  nudges with an absolute expiry) and listening, plus Auto's runtime. The clock, sections, LLM beliefs, situation
  timers and the RoomView deliberately start cold. Test sources persist nothing.
* **Shape:** ``{"v": 1, "saved_at": <epoch s>, "version": <show_service version>, "<component>": {...}, ...}``.
* **Written atomically:** ``state.json.tmp`` in the same directory (the directory is created with mode 0700), write,
  flush, fsync, ``os.replace``, then the directory is fsynced. A crash leaves the old file or the new one, never half.
* **Read once, at start:** a missing file is ``{}``. A torn or invalid file is renamed ``state.json.bad-<epoch>``, the
  brain starts cold, and ``status()["error"]`` says so in plain words (it stays visible until the next restart).
  Sections are checked against README S3.4's shapes: a key of the wrong type is dropped (listed in ``problems``), so a
  component's ``restore()`` gets a dict it can trust, or ``{}``.
* **Throttled:** ``dirty()`` from any thread; ``flush(collect)`` on show-brain writes at most once per
  ``contract.PERSIST_MIN_S`` and only after a change. ``flush(collect, force=True)`` at stop.
* **Never raises** from ``load``, ``save``, ``flush``, ``append_jsonl`` or ``tail_jsonl``: a full disk or a read-only
  directory makes persistence quieter (an error in ``status()``), never a crash.

Also the two jsonl helpers the logs use (``contract.AUTOPILOT_LOG``, ``contract.DIRECTOR_LOG``): ``append_jsonl``
(rotates to ``<file>.1`` at 1 MB) and ``tail_jsonl`` (the last n entries, newest last; api.py's fallback for
``GET /api/v1/log`` when a component is missing).
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import __version__
from . import contract

log = logging.getLogger("show_service.state_store")

SCHEMA_V = 1
RESERVED: tuple[str, ...] = ("v", "saved_at", "version")
JSONL_MAX_BYTES = 1 << 20            # 1 MB, then <file>.1 (one generation kept)
LOG_EVERY_S = 30.0                   # a repeating write error is logged at most this often
_TAIL_CHUNK = 64 << 10


# -- JSON (strict: NaN/inf become null, numpy becomes lists, tuples become lists) -------------------------------------
def clean_json(o: Any, depth: int = 0) -> Any:
    """A JSON-safe copy: non-finite floats -> None, numpy -> lists/scalars, tuples/sets -> lists, keys -> str."""
    if depth > 48:
        return None
    if isinstance(o, bool) or o is None or isinstance(o, (int, str)):
        return o
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, Mapping):
        return {str(k): clean_json(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [clean_json(v, depth + 1) for v in o]
    if hasattr(o, "tolist"):
        return clean_json(o.tolist(), depth + 1)
    if isinstance(o, Path):
        return str(o)
    return str(o)


def dumps(obj: Any, *, indent: Optional[int] = None) -> str:
    """Strict JSON text (never NaN). Raises only on something that can't be made JSON at all (it always can)."""
    try:
        return json.dumps(obj, separators=(",", ":") if indent is None else None, indent=indent, allow_nan=False,
                          ensure_ascii=False)
    except (TypeError, ValueError, RecursionError):
        return json.dumps(clean_json(obj), separators=(",", ":") if indent is None else None, indent=indent,
                          allow_nan=False, ensure_ascii=False)


# -- files ------------------------------------------------------------------------------------------------------------
def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def ensure_dir(d: Path, mode: int = 0o700) -> None:
    """Create ``d`` (and parents) if missing; the leaf gets ``mode`` (parents the default). An existing directory's
    mode is left alone."""
    Path(d).mkdir(mode=mode, parents=True, exist_ok=True)


def atomic_write_json(path: Path, obj: Any, *, dir_mode: int = 0o700, file_mode: int = 0o600) -> None:
    """Write ``obj`` as JSON to ``path`` atomically (tmp in the same directory, fsync, os.replace). Raises OSError."""
    path = Path(path)
    ensure_dir(path.parent, dir_mode)
    text = dumps(obj)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, file_mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def quarantine(path: Path, wall_s: float) -> Optional[Path]:
    """Rename a bad file to ``<name>.bad-<epoch>`` (a numbered suffix if that exists). None if it couldn't be moved."""
    path = Path(path)
    base = path.with_name(f"{path.name}.bad-{int(wall_s)}")
    dest, n = base, 1
    while dest.exists() and n < 100:
        dest = base.with_name(f"{base.name}.{n}")
        n += 1
    try:
        os.replace(path, dest)
    except OSError as e:
        log.error("state: can't move the bad %s aside: %s", path, e)
        return None
    return dest


# -- the persisted shapes (README S3.4) --------------------------------------------------------------------------------
def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _opt(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda v: v is None or check(v)


def _is_str(v: Any) -> bool:
    return isinstance(v, str)


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _is_target(v: Any) -> bool:
    return isinstance(v, dict) and isinstance(v.get("vibe"), str) and isinstance(v.get("listening", False), bool) \
        and isinstance(v.get("overrides", {}), dict)


#: section -> key -> check. Keys not listed pass through untouched (a component may keep more than S3.4 lists);
#: sections not listed pass through whole (a component this table doesn't know).
SHAPES: dict[str, dict[str, Callable[[Any], bool]]] = {
    "director": {
        "target": _opt(_is_target),
        "target_source": lambda v: v in ("mode", "api"),
        "target_set_at": _opt(_is_num),
        "mode": _opt(_is_str),
        "nudges": _is_dict,
    },
    "listener": {
        "on": _is_bool,
        "set_at": _opt(_is_num),
    },
    "autopilot": {
        "entered": _opt(_is_dict),
        "hold": _opt(_is_dict),
        "pause": _opt(_is_dict),
        "backoff_until": _opt(_is_num),
        "suppressed": lambda v: isinstance(v, list) and all(isinstance(x, dict) for x in v),
        "switches": lambda v: isinstance(v, list) and all(_is_num(x) for x in v),
        "last": _opt(_is_dict),
    },
}
NUDGE_SHAPES: dict[str, Callable[[Any], bool]] = {
    "steps": _is_int,
    "palette": _is_str,
    "moods": _opt(_is_str_list),
    "keep_until": _opt(_is_num),
    "house": _is_bool,
    "low": _is_bool,
}


def check_sections(saved: Mapping[str, Any]) -> tuple[dict[str, dict], list[str]]:
    """``(clean, problems)``: every section a dict, the reserved keys gone, and README S3.4's keys of the wrong type
    dropped (one problem line each, e.g. ``"director.nudges.steps: dropped (not the saved shape)"``)."""
    clean: dict[str, dict] = {}
    problems: list[str] = []
    for name, part in saved.items():
        name = str(name)
        if name in RESERVED or part is None:
            continue
        if not isinstance(part, dict):
            problems.append(f"{name}: dropped (not an object)")
            continue
        shape = SHAPES.get(name)
        if shape is None:
            clean[name] = dict(part)
            continue
        out: dict[str, Any] = {}
        for k, v in part.items():
            chk = shape.get(k)
            if chk is not None and not _safe(chk, v):
                problems.append(f"{name}.{k}: dropped (not the saved shape)")
                continue
            out[k] = v
        if name == "director" and isinstance(out.get("nudges"), dict):
            nudges = {}
            for k, v in out["nudges"].items():
                chk = NUDGE_SHAPES.get(k)
                if chk is not None and not _safe(chk, v):
                    problems.append(f"director.nudges.{k}: dropped (not the saved shape)")
                    continue
                nudges[k] = v
            out["nudges"] = nudges
        clean[name] = out
    return clean, problems


def _safe(chk: Callable[[Any], bool], v: Any) -> bool:
    try:
        return bool(chk(v))
    except Exception:
        return False


# -- the store ----------------------------------------------------------------------------------------------------------
class StateStore:
    """state.json for the brain. ``load()`` once at start (show-brain), ``dirty()`` from any thread, ``flush()`` on
    show-brain (each tick is fine: it writes only when due), ``flush(force=True)`` at stop."""

    def __init__(self, path: Path = contract.STATE_FILE, *, version: str = __version__,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 min_interval_s: float = contract.PERSIST_MIN_S) -> None:
        self.path = Path(path)
        self.version = version
        self.wall, self.mono = wall, mono
        self.min_interval_s = float(min_interval_s)
        self.load_error: Optional[str] = None       # stays until restart: "started cold: ..."
        self.write_error: Optional[str] = None      # cleared by the next good write
        self.problems: list[str] = []
        self.bad_file: Optional[Path] = None
        self.loaded_saved_at: Optional[float] = None
        self.saved_at: Optional[float] = None       # wall time of this process's last good write
        self.saves = self.failures = 0
        self._dirty = False
        self._last_save_mono: Optional[float] = None
        self._last_log = -1e9
        self._lock = threading.Lock()               # save() from show-brain, a final flush from the stopping thread

    # -- read (at start) --------------------------------------------------------------------------------------------
    def load(self) -> dict[str, dict]:
        """The saved sections by component name (``v``/``saved_at``/``version`` removed), or ``{}``. Never raises."""
        self.problems = []
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as e:
            return self._bad(f"couldn't be read ({e.strerror or e})")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            return self._bad(f"isn't valid JSON ({type(e).__name__}: {str(e)[:80]})")
        if not isinstance(data, dict):
            return self._bad("isn't a JSON object")
        if data.get("v") != SCHEMA_V:
            return self._bad(f"has version {data.get('v')!r}, not {SCHEMA_V}")
        at = data.get("saved_at")
        self.loaded_saved_at = float(at) if _is_num(at) else None
        clean, problems = check_sections(data)
        self.problems = problems
        if problems:
            log.warning("state: %s: %s", self.path, "; ".join(problems))
        return clean

    def _bad(self, why: str) -> dict:
        moved = quarantine(self.path, self.wall())
        self.bad_file = moved
        where = f", kept as {moved.name}" if moved is not None else ""
        self.load_error = f"Started cold: the saved state {why}{where}."
        log.error("state: %s %s", self.path, self.load_error)
        return {}

    # -- write --------------------------------------------------------------------------------------------------------
    def save(self, sections: Mapping[str, Any]) -> bool:
        """Write now: ``{"v", "saved_at", "version", <name>: <section>}``; a None section is left out. Never raises."""
        body: dict[str, Any] = {"v": SCHEMA_V, "saved_at": round(self.wall(), 3), "version": self.version}
        try:
            for name, part in (sections or {}).items():
                if part is None or str(name) in RESERVED:
                    continue
                body[str(name)] = part
        except Exception as e:                     # a broken mapping: keep what we have
            self._write_failed(f"bad sections ({type(e).__name__})")
            return False
        with self._lock:
            try:
                atomic_write_json(self.path, body)
            except Exception as e:
                self._write_failed(f"{getattr(e, 'strerror', None) or e}")
                return False
            self.saved_at = body["saved_at"]
            self._last_save_mono = self.mono()
            self.saves += 1
            self.write_error = None
        return True

    def _write_failed(self, why: str) -> None:
        self.failures += 1
        self.write_error = f"Can't save the state to {self.path}: {why}."
        now = self.mono()
        if now - self._last_log >= LOG_EVERY_S:
            self._last_log = now
            log.error("state: %s", self.write_error)

    def dirty(self) -> None:
        """Any thread (= ``ctx.persist``): something worth keeping changed; written within PERSIST_MIN_S."""
        self._dirty = True

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def due(self) -> bool:
        if not self._dirty:
            return False
        last = self._last_save_mono
        return last is None or self.mono() - last >= self.min_interval_s

    def flush(self, collect: Callable[[], Mapping[str, Any]], *, force: bool = False) -> bool:
        """If due (or ``force``): ``save(collect())``. A ``dirty()`` during collect() re-arms; a failed write stays
        dirty, so it is retried after the next interval. True when it wrote."""
        if not force and not self.due():
            return False
        self._dirty = False
        try:
            sections = collect()
        except Exception as e:
            self._dirty = True
            self._write_failed(f"collecting the state failed ({type(e).__name__}: {e})")
            self._last_save_mono = self.mono()     # don't spin on a collect that keeps failing
            return False
        ok = self.save(sections)
        if not ok:
            self._dirty = True
            self._last_save_mono = self.mono()
        return ok

    # -- status (any thread) ------------------------------------------------------------------------------------------
    @property
    def error(self) -> Optional[str]:
        return self.write_error or self.load_error

    def status(self) -> dict:
        """``{"path", "saved_age_s", "error", "problems", "bad_file", "saves", "failures"}`` (README's
        ``brain.snapshot()["state_file"]``). ``saved_age_s``: since this process's last write, else the loaded
        file's."""
        at = self.saved_at if self.saved_at is not None else self.loaded_saved_at
        age = round(max(0.0, self.wall() - at), 1) if at is not None else None
        return {"path": str(self.path), "saved_age_s": age, "error": self.error, "problems": list(self.problems),
                "bad_file": str(self.bad_file) if self.bad_file is not None else None, "saves": self.saves,
                "failures": self.failures}


# -- jsonl logs -----------------------------------------------------------------------------------------------------------
def append_jsonl(path: Path, obj: Any, *, max_bytes: int = JSONL_MAX_BYTES) -> bool:
    """Append one JSON line; at ``max_bytes`` the file becomes ``<file>.1`` first (one generation). Never raises."""
    path = Path(path)
    try:
        ensure_dir(path.parent)
        try:
            if path.stat().st_size >= max_bytes:
                os.replace(path, path.with_name(path.name + ".1"))
        except FileNotFoundError:
            pass
        line = dumps(obj) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception as e:
        log.debug("jsonl: can't append to %s: %s", path, e)
        return False


def _tail_lines(path: Path, n: int) -> list[str]:
    """The last ``n`` non-empty lines of a file, oldest first ([] if it can't be read)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            buf = b""
            while pos > 0 and buf.count(b"\n") <= n:
                step = min(_TAIL_CHUNK, pos)
                pos -= step
                f.seek(pos)
                buf = f.read(step) + buf
    except OSError:
        return []
    lines = [ln for ln in buf.split(b"\n") if ln.strip()]
    if pos > 0 and lines:                      # the first piece may be a partial line
        lines = lines[1:]
    return [ln.decode("utf-8", "replace") for ln in lines[-n:]] if n > 0 else []


def tail_jsonl(path: Path, n: int = 50) -> list[dict]:
    """The last ``n`` entries (objects), newest last, reaching into ``<file>.1`` when the live file has fewer. Lines
    that aren't JSON objects are skipped. Never raises."""
    path = Path(path)
    n = max(0, int(n))
    if n == 0:
        return []
    out: list[dict] = []
    for p in (path, path.with_name(path.name + ".1")):
        need = n - len(out)
        if need <= 0:
            break
        got = []
        for ln in _tail_lines(p, need * 2 + 8):    # a few spare lines, so a bad line doesn't shorten the answer
            try:
                v = json.loads(ln)
            except ValueError:
                continue
            if isinstance(v, dict):
                got.append(v)
        out = got[-need:] + out
    return out[-n:]
