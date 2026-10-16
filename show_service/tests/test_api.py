"""api.py: every route against fake components, the /v1/events socket, the pure card, merge patches, and the bus seam.

Servers are aiohttp TestServers (or a real Bus) on ephemeral 127.0.0.1 ports: never 8703, never the room. The fake
brain runs its own loop on a thread named "show-brain", so every ``brain.call`` crosses loops as in production."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import random
import threading
import time
import types

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from show_service import api as apimod
from show_service import contract as c
from show_service.api import Api, apply_patch, card, merge_patch
from show_service.bus import Bus

WS = aiohttp.WSMsgType


# -- fakes ------------------------------------------------------------------------------------------------------------
def _thread() -> str:
    return threading.current_thread().name


class FakeDirector:
    name, tick_hz = "director", 10.0

    def __init__(self) -> None:
        self.steps, self.max = 0, 3
        self.pick, self.house, self.low = "auto", False, False
        self.chips: list[dict] = []
        self.target: dict | None = None
        self.source: str | None = None
        self.threads: list[str] = []
        self.choices = [{"name": n, "label": n.title(), "family": "warm", "swatch": ["#aa5500"] * 5}
                        for n in ("ember", "sea", "dusk", "moss", "rose", "sand", "ink")]
        self.slow_s = 0.0
        self.boom = False
        self.snap: dict = {}
        self._swap()

    def _steps(self) -> dict:
        return {"value": self.steps, "min": -self.max, "max": self.max, "at_limit": abs(self.steps) >= self.max}

    def _chip(self, cid: str, label: str, value) -> None:
        self.chips = [ch for ch in self.chips if ch["id"] != cid]
        self.chips.append({"id": cid, "kind": cid, "label": label, "value": value, "expires_in_s": None})

    def _swap(self) -> None:            # a new dict by reference, never mutated afterwards
        self.snap = {"target": self.target, "readback": self.readback(),
                     "direction": {"loose": True, "moods": ["warm", "mellow"], "energy": 0.4},
                     "look": {"look_id": "L3", "palette": {"name": "ember"}}, "steps": self._steps(),
                     "palette": {"pick": self.pick, "current": "ember",
                                 "choices": [{"name": "art", "label": "From the art", "family": None, "swatch": []}]
                                 + self.choices},
                     "chips": [dict(ch) for ch in self.chips], "house": self.house, "low": self.low,
                     "next_pending": False, "keep_in_s": 899.6 if any(ch["id"] == "keep" for ch in self.chips) else None,
                     "engaged": bool(self.chips)}

    def snapshot(self) -> dict:
        return self.snap

    def step(self, d: str) -> dict:
        self.threads.append(_thread())
        if self.slow_s:
            time.sleep(self.slow_s)
        if self.boom:
            raise KeyError("oops")
        self.steps = max(-self.max, min(self.max, self.steps + (1 if d == "wilder" else -1)))
        if self.steps:
            self._chip("steps", f"{'Wilder' if self.steps > 0 else 'Calmer'} ×{abs(self.steps)}", self.steps)
        self._swap()
        return {"steps": self._steps(), "chips": self.snap["chips"]}

    def set_palette(self, name: str) -> dict:
        self.threads.append(_thread())
        if name not in ("auto", "art") and name not in [ch["name"] for ch in self.choices]:
            raise ValueError("unknown_palette")
        self.pick = name
        self._chip("palette", f"Palette: {name.title()}", name)
        self._swap()
        return {"palette": {"pick": name, "current": name}, "chips": self.snap["chips"]}

    def action(self, do: str) -> dict:
        if do == "house":
            self.house = not self.house
        elif do == "low":
            self.low = not self.low
        elif do == "keep":
            self._chip("keep", "Keep this look · 15 min", True)
        self._swap()
        return {"done": True, "chips": self.snap["chips"], "look": {"id": "L3", "next_pending": do == "next"}}

    def clear(self, nid: str) -> list[str]:
        ids = [ch["id"] for ch in self.chips] if nid == "all" else [ch["id"] for ch in self.chips if ch["id"] == nid]
        self.chips = [ch for ch in self.chips if ch["id"] not in ids]
        if "steps" in ids:
            self.steps = 0
        if "palette" in ids:
            self.pick = "auto"
        # deliberately NOT swapping the snapshot here: api must not echo a cleared chip from a lagging snapshot
        return ids

    def readback(self) -> dict:
        t = self.target or {}
        known = t.get("vibe") in ("home", "party")
        problems = ([] if known or not t else [f"the feel {t.get('vibe')!r} isn't in vibes.yaml"])
        if (t.get("overrides") or {}).get("level") == "loud":
            problems.append("overrides.level: not a range")
        out = {"vibe": t.get("vibe"), "listening": bool(t.get("listening")), "source": self.source or "none",
               "set_age_s": 1.0 if self.target else None, "known": known,
               "label": (t.get("vibe") or "").title(), "effective": {"persona": "a warm host", "moods": ["warm"]},
               "listening_effective": False, "listening_why": "AI listening is off", "problems": problems}
        if t.get("overrides"):
            out["overrides"] = dict(t["overrides"])
        return out

    def set_target(self, body, source: str = "api") -> dict:
        """As the real one: kept as asked (an unknown vibe or a bad override degrades, never refused); None or
        {"vibe": null} is no feel; ValueError only for a body that isn't a target."""
        if isinstance(body, dict) and "name" in body:
            body = {"vibe": body["name"]}
        if isinstance(body, str):
            body = {"vibe": body}
        if body is None or (isinstance(body, dict) and body.get("vibe") is None and "vibe" in body):
            self.target, self.source = None, "none"
            self._swap()
            return self.readback()
        if not isinstance(body, dict) or not isinstance(body.get("vibe"), str):
            raise ValueError("bad_target: vibe must be a name")
        self.target = {"vibe": body["vibe"], "listening": bool(body.get("listening"))}
        if body.get("overrides"):
            self.target["overrides"] = dict(body["overrides"])
        self.source = source
        self._swap()
        return self.readback()

    def catalog(self) -> dict:
        return {"revision": "abc123def456", "path": "config/room/vibes.yaml", "error": None, "order": ["home", "party"],
                "vibes": {"home": {"label": "Home", "description": "", "fields": {}}}, "fields": {}, "neutral": {},
                "palettes": self.choices, "vocab": {"moods": ["warm"]}}

    def apply_describe(self, intent: dict) -> list[dict]:
        self.threads.append(_thread())
        out = []
        if intent.get("steps"):
            self.steps = max(-self.max, min(self.max, self.steps + intent["steps"]))
            self._chip("steps", "Calmer ×1", self.steps)
            out.append(self.chips[-1])
        self._swap()
        return out

    def release_hue(self) -> dict:
        return {"released": True, "via": "room"}

    def log(self, n: int = 50) -> list[dict]:
        return [{"at": i, "kind": "look_change"} for i in range(100)][-n:]


class FakeListener:
    name, tick_hz = "listener", 1.0

    def __init__(self) -> None:
        self.on = False
        self.delay_s = 0.0
        self.intent = {"steps": -1, "palette": None, "moods": ["mellow"], "keep": False, "switch_mode": "party",
                       "reply": "Calmer, and mellow."}
        self.contexts: list[dict] = []
        self._beliefs = {"state": "fresh", "age_s": 3.0, "genre": {"word": "soul", "conf": 0.8},
                         "moods": [{"word": "relaxed", "conf": 0.7}], "instruments": [], "vocals": None,
                         "section": None, "trajectory": None, "content": "music"}

    def view(self) -> dict:
        return {**apimod.neutral_listener(None if self.on else "AI listening is off"), "on": self.on}

    def set_on(self, on: bool) -> dict:
        self.on = on
        return self.view()

    def beliefs(self) -> dict:
        return self._beliefs

    def snapshot(self) -> dict:         # no beliefs here: api adds them from beliefs()
        return self.view()

    async def describe(self, text: str, context: dict):
        self.contexts.append(context)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.intent, "llm"

    def log(self, n: int = 50) -> list[dict]:
        return [{"verdict": i} for i in range(n)]


class FakeAutopilot:
    name, tick_hz = "autopilot", 1.0

    def __init__(self) -> None:
        self.on = False
        self.unreachable = False
        self.refuse: str | None = None
        self.last_switch = False

    def state(self) -> dict:
        return {**apimod.neutral_autopilot("Auto is off" if not self.on else "Auto · watching"),
                "on": self.on, "state": "watching" if self.on else "off"}

    def snapshot(self) -> dict:
        return self.state()

    async def set_on(self, on: bool) -> dict:
        await asyncio.sleep(0)
        if self.unreachable:
            raise RuntimeError("room_unreachable")
        if self.refuse:                       # as autopilot.py raises the room's refusal
            e = ValueError(self.refuse)
            e.detail, e.status = "no such target: autopilot.main", 404
            raise e
        self.on = on
        return self.state()

    async def undo(self) -> dict:
        if not self.last_switch:
            raise LookupError("nothing_to_undo")
        self.last_switch = False
        return {"undone": True, "mode": "gallery", "transition_id": "t9"}

    def resume(self) -> dict:
        return self.state()

    def log(self, n: int = 50) -> list[dict]:
        return [{"kind": "switch"}]


class FakeSituations:
    name, tick_hz = "situations", 1.0

    def __init__(self) -> None:
        self.readings = {"talking": {"value": None, "since_s": None, "via": "none", "evidence": {}},
                         "quiet": {"value": False, "since_s": 3.0, "via": "dsp", "evidence": {}},
                         "music_audible": {"value": True, "since_s": 9.0, "via": "dsp", "evidence": {}}}

    def snapshot(self) -> dict:
        return {"readings": self.readings,
                "vocabulary": [{"id": "quiet", "label": "the room is quiet", "description": "", "source": "audio",
                                "rank": 2, "params": {}, "available": True}]}


class FakeSources:
    name, tick_hz = "sources", 1.0

    def __init__(self) -> None:
        self.running: dict[str, str] = {}

    def pieces(self) -> dict:
        return {"piano": [{"id": "file:etude.mid", "label": "Etude", "kind": "file"}],
                "audio": [{"id": "file:jazz.mp3", "label": "Jazz", "kind": "file"}]}

    def _status(self, s: str) -> dict:
        return {**apimod.neutral_source(s), "state": "starting" if s in self.running else "off",
                "piece": self.running.get(s), "sender": f"test-{s}"}

    def status(self) -> dict:
        return {s: self._status(s) for s in c.TEST_SOURCES}

    def start_test(self, source: str, piece: str | None = None) -> dict:
        if source not in c.TEST_SOURCES:
            raise ValueError("unknown_source")
        ids = [p["id"] for p in self.pieces()[source]]
        piece = piece or ids[0]
        if piece not in ids:
            raise ValueError("unknown_piece")
        self.running[source] = piece
        return self._status(source)

    def stop_test(self, source: str | None = None) -> list[str]:
        names = [s for s in list(self.running) if source is None or s == source]
        for s in names:
            del self.running[s]
        return names

    def snapshot(self) -> dict:
        return {"tests": self.status(), "senders": [], "priority": [], "errors": []}


class FakeRoom:
    name, tick_hz = "room", 0.0

    def view(self) -> dict:
        return {"reachable": True, "current": "gallery", "order": ["gallery", "party", "piano"],
                "modes": {"gallery": {"label": "Gallery"}, "party": {"label": "Party time"}, "piano": {}}}

    def snapshot(self) -> dict:
        return {"reachable": True}


class FakeBrain:
    """Brain's any-thread surface: ``call`` runs fn on its own loop (thread "show-brain"), ``snapshot``, ``ctx``."""

    def __init__(self, comps=None, *, settings=None, clock_action=None, timeout: float = 2.0) -> None:
        comps = comps if comps is not None else default_components()
        self.components = {x.name: x for x in comps}
        self.timeout = timeout
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="show-brain", daemon=True)
        self.thread.start()
        self.ctx = types.SimpleNamespace(loop=self.loop, settings=settings or {}, clock_action=clock_action,
                                         clock=lambda: {"bpm": 120.0, "gate": "beat"}, room=None)
        self.calls = 0
        self.timeouts: dict[str, object] = {}

    async def call(self, fn, *args, timeout=None):
        self.calls += 1
        self.timeouts[getattr(fn, "__name__", "?")] = timeout

        async def run():
            r = fn(*args)
            return await r if inspect.isawaitable(r) else r

        fut = asyncio.run_coroutine_threadsafe(run(), self.loop)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout or self.timeout)

    def snapshot(self) -> dict:
        return {"components": {n: x.snapshot() for n, x in self.components.items()}, "ticks": {}}

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2.0)


def default_components():
    return [FakeRoom(), FakeSituations(), FakeAutopilot(), FakeSources(), FakeListener(), FakeDirector()]


ENGINE = {"active_audio": "room-mic", "features": {"energy": 0.42, "age_ms": 40.0, "stream": "room-mic"},
          "clock": {"bpm": 118.0, "gate": "beat"}}


@pytest.fixture
def brain():
    b = FakeBrain()
    yield b
    b.close()


@contextlib.asynccontextmanager
async def served(api: Api):
    app = web.Application()
    api(app)
    server = TestServer(app, host="127.0.0.1", port=0)
    client = TestClient(server)
    await client.start_server()
    assert server.port not in (0, c.BUS_PORT, 8700)
    try:
        yield client
    finally:
        await client.close()


def make_api(brain, engine=ENGINE, **kw) -> Api:
    return apimod.routes(lambda: brain, lambda: engine, **kw)


def run(coro):
    return asyncio.run(coro)


async def body(resp) -> dict:
    assert resp.content_type == "application/json"
    return json.loads(await resp.text())


# -- starting / missing / busy -------------------------------------------------------------------------------------------
def test_everything_answers_starting_before_the_brain_runs():
    api = apimod.routes(lambda: None, lambda: ENGINE)

    async def go():
        async with served(api) as cl:
            for method, path in apimod.ROUTES:
                if path in (c.EVENTS_PATH, "/api/v1/hue/release"):
                    continue
                path = path.replace("{id}", "all").replace("{source}", "piano")
                r = await cl.request(method, path, json={})
                assert r.status == 503, (method, path)
                b = await body(r)
                assert b["error"] == "starting" and "starting" in b["detail"]
            r = await cl.get(c.EVENTS_PATH)
            assert r.status == 503 and (await body(r))["error"] == "starting"
            r = await cl.post("/api/v1/hue/release")          # never blocks a mode's exit
            assert r.status == 200 and (await body(r))["released"] is True

    run(go())


def test_a_brain_that_isnt_running_yet_is_starting():
    b = FakeBrain([])
    b.ctx.loop = None                                       # Brain built, start() not done
    try:
        async def go():
            async with served(make_api(b)) as cl:
                r = await cl.get("/api/v1/card")
                assert r.status == 503
        run(go())
    finally:
        b.close()


def test_missing_components_degrade_never_refuse():
    b = FakeBrain([])

    async def go():
        async with served(make_api(b)) as cl:
            r = await cl.get("/api/v1/card")
            assert r.status == 200
            cd = await body(r)
            assert cd["auto"]["line"] == apimod.MISSING["autopilot"] and cd["listening"]["on"] is False
            assert [x["name"] for x in cd["palette"]["choices"]] == ["auto", "art"]
            for method, path, payload in [
                    ("POST", "/api/v1/step", {"dir": "calmer"}), ("PUT", "/api/v1/palette", {"name": "ember"}),
                    ("POST", "/api/v1/action", {"do": "next"}), ("DELETE", "/api/v1/nudge/all", None),
                    ("GET", "/api/v1/vibes", None), ("GET", "/api/v1/vibe", None),
                    ("POST", "/api/v1/describe", {"text": "calmer please"}),
                    ("GET", "/api/v1/listening", None), ("PUT", "/api/v1/listening", {"on": True}),
                    ("GET", "/api/v1/autopilot", None), ("PUT", "/api/v1/autopilot", {"on": True}),
                    ("POST", "/api/v1/autopilot/undo", None), ("POST", "/api/v1/autopilot/resume", None),
                    ("GET", "/api/v1/situations", None), ("GET", "/api/v1/test", None),
                    ("POST", "/api/v1/test", {"source": "piano"}), ("DELETE", "/api/v1/test", None),
                    ("GET", "/api/v1/look", None), ("GET", "/api/v1/log?kind=autopilot", None),
                    ("POST", "/api/v1/action", {"do": "tap"})]:
                r = await cl.request(method, path, json=payload)
                assert r.status == 200, (method, path, r.status, await r.text())
            r = await cl.put("/api/v1/vibe", json={"vibe": "party"})     # the adapter's: retry later
            assert r.status == 503 and (await body(r))["error"] == "unavailable"
            r = await cl.post("/api/v1/describe", json={"text": "calmer"})
            assert (await body(r))["ok"] is True

    try:
        run(go())
    finally:
        b.close()


def test_busy_brain_is_503_busy(brain):
    brain.timeout = 0.2
    brain.components["director"].slow_s = 0.6

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.post("/api/v1/step", json={"dir": "calmer"})
            assert r.status == 503 and (await body(r))["error"] == "busy"

    run(go())


def test_a_component_that_breaks_is_500_in_plain_words(brain):
    brain.components["director"].boom = True

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.post("/api/v1/step", json={"dir": "calmer"})
            b = await body(r)
            assert r.status == 500 and b["error"] == "failed" and "show carries on" in b["detail"]

    run(go())


# -- nudges -----------------------------------------------------------------------------------------------------------------
def test_step_runs_on_show_brain_and_clamps(brain):
    d = brain.components["director"]

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.post("/api/v1/step", json={"dir": "Calmer"})
            b = await body(r)
            assert r.status == 200 and b["steps"]["value"] == -1 and b["chips"][0]["id"] == "steps"
            for _ in range(5):
                r = await cl.post("/api/v1/step", json={"dir": "calmer"})
            b = await body(r)
            assert r.status == 200 and b["steps"] == {"value": -3, "min": -3, "max": 3, "at_limit": True}
            r = await cl.post("/api/v1/step", json={"dir": "sideways"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "bad_request" and b["choices"] == ["calmer", "wilder"]
            r = await cl.post("/api/v1/step", data=b"{not json")
            assert r.status == 400 and (await body(r))["detail"] == "The request body isn't JSON."

    run(go())
    assert set(d.threads) == {"show-brain"}


def test_palette_pick_and_unknown_gives_choices(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.put("/api/v1/palette", json={"name": "sea"})
            b = await body(r)
            assert r.status == 200 and b["palette"]["pick"] == "sea" and b["chips"][0]["label"] == "Palette: Sea"
            r = await cl.put("/api/v1/palette", json={"name": "plaid"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "unknown_palette" and "plaid" in b["detail"]
            assert b["choices"][:4] == ["auto", "art", "ember", "sea"]
            r = await cl.put("/api/v1/palette", json={})
            assert r.status == 400 and (await body(r))["error"] == "bad_request"

    run(go())


def test_action_director_and_clock_words(brain):
    taps = []
    brain.ctx.clock_action = lambda word, t: taps.append((word, t, _thread()))

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.post("/api/v1/action", json={"do": "house"})
            b = await body(r)
            assert r.status == 200 and b["done"] and b["look"] == {"id": "L3", "next_pending": False}
            assert brain.components["director"].house is True
            t0 = time.monotonic()
            r = await cl.post("/api/v1/action", json={"do": "tap"})
            b = await body(r)
            assert r.status == 200 and b == {"done": True, "clock": ENGINE["clock"]}
            assert taps[0][0] == "tap" and t0 - 1.0 < taps[0][1] <= time.monotonic()
            r = await cl.post("/api/v1/action", json={"do": "dance"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "unknown_action" and "one" in b["choices"]

    run(go())


def test_explicit_clock_action_wins_and_a_missing_clock_degrades(brain):
    got = []

    async def go():
        async with served(make_api(brain, clock_action=lambda w, t: got.append(w))) as cl:
            r = await cl.post("/api/v1/action", json={"do": "half"})
            assert (await body(r))["done"] is True and got == ["half"]
        async with served(make_api(brain)) as cl:                     # ctx.clock_action is None
            r = await cl.post("/api/v1/action", json={"do": "one"})
            b = await body(r)
            assert r.status == 200 and b["done"] is False and "clock" in b["detail"]

    run(go())


def test_nudge_clear_one_unknown_and_all(brain):
    d = brain.components["director"]

    async def go():
        async with served(make_api(brain)) as cl:
            await cl.post("/api/v1/step", json={"dir": "wilder"})
            await cl.put("/api/v1/palette", json={"name": "ember"})
            r = await cl.delete("/api/v1/nudge/steps")
            b = await body(r)
            assert r.status == 200 and b["cleared"] == ["steps"] and [x["id"] for x in b["chips"]] == ["palette"]
            r = await cl.delete("/api/v1/nudge/nope")
            b = await body(r)
            assert r.status == 200 and b["cleared"] == []
            r = await cl.delete("/api/v1/nudge/all")
            b = await body(r)
            assert b["cleared"] == ["palette"] and b["chips"] == []

    run(go())
    assert d.chips == []


# -- vibes -------------------------------------------------------------------------------------------------------------------
def test_vibe_routes(brain):
    d = brain.components["director"]

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.get("/api/v1/vibes")
            assert r.status == 200 and (await body(r))["order"] == ["home", "party"]
            r = await cl.get("/api/v1/vibe")
            assert (await body(r))["vibe"] is None
            r = await cl.put("/api/v1/vibe", json={"vibe": "party", "listening": True})
            b = await body(r)
            assert r.status == 200 and b["vibe"] == "party" and b["listening"] is True and d.source == "mode"
            r = await cl.put("/api/v1/vibe?source=api", json={"name": "home"})
            assert (await body(r))["vibe"] == "home" and d.source == "api"
            r = await cl.put("/api/v1/vibe", json="party")
            assert r.status == 200 and d.target["vibe"] == "party"
            boot = (await body(r))["boot_id"]
            assert isinstance(boot, str) and boot
            # degrade, never refuse: an unknown feel is kept as asked (played neutral), and says so
            r = await cl.put("/api/v1/vibe", json={"vibe": "rave"})
            b = await body(r)
            assert r.status == 200 and b["vibe"] == "rave" and b["known"] is False and "rave" in b["problems"][0]
            assert d.target == {"vibe": "rave", "listening": False}         # Party's feel doesn't linger
            r = await cl.put("/api/v1/vibe", json={"vibe": "party", "overrides": {"level": "loud"}})
            b = await body(r)
            assert r.status == 200 and b["overrides"] == {"level": "loud"}  # echoed: the adapter's compare holds
            assert b["problems"] == ["overrides.level: not a range"]
            # no feel: {"vibe": null} and DELETE (the room's put-back of "nothing set")
            r = await cl.put("/api/v1/vibe", json={"vibe": None, "listening": False})
            b = await body(r)
            assert r.status == 200 and b["vibe"] is None and d.target is None and b["boot_id"] == boot
            await cl.put("/api/v1/vibe", json={"vibe": "home"})
            r = await cl.delete("/api/v1/vibe")
            assert r.status == 200 and (await body(r))["vibe"] is None and d.target is None
            # 400 only for a body that isn't a target at all, with the choices
            r = await cl.put("/api/v1/vibe", json={"vibe": 5})
            b = await body(r)
            assert r.status == 400 and b["error"] == "bad_target" and b["choices"] == ["home", "party"]
            r = await cl.put("/api/v1/vibe")
            assert r.status == 400 and (await body(r))["error"] == "bad_target"

    run(go())


# -- Describe it --------------------------------------------------------------------------------------------------------------
def test_describe_end_to_end_with_event(brain):
    lis, d = brain.components["listener"], brain.components["director"]
    brain.ctx.settings = {"listener": {"describe_timeout_s": 3}}

    async def go():
        async with served(make_api(brain)) as cl:
            ws = await cl.ws_connect(c.EVENTS_PATH)
            w = await recv(ws)
            assert w["type"] == "welcome" and w["card"]["describe"]["state"] == "idle"
            r = await cl.post("/api/v1/describe", json={"text": "  a bit   calmer please  " + "x" * 400})
            b = await body(r)
            assert r.status == 200 and b["ok"] and b["via"] == "llm" and b["reply"] == "Calmer, and mellow."
            assert len(b["text"]) == apimod.DESCRIBE_MAX_CHARS and b["text"].startswith("a bit calmer please x")
            assert [x["id"] for x in b["applied"]] == ["steps"]
            assert b["suggest"] == {"mode": "party", "label": "Party time"}
            ev = await until(ws, lambda m: m["type"] == "describe")
            assert ev["result"]["reply"] == b["reply"]
            ctx = lis.contexts[0]
            assert ctx["vibe"] is None and ctx["persona"] == "a warm host" and ctx["current_mode"] == "gallery"
            assert ctx["palettes"][:2] == ["art", "ember"] and ctx["palette_labels"]["art"] == "From the art"
            assert ctx["modes"][1] == {"name": "party", "label": "Party time"} and ctx["moods"] == ["warm", "mellow"]
            r = await cl.get("/api/v1/card")
            assert (await body(r))["describe"]["state"] == "done"
            await ws.close()

    run(go())
    assert d.threads == ["show-brain"]


def test_describe_never_refuses(brain):
    lis = brain.components["listener"]
    brain.ctx.settings = {"listener": {"describe_timeout_s": 0.1}}
    lis.delay_s = 3.0

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.post("/api/v1/describe", json={"text": ""})
            b = await body(r)
            assert r.status == 200 and b["ok"] and b["applied"] == [] and "mellow" in b["reply"]
            t0 = time.monotonic()
            r = await cl.post("/api/v1/describe", json={"text": "wilder"})
            b = await body(r)
            assert r.status == 200 and b["ok"] and "too long" in b["reply"] and time.monotonic() - t0 < 2.5
            r = await cl.post("/api/v1/describe", json={"text": 5})
            assert r.status == 400
        lis.delay_s = 0.0
        lis.intent = {"steps": 0, "palette": None, "moods": None, "keep": False, "switch_mode": "gallery",
                      "reply": ""}
        async with served(make_api(brain)) as cl:
            b = await body(await cl.post("/api/v1/describe", json={"text": "zzz"}))
            assert b["suggest"] is None and "didn't catch" in b["reply"]      # never suggests the current mode

    run(go())


# -- listening, Hue, Auto, situations ------------------------------------------------------------------------------------------
def test_listening_routes(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/listening"))
            assert b["on"] is False and b["why"] == "AI listening is off"
            r = await cl.put("/api/v1/listening", json={"on": True})
            assert r.status == 200 and (await body(r))["on"] is True
            r = await cl.put("/api/v1/listening", json={"on": "yes"})
            assert r.status == 400 and (await body(r))["error"] == "bad_request"

    run(go())


def test_hue_release(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.post("/api/v1/hue/release"))
            assert b["released"] is True and b["via"] == "room" and "P5" in b["detail"]

    run(go())


def test_autopilot_routes(brain):
    ap = brain.components["autopilot"]

    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/autopilot"))
            assert b["state"] == "off" and b["on"] is False
            r = await cl.put("/api/v1/autopilot", json={"on": True})
            b = await body(r)
            assert r.status == 200 and b["on"] is True and b["state"] == "watching"
            ap.unreachable = True
            r = await cl.put("/api/v1/autopilot", json={"on": False})
            b = await body(r)
            assert r.status == 502 and b["error"] == "room_unreachable" and "isn't answering" in b["detail"]
            ap.unreachable, ap.refuse = False, "unknown_target"
            r = await cl.put("/api/v1/autopilot", json={"on": False})
            b = await body(r)
            assert r.status == 502 and b["error"] == "unknown_target" and b["room_status"] == 404
            assert b["detail"] == "The room didn't take that: no such target: autopilot.main"
            r = await cl.put("/api/v1/autopilot", json={})
            assert r.status == 400
            r = await cl.post("/api/v1/autopilot/undo")
            b = await body(r)
            assert r.status == 200 and b["undone"] is False and b["why"] == "nothing_to_undo"
            ap.last_switch = True
            b = await body(await cl.post("/api/v1/autopilot/undo"))
            assert b == {"undone": True, "mode": "gallery", "transition_id": "t9"}
            r = await cl.post("/api/v1/autopilot/resume")
            assert r.status == 200 and (await body(r))["state"] == "watching"     # it was switched on above
            # Auto on/off and Undo each wait on one room PUT: their call outlasts that PUT, never cut off mid-PUT
            assert brain.timeouts["set_on"] == brain.timeouts["undo"] == c.AUTO_CALL_TIMEOUT_S
            assert c.AUTO_CALL_TIMEOUT_S > c.ROOM_PUT_TIMEOUT_S

    run(go())


def test_situations_route(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/situations"))
            assert b["combinators"] == ["all", "any", "not"] and b["vocabulary"][0]["id"] == "quiet"
            assert b["readings"]["music_audible"]["value"] is True

    run(go())


# -- test sources ------------------------------------------------------------------------------------------------------------
def test_test_source_routes(brain):
    src = brain.components["sources"]

    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/test"))
            assert set(b["sources"]) == {"piano", "audio"} and b["pieces"]["piano"][0]["id"] == "file:etude.mid"
            r = await cl.post("/api/v1/test", json={"source": "piano"})
            b = await body(r)
            assert r.status == 200 and b["state"] == "starting" and b["piece"] == "file:etude.mid"
            r = await cl.post("/api/v1/test", json={"source": "audio", "piece": "file:polka.mp3"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "unknown_piece" and b["choices"] == ["file:jazz.mp3"]
            r = await cl.post("/api/v1/test", json={"source": "kazoo"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "unknown_source" and b["choices"] == ["piano", "audio"]
            await cl.post("/api/v1/test", json={"source": "audio"})
            b = await body(await cl.delete("/api/v1/test/piano"))
            assert b == {"stopped": ["piano"]}
            b = await body(await cl.delete("/api/v1/test/piano"))
            assert b == {"stopped": []}                                  # not running: nothing to stop, no error
            b = await body(await cl.delete("/api/v1/test", json={"source": "audio"}))
            assert b == {"stopped": ["audio"]}
            await cl.post("/api/v1/test", json={"source": "audio"})
            b = await body(await cl.delete("/api/v1/test"))
            assert b == {"stopped": ["audio"]} and src.running == {}
            r = await cl.delete("/api/v1/test/kazoo")
            assert r.status == 200 and (await body(r))["stopped"] == []

    run(go())


# -- console ------------------------------------------------------------------------------------------------------------------
def test_look_and_log_routes(brain, tmp_path):
    from show_service import state_store

    p = tmp_path / "autopilot.jsonl"
    for i in range(5):
        state_store.append_jsonl(p, {"i": i})

    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/look"))
            assert b["look"]["look_id"] == "L3" and b["direction"]["loose"] is True
            b = await body(await cl.get("/api/v1/log?kind=director&n=3"))
            assert [e["at"] for e in b["entries"]] == [97, 98, 99]
            b = await body(await cl.get("/api/v1/log?kind=listener&n=99999"))
            assert len(b["entries"]) == apimod.LOG_N_MAX
            r = await cl.get("/api/v1/log?kind=gossip")
            assert r.status == 400 and (await body(r))["choices"] == list(apimod.LOG_KINDS)
        del brain.components["autopilot"]                         # missing: read its jsonl instead
        async with served(make_api(brain, log_files={"autopilot": p})) as cl:
            b = await body(await cl.get("/api/v1/log?kind=autopilot&n=2"))
            assert [e["i"] for e in b["entries"]] == [3, 4] and "log file" in b["detail"]

    run(go())


# -- the card (pure) ---------------------------------------------------------------------------------------------------------
def snap_of(b: FakeBrain) -> dict:
    return Api(lambda: b).snapshot(b)


def test_card_maps_every_component(brain):
    cd = card(snap_of(brain), ENGINE)
    assert set(cd) == set(c.CardData.__annotations__)
    assert cd["hearing"] == {"text": "soul · relaxed", "level": 0.42, "loose": True, "stream": "room-mic",
                             "test": False}
    assert cd["vibe"] == {"name": None, "label": "", "known": False}
    assert [x["name"] for x in cd["palette"]["choices"]] == ["auto", "art", "ember", "sea", "dusk", "moss", "rose"]
    assert cd["palette"]["choices"][0] == apimod.AUTO_CHOICE
    assert cd["palette"]["choices"][1]["label"] == "From the art" and cd["palette"]["pick"] == "auto"
    assert cd["look"] == {"id": "L3", "keep_in_s": None, "next_pending": False}
    assert cd["listening"] == {"on": False, "effective": False, "why": "AI listening is off"}
    assert cd["auto"]["state"] == "off" and cd["auto"]["line"] == "Auto is off"
    assert set(cd["tests"]) == {"piano", "audio"} and cd["describe"]["state"] == "idle"


@pytest.mark.parametrize("beliefs,readings,engine,want", [
    ({"state": "holding", "genre": {"word": "jazz", "conf": 0.6}, "moods": []}, {}, ENGINE, "jazz"),
    ({"state": "fading", "genre": {"word": "jazz", "conf": 0.9}, "moods": []}, {"talking": True}, ENGINE, "Talking"),
    ({"state": "fresh", "genre": {"word": "jazz", "conf": 0.5}, "moods": []}, {"quiet": True}, ENGINE, "Quiet"),
    (None, {"music_audible": True}, {"active_audio": None, "features": {"age_ms": 10}}, "No sound coming in"),
    (None, {"music_audible": True}, {"active_audio": "room-mic", "features": {"age_ms": 6000}}, "No sound coming in"),
    (None, {"music_audible": True}, ENGINE, "Music"),
    (None, {"music_audible": False}, ENGINE, "Listening"),
    ({"state": "fresh", "genre": {"word": "unknown", "conf": 0.9}, "moods": []}, {}, ENGINE, "Listening"),
])
def test_card_hearing_first_match_wins(beliefs, readings, engine, want):
    snap = {"components": {"listener": {"on": True, "beliefs": beliefs},
                           "situations": {"readings": {k: {"value": v} for k, v in readings.items()}}}}
    h = card(snap, engine)["hearing"]
    assert h["text"] == want
    if want == "No sound coming in":
        assert h["level"] == 0.0


def test_card_with_nothing_is_neutral_and_json_safe():
    cd = card(None, None)
    json.dumps(cd, allow_nan=False)
    assert cd["hearing"]["text"] == "No sound coming in" and cd["steps"] == apimod.NEUTRAL_STEPS
    assert cd["listening"]["why"] == apimod.MISSING["listener"]


def test_card_keeps_the_directors_auto_swatch():
    snap = {"components": {"director": {"palette": {"pick": "auto", "current": "ember", "choices": [
        {"name": "auto", "label": "Auto", "family": None, "swatch": ["#112233"]},
        {"name": "art", "label": "From the art", "family": None, "swatch": ["#445566"]}]
        + [{"name": f"p{i}", "label": f"P{i}", "family": "warm", "swatch": []} for i in range(7)]}}}}
    ch = card(snap, ENGINE)["palette"]["choices"]
    assert [x["name"] for x in ch] == ["auto", "art", "p0", "p1", "p2", "p3", "p4"]
    assert ch[0]["swatch"] == ["#112233"] and ch[1]["swatch"] == ["#445566"]


def test_card_test_stream_flag():
    eng = {"active_audio": "test-audio", "features": {"energy": 2.0, "age_ms": 1}}
    h = card({"components": {}}, eng)["hearing"]
    assert h["test"] is True and h["level"] == 1.0


# -- merge patches -----------------------------------------------------------------------------------------------------------
def _rand(rng: random.Random, depth: int = 0):
    r = rng.random()
    if depth > 2 or r < 0.4:
        return rng.choice([0, 1, 2.5, "a", "b", True, False, [1, 2], [rng.randint(0, 3)], None])
    return {k: _rand(rng, depth + 1) for k in rng.sample("abcdef", rng.randint(0, 4))}


def _strip_none(v):
    if isinstance(v, dict):
        return {k: _strip_none(x) for k, x in v.items() if x is not None}
    return v


def test_merge_patch_round_trips():
    rng = random.Random(7)
    for _ in range(2000):
        a, b = _rand(rng), _rand(rng)
        a = a if isinstance(a, dict) else {"x": a}
        b = b if isinstance(b, dict) else {"x": b}
        p = merge_patch(a, b)
        assert apply_patch(_strip_none(a), p) == _strip_none(b)
        assert merge_patch(b, b) == {}


def test_merge_patch_lists_replace_whole_and_null_deletes():
    old = {"chips": [{"id": "a"}], "describe": {"suggest": {"mode": "party"}, "state": "done"}, "n": 1}
    new = {"chips": [], "describe": {"suggest": None, "state": "idle"}, "n": True}
    assert merge_patch(old, new) == {"chips": [], "describe": {"suggest": None, "state": "idle"}, "n": True}


# -- /v1/events --------------------------------------------------------------------------------------------------------------
async def recv(ws, timeout: float = 3.0) -> dict:
    msg = await ws.receive(timeout=timeout)
    assert msg.type == WS.TEXT, (msg.type, msg.data)
    return json.loads(msg.data)


async def until(ws, pred, timeout: float = 3.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        m = await recv(ws, max(0.05, deadline - time.monotonic()))
        if pred(m):
            return m


def test_events_welcome_patch_ping_resync(brain):
    d = brain.components["director"]

    async def go():
        api = make_api(brain)
        async with served(api) as cl:
            ws = await cl.ws_connect(c.EVENTS_PATH)
            w = await recv(ws)
            assert w["type"] == "welcome" and w["v"] == 1 and w["seq"] == 1 and isinstance(w["t_ms"], float)
            local = w["card"]
            assert local == card(snap_of(brain), ENGINE)
            seq = 1
            # a change on show-brain + notify("card") from another thread -> a patch at once
            await brain.call(d.step, "wilder")
            threading.Thread(target=api.notify, args=("card",)).start()
            m = await until(ws, lambda m: m["type"] == "card")
            assert m["seq"] > seq and m["patch"]["steps"]["value"] == 1
            local = apply_patch(local, m["patch"])
            seq = m["seq"]
            # a change nobody announces still arrives within a 2 Hz tick
            await brain.call(d.action, "house")
            t0 = time.monotonic()
            m = await until(ws, lambda m: m["type"] == "card" and "house" in m["patch"])
            assert time.monotonic() - t0 < 1.5 and m["seq"] == seq + 1
            local = apply_patch(local, m["patch"])
            assert local == card(snap_of(brain), ENGINE)
            await ws.send_json({"type": "ping"})
            m = await until(ws, lambda m: m["type"] == "pong")
            await ws.send_str("not json")
            await ws.send_json({"type": "whatever"})
            await ws.send_json({"type": "resync"})
            m2 = await until(ws, lambda m: m["type"] == "welcome")
            assert m2["seq"] > m["seq"] and m2["card"] == card(snap_of(brain), ENGINE)
            # Auto announcements go out as they come
            api.notify("auto", {"kind": "switch", "text": "Auto → Party · music is playing", "mode": "party",
                                "previous": "gallery", "rule": "party/enter_when", "transition_id": "t1",
                                "undo": True, "expires_in_s": 45.0})
            m = await until(ws, lambda m: m["type"] == "auto")
            assert m["event"]["text"].startswith("Auto → Party")
            await ws.close()
        assert api.events.clients == set()

    run(go())


def test_events_seq_is_per_client(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            a = await cl.ws_connect(c.EVENTS_PATH)
            assert (await recv(a))["seq"] == 1
            for _ in range(3):
                await a.send_json({"type": "ping"})
                await until(a, lambda m: m["type"] == "pong")
            b = await cl.ws_connect(c.EVENTS_PATH)
            assert (await recv(b))["seq"] == 1
            await a.close()
            await b.close()

    run(go())


def test_events_too_many_clients(brain):
    async def go():
        async with served(make_api(brain, max_clients=1)) as cl:
            a = await cl.ws_connect(c.EVENTS_PATH)
            await recv(a)
            r = await cl.get(c.EVENTS_PATH)
            b = await body(r)
            assert r.status == 503 and b["error"] == "too_many_clients" and "close one" in b["detail"]
            await a.close()

    run(go())


def test_events_overflow_drops_the_backlog_and_resyncs(brain):
    class StuckWS:
        def __init__(self):
            self.sent = []
            self.gate = asyncio.Event()

        async def send_str(self, s):
            await self.gate.wait()
            self.sent.append(json.loads(s))

    async def go():
        api = make_api(brain, queue_max=8)
        hub = api.events
        hub.loop = asyncio.get_running_loop()
        ws = StuckWS()
        cl = apimod._EvClient(ws, "test")
        hub._welcome(cl)
        hub.clients.add(cl)
        cl.task = asyncio.create_task(hub._sender(cl))
        await asyncio.sleep(0.01)                     # the sender is stuck on the first welcome
        for i in range(20):
            hub.broadcast({"type": "card", "patch": {"n": i}})
        assert len(cl.queue) <= 8 and cl.overflows >= 1
        assert any(kind == "welcome" for kind, _ in cl.queue)
        ws.gate.set()
        await asyncio.sleep(0.05)
        seqs = [m["seq"] for m in ws.sent]
        assert seqs == list(range(1, len(seqs) + 1))   # seq has no gaps: dropped messages never got one
        kinds = [m["type"] for m in ws.sent]
        assert kinds[0] == "welcome" and "welcome" in kinds[1:]
        last_welcome = max(i for i, k in enumerate(kinds) if k == "welcome")
        assert [m["patch"]["n"] for m in ws.sent[last_welcome + 1:]] == list(range(20))[-len(kinds[last_welcome + 1:]):]
        cl.closing = True
        cl.wake.set()
        cl.task.cancel()

    run(go())


def test_notify_before_the_loop_is_dropped_quietly():
    api = apimod.routes(lambda: None)
    api.notify("card")
    api.notify("auto", {"kind": "switch"})
    assert api.events.notified == 0


# -- the bus seam ------------------------------------------------------------------------------------------------------------
def test_bus_mounts_the_api_and_replaces_its_stubs(brain):
    api = make_api(brain)
    b = Bus("127.0.0.1", 0, state_fn=lambda: {"ok": 1}, health_fn=lambda: {}, routes=api)
    b.start()
    try:
        assert b.bind_error is None and b.port not in (0, c.BUS_PORT) and b.routes_error is None
        base = f"http://127.0.0.1:{b.port}"

        async def go():
            async with aiohttp.ClientSession() as s:
                r = await s.post(base + "/api/v1/step", json={"dir": "calmer"})
                assert r.status == 200 and (await r.json())["steps"]["value"] == -1
                r = await s.get(base + "/api/v1/state")
                assert r.status == 200
                ws = await s.ws_connect(base + c.EVENTS_PATH)
                assert (await recv(ws))["type"] == "welcome"
                api.notify("describe", {"ok": True, "via": "keywords", "text": "x", "reply": "ok", "applied": [],
                                        "suggest": None, "working_ms": 1.0})
                m = await until(ws, lambda m: m["type"] == "describe")
                assert m["result"]["reply"] == "ok"
                await ws.close()

        run(go())
        assert {("GET", "/api/v1/card"), ("GET", c.EVENTS_PATH)} <= set(api.mounted)
    finally:
        b.stop()


def test_bus_keeps_its_stubs_when_routes_fail():
    async def half(request):
        return web.json_response({"half": True})

    def broken(app):
        app.router.add_get("/api/v1/card", half)
        raise RuntimeError("api didn't load")

    b = Bus("127.0.0.1", 0, routes=broken)
    b.start()
    try:
        assert b.routes_error == "RuntimeError: api didn't load"
        base = f"http://127.0.0.1:{b.port}"

        async def go():
            async with aiohttp.ClientSession() as s:
                r = await s.post(base + "/api/v1/step", json={})
                assert r.status == 501 and "not built yet" in await r.text()
                r = await s.get(base + "/api/v1/card")             # what it did register still works
                assert r.status == 200

        run(go())
        assert b.stats()["routes_error"] == b.routes_error
    finally:
        b.stop()


def test_bus_latest_on_connect_includes_the_brains_look():
    from show_service import bus as busmod

    assert set(c.LATEST_ON_CONNECT_BRAIN) <= set(busmod.LATEST_ON_CONNECT)
    b = Bus("127.0.0.1", 0)
    b.start()
    try:
        b.publish({"type": c.ROOM_LOOK, "data": {"look_id": "L7"}})
        b.publish({"type": c.ROOM_DIRECTION, "data": {"energy": 0.5}})
        deadline = time.monotonic() + 3
        while b.latest(c.ROOM_DIRECTION) is None and time.monotonic() < deadline:
            time.sleep(0.01)

        async def go():
            async with aiohttp.ClientSession() as s:
                ws = await s.ws_connect(b.url)
                await ws.send_json({"type": "hello", "v": 1, "role": "renderer", "client_id": "t"})
                got = []
                while not {c.ROOM_LOOK, c.ROOM_DIRECTION} <= set(got):
                    m = await recv(ws)
                    got.append(m["type"])
                    if m["type"] == c.ROOM_LOOK:
                        assert m["data"]["look_id"] == "L7"
                assert got[0] == "welcome"
                await ws.close()

        run(go())
    finally:
        b.stop()


def test_card_hearing_reads_music_context_genre_first():
    """README S4.8: music.context's genre (structure's snapshot) at conf >= 0.6 leads, with the listener's first mood
    while its beliefs stand; a low conf, no word or no sound falls back to the beliefs' rule."""
    beliefs = {"state": "fresh", "genre": {"word": "soul", "conf": 0.9}, "moods": [{"word": "relaxed", "conf": 0.8}]}

    def snap(word, conf, b=beliefs):
        return {"components": {"listener": {"on": True, "beliefs": b},
                               "structure": {"context": {"genre": {"word": word, "conf": conf, "parent": "Jazz",
                                                                   "parent_p": 0.4, "via": "ml"}}}}}
    assert card(snap("jazz", 0.7), ENGINE)["hearing"]["text"] == "jazz · relaxed"
    assert card(snap("jazz", 0.7, {"state": "cold"}), ENGINE)["hearing"]["text"] == "jazz"
    assert card(snap("jazz", 0.5), ENGINE)["hearing"]["text"] == "soul · relaxed"      # low conf: the beliefs
    assert card(snap(None, 0.0), ENGINE)["hearing"]["text"] == "soul · relaxed"
    assert card(snap("jazz", 0.9), None)["hearing"]["text"] == "soul · relaxed"        # no sound: not from it
