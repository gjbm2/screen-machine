"""fixture_encode (docs/LIGHTS.md §2.3, §2.4, §2.8): attributes to DMX bytes, capabilities, resting values.

Pure functions on OFL documents embedded in test_ofl (the Intimidator Spot 110 extended with a gobo rotation and a
prism channel; the RGBW par; the gobo spot) and on fixtures.py's generic types. Exact bytes throughout; nothing here
reads config or touches a device. ``-s`` prints the encode cost.
"""

from __future__ import annotations

import copy
import json
import random
import statistics
import time

from show_service import colour_mix, contract, fixture_encode as fe, ofl
from show_service import fixtures as fx
from show_service.tests.test_ofl import GOBOS, PAR, SPOT, doc_with

# The Spot 110 (test_ofl) with a gobo rotation channel (stop 0-4, CW 5-127, stop 128-131, CCW 132-255) and a prism
# channel, in one 11-channel mode: Pan, Pan fine, Tilt, Tilt fine, speed, colour wheel, strobe, dimmer, gobo wheel,
# gobo rotation, prism.
MOVER = copy.deepcopy(SPOT)
MOVER["availableChannels"]["Gobo Rotation"] = {"capabilities": [
    {"dmxRange": [0, 4], "type": "WheelSlotRotation", "wheel": "Gobo Wheel", "speed": "stop"},
    {"dmxRange": [5, 127], "type": "WheelSlotRotation", "wheel": "Gobo Wheel", "speedStart": "slow CW",
     "speedEnd": "fast CW"},
    {"dmxRange": [128, 131], "type": "WheelSlotRotation", "wheel": "Gobo Wheel", "speed": "stop"},
    {"dmxRange": [132, 255], "type": "WheelSlotRotation", "wheel": "Gobo Wheel", "speedStart": "slow CCW",
     "speedEnd": "fast CCW"}]}
MOVER["availableChannels"]["Prism"] = {"capabilities": [{"dmxRange": [0, 127], "type": "NoFunction"},
                                                        {"dmxRange": [128, 255], "type": "Prism"}]}
MOVER["modes"].append({"name": "mover", "channels": ["Pan", "Pan fine", "Tilt", "Tilt fine", "Pan/Tilt Speed",
                                                     "Color Wheel", "Strobe", "Dimmer", "Gobo Wheel", "Gobo Rotation",
                                                     "Prism"]})
PAN, PANF, TILT, TILTF, SPEED, WHEEL, STROBE, DIM, GOBO, ROT, PRISM = range(11)


def fixture(**kw) -> dict:
    f = {"id": "f", "label": "f", "type": "t", "kind": "lamp", "output": None, "address": 1, "light": None,
         "screen": None, "zone": None, "placement": None, "role": None, "tags": [], "order": None, "wb": {},
         "curve": None, "pin": {}, "house": None, "latency": {}, "setup": {}}
    f.update(kw)
    return f


def mover():
    t, ok = ofl.parse(MOVER, "mover", type_id="spot")
    assert ok and t["problems"] == []
    return t


def enc(t, f=None, intensity=1.0, colour=(1.0, 1.0, 1.0), **attrs) -> list[int]:
    return list(fe.encode(f or fixture(), t, intensity, list(colour), attrs))


# -- the spot (§2.8) ---------------------------------------------------------------------------------------------------
def test_spot_pan_tilt_sixteen_bit_and_clamped():
    t = mover()
    assert enc(t, pan=0)[PAN:PANF + 1] == [0x80, 0x00]
    assert enc(t, pan=-270)[PAN:PANF + 1] == [0, 0] and enc(t, pan=270)[PAN:PANF + 1] == [255, 255]
    assert enc(t, pan=275)[PAN:PANF + 1] == [255, 255] and enc(t, pan=-900)[PAN:PANF + 1] == [0, 0]
    assert enc(t, tilt=135)[TILT:TILTF + 1] == [255, 255] and enc(t, tilt=-135)[TILT:TILTF + 1] == [0, 0]
    v = enc(t, pan=90.0)
    assert v[PAN] * 256 + v[PANF] == round(360 / 540 * 65535)


def test_spot_slots_spins_shutter_speed_and_prism():
    t = mover()
    assert enc(t, gobo=3)[GOBO] == 79                                  # the middle of 64-95
    assert enc(t, gobo_spin=1)[ROT] == 127 and enc(t, gobo_spin=-1)[ROT] == 255 and enc(t, gobo_spin=0)[ROT] == 2
    assert enc(t, shutter={"mode": "strobe", "hz": 11.5})[STROBE] in (73, 74)
    assert enc(t, shutter={"mode": "open"})[STROBE] == 0               # the first Open range's start
    assert enc(t, shutter={"mode": "random", "hz": 23})[STROBE] == 131  # no random range: the strobe one
    assert enc(t, pt_speed=1)[SPEED] == 0 and enc(t, pt_speed=0.01)[SPEED] == 255
    assert enc(t, colour_slot=2)[WHEEL] == 47                          # wins over the nearest (white: Open, 15)
    assert enc(t, prism=1)[PRISM] == 191 and enc(t, prism=True)[PRISM] == 191 and enc(t, prism=0)[PRISM] == 0


def test_spot_nothing_asked_keeps_colour_mix_bytes_and_bad_values_are_skipped():
    t = mover()
    base = list(colour_mix.encode(fixture(), t, 0.7, [1.0, 0.0, 0.0]))
    assert enc(t, intensity=0.7, colour=(1.0, 0.0, 0.0), gobo=None, gobo_spin=None) == base
    assert enc(t, intensity=0.7, colour=(1.0, 0.0, 0.0), pan="left", shutter=["x"], gobo="stars",
               prism={"no": 1}, zoom=30) == base                     # wrong shapes; no zoom channel
    assert base[WHEEL] == 47 and base[PAN] == 128 and base[TILT] == 128   # red slot; resting at centre


# -- parity with colour_mix (§2.8) -------------------------------------------------------------------------------------
def every_type() -> list[dict]:
    types = [fx.BUILTIN_TYPES[tid] for tid in fx.GENERIC_TYPES]
    for doc, mode in [(PAR, m["name"]) for m in PAR["modes"]] + [(SPOT, m["name"]) for m in SPOT["modes"]] + [
            (MOVER, "mover"), (GOBOS, "all")]:
        types.append(ofl.parse(doc, mode, type_id=f"{doc['name']}/{mode}")[0])
    return types


def test_parity_with_colour_mix_for_every_type():
    rng = random.Random(7)
    for t in every_type():
        for _ in range(200):
            f = fixture(wb={"red": 0.9} if rng.random() < 0.3 else {})
            i = rng.random()
            col = [rng.random(), rng.random(), rng.random()]
            m = max(col)
            col = [c / m for c in col]
            want = colour_mix.encode(f, t, i, col)
            assert fe.encode(f, t, i, col) == want, t["id"]
            assert fe.encode(f, t, i, col, {"intensity": 0.2, "colour": [0, 0, 1]}) == want, t["id"]


# -- shared channels (§2.8) --------------------------------------------------------------------------------------------
def test_a_shared_gobo_channel_picks_by_attr_priority():
    t, _ = ofl.parse(GOBOS, "all")
    g1 = 1                                                             # Colour, Gobo 1, ...
    assert enc(t, gobo=2)[g1] == 23                                    # the static slot: middle of 16-31
    assert enc(t, gobo=2, gobo_shake=0.5)[g1] == 79                    # its shake range, speed half way
    assert enc(t, gobo=2, gobo_shake=0)[g1] == 23                      # shake 0: the static slot
    assert enc(t, gobo=3, gobo_shake=1.0)[g1] == 127                   # slot 3's shake, fastest
    assert enc(t, gobo=1, gobo_shake=0.5)[g1] == 7                     # no shake for slot 1: the slot
    assert enc(t, gobo=2, gobo_scroll=0.5)[g1] == 159                  # scroll before a slot (ATTR_PRIORITY)
    assert enc(t, gobo=2, gobo_shake=0.5, gobo_scroll=0.5)[g1] == 79   # shake before scroll
    assert enc(t, gobo_scroll=-1.0)[g1] == 255
    assert fe.ATTR_PRIORITY.index("gobo_shake") < fe.ATTR_PRIORITY.index("gobo_scroll") < fe.ATTR_PRIORITY.index(
        "gobo")
    assert enc(t, gobo2=4)[3] == 111                                   # the second gobo wheel
    assert enc(t, colour_spin=-0.01)[0] == 192 and enc(t, colour_slot=2)[0] == 95


def test_prism_wheel_frost_slot_spin_macro_and_duration_speed():
    t, _ = ofl.parse(GOBOS, "all")
    prisms, turn, macros, move = 4, 5, 6, 7
    assert enc(t, prism=1)[prisms] == 95 and enc(t, prism=2)[prisms] == 223 and enc(t, prism=9)[prisms] == 223
    assert enc(t, prism=0)[prisms] == 0                                # a shared channel: its default (Open)
    assert enc(t, frost=1.0)[prisms] == 159                            # a Frost slot on the prism wheel
    assert enc(t, prism=1, frost=1.0)[prisms] == 95                    # a slot attr wins over a continuous one
    assert enc(t, prism_spin=1.0)[turn] == 255 and enc(t, prism_spin=-1.0)[turn] == 0   # no CCW range: skipped (0)
    assert enc(t, macro={"Macros": "reset"})[macros] == 10 and enc(t, macro={"Macros": "Auto program"})[macros] == 20
    assert enc(t, macro={"Macros": 200})[macros] == 200 and enc(t, macro={"Nope": "reset"})[macros] == 0
    assert enc(t, pt_speed=1.0)[move] == 0 and enc(t, pt_speed=0.0)[move] == 255


def test_explicit_resting_values_on_dedicated_channels_and_pins_kept():
    t = fx.BUILTIN_TYPES["generic-mover-full"]
    shutter, rot, prism = 6, 9, 10
    assert enc(t, gobo_spin=0)[rot] == 1                               # the stop range's middle
    assert enc(t, shutter={"mode": "open"})[shutter] == 8 and enc(t, prism=0)[prism] == 63
    f = fixture(pin={"Shutter": 20, "Gobo rotation": 60})
    assert enc(t, f, shutter={"mode": "open"}, gobo_spin=0)[shutter] == 20       # a pin is the owner's resting value
    assert enc(t, f, shutter={"mode": "open"}, gobo_spin=0)[rot] == 60
    assert enc(t, f, shutter={"mode": "strobe", "hz": 20})[shutter] == 131       # an active value drives it
    assert enc(t, shutter="closed")[shutter] == 0 and enc(t, shutter={"mode": "strobe"})[shutter] == 73


# -- unknown ranges and the setup (§2.8) -------------------------------------------------------------------------------
def test_a_percent_pan_takes_the_setup_range():
    d = doc_with({"Pan": {"capability": {"type": "Pan", "angleStart": "0%", "angleEnd": "100%"}},
                  "Tilt": {"capability": {"type": "Tilt", "angleStart": "0deg", "angleEnd": "270deg"}}},
                 ["Pan", "Tilt"])
    t, _ = ofl.parse(d, "m")
    assert any("540° assumed" in p for p in t["problems"])
    c = fe.caps(t)
    assert (c["pan"]["min"], c["pan"]["max"]) == (-270.0, 270.0) and (c["tilt"]["min"], c["tilt"]["max"]) == (
        -135.0, 135.0)
    setup = {"range": {"pan": 630, "tilt": 300}}
    c = fe.caps(t, setup)
    assert (c["pan"]["min"], c["pan"]["max"]) == (-315.0, 315.0)
    assert (c["tilt"]["min"], c["tilt"]["max"]) == (-135.0, 135.0)     # tilt is in degrees in the file: kept
    f = fixture(setup=setup)
    assert enc(t, f, pan=315)[0] == 255 and enc(t, f, pan=157.5)[0] == 191 and enc(t, pan=157.5)[0] == 202


def test_setup_colours_pick_the_colour_wheels_nearest_slot():
    t = fx.BUILTIN_TYPES["generic-mover-full"]
    wheel = 7
    f = fixture(setup={"slots": {"colour": {"2": {"name": "Red", "hex": "#ff0000"}, "3": {"hex": "#0000ff"}}}})
    assert enc(t, f, colour=(1.0, 0.0, 0.0), pan=0)[wheel] == 23
    assert enc(t, f, colour=(0.0, 0.1, 1.0), pan=0)[wheel] == 39
    assert enc(t, f, colour=(1.0, 1.0, 1.0), pan=0)[wheel] == 7        # white: the Open slot
    assert enc(t, f, colour=(1.0, 0.0, 0.0), pan=0, colour_slot=5)[wheel] == 71
    assert enc(t, colour=(1.0, 0.0, 0.0), pan=0)[wheel] == 0           # no colours known: colour_mix's (Open)
    c = fe.caps(t, f["setup"])
    assert c["colour_slots"][1] == {"slot": 2, "name": "Red", "hex": "#ff0000"}
    assert c["colour_slots"][2] == {"slot": 3, "name": "Colour 3", "hex": "#0000ff"}


# -- the generic movers (§2.5, §2.8) -----------------------------------------------------------------------------------
def test_generic_movers_exact_bytes():
    full = fx.BUILTIN_TYPES["generic-mover-full"]
    attrs = {"pan": 90, "tilt": -45, "pt_speed": 1.0, "shutter": {"mode": "strobe", "hz": 10}, "colour_slot": 3,
             "gobo": 4, "gobo_spin": 0.5, "prism": 1, "zoom": 25, "focus": 0.25}
    assert list(fe.encode(fixture(), full, 0.5, [1, 1, 1], attrs)) == [170, 170, 85, 85, 0, 128, 70, 39, 27, 65, 191,
                                                                        128, 64]
    basic = fx.BUILTIN_TYPES["generic-mover"]
    assert list(fe.encode(fixture(), basic, 1.0, [1, 0, 0], {"pan": -135, "tilt": 67.5})) == [64, 191, 255, 255, 0,
                                                                                              0]
    assert list(fe.encode(fixture(), basic, 1.0, [1, 0, 0])) == [128, 128, 255, 255, 0, 0]   # at rest: centre


def test_generic_mover_caps_and_slot_count():
    c = fe.caps(fx.BUILTIN_TYPES["generic-mover-full"])
    assert {"pan", "tilt", "zoom", "focus", "gobo", "gobo_spin", "gobo_shake", "prism", "shutter", "pt_speed",
            "colour_slot", "colour_spin"} <= set(c["attrs"])
    assert c["moves"] and c["pan"] == {"min": -270.0, "max": 270.0, "fine": True, "limits": None}
    assert c["zoom"] == {"min": 10.0, "max": 40.0, "known": False} and c["colour"] == "wheel"
    assert len(c["gobo_slots"]) == 8 and c["gobo_slots"][0] == {"slot": 1, "name": "Open", "shake": True, "open": True}
    assert c["prism_slots"] == [{"slot": 1, "name": "Prism", "facets": None}]
    assert c["spins"] == ["colour_spin", "gobo_spin", "gobo_scroll"] and c["shutter"] == ["open", "closed", "strobe"]
    assert c["strobe_hz"] == [1.0, 20.0] and c["macros"] == [] and c["cct"] is None
    setup = {"slots": {"count": {"gobo": 6, "colour": 6}, "gobo": {"2": "Stars"}}, "limits": {"pan": [-90, 90]},
             "strobe_hz_max": 8}
    c6 = fe.caps(fx.BUILTIN_TYPES["generic-mover-full"], setup)
    assert len(c6["gobo_slots"]) == 6 and len(c6["colour_slots"]) == 6 and c6["gobo_slots"][1]["name"] == "Stars"
    assert c6["pan"]["limits"] == [-90.0, 90.0] and c6["strobe_hz"] == [1.0, 8.0]
    f = fixture(setup=setup)
    full = fx.BUILTIN_TYPES["generic-mover-full"]
    assert enc(full, f, colour_slot=3)[7] == 52 and enc(full, f, gobo=4)[8] == 36    # re-divided: 6 slots
    assert enc(full, colour_slot=3)[7] == 39 and enc(full, gobo=4)[8] == 27          # the type's own: 8
    b = fe.caps(fx.BUILTIN_TYPES["generic-mover"])
    assert b["moves"] and b["pan"]["fine"] is False and b["gobo_slots"] == [] and b["colour"] == "mix"


# -- caps (§2.8) -------------------------------------------------------------------------------------------------------
def test_caps_for_a_par_and_the_spot():
    par, _ = ofl.parse(PAR, "Pro 7")
    c = fe.caps(par)
    assert set(c) == set(contract.FixtureCaps.__annotations__)
    assert c["moves"] is False and {"intensity", "colour"} <= set(c["attrs"]) and c["pan"] is None
    assert c["colour"] == "mix" and c["shutter"] == ["open", "strobe"] and c["strobe_hz"] == [1.0, 20.0]
    spot, _ = ofl.parse(SPOT, "12ch")
    c = fe.caps(spot)
    assert c["colour"] == "wheel" and len(c["colour_slots"]) == 8 and c["moves"] and c["pt_speed"]
    assert c["colour_slots"][1] == {"slot": 2, "name": "Red", "hex": "#ff0000"}
    assert c["strobe_hz"] == [0.0, 23.0]
    gob, _ = ofl.parse(GOBOS, "all")
    c = fe.caps(gob)
    assert [s["name"] for s in c["gobo_slots"]] == ["Open", "Stars", "Pattern 3"]
    assert [s["shake"] for s in c["gobo_slots"]] == [False, True, True] and len(c["gobo2_slots"]) == 8
    assert c["prism_slots"] == [{"slot": 1, "name": "Prism 3", "facets": 3}, {"slot": 2, "name": "Linear",
                                                                             "facets": 5}]
    assert c["frost"] and c["macros"] == [
        {"channel": "Gobo 1 rotation", "name": "Gobo 1 rotation", "choices": [{"word": "Index", "dmx": 0}]},
        {"channel": "Macros", "name": "Macros", "choices": [{"word": "Reset", "dmx": 10},
                                                            {"word": "Auto program", "dmx": 20}]}]
    assert c["spins"] == ["colour_spin", "gobo_spin", "gobo_scroll", "prism_spin"]
    hue = fe.caps(fx.BUILTIN_TYPES["sm-hue"])
    assert hue["attrs"] == ["intensity", "colour"] and hue["moves"] is False and hue["colour"] == "none"
    json.dumps(c)


def test_neutral_and_enc():
    full = fx.BUILTIN_TYPES["generic-mover-full"]
    setup = {"home": {"pan": 30, "tilt": -200}, "beam": {"zoom": 20, "frost": 0.4}}
    n = fe.neutral(fe.caps(full, setup), setup)
    assert n["pan"] == 30 and n["tilt"] == -135.0 and n["zoom"] == 20 and n["focus"] == 0.5 and "iris" not in n
    assert n["gobo"] is None and n["colour_slot"] is None and n["gobo_spin"] == 0.0 and n["prism"] == 0
    assert n["shutter"] == {"mode": "open", "hz": None} and n["pt_speed"] == 1.0 and "intensity" not in n
    rest = fe.neutral(fe.caps(full))
    assert rest["pan"] == 0.0 and rest["zoom"] == 25.0
    assert list(fe.encode(fixture(), full, 1.0, [1, 1, 1], rest)) == [128, 0, 128, 0, 0, 255, 8, 0, 0, 1, 63, 128,
                                                                      128]
    assert fe.enc(full) and fe.enc(fx.BUILTIN_TYPES["generic-mover"])
    assert not fe.enc(fx.BUILTIN_TYPES["generic-rgbw"]) and not fe.enc(fx.BUILTIN_TYPES["sm-hue"])
    assert fe.enc(ofl.parse(PAR, "Pro 7")[0])                          # a strobe channel: effects may use it
    r = {"dmx": [100, 200], "lo": 2.0, "hi": 1.0}
    assert fe.to_dmx(r, 2.0) == 100 and fe.to_dmx(r, 1.5) == 150 and fe.to_dmx(r, 0) == 200
    assert fe.to_dmx({"dmx": [10, 20], "lo": 3, "hi": 3}, 99) == 15


def test_encode_cost():
    t = mover()
    f = fixture()
    rng = random.Random(1)
    times = []
    for k in range(2000):
        attrs = {"pan": rng.uniform(-270, 270), "tilt": rng.uniform(-135, 135), "gobo": 2, "gobo_spin": 0.3,
                 "shutter": {"mode": "open"}, "prism": k % 2}
        t0 = time.perf_counter()
        fe.encode(f, t, 0.8, [1.0, 0.5, 0.0], attrs)
        times.append((time.perf_counter() - t0) * 1e6)
    p50 = statistics.median(times)
    print(f"\nfixture_encode: a moving spot, p50 {p50:.1f} us")
    assert p50 < 2000


def test_no_config_names_in_code():
    from show_service.tests.test_fixtures import test_no_config_names_in_code as scan
    scan("fixture_encode.py")                                         # the owner's rule: config names live in config
