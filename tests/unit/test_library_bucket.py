"""The Library bucket (owner, 25 Nov): a screen shows another bucket's favourites (display's `bucket`), and
tools/artworks/fetch.py makes the catalogue's works a bucket without losing what people did with it."""
import importlib.util
import json
from pathlib import Path

import routes.bucket_api as bucket_api
import routes.publisher as publisher

REPO = Path(__file__).resolve().parents[2]


def _buckets(monkeypatch, tmp_path, favourites):
    lib = tmp_path / "library"
    lib.mkdir()
    for name in favourites + ["not-a-favourite.jpg"]:
        (lib / name).write_bytes(b"picture")
    (lib / "bucket.json").write_text(json.dumps({"sequence": favourites + ["not-a-favourite.jpg"], "favorites": favourites}))
    monkeypatch.setattr(bucket_api, "bucket_path", lambda b: tmp_path / b)
    monkeypatch.setattr(bucket_api, "load_meta", lambda b: json.loads((tmp_path / b / "bucket.json").read_text())
                        if (tmp_path / b / "bucket.json").exists() else {"sequence": [], "favorites": []})
    shown = []

    def publish(source, publish_destination_id, **kw):
        shown.append((publish_destination_id, Path(source)))
        return {"success": True}
    monkeypatch.setattr(publisher, "publish_to_destination", publish)
    monkeypatch.setattr(publisher, "get_published_info",
                        lambda d: {"published": shown[-1][1].name} if shown else {})
    return lib, shown


def test_a_screen_walks_another_buckets_favourites_from_that_bucket(monkeypatch, tmp_path):
    lib, shown = _buckets(monkeypatch, tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    for _ in range(4):
        assert publisher.display_from_bucket("north-screen", "Next", silent=True, bucket="library")["success"]
    assert [d for d, _ in shown] == ["north-screen"] * 4
    assert [p for _, p in shown] == [lib / "a.jpg", lib / "b.jpg", lib / "c.jpg", lib / "a.jpg"]


def test_without_a_bucket_it_is_the_screens_own(monkeypatch, tmp_path):
    _buckets(monkeypatch, tmp_path, ["a.jpg"])
    r = publisher.display_from_bucket("north-screen", "Random")
    assert not r["success"] and "north-screen" in r["error"]


def test_a_bucket_with_no_favourites_says_so(monkeypatch, tmp_path):
    _buckets(monkeypatch, tmp_path, [])
    r = publisher.display_from_bucket("north-screen", "Next", bucket="library")
    assert not r["success"] and "library" in r["error"]


def _fetch(monkeypatch, out):
    spec = importlib.util.spec_from_file_location("artworks_fetch", REPO / "tools" / "artworks" / "fetch.py")
    fetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch)
    monkeypatch.setattr(fetch, "OUT", out)
    return fetch


def _entry(wid, year=1503):
    return {"id": wid, "title": wid.title(), "artist": "Someone", "year": year, "movement": "High Renaissance",
            "genre": "portrait", "medium": "oil", "people": "one", "period": "Renaissance", "tags": [], "aka": [],
            "width": 100, "height": 150, "source": {"article": wid, "file": f"{wid}.jpg", "license": "Public domain"}}


def test_the_catalogue_becomes_a_bucket_and_keeps_what_people_did_with_it(monkeypatch, tmp_path):
    out = tmp_path / "library"
    (out / "thumbs").mkdir(parents=True)
    for wid in ("mona", "venus", "hare"):
        (out / f"{wid}.jpg").write_bytes(b"picture")
        (out / "thumbs" / f"{wid}.jpg").write_bytes(b"thumb")
    # what people did since the last run: a favourite, a clip made from it, art tags, one picture of their own
    (out / "bucket.json").write_text(json.dumps({"sequence": ["mona.jpg", {"file": "mona-clip.mp4"}, "mine.jpg"],
                                                 "favorites": ["mona.jpg"], "published_meta": {"filename": "mona.jpg"}}))
    (out / "mona.jpg.json").write_text(json.dumps({"tags": ["smile"], "tagging": {"status": "done"}}))
    fetch = _fetch(monkeypatch, out)
    fetch.write_bucket([_entry("mona"), _entry("venus", year=-50), _entry("hare")])

    meta = json.loads((out / "bucket.json").read_text())
    assert meta["sequence"] == ["mona.jpg", {"file": "mona-clip.mp4"}, "mine.jpg", "venus.jpg", "hare.jpg"]
    assert meta["favorites"] == ["mona.jpg"] and meta["published_meta"] == {"filename": "mona.jpg"}
    mona = json.loads((out / "mona.jpg.json").read_text())
    assert mona["tags"] == ["smile"] and mona["tagging"] == {"status": "done"}, "art tags kept"
    assert mona["prompt"] == "Mona, Someone, 1503" and mona["artwork"]["id"] == "mona"
    assert json.loads((out / "venus.jpg.json").read_text())["prompt"].endswith("50 BC")
    assert (out / "thumbnails" / "hare.jpg.jpg").read_bytes() == b"thumb"

    fetch.write_bucket([_entry("mona"), _entry("venus", year=-50), _entry("hare")])
    assert json.loads((out / "bucket.json").read_text())["sequence"] == meta["sequence"], "a second run adds nothing"


def test_the_old_folder_moves_once_and_its_name_still_works(monkeypatch, tmp_path):
    fetch = _fetch(monkeypatch, tmp_path / "library")
    monkeypatch.setattr(fetch, "OLD_OUT", tmp_path / "_artworks")
    (tmp_path / "_artworks").mkdir()
    (tmp_path / "_artworks" / "mona.jpg").write_bytes(b"picture")
    fetch.move_old_folder()
    fetch.move_old_folder()
    assert (tmp_path / "library" / "mona.jpg").exists() and (tmp_path / "_artworks").is_symlink()
    assert (tmp_path / "_artworks" / "mona.jpg").read_bytes() == b"picture"
