"""Test sources and the sender list (README "Stage 3", S3.10): the brain component ``sources``.

- **Senders, hot-reloaded from show.yaml.** On ``configure()`` and on every test start or stop it builds the
  effective sender list and hands it to the receiver (``ctx.set_senders``) when it changed:
  - the static senders (``--sender`` / SHOW_SENDERS) whose names show.yaml doesn't define;
  - plus show.yaml's senders that are not ``on_demand``;
  - plus the ``on_demand`` senders whose test is running.
  Each becomes ``Sender(name, host=hosts[0], port, streams, hosts=tuple(hosts), on_demand)``: ``hosts`` is the
  ordered fallback list the receiver tries (device addresses live in config/room/, never in code). A bad entry is
  skipped and listed in ``errors``.
- **Test sources** (``POST/DELETE/GET /api/v1/test``): ``piano`` plays a .mid (or a built-in phrase) through 98's
  ``tools/show_sender/show_sender.py --test-piano``; ``audio`` plays a music file through ``show_service.test_audio``
  as the stream ``test-audio``. Both are children of show_service on localhost, bound to 127.0.0.1 on their show.yaml
  sender's port, and are subscribed only while they run (``on_demand``). While a source with ``rank_first: true``
  plays, its stream outranks the room's (``ctx.set_priority``).
- **Children** are started with ``start_new_session=True``, ``cwd=REPO_ROOT`` and ``PYTHONUNBUFFERED=1``, logging to
  ``STATE_DIR/test-<source>.log`` (truncated at each start), through PDEATH_SHIM (they die with show_service). States:
  ``starting`` until the stream is ``streaming`` in ``ctx.health()``, then ``playing``; ``stopping`` after SIGTERM
  (SIGKILL to its whole process group 2 s later, so test audio's ffmpeg goes too); ``failed`` when it exited by
  itself (``error`` = its last 5 log lines); ``off``. Only the Popen objects made here (and the process groups they
  lead) are ever signalled. A piece switch stops the running child and starts the new one once the old has let go of
  the port.

Nothing here branches on a source's name: ``PLAYERS`` (built from contract.TEST_SOURCE_PLAYERS) maps the contract's
test-source words to the program that plays them; senders, ports, streams, folders and ranking are show.yaml's. Everything runs on show-brain and never blocks (spawning is a fork/exec; exits are polled), except
``stop()`` at shutdown, which waits at most 2 s for the children.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import pathlib
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Collection, Optional

from . import contract
from .contract import Sender

log = logging.getLogger("show.sources")

MIDI_EXTS = (".mid", ".midi")
AUDIO_EXTS = (".mp3", ".ogg", ".opus", ".flac", ".wav", ".m4a")
KILL_AFTER_S = 2.0                     # SIGTERM, then SIGKILL this long after
FAST_POLL_S = 0.1                      # while a child is starting/stopping (else the 1 Hz tick)
FAST_POLL_FOR_S = 10.0                 # a "starting" child is fast-polled this long, then left to the tick
NO_HEALTH_PLAYING_S = 2.0              # without ctx.health, alive this long counts as playing
LOG_TAIL_LINES = 5
LOG_HEADER = "# show_service starts: "            # the first line of a child's log (left out of ``error``)
LOCAL_BIND = "127.0.0.1"
RUNNING = ("starting", "playing")
PIANO_REPLAY = contract.REPO_ROOT / "stream_service" / "piano_replay.py"   # PHRASE_LABELS, read by file path
NAME_OK = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

#: Each player kind: the show.yaml key of its folder, the files it lists, and the folder when show.yaml leaves it out.
KINDS: dict[str, dict[str, Any]] = {
    "midi": {"dir_key": "pieces", "exts": MIDI_EXTS, "default_dir": contract.TEST_PIANO_DIR},
    "audio": {"dir_key": "music", "exts": AUDIO_EXTS, "default_dir": contract.TEST_MUSIC_DIR},
}
#: How each test source is played: contract.TEST_SOURCE_PLAYERS (the kind and the documented defaults) joined with its
#: kind's folder and files. ``default_*`` are used only when show.yaml leaves ``tests.<source>`` (or its sender) out.
PLAYERS: dict[str, dict[str, Any]] = {
    src: {"kind": p["kind"], **KINDS[p["kind"]], "default_sender": p["sender"], "default_port": p["port"],
          "default_stream": p["stream"]}
    for src, p in contract.TEST_SOURCE_PLAYERS.items()}
assert tuple(PLAYERS) == contract.TEST_SOURCES
#: The children's parent-death guard (README S3.10): run as ``python -c PDEATH_SHIM <show_service pid> <argv...>``, it
#: asks Linux to SIGKILL it when show_service dies (PR_SET_PDEATHSIG survives the exec), checks the parent is still
#: there (it may have died before the prctl), then execs the real program. A crash, a SIGKILL or a slow stop of
#: show_service can then never leave a test child holding its UDP port.
PDEATH_SHIM = ("import ctypes, os, signal, sys\n"
               "try:\n"
               "    ctypes.CDLL(None, use_errno=True).prctl(1, int(signal.SIGKILL), 0, 0, 0)\n"
               "except Exception:\n"
               "    pass\n"
               "if os.getppid() != int(sys.argv[1]):\n"
               "    sys.exit(0)\n"
               "os.execv(sys.argv[2], sys.argv[2:])\n")


# -- senders (pure) ---------------------------------------------------------------------------------------------------
def parse_senders(settings: dict) -> tuple[list[Sender], list[str]]:
    """show.yaml ``senders`` -> (valid Senders in file order, problems). Bad entries are skipped."""
    raw = (settings or {}).get("senders")
    if raw is None:
        return [], []
    if not isinstance(raw, dict):
        return [], ["senders: not a mapping (ignored)"]
    out, errors = [], []
    for name, spec in raw.items():
        where = f"senders.{name}"
        if not isinstance(name, str) or not name or not set(name) <= NAME_OK:
            errors.append(f"{where}: bad name (skipped)")
            continue
        if not isinstance(spec, dict):
            errors.append(f"{where}: not a mapping (skipped)")
            continue
        hosts = spec.get("hosts", spec.get("host"))
        if isinstance(hosts, str):
            hosts = [hosts]
        if (not isinstance(hosts, list) or not hosts
                or not all(isinstance(h, str) and h.strip() and " " not in h.strip() for h in hosts)):
            errors.append(f"{where}.hosts: needs a list of host names or addresses (skipped)")
            continue
        port = spec.get("port", 6980)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(f"{where}.port: {port!r} is not a UDP port (skipped)")
            continue
        streams = spec.get("streams")
        if isinstance(streams, str):
            streams = [streams]
        if (not isinstance(streams, list) or not streams
                or not all(isinstance(s, str) and s and set(s) <= NAME_OK for s in streams)):
            errors.append(f"{where}.streams: needs a list of stream names (skipped)")
            continue
        on_demand = spec.get("on_demand", False)
        if not isinstance(on_demand, bool):
            errors.append(f"{where}.on_demand: {on_demand!r} is not true/false (skipped)")
            continue
        hs = tuple(h.strip() for h in hosts)
        out.append(Sender(name, hs[0], port, tuple(streams), hosts=hs, on_demand=on_demand))
    return out, errors


def senders_from_settings(settings: dict, static: tuple[Sender, ...],
                          running: Collection[str]) -> tuple[Sender, ...]:
    """The effective sender list: static senders show.yaml doesn't define, show.yaml's always-on senders, and the
    on_demand ones named in ``running`` (sender names whose test is running). Pure."""
    yaml_senders, _ = parse_senders(settings)
    defined = {s.name for s in yaml_senders}
    eff = [s for s in static if s.name not in defined]
    eff += [s for s in yaml_senders if not s.on_demand or s.name in running]
    return tuple(eff)


# -- pieces ------------------------------------------------------------------------------------------------------------
_phrases: Optional[dict[str, str]] = None
_phrases_error: Optional[str] = None


def phrase_labels() -> dict[str, str]:
    """piano_replay.PHRASE_LABELS, loaded once by file path ({} if that fails)."""
    global _phrases, _phrases_error
    if _phrases is None:
        try:
            spec = importlib.util.spec_from_file_location("_show_sources_piano_replay", PIANO_REPLAY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)                       # type: ignore[union-attr]
            _phrases = {str(k): str(v) for k, v in dict(mod.PHRASE_LABELS).items()}
        except Exception as e:                                 # noqa: BLE001  degrade: no built-in phrases
            _phrases, _phrases_error = {}, f"piano phrases unavailable: {type(e).__name__}: {e}"
    return _phrases


def _label(stem: str) -> str:
    text = " ".join(stem.replace("_", " ").split())
    return text[:1].upper() + text[1:]


def _files(folder: pathlib.Path, exts: tuple[str, ...]) -> list[pathlib.Path]:
    try:
        return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts and
                       not p.name.startswith(".")), key=lambda p: p.name.lower())
    except OSError:
        return []


def midi_pieces(folder: pathlib.Path) -> list[contract.PieceInfo]:
    out: list[contract.PieceInfo] = [{"id": f"file:{p.name}", "label": _label(p.stem), "kind": "file"}
                                     for p in _files(folder, MIDI_EXTS)]
    out += [{"id": k, "label": v, "kind": "phrase"} for k, v in phrase_labels().items()]
    return out


def audio_pieces(folder: pathlib.Path) -> list[contract.PieceInfo]:
    """Audio files in ``folder``, labelled from its index.json (tools/show/fetch_test_music.py) where it knows them."""
    by_file: dict[str, dict] = {}
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        if isinstance(index, dict):
            by_file = {e["file"]: e for e in index.values() if isinstance(e, dict) and isinstance(e.get("file"), str)}
    except (OSError, ValueError):
        pass
    out: list[contract.PieceInfo] = []
    for p in _files(folder, AUDIO_EXTS):
        e = by_file.get(p.name, {})
        info: contract.PieceInfo = {"id": f"file:{p.name}", "label": str(e.get("title") or _label(p.stem)),
                                    "kind": "file"}
        for key in ("genre", "artist", "license"):
            if isinstance(e.get(key), str) and e[key]:
                info[key] = e[key]                             # type: ignore[literal-required]
        if isinstance(e.get("duration_s"), (int, float)) and not isinstance(e.get("duration_s"), bool):
            info["duration_s"] = float(e["duration_s"])
        out.append(info)
    return out


# -- the component -----------------------------------------------------------------------------------------------------
class _Child:
    """One requested run of a test source: pending (proc None) until the port is free, then a process."""

    def __init__(self, source: str, piece: str, label: str, sender: Sender, stream: str, now: float) -> None:
        self.source, self.piece, self.label = source, piece, label
        self.sender, self.stream = sender, stream
        self.proc: Optional[subprocess.Popen] = None
        self.state = "starting"
        self.since_s = now
        self.started_s: Optional[float] = None
        self.term_s: Optional[float] = None
        self.killed = False
        self.error: Optional[str] = None
        self.log_path: Optional[pathlib.Path] = None

    def set(self, state: str, now: float) -> None:
        if state != self.state:
            self.state, self.since_s = state, now


def _kill(proc: subprocess.Popen) -> None:
    """SIGKILL a child and its process group (it leads one: start_new_session), so test audio's ffmpeg goes too."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


class Sources:
    """Component ``sources`` (tick 1 Hz): the sender list and the two test children."""

    name = "sources"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, python: str = sys.executable,
                 piano_script: pathlib.Path = contract.TEST_PIANO_SCRIPT,
                 audio_cmd: Optional[list[str]] = None, log_dir: pathlib.Path = contract.STATE_DIR,
                 clock: Callable[[], float] = time.monotonic,
                 popen: Callable[..., subprocess.Popen] = subprocess.Popen, pdeath: bool = True) -> None:
        """``pdeath``: run each child through PDEATH_SHIM (off only for a ``popen`` that can't take it)."""
        self.ctx = ctx
        self.pdeath = bool(pdeath)
        self.python, self.piano_script = python, pathlib.Path(piano_script)
        self.audio_cmd = list(audio_cmd) if audio_cmd else [python, "-m", "show_service.test_audio"]
        self.log_dir = pathlib.Path(log_dir)
        self.clock, self.popen = clock, popen
        self.settings: dict = {}
        self._cur: dict[str, Optional[_Child]] = {s: None for s in PLAYERS}
        self._retiring: list[_Child] = []
        self._applied: Optional[tuple[Sender, ...]] = None
        self._prio_applied: Optional[tuple[str, ...]] = None
        self._sender_errors: list[str] = []
        self._apply_errors: list[str] = []
        self._pieces: Optional[dict[str, list[contract.PieceInfo]]] = None
        self._pieces_at = -1e18
        self._fast_handle: Any = None
        self._wanted: tuple[tuple[Sender, ...], tuple[str, ...]] = ((), ())
        self._snap: dict = {}
        self._rebuild()

    # -- Component -----------------------------------------------------------------------------------------------------
    def provides(self) -> dict[str, Any]:
        return {}

    def configure(self, settings: dict) -> None:
        self.settings = settings if isinstance(settings, dict) else {}
        self._pieces = None
        self._apply()
        self._rebuild()

    def restore(self, saved: dict) -> None:
        """Nothing persists: a restart stops the tests."""

    def start(self) -> None:
        self._apply()
        self._rebuild()

    def tick(self, now_s: float) -> None:
        self._poll(self.clock())

    def snapshot(self) -> dict:
        return self._snap

    def persist(self) -> Optional[dict]:
        return None

    def stop(self) -> None:
        """Shutdown: SIGTERM every child made here, wait up to KILL_AFTER_S, then SIGKILL what is left."""
        if self._fast_handle is not None:
            self._fast_handle.cancel()
            self._fast_handle = None
        now = self.clock()
        children = [c for c in list(self._cur.values()) + self._retiring if c is not None and c.proc is not None]
        for c in children:
            self._terminate(c, now)
        deadline = time.monotonic() + KILL_AFTER_S
        for c in children:
            try:
                c.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _kill(c.proc)
                try:
                    c.proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    log.error("test %s: pid %s didn't exit after SIGKILL", c.source, c.proc.pid)
        self._retiring = []
        for c in self._cur.values():
            if c is not None and c.state in RUNNING + ("stopping",):
                c.set("off", now)
        self._apply()
        self._rebuild()

    # -- API (show-brain) ----------------------------------------------------------------------------------------------
    def pieces(self) -> dict[str, list[contract.PieceInfo]]:
        now = self.clock()
        if self._pieces is None or now - self._pieces_at >= contract.RELOAD_S:
            out: dict[str, list[contract.PieceInfo]] = {}
            for source, player in PLAYERS.items():
                folder = self._folder(source)
                out[source] = midi_pieces(folder) if player["kind"] == "midi" else audio_pieces(folder)
            self._pieces, self._pieces_at = out, now
        return self._pieces

    def start_test(self, source: str, piece: Optional[str] = None) -> contract.SourceStatus:
        """Start (or switch) a test source. ValueError("unknown_source" | "unknown_piece")."""
        if source not in PLAYERS:
            raise ValueError("unknown_source")
        choices = self.pieces()[source]
        if piece is None:
            if not choices:
                raise ValueError("unknown_piece")
            info = choices[0]
        else:
            info = next((p for p in choices if p["id"] == piece), None)
            if info is None:
                raise ValueError("unknown_piece")
        now = self.clock()
        old = self._cur.get(source)
        if old is not None and old.proc is not None and old.proc.poll() is None:
            self._terminate(old, now)
            self._retiring.append(old)
        sender, stream = self._test_sender(source)
        self._cur[source] = _Child(source, info["id"], info["label"], sender, stream, now)
        log.info("test %s: %s (%s)", source, info["label"], info["id"])
        self._poll(now)
        self._apply()
        self._rebuild()
        self._schedule()
        return self._status(source, now)

    def stop_test(self, source: Optional[str] = None) -> list[str]:
        """Stop one source (or all); returns the sources that were running."""
        now = self.clock()
        stopped = []
        for src, c in self._cur.items():
            if c is None or (source is not None and src != source):
                continue
            if c.state in RUNNING:
                stopped.append(src)
                if c.proc is not None and c.proc.poll() is None:
                    self._terminate(c, now)
                    c.set("stopping", now)
                else:
                    c.set("off", now)
                log.info("test %s: stopped", src)
            elif c.state == "failed":
                c.set("off", now)
        self._poll(now)
        self._apply()
        self._rebuild()
        self._schedule()
        return stopped

    def status(self) -> dict[str, contract.SourceStatus]:
        now = self.clock()
        return {s: self._status(s, now) for s in PLAYERS}

    # -- internals -----------------------------------------------------------------------------------------------------
    def _tests_cfg(self, source: str) -> dict:
        tests = self.settings.get("tests")
        cfg = tests.get(source) if isinstance(tests, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def _folder(self, source: str) -> pathlib.Path:
        raw = self._tests_cfg(source).get(PLAYERS[source]["dir_key"])
        if not isinstance(raw, str) or not raw.strip():
            return pathlib.Path(PLAYERS[source]["default_dir"])
        p = pathlib.Path(raw.strip()).expanduser()
        return p if p.is_absolute() else contract.REPO_ROOT / p

    def _test_sender(self, source: str) -> tuple[Sender, str]:
        """The show.yaml sender this source plays as (else the contract's localhost default), and its stream."""
        player = PLAYERS[source]
        name = self._tests_cfg(source).get("sender") or player["default_sender"]
        yaml_senders, _ = parse_senders(self.settings)
        for s in yaml_senders:
            if s.name == name:
                return s, s.streams[0]
        stream = player["default_stream"]
        return (Sender(str(name), "localhost", player["default_port"], (stream,), hosts=("localhost",),
                       on_demand=True), stream)

    def _argv(self, c: _Child) -> list[str]:
        port = str(c.sender.port)
        if PLAYERS[c.source]["kind"] == "midi":
            piece = c.piece
            if piece.startswith("file:"):
                folder = self._folder(c.source)
                try:
                    same = folder.resolve() == contract.TEST_PIANO_DIR.resolve()
                except OSError:
                    same = False
                if not same:                                   # show_sender resolves file: ids in its own library
                    piece = str(folder / piece[5:])
            return [self.python, str(self.piano_script), "--test-piano", piece, "--port", port,
                    "--bind", LOCAL_BIND]
        path = self._folder(c.source) / c.piece[5:]
        return [*self.audio_cmd, "--file", str(path), "--port", port, "--bind", LOCAL_BIND, "--stream", c.stream]

    def _spawn(self, c: _Child, now: float) -> None:
        argv = self._argv(c)
        c.log_path = self.log_dir / f"test-{c.source}.log"
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(c.log_path, "wb") as logf:               # truncated at each start
                logf.write((LOG_HEADER + " ".join(argv) + "\n").encode("utf-8", "replace"))
                logf.flush()
                run = [self.python, "-c", PDEATH_SHIM, str(os.getpid()), *argv] if self.pdeath else argv
                c.proc = self.popen(run, cwd=str(contract.REPO_ROOT), env=env, stdin=subprocess.DEVNULL,
                                    stdout=logf, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        except OSError as e:
            c.error = f"couldn't start: {e}"
            c.set("failed", now)
            log.warning("test %s: %s", c.source, c.error)
            return
        c.started_s = now
        log.info("test %s: pid %d: %s", c.source, c.proc.pid, " ".join(argv))

    def _terminate(self, c: _Child, now: float) -> None:
        if c.proc is None or c.proc.poll() is not None or c.term_s is not None:
            return                                             # not running, or SIGTERM already sent
        try:
            c.proc.terminate()
        except OSError:
            pass
        c.term_s = now

    def _tail(self, c: _Child) -> str:
        try:
            with open(c.log_path, "rb") as f:                   # type: ignore[arg-type]
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 8192))
                lines = f.read().decode("utf-8", "replace").splitlines()
        except (OSError, TypeError):
            lines = []
        lines = [ln for ln in lines if ln.strip() and not ln.startswith(LOG_HEADER)]
        return "\n".join(lines[-LOG_TAIL_LINES:])

    def _stream_live(self, c: _Child, now: float) -> bool:
        health = self.ctx.health
        if health is None:
            return c.started_s is not None and now - c.started_s >= NO_HEALTH_PLAYING_S
        try:
            h = health() or {}
            st = (h.get("streams") or {}).get(c.stream) or {}
        except Exception:                                      # noqa: BLE001
            return False
        if st.get("state") not in ("streaming", "quiet"):
            return False
        t_ms, age_ms = h.get("t_ms"), (st.get("net") or {}).get("age_ms")
        if isinstance(t_ms, (int, float)) and isinstance(age_ms, (int, float)) and c.started_s is not None:
            return (t_ms - age_ms) / 1000.0 >= c.started_s     # a packet arrived after this child started
        return True

    def _poll(self, now: float) -> None:
        before = self._running_key()
        for c in list(self._retiring):
            if c.proc.poll() is not None:
                self._retiring.remove(c)
            elif c.term_s is not None and now - c.term_s >= KILL_AFTER_S and not c.killed:
                _kill(c.proc)
                c.killed = True
        for source, c in self._cur.items():
            if c is None:
                continue
            if c.state == "starting" and c.proc is None:
                if not any(r.source == source for r in self._retiring):
                    self._spawn(c, now)
                continue
            if c.proc is None:
                continue
            rc = c.proc.poll()
            if c.state in RUNNING and rc is not None:
                c.error = self._tail(c) or f"exited with code {rc}"
                c.set("failed", now)
                log.warning("test %s: exited by itself (code %s)", source, rc)
            elif c.state == "starting" and self._stream_live(c, now):
                c.set("playing", now)
            elif c.state == "stopping":
                if rc is not None:
                    c.set("off", now)
                elif c.term_s is not None and now - c.term_s >= KILL_AFTER_S and not c.killed:
                    _kill(c.proc)
                    c.killed = True
        if self._running_key() != before:
            self._apply()
        self._rebuild()

    def _running_key(self) -> tuple:
        return tuple(c.state in RUNNING if c is not None else False for c in self._cur.values())

    def _busy(self, now: float) -> bool:
        if self._retiring:
            return True
        for c in self._cur.values():
            if c is None:
                continue
            if c.state == "stopping":
                return True
            if c.state == "starting" and (c.proc is None or now - c.since_s < FAST_POLL_FOR_S):
                return True
        return False

    def _schedule(self) -> None:
        """Poll every FAST_POLL_S on show-brain while something is starting or stopping (only from that loop)."""
        loop = self.ctx.loop
        if loop is None or self._fast_handle is not None or not self._busy(self.clock()):
            return
        try:
            if asyncio.get_running_loop() is not loop:
                return
        except RuntimeError:
            return
        self._fast_handle = loop.call_later(FAST_POLL_S, self._fast)

    def _fast(self) -> None:
        self._fast_handle = None
        self._poll(self.clock())
        self._schedule()

    def _apply(self) -> None:
        """Hand the receiver the effective sender list and priority when they changed."""
        yaml_senders, self._sender_errors = parse_senders(self.settings)
        running_names, extra, prio = set(), [], []
        defined = {s.name for s in yaml_senders}
        for source, c in self._cur.items():
            if c is None or c.state not in RUNNING:
                continue
            running_names.add(c.sender.name)
            if c.sender.name not in defined and all(e.name != c.sender.name for e in extra):
                extra.append(c.sender)                         # show.yaml lacks it: the localhost default
            if self._tests_cfg(source).get("rank_first") is True and c.stream not in prio:
                prio.append(c.stream)
        eff = senders_from_settings(self.settings, tuple(self.ctx.static_senders or ()), running_names)
        eff = eff + tuple(e for e in extra if all(s.name != e.name for s in eff))
        errors = []
        if eff != self._applied:
            if self.ctx.set_senders is None:
                errors.append("the receiver can't take a sender list (set_senders missing): show.yaml senders "
                              "and test sources aren't subscribed")
            else:
                try:
                    self.ctx.set_senders(eff)
                    self._applied = eff
                    log.info("senders: %s", ", ".join(f"{s.name}={'|'.join(s.hosts or (s.host,))}:{s.port}"
                                                       for s in eff) or "none")
                except Exception as e:                         # noqa: BLE001
                    errors.append(f"set_senders failed: {type(e).__name__}: {e}")
        prio_t = tuple(prio)
        if prio_t != self._prio_applied:
            if self.ctx.set_priority is None:
                if prio_t:
                    errors.append("the receiver can't take a priority (set_priority missing)")
            else:
                try:
                    self.ctx.set_priority(prio_t)
                    self._prio_applied = prio_t
                except Exception as e:                         # noqa: BLE001
                    errors.append(f"set_priority failed: {type(e).__name__}: {e}")
        self._apply_errors = errors
        self._wanted = (eff, prio_t)

    def _status(self, source: str, now: float) -> contract.SourceStatus:
        c = self._cur.get(source)
        if c is None:
            sender, stream = self._test_sender(source)
            return {"source": source, "state": "off", "piece": None, "label": None, "stream": stream,
                    "sender": sender.name, "pid": None, "since_s": None, "error": None}
        return {"source": source, "state": c.state, "piece": c.piece, "label": c.label, "stream": c.stream,
                "sender": c.sender.name, "pid": c.proc.pid if c.proc is not None else None,
                "since_s": round(max(0.0, now - c.since_s), 3), "error": c.error if c.state == "failed" else None}

    def _rebuild(self) -> None:
        now = self.clock()
        yaml_senders, _ = parse_senders(self.settings)
        wanted = self._wanted
        subscribed = {s.name for s in (self._applied or ())}
        rows, seen = [], set()
        for s in list(wanted[0]) + yaml_senders:
            if s.name in seen:
                continue
            seen.add(s.name)
            rows.append({"name": s.name, "hosts": list(s.hosts or (s.host,)), "port": s.port,
                         "streams": list(s.streams), "on_demand": s.on_demand, "subscribed": s.name in subscribed})
        errors = list(self._sender_errors) + list(self._apply_errors)
        if _phrases_error:
            errors.append(_phrases_error)
        for source in PLAYERS:
            if self._tests_cfg(source) and not self._folder(source).is_dir():
                errors.append(f"tests.{source}.{PLAYERS[source]['dir_key']}: no folder {self._folder(source)}"
                              + (" (run tools/show/fetch_test_music.py)" if PLAYERS[source]["kind"] == "audio"
                                 else ""))
        self._snap = {"senders": rows, "tests": {s: self._status(s, now) for s in PLAYERS},
                      "priority": list(self._prio_applied or ()), "errors": errors}


def attach(ctx: contract.BrainContext, **kw: Any) -> Sources:
    """The brain's ``sources``. Keyword args go to Sources (__main__ passes ``log_dir``)."""
    return Sources(ctx, **kw)


def main(argv: Optional[list[str]] = None) -> int:
    """``python -m show_service.sources audio --file F --port P ...``: the test-audio child (test_audio.main)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["audio"]:
        from . import test_audio
        return test_audio.main(args[1:])
    print("usage: python -m show_service.sources audio --file F [--port P] [--bind 127.0.0.1] [--stream test-audio]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
