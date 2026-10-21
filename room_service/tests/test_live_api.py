"""live_api.py: route mapping onto stream_service, 409 not_live, thumbnail names, cache invalidation (kept light)."""

from __future__ import annotations

import copy

import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from room_service import live_api

from .conftest import Harness
from .fakes import CATALOG, FakeStreamService

CAT = copy.deepcopy(CATALOG)
CAT["sources"][0]["schema"]["properties"]["zoom_rate"]["x-ui"] = {"basic": True}
CAT["effects"] = [{"name": "glow", "label": "Glow", "stage": "light",
                   "schema": {"properties": {"amount": {"type": "number", "x-ui": {"basic": True}}}}}]
CAT["presets"]["effects"] = {"glow-soft": {"label": "Soft glow", "effect": "glow", "params": {"amount": 0.4}}}
CAT["palettes"] = {"ember": {"label": "Ember", "swatch": ["#e77021", "#581700"], "tags": {"family": "warm"}}}
CAT["vocab"] = {"moods": ["calm"], "transition_style": ["gentle"]}
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


class LiveFake(FakeStreamService):
    """The shared fake plus the 1.1 routes live_api passes through."""

    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.n_catalog = 0
        self.n_thumb = 0
        self.looks: dict[str, dict] = {}

    async def start(self, port: int | None = None) -> str:
        app = web.Application()
        r = app.router
        r.add_get("/api/catalog", self._catalog2)
        r.add_get("/api/sessions", self._list)
        r.add_post("/api/sessions", self._create)
        r.add_get("/api/sessions/{id}", self._get2)
        r.add_post("/api/sessions/{id}/go", self._go)
        r.add_post("/api/sessions/{id}/exit", self._exit)
        r.add_post("/api/sessions/{id}/renew", self._renew)
        r.add_patch("/api/sessions/{id}", self._patch2)
        r.add_delete("/api/sessions/{id}", self._delete)
        r.add_post("/api/sessions/{id}/cue", self._cue)
        r.add_get("/api/sessions/{id}/fx", self._fx)
        r.add_put("/api/sessions/{id}/fx", self._fx)
        r.add_get("/api/sessions/{id}/inputs", self._inputs)
        r.add_get("/api/state", self._state)
        r.add_get("/api/events", self._events)
        r.add_get("/dev/library", self._library)
        r.add_get("/dev/thumb/{name}.png", self._thumb)
        self.server = TestServer(app, host="127.0.0.1", port=port)
        await self.server.start_server()
        return self.url

    def look(self, sid: str) -> dict:
        return self.looks.setdefault(sid, {"direction": {"energy": 0.5, "pace": 0.5}, "auto": None, "palette": None,
                                           "fx": {"rev": 0, "stack": []}, "current_scene": "fractal-zoom"})

    async def _catalog2(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.n_catalog += 1
        return web.json_response(CAT)

    async def _get2(self, request: web.Request) -> web.Response:
        await self._body(request)
        sid = request.match_info["id"]
        if sid not in self.sessions:
            return web.json_response({"error": "not_found"}, status=404)
        return web.json_response({**self._summary(self.sessions[sid]), **self.look(sid)})

    async def _patch2(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        sid = request.match_info["id"]
        if sid not in self.sessions:
            return web.json_response({"error": "not_found"}, status=404)
        lk = self.look(sid)
        for k in ("direction", "palette"):
            if k in body:
                lk[k] = body[k]
        if "auto" in body:
            lk["auto"] = {**(lk["auto"] or {"pool": "all"}), **body["auto"]}
        return web.json_response({**self._summary(self.sessions[sid]), **lk})

    async def _cue(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        if body.get("to") == "nope":
            return web.json_response({"error": "unknown_preset", "detail": "unknown scene preset 'nope'"}, status=400)
        return web.json_response({"session": request.match_info["id"], "state": "live", "cue_id": "c-0001",
                                  "at_pts": None, "duration_s": 0.0}, status=202)

    async def _fx(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        lk = self.look(request.match_info["id"])
        if request.method == "PUT":
            lk["fx"] = {"rev": lk["fx"]["rev"] + 1, "stack": body["stack"], "fade_s": body.get("fade_s", 2.0)}
        return web.json_response({"session": request.match_info["id"], **lk["fx"]})

    async def _inputs(self, request: web.Request) -> web.Response:
        await self._body(request)
        return web.json_response({"online": False, "derived": {"bpm": None}})

    async def _library(self, request: web.Request) -> web.Response:
        return web.json_response({"items": [{"name": "fractal_zoom", "thumb": "/dev/thumb/fractal_zoom.png?v=17"}]})

    async def _thumb(self, request: web.Request) -> web.Response:
        if request.match_info["name"] != "fractal_zoom":
            raise web.HTTPNotFound()
        self.n_thumb += 1
        return web.Response(body=JPEG, content_type="image/jpeg")


@pytest_asyncio.fixture
async def h(tmp_path, monkeypatch):
    hz = Harness(tmp_path)
    hz.stream = LiveFake(hz.stream.token)
    await hz.start()
    try:
        yield hz
    finally:
        await hz.stop()


def sid_of(h: Harness) -> str:
    return h.screen().session["id"]


async def test_not_live_and_unknown_screen(h: Harness):
    for method, path, body in [("POST", "/api/v1/screens/devtest/cue", {"to": "other-scene"}),
                               ("GET", "/api/v1/screens/devtest/look", None),
                               ("PATCH", "/api/v1/screens/devtest/look", {"palette": "ember"}),
                               ("GET", "/api/v1/screens/devtest/fx", None),
                               ("PUT", "/api/v1/screens/devtest/fx", {"stack": []})]:
        st, res = await h.api(method, path, body, token=None)
        assert st == 409 and res["error"] == "not_live", (method, path, st, res)
    st, res = await h.api("GET", "/api/v1/screens/nowhere/look", token=None)
    assert st == 404 and res["error"] == "unknown_screen"
    assert not h.stream.calls_of("POST", "/api/sessions/s-")          # nothing reached stream_service


async def test_routes_map_onto_the_live_session(h: Harness):
    await h.go_live()
    sid = sid_of(h)
    # cue: transition -> via, source panel; the scene is recorded in desired
    st, res = await h.api("POST", "/api/v1/screens/devtest/cue",
                          {"to": "other-scene", "transition": "cut", "at": "beat"}, token=None)
    assert st == 202 and res["cue_id"] == "c-0001" and res["recorded"] is True
    assert h.stream.calls_of("POST", f"/api/sessions/{sid}/cue")[-1][2] == \
        {"to": "other-scene", "via": "cut", "at": "beat", "source": "panel"}
    assert h.screen().desired["live"]["scene"] == "other-scene"
    # "default" transition: no via (the renderer's own precedence)
    st, _ = await h.api("POST", "/api/v1/screens/devtest/cue", {"to": "auto", "transition": "default"}, token=None)
    assert st == 202 and h.stream.calls_of("POST", f"/api/sessions/{sid}/cue")[-1][2] == {"to": "auto", "source": "panel"}
    # stream errors are relayed with their status
    st, res = await h.api("POST", "/api/v1/screens/devtest/cue", {"to": "nope"}, token=None)
    assert st == 400 and res["error"] == "unknown_preset"
    st, res = await h.api("POST", "/api/v1/screens/devtest/cue", {"go": 1}, token=None)
    assert st == 400 and res["error"] == "bad_request"
    # look: auto shorthand, direction merged over the session's
    st, res = await h.api("PATCH", "/api/v1/screens/devtest/look",
                          {"auto": True, "palette": "ember", "direction": {"energy": 0.9}}, token=None)
    assert st == 200, res
    assert h.stream.calls_of("PATCH", f"/api/sessions/{sid}")[-1][2] == \
        {"auto": {"enabled": True}, "palette": "ember", "direction": {"energy": 0.9, "pace": 0.5}}
    assert res["palette"] == "ember" and res["auto"]["enabled"] is True and res["screen"] == "devtest"
    st, res = await h.api("PATCH", "/api/v1/screens/devtest/look", {"scene": "x"}, token=None)
    assert st == 400
    st, res = await h.api("GET", "/api/v1/screens/devtest/look", token=None)
    assert st == 200 and res["session"] == sid and res["direction"]["energy"] == 0.9
    # fx pass-through
    body = {"stack": ["glow-soft"], "fade_s": 1.0, "if_rev": 0}
    st, res = await h.api("PUT", "/api/v1/screens/devtest/fx", body, token=None)
    assert st == 200 and res["rev"] == 1 and h.stream.calls_of("PUT", f"/api/sessions/{sid}/fx")[-1][2] == body
    st, res = await h.api("GET", "/api/v1/screens/devtest/fx", token=None)
    assert st == 200 and res["stack"] == ["glow-soft"]
    st, res = await h.api("GET", "/api/v1/screens/devtest/inputs", token=None)
    assert st == 200 and res["online"] is False


async def test_cues_are_never_rate_limited(h: Harness):
    """live-F9: a second tap straight after the first goes too (no 429)."""
    await h.go_live()
    st, _ = await h.api("POST", "/api/v1/screens/devtest/cue", {"to": "other-scene"}, token=None)
    assert st == 202
    st, _ = await h.api("POST", "/api/v1/screens/devtest/cue", {"to": "fractal-zoom"}, token=None)
    assert st == 202


async def test_auto_scene_change_is_followed(h: Harness):
    await h.go_live()
    sid = sid_of(h)
    h.stream.emit({"type": "session.transition", "session": sid, "screen": "devtest", "kind": "scene",
                   "phase": "started", "cause": "auto", "scene": "other-scene", "transition": "xfade"})
    await h.wait(lambda: h.screen().desired["live"]["scene"] == "other-scene", 2.0, "desired follows auto")
    assert h.screen().session["live"] == h.screen().desired["live"]


async def test_thumb_names_and_lru(h: Harness):
    for bad in ("a.b", "-x", "x" * 81, "a%20b"):
        st, res = await h.api("GET", f"/api/v1/thumb/{bad}.png", token=None)
        assert st == 400 and res["error"] == "bad_name", bad
    st, res = await h.api("GET", "/api/v1/thumb/fractal_zoom.png?v=x1", token=None)
    assert st == 400
    for _ in range(2):
        async with h.http.get(h.url + "/api/v1/thumb/fractal_zoom.png?v=17") as r:
            assert r.status == 200 and await r.read() == JPEG
            assert r.headers["Cache-Control"] == "max-age=300" and r.content_type == "image/jpeg"
    assert h.stream.n_thumb == 1                                      # the second came from the LRU
    st, res = await h.api("GET", "/api/v1/thumb/no_such.png", token=None)
    assert st == 404 and res["error"] == "no_thumb"


async def test_library_and_catalog_caches_drop_on_catalog_changed(h: Harness):
    st, lib = await h.api("GET", "/api/v1/library", token=None)
    assert st == 200
    assert [s["name"] for s in lib["scenes"]] == ["fractal-zoom", "other-scene"]
    assert lib["scenes"][0]["basic"] == {"zoom_rate": 0.25}
    assert lib["scenes"][0]["thumb"] == "/api/v1/thumb/fractal_zoom.png?v=17"
    assert {t["name"] for t in lib["transitions"]} == {"xfade", "crossfade-3s"} and lib["effects"][0]["stage"] == "light"
    assert lib["palettes"][0]["swatch"] and lib["vocab"]["moods"] == ["calm"] and "defaults" in lib["direction"]
    st, _ = await h.api("GET", "/api/v1/catalog", token=None)
    n = h.stream.n_catalog
    await h.api("GET", "/api/v1/library", token=None)
    await h.api("GET", "/api/v1/catalog", token=None)
    assert h.stream.n_catalog == n                                    # both cached
    h.stream.emit({"type": "catalog.changed", "reason": "rescan", "rev": 2})
    await h.wait(lambda: h.svc.app[live_api.LIVE_API]["state"]["library"] is None, 2.0, "library cache dropped")
    await h.api("GET", "/api/v1/catalog", token=None)
    assert h.stream.n_catalog == n + 1                                # /api/v1/catalog re-read
    await h.api("GET", "/api/v1/library", token=None)
    assert h.stream.n_catalog == n + 1                                # the library shares that fresh read


async def test_look_source_rides_on_the_event_not_to_the_renderer(h: Harness, monkeypatch):
    """c8's director tells a hand pick (holds) from its own palette changes by screen.look's source."""
    await h.go_live()
    sid = sid_of(h)
    seen: list[tuple[str, dict]] = []
    real = h.svc.events.emit
    monkeypatch.setattr(h.svc.events, "emit", lambda kind, data=None, *a, **k: (seen.append((kind, data)), real(kind, data, *a, **k))[1])
    st, _ = await h.api("PATCH", "/api/v1/screens/devtest/look",
                        {"palette": "ember", "palette_transition_s": 4, "source": "show_service"}, token=None)
    assert st == 200
    assert h.stream.calls_of("PATCH", f"/api/sessions/{sid}")[-1][2] == {"palette": "ember", "palette_transition_s": 4}
    looks = [d for kind, d in seen if kind == "screen.look"]
    assert looks[-1] == {"screen": "devtest", "session": sid, "source": "show_service",
                         "changed": ["palette", "palette_transition_s"], "palette": "ember"}
    st, _ = await h.api("PATCH", "/api/v1/screens/devtest/look", {"palette": "ice"}, token=None)
    assert st == 200 and [d for k, d in seen if k == "screen.look"][-1]["source"] == "web"
    st, res = await h.api("PATCH", "/api/v1/screens/devtest/look", {"palette": "ice", "source": 5}, token=None)
    assert st == 400 and res["error"] == "bad_source"
