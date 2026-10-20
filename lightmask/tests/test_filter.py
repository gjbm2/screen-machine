"""Unit tests for lightmask.filter (DCL §2.6 filter, staleness, held)."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import pytest

import lightmask as lm
from lightmask.filter import FilterParams, LuxFilter, MaskGate, exceeds_deadband

P = FilterParams()


def ev(lux, p=P):
    return math.log2(lux + p.log_offset_lux)


def feed(f: LuxFilter, lux, t0, t1, hz, seq0=None, held=False):
    """Samples at ``hz`` from t0 (inclusive) to t1 (exclusive). Returns (next_t, next_seq, results)."""
    out, t, seq, k = [], t0, seq0, 0
    while t < t1 - 1e-9:
        out.append(f.sample(lux(t) if callable(lux) else lux, t, seq=seq, held=held))
        if seq is not None:
            seq += 1
        k += 1
        t = t0 + k / hz
    return t, seq, out


def steady(lux=10.0, hz=10.0, until=5.0, **kw):
    f = LuxFilter("living-room", **kw)
    t, seq, _ = feed(f, lux, 0.0, until, hz, seq0=0)
    return f, t, seq


# ───────── snap / drift ──────────────────────────────────────────────────

def test_first_sample_initialises():
    f = LuxFilter("s")
    assert f.lux is None and f.solar_weight(0) == 1.0
    r = f.sample(37.0, 0.0)
    assert r.accepted and r.mode == "init" and f.lux == pytest.approx(37.0)


@pytest.mark.parametrize("hz", [10.0, 5.0, 1.4])
def test_step_up_snaps(hz):
    f, t, seq = steady(10.0, hz)
    target = ev(100.0)
    # first far sample is not applied (spike guard)
    r = f.sample(100.0, t, seq=seq)
    assert r.mode == "pending" and not r.moved and f.ev == pytest.approx(ev(10.0))
    # the second confirms and snaps
    r = f.sample(100.0, t + 1 / hz, seq=seq + 1)
    assert r.mode == "snap" and r.moved
    # settled within 0.1 EV one second after confirmation (tau 0.15 s; snap holds to 0.1 EV)
    _, _, _ = feed(f, 100.0, t + 2 / hz, t + 1 / hz + 1.0 + 1e-6, hz, seq0=seq + 2)
    assert abs(f.ev - target) < 0.1


def test_step_down_snaps_and_settles_fast():
    f, t, seq = steady(200.0, 10.0)
    feed(f, 3.0, t, t + 0.9, 10.0, seq0=seq)
    assert abs(f.ev - ev(3.0)) < 0.1


def test_snap_exit_hysteresis_vs_literal_rule():
    # literal DCL rule (snap only while > 0.75 EV away) leaves a slow drift tail
    lit, t, seq = steady(10.0, 10.0, params=FilterParams(snap_exit_ev=0.75))
    hys, _, _ = steady(10.0, 10.0)
    for f in (lit, hys):
        feed(f, 100.0, t, t + 1.0, 10.0, seq0=seq)
    assert abs(hys.ev - ev(100.0)) < 0.1
    assert abs(lit.ev - ev(100.0)) > 0.25      # still on the 2.5 s drift tau


@pytest.mark.parametrize("hz", [10.0, 2.0, 1.4])
def test_drift_is_time_based(hz):
    # 10 -> 13 lux is 0.35 EV (< 0.75): pure drift with tau 2.5 s
    f, t, seq = steady(10.0, hz)
    d0 = ev(13.0) - ev(10.0)
    n = round(2.5 * hz) if abs(2.5 * hz - round(2.5 * hz)) < 1e-9 else None
    if n is None:      # 1.4 Hz: check at a whole number of samples
        n = 7
    for k in range(1, n + 1):
        f.sample(13.0, t + k / hz, seq=seq + k)
    elapsed = (n + 1) / hz     # the first step sample's interval starts at the last steady sample
    assert ev(13.0) - f.ev == pytest.approx(d0 * math.exp(-elapsed / 2.5), rel=1e-9)


def test_drift_up_tau_optional():
    p = FilterParams(drift_tau_up_s=0.5)
    up, t, seq = steady(10.0, 10.0, params=p)
    down, _, _ = steady(10.0, 10.0, params=p)
    feed(up, 13.0, t, t + 1.0, 10.0, seq0=seq)
    feed(down, 7.7, t, t + 1.0, 10.0, seq0=seq)
    assert (ev(13.0) - up.ev) / (ev(13.0) - ev(10.0)) == pytest.approx(math.exp(-1.0 / 0.5), rel=1e-6)
    assert (down.ev - ev(7.7)) / (ev(10.0) - ev(7.7)) == pytest.approx(math.exp(-1.0 / 2.5), rel=1e-6)


# ───────── spike rejection ───────────────────────────────────────────────

@pytest.mark.parametrize("spike", [5000.0, 0.0, 200000.0])
def test_single_sample_spike_is_rejected(spike):
    f, t, seq = steady(10.0, 10.0)
    before = f.ev
    r = f.sample(spike, t, seq=seq)
    assert r.mode == "pending"
    f.sample(10.0, t + 0.1, seq=seq + 1)
    f.sample(10.0, t + 0.2, seq=seq + 2)
    assert f.ev == pytest.approx(before, abs=1e-12)


def test_alternating_far_samples_never_confirm():
    f, t, seq = steady(10.0, 10.0)
    before = f.ev
    for k in range(20):
        f.sample(1000.0 if k % 2 == 0 else 0.0, t + k * 0.1, seq=seq + k)
    assert f.ev == pytest.approx(before, abs=1e-12)


def test_snap_confirm_one_disables_spike_guard():
    f, t, seq = steady(10.0, 10.0, params=FilterParams(snap_confirm=1))
    r = f.sample(1000.0, t, seq=seq)
    assert r.mode == "snap" and r.moved


# ───────── deadband / rate ───────────────────────────────────────────────

def m(b, a=0.0, src="sensor", hexv="#FFFFFF"):
    return {"brightness": b, "warm_alpha": a, "warm_hex": hexv, "source": src}


def test_deadband():
    assert exceeds_deadband(None, m(0.5))
    assert not exceeds_deadband(m(0.5), m(0.504))
    assert exceeds_deadband(m(0.5), m(0.505))
    assert exceeds_deadband(m(0.5), m(0.495))
    assert not exceeds_deadband(m(0.5, 0.010), m(0.5, 0.0119))
    assert exceeds_deadband(m(0.5, 0.010), m(0.5, 0.012))
    assert exceeds_deadband(m(0.5, src="sensor"), m(0.5, src="blend"))


def test_mask_gate_deadband_and_rate():
    g = MaskGate()
    assert g.offer(m(0.5), 0.0) == m(0.5)
    assert g.offer(m(0.503), 0.01) is None and g.wait_s(0.01) is None     # inside the deadband
    assert g.offer(m(0.6), 0.02) is None                                   # 20 Hz: held back
    assert g.wait_s(0.02) == pytest.approx(0.03)
    assert g.due(0.04) is None
    assert g.due(0.05) == m(0.6)
    assert g.offer(m(0.7), 0.06, force=True) == m(0.7)                    # force bypasses both
    # creep: compared with the last *sent* value
    g2 = MaskGate()
    g2.offer(m(0.500), 0.0)
    sent = [g2.offer(m(0.500 + 0.001 * k), k * 1.0) for k in range(1, 7)]
    assert sent[:4] == [None] * 4 and sent[4] == m(0.505)


# ───────── staleness and ramps ───────────────────────────────────────────

def test_cold_start_ramps_in_over_5s():
    f = LuxFilter("s")
    assert f.is_stale(0.0) and f.solar_weight(0.0) == 1.0
    f.sample(10.0, 100.0)
    assert f.solar_weight(100.0) == 1.0
    assert f.solar_weight(102.5) == pytest.approx(0.5)
    assert f.solar_weight(105.0) == 0.0


def test_stale_after_10s_ramps_to_solar_over_30s_and_back_over_5s():
    f, t, seq = steady(10.0, 10.0, until=20.0)
    last = t - 0.1
    assert f.solar_weight(last + 10.0) == 0.0 and not f.is_stale(last + 10.0)
    assert f.is_stale(last + 10.5)
    assert f.solar_weight(last + 10.0 + 15.0) == pytest.approx(0.5)
    assert f.solar_weight(last + 10.0 + 30.0) == pytest.approx(1.0)
    assert f.solar_weight(last + 500.0) == 1.0
    # readings return half-way through the up-ramp: back down over 5 s from there
    t_back = last + 10.0 + 15.0
    r = f.sample(40.0, t_back, seq=seq)
    assert r.mode == "init" and f.lux == pytest.approx(40.0)   # re-initialised at the new reading
    assert f.solar_weight(t_back) == pytest.approx(0.5)
    assert f.solar_weight(t_back + 1.0) == pytest.approx(0.3)
    assert f.solar_weight(t_back + 2.5) == pytest.approx(0.0)
    assert not f.is_stale(t_back + 2.5)


def test_reads_do_not_advance_anything():
    f, t, seq = steady(10.0, 10.0)
    f.sample(13.0, t, seq=seq)
    snap = (f.ev, f.solar_weight(t + 3), f.is_stale(t + 3))
    for k in range(1000):
        f.state(t + 3)
        f.solar_weight(t + k * 0.01)
        _ = f.lux
    assert (f.ev, f.solar_weight(t + 3), f.is_stale(t + 3)) == snap


def test_stale_ramp_feeds_mask_blend():
    cfg = lm.LightConfig.from_data(
        {"night_floor": 0.025, "warmest_temp_K": 2200, "coolest_temp_K": 6500, "beta_colour": 1.2,
         "warm_alpha_max": 0.1, "dusk_offset_deg": -6, "smoothstep": False, "east_west_bias": 0.0,
         "seasonality_factor": 0.0, "day_stretch": 0.2, "default_lat": 51.5, "default_lon": -0.1},
        {"sensor_mappings": {"s": {"target_group": "g", "lux_to_intensity": [
            {"lux": 0, "intensity": 0.0}, {"lux": 100, "intensity": 1.0}]}}},
        [{"id": "tv", "has_bucket": True, "groups": ["g"], "intensity_cfg": {"adjust": True, "lat": 51.5, "lon": -0.1}}])
    now = datetime(2026, 12, 21, 22, tzinfo=timezone.utc)     # night: solar = 0.025
    f = LuxFilter("s")
    feed(f, 100.0, 0.0, 10.0, 10.0, seq0=0)
    tl = 9.9
    def mask_at(tt):
        return lm.mask_for_dest(cfg, "tv", now, None,
                                {"s": lm.SensorInput(f.intensity(cfg), f.solar_weight(tt))})
    assert mask_at(tl + 5)["source"] == "sensor"
    assert mask_at(tl + 5)["brightness"] == pytest.approx(1.0)
    mid = mask_at(tl + 25)
    assert mid["source"] == "blend" and mid["brightness"] == pytest.approx(1.0 + (0.025 - 1.0) * 0.5, abs=1e-4)
    assert mask_at(tl + 41)["source"] == "astral"


# ───────── warm start ────────────────────────────────────────────────────

def test_warm_start_uses_last_value_until_fresh_sample():
    f = LuxFilter("s", warm_lux=50.0, start_t=1000.0)
    assert f.lux == pytest.approx(50.0) and f.solar_weight(1000.0) == 0.0
    assert not f.is_stale(1009.0)
    assert f.solar_weight(1020.0) == pytest.approx(10.0 / 30.0)       # no sample in 10 s -> stale ramp
    g = LuxFilter("s", warm_lux=50.0, start_t=0.0)
    r = g.sample(55.0, 1.0)                                            # near: drift from the warm value
    assert r.mode == "drift" and 50.0 < g.lux < 55.0
    h = LuxFilter("s", warm_lux=50.0, start_t=0.0)
    assert h.sample(5.0, 1.0).mode == "pending" and h.lux == pytest.approx(50.0)
    assert h.sample(5.0, 1.1).mode == "snap"


def test_export_and_restore_state():
    f, t, seq = steady(22.0, 10.0)
    st = json.loads(json.dumps(f.export_state()))
    g = LuxFilter.from_state(st, start_t=0.0)
    assert g.sensor == "living-room" and g.lux == pytest.approx(f.lux)
    cold = LuxFilter.from_state(None, start_t=0.0, sensor="x")
    assert cold.lux is None and cold.solar_weight(0.0) == 1.0
    with pytest.raises(ValueError):
        LuxFilter("s", warm_lux=1.0)


# ───────── held ──────────────────────────────────────────────────────────

def test_held_samples_freeze_output_and_keep_link_alive():
    f, t, seq = steady(10.0, 10.0)
    frozen = f.ev
    # 60 s of calibration flashes, relay sending held values (any lux) at 1.4 Hz
    t2, seq2, res = feed(f, lambda tt: 5000.0 if int(tt * 3) % 2 else 0.0, t, t + 60.0, 1.4, seq0=seq, held=True)
    assert all(r.accepted and r.mode == "held" and not r.moved for r in res)
    assert f.held and f.ev == frozen
    assert not f.is_stale(t2) and f.solar_weight(t2) == 0.0
    # held=false at the same light: resumes from the frozen state, no jump
    r = f.sample(10.0, t2, seq=seq2)
    assert not f.held and r.mode == "drift" and f.ev == pytest.approx(frozen, abs=1e-12)
    # a real change during the hold still needs the 2-sample confirmation afterwards
    g, t, seq = steady(10.0, 10.0)
    t2, seq2, _ = feed(g, 10.0, t, t + 5.0, 10.0, seq0=seq, held=True)
    assert g.sample(300.0, t2, seq=seq2).mode == "pending"
    assert g.sample(300.0, t2 + 0.1, seq=seq2 + 1).mode == "snap"


def test_held_freezes_the_solar_ramp_too():
    f = LuxFilter("s")
    f.sample(10.0, 0.0)                         # cold start: ramping 1 -> 0 over 5 s
    f.sample(10.0, 2.5, held=True)              # hold begins half-way
    w = f.solar_weight(2.5)
    assert w == pytest.approx(0.5)
    for k in range(1, 20):
        f.sample(10.0, 2.5 + k * 0.5, held=True)
        assert f.solar_weight(2.5 + k * 0.5) == pytest.approx(0.5)
    t_end = 2.5 + 19 * 0.5
    f.sample(10.0, t_end + 0.5)                 # released: ramp resumes from 0.5
    assert f.solar_weight(t_end + 0.5) == pytest.approx(0.5)
    assert f.solar_weight(t_end + 1.5) == pytest.approx(0.3)


def test_relay_dies_during_hold_goes_stale():
    f, t, seq = steady(10.0, 10.0)
    t2, _, _ = feed(f, 10.0, t, t + 3.0, 10.0, seq0=seq, held=True)
    last = t2 - 0.1
    assert not f.is_stale(last + 9.9)
    assert f.is_stale(last + 10.5) and f.solar_weight(last + 25.0) == pytest.approx(0.5)


# ───────── rates, sequence, validation ───────────────────────────────────

def test_rate_changes_are_not_faults():
    f = LuxFilter("s")
    t, seq, _ = feed(f, 22.0, 0.0, 10.0, 10.0, seq0=0)
    t, seq, _ = feed(f, 22.0, t, t + 30.0, 1.4, seq0=seq)        # calibration-rate measureType 5
    assert not f.is_stale(t) and f.solar_weight(t) == 0.0
    t, seq, _ = feed(f, 22.0, t, t + 10.0, 1.0, seq0=seq)        # heartbeat only
    assert not f.is_stale(t) and f.lux == pytest.approx(22.0)
    t, seq, _ = feed(f, 22.0, t, t + 5.0, 10.0, seq0=seq)
    assert f.samples > 0 and f.rejected == 0 and f.lux == pytest.approx(22.0)


def test_seq_backwards_and_duplicates_rejected():
    f = LuxFilter("s")
    assert f.sample(10.0, 0.0, seq=100).accepted
    r = f.sample(500.0, 0.1, seq=99)
    assert not r.accepted and r.reason == "seq_backwards"
    r = f.sample(500.0, 0.1, seq=100)
    assert not r.accepted and r.reason == "seq_duplicate"
    assert f.rejected == 2 and f.lux == pytest.approx(10.0)
    # rejected frames do not count as liveness
    for k in range(30):
        f.sample(10.0, 1.0 + k, seq=5)
    assert f.is_stale(31.0)
    # a new relay connection may restart its count
    f.reset_sequence()
    assert f.sample(10.0, 31.0, seq=0).accepted


def test_lux_clamped_and_invalid_rejected():
    f = LuxFilter("s")
    f.sample(1e9, 0.0)
    assert f.lux == pytest.approx(200_000.0)
    g = LuxFilter("s")
    g.sample(-5.0, 0.0)
    assert g.lux == 0.0
    for bad in (float("nan"), float("inf"), "x", None):
        r = g.sample(bad, 1.0)
        assert not r.accepted and r.reason == "invalid_lux"


def test_clock_going_backwards_is_dt_zero():
    f, t, seq = steady(10.0, 10.0)
    before = f.ev
    f.sample(13.0, t - 5.0, seq=seq)
    assert f.ev == before


# ───────── intensity, params ─────────────────────────────────────────────

def test_intensity_for_filtered_lux():
    cfg = lm.LightConfig.from_data({}, {"sensor_mappings": {"living-room": {"target_group": "g", "lux_to_intensity": [
        {"lux": 0, "intensity": 0.0}, {"lux": 100, "intensity": 1.0}]}}}, [])
    f, _, _ = steady(25.0, 10.0)
    assert f.intensity(cfg) == pytest.approx(0.25)
    assert lm.filter.intensity_for(cfg, "living-room", 50.0) == 0.5
    assert LuxFilter("living-room").intensity(cfg) is None


def test_params_from_mapping_and_file(tmp_path):
    p = FilterParams.from_mapping({"snap_tau_s": "0.2", "snap_confirm": 3, "unknown": 1, "drift_tau_up_s": None})
    assert p.snap_tau_s == 0.2 and p.snap_confirm == 3 and p.drift_tau_up_s is None
    with pytest.raises(ValueError):
        FilterParams(drift_tau_s=0)
    path = tmp_path / "light.json"
    assert lm.load_filter_params(path) == FilterParams()
    path.write_text(json.dumps({"filter": {"drift_tau_s": 4.0}}))
    w = lm.FilterParamsWatcher(path)
    assert w.get().drift_tau_s == 4.0
    path.write_text(json.dumps({"filter": {"drift_tau_s": 0}}))   # invalid -> keep last good
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    assert w.get().drift_tau_s == 4.0 and "ValueError" in w.last_error


def test_step_to_first_visible_change_latency():
    """A light step at 10 Hz reporting changes the mask within 2 samples (<= 200 ms),
    inside the DCL p95 250 ms budget before transport and TV lag."""
    cfg = lm.LightConfig.from_data(
        {"night_floor": 0.025, "warmest_temp_K": 2200, "coolest_temp_K": 6500, "beta_colour": 1.2,
         "warm_alpha_max": 0.1},
        {"sensor_mappings": {"s": {"target_group": "g", "lux_to_intensity": [
            {"lux": 0, "intensity": 0.01}, {"lux": 22, "intensity": 0.5}, {"lux": 100, "intensity": 1.0}]}}},
        [{"id": "tv", "has_bucket": True, "groups": ["g"], "intensity_cfg": {"adjust": True}}])
    f = LuxFilter("s", warm_lux=5.0, start_t=0.0)      # warm: solar weight 0, no cold-start ramp
    gate = MaskGate()
    now = datetime(2026, 10, 13, 20, tzinfo=timezone.utc)
    sent_at = []
    for k in range(60):
        t = k * 0.1
        lux = 5.0 if t < 3.0 else 100.0
        f.sample(lux, t, seq=k)
        mk = lm.mask_for_dest(cfg, "tv", now, None, {"s": lm.SensorInput(f.intensity(cfg), f.solar_weight(t))})
        if mk["source"] == "sensor" and gate.offer(mk, t) is not None and t >= 3.0:
            sent_at.append(t)
    assert sent_at and sent_at[0] - 3.0 <= 0.1 + 1e-9


# ───────── review fixes (adversarial pass) ───────────────────────────────

def test_relay_dying_mid_hold_releases_the_hold_when_stale():
    # f.held latches; is_held(t) (what callers gate pushes on) clears once stale,
    # so the screens follow the stale ramp instead of keeping the held mask forever.
    f, t, seq = steady(10.0, 10.0)
    t2, _, _ = feed(f, 10.0, t, t + 3.0, 10.0, seq0=seq, held=True)
    last = t2 - 0.1
    assert f.is_held(last + 5.0) and f.state(last + 5.0).held
    assert f.held and not f.is_held(last + 10.5) and not f.state(last + 10.5).held
    # the ramp continues from the frozen weight (0 here), not from a jump
    assert f.solar_weight(last + 10.0) == 0.0
    assert f.solar_weight(last + 25.0) == pytest.approx(0.5)


def test_hold_after_stale_gap_still_reinitialises_on_release():
    f, t, seq = steady(10.0, 10.0)
    t_back = t + 100.0                           # relay silent for ~100 s
    assert f.sample(500.0, t_back, seq=seq, held=True).mode == "held"
    assert f.lux == pytest.approx(10.0)          # held: nothing moves
    w_frozen = f.solar_weight(t_back)
    assert w_frozen == 1.0
    assert f.solar_weight(t_back + 0.5) == 1.0   # frozen while held
    r = f.sample(500.0, t_back + 0.5, seq=seq + 1)
    assert r.mode == "init" and f.lux == pytest.approx(500.0)   # not "pending" from a 100 s old state
    assert f.solar_weight(t_back + 3.0) == pytest.approx(0.5)  # 5 s ramp back from the frozen weight


@pytest.mark.parametrize("bad", ["abc", float("nan"), float("inf"), True, 1.5, "12", [3]])
def test_invalid_seq_is_rejected_not_raised(bad):
    f = LuxFilter("s")
    f.sample(10.0, 0.0, seq=5)
    r = f.sample(10.0, 0.1, seq=bad)
    assert not r.accepted and r.reason == "invalid_seq"
    assert f.sample(10.0, 0.2, seq=6).accepted          # the last good seq is kept
    assert f.sample(10.0, 0.3, seq=7.0).accepted        # an integral float is fine


@pytest.mark.parametrize("bad", [True, False, "312.5", b"1", 10 ** 400, {"lux": 1}])
def test_non_numeric_lux_rejected(bad):
    f = LuxFilter("s")
    r = f.sample(bad, 0.0)
    assert not r.accepted and r.reason == "invalid_lux" and f.lux is None and f.samples == 0


@pytest.mark.parametrize("bad_t", [float("nan"), float("inf"), "1.0", None])
def test_non_finite_time_rejected(bad_t):
    f, t, seq = steady(10.0, 10.0)
    r = f.sample(13.0, bad_t, seq=seq)
    assert not r.accepted and r.reason == "invalid_time"
    assert not f.is_stale(t) and f.is_stale(t + 11.0)    # the clock is not poisoned


def test_set_params_keeps_the_output_continuous():
    f, t, seq = steady(10.0, 10.0)
    for p in (FilterParams(log_offset_lux=0.1), FilterParams(lux_max=5.0), FilterParams(drift_tau_s=4.0)):
        before = min(f.lux, p.lux_max)
        f.set_params(p)
        assert f.lux == pytest.approx(before, rel=1e-12)
    f.set_params(FilterParams())
    assert f.lux == pytest.approx(5.0)


@pytest.mark.parametrize("state", [{"lux": "abc"}, {"lux": float("nan")}, {"lux": True}, ["x"], "junk", {}])
def test_corrupt_persisted_state_cold_starts(state):
    f = LuxFilter.from_state(state, start_t=0.0, sensor="s")
    assert f.lux is None and f.solar_weight(0.0) == 1.0


def test_steady_noise_barely_reaches_the_screen():
    """Flicker check: 3 % gaussian lux noise at 10 Hz on today's curve never snaps,
    stays within ~2 deadband steps, and gets through the MaskGate less than
    once per 20 s (each send is a <= 0.006 brightness change)."""
    import random
    rnd = random.Random(3)
    cfg = lm.LightConfig.from_data({}, {"sensor_mappings": {"s": {"target_group": "g", "lux_to_intensity": [
        {"lux": 0, "intensity": 0.01}, {"lux": 15, "intensity": 0.42}, {"lux": 22, "intensity": 0.5},
        {"lux": 30, "intensity": 0.58}, {"lux": 100, "intensity": 1.0}]}}}, [])
    f = LuxFilter("s")
    gate = MaskGate()
    outs, modes, sends = [], set(), 0
    for k in range(3000):
        r = f.sample(20.0 * (1 + rnd.gauss(0, 0.03)), k * 0.1, seq=k)
        modes.add(r.mode)
        if k > 100:
            b = f.intensity(cfg)
            outs.append(b)
            sends += gate.offer({"brightness": b, "warm_alpha": 0.0, "source": "sensor"}, k * 0.1) is not None
    assert "snap" not in modes and "pending" not in modes
    assert max(outs) - min(outs) < 2 * P.deadband_brightness
    assert sends - 1 < (3000 - 101) * 0.1 / 20.0


def test_step_response_is_monotone_without_overshoot():
    for hz in (10.0, 1.4):
        for target in (100.0, 0.5):
            f, t, seq = steady(10.0, hz)
            prev = f.ev
            _, _, res = feed(f, target, t, t + 15.0, hz, seq0=seq)
            evs = [prev]
            for r in res:
                evs.append(math.log2(r.lux + P.log_offset_lux))
            diffs = [b - a for a, b in zip(evs, evs[1:])]
            up = target > 10.0
            assert all((d >= -1e-12) if up else (d <= 1e-12) for d in diffs)
            assert (max(evs) <= ev(target) + 1e-9) if up else (min(evs) >= ev(target) - 1e-9)
            assert abs(f.ev - ev(target)) < 0.01


def test_spike_during_stale_ramp_and_recovery_is_continuous():
    f, t, seq = steady(10.0, 10.0)
    last = t - 0.1
    t_back = last + 20.0                                      # 10 s into the stale ramp
    w_before = f.solar_weight(t_back)
    f.sample(10.0, t_back, seq=seq)
    assert f.solar_weight(t_back) == pytest.approx(w_before)  # no jump in the weight on return
    assert f.solar_weight(t_back + 0.1) < w_before


def test_reads_are_pure_for_all_accessors():
    f, t, seq = steady(10.0, 10.0)
    f.sample(100.0, t, seq=seq)                               # pending far sample
    snap = dict(vars(f))
    for k in range(50):
        f.state(t + k), f.solar_weight(t + k), f.is_stale(t + k), f.is_held(t + k), f.lux, f.ev, f.export_state()
    assert vars(f) == snap
