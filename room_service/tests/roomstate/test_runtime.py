"""ROOM_STATE_MODEL §8 scenario tests (S1–S13, F10, F19) and the state file (§3), on the example room with fakes.
Names follow §8's table. Pure asyncio: the only I/O is the state file under tmp_path.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import stat
import time

import pytest

from .harness import DOWN, ORIGINAL, RADIO, SCREENS, boot, example, fake_adapters, go, make_room, order, privacy, \
    restart, row, sent, signals_sent, started, stored
from room_service.roomstate.coordinator import BACKOFF, Busy, Refused
from room_service.roomstate.fakes import Clock, FakeScreens, hard_kill
from room_service.roomstate.model import RoomConfig

LIVE = {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}}}


async def until(cond, timeout: float = 2.0) -> None:
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "condition not reached"
        await asyncio.sleep(0.002)


def step_events(r, target: str, status: str, tid: str | None = None) -> list[float]:
    """Times of `status` step events for `target`; only transition `tid`'s when given."""
    return [e["t"] for e in r.events if e["type"] == "mode.transition" and e["data"]["phase"] == "step"
            and e["data"]["step"]["subsystem"] == target and e["data"]["step"]["status"] == status
            and (tid is None or e["data"]["id"] == tid)]


def first_sent(r, subsystem: str, after: float = 0.0) -> float:
    return next(t for s, _, t in r.log if s == subsystem and t >= after)


# --- S1 normal -> disco -----------------------------------------------------------------------------------------------

def test_s1_entry_order_nothing_lights_before_blinds(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.log.clear()
        await go(r, "disco")
        o = order(r)
        assert max(o.index("sensor_mask"), o.index("page")) < min(o.index("blinds"), o.index("tv"))
        assert max(o.index("blinds"), o.index("tv")) < o.index("plug") < o.index("screens") \
            < o.index("mirror_ball") < o.index("sonos")
        blinds_ok = max(step_events(r, "blinds.ch02", "ok") + step_events(r, "blinds.ch03", "ok"))
        assert blinds_ok <= first_sent(r, "plug")                           # the blinds are proven down first
    asyncio.run(body())


def test_s1_slots_on_disk_before_first_command(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        seen = []
        first = r.mask.apply

        async def peek(desired, opts=None):
            seen.append(json.loads(r.path.read_text())["stored"])
            return await first(desired, opts)
        r.mask.apply = peek                                                  # step 1 is the first command
        await go(r, "disco")
        assert seen[0]["pre_live"]["blinds.ch02"]["value"] == 0
        assert seen[0]["pre_live"]["tv.south-screen"]["value"] == "art_mode"
    asyncio.run(body())


def test_s1_tv_failure_is_partial_not_abort(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        r.tv.refuse, r.tv.refuse_detail = {"south-screen"}, "IP Control writes not approved: switch to HDMI by hand"
        tr = await go(r, "disco")                                            # v0 TVs: refused on purpose
        assert (tr["status"], r.c.s["mode"]["current"]) == ("partial", "disco")
        assert row(tr, "tv.south-screen")["status"] == "failed" and r.screens.value["north-screen"] != "art"
        assert r.ball.physical["main"] == "spinning" and privacy(r) == DOWN
        assert r.c.s["desired"]["tv.south-screen"]["retry"]["final"]
        n = len(sent(r, "tv"))
        clock.advance(3600)
        await r.c.reconcile_once()
        assert len(sent(r, "tv")) == n                                       # final: never retried
        r.tv.refuse = set()
        r.tv.fail = {"south-screen"}                                         # a TV that doesn't wake: unconfirmed
        tr = await go(r, "normal")
        tr = await go(r, "disco")
        assert tr["status"] == "partial" and row(tr, "tv.south-screen")["status"] == "unconfirmed"
    asyncio.run(body())


# --- S2, S3 -----------------------------------------------------------------------------------------------------------

def test_s2_live_to_live_no_restore_in_stream_change(tmp_path):
    async def body():
        screens = FakeScreens(SCREENS)
        r = make_room(tmp_path, adapters=fake_adapters(screens=screens))
        await boot(r)
        await go(r, "disco")
        before = {t: e for t, e in copy.deepcopy(r.c.s["stored"]["pre_live"]).items() if not t.startswith("sonos.")}
        n, screens.events[:] = len(sent(r, "screens")), []
        tr = await go(r, "ambient")
        assert tr["status"] == "ok"
        assert sent(r, "screens")[n:] == [{"north-screen": LIVE, "south-screen": LIVE}]   # one apply, the new spec
        assert {e for _, e in screens.events} == {"patch"}                  # in-stream: no exit or entry
        assert {t: e for t, e in r.c.s["stored"]["pre_live"].items()} == before   # nothing stored again
        assert r.sonos.value == {"lounge.volume": 25, "lounge.playback": RADIO}    # rest retrieves Sonos
        assert signals_sent(r, "show over") == 0 and signals_sent(r, "show starting") == 1
    asyncio.run(body())


def test_s3_each_member_back_to_its_own_value(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        await go(r, "ambient")
        r.log.clear()
        tr = await go(r, "normal")
        assert tr["status"] == "ok" and privacy(r) == ORIGINAL
        assert not any("ch14" in d for d in sent(r, "blinds"))              # already at 100: cleared, not sent
        assert sent(r, "tv") == [{"south-screen": "art_mode"}]              # north was already hdmi
        o = order(r)
        assert o.index("screens") < o.index("tv") and o.index("screens") < o.index("sensor_mask")
        assert stored(r) == {} and r.c.s["mode"]["entered"] == ["house", "normal"]
        assert signals_sent(r, "show over") == 1
    asyncio.run(body())


# --- S4 interruptions -------------------------------------------------------------------------------------------------

def test_s4_interrupt_real_hardware(tmp_path):
    """keep_moving + stale_until_stop: normal -> disco, then normal while the blinds are lowering. The cache still shows
    0/80, 'stopped', which isn't arrival anywhere; the blinds go back, and their slots clear only once A's commands must
    have finished (R1). No signals; the ball is never sent."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.stale_until_stop = 0.1, True
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        t_a = first_sent(r, "blinds")
        await asyncio.sleep(0.02)
        tr = await go(r, "normal")
        assert tr["status"] == "ok" and privacy(r) == ORIGINAL and stored(r) == {}
        # Only this transition's rows: under load disco's own lowering may already have proven ch02 at 100 (R1 is
        # about the retrieve back to 0 not being taken as arrived before disco's command could have finished).
        assert min(step_events(r, "blinds.ch02", "ok", tr["id"])) >= t_a + r.blinds.change_time_s - 0.01
        assert signals_sent(r, "show starting") == signals_sent(r, "show over") == 0
        assert r.ball.sent == [{"main": "stopped"}]                          # the boot's only
        assert r.c.s["mode"]["entered"] == ["house", "normal"]
    asyncio.run(body())


@pytest.mark.parametrize("when, path", [("before_go", ["entry", "abort"]), ("after_go", ["entry", "go", "live", "exit"])])
def test_s4_interrupt_during_screens_matched_path(tmp_path, when, path):
    """test_s4_interrupt_before_go_aborts_entry / _after_go_completes_then_exits: FakeScreens; the ball never starts."""
    async def body():
        screens = FakeScreens(SCREENS, go_after_s=0.15 if when == "before_go" else 0.01,
                              entry_s=0.15 if when == "after_go" else 0.01)
        r = make_room(tmp_path, adapters=fake_adapters(screens=screens))
        await boot(r)
        await r.c.request_mode("disco")
        await until(lambda: ("north-screen", "entry" if when == "before_go" else "go") in screens.events)
        await go(r, "normal")
        assert [e for m, e in screens.events if m == "north-screen"] == path
        assert screens.value["north-screen"] == "art" and r.ball.sent == [{"main": "stopped"}]
    asyncio.run(body())


def test_s4_redirect_to_other_live_uses_entry_order(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        r.log.clear()
        tr = await go(r, "ambient")                                          # from {normal, disco}: rule 1
        o = order(r)
        assert tr["status"] == "ok" and "blinds" in o and max(i for i, s in enumerate(o) if s == "blinds") < o.index("screens")
        assert privacy(r) == DOWN and stored(r)["blinds.ch02"] == 0
    asyncio.run(body())


def test_s4_interrupted_exit_redirect_keeps_exit_order(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.plug.travel_s = 0.1
        await r.c.request_mode("normal")
        await until(lambda: sent(r, "plug")[-1:] == [{"dmx_power": "off"}])
        r.log.clear()
        tr = await go(r, "piano")                                            # from {disco, normal}: rule 2
        o = order(r)
        assert tr["status"] == "ok" and o.index("plug") < o.index("blinds")
        assert r.plug.value["dmx_power"] == "off" and privacy(r) == ORIGINAL
    asyncio.run(body())


# --- S5 restarts ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kill", ["sigkill", "sigterm"])
def test_s5_resume(tmp_path, kill):
    """test_s5_sigkill_resume / test_s5_sigterm_resume: both resume toward disco under rule 1, under a new id with
    resumes; nothing stored again; nothing fired twice."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco", "alexa")
        await until(lambda: sent(r, "blinds"))
        a = r.c.s["transition"]["id"]
        slots = copy.deepcopy(r.c.s["stored"])
        if kill == "sigkill":
            await hard_kill(r.c)
        else:
            r.c._task.cancel()                                               # the cancel handler writes 'cancelled'
            await r.c.wait_idle()
            assert r.c.s["transition"]["status"] == "cancelled" and r.c.s["mode"]["target"] == "disco"
        r.log.clear()
        r.c = restart(r)
        await r.c.start(reconcile=False)
        b = r.c.s["transition"]
        assert b["resumes"] == a and b["id"] != a and b["source"] == "alexa" and b["via"] == ["disco"]
        await r.c.wait_idle()
        tr = r.c.s["last_transition"]
        assert (tr["status"], r.c.s["mode"]["current"]) == ("ok", "disco")
        assert r.c.s["stored"] == slots                                      # same entries, same stored_at
        o = order(r)
        assert o.index("blinds") < o.index("plug")                          # the entry order is kept
        assert signals_sent(r, "show starting") == 1
        assert started(r)[-1]["resumes"] == a
    asyncio.run(body())


@pytest.mark.parametrize("kind", ["edge_discrete", "edge_toggle"])
def test_s5_crash_mid_edge_send(tmp_path, kind):
    async def body():
        r = make_room(tmp_path, ball_kind=kind)
        await boot(r)
        if kind == "edge_toggle":
            r.c.assume("mirror_ball.main", "stopped", cause="owner")
        r.ball.delay_s = 0.2
        await r.c.request_mode("disco")
        await until(lambda: (row(r.c.s["transition"] or {"steps": []}, "mirror_ball.main") or {}).get("status") == "running")
        await hard_kill(r.c)                                                 # the code may or may not have gone out
        assert r.c.file.load()["believed"]["mirror_ball.main"]["confidence"] == "unknown"
        r.ball.delay_s = 0
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        tr = r.c.s["last_transition"]
        if kind == "edge_discrete":                                          # re-sent once: safe
            assert r.ball.sent == [{"main": "stopped"}, {"main": "spinning"}] and tr["status"] == "ok"
        else:                                                                # never sent blind
            ball = row(tr, "mirror_ball.main")
            assert (ball["status"], ball["detail"], tr["status"]) == ("unconfirmed", "toggle state unknown; not sent", "partial")
            assert r.ball.sent == []
    asyncio.run(body())


def test_s5_crash_mid_signal_is_at_most_once(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.signals.delay_s = 0.3
        await r.c.request_mode("disco")
        await until(lambda: (row(r.c.s["transition"] or {"steps": []}, "log") or {}).get("status") == "running")
        await hard_kill(r.c)
        r.signals.delay_s = 0
        r.c = restart(r)
        await r.c.start(reconcile=False)
        killed = r.c.s["last_transition"]
        assert (row(killed, "log")["status"], row(killed, "log")["detail"]) == ("unconfirmed", "process stopped while sending")
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "disco" and signals_sent(r, "show starting") == 0   # lost, never repeated
    asyncio.run(body())


# --- S6 hand changes in a show ------------------------------------------------------------------------------------------

def test_s6_enforce_reasserts_once_then_overridden(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 100 and r.c.s["drift"]["blinds.ch03"]["reasserted"]
        n = len(sent(r, "blinds"))
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_once()
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n and r.blinds.value["ch03"] == 0  # one RF frame, not one per poll
        assert r.c.s["drift"]["blinds.ch03"]["overridden"]
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 80                                  # the pre-episode value (D5)
    asyncio.run(body())


def test_s6_reconcile_apply_is_cancelled_by_request(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.travel_s = 0.5
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_tick("blinds")                                   # the re-assert runs as an _rtask
        assert len(r.c._rtasks) == 1
        await asyncio.sleep(0.02)
        t0 = time.time()
        await r.c.request_mode("normal")
        await until(lambda: started(r)[-1]["to"] == "normal")
        assert [e["t"] for e in r.events if e["data"].get("phase") == "started"][-1] - t0 < 0.05
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "normal"
    asyncio.run(body())


# --- S7 slots ---------------------------------------------------------------------------------------------------------

def test_s7_hand_move_in_normal_is_stored_next_time(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.hand_set("ch02", 30)
        await r.c.reconcile_once()
        assert r.c.s["drift"] == {}                                          # in normal the blinds are the house's
        await go(r, "disco")
        assert stored(r)["blinds.ch02"] == 30
    asyncio.run(body())


async def closed_ch03(r) -> None:
    """disco, then normal with ch03 never arriving: its slot entry is left closed, explained {100, 80}."""
    await boot(r)
    await go(r, "disco")
    r.blinds.fail = {"ch03"}
    tr = await go(r, "normal")
    e = r.c.s["stored"]["pre_live"]["blinds.ch03"]
    assert tr["status"] == "partial" and e["closed"] and sorted(e["explained"]) == [80, 100]


def test_s7_closed_slot_kept_by_next_store_when_untouched(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await closed_ch03(r)
        r.blinds.fail = set()
        await go(r, "disco")                                                 # ch03 still reads 100: nobody moved it
        e = r.c.s["stored"]["pre_live"]["blinds.ch03"]
        assert e["value"] == 80 and not e["closed"]                          # the original, reopened
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 80
    asyncio.run(body())


def test_s7_closed_slot_replaced_when_moved_by_hand(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await closed_ch03(r)
        r.blinds.fail = set()
        r.blinds.hand_set("ch03", 20)
        await go(r, "disco")
        assert stored(r)["blinds.ch03"] == 20                                # the hand position is the new "before"
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 20
    asyncio.run(body())


def test_s7_retry_dropped_when_moved_by_hand(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await closed_ch03(r)
        r.blinds.hand_set("ch03", 20)
        n = len(sent(r, "blinds"))
        await r.c.reconcile_once()
        assert "blinds.ch03" not in stored(r) and r.c.s["desired"]["blinds.ch03"]["settled"]
        assert len(sent(r, "blinds")) == n and r.blinds.value["ch03"] == 20  # accepted, never fought
    asyncio.run(body())


def test_s7_lingering_slot_dropped_after_off(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        tr = await go(r, "off")
        assert tr["status"] == "ok" and r.tv.value == {"north-screen": "standby", "south-screen": "standby"}
        assert stored(r) == {} and privacy(r) == ORIGINAL                   # off stores nothing: the TV slots go
        await go(r, "normal")
        assert r.tv.value == {"north-screen": "standby", "south-screen": "standby"}   # never "restored" later
    asyncio.run(body())


# --- S8, S9 degraded devices ---------------------------------------------------------------------------------------------

def test_s8_store_from_failed_read_is_stale(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        at = r.c.s["believed"]["tv.south-screen"]["at"]
        clock.advance(60)
        r.tv.offline = True
        await go(r, "disco")
        e = r.c.s["stored"]["pre_live"]["tv.south-screen"]
        assert (e["value"], e["confidence"], e["at"]) == ("art_mode", "stale", at)
        st = r.c.status()
        assert not st["subsystems"]["tv"]["available"] and "unreachable" in st["subsystems"]["tv"]["last_error"]
        assert st["stored"]["pre_live"]["tv.south-screen"]["confidence"] == "stale"
    asyncio.run(body())


def test_s8_slow_tv_runs_parallel_with_blinds(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.tv.travel_s = r.blinds.travel_s = 0.3
        await go(r, "disco")
        running = min(step_events(r, "blinds.ch02", "running") + step_events(r, "tv.south-screen", "running"))
        ok = max(step_events(r, "blinds.ch02", "ok") + step_events(r, "tv.south-screen", "ok"))
        assert 0.3 <= ok - running < 0.5                                     # the max of the two, not the sum
    asyncio.run(body())


@pytest.mark.parametrize("behind", [True, False])
def test_page_policy_is_proved_behind_the_later_steps(tmp_path, behind):
    """The page policy (prove_behind) goes out at its step and is proved while the steps after it run, so a page slow
    to report (kiosk.state every 5 s on the TVs) never holds the screens. Its rows still end ok, and the switch is done
    only once they have."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.page.prove_behind = behind
        r.page.travel_s = 0.4
        r.log.clear()
        t0 = time.time()
        tr = await go(r, "disco")
        page_ok = max(t for s in SCREENS for t in step_events(r, f"page.{s}.timed_reload", "ok", tr["id"]))
        o = order(r)
        assert o.index("sensor_mask") < o.index("blinds") and o.index("page") < o.index("blinds")   # still sent first
        assert o.index("blinds") < o.index("plug") < o.index("screens")
        if behind:
            assert first_sent(r, "screens", t0) < page_ok                  # the screens didn't wait for the report
        else:
            assert first_sent(r, "screens", t0) > page_ok
        assert all(row(tr, f"page.{s}.timed_reload")["status"] == "ok" for s in SCREENS)
        assert tr["status"] == "ok" and tr["finished_at"] >= page_ok
    asyncio.run(body())


def test_a_guarded_prove_behind_target_holds_its_step(tmp_path):
    def guard_page(raw):
        raw["transitions"][0]["steps"][0] = {"targets": ["sensor_mask", "page"], "on_fail": {"abort_on": "page"}}

    async def body():
        r = make_room(tmp_path, example(guard_page))
        await boot(r)
        r.page.prove_behind = True
        r.page.travel_s = 0.2
        t0 = time.time()
        tr = await go(r, "disco")
        page_ok = max(t for s in SCREENS for t in step_events(r, f"page.{s}.timed_reload", "ok", tr["id"]))
        assert first_sent(r, "blinds", t0) > page_ok                     # its outcome decides the abort: waited for
    asyncio.run(body())


def test_a_cancelled_switch_cancels_its_proofs_behind(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.page.prove_behind = True
        r.page.travel_s = 0.5
        r.screens.travel_s = 0.3                   # still entering when the next request comes
        await r.c.request_mode("disco")
        await until(lambda: any(s == "screens" for s, _, _ in r.log))
        first = r.c.s["transition"]
        await r.c.request_mode("normal")
        await r.c.wait_idle()
        await asyncio.sleep(0.6)                   # past the page's travel: a proof left running would land now
        assert first["status"] == "cancelled"
        assert {x["status"] for x in first["steps"] if x["subsystem"].startswith("page.")} == {"cancelled"}
        assert r.c.s["last_transition"]["to"] == "normal" and r.c.s["mode"]["current"] == "normal"
    asyncio.run(body())


def test_s9_hub_offline_aborts_and_rolls_back(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.offline = True
        r.log.clear()
        await go(r, "disco")
        rb = r.c.s["last_transition"]
        assert rb["source"] == "rollback" and rb["to"] == "normal" and rb["via"] == ["disco"]
        assert rb["rollback_of"]["to"] == "disco" and rb["rollback_of"]["detail"].startswith("stopped at step 2: blinds.ch02")
        assert not any(d in ({"dmx_power": "on"}, {"main": "spinning"}) for _, d, _ in r.log)   # nothing after step 2
        assert not any(v != "art" for d in sent(r, "screens") for v in d.values())
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("normal", None)
        assert r.mask.value["main"] == "follow" and r.page.value["devtest.timed_reload"] is True
        assert r.tv.value["south-screen"] == "art_mode"                     # rolled back with the exit order
        assert {t: d["policy"] for t, d in r.c.s["desired"].items() if t.startswith("blinds.")} == \
            {"blinds.ch02": "ignore", "blinds.ch03": "ignore", "blinds.ch14": "ignore"}   # never disco's enforce
        r.blinds.offline = False
        n = len(sent(r, "blinds"))
        await asyncio.sleep(r.blinds.change_time_s)                          # R1: until disco's command could have ended
        await r.c.reconcile_once()                                           # they never moved: settled, slots dropped
        assert len(sent(r, "blinds")) == n and stored(r) == {} and privacy(r) == ORIGINAL
    asyncio.run(body())


def test_s9_ball_offline_partial_and_retried(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        r.ball.offline = True
        tr = await go(r, "disco")
        assert tr["status"] == "partial" and row(tr, "mirror_ball.main")["status"] == "failed"
        b = r.c.s["believed"]["mirror_ball.main"]
        assert (b["value"], b["confidence"]) == ("stopped", "unknown")      # R3: not mistaken for "there"
        retry = lambda: r.c.s["desired"]["mirror_ball.main"]["retry"]       # noqa: E731
        for n, delay in enumerate(BACKOFF, start=1):                         # 30 s, 1, 2, 5, 10 min
            clock.advance(delay - 1)
            await r.c.reconcile_once()
            assert retry()["attempts"] == n - 1
            clock.advance(1)
            await r.c.reconcile_once()
            assert retry()["attempts"] == n
        clock.advance(BACKOFF[-1])
        await r.c.reconcile_once()
        assert retry()["gave_up"] and not r.c.status()["subsystems"]["mirror_ball"]["available"]
        r.ball.offline = False
        clock.advance(3600)
        await r.c.reconcile_once()
        assert r.ball.sent == [{"main": "stopped"}]                          # gave up: no more tries
        r.c._health("mirror_ball", ok=True)                                  # the RM4 is back (room_service reports it)
        await r.c.reconcile_once()
        assert r.ball.physical["main"] == "spinning" and r.c.s["desired"]["mirror_ball.main"]["settled"]
    asyncio.run(body())


def test_s9_hub_returns_retrieve_retried(tmp_path):
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.blinds.offline = True
        tr = await go(r, "normal")
        assert tr["status"] == "partial" and r.c.s["stored"]["pre_live"]["blinds.ch02"]["closed"]
        r.blinds.offline = False
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert privacy(r) == ORIGINAL and stored(r) == {}                    # cleared on success
    asyncio.run(body())


def test_slow_readback_is_proven_not_assumed(tmp_path):
    """The TV adapter says ok when its command is acked, but IP Control reads the old input for a while: the row is ok
    only once a reading shows hdmi (_prove), not on the adapter's word."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.tv.readback_lag_s, r.tv.change_time_s = 0.15, 0.4
        await go(r, "disco")
        t_sent = first_sent(r, "tv")
        assert min(step_events(r, "tv.south-screen", "ok")) >= t_sent + r.tv.travel_s + 0.15
        assert r.c.s["believed"]["tv.south-screen"]["value"] == "hdmi"
    asyncio.run(body())


def test_adapter_that_swallows_a_cancel_still_stops_the_transition(tmp_path):
    """A contract breach (swallow_cancel): gather re-raises the cancel, so the steps after the blinds never start."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.swallow_cancel = 0.1, True
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        disco = r.c.s["transition"]["id"]
        await go(r, "normal")
        cancelled = [e["data"]["id"] for e in r.events if e["data"].get("phase") == "cancelled"]
        assert disco in cancelled and r.plug.value["dmx_power"] == "off" and r.ball.sent == [{"main": "stopped"}]
        assert privacy(r) == ORIGINAL and r.c.s["mode"]["current"] == "normal"
    asyncio.run(body())


def test_s9_placeholder_rows_skipped(tmp_path):
    async def body():
        r = make_room(tmp_path, without=("sonos",))                          # no Sonos adapter yet
        await boot(r)
        tr = await go(r, "disco")
        vol = row(tr, "sonos.lounge.volume")
        assert (vol["status"], vol["detail"], vol["reason"]) == ("skipped", "no adapter", "unavailable")
        assert tr["status"] == "ok"                                          # unguarded: not partial
        assert r.c.status()["subsystems"]["sonos"]["available"] is False
    asyncio.run(body())


def test_s9_hung_apply_times_out_unconfirmed(tmp_path):
    async def body():
        r = make_room(tmp_path, margin_s=0.1)
        await boot(r)
        r.plug.hang_apply = True
        tr = await go(r, "disco")
        assert (row(tr, "plug.dmx_power")["status"], row(tr, "plug.dmx_power")["detail"]) == ("unconfirmed", "timeout")
        assert tr["status"] == "partial" and r.ball.physical["main"] == "spinning"
        r.plug.hang_apply = False
    asyncio.run(body())


def test_hung_read_marks_stale(tmp_path):
    async def body():
        r = make_room(tmp_path, margin_s=0.1)
        await boot(r)
        r.tv.read_timeout_s, r.tv.hang_read = 0.05, True
        await go(r, "disco")
        assert r.c.s["believed"]["tv.north-screen"]["confidence"] == "stale"
        assert not r.c.status()["subsystems"]["tv"]["available"]
        r.tv.hang_read = False
        await r.c.reconcile_once()
        assert r.c.status()["subsystems"]["tv"]["available"]
    asyncio.run(body())


# --- S10 edges after a restart ------------------------------------------------------------------------------------------

def test_s10_toggle_unknown_row_and_assume(tmp_path):
    async def body():
        r = make_room(tmp_path, ball_kind="edge_toggle")
        await boot(r)
        tr = await go(r, "disco")
        ball = row(tr, "mirror_ball.main")
        assert (ball["status"], ball["reason"], tr["status"]) == ("unconfirmed", "unknown_state", "partial")
        r.c.assume("mirror_ball.main", "stopped", cause="owner: it is off")
        tr = await go(r, "disco")                                            # a re-apply flips it correctly
        assert r.ball.physical["main"] == "spinning" and r.ball.sent == [{"main": "spinning"}] and tr["status"] == "ok"
    asyncio.run(body())


def test_s10_restart_does_not_resend_edges(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        n = len(r.ball.sent)
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.reconcile_once()
        assert len(r.ball.sent) == n and r.c._task is None                  # trusted as saved
        r.c.s["believed"]["mirror_ball.main"]["confidence"] = "unknown"      # as a crash mid-send leaves it
        r.c._save()
        r.c = restart(r)
        await r.c.start(reconcile=False)                                     # a retry due now (R3)
        await r.c.reconcile_once()
        assert r.ball.sent[n:] == [{"main": "spinning"}]
    asyncio.run(body())


# --- S11, S12 signals and partial ownership -----------------------------------------------------------------------------

def test_s11_same_mode_request_fires_nothing(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        slots, n = copy.deepcopy(r.c.s["stored"]), len(r.signals.sent)
        tr = await go(r, "disco")
        assert tr["reapply"] and tr["status"] == "ok"
        assert [(x["subsystem"], x["status"]) for x in tr["steps"]] == [("delay", "skipped")]   # nothing to send
        assert len(r.signals.sent) == n and r.c.s["stored"] == slots
    asyncio.run(body())


def test_s11_base_mode_signals_fire_at_family_edge(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        assert signals_sent(r, "show starting") == 1 and r.c.s["mode"]["entered"] == ["house", "live", "disco"]
        await go(r, "ambient")
        assert signals_sent(r, "show starting") == 1 and signals_sent(r, "show over") == 0
        assert r.c.s["mode"]["entered"] == ["house", "live", "ambient"]
        await go(r, "normal")
        assert signals_sent(r, "show over") == 1 and r.c.s["mode"]["entered"] == ["house", "normal"]
    asyncio.run(body())


def test_s12_sonos_unmanaged_in_normal_retrieved_once(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.sonos.hand_set("lounge.volume", 40)
        await r.c.reconcile_once()
        assert r.c.s["drift"] == {} and sent(r, "sonos") == []
        await go(r, "disco")
        await go(r, "normal")
        assert sent(r, "sonos")[-1] == {"lounge.volume": 40, "lounge.playback": RADIO}   # once, at the end
        n = len(sent(r, "sonos"))
        await go(r, "ambient")
        await go(r, "normal")
        assert len(sent(r, "sonos")) == n                                    # nothing stored for Sonos: untouched
    asyncio.run(body())


def test_s12_interrupted_entry_no_exit_signal(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        await go(r, "normal")
        assert signals_sent(r, "show over") == 0 and signals_sent(r, "show starting") == 0
    asyncio.run(body())


# --- S13 requesters -----------------------------------------------------------------------------------------------------

def test_s13_concurrent_requests_last_wins(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.cancel_cleanup_s = 0.1, 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        await asyncio.gather(r.c.request_mode("ambient", "alexa"), r.c.request_mode("normal", "web"))
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "normal" and r.c.s["last_transition"]["source"] == "web"
        assert [s["to"] for s in started(r)] == ["normal", "disco", "normal"]   # boot, disco, normal: ambient never ran
        cancelled = [e["data"]["id"] for e in r.events if e["data"].get("phase") == "cancelled"]
        assert len(cancelled) == 2                                           # disco, and ambient before it planned
    asyncio.run(body())


def test_s13_source_recorded_and_reserved_rejected(tmp_path):
    """test_s13_source_recorded / test_s13_reserved_source_rejected"""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        assert r.c.s["last_transition"]["source"] == "boot"
        await go(r, "disco", source="alexa")
        assert r.c.status()["mode"]["last_transition"]["source"] == "alexa"
        for source in ("boot", "resume", "rollback"):
            with pytest.raises(Refused) as e:
                await r.c.request_mode("normal", source)
            assert e.value.code == "bad_source"
    asyncio.run(body())


def test_s13_manual_screen_during_transition_applies_and_holds(tmp_path):
    """Owner 17 Oct (was a 409): a switch running never blocks a hand change.  It applies now, the switch leaves
    that target alone (its row: skipped, by hand) and the value holds until the next switch."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.2
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        task = await r.c.hand_set("screens.devtest", LIVE)                 # mid-switch: accepted
        out = await task
        assert out.status == "ok"
        await r.c.wait_idle()
        assert r.c.s["believed"]["screens.devtest"]["value"] == LIVE        # the switch didn't put it back
        tr = r.c.s["last_transition"]
        row = next((x for x in tr["steps"] if x["subsystem"] == "screens.devtest"), None)
        assert row is None or row["status"] in ("skipped", "ok") and (row["status"] != "skipped" or row.get("reason") == "hand")
        d = r.c.s["desired"].get("screens.devtest")
        if d and d["value"] != LIVE:
            assert r.c.s["drift"]["screens.devtest"]["manual"]              # by hand, respected
    asyncio.run(body())


# --- F10, F19 -------------------------------------------------------------------------------------------------------------

def test_exception_in_run_fails_cleanly(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.page.show = lambda member, value: 1 / 0                            # a bug while planning the rows
        tr = await go(r, "normal")
        assert tr["status"] == "failed" and "division" in tr["detail"] and r.c.s["transition"] is None
        assert (r.c.s["mode"]["current"], r.c.s["mode"]["target"]) == ("disco", "normal")   # a restart tries once more
        assert r.c.s["log"][-1]["op"] == "error"
        await r.c.reconcile_once()                                           # reconcile goes on (observe only)
        del r.page.show
        renamed = RoomConfig(json.loads(json.dumps(example()).replace('"disco"', '"party"')), r.every)
        r.cfg = renamed                                                      # disco is gone from the config
        r.c = restart(r)
        r.c.s["mode"]["target"] = None
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        assert r.c.s["last_transition"]["source"] == "boot" and r.c.s["mode"]["current"] == "normal"
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_boot_request_before_boot_ran(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await r.c.start(reconcile=False)                                     # the boot task exists but hasn't run
        boot_id = r.c.s["transition"]["id"]
        await r.c.request_mode("disco")
        assert r.c.s["last_transition"]["id"] == boot_id and r.c.s["last_transition"]["status"] == "cancelled"
        assert any(e["data"] == {"id": boot_id, "phase": "cancelled"} for e in r.events)
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "disco"
    asyncio.run(body())


# --- the state file (§3) ----------------------------------------------------------------------------------------------------

def test_state_file_v2_private_and_saved_on_change_only(tmp_path):
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        data = json.loads(r.path.read_text())
        assert data["version"] == 2 and data["saved_at"] > 0 and data["mode"]["current"] == "normal"
        assert stat.S_IMODE(os.stat(r.path).st_mode) == 0o600 and stat.S_IMODE(os.stat(r.path.parent).st_mode) == 0o700
        await r.c.reconcile_once()                                           # the first poll learns idle members
        r.c._save()
        writes = []
        save = r.c.file.save
        r.c.file.save = lambda d: writes.append(save(d)) or writes[-1]
        await r.c.reconcile_once()                                           # reads that change nothing don't write
        assert not any(writes)
        r.blinds.hand_set("ch09", 10)                                        # unmanaged, but read (poll_all): the
        await r.c.reconcile_once()                                           # belief changed, so one write
        assert sum(map(bool, writes)) == 1 and r.c.s["believed"]["blinds.ch09"]["value"] == 10
        await r.c.reconcile_once()
        assert sum(map(bool, writes)) == 1
    asyncio.run(body())


@pytest.mark.parametrize("text", ['{"version": 1, "mode": {"current": "disco"}}', "{not json"])
def test_state_file_unknown_version_or_garbage_moved_aside(tmp_path, text):
    async def body():
        r = make_room(tmp_path)
        r.path.parent.mkdir(parents=True, exist_ok=True)
        r.path.write_text(text)
        r.c = restart(r)
        assert r.c.s["mode"]["current"] is None and r.c.s["log"][-1]["op"] == "state_file"
        assert [p.name.split("-")[0] for p in r.path.parent.iterdir()] == ["state.json.bad"]
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "normal" and stored(r) == {}
    asyncio.run(body())


def test_poll_all_reads_members_no_mode_manages_without_drift_or_commands(tmp_path):
    """d4, 15 Oct, and the default since 16 Oct (68/2f: idle beliefs went stale): reconcile reads every member of a
    level adapter; members with no desired entry only update beliefs (no drift, no enforce, nothing sent). An adapter
    whose reads cost the device opts out with poll_all=False."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.poll_all = False
        r.blinds.hand_set("ch09", 33)                         # ch09 isn't managed by any mode
        n = len(r.blinds.commands)
        await r.c.reconcile_once()
        assert "blinds.ch09" not in r.c.s["believed"] or r.c.s["believed"]["blinds.ch09"]["value"] != 33
        r.blinds.poll_all = True
        await r.c.reconcile_once()
        assert r.c.s["believed"]["blinds.ch09"]["value"] == 33
        assert "blinds.ch09" not in r.c.s["drift"] and len(r.blinds.commands) == n
    asyncio.run(body())
