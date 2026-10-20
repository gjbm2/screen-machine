"""The show bus: `ws://127.0.0.1:8701/v1/show` (LIVE_V1 §1.9, K3 as amended). The wire format is the one
stream_service/showbus.py reads and stream_service/tools/show_synth.py serves; the code there is the spec.

```
client -> {"type":"hello","v":1,"role":"renderer","client_id":"…","token"?:"…"}
server -> {"type":"welcome","schema":1,"rate_hz":40,"stale_after_ms":500,"source":"room_service","fixtures":[…]}
server -> {"v":1,"type":"show.state","seq":…,"t_ms":…,"feed":"live","input":{"kind":"artnet","online":true,
           "age_ms":23,"pps":40.0},"global":{"activity":0.42},"fixtures":{"screen.north":{…},"hue.sofa":{…}}}
```

* **No auth** (owner, 14 Oct: none anywhere in the AV stack). A `hello.token` is ignored; the bus binds 127.0.0.1.
* **Delivery:** a frame per accepted Art-Net packet, on arrival, plus a repeat of the latest every 100 ms when the
  input is quiet (with a fresh `input.age_ms`). `t_ms` is the packet's CLOCK_MONOTONIC arrival time, so a repeat
  keeps it. The `renderer` role is never coalesced: a queue of 64, then disconnect (4008). Other roles get the
  latest frame only.
* **feed:** `live` while Art-Net is fresh (`artnet.stale_after_ms`), else `hold`.
* **Also in room_service's own app** as WS /api/v1/dmx/live (same hello and frames; browsers use role `monitor`,
  which is latest-value only, so a phone can't back-pressure the renderer). With show_bus_port 0 only that route.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

from aiohttp import WSMsgType, web

from .artnet import ArtNetReceiver
from .patch import Patch

log = logging.getLogger("room_service.dmx.bus")

REPEAT_S = 0.1
RENDERER_QUEUE = 64
HELLO_TIMEOUT_S = 5.0


class _Client:
    def __init__(self, ws: web.WebSocketResponse, role: str, client_id: str) -> None:
        self.ws, self.role, self.client_id = ws, role, client_id
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=RENDERER_QUEUE if role == "renderer" else 1)
        self.overflow = False

    def offer(self, data: str) -> None:
        if self.queue.full():
            if self.role == "renderer":
                self.overflow = True
                return
            self.queue.get_nowait()             # latest value only
        self.queue.put_nowait(data)


class ShowBus:
    def __init__(self, patch: Patch, receiver: ArtNetReceiver, *, host: str = "127.0.0.1", port: int = 8701,
                 mode_fn: Callable[[], dict | None] | None = None) -> None:
        self.patch, self.rx = patch, receiver
        self.host, self.port = host, port
        self.mode_fn = mode_fn
        self.clients: set[_Client] = set()
        self.seq = 0
        self.frames_sent = 0
        self.fixtures: dict[str, dict] = {}
        self.globals: dict[str, float] = {}
        self.t_ms: float | None = None
        self._last_sent = 0.0
        self._runner: web.AppRunner | None = None
        self._repeat: asyncio.Task | None = None
        self.bind_error: str | None = None
        receiver.listeners.append(self._on_packet)

    # -- lifecycle -------------------------------------------------------------------------------------------
    async def start(self) -> None:
        """Serve /v1/show on host:port; port 0 serves no port (show_brain owns :8701, SHOW_ENGINE P4), and the bus
        stays in-process for room_service's own WS /api/v1/dmx/live."""
        if self.port:
            app = web.Application(client_max_size=64 << 10)
            app.router.add_get("/v1/show", self._ws)
            self._runner = web.AppRunner(app, access_log=None)
            await self._runner.setup()
            try:
                await web.TCPSite(self._runner, self.host, self.port).start()
                log.info("show bus: ws://%s:%d/v1/show", self.host, self.port)
            except OSError as e:
                self.bind_error = str(e)
                log.error("show bus: can't bind %s:%d: %s", self.host, self.port, e)
        else:
            log.info("show bus: no port (show_bus_port 0); in-app WS /api/v1/dmx/live only")
        self._repeat = asyncio.create_task(self._repeat_loop(), name="show-bus-repeat")

    async def stop(self) -> None:
        if self._repeat is not None:
            self._repeat.cancel()
        for c in list(self.clients):
            await c.ws.close(code=1001, message=b"room_service stopping")
        if self._runner is not None:
            await self._runner.cleanup()

    def set_patch(self, patch: Patch) -> None:
        self.patch = patch
        self._on_packet(self.t_ms or time.monotonic() * 1000.0, fresh=False)

    # -- frames ----------------------------------------------------------------------------------------------
    def _on_packet(self, t_ms: float, fresh: bool = True) -> None:
        self.fixtures, self.globals = self.patch.decode(self.rx.dmx)
        if fresh:
            self.t_ms = t_ms
        self._broadcast()

    def frame(self) -> dict:
        self.seq += 1
        online = self.rx.online
        age = self.rx.age_ms
        f = {"v": 1, "type": "show.state", "seq": self.seq,
             "t_ms": round(self.t_ms if self.t_ms is not None else time.monotonic() * 1000.0, 2),
             "feed": "live" if online else "hold",
             "input": {"kind": "artnet", "online": online, "age_ms": None if age is None else round(age, 1),
                       "pps": self.rx.pps},
             "global": dict(self.globals), "fixtures": self.fixtures}
        if self.mode_fn is not None:
            mode = self.mode_fn()
            if mode:
                f["mode"] = mode
        return f

    def _broadcast(self) -> None:
        self._last_sent = time.monotonic()
        if not self.clients:
            return
        data = json.dumps(self.frame(), separators=(",", ":"))
        self.frames_sent += 1
        for c in list(self.clients):
            c.offer(data)

    async def _repeat_loop(self) -> None:
        while True:
            await asyncio.sleep(REPEAT_S / 2)
            if time.monotonic() - self._last_sent >= REPEAT_S:
                self._broadcast()

    # -- sockets ---------------------------------------------------------------------------------------------
    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10, max_msg_size=64 << 10)
        await ws.prepare(request)
        try:
            msg = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT_S)
            hello = json.loads(msg.data) if msg.type == WSMsgType.TEXT else {}
        except (asyncio.TimeoutError, ValueError):
            await ws.close(code=4400, message=b"hello expected")
            return ws
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            await ws.close(code=4400, message=b"hello expected")
            return ws
        role = str(hello.get("role") or "ui")
        client = _Client(ws, role, str(hello.get("client_id") or "?")[:64])
        await ws.send_json({"type": "welcome", "schema": 1, "rate_hz": 40, "stale_after_ms": 500,
                            "source": "room_service", "fixtures": sorted(self.patch.devices)})
        self.clients.add(client)
        log.info("show bus: %s (%s) connected", client.client_id, role)
        sender = asyncio.create_task(self._send(client))
        try:
            async for _ in ws:
                pass                                # clients only listen
        finally:
            self.clients.discard(client)
            sender.cancel()
            log.info("show bus: %s disconnected", client.client_id)
        return ws

    async def _send(self, c: _Client) -> None:
        try:
            while not c.ws.closed:
                if c.overflow:
                    await c.ws.close(code=4008, message=b"too slow: renderer queue full")
                    return
                data = await c.queue.get()
                await c.ws.send_str(data)
        except (ConnectionResetError, RuntimeError):
            pass

    async def handle(self, request: web.Request) -> web.WebSocketResponse:
        """The bus socket for another app's route (room_service's WS /api/v1/dmx/live): same hello and frames."""
        return await self._ws(request)

    def status(self) -> dict:
        return {"url": f"ws://{self.host}:{self.port}/v1/show" if self.port else None, "bind_error": self.bind_error,
                "frames_sent": self.frames_sent,
                "clients": [{"client_id": c.client_id, "role": c.role, "queued": c.queue.qsize()}
                            for c in self.clients]}
