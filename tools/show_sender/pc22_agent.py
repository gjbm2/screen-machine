"""pc22-agent: PC22's own microphone to show_service as VBAN (docs/SHOW_ENGINE.md §3.2, "PC22").

    py -3.12 pc22_agent.py [--port 6984] [--mic-match Insta360] [--stream pc22-mic]

The Windows twin of show_sender.py's room-mic, and the same wire: show_sender's Out (receiver-initiated: it streams
only to the latest SUBSCRIBE's source address, 5 s without one and it goes quiet) and AudioStream (128-frame INT16
packets locked to the ADC, overflows skipped over, a heartbeat every 500 ms). Only the capture differs: PortAudio's
WASAPI through ``sounddevice`` (pip install --user sounddevice), 48 kHz mono.

- The device: the first WASAPI input whose name contains --mic-match, else the Windows default input. Checked every
  2 s: when it changes (a new default, a replug) the stream is reopened as a new sess; with no device, heartbeats say
  no_device and it retries.
- Clock: time.perf_counter_ns() (QueryPerformanceCounter, the clock WASAPI's time_info uses), never monotonic_ns
  (GetTickCount64, 15.6 ms steps) (§3.1, "Sender clock").
- show_service (WSL) reaches it at PC22's LAN address; the reply goes back to WSL's own address, so no portproxy.
  Its udp port takes an inbound Windows firewall rule from the WSL subnet only.

Run by Spectre's audio-agent card (local-automation scripts/windows/audio-agent/audio-agent.ps1): a scheduled task
at Greg's logon (pythonw, so in his session, where Windows lets desktop apps use the mic), --log to agent.log next to
it. Copied with show_sender.py and show_service/vban.py to %LOCALAPPDATA%\\screen-machine\\audio-agent by
deploy-pc22.sh.
"""
from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
from typing import Optional

import numpy as np

import show_sender as ss
from show_sender import FS, HEARTBEAT_S, RETRY_S, AudioStream, Out, vban

log = logging.getLogger("pc22_agent")

PORT = 6984
CHECK_S = 2.0
now_ns = time.perf_counter_ns
ss.now_ns = now_ns                                           # one clock for everything this process stamps


def find_input(sd, match: Optional[str]) -> Optional[tuple[int, str]]:
    """(index, name) of the WASAPI input to use: the first whose name contains ``match``, else the default input
    (found again under WASAPI by name, so the format is 48 kHz shared mode)."""
    devs = sd.query_devices()
    wasapi = next((i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]), None)
    ins = [(i, d["name"]) for i, d in enumerate(devs) if d["max_input_channels"] > 0 and d["hostapi"] == wasapi]
    if match:
        hit = next(((i, n) for i, n in ins if match.lower() in n.lower()), None)
        if hit:
            return hit
    try:
        default = devs[sd.default.device[0]]["name"]
    except Exception:                                       # no default input at all
        return ins[0] if ins else None
    # MME names are cut to 31 characters: match on the start.
    return next(((i, n) for i, n in ins if n.startswith(default) or default.startswith(n)), ins[0] if ins else None)


def run_wasapi(out: Out, stop: threading.Event, match: Optional[str], stream: str) -> None:
    import sounddevice as sd

    au = AudioStream(stream, out.send, 1)
    period = au.frames
    last_hb = 0.0
    while not stop.is_set():
        try:
            sd._terminate()                                  # re-scan devices (a replug, a new default)
            sd._initialize()
            dev = find_input(sd, match)
        except Exception as e:                              # PortAudio itself failing: retry
            log.warning("devices: %s", e)
            dev = None
        if dev is None:
            if time.monotonic() - last_hb >= HEARTBEAT_S:
                au.dev = "none"
                au.heartbeat("no_device", 0.0)
                last_hb = time.monotonic()
            stop.wait(0.5)
            continue
        idx, name = dev
        info = sd.query_devices(idx)
        chans = 1 if info["max_input_channels"] >= 1 else info["max_input_channels"]
        q: queue.Queue = queue.Queue(maxsize=400)            # ~1 s of packets

        def cb(indata, frames, time_info, status):
            # Stamped when Windows hands the block over, less its own length. Not PortAudio's inputBufferAdcTime: on
            # WASAPI it reads ~12.5 ms in the FUTURE of the callback (measured 16 Oct, perf_counter - adc = -12.5 ms
            # p50), while its currentTime is perf_counter to 0.1 ms. WASAPI shared mode delivers 10 ms bursts; the
            # receiver's lower envelope takes the earliest stamps.
            t_cap = now_ns() - int(frames / FS * 1e9)
            try:
                q.put_nowait((indata.copy(), t_cap, bool(status.input_overflow)))
            except queue.Full:
                pass

        au.dev = name
        au.restart_sess()                                    # a device (re)open is a new sess
        try:
            st = sd.InputStream(device=idx, samplerate=FS, channels=chans, dtype="float32", blocksize=period,
                                latency="low", callback=cb)
        except Exception as e:
            log.warning("mic %s: %s", name, e)
            stop.wait(RETRY_S)
            continue
        in_lat_ms = 1000.0 * float(st.latency)
        log.info("mic: %s (WASAPI #%d, %d ch, %.1f ms in)", name, idx, chans, in_lat_ms)
        next_check = time.monotonic() + CHECK_S
        with st:
            while not stop.is_set():
                try:
                    pcm, t_cap, over = q.get(timeout=0.25)
                except queue.Empty:
                    pcm = None
                if pcm is not None:
                    if over:
                        au.overflow(period / FS)
                    if pcm.shape[1] > 1:
                        pcm = pcm.mean(axis=1, keepdims=True)
                    au.packet(np.ascontiguousarray(pcm, dtype=np.float32), t_cap)
                if time.monotonic() - last_hb >= HEARTBEAT_S:
                    au.heartbeat("ok" if pcm is not None else "stalled", in_lat_ms)
                    last_hb = time.monotonic()
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + CHECK_S
                    if _device_changed(sd, match, name):
                        log.info("mic: the input changed; reopening")
                        break
                    if not st.active:
                        log.warning("mic: the stream stopped; reopening")
                        break
        stop.wait(0.2)


def _device_changed(sd, match: Optional[str], name: str) -> bool:
    """Whether the input we'd pick now differs from ``name`` (without re-initialising PortAudio mid-stream, only the
    default's name is compared; a match pin never changes)."""
    if match:
        return False
    try:
        default = sd.query_devices(sd.default.device[0])["name"]
    except Exception:
        return False
    return not (name.startswith(default) or default.startswith(name))


def main(argv: Optional[list] = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--mic-match", default=None, help="WASAPI input name substring (default: the Windows default)")
    ap.add_argument("--stream", default="pc22-mic")
    ap.add_argument("--log", default=None, help="log to this file (rotated at 1 MB) instead of stderr (pythonw)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    handlers = None
    if a.log:
        from logging.handlers import RotatingFileHandler
        handlers = [RotatingFileHandler(a.log, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    out = Out(a.port, a.bind)
    stop = threading.Event()
    threads = [threading.Thread(target=out.serve, args=(stop,), name="net", daemon=True),
               threading.Thread(target=run_wasapi, args=(out, stop, a.mic_match, a.stream), name="mic", daemon=True)]
    for t in threads:
        t.start()
    log.info("pc22-agent on udp %s:%d (%s, vban %s)", a.bind, a.port, a.stream, vban.__name__)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
