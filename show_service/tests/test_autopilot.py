"""autopilot.py against a fake room_service: an aiohttp server on an ephemeral 127.0.0.1 port (never :8700) with
status, room/config, PUT /mode (expect, 409 stale), room/targets and the events WS (status and mode.transition).
Time is a fake clock driven one second per tick; nothing touches a device, the real room or the real state dir."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest
from aiohttp import web

from show_service import contract as c
from show_service.autopilot import AP_DEFAULTS, attach, merge_skips
from show_service.situations import NoteTap
from show_service.vban import MidiBatch

PIANO_SKIP = ["drawing-room:blinds", {"drawing-room:tv": {"from": "standby"}}]
MODES = {
    "gallery": {"label": "Normal"},
    "piano": {"label": "Piano", "autopilot": {
        "enter_when": {"piano_playing": {"for_s": 3}}, "from": ["gallery", "lights-only"],
        "leave_when": {"piano_idle": {"for_s": 5}}, "leave_to": "previous", "skip": PIANO_SKIP}},
    "lights-only": {"label": "Lights only"},
    "disco": {"label": "Disco"},
}
AUTO = {"on": True, "quiet_hours": None, "min_dwell_s": 0, "backoff_s": 600,
        "max_switches": {"count": 4, "per_s": 1800, "pause_s": 1800},
        "hold": {"until": {"all": [{"piano_idle": {}}, {"not": {"music_audible": {}}}], "for_s": 5},
                 "max_s": 3600, "correction_s": 30, "suppress_s": 3600},
        "chips": {"switch_s": 45, "suggest_s": 120}}
SIT = {"midi": {"streams": ["room-piano"], "channels": [1], "heard_by": None, "window_s": 5, "min_notes": 3,
                 "min_span_s": 2, "min_pitches": 2},
       "music": {"stream": "room-mic", "dsp_min_gate": 0.3},
       "quiet": {"stream": "room-mic", "gate_below": 0.05}}


class Clock:
    t = 1000.0


class FakeRoom:
    """Just enough of room_service's mode machinery (coordinator.request_mode / _emit / status)."""

    def __init__(self, clock: Clock, modes: dict, autopilot: dict | None, on: str = "on") -> None:
        self.clock = clock
        self.config: dict[str, Any] = {"default": "gallery", "modes": json.loads(json.dumps(modes))}
        if autopilot is not None:
            self.config["autopilot"] = autopilot
        self.revision = "r1"
        self.config_error: str | None = None
        self.current, self.target, self.since = "gallery", None, clock.t - 10_000
        self.transition: dict | None = None
        self.last: dict | None = None
        self.on = on
        self.n = 0
        self.puts: list[dict] = []
        self.target_puts: list[dict] = []
        self.look_patches: list[tuple[str, dict]] = []     # Stage 4: the director's screens palette
        self.look_answer = 200
        self.refuse: tuple[int, str] | None = None     # the next autopilot PUT /mode answers this
        self.answer_delay = 0.0                          # PUT /mode answers this long after the transition started
        self.auto_finish = True
        self.finish_status = "ok"
        self.clients: set[web.WebSocketResponse] = set()
        self.status_held = False                         # room_service coalesces status after its events
        self.url = ""
        self.started_at = "2026-10-16T09:00:00+00:00"   # Stage 5: status.service.started_at (RoomLink.room_boot)
        self.lux = None                                  # README S5.19: () -> the light sensor route's JSON (Flask's)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/api/v1/status", self.h_status)
        app.router.add_get("/api/v1/room/config", self.h_config)
        app.router.add_get("/api/v1/modes", self.h_modes)
        app.router.add_put("/api/v1/mode", self.h_mode)
        app.router.add_put("/api/v1/room/targets", self.h_targets)
        app.router.add_get("/api/v1/events", self.h_events)
        app.router.add_patch("/api/v1/screens/{id}/look", self.h_look)
        app.router.add_get("/api/lightsensor/lightsense", self.h_light)
        self.runner = web.AppRunner(app, shutdown_timeout=0.2)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        assert port not in (6454, 6980, 6981, 6982, 8700, 8701, 8702, 8703)
        self.url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        for ws in list(self.clients):
            await ws.close()
        await self.runner.cleanup()

    def _pub(self, tr: dict | None) -> dict | None:
        if tr is None:
            return None
        out = {k: v for k, v in tr.items() if k != "started_at"}
        out["started_age_s"] = round(self.clock.t - tr["started_at"], 1)
        return out

    def status(self) -> dict:
        running = self.transition if self.transition and self.transition["status"] == "running" else None
        last = self.transition if self.transition and not running else self.last
        return {"service": {"version": "test", "epoch": 1, "started_at": self.started_at},
                "mode": {"current": self.current, "target": self.target, "via": [], "entered": [], "leaving": [],
                         "since_age_s": round(self.clock.t - self.since, 1), "default": "gallery",
                         "transition": self._pub(running), "last_transition": self._pub(last), "modes_error": None},
                "subsystems": {"autopilot": {"kind": "level", "believed": self.on, "members": {"main": {
                    "believed": self.on, "confidence": "confirmed", "age_s": 1.0, "sent_age_s": None}}}},
                "stored": {}, "screens": {}, "config": {"revision": self.revision, "error": self.config_error}}

    async def h_status(self, r: web.Request) -> web.Response:
        return web.json_response(self.status())

    async def h_light(self, r: web.Request) -> web.Response:
        if self.lux is None:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response(self.lux())

    async def h_config(self, r: web.Request) -> web.Response:
        return web.json_response({"config": self.config, "revision": self.revision, "path": "x", "error": None})

    async def h_modes(self, r: web.Request) -> web.Response:
        return web.json_response({"modes": {n: {"label": m.get("label")} for n, m in self.config["modes"].items()}})

    async def h_targets(self, r: web.Request) -> web.Response:
        b = await r.json()
        self.target_puts.append(b)
        self.on = b["values"]["autopilot.main"]
        await self.push()
        return web.json_response({"accepted": list(b["values"])}, status=202)

    async def h_look(self, r: web.Request) -> web.Response:
        self.look_patches.append((r.match_info["id"], await r.json()))
        if self.look_answer != 200:
            return web.json_response({"error": "not_live", "detail": "test"}, status=self.look_answer)
        return web.json_response({"screen": r.match_info["id"], "palette": self.look_patches[-1][1].get("palette")})

    async def h_mode(self, r: web.Request) -> web.Response:
        b = await r.json()
        self.puts.append({**b, "_current": self.current})
        if b.get("source") == c.AUTOPILOT_SOURCE and self.refuse:
            st, code = self.refuse
            self.refuse = None
            return web.json_response({"error": code, "detail": "test"}, status=st)
        st, body = await self.request(b["mode"], b.get("source", "web"), b.get("reason"), b.get("expect"),
                                      b.get("skip"))
        if self.answer_delay:                            # the room's lock and device reads: a slow answer
            await asyncio.sleep(self.answer_delay)
        return web.json_response(body, status=st)

    async def h_events(self, r: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(r)
        await ws.send_json({"type": "status", "data": self.status()})
        self.clients.add(ws)
        try:
            async for _ in ws:
                pass
        finally:
            self.clients.discard(ws)
        return ws

    async def send(self, msg: dict) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def push(self) -> None:
        if not self.status_held:
            await self.send({"type": "status", "data": self.status()})

    async def request(self, mode: str, source: str, reason=None, expect=None, skip=None) -> tuple[int, dict]:
        if mode not in self.config["modes"]:
            return 404, {"error": "unknown_mode", "detail": mode}
        if expect:
            last_id = (self.transition or self.last or {}).get("id")
            if expect.get("current") != self.current or expect.get("last_transition_id") != last_id:
                return 409, {"error": "stale", "detail": "the room moved on"}
        prev = self.transition
        if prev and prev["status"] == "running":
            prev["status"] = "cancelled"
            self.last = prev
            await self.send({"type": "mode.transition", "data": {"id": prev["id"], "phase": "cancelled"}})
        self.n += 1
        tr = {"id": f"t-{self.n}", "from": self.current, "to": mode, "source": source, "status": "running",
              "started_at": self.clock.t, "reason": reason, "skip": skip}
        self.transition, self.target = tr, mode
        await self.send({"type": "mode.transition", "data": {"id": tr["id"], "phase": "started", **{
            k: tr[k] for k in ("from", "to", "source", "reason", "skip")}}})
        await self.push()
        if self.auto_finish:
            await self.finish(self.finish_status)
        return 202, {"transition_id": tr["id"], "reapply": False}

    async def finish(self, status: str = "ok") -> None:
        tr = self.transition
        tr["status"] = status
        if status in ("ok", "partial"):
            self.current, self.since = tr["to"], self.clock.t
        self.target = None
        self.last, self.transition = tr, None
        await self.send({"type": "mode.transition", "data": {"id": tr["id"], "phase": "done", "status": status,
                                                              "detail": ""}})
        await self.push()

    async def person(self, mode: str) -> None:
        await self.request(mode, "web")

    def auto_puts(self) -> list[dict]:
        return [p for p in self.puts if p.get("source") == c.AUTOPILOT_SOURCE]


class H:
    """One show-brain's worth of Auto, against the fake room, on the test's own loop."""

    async def start(self, tmp_path, *, modes=None, auto=AUTO, on="on", local=None, sit=SIT) -> "H":
        self.clock = Clock()
        self.room = FakeRoom(self.clock, modes or MODES, auto, on)
        await self.room.start()
        self.tap = NoteTap()
        self.seq = 0
        self.pitch = 0
        self.playing = False
        self.features_on = True
        self.engaged = False
        self.local = local or datetime(2026, 10, 16, 12, 0)
        self.health = {"active_audio": "room-mic", "streams": {
            "room-piano": {"kind": "midi", "state": "streaming", "test": False},
            "room-mic": {"kind": "audio", "state": "streaming", "test": False}}}
        self.features: dict | None = None
        self.notified: list[tuple[str, dict]] = []
        self.persisted = 0
        ctx = c.BrainContext(publish=lambda m: None, features=lambda: self.features, health=lambda: self.health,
                             notes=self.tap, piano=lambda: None)
        ctx.loop = asyncio.get_running_loop()
        ctx.notify = lambda k, p: self.notified.append((k, p))
        ctx.engaged = lambda: self.engaged
        ctx.persist = self._persist
        self.ctx = ctx
        self.log_path = tmp_path / "autopilot.jsonl"
        self.link, self.sit, self.ap = attach(ctx, mono=lambda: self.clock.t, wall=lambda: 1.79e9 + self.clock.t,
                                              local=lambda: self.local, log_path=self.log_path)
        for comp in (self.link, self.sit, self.ap):
            for k, v in comp.provides().items():
                if k == "on_mode_change":
                    ctx.on_mode_change.append(v)
                else:
                    setattr(ctx, k, v)
        self.mode_changes: list[tuple] = []
        ctx.on_mode_change.append(lambda old, new: self.mode_changes.append((old, new)))
        settings = {"room": {"url": self.room.url}, "situations": sit}
        for comp in (self.link, self.sit, self.ap):
            comp.configure(settings)
        for comp in (self.link, self.sit, self.ap):
            comp.start()
        await self.until(lambda: self.link.view()["reachable"] and self.link.view()["modes"])
        await self.step()
        return self

    def _persist(self) -> None:
        self.persisted += 1

    async def stop(self) -> None:
        for comp in (self.ap, self.sit, self.link):
            comp.stop()
        await self.room.stop()
        await asyncio.sleep(0.05)

    async def until(self, pred, timeout: float = 5.0) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not pred():
            assert loop.time() < end, "timed out"
            await asyncio.sleep(0.01)

    async def settle(self) -> None:
        for _ in range(3):
            await asyncio.sleep(0.005)
            p = self.ap._pending
            if p is not None and not p.done():
                await asyncio.wait_for(asyncio.shield(p), 5.0)
        await asyncio.sleep(0.02)

    async def step(self, n: int = 1) -> None:
        for _ in range(n):
            self.clock.t += 1.0
            if self.playing:
                self.seq += 1
                self.pitch = (self.pitch + 1) % 5
                self.tap.on_midi("room-piano", MidiBatch(seq=self.seq, ev=[(0, 0x90, 60 + self.pitch, 80)], prev=[],
                                                         sess="s1"), self.clock.t, lambda t: t)
            if self.features_on:
                self.features = {"t_ms": self.clock.t * 1000.0, "stream": "room-mic", "gate": 0.0, "lufs_s": -70.0,
                                 "energy": 0.0}
            self.sit.tick(self.clock.t)
            self.ap.tick(self.clock.t)
            await self.settle()

    async def step_until(self, pred, limit: int = 30) -> int:
        for i in range(limit):
            if pred():
                return i
            await self.step()
        raise AssertionError("never happened")

    def state(self) -> dict:
        return self.ap.snapshot()


def run(tmp_path, body, **kw):
    async def go():
        h = await H().start(tmp_path, **kw)
        try:
            await body(h)
            for p in h.room.puts:                        # never re-requests the current mode
                assert p["mode"] != p["_current"], p
        finally:
            await h.stop()
    asyncio.run(go())


async def enter_piano(h: H) -> None:
    h.playing = True
    await h.step_until(lambda: h.room.current == "piano")
    await h.settle()
    assert h.state()["state"] == "watching"


# -- the flow -------------------------------------------------------------------------------------------------------------
def test_piano_starts_auto_switches_after_for_s_with_expect_and_skip(tmp_path):
    async def body(h: H):
        assert h.state()["state"] == "watching" and h.state()["line"] == "Auto · watching"
        h.playing = True
        await h.step(4)                                  # playing from t+3 (3 notes, 2 s span); held from t+6
        assert h.room.auto_puts() == []
        await h.step_until(lambda: h.room.auto_puts())
        put = h.room.auto_puts()[0]
        assert put["mode"] == "piano" and put["source"] == "autopilot" and put["skip"] == PIANO_SKIP
        assert put["expect"] == {"current": "gallery", "last_transition_id": None}
        assert put["reason"] == "the piano is playing (piano/enter_when)"
        await h.settle()
        s = h.state()
        assert h.room.current == "piano" and s["state"] == "watching"
        assert s["entered"]["mode"] == "piano" and s["entered"]["previous"] == "gallery" and s["entered"]["by"] == "auto"
        assert s["chip"]["kind"] == "switch" and s["chip"]["undo"] is True
        assert s["chip"]["text"] == "Auto → Piano · the piano is playing"
        assert any(k == "auto" and p["kind"] == "switch" for k, p in h.notified)
        assert s["last"]["status"] == "done" and s["last"]["to"] == "piano"
        lines = [json.loads(x) for x in h.log_path.read_text().splitlines()]
        sw = [x for x in lines if x["kind"] == "switch"]
        assert sw and sw[0]["evidence"]["piano_playing"]["value"] is True and sw[0]["mode"] == "piano"
        assert sw[0]["rule"] == "piano/enter_when" and sw[0]["room"]["revision"] == "r1"
        assert ("gallery", "piano") in h.mode_changes
        assert h.persisted > 0
        await h.step(5)                                  # still playing: never asks for piano again
        assert len(h.room.auto_puts()) == 1
    run(tmp_path, body)


def test_leave_to_previous_when_idle_unless_someone_is_engaged(tmp_path):
    async def body(h: H):
        h.room.config["modes"]["piano"]["autopilot"]["from"] = ["gallery", "lights-only"]
        await h.room.person("lights-only")               # Auto off-path: a person picks, Auto holds...
        await h.settle()
        h.ap.resume()                                    # ...until Resume
        await enter_piano(h)
        assert h.state()["entered"]["previous"] == "lights-only"
        h.playing = False
        h.engaged = True                                 # a nudge in the mode: Auto never leaves
        await h.step(10)
        assert [p["mode"] for p in h.room.auto_puts()] == ["piano"]
        assert any(x["kind"] == "leave" and x["blocked"] == "someone is using the controls"
                   for x in h.state()["candidates"])
        h.engaged = False
        await h.step_until(lambda: h.room.current == "lights-only")
        put = h.room.auto_puts()[-1]
        assert put["mode"] == "lights-only" and put["reason"] == "the piano has stopped (piano/leave_when)"
        assert put["skip"] == PIANO_SKIP                 # leaving piano: piano's skip still holds (§8.9: Auto never
                                                         # moves the blinds); lights-only has none of its own
        await h.settle()
        assert h.state()["entered"] is None and h.state()["chip"]["text"] == "Auto → Lights only · the piano has stopped"
    run(tmp_path, body)


def test_auto_off_never_puts(tmp_path):
    async def body(h: H):
        assert h.state()["state"] == "off" and h.state()["line"] == "Auto is off"
        h.playing = True
        await h.step(15)
        assert h.room.puts == []
        st = await h.ap.set_on(True)
        assert h.room.target_puts == [{"values": {"autopilot.main": "on"}, "source": "show"}]
        assert st["on"] is True
        await h.step_until(lambda: h.room.current == "piano")
    run(tmp_path, body, on="off")


def test_default_is_off_when_nothing_says_on(tmp_path):
    async def body(h: H):
        h.room.on = None                                 # an old room with no autopilot target
        h.room.config["autopilot"] = {}
        h.room.revision = "r2"
        await h.room.push()
        await h.until(lambda: h.link.view()["revision"] == "r2" and h.link.view()["autopilot"] == {})
        await h.step()
        s = h.state()
        assert s["state"] == "off" and s["settings"]["on"] == {"value": False, "source": "default"}
        assert s["settings"]["quiet_hours"]["value"] == AP_DEFAULTS["quiet_hours"]
    run(tmp_path, body)


def test_409_stale_stays_watching_without_backoff(tmp_path):
    async def body(h: H):
        h.room.refuse = (409, "stale")
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.settle()
        s = h.state()
        assert s["state"] == "watching" and h.ap.backoff_until is None and h.room.current == "gallery"
        await h.step_until(lambda: h.room.current == "piano", limit=3)      # next tick: asks again
        assert len(h.room.auto_puts()) == 2
    run(tmp_path, body)


def test_404_blocks_the_rule_until_the_config_changes(tmp_path):
    async def body(h: H):
        h.room.refuse = (404, "unknown_mode")
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.step(5)
        assert len(h.room.auto_puts()) == 1
        cand = [x for x in h.state()["candidates"] if x["rule"] == "piano/enter_when"][0]
        assert "room refused" in cand["blocked"]
        h.room.revision = "r2"
        await h.room.push()
        await h.step_until(lambda: h.room.current == "piano")
    run(tmp_path, body)


def test_failed_switch_backs_off_until_resume(tmp_path):
    async def body(h: H):
        h.room.finish_status = "failed"
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.settle()
        s = h.state()
        assert s["state"] == "backoff" and s["chip"]["kind"] == "failed" and s["until_in_s"] == 600
        await h.step(5)
        assert len(h.room.auto_puts()) == 1
        h.room.finish_status = "ok"
        h.ap.resume()
        await h.step_until(lambda: h.room.current == "piano")
    run(tmp_path, body)


def test_a_partial_switch_landed_so_auto_carries_on(tmp_path):
    """21 Oct (owner, 09's audit): gallery -> piano landed but was "partial" (the TVs' hdmi skipped, no adapter; the
    Sonos pause unconfirmed) and Auto backed off 30 min. The mode landed: it's reported, never a backoff."""
    async def body(h: H):
        h.room.finish_status = "partial"
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.settle()
        s = h.state()
        assert h.room.current == "piano" and s["state"] != "backoff" and s["chip"]["kind"] == "switch"
        assert any(e["kind"] == "partial" for e in h.ap._log)
    run(tmp_path, body)


# -- people win -------------------------------------------------------------------------------------------------------------
def test_a_person_s_choice_holds_auto(tmp_path):
    async def body(h: H):
        await h.room.person("lights-only")
        await h.settle()
        s = h.state()
        assert s["state"] == "holding" and s["hold"]["by"] == "person"
        assert s["line"] == "Auto paused · you chose Lights only · Resume"
        h.playing = True                                 # piano allowed from lights-only, but Auto holds
        await h.step(12)
        assert h.room.auto_puts() == []
        h.ap.resume()                                    # Resume: watching, timers restarted
        assert h.state()["state"] == "watching"
        await h.step_until(lambda: h.room.current == "piano")
    run(tmp_path, body)


def test_the_hold_ends_when_the_activity_ends(tmp_path):
    async def body(h: H):
        await h.room.person("disco")
        await h.settle()
        assert h.state()["state"] == "holding"
        await h.step(4)                                  # idle and no music: hold.until needs 5 s from the choice
        assert h.state()["state"] == "holding"
        await h.step_until(lambda: h.state()["state"] == "watching", limit=4)
        assert h.state()["chip"]["kind"] == "resume"
    run(tmp_path, body)


def test_picking_the_mode_auto_would_pick_is_not_an_override(tmp_path):
    async def body(h: H):
        h.room.config["autopilot"] = {**AUTO, "quiet_hours": {"from": "11:00", "to": "13:00"}}
        h.room.revision = "r2"
        await h.room.push()
        await h.until(lambda: h.link.view()["revision"] == "r2")
        h.playing = True
        await h.step(8)
        s = h.state()
        assert s["state"] == "paused" and s["why"] == "quiet_hours" and h.room.auto_puts() == []
        assert any(p["kind"] == "pause" and "quiet hours" in p["text"] for _, p in h.notified)
        await h.room.person("piano")                     # enter_when holds: the person agrees with Auto
        await h.settle()
        s = h.state()
        assert s["hold"] is None and s["entered"]["mode"] == "piano" and s["entered"]["by"] == "person"
        assert s["entered"]["previous"] == "gallery"
        h.playing = False                                # quiet hours: Auto may still leave what it "entered"
        await h.step_until(lambda: h.room.current == "gallery")
        assert h.room.auto_puts()[-1]["mode"] == "gallery"
    run(tmp_path, body)


def test_a_change_within_correction_s_is_a_correction(tmp_path):
    async def body(h: H):
        await enter_piano(h)
        await h.room.person("gallery")                   # within 30 s of Auto's switch
        await h.settle()
        s = h.state()
        assert s["state"] == "holding" and s["hold"]["by"] == "correction"
        assert [x["rule"] for x in s["suppressed"]] == ["piano/enter_when"]
        h.ap.resume()
        await h.step(10)                                 # still playing, but that pair is suppressed
        assert [p["mode"] for p in h.room.auto_puts()] == ["piano"]
        cand = [x for x in h.state()["candidates"] if x["rule"] == "piano/enter_when"][0]
        assert cand["holds"] and cand["blocked"].startswith("suppressed")
        h.playing = False                                # the episode ends: enter_when false for 5 s
        await h.step(10)
        assert h.state()["suppressed"] == []
        h.playing = True
        await h.step_until(lambda: h.room.current == "piano")
    run(tmp_path, body)


def test_undo_goes_back_and_suppresses(tmp_path):
    async def body(h: H):
        with pytest.raises(LookupError):
            await h.ap.undo()                            # nothing yet
        await enter_piano(h)
        out = await h.ap.undo()
        assert out["undone"] is True and out["mode"] == "gallery"
        put = h.room.puts[-1]
        assert put["mode"] == "gallery" and put["source"] == "auto-undo" and put["reason"] == "Undo Auto"
        assert "skip" not in put and put["expect"]["current"] == "piano"
        await h.settle()
        s = h.state()
        assert h.room.current == "gallery" and s["state"] == "holding" and s["hold"]["by"] == "undo"
        assert s["suppressed"][0]["rule"] == "piano/enter_when" and s["chip"]["kind"] == "undo"
        with pytest.raises(LookupError):
            await h.ap.undo()                            # once
        h.ap.resume()
        await h.step(10)
        assert [p["mode"] for p in h.room.auto_puts()] == ["piano"]
    run(tmp_path, body)


def test_flap_guard_pauses_auto(tmp_path):
    async def body(h: H):
        h.room.config["autopilot"] = {**AUTO, "max_switches": {"count": 2, "per_s": 1800, "pause_s": 900}}
        h.room.revision = "r2"
        await h.room.push()
        await h.until(lambda: h.link.view()["revision"] == "r2")
        await enter_piano(h)                             # switch 1
        h.playing = False
        await h.step_until(lambda: h.room.current == "gallery")   # switch 2 (leave)
        h.playing = True
        await h.step_until(lambda: h.state()["state"] == "paused")
        s = h.state()
        assert s["why"] == "flap" and s["line"] == "Auto paused · too many changes · Resume" and s["until_in_s"] == 900
        assert [p["mode"] for p in h.room.auto_puts()] == ["piano", "gallery"]
        await h.step(10)
        assert len(h.room.auto_puts()) == 2
    run(tmp_path, body)


def test_never_requests_the_current_mode(tmp_path):
    async def body(h: H):
        h.ap.resume()
        h.room.current = "piano"                         # already there (a person, with Auto off)
        await h.room.push()
        await h.until(lambda: h.link.view()["current"] == "piano")
        h.playing = True
        await h.step(10)
        assert h.room.auto_puts() == []
        cand = [x for x in h.state()["candidates"] if x["rule"] == "piano/enter_when"][0]
        assert cand["holds"] and cand["blocked"] == "it's the current mode"
        h.ap.entered = {"mode": "piano", "previous": "piano", "rule": "piano/enter_when", "at": 0, "by": "auto"}
        h.playing = False
        await h.step(10)                                 # leave_to resolves to the current mode: nothing
        assert h.room.auto_puts() == []
    run(tmp_path, body)


def test_blind_when_the_room_goes_away(tmp_path):
    async def body(h: H):
        await h.room.stop()
        await h.step(15)
        s = h.state()
        assert s["state"] == "blind" and s["why"] == "room_unreachable" and s["line"] == "Auto · not listening"
        h.room.runner = web.AppRunner(web.Application())  # stop() again at teardown is harmless
        await h.room.runner.setup()
    run(tmp_path, body)


def test_config_error_is_blind(tmp_path):
    async def body(h: H):
        h.room.config_error = "modes.json: line 3"
        await h.room.push()
        await h.until(lambda: h.link.view()["error"])
        await h.step()
        assert h.state()["state"] == "blind" and h.state()["line"] == ("Auto is paused: a mode has a problem; open "
                                                                        "its editor")
    run(tmp_path, body)


def test_suggestion_said_once_per_episode(tmp_path):
    async def body(h: H):
        h.room.config["modes"]["disco"]["autopilot"] = {"otherwise_suggest_when": {"between": {"from": "11:00",
                                                                                                "to": "13:00"}}}
        h.room.revision = "r2"
        await h.room.push()
        await h.until(lambda: h.link.view()["revision"] == "r2")
        await h.step(5)
        says = [p for k, p in h.notified if p["kind"] == "suggest"]
        assert len(says) == 1 and says[0]["text"].startswith("Switch to Disco?") and h.room.auto_puts() == []
        assert h.state()["suggest"]["mode"] == "disco"
        await h.room.person("disco")                     # taking the tile is agreeing, not an override
        await h.settle()
        assert h.state()["hold"] is None and h.state()["entered"]["mode"] == "disco"
    run(tmp_path, body)


def test_bad_settings_take_the_default_and_say_so(tmp_path):
    async def body(h: H):
        h.room.config["autopilot"] = {**AUTO, "min_dwell_s": "five", "hold": {"until": {"nonsense": {}}}}
        h.room.revision = "r2"
        await h.room.push()
        await h.until(lambda: h.link.view()["revision"] == "r2")
        await h.step()
        st = h.state()["settings"]
        assert st["min_dwell_s"] == {"value": 300.0, "source": "default (bad value)"}
        assert st["hold"]["source"] == "default (bad value)" and st["max_switches"]["source"] == "modes.json"
    run(tmp_path, body)


def test_persist_and_restore(tmp_path):
    async def body(h: H):
        await enter_piano(h)
        await h.room.person("gallery")
        await h.settle()
        saved = json.loads(json.dumps(h.ap.persist()))
        assert saved["hold"]["by"] == "correction" and saved["suppressed"][0]["rule"] == "piano/enter_when"
        assert saved["last"]["to"] == "piano" and len(saved["switches"]) == 1
        _, _, ap2 = attach(h.ctx, mono=lambda: h.clock.t, wall=lambda: 1.79e9 + h.clock.t, log_path=None)
        ap2.restore(saved)
        assert ap2.hold["by"] == "correction" and ap2.suppressed and ap2.last["to"] == "piano"
        ap3 = attach(h.ctx, mono=lambda: h.clock.t, wall=lambda: 1.79e9 + h.clock.t + 10 ** 6, log_path=None)[2]
        ap3.restore(saved)                               # much later: holds and suppressions have expired
        assert ap3.hold is None and ap3.suppressed == [] and ap3.switches == []
    run(tmp_path, body)


def test_status_that_lags_the_events_never_doubles_a_request(tmp_path):
    async def body(h: H):
        h.room.status_held = True                        # done arrives; the status saying "piano" comes later
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.step(4)
        assert h.link.view()["current"] == "gallery" and h.room.current == "piano"
        assert len(h.room.auto_puts()) == 1               # no second PUT on the stale view
        assert h.state()["entered"]["mode"] == "piano"     # and Auto still knows it entered piano
        h.room.status_held = False
        await h.room.push()
        await h.step(2)
        assert h.link.view()["current"] == "piano" and h.state()["entered"]["mode"] == "piano"
        assert len(h.room.auto_puts()) == 1
    run(tmp_path, body)


# -- review fixes (16 Oct) --------------------------------------------------------------------------------------------------
def test_merge_skips_one_entry_per_selector():
    a = ["drawing-room:blinds", {"drawing-room:tv": {"from": "standby"}}, {"sonos": {"from": "stopped"}}]
    b = [{"drawing-room:blinds": {"from": "up"}}, {"drawing-room:tv": {"from": ["off"]}}, "mirror_ball"]
    assert merge_skips(a, b) == ["drawing-room:blinds", {"drawing-room:tv": {"from": ["standby", "off"]}},
                                 {"sonos": {"from": "stopped"}}, "mirror_ball"]
    assert merge_skips([], None, ["x"]) == ["x"]


def test_going_straight_back_within_correction_s_is_a_correction_even_when_a_rule_agrees(tmp_path):
    modes = json.loads(json.dumps(MODES))
    modes["gallery"]["autopilot"] = {"otherwise_suggest_when": {"between": {"from": "00:00", "to": "23:59"}}}

    async def body(h: H):
        await enter_piano(h)
        await h.step()
        assert "gallery" in h.ap._would                  # a gallery rule holds now that the room is in piano
        await h.room.person("gallery")                   # straight back, within 30 s of Auto's switch
        await h.settle()
        s = h.state()
        assert s["state"] == "holding" and s["hold"]["by"] == "correction"
        assert [x["rule"] for x in s["suppressed"]] == ["piano/enter_when"]
        h.ap.resume()
        await h.step(10)
        assert [p["mode"] for p in h.room.auto_puts()] == ["piano"]     # no re-entry after min_dwell
    run(tmp_path, body, modes=modes)


def test_a_slow_room_answer_is_adopted_from_its_events(tmp_path):
    """The room's PUT /mode answers after its lock and device reads; Auto's PUT gives up first. The room's own
    started/done events for that request are Auto's switch all the same: announced, entered, counted, undoable."""
    async def body(h: H):
        h.ap.put_timeout_s = 0.2
        h.room.answer_delay = 0.6
        h.playing = True
        await h.step_until(lambda: h.room.auto_puts())
        await h.settle()
        await asyncio.sleep(0.7)                         # the late answer arrives and is ignored
        s = h.state()
        assert h.room.current == "piano" and s["state"] == "watching"
        assert s["entered"]["mode"] == "piano" and s["entered"]["by"] == "auto"
        assert s["chip"]["kind"] == "switch" and s["last"]["status"] == "done"
        assert len(h.ap.switches) == 1
        assert any(e["kind"] == "request" and e.get("adopted") for e in h.ap.log(20))
        # Undo with a slow room: the PUT gives up (502 room_unreachable), the room undoes, Auto adopts it
        with pytest.raises(RuntimeError, match="room_unreachable"):
            await h.ap.undo()
        await h.settle()
        s = h.state()
        assert h.room.current == "gallery" and s["state"] == "holding" and s["hold"]["by"] == "undo"
        assert [x["rule"] for x in s["suppressed"]] == ["piano/enter_when"] and s["chip"]["kind"] == "undo"
    run(tmp_path, body)


def test_roomlink_patch_for_the_screens_palette(tmp_path):
    async def body(h: H) -> None:
        assert h.link.provides()["room_patch"] == h.link.patch
        st, out = await h.link.patch("/api/v1/screens/wall-left/look", {"palette": "ember", "palette_transition_s": 4})
        assert st == 200 and out == {"screen": "wall-left", "palette": "ember"}
        assert h.room.look_patches == [("wall-left", {"palette": "ember", "palette_transition_s": 4})]
        h.room.look_answer = 409
        st, out = await h.link.patch("/api/v1/screens/wall-left/look", {"palette": "ember"})
        assert st == 409 and out["error"] == "not_live"
        st, out = await h.link.patch("/api/v1/nowhere", {"palette": "ember"})
        assert st == 405 or st == 404
        url = h.link.url
        h.link.url = "http://127.0.0.1:9"                      # nothing listens there: never raises
        st, out = await h.link.patch("/api/v1/screens/wall-left/look", {"palette": "ember"}, timeout=2.0)
        assert st == 0 and out["error"] == "room_unreachable"
        h.link.url = url
    run(tmp_path, body)


# -- Stage 5: room events and the room's boot (README S5.8) ----------------------------------------------------------------
def test_roomlink_fans_out_other_room_events_guarded_and_reports_the_boot(tmp_path):
    async def body(h: H) -> None:
        got: list[tuple[str, dict]] = []

        def broken(t: str, d: dict) -> None:
            raise RuntimeError("a broken listener")

        h.ctx.on_room_event.append(broken)                     # guarded: the next one still hears every event
        h.ctx.on_room_event.append(lambda t, d: got.append((t, d)))
        assert h.link.provides()["room_boot"] == h.link.room_boot and h.ctx.room_boot() == h.room.started_at
        assert h.link.snapshot()["boot"] == h.room.started_at
        look = {"screen": "wall-left", "session": "s1", "source": "web", "changed": ["palette"], "palette": "ember"}
        await h.room.send({"type": c.ROOM_SCREEN_LOOK, "data": look})
        await h.room.send({"type": "log", "data": {"level": "info", "msg": "hello"}})
        await h.room.send({"type": c.ROOM_SCREEN_LOOK, "data": "not a dict"})   # junk: dropped
        await h.room.send({"data": {"no": "type"}})
        await h.room.person("disco")                           # mode.transition and status: not fanned out
        await h.until(lambda: len(got) >= 2 and h.room.current == "disco")
        await h.step()
        assert got == [(c.ROOM_SCREEN_LOOK, look), ("log", {"level": "info", "msg": "hello"})]
        assert h.link.snapshot()["stats"]["room_events"] == {c.ROOM_SCREEN_LOOK: 1, "log": 1}
        json.dumps(h.link.snapshot())
        h.room.started_at = "2026-10-16T10:00:00+00:00"        # room_service restarted
        await h.room.push()
        await h.until(lambda: h.link.room_boot() == "2026-10-16T10:00:00+00:00")
        assert h.mode_changes == [("gallery", "disco")]        # the rest of RoomLink as before
    run(tmp_path, body)


def test_roomlink_room_boot_is_none_until_a_status_says_one():
    link_ctx = c.BrainContext(publish=lambda m: None)
    from show_service.autopilot import RoomLink
    link = RoomLink(link_ctx)
    assert link.room_boot() is None and link.snapshot()["boot"] is None
    link._on_status({"mode": {"current": "gallery"}})
    assert link.room_boot() is None
    link._on_status({"mode": {"current": "gallery"}, "service": {"started_at": 1760600000}})
    assert link.room_boot() == "1760600000"
    link._on_message({"type": c.ROOM_SCREEN_LOOK, "data": {"screen": "x"}})     # no listeners: just counted
    assert link.stats["room_events"] == {c.ROOM_SCREEN_LOOK: 1}


# -- the mic on demand (README S5.16): Auto's live on/off and its listeners -------------------------------------------
def test_is_on_and_the_on_listeners_for_the_mic(tmp_path):
    async def body(h: H):
        woke: list[bool | None] = []
        h.ap.add_on_listener(lambda: woke.append(h.ap.is_on()))
        assert h.ap.is_on() is False and woke == []
        await h.ap.set_on(True)                          # show's own toggle: heard at once (the override)
        assert h.ap.is_on() is True and woke == [True]
        await h.until(lambda: h.link.view()["autopilot_on"] is True)
        assert woke == [True]                            # the room agreeing changes nothing: no second call
        h.room.on = "off"                                # the room's own toggle (the Room page, another session)
        await h.room.push()
        await h.until(lambda: woke[-1] is False)
        assert h.ap.is_on() is False and woke == [True, False]
        h.ap.add_on_listener(lambda: 1 / 0)              # a failing listener is guarded
        h.room.on = "on"
        await h.room.push()
        await h.until(lambda: woke[-1] is True)
        await h.step()
        assert h.state()["on"] is True
    run(tmp_path, body, on="off")


# -- Auto triggers: the light words, the rules and the check (README S5.19) -----------------------------------------------
EVENING = {**MODES, "evening": {"label": "Evening", "autopilot": {
    "enter_when": {"light_below": {"lux": 50, "for_s": 2}}, "from": ["gallery"],
    "leave_when": {"light_above": {"lux": 200, "for_s": 2}}}}}


def test_a_light_rule_switches_to_its_mode_and_back(tmp_path):
    """A mode whose block uses light_below parses and evaluates in Auto: the real poll (the fake room serves the
    sensor route on its ephemeral port), the median, the for_s, the chip's words with the rule's lux."""
    async def body(h: H):
        lux = {"v": 400.0}
        h.room.lux = lambda: {"sensors": {"living-room": {"current": {"lux": lux["v"]}, "history": [
            {"timestamp": 1.79e9 + h.clock.t, "lux": lux["v"]}]}}}
        h.sit.configure({"situations": {**SIT, "light": {"url": h.room.url + "/api/lightsensor/lightsense",
                                                         "sensor": "living-room", "poll_s": 600, "smooth_s": 0}}})
        assert await h.sit.light.poll_once()
        await h.step(4)
        assert h.room.current == "gallery" and not h.room.auto_puts()
        cand = [x for x in h.state()["candidates"] if x["mode"] == "evening" and x["kind"] == "enter"][0]
        assert cand["holds"] is False and cand["blocked"] is None
        lux["v"] = 20.0
        assert await h.sit.light.poll_once()
        n = await h.step_until(lambda: h.room.current == "evening")
        assert n >= 2                                     # its for_s: 2 s
        put = h.room.auto_puts()[-1]
        assert put["mode"] == "evening" and put["reason"] == "the room is darker than 50 lux (evening/enter_when)"
        assert h.notified[-1][1]["text"] == "Auto → Evening · the room is darker than 50 lux"
        lux["v"] = 300.0
        assert await h.sit.light.poll_once()
        await h.step_until(lambda: h.room.current == "gallery")
        assert h.room.auto_puts()[-1]["reason"] == "the room is brighter than 200 lux (evening/leave_when)"

    run(tmp_path, body, modes=EVENING)


def test_rules_describe_every_mode_in_page_words(tmp_path):
    async def body(h: H):
        r = h.ap.rules()
        assert r["reachable"] is True and r["revision"] == "r1" and r["error"] is None
        by = {m["mode"]: m for m in r["modes"]}
        assert list(by) == ["gallery", "piano", "lights-only", "disco"]
        p = by["piano"]
        assert p["label"] == "Piano" and p["suggest"] is None
        assert p["enter"] == "the piano is playing, for at least 3 s"
        assert p["leave"] == "the piano has stopped, for at least 5 s"
        assert p["from"] == ["gallery", "lights-only"] and p["leave_to"] == "previous"
        assert p["nodes"] == {"enter_when": p["enter"], "leave_when": p["leave"],
                              "from": "only from Normal or Lights only", "leave_to": "then back to the mode before"}
        assert p["summary"] == ("Auto switches to Piano when the piano is playing, for at least 3 s (only from Normal "
                                "or Lights only). It goes back to the mode before when the piano has stopped, for at "
                                "least 5 s.")
        assert p["problems"] == [] and p["available"] is True and p["unknown_words"] == []
        assert p["live"] == {"enter_when": False, "leave_when": True} and p["now"] == {
            "enter": False, "suggest": None, "leave": True}       # silent now (its 5 s are Auto's own timer)
        assert p["auto"]["enter"] == {"holds": False, "held_s": 0.0, "needs_s": 3.0, "blocked": None}
        assert p["auto"]["leave"] is None and p["auto"]["suggest"] is None   # Auto didn't enter it
        g = by["gallery"]
        assert g["enter"] is g["leave"] is g["from"] is g["leave_to"] is None and g["problems"] == []
        assert g["summary"] == "Auto never switches to Normal by itself; it starts when someone picks it."
        assert g["live"] == {} and g["nodes"] == {} and g["available"] is True
        h.playing = True
        await h.step_until(lambda: {m["mode"]: m for m in h.ap.rules()["modes"]}["piano"]["live"]["enter_when"])
        p = {m["mode"]: m for m in h.ap.rules()["modes"]}["piano"]
        assert p["now"]["enter"] is True and p["live"]["leave_when"] is False
        await h.step_until(lambda: h.room.current == "piano")
        await h.step()
        p = {m["mode"]: m for m in h.ap.rules()["modes"]}["piano"]
        assert p["auto"]["leave"] is not None and p["auto"]["leave"]["holds"] is False   # Auto entered it: it watches

    run(tmp_path, body)


def test_check_good_and_bad_blocks_with_the_parser_auto_uses(tmp_path):
    async def body(h: H):
        ok = h.ap.check_block("piano", MODES["piano"]["autopilot"])
        assert ok["ok"] is True and ok["problems"] == [] and ok["unknown_words"] == []
        assert ok["enter"] == "the piano is playing, for at least 3 s" and ok["mode"] == "piano"
        # an unknown word
        b = h.ap.check_block("piano", {"enter_when": {"any": [{"piano_playing": {}}, {"piano_playin": {}}]}})
        assert b["ok"] is False and b["unknown_words"] == ["piano_playin"] and b["available"] is False
        assert b["problems"] == [{"path": "enter_when.any[1]", "text": "This isn't something the room can notice (it may have been renamed)",
                                  "level": "error"}]
        assert b["enter"] == "any of: the piano is playing or “piano_playin”"
        assert b["live"]["enter_when.any[1]"] is None and b["nodes"]["enter_when.any[0]"] == "the piano is playing"
        # a bad param, deep down
        b = h.ap.check_block("piano", {"enter_when": {"piano_playing": {}}, "leave_when": {"all": [
            {"not": {"piano_playing": {"min_notes": "lots"}}}, {"quiet": {"for_s": 60}}]}})
        assert b["ok"] is False and [(x["path"], x["level"]) for x in b["problems"]] == [("leave_when.all[0].not", "error")]
        assert "must be a number" in b["problems"][0]["text"] and b["live"]["leave_when.all[0].not"] is None
        assert b["leave"] == "both: not the piano is playing and (the room is quiet, for at least 1 min)"
        # a bad leave_to; then the same with the mode being made in the editor
        blk = {**MODES["piano"]["autopilot"], "leave_to": "late-night"}
        b = h.ap.check_block("piano", blk)
        assert b["ok"] is False and b["problems"] == [{"path": "leave_to", "text": "There's no mode called “late-night”",
                                                       "level": "error"}]
        b = h.ap.check_block("piano", blk, ["late-night"])
        assert b["ok"] is True and b["leave_to"] == "late-night" and b["nodes"]["leave_to"] == "then to late-night"
        assert b["summary"].endswith("It goes to late-night when the piano has stopped, for at least 5 s.")
        # a new mode: its own name in "from" and a leave_to with no leave rule are warns, never errors
        b = h.ap.check_block("late-night", {"enter_when": {"between": {"from": "23:00", "to": "02:00"}},
                                            "from": ["gallery", "late-night"], "leave_to": "gallery"}, ["late-night"])
        assert b["ok"] is True and [(x["path"], x["level"]) for x in b["problems"]] == [("from", "warn"),
                                                                                        ("leave_to", "warn")]
        assert b["enter"] == "it's 23:00-02:00" and b["from"] == ["gallery", "late-night"]
        b = h.ap.check_block("piano", {**MODES["piano"]["autopilot"], "from": ["gallery", "atlantis"]})
        assert not b["ok"] and b["problems"][0] == {"path": "from", "text": "There's no mode called “atlantis”",
                                                    "level": "error"}
        # a word that can't be sensed now is a warn on its node, never an error
        b = h.ap.check_block("disco", {"otherwise_suggest_when": {"all": [{"talking": {}}, {"piano_idle": {}}]}})
        assert b["ok"] is True and b["available"] is False
        assert b["problems"] == [{"path": "otherwise_suggest_when.all[0]", "text": "The music/speech check isn't "
                                  "running yet", "level": "warn", "word": "talking"}]
        assert b["summary"] == ("Auto offers Disco (one tap) when both: people are talking and the piano has stopped. "
                                "Auto never leaves it by itself.")
        # a leave rule with nothing to enter by; an unknown key; not an object
        b = h.ap.check_block("disco", {"leave_when": {"quiet": {}}, "enter_whne": {"quiet": {}}})
        assert b["ok"] is False and [(x["path"], x["level"]) for x in b["problems"]] == [
            ("enter_whne", "error"), ("leave_when", "warn")]
        b = h.ap.check_block("disco", ["piano_playing"])
        assert b["ok"] is False and b["problems"][0]["path"] == "" and b["enter"] is None
        # the empty shapes: no block, {}, absent/null/{} rules and from [] are all fine and say so, with no warning
        for blk in (None, {}, {"enter_when": {}, "leave_when": None, "from": [], "otherwise_suggest_when": {}}):
            e = h.ap.check_block("disco", blk)
            assert e["ok"] is True and e["problems"] == [], blk
            assert e["enter"] is e["suggest"] is e["leave"] is e["from"] is e["leave_to"] is None
            assert e["summary"] == "Auto never switches to Disco by itself; it starts when someone picks it."
        e = h.ap.check_block("disco", {"enter_when": {"piano_playing": {}}, "from": []})
        assert e["ok"] and e["from"] is None and e["nodes"]["from"] == "from any mode" and "(" not in e["summary"]
        assert e["summary"] == ("Auto switches to Disco when the piano is playing. Auto never leaves it by itself.")
        assert h.ap.check_block(None, {"enter_when": {"quiet": {}}})["label"] is None
        assert h.room.auto_puts() == []                       # checking never acts

    run(tmp_path, body)


EMPTY = {**MODES, "piano": {**MODES["piano"], "autopilot": {**MODES["piano"]["autopilot"], "from": []}},
         "disco": {"label": "Disco", "autopilot": {"enter_when": {}, "otherwise_suggest_when": None}}}


def test_the_empty_shapes_in_auto_itself(tmp_path):
    """from [] is any mode (it used to be none); enter_when {} is never by itself, with no "rule error"."""
    async def body(h: H):
        await h.step(2)
        assert [x for x in h.state()["candidates"] if x["mode"] == "disco"] == []
        assert h.state()["state"] == "watching"
        await enter_piano(h)
        assert h.room.auto_puts()[-1]["mode"] == "piano"

    run(tmp_path, body, modes=EMPTY)


def test_switching_auto_on_waives_the_minimum_stay_in_the_current_mode(tmp_path):
    """Owner, 17 Oct: picking a mode and then turning Auto on must not leave Auto waiting out min_dwell_s."""
    async def body(h: H):
        h.room.since = h.clock.t                          # someone just picked the current mode
        h.playing = True
        await h.step(15)
        assert h.room.puts == []                          # off: nothing
        await h.ap.set_on(True)
        await h.step_until(lambda: h.room.current == "piano")   # long before the 300 s minimum stay
        assert h.clock.t - h.room.since < 120
    run(tmp_path, body, on="off", auto={**AUTO, "min_dwell_s": 300})
