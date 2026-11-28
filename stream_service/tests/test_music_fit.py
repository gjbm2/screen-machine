"""The screens' music-fit pieces (27 Nov, docs/AD_MUSIC_FIT_REVIEW_2026-11.md): the karaoke song's measured energy at
photon time, bar-quantised transitions, the FX bus holding round a cut, drops cutting, a build lifting effects."""
from __future__ import annotations

import time
from types import SimpleNamespace

from stream_service import lyric_feed
from stream_service.binding import quant_beats
from stream_service.effects import BUILD_LIFT, build_lift
from stream_service.fx_director import CUT_HOLD_BARS, CUT_HOLD_S, FxDirector
from stream_service.sessions import HOT_SECTION_ENERGY, event_via


def _shape(n: int = 40, step_at: int = 20) -> dict:
    loud = [-30.0] * step_at + [-12.0] * (n - step_at)          # a quiet verse, then a loud chorus
    v = [0.1] * step_at + [0.9] * (n - step_at)
    return {"hop": 0.5, "v": v, "loud": loud, "climax": [10.0, 20.0], "version": 1}


def test_energy_curve_rises_into_the_chorus_not_after_it():
    hop, e = lyric_feed.energy_curve(_shape())
    assert hop == 0.5 and len(e) == 40 and all(0.0 <= x <= 1.0 for x in e)
    assert e[5] < 0.2 and e[35] > 0.9
    # loud[i] is the 3 s ending at (i + 1) x hop: read LOUD_SHIFT_S later, so the rise starts before the step at 10 s
    first = next(i for i, x in enumerate(e) if x > 0.3)
    assert 7 <= first * hop <= 10.0
    assert lyric_feed.curve_at((hop, e), 1000.0) is None          # past the song's end: nothing
    assert abs(lyric_feed.curve_at((hop, e), 0.25) - (e[0] + e[1]) / 2) < 1e-9


def test_energy_curve_degrades():
    assert lyric_feed.energy_curve(None) is None
    assert lyric_feed.energy_curve({"v": [0.5] * 3}) is None
    hop, e = lyric_feed.energy_curve({"hop": 0.5, "v": [0.2] * 10})   # no loudness: the contour alone
    assert e == tuple([0.2] * 10)


def test_karaoke_energy_mixes_the_file_at_photon_time(monkeypatch, tmp_path):
    (tmp_path / "s1.json").write_text(__import__("json").dumps(_shape()))
    now = time.monotonic()
    replies = {"/api/karaoke/now": {"player": {"song_id": "s1", "state": "playing", "pos_s": 15.0, "at": time.time()},
                                    "song": {"id": "s1", "title": "T"}, "settings": {"audio_delay_ms": 500}},
               "/api/karaoke/songs/s1/lyrics": {"lines": []}, "/api/karaoke/songs/s1/pictures": None}
    f = lyric_feed.LyricFeed("http://x", fetch=lambda u: replies.get(u[len("http://x"):]))
    f.shape_dir = str(tmp_path)
    f.poll_once()
    assert f.snap.curve is not None and f.snap.delay_s == 0.5
    e = f.song_energy(now)
    assert e is not None and e > 0.8                       # 14.5 s into the song: the chorus
    monkeypatch.setattr(lyric_feed, "_SHARED", f)
    monkeypatch.setattr(lyric_feed, "_SHARED_DONE", True)
    fast, w = lyric_feed.karaoke_energy(0.2, 0.0, now)     # the mic feed gone: the file alone, at full weight
    assert w == 1.0 and abs(fast - e) < 1e-9
    fast, w = lyric_feed.karaoke_energy(0.2, 1.0, now)
    assert abs(fast - (lyric_feed.SONG_ENERGY_W * e + (1 - lyric_feed.SONG_ENERGY_W) * 0.2)) < 1e-9
    replies["/api/karaoke/now"]["player"]["state"] = "paused"
    f.poll_once()
    assert lyric_feed.karaoke_energy(0.2, 1.0, now) == (0.2, 1.0)    # paused: the mic's, as before


def test_karaoke_energy_never_starts_a_feed(monkeypatch):
    monkeypatch.setattr(lyric_feed, "_SHARED_DONE", False)
    assert lyric_feed.karaoke_energy(0.4, 0.7, time.monotonic()) == (0.4, 0.7)


def test_transitions_round_to_whole_bars_once_long():
    p = 0.5                                                 # 120 bpm
    assert quant_beats(0.6, p, 4) == 0.5                    # a beat
    assert quant_beats(1.1, p, 4) == 1.0                    # two beats
    assert quant_beats(1.6, p, 4) == 2.0                    # 3.2 beats -> one bar
    assert quant_beats(3.4, p, 4) == 4.0                    # 6.8 beats -> two bars
    assert quant_beats(3.4, p, 0) == 3.5                    # no bar known: beats, as before


def test_the_bus_holds_round_an_announced_cut():
    fd = FxDirector(1)
    fd.auto_fx = {"enabled": True, "max": 2}
    fd.armed = True
    fd.next_at = 100.0
    fd.grid = (0.5, 4)
    end = fd.hold_for_cut(101.0, 100.0)
    assert end == 101.0 + CUT_HOLD_BARS * 2.0
    assert not fd.due(102.0) and fd.due(end + 0.01)
    fd.event = "drop"                                       # a drop still moves
    assert fd.due(102.0)
    fd.event, fd.grid, fd.hold_until = None, None, None
    assert fd.hold_for_cut(None, 200.0) == 200.0 + CUT_HOLD_S


def test_drops_cut_and_hot_sections_may():
    assert event_via("drop", None) == ["cut"]
    assert event_via("section_change", {"energy": HOT_SECTION_ENERGY}) == ["cut", "wild"]
    assert event_via("section_change", {"energy": 0.3}) is None
    assert event_via("section_change", None) is None
    assert event_via("track_change", {"energy": 1.0}) is None


def test_a_build_lifts_the_bus():
    feed = SimpleNamespace(building=lambda now: 0.5)
    assert abs(build_lift(SimpleNamespace(feed=feed, now=1.0)) - (1 + BUILD_LIFT * 0.5)) < 1e-9
    assert build_lift(None) == 1.0
    assert build_lift(SimpleNamespace(feed=None, now=1.0)) == 1.0
