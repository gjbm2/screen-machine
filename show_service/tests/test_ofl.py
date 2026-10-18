"""ofl.py and tools/show/add_fixture.py: OFL modes -> AttrChannels (README S5.2 "OFL -> channels").

Two real Open Fixture Library fixtures (MIT), trimmed to the channels the tests need, are embedded below: an RGBW par
with a dimmer, a fine dimmer, a strobe channel with an Open range, switching channels and a null gap (Flash
Professional LED PAR 64 SLIM 7x10W RGBW Mk2), and a colour-wheel spot (Chauvet DJ Intimidator Spot 110). Synthetic
documents cover the edge cases. Files go to ``tmp_path`` only; nothing is written under config/.

The network test (``SM_OFL_NET=1``) fetches three real fixtures from open-fixture-library.org into ``tmp_path``.
"""

from __future__ import annotations

import copy
import io
import json
import os
import pathlib
import sys
import urllib.error

import pytest

from show_service import contract, ofl

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "show"))
import add_fixture  # noqa: E402

_C = {"type": "ColorIntensity"}

# Flash Professional LED PAR 64 SLIM 7x10W RGBW Mk2 (open-fixture-library.org/flash-professional/
# led-par-64-slim-7x10w-rgbw-mk2, MIT). Trimmed: Color / Macro keeps 3 of its 41 capabilities; 4 of 9 modes.
PAR = {
    "$schema": "https://raw.githubusercontent.com/OpenLightingProject/open-fixture-library/master/schemas/fixture.json",
    "name": "LED PAR 64 SLIM 7x10W RGBW Mk2",
    "categories": ["Color Changer"],
    "availableChannels": {
        "Dimmer": {"fineChannelAliases": ["Dimmer fine"], "capability": {"type": "Intensity"}},
        "Strobe": {"capabilities": [
            {"dmxRange": [0, 10], "type": "ShutterStrobe", "shutterEffect": "Open"},
            {"dmxRange": [11, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe", "speedStart": "1Hz",
             "speedEnd": "20Hz"}]},
        "Red": {"fineChannelAliases": ["Red fine"], "capability": {**_C, "color": "Red"}},
        "Green": {"fineChannelAliases": ["Green fine"], "capability": {**_C, "color": "Green"}},
        "Blue": {"fineChannelAliases": ["Blue fine"], "capability": {**_C, "color": "Blue"}},
        "White": {"fineChannelAliases": ["White fine"], "capability": {**_C, "color": "White"}},
        "Color / Macro": {"defaultValue": 0, "capabilities": [
            {"dmxRange": [0, 5], "type": "NoFunction", "comment": "Open", "switchChannels": {"Speed": "Pulse Speed"}},
            {"dmxRange": [6, 10], "type": "ColorPreset", "comment": "Red", "colors": ["#ff0004"],
             "switchChannels": {"Speed": "Pulse Speed"}},
            {"dmxRange": [11, 255], "type": "Effect", "effectPreset": "ColorFade", "comment": "Macro 9",
             "switchChannels": {"Speed": "Macro Speed"}}]},
        "Pulse Speed": {"capabilities": [
            {"dmxRange": [0, 5], "type": "NoFunction"},
            {"dmxRange": [6, 255], "type": "EffectSpeed", "speedStart": "fast", "speedEnd": "slow"}]},
        "Macro Speed": {"capability": {"type": "EffectSpeed", "speedStart": "fast", "speedEnd": "slow"}},
    },
    "modes": [
        {"name": "Classic 8", "channels": ["Dimmer", "Strobe", "Red", "Green", "Blue", "White", "Color / Macro",
                                           "Speed"]},
        {"name": "Pro 7", "channels": ["Red", "Green", "Blue", "White", "Dimmer", "Dimmer fine", "Strobe"]},
        {"name": "RGBW 16bit", "channels": ["Red", "Red fine", "Green", "Green fine", "Blue", "Blue fine", "White",
                                            "White fine"]},
        {"name": "Flash A-DMX", "channels": ["Red", "Green", "Blue", "White", "Dimmer", None, "Strobe",
                                             "Color / Macro"]},
    ],
}

# Chauvet DJ Intimidator Spot 110 (open-fixture-library.org/chauvet-dj/intimidator-spot-110, MIT). Trimmed: the
# 12-channel mode's last four channels and their definitions are left out; Strobe keeps 3 of its 9 ranges.
SPOT = {
    "name": "Intimidator Spot 110",
    "availableChannels": {
        "Pan": {"fineChannelAliases": ["Pan fine"], "capability": {"type": "Pan", "angleStart": "0deg",
                                                                   "angleEnd": "540deg"}},
        "Tilt": {"fineChannelAliases": ["Tilt fine"], "capability": {"type": "Tilt", "angleStart": "0deg",
                                                                     "angleEnd": "270deg"}},
        "Pan/Tilt Speed": {"capability": {"type": "PanTiltSpeed", "speedStart": "fast", "speedEnd": "slow"}},
        "Color Wheel": {"capabilities": [{"dmxRange": [32 * i, 32 * i + 31], "type": "WheelSlot",
                                          "slotNumber": i + 1} for i in range(8)]},
        "Strobe": {"capabilities": [
            {"dmxRange": [0, 15], "type": "ShutterStrobe", "shutterEffect": "Open"},
            {"dmxRange": [16, 131], "type": "ShutterStrobe", "shutterEffect": "Strobe", "speedStart": "0Hz",
             "speedEnd": "23Hz"},
            {"dmxRange": [132, 255], "type": "ShutterStrobe", "shutterEffect": "Open"}]},
        "Dimmer": {"capability": {"type": "Intensity"}},
        "Gobo Wheel": {"capabilities": [{"dmxRange": [32 * i, 32 * i + 31], "type": "WheelSlot",
                                         "slotNumber": i + 1} for i in range(8)]},
    },
    "wheels": {
        "Color Wheel": {"slots": [{"type": "Open"}] + [
            {"type": "Color", "name": n, "colors": [h]} for n, h in (
                ("Red", "#ff0000"), ("Green", "#00ff00"), ("Blue", "#0000ff"), ("Yellow", "#ffff00"),
                ("Purple", "#ff55ff"), ("Orange", "#ffaa00"), ("Light Blue", "#00aaff"))]},
        "Gobo Wheel": {"slots": [{"type": "Open"}] + [{"type": "Gobo"}] * 7},
    },
    "modes": [
        {"name": "12-channel", "shortName": "12ch",
         "channels": ["Pan", "Pan fine", "Tilt", "Tilt fine", "Pan/Tilt Speed", "Color Wheel", "Strobe", "Dimmer"]},
        {"name": "6-channel", "shortName": "6ch",
         "channels": ["Pan", "Tilt", "Color Wheel", "Strobe", "Dimmer", "Gobo Wheel"]},
    ],
}


def by_name(info) -> dict:
    return {c["name"]: c for c in info["channels"]}


def bare(c: dict) -> dict:
    """A channel without its ranges and resolution (docs/LIGHTS.md §2.2), as Stage 5 read it."""
    return {k: v for k, v in c.items() if k not in ("ranges", "res")}


def doc_with(channels: dict, mode_channels: list, **extra) -> dict:
    return {"name": "Test", "availableChannels": channels, "modes": [{"name": "m", "channels": mode_channels}],
            **extra}


# -- the RGBW par ------------------------------------------------------------------------------------------------------
def test_rgbw_par_with_dimmer_fine_dimmer_and_strobe():
    info, ok = ofl.parse(PAR, "Pro 7", type_id="par", file="ofl/x/par.json")
    assert ok and info["problems"] == []
    assert info["id"] == "par" and info["source"] == "ofl" and info["kind"] == "lamp"
    assert info["file"] == "ofl/x/par.json" and info["mode"] == "Pro 7" and info["footprint"] == 7
    assert info["attrs"] == ["intensity", "colour", "shutter"]                  # the strobe: effects may use it
    ch = by_name(info)
    assert [ch[n]["emitter"] for n in ("Red", "Green", "Blue", "White")] == ["red", "green", "blue", "white"]
    assert [ch[n]["offset"] for n in ("Red", "Green", "Blue", "White")] == [0, 1, 2, 3]
    assert bare(ch["Dimmer"]) == {"offset": 4, "name": "Dimmer", "attr": "intensity", "default": 0, "fine": 5}
    assert ch["Dimmer"]["res"] == 2 and ch["Dimmer"]["ranges"] == [
        {"dmx": [0, 65535], "cap": "Intensity", "attr": "intensity", "lo": 0.0, "hi": 1.0}]
    assert [(r["mode"], r["dmx"], r["lo"], r["hi"]) for r in ch["Strobe"]["ranges"]] == [
        ("open", [0, 10], 0.0, 0.0), ("strobe", [11, 255], 1.0, 20.0)]
    assert ch["Dimmer fine"] == {"offset": 5, "name": "Dimmer fine", "attr": "other", "default": 0}
    assert ch["Strobe"]["attr"] == "shutter" and ch["Strobe"]["default"] == 0      # its Open range starts at 0
    assert "fine" not in ch["Red"]                                                 # the mode doesn't carry Red fine
    assert set(contract.AttrChannel.__annotations__) >= set().union(*(c.keys() for c in info["channels"]))
    assert set(info) == set(contract.TypeInfo.__annotations__)


def test_sixteen_bit_emitters_get_their_fine_offsets():
    info, ok = ofl.parse(PAR, "RGBW 16bit", type_id="par")
    ch = by_name(info)
    assert ok and info["footprint"] == 8
    assert [(ch[n]["offset"], ch[n]["fine"]) for n in ("Red", "Green", "Blue", "White")] == [(0, 1), (2, 3), (4, 5),
                                                                                           (6, 7)]
    assert all(ch[f"{n} fine"]["attr"] == "other" for n in ("Red", "Green", "Blue", "White"))


def test_a_null_is_a_gap_and_switching_channels_hold_their_targets_default():
    info, ok = ofl.parse(PAR, "Flash A-DMX", type_id="par")
    assert ok and info["footprint"] == 8
    assert sorted(c["offset"] for c in info["channels"]) == [0, 1, 2, 3, 4, 6, 7]    # offset 5 is the gap
    c8, _ = ofl.parse(PAR, "Classic 8", type_id="par")
    ch = by_name(c8)
    assert ch["Speed"] == {"offset": 7, "name": "Speed", "attr": "other", "default": 0}   # -> Pulse Speed at 0
    assert ch["Color / Macro"]["attr"] == "other"


def test_switching_channel_follows_its_controllers_default():
    d = copy.deepcopy(PAR)
    d["availableChannels"]["Color / Macro"]["defaultValue"] = 200            # Macro 9 -> Speed is Macro Speed
    d["availableChannels"]["Macro Speed"]["defaultValue"] = 77
    d["availableChannels"]["Pulse Speed"]["defaultValue"] = 3
    assert by_name(ofl.parse(d, "Classic 8")[0])["Speed"]["default"] == 77
    d["availableChannels"]["Color / Macro"]["defaultValue"] = 0
    assert by_name(ofl.parse(d, "Classic 8")[0])["Speed"]["default"] == 3


# -- the colour-wheel spot ---------------------------------------------------------------------------------------------
def test_colour_wheel_spot():
    info, ok = ofl.parse(SPOT, "6ch", type_id="spot")
    assert ok and info["mode"] == "6-channel" and info["footprint"] == 6
    ch = by_name(info)
    assert ch["Color Wheel"]["attr"] == "wheel"
    assert ch["Gobo Wheel"]["attr"] == "gobo"                                    # a wheel of patterns (no colours)
    assert (ch["Pan"]["attr"], ch["Tilt"]["attr"]) == ("pan", "tilt")
    assert ch["Dimmer"]["attr"] == "intensity" and ch["Strobe"]["attr"] == "shutter"
    assert info["attrs"] == ["intensity", "colour", "colour_slot", "shutter", "pan", "tilt", "gobo"]
    assert (ch["Pan"]["default"], ch["Tilt"]["default"]) == (128, 128)          # centre, never 0, 0 (owner, 18 Oct)
    assert info["wheel"][0] == {"dmx": 15, "hex": "#ffffff", "name": "Open"}      # Open counts as white
    assert info["wheel"][1] == {"dmx": 47, "hex": "#ff0000", "name": "Red"}       # the middle of 32..63
    assert [s["dmx"] for s in info["wheel"]] == [15 + 32 * i for i in range(8)]


def test_spot_twelve_channel_pan_tilt_fines_by_short_or_long_name():
    a, _ = ofl.parse(SPOT, "12ch")
    b, _ = ofl.parse(SPOT, "12-channel")
    c, _ = ofl.parse(SPOT, "12CH")                                               # unique ignoring case
    assert a["channels"] == b["channels"] == c["channels"]
    ch = by_name(a)
    assert ch["Pan"]["fine"] == 1 and ch["Tilt"]["fine"] == 3


# -- defaults ----------------------------------------------------------------------------------------------------------
def test_shutter_default_is_the_start_of_its_open_range():
    d = doc_with({"Shutter": {"capabilities": [
        {"dmxRange": [0, 9], "type": "ShutterStrobe", "shutterEffect": "Closed"},
        {"dmxRange": [10, 19], "type": "ShutterStrobe", "shutterEffect": "Open"},
        {"dmxRange": [20, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe"}]}}, ["Shutter"])
    assert bare(by_name(ofl.parse(d, "m")[0])["Shutter"]) == {"offset": 0, "name": "Shutter", "attr": "shutter",
                                                               "default": 10}


def test_a_defaultvalue_wins_over_the_open_rule_and_percentages_scale():
    d = doc_with({"Shutter": {"defaultValue": 30, "capabilities": [
                      {"dmxRange": [0, 9], "type": "ShutterStrobe", "shutterEffect": "Closed"},
                      {"dmxRange": [10, 255], "type": "ShutterStrobe", "shutterEffect": "Open"}]},
                  "Tilt": {"defaultValue": "50%", "fineChannelAliases": ["Tilt fine"], "capability": {"type": "Tilt"}}},
                 ["Shutter", "Tilt", "Tilt fine"])
    ch = by_name(ofl.parse(d, "m")[0])
    assert ch["Shutter"]["default"] == 30
    assert (ch["Tilt"]["default"], ch["Tilt fine"]["default"]) == (128, 0)        # 50% of 16 bit = 0x8000


def test_sixteen_bit_default_splits_msb_first_even_without_the_fine_in_the_mode():
    chans = {"Dimmer": {"defaultValue": 0x1234, "fineChannelAliases": ["Dimmer fine"],
                        "capability": {"type": "Intensity"}}}
    with_fine = by_name(ofl.parse(doc_with(chans, ["Dimmer", "Dimmer fine"]), "m")[0])
    assert (with_fine["Dimmer"]["default"], with_fine["Dimmer fine"]["default"]) == (0x12, 0x34)
    coarse = by_name(ofl.parse(doc_with(chans, ["Dimmer"]), "m")[0])
    assert coarse["Dimmer"]["default"] == 0x12 and "fine" not in coarse["Dimmer"]


def test_eight_bit_resolution_on_a_channel_with_a_fine():
    chans = {"Shutter": {"dmxValueResolution": "8bit", "fineChannelAliases": ["Shutter fine"], "capabilities": [
        {"dmxRange": [0, 4], "type": "ShutterStrobe", "shutterEffect": "Closed"},
        {"dmxRange": [5, 255], "type": "ShutterStrobe", "shutterEffect": "Open"}]}}
    ch = by_name(ofl.parse(doc_with(chans, ["Shutter", "Shutter fine"]), "m")[0])
    assert ch["Shutter"]["default"] == 5 and ch["Shutter fine"]["default"] == 0


def test_a_bad_defaultvalue_is_a_problem_and_falls_back():
    d = doc_with({"Dimmer": {"defaultValue": "lots", "capability": {"type": "Intensity"}},
                  "Red": {"defaultValue": 900, "capability": {**_C, "color": "Red"}}}, ["Dimmer", "Red"])
    info, ok = ofl.parse(d, "m")
    ch = by_name(info)
    assert ok and ch["Dimmer"]["default"] == 0 and ch["Red"]["default"] == 255
    assert any("Dimmer: defaultValue 'lots'" in p for p in info["problems"])
    assert any("Red: defaultValue 900 is out of range: clamped" in p for p in info["problems"])


# -- channels the engine must not drive --------------------------------------------------------------------------------
def test_a_dimmer_strobe_channel_is_not_driven_and_held_at_its_brightest():
    d = doc_with({"Dimmer/Strobe": {"capabilities": [
        {"dmxRange": [0, 9], "type": "NoFunction"},
        {"dmxRange": [10, 127], "type": "Intensity", "brightnessStart": "0%", "brightnessEnd": "100%"},
        {"dmxRange": [128, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe"}]},
        "Red": {"capability": {**_C, "color": "Red"}}}, ["Dimmer/Strobe", "Red"])
    info, ok = ofl.parse(d, "m")
    ch = by_name(info)
    assert ok and ch["Dimmer/Strobe"]["attr"] == "other" and ch["Dimmer/Strobe"]["default"] == 127
    assert info["attrs"] == ["intensity", "colour", "shutter"]                   # the emitter still dims; its strobe
    assert [r["attr"] for r in ch["Dimmer/Strobe"]["ranges"]] == ["intensity", "shutter"]   # range stays usable
    assert any("mixes intensity with other functions: not driven, held at 127" in p for p in info["problems"])


def test_a_backwards_dimmer_and_a_mixed_emitter_are_not_driven():
    d = doc_with({"Dim": {"capability": {"type": "Intensity", "brightnessStart": "bright", "brightnessEnd": "off"}},
                  "White": {"capabilities": [
                      {"dmxRange": [0, 200], "type": "ColorIntensity", "color": "White"},
                      {"dmxRange": [201, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe"}]}},
                 ["Dim", "White"])
    info, ok = ofl.parse(d, "m")
    ch = by_name(info)
    assert ch["Dim"]["attr"] == "other" and ch["Dim"]["default"] == 0           # brightest is its start
    assert ch["White"]["attr"] == "other" and info["attrs"] == ["shutter"]     # its strobe range only
    assert any("runs backwards" in p for p in info["problems"])
    assert any("mixes its white intensity" in p for p in info["problems"])


def test_nofunction_first_then_strobe_is_a_shutter():
    d = doc_with({"Strobe": {"capabilities": [
        {"dmxRange": [0, 10], "type": "NoFunction"},
        {"dmxRange": [11, 255], "type": "ShutterStrobe", "shutterEffect": "Strobe"}]}}, ["Strobe"])
    ch = by_name(ofl.parse(d, "m")[0])
    assert ch["Strobe"]["attr"] == "shutter" and ch["Strobe"]["default"] == 0


def test_emitter_names_and_other_colours():
    names = ("Warm White", "Cold White", "UV", "Amber", "Lime", "Indigo", "Cyan", "Magenta", "Yellow")
    chans = {n: {"capability": {**_C, "color": n}} for n in names}
    chans["Odd"] = {"capability": {**_C, "color": "Chartreuse"}}
    info, _ = ofl.parse(doc_with(chans, list(chans)), "m")
    ch = by_name(info)
    assert [ch[n]["emitter"] for n in names] == ["warm_white", "cold_white", "uv", "amber", "lime", "indigo", "cyan",
                                                 "magenta", "yellow"]
    assert ch["Odd"]["attr"] == "other" and "emitter" not in ch["Odd"]
    assert all(ch[n]["emitter"] in contract.EMITTERS for n in names)


def test_attrs_follow_the_channels():
    dim = {"Dimmer": {"capability": {"type": "Intensity"}}}
    wheel = {"Color": {"capabilities": [{"dmxRange": [0, 127], "type": "WheelSlot", "slotNumber": 1},
                                        {"dmxRange": [128, 255], "type": "WheelSlot", "slotNumber": 2}]}}
    wheels = {"Color": {"slots": [{"type": "Open"}, {"type": "Color", "name": "Red", "colors": ["#ff0000"]}]}}
    assert ofl.parse(doc_with(dim, ["Dimmer"]), "m")[0]["attrs"] == ["intensity"]
    assert ofl.parse(doc_with(wheel, ["Color"], wheels=wheels), "m")[0]["attrs"] == ["colour", "colour_slot"]
    assert ofl.parse(doc_with({**dim, **wheel}, ["Dimmer", "Color"], wheels=wheels), "m")[0]["attrs"] == [
        "intensity", "colour", "colour_slot"]
    assert ofl.parse(doc_with({"Pan": {"capability": {"type": "Pan"}}}, ["Pan"]), "m")[0]["attrs"] == ["pan"]


def test_wheels_skip_splits_and_mix_multicolour_slots_and_drive_only_the_first():
    caps = [{"dmxRange": [0, 9], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [10, 19], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [20, 29], "type": "WheelSlot", "slotNumberStart": 1, "slotNumberEnd": 2},
            {"dmxRange": [30, 39], "type": "WheelSlot", "slotNumber": 2.5},
            {"dmxRange": [40, 49], "type": "WheelSlot", "slotNumber": 3},
            {"dmxRange": [50, 255], "type": "WheelRotation"}]
    wheels = {"CW": {"slots": [{"type": "Color", "name": "Red", "colors": ["#ff0000"]},
                               {"type": "Color", "colors": ["#ff0000", "#0000ff"]},
                               {"type": "Closed"}]},
              "CW2": {"slots": [{"type": "Color", "name": "Blue", "colors": ["#0000ff"]}]}}
    d = doc_with({"CW": {"capabilities": caps},
                  "CW2": {"capability": {"type": "WheelSlot", "slotNumber": 1}}}, ["CW", "CW2"], wheels=wheels)
    info, ok = ofl.parse(d, "m")
    ch = by_name(info)
    assert info["wheel"] == [{"dmx": 4, "hex": "#ff0000", "name": "Red"}, {"dmx": 14, "hex": "#800080",
                                                                            "name": "#800080"}]
    assert ch["CW"]["attr"] == "wheel" and ch["CW2"]["attr"] == "other"
    assert any("a second colour wheel" in p for p in info["problems"])


# -- ranges (docs/LIGHTS.md §2.2) --------------------------------------------------------------------------------------
def ranges_of(info, name: str, attr: str | None = None) -> list[tuple]:
    """(dmx lo, dmx hi, lo, hi) per range of a channel (of one attr)."""
    return [(r["dmx"][0], r["dmx"][1], r["lo"], r["hi"]) for r in by_name(info)[name]["ranges"]
            if attr is None or r["attr"] == attr]


def test_pan_and_tilt_are_centred_degrees_with_the_physical_range():
    info, _ = ofl.parse(SPOT, "12ch")
    assert ranges_of(info, "Pan") == [(0, 65535, -270.0, 270.0)] and ranges_of(info, "Tilt") == [(0, 65535, -135.0,
                                                                                                   135.0)]
    assert by_name(info)["Pan"]["res"] == 2 and by_name(info)["Pan"]["fine"] == 1
    assert info["physical"] == {"lens_deg": None, "pan_deg": 540.0, "tilt_deg": 270.0, "zoom_deg": None}
    ch = by_name(info)
    assert (ch["Pan"]["default"], ch["Pan fine"]["default"]) == (0x80, 0x00)     # centre: 0x8000 of 16 bits
    assert info["attrs"] == ["intensity", "colour", "colour_slot", "shutter", "pan", "tilt", "pt_speed"]
    assert ch["Pan/Tilt Speed"]["attr"] == "pt_speed" and ranges_of(info, "Pan/Tilt Speed") == [(0, 255, 1.0, 0.01)]


def test_a_percent_pan_assumes_540_with_a_problem_line_unless_the_file_says():
    d = doc_with({"Pan": {"capability": {"type": "Pan", "angleStart": "0%", "angleEnd": "100%"}},
                  "Tilt": {"capability": {"type": "Tilt"}}}, ["Pan", "Tilt"])
    info, _ = ofl.parse(d, "m")
    assert ranges_of(info, "Pan") == [(0, 255, -270.0, 270.0)] and ranges_of(info, "Tilt") == [(0, 255, -135.0,
                                                                                                 135.0)]
    assert "Pan: pan range not in the file: 540° assumed (set it on the Lights page)" in info["problems"]
    assert info["physical"]["pan_deg"] is None and info["physical"]["tilt_deg"] is None
    d["physical"] = {"focus": {"type": "Head", "panMax": 630, "tiltMax": 200}}
    info, _ = ofl.parse(d, "m")
    assert ranges_of(info, "Pan") == [(0, 255, -315.0, 315.0)] and info["physical"]["pan_deg"] == 630.0
    assert not [p for p in info["problems"] if "range not in the file" in p]
    two = doc_with({"Pan": {"capabilities": [
        {"dmxRange": [0, 127], "type": "Pan", "angleStart": "0deg", "angleEnd": "270deg"},
        {"dmxRange": [128, 255], "type": "Pan", "angleStart": "270deg", "angleEnd": "540deg"}]}}, ["Pan"])
    assert ranges_of(ofl.parse(two, "m")[0], "Pan") == [(0, 127, -270.0, 0.0), (128, 255, 0.0, 270.0)]


def test_rotation_speeds_are_signed_shares_and_hz_divide_by_the_fastest():
    d = doc_with({"Spin": {"capabilities": [
        {"dmxRange": [0, 9], "type": "PanContinuous", "speed": "stop"},
        {"dmxRange": [10, 127], "type": "PanContinuous", "speedStart": "slow CW", "speedEnd": "fast CW"},
        {"dmxRange": [128, 255], "type": "PanContinuous", "speedStart": "-10rpm", "speedEnd": "-60rpm"}]},
        "Tilt spin": {"capability": {"type": "TiltContinuous", "speedStart": "-100%", "speedEnd": "100%"}}},
        ["Spin", "Tilt spin"])
    info, _ = ofl.parse(d, "m")
    assert ranges_of(info, "Spin") == [(0, 9, 0.0, 0.0), (10, 127, 0.01, 1.0), (128, 255, round(-1 / 6, 6), -1.0)]
    assert ranges_of(info, "Tilt spin") == [(0, 255, -1.0, 1.0)]
    assert by_name(info)["Spin"]["attr"] == "pan_spin" and "tilt_spin" in info["attrs"]


def test_strobes_keep_hz_modes_and_keyword_speeds_are_a_starting_point():
    d = doc_with({"Shutter": {"capabilities": [
        {"dmxRange": [0, 9], "type": "ShutterStrobe", "shutterEffect": "Closed"},
        {"dmxRange": [10, 19], "type": "ShutterStrobe", "shutterEffect": "Open"},
        {"dmxRange": [20, 99], "type": "ShutterStrobe", "shutterEffect": "Pulse", "speedStart": "60bpm",
         "speedEnd": "600bpm"},
        {"dmxRange": [100, 149], "type": "ShutterStrobe", "shutterEffect": "Strobe", "randomTiming": True,
         "speedStart": "slow", "speedEnd": "fast"},
        {"dmxRange": [150, 199], "type": "ShutterStrobe", "shutterEffect": "Lightning"},
        {"dmxRange": [200, 229], "type": "ShutterStrobe", "shutterEffect": "RampUpDown"},
        {"dmxRange": [230, 255], "type": "ShutterStrobe", "shutterEffect": "Burst"}]},
        "Strobe speed": {"capability": {"type": "StrobeSpeed", "speedStart": "1Hz", "speedEnd": "25Hz"}}},
        ["Shutter", "Strobe speed"])
    info, _ = ofl.parse(d, "m")
    rs = by_name(info)["Shutter"]["ranges"]
    assert [r["mode"] for r in rs] == ["closed", "open", "pulse", "random", "random", "ramp", "burst"]
    assert (rs[2]["lo"], rs[2]["hi"]) == (1.0, 10.0)                              # bpm -> Hz
    lo, hi = ofl.STROBE_HZ_UNKNOWN
    assert rs[3]["lo"] == pytest.approx(lo + ofl.SLOW * (hi - lo)) and rs[3]["hi"] == hi
    assert any("strobe speed not in Hz in the file" in p for p in info["problems"])
    assert by_name(info)["Strobe speed"]["attr"] == "shutter" and ranges_of(info, "Strobe speed") == [(0, 255, 1.0,
                                                                                                         25.0)]
    assert by_name(info)["Strobe speed"]["ranges"][0]["mode"] == "strobe"


def test_beam_focus_iris_frost_and_colour_temperature():
    chans = {"Zoom": {"capability": {"type": "Zoom", "angleStart": "narrow", "angleEnd": "wide"}},
             "Focus": {"capability": {"type": "Focus", "distanceStart": "near", "distanceEnd": "far"}},
             "Focus m": {"capability": {"type": "Focus", "distanceStart": "2m", "distanceEnd": "12m"}},
             "Iris": {"capability": {"type": "Iris", "openPercentStart": "closed", "openPercentEnd": "open"}},
             "Frost": {"capability": {"type": "Frost", "frostIntensityStart": "0%", "frostIntensityEnd": "100%"}},
             "CTO": {"capability": {"type": "ColorTemperature", "colorTemperatureStart": "6500K",
                                    "colorTemperatureEnd": "3200K"}},
             "Beam": {"capability": {"type": "BeamAngle", "angleStart": "8deg", "angleEnd": "30deg"}}}
    names = list(chans)
    info, _ = ofl.parse(doc_with(chans, names, physical={"lens": {"degreesMinMax": [5, 50]}}), "m")
    assert ranges_of(info, "Zoom") == [(0, 255, 5.0, 50.0)] and ranges_of(info, "Beam") == [(0, 255, 8.0, 30.0)]
    assert info["physical"]["lens_deg"] == [5.0, 50.0] and info["physical"]["zoom_deg"] == [5.0, 50.0]
    assert ranges_of(info, "Focus") == [(0, 255, 0.0, 1.0)] and ranges_of(info, "Focus m") == [(0, 255, 0.0, 1.0)]
    assert ranges_of(info, "Iris") == [(0, 255, 0.0, 1.0)] and ranges_of(info, "Frost") == [(0, 255, 0.0, 1.0)]
    assert ranges_of(info, "CTO") == [(0, 255, 6500.0, 3200.0)]
    assert {"zoom", "focus", "iris", "frost", "cct"} <= set(info["attrs"]) and info["problems"] == []
    assert [by_name(info)[n]["attr"] for n in names] == ["zoom", "focus", "focus", "iris", "frost", "cct", "zoom"]
    guess, _ = ofl.parse(doc_with({"Zoom": chans["Zoom"], "CT": {"capability": {
        "type": "ColorTemperature", "colorTemperatureStart": "warm", "colorTemperatureEnd": "cold"}}},
        ["Zoom", "CT"]), "m")
    assert ranges_of(guess, "Zoom") == [(0, 255, *ofl.ZOOM_UNKNOWN_DEG)] and guess["physical"]["zoom_deg"] is None
    assert ranges_of(guess, "CT") == [(0, 255, ofl.CCT_WARM_K, ofl.CCT_COLD_K)]
    assert any("beam angle not in the file" in p for p in guess["problems"])
    assert any("colour temperature not in kelvin" in p for p in guess["problems"])


GOBOS = {
    "name": "Gobo spot",
    "availableChannels": {
        "Colour": {"capabilities": [
            {"dmxRange": [0, 63], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [64, 127], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [128, 191], "type": "WheelRotation", "speedStart": "slow CW", "speedEnd": "fast CW"},
            {"dmxRange": [192, 255], "type": "WheelRotation", "speedStart": "slow CCW", "speedEnd": "fast CCW"}]},
        "Gobo 1": {"capabilities": [
            {"dmxRange": [0, 15], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [16, 31], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [32, 47], "type": "WheelSlot", "slotNumber": 3},
            {"dmxRange": [48, 63], "type": "WheelSlot", "slotNumberStart": 2.5, "slotNumberEnd": 2.5},
            {"dmxRange": [64, 95], "type": "WheelShake", "slotNumber": 2, "shakeSpeedStart": "slow",
             "shakeSpeedEnd": "fast"},
            {"dmxRange": [96, 127], "type": "WheelShake", "slotNumber": 3, "shakeSpeedStart": "slow",
             "shakeSpeedEnd": "fast"},
            {"dmxRange": [128, 191], "type": "WheelRotation", "speedStart": "slow CW", "speedEnd": "fast CW"},
            {"dmxRange": [192, 255], "type": "WheelRotation", "speedStart": "slow CCW", "speedEnd": "fast CCW"}]},
        "Gobo 1 rotation": {"capabilities": [
            {"dmxRange": [0, 127], "type": "WheelSlotRotation", "wheel": "Gobo 1", "angleStart": "0deg",
             "angleEnd": "360deg", "comment": "Index"},
            {"dmxRange": [128, 255], "type": "WheelSlotRotation", "wheel": "Gobo 1", "speedStart": "fast CCW",
             "speedEnd": "fast CW"}]},
        "Gobo 2": {"capabilities": [{"dmxRange": [32 * i, 32 * i + 31], "type": "WheelSlot", "slotNumber": i + 1}
                                    for i in range(8)]},
        "Prisms": {"capabilities": [
            {"dmxRange": [0, 63], "type": "WheelSlot", "slotNumber": 1},
            {"dmxRange": [64, 127], "type": "WheelSlot", "slotNumber": 2},
            {"dmxRange": [128, 191], "type": "WheelSlot", "slotNumber": 3},
            {"dmxRange": [192, 255], "type": "WheelSlot", "slotNumber": 4}]},
        "Prism turn": {"capability": {"type": "PrismRotation", "speedStart": "slow CW", "speedEnd": "fast CW"}},
        "Macros": {"capabilities": [
            {"dmxRange": [0, 9], "type": "NoFunction"},
            {"dmxRange": [10, 19], "type": "Maintenance", "comment": "Reset"},
            {"dmxRange": [20, 255], "type": "Effect", "effectName": "Auto program"}]},
        "Move time": {"capability": {"type": "PanTiltSpeed", "durationStart": "0s", "durationEnd": "4s"}},
    },
    "wheels": {
        "Colour": {"slots": [{"type": "Open"}, {"type": "Color", "name": "Deep red", "colors": ["#cc0000"]}]},
        "Gobo 1": {"slots": [{"type": "Open"}, {"type": "Gobo", "name": "Stars"}, {"type": "Gobo"}]},
        "Gobo 2": {"slots": [{"type": "Open"}] + [{"type": "Gobo"}] * 7},
        "Prisms": {"slots": [{"type": "Open"}, {"type": "Prism", "facets": 3}, {"type": "Frost"},
                             {"type": "Prism", "name": "Linear", "facets": 5}]},
    },
    "modes": [{"name": "all", "channels": ["Colour", "Gobo 1", "Gobo 1 rotation", "Gobo 2", "Prisms", "Prism turn",
                                           "Macros", "Move time"]}],
}


def test_wheels_gobos_shake_scroll_spin_and_a_prism_wheel():
    info, ok = ofl.parse(GOBOS, "all")
    assert ok and info["problems"] == []
    w = info["wheels"]
    assert {k: (v["kind"], v["attr"]) for k, v in w.items()} == {
        "Colour": ("colour", "colour_slot"), "Gobo 1": ("gobo", "gobo"), "Gobo 2": ("gobo", "gobo2"),
        "Prisms": ("prism", "prism")}
    assert [s["name"] for s in w["Gobo 1"]["slots"]] == ["Open", "Stars", "Pattern 3"]
    assert w["Prisms"]["slots"][1] == {"slot": 2, "type": "Prism", "name": "Prism 3", "facets": 3}
    g1 = by_name(info)["Gobo 1"]["ranges"]
    assert [(r["attr"], r.get("slot")) for r in g1] == [
        ("gobo", 1), ("gobo", 2), ("gobo", 3), ("gobo_shake", 2), ("gobo_shake", 3), ("gobo_scroll", None),
        ("gobo_scroll", None)]                                                     # the split (2.5) is left out
    assert (g1[3]["lo"], g1[3]["hi"]) == (ofl.SLOW, 1.0) and g1[1]["word"] == "Stars"
    assert ranges_of(info, "Colour", "colour_spin") == [(128, 191, 0.01, 1.0), (192, 255, -0.01, -1.0)]
    assert ranges_of(info, "Gobo 1 rotation", "gobo_spin") == [(128, 255, -1.0, 1.0)]
    assert ranges_of(info, "Gobo 1 rotation", "macro") == [(0, 127, 0.0, 0.0)]  # an index angle: the page's More
    assert [r["attr"] for r in by_name(info)["Gobo 2"]["ranges"]] == ["gobo2"] * 8
    pr = by_name(info)["Prisms"]["ranges"]
    assert [(r["attr"], r["lo"]) for r in pr] == [("prism", 0.0), ("prism", 1.0), ("frost", 1.0), ("prism", 2.0)]
    assert ranges_of(info, "Prism turn") == [(0, 255, 0.01, 1.0)] and by_name(info)["Prism turn"]["attr"] == \
        "prism_spin"
    assert by_name(info)["Macros"]["attr"] == "other"
    assert [(r["attr"], r["word"]) for r in by_name(info)["Macros"]["ranges"]] == [("macro", "Reset"),
                                                                                    ("macro", "Auto program")]
    assert ranges_of(info, "Move time") == [(0, 255, 1.0, 0.0)]                   # 0 s = the fastest
    assert info["attrs"] == ["colour", "colour_slot", "colour_spin", "pt_speed", "frost", "gobo", "gobo2",
                             "gobo_spin", "gobo_shake", "gobo_scroll", "prism", "prism_spin", "macro"]
    json.dumps(info)


# -- modes, matrices and bad files -------------------------------------------------------------------------------------
def test_a_bad_mode_is_not_usable_and_lists_the_modes():
    info, ok = ofl.parse(SPOT, "9ch")
    assert not ok and info["channels"] == [] and info["footprint"] == 0
    assert "isn't in the file" in info["problems"][0] and "'6-channel' (6ch)" in info["problems"][0]


def test_matrix_modes_are_inert_and_a_matrix_fixtures_plain_mode_works():
    d = {"name": "Bar", "matrix": {"pixelCount": [4, 1, 1]},
         "availableChannels": {"Dimmer": {"capability": {"type": "Intensity"}}},
         "templateChannels": {"Red $pixelKey": {"capability": {**_C, "color": "Red"}}},
         "modes": [{"name": "1ch", "channels": ["Dimmer"]},
                   {"name": "pixels", "channels": ["Dimmer", {"insert": "matrixChannels", "repeatFor": "eachPixelABC",
                                                              "channelOrder": "perPixel",
                                                              "templateChannels": ["Red $pixelKey"]}]}]}
    info, ok = ofl.parse(d, "pixels")
    assert ok and info["footprint"] == 0 and info["channels"] == [] and info["attrs"] == []
    assert "matrix" in info["problems"][0]
    plain, ok2 = ofl.parse(d, "1ch")
    assert ok2 and plain["attrs"] == ["intensity"] and plain["problems"] == []
    assert [m["matrix"] for m in ofl.modes(d)] == [False, True]


def test_an_unknown_channel_name_sends_zero_with_a_problem():
    info, ok = ofl.parse(doc_with({"Red": {"capability": {**_C, "color": "Red"}}}, ["Red", "Nope"]), "m")
    assert ok and info["footprint"] == 2 and [c["name"] for c in info["channels"]] == ["Red"]
    assert any("'Nope' isn't defined" in p for p in info["problems"])


def test_load_reads_files_and_never_raises(tmp_path):
    good = tmp_path / "par.json"
    good.write_text(json.dumps(PAR))
    info = ofl.load_ofl(good, "Pro 7")
    assert info["id"] == "par" and info["footprint"] == 7
    for name, text, why in (("missing.json", None, "file not found"), ("bad.json", "{nope", "not JSON"),
                            ("nomodes.json", json.dumps({"name": "x"}), "no modes list")):
        p = tmp_path / name
        if text is not None:
            p.write_text(text)
        t, ok = ofl.load(p, "m")
        assert not ok and why in t["problems"][0]
    assert ofl.parse(["not", "a", "doc"], "m")[1] is False


# -- tools/show/add_fixture.py -----------------------------------------------------------------------------------------
def fake_fetch(docs: dict):
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        key = url[len(ofl.OFL_URL) + 1:-len(".json")]
        if key not in docs:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return json.dumps(docs[key]).encode()
    fetch.calls = calls
    return fetch


def run(argv, fetch=None):
    out = io.StringIO()
    code = add_fixture.main(argv, fetch=fetch or fake_fetch({}), out=out)
    return code, out.getvalue()


def test_add_fixture_saves_lists_modes_and_prints_the_types_entry(tmp_path):
    fetch = fake_fetch({"chauvet-dj/intimidator-spot-110": SPOT})
    code, text = run(["chauvet-dj/intimidator-spot-110", "--dest", str(tmp_path)], fetch)
    saved = tmp_path / "ofl" / "chauvet-dj" / "intimidator-spot-110.json"
    assert code == 0 and saved.exists() and json.loads(saved.read_text()) == SPOT
    assert fetch.calls == [f"{ofl.OFL_URL}/chauvet-dj/intimidator-spot-110.json"]
    assert "'12-channel' (12ch): 8 channels" in text and "'6-channel' (6ch): 6 channels" in text
    assert "colour wheel: Open #ffffff @15" in text
    assert "file: ofl/chauvet-dj/intimidator-spot-110.json" in text and 'mode: "12ch"' in text
    info = ofl.load_ofl(saved, "6ch")                                         # what it saved loads as it listed
    assert info["footprint"] == 6
    code2, text2 = run(["chauvet-dj/intimidator-spot-110", "--dest", str(tmp_path), "--mode", "6ch"], fetch)
    assert code2 == 0 and "already up to date" in text2 and "mode '6-channel': 6 channels" in text2
    assert "mode '12-channel'" not in text2


def test_add_fixture_keeps_a_differing_copy_unless_forced(tmp_path):
    saved = tmp_path / "ofl" / "flash-professional" / "par.json"
    saved.parent.mkdir(parents=True)
    saved.write_text("{}")
    fetch = fake_fetch({"flash-professional/par": PAR})
    code, text = run(["flash-professional/par", "--dest", str(tmp_path)], fetch)
    assert code == 4 and "--force" in text and saved.read_text() == "{}"
    code, _ = run(["flash-professional/par", "--dest", str(tmp_path), "--force"], fetch)
    assert code == 0 and json.loads(saved.read_text()) == PAR


def test_add_fixture_errors(tmp_path):
    assert run(["nobody/none", "--dest", str(tmp_path)])[0] == 3
    assert "HTTP 404" in run(["nobody/none", "--dest", str(tmp_path)])[1]
    assert run(["Not A Key", "--dest", str(tmp_path)])[0] == 2
    assert run(["a/b/c", "--dest", str(tmp_path)])[0] == 2
    assert run([])[0] == 2
    bad = fake_fetch({})

    def junk(url):
        return b"<html>"
    assert run(["x/y", "--dest", str(tmp_path)], junk)[0] == 3
    assert list(tmp_path.iterdir()) == [] and bad.calls == []
    assert add_fixture.parse_key(f"{ofl.OFL_URL}/generic/rgbw-fader") == ("generic", "rgbw-fader")
    assert add_fixture.parse_key("generic/rgbw-fader.json") == ("generic", "rgbw-fader")


def test_add_fixture_lists_a_local_file_and_writes_nothing(tmp_path):
    p = tmp_path / "par.json"
    p.write_text(json.dumps(PAR))
    code, text = run(["--file", str(p), "--mode", "Pro 7"])
    assert code == 0 and "mode 'Pro 7': 7 channels, drives intensity, colour" in text
    assert "Dimmer fine" in text and "fine on channel 6" in text
    assert sorted(x.name for x in tmp_path.iterdir()) == ["par.json"]
    assert add_fixture.main.__kwdefaults__["fetch"] is add_fixture.fetch_url
    assert pathlib.Path(add_fixture.contract.FIXTURE_TYPES_DIR) == contract.FIXTURE_TYPES_DIR


@pytest.mark.skipif(os.environ.get("SM_OFL_NET") != "1", reason="network: set SM_OFL_NET=1 to fetch real OFL files")
def test_real_ofl_fixtures_fetched_into_tmp(tmp_path):
    """Three real fixtures from open-fixture-library.org into tmp_path, then through the registry."""
    from show_service import fixtures as fx
    keys = {"par": ("flash-professional/led-par-64-slim-7x10w-rgbw-mk2", "Pro 7"),
            "spot": ("chauvet-dj/intimidator-spot-110", "6ch"),
            "fader": ("generic/rgbw-fader", "8bit")}
    for maker_model, _ in keys.values():
        code, text = run([maker_model, "--dest", str(tmp_path / "types")], add_fixture.fetch_url)
        assert code == 0, text
    types = "\n".join(f"  {t}: {{source: ofl, file: ofl/{mm}.json, mode: \"{m}\"}}" for t, (mm, m) in keys.items())
    (tmp_path / "fixtures.yaml").write_text(
        "version: 1\noutputs:\n  dmx: {driver: artnet, node: n1, port_address: 1}\n"
        "nodes:\n  n1: {virtual: true}\n"
        f"types:\n{types}\n"
        "fixtures:\n  a: {type: par, output: dmx, address: 1}\n  b: {type: spot, output: dmx, address: 8}\n"
        "  c: {type: fader, output: dmx, address: 14}\n")
    v = fx.load(tmp_path / "fixtures.yaml", types_dir=tmp_path / "types")
    assert v["error"] is None and v["problems"] == [], v["problems"]
    assert [v["types"][t]["footprint"] for t in keys] == [7, 6, 4]
    assert by_name(v["types"]["par"])["Dimmer"]["fine"] == 5
    assert by_name(v["types"]["spot"])["Color Wheel"]["attr"] == "wheel" and v["types"]["spot"]["wheel"]
    assert [c["emitter"] for c in v["types"]["fader"]["channels"]] == ["red", "green", "blue", "white"]
