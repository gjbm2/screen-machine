"""treatment.py (README S5.33): the song's plan from what the room offers, taste from people, the AI's answer checked
against its options. Pure: no network (the AI call is driven with a fake utils.llm)."""

import asyncio
import types

from show_service import treatment as tm

CATALOG = {
    "vocab": {"moods": ["calm", "euphoric", "bright", "dreamy"], "transition_style": ["gentle", "lively", "wild"]},
    "presets": {"scenes": {
        "c1": {"source": "cos_a", "tags": {"family": "cosmic", "moods": ["dreamy"], "energy": [0.0, 0.5]}},
        "c2": {"source": "cos_b", "tags": {"family": "cosmic", "moods": ["calm"], "energy": [0.0, 0.5]}},
        "g1": {"source": "geo_a", "tags": {"family": "geometric", "moods": ["euphoric", "bright"],
                                           "energy": [0.5, 1.0]}},
        "g2": {"source": "geo_b", "tags": {"family": "geometric", "moods": ["bright"], "energy": [0.5, 1.0]}},
        "f1": {"source": "fire_a", "tags": {"family": "fire", "moods": ["euphoric"], "energy": [0.4, 1.0]}},
        "off": {"source": "x", "auto": False, "tags": {"family": "fire"}},
    }},
}


def ev(**kw):
    base = {"mode": "party", "direction": {"genre": "house", "moods": ["euphoric", "bright"], "energy": 0.8},
            "palettes": ["neon", "rainbow", "gold"], "palette_now": "neon", "palette_pick": False, "change": "big",
            "light_pools": {"lamps": ["beat-pulse", "slow-wave"]}, "forms": ["echo-across", "call-and-answer"],
            "song_words": None, "based_on": ["the room mic"], "ai_on": False}
    base.update(kw)
    return base


def make(tmp_path, wall=None):
    t = {"now": 1_000_000.0}
    taste = tm.Taste(tmp_path / "taste.json", wall=wall or (lambda: t["now"]))
    tr = tm.Treatments(taste=taste, mono=lambda: 0.0)
    tr.index.ingest(CATALOG)
    return tr, t


def test_the_index_skips_presets_off_auto_and_scores_by_fit_and_stars(tmp_path):
    tr, _ = make(tmp_path)
    assert sorted(tr.index.families) == ["cosmic", "fire", "geometric"]
    assert [s["name"] for s in tr.index.families["fire"]] == ["f1"]
    sc = tr.index.scores(ev()["direction"])
    assert sc["geometric"]["score"] > sc["cosmic"]["score"]          # loud euphoric house: geometric fits
    tr.index.ingest_ratings({"ratings": {"scene/cos_a": {"stars": 5}, "scene/cos_b": {"stars": 5},
                                         "scene/geo_a": {"reject": True}}})
    sc2 = tr.index.scores(ev()["direction"])
    assert sc2["cosmic"]["stars"] == 5 and sc2["geometric"]["n"] == 2   # a reject drops out of the score only


def test_a_song_plan_favours_the_fitting_families_and_moves_the_palette_on(tmp_path):
    tr, _ = make(tmp_path)
    p = tr.song(0.0, "track:1", "a new song", ev())
    assert p["by"] == tm.BY_DIRECTOR and p["families"][0] == "geometric"
    assert p["favour"] == {"tags": [f"family:{f}" for f in p["families"]], "share": 0.5}
    assert p["palette"] == "rainbow"                                  # the next in the feel's ranked list
    assert "geometric" in p["why"] and p["avoid"] is None


def test_consecutive_songs_differ_and_a_family_two_songs_running_is_rested(tmp_path):
    tr, _ = make(tmp_path)
    a = tr.song(0.0, "track:1", "a new song", ev())
    b = tr.song(10.0, "track:2", "a new song", ev(palette_now=a["palette_used"]))
    c = tr.song(20.0, "track:3", "a new song", ev(palette_now=b["palette_used"]))
    assert len({a["palette_used"], b["palette_used"], c["palette_used"]}) == 3
    common = set(a["families"]) & set(b["families"])
    if common <= set(c["families"]):
        assert not common or c["avoid"] is None
    else:
        assert set(c["avoid_families"]) == common - set(c["families"])


def test_a_small_change_keeps_the_palette_until_it_has_run_long_enough(tmp_path):
    tr, _ = make(tmp_path)
    tr.song(0.0, "t1", "a new song", ev(palette_now="neon"))
    tr.plan["palette_used"] = "neon"
    assert tr.song(1.0, "t2", "a new song", ev(palette_now="neon", change="small"))["palette"] is None
    tr.plan["palette_used"] = "neon"
    assert tr.song(2.0, "t3", "a new song", ev(palette_now="neon", change="small"))["palette"] is None
    tr.plan["palette_used"] = "neon"
    assert tr.song(3.0, "t4", "a new song", ev(palette_now="neon", change="small"))["palette"] == "rainbow"


def test_a_persons_palette_pick_is_never_overridden(tmp_path):
    tr, _ = make(tmp_path)
    assert tr.song(0.0, "t1", "a new song", ev(palette_pick=True))["palette"] is None


def test_taste_next_and_keep_move_the_families_and_decay(tmp_path):
    tr, t = make(tmp_path)
    p = tr.song(0.0, "t1", "a new song", ev())
    fam = p["families"][0]
    assert tr.feedback("next") and tr.taste.family(p["ctx"], fam) == -1.0
    t["now"] += 14 * 86400.0
    assert abs(tr.taste.family(p["ctx"], fam) + 0.5) < 1e-6          # half-life 14 days
    again = tm.Taste(tmp_path / "taste.json", wall=lambda: t["now"])  # persisted
    assert abs(again.family(p["ctx"], fam) + 0.5) < 1e-6
    tr.feedback("steps", steps=1)
    tr.feedback("steps", steps=1)
    assert abs(tr.taste.bias(p["ctx"]) - 0.06) < 1e-6
    assert any("skipped" in x for x in tr.taste.lines(p["ctx"]))


def test_skipped_families_drop_down_the_next_plan(tmp_path):
    tr, _ = make(tmp_path)
    p = tr.song(0.0, "t1", "a new song", ev())
    assert set(p["families"]) == {"geometric", "fire"}               # both fit loud euphoric house as well
    p["families"] = ["geometric"]
    for _ in range(4):
        tr.feedback("next")                                          # people skipped the geometric scenes
    q = tr.song(1.0, "t2", "a new song", ev())
    assert q["families"][0] == "fire"


def test_a_long_stretch_is_a_new_plan(tmp_path):
    tr, _ = make(tmp_path)
    tr.song(0.0, "t1", "a new song", ev())
    assert tr.tick(10.0, ev(), None) is False
    assert tr.tick(tm.FALLBACK["replan_s"] + 1.0, ev(), None) is True and tr.plan["change_why"] == "a long stretch"


def test_the_ai_is_asked_once_per_song_and_its_names_are_checked(tmp_path):
    tr, _ = make(tmp_path)
    tr.song(0.0, "t1", "a new song", ev())
    answer = {"families": ["fire", "made-up"], "avoid_families": ["cosmic"], "palette": "gold",
              "lights": [{"group": "lamps", "scene": "beat-pulse"}, {"group": "lamps2", "scene": "x"}],
              "form": "nope", "moods": ["euphoric", "loud"], "transition_style": "wild", "intensity": 1.4,
              "why": "Hot house: fire scenes and gold light, cut on the beat."}

    async def acall_json(task, **kw):
        assert task == tm.TASK and "options" in kw["user"]
        return types.SimpleNamespace(ok=True, data=answer, error=None, model="m", latency_ms=900.0, cost_usd=0.0003)

    tr._llm = types.SimpleNamespace(acall_json=acall_json)
    coros = []
    assert tr.tick(1.0, ev(ai_on=True), coros.append) is False
    assert not coros                                                 # no title: it lets the listener hear first
    tr.tick(1.0 + tm.FALLBACK["ai"]["refine_after_s"], ev(ai_on=True), coros.append)
    assert len(coros) == 1
    asyncio.run(coros[0])
    p = tr.plan
    assert p["by"] == tm.BY_AI and p["families"] == ["fire"] and p["avoid_families"] == ["cosmic"]
    assert p["palette"] == "gold" and p["lights"] == {"lamps": "beat-pulse"} and p["form"] is None
    assert p["moods"] == ["euphoric"] and p["transition_style"] == "wild" and p["intensity"] == 1.0
    tr.tick(100.0, ev(ai_on=True), coros.append)
    assert len(coros) == 1                                           # once per song


def test_a_title_lets_the_ai_go_at_once_and_a_failed_call_keeps_the_directors_plan(tmp_path):
    tr, _ = make(tmp_path)
    tr.song(0.0, "t1", "a new song", ev(song_words="Gimme! — ABBA"))

    async def acall_json(task, **kw):
        return types.SimpleNamespace(ok=False, data=None, error="timeout", model=None, latency_ms=0.0, cost_usd=0.0)

    tr._llm = types.SimpleNamespace(acall_json=acall_json)
    coros = []
    tr.tick(0.5, ev(ai_on=True, song_words="Gimme! — ABBA"), coros.append)
    assert len(coros) == 1
    asyncio.run(coros[0])
    assert tr.plan["by"] == tm.BY_DIRECTOR and tr.ai_state["error"] == "timeout"


def test_parse_keeps_fallbacks_for_bad_values():
    probs = []
    cfg = tm.parse({"favour_share": 3, "families_max": "x", "ai": {"timeout_s": 5}, "bogus": 1}, probs)
    assert cfg["favour_share"] == 0.9 and cfg["families_max"] == 2 and cfg["ai"]["timeout_s"] == 5.0
    assert any("families_max" in p for p in probs) and any("bogus" in p for p in probs)


# -- in the director --------------------------------------------------------------------------------------------------
def test_the_director_plans_each_song_and_says_why(tmp_path):
    from show_service import contract
    from show_service.tests.test_director import LATE_OK, stage4_rig

    r = stage4_rig(tmp_path, LATE_OK)
    r.d.treat.index.ingest(CATALOG)
    r.sec.update(track=1, id=5, letter=None, started_s=r.t)
    r.event("track_change", r.t, track_id=1, via="sonos")
    r.run(3.0)
    p = r.d.treat.plan
    assert p is not None and p["song"] == "track:1" and p["by"] == tm.BY_DIRECTOR
    d = r.of(contract.ROOM_DIRECTION)[-1]
    assert d["scene_favour"] == p["favour"] and d["scene_favour"]["tags"]
    assert d["dwell_s"][0] == 15.0                                    # a feel changes the looks: the show decides
    why = r.d.snapshot()["reasoning"]
    assert why["plan"]["by"] == "the director" and why["plan"]["favour"] == p["families"]
    texts = [x["text"] for x in why["recent"]]
    assert any(t.startswith("A new song (Sonos said so)") for t in texts)
    assert any(t.startswith("The director planned") for t in texts)
    assert any(t.startswith("New look: a new song") for t in texts)
    r.d.action("next")
    assert r.d.treat.taste.family(p["ctx"], p["families"][0]) == -1.0
