#!/usr/bin/env python3
"""Screen → light-sensor latency calibration (run from WSL with the repo's .venv).

Measures how long a change on a kiosk screen takes to reach the room's light sensor,
and from that the effective render-ahead the renderer needs for beat-phased visuals.

What one run does:
  1. Preflight: SSH to the kiosk box over the wired link and find the screen's page over CDP.
  2. Network: ICMP ping and CDP round trips over the wired link; the WSL↔box clock
     offset (NTP-style, minimum-RTT samples) and the page's performance-clock drift,
     measured before and after the pulses.
  3. Sensor, one of two strategies:
       relay      (default, no sudo) signal the light relay (lightsense.py "calib-*") to
                  log fast, box-timestamped timed reports while it keeps running. It
                  holds its output at the pre-capture lux, so auto-brightness stays steady.
       exclusive  stop the relay and run kiosk_sampler.py, which has exclusive USB access.
  4. Screen: inject a full-screen overlay into the page (no navigation) and toggle it
     white/black at a photosafe pace (≥ MIN_INTERVAL_S between changes). Each change is
     logged with its rAF frame and, through Chrome's Element Timing API, the time Chrome
     reports the frame as presented. The overlay removes itself if this script goes silent.
  5. Cleanup, always and cancellation-safe: remove the overlay (no sooner than
     MIN_INTERVAL_S after the last change), end the capture (the sensor's settings are
     restored), and in exclusive mode restart the relay.
  6. Analysis: rebuild the sensor's own measurement events from the repeated reports,
     estimate its integration window from the partial measurements, find each pulse's
     10/50/90 % crossings on the box clock, and write a run directory plus
     config/room/latency.json.

The glass leg is timed entirely on the box's clock (CLOCK_REALTIME: Python time.time_ns()
for sensor reports, Date.now() in Chrome), so the network does not enter it. The network
is measured separately and only used for the command → apply leg.

Usage (see README.md):
  .venv/bin/python tools/room/latency_cal/calibrate.py --screen south --yes
  .venv/bin/python tools/room/latency_cal/calibrate.py --probe            # exclusive; relay stopped
  .venv/bin/python tools/room/latency_cal/calibrate.py --reanalyse tools/room/latency_cal/runs/<run>
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import random
import re
import signal
import socket
import statistics
import subprocess
import sys
import time
import urllib.request

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RUNS_DIR = os.path.join(HERE, "runs")
LATENCY_JSON = os.path.join(REPO, "config", "room", "latency.json")
ROOM_API = "http://127.0.0.1:8700/api/v1"      # --path live: room_service puts the screen live
STREAM_API = "http://127.0.0.1:8702/api"       # ...and stream_service's session takes the flips
LIVE_SCENE = "cal-flash"                       # stream_service/plugins/sources/cal_flash.py
LIVE_LEAD_S = 0.4                              # each flip is scheduled this far after its command
STREAM_LOG = os.path.expanduser("~/.local/state/screen-machine/logs/stream_service.log")   # cal_flash logs each flip

DEFAULT_HOST = "gjbm2@192.168.1.10"          # kiosk box, wired (enp1s0)
FALLBACK_HOST = "gjbm2@192.168.1.92"         # the box's Wi-Fi address, used only for cleanup retries
CDP_PORTS = {"north": 9222, "south": 9223}
BOX_DIR = "~/latency_cal"
BOX_PY = "~/.venv/bin/python"
RELAY_UNIT = "light-relay"
RELAY_VERSION_PREFIX = "calib-"
MIN_INTERVAL_S = 1.0                          # photosafe: ≥1 s between changes (≤0.5 flash/s)
OVERLAY_ID = "__smLatencyCal"
MEASURE_TYPES = {"human_eye": 0, "wide_spectrum": 1, "infrared": 2,
                 "high_rate": 3, "high_energy": 4, "high_resolution": 5}

# The overlay removes itself when no change has arrived for EXTEND ms, or at CAP ms after
# injection, but never sooner than MIN_GAP ms after the last change (photosafe).
JS_INJECT = """(() => {
  if (window.__smLat) return 'exists';
  const MIN_GAP = __MIN_GAP__, EXTEND = __EXTEND__, CAP = Date.now() + __CAP__;
  const d = document.createElement('div');
  d.id = '__ID__';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#000;'
    + 'pointer-events:none;transition:none;animation:none;opacity:1;';
  document.documentElement.appendChild(d);
  const s = {d, log: [], et: {}, last: Date.now(), deadline: Date.now() + EXTEND, mark: null};
  window.__smLat = s;
  try {
    s.obs = new PerformanceObserver(list => {
      for (const e of list.getEntries())
        if (e.identifier && e.identifier.startsWith('smlat-')) s.et[e.identifier] = e.renderTime || e.loadTime;
    });
    s.obs.observe({type: 'element', buffered: true});
  } catch (err) { s.etError = String(err); }
  s.remove = () => {
    clearInterval(s.wd);
    try { s.obs && s.obs.disconnect(); } catch (err) {}
    d.remove();
    if (window.__smLat === s) { delete window.__smLat; delete window.__smLatSet; }
  };
  s.wd = setInterval(() => {
    const now = Date.now();
    if ((now > s.deadline || now > CAP) && now - s.last >= MIN_GAP) s.remove();
  }, 250);
  window.__smLatSet = (c, i) => new Promise(res => requestAnimationFrame(t1 => {
    d.style.background = c;
    if (s.mark) s.mark.remove();
    const m = document.createElement('span');
    m.setAttribute('elementtiming', 'smlat-' + i);
    m.textContent = '.';
    m.style.cssText = 'position:absolute;left:0;top:0;font:8px sans-serif;color:' + c + ';';
    d.appendChild(m);
    s.mark = m;
    const tSet = performance.timeOrigin + performance.now();
    s.last = Date.now();
    s.deadline = Math.min(CAP, s.last + EXTEND);
    requestAnimationFrame(t2 => {
      const e = {i, c, raf1: performance.timeOrigin + t1, tSet,
                 raf2: performance.timeOrigin + t2, dn: s.last};
      s.log.push(e);
      res(e);
    });
  }));
  return 'ok';
})()"""

JS_FETCH_TIMING = """(() => {
  const s = window.__smLat;
  return s ? {et: s.et, timeOrigin: performance.timeOrigin, error: s.etError || null} : null;
})()"""

JS_REMOVE = """(() => {
  const s = window.__smLat;
  if (s && s.remove) s.remove();
  const stray = document.getElementById('__ID__');
  if (stray) stray.remove();
  delete window.__smLat; delete window.__smLatSet;
  return document.getElementById('__ID__') ? 'still-present' : 'removed';
})()""".replace("__ID__", OVERLAY_ID)

JS_CLOCK = "[performance.timeOrigin + performance.now(), Date.now()]"


def js_inject(extend_ms, cap_ms):
    return (JS_INJECT.replace("__ID__", OVERLAY_ID).replace("__MIN_GAP__", str(int(MIN_INTERVAL_S * 1000)))
            .replace("__EXTEND__", str(int(extend_ms))).replace("__CAP__", str(int(cap_ms))))


# ---------------------------------------------------------------- helpers

def log(msg):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def quantiles(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    q = lambda p: xs[min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))]
    return {"n": len(xs), "p10": round(q(0.1), 1), "p50": round(q(0.5), 1),
            "p90": round(q(0.9), 1), "mean": round(statistics.fmean(xs), 1),
            "sd": round(statistics.pstdev(xs), 1) if len(xs) > 1 else 0.0}


class Box:
    """SSH access to the kiosk box. Non-interactive calls run in their own session, so a
    Ctrl-C at the terminal reaches only this script, never the ssh children it needs for cleanup."""

    def __init__(self, host):
        self.host = host
        self.ip = host.split("@")[-1]

    def run(self, cmd, check=True, timeout=30, stdin=None, tty=False, host=None):
        args = ["ssh", "-o", "ConnectTimeout=5"]
        args += ["-t"] if tty else ["-o", "BatchMode=yes"]
        res = subprocess.run(args + [host or self.host, cmd], input=stdin, capture_output=not tty,
                             text=True, timeout=timeout, start_new_session=not tty)
        if check and res.returncode != 0:
            raise RuntimeError(f"ssh `{cmd}` failed ({res.returncode}): {(res.stderr or '').strip()}")
        return res

    def relay_active(self):
        return self.run(f"systemctl is-active {RELAY_UNIT}", check=False).stdout.strip() == "active"

    def ping(self, count=20):
        res = subprocess.run(["ping", "-c", str(count), "-i", "0.2", self.ip],
                             capture_output=True, text=True, timeout=count + 10, start_new_session=True)
        rtts = [float(m) for m in re.findall(r"time=([\d.]+) ms", res.stdout)]
        return {"target": self.ip, "sent": count, "received": len(rtts), "rtt_ms": quantiles(rtts)}

    def wait_header(self, path, alive_cmd, timeout_s=15):
        for _ in range(int(timeout_s / 0.25)):
            out = self.run(f"head -1 {path} 2>/dev/null; echo '<<>>'; {alive_cmd} && echo alive",
                           check=False).stdout
            first, alive = (out.split("<<>>") + [""])[:2]
            if first.strip().startswith("{"):
                return json.loads(first.strip().splitlines()[0])
            if "alive" not in alive:
                raise RuntimeError("the sensor capture process is not running")
            time.sleep(0.25)
        raise RuntimeError(f"no capture header in {path} within {timeout_s} s")


class RelayCapture:
    """Ask the running light relay (lightsense.py calib-*) for a capture. No sudo."""

    def __init__(self, box, path, seconds, freq, measure_type=None):
        self.box, self.path, self.seconds, self.freq = box, path, seconds, freq
        self.measure_type = measure_type
        self.pid = None

    def start(self):
        if not self.box.relay_active():
            raise RuntimeError(f"{RELAY_UNIT} is not running; start it, or use --sensor exclusive")
        main_pid = self.box.run(f"systemctl show -p MainPID --value {RELAY_UNIT}").stdout.strip()
        status = json.loads(self.box.run(f"cat {BOX_DIR}/relay.json").stdout)
        if str(status.get("pid")) != main_pid or not str(status.get("version", "")).startswith(RELAY_VERSION_PREFIX):
            raise RuntimeError(f"the running relay (pid {main_pid}) is not a calibration-capable "
                               f"lightsense.py (status {status}); deploy tools/room/latency_cal/lightsense.py")
        if self.measure_type is not None and status.get("version") == "calib-1":
            raise RuntimeError("the deployed relay is calib-1, which cannot set the measure type; redeploy it")
        self.pid = main_pid
        request = {"out": self.path.replace("~", "/home/" + self.box.host.split("@")[0]),
                   "seconds": self.seconds, "freq": self.freq, "hold_output": True}
        if self.measure_type is not None:
            request["sensor"] = {"measureType": self.measure_type}
        self.box.run(f"cat > {BOX_DIR}/capture.json", stdin=json.dumps(request))
        self.box.run(f"kill -USR1 {self.pid}")
        return self.box.wait_header(self.path, f"kill -0 {self.pid} 2>/dev/null")

    def stop(self):
        if not self.pid:
            return ""
        self.box.run(f"kill -USR2 {self.pid}", check=False)
        return self.box.run(f"for i in $(seq 25); do tail -1 {self.path} | grep -q footer && break; "
                            f"sleep 0.2; done; tail -1 {self.path}", check=False).stdout.strip()


class ExclusiveSampler:
    """Stop the relay and run kiosk_sampler.py with exclusive USB access."""

    def __init__(self, box, path, seconds, freq, measure_type, relay_mode, run_id):
        self.box, self.path, self.seconds, self.freq = box, path, seconds, freq
        self.measure_type, self.relay_mode, self.run_id = measure_type, relay_mode, run_id
        self.stopped_relay = self.started = False
        self.pid = None

    def relay(self, action):
        if self.relay_mode == "sudo-n":
            self.box.run(f"sudo -n systemctl {action} {RELAY_UNIT}")
        elif self.relay_mode == "tty":
            self.box.run(f"sudo systemctl {action} {RELAY_UNIT}", tty=True, timeout=120)
        else:
            return False
        return True

    def start(self):
        self.box.run(f"{BOX_PY} -c 'import yoctopuce'")
        push_sampler(self.box)
        if self.box.relay_active():
            if self.relay_mode == "none":
                raise RuntimeError(f"{RELAY_UNIT} is running; stop it first (or --relay tty|sudo-n)")
            log(f"stopping {RELAY_UNIT} ({self.relay_mode})")
            self.stopped_relay = self.relay("stop")
        name = next(k for k, v in MEASURE_TYPES.items() if v == (self.measure_type or 0))
        self.pid = self.box.run(
            f"cd {BOX_DIR} && setsid nohup {BOX_PY} kiosk_sampler.py run --out {self.run_id}.jsonl "
            f"--pidfile {self.run_id}.pid --seconds {self.seconds} --freq {self.freq} "
            f"--measure-type {name} > {self.run_id}.stderr 2>&1 < /dev/null & echo $!").stdout.strip()
        self.started = True
        return self.box.wait_header(self.path, f"kill -0 {self.pid} 2>/dev/null")

    def stop(self):
        out = ""
        if self.started and self.pid:
            out = self.box.run(f"kill -TERM {self.pid} 2>/dev/null; "
                               f"for i in $(seq 50); do kill -0 {self.pid} 2>/dev/null || break; sleep 0.2; done; "
                               f"tail -1 {self.path}", check=False).stdout.strip()
            if '"restored": true' not in out:
                log("sensor not confirmed restored; running kiosk_sampler.py restore")
                out += " | " + self.box.run(f"cd {BOX_DIR} && {BOX_PY} kiosk_sampler.py restore",
                                            check=False).stdout.strip()
        if self.stopped_relay:
            try:
                self.relay("start")
            finally:
                log(f"cleanup: {RELAY_UNIT} {'active' if self.box.relay_active() else 'NOT active'}"
                    f" (manual: ssh -t {self.box.host} sudo systemctl start {RELAY_UNIT})")
        elif self.started and self.relay_mode == "none":
            log(f"reminder: restart the relay: ssh -t {self.box.host} sudo systemctl start {RELAY_UNIT}")
        return out


def push_sampler(box):
    with open(os.path.join(HERE, "kiosk_sampler.py")) as f:
        box.run(f"mkdir -p {BOX_DIR} && cat > {BOX_DIR}/kiosk_sampler.py", stdin=f.read())


class Tunnel:
    """ssh -L to a CDP port on the box, in its own session (immune to the terminal's Ctrl-C)."""

    def __init__(self, host, remote_port):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ExitOnForwardFailure=yes", "-N",
             "-L", f"{self.port}:127.0.0.1:{remote_port}", host],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        for _ in range(80):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                if self.proc.poll() is not None:
                    err = self.proc.stderr.read().decode()
                    raise RuntimeError(f"tunnel failed: {err}")
                time.sleep(0.1)
        self.close()
        raise RuntimeError("tunnel did not come up")

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.next_id = 0

    @classmethod
    async def open(cls, tunnel, screen):
        with urllib.request.urlopen(f"http://127.0.0.1:{tunnel.port}/json", timeout=3) as r:
            targets = json.load(r)
        pages = [t for t in targets if t.get("type") == "page" and f"/display/{screen}" in t.get("url", "")]
        if not pages:
            raise RuntimeError(f"no /display/{screen} page among {[t.get('url') for t in targets]}")
        ws_url = re.sub(r"^ws://[^/]+", f"ws://127.0.0.1:{tunnel.port}", pages[0]["webSocketDebuggerUrl"])
        return cls(await websockets.connect(ws_url, max_size=None, close_timeout=2)), pages[0]["url"]

    async def eval(self, expression, await_promise=False, timeout=10):
        self.next_id += 1
        my_id = self.next_id
        await self.ws.send(json.dumps({"id": my_id, "method": "Runtime.evaluate", "params": {
            "expression": expression, "returnByValue": True, "awaitPromise": await_promise}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if msg.get("id") == my_id:
                break
        if "error" in msg:
            raise RuntimeError(f"CDP error: {msg['error']}")
        result = msg["result"]
        if "exceptionDetails" in result:
            raise RuntimeError(f"page exception: {result['exceptionDetails'].get('text')}")
        return result["result"].get("value")

    async def close(self):
        await self.ws.close()


async def remove_overlay(cdp, args):
    """Remove the overlay; on failure retry over a fresh tunnel (wired, then Wi-Fi)."""
    if cdp:
        try:
            return await cdp.eval(JS_REMOVE, timeout=5)
        except BaseException as exc:                  # noqa: BLE001 — cleanup must continue
            log(f"overlay removal over the run's CDP link failed ({exc!r}); retrying")
    for host in (args.host, FALLBACK_HOST):
        tunnel = None
        try:
            tunnel = Tunnel(host, CDP_PORTS[args.screen])
            fresh, _ = await CDP.open(tunnel, args.screen)
            try:
                return await fresh.eval(JS_REMOVE, timeout=5)
            finally:
                await fresh.close()
        except BaseException as exc:                  # noqa: BLE001
            log(f"overlay removal via {host} failed ({exc!r})")
        finally:
            if tunnel:
                tunnel.close()
    log(f"!! overlay NOT removed. It removes itself within about {args.watchdog_s:.0f} s; "
        f"or restore the screen now: ssh {args.host} \"pkill -f -- '--user-data-dir=/tmp/chrome-{args.screen}'\" "
        f"(kiosk-wayland.sh respawns that Chrome in about 10 s)")
    return "not-removed"


# ---------------------------------------------------------------- measurement

async def clock_sync(cdp, rounds=30):
    """WSL↔box wall-clock offset (box − WSL) and the page's performance-clock drift."""
    samples = []
    for _ in range(rounds):
        t0 = time.time_ns()
        perf_ms, date_ms = await cdp.eval(JS_CLOCK)
        t1 = time.time_ns()
        samples.append({"rtt_ms": (t1 - t0) / 1e6, "mid_ms": (t0 + t1) / 2e6,
                        "perf_ms": perf_ms, "date_ms": date_ms})
        await asyncio.sleep(0.02)
    best = sorted(samples, key=lambda s: s["rtt_ms"])[:8]
    return {
        "offset_box_minus_wsl_ms": round(statistics.median(s["date_ms"] - s["mid_ms"] for s in best), 2),
        "offset_uncertainty_ms": round(best[0]["rtt_ms"] / 2 + 0.5, 2),   # ± half min-RTT + Date.now() resolution
        "perf_minus_date_ms": round(statistics.median(s["perf_ms"] - s["date_ms"] for s in samples), 2),
        "cdp_rtt_ms": quantiles([s["rtt_ms"] for s in samples]),
    }


def combine_clock(before, after):
    """Average the before/after clock measurements; flag a step between them."""
    if not after:
        return {**before, "stable": None}
    return {**before,
            "offset_box_minus_wsl_ms": round((before["offset_box_minus_wsl_ms"] + after["offset_box_minus_wsl_ms"]) / 2, 2),
            "perf_minus_date_ms": round((before["perf_minus_date_ms"] + after["perf_minus_date_ms"]) / 2, 2),
            "offset_change_ms": round(after["offset_box_minus_wsl_ms"] - before["offset_box_minus_wsl_ms"], 2),
            "drift_change_ms": round(after["perf_minus_date_ms"] - before["perf_minus_date_ms"], 2),
            "stable": abs(after["perf_minus_date_ms"] - before["perf_minus_date_ms"]) <= 1.0}


def parse_samples(lines):
    """Timed reports as (t_ms box wall, avg, lo, hi), sorted."""
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "t_ns" in rec and "v" in rec:
            out.append((rec["t_ns"] / 1e6, rec["v"], rec.get("lo", rec["v"]), rec.get("hi", rec["v"])))
    return sorted(out)


def report_rate(samples):
    gaps = [b[0] - a[0] for a, b in zip(samples, samples[1:])]
    return round(1000 / statistics.median(gaps), 1) if gaps else None


def measurements(samples, report_ms):
    """Collapse repeated timed reports into the sensor's own measurement events.

    The sensor makes a new measurement only every ~50-100 ms (mode-dependent); faster
    reports repeat it. A report whose lo != hi straddles a change. Each event is timed half
    a report period before the first report that shows the new value, and takes its value
    from the first report that is wholly new."""
    pts, cur, pending = [], None, None
    for t, v, lo, hi in samples:
        pure = lo == hi
        if cur is None:
            if pure:
                cur = v
                pts.append((t, v))
            continue
        if pending is None and v != cur:
            pending = t - report_ms / 2
        if pending is not None and pure:
            cur = v
            pts.append((pending, v))
            pending = None
    return pts


def crossing(ts, vs, j_from, j_to, level, sign, meas_ms, last=False):
    """Interpolated time where the measurement series crosses level, searching events
    j_from..j_to-1. An unchanged value produces no event, so the event before event j may be
    stale; the measurement before j was published one period earlier with the old value."""
    hit_j = None
    for j in range(max(j_from, 1), j_to):
        if (vs[j] - level) * sign >= 0 and (vs[j - 1] - level) * sign < 0:
            hit_j = j
            if not last:
                break
    if hit_j is None:
        return None, None
    j = hit_j
    t_prev = max(ts[j - 1], ts[j] - meas_ms) if meas_ms else ts[j - 1]
    return t_prev + (level - vs[j - 1]) / (vs[j] - vs[j - 1]) * (ts[j] - t_prev), j


def fit_integration(points, meas_ms):
    """Estimate the sensor's integration window from partial measurements.

    For a sharp light step at time s, a measurement whose window [p − I, p] straddles s
    reads the fraction f = (p − s)/I of the new level, so p − apply = (s − apply) + f·I.
    Regressing each partial event's time on its fraction gives slope I. Darkening steps
    are used when there are enough, because the panel's fall is fast; its residual
    ramp makes this a slight overestimate."""
    if len(points) < 5:
        return None
    xs = [f for f, _ in points]
    ys = [t for _, t in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-6:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    se = (sum(r * r for r in resid) / max(1, len(xs) - 2) / sxx) ** 0.5
    return {"integration_ms": round(slope, 1), "se_ms": round(se, 1), "n": len(points),
            "plausible": 15 <= slope <= 1.3 * meas_ms if meas_ms else False}


def analyse(samples, pulses, sync, timing=None):
    """Find each pulse's lux step on the box clock and summarise.

    samples: parse_samples() output. pulses: page log entries plus t_cmd_ms (WSL clock).
    timing: Element Timing results {et: {"smlat-i": renderTime}, timeOrigin}, or None."""
    report_hz = report_rate(samples)
    report_ms = 1000.0 / report_hz if report_hz else 10.0
    meas = measurements(samples, report_ms)
    gaps = [b[0] - a[0] for a, b in zip(meas, meas[1:]) if b[0] - a[0] < 250]
    meas_ms = statistics.median(gaps) if gaps else None
    ts = [t for t, _ in meas]
    vs = [v for _, v in meas]
    drift = sync["perf_minus_date_ms"]
    offset = sync["offset_box_minus_wsl_ms"]
    et = (timing or {}).get("et") or {}
    origin = (timing or {}).get("timeOrigin")

    def apply_of(p):                                          # box wall clock
        if "at_ns" in p:
            return p.get("render_ns", p["at_ns"]) / 1e6 + offset
        return p["raf1"] - drift

    rows, partials = [], {"rise": [], "fall": []}
    for k, p in enumerate(pulses):
        apply_ms = apply_of(p)
        nxt = apply_of(pulses[k + 1]) if k + 1 < len(pulses) else apply_ms + 2000
        pre = [v for t, v, _, _ in samples if apply_ms - 400 <= t < apply_ms]
        post = [v for t, v, _, _ in samples if apply_ms + 0.6 * (nxt - apply_ms) <= t < nxt - 20]
        row = {"i": p["i"], "dir": "rise" if p["c"].lower() in ("#fff", "#ffffff", "white") else "fall",
               "apply_box_ms": round(apply_ms, 2), "cmd_to_apply_ms": round(apply_ms - offset - p["t_cmd_ms"], 2)}
        if "raf1" in p:
            row.update(frame_ms=round(p["raf2"] - p["raf1"], 2),
                       cmd_to_callback_ms=round(p["tSet"] - drift - offset - p["t_cmd_ms"], 2))
        rt = et.get(f"smlat-{p['i']}")
        if rt and origin:
            row["presented_box_ms"] = round(origin + rt - drift, 2)
            row["apply_to_presented_ms"] = round(row["presented_box_ms"] - apply_ms, 2)
        if len(pre) < 3 or len(post) < 3:
            rows.append({**row, "detected": False, "why": "too few samples"})
            continue
        base, top = statistics.median(pre), statistics.median(post)
        delta = top - base
        mad = statistics.median(abs(v - base) for v in pre) * 1.4826
        row.update(base_lux=round(base, 3), top_lux=round(top, 3), snr=round(abs(delta) / max(mad, 0.01), 1))
        if abs(delta) < max(6 * mad, 0.5) or (row["dir"] == "rise") != (delta > 0):
            rows.append({**row, "detected": False, "why": f"no clear step (Δ={delta:.2f}, noise={mad:.2f})"})
            continue
        sign = 1 if delta > 0 else -1
        start = next((j for j, t in enumerate(ts) if t >= apply_ms), None)
        end = next((j for j, t in enumerate(ts) if t >= nxt), len(ts))
        if start is None:
            rows.append({**row, "detected": False, "why": "no measurements after apply"})
            continue
        t50, j50 = crossing(ts, vs, start, end, base + 0.5 * delta, sign, meas_ms)
        if t50 is None:
            rows.append({**row, "detected": False, "why": "no 50% crossing"})
            continue
        # 10 %: the last crossing at or before the 50 % one, so a noise blip earlier is ignored.
        t10, _ = crossing(ts, vs, start, j50 + 1, base + 0.1 * delta, sign, meas_ms, last=True)
        t90, _ = crossing(ts, vs, j50, end, base + 0.9 * delta, sign, meas_ms)
        row.update({"raw_t10_ms": round(t10 - apply_ms, 2) if t10 is not None else None,
                    "raw_t50_ms": round(t50 - apply_ms, 2),
                    "raw_t90_ms": round(t90 - apply_ms, 2) if t90 is not None else None,
                    "detected": True})
        inter = [(t, (v - base) / delta) for t, v in zip(ts[start:end], vs[start:end]) if 0.05 < (v - base) / delta < 0.95]
        row["intermediate_points"] = len(inter)
        partials[row["dir"]] += [(f, t - apply_ms) for t, f in inter]
        rows.append(row)

    det = [r for r in rows if r["detected"]]
    fit = fit_integration(partials["fall"], meas_ms) or fit_integration(partials["fall"] + partials["rise"], meas_ms)
    if fit and fit["plausible"]:
        integ, integ_src = fit["integration_ms"], "fitted from partial measurements"
    else:
        integ, integ_src = meas_ms or 0.0, "assumed equal to the measurement period"
    bias = integ / 2
    for r in det:
        for f in (10, 50, 90):
            raw = r.get(f"raw_t{f}_ms")
            r[f"t{f}_ms"] = round(raw - bias, 2) if raw is not None else None
        if "presented_box_ms" in r:
            r["presented_to_photon_ms"] = round(r["t50_ms"] - r["apply_to_presented_ms"], 2)

    by_dir = {d: [r for r in det if r["dir"] == d] for d in ("rise", "fall")}
    frames = [r["frame_ms"] for r in rows if r.get("frame_ms") is not None]
    frame = statistics.median(frames) if frames else None
    summary = {
        "pulses": len(rows), "detected": len(det),
        "sensor": {"report_hz": report_hz, "measurement_ms": round(meas_ms, 1) if meas_ms else None,
                   "integration_ms": round(integ, 1), "integration_source": integ_src,
                   "integration_fit": fit, "bias_removed_ms": round(bias, 1),
                   "intermediate_points": quantiles([r["intermediate_points"] for r in det])},
        "frame_ms": round(frame, 2) if frame else None,
        "cmd_to_callback_ms": quantiles([r.get("cmd_to_callback_ms") for r in rows]),
        "cmd_to_apply_ms": quantiles([r["cmd_to_apply_ms"] for r in rows]),
        "apply_to_presented_ms": quantiles([r.get("apply_to_presented_ms") for r in rows]),
        "apply_to_photon_ms": {d: {f"t{f}": quantiles([r.get(f"t{f}_ms") for r in rs])
                                   for f in (10, 50, 90)} for d, rs in by_dir.items()},
        "presented_to_photon_ms": {d: quantiles([r.get("presented_to_photon_ms") for r in rs])
                                   for d, rs in by_dir.items()},
        "lux_step": quantiles([abs(r["top_lux"] - r["base_lux"]) for r in det]),
        "snr": quantiles([r["snr"] for r in det]),
    }
    rise = summary["apply_to_photon_ms"]["rise"]["t50"]
    fall = summary["apply_to_photon_ms"]["fall"]["t50"]
    if rise:
        summary["standard_error_ms"] = round(rise["sd"] / max(1, rise["n"]) ** 0.5, 1)
        p2p = summary["presented_to_photon_ms"]["rise"]
        if p2p:
            summary["presented_to_photon_ms_used"] = p2p["mean"]
            summary["presented_source"] = "Element Timing renderTime (measured)"
        elif frame:
            # Fallback: a DOM change made in frame N's rAF is presented about one frame later.
            summary["presented_to_photon_ms_used"] = round(rise["mean"] - frame, 1)
            summary["presented_source"] = "rAF frame start + one frame (estimate)"
        summary["cmd_to_photon_ms"] = round(summary["cmd_to_apply_ms"]["mean"] + rise["mean"], 1)
    if rise and fall:
        summary["rise_minus_fall_ms"] = round(rise["mean"] - fall["mean"], 1)
    return rows, summary


def effective(summary, stream_to_presented_ms, source):
    """Render→photon for the live path = stream capture→presented + presented→photon (rise)."""
    p2p = summary.get("presented_to_photon_ms_used")
    if p2p is None or stream_to_presented_ms is None:
        return None
    fall = summary["presented_to_photon_ms"].get("fall")
    return {"render_to_photon_ms": round(stream_to_presented_ms + p2p, 1),
            "render_to_photon_fall_ms": round(stream_to_presented_ms + fall["mean"], 1) if fall else None,
            "stream_to_presented_ms": stream_to_presented_ms,
            "stream_to_presented_source": source,
            "presented_to_photon_ms": p2p,
            "presented_source": summary.get("presented_source"),
            "random_uncertainty_ms": summary.get("standard_error_ms"),
            "note": "rise (dark→bright) edge; the brightening edge is the one beats are phased to"}


def http_json(method, url, body=None, timeout=5):
    req = urllib.request.Request(url, method=method, headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return json.loads(data) if data else None


class LiveFlash:
    """--path live: one screen goes live on the cal-flash scene through room_service; each flip is
    scheduled on the renderer's own wall clock (at_ns), so the renderer → photon path is timed end to end."""

    def __init__(self, screen, hold_s):
        self.room_id = f"{screen}-screen"
        self.hold_s = int(hold_s)
        self.sid = None
        self.level = 0
        self.on = False

    def start(self, timeout_s=45):
        self.on = True                                         # set first: the PUT may land even if the reply is lost
        http_json("PUT", f"{ROOM_API}/screens/{self.room_id}",
                  {"desired": {"live": {"scene": LIVE_SCENE}}, "for_s": self.hold_s})
        deadline, obs = time.monotonic() + timeout_s, {}
        while time.monotonic() < deadline:
            obs = http_json("GET", f"{ROOM_API}/status")["screens"][self.room_id]["observed"]
            sess = obs.get("session")
            if obs.get("layer") == "live" and sess:
                self.sid = sess.get("sid") or sess.get("id") if isinstance(sess, dict) else sess
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"{self.room_id} did not go live on {LIVE_SCENE} within {timeout_s} s "
                               f"(observed {obs.get('layer')}/{obs.get('phase')} {obs.get('detail')})")
        try:                                                   # stop the mode's director cutting away mid-run
            http_json("PATCH", f"{STREAM_API}/sessions/{self.sid}", {"auto": None})
        except Exception as exc:                               # noqa: BLE001
            log(f"  could not turn the session's director off ({exc}); a cut-away would end the run early")
        http_json("PATCH", f"{STREAM_API}/sessions/{self.sid}", {"params": {"white": 0, "at_ns": 0, "then": 0}})
        return self.sid

    def flip_at(self, at_ns, level):
        http_json("PATCH", f"{STREAM_API}/sessions/{self.sid}",
                  {"params": {"white": self.level, "at_ns": at_ns, "then": level}})
        self.level = level

    def stop(self):
        http_json("PUT", f"{ROOM_API}/screens/{self.room_id}", {"desired": "art"})
        self.on = False

    @staticmethod
    def rendered(pulses):
        """Replace each scheduled at_ns with the frame's actual render time from cal_flash's log line."""
        try:
            with open(STREAM_LOG, errors="replace") as f:
                found = dict((int(a), int(r)) for r, a in
                             re.findall(r"cal_flash flip to \w+: render_ns=(\d+) at_ns=(\d+)", f.read()))
        except OSError:
            return 0
        n = 0
        for p in pulses:
            if p.get("at_ns") in found:
                p["render_ns"] = found[p["at_ns"]]
                n += 1
        return n


# ---------------------------------------------------------------- orchestration

async def calibrate(args):
    box = Box(args.host)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{args.screen}"
    run_dir = os.path.join(RUNS_DIR, run_id)
    box_log = f"{BOX_DIR}/{run_id}.jsonl"
    meta = {"run": run_id, "screen": args.screen, "host": args.host, "args": vars(args)}
    run_s = args.pulses * (args.interval + args.jitter) + args.baseline + 30
    mtype = MEASURE_TYPES[args.measure_type] if args.measure_type else None
    sensor = (RelayCapture(box, box_log, int(run_s + 90), args.freq, mtype) if args.sensor == "relay" else
              ExclusiveSampler(box, box_log, int(run_s + 90), args.freq, mtype, args.relay, run_id))

    log(f"preflight: {args.host}; sensor strategy {args.sensor}; path {args.path}")
    meta["path"] = args.path
    live = LiveFlash(args.screen, run_s + 60) if args.path == "live" else None
    tunnel = cdp = None
    sensor_on = overlay_on = False
    pulses, timing, clock_after = [], None, None
    t_last_change = None
    try:
        meta["ping"] = box.ping()
        log(f"network: ping rtt {meta['ping']['rtt_ms']}")
        tunnel = Tunnel(args.host, CDP_PORTS[args.screen])
        cdp, url = await CDP.open(tunnel, args.screen)
        meta["page_url"] = url
        meta["clock_before"] = await clock_sync(cdp)
        log(f"clock {meta['clock_before']}")

        log(f"sensor: starting capture at {args.freq}" + (f", measure type {args.measure_type}" if mtype is not None else ""))
        sensor_on = True
        meta["sensor_header"] = sensor.start()
        log(f"  sensor {meta['sensor_header'].get('applied')} module {meta['sensor_header'].get('module')}")

        if live:
            log(f"screen: {args.screen} going live on {LIVE_SCENE} (black), {args.baseline:.0f} s baseline")
            t_last_change = time.monotonic()
            meta["live_session"] = await asyncio.to_thread(live.start)
            log(f"  live session {meta['live_session']}")
        else:
            log(f"screen: overlay on {args.screen} (black), {args.baseline:.0f} s baseline")
            overlay_on = True                                 # set first: the inject may run even if the reply is lost
            t_last_change = time.monotonic()
            await cdp.eval(js_inject(args.watchdog_s * 1000, run_s * 1000))
        await asyncio.sleep(args.baseline)

        colours = ["#ffffff", "#000000"]
        for i in range(args.pulses):
            await asyncio.sleep(max(MIN_INTERVAL_S, args.interval + random.uniform(-args.jitter, args.jitter)))
            t_cmd = time.time_ns()
            t_last_change = time.monotonic() + (LIVE_LEAD_S if live else 0)
            if live:
                at = t_cmd + int(LIVE_LEAD_S * 1e9)           # flips stay ≥ MIN_INTERVAL_S apart: same spacing as t_cmd
                await asyncio.to_thread(live.flip_at, at, 1 - i % 2)
                entry = {"i": i, "c": colours[i % 2], "at_ns": at}
            else:
                entry = await cdp.eval(f"window.__smLatSet('{colours[i % 2]}', {i})", await_promise=True)
            entry["t_cmd_ms"] = t_cmd / 1e6
            entry["t_ack_ms"] = time.time_ns() / 1e6
            pulses.append(entry)
            if (i + 1) % 10 == 0:
                log(f"  {i + 1}/{args.pulses} pulses")
            if args.early_check and i + 1 == args.early_check:
                samples = parse_samples(box.run(f"cat {box_log}", check=False).stdout.splitlines())
                rows, s = analyse(samples, pulses, meta["clock_before"])
                rows = rows[:-1]                              # the latest step has not settled yet
                seen = sum(r["detected"] for r in rows)
                log(f"  early check: {seen}/{len(rows)} steps visible; lux step {s['lux_step']}; "
                    f"reports {s['sensor']['report_hz']} Hz, measurements every {s['sensor']['measurement_ms']} ms")
                if seen == 0:
                    raise RuntimeError("the sensor does not see this screen's steps; aborting early")
        await asyncio.sleep(2.2)                              # the last step's post window must stay clean
        if live:
            log(f"  flip render times from the renderer log: {LiveFlash.rendered(pulses)}/{len(pulses)}")
        else:
            timing = await cdp.eval(JS_FETCH_TIMING)
        clock_after = await clock_sync(cdp, rounds=15)
    finally:
        # Cleanup must survive a second Ctrl-C/SIGTERM/SIGHUP and a pending cancellation.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda *_: log("signal ignored during cleanup"))
        task = asyncio.current_task()
        while task.cancelling():
            task.uncancel()
        if overlay_on:
            if t_last_change is not None:                     # photosafe: no change within 1 s of the last
                time.sleep(max(0.0, MIN_INTERVAL_S - (time.monotonic() - t_last_change)))
            log(f"cleanup: overlay {await remove_overlay(cdp, args)}")
        if live and live.on:
            if t_last_change is not None:                     # photosafe: no change within 1 s of the last
                time.sleep(max(0.0, MIN_INTERVAL_S - (time.monotonic() - t_last_change)))
            try:
                live.stop()
                log(f"cleanup: {live.room_id} back to art")
            except BaseException as exc:                      # noqa: BLE001
                log(f"!! could not put {live.room_id} back to art ({exc!r}); "
                    f"run: curl -X PUT {ROOM_API}/screens/{live.room_id} -d '{{\"desired\": \"art\"}}'")
        for step, fn in (("cdp", lambda: cdp.close() if cdp else None),):
            try:
                res = fn()
                if asyncio.iscoroutine(res):
                    await asyncio.wait_for(res, 3)
            except BaseException as exc:                      # noqa: BLE001
                log(f"cleanup: {step} close failed ({exc!r})")
        if tunnel:
            tunnel.close()
        if sensor_on:
            try:
                log(f"cleanup: sensor capture ended: {sensor.stop()[:300]}")
            except BaseException as exc:                      # noqa: BLE001
                log(f"!! sensor cleanup failed: {exc!r}")

    meta["clock"] = combine_clock(meta["clock_before"], clock_after)
    if meta["clock"].get("stable") is False:
        log(f"!! the page clock moved during the run ({meta['clock']['drift_change_ms']} ms); treat this run with care")
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "samples.jsonl"), "w") as f:
        f.write(box.run(f"cat {box_log}").stdout)
    box.run(f"rm -f {box_log}", check=False)
    for name, obj in (("pulses.json", pulses), ("timing.json", timing), ("meta.json", meta)):
        with open(os.path.join(run_dir, name), "w") as f:
            json.dump(obj, f, indent=1 if name == "meta.json" else None)
    return report(run_dir, args.stream_to_presented_ms, args.stream_source, update_latest=True)


def report(run_dir, stream_to_presented_ms, stream_source, update_latest):
    with open(os.path.join(run_dir, "meta.json")) as f:
        meta = json.load(f)
    with open(os.path.join(run_dir, "pulses.json")) as f:
        pulses = json.load(f)
    with open(os.path.join(run_dir, "samples.jsonl")) as f:
        samples = parse_samples(f.read().splitlines())
    timing = None
    if os.path.exists(os.path.join(run_dir, "timing.json")):
        with open(os.path.join(run_dir, "timing.json")) as f:
            timing = json.load(f)
    clock = meta.get("clock") or meta.get("clock_before")
    rows, summary = analyse(samples, pulses, clock, timing)
    path = meta.get("path", "dom")
    eff = effective(summary, stream_to_presented_ms, stream_source) if path == "dom" else None
    result = {"run": meta["run"], "screen": meta["screen"],
              "measured_at": dt.datetime.strptime(meta["run"][:16], "%Y%m%dT%H%M%SZ")
                             .replace(tzinfo=dt.timezone.utc).isoformat(),
              "path": path,
              "method": ("dom-overlay via CDP; box-clock photon timing; Yocto timed reports; Element Timing"
                         if path == "dom" else f"live: {LIVE_SCENE} scene via room_service + stream_service; "
                         "flips scheduled at at_ns on the renderer clock; box-clock photon timing; Yocto timed reports"),
              "sensor_header": meta.get("sensor_header"),
              "network": {"ping": meta.get("ping"), "cdp_rtt_ms": clock["cdp_rtt_ms"],
                          "one_way_ms_est": round(meta["ping"]["rtt_ms"]["p10"] / 2, 2) if meta.get("ping", {}).get("rtt_ms") else None,
                          "clock_offset_box_minus_wsl_ms": clock["offset_box_minus_wsl_ms"],
                          "clock_offset_uncertainty_ms": clock["offset_uncertainty_ms"],
                          "clock_stable": clock.get("stable")},
              "summary": summary, "effective": eff}
    with open(os.path.join(run_dir, "result.json"), "w") as f:
        json.dump({**result, "pulses": rows}, f, indent=1)
    if update_latest and summary["detected"]:
        update_latency_json(result, run_dir)
    print_summary(result)
    return result


def output_entry(result, run_dir, trim_ms=0.0):
    """latency.json `outputs` entry per edge and level (schema in README). The DOM path is measured from the
    frame's presented time; the live path from the renderer's scheduled flip (at_ns)."""
    s = result["summary"]
    if result.get("path") == "live":
        presented, zero = 0.0, "renderer frame with the flip (render_ns from cal_flash's log, else the scheduled at_ns)"
    else:
        presented, zero = (s.get("apply_to_presented_ms") or {}).get("mean"), "frame presented (Element Timing renderTime)"
    if presented is None:
        return None
    edges = {}
    for d in ("rise", "fall"):
        levels = s["apply_to_photon_ms"].get(d) or {}
        if not levels.get("t50"):
            continue
        edges[d] = {f"t{f}": round(levels[f"t{f}"]["mean"] - presented, 1)
                    for f in (10, 50, 90) if levels.get(f"t{f}")}
        edges[d]["n"] = levels["t50"]["n"]
    return {**edges, "trim_ms": trim_ms, "zero": zero,
            "se_ms": s.get("standard_error_ms"), "method": result["method"],
            "measured_at": result["measured_at"], "run_dir": os.path.relpath(run_dir, REPO)}


def update_latency_json(result, run_dir):
    """Update only this run's keys; other writers' outputs and sources are kept. Atomic replace."""
    latest = {}
    if os.path.exists(LATENCY_JSON):
        with open(LATENCY_JSON) as f:
            latest = json.load(f)
    live = result.get("path") == "live"
    if not live:                                       # stream_service reads screens.<id> (the TV's own share)
        latest.setdefault("screens", {})[result["screen"]] = {**result, "run_dir": os.path.relpath(run_dir, REPO)}
    key, attr = f"tv-{result['screen']}", "live" if live else "brightness"
    old = latest.setdefault("outputs", {}).get(key, {}).get(attr, {})
    entry = output_entry(result, run_dir, trim_ms=old.get("trim_ms", 0.0))   # a hand-set trim survives re-runs
    if entry:
        latest["outputs"].setdefault(key, {})[attr] = entry
    latest.setdefault("sources", {})
    latest["version"] = 2
    os.makedirs(os.path.dirname(LATENCY_JSON), exist_ok=True)
    tmp = LATENCY_JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(latest, f, indent=1)
    os.replace(tmp, LATENCY_JSON)


def print_summary(r):
    s, n = r["summary"], r["network"]
    print("\n=== latency calibration:", r["screen"], r["measured_at"])
    print(f"pulses {s['pulses']}, detected {s['detected']}; lux step {s['lux_step']}; snr {s['snr']}")
    sen = s["sensor"]
    print(f"sensor: reports {sen['report_hz']} Hz, measurements every {sen['measurement_ms']} ms, "
          f"integration {sen['integration_ms']} ms ({sen['integration_source']}); fit {sen['integration_fit']}")
    print(f"network: ping {n['ping']['rtt_ms'] if n['ping'] else None}; one-way ≈ {n['one_way_ms_est']} ms")
    print(f"         CDP rtt {n['cdp_rtt_ms']}; clock offset box−WSL {n['clock_offset_box_minus_wsl_ms']} "
          f"± {n['clock_offset_uncertainty_ms']} ms; stable {n['clock_stable']}")
    print(f"command → page callback: {s['cmd_to_callback_ms']}")
    print(f"apply frame → presented (Element Timing): {s['apply_to_presented_ms']}")
    for d in ("rise", "fall"):
        a = s["apply_to_photon_ms"][d]
        print(f"apply → photon, {d}: t10 {a['t10']}\n                     t50 {a['t50']}\n                     t90 {a['t90']}")
        print(f"presented → photon, {d}: {s['presented_to_photon_ms'][d]}")
    print(f"rise − fall: {s.get('rise_minus_fall_ms')} ms; presented → photon used: "
          f"{s.get('presented_to_photon_ms_used')} ms [{s.get('presented_source')}] ± {s.get('standard_error_ms')} (s.e.)")
    if r["effective"]:
        e = r["effective"]
        print(f"EFFECTIVE render → photon: {e['render_to_photon_ms']} ms (rise; fall {e['render_to_photon_fall_ms']}) "
              f"= {e['stream_to_presented_ms']} stream→presented [{e['stream_to_presented_source']}] "
              f"+ {e['presented_to_photon_ms']} presented→photon")


def sensor_modes(args):
    """Through the running relay (no sudo, no screen): try each measure type for a few
    seconds and report what the module applied and how often it actually measures."""
    box = Box(args.host)
    for name, value in MEASURE_TYPES.items():
        path = f"{BOX_DIR}/modes-{name}.jsonl"
        cap = RelayCapture(box, path, int(args.probe_seconds + 10), args.freq, value)
        try:
            header = cap.start()
            time.sleep(args.probe_seconds)
        except Exception as exc:
            print(json.dumps({"measure_type": name, "error": str(exc)}))
            continue
        finally:
            footer = cap.stop()
        samples = parse_samples(box.run(f"cat {path}", check=False).stdout.splitlines())
        box.run(f"rm -f {path}", check=False)
        hz = report_rate(samples)
        meas = measurements(samples, 1000.0 / hz if hz else 10.0)
        gaps = [b[0] - a[0] for a, b in zip(meas, meas[1:]) if b[0] - a[0] < 1000]
        print(json.dumps({"measure_type": name, "requested": value,
                          "applied": header.get("applied", {}).get("measureType"),
                          "firmware": (header.get("module") or {}).get("firmware"),
                          "reports_hz": hz, "measurement_ms_p50": round(statistics.median(gaps), 1) if gaps else None,
                          "lux_median": round(statistics.median(v for _, v, _, _ in samples), 3) if samples else None,
                          "restored": '"restored": true' in footer}), flush=True)


def probe(args):
    box = Box(args.host)
    push_sampler(box)
    if box.relay_active():
        sys.exit(f"{RELAY_UNIT} is running; --probe needs exclusive USB access (stop it first)")
    res = box.run(f"cd {BOX_DIR} && {BOX_PY} kiosk_sampler.py probe --seconds {args.probe_seconds}",
                  timeout=300, check=False)
    print(res.stdout)
    if res.returncode:
        print(res.stderr, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--screen", choices=list(CDP_PORTS), default="south")
    ap.add_argument("--path", choices=["dom", "live"], default="dom",
                    help="dom: a page overlay (the TV's own share); live: the cal-flash scene through the live stream "
                         "(renderer → photon end to end)")
    ap.add_argument("--pulses", type=int, default=20, help="number of changes (alternating white/black); 20 gives about ±4 ms")
    ap.add_argument("--interval", type=float, default=1.2, help="seconds between changes (the TV settles in about 0.4 s)")
    ap.add_argument("--jitter", type=float, default=0.2, help="± random seconds on each interval (randomises the sensor phase)")
    ap.add_argument("--baseline", type=float, default=3.0)
    ap.add_argument("--freq", default="100/s", help="Yocto timed-report frequency")
    ap.add_argument("--sensor", choices=["relay", "exclusive"], default="relay",
                    help="relay: signal the running light relay (no sudo); exclusive: stop it and sample directly")
    ap.add_argument("--measure-type", choices=list(MEASURE_TYPES),
                    help="sensor mode for the capture (restored afterwards); default: leave as is")
    ap.add_argument("--relay", choices=["none", "tty", "sudo-n"], default="tty",
                    help="exclusive mode: how to stop/start light-relay")
    ap.add_argument("--early-check", type=int, default=5,
                    help="after this many pulses, abort if the sensor saw none (0 = off)")
    ap.add_argument("--watchdog-s", type=float, default=10.0,
                    help="the overlay removes itself after this long without a command from this script")
    ap.add_argument("--stream-to-presented-ms", type=float,
                    help="capture→presented for the live stream path (from bd's lab), to compute render→photon")
    ap.add_argument("--stream-source", default="bd lab, rVFC expectedDisplayTime, p50")
    ap.add_argument("--yes", action="store_true", help="confirm an agreed owner window for taking over the screen")
    ap.add_argument("--probe", action="store_true", help="only measure the sensor's achievable report rates")
    ap.add_argument("--sensor-modes", action="store_true",
                    help="through the relay (no sudo, no screen): which measure types the sensor accepts, and how fast each measures")
    ap.add_argument("--probe-seconds", type=float, default=3.0)
    ap.add_argument("--reanalyse", metavar="RUN_DIR")
    ap.add_argument("--update-latest", action="store_true",
                    help="with --reanalyse: also write the result to config/room/latency.json")
    args = ap.parse_args()

    if args.reanalyse:
        report(args.reanalyse, args.stream_to_presented_ms, args.stream_source, update_latest=args.update_latest)
        return
    if args.probe:
        probe(args)
        return
    if args.sensor_modes:
        sensor_modes(args)
        return
    if args.jitter < 0 or args.interval - args.jitter < MIN_INTERVAL_S:
        sys.exit(f"photosafe: need jitter ≥ 0 and interval − jitter ≥ {MIN_INTERVAL_S} s")
    if args.watchdog_s < args.interval + args.jitter + 5:
        sys.exit("--watchdog-s must exceed the longest gap between changes by at least 5 s")
    if not args.yes:
        sys.exit(f"This takes over the {args.screen} TV for about "
                 f"{args.pulses * args.interval + args.baseline + 10:.0f} s with slow white/black changes. "
                 f"Re-run with --yes inside an agreed owner window.")

    def on_term(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, on_term)
    asyncio.run(calibrate(args))


if __name__ == "__main__":
    main()
