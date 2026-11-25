"""The favourites script (routes/scheduler/scripts/favourites.json; owner, 25 Nov: "computer, use ai to show favourites
from library ... a sensible scheduler ... with the bucket name given as a var"), run on the simulated house
(scheduler_sim.py): loaded with its bucket as Alexa loads it, it shows one favourite at once, then one every 3
minutes, and hands the screen back to its own schedule at 03:00 or when unloaded."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from scheduler_sim import Sim

SCRIPT = json.loads((Path(__file__).resolve().parents[2] / "routes" / "scheduler" / "scripts" / "favourites.json").read_text())
HOUSE = {"north": ["room"], "south": ["room"], "library": []}


def _sim(monkeypatch, tmp_path, at):
    return Sim(monkeypatch, tmp_path, at, HOUSE)


def displays(sim, dest):
    return [(round(t), i.get("show"), i.get("bucket")) for t, d, a, i in sim.actions if d == dest and a == "display"]


def base_schedule():
    return {"triggers": [{"type": "event", "value": "Base", "trigger_actions": {"important": True, "instructions_block": [
        {"action": "publish", "source": "base-picture.jpg"}]}}]}


@pytest.mark.parametrize("second", [0, 30, 59])
def test_loaded_with_a_bucket_it_shows_one_at_once_then_one_every_three_minutes(monkeypatch, tmp_path, second):
    sim = _sim(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 1, second))
    sim.install("north", base_schedule())
    assert su_load(sim, "north", {"bucket": "library"})["status"] == "success"
    sim.run(minutes=10)
    got = displays(sim, "north")
    assert all(show == "Next" and bucket == "library" for _, show, bucket in got)
    starts = [t for t, _, _ in got]
    assert starts[0] == 0, "the first one at once"
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 4 and gaps[0] >= 179 - second and all(g == 180 for g in gaps[1:]), starts


def test_without_a_bucket_it_shows_the_screens_own_favourites(monkeypatch, tmp_path):
    sim = _sim(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 0, 10))
    sim.install("south", base_schedule())
    sim.load("south", SCRIPT)
    sim.run(minutes=4)
    assert {b for _, _, b in displays(sim, "south")} == {"south"}


def test_it_hands_the_screen_back_at_three_in_the_morning(monkeypatch, tmp_path):
    sim = _sim(monkeypatch, tmp_path, datetime(2026, 11, 26, 2, 50, 0))
    sim.install("north", base_schedule())
    su_load(sim, "north", {"bucket": "library"})
    sim.run(minutes=15)
    assert len(sim.stack("north")) == 1, "unloaded at 03:00"
    assert max(t for t, _, _ in displays(sim, "north")) < 10 * 60 + 1
    sim.throw("north", "Base")
    sim.run(seconds=5)
    assert sim.shown("north")[-1][1] == "base-picture.jpg", "the screen's own schedule runs again"


def test_unloading_it_stops_it_and_the_screens_vars_are_its_own_again(monkeypatch, tmp_path):
    sim = _sim(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 0, 0))
    sim.install("north", base_schedule(), vars={"bucket": "theirs"})
    su_load(sim, "north", {"bucket": "library"})
    sim.run(minutes=4)
    n = len(displays(sim, "north"))
    import routes.scheduler_utils as su
    su.unload_schedule_from_stack("north")
    sim.run(minutes=10)
    assert len(displays(sim, "north")) == n
    assert sim.vars("north")["bucket"] == "theirs"


def su_load(sim, dest, vars):
    import copy
    import routes.scheduler_utils as su
    return su.load_schedule_on_stack(dest, copy.deepcopy(SCRIPT), vars)


def test_someone_using_the_screen_holds_the_next_favourite(monkeypatch, tmp_path):
    sim = _sim(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 0, 0))
    sim.install("north", base_schedule())
    su_load(sim, "north", {"bucket": "library"})
    sim.run(seconds=30)
    sim.throw("north", "_user_interacting", payload={"action": "generate", "wait": "5m"})
    sim.run(minutes=5)
    assert [t for t, _, _ in displays(sim, "north")] == [0], "nothing over what they asked for while they wait"
    sim.run(minutes=4)
    assert len(displays(sim, "north")) > 1, "then the favourites carry on"
