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


def test_notes_scene_never_without_midi():
    assert _share(None) == 0.0


def test_rotation_floor_keeps_order_and_spreads():
    import random

    from stream_service.director import effective_n, spread, weighted_pick
    w = [8.0] + [0.01] * 99 + [0.0]
    s = spread(w, 16.0)
    assert effective_n(s) >= 16.0 - 1e-6 and s[0] > s[1] > 0 and s[-1] == 0.0
    names = [f"s{i}" for i in range(len(w))]
    rng = random.Random(3)
    picks = [weighted_pick(names, lambda n: w[int(n[1:])], rng, (), 16.0)[0] for _ in range(2000)]
    assert len(set(picks)) > 40 and "s100" not in picks and picks.count("s0") > picks.count("s1")
