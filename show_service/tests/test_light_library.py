"""light_library: the lighting half of the Scene library (the real config/live/light_*.yaml as Library items, the
words for what each does, the ratings store). Nothing is read from a running service; ratings go to tmp_path."""

from __future__ import annotations

import json

import pytest

from show_service import light_library as ll
from show_service import light_scenes as ls
from show_service import roomlight as rl

SPOT = "ofl/chauvet-dj/gigbar-move-ils-spot.json"


class _Dir:
    """A director's worth of what the index reads: two groups, a rig, the photometry, a room light view."""

    def __init__(self) -> None:
        self._groups = {"movers": {"label": "Moving lights", "members": ["s0", "s1"], "caps": ["intensity", "colour", "pan", "tilt"],
                                   "scenes_for": []},
                        "lasers": {"label": "Lasers", "members": ["z0"], "caps": ["colour", "macro", "shutter"],
                                   "scenes_for": ["laser"]},
                        ls.OTHER_GROUP: {"label": "Other lights", "members": ["h0"], "caps": ["intensity"], "scenes_for": []}}
        self.lights_cfg = {"groups": {"movers": {}, "lasers": {"scenes_for": ["laser"], "reads_under_lux": 3.0}},
                           "lineups": {"a": {"label": "A", "plays": ["movers", "lasers"], "features": ["lasers"]}}}
        self._photo = rl.load()
        self._rig_seen = {"types": {"spot": {"file": SPOT}, "laser": {"file": "ofl/chauvet-dj/gigbar-move-ils-laser.json"}},
                          "fixtures": {"s0": {"type": "spot"}, "s1": {"type": "spot"}, "z0": {"type": "laser"}}}
        self._room_light_view = {"lux": 5.0, "other_lux": 1.0}

    def _space_now(self):
        return None, {"room": {"size": [5.0, 10.0, 4.0]}}


def lib(tmp_path, director=None) -> ll.LightLibrary:
    ls.shared().poll()
    return ll.LightLibrary(lambda: director, shots_dir=tmp_path / "shots", ratings_file=tmp_path / "ratings.json")


def test_every_scene_effect_and_transition_is_an_item_with_words_and_its_groups(tmp_path):
    (tmp_path / "shots" / "scene").mkdir(parents=True)
    (tmp_path / "shots" / "scene" / "movers-ceiling-circles.jpg").write_bytes(b"jpg")
    idx = lib(tmp_path, _Dir()).index()
    by = {i["key"]: i for i in idx["items"]}
    cat = ls.shared()
    gests = {ls.gesture_name(n) for n in cat.transitions}
    assert len(by) == len(cat.scenes) + len(cat.transitions) + len([n for n in cat.effects if n not in gests])
    c = by["light/movers-ceiling-circles"]
    assert c["kind"] == "light" and c["label"] == "Circles on the ceiling"
    assert c["description"].startswith("The heads circle on the ceiling")
    assert c["light"]["lands"] == ["ceiling"] and 0.5 < c["light"]["share"] < 0.9
    assert [g["id"] for g in c["light"]["groups"]] == ["movers"] and c["light"]["groups"][0]["lux"] > 1
    assert c["thumb"].startswith("/api/v1/lights/thumb/scene/movers-ceiling-circles.jpg?v=")
    assert by["light/laser-turning"]["facets"]["plays_on"] == ["Lasers"]          # a part's scene: that part only
    assert by["light/laser-turning"]["thumb"] is None                              # no picture taken yet
    assert by["light/rest-dark"]["light"]["rest"] and by["light/rest-dark"]["light"]["share"] == 0.0
    assert by["light_effect/laser-burst"]["facets"]["when"][0] == "the drop"
    assert "Fires when" in by["light_effect/laser-burst"]["description"]
    assert by["light_transition/fly-up"]["description"] == "One scene fades into the next; across it the heads lift."
    assert by["light_transition/cut"]["light"]["transition_kind"] == "cut"
    assert ls.OTHER_GROUP not in idx["groups"] and idx["groups"]["lasers"]["reads_under_lux"] == 3.0
    assert idx["lineups"]["a"]["features"] == ["lasers"]
    json.dumps(idx)                                                                # it is plain JSON


def test_with_no_director_yet_everything_is_still_listed(tmp_path):
    idx = lib(tmp_path, None).index()
    assert len(idx["items"]) > 60 and idx["groups"] == {}
    assert all(i["light"]["groups"] == [] for i in idx["items"])


def test_what_a_scene_does_in_words():
    assert ll.does({"layers": []}) == ["steady light"]
    assert ll.does({"layers": [], "appliances": {"power": "on"}})[0].startswith("works the room's own effects")
    assert ll.does({"layers": [{"fx": "dim", "depth": 1.0}]}) == ["dark"]
    assert ll.does({"layers": [{"fx": "dim", "depth": 0.82}]}) == ["a low glow"]
    assert ll.does({"layers": [{"kind": "position", "mode": "sweep", "on": "floor"}, {"kind": "beam"},
                               {"fx": "swell", "phase": "beat", "depth": 0.5}],
                    "hits": {"styles": [], "drop": {"depth": 1.0}}}) == \
        ["the heads sweep on the floor", "pulses on the beat", "a flash on the drop"]


def test_ratings_are_kept_and_checked(tmp_path):
    L = lib(tmp_path)
    assert L.ratings() == {}
    assert L.rate({"key": "light/beat-pulse", "stars": 4})["entry"]["stars"] == 4
    assert L.rate({"key": "light/beat-pulse", "reject": True, "tags": ["party"]})["entry"] == \
        {**L.ratings()["light/beat-pulse"], "stars": 4, "reject": True, "tags": ["party"]}
    assert json.loads((tmp_path / "ratings.json").read_text())["light/beat-pulse"]["stars"] == 4
    assert ll.LightLibrary(lambda: None, ratings_file=tmp_path / "ratings.json").ratings()["light/beat-pulse"]["reject"]
    assert L.rate({"key": "light/beat-pulse", "stars": 0, "reject": False, "tags": []})["entry"] is None
    for bad in ({"key": "scene/x", "stars": 3}, {"key": "light/x", "stars": 9}, {"key": "light/x", "colour": 1},
                {"key": "light/x", "tags": ["a" * 40]}, "nope"):
        with pytest.raises(ValueError):
            L.rate(bad)
