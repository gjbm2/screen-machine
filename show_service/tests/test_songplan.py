"""songplan.py: a karaoke song's sections with the level its shape gives each, its climax marked and split out."""
import random

from show_service import light_scenes as ls
from show_service import songplan as sp
from show_service.director import Director

SECS = [{"start_s": 10.0, "kind": "verse", "letter": "A", "line": 0},
        {"start_s": 50.0, "kind": "chorus", "letter": "B", "line": 8},
        {"start_s": 80.0, "kind": "verse", "letter": "A", "line": 14},
        {"start_s": 150.0, "kind": "chorus", "letter": "B", "line": 30}]


def _shape(levels, climax):
    """levels: [(until_s, value)] -> a shape at hop 0.5."""
    v, t = [], 0.0
    for until, val in levels:
        while t < until:
            v.append(val)
            t += 0.5
    return {"hop": 0.5, "v": v, "climax": list(climax)}


def test_no_shape_leaves_the_sections_with_nothing_known():
    out = sp.lay_out(SECS, None, 200.0)
    assert [x["kind"] for x in out] == ["verse", "chorus", "verse", "chorus"]
    assert all(x["level"] is None and x["peak"] is None for x in out)


def test_each_section_gets_its_level_and_the_last_chorus_is_the_peak():
    shape = _shape([(50, 0.2), (80, 0.6), (150, 0.3), (200, 0.95)], (160, 180))
    out = sp.lay_out(SECS, shape, 200.0)
    assert [x["level_raw"] for x in out] == [0.2, 0.6, 0.3, 0.95]
    lv = [x["level"] for x in out]
    assert lv[0] < lv[2] < lv[1] < lv[3] and lv[0] < 0.15 and lv[3] >= 0.95   # spread over the song's own range
    assert [x["peak"] for x in out] == [False, False, False, True]


def test_a_climax_inside_a_long_verse_starts_its_own_section():
    shape = _shape([(50, 0.2), (80, 0.4), (120, 0.3), (150, 0.95), (200, 0.5)], (120, 140))
    out = sp.lay_out(SECS, shape, 200.0)
    assert [(x["start_s"], x["kind"], x["letter"]) for x in out] == [
        (10.0, "verse", "A"), (50.0, "chorus", "B"), (80.0, "verse", "A"), (120.0, "verse", "A"),
        (150.0, "chorus", "B")]
    assert [x["peak"] for x in out] == [False, False, False, True, False]
    assert out[3]["level"] > 0.9 and out[2]["level"] < 0.35


def test_a_loud_stretch_that_is_not_a_peak_splits_nothing():
    # the climax window inside a long verse, but the song is near-level: no peak, so no false same-letter boundary
    shape = _shape([(80, 0.7), (120, 0.72), (150, 0.76), (200, 0.7)], (120, 140))
    out = sp.lay_out(SECS, shape, 200.0)
    assert [x["start_s"] for x in out] == [10.0, 50.0, 80.0, 150.0]
    assert all(x["peak"] is None for x in out) and not any(x.get("split") for x in out)


def test_a_level_song_has_no_peak():
    out = sp.lay_out(SECS, _shape([(200, 0.7)], (100, 120)), 200.0)
    assert all(x["peak"] is None for x in out) and all(x["level"] == 0.7 for x in out)


def test_the_directors_kind_for_the_section_coming():
    k = Director._into_kind
    assert k({"section_kind": "chorus", "final": True, "peak": None}) == "final_chorus"     # no shape: the last chorus
    assert k({"section_kind": "chorus", "final": True, "peak": True}) == "final_chorus"
    assert k({"section_kind": "chorus", "final": True, "peak": False}) == "chorus"          # the climax is elsewhere
    assert k({"section_kind": "verse", "peak": True}) == "climax"
    assert k({"section_kind": "verse", "peak": False}) == "verse" and k({"kind": "section_change"}) is None


def test_a_grown_section_takes_a_line_up_that_keeps_its_parts():
    lus = ls.parse_lineups({"one": {"plays": ["a"], "into": ["chorus"]},
                            "two": {"plays": ["a", "b"], "into": ["chorus"]},
                            "other": {"plays": ["b", "c"], "into": ["chorus"]}}, {"a", "b", "c"}, [])
    rng = random.Random(1)
    assert {ls.pick_lineup(lus, "chorus", 0.5, rng, over={"a"}) for _ in range(20)} == {"two"}
    assert ls.pick_lineup(lus, "chorus", 0.5, rng, over={"a", "b", "c"}) is None


# -- the pre-state: a mode's own scenes for the moment, then the moment's for any mode, then the general pool ----------
def _moment_cat(tmp_path):
    from show_service.tests import test_light_scenes as tls
    tag = {"energy": [0.0, 1.0], "genre_fit": {"*": 0.5}, "valence": 0.5, "organic": 0.5, "density": 0.2}
    scenes = {"version": 1, "scenes": {
        "calm": {"layers": [], "tags": {**tag, "energy": [0.0, 0.3]}},
        "busy": {"layers": [], "tags": {**tag, "energy": [0.7, 1.0]}},
        "own-wait": {"layers": [], "tags": {**tag, "moments": ["waiting"], "modes": ["sing"]}},
        "flash-dark": {"layers": [], "tags": {**tag, "moments": ["waiting"], "parts": ["flash"]}}}}
    c = tls.full_cat(tmp_path, scenes=scenes, effects={"version": 1, "effects": {}})
    g = ls.group_members({"lamps": {"select": "kind:lamp"},
                          "flashes": {"select": "kind:lamp", "scenes_for": ["flash"]}}, tls.rig_of(tmp_path, movers=0))
    return c, g


def test_the_pre_state_is_the_modes_own_scene_or_the_general_pool(tmp_path):
    c, g = _moment_cat(tmp_path)
    assert sorted(c.pool_for(g["lamps"], ("modes:sing",), "waiting")) == ["own-wait"]       # the mode's own
    assert sorted(c.pool_for(g["lamps"], (), "waiting")) == ["busy", "calm"]                # no mode: the general pool
    assert sorted(c.pool_for(g["lamps"], ("modes:other",), "waiting")) == ["busy", "calm"]  # another mode's isn't taken
    assert sorted(c.pool_for(g["lamps"], ("modes:sing",), "song")) == ["busy", "calm"]      # nor does it leak into songs
    assert c.pool_for(g["flashes"], (), "waiting") == ["flash-dark"]                        # a part's, in any mode


def test_the_moment_holds_the_directed_energy_down():
    d = Director.__new__(Director)
    d._moments_cfg = {"lull_after_s": 180.0, "energy": {"waiting": 0.2, "lull": 0.1}}
    d._moment = "waiting"
    assert d._moment_energy(0.6) == 0.2 and d._moment_energy(0.1) == 0.1 and d._moment_energy(0.6, "song") == 0.6
    d.direction = {"energy": 0.55, "moods": ["calm"]}
    assert d._direction_now("lull")["energy"] == 0.1 and d._direction_now("song") == d.direction


def test_a_section_with_no_bigger_line_up_of_its_own_grows_into_any_bigger_one():
    lus = ls.parse_lineups({"v2": {"plays": ["a", "b"], "into": ["verse"]},
                            "big": {"plays": ["a", "b", "c"], "into": ["chorus", "any"]}}, {"a", "b", "c"}, [])
    assert ls.pick_lineup(lus, "verse", 0.5, random.Random(1), over={"a", "b"}) == "big"


def test_what_carries_the_beat_is_a_pulse_not_bar_locked_movement():
    assert ls.carries_beat({"layers": [{"id": "p", "fx": "swell", "phase": "beat"}]})
    assert ls.carries_beat({"layers": [], "hits": {"styles": ["beat"]}})
    assert not ls.carries_beat({"layers": [{"id": "s", "kind": "position", "mode": "sweep", "phase": "bar",
                                            "needs": "grid"}], "hits": {"styles": []}})
