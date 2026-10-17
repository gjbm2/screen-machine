"""The owner's Library ratings steer the auto-directors: stars weight the picks, a reject is never auto-picked."""
import json

from stream_service.director import DIRECTION_DEFAULTS, Director, validate_auto
from stream_service.fx_director import FxDirector
from stream_service.ratings import STAR_WEIGHT, Ratings


def _ratings(tmp_path, entries):
    p = tmp_path / "ratings.json"
    p.write_text(json.dumps({"ratings": entries}))
    return Ratings(p)


def test_star_weights(tmp_path):
    r = _ratings(tmp_path, {"scene/good": {"stars": 5}, "scene/meh": {"stars": 1}, "scene/no": {"stars": 4, "reject": True},
                            "transition/wipe": {"stars": 3}, "effect/grain": {"stars": 2}})
    assert r.weight("source", "good") == STAR_WEIGHT[5] and r.weight("scene", "meh") == STAR_WEIGHT[1]
    assert r.weight("source", "no") == 0.0 and r.weight("source", "unrated") == 1.0 and r.weight("source", None) == 1.0
    assert r.weight("transition", "wipe") == STAR_WEIGHT[3] and r.weight("effect", "grain") == STAR_WEIGHT[2]
    r.set({"key": "scene/meh", "stars": 4})                      # a new rating takes effect on the next pick
    assert r.weight("source", "meh") == STAR_WEIGHT[4]


def test_scene_picks_follow_the_stars(tmp_path):
    r = _ratings(tmp_path, {"scene/a": {"stars": 5}, "scene/c": {"stars": 1}})
    scenes = {n: {"source": n, "tags": {"energy": [0, 1]}} for n in ("a", "b", "c")}
    d = Director(validate_auto({"seed": 7}), dict(DIRECTION_DEFAULTS))
    d.rate = r.weight
    counts = {n: 0 for n in scenes}
    for _ in range(3000):
        got = d.pick_scene(scenes, None, 0.0)
        counts[got[0]] += 1
    assert counts["a"] > 4 * counts["b"] > 0 and counts["c"] < counts["b"] / 5


def test_fx_picks_follow_the_stars(tmp_path):
    r = _ratings(tmp_path, {"effect/x": {"stars": 5}})
    fx = FxDirector(seed=3)
    fx.rate = r.weight
    presets = {"px": {"effect": "x", "tags": {}}, "py": {"effect": "y", "tags": {}}}
    picks = [fx._pick(["px", "py"], presets)[0] for _ in range(2000)]
    assert picks.count("px") > 4 * picks.count("py") > 0


def test_a_reject_leaves_the_vetted_pool(tmp_path):
    import pathlib

    from stream_service.server import MANAGER, make_app
    app = make_app(config_dir=pathlib.Path(__file__).resolve().parents[2] / "config" / "live")
    mgr = app[MANAGER]
    assert mgr.auto_ok("source", "fractal_zoom")
    mgr.ratings = _ratings(tmp_path, {"scene/fractal_zoom": {"reject": True}})
    assert not mgr.auto_ok("source", "fractal_zoom") and mgr.rating_weight("source", "fractal_zoom") == 0.0


def test_the_star_table_is_config_and_hot_reloads(tmp_path):
    r = _ratings(tmp_path, {"scene/good": {"stars": 4}, "scene/tagged": {"tags": ["x"]}})
    w = tmp_path / "rating_weights.json"
    w.write_text(json.dumps({"stars": {"unrated": 0.35, "4": 5.0, "2": "bad", "9": 3.0}}))
    r._checked = None                                          # past the 1 s mtime throttle
    assert r.weight("source", "nobody") == 0.35 and r.weight("source", "tagged") == 0.35   # unrated, tags only
    assert r.weight("source", "good") == 5.0 and r.star_weight[2] == STAR_WEIGHT[2]       # a bad value: default
    assert 9 not in r.star_weight
    w.write_text(json.dumps({"stars": {"unrated": 0.5}}))
    import os
    os.utime(w, (w.stat().st_atime, w.stat().st_mtime + 5))
    r._checked = None
    assert r.weight("source", "nobody") == 0.5 and r.weight("source", "good") == STAR_WEIGHT[4]
    w.unlink()
    r._checked = None
    assert r.weight("source", "nobody") == STAR_WEIGHT[0]     # no file: the code's table
