"""Live click-to-preview for the /dev Library: one reusable 720p session per browser tab.

  POST   /dev/preview {"client", "kind": scene|transition|effect, "spec", "gpu"?}  -> {"session", "attach", ...}
  POST   /dev/preview {"client", "keepalive": true}                                 -> renews the tab's session
  DELETE /dev/preview?client=...                                                     -> ends it

The first show for a client creates its session (h264-720p30, a black anchor, no director, short TTL) and goes
live straight into the item; every later show re-cues the same session with a hard cut, so switching cards is
instant.  A scene plays as itself (its preset's bindings make it react to the show bus); an effect plays at
amount 1 on a moving core scene; a transition loops between two core scenes via that transition.  The page
sends a keepalive while its player is open; without one the session ends at its TTL.

Everything goes through the Sessions API (create / go / cue / renew / delete): no engine internals.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from typing import Any, Optional

from .plugins.base import ParamError

log = logging.getLogger("stream_service.preview")

PROFILE = "h264-720p30"
DEFAULT_GPU = 1
TTL_S = 30.0
CUT = {"transition": "cut"}
SCENE_A = {"source": "warp_stack"}                  # transition previews alternate A <-> B; effects run over A
SCENE_B = {"source": "fractal_zoom"}
HOLD_S = 1.2                                        # each side is shown this long between transition runs
CLIENT_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
LIVE = ("entering", "live")


class PreviewError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail


class Previews:
    def __init__(self, mgr, presets, *, gpu: int = DEFAULT_GPU):
        self.mgr, self.presets, self.gpu = mgr, presets, gpu
        self.sids: dict[str, str] = {}               # client -> session id
        self.loops: dict[str, asyncio.Task] = {}     # client -> transition loop
        self.locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _client(body: Any) -> str:
        c = body.get("client") if isinstance(body, dict) else None
        if not isinstance(c, str) or not CLIENT_RE.match(c):
            raise ParamError("bad_request", "client must be an id of 4-64 letters, digits, '-' or '_'")
        return c

    @staticmethod
    def _screen(client: str) -> str:
        return "dev-preview-" + hashlib.sha1(client.encode()).hexdigest()[:10]

    def _session(self, client: str):
        sid = self.sids.get(client)
        s = self.mgr.sessions.get(sid) if sid else None
        if s is None or s.state in ("ended", "failed", "exiting", "held"):
            self.sids.pop(client, None)
            return None
        return s

    def _stop_loop(self, client: str) -> None:
        t = self.loops.pop(client, None)
        if t is not None:
            t.cancel()

    def _effect_scene(self, spec: Any) -> dict:
        """The effect at amount 1 (when it has an amount) over scene A."""
        fx = {"preset": spec} if isinstance(spec, str) else dict(spec)
        e = self.presets.resolve_effect(fx)                       # a bad spec is a 400 here
        cls = self.presets.registry.effects.get(e.effect)
        if cls is not None and "amount" in cls.param_props():
            fx = {**fx, "params": {**(fx.get("params") or {}), "amount": 1.0}}
        return {**SCENE_A, "effects": [fx]}

    def _to(self, kind: str, spec: Any) -> Any:
        if kind == "scene":
            return spec
        if kind == "effect":
            return self._effect_scene(spec)
        if kind == "transition":
            self.presets.resolve_transition(spec)        # validate now: a bad spec is a 400, not a dead loop
            return SCENE_A
        raise ParamError("bad_request", "kind must be scene, transition or effect")

    async def _wait_state(self, s, states: tuple, timeout: float = 30.0) -> None:
        end = asyncio.get_running_loop().time() + timeout
        while s.state not in states:
            if s.state in ("ended", "failed"):
                raise PreviewError(500, "preview_failed", f"preview session {s.state}")
            if asyncio.get_running_loop().time() > end:
                raise PreviewError(504, "preview_timeout", f"preview session stuck in {s.state}")
            await asyncio.sleep(0.05)

    # ------------------------------------------------------------------ API
    async def show(self, body: Any, host: str) -> dict:
        client = self._client(body)
        kind = body.get("kind")
        spec = body.get("spec")
        if spec is None:
            raise ParamError("bad_request", "spec is required (a preset name or a plug-in spec)")
        gpu = body.get("gpu", self.gpu)
        lock = self.locks.setdefault(client, asyncio.Lock())
        async with lock:
            self._stop_loop(client)
            to = self._to(kind, spec)
            s = self._session(client)
            if s is None:
                s, _ = await self.mgr.create({
                    "screen": self._screen(client), "gpu": gpu, "profile": PROFILE, "anchor": {"rgb": [0, 0, 0]},
                    "scene": to, "transitions": {"in": CUT, "out": CUT}, "ttl_s": TTL_S}, host)
                self.sids[client] = s.id
                await self._wait_state(s, ("holding",) + LIVE)
                if s.state == "holding":
                    await s.go()
            else:
                s.renew()
                await self._wait_state(s, LIVE)
                await s.cue({"to": to, "via": CUT, "at": "now"})
            if kind == "transition":
                self.loops[client] = asyncio.get_running_loop().create_task(self._loop(client, s, spec))
            return {"session": s.id, "state": s.state, "attach": s.attach, "kind": kind}

    async def _loop(self, client: str, s, via: Any) -> None:
        """Alternate A -> B -> A ... via the previewed transition while the session lives."""
        try:
            dur = float(self.presets.resolve_transition(via).params.get("duration_s", 1.5))
        except Exception:
            dur = 1.5
        side = 1
        try:
            await asyncio.sleep(HOLD_S)
            while self.sids.get(client) == s.id and s.state in LIVE:
                await s.cue({"to": SCENE_B if side else SCENE_A, "via": via, "at": "now"})
                side ^= 1
                await asyncio.sleep(dur + HOLD_S)
        except asyncio.CancelledError:
            pass
        except Exception as e:   # the session ended or the transition failed: stop quietly
            log.info("preview loop for %s stopped: %s", s.id, e)

    def keepalive(self, body: Any) -> dict:
        client = self._client(body)
        s = self._session(client)
        if s is None:
            return {"session": None}
        s.renew()
        return {"session": s.id, "state": s.state}

    async def stop(self, client: str) -> dict:
        if not isinstance(client, str) or not CLIENT_RE.match(client):
            raise ParamError("bad_request", "client must be an id of 4-64 letters, digits, '-' or '_'")
        self._stop_loop(client)
        s = self._session(client)
        self.sids.pop(client, None)
        if s is not None:
            await self.mgr.delete(s.id)
        return {"ended": s.id if s is not None else None}

    async def close(self) -> None:
        for c in list(self.loops):
            self._stop_loop(c)

    def owns(self, sid: Optional[str]) -> bool:
        return sid in self.sids.values()
