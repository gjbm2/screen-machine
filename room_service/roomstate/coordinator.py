"""Room-state coordinator (ROOM_STATE_MODEL §4): request a mode, run its transition, keep the room in it.

A request cancels whatever ran before it and re-plans from fresh reads; an unfinished chain (`mode.via`) counts as being
in every mode it was heading to, so the safe rule is chosen. Stores only fill an empty slot (or reopen a closed one
nobody has moved since), a retrieve clears its slot once the value is back, and a level belief only ever takes a reading
or a proven arrival, never a commanded value. So interruptions and restarts always head back to the original values.
Every side effect is on disk before it is sent: in-flight marks, `entered`/`leaving`, and rule-signal keys (`fired`).

plan() runs the same planner without changing anything, so a preview shows the rows a real run would write (§7.2):
  {"id": "i4", "subsystem": "blinds.ch02", "action": "store pre_live, set 100", "status": "pending", "detail": "",
   "step": 2, "kind": "set", "value": 100, "stored": 0, "guarded": true}
Times on disk are epoch seconds from `now`; status() gives ages (LIVE_V1 §0).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable

from .contract import Adapter, Outcome
from .model import LEAVE, RESTORE_SLOT, Entry, Mode, RoomConfig, Step, explain, mode_signals, sets_value, value_matches
from .schema import entry_schema, mode_schema, room_schema
from .state import StateFile


def _subsystem_label(a: Adapter) -> str:
    """Page words for a subsystem: the adapter's own, else its name made readable ("sensor_mask" -> "Sensor mask")."""
    return a.subsystem_label or a.subsystem.replace("_", " ").capitalize()


def _vocabulary(a: Adapter) -> dict:
    """An adapter's editor word lists (contract.Adapter.vocabulary); none when it has none or it fails."""
    try:
        v = a.vocabulary()
    except Exception:
        return {}
    return v if isinstance(v, dict) else {}


mode_log = logging.getLogger("room_service.mode")   # one line per finished switch (mo-11)

RESERVED_SOURCES = {"boot", "resume", "rollback"}  # set only by room_service itself; the API answers 400 bad_source
BACKOFF = (30, 60, 120, 300, 600)                 # retry delays (s); after the last one the retry gives up (§4.1)
POLICY_RANK = {"ignore": 0, "respect": 1, "enforce": 2}
LOG_KEEP = 200
EDGE_TICK_S = 10.0                                # reconcile period for a subsystem with no poll_s (edges: retries only)


class Refused(RuntimeError):
    """A change the coordinator won't make now. `code` is for the API: bad_source | transition_running | device_busy |
    unknown_stored | unavailable | readable."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


HAND_WAIT_S = 15.0        # a hand set during a switch waits at most this long for the switch to let go of its device


class Busy(Refused):
    """Refused because a transition or another device change is running."""


def same(x: Any, y: Any, a: Adapter | None = None) -> bool:
    """Numbers within the adapter's tolerance, anything else with == or the adapter's equivalent() (Sonos paused and
    stopped). Both sides are canonical (§5.2 rule 1)."""
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (x, y)):
        return abs(x - y) <= (a.tolerance if a is not None else 0)
    return x == y or (a is not None and a.equivalent(x, y))


def err(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _unique(values: Iterable) -> list:
    out: list = []
    for v in values:
        if v is not None and v not in out:
            out.append(v)
    return out


def _expired(have: dict, e: Entry, now: float) -> bool:
    return e.max_age_s is not None and have.get("at") is not None and now - have["at"] > e.max_age_s


def _eager(coro) -> asyncio.Task:
    """A task that runs now, up to its first wait (so a prove_behind adapter's commands are out when this returns)."""
    return asyncio.Task(coro, loop=asyncio.get_running_loop(), eager_start=True)


async def _cancel(tasks: list[asyncio.Task]) -> None:
    live = [t for t in tasks if not t.done()]
    for t in live:
        t.cancel()
    if live:
        await asyncio.wait(live)


@dataclass
class Plan:
    """What a transition will do (§4.3 steps 3-4), shared by a real run and plan()."""
    desired: dict[str, Any]
    slots: dict[str, str]                    # target -> slot, for values that come from a retrieve
    pending: dict[str, Any]                  # to send
    unsent: dict[str, tuple[str, str]]       # target -> (row status, detail): skipped, or an unknown toggle
    exits: list[str]
    enters: list[str]
    steps: list[Step]
    rows: list[dict]
    ref: dict                                # ("exit" | "enter", mode, j) or ("step", k) -> that signal's or delay's row


class Coordinator:
    def __init__(self, config: RoomConfig, adapters: Iterable[Adapter], state_path, now: Callable[[], float] = time.time,
                 on_event: Callable[[dict], None] | None = None, margin_s: float = 15.0, cleanup_s: float = 3.0,
                 signal_timeout_s: float = 5.0) -> None:
        """`adapters`: the working ones; placeholders come from the config. margin_s: how long past change_time_s an
        apply, or past a mark's `until` a proof of arrival, may take; cleanup_s: an adapter's bound after a cancel."""
        self.cfg = config
        self.adapters: dict[str, Adapter] = {**config.adapters, **{a.subsystem: a for a in adapters}}
        self.now = now
        self.on_event = on_event or (lambda e: None)
        self.margin_s, self.cleanup_s, self.signal_timeout_s = margin_s, cleanup_s, signal_timeout_s
        self.config_error: str | None = None      # set by room_service when a modes.json edit fails to load (§2.1)
        self.file = StateFile(state_path, now)
        self.s = self.file.load()
        if self.file.error:
            self._log("state_file", "load", error=self.file.error)
        self._req = asyncio.Lock()                # request_mode, hand_set and owner corrections, one at a time
        self._work = asyncio.Lock()               # one adapter-driving activity at a time
        self._task: asyncio.Task | None = None    # the transition (and its rollback)
        self._rtasks: set[asyncio.Task] = set()   # reconcile applies and hand sets; a request cancels them all
        self._hand_wins: dict[str, Any] = {}       # hand sets made while a switch runs: the switch leaves them alone
        self._loops: list[asyncio.Task] = []
        self._gen = 0                             # bumped by requests and hand sets: a tick that read under an older one drops it
        self._moving: set[str] = set()            # targets whose last reading said moving
        self._healthy: dict[str, dict] = {}       # subsystem -> {available, last_ok_at, last_error}
        self._reload_next: tuple[RoomConfig, str] | None = None   # a reload that arrived during a transition

    # small helpers -------------------------------------------------------------------------------------------
    def _adapter(self, t: str) -> Adapter | None:
        sub = t.split(".", 1)[0]
        return self.adapters.get(sub) or self.cfg.adapters.get(sub)

    def _target(self, t: str) -> tuple[Adapter, str]:
        a, member = self._adapter(t), t.partition(".")[2]
        if a is None or member not in a.members:
            raise KeyError(f"no target {t!r}")
        return a, member

    def _readable(self, t: str) -> bool:
        a = self._adapter(t)
        return a is not None and a.kind == "level" and not a.placeholder

    def _level(self, targets: Iterable[str]) -> set[str]:
        return {t for t in targets if self._readable(t)}

    def _inflight(self) -> set[str]:
        return {t for t, b in self.s["believed"].items() if b.get("inflight")}

    def _belief(self, t: str) -> dict:
        return self.s["believed"].setdefault(t, {"value": None, "confidence": "unknown", "at": None})

    def _believe(self, t: str, value: Any, confidence: str) -> None:
        self._belief(t).update(value=value, confidence=confidence, at=self.now())   # keeps inflight and sent_at

    def _save(self) -> None:
        self.file.save(self.s)

    def _fingerprint(self) -> str:
        """What a reconcile tick can change, without reading times: a tick that only re-read unchanged values doesn't
        write (§3.4)."""
        quiet = {t: {k: v for k, v in b.items() if k != "at"} for t, b in self.s["believed"].items()}
        return json.dumps([quiet, self.s["desired"], self.s["drift"], self.s["stored"], len(self.s["log"])], default=str)

    def _emit(self, phase: str, tr: dict, **data) -> None:
        """mode.transition events (§7.4): phase is started | step | done | cancelled."""
        head = {"id": tr["id"], "phase": phase}
        if phase == "started":
            head.update({k: tr.get(k) for k in ("from", "via", "to", "source", "resumes", "reapply", "reason", "skip")})
            if "rollback_of" in tr:
                head["rollback_of"] = tr["rollback_of"]
        self.on_event({"type": "mode.transition", "data": {**head, **data}})

    def _log(self, op: str, cause: str, **data) -> None:
        entry = {"at": self.now(), "op": op, "cause": cause, **data}
        self.s["log"] = (self.s["log"] + [entry])[-LOG_KEEP:]
        self.on_event({"type": "log", "data": {"level": "info", "msg": f"{op}: {json.dumps(data, default=str)} ({cause})"}})

    def _busy(self) -> bool:
        return (self._req.locked() or (self._task is not None and not self._task.done())
                or any(not t.done() for t in self._rtasks))

    def _check_free(self) -> None:
        """Owner corrections wait for nothing: refused while a request, a transition or a device change runs."""
        if self._req.locked() or (self._task and not self._task.done()):
            raise Busy("transition_running", "wait for the switch to finish")
        if self._work.locked() or any(not t.done() for t in self._rtasks):
            raise Busy("device_busy", "another change is still running")

    async def _stop_tasks(self, rtasks_only: bool = False) -> None:
        """Cancel the transition (unless rtasks_only) and every reconcile apply and hand set; each gets cleanup_s."""
        tasks = [t for t in ([] if rtasks_only else [self._task]) + list(self._rtasks) if t and not t.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            _, late = await asyncio.wait(tasks, timeout=self.cleanup_s)
            if late:                              # whoever needs _work next waits for it to let go
                self._log("contract_breach", "cancel", detail=f"{len(late)} task(s) still running after {self.cleanup_s:g} s")

    async def wait_idle(self) -> None:
        """Until the transition (with its rollback) and every apply in flight have finished."""
        while tasks := [t for t in [self._task, *self._rtasks] if t and not t.done()]:
            await asyncio.wait(tasks)

    # requesting a mode (§4.2) ----------------------------------------------------------------------------------
    async def request_mode(self, name: str, source: str = "web", reason: str | None = None,
                           expect: dict | None = None, skip: list[str | dict] | None = None) -> str:
        """PUT /mode: start the transition to `name` and return its id. The last request wins: it cancels whatever ran
        before it. ConfigError for an unknown or abstract mode (404 unknown_mode); Refused bad_source for a reserved source.
        Auto (show_brain, source "autopilot") adds: `reason` (kept on the transition and its started event); `expect`
        {current?, last_transition_id?}: Refused "stale" (409) if the room has moved on; `skip`: what this
        transition leaves alone (not stored, not set): selectors, or {selector: {"from": pattern or [patterns]}} for
        the targets whose believed value matches now (music that's playing); ValueError for a selector that names
        nothing (400)."""
        if source in RESERVED_SOURCES:
            raise Refused("bad_source", f"{source!r} is set only by room_service")
        targets: list[str] = []
        for item in skip or []:
            conds = item.items() if isinstance(item, dict) else [(item, None)]
            for sel, cond in conds:
                try:
                    ts = self.cfg._expand(sel, "")
                except Exception as e:            # ConfigError: matches no target
                    raise ValueError(f"skip: {e}") from None
                if cond is not None:
                    await self._refresh(ts)       # what it is now, not at the last poll (reads only)
                    pats = cond.get("from") if isinstance(cond, dict) else None
                    pats = pats if isinstance(pats, list) else [pats]
                    ts = [t for t in ts if (b := self.s["believed"].get(t)) and b.get("confidence") != "unknown"
                          and any(value_matches(p, b["value"]) for p in pats)]
                targets += ts
        return await self._request(name, source, reason=reason, expect=expect, skip=sorted(set(targets)))

    async def _request(self, name: str, source: str, reason: str | None = None, expect: dict | None = None,
                       skip: list[str] | None = None) -> str:
        self.cfg.mode(name)
        self._gen += 1                            # a reconcile tick reading right now drops its result (R5)
        async with self._req:                     # F2: one request at a time
            if expect:
                last = (self.s["transition"] or self.s["last_transition"] or {}).get("id")
                if ("current" in expect and expect["current"] != self.s["mode"]["current"]) or (
                        "last_transition_id" in expect and expect["last_transition_id"] != last):
                    raise Refused("stale", f"the room moved on: now {self.s['mode']['current']!r}, last transition {last}")
            await self._stop_tasks()
            self._hand_wins = {}                  # a new switch: hands set during the last one don't carry over
            prev = self.s["transition"]
            if prev and prev["status"] == "running":            # F19: cancelled before its task ran, or a crash
                prev["status"] = prev["phase"] = "cancelled"
                self._mark_rows(prev, ("pending", "running"), "cancelled")
                self._emit("cancelled", prev)
            m = self.s["mode"]
            reapply = m["target"] is None and name == m["current"]
            if m["target"] and m["target"] not in m["via"]:
                m["via"].append(m["target"])                    # F4: the room may be partly there
            carry = bool(prev) and prev["to"] == name           # same destination: carry on (new id, fired kept)
            resuming = carry and source == "resume"
            tr = {"id": f"t-{uuid.uuid4().hex[:8]}", "resumes": prev["id"] if carry else None,
                  "from": m["current"], "via": list(m["via"]), "to": name,
                  "source": prev["source"] if resuming else source,
                  "status": "running", "phase": "running", "reapply": reapply,
                  "started_at": self.now(), "detail": "", "fired": list(prev["fired"]) if carry else [], "steps": []}
            if reason:
                tr["reason"] = str(reason)
            if skip or (resuming and prev.get("skip")):
                tr["skip"] = list(skip or prev.get("skip", []))
            if prev:
                self.s["last_transition"] = prev
            m["target"] = name
            self.s["transition"] = tr
            self._save()
            self._task = asyncio.create_task(self._run(tr, self.cfg))   # the task keeps this config, rollback included
            return tr["id"]

    # running a transition (§4.3) -------------------------------------------------------------------------------
    async def _run(self, tr: dict | None, cfg: RoomConfig) -> None:
        try:
            while tr is not None:                 # a transition, then possibly its rollback, in one task and one config
                tr = await self._run_one(tr, cfg)
        finally:
            if self._reload_next:                 # a reload that arrived meanwhile applies now (§2.1)
                (config, cause), self._reload_next = self._reload_next, None
                self._reload_now(config, cause)

    async def _run_one(self, tr: dict, cfg: RoomConfig) -> dict | None:
        behind: list[asyncio.Task] = []           # prove_behind sends, proved while the later steps run
        try:
            full = to = self._with_restores(cfg.mode(tr["to"]))   # inside the try: a vanished mode fails cleanly
            if tr.get("skip"):                    # Auto: these targets are left alone this time (not stored, not set)
                to = replace(to, state={t: e for t, e in to.state.items() if t not in set(tr["skip"])})
            async with self._work:
                # 1. Read every readable target the destination names, plus anything still in flight, plus what its
                #    rows' "Leave it as it is while" (skip_if) look at.
                await self._refresh(self._level(to.state) | self._inflight() | self._level(self._skip_watch(to)))
                to, left = self._leave_alone(to, tr)     # rows whose skip_if holds now: not stored, not set
                # 2. Store, into slots that are empty, or closed and still valid (§4.6). Saved, with the plan, before
                #    any command.
                fresh = await self._store(to, tr)
                # 3-4. What the destination wants, what must be sent, the rule, and every row as 'pending'.
                p = self._plan(to, tr, cfg, left=left)
                self.s["desired"] = {t: {"value": v, "policy": to.state[t].policy, "slot": p.slots.get(t),
                                         "settled": False, "retry": None, "reasserted": False}
                                     for t, v in p.desired.items()}
                self.s["drift"], self.s["config_pending"] = {}, []
                for t, hv in self._hand_wins.items():
                    self._hand_marks(t, hv, "hand")
                for t in p.desired:
                    if t not in p.pending and t not in p.unsent:
                        self._settle(t)           # already there, or the user's (ignore): a retrieve's slot is cleared
                tr["steps"] = p.rows
                self._save()
                self._emit("started", tr, plan=p.rows)
                # 5. Exit signals of the modes the room is leaving, innermost first. `leaving` records each one, and
                #    the mode stays in `entered` until this transition completes (R18).
                for name in p.exits:
                    if name in self.s["mode"]["leaving"]:
                        continue
                    self.s["mode"]["leaving"].append(name)          # on disk before a signal goes: _fire saves first
                    for j, sig in enumerate(mode_signals(cfg, name, "on_exit")):
                        await self._fire(tr, sig, p.ref[("exit", name, j)])
                # 6. The steps, in order.
                worked = False
                for k, step in enumerate(p.steps):
                    row = p.ref.get(("step", k))
                    if step.kind == "delay":
                        if worked:
                            self._row(tr, row, "running", save=False)   # a wait has no side effect to record
                            await asyncio.sleep(step.seconds)
                            self._row(tr, row, "ok")
                        else:
                            self._row(tr, row, "skipped", "nothing changed before it", "quiet_before")
                        continue
                    if step.kind == "signal":
                        if tr["reapply"]:         # a re-apply is state only (R10)
                            self._row(tr, row, "skipped", "re-apply")
                        else:
                            n = 1 + sum(s.kind == "signal" and s.action == step.action for s in p.steps[:k])
                            await self._fire(tr, step, row, key=f"step:{step.action}:{n}")
                            worked = True
                        continue
                    names = step.targets if step.kind == "targets" else self._rest(p.steps, p.pending, p.unsent)
                    batch = {t: p.pending.pop(t) for t in names if t in p.pending}
                    for t in [t for t in batch if t in self._hand_wins]:     # set by hand during the switch: it stays
                        batch.pop(t)
                        self._row(tr, t, "skipped", "set by hand during the switch", "hand")
                    watch = [t for t in batch if t in to.state and to.state[t].skip_targets]
                    if watch:                     # skip_if again, on fresh readings, now that this step would send
                        await self._refresh(self._level({c for t in watch for c, _ in to.state[t].skip_targets}))
                        for t in watch:
                            if why := self._left_alone(to.state[t]):
                                batch.pop(t)
                                self._left_at_step(tr, t, to.state[t], why, fresh)
                    worked = bool(batch)
                    later = {t: batch.pop(t) for t in list(batch) if self._proved_behind(t, step)}
                    if later:                     # sent now (in order on its channel); the next step doesn't wait
                        behind.append(_eager(self._apply(tr, later, p.slots, mode=to, eager=True)))
                    out = await self._apply(tr, batch, p.slots, mode=to) if batch else {}
                    self._hand_rows(tr, out)      # set by hand while the batch ran: the person's value stands
                    guards = [t for t in names if t in step.abort_on and t in p.desired]
                    bad = [t for t in guards if not self._held_already(t, p.desired[t], tr, cfg)    # R7
                           and ((t in out and out[t].status != "ok")
                                or (t in p.unsent and (p.unsent[t][0] != "skipped" or not step.allow_skipped)))]
                    if bad:                       # F14: only a guarded target can abort
                        await asyncio.gather(*behind)
                        tr["status"] = "failed"
                        tr["detail"] = f"stopped at step {k + 1}: {', '.join(bad)} not ok"
                        self._mark_rows(tr, ("pending",), "cancelled", "aborted: an earlier step failed", "aborted")
                        self._finish(tr, completed=False)
                        return self._rollback(tr, step, cfg)       # None when the step says rollback: false
                    weak = [t for t in guards if p.unsent.get(t, ("",))[0] == "skipped"]
                    if weak:                      # R8: allow_skipped
                        tr["detail"] = "; ".join(filter(None, [tr["detail"], f"guard not enforced: {', '.join(weak)} skipped"]))
                await asyncio.gather(*behind)     # every row has its outcome before the enter signals
                self._hand_rows(tr, p.desired)
                # 7. Enter signals of the modes the room is entering, outermost first.
                for name in p.enters:
                    self.s["mode"]["entered"].append(name)          # on disk before a signal goes: _fire saves first
                    for j, sig in enumerate(mode_signals(cfg, name, "on_enter")):
                        await self._fire(tr, sig, p.ref[("enter", name, j)])
                # 8. Done.
                m = self.s["mode"]
                m["entered"] = [x for x in m["entered"] if x in to.family]     # the modes left go only now
                m["leaving"] = []
                tr["status"] = "partial" if self._any_not_ok(tr) else "ok"
                m.update(current=to.name, target=None, via=[], since=self.now())
                self._close_slots(full, cfg)      # the whole mode: a skipped target's stored value stays
                self._finish(tr, completed=True)
                return None
        except asyncio.CancelledError:
            await _cancel(behind)
            tr["status"] = tr["phase"] = "cancelled"    # F3: target and via are kept; the next request carries on
            self._mark_rows(tr, ("pending", "running"), "cancelled")
            self._save()
            self._emit("cancelled", tr)
            raise
        except Exception as e:                    # F10: never leave a 'running' record behind
            await _cancel(behind)
            tr["status"], tr["detail"] = "failed", err(e)
            self._mark_rows(tr, ("pending", "running"), "cancelled", "error")
            self._log("error", tr["id"], error=f"{type(e).__name__}: {e}")
            self._finish(tr, completed=False)
            return None

    def _plan(self, to: Mode, tr: dict, cfg: RoomConfig, mutate: bool = True,
              left: dict[str, tuple[Entry, str]] | None = None) -> Plan:
        """`left`: rows _leave_alone took out of `to` (target -> (entry, why)); each gets a skipped row in its step."""
        desired, slots = self._desired(to, mutate)
        pending: dict[str, Any] = {}
        unsent: dict[str, tuple[str, str]] = {}
        for t, v in desired.items():
            why = self._send_reason(t, v, to, tr)
            if why == "send":
                pending[t] = v
            elif why is not None:
                unsent[t] = why
        for t, e in to.state.items():             # retrieves that will leave their target alone say why
            if e.retrieve and t not in desired:
                have = self.s["stored"].get(e.retrieve, {}).get(t)
                expired = have is not None and _expired(have, e, self.now())
                self._note(tr, t, ("stored value too old" if expired else "nothing stored") + ": leave")
        exits, enters = self._edges(to)
        changes = {t: ((self.s["believed"].get(t) or {}).get("value"), desired[t]) for t in [*pending, *unsent]}
        tr["rule"] = cfg.transition_for({tr["from"], *tr["via"]}, to.name, changes)   # `when` sees what changes
        steps = cfg.plan_steps({tr["from"], *tr["via"]}, to.name, changes)
        rows, ref = self._rows(exits, steps, enters, desired, pending, unsent, slots, to, cfg, left or {})
        return Plan(desired, slots, pending, unsent, exits, enters, steps, rows, ref)

    def _hand_rows(self, tr: dict, targets) -> None:
        """S12: a target set by hand while the switch was sending it: its row says so (skipped), not the switch's
        outcome, and it never makes the switch partial."""
        hit = [t for t in targets if t in self._hand_wins]
        for t in hit:
            self._row(tr, t, "skipped", "set by hand during the switch", "hand", save=False)
        if hit:
            self._save()

    def _proved_behind(self, t: str, step: Step) -> bool:
        """An unguarded target of a prove_behind adapter (the page policy): the step sends it, the next step goes on."""
        a = self._adapter(t)
        return a is not None and a.kind == "level" and a.prove_behind and t not in step.abort_on

    def _rest(self, steps: list[Step], pending: dict, unsent: dict) -> list[str]:
        """The pending and unsent targets that no targets step of the rule names."""
        named = {t for s in steps if s.kind == "targets" for t in s.targets}
        return [t for t in [*pending, *unsent] if t not in named]

    def _finish(self, tr: dict, completed: bool) -> None:
        tr["finished_at"] = self.now()
        if tr["phase"] != "cancelled":
            tr["phase"] = "done"
        self.s["last_transition"], self.s["transition"] = tr, None
        if completed:
            self.s["mode"]["target"] = None
        self._save()
        self._emit("done", tr, status=tr["status"], detail=tr["detail"])
        bad = [f"{r['subsystem']} {r['status']}" + (f" ({r['detail']})" if r.get("detail") else "")
               for r in tr["steps"] if r["status"] != "ok" and r["kind"] != "delay"]
        mode_log.info("switch %s %s→%s %s (source %s): %s", tr["id"], tr["from"], tr["to"], tr["status"],
                      tr.get("source"), "; ".join(bad) or "all ok")

    def _note(self, tr: dict, t: str, text: str) -> None:
        tr.setdefault("notes", {})[t] = text

    # rows (§7.2) -------------------------------------------------------------------------------------------------
    def _rows(self, exits, steps, enters, desired, pending, unsent, slots, to, cfg,
              left: dict | None = None) -> tuple[list[dict], dict]:
        """Every row in run order, as 'pending' (an unsent target already has its final status). `step` is 0 for exit
        signals, 1..N for the rule's steps, N+1 for enter signals; `id` is i<k> in row order (§4.12). A row left alone
        by its skip_if (`left`) is skipped already, in the step that would have sent it, saying why."""
        left = left or {}
        rows: list[dict] = []
        ref: dict = {}

        def add(key, subsystem, action, n, kind, status="pending", detail="", **extra):
            row = {"id": f"i{len(rows)}", "subsystem": subsystem, "action": action, "status": status, "detail": detail,
                   "step": n, "kind": kind, **extra}
            rows.append(row)
            ref[key] = row

        def signal(key, sig: Step, n: int):
            add(key, sig.subsystem + (f".{sig.member}" if sig.member else ""), f"signal {sig.action}", n, "signal")

        for name in exits:
            if name not in self.s["mode"]["leaving"]:     # an exit already fired by an earlier, cancelled transition
                for j, sig in enumerate(mode_signals(cfg, name, "on_exit")):
                    signal(("exit", name, j), sig, 0)
        done: set[str] = set()
        for k, step in enumerate(steps):
            if step.kind == "delay":
                add(("step", k), "delay", f"wait {step.seconds:g} s", k + 1, "delay")
            elif step.kind == "signal":
                signal(("step", k), step, k + 1)
            else:
                names = step.targets if step.kind == "targets" else self._rest(steps, {**pending, **left}, unsent)
                for t in names:
                    if t in left and t not in done:
                        done.add(t)
                        e, why = left[t]
                        v = e.set if e.sets else None
                        text = self._adapter(t).show(t.split(".", 1)[1], v)
                        add(t, t, f"left alone, not set {text}" if e.sets else "left alone, not put back", k + 1,
                            "retrieve" if e.retrieve else "set", "skipped", why, value=v, reason="left_alone",
                            **({"guarded": True} if t in step.abort_on else {}))
                        continue
                    if t in done or (t not in pending and t not in unsent):
                        continue
                    done.add(t)
                    e, v, a = to.state[t], desired[t], self._adapter(t)
                    text = a.show(t.split(".", 1)[1], v)
                    if t in slots:
                        action, kind = (f"put back → {text}" if slots[t] == RESTORE_SLOT
                                        else f"retrieve {slots[t]} → {text}"), "retrieve"
                    elif e.retrieve:
                        action, kind = f"retrieve {e.retrieve} (empty) → else {text}", "retrieve"
                    else:
                        action, kind = (f"set {text}, put back afterwards" if e.restore
                                        else f"store {e.store}, set {text}" if e.store else f"set {text}"), "set"
                    status, detail = unsent.get(t, ("pending", ""))
                    extra: dict[str, Any] = {"value": v}
                    if e.store:                   # the value stored before it (a plan shows what would be stored)
                        src = self.s["stored"].get(e.store, {}).get(t) or self.s["believed"].get(t)
                        if src and src.get("confidence") != "unknown":
                            extra["stored"] = src["value"]
                    if t in step.abort_on:
                        extra["guarded"] = True
                    reason = {"no adapter": "unavailable", "toggle state unknown; not sent": "unknown_state"}.get(detail)
                    if reason:
                        extra["reason"] = reason
                    add(t, t, action, k + 1, kind, status, detail, **extra)
        for name in enters:
            for j, sig in enumerate(mode_signals(cfg, name, "on_enter")):
                signal(("enter", name, j), sig, len(steps) + 1)
        return rows, ref

    def _row(self, tr: dict | None, row: dict | str | None, status: str, detail: str = "", reason: str | None = None,
             save: bool = True) -> None:
        """Patch one row (the row itself, or a target's) and save: the record is saved at every row change (§3.4).
        save=False: the caller saves once for several rows, before its next await."""
        if tr is None:
            return
        if isinstance(row, str):
            row = next((r for r in tr["steps"] if r["subsystem"] == row and r["kind"] in ("set", "retrieve")), None)
        if row is None:
            return
        row.update(status=status, detail=detail)
        if reason:
            row["reason"] = reason
        if save:
            self._save()
        self._emit("step", tr, step=row)

    @staticmethod
    def _mark_rows(tr: dict, from_: tuple[str, ...], to: str, detail: str | None = None, reason: str | None = None) -> None:
        for r in tr["steps"]:
            if r["status"] in from_:
                r["status"] = to
                if detail is not None:
                    r["detail"] = detail
                if reason:
                    r["reason"] = reason

    @staticmethod
    def _any_not_ok(tr: dict) -> bool:
        """failed or unconfirmed rows, or a skipped guarded one (allow_skipped); other skipped rows never count, nor
        one the person set by hand during the switch, nor one its skip_if left alone (the config asked for that)."""
        return any(r["status"] in ("failed", "unconfirmed")
                   or (r["status"] == "skipped" and r.get("guarded") and r.get("reason") not in ("hand", "left_alone"))
                   for r in tr["steps"])

    # store, desired values, send decisions (§4.4) --------------------------------------------------------------
    def _with_restores(self, to: Mode) -> Mode:
        """The destination plus "put back afterwards": every value a mode stored with restore and the destination
        doesn't set itself becomes an implicit retrieve (policy ignore: after it's back, it's the user's)."""
        extra = {}
        for t, have in self.s["stored"].get(RESTORE_SLOT, {}).items():
            e = to.state.get(t)
            if self._adapter(t) is None:
                continue
            if e is None or (e.retrieve and e.retrieve != RESTORE_SLOT and not self._usable(e, t)):
                # unmanaged, or an old-style retrieve with nothing usable stored: that doesn't set the target, so the
                # put-back still happens (one mechanism must never silently drop the other's value)
                extra[t] = Entry(retrieve=RESTORE_SLOT, policy="ignore", max_age_s=have.get("max_age_s"))
        return replace(to, state={**to.state, **extra}) if extra else to

    def _usable(self, e: Entry, t: str) -> bool:
        """A retrieve entry finds a value it would put back (stored and not too old)."""
        have = self.s["stored"].get(e.retrieve, {}).get(t)
        return bool(have) and not _expired(have, e, self.now())

    # "leave it as it is while" (skip_if) ------------------------------------------------------------------------
    @staticmethod
    def _skip_watch(mode: Mode) -> set[str]:
        """Every target a row of `mode` looks at through its skip_if."""
        return {c for e in mode.state.values() for c, _ in e.skip_targets}

    def _left_alone(self, e: Entry | None) -> tuple[str, Any] | None:
        """The first of a row's skip_if conditions that holds on what the room believes now: (target, its value). A
        value that is unknown, or stale (unreadable), never holds: the row applies as usual."""
        for c, pats in (e.skip_targets if e is not None else ()):
            b = self.s["believed"].get(c)
            if b and b.get("confidence") in ("confirmed", "unconfirmed") and any(value_matches(p, b.get("value"))
                                                                               for p in pats):
                return c, b.get("value")
        return None

    def _alone_words(self, c: str, v: Any) -> str:
        """Page words for why a row was left alone: "left alone: Drawing Room music is Play Chopin"."""
        try:
            a, m = self._adapter(c), c.split(".", 1)[1]
            what, value = self.cfg.labels.get(c) or a.label(m), a.words(m, v)
        except Exception:                         # a target the adapter can't word: plain text
            what, value = c, json.dumps(v, default=str)
        return f"left alone: {what} is {value}"

    def _leave_alone(self, to: Mode, tr: dict) -> tuple[Mode, dict[str, tuple[Entry, str]]]:
        """skip_if at entry, on the readings just taken: rows whose condition holds leave this entry, not stored and not
        set (as Auto's skip: what earlier modes stored for them stays). Returns the mode without them and
        target -> (entry, why) for their skipped rows; each is noted on the transition."""
        left = {t: (e, self._alone_words(*why)) for t, e in to.state.items() if (why := self._left_alone(e))}
        for t, (_, words) in left.items():
            self._note(tr, t, words)
        if not left:
            return to, left
        tr["left_alone"] = sorted(left)
        return replace(to, state={t: e for t, e in to.state.items() if t not in left}), left

    def _left_at_step(self, tr: dict, t: str, e: Entry, why: tuple[str, Any], fresh: set[str]) -> None:
        """skip_if at the step that would send t: the row is skipped, what this entry stored for it is taken back, and
        the room doesn't hold it (nothing to retry, re-assert or report as drift)."""
        words = self._alone_words(*why)
        if e.store and t in fresh:
            self._unstore(e.store, t)
        self.s["desired"].pop(t, None)
        self.s["drift"].pop(t, None)
        tr["left_alone"] = sorted({*tr.get("left_alone", []), t})
        self._note(tr, t, words)
        self._row(tr, t, "skipped", words, "left_alone")

    async def _store(self, to: Mode, tr: dict) -> set[str]:
        """Returns the targets it stored a new value for (a row a later skip_if leaves alone takes its own back)."""
        fresh: set[str] = set()
        stores = [t for t, e in to.state.items() if e.store and not
                  ((have := self.s["stored"].get(e.store, {}).get(t)) and not have.get("closed"))]
        await self._refresh(stores)               # what it is now, never an old belief: a failed read makes it stale,
        for t, e in to.state.items():             # and a stale store is never put back
            if not e.store:
                continue
            slot = self.s["stored"].setdefault(e.store, {})
            have = slot.get(t)
            if have and not have.get("closed"):
                continue                          # keep the original (live -> live, interrupted, re-entry)
            b = await self._settled_belief(t)     # waits <= change_time_s while moving or in flight
            if have and not self._moved_since(t, have):
                have["closed"] = False            # closed, and nobody has moved it since: the episode reopens (R6)
                continue
            if b is None or b["confidence"] == "unknown":
                slot.pop(t, None)                 # nothing usable: the retrieve will use `else`
                self._note(tr, t, "nothing stored: value unknown")
                continue
            if e.store_if and not any(value_matches(p, b["value"]) for p in e.store_if):
                slot.pop(t, None)                 # not worth putting back (e.g. Sonos wasn't playing): retrieve leaves it
                self._note(tr, t, f"nothing stored: {self._adapter(t).show(t.split('.', 1)[1], b['value'])} isn't in store_if")
                continue
            slot[t] = {"value": b["value"], "at": b["at"], "confidence": b["confidence"],    # F7/G9: the belief's own
                       "from_mode": tr["from"], "stored_at": self.now(), "closed": False}     # time and confidence
            fresh.add(t)
            limit = e.max_age_s or (self.cfg.put_back_max_age_s if e.restore else None)
            if limit:
                slot[t]["max_age_s"] = limit         # the put-back, by whichever mode comes next, honours it
        self.s["stored"] = {s: e for s, e in self.s["stored"].items() if e}
        return fresh
        # saved with the plan, which comes next and sends nothing (§3.4 rule 1: slots on disk before the first command)

    async def _settled_belief(self, t: str) -> dict | None:
        """t's belief once it is neither moving nor in flight, re-reading every in-flight poll for at most change_time_s."""
        a = self._adapter(t)
        if self._readable(t):
            loop = asyncio.get_running_loop()
            deadline = loop.time() + a.change_time_s
            while (t in self._moving or self._belief(t).get("inflight")) and loop.time() < deadline:
                await asyncio.sleep(a.poll_inflight_s)
                await self._refresh([t])
        return self.s["believed"].get(t)

    def _desired(self, to: Mode, mutate: bool = True) -> tuple[dict[str, Any], dict[str, str]]:
        """(target -> value, target -> slot for retrieved values). plan() and reload() use mutate=False."""
        desired, slots = {}, {}
        for t, e in to.state.items():
            if e.retrieve:
                entries = self.s["stored"].get(e.retrieve, {})
                have = entries.get(t)
                if have and _expired(have, e, self.now()):
                    have = None                   # too old: `else`; dropped at completion
                elif have and have.get("closed") and not have.get("final") and self._moved_since(t, have):
                    if mutate:
                        entries.pop(t)            # moved by hand since the failed retrieve: accept it (R6)
                    have = None
                if have:
                    desired[t], slots[t] = have["value"], e.retrieve
                elif e.else_ != LEAVE:
                    desired[t] = e.else_
            elif e.sets:
                desired[t] = e.set
        return desired, slots

    def _send_reason(self, t: str, v: Any, to: Mode, tr: dict) -> str | tuple[str, str] | None:
        """"send"; None (already there, or the user's); or (row status, detail) for a target that can't be sent."""
        a, b, e = self._adapter(t), self.s["believed"].get(t), to.state[t]
        if a is None or a.placeholder:
            return ("skipped", "no adapter")
        m = self.s["mode"]
        if (e.policy == "ignore" and tr["to"] == m["current"] and not self._closed(t, e)
                and (b is None or (b.get("sent_at") or 0) <= (m["since"] or 0))):
            return None                           # R13/R18: a re-apply or a return; nobody here touched it since arrival
        if a.kind == "edge_toggle":               # one code flips it: never send blind (S10)
            if not b or b["confidence"] == "unknown":
                return ("unconfirmed", "toggle state unknown; not sent")
            if same(b["value"], v, a):
                return None
            return ("skipped", "dry run") if a.dry_run else "send"
        if b and not b.get("inflight") and t not in self._moving and same(b["value"], v, a):
            if a.kind == "level" and b["confidence"] == "confirmed":
                return None                       # already there
            if a.kind == "edge_discrete" and b["confidence"] == "unconfirmed" and not a.resend:
                return None                       # sent before, trusted as saved
        return ("skipped", "dry run") if a.dry_run else "send"

    def _closed(self, t: str, e: Entry) -> bool:
        return bool(e.retrieve) and bool((self.s["stored"].get(e.retrieve, {}).get(t) or {}).get("closed"))

    def _moved_since(self, t: str, have: dict) -> bool:
        """A confirmed reading outside the entry's `explained` (someone moved it). Stale or unknown: can't tell (False)."""
        b, a = self.s["believed"].get(t), self._adapter(t)
        return (bool(b) and b["confidence"] == "confirmed"
                and not any(same(b["value"], x, a) for x in have.get("explained", [have["value"]])))

    def _held_already(self, t: str, v: Any, tr: dict, cfg: RoomConfig) -> bool:
        """R7: the room already held this guarded value: the last completed mode and every mode in `via` set it."""
        return all(n in cfg.modes and same(sets_value(cfg.modes[n], t), v, self._adapter(t))
                   for n in [tr["from"], *tr["via"]])

    # apply and refresh (§4.4) ----------------------------------------------------------------------------------
    async def _apply(self, tr: dict | None, batch: dict[str, Any], slots: dict[str, str], reasserts=(),
                     mode: Mode | None = None, eager: bool = False) -> dict[str, Outcome]:
        explained = {t: self._explained(t, v) for t, v in batch.items()}    # R2: taken before the send
        by_sub: dict[str, dict[str, Any]] = defaultdict(dict)
        for t, v in batch.items():
            sub, m = t.split(".", 1)
            by_sub[sub][m] = v
        opts = {sub: {k: o for m in vals if mode and f"{sub}.{m}" in mode.state
                      for k, o in mode.state[f"{sub}.{m}"].opts.items()} for sub, vals in by_sub.items()}
        sends = [self._send(sub, vals, tr, opts[sub]) for sub, vals in by_sub.items()]
        outs = await asyncio.gather(*([_eager(c) for c in sends] if eager else sends))
        out = {t: o for d in outs for t, o in d.items()}  # gather re-raises a cancel even if an adapter swallows it
        self._after_send(tr, out, slots, reasserts, explained)
        return out

    async def _send(self, sub: str, vals: dict[str, Any], tr: dict | None = None, opts: dict | None = None) -> dict[str, Outcome]:
        """Mark, send, prove. Updates beliefs only; the caller decides what an outcome means for desired and slots."""
        a = self.adapters[sub]
        pending = {}
        for m, v in vals.items():                 # F1/F9: marked on disk BEFORE sending
            t = f"{sub}.{m}"
            b = self._belief(t)
            b["sent_at"] = self.now()
            if a.kind == "level":
                fl = b.get("inflight")
                if fl is None:
                    b["inflight"] = {"value": v, "from": b["value"], "earlier": [], "earlier_until": 0,
                                     "until": self.now() + a.change_time_s}
                else:                             # R1: re-targeting an unresolved command; never forget it
                    if not same(fl["value"], v, a):
                        fl["earlier"].append(fl["value"])
                        fl["earlier_until"] = max(fl["earlier_until"], fl["until"])
                    fl.update(value=v, until=self.now() + a.change_time_s)
                    pending[m] = {k: fl[k] for k in ("from", "earlier", "earlier_until")}
            else:
                b["confidence"] = "unknown"       # the value is kept; a crash now leaves it unknown
            self._row(tr, t, "running", save=False)
        self._save()
        hints = {**(opts or {}), **({"pending": pending} if pending else {})}    # hints (§5.2 rule 3)
        try:
            out = await asyncio.wait_for(a.apply(vals, hints), a.change_time_s + self.margin_s)
        except asyncio.TimeoutError:              # F11
            out = Outcome("unconfirmed", "timeout")
        except asyncio.CancelledError:
            self._save()                          # the marks stay: the device may still be moving
            raise
        except Exception as e:                    # a dead transport or an adapter bug
            out = Outcome("failed", err(e))
            self._health(sub, ok=False, error=err(e))
        if out.status == "ok":
            self._health(sub, ok=True)
        res = {}
        for m, v in vals.items():
            t, mo = f"{sub}.{m}", out.members.get(m, out)
            if a.kind == "level":
                if mo.status == "ok" and not await self._prove(t):     # R1: the adapter may have read a stale cache
                    mo = Outcome("unconfirmed", "arrival not proven", retry=mo.retry)
                if mo.status == "ok":
                    self._believe(t, v, "confirmed")
                    self._belief(t).pop("inflight", None)
                # unconfirmed or failed (R2): the belief keeps the last reading; the mark stays until proof or `until`
            elif mo.status == "ok":
                self._believe(t, v, "unconfirmed")                     # an edge that was sent
            # an edge that failed or timed out stays `unknown` with its old value (R3)
            res[t] = mo
        return res                                # the caller saves the outcome with desired, slots and rows (§3.4 rule 4)

    def _arrived(self, fl: dict, r, a: Adapter) -> bool:
        """The only arrival test (R1): a stopped reading of the commanded value that only a fresh reading can show."""
        return (not r.moving and r.value is not None and same(r.value, fl["value"], a)
                and (not any(same(fl["value"], x, a) for x in [fl["from"], *fl["earlier"]])
                     or self.now() >= fl["earlier_until"]))

    async def _prove(self, t: str) -> bool:
        """Does a reading prove the commanded value? Re-reads every in-flight poll until _refresh drops the mark, or until
        the mark's until + margin_s passes (False)."""
        b, a = self._belief(t), self._adapter(t)
        fl = b.get("inflight")
        if fl is None:
            return True

        async def poll() -> bool:
            while True:
                await self._refresh([t])
                if not b.get("inflight"):
                    return same(b["value"], fl["value"], a)      # proven, or seen stopped somewhere else after `until`
                await asyncio.sleep(a.poll_inflight_s)
        try:
            return await asyncio.wait_for(poll(), max(a.poll_inflight_s, fl["until"] + self.margin_s - self.now()))
        except asyncio.TimeoutError:
            return False

    def _explained(self, t: str, v: Any) -> list:
        """R2: the readings our own commands account for: the reading before them, every value commanded, and v."""
        b = self.s["believed"].get(t) or {}
        fl = b.get("inflight")
        return _unique([fl["from"], *fl["earlier"], fl["value"], v] if fl else [b.get("value"), v])

    def _after_send(self, tr, out: dict[str, Outcome], slots: dict[str, str], reasserts=(), explained=None) -> None:
        for t, mo in out.items():
            d = self.s["desired"].get(t)
            if d is not None and mo.status == "ok":
                d["settled"], d["retry"] = True, None
                if t in slots:
                    self._unstore(slots[t], t)    # a retrieve that landed clears its slot
            elif d is not None and t in reasserts:    # enforce's one re-assert didn't land: report, never retry
                self.s["drift"].setdefault(t, {"observed": self._belief(t)["value"], "desired": d["value"],
                                               "at": self.now()})["overridden"] = True
            elif d is not None:
                r = d["retry"] or {"attempts": 0, "next_at": self.now() + BACKOFF[0]}
                r.update(explained=(explained or {}).get(t) or self._explained(t, d["value"]),
                         final=not mo.retry, why=mo.detail)
                d["settled"], d["retry"] = False, r
            self._row(tr, t, mo.status, mo.detail, save=False)
        self._save()

    async def _refresh(self, targets: Iterable[str]) -> None:
        """Read level targets, in parallel across subsystems. A failed read makes beliefs stale (value and `at` kept)
        and the subsystem unavailable (F7). A reading that proves arrival, or shows the device stopped somewhere else
        after the mark's `until`, drops the in-flight mark."""
        by_sub: dict[str, list[str]] = defaultdict(list)
        for t in sorted(self._level(targets)):
            sub, m = t.split(".", 1)
            if m in self.adapters[sub].members:
                by_sub[sub].append(m)
        if not by_sub:
            return
        async def read(a: Adapter, members: list[str]) -> dict:
            return await asyncio.wait_for(a.read(members), a.read_timeout_s)
        results = await asyncio.gather(*(read(self.adapters[s], ms) for s, ms in by_sub.items()), return_exceptions=True)
        for (sub, members), res in zip(by_sub.items(), results):
            a = self.adapters[sub]
            if isinstance(res, BaseException):
                for m in members:
                    b = self.s["believed"].get(f"{sub}.{m}")
                    if b and b["confidence"] != "unknown":
                        b["confidence"] = "stale"
                self._health(sub, ok=False, error=f"read: {err(res)}")
                continue
            self._health(sub, ok=True)
            for m, r in res.items():
                t = f"{sub}.{m}"
                if r.value is not None:
                    self._believe(t, r.value, "confirmed")          # keeps the in-flight mark and sent_at
                fl = self._belief(t).get("inflight")
                if fl and (self._arrived(fl, r, a) or (not r.moving and self.now() > fl["until"])):
                    del self._belief(t)["inflight"]
                (self._moving.add if r.moving else self._moving.discard)(t)

    def _health(self, sub: str, ok: bool, error: str | None = None) -> None:
        """Adapter health for status. Unavailable -> available restarts that subsystem's retries that gave up (never
        final ones), due now. Retries still in their backoff keep it, so a flapping device can't make them hammer."""
        h = self._healthy.setdefault(sub, {"available": True, "last_ok_at": None, "last_error": None})
        if ok:
            if not h["available"]:
                self._log("available", "health", subsystem=sub)
                for t, d in self.s["desired"].items():
                    r = d["retry"]
                    if t.split(".", 1)[0] == sub and r and r.get("gave_up") and not r.get("final"):
                        r.update(attempts=0, next_at=self.now())
                        del r["gave_up"]
            h.update(available=True, last_ok_at=self.now())
        else:
            if h["available"]:
                self._log("unavailable", "health", subsystem=sub, error=error)   # once per outage
            h.update(available=False, last_error=error)

    def _available(self, sub: str) -> bool:
        a = self.adapters.get(sub)
        return a is not None and not a.placeholder and self._healthy.get(sub, {}).get("available", True)

    def _settle(self, t: str, accept: bool = False) -> None:
        """Settled; a retrieve's slot entry goes. accept: someone else moved it: enforce's re-assert counts as used,
        and unless the policy is ignore the drift is recorded (overridden under enforce)."""
        d = self.s["desired"][t]
        d["settled"], d["retry"] = True, None
        if d.get("slot"):
            self._unstore(d["slot"], t)
        if accept:
            d["reasserted"] = True
            if d["policy"] != "ignore":
                dr = self.s["drift"].setdefault(t, {"observed": None, "desired": d["value"], "at": self.now()})
                dr["observed"] = self._belief(t)["value"]
                if d["policy"] == "enforce":
                    dr["overridden"] = True

    def _mark_unsettled(self, t: str) -> None:
        self.s["desired"][t].update(settled=False, retry={"attempts": 0, "next_at": self.now(), "explained": [],
                                                          "final": False})

    def _unstore(self, slot: str, t: str) -> None:
        """Drop one slot entry, and the slot once it is empty."""
        entries = self.s["stored"].get(slot)
        if entries is not None:
            entries.pop(t, None)
            if not entries:
                del self.s["stored"][slot]

    def _slots(self) -> dict[str, str]:
        return {t: d["slot"] for t, d in self.s["desired"].items() if d.get("slot")}

    # signals (§4.5) ----------------------------------------------------------------------------------------------
    def _edges(self, to: Mode) -> tuple[list[str], list[str]]:
        entered = self.s["mode"]["entered"]
        exits = [m for m in reversed(entered) if m not in to.family]    # includes names the config no longer has
        enters = [m for m in to.family if m not in entered]
        return exits, enters

    async def _fire(self, tr: dict, sig: Step, row: dict, key: str | None = None) -> None:
        """One signal, at most once: its ledger (the caller's `leaving`/`entered`, or `fired` for a rule signal's key)
        is on disk before it is sent (F9). Failures are reported, never retried."""
        if key:                                   # rule signals: once per transition, even across a resume
            if key in tr["fired"]:
                self._row(tr, row, "skipped", "already fired", "fired")
                return
            tr["fired"].append(key)
        a = self.adapters.get(sig.subsystem)
        if a is None or a.placeholder:            # nothing sent; not recorded as fired
            if key:
                tr["fired"].remove(key)
            self._row(tr, row, "skipped", "no adapter", "unavailable")
            return
        self._row(tr, row, "running")             # saves: the ledger is on disk before the send
        try:
            out = await asyncio.wait_for(a.signal(sig.member, sig.action, sig.args), self.signal_timeout_s)
        except asyncio.TimeoutError:
            out = Outcome("unconfirmed", "timeout")
        except Exception as e:
            out = Outcome("failed", err(e))
        self._row(tr, row, out.status, out.detail)

    # slot lifetime (§4.6) ----------------------------------------------------------------------------------------
    def _close_slots(self, to: Mode, cfg: RoomConfig) -> None:
        """At the end of a completed transition: a slot entry lives while the room is in modes that store into its
        slot or retrieve it (R17). A retrieve that didn't land leaves it closed, for the retry (R6)."""
        stores_into = {e.store for e in to.state.values() if e.store}
        for slot, entries in self.s["stored"].items():
            for t, have in list(entries.items()):
                if slot == RESTORE_SLOT:          # "put back afterwards" is per target: kept while the room is in a
                    e = to.state.get(t)           # mode that restores it; put back (or tried) otherwise
                    if e and e.restore:
                        continue
                    if e and e.retrieve == RESTORE_SLOT and not _expired(have, e, self.now()):
                        r = (self.s["desired"].get(t) or {}).get("retry") or {}
                        have.update(closed=True, explained=r.get("explained", [have["value"]]),
                                    final=r.get("final", False))
                        continue                  # the put-back didn't land: kept for the retry (R6)
                    del entries[t]                # put back, too old, or the destination now owns the target
                    continue
                if not cfg.uses(slot, t):         # no mode stores or retrieves (slot, t) any more
                    have["orphan"] = True         # R9: kept and shown; forgotten only by the owner
                    continue
                have.pop("orphan", None)
                e = to.state.get(t)
                if e and e.retrieve == slot:      # a retrieve that landed was cleared already
                    if _expired(have, e, self.now()):
                        del entries[t]            # too old: `else` applied
                    else:
                        r = (self.s["desired"].get(t) or {}).get("retry") or {}
                        have.update(closed=True, explained=r.get("explained", [have["value"]]),
                                    final=r.get("final", False))
                    continue
                if slot in stores_into:
                    continue                      # the episode goes on (live -> live, even a set inside it)
                del entries[t]                    # the episode is over for t
        self.s["stored"] = {s: e for s, e in self.s["stored"].items() if e}

    # abort and rollback (§4.7) -------------------------------------------------------------------------------------
    def _rollback(self, failed: dict, step: Step, cfg: RoomConfig) -> dict | None:
        m = self.s["mode"]
        back = m["current"] if m["current"] in cfg.modes else cfg.default
        if (failed["source"] == "rollback" or not step.rollback or m["current"] is None or back == failed["to"]):
            return None                           # stay part-way: target stays set; the owner chooses Retry or Back
        if m["target"] not in m["via"]:
            m["via"].append(m["target"])
        m["target"] = back
        rb = {"id": f"t-{uuid.uuid4().hex[:8]}", "resumes": None, "from": m["current"], "via": list(m["via"]),
              "to": back, "source": "rollback", "status": "running", "phase": "running", "reapply": False,
              "rollback_of": {"id": failed["id"], "to": failed["to"], "detail": failed["detail"]},
              "started_at": self.now(), "detail": "", "fired": [], "steps": []}
        if failed.get("skip"):
            rb["skip"] = list(failed["skip"])     # undo only what Auto touched
        self.s["transition"] = rb
        self._save()
        return rb                                 # _run runs it in the same task and config

    # reconcile (§4.8) ----------------------------------------------------------------------------------------------
    async def reconcile_tick(self, sub: str) -> None:
        """One pass over one subsystem: read; settle, accept or retry what didn't land; report drift; enforce once.
        Applies run as cancellable _rtasks. A tick that a request overlapped drops what it read (R5)."""
        a = self.adapters.get(sub)
        if a is None or a.placeholder or a.kind == "action" or self._busy():
            return                                # a request, a transition or an apply owns the room
        watched = [t for t, d in self.s["desired"].items()
                   if t.split(".", 1)[0] == sub and (d["policy"] != "ignore" or not d["settled"])]
        also = ([t for t in (f"{sub}.{m}" for m in a.members) if t not in watched]
                if a.kind == "level" and getattr(a, "poll_all", False) else [])   # read only: beliefs, no decisions
        if not watched and not also:
            return
        gen, before = self._gen, self._fingerprint()
        observe_only = self.s["mode"]["target"] is not None    # failed and not rolled back: never enforce (F4)
        mode = self.cfg.modes.get(self.s["mode"]["current"])
        alone = {t: e for t in watched if mode and (e := mode.state.get(t)) is not None and e.skip_targets}
        looks = self._level({c for e in alone.values() for c, _ in e.skip_targets}) - set(watched) - set(also)
        reads = (watched + also if a.kind == "level" else []) + sorted(looks)   # skip_if's targets: fresh readings
        if reads:
            await self._refresh(reads)
            if gen != self._gen or self._busy():
                return
        todo: dict[str, tuple[Any, str]] = {}
        for t in watched:
            d, b = self.s["desired"][t], self.s["believed"].get(t)
            if b is None or b.get("inflight") or t in self._moving or b["confidence"] == "stale":
                continue                          # can't decide yet
            if not d["settled"]:                  # our command didn't land (S9): retry
                r = d["retry"]
                if r is None or r.get("final") or a.kind == "edge_toggle":
                    continue                      # never sent here, refused on purpose (R12, R15), or a toggle
                if b["confidence"] in ("confirmed", "unconfirmed") and same(b["value"], d["value"], a):
                    self._settle(t)               # it got there after all (clears a retrieve's slot)
                    continue
                if a.kind == "level" and not any(same(b["value"], x, a) for x in r.get("explained", [])):
                    self._settle(t, accept=True)  # R2: a reading none of our commands explains: someone else moved
                    continue                      # it; accept, never fight
                if r.get("gave_up") or a.dry_run or observe_only or self.now() < r["next_at"]:
                    continue
                if t in alone and self._left_alone(alone[t]):
                    continue                      # skip_if holds: no retry while it does (none used up either)
                if r["attempts"] >= len(BACKOFF):
                    r["gave_up"] = True
                    self._log("gave_up", "reconcile", target=t, attempts=r["attempts"])
                    continue
                r["attempts"] += 1
                r["next_at"] = self.now() + BACKOFF[min(r["attempts"], len(BACKOFF) - 1)]
                todo[t] = (d["value"], "retry")
                continue
            if a.kind != "level" or d["policy"] == "ignore":
                continue
            if same(b["value"], d["value"], a):
                self.s["drift"].pop(t, None)
                continue
            drift = self.s["drift"].get(t) or {"observed": b["value"], "desired": d["value"], "at": self.now()}
            drift["observed"] = b["value"]
            if (d["policy"] == "enforce" and not d["reasserted"] and not observe_only
                    and t in alone and self._left_alone(alone[t])):
                pass                              # skip_if holds: not re-asserted while it does (the drift is shown)
            elif d["policy"] == "enforce" and not d["reasserted"] and not observe_only:
                todo[t] = (d["value"], "reassert")
                d["reasserted"] = drift["reasserted"] = True
            elif d["policy"] == "enforce":
                drift["overridden"] = True        # F8/G13: re-asserted once already; never fight
            self.s["drift"][t] = drift
        if todo:
            task = asyncio.create_task(self._reconcile_apply(todo, gen))
            self._rtasks.add(task)
            task.add_done_callback(self._rtasks.discard)
        if self._fingerprint() != before:         # a tick that changes nothing doesn't write (§3.4)
            self._save()

    async def _reconcile_apply(self, todo: dict[str, tuple[Any, str]], gen: int) -> None:
        async with self._work:
            if gen != self._gen:
                return                            # R5: a request or a hand set came in while this waited
            todo = {t: (v, why) for t, (v, why) in todo.items()
                    if t in self.s["desired"] and same(self.s["desired"][t]["value"], v, self._adapter(t))}
            if todo:
                await self._apply(None, {t: v for t, (v, _) in todo.items()}, self._slots(),
                                  reasserts={t for t, (_, why) in todo.items() if why == "reassert"},
                                  mode=self.cfg.modes.get(self.s["mode"]["current"]))

    async def reconcile_once(self) -> None:
        """One tick of every subsystem, each followed by the applies it started (for tests and manual runs)."""
        for sub in list(self.adapters):
            await self.reconcile_tick(sub)
            while pending := [t for t in self._rtasks if not t.done()]:
                await asyncio.wait(pending)

    def _start_reconcile_loops(self) -> None:
        async def loop(sub: str, period: float) -> None:
            while True:
                await asyncio.sleep(period)
                try:
                    await self.reconcile_tick(sub)
                except Exception as e:            # a bug in one tick must not stop the loop
                    self._log("error", "reconcile", subsystem=sub, error=err(e))
        for sub, a in self.adapters.items():
            if not a.placeholder and a.kind != "action":
                self._loops.append(asyncio.create_task(loop(sub, a.poll_s or EDGE_TICK_S)))

    # restart (§4.9) --------------------------------------------------------------------------------------------------
    async def start(self, reconcile: bool = True) -> None:
        """After a restart: resume toward mode.target (crash, SIGTERM, cancel), or enter the default mode. Returns once
        the transition is launched; then starts one reconcile loop per subsystem (they skip while a transition runs)."""
        tr = self.s["transition"]
        for r in (tr or {}).get("steps", []):     # at most once: a signal that was being sent is not sent again
            if r["kind"] == "signal" and r["status"] == "running":
                r.update(status="unconfirmed", detail="process stopped while sending")
        for t, b in self.s["believed"].items():
            a = self._adapter(t)
            if a and a.kind == "edge_discrete" and b["confidence"] == "unknown" and t in self.s["desired"]:
                self._mark_unsettled(t)           # S10/R3: a retry due now; or the next transition re-sends it
        self._save()
        m = self.s["mode"]
        if m["target"] is not None:
            await self._request(m["target"] if self.cfg.is_concrete(m["target"]) else self.cfg.default, "resume")
        elif not self.cfg.is_concrete(m["current"]):
            await self._request(self.cfg.default, "boot")
        if reconcile:
            self._start_reconcile_loops()

    async def stop(self) -> None:
        """Service shutdown: stop the reconcile loops, the transition and any hand set. The state file keeps an
        unfinished transition's target, so the next start() resumes toward it."""
        for t in self._loops:
            t.cancel()
        await asyncio.gather(*self._loops, return_exceptions=True)
        self._loops.clear()
        await self._stop_tasks()

    # changes by hand and owner corrections (§4.11) -------------------------------------------------------------------
    async def hand_set(self, t: str, value: Any, hold: bool = False, source: str = "web") -> asyncio.Task:
        """PUT /room/targets/{t}, PUT /screens/{id}: one target by hand, under the current mode's policy. Returns the
        running change (202): its result is the Outcome. KeyError: unknown target (404); ValueError: bad value (400);
        Refused: unavailable (409). The person's value always goes: an enforced target takes it (drift overridden)
        until the next switch, and a toggle whose state is unknown gets one press, the tapped value believed
        (unconfirmed). `hold` is kept for the log."""
        a, member = self._target(t)
        v = a.normalize(member, value)
        if a.placeholder or a.dry_run:
            raise Refused("unavailable", f"{t}: " + ("no adapter" if a.placeholder else "writes are off (dry run)"))
        self._gen += 1
        async with self._req:
            running = bool(self._task and not self._task.done())
            if running:                           # owner 17 Oct: a switch running never blocks a hand change (Auto on /
                self._hand_wins[t] = v            # off mid-switch): it applies now, the switch leaves it alone, and the
                task = asyncio.create_task(self._hand_apply_during(t, v, source, hold))   # value holds until the next
                self._rtasks.add(task)            # switch
                task.add_done_callback(self._rtasks.discard)
                return task
            await self._stop_tasks(rtasks_only=True)    # the last change wins; devices keep moving, marks stay
            task = asyncio.create_task(self._hand_apply(t, v, source, hold))
            self._rtasks.add(task)
            task.add_done_callback(self._rtasks.discard)
        return task

    def _driving(self, sub: str) -> bool:
        """The running switch is sending to this subsystem right now (never two commands to one adapter at once)."""
        tr = self.s.get("transition")
        return bool(tr) and any(r["status"] == "running" and (r["subsystem"] == sub or r["subsystem"].startswith(sub + "."))
                                for r in tr.get("steps", []))

    def _hand_marks(self, t: str, v: Any, source: str) -> None:
        """The user's value wins until the next switch: desired settles, no retry, drift says by hand (as _hand_apply)."""
        d = self.s["desired"].get(t)
        if d and not same(v, d["value"], self._adapter(t)):
            d["settled"], d["retry"], d["reasserted"] = True, None, True
            if d.get("slot"):
                self._unstore(d["slot"], t)
            if d["policy"] != "ignore":
                self.s["drift"][t] = {"observed": v, "desired": d["value"], "at": self.now(), "manual": True,
                                      "overridden": d["policy"] == "enforce", "source": source}
        elif d:
            self.s["drift"].pop(t, None)

    async def _hand_apply_during(self, t: str, v: Any, source: str, hold: bool) -> Outcome:
        """A hand set while a switch runs: outside the switch's work lock, sent as soon as the switch isn't driving
        that subsystem (at most HAND_WAIT_S; then sent anyway: the person's newest choice)."""
        sub, member = t.split(".", 1)
        for _ in range(int(HAND_WAIT_S / 0.05)):
            if not self._driving(sub):
                break
            await asyncio.sleep(0.05)
        out = (await self._send(sub, {member: v}))[t]
        self._hand_marks(t, v, source)
        if out.status == "ok" and t not in self.s["drift"] and self.s["desired"].get(t):
            self._settle(t)
        self._log("hand_set", source, target=t, value=v, hold=hold, status=out.status, during="switch")
        self._save()
        return out

    async def _hand_apply(self, t: str, v: Any, source: str, hold: bool) -> Outcome:
        async with self._work:
            sub, member = t.split(".", 1)
            out = (await self._send(sub, {member: v}))[t]    # no row; a hand set is reported, not retried
            d = self.s["desired"].get(t)
            if d and not same(v, d["value"], self._adapter(t)):
                d["settled"], d["retry"], d["reasserted"] = True, None, True   # the user's value wins until the next transition
                if d.get("slot"):
                    self._unstore(d["slot"], t)   # a closed retrieve is abandoned, not fought
                if d["policy"] != "ignore":
                    self.s["drift"][t] = {"observed": v, "desired": d["value"], "at": self.now(), "manual": True,
                                          "overridden": d["policy"] == "enforce", "source": source}
            elif d:
                self.s["drift"].pop(t, None)
                if out.status == "ok":
                    self._settle(t)
            self._log("hand_set", source, target=t, value=v, hold=hold, status=out.status)
            self._save()
            return out

    async def set_target(self, target: str, value: Any, cause: str = "manual", hold: bool = False) -> Outcome:
        """aa's name for hand_set, waiting for the result; a change superseded by a mode switch gives failed."""
        task = await self.hand_set(target, value, hold=hold, source=cause)
        try:
            return await task
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if task.cancelled() and not (me and me.cancelling()):
                return Outcome("failed", "superseded by a mode switch")
            raise

    def assume(self, t: str, value: Any, cause: str) -> None:
        """Tell the room what an edge target is doing without sending anything (a toggle relay after a restart)."""
        self._check_free()
        a, member = self._target(t)
        v = a.normalize(member, value)
        if a.kind == "level":
            raise Refused("readable", f"{t} is read back; it can't be assumed")
        self._believe(t, v, "unconfirmed")
        self._log("assume", cause, target=t, value=v)
        self._save()

    def stored_edit(self, slot: str, t: str, value: Any, cause: str) -> None:
        """Correct an existing stored value. It keeps its `at`, so its expiry doesn't move."""
        self._check_free()
        a, member = self._target(t)
        v = a.normalize(member, value)
        entry = self.s["stored"].get(slot, {}).get(t)
        if entry is None:
            raise Refused("unknown_stored", f"nothing stored for {t} in {slot!r}")
        old = entry["value"]
        entry.update(value=v, confidence="confirmed", edited_at=self.now())
        self._log("stored_edit", cause, slot=slot, target=t, old=old, new=v)
        self._save()

    async def stored_restore(self, slot: str, t: str, cause: str) -> Outcome:
        """Put one stored value back now (a blind that missed its retrieve), with hold; cleared when it arrives."""
        self._check_free()
        entry = self.s["stored"].get(slot, {}).get(t)
        if entry is None:
            raise Refused("unknown_stored", f"nothing stored for {t} in {slot!r}")
        out = await self.set_target(t, entry["value"], cause=f"restore {slot}: {cause}", hold=True)
        if out.status == "ok":
            self._unstore(slot, t)
            self._save()
        return out

    def stored_forget(self, slot: str, t: str | None, cause: str) -> None:
        """Drop one stored value, or a whole slot: the next retrieve uses its `else`. The way to clear an orphan."""
        self._check_free()
        entries = self.s["stored"].get(slot, {})
        old = {t: entries.pop(t, {}).get("value")} if t else {k: e["value"] for k, e in entries.items()}
        self.s["stored"] = {s: e for s, e in self.s["stored"].items() if e and (t or s != slot)}
        self._log("stored_forget", cause, slot=slot, target=t, old=old)
        self._save()

    # config reload (§2.1) ----------------------------------------------------------------------------------------
    def reload(self, config: RoomConfig, cause: str) -> dict:
        """Swap in a new, already validated config. Nothing moves; during a transition it is applied when the
        transition ends (the task keeps the config it started with)."""
        if self._task and not self._task.done():
            self._reload_next = (config, cause)
            return {"revision": config.revision, "previous": self.cfg.revision, "pending_targets": None, "deferred": True}
        return self._reload_now(config, cause)

    def _reload_now(self, config: RoomConfig, cause: str) -> dict:
        """The current mode's desired values stay; policies only relax (enforce -> respect -> ignore); targets the new
        config no longer manages are dropped. Values it would change are pending until the next switch or a re-apply."""
        old, self.cfg = self.cfg.revision, config
        for sub, a in config.adapters.items():
            self.adapters.setdefault(sub, a)      # new placeholders
        m = self.s["mode"]
        mode = config.modes.get(m["target"] or m["current"])
        new_vals = self._desired(mode, mutate=False)[0] if mode else {}
        kept, pending = {}, []
        for t, d in self.s["desired"].items():
            e = mode.state.get(t) if mode else None
            if e is None:
                pending.append(t)
                continue
            kept[t] = {**d, "policy": min(e.policy, d["policy"], key=POLICY_RANK.get)}
            if t in new_vals and not same(new_vals[t], d["value"], self._adapter(t)):
                pending.append(t)
        pending += [t for t in new_vals if t not in self.s["desired"]]
        self.s["desired"] = kept
        self.s["config_pending"] = sorted(set(pending))
        self._log("reload", cause, old=old, new=config.revision, pending=self.s["config_pending"])
        self._save()
        return {"revision": config.revision, "previous": old, "pending_targets": self.s["config_pending"], "deferred": False}

    # views (§7) ------------------------------------------------------------------------------------------------------
    def status(self) -> dict:
        """Additive to LIVE_V1 §3 status: mode with transition and last_transition, subsystems with members, stored
        slots and config. Times are ages in seconds, never clock times."""
        now = self.now()

        def age(at: float | None) -> float | None:
            return None if at is None else round(now - at, 1)

        def public(tr: dict | None) -> dict | None:
            if tr is None:
                return None
            out = {k: v for k, v in tr.items() if k not in ("started_at", "finished_at")}
            out["started_age_s"] = age(tr.get("started_at"))
            if tr.get("finished_at") is not None:
                out["finished_age_s"] = age(tr["finished_at"])
            return out
        m, tr = self.s["mode"], self.s["transition"]
        running = tr if tr and tr["status"] == "running" else None
        last = tr if tr and not running else self.s["last_transition"]   # a cancelled record waiting to be carried on
        mode = {k: m[k] for k in ("current", "target", "via", "entered", "leaving")}
        mode.update(since_age_s=age(m["since"]), default=self.cfg.default,
                    transition=public(running), last_transition=public(last))
        guarded = {t for r in self.cfg.transitions for s in r.steps for t in s.abort_on}
        subsystems = {}
        for sub, a in self.adapters.items():
            h = self._healthy.get(sub, {})
            members = {}
            for mem in a.members:
                t = f"{sub}.{mem}"
                b, d, dr = self.s["believed"].get(t) or {}, self.s["desired"].get(t), self.s["drift"].get(t)
                fl, r = b.get("inflight"), (d or {}).get("retry")
                members[mem] = {
                    "believed": b.get("value"), "confidence": b.get("confidence", "unknown"), "age_s": age(b.get("at")),
                    "sent_age_s": age(b.get("sent_at")),          # Auto discounts the room's own changes
                    "moving": t in self._moving,
                    "inflight": {"value": fl["value"], "until_s": round(fl["until"] - now, 1)} if fl else None,
                    "desired": d["value"] if d else None, "policy": d["policy"] if d else None,
                    "settled": d["settled"] if d else None,
                    "retry": ({k: v for k, v in r.items() if k != "next_at"} | {"next_in_s": round(r["next_at"] - now, 1)}
                              if r else None),
                    "drift": ({k: v for k, v in dr.items() if k != "at"} | {"age_s": age(dr["at"])} if dr else None)}
            s = {"kind": a.kind, "available": self._available(sub), "dry_run": a.dry_run,
                 "last_error": "no adapter" if a.placeholder else h.get("last_error"),
                 "last_ok_age_s": age(h.get("last_ok_at")),
                 "believed": (members[a.members[0]]["believed"] if len(a.members) == 1
                              else {mem: x["believed"] for mem, x in members.items()}),
                 "members": members}
            if (a.placeholder or a.dry_run) and any(t.split(".", 1)[0] == sub for t in guarded):
                s["guard"] = "inactive"
            subsystems[sub] = s
        max_age: dict[tuple[str, str], float] = {}  # the shortest max_age_s of any retrieve of (slot, target)
        for mo in self.cfg.modes.values():
            for t, e in mo.state.items():
                if e.retrieve and e.max_age_s:
                    max_age[(e.retrieve, t)] = min(max_age.get((e.retrieve, t), e.max_age_s), e.max_age_s)
        stored = {}
        for slot, entries in self.s["stored"].items():
            stored[slot] = {}
            for t, e in entries.items():
                x = {"value": e["value"], "age_s": age(e.get("at")), "confidence": e.get("confidence"),
                     "from_mode": e.get("from_mode"), "closed": bool(e.get("closed")),
                     "expires_in_s": (round(limit - (now - e["at"]), 1)
                                      if (limit := max_age.get((slot, t), e.get("max_age_s"))) and e.get("at") is not None
                                      else None)}
                if e.get("orphan") or not self.cfg.uses(slot, t):
                    x["orphan"] = True
                if e.get("edited_at") is not None:
                    x["edited_age_s"] = age(e["edited_at"])
                if e.get("final"):
                    x["final"] = True
                stored[slot][t] = x
        return {"mode": mode, "subsystems": subsystems, "stored": stored,
                "config": {"revision": self.cfg.revision, "error": self.config_error,
                           "pending_targets": self.s["config_pending"],
                           "warnings": [explain({**w, "level": "warning"}) for w in self.cfg.lint()]}}

    def describe(self) -> dict:
        """Everything the Room page's editor needs, in its words (c8): targets with value schemas, labels, groups,
        category and the row options each takes (entry_schema); subsystems with labels and categories; the modes as
        flat rows with page words; the forms for a mode and for the room; rules; warnings with page text."""
        in_groups: dict[str, list[str]] = defaultdict(list)
        for g, ts in self.cfg.groups.items():
            for t in ts:
                in_groups[t].append(g)
        pb = self.cfg.put_back_max_age_s
        targets = {}
        for t in self.cfg.targets:
            a, m = self._adapter(t), t.split(".", 1)[1]
            vs = a.value_schema(m)
            targets[t] = {"subsystem": a.subsystem, "member": m, "kind": a.kind, "category": a.category,
                          "subsystem_label": _subsystem_label(a), "label": self.cfg.labels.get(t, a.label(m)),
                          "schema": vs, "entry_schema": entry_schema(vs, pb), "groups": in_groups.get(t, [])}
        modes = {}
        for name, mode in self.cfg.modes.items():
            if mode.abstract:
                continue                          # an old-style base: lint says to flatten it
            entries = {}
            for t, e in mode.state.items():
                src, sel = mode.origin.get(t, (name, t))
                a, m = self._adapter(t), t.split(".", 1)[1]
                entries[t] = {"verb": e.verb(), "value": e.set if e.sets else None,
                              "words": a.words(m, e.set) if e.sets else None, "restore": e.restore,
                              "store_if": list(e.store_if), "policy": e.policy, "max_age_s": e.max_age_s,
                              "opts": e.opts, "from": src, "selector": sel}
                if e.skip_if:
                    entries[t]["skip_if"] = {k: list(v) for k, v in e.skip_if.items()}   # as written
                if (slot := e.retrieve or (None if e.restore else e.store)):
                    entries[t]["slot"] = slot     # an old-style pair only: the page keeps pairs whole when deleting
            modes[name] = {"label": mode.label, "description": mode.description, "icon": mode.icon, "voice": mode.voice,
                           "order": len(modes), "entries": entries,
                           "on_enter": [s.signal for s in mode.on_enter], "on_exit": [s.signal for s in mode.on_exit]}
        names = list(modes)
        order = self._subsystem_order()
        vocab = {s: _vocabulary(a) for s, a in self.adapters.items()}       # e.g. show's Auto situations
        situations = [w for v in vocab.values() for w in v.get("situations") or ()]
        return {"revision": self.cfg.revision, "default": self.cfg.default, "put_back_max_age_s": pb,
                "targets": targets, "groups": self.cfg.groups, "groups_from_rooms": sorted(self.cfg.derived),
                "subsystem_order": order,
                "subsystems": {s: {**vocab[s], "kind": a.kind, "label": _subsystem_label(a), "category": a.category,
                                   "order": order.index(s), "available": self._available(s), "placeholder": a.placeholder,
                                   "dry_run": a.dry_run, "members": list(a.members), "actions": a.actions,
                                   "poll_s": a.poll_s, "change_time_s": a.change_time_s}
                               for s, a in self.adapters.items()},
                "modes": modes, "entry_schema": entry_schema(None, pb), "mode_schema": mode_schema(names, situations),
                "room_schema": room_schema(names, pb), "rules": self.cfg.rules(),
                "warnings": [explain({**w, "level": "warning"}) for w in self.cfg.lint()]}

    def _subsystem_order(self) -> list[str]:
        """The order the page lists subsystems in, from config: as rooms.json lists them (the order of each room's
        lists, then its own zone: its "<room>:<subsystem>" groups), then any others, then the room's settings."""
        rank: dict[str, int] = {}
        for g in self.cfg.derived:
            sub = g.split(":", 1)[1]
            if sub in self.adapters:
                rank.setdefault(sub, len(rank))
        subs = list(self.adapters)
        return sorted(subs, key=lambda s: (self.adapters[s].category == "setting", rank.get(s, len(rank)), subs.index(s)))

    async def plan(self, name: str, refresh: bool = False, config: RoomConfig | None = None) -> dict:
        """Dry run: the rows request_mode(name) would write now, optionally under a draft `config`. Changes nothing;
        refresh=True re-reads readable targets first (reads only)."""
        cfg = config or self.cfg
        to = self._with_restores(cfg.mode(name))
        if refresh and not self._busy():
            await self._refresh(self._level(to.state) | self._level(self._skip_watch(to)))
        m = self.s["mode"]
        via = m["via"] + ([m["target"]] if m["target"] and m["target"] not in m["via"] else [])
        tr = {"id": None, "from": m["current"], "via": via, "to": name,
              "reapply": m["target"] is None and name == m["current"], "fired": []}
        saved, self.cfg = self.cfg, cfg           # no awaits below: placeholders come from the draft
        try:
            to, left = self._leave_alone(to, tr)  # what skip_if would leave alone now
            p = self._plan(to, tr, cfg, mutate=False, left=left)
        finally:
            self.cfg = saved
        return {"from": tr["from"], "via": via, "to": name, "reapply": tr["reapply"],
                "transition": tr.get("rule"), "revision": cfg.revision,
                "draft": config is not None, "steps": p.rows, "notes": tr.get("notes", {})}
