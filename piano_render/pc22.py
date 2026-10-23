"""PC22's speakers: play a rendered file, or a stream rendered as it plays, on Windows' default output and say when
its sample 0 reaches the DAC.

    out = PC22Output()
    t_audio0 = out.start("render.flac")     # time.monotonic() (WSL) at which file sample 0 reaches PC22's DAC
    t_audio0 = out.start(streamer)          # or a stream: piano_render.stream.Streamer, paced by out's position
    out.wait()                              # or out.stop()

    python -m piano_render play-pc22 FILE_OR_MID [--instrument ID]      (play() below; a .mid renders as it plays)

How it works: start() spawns ``/mnt/c/Windows/py.exe -3.12 pc22_player.py`` (the windows/ folder, by its wslpath -w
path) and streams the PCM to its stdin from a writer thread (float32 by default; the protocol is in
windows/pc22_player.py's docstring). The player opens WASAPI (shared, auto_convert), buffers prebuffer_ms, starts, and
from its first callback prints ``started`` with that callback's DAC time; start() returns t_audio0 = arrival of that
line + dac_in_ms/1000 - first_frame/rate + extra_ms/1000.

Sources (start's first argument):
- A file (a path): decoded with soundfile and sent in CHUNK_S blocks; the header carries its length.
- A stream: an object with ``blocks(position)`` (piano_render.stream.Streamer), a callable ``f(position)`` returning
  an iterable, or any iterable of blocks ((n, ch) float arrays, or objects with ``.data`` such as stream.Block).
  ``position()`` is this play's: the frame of the stream the player's callback is taking now ((now - t_audio0 +
  the device latency) * rate; 0 until the player has started), so a Streamer stays its ahead_s ahead of what the
  device needs. After a stop it raises ``Halted``, which ends a Streamer's blocks() at once. The header has no length
  ("frames": null): the end of the stream is EOF on a frame boundary, sent once at most END_HOLD_S is left unplayed
  (so a stop until then still fades; a stop after it waits for the last END_HOLD_S to play out). Blocks are
  coalesced to CHUNK_S writes once the player runs (small writes cost a core on both sides of the interop pipe),
  unless the next frame is due within URGENT_S. The prebuffer is the stream's render-ahead (``ahead_s``, else
  STREAM_PREBUFFER_MS), never more: the player waits for it, and a paced source makes no more until the player
  plays. A source that raises ends the stream there (what was sent plays out); the session's ``stream_error`` says
  why. Nothing is written to disk.

The writer stays at most lead_s (2 s) ahead of the player (it knows t_audio0), so the player's stdin is always being
read and a stop is seen at once. stop(): one stop byte and EOF (the player fades 10 ms, plays the device buffer out,
closes the device, exits; ~40 ms to silence); if it hasn't exited within the timeout it is killed. Killing the py.exe
shim from WSL takes the Windows python.exe with it (py.exe's job object), and Windows closes the device. Every player
is tracked and killed at interpreter exit; play() also turns SIGTERM/SIGHUP into a clean stop.

How accurate t_audio0 is (measured on PC22, 22 Oct; see piano_render/tests/test_pc22.py):
- The 'started' line's trip from Windows to WSL: echo round trips over the interop pipe were 0.32 ms min, 0.46 ms
  p50, 0.58 ms p90, 1.6 ms max, so the line is ~0.2-0.8 ms late (not corrected; it makes t_audio0 that much late).
  The player measures dac_in_ms against the moment it writes the line, so its own thread hand-off is not an error.
- PortAudio's DAC time on WASAPI is currentTime + the frames already queued in the shared buffer (the first callback
  says 0 ms). It leaves out the Windows audio engine (one 10 ms period, plus the endpoint buffer) and the device
  itself (here a DisplayPort-audio monitor, "DELL UP2715K"): the sound comes out later than t_audio0 by a constant,
  typically 10-20 ms for the engine plus the monitor's own delay (unknown; tens of ms is common). That constant can
  only be measured acoustically: piano_render/calibrate.py measures it with PC22's own mic and moves the MIDI clock
  by it (its delay is on top of t_audio0, so it is not added here). extra_ms stays 0 unless a caller passes one.
- Underruns (the player's buffer running dry) shift the rest of the file later; done["underruns"] says if any did.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import numpy as np
import soundfile as sf

log = logging.getLogger("piano_render.pc22")

PY = "/mnt/c/Windows/py.exe"
PY_ARGS = ("-3.12",)
PLAYER = Path(__file__).resolve().parent / "windows" / "pc22_player.py"
READY_S = 10.0
STARTED_S = 10.0
STOP_TOKEN = b"\x00"           # after whole frames, an odd tail no frame can make: the player's stop
CHUNK_S = 0.05                 # writer block
PREBUFFER_MS = 300.0           # a file's
STREAM_PREBUFFER_MS = 300.0    # a stream's that says no ahead_s
STREAM_RATE = 48000            # a stream's that says no sr
END_HOLD_S = 0.15              # a stream's EOF goes when at most this much is left to play
URGENT_S = 0.1                 # a stream's blocks go at once (not coalesced) when due within this
SEND_LOCK_S = 0.5              # stop() waits at most this for a write in progress


class PC22Error(RuntimeError):
    pass


class Halted(Exception):
    """A play's position() after its stop: a source paced on it stops making blocks."""


def available() -> bool:
    """WSL interop and Windows' py.exe are here."""
    return (os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
            or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop-late")) and os.access(PY, os.X_OK)


def audio0(t_arrival: float, started: dict, rate: float, extra_ms: float = 0.0) -> float:
    """WSL monotonic time at which file sample 0 reaches the DAC, from the 'started' line and its arrival time."""
    return t_arrival + float(started["dac_in_ms"]) / 1000.0 - float(started.get("first_frame", 0)) / float(rate) \
        + float(extra_ms) / 1000.0


def to_pcm(block: np.ndarray, fmt: str) -> bytes:
    """(n, ch) float -> interleaved little-endian bytes."""
    if fmt == "f32le":
        return np.ascontiguousarray(block, dtype="<f4").tobytes()
    if fmt == "s16le":
        return np.clip(np.rint(np.asarray(block, dtype=np.float64) * 32768.0), -32768, 32767).astype("<i2").tobytes()
    raise ValueError(f"format {fmt!r}")


def to_stereo(block: np.ndarray) -> np.ndarray:
    if block.shape[1] == 2:
        return block
    if block.shape[1] == 1:
        return np.repeat(block, 2, axis=1)
    return block[:, :2]


def is_file_source(source: Any) -> bool:
    return isinstance(source, (str, os.PathLike))


def _block_array(b: Any) -> np.ndarray:
    a = np.asarray(getattr(b, "data", b), dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    if a.ndim != 2 or a.shape[1] < 1:
        raise ValueError(f"a block of shape {a.shape}: want (frames, channels)")
    return a


_LIVE: set = set()
_LIVE_LOCK = threading.Lock()


def _track(p: subprocess.Popen) -> None:
    with _LIVE_LOCK:
        _LIVE.add(p)


def _untrack(p: subprocess.Popen) -> None:
    with _LIVE_LOCK:
        _LIVE.discard(p)


def live_pids() -> list[int]:
    """PIDs (WSL side, the py.exe shims) of the players still running."""
    with _LIVE_LOCK:
        return [p.pid for p in _LIVE if p.poll() is None]


@atexit.register
def _kill_all() -> None:
    with _LIVE_LOCK:
        procs = list(_LIVE)
    for p in procs:
        if p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass


class _Session:
    """One file or one stream on one player process."""

    def __init__(self, source: Any, *, py: str, device: Optional[str], prebuffer_ms: Optional[float],
                 latency_ms: float, fmt: str, lead_s: float, buffer_ms: Optional[float], lead_in_ms: float,
                 extra_ms: float, rate: Optional[int] = None) -> None:
        self.stream = not is_file_source(source)
        self.source = source
        if self.stream:
            self.path = str(getattr(source, "label", None) or "stream")
            self.rate = int(rate or getattr(source, "sr", None) or STREAM_RATE)
            self.frames: Optional[int] = None
            self.duration_s: Optional[float] = None       # known at the end
            ahead = getattr(source, "ahead_s", None)
            ahead_ms = float(ahead) * 1000.0 if isinstance(ahead, (int, float)) and ahead > 0 else None
            pre = float(prebuffer_ms) if prebuffer_ms is not None else (ahead_ms or STREAM_PREBUFFER_MS)
            self.prebuffer_ms = max(10.0, min(pre, ahead_ms) if ahead_ms else pre)   # never more than it makes
        else:
            self.path = str(source)
            try:
                info = sf.info(self.path)
            except (OSError, RuntimeError) as e:
                raise PC22Error(f"can't read {self.path}: {e}") from e
            self.rate = int(info.samplerate)
            self.frames = int(info.frames)
            self.duration_s = self.frames / self.rate if self.rate else 0.0
            self.prebuffer_ms = float(PREBUFFER_MS if prebuffer_ms is None else prebuffer_ms)
        self.py = py
        self.fmt = fmt
        self.lead_s = float(lead_s)
        self.extra_ms = float(extra_ms)
        self.header = {"rate": self.rate, "channels": 2, "format": fmt, "frames": self.frames, "device": device,
                       "prebuffer_ms": self.prebuffer_ms,
                       "buffer_ms": float(buffer_ms) if buffer_ms else self.prebuffer_ms + self.lead_s * 1000 + 3000,
                       "latency_ms": float(latency_ms), "lead_in_ms": float(lead_in_ms)}
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()          # one writer of stdin at a time: the PCM, the stop byte, the EOF
        self.ready_evt = threading.Event()
        self.started_evt = threading.Event()
        self.exited = threading.Event()
        self.halt = threading.Event()             # the writer: stop sending
        self.stopping = False
        self.eof_sent = False                     # a stream's end: stdin closed by the writer
        self.ready: Optional[dict] = None
        self.started: Optional[dict] = None
        self.done: Optional[dict] = None
        self.error: Optional[str] = None
        self.stream_error: Optional[str] = None   # the source raised: the stream ended there
        self.t_spawn = 0.0
        self.t_ready = 0.0
        self.t_started = 0.0                       # arrival of the 'started' line
        self.t_done = 0.0
        self.t0: Optional[float] = None            # t_audio0
        self.dev_latency_s = 0.0                   # the 'ready' line's latency_ms
        self.sent = 0                              # frames written
        self.writes = 0                            # PCM writes (blocks sent)
        self.send_s = 0.0                          # time spent in os.write
        self.stderr_tail: deque = deque(maxlen=20)
        self.threads: list[threading.Thread] = []

    # -- the process --------------------------------------------------------------------------------------------
    def launch(self) -> None:
        if self.py == PY:
            try:
                script = subprocess.run(["wslpath", "-w", str(PLAYER)], capture_output=True, text=True, check=True,
                                        timeout=5).stdout.strip()
            except (OSError, subprocess.SubprocessError) as e:
                raise PC22Error(f"wslpath: {e}") from e
        else:
            script = str(PLAYER)                     # another interpreter (the tests' fake device): the plain path
        self.t_spawn = time.monotonic()
        try:
            self.proc = subprocess.Popen([self.py, *PY_ARGS, script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, bufsize=0, start_new_session=True)
        except OSError as e:
            raise PC22Error(f"can't start {self.py}: {e}") from e
        _track(self.proc)
        for fn, name in ((self._read_out, "pc22-out"), (self._read_err, "pc22-err"), (self._write, "pc22-pcm")):
            t = threading.Thread(target=fn, name=name, daemon=True)
            self.threads.append(t)
        try:
            self._send((json.dumps(self.header) + "\n").encode("utf-8"))
        except OSError:
            pass                                    # it died at once: _read_out says why
        for t in self.threads:
            t.start()

    def _send(self, data: bytes) -> None:
        fd = self.proc.stdin.fileno()
        mv = memoryview(data)
        t = time.monotonic()
        while mv:
            n = os.write(fd, mv)
            mv = mv[n:]
        self.send_s += time.monotonic() - t

    def _pos(self) -> float:
        t0 = self.t0
        return 0.0 if t0 is None else (time.monotonic() - t0 + self.dev_latency_s) * self.rate

    def position(self) -> float:
        """The stream frame the player's callback takes now (module docstring); Halted after a stop."""
        if self.halt.is_set():
            raise Halted()
        return self._pos()

    def _room(self, want: int) -> bool:
        """Wait until ``want`` more frames keep the writer within lead_s of the player; False once halted."""
        ahead = self.prebuffer_ms / 1000.0 * self.rate + self.lead_s * self.rate
        while not self.halt.is_set():
            t0 = self.t0
            allowed = ahead if t0 is None else max(ahead, (time.monotonic() - t0) * self.rate
                                                    + self.lead_s * self.rate)
            room = int(allowed) - self.sent
            if room >= want:                         # whole chunks only: dribbles cost a core on both sides
                return True
            self.halt.wait(min(0.05, max(0.005, (want - room) / self.rate)))
        return False

    def _file_blocks(self) -> Iterator[np.ndarray]:
        with sf.SoundFile(self.path) as f:
            chunk = max(1, int(self.rate * CHUNK_S))
            while not self.halt.is_set():
                left = self.frames - self.sent
                want = min(chunk, left) if left > 0 else chunk
                if not self._room(want):
                    return
                block = f.read(want, dtype="float32", always_2d=True)
                if len(block) == 0:
                    return
                yield block

    def _source_iter(self):
        src = self.source
        blocks = getattr(src, "blocks", None)
        if callable(blocks):
            return iter(blocks(position=self.position))
        if callable(src):
            return iter(src(self.position))
        return iter(src)

    def _stream_blocks(self) -> Iterator[np.ndarray]:
        chunk = max(1, int(self.rate * CHUNK_S))
        urgent = URGENT_S * self.rate
        held: list = []
        held_n = 0
        it = None
        try:
            it = self._source_iter()
            while not self.halt.is_set():
                try:
                    a = _block_array(next(it))
                except StopIteration:
                    break
                if not len(a):
                    continue
                held.append(a)
                held_n += len(a)
                if held_n < chunk and self.t0 is not None and self.sent >= self._pos() + urgent:
                    continue                         # coalesce: whole chunks while nothing is due soon
                data = np.concatenate(held) if len(held) > 1 else held[0]
                held, held_n = [], 0
                for i in range(0, len(data), chunk):
                    piece = data[i:i + chunk]
                    if not self._room(len(piece)):
                        return
                    yield piece
        except Halted:
            return
        except Exception as e:                       # noqa: BLE001  the source broke: what was sent plays out
            if not self.halt.is_set():
                self.stream_error = f"{type(e).__name__}: {e}"
                log.warning("pc22: the stream %s failed: %s", self.path, self.stream_error)
        finally:
            close = getattr(it, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:                    # noqa: BLE001
                    pass
        if held and not self.halt.is_set():
            data = np.concatenate(held) if len(held) > 1 else held[0]
            for i in range(0, len(data), chunk):
                piece = data[i:i + chunk]
                if not self._room(len(piece)):
                    return
                yield piece

    def _write(self) -> None:
        try:
            for block in (self._stream_blocks() if self.stream else self._file_blocks()):
                with self.send_lock:
                    if self.halt.is_set():
                        break
                    self._send(to_pcm(to_stereo(block), self.fmt))
                self.sent += len(block)
                self.writes += 1
            if self.stream:
                self.duration_s = self.sent / self.rate
                self._end_stream()
        except (OSError, ValueError, RuntimeError) as e:   # the player went away (BrokenPipe), or the file broke
            if not self.halt.is_set() and self.proc.poll() is None:
                log.warning("pc22: sending %s: %s", self.path, e)

    def _end_stream(self) -> None:
        """EOF once at most END_HOLD_S is left to play; at once if less than the prebuffer was sent (EOF starts the
        player then)."""
        hold = END_HOLD_S * self.rate
        need = int(self.prebuffer_ms / 1000.0 * self.rate)
        while not self.halt.is_set():
            if self.t0 is None:
                if self.sent < need:
                    break
                self.started_evt.wait(0.05)          # it starts by itself
                continue
            left = self.sent - self._pos()
            if left <= hold:
                break
            self.halt.wait(min(0.05, max(0.005, (left - hold) / self.rate)))
        with self.send_lock:
            if self.halt.is_set():
                return                               # stop() sends the stop byte and the EOF
            self.eof_sent = True
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def _read_out(self) -> None:
        for raw in iter(self.proc.stdout.readline, b""):
            t = time.monotonic()
            line = raw.decode("utf-8", "replace").strip()
            kind, _, rest = line.partition(" ")
            try:
                if kind == "started":
                    self.started = json.loads(rest)
                    self.t_started = t
                    self.t0 = audio0(t, self.started, self.rate, self.extra_ms)
                    self.started_evt.set()
                elif kind == "ready":
                    self.ready = json.loads(rest)
                    self.t_ready = t
                    try:
                        self.dev_latency_s = max(0.0, float(self.ready.get("latency_ms") or 0.0) / 1000.0)
                    except (TypeError, ValueError):
                        self.dev_latency_s = 0.0
                    self.ready_evt.set()
                elif kind == "done":
                    self.done = json.loads(rest)
                    self.t_done = t
                elif kind == "error":
                    self.error = rest or "unknown"
                    self.ready_evt.set()
                    self.started_evt.set()
                else:
                    log.warning("pc22: unexpected line from the player: %r", line[:200])
            except ValueError:
                log.warning("pc22: bad line from the player: %r", line[:200])
        # stdout closed: the player is exiting
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._finish()

    def _read_err(self) -> None:
        for raw in iter(self.proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.stderr_tail.append(line)
                log.debug("pc22: %s", line)

    def _finish(self) -> None:
        self.halt.set()
        got = self.send_lock.acquire(timeout=SEND_LOCK_S)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        finally:
            if got:
                self.send_lock.release()
        _untrack(self.proc)
        if self.error is None and self.done is None:
            self.error = "the player exited (rc %s): %s" % (self.proc.returncode,
                                                            " | ".join(list(self.stderr_tail)[-3:]) or "no output")
        self.ready_evt.set()
        self.started_evt.set()
        self.exited.set()

    # -- control ------------------------------------------------------------------------------------------------
    def stop(self, timeout: float = 1.0) -> None:
        with self.lock:
            first = not self.stopping
            self.stopping = True
        p = self.proc
        if p is None:                                # never launched
            self.halt.set()
            self.ready_evt.set()
            self.started_evt.set()
            self.exited.set()
            return
        if first:
            self.halt.set()
            w = self.threads[2] if len(self.threads) > 2 else None
            if w is not None and w.is_alive() and w is not threading.current_thread():
                w.join(0.3)
            if p.poll() is None:
                got = self.send_lock.acquire(timeout=SEND_LOCK_S)   # never in the middle of a block's write
                try:
                    if got and not self.eof_sent:
                        fd = p.stdin.fileno()
                        os.set_blocking(fd, False)
                        os.write(fd, STOP_TOKEN)
                except (OSError, ValueError):
                    pass
                finally:
                    try:
                        p.stdin.close()
                    except OSError:
                        pass
                    if got:
                        self.send_lock.release()
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.warning("pc22: the player didn't stop in %.1f s; killing it", timeout)
            p.kill()
            p.wait()
        if not self.exited.wait(2.0):
            self._finish()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self.exited.wait(timeout)


class PC22Output:
    """Plays one file or stream at a time on PC22's speakers. start() returns t_audio0 (WSL time.monotonic() at which
    sample 0 of the file or stream reaches PC22's DAC); stop(); wait(timeout) -> True once the player has exited;
    position() (a stream's, module docstring). Thread-safe; a start() while playing stops the previous one first. Use
    as a context manager to be sure it stops. prebuffer_ms None: PREBUFFER_MS for a file, the stream's ahead_s for a
    stream."""

    def __init__(self, *, device: Optional[str] = None, prebuffer_ms: Optional[float] = None,
                 latency_ms: float = 20.0, fmt: str = "f32le", lead_s: float = 2.0, buffer_ms: Optional[float] = None,
                 lead_in_ms: float = 0.0, extra_ms: float = 0.0, py: str = PY) -> None:
        if fmt not in ("f32le", "s16le"):
            raise ValueError(f"fmt {fmt!r}")
        self.opts = dict(device=device, prebuffer_ms=prebuffer_ms, latency_ms=latency_ms, fmt=fmt, lead_s=lead_s,
                         buffer_ms=buffer_ms, lead_in_ms=lead_in_ms, extra_ms=extra_ms)
        self.py = py
        self._lock = threading.Lock()
        self._s: Optional[_Session] = None

    # what the last start() learnt
    @property
    def session(self) -> Optional[_Session]:
        return self._s

    @property
    def t_audio0(self) -> Optional[float]:
        return self._s.t0 if self._s else None

    @property
    def ready(self) -> Optional[dict]:
        return self._s.ready if self._s else None

    @property
    def started(self) -> Optional[dict]:
        return self._s.started if self._s else None

    @property
    def done(self) -> Optional[dict]:
        return self._s.done if self._s else None

    @property
    def playing(self) -> bool:
        s = self._s
        return bool(s and s.proc is not None and not s.exited.is_set())

    def position(self) -> float:
        """The current play's position() (module docstring); 0 with none; Halted once it is stopped."""
        s = self._s
        return 0.0 if s is None else s.position()

    def start(self, source, **overrides) -> float:
        """Starts a file (a path) or a stream (module docstring, Sources); returns t_audio0 once the player's first
        callback is in. Raises PC22Error (the player is then gone). overrides: any constructor option for this play
        only (e.g. lead_in_ms), and ``rate`` for a stream that has no ``sr``."""
        if not available() and self.py == PY:
            raise PC22Error("no Windows here (WSL interop, /mnt/c/Windows/py.exe)")
        opts = {**self.opts, **overrides}
        s = _Session(source if not is_file_source(source) else str(source), py=self.py, **opts)
        with self._lock:
            old, self._s = self._s, s
        if old is not None:
            old.stop()
        try:
            s.launch()
            if not s.ready_evt.wait(READY_S):
                raise PC22Error(f"no 'ready' from the player in {READY_S:.0f} s")
            self._check(s, "ready")
            if not s.started_evt.wait(STARTED_S):
                raise PC22Error(f"no 'started' from the player in {STARTED_S:.0f} s")
            self._check(s, "started")
        except BaseException:
            s.stop(timeout=0.5)
            raise
        log.info("pc22: %s on %s, %.0f ms to start, t_audio0 in %+.1f ms", Path(s.path).name,
                 (s.ready or {}).get("device"), (s.t_started - s.t_spawn) * 1000, (s.t0 - time.monotonic()) * 1000)
        return s.t0

    @staticmethod
    def _check(s: _Session, what: str) -> None:
        if s.stopping:
            raise PC22Error(f"stopped before '{what}'")
        if s.error:
            raise PC22Error(s.error)
        if (what == "ready" and s.ready is None) or (what == "started" and s.started is None):
            raise PC22Error(s.stream_error and f"the stream failed: {s.stream_error}"
                            or f"the player exited before '{what}'")

    def stop(self, timeout: float = 1.0) -> None:
        """Stops the file (10 ms fade); kills the player if it hasn't exited in timeout s. Safe to call any time."""
        s = self._s
        if s is not None:
            s.stop(timeout)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """True once the player has exited (played to the end, stopped or failed); False on timeout."""
        s = self._s
        return True if s is None else s.wait(timeout)

    def close(self) -> None:
        self.stop()

    def __enter__(self) -> "PC22Output":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def __del__(self) -> None:                       # never leave a player behind
        try:
            s = self._s
            if s is not None and s.proc is not None and s.proc.poll() is None:
                s.proc.kill()
        except Exception:                            # noqa: BLE001
            pass


def play(path, *, instrument: Optional[str] = None, room: Optional[str] = None, realism: bool = True,
         device: Optional[str] = None, progress: Optional[Callable] = None, out: Optional[PC22Output] = None) -> dict:
    """Plays an audio file, or a .mid rendered as it plays (piano_render.stream; nothing is kept), on PC22 to the end;
    Ctrl-C, SIGTERM and SIGHUP stop it cleanly. Returns what happened: path, t_audio0, startup_s, ready, started,
    done (and for a .mid "render": the instrument and the stream's stats). ``progress`` is not used any more."""
    path = Path(path)
    render_info = None
    source: Any = path
    streamer = None
    if path.suffix.lower() in (".mid", ".midi"):
        from piano_render import stream as _stream                      # only now: heavy, and optional here
        _stream.prepare_process()
        streamer = _stream.Streamer(str(path), instrument, room=room, realism=realism)
        streamer.open()
        render_info = {"instrument": streamer.inst.get("id", instrument), "streamed": True}
        source = streamer
    out = out or PC22Output(device=device)
    old = {}
    if threading.current_thread() is threading.main_thread():
        def _term(signum, _frame):
            raise KeyboardInterrupt(f"signal {signum}")
        for sig in (signal.SIGTERM, signal.SIGHUP):
            try:
                old[sig] = signal.signal(sig, _term)
            except (ValueError, OSError):
                pass
    t = time.monotonic()
    t0, startup = None, None
    try:
        t0 = out.start(source if streamer is not None else path)
        startup = time.monotonic() - t
        while not out.wait(0.2):
            pass
    except PC22Error as e:
        s = out.session
        return {"path": str(path), "render": render_info, "t_audio0": None, "startup_s": None,
                "duration_s": s.duration_s if s else None, "ready": out.ready, "started": out.started,
                "done": out.done, "error": str(e)}
    except KeyboardInterrupt:
        out.stop()
        t0 = out.t_audio0
    finally:
        out.stop()
        for sig, h in old.items():
            signal.signal(sig, h)
        if streamer is not None:
            render_info = dict(render_info or {}, stats={k: v for k, v in streamer.stats.items()
                                                         if k in ("load_s", "audio_s", "cpu_pct_of_core",
                                                                  "late_blocks", "max_late_ms")})
            streamer.close()
    s = out.session
    return {"path": str(path), "render": render_info, "t_audio0": t0,
            "startup_s": None if startup is None else round(startup, 3),
            "duration_s": s.duration_s if s else None, "ready": out.ready, "started": out.started, "done": out.done,
            "error": s.error if s else None}
