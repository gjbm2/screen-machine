"""Light mask: the page's black-then-warm-multiply as per-channel gains, eased linearly."""
import pytest

from stream_service.mask import IDENTITY, MaskRamp, parse_mask
from stream_service.plugins.base import ParamError


def test_gains_match_the_pages_black_then_warm_multiply():
    gains, dur, norm = parse_mask({"brightness": 0.8, "warm_hex": "ffb070", "warm_alpha": 0.25, "transition_ms": 300})
    assert dur == 0.3 and norm["warm_hex"] == "#ffb070"
    assert gains == pytest.approx(tuple(0.8 * (0.75 + 0.25 * c / 255) for c in (0xff, 0xb0, 0x70)))
    assert parse_mask(None)[0] == IDENTITY and parse_mask({})[0] == IDENTITY


@pytest.mark.parametrize("bad", [{"brightness": -0.1}, {"warm_alpha": True}, {"warm_hex": "orange"},
                                 {"transition_ms": -1}, {"opacity": 1}, [1]])
def test_bad_masks_are_400(bad):
    with pytest.raises(ParamError):
        parse_mask(bad)


def test_ramp_eases_from_where_it_is():
    r = MaskRamp()
    r.set((0.0, 0.0, 0.0), 1.0, now=10.0)
    assert r.at(10.5) == pytest.approx((0.5, 0.5, 0.5))
    r.set((1.0, 1.0, 1.0), 1.0, now=10.5)          # a new target mid-ramp starts from the current gains
    assert r.at(10.5) == pytest.approx((0.5, 0.5, 0.5)) and r.at(11.5) == (1.0, 1.0, 1.0)
