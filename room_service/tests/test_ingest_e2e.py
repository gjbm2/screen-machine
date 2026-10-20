"""End to end: `python -m room_service` as a real process on a 303x port with a temp config and
a temp sensor keys file, a fake Flask, a fake relay replaying a lux step (50 -> 2000 lux) and a
held window, and a viewer page socket on a mask_push screen.

Measures reading -> mask.set as the viewer sees it (target p95 < 100 ms) and the server's own
arrival -> enqueue figure, and checks that the held window produces no mask.set and that
Flask's /mask is not fetched while the sensor drives the screen. Production timings are used
(1 s Flask fetch interval, 2 s override poll, 1 s evaluation timer), except the filter's own
constants, which are lightmask's defaults.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from .ingest_helpers import KEY, REPO, SENSOR, FakeRelay, OverrideFlask, Viewer, free_port, pct, write_keys, write_light_config

RESULTS = os.environ.get("INGEST_E2E_RESULTS")      # optional: write the measurements here (JSON)


class PortFlask(OverrideFlask):
    def __init__(self, port: int) -> None:
        super().__init__()
        self.port = port

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/{screen}/mask", self._mask)
        app.router.add_get("/api/{screen}/maskstate", self._maskstate)
        app.router.add_get("/api/{screen}/brightness-override", self._bo)
        app.router.add_get("/ws", self._ws)
        self.http_server = TestServer(app, host="127.0.0.1", port=self.port)
        await self.http_server.start_server()

    async def stop(self) -> None:
        for ws in list(self.ws_clients):
            await ws.close()
        await self.http_server.close()


def write_room_yaml(tmp: Path, port_flask: int, light: dict) -> Path:
    (tmp / "modes.yaml").write_text(
        "default: gallery\nmodes:\n  gallery:\n    label: Gallery\n    state:\n"
        "      screens.devtest: art\n      sensor_mask: follow\n"
        "transitions:\n  - {from: '*', to: '*', steps: [rest]}\n")
    (tmp / "stream.token").write_text("s" * 64 + "\n")
    os.chmod(tmp / "stream.token", 0o600)
    cfg = {
        "stream_service": {"url": "http://127.0.0.1:9", "token_file": str(tmp / "stream.token")},
        "flask": {"url": f"http://127.0.0.1:{port_flask}", "ws": f"ws://127.0.0.1:{port_flask}/ws"},
        "art_page_base": "http://127.0.0.1:1",
        "mask": {"deadband_brightness": 0.005, "deadband_warm_alpha": 0.002, "transition_ms": 200, "backstop_s": 1.0},
        "screens": {"devtest": {"gpu": 0, "live_enabled": False, "mask_push": True}},
        "blinds": {"enabled": False}, "sonos": {"enabled": False},             # never reach the real Bloc hub from a test
        "snapshot": {"enabled": False},
        "service": {"state_dir": str(tmp / "state"), "token_file": str(tmp / "room.token"),
                    "page_keys_file": str(tmp / "page_keys.json"), "modes_file": str(tmp / "modes.yaml"),
                    "destinations_file": str(tmp / "no-destinations.json")},   # screens: only the block above
        "ingest": {"sensors_file": str(tmp / "room_sensors.json"), "filter_file": None,
                   "lightmask": {k: str(v) for k, v in light.items()}},
    }
    p = tmp / "room.yaml"
    p.write_text(json.dumps(cfg))          # JSON is valid YAML
    return p


async def wait_http(url: str, timeout: float, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession() as s:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"room_service exited early ({proc.returncode})")
            try:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=1)) as r:
                    if r.status == 200:
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            await asyncio.sleep(0.2)
    raise AssertionError(f"{url} not up after {timeout} s")


async def status(port: int) -> dict:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/v1/status") as r:
            return await r.json()


def latencies(viewer: Viewer, relay: FakeRelay, t0: float, t1: float, pred) -> list[float]:
    """For each matching mask.set in [t0, t1): its arrival minus the send time of the newest
    reading sent before it (the reading it reflects)."""
    sends = sorted(relay.sent.values())
    out = []
    for at, m in viewer.masks:
        if not (t0 <= at < t1) or not pred(m):
            continue
        before = [s for s in sends if s <= at]
        if before:
            out.append((at - before[-1]) * 1000.0)
    return out


async def test_end_to_end_relay_to_page(tmp_path: Path):
    port_room = free_port()
    port_flask = free_port(skip={port_room})
    light = write_light_config(tmp_path)
    write_keys(tmp_path / "room_sensors.json", {SENSOR: KEY})
    cfg_path = write_room_yaml(tmp_path, port_flask, light)
    flask = PortFlask(port_flask)
    await flask.start()
    log_path = tmp_path / "room_service.log"
    logf = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "room_service", "--port", str(port_room), "--host", "127.0.0.1",
         "--config", str(cfg_path), "--no-snapshots", "--log-level", "INFO"],
        cwd=str(REPO), stdout=logf, stderr=subprocess.STDOUT)
    viewer = relay = None
    results: dict = {"ports": {"room_service": port_room, "flask_fake": port_flask}}
    try:
        await wait_http(f"http://127.0.0.1:{port_room}/healthz", 20, proc)
        base = f"ws://127.0.0.1:{port_room}"
        viewer = await Viewer(base, "devtest").connect()
        relay = await FakeRelay(base + "/v1/ingest/lux").connect()

        # 1. Steady 50 lux at 10 Hz: the sensor takes the screen over from Flask.
        for _ in range(25):
            await relay.reading(50.0)
            await asyncio.sleep(0.1)
        st = await status(port_room)
        assert st["mask"]["tiers"]["devtest"] == "sensor", st["mask"]
        t_sensor = time.monotonic()
        for _ in range(10):
            await relay.reading(50.0)
            await asyncio.sleep(0.1)

        # 2. The step 50 -> 2000 lux (and back, to collect more samples), readings at 10 Hz.
        t_step = time.monotonic()
        first_change = []
        for cycle in range(4):
            for lux, secs in ((2000.0, 2.0), (50.0, 2.5)) if cycle < 3 else ((2000.0, 2.5),):
                t_edge = time.monotonic()
                for _ in range(int(secs * 10)):
                    await relay.reading(lux)
                    await asyncio.sleep(0.1)
                after = [at for at, m in viewer.masks if at >= t_edge]
                assert after, f"no mask.set after the step to {lux} lux"
                first_change.append((min(after) - t_edge) * 1000.0)
        t_end = time.monotonic()
        snap = latencies(viewer, relay, t_step, t_end, lambda m: m["transition_ms"] == 200)
        assert len(snap) >= 7, [m for _, m in viewer.masks]
        results["step"] = {"edges": len(first_change),
                           "first_change_after_edge_ms": [round(x, 1) for x in first_change],
                           "client_ms": {"n": len(snap), "p50": round(pct(snap, 0.5), 2), "p95": round(pct(snap, 0.95), 2),
                                         "max": round(max(snap), 2)}}
        assert pct(snap, 0.95) < 100.0, results
        assert viewer.masks[-1][1]["brightness"] == 1.0           # 2000 lux: top of the curve

        # 3. The held window (a calibration capture): flashes, held -> no mask.set at all.
        t_held = time.monotonic()
        n_held = len(viewer.masks)
        for i in range(10):
            await relay.reading(0.5 if i % 2 else 2000.0, held=True)
            await asyncio.sleep(0.3)
        await asyncio.sleep(0.3)
        held_masks = [m for at, m in viewer.masks[n_held:]]
        results["held"] = {"seconds": round(time.monotonic() - t_held, 2), "mask_sets": len(held_masks)}
        assert held_masks == []

        # 4. Released at the same light: nothing visible changes.
        for _ in range(10):
            await relay.reading(2000.0)
            await asyncio.sleep(0.1)
        assert all(m["brightness"] == 1.0 for _, m in viewer.masks[n_held:])

        st = await status(port_room)
        lat = st["mask"]["latency_ms"]
        results["server_ms"] = lat
        results["flask_mask_fetches_while_sensor_live"] = flask.mask_requests("devtest", since=t_sensor)
        results["override_polls"] = len(flask.override_requests)
        results["acks"] = len(relay.acks)
        results["ingest"] = {k: st["mask"]["ingest"][k] for k in ("hellos", "closes", "rejected", "relay_lag_ms")}
        assert lat["p95"] is not None and lat["p95"] < 50.0, lat
        assert results["flask_mask_fetches_while_sensor_live"] == 0
        assert st["mask"]["ingest"]["hellos"] == 1 and relay.acks
        assert st["screens"]["devtest"]["mask"]["source"] == "sensor"
    finally:
        for c in (relay, viewer):
            if c is not None:
                await c.close()
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        logf.close()
        await flask.stop()
        if RESULTS:
            Path(RESULTS).write_text(json.dumps(results, indent=2))
    assert proc.returncode == 0, log_path.read_text()[-3000:]
    text = log_path.read_text()
    assert KEY not in text
    assert "sensor ingest" not in text or "4401" not in text, "no ingest refusals expected"
    state = json.loads((tmp_path / "state" / "sensors.json").read_text())
    assert state["filters"][SENSOR]["lux"] == pytest.approx(2000.0, rel=0.05)
