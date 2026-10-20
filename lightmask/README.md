# lightmask

The one implementation of the light → mask mapping (LIVE_V1 §2.2b, DCL §2.6).
It is a faithful, pure-Python port of today's Flask logic, so that room_service
can compute masks itself and Flask can switch to the same code later.

- **Needs** only `astral` (3.2, the version in Flask's venv).
- **No Flask imports, no `routes.*`, no module state.** Config snapshots are
  immutable, and reload state lives on a `ConfigWatcher` instance.
- **Importable** as `import lightmask` from the repo root (Flask's cwd) and
  from room_service.

| File | What |
|---|---|
| `model.py` | Config loading/reload, target resolution, the lux→intensity curve, `mask_for` / `mask_for_dest`, the staleness blend, and Flask drop-ins (`flask_compute_mask`, `flask_mask_route`) |
| `filter.py` | `LuxFilter`: time-based log-lux snap/drift, held, seq, staleness, warm start. Also `exceeds_deadband`, `MaskGate` (deadband + ≤ 20 Hz), and `FilterParams` |
| `tests/gen_golden.py` | Runs Flask's real `compute_mask` and `GET /api/<id>/mask` and writes `tests/fixtures/golden_flask.json.gz` |
| `tests/test_golden.py` | lightmask against those fixtures, exact equality |
| `tests/test_model.py`, `tests/test_filter.py` | Unit tests |

## What it reproduces (reference: today's Flask)

- **Config.** It reads the same files Flask reads, re-read when their mtime or size changes:
  - `INTENSITY_CFG`: `config.py` is executed in an isolated `config` namespace, as `compute_mask` does, and only `INTENSITY_CFG` is read. It is compiled by hand, so no `__pycache__` is written. TODO: move it to `config/light.json`.
  - `routes/data/intensity.json`.
  - `publish-destinations.json`, found the way `routes.utils.findfile` finds it from the repo root. Today that is **`src/data/publish-destinations.json`**, not `routes/data/`.
- **Targets.** sensor → `target_group` → destinations, exactly as in `get_destinations_for_group`:
  - `global` means every destination with `has_bucket`;
  - a destination id beats a group name;
  - only destinations with `has_bucket` count.
- **Sensor choice.** When several sensors cover a screen, the first one in `intensity.json` order that has a reading wins.
- **Curve.** Piecewise-linear lux → intensity over `lux_to_intensity`, sorted by lux. The first segment with `x0 ≤ lux ≤ x1` is used; out-of-range values clamp to the end points.
- **Sensor branch.**
  - `brightness` is the intensity, **unrounded**.
  - Colour comes from mired interpolation → kelvin → sRGB → gain against `_REF_WHITE` → hex, and `warm_alpha` is rounded to 3 dp.
  - It uses the **base** `INTENSITY_CFG`. Flask merges per-screen keys only on the solar path, and that quirk is kept.
- **Solar branch.**
  - The per-screen `intensity_cfg` is merged over `INTENSITY_CFG`: only base keys are taken, cast with `bool()` when the base value is a bool and `float()` otherwise. `lat`/`lon` come from the screen.
  - `_solar_index` (astral; `dusk_offset_deg`, `seasonality_factor`, `smoothstep`), then the `east_west_bias` power, the `day_stretch` gamma and `night_floor`.
  - `brightness` and `warm_alpha` are rounded to 3 dp.
- **Overrides**, in the order of `GET /api/<id>/mask`:
  1. A brightness override → `{override, #FFFFFF, 0.0, "manual_override"}`.
  2. Maskoff → `{1.0, #FFFFFF, 0.0}`.
  3. `adjust` false or missing → `{1.0, #FFFFFF, 0.0}`.
  4. Otherwise the computed mask.

## room_service usage

```python
import time, lightmask as lm

watcher = lm.ConfigWatcher()                 # repo-root paths by default
# every ~2 s, off the event loop (it may exec config.py):
await loop.run_in_executor(None, watcher.refresh)
cfg = watcher.current

filters = {}                                 # one per sensor
f = filters.setdefault(name, lm.LuxFilter.from_state(persisted.get(name), start_t=time.monotonic(), sensor=name))
# on WS hello from a relay:   f.reset_sequence()
# on ingest.lux:
r = f.sample(d["lux"], time.monotonic(), seq=d["seq"], held=d.get("held", False))
if not r.accepted: ...                       # r.reason: seq_backwards / seq_duplicate / invalid_seq / invalid_lux / invalid_time

# per screen, after a sample or on a timer:
t = time.monotonic()
sensors = {n: lm.SensorInput(fl.intensity(cfg), fl.solar_weight(t)) for n, fl in filters.items()}
ov = lm.Overrides.from_flask(maskstate_json, brightness_override_json)   # Flask GETs every 2 s
mask = lm.mask_for_dest(cfg, screen_id, datetime.now(timezone.utc), ov, sensors)
out = gates[screen_id].offer(mask, t)        # MaskGate: deadband + 20 Hz; gate.due(t) later
if out is not None: push mask.set(out, transition_ms=f.params.transition_ms)
```

- **Sources.** `mask["source"]` is one of:
  - `sensor`, `astral` (the solar curve; DCL calls it "solar") or `manual_override`, the strings Flask uses;
  - `maskoff` or `fixed` (`adjust` off), for which Flask sends no `source`;
  - `blend`, for a stale-sensor ramp in progress (with `solar_weight`).

  A sensor-driven mask also carries `"sensor": <name>`.
- **Timer.** Evaluate each screen on a timer as well as on each sample (every 1–2 s is enough). The solar curve, the stale ramp and the ramp back all move without new samples.
- **Clock.** Pass room_service's own `time.monotonic()` receive time as `t`. The relay's `mono_ms` is another host's clock.
- **Held.** `f.is_held(t)` is true while a calibration capture is running. The filter's output and its solar ramp are frozen until a `held: false` sample arrives. To meet "keep the last mask", room_service should not push new masks for that sensor's targets while `f.is_held(t)` is true.
  - Gate on `is_held(t)`, not on the latched `f.held`. `is_held(t)` turns false once the link is stale (10 s without a sample), so a relay that dies mid-capture does not pin the mask forever: the screens then follow the normal stale ramp from the frozen weight.
  - A hold that begins after a stale gap re-initialises at the first `held: false` sample, as any return from staleness does.

  This is not the same as the server-side `mask.set` *hold* during cut handshakes (DCL §2.5/2.6). During that hold, keep sampling, gate the push, and send the latest mask when the hold releases.
- **Persistence.** Store `f.export_state()`, for example with each push, so a restart warm-starts. The last value is used until the first fresh sample, and the stale ramp starts 10 s later if none arrives.
- **Alerts.** Raising Siren `sensor.stale` at 60 s is the caller's job: use `f.state(t).age_s`.

## Flask switch (later; not done here)

The new body of `routes/display_utils.compute_mask` is one line, plus a module-level watcher and a reader that keeps today's `lux_history` and EMA:

```python
import lightmask
_LIGHT = lightmask.ConfigWatcher()

def _sensor_intensity(name):
    from routes.lightsensor import lux_history, intensity_mapper
    h = lux_history.get(name)
    return None if not h else (intensity_mapper.get_intensity(name, h[-1][1]), h[-1][1])

def compute_mask(lat, lon, cfg=None, now_utc=None, time=None, screen_cfg=None):
    return lightmask.flask_compute_mask(_LIGHT.get(), lat, lon, cfg, now_utc, time, screen_cfg, sensor_intensity=_sensor_intensity)
```

`flask_compute_mask` returns compute_mask's whole payload, `_debug` included. The following command checks the switch against live Flask code. It runs the full grid through Flask's own route twice, first unchanged and then with `compute_mask` swapped for the lines above, and compares the results. It writes nothing. Last run: **5049/5049 identical.**

```
/home/gjbm2/dev/screen-machine/.venv/bin/python -I -B lightmask/tests/gen_golden.py --verify-switch
```

To move Flask onto the new filter later, swap `_sensor_intensity` for one backed by a `LuxFilter` that is fed in `broadcast_lux_level`.

## Golden fixtures

Regenerate them after any change to the Flask mask code or to the config files. The generator uses Flask's venv **read-only**: it is run with `-B`, its stubs replace `utils.logger` (so it never opens the production log file) and `overlay_ws_server`, and it opens no sockets.

```
/home/gjbm2/dev/screen-machine/.venv/bin/python -I -B lightmask/tests/gen_golden.py
```

- **Determinism.** `lux_history` is set to one sample per sensor, so the moving average returns that lux. The EMA is cleared before every call, and `utcnow` is frozen for each case. The output is byte-reproducible.
- **Grid.** There are two input sets, and the fixture stores the inputs it used, so the tests are hermetic.
  - **real** (today's files):
    - lux {0, 0.5, 1, 2, 5, 10, 22, 50, 100, 500};
    - every 30 min of London local days: 21 Jun, 21 Dec and both 2026 DST change days, plus off-grid times;
    - every destination;
    - override states none, maskon, maskoff, brightness-override 0.4, and maskoff + 0.4.
  - **synthetic**: extra destinations and sensors covering seasonality, smoothstep, negative and maximal bias, `day_stretch` 0, string and int casts, id vs group vs `global` targets, `has_bucket`, an empty `target_group` and multi-sensor order.
- **Size.** 3711 route cases and 1338 compute cases.
- **Comparison.** Exact equality (`==`) on every field, with no tolerance needed. A mutation check (patching the sensor colour constants, the brightness rounding, the bool merge, the `has_bucket` rule or the solar date) makes the tests fail.
- **Drift check.** `test_load_config_reads_what_flask_read` is skipped, not failed, when `config.py`, `intensity.json` or the destinations file changed after the fixtures were generated. Its message tells you to regenerate.

Run the tests. Any venv with `astral==3.2` and `pytest` works; they do not need Flask.

```
<venv>/bin/python -B -m pytest lightmask/tests -q
```

## What differs from Flask on purpose

1. **Smoothing.** Flask's `get_intensity` does two things:
   - it averages the last 5 `lux_history` samples together with the latest one again;
   - it applies an α = 0.2 EMA that advances on every *read*, from every caller, with state shared across screens. Its time constant therefore depends on how many clients poll.

   lightmask's `intensity_for` is the bare curve, and smoothing is `LuxFilter`, which advances only on ingest and only in real time. Golden tests remove Flask's smoothing by construction (a single-sample history and a cleared EMA).
2. **Staleness.** Flask freezes the mask forever when the relay dies. lightmask goes stale after 10 s, ramps to solar over 30 s and ramps back over 5 s (`blend`). It also warm-starts.
3. **Source names.** lightmask returns `maskoff`, `fixed` and `blend` where Flask sends no `source`.
4. **Where Flask would raise, lightmask returns a value:**
   - a missing `lat`/`lon` with `adjust` on: Flask raises KeyError; lightmask uses `default_lat`/`default_lon`;
   - polar latitudes: Flask's `sun()` raises ValueError; lightmask calls only `noon()`, which gives the same value;
   - an empty curve or a zero-width segment: Flask raises IndexError or ZeroDivisionError; lightmask returns None or skips the segment;
   - a missing `intensity.json`: Flask raises AttributeError for adjusting screens; lightmask treats it as having no sensors.
5. **Bad config files.** A half-written file keeps the last good snapshot in `ConfigWatcher` (`last_error` is set). Flask either disables the sensor path or returns 500.
6. **Time zones.** `mask_for` converts `now` to UTC, and a naive `now` is taken as UTC. Flask always passes aware UTC, so the results are identical. `flask_compute_mask` uses times as given, as Flask does.

## Filter limits to know about (not bugs; owner/peer review)

- **Snap confirmation needs a second sample.** With event-driven reporting (Yocto callbacks only on a change of at least 1 %), a clean step that settles in one callback is confirmed by the next sample. If that is the 1 Hz heartbeat, the first visible change comes up to 1 s after the step, outside the DCL p95 250 ms target. A step the sensor reports over several callbacks (the usual case while its reading settles) is confirmed at the second callback. The filter advances only on ingest, so it cannot confirm on a timer by itself. The fix belongs in the relay: re-send once about 100 ms after any reading that moved more than `snap_ev`, or report at a fixed rate.
- **The output is constant between samples.** After the light stops changing, only heartbeats drive the drift, so the last part of a change arrives in 1 Hz steps (each 1 - e^(-1/2.5) = 33 % of what is left), smoothed by `transition_ms`.
- **Fast dimmer ramps** (above about 0.3 EV/s, where the 2.5 s drift lags by more than 0.75 EV) alternate snap and drift. The output stays monotonic, but its speed pulses. With `snap_exit_ev = 0.1` the largest per-sample step on 3–10 s ramps is about half that of the literal rule (`snap_exit_ev = 0.75`), measured on today's curve.

## Filter constants (FilterParams; D27 item 11 defaults)

| Key | Default | Note |
|---|---|---|
| `snap_ev`, `snap_confirm`, `snap_tau_s` | 0.75 EV, 2 samples, 0.15 s | the first far sample is **not applied** (spike guard) |
| `snap_exit_ev` | 0.1 EV | **judgement call:** once snapping, keep τ 0.15 s until within 0.1 EV. Otherwise the last ~0.75 EV of a step would creep in on the 2.5 s drift τ. Set it to 0.75 for the literal DCL rule |
| `drift_tau_s`, `drift_tau_up_s` | 2.5 s, None | the optional faster brightening drift |
| `log_offset_lux` | 1.0 | EV = log2(lux + 1). Keeps 0 lux finite and stops sub-lux noise counting as several EV |
| `lux_min`, `lux_max` | 0, 200000 | clamp; NaN, inf and non-numbers (bool, str) are rejected; so is a non-integer `seq` or a non-finite `t` |
| `stale_after_s`, `stale_ramp_s`, `fresh_ramp_s` | 10, 30, 5 s | the first sample after a stale gap re-initialises the state |
| `deadband_brightness`, `deadband_warm_alpha` | 0.005, 0.002 | a change of `source` is always sent |
| `max_rate_hz`, `transition_ms` | 20, 150 | `transition_ms` is carried for the caller |

`load_filter_params()` and `FilterParamsWatcher` read the `"filter"` object of `config/light.json`, reloading on mtime. That file does not exist yet; this package does not create it.
