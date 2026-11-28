"""The singer's own photo in the photo scenes (owner, 28 Nov: "inject the current singer into the photostream"):
/api/karaoke/now's singer_pictures lead the song's pictures, and the scene shows them more often than once a bag."""
from __future__ import annotations

import numpy as np

from stream_service.lyric_feed import Picture, picture_set, with_singers
from stream_service.plugins.sources import song_photos as sp


def row(i, kind="live"):
    return {"id": f"a/{i}.jpg", "path": f"/p/{i}.jpg", "w": 1200, "h": 900, "kind": kind, "appeal": 0.8}


SINGER = {"id": "singer/clare-1.jpg", "path": "/s/clare-1.jpg", "w": 900, "h": 1200, "kind": "singer", "appeal": 1.0,
          "faces": [{"x": 0.3, "y": 0.2, "w": 0.4, "h": 0.4}]}


def test_the_singer_leads_the_songs_pictures_and_changes_the_key():
    ps = picture_set("s1", {"pictures": [row(i) for i in range(3)]})
    both = with_singers(ps, [SINGER], "s1")
    assert [p.kind for p in both.pictures] == ["singer", "live", "live", "live"] and both.key != ps.key
    assert both.pictures[0].face is not None
    assert with_singers(ps, [], "s1") is ps and with_singers(ps, None, "s1") is ps
    alone = with_singers(None, [SINGER], "s1")                    # no artist pictures yet: the singer still
    assert len(alone.pictures) == 1


def test_the_scene_shows_the_singer_more_than_once_a_bag():
    s = sp.SongPhotos.__new__(sp.SongPhotos)
    s.pics = {f"p{i}": Picture(f"p{i}", "/x", 100, 100, "live", 0.8, None, None, (), 0.8) for i in range(18)}
    s.pics = {"me": Picture("me", "/x", 100, 100, "singer", 0.9, None, None, (), 1.0), **s.pics}
    s.order, s.bag, s.shown = list(s.pics), [], 0
    s.rng = np.random.default_rng(3)
    s.sound = None
    seen = [s._choose(set()) for _ in range(2 * len(s.pics))]
    assert seen.count("me") >= 4                                   # about every SINGER_EVERY, not once in 19
    assert set(seen) == set(s.pics)                                # and every artist picture still comes round
