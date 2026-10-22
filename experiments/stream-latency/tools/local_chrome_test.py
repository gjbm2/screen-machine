#!/usr/bin/env python3
"""Drive a local Chrome (e.g. Chrome for Testing 136) against a running lab server.

Validates the plumbing end to end before touching the real kiosk: MSE / WebCodecs /
MediaStreamTrackGenerator playback, rVFC timing, barcode read-back, clock sync and
server-side stage attribution.  Numbers from a headless software-GL Chrome are NOT
representative of the N95 + TV; only correctness is being checked here.
A local Chrome shares the server's clock (both on WSL's CLOCK_MONOTONIC), so the skew the clock sync
reports should be ~0 ppm here; anything large points at a sync bug, not at the clock. The real
cross-host skew (~2 % on this WSL host) only shows on the kiosk.

Usage:
  python tools/local_chrome_test.py --chrome /path/to/chrome --server http://127.0.0.1:8091 \
      --modes mse,webcodecs,wctrack --seconds 12 [--headed] [--profile-root DIR]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request

import websockets


def http_json(url: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


async def run_mode(port: int, url: str, seconds: float, server: str) -> dict:
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5))
    page = next(t for t in targets if t["type"] == "page")
    logs: list[str] = []
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=None) as ws:
        mid = 0

        async def call(method, params=None):
            nonlocal mid
            mid += 1
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            return mid

        await call("Runtime.enable")
        await call("Log.enable")
        await call("Page.navigate", {"url": url})
        t_end = time.time() + seconds
        while time.time() < t_end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5))
            except asyncio.TimeoutError:
                continue
            if msg.get("method") == "Runtime.consoleAPICalled":
                args = msg["params"]["args"]
                logs.append(" ".join(str(a.get("value", a.get("description", ""))) for a in args)[:300])
            elif msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                logs.append("EXC " + (d.get("exception", {}).get("description") or d.get("text", ""))[:400])
            elif msg.get("method") == "Log.entryAdded":
                e = msg["params"]["entry"]
                if e.get("level") in ("error", "warning"):
                    logs.append(f"LOG {e['level']}: {e.get('text', '')[:300]}")
    state = http_json(f"{server}/api/state?seconds={max(4, seconds - 5)}")
    return {"logs": logs[-25:], "state": state}


SKEW_WARN_PPM = 200  # local Chrome and server share a clock: a larger fitted skew is a sync bug


def fmt(v, spec: str = "8.2f") -> str:
    return format(v, spec) if isinstance(v, (int, float)) else f"{'-':>{spec.split('.')[0] or 1}}"


def summarize(mode: str, res: dict) -> None:
    print(f"\n=========== mode={mode}")
    for line in res["logs"]:
        print("  console:", line)
    st = res["state"]
    print(f"  server epoch={st.get('epoch')} clock_ppm_vs_raw={st.get('server_clock_ppm_vs_raw')} "
          f"late_source_frames={st.get('late_source_frames')} fb_frames={st.get('fb_frames')}")
    viewers = [c for c in st.get("clients") or [] if c.get("role") == "viewer"]
    viewers = [c for c in viewers if c.get("mode") == mode] or viewers  # earlier modes may still be in the window
    if not viewers:
        print("  NO VIEWER CONNECTED")
        return
    c = viewers[-1]
    g = c.get
    print(f"  client {g('id')} mode={g('mode')} params={json.dumps(g('params') or {})} reconnects={g('reconnects', 0)}")
    print(f"    sent={g('sent')} reported={g('reported')} presented={g('presented')} overdrawn={g('overdrawn')} "
          f"never_presented={g('never_presented')} server_dropped={g('server_dropped')} unreported={g('unreported')} "
          f"presented_ratio={fmt(g('presented_ratio'), '.3f')}")
    print(f"    barcode ok/bad/race={g('barcode_ok')}/{g('barcode_bad')}/{g('barcode_race')} "
          f"items bad/stale={g('items_bad')}/{g('items_stale')} mse={json.dumps(g('mse'))}")
    clk = g("clock") or {}
    print(f"    clock: rtt={clk.get('rtt', g('rtt_ms'))} ppm={clk.get('ppm')} resid={clk.get('resid')} n={clk.get('n')}")
    if isinstance(clk.get("ppm"), (int, float)) and abs(clk["ppm"]) > SKEW_WARN_PPM:
        print(f"  WARNING: fitted skew {clk['ppm']} ppm against a shared local clock -> clock-sync bug")
    caps = (g("info") or {}).get("caps")
    if caps:
        print("  caps:", json.dumps(caps))
    for k, v in (g("stages") or {}).items():
        print(f"    {k:<18} n={v.get('n', 0):<5} p50={fmt(v.get('p50'))}  p95={fmt(v.get('p95'))}  max={fmt(v.get('max'))}")
    for ev in (g("recent_events") or [])[-6:]:
        print("  event:", json.dumps(ev)[:300])
    for p in (x for x in st.get("clients") or [] if x.get("role") == "probe"):
        print(f"  probe {p.get('id')}: clock={json.dumps(p.get('clock'))} items bad/stale={p.get('items_bad')}/"
              f"{p.get('items_stale')} stats={json.dumps((p.get('info') or {}).get('probe_stats'))[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chrome", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8091")
    ap.add_argument("--modes", default="mse,webcodecs,wctrack")
    ap.add_argument("--seconds", type=float, default=12)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--extra", default="", help="extra query string for client.html")
    ap.add_argument("--port", type=int, default=9333, help="DevTools port of the test Chrome")
    ap.add_argument("--profile-root", default=None, help="directory for the throw-away Chrome profile (default: $TMPDIR)")
    args = ap.parse_args()
    if args.profile_root:
        os.makedirs(args.profile_root, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="lab-chrome-", dir=args.profile_root)
    flags = [args.chrome, f"--remote-debugging-port={args.port}", f"--user-data-dir={profile}",
             "--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
             "--window-size=1920,1080", "about:blank"]
    if not args.headed:
        flags.insert(1, "--headless=new")
    proc = subprocess.Popen(flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={**os.environ})
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{args.port}/json/version", timeout=1)
                break
            except Exception:
                time.sleep(0.2)
        for mode in args.modes.split(","):
            url = f"{args.server}/client.html?mode={mode}&barcode=10{('&' + args.extra) if args.extra else ''}"
            res = asyncio.run(run_mode(args.port, url, args.seconds, args.server))
            summarize(mode, res)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    main()
