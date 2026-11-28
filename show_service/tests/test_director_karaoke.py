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


def test_a_songs_brief_is_what_the_director_performs(tmp_path):
    """brief.py: the song's idiom is a limit (which lights play, which scenes are drawn), its words lead, and a
    section that is a lift gets its wind-up and landing (owner, 27 Nov: "the a.d.'s job is then to execute that
    strategy properly")."""
    from types import SimpleNamespace
    from show_service import idiom
    r = karaoke_rig(tmp_path)
    np0 = r.ctx.now_playing()
    bf = {"idiom": "hush", "tone": "spare and still", "palette": None, "moods": ["calm", "warm"], "genre": "folk",
          "ending": "fade", "by": "ai", "why": "w"}
    r.ctx.now_playing = lambda: {**np0, "brief": bf}
    r.run(3.0)
    d = r.d
    b = d._brief()
    assert b["entry"]["motion_max"] == "still" and b["idiom"] == "hush"
    assert d._song_genre() == "folk" and d._song_char()["moods"] == ["calm", "warm"]
    assert d._words(d.vs)["moods_from"] in ("brief", "human")
    # which lights play: a hushed song's strobe zones never; its heads always
    d._sec_brief = {"size": 0.3, "arrive": "none", "peak": False, "brief_kind": "verse"}
    assert d._sec_class() == "verse" and d._idiom_rests("flash") and not d._idiom_rests("movers")
    d._sec_brief = {"size": 1.0, "arrive": "bump", "peak": True, "brief_kind": "chorus"}
    assert d._sec_class() == "peak" and d._idiom_rests("flash") and d._rests("flash") is True
    # the scenes outside the idiom
    out = d._idiom_scenes_out(b)
    assert all(not idiom.allows(b["entry"], d.light_scenes.scenes[n]) for n in out)
    if "movers-ballyhoo" in d.light_scenes.scenes:
        assert "movers-ballyhoo" in out and "movers-still-pools" not in out
    # a lift: wound up to and landed; the look carries it until it has settled
    grid = SimpleNamespace(bar_s=2.0, beat_s=0.5)
    d._sec_size, d._arrival = 0.3, None
    d._arrive(100.0, grid)
    a = d._arrival_view(99.0)
    assert a["kind"] == "bump" and a["at_ms"] == 100000.0 and a["wind_ms"] == 4000.0 and a["settle_ms"] == 2000.0
    assert a["wind"] == pytest.approx(b["entry"]["wind"]) and a["lift"] == pytest.approx(b["entry"]["lift"])
    assert d._arrival_view(103.0) is None and d._arrival is None
    d._sec_size, d._sec_brief = 0.9, {"size": 0.4, "arrive": "snap", "peak": False, "brief_kind": "verse"}
    d._arrive(120.0, grid)
    assert d._arrival is None                                        # down: a change, not a lift
    d._sec_size, d._sec_brief = 0.4, {"size": 0.7, "arrive": "none", "peak": False, "brief_kind": "bridge"}
    d._arrive(140.0, grid)
    assert d._arrival["kind"] == b["entry"]["arrive"]                # a lift the brief didn't name: the idiom's way
    # the real draw, under the brief: every group gets a program with a named transition, and no mover scene
    # outside the idiom is drawn while one inside it fits
    d._sec_brief = {"size": 0.3, "arrive": "none", "peak": False, "brief_kind": "verse"}
    for _ in range(6):
        d._pick_light_scene("section", r.t)
        progs = d._programs_view()
        assert all(isinstance(pg.get("transition"), dict) and pg["transition"].get("name") for pg in progs.values())
        for gid, pg in progs.items():
            pool = [n for n in d.light_scenes.pool_for(d._groups[gid], d._light_tags, d._moment,
                                                       rest=d._rests(gid), alone=d._alone(gid))]
            if any(n not in out for n in pool):
                assert pg["scene"] not in out, (gid, pg["scene"])
    # the palette: the brief's first choice, its second when the song before played in the first
    cands = d._candidates(d.vs)
    if len(cands) >= 2:
        two = {**b, "palette": cands[0], "palette_alt": cands[1]}
        d._bpal, d._prev_song_pal = (None, None), None
        assert d._brief_palette(two, d.vs) == cands[0]
        d._bpal, d._prev_song_pal = (None, None), cands[0]
        assert d._brief_palette(two, d.vs) == cands[1]
        d._prev_song_pal = None
        assert d._brief_palette(two, d.vs) == cands[1]               # settled once a song
        d._bpal = (None, None)
        assert d._brief_palette({**b, "palette": "no-such", "palette_alt": None}, d.vs) is None
    # an idiom the file doesn't have, or the setting off: the feel's whole catalogue, as before
    d.cfg["brief"]["enabled"] = False
    assert d._brief() is None and not d._idiom_rests("flash")
    d.cfg["brief"]["enabled"] = True
    r.ctx.now_playing = lambda: {**np0, "brief": {**bf, "idiom": "polka"}}
    assert d._brief() is None


def test_with_no_brief_a_lift_is_what_is_heard_coming(tmp_path):
    """Music nobody laid out ahead has no narrative to plan: a section predicted while the music builds gets the
    wind-up and landing, sized by the build (owner, 27 Nov: "degrade gracefully to emergent audio cues")."""
    from types import SimpleNamespace
    r = karaoke_rig(tmp_path)
    r.ctx.now_playing = lambda: None                                 # the mic: no song, no brief
    r.run(3.0)
    d = r.d
    grid = SimpleNamespace(bar_s=2.0, beat_s=0.5)
    d._arrival, d._armed = None, None
    d._phase_eff = lambda now_s: "build"
    d._build_progress = lambda: 0.8
    d._arrive_heard({"predicted": True}, r.t + 6.0, r.t, grid)
    a = d._arrival
    assert a["kind"] == "bump" and a["wind_s"] == 4.0 and 0.2 < a["wind"] < 0.3
    d._arrival = None
    d._arrive_heard({"predicted": True}, r.t + 2.5, r.t, grid)
    assert d._arrival["wind_s"] == pytest.approx(2.5)                # only as long as the warning allows
    for ev, bp, phase, armed in (({"predicted": False}, 0.8, "build", None), ({"predicted": True}, 0.1, "build", None),
                                 ({"predicted": True}, 0.8, "steady", None), ({"predicted": True}, 0.8, "build", {})):
        d._arrival, d._armed = None, armed
        d._phase_eff, d._build_progress = (lambda now_s, p=phase: p), (lambda b=bp: b)
        d._arrive_heard(ev, r.t + 6.0, r.t, grid)
        assert d._arrival is None, (ev, bp, phase, armed)


def test_no_song_chosen_is_a_holding_pattern_not_a_show_of_the_background_music(tmp_path):
    """Owner, 28 Nov: "feels like we're playing entire music scenes etc. during pre-state (i.e. no actively selected
    song) ... it should just be in a gentle holding pattern". Between singers the karaoke screen plays quiet music; the
    mic hears it, but no song is chosen (Flask's rest): the room waits, relaxes, and ignores its sections and drops."""
    r = Rig(tmp_path)
    r.d.set_target("alpha")
    r.ctx.now_playing = lambda: None
    r.ctx.room_rest = lambda: True
    r.run(3.0)
    assert r.d._moment in ("waiting", "lull")
    n = r.d.sec_stats["events"].get("section_change", 0)
    r.event("section_change", r.t + 0.5)
    r.event("drop", r.t + 1.0)
    r.run(1.0)
    assert r.d.sec_stats["events"]["rest_ignored"] >= 2
    assert not [e for e in r.d._log if e.get("kind") in ("section_look", "token")]
    r.run(40.0)
    assert r.d._relaxed
    r.ctx.room_rest = lambda: False                                # a song is chosen: the show again
    r.run(3.0)
    assert not r.d._resting()
