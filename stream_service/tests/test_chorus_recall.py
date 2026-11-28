"""A karaoke chorus comes back to its own scene within a song, never to another song's (27 Nov: every song's chorus
recalled the first chorus scene ever picked, because the session read a song counter only its manager keeps)."""
from __future__ import annotations

import types

from stream_service.sessions import Session
from stream_service.upnext import UpNext


def _session(track_n: int) -> Session:
    s = Session.__new__(Session)
    s.mgr = types.SimpleNamespace(track_n=track_n, presets=types.SimpleNamespace(scenes={"lava": {}, "other": {}}))
    s.director = types.SimpleNamespace(quarantine={})
    s.upnext = UpNext()
    s.current_name = "other"
    s.prepared_pick = None
    s._letter_scene = {}
    s._lyric_letter = None
    return s


def test_a_chorus_recalls_its_scene_in_the_same_song_only():
    s = _session(track_n=1)
    s._letter_scene[(1, "B")] = "lava"                       # song 1's chorus got lava
    assert s._lyric_section({"letter": "B", "section_kind": "chorus"}) is True
    assert s.upnext.head("scene")["scene"] == "lava"
    s2 = _session(track_n=1)
    s2._letter_scene[(1, "B")] = "lava"
    s2.mgr.track_n = 2                                        # the next song
    assert s2._lyric_section({"letter": "B", "section_kind": "chorus"}) is False
    assert s2._letter_scene == {} and s2._lyric_letter == (2, "B")
