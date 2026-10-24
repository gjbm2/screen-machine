"""A scene drawn from the piano's notes is nearly never auto-picked with no MIDI, and picked as usual while it plays."""
from stream_service.director import AUTO_DEFAULTS, DIRECTION_DEFAULTS, Director


def _share(age):
    d = Director(dict(AUTO_DEFAULTS, seed=7, avoid_repeat=0), dict(DIRECTION_DEFAULTS, moods=[]))
    d.listens = lambda src: ("notes",) if src == "piano_waterfall" else ()
    d.notes_age = lambda: age
    scenes = {"waterfall": {"source": "piano_waterfall", "tags": {"family": "x"}}, "plasma": {"source": "plasma", "tags": {"family": "x"}}}
    n = sum(d.pick_scene(scenes, None, 0.0)[0] == "waterfall" for _ in range(400))
    return n / 400


def test_notes_scene_rare_without_midi():
    assert _share(None) < 0.12           # never played
    assert _share(600.0) < 0.12          # played long ago


def test_notes_scene_as_usual_while_playing():
    assert 0.35 < _share(5.0) < 0.65
