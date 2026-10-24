"""Helpers for the sensor-ingest tests: temp light config, temp keys, a Flask fake with the
override endpoints, a fake relay and a timing viewer. Real key files are never touched."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

import lightmask as lm
from lightmask.model import read_intensity_cfg

from room_service.ingest_config import ingest_config_from_dict

from .conftest import Harness
from .fakes import FakeFlask, _serve

REPO = Path(__file__).resolve().parents[2]
SENSOR = "living-room"
KEY = "k" * 40          # test-only key (never a real one)


def write_light_config(tmp: Path, *, devtest_adjust: bool = True) -> dict[str, Path]:
    """Temp copies of Flask's three light inputs. INTENSITY_CFG and the curve are today's
    (read-only from the repo); the destinations are a small fixed set where devtest adjusts."""
    d = tmp / "light"
    d.mkdir(parents=True, exist_ok=True)
    base = read_intensity_cfg(REPO / "config.py")
    (d / "config.py").write_text(f"INTENSITY_CFG = {base!r}\n")
    mapping = json.loads((REPO / "routes" / "data" / "intensity.json").read_text())
    (d / "intensity.json").write_text(json.dumps(mapping))
    icfg = {"lat": 51.5074, "lon": -0.1278, "east_west_bias": 0.25, "adjust": True}
    dests = [
        {"id": "north-screen", "has_bucket": True, "groups": ["drawing-room", "home"], "intensity_cfg": icfg},
        {"id": "south-screen", "has_bucket": True, "groups": ["drawing-room", "home"], "intensity_cfg": icfg},
        {"id": "devtest", "has_bucket": True, "groups": ["drawing-room", "home"],
         "intensity_cfg": ({**icfg} if devtest_adjust else {})},
        {"id": "the-beast", "has_bucket": True, "groups": ["work"], "intensity_cfg": {}},
    ]
    (d / "dests.json").write_text(json.dumps(dests))
    return {"config_py": d / "config.py", "intensity_json": d / "intensity.json",
            "destinations_json": d / "dests.json", "root": d}


def write_keys(path: Path, keys: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys))
    os.chmod(path, 0o600)
    return path


def light_config(paths: dict[str, Path]) -> lm.LightConfig:
    return lm.load_config(lm.ConfigPaths(root=paths["root"], config_py=paths["config_py"],
                                         intensity_json=paths["intensity_json"],
                                         destinations_json=paths["destinations_json"]))


class OverrideFlask(FakeFlask):
    """FakeFlask plus GET /api/<id>/maskstate and /api/<id>/brightness-override."""

    def __init__(self) -> None:
        super().__init__()
        self.mask_enabled: dict[str, bool] = {}
        self.brightness_override: dict[str, float] = {}
        self.override_requests: list[tuple[float, str, str]] = []
        self.override_fail = False

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/{screen}/mask", self._mask)
        app.router.add_get("/api/{screen}/maskstate", self._maskstate)
        app.router.add_get("/api/{screen}/brightness-override", self._bo)
        self.http_server = await _serve(app)
        wapp = web.Application()
        wapp.router.add_get("/", self._ws)
        self.ws_server = await _serve(wapp)

    async def _maskstate(self, request: web.Request) -> web.Response:
        s = request.match_info["screen"]
        self.override_requests.append((time.monotonic(), s, "maskstate"))
        if self.override_fail:
            return web.Response(status=500)
        return web.json_response({"enabled": self.mask_enabled.get(s, True), "destination": s})

    async def _bo(self, request: web.Request) -> web.Response:
        s = request.match_info["screen"]
        self.override_requests.append((time.monotonic(), s, "brightness-override"))
        if self.override_fail:
            return web.Response(status=500)
        o = self.brightness_override.get(s)
        return web.json_response({"destination": s, "override": o, "active": o is not None})

    def mask_requests(self, screen: str, since: float = 0.0) -> int:
        return sum(1 for t, s in self.requests if s == screen and t >= since)


def ingest_raw(tmp: Path, light: dict[str, Path], **over: Any) -> dict:
    """The `ingest:` section for a test (fast timings; temp keys and light config)."""
    raw = {"sensors_file": str(tmp / "room_sensors.json"), "filter_file": None,
           "lightmask": {k: str(v) for k, v in light.items()},
           "overrides_poll_s": 0.2, "eval_interval_s": 0.2, "config_refresh_s": 0.2,
           "hello_timeout_s": 1.0, "ws_heartbeat_s": 0}
    raw.update(over)
    return raw


class IngestHarness(Harness):
    """The standard harness plus an `ingest:` section and the override-capable Flask fake."""

    def __init__(self, tmp: Path, *, ingest: dict | None = None, mask: dict | None = None,
                 keys: dict[str, str] | None = None, devtest_adjust: bool = True) -> None:
        super().__init__(tmp)
        self.flask = OverrideFlask()
        self.light = write_light_config(tmp, devtest_adjust=devtest_adjust)
        self.ingest_over = ingest or {}
        self.mask_over = mask or {}
        if keys is not None:
            write_keys(tmp / "room_sensors.json", keys)

    def config(self) -> Any:
        self.overrides = {**self.overrides, "mask": {**self.overrides.get("mask", {}), **self.mask_over}}
        cfg = super().config()
        raw = {"ingest": ingest_raw(self.tmp, self.light, **self.ingest_over),
               "mask": {"deadband_brightness": 0.005, "deadband_warm_alpha": 0.002, "transition_ms": 200,
                        **self.mask_over}}
        cfg.ingest = ingest_config_from_dict(raw)
        return cfg

    @property
    def ws_base(self) -> str:
        return self.url.replace("http", "ws", 1)


class FakeRelay:
    """What calib-3's RoomSink sends: hello{role, sensor, key}, then ingest.lux readings."""

    def __init__(self, url: str, *, sensor: str = SENSOR, key: str | None = KEY, origin: str | None = None,
                 seq0: int | None = None) -> None:
        self.url = url
        self.sensor = sensor
        self.key = key
        self.origin = origin
        self.seq = seq0 if seq0 is not None else time.time_ns() // 1_000_000
        self.acks: list[tuple[float, int]] = []
        self.sent: dict[int, float] = {}
        self.other: list[dict] = []
        self._http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task | None = None
        self.closed = asyncio.Event()

    async def connect(self, hello: bool = True) -> "FakeRelay":
        self._http = aiohttp.ClientSession()
        headers = {"Origin": self.origin} if self.origin else None
        self.ws = await self._http.ws_connect(self.url, heartbeat=None, headers=headers)
        self._reader = asyncio.get_running_loop().create_task(self._read())
        if hello:
            await self.hello()
        return self

    async def hello(self, **extra: Any) -> None:
        data = {"role": "sensor", "sensor": self.sensor, **extra}
        if self.key is not None and "key" not in extra:
            data["key"] = self.key
        await self.send("hello", data)

    async def send(self, type_: str, data: dict) -> None:
        await self.ws.send_str(json.dumps({"v": 1, "type": type_, "data": data}))

    async def reading(self, lux: Any, *, held: bool | None = None, seq: Any = None, **extra: Any) -> int:
        if seq is None:
            self.seq += 1
            seq = self.seq
        data = {"sensor": self.sensor, "lux": lux, "seq": seq, "mono_ms": time.monotonic_ns() // 1_000_000, **extra}
        if held is not None:
            data["held"] = held
        if isinstance(seq, int):
            self.sent[seq] = time.monotonic()
        await self.ws.send_str(json.dumps({"v": 1, "type": "ingest.lux", "data": data}))
        return seq if isinstance(seq, int) else -1

    async def _read(self) -> None:
        try:
            async for msg in self.ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                env = json.loads(msg.data)
                if env.get("type") == "lux.ack":
                    self.acks.append((time.monotonic(), env["data"]["seq"]))
                else:
                    self.other.append(env)
        finally:
            self.closed.set()

    @property
    def close_code(self) -> int | None:
        return self.ws.close_code if self.ws is not None else None

    async def wait_closed(self, timeout: float = 3.0) -> int | None:
        await asyncio.wait_for(self.closed.wait(), timeout)
        return self.close_code

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self.ws is not None and not self.ws.closed:
            await self.ws.close()
        if self._http is not None:
            await self._http.close()


class Viewer:
    """A display page in role=viewer that timestamps every mask.set on arrival."""

    def __init__(self, url: str, screen: str = "devtest") -> None:
        self.url = f"{url}/v1/screen/{screen}?role=viewer"
        self.screen = screen
        self.masks: list[tuple[float, dict]] = []
        self.welcome: dict | None = None
        self._http: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._tasks: list[asyncio.Task] = []
        self._got = asyncio.Event()

    async def connect(self) -> "Viewer":
        self._http = aiohttp.ClientSession()
        self.ws = await self._http.ws_connect(self.url, heartbeat=None)
        loop = asyncio.get_running_loop()
        self._tasks = [loop.create_task(self._read()), loop.create_task(self._hb())]
        await self.ws.send_str(json.dumps({"v": 1, "type": "hello", "data": {
            "role": "viewer", "screen": self.screen, "page_load_id": f"v-{time.monotonic_ns()}", "build": "test",
            "caps": {}, "flags": {}}}))
        deadline = time.monotonic() + 3
        while self.welcome is None:
            if time.monotonic() > deadline:
                raise AssertionError("no welcome")
            await asyncio.sleep(0.01)
        return self

    async def _hb(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            if self.ws.closed:
                return
            await self.ws.send_str(json.dumps({"v": 1, "type": "hb", "data": {}}))

    async def _read(self) -> None:
        async for msg in self.ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            env = json.loads(msg.data)
            if env["type"] == "welcome":
                self.welcome = env["data"]
            elif env["type"] == "mask.set":
                self.masks.append((time.monotonic(), env["data"]))
                self._got.set()

    def since(self, t: float) -> list[dict]:
        return [m for at, m in self.masks if at >= t]

    async def wait_mask(self, pred=None, timeout: float = 3.0, after: float = 0.0) -> tuple[float, dict]:
        deadline = time.monotonic() + timeout
        while True:
            for at, m in self.masks:
                if at >= after and (pred is None or pred(m)):
                    return at, m
            if time.monotonic() > deadline:
                raise AssertionError(f"no matching mask.set (got {[m for _, m in self.masks][-5:]})")
            self._got.clear()
            try:
                await asyncio.wait_for(self._got.wait(), 0.05)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.ws is not None:
            await self.ws.close()
        if self._http is not None:
            await self._http.close()


def free_port(lo: int = 3031, hi: int = 3039, skip: set[int] | None = None) -> int:
    """A free port in this agent's test range (never 3021/3091/8092/8093/8711)."""
    for p in range(lo, hi + 1):
        if skip and p in skip:
            continue
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
            except OSError:
                continue
            return p
    raise RuntimeError(f"no free port in {lo}-{hi}")


def pct(values: list[float], q: float) -> float:
    import math
    v = sorted(values)
    return v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))]
