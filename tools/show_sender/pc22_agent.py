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
SCAN_S = 5.0                                                 # device list refresh while the mic is off
MISSING_RESCAN_S = 10.0                                      # the chosen mic isn't listed yet: rescan this often
now_ns = time.perf_counter_ns
ss.now_ns = now_ns                                           # one clock for everything this process stamps


def capture_endpoints() -> Optional[frozenset]:
    """The ids of Windows' active capture endpoints, read from the registry (microseconds; no PortAudio, so it can run
    while a stream is open). A change means a mic was plugged in or out: the agent then reopens with a fresh scan,
    because PortAudio only sees new devices after a re-initialise. None off Windows or if unreadable."""
    try:
        import winreg
    except ImportError:
        return None
    path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
    out = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        if winreg.QueryValueEx(s, "DeviceState")[0] == 1:      # DEVICE_STATE_ACTIVE
                            out.add(sub)
                except OSError:
                    continue
    except OSError:
        return None
    return frozenset(out)


def wasapi_inputs(sd) -> list[tuple[int, str]]:
    """Every WASAPI capture device: (index, name). The names are what the Room page offers (heartbeat ``devices``)."""
    devs = sd.query_devices()
    wasapi = next((i for i, h in enumerate(sd.query_hostapis()) if "WASAPI" in h["name"]), None)
    return [(i, d["name"]) for i, d in enumerate(devs) if d["max_input_channels"] > 0 and d["hostapi"] == wasapi]


def find_input(sd, want: Optional[str], match: Optional[str]) -> Optional[tuple[int, str]]:
    """(index, name) of the WASAPI input to use: the one named ``want`` exactly (the Room page's choice, from the
    SUBSCRIBE), else the first whose name contains ``match`` (--mic-match), else the Windows default input (found
    again under WASAPI by name, so the format is 48 kHz shared mode)."""
    ins = wasapi_inputs(sd)
    if want:
        hit = next(((i, n) for i, n in ins if n == want), None)
        if hit:
            return hit
    if match:
        hit = next(((i, n) for i, n in ins if match.lower() in n.lower()), None)
        if hit:
            return hit
    try:
        default = sd.query_devices()[sd.default.device[0]]["name"]
    except Exception:                                       # no default input at all
        return ins[0] if ins else None
    # MME names are cut to 31 characters: match on the start.
    return next(((i, n) for i, n in ins if n.startswith(default) or default.startswith(n)), ins[0] if ins else None)


class Choice:
    """What the latest SUBSCRIBE asks of this sender, read off the same socket as the subscription itself: the device
    per stream (``devices: {stream: name}``, the Room page's Mic choice) and the streams it wants OFF (``off``: owner,
    16 Oct, the mic runs only while something needs it; their heartbeats go on, saying "off", so the Mic list
    stays, while the device is closed)."""

    def __init__(self, out: Out) -> None:
        self.out = out
        self.lock = threading.Lock()
        self.devices: dict[str, str] = {}
        self.off: set[str] = set()
        self.addr: Optional[tuple] = None
        self.until = 0.0
        orig = out.subs.on_packet

        def on_packet(buf: bytes, addr: tuple, now: float) -> bool:
            ok = orig(buf, addr, now)
            if ok:
                pkt = vban.parse(buf)
                d = vban.subscribed_devices(pkt) if pkt is not None else None
                if hasattr(vban, "subscribed_off"):
                    off = vban.subscribed_off(pkt) if pkt is not None else []
                else:                                       # an older vban.py: read the key itself
                    o = getattr(pkt, "obj", None)
                    off = o.get("off") if isinstance(o, dict) and isinstance(o.get("off"), list) else []
                with self.lock:
                    self.devices = dict(d) if isinstance(d, dict) else {}
                    self.off = {x for x in off if isinstance(x, str)}
                    self.addr, self.until = addr, now + ss.SUB_TTL_S
            return ok
        out.subs.on_packet = on_packet                      # type: ignore[method-assign]

    def want(self, stream: str) -> Optional[str]:
        with self.lock:
            return self.devices.get(stream)

    def mode(self, stream: str) -> str:
        """"on" (subscribed: capture and send), "off" (listed off: device closed, "off" heartbeats) or "none" (not
        asked, or no SUBSCRIBE for 5 s: device closed, silent)."""
        now = time.monotonic()
        sub_mode = getattr(self.out.subs, "mode", None)
        if sub_mode is not None:                            # show_sender's own answer: target() is also set for
            return sub_mode(stream, now) or "none"          # "off" streams (their heartbeats), so it can't mean "on"
        if self.out.subs.target(stream, now) is not None:
            return "on"
        with self.lock:
            return "off" if stream in self.off and now <= self.until else "none"

    def send_off(self, au: "ListedStream") -> None:
        """An "off" heartbeat for ``au``'s stream to the subscriber (Out.send only sends subscribed streams)."""
        if "off" not in vban.HEARTBEAT_STATES:              # an older vban.py can't say it: stay quiet
            return
        with self.lock:
            addr = self.addr
        if addr is None:
            return
        obj = vban.make_heartbeat(sess=au.sess, frame=None, n=None, t_cap_ns=None, dev=au.dev or "none", state="off",
                                  devices=au.devices)
        try:
            self.out.sock.sendto(vban.pack_text(au.stream, au.text_counter, obj), addr)
        except OSError:
            return
        au.text_counter = (au.text_counter + 1) & 0xFFFFFFFF


class ListedStream(AudioStream):
    """AudioStream whose heartbeats also list the devices this sender can capture (``devices``)."""

    devices: list[str] = []

    def heartbeat(self, state: str, in_lat_ms: float) -> None:
        def db(x):
            return [round(max(-120.0, 20.0 * np.log10(v)) if v > 0 else -120.0, 1) for v in x]
        rms = np.sqrt(self._sq / self._cnt) if self._cnt else np.zeros(self.channels)
        frame, n, t_cap = self.last if self.last else (None, None, None)
        obj = vban.make_heartbeat(sess=self.sess, frame=frame, n=n, t_cap_ns=t_cap, in_lat_ms=round(in_lat_ms, 2),
                                  dev=self.dev or "none", state=state, xruns=self.xruns,
                                  peak_dbfs=db(self._peak), rms_dbfs=db(rms), clips=self.clips,
                                  devices=self.devices)              # vban trims the list to fit one packet
        self.send(self.stream, vban.pack_text(self.stream, self.text_counter, obj))
        self.text_counter = (self.text_counter + 1) & 0xFFFFFFFF
        self._peak[:] = 0.0
        self._sq[:] = 0.0
        self._cnt = 0


def run_wasapi(out: Out, stop: threading.Event, match: Optional[str], stream: str) -> None:
    """The capture thread, restarted on any error (logged): it must never die and leave the agent silent."""
    while not stop.is_set():
        try:
            _run_wasapi(out, stop, match, stream)
        except Exception:                                   # noqa: BLE001
            log.exception("mic: capture failed; restarting in %.0f s", RETRY_S)
            stop.wait(RETRY_S)


def _run_wasapi(out: Out, stop: threading.Event, match: Optional[str], stream: str) -> None:
    import sounddevice as sd

    choice = Choice(out)
    au = ListedStream(stream, out.send, 1)
    period = au.frames
    last_hb = 0.0
    last_scan = 0.0
    last_eps: Optional[frozenset] = None
    was = None
    while not stop.is_set():
        mode = choice.mode(stream)
        if mode != was:
            log.info("mic: %s", {"on": "on (subscribed)", "off": "off (show_service: nothing needs it)",
                                 "none": "off (not subscribed)"}[mode])
            was = mode
        if mode != "on":
            # Nothing captures unless asked: the device stays closed (Windows' mic-in-use light goes out). The device
            # list is kept fresh for the Room page's Mic choice, and "off" heartbeats carry it while listed off.
            eps = capture_endpoints()
            if time.monotonic() - last_scan >= SCAN_S or (eps is not None and eps != last_eps):
                last_eps = eps
                try:
                    sd._terminate()
                    sd._initialize()
                    au.devices = [n for _, n in wasapi_inputs(sd)]
                except Exception as e:                      # noqa: BLE001
                    log.warning("devices: %s", e)
                last_scan = time.monotonic()
            if mode == "off" and time.monotonic() - last_hb >= HEARTBEAT_S:
                choice.send_off(au)
                last_hb = time.monotonic()
            stop.wait(0.1)
            continue
        try:
            sd._terminate()                                  # re-scan devices (a replug, a new default)
            sd._initialize()
            au.devices = [n for _, n in wasapi_inputs(sd)]
            dev = find_input(sd, choice.want(stream), match)
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
        endpoints = capture_endpoints()
        opened_at = time.monotonic()
        log.info("mic: %s (WASAPI #%d, %d ch, %.1f ms in; %d inputs)", name, idx, chans, in_lat_ms, len(au.devices))
        next_check = time.monotonic() + CHECK_S
        with st:
            while not stop.is_set():
                if choice.mode(stream) != "on":                 # not wanted any more: close the device now
                    log.info("mic: closing %s", name)
                    break
                try:
                    pcm, t_cap, over = q.get(timeout=0.1)
                except queue.Empty:
                    pcm = None
                if pcm is not None:
                    if over:
                        au.overflow(period / FS)
                    if pcm.shape[1] > 1:
                        pcm = pcm.mean(axis=1, keepdims=True)
                    au.packet(np.ascontiguousarray(pcm, dtype=np.float32), t_cap)
                if time.monotonic() - last_hb >= HEARTBEAT_S:
                    au.heartbeat("ok", in_lat_ms)                 # (a state vban knows: an unknown one raises)
                    last_hb = time.monotonic()
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + CHECK_S
                    # The device it should be on: the SUBSCRIBE's choice, else the --mic-match one, else the default.
                    want = choice.want(stream)
                    target = want if want in au.devices else next(
                        (d for d in au.devices if match and match.lower() in d.lower()), None)
                    if want and want not in au.devices and time.monotonic() - opened_at >= MISSING_RESCAN_S:
                        log.info("mic: %s chosen but not in Windows' list yet; reopening to rescan", want)
                        break
                    if target and target != name:
                        log.info("mic: %s %s; reopening", target, "chosen" if target == want else "(the default)")
                        break
                    if not target and _device_changed(sd, match, name):
                        log.info("mic: the input changed; reopening")
                        break
                    if not st.active:
                        log.warning("mic: the stream stopped; reopening")
                        break
                    now_eps = capture_endpoints()
                    if now_eps is not None and endpoints is not None and now_eps != endpoints:
                        log.info("mic: Windows' inputs changed (%d -> %d); reopening to rescan", len(endpoints), len(now_eps))
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
