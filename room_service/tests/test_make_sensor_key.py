"""tools/room/ingest_keys/make_sensor_key.py: temp paths only; the key is never printed."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

from room_service.ingest import SensorKeys

from .ingest_helpers import REPO

TOOL = REPO / "tools" / "room" / "ingest_keys" / "make_sensor_key.py"


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(TOOL), *args], capture_output=True, text=True, timeout=30)


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_creates_rotates_and_never_prints_the_key(tmp_path):
    keys, out = tmp_path / "cfg" / "room_sensors.json", tmp_path / "out" / "room.json"
    r = run("living-room", "--keys-file", str(keys), "--out", str(out), "--url", "ws://127.0.0.1:3031/v1/ingest/lux")
    assert r.returncode == 0, r.stderr
    k1 = json.loads(keys.read_text())["living-room"]
    relay = json.loads(out.read_text())
    assert relay == {"url": "ws://127.0.0.1:3031/v1/ingest/lux", "key": k1} and len(k1) == 64
    assert mode(keys) == 0o600 and mode(out) == 0o600 and mode(keys.parent) == 0o700
    assert k1 not in r.stdout + r.stderr and "created" in r.stdout and str(keys) in r.stdout
    # room_service accepts it
    sk = SensorKeys(keys)
    sk.refresh()
    assert sk.check("living-room", relay["key"])
    # a second sensor is added, then the first is rotated; the other key is kept
    assert run("hall", "--keys-file", str(keys), "--out", str(tmp_path / "hall.json")).returncode == 0
    r = run("living-room", "--keys-file", str(keys), "--out", str(out))
    assert r.returncode == 0 and "rotated" in r.stdout
    data = json.loads(keys.read_text())
    assert set(data) == {"living-room", "hall"} and data["living-room"] != k1
    assert json.loads(out.read_text())["key"] == data["living-room"]
    assert json.loads(out.read_text())["url"] == "ws://192.168.1.205:8700/v1/ingest/lux"   # the default
    assert data["living-room"] not in r.stdout + r.stderr
    assert not list(keys.parent.glob(".*.tmp")) and not list(out.parent.glob(".*.tmp"))


def test_rejects_bad_input_and_keeps_a_broken_keys_file(tmp_path):
    keys = tmp_path / "room_sensors.json"
    assert run("bad id!", "--keys-file", str(keys), "--out", str(tmp_path / "o.json")).returncode == 2
    assert run("x", "--url", "http://h/v1/ingest/lux", "--keys-file", str(keys)).returncode == 2
    assert run("x", "--url", "ws://h:1/other", "--keys-file", str(keys)).returncode == 2
    assert not keys.exists()
    keys.write_text("{not json")
    r = run("living-room", "--keys-file", str(keys), "--out", str(tmp_path / "o.json"))
    assert r.returncode == 2 and keys.read_text() == "{not json" and not (tmp_path / "o.json").exists()
    assert run("x", "--keys-file", str(keys), "--out", str(keys)).returncode == 2
