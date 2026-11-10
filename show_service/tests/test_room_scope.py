"""The room scope of the show (multi-room plan): golden baselines first (step 1), recorded on the tree before any
show-scope change. Until a room sets "show": false, RoomLink._build and the ensemble's players must reproduce them.

* RoomLink._build on room_service's recorded finsbury status (room_service/tests/golden/finsbury_status.json);
* ensemble.players on finsbury's real rig (lights/fixtures*.yaml), light groups and show.yaml (the director's own
  _ensemble_players, the room's space included): with no room view, with the recorded one, and with every screen live.

To re-record on purpose: SM_GOLDEN_RECORD=1 pytest show_service/tests/test_room_scope.py, then review the diff.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib

import pytest

from show_service import config, contract, fixtures
from show_service import director as dmod
from show_service import palette as pal
from show_service import space as spmod
from show_service.autopilot import RoomLink

HERE = pathlib.Path(__file__).resolve().parent
GOLDEN = HERE / "golden"
ROOM_STATUS = HERE.parents[1] / "room_service" / "tests" / "golden" / "finsbury_status.json"


def _check(name: str, got, note: str = "") -> None:
    p = GOLDEN / name
    got = json.loads(json.dumps(got, sort_keys=True, default=str))
    if os.environ.get("SM_GOLDEN_RECORD") == "1":
        GOLDEN.mkdir(exist_ok=True)
        p.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
        pytest.skip(f"recorded show_service/tests/golden/{name}")
    assert got == json.loads(p.read_text()), f"differs from show_service/tests/golden/{name}{note}"


# The deployment files the players read (the Lights page writes the *.added.yaml ones): a mismatch while one changed
# since recording is new input, not a regression.
INPUTS = ("lights/fixtures.yaml", "lights/fixtures.added.yaml", "lights/fixtures.setup.yaml", "lights/space.yaml",
          "lights/space.added.yaml", "lights/patch.yaml", "show.yaml", "rooms.json")


def _inputs() -> dict:
    import hashlib
    out = {}
    for rel in INPUTS:
        p = contract.DEPLOYMENT.path(*rel.split("/"))
        out[rel] = hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else None
    out["config/room/show.yaml"] = hashlib.sha1(contract.SHOW_FILE.read_bytes()).hexdigest()
    return out


def _check_inputs() -> str:
    p = GOLDEN / "finsbury_inputs.json"
    if os.environ.get("SM_GOLDEN_RECORD") == "1":
        GOLDEN.mkdir(exist_ok=True)
        p.write_text(json.dumps(_inputs(), indent=1, sort_keys=True) + "\n")
        return ""
    was, now = json.loads(p.read_text()), _inputs()
    changed = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
    return f" (but {', '.join(changed)} changed since it was recorded: new input, re-record if the code is " \
           "unchanged)" if changed else ""


def _room_view() -> dict:
    return RoomLink(contract.BrainContext(publish=lambda e: None))._build(json.loads(ROOM_STATUS.read_text()))


@pytest.fixture
def finsbury():
    if contract.DEPLOYMENT.id != "finsbury":
        pytest.skip("the goldens are finsbury's")


def test_roomlink_build_matches_its_golden(finsbury):
    _check("finsbury_roomlink.json", _room_view())


def _players(tmp_path, room) -> list:
    show = config.read_show(contract.SHOW_FILE)
    reg = fixtures.Registry(contract.FIXTURES_FILE)
    reg.poll(looks=show.get("looks"), force=True, lamps=show.get("lamps"))
    rig = reg.view()
    ctx = contract.BrainContext(publish=lambda e: None)
    ctx.rig = lambda: rig
    d = dmod.Director(ctx, catalog=pal.Catalog(), log_path=tmp_path / "director.jsonl", wall=lambda: 1.8e9,
                      mono=lambda: 1000.0)
    d.configure(show)
    d._check_rig()
    d._groups_refresh()
    d._ensemble_players(1000.0, room)
    return d.ensemble._players


def test_ensemble_players_match_their_golden(finsbury, tmp_path, monkeypatch):
    monkeypatch.setattr(spmod, "_SHARED", None)          # the deployment's own space.yaml, read afresh
    view = _room_view()
    live = copy.deepcopy(view)
    for sc in live["screens"].values():
        sc["layer"], sc["phase"] = "live", "live"
    got = {"no_room_view": _players(tmp_path, None), "recorded_view": _players(tmp_path, view),
           "every_screen_live": _players(tmp_path, live)}
    _check("finsbury_players.json", got, note=_check_inputs())


# -- step 3: rooms with "show": false are outside the show; the ensemble is the primary room's (E9) -----------------------
def _status_with_show_false() -> dict:
    """The recorded finsbury status with the lab outside the show (status rooms[] carries "show": false when set)."""
    st = json.loads(ROOM_STATUS.read_text())
    st["rooms"].append({"id": "lab2", "name": "Lab 2", "screens": ["south-screen"], "lights": [], "blinds": [],
                        "sensors": [], "effects": [], "show": False})
    return st


def test_roomlink_drops_the_screens_of_a_room_outside_the_show():
    link = RoomLink(contract.BrainContext(publish=lambda e: None))
    assert link.excluded_screens() == frozenset()
    full = link._build(json.loads(ROOM_STATUS.read_text()))
    assert link.excluded_screens() == frozenset()                     # no room sets it: nothing excluded
    v = link._build(_status_with_show_false())
    assert link.excluded_screens() == frozenset({"south-screen"})
    assert "south-screen" in full["screens"] and "south-screen" not in v["screens"]
    assert set(v["screens"]) == set(full["screens"]) - {"south-screen"}
    assert link.provides()["room_excluded"]() == frozenset({"south-screen"})


def test_players_leave_out_excluded_screen_fixtures():
    from show_service import ensemble as ensmod
    rig = {"types": {"scr": {"kind": "screen"}},
           "fixtures": {"screen.a": {"type": "scr", "screen": "a"}, "screen.b": {"type": "scr", "screen": "b"},
                        "screen.x": {"type": "scr"}}}
    ids = lambda ps: sorted(p["id"] for p in ps)                     # noqa: E731
    assert ids(ensmod.players(rig, {}, cfg={})) == ["screen.a", "screen.b", "screen.x"]
    assert ids(ensmod.players(rig, {}, cfg={}, exclude_screens=set())) == ["screen.a", "screen.b", "screen.x"]
    assert ids(ensmod.players(rig, {}, cfg={}, exclude_screens={"b"})) == ["screen.a", "screen.x"]


def _director(tmp_path):
    show = config.read_show(contract.SHOW_FILE)
    reg = fixtures.Registry(contract.FIXTURES_FILE)
    reg.poll(looks=show.get("looks"), force=True, lamps=show.get("lamps"))
    rig = reg.view()
    ctx = contract.BrainContext(publish=lambda e: None)
    ctx.rig = lambda: rig
    d = dmod.Director(ctx, catalog=pal.Catalog(), log_path=tmp_path / "director.jsonl", wall=lambda: 1.8e9,
                      mono=lambda: 1000.0)
    d.configure(show)
    d._check_rig()
    d._groups_refresh()
    return d, ctx


def test_ensemble_players_skip_screens_outside_the_show_and_other_rooms(finsbury, tmp_path, monkeypatch):
    monkeypatch.setattr(spmod, "_SHARED", None)
    d, ctx = _director(tmp_path)
    assert d._other_screens == frozenset({"devtest"})               # finsbury: the lab's screen (no fixture)
    screens = lambda: sorted(p["screen"] for p in d.ensemble._players if p["kind"] == "screen")   # noqa: E731
    d._ensemble_players(1000.0, None)
    assert screens() == ["north-screen", "south-screen"]
    ctx.room_excluded = lambda: frozenset({"south-screen"})          # a room outside the show
    d._ensemble_players(1001.0, None)
    assert screens() == ["north-screen"]
    ctx.room_excluded = lambda: frozenset()
    d._other_screens = frozenset({"north-screen"})                   # E9: another room's screen is never a player
    d._ensemble_players(1002.0, None)
    assert screens() == ["south-screen"]


def test_a_pick_on_a_screen_outside_the_show_is_ignored(finsbury, tmp_path, monkeypatch):
    monkeypatch.setattr(spmod, "_SHARED", None)
    d, ctx = _director(tmp_path)
    ctx.room_excluded = lambda: frozenset({"devtest"})
    d.on_room_event(contract.ROOM_SCREEN_LOOK, {"screen": "devtest", "palette": "ember", "source": "web"})
    assert d._scr["picks"] == 0 and d.screen_pick is None
    d.on_room_event(contract.ROOM_SCREEN_LOOK, {"screen": "north-screen", "palette": "ember", "source": "web"})
    assert d._scr["picks"] == 1
