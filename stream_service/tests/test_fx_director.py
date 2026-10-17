"""The FX director's pure decisions (EFFECTS_LAYER_DESIGN D.4)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import pytest

from stream_service.fx_director import (FX_BUS_BUDGET_MS, FX_INTERVAL_AT_0, FX_INTERVAL_AT_1, FxDirector, fade_for,
                                        interval_for)
from stream_service.presets import EffectSpec

PRESETS = {
    "warm": {"effect": "grade", "stage": "colour", "tags": {"energy": [0.0, 0.6], "moods": ["warm"], "weight": 1.0}},
    "cool": {"effect": "grade", "stage": "colour", "tags": {"energy": [0.0, 0.6], "moods": ["cool"], "weight": 1.0}},
    "kick": {"effect": "shake", "stage": "motion", "tags": {"energy": [0.6, 1.0], "moods": ["euphoric"],
                                                            "style": ["wild"], "weight": 1.0}},
    "split": {"effect": "chroma", "stage": "glitch", "tags": {"energy": [0.0, 1.0], "weight": 1.0}},
    "heavy": {"effect": "cube", "stage": "projection", "tags": {"weight": 1.0}},
    "manual-only": {"effect": "flash", "stage": "light"},                       # untagged: never auto
    "lab-unreviewed": {"effect": "x", "stage": "overlay", "tags": {"weight": 1.0, "auto": False}},
}


@dataclass
class St:
    id: str
    preset: str
    origin: str = "auto"
    stage: str = ""
    spec: EffectSpec = field(init=False)

    def __post_init__(self):
        p = PRESETS[self.preset]
        self.stage = self.stage or p["stage"]
        self.spec = EffectSpec(p["effect"], {}, self.preset, self.preset)


def director(density=1.0, rate=0.5, mx=2, seed=1, **dirn):
    d = FxDirector(seed)
    d.configure({"enabled": True, "pool": "all", "max": mx},
                {"energy": 0.5, "moods": [], "transition_style": None, **dirn,
                 "fx": {"density": density, "rate": rate, "stages": dirn.get("stages"), "moods": dirn.get("fxmoods")}})
    return d


def cost(name):
    return {"heavy": 2.9}.get(name, 0.5)


def test_interval_vs_rate_is_log_linear_and_jittered():
    assert interval_for(0) == FX_INTERVAL_AT_0 and interval_for(1) == pytest.approx(FX_INTERVAL_AT_1)
    assert interval_for(0.5) == pytest.approx(math.sqrt(FX_INTERVAL_AT_0 * FX_INTERVAL_AT_1))
    assert fade_for(0) == 6.0 and fade_for(1) == 2.0
    d = director(rate=1.0)
    d.arm(True, 0.0)
    assert 0.5 * 15 * 0.75 <= d.next_at <= 0.5 * 15 * 1.25                    # first move: half an interval
    for _ in range(20):
        d.done(100.0, None)
        assert 100 + 15 * 0.75 <= d.next_at <= 100 + 15 * 1.25
    a, b = director(seed=5), director(seed=5)
    a.arm(True, 0.0), b.arm(True, 0.0)
    assert a.next_at == b.next_at                                                # seeded determinism


def test_count_vs_density_and_max():
    d = director(density=1.0, mx=2)
    assert all(d.target_count() == 2 for _ in range(50))
    d = director(density=0.0, mx=2)
    assert all(d.target_count() == 0 for _ in range(50))
    d = director(density=0.5, mx=3)
    ks = [d.target_count() for _ in range(400)]
    assert set(ks) <= {1, 2} and 0.35 < ks.count(2) / len(ks) < 0.65       # 1.5 -> stochastic rounding


def test_add_until_density_then_remove_oldest_auto():
    d = director(density=1.0, mx=2)
    m = d.plan(0, [], PRESETS, cost)
    assert m.op == "add" and m.add in {"warm", "cool", "kick", "split", "heavy"} and "p " in m.why
    d = director(density=0.0, mx=2)
    stack = [St("fx-1", "warm"), St("fx-2", "kick")]
    m = d.plan(0, stack, PRESETS, cost)
    assert m.op == "remove" and m.remove == "fx-1"


def test_one_effect_per_stage_and_untagged_or_auto_false_never():
    d = director(density=1.0, mx=4)
    stack = [St("fx-1", "warm"), St("fx-2", "kick"), St("fx-3", "split")]
    for _ in range(30):
        m = d.plan(0, stack, PRESETS, cost)
        assert m is None or (m.op == "add" and m.add == "heavy") or m.op == "swap"
    cands = d.candidates(0, stack, PRESETS, cost, 1.0)
    assert "cool" not in cands and "manual-only" not in cands and "lab-unreviewed" not in cands


def test_stage_allow_list():
    d = director(density=1.0, mx=2, stages=["motion"])
    assert d.candidates(0, [], PRESETS, cost, 1.0) == ["kick"]


def test_moods_energy_and_style_weights():
    d = director(energy=0.9, moods=["euphoric"], transition_style="wild")
    assert d.weight(PRESETS["kick"]["tags"]) == 1.0
    assert d.weight(PRESETS["warm"]["tags"]) == pytest.approx(0.3 * 0.3)
    d = director(fxmoods=["cool"])                                               # fx.moods overrides direction
    assert d.weight(PRESETS["cool"]["tags"]) == 1.0 and d.weight(PRESETS["warm"]["tags"]) == pytest.approx(0.3)
    picks = [director(seed=i, energy=0.9, moods=["euphoric"], stages=["colour", "motion"]).plan(0, [], PRESETS, cost).add
             for i in range(200)]
    assert picks.count("kick") > picks.count("warm") * 2


def test_novelty_downweights_recent_picks():
    d = director(stages=["colour"])
    d.recent.extend(["warm"] * 4)
    picks = [d._pick(["warm", "cool"], PRESETS)[0] for _ in range(400)]
    assert picks.count("cool") > picks.count("warm") * 2


def test_budget_excludes_costly_candidates():
    d = director(density=1.0, mx=4)
    stack = [St("fx-1", "warm")]
    assert "heavy" not in d.candidates(0, stack, PRESETS, cost, 1.0)           # 0.5 + 2.9 > 3.0
    assert "heavy" in d.candidates(0, stack, PRESETS, cost, 0.25)               # 1080p: budget 12 ms
    assert "heavy" in d.candidates(0, [], PRESETS, cost, 1.0)
    assert FX_BUS_BUDGET_MS == 3.0


def test_manual_slots_are_never_touched_but_count():
    d = director(density=0.0, mx=2)
    stack = [St("fx-1", "warm", origin="manual"), St("fx-2", "kick", origin="manual")]
    for _ in range(20):
        assert d.plan(0, stack, PRESETS, cost) is None
    d = director(density=1.0, mx=2)
    cands = d.candidates(0, stack, PRESETS, cost, 1.0)
    assert "cool" not in cands and "kick" not in cands                         # manual slots hold their stages


def test_swap_keeps_the_count():
    d = director(density=1.0, mx=1, seed=3)
    stack = [St("fx-1", "warm")]
    moves = [d.plan(0, stack, PRESETS, cost) for _ in range(100)]
    swaps = [m for m in moves if m is not None]
    assert swaps and all(m.op == "swap" and m.remove == "fx-1" and m.add != "warm" for m in swaps)
    assert 0.4 < len(swaps) / len(moves) < 0.8


def test_quarantine_and_disarm():
    d = director(stages=["colour"])
    d.quarantine("warm", 0.0)
    assert d.candidates(1.0, [], PRESETS, cost, 1.0) == ["cool"]
    assert "warm" in d.candidates(700.0, [], PRESETS, cost, 1.0)
    d.arm(True, 0.0)
    d.next_at = 0.0
    assert d.due(1.0)
    d.arm(False, 1.0)
    assert not d.due(1.0) and d.next_at is None
    off = FxDirector(1)
    off.configure({"enabled": False, "pool": "all", "max": 2}, {"fx": {}})
    off.arm(True, 0)
    off.next_at = 0
    assert not off.due(5) and off.plan(0, [], PRESETS, cost) is None


# -- on the music (owner, 17 Oct) ------------------------------------------------------------------------------------

def on_clock(d, beat_s=0.5, bpb=4):
    d.clock = lambda now: (beat_s, bpb)
    return d


def test_on_the_clock_the_cadence_is_whole_phrases_and_fades_whole_beats():
    d = on_clock(director(rate=1.0))                                    # 15 s at 120 bpm: 7.5 bars -> 8
    d.arm(True, 0.0)
    assert d.grid == (0.5, 4) and d.status(0.0)["on_the_bar"]
    for _ in range(30):
        d.done(100.0, None)
        assert (d.next_at - 100.0) / 2.0 in (4, 8)                      # 15 s x jitter -> 4 or 8 bars
    assert d.interval() == 16.0
    assert d.fade_s() == 2.0                                            # rate 1: 2 s -> 4 beats
    assert director(rate=0.0).fade_s() == 6.0 and on_clock(director(rate=0.0)).fade_s() == 6.0
    d = on_clock(director(rate=0.0))
    d.arm(True, 0.0)
    assert d.fade_s() == 8.0                                            # 6 s -> 16 beats (8 s), the nearest
    d.clock = lambda now: None                                          # the clock goes: seconds again
    d.arm(True, 1.0)
    assert d.grid is None and d.fade_s() == 6.0


def test_a_drop_brings_an_effect_at_once_fading_in_on_a_beat():
    d = on_clock(director(density=0.0, mx=2))
    d.arm(True, 0.0)
    assert d.on_event("drop", 50.0) and d.due(50.0) and d.event == "drop"
    move = d.plan(50.0, [], PRESETS, cost)
    assert move.op == "add" and "on the drop" in move.why            # density 0, but a drop always brings one
    assert d.fade_s() == 0.5                                            # one beat
    d.done(51.0, move)
    assert d.event is None and d.last_move_at == 51.0 and not d.due(52.0)
    assert not d.on_event("drop", 55.0)                                 # too soon after the last move


def test_a_section_change_swaps_and_follow_zero_ignores_the_room():
    d = director(density=1.0, mx=1)
    d.arm(True, 0.0)
    stack = [St("e1", "warm")]
    hits = 0
    for i in range(40):
        d.last_move_at = None
        if d.on_event("section_change", 100.0 + i):
            hits += 1
            move = d.plan(100.0 + i, stack, PRESETS, cost)
            assert move is not None and move.op == "swap"                # an event always changes something
            d.event = None
    assert 5 < hits < 30                                                # 0.5 x follow 0.8
    off = director(density=1.0, mx=1)
    off.direction["fx"]["follow"] = 0.0                                 # direction.fx.follow (config)
    off.arm(True, 0.0)
    assert not any(off.on_event(k, 10.0) for k in ("drop", "section_change", "breakdown", "track_change"))


def test_a_breakdown_thins_the_bus_until_the_music_lifts():
    d = director(density=1.0, mx=2, seed=3)
    d.arm(True, 0.0)
    while not d.on_event("breakdown", 10.0):
        d.last_move_at = None
    stack = [St("e1", "warm"), St("e2", "split")]
    move = d.plan(10.0, stack, PRESETS, cost)
    assert move.op == "remove" and move.remove == "e1" and d.status(10.0)["thinned"]
    d.event = None
    for _ in range(10):                                                 # thinned to 1: never adds a second
        move = d.plan(20.0, stack[1:], PRESETS, cost)
        assert move is None or move.op == "swap"
    d.on_event("drop", 30.0)                                            # a lift ends the thinning
    assert d.thin_until is None
    d.event = None
    assert d.plan(40.0, stack[1:], PRESETS, cost).op == "add"
    d.thin_until = 60.0                                                 # a thinning also runs out
    d.plan(61.0, stack, PRESETS, cost)
    assert d.thin_until is None


def test_events_only_while_armed_and_enabled():
    d = director()
    assert not d.on_event("drop", 1.0) and d.next_at is None
