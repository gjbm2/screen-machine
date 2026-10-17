"""Tonal scoring (17 Oct audit, screen-machine-2b): genre fit, the feel axes, graded energy, transitions' moods,
direction.genres and the axes through validation, the music trim, pick_key and Up next's reasons."""
from __future__ import annotations

import pytest

from stream_service import music_direction as M
from stream_service.director import DIRECTION_DEFAULTS, FX_DIRECTION_DEFAULTS, validate_direction
from stream_service.fx_director import FxDirector
from stream_service.plugins.base import ParamError
from stream_service.tags import GENRE_FLOOR, genre_fit, scene_weight, transition_weight
from stream_service.upnext import why_words

VOCAB = {"genre": ["ambient", "techno", "house", "jazz", "classical", "rock"], "transition_style": ["gentle", "wild"]}
INK = {"energy": [0.0, 0.7], "moods": ["calm", "liquid"], "genre": ["ambient", "classical"],
       "genre_fit": {"ambient": 1.0, "classical": 0.8, "jazz": 0.6, "techno": 0.2}, "valence": 0.6, "organic": 0.9,
       "density": 0.2, "weight": 1.0}
LASER = {"energy": [0.5, 1.0], "moods": ["aggressive", "geometric"], "genre": ["techno", "house"],
         "genre_fit": {"techno": 1.0, "house": 0.8, "rock": 0.5}, "valence": 0.4, "organic": 0.0, "density": 0.9,
         "weight": 1.0}


def d(**kw):
    return validate_direction(kw, VOCAB)


def test_genre_fit_reads_genre_fit_a_word_or_a_map():
    assert genre_fit("ambient", INK) == 1.0
    assert genre_fit("rock", INK) == 0.0
    assert genre_fit({"techno": 0.5, "house": 0.5}, LASER) == pytest.approx(0.9)
    assert genre_fit("techno", {"genre": ["techno"]}) == 1.0              # a word list only: listed or not
    assert genre_fit("jazz", {"genre": ["techno"]}) == 0.0
    assert genre_fit("jazz", {"energy": [0, 1]}) is None                  # untagged: no opinion
    assert genre_fit(None, INK) is None and genre_fit({}, INK) is None


def test_genre_separates_ambient_from_techno_at_the_same_energy():
    amb, tech = d(energy=0.6, genre="ambient"), d(energy=0.6, genre="techno")
    assert scene_weight(INK, amb) > 4 * scene_weight(LASER, amb)
    assert scene_weight(LASER, tech) > 3 * scene_weight(INK, tech)
    assert scene_weight(INK, d(genre="rock")) == pytest.approx(GENRE_FLOOR)   # would jar: the floor, never 0


def test_genres_map_wins_over_the_word_and_axes_weigh_when_both_sides_have_them():
    both = d(genre="ambient", genres={"techno": 1.0})
    assert scene_weight(LASER, both) == pytest.approx(1.0)
    calm = d(organic=0.9, density=0.2, valence=0.6)
    assert scene_weight(INK, calm) == pytest.approx(1.0)
    assert scene_weight(LASER, calm) == pytest.approx((1 - 0.6 * 0.9) * (1 - 0.6 * 0.7) * (1 - 0.6 * 0.2))


def test_transitions_weigh_genre_and_moods_softly():
    t = {"transition_style": ["gentle"], "energy": [0.0, 0.6], "moods": ["calm"],
         "genre_fit": {"ambient": 1.0, "techno": 0.0}, "weight": 1.0}
    assert transition_weight(t, d(energy=0.3, genre="ambient", moods=["calm"])) == 1.0
    assert transition_weight(t, d(energy=0.3, genre="ambient", moods=["aggressive"])) == pytest.approx(0.6)
    assert transition_weight(t, d(energy=0.3, genre="techno")) == pytest.approx(GENRE_FLOOR)


def test_effects_weigh_genre():
    fd = FxDirector(1)
    fd.configure({"enabled": True, "pool": "all", "max": 2}, d(energy=0.6, genre="techno"))
    assert fd.weight(LASER) > 3 * fd.weight(INK)


def test_validate_direction_genres_and_axes():
    v = d(genres={"techno": 0.6, "house": 0.2, "polka": 0.9, "jazz": 0})
    assert v["genres"] == {"techno": 0.75, "house": 0.25} and v["genre"] == "techno"
    assert d(genre="jazz", genres={"techno": 1})["genre"] == "jazz"      # a word given stays (console, Room page)
    assert d(genres={"polka": 1})["genres"] is None
    assert d(valence=1.4, organic=0.2)["valence"] == 1.0
    base = d()
    assert all(base[k] is None for k in ("genres", "valence", "organic", "density"))
    with pytest.raises(ParamError):
        d(genres=["techno"])
    with pytest.raises(ParamError):
        d(valence="happy")


def test_the_music_fills_genres_and_axes_only_where_the_panel_left_them_empty():
    panel = dict(DIRECTION_DEFAULTS, moods=[], fx=dict(FX_DIRECTION_DEFAULTS))
    music = dict(panel, genre="techno", genres={"techno": 0.7, "house": 0.3}, organic=0.1, density=0.8,
                 fx=dict(FX_DIRECTION_DEFAULTS, moods=[]))
    eff = M.effective_direction(panel, music, 1.0)
    assert eff["genres"] == {"techno": 0.7, "house": 0.3} and eff["organic"] == 0.1 and eff["density"] == 0.8
    assert {"genre", "organic", "density"} <= set(M.music_fields(panel, music, 1.0))
    mine = dict(panel, genre="jazz", organic=0.9)
    eff = M.effective_direction(mine, music, 1.0)
    assert eff["genre"] == "jazz" and eff["genres"] is None and eff["organic"] == 0.9
    assert M.effective_direction(panel, music, 0.25)["genres"] is None     # words and axes need m >= 0.5


def test_pick_key_ignores_small_genre_and_axis_wobble():
    a = d(genres={"techno": 0.62, "house": 0.38}, density=0.5)
    b = d(genres={"techno": 0.64, "house": 0.36}, density=0.52)
    c = d(genres={"techno": 0.3, "house": 0.7}, density=0.5)
    assert M.pick_key(a) == M.pick_key(b)
    assert M.pick_key(a) != M.pick_key(c)
    hash(M.pick_key(a))


def test_why_words_uses_genre_fit():
    assert "fits techno" in why_words("scene", LASER, d(genres={"techno": 0.8, "house": 0.2})).lower()
    assert "fits rock" not in why_words("scene", INK, d(genre="rock")).lower()


def test_the_musics_words_under_a_modes_own_shade_softly():
    party = dict(d(energy=0.6, moods=["euphoric", "bright"], transition_style="wild"),
                 music_moods=["calm", "liquid"], music_style="gentle")
    assert scene_weight(INK, party) == pytest.approx(0.3)            # the mode's moods miss (x0.3), the music's fit
    assert scene_weight(LASER, party) == pytest.approx(0.3 * 0.6)    # both miss: the music's costs only x0.6
    t = {"transition_style": ["gentle"], "energy": [0.0, 1.0], "moods": ["calm"], "weight": 1.0}
    assert transition_weight(t, party) == pytest.approx(0.3 * 0.6)   # style and moods miss the mode; the music's fit
    t2 = dict(t, transition_style=["lively"], moods=["glitchy"])
    assert transition_weight(t2, party) == pytest.approx(0.3 * 0.6 * 0.8 * 0.6)
    fd = FxDirector(1)
    fd.configure({"enabled": True, "pool": "all", "max": 2}, party)
    assert fd.weight({"moods": ["calm"], "style": "gentle", "weight": 1.0}) == pytest.approx(0.3 * 0.3)
    assert fd.weight({"moods": ["glitchy"], "style": "lively", "weight": 1.0}) == pytest.approx(0.3 * 0.3 * 0.6 * 0.6)
