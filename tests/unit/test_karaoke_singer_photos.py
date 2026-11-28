"""A singer's own photo (routes/karaoke/singer_photos.py; owner, 28 Nov): uploaded from a phone, upright, kept per
name, served, on the overlays' and the photo scenes' lists, and gone with the name or a new session."""
from __future__ import annotations

import io

import pytest
from flask import Flask
from PIL import Image

from routes.karaoke import api, singer_photos as sp
from routes.karaoke.store import Store


def jpeg(w=400, h=300, orientation=None) -> bytes:
    im = Image.new("RGB", (w, h), (200, 30, 30))
    buf = io.BytesIO()
    if orientation:
        ex = Image.Exif()
        ex[0x0112] = orientation
        im.save(buf, "JPEG", exif=ex)
    else:
        im.save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def app(tmp_path, monkeypatch):
    st = Store(tmp_path / "state.json")
    monkeypatch.setattr(api, "store", lambda: st)
    monkeypatch.setattr(api.smconfig, "enabled", lambda cap: True)
    monkeypatch.setattr(sp, "folder", lambda: tmp_path / "singers")
    monkeypatch.setattr(sp, "_find_faces", lambda key, file: None)       # no AI in tests
    a = Flask(__name__)
    a.register_blueprint(api.karaoke_api, url_prefix="/api")
    a.st = st
    return a


def upload(c, name, data):
    return c.post("/api/karaoke/singers/photo", data={"name": name, "photo": (io.BytesIO(data), "p.jpg")},
                  content_type="multipart/form-data")


def test_a_photo_is_kept_upright_served_and_listed(app):
    c = app.test_client()
    r = upload(c, "Clare", jpeg(400, 300, orientation=6))                # a phone's photo stored on its side
    assert r.status_code == 200
    u = r.get_json()["photos"]["Clare"]
    got = c.get(u)
    assert got.status_code == 200 and Image.open(io.BytesIO(got.data)).size == (300, 400)
    assert "Clare" in app.st.singers()                                   # the name joins tonight's
    assert sp.photos_of(["clare", "Tom"]) == {"clare": u}               # any spelling; names without one left out
    pics = sp.pictures_of(["Clare"])
    assert len(pics) == 1 and pics[0]["kind"] == "singer" and pics[0]["w"] == 300


def test_a_new_photo_replaces_the_old_one(app):
    c = app.test_client()
    u1 = upload(c, "Clare", jpeg()).get_json()["photos"]["Clare"]
    u2 = upload(c, "Clare", jpeg(200, 200)).get_json()["photos"]["Clare"]
    assert u1 != u2 and c.get(u1).status_code == 404 and c.get(u2).status_code == 200


def test_not_a_photo_and_missing_name_are_refused(app):
    c = app.test_client()
    assert upload(c, "Clare", b"not an image").status_code == 400
    assert upload(c, "", jpeg()).status_code == 400
    assert c.get("/api/karaoke/singers/photo/..%2Fstate.json").status_code == 404


def test_the_photo_goes_with_its_name_and_with_a_new_session(app):
    c = app.test_client()
    u = upload(c, "Clare", jpeg()).get_json()["photos"]["Clare"]
    upload(c, "Tom", jpeg())
    c.post("/api/karaoke/singers", json={"remove": ["clare"]})
    assert c.get(u).status_code == 404 and list(sp.photos()) == ["Tom"]
    assert c.delete("/api/karaoke/singers/photo?name=Tom").get_json()["photos"] == {}
    upload(c, "Ann", jpeg())
    c.post("/api/karaoke/session", json={"op": "new"})
    assert sp.photos() == {}
