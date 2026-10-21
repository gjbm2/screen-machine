"""light_scenes: the catalogue (load, problems, hot reload, subscribers), the scoring and the pick (README S5.24), and the
director's side: when it picks, room.look's fields, and drops. Config files are read, never written; tmp files only.

Scene names here are made up on purpose, except where the real config/live/light_scenes.yaml is checked.
"""

from __future__ import annotations

import inspect
import json
import os
import random
import sys

import pytest
import yaml

from show_service import contract
from show_service import light_scenes as ls
from show_service.looks import LookEngine
from show_service.tests import test_looks as tl

VOCAB = """moods: [calm, mellow, bright, euphoric, dark]
genre: [jazz, techno, house, ambient]
transition_style: [gentle, lively, wild]
palette_family: [warm, cool]
"""

SCENES = {
    "version": 1,
    "scenes": {
        "hush": {"label": "Hush", "layers": [],
                 "tags": {"energy": [0.0, 0.3], "moods": ["calm"], "genre_fit": {"jazz": 0.9, "ambient": 0.9, "*": 0.1},
                          "valence": 0.4, "organic": 0.8, "density": 0.1}},
        "thump": {"label": "Thump",
                  "layers": [{"id": "pulse", "fx": "square", "phase": "beat", "depth": 1.0, "attack_ms": 0,
                              "select": "kind:lamp"}],
                  "hits": {"select": "kind:lamp", "styles": ["beat"], "min_strength": 0.0, "depth": 0.5,
                           "decay_ms": 100, "drop": {"depth": 1.0, "decay_ms": 400}},
                  "tags": {"energy": [0.6, 1.0], "moods": ["euphoric", "bright"],
                           "genre_fit": {"techno": 0.95, "house": 0.9, "*": 0.1}, "valence": 0.7, "organic": 0.1,
                           "density": 0.9}},
    },
}


def write(tmp_path, scenes=None, vocab=VOCAB, name="light_scenes.yaml"):
    (tmp_path / "vocab.yaml").write_text(vocab)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(scenes if scenes is not None else SCENES, sort_keys=False))
    return p


def cat_of(tmp_path, scenes=None) -> ls.Catalogue:
    c = ls.Catalogue(write(tmp_path, scenes), vocab_path=tmp_path / "vocab.yaml")
    c.poll()
    return c


def bump(p):
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


CALM = {"energy": 0.2, "moods": ["calm"], "genre": "jazz", "genres": {"jazz": 1.0}, "valence": 0.4, "organic": 0.8,
        "density": 0.1}
WILD = {"energy": 0.9, "moods": ["euphoric"], "genre": "techno", "genres": {"techno": 1.0}, "valence": 0.7,
        "organic": 0.1, "density": 0.9}


# -- the catalogue ----------------------------------------------------------------------------------------------------------
def test_loads_scenes_labels_and_tags(tmp_path):
    c = cat_of(tmp_path)
    assert list(c.scenes) == ["hush", "thump"] and c.error is None and c.problems == []
    assert c.label("hush") == "Hush" and c.label(None) is None and c.revision
    gf = c.scenes["hush"]["tags"]["genre_fit"]
    assert gf == {"jazz": 0.9, "ambient": 0.9, "techno": 0.1, "house": 0.1}      # "*" filled the vocab's other words
    assert c.view()["scenes"] == {"hush": "Hush", "thump": "Thump"}
    json.dumps(c.view())


def test_tags_py_is_loaded_by_path_never_as_a_package(tmp_path):
    cat_of(tmp_path)
    assert "stream_service" not in sys.modules and "stream_service.tags" not in sys.modules
    assert "_show_light_scene_tags" not in sys.modules


def test_bad_scenes_are_left_out_with_problems_and_the_rest_load(tmp_path):
    bad = {"version": 1, "extra": 1, "scenes": {
        "Bad Name": {"layers": []},
        "notamap": 3,
        "layers-bad": {"layers": "nope"},
        "hits-bad": {"layers": [], "hits": [1]},
        "tags-bad": {"layers": [], "tags": {"energy": [0.9, 0.1]}},
        "words": {"layers": [], "unknown_key": 1, "tags": {"moods": ["calm", "zesty"], "genre_fit": {"polka": 0.5},
                                                           "valence": 7}},
        "ok": {"layers": []}}}
    c = cat_of(tmp_path, bad)
    assert sorted(c.scenes) == ["ok", "words"]
    text = " | ".join(c.problems)
    for needle in ("extra: not a key", "scenes.Bad Name", "scenes.notamap", "scenes.layers-bad.layers",
                   "scenes.hits-bad.hits", "scenes.tags-bad.tags", "scenes.words.unknown_key",
                   "moods:zesty not in vocab.yaml", "polka not in vocab.yaml genre", "scenes.words.tags.valence"):
        assert needle in text, needle
    assert c.scenes["ok"]["label"] == "ok" and c.scenes["ok"]["tags"] == {"weight": 1.0}
    assert "valence" not in c.scenes["words"]["tags"]


def test_a_broken_file_keeps_the_last_good_scenes_and_hot_reloads(tmp_path):
    c = cat_of(tmp_path)
    seen = []

    class Sub:
        def hear(self, cat):
            seen.append(list(cat.scenes))
    s = Sub()
    c.subscribe(s.hear)
    p = c.path
    p.write_text("scenes: [unclosed")
    bump(p)
    assert c.poll() is False and c.error and list(c.scenes) == ["hush", "thump"] and seen == []
    p.write_text(yaml.safe_dump({"version": 1, "scenes": {"only": {"layers": []}}}))
    bump(p)
    assert c.poll() is True and c.error is None and list(c.scenes) == ["only"] and seen == [["only"]]
    assert c.poll() is False                                       # unchanged: no re-read, no call
    del s                                                          # a gone subscriber drops out (held weakly)
    p.write_text(yaml.safe_dump(SCENES))
    bump(p)
    assert c.poll() is True and seen == [["only"]] and c._subs == []


def test_no_file_means_no_scenes(tmp_path):
    c = ls.Catalogue(tmp_path / "missing.yaml", vocab_path=tmp_path / "vocab.yaml")
    assert c.poll() is False and c.scenes == {} and "not there" in c.error
    assert c.pick(CALM, random.Random(1)) is None


def test_a_failing_subscriber_never_stops_the_poll(tmp_path):
    c = cat_of(tmp_path)
    got = []
    c.subscribe(lambda cat: (_ for _ in ()).throw(RuntimeError("boom")))
    c.subscribe(lambda cat: got.append(cat.revision))
    write(tmp_path, {"version": 1, "scenes": {"x": {"layers": []}}})
    bump(c.path)
    assert c.poll() is True and got == [c.revision]


# -- scoring and the pick -----------------------------------------------------------------------------------------------
def test_weights_follow_the_direction_on_the_screens_scales(tmp_path):
    c = cat_of(tmp_path)
    assert c.weight("hush", CALM) > 10 * c.weight("thump", CALM)
    assert c.weight("thump", WILD) > 10 * c.weight("hush", WILD)
    assert c.weight("hush", {}) == pytest.approx(1.0)              # nothing known: every factor 1
    assert c.weight("nope", CALM) == 0.0


def test_the_pick_is_a_seeded_weighted_draw_that_never_repeats(tmp_path):
    c = cat_of(tmp_path)
    calm = [c.pick(CALM, random.Random(seed))[0] for seed in range(200)]
    assert calm.count("hush") >= 195                               # weighted by the fit...
    even = [c.pick({}, random.Random(seed))[0] for seed in range(200)]
    assert 60 < even.count("hush") < 140                           # ...and a draw: nothing known, both come up
    r1, r2 = random.Random(7), random.Random(7)
    assert [c.pick({}, r1)[0] for _ in range(8)] == [c.pick({}, r2)[0] for _ in range(8)]   # seeded: the same draws
    assert all(c.pick(CALM, random.Random(s), avoid="hush")[0] == "thump" for s in range(20))
    one = cat_of(tmp_path, {"version": 1, "scenes": {"solo": {"layers": []}}})
    assert one.pick(CALM, random.Random(1), avoid="solo")[0] == "solo"   # the only one may repeat
    name, why, ws = c.pick(WILD, random.Random(3), avoid="hush")
    assert name == "thump" and set(ws) == {"thump"}


def test_why_names_what_fits(tmp_path):
    c = cat_of(tmp_path)
    assert c.why("hush", CALM) == "energy 0.2, calm, jazz"
    assert c.why("thump", WILD) == "energy 0.9, euphoric, techno"
    assert c.why("hush", WILD) == "a change"
    assert c.why("thump", {"energy": 0.7, "genres": {"house": 0.8, "jazz": 0.2}}) == "energy 0.7, house"


# -- the real catalogue ---------------------------------------------------------------------------------------------------
def mover_type() -> str:
    from show_service import fixtures as fxmod
    return "generic-mover-full" if "generic-mover-full" in fxmod.BUILTIN_TYPES else "generic-mover"


def rig_of(tmp_path, movers: int = 2, hue: int = 4, extra: dict | None = None) -> dict:
    """A registry view (fixtures.load of a tmp fixtures.yaml): ``movers`` DMX moving heads and ``hue`` Hue lamps."""
    from show_service import fixtures as fxmod
    doc = {"version": 1, "outputs": {"dmx": {"driver": "artnet", "enabled": False},
                                     "hue": {"driver": "hue", "enabled": False}},
           "nodes": {}, "types": {"sm-hue": {"source": "builtin"}, mover_type(): {"source": "builtin"}},
           "fixtures": {**{f"lamp.{i}": {"type": "sm-hue", "output": "hue", "light": f"L{i}"} for i in range(hue)},
                        **{f"head.{i}": {"type": mover_type(), "output": "dmx", "address": 1 + 20 * i}
                           for i in range(movers)}, **(extra or {})}}
    p = tmp_path / "fixtures.yaml"
    p.write_text(yaml.safe_dump(doc))
    return fxmod.load(p)


def test_the_three_real_files_load_clean_and_compile_against_movers_and_lamps(tmp_path):
    c = ls.Catalogue()
    c.poll()
    assert c.error is None and c.problems == [], c.problems
    assert len(c.scenes) >= 24 and len(c.effects) >= 10 and len(c.transitions) >= 5 and c.view()["notes"] == []
    assert {t["kind"] for t in c.transitions.values()} == set(contract.LIGHT_TRANSITION_KINDS)
    assert all(e["on"] and set(e["on"]) <= set(contract.LIGHT_EFFECT_TRIGGERS) for e in c.effects.values())
    assert not any(ls.layer_kind(L) == "shutter" for s in c.scenes.values() for L in s["layers"])   # effects only
    sh = yaml.safe_load(contract.SHOW_FILE.read_text())
    view = rig_of(tmp_path)
    e = LookEngine(light_scenes=c)
    kw = {"lights": sh.get("lights")} if "lights" in inspect.signature(e.configure).parameters else {}
    assert e.configure(sh["looks"], view, lamps=sh.get("lamps"), **kw) == []
    prog = e._program
    assert set(prog.scenes) == set(c.scenes) and prog.scene_problems == (), prog.scene_problems
    assert prog.scene_revision == c.revision
    per_group = getattr(prog, "group_scenes", None)                  # the engine's per-group compile (docs/LIGHTS.md
    if isinstance(per_group, dict) and per_group:                    # §5.6), once it is there
        movers = per_group["movers"]
        for name, sc in movers.items():                              # every scene's layers compiled, none left out
            assert len(sc.layers) == len(c.scenes[name]["layers"]), name
        heads = {f"head.{i}" for i in range(2)}
        for name in [n for n, s in c.scenes.items() if s["needs"]]:
            assert all(set(L.members) == heads for L in movers[name].layers), name   # each runs on the movers
    groups = ls.group_members(sh["lights"]["groups"], view, lamps=sh.get("lamps"))
    assert groups["lamps"]["members"] == [f"lamp.{i}" for i in range(4)] and groups["*"]["members"] == []
    if "cap" in __import__("show_service.fixtures", fromlist=["x"]).SELECTOR_KEYS:
        assert groups["movers"]["members"] == ["head.0", "head.1"]
    vocab = yaml.safe_load(contract.VOCAB_FILE.read_text())
    for kind, entries in (("scenes", c.scenes), ("effects", c.effects)):
        for name, s in entries.items():                              # the screens' scales, the vocab's words
            t = s["tags"]
            assert set(t.get("moods") or []) <= set(vocab["moods"]), name
            assert set((t.get("flow") or {}).get("dirs") or []) <= set(contract.FLOW_DIRS), name
            if kind == "scenes":
                assert set(t["genre_fit"]) == set(vocab["genre"]), name  # "*" filled it to every genre word
                assert all(0.0 <= t[k] <= 1.0 for k in ("valence", "organic", "density")), name


def test_the_real_catalogue_picks_by_the_music():
    c = ls.Catalogue()
    c.poll()
    rng = random.Random(0)
    quiet = {"energy": 0.1, "moods": ["calm"], "genre": "classical", "genres": {"classical": 1.0}, "valence": 0.5,
             "organic": 0.9, "density": 0.1}
    loud = {"energy": 0.95, "moods": ["euphoric"], "genre": "techno", "genres": {"techno": 1.0}, "valence": 0.6,
            "organic": 0.1, "density": 0.9}
    q = [c.pick(quiet, rng)[0] for _ in range(100)]
    lo = [c.pick(loud, rng)[0] for _ in range(100)]
    assert not set(q) & {"peak-strobe", "drop-hit"} and not set(lo) & {"still-glow", "slow-breathe"}


# -- the engine: a catalogue change recompiles on the poller's thread -------------------------------------------------------
def test_the_engine_recompiles_when_the_catalogue_changes(tmp_path):
    c = cat_of(tmp_path)
    e = LookEngine(light_scenes=c)
    assert e.configure({"layers": []}, tl.lamps_rig(2)) == []
    assert set(e._program.scenes) == {"hush", "thump"} and e._program.scenes["thump"].layers[0].id == "thump/pulse"
    write(tmp_path, {"version": 1, "scenes": {"fresh": {"layers": [
        {"id": "w", "fx": "sine", "phase": "time", "period_s": 2, "depth": 0.5, "select": "group:nope"},
        {"id": "bad", "fx": "zigzag", "phase": "beat", "depth": 1.0, "select": "kind:lamp"}]}}})
    bump(c.path)
    assert c.poll() is True                                          # the director's poll, on show-brain
    prog = e._program
    assert set(prog.scenes) == {"fresh"} and prog.scene_revision == c.revision
    assert [L.id for L in prog.scenes["fresh"].layers] == ["fresh/w"]
    assert any("light_scenes.fresh.layers.bad" in p and "zigzag" in p for p in prog.scene_problems)
    assert prog.problems == ()                                       # a scene's problems are never configure's
    st = e.stats()["program"]["scenes"]
    assert st["compiled"] == {"fresh": ["fresh/w"]} and st["problems"] == list(prog.scene_problems)


def test_no_light_scenes_at_all():
    e = LookEngine(light_scenes=False)
    assert e.configure({"layers": []}, tl.lamps_rig(1)) == [] and e._program.scenes == {}


# -- the director's side (README S5.24) -------------------------------------------------------------------------------------
def drig(tmp_path, *, no_feel: bool = False, clock: bool = True):
    """test_director's Rig (made-up vibes, lamps and screens) with this file's two-scene catalogue and a seeded draw."""
    from show_service.tests import test_director as td
    settings = json.loads(json.dumps(td.SETTINGS))
    if no_feel:
        settings["director"]["no_feel"] = {"palettes": ["ember"]}
    r = td.Rig(tmp_path, clock=clock, settings=settings)
    d = tmp_path / "ls"
    d.mkdir(exist_ok=True)
    r.d.light_scenes = cat_of(d)
    r.d._ls_rng = random.Random(1)
    return r


def last_look(r) -> dict:
    return r.of(contract.ROOM_LOOK)[-1]


def test_a_feel_picks_a_light_scene_and_room_look_names_it(tmp_path):
    r = drig(tmp_path)
    r.run(1.0)
    assert r.d.light_scene is None and last_look(r)["light_scene"] is None     # neutral: hands off
    r.d.set_target("alpha")
    r.run(1.0)
    name = r.d.light_scene
    assert name in ("hush", "thump") and r.d.light_scene_why.startswith("a new feel: ")
    lk_ = last_look(r)
    assert lk_["light_scene"] == name and lk_["light_scene_why"] == r.d.light_scene_why
    assert lk_["light_scene_drop"] is None
    snap = r.d.snapshot()["light_scene"]
    assert snap["name"] == name and snap["label"] in ("Hush", "Thump") and snap["picks"] == 1
    assert snap["catalogue"]["scenes"] == {"hush": "Hush", "thump": "Thump"}
    json.dumps(snap)
    log = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    picks = [x for x in log if x.get("kind") == "light_scene"]
    assert picks and picks[-1]["scene"] == name and set(picks[-1]["weights"]) <= {"hush", "thump"}
    assert picks[-1]["reason"] == "feel" and set(picks[-1]["direction"]) >= {"energy", "moods", "genre"}


def test_next_look_moves_it_on_and_keep_this_look_holds_the_directors_own(tmp_path):
    r = drig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    first = r.d.light_scene
    r.d.action("keep")
    r.event("track_change", r.t, track_id=7, via="dsp")             # held: no look, no new light scene
    r.run(1.0)
    assert r.d.light_scene == first
    r.d.action("next")                                              # a person's own change still picks
    r.run(4.5)
    assert r.d.light_scene not in (None, first) and r.d.light_scene_why.startswith("someone pressed Next look: ")
    assert last_look(r)["light_scene"] == r.d.light_scene


def test_with_no_feel_a_new_song_and_next_look_pick_once_as_their_look_lands(tmp_path):
    """09's audit F5 (21 Oct): with no feel they picked at once and again as their look landed (46 doubles under 2 s,
    e.g. flow-wave then still-glow 0.6 s later on one Next look). Now the look that lands picks, once."""
    r = drig(tmp_path, no_feel=True)
    r.run(1.0)
    first = r.d.light_scene
    assert first is not None and r.d.light_scene_why.startswith("the first light scene: ")
    picks = r.d.snapshot()["light_scene"]["picks"]
    r.event("track_change", r.t, track_id=3, via="dsp")
    r.run(0.2)
    assert r.d.light_scene == first                                 # not at once: the new song's look picks it
    r.run(4.5)
    assert r.d.light_scene not in (None, first) and r.d.light_scene_why.startswith("a new song: ")
    assert r.d.snapshot()["light_scene"]["picks"] == picks + 1
    second = r.d.light_scene
    r.d.action("next")
    r.run(4.5)
    assert r.d.light_scene == first and r.d.light_scene_why.startswith("someone pressed Next look: ")
    assert r.d.snapshot()["light_scene"]["picks"] == picks + 2
    r.d.keep_until = r.d._now() + 600.0                             # Keep this look: a new song keeps it
    r.event("track_change", r.t, track_id=4, via="dsp")
    r.run(4.5)
    assert r.d.light_scene == first and second != first


def test_no_scenes_is_null_and_a_scene_that_leaves_is_picked_again(tmp_path):
    r = drig(tmp_path)
    r.d.set_target("alpha")
    r.run(0.5)
    assert r.d.light_scene in ("hush", "thump")
    gone = r.d.light_scene
    keep = "thump" if gone == "hush" else "hush"
    p = r.d.light_scenes.path
    p.write_text(yaml.safe_dump({"version": 1, "scenes": {keep: SCENES["scenes"][keep]}}))
    bump(p)
    r.run(contract.RELOAD_S + 0.3)
    assert r.d.light_scene == keep and r.d.light_scene_why.startswith("the light scenes changed: ")
    p.write_text(yaml.safe_dump({"version": 1, "scenes": {}}))
    bump(p)
    r.run(contract.RELOAD_S + 0.3)
    assert r.d.light_scene is None and last_look(r)["light_scene"] is None
    r.d.action("next")
    r.run(4.5)
    assert r.d.light_scene is None                                  # an empty catalogue: show.yaml's looks.layers


def test_every_drop_goes_to_the_look_engine_once_a_beat_whatever_the_tokens(tmp_path):
    r = drig(tmp_path)
    r.event("drop", r.t + 0.5)
    r.run(0.2)
    assert last_look(r)["light_scene_drop"] is None                 # neutral: hands off
    r.d.set_target("gamma")                                         # a feel with no peak tokens and no beat lock
    r.run(0.5)
    line = r.t + 0.5
    r.event("drop", line, id="cue:drop:1", detail="forecast: drop")  # a cue's forecast drop, at its line
    r.run(0.1)
    dl = r.d.look["light_scene_drop"]
    assert dl == {"id": "drop:1", "at_ms": pytest.approx(line * 1000.0, abs=0.1), "strength": 1.0}
    r.event("drop", line + 0.2)                                     # structure hears the same drop: one drop
    r.run(0.1)
    assert r.d.look["light_scene_drop"]["id"] == "drop:1"
    r.run(2.0)
    r.event("drop", r.t)
    r.run(0.1)
    assert r.d.look["light_scene_drop"]["id"] == "drop:2" and r.d.snapshot()["light_scene"]["drops"] == 2
    # the look engine takes it from LookData and the running scene flashes on the line (DMX)
    r.d.light_scene = "thump"
    r.event("drop", r.t + 3.0)
    r.run(0.1)
    at = r.d.look["light_scene_drop"]["at_ms"] / 1000.0
    e = LookEngine(palette_fn=lambda t: {"lamp.0": (0.2, 0.2, 0.2)}, look_fn=r.d.look_fn,
                   light_scenes=r.d.light_scenes)
    assert e.configure({"layers": []}, tl.lamps_rig(1, output="dmx")) == []
    assert tl.inten(e.evaluate("dmx", at - 0.5, tl.DMX_CAPS), "lamp.0") == pytest.approx(0.2)
    assert tl.inten(e.evaluate("dmx", at + 0.01, tl.DMX_CAPS), "lamp.0") == pytest.approx(1.0)
    assert e.drops == 1


# == docs/LIGHTS.md §6, §7 (18 Oct): effects, transitions, groups and programs ========================================
MOVERS = {"version": 1, "scenes": {
    "hush": SCENES["scenes"]["hush"],
    "thump": SCENES["scenes"]["thump"],
    "orbit": {"label": "Orbit", "needs": ["pan", "tilt"],
              "layers": [{"id": "o", "kind": "position", "mode": "circle", "size_deg": 10},
                         {"id": "b", "kind": "beam", "zoom": "narrow"}, {"id": "g", "kind": "gobo", "slot": "auto"},
                         {"id": "p", "fx": "sine", "phase": "bar", "depth": 0.3}],
              "tags": {"energy": [0.0, 1.0], "flow": {"dirs": ["cw", "ccw"], "honours": False}}},
    "climb": {"label": "Climb", "needs": ["pan", "tilt"],
              "layers": [{"id": "l", "kind": "position", "mode": "lift", "lift": 1.0},
                         {"id": "b", "kind": "beam", "zoom": "wide"}, {"id": "g", "kind": "gobo", "slot": "auto"},
                         {"id": "p", "fx": "saw", "phase": "bar", "depth": 0.3}],
              "tags": {"energy": [0.0, 1.0], "flow": {"dirs": ["up"], "honours": False}}}}}
EFFECTS = {"version": 1, "effects": {
    "flash": {"label": "Flash", "on": ["drop"], "length": {"beats": 2}, "layers": [],
              "hits": {"styles": [], "drop": {"depth": 1.0, "decay_ms": 300}}},
    "flash-two": {"label": "Flash two", "on": ["drop"], "length": {"beats": 2}, "layers": [],
                  "hits": {"styles": [], "drop": {"depth": 1.0, "decay_ms": 300}}},
    "glow-up": {"label": "Glow up", "on": ["build"], "length": "while", "fade": {"in_beats": 0, "out_beats": 1},
                "layers": [{"id": "c", "fx": "saw", "phase": "beat", "depth": 0.5}]},
    "kick": {"label": "Kick", "on": ["section_change"], "chance": 0.5, "length": {"bars": 1},
             "layers": [{"id": "k", "fx": "square", "phase": "beat", "depth": 0.5}]},
    "tick": {"label": "Tick", "on": ["every_bars"], "every_bars": 4, "length": {"bars": 1},
             "layers": [{"id": "t", "fx": "square", "phase": "beat", "depth": 0.5}]},
    "shaker": {"label": "Shaker", "on": ["drop"], "needs": ["gobo"], "length": {"bars": 1},
               "layers": [{"id": "s", "kind": "gobo", "shake": 0.5}]}}}
TRANSITIONS = {"version": 1, "transitions": {
    "snap": {"label": "Snap", "kind": "cut", "tags": {"transition_style": ["wild"]}},
    "slide": {"label": "Slide", "kind": "crossfade", "beats": 8, "seconds": 6, "dark_move": True,
              "tags": {"transition_style": ["gentle"]}},
    "dim": {"label": "Dim", "kind": "fade_dark", "out_s": 2.0, "in_s": 1.0},
    "roll": {"label": "Roll", "kind": "sweep", "beats": 2, "wave_beats": 4,
             "tags": {"flow": {"dirs": ["up", "left"], "honours": True}}}}}


def full_cat(tmp_path, scenes=None, effects=None, transitions=None) -> ls.Catalogue:
    d = tmp_path / "cat"
    d.mkdir(parents=True, exist_ok=True)
    (d / "vocab.yaml").write_text(VOCAB)
    for name, doc in (("light_scenes.yaml", scenes if scenes is not None else MOVERS),
                      ("light_effects.yaml", effects if effects is not None else EFFECTS),
                      ("light_transitions.yaml", transitions if transitions is not None else TRANSITIONS)):
        (d / name).write_text(yaml.safe_dump(doc, sort_keys=False))
    c = ls.Catalogue(d / "light_scenes.yaml", vocab_path=d / "vocab.yaml")
    c.poll()
    return c


def group(members: dict[str, list[str]], gid: str = "g") -> dict:
    caps: list[str] = []
    for a in members.values():
        caps += [x for x in a if x not in caps]
    return {"id": gid, "label": gid, "members": list(members), "caps": caps, "follows": None, "dark_level": "floor",
            "member_caps": members}


HEAD = ["intensity", "colour", "pan", "tilt", "zoom", "gobo", "prism", "colour_slot"]
LAMP = ["intensity", "colour"]


def test_effects_and_transitions_load_with_the_scenes_and_one_revision(tmp_path):
    c = full_cat(tmp_path)
    assert c.problems == [] and c.error is None, c.problems
    assert sorted(c.effects) == sorted(EFFECTS["effects"]) and sorted(c.transitions) == sorted(TRANSITIONS["transitions"])
    assert c.effects["glow-up"]["length"] == "while" and c.effects["tick"]["every_bars"] == 4
    assert c.effects["flash"]["drives"] == ["intensity"] and c.scenes["orbit"]["drives"][:2] == ["pan", "tilt"]
    assert c.transitions["slide"]["dark_move"] is True and c.transitions["roll"]["wave_beats"] == 4.0
    v = c.view()
    assert v["effects"]["glow-up"] == "Glow up" and v["transitions"]["dim"] == "Dim" and v["errors"] == {}
    rev = c.revision
    p = c._parts["effects"].path
    doc = json.loads(json.dumps(EFFECTS))
    del doc["effects"]["kick"]
    p.write_text(yaml.safe_dump(doc))
    bump(p)
    assert c.poll() is True and c.revision != rev and "kick" not in c.effects
    p.write_text("effects: [broken")                                 # a bad file keeps that file's last good entries
    bump(p)
    assert c.poll() is False and "glow-up" in c.effects and "light_effects.yaml" in c.view()["errors"]["effects"]
    assert "orbit" in c.scenes


def test_missing_effects_and_transitions_are_a_note_not_a_problem(tmp_path):
    c = cat_of(tmp_path)
    assert c.effects == {} and c.transitions == {} and c.problems == []
    assert sorted(c.view()["notes"]) == ["light_effects.yaml: not there (no effects)",
                                         "light_transitions.yaml: not there (no transitions)"]


def test_on_keys_read_as_words_not_yaml_booleans(tmp_path):
    raw = "effects:\n  x: {on: [drop], length: {beats: 1}, layers: [{id: a, kind: position, mode: aim, on: floor}]}\n"
    got = ls.load_yaml(raw)
    assert got["effects"]["x"]["on"] == ["drop"] and got["effects"]["x"]["layers"][0]["on"] == "floor"
    assert ls.load_yaml("a: true\nb: false\nc: yes\nd: off\n") == {"a": True, "b": False, "c": "yes", "d": "off"}


def test_validation_lists_what_cant_be_used_and_the_rest_loads(tmp_path):
    scenes = {"version": 1, "scenes": {
        "ok": {"layers": [{"id": "a", "fx": "sine", "phase": "bar", "depth": 0.2}]},
        "weird-kind": {"layers": [{"id": "z", "kind": "laser"}, {"id": "a", "fx": "sine", "phase": "bar",
                                                                 "depth": 0.2}]},
        "strobing": {"layers": [{"id": "s", "kind": "shutter", "mode": "strobe"}]},
        "bad-flow": {"layers": [], "tags": {"flow": {"dirs": ["up", "sideways"], "honours": "maybe", "x": 1}}},
        "own-dir": {"layers": [{"id": "w", "fx": "saw", "phase": "bar", "depth": 0.5, "flow_dir": "diagonal"}]},
        "bad-needs": {"needs": [1], "layers": []}}}
    effects = {"version": 1, "effects": {
        "no-on": {"length": {"bars": 1}, "layers": [{"id": "a", "fx": "sine", "phase": "bar", "depth": 0.2}]},
        "bad-on": {"on": ["drop", "sunrise"], "length": {"furlongs": 2}, "chance": 3, "zap": 1,
                   "layers": [{"id": "a", "fx": "sine", "phase": "bar", "depth": 0.2}]},
        "nothing": {"on": ["drop"], "length": {"bars": 1}, "layers": []},
        "every-what": {"on": ["every_bars"], "length": {"bars": 1},
                       "layers": [{"id": "a", "fx": "sine", "phase": "bar", "depth": 0.2}]},
        "strobe": {"on": ["drop"], "length": {"bars": 1}, "layers": [{"id": "s", "kind": "shutter", "mode": "strobe"}]}}}
    transitions = {"version": 1, "transitions": {"warp": {"kind": "teleport"}, "fine": {"kind": "cut", "beats": -1,
                                                                                         "dark_move": "yes"}}}
    c = full_cat(tmp_path, scenes, effects, transitions)
    text = " | ".join(c.problems)
    for needle in ("scenes.weird-kind.layers.z: kind 'laser'", "scenes.strobing.layers.s: a scene never strobes",
                   "scenes.bad-flow.tags.flow.dirs: sideways", "scenes.bad-flow.tags.flow.honours",
                   "scenes.bad-flow.tags.flow.x", "scenes.own-dir.layers.w.flow_dir", "scenes.bad-needs.needs",
                   "effects.no-on.on: an effect needs on", "effects.bad-on.on: sunrise", "effects.bad-on.length",
                   "effects.bad-on.chance", "effects.bad-on.zap: not an effect key", "effects.nothing: no layers",
                   "effects.every-what: on every_bars needs every_bars", "transitions.warp.kind: 'teleport'",
                   "transitions.fine.beats", "transitions.fine.dark_move"):
        assert needle in text, needle
    assert sorted(c.scenes) == ["bad-flow", "ok", "own-dir", "strobing", "weird-kind"]
    assert [L["id"] for L in c.scenes["weird-kind"]["layers"]] == ["a"] and c.scenes["strobing"]["layers"] == []
    assert c.scenes["bad-flow"]["tags"]["flow"] == {"dirs": ["up"], "honours": False}
    assert "flow_dir" not in c.scenes["own-dir"]["layers"][0]
    assert sorted(c.effects) == ["bad-on", "strobe"] and c.effects["bad-on"]["on"] == ["drop"]
    assert c.effects["bad-on"]["length"] == {"bars": 1.0} and c.effects["bad-on"]["chance"] == 1.0
    assert sorted(c.transitions) == ["fine"] and c.transitions["fine"]["beats"] is None


def test_needs_fit_one_member_that_can_do_them_all(tmp_path):
    c = full_cat(tmp_path)
    heads = group({"head.0": HEAD, "lamp.0": LAMP})
    lamps = group({"lamp.0": LAMP, "lamp.1": LAMP})
    split = group({"a": ["intensity", "pan"], "b": ["intensity", "tilt"]})
    assert c.fits(c.scenes["orbit"], heads) and not c.fits(c.scenes["orbit"], lamps)
    assert not c.fits(c.scenes["orbit"], split)                      # pan here and tilt there: no one light does both
    assert c.fits(c.scenes["hush"], lamps) and c.fits(c.scenes["hush"], group({}))   # no needs: any group
    union_only = {"members": ["a"], "caps": ["pan", "tilt"]}          # another reader's group without member_caps
    assert c.fits(c.scenes["orbit"], union_only) and not c.fits(c.scenes["orbit"], {"members": [], "caps": []})


def test_cap_fit_and_flow_fit_order_the_picks(tmp_path):
    c = full_cat(tmp_path)
    heads = group({"head.0": HEAD, "head.1": HEAD})
    lamps = group({"lamp.0": LAMP})
    assert c.cap_fit(c.scenes["orbit"], heads) == pytest.approx(0.3 + 0.7 * 5 / 7)   # pan tilt zoom gobo intensity
    assert c.cap_fit(c.scenes["hush"], heads) == pytest.approx(0.3 + 0.7 / 7)        # drives nothing: intensity
    assert c.cap_fit(c.scenes["hush"], lamps) == 1.0 and c.cap_fit(c.scenes["orbit"], None) == 1.0
    assert c.flow_fit(c.scenes["orbit"], {"dir": "cw"}) == 1.0 and c.flow_fit(c.scenes["orbit"], "up") == ls.FLOW_MISMATCH
    assert c.flow_fit(c.scenes["hush"], "up") == 1.0 and c.flow_fit(c.scenes["orbit"], None) == 1.0
    picks = [c.pick_for(heads, {}, {"dir": "cw"}, random.Random(s))[0] for s in range(400)]
    assert picks.count("orbit") > picks.count("hush") and picks.count("orbit") > picks.count("thump")
    up = [c.pick_for(heads, {}, {"dir": "up"}, random.Random(s))[0] for s in range(400)]
    assert up.count("climb") > up.count("orbit")                      # the flow decides between the mover scenes
    lam = [c.pick_for(lamps, {}, {"dir": "cw"}, random.Random(s))[0] for s in range(100)]
    assert set(lam) <= {"hush", "thump"}                              # needs filter: no mover scene for lamps
    assert all(c.pick_for(heads, {}, None, random.Random(s), avoid="orbit")[0] != "orbit" for s in range(50))
    assert c.pick_for(group({"x": ["intensity"]}), {}, None, random.Random(1),
                      avoid="hush")[0] == "thump"                     # never the one showing while another fits
    none_fit = full_cat(tmp_path / "x", {"version": 1, "scenes": {"orbit": MOVERS["scenes"]["orbit"]}})
    assert none_fit.pick_for(lamps, {}, None, random.Random(1)) is None
    name, why, ws = c.pick_for(heads, CALM, {"dir": "cw"}, random.Random(3))
    assert set(ws) == set(c.scenes) and why


def test_pick_effect_by_trigger_fit_running_bars_and_chance(tmp_path):
    c = full_cat(tmp_path)
    heads = group({"head.0": HEAD})
    lamps = group({"lamp.0": LAMP})
    assert c.pick_effect(heads, "drop", {}, None, random.Random(1), running=[])[0] in ("flash", "flash-two", "shaker")
    assert {c.pick_effect(lamps, "drop", {}, None, random.Random(s), running=[])[0] for s in range(40)} == \
        {"flash", "flash-two"}                                        # no gobo on a lamp
    assert c.pick_effect(lamps, "drop", {}, None, random.Random(1), running=["flash", "flash-two"]) is None
    assert c.pick_effect(lamps, "build", {}, None, random.Random(1), running=[]) == ("glow-up", "the song builds")
    assert c.pick_effect(lamps, "every_bars", {}, None, random.Random(1), running=[], bar=8) == ("tick", "every 4 bars")
    assert c.pick_effect(lamps, "every_bars", {}, None, random.Random(1), running=[], bar=6) is None
    fired = [c.pick_effect(lamps, "section_change", {}, None, random.Random(s), running=[]) for s in range(400)]
    assert 140 < sum(1 for f in fired if f) < 260                     # chance 0.5, seeded
    r1, r2 = random.Random(9), random.Random(9)
    assert [c.pick_effect(lamps, "section_change", {}, None, r1, running=[]) for _ in range(10)] == \
        [c.pick_effect(lamps, "section_change", {}, None, r2, running=[]) for _ in range(10)]
    assert c.pick_effect(lamps, "outro", {}, None, random.Random(1), running=[]) is None


def test_pick_transition_among_kinds_by_style_and_flow(tmp_path):
    c = full_cat(tmp_path)
    assert c.pick_transition(("cut", "dark_move"), "wild", {}, None, random.Random(1)) == "snap"
    picks = [c.pick_transition(("crossfade", "sweep"), "gentle", {}, {"dir": "down"}, random.Random(s))
             for s in range(200)]
    assert set(picks) == {"slide", "roll"} and picks.count("slide") > picks.count("roll")   # gentle: slide's style
    assert c.pick_transition(("fade_dark",), None, {}, None, random.Random(1)) == "dim"
    assert c.pick_transition(("dark_move",), None, {}, None, random.Random(1)) is None


def test_group_members_first_match_follows_and_the_other_lights(tmp_path):
    view = rig_of(tmp_path, movers=2, hue=3, extra={"tv.a": {"type": "sm-screen", "output": "hue"}})
    cfg = {"one": {"select": "id:head.0", "label": "First head"},
           "heads": {"select": "type:" + mover_type(), "follows": "one"},
           "again": {"select": "id:head.0 | id:lamp.0"},
           "broken": {"select": "nokey:x"}}
    probs: list[str] = []
    g = ls.group_members(cfg, view, problems=probs)
    assert list(g) == ["one", "heads", "again", "broken", "*"]
    assert g["one"]["members"] == ["head.0"] and g["heads"]["members"] == ["head.1"]     # first match wins
    assert g["again"]["members"] == ["lamp.0"] and g["broken"]["members"] == []
    assert g["*"]["members"] == ["lamp.1", "lamp.2"] and g["*"]["label"] == "Other lights"   # never the screen
    assert g["heads"]["follows"] == "one" and g["one"]["label"] == "First head" and g["heads"]["label"] == "heads"
    assert {"pan", "tilt", "intensity"} <= set(g["one"]["caps"]) and set(g["*"]["caps"]) == {"intensity", "colour"}
    assert g["one"]["member_caps"]["head.0"] == g["one"]["caps"]
    assert any("lights.groups.broken.select" in p for p in probs)
    assert ls.group_members(cfg, view) == g                           # pure and deterministic
    none = ls.group_members(None, None)
    assert list(none) == ["*"] and none["*"]["members"] == []
    seen = []

    def spatial(fid, key, value):
        seen.append((fid, key, value))
        return fid == "lamp.2"
    from show_service import fixtures as fxmod
    if "side" in fxmod.SELECTOR_KEYS:                                 # the encode builder's spatial keys
        sg = ls.group_members({"north": {"select": "side:north"}}, view, spatial=spatial)
        assert sg["north"]["members"] == ["lamp.2"] and seen
        assert ls.group_members({"north": {"select": "side:north"}}, view)["north"]["members"] == []


def test_parse_lights_falls_back_and_lists_problems():
    probs: list[str] = []
    out = ls.parse_lights({"scene_group": "nowhere", "extra": 1, "programs": {"effects_max": -1, "zap": 2},
                           "groups": {"A b": {}, "a": {"select": "kind:lamp", "follows": "b", "dark_level": 7,
                                                       "colour": "red"},
                                      "b": {"select": "kind:lamp", "follows": "a"}, "c": {"label": "Nothing"}}},
                          probs)
    text = " | ".join(probs)
    for needle in ("lights.extra", "lights.scene_group", "lights.programs.effects_max", "lights.programs.zap",
                   "lights.groups.A b", "lights.groups.a.dark_level", "lights.groups.a.colour",
                   "follows: 'b' leads round in a loop", "lights.groups.c.select: none"):
        assert needle in text, needle
    assert out["scene_group"] == "*" and out["programs"] == ls.PROGRAMS_FALLBACK
    assert out["groups"]["a"]["follows"] is None and out["groups"]["b"]["follows"] == "a"
    assert out["groups"]["a"]["dark_level"] == "floor" and out["groups"]["c"]["select"] is None
    assert ls.parse_lights(None, []) == {"scene_group": "*", "groups": {}, "programs": ls.PROGRAMS_FALLBACK,
                                         "flow": ls.flowmod.FLOW_FALLBACK}
    real: list[str] = []
    got = ls.parse_lights(yaml.safe_load(contract.SHOW_FILE.read_text())["lights"], real)
    assert real == [] and got["scene_group"] == "lamps" and list(got["groups"]) == ["movers", "lamps"]


# -- the director: programs per group (docs/LIGHTS.md §7.3) ----------------------------------------------------------------
LIGHTS = {"scene_group": "lamps",
          "groups": {"movers": {"label": "Moving lights", "select": "type:" + "{mover}", "dark_level": 0},
                     "lamps": {"label": "Lamps", "select": "kind:lamp", "dark_level": "floor"}},
          "programs": {"effects_max": 2, "effect_gap_bars": 4}}


def prig(tmp_path, *, lights: dict | None = None, cat: ls.Catalogue | None = None, no_feel: bool = False):
    """test_director's Rig with a registry (two movers, four Hue lamps), show.yaml-style lights groups, this file's
    made-up scenes, effects and transitions, and seeded draws."""
    from show_service.tests import test_director as td
    settings = json.loads(json.dumps(td.SETTINGS))
    lt = json.loads(json.dumps(lights if lights is not None else LIGHTS).replace("{mover}", mover_type()))
    settings["lights"] = lt
    if no_feel:
        settings["director"]["no_feel"] = {"palettes": ["ember"]}
    r = td.Rig(tmp_path, clock=True, settings=settings)
    view = rig_of(tmp_path)
    r.ctx.rig = lambda: view
    r.d.light_scenes = cat if cat is not None else full_cat(tmp_path)
    r.d._ls_rng = random.Random(1)
    r.d._fx_rng = random.Random(2)
    return r


def test_each_group_gets_its_program_in_room_look_and_light_scene_is_the_scene_groups(tmp_path):
    r = prig(tmp_path)
    r.run(1.0)
    assert last_look(r)["light_programs"] == {} and r.d.light_scene is None     # neutral: hands off
    r.d.set_target("alpha")
    r.run(1.0)
    lk = last_look(r)
    progs = lk["light_programs"]
    assert set(progs) == {"movers", "lamps"}                          # "*" is empty and not the scene group
    for p in progs.values():
        assert set(p) == set(contract.LightProgram.__annotations__)
        assert set(p["transition"]) == set(contract.LightTransitionRun.__annotations__)
        assert set(p["flow"]) == set(contract.FlowData.__annotations__)
    assert progs["movers"]["members"] == ["head.0", "head.1"] and progs["lamps"]["members"] == [f"lamp.{i}"
                                                                                                for i in range(4)]
    assert progs["lamps"]["scene"] in ("hush", "thump") and progs["movers"]["scene"] in MOVERS["scenes"]
    assert lk["light_scene"] == progs["lamps"]["scene"] and lk["light_scene_why"] == progs["lamps"]["why"]
    assert progs["lamps"]["why"].startswith("a new feel: ") and progs["lamps"]["scene_label"] in ("Hush", "Thump")
    assert {"pan", "tilt"} <= set(progs["movers"]["caps"]) and r.d.look["flow"] is r.d.direction["flow"]
    snap = r.d.snapshot()["lights"]
    assert snap["groups"]["movers"]["members"] == ["head.0", "head.1"] and snap["scene_group"] == "lamps"
    assert set(snap["programs"]) == {"movers", "lamps"}
    json.dumps(r.d.snapshot())
    log = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    progs_log = [x for x in log if x.get("kind") == "light_program" and "groups" in x]
    assert progs_log and set(progs_log[-1]["groups"]) == {"movers", "lamps"}
    movers_picks = []
    for s in range(30):                                               # the movers mostly run mover scenes
        r.d._ls_rng = random.Random(s)
        r.d._pick_light_scene("next", r.t, human=True)
        movers_picks.append(r.d._lp["movers"]["scene"])
        assert r.d._lp["lamps"]["scene"] in ("hush", "thump")
    assert sum(1 for x in movers_picks if x in ("orbit", "climb")) > 15


def test_lockstep_a_look_changes_programs_go_out_ahead_with_the_look_changes_at_ms(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    before = r.d.light_scene
    r.d.action("next")
    r.run(0.1)
    ev = r.looks()[-1]
    lk = last_look(r)
    progs = lk["light_programs"]
    assert progs and all(p["at_ms"] == ev["at_ms"] and p["transition"]["at_ms"] == ev["at_ms"] for p in progs.values())
    assert lk["t_ms"] < ev["at_ms"] and ev["flow_id"] == lk["flow"]["id"]   # published ahead, the flow named
    assert progs["lamps"]["scene"] != before and r.d.light_scene == before   # light_scene (compat) waits for it
    assert r.d.snapshot()["lights"]["next"]["look_id"] == ev["look_id"]
    r.run_to(ev["at_ms"] / 1000.0 + 0.15)
    assert r.d.light_scene == progs["lamps"]["scene"] and r.d._lp_next is None
    assert last_look(r)["light_programs"]["lamps"]["at_ms"] == ev["at_ms"]


def test_a_look_change_that_never_lands_takes_its_programs_back(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(10.0)                                                        # past one phrase: the grammar plays
    showing = {g: p["scene"] for g, p in last_look(r)["light_programs"].items()}
    line = r.next_bar(lead=2.5)
    r.event("drop_armed", line, section_id=1, eta_beats=8)             # a token reserved: the drop's look announced
    r.run(0.1)
    assert r.looks()[-1]["detail"] == "drop" and r.d._lp_next is not None
    r.event("drop_armed", line, section_id=1, eta_beats=None, detail="disarmed")   # called off
    r.run(0.2)
    assert r.d._lp_next is None and r.d.pending is None
    assert {g: p["scene"] for g, p in last_look(r)["light_programs"].items()} == showing
    log = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    assert any(x.get("kind") == "light_program" and x.get("cancelled") for x in log)


def test_keep_holds_the_groups_scenes_and_a_persons_next_still_picks(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    showing = {g: p["scene"] for g, p in r.d._lp.items()}
    r.d.action("keep")
    r.d._pick_light_scene("track", r.t)                                # the director's own: held
    assert {g: p["scene"] for g, p in r.d._lp.items()} == showing
    r.event("drop", r.next_bar())                                      # Keep: no effects either
    r.run(1.0)
    assert all(not p["effects"] for p in last_look(r)["light_programs"].values())
    r.d.action("next")
    r.run(4.5)
    assert {g: p["scene"] for g, p in r.d._lp.items()} != showing


def test_a_follower_runs_its_leaders_program(tmp_path):
    lights = json.loads(json.dumps(LIGHTS))
    lights["groups"] = {"first-head": {"select": "id:head.0", "follows": "movers"}, **lights["groups"]}
    r = prig(tmp_path, lights=lights)
    r.d.set_target("alpha")
    r.run(1.0)
    progs = last_look(r)["light_programs"]
    assert progs["first-head"]["members"] == ["head.0"] and progs["movers"]["members"] == ["head.1"]
    assert progs["first-head"]["scene"] == progs["movers"]["scene"]
    assert progs["first-head"]["transition"] == progs["movers"]["transition"]
    r.event("drop", r.next_bar())
    r.run(0.2)
    progs = last_look(r)["light_programs"]
    assert progs["movers"]["effects"] and progs["first-head"]["effects"] == progs["movers"]["effects"]


def test_the_transition_is_the_screens_decision_in_light_kinds(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    t = r.d._light_transition({"style": "cut", "beats": None, "seconds": 0.05}, 10.0)
    assert t["kind"] == "cut" and t["name"] == "snap" and t["at_ms"] == 10000.0
    kinds = {r.d._light_transition({"style": "crossfade", "beats": 2.0, "seconds": 1.5}, 10.0)["kind"]
             for _ in range(30)}
    assert kinds == {"crossfade", "sweep"}
    t = r.d._light_transition({"style": "crossfade", "beats": 2.0, "seconds": 1.5}, 10.0)
    assert t["params"]["beats"] == 2.0 and t["params"]["seconds"] == 1.5      # the look's own numbers
    t = r.d._light_transition({"style": "crossfade", "beats": None, "seconds": 3.0}, 12.0,
                              {"style": "fade-dark", "out_s": 3.0, "in_s": 1.5, "seconds": None, "on": "now",
                               "change": "big", "dark_max_s": 8.0})
    assert t["kind"] == "fade_dark" and t["name"] == "dim" and t["params"]["out_s"] == 3.0
    assert t["params"]["in_s"] == 1.5 and t["params"]["in_at_ms"] == 12000.0
    r.d.light_scenes = full_cat(tmp_path / "bare", transitions={"version": 1, "transitions": {}})
    t = r.d._light_transition({"style": "cut"}, 10.0)
    assert t == {"name": "cut", "kind": "cut", "params": {"beats": None, "seconds": None, "out_s": None, "in_s": None,
                                                          "wave_beats": None, "dark_move": False, "in_at_ms": None},
                 "at_ms": 10000.0}


def test_a_sweep_puts_sweep_beats_on_the_look_change(tmp_path):
    only_roll = {"version": 1, "transitions": {"roll": TRANSITIONS["transitions"]["roll"]}}
    r = prig(tmp_path, cat=full_cat(tmp_path, transitions=only_roll))
    r.d.set_target("alpha")
    r.run(1.0)
    r.d._announce(r.t, "next", "human", r.d._grid(r.t), at_s=r.next_bar(),
                  transition={"style": "crossfade", "beats": 2.0, "seconds": 1.0})
    ev = r.looks()[-1]
    assert ev["sweep_beats"] == 4.0 and ev["flow_id"] == r.d._flow["id"]
    assert r.d._lp_next["programs"]["lamps"]["transition"]["kind"] == "sweep"


def test_effects_fire_on_the_events_lines_within_the_max_and_the_gap(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    line = r.next_bar()
    r.event("drop", line, id="drop:0:1")
    r.run(0.1)
    progs = last_look(r)["light_programs"]
    for g in ("movers", "lamps"):
        runs = progs[g]["effects"]
        assert len(runs) == 1 and runs[0]["at_ms"] == pytest.approx(line * 1000.0) and runs[0]["why"] == "the drop"
        length_s = {"flash": 2 * 0.5, "flash-two": 2 * 0.5, "shaker": 4 * 0.5}[runs[0]["name"]]   # beats, a bar
        assert set(runs[0]) == set(contract.LightEffectRun.__annotations__)
        assert runs[0]["until_ms"] == pytest.approx((line + length_s) * 1000.0)
    assert progs["lamps"]["effects"][0]["name"] in ("flash", "flash-two")     # no gobo on a lamp
    r.event("drop", line + 0.2, id="cue:drop:1")                       # the same line heard twice: one effect
    r.event("section_change", line + 2 * 4 * 0.5)                      # within effect_gap_bars: none
    r.run(0.1)
    assert all(len(p["effects"]) == 1 for p in last_look(r)["light_programs"].values())
    r.event("drop", line + 4 * 0.5)                                    # a drop always may: a second one
    r.run(0.1)
    assert all(len(p["effects"]) == 2 for p in last_look(r)["light_programs"].values())
    r.event("drop", line + 8 * 0.5)                                    # effects_max 2: no third while two run
    r.run(0.1)
    assert all(len(p["effects"]) == 2 for p in last_look(r)["light_programs"].values())
    r.run_to(line + 4.0)                                                # they end, and go
    assert all(not p["effects"] for p in last_look(r)["light_programs"].values())
    fired = r.d.snapshot()["lights"]["effects"]
    assert fired and {e["group"] for e in fired} == {"movers", "lamps"}
    log = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    assert sum(1 for x in log if x.get("kind") == "light_effect") == len(fired)


def test_a_while_effect_lasts_while_its_phase_does(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    r.sec = {"id": 1, "phase": "build", "bp": 0.2}
    r.event("build", r.t)
    r.run(1.0)
    runs = last_look(r)["light_programs"]["lamps"]["effects"]
    assert [x["name"] for x in runs] == ["glow-up"] and runs[0]["until_ms"] is None
    r.sec = {"id": 2, "phase": "calm", "bp": 0.0}
    r.run(2.0)                                                         # the phase left: it ends, then fades out
    assert not last_look(r)["light_programs"]["lamps"]["effects"]


def test_every_bars_counts_the_clocks_bars(tmp_path):
    only_tick = {"version": 1, "effects": {"tick": EFFECTS["effects"]["tick"]}}
    r = prig(tmp_path, cat=full_cat(tmp_path, effects=only_tick))
    r.d.set_target("alpha")
    r.run(20.0)                                                        # 10 bars at 120 bpm
    fired = [e for e in r.d.snapshot()["lights"]["effects"] if e["group"] == "lamps"]
    assert 2 <= len(fired) <= 3
    beats = [e["at_ms"] / 1000.0 / 0.5 for e in fired]
    assert all(b % 16 == pytest.approx(0.0) for b in beats)             # every 4th bar line


def test_no_effects_while_neutral_house_or_low(tmp_path):
    r = prig(tmp_path)
    r.run(0.5)
    r.event("drop", r.next_bar())
    r.run(0.2)
    assert not r.d._fx_log                                            # neutral
    r.d.set_target("alpha")
    r.run(0.5)
    r.d.action("house")
    r.event("drop", r.next_bar())
    r.run(0.2)
    r.d.action("low")
    r.event("drop", r.next_bar() + 2.0)
    r.run(0.2)
    assert not r.d._fx_log


def test_a_light_registered_later_gets_a_scene_at_once(tmp_path):
    r = prig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    lamps_scene = r.d._lp["lamps"]["scene"]
    (tmp_path / "more").mkdir()
    view = rig_of(tmp_path / "more", movers=3)                         # the Lights page adds a third head
    r.ctx.rig = lambda: view
    r.run(0.3)
    progs = last_look(r)["light_programs"]
    assert progs["movers"]["members"] == ["head.0", "head.1", "head.2"] and progs["lamps"]["scene"] == lamps_scene


def test_with_no_lights_block_every_lamp_is_one_group_as_before(tmp_path):
    r = drig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    progs = last_look(r)["light_programs"]
    assert list(progs) == ["*"] and progs["*"]["scene"] == r.d.light_scene and progs["*"]["label"] == "Other lights"
