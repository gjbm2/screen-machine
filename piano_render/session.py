"""One rendered test-piano run for show_sender: render (cached), then play it on PC22 on command.

    ~/.venvs/sm-piano/bin/python -m piano_render.session --midi PATH --instrument ID [--pad-s X]

Started by tools/show_sender/show_sender.py (``--test-piano PIECE --instrument ID --output pc22``) from the repo root.

stdout: the protocol only, one JSON object per line, flushed (anything else that writes to fd 1 goes to stderr; logs
are on stderr):
  {"ev": "progress", "stage": "rendering", "step", "done_s", "total_s", "frac"[, "shared": true]}
        while rendering: at most 4 a second, and at least every 0.5 s. step: render.py's stage
        (load|engine|realism|master|write); done_s and total_s: that stage's; frac: the whole render, 0..1, never going
        back. Each stage counts for its expected time (Estimate: this instrument's own renders in the cache, else
        DEFAULT_RATES), and a stage that reports nothing inside it (loading the samples, mastering, writing) moves on
        its expected time up to 90% of it, so the bar never sits still while the work goes on. shared: another
        process is rendering this piece (a background render below, or a prerender) and this one waits for it
        (render.py's per-key lock); that process's progress is passed on. Nothing when the render is cached.
  {"ev": "rendered", "path", "duration_s", "onset_ms", "cached", "render_s"}
        once; then it waits for commands. duration_s: the file's (sample 0 = MIDI time 0, tail included).
  {"ev": "started", "t_audio0", "pad_s", "startup_s"}
        after each "play": t_audio0 = the WSL time.monotonic() at which MIDI time 0 is heard (file sample 0 at PC22's
        DAC, after pad_s of digital silence), from piano_render.pc22.PC22Output.start.
  {"ev": "played", "underruns", "frames", ...}   the audio ended by itself (the player's "done")
  {"ev": "stopped"}                             after "stop": silent (also if nothing was playing)
  {"ev": "error", "why": plain words}           then exit 1
stdin: one command per line:
  play   start (or restart) PC22 playback of the rendered file, pad_s of silence first; a "play" before "rendered"
         waits for it
  stop   silence within ~50 ms (the player's 10 ms fade, then the device buffer); a render still running goes on, and
         the next "play" plays it
  EOF    stop, then exit 0; a render still running goes on in the background (below). SIGTERM, SIGHUP and the
         parent's death (PR_SET_PDEATHSIG) do the same.

Priorities: the render runs in a child process of its own (this module with --render-child, in its own session) at
nice 19 with normal scheduling, single-threaded: someone is waiting for it, so it must get on when the box is busy
(at SCHED_IDLE it got next to nothing then), and nice 19 still yields to the live TV streams. This process is the
playback path (the PCM writer thread and the player's pipe) and keeps normal priority: it leaves SCHED_IDLE if it
inherited it (as far as Linux lets an unprivileged process). pc22.py tracks its player and kills it at exit; every
exit path here stops it.

Background renders (22 Oct: every Stop, re-press or sound change used to kill the render, so the next press began
again from 0). A stop, EOF or kill of this process never kills its render. The child reads its stdin, a pipe from
this process; at EOF (this process closed it, or died) it carries on by itself, so the cache fills and the next press
finds the piece rendered:
  - its stdout and stderr go to ~/.local/state/screen-machine/piano-background.log ($PIANO_SESSION_BG_LOG);
  - if another live piano_render process is already rendering the same piece (its <key>.progress.json says so), it
    leaves the piece to that one and exits at once: one render per key;
  - else it becomes a background render, and at most BG_MAX (2, $PIANO_SESSION_BG_MAX) of those run at once: beyond
    that the oldest is killed, those nobody waits for first (SIGKILL, after checking it is that very process: its
    start time and --render-child);
  - it drops itself, every thread, to SCHED_IDLE once no waited-on render child has wanted its piece for
    BG_IDLE_AFTER_S (10 s, $PIANO_SESSION_BG_IDLE_AFTER_S): a press of the same piece just after a stop waits for it
    at normal scheduling. It never comes back: Linux doesn't let an unprivileged process leave SCHED_IDLE
    (RLIMIT_NICE is 0 here);
  - it gives up after BG_MAX_S (1 h).
  A press that waits for a background render already at SCHED_IDLE watches it: when over TAKEOVER_WINDOW_S it got
  less than 30% of a core while waiting to run more than half the time (/proc/<pid>/schedstat: starved, not merely
  reading samples from disk) and it is less than half done, it is killed and the waiting child renders the piece
  itself at normal scheduling (the bar starts again from there). Prerenders and waited-on renders are never killed.
  A render child whose stdin is not a pipe (an older session gave it /dev/null) dies with its parent, as before.
Every render child, waited for or not, has <cache>/.running/<pid>.json while it runs: {"pid", "start" (its start
time in clock ticks), "key", "midi", "instrument", "background", "t", "since"}.

PIANO_SESSION_GAIN_DB (env, dB): scales the PCM sent to the player (-120 = a silent end-to-end check of the real PC22
path). If pc22.py no longer sends its PCM through ``to_pcm``, a run with it set refuses to play (fails closed).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import signal
import stat
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("piano_render.session")

REPO = Path(__file__).resolve().parent.parent
GAIN_ENV = "PIANO_SESSION_GAIN_DB"
PROGRESS_EVERY_S = 0.25
PROGRESS_KEEPALIVE_S = 0.5          # a progress line at least this often while rendering (the estimate moves)
PAD_MAX_S = 30.0
POLL_S = 0.25
PR_SET_PDEATHSIG = 1
STAGES = ("load", "engine", "realism", "master", "write")    # render.py's stages, in order
QUIET_STAGE_MAX = 0.9               # a stage that reports nothing inside it moves on its expected time up to this
#: expected time of each stage when the cache holds no render of the instrument yet: "load" in seconds, the others in
#: seconds per second of audio. Medians of the 22 Oct renders: the sfizz libraries load in 2.6-16 s (concert-d 14,
#: grand-c5 15, bright-cf3 9) and render at 0.07-0.3 s/s, FluidSynth loads at once and renders at 0.03 s/s; realism
#: 0.01-0.02, master 0.02-0.04 (it is no small part: 17 s of a 42 s Fazioli render), write 0.005.
DEFAULT_RATES: dict = {
    "sfizz": {"load": 12.0, "engine": 0.2, "realism": 0.02, "master": 0.03, "write": 0.006},
    "fluidsynth": {"load": 0.3, "engine": 0.03, "realism": 0.02, "master": 0.03, "write": 0.006},
    None: {"load": 5.0, "engine": 0.15, "realism": 0.02, "master": 0.03, "write": 0.006},
}
HISTORY_N = 12                      # the instrument's latest renders the estimate takes medians of
BG_MAX = 2
BG_MAX_ENV = "PIANO_SESSION_BG_MAX"
BG_IDLE_AFTER_S = 10.0
BG_IDLE_ENV = "PIANO_SESSION_BG_IDLE_AFTER_S"
BG_MAX_S = 3600.0
BG_POLL_S = 0.5
BG_LOG_ENV = "PIANO_SESSION_BG_LOG"
DEFAULT_BG_LOG = Path("~/.local/state/screen-machine/piano-background.log").expanduser()
BG_LOG_MAX_BYTES = 2_000_000        # then it is moved to .1 (one old copy)
RUNNING_DIR = ".running"
HOLDER_FRESH_S = 10.0               # a lock holder outside piano_render counts while its heartbeat is this fresh
TAKEOVER_WINDOW_S = 3.0             # a starved background render is judged over this long
TAKEOVER_MAX_RUN = 0.3              # ...getting less than this share of a core
TAKEOVER_MIN_WAIT = 0.5             # ...while waiting to run more than this share of the time
TAKEOVER_MAX_FRAC = 0.5             # ...and less than this far through


class Proto:
    """The protocol's stdout. Given no stream it keeps fd 1 for itself and points fd 1 (and sys.stdout) at stderr,
    so library chatter can never break a line."""

    def __init__(self, stream=None) -> None:
        if stream is None:
            stream = os.fdopen(os.dup(1), "w", encoding="utf-8")
            os.dup2(2, 1)
            sys.stdout = sys.stderr
        self.f = stream
        self.lock = threading.Lock()
        self.ok = True

    def emit(self, obj: dict) -> bool:
        line = json.dumps(obj, separators=(",", ":"), default=str)
        with self.lock:
            if self.f is None:
                return False
            try:
                self.f.write(line + "\n")
                self.f.flush()
                return True
            except (OSError, ValueError):                 # the reader is gone
                self.ok = False
                return False

    def detach(self) -> None:
        """No reader any more: close the stream; emit() does nothing from now on."""
        with self.lock:
            f, self.f, self.ok = self.f, None, False
        if f is not None:
            try:
                f.close()
            except (OSError, ValueError):
                pass


class _Term(BaseException):
    """SIGTERM/SIGHUP in the main thread: unwind through the finally blocks (they stop the sound)."""


def plain(e: BaseException) -> str:
    """An exception as plain words for the Room page."""
    if isinstance(e, KeyError):
        return str(e.args[0]) if e.args else "unknown name"
    if isinstance(e, FileNotFoundError):
        return f"file not found: {e.filename or e}"
    if isinstance(e, MemoryError):
        return "out of memory"
    text = " ".join(str(e).split())
    return text or type(e).__name__


def _num(v: Any) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def _env_num(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v == v and v >= 0 else default


def _pdeathsig(sig: int) -> None:
    """Ask Linux to send ``sig`` when the parent goes (best effort), and go now if it already has."""
    try:
        import ctypes
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_PDEATHSIG, int(sig), 0, 0, 0)
    except Exception:                                    # noqa: BLE001
        pass
    if os.getppid() == 1:
        os.kill(os.getpid(), sig)


def sched_state() -> dict:
    try:
        pol = os.sched_getscheduler(0)
        name = {getattr(os, "SCHED_IDLE", -1): "idle", getattr(os, "SCHED_OTHER", -2): "other",
                getattr(os, "SCHED_BATCH", -3): "batch"}.get(pol, str(pol))
    except (AttributeError, OSError):
        name = "unknown"
    try:
        nice = os.nice(0)
    except OSError:
        nice = None
    return {"sched": name, "nice": nice}


def leave_idle() -> None:
    """The playback path must not be SCHED_IDLE: leave it if inherited (allowed when RLIMIT_NICE permits)."""
    try:
        if os.sched_getscheduler(0) == os.SCHED_IDLE:
            os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
            log.info("left SCHED_IDLE for playback")
    except (AttributeError, OSError) as e:
        log.warning("playback runs at SCHED_IDLE (inherited; can't leave it: %s)", e)


def foreground_priority() -> None:
    """A render someone waits for: nice 19, keeping the scheduling it has (normal, unless it inherited SCHED_IDLE,
    which Linux won't let it leave). Call it while the process has one thread: threads started later inherit it."""
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass


def idle_all_threads() -> int:
    """SCHED_IDLE and nice 19 on every thread of this process (Linux sets both per thread); how many were set."""
    try:
        tids = [int(t) for t in os.listdir("/proc/self/task")]
    except (OSError, ValueError):
        tids = [threading.get_native_id()]
    n = 0
    for tid in tids:
        try:
            os.sched_setscheduler(tid, os.SCHED_IDLE, os.sched_param(0))
            n += 1
        except (AttributeError, OSError):
            continue
        try:
            os.setpriority(os.PRIO_PROCESS, tid, 19)
        except OSError:
            pass
    return n


# -- processes, the running list and the background bound -----------------------------------------------------------
def _proc(pid: Any) -> Optional[tuple[str, int]]:
    """(state letter, start time in clock ticks) of a process, from /proc/<pid>/stat; None if there is none."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read()
        rest = data[data.rindex(b")") + 2:].split()
        return rest[0].decode("ascii", "replace"), int(rest[19])
    except (OSError, ValueError, IndexError):
        return None


def _cmdline(pid: int) -> bytes:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read()
    except OSError:
        return b""


def _schedstat(pid: Any) -> Optional[tuple[float, float]]:
    """(seconds on a CPU, seconds runnable but waiting for one) of a process's main thread, from /proc."""
    try:
        with open(f"/proc/{int(pid)}/schedstat", encoding="ascii") as f:
            a = f.read().split()
        return int(a[0]) / 1e9, int(a[1]) / 1e9
    except (OSError, ValueError, IndexError, TypeError):
        return None


def _policy(pid: Any) -> Optional[int]:
    try:
        return os.sched_getscheduler(int(pid))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def same_process(pid: Any, start: Any) -> bool:
    """``pid`` is alive (not a zombie) and is the process that started at ``start`` (no pid reuse)."""
    p = _proc(pid)
    return p is not None and p[0] != "Z" and p[1] == start


def _write_json(path: Path, doc: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)


class Running:
    """<cache>/.running/<pid>.json: this render child, while it runs (module docstring)."""

    def __init__(self, cache: "str | os.PathLike", key: str, midi: str, instrument: str) -> None:
        self.dir = Path(cache) / RUNNING_DIR
        self.path = self.dir / f"{os.getpid()}.json"
        me = _proc(os.getpid())
        self.doc = {"pid": os.getpid(), "start": me[1] if me else None, "key": key, "midi": str(midi),
                    "instrument": instrument, "background": False, "t": round(time.time(), 3), "since": None}
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.path, self.doc)

    def background(self) -> None:
        self.doc.update(background=True, since=round(time.time(), 3))
        try:
            _write_json(self.path, self.doc)
        except OSError as e:
            log.warning("couldn't mark %s as a background render: %s", self.path, e)

    def remove(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def running(cache: "str | os.PathLike") -> list[dict]:
    """The live render children's entries (an entry whose process is gone is removed)."""
    d = Path(cache) / RUNNING_DIR
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return []
    out = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or not same_process(doc.get("pid"), doc.get("start")):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        out.append(doc)
    return out


def holder_progress(cache: "str | os.PathLike", key: str) -> Optional[dict]:
    """<key>.progress.json when the process that wrote it (the key's lock holder) still runs, else None: its pid is
    alive and either runs piano_render or kept the file's heartbeat (render.py rewrites it every 0.5 s) going within
    HOLDER_FRESH_S (scripts/piano_prerender_all.sh runs as "python -"). A killed render's file names a dead pid."""
    try:
        doc = json.loads((Path(cache) / f"{key}.progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = doc.get("pid") if isinstance(doc, dict) else None
    p = _proc(pid)
    if p is None or p[0] == "Z":
        return None
    if b"piano_render" not in _cmdline(pid) and not abs(time.time() - _num(doc.get("updated"))) < HOLDER_FRESH_S:
        return None
    return doc


def kill_background(cache: "str | os.PathLike", entry: dict) -> bool:
    """SIGKILL one background render (only if it is still that very render child), then remove its files: its
    running entry, its progress file and its partial outputs (render.py's temp names carry its pid)."""
    pid, start = entry.get("pid"), entry.get("start")
    if not same_process(pid, start) or b"--render-child" not in _cmdline(pid):
        return False
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return False
    end = time.monotonic() + 1.0
    while same_process(pid, start) and time.monotonic() < end:
        time.sleep(0.02)
    cache = Path(cache)
    leftovers = [cache / RUNNING_DIR / f"{pid}.json"]
    key = entry.get("key")
    if isinstance(key, str) and key:
        prog = cache / f"{key}.progress.json"
        try:
            if json.loads(prog.read_text(encoding="utf-8")).get("pid") == pid:
                leftovers.append(prog)
        except (OSError, ValueError, AttributeError):
            pass
        leftovers += list(cache.glob(f"{key}.*.tmp{pid}-*"))
    for f in leftovers:
        try:
            f.unlink()
        except OSError:
            pass
    return True


def enforce_bound(cache: "str | os.PathLike", keep: int, me: int) -> list[int]:
    """At most ``keep`` background renders, counting ``me``: the oldest others beyond that are killed, those whose
    piece no waited-on render child wants first. Returns the pids killed."""
    entries = running(cache)
    bg = [e for e in entries if e.get("background")]
    if len(bg) <= keep:
        return []
    wanted = {e.get("key") for e in entries if not e.get("background")}
    others = sorted((e for e in bg if e.get("pid") != me),
                    key=lambda e: (e.get("key") in wanted, _num(e.get("since"))))
    n, killed = len(bg), []
    for e in others:
        if n <= keep:
            break
        if kill_background(cache, e):
            killed.append(e["pid"])
            log.info("too many background renders: killed pid %d (%s on %s)", e["pid"], Path(str(e.get("midi"))).name,
                     e.get("instrument"))
        n -= 1                                           # killed, or no longer that render: gone either way
    return killed


# -- the progress estimate ------------------------------------------------------------------------------------------
def history_rates(cache: "str | os.PathLike", instrument: str, n: int = HISTORY_N) -> Optional[dict]:
    """Each stage's expected time (DEFAULT_RATES' units) as medians over this instrument's latest renders in the
    cache (their sidecars' stats); None when it has none."""
    try:
        files = [p for p in Path(cache).glob("*.json") if not p.name.endswith(".progress.json")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    rows = []
    for p in files:
        if len(rows) >= n:
            break
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("instrument") != instrument:
                continue
            s = d.get("stats") or {}
            e = s.get("engine") or {}
            audio = _num(e.get("audio_s")) or _num(d.get("duration_s"))
            if audio <= 0:
                continue
            rows.append({"load": float(e["load_s"]), "engine": float(s["engine_s"]) / audio,
                         "realism": float(s["realism_s"]) / audio, "master": float(s["master_s"]) / audio,
                         "write": float(s["write_s"]) / audio})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    if not rows:
        return None
    return {k: float(statistics.median(r[k] for r in rows)) for k in STAGES}


class Estimate:
    """The whole render's fraction done from render.py's progress state: every stage counts for its expected time
    (``rates``, DEFAULT_RATES' units; None: this instrument's history in ``cache``, else the engine's defaults)."""

    def __init__(self, rates: Optional[dict] = None, *, instrument: Optional[str] = None,
                 engine: Optional[str] = None, cache: "str | os.PathLike | None" = None) -> None:
        self._rates = dict(rates) if rates else None
        self.instrument, self.engine, self.cache = instrument, engine, cache
        self.dur: Optional[float] = None                 # the piece's length (the first stage total seen)

    @property
    def rates(self) -> dict:
        if self._rates is None:
            got = history_rates(self.cache, self.instrument) if self.cache is not None and self.instrument else None
            base = DEFAULT_RATES.get(self.engine) or DEFAULT_RATES[None]
            self._rates = {k: max(0.0, float(got[k] if got else base[k])) for k in STAGES}
        return self._rates

    def weights(self, dur: float) -> dict:
        r = self.rates
        return {s: r[s] if s == "load" else r[s] * dur for s in STAGES}

    def frac(self, state: dict, in_stage_s: float = 0.0) -> float:
        stage = state.get("stage")
        done, tot = _num(state.get("done_s")), _num(state.get("total_s"))
        if self.dur is None and tot > 0:
            self.dur = tot
        if stage not in STAGES or not self.dur:
            return 0.0
        w = self.weights(self.dur)
        whole = sum(w.values())
        if whole <= 0:
            return 0.0
        i = STAGES.index(stage)
        before = sum(w[s] for s in STAGES[:i])
        if stage in ("engine", "realism") and tot > 0:
            x = min(1.0, max(0.0, done / tot))
        else:
            x = min(QUIET_STAGE_MAX, max(0.0, in_stage_s) / w[stage]) if w[stage] > 0 else 0.0
        return round(min(1.0, max(0.0, (before + w[stage] * x) / whole)), 4)


class ChildProgress:
    """The render child's progress lines: render.py's callback (``own``), thinned to PROGRESS_EVERY_S per step; a
    ticker that repeats the moving estimate every PROGRESS_KEEPALIVE_S, and while this process has no progress of its
    own (waiting on the key's lock), passes on the lock holder's <key>.progress.json as ``shared``."""

    def __init__(self, proto: Proto, est: Estimate, cache: "str | os.PathLike | None" = None,
                 key: Optional[str] = None) -> None:
        self.proto, self.est, self.cache, self.key = proto, est, cache, key
        self.lock = threading.Lock()
        self.state: Optional[dict] = None
        self.stage: Optional[str] = None
        self.stage_t0 = 0.0
        self.last_emit = -1e18
        self.last_step: Optional[str] = None
        self.best = 0.0
        self.own_seen = False
        self.shared = False
        self.took_over = False
        self._watch: Optional[tuple] = None              # (holder pid, monotonic, its schedstat) for _starved_holder
        self._halt = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def own(self, state: dict) -> None:
        self.own_seen = True
        self._update(dict(state), False)

    def _update(self, state: dict, shared: bool) -> None:
        now = time.monotonic()
        with self.lock:
            if self._halt.is_set():
                return
            step = state.get("stage")
            if step != self.stage:
                self.stage, self.stage_t0 = step, now
            self.state, self.shared = state, shared
            if step == self.last_step and now - self.last_emit < PROGRESS_EVERY_S:
                return
            self._emit(now)

    def _emit(self, now: float) -> None:                 # under self.lock
        st = self.state or {}
        self.best = max(self.best, self.est.frac(st, now - self.stage_t0))
        self.last_emit, self.last_step = now, st.get("stage")
        msg = {"ev": "progress", "stage": "rendering", "step": st.get("stage"), "done_s": st.get("done_s"),
               "total_s": st.get("total_s"), "frac": self.best}
        if self.shared:
            msg["shared"] = True
        self.proto.emit(msg)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._tick, name="progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self.lock:
            self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _tick(self) -> None:
        while not self._halt.wait(PROGRESS_EVERY_S):
            if not self.own_seen and self.key and self.cache is not None:
                doc = holder_progress(self.cache, self.key)
                if doc is not None and doc.get("pid") != os.getpid():
                    self._update({k: doc.get(k) for k in ("stage", "done_s", "total_s")}, True)
                    self._starved_holder(doc.get("pid"))
            now = time.monotonic()
            with self.lock:
                if not self._halt.is_set() and self.state is not None and now - self.last_emit >= PROGRESS_KEEPALIVE_S:
                    self._emit(now)

    def _starved_holder(self, pid: Any) -> None:
        """The process this one waits for, if it is a background render of this piece starved at SCHED_IDLE (it can
        never come back to normal scheduling) and not yet half done: kill it, so this process takes the key's lock and
        renders the piece itself (module docstring)."""
        now, ss = time.monotonic(), _schedstat(pid)
        if ss is None or self.cache is None:
            return
        w = self._watch
        if w is None or w[0] != pid:
            self._watch = (pid, now, ss)
            return
        dt = now - w[1]
        if dt < TAKEOVER_WINDOW_S:
            return
        self._watch = (pid, now, ss)
        run, wait = (ss[0] - w[2][0]) / dt, (ss[1] - w[2][1]) / dt
        if run >= TAKEOVER_MAX_RUN or wait < TAKEOVER_MIN_WAIT or _policy(pid) != getattr(os, "SCHED_IDLE", None):
            return
        with self.lock:
            f = self.est.frac(self.state or {}, now - self.stage_t0)
        if f >= TAKEOVER_MAX_FRAC:
            return
        e = next((e for e in running(self.cache) if e.get("pid") == pid and e.get("background")), None)
        if e is not None and kill_background(self.cache, e):
            with self.lock:
                self.took_over, self.best = True, 0.0
            log.info("the background render of this piece (pid %s) got %.0f%% of a core at SCHED_IDLE (waiting %.0f%%"
                     " of the time), %.0f%% done: rendering it here instead", pid, run * 100, wait * 100, f * 100)


# -- background renders -----------------------------------------------------------------------------------------------
def bg_log_path() -> Path:
    raw = os.environ.get(BG_LOG_ENV)
    return Path(raw).expanduser() if raw and raw.strip() else DEFAULT_BG_LOG


def redirect_output(path: Path) -> None:
    """fd 1 and fd 2 to ``path`` (appending; moved to .1 past BG_LOG_MAX_BYTES), else to /dev/null: never to the
    session's log, which show_service truncates for its next run (a write at the old offset would leave a hole)."""
    try:
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > BG_LOG_MAX_BYTES:
                os.replace(path, path.with_name(path.name + ".1"))
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError:
        fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
    finally:
        os.close(fd)


class Background:
    """The render child's watch on its session (module docstring, Background renders): at EOF on ``fd`` it goes on by
    itself. ``done`` is set by render_child when the render has ended (an EOF after that changes nothing)."""

    def __init__(self, fd: int, proto: Proto, entry: Optional[Running], cache: "str | os.PathLike | None",
                 key: Optional[str], label: str, *, redirect: bool = True) -> None:
        self.fd, self.proto, self.entry, self.cache, self.key, self.label = fd, proto, entry, cache, key, label
        self.redirect = redirect
        self.done = threading.Event()
        self.orphaned = threading.Event()
        self.idle = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="session-watch", daemon=True).start()

    def _run(self) -> None:
        try:
            while os.read(self.fd, 4096):
                pass
        except OSError:
            pass
        if self.done.is_set():
            return
        self.orphaned.set()
        self.go_background()

    def go_background(self) -> None:
        if self.redirect:
            redirect_output(bg_log_path())
        self.proto.detach()
        log.info("the session went away: %s goes on rendering in the background (pid %d)", self.label, os.getpid())
        if self.key and self.cache is not None:
            doc = holder_progress(self.cache, self.key)
            if doc is not None and doc.get("pid") != os.getpid():
                log.info("pid %s is rendering %s already: leaving it to that one", doc.get("pid"), self.label)
                self._exit(0)
                return
        if self.entry is not None:
            self.entry.background()
            enforce_bound(self.cache, int(_env_num(BG_MAX_ENV, BG_MAX)), os.getpid())
        t0 = last_wanted = time.monotonic()
        idle_after = _env_num(BG_IDLE_ENV, BG_IDLE_AFTER_S)
        while not self.done.is_set():
            now = time.monotonic()
            if self._wanted():
                last_wanted = now
            if not self.idle and now - last_wanted >= idle_after:
                n = idle_all_threads()
                self.idle = True
                log.info("nobody is waiting for %s: on at SCHED_IDLE (%d threads)", self.label, n)
            elif self.idle:
                idle_all_threads()                       # threads started since
            if now - t0 >= BG_MAX_S:
                log.warning("%s: still rendering after %.0f s in the background: giving up", self.label, BG_MAX_S)
                self._exit(3)
                return
            self.done.wait(BG_POLL_S)

    def _wanted(self) -> bool:
        """A waited-on render child (not a background one) wants this piece."""
        if not self.key or self.cache is None:
            return False
        me = os.getpid()
        return any(not e.get("background") and e.get("key") == self.key and e.get("pid") != me
                   for e in running(self.cache))

    def _exit(self, rc: int) -> None:
        if self.entry is not None:
            self.entry.remove()
        if self.key and self.cache is not None:
            prog = Path(self.cache) / f"{self.key}.progress.json"
            try:
                if json.loads(prog.read_text(encoding="utf-8")).get("pid") == os.getpid():
                    prog.unlink()
            except (OSError, ValueError, AttributeError):
                pass
        try:
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        os._exit(rc)


# -- the render child ---------------------------------------------------------------------------------------------
def render_child(midi: str, instrument: str, proto: Proto, *, lower: bool = True,
                 render_fn: Optional[Callable[..., Any]] = None, plan_fn: Optional[Callable[..., Any]] = None,
                 stdin_fd: Optional[int] = None, redirect: bool = True) -> int:
    """--render-child: render (or find in the cache) at nice 19 (``lower``); progress and the result as protocol
    lines on ``proto``. ``stdin_fd``: the session's pipe (EOF = go on in the background). ``plan_fn`` (default
    render.plan, or none with a ``render_fn`` of the caller's) gives the cache key for the shared progress, the
    running list and the background rules. Returns the exit code."""
    if lower:
        foreground_priority()
    pri = sched_state()
    t = time.monotonic()
    if render_fn is None:
        from . import render as _render
        render_fn = _render.render
        plan_fn = plan_fn or _render.plan
    key: Optional[str] = None
    cache: Optional[Path] = None
    engine: Optional[str] = None
    if plan_fn is not None:
        try:
            p = plan_fn(midi, instrument)
            key, cache, engine = str(p.key), Path(p.dir), (p.inst or {}).get("engine")
        except Exception:                                # noqa: BLE001  render_fn says what is wrong
            pass
    entry: Optional[Running] = None
    if key and cache is not None:
        try:
            entry = Running(cache, key, midi, instrument)
        except OSError as e:
            log.warning("couldn't list this render in %s: %s", cache / RUNNING_DIR, e)
    label = f"{Path(midi).name} on {instrument}"
    prog = ChildProgress(proto, Estimate(instrument=instrument, engine=engine, cache=cache), cache, key)
    bg: Optional[Background] = None
    if stdin_fd is not None:
        bg = Background(stdin_fd, proto, entry, cache, key, label, redirect=redirect)
        bg.start()
    prog.start()
    err: Optional[BaseException] = None
    res: Any = None
    try:
        res = render_fn(midi, instrument, progress=prog.own)
    except Exception as e:                               # noqa: BLE001  one plain line, then exit 1
        log.exception("render %s on %s", midi, instrument)
        err = e
    finally:
        prog.stop()
        if bg is not None:
            bg.done.set()
        if entry is not None:
            entry.remove()
    if err is not None:
        proto.emit({"ev": "error", "why": f"couldn't render {Path(midi).name} on {instrument}: {plain(err)}"})
        return 1
    render_s = round(time.monotonic() - t, 3)
    proto.emit({"ev": "rendered", "path": str(res.path), "duration_s": float(res.duration_s),
                "onset_ms": float(res.onset_ms or 0.0), "cached": bool(getattr(res, "cached", False)),
                "render_s": render_s, **pri})
    if bg is not None and bg.orphaned.is_set():
        log.info("rendered %s in the background (%.1f s%s): %s", label, render_s,
                 ", at SCHED_IDLE" if bg.idle else "", res.path)
    return 0


# -- the gain (PIANO_SESSION_GAIN_DB) -----------------------------------------------------------------------------
def apply_gain(pc22mod, gain_db: Optional[float]) -> Optional[float]:
    """Scale every block pc22.py sends to the player by gain_db (patches its ``to_pcm`` in this process). Raises
    RuntimeError when pc22.py no longer sends its PCM through to_pcm (never play unscaled when asked not to)."""
    if gain_db is None:
        return None
    code = getattr(getattr(getattr(pc22mod, "_Session", None), "_write", None), "__code__", None)
    if code is None or "to_pcm" not in code.co_names:
        raise RuntimeError(f"{GAIN_ENV} is set but pc22.py no longer sends its PCM through to_pcm: not playing")
    import numpy as np
    g = float(10.0 ** (float(gain_db) / 20.0))
    orig = getattr(pc22mod.to_pcm, "_unscaled", pc22mod.to_pcm)

    def to_pcm(block, fmt):
        return orig(np.asarray(block, dtype=np.float32) * np.float32(g), fmt)

    to_pcm._unscaled = orig                              # type: ignore[attr-defined]
    to_pcm.gain = g                                      # type: ignore[attr-defined]
    pc22mod.to_pcm = to_pcm
    log.info("%s=%g: the PCM sent to the player is scaled by %.3g", GAIN_ENV, gain_db, g)
    return g


def gain_from_env() -> Optional[float]:
    raw = os.environ.get(GAIN_ENV)
    if raw is None or not raw.strip():
        return None
    v = float(raw)                                       # ValueError: refused by main (fails closed)
    if v != v:
        raise ValueError(raw)
    return v


# -- the session ----------------------------------------------------------------------------------------------------
def _default_output():
    from . import pc22
    return pc22.PC22Output()


class Session:
    """The playback process: the render child, the commands, one PC22Output. run() returns the exit code."""

    def __init__(self, midi: str, instrument: str, pad_s: float = 0.0, *, proto: Proto,
                 output_factory: Optional[Callable[[], Any]] = None,
                 render_argv: Optional[list[str]] = None) -> None:
        self.midi, self.instrument = str(midi), str(instrument)
        self.pad_s = min(PAD_MAX_S, max(0.0, float(pad_s)))
        self.proto = proto
        self.output_factory = output_factory or _default_output
        self.render_argv = render_argv or [sys.executable, "-m", "piano_render.session", "--render-child",
                                           "--midi", self.midi, "--instrument", self.instrument]
        self.q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.rendered: Optional[dict] = None
        self.render_proc: Optional[subprocess.Popen] = None
        self.out: Any = None
        self.gen = 0                                     # bumped by every play and stop: a stale player is ignored
        self.pending_play = False

    # -- the render child --
    def _start_render(self) -> None:
        try:
            self.render_proc = subprocess.Popen(self.render_argv, cwd=str(REPO), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, start_new_session=True)
        except OSError as e:
            self.q.put(("render", {"ev": "error", "why": f"couldn't start the renderer: {plain(e)}"}))
            return
        threading.Thread(target=self._read_render, args=(self.render_proc,), name="render-out", daemon=True).start()

    def _read_render(self, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            try:
                msg = json.loads(line) if line else None
            except ValueError:
                log.warning("renderer: not a protocol line: %r", line[:200])
                continue
            if isinstance(msg, dict):
                self.q.put(("render", msg))
        self.q.put(("render_eof", proc.wait()))

    def _release_render(self) -> None:
        """Let go of the render child (EOF on its stdin): a render still running goes on in the background."""
        p = self.render_proc
        if p is not None and p.stdin is not None and not p.stdin.closed:
            try:
                p.stdin.close()
            except OSError:
                pass

    # -- commands --
    def _read_cmds(self, f) -> None:
        try:
            for line in iter(f.readline, ""):
                self.q.put(("cmd", line.strip()))
        except (OSError, ValueError):
            pass
        self.q.put(("eof", None))

    def _play(self) -> None:
        self.gen += 1
        if self.out is None:
            self.out = self.output_factory()
        threading.Thread(target=self._play_worker, args=(self.gen, self.rendered["path"]), name="play",
                         daemon=True).start()

    def _play_worker(self, gen: int, path: str) -> None:
        out = self.out
        t = time.monotonic()
        try:
            t0 = out.start(path, lead_in_ms=self.pad_s * 1000.0)
        except Exception as e:                           # noqa: BLE001  PC22Error and anything else
            self.q.put(("play_error", (gen, f"couldn't play on PC22: {plain(e)}")))
            return
        if gen != self.gen:                              # a stop (or another play) came during the start
            out.stop()
            return
        self.proto.emit({"ev": "started", "t_audio0": t0, "pad_s": self.pad_s,
                         "startup_s": round(time.monotonic() - t, 3)})
        out.wait()
        s = getattr(out, "session", None)
        self.q.put(("played", (gen, dict(getattr(out, "done", None) or {}) or None, getattr(s, "error", None))))

    def stop(self) -> None:
        """Silence. A render still running goes on (the next play plays it)."""
        self.gen += 1
        self.pending_play = False
        if self.out is not None:
            self.out.stop()

    def shutdown(self) -> None:
        self.gen += 1
        try:
            if self.out is not None:
                self.out.stop()
        finally:
            self._release_render()

    def handle(self, kind: str, val: Any) -> Optional[int]:
        """One event; returns an exit code to end the run."""
        if kind == "cmd":
            if val == "play":
                if self.rendered is not None:
                    self._play()
                else:
                    self.pending_play = True
                    if self.render_proc is None:
                        self._start_render()
            elif val == "stop":
                self.stop()
                self.proto.emit({"ev": "stopped"})
            elif val:
                log.warning("unknown command %r", val[:80])
        elif kind == "eof":
            return 0
        elif kind == "render":
            ev = val.get("ev")
            if ev == "progress":
                self.proto.emit(val)
            elif ev == "rendered":
                self.rendered = val
                log.info("rendered %s on %s: %s (%.1f s, %s, %.1f s)", Path(self.midi).name, self.instrument,
                         val.get("path"), float(val.get("duration_s") or 0.0),
                         "cached" if val.get("cached") else "new", float(val.get("render_s") or 0.0))
                self.proto.emit(val)
                if self.pending_play:
                    self.pending_play = False
                    self._play()
            elif ev == "error":
                self.proto.emit({"ev": "error", "why": str(val.get("why") or "the render failed")})
                return 1
        elif kind == "render_eof":
            if self.rendered is None:
                self.proto.emit({"ev": "error", "why": f"the renderer stopped (exit code {val}) before it finished"})
                return 1
        elif kind == "played":
            gen, done, err = val
            if gen == self.gen:
                if done is None and err:
                    self.proto.emit({"ev": "error", "why": f"PC22 playback stopped: {err}"})
                    return 1
                self.proto.emit({"ev": "played", **(done or {})})
        elif kind == "play_error":
            gen, why = val
            if gen == self.gen:
                self.proto.emit({"ev": "error", "why": why})
                return 1
        return None

    def run(self, cmds) -> int:
        threading.Thread(target=self._read_cmds, args=(cmds,), name="cmds", daemon=True).start()
        self._start_render()
        try:
            while True:
                try:
                    kind, val = self.q.get(timeout=POLL_S)
                except queue.Empty:
                    continue
                rc = self.handle(kind, val)
                if rc is not None:
                    return rc
        finally:
            self.shutdown()


# -- main -------------------------------------------------------------------------------------------------------------
def _is_pipe(fd: int) -> bool:
    try:
        return stat.S_ISFIFO(os.fstat(fd).st_mode)
    except OSError:
        return False


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m piano_render.session", description=__doc__.split("\n")[0])
    ap.add_argument("--midi", required=True, help="the .mid to render and play")
    ap.add_argument("--instrument", required=True, help="instrument id (config/piano/instruments.json)")
    ap.add_argument("--pad-s", type=float, default=0.0, help="digital silence before MIDI time 0 on each play")
    ap.add_argument("--render-child", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s piano-session %(levelname)s %(message)s")
    proto = Proto()
    if a.render_child:
        if _is_pipe(0):                                  # the session's pipe: EOF = on in the background
            return render_child(a.midi, a.instrument, proto, stdin_fd=0)
        _pdeathsig(signal.SIGKILL)                       # an older session (stdin /dev/null): die with it, as before
        return render_child(a.midi, a.instrument, proto)
    _pdeathsig(signal.SIGTERM)
    leave_idle()

    def _term(signum, _frame):
        raise _Term(signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _term)
    try:
        gain_db = gain_from_env()
    except ValueError:
        proto.emit({"ev": "error", "why": f"{GAIN_ENV}={os.environ.get(GAIN_ENV)!r} is not a number of dB"})
        return 1
    try:
        from . import pc22                               # now, while the render runs: the first play starts sooner
    except ImportError as e:
        proto.emit({"ev": "error", "why": f"PC22 playback can't load: {plain(e)}"})
        return 1
    if gain_db is not None:
        try:
            apply_gain(pc22, gain_db)
        except RuntimeError as e:
            proto.emit({"ev": "error", "why": str(e)})
            return 1
    s = Session(a.midi, a.instrument, a.pad_s, proto=proto)
    try:
        return s.run(sys.stdin)
    except _Term:
        return 0
    finally:
        s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
