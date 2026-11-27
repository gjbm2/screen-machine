"""preview.py: the rig as it would look, from this deployment's own files, never sent anywhere."""
import pytest

from show_service import preview


def _slots(d: bytes) -> int:
    return sum(1 for b in d if b)


def _make(what, colour=None):
    """A preview of the deployment the tests run on; skipped where it has no DMX lights (finsbury's are Hue)."""
    try:
        return preview.Preview(what, colour)
    except ValueError as e:
        if "no DMX lights" in str(e):
            pytest.skip("this deployment has no DMX lights")
        raise


def test_plain_light_lights_the_rig_and_never_the_hazer():
    pv = _make("plain")
    d = pv.frame()
    assert len(d) == 512 and _slots(d) > 20
    for fid in pv.hazers:
        a, n = pv.spans[fid]
        assert d[a:a + n] == pv.base[a:a + n]                       # the hazer keeps its resting bytes


def test_a_colour_and_a_scene_and_a_bad_scene():
    assert _slots(_make("colour", (0.0, 0.2, 1.0)).frame()) > 10
    some = next(iter(_make("plain").cat.scenes))
    assert len(_make(f"scene:{some}").frame()) == 512
    with pytest.raises(ValueError, match="no light scene"):
        _make("scene:no-such-scene")


def test_a_mode_set_light_holds_its_level_whatever_the_look():
    import pytest
    try:
        pv = preview.Preview("plain", None)
    except ValueError:
        pytest.skip("no DMX lights in this deployment")
    one = next((fid for fid, sp in pv.spans.items() if sp[1] == 1), None)
    if one is None:
        pytest.skip("no one-channel light here")
    a = pv.spans[one][0]
    assert preview.Preview("plain", None, sets={one: 0.0}).frame()[a] == 0
    assert preview.Preview("plain", None, sets={one: 0.5}).frame()[a] == 128
