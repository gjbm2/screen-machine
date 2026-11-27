"""smsound.eq: each output's graphic EQ, kept beside its latency."""
from pathlib import Path

import pytest

from smsound import eq as EQ


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("smsound.latency.path", lambda dep=None: Path(tmp_path) / "latency.json")


def test_unset_is_flat_and_a_preset_sets_its_gains(store):
    assert EQ.describe("screen:vj")["gains"] == [0.0] * len(EQ.BANDS)
    d = EQ.set_eq("screen:vj", preset="party")
    assert d["preset"] == "party" and d["gains"] == [float(x) for x in EQ.PRESETS["party"][1]]
    assert EQ.gains("screen:vj") == d["gains"]


def test_gains_by_hand_are_clamped_and_name_a_matching_preset(store):
    assert EQ.set_eq("screen:vj", gains_db=[0, 0, 0, 0, 0, 0, 99])["gains"][-1] == EQ.RANGE_DB
    assert EQ.set_eq("screen:vj", gains_db=list(EQ.PRESETS["bass"][1]))["preset"] == "bass"


def test_flat_leaves_nothing_stored_and_bad_input_is_refused(store):
    EQ.set_eq("screen:vj", preset="bass")
    EQ.set_eq("screen:vj", preset="flat")
    assert EQ.describe("screen:vj")["preset"] == "flat"
    with pytest.raises(ValueError):
        EQ.set_eq("screen:vj", gains_db=[1, 2])
    with pytest.raises(ValueError):
        EQ.set_eq("none", preset="bass")


def test_only_players_that_shape_the_sound_apply_it():
    assert EQ.describe("screen:vj")["applies"] is True
    assert EQ.describe("sonos:living")["applies"] is False


def test_an_unmeasured_output_uses_the_browsers_reported_latency(store):
    from smsound import latency as L
    assert L.effective_ms("screen:vj") is None
    L.set_browser("screen:vj", 180)
    assert L.effective_ms("screen:vj") == 180
    assert L.set_browser("screen:vj", 182) is None          # under 5 ms: not written
    L.set_manual("screen:vj", 120)
    assert L.effective_ms("screen:vj") == 120               # a measurement wins
