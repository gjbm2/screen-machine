"""An event remembers where it was thrown (_event.scope), so a schedule can tell a broadcast to its room from a throw
at this screen alone; and _current_image is the still on the screen, not its clip (routes/scheduler_utils.py)."""
from datetime import datetime

import routes.scheduler_utils as su


def test_an_event_keeps_its_scope(monkeypatch):
    monkeypatch.setattr(su, "get_destinations_for_group", lambda scope: ["t-north", "t-south"] if scope == "t-room" else [scope])
    su.throw_event(scope="t-room", key="T_scope_test", ttl="60s")
    su.throw_event(scope="t-north", key="T_scope_test2", ttl="60s")
    now = datetime.now()
    assert su.pop_next_event("t-south", "T_scope_test", now).scope == "t-room"
    assert su.pop_next_event("t-north", "T_scope_test2", now).scope == "t-north"


def test_current_image_is_the_still_not_its_clip(monkeypatch, tmp_path):
    import routes.bucketer as bk
    import routes.publisher as pub
    (tmp_path / "a.jpg").write_bytes(b"x")
    (tmp_path / "a-clip.mp4").write_bytes(b"x")
    monkeypatch.setattr(bk, "bucket_path", lambda d: tmp_path)
    monkeypatch.setattr(pub, "get_published_info", lambda d: {"published": "a-clip.mp4", "meta": {"still": "a.jpg"}})
    assert su.process_jinja_template("{{ _current_image }}", {"vars": {}}, "north-screen") == str(tmp_path / "a.jpg")
    monkeypatch.setattr(pub, "get_published_info", lambda d: {"published": "v.mp4", "meta": {}})
    (tmp_path / "v.mp4").write_bytes(b"x")
    assert su.process_jinja_template("{{ _current_image }}", {"vars": {}}, "north-screen") == str(tmp_path / "v.mp4")
