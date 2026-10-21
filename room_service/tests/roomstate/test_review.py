"""ROOM_STATE_MODEL §8 review regressions (R1–R22, §11) and status tests (§7), on the example room with fakes.
Names follow §8's table. Pure asyncio: the only I/O is the state file under tmp_path.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest

from .harness import current, DOWN, ORIGINAL, SCREENS, boot, example, fake_adapters, go, make_room, privacy, restart, \
    row, sent, signals_sent, started, stored
from room_service.roomstate.coordinator import BACKOFF
from room_service.roomstate.fakes import Clock, FakeScreens, hard_kill
from room_service.roomstate.model import RoomConfig


async def until(cond, timeout: float = 2.0) -> None:
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "condition not reached"
        await asyncio.sleep(0.002)


def oks(r, target: str, after: float = 0.0) -> list[float]:
    return [e["t"] for e in r.events if e["type"] == "mode.transition" and e["data"]["phase"] == "step"
            and e["data"]["step"]["subsystem"] == target and e["data"]["step"]["status"] == "ok" and e["t"] >= after]


def sends(r, subsystem: str, after: float = 0.0) -> list[tuple[dict, float]]:
    return [(d, t) for s, d, t in r.log if s == subsystem and t >= after]


def stale_blinds(r, travel: float = 0.06, retarget: bool = True) -> None:
    """The Bloc hub: the cache shows a moving blind 'stopped' at its old position until the motor stops."""
    r.blinds.travel_s, r.blinds.stale_until_stop, r.blinds.retarget = travel, True, retarget


# --- R1 proof of arrival ---------------------------------------------------------------------------------------------

def test_r1_return_to_origin_both_directions(tmp_path):
    """stale_until_stop: normal -> disco -> normal and disco -> normal -> disco, a moment apart. No 'ok' before the
    earlier command could have finished; in the second, DMX power waits for ch02's proof at 100."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        stale_blinds(r)
        ct = r.blinds.change_time_s
        # normal -> disco, interrupted mid-lowering -> normal: the blinds must still go back up.
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        t_a = sends(r, "blinds")[0][1]
        await asyncio.sleep(0.02)
        tr = await go(r, "normal")
        assert tr["status"] == "ok" and privacy(r) == ORIGINAL and stored(r) == {}
        assert min(oks(r, "blinds.ch02", t_a)) >= t_a + ct - 0.01            # not proven before A's commands ended
        # disco -> normal, interrupted mid-raising -> disco: the lights wait for the blinds.
        await go(r, "disco")
        t0 = time.time()
        await r.c.request_mode("normal")
        await until(lambda: [d for d, _ in sends(r, "blinds", t0) if d.get("ch02") == 0])
        t_b = [t for d, t in sends(r, "blinds", t0) if d.get("ch02") == 0][0]
        await asyncio.sleep(0.02)
        tr = await go(r, "disco")
        proven = min(oks(r, "blinds.ch02", t_b))
        dmx_on = [t for d, t in sends(r, "plug", t_b) if d == {"dmx_power": "on"}][0]
        assert proven >= t_b + ct - 0.01 and dmx_on >= proven                 # DMX power only after the proof
        assert tr["status"] == "ok" and privacy(r) == DOWN
        assert stored(r)["blinds.ch02"] == 0                                  # the original is never lost
    asyncio.run(body())


def test_r1_return_to_origin_without_retarget_restored_by_retry(tmp_path):
    """retarget=False (a hub that ignores a command mid-travel, Q11): the return isn't proven, the row is unconfirmed,
    the slot stays closed with explained {0, 100}, and reconcile's retry restores the original."""
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        stale_blinds(r, retarget=False)
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        await asyncio.sleep(0.02)
        tr = await go(r, "normal")
        assert row(tr, "blinds.ch02")["status"] == "unconfirmed" and r.blinds.value["ch02"] == 100
        e = r.c.s["stored"]["pre_live"]["blinds.ch02"]
        assert e["closed"] and sorted(e["explained"]) == [0, 100]
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert privacy(r) == ORIGINAL and stored(r) == {}
    asyncio.run(body())


def test_r1_restart_between_mark_and_send(tmp_path):
    """A hard kill after normal's extended mark is saved but before its command goes out: the resumed plan sends, and
    the slot survives until the proof."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        stale_blinds(r)
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        await asyncio.sleep(0.02)
        r.blinds.hang_apply = True                                           # normal's blind command never goes out
        await r.c.request_mode("normal")
        await until(lambda: (row(r.c.s["transition"], "blinds.ch02") or {}).get("status") == "running")
        await hard_kill(r.c)
        mark = json.loads(r.path.read_text())["believed"]["blinds.ch02"]["inflight"]
        assert (mark["value"], mark["from"], mark["earlier"]) == (0, 0, [100])
        r.blinds.hang_apply = False
        t0 = time.time()
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await until(lambda: sends(r, "blinds", t0))                          # the resumed plan sends
        assert stored(r)["blinds.ch02"] == 0                                 # kept until the proof
        await r.c.wait_idle()
        assert privacy(r) == ORIGINAL and stored(r) == {} and r.c.s["last_transition"]["status"] == "ok"
    asyncio.run(body())


def test_r1_resend_same_value_not_delayed(tmp_path):
    """A resume re-sending 100 doesn't add to `earlier`, so the first fresh reading of 100 proves it."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        stale_blinds(r, travel=0.05)
        r.blinds.change_time_s = 1.0                                         # until is far away; proof must not wait
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        t_a = sends(r, "blinds")[0][1]
        await hard_kill(r.c)
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "disco" and privacy(r) == DOWN
        assert r.blinds.commands[-1]["opts"]["pending"]["ch02"]["earlier"] == []
        assert min(oks(r, "blinds.ch02", t_a)) < t_a + 0.5
    asyncio.run(body())


# --- R2, R3 retries -----------------------------------------------------------------------------------------------------

def test_r2_unconfirmed_retrieve_is_retried(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.blinds.fail = {"ch03"}                                             # never moves
        await go(r, "normal")
        n = len(sent(r, "blinds"))
        await asyncio.sleep(r.blinds.change_time_s)                          # past `until`
        await r.c.reconcile_once()
        d = r.c.s["desired"]["blinds.ch03"]
        assert not d["settled"] and stored(r) == {"blinds.ch03": 80}          # 100 is explained: not accepted
        assert len(sent(r, "blinds")) == n                                   # and not due yet
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert sent(r, "blinds")[n:] == [{"ch03": 80}] and d["retry"]["attempts"] == 1   # retried with backoff
        r.blinds.fail = set()
        clock.advance(BACKOFF[1])
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 80 and stored(r) == {}
    asyncio.run(body())


def test_r2_never_arrives_keeps_its_slot_with_reconcile_running(tmp_path):
    """aa's a_blind_that_never_arrives_keeps_its_slot_for_a_retry, with the reconcile loops running."""
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await r.c.start()                                                    # loops on (blinds poll every 10 ms)
        await r.c.wait_idle()
        await go(r, "disco")
        r.blinds.fail = {"ch03"}
        tr = await go(r, "normal")
        await asyncio.sleep(0.2)                                             # ~20 ticks
        assert tr["status"] == "partial" and stored(r) == {"blinds.ch03": 80}
        r.blinds.fail = set()
        clock.advance(BACKOFF[0])
        await until(lambda: stored(r) == {})
        assert r.blinds.value["ch03"] == 80
    asyncio.run(body())


def test_r2_slow_tv_unconfirmed_retried(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock, margin_s=0.05)
        await boot(r)
        r.tv.travel_s, r.tv.change_time_s = 0.4, 0.05                        # a wake past change_time_s + margin
        r.tv.reports_moving = False                                          # IP Control can't tell it is waking
        tr = await go(r, "disco")
        assert (row(tr, "tv.south-screen")["status"], row(tr, "tv.south-screen")["detail"]) == ("unconfirmed", "timeout")
        assert r.c.s["believed"]["tv.south-screen"]["value"] == "art_mode"  # R2: the belief keeps the reading
        assert r.c.s["desired"]["tv.south-screen"]["retry"]["explained"] == ["art_mode", "hdmi"]
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()                                           # still waking: art_mode is explained
        assert r.c.s["desired"]["tv.south-screen"]["retry"]["attempts"] == 1
        await until(lambda: r.tv.value["south-screen"] == "hdmi")
        await r.c.reconcile_once()
        assert r.c.s["desired"]["tv.south-screen"]["settled"]
    asyncio.run(body())


def test_r3_failed_edge_send_retried(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        r.ball.offline = True                                                # RM4 offline
        await go(r, "disco")
        b = r.c.s["believed"]["mirror_ball.main"]
        assert (b["value"], b["confidence"]) == ("stopped", "unknown")
        await r.c.reconcile_once()
        assert not r.c.s["desired"]["mirror_ball.main"]["settled"]           # unknown is never "there"
        r.ball.offline = False
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert r.ball.physical["main"] == "spinning" and r.c.s["desired"]["mirror_ball.main"]["settled"]
        assert r.c.s["believed"]["mirror_ball.main"]["confidence"] == "unconfirmed"
    asyncio.run(body())


# --- R4 the exit gate -----------------------------------------------------------------------------------------------------

def test_r4_exit_gate_stops_with_blinds_down(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.plug.offline = True
        t0 = time.time()
        tr = await go(r, "normal")
        assert tr["status"] == "failed" and tr["detail"] == "stopped at step 2: plug.dmx_power not ok"
        assert tr["source"] == "web" and r.c.s["transition"] is None          # no rollback
        assert not sends(r, "blinds", t0) and privacy(r) == DOWN              # the blinds stay down
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("disco", "normal")
        r.plug.offline = False
        clock.advance(3600)
        n = len(r.log)
        await r.c.reconcile_once()                                           # observe only: nothing applied
        assert len(r.log) == n
        t0 = time.time()
        tr = await go(r, "normal")                                           # Retry: the gate first
        o = [s for s, _, t in r.log if t >= t0]
        assert tr["status"] == "ok" and o.index("plug") < o.index("blinds") and privacy(r) == ORIGINAL
    asyncio.run(body())


# --- R5 reconcile vs requests ---------------------------------------------------------------------------------------------

def test_r5_tick_overlapping_request_drops_its_view(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.hand_set("ch03", 0)
        r.blinds.hang_read = True
        n = len(sent(r, "blinds"))
        tick = asyncio.create_task(r.c.reconcile_tick("blinds"))            # reads under disco's desired values...
        await asyncio.sleep(0.01)
        req = asyncio.create_task(r.c.request_mode("normal"))               # ...while a request comes in
        await asyncio.sleep(0.01)
        r.blinds.hang_read = False
        await tick
        await req
        await r.c.wait_idle()
        assert not r.c._rtasks and r.c.s["mode"]["current"] == "normal"
        assert sent(r, "blinds")[n:] == [{"ch02": 0, "ch03": 80}]            # normal's retrieve only: no re-lower
        assert r.blinds.value["ch03"] == 80
    asyncio.run(body())


def test_r5_two_ticks_one_apply(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        r.ball.offline = True
        await go(r, "disco")                                                 # the ball has a retry due later
        r.ball.offline = False
        clock.advance(BACKOFF[0])
        r.blinds.travel_s = 0.3
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_tick("blinds")                                   # a re-assert, running
        await r.c.reconcile_tick("mirror_ball")                              # busy: waits for the next tick
        assert len(r.c._rtasks) == 1
        task = next(iter(r.c._rtasks))
        await r.c.request_mode("normal")                                     # a request cancels it
        assert task.cancelled() and not r.c._rtasks
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "normal"
    asyncio.run(body())


# --- R6 closed slots ------------------------------------------------------------------------------------------------------

async def given_up_ch03(r, clock) -> None:
    """disco, then normal with ch03 deaf: closed slot; five retries with backoff; then gave_up."""
    await boot(r)
    await go(r, "disco")
    r.blinds.fail = {"ch03"}
    await go(r, "normal")
    for delay in BACKOFF:
        clock.advance(delay)
        await r.c.reconcile_once()
    clock.advance(BACKOFF[-1])
    await r.c.reconcile_once()
    retry = r.c.s["desired"]["blinds.ch03"]["retry"]
    assert retry["attempts"] == 5 and retry["gave_up"]
    assert r.c.s["stored"]["pre_live"]["blinds.ch03"]["closed"]          # the closed slot survives the give-up


def test_r6_closed_slot_survives_give_up_and_hub_return(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await given_up_ch03(r, clock)
        n = len(sent(r, "blinds"))
        clock.advance(3600)
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n                                   # given up: quiet
        r.blinds.offline = True                                              # the hub drops off and comes back
        await r.c.reconcile_once()
        r.blinds.offline, r.blinds.fail = False, set()
        await r.c.reconcile_once()                                           # available again: retried, restored
        assert r.blinds.value["ch03"] == 80 and stored(r) == {}
        await go(r, "disco")
        assert stored(r)["blinds.ch03"] == 80                                # the true original
    asyncio.run(body())


def test_r6_hand_move_after_give_up_is_accepted(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await given_up_ch03(r, clock)
        r.blinds.fail = set()
        r.blinds.hand_set("ch03", 50)
        n = len(sent(r, "blinds"))
        await go(r, "piano")
        assert r.blinds.value["ch03"] == 50 and len(sent(r, "blinds")) == n and stored(r) == {}
    asyncio.run(body())


# --- R7, R8 guards --------------------------------------------------------------------------------------------------------

def test_r7_hop_privacy_failure_is_partial(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.offline = True                                              # a read blip: stale at 100, re-sent, fails
        tr = await go(r, "ambient")
        assert tr["status"] == "partial" and tr["source"] == "web" and r.c.s["mode"]["current"] == "ambient"
        assert row(tr, "blinds.ch02")["status"] == "failed" and not r.c.s["desired"]["blinds.ch02"]["settled"]
        assert r.screens.value["north-screen"]["live"]["scene"] == "fractal-zoom"   # the scene still changed
        assert r.c.s["desired"]["blinds.ch02"]["retry"] is not None          # retried, no rollback
    asyncio.run(body())


def test_r7_reapply_failure_not_observe_only(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.blinds.hand_set("ch03", 0)
        r.blinds.fail = {"ch03"}
        tr = await go(r, "disco")                                            # re-apply: the room already held 100
        assert tr["status"] == "partial" and r.c.s["mode"]["target"] is None
        n = len(sent(r, "blinds"))
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert sent(r, "blinds")[n:] == [{"ch03": 100}]                      # retries go on
    asyncio.run(body())


@pytest.mark.parametrize("allow", [False, True])
def test_r8_skipped_guard_aborts_unless_allowed(tmp_path, allow):
    async def body():
        raw = example(lambda raw: raw["transitions"][0]["steps"][1]["on_fail"].update(allow_skipped=allow))
        r = make_room(tmp_path, raw)
        r.blinds.dry_run = True
        await boot(r)
        await go(r, "disco")
        tr = r.c.s["last_transition"]
        assert r.c.status()["subsystems"]["blinds"]["guard"] == "inactive"
        if not allow:
            assert tr["source"] == "rollback" and "blinds.ch02, blinds.ch03 not ok" in tr["rollback_of"]["detail"]
            assert r.plug.value["dmx_power"] == "off" and r.screens.value["north-screen"] == "art"
        else:
            assert tr["status"] == "partial" and tr["detail"] == "guard not enforced: blinds.ch02, blinds.ch03 skipped"
            assert row(tr, "blinds.ch02")["status"] == "skipped" and row(tr, "blinds.ch02")["guarded"]
            assert r.c.s["mode"]["current"] == "disco" and r.blinds.commands == []
    asyncio.run(body())


# --- R9 config edits ------------------------------------------------------------------------------------------------------

def test_r9_reload_moves_nothing_and_reapply_stores_first(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        n = len(r.log)
        new = RoomConfig(example(lambda raw: raw["groups"]["privacy_blinds"].append("blinds.ch09")), r.every)
        out = r.c.reload(new, cause="owner: ch09 too")
        assert out["pending_targets"] == ["blinds.ch09"] and len(r.log) == n and r.blinds.value["ch09"] == 50
        assert r.c.status()["config"]["pending_targets"] == ["blinds.ch09"]
        await go(r, "disco")                                                 # re-apply: store first, then lower
        assert stored(r)["blinds.ch09"] == 50 and r.blinds.value["ch09"] == 100
        assert r.c.status()["config"]["pending_targets"] == []
        await go(r, "normal")
        assert r.blinds.value["ch09"] == 50
    asyncio.run(body())


def test_r9_renamed_slot_kept_as_orphan(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.c.reload(RoomConfig(json.loads(json.dumps(example()).replace("pre_live", "pre_show")), r.every), cause="rename")
        assert r.c.status()["stored"]["pre_live"]["blinds.ch02"]["orphan"]
        await go(r, "normal")                                                # pre_show is empty: leave
        assert privacy(r) == DOWN
        assert r.c.s["stored"]["pre_live"]["blinds.ch02"] == r.c.s["stored"]["pre_live"]["blinds.ch02"] | {"orphan": True}
        r.c.stored_forget("pre_live", None, cause="owner")
        assert r.c.s["stored"] == {}
    asyncio.run(body())


def test_r9_renamed_current_mode(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.c.reload(RoomConfig(json.loads(json.dumps(example()).replace('"disco"', '"party"')), r.every), cause="rename")
        tr = await go(r, "normal")
        assert tr["status"] == "ok" and privacy(r) == ORIGINAL and signals_sent(r, "show over") == 1
        # A reload during a transition waits; the task's own config is used for its rollback.
        r.blinds.offline = True
        r.tv.travel_s = 0.15
        await r.c.request_mode("ambient")
        await asyncio.sleep(0.05)
        home = RoomConfig(json.loads(json.dumps(example()).replace('"normal"', '"home"')), r.every)
        assert r.c.reload(home, cause="rename")["deferred"]
        await r.c.wait_idle()
        rb = r.c.s["last_transition"]
        assert (rb["source"], rb["to"], rb["status"]) == ("rollback", "normal", "partial")   # the old config's name
        assert r.c.cfg.revision == home.revision
        r.blinds.offline = False
        tr = await go(r, "home")
        assert tr["status"] in ("ok", "partial") and r.c.s["mode"]["current"] == "home"
    asyncio.run(body())


# --- R10–R13 -------------------------------------------------------------------------------------------------------------

def test_r10_reapply_skips_rule_signals(tmp_path):
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["transitions"][0]["steps"].insert(
            0, {"signal": "log.info", "args": {"msg": "rule"}})))
        await boot(r)
        await go(r, "disco")
        await go(r, "ambient")                                               # rule 1 serves hops: it fires
        tr = await go(r, "ambient")                                          # a re-apply: it doesn't
        assert signals_sent(r, "rule") == 2
        assert (row(tr, "log")["status"], row(tr, "log")["detail"]) == ("skipped", "re-apply")
        assert r.c.s["last_transition"]["fired"] == []
    asyncio.run(body())


def test_r10_resume_skips_rule_signals_already_fired(tmp_path):
    """Rule signals are keyed step:<action>:<n> in `fired`; a resumed transition carries them and doesn't repeat one."""
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["transitions"][0]["steps"].insert(
            0, {"signal": "log.info", "args": {"msg": "rule"}})))
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        assert r.c.s["transition"]["fired"] == ["step:info:1"]
        await hard_kill(r.c)
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        tr = r.c.s["last_transition"]
        assert tr["fired"] == ["step:info:1"] and signals_sent(r, "rule") == 1
        assert (row(tr, "log")["status"], row(tr, "log")["detail"]) == ("skipped", "already fired")
    asyncio.run(body())


@pytest.mark.parametrize("persist", [True, False])
def test_r11_restart_settled_keeps_mask_suspended(tmp_path, persist):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        n = len(r.log)
        r.mask.persist = persist
        r.mask.restart()                                                     # room_service restarted
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.reconcile_once()
        assert len(r.log) == n and r.c._task is None                         # nothing resumed, nothing sent
        if persist:
            assert r.c.s["believed"]["sensor_mask.main"]["value"] == "suspended" and r.c.s["drift"] == {}
        else:                                                                # amnesia: only reported (respect)
            assert r.c.s["drift"]["sensor_mask.main"]["observed"] == "follow"
    asyncio.run(body())


def test_r12_failed_screen_not_retried_and_reset_on_exit(tmp_path):
    async def body():
        clock = Clock()
        screens = FakeScreens(SCREENS)
        r = make_room(tmp_path, now=clock, adapters=fake_adapters(screens=screens))
        await boot(r)
        await go(r, "disco")
        screens.give_up()                                                    # 3 fallbacks in 10 min
        n = len(sent(r, "screens"))
        await r.c.reconcile_once()
        assert r.c.s["drift"]["screens.north-screen"]["observed"] == "failed" and len(sent(r, "screens")) == n
        tr = await go(r, "disco")                                            # a deliberate new attempt, refused
        assert row(tr, "screens.north-screen")["status"] == "failed"
        assert r.c.s["desired"]["screens.north-screen"]["retry"]["final"]
        clock.advance(3600)
        n = len(sent(r, "screens"))
        await r.c.reconcile_once()
        assert len(sent(r, "screens")) == n                                  # never retried
        await go(r, "normal")
        assert sent(r, "screens")[-1] == {"north-screen": "art", "south-screen": "art"} and screens.refuse == set()
    asyncio.run(body())


def test_r13_disco_to_piano_pauses_with_rest(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.log.clear()
        tr = await go(r, "piano")
        assert sent(r, "sonos") == [{"lounge.volume": 25, "lounge.playback": "paused"}]   # never the radio
        assert row(tr, "sonos.lounge.playback")["step"] == row(tr, "blinds.ch02")["step"]   # in rest, with the blinds
    asyncio.run(body())


def test_r13_piano_return_leaves_user_tracks(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "piano")
        r.sonos.hand_set("lounge.playback", {"uri": "tracks"})               # the user plays something
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        n = len(sent(r, "sonos"))
        await go(r, "piano")                                                 # back before disco reached Sonos
        assert len(sent(r, "sonos")) == n and r.sonos.value["lounge.playback"] == {"uri": "tracks"}
    asyncio.run(body())


# --- R14–R18 -------------------------------------------------------------------------------------------------------------

def test_r14_hand_set_on_an_enforced_target_is_accepted_and_overrides(tmp_path):
    """mo-7: no refusal and no hold needed: the person's value goes, drift says overridden, and it isn't put back."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        out = await (await r.c.hand_set("blinds.ch03", 0, source="owner"))
        drift = r.c.s["drift"]["blinds.ch03"]
        assert out.status == "ok" and (drift["overridden"], drift["manual"], drift["source"]) == (True, True, "owner")
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 0                                   # not re-lowered at the next tick
    asyncio.run(body())


def test_r15_taken_over_not_retried_reclaim_restores(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.tv.refuse, r.tv.refuse_detail = {"south-screen"}, "taken_over"
        tr = await go(r, "normal")
        assert (row(tr, "tv.south-screen")["status"], row(tr, "tv.south-screen")["detail"]) == ("failed", "taken_over")
        e = r.c.s["stored"]["pre_live"]["tv.south-screen"]
        assert e["closed"] and e["final"]
        clock.advance(10 ** 5)
        n = len(sent(r, "tv"))
        await r.c.reconcile_once()
        assert len(sent(r, "tv")) == n and "tv.south-screen" in stored(r)    # never retried, never accepted away
        r.tv.refuse = set()
        await go(r, "normal")                                                # Reclaim: a re-request
        assert r.tv.value["south-screen"] == "art_mode" and stored(r) == {}
    asyncio.run(body())


def test_r16_normalize_offline(tmp_path):
    async def body():
        a = fake_adapters()
        a.sonos.offline = True                                               # the speaker is unreachable at load
        r = make_room(tmp_path, adapters=a)
        assert r.cfg.modes["disco"].state["sonos.lounge.playback"].set == {"favourite": "Disco"}
        a.sonos.offline = False
        await boot(r)
        await go(r, "disco")
        assert sent(r, "sonos")[-1]["lounge.playback"] == {"favourite": "Disco"}   # resolved by the adapter at apply
    asyncio.run(body())


def test_r17_set_inside_family_keeps_slot(tmp_path):
    async def body():
        raw = example(lambda raw: raw["modes"].update(karaoke={"extends": "live", "state": {"sonos.lounge.volume": 70}}))
        r = make_room(tmp_path, raw)
        assert [w["code"] for w in current(r.cfg.lint())] == ["set_over_retrieve"]
        await boot(r)
        await go(r, "disco")
        await go(r, "karaoke")
        assert r.sonos.value["lounge.volume"] == 70 and stored(r)["sonos.lounge.volume"] == 25
        await go(r, "normal")
        assert r.sonos.value["lounge.volume"] == 25                          # the pre-disco volume
    asyncio.run(body())


def test_r18_return_before_completion_no_on_enter(tmp_path):
    async def body():
        r = make_room(tmp_path, example(lambda raw: raw["modes"]["piano"].update(
            on_enter=[{"signal": "log.info", "args": {"msg": "piano"}}])))
        await boot(r)
        await go(r, "piano")
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        await go(r, "piano")
        assert signals_sent(r, "piano") == 1 and "piano" in r.c.s["mode"]["entered"]
    asyncio.run(body())


# --- R19, R22 and status ----------------------------------------------------------------------------------------------------

def test_r19_room_page_compat(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sends(r, "blinds"))
        st = r.c.status()["mode"]
        assert st["transition"]["phase"] == "running" and st["last_transition"]["phase"] == "done"
        ids = [x["id"] for x in st["transition"]["steps"]]
        assert ids == [f"i{k}" for k in range(len(ids))]
        a = st["transition"]["id"]
        await hard_kill(r.c)
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        resumed = started(r)[-1]
        assert resumed["id"] != a and resumed["resumes"] == a and resumed["phase"] == "started"
        last = r.c.status()["mode"]["last_transition"]
        assert (last["id"], last["phase"], last["status"]) == (resumed["id"], "done", "ok")
    asyncio.run(body())


def test_r22_rollback_keeps_failure(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        failed = []
        on_event = r.c.on_event

        def spy(e):
            on_event(e)
            if e["data"].get("phase") == "done" and e["data"].get("status") == "failed":
                failed.append(copy.deepcopy(r.c.s["last_transition"]))
        r.c.on_event = spy
        r.blinds.offline = True
        await go(r, "disco")
        aborted, rb = failed[0], r.c.s["last_transition"]
        assert rb["rollback_of"] == {"id": aborted["id"], "to": "disco", "detail": aborted["detail"]}
        never = [x for x in aborted["steps"] if x["step"] > 2]
        assert never and all((x["status"], x["detail"], x["reason"]) == ("cancelled", "aborted: an earlier step failed",
                                                                        "aborted") for x in never)
        assert started(r)[-1]["source"] == "rollback" and started(r)[-1]["rollback_of"]["id"] == aborted["id"]
    asyncio.run(body())


def test_status_rows_ids_ages_and_round_trip(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        plan = started(r)[-1]["plan"]
        tr = r.c.s["last_transition"]
        assert [x["id"] for x in plan] == [x["id"] for x in tr["steps"]]     # written in full at the start...
        assert {x["status"] for x in plan} == {"pending"}
        assert all(e["data"]["step"]["id"] in {x["id"] for x in plan}       # ...and patched in place by id
                   for e in r.events if e["data"].get("phase") == "step" and e["data"]["id"] == tr["id"])
        st = r.c.status()
        m = st["mode"]
        assert {"current", "target", "transition", "last_transition", "via", "entered", "since_age_s", "default"} <= set(m)
        assert m["transition"] is None and (m["last_transition"]["phase"], m["last_transition"]["status"]) == ("done", "ok")
        assert m["last_transition"]["finished_age_s"] >= 0 and "finished_at" not in m["last_transition"]
        blinds = st["subsystems"]["blinds"]
        assert {"kind", "available", "believed", "members", "dry_run", "last_error", "last_ok_age_s"} <= set(blinds)
        assert blinds["believed"] == {"ch02": 100, "ch03": 100, "ch14": 100, "ch09": None}   # ch09 is never read
        assert st["subsystems"]["plug"]["believed"] == "on"                  # a single member: just the value
        assert st["subsystems"]["mirror_ball"]["available"] is True
        text = json.dumps(st)
        assert '_at"' not in text and json.loads(text) == st                # ages, never clock times
        assert {"revision", "error", "pending_targets", "warnings"} <= set(st["config"])
    asyncio.run(body())
