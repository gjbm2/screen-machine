"""karaoke_feed.py: Flask's karaoke player as a music source for the art director, and the mic's Karaoke want."""
from __future__ import annotations

import pytest

from show_service import karaoke_feed as K
from show_service.tests.test_mic import Env
from show_service.tests.test_structure import Rig


def reply(state="playing", pos=10.0, at=100.0, song="k1", qid="q1", dur=200.0):
    return {"now": at, "player": {"state": state, "pos_s": pos, "at": at, "song_id": song, "qid": qid, "rev": 1},
            "song": {"id": song, "title": "Happy", "artist": "Pharrell Williams", "audio_s": dur, "duration": 199.0},
            "settings": {}, "up_next": None}


class Clock:
    def __init__(self) -> None:
        self.t = 100.0


def feed(c: Clock, replies: list):
    def fetch(url):
        o = replies[0]
        if isinstance(o, Exception):
            raise o
        return o
    return K.KaraokeFeed("http://127.0.0.1:5000/", fetch=fetch, clock=lambda: c.t, mono=lambda: c.t)


def test_the_song_is_the_players_shape_at_the_servers_position_now():
    c, rs = Clock(), [reply()]
    f = feed(c, rs)
    assert f.url == "http://127.0.0.1:5000/api/karaoke/now"
    assert f.now_playing() is None and f.playing() is False      # nothing read yet
    f.poll_once()
    c.t = 102.5
    np_ = f.now_playing()
    assert np_ == {"key": "karaoke:k1:q1", "state": "playing", "pos_s": pytest.approx(12.5), "dur_s": 200.0,
                   "speed": 1.0, "title": "Happy — Pharrell Williams", "source": "karaoke",
                   "artist": "Pharrell Williams", "genre": None, "year": None, "lyrics_text": "", "lyrics_excerpt": "",
                   "sections": []}                               # its lyrics aren't synced here: no sections
    assert f.playing() is True
    rs[0] = reply(state="paused", pos=30.0)
    f.poll_once()
    c.t = 106.0
    assert f.now_playing()["pos_s"] == 30.0 and f.playing() is False   # paused: it holds its place
    rs[0] = reply(state="stopped", song=None)
    f.poll_once()
    assert f.now_playing() is None and f.playing() is False
    rs[0] = {**reply(state="paused"), "calibration": {"id": "c1", "state": "running"}}
    f.poll_once()
    assert f.playing() is True                                    # the calibration wants the mic


def test_flask_not_answering_is_no_song_once_the_read_is_stale_and_changes_wake():
    c, rs = Clock(), [reply()]
    f = feed(c, rs)
    woke = []
    f.add_on_change(lambda: woke.append(1))
    f.poll_once()
    assert woke == [1]                                            # it started playing
    f.poll_once()
    assert woke == [1]                                            # no change, no wake
    rs[0] = OSError("refused")
    c.t += 3.0
    f.poll_once()
    assert f.playing() is True                                    # one missed read: still the song
    c.t += K.STALE_S
    assert f.now_playing() is None and f.playing() is False
    rs[0] = "garbage"
    f.poll_once()
    assert f.now_playing() is None


def test_the_music_pages_piece_wins_over_karaoke():
    piece = {"key": "lib:a", "state": "playing"}
    k = {"key": "karaoke:k1:q1", "state": "playing"}
    assert K.chain_now_playing(lambda: piece, lambda: k)() is piece
    assert K.chain_now_playing(lambda: None, lambda: k)() is k
    assert K.chain_now_playing(None, lambda: k)() is k

    def boom():
        raise RuntimeError
    assert K.chain_now_playing(boom, lambda: k)() is k


def test_a_karaoke_song_playing_wants_the_mic_and_mute_wins(tmp_path):
    e = Env(tmp_path)
    singing = {"on": False}
    e.mic.add_want("karaoke", "Karaoke", lambda: singing["on"])
    assert e.mic.evaluate()["why"] == "Off: nothing needs it"
    singing["on"] = True
    b = e.mic.evaluate()
    assert b["on"] is True and b["wanted"] is True and b["why"] == "Listening for Karaoke"
    assert b["claims"] == [{"by": "karaoke", "label": "Karaoke"}]
    assert b["streams"]["capturing"] == ["room-mic", "pc22-mic"]
    b = e.mic.put(muted=True)
    assert b["on"] is False and b["why"] == "Switched off: Karaoke can't hear the room"
    e.mic.put(muted=False)
    e.auto = True
    assert e.mic.evaluate()["why"] == "Listening for Auto"
    singing["on"] = "yes"                                         # not True: no want
    e.auto = None
    assert e.mic.evaluate()["on"] is False


def test_karaoke_songs_back_to_back_arm_end_and_change_track_for_the_director():
    """Owner, 25 Nov: "song endings should trigger the a.d. to switch effects"; autoplay runs songs with no gap."""
    r = Rig(lambda t: {"vec": "A"})
    songs = [(-100.0, 130.0, "k1", "q1"), (30.0, 90.0, "k2", "q2")]   # (start, duration, song, queue entry)

    def fetch(url):
        start, dur, sid, qid = [s for s in songs if r.t >= s[0]][-1]
        return reply(pos=0.0, at=start, song=sid, qid=qid, dur=dur)
    f = K.KaraokeFeed("http://x", fetch=fetch, clock=lambda: r.t, mono=lambda: r.t)
    r.ctx.now_playing = K.chain_now_playing(None, f.now_playing)
    t = 0.0
    while t < 36.0:
        t += 1.0
        f.poll_once()
        r.run(t)
    arms, te, tc = r.kinds("track_end_armed"), r.kinds("track_end"), r.kinds("track_change")
    assert len(arms) == 1 and arms[0]["via"] == "player" and arms[0]["at_ms"] / 1000.0 == pytest.approx(30.0, abs=0.2)
    assert te[0]["at_ms"] / 1000.0 == pytest.approx(30.0, abs=0.2) and te[0].get("detail") is None
    assert {e["id"] for e in te} == {te[0]["id"]}                 # one end (a re-send, sound_back, keeps its id)
    assert len(tc) == 1 and tc[0]["via"] == "player" and tc[0]["seq"] > te[0]["seq"]


def test_re_timed_lyrics_are_read_again_and_still_belong_to_the_song():
    """A song's lyrics can be re-timed while it plays (the song's lyrics_rev goes up): the sections are read again."""
    calls = []
    lyr = {"kind": "synced", "lines": [{"start": 10.0, "end": 12.0, "text": "one"}, {"start": 30.0, "end": 32.0, "text": "two"}]}

    def fetch(url):
        if url.endswith("/lyrics"):
            calls.append(url)
            return lyr
        return now

    now = {"player": {"song_id": "k1", "state": "playing", "pos_s": 0.0, "at": 0.0, "qid": "q"},
           "song": {"title": "T", "audio_s": 60.0, "lyrics_rev": 0}, "settings": {}}
    f = K.KaraokeFeed("http://x", fetch=fetch, clock=lambda: 0.0, mono=lambda: 0.0)
    f.poll_once()
    f.poll_once()
    assert len(calls) == 1
    now["song"]["lyrics_rev"] = 2
    f.poll_once()
    assert len(calls) == 2 and f.now_playing()["sections"]                 # read again, and still this song's
