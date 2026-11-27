"""Keep-out regions (space.yaml keep_out; owner, 27 Nov): parsing, the beam and burst tests, a head's aim stepping
aside, and a fixed laser's tilt checked against them."""
import math

from show_service import space as sp

BALL = ([2.5, 5.0, 3.5], [0.6, 0.6, 0.6])


def test_a_keep_out_region_parses_and_a_bad_one_says_why():
    probs: list[str] = []
    out = sp.parse_doc({"version": 1, "keep_out": {
        "ball": {"label": "Mirror ball", "centre": [2.5, 5.0, 3.5], "size": [0.6, 0.6, 0.6], "groups": ["lasers"]},
        "bad": {"centre": [1, 2], "size": [1, 1, 1]}}}, "space.yaml", probs)
    assert out["keep_out"] == {"ball": {"centre": [2.5, 5.0, 3.5], "size": [0.6, 0.6, 0.6], "groups": ["lasers"],
                                        "label": "Mirror ball"}}
    assert len(probs) == 1 and "bad" in probs[0]


def test_a_beam_through_the_box_and_one_beside_it():
    assert sp.ray_crosses_box([0, 5, 3.5], [1, 0, 0], *BALL)
    assert not sp.ray_crosses_box([0, 5, 3.5], [1, 0.2, 0], *BALL)
    assert not sp.ray_crosses_box([0, 5, 3.5], [-1, 0, 0], *BALL)          # behind the light


def test_a_burst_aimed_up_misses_a_ball_out_in_the_room_and_one_leant_at_it_hits():
    at, mid = [4.6, 9.6, 2.5], [2.5, 5.0, 2.0]
    assert not sp.cone_crosses_box(at, sp.lean_axis(at, mid, -1.5), 46.5, *BALL)
    assert sp.cone_crosses_box(at, sp.lean_axis(at, mid, 60.0), 46.5, *BALL)


def test_a_heads_aim_steps_aside_from_a_region_for_its_group():
    view = {"keep_out": {"ball": {"centre": BALL[0], "size": BALL[1], "groups": ["movers"]}}}
    assert sp.keep_boxes(view, "lasers") == () and len(sp.keep_boxes(view, "movers")) == 1
    o = [4.3, 9.7, 2.7]
    q, moved = sp.clear_target(o, BALL[0], sp.keep_boxes(view, "movers"))
    assert moved and not sp.ray_crosses_box(o, [q[i] - o[i] for i in range(3)], BALL[0], [x + 0.2 for x in BALL[1]])
    assert math.dist(q, BALL[0]) < 2.0                                      # beside it, not across the room
    q2, moved2 = sp.clear_target(o, [1.0, 9.0, 0.0], sp.keep_boxes(view, "movers"))
    assert not moved2 and q2 == (1.0, 9.0, 0.0)


def test_a_lasers_tilt_is_checked_and_the_clear_tilt_found():
    view = {"room": {"centre": [2.5, 5.0, 2.0]},
            "keep_out": {"ball": {"centre": [4.0, 8.3, 3.6], "size": [0.5, 0.5, 0.5], "groups": []}}}
    got = sp.laser_checks(view, [{"id": "l", "at": [4.6, 9.6, 2.5], "spread_deg": 93.0, "lean_deg": None,
                                  "group": "lasers"}])["l"]
    assert got["lean_deg"] == -1.5 and got["crosses"] == ["ball"]           # 45 degrees forward reaches it
    assert got["clear_lean_deg"] is not None and got["clear_lean_deg"] < -1.5
    view["keep_out"]["ball"]["groups"] = ["movers"]                          # not for lasers: nothing to cross
    assert sp.laser_checks(view, [{"id": "l", "at": [4.6, 9.6, 2.5], "spread_deg": 93.0, "lean_deg": None,
                                   "group": "lasers"}])["l"]["crosses"] == []


# -- bars: a light bar as one thing, its parts placed from its layout ---------------------------------------------------
LAYOUT = {"gigbar": {"parts": {"par1": {"along": -0.5, "lean_deg": 68, "turn_deg": -27},
                                "spot1": {"along": -0.33, "up": 0.18, "head": True}, "laser": {"along": 0.0}}}}


def _bars_view(extra=None):
    hand = {"version": 1, "room": {"size": [5.0, 10.0, 4.0], "front": "east"}, "layouts": LAYOUT,
            "bars": {"a": {"at": [4.6, 9.6, 2.5], "facing_deg": -114.5, "layout": "gigbar"}}, **(extra or {})}
    return sp.build_view(hand, None)


def test_a_bars_parts_sit_where_its_layout_says_and_its_heads_face_its_front():
    v = _bars_view()
    assert v["problems"] == []
    ap = v["appliances"]
    assert [round(x, 3) for x in ap["a.par1"]["at"]] == [4.145, 9.807, 2.5]          # half a metre to the bar's left
    assert [round(x, 3) for x in ap["a.spot1"]["at"]] == [4.3, 9.737, 2.68]          # a head, 18 cm above the bar
    pose = ap["a.spot1"]["pose"]
    assert round(pose["rot"][2], 4) == round(math.radians(-114.5), 4) and ap["a.spot1"]["mount"] == "floor"
    b = v["bars"]["a"]
    assert [round(x, 3) for x in b["front"]] == [-0.415, -0.91, 0.0] and b["parts"]["par1"]["lean_deg"] == 68


def test_a_part_placed_by_hand_keeps_its_own_place_and_a_bar_without_its_layout_says_so():
    v = _bars_view({"appliances": {"a.laser": {"at": [1.0, 1.0, 1.0]}}})
    assert v["appliances"]["a.laser"]["at"] == [1.0, 1.0, 1.0] and v["bars"]["a"]["parts"]["laser"]["own_place"]
    hand = {"version": 1, "bars": {"x": {"at": [1, 1, 1], "facing_deg": 0, "layout": "nope"}}}
    v2 = sp.build_view(hand, None)
    assert v2["bars"] == {} and any("layout" in p for p in v2["problems"])


def test_which_way_a_fixed_light_points_from_its_tilt_and_turn():
    front = (0.0, 1.0, 0.0)                                                          # the bar faces north
    assert [round(x, 3) for x in sp.aim_dir(front, 0.0)] == [0.0, 0.0, 1.0]          # straight up
    assert [round(x, 3) for x in sp.aim_dir(front, 90.0)] == [0.0, 1.0, 0.0]         # level, ahead
    assert [round(x, 3) for x in sp.aim_dir(front, 90.0, 90.0)] == [-1.0, 0.0, 0.0]  # level, to the bar's right (west)
