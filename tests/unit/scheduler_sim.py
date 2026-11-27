"""A simulated house for end-to-end tests of the scheduler (owner, 25 Nov: "proper e2e logic test patterns for the
scheduler machinery ... a carefully reasoned test harness").

What runs is the scheduler itself: routes.scheduler.scheduler_step (one pass of a destination's loop: trigger
evaluation every SCHEDULER_TICK_INTERVAL, the instruction queue with its urgent / important rules, waits and their
interruption), process_triggers, the event store (throw_event, scopes, TTLs, delays), schedule and context stacks
(push, load_schedule_on_stack, unload), the exported-variable registry and run_instruction with every handler, Jinja
included. Only the edges are stood in for:

- the clock: datetime.now() and time.time() in the scheduler modules read a simulated clock that only moves when the
  test says so (Sim.run), so hours of schedule run in milliseconds and every timing is exact;
- the world: publish_to_destination records what each screen is shown (and keeps it as the screen's published file,
  so _current_image reads it), and the transition it plays when one was made for the change (Sim.made); generate /
  animate / reason / device actions are recorded, not done; an overlay runs the real handler (its text rendered as
  on the screens) and only the send to the screen is recorded;
- persistence: state files and the variable registry go to a temp folder; the site's destinations and groups are
  the test's own (no live state is read or written);
- threads: no scheduler thread is started; Sim.run steps each destination's loop in turn, as many passes per
  simulated second as it takes to run what is ready, like the real loop's 0.05-0.1 s passes.

    sim = Sim(monkeypatch, tmp_path, start=datetime(2026, 11, 25, 15, 0),
              destinations={"north": ["room"], "south": ["room"], "cloud": ["room"]})
    sim.install("north", schedule)          # a running scheduler with this schedule on its stack
    sim.throw("room", "Replay_day")         # as Alexa / the Room page / a display page's button would
    sim.run(minutes=5)
    sim.shown("north")                      # [(seconds since start, picture), ...]
"""
from __future__ import annotations

import copy
import datetime as _dt
import shutil
import time as _time
from pathlib import Path
from types import SimpleNamespace

import routes.publisher as publisher
import routes.scheduler as sch
import routes.scheduler_handlers as sh
import routes.scheduler_queue as sq
import routes.scheduler_utils as su
import routes.utils as ru


class _Clock:
    def __init__(self, start: _dt.datetime):
        self.start = start
        self.t = 0.0

    def now(self) -> _dt.datetime:
        return self.start + _dt.timedelta(seconds=self.t)

    def epoch(self) -> float:
        return self.now().timestamp()


def _fake_datetime(clock: _Clock):
    class FakeDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            n = clock.now()
            return n if tz is None else n.replace(tzinfo=tz)

        @classmethod
        def utcnow(cls):
            return clock.now()
    return FakeDatetime


class _Time:
    """time, with time() on the simulated clock (perf_counter, sleep and the rest are the real ones)."""

    def __init__(self, clock: _Clock):
        self._clock = clock

    def time(self) -> float:
        return self._clock.epoch()

    def __getattr__(self, name):
        return getattr(_time, name)


GLOBALS = ("scheduler_schedule_stacks", "scheduler_contexts_stacks", "scheduler_states", "active_events",
           "event_history", "scheduler_logs", "important_triggers", "last_trigger_executions")


class Sim:
    def __init__(self, monkeypatch, tmp_path: Path, start: _dt.datetime, destinations: dict[str, list[str]]):
        """destinations: id -> its groups (e.g. {"north": ["drawing-room"], ...}); each has a bucket."""
        self.mp, self.dir, self.clock = monkeypatch, Path(tmp_path), _Clock(start)
        self.destinations = destinations
        self.loops: dict[str, sch.LoopState] = {}
        self.published: dict[str, list] = {d: [] for d in destinations}
        self.actions: list[tuple[float, str, str, dict]] = []   # (t, destination, action, instruction) not done
        self.made: set[tuple[str, str]] = set()                  # transitions made: (from picture, to picture)
        self.ran = 0
        fake_dt = _fake_datetime(self.clock)
        for mod in (sch, su, sh, sq):
            if hasattr(mod, "datetime"):
                monkeypatch.setattr(mod, "datetime", fake_dt)
        fake_time = _Time(self.clock)
        for mod in (sch, su, sq):
            if hasattr(mod, "time"):
                monkeypatch.setattr(mod, "time", fake_time)
        monkeypatch.setattr(sh, "time", fake_time.time)                       # handlers: `from time import time`

        # quiet: the scheduler logs a debug line for almost everything, which is most of a long simulation's time
        # (warnings and errors still show)
        for mod in (sch, su, sh, sq, ru, publisher):
            for name in ("debug", "info"):
                if hasattr(mod, name):
                    monkeypatch.setattr(mod, name, lambda *a, **k: None)

        # nothing live: state files, the variable registry, the destinations and their buckets
        (self.dir / "state").mkdir(exist_ok=True)
        monkeypatch.setattr(su, "get_scheduler_storage_path", lambda d: str(self.dir / "state" / f"{d}.json"))
        monkeypatch.setattr(su, "VARS_REGISTRY_PATH", str(self.dir / "vars.json"))
        dests = [{"id": d, "name": d, "groups": g, "has_bucket": True, "type": "output_file", "file": d}
                 for d, g in destinations.items()]
        real_load = ru._load_json_once
        monkeypatch.setattr(ru, "_load_json_once",
                            lambda cat, name, *a, **k: copy.deepcopy(dests) if cat == "destination" else real_load(cat, name, *a, **k))
        for d in destinations:
            (self.dir / "buckets" / d).mkdir(parents=True, exist_ok=True)
        import routes.bucketer as bk
        monkeypatch.setattr(bk, "bucket_path", lambda d: self.dir / "buckets" / d)
        monkeypatch.setattr(publisher, "get_published_info", self._published_info)
        monkeypatch.setattr(publisher, "publish_to_destination", self._publish)

        # the world's other effects are recorded, not done
        for name, action in (("handle_generate", "generate"), ("handle_animate", "animate"), ("handle_reason", "reason"),
                             ("handle_device_wake", "device-wake"),
                             ("handle_device_sleep", "device-sleep"), ("handle_device_standby", "device_standby"),
                             ("handle_device_media_sync", "device-media-sync"), ("handle_display", "display"),
                             ("handle_purge", "purge"), ("handle_set_mode", "set_mode"), ("handle_set_auto", "set_auto"),
                             ("handle_room_state", "room_state")):
            if hasattr(sch, name):
                monkeypatch.setattr(sch, name, self._recorder(action))
        # an overlay: the real handler renders its text; what it would send to the screen is recorded
        import routes.display as display
        monkeypatch.setattr(display, "send_overlay", self._send_overlay)
        monkeypatch.setattr(sh, "get_qr", lambda **k: "")
        self._overlay_dest = None
        real_overlay = sch.handle_overlay

        def overlay(instruction, context, now, output, dest):
            self._overlay_dest = dest
            return real_overlay(instruction, context, now, output, dest)
        monkeypatch.setattr(sch, "handle_overlay", overlay)

        # a clean scheduler: no other test's stacks, events, queues or running loops
        for g in GLOBALS:
            for mod in (su, sch):
                if hasattr(mod, g) and isinstance(getattr(mod, g), dict):
                    getattr(mod, g).clear()
        sq.instruction_queues.clear()
        sch.running_schedulers.clear()

    # -- the world -------------------------------------------------------------------------------------------------

    def _recorder(self, action):
        def record(instruction, context, now, output, dest):
            ins = copy.deepcopy(instruction)
            if action == "overlay":                     # the overlay handler renders its own text: so do we
                ins["text"] = su.process_jinja_template(ins.get("text", ""), context, dest)
            self.actions.append((self.clock.t, dest, action, ins))
            return False
        return record

    def _send_overlay(self, html=None, screens=None, duration=None, substitutions=None, clear=None, **kw):
        for dest in screens or [self._overlay_dest]:
            self.actions.append((self.clock.t, dest, "overlay", {"text": (substitutions or {}).get("TEXT"),
                                                                 "duration_ms": duration, "html": html}))

    def _publish(self, source=None, publish_destination_id=None, **kw):
        """A screen is shown `source`: recorded, and kept as the screen's published file (what _current_image reads).
        Asked for a transition, it plays the one made from what the screen shows to it (Sim.made), as the publisher's
        cached() would; else it cross-fades (transition None)."""
        name = Path(str(source)).name
        f = self.dir / "buckets" / publish_destination_id / name
        if not f.exists():
            f.write_bytes(b"picture")
        meta = kw.get("metadata") or {}
        before = (self.published.get(publish_destination_id) or [None])[-1]
        played = (before.name, name) if kw.get("transition") and before and (before.name, name) in self.made else None
        self.published.setdefault(publish_destination_id, []).append(
            SimpleNamespace(t=self.clock.t, source=str(source), name=name, kw=kw, meta=meta, transition=played))
        return {"success": True, "meta": {"filename": name}}

    def _published_info(self, dest):
        shown = self.published.get(dest) or []
        if not shown:
            return {}
        last = shown[-1]
        return {"published": last.name, "meta": last.meta}

    def shows(self, dest, picture, meta=None):
        """The screen already shows `picture` (a published file in its bucket), as if published before the test."""
        f = self.dir / "buckets" / dest / picture
        f.write_bytes(b"picture")
        self.published[dest].append(SimpleNamespace(t=-1, source=str(f), name=picture, kw={}, meta=meta or {}))
        return str(f)

    # -- the scheduler ---------------------------------------------------------------------------------------------

    def install(self, dest, schedule, vars: dict | None = None):
        """dest runs `schedule` (bottom of its stack), its scheduler running, with these context variables."""
        sch.scheduler_schedule_stacks[dest] = [copy.deepcopy(schedule)]
        sch.scheduler_contexts_stacks[dest] = [{"vars": dict(vars or {}), "last_generated": None}]
        sch.scheduler_states[dest] = "running"
        sch.running_schedulers[dest] = {"future": None, "loop": None}
        self.loops[dest] = sch.LoopState(sq.get_instruction_queue(dest))

    def load(self, dest, script):
        """A script loaded on dest's stack while it runs (Alexa's load_schedule intent, the API's push)."""
        return su.load_schedule_on_stack(dest, copy.deepcopy(script))

    def export(self, owner, var, value, scope):
        """owner's schedule exports var to scope (as an export_var instruction would)."""
        ctx = sch.scheduler_contexts_stacks.setdefault(owner, [{"vars": {}}])[-1]
        ctx.setdefault("vars", {})[var] = value
        su.register_exported_var(var, var, scope, owner, self.clock.now().isoformat())

    def throw(self, scope, key, payload=None, ttl="60s", delay=None):
        """An event thrown to scope: a destination, a group or global (Alexa, the Room page, a display page)."""
        return su.throw_event(scope=scope, key=key, payload=payload, ttl=ttl, delay=delay)

    def vars(self, dest):
        return sch.scheduler_contexts_stacks[dest][-1].get("vars", {})

    def stack(self, dest):
        return sch.scheduler_schedule_stacks.get(dest, [])

    def waiting(self, dest):
        ctx = (sch.scheduler_contexts_stacks.get(dest) or [{}])[-1]
        return "wait_until" in ctx

    def stop(self, dest):
        sch.running_schedulers.pop(dest, None)

    def restart(self, dest):
        """Flask restarts: what is saved (stacks, contexts, events) comes back from the state file, what is in memory
        only (the instruction queue, the loop's own state) is gone, and the scheduler resumes."""
        su.update_scheduler_state(dest, schedule_stack=sch.scheduler_schedule_stacks[dest],
                                  context_stack=sch.scheduler_contexts_stacks[dest], force_save=True)
        sq.get_instruction_queue(dest).clear()
        for g in ("scheduler_schedule_stacks", "scheduler_contexts_stacks"):
            getattr(su, g).pop(dest, None)
        state = su.load_scheduler_state(dest)
        sch.scheduler_schedule_stacks[dest] = state.get("schedule_stack", [])
        sch.scheduler_contexts_stacks[dest] = state.get("context_stack", [])
        sch.scheduler_states[dest] = "running"
        sch.running_schedulers[dest] = {"future": None, "loop": None}
        self.loops[dest] = sch.LoopState(sq.get_instruction_queue(dest))

    # -- running ---------------------------------------------------------------------------------------------------

    def run(self, seconds: float = 0, minutes: float = 0, dt: float = 1.0, until=None):
        """Advance the clock by seconds + minutes in steps of dt. At each step every running destination's loop is
        stepped until it stops making progress (nothing ran, nothing was queued). until(sim) ends it early."""
        end = self.clock.t + seconds + 60 * minutes
        while self.clock.t < end - 1e-9:
            for dest in list(self.loops):
                self._drain(dest)
            if until and until(self):
                return True
            self.clock.t = min(end, self.clock.t + dt)
        for dest in list(self.loops):
            self._drain(dest)
        return bool(until and until(self))

    def _drain(self, dest):
        st = self.loops[dest]
        for _ in range(500):
            if dest not in sch.running_schedulers or not sch.scheduler_schedule_stacks.get(dest):
                return
            q = st.instruction_queue
            before = (id(q.running), q.get_size(), self.waiting(dest))
            pause = sch.scheduler_step(dest, st)
            if pause is None:
                self.loops.pop(dest, None)
                return
            # progress: the queue took an instruction off, or grew or shrank, or a wait began or ended (the loop's own
            # "is the wait over?" check is not progress)
            if (id(q.running), q.get_size(), self.waiting(dest)) == before:
                return
        raise AssertionError(f"{dest}: the loop kept running instructions at one instant (a runaway block?)")

    # -- reading ---------------------------------------------------------------------------------------------------

    def shown(self, dest, since=0.0):
        """[(seconds since start, picture name)] published to dest at or after `since`."""
        return [(round(p.t, 1), p.name) for p in self.published.get(dest, []) if p.t >= since]

    def played(self, dest, since=0.0):
        """[(seconds since start, picture, the transition into it or None)] published to dest at or after `since`."""
        return [(round(p.t, 1), p.name, getattr(p, "transition", None)) for p in self.published.get(dest, [])
                if p.t >= since]

    def published_kw(self, dest):
        return [p.kw for p in self.published.get(dest, []) if p.t >= 0]

    def log(self, dest):
        return list(su.scheduler_logs.get(dest, []))

    def cleanup(self):
        shutil.rmtree(self.dir / "state", ignore_errors=True)
