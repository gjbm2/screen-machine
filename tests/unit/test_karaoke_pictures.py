"""Karaoke artist pictures (routes/karaoke/pictures.py; owner, 26 Nov): who the artists are, where the pictures come
from, what the vetting keeps, and a song's list. No network: the catalogues and the AI are fakes."""
import io
import types

import pytest

from routes.karaoke import pictures as pic


def jpeg(w=1200, h=900, colour=(120, 80, 40)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(pic, "pictures_root", lambda: tmp_path / "pictures")
    monkeypatch.setattr(pic, "COMMONS_GAP_S", 0.0)
    monkeypatch.setattr(pic, "_commons", {"pause_until": 0.0, "last": 0.0})
    monkeypatch.setattr(pic, "_moods", lambda: ["warm", "bright", "playful"])
    return tmp_path / "pictures"


def test_a_band_name_with_an_ampersand_is_one_artist_and_feat_splits(monkeypatch):
    known = {"simon garfunkel": {"id": 1, "name": "Simon & Garfunkel"},
             "calvin harris": {"id": 2, "name": "Calvin Harris", "nb_fan": 9e6}, "rihanna": {"id": 3, "name": "Rihanna",
                                                                                      "nb_fan": 9e6},
             "pharrell williams": {"id": 4, "name": "Pharrell Williams"}, "williams": {"id": 5, "name": "Williams",
                                                                                     "nb_fan": 12}}
    monkeypatch.setattr(pic, "deezer_artist", lambda n: known.get(pic.norm(n)))
    assert [a["name"] for a in pic.resolve_artists("Simon & Garfunkel")] == ["Simon & Garfunkel"]
    assert [a["name"] for a in pic.resolve_artists("Calvin Harris feat. Rihanna")] == ["Calvin Harris", "Rihanna"]
    assert pic.resolve_artists("") == []
    assert [a["name"] for a in pic.resolve_artists("Williams, Pharrell")] == ["Pharrell Williams"]   # surname first
    assert pic.resolve_artists("Williams & Nobody Known") == []          # a little-known split-off name: no one


def test_deezer_artist_is_strict_about_the_name(monkeypatch):
    monkeypatch.setattr(pic, "_get", lambda url, params=None, **k: {"data": [
        {"id": 1, "name": "Queen Latifah", "nb_fan": 9}, {"id": 2, "name": "Queen", "nb_fan": 5},
        {"id": 3, "name": "QUEEN", "nb_fan": 1}]})
    assert pic.deezer_artist("Queen")["id"] == 2
    assert pic.deezer_artist("Quen") is None


def test_commons_rows_use_standard_thumbnails_and_free_licences(monkeypatch):
    def page(i, title, w, h, lic, mime="image/jpeg"):
        return {"index": i, "title": f"File:{title}", "imageinfo": [{
            "url": f"https://upload.wikimedia.org/wikipedia/commons/a/ab/{title.replace(' ', '_')}", "width": w,
            "height": h, "mime": mime, "descriptionurl": "d",
            "extmetadata": {"LicenseShortName": {"value": lic}, "Artist": {"value": "<a href='x'>Jo Photo</a>"}}}]}
    monkeypatch.setattr(pic, "_get", lambda url, params=None, **k: {"query": {"pages": {
        "1": page(1, "Singer live 2019.jpg", 3000, 2000, "CC BY-SA 4.0"),
        "2": page(2, "Singer small.jpg", 900, 600, "CC BY 2.0"),            # no standard width below its own
        "3": page(3, "Singer press.jpg", 2000, 3000, "All rights reserved"),
        "4": page(4, "Singer wide.jpg", 1300, 500, "CC0"),                   # 1280 thumb is 492 high: too small
        "5": page(5, "Singer.png", 1500, 1500, "Public domain", "image/png")}}})
    rows = pic._commons_rows({})
    assert [r["url"] for r in rows] == [
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Singer_live_2019.jpg/1920px-Singer_live_2019.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Singer.png/1280px-Singer.png"]
    assert rows[0]["credit"] == "Jo Photo" and rows[0]["license"] == "CC BY-SA 4.0"


def test_deezer_gives_only_the_artists_own_photo():
    got = pic.deezer_candidates({"id": 7, "name": "T", "picture_xl": "https://p/artist/abc/1000x1000-000000-80-0-0.jpg"},
                                "Fearless")
    assert [c["title"] for c in got] == ["T (Deezer)"]                   # no album covers (owner, 26 Nov)
    assert pic.deezer_candidates({"id": 7, "name": "T", "picture_xl": "https://p/images/artist//1000x1000.jpg"}, None) == []


def test_covers_objects_dull_pictures_and_a_sixth_portrait_are_not_kept():
    v = {"shows": "the_artist", "kind": "live", "quality": 0.9, "appeal": 0.8, "child_safe": True}
    assert pic.keep(v) == (True, "ok")
    assert pic.keep({**v, "shows": "album_cover"})[0] is False and pic.keep({**v, "kind": "other"})[0] is False
    assert pic.keep({**v, "appeal": 0.2}) == (False, "appeal 0.2")


def fake_vet(answers):
    def vet(path, artist, c, band=()):
        a = answers.get(c["title"], {})
        return {"shows": "the_artist", "kind": "live", "quality": 0.9, "child_safe": True, "faces": [], "era": "2010s",
                "moods": [], "caption": "", "model": "m", "cost_usd": 0.0, **a}
    return vet


def test_the_vetting_keeps_the_good_and_remembers_the_rest(root, monkeypatch):
    cands = [{"url": f"https://x/{i}.jpg", "source": "commons", "kind_hint": None, "title": t, "trusted": False}
             for i, t in enumerate(["good", "unsafe", "someone else", "blurry", "tiny"])]
    monkeypatch.setattr(pic, "deezer_candidates", lambda a, album: [])
    monkeypatch.setattr(pic, "commons_candidates", lambda n, y, wd, band=(): list(cands))
    monkeypatch.setattr(pic, "fanart_candidates", lambda m: [])
    monkeypatch.setattr(pic, "audiodb_candidates", lambda n: [])
    monkeypatch.setattr(pic, "brave_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "youtube_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "_mbid", lambda n: None)
    monkeypatch.setattr(pic, "ai_ready", lambda: True)
    monkeypatch.setattr(pic, "_get", lambda url, params=None, raw=False, **k: jpeg(300, 200) if "4" in url else jpeg())
    monkeypatch.setattr(pic, "vet", fake_vet({"unsafe": {"child_safe": False}, "someone else": {"shows": "other_people"},
                                              "blurry": {"quality": 0.2}}))
    idx = pic.fetch_artist("Singer", None, year=2015)
    assert [p["title"] for p in idx["pictures"]] == ["good"]
    assert sorted(idx["rejected"].values()) == ["download: ValueError", "not for children", "quality 0.2",
                                                "shows other_people"]
    assert len(list((root / "singer").glob("*.jpg"))) == 1 and idx["decades"] == ["2010s"]


def test_without_the_ai_only_the_catalogues_own_pictures(root, monkeypatch):
    monkeypatch.setattr(pic, "deezer_candidates", lambda a, album: [
        {"url": "https://d/a.jpg", "source": "deezer", "kind_hint": "portrait", "title": "a", "trusted": True},
        {"url": "https://d/b.jpg", "source": "deezer", "kind_hint": "cover", "title": "b", "trusted": False}])
    monkeypatch.setattr(pic, "commons_candidates", lambda n, y, wd, band=(): [
        {"url": "https://c/c.jpg", "source": "commons", "kind_hint": None, "title": "c", "trusted": False}])
    monkeypatch.setattr(pic, "fanart_candidates", lambda m: [])
    monkeypatch.setattr(pic, "audiodb_candidates", lambda n: [])
    monkeypatch.setattr(pic, "brave_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "youtube_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "_mbid", lambda n: None)
    monkeypatch.setattr(pic, "ai_ready", lambda: False)
    monkeypatch.setattr(pic, "_get", lambda url, params=None, raw=False, **k: jpeg())
    idx = pic.fetch_artist("Singer", {"id": 1}, year=None)
    assert [(p["title"], p["vetted"]) for p in idx["pictures"]] == [("a", False)]
    assert pic._unvetted(idx)


def test_a_rate_limit_is_for_later_not_a_rejection(root, monkeypatch):
    import urllib.error

    def get(url, params=None, raw=False, **k):
        raise urllib.error.HTTPError(url, 429, "slow down", {"Retry-After": "600"}, None)
    monkeypatch.setattr(pic, "deezer_candidates", lambda a, album: [])
    monkeypatch.setattr(pic, "commons_candidates", lambda n, y, wd, band=(): [
        {"url": "https://upload.wikimedia.org/x.jpg", "source": "commons", "title": "x", "trusted": False},
        {"url": "https://upload.wikimedia.org/y.jpg", "source": "commons", "title": "y", "trusted": False}])
    monkeypatch.setattr(pic, "fanart_candidates", lambda m: [])
    monkeypatch.setattr(pic, "audiodb_candidates", lambda n: [])
    monkeypatch.setattr(pic, "brave_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "youtube_candidates", lambda n, y: [])
    monkeypatch.setattr(pic, "_mbid", lambda n: None)
    monkeypatch.setattr(pic, "ai_ready", lambda: True)
    monkeypatch.setattr(pic, "_get", get)
    idx = pic.fetch_artist("Singer", None)
    assert idx["pictures"] == [] and idx["rejected"] == {} and idx["incomplete"] is True
    assert pic._commons["pause_until"] > 0


def test_a_songs_list_merges_its_artists_hides_and_puts_its_era_first(root, monkeypatch):
    import json
    for s, rows in {"a": [("1", "2010s", 0.9), ("2", "1980s", 0.6)], "b": [("3", "1980s", 0.95)]}.items():
        (root / s).mkdir(parents=True)
        for f, _, _ in rows:
            (root / s / f"{f * 16}.jpg").write_bytes(jpeg())
        (root / s / "index.json").write_text(json.dumps({"fetched_at": 1.0, "pictures": [
            {"file": f"{f * 16}.jpg", "url": f, "w": 1200, "h": 900, "kind": "live", "era": e, "quality": q,
             "appeal": q, "hidden": False, "faces": []} for f, e, q in rows]}))
    (root / "songs.json").write_text(json.dumps({"s1": {"artist": "A & B", "artists": [
        {"name": "A", "slug": "a"}, {"name": "B", "slug": "b"}]}}))
    st = types.SimpleNamespace(lock=__import__("threading").RLock(), s={"songs": {"s1": {"artist": "A & B", "year": 1986}}})
    monkeypatch.setattr(pic, "store", lambda: st)
    got = pic.song_pictures("s1")
    assert got["state"] == "ready" and got["era"] == "1980s"
    assert [p["id"] for p in got["pictures"]] == ["b/" + "3" * 16 + ".jpg", "a/" + "2" * 16 + ".jpg", "a/" + "1" * 16 + ".jpg"]
    idx = json.loads((root / "a" / "index.json").read_text())
    idx["pictures"][0]["hidden"] = True
    (root / "a" / "index.json").write_text(json.dumps(idx))
    assert len(pic.song_pictures("s1")["pictures"]) == 2
    assert pic.song_pictures("nope")["state"] == "none"


def test_a_bands_members_longest_serving_first():
    rel = lambda n, b, e: {"type": "member of band", "direction": "backward", "artist": {"name": n}, "begin": b, "end": e}   # noqa: E731
    d = {"type": "Group", "relations": [rel("Drummer One", "1987", "1988"), rel("Singer", "1987", "1994"),
                                        rel("Bassist", "1987", "1994"), rel("Drummer Two", "1990", "1994"),
                                        {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q1"}}]}
    assert pic.members(d) == ["Singer", "Bassist", "Drummer Two", "Drummer One"]
    assert pic.members({**d, "type": "Person"}) == []


def test_dark_bars_are_trimmed():
    from PIL import Image
    im = Image.new("RGB", (1920, 1080), (0, 0, 0))
    im.paste(Image.new("RGB", (1440, 1080), (150, 90, 60)), (240, 0))
    assert pic._trim(im).size == (1440, 1080)
    full = Image.new("RGB", (800, 600), (150, 90, 60))
    assert pic._trim(full).size == (800, 600)


def test_brave_never_goes_past_its_monthly_cap(root, monkeypatch):
    monkeypatch.setattr(pic, "BRAVE_MONTH_MAX", 3)
    assert [pic._brave_take() for _ in range(5)] == [True, True, True, False, False]
    monkeypatch.setattr(pic.smconfig, "env", lambda k, d="": "")
    assert pic.brave_candidates("Singer", 1999) == []                    # no key: no search
