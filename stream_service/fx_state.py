"""A session's master FX bus, declaratively: GET / PUT /api/sessions/{id}/fx (EFFECTS_LAYER_DESIGN D.2).

``PUT`` replaces the whole stack.  Items match the current slots by ``id``, then (items without an id) by
plug-in and preset; matched slots get their params / bindings eased and their level faded over ``fade_s``,
unmatched slots fade out and retire, new items are set up and primed on a worker thread and fade in from 0.
Everything is validated before anything changes (400); PUTs are serialised by a lock and prepared outside the
session lock, so an exit never waits behind one.  An item whose plug-in fails to prepare is left out and listed
in ``rejected``.  Each accepted change bumps ``rev`` and emits ``session.fx``.

Engine slots have their own ids (``e<n>``) so a declarative id can be reused at once while its old slot still
fades out.  The FX director (fx_director.py) changes the stack through :meth:`SessionFx.auto_move`
(``origin: "auto"``); a manual change to an auto slot makes it manual.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .effects import MASTER_PROPS, MAX_BUS_SLOTS, EffectSlot, FxUpdate, SlotUpdate
from .modulation import Modulation, split_bindings
from .plugins.base import ParamError
from .presets import EffectSpec

log = logging.getLogger("stream_service.fx")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FADE_DEFAULT_S = 2.0
FADE_MAX_S = 30.0
FX_STATES = ("starting", "holding", "entering", "live")
ITEM_KEYS = {"id", "level", "bypass", "preset", "effect", "params", "bindings"}
READ_ONLY_KEYS = {"label", "stage", "origin", "state", "level_now", "amount_now", "cost_ms"}
# The room's gesture slot (gestures.py, docs/ENSEMBLE.md §8.2): origin GESTURE, id GESTURE_ID, last on the bus.  The
# ensemble's own: gestures.sync_slots adds and removes it (:meth:`SessionFx.gesture`); a PUT keeps it as it is (an
# echoed GET entry with its id is left out of the parse), a removal never takes it, and it is never counted toward
# MAX_BUS_SLOTS.  The FX director never sees it (fx_director.bus_slots).
GESTURE = "gesture"
GESTURE_ID = "gesture"


@dataclass
class SlotState:
    id: str
    spec: EffectSpec
    level: float
    bypass: bool
    origin: str                         # manual | auto | gesture (GESTURE)
    stage: str
    label: str
    engine_slot: Any = None             # effects.EffectSlot (render-thread object; plain reads only)
    seq: int = 0

    def target(self) -> float:
        return 0.0 if self.bypass else self.level

    def state(self) -> str:
        s = self.engine_slot
        return s.state if s is not None else ("bypassed" if self.bypass else "fading_in")

    def entry(self) -> dict:
        """The event / summary form."""
        return {"id": self.id, "effect": self.spec.effect, "preset": self.spec.preset, "label": self.label,
                "stage": self.stage, "level": self.level, "bypass": self.bypass, "origin": self.origin}


@dataclass
class _Item:
    id: Optional[str]
    spec: EffectSpec
    level: float = 1.0
    bypass: bool = False
    origin: Optional[str] = None        # None: keep the matched slot's (new: the cause's)
    index: int = 0


@dataclass
class _Rejected:
    index: int
    effect: str
    error: str
    detail: str

    def as_dict(self) -> dict:
        return {"index": self.index, "effect": self.effect, "error": self.error, "detail": self.detail}


def _unit(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        raise ParamError("bad_request", f"{what} must be a number 0-1")
    return max(0.0, min(1.0, float(v)))


class SessionFx:
    def __init__(self, session: Any):
        self.s = session
        self.stack: list[SlotState] = []
        self.rev = 0
        self.fade_s = FADE_DEFAULT_S
        self.master: dict = {"amount": 1.0, "pulse": 1.0}   # as given: values or normalised bindings
        self.lock = asyncio.Lock()
        self.epoch = 0
        self.open = False
        self.errors: collections.deque = collections.deque(maxlen=16)
        self.retiring: dict[str, SlotState] = {}            # engine id -> removed slot still fading out
        self._n = 0
        self._eid = 0
        self._seq = 0

    # -- helpers -------------------------------------------------------------------
    @property
    def presets(self):
        return self.s.mgr.presets

    @property
    def registry(self):
        return self.s.mgr.registry

    def _next_id(self, used: set[str]) -> str:
        while True:
            self._n += 1
            sid = f"fx-{self._n}"
            if sid not in used:
                return sid

    def by_engine_id(self, eid: str) -> Optional[SlotState]:
        for st in self.stack:
            if st.engine_slot is not None and st.engine_slot.slot_id == eid:
                return st
        return self.retiring.get(eid)

    def _check_state(self) -> None:
        from .sessions import SessionError

        if self.s.state not in FX_STATES or self.s.exited:
            raise SessionError(409, "bad_state", f"session is {self.s.state}; the FX bus needs "
                                                 f"{'/'.join(FX_STATES)} (and no exit)")

    # -- parsing (400 before anything changes) ---------------------------------------
    def parse_item(self, raw: Any, index: int) -> _Item:
        if isinstance(raw, str):
            return _Item(None, self.presets.resolve_effect(raw), index=index)
        if not isinstance(raw, dict):
            raise ParamError("bad_spec", f"stack[{index}] must be a preset name or an object")
        unknown = set(raw) - ITEM_KEYS - READ_ONLY_KEYS
        if unknown:
            raise ParamError("bad_spec", f"stack[{index}]: unknown keys {sorted(unknown)}")
        sid = raw.get("id")
        if sid is not None and (not isinstance(sid, str) or not ID_RE.match(sid)):
            raise ParamError("bad_request", f"stack[{index}].id must match {ID_RE.pattern}")
        spec = {k: raw[k] for k in ("preset", "effect", "params", "bindings") if k in raw}
        if spec.get("preset") is not None and "effect" in spec:   # a GET entry echoed back: the preset wins
            spec.pop("effect")
        elif "preset" in spec and spec["preset"] is None:
            spec.pop("preset")
        level = _unit(raw["level"], f"stack[{index}].level") if raw.get("level") is not None else 1.0
        bypass = raw.get("bypass", False)
        if not isinstance(bypass, bool):
            raise ParamError("bad_request", f"stack[{index}].bypass must be true or false")
        return _Item(sid, self.presets.resolve_effect(spec), level, bypass, index=index)

    def parse_master(self, raw: Any) -> dict:
        if not isinstance(raw, dict) or set(raw) - set(MASTER_PROPS):
            raise ParamError("bad_request", "master must be {\"amount\"?: value|binding, \"pulse\"?: value|binding}")
        plain, bindings = split_bindings(MASTER_PROPS, raw)
        out = {}
        for k in raw:
            out[k] = bindings[k] if k in bindings else max(0.0, min(1.0, float(plain[k])))
        return out

    def parse(self, body: Any) -> dict:
        if not isinstance(body, dict):
            raise ParamError("bad_request", "body must be a JSON object")
        unknown = set(body) - {"stack", "fade_s", "master", "if_rev"}
        if unknown:
            raise ParamError("bad_request", f"unknown fields {sorted(unknown)}")
        stack = body.get("stack")
        if not isinstance(stack, list):
            raise ParamError("bad_request", "stack must be a list of effect specs")
        stack = [raw for raw in stack if not (isinstance(raw, dict) and raw.get("id") == GESTURE_ID)]   # kept as is
        if len(stack) > MAX_BUS_SLOTS:
            raise ParamError("too_many_effects", f"at most {MAX_BUS_SLOTS} effects on the bus")
        items = [self.parse_item(raw, i) for i, raw in enumerate(stack)]
        ids = [it.id for it in items if it.id is not None]
        if len(ids) != len(set(ids)):
            raise ParamError("bad_request", "stack ids must be unique")
        fade = body.get("fade_s", FADE_DEFAULT_S)
        if isinstance(fade, bool) or not isinstance(fade, (int, float)) or fade != fade:
            raise ParamError("bad_request", "fade_s must be a number of seconds")
        fade = max(0.0, min(FADE_MAX_S, float(fade)))
        master = self.parse_master(body["master"]) if body.get("master") is not None else None
        if_rev = body.get("if_rev")
        if if_rev is not None and (isinstance(if_rev, bool) or not isinstance(if_rev, int)):
            raise ParamError("bad_request", "if_rev must be an integer")
        return {"items": items, "fade_s": fade, "master": master, "if_rev": if_rev}

    # -- PUT -------------------------------------------------------------------------
    async def put(self, body: Any, cause: str = "manual", detail: Optional[str] = None) -> dict:
        from .sessions import SessionError

        self._check_state()
        parsed = self.parse(body)
        async with self.lock:
            if parsed["if_rev"] is not None and parsed["if_rev"] != self.rev:
                raise SessionError(409, "fx_conflict", f"the FX stack is at rev {self.rev}, not {parsed['if_rev']}")
            items = parsed["items"]
            g = next((st for st in self.stack if st.origin == GESTURE), None)
            if g is not None:                              # the room's gesture slot stays as it is
                items = [*items, _Item(g.id, g.spec, g.level, g.bypass, index=len(items))]
            return await self._apply(items, parsed["fade_s"], parsed["master"], cause, detail)

    async def gesture(self, on: bool, spec: Optional[EffectSpec], fade_s: float) -> Optional[dict]:
        """The room's gesture slot (gestures.sync_slots, loop thread): ``on`` puts ``spec`` last on the bus (origin
        GESTURE), off takes it away; nothing else on the bus changes.  None when the session is not live or the
        slot is already so."""
        if self.s.state != "live" or self.s.exited:
            return None
        async with self.lock:
            if any(st.origin == GESTURE for st in self.stack) == on:
                return None
            items = [_Item(st.id, st.spec, st.level, st.bypass, index=i)
                     for i, st in enumerate(st for st in self.stack if st.origin != GESTURE)]
            if on:
                if spec is None:
                    return None
                items.append(_Item(GESTURE_ID, spec, 1.0, False, origin=GESTURE, index=len(items)))
            return await self._apply(items, fade_s, None, GESTURE, "the room's gestures " + ("on" if on else "off"))

    async def remove(self, keep: Any, fade_s: float, detail: Optional[str] = None) -> Optional[dict]:
        """Take slots off the bus (loop thread): every slot for which ``keep(slot)`` is false (never the room's
        gesture slot).  None when the state no longer allows it."""
        if self.s.state != "live" or self.s.exited:
            return None
        async with self.lock:
            items = [_Item(st.id, st.spec, st.level, st.bypass, index=i)
                     for i, st in enumerate(st for st in self.stack if keep(st) or st.origin == GESTURE)]
            if len(items) == len(self.stack):
                return self.summary()
            return await self._apply(items, fade_s, None, "manual", detail)

    async def auto_move(self, move: Any, fade_s: float) -> Optional[dict]:
        """Apply an FX director move (loop thread).  None when the state no longer allows it."""
        if self.s.state != "live" or self.s.exited:
            return None
        async with self.lock:
            # index = the position in the new list (a swap of any slot but the last left two items on one index:
            # the added effect then took the kept slot's place and the bus showed one effect twice)
            items = [_Item(st.id, st.spec, st.level, st.bypass, index=i)
                     for i, st in enumerate(st for st in self.stack if st.id != move.remove)]
            if move.add is not None:
                if sum(1 for st in self.stack if st.id != move.remove and st.origin != GESTURE) >= MAX_BUS_SLOTS:
                    return None
                level = max(0.0, min(1.0, float(getattr(move, "level", 1.0))))   # the interplay's ceiling
                items.append(_Item(None, self.presets.resolve_effect(move.add), level, False, origin="auto",
                                   index=len(items)))
            return await self._apply(items, fade_s, None, "auto", move.why)

    async def _apply(self, items: list[_Item], fade_s: float, master: Optional[dict], cause: str,
                     detail: Optional[str]) -> dict:
        from .sessions import SessionError

        s = self.s
        epoch = self.epoch
        current = list(self.stack)
        matched: dict[int, SlotState] = {}
        free = list(current)
        for it in items:                                   # by id (same plug-in: else replace)
            if it.id is None:
                continue
            st = next((c for c in free if c.id == it.id), None)
            if st is not None and st.spec.effect == it.spec.effect:
                matched[it.index] = st
                free.remove(st)
        for it in items:                                   # no id: the first slot with the same plug-in / preset
            if it.id is not None or it.index in matched:
                continue
            st = next((c for c in free if c.spec.effect == it.spec.effect and c.spec.preset == it.spec.preset), None)
            if st is not None:
                matched[it.index] = st
                free.remove(st)
        used = {it.id for it in items if it.id is not None} | {st.id for st in matched.values()}
        # prepare the new slots (worker thread; outside the session lock)
        new_states: dict[int, SlotState] = {}
        rejected: list[_Rejected] = []
        for it in items:
            if it.index in matched:
                continue
            sid = it.id or self._next_id(used)
            used.add(sid)
            try:
                st = await self._prepare_slot(sid, it, fade_s, cause)
            except SessionError:
                raise
            except Exception as e:
                d = f"{type(e).__name__}: {e}"[:300]
                rejected.append(_Rejected(it.index, it.spec.effect, "prepare_failed", d))
                s.note_prepare_failure("effect", it.spec.effect, it.spec.preset, e, slot=sid)
                continue
            new_states[it.index] = st
        if self.epoch != epoch or s.state not in FX_STATES or s.exited:
            for st in new_states.values():
                s.retire(st.engine_slot.effect)
            raise SessionError(409, "bad_state", f"session is {s.state}")
        updates: list[SlotUpdate] = []
        changed: list[str] = []
        for it in items:
            st = matched.get(it.index)
            if st is None:
                continue
            spec_changed = it.spec.params != st.spec.params or it.spec.bindings != st.spec.bindings
            level_changed = it.level != st.level or it.bypass != st.bypass
            u = SlotUpdate(st.engine_slot.slot_id)
            if spec_changed:
                u.params, u.bindings = dict(it.spec.params), dict(it.spec.bindings)
            u.target = 0.0 if it.bypass else it.level
            if spec_changed or level_changed:
                changed.append(st.id)
                if st.origin == "auto" and cause == "manual":
                    st.origin = "manual"
            if it.id is not None and it.id != st.id:
                st.id = it.id
            st.spec, st.level, st.bypass = it.spec, it.level, it.bypass
            st.label = it.spec.label or st.label
            updates.append(u)
        removed = [st for st in free]
        for st in removed:
            updates.append(SlotUpdate(st.engine_slot.slot_id, remove=True))
            self.retiring[st.engine_slot.slot_id] = st
        stack = [matched.get(it.index) or new_states.get(it.index) for it in items]
        stack = [st for st in stack if st is not None]
        from .effects import stage_index

        stack.sort(key=lambda st: (stage_index(st.stage), st.origin == GESTURE, st.seq))
        self.stack = stack
        self.rev += 1
        self.fade_s = fade_s
        if master is not None:
            self.master = {**self.master, **master}
        s.engine.submit(FxUpdate(self.rev, add=[st.engine_slot for st in new_states.values()], update=updates,
                                 fade_s=fade_s, master=master))
        added = [st.id for st in new_states.values()]
        self._emit(cause, added=added, removed=[st.id for st in removed], changed=changed, detail=detail)
        out = self.get()
        if rejected:
            out["rejected"] = [r.as_dict() for r in rejected]
        return out

    async def _prepare_slot(self, sid: str, it: _Item, fade_s: float, cause: str) -> SlotState:
        s = self.s
        g = it.origin == GESTURE                           # the room's gesture slot: an internal plug-in, last
        cls = self.registry.effect(it.spec.effect, allow_internal=g)
        eff = cls()
        eff = await s.prepare_effect(eff, it.spec.params)
        self._eid += 1
        self._seq += 1
        mod = Modulation(s.runtime, cls.param_props(), it.spec.bindings, key=f"effect/{it.spec.effect}")
        slot = EffectSlot(f"e{self._eid}", eff, name=cls.name, preset=it.spec.preset,
                          stage=getattr(cls, "stage", "stylise"), modulation=mod,
                          mix=getattr(cls, "amount_mode", "native") == "mix", level=0.0,
                          target=0.0 if it.bypass else it.level, fade_s=fade_s, where="gesture" if g else "bus",
                          label=it.spec.label)
        origin = it.origin or ("auto" if cause == "auto" else "manual")
        return SlotState(sid, it.spec, it.level, it.bypass, origin, slot.stage, it.spec.label or cls.name,
                         slot, self._seq)

    # -- engine / session callbacks (loop thread) --------------------------------------
    def on_retired(self, ids: list[str]) -> None:
        for eid in ids:
            self.retiring.pop(eid, None)

    def on_slot_failed(self, eid: str, detail: str) -> Optional[str]:
        """A bus slot failed in render: drop it from the stack (rev + 1, session.fx cause error)."""
        st = next((x for x in self.stack if x.engine_slot is not None and x.engine_slot.slot_id == eid), None)
        if st is None:
            return None
        self.stack.remove(st)
        self.retiring[eid] = st
        self.rev += 1
        self.errors.append({"slot": st.id, "effect": st.spec.effect, "preset": st.spec.preset, "detail": detail,
                            "at_pts": round(self.s.pts_now(), 3)})
        self.s.engine.submit(FxUpdate(self.rev, update=[SlotUpdate(eid, remove=True)], fade_s=0.0))
        self._emit("error", removed=[st.id], detail=f"{st.spec.name} failed: {detail}")
        return st.id

    def on_bus(self, is_open: bool) -> None:
        if is_open == self.open:
            return
        self.open = is_open
        self._emit("bus")

    def cancel_for_exit(self) -> None:
        self.epoch += 1

    # -- reporting -------------------------------------------------------------------
    def _emit(self, cause: str, *, added=(), removed=(), changed=(), detail: Optional[str] = None) -> None:
        self.s.emit("session.fx", rev=self.rev, cause=cause, open=self.open, fade_s=self.fade_s, added=list(added),
                    removed=list(removed), changed=list(changed), stack=[st.entry() for st in self.stack],
                    **({"detail": detail} if detail else {}))

    def _master_out(self) -> dict:
        bus = getattr(self.s.engine, "bus", None)
        return {**self.master, "level_now": None if bus is None else round(float(bus.level), 4)}

    def get(self) -> dict:
        cost = getattr(self.s.mgr, "cost_ms", None)
        stack = []
        for st in self.stack:
            es = st.engine_slot
            plain = {k: v for k, v in st.spec.params.items() if k not in st.spec.bindings}
            c = cost("effect", st.spec.preset or st.spec.effect) if cost else None
            stack.append({**st.entry(), "params": plain, "bindings": st.spec.bindings, "state": st.state(),
                          "level_now": None if es is None else round(float(es.level), 4),
                          "amount_now": None if es is None else round(float(es.amount_now), 4),
                          "cost_ms": None if c is None else round(c * self.s.pixels_ratio(), 3)})
        return {"session": self.s.id, "rev": self.rev, "fade_s": self.fade_s, "open": self.open,
                "master": self._master_out(), "stack": stack, "auto": self.s.fx_auto_status(),
                "errors": list(self.errors)}

    def summary(self) -> dict:
        return {"rev": self.rev, "open": self.open, "stack": [st.entry() for st in self.stack],
                "master": self.master}

    def bindings(self) -> list[dict]:
        out = []
        for st in self.stack:
            mod = getattr(st.engine_slot, "modulation", None)
            last = getattr(mod, "last", {}) if mod is not None else {}
            for param, b in st.spec.bindings.items():
                out.append({"slot": st.id, "param": param, **b,
                            "value": None if param not in last else round(float(last[param]), 5),
                            **(mod.section_row(param) if hasattr(mod, "section_row") else {})})
        return out
