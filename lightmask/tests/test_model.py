"""Unit tests for lightmask.model beyond the golden grid."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import lightmask as lm
from lightmask import model

REPO = Path(__file__).resolve().parent.parent.parent

BASE_CFG = {
    "night_floor": 0.025, "gamma_brightness": 1.0, "warmest_temp_K": 2200, "coolest_temp_K": 6500,
    "beta_colour": 1.2, "warm_alpha_max": 0.10, "dusk_offset_deg": -6, "elev_range_deg": 96,
    "smoothstep": False, "east_west_bias": 0.0, "seasonality_factor": 0.0, "day_stretch": 0.2,
    "default_lat": 51.5074, "default_lon": -0.1278,
}
CURVE = {"sensor_mappings": {"living-room": {"target_group": "living-room", "lux_to_intensity": [
    {"lux": 100, "intensity": 1.0}, {"lux": 22, "intensity": 0.5}, {"lux": 0, "intensity": 0.01}]}}}
DESTS = [
    {"id": "a", "has_bucket": True, "groups": ["living-room"],
     "intensity_cfg": {"lat": 51.5, "lon": -0.12, "adjust": True}},
    {"id": "b", "has_bucket": True, "groups": ["living-room"]},
    {"id": "c", "groups": ["living-room"], "intensity_cfg": {"adjust": True, "lat": 51.5, "lon": -0.12}},
    {"id": "d", "has_bucket": True, "groups": ["work"], "intensity_cfg": {"adjust": True}},
]


def cfg(dests=DESTS, curve=CURVE, base=BASE_CFG) -> lm.LightConfig:
    return lm.LightConfig.from_data(base, curve, dests)


def write_tree(root: Path, *, base=BASE_CFG, curve=CURVE, dests=DESTS, dest_dir="src/data"):
    (root / "routes" / "data").mkdir(parents=True, exist_ok=True)
    (root / dest_dir).mkdir(parents=True, exist_ok=True)
    (root / "config.py").write_text("import os\nX = 1\nINTENSITY_CFG = " + repr(base) + "\n")
    (root / "routes" / "data" / "intensity.json").write_text(json.dumps(curve))
    (root / dest_dir / "publish-destinations.json").write_text(json.dumps(dests))


def bump_mtime(p: Path):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


# ───────── import hygiene ────────────────────────────────────────────────

def test_import_pulls_in_no_flask_or_routes():
    code = ("import sys, lightmask; bad=[m for m in sys.modules if m=='flask' or m.startswith('flask.') "
            "or m=='routes' or m.startswith('routes.') or m=='config']; print(bad)")
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(REPO), capture_output=True, text=True,
                         check=True).stdout.strip()
    assert out == "[]"


def test_mask_for_does_not_mutate_inputs():
    base = dict(BASE_CFG)
    dest = {"id": "x", "intensity_cfg": {"adjust": True, "lat": 51.5, "lon": 0.0, "night_floor": 0.4,
                                         "smoothstep": 1, "east_west_bias": "0.5"}}
    snap_base, snap_dest = json.dumps(base), json.dumps(dest)
    ref_white = model._REF_WHITE
    m = lm.mask_for(dest, None, datetime(2026, 1, 1, 3, tzinfo=timezone.utc), base_cfg=base)
    assert m["brightness"] == 0.4          # the per-screen night_floor applied...
    assert json.dumps(base) == snap_base   # ...without touching the base (Flask mutates its exec'd copy)
    assert json.dumps(dest) == snap_dest
    assert model._REF_WHITE == ref_white


# ───────── config loading ────────────────────────────────────────────────

def test_load_config_from_tree(tmp_path):
    write_tree(tmp_path)
    c = lm.load_config(lm.ConfigPaths(root=tmp_path))
    assert dict(c.intensity_cfg) == BASE_CFG
    assert c.sensor_mappings["living-room"].points == ((0, 0.01), (22, 0.5), (100, 1.0))
    assert [d["id"] for d in c.destinations] == ["a", "b", "c", "d"]
    assert not (tmp_path / "__pycache__").exists()   # config.py exec'd without writing bytecode


def test_destinations_search_order_matches_flask(tmp_path):
    write_tree(tmp_path, dest_dir="routes/data")
    assert lm.find_destinations_file(tmp_path) == (tmp_path / "routes/data/publish-destinations.json").resolve()
    (tmp_path / "src/data").mkdir(parents=True)
    (tmp_path / "src/data/publish-destinations.json").write_text("[]")
    assert lm.find_destinations_file(tmp_path) == (tmp_path / "src/data/publish-destinations.json").resolve()
    (tmp_path / "publish-destinations.json").write_text("[]")
    assert lm.find_destinations_file(tmp_path) == (tmp_path / "publish-destinations.json").resolve()


def test_missing_intensity_json_means_no_sensors(tmp_path):
    write_tree(tmp_path)
    (tmp_path / "routes/data/intensity.json").unlink()
    c = lm.load_config(lm.ConfigPaths(root=tmp_path))
    assert dict(c.sensor_mappings) == {}
    assert lm.resolve_targets(c, "living-room") == []


def test_watcher_reloads_on_mtime_and_keeps_last_good(tmp_path):
    write_tree(tmp_path)
    clock = [0.0]
    w = lm.ConfigWatcher(lm.ConfigPaths(root=tmp_path), min_interval_s=1.0, clock=lambda: clock[0])
    c1 = w.get()
    assert w.reloads == 1
    # unchanged files: no reload
    clock[0] = 5.0
    assert w.get() is c1
    # curve edited (LightSense editor) -> picked up on the next check
    curve2 = json.loads(json.dumps(CURVE))
    curve2["sensor_mappings"]["living-room"]["lux_to_intensity"][1]["intensity"] = 0.6
    p = tmp_path / "routes/data/intensity.json"
    p.write_text(json.dumps(curve2))
    bump_mtime(p)
    clock[0] = 5.5          # inside min_interval: not checked yet
    assert w.get() is c1
    clock[0] = 6.1
    c2 = w.get()
    assert c2 is not c1 and lm.intensity_for(c2, "living-room", 22) == 0.6
    # half-written file: keep c2, report the error
    p.write_text('{"sensor_mappings": ')
    bump_mtime(p)
    assert w.refresh() is False
    assert w.current is c2 and "JSONDecodeError" in (w.last_error or "")
    # config.py edited -> re-exec'd
    base2 = dict(BASE_CFG, night_floor=0.2)
    cp = tmp_path / "config.py"
    cp.write_text("INTENSITY_CFG = " + repr(base2) + "\n")
    bump_mtime(cp)
    p.write_text(json.dumps(CURVE))
    bump_mtime(p)
    assert w.refresh() is True
    assert w.current.intensity_cfg["night_floor"] == 0.2 and w.last_error is None


def test_watcher_without_any_good_config_raises(tmp_path):
    w = lm.ConfigWatcher(lm.ConfigPaths(root=tmp_path))
    with pytest.raises(RuntimeError):
        w.get()


# ───────── targets and curve ─────────────────────────────────────────────

def test_destinations_for_group_rules():
    dests = DESTS + [{"id": "work", "has_bucket": True, "groups": ["x"]}, {"id": "nb", "groups": ["work"]}]
    assert lm.destinations_for_group(dests, "living-room") == ["a", "b"]          # c has no bucket
    assert lm.destinations_for_group(dests, "global") == ["a", "b", "d", "work"]
    assert lm.destinations_for_group(dests, "a") == ["a"]                          # by id
    assert lm.destinations_for_group(dests, "work") == ["work"]                    # id beats group name
    assert lm.destinations_for_group(dests, "c") == []                             # id without bucket
    c = cfg()
    assert lm.resolve_targets(c, "living-room") == ["a", "b"]
    assert lm.resolve_targets(c, "nope") == []
    assert lm.sensors_for_dest(c, "a") == ["living-room"] and lm.sensors_for_dest(c, "d") == []


@pytest.mark.parametrize("lux, want", [
    (0, 0.01), (11, 0.01 + (0.5 - 0.01) * 11 / 22), (22, 0.5), (61, 0.75), (100, 1.0), (5000, 1.0), (-3, 0.01),
])
def test_curve(lux, want):
    assert lm.intensity_for(cfg(), "living-room", lux) == pytest.approx(want, abs=1e-12)


def test_curve_edges():
    assert lm.intensity_for(cfg(), "unknown", 10) is None
    assert lm.curve_intensity((), 5) is None
    assert lm.curve_intensity(((5, 0.2), (5, 0.4), (10, 0.9)), 5) == 0.4     # zero-width segment skipped
    assert lm.curve_intensity(((5, 0.2),), 1) == 0.2 and lm.curve_intensity(((5, 0.2),), 9) == 0.2


# ───────── overrides and branches ────────────────────────────────────────

def test_override_precedence():
    c = cfg()
    now = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
    a = c.destination("a")
    assert lm.mask_for(a, 0.3, now, lm.Overrides(brightness=0.4), base_cfg=c.intensity_cfg) == \
        {"brightness": 0.4, "warm_hex": "#FFFFFF", "warm_alpha": 0.0, "source": "manual_override"}
    assert lm.mask_for(a, 0.3, now, {"maskoff": True, "brightness": 0.7}, base_cfg=c.intensity_cfg)["brightness"] == 0.7
    assert lm.mask_for(a, 0.3, now, lm.Overrides(mask_enabled=False), base_cfg=c.intensity_cfg)["source"] == "maskoff"
    assert lm.mask_for(c.destination("b"), 0.3, now, None, base_cfg=c.intensity_cfg)["source"] == "fixed"
    assert lm.mask_for(a, 0.3, now, None, base_cfg=c.intensity_cfg)["source"] == "sensor"
    assert lm.mask_for(a, None, now, None, base_cfg=c.intensity_cfg)["source"] == "astral"


def test_overrides_from_flask_bodies():
    assert lm.Overrides.from_flask({"enabled": False, "destination": "a"},
                                   {"destination": "a", "override": None, "active": False}) == \
        lm.Overrides(mask_enabled=False, brightness=None)
    assert lm.Overrides.from_flask({"enabled": True}, {"override": 0.25, "active": True}) == \
        lm.Overrides(mask_enabled=True, brightness=0.25)
    assert lm.Overrides.from_flask(None, None) == lm.Overrides()


def test_times_are_utc_normalised():
    c = cfg()
    a = c.destination("a")
    utc = datetime(2026, 12, 21, 15, 30, tzinfo=timezone.utc)
    naive = utc.replace(tzinfo=None)
    local = utc.astimezone(ZoneInfo("America/New_York"))       # different local date near midnight
    ms = [lm.mask_for(a, None, t, base_cfg=c.intensity_cfg) for t in (utc, naive, local)]
    assert ms[0] == ms[1] == ms[2]
    late = datetime(2026, 12, 22, 2, 0, tzinfo=timezone.utc)   # 21 Dec in New York
    assert lm.mask_for(a, None, late.astimezone(ZoneInfo("America/New_York")), base_cfg=c.intensity_cfg) == \
        lm.mask_for(a, None, late, base_cfg=c.intensity_cfg)


def test_missing_latlon_falls_back_to_defaults():
    c = cfg()
    d = c.destination("d")                                     # adjust but no lat/lon (Flask: KeyError)
    t = datetime(2026, 6, 21, 20, tzinfo=timezone.utc)
    ref = {"id": "r", "intensity_cfg": {"adjust": True, "lat": 51.5074, "lon": -0.1278}}
    assert lm.mask_for(d, None, t, base_cfg=c.intensity_cfg) == lm.mask_for(ref, None, t, base_cfg=c.intensity_cfg)


def test_polar_latitude_does_not_raise():
    dest = {"id": "p", "intensity_cfg": {"adjust": True, "lat": 78.2, "lon": 15.6}}
    for month in (6, 12):
        m = lm.mask_for(dest, None, datetime(2026, month, 21, 12, tzinfo=timezone.utc), base_cfg=BASE_CFG)
        assert 0 <= m["brightness"] <= 1


def test_solar_curve_shape_london():
    c = cfg()
    a = c.destination("a")
    day = [lm.mask_for(a, None, datetime(2026, 6, 21, h, tzinfo=timezone.utc), base_cfg=c.intensity_cfg)
           for h in range(24)]
    assert day[12]["brightness"] == 1.0 and day[12]["warm_alpha"] == 0.0
    assert day[0]["brightness"] == 0.025 and day[0]["warm_alpha"] == 0.1
    assert day[0]["warm_hex"] != "#FFFFFF"


# ───────── mask_for_dest: sensor choice, staleness blend ─────────────────

def test_mask_for_dest_sensor_and_blend():
    c = cfg()
    now = datetime(2026, 12, 21, 21, tzinfo=timezone.utc)      # night: solar is at the floor
    fresh = lm.mask_for_dest(c, "a", now, None, {"living-room": lm.SensorInput(0.8, 0.0)})
    assert fresh["source"] == "sensor" and fresh["brightness"] == 0.8 and fresh["sensor"] == "living-room"
    solar = lm.mask_for_dest(c, "a", now, None, {})
    assert solar["source"] == "astral" and solar["brightness"] == 0.025
    assert lm.mask_for_dest(c, "a", now, None, {"living-room": lm.SensorInput(0.8, 1.0)}) == solar
    half = lm.mask_for_dest(c, "a", now, None, {"living-room": lm.SensorInput(0.8, 0.5)})
    assert half["source"] == "blend" and half["solar_weight"] == 0.5
    assert half["brightness"] == pytest.approx((0.8 + 0.025) / 2, abs=1e-4)
    assert half["warm_alpha"] == pytest.approx((fresh["warm_alpha"] + solar["warm_alpha"]) / 2, abs=1e-4)
    # a bare float is accepted; a sensor with no reading is skipped
    assert lm.mask_for_dest(c, "a", now, None, {"living-room": 0.8}) == fresh
    assert lm.mask_for_dest(c, "a", now, None, {"living-room": lm.SensorInput(None)}) == solar
    # dest outside the sensor's targets ignores it; unknown dest -> None
    assert lm.mask_for_dest(c, "c", now, None, {"living-room": 0.8})["source"] == "astral"
    assert lm.mask_for_dest(c, "zzz", now) is None
    # overrides beat the sensor
    assert lm.mask_for_dest(c, "a", now, lm.Overrides(brightness=0.3), {"living-room": 0.8})["source"] == "manual_override"


def test_solar_mask_ignores_sensor_and_overrides():
    c = cfg()
    now = datetime(2026, 6, 21, 21, 30, tzinfo=timezone.utc)
    assert lm.solar_mask(c, "a", now) == lm.mask_for_dest(c, "a", now)
    assert lm.solar_mask(c, "nope", now) is None


def test_blend_masks_endpoints_and_hex():
    a = {"brightness": 1.0, "warm_hex": "#FFFFFF", "warm_alpha": 0.0, "source": "sensor"}
    b = {"brightness": 0.0, "warm_hex": "#000000", "warm_alpha": 0.1, "source": "astral"}
    assert lm.blend_masks(a, b, 0) == a and lm.blend_masks(a, b, 1.2) == b
    m = lm.blend_masks(a, b, 0.25)
    assert m["warm_hex"] == "#BFBFBF" and m["brightness"] == 0.75 and m["warm_alpha"] == 0.025


def test_snapshot_is_read_only():
    c = cfg()
    with pytest.raises(TypeError):
        c.intensity_cfg["night_floor"] = 1          # type: ignore[index]
    with pytest.raises(TypeError):
        c.destination("a")["intensity_cfg"]["adjust"] = False   # type: ignore[index]
    d = c.destination_dict("a")
    d["intensity_cfg"]["adjust"] = False
    assert c.destination("a")["intensity_cfg"]["adjust"] is True


def test_mask_for_speed():
    """room_service may evaluate a few screens at 20 Hz; keep a mask well under 1 ms."""
    c = cfg()
    a = c.destination("a")
    now = datetime(2026, 6, 21, 18, tzinfo=timezone.utc)
    n = 300
    t0 = time.perf_counter()
    for i in range(n):
        lm.mask_for(a, None, now + timedelta(seconds=i), base_cfg=c.intensity_cfg)
    solar_ms = (time.perf_counter() - t0) / n * 1e3
    t0 = time.perf_counter()
    for i in range(n):
        lm.mask_for(a, 0.5, now, base_cfg=c.intensity_cfg)
    sensor_ms = (time.perf_counter() - t0) / n * 1e3
    assert solar_ms < 2.0 and sensor_ms < 0.5, (solar_ms, sensor_ms)


def test_config_py_exec_matches_exec_module(tmp_path):
    """config.py is compiled without inheriting lightmask's own __future__ flags
    (``from __future__ import annotations`` would turn its annotations into
    strings) and is never registered in sys.modules."""
    import importlib.util
    src = ("from pathlib import Path\nLIMIT: int = 5\nROOT = Path(__file__).parent\n"
           "INTENSITY_CFG = {'ann': repr(__annotations__['LIMIT']), 'name': __name__, 'root': str(ROOT)}\n")
    p = tmp_path / "config.py"
    p.write_text(src)
    before = set(sys.modules)
    got = model.read_intensity_cfg(p)
    assert set(sys.modules) - before <= {"pathlib"}
    spec = importlib.util.spec_from_file_location("config", str(p))
    mod = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode, old = True, sys.dont_write_bytecode
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    assert got == mod.INTENSITY_CFG == {"ann": "<class 'int'>", "name": "config", "root": str(tmp_path)}
    assert not (tmp_path / "__pycache__").exists()
