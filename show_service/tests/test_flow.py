"""flow (docs/LIGHTS.md §4.2, §4.5): the votes per source, opposites cancelling, dwell and margin, events bypassing the
dwell, the pitch contour, the settings' problems and the period rule; and the director's side: room.direction.flow and
room.look.flow, the timing (a bar line past the lead, a power-of-two period, the anchor beat), a new id only on a
change of dir, events' flow_id and sweep_beats, and the glitterball's motor. Config files are read, never written.

Mood, phase and room names here are the fixed vocabularies' or made up on purpose.
"""

from __future__ import annotations

import asyncio
import json
import math

import pytest
import yaml

from show_service import contract
from show_service import flow as fl
from show_service.tests import test_director as td

CFG = {"sources": {"structure": 1.0, "pitch": 0.6, "listener": 0.5, "moods": 0.3},
       "structure": {"build": "up", "peak": "cw", "drop": "outward", "breakdown": "inward", "outro": "down",
                     "calm": "still"},
       "listener": {"building": "up", "releasing": "down"},
       "moods": {"euphoric": "up", "dark": "down", "cosmic": "cw"},
       "pitch": {"window_s": 4, "slope": 0.03, "min_conf": 0.5},
       "events": {"drop": {"dir": "outward", "bars": 1, "then": "down"}, "build": {"dir": "up"},
                  "track_change": {"sweep_beats": 4}},
       "min_score": 0.25, "margin": 0.15, "dwell_s": 8, "still_below_energy": 0.15,
       "cross_beats": [16, 2], "cross_s": [8, 1], "speed": {"pace": 0.6, "energy": 0.4}, "spread": "look.spread",
       "coherence": {"events": 1.0, "floor": 0.3}, "symmetry": "none"}


def decider(**over) -> fl.FlowDecider:
    probs: list[str] = []
    cfg = fl.parse_settings({**CFG, **over}, probs)
    assert probs == [], probs
    d = fl.FlowDecider(cfg)
    d.listener_min_conf = 0.6
    return d


def run(d: fl.FlowDecider, t0: float, seconds: float, **votes_kw) -> float:
    kw = {"phase": None, "register": None, "trajectory": None, "moods": [], "energy": 0.6}
    kw.update(votes_kw)
    t = t0
    for _ in range(int(round(seconds * 10))):
        t = round(t + 0.1, 6)
        d.step(t, d.votes(**kw))
    return t


# -- votes ------------------------------------------------------------------------------------------------------------------
def test_each_source_votes_for_its_dir():
    d = decider()
    assert d.votes(phase="build", register=None, trajectory=None, moods=[], energy=0.6) == {"up": 1.0}
    assert d.votes(phase="calm", register=None, trajectory=None, moods=[], energy=0.6) == {}    # still is no vote
    v = d.votes(phase=None, register=None, trajectory={"state": "releasing", "conf": 0.8}, moods=[], energy=0.6)
    assert v == {"down": pytest.approx(0.4)}
    assert d.votes(phase=None, register=None, trajectory={"state": "releasing", "conf": 0.5}, moods=[],
                   energy=0.6) == {}                                     # under the listener's confidence
    v = d.votes(phase=None, register=None, trajectory=None, moods=["euphoric", "cosmic", "dark", "euphoric"],
                energy=0.6)
    assert v == {"up": pytest.approx(0.1), "cw": pytest.approx(0.1), "down": pytest.approx(0.1)}   # the first three
    assert {s for s in d.view()["by_source"]} == {"moods"}


def test_opposites_cancel_and_a_small_lead_is_still():
    d = decider(sources={"structure": 0.6, "listener": 0.5})
    t = run(d, 0.0, 20.0, phase="build", trajectory={"state": "releasing", "conf": 1.0})
    assert d.view()["net"]["up"] == pytest.approx(0.1) and d.dir == "still"   # up 0.6, down 0.5: up at 0.1 < 0.25
    run(d, t, 20.0, phase="build")
    assert d.dir == "up" and d.source == "structure" and d.why == "the song builds"


def test_dwell_and_margin_hold_a_flapping_input():
    d = decider()
    t = 0.0
    for i in range(10):                                                 # up and down by turns, 4 s each: never lands
        t = run(d, t, 4.0, phase="build" if i % 2 == 0 else "outro")
        assert d.dir == "still"
    t = run(d, t, 7.9, phase="build")
    assert d.dir == "still" and d.view()["candidate"] == "up"           # waiting out the dwell
    t = run(d, t, 0.2, phase="build")
    assert d.dir == "up" and d.change_at_s is None                      # lands at the next line (the director's)
    run(d, t, 9.0, moods=["dark"], phase=None)                          # down at 0.1: under the bar, back to still
    assert d.dir == "still"


def test_low_energy_is_still_and_coherence_follows_the_net():
    d = decider(dwell_s=0)
    run(d, 0.0, 0.2, phase="build")
    assert d.dir == "up" and d.coherence == pytest.approx(1.0)          # net 1.0 / (2 x 0.25), capped
    run(d, 1.0, 0.2, phase="build", energy=0.1)
    assert d.dir == "still" and d.why == "quiet" and d.coherence == pytest.approx(0.3)


def test_a_drop_lands_outward_at_its_line_then_down_after_its_bars():
    d = decider()
    t = run(d, 0.0, 1.0, phase="calm")
    d.event("drop", t + 1.5, 2.0)                                       # the line 1.5 s ahead, bars of 2 s
    t = run(d, t, 0.1, phase="calm")
    assert (d.dir, d.source, d.coherence) == ("outward", "event", 1.0) and d.change_at_s == pytest.approx(t + 1.4)
    assert d.why == "a drop"
    t = run(d, t, 3.3, phase="calm")                                    # 1.4 + 2.0 - 0.1: still held
    assert d.dir == "outward"
    t = run(d, t, 0.2, phase="calm")
    assert d.dir == "down" and d.why == "after a drop" and d.change_at_s is None
    run(d, t, 5.0, phase="calm")                                        # the votes take over after the dwell
    assert d.dir == "down"
    run(d, t + 5.0, 4.0, phase="calm")
    assert d.dir == "still"


def test_a_build_holds_up_while_the_phase_is_build_and_a_new_song_lands_the_vote_at_once():
    d = decider()
    d.event("build", 1.0, None)
    t = run(d, 0.0, 3.0, phase="build", moods=["dark"])
    assert d.dir == "up" and d.source == "event"
    t = run(d, t, 0.2, phase="calm", moods=["dark", "dark", "dark"])     # the build left: up stays, voting resumes
    assert d.dir == "up" and d.view()["hold"] is None
    d.event("track_change", t + 2.0, 2.0)                               # down leads: it lands at the song, no dwell
    d.step(t + 0.1, d.votes(phase="calm", register=None, trajectory=None, moods=["dark", "dark", "dark"], energy=0.6))
    assert d.dir == "down" and d.change_at_s == pytest.approx(t + 2.0) and d.why.startswith("a new song")


def test_a_rising_register_votes_up_and_a_falling_one_down():
    d = decider()
    for i in range(50):                                                 # +0.06 a second: a rising run
        v = d.votes(phase=None, register=(i * 0.1, 0.3 + 0.006 * i, 0.9), trajectory=None, moods=[], energy=0.6)
    assert set(v) == {"up"} and v["up"] == pytest.approx(0.6) and d.view()["pitch_slope"] == pytest.approx(0.06)
    d2 = decider()
    for i in range(50):
        v = d2.votes(phase=None, register=(i * 0.1, 0.8 - 0.004 * i, 0.9), trajectory=None, moods=[], energy=0.6)
    assert set(v) == {"down"} and v["down"] == pytest.approx(0.6 * 0.04 / 0.06)
    d3 = decider()
    for i in range(50):                                                 # low confidence: no samples, no vote
        v = d3.votes(phase=None, register=(i * 0.1, 0.3 + 0.006 * i, 0.2), trajectory=None, moods=[], energy=0.6)
    assert v == {}
    d4 = decider()
    for i in range(50):                                                 # a flat line
        v = d4.votes(phase=None, register=(i * 0.1, 0.5 + 0.0001 * (i % 2), 0.9), trajectory=None, moods=[],
                     energy=0.6)
    assert v == {}


def test_reset_is_still():
    d = decider(dwell_s=0)
    run(d, 0.0, 0.2, phase="build")
    d.reset(1.0)
    assert d.dir == "still" and d.view()["votes"] == {}


# -- settings and timing --------------------------------------------------------------------------------------------------
def test_bad_settings_fall_back_and_are_listed():
    probs: list[str] = []
    cfg = fl.parse_settings({"structure": {"build": "sideways", "nope": "up"}, "sources": {"vibes": 1},
                             "events": {"drop": {"dir": "out", "bars": -1, "extra": 1}, "zap": {}},
                             "cross_beats": [16], "symmetry": "spiral", "spread": "loud", "min_score": -1,
                             "mirror_ball": {"enabled": True}, "blinds": {"enabled": True}}, probs)
    text = " | ".join(probs)
    for needle in ("structure.build", "structure.nope", "sources.vibes", "events.drop.dir", "events.drop.bars",
                   "events.drop.extra", "events.zap", "cross_beats", "symmetry", "spread", "min_score",
                   "mirror_ball: needs", "blinds: enabled with no targets"):
        assert needle in text, needle
    assert cfg["structure"] == {} and cfg["cross_beats"] == [16.0, 2.0] and cfg["symmetry"] == "none"
    assert cfg["mirror_ball"]["enabled"] is False and cfg["blinds"]["enabled"] is False
    assert fl.parse_settings(None, []) == fl.FLOW_FALLBACK                 # no lights block: still, nothing votes
    real = yaml.safe_load(contract.SHOW_FILE.read_text())["lights"]["flow"]
    p2: list[str] = []
    fl.parse_settings(real, p2)
    assert p2 == []


def test_the_period_is_a_power_of_two_with_hysteresis():
    assert fl.period([16, 2], 0.0) == (16.0, 0) and fl.period([16, 2], 1.0) == (2.0, 3)
    for sp in [i / 20 for i in range(21)]:
        p, n = fl.period([16, 2], sp)
        assert math.log2(p).is_integer() and 2.0 <= p <= 16.0 and p == 16.0 / 2 ** n
    raw = math.log2(16 / (16 * (2 / 16) ** 0.5))                        # 1.5: halfway between 8 and 4
    assert raw == pytest.approx(1.5)
    assert fl.period([16, 2], 0.53, n_prev=1)[0] == 8.0                 # held near the edge ...
    assert fl.period([16, 2], 0.6, n_prev=1)[0] == 4.0                  # ... moved once well past it
    assert fl.period([8, 1], 0.5) == (4.0, 1)                           # seconds alike (2.83 s -> 4 s, a halving)


def test_speed_words():
    sp = [[0.0, "lo"], [0.4, "mid"], [0.75, "hi"]]
    assert [fl.speed_word(sp, x) for x in (0.0, 0.39, 0.4, 0.9)] == ["lo", "lo", "mid", "hi"]
    assert fl.speed_word([], 0.5) is None


# -- the director's side ----------------------------------------------------------------------------------------------------
BALL = {"enabled": True, "power_member": "ball.main", "power_on": "on-word", "direction_target": "ball_motor.dir",
        "speed_target": "ball_motor.speed", "directions": {"cw": "turn-a", "ccw": "turn-b"},
        "speeds": [[0.0, "slow-word"], [0.5, "fast-word"]], "min_gap_s": 30}


def lights_rig(tmp_path, **flow_over):
    settings = json.loads(json.dumps(td.SETTINGS))
    settings["lights"] = {"flow": {**json.loads(json.dumps(CFG)), "dwell_s": 2, **flow_over}}
    return td.Rig(tmp_path, clock=True, settings=settings)


def test_the_flow_is_on_room_direction_and_room_look_with_a_new_id_only_on_a_change(tmp_path):
    r = lights_rig(tmp_path)
    r.run(1.0)
    d0 = r.of(contract.ROOM_DIRECTION)[-1]["flow"]
    assert d0["dir"] == "still" and set(d0) == set(contract.FlowData.__annotations__)   # neutral too: still
    r.d.set_target("alpha")
    r.run(1.0)
    r.sec = {"id": 1, "phase": "build", "bp": 0.3}                       # structure says build: up after the dwell
    r.run(3.0)
    f = r.d.direction["flow"]
    assert f["dir"] == "up" and f["source"] == "structure" and f["id"] == 1
    assert r.d.look["flow"] is r.d.direction["flow"]
    pubs = [x["flow"] for x in r.of(contract.ROOM_DIRECTION)]
    assert pubs[-1]["id"] == 1 and any(p["id"] == 0 for p in pubs)
    i = next(k for k, p in enumerate(pubs) if p["id"] == 1)
    assert r.of(contract.ROOM_DIRECTION)[i]["flow"]["dir"] == "up"      # published on the change (at once)
    # the timing: a bar line at least the screens' lead after the change; a power-of-two period; the anchor beat
    bar = 4 * td.BEAT
    changed_at = f["at_ms"] / 1000.0
    assert changed_at / bar == pytest.approx(round(changed_at / bar))
    assert f["period_beats"] in (2.0, 4.0, 8.0, 16.0) and f["period_s"] == pytest.approx(f["period_beats"] * td.BEAT)
    assert f["anchor_beat"] == pytest.approx(changed_at / td.BEAT)       # the test clock: beat 0 at t 0
    log = [json.loads(x) for x in r.log_path.read_text().splitlines()]
    assert [x["dir"] for x in log if x.get("kind") == "flow"] == ["up"]
    r.energy = 0.95                                                       # faster: speed and period move, the id not
    r.run(3.0)
    assert r.d.direction["flow"]["id"] == 1 and r.d.direction["flow"]["speed"] != f["speed"]
    json.dumps(r.d.snapshot()["lights"])


def test_with_no_grid_the_period_is_in_seconds(tmp_path):
    r = lights_rig(tmp_path)
    r.clock_on = False
    r.d.set_target("alpha")
    r.run(1.0)
    f = r.d.direction["flow"]
    assert f["period_beats"] is None and f["anchor_beat"] is None and 1.0 <= f["period_s"] <= 8.0


def test_events_carry_flow_id_and_a_new_song_its_sweep_beats(tmp_path):
    r = lights_rig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    r.d.action("next")
    r.run(0.1)
    assert r.looks()[-1]["flow_id"] == r.d.direction["flow"]["id"]
    r.run(4.5)                                                           # landed: a person's change no longer holds
    at = r.next_bar() + 2.0
    got = r.d.track_transition({"kind": "track_change", "id": "track_change:9", "at_ms": at * 1000.0,
                                "track_id": 9, "prev_track_id": 8}, {})
    assert got["sweep_beats"] == 4.0 and got["flow_id"] == r.d.direction["flow"]["id"] and "transition" in got
    r.d.action("keep")
    assert r.d.track_transition({"kind": "track_change", "id": "track_change:10", "at_ms": at * 1000.0,
                                 "track_id": 10}, {}) == {"held": True}


def test_a_drop_moves_the_flow_at_its_line(tmp_path):
    r = lights_rig(tmp_path)
    r.d.set_target("alpha")
    r.run(1.0)
    line = r.next_bar()
    r.event("drop", line, id="drop:0:1")
    r.run(0.1)
    f = r.d.direction["flow"]
    assert f["dir"] == "outward" and f["source"] == "event" and f["at_ms"] == pytest.approx(line * 1000.0)
    assert f["coherence"] == 1.0
    r.run_to(line + 4 * td.BEAT + 0.2)                                  # one bar later: down
    assert r.d.direction["flow"]["dir"] == "down"


def test_the_glitterball_turns_with_the_flow_only_while_it_spins_and_at_most_once_a_gap(tmp_path):
    r = lights_rig(tmp_path, mirror_ball=BALL, structure={"peak": "cw", "breakdown": "inward"}, moods={})
    puts: list[tuple[str, dict]] = []

    async def room_put(path: str, body: dict, timeout: float = 5.0):
        puts.append((path, body))
        return 200, {}

    async def main():
        r.ctx.loop = asyncio.get_running_loop()
        r.ctx.room_put = room_put

        async def run(seconds):
            for _ in range(int(round(seconds * 10))):
                r.t = round(r.t + 0.1, 6)
                r.d.tick(r.t)
                await asyncio.sleep(0)

        r.d.set_target("alpha")
        r.room = {"current": "x", "stored_restore": {}, "screens": {}, "members": {"ball.main": {"believed": "off"}}}
        r.sec = {"id": 1, "phase": "peak", "bp": 0.0}
        await run(4.0)
        assert r.d.direction["flow"]["dir"] == "cw" and puts == []       # turning, but the ball is off: hands off
        r.room["members"]["ball.main"]["believed"] = "on-word"
        await run(2.5)                                                   # on the next bar line
        assert len(puts) == 1
        path, body = puts[0]
        assert path == "/api/v1/room/targets" and body["source"] == "show"
        assert body["values"]["ball_motor.dir"] == "turn-a" and body["values"]["ball_motor.speed"] in (
            "slow-word", "fast-word")
        r.sec = {"id": 2, "phase": "breakdown", "bp": 0.0}               # inward: not a turning dir, left as it is
        await run(4.0)
        assert len(puts) == 1
        r.sec = {"id": 3, "phase": "peak", "bp": 0.0}
        r.d.lights_cfg["flow"]["mirror_ball"]["directions"] = {"cw": "turn-b"}   # a new word wanted ...
        await run(4.0)
        assert len(puts) == 1                                            # ... but within min_gap_s
        await run(30.0)
        assert len(puts) == 2 and puts[1][1]["values"]["ball_motor.dir"] == "turn-b"
        snap = r.d.snapshot()["lights"]["mirror_ball"]
        assert snap["sends"] == 2 and snap["error"] is None and snap["next_allowed_in_s"] > 0

    asyncio.run(main())


def test_the_glitterball_is_left_alone_without_its_settings_or_a_room(tmp_path):
    r = lights_rig(tmp_path, structure={"peak": "cw"})
    r.d.set_target("alpha")
    r.room = {"current": "x", "stored_restore": {}, "screens": {}, "members": {"ball.main": {"believed": "on-word"}}}
    r.sec = {"id": 1, "phase": "peak", "bp": 0.0}
    r.run(5.0)                                                           # no mirror_ball block: off; no loop: no put
    assert r.d.direction["flow"]["dir"] == "cw" and r.d.snapshot()["lights"]["mirror_ball"]["sends"] == 0


class FakeSpace:
    """space.py's interface (docs/LIGHTS.md §3.4) on a made-up room: the screens see the flow by their fixture id."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.rev = "r1"

    def shared(self):
        return self

    def poll(self, force=False):
        return False

    def view(self):
        return {"revision": self.rev, "room": {"centre": [3.0, 4.0, 1.5], "size": [6.0, 8.0, 3.0]},
                "points": {"ball": {"at": [3.0, 4.0, 2.8]}}}

    def resolve(self, view, name):
        return tuple(view["points"][name]["at"]) if name in view["points"] else None

    def screen_flow(self, view, screen_id, dir, centre):
        self.calls.append((screen_id, dir, centre))
        return {"fixture": screen_id, "x": 1.0 if dir == "right" else 0.0, "y": 1.0 if dir == "up" else 0.0,
                "angle_deg": 90.0 if dir == "up" else 0.0, "inplane": 1.0, "depth": 0.0, "radial": 0, "spin": 0,
                "phase": 0.5}

    def spatial_fn(self, view):
        return lambda fid, key, value: True


def test_the_flow_carries_each_screens_view_and_its_centre_from_the_space(tmp_path):
    r = lights_rig(tmp_path, centre="ball")
    sp = FakeSpace()
    r.d._space = sp
    r.d.set_target("alpha")
    r.sec = {"id": 1, "phase": "build", "bp": 0.3}
    r.run(3.0)
    f = r.d.direction["flow"]
    assert f["dir"] == "up" and f["at"] == "ball" and f["at_xyz"] == [3.0, 4.0, 2.8]
    assert set(f["screens"]) == {"wall-left", "wall-right"}            # keyed by the renderer's screen names
    assert f["screens"]["wall-left"]["angle_deg"] == 90.0 and ("tv.left", "up", (3.0, 4.0, 2.8)) in sp.calls
    n = len(sp.calls)
    r.run(1.0)
    assert len(sp.calls) == n                                         # cached until the dir or the space changes
    sp.rev = "r2"
    r.run(0.2)
    assert len(sp.calls) == n + 2
    assert r.d.snapshot()["lights"]["flow_screens"] == ["wall-left", "wall-right"]
