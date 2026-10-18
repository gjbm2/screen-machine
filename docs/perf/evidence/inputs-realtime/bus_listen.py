"""Passive, read-only bus listener (no writes beyond the hello). 12 s on both buses; logs per message."""
import asyncio, json, time, sys, os
import aiohttp
DUR = float(sys.argv[1]); OUT = sys.argv[2]
recs = {"8703": [], "8701": []}
async def listen(url, key, hello):
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, heartbeat=10.0, max_msg_size=1 << 22) as ws:
            await ws.send_str(json.dumps(hello))
            t_end = time.monotonic() + DUR
            while True:
                left = t_end - time.monotonic()
                if left <= 0: break
                try:
                    msg = await asyncio.wait_for(ws.receive(), left)
                except asyncio.TimeoutError:
                    break
                now = time.monotonic()
                if msg.type != aiohttp.WSMsgType.TEXT: break
                try:
                    o = json.loads(msg.data)
                except ValueError:
                    continue
                d = o.get("data") if isinstance(o.get("data"), dict) else {}
                tm = d.get("t_ms", o.get("t_ms"))
                extra = {}
                if o.get("type") == "music.clock":
                    extra = {k: d.get(k) for k in ("anchor_ms", "bpm", "gate", "state", "conf")}
                if o.get("type") == "music.note":
                    extra = {k: d.get(k) for k in ("pitch", "vel", "on", "seq")}
                recs[key].append((now * 1000.0, o.get("type"), len(msg.data), tm, o.get("seq", d.get("seq")), extra))
async def main():
    pid = os.getpid()
    await asyncio.gather(
        listen("ws://127.0.0.1:8703/v1/show", "8703", {"type": "hello", "v": 1, "role": "observer", "client_id": f"perf-probe-{pid}",
               "want": ["music", "music.rhythm", "room", "room.why", "show"]}),
        listen("ws://127.0.0.1:8701/v1/show", "8701", {"type": "hello", "v": 1, "role": "monitor", "client_id": f"perf-probe-{pid}"}),
        return_exceptions=True)
asyncio.run(main())
json.dump(recs, open(OUT, "w"))
for k, v in recs.items():
    print(k, len(v))
