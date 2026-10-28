"""direction.fx.favour: a mode's own effects (tagged, e.g. modes: [disco]) get at least `share` of the pick."""
import pytest

from stream_service.director import ParamError, validate_direction_fx
from stream_service.fx_director import favoured


def test_favoured_lifts_tagged_to_share_and_keeps_order():
    tags = [{"modes": ["disco"]}, {"moods": ["calm"]}, {"moods": ["dark"]}]
    w = favoured([0.1, 1.0, 3.0], tags, {"tags": ["modes:disco"], "share": 0.3})
    assert abs(w[0] / sum(w) - 0.3) < 1e-9 and w[1:] == [1.0, 3.0]
    assert favoured([5.0, 1.0, 3.0], tags, {"tags": ["disco"], "share": 0.3}) == [5.0, 1.0, 3.0]   # never lowered
    assert favoured([0.1, 1.0], tags[:2], {"tags": ["modes:party"], "share": 0.3}) == [0.1, 1.0]   # no match
    assert favoured([0.1, 1.0], tags[:2], None) == [0.1, 1.0]


def test_validate_favour():
    assert validate_direction_fx({"favour": {"tags": ["modes:disco"]}})["favour"] == {"tags": ["modes:disco"],
                                                                                      "share": 0.3}
    assert validate_direction_fx({})["favour"] is None
    for bad in ({"tags": []}, {"tags": "disco"}, {"tags": ["x"], "weight": 2}, ["x"]):
        with pytest.raises(ParamError):
            validate_direction_fx({"favour": bad})
