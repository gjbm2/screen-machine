"""show adapter (show.main, the Feel) against a fake show_service on an ephemeral 127.0.0.1 port: canonical reads,
no feel and unreachable, apply ok / an unknown vibe / refused (never retried) / down (retried) / unconfirmed, the
cold-restart re-apply, normalize, the Feel form
from the live catalog, from vibes.yaml and from the saved cache, the Auto situations in describe(), and a mode with a
show row put back by the room's own restore. Never touches the real show_service (:8703) or the room API (:8700)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, unused_port

from room_service.adapters import build_adapters
from room_service.adapters.show import BASIC_FIELDS, ShowAdapter, canonical
from room_service.roomstate.contract import Placeholder
from room_service.roomstate.fakes import FakeLevel
from room_service.roomstate.model import RoomConfig

from .roomstate.harness import boot, example, fake_adapters, go, make_room

CATALOG = {
    "revision": "0123456789ab", "path": "config/room/vibes.yaml", "error": None,
    "order": ["party", "home"],
    "vibes": {"home": {"label": "Home", "description": "Everyday", "fields": {}},
              "party": {"label": "Party", "description": "Full colour", "fields": {}},
              "chill": {"label": "Chill", "description": "", "fields": {}}},          # not in order: appended
    "fields": {"label": {"type": "string", "x-ui": {"label": "Name"}},
               "energy": {"type": "array", "x-ui": {"label": "Energy"}},
               "strobe": {"type": "boolean", "x-ui": {"label": "Strobe allowed", "advanced": True}},
               "beat_lock": {"type": "boolean", "x-ui": {"label": "Lock to the beat"}},
               "accents": {"type": "string", "x-ui": {"label": "Lamps' beat swell"}},
               "peak_tokens_per_hour": {"type": "integer", "x-ui": {"label": "Big moments (not built yet)",
                                                                    "not_built": True}},
               "persona": {"type": "string", "x-ui": {"label": "Persona"}}},
    "neutral": {}, "palettes": [], "vocab": {},
}
SITUATIONS = {"vocabulary": [
    {"id": "piano_playing", "label": "the piano is playing", "description": "", "source": "midi", "rank": 0,
     "params": {"for_s": {"type": "number"}}, "available": True},
    {"id": "quiet", "label": "the room is quiet", "description": "", "source": "audio", "rank": 2, "params": {},
     "available": False}], "readings": {}, "combinators": ["all", "any", "not"]}


class FakeShow:
    """show_service's Stage 3 vibe routes, as S3.9 specifies them: any target is kept as asked (an unknown vibe is
    played neutral, known false); {"vibe": null} is no feel; 400 only for a body that isn't a target. And the mic
    routes (GET/PUT /api/v1/mic): Auto's claim, the mode's claim and Mute; 400 for a PUT with neither field. And the
    listening routes (GET/PUT /api/v1/listening, 20 Oct): the Music card's switch "on", the mode's leave "mode" and
    boot_id; a bare bool is {"on": bool}; 400 for a PUT with neither field."""

    def __init__(self) -> None:
        self.target: dict | None = {"vibe": "home", "listening": False}
        self.puts: list[dict] = []
        self.status = {"vibe": 200, "put": 200, "mic": 200, "mic_put": 200, "listening": 200, "listening_put": 200}
        self.ignore_puts = False
        self.put_delay = 0.0
        self.boot = "boot-1"
        self.mic = {"mode": False, "muted": False, "auto": False}
        self.mic_puts: list[dict] = []
        self.ignore_mic_puts = False
        self.mic_boot = False                     # the mic's answer carries boot_id (the contract doesn't promise it)
        self.listening = {"on": False, "mode": False}
        self.ai_puts: list = []                   # every PUT /api/v1/listening body, as sent
        self.ignore_ai_puts = False
        self.gets: list[str] = []                 # every GET of /vibe, /mic and /listening, in order
        self.server: TestServer | None = None

    def answer(self) -> dict:
        t = self.target or {"vibe": None, "listening": False}
        known = t.get("vibe") in CATALOG["vibes"]
        return {**t, "source": "mode", "set_age_s": 1.0, "known": known, "label": "x", "effective": {"energy": [0, 1]},
                "listening_effective": False, "listening_why": "AI listening is off", "boot_id": self.boot,
                "problems": [] if known or not self.target else [f"the feel {t['vibe']!r} isn't in vibes.yaml"]}

    async def get_vibe(self, request: web.Request) -> web.Response:
        self.gets.append(request.path)
        if self.status["vibe"] != 200:
            return web.json_response({"error": "starting"}, status=self.status["vibe"])
        return web.json_response(self.answer())

    async def put_vibe(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.puts.append(body)
        if self.put_delay:
            await asyncio.sleep(self.put_delay)
        if self.status["put"] == 400 or not isinstance(body, dict) or "vibe" not in body:
            return web.json_response({"error": "bad_target", "detail": "that isn't a feel",
                                      "choices": CATALOG["order"]}, status=400)
        if self.status["put"] != 200:
            return web.json_response({"error": "busy", "detail": "brain busy"}, status=self.status["put"])
        if not self.ignore_puts:
            self.target = None if body.get("vibe") is None else body
        return web.json_response(self.answer())

    def mic_answer(self) -> dict:
        m = self.mic
        wanted = m["auto"] or m["mode"]
        claims = [{"by": "auto", "label": "Auto"}] * m["auto"] + [{"by": "mode", "label": "Party"}] * m["mode"]
        on = wanted and not m["muted"]
        out = {"on": on, "muted": m["muted"], "wanted": wanted, "why": "Listening" if on else "Off: nothing needs it",
               "claims": claims, "mode": m["mode"], "streams": {"capturing": ["room-mic"] * on, "off": []}}
        return {**out, "boot_id": self.boot} if self.mic_boot else out

    async def get_mic(self, request: web.Request) -> web.Response:
        self.gets.append(request.path)
        if self.status["mic"] != 200:
            return web.json_response({"error": "starting"}, status=self.status["mic"])
        return web.json_response(self.mic_answer())

    async def put_mic(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.mic_puts.append(body)
        ok = isinstance(body, dict) and body.keys() & {"muted", "mode"} and all(
            isinstance(body[k], bool) for k in body.keys() & {"muted", "mode"})
        if self.status["mic_put"] == 400 or not ok:
            return web.json_response({"error": "bad_mic", "detail": "say muted or mode, true or false"}, status=400)
        if self.status["mic_put"] != 200:
            return web.json_response({"error": "busy", "detail": "brain busy"}, status=self.status["mic_put"])
        if not self.ignore_mic_puts:
            self.mic.update({k: body[k] for k in ("muted", "mode") if k in body})
        return web.json_response(self.mic_answer())

    def listening_answer(self) -> dict:
        on, mode = self.listening["on"], self.listening["mode"]
        return {"on": on, "effective": on and mode, "why": None if on and mode else "AI listening is off in this mode",
                "spend_today_usd": 0.0, "mode": mode, "boot_id": self.boot}

    async def get_listening(self, request: web.Request) -> web.Response:
        self.gets.append(request.path)
        if self.status["listening"] != 200:
            return web.json_response({"error": "starting"}, status=self.status["listening"])
        return web.json_response(self.listening_answer())

    async def put_listening(self, request: web.Request) -> web.Response:
        raw = await request.json()
        self.ai_puts.append(raw)
        body = {"on": raw} if isinstance(raw, bool) else raw
        ok = isinstance(body, dict) and body and set(body) <= {"on", "mode"} and all(
            isinstance(v, bool) for v in body.values())
        if self.status["listening_put"] == 400 or not ok:
            return web.json_response({"error": "bad_request", "detail": 'Say {"on": true|false} and/or {"mode": '
                                      'true|false}.'}, status=400)
        if self.status["listening_put"] != 200:
            return web.json_response({"error": "busy", "detail": "brain busy"}, status=self.status["listening_put"])
        if not self.ignore_ai_puts:
            self.listening.update(body)
        return web.json_response(self.listening_answer())

    @staticmethod
    def get_json(body: dict):
        async def handler(request: web.Request) -> web.Response:
            return web.json_response(body)
        return handler

    async def start(self) -> str:
        app = web.Application()
        app.router.add_get("/api/v1/vibe", self.get_vibe)
        app.router.add_put("/api/v1/vibe", self.put_vibe)
        app.router.add_get("/api/v1/mic", self.get_mic)
        app.router.add_put("/api/v1/mic", self.put_mic)
        app.router.add_get("/api/v1/listening", self.get_listening)
        app.router.add_put("/api/v1/listening", self.put_listening)
        app.router.add_get("/api/v1/vibes", self.get_json(CATALOG))
        app.router.add_get("/api/v1/situations", self.get_json(SITUATIONS))
        self.server = TestServer(app, host="127.0.0.1")         # port 0: an ephemeral port
        await self.server.start_server()
        return str(self.server.make_url("")).rstrip("/")


@pytest.fixture
async def show():
    fake = FakeShow()
    fake.url = await fake.start()
    yield fake
    await fake.server.close()


def nowhere() -> str:
    return f"http://127.0.0.1:{unused_port()}"                 # nothing listens there


def vibes_yaml(tmp_path, text: str = "order: [dinner]\nvibes:\n  dinner: {label: Dinner}\n  bright: {}\n"):
    p = tmp_path / "vibes.yaml"
    p.write_text(text)
    return p


async def settled(a: ShowAdapter) -> None:
    if a._task is not None:
        await a._task


# -- read -------------------------------------------------------------------------------------------------------------

async def test_read_gives_only_the_canonical_target(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    assert (await a.read(["main"]))["main"].value == {"vibe": "home", "listening": False}
    show.target = {"vibe": "party", "listening": True, "overrides": {"strobe": False}}
    assert (await a.read(["main"]))["main"].value == {"vibe": "party", "listening": True, "overrides": {"strobe": False}}
    show.target = {"vibe": "party", "listening": True, "overrides": {}}          # empty overrides are dropped
    assert (await a.read(["main"]))["main"].value == {"vibe": "party", "listening": True}
    show.target = None                                                           # no feel: a value, not unknown
    assert (await a.read(["main"]))["main"].value == {"vibe": None, "listening": False}
    await settled(a)


async def test_read_raises_when_show_service_is_down_or_not_ready(show, tmp_path):
    with pytest.raises(ConnectionError, match="unreachable"):
        await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).read(["main"])
    show.status["vibe"] = 503
    with pytest.raises(ConnectionError, match="503"):
        await ShowAdapter(show.url, vibes_yaml(tmp_path)).read(["main"])


# -- apply ------------------------------------------------------------------------------------------------------------

async def test_apply_puts_the_canonical_target_and_reads_it_back(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"main": {"name": "party", "listening": True, "overrides": {"strobe": False, "energy": None}}}, {})
    assert out.status == "ok" and out.members["main"].status == "ok"
    assert show.puts == [{"vibe": "party", "listening": True, "overrides": {"strobe": False}}]
    assert show.target == show.puts[0]
    await settled(a)


async def test_apply_refused_is_never_retried(show, tmp_path):
    show.status["put"] = 400                                                     # only a body that isn't a target
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"main": {"vibe": "party"}}, {})
    assert out.status == "failed" and out.retry is False and out.members["main"].retry is False
    assert "bad_target" in out.detail and "party" in out.detail                  # the choices are named


async def test_an_unknown_vibe_lands_and_plays_neutral(show, tmp_path):
    """Degrade, never refuse: a vibe vibes.yaml has lost is taken as asked (the previous feel doesn't linger), so the
    row lands; show_service's read-back says known false and why."""
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"main": {"vibe": "nowhere", "listening": True}}, {})
    assert out.status == "ok" and show.target == {"vibe": "nowhere", "listening": True}
    await settled(a)


async def test_no_feel_is_a_value_the_room_can_put_back(show, tmp_path):
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    out = await a.apply({"main": {"vibe": None}}, {})
    assert out.status == "ok" and show.puts[-1] == {"vibe": None, "listening": False} and show.target is None
    assert (await a.read(["main"]))["main"].value == {"vibe": None, "listening": False}
    assert a.show("main", {"vibe": None, "listening": False}) == "No feel"
    await settled(a)


async def test_a_cold_show_service_gets_the_feel_it_had_again(show, tmp_path):
    """show_service restarted with no state.json (a new boot_id, no feel) after the adapter landed a feel: the room
    never retries a settled "ignore" row, so the adapter puts its last value back, once per boot."""
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    assert (await a.apply({"main": {"vibe": "party", "listening": True}}, {})).status == "ok"
    show.target, show.boot = None, "boot-2"                                      # the cold restart
    got = (await a.read(["main"]))["main"].value
    assert got == {"vibe": None, "listening": False}                             # the fact, at once
    await a._reapply
    assert show.target == {"vibe": "party", "listening": True} and len(show.puts) == 2
    assert (await a.read(["main"]))["main"].value == {"vibe": "party", "listening": True}
    show.target = None                                                           # no feel again, same boot: left be
    await a.read(["main"])
    assert a._reapply.done() and len(show.puts) == 2
    b = ShowAdapter(show.url, vibes_yaml(tmp_path))                             # nothing applied by this one: nothing
    show.boot = "boot-3"
    await b.read(["main"])
    assert b._reapply is None and len(show.puts) == 2
    await settled(a)
    await settled(b)


async def test_apply_down_or_busy_is_retried(show, tmp_path):
    out = await ShowAdapter(nowhere(), vibes_yaml(tmp_path)).apply({"main": "party"}, {})
    assert out.status == "failed" and out.retry is True and "show_service unreachable" in out.detail
    show.status["put"] = 503
    out = await ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"main": "party"}, {})
    assert out.status == "failed" and out.retry is True and "503" in out.detail


async def test_apply_that_does_not_land_is_unconfirmed(show, tmp_path):
    show.ignore_puts = True
    a = ShowAdapter(show.url, vibes_yaml(tmp_path))
    a.change_time_s = 0.3
    out = await a.apply({"main": "party"}, {})
    assert out.status == "unconfirmed" and "Home" in out.detail and out.retry is True
    await settled(a)


async def test_apply_cancelled_mid_put_re_raises(show, tmp_path):
    show.put_delay = 2.0
    task = asyncio.create_task(ShowAdapter(show.url, vibes_yaml(tmp_path)).apply({"main": "party"}, {}))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# -- normalize, words ---------------------------------------------------------------------------------------------------

def test_normalize_accepts_the_three_spellings_and_refuses_bad_shapes(tmp_path):
    a = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    assert a.normalize("main", "party") == {"vibe": "party", "listening": False}
    assert a.normalize("main", {"name": "party"}) == {"vibe": "party", "listening": False}
    assert a.normalize("main", {"vibe": "party", "listening": True, "overrides": {}}) == {"vibe": "party", "listening": True}
    assert a.normalize("main", {"vibe": "a-b_1", "overrides": {"strobe": True, "level": None}}) == \
        {"vibe": "a-b_1", "listening": False, "overrides": {"strobe": True}}
    # a name vibes.yaml doesn't have is still loadable (show_service plays it neutral and says so): a renamed vibe
    # never stops modes.json loading
    assert a.normalize("main", "renamed-away")["vibe"] == "renamed-away"
    assert a.normalize("main", {"vibe": None, "listening": True}) == {"vibe": None, "listening": False}   # no feel
    assert a.normalize("main", {"vibe": "p", "overrides": {"follow": ["energy", "energy", "onsets"]}}) == \
        {"vibe": "p", "listening": False, "overrides": {"follow": ["energy", "onsets"]}}     # as show_service keeps it
    for bad in (3, None, [], "Party!", "", {"vibe": "party", "colour": "red"}, {"vibe": "party", "listening": "yes"},
                {"vibe": "party", "overrides": []}, {"vibe": "party", "overrides": {"persona": "x"}},
                {"vibe": "party", "overrides": {"Bad Key": 1}}, {"vibe": "a", "name": "b"}, {"listening": True}):
        with pytest.raises(ValueError):
            a.normalize("main", bad)
    with pytest.raises(ValueError):
        a.normalize("other", "party")
    assert canonical(canonical({"name": "x", "listening": True})) == {"vibe": "x", "listening": True}   # idempotent


def test_show_words(tmp_path):
    a = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    assert a.show("main", {"vibe": "dinner", "listening": False}) == "Dinner"
    assert a.words("main", {"vibe": "dinner", "listening": True}) == "Dinner · listening"
    assert a.show("main", {"vibe": "gone", "listening": False, "overrides": {"strobe": False}}) == "gone · fine-tuned"


# -- the Feel form and the Auto vocabulary ----------------------------------------------------------------------------

def test_value_schema_falls_back_to_vibes_yaml(tmp_path):
    a = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    s = a.value_schema("main")
    vibe = s["properties"]["vibe"]
    assert vibe["enum"] == ["dinner", "bright"] and vibe["x-ui"]["labels"] == {"dinner": "Dinner", "bright": "bright"}
    assert s["x-ui"]["default_policy"] == "ignore" and s["required"] == ["vibe"]
    assert s["properties"]["listening"]["default"] is False
    assert s["properties"]["overrides"]["properties"] == {}                      # no catalog yet: no fields
    assert a.situations() == [] and a.vocabulary() == {"situations": []}
    no_yaml = ShowAdapter(nowhere(), tmp_path / "missing.yaml")
    assert "enum" not in no_yaml.value_schema("main")["properties"]["vibe"]     # unknown names: no enum, not an empty one


async def test_value_schema_and_situations_from_show_service_and_the_saved_cache(show, tmp_path):
    cache = tmp_path / "state" / "show_catalog.json"
    a = ShowAdapter(show.url, vibes_yaml(tmp_path), cache_file=cache)
    await a.read(["main"])                                                       # starts the catalog fetch
    await settled(a)
    s = a.value_schema("main")
    assert s["properties"]["vibe"]["enum"] == ["party", "home", "chill"]       # show_service's order wins over the yaml
    assert s["properties"]["vibe"]["x-ui"]["labels"]["party"] == "Party"
    fine = s["properties"]["overrides"]
    assert fine["x-ui"]["advanced"] is True and fine["x-ui"]["unset_label"] == "As the feel"
    assert fine["x-ui"]["unset_label_by"]["vibe"]["party"] == "As the Party feel"
    assert set(fine["properties"]) == {"energy", "strobe", "beat_lock", "accents", "peak_tokens_per_hour"}
    for name, field in fine["properties"].items():                              # (label/persona are the vibe's own)
        assert bool(field["x-ui"].get("advanced")) is (name not in BASIC_FIELDS or name == "peak_tokens_per_hour"), name
    assert "strobe" not in BASIC_FIELDS                                          # not built yet: behind More
    assert [x["id"] for x in a.situations()] == ["piano_playing", "quiet"]
    assert json.loads(cache.read_text())["situations"][0]["id"] == "piano_playing"
    # a restart with show_service down and vibes.yaml gone: the saved catalog still fills the form and the vocabulary
    b = ShowAdapter(nowhere(), tmp_path / "missing.yaml", cache_file=cache)
    sb = b.value_schema("main")
    assert sb["properties"]["vibe"]["enum"] == ["party", "home", "chill"]
    assert set(sb["properties"]["overrides"]["properties"]) == {"energy", "strobe", "beat_lock", "accents",
                                                                 "peak_tokens_per_hour"}
    assert [x["id"] for x in b.situations()] == ["piano_playing", "quiet"]


# -- registration, the room config, describe, and a mode end to end ---------------------------------------------------

def test_build_adapters_registers_show_and_a_placeholder_for_tests(tmp_path):
    mask = SimpleNamespace(suspended=False, set_suspended=lambda v: None)
    blinds = FakeLevel("blinds", {"ch02": 0})
    built = {a.subsystem: a for a in build_adapters({}, mask, lambda l, m: None, tmp_path, blinds=blinds, sonos=False,
                                                      remotes=[])}
    assert isinstance(built["show"], ShowAdapter) and built["show"].cache_file == tmp_path / "show_catalog.json"
    assert built["show"].category == "setting" and built["show"].subsystem_label == "Listening and feel"
    assert built["show"].members == ["main", "mic", "ai"]
    off = {a.subsystem: a for a in build_adapters({}, mask, lambda l, m: None, tmp_path, blinds=blinds, sonos=False,
                                                    remotes=[], show=False)}
    assert isinstance(off["show"], Placeholder) and off["show"].members == ["main"]


def with_show(raw: dict) -> None:
    raw["modes"]["disco"]["state"]["show.main"] = {"set": {"vibe": "party", "listening": True}, "restore": True,
                                                   "policy": "ignore"}


async def test_a_mode_sets_the_feel_and_the_room_puts_it_back(show, tmp_path):
    a = fake_adapters()
    a.show = ShowAdapter(show.url, vibes_yaml(tmp_path), cache_file=tmp_path / "show_catalog.json")
    r = make_room(tmp_path, example(with_show), adapters=a)
    assert r.cfg.modes["disco"].state["show.main"].set == {"vibe": "party", "listening": True}
    await boot(r)
    await r.c._refresh(["show.main"])                                       # the room's belief before the switch
    tr = await go(r, "disco")
    assert tr["status"] == "ok", tr
    assert show.target == {"vibe": "party", "listening": True}
    await go(r, "normal")                                                    # restore: the room's own put-back
    assert show.target == {"vibe": "home", "listening": False}
    assert show.puts == [{"vibe": "party", "listening": True}, {"vibe": "home", "listening": False}]
    await settled(a.show)
    d = r.c.describe()
    assert [x["id"] for x in d["subsystems"]["show"]["situations"]] == ["piano_playing", "quiet"]
    cond = d["mode_schema"]["properties"]["autopilot"]["properties"]["enter_when"]
    assert [x["id"] for x in cond["x-ui"]["situations"]] == ["piano_playing", "quiet"]
    assert d["targets"]["show.main"]["schema"]["properties"]["vibe"]["enum"] == ["party", "home", "chill"]
    assert d["modes"]["disco"]["entries"]["show.main"]["words"] == "Party · listening"
    assert "situations" not in d["subsystems"]["blinds"]                     # fakes without a vocabulary: unchanged


async def test_leaving_a_mode_puts_no_feel_back(show, tmp_path):
    """The director was neutral before the mode (no feel): the room stores that value and puts it back on leaving,
    so the mode's feel doesn't stick."""
    show.target = None
    a = fake_adapters()
    a.show = ShowAdapter(show.url, vibes_yaml(tmp_path), cache_file=tmp_path / "show_catalog.json")
    r = make_room(tmp_path, example(with_show), adapters=a)
    await boot(r)
    await r.c._refresh(["show.main"])
    assert (await go(r, "disco"))["status"] == "ok" and show.target == {"vibe": "party", "listening": True}
    await go(r, "normal")
    assert show.target is None and show.puts[-1] == {"vibe": None, "listening": False}
    await settled(a.show)


def test_a_show_row_with_a_bad_shape_is_a_config_error(tmp_path):
    def bad(raw):
        raw["modes"]["disco"]["state"]["show.main"] = {"set": {"vibe": "Party!"}}
    a = fake_adapters()
    a.show = ShowAdapter(nowhere(), vibes_yaml(tmp_path))
    with pytest.raises(Exception, match="vibe name"):
        RoomConfig(example(bad), list(vars(a).values()))
    json.dumps(a.show.value_schema("main"))                                  # a plain JSON-able dict
