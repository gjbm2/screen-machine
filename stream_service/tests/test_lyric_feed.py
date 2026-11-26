"""The karaoke song's words at photon time (stream_service/lyric_feed.py; owner, 25 Nov)."""
from __future__ import annotations

import time

import pytest

from stream_service import lyric_feed as L


def lyr(*lines, kind="synced"):
    out = []
    for text, start, step in lines:
        toks = text.split()
        out.append({"start": start, "end": start + step * len(toks), "text": text,
                    "words": [{"t0": start + j * step, "t1": start + (j + 1) * step} for j in range(len(toks))]})
    return {"kind": kind, "lines": out}


SONG = lyr(("hello there you", 10.0, 0.5), ("la la la", 12.0, 0.4), ("hello there you", 30.0, 0.5))


def test_a_song_is_built_word_by_word_with_its_chorus():
    s = L.build_song("k", "T", SONG)
    assert [ln.text for ln in s.lines] == ["hello there you", "la la la", "hello there you"]
    assert [w.text for w in s.lines[0].words] == ["hello", "there", "you"] and s.lines[0].words[1].t0 == 10.5
    assert [(ln.chorus, ln.repeat) for ln in s.lines] == [(True, False), (False, False), (True, True)]
    assert len(s.words) == 9 and s.words[3].line == 1 and s.words[3].i == 0


def test_only_synced_lyrics_make_a_song_and_missing_word_times_spread():
    assert L.build_song("k", "T", lyr(("a b", 1.0, 0.5), kind="plain")) is None
    assert L.build_song("k", "T", {"kind": "synced", "lines": []}) is None
    assert L.build_song("k", "T", None) is None
    s = L.build_song("k", "T", {"kind": "synced", "lines": [{"start": 2.0, "end": 3.0, "text": "one two"}]})
    assert [(w.t0, w.t1) for w in s.words] == [(2.0, 2.5), (2.5, 3.0)]


def test_the_current_line_and_active_follow_the_gaps():
    s = L.build_song("k", "T", SONG)
    assert s.line_at(5.0) == -1 and s.line_at(9.5) == 0 and s.line_at(11.0) == 0
    assert s.line_at(13.5) == 1 and s.line_at(20.0) == -1                  # held a moment, then none in the gap
    assert s.word_at(10.6) == 1 and s.word_at(1.0) == -1
    assert s.active(11.0) == 1.0 and s.active(11.8) == 1.0                  # a short gap keeps it up
    assert s.active(20.0) == 0.0 and s.active(29.5) == pytest.approx(0.5)  # a long gap: down, then up before
    assert s.active(5.0) == 0.0


class Flask:
    def __init__(self):
        self.now = {"player": {"song_id": "k1", "state": "playing", "pos_s": 10.0, "at": time.time()},
                    "song": {"id": "k1", "title": "Song", "artist": "Band", "offset_ms": 100, "user_offset_ms": 50},
                    "settings": {"audio_delay_ms": 200}}
        self.lyrics_reads = 0

    def __call__(self, url):
        if url.endswith("/api/karaoke/now"):
            return self.now
        if url.endswith("/pictures"):                    # the song's artist pictures (test_song_photos.py)
            return {"state": "none", "pictures": []}
        self.lyrics_reads += 1
        return SONG


def test_the_feed_reads_flask_and_the_view_runs_the_lyric_clock():
    fl = Flask()
    feed = L.LyricFeed("http://x", fetch=fl)
    feed.poll_once()
    feed.poll_once()
    assert fl.lyrics_reads == 1 and feed.present()
    sn = feed.fresh()
    assert sn.song.title == "Song — Band" and sn.shift_s == pytest.approx(0.35)
    v = L.LyricView(feed)
    f = v.update(time.monotonic())
    assert f.present and f.t == pytest.approx(10.0 - 0.35, abs=0.05) and f.line == 0
    fl.now["player"] = {"song_id": "k1", "state": "paused", "pos_s": 10.0, "at": time.time()}
    feed.poll_once()
    assert not feed.present()
    fl.now["player"] = {"song_id": None, "state": "stopped"}
    feed.poll_once()
    assert feed.fresh().song is None and not feed.present()


def test_the_clock_slews_small_errors_and_snaps_big_ones():
    s = L.build_song("k", "T", SONG)
    v = L.LyricView()
    assert v.step(s, 10.0, 100.0).t == 10.0
    f = v.step(s, 10.0 + 1 / 60 + 0.1, 100.0 + 1 / 60)                     # 0.1 s behind: eased, no jump
    assert 10.0 + 1 / 60 < f.t < 10.0 + 1 / 60 + 0.02
    f = v.step(s, 25.0, 100.0 + 2 / 60)                                     # a seek: straight there
    assert f.t == 25.0
    for k in range(3, 120):                                                 # active eases toward the song's
        f = v.step(s, 10.0 + k / 60, 100.0 + k / 60)
    assert f.active > 0.95


def test_no_song_fades_out_then_nothing():
    s = L.build_song("k", "T", SONG)
    v = L.LyricView()
    for k in range(60):
        f = v.step(s, 10.0 + k / 60, k / 60)
    assert f.active > 0.9
    f = v._idle(1.05)
    assert f.song is not None and not f.present and f.active < 0.9          # the last words fade ...
    for k in range(60):
        f = v._idle(1.1 + k / 60)
    assert f.song is None and f.active < 0.01                               # ... then nothing


def test_the_demo_song_has_a_chorus():
    s = L.demo_song()
    assert s.lines[0].chorus and s.lines[2].repeat and s.word_at(3.0) >= 0


def test_child_mode_fetches_the_masked_lyrics_and_starts_again():
    fl = Flask()
    masked = lyr(("hello ----- you", 10.0, 0.5))
    def fetch(url):
        if url.endswith("/api/karaoke/now"):
            return fl.now
        if url.endswith("/pictures"):
            return {"state": "none", "pictures": []}
        fl.lyrics_reads += 1
        return masked if fl.now["settings"].get("child_mode") else SONG
    feed = L.LyricFeed("http://x", fetch=fetch)
    feed.poll_once()
    assert feed.fresh().song.lines[0].text == "hello there you"
    fl.now["settings"]["child_mode"] = True
    feed.poll_once()
    s = feed.fresh().song
    assert s.lines[0].text == "hello ----- you" and s.key != "k1" and fl.lyrics_reads == 2
    feed.poll_once()
    assert fl.lyrics_reads == 2                                    # one fetch per switch
