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


# -- the stars steer the draw (light_scenes.LightRatings) ---------------------------------------------------------------
class _Rng:
    def __init__(self, x: float = 0.5) -> None:
        self.x = x

    def random(self) -> float:
        return self.x


def _rated(tmp_path, data: dict, table: dict | None = None) -> ls.LightRatings:
    (tmp_path / "r.json").write_text(json.dumps(data))
    if table is not None:
        (tmp_path / "w.json").write_text(json.dumps({"stars": table}))
    return ls.LightRatings(tmp_path / "r.json", tmp_path / "w.json")


def test_stars_weigh_against_an_unrated_item_by_the_screens_table(tmp_path):
    r = _rated(tmp_path, {"light/a": {"stars": 5}, "light/b": {"stars": 1}, "light/c": {"reject": True, "stars": 4},
                          "light_effect/e": {"stars": 3}, "light/bad": {"stars": 9}, "junk": 3})
    assert r.weight("scene", "a") == pytest.approx(8.0 / 0.35)
    assert r.weight("scene", "b") == pytest.approx(0.05 / 0.35)
    assert r.weight("scene", "c") == 0.0 and r.stars("scene", "c") == -1          # a reject, whatever its stars
    assert r.weight("effect", "e") == pytest.approx(1.5 / 0.35)
    assert r.weight("scene", "nobody-rated-this") == 1.0 and r.weight("scene", "bad") == 1.0
    assert r.weight("transition", "a") == 1.0                                      # another kind's stars
    assert r.table()["3"] == pytest.approx(4.29, abs=0.01)
    own = _rated(tmp_path, {"light/a": {"stars": 5}}, {"unrated": 1.0, "5": 2.0})  # the file's own table wins
    assert own.weight("scene", "a") == 2.0


def test_no_ratings_file_and_a_half_written_one_change_nothing(tmp_path):
    r = ls.LightRatings(tmp_path / "none.json", tmp_path / "none-w.json")
    assert r.weight("scene", "a") == 1.0
    (tmp_path / "r.json").write_text("{ not json")
    assert ls.LightRatings(tmp_path / "r.json", tmp_path / "w.json").weight("scene", "a") == 1.0


def test_the_draws_take_the_stars_and_a_plain_catalogue_takes_none(tmp_path):
    group = _Dir()._groups["movers"]
    plain = ls.Catalogue()
    plain.poll()
    _n, _why, base = plain.pick_for(group, None, None, _Rng())
    names = sorted(base)
    fav, out = names[0], names[1]
    c = ls.Catalogue(ratings=_rated(tmp_path, {f"light/{fav}": {"stars": 5}, f"light/{out}": {"reject": True},
                                               "light_effect/drop-flash": {"stars": 5},
                                               "light_transition/cut": {"reject": True}}))
    c.poll()
    _n, _why, ws = c.pick_for(group, None, None, _Rng())
    assert ws[fav] == pytest.approx(base[fav] * 8.0 / 0.35) and ws[out] == 0.0
    assert all(ws[n] == pytest.approx(base[n]) for n in names[2:])                # the unrated ones: as before
    got = {c.pick_for(group, None, None, _Rng(i / 50.0))[0] for i in range(50)}
    assert out not in got                                                          # a reject is never drawn...
    name, why, _ = c.pick_for(group, None, None, _Rng(0.5), prefer={fav: 1e9})
    assert name == fav and "your 5 stars; " in why
    only = {**group, "scenes_for": ["laser"], "caps": ["colour", "macro", "shutter"]}
    rest = [n for n in c.pool_for(only, rest=True)]
    r2 = ls.Catalogue(ratings=_rated(tmp_path, {f"light/{n}": {"reject": True} for n in rest}))
    r2.poll()
    assert r2.pick_for(only, None, None, _Rng(), rest=True)[0] in rest             # ...unless nothing else fits
    for i in range(40):                                                            # effects and transitions too
        assert c.pick_transition(("cut", "dark_move"), None, None, None, _Rng(i / 40.0)) != "cut"
    assert c.rated("effect", "drop-flash") == pytest.approx(8.0 / 0.35) and plain.rated("effect", "drop-flash") == 1.0
    assert ls.shared().ratings is not None                                         # the running show reads the stars
