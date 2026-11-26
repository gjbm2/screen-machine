"""A karaoke song's character from its genre, year and lyrics (song_character.py), the treatment's karaoke path, and
the director's use of the song (owner, 26 Nov: song context "should be doing more work"; the a.d. "isn't using the
live audio feed data smartly enough").  Lyrics here are invented test lines."""
from __future__ import annotations

import asyncio
import pathlib
import types

import pytest
import yaml

from show_service import contract
from show_service import director as dmod
from show_service import song_character as sc
from show_service import treatment as tm
from show_service.tests.test_director import Rig, music_context
from show_service.tests.test_treatment import ev, make

VOCAB = yaml.safe_load((pathlib.Path(__file__).parents[2] / "config/live/vocab.yaml").read_text())
MM, MOODS = VOCAB["music_moods"], VOCAB["moods"]

JOY = "we dance in the sun tonight / happy and golden we shine / party till the morning light"
SAD = "I cry alone in the cold rain / you are gone and I miss you / tears on an empty road"


def test_genre_sets_the_base_and_the_lyrics_shade_it():
    happy = sc.character("pop", 2013, JOY, MM, MOODS)
    blue = sc.character("pop", 2013, SAD, MM, MOODS)
    assert happy["theme"] == "joy" and blue["theme"] == "heartbreak"
    assert happy["valence"] > blue["valence"] + 0.2
    assert set(happy["moods"]) <= set(MOODS) and len(happy["moods"]) <= 3
    assert "bright" in happy["moods"] and ("mellow" in blue["moods"] or "dreamy" in blue["moods"])


def test_no_lyrics_still_reads_the_genre_and_the_era():
    c = sc.character("disco", 1978, "", MM, MOODS)
    assert c["theme"] is None and "nostalgic" in c["words"] and c["arousal"] > 0.6
    assert sc.character(None, None, "", MM, MOODS)["moods"]                    # no facts: a gentle default


def test_deezer_genres_map_into_the_vocabulary_and_excerpts_skip_repeats():
    assert sc.genre_word("Alternative") == "indie" and sc.genre_word("R&B/Soul") == "soul"
    assert sc.genre_word("Polka?") is None and sc.genre_word(None) is None
    assert set(sc.SONG_GENRES.values()) <= set(VOCAB["genre"])
    assert sc.excerpt(["one line", "two line", "One  line", "three"]) == "one line / two line / three"
    assert len(sc.excerpt(["x" * 50] * 1 + [f"line {i} " + "y" * 40 for i in range(100)], limit=300)) <= 300


# -- the treatment's AI for a karaoke song -------------------------------------------------------------------------
FACTS = {"title": "Test Song — Test Band", "artist": "Test Band", "genre": "Pop", "year": 2001,
         "character": {"words": ["joyful"], "theme": "joy"}, "sections": "verse 0:10, chorus 0:40",
         "lyrics": "we dance in the sun tonight"}


def test_a_karaoke_song_asks_the_ai_without_ai_listening_and_takes_its_section_curve(tmp_path):
    tr, _ = make(tmp_path)
    tr.song(0.0, "t1", "a new song", ev(song_words=FACTS["title"], song_facts=FACTS))
    sent = {}

    async def acall_json(task, **kw):
        sent.update(kw)
        return types.SimpleNamespace(ok=True, data={
            "families": ["fire"], "avoid_families": [], "palette": None, "lights": [], "form": None,
            "moods": ["euphoric"], "transition_style": "lively", "intensity": 0.8, "why": "A sunny pop song.",
            "sections": {"verse": 0.4, "chorus": 0.9, "bridge": 0.5, "instrumental": "x"}},
            error=None, model="m", latency_ms=500.0, cost_usd=0.0)

    tr._llm = types.SimpleNamespace(acall_json=acall_json)
    coros = []
    tr.tick(0.5, ev(ai_on=False, song_words=FACTS["title"], song_facts=FACTS), coros.append)
    assert len(coros) == 1                                         # no AI listening needed: the facts are known
    asyncio.run(coros[0])
    assert '"song_facts"' in sent["user"] and "dance in the sun" in sent["user"]
    assert tr.plan["by"] == tm.BY_AI and tr.plan["sections"] == {"verse": 0.4, "chorus": 0.9}
    tr2, _ = make(tmp_path / "x") if (tmp_path / "x").mkdir() is None else (None, None)
    tr2.song(0.0, "t2", "a new song", ev())
    coros2 = []
    tr2.tick(30.0, ev(ai_on=False), coros2.append)
    assert not coros2                                               # no facts, no listening: no call, as before


# -- the director plays the song ---------------------------------------------------------------------------------------
def karaoke_rig(tmp_path, lyrics=JOY, genre="Pop"):
    r = Rig(tmp_path)
    r.d.vibes.vocab_path = tmp_path / "live_vocab.yaml"           # the renderer's vocabulary, with music_moods
    r.d.vibes.vocab_path.write_text(yaml.safe_dump(VOCAB))
    r.sec = {"id": 1, "letter": "A"}
    r.lyr = {"kind": "verse", "plan": {"next_kind": "chorus", "next_in_s": 12.0, "median_s": 30.0}}
    r.lufs = -30.0

    def mc():
        m = music_context(r.t, r.sec)
        if r.lyr is not None:
            m["section"].update(r.lyr)
        return m
    r.ctx.music_context = mc
    r.ctx.features = lambda: {**dict(t_ms=r.t * 1000.0, stream="test", energy=0.02, gate=0.9, onset=0.3,
                                     drive=0.5, punch=0.4), "lufs_s": r.lufs}
    r.ctx.now_playing = lambda: {"key": "karaoke:k1:q1", "state": "playing", "pos_s": 10.0, "dur_s": 200.0,
                                 "speed": 1.0, "title": "Test Song — Test Band", "source": "karaoke",
                                 "artist": "Test Band", "genre": genre, "year": 2001, "sections": [],
                                 "lyrics_text": lyrics, "lyrics_excerpt": lyrics}
    return r


def last_dir(r):
    return r.of(contract.ROOM_DIRECTION)[-1]


def test_the_songs_character_gives_the_moods_and_valence(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(3.0)
    d = last_dir(r)
    assert r.d._words_now["moods_from"] == "song" and "bright" in d["moods"]
    assert d["valence"] == pytest.approx(sc.character("pop", 2001, JOY, MM, MOODS)["valence"])
    (tmp_path / "x").mkdir()
    r2 = karaoke_rig(tmp_path / "x", lyrics=SAD)
    r2.run(3.0)
    assert last_dir(r2)["valence"] < d["valence"]


def test_a_quiet_mic_still_hears_the_song_get_louder(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(30.0)                                                    # the verse, at the song's usual level
    verse = last_dir(r)["energy"]
    r.lufs = -24.0                                                 # 6 dB up: the chorus, however quiet the mic
    r.run(6.0)
    louder = last_dir(r)["energy"]
    assert louder > verse + 0.08
    r.lufs = -36.0
    r.run(8.0)
    assert last_dir(r)["energy"] < verse                           # and quieter than its usual: down


def test_the_ais_section_curve_shapes_the_lift(tmp_path):
    r = karaoke_rig(tmp_path)
    r.run(1.0)
    real = r.d.treat.plan
    r.d.treat.plan = {"sections": {"verse": 0.2, "chorus": 1.0}}      # read directly, no tick (a partial plan)
    r.d._mc = r.ctx.music_context()
    assert r.d._section_lift() == pytest.approx(-0.2)               # 0.5 x (0.2 - 0.6)
    r.lyr["kind"] = "chorus"
    r.d._mc = r.ctx.music_context()
    assert r.d._section_lift() == pytest.approx(0.2)
    r.d.treat.plan = {"sections": None}
    assert r.d._section_lift() == dmod.SECTION_ENERGY["chorus"]
    r.d.treat.plan = real


def test_a_quiet_room_never_flattens_a_playing_karaoke_song(tmp_path):
    """The laptop's mic barely hears the screen: "quiet" must not relax the director to the centre while a song plays."""
    r = karaoke_rig(tmp_path)
    r.ctx.situations = lambda: {"quiet": {"value": True}, "talking": {"value": False}}
    r.run(20.0)
    assert r.d._relax < 0.1 and r.d.lv > 0.9
    assert last_dir(r)["energy"] == pytest.approx(r.d.music["band_energy"], abs=0.02)   # the song's, not the centre
