#!/usr/bin/env python3
"""Hue lamps → light-sensor latency calibration through the production path (run from WSL with the repo's .venv).

Measures how long a level change in the Art-Net that room_service receives takes to reach the room's light sensor as
light from the Hue lamps, the same way calibrate.py measures the screens (README.md, "Hue lamps"). It reuses
calibrate.py: Box, RelayCapture, parse_samples, analyse, quantiles and the latency.json writer pattern.

The path, unchanged from production:
  this script ──ArtDmx, universe 0──▶ room_service's receiver (127.0.0.1:6454, inside Flask)
     ──HueOutput: 25 Hz latest value, xy+brightness──▶ Hue Bridge Pro (Entertainment area) ──Zigbee──▶ lamp
     ──light──▶ Yocto-Light-V5 on the kiosk box (timed on the box's clock)

What one run does:
  1. Preflight, reads only: no screen is live (our frames zero the screens' channels); the Hue output is room's
     (hue.streamer "room"), not taken over; each lamp's room target (its believed value), so it can be put back.
  2. Clock: the WSL↔box wall-clock offset from ~200 UDP echoes to a throwaway responder started on the box over SSH
     (the median of the minimum-RTT probes' midpoints; ± half the minimum RTT), before and after the flips.
  3. Sensor: the light relay's capture (calibrate.RelayCapture: no sudo; the relay holds its output meanwhile).
  4. Art-Net: full 512-channel ArtDmx frames at 40 Hz from a second loopback address (127.0.0.2), the lamps dark
     (MASTER DIMMER 255, RGB 0) and every other channel 0. show_service's virtual Maestro sees a foreign sender and
     yields (artnet_out.py "Yield"); the run waits until room's input shows only our frames.
  5. Targets: each lamp that isn't "dmx" already is set to "dmx" by hand (PUT /api/v1/room/targets, one lamp at a
     time: a hand set cancels the previous one's apply), then the run waits for HueOutput to stream them.
  6. Flips: after >= --preroll s of dark Art-Net, white (255,255,255,255) and dark alternate at a photosafe pace
     (>= 1 s apart). Time zero of a flip is time.time_ns() when the first frame carrying the new level is sent.
  7. Cleanup on every exit path (exception, Ctrl-C, SIGTERM): back to dark, each lamp's target put back to exactly
     what it was, Art-Net stopped (show_service resumes by itself once room's input goes stale), the capture ended.
  8. Analysis: calibrate.analyse() with each flip moved onto the box clock; a run directory, and
     config/room/latency.json outputs.<key>.intensity (all four lamps: the hue output's key, "hue"; fewer: each
     lamp's own id). --each only reports which lamps the sensor sees, with their SNR.

Usage (see README.md):
  .venv/bin/python tools/room/latency_cal/hue_cal.py --dry-run           # daylight check: no lamp is touched
  .venv/bin/python tools/room/latency_cal/hue_cal.py --yes --each        # which lamps the sensor sees (dark room)
  .venv/bin/python tools/room/latency_cal/hue_cal.py --yes               # all four lamps together, 20 flips
  .venv/bin/python tools/room/latency_cal/hue_cal.py --yes --lamps hue.se,hue.sw
  .venv/bin/python tools/room/latency_cal/hue_cal.py --reanalyse tools/room/latency_cal/runs/<run>
"""
import argparse
import collections
import datetime as dt
import json
import os
import random
import select
import shlex
import signal
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import urllib.error

import calibrate as cal

if cal.REPO not in sys.path:
    sys.path.insert(0, cal.REPO)
from show_service.artnet_out import (PATCH_FILE, UNIVERSE_SIZE, ArtDmxFrame,    # noqa: E402  (needs REPO on the path)
                                     encode_into, load_layout)

log = cal.log
ROOM_API = cal.ROOM_API
ARTNET_TARGET = ("127.0.0.1", 6454)          # room_service's receiver (patch.yaml artnet.bind), inside Flask
SOURCE_IP = "127.0.0.2"                      # not show_service's 127.0.0.1, so its yield check sees us by address too
SEND_HZ = 40.0                               # under Art-Net's 44 Hz; room's HueOutput samples the universe at 25 Hz
FOREIGN_PPS_MARGIN = 8.0                     # room's input pps over our rate by this: someone else is sending too
EACH_GAP_S = 2.5                             # --each: dark between lamps (analyse's last-pulse window is 2 s)
TAIL_S = 2.2                                 # after the last flip: its post window must stay clean
SOURCE = "latency-cal"                       # the hand sets' source in room's log
ZERO = "Art-Net frame sent (WSL)"
FIXTURES_FILE = os.path.join(cal.REPO, "config", "room", "fixtures.yaml")
WHITE, BLACK = "#ffffff", "#000000"          # analyse() reads a pulse's direction from its colour

# The box's clock responder: one UDP socket on an ephemeral port (printed first), replying to each probe with the
# box's time.time_ns() taken on receipt. It exits on "quit", after 5 s without a probe, or after 60 s in all.
ECHO_PY = r'''
import socket, struct, time
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("0.0.0.0", 0))
s.settimeout(0.5)
print(s.getsockname()[1], flush=True)
end = time.monotonic() + 60
idle = time.monotonic() + 5
while time.monotonic() < min(end, idle):
    try:
        d, a = s.recvfrom(64)
    except socket.timeout:
        continue
    t = time.time_ns()
    if d == b"quit":
        break
    s.sendto(d[:8] + struct.pack("<q", t), a)
    idle = time.monotonic() + 5
'''


# ---------------------------------------------------------------- frames

def hue_devices(layout):
    return {d.id: d for d in layout.devices if d.kind == "hue"}


def universe(layout, levels):
    """The 512 channels of one frame: each lamp in `levels` at MASTER DIMMER 255 and R = G = B = its level (0..1);
    every other channel 0, the screens' included."""
    data = bytearray(UNIVERSE_SIZE)
    devs = hue_devices(layout)
    for lamp, level in levels.items():
        encode_into(data, devs[lamp], {"dimmer": 1.0, "rgb": (level, level, level)})
    return bytes(data)


class ArtNetSender:
    """Full-universe ArtDmx frames to room_service's receiver from its own thread: SEND_HZ steadily, and a level
    change at once rather than on the next tick. set_levels() returns that first frame's send time (time.time_ns on
    WSL), which is the flip's time zero. The thread is a daemon: if this script dies, the frames stop with it."""

    def __init__(self, layout, levels, target=ARTNET_TARGET, hz=SEND_HZ, source_ip=SOURCE_IP):
        self.layout, self.target, self.hz, self.period = layout, target, hz, 1.0 / hz
        self.frame = ArtDmxFrame(layout.universe)
        self.levels = dict(levels)
        data = universe(layout, self.levels)
        self.frame.data[:] = data
        self.recent = collections.deque([data], maxlen=3)     # payloads sent lately: the monitor's "ours"
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((source_ip, 0))
            self.source = source_ip
        except OSError:                                         # show_service then yields on our rate alone
            self.source = "127.0.0.1"
        self.frames = self.errors = 0
        self.last_error = None
        self._req = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hue-cal-artnet", daemon=True)

    def start(self):
        self._thread.start()

    def set_levels(self, levels, timeout=1.0):
        req = {"levels": dict(levels), "done": threading.Event(), "t_ns": None}
        with self._lock:
            self._req = req
        self._wake.set()
        if not req["done"].wait(timeout) or req["t_ns"] is None:
            raise RuntimeError(f"the Art-Net sender did not send the new level ({self.last_error or 'timeout'})")
        return req["t_ns"]

    def _run(self):
        next_t = time.monotonic()
        while not self._stop.is_set():
            delay = next_t - time.monotonic()
            if delay > 0:
                self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                req, self._req = self._req, None
            if req:
                self.levels = req["levels"]
                data = universe(self.layout, self.levels)
                self.frame.data[:] = data
                self.recent.append(data)
            buf = self.frame.advance()
            t_ns = time.time_ns()
            try:
                self.sock.sendto(buf, self.target)
                self.frames += 1
                sent = True
            except OSError as exc:
                self.errors += 1
                self.last_error = str(exc)
                sent = False
            if req:
                req["t_ns"] = t_ns if sent else None
                req["done"].set()
            now = time.monotonic()
            next_t = now + self.period if req or next_t + self.period < now else next_t + self.period

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(2)
        self.sock.close()


# ---------------------------------------------------------------- clock

def offset_from_probes(probes, best=8):
    """(t0 WSL send, t box receipt, t1 WSL reply) ns triples -> the box − WSL offset: the median of the `best`
    minimum-RTT probes' midpoint offsets, ± half the minimum RTT."""
    if len(probes) < best:
        raise RuntimeError(f"only {len(probes)} clock probes were answered (need {best})")
    rows = sorted(((t1 - t0) / 1e6, (tb - (t0 + t1) // 2) / 1e6) for t0, tb, t1 in probes)
    top = rows[:best]
    return {"offset_box_minus_wsl_ms": round(statistics.median(o for _, o in top), 3),
            "offset_uncertainty_ms": round(top[0][0] / 2, 3),
            "perf_minus_date_ms": 0.0,                      # analyse(): the flips carry at_ns, no page clock
            "rtt_ms": cal.quantiles([r for r, _ in rows]),
            "method": f"UDP echo (box time.time_ns() on receipt); median of the {best} minimum-RTT midpoints"}


def udp_clock_sync(box, probes=200):
    """Start the echo responder on the box over SSH and probe it from WSL."""
    proc = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", box.host,
                             "python3 -u -c " + shlex.quote(ECHO_PY)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    out, sent = [], 0
    try:
        ready, _, _ = select.select([proc.stdout], [], [], 15)
        line = proc.stdout.readline().strip() if ready else ""
        if not line.isdigit():
            proc.kill()
            raise RuntimeError(f"the box's clock responder did not start: {line!r} {proc.stderr.read()[-300:]!r}")
        addr = (box.ip, int(line))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.2)
            for i in range(probes):
                msg = struct.pack("<q", i)
                sent += 1
                t0 = time.time_ns()
                sock.sendto(msg, addr)
                data = None
                while True:
                    try:
                        data, _ = sock.recvfrom(64)
                    except socket.timeout:
                        data = None
                        break
                    if data[:8] == msg:                     # a late reply to an earlier probe is dropped
                        break
                t1 = time.time_ns()
                if data is not None and len(data) >= 16:
                    out.append((t0, struct.unpack("<q", data[8:16])[0], t1))
                elif i >= 19 and not out:
                    raise RuntimeError(f"no UDP reply from {addr[0]}:{addr[1]} (a firewall on the box?)")
                time.sleep(0.003)
            sock.sendto(b"quit", addr)
    finally:
        try:
            proc.wait(3)
        except subprocess.TimeoutExpired:
            proc.kill()
    res = offset_from_probes(out)
    res["probes"] = {"sent": sent, "answered": len(out)}
    return res


def combine_clock(before, after):
    """calibrate.combine_clock, judged on the offset itself (there is no page clock here)."""
    c = cal.combine_clock(before, after)
    c.pop("drift_change_ms", None)
    if after:
        c["offset_uncertainty_ms"] = round(max(before["offset_uncertainty_ms"], after["offset_uncertainty_ms"]), 3)
        c["stable"] = abs(c["offset_change_ms"]) <= max(1.0, before["offset_uncertainty_ms"]
                                                        + after["offset_uncertainty_ms"])
    return c


# ---------------------------------------------------------------- room

def get_status():
    return cal.http_json("GET", f"{ROOM_API}/status")


def get_dmx():
    return cal.http_json("GET", f"{ROOM_API}/dmx")


def live_screens(status):
    out = []
    for sid, s in (status.get("screens") or {}).items():
        obs, des = s.get("observed") or {}, s.get("desired")
        if obs.get("layer") == "live" or obs.get("session") or des == "live" or (isinstance(des, dict) and "live" in des):
            out.append(sid)
    return out


def lamp_targets(status, lamps):
    """Each lamp's room-state member (believed, desired, policy, inflight ...), or None if room doesn't know it."""
    members = (((status.get("subsystems") or {}).get("hue") or {}).get("members")) or {}
    return {lamp: members.get(lamp.split(".", 1)[1]) for lamp in lamps}


class RoomRefused(RuntimeError):
    """room answered the hand set with an error: nothing was changed."""


def put_target(lamp, value):
    try:
        cal.http_json("PUT", f"{ROOM_API}/room/targets", {"values": {lamp: value}, "source": SOURCE})
    except urllib.error.HTTPError as exc:
        raise RoomRefused(f"room refused {lamp} = {value!r} ({exc.code}: "
                          f"{exc.read().decode(errors='replace')[:300]})") from None


def member(lamp):
    return lamp_targets(get_status(), [lamp])[lamp] or {}


def wanted():
    return set((get_dmx().get("hue") or {}).get("wanted") or [])


def wait_for(cond, timeout_s, step=0.25):
    end = time.monotonic() + timeout_s
    while True:
        got = cond()
        if got or time.monotonic() >= end:
            return got
        time.sleep(step)


def foreign_reason(dmx, sender, hz=SEND_HZ):
    """Why room's Art-Net input isn't only our frames, or None."""
    inp = dmx.get("input") or {}
    frm = inp.get("last_from")
    if frm and frm != sender.source:
        return f"frames from {frm}"
    pps = inp.get("pps")
    if isinstance(pps, (int, float)) and pps > hz + FOREIGN_PPS_MARGIN:
        return f"{pps:.0f} packets/s on the universe (we send {hz:.0f})"
    ch = bytes(dmx.get("channels") or [])
    if ch and not any(r[:len(ch)] == ch for r in list(sender.recent)):
        return "channels that aren't ours"
    return None


def preflight(lamps):
    """Reads only. (status, dmx, lamp targets, problems that make a real run refuse)."""
    st, dmx = get_status(), get_dmx()
    problems = []
    live = live_screens(st)
    if live:
        problems.append(f"screen(s) live: {', '.join(live)}; our frames zero the screens' channels, so end that first")
    if not dmx.get("enabled"):
        problems.append("room_service's DMX stack is off")
    hue = dmx.get("hue") or {}
    state = hue.get("state")
    if state == "show":
        problems.append("hue.streamer is 'show': show_service streams the lamps, not room's Art-Net path")
    elif state in ("unavailable", "taken_over", "error", None):
        problems.append(f"the Hue output is {state}: {hue.get('detail')}")
    targets = lamp_targets(st, lamps)
    for lamp, m in targets.items():
        if m is None:
            problems.append(f"{lamp}: room has no such target")
        elif m.get("believed") in (None, "taken_over"):
            problems.append(f"{lamp}: room believes {m.get('believed')!r}, which can't be put back exactly")
    return st, dmx, targets, problems


class Monitor:
    """Room's view during the run, polled from its own thread so no HTTP sits on the flip path: another Art-Net sender
    on the universe (logged), a screen going live or the Hue output no longer streaming our lamps (abort), a mode
    change (logged)."""

    def __init__(self, sender, lamps, mode):
        self.sender, self.lamps, self.mode = sender, list(lamps), mode
        self.events, self.abort = [], None
        self.require_streaming = False
        self.polls = self.foreign = self.errors = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hue-cal-monitor", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(3)

    def _event(self, kind, detail):
        self.events.append({"t_wsl_ms": round(time.time_ns() / 1e6, 1), "kind": kind, "detail": detail})
        log(f"  monitor: {kind}: {detail}")

    def _run(self):
        fails = bad_hue = 0
        next_status, last_why = 0.0, None
        while not self._stop.wait(0.5):
            try:
                dmx = get_dmx()
                fails = 0
            except Exception as exc:                          # noqa: BLE001
                self.errors += 1
                fails += 1
                if fails == 10:
                    self.abort = f"room_service unreachable for 5 s ({exc})"
                continue
            self.polls += 1
            why = foreign_reason(dmx, self.sender)
            if why:
                self.foreign += 1
                if why != last_why:
                    self._event("foreign", why)
            last_why = why
            hue = dmx.get("hue") or {}
            if self.require_streaming:
                ok = hue.get("state") == "streaming" and set(self.lamps) <= set(hue.get("wanted") or [])
                bad_hue = 0 if ok else bad_hue + 1
                if bad_hue == 4:
                    self.abort = (f"the Hue output stopped streaming our lamps "
                                  f"(state {hue.get('state')}, wanted {hue.get('wanted')}: {hue.get('detail')})")
            if time.monotonic() >= next_status:
                next_status = time.monotonic() + 2.0
                try:
                    st = get_status()
                except Exception:                             # noqa: BLE001
                    continue
                live = live_screens(st)
                if live:
                    self.abort = f"screen(s) went live: {', '.join(live)}"
                mode = (st.get("mode") or {}).get("current")
                if mode != self.mode:
                    self._event("mode", f"{self.mode} -> {mode}")
                    self.mode = mode


def wait_exclusive(sender, timeout_s=20.0, clean_s=1.5):
    """Until room's input has shown only our frames for clean_s (show_service's yield check polls every 5 s)."""
    t0 = time.monotonic()
    clean_since, last = None, None
    while time.monotonic() - t0 < timeout_s:
        try:
            dmx = get_dmx()
            why = foreign_reason(dmx, sender)
        except Exception as exc:                              # noqa: BLE001
            dmx, why = None, f"room unreachable ({exc})"
        if why is None:
            clean_since = clean_since or time.monotonic()
            if time.monotonic() - clean_since >= clean_s:
                inp = dmx.get("input") or {}
                return {"after_s": round(time.monotonic() - t0, 1), "pps": inp.get("pps"), "last_from": inp.get("last_from")}
        else:
            clean_since, last = None, why
        time.sleep(0.25)
    raise RuntimeError(f"room's Art-Net input still shows another sender after {timeout_s:.0f} s ({last}); "
                       f"show_service's virtual Maestro should yield within ~5 s")


def arm_lamps(lamps, targets, armed):
    """Set each lamp not already "dmx" to "dmx", one hand set at a time (a hand set cancels the previous one's apply,
    so each is let finish); `armed` lists the ones we changed."""
    for lamp in lamps:
        if (targets[lamp] or {}).get("believed") == "dmx":
            log(f"  {lamp}: already follows Art-Net (dmx); left as is")
            continue
        armed.append(lamp)                                    # first: the PUT may land even if the reply is lost
        try:
            put_target(lamp, "dmx")
        except RoomRefused:
            armed.remove(lamp)                                # not accepted: nothing to put back
            raise
        if not wait_for(lambda: lamp in wanted(), 10):
            raise RuntimeError(f"{lamp} did not join the Hue output within 10 s")
        settled = wait_for(lambda: not member(lamp).get("inflight"), 12)
        log(f"  {lamp}: {targets[lamp].get('believed')!r} -> 'dmx'" + ("" if settled else " (room hasn't confirmed it yet)"))


def wait_streaming(lamps, timeout_s=25.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        hue = get_dmx().get("hue") or {}
        if hue.get("state") in ("taken_over", "error", "show"):
            raise RuntimeError(f"the Hue output is {hue.get('state')}: {hue.get('detail')}")
        if hue.get("state") == "streaming" and set(lamps) <= set(hue.get("wanted") or []):
            return {"after_s": round(time.monotonic() - t0, 1), "area": hue.get("area"),
                    "sent": {k: v for k, v in (hue.get("sent") or {}).items() if k in lamps}}
        time.sleep(0.25)
    raise RuntimeError(f"the Hue output did not stream {lamps} within {timeout_s:.0f} s")


def restore_targets(armed, before):
    """Put each lamp we changed back to exactly its believed value from before the run.

    Pass 1 hands each lamp back in turn, waiting only until it has left the Hue output: a hand set cancels the
    previous one's apply, and a lamp's state written while the area still streams may not stick. The last lamp out
    stops the stream (the bridge returns the lamps to their pre-stream state) and its apply is let finish.
    Pass 2 then sets again, one at a time and confirmed, every lamp room doesn't yet believe is back.
    A lamp whose mode-desired value changed during the run belongs to the new mode and is left alone."""
    report = {}
    try:
        now = lamp_targets(get_status(), armed)
    except Exception as exc:                                  # noqa: BLE001
        now = {}
        log(f"  restore: can't read room status ({exc!r}); putting back anyway")
    todo = []
    for lamp in armed:
        if now.get(lamp) and now[lamp].get("desired") != before[lamp].get("desired"):
            report[lamp] = f"left to the new mode (desired {now[lamp].get('desired')!r})"
        else:
            todo.append(lamp)

    def back(lamp):
        m = member(lamp)
        return m.get("believed") == before[lamp]["believed"] and not m.get("inflight")

    for lamp in todo:                                         # pass 1
        try:
            put_target(lamp, before[lamp]["believed"])
            wait_for(lambda: lamp not in wanted(), 10)
        except Exception as exc:                              # noqa: BLE001
            report[lamp] = f"failed: {exc}"
    if todo:
        wait_for(lambda: back(todo[-1]), 15)
    for lamp in todo:                                         # pass 2
        try:
            if back(lamp):
                report[lamp] = "restored"
                continue
            put_target(lamp, before[lamp]["believed"])
            report[lamp] = "restored" if wait_for(lambda: back(lamp), 15) else "not confirmed"
        except Exception as exc:                              # noqa: BLE001
            report[lamp] = f"failed: {exc}"
    for lamp in armed:
        line = f"cleanup: {lamp} -> {before[lamp]['believed']!r}: {report.get(lamp)}"
        log(line if report.get(lamp) == "restored" or "new mode" in str(report.get(lamp)) else
            f"!! {line}; by hand: curl -X PUT {ROOM_API}/room/targets -H 'Content-Type: application/json' "
            f"-d '{json.dumps({'values': {lamp: before[lamp]['believed']}, 'source': SOURCE})}'")
    return report


# ---------------------------------------------------------------- the run

def parse_lamps(spec, devs):
    lamps = list(devs) if not spec else [s.strip() if s.strip().startswith("hue.") else f"hue.{s.strip()}"
                                         for s in spec.split(",") if s.strip()]
    bad = [lamp for lamp in lamps if lamp not in devs]
    if bad or not lamps:
        sys.exit(f"--lamps: unknown {bad}; patched: {', '.join(devs)}")
    return lamps


def plan_flips(lamps, args, rng=random):
    """[(gap after the previous change in s, flashing lamps, level)]: white first, then alternating; --each runs one
    sequence per lamp (an even count, so each ends dark) with EACH_GAP_S of dark between lamps."""
    seqs = [[lamp] for lamp in lamps] if args.each else [list(lamps)]
    n = args.each_pulses + args.each_pulses % 2 if args.each else args.pulses
    out = []
    for k, flashing in enumerate(seqs):
        for i in range(n):
            gap = max(cal.MIN_INTERVAL_S, args.interval + rng.uniform(-args.jitter, args.jitter))
            if k and i == 0:
                gap = max(gap, EACH_GAP_S)
            out.append((gap, flashing, 1.0 if i % 2 == 0 else args.dark_level))
    return out


def sleep_until(t_mono, monitor=None):
    while True:
        if monitor and monitor.abort:
            raise RuntimeError(monitor.abort)
        left = t_mono - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(left, 0.1))


def run(args, layout, lamps):
    box = cal.Box(args.host)
    all_lamps = list(hue_devices(layout))
    tag = "hue-each" if args.each else "hue"
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{tag}"
    run_dir = os.path.join(cal.RUNS_DIR, run_id)
    box_log = f"{cal.BOX_DIR}/{run_id}.jsonl"
    plan = plan_flips(lamps, args)
    dark = {lamp: args.dark_level for lamp in lamps}
    run_s = args.preroll + 40 + sum(g for g, _, _ in plan) + TAIL_S
    meta = {"run": run_id, "lamps": lamps, "all_lamps": all_lamps, "each": args.each, "host": args.host,
            "args": vars(args), "zero": ZERO}

    log(f"preflight: lamps {', '.join(lamps)}{' (one at a time)' if args.each else ''}; {len(plan)} flips, "
        f"about {run_s:.0f} s")
    st, dmx, targets, problems = preflight(lamps)
    if problems:
        sys.exit("refusing to run:\n  " + "\n  ".join(problems))
    meta["preflight"] = {"mode": (st.get("mode") or {}).get("current"), "targets": targets,
                         "artnet_input": dmx.get("input"), "hue": {k: (dmx.get("hue") or {}).get(k)
                                                                  for k in ("state", "detail", "area", "wanted")}}
    log(f"  mode {meta['preflight']['mode']}; hue output {meta['preflight']['hue']['state']}; Art-Net in "
        f"{(dmx.get('input') or {}).get('pps')} pps from {(dmx.get('input') or {}).get('last_from')}; targets "
        + ", ".join(f"{lamp}={(m or {}).get('believed')!r}" for lamp, m in targets.items()))

    sensor = cal.RelayCapture(box, box_log, int(run_s + 90), args.freq)
    sender = monitor = None
    sensor_on, armed, pulses = False, [], []
    t_last_change, level_now = None, "dark"
    try:
        meta["clock_before"] = udp_clock_sync(box)
        c = meta["clock_before"]
        log(f"clock: box − WSL {c['offset_box_minus_wsl_ms']} ± {c['offset_uncertainty_ms']} ms "
            f"(UDP rtt {c['rtt_ms']})")
        log(f"sensor: starting capture at {args.freq}")
        sensor_on = True
        meta["sensor_header"] = sensor.start()
        log(f"  sensor {meta['sensor_header'].get('applied')} module {meta['sensor_header'].get('module')}")

        sender = ArtNetSender(layout, dark)
        sender.start()
        t_send0 = time.monotonic()
        meta["artnet"] = {"target": list(ARTNET_TARGET), "source": sender.source, "hz": SEND_HZ,
                          "universe": layout.universe, "dark_level": args.dark_level}
        log(f"art-net: dark frames at {SEND_HZ:.0f} Hz from {sender.source} to {ARTNET_TARGET[0]}:{ARTNET_TARGET[1]}; "
            f"waiting for room's input to be ours alone")
        meta["exclusive"] = wait_exclusive(sender)
        log(f"  only our frames after {meta['exclusive']['after_s']} s ({meta['exclusive']['pps']} pps)")
        monitor = Monitor(sender, lamps, meta["preflight"]["mode"])
        monitor.start()
        arm_lamps(lamps, targets, armed)
        meta["streaming"] = wait_streaming(lamps)
        monitor.require_streaming = True
        log(f"  Hue output streaming after {meta['streaming']['after_s']} s; sent {meta['streaming']['sent']}")
        t_go = max(t_send0 + args.preroll, time.monotonic() + args.baseline)
        log(f"flips: {len(plan)} in about {sum(g for g, _, _ in plan):.0f} s, after {t_go - time.monotonic():.1f} s dark")
        sleep_until(t_go, monitor)

        t_prev = t_go
        for i, (gap, flashing, level) in enumerate(plan):
            sleep_until(t_prev + gap, monitor)
            t_cmd = time.time_ns()
            at = sender.set_levels({**dark, **{lamp: level for lamp in flashing}})
            t_prev = t_last_change = time.monotonic()
            level_now = "white" if level == 1.0 else "dark"
            pulses.append({"i": i, "c": WHITE if level == 1.0 else BLACK, "level": level, "lamps": flashing,
                           "at_ns": at, "t_cmd_ms": t_cmd / 1e6})
            if (i + 1) % 10 == 0:
                log(f"  {i + 1}/{len(plan)} flips")
            if not args.each and args.early_check and i + 1 == args.early_check:
                samples = cal.parse_samples(box.run(f"cat {box_log}", check=False).stdout.splitlines())
                rows, s = cal.analyse(samples, pulses, meta["clock_before"])
                rows = rows[:-1]                              # the latest step has not settled yet
                seen = sum(r["detected"] for r in rows)
                log(f"  early check: {seen}/{len(rows)} steps visible; lux step {s['lux_step']}; snr {s['snr']}; "
                    f"reports {s['sensor']['report_hz']} Hz, measurements every {s['sensor']['measurement_ms']} ms")
                if seen == 0:
                    raise RuntimeError("the sensor does not see these lamps' steps; aborting early "
                                       "(try --each, or --lamps hue.se,hue.sw)")
        sleep_until(time.monotonic() + TAIL_S, monitor)
    finally:
        # Cleanup must survive a second Ctrl-C/SIGTERM/SIGHUP.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: log("signal ignored during cleanup"))
        if sender and level_now != "dark":
            try:
                time.sleep(max(0.0, cal.MIN_INTERVAL_S - (time.monotonic() - t_last_change)))   # photosafe
                sender.set_levels(dark)
                t_last_change, level_now = time.monotonic(), "dark"
                log("cleanup: lamps dark")
            except BaseException as exc:                      # noqa: BLE001
                log(f"cleanup: could not send dark ({exc!r})")
        if armed:
            if t_last_change is not None:
                time.sleep(max(0.0, cal.MIN_INTERVAL_S - (time.monotonic() - t_last_change)))   # photosafe
            try:
                meta["restore"] = restore_targets(armed, targets)
            except BaseException as exc:                      # noqa: BLE001
                log(f"!! restoring the lamps' targets failed ({exc!r}); they were: "
                    + json.dumps({lamp: targets[lamp]["believed"] for lamp in armed}))
        if monitor:
            monitor.stop()
            meta["monitor"] = {"polls": monitor.polls, "errors": monitor.errors, "foreign_polls": monitor.foreign,
                               "events": monitor.events, "abort": monitor.abort}
        if sender:
            sender.stop()
            meta["sender"] = {"frames": sender.frames, "errors": sender.errors, "last_error": sender.last_error}
            log(f"cleanup: Art-Net stopped after {sender.frames} frames ({sender.errors} errors); "
                f"show_service resumes once room's input goes stale")
        if sensor_on:
            try:
                log(f"cleanup: sensor capture ended: {sensor.stop()[:300]}")
            except BaseException as exc:                      # noqa: BLE001
                log(f"!! sensor cleanup failed: {exc!r}")

    try:
        meta["clock_after"] = udp_clock_sync(box)
    except Exception as exc:                                  # noqa: BLE001
        log(f"clock after the run failed ({exc!r}); using the one from before")
        meta["clock_after"] = None
    meta["clock"] = combine_clock(meta["clock_before"], meta["clock_after"])
    if meta["clock"].get("stable") is False:
        log(f"!! the box − WSL offset moved {meta['clock']['offset_change_ms']} ms during the run; treat it with care")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "samples.jsonl"), "w") as f:
        f.write(box.run(f"cat {box_log}").stdout)
    box.run(f"rm -f {box_log}", check=False)
    for name, obj in (("pulses.json", pulses), ("meta.json", meta)):
        with open(os.path.join(run_dir, name), "w") as f:
            json.dump(obj, f, indent=1 if name == "meta.json" else None, default=str)
    return report(run_dir, update_latest=True)


# ---------------------------------------------------------------- analysis and output

METHOD = ("hue: ArtDmx to room_service's receiver (127.0.0.1:6454) -> HueOutput 25 Hz -> Hue Bridge Pro "
          "Entertainment (xy+bri) -> lamp; zero = the first frame with the new level sent (WSL time.time_ns); "
          "box-clock photon timing; Yocto timed reports; WSL<->box offset by UDP echo (min RTT)")


def edge_t50(summary):
    out = {}
    for d in ("rise", "fall"):
        t50 = (summary["apply_to_photon_ms"].get(d) or {}).get("t50")
        if t50:
            out[d] = {"mean": t50["mean"], "se": round(t50["sd"] / max(1, t50["n"]) ** 0.5, 1), "n": t50["n"]}
    return out


def output_key(lamps, fixtures_file=FIXTURES_FILE):
    """show_service reads outputs.<fixture id>, then outputs.<the output's latency_key> (fixtures.yaml; default the
    output's id). The lamps' common output's key, else "hue"."""
    try:
        import yaml
        with open(fixtures_file) as f:
            fx = yaml.safe_load(f) or {}
        outs = {((fx.get("fixtures") or {}).get(lamp) or {}).get("output") for lamp in lamps}
        if len(outs) == 1 and None not in outs:
            oid = outs.pop()
            return ((fx.get("outputs") or {}).get(oid) or {}).get("latency_key") or oid
    except Exception:                                         # noqa: BLE001
        pass
    return "hue"


def latency_keys(lamps, all_lamps, fixtures_file=FIXTURES_FILE):
    """All the lamps together: the hue output's key. Fewer: each lamp's own id (they were measured together)."""
    return [output_key(lamps, fixtures_file)] if set(lamps) == set(all_lamps) else list(lamps)


def output_entry(result, run_dir, trim_ms=0.0):
    """latency.json `outputs.<key>.intensity` (schema in README): ms from the Art-Net frame to 10/50/90 % per edge."""
    s = result["summary"]
    edges = {}
    for d in ("rise", "fall"):
        lv = s["apply_to_photon_ms"].get(d) or {}
        if not lv.get("t50"):
            continue
        edges[d] = {f"t{f}": round(lv[f"t{f}"]["mean"], 1) for f in (10, 50, 90) if lv.get(f"t{f}")}
        edges[d]["n"] = lv["t50"]["n"]
    if not edges:
        return None
    return {**edges, "trim_ms": trim_ms, "zero": ZERO, "se_ms": s.get("standard_error_ms"),
            "method": result["method"], "measured_at": result["measured_at"],
            "run_dir": os.path.relpath(run_dir, cal.REPO), "lamps": result["lamps"]}


def update_latency_json(result, run_dir, path=cal.LATENCY_JSON):
    """Change only outputs.<key>.intensity for this run's keys; everything else is kept. Atomic replace."""
    latest = {}
    if os.path.exists(path):
        with open(path) as f:
            latest = json.load(f)
    outs = latest.setdefault("outputs", {})
    written = []
    for key in result["latency_keys"]:
        old = (outs.get(key) or {}).get("intensity") or {}
        entry = output_entry(result, run_dir, trim_ms=old.get("trim_ms", 0.0))   # a hand-set trim survives re-runs
        if entry:
            outs.setdefault(key, {})["intensity"] = entry
            written.append(f"outputs.{key}.intensity")
    latest.setdefault("sources", {})
    latest["version"] = 2
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(latest, f, indent=1)
    os.replace(tmp, path)
    return written


def report(run_dir, update_latest):
    with open(os.path.join(run_dir, "meta.json")) as f:
        meta = json.load(f)
    with open(os.path.join(run_dir, "pulses.json")) as f:
        pulses = json.load(f)
    with open(os.path.join(run_dir, "samples.jsonl")) as f:
        samples = cal.parse_samples(f.read().splitlines())
    clock = meta.get("clock") or meta["clock_before"]
    result = {"run": meta["run"], "lamps": meta["lamps"], "each": meta.get("each", False),
              "measured_at": dt.datetime.strptime(meta["run"][:16], "%Y%m%dT%H%M%SZ")
                             .replace(tzinfo=dt.timezone.utc).isoformat(),
              "method": METHOD, "zero": ZERO, "sensor_header": meta.get("sensor_header"),
              "clock": clock, "artnet": meta.get("artnet"), "exclusive": meta.get("exclusive"),
              "streaming": meta.get("streaming"), "monitor": meta.get("monitor"), "restore": meta.get("restore")}
    if result["each"]:
        rows, seen = [], {}
        for lamp in meta["lamps"]:
            r, s = cal.analyse(samples, [p for p in pulses if p["lamps"] == [lamp]], clock)
            rows += [{**x, "lamp": lamp} for x in r]
            seen[lamp] = {"detected": s["detected"], "flips": s["pulses"], "lux_step": s["lux_step"],
                          "snr": s["snr"], "t50": edge_t50(s)}
        result["lamps_seen"] = seen
    else:
        rows, summary = cal.analyse(samples, pulses, clock)
        result["summary"] = summary
        result["t50"] = edge_t50(summary)
        result["latency_keys"] = latency_keys(meta["lamps"], meta.get("all_lamps") or meta["lamps"])
    with open(os.path.join(run_dir, "result.json"), "w") as f:
        json.dump({**result, "pulses": rows}, f, indent=1, default=str)
    if update_latest and not result["each"] and result["summary"]["detected"]:
        result["latency_json"] = update_latency_json(result, run_dir)
    print_summary(result)
    return result


def print_summary(r):
    c = r["clock"]
    print(f"\n=== Hue latency calibration: {', '.join(r['lamps'])} {r['measured_at']}"
          + (" (one lamp at a time)" if r["each"] else ""))
    print(f"clock: box − WSL {c['offset_box_minus_wsl_ms']} ± {c['offset_uncertainty_ms']} ms "
          f"(UDP rtt {c.get('rtt_ms')}); change during the run {c.get('offset_change_ms')} ms; stable {c.get('stable')}")
    mon = r.get("monitor") or {}
    if mon.get("foreign_polls"):
        print(f"!! another Art-Net sender showed in {mon['foreign_polls']} of {mon['polls']} polls during the run: "
              f"{[e['detail'] for e in mon.get('events', []) if e['kind'] == 'foreign']}; treat this run with care")
    if r["each"]:
        print(f"{'lamp':8} {'seen':>6} {'lux step p50':>13} {'snr p50':>8}   t50 rise / fall (ms, mean ± se)")
        for lamp, s in r["lamps_seen"].items():
            step, snr, t = s["lux_step"] or {}, s["snr"] or {}, s["t50"]
            edges = " / ".join(f"{t[d]['mean']} ± {t[d]['se']}" if d in t else "-" for d in ("rise", "fall"))
            print(f"{lamp:8} {s['detected']:>2}/{s['flips']:<3} {step.get('p50', '-'):>13} {snr.get('p50', '-'):>8}   {edges}")
        return
    s = r["summary"]
    sen = s["sensor"]
    print(f"flips {s['pulses']}, detected {s['detected']}; lux step {s['lux_step']}; snr {s['snr']}")
    print(f"sensor: reports {sen['report_hz']} Hz, measurements every {sen['measurement_ms']} ms, "
          f"integration {sen['integration_ms']} ms ({sen['integration_source']}); bias removed {sen['bias_removed_ms']} ms")
    print(f"flip request -> frame sent: {s['cmd_to_apply_ms']}")
    for d in ("rise", "fall"):
        a = s["apply_to_photon_ms"][d]
        print(f"Art-Net sent -> light, {d}: t10 {a['t10']}\n                         t50 {a['t50']}\n"
              f"                         t90 {a['t90']}")
    if r.get("latency_json"):
        print(f"latency.json: {', '.join(r['latency_json'])}")
    t = r["t50"]
    print("SUMMARY Art-Net send -> light t50: " + "; ".join(
        f"{d} {t[d]['mean']} ± {t[d]['se']} ms (n {t[d]['n']})" for d in ("rise", "fall") if d in t)
        + f"  [clock ± {c['offset_uncertainty_ms']} ms]")


# ---------------------------------------------------------------- dry run

def dry_run(args, layout, lamps):
    """Everything but the lamps: status reads, clock sync, a 3 s sensor capture. Nothing is set or sent."""
    box = cal.Box(args.host)
    st, dmx, targets, problems = preflight(lamps)
    inp, hue = dmx.get("input") or {}, dmx.get("hue") or {}
    print(f"room: mode {(st.get('mode') or {}).get('current')}; live screens {live_screens(st) or 'none'}")
    print(f"Art-Net input: {inp.get('pps')} pps from {inp.get('last_from')} (source {inp.get('source')}, "
          f"age {inp.get('age_ms')} ms)")
    print(f"Hue output: state {hue.get('state')} {hue.get('detail') or ''}; wanted {hue.get('wanted')}; "
          f"area {hue.get('area')}")
    devs = hue_devices(layout)
    white, dark = universe(layout, {lamp: 1.0 for lamp in lamps}), universe(layout, {lamp: args.dark_level for lamp in lamps})
    for lamp in lamps:
        m, a = targets.get(lamp) or {}, devs[lamp].address
        print(f"  {lamp} @{a}: white {list(white[a - 1:a + 3])} dark {list(dark[a - 1:a + 3])}; room believes "
              f"{m.get('believed')!r} (desired {m.get('desired')!r}, policy {m.get('policy')}): "
              + ("left as is" if m.get("believed") == "dmx" else f"set to 'dmx', then back to {m.get('believed')!r}"))
    print("a real run would refuse: " + ("; ".join(problems) if problems else "no"))

    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-hue-dry"
    box_log = f"{cal.BOX_DIR}/{run_id}.jsonl"
    sensor = cal.RelayCapture(box, box_log, int(args.dry_seconds + 15), args.freq)
    sensor_on, footer = False, ""
    before = udp_clock_sync(box)
    print(f"clock before: box − WSL {before['offset_box_minus_wsl_ms']} ± {before['offset_uncertainty_ms']} ms; "
          f"rtt {before['rtt_ms']}; probes {before['probes']}")
    try:
        sensor_on = True
        header = sensor.start()
        print(f"sensor: capture started: {header.get('applied')} module {header.get('module')} "
              f"relay {header.get('version')} held lux {header.get('held_lux')}")
        time.sleep(args.dry_seconds)
    finally:
        if sensor_on:
            footer = sensor.stop()
    samples = cal.parse_samples(box.run(f"cat {box_log}", check=False).stdout.splitlines())
    box.run(f"rm -f {box_log}", check=False)
    after = udp_clock_sync(box)
    clock = combine_clock(before, after)
    hz = cal.report_rate(samples)
    meas = cal.measurements(samples, 1000.0 / hz if hz else 10.0)
    gaps = [b[0] - a[0] for a, b in zip(meas, meas[1:]) if b[0] - a[0] < 1000]
    vals = [v for _, v, _, _ in samples]
    print(f"sensor: {len(samples)} reports in {args.dry_seconds:.0f} s ({hz} Hz); measurements every "
          f"{round(statistics.median(gaps), 1) if gaps else None} ms; lux median "
          f"{round(statistics.median(vals), 3) if vals else None}, MAD "
          f"{round(statistics.median(abs(v - statistics.median(vals)) for v in vals), 3) if vals else None}")
    print(f"sensor: capture ended: {footer[:300]}")
    print(f"clock after: box − WSL {after['offset_box_minus_wsl_ms']} ± {after['offset_uncertainty_ms']} ms; "
          f"combined {clock['offset_box_minus_wsl_ms']} ms, change {clock['offset_change_ms']} ms, stable {clock['stable']}")
    plan = plan_flips(lamps, args)
    print(f"a real run: {len(plan)} flips over about {args.preroll + sum(g for g, _, _ in plan) + TAIL_S:.0f} s "
          f"(+ arming and putting back), Art-Net {SEND_HZ:.0f} Hz from {SOURCE_IP}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=cal.DEFAULT_HOST)
    ap.add_argument("--lamps", help="comma-separated hue.* devices (default: every lamp in patch.yaml), e.g. hue.se,hue.sw")
    ap.add_argument("--each", action="store_true", help="a short sequence per lamp: which lamps the sensor sees, with SNR "
                                                        "(no latency.json write)")
    ap.add_argument("--each-pulses", type=int, default=6, help="--each: flips per lamp (rounded up to even)")
    ap.add_argument("--pulses", type=int, default=20, help="flips, alternating white/dark")
    ap.add_argument("--interval", type=float, default=1.5, help="seconds between flips")
    ap.add_argument("--jitter", type=float, default=0.2, help="± random seconds on each interval (randomises the phase)")
    ap.add_argument("--dark-level", type=float, default=0.0,
                    help="the dark level, 0..1 of white (default 0: DIMMER 255, RGB 0)")
    ap.add_argument("--preroll", type=float, default=8.0, help="seconds of dark Art-Net before the first flip")
    ap.add_argument("--baseline", type=float, default=3.0, help="at least this long streaming dark before the first flip")
    ap.add_argument("--freq", default="100/s", help="Yocto timed-report frequency")
    ap.add_argument("--early-check", type=int, default=5, help="after this many flips, abort if the sensor saw none (0 = off)")
    ap.add_argument("--dry-run", action="store_true",
                    help="status reads, clock sync and a short sensor capture only: no target set, no Art-Net sent")
    ap.add_argument("--dry-seconds", type=float, default=3.0)
    ap.add_argument("--yes", action="store_true", help="confirm the room is dark and the lamps may flash")
    ap.add_argument("--reanalyse", metavar="RUN_DIR")
    ap.add_argument("--update-latest", action="store_true", help="with --reanalyse: also write config/room/latency.json")
    args = ap.parse_args()

    if args.reanalyse:
        report(args.reanalyse, update_latest=args.update_latest)
        return
    if args.jitter < 0 or args.interval - args.jitter < cal.MIN_INTERVAL_S:
        sys.exit(f"photosafe: need jitter ≥ 0 and interval − jitter ≥ {cal.MIN_INTERVAL_S} s")
    if not 0.0 <= args.dark_level < 1.0:
        sys.exit("--dark-level: 0 ≤ level < 1")
    layout = load_layout(PATCH_FILE)
    lamps = parse_lamps(args.lamps, hue_devices(layout))

    def on_term(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_term)
    if args.dry_run:
        dry_run(args, layout, lamps)
        return
    if not args.yes:
        n = len(plan_flips(lamps, args))
        sys.exit(f"This flashes {', '.join(lamps)} white/dark {n} times, at least {cal.MIN_INTERVAL_S:.0f} s apart, "
                 f"for about {args.preroll + n * args.interval + 30:.0f} s. Re-run with --yes when the room is dark.")
    run(args, layout, lamps)


if __name__ == "__main__":
    main()
