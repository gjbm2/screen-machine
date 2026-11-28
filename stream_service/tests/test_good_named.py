"""POST /good with what the person was looking at, and its undo (sessions.Session.good)."""
from types import SimpleNamespace

from stream_service.sessions import Session


def fake(signals):
    spec = SimpleNamespace(name="tunnel-blue", source="tunnel")
    return SimpleNamespace(
        id="s-1", direction_effective="calm",
        mgr=SimpleNamespace(taste_signal=lambda *a: signals.append(a), presets=SimpleNamespace(resolve_scene=lambda x: spec)),
        scene_spec=SimpleNamespace(name="other", source="other"), fx=SimpleNamespace(stack=[]),
        _require=lambda state: None)


def test_a_named_scene_is_credited_not_the_one_on_screen():
    got = []
    r = Session.good(fake(got), {"scene": {"preset": "tunnel-blue"}, "effects": ["kaleido"]})
    assert r["scene"] == "tunnel-blue" and r["effects"] == ["kaleido"] and not r["undo"]
    assert [a[:2] for a in got] == [("good", "tunnel"), ("good", "scene:tunnel-blue"), ("good", "fx:kaleido"),
                                    ("good", "pair:tunnel-blue|kaleido")]


def test_undo_takes_it_back():
    got = []
    r = Session.good(fake(got), {"scene": "tunnel-blue", "undo": True})
    assert r["undo"] and {a[0] for a in got} == {"ungood"}


def test_no_body_is_what_is_on_screen():
    got = []
    assert Session.good(fake(got))["scene"] == "other"
