"""aa's 21 editor tests (the self-describing API behind the Room page's editors: schemas, validate, describe, plan,
hand changes, stored-value edits, reload), ported to ROOM_STATE_MODEL v1.1. "v1.1:" marks an expectation the spec
changes. Pure asyncio with fakes.
"""

from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from .harness import EXAMPLE, RADIO, SCREENS, current, example, make_room, plain
from room_service.roomstate.coordinator import Busy, Refused
from room_service.roomstate.fakes import FakeEdge, FakeLevel, FakeSignals
from room_service.roomstate.model import RoomConfig, revision, validate
from room_service.roomstate.schema import enum_schema, range_schema, room_config_schema


def adapters(ball_kind: str = "edge_discrete") -> SimpleNamespace:
    blind = range_schema(0, 100, "Blind", unit="%", presets={"raised": 0, "lowered": 100})
    t = 0.01
    return SimpleNamespace(
        blinds=FakeLevel("blinds", {"ch02": 0, "ch03": 80, "ch14": 100, "ch09": 50}, t, tolerance=3, schemas={"*": blind}),
        tv=FakeLevel("tv", {"north-screen": "art_mode", "south-screen": "standby"}, t, schemas={"*": enum_schema(
            ["standby", "art_mode", "hdmi"], "Display", {"art_mode": "Frame Art Mode", "hdmi": "Kiosk (HDMI)"})}),
        screens=FakeLevel("screens", {s: "art" for s in SCREENS}, t,
                          schemas={"*": {"x-ui": {"label": "Screen", "widget": "screen"}}}),
        mask=FakeLevel("sensor_mask", {"main": "follow"}, t, schemas={"*": enum_schema(["follow", "suspended"], "Sensor mask")}),
        page=FakeLevel("page", {f"{s}.timed_reload": True for s in SCREENS}, t, schemas={"*": {"type": "boolean"}}),
        sonos=FakeLevel("sonos", {"lounge.volume": 25, "lounge.playback": RADIO}, t, tolerance=1,
                        schemas={"lounge.volume": range_schema(0, 100, "Volume")},
                        actions={"play": {"label": "Play", "schema": {"type": "object", "properties": {
                                     "playlist": {"type": "string", "default": "", "x-ui": {"label": "Playlist"}}}}},
                                 "pause": {"label": "Pause", "schema": {"type": "object", "properties": {}}}}),
        plug=FakeLevel("plug", {"dmx_power": "off"}, t, schemas={"*": enum_schema(["off", "on"], "DMX power")}),
        ball=FakeEdge("mirror_ball", ["main"], kind=ball_kind, schema=enum_schema(["stopped", "spinning"], "Mirror ball")),
        signals=FakeSignals("log"),
    )


def build(tmp_path, raw=None, **kw):
    return make_room(tmp_path, raw or example(), adapters=adapters(**kw))


def every():
    return list(vars(adapters()).values())


async def boot(r):
    await r.c.start(reconcile=False)
    await r.c.wait_idle()


async def go(r, mode):
    await r.c.request_mode(mode)
    await r.c.wait_idle()
    return r.c.s["last_transition"]


# --- validate -------------------------------------------------------------------------------------------------------

def test_the_example_validates_clean():
    assert validate(EXAMPLE, every()) == []


def test_validate_collects_every_error_with_json_pointers():
    raw = copy.deepcopy(EXAMPLE)
    raw["modes"]["disco"]["state"]["privacy_blinds"] = {"store": "pre_live", "set": 120}
    raw["modes"]["ambient"]["state"] = {"tv": "projector", "mirror_ball": "wobbling"}
    raw["transitions"][0]["steps"].append({"signal": "pause"})
    raw["groups"]["privacy_blinds"].append("blinds.ch99")
    raw["labels"] = {"blinds.ch99": "Nowhere"}
    raw["default"] = "nowhere"
    by_path = {e["path"]: e["message"] for e in validate(raw, every())}
    assert "above maximum 100" in by_path["/modes/disco/state/privacy_blinds/set"]
    assert "'projector' is not one of" in by_path["/modes/ambient/state/tv"]
    assert "'wobbling' is not one of" in by_path["/modes/ambient/state/mirror_ball"]
    assert "<subsystem>.<action>" in by_path["/transitions/0/steps/7/signal"]
    assert "matches no target" in by_path["/groups/privacy_blinds/3"]
    assert "unknown target" in by_path["/labels/blinds.ch99"]
    assert "missing or abstract" in by_path["/default"]


def test_revision_tracks_content():
    raw = copy.deepcopy(EXAMPLE)
    assert revision(raw) == revision(copy.deepcopy(EXAMPLE))
    raw["modes"]["disco"]["label"] = "Disco!"
    assert revision(raw) != revision(EXAMPLE)


def test_room_config_schema_accepts_the_example_and_rejects_bad_entries():
    jsonschema = pytest.importorskip("jsonschema")
    cfg = RoomConfig(EXAMPLE, every())
    s = room_config_schema({t: cfg.adapters[t.split(".")[0]].value_schema(t.split(".", 1)[1]) for t in cfg.targets},
                           list(cfg.groups))
    json.dumps(s)
    assert s["$defs"]["blinds.ch02"]["x-ui"]["presets"] == {"raised": 0, "lowered": 100}
    jsonschema.validate(EXAMPLE, s)                                          # every entry form in the example
    for bad in ({"retrieve": "x", "set": 1}, {"store": "x", "colour": 1}, {"store": "x", "set": 1, "max_age_s": 5}):
        raw = copy.deepcopy(EXAMPLE)
        raw["modes"]["normal"]["state"] = {"tv": bad}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(raw, s)
    raw = copy.deepcopy(EXAMPLE)
    raw["modes"]["normal"]["state"] = {"tv": {"retrieve": "pre_live", "opts": {"over_s": 2}}}
    jsonschema.validate(raw, s)


# --- describe -------------------------------------------------------------------------------------------------------

def test_describe_gives_editors_schemas_labels_and_page_words(tmp_path):
    """c8: no slots or inheritance in what the editor reads; per target its category, subsystem label and row
    options; per row its page words; forms for a mode and the room; warnings with page text."""
    raw = example(lambda raw: raw.update(labels={"blinds.ch02": "Lounge left"}))
    r = build(tmp_path, raw)
    d = r.c.describe()
    ch02 = d["targets"]["blinds.ch02"]
    assert ch02["label"] == "Lounge left" and ch02["groups"] == ["all_blinds", "privacy_blinds"]
    assert ch02["schema"]["type"] == "integer" and ch02["schema"]["x-ui"]["unit"] == "%"
    assert ch02["category"] == "device" and ch02["subsystem_label"] == "Blinds"
    assert ch02["entry_schema"]["properties"]["restore"]["x-ui"]["label"] == "Put back afterwards"
    assert d["targets"]["tv.north-screen"]["schema"]["x-ui"]["labels"]["art_mode"] == "Frame Art Mode"
    assert "house" not in d["modes"] and "slots" not in d                      # an old-style base isn't a mode
    disco = d["modes"]["disco"]
    assert not {"extends", "abstract", "family"} & disco.keys()
    assert disco["entries"]["blinds.ch02"] == {"verb": "store+set", "value": 100, "words": "100%", "restore": False,
                                               "store_if": [], "policy": "enforce", "max_age_s": None, "opts": {},
                                               "from": "live", "selector": "privacy_blinds", "slot": "pre_live"}
    assert disco["entries"]["mirror_ball.main"]["from"] == "disco"
    assert d["modes"]["normal"]["entries"]["blinds.ch02"]["verb"] == "retrieve"
    assert "live" not in d["modes"]                                                 # abstract too
    assert d["mode_schema"]["properties"]["icon"]["enum"] and "disco" in d["room_schema"]["properties"]["default"]["enum"]
    assert {w["code"] for w in d["warnings"]} >= {"legacy_inheritance"} and all(w["text"] for w in d["warnings"])
    assert set(d["subsystems"]["sonos"]["actions"]) == {"play", "pause"}
    assert d["revision"] == revision(raw)
    json.dumps(d)


# --- plan (dry run) -------------------------------------------------------------------------------------------------

def test_plan_changes_nothing_and_matches_the_real_run(tmp_path):
    """v1.1: rows are one per target command, signal and delay (§7.2); a target already as wanted (ch14) gets none,
    and stores show in the row's action and `stored` instead of rows of their own."""
    async def body():
        r = build(tmp_path)
        await boot(r)
        before_file = r.path.read_bytes()
        n_cmds = len(r.blinds.commands)
        p = await r.c.plan("disco")
        assert r.path.read_bytes() == before_file and len(r.blinds.commands) == n_cmds
        assert set(p["steps"][0]) >= {"id", "step", "subsystem", "action", "status", "detail"}   # LIVE_V1 §3 + id
        rows = {s["subsystem"]: s for s in p["steps"]}
        assert rows["blinds.ch02"]["action"] == "store pre_live, set 100" and rows["blinds.ch02"]["stored"] == 0
        assert rows["blinds.ch02"]["status"] == "pending" and rows["blinds.ch02"]["guarded"]
        assert "blinds.ch14" not in rows                                     # already 100
        assert rows["blinds.ch02"]["step"] == rows["tv.north-screen"]["step"] < rows["plug.dmx_power"]["step"]
        assert rows["plug.dmx_power"]["step"] < rows["delay"]["step"] < rows["screens.north-screen"]["step"] \
            < rows["mirror_ball.main"]["step"] < rows["log"]["step"]        # live's on_enter comes last
        assert [s["id"] for s in p["steps"]] == [f"i{i}" for i in range(len(p["steps"]))]

        tr = await go(r, "disco")
        assert [(s["id"], s["subsystem"], s["action"]) for s in tr["steps"]] == \
            [(s["id"], s["subsystem"], s["action"]) for s in p["steps"]]
        assert {s["status"] for s in tr["steps"]} == {"ok"}
    asyncio.run(body())


def test_plan_says_why_a_blind_will_not_be_restored(tmp_path):
    """v1.1: a retrieve that will leave its target alone sends nothing, so it has no row; plan() says why in notes."""
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.c.stored_forget("pre_live", "blinds.ch03", cause="test")
        p = await r.c.plan("normal")
        assert not [s for s in p["steps"] if s["subsystem"] == "blinds.ch03"]
        assert p["notes"]["blinds.ch03"] == "nothing stored: leave"
        ch02 = [s for s in p["steps"] if s["subsystem"] == "blinds.ch02"]
        assert ch02[0]["action"] == "retrieve pre_live → 0" and ch02[0]["status"] == "pending"
    asyncio.run(body())


# --- hand changes, stored edits, reload -----------------------------------------------------------------------------

def test_set_target_validates_and_follows_the_mode_policy(tmp_path):
    """v1.1 (marked in §8): hold makes the enforced target overridden (manual drift) instead of aa's held/respect."""
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        with pytest.raises(ValueError, match="above maximum"):
            await r.c.set_target("blinds.ch03", 150)
        with pytest.raises(KeyError):
            await r.c.set_target("blinds.ch99", 10)
        out = await r.c.set_target("sonos.lounge.volume", 30)                # respect: accepted, shown as drift
        assert out.status == "ok" and r.sonos.value["lounge.volume"] == 30
        drift = r.c.s["drift"]["sonos.lounge.volume"]
        assert {k: drift[k] for k in ("observed", "desired", "manual", "overridden")} == {
            "observed": 30, "desired": 60, "manual": True, "overridden": False}
        with pytest.raises(Refused) as e:                                    # enforced: refused without hold
            await r.c.set_target("blinds.ch03", 0)
        assert e.value.code == "enforced" and r.blinds.value["ch03"] == 100
        await r.c.set_target("blinds.ch03", 0, cause="owner peeked", hold=True)   # held until the next switch
        st = r.c.status()["subsystems"]["blinds"]["members"]["ch03"]
        assert r.blinds.value["ch03"] == 0 and st["drift"]["overridden"] and st["drift"]["manual"]
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 0                                    # not put back while held
        assert r.c.s["drift"]["blinds.ch03"]["observed"] == 0
        assert [e["op"] for e in r.c.s["log"]][-2:] == ["hand_set", "hand_set"]
        assert r.c.s["log"][-1]["cause"] == "owner peeked"
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 80                                   # the pre-disco value comes back
    asyncio.run(body())


def test_edits_are_refused_while_a_transition_runs(tmp_path):
    """v1.1: a reload during a transition is no longer refused; it is applied when the transition ends (§2.1)."""
    async def body():
        r = build(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.2
        await r.c.request_mode("disco")
        await asyncio.sleep(0.05)
        out = await r.c.set_target("sonos.lounge.volume", 30)   # owner 17 Oct: a hand change mid-switch applies
        assert out.status == "ok"
        with pytest.raises(Busy):
            r.c.stored_edit("pre_live", "blinds.ch03", 50, cause="x")
        new = RoomConfig(example(lambda raw: raw["modes"]["disco"].update(label="Disco!")), r.every)
        assert r.c.reload(new, cause="x")["deferred"] and r.c.cfg.revision != new.revision
        await r.c.wait_idle()
        assert r.c.cfg.revision == new.revision and r.c.s["mode"]["current"] == "disco"
    asyncio.run(body())


def test_a_corrected_stored_value_is_what_gets_restored(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        with pytest.raises(ValueError):
            r.c.stored_edit("pre_live", "blinds.ch03", 101, cause="typo")
        with pytest.raises(Refused, match="unknown_stored"):
            r.c.stored_edit("pre_live", "blinds.ch09", 10, cause="not stored")
        r.c.stored_edit("pre_live", "blinds.ch03", 50, cause="owner: was half open")
        assert r.c.s["log"][-1] | {"at": 0} == {"at": 0, "op": "stored_edit", "cause": "owner: was half open",
                                                 "slot": "pre_live", "target": "blinds.ch03", "old": 80, "new": 50}
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 50
        r.c.stored_forget("pre_live", None, cause="clear all")
        assert "pre_live" not in r.c.s["stored"]
    asyncio.run(body())


def test_reload_keeps_stored_values_and_sends_nothing(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        stored, n = copy.deepcopy(r.c.s["stored"]), len(r.sonos.commands)
        new = RoomConfig(example(lambda raw: raw["modes"]["disco"]["state"]["sonos.lounge.volume"].update(set=70)), r.every)
        out = r.c.reload(new, cause="owner edit")
        assert r.c.s["stored"] == stored and len(r.sonos.commands) == n          # nothing moved
        assert r.c.s["desired"]["sonos.lounge.volume"]["value"] == 60            # desired kept until a switch
        assert out["pending_targets"] == ["sonos.lounge.volume"]
        cfg = r.c.status()["config"]
        assert (cfg["revision"], cfg["pending_targets"], cfg["error"]) == (new.revision, ["sonos.lounge.volume"], None)
        await r.c.reconcile_once()
        assert r.sonos.value["lounge.volume"] == 60
        await go(r, "disco")                                                     # re-apply: now it moves, no signals
        assert r.sonos.value["lounge.volume"] == 70 and r.c.status()["config"]["pending_targets"] == []
        assert r.signals.sent.count((None, "info", {"msg": "show starting"})) == 1
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 25
    asyncio.run(body())


def test_reload_relaxes_policies_at_once(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        raw = example(lambda raw: raw["modes"]["live"]["state"]["privacy_blinds"].update(policy="ignore"))
        r.c.reload(RoomConfig(raw, r.every), cause="stop enforcing")
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 0 and "blinds.ch03" not in r.c.s["drift"]
    asyncio.run(body())


def test_assume_tells_a_toggle_relay_its_state_without_sending(tmp_path):
    async def body():
        r = build(tmp_path, ball_kind="edge_toggle")
        await boot(r)
        with pytest.raises(Refused) as e:
            await r.c.set_target("mirror_ball.main", "spinning")
        assert e.value.code == "toggle_state_unknown" and r.ball.sent == []
        r.c.assume("mirror_ball.main", "stopped", cause="owner: it is off")
        assert r.ball.sent == []
        with pytest.raises(Refused, match="readable"):                          # v1.1: level targets are read
            r.c.assume("blinds.ch02", 0, cause="x")
        await r.c.set_target("mirror_ball.main", "spinning")
        assert r.ball.physical["main"] == "spinning"
    asyncio.run(body())


def test_a_mode_switch_cancels_a_manual_change_still_running(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.3
        manual = asyncio.create_task(r.c.set_target("blinds.ch09", 0))
        await asyncio.sleep(0.05)
        await r.c.request_mode("disco")
        out = await manual
        assert out.status == "failed" and "superseded" in out.detail
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "disco"
    asyncio.run(body())


def test_status_carries_ages_not_clock_times(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        st = r.c.status()
        e = st["stored"]["pre_live"]["blinds.ch02"]
        assert "at" not in e and e["age_s"] >= 0 and e["value"] == 0
        text = json.dumps(st)
        assert '"at"' not in text and "_at" not in text.replace("_age_s", "")
    asyncio.run(body())


def test_lint_and_rules_explain_the_transitions():
    """v1.1: a bare abstract name in a rule is now an error (§2.5), so the shadowed rule here names @live."""
    raw = copy.deepcopy(EXAMPLE)
    raw["transitions"].append({"from": "*", "to": "@live", "steps": []})     # behind the catch-all: never used
    raw["modes"]["disco"]["state"]["plug.dmx_power"] = {"store": "pre_disco", "set": "on"}
    cfg = RoomConfig(raw, every())
    codes = {w["code"] for w in current(cfg.lint())}
    assert {"rule_unused", "slot_never_retrieved"} <= codes
    rules = {(r["from"], r["to"]): r["rule"] for r in cfg.rules()}
    assert rules[(None, "disco")] == 0 and rules[("normal", "disco")] == 0
    assert rules[("disco", "normal")] == 1 and rules[("ambient", "disco")] == 0
    assert current(RoomConfig(EXAMPLE, every()).lint()) == []


def test_integer_like_keys_are_rejected():
    raw = copy.deepcopy(EXAMPLE)
    raw["modes"]["2"] = {"extends": "live"}
    assert {"path": "/modes/2", "message": "a mode name can't be a number", "code": "invalid"} in plain(validate(raw, every()))


def test_plan_can_preview_a_draft_config(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        draft = RoomConfig(example(lambda raw: raw["modes"]["disco"]["state"]["sonos.lounge.volume"].update(set=80)), r.every)
        p = await r.c.plan("disco", config=draft)
        assert p["draft"] and p["revision"] == draft.revision
        assert any(s["action"] == "store pre_live, set 80" and s["subsystem"] == "sonos.lounge.volume" for s in p["steps"])
        assert r.c.cfg.revision == revision(example())                          # the live config is untouched
    asyncio.run(body())


def test_unknown_keys_are_reported_at_every_level():
    raw = copy.deepcopy(EXAMPLE)
    raw["colour"] = "red"
    raw["modes"]["disco"]["lable"] = "Disco"
    raw["transitions"][0]["stpes"] = []
    paths = {e["path"] for e in validate(raw, every())}
    assert {"/colour", "/modes/disco/lable", "/transitions/0/stpes"} <= paths


def test_restore_one_stored_value_now_and_see_its_expiry(tmp_path):
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        e = r.c.status()["stored"]["pre_live"]["blinds.ch03"]
        assert 43190 < e["expires_in_s"] <= 43200                              # normal retrieves with max_age_s 12 h
        assert r.c.status()["stored"]["pre_live"]["tv.north-screen"]["expires_in_s"] is None
        out = await r.c.stored_restore("pre_live", "blinds.ch03", cause="peek")
        assert out.status == "ok" and r.blinds.value["ch03"] == 80
        assert "blinds.ch03" not in r.c.s["stored"]["pre_live"]
    asyncio.run(body())


def test_forget_without_tombstone_the_one_case_that_differs(tmp_path):
    """Forget = drop the value, no tombstone. The only difference a tombstone would make: forget, then hold the blind
    up by hand, then hop live -> live. The hop stores the held position, and Normal later puts that back."""
    async def body():
        r = build(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.c.stored_forget("pre_live", "blinds.ch03", cause="owner: don't put this one back")
        await go(r, "normal")                                                   # plain case: forgotten -> left down
        assert r.blinds.value["ch03"] == 100

        await go(r, "disco")                                                    # stores 100 for ch03 (it's down now)
        r.c.stored_forget("pre_live", "blinds.ch03", cause="again")
        await go(r, "ambient")                                                  # hop: re-stores 100 (current)
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 100                                    # same as leave

        await go(r, "disco")
        r.c.stored_forget("pre_live", "blinds.ch03", cause="again")
        await r.c.set_target("blinds.ch03", 0, hold=True)                       # held up by hand during disco
        await go(r, "ambient")                                                  # hop stores 0, then lowers it
        assert r.blinds.value["ch03"] == 100 and r.c.s["stored"]["pre_live"]["blinds.ch03"]["value"] == 0
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 0                                      # a tombstone would have left it down
    asyncio.run(body())
