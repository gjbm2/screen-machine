"""skip_if, "Leave it as it is while" (owner, 22 Oct: "Piano mode must leave Sonos alone when our own piano sound is
playing on it - triggered via config NOT hard coded"), and the object / wildcard value patterns it uses. Pure asyncio
with fakes; Piano here is a cut-down copy of modes.json's two Sonos rows on the fake lounge zone."""

from __future__ import annotations

import asyncio
import copy

import pytest

from room_service.roomstate.model import ConfigError, RoomConfig, pattern_problem, validate, value_matches
from room_service.roomstate.schema import entry_schema

from .harness import RADIO, boot, fake_adapters, go, make_room, row, sent

PIANO_URI = "http://192.168.1.205:8080/output/piano/chopin-op9-2.flac"
OURS_VALUE = {"uri": PIANO_URI, "title": "Chopin", "volume": 40}
OURS = [{"uri": "http://*/output/piano/*"}]          # our own piano sound, by its address


def piano_rows(volume_policy: str = "ignore") -> dict:
    when = {"sonos.lounge.playback": copy.deepcopy(OURS)}
    return {
        "sonos.lounge.playback": {"set": "paused", "restore": True, "store_if": ["favourite", "uri"], "policy": "ignore",
                                  "skip_if": when},
        "sonos.lounge.volume": {"set": 0, "restore": True, "policy": volume_policy, "skip_if": copy.deepcopy(when)},
    }


def config(volume_policy: str = "ignore", steps: list | None = None, extra: dict | None = None) -> dict:
    return {
        "default": "gallery",
        "modes": {
            "gallery": {"label": "Gallery", "state": {"plug.dmx_power": "off"}},
            "piano": {"label": "Piano", "state": {"plug.dmx_power": "on", **piano_rows(volume_policy), **(extra or {})}},
        },
        "transitions": [{"from": "*", "to": "*", "steps": steps or ["rest"]}],
    }


# value patterns ---------------------------------------------------------------------------------------------------
def test_existing_patterns_match_as_before():
    live = {"live": {"scene": "x"}}
    assert value_matches("*", None) and value_matches("*", {"a": 1}) and value_matches("*", 3)
    assert value_matches(5, 5) and not value_matches(5, 6)
    assert value_matches("live", live) and not value_matches("live", "art") and not value_matches("art", live)
    assert value_matches("li*", "li*") and not value_matches("li*", live)       # a wildcard is never a key test
    assert value_matches({}, {}) and not value_matches({}, {"a": 1})              # an empty object: equal only
    assert value_matches({"a": 1}, {"a": 1}) and value_matches([1, 2], [1, 2]) and not value_matches([1], [1, 2])
    assert value_matches(None, None) and not value_matches("favourite", "paused")
    assert value_matches("uri", RADIO) and not value_matches("favourite", RADIO)   # store_if ["favourite", "uri"]


def test_wildcard_strings_and_object_patterns():
    assert value_matches("http://*/output/piano/*", PIANO_URI)
    assert value_matches("x?", "xy") and value_matches("[ab]c", "bc") and not value_matches("[ab]c", "cc")
    assert not value_matches("Http://*", PIANO_URI)                               # case-sensitive
    assert not value_matches("1*", 10) and not value_matches("*x", None)          # strings only
    assert value_matches(OURS[0], OURS_VALUE) and value_matches(OURS[0], {"uri": PIANO_URI})
    assert not value_matches(OURS[0], RADIO) and not value_matches(OURS[0], "paused")
    assert not value_matches({"uri": "*"}, {"favourite": "Radio 4"})              # the key must be there
    assert value_matches({"uri": "*"}, RADIO) and value_matches({"volume": 40}, OURS_VALUE)
    assert not value_matches({"volume": 41}, OURS_VALUE)
    assert value_matches({"a": {"b": "x*"}}, {"a": {"b": "xy", "c": 1}, "d": 2})  # nested, same rules
    assert value_matches({"live": "scene"}, {"live": {"scene": "x"}})             # a key test inside an object
    assert not value_matches({"uri": OURS[0]["uri"], "title": "Bach"}, OURS_VALUE)  # every key must match


def test_patterns_are_checked():
    assert pattern_problem({}) and pattern_problem({"a": {}}) and pattern_problem({1: "x"})
    assert pattern_problem(object())
    for ok in ("*", "x*", 3, None, True, [], [{}], {"uri": "*"}, {"a": {"b": [1]}}):
        assert pattern_problem(ok) is None


# the model --------------------------------------------------------------------------------------------------------
def adapters() -> list:
    return list(vars(fake_adapters()).values())


def problems(raw: dict) -> list[dict]:
    return validate(raw, adapters())


def test_skip_if_resolves_targets_groups_and_prefixes():
    raw = config(extra={"plug.dmx_power": {"set": "on", "skip_if": {"sonos": [{"uri": "*"}], "lounge": ["paused"]}}})
    raw["groups"] = {"lounge": ["sonos.lounge.playback"]}
    assert problems(raw) == []
    cfg = RoomConfig(raw, adapters())
    e = cfg.modes["piano"].state["sonos.lounge.volume"]
    assert e.skip_if == {"sonos.lounge.playback": OURS}
    assert e.skip_targets == (("sonos.lounge.playback", tuple(OURS)),)
    plug = dict(cfg.modes["piano"].state["plug.dmx_power"].skip_targets)
    assert plug == {"sonos.lounge.volume": ({"uri": "*"},), "sonos.lounge.playback": ({"uri": "*"}, "paused")}
    assert cfg.modes["gallery"].state["plug.dmx_power"].skip_targets == ()


@pytest.mark.parametrize("skip_if, code, text", [
    ({"sonos.nope.playback": ["*"]}, "bad_leave_alone_while",
     "'Leave it as it is while' names an unknown setting: sonos.nope.playback"),
    ({"sonoss.lounge.playback": ["*"]}, "bad_leave_alone_while",             # a typo is never a new placeholder
     "'Leave it as it is while' names an unknown setting: sonoss.lounge.playback"),
    ({"sonos.lounge.playback": []}, "bad_leave_alone_while",
     "'Leave it as it is while' needs a setting and the values to look for"),
    ({"sonos.lounge.playback": "paused"}, "bad_leave_alone_while",
     "'Leave it as it is while' needs a setting and the values to look for"),
    ({}, "bad_leave_alone_while", "'Leave it as it is while' needs a setting and the values to look for"),
    (["sonos.lounge.playback"], "bad_leave_alone_while",
     "'Leave it as it is while' needs a setting and the values to look for"),
    ({"sonos.lounge.playback": [{}]}, "bad_pattern",
     "A value to look for can't be used: an empty {} names nothing to look for"),
])
def test_bad_skip_if_is_a_problem_in_page_words(skip_if, code, text):
    raw = config()
    raw["modes"]["piano"]["state"]["sonos.lounge.volume"]["skip_if"] = skip_if
    out = problems(raw)
    assert [(p["code"], p["text"], p["mode"], p["target"]) for p in out] == [(code, text, "piano", "sonos.lounge.volume")]
    with pytest.raises(ConfigError):
        RoomConfig(raw, adapters())


def test_store_if_and_when_patterns_are_checked_too():
    raw = config()
    raw["modes"]["piano"]["state"]["sonos.lounge.playback"]["store_if"] = ["uri", {}]
    raw["transitions"].insert(0, {"from": "*", "to": "*", "when": {"sonos.lounge.playback": {"to": {}}},
                                  "steps": ["rest"]})
    out = problems(raw)
    assert {(p["path"], p["code"]) for p in out} == {
        ("/modes/piano/state/sonos.lounge.playback/store_if/1", "bad_pattern"),
        ("/transitions/0/when/sonos.lounge.playback/to", "bad_pattern")}
    raw = config()                                        # object patterns in a rule's `when` are fine
    raw["transitions"].insert(0, {"from": "*", "to": "*", "when": {"sonos.lounge.playback": {"from": OURS[0]}},
                                  "steps": ["rest"]})
    assert problems(raw) == []


def test_the_page_gets_the_option_and_the_rows_as_written(tmp_path):
    s = entry_schema()["properties"]["skip_if"]
    assert s["x-ui"]["label"] == "Leave it as it is while" and s["x-ui"]["advanced"] is True and s["type"] == "object"
    assert "piano" in s["x-ui"]["description"]
    r = make_room(tmp_path, config())
    d = r.c.describe()
    piano = d["modes"]["piano"]["entries"]
    assert piano["sonos.lounge.volume"]["skip_if"] == {"sonos.lounge.playback": OURS}
    assert "skip_if" not in piano["plug.dmx_power"]
    assert d["targets"]["sonos.lounge.volume"]["entry_schema"]["properties"]["skip_if"]["x-ui"]["label"] == \
        "Leave it as it is while"


# the coordinator ------------------------------------------------------------------------------------------------
def test_entering_while_our_piano_plays_leaves_sonos_alone(tmp_path):
    """Not stored (no put-back slot), not set, a skipped row saying why in plain words; the switch is ok; leaving
    puts nothing back; plan() shows the same."""
    async def body():
        r = make_room(tmp_path, config())
        await boot(r)
        r.sonos.hand_set("lounge.playback", dict(OURS_VALUE))   # show_service started our piano sound
        p = await r.c.plan("piano", refresh=True)
        assert {x["subsystem"]: x["reason"] for x in p["steps"] if x.get("reason")} == {
            "sonos.lounge.playback": "left_alone", "sonos.lounge.volume": "left_alone"}
        r.log.clear()
        tr = await go(r, "piano", source="autopilot")
        assert tr["status"] == "ok" and sent(r, "sonos") == [] and sent(r, "plug") == [{"dmx_power": "on"}]
        for t in ("sonos.lounge.playback", "sonos.lounge.volume"):
            x = row(tr, t)
            assert x["status"] == "skipped" and x["reason"] == "left_alone"
            assert x["detail"].startswith("left alone: lounge.playback is ") and "chopin" in x["detail"]
            assert tr["notes"][t] == x["detail"]
            assert t not in r.c.s["desired"]
        assert tr["left_alone"] == ["sonos.lounge.playback", "sonos.lounge.volume"]
        assert not any(t.startswith("sonos.") for t in r.c.s["stored"].get("_restore", {}))
        assert row(tr, "sonos.lounge.volume")["action"] == "left alone, not set 0"
        await r.c.reconcile_once()
        assert sent(r, "sonos") == []
        r.sonos.hand_set("lounge.playback", "stopped")            # the piece ended
        tr = await go(r, "gallery")
        assert sent(r, "sonos") == [] and row(tr, "sonos.lounge.volume") is None
        assert r.sonos.value["lounge.volume"] == 25
    asyncio.run(body())


def test_condition_false_applies_as_usual(tmp_path):
    async def body():
        r = make_room(tmp_path, config())
        await boot(r)                                          # the radio is playing at 25
        tr = await go(r, "piano")
        assert tr["status"] == "ok" and "left_alone" not in tr
        assert r.sonos.value == {"lounge.volume": 0, "lounge.playback": "paused"}
        assert r.c.s["stored"]["_restore"]["sonos.lounge.playback"]["value"] == RADIO
        assert r.c.s["stored"]["_restore"]["sonos.lounge.volume"]["value"] == 25
        assert row(tr, "sonos.lounge.volume")["status"] == "ok"
        await go(r, "gallery")
        assert r.sonos.value == {"lounge.volume": 25, "lounge.playback": RADIO}    # put back
    asyncio.run(body())


def test_another_targets_condition_is_read_fresh_and_unknown_never_holds(tmp_path):
    """The plug row looks at Sonos (a prefix), which nothing else in Piano names: the entry reads it first. With Sonos
    unreadable the condition can't hold, so the row applies."""
    async def body():
        raw = config()
        raw["modes"]["piano"]["state"] = {"plug.dmx_power": {"set": "on", "skip_if": {"sonos.lounge": OURS}}}
        r = make_room(tmp_path, raw)
        await boot(r)
        assert "sonos.lounge.playback" not in r.c.s["believed"]   # never read so far
        r.sonos.hand_set("lounge.playback", dict(OURS_VALUE))
        tr = await go(r, "piano")
        assert row(tr, "plug.dmx_power")["reason"] == "left_alone" and r.plug.value["dmx_power"] == "off"
        await go(r, "gallery")
        r.sonos.offline = True
        tr = await go(r, "piano")
        assert row(tr, "plug.dmx_power")["status"] == "ok" and r.plug.value["dmx_power"] == "on"
    asyncio.run(body())


def test_a_step_checks_again_before_it_sends(tmp_path):
    """Our piano starts after the entry's reading but before the step that would pause Sonos: that step leaves it
    alone, and what the entry stored for it is taken back (nothing to put back later)."""
    async def body():
        r = make_room(tmp_path, config(steps=["plug.dmx_power", {"delay": 0.01}, "sonos", "rest"]))
        await boot(r)
        orig = r.plug.apply

        async def plug_then_piano(desired, opts=None):
            out = await orig(desired, opts)
            r.sonos.hand_set("lounge.playback", dict(OURS_VALUE))
            return out
        r.plug.apply = plug_then_piano
        r.log.clear()
        tr = await go(r, "piano")
        assert tr["status"] == "ok" and sent(r, "sonos") == []
        assert {row(tr, t)["reason"] for t in ("sonos.lounge.playback", "sonos.lounge.volume")} == {"left_alone"}
        assert not any(t.startswith("sonos.") for t in r.c.s["stored"].get("_restore", {}))
        assert not any(t.startswith("sonos.") for t in r.c.s["desired"])
        assert sorted(tr["left_alone"]) == ["sonos.lounge.playback", "sonos.lounge.volume"]
    asyncio.run(body())


def test_enforce_does_not_reassert_while_the_condition_holds(tmp_path):
    async def body():
        r = make_room(tmp_path, config(volume_policy="enforce"))
        await boot(r)
        await go(r, "piano")                                  # radio: paused, volume 0, kept there
        r.sonos.hand_set("lounge.playback", dict(OURS_VALUE))
        r.sonos.hand_set("lounge.volume", 40)
        r.log.clear()
        await r.c.reconcile_once()
        assert sent(r, "sonos") == [] and r.sonos.value["lounge.volume"] == 40
        d = r.c.s["desired"]["sonos.lounge.volume"]
        assert d["reasserted"] is False and r.c.s["drift"]["sonos.lounge.volume"]["observed"] == 40
        r.sonos.hand_set("lounge.playback", "stopped")         # the piece ended: Piano's volume comes back, once
        await r.c.reconcile_once()
        assert sent(r, "sonos") == [{"lounge.volume": 0}] and r.c.s["desired"]["sonos.lounge.volume"]["reasserted"]
    asyncio.run(body())


def test_no_retry_while_the_condition_holds(tmp_path):
    async def body():
        clock = [1000.0]
        r = make_room(tmp_path, config(), now=lambda: clock[0])
        await boot(r)
        r.sonos.fail = {"lounge.volume"}                      # deaf: the volume command never lands
        tr = await go(r, "piano")
        assert row(tr, "sonos.lounge.volume")["status"] == "unconfirmed"
        assert r.c.s["desired"]["sonos.lounge.volume"]["retry"]["attempts"] == 0
        r.sonos.fail = set()
        r.sonos.hand_set("lounge.playback", dict(OURS_VALUE))
        clock[0] += 31                                        # the retry is due
        r.log.clear()
        await r.c.reconcile_once()
        assert sent(r, "sonos") == [] and r.c.s["desired"]["sonos.lounge.volume"]["retry"]["attempts"] == 0
        r.sonos.hand_set("lounge.playback", "stopped")
        await r.c.reconcile_once()
        assert sent(r, "sonos") == [{"lounge.volume": 0}]
    asyncio.run(body())
