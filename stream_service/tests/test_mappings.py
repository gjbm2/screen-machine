"""Per-item music mappings: the v2 evaluator (binding.py), the store and its layering (mappings.py, presets.py), the
API and the session's cue sampling (fake engine, no GPU)."""
from __future__ import annotations

import asyncio
import json
import math
import shutil

import pytest
from aiohttp.test_utils import TestClient, TestServer

from stream_service import binding, mappings as mappings_mod, sessions as sessions_mod
from stream_service.modulation import Modulation, SessionRuntime, split_bindings
from stream_service.plugins.base import ParamError
from stream_service.server import MANAGER, PRESETS, make_app

from .test_cue import AUTO, live
from .test_sessions import AUTH, REPO_CONFIG, FakeEngine

NUM = {"type": "number", "minimum": 0.0, "maximum": 2.0, "default": 1.0, "x-bindable": True}
ENERGY = {"in": [{"sig": "energy", "w": 0.7}, {"sig": "drive", "w": 0.3}], "map": {"around": [-0.2, 0.2]}}


def bound(spec, prop=NUM, key="p"):
    return binding.Bound(key, prop, binding.parse_v2(key, prop, spec), "scene/x")


# -- the evaluator -------------------------------------------------------------------------------------------
def test_pivot_idle_is_base_and_the_maps():
    b = bound(ENERGY)
    for base in (0.4, 1.0, 1.7):
        assert b.value({"energy": 0.5, "drive": 0.5}, None, base, 1.0) == pytest.approx(base)
        assert b.value({}, None, base, 1.0) == pytest.approx(base)                 # no signals at all
    assert b.value({"energy": 1.0, "drive": 1.0}, None, 1.0, 1.0) == pytest.approx(1.4)    # base + 0.2 * span
    assert b.value({"energy": 0.0, "drive": 0.0}, None, 1.0, 1.0) == pytest.approx(0.6)
    assert b.value({"energy": 1.0, "drive": 1.0}, None, 1.0, 0.5) == pytest.approx(1.2)    # depth halves it
    s = bound({"in": [{"sig": "energy"}], "map": {"scale": [0.5, 1.5]}})
    assert s.value({"energy": 1.0}, None, 0.8, 1.0) == pytest.approx(1.2)
    assert s.value({"energy": 0.0}, None, 0.8, 1.0) == pytest.approx(0.4)
    r = bound({"in": [{"sig": "beat"}], "map": {"range": [0.2, 1.8]}})
    assert r.value({"beat": 0.0}, None, 1.0, 1.0) == pytest.approx(0.2)            # v1-like: linear from lo
    assert r.value({"beat": 1.0}, None, 1.0, 1.0) == pytest.approx(1.8)


def test_na_inputs_drop_out_and_renormalise():
    b = bound({"in": [{"sig": "energy", "w": 0.5}, {"sig": "tempo", "w": 0.5}], "map": {"around": [-0.25, 0.25]}})
    both = b.value({"energy": 1.0, "tempo": 0.0}, None, 1.0, 1.0)
    alone = b.value({"energy": 1.0, "tempo": None}, None, 1.0, 1.0)
    assert both == pytest.approx(1.0) and alone == pytest.approx(1.5)
    half = b.value({"energy": 1.0, "tempo": 0.0}, {"tempo": 0.0}, 1.0, 1.0)
    assert half == pytest.approx(1.5)                                             # avail 0: as if n/a


def test_mul_kick_steps_and_cue_quant():
    pulse = bound({"in": [{"sig": "beat"}, {"sig": "punch", "floor": 0.6}], "op": "mul", "map": {"range": [0, 1]}},
                  {"type": "number", "minimum": 0.0, "maximum": 1.0, "x-bindable": True}, "pulse")
    assert pulse.value({"beat": 0.0, "punch": 1.0}, None, 0.0, 1.0) == pytest.approx(0.0, abs=1e-3)
    assert pulse.value({"beat": 1.0, "punch": 0.0}, None, 0.0, 1.0) == pytest.approx(0.6)
    k = bound({**ENERGY, "kick": {"sig": "beat", "amt": 0.1, "by": "punch"}})
    assert k.value({"energy": .5, "drive": .5, "beat": 1.0, "punch": 1.0}, None, 1.0, 1.0) == pytest.approx(1.2)
    enum = {"type": "string", "enum": ["linear", "ease-in-out", "ease-in", "ease-out"]}
    st = binding.Bound("easing", enum, binding.parse_v2("easing", enum, {
        "at": "cue", "in": [{"sig": "energy"}], "steps": [None, None, None, "ease-out"]}, kind="transition"))
    assert st.value({"energy": 0.9}, None, "linear", 1.0) == "ease-out"
    assert st.value({"energy": 0.2}, None, "linear", 1.0) == "linear"
    dur = {"type": "number", "minimum": 0.0, "maximum": 60.0}
    m = {"duration_s": binding.parse_v2("duration_s", dur, {"at": "cue", "in": [{"sig": "energy", "inv": True}],
                                                             "map": {"scale": [0.65, 1.35]}, "quant": "beat"},
                                        kind="transition")}
    out = binding.sample_cue({"duration_s": dur}, m, {"duration_s": 1.6}, {"energy": 1.0}, None, 1.0, "t", 0.4545)
    assert out["duration_s"] == pytest.approx(2 * 0.4545)                         # 1.04 s -> 2 beats at 132


@pytest.mark.parametrize("spec,code", [
    ({"in": [{"sig": "beat"}, {"sig": "energy"}], "map": {"around": [-.1, .1]}}, "mixed_kinds"),
    ({"in": [{"sig": "bogus"}], "map": {"around": [-.1, .1]}}, "unknown_signal"),
    ({"in": [{"sig": "energy"}], "map": {"around": [-.5, .1]}}, "bad_map"),
    ({"in": [{"sig": "energy"}] * 5, "map": {"around": [-.1, .1]}}, "too_many_inputs"),
    ({"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}, "kick": {"sig": "beat", "amt": .5}}, "bad_kick"),
    ({"at": "section", "in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}, "kick": {"sig": "beat", "amt": .1}},
     "bad_kick"),
    ({"in": [{"sig": "gobo"}], "map": {"around": [-.1, .1]}}, "bad_mode"),
    ({"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}, "extra": 1}, "unknown_key"),
])
def test_validation_codes(spec, code):
    with pytest.raises(binding.MappingError) as e:
        binding.parse_v2("p", NUM, spec)
    assert code in {p["code"] for p in e.value.problems}


def test_kicks_on_rates_but_not_on_angles():
    k = {"in": [{"sig": "energy"}], "map": {"scale": [0.8, 1.2]}, "kick": {"sig": "beat", "amt": 0.08}}
    assert binding.parse_v2("rotation_speed", NUM, k)["kick"]["amt"] == 0.08
    with pytest.raises(binding.MappingError, match="no kicks on positions"):
        binding.parse_v2("rotation", NUM, k)


def test_tier_rules_flash_and_never():
    reg = {"type": "number", "minimum": 0.0, "maximum": 1.0, "x-tier": "regional"}
    with pytest.raises(binding.MappingError, match="live_needs_ok"):
        binding.parse_v2("warp", reg, {"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}})
    with pytest.raises(binding.MappingError, match="smooth_too_short"):
        binding.parse_v2("warp", reg, {"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}, "live_ok": True})
    assert binding.parse_v2("warp", reg, {"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}, "live_ok": True,
                                          "ease_s": 2.5})["at"] == "live"
    assert binding.parse_v2("warp", reg, {"at": "section", "in": [{"sig": "energy"}],
                                          "map": {"around": [-.1, .1]}})["ease_s"] == 4.0
    with pytest.raises(binding.MappingError, match="flash_rule"):
        binding.parse_v2("flash_kick", NUM, {"in": [{"sig": "punch"}], "map": {"scale": [0.5, 1.2]}})
    for name in ("speed", "seed", "render_scale", "iterations"):
        with pytest.raises(binding.MappingError, match="not_mappable"):
            binding.parse_v2(name, NUM, {"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}})
    with pytest.raises(binding.MappingError, match="transitions are cue only"):
        binding.parse_v2("spread", NUM, {"in": [{"sig": "energy"}], "map": {"around": [-.1, .1]}}, kind="transition")


def test_modes_section_holds_until_the_epoch_moves_and_cue_holds():
    b = bound({"at": "section", "in": [{"sig": "energy"}], "map": {"around": [-.25, .25]}, "ease_s": 0})
    v0 = b.step({"energy": 1.0}, None, 1.0, 1.0, (0, 0), 0.0, 0.1)
    assert v0 == pytest.approx(1.5)
    assert b.step({"energy": 0.0}, None, 1.0, 1.0, (0, 0), 1.0, 0.1) == pytest.approx(1.5)   # held
    assert b.step({"energy": 0.0}, None, 1.0, 1.0, (1, 0), 3.0, 0.1) == pytest.approx(1.5)   # < 8 s since
    assert b.step({"energy": 0.0}, None, 1.0, 1.0, (1, 0), 9.0, 0.1) == pytest.approx(0.5)   # re-sampled
    assert b.step({"energy": 1.0}, None, 1.0, 1.0, (1, 1), 9.5, 0.1) == pytest.approx(1.5)   # liveness: at once
    c = bound({"at": "cue", "in": [{"sig": "energy"}], "map": {"around": [-.25, .25]}})
    assert c.step({"energy": 1.0}, None, 1.0, 1.0, (0, 0), 0.0, 0.1) == pytest.approx(1.5)
    assert c.step({"energy": 0.0}, None, 1.0, 1.0, (5, 5), 99.0, 0.1) == pytest.approx(1.5)


def test_modulation_v1_and_v2_together_colour_and_split():
    rt = SessionRuntime(None)
    props = {"a": NUM, "b": NUM, "palette": {"type": "string", "default": "ember", "x-palette": True},
             "palette_mix": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.0}}
    plain, bindings = split_bindings(props, {"a": {"bind": "beat", "range": [0, 2]},
                                             "b": {"in": [{"sig": "energy"}], "map": {"around": [-.25, .25]}},
                                             "palette": {"colour": "room", "mix": 0.6}})
    assert plain == {"a": 0} and set(bindings) == {"a", "b", "palette"}
    mod = Modulation(rt, props, bindings, key="scene/x")
    rt.signals = {"beat": 1.0, "energy": 1.0}
    rt.colour_avail, rt.maestro_rows = 1.0, __import__("numpy").array(binding.maestro_rows(0.0, 0.05, 1.0))
    out = mod.apply({"a": 0.0, "b": 1.0, "palette": "ember", "palette_mix": 0.0})
    assert out["a"] == 2.0 and out["b"] == pytest.approx(1.5) and out["palette_mix"] == pytest.approx(0.6)
    rows = mod.palette_rows()
    assert rows is not None and rows.shape == (4, 3)
    plain, bindings = split_bindings(props, {"b": {"base": 0.7}}, bindings)        # move the centre, keep it
    assert plain == {"b": 0.7} and "b" in bindings
    with pytest.raises(ParamError):
        split_bindings(props, {"b": {"nope": 1}})


def test_maestro_palette_fit_puts_the_hue_at_the_middle():
    for hue in (0.0, 0.1, 0.33, 0.6, 0.9):
        a, b, c, d = binding.maestro_rows(hue, 0.05, 1.0)
        rgb = [a[i] + b[i] * math.cos(2 * math.pi * (c[i] * 0.5 + d[i])) for i in range(3)]
        mx, mn = max(rgb), min(rgb)
        r, g, bl = rgb
        h = ((g - bl) / (mx - mn) % 6 if mx == r else (bl - r) / (mx - mn) + 2 if mx == g else (r - g) / (mx - mn) + 4) / 6
        assert min(abs(h - hue), 1 - abs(h - hue)) < 0.02


# -- the store, layering and the API -------------------------------------------------------------------------
def config_with(tmp_path, gen=None, user=None):
    for f in ("scenes.yaml", "transitions.yaml", "vocab.yaml", "effects.yaml"):
        if (REPO_CONFIG / f).exists():
            shutil.copy(REPO_CONFIG / f, tmp_path / f)
    if gen is not None:
        (tmp_path / "mappings.generated.json").write_text(json.dumps({"format": 1, "items": gen}))
    if user is not None:
        (tmp_path / "mappings.json").write_text(json.dumps({"format": 1, "items": user}))
    return tmp_path


@pytest.fixture
async def mclient(monkeypatch, tmp_path):
    FakeEngine.instances.clear()
    monkeypatch.setattr(sessions_mod, "Engine", FakeEngine)
    gen = {"scene/fractal_zoom": {
        "bindings": {"palette_drift": {"in": [{"sig": "energy"}], "map": {"scale": [0.5, 2.0]}, "fam": "rate"},
                     "zoom_rate": {"in": [{"sig": "drive"}], "map": {"around": [-.1, .1]}},
                     "rotation": {"at": "cue", "in": [{"sig": "energy"}], "map": {"around": [-.2, .2]}},
                     "palette": {"colour": "room", "mix": 0.5}},
        "review": {"by": "llm", "confidence": 0.8}},
        "transition/melt": {"bindings": {"spread": {"at": "cue", "in": [{"sig": "energy"}],
                                                     "map": {"around": [-.3, .3]}}}}}
    app = make_app(config_dir=config_with(tmp_path, gen), max_sessions=3)
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_layering_precedence(mclient):
    presets = mclient.app[PRESETS]
    spec = presets.resolve_scene("fractal-zoom")
    b = spec.bindings
    assert b["palette_drift"]["in"][0]["sig"] == "energy" and b["palette_drift"]["fam"] == "rate"   # generated
    assert b["zoom_rate"] == {"bind": "activity_slow", "range": [0.15, 0.4], "rest": 0.275}         # authored wins
    assert b["palette"]["colour"] == "room"
    maps = presets.maps
    maps.put_user("scene/fractal_zoom", {"bindings": {"zoom_rate": {"in": [{"sig": "motion"}],
                                                                    "map": {"around": [-.05, .05]}},
                                                      "palette": None}}, None, merge=False)
    b = presets.resolve_scene("fractal-zoom").bindings
    assert b["zoom_rate"]["in"][0]["sig"] == "motion" and "palette" not in b                      # owner wins
    b = presets.resolve_scene({"preset": "fractal-zoom", "params": {"zoom_rate": 0.3}}).bindings
    assert "zoom_rate" not in b                                                                    # a value pins
    maps.put_user("scene/fractal_zoom", {"follow": False}, None, merge=True)
    assert presets.resolve_scene("fractal-zoom").bindings == {}
    t = presets.resolve_transition("melt-2s")
    assert t.bindings["spread"]["at"] == "cue"


async def test_api_get_put_patch_delete_eval(mclient):
    r = await mclient.get("/api/mappings?kind=scene&q=fractal", headers=AUTH)
    idx = await r.json()
    it = idx["items"]["scene/fractal_zoom"]
    assert r.status == 200 and it["src"] == "llm" and it["colour"] == 0.5 and "energy" in it["signals"]
    r = await mclient.get("/api/mappings/scene/fractal_zoom", headers=AUTH)
    rec = await r.json()
    assert rec["preset"] and rec["effective"]["palette_drift"]["preview"]["ambient"] < \
        rec["effective"]["palette_drift"]["preview"]["peak"]
    assert rec["params"]["speed" if "speed" in rec["params"] else "iterations"]["modes"] in ([], ["cue"])
    bad = await mclient.put("/api/mappings/scene/fractal_zoom", json={"bindings": {"exposure": {
        "in": [{"sig": "beat"}, {"sig": "energy"}], "map": {"around": [-.1, .1]}}}}, headers=AUTH)
    body = await bad.json()
    assert bad.status == 400 and body["error"] == "invalid_mapping" and body["problems"][0]["code"] == "mixed_kinds"
    ok = await mclient.patch("/api/mappings/scene/fractal_zoom", json={"base_rev": 0, "bindings": {
        "exposure": {"in": [{"sig": "level_slow"}], "map": {"scale": [0.9, 1.1]}}}}, headers=AUTH)
    rec = await ok.json()
    assert ok.status == 200 and rec["rev"] == 1 and rec["user"]["bindings"]["exposure"]["map"] == {"scale": [0.9, 1.1]}
    stale = await mclient.patch("/api/mappings/scene/fractal_zoom", json={"base_rev": 0, "bindings": {}},
                                headers=AUTH)
    assert stale.status == 409 and (await stale.json())["error"] == "stale_base"
    ev = await mclient.post("/api/mappings/eval", json={"key": "scene/fractal_zoom", "bindings": {
        "palette_shift": {"at": "section", "in": [{"sig": "gobo"}], "map": {"around": [-.2, .2]}}}}, headers=AUTH)
    assert ev.status == 200 and "palette_shift" in (await ev.json())["effective"]
    d = await mclient.delete("/api/mappings/scene/fractal_zoom", headers=AUTH)
    assert d.status == 200 and (await d.json())["user"] is None
    assert (await mclient.get("/api/mappings/scene/nope", headers=AUTH)).status == 404


async def test_session_samples_cue_mappings_at_prepare_and_transitions(mclient):
    sid, s, eng = await live(mclient, scene="fractal-zoom", auto=AUTO)
    s.runtime.extra = {"energy": 0.9, "drive": 0.9, "punch": 0.9, "tempo": None, "epoch": 0}
    s.sampler.w = 1.0
    src = await s._make_scene(mclient.app[PRESETS].resolve_scene("fractal-zoom"))
    assert "rotation" not in src.modulation.bindings and src.prepared_params["rotation"] > 3.0   # cue: sampled
    assert "palette_drift" in src.modulation.bindings
    t = await s._transition_ready(mclient.app[PRESETS].resolve_transition("melt-2s"))
    assert t.frozen_spec.bindings == {} and t.frozen_spec.params["spread"] != \
        mclient.app[PRESETS].resolve_transition("melt-2s").params["spread"]
    g = await s._transition_ready(mclient.app[PRESETS].resolve_transition("melt-2s"), mapped=False)
    assert g.frozen_spec.params == mclient.app[PRESETS].resolve_transition("melt-2s").params    # go / exit: as is


def test_schema_hash_and_modes():
    a = {"x": NUM, "e": {"type": "string", "enum": ["a", "b"]}}
    assert mappings_mod.schema_hash(a) == mappings_mod.schema_hash(dict(reversed(list(a.items()))))
    assert mappings_mod.modes_for("x", NUM, "scene") == ["live", "section", "cue"]
    assert mappings_mod.modes_for("e", a["e"], "scene") == ["cue"]
    assert mappings_mod.modes_for("seed", NUM, "scene") == []
    assert mappings_mod.modes_for("x", NUM, "transition") == ["cue"]


def test_clock_signals_bind_and_kick():
    """04's show_service signals (17 Oct): a kick on each bar's first beat, and a phase as an input."""
    prop = {"type": "number", "minimum": 0.0, "maximum": 2.0, "x-bindable": True}
    b = binding.parse_v2("swirl_speed", prop, {"in": [{"sig": "bass"}], "map": {"scale": [.8, 1.3]},
                                               "kick": {"sig": "downbeat", "amt": .1}})
    assert b["kick"]["sig"] == "downbeat"
    binding.parse_v2("swirl_speed", prop, {"in": [{"sig": "bar_phase"}], "map": {"around": [-.1, .1]}})


def test_section_floats_follow_live_around_their_latch_while_the_feed_is_live():
    """music_feed.Response.follow (owner, 17 Oct): a float section binding latches as before and follows the live
    value around it, eased in from the latch; integers, gobo inputs, snaps (ease 0), the latched families and
    bindings hold; a regional param follows no faster than parse_v2 lets live move it."""
    from stream_service.music_feed import Response
    spec = {"at": "section", "in": [{"sig": "energy"}], "map": {"around": [-.25, .25]}, "ease_s": 4}
    resp = Response(latched=frozenset({"symmetry_count"}), latched_bindings=frozenset({"scene/x/q"}))
    regional = {"type": "number", "minimum": 0.0, "maximum": 2.0, "default": 1.0, "x-tier": "regional"}
    b, held, fam, snap, reg = (bound(spec), bound(spec), bound({**spec, "fam": "symmetry_count"}),
                               bound({**spec, "ease_s": 0}), bound(spec, regional))
    opted = bound(spec, key="q")                         # "scene/x/q": a reviewed binding that must hold
    gobo = bound({**spec, "in": [{"sig": "gobo"}, {"sig": "energy"}]})
    integer = binding.Bound("n", {"type": "integer", "minimum": 0, "maximum": 10, "default": 5},
                            {"at": "section", "in": [{"sig": "energy", "w": 1.0}], "map": {"around": [-.25, .25]},
                             "ease_s": 4.0}, "scene/x")
    assert snap.pinned and not b.pinned and reg.regional and not b.regional
    floats = (b, held, fam, snap, reg, opted)
    for x in floats:
        assert x.step({"energy": 0.5}, None, 1.0, 1.0, (0, 0), 0.0, 0.05) == pytest.approx(1.0)   # latched at base
    g0 = gobo.step({"energy": 0.5, "gobo": 2}, None, 1.0, 1.0, (0, 0), 0.0, 0.05)
    integer.step({"energy": 0.5}, None, 5, 1.0, (0, 0), 0.0, 0.05)
    t, out = 0.0, {}
    for k in range(1, 41):                               # loud music, same section, 20 fps: the live part eases in
        t += 0.05
        for x in floats:
            out[x] = x.step({"energy": 1.0}, None, 1.0, 1.0, (0, 0), t, 0.05, *((0.7, resp) if x is not held else ()))
        gv = gobo.step({"energy": 1.0, "gobo": 2}, None, 1.0, 1.0, (0, 0), t, 0.05, 0.7, resp)
        iv = integer.step({"energy": 1.0}, None, 5, 1.0, (0, 0), t, 0.05, 0.7, resp)
        if k == 1:                                       # from the latch, no jump on the first frame
            assert out[b] < 1.0 + 0.7 * 0.5 * 0.06
        if k == 20:                                      # 1 s: tau min(ease 4, max_ease 1) = 1 s; regional 2 s
            assert out[b] == pytest.approx(1.0 + 0.7 * 0.5 * (1.0 - math.exp(-1.0)), rel=1e-6)
            assert out[reg] == pytest.approx(1.0 + 0.7 * 0.5 * (1.0 - math.exp(-0.5)), rel=1e-6)
    assert b.target == pytest.approx(1.0) and out[b] == pytest.approx(1.0 + 0.7 * 0.5 * (1.0 - math.exp(-2.0)))
    for x in (held, fam, snap, opted):                   # no feed / a latched family / a snap / opted out: held
        assert out[x] == pytest.approx(1.0) and x.yl is None
    assert gv == pytest.approx(g0) and iv == 5
    for _ in range(40):                                  # the feed goes: follow 0, back to the latch (no pop: the
        t += 0.05                                        # runtime's follow fades with the feed's liveness)
        v = b.step({"energy": 1.0}, None, 1.0, 1.0, (0, 0), t, 0.05, 0.0, resp)
    assert v == pytest.approx(1.0) and b.yl is None
    # the latch is sampled from the slow energy (as it always was); the live part from the fast one
    s2 = bound(spec)
    s2.step({"energy": 1.0, "energy_slow": 0.5}, None, 1.0, 1.0, (0, 0), 0.0, 0.0, 0.7, resp)
    assert s2.target == pytest.approx(1.0) and s2.yl == pytest.approx(1.0)
    s2.step({"energy": 1.0, "energy_slow": 0.5}, None, 1.0, 1.0, (0, 0), 0.05, 0.05, 0.7, resp)
    assert s2.target == pytest.approx(1.0) and s2.yl > 1.0
    cue = binding.sample_cue({"p": NUM}, {"p": {**ENERGY, "at": "cue"}}, {"p": 1.0},
                             {"energy": 1.0, "energy_slow": 0.5, "drive": 0.5}, None, 1.0, "scene/x")
    assert cue["p"] == pytest.approx(1.0)               # a cue is held too: the slow energy
    # through the runtime: Modulation hands the follow to every section binding; /inputs shows latch and follow
    rt = SessionRuntime(None)
    mod = Modulation(rt, {"p": NUM}, {"p": binding.parse_v2("p", NUM, spec)}, key="scene/x")
    rt.signals, rt.epoch, rt.dt = {"energy": 0.5}, (0, 0), 0.05
    assert mod.apply({"p": 1.0})["p"] == pytest.approx(1.0)
    assert mod.describe()[0]["target"] == pytest.approx(1.0) and mod.describe()[0]["follows"] is False
    rt.signals, rt.follow, rt.response = {"energy": 1.0}, 0.7, resp
    for _ in range(40):
        rt.now += 0.05
        o = mod.apply({"p": 1.0})
    assert o["p"] > 1.2 and mod.describe()[0]["follows"] is True and mod.describe()[0]["value"] > 1.2
    assert mod.describe()[0]["follow_tau_s"] == 1.0 and reg.follow_taus(resp) == (2.0, 2.0)   # min(4, 1); regional 2
    # the bus effects' rows (fx_state.SessionFx.bindings) show the same
    from types import SimpleNamespace as NS

    from stream_service.fx_state import SessionFx
    fx = SessionFx.__new__(SessionFx)
    fx.stack = [NS(id="fx-1", engine_slot=NS(modulation=mod), spec=NS(bindings={"p": spec}))]
    row = fx.bindings()[0]
    assert row["follows"] is True and row["target"] == pytest.approx(1.0) and row["follow_tau_s"] == 1.0
    assert Modulation(rt, {"p": NUM}, {"p": binding.parse_v2("p", NUM, {**spec, "at": "live"})}).section_row("p") == {}


def test_the_runtime_follows_only_while_the_feed_is_live():
    from stream_service.music_feed import MusicFeed
    rt = SessionRuntime(None)
    rt.begin_frame(10.0)
    assert rt.follow == 0.0                              # no feed at all: section bindings hold, as before
    f = MusicFeed()
    rt.feed = f
    for i in range(50):
        f.on_message({"type": "music.features", "data": {"energy": 0.5}}, 10.0 + i * 0.04)
    rt.begin_frame(12.0)
    assert rt.follow == pytest.approx(0.7) and rt.response is f.response.cfg
    rt.begin_frame(20.0)                                 # the feed has gone (faded out)
    assert rt.follow == 0.0