"""Flat modes (owner, 16 Oct: "there's no such thing as a 'live' mode ... all the config should be just that — config"):
no abstract/extends, and transition rules chosen by what a transition changes (`when`), not by mode families."""

from __future__ import annotations

import asyncio
import copy

import pytest

from room_service.roomstate.model import ConfigError, RoomConfig, validate

from .harness import RADIO, boot, fake_adapters, go, make_room, order

HOME = {"screens": "art", "sensor_mask": "follow", "page": True, "mirror_ball": "stopped", "plug.dmx_power": "off",
        "privacy": {"retrieve": "pre_live"}, "tv": {"retrieve": "pre_live"}}
LIVE = {"live": {"scene": "fractal-zoom", "transitions": {"in": "x", "out": "x"}}}
SHOW = {**HOME, "sensor_mask": "suspended", "page": False, "privacy": {"store": "pre_live", "set": 100, "policy": "enforce"},
        "tv": {"store": "pre_live", "set": "hdmi"}, "screens.north-screen": LIVE, "screens.south-screen": LIVE}
FLAT = {
    "default": "normal",
    "groups": {"privacy": ["blinds.ch02", "blinds.ch03", "blinds.ch14"], "frames": ["screens.north-screen", "screens.south-screen"]},
    "modes": {
        "normal": {"label": "Normal", "state": HOME},
        "lights": {"label": "Lights", "state": {**HOME, "plug.dmx_power": "on"}},
        "disco": {"label": "Disco", "state": {**SHOW, "plug.dmx_power": "on", "mirror_ball": "spinning"}},
        "ambient": {"label": "Ambient", "state": SHOW},
    },
    "transitions": [
        {"_note": "anything that lights the room: blinds first", "from": "*", "to": "*",
         "when": [{"frames": {"to": "live"}}, {"plug.dmx_power": "on"}, {"mirror_ball": "spinning"}],
         "steps": [["sensor_mask", "page"], {"targets": ["privacy", "tv"], "on_fail": {"abort_on": "privacy"}},
                   "plug.dmx_power", {"delay": 0.01}, "frames", "mirror_ball", "rest"]},
        {"_note": "lights off before the blinds rise", "from": "*", "to": "*",
         "when": [{"frames": {"from": "live", "to": "art"}}, {"plug.dmx_power": {"from": "on", "to": "off"}},
                  {"mirror_ball": {"from": "spinning", "to": "stopped"}}],
         "steps": ["mirror_ball", {"targets": "plug.dmx_power", "on_fail": {"abort_on": "plug.dmx_power", "rollback": False}},
                   "frames", "rest"]},
        {"from": "*", "to": "*", "steps": ["rest"]},
    ],
}


def first(log: list[str], sub: str) -> int:
    return log.index(sub)


def test_entry_and_exit_order_come_from_what_changes(tmp_path):
    async def body():
        r = make_room(tmp_path, copy.deepcopy(FLAT))
        await boot(r)
        r.log.clear()
        tr = await go(r, "disco")
        assert tr["rule"] == 0 and tr["status"] in ("ok", "partial")
        o = order(r)
        assert first(o, "blinds") < first(o, "plug") < first(o, "screens") < first(o, "mirror_ball")
        r.log.clear()
        tr = await go(r, "normal")
        assert tr["rule"] == 1
        o = order(r)
        assert first(o, "mirror_ball") < first(o, "plug") < first(o, "screens") < first(o, "blinds")
    asyncio.run(body())


def test_a_hop_between_two_show_modes_keeps_the_entry_order(tmp_path):
    async def body():
        r = make_room(tmp_path, copy.deepcopy(FLAT))
        await boot(r)
        await go(r, "ambient")
        tr = await go(r, "disco")                        # screens stay live; DMX and the ball come on: blinds first
        assert tr["rule"] == 0
    asyncio.run(body())


def test_lights_without_screens_still_lower_the_blinds_first(tmp_path):
    async def body():
        raw = copy.deepcopy(FLAT)
        raw["modes"]["lights"]["state"]["privacy"] = {"store": "pre_live", "set": 100}
        r = make_room(tmp_path, raw)
        await boot(r)
        r.log.clear()
        tr = await go(r, "lights")                       # no screens change: DMX on is enough to pick the entry rule
        assert tr["rule"] == 0 and first(order(r), "blinds") < first(order(r), "plug")
    asyncio.run(body())


def test_when_is_validated_and_never_flagged_unused():
    every = list(vars(fake_adapters()).values())
    bad = copy.deepcopy(FLAT)
    bad["transitions"][0]["when"] = [{"frames": {"to": "live", "sideways": 1}}, {"blinds.ch99": 1}, "nope"]
    paths = {e["path"] for e in validate(bad, every)}
    assert {"/transitions/0/when/0/frames", "/transitions/0/when/1/blinds.ch99", "/transitions/0/when/2"} <= paths
    cfg = RoomConfig(copy.deepcopy(FLAT), every)
    assert not [w for w in cfg.lint() if w["code"] == "rule_unused"]
    assert cfg.transitions[2].catch_all and not cfg.transitions[0].catch_all
    with pytest.raises(ConfigError):
        RoomConfig({**copy.deepcopy(FLAT), "transitions": FLAT["transitions"][:2]}, every)   # no plain catch-all


def test_autopilot_request_fields_skip_expect_reason(tmp_path):
    """show_brain's Auto (docs/SHOW_ENGINE.md §8.9): `skip` leaves targets alone (not stored, not set), `expect` refuses
    a stale request, `reason` rides on the transition; status gives sent_age_s per member."""
    from room_service.roomstate.coordinator import Refused

    async def body():
        raw = copy.deepcopy(FLAT)
        raw["autopilot"] = {"on": True}                                    # opaque until its schema settles
        raw["modes"]["disco"]["autopilot"] = {"enter_when": "music_loud"}
        r = make_room(tmp_path, raw)
        await boot(r)
        before = dict(r.blinds.value)
        await r.c.request_mode("disco", source="autopilot", reason="music got loud", skip=["privacy", "mirror_ball"])
        await r.c.wait_idle()
        tr = r.c.s["last_transition"]
        assert tr["reason"] == "music got loud" and set(tr["skip"]) >= {"blinds.ch02", "mirror_ball.main"}
        assert r.blinds.value == before                                    # skipped: never moved
        assert all(d != {"main": "spinning"} for d in r.ball.sent)         # skipped: never started
        assert "blinds.ch02" not in r.c.s["stored"].get("pre_live", {})    # skipped: nothing stored for it
        assert r.c.status()["subsystems"]["screens"]["members"]["north-screen"]["sent_age_s"] is not None
        with pytest.raises(Refused) as e:
            await r.c.request_mode("normal", source="autopilot", expect={"current": "normal"})
        assert e.value.code == "stale"
        await r.c.request_mode("normal", expect={"current": "disco", "last_transition_id": tr["id"]})
        await r.c.wait_idle()
        await go(r, "disco")                                             # a person: everything, blinds stored + lowered
        assert r.c.s["stored"]["pre_live"]["blinds.ch02"]["value"] == before["ch02"]
        with pytest.raises(ValueError):
            await r.c.request_mode("normal", skip=["blinds.ch99"])
    asyncio.run(body())


# "put back afterwards" (owner, 16 Oct): generic per row, no named slots, the storing mode restores on exit ----------

def restore_room(tmp_path, now=None):
    raw = copy.deepcopy(FLAT)
    PB = {"sonos.lounge.volume": {"set": 0, "restore": True}, "sonos.lounge.playback": {"set": "paused", "restore": True}}
    raw["modes"]["piano"] = {"label": "Piano", "state": {**HOME, **PB}}
    raw["modes"]["quiet"] = {"label": "Quiet", "state": {**HOME, "sonos.lounge.volume": {"set": 10, "restore": True}}}
    raw["modes"]["loud"] = {"label": "Loud", "state": {**HOME, "sonos.lounge.volume": 70}}
    raw["modes"]["short"] = {"label": "Short", "state": {
        **HOME, "sonos.lounge.volume": {"set": 5, "restore": True, "max_age_s": 60}}}
    return make_room(tmp_path, raw, now=now) if now else make_room(tmp_path, raw)


def test_put_back_afterwards_restores_on_leaving_whatever_comes_next(tmp_path):
    async def body():
        r = restore_room(tmp_path)
        await boot(r)
        before = dict(r.sonos.value)                                       # volume 25, the radio playing
        tr = await go(r, "piano")
        assert r.sonos.value == {"lounge.volume": 0, "lounge.playback": "paused"}
        assert any(x["action"].endswith("put back afterwards") for x in tr["steps"])
        tr = await go(r, "lights")                                         # lights doesn't touch Sonos: put back now
        assert r.sonos.value == before and r.c.s["stored"].get("_restore") is None
        assert any(x["action"].startswith("put back") for x in tr["steps"])
        await go(r, "normal")
        assert r.sonos.value == before                                     # nothing left to put back
    asyncio.run(body())


def test_a_next_mode_that_sets_it_owns_it_and_one_that_restores_keeps_the_original(tmp_path):
    async def body():
        r = restore_room(tmp_path)
        await boot(r)
        await go(r, "piano")
        await go(r, "quiet")                                               # also restores volume: the original stays
        assert r.sonos.value["lounge.volume"] == 10 and r.sonos.value["lounge.playback"] == RADIO   # playback put back
        assert r.c.s["stored"]["_restore"]["sonos.lounge.volume"]["value"] == 25
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 25                        # the pre-piano volume, two modes later
        await go(r, "piano")
        await go(r, "loud")                                                # sets volume itself: it owns it now
        assert r.sonos.value["lounge.volume"] == 70
        assert "sonos.lounge.volume" not in r.c.s["stored"].get("_restore", {})   # loud owns it: nothing to put back
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 70                        # not put back
    asyncio.run(body())


def test_put_back_honours_its_max_age(tmp_path):
    from room_service.roomstate.fakes import Clock

    async def body():
        clock = Clock()
        r = restore_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "short")
        clock.advance(120)
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 5 and not r.c.s["stored"].get("_restore")
    asyncio.run(body())


def test_restore_is_validated():
    every = list(vars(fake_adapters()).values())
    for entry, path in [({"set": 1, "restore": True, "store": "x"}, "restore"), ({"set": 1, "restore": "yes"}, "restore"),
                        ({"restore": True}, "restore"), ({"store": "_mine", "set": 1}, "store")]:
        bad = copy.deepcopy(FLAT)
        bad["modes"]["normal"]["state"]["sonos.lounge.volume"] = entry
        paths = {e["path"] for e in validate(bad, every)}
        assert f"/modes/normal/state/sonos.lounge.volume/{path}" in paths, (entry, paths)
    ok = copy.deepcopy(FLAT)
    ok["modes"]["normal"]["state"]["sonos.lounge.volume"] = {"set": 1, "restore": True, "max_age_s": 600,
                                                              "store_if": ["*"]}
    assert validate(ok, every) == [] and not [w for w in RoomConfig(ok, every).lint() if w["code"] == "slot_never_retrieved"]


def test_an_old_style_retrieve_with_nothing_stored_does_not_drop_a_put_back(tmp_path):
    """c8 review #1: while store/retrieve rows and restore rows coexist, a destination retrieve that finds nothing
    must not count as setting the target, or the restore's value would be lost."""
    async def body():
        raw = copy.deepcopy(FLAT)
        raw["modes"]["piano"] = {"label": "Piano", "state": {**HOME, "sonos.lounge.volume": {"set": 0, "restore": True}}}
        raw["modes"]["normal"]["state"] = {**HOME, "sonos.lounge.volume": {"retrieve": "pre_live"}}
        raw["modes"]["disco"]["state"]["sonos.lounge.volume"] = {"store": "pre_live", "set": 60}
        r = make_room(tmp_path, raw)
        await boot(r)
        await go(r, "piano")
        assert r.sonos.value["lounge.volume"] == 0
        await go(r, "normal")                            # normal's retrieve finds nothing in pre_live: put back wins
        assert r.sonos.value["lounge.volume"] == 25 and not r.c.s["stored"].get("_restore")
    asyncio.run(body())


def test_a_conditional_skip_leaves_alone_only_what_matches_now(tmp_path):
    """Auto's skip item {selector: {"from": pattern}}: e.g. leave the music alone only while it's playing."""
    async def body():
        r = restore_room(tmp_path)
        await boot(r)
        playing = {"sonos.lounge.playback": {"from": ["uri", "favourite"]}}
        await r.c.request_mode("piano", source="autopilot", skip=[playing])
        await r.c.wait_idle()
        assert r.sonos.value == {"lounge.volume": 0, "lounge.playback": RADIO}    # playing: left alone; volume set
        await go(r, "normal")
        r.sonos.value["lounge.playback"] = "paused"                         # someone paused it: no longer skipped
        await r.c.request_mode("piano", source="autopilot", skip=[playing])
        await r.c.wait_idle()
        assert "sonos.lounge.playback" not in r.c.s["last_transition"].get("skip", [])
    asyncio.run(body())


def test_the_room_wide_put_back_age_applies_where_a_row_names_none(tmp_path):
    from room_service.roomstate.fakes import Clock

    async def body():
        clock = Clock()
        raw = copy.deepcopy(FLAT)
        raw["put_back_max_age_s"] = 60
        raw["modes"]["quiet"] = {"label": "Quiet", "state": {**HOME, "sonos.lounge.volume": {"set": 10, "restore": True}}}
        r = make_room(tmp_path, raw, now=clock)
        await boot(r)
        await go(r, "quiet")
        assert r.c.s["stored"]["_restore"]["sonos.lounge.volume"]["max_age_s"] == 60
        clock.advance(120)
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 10                    # changed too long ago: not put back
    asyncio.run(body())


def test_mode_words_icons_and_auto_blocks_are_checked():
    every = list(vars(fake_adapters()).values())
    ok = copy.deepcopy(FLAT)
    ok["modes"]["disco"].update(description="Lights and the ball", icon="disco", autopilot={
        "enter_when": {"music": "loud"}, "from": ["normal"], "leave_to": "previous",
        "skip": ["privacy", {"sonos.lounge.playback": {"from": "favourite"}}]})
    ok["autopilot"] = {"on": True, "min_dwell_s": 600}
    assert validate(ok, every) == []
    cfg = RoomConfig(ok, every)
    assert cfg.modes["disco"].description == "Lights and the ball" and cfg.modes["disco"].icon == "disco"
    bad = copy.deepcopy(ok)
    bad["modes"]["disco"]["icon"] = "rocket"
    bad["modes"]["disco"]["autopilot"].update(leave_to="nowhere", colour="red", skip=["blinds.ch99", {"privacy": "on"}])
    bad["autopilot"]["speed"] = 3
    bad["put_back_max_age_s"] = -1
    found = {e["path"]: e for e in validate(bad, every)}
    for path in ["/modes/disco/icon", "/modes/disco/autopilot/leave_to", "/modes/disco/autopilot/colour",
                 "/modes/disco/autopilot/skip/0", "/modes/disco/autopilot/skip/1/privacy", "/autopilot/speed",
                 "/put_back_max_age_s"]:
        assert path in found, (path, sorted(found))
    for e in found.values():                                           # page words: no slots, pointers or verbs
        assert e["text"] and not any(w in e["text"] for w in ("slot", "retrieve", "store_if", "pointer"))
    assert found["/modes/disco/icon"]["code"] == "bad_icon" and found["/modes/disco/icon"]["mode"] == "disco"


def test_mode_voice_name_is_kept_and_checked():
    """Owner 18 Oct: a mode's short name for Alexa (routes/alexa.py feeds only these to its AI step)."""
    every = list(vars(fake_adapters()).values())
    ok = copy.deepcopy(FLAT)
    ok["modes"]["disco"]["voice"] = "  disco   lights "
    assert validate(ok, every) == []
    cfg = RoomConfig(ok, every)
    assert cfg.modes["disco"].voice == "disco lights" and cfg.modes["normal"].voice == ""
    bad = copy.deepcopy(ok)
    bad["modes"]["disco"]["voice"] = ["disco"]
    assert "/modes/disco/voice" in {e["path"] for e in validate(bad, every)}


def test_put_back_stores_what_a_fresh_read_says_and_idle_targets_stay_polled(tmp_path):
    """68/2f, 16 Oct: lamps turned off at the bridge while nothing managed them still read as on, so a put-back
    stored "on" and leaving the show turned them on. A store reads first; idle members are polled too."""
    async def body():
        r = restore_room(tmp_path)
        await boot(r)
        r.sonos.value["lounge.volume"] = 40                              # changed behind the room's back
        await r.c.reconcile_tick("sonos")                                # nothing manages it in normal: still read
        assert r.c.s["believed"]["sonos.lounge.volume"]["value"] == 40
        r.sonos.value["lounge.volume"] = 33                              # and again, just before the switch
        await go(r, "quiet")
        assert r.c.s["stored"]["_restore"]["sonos.lounge.volume"]["value"] == 33
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 33
    asyncio.run(body())
