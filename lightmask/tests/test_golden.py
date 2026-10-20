"""lightmask against Flask's own outputs (fixtures from gen_golden.py).

Three comparisons per fixture set ("real" = today's config files,
"synthetic" = extra destinations/sensors covering every branch):

1. route/new API: ``mask_for_dest`` (what room_service uses) against
   GET /api/<id>/mask: brightness, warm_hex, warm_alpha, source, sensor name.
2. route/compat: ``flask_mask_route`` against the whole route payload
   (timestamp, blend, sensor {name, lux} included).
3. compute/compat: ``flask_compute_mask`` against compute_mask's whole
   payload, _debug included.

All comparisons are exact (==). The only tolerance the spec allows is 1e-3
on rounded floats; it has not been needed.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

import lightmask as lm
from lightmask import model

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "golden_flask.json.gz"
REPO = HERE.parent.parent

with gzip.open(FIXTURE, "rt", encoding="utf-8") as _f:
    GOLD = json.load(_f)

OVERRIDES = {
    "none": lm.Overrides(),
    "maskon": lm.Overrides(mask_enabled=True),
    "maskoff": lm.Overrides(mask_enabled=False),
    "bright0.4": lm.Overrides(brightness=0.4),
    "maskoff+bright0.4": lm.Overrides(mask_enabled=False, brightness=0.4),
}
SETS = sorted(GOLD["sets"])


def config_for(name: str) -> lm.LightConfig:
    inp = GOLD["sets"][name]["inputs"]
    return lm.LightConfig.from_data(inp["intensity_cfg"], inp["intensity_mapping"], inp["destinations"])


def reader(cfg: lm.LightConfig, lux: dict | None):
    lux = lux or {}

    def read(name):
        if name not in lux:
            return None
        return lm.intensity_for(cfg, name, lux[name]), lux[name]
    return read


def jsonable(v):
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def roundtrip(v):
    return json.loads(json.dumps(jsonable(v)))


def _report(bad, total):
    lines = [f"{len(bad)}/{total} cases differ; first ones:"]
    for case, got in bad[:8]:
        lines.append(json.dumps({"case": {k: case[k] for k in case if k != "expect"},
                                 "flask": case["expect"], "lightmask": got}, default=str))
    return "\n".join(lines)


@pytest.mark.parametrize("set_name", SETS)
def test_route_new_api(set_name):
    cfg = config_for(set_name)
    cases = GOLD["sets"][set_name]["route"]
    assert cases
    bad = []
    for case in cases:
        now = datetime.fromisoformat(case["now"])
        sensors = None
        if case["lux"]:
            sensors = {n: lm.intensity_for(cfg, n, v) for n, v in case["lux"].items()}
        got = lm.mask_for_dest(cfg, case["dest"], now, OVERRIDES[case["override"]], sensors)
        exp = case["expect"]
        ok = (got is not None
              and got["brightness"] == exp["brightness"]
              and got["warm_hex"] == exp["warm_hex"]
              and got["warm_alpha"] == exp["warm_alpha"])
        if ok and "source" in exp:
            ok = got["source"] == exp["source"]
        elif ok:
            # Flask sends no source for maskoff / adjust:false; lightmask names them
            ov = OVERRIDES[case["override"]]
            ok = got["source"] == (lm.SOURCE_MASKOFF if not ov.mask_enabled else lm.SOURCE_FIXED)
        if ok and "sensor" in exp:
            ok = got.get("sensor") == exp["sensor"]["name"]
        if ok and "sensor" not in exp:
            ok = "sensor" not in got
        if not ok:
            bad.append((case, got))
    assert not bad, _report(bad, len(cases))


@pytest.mark.parametrize("set_name", SETS)
def test_route_compat_payload(set_name):
    cfg = config_for(set_name)
    cases = GOLD["sets"][set_name]["route"]
    bad = []
    for case in cases:
        now = datetime.fromisoformat(case["now"])
        got = lm.flask_mask_route(cfg, case["dest"], now, OVERRIDES[case["override"]],
                                  sensor_intensity=reader(cfg, case["lux"]))
        if roundtrip(got) != case["expect"]:
            bad.append((case, got))
    assert not bad, _report(bad, len(cases))


@pytest.mark.parametrize("set_name", SETS)
def test_compute_compat_payload(set_name):
    cfg = config_for(set_name)
    cases = GOLD["sets"][set_name]["compute"]
    assert cases
    bad = []
    for case in cases:
        now = datetime.fromisoformat(case["now"])
        got = lm.flask_compute_mask(cfg, case["lat"], case["lon"], screen_cfg=case["screen_cfg"], time=now,
                                    sensor_intensity=reader(cfg, case["lux"]))
        if roundtrip(got) != case["expect"]:
            bad.append((case, got))
    assert not bad, _report(bad, len(cases))


def test_grid_covers_the_spec():
    real = GOLD["sets"]["real"]
    luxes = {c["lux"]["living-room"] for c in real["route"] if c["lux"]}
    assert luxes == {0, 0.5, 1, 2, 5, 10, 22, 50, 100, 500}
    assert {c["date"] for c in real["route"]} >= {"summer", "winter"}
    assert {c["override"] for c in real["route"]} >= {"none", "maskoff", "bright0.4"}
    dests = {d["id"] for d in real["inputs"]["destinations"]}
    assert {c["dest"] for c in real["route"]} == dests
    sources = {c["expect"].get("source") for c in real["route"]}
    assert {"sensor", "astral", "manual_override", None} <= sources
    # the solar grid really spans day and night in both seasons
    for season in ("summer", "winter"):
        b = [c["expect"]["brightness"] for c in real["route"]
             if c["dest"] == "north-screen" and c["date"] == season and c["lux"] is None and c["override"] == "none"]
        assert min(b) < 0.05 and max(b) > 0.6


def _sha16(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def test_load_config_reads_what_flask_read():
    """load_config() on the repo files gives the inputs Flask used for the
    "real" fixtures. Skipped (not failed) when those files changed since the
    fixtures were generated: regenerate with gen_golden.py."""
    shas = GOLD["flask_sources_sha256_16"]
    cfg = lm.load_config()
    dest_rel = GOLD["destinations_file"]
    assert Path(cfg.sources["destinations_json"]) == (REPO / dest_rel).resolve()
    inputs = ("config.py", "routes/data/intensity.json", dest_rel)
    changed = [p for p in inputs if _sha16(REPO / p) != shas[p]]
    if changed:
        pytest.skip(f"{changed} changed since fixtures were generated; rerun lightmask/tests/gen_golden.py")
    real = GOLD["sets"]["real"]["inputs"]
    assert dict(cfg.intensity_cfg) == real["intensity_cfg"]
    want = config_for("real")
    assert {k: (v.target_group, v.points) for k, v in cfg.sensor_mappings.items()} == \
        {k: (v.target_group, v.points) for k, v in want.sensor_mappings.items()}
    logic = [{k: model._thaw(d[k]) for k in ("id", "has_bucket", "groups", "intensity_cfg") if k in d}
             for d in cfg.destinations]
    assert logic == real["destinations"]
