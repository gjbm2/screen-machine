"""The director while a karaoke song's lyric plan runs (owner, 26 Nov: "verse / chorus etc. triggers ... to help it
control pacing"; song context "should be doing more work"): the screens' dwell from the song's sections, the energy
shaped by the section, and the song's catalogue genre."""
from __future__ import annotations

import pytest

from show_service import contract
from show_service import director as dmod
from show_service.tests.test_director import Rig, music_context


def karaoke_rig(tmp_path, genre="Alternative"):
    r = Rig(tmp_path)
    r.sec = {"id": 1, "letter": "A"}
    r.lyr = {"kind": "verse", "plan": {"next_kind": "chorus", "next_in_s": 12.0, "median_s": 30.0}}

    def mc():
        m = music_context(r.t, r.sec)
        if r.lyr is not None:
            m["section"].update(r.lyr)
        return m
    r.ctx.music_context = mc
    r.ctx.now_playing = lambda: {"key": "karaoke:k1:q1", "state": "playing", "pos_s": 10.0, "dur_s": 200.0,
                                 "speed": 1.0, "title": "Creep — Radiohead", "source": "karaoke",
                                 "artist": "Radiohead", "genre": genre, "year": 1992, "sections": []}
    return r


def last_dir(r):
    return r.of(contract.ROOM_DIRECTION)[-1]


def test_the_song_paces_the_screens_by_its_sections(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(3.0)
    d = last_dir(r)
    assert d["dwell_s"] == [dmod.KARAOKE_DWELL_MIN_S, 45.0]          # 1.5 x the song's 30 s sections
    assert d["section"]["kind"] == "verse"
    r.lyr["plan"]["median_s"] = 12.0
    r.run(3.0)
    assert last_dir(r)["dwell_s"] == [dmod.KARAOKE_DWELL_MIN_S, 30.0]   # never under the floor of the max
    r.lyr = None                                                    # no plan: the feel's (none here) as before
    r.run(3.0)
    assert last_dir(r)["dwell_s"] is None


def test_choruses_lift_and_verses_settle(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(3.0)
    verse = last_dir(r)["energy"]
    r.lyr["kind"] = "chorus"
    r.run(3.0)
    chorus = last_dir(r)["energy"]
    assert chorus - verse == pytest.approx(dmod.SECTION_ENERGY["chorus"] - dmod.SECTION_ENERGY["verse"], abs=0.03)


def test_the_songs_genre_leads_while_it_plays(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(3.0)
    d = last_dir(r)
    assert d["genre"] == "indie" and d["genres"] == {"indie": 1.0}
    (tmp_path / "x").mkdir()
    r2 = karaoke_rig(tmp_path / "x", genre="Something Unknown")
    r2.run(3.0)
    assert last_dir(r2)["genre"] != "indie"                         # unmapped: the room's guess, as before


def test_song_genres_map_into_the_vocabulary():
    import yaml, pathlib
    vocab = yaml.safe_load((pathlib.Path(__file__).parents[2] / "config/live/vocab.yaml").read_text())["genre"]
    assert set(dmod.SONG_GENRES.values()) <= set(vocab)


def test_flourishes_are_spaced_and_few_a_song():
    """show.yaml director.design (owner, 27 Nov: restraint, "not a school disco"): after a flourish the next
    flourish_gap section changes go across plainly; a karaoke song has flourish_per_song of them at most, the last
    kept for its big moment; a new song, a drop and a person's Next are their own occasions."""
    held = dmod.flourish_held
    assert held("section", 0, 0, 3, 2, True) and held("repeat", 0, 1, 3, 2, True)      # just had one
    assert not held("section", 0, 2, 3, 2, True) and not held("section", 1, 5, 3, 2, True)
    assert held("section", 2, 9, 3, 2, True)                          # two spent: the third is the big moment's
    assert not held("section", 9, 9, 3, 2, False)                     # no song to count in: only the spacing
    assert held("dwell", 9, 1, 3, 2, False)
    assert not any(held(r, 9, 0, 3, 2, True) for r in ("track", "drop", "next"))
    assert not held("section", 0, 0, 3, 0, True) and held("section", 0, 9, 1, 0, True)


def test_a_karaoke_song_keeps_one_palette(tmp_path):
    """design.song_palette: once the song's first look has landed, a later plan's palette waits for the next song
    (the sections re-allocate the roles instead)."""
    r = karaoke_rig(tmp_path)
    r.run(3.0)
    d = r.d
    d.pending, d._treat_pal = None, None
    d._song_pal_locked = False
    d._apply_plan({"palette": "another"}, r.t, landed=True)
    assert d._treat_pal == "another"                                 # the song has no look of its own yet
    d._treat_pal, d._song_pal_locked = None, True
    d._apply_plan({"palette": "another"}, r.t, landed=True)
    assert d._treat_pal is None                                      # it has: the palette holds
    d.cfg["design"]["song_palette"] = False
    d._apply_plan({"palette": "another"}, r.t, landed=True)
    assert d._treat_pal == "another"
    d.cfg["design"]["song_palette"], d._treat_pal = True, None
    r.ctx.now_playing = lambda: None                                 # no karaoke song: plans change it as before
    d._apply_plan({"palette": "another"}, r.t, landed=True)
    assert d._treat_pal == "another"
