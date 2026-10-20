"""room_in_app (repo root): room_service on its own thread and event loop, as the Flask app runs it.

Starts the real RoomRunner on a free port with a temp config (no stream_service, no Flask, no Chrome,
no blinds, no DMX): it serves status and the page socket, stops cleanly (port and instance lock free),
waits quietly while the port or the state dir's lock is taken and takes over once they are released,
and a broken config backs off without ever raising.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import urllib.request
from pathlib import Path

import aiohttp

import room_in_app
from room_service.util import InstanceLock


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_config(tmp: Path) -> Path:
    (tmp / "modes.yaml").write_text(
        "default: gallery\nmodes:\n  gallery:\n    label: Gallery\n    state:\n      screens: art\n"
        "transitions:\n  - {from: '*', to: '*', steps: [rest]}\n")
    cfg = {
        "stream_service": {"url": "http://127.0.0.1:9", "token_file": str(tmp / "stream.token")},
        "flask": {"url": "http://127.0.0.1:9", "ws": "ws://127.0.0.1:9/"},
        "art_page_base": "http://127.0.0.1:9",
        "screens": {"devtest": {"gpu": 0, "live_enabled": False, "mask_push": False}},
        "blinds": {"enabled": False}, "sonos": {"enabled": False},
        "snapshot": {"enabled": False},
        "ingest": {"enabled": False},
        "service": {"state_dir": str(tmp / "state"), "token_file": str(tmp / "room.token"),
                    "page_keys_file": str(tmp / "page_keys.json"), "modes_file": str(tmp / "modes.yaml")},
    }
    p = tmp / "room.yaml"
    p.write_text(json.dumps(cfg))          # JSON is valid YAML
    return p


def get_json(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as r:
        assert r.status == 200
        return json.loads(r.read())


async def viewer_welcome(port: int) -> dict:
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(f"http://127.0.0.1:{port}/v1/screen/devtest?role=viewer", heartbeat=None) as ws:
            await ws.send_str(json.dumps({"v": 1, "type": "hello", "data": {
                "role": "viewer", "screen": "devtest", "page_load_id": "t-1", "build": "test", "caps": {}, "flags": {}}}))
            while True:
                msg = await ws.receive(timeout=3)
                assert msg.type == aiohttp.WSMsgType.TEXT, msg
                env = json.loads(msg.data)
                if env.get("type") == "welcome":
                    return env["data"]


def runner(tmp: Path, port: int, **kw) -> room_in_app.RoomRunner:
    return room_in_app.RoomRunner(host="127.0.0.1", port=port, config=write_config(tmp), **kw)


def test_serves_and_stops_cleanly(tmp_path: Path) -> None:
    port = free_port()
    r = runner(tmp_path, port)
    r.start()
    try:
        assert r.wait_serving(10), (r.state, r.detail)
        assert r.thread is not None and r.thread.daemon and r.state == "running"
        st = get_json(port, "/api/v1/status")
        assert "devtest" in st["screens"]
        assert asyncio.run(viewer_welcome(port))["screen"] == "devtest"
    finally:
        r.stop(10)
    assert r.state == "stopped" and not r.thread.is_alive()
    with socket.socket() as s:                         # the listener is gone
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
    lock = InstanceLock(tmp_path / "state" / "room.lock")
    lock.acquire()                                     # and so is the instance lock
    lock.release()


def test_waits_for_a_busy_port_then_takes_over(tmp_path: Path) -> None:
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    r = runner(tmp_path, port, retry_busy_s=0.2)
    r.start()
    try:
        deadline = time.monotonic() + 5
        while r.state != "waiting" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert r.state == "waiting" and "in use" in r.detail
        assert not (tmp_path / "state" / "room.lock").exists()   # nothing of room_service started
        holder.close()
        assert r.wait_serving(10), (r.state, r.detail)
        get_json(port, "/healthz")
    finally:
        holder.close()
        r.stop(10)


def test_waits_while_another_instance_holds_the_state_dir(tmp_path: Path) -> None:
    other = InstanceLock(tmp_path / "state" / "room.lock")
    other.acquire()                                    # "the separate room_service is still running"
    r = runner(tmp_path, free_port(), retry_busy_s=0.2)
    r.start()
    try:
        deadline = time.monotonic() + 5
        while r.state != "waiting" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert r.state == "waiting" and "room.lock" in r.detail
        other.release()
        assert r.wait_serving(10), (r.state, r.detail)
    finally:
        other.release()
        r.stop(10)


def test_a_broken_start_backs_off_and_never_raises(tmp_path: Path) -> None:
    crashes: list[str] = []
    r = room_in_app.RoomRunner(host="127.0.0.1", port=free_port(), config=tmp_path / "missing.yaml",
                               backoff_s=(0.1,), on_crash=lambda s, e: crashes.append(s))
    r.start()
    try:
        deadline = time.monotonic() + 5
        while len(crashes) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(crashes) >= 2 and r.thread.is_alive()   # retried, still supervising
        assert r.state == "backoff" and "FileNotFoundError" in r.detail
    finally:
        r.stop(5)
    assert not r.thread.is_alive()
