"""aa's 29 scenario tests, ported to ROOM_STATE_MODEL v1.1 (names: mirror_ball, tv.<screen id>, sensor_mask.main;
the §2.2 example; §6's starting point). Tests whose expectation the spec changes say so in a "v1.1:" comment.
Pure asyncio with fake adapters: no I/O but the state file, no RF, no hub.

Run from experiments/rf-spike:  .venv/bin/python -m pytest -q roomstate/tests
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time

import pytest

from harness import DOWN, ORIGINAL, RADIO, boot, example, go, make_room, order, privacy, restart, sent, \
    signals_sent, stored
from roomstate.fakes import Clock
from roomstate.model import ConfigError


# --- the owner's example: FROM normal store, TO live modes set, TO normal retrieve ---------------------------------

def test_owner_example_store_set_retrieve(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        assert r.c.s["mode"]["current"] == "normal"

        tr = await go(r, "disco")
        assert tr["status"] == "ok"
        assert privacy(r) == DOWN
        assert r.blinds.value["ch09"] == 50                                  # not managed: untouched
        assert stored(r) == {"blinds.ch02": 0, "blinds.ch03": 80, "blinds.ch14": 100,
                             "tv.north-screen": "hdmi", "tv.south-screen": "art_mode",
                             "sonos.lounge.volume": 25, "sonos.lounge.playback": RADIO}
        assert r.tv.value == {"north-screen": "hdmi", "south-screen": "hdmi"}
        assert r.plug.value["dmx_power"] == "on" and r.ball.physical["main"] == "spinning"
        assert r.sonos.value["lounge.volume"] == 60 and r.sonos.commands[-1]["opts"] == {"over_s": 5}
        assert r.sonos.value["lounge.playback"] == {"favourite": "Disco"}   # v1.1: playback is state, not a signal

        tr = await go(r, "normal")
        assert tr["status"] == "ok"
        assert privacy(r) == ORIGINAL                                        # each blind back to its own value
        assert r.tv.value == {"north-screen": "hdmi", "south-screen": "art_mode"}
        assert r.sonos.value == {"lounge.volume": 25, "lounge.playback": RADIO}
        assert r.plug.value["dmx_power"] == "off" and r.ball.physical["main"] == "stopped"
        assert stored(r) == {}                                               # retrieve clears what it used
        assert not any("ch14" in d for d in sent(r, "blinds"))              # already at 100: never sent
    asyncio.run(body())


def test_live_to_live_hop_keeps_the_original_store(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        before, n_blinds = stored(r), len(sent(r, "blinds"))

        await go(r, "ambient")
        assert {t: v for t, v in stored(r).items() if not t.startswith("sonos.")} == \
            {t: v for t, v in before.items() if not t.startswith("sonos.")}    # no re-store on live -> live
        assert len(sent(r, "blinds")) == n_blinds                            # nothing re-sent to the blinds
        assert r.ball.physical["main"] == "stopped"                          # leaving disco stops the ball
        assert r.plug.value["dmx_power"] == "off"
        assert r.sonos.value["lounge.volume"] == 25                          # v1.1: ambient inherits house's retrieve

        await go(r, "normal")
        assert privacy(r) == ORIGINAL
        assert r.sonos.value["lounge.volume"] == 25                          # the pre-disco value
    asyncio.run(body())


# --- interruption ---------------------------------------------------------------------------------------------------

def test_interrupted_entry_restores_the_original_values(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await asyncio.sleep(0.05)                                            # blinds mid-travel
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
        assert all(d == {"main": "stopped"} for d in r.ball.sent)            # the ball never got "spinning"
        assert r.screens.value["north-screen"] == "art"
        assert any(e["data"]["phase"] == "cancelled" for e in r.events)
        assert stored(r) == {}
    asyncio.run(body())


def test_rapid_toggling_never_stores_a_transient_position(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.05
        for mode in ["disco", "normal", "disco", "normal", "disco", "normal", "disco"]:
            await r.c.request_mode(mode)
            await asyncio.sleep(0.03)
            s = stored(r)
            for t, v in {"blinds.ch02": 0, "blinds.ch03": 80, "blinds.ch14": 100}.items():
                assert s.get(t, v) == v, f"slot holds a transient value after {mode}: {s}"
        await r.c.wait_idle()
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_last_request_wins(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await r.c.request_mode("ambient")
        await r.c.wait_idle()
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("ambient", None)
        assert r.ball.physical["main"] == "stopped"
        assert stored(r)["blinds.ch02"] == 0
    asyncio.run(body())


# --- changes made by hand -------------------------------------------------------------------------------------------

def test_enforce_relowers_a_blind_raised_by_hand(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 100
        await go(r, "normal")
        assert privacy(r) == ORIGINAL                                        # back to the pre-mode value
    asyncio.run(body())


def test_respect_reports_drift_and_exit_still_retrieves(tmp_path):
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["modes"]["live"]["state"]["privacy_blinds"].update(policy="respect")))
        await boot(r)
        await go(r, "disco")
        n = len(sent(r, "blinds"))
        r.blinds.hand_set("ch03", 40)
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n                                   # no command
        assert r.c.s["drift"]["blinds.ch03"]["observed"] == 40
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 80                                  # the stored value, not the hand one
    asyncio.run(body())


def test_hand_change_in_normal_is_left_alone_and_picked_up_by_the_next_store(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        await go(r, "normal")
        n = len(sent(r, "blinds"))
        r.blinds.hand_set("ch02", 30)
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n and r.c.s["drift"] == {}
        await go(r, "disco")
        assert stored(r)["blinds.ch02"] == 30
    asyncio.run(body())


# --- restarts -------------------------------------------------------------------------------------------------------

def test_restart_mid_mode_keeps_the_stored_values(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.c = restart(r)
        await r.c.start(reconcile=False)                                     # nothing to resume
        assert r.c._task is None
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_restart_mid_transition_resumes_without_refiring_signals(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.screens.travel_s = 0.3
        await r.c.request_mode("normal")
        await asyncio.sleep(0.1)                                             # "show over" fired; screens mid-step
        assert signals_sent(r, "show over") == 1                             # v1.1: live's on_exit, not sonos.pause
        crash = tmp_path / "crash.json"
        shutil.copy(r.path, crash)                                           # the file as a crash would leave it
        r.c._task.cancel()
        await r.c.wait_idle()
        shutil.copy(crash, r.path)

        r.screens.travel_s = 0.01
        r.c = restart(r)
        assert r.c.s["transition"]["status"] == "running"
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("normal", None)
        assert signals_sent(r, "show over") == 1                             # not fired again
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


# --- failures and degraded routes -----------------------------------------------------------------------------------

def test_unreachable_hub_aborts_before_anything_flashes(tmp_path):
    """v1.1: the abort rolls back to normal (a transition, exit order), instead of aa's restore of the old desired."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.offline = True
        await go(r, "disco")
        tr = r.c.s["last_transition"]
        assert tr["source"] == "rollback" and tr["rollback_of"]["to"] == "disco"
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("normal", None)
        assert r.screens.value["north-screen"] == "art" and r.ball.physical["main"] == "stopped"
        assert r.plug.value["dmx_power"] == "off"
        r.blinds.offline = False
        n = len(sent(r, "blinds"))
        await r.c.reconcile_once()                                           # normal's rules apply, not disco's
        assert len(sent(r, "blinds")) == n and privacy(r) == ORIGINAL
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_a_blind_that_never_arrives_keeps_its_slot_for_a_retry(tmp_path):
    """v1.1: the belief keeps the reading (confirmed, 100) and the in-flight mark carries the command."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.fail = {"ch03"}
        tr = await go(r, "normal")
        assert tr["status"] == "partial"
        assert r.blinds.value["ch02"] == 0
        assert stored(r) == {"blinds.ch03": 80}
        b = r.c.s["believed"]["blinds.ch03"]
        assert (b["value"], b["confidence"], b["inflight"]["value"]) == (100, "confirmed", 80)
        assert r.c.s["stored"]["pre_live"]["blinds.ch03"]["closed"]
        r.blinds.fail = set()
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 80 and stored(r) == {}
    asyncio.run(body())


def test_unreadable_blinds_route_falls_back_to_else(tmp_path):
    async def body():
        raw = example(lambda raw: raw["modes"]["normal"].setdefault("state", {}).update(
            privacy_blinds={"retrieve": "pre_live", "else": 0}))
        r = make_room(tmp_path, raw, blinds_edge=True)                      # RM4 learned codes: no readback
        await boot(r)                                                        # boot: nothing stored, so else: 0
        assert r.blinds.sent == [{"ch02": 0, "ch03": 0, "ch14": 0}]
        assert r.c.s["believed"]["blinds.ch02"]["confidence"] == "unconfirmed"
        r.c.s["believed"]["blinds.ch03"]["confidence"] = "unknown"           # e.g. died mid-send
        await go(r, "disco")
        assert stored(r)["blinds.ch02"] == 0                                 # believed (sent) values are stored
        assert "blinds.ch03" not in stored(r)                                # unknown: nothing stored
        assert r.c.s["stored"]["pre_live"]["blinds.ch02"]["confidence"] == "unconfirmed"
        await go(r, "normal")
        assert r.blinds.sent[-1] == {"ch02": 0, "ch03": 0, "ch14": 0}        # ch03 via else
    asyncio.run(body())


def test_stale_store_is_not_retrieved(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        clock.advance(13 * 3600)                                             # max_age_s is 12 h for the blinds
        await go(r, "normal")
        assert privacy(r) == DOWN                                            # else: leave
        assert r.tv.value["south-screen"] == "art_mode"                      # tv has no max age
        assert stored(r) == {}
    asyncio.run(body())


def test_store_waits_for_a_moving_member_to_settle(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.05
        hand = asyncio.create_task(r.blinds.apply({"ch02": 60}))            # someone pressed the remote
        await asyncio.sleep(0)
        await go(r, "disco")
        await hand
        assert stored(r)["blinds.ch02"] == 60
    asyncio.run(body())


# --- edge-only subsystems -------------------------------------------------------------------------------------------

def test_edge_discrete_ball_sends_only_on_change_unless_unknown(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        assert r.ball.sent == [{"main": "stopped"}]                          # unknown at first boot: sent once
        await go(r, "normal")
        assert len(r.ball.sent) == 1
        await go(r, "disco")
        assert r.ball.sent[-1] == {"main": "spinning"}
        r.c.s["believed"]["mirror_ball.main"]["confidence"] = "unknown"      # e.g. died mid-send
        await go(r, "disco")
        assert r.ball.sent[-1] == {"main": "spinning"} and len(r.ball.sent) == 3
    asyncio.run(body())


def test_edge_toggle_ball_never_sends_blind(tmp_path):
    """v1.1: the unknown toggle gets an 'unconfirmed' row and makes the transition partial (it was 'skipped')."""
    async def body():
        r = make_room(tmp_path, ball_kind="edge_toggle")
        await boot(r)
        assert r.ball.sent == []                                             # unknown: a toggle can't be trusted
        tr = r.c.s["last_transition"]
        ball = next(x for x in tr["steps"] if x["subsystem"] == "mirror_ball.main")
        assert (ball["status"], ball["detail"], tr["status"]) == ("unconfirmed", "toggle state unknown; not sent", "partial")
        r.c.assume("mirror_ball.main", "stopped", cause="owner: it is off")  # the owner confirms it is off
        await go(r, "disco")
        assert r.ball.physical["main"] == "spinning"
        await go(r, "ambient")
        assert r.ball.physical["main"] == "stopped"
        await go(r, "normal")
        assert len(r.ball.sent) == 2
    asyncio.run(body())


# --- ordering, extension and config ---------------------------------------------------------------------------------

def test_transition_steps_order_the_room(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.log.clear()
        await go(r, "disco")
        o = order(r)
        assert max(o.index("sensor_mask"), o.index("page")) < min(o.index("blinds"), o.index("tv"))
        assert max(o.index("blinds"), o.index("tv")) < o.index("plug") < o.index("screens") < o.index("mirror_ball")
        r.log.clear()
        await go(r, "normal")
        o = order(r)
        assert o.index("mirror_ball") < o.index("plug") < o.index("screens") < o.index("blinds")   # lights off first
    asyncio.run(body())


def test_delay_is_skipped_when_its_step_changed_nothing(tmp_path):
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["modes"].update(karaoke={"extends": "live"}), delay=0.5))
        await boot(r)
        await go(r, "ambient")
        t0 = time.monotonic()
        await go(r, "karaoke")                                               # live -> live with nothing to change
        assert time.monotonic() - t0 < 0.3
    asyncio.run(body())


def test_a_new_live_mode_needs_no_new_rules(tmp_path):
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["modes"].update(karaoke={"label": "Karaoke", "extends": "live"})))
        await boot(r)
        await go(r, "karaoke")
        assert privacy(r) == DOWN and r.screens.value["north-screen"] != "art"
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_later_entries_override_and_null_unmanages(tmp_path):
    async def body():
        def patch(raw):
            raw["modes"]["dim"] = {"extends": "live", "state": {"blinds.ch03": {"store": "pre_live", "set": 40}}}
            raw["modes"]["peek"] = {"extends": "live", "state": {"privacy_blinds": None}}
        r = make_room(tmp_path, example(patch))
        assert r.cfg.modes["dim"].state["blinds.ch03"].set == 40
        assert r.cfg.modes["dim"].state["blinds.ch02"].set == 100
        assert not any(t.startswith("blinds.") for t in r.cfg.modes["peek"].state)
        await boot(r)
        await go(r, "dim")
        assert privacy(r) == {"ch02": 100, "ch03": 40, "ch14": 100}
    asyncio.run(body())


@pytest.mark.parametrize("patch, message", [
    (lambda m: m["modes"]["normal"].setdefault("state", {}).update({"tv": {"retrieve": "x", "set": 1}}),
     "retrieve can't be combined"),
    (lambda m: m["modes"]["normal"].setdefault("state", {}).update({"blinds.ch99": 1}), "matches no target"),
    (lambda m: m["modes"]["live"].update({"extends": "disco"}), "extends cycle"),
    (lambda m: m["transitions"].append({"from": "nowhere", "to": "*", "steps": []}), "unknown mode"),
    (lambda m: m.update({"default": "live"}), "missing or abstract"),
    (lambda m: m["transitions"][0]["steps"].append({"signal": "pause"}), "<subsystem>.<action>"),
    (lambda m: m["modes"]["normal"].setdefault("state", {}).update({"tv": {"retrieve": "pre_live", "policy": "sometimes"}}),
     "policy must be"),
])
def test_config_is_validated_at_load(tmp_path, patch, message):
    raw = example()
    patch(raw)
    with pytest.raises(ConfigError, match=message):
        make_room(tmp_path, raw)


def test_status_is_additive_to_live_v1(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        st = r.c.status()
        assert st["mode"]["current"] == "disco"
        ch02 = st["subsystems"]["blinds"]["members"]["ch02"]
        assert {k: ch02[k] for k in ("believed", "confidence", "desired", "policy", "drift", "settled")} == {
            "believed": 100, "confidence": "confirmed", "desired": 100, "policy": "enforce", "drift": None,
            "settled": True}                                                 # v1.1: aa's "held" is now drift.overridden
        assert ch02["age_s"] >= 0
        assert st["subsystems"]["mirror_ball"]["members"]["main"]["confidence"] == "unconfirmed"
        assert st["stored"]["pre_live"]["blinds.ch02"]["value"] == 0
        assert json.loads(json.dumps(st)) == st
    asyncio.run(body())

