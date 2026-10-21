"""ensemble: the room's band (docs/ENSEMBLE.md §11.1). A synthetic registry (two screens zoned on opposite walls, four
lamps zoned and hung at them), light_scenes.group_members for the lamps' group, director.Grid at 120 bpm, 4/4,
8-bar phrases (a beat every 0.5 s, a bar every 2 s, a phrase every 16 s), and an Ensemble ticked at 10 Hz on fake
monotonic times. Forms come from small catalogue files in tmp_path; the real config/live/interplays.yaml is loaded
once to prove it reads clean. No network, no bus, no room.
"""

from __future__ import annotations

import ast
import json
import math
import pathlib
import random

import pytest
import yaml

from show_service import bus as busmod
from show_service import contract as c
from show_service import director as dmod
from show_service import ensemble as E
from show_service import light_scenes as ls
from show_service import space as spmod

LEAD = 1.1
ZONES = {"south": "left", "north": "right"}
TYPES = {"sm-screen": {"id": "sm-screen", "kind": "screen", "attrs": ["colour"]},
         "sm-hue": {"id": "sm-hue", "kind": "lamp", "attrs": ["intensity", "colour"]}}
GROUPS = {"lamps": {"label": "Lamps", "select": "kind:lamp"}}
DIRECTION = {"energy": 0.5, "moods": ["hypnotic"], "genre": "techno", "genres": {}}
LEFT = {"lamps:left", "screen.south"}
RIGHT = {"lamps:right", "screen.north"}


def clock_body(t_s: float, *, gate: str = "beat") -> dict:
    return {"bpm": 120.0, "anchor_ms": 0.0, "beat_at_anchor": 0, "beats_per_bar": 4, "bar_anchor_beat": 0,
            "phrase_bars": 8, "conf": 0.9, "bar_conf": 0.9, "phrase_conf": 0.8, "state": "locked", "gate": gate,
            "epoch": 1, "stability": 0.8, "source": "test", "t_ms": t_s * 1000.0}


def fixture(fid: str, typ: str, screen: str, zone: str | None, label: str | None = None) -> dict:
    f = {"id": fid, "type": typ, "kind": TYPES[typ]["kind"], "screen": screen, "label": label or fid}
    if zone is not None:
        f["zone"] = zone
    return f


def rig_view(*, lamp_zones: bool = True, south_lamp_zone: bool = True) -> dict:
    fx = {"screen.south": fixture("screen.south", "sm-screen", "south-screen", "south", "South Frame"),
          "screen.north": fixture("screen.north", "sm-screen", "north-screen", "north", "North Frame")}
    for fid, scr, zone in (("hue.ne", "north-screen", "north"), ("hue.nw", "north-screen", "north"),
                           ("hue.se", "south-screen", "south"), ("hue.sw", "south-screen", "south")):
        keep = lamp_zones and (south_lamp_zone or zone != "south")
        fx[fid] = fixture(fid, "sm-hue", scr, zone if keep else None)
    return {"revision": "test", "path": "test", "error": None, "problems": [], "outputs": {}, "nodes": {},
            "types": TYPES, "fixtures": fx, "groups": {}}


def settings(**extra) -> dict:
    s = {"players": {"zones": dict(ZONES)}}
    for k, v in extra.items():
        s[k] = v
    return s


def players(rig: dict | None = None, *, cfg: dict | None = None, **kw) -> list[dict]:
    rig = rig if rig is not None else rig_view()
    cfg = cfg if cfg is not None else E.parse_settings(settings(), [])
    return E.players(rig, ls.group_members(GROUPS, rig), cfg=cfg, **kw)


@pytest.fixture
def scorer(tmp_path):
    return ls.Catalogue(tmp_path / "no_scenes.yaml")               # the scenes' tags rule and score, no scenes


class Band:
    """An Ensemble with one catalogue (``forms``: YAML under ``interplays:``), ticked every 100 ms."""

    def __init__(self, tmp_path: pathlib.Path, scorer, forms: str | None, *, cfg: dict | None = None,
                 t: float = 94.0, seed: int = 1, plist: list | None = None) -> None:
        if forms is None:
            path = c.INTERPLAYS_FILE
        else:
            path = tmp_path / "interplays.yaml"
            path.write_text("version: 1\ninterplays:\n" + forms)
        self.cat = E.Catalogue(path, scorer=scorer)
        self.cat.poll()
        self.probs: list[str] = []
        self.cfg = E.parse_settings(cfg if cfg is not None else settings(), self.probs)
        self.en = E.Ensemble(self.cfg, self.cat, rng=random.Random(seed))
        self.t = t
        self.grid_on, self.gate, self.accents, self.beat_lock, self.energy = True, "beat", "full", True, 0.4
        self.flow: dict = {"dir": "right"}
        self.direction = dict(DIRECTION)
        self.active, self.held = True, False
        self.out: list[dict] = []
        self.en.set_players(plist if plist is not None else players(cfg=self.cfg), t)
        self.tick()

    def grid(self) -> dmod.Grid:
        return dmod.Grid(clock_body(self.t, gate=self.gate) if self.grid_on else None, self.t)

    def tick(self) -> list[dict]:
        g = self.en.tick(self.t, self.grid(), active=self.active, accents=self.accents, beat_lock=self.beat_lock,
                         energy=self.energy, direction=self.direction, flow=self.flow, lead_s=LEAD, held=self.held)
        self.out += g
        return g

    def run_to(self, t: float) -> None:
        while self.t < t - 1e-9:
            self.t = round(self.t + 0.1, 6)
            self.tick()

    def look(self, at: float, reason: str = "section", look_id: str = "L1", *, held: bool = False,
             piano: bool = False) -> None:
        self.en.on_look(self.t, at, look_id=look_id, reason=reason, by="director", direction=self.direction,
                        flow=self.flow, grid=self.grid(), held=held, piano=piano)

    def start(self, at: float = 96.0, *, piano: bool = False) -> None:
        """A song change at ``at`` (a phrase line): the catalogue's form takes effect there."""
        self.look(at, "track", "L0", piano=piano)
        self.run_to(at + 0.05)

    def notes(self, *notes: tuple) -> None:
        """(dt, pitch, vel) from now."""
        self.en.on_notes([(self.t + dt, "test-piano", 1, p, v) for dt, p, v in notes], self.t)

    def latest(self) -> dict[str, dict]:
        return {g["id"]: g for g in self.out}

    def of(self, kind: str) -> list[dict]:
        return [g for g in self.latest().values() if g["kind"] == kind]


def at_of(g: dict) -> dict[str, float]:
    return {p["player"]: p["at_ms"] / 1000.0 for p in g["parts"]}


def st_of(g: dict) -> dict[str, float]:
    return {p["player"]: p["strength"] for p in g["parts"]}


ECHO = """  echo-test:
    kind: echo
    line: "echo, {order}"
    params: {team_by: side, order: flow, caller: first, delay_beats: 1, delay_s: 0.75, decay: 0.6, repeats: 1}
    routes: [chord]
    needs: [sides]
"""


# -- players (§2) -------------------------------------------------------------------------------------------------------
def test_zones_give_the_sides_and_the_order():
    pl = players(devices=[{"id": "mirror_ball"}])
    band = [p["id"] for p in pl if p["kind"] != "device"]
    assert band == ["lamps:left", "screen.south", "lamps:right", "screen.north"]
    by = {p["id"]: p for p in pl}
    assert by["lamps:left"]["members"] == ["hue.se", "hue.sw"] and by["lamps:right"]["members"] == ["hue.ne", "hue.nw"]
    assert {p["id"]: p["side"] for p in pl if p["kind"] != "device"} == {
        "lamps:left": "left", "screen.south": "left", "lamps:right": "right", "screen.north": "right"}
    assert all(p["via"] == "zone" for p in pl if p["kind"] != "device")
    assert by["lamps:left"]["label"] == "Lamps, left" and by["screen.north"]["label"] == "North Frame"
    assert by["screen.south"]["screen"] == "south-screen" and by["lamps:left"]["group"] == "lamps"
    ball = by["device:mirror_ball"]
    assert ball["side"] == "centre" and ball["members"] == [] and ball["label"] == "Mirror ball"
    assert [p["order"] for p in pl] == list(range(len(pl)))
    assert all(set(p) == set(c.PlayerInfo.__annotations__) for p in pl)


def _view(front: str, south_y: tuple[float, float], north_y: tuple[float, float], *, lamps: bool = True) -> dict:
    """south_y / north_y: (the lamps' y, the screen's y) on a 6 x 5 m room."""
    app = {"screen.south": {"at": [3.0, south_y[1], 1.5]}, "screen.north": {"at": [3.0, north_y[1], 1.5]}}
    if lamps:
        for fid, y in (("hue.se", south_y[0]), ("hue.sw", south_y[0]), ("hue.ne", north_y[0]),
                       ("hue.nw", north_y[0])):
            app[fid] = {"at": [3.0, y, 2.5]}
    return spmod.build_view({"room": {"size": [6.0, 5.0, 3.0], "front": front}, "appliances": app})


def test_the_room_space_gives_the_same_sides_and_wins_over_zones():
    view = _view("west", (0.1, 0.3), (4.7, 4.9))                     # facing west, south is on the left
    pl = players(space_mod=spmod, view=view)
    assert [p["id"] for p in pl] == ["lamps:left", "screen.south", "lamps:right", "screen.north"]
    assert all(p["via"] == "space" for p in pl)
    assert pl[0]["x"] == pytest.approx(-1.0) and pl[-1]["x"] == pytest.approx(1.0)
    # no zones at all: the space alone decides
    nz = players(rig_view(lamp_zones=False), cfg=E.parse_settings({}, []), space_mod=spmod, view=view)
    assert [p["side"] for p in nz] == ["left", "left", "right", "right"]


def test_screens_on_the_front_axis_fall_back_to_the_zones():
    view = _view("north", (0.1, 0.3), (4.7, 4.9), lamps=False)       # front north: both screens on the axis
    pl = players(space_mod=spmod, view=view)
    assert [p["id"] for p in pl] == ["lamps:left", "screen.south", "lamps:right", "screen.north"]
    assert all(p["via"] == "zone" for p in pl)


def test_no_zones_and_no_space_is_all_centre_and_only_unison_forms(tmp_path, scorer):
    cfg = E.parse_settings({}, [])
    pl = players(cfg=cfg)
    assert {p["side"] for p in pl} == {"centre"} and all(p["id"].endswith(":all") or p["kind"] == "screen"
                                                         for p in pl)
    assert len(E.teams(pl, "side", E.flow_order(pl, None))) < 2
    cat = E.Catalogue(c.INTERPLAYS_FILE, scorer=scorer)
    cat.poll()
    ws = cat.weights(DIRECTION, {"dir": "right"}, {"sides": False, "grid": True, "piano": False})
    assert set(ws) and all(cat.entries[n]["kind"] == "unison" for n in ws)


def test_a_lamp_with_no_zone_takes_its_screens_side():
    pl = {p["id"]: p for p in players(rig_view(south_lamp_zone=False))}
    assert pl["lamps:left"]["side"] == "left" and pl["lamps:left"]["via"] == "screen"
    assert pl["lamps:right"]["via"] == "zone"


def test_on_follows_the_live_screens():
    pl = {p["id"]: p for p in players(live_screens=["north-screen"])}
    assert pl["screen.north"]["on"] and not pl["screen.south"]["on"] and pl["lamps:left"]["on"]
    t = E.teams(list(pl.values()), "side", E.flow_order(list(pl.values()), None))
    assert t == [["lamps:left"], ["lamps:right", "screen.north"]]
    assert all(p["on"] for p in players(live_screens=None))


def test_teams_and_the_travel_order_follow_the_flow():
    pl = players()
    assert E.flow_order(pl, {"dir": "right"}) == ["lamps:left", "screen.south", "lamps:right", "screen.north"]
    assert E.flow_order(pl, {"dir": "left"}) == ["screen.north", "lamps:right", "screen.south", "lamps:left"]
    assert E.flow_order(pl, {"dir": "up"}) == E.flow_order(pl, None)
    assert E.teams(pl, "side", E.flow_order(pl, {"dir": "ccw"})) == [["screen.north", "lamps:right"],
                                                                     ["screen.south", "lamps:left"]]
    assert len(E.teams(pl, "player", E.flow_order(pl, None))) == 4


# -- the catalogue (§4.1) ------------------------------------------------------------------------------------------------
def test_the_real_catalogue_loads_with_no_problems(scorer):
    cat = E.Catalogue(c.INTERPLAYS_FILE, scorer=scorer)
    assert cat.poll() is True
    assert cat.error is None and cat.problems == []
    assert set(e["kind"] for e in cat.entries.values()) == set(c.FORM_KINDS)
    assert len(cat.entries) == 10 and cat.revision
    assert cat.poll() is False                                       # unchanged: nothing re-read


def test_an_entry_that_cannot_be_used_is_left_out_with_a_problem(tmp_path, scorer):
    (tmp_path / "i.yaml").write_text("""version: 1
interplays:
  good: {kind: echo, routes: [chord], needs: [sides]}
  badkind: {kind: fugue}
  badparam: {kind: echo, params: {wobble: 2}}
  badrange: {kind: echo, params: {decay: 3}}
  badroute: {kind: unison, routes: [sneeze]}
  badneed: {kind: unison, needs: [moon]}
  badtags: {kind: unison, tags: {energy: [0.9, 0.1]}}
  oddword: {kind: unison, tags: {moods: [gloopy]}}
  BadName: {kind: unison}
""")
    cat = E.Catalogue(tmp_path / "i.yaml", scorer=scorer)
    cat.poll()
    assert set(cat.entries) == {"good", "oddword"}
    probs = " | ".join(cat.problems)
    for word in ("badkind", "wobble", "decay", "badroute", "badneed", "badtags", "BadName", "gloopy"):
        assert word in probs, word
    assert cat.entries["good"]["params"]["delay_beats"] == 1.0         # a missing param takes the kind's default


def test_a_broken_file_keeps_the_last_good_entries_and_a_change_reloads(tmp_path, scorer):
    p = tmp_path / "i.yaml"
    p.write_text("version: 1\ninterplays:\n  one: {kind: unison}\n")
    cat = E.Catalogue(p, scorer=scorer)
    assert cat.poll() and set(cat.entries) == {"one"}
    p.write_text("version: 1\ninterplays: [unclosed\n")
    assert cat.poll() is False and set(cat.entries) == {"one"} and "i.yaml" in cat.error
    p.write_text("version: 1\ninterplays:\n  one: {kind: unison}\n  two: {kind: canon, needs: [sides]}\n")
    assert cat.poll() is True and set(cat.entries) == {"one", "two"} and cat.error is None
    p.unlink()
    assert cat.poll() is True and cat.entries == {} and "not there" in cat.error


def test_the_tags_are_scored_by_the_scenes_rule(tmp_path, scorer):
    p = tmp_path / "i.yaml"
    p.write_text("""version: 1
interplays:
  calm: {kind: unison, tags: {energy: [0.0, 0.3], moods: [calm]}}
  wild: {kind: unison, tags: {energy: [0.7, 1.0], moods: [aggressive]}}
""")
    cat = E.Catalogue(p, scorer=scorer)
    cat.poll()
    facts = {"sides": True, "grid": True, "piano": False}
    low = cat.weights({"energy": 0.1, "moods": ["calm"]}, None, facts)
    high = cat.weights({"energy": 0.9, "moods": ["aggressive"]}, None, facts)
    assert low["calm"] > low["wild"] and high["wild"] > high["calm"]
    assert cat.why("calm", {"energy": 0.1, "moods": ["calm"]}) == "energy 0.1, calm"


# -- the pick (§4.2) ----------------------------------------------------------------------------------------------------
def test_seeded_draws_are_reproducible(tmp_path, scorer):
    def picks(seed: int) -> list[str]:
        b = Band(tmp_path, scorer, None, seed=seed)
        got = []
        for i in range(8):
            b.look(b.t + 2.0, "track", f"L{i}")
            b.run_to(b.t + 2.1)
            got.append(b.en.block()["form"])
        return got
    assert picks(3) == picks(3)


def test_the_piano_form_needs_the_piano_and_leads_while_it_plays(tmp_path, scorer):
    cat = E.Catalogue(c.INTERPLAYS_FILE, scorer=scorer)
    cat.poll()
    d = {"energy": 0.3, "moods": ["calm"], "genre": "piano", "genres": {}}         # the piano playing, heard as one
    off = cat.weights(d, {"dir": "still"}, {"sides": True, "grid": True, "piano": False})
    assert "piano-hands" not in off
    on = cat.weights(d, {"dir": "still"}, {"sides": True, "grid": True, "piano": True})
    assert on["piano-hands"] / sum(on.values()) > 0.7                # one draw from nothing: about 3 in 4
    b = Band(tmp_path, scorer, None, seed=7)                         # the room's own draws, the form playing x stay
    b.direction, b.flow = d, {"dir": "still"}
    wins = 0
    for i in range(200):                                             # 200 song changes while the piano plays
        b.look(b.t + 1.2, "track", f"L{i}", piano=True)
        b.run_to(b.t + 1.3)
        wins += b.en.block()["form"] == "piano-hands"
    assert wins >= 150


def test_no_new_form_before_the_dwell_except_a_song_change(tmp_path, scorer):
    forms = """  a: {kind: unison, routes: []}
  b: {kind: unison, routes: [hit]}
"""
    b = Band(tmp_path, scorer, forms)
    b.start(96.0)
    picks = [x for x in b.en.drain_log() if x["change"] == "pick"]
    assert len(picks) == 1                                           # the first look picks (nothing picked yet)
    b.look(112.0, "section", "L1")                                   # 8 bars in: the dwell (16 bars) holds
    b.run_to(112.1)
    assert [x for x in b.en.drain_log() if x["change"] == "pick"] == []
    b.look(114.0, "track", "L2")                                     # a song change draws again
    assert [x["change"] for x in b.en.drain_log()] == ["pick"]
    b.run_to(146.1)
    b.look(148.0, "section", "L3")                                   # 17 bars after the song change: a draw
    assert any(x["change"] == "pick" for x in b.en.drain_log())


def test_keep_holds_the_form_and_gestures_go_on(tmp_path, scorer):
    forms = """  a: {kind: canon, routes: [section], needs: [sides]}
  b: {kind: unison, routes: [section]}
"""
    b = Band(tmp_path, scorer, forms, seed=4)
    b.start(96.0)
    form = b.en.block()["form"]
    b.en.drain_log()
    b.held = True
    b.tick()
    b.look(132.0, "track", "L1", held=True)                         # even a song change: nothing changes
    b.run_to(132.1)
    assert b.en.drain_log() == [] and b.en.block()["form"] == form and b.en.block()["source"] == "held"
    g = b.latest()["g:look:L1"]                                      # ...but its moment still plays
    assert g["kind"] == "section" and len(g["parts"]) == 4


def test_a_new_form_goes_out_as_next_and_folds_in_after_its_moment(tmp_path, scorer):
    b = Band(tmp_path, scorer, ECHO)
    b.look(96.0, "section", "L1")
    blk = b.en.block()
    assert blk["form"] == "unison" and blk["source"] == "default"
    assert blk["next"]["form"] == "echo-test" and blk["next"]["at_ms"] == 96000.0
    assert blk["next"]["players"]["screen.north"]["stagger_beats"] == 1.0
    assert b.en.part_of("hue.ne", 95.9)["stagger_s"] == 0.0 and b.en.part_of("hue.ne", 96.0)["stagger_s"] == 0.5
    b.run_to(95.9)
    assert b.en.block()["form"] == "unison"
    b.run_to(96.0)
    blk = b.en.block()
    assert blk["form"] == "echo-test" and blk["next"] is None and blk["at_ms"] == 96000.0 and blk["id"] >= 1
    assert blk["line"] == "Playing together: echo, left to right"
    assert set(blk) == set(c.EnsembleData.__annotations__)
    assert all(set(p) == set(c.EnsemblePart.__annotations__) for p in blk["players"].values())
    json.dumps(b.en.view(b.t))


def test_a_look_that_never_lands_takes_its_decision_and_its_pop_back(tmp_path, scorer):
    b = Band(tmp_path, scorer, ECHO.replace("routes: [chord]", "routes: [chord, section]"))
    b.look(97.0, "section", "L1")
    b.tick()
    assert b.en.next_look() == "L1" and b.latest()["g:look:L1"]["parts"]
    b.en.cancel_look("L1", b.t)
    b.tick()
    assert b.en.next_look() is None and b.en.block()["next"] is None
    assert b.latest()["g:look:L1"]["parts"] == []                    # called off: the lights and screens drop it
    b.run_to(98.0)
    assert b.en.block()["form"] == "unison"


# -- routing, one per kind (§4.3) ----------------------------------------------------------------------------------------
def test_unison_with_no_routes_publishes_nothing_and_together_hits_everyone(tmp_path, scorer):
    b = Band(tmp_path, scorer, "  quiet: {kind: unison, routes: []}\n")
    b.start(96.0)
    b.en.on_event("drop_armed", {"id": "drop_armed:1:1", "eta_beats": 4.0}, 100.0, b.t, b.grid())
    b.notes((0.0, 60, 100), (0.01, 64, 100), (0.02, 67, 100))
    b.run_to(101.0)
    assert b.out == []
    t = Band(tmp_path, scorer, "  together: {kind: unison, routes: [hit]}\n")
    t.start(96.0)
    t.en.on_event("drop_armed", {"id": "drop_armed:1:1", "eta_beats": 4.0}, 100.0, t.t, t.grid())
    t.run_to(96.3)
    g = t.latest()["g:hit:drop_armed:1:1"]
    assert at_of(g) == {"lamps:left": 100.0, "screen.south": 100.0, "lamps:right": 100.0, "screen.north": 100.0}
    assert all(p["predicted"] and p["gen"] == 0 and p["colour"] == "highlight" for p in g["parts"])
    assert set(g) == set(c.GestureData.__annotations__) and g["shape"]["depth"] == 1.0


def _chord(b: Band, pitches=(60, 64, 67), vel=100) -> dict:
    t0 = b.t
    b.notes(*[(0.01 * i, p, vel) for i, p in enumerate(pitches)])
    b.run_to(t0 + 0.3)
    return [g for g in b.out if g["kind"] == "chord"][-1]


def test_echo_goes_left_then_right_and_a_left_flow_reverses_it(tmp_path, scorer):
    b = Band(tmp_path, scorer, ECHO)
    b.start(96.0)
    b.run_to(97.0)
    g = _chord(b)
    t0 = g["parts"][0]["at_ms"] / 1000.0
    s = (100 / 127) ** 1.5 * 0.7
    assert at_of(g) == pytest.approx({"lamps:left": t0, "screen.south": t0, "lamps:right": t0 + 0.5,
                                      "screen.north": t0 + 0.5})            # 1 beat at 120 bpm
    assert st_of(g) == pytest.approx({"lamps:left": s, "screen.south": s, "lamps:right": s * 0.6,
                                      "screen.north": s * 0.6}, abs=1e-4)
    assert [p["predicted"] for p in g["parts"]] == [False, False, True, True]   # the echo is ahead when decided
    assert g["register"] == pytest.approx((sum((60, 64, 67)) / 3 - 21) / 87, abs=1e-4) and g["why"] == "a chord, middle"
    b.flow = {"dir": "left"}
    b.run_to(b.t + 1.0)
    g = _chord(b)
    t0 = min(p["at_ms"] for p in g["parts"]) / 1000.0
    assert {k for k, v in at_of(g).items() if v == pytest.approx(t0)} == RIGHT
    assert b.en.block()["line"] == "Playing together: echo, right to left"


def test_echo_by_register_starts_a_low_chord_on_the_left(tmp_path, scorer):
    b = Band(tmp_path, scorer, ECHO.replace("caller: first", "caller: register"))
    b.flow = {"dir": "left"}                                         # the travel order starts on the right
    b.start(96.0)
    g = _chord(b, (36, 40, 43))
    first = min(p["at_ms"] for p in g["parts"])
    assert {p["player"] for p in g["parts"] if p["at_ms"] == first} == LEFT
    b.run_to(b.t + 1.0)
    g = _chord(b, (84, 88, 91))
    first = min(p["at_ms"] for p in g["parts"])
    assert {p["player"] for p in g["parts"] if p["at_ms"] == first} == RIGHT


CR = """  cr:
    kind: call_and_response
    line: "call and answer, {first} starts"
    params: {team_by: side, order: room, answer: replay, decay: 0.85, half_s: 4, every_bars: 1}
    routes: [downbeat, hit]
    needs: [sides]
"""


def test_call_and_answer_halves_replay_and_swap(tmp_path, scorer):
    b = Band(tmp_path, scorer, CR)
    b.accents = "downbeat"
    b.start(96.0)                                                    # phrase 6 starts here: the left calls
    assert b.en.block()["line"] == "Playing together: call and answer, left starts"
    b.run_to(100.5)
    g = b.latest()["g:beat:1:196"]                                   # the downbeat at 98 s, in the call's half
    s = (0.5 + 0.5 * 0.4) * 0.55
    assert at_of(g) == pytest.approx({"lamps:left": 98.0, "screen.south": 98.0, "lamps:right": 106.0,
                                      "screen.north": 106.0})        # the answer: + 16 beats
    assert st_of(g)["screen.south"] == pytest.approx(s, abs=1e-4)
    assert st_of(g)["lamps:right"] == pytest.approx(s * 0.85 * 0.5, abs=1e-4)   # x decay x accompany's gestures
    roles = {k: p["role"] for k, p in b.en.block()["players"].items()}
    assert roles["screen.south"] == "lead" and roles["screen.north"] == "accompany"
    b.en.on_event("drop_armed", {"id": "drop_armed:1:2", "eta_beats": 4.0}, 107.0, b.t, b.grid())
    b.run_to(110.0)
    assert "g:beat:1:212" not in b.latest()                          # 106 s: the answer's half takes no live seed
    assert len(b.latest()["g:hit:drop_armed:1:2"]["parts"]) == 4     # ...but a hit is everyone's
    assert {k: p["role"] for k, p in b.en.block()["players"].items()}["screen.north"] == "lead"   # swapped at 104
    b.run_to(113.0)
    g = b.latest()["g:beat:1:224"]                                   # 112 s: phrase 7, the right calls
    assert {p["player"] for p in g["parts"] if p["at_ms"] == 112000.0} == RIGHT
    assert b.en.block()["line"] == "Playing together: call and answer, right starts"


PING = """  pp:
    kind: ping_pong
    params: {team_by: side, order: flow, "on": beat, downbeat_boost: 1.5, scene_offset_beats: 1}
    routes: [accent, downbeat, fill]
    needs: [sides, grid]
"""


def test_ping_pong_alternates_boosts_downbeats_and_rolls_a_fill(tmp_path, scorer):
    b = Band(tmp_path, scorer, PING)
    b.start(96.0)
    b.run_to(99.0)
    sides = []
    for k in range(192, 200):                                        # 8 beats from 96 s
        g = b.latest()[f"g:beat:1:{k}"]
        who = {p["player"] for p in g["parts"]}
        sides.append("L" if who == LEFT else "R" if who == RIGHT else "?")
    assert "".join(sides) == "LRLRLRLR"
    down, acc = b.latest()["g:beat:1:196"], b.latest()["g:beat:1:197"]
    e = 0.5 + 0.5 * 0.4
    assert down["kind"] == "downbeat" and st_of(down)["screen.south"] == pytest.approx(e * 0.55 * 1.5, abs=1e-4)
    assert acc["kind"] == "accent" and st_of(acc)["screen.north"] == pytest.approx(e * 0.35, abs=1e-4)
    b.energy = 0.7                                                   # the phrase's last bar rolls across the room
    b.run_to(111.0)
    fills = [b.latest()[f"g:beat:1:{k}"] for k in range(220, 224)]
    assert [g["kind"] for g in fills] == ["fill"] * 4
    assert [g["parts"][0]["player"] for g in fills] == ["lamps:left", "screen.south", "lamps:right", "screen.north"]
    assert [g["parts"][0]["strength"] for g in fills] == pytest.approx([0.45 * k / 4 for k in (1, 2, 3, 4)],
                                                                      abs=1e-4)


CANON = """  round:
    kind: canon
    line: "a round, {second} follows {first} by {beats}"
    params: {team_by: side, order: flow, offset_beats: 2, offset_s: 1.0, every_bars: 1, clock_offset: true}
    routes: [downbeat]
    needs: [sides]
"""


def test_canon_plays_a_round_at_equal_strength(tmp_path, scorer):
    b = Band(tmp_path, scorer, CANON)
    b.accents = "downbeat"
    b.start(96.0)
    b.run_to(98.5)
    g = b.latest()["g:beat:1:196"]
    assert at_of(g) == pytest.approx({"lamps:left": 98.0, "screen.south": 98.0, "lamps:right": 99.0,
                                      "screen.north": 99.0})
    assert len(set(st_of(g).values())) == 1 and [p["gen"] for p in g["parts"]] == [0, 0, 1, 1]
    ps = b.en.block()["players"]
    assert ps["screen.north"]["clock_offset_beats"] == 2.0 and ps["screen.south"]["clock_offset_beats"] == 0.0
    assert ps["lamps:right"]["stagger_beats"] == 2.0 and ps["lamps:right"]["stagger_s"] == 1.0
    assert ps["screen.north"]["scale"]["pulse"] == pytest.approx(0.7)
    assert b.en.block()["line"] == "Playing together: a round, right follows left by 2 beats"


ECHO_SAME = """  echo:
    kind: echo
    line: "echo, {order}"
    params: {team_by: side, order: flow, delay_beats: 1, delay_s: 0.5, decay: 0.6, every_bars: 1, clock_offset: true}
    routes: [downbeat]
    needs: [sides]
"""


def test_echo_with_clock_offset_is_the_same_picture_a_step_behind(tmp_path, scorer):
    """ff, 22 Oct: echo's later side runs its beat-bound motion delay_beats behind (clock_offset_beats)."""
    b = Band(tmp_path, scorer, ECHO_SAME)
    b.accents = "downbeat"
    b.start(96.0)
    b.run_to(98.5)
    ps = b.en.block()["players"]
    assert ps["screen.south"]["clock_offset_beats"] == 0.0 and ps["screen.north"]["clock_offset_beats"] == 1.0


SOLO = """  solo:
    kind: solo_and_accompany
    line: "{lead} leads"
    params: {team_by: side, order: room, pass_on: section, pass: next, accompany_beats: 8, lead_kinds: [screen, lights]}
    routes: [downbeat, section]
    needs: [sides]
"""


def test_solo_roles_and_the_lead_passes_at_a_section(tmp_path, scorer):
    b = Band(tmp_path, scorer, SOLO)
    b.accents = "downbeat"
    b.start(96.0)
    b.run_to(99.0)
    ps = b.en.block()["players"]
    assert {k: p["role"] for k, p in ps.items()} == {"lamps:left": "lead", "screen.south": "lead",
                                                     "lamps:right": "accompany", "screen.north": "accompany"}
    assert ps["screen.north"]["stagger_beats"] == 8.0 and ps["screen.north"]["scale"]["level"] == 0.7
    assert ps["screen.north"]["scale"]["pulse"] == pytest.approx(0.6)
    assert {p["player"] for p in b.latest()["g:beat:1:196"]["parts"]} == LEFT   # the lead takes the downbeats
    assert b.en.block()["line"] == "Playing together: the left side leads"
    b.look(104.0, "section", "L1")
    nx = b.en.block()["next"]
    assert nx["at_ms"] == 104000.0 and nx["players"]["screen.north"]["role"] == "lead"
    g = b.latest()
    b.run_to(104.5)
    sec = b.latest()["g:look:L1"]                                    # everyone takes a section, each at its cut
    assert at_of(sec) == pytest.approx({"lamps:left": 108.0, "screen.south": 108.0, "lamps:right": 104.0,
                                        "screen.north": 104.0})
    assert b.en.block()["players"]["screen.south"]["role"] == "accompany" and g


TWO = """  two:
    kind: contrast
    params: {team_by: side, order: room, palette_roles: [base, highlight], energy_split: 0.15, swap_on: section}
    routes: [section]
    needs: [sides]
"""


def test_contrast_gives_each_side_a_palette_role_and_swaps_at_a_section(tmp_path, scorer):
    b = Band(tmp_path, scorer, TWO)
    b.start(96.0)
    ps = b.en.block()["players"]
    assert {k: p["palette_role"] for k, p in ps.items()} == {"lamps:left": "base", "screen.south": "base",
                                                             "lamps:right": "highlight", "screen.north": "highlight"}
    assert ps["screen.south"]["energy_bias"] == pytest.approx(-0.075)
    assert ps["screen.north"]["energy_bias"] == pytest.approx(0.075) and ps["lamps:right"]["energy_bias"] == 0.0
    b.look(104.0, "section", "L1")
    b.run_to(104.1)
    ps = b.en.block()["players"]
    assert ps["screen.south"]["palette_role"] == "highlight"
    assert ps["screen.south"]["energy_bias"] == pytest.approx(0.075)
    g = b.latest()["g:look:L1"]
    assert {p["player"]: p["colour"] for p in g["parts"]} == {"lamps:left": "highlight", "screen.south": "highlight",
                                                              "lamps:right": "base", "screen.north": "base"}


HANDS = """  hands:
    kind: register_split
    line: "piano: low notes {low}, high notes {high}"
    params: {team_by: side, order: room, low: left, outside: 0.25}
    routes: [note, chord, hit, section]
    needs: [piano, sides]
    boost: {piano: 4.0}
"""


def test_register_split_sends_low_notes_left_and_high_ones_right(tmp_path, scorer):
    b = Band(tmp_path, scorer, HANDS, cfg=settings(piano={"split": 60}))
    b.start(96.0, piano=True)
    assert b.en.block()["form"] == "hands"
    assert b.en.block()["line"] == "Playing together: piano: low notes left, high notes right"
    reg = (60 - 21) / 87
    assert b.en.block()["players"]["lamps:left"]["register"] == pytest.approx([0.0, reg], abs=1e-4)
    assert b.en.block()["players"]["screen.north"]["register"] == pytest.approx([reg, 1.0], abs=1e-4)
    b.run_to(97.0)                                                   # clear of the song change's own pop
    assert {p["player"] for p in _chord(b, (36, 40, 43))["parts"]} == LEFT
    b.run_to(b.t + 1.0)
    assert {p["player"] for p in _chord(b, (72, 76, 79))["parts"]} == RIGHT
    b.run_to(b.t + 1.0)
    t0 = b.t
    b.notes((0.0, 40, 60), (0.01, 76, 110), (0.02, 79, 90))         # both hands: each side its own loudest
    b.run_to(t0 + 0.3)
    g = [x for x in b.out if x["kind"] == "chord"][-1]
    st = st_of(g)
    assert set(st) == LEFT | RIGHT
    assert st["screen.south"] == pytest.approx((60 / 127) ** 1.5 * 0.7, abs=1e-4)
    assert st["screen.north"] == pytest.approx((110 / 127) ** 1.5 * 0.7, abs=1e-4)


def test_an_auto_split_follows_the_notes_within_its_range(tmp_path, scorer):
    b = Band(tmp_path, scorer, HANDS)
    b.start(96.0, piano=True)
    b.en.on_notes([(b.t + 0.3 * i, "test-piano", 1, 30, 10) for i in range(64)], b.t)   # quiet: no gestures
    b.run_to(b.t + 0.2)
    assert b.en.block()["players"]["lamps:left"]["register"][1] == pytest.approx((48 - 21) / 87, abs=1e-4)


def test_without_a_grid_delays_are_seconds_and_there_are_no_clock_gestures(tmp_path, scorer):
    b = Band(tmp_path, scorer, ECHO.replace("routes: [chord]", "routes: [chord, accent, downbeat, phrase]"))
    b.grid_on = False
    b.start(96.0)
    b.run_to(100.0)
    assert [g for g in b.out if g["source"] == "clock"] == []
    g = _chord(b)
    t0 = min(p["at_ms"] for p in g["parts"]) / 1000.0
    assert at_of(g)["screen.north"] == pytest.approx(t0 + 0.75)      # delay_s
    assert g["beat"] is None
    assert b.en.block()["players"]["screen.north"]["stagger_s"] == 0.75
    assert b.en.block()["glide_ms"] == 1000.0                        # glide_s


# -- density (§3.5) -------------------------------------------------------------------------------------------------------
NOTES = "  all: {kind: unison, routes: [note, hit]}\n"


def test_spacing_keeps_the_first_and_a_hit_always_plays(tmp_path, scorer):
    b = Band(tmp_path, scorer, NOTES)
    b.start(96.0)
    b.run_to(97.0)
    b.notes((0.0, 60, 100), (0.1, 62, 100))                          # two bursts, 100 ms apart
    b.en.on_event("drop", {"id": "drop:1:5"}, b.t + 0.05, b.t, b.grid())
    b.run_to(b.t + 0.4)
    notes = [g for g in b.out if g["kind"] == "note"]
    assert len(notes) == 1 and len(notes[0]["parts"]) == 4
    assert b.en.counts["spaced"] == 4                                # one per player
    hit = b.latest()["g:hit:drop:1:5"]
    assert len(hit["parts"]) == 4 and not any(p["predicted"] for p in hit["parts"])


def test_at_most_max_per_bar_and_a_weak_part_is_left_out(tmp_path, scorer):
    b = Band(tmp_path, scorer, NOTES)
    b.start(96.0)
    b.run_to(97.95)
    t0 = b.t                                                         # 98.0 is a bar line: 6 notes in one bar
    b.en.on_notes([(98.05 + 0.25 * i, "test-piano", 1, 60, 100) for i in range(6)], b.t)
    b.run_to(t0 + 2.0)
    kept = [g for g in b.out if g["kind"] == "note"]
    assert len(kept) == 4 and b.en.counts["over_bar"] == 2 * 4
    w0 = b.en.counts["weak"]
    b.run_to(b.t + 1.0)
    b.notes((0.0, 60, 20))                                           # (20 / 127) ^ 1.5 x 0.5 < min_strength
    b.run_to(b.t + 0.3)
    assert b.en.counts["weak"] == w0 + 4 and len([g for g in b.out if g["kind"] == "note"]) == 4


# -- the piano's bursts (§3.4) ---------------------------------------------------------------------------------------------
def test_piano_bursts_make_notes_and_chords(tmp_path, scorer):
    b = Band(tmp_path, scorer, "  all: {kind: unison, routes: [note, chord]}\n")
    b.start(96.0)
    b.notes((0.0, 60, 127), (0.05, 64, 127))                         # within burst_ms: one note gesture of two
    b.run_to(b.t + 0.3)
    g = [x for x in b.out if x["source"] == "piano"]
    assert len(g) == 1 and g[0]["kind"] == "note" and g[0]["pitches"] == [60, 64]
    assert g[0]["strength"] == pytest.approx(0.5) and g[0]["id"] == "g:piano:1"
    b.run_to(b.t + 1.0)
    g = _chord(b, (48, 52, 55), vel=64)
    assert g["kind"] == "chord" and g["register"] == pytest.approx((sum((48, 52, 55)) / 3 - 21) / 87, abs=1e-4)
    assert g["strength"] == pytest.approx((64 / 127) ** 1.5 * 0.7, abs=1e-4) and g["why"] == "a chord, low"
    n = len(b.out)
    b.run_to(b.t + 1.0)
    b.notes((0.0, 60, 19))                                           # under min_vel: nothing
    b.run_to(b.t + 0.3)
    assert len(b.out) == n


# -- the clock's lines (§3.1) ------------------------------------------------------------------------------------------------
CLOCKED = "  all: {kind: unison, routes: [accent, downbeat, phrase, fill]}\n"


def test_each_beat_once_ahead_of_the_lead(tmp_path, scorer):
    b = Band(tmp_path, scorer, CLOCKED)
    b.accents = "beat"
    b.start(96.0)
    b.run_to(106.0)
    ids = [g["id"] for g in b.out if g["source"] == "clock"]
    assert len(ids) == len(set(ids)) and all(i.startswith("g:beat:1:") for i in ids)
    beats = sorted(int(i.rsplit(":", 1)[1]) for i in ids)
    assert beats == list(range(beats[0], beats[-1] + 1))             # every beat, none twice
    assert all(min(p["at_ms"] for p in g["parts"]) - g["t_ms"] >= LEAD * 1000.0 - 1.0
               for g in b.out if g["source"] == "clock")
    kinds = {b.latest()[f"g:beat:1:{k}"]["kind"] for k in beats}
    assert kinds == {"accent", "downbeat", "phrase"}                 # beat: no fills


def test_the_feels_accents_word_bounds_the_clock(tmp_path, scorer):
    b = Band(tmp_path, scorer, CLOCKED)
    b.accents = "none"
    b.start(96.0)
    b.run_to(115.0)
    assert [g for g in b.out if g["source"] == "clock"] == []
    b.accents = "downbeat"
    b.run_to(131.0)
    assert {g["kind"] for g in b.out if g["source"] == "clock"} == {"downbeat", "phrase"}
    b.out.clear()
    b.accents, b.gate = "full", "dmx"                                # accents and fills need the gate at beat
    b.run_to(140.0)
    assert {g["kind"] for g in b.out if g["source"] == "clock"} <= {"downbeat", "phrase"}
    b.out.clear()
    b.gate, b.beat_lock = "beat", False                              # the feel's beat_lock: no clock gestures
    b.run_to(150.0)
    assert [g for g in b.out if g["source"] == "clock"] == []


# -- lifecycle -------------------------------------------------------------------------------------------------------------
def test_an_armed_drop_line_its_call_off_and_a_drop_on_the_line(tmp_path, scorer):
    b = Band(tmp_path, scorer, "  all: {kind: unison, routes: [hit]}\n")
    b.start(96.0)
    b.en.on_event("drop_armed", {"id": "drop_armed:1:1", "eta_beats": 8.0}, 104.0, b.t, b.grid())
    b.run_to(b.t + 0.2)
    g = b.latest()["g:hit:drop_armed:1:1"]
    assert all(p["predicted"] and p["at_ms"] == 104000.0 for p in g["parts"])
    b.en.on_event("drop", {"id": "drop:1:3"}, 104.2, b.t, b.grid())   # heard inside the armed beat: nothing new
    b.run_to(b.t + 0.2)
    assert "g:hit:drop:1:3" not in b.latest()
    seq = g["seq"]
    b.en.on_event("drop_armed", {"id": "drop_armed:1:1", "eta_beats": None, "detail": "disarmed"}, 104.0, b.t,
                  b.grid())
    b.run_to(b.t + 0.2)
    off = b.latest()["g:hit:drop_armed:1:1"]
    assert off["parts"] == [] and off["seq"] > seq
    assert any(x["id"] == off["id"] and x["parts"] == [] for x in b.en.recent(b.t))   # the look engine sees it


def test_neutral_house_and_low_seed_nothing(tmp_path, scorer):
    b = Band(tmp_path, scorer, "  all: {kind: unison, routes: [hit, chord, section, downbeat]}\n")
    b.start(96.0)
    b.active = False
    b.out.clear()
    b.tick()
    blk = b.en.block()
    assert blk["source"] == "neutral" and blk["line"] is None and blk["kind"] == "unison"
    b.en.on_event("drop", {"id": "drop:9"}, b.t, b.t, b.grid())
    b.notes((0.0, 60, 100), (0.01, 64, 100), (0.02, 67, 100))
    b.look(b.t + 2.0, "section", "L5")
    b.run_to(b.t + 4.0)
    assert b.out == []
    b.active = True
    b.tick()
    assert b.en.block()["source"] != "neutral" and b.en.block()["line"].startswith(E.LINE_PREFIX)


def test_reset_goes_back_to_unison_and_calls_off_what_was_coming(tmp_path, scorer):
    b = Band(tmp_path, scorer, CANON)
    b.accents = "downbeat"
    b.start(96.0)
    b.run_to(97.5)
    coming = [g["id"] for g in b.out if any(p["at_ms"] / 1000.0 > b.t for p in g["parts"])]
    assert coming
    b.en.reset(b.t, "a new mode")
    blk = b.en.block()
    assert blk["form"] == "unison" and blk["source"] == "default" and blk["next"] is None
    b.out.clear()
    b.tick()
    assert {g["id"] for g in b.out if g["parts"] == []} == set(coming)


def test_no_ensemble_block_is_todays_room(tmp_path, scorer):
    b = Band(tmp_path, scorer, None, cfg=None)
    b.en.configure(E.parse_settings(None, []))
    b.start(96.0)
    b.en.on_event("drop_armed", {"id": "drop_armed:1:1", "eta_beats": 8.0}, 100.0, b.t, b.grid())
    b.notes((0.0, 60, 100), (0.01, 64, 100), (0.02, 67, 100))
    b.run_to(110.0)
    assert b.out == [] and b.en.block()["form"] == "unison" and b.en.block()["line"] is None
    assert b.en.lamp_parts() == () and b.en.recent(b.t) == []


# -- settings ----------------------------------------------------------------------------------------------------------------
def test_settings_fall_back_with_a_problem_line():
    probs: list[str] = []
    cfg = E.parse_settings({"players": {"zones": {"south": "up"}}, "pick": {"dwell_bars": -1},
                            "roles": {"accompany": {"level": 2}, "boss": {}},
                            "gestures": {"colours": {"hit": "purple"}, "shapes": {"hit": {"depth": 1}}},
                            "piano": {"split": "middle"}, "wibble": 1}, probs)
    assert cfg["on"] and cfg["players"]["zones"] == {} and cfg["pick"]["dwell_bars"] == 16.0
    assert cfg["roles"]["accompany"]["level"] == 0.7 and cfg["gestures"]["colours"]["hit"] == "highlight"
    assert cfg["gestures"]["shapes"]["hit"] == E.ENSEMBLE_FALLBACK["gestures"]["shapes"]["hit"]
    assert cfg["piano"]["split"] == "auto"
    assert len(probs) == 8
    real = yaml.safe_load(c.SHOW_FILE.read_text()).get("ensemble")
    probs = []
    got = E.parse_settings(real, probs)
    assert probs == [] and got["players"]["zones"] == ZONES
    assert E.parse_settings(None, [])["on"] is False


# -- contract.gesture_weight (§6.1) ---------------------------------------------------------------------------------------
def test_gesture_weight_peaks_holds_and_decays():
    sh = {"attack_ms": 0, "hold_ms": 100, "decay_ms": 200, "depth": 1, "colour_depth": 0, "kick": 0}
    w = c.gesture_weight
    assert w(10.0, 10.0, sh, step=True) == 1.0 and w(9.99, 10.0, sh, step=True) == 0.0
    assert w(10.0 - 0.081, 10.0, sh, step=True, min_attack_s=0.08) == 0.0
    assert 0.0 < w(10.0 - 0.04, 10.0, sh, step=True, min_attack_s=0.08) < 1.0
    assert w(10.0, 10.0, sh, step=True, min_attack_s=0.08) == 1.0
    assert w(10.0, 10.0, sh, step=False, swell_s=0.3) == 0.0          # a reactive part swells from its moment
    assert w(10.15, 10.0, sh, step=False, swell_s=0.3) == pytest.approx(0.5)
    assert w(10.3, 10.0, sh, step=False, swell_s=0.3) == 1.0
    assert w(11.0, 10.0, sh, step=True, dur_s=2.0) == pytest.approx(0.5)   # a swell over its length
    assert w(10.09, 10.0, sh, step=True) == 1.0                       # held
    assert w(10.3, 10.0, sh, step=True) == pytest.approx(math.exp(-1.0))
    assert w(10.1 + 8 * 0.2 + 0.01, 10.0, sh, step=True) == 0.0


# -- the bus (§6.3) -------------------------------------------------------------------------------------------------------
def test_room_gesture_goes_only_to_a_client_that_names_it():
    assert c.ROOM_GESTURE in busmod.WANT_ONLY
    assert not busmod._Client(None, "renderer", "x", busmod._parse_want(None)).wants(c.ROOM_GESTURE)
    assert busmod._Client(None, "renderer", "x", busmod._parse_want(["room.gesture"])).wants(c.ROOM_GESTURE)
    assert not busmod._Client(None, "renderer", "x", busmod._parse_want(["room"])).wants(c.ROOM_GESTURE)


# -- no names in code ----------------------------------------------------------------------------------------------------
def test_no_device_zone_group_or_form_names_in_code():
    """Nothing in ensemble.py names a fixture, zone, screen, light group, mode, vibe or form: test_director's rule
    (README S5.0 rule 1) plus the catalogue's form names; its own need words are vocabulary (docs/ENSEMBLE.md §4.1)."""
    from show_service.tests.test_director import _fixed_vocabulary, _names_from_config
    names = _names_from_config() | set((yaml.safe_load(c.INTERPLAYS_FILE.read_text()).get("interplays") or {}))
    names -= _fixed_vocabulary() | set(E.NEEDS) | {E.BUILTIN_NAME}
    tree = ast.parse(pathlib.Path(E.__file__).read_text())
    hits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value in names]
    assert hits == []
