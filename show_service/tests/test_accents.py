"""Light moments (accents.py): allowed by the room's accents.<id> row, start on their events, put back at the end."""
import random

from show_service.accents import Accents, parse

CFG = {"accents": {
    "fast": {"label": "Fast", "needs": {"ball.main": "spinning"}, "set": {"ball.speed": "high"}, "on": ["drop"],
             "hold_s": [10, 10], "gap_s": 60},
    "moment": {"set": {"ball.main": "spinning"}, "on": ["track_change"], "hold_s": [5, 5], "gap_s": 0},
    "bad": {"set": {}, "on": ["drop"]}}}


def room(mode="disco", **vals):
    return {"current": mode, "members": {k.replace("__", "."): {"believed": v} for k, v in vals.items()}}


def make():
    a = Accents(rng=random.Random(1))
    a.cfg, a.problems = parse(CFG)
    a.poll = lambda now: None
    return a


def test_parse_drops_bad():
    cfg, probs = parse(CFG)
    assert set(cfg) == {"fast", "moment"} and any("bad" in p for p in probs)


def test_allowed_needs_and_put_back():
    a = make()
    a.tick(0.0, room(ball__main="spinning", ball__speed="low", accents__fast=0))
    assert a.on_event("drop", 1.0, "d1") == {}                        # not allowed
    a.tick(1.0, room(ball__main="stopped", ball__speed="low", accents__fast=100))
    assert a.on_event("drop", 2.0, "d2") == {}                        # needs the ball spinning
    a.tick(2.0, room(ball__main="spinning", ball__speed="low", accents__fast=100))
    assert a.on_event("drop", 3.0, "d3") == {"ball.speed": "high"}
    assert a.on_event("drop", 3.5, "d3") == {} and a.held() == {"ball.speed"}
    assert a.tick(12.0, room(ball__main="spinning", ball__speed="high", accents__fast=100)) == {}
    assert a.tick(13.1, room(ball__main="spinning", ball__speed="high", accents__fast=100)) == {"ball.speed": "low"}
    assert a.on_event("drop", 20.0, "d4") == {}                       # gap_s


def test_mode_change_keeps_new_mode():
    a = make()
    a.tick(0.0, room("party", ball__main="stopped", accents__moment=100))
    assert a.on_event("track_change", 1.0, "t1") == {"ball.main": "spinning"}
    assert a.tick(7.0, room("off", ball__main="spinning", accents__moment=100)) == {}
