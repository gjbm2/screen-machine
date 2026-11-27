"""The staging planners (show_service/staging): room geometry under the light scenes. Built from a made-up room and
two made-up bars, never a deployment's files: a planner must work in any room."""
from __future__ import annotations

import math

import pytest

from show_service import space, staging
from show_service.staging import Key, Track, geom

ROOM = {"version": 1, "room": {"size": [6.0, 8.0, 3.5], "front": "north"},
        "layouts": {"bar2": {"parts": {
            "spot1": {"along": -0.45, "up": -0.13, "head": True, "hang": True},
            "spot2": {"along": 0.45, "up": -0.13, "head": True, "hang": True}}}},
        "bars": {"left": {"at": [0.5, 7.5, 2.4], "facing_deg": -60.0, "layout": "bar2"},
                 "right": {"at": [5.5, 7.5, 2.4], "facing_deg": -120.0, "layout": "bar2"}},
        "regions": {"floor-area": {"surface": "floor", "centre": [3.0, 4.0, 0.0], "size": [3.0, 4.0]}},
        "points": {"mic": {"at": [3.0, 6.5, 1.2]}}}


def _view() -> dict:
    v = space.build_view(ROOM)
    assert not v["problems"], v["problems"]
    return v


def _lights(view: dict) -> list[staging.Light]:
    out = []
    for fid, a in sorted(view["appliances"].items()):
        pose = a.get("pose")
        if not pose:
            continue

        def aim(q, cur, _p=pose):
            g = space.aim(_p, q, current=cur, pan_range=(-270, 270), tilt_range=(-135, 135))
            return g["pan"], g["tilt"], g["reachable"]
        out.append(staging.Light(fid, tuple(a["at"]), 17.0, aim, (180.0, 120.0), bar=a.get("bar")))
    return out


def _plan(name: str, **w):
    view = _view()
    prm, probs = staging.parse(name, w)
    assert not probs, probs
    return staging.make(name, prm, view, _lights(view), ramp_s=0.06, jerk_s=0.06), view


def test_track_steps_in_the_dark_and_glides_lit():
    tr = Track((Key(0.0, (0.0, 0.0, 0.0), 1.0), Key(0.5, (2.0, 0.0, 0.0), 1.0), Key(0.75, (2.0, 0.0, 0.0), 0.0),
                Key(0.9, (9.0, 0.0, 0.0), 0.0)))
    assert tr.sample(0.25) == ((1.0, 0.0, 0.0), 1.0)                       # lit: straight along
    at, lv = tr.sample(0.8)
    assert at == (9.0, 0.0, 0.0) and lv == 0.0                           # dark: already where it goes next
    assert tr.sample(1.25) == tr.sample(0.25)                            # a loop
    assert Track(tr.keys).sample(2.0, loop=False) == ((9.0, 0.0, 0.0), 0.0)   # once: held at the end


def test_parse_checks_the_settings():
    prm, probs = staging.parse("wall-run", {"spots": 3, "direction": "sideways", "colour": "red"})
    assert prm["spots"] == 3 and prm["direction"] == "cw"
    assert any("direction" in p for p in probs) and any("colour" in p for p in probs)
    assert staging.parse("nope", {})[0] is None


def test_spot_score_prefers_round_and_refuses_grazing():
    at = (0.0, 0.0, 2.0)
    wall_n = (-1.0, 0.0, 0.0)                                            # a wall at x = 5 facing back at the light
    head_on = geom.spot_score(at, (5.0, 0.0, 2.0), wall_n, 17.0)
    slanted = geom.spot_score(at, (5.0, 4.0, 2.0), wall_n, 17.0)
    assert head_on < slanted
    assert geom.spot_score(at, (5.0, 90.0, 2.0), wall_n, 17.0) == math.inf     # nearly along the wall
    assert geom.spot_score(at, (0.5, 0.0, 2.0), wall_n, 17.0) == math.inf      # the wall beside it


def test_the_wall_strip_leaves_out_the_front_and_turns_the_corners():
    st = geom.strip(_view(), height=2.0, margin=0.5)
    assert [w for w, *_ in st.walls] == ["wall-e", "wall-s", "wall-w"]  # front north: the other three, in order
    assert st.length == pytest.approx(8.0 + 6.0 + 8.0 - 1.0)
    q, n, wall = st.point(st.length / 2.0)
    assert wall == "wall-s" and q[1] == pytest.approx(0.0) and n == (0.0, 1.0, 0.0)


def test_wall_run_hands_each_spot_on_and_lights_go_back_dark():
    plan, view = _plan("wall-run", spots=2)
    assert plan.tracks and not plan.problems
    lights = {lt.id: lt for lt in _lights(view)}
    for w in [k / 40 for k in range(40)]:                               # every moment: two spots, each lit once
        lit = [fid for fid, tr in plan.tracks.items() if tr.sample(w)[1] > 0.99]
        assert len(lit) <= 2
    for fid, tr in plan.tracks.items():                                  # a lit stretch never turns a head far
        cur = lights[fid].home
        prev = None
        for k in tr.keys:
            pan, tilt, _r = lights[fid].aim(k.at, cur)
            if prev is not None and prev[2] > 0 and k.level > 0:
                assert max(abs(pan - prev[0]), abs(tilt - prev[1])) <= staging.FLIP_DEG
            cur = (pan, tilt)
            prev = (pan, tilt, k.level)
    assert 0.0 < plan.min_period_s < 60.0


def test_ring_fits_its_circle_to_the_region():
    plan, _view_ = _plan("ring", centre=["floor-area"])
    assert plan.tracks
    pts = [k.at for tr in plan.tracks.values() for k in tr.keys if k.level > 0]
    r = [math.hypot(x - 3.0, y - 4.0) for x, y, _z in pts]
    assert max(r) <= 0.8 * 1.5 + 1e-6 and min(r) > 0.5                   # fill 0.8 of the nearer half-size


def test_glide_uses_one_bars_pair_and_ends_on_a_place_once():
    plan, view = _plan("glide")
    lit = {fid for fid, tr in plan.tracks.items() if any(k.level > 0 for k in tr.keys)}
    assert len(lit) == 2 and len({view["appliances"][f]["bar"] for f in lit}) == 1
    assert not plan.once
    tr = plan.tracks[sorted(lit)[0]]
    assert tr.sample(0.95)[1] == 0.0                                      # out, going back in the dark
    plan2, _v = _plan("glide", end=["mic"])
    assert plan2.once
    for fid in lit:
        at, lv = plan2.tracks[fid].sample(1.0, loop=False)
        assert lv == 1.0 and at == pytest.approx((3.0, 6.5, 1.2))


def test_a_room_without_a_size_plans_nothing_and_says_why():
    view = space.build_view({"version": 1})
    plan = staging.make("wall-run", staging.parse("wall-run", {})[0], view,
                        [staging.Light("x", (0.0, 0.0, 2.0), 17.0, lambda q, c: (0.0, 0.0, True))])
    assert not plan.tracks and plan.problems
