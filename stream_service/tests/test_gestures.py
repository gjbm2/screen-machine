"""The gesture layer (docs/ENSEMBLE.md §8.2; gestures.py, plugins/effects/gesture_accents.py): timing on the glass
(the lead, a late part eased, a part more than a beat late dropped), the screen's own parts only, and the slot kept
last on the bus.  Host side only (no GPU)."""
from __future__ import annotations

import math
import types

import numpy as np
import pytest

from stream_service import gestures
from stream_service.effects import EffectChain, EffectSlot
from stream_service.fx_director import bus_slots
from stream_service.gestures import GestureBook, schedule, weight

SHAPE = {"attack_ms": 100.0, "hold_ms": 50.0, "decay_ms": 200.0, "depth": 1.0, "colour_depth": 0.5, "kick": 0.0}
ENS = {"ensemble": {"screens": {"south-screen": "screen.south", "north-screen": "screen.north"},
                    "players": {"screen.south": {}, "screen.north": {}}, "next": None}}


def gesture(gid: str, at_s: dict, kind: str = "accent", **kw) -> dict:
    return {"id": gid, "seq": 1, "t_ms": 0.0, "kind": kind, "source": "clock", "form": "echo", "strength": 1.0,
            "shape": SHAPE, "dur_ms": kw.get("dur_ms", 0.0), "register": kw.get("register"), "pitches": [],
            "beat": None, "why": "test",
            "parts": [{"player": p, "at_ms": t * 1000.0, "strength": 1.0, "gen": i, "predicted": True, "colour": None}
                      for i, (p, t) in enumerate(at_s.items())]}


# -- timing -----------------------------------------------------------------------------------------------------------------
def test_schedule_peaks_on_the_glass_at_at_ms():
    # rendered at now, shown lead later: the peak is rendered at at - lead, the rise starts an attack before
    assert schedule(10.0, now=9.0, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.06) == pytest.approx((9.6, 9.7))


def test_schedule_inside_the_lead_starts_at_once_with_the_attack_squeezed():
    # the start (9.6) has passed but the peak (9.7) is still ahead: at once, same peak
    assert schedule(10.0, now=9.65, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.02) == pytest.approx((9.65, 9.7))
    # the peak is past (glass 0.2 s late), within a beat: at once, rising over the eased attack
    assert schedule(10.0, now=9.9, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.06) == pytest.approx((9.9, 9.96))


def test_schedule_more_than_a_beat_late_is_dropped():
    assert schedule(10.0, now=10.5, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.06) is None
    assert schedule(10.0, now=10.1, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.06) is not None


def test_schedule_swell_rises_over_its_dur_from_at():
    assert schedule(10.0, now=5.0, lead=0.3, attack_s=0.1, beat_s=0.5, ease_s=0.06, dur_s=2.0) == \
        pytest.approx((9.7, 11.7))


def test_weight_rises_holds_decays_and_ends():
    assert weight(9.59, 9.6, 9.7, 0.05, 0.2) == 0.0
    assert 0.0 < weight(9.65, 9.6, 9.7, 0.05, 0.2) < 1.0
    assert weight(9.72, 9.6, 9.7, 0.05, 0.2) == 1.0
    assert weight(9.95, 9.6, 9.7, 0.05, 0.2) == pytest.approx(math.exp(-1.0), rel=1e-3)
    assert weight(9.75 + 8.0 * 0.2 + 0.01, 9.6, 9.7, 0.05, 0.2) == 0.0


# -- the book: the screen's own parts -----------------------------------------------------------------------------------------
def test_book_keeps_only_this_screens_player_and_nothing_without_the_mapping():
    b = GestureBook()
    b.on_gesture(gesture("g:1", {"screen.south": 10.0, "screen.north": 10.5}), t=9.0)
    assert b.parts("south-screen")[1] == []                     # no ensemble yet: nothing shows
    b.on_direction(ENS)
    south = b.parts("south-screen")[1]
    assert [p["player"] for _, _, p in south] == ["screen.south"]
    assert [p["at_ms"] for _, _, p in b.parts("north-screen")[1]] == [10500.0]
    assert b.parts("devtest")[1] == []                          # a screen the ensemble doesn't name
    assert b.wants_slot("south-screen", 9.5, keep_s=30.0) and not b.wants_slot("devtest", 9.5, keep_s=30.0)
    assert not b.wants_slot("south-screen", 100.0, keep_s=30.0)  # nothing for it lately
    b.on_direction({"vibe": None})                              # unison again: no mapping
    assert b.parts("south-screen")[1] == [] and not b.wants_slot("south-screen", 9.5, keep_s=30.0)


def test_book_resend_replaces_and_empty_parts_call_off():
    b = GestureBook()
    b.on_direction(ENS)
    b.on_gesture(gesture("g:1", {"screen.south": 10.0}), t=9.0)
    b.on_gesture(gesture("g:1", {"screen.south": 10.25}), t=9.1)
    assert [p["at_ms"] for _, _, p in b.parts("south-screen")[1]] == [10250.0]
    b.on_gesture({**gesture("g:1", {}), "parts": []}, t=9.2)
    assert b.parts("south-screen")[1] == [] and b.counts["called_off"] == 1


# -- the effect's host side: scheduling through the book --------------------------------------------------------------------
def accents(monkeypatch, screen: str):
    from stream_service.plugins.effects.gesture_accents import MAXR, RW, GestureAccents
    book = GestureBook()
    monkeypatch.setattr(gestures, "BOOK", book)
    e = GestureAccents()
    e.ctx = types.SimpleNamespace(screen=screen, width=1920, height=1080)
    e._parts, e._rev, e.dropped = {}, -1, 0
    e.rows_h = np.zeros((MAXR, RW), np.float32)
    return e, book


CFG = {"late_attack_ms": 60, "no_grid_beat_s": 0.5, "own_colour": 0.6, "max_accents": 24,
       "kinds": {"accent": {"look": "flash", "level": 0.2}, "note": {"look": "ring", "level": 0.5, "size": 0.2,
                                                                     "grow_ms": 400, "width": 0.01, "x": "register"}}}


def test_effect_takes_its_own_parts_on_time_eased_or_dropped(monkeypatch):
    e, book = accents(monkeypatch, "south-screen")
    book.on_direction(ENS)
    book.on_gesture(gesture("g:on-time", {"screen.south": 12.0, "screen.north": 12.0}), t=9.0)
    book.on_gesture(gesture("g:eased", {"screen.south": 10.2}), t=9.0)
    book.on_gesture(gesture("g:late", {"screen.south": 9.0}), t=9.0)
    book.on_gesture(gesture("g:north", {"screen.north": 10.5}), t=9.0)
    e._take(10.0, 0.3, CFG, None)                               # glass now = 10.3
    assert set(e._parts) == {("g:on-time", 0), ("g:eased", 0)}  # north's parts and the late one are not here
    assert e.dropped == 1
    _, start, accs = e._parts[("g:on-time", 0)]
    assert start == pytest.approx(12.0 - 0.3 - 0.1) and accs[0].peak == pytest.approx(11.7)
    _, start, accs = e._parts[("g:eased", 0)]
    assert start == pytest.approx(10.0) and accs[0].peak == pytest.approx(10.06)
    n, E, K, S, sx, sy = e._pack(10.06, CFG)                    # the eased accent at its peak: light added only
    assert n == 0 and E[:3].min() > 0.0 and E.min() >= 0.0 and sx == sy == 0.0


def test_effect_rings_by_register_and_call_off_before_start(monkeypatch):
    e, book = accents(monkeypatch, "north-screen")
    book.on_direction(ENS)
    g = gesture("g:piano:1", {"screen.north": 10.5}, kind="note", register=0.9)
    book.on_gesture(g, t=10.0)
    e._take(10.0, 0.2, CFG, None)
    n, *_ = e._pack(10.31, CFG)
    assert n == 1 and e.rows_h[0, 0] > 1920 / 1080 * 0.5        # a high note sits right of centre
    book.on_gesture({**g, "parts": []}, t=10.05)                # called off: it has begun, so it plays out
    e._take(10.32, 0.2, CFG, None)
    assert ("g:piano:1", 0) in e._parts
    book.on_gesture(gesture("g:piano:2", {"screen.north": 12.0}, kind="note", register=0.1), t=10.4)
    e._take(10.4, 0.2, CFG, None)
    book.on_gesture({**gesture("g:piano:2", {}), "parts": []}, t=10.5)
    e._take(10.5, 0.2, CFG, None)                               # called off before it began: gone
    assert ("g:piano:2", 0) not in e._parts


# -- placement -------------------------------------------------------------------------------------------------------------
def test_gesture_slot_is_last_on_the_bus_and_invisible_to_the_fx_director():
    def slot(sid, stage, where="bus"):
        return EffectSlot(sid, types.SimpleNamespace(step_params=lambda dt: {}), name=sid, stage=stage, where=where)
    chain = EffectChain()
    chain.add(slot("g", "overlay", "gesture"))
    chain.add(slot("a", "overlay"))
    chain.add(slot("b", "warp"))
    assert [s.slot_id for s in chain.slots] == ["b", "a", "g"]
    st = [types.SimpleNamespace(id="x", origin="auto"), types.SimpleNamespace(id="gesture", origin="gesture")]
    assert [s.id for s in bus_slots(st)] == ["x"]


# -- the slot through a live session (fake engine) -------------------------------------------------------------------------
from .test_cue import live  # noqa: E402
from .test_fx_api import client, get, put  # noqa: E402,F401


async def test_sync_slots_puts_it_on_keeps_it_through_a_put_and_takes_it_off(client, monkeypatch):
    import asyncio
    import time

    from stream_service.plugins.effects.gesture_accents import GestureAccents
    sid, s, _ = await live(client)
    s.mgr.registry.register(GestureAccents)
    book = GestureBook()
    monkeypatch.setattr(gestures, "BOOK", book)
    book.on_direction({"ensemble": {"screens": {s.screen: "screen.x"}, "players": {"screen.x": {}}}})
    now = time.monotonic()
    book.on_gesture(gesture("g:1", {"screen.x": now + 1.0}), t=now)

    async def synced(want: bool):
        for _ in range(50):                               # the app's own ticker syncs too: wait for either
            gestures.sync_slots(s.mgr, time.monotonic())
            t = gestures._slots[s]["task"]
            if t is not None:
                await t
            if any(st.origin == "gesture" for st in s.fx.stack) == want:
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f"gesture slot never {'on' if want else 'off'}")

    await synced(True)
    st = s.fx.stack[-1]
    assert (st.id, st.origin, st.spec.effect, st.engine_slot.where) == ("gesture", "gesture", "gesture_accents",
                                                                      "gesture")
    r = await put(client, sid, stack=["hue-drift-slow"])  # a person's PUT keeps it, last
    assert r.status == 200, await r.text()
    assert [x["origin"] for x in (await r.json())["stack"]] == ["manual", "gesture"]
    r = await put(client, sid, stack=(await get(client, sid))["stack"])   # an echoed GET too
    assert r.status == 200, await r.text()
    await s.fx.remove(lambda st: False, 0.0)              # Clear effects: never the gesture slot
    assert [x.origin for x in s.fx.stack] == ["gesture"]
    book.on_direction({})                                 # no ensemble (unison): off
    await synced(False)
    assert s.fx.stack == []
