"""One test-piano run for show_sender: load the sound once, then render the piece as it plays on PC22 or a Sonos, on
command. Nothing is rendered ahead and nothing is stored (owner, 22 Oct: "it should be rendered on demand").

    ~/.venvs/sm-piano/bin/python -m piano_render.session --midi PATH --instrument ID [--pad-s X] [--output OUT]
        [--calibration-mic STREAM]

Started by tools/show_sender/show_sender.py (``--test-piano PIECE --instrument ID --output pc22|sonos:<zone>``) from
the repo root. --output: pc22 (the default: PC22's speakers, piano_render/pc22.py) or sonos:<zone> (a zone of
config/room/sonos.json, through room_service: piano_render/sonos.py, whose docstring has the volume, stream, start
estimate and watch rules).

How a play works (piano_render/README.md, "Latency and compute", has the numbers):
  * At start the instrument is loaded once (piano_render.stream.Streamer.open(): the engine with partial preload, the
    room's IR, the cached tuning; 0.1-5 s), alongside, on PC22, the player's own start (the "arm", below).
  * Each "play" streams the piece from MIDI time 0 through the Streamer into the sink while it plays, AHEAD_S ahead of
    what the sink plays (PC22: PC22_AHEAD_S, 1 s; Sonos: sonos.STREAM_AHEAD_S, 4 s), at most the engine's max_speed.
    A later play rewinds the same Streamer (samples stay loaded; the chain is built again, in the background as soon
    as the last play has let go of it).
  * Each block handed to the sink also goes to piano_render/calibrate.py's StreamReference: the Calibrator hears the
    piano through the room's mic (PC22's own mic for PC22, the drawing room's for Sonos), and moves the MIDI clock with
    "rebase" lines. It reads show_service's levels feed only (never turns a mic on) and fails closed.
    --calibration-mic STREAM (show_service's sources.py names it, through show_sender): the stream it watches
    (``?stream=``; an older feed that ignores it gives the active input only, and the status says so); "" = no mic
    hears this output (no mic check: the last measurement or the sink's estimate stands); absent: calibrate.MIC_FOR.
  * Nothing goes to disk, except: the Sonos's MP3 stream (sonos.py: deleted 10 min after it ends) and, only when
    $PIANO_SESSION_KEEP_DIR is set (default: unset, off), an MP3 copy of each play that played to its end
    (KeepCopy: ``<dir>/<UTC> <piece> - <instrument>.mp3``, the newest KEEP_MAX kept).

The arm (PC22 only; $PIANO_SESSION_ARM=0 turns it off): the Windows player takes ~2.6 s to start (py.exe, sounddevice,
WASAPI open), so the first play's player is started while the instrument loads, timed (ARM_LEAD_S) so it is ready about
when the load ends; it waits, its device open and silent, until "play". If it isn't used in time (pc22.py's and the
player's 10 s waits) or fails, the play starts a player of its own as before. After each play that ends by itself the
next player is armed REARM_S later (a loop's next pass comes LOOP_GAP_S, 3 s, after the last note); a stop or EOF in
the meantime drops it before it starts.

stdout: the protocol only, one JSON object per line, flushed (anything else that writes to fd 1 goes to stderr; logs
are on stderr):
  {"ev": "progress", "stage": "loading", "step": "load", "done_s", "total_s", "frac"}
        while the instrument loads (the only wait before a play): every PROGRESS_EVERY_S. done_s: seconds so far;
        total_s: the expected load (this instrument's last loads on this box, LOADS_FILE; else DEFAULT_LOAD_S by
        engine); frac: done/total, at most LOAD_FRAC_MAX, never going back.
  {"ev": "rendered", "streaming": true, "path": null, "duration_s", "onset_ms", "cached": false, "render_s", "load_s",
   "ahead_s", "max_speed", "sched", "nice"}
        once, when it can play (the cached-file protocol's name, kept: show_sender waits for it). duration_s: the
        MIDI's own length (its last event; the audio's tail ends a few seconds later). render_s = load_s, the load.
  {"ev": "started", "t_audio0", "pad_s", "startup_s", "sink_t0", "delay_ms"[, "armed": true]}
        after each play: t_audio0 = the WSL time.monotonic() at which MIDI time 0 is heard: sink_t0, the sink's own
        report (PC22: the DAC time of stream frame 0, after pad_s of digital silence; Sonos: SonosOutput's estimate,
        the PUT + its start delay + pad_s + the MP3 encoder's delay), plus delay_ms, this output's measured delay
        (calibrate.py's prior; 0 until one is measured). startup_s: from the play command.
  {"ev": "rebase", "t_audio0", "was", "by": "mic"|"sink", "delay_ms"}   MIDI time 0 is heard at t_audio0 instead
  {"ev": "calibration", "state", "why", ...}    calibrate.py's state in plain words (informational)
  {"ev": "warning", "why": plain words}         Sonos only: it plays, but e.g. "Sonos volume is 0"
  {"ev": "played", "underruns", "frames", ..., "late_blocks", "max_late_ms", "cpu_pct", "rss_peak_mb"}
        the audio ended by itself (the player's "done"; Sonos: {"by", ...}), with the stream's own stats
  {"ev": "stopped"}                             after "stop": silent (also if nothing was playing)
  {"ev": "error", "why": plain words}           then exit 1 (Sonos: "Sonos stopped" | "Sonos paused" | "Sonos is
                                                playing something else" | "Sonos didn't start" | why it can't play)
stdin: one command per line:
  play   start (or restart) the piece on PC22 from MIDI time 0, pad_s of silence first; a "play" before "rendered"
         waits for it. On Sonos: the last play-sonos again.
  play-sonos {"url", "title"?, "volume"?, "zone"?}
         --output sonos:<zone> only: the Sonos plays the stream from show_service's /api/v1/piano/stream/<id>.mp3 at
         url's host and door (url: show_sender's http://<SM_LAN_HOST>:8700/show, or an older show_sender's
         .../api/v1/piano/audio/<key>.flac: only its host and door are used); title: what the Sonos app shows; volume
         0..100 (default: SonosOutput's choice); zone, if given, must be --output's. Waits for "rendered" like play.
  stop   silence within ~50 ms (the player's 10 ms fade, then the device buffer); the render stops with it. On Sonos:
         playback "paused" through room_service (its fade-out), only while the Sonos still plays our stream.
  EOF    stop, then exit 0. SIGTERM, SIGHUP and the parent's death (PR_SET_PDEATHSIG) do the same.

--pad-s: how long after "started" the first MIDI event is due at the earliest (show_sender: lead - first event + 0.1).
PC22 plays that much digital silence first. On Sonos it is less the Sonos's expected start delay (stream_start_ms +
the encoder's delay + the measured delay), at least 0: its own start delay usually covers it.

Priorities and memory: this process renders and plays (stream.prepare_process(): normal scheduling, nice 5, under the
TVs' streams; single-threaded DSP; RLIMIT_DATA 3 GB, so a runaway sample set ends this play, never the box; no core
dumps). pc22.py tracks its player and kills it at exit; every exit path here stops it.

Until show_service serves the live stream (GET /api/v1/piano/stream/<id>.mp3: its restart, screen-machine-3a's batch),
--output sonos:<zone> execs piano_render/session_files.py, the old cached-file path, so Play on Sonos keeps working
(stream_route_live(); $PIANO_SESSION_SONOS_STREAM=1|0 forces either). Delete that file once the route is live.

PIANO_SESSION_GAIN_DB (env, dB): scales the PCM sent to the PC22 player (-120 = a silent end-to-end check of the real
PC22 path; the calibrator is off then: there is nothing to hear). If pc22.py no longer sends its PCM through
``to_pcm``, a run with it set refuses to play (fails closed); so does a Sonos run (a silent check never sends the
Sonos anything).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
import queue
import re
import signal
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
ARM_ENV = "PIANO_SESSION_ARM"
CALIBRATE_ENV = "PIANO_CALIBRATE"
KEEP_DIR_ENV = "PIANO_SESSION_KEEP_DIR"
KEEP_MAX_ENV = "PIANO_SESSION_KEEP_MAX"
KEEP_MAX = 50
KEEP_QUALITY = "2"                  # libmp3lame -q:a (VBR, ~190 kb/s)
KEEP_QUEUE_BLOCKS = 4096            # ~44 s of 512-frame blocks; past that the copy is dropped, never the play
PROGRESS_EVERY_S = 0.25
LOAD_FRAC_MAX = 0.95
PAD_MAX_S = 30.0
POLL_S = 0.25
PR_SET_PDEATHSIG = 1
PC22_OUTPUT = "pc22"
SONOS_PREFIX = "sonos:"
SETTLE_S = 2.0                      # at exit, a Sonos pause that waits for our stream to be heard gets this long
PC22_AHEAD_S = 1.0                  # PC22's render-ahead: 0.44 s of fill at sfizz's 2.28x, inside the player's start
SR = 48000
STREAM_LOCK_S = 5.0                 # a play waits at most this for the last one's render to let go
LOADS_FILE = Path("~/.local/state/screen-machine/piano/loads.json")
LOADS_ENV = "PIANO_SESSION_LOADS_FILE"
LOADS_KEPT = 8
#: the expected load (Streamer.open(): the engine alongside the chain's imports) with no history: README "Latency and
#: compute" (sfizz sets 1.9-5 s median, up to 10 s cold; FluidSynth 0.13 s, but the chain's scipy import 1.9-4 s)
DEFAULT_LOAD_S = {"sfizz": 4.5, "fluidsynth": 2.5, None: 4.5}
ARM_LEAD_S = 3.0                    # the player is started this long before the load is expected to end
REARM_S = 0.5                       # after a play ends: the next one's player (a loop's next pass) starts this soon
ARM_WAIT_S = 0.02
SONOS_STREAM_ENV = "PIANO_SESSION_SONOS_STREAM"   # 1: stream to the Sonos, 0: the old file path, unset: ask show
SHOW_URL_ENV = "PIANO_SHOW_URL"
SHOW_URL = "http://127.0.0.1:8703"
STREAM_PROBE_ID = "0" * 32
OLD_SONOS_PAD_MIN_S = 0.75          # the old file path's own minimum pad (show_sender used to send it)


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


def _env_on(name: str, default: bool = True) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "no", "off", "false")


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


def _status_kb() -> dict:
    d: dict = {}
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                k, _, v = line.partition(":")
                if k in ("VmRSS", "VmHWM"):
                    d[k] = int(v.split()[0])
    except OSError:
        pass
    return d


# -- the expected load (the progress bar while the instrument loads) ---------------------------------------------------
def loads_path() -> Path:
    raw = os.environ.get(LOADS_ENV)
    return Path(raw).expanduser() if raw and raw.strip() else LOADS_FILE.expanduser()


def expected_load_s(instrument: str, engine: Optional[str], path: Optional[Path] = None) -> float:
    """The median of this instrument's last loads (LOADS_FILE), else DEFAULT_LOAD_S for its engine."""
    try:
        doc = json.loads((path or loads_path()).read_text(encoding="utf-8"))
        vals = [float(v) for v in (doc.get(instrument) or {}).get("load_s") or [] if 0 < float(v) < 600]
    except (OSError, ValueError, TypeError, AttributeError):
        vals = []
    if vals:
        return float(statistics.median(vals))
    return float(DEFAULT_LOAD_S.get(engine, DEFAULT_LOAD_S[None]))


def record_load_s(instrument: str, seconds: float, path: Optional[Path] = None) -> None:
    p = path or loads_path()
    try:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            doc = doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            doc = {}
        ent = doc.get(instrument) if isinstance(doc.get(instrument), dict) else {}
        vals = [v for v in (ent.get("load_s") or []) if isinstance(v, (int, float))][-(LOADS_KEPT - 1):]
        doc[instrument] = {"load_s": vals + [round(float(seconds), 3)]}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        log.debug("couldn't note the load time in %s: %s", p, e)


# -- the optional kept copy ------------------------------------------------------------------------------------------
def keep_dir() -> Optional[Path]:
    raw = os.environ.get(KEEP_DIR_ENV)
    return Path(raw).expanduser() if raw and raw.strip() else None


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w .,()'&+-]+", "_", text).strip()[:120] or "piece"


class KeepCopy:
    """$PIANO_SESSION_KEEP_DIR only: one play encoded to MP3 (ffmpeg libmp3lame VBR -q:a KEEP_QUALITY) as it plays,
    from a queue on a thread of its own (the play never waits for it; a copy that falls KEEP_QUEUE_BLOCKS behind is
    dropped). finish(True): renamed into place and the folder pruned to the newest KEEP_MAX; finish(False): deleted."""

    def __init__(self, folder: Path, name: str, sr: int = SR, ffmpeg: str = "ffmpeg") -> None:
        self.dir = Path(folder)
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.path = self.dir / f"{stamp} {_safe_name(name)}.mp3"
        self.tmp = self.dir / f".{self.path.name}.{os.getpid()}.part"
        self.q: "queue.Queue[Any]" = queue.Queue(maxsize=KEEP_QUEUE_BLOCKS)
        self.dropped = False
        self.proc: Optional[subprocess.Popen] = None
        self.dir.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-f", "f32le", "-ar", str(int(sr)), "-ac", "2",
               "-i", "pipe:0", "-c:a", "libmp3lame", "-q:a", KEEP_QUALITY, "-threads", "1", "-f", "mp3", "-y",
               str(self.tmp)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        self.thread = threading.Thread(target=self._run, name="keep-copy", daemon=True)
        self.thread.start()

    def write(self, data) -> None:
        if self.dropped:
            return
        try:
            self.q.put_nowait(data)
        except queue.Full:
            self.dropped = True
            log.warning("keep: the MP3 copy fell behind: dropped (the play goes on)")

    def _run(self) -> None:
        import numpy as np
        fd = self.proc.stdin.fileno()
        try:
            while True:
                a = self.q.get()
                if a is None:
                    break
                if self.dropped:
                    continue
                mv = memoryview(np.ascontiguousarray(a, dtype="<f4").tobytes())
                while mv:
                    mv = mv[os.write(fd, mv):]
        except OSError as e:
            self.dropped = True
            log.warning("keep: ffmpeg went away: %s", e)
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def finish(self, complete: bool) -> Optional[Path]:
        if not complete:
            self.dropped = True
        try:
            self.q.put(None, timeout=1.0)
        except queue.Full:
            self.dropped = True
        self.thread.join(5.0)
        try:
            rc = self.proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            rc = self.proc.wait()
        if complete and not self.dropped and rc == 0:
            try:
                os.replace(self.tmp, self.path)
                self._prune()
                log.info("keep: %s", self.path)
                return self.path
            except OSError as e:
                log.warning("keep: %s", e)
        try:
            self.tmp.unlink()
        except OSError:
            pass
        return None

    def _prune(self) -> None:
        try:
            n = int(os.environ.get(KEEP_MAX_ENV) or KEEP_MAX)
        except ValueError:
            n = KEEP_MAX
        try:
            files = sorted(self.dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for p in files[max(1, n):]:
            try:
                p.unlink()
            except OSError:
                pass


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


# -- one play's audio -------------------------------------------------------------------------------------------------
class PlaySource:
    """One play's audio, as the sinks take it (pc22.py, sonos.py: ``blocks(position)``, ``sr``, ``ahead_s``,
    ``label``): the session's Streamer from MIDI time 0, each block also to the calibrator's reference and the kept
    copy. ``armed``: blocks() first waits for ``go`` (polling position(), which raises pc22.Halted after a stop).
    The Streamer is used by one play at a time (the session's stream lock, held while blocks() runs)."""

    def __init__(self, sess: "Session", *, armed: bool = False) -> None:
        self.s = sess
        self.armed = armed
        self.go = threading.Event()
        if not armed:
            self.go.set()
        self.gen: Optional[int] = None                    # set when a play takes it
        self.ref: Any = None
        self.sr = SR
        self.ahead_s = sess.ahead_s
        self.label = f"{Path(sess.midi).name} on {sess.instrument}"
        self.blocks_out = 0
        self.finished = False
        self.t_first: Optional[float] = None

    def blocks(self, position: Callable[[], float]):
        while not self.go.wait(ARM_WAIT_S):
            position()                                   # Halted once the play is stopped
        s = self.s
        if not s.stream_lock.acquire(timeout=STREAM_LOCK_S):
            raise RuntimeError("the last play's render didn't let go")
        keep: Optional[KeepCopy] = None
        try:
            st = s.streamer
            st.rewind()                                  # nothing the first time; else back to frame 0
            keep = s.keep_factory() if s.keep_factory is not None else None
            ref = self.ref
            for b in st.blocks(position):
                if self.t_first is None:
                    self.t_first = time.monotonic()
                if ref is not None:
                    ref.sent(b.pos, b.data)
                if keep is not None:
                    keep.write(b.data)
                self.blocks_out += 1
                yield b
            self.finished = True
        finally:
            if keep is not None:
                threading.Thread(target=keep.finish, args=(self.finished,), name="keep-finish", daemon=True).start()
            s.stream_lock.release()


# -- the session ----------------------------------------------------------------------------------------------------
def _default_output():
    from . import pc22
    return pc22.PC22Output()


class Session:
    """The playback process: the instrument (one Streamer), the commands, one output. run() returns the exit code."""

    def __init__(self, midi: str, instrument: str, pad_s: float = 0.0, *, proto: Proto,
                 output_factory: Optional[Callable[[], Any]] = None, output: str = PC22_OUTPUT,
                 streamer_factory: Optional[Callable[[], Any]] = None, calibrator: Any = None,
                 keep_factory: Optional[Callable[[], Optional[KeepCopy]]] = None, arm: Optional[bool] = None,
                 ahead_s: Optional[float] = None, loads_file: Optional[Path] = None) -> None:
        self.midi, self.instrument = str(midi), str(instrument)
        self.output = str(output)
        self.zone = self.output[len(SONOS_PREFIX):] if self.output.startswith(SONOS_PREFIX) else None
        self.where = "Sonos" if self.zone else "PC22"
        self.play_opts: Optional[dict] = None            # the last play-sonos's {url, title?, volume?}
        self.pad_s = min(PAD_MAX_S, max(0.0, float(pad_s)))
        self.proto = proto
        self.output_factory = output_factory or _default_output
        if ahead_s is None:
            if self.zone is not None:
                from . import sonos
                ahead_s = sonos.STREAM_AHEAD_S
            else:
                ahead_s = PC22_AHEAD_S
        self.ahead_s = float(ahead_s)
        self.own_streamer = streamer_factory is None      # ours: its engine is looked up, its load time kept
        self.streamer_factory = streamer_factory or self._make_streamer
        self.cal = calibrator
        self.keep_factory = keep_factory
        self.arm = (self.zone is None and _env_on(ARM_ENV)) if arm is None else bool(arm and self.zone is None)
        self.loads_file = loads_file
        self.q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.streamer: Any = None
        self.loaded: Optional[dict] = None
        self.load_t0 = 0.0
        self.engine: Optional[str] = None
        self.expected_s = DEFAULT_LOAD_S[None]
        self.out: Any = None
        self.gen = 0                                     # bumped by every play and stop: a stale play is ignored
        self.pending_play = False
        self.armed: Optional[PlaySource] = None
        self.stream_lock = threading.Lock()
        self._loading = threading.Event()                # set once the load has ended (either way)
        self._rewinding: Optional[threading.Thread] = None

    # -- loading the instrument --
    def _make_streamer(self):
        from . import stream
        return stream.Streamer(self.midi, self.instrument, ahead_s=self.ahead_s)

    def _engine_of(self) -> Optional[str]:
        try:
            from . import instruments
            return instruments.load().get(self.instrument).get("engine")
        except Exception:                                # noqa: BLE001  the load says what is wrong
            return None

    def _start_load(self) -> None:
        self.load_t0 = time.monotonic()
        if self.own_streamer:
            self.engine = self._engine_of()
        self.expected_s = expected_load_s(self.instrument, self.engine, self.loads_file)
        threading.Thread(target=self._load, name="load", daemon=True).start()
        threading.Thread(target=self._load_progress, name="load-progress", daemon=True).start()

    def _load(self) -> None:
        try:
            st = self.streamer_factory()
            stats = st.open()
        except BaseException as e:                       # noqa: BLE001  one plain line, then exit 1
            log.exception("loading %s for %s", self.instrument, self.midi)
            self._loading.set()
            self.q.put(("load_error", e))
        else:
            took = time.monotonic() - self.load_t0
            self._loading.set()                          # no progress line after "rendered"
            self.q.put(("loaded", (st, dict(stats or {}), took)))

    def _load_progress(self) -> None:
        best = 0.0
        while not self._loading.wait(PROGRESS_EVERY_S):
            done = time.monotonic() - self.load_t0
            best = max(best, min(LOAD_FRAC_MAX, done / max(0.1, self.expected_s)))
            self.proto.emit({"ev": "progress", "stage": "loading", "step": "load", "done_s": round(done, 2),
                             "total_s": round(self.expected_s, 2), "frac": round(best, 4)})

    def _on_loaded(self, st: Any, stats: dict, took: float) -> None:
        self.streamer = st
        if self.own_streamer:
            record_load_s(self.instrument, took, self.loads_file)
        perf = getattr(st, "perf", None)
        inst = getattr(st, "inst", None) or {}
        dur = float(getattr(perf, "duration_s", 0.0) or 0.0)
        self.loaded = {"ev": "rendered", "streaming": True, "path": None, "duration_s": round(dur, 3),
                       "onset_ms": float(inst.get("onset_ms") or 0.0), "cached": False, "render_s": round(took, 3),
                       "load_s": round(took, 3), "ahead_s": self.ahead_s, "max_speed": stats.get("max_speed"),
                       "engine_load_s": stats.get("load_engine_s"),
                       "rss_mb": round((stats.get("mem_after_load_kb") or {}).get("VmRSS", 0) / 1024.0, 1),
                       **sched_state()}
        log.info("loaded %s for %s in %.2f s (expected %.1f s): ready to play", self.instrument, Path(self.midi).name,
                 took, self.expected_s)
        self.proto.emit(self.loaded)

    # -- the arm (PC22: the first play's player starts while the instrument loads) --
    def _arm(self, delay: Optional[float] = None) -> None:
        if not self.arm:
            return
        if self.out is None:
            self.out = self.output_factory()
        src = PlaySource(self, armed=True)
        self.armed = src
        if delay is None:
            delay = max(0.0, self.expected_s - ARM_LEAD_S)
            log.info("arming the player (it starts in %.1f s; the load is expected to take %.1f s)", delay,
                     self.expected_s)
        threading.Thread(target=self._play_worker, args=(src, delay), name="play-armed", daemon=True).start()

    # -- commands --
    def _read_cmds(self, f) -> None:
        try:
            for line in iter(f.readline, ""):
                self.q.put(("cmd", line.strip()))
        except (OSError, ValueError):
            pass
        self.q.put(("eof", None))

    def _new_ref(self):
        if self.cal is None:
            return None
        from . import calibrate
        return calibrate.StreamReference(SR)

    def _play(self) -> None:
        """A play, now that the instrument is loaded: the armed player if it is still waiting, else a new one."""
        self.gen += 1
        if self.out is None:
            self.out = self.output_factory()
        src, self.armed = self.armed, None
        if src is not None and src.gen is None and not getattr(src, "dead", False):
            src.gen, src.ref = self.gen, self._new_ref()
            src.t_play = time.monotonic()
            src.go.set()                                 # its worker reports "started" (or starts a new player)
            return
        src = PlaySource(self)
        src.gen, src.ref = self.gen, self._new_ref()
        src.t_play = time.monotonic()
        threading.Thread(target=self._play_worker, args=(src,), name="play", daemon=True).start()

    def play_sonos(self, arg: str) -> Optional[str]:
        """A play-sonos command's JSON -> self.play_opts; plain words when it can't be played."""
        if self.zone is None:
            return "play-sonos needs --output sonos:<zone> (this session plays on PC22)"
        try:
            doc = json.loads(arg) if arg.strip() else None
        except ValueError:
            doc = None
        if not isinstance(doc, dict) or not isinstance(doc.get("url"), str) or not doc["url"].strip():
            return 'play-sonos takes {"url": ..., "title"?, "volume"?}'
        if doc.get("zone") not in (None, self.zone):
            return f"this session plays on the Sonos zone “{self.zone}”, not “{doc.get('zone')}”"
        self.play_opts = {"url": doc["url"].strip(), "title": doc.get("title") if isinstance(doc.get("title"), str)
                          else None, "volume": doc.get("volume")}
        return None

    def _prior_s(self) -> float:
        cal = self.cal
        if cal is None:
            return 0.0
        try:
            from . import calibrate
            v = calibrate.prior_delay(cal.key, getattr(cal, "lat_path", None))
        except Exception:                                # noqa: BLE001
            v = None
        return float(v or 0.0)

    def _sonos_pad(self, out: Any) -> float:
        """--pad-s less the Sonos's expected delay after the PUT (module docstring), at least 0."""
        from . import sonos
        expect = _num(getattr(out, "stream_start_ms", sonos.STREAM_START_MS_DEFAULT)) / 1000.0 \
            + sonos.MP3_DELAY_FRAMES / SR + self._prior_s()
        return max(0.0, self.pad_s - expect)

    def _play_worker(self, src: PlaySource, delay: float = 0.0) -> None:
        out = self.out
        if delay > 0:
            src.go.wait(delay)                           # a play that comes first starts the player at once
        if src.armed and src.gen is None and self.armed is not src:
            return                                       # stopped (or replaced) before its player started
        t = time.monotonic()
        try:
            if self.zone is not None:
                pad = self._sonos_pad(out)
                sink_t0 = out.start(src, lead_in_ms=pad * 1000.0, **(self.play_opts or {}))
                pad = float(getattr(out, "pad_s", pad))
            else:
                pad = self.pad_s
                sink_t0 = out.start(src, lead_in_ms=pad * 1000.0)
        except Exception as e:                           # noqa: BLE001  PC22Error, SonosError and anything else
            if src.armed:
                src.dead = True                          # type: ignore[attr-defined]
                if src.gen is None or src.gen != self.gen:
                    log.info("the armed player wasn't used (%s): a play starts its own", plain(e))
                    if self.armed is src:
                        self.armed = None
                    return
                log.warning("the armed player failed (%s): starting a new one", plain(e))
                again = PlaySource(self)
                again.gen, again.ref, again.t_play = src.gen, src.ref, getattr(src, "t_play", t)
                return self._play_worker(again)
            self.q.put(("play_error", (src.gen, f"couldn't play on {self.where}: {plain(e)}")))
            return
        if src.gen != self.gen:                          # a stop (or another play) came during the start
            out.stop()
            return
        delay_s = 0.0
        t_audio0 = sink_t0
        if self.cal is not None and src.ref is not None:
            try:
                if self.zone is None:
                    from . import calibrate
                    dev = (getattr(out, "ready", None) or {}).get("device")
                    self.cal.key = calibrate.output_key("pc22", device=dev)
                t_audio0 = float(self.cal.begin(sink_t0, src.ref))
                delay_s = t_audio0 - sink_t0
            except Exception as e:                       # noqa: BLE001  calibration never stops a play
                log.warning("calibration couldn't start: %s", e)
                t_audio0 = sink_t0
        msg = {"ev": "started", "t_audio0": t_audio0, "pad_s": round(pad, 3),
               "startup_s": round(time.monotonic() - getattr(src, "t_play", t), 3), "sink_t0": sink_t0,
               "delay_ms": round(delay_s * 1000.0, 1)}
        if src.armed:
            msg["armed"] = True
        self.proto.emit(msg)
        out.wait()
        s = getattr(out, "session", None)
        done = dict(getattr(out, "done", None) or {}) or None
        err = getattr(s, "error", None)
        serr = getattr(s, "stream_error", None)
        self.q.put(("played", (src.gen, done, err, serr, self._stream_stats(src))))

    def _stream_stats(self, src: PlaySource) -> dict:
        st = self.streamer
        d = dict(getattr(st, "stats", None) or {})
        mem = _status_kb()
        return {"late_blocks": d.get("late_blocks"), "max_late_ms": d.get("max_late_ms"),
                "cpu_pct": d.get("cpu_pct_of_core"), "audio_s": d.get("audio_s"),
                "rss_peak_mb": round(mem.get("VmHWM", 0) / 1024.0, 1), "blocks": src.blocks_out,
                "complete": src.finished}

    def _rewind_later(self) -> None:
        """Build the next play's chain now, in the background, once the last play has let go of the Streamer."""
        if self.streamer is None or (self._rewinding is not None and self._rewinding.is_alive()):
            return

        def work() -> None:
            if not self.stream_lock.acquire(timeout=STREAM_LOCK_S * 2):
                return
            try:
                took = self.streamer.rewind()
                log.info("ready for the next play (rewound in %.2f s)", _num((took or {}).get("rewind_s")))
            except Exception as e:                       # noqa: BLE001  the next play tries again and says why
                log.warning("rewind: %s", e)
            finally:
                self.stream_lock.release()
        self._rewinding = threading.Thread(target=work, name="rewind", daemon=True)
        self._rewinding.start()

    def stop(self) -> None:
        """Silence; the render stops with it."""
        self.gen += 1
        self.pending_play = False
        self.armed = None
        if self.out is not None:
            self.out.stop()
        if self.cal is not None:
            self.cal.end()
        self._rewind_later()

    def shutdown(self) -> None:
        self.gen += 1
        self.armed = None
        try:
            if self.out is not None:
                self.out.stop()
                settle = getattr(self.out, "settle", None)    # Sonos: a pause waiting for our stream to be heard
                if callable(settle):
                    settle(SETTLE_S)
        finally:
            if self.cal is not None:
                try:
                    self.cal.close()
                except Exception:                        # noqa: BLE001
                    pass
            st, self.streamer = self.streamer, None
            # The engines are freed only once no render runs (a sink's thread may still be inside one): never under
            # a running render, which would crash the process on its way out.
            if st is not None and self.stream_lock.acquire(timeout=2.0):
                try:
                    st.close()
                except Exception:                        # noqa: BLE001
                    pass
                finally:
                    self.stream_lock.release()

    def handle(self, kind: str, val: Any) -> Optional[int]:
        """One event; returns an exit code to end the run."""
        if kind == "cmd":
            if val.split(" ", 1)[0] == "play-sonos" or (val == "play" and self.zone is not None):
                why = (self.play_sonos(val[len("play-sonos"):]) if val != "play"
                       else None if self.play_opts else 'Sonos plays on "play-sonos {\"url\": ...}"')
                if why:
                    self.proto.emit({"ev": "error", "why": why})
                    return 1
                val = "play"
            if val == "play":
                if self.streamer is not None:
                    self._play()
                else:
                    self.pending_play = True
            elif val == "stop":
                self.stop()
                self.proto.emit({"ev": "stopped"})
            elif val:
                log.warning("unknown command %r", val[:80])
        elif kind == "eof":
            return 0
        elif kind == "loaded":
            st, stats, took = val
            self._on_loaded(st, stats, took)
            if self.pending_play:
                self.pending_play = False
                self._play()
        elif kind == "load_error":
            self.proto.emit({"ev": "error", "why": f"couldn't load {self.instrument} for {Path(self.midi).name}: "
                                                   f"{plain(val)}"})
            return 1
        elif kind == "played":
            gen, done, err, serr, stats = val
            if gen == self.gen:
                if self.cal is not None:
                    self.cal.end()
                self._rewind_later()
                if done is None and err:
                    self.proto.emit({"ev": "error", "why": err if self.zone else f"PC22 playback stopped: {err}"})
                    return 1
                if serr:
                    self.proto.emit({"ev": "error", "why": f"the piano render failed while it played: {serr}"})
                    return 1
                self.proto.emit({"ev": "played", **(done or {}), **{k: v for k, v in stats.items() if v is not None}})
                self._arm(REARM_S)                       # a loop's next pass: its player starts now
        elif kind == "play_error":
            gen, why = val
            if gen == self.gen:
                self.proto.emit({"ev": "error", "why": why})
                return 1
        return None

    def run(self, cmds) -> int:
        threading.Thread(target=self._read_cmds, args=(cmds,), name="cmds", daemon=True).start()
        self._start_load()
        self._arm()
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
def output_arg(v: str) -> str:
    """--output: "pc22" or "sonos:<zone>" (letters, digits, - _ .)."""
    zone = v[len(SONOS_PREFIX):] if v.startswith(SONOS_PREFIX) else None
    if v == PC22_OUTPUT or (zone and all(c.isalnum() or c in "-_." for c in zone)):
        return v
    raise argparse.ArgumentTypeError(f"{v!r}: pc22 or sonos:<zone>")


def mic_arg(v: str) -> str:
    """--calibration-mic: a stream name (letters, digits, - _ .; at most 16, VBAN's limit) or "" (none)."""
    v = v.strip()
    if v == "" or (len(v) <= 16 and all(c.isalnum() or c in "-_." for c in v)):
        return v
    raise argparse.ArgumentTypeError(f"{v!r}: a stream name, or \"\" for none")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m piano_render.session", description=__doc__.split("\n")[0])
    ap.add_argument("--midi", required=True, help="the .mid to play")
    ap.add_argument("--instrument", required=True, help="instrument id (config/piano/instruments.json)")
    ap.add_argument("--pad-s", type=float, default=0.0, help="the first MIDI event's need after 'started' (above)")
    ap.add_argument("--output", default=PC22_OUTPUT, type=output_arg, metavar="pc22|sonos:<zone>",
                    help="where it plays: PC22's speakers (default) or a Sonos zone of config/room/sonos.json")
    ap.add_argument("--calibration-mic", default=None, type=mic_arg, metavar="STREAM",
                    help="the mic stream that hears the output (show_service names it); \"\": none hears it")
    return ap


def stream_route_live(show_url: Optional[str] = None, timeout: float = 2.0) -> bool:
    """show_service answers GET /api/v1/piano/stream/<id>.mp3 (its own 404 for an unknown id: JSON; a server without
    the route answers aiohttp's plain-text 404). False when it can't be asked."""
    import urllib.error
    import urllib.request
    base = (show_url or os.environ.get(SHOW_URL_ENV) or SHOW_URL).rstrip("/")
    req = urllib.request.Request(f"{base}/api/v1/piano/stream/{STREAM_PROBE_ID}.mp3", method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except urllib.error.HTTPError as e:
        return e.code == 404 and "json" in (e.headers.get("Content-Type") or "")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def sonos_streams() -> bool:
    raw = (os.environ.get(SONOS_STREAM_ENV) or "").strip()
    if raw:
        return _env_on(SONOS_STREAM_ENV)
    return stream_route_live()


def files_argv(a: argparse.Namespace) -> list[str]:
    """The old cached-file session's command line for these arguments (its own pad minimum)."""
    return [sys.executable, "-m", "piano_render.session_files", "--midi", str(a.midi), "--instrument", a.instrument,
            "--pad-s", f"{max(float(a.pad_s), OLD_SONOS_PAD_MIN_S):.3f}", "--output", a.output]


def _calibrator(output: str, proto: Proto, gain_db: Optional[float], mic: Optional[str] = None):
    """One Calibrator for the session (module docstring) on ``mic`` (--calibration-mic; "" = none hears it, None:
    calibrate's default); None when off ($PIANO_CALIBRATE=0, a silent check) or it can't load."""
    if not _env_on(CALIBRATE_ENV, default=gain_db is None):
        return None
    try:
        from . import calibrate
        cal = calibrate.Calibrator(output, emit=proto.emit, mic=mic)
        log.info("calibration on %s: %s", output, f"mic {cal.mic}" if cal.mic else cal.no_mic_why)
        return cal
    except Exception as e:                               # noqa: BLE001  the piano plays without it
        log.warning("no calibration this session: %s", e)
        return None


def main(argv: Optional[list[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                        format="%(asctime)s piano-session %(levelname)s %(message)s")
    if a.output.startswith(SONOS_PREFIX) and os.environ.get(GAIN_ENV) is None and not sonos_streams():
        log.info("show_service doesn't serve the live stream yet: the old cached-file path plays on Sonos")
        sys.stdout.flush()
        sys.stderr.flush()
        args = files_argv(a)
        os.execv(args[0], args)
    proto = Proto()
    _pdeathsig(signal.SIGTERM)

    def _term(signum, _frame):
        raise _Term(signum)

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _term)
    try:
        gain_db = gain_from_env()
    except ValueError:
        proto.emit({"ev": "error", "why": f"{GAIN_ENV}={os.environ.get(GAIN_ENV)!r} is not a number of dB"})
        return 1
    if a.output.startswith(SONOS_PREFIX) and gain_db is not None:
        proto.emit({"ev": "error", "why": f"{GAIN_ENV} is set: a silent check plays on PC22 only, never the Sonos"})
        return 1
    from . import stream
    prep = stream.prepare_process()
    log.info("process: %s", prep)
    cal = _calibrator(a.output, proto, gain_db, a.calibration_mic)
    folder = keep_dir()
    keep = (lambda: KeepCopy(folder, f"{Path(a.midi).stem} - {a.instrument}")) if folder is not None else None
    if a.output.startswith(SONOS_PREFIX):
        return _main_sonos(a, proto, cal, keep)
    try:
        from . import pc22
    except ImportError as e:
        proto.emit({"ev": "error", "why": f"PC22 playback can't load: {plain(e)}"})
        return 1
    if gain_db is not None:
        try:
            apply_gain(pc22, gain_db)
        except RuntimeError as e:
            proto.emit({"ev": "error", "why": str(e)})
            return 1
    s = Session(a.midi, a.instrument, a.pad_s, proto=proto, calibrator=cal, keep_factory=keep)
    try:
        return s.run(sys.stdin)
    except _Term:
        return 0
    finally:
        s.shutdown()


def _main_sonos(a: argparse.Namespace, proto: Proto, cal: Any, keep: Any) -> int:
    """--output sonos:<zone>: the Session with a SonosOutput (no PC22 player is loaded)."""
    from . import sonos
    zone = a.output[len(SONOS_PREFIX):]
    try:
        out = sonos.SonosOutput(zone, emit=cal.sink_emit(proto.emit) if cal is not None else proto.emit)
    except sonos.SonosError as e:
        proto.emit({"ev": "error", "why": f"couldn't play on Sonos: {e}"})
        return 1
    s = Session(a.midi, a.instrument, a.pad_s, proto=proto, output_factory=lambda: out, output=a.output,
                calibrator=cal, keep_factory=keep)
    try:
        return s.run(sys.stdin)
    except _Term:
        return 0
    finally:
        s.shutdown()


if __name__ == "__main__":
    sys.exit(main())
