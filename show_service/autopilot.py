"""Auto: the room picks its own mode (docs/SHOW_ENGINE.md §8.9; README "Stage 3 > S3.8", builder B2).

``attach(ctx)`` returns ``[RoomLink, Situations, Autopilot]``:

- ``RoomLink`` ("room"): show_service's only room_service client. It follows WS /api/v1/events (status and
  mode.transition), falls back to GET /api/v1/status every 5 s while that socket is down, and reads the modes'
  ``autopilot`` blocks from GET /api/v1/room/config whenever status.config.revision changes. It serves ``RoomView``.
  Stage 5 (README S5.8): every other WS event (``screen.look``, ...) goes to ``ctx.on_room_event`` as (type, data),
  and ``room_boot()`` is status.service.started_at (it changes when room_service restarts).
- ``Situations`` ("situations", situations.py): the vocabulary.
- ``Autopilot`` ("autopilot"): the supervisor. It evaluates every mode's own ``autopilot`` block (config only, so no
  code knows any mode) and acts only through room PUT /api/v1/mode {mode, source: "autopilot", reason, expect, skip}.

Owner rules kept here:
- Off by default: the room target ``autopilot.main`` is the truth, with modes.json's top-level ``autopilot.on`` as
  the fallback while the room has no such target.
- It holds after a person's choice, treats Undo (and a change within correction_s) as a correction, and never
  re-requests the current mode. It never acts while a transition runs, and sends ``expect``, so a tap that landed
  meanwhile wins (409 stale).
- It announces every switch, suggestion, hold, undo, pause and failure. A room that answers its PUT late (its lock,
  device reads for a ``skip`` with ``from``) is waited for up to contract.ROOM_PUT_TIMEOUT_S; a PUT with no answer
  leaves a "maybe", and the room's own ``started`` event for it is adopted as Auto's switch (or Undo), so it is still
  announced, entered, counted and undoable.
- A leave sends the union of the left mode's and the destination's ``skip`` (what Auto entered with stays skipped);
  a person going straight back to the mode Auto left, within correction_s, is always a correction.
- Put-back is the room's own per-row ``restore``; nothing here stores or restores device state.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import aiohttp

from . import contract
from .situations import WORDS, Conditions, NoteTap, Situations, _hhmm, in_window, rank_of, word_label

__all__ = ["attach", "RoomLink", "Autopilot", "Situations", "NoteTap", "AP_DEFAULTS"]

log = logging.getLogger("show_service.autopilot")

#: transitions whose source is one of these are never a person's choice
NOT_A_PERSON = (contract.AUTOPILOT_SOURCE, contract.UNDO_SOURCE, "boot", "resume", "rollback")
RULE_KEYS = (("enter", "enter_when"), ("suggest", "otherwise_suggest_when"), ("leave", "leave_when"))
LOG_MAX_BYTES = 1 << 20

#: Auto's globals when modes.json's top-level "autopilot" leaves a key out: design defaults (starting points, §8.9).
AP_DEFAULTS: dict[str, Any] = {
    "on": False,
    "quiet_hours": None,                                 # none unless modes.json sets them (owner, 16 Oct: never invented)
    "min_dwell_s": 300.0,
    "max_switches": {"count": 4, "per_s": 1800.0, "pause_s": 1800.0},
    "backoff_s": 1800.0,
    "hold": {"until": {"all": [{"piano_idle": {}}, {"not": {"music_audible": {}}}], "for_s": 600},
             "max_s": 7200.0, "correction_s": 60.0, "suppress_s": 14400.0},
    "chips": {"switch_s": 45.0, "suggest_s": 120.0},
}
assert tuple(AP_DEFAULTS) == contract.AUTOPILOT_GLOBAL_KEYS

_BAD = object()


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return None
    return float(v)


def _guard(what: str, fn: Callable, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:
        log.exception("%s failed", what)
        return None


def merge_skips(*lists: Any) -> list:
    """The union of PUT /api/v1/mode ``skip`` lists, one entry per selector, in first-seen order: a plain selector
    (skip it always) wins over ``{selector: {"from": patterns}}``; two conditional ones join their ``from`` patterns."""
    out: "OrderedDict[str, Any]" = OrderedDict()
    for lst in lists:
        for item in lst or []:
            if isinstance(item, str):
                out[item] = item
            elif isinstance(item, dict) and len(item) == 1:
                sel, spec = next(iter(item.items()))
                have = out.get(sel)
                if isinstance(have, str):
                    continue
                if have is None:
                    out[sel] = {sel: copy.deepcopy(spec)}
                    continue
                a = (have[sel] or {}).get("from") if isinstance(have[sel], dict) else None
                b = spec.get("from") if isinstance(spec, dict) else None
                pats = [x for x in ([a] if isinstance(a, str) else a or []) + ([b] if isinstance(b, str) else b or [])]
                if a is None or b is None:              # one of them has no "from": skip it whatever it is
                    out[sel] = sel
                else:
                    out[sel] = {sel: {**have[sel], "from": list(dict.fromkeys(pats))}}
    return list(out.values())


# =====================================================================================================================
# RoomLink
# =====================================================================================================================
def _tview(tr: Any) -> Optional[contract.TransitionView]:
    if not isinstance(tr, dict) or not isinstance(tr.get("id"), str):
        return None
    st = tr.get("status")
    return {"id": tr["id"], "from_": tr.get("from"), "to": tr.get("to"), "source": tr.get("source"),
            "reason": tr.get("reason"), "status": "done" if st == "ok" else st}


def _empty_view(error: Optional[str] = None) -> contract.RoomView:
    return {"reachable": False, "age_s": None, "error": error, "current": None, "target": None, "since_age_s": None,
            "transition": None, "last_transition": None, "last_transition_id": None, "autopilot_on": None,
            "revision": None, "modes": {}, "order": [], "autopilot": None, "members": {}, "screens": {},
            "stored_restore": {}}


class RoomLink:
    """Component "room" (no tick: asyncio tasks on ctx.loop). ``view()`` is safe from any thread."""

    name = "room"
    tick_hz = 0.0
    RECONNECT_S = (1.0, 2.0, 5.0)
    POLL_S = 5.0                     # GET /api/v1/status while the WS is down
    STALE_S = 12.0                   # without the WS, the room counts as reachable this long after a good status
    EVENT_FRESH_S = 60.0             # a transition first seen in a status older than this is not news
    CONFIG_RETRY_S = 5.0
    EVENT_TYPES_MAX = 64             # room event types counted in stats["room_events"] (the rest still fan out)

    def __init__(self, ctx: contract.BrainContext, *, mono: Callable[[], float] = time.monotonic) -> None:
        self.ctx = ctx
        self._mono = mono
        self.url = contract.ROOM_URL
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._cfg_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws_up = False
        self._attempt = 0
        self._last_ok: Optional[float] = None       # mono of the last status (WS or GET)
        self._last_poll: Optional[float] = None
        self._status: Optional[dict] = None
        self._status_at: Optional[float] = None
        self._error: Optional[str] = None
        self._cfg: dict = {"modes": {}, "order": [], "autopilot": None, "revision": None, "error": None}
        self._cfg_trigger: Any = _BAD                 # the status revision that last triggered a fetch
        self._cfg_next = 0.0
        self._view: contract.RoomView = _empty_view()
        self._seen: OrderedDict[str, set] = OrderedDict()
        self._info: OrderedDict[str, dict] = OrderedDict()
        self._baselined = False
        self._last_current: Any = _BAD
        self._listeners: list[Callable[[dict], None]] = []
        self._status_listeners: list[Callable[[], None]] = []
        # room_events (Stage 5): every other room WS event type -> how many came (fanned out to ctx.on_room_event)
        self.stats = {"statuses": 0, "events": 0, "ws_connects": 0, "config_fetches": 0, "errors": 0,
                      "room_events": {}}

    # -- Component ------------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        # room_patch: Stage 4 (the director's screens palette); brain.add sets it once contract.BrainContext has
        # the slot (c8), and ignores it until then. room_boot: Stage 5 (README S5.8, the director's source probe).
        return {"room": self.view, "room_get": self.get, "room_put": self.put, "room_patch": self.patch,
                "room_boot": self.room_boot}

    def configure(self, settings: dict) -> None:
        r = settings.get("room") if isinstance(settings, dict) else None
        url = r.get("url") if isinstance(r, dict) else None
        url = (url if isinstance(url, str) and url.startswith(("http://", "https://")) else contract.ROOM_URL).rstrip("/")
        if url != self.url:
            self.url = url
            if self._task is not None:              # a new address: reconnect there
                self._task.cancel()
                self._task = None
                self._ws_up = False
                self.start()

    def restore(self, saved: dict) -> None:
        pass

    def start(self) -> None:
        if self._task is not None:
            return
        loop = self.ctx.loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.error("RoomLink.start: no event loop; Auto stays blind")
                return
        self._loop = loop
        self._task = loop.create_task(self._run(), name="show-roomlink")

    def tick(self, now_s: float) -> None:
        pass

    def persist(self) -> None:
        return None

    def stop(self) -> None:
        for t in (self._task, self._cfg_task):
            if t is not None:
                t.cancel()
        self._task = self._cfg_task = None
        self._ws_up = False
        s, self._session = self._session, None
        if s is not None and not s.closed and self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.create_task(s.close())
            except RuntimeError:
                pass

    def snapshot(self) -> dict:
        v = self.view()
        return {"url": self.url, "ws": self._ws_up, "reachable": v["reachable"], "age_s": v["age_s"],
                "error": self._error, "revision": v["revision"], "rules": {"revision": self._cfg["revision"],
                                                                           "error": self._cfg["error"]},
                "boot": self.room_boot(),
                "stats": {**self.stats, "room_events": dict(self.stats["room_events"])}}

    # -- any thread -----------------------------------------------------------------------------------------------------
    def add_listener(self, fn: Callable[[dict], None]) -> None:
        """fn(notice) on show-brain for each mode.transition phase, once per (id, phase): {"id", "phase": started |
        done | cancelled, "from", "to", "source", "reason", "skip", "status" (done/cancelled), "detail", "via": "event" |
        "status" (seen first in a status rather than as a WS event)}."""
        self._listeners.append(fn)

    def add_status_listener(self, fn: Callable[[], None]) -> None:
        """fn() on show-brain after each status (WS or GET), once the view has it (Autopilot: Auto's on/off may have
        changed). It must not block."""
        self._status_listeners.append(fn)

    def rules_error(self) -> Optional[str]:
        return self._cfg["error"]

    def status_count(self) -> int:
        """Statuses received so far. room_service sends mode.transition at once but coalesces status (<= 4/s), so a
        reader that just heard an event waits for the next status before trusting ``current``."""
        return self.stats["statuses"]

    def room_boot(self) -> Optional[str]:
        """ctx.room_boot (Stage 5, README S5.8): the last status's ``service.started_at`` as a string, None until a
        status says one. It changes when room_service restarts (the director probes ``source`` again)."""
        st = self._status
        svc = st.get("service") if isinstance(st, dict) else None
        v = svc.get("started_at") if isinstance(svc, dict) else None
        return None if v is None else str(v)

    def view(self) -> contract.RoomView:
        v, t, now = self._view, self._status_at, self._mono()
        reachable = self._ws_up or (self._last_ok is not None and now - self._last_ok <= self.STALE_S)
        if t is None:
            return {**v, "reachable": False, "error": self._error}
        d = max(0.0, now - t)
        out = dict(v)
        out["reachable"] = bool(reachable and self._status is not None)
        out["age_s"] = round(d, 1)
        if not out["reachable"]:
            out["error"] = self._error or "no status"
        if v["since_age_s"] is not None:
            out["since_age_s"] = v["since_age_s"] + d
        out["members"] = {k: {**m, "age_s": None if m["age_s"] is None else m["age_s"] + d,
                              "sent_age_s": None if m["sent_age_s"] is None else m["sent_age_s"] + d}
                          for k, m in v["members"].items()}
        return out

    # -- HTTP -----------------------------------------------------------------------------------------------------------
    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=3.0))
        return self._session

    async def get(self, path: str, *, timeout: float = 3.0) -> tuple[int, bytes]:
        try:
            s = await self._sess()
            async with s.get(self.url + path, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                return r.status, await r.read()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._error = f"GET {path}: {type(e).__name__}: {e}"
            return 0, b""

    async def put(self, path: str, body: dict, *, timeout: float = contract.ROOM_PUT_TIMEOUT_S) -> tuple[int, dict]:
        try:
            s = await self._sess()
            async with s.put(self.url + path, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                raw = await r.read()
                try:
                    obj = json.loads(raw) if raw else {}
                except ValueError:
                    obj = {"error": "bad_response", "detail": raw[:200].decode("utf-8", "replace")}
                return r.status, obj if isinstance(obj, dict) else {"value": obj}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._error = f"PUT {path}: {type(e).__name__}: {e}"
            return 0, {"error": "room_unreachable", "detail": self._error}

    async def patch(self, path: str, body: dict, *, timeout: float = contract.ROOM_PUT_TIMEOUT_S) -> tuple[int, dict]:
        """PATCH ``path`` with a JSON body, as ``put`` (Stage 4: the director's screens palette, PATCH
        /api/v1/screens/{id}/look). (status, the answer's JSON object); 0 and room_unreachable when the room didn't
        answer. Never raises but for cancellation."""
        try:
            s = await self._sess()
            async with s.patch(self.url + path, json=body, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                raw = await r.read()
                try:
                    obj = json.loads(raw) if raw else {}
                except ValueError:
                    obj = {"error": "bad_response", "detail": raw[:200].decode("utf-8", "replace")}
                return r.status, obj if isinstance(obj, dict) else {"value": obj}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._error = f"PATCH {path}: {type(e).__name__}: {e}"
            return 0, {"error": "room_unreachable", "detail": self._error}

    # -- the socket and the poll ----------------------------------------------------------------------------------------
    def _ws_url(self) -> str:
        return ("wss://" + self.url[8:] if self.url.startswith("https://") else "ws://" + self.url[7:]) + "/api/v1/events"

    async def _run(self) -> None:
        try:
            while True:
                try:
                    await self._ws_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._error = f"events: {type(e).__name__}: {e}"
                self._ws_up = False
                if self._last_poll is None or self._mono() - self._last_poll >= self.POLL_S:
                    await self._poll()
                delay = self.RECONNECT_S[min(self._attempt, len(self.RECONNECT_S) - 1)]
                self._attempt += 1
                await asyncio.sleep(delay)
        finally:
            self._ws_up = False
            s, self._session = self._session, None
            if s is not None and not s.closed:
                try:
                    await asyncio.shield(s.close())
                except BaseException:
                    pass

    async def _ws_once(self) -> None:
        s = await self._sess()
        async with s.ws_connect(self._ws_url(), heartbeat=20.0, max_msg_size=16 << 20) as ws:
            self._ws_up = True
            self._attempt = 0
            self.stats["ws_connects"] += 1
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        m = json.loads(msg.data)
                    except ValueError:
                        continue
                    if isinstance(m, dict):
                        _guard("room event", self._on_message, m)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        self._ws_up = False

    async def _poll(self) -> None:
        self._last_poll = self._mono()
        st, body = await self.get("/api/v1/status")
        if st == 200:
            try:
                d = json.loads(body)
            except ValueError:
                return
            if isinstance(d, dict):
                _guard("room status", self._on_status, d)
        elif st:
            self._error = f"GET /api/v1/status answered {st}"

    def _on_message(self, m: dict) -> None:
        t, d = m.get("type"), m.get("data")
        if t == "status" and isinstance(d, dict):
            self._on_status(d)
        elif t == "mode.transition" and isinstance(d, dict):
            self.stats["events"] += 1
            self._on_event(d)
        elif isinstance(t, str) and isinstance(d, dict):
            # Stage 5 (README S5.8): every other event (screen.look, ...) goes to ctx.on_room_event, each callback
            # guarded, counted per type
            counts = self.stats["room_events"]
            if t in counts or len(counts) < self.EVENT_TYPES_MAX:
                counts[t] = counts.get(t, 0) + 1
            for cb in list(getattr(self.ctx, "on_room_event", None) or []):
                _guard("on_room_event", cb, t, d)

    # -- status -> RoomView ---------------------------------------------------------------------------------------------
    def _on_status(self, d: dict) -> None:
        now = self._mono()
        self.stats["statuses"] += 1
        self._status, self._status_at, self._last_ok = d, now, now
        self._error = None
        mode = d.get("mode") if isinstance(d.get("mode"), dict) else {}
        cfg = d.get("config") if isinstance(d.get("config"), dict) else {}
        self._maybe_fetch_config(cfg.get("revision"))
        self._view = self._build(d)
        self._status_notices(mode)
        cur = mode.get("current")
        if self._last_current is not _BAD and cur != self._last_current and isinstance(cur, str):
            old = self._last_current if isinstance(self._last_current, str) else None
            for cb in list(self.ctx.on_mode_change or []):
                _guard("on_mode_change", cb, old, cur)
        self._last_current = cur
        self._baselined = True
        for fn in list(self._status_listeners):
            _guard("status listener", fn)

    def _build(self, d: dict) -> contract.RoomView:
        mode = d.get("mode") if isinstance(d.get("mode"), dict) else {}
        cfg = d.get("config") if isinstance(d.get("config"), dict) else {}
        subs = d.get("subsystems") if isinstance(d.get("subsystems"), dict) else {}
        ap = subs.get("autopilot") if isinstance(subs.get("autopilot"), dict) else None
        b = (ap or {}).get("believed")
        members: dict[str, contract.MemberView] = {}
        for s, sub in subs.items():
            for m, x in ((sub or {}).get("members") or {}).items():
                if isinstance(x, dict):
                    members[f"{s}.{m}"] = {"believed": x.get("believed"), "confidence": x.get("confidence", "unknown"),
                                           "age_s": _num(x.get("age_s")), "sent_age_s": _num(x.get("sent_age_s"))}
        screens = {}
        for sid, sc in (d.get("screens") or {}).items():
            obs = (sc or {}).get("observed") or {}
            phase = obs.get("phase")
            screens[sid] = {"layer": obs.get("layer"), "phase": phase,
                            "cutting": phase is not None and phase not in ("idle", "live", "failed")}
        stored = ((d.get("stored") or {}).get("_restore") or {})
        tr, last = _tview(mode.get("transition")), _tview(mode.get("last_transition"))
        return {"reachable": True, "age_s": 0.0, "error": cfg.get("error") or mode.get("modes_error") or None,
                "current": mode.get("current"), "target": mode.get("target"),
                "since_age_s": _num(mode.get("since_age_s")),
                "transition": tr if tr and tr["status"] == "running" else None,
                "last_transition": last, "last_transition_id": (tr or last or {}).get("id"),
                "autopilot_on": True if b == "on" else False if b == "off" else None,
                "revision": cfg.get("revision"), "modes": self._cfg["modes"], "order": self._cfg["order"],
                "autopilot": self._cfg["autopilot"], "members": members, "screens": screens,
                "stored_restore": {t: (x.get("value") if isinstance(x, dict) else x) for t, x in stored.items()}}

    # -- rules (GET /api/v1/room/config) ----------------------------------------------------------------------------------
    def _maybe_fetch_config(self, revision: Any) -> None:
        if self._cfg_task is not None and not self._cfg_task.done():
            return
        retry = self._cfg["error"] is not None and self._mono() >= self._cfg_next
        if revision != self._cfg_trigger or retry:
            self._cfg_trigger = revision
            loop = self._loop or self.ctx.loop
            if loop is not None:
                self._cfg_task = loop.create_task(self._fetch_config(), name="show-roomlink-config")

    async def _fetch_config(self) -> None:
        self.stats["config_fetches"] += 1
        st, body = await self.get("/api/v1/room/config")
        obj: Any = None
        if st == 200:
            try:
                obj = json.loads(body)
            except ValueError:
                obj = None
        if isinstance(obj, dict) and isinstance(obj.get("config"), dict):
            self._cfg = self._parse(obj["config"], obj.get("revision"), None)
        else:
            err = f"GET /api/v1/room/config answered {st or 'nothing'}: Auto has no rules"
            st2, b2 = await self.get("/api/v1/modes")              # labels and order at least
            cfg = {"modes": {}, "order": [], "autopilot": None, "revision": None, "error": err}
            if st2 == 200:
                try:
                    mv = json.loads(b2)
                    cfg = self._parse({"modes": {n: {"label": m.get("label")} for n, m in mv.get("modes", {}).items()}},
                                      mv.get("revision"), err)
                except (ValueError, AttributeError):
                    pass
            self._cfg = cfg
            self._cfg_next = self._mono() + self.CONFIG_RETRY_S
        if self._status is not None:
            self._view = self._build(self._status)

    @staticmethod
    def _parse(cfg: dict, revision: Any, error: Optional[str]) -> dict:
        modes, order = {}, []
        for name, m in (cfg.get("modes") or {}).items():
            if not isinstance(name, str) or name.startswith("_") or not isinstance(m, dict) or m.get("abstract"):
                continue
            ap = m.get("autopilot")
            label = m.get("label")
            modes[name] = {"label": label if isinstance(label, str) and label else name,
                           "autopilot": ap if isinstance(ap, dict) else None}
            order.append(name)
        top = cfg.get("autopilot")
        return {"modes": modes, "order": order, "autopilot": top if isinstance(top, dict) else None,
                "revision": revision, "error": error}

    # -- transitions ------------------------------------------------------------------------------------------------------
    def _remember(self, tid: str, info: dict) -> dict:
        have = self._info.get(tid)
        if have is None:
            self._info[tid] = have = dict(info)
            while len(self._info) > 256:
                self._info.popitem(last=False)
        else:
            have.update({k: v for k, v in info.items() if v is not None})
        return have

    def _mark(self, tid: str, phase: str) -> bool:
        """True when (tid, phase) is new."""
        s = self._seen.get(tid)
        if s is None:
            s = self._seen[tid] = set()
            while len(self._seen) > 256:
                self._seen.popitem(last=False)
        if phase in s:
            return False
        s.add(phase)
        return True

    def _notice(self, tid: str, phase: str, extra: Optional[dict] = None, via: str = "event") -> None:
        if not self._mark(tid, phase):
            return
        n = {"id": tid, "phase": phase, **(self._info.get(tid) or {}), **(extra or {}), "via": via}
        for fn in list(self._listeners):
            _guard("transition listener", fn, n)

    def _on_event(self, d: dict) -> None:
        tid, phase = d.get("id"), d.get("phase")
        if not isinstance(tid, str):
            return
        if phase == "started":
            self._remember(tid, {k: d.get(k) for k in ("from", "to", "source", "reason", "skip", "rollback_of")})
            self._notice(tid, "started")
        elif phase in ("done", "cancelled"):
            self._remember(tid, {})
            self._notice(tid, phase, {"status": d.get("status") if phase == "done" else "cancelled",
                                      "detail": d.get("detail")})

    def _status_notices(self, mode: dict) -> None:
        for rec in (mode.get("last_transition"), mode.get("transition")):          # older first
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str):
                continue
            tid = rec["id"]
            self._remember(tid, {k: rec.get(k) for k in ("from", "to", "source", "reason", "skip", "rollback_of")})
            st = rec.get("status")
            if not self._baselined:                  # what was there before we looked isn't news
                for ph in ("started", "done", "cancelled"):
                    self._mark(tid, ph)
                continue
            age = _num(rec.get("started_age_s"))
            if age is None or age <= self.EVENT_FRESH_S:
                self._notice(tid, "started", via="status")
            else:
                self._mark(tid, "started")
            if st and st != "running":
                phase = "cancelled" if st == "cancelled" else "done"
                self._notice(tid, phase, {"status": st, "detail": rec.get("detail")}, via="status")


# =====================================================================================================================
# The supervisor
# =====================================================================================================================
class Autopilot:
    """Component "autopilot", 1 Hz. Everything runs on show-brain; ``snapshot()`` is safe from any thread."""

    name = "autopilot"
    tick_hz = contract.SLOW_TICK_HZ
    UNREACHABLE_HOLD_S = 5.0          # after a PUT that couldn't reach the room
    ACTING_LOST_S = 60.0              # an Auto transition with no word of it for this long is given up
    MAYBE_S = 30.0                    # a PUT with no answer: the room's own event for it is adopted this long after
    LOG_KEEP = 200

    def __init__(self, ctx: contract.BrainContext, link: RoomLink, situations: Situations, *,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 local: Optional[Callable[[], datetime]] = None,
                 log_path: Optional[Path] = contract.AUTOPILOT_LOG) -> None:
        self.ctx, self.link, self.sit = ctx, link, situations
        self.cond = Conditions(situations)
        self._wall, self._mono = wall, mono
        self._local = local or datetime.now
        self.log_path = Path(log_path) if log_path else None
        # persisted (README S3.4 "autopilot")
        self.entered: Optional[dict] = None
        self.hold: Optional[dict] = None
        self.pause: Optional[dict] = None
        self.backoff_until: Optional[float] = None
        self.suppressed: list[dict] = []
        self.switches: list[float] = []
        self.last: Optional[dict] = None
        # runtime
        self.acting: Optional[dict] = None
        self._pending: Optional[asyncio.Task] = None
        self._blocked: dict[str, dict] = {}            # rule path -> {"revision", "error"} (a 404/400 from the room)
        self._suggested: set[str] = set()              # modes whose suggestion was said in this episode
        self._chip: Optional[tuple[dict, Optional[float]]] = None      # (announcement, expires mono)
        self._suggest: Optional[tuple[dict, Optional[float]]] = None
        self._candidates: list[dict] = []
        self._infos: dict[str, dict] = {}
        self._would: dict[str, str] = {}              # mode -> rule path: modes Auto would pick now (enter/suggest)
        self._settings: dict = copy.deepcopy(AP_DEFAULTS)
        self._sources: dict[str, str] = {k: "default" for k in AP_DEFAULTS}
        self._settings_key: Any = _BAD
        self._on_prev: Optional[bool] = None
        self._on_override: Optional[tuple[bool, float]] = None
        self._on_listeners: list[Callable[[], None]] = []      # add_on_listener (mic.py's wake)
        self._on_told: Optional[bool] = None
        self._unreachable_until = 0.0
        self._finished: OrderedDict[str, dict] = OrderedDict()
        self._started: OrderedDict[str, dict] = OrderedDict()   # recent started notices with Auto's or Undo's source
        self._maybe: Optional[dict] = None             # a PUT with no answer (README S3.8 "maybe")
        self.put_timeout_s = contract.ROOM_PUT_TIMEOUT_S
        self._state: tuple[str, Optional[str]] = ("off", None)
        self._state_since = mono()
        self._quiet_said = False
        self._last_entered_before: Optional[dict] = None
        self._entered_seq = -1                         # link.status_count() when entered was set
        self._wait_status: Optional[tuple[int, float]] = None   # (status count, mono) at the last room event
        self._log: deque = deque(maxlen=self.LOG_KEEP)
        self.log_errors = 0
        self._snap: dict = {}
        link.add_listener(self._on_transition)
        add = getattr(link, "add_status_listener", None)
        if add is not None:
            add(self._on_maybe_changed)
        self._snap = self._build()

    # -- Component ------------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {}

    def configure(self, settings: dict) -> None:
        pass                                           # Auto's settings live in modes.json (RoomView.autopilot)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()

    def snapshot(self) -> dict:
        return self._snap

    def state(self) -> contract.AutopilotState:
        return self._publish()

    def log(self, n: int = 50) -> list[dict]:
        return list(self._log)[-max(0, int(n)):]

    def persist(self) -> dict:
        last = None
        if self.last:
            last = {k: self.last.get(k) for k in ("at", "from", "to", "reason", "rule", "transition_id", "status")}
        return {"entered": self.entered, "hold": self.hold, "pause": self.pause, "backoff_until": self.backoff_until,
                "suppressed": [{k: s.get(k) for k in ("mode", "rule", "since", "until")} for s in self.suppressed],
                "switches": list(self.switches), "last": last}

    def restore(self, saved: dict) -> None:
        if not isinstance(saved, dict):
            return
        wall = self._wall()

        def rec(v: Any, *keys: str) -> Optional[dict]:
            return dict(v) if isinstance(v, dict) and all(k in v for k in keys) else None

        self.entered = rec(saved.get("entered"), "mode")
        h = rec(saved.get("hold"), "mode", "by", "until")
        self.hold = h if h and (_num(h["until"]) or 0) > wall else None
        p = rec(saved.get("pause"), "why", "until")
        self.pause = p if p and (_num(p["until"]) or 0) > wall else None
        b = _num(saved.get("backoff_until"))
        self.backoff_until = b if b and b > wall else None
        self.suppressed = [dict(s) for s in saved.get("suppressed") or []
                           if isinstance(s, dict) and s.get("mode") and s.get("rule") and (_num(s.get("until")) or 0) > wall]
        self.switches = [t for t in (saved.get("switches") or []) if _num(t) is not None and wall - t < 86400]
        self.last = rec(saved.get("last"), "to", "rule")
        if self.last and self.last.get("status") == "running":
            self.last["status"] = "unknown"            # it ran while we were away
        self._snap = self._build()

    # -- settings (modes.json's top-level "autopilot") ----------------------------------------------------------------------
    def _resolve_settings(self, raw: Any) -> None:
        raw = raw if isinstance(raw, dict) else {}
        key = (json.dumps(raw, sort_keys=True, default=str), self.sit.revision)
        if key == self._settings_key:
            return
        self._settings_key = key
        out, src = {}, {}
        for k in contract.AUTOPILOT_GLOBAL_KEYS:
            d = copy.deepcopy(AP_DEFAULTS[k])
            if k not in raw:
                out[k], src[k] = d, "default"
                continue
            v = self._valid(k, raw[k], d)
            if v is _BAD:
                out[k], src[k] = d, "default (bad value)"
                self._note("settings", None, None, f"modes.json autopilot.{k} is not valid: using the default")
            else:
                out[k], src[k] = v, "modes.json"
        self._settings, self._sources = out, src

    def _valid(self, k: str, v: Any, d: Any) -> Any:
        def nonneg(x: Any, pos: bool = False) -> bool:
            n = _num(x)
            return n is not None and (n > 0 if pos else n >= 0)

        if k == "on":
            return v if isinstance(v, bool) else _BAD
        if k == "quiet_hours":
            if v is None:
                return None
            ok = isinstance(v, dict) and set(v) == {"from", "to"} and _hhmm(v["from"]) is not None and _hhmm(v["to"]) is not None
            return {"from": v["from"], "to": v["to"]} if ok else _BAD
        if k in ("min_dwell_s", "backoff_s"):
            return float(v) if nonneg(v) else _BAD
        if not isinstance(v, dict) or set(v) - set(d):
            return _BAD
        m = {**d, **v}
        if k == "max_switches":
            ok = (_num(m["count"]) is not None and int(m["count"]) == m["count"] and m["count"] >= 1
                  and nonneg(m["per_s"], True) and nonneg(m["pause_s"]))
        elif k == "hold":
            ok = (isinstance(m["until"], dict) and not self.cond.check(m["until"]) and nonneg(m["max_s"], True)
                  and nonneg(m["correction_s"]) and nonneg(m["suppress_s"]))
        else:                                           # chips
            ok = nonneg(m["switch_s"]) and nonneg(m["suggest_s"])
        return m if ok else _BAD

    # -- the tick (1 Hz) ------------------------------------------------------------------------------------------------
    def tick(self, now_s: float) -> None:
        view = self.link.view()
        self._resolve_settings(view.get("autopilot"))
        on = self._on(view)
        if on is True and self._on_prev is False:      # turned on: a fresh start (suppressions kept)
            self.hold = self.pause = None
            self.backoff_until = None
            self._dirty()
        if on is not None:
            self._on_prev = on
        wall = self._wall()
        e = self.entered
        if (e and isinstance(view.get("current"), str) and view["current"] != e.get("mode") and not self.acting
                and view.get("transition") is None and view.get("target") is None
                and self.link.status_count() > self._entered_seq):
            self.entered = None                        # the room is elsewhere now: nothing of Auto's to leave
            self._dirty()
        self._candidates = self._evaluate(view, now_s)
        self._expire(view, now_s, wall)
        self._acting_lost(view)
        state = self._compute_state(view)
        quiet = state == ("paused", "quiet_hours")
        if quiet and not self._quiet_said:
            self._announce("pause", "Auto paused · quiet hours", expires_s=self._settings["chips"]["switch_s"])
        self._quiet_said = quiet
        self._decide(view, state, wall)
        self.cond.prune(now_s)
        self._publish()

    def _on(self, view: contract.RoomView) -> Optional[bool]:
        ov = self._on_override
        if ov is not None and (view.get("autopilot_on") == ov[0] or self._mono() - ov[1] > 15.0):
            self._on_override = None                   # the room has it (or it never will): its word from now on
        return self._on_value(view)

    def _on_value(self, view: contract.RoomView) -> Optional[bool]:
        """Auto on/off for ``view`` with no side effects (any thread): a set_on's own word for up to 15 s until the
        room says the same, else the room's ``autopilot.main``, else modes.json's while the room has no such target;
        None before the room is known."""
        ov = self._on_override
        if ov is not None and view.get("autopilot_on") != ov[0] and self._mono() - ov[1] <= 15.0:
            return ov[0]
        if view.get("autopilot_on") is not None:
            return view["autopilot_on"]
        if not view.get("reachable"):
            return None
        return bool(self._settings.get("on"))         # the room has no autopilot target: modes.json's, default off

    def is_on(self) -> Optional[bool]:
        """Auto on/off now (any thread, no side effects): mic.py reads it (README S5.16)."""
        return self._on_value(self.link.view())

    def add_on_listener(self, fn: Callable[[], None]) -> None:
        """fn() (it must not block; any thread may run it) whenever Auto's on/off may have changed: a set_on, or a room
        status that changed it. mic.py's wake (README S5.16)."""
        self._on_listeners.append(fn)

    def _on_maybe_changed(self) -> None:
        v = self.is_on()
        if v == self._on_told:
            return
        self._on_told = v
        for fn in list(self._on_listeners):
            _guard("on listener", fn)

    def _quiet(self) -> bool:
        q = self._settings.get("quiet_hours")
        if not q:
            return False
        t = self._local()
        return in_window(t.hour * 60 + t.minute + t.second / 60.0, _hhmm(q["from"]), _hhmm(q["to"]))

    def _quiet_left_s(self) -> Optional[float]:
        q = self._settings.get("quiet_hours")
        if not q or not self._quiet():
            return None
        t = self._local()
        end = _hhmm(q["to"])
        target = t.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
        if target <= t:
            target += timedelta(days=1)
        return round((target - t).total_seconds(), 1)

    def _compute_state(self, view: contract.RoomView) -> tuple[str, Optional[str]]:
        if not view.get("reachable") or self._mono() < self._unreachable_until:
            st = ("blind", "room_unreachable")
        elif view.get("error"):
            st = ("blind", "config_error")
        elif self._on(view) is not True:
            st = ("off", None)
        elif self.acting:
            st = ("acting", None)
        elif self.hold:
            st = ("holding", self.hold.get("by"))
        elif self.pause:
            st = ("paused", self.pause.get("why", "flap"))
        elif self.backoff_until:
            st = ("backoff", "failed")
        elif self._quiet():
            st = ("paused", "quiet_hours")
        else:
            st = ("watching", None)
        if st != self._state:
            self._state, self._state_since = st, self._mono()
        return st

    # -- candidates -----------------------------------------------------------------------------------------------------
    def _suppressed(self, mode: str, rule: str) -> bool:
        return any(s["mode"] == mode and s["rule"] == rule for s in self.suppressed)

    def _reason(self, info: dict, rule: str) -> str:
        words = info.get("words") or {}
        true = [w for w, r in words.items() if r.get("value") is True] or list(words)
        return f"{' and '.join(word_label(w) for w in true) or 'its rule holds'} ({rule})"

    def _evaluate(self, view: contract.RoomView, now_s: float) -> list[dict]:
        """Every mode's rules, every tick (so for_s timers run), with why each can't act now."""
        current = view.get("current")
        modes = view.get("modes") or {}
        order = list(view.get("order") or [])
        cands: list[dict] = []
        infos: dict[str, dict] = {}
        would: dict[str, str] = {}
        since = view.get("since_age_s")
        dwell = self._settings["min_dwell_s"]
        engaged = bool(_guard("engaged", self.ctx.engaged)) if self.ctx.engaged else False
        for idx, name in enumerate(order):
            ap = (modes.get(name) or {}).get("autopilot")
            if not isinstance(ap, dict):
                continue
            frm = ap.get("from")
            allowed = None if frm is None else ([frm] if isinstance(frm, str) else [x for x in frm if isinstance(x, str)]
                                                if isinstance(frm, list) else [])
            enter_holds = False
            for kind, key in RULE_KEYS:
                cond = ap.get(key)
                if cond is None:
                    continue
                rule = f"{name}/{key}"
                holds, info = self.cond.holds(cond, rule, now_s)
                infos[rule] = info
                if kind == "enter":
                    enter_holds = holds
                ranks = [rank_of(w) for w in info.get("words") or {}]
                c = {"mode": name, "kind": kind, "rule": rule, "holds": holds, "value": info.get("value"),
                     "held_s": info.get("held_s"), "needs_s": info.get("needs_s"), "blocked": None, "to": name,
                     "rank": min(ranks) if ranks else 99, "order": idx, "info": info}
                b = self._blocked.get(rule)
                if b is not None and b["revision"] != view.get("revision"):
                    del self._blocked[rule]
                    b = None
                if info.get("problems"):
                    c["blocked"] = "rule error: " + "; ".join(info["problems"])
                elif b is not None:
                    c["blocked"] = f"the room refused it ({b['error']}) until modes.json changes"
                elif kind in ("enter", "suggest"):
                    if name == current:
                        c["blocked"] = "it's the current mode"
                    elif allowed is not None and current not in allowed:
                        c["blocked"] = f"only from {', '.join(allowed) or 'no mode'}"
                    elif self._suppressed(name, rule):
                        c["blocked"] = "suppressed: you changed it back"
                    elif kind == "suggest" and enter_holds:
                        c["blocked"] = "it would switch instead"
                    elif kind == "enter" and since is not None and since < dwell:
                        c["blocked"] = f"staying in the current mode {dwell - since:.0f} s more"
                    elif kind == "enter" and self._quiet():
                        c["blocked"] = "quiet hours"
                    if holds and not info.get("problems") and name != current:
                        would.setdefault(name, rule)
                else:                                   # leave: only a mode Auto (or a person, agreeing) entered
                    e = self.entered
                    if not e or e.get("mode") != name:
                        continue
                    to = ap.get("leave_to") or "previous"
                    to = e.get("previous") if to == "previous" else to
                    c["to"] = to
                    if current != name:
                        c["blocked"] = "it isn't the current mode"
                    elif not isinstance(to, str) or to not in modes:
                        c["blocked"] = "no mode to go back to"
                    elif to == current:
                        c["blocked"] = "already there"
                    elif self._suppressed(name, rule):
                        c["blocked"] = "suppressed: you changed it back"
                    elif engaged:
                        c["blocked"] = "someone is using the controls"
                if holds:
                    c["reason"] = self._reason(info, rule)
                cands.append(c)
        self._infos, self._would = infos, would
        return cands

    # -- expiry -----------------------------------------------------------------------------------------------------------
    def _episode_s(self) -> float:
        u = self._settings["hold"].get("until")
        return float(_num(u.get("for_s")) or 0.0) if isinstance(u, dict) else 600.0

    def _expire(self, view: contract.RoomView, now_s: float, wall: float) -> None:
        changed = False
        if self.hold:
            ended = None
            if wall >= (_num(self.hold.get("until")) or 0):
                ended = "the hold's time is up"
            else:
                held, info = self.cond.holds(self._settings["hold"]["until"], "_hold/until", now_s)
                if held:
                    ended = self._reason(info, "hold")
            if ended:
                self.hold = None
                changed = True
                self._announce("resume", "Auto · watching again", reason=ended,
                               expires_s=self._settings["chips"]["switch_s"])
        if self.pause and wall >= (_num(self.pause.get("until")) or 0):
            self.pause, changed = None, True
            self._note("pause_end", None, None, "the pause is over")
        if self.backoff_until and wall >= self.backoff_until:
            self.backoff_until, changed = None, True
            self._note("backoff_end", None, None, "the backoff is over")
        keep, ep = [], self._episode_s()
        for s in self.suppressed:
            if wall >= (_num(s.get("until")) or 0):
                changed = True
                continue
            info = self._infos.get(s["rule"])
            if info is None:                            # the rule is gone
                changed = True
                continue
            if info.get("value") is False:
                s.setdefault("_false_since", now_s)
                if now_s - s["_false_since"] >= ep:
                    changed = True
                    self._note("suppress_end", s["mode"], s["rule"], "the episode is over")
                    continue
            else:
                s.pop("_false_since", None)
            keep.append(s)
        self.suppressed = keep
        if self._chip and self._chip[1] is not None and self._mono() >= self._chip[1]:
            self._chip = None
        if self._suggest is not None:
            m = self._suggest[0].get("mode")
            if (self._suggest[1] is not None and self._mono() >= self._suggest[1]) or m not in self._would \
                    or m == view.get("current"):
                self._suggest = None
        self._suggested &= set(self._would)            # a suggestion that stopped holding ends its episode
        if changed:
            self._dirty()

    def _acting_lost(self, view: contract.RoomView) -> None:
        a = self.acting
        if not a or view.get("transition") is not None or view.get("target") is not None:
            return
        if self._mono() - a["since"] > self.ACTING_LOST_S and view.get("last_transition_id") != a["transition_id"]:
            self.acting = None
            if self.last and self.last.get("transition_id") == a["transition_id"]:
                self.last["status"] = "unknown"
            self._note("lost", a["mode"], a["rule"], "no word of Auto's switch from the room")
            self._dirty()

    # -- deciding ---------------------------------------------------------------------------------------------------------
    def _decide(self, view: contract.RoomView, state: tuple[str, Optional[str]], wall: float) -> None:
        if self._pending is not None and not self._pending.done():
            return
        if self._wait_status is not None:              # the room's status hasn't caught up with its last event yet
            cnt, t = self._wait_status
            if self.link.status_count() <= cnt and self._mono() - t < 10.0:
                return
            self._wait_status = None
        if view.get("transition") is not None or view.get("target") is not None:
            return                                      # a transition runs: only wait
        if state not in (("watching", None), ("paused", "quiet_hours")):
            return
        leaves = [c for c in self._candidates if c["kind"] == "leave" and c["holds"] and not c["blocked"]]
        if leaves:
            if not self._flap(wall):
                self._request(view, leaves[0])
            return
        if state != ("watching", None):
            return
        enters = [c for c in self._candidates if c["kind"] == "enter" and c["holds"] and not c["blocked"]]
        if enters:
            best = min(enters, key=lambda c: (c["rank"], c["order"]))
            if not self._flap(wall):
                self._request(view, best)
            return
        for c in self._candidates:
            if c["kind"] == "suggest" and c["holds"] and not c["blocked"] and c["mode"] not in self._suggested:
                self._suggested.add(c["mode"])
                self._announce("suggest", f"Switch to {self._label(view, c['mode'])}? · {self._words(c)}",
                               mode=c["mode"], previous=view.get("current"), rule=c["rule"],
                               reason=c.get("reason"), evidence=c["info"].get("words"),
                               expires_s=self._settings["chips"]["suggest_s"])

    def _flap(self, wall: float) -> bool:
        """The flap guard: True (and paused) when max_switches.count switches already happened within per_s."""
        ms = self._settings["max_switches"]
        self.switches = [t for t in self.switches if wall - t < ms["per_s"]]
        if len(self.switches) < ms["count"]:
            return False
        self.pause = {"why": "flap", "until": wall + ms["pause_s"]}
        self._dirty()
        self._announce("pause", "Auto paused · too many changes", reason=f"{len(self.switches)} switches in "
                       f"{ms['per_s'] / 60:.0f} min", expires_s=self._settings["chips"]["switch_s"])
        return True

    @staticmethod
    def _label(view: contract.RoomView, mode: Optional[str]) -> str:
        if not mode:
            return "?"
        return ((view.get("modes") or {}).get(mode) or {}).get("label") or mode

    @staticmethod
    def _words(c: dict) -> str:
        r = c.get("reason") or ""
        return r.rsplit(" (", 1)[0] if r else c["rule"]

    @staticmethod
    def _skip_of(view: contract.RoomView, mode: Any) -> list:
        ap = ((view.get("modes") or {}).get(mode) or {}).get("autopilot") or {} if isinstance(mode, str) else {}
        return ap.get("skip") if isinstance(ap.get("skip"), list) else []

    def _request(self, view: contract.RoomView, c: dict) -> None:
        to = c["to"]
        if not isinstance(to, str) or to == view.get("current"):
            return                                      # never re-request the current mode
        skip = self._skip_of(view, to)
        if c["kind"] == "leave":                        # leaving: what the mode being left keeps away from (its
            skip = merge_skips(self._skip_of(view, c["mode"]), skip)   # rule entered it) still holds (§8.9)
        body = {"mode": to, "source": contract.AUTOPILOT_SOURCE, "reason": c.get("reason") or c["rule"],
                "expect": {"current": view.get("current"), "last_transition_id": view.get("last_transition_id")},
                "skip": skip}
        loop = self.ctx.loop or self.link._loop
        if loop is None:
            return
        self._pending = loop.create_task(self._send(c, body, view), name="show-autopilot-put")

    async def _send(self, c: dict, body: dict, view: contract.RoomView) -> None:
        maybe = {"what": "switch", "source": contract.AUTOPILOT_SOURCE, "to": body["mode"], "c": c, "body": body,
                 "from": view.get("current"), "sent_at": self._mono()}
        self._maybe = None                               # a new request supersedes an older unanswered one
        try:
            st, resp = await self.link.put("/api/v1/mode", body, timeout=self.put_timeout_s)
        except asyncio.CancelledError:
            self._maybe = maybe                          # the room may still run it: adopt its event if it comes
            raise
        if st == 202 and isinstance(resp.get("transition_id"), str):
            self._act(maybe, resp["transition_id"])
        elif st == 409:
            self._note("stale", body["mode"], c["rule"], f"the room moved on ({resp.get('detail') or 'stale'})")
        elif st in (400, 404):
            self._blocked[c["rule"]] = {"revision": view.get("revision"),
                                        "error": f"{st} {resp.get('error')}: {resp.get('detail') or ''}".strip()}
            self._note("refused", body["mode"], c["rule"], self._blocked[c["rule"]]["error"])
        else:
            self._unreachable_until = self._mono() + self.UNREACHABLE_HOLD_S
            if st == 0:                                  # no answer: the room may have taken it all the same
                self._maybe = maybe
                self._adopt_seen()
            self._note("unreachable" if st == 0 else "room_error", body["mode"], c["rule"],
                       f"{st or 'no answer'}: {resp.get('error')} {resp.get('detail') or ''}".strip())
        self._publish()

    def _act(self, m: dict, tid: str, adopted: bool = False) -> None:
        """Auto's own switch is running as ``tid`` (from the 202, or adopted from the room's event): acting, last, the
        request noted; resolved at once when the room has already finished it."""
        if self.acting and self.acting["transition_id"] == tid:
            return
        if self.last and self.last.get("transition_id") == tid:
            return                                       # already adopted and resolved
        c, body = m["c"], m["body"]
        current = m["from"]
        self.acting = {"transition_id": tid, "kind": c["kind"], "mode": body["mode"], "from": current,
                       "rule": c["rule"], "reason": body["reason"], "since": self._mono(),
                       "evidence": c["info"].get("words"), "entered_before": self.entered}
        self.last = {"at": self._wall(), "from": current, "to": body["mode"], "reason": body["reason"],
                     "rule": c["rule"], "transition_id": tid, "status": "running"}
        self._note("request", body["mode"], c["rule"], body["reason"], evidence=c["info"].get("words"),
                   extra={"transition_id": tid, "skip": body["skip"], "expect": body["expect"],
                          **({"adopted": True} if adopted else {})})
        self._dirty()
        done = self._finished.get(tid)
        if done is not None:                            # the room finished before its answer reached us
            self._resolve(done)

    def _maybe_match(self, n: dict) -> Optional[dict]:
        """The unanswered request (``_maybe``) this room event is, if any: same source and mode, within MAYBE_S."""
        m = self._maybe
        if m is None:
            return None
        if self._mono() - m["sent_at"] > self.MAYBE_S:
            self._maybe = None
            return None
        return m if n.get("source") == m["source"] and n.get("to") == m["to"] else None

    def _adopt(self, n: dict) -> bool:
        """A room ``started`` that is our own unanswered request: take it as Auto's (switch: acting, announced when it
        ends, counted, undoable; undo: the correction and the hold). True when adopted."""
        m = self._maybe_match(n)
        if m is None or not isinstance(n.get("id"), str):
            return False
        self._maybe = None
        self._unreachable_until = 0.0
        if m["what"] == "switch":
            self._act(m, n["id"], adopted=True)
        else:
            self._undone(m["last"], n["id"])
        return True

    def _adopt_seen(self) -> None:
        """The room's ``started`` may have come before our PUT gave up: look among the recent ones."""
        for n in reversed(list(self._started.values())):
            if self._adopt(n):
                return

    # -- the room's transitions -------------------------------------------------------------------------------------------
    def _on_transition(self, n: dict) -> None:
        tid, phase = n.get("id"), n.get("phase")
        if n.get("via") == "event":                    # the status that reflects it is still on its way
            self._wait_status = (self.link.status_count(), self._mono())
        if phase in ("done", "cancelled"):
            self._finished[tid] = n
            while len(self._finished) > 64:
                self._finished.popitem(last=False)
            if self.acting and tid == self.acting["transition_id"]:
                self._resolve(n)
        elif phase == "started" and n.get("source") in (contract.AUTOPILOT_SOURCE, contract.UNDO_SOURCE):
            self._started[tid] = n                     # ours, perhaps, with no answer to our PUT (yet)
            while len(self._started) > 16:
                self._started.popitem(last=False)
            self._adopt(n)
        elif phase == "started" and n.get("source") not in NOT_A_PERSON:
            if self._on(self.link.view()) is True:
                self._person(n)
        self._publish()

    def _resolve(self, n: dict) -> None:
        a, self.acting = self.acting, None
        if a is None:
            return
        wall = self._wall()
        status = "cancelled" if n.get("phase") == "cancelled" else (n.get("status") or "failed")
        if self.last and self.last.get("transition_id") == a["transition_id"]:
            self.last["status"] = "done" if status == "ok" else status
        view = self.link.view()
        chips = self._settings["chips"]
        if status == "cancelled":                       # someone else's request took over; its own "started"
            self._note("cancelled", a["mode"], a["rule"], "someone else's choice took over")   # says agree/hold
        elif status in ("ok", "partial"):
            if a["kind"] == "enter":
                self.entered = {"mode": a["mode"], "previous": a["from"], "rule": a["rule"], "at": wall,
                                "transition_id": a["transition_id"], "by": "auto"}
                self._entered_seq = self.link.status_count()
            else:
                self.entered = None
            self._last_entered_before = a["entered_before"]
            self.switches.append(wall)
            if self.last:
                self.last["at"] = wall
            words = a["reason"].rsplit(" (", 1)[0]
            self._announce("switch", f"Auto → {self._label(view, a['mode'])} · {words}", mode=a["mode"],
                           previous=a["from"], rule=a["rule"], tid=a["transition_id"], undo=True,
                           reason=a["reason"], evidence=a["evidence"], expires_s=chips["switch_s"])
            if status == "partial":
                self._fail(a, wall, "didn't fully finish")
        else:                                           # failed, or rolled back
            self._fail(a, wall, "didn't finish")
        self._dirty()

    def _fail(self, a: dict, wall: float, how: str) -> None:
        b = self._settings["backoff_s"]
        self.backoff_until = wall + b
        view = self.link.view()
        self._announce("failed", f"Auto: the switch to {self._label(view, a['mode'])} {how} · waiting {b / 60:.0f} min",
                       mode=a["mode"], previous=a["from"], rule=a["rule"], tid=a["transition_id"],
                       expires_s=self._settings["chips"]["switch_s"])

    def _person(self, n: dict) -> None:
        """People win (README S3.8): agreeing, a correction, or a hold."""
        view = self.link.view()
        to, frm, wall = n.get("to"), n.get("from"), self._wall()
        if not isinstance(to, str):
            return
        hs = self._settings["hold"]
        e = self.entered
        last = self.last
        in_window = bool(last and _num(last.get("at")) is not None and wall - last["at"] <= hs["correction_s"]
                         and last.get("status") in ("running", "done", "partial", "cancelled"))
        back = in_window and to == last.get("from")    # straight back to where Auto just came from: a correction,
        if not back and (to in self._would or (e and e.get("mode") == to)):   # even if a rule would pick it too
            prev = e["previous"] if e and e.get("mode") == to else frm
            self.entered = {"mode": to, "previous": prev, "rule": self._would.get(to) or (e or {}).get("rule"),
                            "at": wall, "transition_id": n.get("id"), "by": "person"}
            self._entered_seq = self.link.status_count()
            if self.hold:
                self.hold = None
            self._note("agree", to, self.entered["rule"], "a person chose what Auto would choose")
            self._dirty()
            return
        if in_window:
            self._suppress(last["rule"], wall)
            by = "correction"
        else:
            by = "person"
        self.hold = {"mode": to, "by": by, "since": wall, "until": wall + hs["max_s"]}
        self.cond.reset("_hold")
        if e and e.get("mode") != to:
            self.entered = None
        self._announce("hold", f"Auto paused · you chose {self._label(view, to)}", mode=to, previous=frm,
                       rule=last.get("rule") if by == "correction" and last else None, tid=n.get("id"),
                       reason=by, expires_s=self._settings["chips"]["switch_s"])
        self._dirty()

    def _suppress(self, rule: str, wall: float) -> None:
        mode = rule.rsplit("/", 1)[0]
        self.suppressed = [s for s in self.suppressed if not (s["mode"] == mode and s["rule"] == rule)]
        self.suppressed.append({"mode": mode, "rule": rule, "since": wall,
                                "until": wall + self._settings["hold"]["suppress_s"]})
        self._note("suppress", mode, rule, "a correction: not again this episode")

    # -- api.py's calls (show-brain) --------------------------------------------------------------------------------------
    async def set_on(self, on: bool) -> contract.AutopilotState:
        st, resp = await self.link.put("/api/v1/room/targets",
                                       {"values": {"autopilot.main": "on" if on else "off"}, "source": "show"},
                                       timeout=self.put_timeout_s)
        if st == 0:
            raise RuntimeError("room_unreachable")
        if st not in (200, 202):
            e = ValueError(str(resp.get("error") or f"http_{st}"))
            e.detail, e.status = resp.get("detail") or "", st  # type: ignore[attr-defined]
            raise e
        self._on_override = (bool(on), self._mono())
        self._on_maybe_changed()                       # mic.py hears it at once (README S5.16)
        self._note("on" if on else "off", None, None, "Auto turned " + ("on" if on else "off"))
        return self._publish()

    async def undo(self) -> dict:
        view = self.link.view()
        last = self.last
        if (not last or last.get("status") not in ("done", "partial") or not isinstance(last.get("from"), str)
                or view.get("current") != last.get("to") or view.get("transition") is not None
                or view.get("target") is not None or (self._pending is not None and not self._pending.done())):
            raise LookupError("nothing_to_undo")
        body = {"mode": last["from"], "source": contract.UNDO_SOURCE, "reason": "Undo Auto",
                "expect": {"current": view.get("current"), "last_transition_id": view.get("last_transition_id")}}
        maybe = {"what": "undo", "source": contract.UNDO_SOURCE, "to": last["from"], "last": last,
                 "sent_at": self._mono()}
        self._maybe = None
        try:
            st, resp = await self.link.put("/api/v1/mode", body, timeout=self.put_timeout_s)
        except asyncio.CancelledError:
            self._maybe = maybe                          # the room may still run the undo: adopt it if it comes
            raise
        if st == 0:
            self._maybe = maybe
            self._adopt_seen()
            raise RuntimeError("room_unreachable")
        if st == 409:
            raise LookupError("nothing_to_undo")
        if st != 202:
            e = ValueError(str(resp.get("error") or f"http_{st}"))
            e.detail, e.status = resp.get("detail") or "", st  # type: ignore[attr-defined]
            raise e
        self._undone(last, resp.get("transition_id"))
        return {"undone": True, "mode": last["from"], "transition_id": resp.get("transition_id")}

    def _undone(self, last: dict, tid: Optional[str]) -> None:
        """An Undo the room took (from its 202, or adopted from its event): a correction, a hold, and said."""
        if last.get("status") == "undone":
            return
        view = self.link.view()
        wall = self._wall()
        self._suppress(last["rule"], wall)
        self.hold = {"mode": last["from"], "by": "undo", "since": wall, "until": wall + self._settings["hold"]["max_s"]}
        self.cond.reset("_hold")
        leave = last["rule"].endswith("/leave_when")
        self.entered = self._last_entered_before if leave else None
        self._entered_seq = self.link.status_count()
        self._wait_status = (self.link.status_count(), self._mono())
        last["status"] = "undone"
        self._chip = None
        self._announce("undo", f"Undone · back to {self._label(view, last['from'])} · Auto paused",
                       mode=last["from"], previous=last["to"], rule=last["rule"], tid=tid,
                       expires_s=self._settings["chips"]["switch_s"])
        self._dirty()
        self._publish()

    def resume(self) -> contract.AutopilotState:
        self.hold = self.pause = None
        self.backoff_until = None
        self.cond.reset("")
        self._suggested.clear()
        self._announce("resume", "Auto · watching again", reason="Resume",
                       expires_s=self._settings["chips"]["switch_s"])
        self._dirty()
        return self._publish()

    # -- saying it ------------------------------------------------------------------------------------------------------
    def _dirty(self) -> None:
        if self.ctx.persist is not None:
            _guard("persist", self.ctx.persist)

    def _announce(self, kind: str, text: str, *, mode: Optional[str] = None, previous: Optional[str] = None,
                  rule: Optional[str] = None, tid: Optional[str] = None, undo: bool = False,
                  reason: Optional[str] = None, evidence: Optional[dict] = None,
                  expires_s: Optional[float] = None) -> contract.AutoAnnouncement:
        ann: contract.AutoAnnouncement = {"kind": kind, "text": text, "mode": mode, "previous": previous,
                                          "rule": rule, "transition_id": tid, "undo": undo,
                                          "expires_in_s": expires_s}
        exp = None if expires_s is None else self._mono() + expires_s
        if kind == "suggest":
            self._suggest = (ann, exp)
        else:
            self._chip = (ann, exp)
        if self.ctx.notify is not None:
            _guard("notify", self.ctx.notify, "auto", dict(ann))
        self._note(kind, mode, rule, reason or text, evidence=evidence, extra={"text": text})
        return ann

    def _note(self, kind: str, mode: Optional[str], rule: Optional[str], reason: str, *,
              evidence: Optional[dict] = None, extra: Optional[dict] = None) -> None:
        """One decision: the in-memory log (GET /api/v1/log) and contract.AUTOPILOT_LOG (one JSON line)."""
        view = self.link.view()
        e = {"at": round(self._wall(), 3), "kind": kind, "mode": mode, "rule": rule, "reason": reason,
             "evidence": evidence or {}, "room": {"current": view.get("current"), "revision": view.get("revision")},
             **(extra or {})}
        self._log.append(e)
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self.log_path.stat().st_size > LOG_MAX_BYTES:
                    self.log_path.replace(self.log_path.with_name(self.log_path.name + ".1"))
            except FileNotFoundError:
                pass
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e, default=str) + "\n")
        except OSError:
            self.log_errors += 1

    # -- the view (AutopilotState) ----------------------------------------------------------------------------------------
    def _publish(self) -> contract.AutopilotState:
        self._snap = self._build()
        return self._snap

    def _build(self) -> contract.AutopilotState:
        view = self.link.view()
        now_m, wall = self._mono(), self._wall()
        state, why = self._compute_state(view)
        until: Optional[float] = None
        label = self._label
        if state == "off":
            line = "Auto is off"
        elif state == "watching":
            line = "Auto · watching"
        elif state == "acting":
            line = f"Auto → {label(view, (self.acting or {}).get('mode'))}…"
        elif state == "holding":
            until = (_num(self.hold.get("until")) or wall) - wall
            line = f"Auto paused · you chose {label(view, self.hold.get('mode'))} · Resume"
        elif state == "paused" and why == "quiet_hours":
            until = self._quiet_left_s()
            line = "Auto paused · quiet hours"
        elif state == "paused":
            until = (_num(self.pause.get("until")) or wall) - wall
            line = "Auto paused · too many changes · Resume"
        elif state == "backoff":
            until = (self.backoff_until or wall) - wall
            line = "Auto waiting · the last switch didn't finish · Resume"
        else:
            line = "Auto · not listening" if why == "room_unreachable" else "Auto is paused: a mode has a problem; open its editor"

        def chip(c: Optional[tuple]) -> Optional[dict]:
            if not c:
                return None
            ann, exp = c
            if exp is not None and now_m >= exp:
                return None
            return {**ann, "expires_in_s": None if exp is None else round(exp - now_m, 1)}

        e, h, last = self.entered, self.hold, self.last
        return {
            "on": self._on(view), "state": state, "why": why, "line": line,
            "since_s": round(now_m - self._state_since, 1),
            "until_in_s": None if until is None else round(max(0.0, until), 1),
            "entered": None if not e else {"mode": e.get("mode"), "previous": e.get("previous"), "rule": e.get("rule"),
                                           "at_age_s": round(wall - (_num(e.get("at")) or wall), 1),
                                           "transition_id": e.get("transition_id"), "by": e.get("by")},
            "hold": None if not h else {"mode": h.get("mode"), "by": h.get("by"),
                                        "since_age_s": round(wall - (_num(h.get("since")) or wall), 1),
                                        "until_in_s": round(max(0.0, (_num(h.get("until")) or wall) - wall), 1)},
            "last": None if not last else {"age_s": round(wall - (_num(last.get("at")) or wall), 1),
                                           "from": last.get("from"), "to": last.get("to"),
                                           "reason": last.get("reason"), "transition_id": last.get("transition_id"),
                                           "status": last.get("status"), "source": contract.AUTOPILOT_SOURCE},
            "chip": chip(self._chip), "suggest": chip(self._suggest),
            "suppressed": [{"mode": s["mode"], "rule": s["rule"],
                            "until_in_s": round(max(0.0, (_num(s.get("until")) or wall) - wall), 1)}
                           for s in self.suppressed],
            "candidates": [{k: c.get(k) for k in ("mode", "kind", "rule", "held_s", "needs_s", "blocked", "holds")}
                           for c in self._candidates],
            "settings": {k: {"value": self._settings[k], "source": self._sources.get(k, "default")}
                         for k in contract.AUTOPILOT_GLOBAL_KEYS},
            "room": {"reachable": bool(view.get("reachable")), "revision": view.get("revision"),
                     "current": view.get("current"), "transition_running": view.get("transition") is not None,
                     "rules_error": self.link.rules_error()},
        }


# =====================================================================================================================
def attach(ctx: contract.BrainContext, **kw: Any) -> list:
    """[RoomLink, Situations, Autopilot] for brain.add, in that order. Keyword args (tests): ``wall``, ``mono``,
    ``local`` (a datetime factory) and ``log_path``."""
    mono = kw.get("mono", time.monotonic)
    link = RoomLink(ctx, mono=mono)
    sit = Situations(ctx, local=kw.get("local"), mono=mono)
    ap = Autopilot(ctx, link, sit, wall=kw.get("wall", time.time), mono=mono, local=kw.get("local"),
                   log_path=kw.get("log_path", contract.AUTOPILOT_LOG))
    return [link, sit, ap]
