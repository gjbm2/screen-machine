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
