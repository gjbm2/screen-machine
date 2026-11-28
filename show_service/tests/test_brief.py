"""The song brief, its idiom and its phrasing: pure parts (brief.py, idiom.py, phrasing.py)."""
from __future__ import annotations

import json

import pytest

from show_service import brief, idiom, phrasing

SCENES = {
    "pools": {"layers": [{"kind": "position", "mode": "drift", "phase": "phrase"},
                         {"fx": "sine", "phase": "bar", "period_bars": [8, 4], "depth": 0.25}]},
    "slow-cross": {"layers": [{"kind": "position", "mode": "sweep", "phase": "bar", "period_bars": [4, 2]}]},
    "sweep": {"layers": [{"kind": "position", "mode": "sweep", "phase": "bar", "period_bars": [2, 1]},
                         {"fx": "swell", "phase": "beat", "depth": 0.5}]},
    "ballyhoo": {"layers": [{"kind": "position", "mode": "ballyhoo", "phase": "bar", "period_bars": [2, 1]},
                            {"fx": "square", "phase": "beat", "depth": 0.5}]},
    "bar-swell": {"layers": [{"fx": "swell", "phase": "bar", "period_bars": 1, "depth": 0.4}]},
    "rest": {"layers": [{"fx": "square", "phase": "beat", "depth": 1.0}], "tags": {"rest": True}},
}


def test_a_scenes_traits_are_read_from_its_layers():
    t = {n: idiom.traits(s) for n, s in SCENES.items()}
    assert t["pools"] == {"motion": "still", "pulse": "soft"}
    assert t["slow-cross"] == {"motion": "slow", "pulse": "none"}
    assert t["sweep"] == {"motion": "moving", "pulse": "beat"}
    assert t["ballyhoo"] == {"motion": "fast", "pulse": "hard"}
    assert t["bar-swell"] == {"motion": "none", "pulse": "bar"}
    assert idiom.traits({"layers": [], "tags": {"traits": {"motion": "fast"}}})["motion"] == "fast"


def test_an_idiom_is_a_limit_not_a_weight():
    cat = idiom.load()
    assert not cat["problems"] and {"hush", "ballad", "anthem", "disco", "dance"} <= set(cat["idioms"])
    hush, anthem, dance = (cat["idioms"][k] for k in ("hush", "anthem", "dance"))
    assert [n for n, s in SCENES.items() if idiom.allows(hush, s)] == ["pools", "rest"]   # a rest always is
    assert idiom.allows(anthem, SCENES["sweep"]) and not idiom.allows(anthem, SCENES["ballyhoo"])
    assert all(idiom.allows(dance, s) for s in SCENES.values()) and idiom.allows(None, SCENES["ballyhoo"])
    # which lights play: a hushed song's strobe zones never, an anthem's from the chorus, its lasers at the peak
    assert not idiom.plays(hush, "flash", "peak") and idiom.plays(hush, "movers", "verse")
    assert not idiom.plays(anthem, "flash", "verse") and idiom.plays(anthem, "flash", "chorus")
    assert not idiom.plays(anthem, "laser", "chorus") and idiom.plays(anthem, "laser", "peak")
    assert idiom.plays(anthem, "laser", None) and not idiom.plays(hush, "laser", None)   # not known: only "never"
    assert idiom.transition_ok(hush, "glide") and not idiom.transition_ok(hush, "strobe-snap")
    assert idiom.transition_ok({"transitions": None}, "anything") and idiom.transition_ok(hush, None)


def test_an_idiom_says_what_its_looks_are_like():
    cat = idiom.load()["idioms"]
    hush, anthem = cat["hush"], cat["anthem"]
    still, sweep = idiom.traits(SCENES["pools"]), idiom.traits(SCENES["sweep"])
    assert idiom.closeness(anthem, sweep, "chorus") == 1.0                    # the look an anthem's chorus wants
    assert idiom.closeness(anthem, still, "chorus") < 0.1 < idiom.closeness(anthem, still, "verse")
    assert idiom.closeness(hush, still, "verse") > idiom.closeness(hush, sweep, "verse")
    assert idiom.closeness(anthem, sweep, None) == idiom.closeness(anthem, sweep, "verse")   # not known: a verse
    assert idiom.closeness(None, sweep, "peak") == 1.0 and idiom.closeness({"looks": {}}, sweep, "peak") == 1.0
    for name, e in cat.items():                                               # every idiom says all three, in order
        assert set(e["looks"]) == set(idiom.LEVELS), name
        m = [idiom.MOTION.index(e["looks"][lv][0]) for lv in idiom.LEVELS]
        assert m == sorted(m) and m[-1] <= idiom.MOTION.index(e["motion_max"]), name


def test_the_real_scenes_leave_every_idiom_something_to_show():
    import yaml
    scenes = yaml.safe_load((idiom.PATH.parent / "light_scenes.yaml").read_text())["scenes"]
    movers = {n: s for n, s in scenes.items() if n.startswith("movers-")}
    for name, e in idiom.load()["idioms"].items():
        ok = [n for n, s in movers.items() if idiom.allows(e, s)]
        assert len(ok) >= 3, (name, ok)
    hush = idiom.load()["idioms"]["hush"]
    assert not idiom.allows(hush, scenes["movers-ballyhoo"]) and not idiom.allows(hush, scenes["peak-strobe"])
    assert idiom.allows(hush, scenes["movers-still-pools"])


def test_an_answer_is_checked_against_its_options():
    opts = brief.options(idiom.load()["idioms"], {"gold": "Gold.", "ocean": "Blue."}, ["gold", "ocean", "art"],
                         ["warm", "calm"], ["pop"])
    assert set(opts["palettes"]) == {"gold", "ocean"} and "hush" in opts["idioms"]
    f = brief.facts({"title": "S", "artist": "A"}, [(10.0, "one"), (40.0, "two")], None, {"bpm": 100.0}, 200.0)
    data = {"tone": "t", "idiom": "ballad", "moods": ["warm", "loud"], "palette": "neon", "ending": "boom", "why": "w",
            "sections": [{"start_s": 9.8, "kind": "verse", "size": 0.3, "arrive": "none"},
                         {"start_s": 41.0, "kind": "prechorus", "size": 0.5, "arrive": "swell"},
                         {"start_s": 43.0, "kind": "chorus", "size": 0.9, "arrive": "bump", "final": True},   # too short
                         {"start_s": 90.0, "kind": "chorus", "size": 2.0, "arrive": "bump", "final": True},
                         {"start_s": 60.0, "kind": "solo", "size": 0.5, "arrive": "none"},                   # not a kind
                         {"start_s": 150.0, "kind": "outro", "size": 0.2, "arrive": "wobble"},
                         {"start_s": 199.5, "kind": "outro", "size": 0.2, "arrive": "none"}]}                # past the end
    probs: list = []
    b = brief.parse(data, opts, f, None, probs)
    assert b["idiom"] == "ballad" and b["moods"] == ["warm"] and b["palette"] is None and b["ending"] == "fade"
    secs = b["sections"]
    assert [(s["start_s"], s["kind"]) for s in secs] == [(0.0, "verse"), (41.0, "prechorus"), (90.0, "chorus"),
                                                         (150.0, "outro")]
    assert secs[2]["size"] == 1.0 and secs[2]["final"] and secs[2]["peak"] and sum(s["peak"] for s in secs) == 1
    assert secs[3]["arrive"] == "none" and any("solo" in p for p in probs)
    assert brief.parse({**data, "idiom": "polka"}, opts, f) is None
    assert brief.parse({**data, "sections": data["sections"][:1]}, opts, f) is None


def test_a_sung_section_starts_on_the_line_it_names():
    # nightly eval 28 Nov: Hallelujah's refrains came back seconds off their lines (42.8 for the line at 38.3)
    opts = brief.options(idiom.load()["idioms"], {"gold": "Gold."}, ["gold"], [], [])
    lines = [(12.0, "I heard there was"), (20.0, "a secret chord"), (38.3, "Hallelujah"), (60.0, "well your faith"),
             (93.4, "Hallelujah")]
    f = brief.facts({"title": "S", "artist": "A"}, lines, None, None, 200.0)
    data = {"tone": "t", "idiom": "ballad", "moods": [], "palette": "gold", "ending": "fade", "why": "w",
            "sections": [{"start_s": 0.0, "line": -1, "kind": "intro", "size": 0.2, "arrive": "none"},
                         {"start_s": 14.0, "line": 0, "kind": "verse", "size": 0.3, "arrive": "none"},
                         {"start_s": 42.8, "line": 2, "kind": "chorus", "size": 0.6, "arrive": "swell"},
                         {"start_s": 63.0, "line": 3, "kind": "verse", "size": 0.3, "arrive": "none"},
                         {"start_s": 70.0, "line": 1, "kind": "bridge", "size": 0.4, "arrive": "none"},   # goes back
                         {"start_s": 97.7, "line": 4, "kind": "chorus", "size": 0.9, "arrive": "swell"},
                         {"start_s": 150.0, "line": 9, "kind": "outro", "size": 0.2, "arrive": "none"}]}  # no such line
    probs: list = []
    secs = brief.parse(data, opts, f, None, probs)["sections"]
    assert [(s["start_s"], s["kind"]) for s in secs] == [(0.0, "intro"), (12.0, "verse"), (38.3, "chorus"),
                                                         (60.0, "verse"), (70.0, "bridge"), (93.4, "chorus"),
                                                         (150.0, "outro")]
    assert len(probs) == 2
    # it counts loosely: one line early for the refrain, but its words say which it meant; a repeated line is the
    # one nearest the number it gave, never one before the section ahead of it
    lines = [(4.4, "Now I've heard"), (85.8, "And from your lips she drew the Hallelujah"),
             (93.4, "Hallelujah, Hallelujah"), (99.3, "Hallelujah, Hallelujah"), (115.5, "You say I took the name")]
    f = brief.facts({"title": "S", "artist": "A"}, lines, None, None, 200.0)
    data["sections"] = [{"start_s": 0.0, "line": 0, "words": "Now I've heard", "kind": "verse", "size": 0.3,
                         "arrive": "none"},
                        {"start_s": 86.0, "line": 1, "words": "Hallelujah, Hallelujah", "kind": "chorus", "size": 0.6,
                         "arrive": "swell"},
                        {"start_s": 99.0, "line": 3, "words": "You say I took", "kind": "verse", "size": 0.3,
                         "arrive": "none"}]
    secs = brief.parse(data, opts, f)["sections"]
    assert [s["start_s"] for s in secs] == [0.0, 93.4, 115.5]
    assert '[1,85.8,"And from your lips' in brief.prompt(f, opts)          # numbered for it


def test_a_brief_with_no_ai_and_its_plan_for_structure(tmp_path):
    idioms = idiom.load()["idioms"]
    f = brief.facts({"title": "S", "genre": "Singer/Songwriter"}, [(12.0, "a")], {"hop": 0.5, "v": [0.2] * 60 + [0.9] * 60,
                    "climax": [30.0, 50.0]}, {"bpm": 167.0, "range_db": 10.0}, 60.0)
    b = brief.local(f, idioms, [{"start_s": 12.0, "kind": "verse", "letter": "A"},
                                {"start_s": 30.0, "kind": "chorus", "letter": "B"}])
    assert b["idiom"] in ("hush", "ballad", "story")                 # the genre holds it: never a club track
    assert [s["kind"] for s in b["sections"]] == ["intro", "verse", "chorus"]
    assert b["sections"][2]["peak"] and b["sections"][2]["arrive"] != "none" and b["sections"][2]["size"] == 1.0
    out = brief.stamp(b, "local", brief.facts_key(f), wall=lambda: 5.0)
    brief.save(tmp_path, out)
    assert brief.load(tmp_path, brief.facts_key(f)) == json.loads((tmp_path / brief.FILE).read_text())
    assert brief.load(tmp_path, "other lyrics") is None and brief.load(tmp_path / "none") is None
    plan = brief.plan(out, idioms, None, 60.0)
    lo, hi = idioms[b["idiom"]]["level"]
    assert [p["kind"] for p in plan] == ["instrumental", "verse", "chorus"] and plan[2]["peak"]
    assert plan[2]["level"] == pytest.approx(hi) and lo <= plan[1]["level"] < hi
    assert {"size", "arrive", "brief_kind", "final", "letter"} <= set(plan[0])
    assert brief.view(out)["idiom"] == b["idiom"] and brief.view(None) is None
    # the same kind comes back with the same letter (the look returns), and a prechorus is the verse's look, grown
    two = brief.plan({"idiom": "anthem", "sections": [
        {"start_s": 0.0, "kind": "verse", "size": 0.4}, {"start_s": 15.0, "kind": "prechorus", "size": 0.6},
        {"start_s": 20.0, "kind": "chorus", "size": 0.8}, {"start_s": 40.0, "kind": "verse", "size": 0.5},
        {"start_s": 60.0, "kind": "chorus", "size": 1.0}, {"start_s": 80.0, "kind": "bridge", "size": 0.6},
        {"start_s": 95.0, "kind": "outro", "size": 0.3}]}, idioms)
    assert [p["letter"] for p in two] == ["A", "E", "B", "A", "B", "D", "C"]
    assert two[1]["kind"] == "verse" and two[1]["level"] > two[0]["level"]


def test_make_asks_once_and_keeps_what_fits(tmp_path):
    (tmp_path / "lyrics.json").write_text(json.dumps({"lines": [{"start": 10.0, "text": "a"}, {"start": 40.0, "text": "b"}]}))
    calls = []

    class Llm:
        @staticmethod
        def call_json(task, **kw):
            calls.append(task)

            class R:
                ok, model, error = True, "m", None
                data = {"tone": "t", "idiom": "disco", "moods": [], "palette": "gold", "ending": "fade", "why": "w",
                        "sections": [{"start_s": 0.0, "kind": "intro", "size": 0.3, "arrive": "none"},
                                     {"start_s": 10.0, "kind": "verse", "size": 0.5, "arrive": "bump"},
                                     {"start_s": 40.0, "kind": "chorus", "size": 1.0, "arrive": "bump"}]}
            return R
    song = {"title": "S", "artist": "A", "audio_s": 90.0}
    loc = brief.make(tmp_path, song, ai=False)
    assert loc["by"] == "local" and not calls
    ai = brief.make(tmp_path, song, llm=Llm, feel_palettes=["gold"])
    assert ai["by"] == "ai" and ai["idiom"] == "disco" and ai["palette"] == "gold" and calls == [brief.TASK]
    assert brief.make(tmp_path, song, llm=Llm)["by"] == "ai" and len(calls) == 1          # kept: not asked again
    assert brief.make(tmp_path, song, ai=False)["by"] == "ai"
    (tmp_path / "lyrics.json").write_text(json.dumps({"lines": [{"start": 11.0, "text": "other"}]}))
    assert brief.make(tmp_path, song, ai=False)["by"] == "local"                          # other lyrics: made again


def test_a_lift_winds_up_and_lands():
    a = {"at_ms": 10000.0, "kind": "bump", "wind": 0.4, "lift": 0.5, "wind_ms": 4000.0, "settle_ms": 2000.0,
         "beat_ms": 500.0}
    assert phrasing.envelope(5.0, a) == (1.0, 1.0)                    # before the wind-up: the look as it is
    lv, sz = phrasing.envelope(8.0, a)
    assert 0.6 < lv < 1.0 and 0.76 < sz < 1.0                         # pulling in
    lv, sz = phrasing.envelope(9.99, a)
    assert lv == pytest.approx(0.6, abs=0.01) and sz == pytest.approx(0.76, abs=0.01)
    lv, sz = phrasing.envelope(10.0, a)
    assert lv == pytest.approx(1.5) and sz == pytest.approx(1.25)     # the landing: over its steady level, wide
    lv1, _ = phrasing.envelope(11.0, a)
    assert 1.0 < lv1 < lv and phrasing.envelope(12.0, a) == (1.0, 1.0)
    snap = {**a, "kind": "snap"}
    assert phrasing.envelope(9.8, snap)[0] == phrasing.SNAP_DARK and phrasing.envelope(9.0, snap)[0] > 0.5
    swell = {**a, "kind": "swell"}
    assert phrasing.envelope(9.9, swell) == (1.0, 1.0) and phrasing.envelope(10.0, swell)[0] == pytest.approx(1.5)
    assert phrasing.envelope(13.0, swell)[0] > 1.0                    # a swell settles for longer
    for bad in (None, {}, {"kind": "none"}, {"kind": "bump"}, {"kind": "bump", "at_ms": "x"}):
        assert phrasing.envelope(1.0, bad) == (1.0, 1.0)
