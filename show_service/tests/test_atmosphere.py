"""atmosphere.py: the haze model fills, holds, rests after a long burst, caps at max, and sends nothing when off."""
from show_service import atmosphere as a

TYPES = {"z-350": {"output": {"channel": "Haze", "range": [5, 255]}, "fan": {"channel": "Fan", "range": [5, 255]},
                   "fluid_ml_min": 3.0}}
ROOM = {"size": [5.0, 10.0, 4.0], "haze_half_life_s": 150.0}
HAZER = [("hazer", "z-350")]


def _air(**more):
    probs: list[str] = []
    air = a.Air()
    more.setdefault("levels", {"off": 0.0, "light": 0.005, "medium": 0.01, "thick": 0.02})   # the tests' own sizes
    air.configure(a.parse({"types": TYPES, **more}, probs))
    assert probs == []
    return air


def test_fills_then_holds_near_the_target():
    air = _air()
    for t in range(600):
        v = air.step(float(t), "medium", HAZER, ROOM)
    assert v["phase"] == "holding" and 0.97 < v["share"] < 1.03
    assert v["volume_m3"] == 200.0 and v["half_life_s"] == 150.0
    # holding medium in 200 m³ at a 150 s half-life: 0.01 x 200 x ln2 / 150 ml/s of a 0.05 ml/s machine
    assert 0.17 < v["output"] < 0.2
    assert v["channels"]["hazer"]["Haze"] == a.to_byte(v["output"], {"range": [5, 255], "off": 0})


def test_off_sends_nothing_and_clears():
    air = _air()
    for t in range(200):
        air.step(float(t), "thick", HAZER, ROOM)
    v = air.step(200.0, "off", HAZER, ROOM)
    assert v["channels"] == {} and v["output"] == 0.0 and v["phase"] == "clearing"
    d0 = v["estimate_ml_m3"]
    v = air.step(350.0, None, HAZER, ROOM)                # a stalled tick counts as DT_MAX_S, never more
    assert 0 < v["estimate_ml_m3"] < d0


def test_a_long_fill_rests_at_the_hold_rate():
    air = _air(gain=50.0, burst_s=10, rest_s=5)            # runs flat out until the burst limit
    phases = [air.step(float(t), "thick", HAZER, {"size": [20.0, 20.0, 5.0]})["phase"] for t in range(40)]
    assert "resting" in phases
    i = phases.index("resting")
    assert all(p != "resting" for p in phases[:10]) and phases[i:i + 5] == ["resting"] * 5


def test_max_caps_the_level_and_no_known_type_says_why():
    air = _air(max="light")
    v = air.step(0.0, "thick", HAZER, ROOM)
    assert v["want"] == "light"
    v = air.step(1.0, "thick", [("smoke", "other")], ROOM)
    assert v["channels"] == {} and "type" in v["why"]


def test_bad_config_lines():
    probs: list[str] = []
    cfg = a.parse({"types": {"x": {"output": {"channel": ""}}}, "max": "huge", "gain": -1, "nope": 1}, probs)
    assert cfg["types"] == {} and cfg["max"] == "thick" and cfg["gain"] == a.FALLBACK["gain"]
    assert len(probs) == 4


def test_a_person_sets_what_the_room_is_and_the_model_carries_on():
    air = _air()
    air.set_now(air.level_density("thick"))                 # by eye: already thick; the feel wants light
    v = air.step(0.0, "light", HAZER, ROOM)
    assert v["estimate_ml_m3"] == 0.02 and v["output"] == 0.0 and v["want"] == "light"   # nothing added: it clears
    air.set_now(0.0)                                        # by eye: clear after all
    v = air.step(1.0, "light", HAZER, ROOM)
    assert v["phase"] == "filling" and v["output"] > 0.3
    assert air.level_density(False) == 0.0 and air.level_density("fog") is None


def test_a_cold_hazer_counts_nothing_until_it_is_warm():
    air = _air(warmup_s=60)
    air.set_warming(air.cfg["warmup_s"])
    for t in range(60):
        v = air.step(float(t), "medium", HAZER, ROOM)
    assert v["estimate_ml_m3"] == 0.0 and v["phase"] == "warming" and v["output"] > 0.5   # it still asks for haze
    for t in range(60, 120):
        v = air.step(float(t), "medium", HAZER, ROOM)
    assert v["estimate_ml_m3"] > 0.0 and v["phase"] != "warming" and v["warming_s"] == 0


def test_the_estimate_survives_a_restart_with_the_time_away_decayed():
    air = _air()
    air.set_now(0.02)
    air.step(0.0, "off", HAZER, ROOM)
    saved = air.dump(1000.0)
    back = _air()
    assert back.load(saved, 1150.0) and abs(back.density - 0.01) < 1e-4        # one half-life (150 s) away
    assert not _air().load({"density": "x"}, 0.0) and not _air().load(None, 0.0)
