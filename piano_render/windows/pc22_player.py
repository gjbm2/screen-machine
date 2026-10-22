"""pc22-player: plays one stream of PCM on PC22's speakers (Windows' default output) and says when it reached the DAC.

    py -3.12 pc22_player.py        (Windows Python 3.12, numpy + sounddevice; started by piano_render/pc22.py through
                                    WSL interop, never by hand)

Output: PortAudio's WASAPI through ``sounddevice`` in shared mode with auto_convert (Windows resamples when the device
runs at another rate), the way tools/show_sender/pc22_agent.py uses it. Clock: time.perf_counter() (QueryPerformanceCounter),
the clock PortAudio's WASAPI time_info uses (currentTime == perf_counter to ~0.1 ms, measured 22 Oct).

stdin
  line 1  JSON header: {"rate": 48000, "channels": 2, "format": "f32le"|"s16le", "frames": N or null,
          "device": null (Windows' default output) or a name substring (WASAPI outputs only), "prebuffer_ms": 300,
          "buffer_ms": 5000 (the bound on what is held here), "latency_ms": 20 (the WASAPI buffer asked for),
          "lead_in_ms": 0 (digital silence played before file sample 0, so the caller learns t_audio0 that much ahead)}
  then    raw interleaved little-endian PCM, whole frames only.
  The end of the input is "frames" frames, or EOF on a frame boundary (the sender died, or sent no "frames"): what has
  arrived is played out to the end. Bytes past "frames" are dropped.
  STOP is one extra byte (any value) and then EOF: an odd tail no whole frame can make, so it can never be mistaken for
  audio. It fades 10 ms, plays the device's buffer out in silence, closes the device and exits 0 (within ~40 ms with
  the default latency). The sender may also just kill the process: py.exe's job object takes python.exe with it and
  Windows closes the device (abrupt, no fade); piano_render/pc22.py does that only when the stop byte doesn't work.

stdout (one line each, nothing else ever; logs go to stderr)
  ready {"device", "index", "rate", "device_rate", "channels", "format", "latency_ms", "pid"}
          the device is open, not started; then it buffers prebuffer_ms (or the whole input if shorter) and starts.
  started {"dac_in_ms", "first_frame", "cb_dac_ms", "since_cb_ms", "perf_minus_cur_ms", "buffered_ms", "latency_ms"}
          printed at once from the first callback (it carries file sample 0, or the lead-in that comes before it).
          dac_in_ms: outputBufferDacTime minus perf_counter() at the moment this line is written (the callback's own
                     outputBufferDacTime - currentTime is cb_dac_ms; since_cb_ms is the gap from the callback to the
                     write). So: the callback's first frame reaches the DAC dac_in_ms after the line is written.
          first_frame: the file frame index at that callback's first frame: 0 (file sample 0 is written at the top of
                     a buffer), or -lead-in frames. File sample 0 reaches the DAC at write + dac_in_ms - first_frame/rate.
          PortAudio's DAC time is currentTime + the frames already queued in the WASAPI buffer; it leaves out the
          Windows audio engine and the device itself (see piano_render/pc22.py: "How accurate").
  done {"frames", "underruns", "underrun_frames", "xruns", "stopped", "short", "cpu_s", "wall_s", "max_buffered_ms"}
          frames: file frames played; underruns: callbacks that found the buffer empty before the end (each one
          shifts the rest of the file later by underrun_frames / rate); xruns: PortAudio's output_underflow flags.
  error <why>   then exit 1.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger("pc22_player")

FORMATS = {"f32le": ("<f4", 4), "s16le": ("<i2", 2)}
FADE_S = 0.010                     # the stop fade
START_TIMEOUT_S = 5.0              # stream started -> its first callback
NO_AUDIO_TIMEOUT_S = 10.0          # header -> enough audio to start
CB_STALL_S = 2.0                   # no callback this long while playing = the device went away
READ_BYTES = 1 << 16
now = time.perf_counter
T_IMPORT = now()                   # for the startup log


class Feed:
    """The PCM between stdin and the audio callback: whole frames as float32 chunks in a deque, bounded by cap_frames
    (the reader waits while it is full). Also decides what the end of stdin means: complete (all "frames", or EOF on a
    frame boundary) or stop (an odd tail before EOF)."""

    def __init__(self, channels: int, fmt: str, frames: Optional[int] = None, cap_frames: int = 240000) -> None:
        if fmt not in FORMATS:
            raise ValueError(f"format {fmt!r} (want one of {', '.join(FORMATS)})")
        if channels < 1:
            raise ValueError(f"channels {channels}")
        self.channels = channels
        self.dtype, width = FORMATS[fmt]
        self.frame_bytes = width * channels
        self.frames = frames
        self.cap = max(1, int(cap_frames))
        self.lock = threading.Lock()
        self.chunks: deque = deque()
        self.offset = 0                 # into chunks[0]
        self.buffered = 0               # frames held
        self.max_buffered = 0
        self.received = 0               # file frames accepted
        self.complete = False           # no more audio will come
        self.short = False              # ended (EOF) before "frames"
        self.stop = False               # the stop byte arrived
        self.t_stop: Optional[float] = None
        self.eof = False

    def _decode(self, data: bytes) -> np.ndarray:
        a = np.frombuffer(data, dtype=self.dtype).reshape(-1, self.channels)
        if self.dtype == "<i2":
            return a.astype(np.float32) * np.float32(1.0 / 32768.0)
        return a.astype(np.float32, copy=False)

    def push(self, data: bytes) -> None:
        """Whole frames only (len(data) % frame_bytes == 0)."""
        if not data:
            return
        a = self._decode(data)
        if self.frames is not None:
            a = a[:max(0, self.frames - self.received)]
        if len(a) == 0:
            return
        with self.lock:
            self.chunks.append(a)
            self.buffered += len(a)
            self.received += len(a)
            self.max_buffered = max(self.max_buffered, self.buffered)
            if self.frames is not None and self.received >= self.frames:
                self.complete = True

    def run(self, f, halt: Optional[threading.Event] = None) -> None:
        """Reads f (a binary stream with read1, or read) to EOF."""
        read = getattr(f, "read1", None) or f.read
        rem = b""
        while True:
            while self.buffered >= self.cap and not self.complete and not (halt is not None and halt.is_set()):
                time.sleep(0.002)                            # full: let the callback drain it
            try:
                b = read(READ_BYTES)
            except (OSError, ValueError) as e:              # stdin gone: the same as EOF
                log.warning("stdin: %s", e)
                b = b""
            if not b:
                break
            data = rem + b if rem else b
            n = len(data) - len(data) % self.frame_bytes
            rem = data[n:]
            if n and not self.complete:
                self.push(data[:n])
        with self.lock:
            self.eof = True
            if rem:                                          # an odd tail: the stop byte
                self.t_stop = now()
                self.stop = True
            else:
                self.short = self.frames is not None and self.received < self.frames
            self.complete = True

    def fill(self, out: np.ndarray, frames: int) -> int:
        """Copies up to frames frames into out[:frames]; returns how many."""
        pos = 0
        with self.lock:
            while pos < frames and self.chunks:
                c = self.chunks[0]
                take = min(len(c) - self.offset, frames - pos)
                out[pos:pos + take] = c[self.offset:self.offset + take]
                pos += take
                self.offset += take
                if self.offset >= len(c):
                    self.chunks.popleft()
                    self.offset = 0
            self.buffered -= pos
        return pos


class Player:
    """The audio callback's state: Feed -> the device, the start stamp, the stop fade, the silent tail, counters."""

    def __init__(self, feed: Feed, rate: int, tail_frames: int = 0, lead_in_frames: int = 0) -> None:
        self.feed = feed
        self.rate = rate
        self.lead_in = max(0, int(lead_in_frames))
        self.lead_left = self.lead_in
        self.fade_frames = max(1, int(round(FADE_S * rate)))
        self.tail_frames = int(tail_frames)       # silence after the end, so the last real frames leave the device
        self.fade_left: Optional[int] = None
        self.fade_dac_end: Optional[float] = None  # DAC time at which the stop fade ends
        self.tail_left: Optional[int] = None
        self.played = 0
        self.underruns = 0
        self.underrun_frames = 0
        self.xruns = 0
        self.first: Optional[dict] = None         # the stamp of the callback that carried file sample 0
        self.started = threading.Event()
        self.last_cb = 0.0
        self.error: Optional[str] = None
        self.stopped = False
        self.stop_exc: type = CallbackStop
        self.abort_exc: type = CallbackAbort

    def callback(self, out, frames, time_info, status):
        try:
            t = now()
            self.last_cb = t
            if status.output_underflow:
                self.xruns += 1
            if self.tail_left is not None:                  # the end was reached: silence, then stop
                out[:] = 0
                self.tail_left -= frames
                if self.tail_left <= 0:
                    raise self.stop_exc()
                return
            complete = self.feed.complete                   # read before fill: by then all audio was pushed
            stop = self.feed.stop
            pre = min(self.lead_left, frames)               # the lead-in's silence comes first
            if pre:
                out[:pre] = 0
            n = self.feed.fill(out[pre:], frames - pre) if pre < frames else 0
            if pre + n < frames:
                out[pre + n:] = 0
            if self.first is None and (n or pre):
                self.first = {"cur": float(time_info.currentTime), "dac": float(time_info.outputBufferDacTime),
                              "perf": t, "first_frame": self.played - self.lead_left}
                self.started.set()
            self.lead_left -= pre
            if self.first is not None and pre + n < frames and not complete and not stop and self.fade_left is None:
                self.underruns += 1
                self.underrun_frames += frames - pre - n
            self.played += n
            if stop and self.fade_left is None:
                self.fade_left = self.fade_frames
                self.fade_dac_end = float(time_info.outputBufferDacTime) + self.fade_frames / self.rate
                self.stopped = True
            if self.fade_left is not None:
                m = min(self.fade_left, frames)
                ramp = (self.fade_left - np.arange(m, dtype=np.float32)) / np.float32(self.fade_frames)
                out[:m] *= ramp[:, None]
                out[m:] = 0
                self.fade_left -= m
                if self.fade_left == 0:
                    self._end(frames - m)
            elif complete and pre + n < frames:
                self._end(frames - pre - n)
        except (self.stop_exc, self.abort_exc):
            raise
        except Exception as e:                                # noqa: BLE001  the stream can't go on
            self.error = f"callback: {type(e).__name__}: {e}"
            out[:] = 0
            raise self.abort_exc()

    def _end(self, silent_frames_here: int) -> None:
        self.tail_left = self.tail_frames - silent_frames_here
        if self.tail_left <= 0:
            raise self.stop_exc()


class CallbackStop(Exception):
    """Stand-ins for sounddevice's (main() swaps them in), so Player runs without it."""


class CallbackAbort(Exception):
    pass


def parse_header(line: bytes) -> dict:
    if not line or not line.endswith(b"\n"):
        raise ValueError("no header line")
    h = json.loads(line.decode("utf-8"))
    if not isinstance(h, dict):
        raise ValueError("the header is not a JSON object")
    out = {
        "rate": int(h.get("rate", 48000)),
        "channels": int(h.get("channels", 2)),
        "format": str(h.get("format", "f32le")),
        "frames": None if h.get("frames") is None else int(h["frames"]),
        "device": h.get("device") or None,
        "prebuffer_ms": float(h.get("prebuffer_ms", 300)),
        "buffer_ms": float(h.get("buffer_ms", 5000)),
        "latency_ms": float(h.get("latency_ms", 20)),
        "lead_in_ms": float(h.get("lead_in_ms", 0)),
    }
    if not 8000 <= out["rate"] <= 384000:
        raise ValueError(f"rate {out['rate']}")
    if not 1 <= out["channels"] <= 8:
        raise ValueError(f"channels {out['channels']}")
    if out["format"] not in FORMATS:
        raise ValueError(f"format {out['format']!r}")
    if not 0 <= out["lead_in_ms"] <= 60000:
        raise ValueError(f"lead_in_ms {out['lead_in_ms']}")
    if out["frames"] is not None and out["frames"] < 0:
        raise ValueError(f"frames {out['frames']}")
    return out


def pick_output(sd, want: Optional[str]) -> int:
    """The WASAPI output: Windows' default, or the first whose name contains want (any case)."""
    apis = sd.query_hostapis()
    ha = next((i for i, h in enumerate(apis) if "WASAPI" in h["name"]), None)
    if ha is None:
        raise RuntimeError("no WASAPI host API")
    if not want:
        idx = apis[ha]["default_output_device"]
        if idx is None or idx < 0:
            raise RuntimeError("no default WASAPI output")
        return int(idx)
    for i, d in enumerate(sd.query_devices()):
        if d["hostapi"] == ha and d["max_output_channels"] > 0 and want.lower() in d["name"].lower():
            return i
    raise RuntimeError(f"no WASAPI output named like {want!r}")


class Proto:
    """stdout, kept for the protocol only: everything else that writes to fd 1 goes to stderr."""

    def __init__(self) -> None:
        self.f = os.fdopen(os.dup(1), "wb", buffering=0)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        self.ok = True

    def emit(self, kind: str, obj=None) -> bool:
        text = kind if obj is None else f"{kind} {obj if isinstance(obj, str) else json.dumps(obj)}"
        try:
            self.f.write(text.replace("\n", " ").encode("utf-8") + b"\n")
            return True
        except OSError:                                       # WSL is gone
            self.ok = False
            return False


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="pc22_player %(levelname)s %(message)s")
    proto = Proto()
    stream = None
    try:
        inp = sys.stdin.buffer
        h = parse_header(inp.readline(65536))
        rate, ch = h["rate"], h["channels"]
        feed = Feed(ch, h["format"], h["frames"], cap_frames=int(h["buffer_ms"] / 1000 * rate))
        halt = threading.Event()
        threading.Thread(target=feed.run, args=(inp, halt), name="stdin", daemon=True).start()

        t_hdr = now()
        import sounddevice as sd
        t_sd = now()
        idx = pick_output(sd, h["device"])
        info = sd.query_devices(idx)
        latency_s = max(0.0, h["latency_ms"] / 1000.0)
        player = Player(feed, rate, lead_in_frames=int(round(h["lead_in_ms"] / 1000 * rate)))
        player.stop_exc, player.abort_exc = sd.CallbackStop, sd.CallbackAbort
        finished = threading.Event()
        stream = sd.OutputStream(device=idx, samplerate=rate, channels=ch, dtype="float32", latency=latency_s,
                                 extra_settings=sd.WasapiSettings(auto_convert=True), callback=player.callback,
                                 finished_callback=finished.set)
        out_latency = float(stream.latency)
        player.tail_frames = int((out_latency + 0.02) * rate)
        proto.emit("ready", {"device": info["name"], "index": idx, "rate": rate,
                             "device_rate": info["default_samplerate"], "channels": ch, "format": h["format"],
                             "latency_ms": round(out_latency * 1000, 3), "pid": os.getpid()})
        log.info("open: %s (#%d), %d Hz x %d, %.1f ms; numpy+header %.0f ms, sounddevice %.0f ms, open %.0f ms",
                 info["name"], idx, rate, ch, out_latency * 1000, (t_hdr - T_IMPORT) * 1000, (t_sd - t_hdr) * 1000,
                 (now() - t_sd) * 1000)

        need = int(h["prebuffer_ms"] / 1000 * rate)
        if h["frames"] is not None:
            need = min(need, h["frames"])
        t_wait = now()
        while feed.buffered < need and not feed.complete:
            if now() - t_wait > NO_AUDIO_TIMEOUT_S:
                raise RuntimeError(f"no audio in {NO_AUDIO_TIMEOUT_S:.0f} s ({feed.buffered} of {need} frames)")
            time.sleep(0.002)
        if feed.stop or (feed.complete and feed.received == 0):
            stream.close()
            stream = None
            proto.emit("done", {"frames": 0, "underruns": 0, "underrun_frames": 0, "xruns": 0, "stopped": feed.stop,
                                "short": feed.short, "cpu_s": round(time.process_time(), 3), "wall_s": 0.0,
                                "max_buffered_ms": round(feed.max_buffered / rate * 1000, 1)})
            return 0
        buffered_ms = feed.buffered / rate * 1000
        t_start = now()
        cpu0 = time.process_time()
        stream.start()
        if not player.started.wait(START_TIMEOUT_S):
            raise RuntimeError(player.error or f"the device asked for no audio in {START_TIMEOUT_S:.0f} s")
        f = player.first
        t_w = now()
        proto.emit("started", {"dac_in_ms": round((f["dac"] - t_w) * 1000, 3),
                               "first_frame": f["first_frame"],
                               "cb_dac_ms": round((f["dac"] - f["cur"]) * 1000, 3),
                               "since_cb_ms": round((t_w - f["perf"]) * 1000, 3),
                               "perf_minus_cur_ms": round((f["perf"] - f["cur"]) * 1000, 3),
                               "buffered_ms": round(buffered_ms, 1), "latency_ms": round(out_latency * 1000, 3)})
        while not finished.wait(0.1):
            if not proto.ok and not player.stopped:
                feed.stop = True                             # nobody to tell: stop
            if player.error:
                break
            if now() - player.last_cb > CB_STALL_S:
                raise RuntimeError(f"the device stopped asking for audio ({now() - player.last_cb:.1f} s)")
        if player.error:
            raise RuntimeError(player.error)
        wall = now() - t_start
        stream.close()
        stream = None
        halt.set()
        proto.emit("done", {"frames": player.played, "underruns": player.underruns,
                            "underrun_frames": player.underrun_frames, "xruns": player.xruns,
                            "stopped": player.stopped, "short": feed.short,
                            "cpu_s": round(time.process_time() - cpu0, 3), "wall_s": round(wall, 3),
                            "max_buffered_ms": round(feed.max_buffered / rate * 1000, 1),
                            "stop_to_silence_ms": None if not (player.stopped and feed.t_stop and player.fade_dac_end)
                            else round((player.fade_dac_end - feed.t_stop) * 1000, 1)})
        return 0
    except Exception as e:                                    # noqa: BLE001  one line for WSL, then exit 1
        log.exception("failed")
        proto.emit("error", f"{type(e).__name__}: {e}")
        return 1
    finally:
        if stream is not None:
            try:
                stream.abort()
                stream.close()
            except Exception:                                 # noqa: BLE001
                pass


if __name__ == "__main__":
    code = main()
    sys.stderr.flush()
    os._exit(code)                                            # the stdin thread may still be blocked in a read
