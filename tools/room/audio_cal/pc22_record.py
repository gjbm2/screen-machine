"""pc22_record: PC22's microphone to a WAV file, for the room audio calibration (tools/room/audio_cal/README.md).

    py -3.12 pc22_record.py --out C:\\...\\rec.wav --device "Echo Cancelling Speakerphone (U3224KB/A)" [--max-s 3600]

It opens the mic the way tools/show_sender/pc22_agent.py does: PortAudio's WASAPI through ``sounddevice``, shared
mode, 48 kHz, mono, float32, latency "low". So it records the same samples the agent sends to show_service (before
the agent's INT16 step), and the agent's own capture carries on alongside it: shared mode lets both have the device.

- The device: the WASAPI input named --device exactly, else the first whose name contains it. No match is an error
  (never a quiet fallback to another mic: the calibration is of one mic).
- It stops when ``<out>.stop`` exists, at --max-s, or on Ctrl-C, and always finishes the file.
- ``<out>``: a 32-bit float WAV. The header's sizes are patched every second, so a killed recorder still leaves a
  readable file.
- ``<out>.json``: the status, rewritten every second while it runs (``state: recording``) and once at the end
  (``state: stopped``): device, format, frames, overflows, the last second's peak and RMS, times.

Run from WSL by calibrate.py ``record start`` (through powershell.exe, in Greg's session, where Windows lets desktop
apps use the mic). Windows mic privacy must allow desktop apps, as for the agent.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import queue
import struct
import sys
import time

import numpy as np

FS = 48000
STATUS_S = 1.0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def find_input(sd, want: str) -> tuple[int, str]:
    devs = sd.query_devices()
    wasapi = next((i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]), None)
    ins = [(i, d["name"]) for i, d in enumerate(devs) if d["max_input_channels"] > 0 and d["hostapi"] == wasapi]
    hit = next(((i, n) for i, n in ins if n == want), None) or \
        next(((i, n) for i, n in ins if want.lower() in n.lower()), None)
    if hit is None:
        raise SystemExit(f"no WASAPI input matches {want!r}; inputs: {[n for _, n in ins]}")
    return hit


class FloatWav:
    """A mono 32-bit IEEE float WAV written as it goes; ``patch()`` makes the header match what is on disk."""

    def __init__(self, path: str, fs: int) -> None:
        self.f = open(path, "wb")
        self.fs = fs
        self.frames = 0
        self.f.write(self._header())

    def _header(self) -> bytes:
        data = self.frames * 4
        fmt = struct.pack("<HHIIHH", 3, 1, self.fs, self.fs * 4, 4, 32)        # WAVE_FORMAT_IEEE_FLOAT, mono
        return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + data) + b"WAVE"
                + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", data))

    def write(self, x: np.ndarray) -> None:
        self.f.write(np.ascontiguousarray(x, dtype="<f4").tobytes())
        self.frames += len(x)

    def patch(self) -> None:
        pos = self.f.tell()
        self.f.seek(0)
        self.f.write(self._header())
        self.f.seek(pos)
        self.f.flush()

    def close(self) -> None:
        self.patch()
        self.f.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", required=True, help="the WASAPI input's name (exact, else a substring)")
    ap.add_argument("--max-s", type=float, default=3600.0)
    a = ap.parse_args()

    import sounddevice as sd

    idx, name = find_input(sd, a.device)
    stop_file = a.out + ".stop"
    status_file = a.out + ".json"
    if os.path.exists(stop_file):
        os.remove(stop_file)
    q: queue.Queue = queue.Queue()
    overflows = 0

    def cb(indata, frames, time_info, status):
        nonlocal overflows
        if status.input_overflow:
            overflows += 1
        q.put(indata[:, 0].copy())

    wav = FloatWav(a.out, FS)
    st = sd.InputStream(device=idx, samplerate=FS, channels=1, dtype="float32", latency="low", callback=cb)
    info = {"device": name, "wasapi_index": idx, "fs": FS, "channels": 1, "dtype": "float32",
            "latency_s": float(st.latency), "started_utc": utc_now(), "out": a.out, "pid": os.getpid()}
    t0 = time.perf_counter()
    reason = "max-s"
    last_status = 0.0
    peak = rms = None
    sec: list[np.ndarray] = []

    def status(state: str) -> None:
        s = dict(info, state=state, frames=wav.frames, seconds=round(wav.frames / FS, 3), overflows=overflows,
                 last_peak_dbfs=peak, last_rms_dbfs=rms, t_utc=utc_now())
        if state == "stopped":
            s.update(stopped_utc=utc_now(), stop_reason=reason)
        tmp = status_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, status_file)

    try:
        with st:
            while time.perf_counter() - t0 < a.max_s:
                try:
                    x = q.get(timeout=0.2)
                    wav.write(x)
                    sec.append(x)
                except queue.Empty:
                    pass
                now = time.perf_counter()
                if now - last_status >= STATUS_S:
                    last_status = now
                    if sec:
                        y = np.concatenate(sec)
                        sec = []
                        peak = round(20 * np.log10(max(float(np.max(np.abs(y))), 1e-9)), 1)
                        rms = round(10 * np.log10(max(float(np.mean(y.astype(np.float64) ** 2)), 1e-18)), 1)
                    wav.patch()
                    status("recording")
                    if os.path.exists(stop_file):
                        reason = "stop-file"
                        break
    except KeyboardInterrupt:
        reason = "interrupt"
    while not q.empty():                                    # what PortAudio handed over before the stream closed
        wav.write(q.get_nowait())
    wav.close()
    status("stopped")
    print(json.dumps({"frames": wav.frames, "overflows": overflows, "stop_reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
