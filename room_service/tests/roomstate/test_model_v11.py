"""The v1.1 config layer (ROOM_STATE_MODEL §2.4, §2.5, §5.1): selectors and specificity, the extends merge rules,
transition rules and @name, signals, placeholders, normalize at load, slot checks and lint codes.
Pure: configs and fake adapters only, no coordinator, no I/O.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path

import pytest

from .harness import current, plain

from room_service.roomstate.contract import Adapter, Outcome, Placeholder
from room_service.roomstate.fakes import FakeEdge, FakeLevel
from room_service.roomstate.model import ConfigError, RoomConfig, mode_signals, revision, sets_value, validate
from room_service.roomstate.schema import enum_schema, range_schema, room_config_schema

EXAMPLE = json.loads((Path(__file__).parents[2] / "roomstate" / "example_room.json").read_text())
SCREENS = ["north-screen", "south-screen", "devtest"]
PRIVACY = {"blinds.ch02", "blinds.ch03", "blinds.ch14"}


class Blinds(FakeLevel):
    """Positions 0..100 with "raised"/"lowered" as aliases: normalize() returns the canonical int."""

    def normalize(self, member, value):
        return super().normalize(member, {"raised": 0, "lowered": 100}.get(value, value))


class Log(Adapter):
    subsystem, kind, members = "log", "action", []


def adapters(**replace) -> list[Adapter]:
    """The §5.3 subsystems as fakes; replace=None removes one (it then has no adapter)."""
    a = {
        "screens": FakeLevel("screens", {s: "art" for s in SCREENS}),
        "tv": FakeLevel("tv", {s: "art_mode" for s in SCREENS[:2]},
                        schemas={"*": enum_schema(["standby", "art_mode", "hdmi"], "TV")}),
        "sensor_mask": FakeLevel("sensor_mask", {"main": "follow"},
                                 schemas={"*": enum_schema(["follow", "suspended"], "Sensor mask")}),
        "page": FakeLevel("page", {f"{s}.timed_reload": True for s in SCREENS}, schemas={"*": {"type": "boolean"}}),
        "blinds": Blinds("blinds", {f"ch{i:02d}": 0 for i in range(2, 15)}, schemas={"*": range_schema(0, 100, "Blind")}),
        "mirror_ball": FakeEdge("mirror_ball", ["main"], schema=enum_schema(["stopped", "spinning"], "Mirror ball")),
        "plug": FakeLevel("plug", {"dmx_power": "off"}, schemas={"*": enum_schema(["off", "on"], "DMX power")}),
        "sonos": FakeLevel("sonos", {"lounge.volume": 25, "lounge.playback": "paused"},
                           schemas={"lounge.volume": range_schema(0, 100, "Volume")}),
        "log": Log(),
    }
    a.update(replace)
    return [x for x in a.values() if x is not None]


def raw_with(patch=None) -> dict:
    raw = copy.deepcopy(EXAMPLE)
    if patch:
        patch(raw)
    return raw


def load(patch=None, **replace) -> RoomConfig:
    return RoomConfig(raw_with(patch), adapters(**replace))


def errors(patch, **replace) -> dict[str, str]:
    return {e["path"]: e["message"] for e in validate(raw_with(patch), adapters(**replace))}


def codes(cfg: RoomConfig) -> set[str]:
    return {w["code"] for w in current(cfg.lint())}


def state(mode: str, **entries):
    """A patch that replaces one mode's own state."""
    return lambda raw: raw["modes"][mode].update(state=entries)


# --- the example ------------------------------------------------------------------------------------------

def test_the_example_loads_clean_with_no_warnings():
    assert validate(EXAMPLE, adapters()) == []
    cfg = load()
    assert current(cfg.lint()) == []
    assert cfg.default == "normal" and cfg.concrete() == ["normal", "piano", "off", "ambient", "disco"]
    assert cfg.modes["disco"].family == ["house", "live", "disco"]
    assert cfg.modes["live"].descendants == {"live", "ambient", "disco"}
    assert len(cfg.groups["all_blinds"]) == 13                       # a group can be a single selector
    disco = cfg.modes["disco"].state
    assert disco["blinds.ch02"].verb() == "store+set" and disco["blinds.ch02"].policy == "enforce"
    assert disco["sonos.lounge.volume"].opts == {"over_s": 5}
    assert disco["screens.devtest"].set == "art"                     # frames is only the two frame screens
    assert cfg.modes["normal"].state["sonos.lounge.playback"].retrieve == "pre_live"


# --- selectors ----------------------------------------------------------------------------------------------

def test_the_more_specific_selector_wins_whatever_the_key_order():
    values = {"blinds": 10, "privacy_blinds": 20, "blinds.ch03": 30}
    for keys in (list(values), list(reversed(values))):
        cfg = load(state("normal", **{k: values[k] for k in keys}))
        got = {t: e.set for t, e in cfg.modes["normal"].state.items() if t.startswith("blinds.")}
        assert (got["blinds.ch03"], got["blinds.ch02"], got["blinds.ch14"], got["blinds.ch05"]) == (30, 20, 20, 10)
        assert cfg.modes["normal"].origin["blinds.ch02"] == ("normal", "privacy_blinds")


def test_equally_specific_selectors_conflict_only_with_different_entries():
    def groups(raw):
        raw["groups"]["left_blinds"] = ["blinds.ch02", "blinds.ch05"]
    with pytest.raises(ConfigError, match="blinds.ch02: named by 'privacy_blinds' and 'left_blinds', equally specific"):
        load(lambda raw: (groups(raw), state("normal", privacy_blinds=50, left_blinds=60)(raw)))
    with pytest.raises(ConfigError, match="equally specific"):
        load(state("normal", **{"blinds": 1, "blinds.*": 2}))         # a prefix and a wildcard are both 1
    cfg = load(lambda raw: (groups(raw), state("normal", privacy_blinds=50, left_blinds=50)(raw)))
    assert cfg.modes["normal"].state["blinds.ch02"].set == 50


def test_prefixes_expand_at_dot_boundaries_and_wildcards_cross_dots():
    cfg = load(lambda raw: raw["groups"].update(lounge="sonos.lounge", reloads="*.timed_reload", pages="page"))
    assert set(cfg.groups["lounge"]) == {"sonos.lounge.volume", "sonos.lounge.playback"}
    assert cfg.groups["reloads"] == cfg.groups["pages"] == [f"page.{s}.timed_reload" for s in SCREENS]
    for bad in ("sonos.lou", "blinds.ch99", "page.*.reload"):
        with pytest.raises(ConfigError, match="matches no target"):
            load(lambda raw: raw["groups"].update(bad=bad))
    assert load(lambda raw: raw["groups"].update(odd="blind")).groups["odd"] == []   # not blinds.*: an unknown subsystem


@pytest.mark.parametrize("name, message", [
    ("a.b", "can't contain '.'"), ("blinds", "shadow a subsystem"), ("7", "can't be a number"),
    ("rest", "be 'rest' or '\\*'"), ("*", "be 'rest' or '\\*'"),
])
def test_group_names_are_checked(name, message):
    with pytest.raises(ConfigError, match=message):
        load(lambda raw: raw["groups"].update({name: ["blinds.ch05"]}))


def test_groups_dont_nest_and_rest_is_reserved():
    assert "can't contain another group" in errors(lambda raw: raw["groups"].update(more=["privacy_blinds"]))["/groups/more/0"]
    assert "reserved" in errors(state("normal", rest=1))["/modes/normal/state/rest"]
    assert "reserved" in errors(lambda raw: raw["transitions"][2].update(steps=[["rest", "frames"]]))[
        "/transitions/2/steps/0/0"]


# --- entries and the merge rules -------------------------------------------------------------------------

def test_the_four_extends_merge_rules():
    def patch(raw):
        raw["modes"]["karaoke"] = {"extends": "disco", "state": {
            "blinds.ch02": 50,                                        # 1 over store+set: store, policy kept
            "sonos.lounge.volume": {"set": 70},                       # 1: opts {"over_s": 5} kept
            "blinds.ch14": {"store": "pre_live", "set": 90},          # 2 replaces: enforce is gone
            "blinds.ch03": {"policy": "respect"},                     # 3 patches
            "tv.south-screen": None}}                                 # 4 drops
        raw["modes"]["quiet"] = {"extends": "house", "state": {
            "blinds.ch02": {"set": 40, "opts": {"x": 1}},             # 1 over a retrieve: a set entry
            "blinds.ch03": {"policy": "respect", "max_age_s": 60}}}   # 3 over a retrieve keeps the retrieve
    cfg = load(patch)
    k, q = cfg.modes["karaoke"].state, cfg.modes["quiet"].state
    assert (k["blinds.ch02"].set, k["blinds.ch02"].store, k["blinds.ch02"].policy) == (50, "pre_live", "enforce")
    assert (k["sonos.lounge.volume"].set, k["sonos.lounge.volume"].opts) == (70, {"over_s": 5})
    assert (k["blinds.ch14"].set, k["blinds.ch14"].store, k["blinds.ch14"].policy) == (90, "pre_live", "respect")
    assert (k["blinds.ch03"].set, k["blinds.ch03"].store, k["blinds.ch03"].policy) == (100, "pre_live", "respect")
    assert "tv.south-screen" not in k and "tv.north-screen" in k
    assert cfg.modes["karaoke"].origin["blinds.ch02"] == ("karaoke", "blinds.ch02")
    assert cfg.modes["karaoke"].origin["screens.north-screen"] == ("disco", "frames")
    assert q["blinds.ch02"].verb() == "set" and q["blinds.ch02"].policy == "respect"
    assert q["blinds.ch02"].max_age_s is None and q["blinds.ch02"].opts == {"x": 1}
    assert (q["blinds.ch03"].retrieve, q["blinds.ch03"].policy, q["blinds.ch03"].max_age_s) == ("pre_live", "respect", 60)
    assert q["blinds.ch14"].policy == "ignore"                        # a retrieve's default policy


@pytest.mark.parametrize("entry, path, message", [
    ({"store": "pre_live", "set": 100, "max_age_s": 60}, "/max_age_s", "max_age_s is allowed on retrieve only"),
    ({"set": 100, "else": 0}, "/else", "else is allowed on retrieve only"),
    ({"retrieve": "pre_live", "set": 1}, "", "retrieve can't be combined"),
    ({"retrieve": "pre_live", "policy": "sometimes"}, "/policy", "policy must be one of"),
    ({"retrieve": "pre_live", "max_age_s": -1}, "/max_age_s", "positive number"),
    ({"store": "", "set": 1}, "/store", "needs a slot name"),
    ({"set": 1, "colour": "red"}, "", "unknown keys \\['colour'\\]"),
    ({"policy": "enforce"}, "", "needs one of set, store, retrieve"),        # nothing inherited to patch
])
def test_entries_are_checked(entry, path, message):
    found = errors(state("normal", **{"blinds.ch05": entry}))
    assert re.search(message, found.get("/modes/normal/state/blinds.ch05" + path, "")), found


def test_an_options_patch_must_leave_a_valid_entry():
    with pytest.raises(ConfigError, match="max_age_s allowed on retrieve only"):
        load(state("normal", sensor_mask={"max_age_s": 5}))           # house sets the mask: no retrieve to patch


def test_values_are_normalized_at_load():
    cfg = load(lambda raw: raw["modes"]["live"]["state"]["privacy_blinds"].update(set="lowered"))
    assert cfg.modes["live"].state["blinds.ch02"].set == 100 and cfg.modes["disco"].state["blinds.ch14"].set == 100
    cfg = load(lambda raw: raw["modes"]["house"]["state"]["privacy_blinds"].update({"else": "raised"}))
    assert cfg.modes["normal"].state["blinds.ch03"].else_ == 0
    found = errors(lambda raw: (raw["modes"]["disco"]["state"].update(privacy_blinds={"store": "pre_live", "set": 120}),
                                raw["modes"]["ambient"].update(state={"frame_tvs": "projector"}),
                                raw["modes"]["house"]["state"].update(frame_tvs={"retrieve": "pre_live", "else": "off"})))
    assert found["/modes/disco/state/privacy_blinds/set"] == "blinds.ch02: 120 is above maximum 100"
    assert "tv.north-screen: 'projector' is not one of" in found["/modes/ambient/state/frame_tvs"]
    assert "'off' is not one of" in found["/modes/house/state/frame_tvs/else"]


# --- slots -----------------------------------------------------------------------------------------------

def test_every_retrieved_slot_target_must_be_stored_by_some_mode():
    with pytest.raises(ConfigError, match="frame_tvs/retrieve: no mode stores tv.north-screen, tv.south-screen into slot 'pre_lvie'"):
        load(lambda raw: raw["modes"]["house"]["state"].update(frame_tvs={"retrieve": "pre_lvie"}))
    with pytest.raises(ConfigError, match="no mode stores blinds.ch05"):
        load(lambda raw: raw["modes"]["house"]["state"].update({"blinds.ch05": {"retrieve": "pre_live"}}))
    cfg = load(lambda raw: (raw["modes"]["house"]["state"].update({"blinds.ch05": {"retrieve": "pre_live"}}),
                            raw["modes"].update(dim={"extends": "live", "state": {
                                "blinds.ch05": {"store": "pre_live", "set": 100}}})))      # §2.6
    assert cfg.uses("pre_live", "blinds.ch05") and current(cfg.lint()) == []


def test_helpers_for_the_coordinator():
    cfg = load()
    assert sets_value(cfg.modes["disco"], "blinds.ch02") == 100
    assert sets_value(cfg.modes["normal"], "blinds.ch02") is None     # a retrieve
    assert sets_value(cfg.modes["normal"], "blinds.ch05") is None     # no entry
    assert cfg.uses("pre_live", "sonos.lounge.volume") and not cfg.uses("pre_live", "blinds.ch05")
    assert cfg.is_concrete("disco") and not cfg.is_concrete("live") and not cfg.is_concrete("gone")
    with pytest.raises(ConfigError, match="abstract"):
        cfg.mode("live")
    with pytest.raises(ConfigError, match="no such mode"):
        cfg.mode("gone")


# --- transition rules ------------------------------------------------------------------------------------

def test_at_name_means_the_mode_and_its_descendants_and_star_matches_first_boot():
    cfg = load(lambda raw: raw["modes"].update(karaoke={"extends": "disco"}))
    rules = {(r["from"], r["to"]): r["rule"] for r in cfg.rules()}
    assert rules[(None, "karaoke")] == rules[("normal", "karaoke")] == rules[("disco", "ambient")] == 0
    assert rules[("karaoke", "normal")] == 1 and rules[("normal", "piano")] == rules[(None, "normal")] == 2


def test_from_set_picks_the_safe_rule_for_an_unfinished_chain():
    cfg = load()
    assert cfg.transition_for({"normal", "disco"}, "piano") == 1     # was heading out of live: exit order
    assert cfg.transition_for({"normal"}, "piano") == 2
    assert cfg.transition_for({None}, "disco") == 0
    steps = cfg.plan_steps({"disco"}, "normal")
    assert [(s.kind, s.n) for s in steps] == [("targets", 1), ("targets", 2), ("targets", 3), ("rest", 4)]


def test_lists_and_rest_appended():
    cfg = load(lambda raw: raw["transitions"].insert(0, {"from": ["piano", "@live"], "to": "off", "steps": ["frame_tvs"]}))
    assert cfg.transition_for("piano", "off") == cfg.transition_for("disco", "off") == 0
    assert cfg.transition_for("normal", "off") == 3
    assert [(s.kind, s.n) for s in cfg.plan_steps({"piano"}, "off")] == [("targets", 1), ("rest", 2)]


@pytest.mark.parametrize("rule, path, message", [
    ({"from": "*", "to": "live", "steps": []}, "/transitions/0/to", "'live' is abstract and never entered: did you mean '@live'"),
    ({"from": "@nowhere", "to": "*", "steps": []}, "/transitions/0/from", "unknown mode '@nowhere'"),
    ({"from": ["normal", "nowhere"], "to": "*", "steps": []}, "/transitions/0/from/1", "unknown mode 'nowhere'"),
    ({"from": "*", "to": "*", "steps": [], "after": "x"}, "/transitions/0/after", "'after' is gone"),
])
def test_rule_endpoints_are_checked(rule, path, message):
    assert message in errors(lambda raw: raw["transitions"].insert(0, rule))[path]


def test_a_catch_all_rule_is_required():
    with pytest.raises(ConfigError, match=r"/transitions: a '\* -> \*' rule is required"):
        load(lambda raw: raw["transitions"].pop())


def test_on_fail_forms():
    cfg = load()
    entry, exit_ = cfg.transitions[0].steps[1], cfg.transitions[1].steps[1]
    assert entry.abort_on == PRIVACY and entry.rollback and not entry.allow_skipped
    assert entry.targets == ["blinds.ch02", "blinds.ch03", "blinds.ch14", "tv.north-screen", "tv.south-screen"]
    assert exit_.abort_on == {"plug.dmx_power"} and exit_.rollback is False
    assert cfg.transitions[0].steps[0].abort_on == set()              # continue
    step = lambda raw: raw["transitions"][0]["steps"][1]                # noqa: E731
    cfg = load(lambda raw: step(raw).update(on_fail="abort"))
    assert cfg.transitions[0].steps[1].abort_on == set(entry.targets)
    cfg = load(lambda raw: step(raw)["on_fail"].update(allow_skipped=True))
    assert cfg.transitions[0].steps[1].allow_skipped
    p = "/transitions/0/steps/1/on_fail"
    assert "on_fail must be" in errors(lambda raw: step(raw).update(on_fail="stop"))[p]
    assert "unknown key 'retry'" in errors(lambda raw: step(raw)["on_fail"].update(retry=1))[p + "/retry"]
    assert "rollback must be true or false" in errors(lambda raw: step(raw)["on_fail"].update(rollback="no"))[p + "/rollback"]
    assert "doesn't send: ['mirror_ball.main']" in errors(
        lambda raw: step(raw)["on_fail"].update(abort_on=["privacy_blinds", "mirror_ball"]))[p + "/abort_on"]


def test_steps_are_checked():
    p = "/transitions/0/steps/3"
    assert "delay must be" in errors(lambda raw: raw["transitions"][0]["steps"][3].update(delay="2 s"))[p + "/delay"]
    assert "can't parse step" in errors(lambda raw: raw["transitions"][0]["steps"].__setitem__(3, {"wait": 2}))[p]
    assert "'after' is gone" in errors(
        lambda raw: raw["transitions"][0]["steps"].__setitem__(3, {"mirror_ball": {"after": "screens.live"}}))[p]


# --- signals ---------------------------------------------------------------------------------------------

def test_signals_parse_subsystem_member_action():
    cfg = load(lambda raw: (raw["modes"]["disco"].update(on_enter=[
        {"signal": "sonos.lounge.pause"}, {"signal": "maestro.show.disco.play", "args": {"at": 1}}]),
        raw["transitions"][2]["steps"].insert(0, {"signal": "log.warning", "args": {"msg": "hi"}})))
    log = cfg.modes["live"].on_enter[0]
    assert (log.subsystem, log.member, log.action, log.args) == ("log", None, "info", {"msg": "show starting"})
    pause, play = cfg.modes["disco"].on_enter
    assert (pause.signal, pause.subsystem, pause.member, pause.action) == ("sonos.lounge.pause", "sonos", "lounge", "pause")
    assert (play.subsystem, play.member, play.action, play.args) == ("maestro", "show.disco", "play", {"at": 1})
    assert cfg.adapters["maestro"].placeholder                       # no adapter yet: a placeholder
    rule = cfg.transitions[2].steps
    assert [(s.kind, s.n, s.action) for s in rule] == [("signal", 1, "warning"), ("rest", 2, "")]


def test_mode_signals_are_their_own_not_inherited():
    cfg = load()
    assert cfg.modes["disco"].on_enter == [] and cfg.modes["ambient"].on_exit == []
    assert mode_signals(cfg, "live", "on_exit")[0].args == {"msg": "show over"}
    assert mode_signals(cfg, "renamed", "on_exit") == []


@pytest.mark.parametrize("sig, path, message", [
    ({"signal": "pause"}, "/signal", "'<subsystem>.<action>' or '<subsystem>.<member>.<action>'"),
    ({"signal": "log..info"}, "/signal", "must be"),
    ({"signal": "log.info", "args": [1]}, "/args", "args must be an object"),
    ("log.info", "", "expected a signal"),
])
def test_bad_signals(sig, path, message):
    found = errors(lambda raw: raw["modes"]["disco"].update(on_enter=[sig]))
    assert message in found["/modes/disco/on_enter/0" + path]


# --- placeholders --------------------------------------------------------------------------------------

def test_placeholders_from_device_config_check_their_values():
    ball = Placeholder("mirror_ball", ["main"], values=["spinning", "stopped"])
    cfg = load(mirror_ball=ball)
    assert cfg.modes["disco"].state["mirror_ball.main"].set == "spinning" and current(cfg.lint()) == []
    with pytest.raises(ConfigError, match="mirror_ball.main: 'spining' is not one of"):
        load(lambda raw: raw["modes"]["disco"]["state"].update(mirror_ball="spining"), mirror_ball=ball)
    cfg = load(lambda raw: raw["modes"]["disco"]["state"].update(mirror_ball="spining"),
               mirror_ball=Placeholder("mirror_ball", ["main"]))          # no values: accepted, with a warning
    assert codes(cfg) == {"placeholder_values_unchecked"}


def test_subsystems_without_adapters_become_placeholders():
    cfg = load(plug=None, sonos=None)
    assert cfg.adapters["plug"].placeholder and cfg.members["plug"] == ["dmx_power"]
    assert set(cfg.members["sonos"]) == {"lounge.volume", "lounge.playback"}    # "sonos.lounge" stays a prefix
    assert cfg.modes["normal"].state["sonos.lounge.volume"].retrieve == "pre_live"
    assert cfg.transitions[1].steps[1].abort_on == {"plug.dmx_power"}            # the guard still has its target
    lint = {w["code"]: w for w in current(cfg.lint())}
    assert lint["guard_inactive"]["path"] == "/transitions/1/steps/1/on_fail"
    assert lint["unknown_subsystem"]["path"].startswith("/")                    # where plug/sonos were first named
    with pytest.raises(ConfigError, match="matches no target"):
        load(state("normal", **{"blinds.ch99": 1}), plug=None)


def test_an_unknown_bare_name_is_a_placeholder_with_a_warning():
    cfg = load(state("normal", privacy_blind=100))                       # a typo of a group
    assert any(w["code"] == "unknown_subsystem" and w["path"] == "/modes/normal/state/privacy_blind"
               and "'privacy_blind'" in w["message"] for w in current(cfg.lint()))


# --- load checks -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("patch, path, message", [
    (lambda raw: raw["modes"]["live"].update({"class": "live"}), "/modes/live/class", "'class' is gone"),
    (lambda raw: raw["modes"]["disco"].update(sequence=[["blinds", "screens"]]), "/modes/disco/sequence",
     "'sequence' is gone"),
    (lambda raw: raw.update(sequence=[]), "/sequence", "'sequence' is gone"),
    (lambda raw: raw.update(colour="red"), "/colour", "unknown key 'colour'"),
    (lambda raw: raw["modes"]["disco"].update(lable="Disco"), "/modes/disco/lable", "unknown key"),
    (lambda raw: raw["transitions"][0].update(stpes=[]), "/transitions/0/stpes", "unknown key"),
    (lambda raw: raw["modes"]["live"].update(extends="disco"), "/modes/disco/extends", "extends cycle"),
    (lambda raw: raw["modes"]["normal"].update(extends="nowhere"), "/modes/normal/extends", "extends unknown mode"),
    (lambda raw: raw.update(default="live"), "/default", "missing or abstract"),
    (lambda raw: raw.update(default="nowhere"), "/default", "missing or abstract"),
    (lambda raw: raw["modes"].update({"2": {"extends": "live"}}), "/modes/2", "a mode name can't be a number"),
    (lambda raw: raw["modes"]["normal"].update(state={"3": 1}), "/modes/normal/state/3", "a selector can't be a number"),
    (lambda raw: raw.update(labels={"blinds.ch99": "Nowhere"}), "/labels/blinds.ch99", "unknown target"),
])
def test_load_checks_report_json_pointers(patch, path, message):
    found = errors(patch)
    assert message in found.get(path, ""), found


def test_comment_keys_are_ignored_everywhere():
    def patch(raw):
        raw["groups"]["_why"] = "x"
        raw["modes"]["_old"] = {"label": "Old", "class": "gone"}
        raw["modes"]["disco"]["state"]["_note"] = "x"
        raw["modes"]["disco"]["state"]["mirror_ball"] = {"set": "spinning", "_why": "x"}
        raw["transitions"][0]["steps"][1]["on_fail"]["_note"] = "x"
        raw["transitions"][0]["steps"][3]["_note"] = "boot time"
        raw.update(labels={"blinds.ch02": "Lounge left", "_note": "x"})
    cfg = load(patch)
    assert "_old" not in cfg.modes and "_why" not in cfg.groups and cfg.labels == {"blinds.ch02": "Lounge left"}
    assert current(cfg.lint()) == []


def test_validate_collects_every_error():
    found = errors(lambda raw: (raw.update(default="nowhere"), raw["transitions"].pop(),
                                raw["modes"]["disco"]["state"].update(mirror_ball="wobbling")))
    assert set(found) >= {"/default", "/transitions", "/modes/disco/state/mirror_ball"}
    assert plain(validate([], adapters())) == [{"path": "", "message": "the config must be a JSON object",
                                                "code": "invalid"}]


def test_revision_tracks_content_and_key_order():
    assert revision(raw_with()) == revision(EXAMPLE)
    assert revision(raw_with(lambda raw: raw["modes"]["disco"].update(label="Disco!"))) != revision(EXAMPLE)
    reordered = raw_with()
    reordered["modes"] = dict(reversed(reordered["modes"].items()))
    assert revision(reordered) != revision(EXAMPLE)


# --- warnings ----------------------------------------------------------------------------------------------

def test_lint_codes():
    dim = {"extends": "live", "state": {"blinds.ch05": {"store": "pre_live", "set": 100}}}
    assert codes(load(lambda raw: raw["modes"].update(dim=dim))) == {"slot_never_retrieved"}
    karaoke = lambda v: lambda raw: raw["modes"].update(karaoke={"extends": "live", "state": {"sonos.lounge.volume": v}})  # noqa: E731
    lint = current(load(karaoke(70)).lint())
    assert [(w["code"], w["path"]) for w in lint] == [("set_over_retrieve", "/modes/karaoke/state/sonos.lounge.volume")]
    assert current(load(karaoke({"store": "pre_live", "set": 70})).lint()) == []
    lint = current(load(lambda raw: raw["transitions"].append({"from": "normal", "to": "piano", "steps": []})).lint())
    assert {(w["code"], w["path"]) for w in lint} == {("rule_unused", "/transitions/3"),       # behind the catch-all,
                                                      ("falls_to_catch_all", "/transitions/2")}   # so normal -> piano falls
    lint = current(load(lambda raw: raw["transitions"][1].update(to="normal")).lint())   # exit order only towards normal
    assert [w["code"] for w in lint] == ["falls_to_catch_all"] and "disco -> piano" in lint[0]["message"]


def test_guard_inactive_for_dry_run_or_placeholder_guards():
    blinds = adapters()[4]
    blinds.dry_run = True
    lint = {w["code"]: w for w in current(load(blinds=blinds).lint())}
    assert lint["guard_inactive"]["path"] == "/transitions/0/steps/1/on_fail" and "aborts" in lint["guard_inactive"]["message"]
    lint = current(load(lambda raw: raw["transitions"][0]["steps"][1]["on_fail"].update(allow_skipped=True), blinds=blinds).lint())
    assert "not enforced" in lint[0]["message"]


# --- contract and schema -----------------------------------------------------------------------------------

def test_contract_v1_defaults():
    assert Outcome("ok").retry is True and Outcome("failed", "taken_over", retry=False).retry is False
    tv = adapters()[1]
    assert tv.normalize("north-screen", "hdmi") == "hdmi"
    with pytest.raises(ValueError, match="not one of"):
        tv.normalize("north-screen", "projector")
    assert (tv.read_timeout_s, Adapter.poll_inflight_s, tv.dry_run, tv.placeholder) == (5.0, 0.5, False, False)
    assert tv.show("north-screen", {"live": {"scene": "x"}}) == '{"live":{"scene":"x"}}'
    plug = Placeholder("plug", ["dmx_power"], {"dmx_power": ["on", "off"]})
    assert plug.value_schema("dmx_power") == {"enum": ["on", "off"]} and plug.value_schema("other") == {}
    assert asyncio.run(Log().signal(None, "info", {})).status == "failed"


def test_room_config_schema_follows_the_v11_grammar():
    jsonschema = pytest.importorskip("jsonschema")
    cfg = load()
    s = room_config_schema({t: {} for t in cfg.targets}, list(cfg.groups))
    json.dumps(s)
    jsonschema.validate(EXAMPLE, s)
    for good in (lambda raw: raw["modes"]["normal"].update(state={"privacy_blinds": {"policy": "respect"}}),
                 lambda raw: raw["modes"]["disco"].update(on_enter=[{"signal": "sonos.lounge.pause"}])):
        jsonschema.validate(raw_with(good), s)
    for bad in (lambda raw: raw["modes"]["live"].update({"class": "live"}),
                lambda raw: raw["modes"]["live"]["state"]["privacy_blinds"].update(max_age_s=60),
                lambda raw: raw["modes"]["house"]["state"].update(frame_tvs={"retrieve": "x", "set": 1}),
                lambda raw: raw["transitions"][1]["steps"][1]["on_fail"].update(retry=1),
                lambda raw: raw["modes"]["live"].update(on_enter=[{"signal": "pause"}])):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(raw_with(bad), s)


def test_a_misspelt_group_is_a_warning_with_its_pointer():
    """Deviation 7 (2f, 14 Oct): an unknown name becomes a placeholder, so it loads, but validate(warnings=True) says
    where, so the Room page can show it."""
    from .harness import example, fake_adapters
    raw = example()
    raw["modes"]["live"]["state"]["privacy_blind"] = {"store": "pre_live", "set": 100}
    every = list(vars(fake_adapters()).values())
    assert validate(raw, every) == []
    w = [x for x in validate(raw, every, warnings=True) if x["code"] == "unknown_subsystem"]
    assert plain(w) == [{"path": "/modes/live/state/privacy_blind", "code": "unknown_subsystem", "level": "warning",
                         "message": w[0]["message"]}] and "misspelt" in w[0]["message"]
    assert w[0]["mode"] == "live" and w[0]["target"] == "privacy_blind" and "misspelt" in w[0]["text"]
    assert current(validate(example(), every, warnings=True)) == []
