"""A session's brightness (`level`, the room's brightness.screens): parsed 0-1 and eased on the render side."""
import pytest

from stream_service.modulation import LEVEL_EASE_S, SessionRuntime
from stream_service.sessions import ParamError, parse_level


def test_parse_level_clamps_and_refuses_non_numbers():
    assert parse_level(0.4) == 0.4 and parse_level(2) == 1.0 and parse_level(-1) == 0.0
    for bad in (True, "0.5", None, float("nan")):
        with pytest.raises(ParamError):
            parse_level(bad)


def test_level_eases_to_target():
    rt = SessionRuntime(None)
    rt.begin_frame(0.0)
    rt.level_target = 0.5
    t = 0.0
    while t < LEVEL_EASE_S / 2:
        t += 1 / 60
        rt.begin_frame(t)
    assert 0.5 < rt.level < 0.7             # on its way, not a jump
    while t < LEVEL_EASE_S * 1.5:
        t += 1 / 60
        rt.begin_frame(t)
    assert abs(rt.level - 0.5) < 0.01
