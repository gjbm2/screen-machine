#!/usr/bin/env python
"""Generate lightmask's golden fixtures from today's Flask code.

Run from anywhere, with the interpreter that has Flask's deps (read-only use):

    /home/gjbm2/dev/screen-machine/.venv/bin/python -I -B lightmask/tests/gen_golden.py
    /home/gjbm2/dev/screen-machine/.venv/bin/python -I -B lightmask/tests/gen_golden.py --verify-switch

The second form writes nothing: it runs the grid through Flask's route twice,
once as is and once with ``compute_mask`` replaced by
``lightmask.flask_compute_mask`` (fed by Flask's own lux_history and EMA),
and reports any difference. It is the check for the later Flask switch.

* ``-B`` (and ``sys.dont_write_bytecode`` below): nothing is written under
  routes/ or the repo root; the only output is the fixture file.
* No Flask app is served and no socket is opened. ``utils.logger`` (it opens
  logs/screen_machine.log) and ``overlay_ws_server`` (unused by these
  routes) are replaced by in-memory stubs before ``routes.*`` is imported.
* Determinism: ``routes.lightsensor.lux_history`` is set to one sample per
  sensor (so the 5-sample moving average returns that lux exactly),
  ``intensity_mapper.smoothed_values`` is cleared before every call (so the
  read-driven EMA returns the curve value), and ``datetime.utcnow`` in
  routes.display / routes.display_utils is frozen per case.

The fixture holds the inputs it used (INTENSITY_CFG, the intensity.json
mapping, the destination fields the mapping reads) so the pytest side is
hermetic. Two input sets: "real" (today's files) and "synthetic" (extra
destinations and sensor mappings exercising every branch: seasonality,
smoothstep, negative/maximal bias, day_stretch 0, string/int casts, group vs
id vs "global" targets, has_bucket, sensor order).
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = HERE / "fixtures" / "golden_flask.json.gz"
LONDON = ZoneInfo("Europe/London")

# ───────── stubs (before any routes.* import) ─────────────────────────────

_logger = types.ModuleType("utils.logger")
for _n in ("log_to_console", "info", "error", "warning", "debug"):
    setattr(_logger, _n, lambda *a, **k: None)
_logger.console_logs = []
sys.modules["utils.logger"] = _logger

_ows = types.ModuleType("overlay_ws_server")


async def _no_send(*a, **k):  # pragma: no cover - never awaited here
    raise RuntimeError("gen_golden must not send overlays")


_ows.send_overlay_to_clients = _no_send
sys.modules["overlay_ws_server"] = _ows

os.chdir(REPO)                      # compute_mask execs "config.py" relative to cwd
sys.path.insert(0, str(REPO))

import routes.display as rdisplay          # noqa: E402
import routes.display_utils as rdu         # noqa: E402
import routes.lightsensor as rls           # noqa: E402
import routes.utils as rutils              # noqa: E402
from flask import Flask                    # noqa: E402

APP = Flask("lightmask_golden")


class FrozenDateTime(datetime):
    frozen: datetime | None = None   # naive UTC, like datetime.utcnow()

    @classmethod
    def utcnow(cls):
        if cls.frozen is None:
            raise RuntimeError("FrozenDateTime.frozen not set")
        return cls.frozen


rdu.datetime = FrozenDateTime
rdisplay.datetime = FrozenDateTime


# ───────── inputs ────────────────────────────────────────────────────────

def flask_intensity_cfg() -> dict:
    spec = importlib.util.spec_from_file_location("config", "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.INTENSITY_CFG)


LOGIC_KEYS = ("id", "has_bucket", "groups", "intensity_cfg")


def strip_dest(d: dict) -> dict:
    return {k: copy.deepcopy(d[k]) for k in LOGIC_KEYS if k in d}


REAL_DESTS = [strip_dest(d) for d in rutils._load_json_once("destination", "publish-destinations.json")]
REAL_MAPPING = copy.deepcopy(rls.intensity_mapper.mapping_data)

SYN_DESTS = REAL_DESTS + [
    {"id": "syn-west", "has_bucket": True, "groups": ["drawing-room"],
     "intensity_cfg": {"lat": 51.5074, "lon": -0.1278, "east_west_bias": -0.6, "day_stretch": 0, "adjust": True}},
    {"id": "syn-season", "has_bucket": True, "groups": ["hall-group"],
     "intensity_cfg": {"lat": 55.9533, "lon": -3.1883, "seasonality_factor": 0.5, "smoothstep": True,
                       "day_stretch": -0.5, "night_floor": 0.1, "warm_alpha_max": 0.05, "beta_colour": 2,
                       "warmest_temp_K": 1800, "dusk_offset_deg": -4, "adjust": True}},
    {"id": "syn-nyc", "has_bucket": True, "groups": ["work"],
     "intensity_cfg": {"lat": "40.7128", "lon": "-74.0060", "east_west_bias": 1.0, "adjust": True}},
    {"id": "syn-nobucket", "groups": ["drawing-room"],
     "intensity_cfg": {"lat": 51.5074, "lon": -0.1278, "adjust": True}},
    {"id": "syn-adjust-false", "has_bucket": True, "groups": ["drawing-room"],
     "intensity_cfg": {"lat": 51.5074, "lon": -0.1278, "adjust": False, "night_floor": 0.5}},
    {"id": "syn-sensor-id", "has_bucket": True, "groups": ["drawing-room"],
     "intensity_cfg": {"lat": 50.0, "lon": 5.0, "smoothstep": 1, "east_west_bias": "0.3",
                       "coolest_temp_K": 5000, "adjust": True}},
]
SYN_MAPPING = {"sensor_mappings": {
    "hall": {"target_group": "hall-group",
             "lux_to_intensity": [{"lux": 1000, "intensity": 0.9}, {"lux": 0, "intensity": 0.2}]},
    "living-room": copy.deepcopy(REAL_MAPPING["sensor_mappings"]["living-room"]),
    "nowhere": {"target_group": "",
                "lux_to_intensity": [{"lux": 0, "intensity": 0.5}, {"lux": 10, "intensity": 0.6}]},
    "desk": {"target_group": "syn-sensor-id",
             "lux_to_intensity": [{"lux": 10, "intensity": 0.3}, {"lux": 5, "intensity": 0.1},
                                  {"lux": 300, "intensity": 1}]},
    "everywhere": {"target_group": "global",
                   "lux_to_intensity": [{"lux": 0, "intensity": 0.05}, {"lux": 50, "intensity": 0.5},
                                        {"lux": 2000, "intensity": 0.95}]},
}}


# ───────── grid ──────────────────────────────────────────────────────────

LUX_GRID = [0, 0.5, 1, 2, 5, 10, 22, 50, 100, 500]
DATES = {
    "summer": date(2026, 6, 21),
    "winter": date(2026, 12, 21),
    "dst_spring": date(2026, 3, 29),    # 01:00 GMT -> 02:00 BST
    "dst_autumn": date(2026, 10, 25),   # 02:00 BST -> 01:00 GMT
}


def london_day_utc(d: date, step: timedelta, offset: timedelta = timedelta(0)) -> list[datetime]:
    """UTC instants from London local midnight to the next local midnight."""
    start = datetime(d.year, d.month, d.day, tzinfo=LONDON).astimezone(timezone.utc)
    nd = d + timedelta(days=1)
    end = datetime(nd.year, nd.month, nd.day, tzinfo=LONDON).astimezone(timezone.utc)
    out, t = [], start + offset
    while t < end:
        out.append(t)
        t += step
    return out


def all_times(step_min: int) -> list[tuple[str, datetime]]:
    out = []
    for label, d in DATES.items():
        for t in london_day_utc(d, timedelta(minutes=step_min)):
            out.append((label, t))
        for t in london_day_utc(d, timedelta(hours=2), timedelta(minutes=7, seconds=13)):
            out.append((label, t))
    return out


def few_times() -> list[tuple[str, datetime]]:
    # one daytime and one night-time instant per date (local 13:20 and 22:40)
    out = []
    for label, d in DATES.items():
        for hh, mm in ((13, 20), (22, 40)):
            out.append((label, datetime(d.year, d.month, d.day, hh, mm, tzinfo=LONDON).astimezone(timezone.utc)))
    return out


OVERRIDE_STATES = {
    "none": (None, None),
    "maskon": (True, None),
    "maskoff": (False, None),
    "bright0.4": (None, 0.4),
    "maskoff+bright0.4": (False, 0.4),
}


# ───────── drivers ───────────────────────────────────────────────────────

SWITCH: dict = {"on": False, "cfg": None}


def use_inputs(dests: list, mapping: dict | None):
    if SWITCH["on"]:
        import lightmask
        if dests is REAL_DESTS:     # the real files, loaded the lightmask way
            SWITCH["cfg"] = lightmask.load_config()
        else:
            SWITCH["cfg"] = lightmask.LightConfig.from_data(flask_intensity_cfg(), mapping, dests)

    def loader(cache_key, filename, force_reload=False):
        if filename == "publish-destinations.json":
            return dests
        raise AssertionError(f"unexpected load {filename}")
    rutils._load_json_once = loader
    rdisplay._load_json_once = loader
    rls.intensity_mapper.mapping_data = copy.deepcopy(mapping)


def set_state(t_utc: datetime, lux: dict | None, override: str, dest_id: str):
    FrozenDateTime.frozen = t_utc.astimezone(timezone.utc).replace(tzinfo=None)
    rls.lux_history.clear()
    for name, v in (lux or {}).items():
        rls.lux_history[name] = [(0.0, v)]
    rls.intensity_mapper.smoothed_values.clear()
    rdisplay.mask_states.clear()
    rdisplay.brightness_overrides.clear()
    enabled, bright = OVERRIDE_STATES[override]
    if enabled is not None:
        rdisplay.mask_states[dest_id] = enabled
    if bright is not None:
        rdisplay.brightness_overrides[dest_id] = bright


def jsonable(v):
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def run_route(dest_id, t, lux, override):
    set_state(t, lux, override, dest_id)
    with APP.test_request_context(f"/api/{dest_id}/mask"):
        resp = rdisplay.mask(dest_id)
        return json.loads(resp.get_data(as_text=True))


def run_compute(lat, lon, screen_cfg, t, lux):
    set_state(t, lux, "none", "")
    return jsonable(rdu.compute_mask(lat, lon, screen_cfg=screen_cfg, time=t))


def _adjusts(d: dict) -> bool:
    return bool((d.get("intensity_cfg") or {}).get("adjust", False))


def build_set(dests, mapping, sensor_scenarios, solar_times, other_times, sensor_times, compute_dests,
              sensor_overrides=tuple(OVERRIDE_STATES), full_solar_ids=None):
    """Route cases for every destination; screens that never adjust (constant
    output) and ids not in ``full_solar_ids`` get the short time list."""
    use_inputs(dests, mapping)
    route, compute = [], []
    for d in dests:
        dest_id = d["id"]
        full = _adjusts(d) and (full_solar_ids is None or dest_id in full_solar_ids)
        s_times = solar_times if full else other_times
        x_times = sensor_times if _adjusts(d) else sensor_times[:1]
        for label, t in s_times:
            route.append({"dest": dest_id, "now": t.isoformat(), "date": label, "override": "none",
                          "lux": None, "expect": run_route(dest_id, t, None, "none")})
        for override in OVERRIDE_STATES:
            if override == "none":
                continue
            for label, t in other_times:
                route.append({"dest": dest_id, "now": t.isoformat(), "date": label, "override": override,
                              "lux": None, "expect": run_route(dest_id, t, None, override)})
        for lux in sensor_scenarios:
            for override in sensor_overrides:
                for label, t in x_times:
                    route.append({"dest": dest_id, "now": t.isoformat(), "date": label, "override": override,
                                  "lux": lux, "expect": run_route(dest_id, t, lux, override)})
    for dest_id, lat, lon, screen_cfg in compute_dests:
        for label, t in solar_times:
            compute.append({"lat": lat, "lon": lon, "screen_cfg": screen_cfg, "now": t.isoformat(),
                            "date": label, "lux": None, "expect": run_compute(lat, lon, screen_cfg, t, None)})
        for lux in sensor_scenarios:
            for label, t in sensor_times[:1]:
                compute.append({"lat": lat, "lon": lon, "screen_cfg": screen_cfg, "now": t.isoformat(),
                                "date": label, "lux": lux, "expect": run_compute(lat, lon, screen_cfg, t, lux)})
    return route, compute


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def build_all() -> dict:
    intensity_cfg = flask_intensity_cfg()
    dest_path = Path(rutils.findfile("publish-destinations.json"))
    t_all = all_times(30)
    t_hourly = all_times(60)
    t_2h = all_times(120)
    t_few = few_times()
    t_sensor = t_few[:2] + t_few[2:3]          # summer day + night, winter day

    real_lux = [{"living-room": v} for v in LUX_GRID]
    real_compute = []
    for d in REAL_DESTS:
        icfg = d.get("intensity_cfg")
        if icfg and d["id"] == "north-screen":    # south-screen's cfg is identical today
            real_compute.append((d["id"], float(icfg["lat"]), float(icfg["lon"]), {**icfg, "id": d["id"]}))
    real_compute.append(("devtest", 51.5074, -0.1278, {"id": "devtest"}))       # no intensity_cfg
    real_compute.append(("unknown", 51.5074, -0.1278, {"id": "no-such-screen"}))  # not in the file
    real_compute.append((None, 51.5074, -0.1278, None))                          # global cfg only
    real_compute.append((None, 40.7128, -74.006, None))
    real_route, _ = build_set(REAL_DESTS, REAL_MAPPING, real_lux, t_all, t_few, t_sensor, [])
    _, real_comp = build_set(REAL_DESTS, REAL_MAPPING, real_lux, t_2h, t_few, t_sensor, real_compute)

    syn_lux = []
    for v in (0, 5, 22, 500, 1500):
        syn_lux += [{"hall": v}, {"desk": v}, {"everywhere": v}, {"nowhere": v},
                    {"living-room": v, "everywhere": 3.0}, {"desk": v, "living-room": 7.0},
                    {"hall": v, "everywhere": 40.0}]
    syn_compute = []
    for d in SYN_DESTS:
        if d["id"].startswith("syn-"):
            icfg = d["intensity_cfg"]
            syn_compute.append((d["id"], float(icfg["lat"]), float(icfg["lon"]), {**icfg, "id": d["id"]}))
    syn_ids = {d["id"] for d in SYN_DESTS if d["id"].startswith("syn-")}
    syn_route, _ = build_set(SYN_DESTS, SYN_MAPPING, syn_lux, t_hourly, t_few[:2], t_sensor[:1], [],
                             sensor_overrides=("none", "maskoff", "bright0.4"), full_solar_ids=syn_ids)
    _, syn_comp = build_set(SYN_DESTS, SYN_MAPPING, syn_lux, t_2h, t_few[:2], t_sensor[:1], syn_compute)

    files = {p: sha(REPO / p) for p in ("routes/display_utils.py", "routes/lightsensor.py", "routes/display.py",
                                         "routes/utils.py", "config.py", "routes/data/intensity.json")}
    files[str(dest_path.relative_to(REPO))] = sha(dest_path)
    out = {
        "about": "Golden outputs of Flask's compute_mask and GET /api/<id>/mask; see gen_golden.py",
        "python": sys.version.split()[0],
        "astral": getattr(__import__("astral"), "__version__", "?"),
        "flask_sources_sha256_16": files,
        "destinations_file": str(dest_path.relative_to(REPO)),
        "sets": {
            "real": {"inputs": {"intensity_cfg": intensity_cfg, "intensity_mapping": REAL_MAPPING,
                                "destinations": REAL_DESTS},
                     "route": real_route, "compute": real_comp},
            "synthetic": {"inputs": {"intensity_cfg": intensity_cfg, "intensity_mapping": SYN_MAPPING,
                                     "destinations": SYN_DESTS},
                          "route": syn_route, "compute": syn_comp},
        },
    }
    return out


def _legacy_sensor_intensity(name):
    """What Flask would pass after the switch: today's lux_history + read-driven EMA."""
    hist = rls.lux_history.get(name)
    if not hist:
        return None
    lux = hist[-1][1]
    return rls.intensity_mapper.get_intensity(name, lux), lux


def _switched_compute_mask(lat, lon, cfg=None, now_utc=None, time=None, screen_cfg=None):
    import lightmask
    # the proposed one-line body of routes.display_utils.compute_mask (time defaulted
    # through the frozen clock here only so timestamps are comparable)
    now = time or now_utc or rdu.datetime.utcnow().replace(tzinfo=timezone.utc)
    return lightmask.flask_compute_mask(SWITCH["cfg"], lat, lon, cfg, now_utc, now, screen_cfg,
                                        sensor_intensity=_legacy_sensor_intensity)


def verify_switch() -> int:
    """Run the grid through Flask twice: as is, then with compute_mask replaced by
    lightmask (Flask's routes, overrides, lux_history and EMA left in place)."""
    original = build_all()
    flask_cm = rdu.compute_mask
    SWITCH["on"] = True
    rdu.compute_mask = _switched_compute_mask
    rdisplay.compute_mask = _switched_compute_mask
    try:
        switched = build_all()
    finally:
        rdu.compute_mask = flask_cm
        rdisplay.compute_mask = flask_cm
        SWITCH["on"] = False
    bad, total = 0, 0
    for name, sset in original["sets"].items():
        for kind in ("route", "compute"):
            for a, b in zip(sset[kind], switched["sets"][name][kind], strict=True):
                total += 1
                if a["expect"] != b["expect"]:
                    bad += 1
                    if bad <= 5:
                        print("DIFF", name, kind, {k: a[k] for k in a if k != "expect"}, a["expect"], b["expect"])
    print(f"verify-switch: {total - bad}/{total} identical")
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    if "--verify-switch" in argv:
        return verify_switch()
    out = build_all()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + ".tmp")
    data = (json.dumps(out, separators=(",", ":")) + "\n").encode("utf-8")
    with tmp.open("wb") as f:
        with gzip.GzipFile(filename="golden_flask.json", mode="wb", fileobj=f, mtime=0) as gz:
            gz.write(data)
    tmp.replace(OUT)
    n = {k: (len(v["route"]), len(v["compute"])) for k, v in out["sets"].items()}
    print(f"wrote {OUT} (route, compute) cases: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
