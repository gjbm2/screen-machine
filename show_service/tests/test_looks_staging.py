"""looks: planned position layers (show_service/staging; docs/LIGHTS.md §5.10) and a colour layer's own hex. Built in
memory with test_looks_motion's helpers: the real engine, the real room geometry, a fake clock."""
from __future__ import annotations

import math

import pytest

from show_service import space as sp
from show_service.tests.test_looks import DMX_CAPS, out, rigview, look
from show_service.tests.test_looks_motion import FAST, MOVER, at, engine, mover, pose, run, scene


def _rig_and_room():
    hang = [math.pi, 0.0, 0.0]                                            # hanging, facing +x
    apps = {"a": {"at": [0.5, 0.5, 2.8], "pose": pose([0.5, 0.5, 2.8], hang)},
            "b": {"at": [5.5, 7.5, 2.8], "pose": pose([5.5, 7.5, 2.8], hang)}}
    view = sp.build_view({"version": 1, "room": {"size": [6.0, 8.0, 3.0], "front": "north"}, "appliances": apps})
    assert view["problems"] == [], view["problems"]
    rig = rigview([mover("a", 1, setup={"max_speed": FAST}), mover("b", 30, setup={"max_speed": FAST})], [MOVER],
                  [out("dmx", "artnet")])
    return rig, view


def test_a_planned_ring_lands_on_its_circle_and_the_free_head_is_dark():
    rig, view = _rig_and_room()
    sc = scene({"id": "ring", "kind": "position", "mode": "planned", "plan": "ring", "with": {"spots": 1},
                "phase": "bar", "period_bars": 2},
               {"id": "white", "kind": "colour", "hex": "#ffffff"})
    box = {"look": look(light_scene="m"), "pal": {"*": (1.0, 0.0, 0.0)}}
    e = engine(rig, box, {"m": sc}, space=view, lights={"motion": {"accel_s": 0}})
    assert box["probs"] == [] and e._program.scene_problems == (), e._program.scene_problems
    frames = run(e, box, 10.0, 18.0)
    lit_by = set()
    for _t, fr in frames[20:]:
        lit = [f for f in ("a", "b") if fr["fixtures"][f]["intensity"] > 0.5]
        assert len(lit) <= 1 or any(0.05 < fr["fixtures"][f]["intensity"] < 0.95 for f in ("a", "b"))  # a handover
        for f in lit:
            pv = view["appliances"][f]["pose"]
            a = at(fr, f)
            hit, surf = sp.ray_hit(view, pv["at"], sp.beam_dir(pv, a["pan"], a["tilt"]))
            assert surf == "floor"
            assert math.hypot(hit[0] - 3.0, hit[1] - 4.0) == pytest.approx(1.2, abs=0.15)
            assert fr["fixtures"][f]["colour"] == pytest.approx([1.0, 1.0, 1.0])   # its own white, not the palette
            lit_by.add(f)
    assert lit_by == {"a", "b"}                                           # the spot is handed from one to the other
    dark = [fr["fixtures"][f]["intensity"] for _t, fr in frames[20:] for f in ("a", "b")]
    assert min(dark) < 0.05                                               # the free head goes back in the dark
    assert "spot" in e.stats()["plans"]["m/ring"]


def test_an_unknown_plan_or_a_bad_hex_is_left_out_with_a_problem_line():
    rig, view = _rig_and_room()
    sc = scene({"id": "x", "kind": "position", "mode": "planned", "plan": "nope"},
               {"id": "c", "kind": "colour", "hex": "red"})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc}, space=view)
    probs = " ".join(e._program.scene_problems)
    assert "plan 'nope'" in probs and "hex 'red'" in probs


def test_without_a_room_a_planned_layer_leaves_the_light_where_it_was():
    rig, _view = _rig_and_room()
    sc = scene({"id": "ring", "kind": "position", "mode": "planned", "plan": "ring", "phase": "bar"})
    box = {"look": look(light_scene="m")}
    e = engine(rig, box, {"m": sc})                                       # no space: nothing is calibrated
    fr = e.evaluate("dmx", 10.0, DMX_CAPS)
    assert fr["fixtures"]["a"]["intensity"] > 0.5                         # not darkened by a plan it doesn't have
    assert "m/ring" in e.stats()["motion"]["a"]["unresolved"]


def test_a_scene_tagged_alone_is_drawn_only_while_its_group_plays_alone():
    from show_service import light_scenes
    cat = light_scenes.Catalogue()
    cat.poll()
    assert not [p for p in cat.problems if "glide" in p or "chase" in p or "frenzy" in p or "heavy" in p or "snare" in p], cat.problems
    movers = {"label": "Heads", "members": ["mv"], "member_caps": {"mv": ["pan", "tilt", "intensity", "colour"]}}
    assert "movers-twin-glide" in cat.scenes
    assert "movers-twin-glide" not in cat.pool_for(movers, rest=False, alone=None)
    assert "movers-twin-glide" not in cat.pool_for(movers, rest=False, alone=False)
    assert "movers-twin-glide" in cat.pool_for(movers, rest=False, alone=True)
