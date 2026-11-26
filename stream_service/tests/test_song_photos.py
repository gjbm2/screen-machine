"""The karaoke song's artist pictures in the renderer (owner, 26 Nov): the feed's PictureSet, the photo scene's
framing, and the scene director's gate, floor share and section fit (measured on the real catalogue)."""
from __future__ import annotations

import pathlib
import random

import numpy as np
import pytest

from stream_service import director as dm
from stream_service.director import AUTO_DEFAULTS, DIRECTION_DEFAULTS, Director, floor_share
from stream_service.lyric_feed import LyricFeed, _face, picture_set
from stream_service.plugins.sources import song_photos as sp

REPO = pathlib.Path(__file__).resolve().parents[2]


def row(i, kind="live", faces=None, **k):
    return {"id": f"a/{i}.jpg", "path": f"/p/{i}.jpg", "w": 1200, "h": 900, "kind": kind, "quality": 0.8,
            "faces": faces or [], "era": "2010s", "moods": ["warm"], **k}


def test_the_feed_reads_a_songs_pictures_and_their_faces():
    ps = picture_set("s1", {"state": "ready", "era": "2010s", "pictures": [
        row(1, faces=[{"x": 0.4, "y": 0.2, "w": 0.1, "h": 0.1}, {"x": 0.6, "y": 0.25, "w": 0.1, "h": 0.1}]),
        row(2), {"id": "bad"}]})
    assert len(ps.pictures) == 2 and ps.era == "2010s" and ps.key.startswith("s1:2:")
    assert ps.pictures[0].face == pytest.approx((0.55, 0.275, 0.15)) and ps.pictures[1].face is None
    assert picture_set("s1", {"pictures": []}) is None and picture_set("s1", None) is None
    assert _face([{"x": "q"}]) is None


def test_the_feed_polls_pictures_until_flask_has_enough():
    calls = []
    clock = [0.0]

    def fetch(url):
        calls.append(url)
        if url.endswith("/now"):
            return {"player": {"song_id": "s1", "state": "playing", "pos_s": 1.0, "at": 0.0}, "song": {"id": "s1"}}
        if url.endswith("/lyrics"):
            return None
        n = 2 if clock[0] < 20 else 12
        return {"state": "fetching" if n < 12 else "ready", "pictures": [row(i) for i in range(n)]}
    f = LyricFeed("http://x", fetch=fetch, mono=lambda: clock[0])
    f.poll_once()
    assert len(f.snap.pictures.pictures) == 2
    clock[0] = 3.0
    f.poll_once()
    assert sum(u.endswith("/pictures") for u in calls) == 1          # not again until PICS_POLL_S
    clock[0] = 21.0
    f.poll_once()
    assert len(f.snap.pictures.pictures) == 12 and f.pictures() is f.snap.pictures
    clock[0] = 60.0
    f.poll_once()
    assert sum(u.endswith("/pictures") for u in calls) == 2          # done: never again


def director(n_pictures, section=None, direction=None):
    d = Director(dict(AUTO_DEFAULTS, seed=3, avoid_repeat=0), direction or dict(DIRECTION_DEFAULTS, moods=[]))
    d.listens = lambda src: ("pictures", "lyrics") if src == "song_photos" else ()
    d.pictures_n = lambda: n_pictures
    d.section = lambda: section
    return d


SCENES = {"photo-verse": {"source": "song_photos", "tags": {"family": "photo", "sections": ["verse"]}},
          "photo-chorus": {"source": "song_photos", "tags": {"family": "photo", "sections": ["chorus"]}},
          **{f"plasma-{i}": {"source": "plasma", "tags": {"family": "x"}} for i in range(30)}}


def share(d, prefix="photo", n=600):
    return sum(d.pick_scene(SCENES, None, 0.0)[0].startswith(prefix) for _ in range(n)) / n


def test_no_engaging_pictures_no_photo_scenes_and_with_them_the_floor():
    assert share(director(0)) == 0.0 and share(director(dm.PICTURES_MIN - 1)) == 0.0
    full = share(director(dm.PICTURES_FULL))
    assert dm.PICTURES_SHARE - 0.06 < full < dm.PICTURES_SHARE + 0.08
    assert share(director(dm.PICTURES_FULL // 2)) < full - 0.1          # a handful of good ones: less often


def test_only_engaging_pictures_count():
    from stream_service.lyric_feed import good_pictures
    ps = picture_set("s1", {"pictures": [row(i, appeal=a) for i, a in enumerate([0.9, 0.7, 0.5, None, 0.2])]})
    assert good_pictures(ps) == 2 and good_pictures(None) == 0


def test_the_section_picks_the_look():
    d = director(10, section="chorus")
    picks = [d.pick_scene(SCENES, None, 0.0)[0] for _ in range(800)]
    assert picks.count("photo-chorus") > 2.5 * picks.count("photo-verse")


def test_not_two_photo_scenes_running_nor_against_the_treatment():
    d = director(10)
    after = sum(d.pick_scene(SCENES, "photo-verse", 0.0)[0].startswith("photo") for _ in range(600)) / 600
    assert after < dm.PICTURES_SHARE_AGAIN + 0.08                      # straight after one: less likely
    shun = director(10, direction=dict(DIRECTION_DEFAULTS, moods=[],
                                       scene_avoid={"tags": ["family:photo"], "factor": 0.3}))
    assert share(shun) < dm.PICTURES_SHARE / 2


def test_floor_share_only_raises():
    assert floor_share([1.0, 9.0], [True, False], 0.25) == pytest.approx([3.0, 9.0])
    assert floor_share([5.0, 5.0], [True, False], 0.25) == [5.0, 5.0]
    assert floor_share([0.0, 5.0], [True, False], 0.25) == [0.0, 5.0]


def test_on_the_real_catalogue_medium_often_for_every_kind_of_song():
    from stream_service.plugins import default_registry
    from stream_service.presets import Presets
    reg = default_registry()
    p = Presets(REPO / "config" / "live", reg)
    tab = reg.table("source")
    photos = [n for n, s in p.scenes.items() if s.get("source") == "song_photos"]
    assert len(photos) >= 8 and {p.resolve_scene(n).params.get("layout") for n in photos} >= {"drift", "float", "strip",
                                                                                           "mosaic"}
    for moods, energy in ((["warm", "elegant", "dreamy"], 0.35), (["bright", "playful", "euphoric"], 0.7),
                          (["aggressive", "tense", "dark"], 0.8)):
        d = Director(dict(AUTO_DEFAULTS, seed=5, avoid_repeat=2), dict(DIRECTION_DEFAULTS, energy=energy, moods=moods))
        d.listens = lambda src: tuple(getattr(tab.get(src or ""), "listens", ()) or ())
        d.pictures_n = lambda: dm.PICTURES_FULL
        cur, hits = None, 0
        for _ in range(250):
            cur = d.pick_scene(p.scenes, cur, 0.0)[0]
            d.note_cue(cur, None, None)
            hits += cur in photos
        assert 0.3 < hits / 250 < 0.5, (moods, hits / 250)                 # owner: "around 30-50%"
        d.pictures_n = lambda: 0
        assert not any(d.pick_scene(p.scenes, None, 0.0)[0] in photos for _ in range(100))


def test_the_framing_holds_the_faces_above_the_lyrics_and_stays_inside():
    for z in (1.0, 1.2, 1.4):
        s, cx, cy = sp.frame_view(1000, 1500, 1920, 1080, (0.5, 0.3, 0.2), z, 1.0)
        vw, vh = 1920 * s, 1080 * s
        assert vw / 2 - 1e-6 <= cx <= 1000 - vw / 2 + 1e-6 and vh / 2 - 1e-6 <= cy <= 1500 - vh / 2 + 1e-6
        face_y = (450 - cy) / s + 540                            # where the face lands on screen
        assert face_y < 0.62 * 1080                              # above the display page's lyrics
    wide = sp.frame_view(1920, 1080, 600, 400, None, 1.0, 0.5)   # a panel of another shape: still inside
    assert wide[0] * 600 <= 1920 + 1e-6 and wide[0] * 400 <= 1080 + 1e-6


def test_the_scene_picks_engaging_ones_most_and_never_repeats_until_all_shown():
    s = sp.SongPhotos.__new__(sp.SongPhotos)
    from stream_service.lyric_feed import Picture
    s.pics = {f"p{i}": Picture(f"p{i}", "/x", 100, 100, k, 0.8, None, None, (), a)
              for i, (k, a) in enumerate([("portrait", 0.6), ("live", 0.9), ("group", 0.7), ("live", 0.8),
                                          ("live", 0.6), ("portrait", 0.95)])}
    s.order, s.bag, s.shown = list(s.pics), [], 0
    s.rng = np.random.default_rng(1)
    s.sound = None
    seen = [s._choose(set()) for _ in range(6)]
    assert sorted(seen) == sorted(s.pics)
    s.bag = list(s.order)
    assert s._choose({"p1", "p2", "p3"}) not in {"p1", "p2", "p3"}      # never what's on screen when another will do
