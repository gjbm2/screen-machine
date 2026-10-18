"""space: the room frame, flow geometry, screens' view of the flow, mirror symmetry, the side/height selector words, the
mover pose solve and aim, ray hits, and the store (merge, last good, atomic writes, subscribers, refusals) (docs/
LIGHTS.md §3.7). Config files are read, never written: the store's tests use tmp files only.

The room here is synthetic (6 × 8 × 3 m, front north) and its places are made up on purpose.
"""

from __future__ import annotations

import gc
import math
import os
import random

import pytest
import yaml

from show_service import contract
from show_service import space as sp

ROOM = {"room": {"size": [6, 8, 3]}}
RANGES = ((-270.0, 270.0), (-135.0, 135.0))
P_TRUE = (2.0, 3.0, 2.9)


def _view(**extra):
    doc = {"room": {"size": [6, 8, 3], "front": "north"}}
    doc.update(extra)
    return sp.build_view(doc)


def _rotvec(yaw_deg: float, hung: bool) -> list[float]:
    from scipy.spatial.transform import Rotation
    r = Rotation.from_euler("z", math.radians(yaw_deg))
    if hung:
        r = r * Rotation.from_euler("x", math.pi)
    return list(r.as_rotvec())


def _pose(at=P_TRUE, yaw=37.0, hung=True, off=3.0, ps=-1, ts=1) -> dict:
    return {"at": list(at), "rot": _rotvec(yaw, hung), "tilt_offset_deg": off, "pan_sign": ps, "tilt_sign": ts,
            "rms_deg": 0.0, "worst_deg": 0.0, "samples": 0, "solved_at": ""}


def _teach(pose: dict, pts, rng: random.Random, noise: float = 0.3) -> list[dict]:
    out = []
    for q in pts:
        a = sp.aim(pose, q, current=(0, 0), pan_range=RANGES[0], tilt_range=RANGES[1])
        assert a["reachable"]
        out.append({"pan": a["pan"] + rng.gauss(0, noise), "tilt": a["tilt"] + rng.gauss(0, noise), "point": None,
                    "at": list(q), "t": ""})
    return out


# -- the frame and the view --------------------------------------------------------------------------------------------
def test_room_surfaces_face_into_the_room_with_u_left_to_right():
    v = _view()
    assert v["error"] is None and v["problems"] == []
    assert list(v["surfaces"]) == list(sp.ROOM_SURFACES)
    assert v["room"]["centre"] == [3.0, 4.0, 1.5] and v["room"]["right"] == [1.0, 0.0, 0.0]
    assert v["extent"] == [[0.0, 0.0, 0.0], [6.0, 8.0, 3.0]]
    centre = (3.0, 4.0, 1.5)
    for name, s in v["surfaces"].items():
        n, u, vv = s["normal"], s["u"], s["v"]
        assert sum(a * b for a, b in zip(n, u)) == pytest.approx(0) and sum(a * b for a, b in zip(u, vv)) == 0
        o = s["origin"]
        assert sum((c - x) * y for c, x, y in zip(centre, o, n)) > 0, name        # the normal points into the room
        far = sp.surface_point(s, *s["size"])                                     # the far corner is in the box
        assert all(-1e-9 <= far[i] <= (6, 8, 3)[i] + 1e-9 for i in range(3)), name
    # facing a wall from inside, u runs left to right: on the north wall that is west -> east, on the south east -> west
    assert v["surfaces"]["wall-n"]["u"] == [1.0, 0.0, 0.0] and v["surfaces"]["wall-s"]["u"] == [-1.0, 0.0, 0.0]
    assert v["surfaces"]["ceiling"]["u"] == [1.0, 0.0, 0.0] and v["surfaces"]["ceiling"]["v"] == [0.0, 1.0, 0.0]


def test_no_size_still_has_the_floor_and_a_placed_extent():
    v = sp.build_view({"points": {"piano": {"at": [1, 2, 1]}, "mirror-ball": {"at": [3, 5, 2.5]}}})
    assert list(v["surfaces"]) == ["floor"] and v["surfaces"]["floor"]["size"] is None
    assert v["room"]["size"] is None and v["room"]["centre"] is None
    assert v["extent"] == [[1, 2, 1], [3, 5, 2.5]]
    assert sp.flow_coord(v, "up", None, (2, 3, 2.5)) == 1.0
    empty = sp.empty_view()
    assert empty["revision"] == "" and empty["extent"] is None
    assert sp.flow_coord(empty, "right", None, (1, 1, 1)) == 0.0
    assert sp.flow_vector(empty, "cw", None, (1, 1, 1)) == sp.ZERO


def test_labels_resolve_regions_and_surface_maths():
    v = _view(points={"mirror-ball": {"at": [3, 4, 2.6], "device": "mirror_ball"}},
              regions={"dance-floor": {"surface": "floor", "centre": [3, 3, 0], "size": [2, 2]}},
              appliances={"screen.north": {"at": [3, 8, 1.5]}},
              surfaces={"tv-wall": {"label": "TV wall", "origin": [0, 7.5, 0], "normal": [0, -1, 0], "up": [0, 0, 1],
                                    "size": [6, 3]}})
    assert v["points"]["mirror-ball"] == {"label": "Mirror ball", "at": [3, 4, 2.6], "from": "hand",
                                          "device": "mirror_ball"}
    assert v["regions"]["dance-floor"]["label"] == "Dance floor"
    assert sp.resolve(v, "mirror-ball") == (3, 4, 2.6) and sp.resolve(v, "dance-floor") == (3, 3, 0)
    assert sp.resolve(v, "screen.north") == (3, 8, 1.5) and sp.resolve(v, "nowhere") is None
    assert sp.resolve(v, "ceiling") == (3.0, 4.0, 3.0)
    surf, centre, size = sp.region_of(v, "dance-floor")
    assert surf["name"] == "floor" and centre == (3, 3, 0) and size == [2, 2]
    assert sp.surface_of(v, "dance-floor")["name"] == "floor"
    assert sp.surface_of(v, "mirror-ball")["name"] == "ceiling"                     # a place: its nearest surface
    assert sp.surface_of(v, "screen.north")["name"] == "wall-n"
    tv = v["surfaces"]["tv-wall"]
    assert tv["u"] == [1.0, 0.0, 0.0] and tv["v"] == [0.0, 0.0, 1.0]
    for s in v["surfaces"].values():
        p = sp.surface_point(s, 1.25, 0.5)
        assert sp.surface_uv(s, p) == pytest.approx((1.25, 0.5))
    names = [t["name"] for t in sp.targets(v)]
    assert names[:3] == ["mirror-ball", "dance-floor", "screen.north"] and "floor" in names


# -- flow (§3.5) -------------------------------------------------------------------------------------------------------
def test_flow_coord_for_every_dir():
    v = _view()
    fc = lambda d, p, c=None: sp.flow_coord(v, d, c, p)                             # noqa: E731
    assert fc("right", (6, 4, 1)) == 1.0 and fc("right", (0, 4, 1)) == 0.0 and fc("right", (3, 1, 2)) == 0.5
    assert fc("left", (6, 4, 1)) == 0.0 and fc("left", (0, 4, 1)) == 1.0
    assert fc("up", (1, 1, 0)) == 0.0 and fc("up", (1, 1, 3)) == 1.0 and fc("down", (1, 1, 3)) == 0.0
    assert fc("up", (1, 1, 1.5)) == pytest.approx(0.5)
    assert fc("cw", (3, 6, 1.5)) == pytest.approx(0.0)                               # north of the centre
    assert fc("cw", (5, 4, 1.5)) == pytest.approx(0.25)                              # east
    assert fc("cw", (3, 2, 1.5)) == pytest.approx(0.5)                               # south
    assert fc("cw", (1, 4, 1.5)) == pytest.approx(0.75)                              # west
    assert fc("ccw", (1, 4, 1.5)) == pytest.approx(0.25) and fc("ccw", (3, 6, 1.5)) == pytest.approx(0.0)
    assert fc("outward", (3, 4, 1.5)) == 0.0 and fc("outward", (0, 0, 0)) == pytest.approx(1.0)
    assert fc("inward", (3, 4, 1.5)) == 1.0 and fc("inward", (6, 8, 3)) == pytest.approx(0.0)
    X = (1.0, 7.0, 1.0)
    assert fc("towards", X, X) == pytest.approx(1.0) and fc("away", X, X) == pytest.approx(0.0)
    assert fc("towards", (6, 0, 3), X) == pytest.approx(0.0)                        # the farthest corner from X
    assert fc("still", (6, 8, 3)) == 0.0 and fc("sideways", (6, 8, 3)) == 0.0
    for d in contract.FLOW_DIRS:                                                     # always 0..1
        for p in ((0, 0, 0), (6, 8, 3), (1, 7, 2), (10, -3, 9)):
            assert 0.0 <= fc(d, p) <= 1.0


def test_flow_vector_is_unit_or_zero():
    v = _view()
    rng = random.Random(4)
    for d in contract.FLOW_DIRS:
        for _ in range(20):
            p = (rng.uniform(0, 6), rng.uniform(0, 8), rng.uniform(0, 3))
            n = sp._len(sp.flow_vector(v, d, None, p))
            assert n == pytest.approx(0.0 if d == "still" else 1.0)
    for d in ("cw", "ccw", "outward", "inward", "towards", "away"):                  # zero at the centre
        assert sp.flow_vector(v, d, None, (3, 4, 1.5)) == sp.ZERO
    assert sp.flow_vector(v, "cw", None, (3, 6, 1)) == pytest.approx((1, 0, 0))     # north of it, clockwise: east
    assert sp.flow_vector(v, "towards", (3, 8, 1), (3, 4, 1)) == pytest.approx((0, 1, 0))
    assert sp.flow_vector(v, "up", None, (0, 0, 0)) == (0, 0, 1)


def test_front_turns_left_and_right():
    v = sp.build_view({"room": {"size": [6, 8, 3], "front": "e"}})
    assert v["room"]["front"] == "east" and v["room"]["right"] == [0.0, -1.0, 0.0]     # facing east, right is south
    assert sp.flow_coord(v, "right", None, (3, 0, 1)) == 1.0
    assert sp.flow_coord(v, "cw", None, (5, 4, 1)) == pytest.approx(0.0)              # the front (east) starts
    assert sp.flow_coord(v, "cw", None, (3, 2, 1)) == pytest.approx(0.25)             # then south: clockwise
    assert sp.lateral(v, (3, 1, 1)) == pytest.approx(3.0)


def test_mirror_fires_both_halves_together():
    v = _view()
    assert sp.lateral(v, (5, 4, 1)) == pytest.approx(2.0) and sp.lateral(v, (1, 4, 1)) == pytest.approx(-2.0)
    for d in ("left", "right", "outward", "inward", "cw", "ccw"):
        a = sp.flow_coord(v, d, None, (5, 6, 1), symmetry="mirror")
        b = sp.flow_coord(v, d, None, (1, 6, 1), symmetry="mirror")
        assert a == pytest.approx(b), d
    assert sp.flow_coord(v, "right", None, (5, 6, 1)) != sp.flow_coord(v, "right", None, (1, 6, 1))
    # the left half runs the right half's mirror image
    assert sp.flow_vector(v, "right", None, (5, 6, 1), symmetry="mirror") == pytest.approx((1, 0, 0))
    assert sp.flow_vector(v, "right", None, (1, 6, 1), symmetry="mirror") == pytest.approx((-1, 0, 0))
    cw_r = sp.flow_vector(v, "cw", None, (5, 6, 1), symmetry="mirror")
    cw_l = sp.flow_vector(v, "cw", None, (1, 6, 1), symmetry="mirror")
    assert cw_l == pytest.approx((-cw_r[0], cw_r[1], cw_r[2]))


# -- screens (§3.5, §9) ------------------------------------------------------------------------------------------------
def _screens_view():
    return _view(appliances={
        "screen.north": {"at": [3, 8, 1.5], "plane": {"normal": [0, -1, 0], "up": [0, 0, 1], "size": [1.4, 0.8]}},
        "screen.south": {"at": [3, 0, 1.5], "plane": {"normal": [0, 1, 0], "up": [0, 0, 1], "size": [1.4, 0.8]}},
        "screen.east": {"at": [6, 4, 1.5]}})                                       # no plane: the east wall's


def test_screen_flow_in_each_screens_own_plane():
    v = _screens_view()
    n = sp.screen_flow(v, "screen.north", "right", None)
    assert n["fixture"] == "screen.north" and n["angle_deg"] == 0.0 and n["inplane"] == 1.0 and n["depth"] == 0.0
    assert n["phase"] == 0.5 and n["radial"] == 0 and n["spin"] == 0
    assert sp.screen_flow(v, "screen.south", "right", None)["angle_deg"] == 180.0   # physically the same flow
    for s in ("screen.north", "screen.south", "screen.east"):
        up = sp.screen_flow(v, s, "up", None)
        assert up["angle_deg"] == 90.0 and up["y"] == 1.0 and up["phase"] == 0.5
    out = sp.screen_flow(v, "screen.north", "outward", None)
    assert out["depth"] == -1.0 and out["radial"] == 1 and out["inplane"] == 0.0
    assert sp.screen_flow(v, "screen.north", "inward", None)["radial"] == -1
    assert sp.screen_flow(v, "screen.north", "towards", (3, 4, 1.5))["radial"] == -1
    assert sp.screen_flow(v, "screen.north", "cw", None)["spin"] == 1
    assert sp.screen_flow(v, "screen.south", "ccw", None)["spin"] == -1
    east = sp.screen_flow(v, "screen.east", "right", None)                          # flow east, into the east wall
    assert east["depth"] == -1.0 and east["inplane"] == 0.0
    assert sp.screen_flow(v, "screen.east", "cw", None)["angle_deg"] == 0.0         # facing east, its right is south
    still = sp.screen_flow(v, "screen.north", "still", None)
    assert still["inplane"] == 0.0 and still["angle_deg"] == 0.0 and still["phase"] == 0.0
    assert sp.screen_flow(v, "screen.nowhere", "up", None) is None
    assert set(n) == set(contract.ScreenFlow.__annotations__)


# -- spatial words (fixtures.select's spatial) -------------------------------------------------------------------------
def test_spatial_fn_sides_and_heights():
    v = _view(appliances={"a": {"at": [1, 7, 0.5]}, "b": {"at": [5, 1, 2.8]}, "c": {"at": [3, 4, 1.5]},
                          "d": {"pose": _pose(at=(4, 2, 1.2))}})
    sp_fn = sp.spatial_fn(v)
    assert sp_fn("a", "side", "north") and sp_fn("a", "side", "n") and not sp_fn("a", "side", "south")
    assert sp_fn("b", "side", "south") and sp_fn("b", "side", "east") and not sp_fn("b", "side", "west")
    assert sp_fn("a", "side", "left") and sp_fn("b", "side", "right")
    assert sp_fn("a", "side", "front") and sp_fn("b", "side", "back")
    assert not sp_fn("c", "side", "north") and not sp_fn("c", "side", "south")      # on the line: neither half
    assert sp_fn("a", "height", "low") and sp_fn("c", "height", "mid") and sp_fn("b", "height", "high")
    assert sp_fn("d", "height", "mid") and sp_fn("d", "side", "south")              # a pose's position counts
    assert not sp_fn("nobody", "side", "north") and not sp_fn("a", "height", "tall") and not sp_fn("a", "zone", "x")
    turned = sp.build_view({"room": {"size": [6, 8, 3], "front": "east"},
                            "appliances": {"a": {"at": [1, 7, 0.5]}}})
    assert sp.spatial_fn(turned)("a", "side", "left") and sp.spatial_fn(turned)("a", "side", "back")
    assert not sp.spatial_fn(sp.empty_view())("a", "side", "north")


# -- movers: forward model, aim, ray hits (§3.6) -----------------------------------------------------------------------
def test_aim_round_trips_through_beam_dir():
    pose = _pose()
    rng = random.Random(11)
    for _ in range(200):
        q = (rng.uniform(0, 6), rng.uniform(0, 8), rng.uniform(0, 2.6))
        a = sp.aim(pose, q, current=(rng.uniform(-200, 200), rng.uniform(-100, 100)), pan_range=RANGES[0],
                   tilt_range=RANGES[1])
        assert a["reachable"] and a["error_deg"] == 0.0
        assert RANGES[0][0] <= a["pan"] <= RANGES[0][1] and RANGES[1][0] <= a["tilt"] <= RANGES[1][1]
        assert sp._angle_deg(sp.beam_dir(pose, a["pan"], a["tilt"]), sp._sub(q, P_TRUE)) < 0.05


def test_aim_keeps_to_the_current_turn_and_clamps_out_of_range():
    pose = _pose(off=0.0, ps=1)
    q = (5.0, 3.0, 0.0)
    near = sp.aim(pose, q, current=(200.0, 30.0), pan_range=RANGES[0], tilt_range=RANGES[1])
    far = sp.aim(pose, q, current=(-200.0, 30.0), pan_range=RANGES[0], tilt_range=RANGES[1])
    assert near["reachable"] and far["reachable"]
    assert abs(near["pan"] - 200.0) <= 180.0 and abs(far["pan"] + 200.0) <= 180.0
    assert near["pan"] - far["pan"] == pytest.approx(360.0) or near["tilt"] == pytest.approx(-far["tilt"])
    # straight up from a hung light is past its tilt limits: the nearest it can do, flagged, never refused
    up = sp.aim(pose, (2.0, 3.0, 3.0), current=(0.0, 0.0), pan_range=RANGES[0], tilt_range=RANGES[1],
                limits={"tilt": [-30, 30]})
    assert not up["reachable"] and abs(up["tilt"]) == pytest.approx(30.0)
    assert up["error_deg"] == pytest.approx(150, abs=1)
    lim = sp.aim(pose, (5.5, 7.5, 0.0), current=(0.0, 0.0), pan_range=RANGES[0], tilt_range=RANGES[1],
                 limits={"pan": [-10, 10], "tilt": [-20, 20]})
    assert -10 <= lim["pan"] <= 10 and -20 <= lim["tilt"] <= 20
    if not lim["reachable"]:
        assert lim["error_deg"] > 0
    me = sp.aim(pose, P_TRUE, current=(500.0, 0.0), pan_range=RANGES[0], tilt_range=RANGES[1])
    assert me["reachable"] and me["pan"] == 270.0                                   # aimed at itself: stays, clamped


def test_aim_prefers_the_soonest_solution():
    pose = _pose(off=0.0, ps=1, yaw=0.0)
    q = (2.0, 3.0 + 1.0, 0.0)                                                       # a little off the vertical
    a = sp.aim(pose, q, current=(0.0, 0.0), pan_range=RANGES[0], tilt_range=RANGES[1])
    b = sp.aim(pose, q, current=(180.0, 0.0), pan_range=RANGES[0], tilt_range=RANGES[1])
    for x in (a, b):
        assert sp._angle_deg(sp.beam_dir(pose, x["pan"], x["tilt"]), sp._sub(q, P_TRUE)) < 1e-6
    assert abs(a["pan"]) <= 90.0 and abs(b["pan"] - 180.0) <= 90.0                # the other branch, no turn round


def test_ray_hit():
    v = _view(surfaces={"tv-wall": {"origin": [0, 7, 0], "normal": [0, -1, 0], "up": [0, 0, 1], "size": [2, 2]}})
    assert sp.ray_hit(v, P_TRUE, (0, 0, -1)) == (pytest.approx((2, 3, 0)), "floor")
    hit, name = sp.ray_hit(v, (4, 4, 1.5), (0, 1, 0))
    assert name == "wall-n" and hit == pytest.approx((4, 8, 1.5))
    hit, name = sp.ray_hit(v, (1, 4, 1.5), (0, 1, 0))                               # the named surface is nearer
    assert name == "tv-wall" and hit == pytest.approx((1, 7, 1.5))
    hit, name = sp.ray_hit(v, (3, 4, 2.9), (0, 0, 1))                               # just under the ceiling
    assert name == "ceiling" and hit == pytest.approx((3, 4, 3))
    assert sp.ray_hit(v, (3, 4, 3), (0, 0, 1)) is None                              # on the ceiling, going out
    assert sp.ray_hit(sp.build_view({}), (3, 4, 2), (0, 0, -1)) == (pytest.approx((3, 4, 0)), "floor")
    pose = _pose()
    for pan, tilt in ((0, 0), (40, 30), (-120, 60)):                                # every beam lands somewhere
        assert sp.ray_hit(v, P_TRUE, sp.beam_dir(pose, pan, tilt)) is not None


# -- the solve (§3.6) --------------------------------------------------------------------------------------------------
FOUR = [(0.8, 0.8, 0.0), (5.2, 6.8, 0.0), (1.0, 8.0, 1.8), (6.0, 1.5, 2.0)]        # floor and walls


def test_solve_four_places():
    pose = _pose()
    for seed in (0, 1):
        fit = sp.solve_pose(_teach(pose, FOUR, random.Random(seed)), at_hint=None, mount="ceiling",
                            pan_range=RANGES[0], tilt_range=RANGES[1], view=_view())
        assert fit["level"] == "good" and fit["words"] == "Spot on (within 1.5°)."
        assert fit["rms_deg"] < 1.0 and fit["samples"] == 4 and fit["spread_deg"] > 60
        got = fit["pose"]
        assert math.dist(got["at"], P_TRUE) < 0.1 and fit["pos_m"] < 0.3
        assert got["pan_sign"] == -1 and got["tilt_sign"] == 1 and got["tilt_offset_deg"] == pytest.approx(3, abs=1)
        assert set(got) == set(contract.MoverPose.__annotations__) and set(fit) == set(contract.PoseFit.__annotations__)
        for q in ((3, 4, 0), (0.5, 7.5, 1)):                                        # it aims like the real light
            a = sp.aim(got, q, current=(0, 0), pan_range=RANGES[0], tilt_range=RANGES[1])
            assert sp._angle_deg(sp.beam_dir(pose, a["pan"], a["tilt"]), sp._sub(q, P_TRUE)) < 1.0


def test_solve_three_places_and_the_map():
    pose = _pose()
    fit = sp.solve_pose(_teach(pose, FOUR[:3], random.Random(2)), at_hint=(2.2, 2.85, 2.8), mount="ceiling",
                        pan_range=RANGES[0], tilt_range=RANGES[1], cfg={"fit_deg": [1, 3, 6]})
    assert fit["rms_deg"] < 1.5 and fit["level"] == "good" and fit["words"] == "Spot on (within 1°)."
    assert math.dist(fit["pose"]["at"], P_TRUE) < 0.3
    loose = sp.solve_pose(_teach(pose, FOUR[:3], random.Random(2)), at_hint=None, mount="ceiling",
                          pan_range=RANGES[0], tilt_range=RANGES[1], view=sp.build_view({}))
    assert loose["pose"] is not None and loose["level"] == "rough"                 # nothing ties it down
    assert loose["words"] == "Rough: point it at one more place."


def test_solve_asks_for_more_places():
    pose = _pose()
    rng = random.Random(3)
    two = sp.solve_pose(_teach(pose, FOUR[:2], rng), at_hint=None, mount=None, pan_range=RANGES[0],
                        tilt_range=RANGES[1])
    assert two["level"] == "need_more" and two["pose"] is None
    assert two["words"] == "Point it at 3 places so it learns where it hangs (2 so far)."
    line = [(0.5 + i, 1.0 + 1.2 * i, 0.0) for i in range(4)]                        # nearly in a line
    fit = sp.solve_pose(_teach(pose, line, rng), at_hint=None, mount="ceiling", pan_range=RANGES[0],
                        tilt_range=RANGES[1], view=_view())
    assert fit["level"] == "need_more" and fit["words"] == "Point it at places further apart." and fit["pose"] is None
    close = [(3.0, 4.0, 0.0), (3.3, 4.0, 0.0), (3.0, 4.3, 0.0)]                     # all close together
    fit = sp.solve_pose(_teach(pose, close, rng), at_hint=P_TRUE, mount="ceiling", pan_range=RANGES[0],
                        tilt_range=RANGES[1])
    assert fit["level"] == "need_more" and fit["spread_deg"] < 20
    assert fit["words"] == "Point it at places further apart."


def test_solve_names_a_wrong_place():
    pose = _pose()
    s = _teach(pose, FOUR + [(3.0, 0.0, 1.0)], random.Random(5))
    s[1]["at"] = [s[1]["at"][0] - 2.0, s[1]["at"][1], s[1]["at"][2]]                # taught against the wrong place
    for hint in (None, (2.2, 2.85, 2.8)):
        fit = sp.solve_pose(s, at_hint=hint, mount="ceiling", pan_range=RANGES[0], tilt_range=RANGES[1],
                            view=_view())
        assert fit["worst_index"] == 1 and fit["level"] in ("rough", "bad") and fit["pose"] is not None
        assert fit["worst_deg"] > fit["rms_deg"]


def test_solve_jacobian_is_exact(monkeypatch):
    """The analytic Jacobian the solve hands least_squares (and pos_m reads) matches central differences."""
    import numpy as np
    import scipy.optimize as so
    seen, real = {}, so.least_squares

    def spy(fun, x0, jac=None, args=(), **kw):
        seen.update(fun=fun, jac=jac)
        return real(fun, x0, jac=jac, args=args, **kw)
    monkeypatch.setattr(so, "least_squares", spy)
    s = _teach(_pose(), FOUR + [(3.0, 0.0, 1.0)], random.Random(1))
    for hint in (None, (2.2, 2.85, 2.8)):
        sp.solve_pose(s, at_hint=hint, mount="ceiling", pan_range=RANGES[0], tilt_range=RANGES[1], view=_view())
        f, J = seen["fun"], seen["jac"]
        rng = np.random.default_rng(0)
        for _ in range(5):
            x = np.concatenate([rng.uniform(0, 4, 3), rng.normal(0, 1.5, 3), [rng.normal(0, 0.3)]])
            for ps in (1, -1):
                num = np.stack([(f(x + e, ps) - f(x - e, ps)) / 2e-6 for e in np.eye(7) * 1e-6], axis=1)
                assert np.abs(J(x, ps) - num).max() <= 1e-6 * max(1.0, np.abs(num).max())


def test_solve_hung_and_standing_lights_either_way_round():
    rng = random.Random(9)
    for at, yaw, hung, off, ps, ts in (((4.5, 6.0, 2.8), 200.0, True, -6.0, 1, -1),
                                       ((1.0, 2.0, 0.3), 95.0, False, 4.0, -1, 1)):
        pose = _pose(at=at, yaw=yaw, hung=hung, off=off, ps=ps, ts=ts)
        pts = [(0.5, 7.5, 1.5 if hung else 2.9), (5.5, 0.5, 0.0 if hung else 2.9), (5.9, 6.5, 2.0),
               (0.0, 0.5, 1.0 if hung else 2.5), (3.0, 3.0, 0.0 if hung else 3.0)]
        fit = sp.solve_pose(_teach(pose, pts, rng, noise=0.0), at_hint=None, mount="ceiling" if hung else "floor",
                            pan_range=RANGES[0], tilt_range=RANGES[1], view=_view())
        assert fit["rms_deg"] < 0.05 and math.dist(fit["pose"]["at"], at) < 0.01, (at, fit)
        for q in ((3, 4, 1.5), (1, 1, 1)):                                          # same beams, whatever the signs
            a = sp.aim(fit["pose"], q, current=(0, 0), pan_range=RANGES[0], tilt_range=RANGES[1])
            if a["reachable"]:
                assert sp._angle_deg(sp.beam_dir(pose, a["pan"], a["tilt"]), sp._sub(q, at)) < 0.05


# -- the store ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def files(tmp_path):
    hand, added = tmp_path / "space.yaml", tmp_path / "space.added.yaml"
    hand.write_text(yaml.safe_dump({"version": 1, "room": {"size": [6, 8, 3]},
                                    "points": {"piano": {"label": "The piano", "at": [1, 2, 1]}},
                                    "appliances": {"hue.ne": {"at": [5.8, 7.8, 2.2]}}}))
    return hand, added


def test_store_merges_the_hand_file_over_the_added_one(files):
    hand, added = files
    added.write_text(yaml.safe_dump({"version": 1, "room": {"size": [5, 5, 5], "front": "west"},
                                     "points": {"piano": {"at": [9, 9, 9]}, "mirror-ball": {"at": [3, 4, 2.6]}},
                                     "appliances": {"hue.ne": {"at": [0, 0, 0], "mount": "wall",
                                                               "teach": [{"pan": 1, "tilt": 2, "at": [1, 2, 3]}]},
                                                    "dmx.mover-1": {"at": [2, 3, 2.9], "mount": "ceiling"}}}))
    st = sp.Store(hand)
    assert st.added_path == added and st.poll() and not st.poll()
    v = st.view()
    assert v["error"] is None
    assert v["room"]["size"] == [6, 8, 3] and v["room"]["front"] == "west"          # the hand file wins, per key
    assert v["points"]["piano"]["at"] == [1, 2, 1] and v["points"]["mirror-ball"]["from"] == "page"
    ne = v["appliances"]["hue.ne"]
    assert ne["at"] == [5.8, 7.8, 2.2] and ne["mount"] == "wall" and len(ne["teach"]) == 1 and not ne["editable"]
    assert v["appliances"]["dmx.mover-1"]["editable"]
    joined = " | ".join(v["problems"])
    assert "room.size is set in space.yaml too" in joined and "points.piano is in space.yaml too" in joined
    assert "appliances.hue.ne.at is set in space.yaml too" in joined


def test_store_keeps_the_last_good_on_a_bad_file(files):
    hand, added = files
    st = sp.Store(hand)
    st.poll()
    rev = st.revision
    hand.write_text("room: [unclosed\n")
    assert not st.poll()
    assert st.revision == rev and "space.yaml" in st.view()["error"] and st.view()["room"]["size"] == [6, 8, 3]
    added.write_text("- a list\n")                                                   # one bad file each
    st.poll()
    assert "space.added.yaml" in st.view()["error"] and st.revision == rev
    hand.write_text(yaml.safe_dump({"room": {"size": [7, 8, 3]}}))
    added.unlink()
    assert st.poll() and st.view()["error"] is None and st.view()["room"]["size"] == [7, 8, 3]


def test_store_problem_lines_for_values_it_cant_use(tmp_path):
    hand = tmp_path / "space.yaml"
    hand.write_text(yaml.safe_dump({
        "room": {"size": [6, -1, 3], "front": "up", "colour": "red"},
        "appliances": {"Bad Id": {"at": [1, 2, 3]}, "m1": {"at": [1, "x", 3], "mount": "sky", "teach": "no"},
                       "s1": {"at": [1, 2, 3], "plane": {"normal": [0, 0, 0], "up": [0, 0, 1]}}},
        "points": {"p": {"at": [1, 2]}, "q": {"at": [1, 2, float("nan")]}},
        "regions": {"r": {"surface": "stage", "centre": [1, 1, 0], "size": [1, 1]}},
        "surfaces": {"floor": {"origin": [0, 0, 0], "normal": [0, 0, 1], "up": [0, 1, 0]}}}))
    st = sp.Store(hand)
    st.poll()
    v, probs = st.view(), " | ".join(st.view()["problems"])
    assert v["error"] is None and v["room"]["size"] is None and v["room"]["front"] == "north"
    assert v["points"] == {} and v["regions"] == {} and list(v["surfaces"]) == ["floor"]
    assert v["appliances"]["s1"] == {"at": [1, 2, 3], "teach": [], "pose": None, "editable": False}
    assert "m1" not in v["appliances"]
    for words in ("room.size: three lengths", "room.front: 'up' isn't north", "'colour' isn't a key here",
                  "'Bad Id' isn't a name", "m1.at: three numbers", "m1.mount: 'sky' isn't one of",
                  "m1.teach: isn't a list", "s1.plane: normal and up", "points.p.at: three numbers",
                  "there's no surface 'stage'", "floor comes from the room's size"):
        assert words in probs, words


def test_store_writes_atomically_and_rereads_equal(files):
    hand, added = files
    st = sp.Store(hand)
    st.poll()
    got = st.set_point("mirror-ball", [3, 4, 2.6], device="mirror_ball")
    assert got == {"label": "Mirror ball", "at": [3.0, 4.0, 2.6], "from": "page", "device": "mirror_ball"}
    assert st.set_point("mirror-ball", [3, 4, 2.5])["device"] == "mirror_ball"     # a key left out is kept
    st.set_point("spot-1", [1, 1, 0], label="Where it lands", source="dmx.mover-1")
    a = st.set_appliance("dmx.mover-1", at=[2, 3, 2.9], mount="ceiling", kind="mover")
    assert a["at"] == [2.0, 3.0, 2.9] and a["mount"] == "ceiling" and a["editable"]
    assert st.set_appliance("dmx.mover-1", mount=None) == {"at": [2.0, 3.0, 2.9], "kind": "mover", "teach": [],
                                                           "pose": None, "editable": True}
    st.set_appliance("screen.north", at=[3, 8, 1.5], plane={"normal": [0, -2, 0], "up": [0, 0.2, 1], "size": [1, 1]})
    assert st.view()["appliances"]["screen.north"]["plane"] == {"normal": [0.0, -1.0, 0.0], "up": [0.0, 0.0, 1.0],
                                                                "size": [1.0, 1.0]}
    st.set_region("dance-floor", "floor", [3, 3, 0], [2, 2])
    view = st.set_room(front="s")
    assert view["room"]["front"] == "south" and view["room"]["size"] == [6, 8, 3]
    assert not [p.name for p in hand.parent.iterdir() if p.name.endswith(".tmp")]
    text = added.read_text()
    assert text.startswith(sp.ADDED_HEADER) and "don't edit it by hand" in text
    again = sp.Store(hand)
    again.poll()
    assert again.view() == st.view() and again.view()["problems"] == []
    st.remove_point("spot-1")
    st.remove_region("dance-floor")
    st.remove_appliance("screen.north")
    v = st.view()
    assert "spot-1" not in v["points"] and "dance-floor" not in v["regions"] and "screen.north" not in v["appliances"]
    st.remove_point("never-there")                                                   # nothing to do: no write
    with pytest.raises(sp.Refused) as e:
        st.set_room(size=[])                                                         # the hand file's size stays
    assert e.value.code == "in_hand_file" and st.view()["room"]["size"] == [6, 8, 3]
    hand.write_text("version: 1\n")
    st.set_room(size=[4, 5, 2.5])
    assert st.view()["room"]["size"] == [4.0, 5.0, 2.5] and st.view()["room"]["front"] == "south"
    assert st.set_room(size=[])["room"]["size"] is None                              # [] clears it


def test_store_teach_samples_and_pose(files):
    hand, added = files
    st = sp.Store(hand)
    st.poll()
    s = st.add_sample("dmx.mover-1", {"pan": 10, "tilt": -20, "point": "piano", "at": [1, 2, 1]})
    assert len(s) == 1 and s[0]["point"] == "piano" and s[0]["t"]
    for i in range(sp.TEACH_MAX + 2):
        s = st.add_sample("dmx.mover-1", {"pan": i, "tilt": 0, "at": [1, 2, 1], "t": f"t{i}"})
    assert len(s) == sp.TEACH_MAX and s[-1]["t"] == f"t{sp.TEACH_MAX + 1}"
    s = st.remove_sample("dmx.mover-1", 0)
    assert len(s) == sp.TEACH_MAX - 1
    with pytest.raises(sp.Refused) as e:
        st.remove_sample("dmx.mover-1", 99)
    assert e.value.code == "not_found"
    st.set_pose("dmx.mover-1", _pose())
    assert st.view()["appliances"]["dmx.mover-1"]["pose"]["pan_sign"] == -1
    assert sp.position(st.view(), "dmx.mover-1") == P_TRUE                          # unplaced: the pose's position
    st.clear_samples("dmx.mover-1")
    assert st.view()["appliances"]["dmx.mover-1"]["teach"] == []
    st.set_pose("dmx.mover-1", None)
    assert "dmx.mover-1" not in st.view()["appliances"]                             # nothing left: the entry goes
    st.add_sample("hue.ne", {"pan": 0, "tilt": 0, "at": [1, 1, 1]})                  # a hand-placed light can be taught
    assert len(st.view()["appliances"]["hue.ne"]["teach"]) == 1


def test_store_refuses_and_writes_nothing(files):
    hand, added = files
    st = sp.Store(hand)
    st.poll()
    st.set_point("mirror-ball", [3, 4, 2.6])
    before = added.read_bytes()
    bad = [lambda: st.set_point("Mirror Ball", [1, 2, 3]),
           lambda: st.set_point("x", [1, 2, float("inf")]),
           lambda: st.set_point("x", [1, 2]),
           lambda: st.set_point("x", [1, 2, 3], source="Not An Id"),
           lambda: st.set_appliance("dmx.m", at=[1, float("nan"), 2]),
           lambda: st.set_appliance("dmx.m", mount="sky"),
           lambda: st.set_appliance("dmx.m", plane={"normal": [0, 0, 1], "up": [0, 0, 1]}),
           lambda: st.set_region("r", "stage", [1, 1, 0], [1, 1]),
           lambda: st.set_region("r", "floor", [1, 1, 0], [0, 1]),
           lambda: st.set_room(size=[6, 8]),
           lambda: st.set_room(front="up"),
           lambda: st.add_sample("dmx.m", {"pan": "left", "tilt": 0, "at": [1, 1, 1]}),
           lambda: st.set_pose("dmx.m", {"at": [1, 2, 3]})]
    for fn in bad:
        with pytest.raises(sp.Refused) as e:
            fn()
        assert e.value.code == "bad_request" and e.value.detail
    for fn, code in ((lambda: st.set_point("piano", [1, 1, 1]), "in_hand_file"),
                     (lambda: st.remove_point("piano"), "in_hand_file"),
                     (lambda: st.set_appliance("hue.ne", at=[1, 1, 1]), "in_hand_file"),
                     (lambda: st.set_room(size=[5, 5, 5]), "in_hand_file")):
        with pytest.raises(sp.Refused) as e:
            fn()
        assert e.value.code == code
    assert added.read_bytes() == before
    added.write_text("points: [broken\n")
    with pytest.raises(sp.Refused) as e:
        st.set_point("other", [1, 1, 1])
    assert e.value.code == "added_unreadable" and added.read_text() == "points: [broken\n"


class _Engine:
    def __init__(self):
        self.seen = []

    def on_space(self, store):
        self.seen.append(store.revision)


def test_store_subscribers_are_weak_and_hear_changes(files):
    hand, added = files
    st = sp.Store(hand)
    eng, calls = _Engine(), []
    st.subscribe(eng.on_space)
    st.subscribe(lambda s: calls.append(s.revision))
    st.subscribe(lambda s: 1 / 0)                                                    # a failing one is only logged
    st.poll()
    assert eng.seen == [st.revision] and calls == [st.revision]
    st.set_point("mirror-ball", [3, 4, 2.6])
    assert len(eng.seen) == 2 and eng.seen[-1] == st.revision
    del eng
    gc.collect()
    st.set_point("mirror-ball", [3, 4, 2.5])
    assert len(calls) == 3 and len(st._subs) == 2                                    # the engine dropped out


def test_shared_store_is_one_per_process(monkeypatch):
    monkeypatch.setattr(sp, "_SHARED", None)
    a = sp.shared()
    assert a is sp.shared() and a.path == contract.SPACE_FILE and a.added_path == contract.SPACE_ADDED_FILE


def test_the_real_space_files_load_clean():
    st = sp.Store(contract.SPACE_FILE)
    st.poll()
    v = st.view()
    assert v["error"] is None and v["problems"] == [], v["problems"]
    assert v["room"]["front"] in sp.FRONTS
    assert set(v) == set(contract.SpaceView.__annotations__)
    assert os.path.exists(contract.SPACE_ADDED_FILE)
