"""A karaoke song's sections from its lyrics (lyric_sections.py) and structure's lyric plan (owner, 26 Nov: "verse /
chorus etc. triggers we can give to the a.d.")."""
from __future__ import annotations

import pytest

from show_service.lyric_sections import sections_from_lyrics
from show_service.tests.test_structure import Rig, abab


def song(*lines):
    return {"kind": "synced", "lines": [{"start": s, "end": e, "text": t} for s, e, t in lines]}


VERSE1 = [(10, 13, "walking down the road"), (13.5, 16, "nothing on my mind"), (16.5, 19, "the sun is in my eyes")]
CHORUS = [(20, 23, "Oh we are golden"), (23.5, 26, "turn it up tonight")]
VERSE2 = [(27, 30, "talking to the moon"), (30.5, 33, "it's later than it seems"), (33.5, 36, "nobody else around")]


def kinds(secs):
    return [(round(s["start_s"]), s["kind"], s["letter"]) for s in secs]


def test_repeated_lines_are_the_chorus_and_the_rest_verse():
    secs = sections_from_lyrics(song(*VERSE1, *CHORUS, *VERSE2, *[(s + 17, e + 17, t) for s, e, t in CHORUS]))
    assert kinds(secs) == [(10, "verse", "A"), (20, "chorus", "B"), (27, "verse", "A"), (37, "chorus", "B")]


def test_a_long_gap_is_a_break_and_the_tail_an_outro():
    later = [(s + 30, e + 30, t) for s, e, t in CHORUS]          # 30 s on: an 11 s gap after the second verse
    secs = sections_from_lyrics(song(*VERSE1, *CHORUS, *VERSE2, *later), dur=80.0)
    assert kinds(secs) == [(10, "verse", "A"), (20, "chorus", "B"), (27, "verse", "A"), (36, "instrumental", "C"),
                           (50, "chorus", "B"), (56, "outro", "C")]


def test_a_lone_line_and_a_short_section_dont_split_it():
    ad_lib = [(26.2, 26.9, "yeah")]                             # once, between two chorus lines' runs
    secs = sections_from_lyrics(song(*CHORUS, *ad_lib, *[(s + 7, e + 7, t) for s, e, t in CHORUS], *VERSE2[:0],
                                     (40, 43, "a verse line"), (43.5, 46, "another one"), (46.5, 49, "and a third")))
    assert kinds(secs) == [(20, "chorus", "B"), (40, "verse", "A")]
    short = sections_from_lyrics(song(*VERSE1, (24, 25, "hey"), (25.2, 26.5, "hey"), *VERSE2))
    assert [s["kind"] for s in short] == ["verse"]               # a 4 s chorus tag joins the verse


def test_nothing_known_without_synced_lyrics():
    assert sections_from_lyrics({"kind": "plain", "lines": [{"start": 1, "end": 2, "text": "x"}]}) == []
    assert sections_from_lyrics(None) == [] and sections_from_lyrics({"kind": "synced", "lines": []}) == []
    v2 = [(a - 7, b - 7, t) for a, b, t in VERSE2]
    assert [s["kind"] for s in sections_from_lyrics(song(*VERSE1, *v2))] == ["verse"]   # no repeats: one verse


# -- structure follows the plan --------------------------------------------------------------------------------------
PLAN = [{"pos_s": 20.0, "kind": "verse", "letter": "A"}, {"pos_s": 40.0, "kind": "chorus", "letter": "B"},
        {"pos_s": 60.0, "kind": "verse", "letter": "A"}, {"pos_s": 80.0, "kind": "chorus", "letter": "B"}]


def karaoke(r: Rig, offset: float = 0.0, state: str = "playing"):
    return lambda: {"key": "karaoke:k1:q1", "state": state, "pos_s": r.t + offset, "dur_s": 200.0, "speed": 1.0,
                    "title": "Song", "source": "karaoke", "sections": PLAN}


def test_structure_announces_the_lyrics_sections_ahead_with_their_letters():
    r = Rig(lambda t: abab(t, length=25.0))                    # the ear alone would cut every 25 s
    r.ctx.now_playing = karaoke(r)
    r.run(95.0)
    ev = [e for e in r.events if e["kind"] == "section_change"]
    lyr = [e for e in ev if e.get("detail") == "lyrics"]
    assert [(round(e["at_ms"] / 1000.0), e["letter"], e["section_kind"]) for e in lyr] == \
        [(20, "A", "verse"), (40, "B", "chorus"), (60, "A", "verse"), (80, "B", "chorus")]
    assert all(e["predicted"] for e in lyr)
    assert all(e["at_ms"] / 1000.0 - e["t_ms"] / 1000.0 >= 1.0 for e in lyr)      # announced ahead (>= min_lead_s)
    assert not [e for e in ev if e.get("detail") == "section"]                   # the ear's guesses held
    letters = [s["letter"] for s in r.s.sections if s.get("lyrics")]
    assert letters == ["A", "B", "A", "B"] and r.s.chorus == "B"
    b = [x for x in r.s.sections if x.get("lyrics")]
    assert b[2]["repeat_of"] == b[0]["id"] and b[3]["repeat_of"] == b[1]["id"]   # each chorus recalls the last


def test_a_seek_skips_what_passed_and_no_plan_means_the_ear_again():
    r = Rig(lambda t: abab(t, length=25.0))
    r.ctx.now_playing = karaoke(r, offset=45.0)                # joined at song 45 s: only 60 and 80 are ahead
    r.run(40.0)
    lyr = [e for e in r.events if e.get("detail") == "lyrics"]
    assert [round(e["at_ms"] / 1000.0) for e in lyr] == [15, 35]
    r.ctx.now_playing = lambda: None                           # the song stopped: structure listens again
    r.run(140.0)
    assert r.s._plan is None
    assert [e for e in r.events if e["kind"] == "section_change" and e.get("detail") == "section"]


def test_paused_announces_nothing():
    r = Rig(lambda t: abab(t, length=25.0))
    r.ctx.now_playing = lambda: {"key": "karaoke:k1:q1", "state": "paused", "pos_s": 10.0, "dur_s": 200.0,
                                 "speed": 1.0, "source": "karaoke", "sections": PLAN}
    r.run(30.0)
    assert not [e for e in r.events if e.get("detail") == "lyrics"]


@pytest.mark.parametrize("bad", [None, [], [{"pos_s": "x", "letter": "A"}], "nope"])
def test_bad_sections_are_no_plan(bad):
    r = Rig(lambda t: {"vec": "A"})
    r.ctx.now_playing = lambda: {"key": "karaoke:k1:q1", "state": "playing", "pos_s": r.t, "dur_s": 200.0,
                                 "speed": 1.0, "source": "karaoke", "sections": bad}
    r.run(10.0)
    assert not [e for e in r.events if e.get("detail") == "lyrics"]


def test_a_pause_with_a_section_announced_starts_nothing_and_resume_announces_it_again():
    r = Rig(lambda t: abab(t, length=25.0))
    st = {"state": "playing", "pos": None, "t0": 0.0}

    def np_():
        pos = r.t - st["t0"] if st["state"] == "playing" else st["pos"]
        return {"key": "karaoke:k1:q1", "state": st["state"], "pos_s": pos, "dur_s": 200.0, "speed": 1.0,
                "title": "Song", "source": "karaoke", "sections": PLAN}
    r.ctx.now_playing = np_
    r.run(36.0)                                                # chorus B (song 40 s) is announced by now
    assert [e["letter"] for e in r.events if e.get("detail") == "lyrics"][-1] == "B"
    st.update(state="paused", pos=36.0)
    r.run(60.0)                                                # paused for 24 s across where B would have started
    started = [s for s in r.s.sections if s.get("lyrics")]
    assert [s["letter"] for s in started] == ["A"]             # B never started during the pause
    st.update(state="playing", t0=r.t - 36.0)                  # resume from song 36 s: B is 4 s ahead
    r.run(70.0)
    lyr = [e for e in r.events if e.get("detail") == "lyrics"]
    assert [e["letter"] for e in lyr] == ["A", "B", "B"]       # announced again after the resume
    assert round(lyr[-1]["at_ms"] / 1000.0) == 64              # at song 40 s as it now plays
    assert [s["letter"] for s in r.s.sections if s.get("lyrics")] == ["A", "B"]
