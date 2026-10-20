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
import yaml
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
        self.mode = False                               # owner, 20 Oct: the mode's permission (show.ai)
        self.delay_s = 0.0
        self.intent = {"steps": -1, "palette": None, "moods": ["mellow"], "keep": False, "switch_mode": "party",
                       "reply": "Calmer, and mellow."}
        self.contexts: list[dict] = []
        self._beliefs = {"state": "fresh", "age_s": 3.0, "genre": {"word": "soul", "conf": 0.8},
                         "moods": [{"word": "relaxed", "conf": 0.7}], "instruments": [], "vocals": None,
                         "section": None, "trajectory": None, "content": "music"}
        self.extra: dict = {}                           # README S5.20: spend, provider, estimate

    def view(self) -> dict:
        return {**apimod.neutral_listener(None if self.on else "AI listening is off"), "on": self.on,
                "mode": self.mode, **self.extra}

    def set_on(self, on: bool) -> dict:
        self.on = on
        return self.view()

    def set_mode(self, mode: bool) -> dict:
        self.mode = mode
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
        self.checked: list[tuple] = []

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

    def rules(self) -> dict:                  # README S5.19: autopilot.Autopilot.rules's shape
        return {"modes": [{"mode": "piano", "label": "Piano", "enter": "the piano is playing, for at least 10 s",
                           "suggest": None, "leave": "the piano has stopped, for at least 2 min", "from": ["gallery"],
                           "leave_to": "previous", "problems": [], "available": True,
                           "live": {"enter_when": False, "leave_when": True}, "nodes": {}, "now": {}, "auto": {},
                           "summary": "Auto switches to Piano when …", "unknown_words": []}],
                "revision": "r1", "reachable": True, "error": None, "thread": _thread()}

    def check_block(self, mode, block, extra=None) -> dict:
        self.checked.append((mode, block, extra, _thread()))
        bad = isinstance(block, dict) and "nope" in json.dumps(block)
        return {"ok": not bad, "problems": [{"path": "enter_when", "text": "“nope” isn't something Auto can notice",
                                             "level": "error"}] if bad else [],
                "enter": None, "suggest": None, "leave": None, "unknown_words": ["nope"] if bad else []}

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
def test_everything_answers_starting_before_the_brain_runs(tmp_path):
    api = apimod.routes(lambda: None, lambda: ENGINE, patch_path=tmp_path / "patch.yaml",   # none: room streams
                        fixtures_path=tmp_path / "fixtures.yaml")

    async def go():
        async with served(api) as cl:
            for method, path in apimod.ROUTES:
                if path in (c.EVENTS_PATH, "/api/v1/hue/release"):
                    continue
                path = path.replace("{id}", "all").replace("{source}", "piano").replace("{name}", "piano") \
                    .replace("{n}", "0")
                r = await cl.request(method, path, json={})
                assert r.status == 503, (method, path)
                b = await body(r)
                assert b["error"] == "starting" and "starting" in b["detail"]
            r = await cl.get(c.EVENTS_PATH)
            assert r.status == 503 and (await body(r))["error"] == "starting"
            r = await cl.post("/api/v1/hue/release")          # never blocks a mode's exit
            assert r.status == 200 and (await body(r))["released"] is True

    run(go())


@pytest.mark.parametrize("streamer,hue_on,want", [("show", True, False), ("show", False, True), ("room", True, True),
                                                  (None, True, True), ("garbage", True, False),
                                                  ("missing", True, True)])
def test_a_hue_release_before_the_brain_runs(tmp_path, streamer, hue_on, want):
    """Before the brain runs a release answers at once (a mode's exit never waits on show_service). Under patch.yaml
    hue.streamer: show with a hue output on, a crashed predecessor may still hold the area: released false, why
    "starting", so room checks the bridge itself. A patch.yaml that doesn't read counts as show (always safe)."""
    patch, fx = tmp_path / "patch.yaml", tmp_path / "fixtures.yaml"
    if streamer == "garbage":
        patch.write_text("hue: [unclosed\n")
    elif streamer != "missing":
        patch.write_text(yaml.safe_dump({"hue": {"bridge": "b1", **({"streamer": streamer} if streamer else {})}}))
    fx.write_text(yaml.safe_dump({"outputs": {"hue": {"driver": "hue", "enabled": hue_on},
                                              "dmx-a": {"driver": "artnet", "enabled": True}}}))
    api = apimod.routes(lambda: None, lambda: ENGINE, patch_path=patch, fixtures_path=fx)

    async def go():
        async with served(api) as cl:
            r = await cl.post("/api/v1/hue/release", json={"lights": ["hue.ne"]})
            b = await body(r)
            assert r.status == 200 and b["released"] is want, b
            if want:
                assert b["via"] == "room" and b["state"] == "idle"
            else:
                assert b["why"] == "starting" and b["via"] == "show" and b["state"] in c.HUE_ROOM_STATES
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
                    ("GET", "/api/v1/autopilot/rules", None),
                    ("POST", "/api/v1/autopilot/check", {"mode": "piano", "autopilot": {}}),
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


def test_listening_takes_the_modes_permission_and_carries_boot_id(brain):
    """Owner, 20 Oct: room_service's per-mode show.ai PUTs {"mode": bool}; the Room page's {"on": bool} (or a bare
    bool) stays the master switch. Every answer carries "mode" and this process's boot_id (room's re-sync)."""
    lis = brain.components["listener"]

    async def go():
        api = make_api(brain)
        async with served(api) as cl:
            b = await body(await cl.get("/api/v1/listening"))
            assert b["mode"] is False and b["on"] is False and b["boot_id"] == api.boot_id    # never set: off
            r = await cl.put("/api/v1/listening", json={"mode": True})
            b = await body(r)
            assert r.status == 200 and b["mode"] is True and b["on"] is False and b["boot_id"] == api.boot_id
            assert lis.mode is True and lis.on is False
            r = await cl.put("/api/v1/listening", json={"on": True})
            b = await body(r)
            assert r.status == 200 and b["on"] is True and b["mode"] is True and lis.mode is True
            r = await cl.put("/api/v1/listening", data="false", headers={"Content-Type": "application/json"})
            b = await body(r)
            assert r.status == 200 and b["on"] is False and lis.on is False and lis.mode is True   # bare bool = on
            r = await cl.put("/api/v1/listening", json={"on": True, "mode": False})
            b = await body(r)
            assert r.status == 200 and (b["on"], b["mode"]) == (True, False) and (lis.on, lis.mode) == (True, False)
            for bad in ({}, {"mode": "x"}, {"on": True, "mode": 1}, {"mode": None}, "yes", None, [True]):
                r = await cl.put("/api/v1/listening", json=bad)
                assert r.status == 400 and (await body(r))["error"] == "bad_request", bad
            r = await cl.put("/api/v1/listening")                            # no body at all
            assert r.status == 400
            assert (lis.on, lis.mode) == (True, False)                        # nothing changed by a bad body
    run(go())


def test_listening_without_a_listener_is_neutral_with_mode_off():
    b = FakeBrain([])

    async def go():
        api = make_api(b)
        async with served(api) as cl:
            for r in (await cl.get("/api/v1/listening"), await cl.put("/api/v1/listening", json={"mode": True})):
                got = await body(r)
                assert r.status == 200 and got["mode"] is False and got["on"] is False
                assert got["why"] == apimod.MISSING["listener"] and got["boot_id"] == api.boot_id
    try:
        run(go())
    finally:
        b.close()


def test_hue_release(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.post("/api/v1/hue/release"))
            assert b["released"] is True and b["via"] == "room" and b["state"] == "idle"
            assert "nothing streams Hue from show_service" in b["detail"]

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


def test_autopilot_rules_and_check_routes(brain):
    """README S5.19: both run on show-brain; the check takes the mode, the block and the editor's unsaved modes, and
    400s only a body that isn't what the route takes."""
    ap = brain.components["autopilot"]

    async def go():
        async with served(make_api(brain)) as cl:
            r = await cl.get("/api/v1/autopilot/rules")
            b = await body(r)
            assert r.status == 200 and b["thread"] == "show-brain" and b["modes"][0]["leave_to"] == "previous"
            blk = {"enter_when": {"piano_playing": {"for_s": 10}}, "from": ["gallery", "late-night"]}
            r = await cl.post("/api/v1/autopilot/check", json={"mode": " piano ", "autopilot": blk,
                                                               "modes": ["late-night"]})
            b = await body(r)
            assert r.status == 200 and b["ok"] is True
            assert ap.checked[-1] == ("piano", blk, ["late-night"], "show-brain")
            r = await cl.post("/api/v1/autopilot/check", json={"autopilot": {"enter_when": {"nope": {}}}})
            b = await body(r)
            assert r.status == 200 and b["ok"] is False and b["unknown_words"] == ["nope"]   # a bad block: 200, words
            assert ap.checked[-1][0] is None and ap.checked[-1][2] == []
            await cl.post("/api/v1/autopilot/check", json={"mode": "disco"})                 # no block: only by hand
            assert ap.checked[-1][:3] == ("disco", None, [])
            for bad in ([1], "piano", {"mode": 3}, {"mode": " "}, {"modes": "late"}, {"modes": [""]}):
                r = await cl.post("/api/v1/autopilot/check", json=bad)
                assert r.status == 400 and (await body(r))["error"] == "bad_request", bad
            r = await cl.post("/api/v1/autopilot/check", data=b"{not json", headers={"Content-Type": "application/json"})
            assert r.status == 400
            assert ("GET", "/api/v1/autopilot/rules") in apimod.ROUTES and ("POST", "/api/v1/autopilot/check") in \
                apimod.ROUTES

    run(go())


def test_auto_rules_and_check_without_auto_degrade():
    b = FakeBrain([FakeRoom()])

    async def go():
        async with served(make_api(b)) as cl:
            r = await cl.get("/api/v1/autopilot/rules")
            got = await body(r)
            assert r.status == 200 and got["modes"] == [] and got["detail"] == apimod.MISSING["autopilot"]
            r = await cl.post("/api/v1/autopilot/check", json={"mode": "piano", "autopilot": {}})
            got = await body(r)
            assert r.status == 200 and got["ok"] is False and got["checked"] is False and got["problems"]

    try:
        run(go())
    finally:
        b.close()


def test_situations_route_lists_the_light_words_with_their_schemas():
    """The real Situations behind GET /api/v1/situations: the light words, their params' JSON schemas and hints."""
    from show_service.situations import Situations
    t = [1000.0]
    sit = Situations(c.BrainContext(publish=lambda m: None), mono=lambda: t[0], wall=lambda: 1.79e9 + t[0])
    sit.configure({"situations": {"light": {"url": "http://127.0.0.1:9/api/lightsensor/lightsense",
                                            "sensor": "living-room"}}})
    assert sit.light.ingest({"sensors": {"living-room": {"current": {"lux": 30.0}, "history": [
        {"timestamp": 1.79e9 + t[0], "lux": 30.0}]}}})
    sit.tick(t[0])
    b = FakeBrain([sit])

    async def go():
        async with served(make_api(b)) as cl:
            got = await body(await cl.get("/api/v1/situations"))
            voc = {d["id"]: d for d in got["vocabulary"]}
            assert {"light_below", "light_above"} <= set(voc) and set(voc) == set(c.SITUATIONS)
            lb = voc["light_below"]
            assert lb["label"] == "the room is darker than" and lb["source"] == "light" and lb["available"] is True
            assert set(lb["params"]) == {"for_s", "lux", "sensor"}
            lux = lb["params"]["lux"]
            assert lux["type"] == "number" and lux["minimum"] == 0
            assert lux["x-ui"]["label"] == "Light level" and lux["x-ui"]["unit"] == "lux" and lux["x-ui"]["required"]
            sensor = lb["params"]["sensor"]
            assert sensor["default"] == "living-room" and sensor["enum"] == ["living-room"]
            assert sensor["x-ui"]["enum_labels"] == ["Living room"] and sensor["x-ui"]["unset_label"] == "The only sensor"
            assert lb["params"]["for_s"]["x-ui"] == {"label": "For at least", "unit": "s", "required": False}
            assert got["readings"]["light_below"]["value"] is None          # needs a rule's lux
            assert got["combinators"] == ["all", "any", "not"]

    try:
        run(go())
    finally:
        b.close()


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
                             "test": False, "input": None, "input_trace": None,
                             "mic": None}
    assert cd["vibe"] == {"name": None, "label": "", "known": False}
    assert [x["name"] for x in cd["palette"]["choices"]] == ["auto", "art", "ember", "sea", "dusk", "moss", "rose"]
    assert cd["palette"]["choices"][0] == apimod.AUTO_CHOICE
    assert cd["palette"]["choices"][1]["label"] == "From the art" and cd["palette"]["pick"] == "auto"
    assert cd["look"] == {"id": "L3", "keep_in_s": None, "next_pending": False}
    assert cd["listening"] == {"on": False, "effective": False, "why": "AI listening is off", "mode": False,
                               "spend_today_usd": 0.0,
                               "est_evening_usd": None, "provider": None, "character": None, "approach": None,
                               "armed": []}
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
    assert cd["listening"]["est_evening_usd"] is None and cd["listening"]["spend_today_usd"] == 0.0


ESTIMATE = {"per_call_usd": 0.00403, "per_hour_usd": 0.725, "per_evening_usd": 2.9, "evening_h": 4.0,
            "cadence_s": 20.0, "model": "gemini-3.8-flash", "provider": "Google", "basis": "price table"}


def test_card_and_listening_show_the_cost_even_while_off(brain):
    """README S5.20: the Music card shows today's spend, the evening's estimate and the provider before the switch
    is on; GET /api/v1/listening carries the whole estimate."""
    lis = brain.components["listener"]
    lis.extra = {"spend_today_usd": 0.12345, "est_evening_usd": 2.9, "provider": "Google", "estimate": ESTIMATE,
                 "model": "gemini-3.8-flash"}
    cd = card(snap_of(brain), ENGINE)
    assert cd["listening"] == {"on": False, "effective": False, "why": "AI listening is off", "mode": False,
                               "spend_today_usd": 0.1235, "est_evening_usd": 2.9, "provider": "Google",
                               "character": None, "approach": None, "armed": []}
    # README S5.22: the listener's line, its approach and the cue sheet ride on the card's listening block
    sheet = [{"kind": "chorus", "words": "Big change at the chorus, likely in 10-25 s", "window_s": [10.0, 25.0],
              "size": "big"}]
    lis.extra = dict(lis.extra, character="soft late-night jazz trio", armed=sheet,
                     approach={"intensity": 0.2, "change": "slow", "transitions": "gentle", "conf": 0.8})
    li = card(snap_of(brain), ENGINE)["listening"]
    assert li["character"] == "soft late-night jazz trio" and li["armed"] == sheet
    assert li["approach"] == {"intensity": 0.2, "change": "slow", "transitions": "gentle", "conf": 0.8}
    lis.extra = {k: v for k, v in lis.extra.items() if k not in ("character", "armed", "approach")}

    async def go():
        async with served(make_api(brain)) as cl:
            b = await body(await cl.get("/api/v1/listening"))
            assert b["on"] is False and b["estimate"] == ESTIMATE and b["provider"] == "Google"
            assert b["est_evening_usd"] == 2.9
    run(go())
    assert set(apimod.neutral_listener("x")) == set(c.ListenerView.__annotations__)


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


# -- Stage 5: the rig and the Hue routes (README S5.4) ----------------------------------------------------------------
def _rig(hue_enabled=True):
    """A real outputs.Rig on test_outputs' fakes: a fake registry (a hue output, an artnet output that is off, a
    node), a fake engine and fake drivers. Nothing is sent anywhere."""
    from show_service import outputs as om
    from show_service.tests import test_outputs as to
    reg = to.FakeRegistry(to.view({"o-hue": to.out("o-hue", "hue", enabled=hue_enabled),
                                   "o-dmx": to.out("o-dmx", "artnet", enabled=False)}))
    rig = to.make(reg, to.FakeEngine(), settings={"rig": {"enabled": True, "hue": {"release_timeout_s": 3}}})
    rig.start()
    assert isinstance(rig, om.Rig)
    return rig


def test_rig_routes_without_a_rig_component(brain):
    api = make_api(brain)

    async def go():
        async with served(api) as cl:
            b = await body(await cl.get("/api/v1/rig"))
            assert b["enabled"] is False and "isn't running" in b["why"]
            r = await cl.get("/api/v1/rig/frame?output=hue")
            assert r.status == 404 and (await body(r))["error"] == "unknown_output"
            r = await cl.post("/api/v1/rig/nodes/n1/record-failsafe")
            assert r.status == 404 and (await body(r))["error"] == "unknown_node"
            assert (await body(await cl.get("/api/v1/rig/dmx"))) == {"nodes": {}}
            st = await body(await cl.post("/api/v1/hue/stream", json={"lights": ["hue.ne"], "on": True}))
            assert st["state"] == "idle" and set(st) == set(c.HueAnswer.__annotations__)   # 68's four words
            assert st["words"] == "Not following" and "isn't running" in st["detail"]
            assert st["lights"] == [] and st["boot_id"] == api.boot_id                  # 68's addendum
            hs = await body(await cl.get("/api/v1/hue/status"))
            assert hs["state"] == "idle" and hs["lights"] == [] and hs["boot_id"] == api.boot_id
            for bad in ({"lights": "hue.ne"}, {"lights": [1]}, {"lights": [], "on": "yes"}):
                r = await cl.post("/api/v1/hue/stream", json=bad)
                assert r.status == 400 and (await body(r))["error"] == "bad_request", bad
            b = await body(await cl.post("/api/v1/hue/release", json={"lights": ["hue.ne"]}))
            assert b["released"] is True and b["via"] == "room" and b["state"] == "idle"
    run(go())


def test_rig_routes_with_a_rig(brain):
    rig = _rig()
    brain.components["rig"] = rig
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                snap = await body(await cl.get("/api/v1/rig"))
                assert snap["enabled"] is True and snap["registry_view"]["revision"] == "r1"
                assert snap["outputs"]["o-hue"]["state"] == "sending" and snap["outputs"]["o-dmx"]["state"] == "off"
                r = await cl.get("/api/v1/rig/frame?output=nope")
                b = await body(r)
                assert r.status == 404 and b["error"] == "unknown_output" and b["choices"] == ["o-dmx", "o-hue"]
                r = await cl.get("/api/v1/rig/frame")
                assert r.status == 404 and "?output=" in (await body(r))["detail"]
                for _ in range(50):
                    fv = await body(await cl.get("/api/v1/rig/frame?output=o-hue"))
                    if fv["fixtures"]:
                        break
                    await asyncio.sleep(0.02)
                assert fv["output"] == "o-hue" and fv["fixtures"]["lamp.a"]["dmx"] == [1, 2]
                st = await body(await cl.post("/api/v1/hue/stream", json={"lights": ["lamp.a"], "on": True}))
                assert st["state"] == "idle" and rig.drivers_of("hue")[0].requests == [(["lamp.a"], True)]
                assert (await body(await cl.get("/api/v1/hue/status")))["state"] == "idle"
                rel = await body(await cl.post("/api/v1/hue/release", json={"lights": ["lamp.a"]}))
                assert rel["via"] == "show" and rel["released"] is True
                assert brain.timeouts["hue_release"] == 5.0                 # release_timeout_s + 2
                r = await cl.post("/api/v1/hue/release", json={"lights": "lamp.a"})
                assert r.status == 400
                r = await cl.post("/api/v1/rig/nodes/n1/record-failsafe")
                assert r.status == 200 and (await body(r))["recorded"] is False
                r = await cl.post("/api/v1/rig/nodes/zz/record-failsafe")
                assert r.status == 404 and (await body(r))["choices"] == ["n1"]
        run(go())
    finally:
        rig.stop()


def test_hue_routes_with_the_hue_output_off(brain):
    rig = _rig(hue_enabled=False)
    brain.components["rig"] = rig
    api = make_api(brain)
    try:
        async def go():
            async with served(api) as cl:
                st = await body(await cl.post("/api/v1/hue/stream", json={"lights": ["lamp.a"], "on": True}))
                assert st["state"] == "idle" and st["wanted"] == []
                assert st["detail"] == "outputs.o-hue.enabled is false in config/room/fixtures.yaml"
                assert st["lights"] == ["lamp.a"] and st["boot_id"] == api.boot_id     # kept for on again
                hs = await body(await cl.get("/api/v1/hue/status"))
                assert hs["lights"] == ["lamp.a"] and hs["boot_id"] == api.boot_id
                b = await body(await cl.post("/api/v1/hue/release"))
                assert b["released"] is True and b["via"] == "room" and b["state"] == "idle"
        run(go())
    finally:
        rig.stop()


@pytest.mark.parametrize("state,wanted,want", [("streaming", ["lamp.a"], "streaming"),
                                               ("waiting_input", ["lamp.a"], "armed"),
                                               ("starting", ["lamp.a"], "armed"),
                                               ("error", ["lamp.a"], "armed"),
                                               ("taken_over", ["lamp.a"], "taken_over"),
                                               ("waiting_room", ["lamp.a"], "idle"),
                                               ("off", ["lamp.a"], "idle"),
                                               ("idle", [], "idle")])
def test_hue_routes_answer_exactly_the_four_room_states(brain, state, wanted, want):
    """68's contract: stream and status answer {state: idle|armed|streaming|taken_over}; the driver's own word stays in
    GET /api/v1/rig."""
    rig = _rig()
    d = rig.drivers_of("hue")[0]
    d.hue_status = lambda: {**om_hue_off(d.output_id), "state": state, "wanted": list(wanted)}
    brain.components["rig"] = rig
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                st = await body(await cl.post("/api/v1/hue/stream", json={"lights": ["lamp.a"], "on": True}))
                assert st["state"] == want and st["words"] == c.HUE_WORDS[want]
                st = await body(await cl.get("/api/v1/hue/status"))
                assert st["state"] == want and st["state"] in c.HUE_ROOM_STATES
                snap = await body(await cl.get("/api/v1/rig"))
                assert snap["hue"]["state"] == state                     # the richer word, elsewhere
        run(go())
    finally:
        rig.stop()


def om_hue_off(oid):
    from show_service import outputs as om
    return om.hue_off(oid, "fake")


class _RoomPatch:
    """What room's HueAdapter reads of its Patch: ``hue`` (the switch, show_url) and ``of_kind("hue")``."""

    def __init__(self, show_url: str) -> None:
        self.hue = {"streamer": "show", "show_url": show_url, "writes_enabled": False}
        self._devs = [types.SimpleNamespace(id=f"hue.{m}", label=m.upper(), extra={"light": f"light-{m}"})
                      for m in ("ne", "nw", "se", "sw")]

    def of_kind(self, kind):
        return list(self._devs) if kind == "hue" else []


class _RoomBridge:
    """room's REST client stand-in (GET /light only); never a bridge."""

    def __init__(self) -> None:
        self.lights = [{"id": f"light-{m}", "on": {"on": False}, "mode": "normal"} for m in ("ne", "nw", "se", "sw")]

    async def get(self, rtype, rid=None):
        return [dict(x) for x in self.lights] if rtype == "light" else []

    async def close(self):
        pass


def test_rooms_hue_adapter_adopts_our_set_then_re_sends_after_a_new_boot_or_a_stale_set(brain):
    """68's addendum, end to end against room's real HueAdapter (room_service.adapters.hue, read-only import) and these
    routes: GET /hue/status carries lights (what room asked, unfiltered) and boot_id.
    - room restarted, show_service still following (its persisted set): room's first read adopts our set, with no
      POST, and reads those lamps "dmx" (not "taken_over");
    - show_service restarted (a new boot_id): room re-sends its set;
    - show_service restored a stale set: room re-sends its own."""
    pytest.importorskip("room_service.adapters.hue")
    from room_service.adapters.hue import HueAdapter
    from show_service import outputs as om
    from show_service.tests import test_outputs as to

    class AskedDriver(to.FakeDriver):
        def __init__(self, oid, **kw):
            super().__init__(oid, **kw)
            self.asked: list = []

        def hue_status(self):
            return om.hue_off(self.output_id, "fake") | {"state": "waiting_input", "asked": list(self.asked),
                                                         "wanted": [a for a in self.asked if a.startswith("hue.")]}

        def request(self, lights, on):
            self.requests.append((list(lights), on))
            self.asked = list(dict.fromkeys(lights)) if on else []
            return self.hue_status()

    reg = to.FakeRegistry(to.view({"o-hue": to.out("o-hue", "hue")}))
    rig = to.make(reg, to.FakeEngine(), factories={"hue": lambda oid, **k: AskedDriver(oid, kind="hue", **k)},
                  settings={"rig": {"enabled": True}})
    rig.start()
    d = rig.drivers_of("hue")[0]
    d.asked = ["hue.ne", "hue.sw"]                             # restored after a restart (state.json)
    brain.components["rig"] = rig
    api = make_api(brain)
    try:
        async def go():
            async with served(api) as cl:
                room = HueAdapter(types.SimpleNamespace(client=None, wanted=set(), state="show"),
                                  _RoomPatch(str(cl.make_url("")).rstrip("/")), client=_RoomBridge())
                try:
                    members = ["ne", "nw", "se", "sw"]
                    got = await room.read(members)                # room's first contact since its restart
                    assert room.show_wanted == {"hue.ne", "hue.sw"} and d.requests == []          # adopted, no POST
                    assert {m: r.value for m, r in got.items()} == {"ne": "dmx", "nw": "off", "se": "off",
                                                                     "sw": "dmx"}
                    await room.read(members)                      # in step: nothing sent
                    assert d.requests == [] and room._show_boot == api.boot_id
                    api.boot_id = "a-new-process"                 # show_service restarted
                    await room.read(members)
                    assert d.requests == [(["hue.ne", "hue.sw"], True)] and room._show_boot == "a-new-process"
                    await room.read(members)
                    assert len(d.requests) == 1                   # in step again
                    d.asked = ["hue.nw"]                          # a stale restored set
                    await room.read(members)
                    assert d.requests[-1] == (["hue.ne", "hue.sw"], True) and d.asked == ["hue.ne", "hue.sw"]
                finally:
                    await room.close()
        run(go())
    finally:
        rig.stop()


def test_card_hearing_says_why_nothing_arrives():
    from show_service.api import NO_DEVICE, NO_SENDER, NOT_ANSWERING, STOPPED

    def words(inputs, active=None):
        return card(None, {"active_audio": active, "features": None, "inputs": inputs})["hearing"]["text"]

    assert words({"senders": 0, "room": {}}) == NO_SENDER
    assert words({"senders": 1, "room": {}}) == NOT_ANSWERING
    assert words({"senders": 1, "room": {"room-mic": "no_device"}}) == NO_DEVICE
    assert words({"senders": 1, "room": {"room-mic": "down"}}) == STOPPED
    assert words({"senders": 1, "room": {"room-mic": "stale"}}) == STOPPED
    assert words(None) == "No sound coming in"                  # an engine without inputs reads as before
    live = {"senders": 1, "room": {"room-mic": "quiet"}}
    assert words(live) == "No sound coming in"                  # a room stream is up: the old words


def test_card_hearing_carries_the_raw_input_meter():
    trace = {"seq": 42, "hz": 10.0, "peak_dbfs": [-20.0, -12.5], "rms_dbfs": [-40.0, -33.0]}
    eng = {"active_audio": "pc22-mic", "features": {"energy": 0.5, "age_ms": 10.0, "stream": "pc22-mic"},
           "input": {"peak_dbfs": -12.5, "rms_dbfs": -33.0, "trace": trace}}
    h = card(None, eng)["hearing"]
    assert h["input"] == {"peak_dbfs": -12.5, "rms_dbfs": -33.0}
    assert h["input_trace"] == trace
    h = card(None, {"active_audio": None, "features": None, "input": eng["input"]})["hearing"]
    assert h["input"] is None and h["input_trace"] is None


def test_card_says_mic_muted_or_off_when_that_is_why_nothing_arrives():
    """README S5.16: the mic on demand's words win over the connection words; with sound coming in (a test stream)
    the usual words; the card carries the mic's state as mic_state."""
    from show_service.api import MIC_MUTED, MIC_OFF, MIC_STARTING, STOPPED
    off = {"on": False, "muted": False, "wanted": False, "why": "Off: nothing needs it", "claims": [], "mode": False,
           "streams": {"capturing": [], "off": ["room-mic"]}, "error": None}
    muted = {**off, "muted": True, "wanted": True, "why": "Muted: Auto can't hear the room",
             "claims": [{"by": "auto", "label": "Auto"}]}
    on = {**off, "on": True, "wanted": True, "why": "Listening for Auto", "claims": [{"by": "auto", "label": "Auto"}],
          "streams": {"capturing": ["room-mic"], "off": []}}
    inputs = {"senders": 1, "room": {"room-mic": "off"}}

    def hearing(mic_state, active=None, inp=inputs):
        eng = {"active_audio": active, "features": {"energy": 0.5, "age_ms": 10.0, "stream": active} if active else None,
               "inputs": inp, "mic_state": mic_state}
        return card(None, eng)

    cd = hearing(off)
    assert cd["hearing"]["text"] == MIC_OFF and cd["hearing"]["level"] == 0.0 and cd["mic_state"] == off
    assert cd["mic_state"] is not off                                  # a copy, never the mic's own body
    assert hearing(muted)["hearing"]["text"] == MIC_MUTED
    assert hearing(on)["hearing"]["text"] == MIC_STARTING              # on, the sender hasn't opened it yet
    assert hearing(on, inp={"senders": 1, "room": {"room-mic": "down"}})["hearing"]["text"] == STOPPED
    assert hearing(off, active="test-audio")["hearing"]["text"] == "Listening"      # sound comes in: not the mic's words
    assert hearing(None)["mic_state"] is None and hearing(None)["hearing"]["text"] == MIC_STARTING
    json.dumps(hearing(muted), allow_nan=False)


def test_room_why_is_the_cards_words_in_short():
    """room.why (docs/LIVE_DIAGNOSTICS.md v2): Auto's line and last switch, the listening why, the mic, the last look's
    reason; every text cut to WHY_TEXT_MAX."""
    from show_service import api as A
    snap = {"components": {"autopilot": {"last": {"from": "piano", "to": "gallery", "from_label": "Piano",
                                                  "to_label": "Gallery", "age_s": 3.0,
                                                  "reason": "the piano has stopped (piano/leave_when)"}},
                           "director": {"last_look": {"why": "a new section", "age_s": 12.4}}}}
    card_body = {"auto": {"on": True, "line": "Auto · Gallery" + "x" * 200},
                 "listening": {"on": True, "effective": False, "why": "Auto is off"},
                 "mic_state": {"on": False, "why": "Off: nothing needs it"}}
    w = A.why_of(snap, card_body, wall_ms=1000.0)
    assert set(w) == set(c.WhyData.__annotations__)
    assert w["auto"]["last"] == "Piano -> Gallery: the piano has stopped"
    assert len(w["auto"]["line"]) == c.WHY_TEXT_MAX
    assert w["listening"] == {"on": True, "effective": False, "why": "Auto is off"}
    assert w["mic"] == {"on": False, "why": "Off: nothing needs it"}
    assert w["look"] == {"why": "a new section", "age_s": 12}
    assert c.ROOM_WHY in c.WANT_ONLY_STAGE5
    w = A.why_of(None, None, wall_ms=0.0)                            # nothing yet: still the shape
    assert w["auto"]["last"] is None and w["look"]["why"] is None


# -- README S5.23: the Lights page's routes ----------------------------------------------------------------------------
def test_lights_routes_without_a_rig_component(brain):
    async def go():
        async with served(make_api(brain)) as cl:
            types_ = await body(await cl.get("/api/v1/rig/types"))
            assert isinstance(types_, list) and types_[0]["id"] == "generic-dimmer"     # the generic types still list
            for method, path in (("POST", "/api/v1/rig/fixtures"), ("PUT", "/api/v1/rig/fixtures/x"),
                                 ("DELETE", "/api/v1/rig/fixtures/x")):
                r = await cl.request(method, path, json={"label": "x"})
                assert r.status == 503 and (await body(r))["error"] == "unavailable", (method, path)
            r = await cl.post("/api/v1/rig/follow", json={"fixtures": {"dmx.a": "dmx"}})
            assert r.status == 503 and (await body(r))["error"] == "unavailable"           # room's adapter retries
            assert (await body(await cl.get("/api/v1/rig/follow"))) == {"fixtures": {}}
            got = await body(await cl.post("/api/v1/rig/fixtures/x/identify"))
            assert got["ok"] is False and "isn't running" in got["why"]
    run(go())


def test_lights_routes_with_a_rig(brain, tmp_path):
    from show_service.tests import test_outputs as to
    eng = to.LightsEngine()
    rig = to.lights_rig(tmp_path, engine=eng)
    rig.start()
    brain.components["rig"] = rig
    body_ = dict(to.BODY)
    try:
        async def go():
            async with served(make_api(brain)) as cl:
                types_ = await body(await cl.get("/api/v1/rig/types"))
                assert [t["id"] for t in types_][:3] == ["generic-dimmer", "generic-rgb", "generic-rgbw"]
                assert set(types_[0]) == set(c.RigTypeRow.__annotations__)
                # add
                r = await cl.post("/api/v1/rig/fixtures", json=body_)
                got = await body(r)
                assert r.status == 200 and got["problems"] == [] and got["fixture"]["id"] == "dmx.ball-spot"
                assert set(got["fixture"]) == set(c.RigFixtureRow.__annotations__)
                snap = await body(await cl.get("/api/v1/rig"))
                rows = {x["id"]: x for x in snap["fixtures"]}
                assert set(rows) == {"hue.a", "dmx.ball-spot"} and rows["dmx.ball-spot"]["universe"] == 3
                assert "dmx-192-0-2-10-u3" in snap["outputs"]
                # refusals: page words, problems, choices; nothing written
                r = await cl.post("/api/v1/rig/fixtures", json={**body_, "address": 510})
                b = await body(r)
                assert r.status == 400 and b["error"] == "not_added" and b["problems"] and "512" in b["detail"]
                r = await cl.post("/api/v1/rig/fixtures", json={**body_, "type": "sm-hue"})
                b = await body(r)
                assert r.status == 400 and b["error"] == "unknown_type" and b["choices"][0]["id"] == "generic-dimmer"
                r = await cl.post("/api/v1/rig/fixtures", data=b"{nope")
                assert r.status == 400 and (await body(r))["error"] == "bad_request"
                r = await cl.put("/api/v1/rig/fixtures/hue.a", json={"label": "x"})
                assert r.status == 409 and (await body(r))["error"] == "in_hand_file"
                r = await cl.put("/api/v1/rig/fixtures/dmx.nope", json={"label": "x"})
                b = await body(r)
                assert r.status == 404 and b["error"] == "unknown_fixture" and b["choices"] == ["dmx.ball-spot"]
                # update
                b = await body(await cl.put("/api/v1/rig/fixtures/dmx.ball-spot", json={"label": "Ball",
                                                                                         "zone": None}))
                assert b["fixture"]["label"] == "Ball" and b["fixture"]["zone"] is None
                # identify
                b = await body(await cl.post("/api/v1/rig/fixtures/dmx.ball-spot/identify", json={"seconds": 2}))
                assert b == {"ok": True, "seconds": 2.0} and eng.identified == [("dmx.ball-spot", 2.0)]
                b = await body(await cl.post("/api/v1/rig/fixtures/dmx.ball-spot/identify"))
                assert b == {"ok": True, "seconds": 3.0}
                r = await cl.post("/api/v1/rig/fixtures/zz/identify")
                b = await body(r)
                assert r.status == 404 and b["error"] == "unknown_fixture" and "dmx.ball-spot" in b["choices"]
                r = await cl.post("/api/v1/rig/fixtures/dmx.ball-spot/identify", json={"seconds": "soon"})
                assert r.status == 400
                # follow
                b = await body(await cl.post("/api/v1/rig/follow", json={"fixtures": {
                    "dmx.ball-spot": {"hex": "#00FF00", "bri": 50}}}))
                assert b["accepted"] is True and b["fixtures"] == {"dmx.ball-spot": {"hex": "#00ff00", "bri": 50}}
                assert eng.follows[-1] == b["fixtures"]
                assert (await body(await cl.get("/api/v1/rig/follow"))) == {"fixtures": b["fixtures"]}
                for bad in ({"fixtures": {"dmx.ball-spot": "on"}}, {"fixtures": ["x"]}, {"dmx.ball-spot": "dmx"}):
                    r = await cl.post("/api/v1/rig/follow", json=bad)
                    b = await body(r)
                    assert r.status == 400 and b["choices"] == apimod.FOLLOW_CHOICES, bad
                # remove
                b = await body(await cl.delete("/api/v1/rig/fixtures/dmx.ball-spot"))
                assert b["removed"] == "dmx.ball-spot" and b["outputs"] == ["dmx-192-0-2-10-u3"]
                assert (await body(await cl.get("/api/v1/rig/follow"))) == {"fixtures": {}}
                r = await cl.delete("/api/v1/rig/fixtures/dmx.ball-spot")
                assert r.status == 404
        run(go())
    finally:
        aw = rig.stop()
        if aw is not None:
            asyncio.run(aw)


# -- docs/LIGHTS.md §8.1: moving lights, the Room map and the live view ---------------------------------------------------
# The rig's hooks (engine), fixtures.SetupStore (encode) and the space module (space) are fakes to the spec's
# interfaces, on tmp files: nothing under config/ is written.
import copy
import math

from show_service import fixtures as fxmod

CONFIG_FILES = (c.CONFIG_ROOM_DIR / "fixtures.setup.yaml", c.CONFIG_ROOM_DIR / "space.added.yaml",
                c.CONFIG_ROOM_DIR / "space.yaml")
MOVER_CAPS = {"attrs": ["intensity", "colour", "pan", "tilt", "zoom", "gobo"], "moves": True,
              "pan": {"min": -270.0, "max": 270.0, "fine": True, "limits": None},
              "tilt": {"min": -135.0, "max": 135.0, "fine": True, "limits": None},
              "zoom": {"min": 10.0, "max": 40.0, "known": False}, "colour": "wheel",
              "gobo_slots": [{"slot": 1, "name": "Open", "shake": False, "open": True}]}
PAR_CAPS = {"attrs": ["intensity", "colour"], "moves": False, "pan": None, "tilt": None, "colour": "mix"}


def _config_stamp() -> dict:
    return {str(p): (p.stat().st_mtime_ns if p.exists() else None) for p in CONFIG_FILES}


class FakeSetupStore:
    """fixtures.SetupStore (docs/LIGHTS.md §2.6) on a tmp file: null removes a key, positions/slots merge per entry."""

    def __init__(self, path) -> None:
        self.path, self.data, self.threads = path, {}, []

    def read(self) -> dict:
        return copy.deepcopy(self.data)

    def patch(self, fid: str, patch: dict) -> dict:
        self.threads.append(_thread())
        cur = copy.deepcopy(self.data.get(fid, {}))
        for k, v in patch.items():
            if k in ("positions", "slots") and isinstance(v, dict):
                merged = dict(cur.get(k) or {})
                for n, e in v.items():
                    if e is None:
                        merged.pop(n, None)
                    else:
                        merged[n] = e
                cur[k] = merged
            elif v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        home = cur.get("home")
        if home is not None and not (isinstance(home, dict) and all(isinstance(home.get(a), (int, float))
                                                                    for a in ("pan", "tilt"))):
            raise fxmod.Refused("bad_request", "home is {pan, tilt} in degrees")
        self.data[fid] = cur
        self.path.write_text(yaml.safe_dump(self.data))
        return copy.deepcopy(cur)


class FakeSpaceRefused(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def _finite3(v) -> bool:
    return isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)


class FakeSpaceStore:
    """space.Store (docs/LIGHTS.md §3.4) on a tmp file, with SpaceView's shape. No remove_region (an older store)."""

    def __init__(self, path) -> None:
        self.path, self.polls, self.threads = path, [], []
        self.doc: dict = {"room": {"size": [6.0, 8.0, 3.0], "front": "north"},
                          "points": {"piano": {"label": "The piano", "at": [3.0, 2.0, 0.8], "from": "page"},
                                     "mirror-ball": {"label": "Mirror ball", "at": [3.0, 4.0, 2.8], "from": "page"}},
                          "regions": {"dance-floor": {"label": "Dance floor", "surface": "floor",
                                                      "centre": [3.0, 5.0, 0.0], "size": [2.0, 2.0]}},
                          "appliances": {"screen.north": {"at": [3.0, 8.0, 1.5],
                                                          "plane": {"normal": [0, -1, 0], "up": [0, 0, 1]}},
                                         "dmx.mover": {"at": [2.0, 3.0, 2.9], "mount": "ceiling"}}}
        self._write()

    def _write(self) -> None:
        self.threads.append(_thread())
        self.path.write_text(yaml.safe_dump(self.doc))

    def poll(self, force: bool = False) -> bool:
        self.polls.append(_thread())
        return False

    def view(self) -> dict:
        size = self.doc["room"]["size"]
        surfaces = {}
        if size:
            x, y, z = size
            surfaces = {"floor": {"name": "floor", "label": "The floor", "origin": [0, 0, 0], "normal": [0, 0, 1],
                                  "u": [1, 0, 0], "v": [0, 1, 0], "size": [x, y]},
                        "wall-n": {"name": "wall-n", "label": "The north wall", "origin": [0, y, 0],
                                   "normal": [0, -1, 0], "u": [1, 0, 0], "v": [0, 0, 1], "size": None}}
        apps = {k: {"teach": [], "pose": None, "editable": True, **copy.deepcopy(v)}
                for k, v in self.doc["appliances"].items()}
        return {"revision": "s1", "error": None, "problems": [],
                "room": {"size": size, "front": self.doc["room"]["front"], "centre": None, "right": [1, 0, 0]},
                "surfaces": surfaces, "points": copy.deepcopy(self.doc["points"]),
                "regions": copy.deepcopy(self.doc["regions"]), "appliances": apps, "extent": None}

    @staticmethod
    def _id(name: str) -> None:
        if not fxmod._ID.match(name):
            raise FakeSpaceRefused("bad_request", f"{name!r} isn't an id")

    def set_room(self, size=None, front=None) -> dict:
        """None leaves that as it is; a size of [] clears it (space.Store's rule)."""
        if front is not None and front not in ("north", "east", "south", "west"):
            raise FakeSpaceRefused("bad_request", "front")
        if size is not None:
            self.doc["room"]["size"] = size or None
        if front is not None:
            self.doc["room"]["front"] = front
        self._write()
        return self.view()

    def set_appliance(self, aid, *, at=..., mount=..., plane=...) -> dict:
        self._id(aid)
        a = self.doc["appliances"].setdefault(aid, {})
        for k, v in (("at", at), ("mount", mount), ("plane", plane)):
            if v is ...:
                continue
            if v is None:
                a.pop(k, None)
            elif k == "at" and not _finite3(v):
                raise FakeSpaceRefused("bad_request", "at is [x, y, z] in metres")
            else:
                a[k] = v
        self._write()
        return copy.deepcopy(a)

    def remove_appliance(self, aid) -> None:
        self.doc["appliances"].pop(aid, None)
        self._write()

    def set_point(self, name, at, label=None, source="page") -> dict:
        self._id(name)
        if not _finite3(at):
            raise FakeSpaceRefused("bad_request", "at is [x, y, z] in metres")
        self.doc["points"][name] = {"label": label or name, "at": list(at), "from": source}
        self._write()
        return copy.deepcopy(self.doc["points"][name])

    def remove_point(self, name) -> None:
        self.doc["points"].pop(name, None)
        self._write()

    def set_region(self, name, surface, centre, size, label=None) -> dict:
        self._id(name)
        self.doc["regions"][name] = {"label": label or name, "surface": surface, "centre": centre, "size": size}
        self._write()
        return copy.deepcopy(self.doc["regions"][name])

    def _app(self, aid) -> dict:
        return self.doc["appliances"].setdefault(aid, {})

    def add_sample(self, aid, s) -> list:
        self._app(aid).setdefault("teach", []).append(dict(s))
        self._write()
        return copy.deepcopy(self._app(aid)["teach"])

    def remove_sample(self, aid, index) -> list:
        self._app(aid).setdefault("teach", []).pop(index)
        self._write()
        return copy.deepcopy(self._app(aid)["teach"])

    def clear_samples(self, aid) -> None:
        self._app(aid)["teach"] = []
        self._write()

    def set_pose(self, aid, pose) -> None:
        self._app(aid)["pose"] = copy.deepcopy(pose)
        self._write()


def fake_space(path):
    """The space module's surface the routes use: shared(), resolve, solve_pose, beam_dir, ray_hit."""
    store = FakeSpaceStore(path)
    ns = types.SimpleNamespace(store=store, solves=[])

    def resolve(view, name):
        for sect, key in (("points", "at"), ("regions", "centre"), ("appliances", "at")):
            got = (view.get(sect) or {}).get(name)
            if got and got.get(key):
                return tuple(got[key])
        return None

    def solve_pose(samples, *, at_hint, mount, pan_range, tilt_range, cfg=None):
        ns.solves.append({"n": len(samples), "at_hint": at_hint, "mount": mount, "pan_range": pan_range,
                          "tilt_range": tilt_range, "cfg": cfg})
        n = len(samples)
        if n < 3:
            return {"pose": None, "rms_deg": None, "worst_deg": None, "worst_index": None, "spread_deg": 0.0,
                    "samples": n, "level": "need_more",
                    "words": f"Point it at 3 places so it learns where it hangs ({n} so far)."}
        pose = {"at": list(at_hint or (3.0, 4.0, 2.9)), "rot": [0.0, 0.0, 0.0], "tilt_offset_deg": 0.0,
                "pan_sign": 1, "tilt_sign": 1, "rms_deg": 0.4, "worst_deg": 0.7, "samples": n, "solved_at": "t"}
        return {"pose": pose, "rms_deg": 0.4, "worst_deg": 0.7, "worst_index": 1, "spread_deg": 60.0, "samples": n,
                "level": "good", "words": "Spot on (within 1°)."}

    ns.shared = lambda: store
    ns.resolve = resolve
    ns.solve_pose = solve_pose
    ns.beam_dir = lambda pose, pan, tilt: (0.0, 0.0, -1.0)                 # straight down, whatever the angles
    ns.ray_hit = lambda view, origin, d: ((origin[0], origin[1], 0.0), "floor") if d[2] < 0 else None
    ns.Refused = FakeSpaceRefused
    return ns


class FakeLightsRig:
    """outputs.Rig's hooks for the page (docs/LIGHTS.md §5.8): jog (any thread), holds, caps_of, reload_now,
    live_view, view, snapshot, fixture_rows."""
    name, tick_hz = "rig", 0.0

    def __init__(self) -> None:
        self.fixtures = {"dmx.mover": {"id": "dmx.mover", "label": "Mover", "kind": "lamp", "type": "generic-mover"},
                         "dmx.par": {"id": "dmx.par", "label": "Par", "kind": "lamp", "type": "generic-rgb"},
                         "screen.north": {"id": "screen.north", "label": "North Frame", "kind": "screen",
                                          "type": "sm-screen", "zone": "north"}}
        self._holds: dict = {}
        self.jogs: list = []
        self.reloads: list = []

    def view(self) -> dict:
        return {"revision": "r1", "fixtures": self.fixtures, "types": {}, "outputs": {}, "nodes": {}}

    def fixture_rows(self) -> list:
        return [{"id": f["id"], "label": f["label"], "kind": f["kind"], "zone": f.get("zone")}
                for f in self.fixtures.values()]

    def snapshot(self) -> dict:
        return {"enabled": True, "outputs": {}, "fixtures": self.fixture_rows()}

    def caps_of(self, fid):
        return {"dmx.mover": MOVER_CAPS, "dmx.par": PAR_CAPS}.get(fid)

    def jog(self, fid, values, *, release=False, free=False) -> dict:
        self.jogs.append((fid, dict(values), release, free, _thread()))
        if release:
            self._holds.pop(fid, None)
            return {"held": False, "until_s": None, "idle_s": 60.0, "target": {}, "now": {}, "unknown": [],
                    "why": None}
        attrs = (self.caps_of(fid) or {}).get("attrs") or []
        start = {"intensity": 1.0, **({"pan": 0.0, "tilt": 0.0} if "pan" in attrs else {})}
        h = self._holds.setdefault(fid, {"target": start, "now": dict(start), "until_s": 0.0, "free": free})
        unknown = sorted(k for k in values if k not in attrs)
        h["target"].update({k: v for k, v in values.items() if k not in unknown})
        h["free"], h["until_s"] = free, 160.0
        return {"held": True, "until_s": 160.0, "idle_s": 60.0, "target": dict(h["target"]), "now": dict(h["now"]),
                "unknown": unknown, "why": None}

    def holds(self) -> dict:
        return copy.deepcopy(self._holds)

    def reload_now(self) -> None:
        self.reloads.append(_thread())

    def live_view(self) -> dict:
        return {"t_ms": 1.0, "flow": None, "programs": {},
                "fixtures": {"dmx.mover": {"at": [2.0, 3.0, 2.9], "dir": [0, 0, -1], "hit": [2.0, 3.0, 0.0],
                                           "surface": "floor", "pan": 0.0, "tilt": 0.0, "zoom_deg": 20.0,
                                           "gobo": None, "prism": False, "intensity": 1.0, "hex": "#ffffff",
                                           "held": False, "calibrated": False}}}


class NoHoldRig(FakeLightsRig):
    """A rig from before the engine's hold landed."""
    jog = None
    holds = None


def lights_api(brain, tmp_path, *, space=True):
    setup = FakeSetupStore(tmp_path / "fixtures.setup.yaml")
    sp = fake_space(tmp_path / "space.added.yaml") if space else types.SimpleNamespace()
    return make_api(brain, setup_store=setup, space_mod=sp), setup, sp


def test_place_ids_and_setup_patch_are_plain():
    assert [apimod.place_id(w) for w in ("The piano", "Dance floor", "dmx.mover-1", "A spot by the door!")] == \
        ["piano", "dance-floor", "dmx.mover-1", "spot-by-the-door"]
    assert apimod.front_word("E") == "east" and apimod.front_word("west") == "west" and apimod.front_word("up") is None
    here = {"pan": 40.0, "tilt": -10.0, "zoom": 20.0}
    # no limits yet: the full range, the nearer end moves; then the other end by name; sorted either way
    p = apimod.setup_patch({"limit": {"axis": "pan", "end": "here"}}, {}, here, MOVER_CAPS)
    assert p == {"limits": {"pan": [-270.0, 40.0]}}
    p = apimod.setup_patch({"limit": {"axis": "pan", "end": "low"}}, p, {"pan": -100.0}, MOVER_CAPS)
    assert p == {"limits": {"pan": [-100.0, 40.0]}}
    p = apimod.setup_patch({"limit": {"axis": "tilt", "end": "high"}}, {"limits": {"pan": [-100, 40]}},
                           {"tilt": -150.0}, {})                          # no caps: ± half the unknown range, sorted
    assert p["limits"] == {"pan": [-100, 40], "tilt": [-150.0, -135.0]}
    assert apimod.setup_patch({"home": "here", "beam": "here", "max_speed": {"pan": 90}}, {}, here, MOVER_CAPS) == \
        {"max_speed": {"pan": 90}, "home": {"pan": 40.0, "tilt": -10.0}, "beam": {"zoom": 20.0}}
    for bad, code in (({"home": "here"}, "no_position"), ({"beam": "here"}, "no_beam"),
                      ({"limit": {"axis": "roll"}}, "bad_request")):
        with pytest.raises(apimod.Refusal) as e:
            apimod.setup_patch(bad, {}, {"intensity": 1.0}, PAR_CAPS)
        assert e.value.code == code
    assert apimod.setup_notes({"home": {"pan": 100, "tilt": 0}, "limits": {"pan": [-10, 50]}}) == \
        ["Its home is outside where it may turn, so it rests at the nearest limit."]


def test_moving_light_routes_without_a_rig_or_a_space(brain, tmp_path):
    before = _config_stamp()
    api, _, _ = lights_api(brain, tmp_path, space=False)

    async def go():
        async with served(api) as cl:
            for method, path, body_ in (("POST", "/api/v1/rig/fixtures/dmx.mover/jog", {}),
                                        ("POST", "/api/v1/rig/fixtures/dmx.mover/setup", {"max_speed": {"pan": 1}}),
                                        ("POST", "/api/v1/rig/fixtures/dmx.mover/positions", {"name": "x"}),
                                        ("POST", "/api/v1/rig/fixtures/dmx.mover/teach", {"point": "piano"}),
                                        ("DELETE", "/api/v1/rig/fixtures/dmx.mover/positions/piano", None),
                                        ("DELETE", "/api/v1/rig/fixtures/dmx.mover/teach/0", None),
                                        ("DELETE", "/api/v1/rig/fixtures/dmx.mover/teach", None)):
                r = await cl.request(method, path, json=body_)
                b = await body(r)
                assert r.status == 503 and b["error"] == "unavailable" and "isn't running" in b["detail"], path
            live = await body(await cl.get("/api/v1/rig/live"))
            assert live["fixtures"] == {} and "isn't running" in live["why"]
            sp = await body(await cl.get("/api/v1/rig/space"))
            assert sp["error"] == apimod.NO_SPACE and sp["targets"] == [] and sp["room"]["size"] is None
            if hasattr(c, "SpaceView"):                                     # the space builder's shape, once landed
                assert set(c.SpaceView.__annotations__) <= set(sp)
            for method, path, b_ in (("PUT", "/api/v1/rig/space/room", {"front": "n"}),
                                     ("PUT", "/api/v1/rig/space/points/piano", {"at": [1, 1, 1]}),
                                     ("DELETE", "/api/v1/rig/space/regions/stage", None)):
                r = await cl.request(method, path, json=b_)
                assert r.status == 503 and (await body(r))["detail"] == apimod.NO_SPACE, path
    run(go())
    assert _config_stamp() == before


def test_jog_is_direct_and_degrades(brain, tmp_path):
    rig = FakeLightsRig()
    brain.components["rig"] = rig
    api, _, _ = lights_api(brain, tmp_path)

    async def go():
        async with served(api) as cl:
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={}))
            assert b["held"] is True and b["target"] == {"intensity": 1.0, "pan": 0.0, "tilt": 0.0}
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/jog",
                                         json={"values": {"pan": 30, "warp": 1}, "free": True}))
            assert b["target"]["pan"] == 30 and b["unknown"] == ["warp"]
            assert rig.jogs[-1][:4] == ("dmx.mover", {"pan": 30, "warp": 1}, False, True)
            assert all(j[4] != "show-brain" for j in rig.jogs)               # no brain hop
            assert brain.calls == 0
            for bad in ({"values": [1]}, {"release": "yes"}, ["x"]):
                r = await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json=bad)
                assert r.status == 400 and (await body(r))["error"] == "bad_request", bad
            r = await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", data=b"{nope")
            assert r.status == 400
            r = await cl.post("/api/v1/rig/fixtures/dmx.nope/jog", json={})
            b = await body(r)
            assert r.status == 404 and b["error"] == "unknown_fixture" and "dmx.mover" in b["choices"]
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"release": True}))
            assert b["held"] is False and rig.holds() == {}
            brain.components["rig"] = NoHoldRig()
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": {"pan": 5}}))
            assert b["held"] is False and b["why"] == apimod.CANT_MOVE and b["idle_s"] == 60.0
            r = await cl.post("/api/v1/rig/fixtures/dmx.mover/setup", json={"home": "here"})
            assert r.status == 409 and (await body(r))["detail"] == "Open Move it first."
    run(go())


def test_setup_route_keeps_home_beam_limits_and_the_rest(brain, tmp_path):
    before = _config_stamp()
    rig = FakeLightsRig()
    brain.components["rig"] = rig
    api, setup, _ = lights_api(brain, tmp_path)

    async def go():
        async with served(api) as cl:
            async def post(b_, status=200, fid="dmx.mover"):
                r = await cl.post(f"/api/v1/rig/fixtures/{fid}/setup", json=b_)
                got = await body(r)
                assert r.status == status, (b_, got)
                return got

            assert (await post({}, 400))["choices"] == list(apimod.SETUP_BODY_KEYS)
            assert "warp" in (await post({"warp": 1}, 400))["detail"]
            got = await post({"home": "here"}, 409)
            assert got == {"error": "not_held", "detail": "Open Move it first."}
            assert (await post({"limit": {"axis": "pan", "end": "here"}}, 409))["error"] == "not_held"
            got = await post({"max_speed": {"pan": 200}, "jog_flip": {"pan": True}})   # no hold needed
            assert got["setup"] == {"max_speed": {"pan": 200}, "jog_flip": {"pan": True}} and got["problems"] == []
            assert setup.threads == ["show-brain"] and rig.reloads == ["show-brain"]
            await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": {"pan": 40, "tilt": -10, "zoom": 20}})
            assert (await post({"home": "here"}))["setup"]["home"] == {"pan": 40.0, "tilt": -10.0}
            assert (await post({"beam": "here"}))["setup"]["beam"] == {"zoom": 20.0}
            assert (await post({"limit": {"axis": "pan", "end": "here"}}))["setup"]["limits"] == {"pan": [-270.0, 40.0]}
            await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": {"pan": -100}, "free": True})
            got = await post({"limit": {"axis": "pan", "end": "low"}})
            assert got["setup"]["limits"] == {"pan": [-100.0, 40.0]} and got["problems"] == []
            got = await post({"home": {"pan": 100, "tilt": 0}})
            assert got["problems"] == ["Its home is outside where it may turn, so it rests at the nearest limit."]
            assert (await post({"limit": {"axis": "roll"}}, 400))["choices"] == list(apimod.LIMIT_ENDS)
            kept = setup.read()
            got = await post({"home": "far"}, 400)                       # the store refuses: nothing written
            assert got["error"] == "bad_request" and setup.read() == kept
            assert "limits" not in (await post({"limits": None}))["setup"]
            await cl.post("/api/v1/rig/fixtures/dmx.par/jog", json={})
            assert (await post({"home": "here"}, 400, "dmx.par"))["error"] == "no_position"
            assert (await post({"home": "here"}, 404, "dmx.nope"))["error"] == "unknown_fixture"
            api.setup_store = None                                        # no SetupStore in fixtures.py yet: 503
            if not hasattr(fxmod, "SetupStore"):
                assert (await post({"max_speed": {"pan": 1}}, 503))["detail"] == apimod.NO_SETUP
    run(go())
    assert _config_stamp() == before
    assert yaml.safe_load((tmp_path / "fixtures.setup.yaml").read_text())["dmx.mover"]["home"] == {"pan": 100,
                                                                                                    "tilt": 0}


def test_teach_and_positions_routes(brain, tmp_path):
    before = _config_stamp()
    brain.ctx.settings = {"lights": {"calibration": {"fit_deg": [1.5, 4.0, 8.0], "min_spread_deg": 20}}}
    rig = FakeLightsRig()
    brain.components["rig"] = rig
    api, setup, sp = lights_api(brain, tmp_path)
    teach = "/api/v1/rig/fixtures/dmx.mover/teach"

    async def go():
        async with served(api) as cl:
            r = await cl.post(teach, json={"point": "piano"})
            assert r.status == 409 and (await body(r))["error"] == "not_held"
            await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": {"pan": 10, "tilt": 20}})
            r = await cl.post(teach, json={"point": "nowhere"})
            b = await body(r)
            assert r.status == 400 and b["error"] == "unknown_point" and {"piano", "mirror-ball", "dance-floor",
                                                                          "screen.north"} <= set(b["choices"])
            assert (await body(await cl.post(teach, json={})))["error"] == "bad_request"
            b = await body(await cl.post(teach, json={"point": "piano"}))
            assert len(b["samples"]) == 1 and b["samples"][0]["at"] == [3.0, 2.0, 0.8]
            assert b["samples"][0]["pan"] == 10.0 and b["samples"][0]["point"] == "piano"
            assert b["fit"]["level"] == "need_more" and "(1 so far)" in b["fit"]["words"]
            assert setup.read()["dmx.mover"]["positions"] == {"piano": {"pan": 10.0, "tilt": 20.0}}
            await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": {"pan": -30}})
            await body(await cl.post(teach, json={"point": "mirror-ball"}))
            b = await body(await cl.post(teach, json={"point": "dance-floor"}))
            assert len(b["samples"]) == 3 and b["fit"]["level"] == "good" and b["fit"]["pose"]["at"] == [2.0, 3.0, 2.9]
            last = sp.solves[-1]
            assert last["pan_range"] == (-270.0, 270.0) and last["tilt_range"] == (-135.0, 135.0)
            assert last["at_hint"] == (2.0, 3.0, 2.9) and last["mount"] == "ceiling"
            assert last["cfg"] == {"fit_deg": [1.5, 4.0, 8.0], "min_spread_deg": 20}
            assert sp.store.view()["appliances"]["dmx.mover"]["pose"]["samples"] == 3
            assert set(sp.store.threads[1:]) == {"show-brain"}
            # a calibrated mover's new name becomes a room point where its beam lands
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/positions", json={"name": "Over the sofa"}))
            assert b["positions"]["over-the-sofa"] == {"pan": -30.0, "tilt": 20.0}
            assert b["point"] == {"name": "over-the-sofa", "at": [2.0, 3.0, 0.0], "surface": "floor"}
            assert sp.store.doc["points"]["over-the-sofa"] == {"label": "Over the sofa", "at": [2.0, 3.0, 0.0],
                                                               "from": "dmx.mover"}
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/positions", json={"name": "piano"}))
            assert b["point"] is None and sp.store.doc["points"]["piano"]["at"] == [3.0, 2.0, 0.8]  # never moved
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/positions",
                                         json={"name": "Door", "at": {"pan": 5, "tilt": 6}}))
            assert b["positions"]["door"] == {"pan": 5.0, "tilt": 6.0}
            for bad in ({}, {"name": "x", "at": "there"}):
                r = await cl.post("/api/v1/rig/fixtures/dmx.mover/positions", json=bad)
                assert r.status == 400, bad
            b = await body(await cl.delete("/api/v1/rig/fixtures/dmx.mover/positions/over-the-sofa"))
            assert "over-the-sofa" not in b["positions"] and "piano" in b["positions"]
            # forgetting places: the pose goes below 3
            b = await body(await cl.delete(teach + "/1"))
            assert [s["point"] for s in b["samples"]] == ["piano", "dance-floor"] and b["fit"]["level"] == "need_more"
            assert sp.store.view()["appliances"]["dmx.mover"]["pose"] is None
            b = await body(await cl.delete(teach + "/9"))                  # not there: what is true now
            assert len(b["samples"]) == 2
            assert (await cl.delete(teach + "/x")).status == 400
            b = await body(await cl.delete(teach))
            assert b["samples"] == [] and b["fit"]["level"] == "need_more"
            # a light that doesn't turn has no position to keep
            await cl.post("/api/v1/rig/fixtures/dmx.par/jog", json={})
            r = await cl.post("/api/v1/rig/fixtures/dmx.par/positions", json={"name": "x"})
            assert r.status == 400 and (await body(r))["error"] == "no_position"
            # a solver that breaks: a "bad" fit in words, the samples kept
            sp.solve_pose = lambda *a, **k: 1 / 0
            b = await body(await cl.post(teach, json={"point": "piano"}))
            assert b["fit"]["level"] == "bad" and "ZeroDivisionError" in b["fit"]["words"] and len(b["samples"]) == 1
    run(go())
    assert _config_stamp() == before


def test_space_routes(brain, tmp_path):
    before = _config_stamp()
    rig = FakeLightsRig()
    brain.components["rig"] = rig
    api, _, sp = lights_api(brain, tmp_path)

    async def go():
        async with served(api) as cl:
            v = await body(await cl.get("/api/v1/rig/space"))
            assert sp.store.polls == ["show-brain"] and v["room"]["front"] == "north"
            targets = {t["name"]: t for t in v["targets"]}
            assert [t["kind"] for t in v["targets"]] == ["point", "point", "region", "screen", "appliance", "surface"]
            assert targets["piano"]["label"] == "The piano"
            assert targets["screen.north"]["label"] == "Centre of the north screen"
            assert targets["dmx.mover"]["label"] == "Mover"
            assert targets["floor"] == {"name": "floor", "label": "The floor", "kind": "surface",
                                        "at": [3.0, 4.0, 0.0]}
            assert "wall-n" not in targets                                  # unbounded: no centre to point at
            v = await body(await cl.put("/api/v1/rig/space/room", json={"front": "E"}))
            assert v["room"]["front"] == "east" and v["room"]["size"] == [6.0, 8.0, 3.0] and "targets" in v
            for bad in ({"front": "up"}, {"size": [6, -1, 3]}, {"size": [6, 8]}, {"walls": 4}, {}):
                r = await cl.put("/api/v1/rig/space/room", json=bad)
                assert r.status == 400, bad
            v = await body(await cl.put("/api/v1/rig/space/room", json={"size": None}))
            assert v["room"]["size"] is None and v["room"]["front"] == "east"
            v = await body(await cl.put("/api/v1/rig/space/room", json={"size": [5, 7, 2.6], "front": "north"}))
            assert v["room"]["size"] == [5.0, 7.0, 2.6]
            n = len(rig.reloads)
            a = await body(await cl.put("/api/v1/rig/space/appliances/dmx.par", json={"at": [1, 2, 2.5],
                                                                                      "mount": "ceiling"}))
            assert a == {"at": [1, 2, 2.5], "mount": "ceiling"} and len(rig.reloads) == n + 1
            assert await body(await cl.put("/api/v1/rig/space/appliances/dmx.par", json={"mount": None})) == \
                {"at": [1, 2, 2.5]}
            for path, bad in (("dmx.par", {"warp": 1}), ("dmx.par", {"at": [1, 2]}), ("Bad Id", {"at": [1, 1, 1]})):
                r = await cl.put(f"/api/v1/rig/space/appliances/{path}", json=bad)
                assert r.status == 400, (path, bad)
            assert await body(await cl.delete("/api/v1/rig/space/appliances/dmx.par")) == {"removed": "dmx.par"}
            assert "dmx.par" not in sp.store.doc["appliances"]
            p = await body(await cl.put("/api/v1/rig/space/points/sofa", json={"at": [1, 1, 0.5], "label": "Sofa",
                                                                               "device": "ignored"}))
            assert p == {"label": "Sofa", "at": [1, 1, 0.5], "from": "page"}
            r = await cl.put("/api/v1/rig/space/points/sofa", data=b'{"at": [NaN, 1, 1]}',
                             headers={"Content-Type": "application/json"})
            assert r.status == 400
            assert (await cl.put("/api/v1/rig/space/points/sofa", json={"label": "no at"})).status == 400
            assert await body(await cl.delete("/api/v1/rig/space/points/sofa")) == {"removed": "sofa"}
            g = await body(await cl.put("/api/v1/rig/space/regions/stage", json={
                "surface": "floor", "centre": [2, 2, 0], "size": [2, 1], "label": "Stage"}))
            assert g["label"] == "Stage" and g["size"] == [2, 1]
            assert (await cl.put("/api/v1/rig/space/regions/stage", json={"surface": "floor"})).status == 400
            r = await cl.delete("/api/v1/rig/space/regions/stage")             # this store can't remove one yet
            assert r.status == 503 and "isn't possible yet" in (await body(r))["detail"]
            del brain.components["rig"]                                        # the map works without a rig
            p = await body(await cl.put("/api/v1/rig/space/points/door", json={"at": [0, 4, 1]}))
            assert p["at"] == [0, 4, 1]
            v = await body(await cl.get("/api/v1/rig/space"))
            assert "door" in {t["name"] for t in v["targets"]}
    run(go())
    assert _config_stamp() == before


def test_rig_view_carries_the_jog_numbers_caps_and_setup_and_the_live_view(tmp_path):
    b = FakeBrain(settings={"lights": {"jog": {"send_hz": 10, "nudge_deg": {"fine": 2, "coarse": "x"}}}})
    try:
        rig = FakeLightsRig()
        b.components["rig"] = rig
        api, setup, _ = lights_api(b, tmp_path)
        setup.data = {"dmx.mover": {"home": {"pan": 1, "tilt": 2}}}

        async def go():
            async with served(api) as cl:
                snap = await body(await cl.get("/api/v1/rig"))
                assert snap["lights_ui"] == {"send_hz": 10, "idle_s": 60.0, "nudge_deg": {"fine": 2, "coarse": 5.0},
                                             "drag_deg_per_px": {"fine": 0.1, "coarse": 0.5}}
                rows = {r["id"]: r for r in snap["fixtures"]}
                assert rows["dmx.mover"]["caps"] == MOVER_CAPS and rows["dmx.par"]["caps"] == PAR_CAPS
                assert rows["screen.north"]["caps"] is None
                assert rows["dmx.mover"]["setup"] == {"home": {"pan": 1, "tilt": 2}} and rows["dmx.par"]["setup"] == {}
                rig.fixtures["dmx.par"]["setup"] = {"home": {"pan": 0, "tilt": 0}}  # the registry's merge wins
                rows = {r["id"]: r for r in (await body(await cl.get("/api/v1/rig")))["fixtures"]}
                assert rows["dmx.par"]["setup"] == {"home": {"pan": 0, "tilt": 0}}
                live = await body(await cl.get("/api/v1/rig/live"))
                assert live["fixtures"]["dmx.mover"]["hit"] == [2.0, 3.0, 0.0]
        run(go())
    finally:
        b.close()


def test_the_page_flows_against_the_real_room_space(brain, tmp_path):
    """Room map -> Move it -> Point it at… -> Save as…, through the routes with the real space module on tmp files:
    the angles come from a synthetic hung mover (space.aim), the solve recovers it, a saved position lands on the
    floor where its beam does."""
    space = pytest.importorskip("show_service.space")
    pytest.importorskip("scipy")
    before = _config_stamp()
    store = space.Store(tmp_path / "space.yaml", added_path=tmp_path / "space.added.yaml")
    sp = types.SimpleNamespace(shared=lambda: store, resolve=space.resolve, solve_pose=space.solve_pose,
                               beam_dir=space.beam_dir, ray_hit=space.ray_hit, Refused=space.Refused)
    rig = FakeLightsRig()
    brain.components["rig"] = rig
    setup = FakeSetupStore(tmp_path / "fixtures.setup.yaml")
    api = make_api(brain, setup_store=setup, space_mod=sp)
    yaw = math.radians(37.0)        # hung (z_f down: 180° about x), then turned 37° about the room's up
    rot = [math.pi * math.cos(yaw / 2.0), math.pi * math.sin(yaw / 2.0), 0.0]
    true = {"at": [2.0, 3.0, 2.9], "rot": rot, "tilt_offset_deg": 3.0, "pan_sign": -1, "tilt_sign": 1,
            "rms_deg": 0.0, "worst_deg": 0.0, "samples": 0, "solved_at": ""}
    places = {"corner": [0.5, 0.5, 0.0], "piano": [5.0, 1.5, 0.8], "rug": [4.0, 7.0, 0.0], "door": [0.0, 6.0, 1.5]}

    def angles(q):
        a = space.aim(true, q, current=(0.0, 0.0), pan_range=(-270.0, 270.0), tilt_range=(-135.0, 135.0))
        assert a["reachable"], q
        return {"pan": round(a["pan"], 3), "tilt": round(a["tilt"], 3)}

    async def go():
        async with served(api) as cl:
            v = await body(await cl.put("/api/v1/rig/space/room", json={"size": [6, 8, 3], "front": "n"}))
            assert v["room"]["size"] == [6.0, 8.0, 3.0] and v["room"]["front"] == "north"
            a = await body(await cl.put("/api/v1/rig/space/appliances/dmx.mover", json={"at": [2.1, 2.9, 2.9],
                                                                                        "mount": "ceiling"}))
            assert a["at"] == [2.1, 2.9, 2.9]
            for n, at in places.items():
                p = await body(await cl.put(f"/api/v1/rig/space/points/{n}", json={"at": at}))
                assert p["at"] == at
            v = await body(await cl.get("/api/v1/rig/space"))
            kinds = {t["name"]: t["kind"] for t in v["targets"]}
            assert {n: "point" for n in places}.items() <= kinds.items() and kinds["floor"] == "surface"
            fit = None
            for i, (n, at) in enumerate(places.items()):
                r = await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": angles(at)})
                assert (await body(r))["held"] is True
                b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/teach", json={"point": n}))
                assert len(b.get("samples") or ()) == i + 1, b
                fit = b["fit"]
                assert (fit["level"] == "need_more") == (i < 2), fit
            assert fit["level"] == "good" and fit["rms_deg"] < 1.0 and fit["words"]
            got = store.view()["appliances"]["dmx.mover"]["pose"]
            assert math.dist(got["at"], true["at"]) < 0.1
            # a calibrated mover's new position becomes a place on the floor where its beam lands
            await cl.post("/api/v1/rig/fixtures/dmx.mover/jog", json={"values": angles([3.0, 5.0, 0.0])})
            b = await body(await cl.post("/api/v1/rig/fixtures/dmx.mover/positions",
                                         json={"name": "By the sofa"}))
            assert b["point"]["name"] == "by-the-sofa" and b["point"]["surface"] == "floor"
            assert math.dist(b["point"]["at"], [3.0, 5.0, 0.0]) < 0.05
            assert store.view()["points"]["by-the-sofa"]["from"] == "dmx.mover"
            b = await body(await cl.delete("/api/v1/rig/fixtures/dmx.mover/teach/7"))   # not there: as it is
            assert len(b["samples"]) == 4
            b = await body(await cl.delete("/api/v1/rig/fixtures/dmx.mover/teach/0"))
            assert len(b["samples"]) == 3 and b["fit"]["pose"] is not None
            b = await body(await cl.delete("/api/v1/rig/fixtures/dmx.mover/teach"))
            assert b["samples"] == [] and b["fit"]["level"] == "need_more"
            assert store.view()["appliances"]["dmx.mover"]["pose"] is None
            g = await body(await cl.put("/api/v1/rig/space/regions/dance-floor", json={
                "surface": "floor", "centre": [3, 5, 0], "size": [2, 2], "label": "Dance floor"}))
            assert g["label"] == "Dance floor"
            assert await body(await cl.delete("/api/v1/rig/space/regions/dance-floor")) == {"removed": "dance-floor"}
            r = await cl.put("/api/v1/rig/space/points/Bad Name", json={"at": [1, 1, 1]})
            assert r.status == 400
    run(go())
    assert _config_stamp() == before


def test_move_it_on_the_real_rig(brain, tmp_path):
    """Add a mover, hold it, move it, keep its home and a limit, place it, read it back: the real Rig, registry, look
    engine, setup store and space store on tmp files, the real Art-Net driver on a fake socket. Crash level."""
    space = pytest.importorskip("show_service.space")
    pytest.importorskip("show_service.fixture_encode")
    from show_service import looks
    from show_service.tests import test_outputs as to
    if not all(hasattr(looks.LookEngine, k) for k in ("hold", "release", "holds")):
        pytest.skip("the look engine's hold hasn't landed")
    before = _config_stamp()
    store = space.Store(tmp_path / "space.yaml", added_path=tmp_path / "space.added.yaml")
    rig = to.lights_rig(tmp_path, space_store=store)
    rig.start()
    brain.components["rig"] = rig
    sp = types.SimpleNamespace(shared=lambda: store, resolve=space.resolve, solve_pose=space.solve_pose,
                               beam_dir=space.beam_dir, ray_hit=space.ray_hit, Refused=space.Refused)
    api = make_api(brain, space_mod=sp)                   # the setup store: the registry's own file, next to tmp's
    mover = {"label": "Mover", "type": "generic-mover-full", "node_ip": "192.0.2.10", "universe": 1, "address": 1}
    try:
        async def go():
            async with served(api) as cl:
                r = await cl.post("/api/v1/rig/fixtures", json=mover)
                got = await body(r)
                assert r.status == 200, got
                fid, row = got["fixture"]["id"], got["fixture"]
                assert row["caps"]["moves"] is True and "pan" in row["caps"]["attrs"] and row["setup"] == {}
                b = await body(await cl.post(f"/api/v1/rig/fixtures/{fid}/jog", json={}))
                assert b["held"] is True, b
                b = await body(await cl.post(f"/api/v1/rig/fixtures/{fid}/jog",
                                             json={"values": {"pan": 30, "tilt": -10, "zoom": 20}}))
                assert b["held"] is True and b["target"]["pan"] == 30 and b["unknown"] == []
                b = await body(await cl.post(f"/api/v1/rig/fixtures/{fid}/setup", json={"home": "here"}))
                assert b["setup"]["home"] == {"pan": 30.0, "tilt": -10.0}, b
                b = await body(await cl.post(f"/api/v1/rig/fixtures/{fid}/setup",
                                             json={"limit": {"axis": "pan", "end": "here"}}))
                assert b["setup"]["limits"]["pan"][1] == 30.0, b
                snap = await body(await cl.get("/api/v1/rig"))
                rows = {x["id"]: x for x in snap["fixtures"]}
                assert rows[fid]["setup"]["home"] == {"pan": 30.0, "tilt": -10.0}
                assert snap["lights_ui"]["idle_s"] > 0
                assert (tmp_path / "fixtures.setup.yaml").exists()
                v = await body(await cl.put("/api/v1/rig/space/room", json={"size": [6, 8, 3]}))
                assert v["room"]["size"] == [6.0, 8.0, 3.0]
                a = await body(await cl.put(f"/api/v1/rig/space/appliances/{fid}", json={"at": [2, 3, 2.9],
                                                                                         "mount": "ceiling"}))
                assert a["at"] == [2.0, 3.0, 2.9]
                live = await body(await cl.get("/api/v1/rig/live"))
                assert isinstance(live["fixtures"], dict)
                b = await body(await cl.post(f"/api/v1/rig/fixtures/{fid}/jog", json={"release": True}))
                assert b["held"] is False
                r = await cl.post(f"/api/v1/rig/fixtures/{fid}/setup", json={"beam": "here"})
                assert r.status == 409 and (await body(r))["detail"] == "Open Move it first."
        run(go())
    finally:
        to.stop(rig)
    assert _config_stamp() == before
