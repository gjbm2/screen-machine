"""NVENC encoder running as an ffmpeg subprocess.

raw NV12 frames -> stdin (1 MiB pipe) -> {h264,hevc,av1}_nvenc (-delay 0, no B-frames)
-> FLV on stdout (one length-prefixed tag per packet, flushed immediately)
-> reader thread -> on_config / on_frame callbacks.

Why not in-process? On this workstation (NVIDIA driver 566.14 = NVENC API 12.2):
  * PyAV wheels bundle FFmpeg 8, which requires NVENC API 13 / driver >= 570;
  * PyNvVideoCodec 2.2 works but its encoder holds output back by 3 frames.
The distro ffmpeg 6.1.1 supports API 12 and honours -delay 0.
"""
from __future__ import annotations

import fcntl
import secrets
import subprocess
import threading
import time
from typing import Callable, Optional, Sequence

from ..stats import set_thread_name
from .bitstream import FlvVideoPacket, SampleFixer, StreamConfig, iter_flv_video

F_SETPIPE_SZ = 1031
ENCODERS = {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"}


def _grow_pipe(fd: int, size: int = 1 << 20) -> None:
    try:
        fcntl.fcntl(fd, F_SETPIPE_SZ, size)
    except OSError:
        pass  # falls back to the 64 KiB default (about 3x slower at 4K)


class NvencPipeline:
    def __init__(self, codec: str, width: int, height: int, fps: int, *, gpu: str = "0",
                 bitrate: str = "8M", gop_seconds: float = 1.0, preset: str = "p1",
                 profile: Optional[str] = None,
                 vbv_frames: Optional[float] = None, fix_reorder: bool = True, slices: Optional[int] = None,
                 extra_args: Sequence[str] = (), delay: int = 0, name: str = "", id_base: int = 0,
                 clock: Callable[[], float],
                 on_config: Callable[[StreamConfig], None],
                 on_frame: Callable[[int, bool, bytes, float], None],
                 on_exit: Callable[[int, str], None]):
        if codec not in ENCODERS:
            raise ValueError(f"codec must be one of {list(ENCODERS)}")
        self.codec, self.w, self.h, self.fps = codec, width, height, fps
        self.clock = clock
        self.on_config, self.on_frame, self.on_exit = on_config, on_frame, on_exit
        self.fixer = SampleFixer(codec, width, height, fix_reorder=fix_reorder)
        self.frame_bytes = width * height * 3 // 2
        gop = max(1, round(gop_seconds * fps))
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
               "-f", "rawvideo", "-pix_fmt", "nv12", "-s", f"{width}x{height}", "-r", str(fps),
               "-i", "pipe:0",
               "-c:v", ENCODERS[codec], "-gpu", str(gpu), "-preset", preset, "-tune", "ull",
               "-zerolatency", "1", "-delay", str(int(delay)), "-rc", "cbr", "-b:v", bitrate, "-maxrate", bitrate,
               "-forced-idr", "1"]
        if vbv_frames:
            bits = _parse_rate(bitrate)
            cmd += ["-bufsize", str(int(bits / fps * vbv_frames))]
        if codec != "av1":
            cmd += ["-bf", "0"]
        if profile:
            cmd += ["-profile:v", profile]
        if slices and codec != "av1":
            # Chrome's software H.264 decoder uses slice threading only in live/low-delay mode,
            # so a single-slice 4K stream decodes on one core. Slices only matter for SW fallback.
            cmd += ["-slices", str(int(slices))]
        # No intra refresh: NVENC then emits a single IDR, and the server's drop-to-keyframe (overflow, late
        # join, re-hello) would wait forever for the next one.  Periodic IDRs every `gop` frames.
        cmd += ["-g", str(gop)]
        cmd += list(extra_args)  # stream_service: VUI colour tags etc.
        cmd += ["-fps_mode", "passthrough", "-flush_packets", "1", "-f", "flv", "pipe:1"]
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, bufsize=0)
        _grow_pipe(self.proc.stdin.fileno())
        _grow_pipe(self.proc.stdout.fileno())
        self._stderr_tail: list[str] = []
        self._closed = False
        self.tag = name             # OS thread names (top -H): enc-<tag>, encerr-<tag>
        self.frames_out = 0
        self.id_base = id_base      # frame ids continue from here (a fallback from the GPU encoder mid-session)
        self.last_frame_id = id_base - 1
        self.reader = threading.Thread(target=self._read_loop, name=f"nvenc-read-{codec}", daemon=True)
        self.errlog = threading.Thread(target=self._stderr_loop, name="nvenc-stderr", daemon=True)
        self.errlog.start()  # first: the reader joins it on exit
        self.reader.start()

    # -- input -------------------------------------------------------------
    def submit(self, frame: memoryview, frame_id: int = -1, idr: bool = False) -> float:
        """Write one NV12 frame; blocks until the pipe has taken all of it.  ``frame_id`` and ``idr`` are the GPU
        encoder's (nvenc.GpuEncoder): frame ids here are the input order, and the pipe cannot force an IDR.

        Returns the clock() time at which the write completed."""
        if len(frame) != self.frame_bytes:
            raise ValueError("frame size mismatch")
        # stdin is unbuffered (bufsize=0): one write(2) may take less than everything (e.g. after a signal)
        mv = memoryview(frame).cast("B")
        off, n = 0, len(mv)
        while off < n:
            off += self.proc.stdin.write(mv[off:]) or 0
        return self.clock()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    # -- output ------------------------------------------------------------
    def _read_loop(self) -> None:
        set_thread_name(f"enc-{self.tag}")
        err = None
        try:
            for pkt in iter_flv_video(self.proc.stdout):
                self._handle(pkt)
        except EOFError:
            pass  # stdout closed before the FLV header / mid-tag: ffmpeg exited (bad GPU, option...): rc + stderr say why
        except Exception as e:  # parse/SampleFixer error or a raising callback: surfaced via on_exit
            err = f"reader error: {type(e).__name__}: {e}"
            # Nobody drains stdout any more: ffmpeg would block on its full pipe, stop reading stdin and
            # wedge the source thread in submit().  Kill it; submit() then fails with BrokenPipeError.
            try:
                self.proc.kill()
            except OSError:
                pass
        finally:
            rc = self.proc.wait()
            self.errlog.join(timeout=1)  # stderr reaches EOF with the process: report its complete tail
            if err is None:
                self.on_exit(rc, "\n".join(self._stderr_tail[-20:]))
            else:
                self.on_exit(-1, "\n".join([err + f" (ffmpeg killed, rc={rc})", *self._stderr_tail[-19:]]))

    def _handle(self, pkt: FlvVideoPacket) -> None:
        t = self.clock()
        if pkt.is_config:
            self.on_config(self.fixer.on_config(pkt.payload))
            return
        if pkt.is_end:
            return
        frame_id = self.id_base + round(pkt.timestamp_ms * self.fps / 1000)
        if frame_id <= self.last_frame_id:  # FLV ms rounding can never legitimately go backwards
            frame_id = self.last_frame_id + 1
        self.last_frame_id = frame_id
        self.frames_out += 1
        self.on_frame(frame_id, pkt.keyframe, self.fixer.fix_sample(pkt.payload), t)

    def _stderr_loop(self) -> None:
        set_thread_name(f"encerr-{self.tag}")
        for line in iter(self.proc.stderr.readline, b""):
            s = line.decode(errors="replace").rstrip()
            if s:
                self._stderr_tail.append(s)
                del self._stderr_tail[:-50]


def _parse_rate(s: str) -> int:
    s = s.strip().upper()
    mult = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(s[-1], 1)
    return int(float(s[:-1] if s[-1] in "KMG" else s) * mult)


class MonotonicEpochClock:
    """Epoch milliseconds derived from a monotonic clock (immune to NTP steps).

    Smooth, but not necessarily the right rate: on this WSL host CLOCK_MONOTONIC runs ~2 % fast (kernel
    tick), so remote clients must fit skew, not just an offset.  The anchor is taken from the wall clock
    at start, so two instances differ by an arbitrary step: `id` (random, per instance) lets clients
    tell that their sync samples belong to another instance."""

    def __init__(self):
        self.id = secrets.token_hex(6)
        self._epoch0 = time.time_ns()
        self._mono0 = time.monotonic_ns()

    def __call__(self) -> float:
        return (self._epoch0 + (time.monotonic_ns() - self._mono0)) / 1e6
