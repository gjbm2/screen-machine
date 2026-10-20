"""The tracers' probes (ROOM_STATE_MODEL §9.3, last paragraph) as regression tests, ported to v1.1 on the example room.

Three tracers ran aa's reference: `trace/exp*.py` (E1-E22, findings F1-F23), `tracer-generality/probes` (G1-G14) and
the spike's `test_tracer_probes.py` (P1-P14, findings I-F1-I-F16). Each probe asserted the old, broken behaviour; each
test here asserts what the spec says instead. The spec names findings only by §1.3 row, so a test carries every ID its
row gives the probe's scenario. One test per finding:

  F1  I-F1   P2, E6, exp7     a moving or in-flight target is re-sent and proven, whatever its reading says (§4.4)
  F2  G8     P12, E3, probes2/3   requests serialised; the last wins; nothing orphaned (§4.2)
  F3         E16              a cancelled transition resumes after a restart; reconcile isn't blocked (§4.9)
  F4  I-F3   E21, E4, P3, P9  an abort rolls back with the exit order and never enforces the aborted mode (§4.7)
  F5  I-F5   P4, E8, E22      no exit signal for a mode never entered (§4.5)
  F6  F18    P13, E12         mask first; DMX power only after the blinds (§2.2 rule 1, I-F10)
  F7  G9     P8, E9, probes   a store while unreachable is stale with its old time; unavailable (§4.4)
  F8  G13    E10, E11         enforce once, then overridden; reconcile applies are cancellable (§4.8)
  F9         E7               the ledger is on disk before a send: a toggle never flips twice (§3.4)
  F10        E5               a config without the current mode can't wedge the room (§4.3, §4.9)
  F11 I-F8   P11              every adapter call has a timeout (§4.1)
  F12        E18              a retrieve that didn't land is retried until a hand move is accepted (§4.8)
  F13        P1, E1, probes   a retrieve that needed nothing still clears its slot (§4.4, §4.6)
  F14        E19              only a guarded target aborts (§2.4)
  F15 I-F4   P6               normalize() at load rejects a value the adapter can't take (§2.5)
  F17        E15              an unknown toggle is never sent (§4.4)
  F19        E20              a crash leaves 'running' on disk; the restart finalises it (§4.2)
  F20 G14    P14              a screen entry interrupted before/after go: matched cut, never live in art (§5.4)
  F23 G12    P5, E2, probes   a re-apply fires nothing; Sonos playback is state, not pause/play (§4.2, §2.2)
  G1         probes2          signals fire at family edges; they aren't copied into children (§4.5)
  G2         probes           a plain value over a store+set keeps the store (§2.4 merge rule 1)
  G4         probes, E13      max_age_s at retrieve only: a long episode can't capture "lowered" (§4.6)
  G5  I-F7   P7, probes       a subsystem with no adapter is a placeholder, not a ConfigError (§5.3)
  G6  I-F6   P10              rows written at the start, in the LIVE_V1 shape (§7.2)
  G10        probes           specificity beats key order; prefixes expand at dots (§2.4)

Not ported: G7's "never abort on unconfirmed" (rejected in §1.3; the F14 test asserts the guarded abort instead);
probes::test_s2_order (print-only; the hop order it shows is §6 S2's plan, and a separate @live -> @live rule is
rejected in §1.3); E14 (it confirmed aa's code, see test_adapter_that_swallows_a_cancel_still_stops_the_transition).
"""

from __future__ import annotations

import asyncio
import copy
import json
import time

import pytest

from .harness import DOWN, ORIGINAL, RADIO, boot, example, fake_adapters, go, make_room, order, privacy, restart, row, \
    sent, signals_sent, started, stored
from room_service.roomstate.coordinator import BACKOFF, Coordinator
from room_service.roomstate.contract import Placeholder
from room_service.roomstate.fakes import Clock, FakeScreens, hard_kill
from room_service.roomstate.model import ConfigError, RoomConfig

LIVE = {"live": {"scene": "fractal-zoom", "transitions": {"in": "crossfade-3s", "out": "crossfade-3s"}}}


async def until(cond, timeout: float = 2.0) -> None:
    t0 = time.monotonic()
    while not cond():
        assert time.monotonic() - t0 < timeout, "condition not reached"
        await asyncio.sleep(0.002)


def oks(r, target: str) -> list[float]:
    return [e["t"] for e in r.events if e["type"] == "mode.transition" and e["data"]["phase"] == "step"
            and e["data"]["step"]["subsystem"] == target and e["data"]["step"]["status"] == "ok"]


def phase(r, name: str) -> list[dict]:
    return [e["data"] for e in r.events if e["type"] == "mode.transition" and e["data"]["phase"] == name]


def adapters():
    return list(vars(fake_adapters()).values())


# --- F: trace/exp*.py and the spike's P probes ---------------------------------------------------------------------------

def test_f1_moving_targets_are_resent_and_proven_whatever_they_read(tmp_path):
    """P2, E6, exp7: normal -> disco, then normal while ch03 is lowering and the south TV is switching. ch03 reads 81,
    within tolerance of its stored 80, but is moving; the TV's readback still shows art_mode (a stale cache). Both were
    commanded, so both are sent back, and their slots clear only once a reading proves it."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.change_time_s = 0.1, 0.25
        r.tv.travel_s, r.tv.change_time_s, r.tv.stale_until_stop = 0.1, 0.25, True
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds") and sent(r, "tv"))
        r.blinds.value["ch03"] = 81                                          # passing its pre-show position
        await asyncio.sleep(0.01)
        r.log.clear()
        tr = await go(r, "normal")
        assert sent(r, "blinds") == [{"ch02": 0, "ch03": 80}] and sent(r, "tv") == [{"south-screen": "art_mode"}]
        assert tr["status"] == "ok" and privacy(r) == ORIGINAL and r.tv.value["south-screen"] == "art_mode"
        assert stored(r) == {}
    asyncio.run(body())


def test_f2_g8_request_during_cancel_cleanup_runs_only_the_last(tmp_path):
    """E3 (exp2-4), P12, probes2/3: ambient is running with a slow cancel cleanup; disco arrives, and normal arrives
    while disco's request still waits for that cleanup. Only normal runs to the end, the others are finalised as
    cancelled, no task is left behind, and the ball never spins."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.cancel_cleanup_s = 0.1, 0.1
        ambient = await r.c.request_mode("ambient")
        await until(lambda: sent(r, "blinds"))
        n_done = len(phase(r, "done"))
        a = asyncio.create_task(r.c.request_mode("disco", "alexa"))
        await asyncio.sleep(0.01)                                            # disco's request waits for the cleanup
        b = asyncio.create_task(r.c.request_mode("normal"))
        disco, normal = await asyncio.gather(a, b)
        await r.c.wait_idle()
        assert [d["id"] for d in phase(r, "done")[n_done:]] == [normal]     # one transition finished, not two
        assert {ambient, disco} <= {d["id"] for d in phase(r, "cancelled")}
        assert r.c.s["last_transition"]["source"] == "web" and r.c.s["mode"]["current"] == "normal"
        assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []
        assert r.ball.sent == [{"main": "stopped"}] and privacy(r) == ORIGINAL
    asyncio.run(body())


def test_f3_cancelled_transition_resumes_after_a_restart_and_reconcile_runs(tmp_path):
    """E16: a SIGTERM mid-entry writes 'cancelled' and keeps mode.target. The restart resumes toward disco, and
    reconcile, which the cancelled record used to block for good, enforces the privacy blinds again."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        r.c._task.cancel()
        await r.c.wait_idle()
        disk = json.loads(r.path.read_text())
        assert disk["transition"]["status"] == "cancelled" and disk["mode"]["target"] == "disco"
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        m = r.c.s["mode"]
        assert (m["current"], m["target"], r.c.s["transition"]) == ("disco", None, None)
        assert r.c.s["last_transition"]["resumes"] == disk["transition"]["id"]
        r.blinds.hand_set("ch02", 10)
        await r.c.reconcile_once()
        assert r.blinds.value["ch02"] == 100 and r.c.s["drift"]["blinds.ch02"]["reasserted"]
    asyncio.run(body())


def test_f4_late_abort_rolls_back_in_exit_order_and_never_enforces_the_aborted_mode(tmp_path):
    """E21, E4, P3, P9: an owner rule that guards the screens step; north-screen never goes live, after DMX power came
    on. The rollback to normal is planned with via [disco], so the exit order applies (DMX off before any blind rises);
    desired values are normal's, so reconcile never re-lowers a blind; status keeps the failure and clears target."""
    async def body():
        raw = example(lambda x: x["transitions"][0]["steps"].__setitem__(4, {"targets": "frames", "on_fail": "abort"}))
        r = make_room(tmp_path, raw)
        await boot(r)
        r.screens.fail = {"north-screen"}
        r.log.clear()
        await go(r, "disco")
        st = r.c.status()["mode"]
        rb = st["last_transition"]
        assert rb["source"] == "rollback" and rb["rollback_of"]["to"] == "disco"
        assert "screens.north-screen" in rb["rollback_of"]["detail"]
        assert (st["current"], st["target"], st["transition"]) == ("normal", None, None)
        at = {k: i for i, (s, d, _) in enumerate(r.log) for k in [(s, json.dumps(d, sort_keys=True))]}
        dmx_on, dmx_off = at[("plug", '{"dmx_power": "on"}')], at[("plug", '{"dmx_power": "off"}')]
        raise_blinds = at[("blinds", '{"ch02": 0, "ch03": 80}')]
        assert dmx_on < dmx_off < raise_blinds
        assert privacy(r) == ORIGINAL and r.ball.sent == [{"main": "stopped"}]   # the ball step never ran
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_once()
        assert r.blinds.value["ch03"] == 0 and r.c.s["drift"] == {}          # normal's ignore, never disco's enforce
    asyncio.run(body())


def test_f5_interrupted_or_repeated_entry_fires_no_exit_signal(tmp_path):
    """P4, E8, E22, probes::test_s4: normal -> disco interrupted -> normal fires no exit signal of a mode the room
    never reached; and disco requested again while the first is still running (a double click) carries it on, so
    live's on_enter fires once and nothing fires on the way."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        await go(r, "normal")
        assert signals_sent(r, "show over") == signals_sent(r, "show starting") == 0
        first = await r.c.request_mode("disco")
        await until(lambda: len(sent(r, "blinds")) == 3)
        tr = await go(r, "disco")
        assert tr["resumes"] == first and tr["status"] == "ok"
        assert signals_sent(r, "show starting") == 1 and signals_sent(r, "show over") == 0
        assert r.c.s["mode"]["entered"] == ["house", "live", "disco"]
    asyncio.run(body())


def test_f6_f18_mask_first_and_dmx_power_only_after_the_blinds(tmp_path):
    """P13, E12 (I-F10): the mask is suspended before anything else moves, and DMX power, a step of its own, goes on
    only once both blinds read back down (a stale hub cache, so that takes their full travel). On the way out DMX
    power goes off before any blind rises, and the mask follows again only after the screens are back on art."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s, r.blinds.stale_until_stop = 0.05, True
        r.log.clear()
        await go(r, "disco")
        o = order(r)
        assert o.index("sensor_mask") < min(o.index(s) for s in ("blinds", "tv", "plug", "screens", "mirror_ball"))
        dmx_on = next(t for s, d, t in r.log if s == "plug")
        assert max(oks(r, "blinds.ch02") + oks(r, "blinds.ch03")) <= dmx_on
        r.log.clear()
        await go(r, "normal")
        o = order(r)
        assert o.index("plug") < o.index("blinds") and o.index("screens") < o.index("sensor_mask")
    asyncio.run(body())


def test_f7_g9_store_while_the_hub_is_unreachable_is_stale_with_its_old_time(tmp_path):
    """P8, E9, probes::test_offline_store_marks_confirmed: the hub goes offline and ch02 is moved by hand meanwhile. The
    disco store keeps the last good reading as stale, with its old `at`, never a fresh 'confirmed', and status says
    the blinds are unavailable."""
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        at = r.c.s["believed"]["blinds.ch02"]["at"]
        clock.advance(3600)
        r.blinds.offline = True
        r.blinds.hand_set("ch02", 70)
        await go(r, "disco")                                                 # guarded blinds: aborts and rolls back
        e = r.c.s["stored"]["pre_live"]["blinds.ch02"]
        assert (e["value"], e["confidence"], e["at"]) == (0, "stale", at)
        st = r.c.status()
        assert st["subsystems"]["blinds"]["available"] is False
        assert st["stored"]["pre_live"]["blinds.ch02"]["confidence"] == "stale"
    asyncio.run(body())


def test_f8_g13_enforce_reasserts_a_stuck_blind_once_and_a_request_never_waits(tmp_path):
    """E10, E11: in disco ch03 is raised by hand and then can't move (an obstacle, a flat battery). Ten reconcile ticks
    send one re-assert and then report it overridden. Later, a re-assert still travelling is cancelled by a mode
    request, which starts at once."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.blinds.hand_set("ch03", 40)
        r.blinds.fail = {"ch03"}
        n = len(sent(r, "blinds"))
        for _ in range(10):
            await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n + 1 and r.c.s["drift"]["blinds.ch03"]["overridden"]
        r.blinds.fail = set()
        await go(r, "disco")                                                 # re-apply: re-lowers, resets the re-assert
        r.blinds.travel_s = 0.3
        r.blinds.hand_set("ch03", 0)
        await r.c.reconcile_tick("blinds")
        assert len(r.c._rtasks) == 1
        t0 = time.time()
        await r.c.request_mode("normal")
        await until(lambda: started(r)[-1]["to"] == "normal")
        assert phase(r, "started")[-1] and [e["t"] for e in r.events if e["data"].get("phase") == "started"][-1] - t0 < 0.1
        await r.c.wait_idle()
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_f9_crash_after_the_toggle_code_went_out_never_flips_it_back(tmp_path):
    """E7: a toggle ball's code goes out, then room_service dies before the ack is processed. Its belief was saved as
    unknown before the send, so the resumed transition never sends the code again (that would stop the ball): the
    row is unconfirmed and the transition partial until the owner says what it is doing."""
    async def body():
        r = make_room(tmp_path, ball_kind="edge_toggle")
        await boot(r)
        r.c.assume("mirror_ball.main", "stopped", cause="owner")
        send, gate = r.ball.apply, asyncio.Event()

        async def ack_lost(desired, opts=None):
            out = await send(desired, opts)
            await gate.wait()
            return out
        r.ball.apply = ack_lost
        await r.c.request_mode("disco")
        await until(lambda: r.ball.physical["main"] == "spinning")
        await hard_kill(r.c)
        assert r.c.file.load()["believed"]["mirror_ball.main"]["confidence"] == "unknown"
        r.ball.apply = send
        r.c = restart(r)
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        tr = r.c.s["last_transition"]
        assert r.ball.physical["main"] == "spinning" and len(r.ball.sent) == 1
        assert (row(tr, "mirror_ball.main")["status"], tr["status"]) == ("unconfirmed", "partial")
        r.c.assume("mirror_ball.main", "spinning", cause="owner: it is spinning")
        await go(r, "normal")
        assert r.ball.physical["main"] == "stopped"
    asyncio.run(body())


def test_f10_config_without_the_current_mode_does_not_wedge_the_room(tmp_path):
    """E5: the mode the room is in disappears from modes.json (renamed; deleting disco would orphan house's Sonos
    retrieve, which no longer loads) and room_service restarts. Reconcile doesn't raise, the room boots into the
    default under the new config, and later requests complete."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        r.cfg = RoomConfig(json.loads(json.dumps(example()).replace('"disco"', '"party"')), r.every)
        r.c = restart(r)
        await r.c.reconcile_once()
        await r.c.start(reconcile=False)
        await r.c.wait_idle()
        assert r.c.s["last_transition"]["source"] == "boot" and r.c.s["mode"]["current"] == "normal"
        assert privacy(r) == ORIGINAL and r.ball.physical["main"] == "stopped" and r.c.s["transition"] is None
        await r.c.reconcile_once()
        assert (await go(r, "party"))["status"] == "ok" and (await go(r, "normal"))["status"] == "ok"
    asyncio.run(body())


def test_f11_hung_screens_apply_times_out_and_frees_the_room(tmp_path):
    """P11 (I-F8): the screens controller never answers. The step ends unconfirmed on a timeout instead of holding the
    transition for ever; the show goes on (screens aren't guarded), and reconcile's retry finishes the job."""
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock, margin_s=0.05)
        await boot(r)
        r.screens.hang_apply = True
        tr = await go(r, "disco")
        north = row(tr, "screens.north-screen")
        assert (north["status"], north["detail"], tr["status"]) == ("unconfirmed", "timeout", "partial")
        assert r.c.s["transition"] is None and r.ball.physical["main"] == "spinning"
        r.screens.hang_apply = False
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert r.screens.value["north-screen"] != "art" and r.c.s["desired"]["screens.north-screen"]["settled"]
    asyncio.run(body())


def test_f12_missed_retrieve_retried_until_a_hand_move_is_accepted(tmp_path):
    """E18: leaving disco, ch03 never moves (held with the remote). Its slot is closed and reconcile retries it with
    backoff. The owner then sets it to 50: a reading none of our commands explains, so the retry stops and the slot
    goes (never fought). A later hand move to 20 is what the next episode stores and restores."""
    async def body():
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        r.blinds.fail = {"ch03"}
        tr = await go(r, "normal")
        assert tr["status"] == "partial" and r.c.s["stored"]["pre_live"]["blinds.ch03"]["closed"]
        n = len(sent(r, "blinds"))
        clock.advance(BACKOFF[0])
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n + 1 and r.c.s["desired"]["blinds.ch03"]["retry"]["attempts"] == 1
        r.blinds.fail = set()
        r.blinds.hand_set("ch03", 50)
        clock.advance(BACKOFF[1])
        await r.c.reconcile_once()
        assert len(sent(r, "blinds")) == n + 1 and r.c.s["desired"]["blinds.ch03"]["settled"]
        assert "blinds.ch03" not in stored(r) and r.blinds.value["ch03"] == 50
        r.blinds.hand_set("ch03", 20)
        await go(r, "disco")
        assert stored(r)["blinds.ch03"] == 20
        await go(r, "normal")
        assert r.blinds.value["ch03"] == 20
    asyncio.run(body())


def test_f13_retrieve_that_needs_nothing_still_clears_its_slot(tmp_path):
    """P1, E1, probes::test_s7: ch14 is already down, so disco stores 100 and normal's retrieve sends nothing. That
    retrieve still clears its slot, so after the owner raises ch14 and turns the volume up in normal, the next episode
    stores the new values and ends on them, not on the previous episode's."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        await go(r, "normal")
        assert stored(r) == {} and not any("ch14" in d for d in sent(r, "blinds"))
        r.blinds.hand_set("ch14", 0)
        r.sonos.hand_set("lounge.volume", 40)
        await go(r, "disco")
        assert (stored(r)["blinds.ch14"], stored(r)["sonos.lounge.volume"]) == (0, 40)
        await go(r, "normal")
        assert (r.blinds.value["ch14"], r.sonos.value["lounge.volume"]) == (0, 40)
    asyncio.run(body())


def test_f14_only_a_guarded_target_can_abort(tmp_path):
    """E19: a TV whose readback never comes (a slow wake) is unconfirmed but unguarded, so the show goes on as
    partial. A privacy blind that never arrives is guarded: the entry aborts and rolls back before DMX power (G7's
    "never abort on unconfirmed" is rejected in §1.3)."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.tv.fail = {"south-screen"}
        tr = await go(r, "disco")
        assert tr["status"] == "partial" and row(tr, "tv.south-screen")["status"] == "unconfirmed"
        assert r.c.s["mode"]["current"] == "disco" and privacy(r) == DOWN and r.screens.value["north-screen"] != "art"
        await go(r, "normal")
        r.tv.fail, r.blinds.fail = set(), {"ch03"}
        await go(r, "disco")
        rb = r.c.s["last_transition"]
        assert rb["source"] == "rollback" and rb["rollback_of"]["detail"] == "stopped at step 2: blinds.ch03 not ok"
        assert r.c.s["mode"]["current"] == "normal" and r.plug.value["dmx_power"] == "off" and privacy(r) == ORIGINAL
    asyncio.run(body())


def test_f15_live_v1_per_member_map_is_rejected_at_load(tmp_path):
    """P6 (I-F4): a LIVE_V1-style map under "screens" ({"north-screen": "art", ...}) used to load as that whole map
    being the value of every screen. normalize() runs on every value at load (the per-member sugar is gone, R23), so
    the screens adapter rejects it with a pointer to the entry."""
    a = fake_adapters()

    def normalize(member, value):                                        # the shape normalize_desired accepts
        if value == "art" or (isinstance(value, dict) and set(value) == {"live"}):
            return value
        raise ValueError(f"not a screen value: {value!r}")
    a.screens.normalize = normalize
    v1 = {"screens": {"north-screen": "art", "south-screen": "art", "devtest": "art"}}
    with pytest.raises(ConfigError, match=r"^/modes/normal/state/screens: screens\.[a-z-]+: not a screen value"):
        RoomConfig(example(lambda x: x["modes"]["normal"].__setitem__("state", v1)), vars(a).values())
    assert RoomConfig(example(), vars(a).values()).modes["disco"].state["screens.devtest"].set == "art"


def test_f17_unknown_toggle_is_never_sent_blind(tmp_path):
    """E15: a toggle ball in an unknown state (first boot): no code is sent, so it can't end up spinning in normal; the
    row is unconfirmed, the transition partial, and reconcile never retries it."""
    async def body():
        r = make_room(tmp_path, ball_kind="edge_toggle")
        await boot(r)
        tr = await go(r, "disco")
        ball = row(tr, "mirror_ball.main")
        assert (ball["status"], ball["detail"], tr["status"]) == ("unconfirmed", "toggle state unknown; not sent", "partial")
        await r.c.reconcile_once()
        assert r.ball.sent == [] and r.ball.physical["main"] == "stopped"
    asyncio.run(body())


def test_f19_hard_kill_mid_entry_finalises_the_running_record_on_resume(tmp_path):
    """E20: killed while the screens are entering (blinds down, slots stored), so the file still says 'running'. The
    restart finalises that record as cancelled, resumes toward disco under a new id, stores nothing again, fires live's
    on_enter once, and the next normal restores the originals."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.screens.travel_s = 0.2
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "screens"))
        a, slots = r.c.s["transition"]["id"], copy.deepcopy(r.c.s["stored"])
        await hard_kill(r.c)
        assert json.loads(r.path.read_text())["transition"]["status"] == "running"
        r.c = restart(r)
        await r.c.start(reconcile=False)
        assert (r.c.s["last_transition"]["id"], r.c.s["last_transition"]["status"]) == (a, "cancelled")
        assert {"id": a, "phase": "cancelled"} in [e["data"] for e in r.events]
        await r.c.wait_idle()
        assert r.c.s["mode"]["current"] == "disco" and r.c.s["stored"] == slots
        assert signals_sent(r, "show starting") == 1 and r.ball.sent.count({"main": "spinning"}) == 1
        await go(r, "normal")
        assert privacy(r) == ORIGINAL and r.tv.value == {"north-screen": "hdmi", "south-screen": "art_mode"}
    asyncio.run(body())


SLICE = {"default": "gallery",
         "modes": {"gallery": {"state": {"screens.devtest": "art"}},
                   "live-test": {"state": {"screens.devtest": LIVE}}},
         "transitions": [{"from": "*", "to": "*", "steps": ["rest"]}]}


@pytest.mark.parametrize("when, path", [("before_go", ["entry", "abort"]), ("after_go", ["entry", "go", "live", "exit"])])
def test_f20_g14_interrupted_screen_entry_never_stays_live(tmp_path, when, path):
    """P14 (I-F12): the slice's gallery -> live-test on devtest, changed back to gallery mid-handshake. The controller
    carries on after the cancel and read() says moving, so "art" is sent: before go it aborts the entry unseen; after
    go the entry completes and then exits. Never live on glass in gallery."""
    async def body():
        screens = FakeScreens(["devtest"], go_after_s=0.1 if when == "before_go" else 0.01,
                              entry_s=0.1 if when == "after_go" else 0.01)
        c = Coordinator(RoomConfig(SLICE, [screens]), [screens], tmp_path / "state.json")
        await c.start(reconcile=False)
        await c.wait_idle()
        await c.request_mode("live-test")
        await until(lambda: ("devtest", "entry" if when == "before_go" else "go") in screens.events)
        await c.request_mode("gallery")
        await c.wait_idle()
        assert c.s["mode"]["current"] == "gallery" and c.s["last_transition"]["status"] == "ok"
        assert [e for _, e in screens.events] == path and screens.value["devtest"] == "art"
    asyncio.run(body())


def test_f23_g12_same_mode_request_fires_nothing_and_playback_is_state(tmp_path):
    """P5, E2, probes::test_s11: disco -> disco used to fire on_exit then on_enter again (a Sonos pause, then play).
    A re-apply fires no signals and stores nothing, and since playback is state that already matches, Sonos gets
    nothing; leaving the episode puts the pre-disco playback back as state, with no pause or play signal ever."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        await go(r, "disco")
        n_sig, n_sonos, slots = len(r.signals.sent), len(sent(r, "sonos")), copy.deepcopy(r.c.s["stored"])
        tr = await go(r, "disco")
        assert tr["reapply"] and len(r.signals.sent) == n_sig and r.c.s["stored"] == slots
        assert len(sent(r, "sonos")) == n_sonos and r.sonos.value["lounge.playback"] == {"favourite": "Disco"}
        await go(r, "normal")
        assert r.sonos.value == {"lounge.volume": 25, "lounge.playback": RADIO} and r.sonos.signals == []
    asyncio.run(body())


# --- G: tracer-generality/probes -------------------------------------------------------------------------------------------

def test_g1_signals_fire_at_each_family_edge_without_being_copied(tmp_path):
    """probes2::test_on_enter_not_inherited: disco2 extends disco. Signals aren't copied into the child, yet entering
    disco2 fires live's and disco's on_enter once each, leaving disco's family fires disco's on_exit (but not live's,
    on a hop), and leaving the live family fires live's on_exit once."""
    async def body():
        def patch(x):
            x["modes"]["disco"]["on_enter"] = [{"signal": "log.info", "args": {"msg": "disco on"}}]
            x["modes"]["disco"]["on_exit"] = [{"signal": "log.info", "args": {"msg": "disco off"}}]
            x["modes"]["disco2"] = {"label": "Disco 2", "extends": "disco"}
        r = make_room(tmp_path, example(patch))
        assert r.cfg.modes["disco2"].on_enter == []
        await boot(r)
        await go(r, "disco2")
        assert signals_sent(r, "show starting") == signals_sent(r, "disco on") == 1
        await go(r, "disco")                                                 # still inside disco's family
        assert signals_sent(r, "disco on") == 1 and signals_sent(r, "disco off") == 0
        await go(r, "ambient")
        assert signals_sent(r, "disco off") == 1 and signals_sent(r, "show over") == 0
        await go(r, "normal")
        assert signals_sent(r, "show over") == 1 and signals_sent(r, "show starting") == 1
    asyncio.run(body())


def test_g2_plain_value_over_a_store_keeps_the_store(tmp_path):
    """probes::test_extends_bare_value_drops_store: a child that writes a plain value over live's store+set entry
    replaces only the value (merge rule 1), so the blinds are still stored first and come back afterwards."""
    async def body():
        r = make_room(tmp_path, example(lambda x: x["modes"].__setitem__(
            "dim", {"label": "Dim", "extends": "live", "state": {"privacy_blinds": 50}})))
        e = r.cfg.modes["dim"].state["blinds.ch02"]
        assert (e.store, e.set, e.policy) == ("pre_live", 50, "enforce")
        await boot(r)
        await go(r, "dim")
        assert privacy(r) == {"ch02": 50, "ch03": 50, "ch14": 50} and stored(r)["blinds.ch03"] == 80
        await go(r, "normal")
        assert privacy(r) == ORIGINAL
    asyncio.run(body())


def test_g4_long_episode_never_captures_lowered_and_max_age_applies_at_retrieve(tmp_path):
    """probes::test_store_max_age_overwrites_original_mid_episode, E13: max_age_s on a store entry is a ConfigError.
    With it on house's retrieve (12 h), a 13 h disco followed by an ambient hop keeps the original slot entry, and the
    return to normal finds it too old: else 'leave', so the blinds stay down and the entry goes."""
    async def body():
        with pytest.raises(ConfigError, match="max_age_s is allowed on retrieve only"):
            RoomConfig(example(lambda x: x["modes"]["live"]["state"]["privacy_blinds"].__setitem__("max_age_s", 3600)),
                       adapters())
        clock = Clock()
        r = make_room(tmp_path, now=clock)
        await boot(r)
        await go(r, "disco")
        entry = copy.deepcopy(r.c.s["stored"]["pre_live"]["blinds.ch02"])
        clock.advance(13 * 3600)
        await go(r, "ambient")
        assert r.c.s["stored"]["pre_live"]["blinds.ch02"] == entry          # not re-stored as "lowered"
        tr = await go(r, "normal")
        assert tr["status"] == "ok" and privacy(r) == DOWN and stored(r) == {}
        assert r.tv.value["south-screen"] == "art_mode"                      # no max_age on the TVs: restored
    asyncio.run(body())


def test_g5_subsystems_without_an_adapter_become_placeholders(tmp_path):
    """P7, probes::test_missing_adapter_is_a_config_error (I-F7): no mirror_ball adapter (the RM4 Pro is offline) and
    Hue named before it exists: the config loads, their rows are skipped 'no adapter' without making the show
    partial, and status reports them unavailable."""
    async def body():
        def patch(x):
            x["modes"]["house"]["state"]["hue.lounge"] = {"retrieve": "pre_live"}
            x["modes"]["live"]["state"]["hue.lounge"] = {"store": "pre_live", "set": "off"}
        a = fake_adapters()                     # the RM4 Pro is offline: its device config still lists the member (§5.3)
        a.ball = Placeholder("mirror_ball", ["main"], ["spinning", "stopped"])
        r = make_room(tmp_path, example(patch), adapters=a)
        await boot(r)
        tr = await go(r, "disco")
        for t in ("mirror_ball.main", "hue.lounge"):
            assert (row(tr, t)["status"], row(tr, t)["detail"]) == ("skipped", "no adapter")
        assert tr["status"] == "ok"
        st = r.c.status()["subsystems"]
        assert st["mirror_ball"]["available"] is False and st["hue"]["available"] is False
    asyncio.run(body())


def test_g6_rows_are_written_at_the_start_in_the_live_v1_shape(tmp_path):
    """P10 (I-F6): while the blinds still travel, the running transition already holds every row, each with a stable
    id and LIVE_V1's subsystem, action, status and detail; rows are patched in place and the record keeps them."""
    async def body():
        r = make_room(tmp_path)
        await boot(r)
        r.blinds.travel_s = 0.1
        await r.c.request_mode("disco")
        await until(lambda: sent(r, "blinds"))
        rows = copy.deepcopy(r.c.s["transition"]["steps"])
        assert all({"id", "subsystem", "action", "status", "detail", "step", "kind"} <= set(x) for x in rows)
        by = {x["subsystem"]: x for x in rows}
        assert by["blinds.ch02"]["status"] == "running" and by["mirror_ball.main"]["status"] == "pending"
        assert by["blinds.ch02"]["action"] == "store pre_live, set 100" and by["blinds.ch02"]["guarded"]
        await r.c.wait_idle()
        done = r.c.s["last_transition"]["steps"]
        assert [(x["id"], x["subsystem"], x["action"]) for x in done] == [(x["id"], x["subsystem"], x["action"]) for x in rows]
        assert {x["status"] for x in done} == {"ok"}
    asyncio.run(body())


def test_g10_specificity_beats_key_order_and_prefixes_expand_at_dots():
    """probes::test_key_order_precedence, ::test_dotted_prefix_selector: the exact target beats the subsystem prefix
    whatever the JSON key order; "sonos.lounge" is a prefix of both Sonos members, and "sonos.lou" names nothing."""
    live = {"live": {"scene": "x"}}
    for state in ({"screens.devtest": live, "screens": "art"}, {"screens": "art", "screens.devtest": live}):
        normal = RoomConfig(example(lambda x: x["modes"]["normal"].__setitem__("state", state)), adapters()).modes["normal"]
        assert normal.state["screens.devtest"].set == live and normal.state["screens.north-screen"].set == "art"
    house = RoomConfig(example(), adapters()).modes["house"]
    assert {t for t in house.state if t.startswith("sonos.")} == {"sonos.lounge.volume", "sonos.lounge.playback"}
    with pytest.raises(ConfigError, match="'sonos.lou' matches no target"):
        RoomConfig(example(lambda x: x["modes"]["normal"].__setitem__("state", {"sonos.lou": 30})), adapters())
