"""routes.screen_notes: a comment on what a screen showed, kept with the song playing then."""
import flask
import pytest

from routes import screen_notes as N


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "path", lambda: tmp_path / "screen_notes.jsonl")
    monkeypatch.setattr(N, "_song", lambda: {"id": "k1", "title": "Rehab", "t_s": 42.0})
    app = flask.Flask(__name__)
    app.register_blueprint(N.screen_notes_api, url_prefix="/api")
    return app.test_client()


def test_a_comment_is_kept_newest_first_with_its_song(client):
    r = client.post("/api/screen-notes", json={"screen": "vj", "key": "scene/tunnel", "label": "Tunnel", "when": "was",
                                               "words": "too busy for the verse"})
    assert r.status_code == 200 and r.json["kind"] == "scene" and r.json["song"]["t_s"] == 42.0
    client.post("/api/screen-notes", json={"screen": "tv", "key": "effect/kaleido", "words": "lovely"})
    notes = client.get("/api/screen-notes").json["notes"]
    assert [n["words"] for n in notes] == ["lovely", "too busy for the verse"]
    assert notes[0]["kind"] == "effect" and notes[0]["when"] == "now"
    assert [n["screen"] for n in client.get("/api/screen-notes?screen=vj").json["notes"]] == ["vj"]


def test_words_and_key_are_needed(client):
    assert client.post("/api/screen-notes", json={"key": "scene/x", "words": "  "}).status_code == 400
