"""Sensor ingest, unit level (fake time; no sockets): config parsing, the MaskShaper, the
SensorService (lightmask wiring, held, staleness, golden parity) and the MaskArbiter's tiers."""

from __future__ import annotations

import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

import lightmask as lm

from room_service.config import config_from_dict
from room_service.ingest import SensorKeys
from room_service.ingest_config import ShaperConfig, ingest_config_from_dict
from room_service.mask import MaskValue
from room_service.mask_arbiter import FLASK, HOLD, STALE, MaskArbiter
from room_service.mask_arbiter import SENSOR as T_SENSOR
from room_service.mask_shaper import LatencyStats, MaskShaper
from room_service.sensors import SensorService

from .ingest_helpers import KEY, REPO, SENSOR, ingest_raw, write_keys, write_light_config

NOON = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
GOLD_PATH = REPO / "lightmask" / "tests" / "fixtures" / "golden_flask.json.gz"


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> float:
        self.t += dt
        return self.t


def mk(b: float, a: float = 0.0, hexv: str = "#FFFFFF", source: str = "sensor") -> dict:
    return {"brightness": b, "warm_alpha": a, "warm_hex": hexv, "source": source}


# ── config ──────────────────────────────────────────────────────────────────

def test_ingest_config_defaults_change_nothing():
    c = ingest_config_from_dict({})
    assert c.enabled and c.mask_source == "auto" and c.screen_sources == {}
    assert c.sensors_file == Path("~/.config/screen-machine/room_sensors.json").expanduser()
    s = c.shaper
    assert (s.snap_transition_ms, s.drift_transition_ms, s.drift_interval_s, s.drift_deadband_brightness,
            s.max_sets_per_s, s.live_max_sets_per_s, s.source_change_transition_ms) == (200, 1000, 3.0, 0.015, 10, 2, 2000)
    assert s.drift_alpha == 0.002                      # the existing mask.deadband_warm_alpha
    # A room.yaml without `ingest:` still parses (config.py's own defaults untouched).
    assert config_from_dict({}).mask.transition_ms == 200


def test_ingest_config_parses_and_validates(tmp_path):
    c = ingest_config_from_dict({
        "ingest": {"sensors_file": "keys.json", "mask_source": "Sensor", "screens": {"north-screen": "flask",
                   "south-screen": {"mask_source": "auto"}}, "filter": {"stale_after_s": 5}, "hello_timeout_s": 3,
                   "max_msgs_per_s": 30, "lightmask": {"root": "/x"}},
        "mask": {"drift_interval_s": 2, "snap_transition_ms": 150, "drift_deadband_warm_alpha": 0.005,
                 "deadband_warm_alpha": 0.003, "transition_ms": 250}}, base_dir=tmp_path)
    assert c.sensors_file == tmp_path / "keys.json"
    assert c.source_for("north-screen") == "flask" and c.source_for("south-screen") == "auto"
    assert c.source_for("devtest") == "sensor"
    assert c.filter == {"stale_after_s": 5} and c.hello_timeout_s == 3.0 and c.max_msgs_per_s == 30
    assert c.lightmask_root == Path("/x")
    assert c.shaper.drift_interval_s == 2.0 and c.shaper.snap_transition_ms == 150
    assert c.shaper.drift_alpha == 0.005 and c.shaper.deadband_warm_alpha == 0.003
    assert c.shaper.flask_transition_ms == 250
    with pytest.raises(ValueError):
        ingest_config_from_dict({"ingest": {"mask_source": "both"}})
    with pytest.raises(ValueError):
        ingest_config_from_dict({"mask": {"max_sets_per_s": 0}})


# ── shaper ──────────────────────────────────────────────────────────────────

def test_shaper_first_snap_and_deadbands():
    sh = MaskShaper(ShaperConfig())
    s = sh.offer(mk(0.5), 0.0)
    assert s.kind == "snap" and s.transition_ms == 200
    sh.sent(s, 0.0)
    assert sh.offer(mk(0.51), 0.5) is None              # below the 0.015 drift deadband
    assert sh.offer(mk(0.503), 0.6, hint="snap") is None   # a filter snap below the base deadband
    s = sh.offer(mk(0.51), 0.7, hint="snap")              # a filter snap beyond it: at once
    assert s.kind == "snap" and s.transition_ms == 200
    sh.sent(s, 0.7)
    s = sh.offer(mk(0.6), 0.8)                            # |Δb| >= 0.05: a real change, at once
    assert s.kind == "snap"
    sh.sent(s, 0.8)
    s = sh.offer(mk(0.6, source="manual_override"), 0.95)  # a source change (override)
    assert s.kind == "snap"


def test_shaper_drift_once_per_3s_with_1s_fade():
    sh = MaskShaper(ShaperConfig())
    sh.sent(sh.offer(mk(0.5), 0.0), 0.0)
    assert sh.offer(mk(0.52), 1.0) is None                # drift, but only 1 s since the last send
    assert sh.wait_s(1.0) == pytest.approx(2.0)
    assert sh.due(2.9) is None
    assert sh.offer(mk(0.525), 2.0) is None               # latest value wins
    s = sh.due(3.0)
    assert s.kind == "drift" and s.transition_ms == 1000 and s.mask["brightness"] == 0.525
    sh.sent(s, 3.0)
    assert sh.offer(mk(0.535), 3.5) is None and sh.pending is None   # within 0.015 of 0.525: nothing
    assert sh.offer(mk(0.525, a=0.0025), 4.0) is None and sh.pending is not None  # warm alpha deadband 0.002
    assert sh.offer(mk(0.525, a=0.001), 4.1) is None and sh.pending is None       # back inside: dropped
    # A slow ramp (0.01/s): one 1 s fade per 3 s, never faster.
    sends, t, b = [], 6.0, 0.525
    while t < 36.0:
        b += 0.001
        s = sh.offer(mk(b), t) or sh.due(t)
        if s:
            assert s.kind == "drift" and s.transition_ms == 1000
            sh.sent(s, t)
            sends.append(t)
        t += 0.1
    gaps = [b2 - a2 for a2, b2 in zip(sends, sends[1:])]
    assert len(sends) >= 9 and min(gaps) >= 3.0 - 1e-6, gaps
    # A faster change reaches |Δb| >= 0.05 before 3 s: DCL v3.1 §4.4 sends that as a snap.
    sh.sent(sh.offer(mk(0.2), 50.0, hint="source"), 50.0)
    assert sh.offer(mk(0.24), 51.0) is None and sh.offer(mk(0.25), 52.0).kind == "snap"


def test_shaper_rate_caps():
    c = ShaperConfig()
    sh = MaskShaper(c)
    sent = []
    t = 0.0
    for i in range(100):                                   # 20 Hz of big changes for 5 s
        s = sh.offer(mk(0.2 + 0.6 * (i % 2)), t) or sh.due(t)
        if s:
            sh.sent(s, t)
            sent.append(t)
        t += 0.05
    per_s = max(sum(1 for x in sent if w <= x < w + 1.0) for w in [k * 0.05 for k in range(80)])
    assert per_s <= 10, per_s
    sh2, sent2, t = MaskShaper(c), [], 0.0
    for i in range(100):                                   # the same while live: 2/s
        s = sh2.offer(mk(0.2 + 0.6 * (i % 2)), t, max_rate_hz=c.live_max_sets_per_s) or sh2.due(t, c.live_max_sets_per_s)
        if s:
            sh2.sent(s, t)
            sent2.append(t)
        t += 0.05
    assert len(sent2) <= 11 and min(b - a for a, b in zip(sent2, sent2[1:])) >= 0.5 - 1e-6


def test_shaper_source_and_flask_kinds():
    sh = MaskShaper(ShaperConfig())
    sh.seed(mk(0.5, source="flask"))
    s = sh.offer(mk(0.5005), 0.0, hint="source")
    assert s.kind == "source" and s.transition_ms == 2000
    sh.sent(s, 0.0)
    s = sh.offer(mk(0.5005, hexv="#FFEEDD", source="flask"), 0.5, flask=True)
    assert s.kind == "flask" and s.transition_ms == 200   # today's Flask rule: any hex change
    sh.sent(s, 0.5)
    assert sh.offer(mk(0.503, hexv="#FFEEDD", source="flask"), 1.0, flask=True) is None


def test_latency_stats_percentiles():
    st = LatencyStats(100)
    for v in range(1, 101):
        st.add(float(v))
    assert st.status()["p50"] == 50 and st.status()["p95"] == 95 and st.status()["immediate"]["n"] == 100
    assert LatencyStats.summary([])["p50"] is None


# ── keys ────────────────────────────────────────────────────────────────────

def test_sensor_keys_file(tmp_path, caplog):
    """The keys file still loads (and its keys stay out of the logs), but no key is checked any more
    (owner ruling, 14 Oct): any sensor id, with any key or none, is accepted."""
    k = SensorKeys(tmp_path / "keys.json")
    k.refresh()
    assert not k.exists and k.keys == {}
    assert k.check(SENSOR, None) and k.check("other", "nope")
    p = write_keys(tmp_path / "keys.json", {SENSOR: KEY, "short": "abc", "bad id!": KEY})
    k.refresh()
    assert k.exists and set(k.keys) == {SENSOR}
    assert k.check(SENSOR, KEY) and k.check(SENSOR, KEY + "x") and k.check(SENSOR, None) and k.check("other", None)
    p.chmod(0o644)
    p.write_text(json.dumps({SENSOR: "z" * 40}))
    k.refresh()
    assert "readable by group/others" in caplog.text
    assert KEY not in caplog.text and "z" * 40 not in caplog.text
    p.unlink()
    k.refresh()
    assert not k.exists and k.keys == {}


# ── SensorService ───────────────────────────────────────────────────────────

def room_cfg(tmp: Path, screens=("devtest", "north-screen", "south-screen"), mask_push=True, **ingest_over):
    light = write_light_config(tmp)
    raw = {"screens": {s: {"mask_push": mask_push} for s in screens}, "service": {"state_dir": str(tmp / "state")},
           "flask": {"url": "http://127.0.0.1:1", "ws": "ws://127.0.0.1:1/"}}
    cfg = config_from_dict(raw)
    cfg.ingest = ingest_config_from_dict({"ingest": ingest_raw(tmp, light, **ingest_over)})
    return cfg, light


def sensor_service(tmp: Path, clock: Clock, wall=lambda: NOON, **ingest_over) -> SensorService:
    cfg, _ = room_cfg(tmp, **ingest_over)
    svc = SensorService(cfg, cfg.ingest, clock=clock, wall=wall)
    assert svc.refresh_config()
    return svc


def test_sensor_service_maps_like_lightmask(tmp_path):
    c = Clock()
    svc = sensor_service(tmp_path, c)
    assert svc.targets(SENSOR) == ["north-screen", "south-screen", "devtest"]
    assert svc.sensors_for("devtest") == [SENSOR] and svc.driver("devtest") is None
    assert svc.mask("devtest", c.t)["source"] == "astral"          # no reading yet: the solar curve
    r = svc.ingest(SENSOR, 50.0, 1, False, c.t)
    assert r.accepted and r.mode == "init"
    v = svc.view("devtest", c.t)
    assert v.driver == SENSOR and v.fresh and v.live_seen and not v.held
    m = svc.mask("devtest", c.t, pure=True)
    cfg = svc.config
    exp = lm.mask_for_dest(cfg, "devtest", NOON, None, {SENSOR: lm.intensity_for(cfg, SENSOR, svc.filters[SENSOR].lux)})
    assert m == exp and m["source"] == "sensor" and m["brightness"] == pytest.approx(0.73)


def test_validation_via_lightmask(tmp_path):
    c = Clock()
    svc = sensor_service(tmp_path, c)
    assert svc.ingest(SENSOR, float("nan"), 1, False, c.t).reason == "invalid_lux"
    assert svc.ingest(SENSOR, float("inf"), 2, False, c.t).reason == "invalid_lux"
    assert svc.ingest(SENSOR, float("-inf"), 3, False, c.t).reason == "invalid_lux"
    assert svc.ingest(SENSOR, "12", 4, False, c.t).reason == "invalid_lux"
    assert svc.ingest(SENSOR, True, 5, False, c.t).reason == "invalid_lux"
    assert svc.ingest(SENSOR, 10 ** 400, 6, False, c.t).reason == "invalid_lux"
    r = svc.ingest(SENSOR, -5.0, 10, False, c.advance(0.1))            # negative: clamped to 0
    assert r.accepted and svc.filters[SENSOR].lux == pytest.approx(0.0)
    svc.filters.clear()
    r = svc.ingest(SENSOR, 1e9, 11, False, c.advance(0.1))             # clamped to 200 klx
    assert r.accepted and svc.filters[SENSOR].lux == pytest.approx(200_000.0)
    assert svc.ingest(SENSOR, 5.0, 11, False, c.advance(0.1)).reason == "seq_duplicate"
    assert svc.ingest(SENSOR, 5.0, 7, False, c.advance(0.1)).reason == "seq_backwards"
    assert svc.ingest(SENSOR, 5.0, 7.5, False, c.advance(0.1)).reason == "invalid_seq"
    svc.reset_sequence(SENSOR)                                          # a new hello
    assert svc.ingest(SENSOR, 5.0, 3, False, c.advance(0.1)).accepted
    assert svc.rejected[SENSOR]["invalid_lux"] == 6


def test_held_freezes_and_releases(tmp_path):
    c = Clock()
    svc = sensor_service(tmp_path, c)
    seq = 0
    for _ in range(5):
        seq += 1
        svc.ingest(SENSOR, 50.0, seq, False, c.advance(0.1))
    before = svc.mask("devtest", c.t, pure=True)
    for lux in (2000, 0, 2000, 0, 2000, 0):                               # calibration flashes, held
        seq += 1
        r = svc.ingest(SENSOR, lux, seq, True, c.advance(0.7))
        assert r.accepted and r.mode == "held"
        assert svc.view("devtest", c.t).held
        assert svc.mask("devtest", c.t, pure=True) == before
    seq += 1
    r = svc.ingest(SENSOR, 50.0, seq, False, c.advance(0.7))             # released at the same light
    assert r.mode == "drift" and not svc.view("devtest", c.t).held
    assert svc.mask("devtest", c.t, pure=True)["brightness"] == pytest.approx(before["brightness"])
    # A relay that dies mid-capture does not pin the mask: held turns false once stale.
    seq += 1
    svc.ingest(SENSOR, 50.0, seq, True, c.advance(0.1))
    assert svc.view("devtest", c.t).held
    assert not svc.view("devtest", c.advance(10.5)).held


def test_stale_sensor_ramps_to_solar_over_30s(tmp_path):
    c = Clock()
    night = datetime(2026, 12, 21, 23, 0, tzinfo=timezone.utc)            # solar floor: far from the sensor value
    svc = sensor_service(tmp_path, c, wall=lambda: night)
    assert svc.ingest(SENSOR, 100.0, 1, False, c.t).accepted
    assert svc.mask("devtest", c.t)["source"] == "astral"      # cold start: from solar...
    for seq in range(2, 14):                                    # ...ramped to the sensor over 5 s
        svc.ingest(SENSOR, 100.0, seq, False, c.advance(0.5))
    t0 = c.t
    sensor_b = svc.mask("devtest", t0)["brightness"]
    solar_b = lm.solar_mask(svc.config, "devtest", night)["brightness"]
    assert sensor_b == 1.0 and solar_b < 0.1
    assert svc.mask("devtest", t0 + 9.9)["source"] == "sensor"            # not stale yet
    assert not svc.view("devtest", t0 + 10.1).fresh
    seen = []
    for dt in (10.1, 15, 25, 35, 39.9):
        m = svc.mask("devtest", t0 + dt)
        assert m["source"] == "blend"
        w = (dt - 10) / 30
        assert m["solar_weight"] == pytest.approx(w, abs=0.002)
        assert m["brightness"] == pytest.approx(sensor_b + (solar_b - sensor_b) * w, abs=1e-3)
        seen.append(m["brightness"])
    assert seen == sorted(seen, reverse=True)
    assert svc.mask("devtest", t0 + 40.1)["source"] == "astral"
    # pure=True (the arbiter's fresh-sensor view) never blends
    assert svc.mask("devtest", t0 + 20, pure=True)["source"] == "sensor"


def test_sensor_snap_and_drift_modes(tmp_path):
    c = Clock()
    svc = sensor_service(tmp_path, c)
    seq = 1
    svc.ingest(SENSOR, 50.0, seq, False, c.t)
    modes = []
    for lux in (2000, 2000, 2000, 2000, 2000, 2000, 2000, 2000):          # a step at 10 Hz
        seq += 1
        modes.append(svc.ingest(SENSOR, lux, seq, False, c.advance(0.1)).mode)
    assert modes[0] == "pending" and modes[1] == "snap"                   # the spike guard, then the snap
    assert svc.filters[SENSOR].lux > 1500


def _golden_sets():
    with gzip.open(GOLD_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)["sets"]


OVERRIDES = {"none": lm.Overrides(), "maskon": lm.Overrides(mask_enabled=True),
             "maskoff": lm.Overrides(mask_enabled=False), "bright0.4": lm.Overrides(brightness=0.4),
             "maskoff+bright0.4": lm.Overrides(mask_enabled=False, brightness=0.4)}


@pytest.mark.parametrize("set_name", ["real", "synthetic"])
def test_golden_parity_with_flask(tmp_path, set_name):
    """SensorService's steady-state mask for every golden case equals Flask's /api/<id>/mask
    (lightmask's fixtures), smoothing excepted: one reading per sensor, then steady."""
    st = _golden_sets()[set_name]
    inp = st["inputs"]
    d = tmp_path / "gold"
    d.mkdir()
    (d / "config.py").write_text(f"INTENSITY_CFG = {inp['intensity_cfg']!r}\n")
    (d / "intensity.json").write_text(json.dumps(inp["intensity_mapping"]))
    (d / "dests.json").write_text(json.dumps(inp["destinations"]))
    dest_ids = sorted({x["id"] for x in inp["destinations"]})
    cfg = config_from_dict({"screens": {i: {"mask_push": True} for i in dest_ids},
                            "service": {"state_dir": str(tmp_path / "state")}})
    cfg.ingest = ingest_config_from_dict({"ingest": {"filter_file": None, "lightmask": {
        "root": str(d), "config_py": str(d / "config.py"), "intensity_json": str(d / "intensity.json"),
        "destinations_json": str(d / "dests.json")}}})
    now = {"t": NOON}
    c = Clock()
    svc = SensorService(cfg, cfg.ingest, clock=c, wall=lambda: now["t"])
    assert svc.refresh_config()
    lcfg = svc.config
    bad, n = [], 0
    for case in st["route"]:
        now["t"] = datetime.fromisoformat(case["now"])
        svc.filters.clear()
        svc.last_reading.clear()
        svc.overrides = {case["dest"]: OVERRIDES[case["override"]]}
        seq = 0
        for name, lux in (case["lux"] or {}).items():
            for _ in range(3):                                  # init, then two identical readings
                seq += 1
                assert svc.ingest(name, lux, seq, False, c.advance(0.1)).accepted
        got = svc.mask(case["dest"], c.t, pure=True)
        # wiring: exactly lightmask on the filtered lux
        sensors = {nm: lm.intensity_for(lcfg, nm, f.lux) for nm, f in svc.filters.items()}
        assert got == lm.mask_for_dest(lcfg, case["dest"], now["t"], svc.overrides[case["dest"]], sensors or None)
        exp = case["expect"]
        n += 1
        ok = (got is not None and math.isclose(got["brightness"], exp["brightness"], abs_tol=1e-9)
              and got["warm_hex"] == exp["warm_hex"] and got["warm_alpha"] == exp["warm_alpha"])
        if ok and "source" in exp:
            ok = got["source"] == exp["source"]
        if ok and "sensor" in exp:
            ok = got.get("sensor") == exp["sensor"]["name"]
        if not ok:
            bad.append((case["dest"], case["now"], case["lux"], case["override"], exp, got))
    assert n == len(st["route"]) and n > 1000
    assert not bad, f"{len(bad)}/{n} differ, e.g. {bad[:3]}"


# ── MaskArbiter tiers (fake time, no sockets) ───────────────────────────────

class StubPages:
    def __init__(self, screens) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.connected = set(screens)

    def broadcast(self, screen: str, type_: str, data: dict) -> int:
        if screen not in self.connected:
            return 0
        self.sent.append((screen, dict(data)))
        return 1

    def any_connected(self, screen: str) -> bool:
        return screen in self.connected

    def of(self, screen: str = "devtest") -> list[dict]:
        return [d for s, d in self.sent if s == screen]


def arbiter(tmp: Path, c: Clock, wall=lambda: NOON, keys=True, **ingest_over):
    cfg, _ = room_cfg(tmp, **ingest_over)
    if keys:
        write_keys(tmp / "room_sensors.json", {SENSOR: KEY})
    pages = StubPages(cfg.screens)
    arb = MaskArbiter(cfg, None, pages, is_frozen=lambda s: False, clock=c, wall=wall)
    arb.keys.refresh()
    arb.sensors.refresh_config()
    return arb, pages


def ready_overrides(arb: MaskArbiter, c: Clock, screens=("devtest", "north-screen", "south-screen")) -> None:
    for s in screens:
        arb.sensors._ov_attempt[s] = c.t


def test_arbiter_without_keys_is_armed(tmp_path):
    """No keys file: ingest is still on (owner ruling, 14 Oct), and Flask feeds every screen until a sensor speaks."""
    c = Clock()
    arb, pages = arbiter(tmp_path, c, keys=False)
    assert arb.armed and arb.disabled_reason() is None
    assert arb._targets() == ["devtest", "north-screen", "south-screen"]
    assert arb._select("devtest", c.t) == FLASK


def test_arbiter_tiers_auto(tmp_path):
    c = Clock()
    arb, pages = arbiter(tmp_path, c, filter={"stale_after_s": 2.0})
    assert arb.armed and arb._select("devtest", c.t) == FLASK
    arb.on_reading(SENSOR, 50.0, 1, False, c.t)
    assert arb.tier["devtest"] == FLASK and arb._ov_wake.is_set()     # overrides first
    assert pages.of() == []
    ready_overrides(arb, c)
    arb.evaluate("devtest")
    assert arb.tier["devtest"] == T_SENSOR
    assert arb._targets() == []                                        # no Flask fetch for sensor screens
    m = pages.of()[-1]
    assert m["source"] == "sensor" and m["transition_ms"] == 200 and m["brightness"] == pytest.approx(0.73)
    c.advance(2.5)                                                     # stale -> Flask again
    ready_overrides(arb, c)
    arb._evaluate_all()
    assert arb.tier["devtest"] == FLASK and arb._targets() == ["devtest", "north-screen", "south-screen"]
    # Flask failing for flask_fail_after_s while the sensor is stale -> lightmask's stale ramp
    arb._flask_failed["devtest"] = True
    c.advance(arb.icfg.flask_fail_after_s + 0.1)
    arb.evaluate("devtest")
    assert arb.tier["devtest"] == STALE and "devtest" in arb._targets()
    assert pages.of()[-1]["source"] in ("blend", "astral") and pages.of()[-1]["transition_ms"] == 2000
    # a successful fetch -> Flask
    arb.latest["devtest"] = MaskValue(0.4, "#FFFFFF", 0.0)
    arb._flask_failed["devtest"] = False
    arb.evaluate("devtest")
    assert arb.tier["devtest"] == FLASK


def test_flask_lux_frames_feed_the_sensor_path(tmp_path):
    """The kiosk relay posts to Flask's WS; its broadcast copy (with target_group) is a sensor reading."""
    c = Clock()
    arb, pages = arbiter(tmp_path, c)
    ready_overrides(arb, c)
    arb.on_lux_frame({"sensor_name": SENSOR, "lux": 50.0})                       # Flask's raw relay copy: ignored
    assert arb.counts["readings"] == 0
    arb.on_lux_frame({"sensor_name": SENSOR, "lux": 50.0, "timestamp": 1.0, "target_intensity": 0.5,
                      "target_group": "x"})
    assert arb.counts["flask_readings"] == 1 and arb.counts["readings"] == 1
    assert arb.tier["devtest"] == T_SENSOR and pages.of()[-1]["source"] == "sensor"
    arb.hub.conns[SENSOR] = object()                                              # a direct relay wins
    arb.on_lux_frame({"sensor_name": SENSOR, "lux": 60.0, "target_group": "x"})
    assert arb.counts["flask_readings"] == 1


def test_arbiter_sensor_mode_ramps_and_never_fetches(tmp_path):
    c = Clock()
    night = datetime(2026, 12, 21, 23, 0, tzinfo=timezone.utc)
    arb, pages = arbiter(tmp_path, c, wall=lambda: night, mask_source="sensor")
    assert arb._select("devtest", c.t) == HOLD and arb._targets() == []
    ready_overrides(arb, c)
    arb.on_reading(SENSOR, 100.0, 1, False, c.t)
    assert arb.tier["devtest"] == T_SENSOR
    for seq in range(2, 14):              # sensor mode is lightmask verbatim: a cold start ramps 5 s
        ready_overrides(arb, c)
        arb.on_reading(SENSOR, 100.0, seq, False, c.advance(0.5))
        arb._flush("devtest")
    assert pages.of()[-1]["brightness"] == 1.0 and pages.of()[-1]["source"] == "sensor"
    pages.sent.clear()
    t0 = c.t
    sent_at = []
    while c.t < t0 + 45:                                               # the relay is gone
        c.advance(0.1)
        ready_overrides(arb, c)
        n = len(pages.of())
        arb.evaluate("devtest")
        arb._flush("devtest")
        if len(pages.of()) > n:
            sent_at.append(c.t - t0)
        assert arb._targets() == []                                    # sensor mode: never Flask
    sources = [m["source"] for m in pages.of()]
    assert "blend" in sources and sources[-1] == "astral"
    assert min(sent_at) >= 10.0                                        # nothing changes before stale
    bs = [m["brightness"] for m in pages.of()]
    assert bs == sorted(bs, reverse=True) and bs[-1] < 0.1


def test_arbiter_held_pushes_nothing(tmp_path):
    c = Clock()
    arb, pages = arbiter(tmp_path, c)
    ready_overrides(arb, c)
    seq = 1
    arb.on_reading(SENSOR, 50.0, seq, False, c.t)
    n = len(pages.of())
    assert n == 1
    for lux in (2000, 0, 2000, 0, 2000):
        seq += 1
        arb.on_reading(SENSOR, lux, seq, True, c.advance(0.7))
        ready_overrides(arb, c)
        arb._evaluate_all()
        arb._flush("devtest")
    assert len(pages.of()) == n and arb.tier["devtest"] == T_SENSOR      # held: no mask, no Flask
    seq += 1
    arb.on_reading(SENSOR, 2000, seq, False, c.advance(0.1))
    seq += 1
    arb.on_reading(SENSOR, 2000, seq, False, c.advance(0.1))           # confirmed: the snap goes
    assert len(pages.of()) == n + 1 and pages.of()[-1]["transition_ms"] == 200


def test_arbiter_step_snaps_at_once_and_drift_is_paced(tmp_path):
    c = Clock()
    arb, pages = arbiter(tmp_path, c)
    ready_overrides(arb, c)
    seq = 1
    arb.on_reading(SENSOR, 5.0, seq, False, c.t)
    sends: list[tuple[float, dict]] = []
    seen = len(pages.of())

    def step(lux):
        nonlocal seq, seen
        seq += 1
        c.advance(0.1)
        ready_overrides(arb, c)
        arb.on_reading(SENSOR, lux, seq, False, c.t)
        arb._flush("devtest")
        for m in pages.of()[seen:]:
            sends.append((c.t, m))
        seen = len(pages.of())

    for _ in range(3):
        step(5.0)
    t_step = c.t
    step(40.0)                                     # first far sample: the spike guard
    assert not sends
    step(40.0)                                     # confirmed: sent while handling this reading
    assert sends and sends[0][0] == pytest.approx(c.t) and sends[0][1]["transition_ms"] == 200
    for _ in range(100):                           # 10 s at 10 Hz: snap, then drift
        step(40.0)
    kinds = [m["transition_ms"] for _, m in sends]
    assert kinds[0] == 200 and 1000 in kinds
    drift_t = [t for t, m in sends if m["transition_ms"] == 1000]
    assert all(b - a >= 3.0 - 1e-6 for a, b in zip(drift_t, drift_t[1:]))
    assert sends[-1][1]["brightness"] == pytest.approx(lm.intensity_for(arb.sensors.config, SENSOR, 40.0), abs=0.016)
    assert t_step < sends[0][0] < t_step + 0.25


def test_arbiter_frozen_and_no_page_hold_then_catch_up(tmp_path):
    c = Clock()
    cfg, _ = room_cfg(tmp_path)
    write_keys(tmp_path / "room_sensors.json", {SENSOR: KEY})
    frozen = {"devtest": False}
    pages = StubPages(cfg.screens)
    arb = MaskArbiter(cfg, None, pages, is_frozen=lambda s: frozen.get(s, False), clock=c, wall=lambda: NOON)
    arb.keys.refresh()
    arb.sensors.refresh_config()
    ready_overrides(arb, c)
    seq = 1
    arb.on_reading(SENSOR, 5.0, seq, False, c.t)
    assert len(pages.of()) == 1
    frozen["devtest"] = True                                     # a cut is in progress
    for lux in (40.0, 40.0, 40.0):
        seq += 1
        arb.on_reading(SENSOR, lux, seq, False, c.advance(0.1))
    assert len(pages.of()) == 1 and arb.shapers["devtest"].pending is not None
    frozen["devtest"] = False
    arb.on_unfrozen("devtest")                                   # the latest value goes at once
    assert len(pages.of()) == 2 and pages.of()[-1]["brightness"] > 0.5
    pages.connected.discard("devtest")                           # nobody connected: kept for later
    for lux in (5.0, 5.0, 5.0):
        seq += 1
        arb.on_reading(SENSOR, lux, seq, False, c.advance(0.1))
    assert len(pages.of()) == 2 and arb.shapers["devtest"].pending is not None
    pages.connected.add("devtest")
    arb._after_welcome("devtest")
    assert len(pages.of()) == 3 and pages.of()[-1]["brightness"] < 0.5
