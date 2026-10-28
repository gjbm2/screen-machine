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


# -- the pinspots: a light on an RF remote (rf_light.py) -------------------------------------------------------------

from room_service.adapters.rf_light import RFLightAdapter, plan  # noqa: E402

LIGHT_BUTTONS = ("on", "off", "white", "colour1", "up", "down", "pulse", "flash")
LCODE = {k: base64.b64encode(bytes([0x26, 1, i])).decode() for i, k in enumerate(LIGHT_BUTTONS)}
ALL = set(LIGHT_BUTTONS)


def light(tmp_path, codes=LCODE, rm=None) -> tuple[RFLightAdapter, FakeRM4]:
    p = tmp_path / "pinspot.json"
    p.write_text(json.dumps({"type": "light", "subsystem": "pinspot", "label": "Pinspots", "steps": 16, "gap_s": 0,
                             "device": {"host": "192.0.2.1"}, "colours": {"white": "White", "colour1": "Red"},
                             "codes": dict(codes)}))
    rm = rm or FakeRM4()
    return RFLightAdapter(Remote(RemoteFile(p), rm), tmp_path), rm


def names(rm: FakeRM4) -> list[str]:
    back = {b(v): k for k, v in LCODE.items()}
    return [back[x] for x in rm.sent]


def presses(p) -> list[tuple[str, int]]:
    return [(btn, n) for btn, n, _ in p[0]]


def test_plan_from_unknown_goes_via_the_floor_and_assumes_no_pulse_or_flash():
    now = dict.fromkeys(("main", "colour", "level", "pulse", "flash"))
    got = plan(now, {"main": "on", "colour": "colour1", "level": 10, "pulse": "on", "flash": "off"}, {}, 16, ALL)
    assert presses(got) == [("on", 1), ("colour1", 1), ("down", 16), ("up", 9), ("pulse", 1)]


def test_plan_known_moves_only_what_differs():
    now = {"main": "on", "colour": "white", "level": 12, "pulse": "on", "flash": "off"}
    assert presses(plan(now, {"level": 9, "pulse": "off"}, {}, 16, ALL)) == [("down", 3), ("pulse", 1)]
    assert presses(plan(now, {"colour": "white", "level": 12}, {}, 16, ALL)) == []
    assert presses(plan(now, {"level": 99}, {}, 16, ALL)) == [("up", 4)]                # clamped to the steps


def test_plan_while_off_keeps_settings_for_the_next_on():
    now = {"main": "off", "colour": "white", "level": 5, "pulse": "off", "flash": "off"}
    p, kept = plan(now, {"colour": "colour1", "level": 5}, {}, 16, ALL)
    assert p == [] and kept == {"colour": "colour1"}
    p, kept = plan(now, {"main": "on"}, kept, 16, ALL)
    assert [(x, n) for x, n, _ in p] == [("on", 1), ("colour1", 1)] and kept == {}


def test_plan_with_one_power_button_toggles():
    now = {"main": "on", "colour": None, "level": None, "pulse": None, "flash": None}
    assert presses(plan(now, {"main": "off"}, {}, 16, ALL - {"on", "off"} | {"toggle"})) == [("toggle", 1)]


def test_light_apply_reads_back_and_survives_a_restart(tmp_path):
    a, rm = light(tmp_path)
    assert a.kind == "level" and not a.dry_run and a.value_schema("main")["x-ui"]["switch"] == {"on": "on", "off": "off"}
    assert a.value_schema("level")["maximum"] == 16 and a.value_schema("colour")["enum"] == ["white", "colour1"]
    out = asyncio.run(a.apply({"main": "on", "colour": "colour1", "level": 2}, {}))
    assert out.status == "ok" and names(rm) == ["on", "colour1"] + ["down"] * 16 + ["up"]
    got = asyncio.run(a.read(["main", "colour", "level", "pulse"]))
    assert {m: r.value for m, r in got.items()} == {"main": "on", "colour": "colour1", "level": 2, "pulse": None}
    rm.sent.clear()
    asyncio.run(a.apply({"main": "off", "colour": "white"}, {}))
    assert names(rm) == ["off"] and asyncio.run(a.read(["colour"]))["colour"].value == "white"   # kept for later
    b2, rm2 = light(tmp_path, rm=FakeRM4())
    assert b2.now["level"] == 2 and b2.kept == {"colour": "white"}
    asyncio.run(b2.apply({"main": "on"}, {}))
    assert names(rm2) == ["on", "white"]


def test_light_reset_and_hand_presses(tmp_path):
    a, rm = light(tmp_path)
    assert asyncio.run(a.reset()).status == "ok"
    assert names(rm) == ["on", "white"] + ["down"] * 16 + ["off"]
    assert a.now == {"main": "off", "colour": "white", "level": 1, "pulse": "off", "flash": "off"}
    asyncio.run(a.signal(None, "press", {"button": "up"}))           # off: it ignores the remote
    assert a.now["level"] == 1
    asyncio.run(a.signal(None, "press", {"button": "on"}))
    asyncio.run(a.signal(None, "press", {"button": "up", "times": 3}))
    asyncio.run(a.signal(None, "press", {"button": "flash"}))
    assert a.now == {"main": "on", "colour": "white", "level": 4, "pulse": "off", "flash": "on"}


def test_light_missing_codes_fail_without_sending(tmp_path):
    a, rm = light(tmp_path, {k: v for k, v in LCODE.items() if k != "pulse"})
    out = asyncio.run(a.apply({"main": "on", "pulse": "on"}, {}))
    assert out.status == "failed" and "pulse" in out.detail and rm.sent == []
    (tmp_path / "none").mkdir()
    assert light(tmp_path / "none", {})[0].dry_run


def test_from_config_reads_every_rm4_file_and_shares_the_rm4(tmp_path, monkeypatch):
    pytest.importorskip("broadlink")
    write(tmp_path, {"toggle": TOG}, SETTINGS)
    light(tmp_path)
    (tmp_path / "blinds.json").write_text(json.dumps({"hub": {"ip": "192.0.2.9"}}))
    monkeypatch.setattr(broadlink_rf, "RF_DIR", tmp_path)
    got = broadlink_rf.from_config(state_dir=tmp_path)
    assert [a.subsystem for a in got] == ["mirror_ball", "mirror_ball_motor", "pinspot"]
    assert got[0].remote.rm is got[2].remote.rm
    rows = {r["id"]: r["group"] for r in got[2].remote.button_rows()}
    assert rows["on"] == "Power" and rows["colour1"] == "Colour"
