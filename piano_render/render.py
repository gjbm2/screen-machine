"""MIDI file + instrument id -> a cached, mastered FLAC whose time 0 is the MIDI's time 0.

    res = render("piece.mid", "salamander")              # RenderResult; res.path is the FLAC
    res = render("piece.mid", "salamander", room="none")   # dry (room=None: the instrument's own room)

Pipeline: score.load_midi -> engine.render (raw stereo float32, sample 0 = MIDI 0) -> realism.apply (if realism;
else realism.apply_dry, the instrument's EQ only) -> master.process (instrument gain_db, cut only to keep true
peak <= -1 dBTP) -> FLAC 24-bit 48 kHz. The realism stage may lengthen the audio (resonance and room tails), never
shift it.

Cache: <cache>/<key>.flac + <key>.json (the RenderResult) in $PIANO_RENDER_CACHE or
~/.cache/screen-machine/piano/renders. key = sha256 over the MIDI bytes, the instrument id and entry (minus the
cosmetic "label", "order", "credit", "_comment"), the instrument file's content (instrument_files_digest: an SFZ's
text and its #includes, an SF2's size and mtime), the room entry, the realism flag, RENDER_VERSION,
realism.VERSION (with or without realism: it holds the EQ too), the engine's version string and the sample rate.
Files are written under temporary names and
renamed (the .json last, so a .json means a complete render). While rendering, <key>.progress.json holds
{"stage", "done_s", "total_s", "pid", "updated"}, rewritten at least twice a second, removed when done. A second
render of the same key (thread or process) waits on <key>.lock (flock) and then returns the first one's result.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from . import engines as _engines
from . import instruments as _instruments
from . import master as _master
from . import score as _score

RENDER_VERSION = "3"      # 2: the realism stage (piano_render/dsp); realism off still applies the instrument EQ
#                           3: engines end the tail at an absolute floor (-70 dBFS after gain_db), with a 50 ms fade
SAMPLE_RATE = 48000
CACHE_ENV = "PIANO_RENDER_CACHE"
DEFAULT_CACHE = Path("~/.cache/screen-machine/piano/renders").expanduser()
COSMETIC_KEYS = frozenset({"label", "order", "credit", "_comment"})
NO_ROOM = ("none", "")              # room aliases for "no room" (unless the registry has a room by that id)
ONSET_THRESHOLD_DBFS = -50.0
HEARTBEAT_S = 0.5

ProgressCb = Optional[Callable[[dict], None]]


@dataclass
class RenderResult:
    path: str
    sample_rate: int
    duration_s: float
    onset_ms: float              # the instrument's configured onset_ms (its samples' lead-in), for sync
    lufs: Optional[float]
    true_peak_dbtp: Optional[float]
    cache_key: str
    engine: str
    instrument: str
    room: Optional[str]
    realism: bool
    stats: dict = field(default_factory=dict)   # timings, master stats, measured first onset, versions
    cached: bool = False                        # this call found it in the cache (not stored)

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("cached", None)
        return d

    @classmethod
    def from_json(cls, d: dict, cached: bool = False) -> "RenderResult":
        names = {f.name for f in fields(cls)}
        r = cls(**{k: v for k, v in d.items() if k in names and k != "cached"})
        r.cached = cached
        return r


def cache_dir(override: "str | os.PathLike | None" = None) -> Path:
    if override:
        return Path(override).expanduser()
    env = os.environ.get(CACHE_ENV)
    return Path(env).expanduser() if env else DEFAULT_CACHE


# --- the key -------------------------------------------------------------------------------------------------
def _clean(entry: Optional[dict]) -> Optional[dict]:
    if entry is None:
        return None
    return {k: v for k, v in entry.items() if k not in COSMETIC_KEYS}


def _realism_module():
    from . import realism
    return realism


def resolve_room(registry, inst: dict, room: Optional[str], realism: bool) -> Optional[str]:
    if not realism:
        return None
    if room is None:
        room = inst.get("room")
    if room is None:
        return None
    if room in registry.rooms:
        return room
    if str(room).lower() in NO_ROOM:
        return None
    raise KeyError(f"unknown room {room!r} (known: {', '.join(sorted(registry.rooms)) or 'none'})")


# --- the instrument's own files in the key -------------------------------------------------------------------
# A rewritten SFZ (a new wrapper or generated map from fetch) changes the sound without changing instruments.json,
# so the key also holds the file's content: an SFZ's text and every file it #includes (sfizz resolves an #include
# against the top SFZ's folder; a missing one counts by its name), and for an SF2 (or any other binary) its size and
# mtime (hashing 140 MB would cost a cached render ~0.3 s). Samples are not read. Each file is read once per
# (size, mtime) and memoised, so a key costs a few stat() calls.
_INCLUDE_RE = re.compile(rb'#include\s+"([^"]+)"')
_FILE_MEMO: "dict[str, tuple[tuple[int, int], str, tuple[str, ...]]]" = {}
_FILE_MEMO_LOCK = threading.Lock()


def _file_sig(path: Path, text: bool) -> tuple[str, tuple[str, ...]]:
    """(digest, the #include paths it names) for one file, memoised by (size, mtime_ns)."""
    st = path.stat()
    sig = (st.st_size, st.st_mtime_ns)
    k = f"{int(text)}:{path}"
    with _FILE_MEMO_LOCK:
        hit = _FILE_MEMO.get(k)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2]
    if text:
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        incs = tuple(dict.fromkeys(m.decode("utf-8", "replace") for m in _INCLUDE_RE.findall(data)))
    else:
        digest, incs = f"size={st.st_size} mtime_ns={st.st_mtime_ns}", ()
    with _FILE_MEMO_LOCK:
        _FILE_MEMO[k] = (sig, digest, incs)
    return digest, incs


def instrument_files_digest(file: "str | os.PathLike | None") -> Optional[str]:
    """sha256 over the instrument file's content signature (see above); None without a file, "missing" when the
    file itself does not exist."""
    if not file:
        return None
    top = Path(file)
    if not top.is_file():
        return "missing"
    h = hashlib.sha256()
    if top.suffix.lower() != ".sfz":
        h.update(_file_sig(top, False)[0].encode())
        return h.hexdigest()
    seen: set[str] = set()
    todo = [top]
    while todo:
        p = todo.pop(0)
        if str(p) in seen:
            continue
        seen.add(str(p))
        try:
            digest, incs = _file_sig(p, True)
        except OSError:                                  # vanished between the walk and the read
            digest, incs = "unreadable", ()
        h.update(f"{p.relative_to(top.parent) if p.is_relative_to(top.parent) else p}\0{digest}\0".encode())
        for inc in incs:
            rel = inc.replace("\\", "/")
            cand = [top.parent / rel, p.parent / rel]
            q = next((c for c in cand if c.is_file()), None)
            if q is None:
                h.update(f"missing {rel}\0".encode())
            else:
                todo.append(q)
    return h.hexdigest()


def compute_key(midi_bytes: bytes, inst_id: str, inst_raw: dict, room_raw: Optional[dict], realism: bool,
                engine_version: str, sr: int = SAMPLE_RATE, files_digest: Optional[str] = None) -> str:
    payload = {
        "render_version": RENDER_VERSION,
        "realism_version": getattr(_realism_module(), "VERSION", "minimal-1"),   # also holds the EQ
        "midi_sha256": hashlib.sha256(midi_bytes).hexdigest(),
        "instrument_id": inst_id,
        "instrument": _clean(inst_raw),
        "instrument_files": files_digest,
        "room": _clean(room_raw) if realism else None,
        "realism": bool(realism),
        "engine": engine_version,
        "sample_rate": int(sr),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


@dataclass
class Plan:
    """Everything render() decides before it renders (also what cached() and the CLI use)."""
    key: str
    midi_path: str
    midi_sha256: str
    inst: dict
    inst_id: str
    room_id: Optional[str]
    room: Optional[dict]
    realism: bool
    engine_version: str
    dir: Path

    @property
    def flac(self) -> Path:
        return self.dir / f"{self.key}.flac"

    @property
    def sidecar(self) -> Path:
        return self.dir / f"{self.key}.json"

    @property
    def progress_file(self) -> Path:
        return self.dir / f"{self.key}.progress.json"

    @property
    def lock_file(self) -> Path:
        return self.dir / f"{self.key}.lock"


def plan(midi_path, instrument_id: Optional[str] = None, *, room: Optional[str] = None, realism: bool = True,
         registry=None, cache_dir_override=None) -> Plan:
    reg = registry if registry is not None else _instruments.load()
    inst_id = instrument_id or reg.default
    if not inst_id:
        raise KeyError("no instrument given and the registry has no default")
    inst = reg.get(inst_id)
    eng = inst.get("engine")
    if eng in _engines.NEVER_RENDERED:
        raise ValueError(f"instrument {inst_id!r} uses {eng!r}, the live PC22 synth: nothing to render")
    room_id = resolve_room(reg, inst, room, realism)
    room_entry = reg.room(room_id) if room_id else None
    room_raw = (reg.data.get("rooms") or {}).get(room_id) if room_id else None
    midi_bytes = Path(midi_path).read_bytes()
    ever = _engines.version(eng, reg.engines_root)
    key = compute_key(midi_bytes, inst_id, reg.raw(inst_id), room_raw, realism, ever,
                      files_digest=instrument_files_digest(inst.get("file")))
    return Plan(key=key, midi_path=str(Path(midi_path).resolve()), midi_sha256=hashlib.sha256(midi_bytes).hexdigest(),
                inst=inst, inst_id=inst_id, room_id=room_id, room=room_entry, realism=bool(realism),
                engine_version=ever, dir=cache_dir(cache_dir_override))


def _read_cached(p: Plan) -> Optional[RenderResult]:
    try:
        if not (p.sidecar.exists() and p.flac.exists()):
            return None
        with open(p.sidecar, encoding="utf-8") as f:
            res = RenderResult.from_json(json.load(f), cached=True)
        res.path = str(p.flac)
        return res
    except (OSError, ValueError, TypeError):
        return None


def cached(midi_path, instrument_id: Optional[str] = None, *, room: Optional[str] = None, realism: bool = True,
           registry=None, cache_dir=None) -> Optional[RenderResult]:
    """The cached RenderResult, or None (never renders)."""
    return _read_cached(plan(midi_path, instrument_id, room=room, realism=realism, registry=registry,
                             cache_dir_override=cache_dir))


def read_progress(key: str, cache_dir_override=None) -> Optional[dict]:
    try:
        with open(cache_dir(cache_dir_override) / f"{key}.progress.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --- locking and progress ----------------------------------------------------------------------------------
@contextlib.contextmanager
def _key_lock(path: Path, timeout: Optional[float] = None):
    """Exclusive flock on ``path``; the holder unlinks it on exit, and a waiter that wakes on an unlinked file
    takes the lock again on the live one (so no lock files are left behind and no two holders overlap)."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if deadline is not None and time.monotonic() > deadline:
                        raise TimeoutError(f"timed out waiting for {path}")
                    time.sleep(0.05)
            try:
                live = os.stat(path).st_ino == os.fstat(fd).st_ino
            except FileNotFoundError:
                live = False
        except BaseException:
            os.close(fd)
            raise
        if live:
            break
        os.close(fd)
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        os.close(fd)


class _ProgressFile:
    """<key>.progress.json, rewritten by a heartbeat thread every HEARTBEAT_S (and on stage changes)."""

    def __init__(self, path: Path, cb: ProgressCb = None):
        self.path = path
        self.cb = cb
        self.state = {"stage": "start", "done_s": 0.0, "total_s": 0.0}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._write()
        self._thread = threading.Thread(target=self._beat, name="piano-render-progress", daemon=True)
        self._thread.start()

    def _write(self) -> None:
        with self._lock:
            state = dict(self.state, pid=os.getpid(), updated=round(time.time(), 3))
        tmp = self.path.with_name(f"{self.path.name}.tmp{os.getpid()}-{threading.get_ident()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, self.path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            self._write()

    def set(self, stage: str, done_s: float = 0.0, total_s: float = 0.0) -> None:
        changed = stage != self.state["stage"]
        with self._lock:
            self.state = {"stage": stage, "done_s": round(float(done_s), 3), "total_s": round(float(total_s), 3)}
        if changed:
            self._write()
        if self.cb is not None:
            try:
                self.cb(dict(self.state))
            except Exception:
                pass

    def stage_cb(self, stage: str) -> Callable[[float, float], None]:
        return lambda done, total: self.set(stage, done, total)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


# --- engines kept loaded between renders (a big library takes seconds to load) ------------------------------
_ENGINE_LOCK = threading.RLock()
_ENGINES: "OrderedDict[str, _engines.Engine]" = OrderedDict()
KEEP_ENGINES = 1


def _engine_for(inst: dict, engines_root) -> "_engines.Engine":
    ek = json.dumps([inst.get("engine"), inst.get("file"), inst.get("preset"), str(engines_root)], sort_keys=True)
    eng = _ENGINES.pop(ek, None)
    if eng is None:
        while len(_ENGINES) >= KEEP_ENGINES:
            _, old = _ENGINES.popitem(last=False)
            old.close()
        eng = _engines.create(inst, engines_root)
    eng.inst = inst
    eng.gamma = float(inst.get("velocity_gamma") or 1.0)
    _ENGINES[ek] = eng
    return eng


def release_engines() -> None:
    """Unload every kept engine (frees the sample memory)."""
    with _ENGINE_LOCK:
        while _ENGINES:
            _, e = _ENGINES.popitem()
            e.close()


# --- render --------------------------------------------------------------------------------------------------
def first_onset_ms(audio: np.ndarray, sr: int, perf: _score.Performance,
                   threshold_dbfs: float = ONSET_THRESHOLD_DBFS) -> Optional[float]:
    """When the first note becomes audible, in ms after its MIDI time: the first sample from 20 ms before the
    note-on that is above threshold_dbfs and 20 dB above the 50 ms before that (so pedal noise or a hall tail
    ahead of the note doesn't count). None if no note or nothing found within 200 ms."""
    t0 = perf.first_note_s
    if t0 is None or audio.size == 0:
        return None
    env = np.max(np.abs(audio), axis=1)
    f0 = int(round(t0 * sr))
    a = max(0, f0 - int(0.020 * sr))
    pre = env[max(0, a - int(0.050 * sr)):a]
    thr = max(10 ** (threshold_dbfs / 20), 10.0 * float(pre.max()) if pre.size else 0.0)
    idx = np.flatnonzero(env[a:f0 + int(0.2 * sr)] > thr)
    if idx.size == 0:
        return None
    return round(float((a + idx[0]) / sr - t0) * 1000.0, 3)


def first_audible_s(audio: np.ndarray, sr: int, threshold_dbfs: float = ONSET_THRESHOLD_DBFS) -> Optional[float]:
    """Time of the first sample above threshold_dbfs (any sound: a pedal thump counts)."""
    if audio.size == 0:
        return None
    idx = np.flatnonzero(np.max(np.abs(audio), axis=1) > 10 ** (threshold_dbfs / 20))
    return round(float(idx[0] / sr), 6) if idx.size else None


def _tmp(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp{os.getpid()}-{threading.get_ident()}")


def render(midi_path, instrument_id: Optional[str] = None, *, room: Optional[str] = None, realism: bool = True,
           registry=None, progress: ProgressCb = None, force: bool = False, cache_dir=None,
           lock_timeout: Optional[float] = None) -> RenderResult:
    """Render (or fetch from the cache) ``midi_path`` on ``instrument_id`` (None = the registry's default).

    room: None = the instrument's own room, "none" = no room at all, else a room id. realism=False skips the realism stage
    (and the room). progress(state) gets {"stage", "done_s", "total_s"} as the progress file does. force=True
    renders again even if cached."""
    reg = registry if registry is not None else _instruments.load()
    p = plan(midi_path, instrument_id, room=room, realism=realism, registry=reg, cache_dir_override=cache_dir)
    if not force:
        hit = _read_cached(p)
        if hit is not None:
            return hit
    p.dir.mkdir(parents=True, exist_ok=True)
    with _key_lock(p.lock_file, timeout=lock_timeout):
        if not force:
            hit = _read_cached(p)            # rendered by whoever held the lock before us
            if hit is not None:
                return hit
        return _render_locked(p, reg, progress)


def _render_locked(p: Plan, reg, progress: ProgressCb) -> RenderResult:
    t_wall, t_cpu = time.monotonic(), time.process_time()
    sr = SAMPLE_RATE
    prog = _ProgressFile(p.progress_file, progress)
    tmp_flac, tmp_json = _tmp(p.flac), _tmp(p.sidecar)
    try:
        prog.set("load")
        perf = _score.load_midi(p.midi_path)
        stats: dict[str, Any] = {}
        with _ENGINE_LOCK:
            engine = _engine_for(p.inst, reg.engines_root)
            prog.set("load", 0.0, perf.duration_s)
            engine.ensure_loaded(sr)
            t1 = time.monotonic()
            audio = engine.render(perf, sr=sr, progress=prog.stage_cb("engine"))
            stats["engine"] = dict(engine.stats)
            stats["engine_s"] = round(time.monotonic() - t1, 3)
            t2 = time.monotonic()
            if p.realism:
                prog.set("realism", 0.0, audio.shape[0] / sr)
                rstats: dict = {}
                audio = _realism_module().apply(audio, sr, perf, p.inst, p.room,
                                                progress=prog.stage_cb("realism"), engine=engine, stats=rstats)
                stats["realism"] = rstats
            else:
                audio = _realism_module().apply_dry(audio, sr, p.inst)      # the instrument's EQ only
            stats["realism_s"] = round(time.monotonic() - t2, 3)
        audio = np.asarray(audio, np.float32)
        if audio.ndim != 2 or audio.shape[1] != 2:
            raise RuntimeError(f"pipeline produced shape {audio.shape}, expected (N, 2)")
        dur = audio.shape[0] / sr
        prog.set("master", 0.0, dur)
        t3 = time.monotonic()
        audio, mstats = _master.process(audio, sr, gain_db=float(p.inst.get("gain_db") or 0.0))
        stats["master_s"] = round(time.monotonic() - t3, 3)
        stats["master"] = mstats.asdict()
        prog.set("write", 0.0, dur)
        t4 = time.monotonic()
        _master.write_flac(str(tmp_flac), audio, sr)
        stats["write_s"] = round(time.monotonic() - t4, 3)
        wall, cpu = time.monotonic() - t_wall, time.process_time() - t_cpu
        stats.update(
            wall_s=round(wall, 3), cpu_s=round(cpu, 3),
            x_realtime_wall=round(dur / wall, 2) if wall > 0 else None,
            x_realtime_cpu=round(dur / cpu, 2) if cpu > 0 else None,
            first_onset_ms=first_onset_ms(audio, sr, perf), first_audible_s=first_audible_s(audio, sr),
            midi_path=p.midi_path, midi_sha256=p.midi_sha256, midi_duration_s=round(perf.duration_s, 3),
            notes=sum(1 for e in perf.events if e.kind == "on"), engine_version=p.engine_version,
            render_version=RENDER_VERSION, created=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        )
        res = RenderResult(path=str(p.flac), sample_rate=sr, duration_s=round(dur, 6),
                           onset_ms=float(p.inst.get("onset_ms") or 0.0), lufs=mstats.lufs,
                           true_peak_dbtp=mstats.true_peak_dbtp, cache_key=p.key, engine=str(p.inst.get("engine")),
                           instrument=p.inst_id, room=p.room_id, realism=p.realism, stats=stats)
        os.replace(tmp_flac, p.flac)
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(res.to_json(), f, indent=1, default=_json_default)
        os.replace(tmp_json, p.sidecar)
        return res
    finally:
        prog.close()
        for t in (tmp_flac, tmp_json):
            with contextlib.suppress(FileNotFoundError):
                t.unlink()


def _json_default(o):
    if isinstance(o, (np.floating,)):
        v = float(o)
        return v if math.isfinite(v) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, Path):
        return str(o)
    return str(o)
