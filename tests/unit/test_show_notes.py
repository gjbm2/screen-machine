"""The owner's verdict on a song's show (routes/show_notes.py): one line in the art director's feedback file, as the
overnight eval reads it (tools/room/ad_eval/run.py notes(): a song-wide note scored by the song's overall)."""
from __future__ import annotations

import json

from flask import Flask

import routes.show_notes as SN


def test_a_song_verdict_is_saved_where_the_eval_reads_it(tmp_path, monkeypatch):
    f = tmp_path / "ad_feedback.jsonl"
    f.write_text(json.dumps({"song": "kold", "t_s": 30.0, "verdict": "bad", "dimension": "contour", "words": "x"}) + "\n")
    monkeypatch.setattr(SN, "path", lambda: f)
    app = Flask(__name__)
    app.register_blueprint(SN.show_notes_api, url_prefix="/api")
    c = app.test_client()
    assert c.post("/api/show-notes", json={"song": "k1", "verdict": "meh", "words": "x"}).status_code == 400
    assert c.post("/api/show-notes", json={"song": "k1", "verdict": "bad", "words": "  "}).status_code == 400
    r = c.post("/api/show-notes", json={"song": "k1", "title": "Song · Artist", "verdict": "bad",
                                        "words": "far too busy for a ballad; the screens never settled"})
    assert r.status_code == 200
    n = r.get_json()
    assert n["scope"] == "song" and n["dimension"] == "overall" and n["t_s"] is None and n["by"] == "owner"
    lines = [json.loads(x) for x in f.read_text().splitlines()]
    assert lines[0]["song"] == "kold" and lines[-1]["words"].startswith("far too busy")   # appended, the old kept
    got = c.get("/api/show-notes?song=k1").get_json()["notes"]
    assert len(got) == 1 and got[0]["verdict"] == "bad"
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("ad_eval_run", pathlib.Path("tools/room/ad_eval/run.py"))
    src = pathlib.Path("tools/room/ad_eval/run.py").read_text()
    assert 'n.get("scope") == "song"' in src                  # the eval scores it as a song-wide case
