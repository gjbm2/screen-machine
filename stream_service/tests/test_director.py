"""The auto-director: tag matching, pools, dwell, triggers and determinism (director.py, tags.py; no GPU)."""
from __future__ import annotations

import pathlib

import pytest

from stream_service.director import (AUTO_DEFAULTS, DIRECTION_DEFAULTS, Director, default_dwell, resolve_pool,
                                     validate_auto, validate_direction)
from stream_service.inputs import Snapshot
from stream_service.plugins import default_registry
from stream_service.plugins.base import ParamError
from stream_service.presets import Presets
from stream_service.tags import matches_filter, scene_weight, transition_weight

REPO_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "live"


@pytest.fixture(scope="module")
def presets():
    return Presets(REPO_CONFIG, default_registry(), plugin_presets=False)   # config/live only: known pools


def direction(**kw):
    return validate_direction(kw)


def snap(**stable) -> Snapshot:
    return Snapshot(0.0, 0.0, {}, {}, True, None, None, 0.0, None, False, 1.5, stable)


def test_weight_is_a_product_of_one_factor_per_descriptor():
    tags = {"energy": [0.4, 0.9], "moods": ["bright", "euphoric"], "weight": 0.8}
    assert scene_weight(tags, direction(energy=0.6, moods=["bright"])) == pytest.approx(0.8)
    # energy is graded: 0.1 below the band = 2/3, 0.3 or more below = the floor 0.1
    assert scene_weight(tags, direction(energy=0.3, moods=["bright"])) == pytest.approx(0.8 * (1 - 0.1 / 0.3))
    assert scene_weight(tags, direction(energy=0.1, moods=["bright"])) == pytest.approx(0.8 * 0.1)
    assert scene_weight(tags, direction(energy=0.1, moods=["calm"])) == pytest.approx(0.8 * 0.1 * 0.3)
    # untagged keys and empty / null direction values count as matched
    assert scene_weight(tags, direction(energy=0.5, genre="jazz", palette_family="warm", valence=0.1)) \
        == pytest.approx(0.8)
    t = {"transition_style": ["wild"], "energy": [0.6, 1.0], "moods": ["glitchy"], "weight": 1.0}
    assert transition_weight(t, direction(energy=0.8, transition_style="wild")) == 1.0
    assert transition_weight(t, direction(energy=0.8, transition_style="gentle")) == pytest.approx(0.3)
    assert transition_weight(t, direction(energy=0.8, moods=["calm"])) == pytest.approx(0.6)   # softer for transitions


def test_pools(presets):
    scenes = resolve_pool("all", presets.scenes)
    assert "black" not in scenes and "fractal-seahorse" in scenes                # untagged: manual only
    assert "fractal-seahorse" not in resolve_pool({"tags": {"heavy": False}}, presets.scenes)
    assert resolve_pool(["warp-cells", "nope"], presets.scenes) == ["warp-cells"]
    assert "cut" not in resolve_pool("all", presets.transitions)
    assert matches_filter({"moods": ["calm", "dark"]}, {"moods": ["dark"]})
    assert not matches_filter({"moods": ["calm"]}, {"moods": "dark"})


def test_direction_and_auto_validation():
    d = validate_direction({"energy": 2, "moods": ["calm"]}, {"transition_style": ["gentle", "lively", "wild"]})
    assert d["energy"] == 1.0 and d["pace"] == 0.5 and d["transition_style"] is None   # whole object, neutral
    with pytest.raises(ParamError):
        validate_direction({"transition_style": "frantic"}, {"transition_style": ["gentle", "lively", "wild"]})
    assert "arousal" not in validate_direction({"arousal": 0.7, "energy": 0.4})     # unknown fields: ignored (SHOW_ENGINE)
    a = validate_auto({"dwell_s": [6, 12], "follow_dmx": ["effect"]})
    assert a["enabled"] is True and a["dwell_s"] == [6.0, 12.0] and a["snap"] == "beat"
    for bad in ({"paused": True}, {"dwell_s": [10, 5]}, {"snap": "phrase"}, {"dwell_bars": [8, 16]},
                {"follow_dmx": ["colour"]}):
        with pytest.raises(ParamError):
            validate_auto(bad)


def test_default_dwell_is_log_linear_in_change_rate():
    assert default_dwell(0.0) == pytest.approx((120.0, 240.0))
    assert default_dwell(1.0) == pytest.approx((15.0, 30.0))
    lo, hi = default_dwell(0.5)
    assert lo == pytest.approx((120 * 15) ** 0.5) and hi == pytest.approx((240 * 30) ** 0.5)


def test_triggers_only_when_armed_and_after_min_dwell():
    d = Director(validate_auto({"dwell_s": [6, 12]}), dict(DIRECTION_DEFAULTS))
    d.scene_done(100.0)
    assert d.due(110.0) is None                                     # not armed (not live)
    d.arm(True)
    d.observe(snap(effect=1, variant=0), True)                      # first fresh frame: the baseline
    assert d.due(103.0) is None
    d.observe(snap(effect=2, variant=0), True)                      # a change inside min dwell: latched
    assert d.due(105.9) is None
    reason, detail = d.due(106.0)
    assert reason == "dmx_change" and "effect 1→2" in detail
    d.note_cue("warp-cells", "melt-2s", snap(effect=2, variant=0))   # a cue re-baselines and clears the latch
    d.scene_done(108.0)
    assert d.due(115.0) is None
    assert d.due(120.0)[0] == "max_dwell"
    # disarmed: triggers are discarded, not queued
    d.arm(False)
    d.observe(snap(effect=5, variant=0), True)
    d.arm(True)
    d.observe(snap(effect=5, variant=0), True)
    assert d.latched is None


def test_follow_dmx_can_be_off():
    d = Director(validate_auto({"dwell_s": [6, 12], "follow_dmx": []}), dict(DIRECTION_DEFAULTS))
    d.arm(True)
    d.scene_done(0.0)
    d.observe(snap(effect=1), True)
    d.observe(snap(effect=2), True)
    assert d.due(7.0) is None and d.due(12.0)[0] == "max_dwell"


def test_picks_are_seeded_novel_and_match_direction(presets):
    def run(seed):
        d = Director(validate_auto({"seed": seed}), direction(energy=0.9, transition_style="wild"))
        out, cur = [], None
        for _ in range(12):
            cur = d.pick_scene(presets.scenes, cur, 0.0)[0]
            t = d.pick_transition(presets.transitions)[0]
            d.note_cue(cur, t, None)
            out.append((cur, t))
        return out
    a, b = run(7), run(7)
    assert a == b and a != run(8)                                    # same seed, same sequence
    assert all(a[i][0] != a[i + 1][0] for i in range(len(a) - 1))   # never the current scene again
    assert all(a[i][0] not in (a[i - 1][0], a[i - 2][0]) for i in range(2, len(a)))   # avoid_repeat 2
    assert all(a[i][1] != a[i + 1][1] for i in range(len(a) - 1))   # a different transition each time
    wild = {n for n, p in presets.transitions.items() if "wild" in (p.get("tags") or {}).get("transition_style", [])}
    assert sum(t in wild for _, t in a) >= 6                         # the style is honoured (x0.3 otherwise; pool grows with plug-ins)


def test_quarantine_and_empty_pool(presets):
    d = Director(validate_auto({"pool": ["warp-cells", "warp-smoke"], "seed": 1}), dict(DIRECTION_DEFAULTS))
    d.quarantine_preset("warp-cells", 0.0)
    assert {d.pick_scene(presets.scenes, None, 1.0)[0] for _ in range(10)} == {"warp-smoke"}
    assert d.pick_scene(presets.scenes, None, 700.0)[0] in ("warp-cells", "warp-smoke")   # 10 min later
    d.set_auto(validate_auto({"pool": {"tags": {"moods": ["nonexistent"]}}}))
    assert d.pick_scene(presets.scenes, None, 0.0) is None
    assert AUTO_DEFAULTS["snap"] == "beat"


def test_while_the_art_director_drives_only_its_change_cuts_and_at_once():
    """Owner, 26 Nov: "there should be one controlling visual logic -- the a.d. has to have dominion"."""
    import time
    d = Director(validate_auto({"dwell_s": [6, 12]}), dict(DIRECTION_DEFAULTS))
    d.scene_done(100.0)
    d.arm(True)
    d.ad_until = time.monotonic() + 60.0                            # room.direction says cues: director, and is fresh
    assert d.ad_drives(time.monotonic()) and d.prepare_due(100.5)   # the next pick is prepared at once
    assert d.due(200.0) is None                                     # never on its own max dwell
    assert d.hint("the art director's change (section)", forced=True)
    reason, detail = d.due(100.5)                                   # ... and its change cuts whatever the dwell
    assert reason == "room_event" and "the art director's change" in detail
    d.note_cue("warp-cells", "melt-2s", None)
    assert d.forced is False and d.due(101.0) is None
    d.ad_until = 0.0                                                # show_service gone quiet: its own dwell again
    d.scene_done(102.0)
    assert d.due(120.0)[0] == "max_dwell"
    assert d.hint("section change") and d.due(105.0) is None        # an ordinary hint still waits for min dwell


def test_a_moves_transition_is_drawn_from_what_it_asks_for_and_fits_before_the_line():
    d = Director(validate_auto({"dwell_s": [6, 12]}), dict(DIRECTION_DEFAULTS))
    tr = {"melt-2s": {"transition": "melt", "params": {"duration_s": 2.0}, "tags": {"family": "warp"}},
          "hole-5s": {"transition": "hole", "params": {"duration_s": 5.0}, "tags": {"family": "warp"}},
          "luma-3s": {"transition": "luma", "params": {"duration_s": 3.0}, "tags": {"family": "blend"}},
          "cut": {"transition": "cut", "params": {"duration_s": 0.0}, "tags": {}}}
    for _ in range(20):
        assert d.pick_transition(tr, want=["family:warp"])[0] in ("melt-2s", "hole-5s")
        assert d.pick_transition(tr, want=["family:warp"], max_s=3.0)[0] == "melt-2s"     # it fits before the line
        assert d.pick_transition(tr, want=["cut"])[0] == "cut"                             # by name
        assert d.pick_transition(tr, want=["family:nothing"]) is not None                  # none match: its own pick
