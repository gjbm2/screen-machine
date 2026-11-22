"""Favouriting a picture in a bucket (routes/bucket_api.py), through the real routes on a throwaway bucket."""
import json

import pytest
from flask import Flask

import routes.bucket_api as bucket_api
import routes.bucketer as bucketer

PIC = "20261122-133124-c5375f6a.jpg"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(bucketer, "BASE_OUTPUT", tmp_path)
    monkeypatch.setattr(bucket_api, "_load_json_once", lambda *a, **k: [{"id": "north-screen", "has_bucket": True}])
    bucket = tmp_path / "north-screen"
    bucket.mkdir()
    (bucket / PIC).write_bytes(b"")
    (bucket / "bucket.json").write_text(json.dumps({"sequence": [{"file": PIC}], "favorites": []}))
    app = Flask(__name__)
    app.register_blueprint(bucket_api.buckets_bp, url_prefix="/api")
    return app.test_client()


def favourites(tmp_path):
    return json.loads((tmp_path / "north-screen" / "bucket.json").read_text())["favorites"]


def test_tag_untag_tag_again(client, tmp_path):
    assert client.post(f"/api/buckets/north-screen/favorite/{PIC}").status_code == 200
    assert favourites(tmp_path) == [PIC]
    assert client.delete(f"/api/buckets/north-screen/favorite/{PIC}").status_code == 200
    assert favourites(tmp_path) == []
    assert client.post(f"/api/buckets/north-screen/favorite/{PIC}").status_code == 200
    assert favourites(tmp_path) == [PIC]


def test_tagging_twice_keeps_one_entry_so_one_untag_clears_it(client, tmp_path):
    client.post(f"/api/buckets/north-screen/favorite/{PIC}")
    client.post(f"/api/buckets/north-screen/favorite/{PIC}")
    assert favourites(tmp_path) == [PIC]
    client.delete(f"/api/buckets/north-screen/favorite/{PIC}")
    assert favourites(tmp_path) == []


def test_a_name_not_in_the_bucket_is_refused_and_changes_nothing(client, tmp_path):
    assert client.post(f"/api/buckets/north-screen/favorite/{PIC}%23dup").status_code == 404
    assert favourites(tmp_path) == []
