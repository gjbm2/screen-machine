"""brain.py (shared plumbing, real tests): add/provides, the show-brain thread and its tick schedules (guarded and in
add order), Brain.call across loops (result, exception, timeout), state.json (restore round trip, a torn file starts
cold), show.yaml (read at start, hot reload, a bad edit keeps the last good, missing -> neutral). Every file is in
tmp_path; nothing is bound."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from show_service import contract
from show_service.brain import Brain, parse_settings

ORDER: list[str] = []


class Comp:
    def __init__(self, name: str, hz: float = 0.0, *, fail_tick: bool = False, provides: dict | None = None,
                 value: int = 0) -> None:
        self.name, self.tick_hz = name, hz
        self.fail_tick = fail_tick
        self._prov = provides or {}
        self.value = value
        self.calls: list[str] = []
        self.ticks: list[float] = []
        self.threads: set[str] = set()
        self.settings: list[dict] = []
        self.saved: dict | None = None

    def provides(self) -> dict:
        return self._prov

    def configure(self, settings: dict) -> None:
        self.calls.append("configure")
        self.settings.append(settings)

    def restore(self, saved: dict) -> None:
        self.calls.append("restore")
        self.saved = saved
        if "value" in saved:
            self.value = saved["value"]

    def start(self) -> None:
        self.calls.append("start")
        self.loop_running = asyncio.get_running_loop() is not None

    def tick(self, now_s: float) -> None:
        self.ticks.append(now_s)
        self.threads.add(threading.current_thread().name)
        ORDER.append(self.name)
        if self.fail_tick:
            raise RuntimeError("boom")

    def snapshot(self) -> dict:
        return {"ticks": len(self.ticks), "value": self.value}

    def persist(self) -> dict | None:
        return {"value": self.value}

    def stop(self) -> None:
        self.calls.append("stop")
        ORDER.append("stop:" + self.name)

    # called through Brain.call
    def bump(self, by: int) -> int:
        assert threading.current_thread().name == "show-brain"
        self.value += by
        return self.value

    async def slow(self, s: float) -> str:
        await asyncio.sleep(s)
        return "late"

    def bad(self) -> None:
        raise ValueError("unknown_vibe: nope")


def until(pred, timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def make(tmp_path, comps, yaml_text: str | None = "version: 1\nroom: {url: http://127.0.0.1:9}\n", **kw):
    show = tmp_path / "show.yaml"
    if yaml_text is not None:
        show.write_text(yaml_text)
    ctx = contract.BrainContext(publish=lambda m: None)
    b = Brain(ctx, show_file=show, state_file=tmp_path / "state" / "state.json", reload_s=kw.pop("reload_s", 0.1),
              **kw)
    for c in comps:
        b.add(c)
    return b, ctx, show


def test_add_applies_provides_and_refuses_duplicates(tmp_path):
    seen = []
    a = Comp("room", provides={"room": lambda: {"current": "x"}, "on_mode_change": lambda o, n: seen.append((o, n)),
                               "no_such_slot": 1})
    b, ctx, _ = make(tmp_path, [a])
    assert ctx.room() == {"current": "x"} and len(ctx.on_mode_change) == 1 and not hasattr(ctx, "no_such_slot")
    with pytest.raises(ValueError):
        b.add(Comp("room"))


def test_lifecycle_ticks_in_add_order_on_show_brain(tmp_path):
    ORDER.clear()
    fast, slow1, slow2 = Comp("director", 10.0), Comp("situations", 1.0), Comp("autopilot", 1.0)
    quiet = Comp("room", 0.0)
    b, ctx, _ = make(tmp_path, [quiet, slow1, slow2, fast])
    assert b.running is False
    b.start()
    try:
        assert b.running and ctx.loop is not None and ctx.persist is not None
        for c in (quiet, slow1, slow2, fast):
            assert c.calls[:3] == ["restore", "configure", "start"] and c.loop_running
            assert c.settings[0]["room"]["url"] == "http://127.0.0.1:9"
        assert ctx.settings["version"] == 1
        until(lambda: len(slow1.ticks) >= 2 and len(fast.ticks) >= 12)
        assert quiet.ticks == [] and fast.threads == {"show-brain"} == slow1.threads
        ones = [n for n in ORDER if n in ("situations", "autopilot")]
        assert ones[:4] == ["situations", "autopilot", "situations", "autopilot"]      # add order within a rate
        rate = (len(fast.ticks) - 1) / (fast.ticks[-1] - fast.ticks[0])
        assert 8.0 <= rate <= 11.0, rate
        until(lambda: b.snapshot()["ticks"].get("director", {}).get("p95_ms") is not None)
        snap = b.snapshot()
        assert snap["running"] and snap["components"]["director"]["ticks"] >= 12
        assert snap["ticks"]["situations"]["hz"] == 1.0 and snap["ticks"]["director"]["errors"] == 0
        t = snap["ticks"]["director"]                       # wall (with GIL waits) and the component's own CPU
        assert t["wall_p95_ms"] == t["p95_ms"] and t["cpu_p95_ms"] is not None and t["cpu_p95_ms"] >= 0
        assert snap["settings"]["error"] is None and snap["settings"]["revision"]
        assert snap["state_file"]["path"].endswith("state.json")
    finally:
        b.stop()
    assert not b.running and [n for n in ORDER if n.startswith("stop:")] == ["stop:director", "stop:autopilot",
                                                                            "stop:situations", "stop:room"]
    saved = json.loads((tmp_path / "state" / "state.json").read_text())       # the final write at stop
    assert saved["v"] == 1 and saved["director"] == {"value": 0} and saved["room"] == {"value": 0}
    assert not any(t.name == "show-brain" for t in threading.enumerate())


def test_a_failing_tick_is_counted_and_the_others_carry_on(tmp_path):
    bad, good = Comp("listener", 10.0, fail_tick=True), Comp("director", 10.0)
    b, ctx, _ = make(tmp_path, [bad, good])
    b.start()
    try:
        until(lambda: len(good.ticks) >= 10)
        until(lambda: b.snapshot()["ticks"].get("listener", {}).get("errors", 0) >= 5)
        t = b.snapshot()["ticks"]
        assert t["listener"]["last_error"] == "RuntimeError: boom" and t["director"]["errors"] == 0
    finally:
        b.stop()


def test_call_runs_on_show_brain_from_another_loop(tmp_path):
    c = Comp("director", 0.0)
    b, ctx, _ = make(tmp_path, [c])

    async def from_bus() -> None:
        with pytest.raises(RuntimeError):                                   # not started: refused, never hangs
            await b.call(c.bump, 1)
        b.start()
        assert await b.call(c.bump, 2) == 2                                   # on show-brain (bump asserts it)
        assert await b.call(c.slow, 0.01) == "late"                           # an awaitable is awaited there
        with pytest.raises(ValueError, match="unknown_vibe"):
            await b.call(c.bad)
        t0 = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await b.call(c.slow, 5.0, timeout=0.2)
        assert time.monotonic() - t0 < 1.0
        assert await asyncio.gather(*(b.call(c.bump, 1) for _ in range(20))) == list(range(3, 23))

    try:
        asyncio.run(from_bus())                                               # this thread's loop plays show-bus
    finally:
        b.stop()


def test_state_round_trip_and_dirty_writes_within_a_second(tmp_path):
    c = Comp("director", 0.0, value=7)
    b, ctx, _ = make(tmp_path, [c])
    b.start()
    try:
        c.value = 8
        ctx.persist()                                                         # = brain.dirty()
        path = tmp_path / "state" / "state.json"
        until(lambda: path.exists() and json.loads(path.read_text())["director"] == {"value": 8}, timeout=3.0)
        until(lambda: b.snapshot()["state_file"]["saves"] >= 1)             # counted just after the replace
    finally:
        b.stop()
    c2 = Comp("director", 0.0)
    b2, _, _ = make(tmp_path, [c2])
    b2.start()
    try:
        assert c2.saved == {"value": 8} and c2.value == 8
    finally:
        b2.stop()


def test_a_torn_state_file_starts_cold(tmp_path):
    (tmp_path / "state").mkdir(mode=0o700)
    (tmp_path / "state" / "state.json").write_text('{"v": 1, "director": {"val')
    c = Comp("director", 0.0)
    b, _, _ = make(tmp_path, [c])
    b.start()
    try:
        assert c.saved == {}
        st = b.snapshot()["state_file"]
        assert st["error"].startswith("Started cold") and st["bad_file"] and ".bad-" in st["bad_file"]
    finally:
        b.stop()


def test_show_yaml_hot_reload_and_a_bad_edit_keeps_the_last_good(tmp_path):
    c = Comp("sources", 0.0)
    b, ctx, show = make(tmp_path, [c], "version: 1\nsenders: {}\n")
    b.start()
    try:
        assert ctx.settings == {"version": 1, "senders": {}}
        time.sleep(0.05)
        show.write_text("version: 1\nsenders: {}\nlistener: {cadence_s: 30}\n")
        until(lambda: len(c.settings) >= 2)
        assert c.settings[-1]["listener"] == {"cadence_s": 30} and ctx.settings is c.settings[-1]
        rev = b.snapshot()["settings"]["revision"]
        show.write_text("version: 1\nsenders: [oops\n")                       # a bad edit
        until(lambda: b.snapshot()["settings"]["error"] is not None)
        assert "keeping the last good" in b.snapshot()["settings"]["error"] and len(c.settings) == 2
        assert ctx.settings["listener"] == {"cadence_s": 30} and b.snapshot()["settings"]["revision"] == rev
        show.write_text("version: 1\n")                                       # fixed: applied, error gone
        until(lambda: len(c.settings) >= 3)
        assert c.settings[-1] == {"version": 1} and b.snapshot()["settings"]["error"] is None
    finally:
        b.stop()


@pytest.mark.parametrize("text", [None, "- a list\n", "{bad: [yaml"])
def test_a_missing_or_bad_show_yaml_at_start_is_neutral(tmp_path, text):
    c = Comp("director", 1.0)
    b, ctx, _ = make(tmp_path, [c], text)
    b.start()
    try:
        assert ctx.settings == {} and c.settings == [{}] and b.running
        err = b.snapshot()["settings"]["error"]
        assert err and "neutral settings" in err
    finally:
        b.stop()


def test_parse_settings():
    assert parse_settings(b"") == {} and parse_settings(b"# only a comment\n") == {}
    assert parse_settings(b"a: 1\n") == {"a": 1}
    with pytest.raises(ValueError):
        parse_settings(b"[1, 2]")
    with pytest.raises(ValueError):
        parse_settings(b"a: [1")


def test_a_component_that_fails_to_start_does_not_stop_the_others(tmp_path):
    class Broken(Comp):
        def start(self) -> None:
            raise RuntimeError("no")

        def configure(self, settings: dict) -> None:
            raise TypeError("nope")

    bad, good = Broken("listener", 10.0), Comp("director", 10.0)
    b, _, _ = make(tmp_path, [bad, good])
    b.start()
    try:
        until(lambda: len(good.ticks) >= 3 and len(bad.ticks) >= 3)          # it still ticks: degrade, never refuse
        assert b.snapshot()["errors"] == {"start": 1, "configure": 1}
    finally:
        b.stop()
