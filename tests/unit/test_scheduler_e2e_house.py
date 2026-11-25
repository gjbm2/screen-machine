"""End-to-end scenarios on the house's LIVE schedules (routes/scheduler/<screen>.json, the top of each stack: the ring,
the replay, sleep / wake, user interaction, live layer, room mode, ...) in the simulated house of scheduler_sim.py.
Skipped where a screen has no live schedule (a fresh checkout).

The room: north and south (taking turns through the day's set, the ring), the cloud (which exports the set and the
ring's settings to the room) and, in another room, the-beast. The set has 8 pictures; the ring changes every 30 min
from 07:00, north on the hour, south on the half hour.

What a replay must do, and the tests that hold it to it:
  - thrown to the room (Alexa): both screens, taking turns, one change at a time, every neighbour pair once, each
    ending on the picture it started on                                             test_room_replay_*
  - thrown at one screen (its display page): that screen alone, the whole set; the other keeps its ring
                                                                                     test_single_screen_replay_*
  - thrown again while replaying: ignored, and the end is still the original picture   test_a_second_replay_*
  - a Flask restart part-way: the screen is back on the ring's picture within a minute test_a_restart_*
  - pictures not made yet: skipped                                                   test_unmade_pictures_*
  - the ring's slot changing during it: the new picture goes up once it is done      test_the_ring_slot_*
  - another room's screen is never touched                                           (every room test)
And what happens when something else happens part-way (pinned as it is today):
  user interaction, the screen going live, sleep, an Alexa script, night time, a still with its clip.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

from scheduler_sim import Sim

SCHEDULES = Path(__file__).resolve().parents[2] / "routes" / "scheduler"
SCREENS = ("north-screen", "south-screen")
SET = [f"p{i}.jpg" for i in range(8)]
TURN = 25


def live(screen):
    p = SCHEDULES / f"{screen}.json"
    if not p.exists():
        pytest.skip(f"{screen} has no live schedule here")
    return json.loads(p.read_text())["schedule_stack"][-1]


def live_vars(screen):
    """The screen's own saved variables (the top context of its live state)."""
    return json.loads((SCHEDULES / f"{screen}.json").read_text())["context_stack"][-1].get("vars", {})


def house(monkeypatch, tmp_path, start, day_set=SET, day="2026-11-25"):
    sim = Sim(monkeypatch, tmp_path, start, {"north-screen": ["drawing-room", "home"], "south-screen": ["drawing-room", "home"],
                                             "cloud": ["drawing-room"], "the-beast": ["work"]})
    sim.install("cloud", {"triggers": []})
    for var, value in (("day_set", day_set), ("day_sets", {day: day_set}), ("ring_step_min", 30),
                       ("day_themes", {day: "Today's art theme is 'Intricate Webs.' Drawing on St. Catherine's Day. More words here."}),
                       ("ring_day_starts", "07:00"), ("ring_screens", 2), ("replay_turn_s", TURN)):
        sim.export("cloud", var, value, "drawing-room")
    for s in SCREENS:
        sim.install(s, live(s), vars={"screen_layer": "art"})
    sim.install("the-beast", {"triggers": [{"type": "event", "value": "Replay_day", "trigger_actions": {
        "important": True, "instructions_block": [{"action": "publish", "source": "beast-replayed"}]}}]})
    return sim


def changes(sim, screen, since):
    return [(round(t - since), n) for t, n in sim.shown(screen, since=since)]


def settle(sim):
    """Let the ring put up each screen's picture for now (its first minute)."""
    sim.run(seconds=70)
    return sim.clock.t


# --- the ring itself ------------------------------------------------------------------------------------------------

def test_the_ring_changes_north_on_the_hour_and_south_on_the_half(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 0, 10))
    sim.run(minutes=61)
    assert [n for _, n in sim.shown("north-screen")] == ["p0.jpg", "p1.jpg"]       # 15:00 slot 0, 16:00 slot 1
    assert [n for _, n in sim.shown("south-screen")] == ["p3.jpg", "p4.jpg"]       # 15:00 slot 3, 15:30 slot 4
    assert 3550 < sim.shown("north-screen")[1][0] < 3600 + 70
    assert 1750 < sim.shown("south-screen")[1][0] < 1800 + 70


# --- a replay for the room (Alexa) ----------------------------------------------------------------------------------

def test_room_replay_takes_turns_one_change_at_a_time_and_each_screen_ends_where_it_started(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    start = {s: sim.shown(s)[-1][1] for s in SCREENS}
    sim.throw("drawing-room", "Replay_day", payload={"day": "2026-11-25"})
    sim.run(minutes=6)
    north, south = changes(sim, "north-screen", t0), changes(sim, "south-screen", t0)
    assert [n for _, n in north] == ["p0.jpg", "p1.jpg", "p2.jpg", "p3.jpg", "p4.jpg", start["north-screen"]]
    assert [n for _, n in south] == ["p4.jpg", "p5.jpg", "p6.jpg", "p7.jpg", "p0.jpg", start["south-screen"]]
    times = sorted([t for t, _ in north] + [t for t, _ in south])
    assert all(b - a >= TURN - 2 for a, b in zip(times, times[1:])), f"one change at a time: {times}"
    whose = [s for _, s in sorted([(t, "N") for t, _ in north] + [(t, "S") for t, _ in south])]
    assert whose == ["N", "S"] * 6, "they alternate"
    assert sim.shown("the-beast") == [], "another room is never touched"
    sim.run(minutes=10)                                                           # nothing more until the ring's slot
    assert changes(sim, "north-screen", t0)[-1][1] == start["north-screen"]
    assert changes(sim, "south-screen", t0)[-1][1] == start["south-screen"]


def test_room_replay_publishes_with_transitions_and_without_clips_then_the_start_with_its_clip(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    settle(sim)
    n0 = len(sim.published["north-screen"])
    sim.throw("drawing-room", "Replay_day", payload={"day": "2026-11-25"})
    sim.run(minutes=6)
    kws = [p.kw for p in sim.published["north-screen"][n0:]]
    assert all(k.get("transition") and k.get("clips") is False for k in kws[:-1])
    assert kws[-1].get("transition") and kws[-1].get("clips") is not False, "the put-back brings its clip"


# --- a replay for one screen (its display page) ---------------------------------------------------------------------

def test_single_screen_replay_plays_the_whole_set_there_and_the_other_keeps_its_ring(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 27, 0))
    t0 = settle(sim)
    start = sim.shown("north-screen")[-1][1]
    sim.throw("north-screen", "Replay_day")                                       # no day: today
    sim.run(minutes=6)
    north = changes(sim, "north-screen", t0)
    assert [n for _, n in north] == [f"p{i}.jpg" for i in range(8)] + ["p0.jpg", start]
    assert [t for t, _ in north][:9] == pytest.approx([TURN * k for k in range(9)], abs=2)
    assert [n for _, n in changes(sim, "south-screen", t0)] == ["p4.jpg"], "south changed at 15:30 as usual"


# --- well-behaved at the edges --------------------------------------------------------------------------------------

def test_a_second_replay_while_one_runs_is_ignored_and_the_end_is_still_the_original(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    start = sim.shown("north-screen")[-1][1]
    sim.throw("north-screen", "Replay_day")
    sim.run(seconds=60)
    sim.throw("north-screen", "Replay_day")                                       # pressed again
    sim.throw("drawing-room", "Replay_day")                                       # and Alexa, for the room
    sim.run(minutes=8)
    north = [n for _, n in changes(sim, "north-screen", t0)]
    assert north[:10] == [f"p{i}.jpg" for i in range(8)] + ["p0.jpg", start]
    assert north[-1] == start


def test_a_restart_part_way_puts_the_ring_picture_back_within_a_minute(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    ring_picture = sim.shown("north-screen")[-1][1]
    sim.throw("north-screen", "Replay_day")
    sim.run(seconds=60)
    assert sim.shown("north-screen")[-1][1] != ring_picture
    sim.restart("north-screen")                                                   # Flask restarts: the queue is gone
    sim.run(seconds=90)
    assert sim.shown("north-screen")[-1][1] == ring_picture
    assert sim.shown("north-screen")[-1][0] - (t0 + 60) <= 75
    sim.throw("north-screen", "Replay_day")                                       # and the next replay isn't blocked
    sim.run(seconds=5)
    assert sim.shown("north-screen")[-1][1] == "p0.jpg"


def test_unmade_pictures_are_skipped(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0),
                day_set=["p0.jpg", None, "p2.jpg", None, "p4.jpg", "p5.jpg", None, "p7.jpg"])
    t0 = settle(sim)
    sim.throw("north-screen", "Replay_day")
    sim.run(minutes=5)
    shown = [n for _, n in changes(sim, "north-screen", t0)]
    assert "None" not in shown and None not in shown
    assert shown[:6] == ["p0.jpg", "p2.jpg", "p4.jpg", "p5.jpg", "p7.jpg", "p0.jpg"]


def test_the_ring_slot_changing_during_a_replay_goes_up_once_it_is_done(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 57, 0))        # north's 16:00 change is due
    t0 = settle(sim)
    sim.throw("north-screen", "Replay_day")
    sim.run(minutes=8)
    north = [n for _, n in changes(sim, "north-screen", t0)]
    assert north[9] == "p0.jpg", "the put-back of the picture it started on"
    assert north[10:] == ["p1.jpg"], "then the ring's 16:00 picture, once"


def test_a_replay_of_a_day_that_has_no_set_changes_nothing(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    sim.export("cloud", "day_set", [], "drawing-room")
    sim.throw("north-screen", "Replay_day", payload={"day": "2026-01-01"})
    sim.run(minutes=2)
    assert [n for _, n in changes(sim, "north-screen", t0)] in ([], ["p0.jpg"])   # at most the start put back


# --- something else happening part-way (pinned) ---------------------------------------------------------------------

@pytest.mark.parametrize("event, payload, pause", [
    ("sleep", None, 0),                                           # Alexa: screens off (urgent: done at once)
    ("_screen_layer", {"layer": "live"}, 0),                      # the screen goes live (important: noted after)
    ("Generate", None, 0),                                        # a normal event: dropped while it runs
    ("_user_interacting", {"action": "tap", "wait": "2m"}, 120),  # someone at the display page's controls: it holds
])
def test_a_replay_keeps_its_pace_through_other_events_and_holds_while_someone_is_at_the_controls(
        monkeypatch, tmp_path, event, payload, pause):
    """Whatever else arrives part-way, the replay walks on at its pace and ends on the picture it started on; an
    interaction holds it for the interaction's own wait, then it carries on where it was."""
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    start = sim.shown("north-screen")[-1][1]
    sim.throw("north-screen", "Replay_day")
    sim.run(seconds=60)
    sim.throw("north-screen", event, payload=payload)
    sim.run(minutes=7)
    north = changes(sim, "north-screen", t0)
    assert [n for _, n in north][:10] == [f"p{i}.jpg" for i in range(8)] + ["p0.jpg", start]
    expected = [TURN * k + (pause if TURN * k > 60 else 0) for k in range(9)]
    assert [t for t, _ in north][:9] == pytest.approx(expected, abs=3)


def test_pinned_an_alexa_script_loaded_part_way_runs_on_top_and_the_replay_still_ends_where_it_started(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    t0 = settle(sim)
    start = sim.shown("north-screen")[-1][1]
    sim.throw("north-screen", "Replay_day")
    sim.run(seconds=60)
    sim.load("north-screen", {"triggers": [{"type": "event", "value": "Done", "trigger_actions": {
        "important": True, "instructions_block": [{"action": "unload"}]}}]})
    sim.run(minutes=5)
    assert [n for _, n in changes(sim, "north-screen", t0)][-1] == start
    sim.throw("north-screen", "Done")
    sim.run(seconds=10)
    assert len(sim.stack("north-screen")) == 1


def test_a_replay_at_night_outside_the_rings_hours_still_ends_where_it_started(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 2, 0, 0))
    sim.shows("north-screen", "night.jpg")
    sim.throw("north-screen", "Replay_day")
    sim.run(minutes=5)
    shown = [n for _, n in sim.shown("north-screen")]
    assert shown[0] == "p0.jpg" and shown[-1] == "night.jpg"


def test_a_replay_over_a_still_with_its_clip_puts_back_the_still(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    settle(sim)
    sim.shows("north-screen", "pic.jpg")
    sim.shows("north-screen", "pic-clip.mp4", meta={"still": "pic.jpg"})      # its clip went up just after it
    sim.throw("north-screen", "Replay_day")
    sim.run(minutes=5)
    assert sim.shown("north-screen")[-1][1] == "pic.jpg"


def test_the_beasts_daily_commission_starts_at_six_though_its_ring_ticks_that_minute(monkeypatch, tmp_path):
    """25 Nov: the 06:00 throw and the every-minute ring were due in one tick; the commission was dropped and the-beast
    made no pictures (or transitions) that day."""
    beast = live("the-beast")
    sim = Sim(monkeypatch, tmp_path, datetime(2026, 11, 25, 5, 59, 30), {"the-beast": ["work"]})
    ring = {k: v for k, v in live_vars("the-beast").items() if k.startswith("ring_")}
    sim.install("the-beast", beast, vars={**ring, "ring_n": 1})          # one picture: it starts, that's the point
    sim.run(minutes=2)
    assert any(d == "the-beast" and a == "generate" for _, d, a, _ in sim.actions), "the day's set was commissioned"



def test_a_replay_opens_with_an_overlay_saying_what_we_are_about_to_see(monkeypatch, tmp_path):
    sim = house(monkeypatch, tmp_path, datetime(2026, 11, 25, 15, 5, 0))
    settle(sim)
    sim.throw("drawing-room", "Replay_day", payload={"day": "2026-11-25"})
    sim.run(seconds=10)
    shown = {d: ins["text"] for _, d, a, ins in sim.actions if a == "overlay"}
    assert set(shown) == {"north-screen", "south-screen"}, "on each screen taking part"
    text = shown["north-screen"]
    assert text.startswith("Today's art theme is 'Intricate Webs.' Drawing on St. Catherine's Day.")
    assert "Playing 8 pictures across both screens, taking turns: about 5 minutes." in text
    sim.run(minutes=6)                                        # the room's replay is over
    sim.throw("north-screen", "Replay_day")
    sim.run(seconds=10)
    solo = [ins["text"] for _, d, a, ins in sim.actions if a == "overlay" and d == "north-screen"][-1]
    assert "Playing 8 pictures on this screen: about 4 minutes." in solo
