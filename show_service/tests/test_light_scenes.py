"""light_scenes: the catalogue (load, problems, hot reload, subscribers), the scoring and the pick (README S5.24), and the
director's side: when it picks, room.look's fields, and drops. Config files are read, never written; tmp files only.

Scene names here are made up on purpose, except where the real config/live/light_scenes.yaml is checked.
"""

from __future__ import annotations

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
def test_the_real_catalogue_loads_clean_and_compiles_against_the_real_rig():
    c = ls.Catalogue()
    c.poll()
    assert c.error is None and c.problems == [], c.problems
    assert len(c.scenes) >= 10
    from show_service import fixtures as fxmod
    reg = fxmod.Registry(contract.FIXTURES_FILE)
    sh = yaml.safe_load(contract.SHOW_FILE.read_text())
    reg.poll(sh.get("looks"), lamps=sh.get("lamps"))
    e = LookEngine(light_scenes=c)
    assert e.configure(sh["looks"], reg.view(), lamps=sh.get("lamps")) == []
    prog = e._program
    assert set(prog.scenes) == set(c.scenes) and prog.scene_problems == (), prog.scene_problems
    assert prog.scene_revision == c.revision
    for name, sc in prog.scenes.items():                            # every scene's layers compiled, none left out
        assert len(sc.layers) == len(c.scenes[name]["layers"]), name
        assert all(L.id.startswith(f"{name}/") for L in sc.layers)
        assert all(L.members for L in sc.layers), name              # each selects the lamps
    vocab = yaml.safe_load(contract.VOCAB_FILE.read_text())
    for name, s in c.scenes.items():                                # the screens' scales, the vocab's words
        t = s["tags"]
        assert set(t["moods"]) <= set(vocab["moods"]), name
        assert set(t["genre_fit"]) == set(vocab["genre"]), name     # "*" filled it to every genre word
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


def test_with_no_feel_a_new_song_and_next_look_pick_at_once(tmp_path):
    r = drig(tmp_path, no_feel=True)
    r.run(1.0)
    first = r.d.light_scene
    assert first is not None and r.d.light_scene_why.startswith("the first light scene: ")
    r.event("track_change", r.t, track_id=3, via="dsp")
    r.run(0.2)
    assert r.d.light_scene not in (None, first) and r.d.light_scene_why.startswith("a new song: ")
    second = r.d.light_scene
    r.d.action("next")
    r.run(0.1)
    assert r.d.light_scene == first and r.d.light_scene_why.startswith("someone pressed Next look: ")
    r.d.keep_until = r.d._now() + 600.0                             # Keep this look: a new song keeps it
    r.event("track_change", r.t, track_id=4, via="dsp")
    r.run(0.2)
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
