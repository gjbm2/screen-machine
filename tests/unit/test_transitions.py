"""A scheduled picture's transition (routes/transitions.py): the drawn length, the fallback to a plain cross-fade,
and both pictures' sidecars listing it."""
import json

from routes import transitions as tr


def test_the_draw_is_about_four_seconds_in_h3_lengths():
    n = tr._frames(tr.DRAW_S)
    assert (n - 5) % 17 == 0 and 3.5 <= n / 24 <= 4.5


def test_nothing_on_the_screen_means_no_transition(monkeypatch):
    import routes.clips
    monkeypatch.setattr(routes.clips, "published_still", lambda screen: None)
    assert tr.before_publish("north-screen", "/nowhere/b.jpg", "job_test_1") is None


def test_any_failure_means_no_transition_and_never_raises(monkeypatch, tmp_path):
    import routes.clips
    monkeypatch.chdir(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "north-screen.jpg").write_bytes(b"not really a jpeg")
    monkeypatch.setattr(routes.clips, "published_still", lambda screen: "a.jpg")

    def boom(*a, **k):
        raise RuntimeError("the writer is down")
    monkeypatch.setattr(tr, "write", boom)
    assert tr.before_publish("north-screen", str(tmp_path / "output" / "north-screen.jpg"), "job_test_2") is None


def test_both_pictures_list_the_transition(monkeypatch, tmp_path):
    import routes.bucketer
    monkeypatch.setattr(routes.bucketer, "bucket_path", lambda screen: tmp_path)
    (tmp_path / "a.jpg").write_bytes(b"a")
    (tmp_path / "a.jpg.json").write_text(json.dumps({"prompt": "a lighthouse"}))
    (tmp_path / "b.jpg").write_bytes(b"b")
    made = {"file": "transitions/x.mp4", "url": "/output/north-screen/transitions/x.mp4", "from": "a.jpg"}
    tr.note_published("north-screen", "b.jpg", made)
    a = json.loads((tmp_path / "a.jpg.json").read_text())
    b = json.loads((tmp_path / "b.jpg.json").read_text())
    assert a["prompt"] == "a lighthouse"
    assert a["transitions"] == b["transitions"] == [{**made, "to": "b.jpg"}]
    tr.note_published("north-screen", "b.jpg", made)            # once each, however often it's noted
    assert len(json.loads((tmp_path / "b.jpg.json").read_text())["transitions"]) == 1
