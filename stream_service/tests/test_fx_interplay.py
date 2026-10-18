"""Scenes and effects together (docs/SCENE_FX_INTERPLAY.md): the interplay arithmetic and the FX director's use of it."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
import yaml

from stream_service import fx_director as F
from stream_service.fx_director import FxDirector
from stream_service.fx_interplay import Interplay
from stream_service.presets import EffectSpec

CFG = {
    "level_min": 0.35, "multiplier_range": [0.25, 1.5], "tag_prior": 0.5, "refit_below": 0.5,
    "scene_props": {"legible": ["live_score"]},
    "roles": {"texture": {"busy": [1.25, 0.35], "legible": [1.0, 0.5]}, "light": {"brightness": [1.25, 0.45]},
              "reshape": {"symmetric": [1.0, 0.4]}, "trails": {"motion": [0.7, 1.15, 0.45]}},
    "pairs": {"reshape+reshape": 0.5, "trails+corrupt": {"techno": 1.0, "default": 0.6}},
    "load": {"scene": 0.5, "by_energy": [[0.0, 0.25], [1.0, 0.95]], "genre_bias": {"techno": 0.15}},
    "genre_groups": {"ambient": ["ambient"], "techno": ["techno", "electronic"], "house": ["house", "electronic"]},
    "roles_by_music": {"default": {}, "ambient": {"corrupt": 0.3, "light": 1.2}, "techno": {"corrupt": 1.2}},
    "listens": {"notes": [0.15, 3.0], "fft": [0.3, 1.5]},
}
ROLES = {"halftone": "texture", "bloom": "light", "kaleido": "reshape", "warp": "reshape", "echo": "trails",
         "mosh": "corrupt", "grade": "grade"}
SCENES = {"fractal": {"busy_pct": 0.95, "motion_pct": 0.5, "brightness_pct": 0.5, "symmetric": 0.95, "plugin": "frac"},
          "ink": {"busy_pct": 0.1, "motion_pct": 0.5, "brightness_pct": 0.1, "symmetric": 0.1, "plugin": "ink"},
          "neon": {"busy_pct": 0.5, "motion_pct": 0.05, "brightness_pct": 0.95, "symmetric": 0.2, "plugin": "neon"},
          "score": {"busy_pct": 0.3, "motion_pct": 0.2, "brightness_pct": 0.8, "plugin": "live_score"}}
EFFECTS = {n: {"change_pct": 0.5} for n in ROLES} | {"mosh": {"change_pct": 0.9}}
PRESETS = {n: {"effect": n, "stage": "stylise", "tags": {"weight": 1.0}} for n in ROLES}


@pytest.fixture
def looks(tmp_path):
    (tmp_path / "fx_interplay.yaml").write_text(yaml.safe_dump(CFG))
    (tmp_path / "effect_roles.generated.json").write_text(json.dumps({"roles": {k: {"role": v} for k, v in ROLES.items()}}))
    (tmp_path / "look_stats.generated.json").write_text(json.dumps({"scenes": SCENES, "effects": EFFECTS}))
    return Interplay(tmp_path)


def scene(n):
    return n, {"source": SCENES[n]["plugin"]}


def fit(looks, eff, sc, others=(), **direction):
    return looks.fit(eff, PRESETS[eff], scene(sc), [(o, o) for o in others], {"energy": 0.5, **direction})


def test_the_scene_shades_each_role(looks):
    assert fit(looks, "halftone", "ink").mult > 2.5 * fit(looks, "halftone", "fractal").mult    # halftone of detail
    assert fit(looks, "bloom", "ink").mult > 2 * fit(looks, "bloom", "neon").mult         # glow on bright blows out
    assert fit(looks, "kaleido", "fractal").mult < fit(looks, "kaleido", "ink").mult      # already symmetric
    assert fit(looks, "echo", "ink").mult > fit(looks, "echo", "neon").mult               # middling motion is best
    assert fit(looks, "halftone", "score").parts["scene"] < fit(looks, "halftone", "neon").parts["scene"]  # legible
    assert fit(looks, "grade", "fractal").mult == 1.0                                      # no row: no opinion


def test_pairs_and_music(looks):
    assert fit(looks, "warp", "ink", others=["kaleido"]).parts["pair"] == pytest.approx(0.5)
    assert fit(looks, "mosh", "ink", others=["echo"], genre="techno").parts["pair"] == pytest.approx(1.0)
    assert fit(looks, "mosh", "ink", others=["echo"], genre="ambient").parts["pair"] == pytest.approx(0.6)
    assert fit(looks, "mosh", "ink", genre="ambient").parts["music"] == pytest.approx(0.3)
    assert fit(looks, "mosh", "ink", genres={"techno": 0.5, "ambient": 0.5}).parts["music"] == pytest.approx(0.75)
    # a word in two groups counts half to each; a word in no group: no opinion
    assert fit(looks, "mosh", "ink", genre="electronic").parts["music"] == pytest.approx((1.2 + 1.0) / 2)
    assert fit(looks, "mosh", "ink", genre="polka").parts["music"] == 1.0


def test_the_level_follows_the_budget_and_the_scene(looks):
    quiet_busy = fit(looks, "mosh", "fractal", energy=0.1)
    assert quiet_busy.level == pytest.approx(0.35) and quiet_busy.capped_by == "budget"     # floor, never off
    loud_sparse = fit(looks, "mosh", "ink", energy=0.9, genre="techno")
    assert loud_sparse.level > 0.9
    assert fit(looks, "halftone", "fractal", energy=0.95).capped_by == "scene"
    two = fit(looks, "warp", "ink", others=["mosh"], energy=0.6)
    assert two.level < fit(looks, "warp", "ink", energy=0.6).level                           # the other effect uses budget


def test_unknown_things_have_no_say(looks):
    f = looks.fit("x", {"effect": "unknown"}, ("nowhere", None), [], {"energy": 0.5})
    assert f.mult == 1.0 and f.level == 1.0 and f.role is None


def test_the_table_reloads(looks, tmp_path):
    before = fit(looks, "mosh", "ink", genre="ambient").parts["music"]
    cfg = dict(CFG, roles_by_music={"ambient": {"corrupt": 0.5}})
    (tmp_path / "fx_interplay.yaml").write_text(yaml.safe_dump(cfg))
    looks.reload_if_changed(force=True)
    assert before == pytest.approx(0.3) and fit(looks, "mosh", "ink", genre="ambient").parts["music"] == 0.5


def test_an_effect_that_draws_what_it_hears_follows_what_there_is(looks):
    looks.listens_of = {"notes_fx": ("notes",), "bars": ("fft",), "both": ("notes", "fft")}.get
    entry = lambda pl: {"effect": pl}
    def mult(pl, heard):
        return looks.fit_in(looks.context(scene("ink"), [], {"energy": 0.5}, heard), pl, entry(pl)).mult
    assert mult("notes_fx", {"notes": 1.0}) == pytest.approx(3.0)       # the piano is playing
    assert mult("notes_fx", {"notes": 0.0}) == pytest.approx(0.15)      # an empty slot: rare, never excluded
    assert mult("notes_fx", {}) == 1.0 and mult("notes_fx", None) == 1.0   # unknown: no opinion
    assert mult("bars", {"fft": 0.5}) == pytest.approx(0.9)
    assert mult("both", {"notes": 0.0, "fft": 1.0}) == pytest.approx(1.5)   # the best of its kinds
    assert mult("halftone", {"notes": 0.0}) == pytest.approx(looks.fit("halftone", PRESETS["halftone"], scene("ink"), [],
                                                                        {"energy": 0.5}).mult)   # deaf: unchanged
    f = looks.fit_in(looks.context(scene("ink"), [], {"energy": 0.5}, {"notes": 0.0}), "notes_fx", entry("notes_fx"))
    assert "heard x0.15" in f.why()


# -- the FX director ---------------------------------------------------------------------------------------------------
@dataclass
class St:
    id: str
    preset: str
    origin: str = "auto"
    stage: str = "stylise"
    spec: EffectSpec = field(init=False)

    def __post_init__(self):
        self.spec = EffectSpec(self.preset, {}, self.preset, self.preset)


def fxd(looks, on_screen):
    d = FxDirector(3)
    d.configure({"enabled": True, "pool": "all", "max": 2}, {"energy": 0.6, "fx": {"density": 1.0, "rate": 0.5}})
    d.looks = looks
    d.scene = lambda: scene(on_screen[0])
    return d


def test_a_new_scene_swaps_the_effect_that_no_longer_suits_it(looks, monkeypatch):
    on = ["ink"]
    d = fxd(looks, on)
    d.arm(True, 0.0)
    move = d.plan(0.0, [], {"halftone": PRESETS["halftone"]}, lambda n: 0.5)
    assert move.op == "add" and move.add == "halftone" and "texture" in move.why
    d.done(1.0, move)
    stack = [St("fx-1", "halftone")]
    on[0] = "fractal"                                         # a busy scene arrives: halftone of it is static
    d.arm(True, 2.0)
    assert d.event == "scene_change" and d.due(2.0)
    move = d.plan(2.0, stack, PRESETS, lambda n: 0.5)
    assert move is not None and move.op == "swap" and move.remove == "fx-1" and "no longer suits" in move.why
    d.done(3.0, move)
    on[0] = "neon"                                            # and a scene it suits: nothing moves
    d._fit_at_add = {"bloom": 1.0}
    d.arm(True, 4.0)
    assert d.plan(4.0, [St("fx-2", "grade")], PRESETS, lambda n: 0.5) is None


def test_a_section_change_lets_the_scene_move_first(looks, monkeypatch):
    monkeypatch.setitem(F.FX_EVENT_P, "section_change", 1.0)
    on = ["ink"]
    d = fxd(looks, on)
    d.arm(True, 0.0)
    d.direction["fx"]["follow"] = 1.0
    assert d.on_event("section_change", 100.0) and d.event == "section_change"
    assert not d.due(100.0) and d.due(100.0 + F.SECTION_WAIT_S)
    on[0] = "fractal"                                          # the scene director moved: only re-check the bus
    d.arm(True, 101.0)
    stack = [St("fx-1", "grade")]
    d._fit_at_add = {"grade": 1.0}
    assert d.plan(105.0, stack, PRESETS, lambda n: 0.5) is None


def test_the_director_asks_what_is_heard(looks):
    looks.listens_of = {"notes_fx": ("notes",)}.get
    d = fxd(looks, ["ink"])
    d.heard = lambda: {"notes": 0.0}
    assert d.fit("notes_fx", {"notes_fx": {"effect": "notes_fx"}}).mult == pytest.approx(0.15)
    d.heard = lambda: 1 / 0                                    # a broken hook: no opinion, never a crash
    assert d.fit("notes_fx", {"notes_fx": {"effect": "notes_fx"}}).mult == 1.0


def test_a_pick_carries_its_level(looks):
    d = fxd(looks, ["fractal"])
    d.configure({"enabled": True, "pool": "all", "max": 2}, {"energy": 0.05, "fx": {"density": 1.0, "rate": 0.5}})
    d.arm(True, 0.0)
    move = d.plan(0.0, [], {"mosh": PRESETS["mosh"]}, lambda n: 0.5)
    assert move.level == pytest.approx(0.35) and "level 0.35 (budget)" in move.why
