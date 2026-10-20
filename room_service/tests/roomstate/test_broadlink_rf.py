"""The RM4 Pro remote adapters (adapters/broadlink_rf.py) against a stand-in RM4: power kind from the codes, settings
(speed, direction) as their own subsystem, settings re-sent after power on, placeholders. Nothing touches the network."""

from __future__ import annotations

import asyncio
import base64
import json
import os

import pytest

from room_service.adapters import broadlink_rf
from room_service.adapters.broadlink_rf import Remote, RemoteFile, RemoteSettingsAdapter, RFError, RFRemoteAdapter
from room_service.roomstate.contract import Placeholder

ON, OFF, TOG, LOW, HIGH, CW = (base64.b64encode(bytes([0x26, 0, i])).decode() for i in (1, 2, 3, 4, 5, 6))
SETTINGS = {"speed": {"label": "Speed", "buttons": {"low": "speed_low", "high": "speed_high"}, "default": "low"},
            "direction": {"label": "Direction", "buttons": {"clockwise": "clockwise"}, "default": "clockwise"}}


class FakeRM4:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[bytes] = []
        self.fail = fail

    async def send(self, packet: bytes) -> None:
        if self.fail:
            raise RFError("the RM4 Pro is locked")
        self.sent.append(packet)


def write(tmp_path, codes: dict, settings: dict | None = None) -> RemoteFile:
    p = tmp_path / "glitterball.json"
    p.write_text(json.dumps({"subsystem": "mirror_ball", "label": "Glitterball", "send_times": 1,
                             "values": {"on": "spinning", "off": "stopped"}, "settings_subsystem": "mirror_ball_motor",
                             "settings": settings or {}, "spacing_s": 0, "device": {"host": "192.0.2.1"},
                             "codes": codes}))
    return RemoteFile(p)


def b(code: str) -> bytes:
    return base64.b64decode(code)


def test_no_codes_is_dry_run_until_one_is_learned(tmp_path):
    pytest.importorskip("broadlink")             # without it from_config() gives the placeholder (tested below)
    f = write(tmp_path, {})
    [a] = broadlink_rf.from_config(f.path)
    assert isinstance(a, RFRemoteAdapter) and not a.placeholder and a.dry_run and a.members == ["main"]
    assert a.normalize("main", "spinning") == "spinning"
    a.remote.rm = FakeRM4()
    assert asyncio.run(a.apply({"main": "spinning"}, {})).status == "failed"
    f.save_code("toggle", b(TOG))
    os.utime(f.path, (2, 2))
    assert not a.dry_run and a.kind == "edge_toggle"         # live, no restart


def test_without_python_broadlink_both_are_placeholders(tmp_path, monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name == "broadlink" else real(name, *a, **k))
    power, motor = broadlink_rf.from_config(write(tmp_path, {"toggle": TOG}, SETTINGS).path)
    assert isinstance(power, Placeholder) and power.value_schema("main") == {"enum": ["spinning", "stopped"]}
    assert isinstance(motor, Placeholder) and motor.subsystem == "mirror_ball_motor"
    assert motor.value_schema("speed") == {"enum": ["low", "high"]}


def test_missing_file_keeps_the_builtin_placeholder(tmp_path):
    assert broadlink_rf.from_config(tmp_path / "nope.json") == []


def test_on_off_is_edge_discrete_and_extra_buttons_are_actions(tmp_path):
    rm = FakeRM4()
    a = RFRemoteAdapter(Remote(write(tmp_path, {"on": ON, "off": OFF, "speed_low": LOW}), rm))
    assert a.kind == "edge_discrete" and a.resend and a.label("main") == "Glitterball"
    assert asyncio.run(a.apply({"main": "spinning"}, {})).status == "ok"
    assert asyncio.run(a.apply({"main": "stopped"}, {})).status == "ok"
    assert rm.sent == [b(ON), b(OFF)]
    assert a.actions["press"]["schema"]["properties"]["button"]["enum"] == ["speed_low"]   # no settings: an action
    assert asyncio.run(a.signal("main", "press", {"button": "speed_low"})).status == "ok"
    assert asyncio.run(a.signal("main", "press", {"button": "nope"})).status == "failed"


def test_power_on_is_power_then_every_setting_and_the_room_reads_them(tmp_path):
    rm = FakeRM4()
    f = write(tmp_path, {"toggle": TOG, "speed_low": LOW, "speed_high": HIGH, "clockwise": CW}, SETTINGS)
    r = Remote(f, rm, tmp_path)
    power, motor = RFRemoteAdapter(r), RemoteSettingsAdapter(r, "mirror_ball_motor")
    assert power.kind == "edge_toggle" and power.actions == {}        # every other button is a setting
    assert motor.kind == "level" and motor.members == ["speed", "direction"] and not motor.dry_run
    assert asyncio.run(motor.read(["speed"]))["speed"].value is None  # never sent: unknown
    # first power on: power, then the defaults, and the room now knows them
    asyncio.run(power.apply({"main": "spinning"}, {}))
    assert rm.sent == [b(TOG), b(LOW), b(CW)]
    assert {m: x.value for m, x in asyncio.run(motor.read(["speed", "direction"])).items()} == \
        {"speed": "low", "direction": "clockwise"}
    rm.sent.clear()
    out = asyncio.run(motor.apply({"speed": "high"}, {}))
    assert out.status == "ok" and rm.sent == [b(HIGH)]
    rm.sent.clear()
    asyncio.run(power.apply({"main": "stopped"}, {}))
    assert rm.sent == [b(TOG)]                                        # off: nothing else
    rm.sent.clear()
    asyncio.run(power.apply({"main": "spinning"}, {}))
    assert rm.sent == [b(TOG), b(HIGH), b(CW)]                        # on again: the last values
    assert Remote(f, rm, tmp_path).sent == {"speed": "high", "direction": "clockwise"}   # survives a restart


def test_a_device_error_is_failed(tmp_path):
    a = RFRemoteAdapter(Remote(write(tmp_path, {"on": ON, "off": OFF}), FakeRM4(fail=True)))
    out = asyncio.run(a.apply({"main": "spinning"}, {}))
    assert out.status == "failed" and "locked" in out.detail


def test_a_relearned_code_is_used_without_a_restart(tmp_path):
    f, rm = write(tmp_path, {"on": ON, "off": OFF}), FakeRM4()
    a = RFRemoteAdapter(Remote(f, rm))
    new = bytes([0x26, 0, 9])
    f.save_code("on", new, {"source": "test"})
    os.utime(f.path, (1, 1))                     # a different mtime even within the same clock tick
    asyncio.run(a.apply({"main": "spinning"}, {}))
    assert rm.sent == [new]
