"""roomlight: the light in the room as a number, and the level that makes room for a featured low-power light
(docs/LIGHTS.md §7.5). Pure sums against the real config/room/photometry.yaml, the line-up parser's new keys, and the
look engine taking a program's level. Nothing is written anywhere."""

from __future__ import annotations

import pytest

from show_service import light_scenes as ls
from show_service import roomlight as rl
from show_service.tests.test_looks import DMX_CAPS, HUE_T, fx, look, out, rigview
from show_service.tests.test_looks_motion import engine, run, scene

PAR = "ofl/chauvet-dj/gigbar-move-ils-par.json"
SPOT = "ofl/chauvet-dj/gigbar-move-ils-spot.json"
ROOM = (5.0, 10.0, 4.0)                 # the laptop rig's room


def test_the_real_photometry_file_loads_clean():
    probs: list[str] = []
    ph = rl.load(problems=probs)
    assert probs == []
    assert {PAR, SPOT, "generic-dimmer"} <= set(ph["types"])
    assert ph["reflect"] == {"ceiling": 0.7, "wall": 0.5, "floor": 0.2}


def test_a_missing_file_is_no_photometry_and_a_bad_entry_is_left_out(tmp_path):
    assert rl.load(tmp_path / "none.yaml")["types"] == {}
    f = tmp_path / "p.yaml"
    f.write_text("types:\n  a: {wheel: 100}\n  b: {wheel: 1, white: 2}\n  c: {emitters: {r: 1, g: 2}}\n"
                 "room: {reflect: {wall: 2}}\n")
    probs: list[str] = []
    ph = rl.load(f, probs)
    assert set(ph["types"]) == {"a"} and len(probs) == 3
    assert ph["reflect"]["wall"] == 0.5


def test_a_colours_light_is_its_own_share_of_the_fixtures_output():
    ph = rl.load()["types"]
    par, spot = ph[PAR], ph[SPOT]
    assert rl.lumens(par, (255, 0, 0)) == pytest.approx(66)
    assert rl.lumens(par, (0, 0, 255)) == pytest.approx(22)
    assert rl.lumens(par, (255, 255, 255)) == pytest.approx(310)
    assert rl.lumens(par, (127.5, 0, 0)) == pytest.approx(33)             # an LED dims with its channel
    assert rl.lumens(spot, (255, 255, 255)) == pytest.approx(1190)        # open white
    assert rl.lumens(spot, (0, 0, 255)) == pytest.approx(1190 * 0.0722)   # a deep blue passes little of it
    assert rl.lumens(spot, (0, 255, 255)) > 10 * rl.lumens(spot, (0, 0, 255))
    assert rl.lumens(spot, (0, 127.5, 127.5)) == pytest.approx(rl.lumens(spot, (0, 255, 255)) / 2)   # the dimmer
    assert rl.lumens(spot, (0, 0, 0)) == 0.0 and rl.lumens(None, (255, 255, 255)) == 0.0
    assert rl.lumens({"white": 800}, (128, 128, 128)) == pytest.approx(800 * 128 / 255)
    assert rl.lumens({"fixed": 15}, (255, 0, 0)) == 15


def test_the_room_turns_lumens_into_lux_by_its_size_and_surfaces():
    ph = rl.load()
    k = rl.lux_per_lumen(ROOM, ph["reflect"])
    assert 1 / k == pytest.approx(115, abs=1)                             # the laptop room: 1 lux per 115 lumens
    assert rl.room_lux(4 * 1190, ROOM, ph["reflect"]) == pytest.approx(41, abs=1)   # four heads in open white
    dark = {"ceiling": 0.1, "wall": 0.1, "floor": 0.1}
    assert rl.lux_per_lumen(ROOM, dark) < 0.6 * k                         # dark walls bounce far less


def test_a_scenes_brightness_layers_leave_their_average():
    assert rl.scene_share([]) == 1.0
    assert rl.scene_share([{"fx": "dim", "depth": 1.0}]) == 0.0           # resting, dark
    assert rl.scene_share([{"fx": "dim", "depth": 0.82}]) == pytest.approx(0.18)
    assert rl.scene_share([{"fx": "sine", "depth": 0.3}]) == pytest.approx(0.85)
    assert rl.scene_share([{"kind": "position", "mode": "aim"}, {"kind": "macro", "set": {}}]) == 1.0
    assert rl.scene_share([{"fx": "swell", "depth": "swell"}]) == pytest.approx(1 - 0.5 + 0.5 * 0.35)
    assert rl.scene_share([{"fx": "sine", "depth": 1.0}, {"fx": "square", "depth": 1.0}]) == pytest.approx(0.25)


def test_headroom_brings_the_others_down_only_as_far_as_the_featured_light_needs():
    # the laser reads under 3 lux; the screens leave 1; the laser itself 0.3
    assert rl.headroom(3, 1.0, 0.3, 4.9, 0.12) == pytest.approx(1.7 / 4.9)   # blue heads: down to a third
    assert rl.headroom(3, 1.0, 0.3, 25.0, 0.12) == 0.12                       # cyan heads: to the least allowed
    assert rl.headroom(3, 1.0, 0.3, 0.8, 0.12) == 1.0                         # blue pars: already under it
    assert rl.headroom(3, 1.0, 0.3, 0.0, 0.12) == 1.0                         # nothing else is lit
    assert rl.headroom(0.5, 1.0, 0.0, 10.0, 0.12) == 0.12                     # the screens alone are over it


def test_a_lineup_names_the_groups_it_features_and_a_group_what_it_needs():
    probs: list[str] = []
    cfg = ls.parse_lights({"groups": {"movers": {"select": "cap:pan"},
                                      "lasers": {"select": "tag:laser", "reads_under_lux": 3, "lands_on": ["ceiling"]},
                                      "bad": {"select": "tag:x", "reads_under_lux": -1, "lands_on": "ceiling"}},
                           "lineups": {"a": {"plays": ["movers", "lasers"], "features": ["lasers"]},
                                       "b": {"plays": ["movers"], "features": ["lasers"]},
                                       "c": {"plays": ["movers"]}},
                           "room_light": {"min_level": 0.2}}, probs)
    assert cfg["groups"]["lasers"]["reads_under_lux"] == 3.0 and cfg["groups"]["lasers"]["lands_on"] == ["ceiling"]
    assert "reads_under_lux" not in cfg["groups"]["bad"] and "lands_on" not in cfg["groups"]["bad"]
    assert cfg["lineups"]["a"]["features"] == ["lasers"]
    assert cfg["lineups"]["b"]["features"] == [] and cfg["lineups"]["c"]["features"] == []
    assert cfg["room_light"] == {"min_level": 0.2}
    assert len(probs) == 3                                                # the two bad group keys, b's stray feature


def test_a_scenes_moves_say_where_it_lands():
    up = {"layers": [{"kind": "position", "mode": "circle", "on": "ceiling"}, {"kind": "beam", "zoom": 12}]}
    assert ls.lands_of(up) == {"ceiling"}
    assert ls.lands_of({"layers": [{"fx": "sine", "depth": 0.3}]}) == frozenset()
    assert ls.lands_of(None) == frozenset()


def test_the_engine_brings_a_group_to_its_programs_level_over_a_couple_of_seconds():
    rig = rigview([fx("l1", HUE_T, "dmx"), fx("m1", HUE_T, "dmx", tags=["m"])], [HUE_T], [out("dmx", "artnet")])
    lights = {"groups": {"movers": {"label": "Movers", "select": "tag:m"}, "lamps": {"label": "Lamps", "select": "kind:lamp"}}}

    def progs(level=None):
        p = {g: {"group": g, "scene": "still", "effects": [], "at_ms": 10_000.0,
                 "transition": {"name": "cut", "kind": "cut", "params": {}, "at_ms": 10_000.0}} for g in ("movers", "lamps")}
        if level is not None:
            p["movers"]["level"] = level
        return p
    box = {"look": look(light_programs=progs())}
    e = engine(rig, box, {"still": scene()}, lights=lights)
    fr = dict(run(e, box, 10.0, 10.2, step=0.1))[10.2]
    assert fr["fixtures"]["m1"]["intensity"] == 1.0 and fr["fixtures"]["l1"]["intensity"] == 1.0
    box["look"] = look(light_programs=progs(0.2))                         # the movers make room; the lamps don't
    frames = dict(run(e, box, 10.3, 13.3, step=0.5))
    assert 0.2 < frames[10.8]["fixtures"]["m1"]["intensity"] < 1.0        # on its way down
    assert frames[13.3]["fixtures"]["m1"]["intensity"] == pytest.approx(0.2)
    assert frames[13.3]["fixtures"]["l1"]["intensity"] == 1.0
    box["look"] = look(light_programs=progs())                            # and back up when nothing is featured
    assert dict(run(e, box, 13.4, 16.4, step=0.5))[16.4]["fixtures"]["m1"]["intensity"] == pytest.approx(1.0)
    assert DMX_CAPS


# -- the director's sum (director._room_light), on a stand-in with just what it reads ----------------------------------
class _Dir:
    """What Director._room_light and _features read, for the laptop rig's two bars."""

    def __init__(self, lineup, house=False, low=False):
        from show_service import director as dm
        self._dm = dm
        self._photo = rl.load()
        t = {"par": {"file": PAR}, "spot": {"file": SPOT},
             "laser": {"file": "ofl/chauvet-dj/gigbar-move-ils-laser.json"}}
        ids = {"lamps": [f"p{i}" for i in range(4)], "movers": [f"s{i}" for i in range(4)], "lasers": ["z0", "z1"]}
        kind = {"lamps": "par", "movers": "spot", "lasers": "laser"}
        self._rig_seen = {"types": t, "fixtures": {f: {"type": kind[g]} for g, fs in ids.items() for f in fs}}
        self._groups = {g: {"members": fs, "follows": None} for g, fs in ids.items()}
        self._lp = {"movers": {"scene": "still"}, "lamps": {"scene": "rest"}, "lasers": {"scene": "still"}}
        self.light_scenes = type("C", (), {"scenes": {"still": {"layers": []},
                                                      "rest": {"layers": [{"fx": "dim", "depth": 1.0}]}}})()
        self.lights_cfg = {"groups": {"movers": {}, "lamps": {}, "lasers": {"reads_under_lux": 3.0, "lands_on": ["ceiling"]}},
                           "lineups": {"feature": {"plays": ["movers", "lasers"], "features": ["lasers"]},
                                       "everything": {"plays": ["movers", "lamps", "lasers"], "features": []}}}
        self._lineup, self._lineup_apply, self._vibe_name = lineup, None, "party"
        self.house, self.low = house, low
        self._group_level, self._room_light_view, self._room_light_logged, self.logged = {}, {}, None, []

    def _space_now(self):
        return None, {"room": {"size": list(ROOM)}}

    def _root(self, gid):
        return gid

    def _log_event(self, kind, body):
        self.logged.append((kind, body))

    def _features(self, applying=False):
        return self._dm.Director._features(self, applying)

    def light(self, movers, lamps=(0, 0, 0)):
        out = {f: {"rgb": list(movers)} for f in self._groups["movers"]["members"]}
        out.update({f: {"rgb": list(lamps)} for f in self._groups["lamps"]["members"]})
        out.update({f: {"rgb": [1.0, 0, 0]} for f in self._groups["lasers"]["members"]})
        self._dm.Director._room_light(self, out)
        return self._room_light_view


def test_the_heads_under_a_featured_laser_come_down_by_their_colour_and_nothing_else_does():
    d = _Dir("feature")
    v = d.light((0.0, 0.0, 1.0))                                          # blue heads: 3 lux of their own
    assert v["features"] == ["lasers"] and v["target_lux"] == 3.0
    assert v["by_group"]["movers"] == pytest.approx(4 * 1190 * 0.0722 / 115, abs=0.1)
    assert 0.5 < d._group_level["movers"] < 0.65 and "lasers" not in d._group_level
    assert v["lux"] == pytest.approx(3.0, abs=0.15)                       # the room lands on what the laser needs
    v = d.light((0.0, 1.0, 1.0))                                          # cyan heads: far more light, far further down
    assert d._group_level["movers"] == 0.12 and v["by_group"]["movers"] > 25
    v = d.light((0.0, 0.0, 0.2))                                          # dim blue: already under it
    assert d._group_level == {} and v["level"] == 1.0
    assert [k for k, _ in d.logged] == ["room_light"] * 3                 # each move of the level is logged


def test_resting_groups_count_for_nothing_and_everything_features_nothing():
    d = _Dir("feature")
    d.light((0.0, 0.0, 1.0), lamps=(1.0, 1.0, 1.0))                       # the pars are resting (dark): no light
    assert d._room_light_view["by_group"]["lamps"] == 0.0 and "lamps" not in d._group_level
    e = _Dir("everything")
    v = e.light((1.0, 1.0, 1.0))
    assert e._group_level == {} and v["features"] == [] and v["lux"] > 40


def test_house_lights_and_lights_low_bring_nothing_down():
    for kw in ({"house": True}, {"low": True}):
        d = _Dir("feature", **kw)
        d.light((0.0, 1.0, 1.0))
        assert d._group_level == {}
