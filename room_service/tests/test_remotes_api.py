"""Remote-controlled devices are config-driven (remotes_api.py): none until added from a device type in Settings, then
their file, their card in rooms.json and their targets in the room, live; removing takes all three away. Nothing is
sent (the RM4 Pro is never reached: no learn, no send)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from room_service.adapters import broadlink_rf

from .conftest import Harness

REPO_TYPES = Path(broadlink_rf.__file__).resolve().parents[2] / "config" / "room" / "rf" / "types"


@pytest.fixture
def rf_dir(tmp_path, monkeypatch):
    pytest.importorskip("broadlink")
    d = tmp_path / "rf"
    shutil.copytree(REPO_TYPES, d / "types")
    monkeypatch.setattr(broadlink_rf, "RF_DIR", d)
    monkeypatch.setattr(broadlink_rf, "TYPES_DIR", d / "types")
    rooms = tmp_path / "rooms.json"
    rooms.write_text(json.dumps({"drawing-room": {"name": "Drawing room", "screens": ["devtest"]}}))
    return d, rooms


def test_new_device_takes_a_unique_id_from_its_name(rf_dir):
    d, _ = rf_dir
    a = broadlink_rf.new_device("pinspot", "Pinspots", "192.0.2.1")
    b = broadlink_rf.new_device("pinspot", "Pinspots", "192.0.2.1")
    assert (a.stem, b.stem) == ("pinspots", "pinspots_2")
    raw = json.loads(a.read_text())
    assert raw["model"] == "pinspot" and raw["subsystem"] == "pinspots" and raw["type"] == "light"
    assert raw["device"] == {"host": "192.0.2.1"} and raw["codes"] == {} and "type_label" not in raw
    g = json.loads(broadlink_rf.new_device("glitterball", "Ball", "192.0.2.1").read_text())
    assert g["settings_subsystem"] == "ball_settings"
    with pytest.raises(ValueError):
        broadlink_rf.new_device("nope", "x", "192.0.2.1")


async def test_add_and_remove_a_device_live(rf_dir, make_harness):
    d, rooms = rf_dir
    h: Harness = await make_harness(service={"rooms_file": str(rooms)})
    st, v = await h.api("GET", "/api/v1/remotes")
    assert st == 200 and v["remotes"] == [] and {t["id"] for t in v["types"]} >= {"pinspot", "glitterball"}
    st, _ = await h.api("POST", "/api/v1/remotes", {"model": "pinspot", "label": "Pinspots", "room": "drawing-room"})
    assert st == 400                                  # no RM4 Pro known yet: it must be named
    st, dev = await h.api("POST", "/api/v1/remotes", {"model": "pinspot", "label": "Pinspots", "room": "drawing-room",
                                                       "host": "192.0.2.1"})
    assert st == 201 and dev["id"] == "pinspots" and dev["room"] == "drawing-room" and dev["light"]
    assert json.loads(rooms.read_text())["drawing-room"]["effects"] == [
        {"power": "pinspots.main", "settings": [f"pinspots.{m}" for m in ("colour", "level", "pulse", "flash")]}]
    st, desc = await h.api("GET", "/api/v1/room/describe")
    assert "pinspots.colour" in desc["targets"] and "reset" in desc["subsystems"]["pinspots"]["actions"]
    st, out = await h.api("DELETE", "/api/v1/remotes/pinspots")
    assert st == 200 and not (d / "pinspots.json").exists()
    assert json.loads(rooms.read_text())["drawing-room"]["effects"] == []
    st, desc = await h.api("GET", "/api/v1/room/describe")
    assert "pinspots.main" not in desc["targets"]
