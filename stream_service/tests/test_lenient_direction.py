"""show_service's room.direction: a bad field takes its default, the rest of the direction still applies (b8, 17 Oct)."""
from stream_service.sessions import lenient_direction

VOCAB = {"transition_style": ["cut", "dissolve"]}


def test_good_direction_passes_whole():
    d, dropped = lenient_direction({"energy": 0.8, "transition_style": "cut", "arousal": 0.4}, VOCAB)
    assert dropped == [] and d["energy"] == 0.8 and d["transition_style"] == "cut"


def test_bad_fields_drop_alone():
    body = {"energy": 0.9, "pace": 0.2, "moods": ["calm"], "transition_style": "wobble",
            "fx": {"density": 0.7, "sparkle": 1}, "valence": 0.3}
    d, dropped = lenient_direction(body, VOCAB)
    assert sorted(dropped) == ["fx", "transition_style"]
    assert d["energy"] == 0.9 and d["pace"] == 0.2 and d["moods"] == ["calm"]
    assert d["transition_style"] is None and d["fx"]["density"] == 0.5
