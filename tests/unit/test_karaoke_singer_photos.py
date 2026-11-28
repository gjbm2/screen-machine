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


def test_riffs_are_queued_made_one_at_a_time_round_the_singers_and_join_the_photo_scenes(app, monkeypatch):
    import threading
    from routes.karaoke import singer_riffs as sr
    c = app.test_client()
    upload(c, "Clare", jpeg())
    upload(c, "Tom", jpeg())
    idx = sp._index()
    assert [len(r["riffs"]) for r in idx.values()] == [sr.PER_SINGER] * 2
    assert all(x["state"] == "queued" for r in idx.values() for x in r["riffs"])   # no generator yet: they wait
    order, done = [], threading.Event()

    def gen(photo, prompt):
        order.append(photo.name.split("-")[0])
        assert "likeness" in prompt.lower() and photo.is_file()
        if len(order) == 2 * sr.PER_SINGER:
            done.set()
        return jpeg(500, 500)
    monkeypatch.setattr(sr, "_thread", None)
    sr.set_backend(gen)
    try:
        assert done.wait(10)
        for _ in range(50):
            if all(x["state"] == "done" for r in sp._index().values() for x in r["riffs"]):
                break
            threading.Event().wait(0.05)
        assert order[:2] in (["clare", "tom"], ["tom", "clare"])       # everyone's first before anyone's second
        pics = sp.pictures_of(["Clare"])
        assert len(pics) == 1 + sr.PER_SINGER and {p.get("riff") for p in pics[1:]} <= set(sr.SCENES)
        assert c.get("/api/karaoke/singers/photo/" + pics[1]["path"].split("/")[-1]).status_code == 200
        c.post("/api/karaoke/singers", json={"remove": ["Clare"]})   # the riffs go with the photo
        assert not any("clare" in f.name for f in (sp.folder()).iterdir())
    finally:
        sr.backend = None


def test_the_api_backend_posts_the_photo_polls_and_fetches_the_picture(tmp_path):
    """routes/karaoke/singer_riffs.api_backend against a stand-in of docs/API.md: 202 queued, a long poll, the file."""
    import json as _json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from routes.karaoke import singer_riffs as sr
    seen = {}
    pic = jpeg(64, 64)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else _json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            seen["idem"] = self.headers.get("Idempotency-Key")
            seen["body"] = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._send(202, {"id": "gen_1", "status": "queued"})

        def do_GET(self):
            if self.path.startswith("/v1/generations/gen_1?wait="):
                base = f"http://127.0.0.1:{self.server.server_port}/v1"
                self._send(200, {"id": "gen_1", "status": "succeeded",
                                 "outputs": [{"download_url": f"{base}/generations/gen_1/outputs/0"}]})
            elif self.path == "/v1/generations/gen_1/outputs/0":
                self._send(200, pic, "image/jpeg")
            else:
                self._send(404, {"error": {"code": "not_found"}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        photo = tmp_path / "p.jpg"
        photo.write_bytes(jpeg())
        make = sr.api_backend(f"http://127.0.0.1:{srv.server_port}/v1", "smk_x_y", "auto", "none")
        assert make(photo, sr.prompt("wembley")) == pic
        b = seen["body"]
        assert seen["auth"] == "Bearer smk_x_y" and seen["idem"] and b["refiner"] == "none" and b["workflow"] == "auto"
        assert b["images"][0]["data"].startswith("data:image/jpeg;base64,") and "Wembley" in b["prompt"]
    finally:
        srv.shutdown()
