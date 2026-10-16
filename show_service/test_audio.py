"""The test-audio child: a music file played as VBAN audio, the way a room mic's sender streams (README S3.10).

    python -m show_service.test_audio --file F [--port 6982] [--bind 127.0.0.1] [--stream test-audio] [--channels 1]
    (also: python -m show_service.sources audio ...)

sources.py starts it for ``POST /api/v1/test {"source": "audio"}`` and stops it with SIGTERM; nobody else does.

- **Receiver-initiated** (§3.1, show_sender's rule): it binds UDP (bind, port) and streams only to the source address
  of the latest SUBSCRIBE that names its stream, for 5 s after it. With no subscriber it sends nothing and decoding
  pauses where it is; when a subscriber comes back it starts a new ``sess`` (vban.py's sender rules: more than
  250 ms of capture time lost is a new sess), so the receiver re-anchors cleanly.
- **Decode:** ``ffmpeg -nostdin -v error -i F -map 0:a:0 -f f32le -ac C -ar 48000 -`` through a pipe, looping: at EOF
  a new ffmpeg starts and counters, ``n`` and ``sess`` carry on (the music simply repeats). A small thread keeps
  0.5 s decoded ahead, so the pacing never waits for an ffmpeg to start; if it ever runs dry the packet goes out as
  silence (counted as an underrun) rather than late.
- **Send:** ``vban.pack_audio(stream, counter, pcm, 48000, INT16)``, 128 frames a packet, paced on
  ``time.monotonic``: packets go out in bursts of about 4 every 10.67 ms and never more than 4 packets ahead of real
  time. Falling more than 250 ms behind (the process was stalled) starts a new sess rather than bursting.
- **Heartbeat** every 0.5 s while subscribed (its own TEXT counter, the audio stream's name): the newest packet's
  ``frame``/``n``, ``t_cap_ns`` = the monotonic_ns when its first sample was due, ``in_lat_ms`` 0, ``dev``
  ``file:<name>``, state ``ok``, peak/rms dBFS per channel over the last 0.5 s, clips.
- **SIGTERM/SIGINT:** ffmpeg is killed and it exits 0. A file that decodes to nothing exits 1 (ffmpeg's last lines
  are logged); a missing file or ffmpeg exits 2.

At module level it imports only the stdlib, numpy and vban (no aiohttp, nothing of show_service's engine).
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import queue
import uuid
from collections import deque
from typing import Callable, Optional

import numpy as np

try:
    from . import vban
except ImportError:                                    # run as a plain script from the repo
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from show_service import vban                      # type: ignore[no-redef]

log = logging.getLogger("show.test_audio")

FS = 48000
FRAMES = vban.DEFAULT_FRAMES                           # 128: fits INT16 at 1-2 channels (frames_per_packet)
PKT_S = FRAMES / FS
AHEAD = 4                                              # never more than this many packets ahead of real time
SUB_TTL_S = 5.0
HEARTBEAT_S = 0.5
NEW_SESS_BEHIND_S = 0.25
IDLE_POLL_S = 0.02
PREFETCH_S = 0.5                                       # decoded ahead of the pacing (Prefetch)
STREAM = "test-audio"
PARENT_CHECK_S = 1.0                                   # the parent watch (show_service gone: stop)


def new_sess(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class DecodeError(RuntimeError):
    """The file gave no audio at all (ffmpeg's last lines in the message)."""


class Decoder:
    """ffmpeg -> float32 frames x channels at 48 kHz, looping the file forever."""

    def __init__(self, path: pathlib.Path, channels: int = 1, *, ffmpeg: str = "ffmpeg") -> None:
        self.path, self.channels, self.ffmpeg = pathlib.Path(path), int(channels), ffmpeg
        self.proc: Optional[subprocess.Popen] = None
        self.loops = 0
        self._got = 0                                  # bytes from the current ffmpeg
        self._carry = b""
        self._err: deque[str] = deque(maxlen=5)
        self._err_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()                  # start vs close (Prefetch reads on its own thread)
        self.closed = False

    def _start(self) -> subprocess.Popen:
        cmd = [self.ffmpeg, "-nostdin", "-v", "error", "-i", str(self.path), "-map", "0:a:0", "-f", "f32le",
               "-ac", str(self.channels), "-ar", str(FS), "-"]
        with self._lock:
            if self.closed:
                raise DecodeError(f"{self.path.name}: closed")
            proc = self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE, bufsize=0)
        self._got = 0
        self._err_thread = threading.Thread(target=self._drain, args=(proc,), name="ffmpeg-stderr", daemon=True)
        self._err_thread.start()
        return proc

    def _drain(self, proc: subprocess.Popen) -> None:
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                self._err.append(text)

    def read(self, frames: int) -> np.ndarray:
        """Exactly ``frames`` x channels float32; restarts ffmpeg at EOF. DecodeError if a fresh ffmpeg gives none."""
        need = frames * self.channels * 4
        buf = self._carry
        while len(buf) < need:
            if self.closed:
                raise DecodeError(f"{self.path.name}: closed")
            proc = self.proc or self._start()
            try:
                chunk = proc.stdout.read(need - len(buf))
            except (OSError, ValueError):               # closed under us (close() from another thread)
                chunk = b""
            if chunk:
                buf += chunk
                self._got += len(chunk)
                continue
            got = self._got
            self._reap()
            if self.closed:
                raise DecodeError(f"{self.path.name}: closed")
            if got == 0:
                why = " | ".join(self._err) or "no audio"
                raise DecodeError(f"{self.path.name}: ffmpeg gave no audio ({why})")
            self.loops += 1
            log.info("%s: end of file, looping (%d)", self.path.name, self.loops)
        self._carry = buf[need:]
        return np.frombuffer(buf[:need], dtype="<f4").reshape(frames, self.channels)

    def get(self) -> np.ndarray:
        """One packet's frames (the sender's interface; synchronous here, Prefetch's is not)."""
        return self.read(FRAMES)

    def prime(self) -> None:
        """Decode the first packet now (kept for the first read), so a file that won't decode fails at start."""
        first = self.read(FRAMES).tobytes()
        self._carry = first + self._carry

    def _reap(self) -> None:
        with self._lock:
            proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
        if self._err_thread is not None:
            self._err_thread.join(timeout=0.5)             # ffmpeg's last words, for a DecodeError

    def close(self) -> None:
        """Kill ffmpeg (SIGTERM path); a read after this raises DecodeError."""
        with self._lock:
            self.closed = True
            proc, self.proc = self.proc, None
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        try:
            proc.stdout.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


class Prefetch:
    """Decodes ahead on its own thread ("decode"): ``get()`` never waits. With no subscriber the queue stays full,
    the thread blocks, and ffmpeg blocks on its pipe: decoding pauses where it is."""

    def __init__(self, dec: Decoder, seconds: float = PREFETCH_S) -> None:
        self.dec, self.channels, self.path = dec, dec.channels, dec.path
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=max(2, int(seconds / PKT_S)))
        self.error: Optional[DecodeError] = None
        self.underruns = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="decode", daemon=True)

    def start(self) -> "Prefetch":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                block = self.dec.read(FRAMES)
            except DecodeError as e:
                if not self._stop.is_set():
                    self.error = e
                return
            while not self._stop.is_set():
                try:
                    self.q.put(block, timeout=0.1)
                    break
                except queue.Full:
                    continue

    def get(self) -> Optional[np.ndarray]:
        """The next packet's frames, or None when the decoder hasn't kept up. DecodeError once it has failed."""
        try:
            return self.q.get_nowait()
        except queue.Empty:
            if self.error is not None:
                raise self.error
            self.underruns += 1
            return None

    def close(self) -> None:
        self._stop.set()
        self.dec.close()
        self._thread.join(timeout=2.0)


class Subscriptions:
    """The latest SUBSCRIBE that names our stream: where to send, until when."""

    def __init__(self, stream: str) -> None:
        self.stream = stream
        self.addr: Optional[tuple] = None
        self.until = 0.0
        self.count = 0

    def on_packet(self, buf: bytes, addr: tuple, now: float) -> bool:
        pkt = vban.parse(buf)
        if pkt is None or not vban.is_subscribe(pkt):
            return False
        if self.stream not in (vban.subscribed_streams(pkt) or []):
            return False
        self.addr, self.until = addr, now + SUB_TTL_S
        self.count += 1
        return True

    def target(self, now: float) -> Optional[tuple]:
        return self.addr if self.addr is not None and now <= self.until else None


class AudioFileSender:
    """Paces a Decoder (or a Prefetch) out of one UDP socket as VBAN ``stream`` (one thread: ``run``)."""

    def __init__(self, sock: socket.socket, source: "Decoder | Prefetch", *, stream: str = STREAM, dev: str = "",
                 clock_ns: Callable[[], int] = time.monotonic_ns) -> None:
        self.sock, self.src, self.stream = sock, source, stream
        self.channels = source.channels
        self.dev = dev or f"file:{source.path.name}"
        self.clock_ns = clock_ns
        self.subs = Subscriptions(stream)
        self.sess: Optional[str] = None
        self.counter = 0
        self.text_counter = 0
        self.n = 0
        self.k = 0                                     # packets sent in this sess
        self.t0_ns = 0
        self.last: Optional[tuple[int, int, int]] = None   # (frame, n, t_cap_ns) of the newest packet
        self.sent = 0
        self.heartbeats = 0
        self.sessions = 0
        self.xruns = 0
        self.underruns = 0
        self.clips = 0
        self._peak = np.zeros(self.channels)
        self._sq = np.zeros(self.channels)
        self._cnt = 0
        self._last_hb = -1.0

    # -- network ------------------------------------------------------------------------------------------------------
    def poll_socket(self, now: float) -> None:
        """Drain pending datagrams (SUBSCRIBEs) without blocking."""
        while True:
            try:
                buf, addr = self.sock.recvfrom(2048)
            except (BlockingIOError, InterruptedError, socket.timeout):
                return
            except OSError:
                return
            if self.subs.on_packet(buf, addr, now):
                log.debug("subscribe from %s", addr)

    def _send(self, addr: tuple, packet: bytes) -> None:
        try:
            self.sock.sendto(packet, addr)
        except OSError:                                # an ICMP refusal: the receiver went away; keep pacing
            pass

    # -- timeline -----------------------------------------------------------------------------------------------------
    def begin(self, now_ns: int) -> None:
        """A new sess: counters and n from 0, real-time anchor now (start, resume after a pause, or a stall)."""
        self.sess = new_sess(self.stream)
        self.sessions += 1
        self.counter = 0
        self.n = 0
        self.k = 0
        self.t0_ns = now_ns
        self.last = None
        self._last_hb = -1.0
        log.info("sess %s", self.sess)

    def due_ns(self, k: int) -> int:
        return self.t0_ns + (k * FRAMES * 1_000_000_000) // FS

    def step(self, stop: threading.Event) -> float:
        """One pass: SUBSCRIBEs, due packets, heartbeat. Returns how long to sleep."""
        now_ns = self.clock_ns()
        now = now_ns / 1e9
        self.poll_socket(now)
        addr = self.subs.target(now)
        if addr is None:
            if self.sess is not None:
                log.info("no subscriber: paused at %d samples", self.n)
                self.sess = None                       # a resume is a new sess
            return IDLE_POLL_S
        if self.sess is None:
            self.begin(now_ns)
        elif (now_ns - self.due_ns(self.k)) / 1e9 > NEW_SESS_BEHIND_S:
            self.xruns += 1
            log.warning("fell %.0f ms behind: new sess", (now_ns - self.due_ns(self.k)) / 1e6)
            self.begin(now_ns)
        horizon = now_ns + int(AHEAD * PKT_S * 1e9)
        while self.due_ns(self.k) <= horizon and not stop.is_set():
            pcm = self.src.get()
            if pcm is None:                                # the decoder fell behind: silence, on time
                pcm = np.zeros((FRAMES, self.channels), np.float32)
                self.underruns += 1
            self._send(addr, vban.pack_audio(self.stream, self.counter, pcm, FS, vban.INT16))
            self.last = (self.counter, self.n, self.due_ns(self.k))
            self._meter(pcm)
            self.sent += 1
            self.counter = (self.counter + 1) & 0xFFFFFFFF
            self.n += FRAMES
            self.k += 1
        if self._last_hb < 0 or now - self._last_hb >= HEARTBEAT_S:
            self._heartbeat(addr)
            self._last_hb = now
        # wake when the next packet is one packet ahead: then up to AHEAD go out together
        wake_ns = self.due_ns(self.k) - int(PKT_S * 1e9)
        return max(0.0, min(IDLE_POLL_S, (wake_ns - self.clock_ns()) / 1e9))

    def _meter(self, pcm: np.ndarray) -> None:
        a = np.abs(pcm)
        self._peak = np.maximum(self._peak, a.max(axis=0))
        self._sq += (pcm.astype(np.float64) ** 2).sum(axis=0)
        self._cnt += pcm.shape[0]
        self.clips += int((a >= 1.0).sum())

    def _heartbeat(self, addr: tuple) -> None:
        def db(x: np.ndarray) -> list[float]:
            return [round(max(-120.0, 20.0 * math.log10(v)) if v > 0 else -120.0, 1) for v in x]
        rms = np.sqrt(self._sq / self._cnt) if self._cnt else np.zeros(self.channels)
        frame, n, t_cap = self.last if self.last else (None, None, None)
        obj = vban.make_heartbeat(sess=self.sess, frame=frame, n=n, t_cap_ns=t_cap, in_lat_ms=0.0, dev=self.dev,
                                  state="ok", xruns=self.xruns, peak_dbfs=db(self._peak), rms_dbfs=db(rms),
                                  clips=self.clips)
        self._send(addr, vban.pack_text(self.stream, self.text_counter, obj))
        self.text_counter = (self.text_counter + 1) & 0xFFFFFFFF
        self.heartbeats += 1
        self._peak[:] = 0.0
        self._sq[:] = 0.0
        self._cnt = 0

    def run(self, stop: threading.Event) -> None:
        """Until ``stop``; DecodeError propagates (the caller exits 1)."""
        self.sock.setblocking(False)
        while not stop.is_set():
            wait = self.step(stop)
            if wait > 0:
                stop.wait(wait)


def _watch_parent(ppid: int, stop: threading.Event, every_s: float = PARENT_CHECK_S) -> None:
    """Stop when the parent (show_service) is gone: a crash or a SIGKILL there never leaves this child holding its
    port (sources.py also runs it under PR_SET_PDEATHSIG; this covers a start by hand or without prctl)."""
    while not stop.wait(every_s):
        if os.getppid() != ppid:
            log.warning("parent %d is gone: stopping", ppid)
            stop.set()
            return


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m show_service.test_audio",
                                 description="Play a music file as VBAN audio (show_service's test audio).")
    ap.add_argument("--file", required=True)
    ap.add_argument("--port", type=int, default=6982, help="UDP port to listen for SUBSCRIBE on (0 = any free)")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--stream", default=STREAM)
    ap.add_argument("--channels", type=int, default=1, choices=(1, 2))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = pathlib.Path(a.file).expanduser()
    if not path.is_file():
        log.error("no such file: %s", path)
        return 2
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.error("ffmpeg is not installed")
        return 2
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(target=_watch_parent, args=(os.getppid(), stop), name="parent-watch", daemon=True).start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((a.bind, a.port))
    except OSError as e:
        log.error("can't bind udp %s:%d: %s", a.bind, a.port, e)
        sock.close()
        return 2
    dec = Decoder(path, a.channels, ffmpeg=ffmpeg)
    pre = Prefetch(dec)
    sender = AudioFileSender(sock, pre, stream=a.stream)
    host, port = sock.getsockname()[:2]
    log.info("test-audio on udp %s:%d: stream %s, %s, %d ch, pid %d", host, port, a.stream, path.name, a.channels,
             os.getpid())
    code = 0
    try:
        dec.prime()                                    # fail fast on a file that won't decode
        pre.start()
        sender.run(stop)
    except DecodeError as e:
        log.error("%s", e)
        code = 1
    finally:
        pre.close()
        sock.close()
    log.info("stopped: %d packets, %d heartbeats, %d sess, %d loops, %d underruns", sender.sent, sender.heartbeats,
             sender.sessions, dec.loops, sender.underruns)
    return code


if __name__ == "__main__":
    sys.exit(main())
