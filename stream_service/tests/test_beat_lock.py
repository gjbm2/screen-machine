"""Effects on the beat (owner, 17 Oct): the beat clock, the tempo lock of x-tempo paces and the opt-in phase lock
(plugins/_lib/beat.py, effects.beat_clock / TempoLock / EffectSlot).  NumPy fakes, no GPU."""
from __future__ import annotations

import math

import numpy as np
import pytest

from stream_service.effects import EffectSlot, TempoLock, beat_clock
from stream_service.layers import FrameCtx
from stream_service.plugins._lib.beat import BeatClock, advance, cycles_per_s, paced, tempo_unit, value_for
from stream_service.plugins.base import ParamEaser

from .test_layers import Pool

FPS = 60
DT = 1.0 / FPS


def clock(bpm=120.0, beats=0.0, bpb=4, locks=None):
    return BeatClock(beats=beats, bpm=bpm, beats_per_bar=bpb, locks=locks or {})


def hz(lo=0.2, hi=12.0, default=3.0, k=1.0):
    return paced({"type": "number", "minimum": lo, "maximum": hi, "default": default}, k)


# -- the clock's divisions --------------------------------------------------------------------------------------

def test_divisions_are_beats_then_whole_bars():
    assert clock().divisions() == (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0)
    assert clock(bpb=3).divisions() == (0.25, 0.5, 1.0, 2.0, 3.0, 6.0, 12.0, 24.0, 48.0, 96.0)


@pytest.mark.parametrize("cps, n", [(3.0, 0.5), (2.0, 1.0), (0.6, 4.0), (0.12, 16.0), (0.05, 32.0), (8.0, 0.25)])
def test_snap_picks_the_nearest_division(cps, n):
    assert clock(120).snap(cps) == n            # 120 bpm: 2 beats/s


def test_snap_keeps_its_division_until_well_past_half_way():
    c = clock(120)
    assert c.snap(2.0 * 2 ** 0.6, current=1.0) == 1.0          # 0.6 octaves faster: still 1 beat
    assert c.snap(2.0 * 2 ** 0.8, current=1.0) == 0.5          # 0.8: half a beat


def test_snap_out_of_reach_is_free():
    c = clock(120)
    assert c.snap(40.0) is None                                 # far faster than 1/16 notes
    assert c.snap(0.001) is None                                # far slower than 32 bars
    assert c.snap(0.0) is None


def test_units():
    assert tempo_unit({"x-tempo": 360}) == 360.0 and tempo_unit({"x-tempo": "period"}) == "period"
    assert tempo_unit({}) is None and tempo_unit({"x-tempo": True}) is None and tempo_unit({"x-tempo": -1}) is None
    assert cycles_per_s(90.0, 360.0) == 0.25 and cycles_per_s(-0.5, 1.0) == 0.5 and cycles_per_s(2.0, "period") == 0.5
    assert value_for(0.25, 360.0, -1.0) == -90.0 and value_for(0.5, "period") == 2.0


# -- the tempo lock -----------------------------------------------------------------------------------------------

def settle(lock, value, clk, seconds=4.0, key="f"):
    out = None
    for _ in range(int(seconds * FPS)):
        p = {key: value}
        lock.apply(p, clk, DT)
        out = p[key]
    return out


def test_a_hz_pace_runs_at_a_division_while_the_clock_is_sure():
    lock = TempoLock({"f": hz()})
    assert settle(lock, 3.0, clock(120)) == pytest.approx(4.0, rel=1e-3)      # 1/8 notes at 120 bpm
    assert lock.locks == {"f": 0.5}
    assert settle(lock, 3.0, clock(90)) == pytest.approx(3.0, rel=1e-3)       # 90 bpm: 1/8 notes = 3 Hz


@pytest.mark.parametrize("k, value, want", [(360.0, 70.0, 90.0), (360.0, -70.0, -90.0), (60.0, 3.0, 3.75),
                                              ("period", 1.7, 2.0)])
def test_units_and_signs(k, value, want):
    # 120 bpm: 70 deg/s -> 1 turn per 8 beats (90 deg/s); 3 /min -> one per 32 beats (3.75 /min); 1.7 s -> 1 bar
    lock = TempoLock({"f": hz(-1000.0, 1000.0, 1.0, k)})
    assert settle(lock, value, clock(120)) == pytest.approx(want, rel=1e-3)


def test_free_without_a_clock_and_untouched_once_settled():
    lock = TempoLock({"f": hz()})
    p = {"f": 3.0}
    lock.apply(p, None, DT)
    assert p["f"] == 3.0 and lock.locks == {}
    settle(lock, 3.0, clock(120))
    out = settle(lock, 3.0, None, seconds=6.0)                  # the clock goes: eases back, then passes untouched
    assert out == 3.0 and "f" not in lock.k


def test_locking_and_letting_go_are_glides():
    lock = TempoLock({"f": hz()})
    prev = 3.0
    for i in range(int(9 * FPS)):
        p = {"f": 3.0}
        lock.apply(p, clock(120) if 3 * FPS <= i < 6 * FPS else None, DT)
        assert abs(p["f"] - prev) < 0.06                        # never a jump: 3 <-> 4 Hz over ~0.4 s
        prev = p["f"]
    assert prev == 3.0


def test_a_slots_first_frame_starts_locked():
    lock = TempoLock({"f": hz()})
    p = {"f": 3.0}
    lock.apply(p, clock(120), DT)
    assert p["f"] == pytest.approx(4.0)


def test_kicks_ride_on_the_locked_pace():
    lock = TempoLock({"f": hz()})
    settle(lock, 3.0, clock(120))
    p = {"f": 3.0 * 1.5}                                        # a one-frame kick in the mapped value
    lock.apply(p, clock(120), DT)
    assert p["f"] == pytest.approx(4.0 * 1.5, rel=0.03)         # scaled with the pace, not snapped away
    assert lock.locks == {"f": 0.5}


def test_a_locked_value_off_the_params_range_stays_free():
    lock = TempoLock({"f": hz(0.2, 3.5, 3.0)})                  # 4 Hz is off the range
    assert settle(lock, 3.0, clock(120)) == 3.0 and lock.locks == {}


def test_a_stopped_pace_and_unmarked_params_pass_untouched():
    lock = TempoLock({"f": hz(0.0, 12.0), "g": {"type": "number", "minimum": 0, "maximum": 9, "default": 3.0}})
    p = {"f": 0.0, "g": 3.0}
    for _ in range(FPS):
        lock.apply(p, clock(120), DT)
    assert p == {"f": 0.0, "g": 3.0} and set(lock.units) == {"f"}


# -- the phase lock -------------------------------------------------------------------------------------------------

def test_advance_without_a_lock_integrates():
    assert advance(0.25, 2.0, 0.5, None, "f") == 1.25
    assert advance(0.25, 2.0, 0.5, clock(120), "f") == 1.25      # not locked: free


def test_advance_pulls_the_phase_onto_the_grid_and_never_backwards():
    t, phase = 0.0, 0.37                                        # a cycle per beat at 120 bpm, starting off the grid
    for _ in range(int(4 * FPS)):
        t += DT
        new = advance(phase, 2.0, DT, clock(120, beats=2.0 * t, locks={"f": 1.0}), "f")
        assert new - phase >= 0.2 * 2.0 * DT - 1e-12            # never backwards (at least 0.2 x the rate)
        assert new - phase <= 2.0 * 2.0 * DT + 1e-12            # at most double speed
        phase = new
    err = (2.0 * t - phase + 0.5) % 1.0 - 0.5
    assert abs(err) < 0.01


def test_advance_with_a_negative_rate_locks_too():
    t, phase = 0.0, -0.3
    for _ in range(int(4 * FPS)):
        t += DT
        phase = advance(phase, -0.5, DT, clock(120, beats=2.0 * t, locks={"f": 4.0}), "f")
    err = (-(2.0 * t) / 4.0 - phase + 0.5) % 1.0 - 0.5
    assert abs(err) < 0.01


# -- the session's clock -------------------------------------------------------------------------------------------

class Feed:
    def __init__(self, c):
        self.c = c

    def clock(self, now):
        return self.c


class Sampler:
    def __init__(self, lead=0.25, follow="extrapolate", grid=None):
        self.lead, self.follow, self.grid = lead, follow, grid

    def beat_grid(self, now):
        return self.grid


class Runtime:
    def __init__(self, c=None, now=10.0, **kw):
        self.feed, self.now, self.sampler = Feed(c), now, Sampler(**kw)


SHOW = {"bpm": 120.0, "anchor_ms": 0.0, "beat_at_anchor": 0.0, "beats_per_bar": 4, "bar_anchor_beat": 1.0,
        "gate": "beat", "state": "locked"}


def test_beat_clock_from_show_service_at_photon_time():
    c = beat_clock(Runtime(SHOW, now=10.0, lead=0.25))
    assert c is not None and c.source == "show" and c.bpm == 120.0 and c.bar_beat == 1.0
    assert c.beats == pytest.approx(20.5)                       # (10 s + 0.25 s lead) x 2 beats/s


@pytest.mark.parametrize("change", [{"gate": "dmx"}, {"state": "searching"}, {"bpm": 0.0}])
def test_beat_clock_needs_a_sure_clock(change):
    assert beat_clock(Runtime({**SHOW, **change})) is None


def test_beat_clock_falls_back_to_the_desk_and_respects_follow():
    c = beat_clock(Runtime(None, now=10.0, lead=0.0, grid=(9.0, 0.5)))
    assert c is not None and c.source == "desk" and c.bpm == 120.0 and c.beats == pytest.approx(2.0)
    assert beat_clock(Runtime(SHOW, follow="hold")) is None
    assert beat_clock(None) is None and beat_clock(object()) is None


# -- the slot ----------------------------------------------------------------------------------------------------

class Mod:
    def __init__(self, props, runtime):
        self.props, self.runtime = props, runtime

    def apply(self, p):
        return p


class Pacer:
    name, stage = "pacer", "motion"
    props = {"amount": {"type": "number", "minimum": 0, "maximum": 1, "default": 1.0}, "frequency": hz()}

    def __init__(self):
        self.easer = ParamEaser(self.props, {k: p["default"] for k, p in self.props.items()}, 0.3)
        self.seen, self.clocks = [], []

    def step_params(self, dt):
        self.easer.step(dt)
        return self.easer.values()

    def update(self, params):
        self.easer.set_targets(params)

    def render(self, t_local, dt, src, out):
        self.seen.append(self.step_params(dt))
        self.clocks.append(getattr(self, "beat_clock", None))
        out[...] = src


def test_slot_locks_its_paces_and_hands_the_clock_to_the_plugin():
    eff = Pacer()
    rt = Runtime(SHOW, now=0.0, lead=0.0)
    s = EffectSlot("e1", eff, name=eff.name, stage=eff.stage, modulation=Mod(Pacer.props, rt))
    pool = Pool()
    src, out = np.zeros((1, 1, 4)), np.zeros((1, 1, 4))
    for n in range(4 * FPS):
        rt.now = n / FPS
        fc = FrameCtx(n, FPS, pool)
        assert s.frame(fc)
        s.render(fc, src, out)
    assert eff.seen[-1]["frequency"] == pytest.approx(4.0, rel=1e-3)
    c = eff.clocks[-1]
    assert c.locks == {"frequency": 0.5} and c.beats == pytest.approx(2.0 * rt.now)
    assert s.describe()["tempo"] == {"frequency": 0.5}
    rt.feed.c = None                                            # the clock goes: the plug-in sees None
    s.frame(FrameCtx(4 * FPS, FPS, pool))
    s.render(FrameCtx(4 * FPS, FPS, pool), src, out)
    assert eff.clocks[-1] is None and "tempo" not in s.describe()


def test_slot_without_modulation_is_untouched():
    eff = Pacer()
    s = EffectSlot("e1", eff, name=eff.name, stage=eff.stage)
    fc = FrameCtx(0, FPS, Pool())
    s.frame(fc)
    s.render(fc, np.zeros((1, 1, 4)), np.zeros((1, 1, 4)))
    assert eff.seen[-1]["frequency"] == 3.0 and eff.clocks[-1] is None and s.tempo is None


def test_math_sanity():
    assert math.isclose(clock(120).bps, 2.0) and clock(120, beats=5.0).bar_phase == pytest.approx(0.25)
