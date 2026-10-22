#!/usr/bin/env python3
"""Ask a Chrome instance (via its DevTools port) what it is really doing with video.

Run on the mini-PC with the venv that has `websockets` (~/.venv/bin/python):
  cdp_probe.py PORT gpu                 # GPU feature status + decode profiles (list stays empty until chrome://gpu was opened once)
  cdp_probe.py PORT media [SECONDS]     # Media-domain properties of players in the first page:
                                        #   kVideoDecoderName / kIsPlatformVideoDecoder prove HW vs SW decode
  cdp_probe.py PORT eval "<js expr>"    # evaluate in the first page (awaits promises, 10 s timeout)
  cdp_probe.py PORT navigate URL
`gpu` and `media` are read-only; `eval` and `navigate` act on the page. The production kiosk instances
are on 9222/9223 (the lab instance on 9224): eval/navigate refuse those ports unless --allow-production.
"""
import asyncio
import json
import sys
import urllib.request

import websockets

PRODUCTION_PORTS = (9222, 9223)  # north/south kiosk Chromes
CALL_TIMEOUT = 10.0


def targets(port):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3))


def pick_page(port):
    """The first real page: a kiosk Chrome can also hold an about:blank tab, which must not be measured."""
    pages = [t for t in targets(port) if t["type"] == "page"]
    real = [t for t in pages if not t.get("url", "").startswith(("about:", "chrome:"))]
    return (real or pages)[0]


async def session(url):
    return await websockets.connect(url, max_size=None)


async def call(ws, mid, method, params=None):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))

    async def reply():
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("id") == mid:
                return msg
    # never leave a DevTools session hanging on a page (e.g. an eval promise that never settles)
    return await asyncio.wait_for(reply(), CALL_TIMEOUT)


async def gpu(port):
    ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3))
    async with await session(ver["webSocketDebuggerUrl"]) as ws:
        g = (await call(ws, 1, "SystemInfo.getInfo"))["result"]["gpu"]
    print("browser:", ver["Browser"])
    print("gl:", g.get("auxAttributes", {}).get("glRenderer"), "|", g.get("auxAttributes", {}).get("glImplementationParts"))
    fs = g["featureStatus"]
    print("featureStatus:", {k: fs[k] for k in ("gpu_compositing", "video_decode", "video_encode", "vulkan", "webgl2") if k in fs})
    dec = g.get("videoDecoding", [])
    print(f"hardware decode profiles: {len(dec)}")
    for d in dec:
        mx, mn = d.get("maxResolution", {}), d.get("minResolution", {})
        print(f"  {d['profile']:<28} {mn.get('width')}x{mn.get('height')} .. {mx.get('width')}x{mx.get('height')}")
    if not dec:
        print("  (none listed: Chrome fills this list only after chrome://gpu has been opened once, so this proves\n"
              "   nothing; check `media` during MSE/<video> playback or grep VaapiVideoDecoder in the chrome log)")


async def media(port, seconds):
    page = pick_page(port)
    props = {}
    async with await session(page["webSocketDebuggerUrl"]) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Media.enable"}))
        end = asyncio.get_event_loop().time() + seconds
        while asyncio.get_event_loop().time() < end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 0.5))
            except asyncio.TimeoutError:
                continue
            m = msg.get("method", "")
            if m == "Media.playerPropertiesChanged":
                pid = msg["params"]["playerId"]
                for p in msg["params"]["properties"]:
                    props.setdefault(pid, {})[p["name"]] = p["value"]
            elif m == "Media.playerErrorsRaised":
                if msg["params"].get("errors"):
                    print("ERROR", json.dumps(msg["params"])[:400])
            elif m == "Media.playerMessagesLogged":
                for x in msg["params"].get("messages", []):
                    txt = x.get("message", "")
                    # e.g. "Video rendering in low delay mode." (stream classified kLive)
                    if any(k in txt.lower() for k in ("low delay", "decoder", "vaapi", "selected", "fallback")):
                        print(f"log[{x.get('level')}]: {txt[:200]}")
    if not props:
        print("no media players reported (WebCodecs decoders may not appear here; check chrome.log)")
    for pid, p in props.items():
        keys = ("kFrameUrl", "kVideoDecoderName", "kIsPlatformVideoDecoder", "kIsVideoDecryptingDemuxerStream",
                "kResolution", "kVideoTracks", "kFramerate", "kBitrate", "kRendererName", "kIsStreaming")
        print(f"player {pid}:")
        for k in keys:
            if k in p:
                print(f"  {k}: {p[k]}")


async def evaluate(port, expr):
    page = pick_page(port)
    async with await session(page["webSocketDebuggerUrl"]) as ws:
        r = await call(ws, 1, "Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
    print(json.dumps(r.get("result", r), indent=1)[:4000])


async def navigate(port, url):
    page = pick_page(port)
    async with await session(page["webSocketDebuggerUrl"]) as ws:
        print(await call(ws, 1, "Page.navigate", {"url": url}))


def main():
    argv = [a for a in sys.argv[1:] if a != "--allow-production"]
    allow_production = len(argv) != len(sys.argv) - 1
    if len(argv) < 2:
        raise SystemExit(__doc__)
    port, cmd = int(argv[0]), argv[1]
    if cmd in ("eval", "navigate") and port in PRODUCTION_PORTS and not allow_production:
        raise SystemExit(f"refusing: port {port} is a production kiosk Chrome; `{cmd}` would act on the live "
                         "display page (use 9224 for the lab Chrome, or pass --allow-production)")
    if cmd in ("eval", "navigate") and len(argv) < 3:
        raise SystemExit(__doc__)
    try:
        if cmd == "gpu":
            asyncio.run(gpu(port))
        elif cmd == "media":
            asyncio.run(media(port, float(argv[2]) if len(argv) > 2 else 8))
        elif cmd == "eval":
            asyncio.run(evaluate(port, argv[2]))
        elif cmd == "navigate":
            asyncio.run(navigate(port, argv[2]))
        else:
            raise SystemExit(__doc__)
    except (asyncio.TimeoutError, TimeoutError):
        raise SystemExit(f"{cmd}: no DevTools reply within {CALL_TIMEOUT:.0f} s (session closed)")


if __name__ == "__main__":
    main()
