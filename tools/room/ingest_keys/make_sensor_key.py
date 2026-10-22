#!/usr/bin/env python3
"""Create or rotate a light sensor's ingest key (LIVE_V1 §2.2b), without showing it.

    make_sensor_key.py <sensor_id> [--url ws://192.168.1.205:8700/v1/ingest/lux]
                       [--keys-file ~/.config/screen-machine/room_sensors.json]
                       [--out ~/.config/screen-machine/light-relay/<sensor_id>.room.json]

1. Writes a fresh 64-hex key for <sensor_id> into room_service's keys file (a JSON object
   {sensor_id: key}, mode 0600, atomic replace). An existing key for that sensor is rotated;
   other sensors' keys are kept. room_service re-reads the file on change (no restart).
2. Writes the relay's config {"url", "key"} (mode 0600) to --out. The owner copies that file
   to the kiosk as ~/.config/light-relay/room.json and sends the relay SIGHUP (README.md).

Prints only paths and the sensor id, never the key. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path

SENSOR_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")      # room_service/ingest.py
DEFAULT_URL = "ws://192.168.1.205:8700/v1/ingest/lux"
DEFAULT_KEYS = "~/.config/screen-machine/room_sensors.json"
DEFAULT_OUT_DIR = "~/.config/screen-machine/light-relay"


def atomic_write_json(path: Path, obj: object) -> None:
    """tmp file in the same directory (0600 from creation), fsync, rename; parent dir 0700."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("sensor_id", help="the relay's sensor name, e.g. living-room (calib-3's SENSOR_NAME)")
    p.add_argument("--url", default=DEFAULT_URL, help=f"room_service ingest URL for the relay (default {DEFAULT_URL})")
    p.add_argument("--keys-file", default=DEFAULT_KEYS, help=f"room_service's sensor keys (default {DEFAULT_KEYS})")
    p.add_argument("--out", help=f"where to write the relay's room.json (default {DEFAULT_OUT_DIR}/<sensor_id>.room.json)")
    a = p.parse_args(argv)

    if not SENSOR_ID_RE.fullmatch(a.sensor_id):
        p.error(f"sensor_id must match {SENSOR_ID_RE.pattern}")
    if not a.url.startswith(("ws://", "wss://")) or not a.url.rstrip("/").endswith("/v1/ingest/lux"):
        p.error("--url must be ws://<host>:<port>/v1/ingest/lux (the relay ignores anything else)")
    keys_path = Path(a.keys_file).expanduser()
    out_path = Path(a.out).expanduser() if a.out else Path(DEFAULT_OUT_DIR).expanduser() / f"{a.sensor_id}.room.json"
    if out_path.resolve() == keys_path.resolve():
        p.error("--out must not be the keys file")

    keys: dict = {}
    if keys_path.exists():
        try:
            keys = json.loads(keys_path.read_text())
        except (OSError, ValueError) as e:
            print(f"error: cannot read {keys_path} ({type(e).__name__}); fix or move it first", file=sys.stderr)
            return 2
        if not isinstance(keys, dict):
            print(f"error: {keys_path} is not a JSON object {{sensor_id: key}}", file=sys.stderr)
            return 2
    action = "rotated" if a.sensor_id in keys else "created"
    key = secrets.token_hex(32)
    keys[a.sensor_id] = key
    # Relay config first: if that fails, room_service still has the old key (and the relay too).
    atomic_write_json(out_path, {"url": a.url, "key": key})
    atomic_write_json(keys_path, keys)
    print(f"sensor {a.sensor_id}: key {action} in {keys_path} (0600; room_service picks it up within ~1 s)")
    print(f"relay config for the kiosk: {out_path} (0600)")
    print("next (owner): copy it to the kiosk's ~/.config/light-relay/room.json and SIGHUP the relay; "
          "see tools/room/ingest_keys/README.md")
    if action == "rotated":
        print("note: the relay keeps the old key until it gets the new room.json; until then room_service "
              "closes its socket with 4401 and its legacy send to :8765 carries on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
