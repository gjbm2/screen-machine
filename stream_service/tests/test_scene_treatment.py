"""The art director's per-song treatment on room.direction (screen-machine-63, 29 Oct): scene_favour {tags, share},
scene_avoid {tags, factor} and dwell_s [min, max], validated, passed through the music's proposal like ``art``, and
steering the scene director's picks and dwell (director.py, music_direction.py; no GPU)."""
from __future__ import annotations

import pytest

from stream_service import music_direction as M
from stream_service.director import (AUTO_DEFAULTS, AUTO_MUSIC_DEFAULTS, Director, ParamError, dwell_window,
                                     favour_share, steer, validate_auto, validate_direction, why_extra)
from stream_service.sessions import lenient_direction
from stream_service.tags import tag_words

from .test_music_direction import room_dir
from .test_sessions import client  # noqa: F401 (fixture)


def test_validate_treatment_shapes():
    d = validate_direction({})
    assert d["scene_favour"] is None and d["scene_avoid"] is None and d["dwell_s"] is None
    d = validate_direction({"scene_favour": {"tags": [" Family:Cosmic", "family:cosmic", "modes:disco", ""],
                                             "share": 1.5},
                            "scene_avoid": {"tags": ["piano"]}, "dwell_s": [0.5, 2000]})
    assert d["scene_favour"] == {"tags": ["family:cosmic", "modes:disco"], "share": 0.9}   # trimmed, lower, max 0.9
    assert d["scene_avoid"] == {"tags": ["piano"], "factor": 0.3}                         # the default factor
    assert d["dwell_s"] == [1.0, 900.0]                                                    # clamped into 1..900
    assert validate_direction({"scene_avoid": {"tags": ["x"], "factor": 0}})["scene_avoid"]["factor"] == 0.05
    assert validate_direction({"scene_favour": {"tags": ["x"], "share": -1}})["scene_favour"]["share"] == 0.0
    many = validate_direction({"scene_favour": {"tags": [f"t{i}" for i in range(20)]}})["scene_favour"]
    assert len(many["tags"]) == 12
    assert validate_direction({"scene_favour": {"tags": []}})["scene_favour"] is None      # nothing to favour
    assert validate_direction({"dwell_s": [20, 20]})["dwell_s"] == [20.0, 20.0]
    for bad in ({"scene_favour": ["x"]}, {"scene_favour": {"tags": "x"}}, {"scene_favour": {"tags": [1]}},
                {"scene_favour": {"tags": ["x"], "weight": 1}}, {"scene_favour": {"tags": ["x"], "share": "a"}},
                {"scene_avoid": {"tags": ["x"], "share": 0.5}}, {"dwell_s": [30, 10]}, {"dwell_s": [10]},
                {"dwell_s": "long"}, {"dwell_s": [True, 10]}):
        with pytest.raises(ParamError):
            validate_direction(bad)
    # room.direction: a bad treatment field drops alone, the rest still applies
    d, dropped = lenient_direction({"energy": 0.8, "dwell_s": [30, 10], "scene_avoid": {"tags": ["piano"]}}, {})
    assert dropped == ["dwell_s"] and d["dwell_s"] is None and d["scene_avoid"]["tags"] == ["piano"]
    # its own output validates again (the room's PATCH look round-trips the direction)
    d = validate_direction({"scene_favour": {"tags": ["cosmic"], "share": 0.5}, "dwell_s": [20, 40]})
    assert validate_direction(d) == d


def test_dwell_window_takes_the_shows_unless_the_auto_block_sets_one():
    d = validate_direction({"dwell_s": [20, 45], "change_rate": 1.0})
    assert dwell_window(AUTO_DEFAULTS, d) == (20.0, 45.0, "show")
    assert dwell_window(validate_auto({"dwell_s": [6, 12]}), d) == (6.0, 12.0, "dwell_s")
    lo, hi, src = dwell_window(AUTO_DEFAULTS, validate_direction({"change_rate": 1.0}))
    assert src == "change_rate" and (lo, hi) == pytest.approx((15.0, 30.0))
    dr = Director(dict(AUTO_DEFAULTS), d)
    assert dr.next_eligible(0.0)["dwell_from"] == "show"


def test_favour_share_and_steer():
    w = favour_share([0.1, 1.0, 3.0], [True, False, False], 0.5)
    assert w[0] / sum(w) == pytest.approx(0.5) and w[1:] == [1.0, 3.0]
    assert favour_share([5.0, 1.0], [True, False], 0.5) == [5.0, 1.0]          # never lowered
    have = [tag_words(t) for t in ({"family": "cosmic", "moods": ["calm"]}, {"family": "fractal"},
                                   {"moods": ["cosmic"]}, {"technique": ["piano"]})]
    w, notes = steer([1.0, 1.0, 1.0, 1.0], have, {"tags": ["family:cosmic"], "share": 0.5}, None)
    assert w[0] / sum(w) == pytest.approx(0.5) and w[2] == 1.0                  # facet:word: only that facet
    assert notes == ["favour family:cosmic 0.5"]
    w, notes = steer([1.0, 1.0, 1.0, 1.0], have, {"tags": ["cosmic"], "share": 0.8}, None)
    assert (w[0] + w[2]) / sum(w) == pytest.approx(0.8)                           # a word: any facet
    w, notes = steer([1.0, 1.0, 1.0, 1.0], have, None, {"tags": ["piano"], "factor": 0.2})
    assert w == [1.0, 1.0, 1.0, 0.2] and notes == ["avoid piano ×0.2"]           # soft: never removed
    assert steer([1.0, 2.0], have[:2], {"tags": ["nope"], "share": 0.5}, {"tags": ["nope"], "factor": 0.1}) \
        == ([1.0, 2.0], [])                                                       # no match: ignored
    assert why_extra(["favour cosmic 0.5"], 1.4) == "; favour cosmic 0.5; taste ×1.4"
    assert why_extra([], 1.02) == ""


def _scenes():
    out = {}
    for i in range(30):
        fam = "cosmic" if i < 3 else "fractal"
        out[f"s{i}"] = {"source": f"p{i}", "tags": {"energy": [0, 1], "family": fam, "weight": 0.8,
                                                    "technique": ["piano"] if i >= 25 else ["raymarch"]}}
    return out


def test_pick_scene_honours_favour_avoid_and_taste():
    scenes = _scenes()
    d = Director(validate_auto({"seed": 11, "avoid_repeat": 0}), validate_direction({}))
    base = sum(d.pick_scene(scenes, None, 0.0)[0] in ("s0", "s1", "s2") for _ in range(2000)) / 2000
    assert base < 0.2                                                            # 3 of 30: about 10 %
    d.direction = validate_direction({"scene_favour": {"tags": ["family:cosmic"], "share": 0.6}})
    picks = [d.pick_scene(scenes, None, 0.0) for _ in range(2000)]
    share = sum(p[0] in ("s0", "s1", "s2") for p in picks) / 2000
    assert 0.55 < share < 0.65 and "favour family:cosmic 0.6" in picks[0][1]
    d.direction = validate_direction({"scene_avoid": {"tags": ["piano"], "factor": 0.05}})
    n = sum(d.pick_scene(scenes, None, 0.0)[0] in {f"s{i}" for i in range(25, 30)} for _ in range(3000))
    assert 0 < n < 3000 * 0.05                                                   # rarer, never gone (5 of 30 = 17 %)
    d.direction = validate_direction({})
    d.taste = lambda plugin, direction: 2.5 if plugin == "p7" else 1.0
    got = [d.pick_scene(scenes, None, 0.0) for _ in range(3000)]
    assert sum(g[0] == "s7" for g in got) > 2 * 3000 / 30
    assert next(g[1] for g in got if g[0] == "s7").endswith("; taste ×2.5)")
    d.taste = lambda plugin, direction: 1 / 0                                    # a broken store: as before
    assert d.pick_scene(scenes, None, 0.0) is not None


def test_the_treatment_rides_the_music_proposal_like_art():
    md = M.MusicDirection(dict(AUTO_MUSIC_DEFAULTS))
    fav = {"tags": ["family:cosmic"], "share": 0.5}

    def rd(**kw):
        r = room_dir()
        r["direction"].update(kw)
        return r
    for t in range(40):
        md.update(t / 10, None, False, (), True, room=rd(scene_favour=fav, dwell_s=[20.0, 40.0]))
    panel = validate_direction({})
    eff = M.effective_direction(panel, md.proposal, md.m)
    assert eff["scene_favour"] == fav and eff["dwell_s"] == [20.0, 40.0] and eff["scene_avoid"] is None
    assert {"scene_favour", "dwell_s"} <= set(M.music_fields(panel, md.proposal, md.m))
    assert M.pick_key(eff) != M.pick_key(dict(eff, scene_favour=None))          # a new favour re-picks the next one
    # a change of treatment publishes; the panel's own wins; none (an older show_service): as before
    assert md.update(4.1, None, False, (), True, room=rd(scene_favour=fav, dwell_s=[60.0, 90.0])) == "music"
    own = validate_direction({"dwell_s": [5, 9]})
    assert M.effective_direction(own, md.proposal, md.m)["dwell_s"] == [5.0, 9.0]
    assert md.update(4.2, None, False, (), True, room=rd()) == "music"
    assert M.effective_direction(panel, md.proposal, md.m)["scene_favour"] is None
    assert M.effective_direction(panel, md.proposal, 0.25).get("dwell_s") is None   # only while m >= 0.5


async def test_room_direction_reaches_the_sessions_director(client):  # noqa: F811 (the fixture)
    import time

    from .test_cue import AUTO, live
    sid, s, eng = await live(client, auto={k: v for k, v in AUTO.items() if k != "dwell_s"})
    mgr = s.mgr
    data = {"energy": 0.6, "moods": ["bright"], "scene_favour": {"tags": ["family:cosmic"], "share": 0.5},
            "scene_avoid": {"tags": ["piano"], "factor": 0.2}, "dwell_s": [25, 50]}
    t0 = time.monotonic()
    for i in range(50):
        mgr.on_room_message({"type": "room.direction", "data": data})
        mgr.room_direction["t"] = t0 + i / 10
        s.tick(t0 + i / 10)
    d = s.director.direction
    assert d["scene_favour"] == {"tags": ["family:cosmic"], "share": 0.5} and d["scene_avoid"]["factor"] == 0.2
    assert s.director.window() == (25.0, 50.0, "show")
    r = await client.get(f"/api/sessions/{sid}")
    st = await r.json()
    assert st["auto"]["dwell_from"] == "show" and st["direction_effective"]["dwell_s"] == [25.0, 50.0]
    assert "scene_favour" in st["music_fields"]

