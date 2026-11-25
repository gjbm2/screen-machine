"""End-to-end logic tests of the scheduler machinery (owner, 25 Nov), on the simulated house of scheduler_sim.py: the
real loop step, queue, triggers, events, stacks and variables, on a clock the test moves.

The matrix, and which test covers each cell:

  block flags       normal / important / urgent / urgent+important
  destination       idle / busy (a block running) / waiting / a script on its stack / stopped
  event             thrown to: this destination / its group / another group / global / an unknown name;
                    now / delayed / expired before it could run; once / again while its block runs
  interleavings     a newer urgent block during a wait; a normal or important trigger during a wait; a second
                    event's block while the first's runs (whose _event do the first's later steps see?); terminate
                    inside a for_each; a script loaded and unloaded under a running block

Each test says what it pins down. Where today's behaviour is questionable, the test pins it with the note "pinned:"
so a change is deliberate (and the owner has the list).
"""
from datetime import datetime

import pytest

from scheduler_sim import Sim

START = datetime(2026, 11, 25, 15, 0, 0)
HOUSE = {"north": ["room"], "south": ["room"], "office": ["work"], "cloud": ["room"]}


@pytest.fixture
def sim(monkeypatch, tmp_path):
    return Sim(monkeypatch, tmp_path, START, HOUSE)


def on(event, *steps, urgent=False, important=False):
    """An event trigger running `steps`."""
    ta = {"instructions_block": list(steps)}
    if urgent:
        ta["urgent"] = True
    if important:
        ta["important"] = True
    return {"type": "event", "value": event, "trigger_actions": ta}


def every_minute(*steps, important=False):
    """A scheduled trigger every minute of the day, as the ring's rotation is."""
    ta = {"instructions_block": list(steps)}
    if important:
        ta["important"] = True
    return {"type": "day_of_week", "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "scheduled_actions": [{"time": "00:00", "repeat_schedule": {"every": "1", "until": "23:59"},
                                   "trigger_actions": ta}]}


def pub(src, **kw):
    return {"action": "publish", "source": src, **kw}


def wait(s):
    return {"action": "wait", "duration": f"{s}s"}


def names(shown):
    return [n for _, n in shown]


# --- waits and the queue's flags ------------------------------------------------------------------------------------

def test_a_block_waits_its_own_waits_whatever_its_flags(sim):
    """The 24 Nov bug: an urgent block's later steps counted as newer urgent work and cut through its own waits."""
    for flags in ({}, {"important": True}, {"urgent": True}, {"urgent": True, "important": True}):
        sim.install("north", {"triggers": [on("Go", pub("a"), wait(30), pub("b"), wait(30), pub("c"), **flags)]})
        sim.published["north"].clear()
        sim.throw("north", "Go")
        sim.run(seconds=90)
        assert [t for t, _ in sim.shown("north", since=sim.clock.t - 90)][-3:] == pytest.approx(
            [sim.clock.t - 90 + d for d in (0, 30, 60)], abs=2.5), flags


def test_a_newer_urgent_block_interrupts_a_wait_and_the_wait_then_resumes_for_what_was_left(sim):
    """An urgent event is dealt with at once, and the block it interrupted then waits out the rest of its wait, so
    it keeps its pace (25 Nov e2e: an urgent event used to cut the wait short)."""
    sim.install("north", {"triggers": [on("Slow", pub("a"), wait(60), pub("b"), important=True),
                                       on("Now", pub("urgent"), urgent=True)]})
    sim.throw("north", "Slow")
    sim.run(seconds=10)
    sim.throw("north", "Now")
    sim.run(seconds=70)
    shown = sim.shown("north")
    assert names(shown) == ["a", "urgent", "b"]
    assert shown[1][0] < 13 and 58 <= shown[2][0] <= 63


def test_an_interruption_with_its_own_wait_pauses_the_block_it_interrupted_for_that_long(sim):
    """Someone at the controls (an urgent block with a wait of its own): the interrupted block's queued steps are
    older, so they don't cut that wait short; the interrupted block resumes after it, for the rest of its own."""
    sim.install("north", {"triggers": [on("Slow", pub("a"), wait(60), pub("b"), urgent=True, important=True),
                                       on("Tap", pub("tap"), wait(30), urgent=True)]})
    sim.throw("north", "Slow")
    sim.run(seconds=10)
    sim.throw("north", "Tap")
    sim.run(seconds=100)
    shown = sim.shown("north")
    assert names(shown) == ["a", "tap", "b"]
    assert 88 <= shown[2][0] <= 93, "b after the tap's 30 s and the 50 s left of its own wait"


def test_during_a_wait_a_normal_trigger_is_skipped_and_an_important_one_runs_after_it(sim):
    sim.install("north", {"triggers": [
        on("Long", pub("long"), wait(120), pub("after"), important=True),
        every_minute(pub("{{ 'minute-' ~ now().strftime('%H%M') }}")),
        on("Later", pub("important-later"), important=True)]})
    sim.run(seconds=1)                                   # the first minute's normal trigger runs while idle
    sim.throw("north", "Long")
    sim.run(seconds=20)
    sim.throw("north", "Later")
    sim.run(seconds=150)
    got = names(sim.shown("north"))
    assert got[:2] == ["minute-1500", "long"]
    assert not any(n.startswith("minute-150") and n != "minute-1500" for n in got[2:got.index("after")]), \
        "no minute ran during the wait"
    assert got.index("after") < got.index("important-later"), "the important block queued behind the wait"


def test_pinned_a_normal_event_thrown_while_busy_is_used_up_and_never_runs(sim):
    """pinned: an event whose block is normal (neither urgent nor important) is consumed when it is seen and its
    block dropped while the destination waits. A button that matters must be important (memory: scheduler-event-
    triggers-important)."""
    sim.install("north", {"triggers": [on("Busy", pub("busy"), wait(60), pub("done"), important=True),
                                       on("Normal", pub("normal"))]})
    sim.throw("north", "Busy")
    sim.run(seconds=5)
    sim.throw("north", "Normal")
    sim.run(seconds=120)
    assert names(sim.shown("north")) == ["busy", "done"]


# --- where events go ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope, reached", [
    ("north", {"north"}),
    ("room", {"north", "south", "cloud"}),
    ("work", {"office"}),
    ("global", {"north", "south", "office", "cloud"}),
    ("nowhere", set()),
])
def test_an_event_reaches_exactly_its_scope(sim, scope, reached):
    for d in HOUSE:
        sim.install(d, {"triggers": [on("Ping", pub("{{ _event.scope }}"), important=True)]})
    sim.throw(scope, "Ping")
    sim.run(seconds=5)
    assert {d for d in HOUSE if sim.shown(d)} == reached
    assert all(names(sim.shown(d)) == [scope] for d in reached), "each saw where it was thrown"


def test_a_delayed_event_runs_when_due_and_an_expired_one_never(sim):
    sim.install("north", {"triggers": [on("Later", pub("later"), important=True)]})
    sim.throw("north", "Later", delay="30s")
    sim.run(seconds=20)
    assert sim.shown("north") == []
    sim.run(seconds=15)
    assert names(sim.shown("north")) == ["later"]

    sim.stop("north")                                    # not running while its event lives and dies
    sim.throw("north", "Later", ttl="5s")
    sim.run(seconds=10)
    sim.install("north", {"triggers": [on("Later", pub("too late"), important=True)]})
    sim.run(seconds=5)
    assert "too late" not in names(sim.shown("north"))


# --- an event's data through its block --------------------------------------------------------------------------------

def test_a_blocks_event_is_its_own_through_waits_and_for_each_rounds_even_when_another_event_comes(sim):
    """Every step of a block reads ITS event: before and after its waits, inside its for_each rounds, while another
    event's block has run in between."""
    sim.install("north", {"triggers": [
        on("Count", {"action": "for_each", "items": "[1, 2, 3]", "as": "i", "instructions_block": [
            pub("{{ _event.payload.who }}-{{ i }}"), wait(20)]}, pub("{{ _event.payload.who }}-end"),
           urgent=True, important=True),
        on("Other", pub("other-{{ _event.payload.who }}"), urgent=True)]})
    sim.throw("north", "Count", payload={"who": "first"})
    sim.run(seconds=25)
    sim.throw("north", "Other", payload={"who": "second"})
    sim.run(seconds=90)
    got = names(sim.shown("north"))
    assert "other-second" in got
    assert [n for n in got if n != "other-second"] == ["first-1", "first-2", "first-3", "first-end"]


def test_terminate_inside_a_for_each_ends_the_rest_of_its_rounds(sim):
    sim.install("north", {"triggers": [on("Go", {"action": "for_each", "items": "[1, 2, 3, 4]", "as": "i",
                                                 "instructions_block": [
                                                     {"action": "terminate", "mode": "block", "test": "{{ i == 3 }}"},
                                                     pub("r{{ i }}")]}, pub("after"), important=True)]})
    sim.throw("north", "Go")
    sim.run(seconds=5)
    assert names(sim.shown("north")) == ["r1", "r2"]


# --- stacks: a script loaded on a running destination --------------------------------------------------------------

def test_a_loaded_script_runs_on_top_and_its_unload_hands_back_to_the_schedule_under_it(sim):
    base = {"triggers": [on("Base", pub("base-{{ who }}"), important=True)]}
    script = {"triggers": [on("Script", pub("script-{{ who }}"), important=True),
                           on("Done", {"action": "unload"}, important=True)]}
    sim.install("north", base, vars={"who": "base-var"})
    sim.load("north", script)
    sim.vars("north")["who"] = "script-var"
    sim.throw("north", "Base")
    sim.throw("north", "Script")
    sim.run(seconds=5)
    assert names(sim.shown("north")) == ["script-script-var"], "only the top schedule's triggers run"
    sim.throw("north", "Done")
    sim.run(seconds=5)
    assert len(sim.stack("north")) == 1 and sim.vars("north")["who"] == "base-var", "the base's own context is back"
    sim.throw("north", "Base")
    sim.run(seconds=5)
    assert names(sim.shown("north"))[-1] == "base-base-var"


def test_a_script_loaded_on_a_running_destination_runs_its_initial_actions(sim):
    sim.install("north", {"triggers": []})
    sim.load("north", {"initial_actions": {"instructions_block": [pub("started")]}, "triggers": []})
    sim.run(seconds=5)
    assert names(sim.shown("north")) == ["started"]


def test_pinned_a_pushed_scripts_initial_actions_run_at_once_so_a_wait_in_them_does_not_hold(sim):
    """pinned: push_schedule runs a script's initial_actions there and then (run_instruction in a loop), not through
    the queue, so a wait among them doesn't hold the steps after it. A script that needs to pace itself does it from a
    trigger (its steps then go through the queue)."""
    sim.install("north", {"triggers": []})
    sim.load("north", {"initial_actions": {"instructions_block": [pub("a"), wait(30), pub("b")]}, "triggers": []})
    sim.run(seconds=40)
    assert [t for t, _ in sim.shown("north")] == [0.0, 0.0]


def test_a_terminate_test_that_renders_empty_is_false_not_missing(sim):
    """25 Nov e2e: a test reading an unset variable rendered "" and terminate took it as no test at all, so it ended
    the block every time (every replay stopped at its guard)."""
    sim.install("north", {"triggers": [on("Go", {"action": "terminate", "mode": "block", "test": "{{ unset_var }}"},
                                          pub("ran"), important=True),
                                       on("Stop", {"action": "terminate", "mode": "block"}, pub("not reached"),
                                          important=True)]})
    sim.throw("north", "Go")
    sim.throw("north", "Stop")
    sim.run(seconds=5)
    assert names(sim.shown("north")) == ["ran"], "an empty test is false; no test at all still terminates"
