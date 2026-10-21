#!/usr/bin/env python3
"""Kiosk output watchdog: un-freezes a TV whose output stops drawing.

Why (21 Oct 2026): at 06:07 BST a new art clip on south set off the usual burst of "Atomic commit failed: Device or
resource busy" from the Intel driver on HDMI-A-2, and this time sway 1.9 / wlroots 0.17.1 never drew that output again:
Chrome got no frames, the TV showed the last picture for two hours. `swaymsg output HDMI-A-2 power off` then `power on`
brought it back. Those "busy" refusals happen 10-130 times an hour on both outputs, so the stall will recur until sway
is upgraded; this makes it recover in about 15 s instead.

How: every PROBE_S, for each TV whose sway output is on, ask that TV's Chrome (its local DevTools port) for a few
animation frames. A page on a working output gets 60 a second; one on a frozen output gets none. After STALL_S of no
frames: power-cycle that output (about 3 s of black on that TV). Still none RECHECK_S later: restart that Chrome (the
launcher, kiosk-wayland.sh, relaunches it within 5 s). Still none: log it and try again after BACKOFF_S. A renderer that
stops answering for HUNG_S gets its Chrome restarted. Nothing else is touched (not sway, kiosk.service or the mic).

Runs as the systemd --user unit kiosk-output-watchdog.service (deploy-output-watchdog.sh). Stdlib only.
--dry-run logs what it would do; --once probes once and prints.
"""
import argparse
import base64
import glob
import json
import os
import signal
import socket
import struct
import subprocess
import time
import urllib.parse
import urllib.request

SCREENS = {"north": ("HDMI-A-1", 9222), "south": ("HDMI-A-2", 9223)}
PROBE_S = 5          # seconds between probes
STALL_S = 10         # no frames this long -> power-cycle the output
RECHECK_S = 15       # after an action, wait this long before judging it
BACKOFF_S = 600      # after the whole ladder failed, wait this long before trying again
HUNG_S = 30          # renderer not answering this long (Chrome up) -> restart Chrome
LOG = "/var/log/kiosk.log"

# Resolves with the number of animation frames seen in 2 s (stops counting at 3).
FRAMES_JS = ("new Promise(r => { let n = 0; const f = () => { if (++n >= 3) r(n); else requestAnimationFrame(f); };"
             " requestAnimationFrame(f); setTimeout(() => r(n), 2000); })")


def log(msg):
    line = f"{time.strftime('%F %T')}  watchdog: {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# --- sway ---

def sway_sock():
    uid = os.getuid()
    for path in sorted(glob.glob(f"/run/user/{uid}/sway-ipc.{uid}.*.sock"), key=os.path.getmtime, reverse=True):
        try:
            os.kill(int(path.rsplit(".", 2)[-2]), 0)
            return path
        except (ValueError, ProcessLookupError, PermissionError):
            continue
    return None


def swaymsg(sock, *args):
    return subprocess.run(["swaymsg", "-s", sock, *args], capture_output=True, text=True, timeout=10)


def outputs_on(sock):
    r = swaymsg(sock, "-r", "-t", "get_outputs")
    if r.returncode:
        return set()
    return {o["name"] for o in json.loads(r.stdout) if o.get("active") and o.get("power", o.get("dpms", True))}


# --- Chrome DevTools, a minimal websocket client ---

def _recv_exact(s, n):
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def _recv_message(s):
    data = b""
    while True:
        b0, b1 = _recv_exact(s, 2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", _recv_exact(s, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _recv_exact(s, 8))[0]
        mask = _recv_exact(s, 4) if b1 & 0x80 else None
        payload = _recv_exact(s, n)
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        op = b0 & 0x0F
        if op == 8:
            raise ConnectionError("closed by Chrome")
        if op in (0, 1, 2):
            data += payload
            if b0 & 0x80:
                return data


def cdp_eval(ws_url, expr, timeout):
    u = urllib.parse.urlparse(ws_url)
    with socket.create_connection((u.hostname, u.port), timeout=timeout) as s:
        key = base64.b64encode(os.urandom(16)).decode()
        s.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = s.recv(4096)
            if not chunk:
                raise ConnectionError("no handshake")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        msg = json.dumps({"id": 1, "method": "Runtime.evaluate",
                          "params": {"expression": expr, "awaitPromise": True, "returnByValue": True}}).encode()
        mask = os.urandom(4)
        n = len(msg)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        s.sendall(hdr + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(msg)))
        while True:
            reply = json.loads(_recv_message(s))
            if reply.get("id") == 1:
                return reply.get("result", {}).get("result", {}).get("value")


def display_page(port):
    """The /display/ page's websocket URL, or None if this Chrome isn't up."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
            targets = json.load(r)
    except (OSError, ValueError):
        return None
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    pages.sort(key=lambda t: "/display/" not in t.get("url", ""))
    return pages[0]["webSocketDebuggerUrl"] if pages else None


def probe(port):
    """('frames', n) | ('hung', None) if the page doesn't answer | ('down', None) if Chrome isn't up."""
    ws = display_page(port)
    if not ws:
        return "down", None
    try:
        return "frames", int(cdp_eval(ws, FRAMES_JS, timeout=6) or 0)
    except (OSError, ValueError, ConnectionError):
        return ("hung", None) if display_page(port) else ("down", None)


def chrome_browser_pid(name):
    """The main browser process of /tmp/chrome-<name> (no --type=), ours only."""
    for d in glob.glob("/proc/[0-9]*"):
        try:
            if os.stat(d).st_uid != os.getuid():
                continue
            argv = open(f"{d}/cmdline", "rb").read().split(b"\0")
        except OSError:
            continue
        if (argv and argv[0].endswith(b"/chrome") and f"--user-data-dir=/tmp/chrome-{name}".encode() in argv
                and not any(a.startswith(b"--type=") for a in argv)):
            return int(d[6:])
    return None


# --- the ladder ---

class Screen:
    def __init__(self, name, output, port):
        self.name, self.output, self.port = name, output, port
        self.bad_since = None      # first probe with no frames (or no answer)
        self.kind = None           # "frames" | "hung"
        self.step = 0              # 0 none, 1 output power-cycled, 2 Chrome restarted, 3 gave up
        self.acted_at = 0.0

    def reset(self, why=None):
        if self.bad_since is not None and why:
            log(f"{self.name}: {why}" + ("" if self.step else
                                         f" after {time.monotonic() - self.bad_since:.0f} s, no action needed"))
        self.bad_since, self.kind, self.step = None, None, 0

    def act(self, sock, now, dry):
        if self.step == 0 and self.kind == "frames":
            log(f"{self.name}: {self.output} drew no frames for {now - self.bad_since:.0f} s: power-cycling the output"
                + (" (dry run)" if dry else ""))
            if not dry:
                swaymsg(sock, "output", self.output, "power", "off")
                time.sleep(1)
                swaymsg(sock, "output", self.output, "power", "on")
            self.step = 1
        elif self.step <= 1:
            pid = chrome_browser_pid(self.name)
            what = "still no frames" if self.kind == "frames" else f"page not answering for {now - self.bad_since:.0f} s"
            log(f"{self.name}: {what}: restarting its Chrome (pid {pid})" + (" (dry run)" if dry else ""))
            if pid and not dry:
                os.kill(pid, signal.SIGTERM)
            self.step = 2
        else:
            log(f"{self.name}: still frozen after an output power-cycle and a Chrome restart; trying again in "
                f"{BACKOFF_S // 60} min")
            self.step = 3
        self.acted_at = now

    def tick(self, sock, on, dry):
        now = time.monotonic()
        if self.output not in on:
            return self.reset(f"{self.output} switched off; standing down")
        state, n = probe(self.port)
        if state == "down":                       # Chrome (re)starting: the launcher's job
            return
        if state == "frames" and n > 0:
            return self.reset(f"{self.output} drawing again ({n} frames in the probe)")
        if self.bad_since is None:
            self.bad_since, self.kind = now, state
            log(f"{self.name}: {'no frames' if state == 'frames' else 'page not answering'}; watching")
            return
        self.kind = state
        wait = {0: STALL_S if state == "frames" else HUNG_S, 1: RECHECK_S, 2: RECHECK_S, 3: BACKOFF_S}[self.step]
        since = self.bad_since if self.step == 0 else self.acted_at
        if now - since >= wait:
            if self.step == 3:
                self.step = 0
            self.act(sock, now, dry)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="log the actions, don't take them")
    ap.add_argument("--once", action="store_true", help="probe each TV once, print, exit")
    a = ap.parse_args()
    screens = [Screen(n, o, p) for n, (o, p) in SCREENS.items()]
    if a.once:
        sock = sway_sock()
        on = outputs_on(sock) if sock else set()
        for s in screens:
            print(s.name, s.output, "on" if s.output in on else "off", *probe(s.port))
        return
    log(f"start (probe every {PROBE_S} s, act after {STALL_S} s without frames{', dry run' if a.dry_run else ''})")
    while True:
        sock = sway_sock()
        if sock:
            on = outputs_on(sock)
            for s in screens:
                try:
                    s.tick(sock, on, a.dry_run)
                except Exception as e:  # keep watching whatever one probe does
                    log(f"{s.name}: probe error {e!r}")
        time.sleep(PROBE_S)


if __name__ == "__main__":
    main()
