"""outputs: the rig component (README "Stage 5", S5.3, S5.4; SHOW_ENGINE §9). c8's integration of B12's spec.

One ``Rig`` (brain component ``rig``, 1 Hz on show-brain) owns the fixture registry (fixtures.Registry, hot-reloaded),
the look engine (looks.LookEngine, built once) and one driver per enabled, built output (hue_out.HueOut,
artnet_lan.ArtNetLan), each fed by its own runner thread ``show-out-<id>``:

```
[show-rx]        Rig.on_hit(music.hit body): the engine's hit ring, then each hits-capable runner woken
[show-brain]     Rig.tick (1 Hz): Registry.poll (fixtures.yaml, type files, latency.json, patch.yaml, show.yaml looks
                 and lamps); engine.configure on a new view or new looks; drivers built, configured, started and
                 stopped as outputs are enabled and disabled (fixtures.yaml) or the rig is switched (show.yaml
                 rig.enabled); driver.tick (hue: its control task's housekeeping; artnet: ArtPoll, replies, failsafe)
[show-out-<id>]  the deadline loop, one per running output:
                     t = driver.plan(t_last, now, active)          (a hit: driver.plan_hit, else on_hit + plan)
                     wait until t - ahead                           (an Event: a hit or a reconfigure re-plans;
                                                                     ahead = max(BUILD_AHEAD_S, evaluate p95 + the
                                                                     GIL switch interval), at most BUILD_AHEAD_MAX_S)
                     frame = engine.evaluate(<id>, t, driver.caps)  (every input read by reference)
                     sleep until t; driver.set_frame(frame)
```

* **Off by default.** Nothing is built for an output unless fixtures.yaml says ``enabled: true`` for it, and nothing
  at all with show.yaml ``rig.enabled: false`` (the rollback: no registry, engine work or outputs; ``view()`` is
  None, so the director keeps patch.yaml's lamps). Every output ships off.
* **Degrade, never refuse.** A driver kind not built in Stage 5 (contract.DRIVERS_BUILT), a driver module that won't
  import or construct, or no look engine: that output reads ``not_built`` with the reason, the rest runs. A raising
  ``plan``/``evaluate``/``set_frame`` is counted and logged at most every LOG_S; RUNNER_ERRORS in a row within
  RUNNER_ERROR_WINDOW_S put the output in ``error`` for RUNNER_BACKOFF_S, then it carries on. A bad fixtures.yaml keeps
  the last good registry (fixtures.py).
* **No special cases.** Drivers come from their kind (contract.DRIVERS, fixed vocabulary) through DRIVER_MODULES;
  nothing branches on an output, node, fixture or group id.
* **Threads.** show-brain builds and swaps the outputs table, the view and the hit runners by reference; a runner owns
  its driver's frame path and its own stats; ``snapshot``, ``view``, ``frame_view``, ``hue_status`` read references
  from any thread. ``on_hit`` (show-rx) only appends to the engine's ring and sets Events.
* **Stop:** runners first (each within RUNNER_STOP_S), then each driver's ``stop`` (hue: the area is stopped, so the
  bridge gives the lamps back; artnet: nothing more is sent, the node's own failsafe takes over). An awaitable a
  driver's stop returns (hue on show-brain) is gathered into the one awaitable ``stop()`` returns; brain.py awaits it.
* **State across a restart** (README S5.14): ``persist`` keeps each driver's ``persist_state()`` (hue: what room asked
  to follow) by output id in state.json's ``rig``, also for outputs switched off since; ``restore`` hands it back to
  the driver's ``restore_state`` when that output is built again. While the hue output is off, what room asks
  (``hue_request``, ``hue_release``) still updates that saved set, so on again follows room's latest ask; and
  ``hue_status`` reports it as ``asked`` (the Hue routes' ``lights``, which room's re-sync compares with its own).
* **Hue off** (fixtures.yaml ``outputs.<hue>.enabled: false``, 68's contract): the driver is stopped at the next
  reconcile (its frames at once, then the area). A release in the next moments waits for that stop plus
  ``rig.hue.release_settle_s`` (the bridge's post-entertainment write), so ``released`` still means writable.
* **The Lights page** (README S5.23), each on show-brain through ``brain.call``: ``add_fixture``, ``update_fixture``,
  ``remove_fixture`` write config/room/fixtures.added.yaml (fixtures.Registrar: checked with the loader first, never
  fixtures.yaml) and reload the registry at once (no debounce: the file was replaced whole), so an output made for a
  new light starts at that reconcile. ``identify`` asks the look engine for full white on one fixture for a few seconds
  (``LookEngine.identify``) and wakes its runner. ``set_follow`` keeps who follows the music (room's DMX-lights device:
  FOLLOW_WORDS or a static colour) in ``follow_file`` (atomic) and hands the whole mapping to ``LookEngine.set_follow``
  at once, after every engine configure, and on each change. An engine without those methods degrades: identify
  answers ``ok: false``, the follow mapping is kept for when it has them. ``fixture_rows`` (any thread) is the rig
  view's per-fixture rows (contract.RigFixtureRow) in ``GET /api/v1/rig``.
* **Online** (thread ``show-rig-online``, off the frame path): one ``ping -c1 -W1`` per node address every
  ONLINE_CHECK_S; a row's ``online`` is its node's last answer. Art-Net goes out unicast whenever the output is on,
  whatever ping says, so a node works the moment it is plugged in; sending to an absent one is a counted, logged
  send error at most (artnet_lan).
"""

from __future__ import annotations

import asyncio
import collections
import importlib
import inspect
import ipaddress
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from . import contract

log = logging.getLogger("show_service.outputs")

#: driver kind -> "module:Class" (imported lazily; a missing one reads not_built and the rest runs)
DRIVER_MODULES: dict[str, str] = {"hue": "show_service.hue_out:HueOut", "artnet": "show_service.artnet_lan:ArtNetLan"}
ENGINE_MODULE = "show_service.looks:LookEngine"
REGISTRY_MODULE = "show_service.fixtures:Registry"
NODES_KEY = "artnet.nodes"         # artnet_lan.SHARED_KEY: the one Nodes per Rig, in ``shared``

RUNNER_BACKOFF_S = 5.0             # starting point: room's own error backoff (room_service/hue/output.py)
RUNNER_ERRORS = 3                  # this many failures in a row ...
RUNNER_ERROR_WINDOW_S = 10.0       # ... within this long: the output reads "error" for RUNNER_BACKOFF_S
RUNNER_STOP_S = 0.5                # a runner stops within this of stop (its Event)
ERROR_PAUSE_S = 0.05               # after a failed frame: never spin
BUILD_AHEAD_S = 0.001              # artnet_out's: evaluate at least this long before the planned time, send at it
BUILD_AHEAD_MAX_S = 0.004          # starting point: the adaptive build-ahead's ceiling (a hit inside it waits a frame)
BUILD_AHEAD_EVERY = 64             # frames between re-reads of a runner's own evaluate p95 (README S5.14)
WAIT_MAX_S = 0.5                   # a runner's longest single wait (it re-checks stop and re-plans)
REANCHOR_S = 1.0                   # a plan more than this in the past is re-anchored on now (never a burst)
MIN_STEP_S = 0.001                 # a plan never repeats the last planned time: at least this after it
LOG_S = 30.0
RING = 256                         # lateness and send-time samples kept per runner
RATE_WINDOW_S = 5.0
HUE_RELEASE_TIMEOUT_S = 15.0       # show.yaml rig.hue.release_timeout_s's starting point (hue_out's RIG_HUE_DEFAULTS)
HUE_SETTLE_S = 1.0                 # show.yaml rig.hue.release_settle_s's starting point (§9.4: the bridge's own write)
FIXTURES_FILE_WORDS = "config/room/fixtures.yaml"
IDENTIFY_S = 3.0                   # README S5.23: identify's full white, unless the call says how long
IDENTIFY_MAX_S = 30.0              # starting point: a longer ask is cut to this (the answer's ``seconds`` says so)
ONLINE_CHECK_S = 10.0              # README S5.23: each node address pinged this often (show-rig-online)
PING_WAIT_S = 1                    # ping -W: one echo's wait, s
FOLLOW_BRI_DEFAULT = 100           # a static colour sent with no ``bri``: as written (full)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _load(spec: str) -> Any:
    mod, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod), attr)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
        return float(v)
    return None


def _params(fn: Callable) -> int:
    """How many positional arguments ``fn`` takes (a fake engine's takes_hit may take only the hit)."""
    try:
        ps = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return 1
    if any(p.kind == p.VAR_POSITIONAL for p in ps):
        return 99
    return sum(1 for p in ps if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


def _pcts(xs: list[float]) -> dict[str, Optional[float]]:
    v = sorted(xs)
    n = len(v)
    return {"p50": round(v[n // 2], 3) if n else None, "p95": round(v[min(n - 1, int(0.95 * n))], 3) if n else None}


def off_status(oid: str, driver: str, state: str, detail: Optional[str], enabled: bool = False) -> dict:
    """An OutputStatus for an output with no running driver (off, not_built)."""
    return {"id": oid, "driver": driver, "enabled": enabled, "state": state, "detail": detail, "frames": 0,
            "rate_hz": 0.0, "late_ms": {"p50": None, "p95": None}, "errors": 0, "last_frame_age_s": None, "extra": {}}


def hue_off(output: Optional[str], detail: str, asked: Optional[list] = None) -> contract.HueStatus:
    """The HueStatus with no hue output running (the routes answer room ``idle``: contract.hue_answer). ``asked``:
    the set room asked, kept for on again (the routes' ``lights``)."""
    return contract.hue_off_status(output, detail, asked)


# -- the Lights page's pieces (README S5.23) ---------------------------------------------------------------------------
def parse_follow(v: Any) -> Any:
    """One fixture's follow value, normalised: a contract.FOLLOW_WORDS word, or {"hex": "#rrggbb", "bri": 0..100}
    (``bri`` unset: FOLLOW_BRI_DEFAULT). ValueError otherwise."""
    if isinstance(v, str) and v in contract.FOLLOW_WORDS:
        return v
    if isinstance(v, Mapping) and set(v) <= {"hex", "bri"}:
        hx, bri = v.get("hex"), v.get("bri", FOLLOW_BRI_DEFAULT)
        n = _num(bri)
        if isinstance(hx, str) and _HEX.match(hx) and n is not None and 0.0 <= n <= 100.0:
            return {"hex": hx.lower(), "bri": int(n) if n == int(n) else round(n, 2)}
    raise ValueError(f"{v!r} isn't " + " or ".join(f'"{w}"' for w in contract.FOLLOW_WORDS)
                     + ' or {"hex": "#rrggbb", "bri": 0..100}')


def ping_once(ip: str) -> Optional[bool]:
    """One ICMP echo to an IPv4 address (``ping -c1 -W1``, no shell): True answered, False not; None when it can't be
    asked here (not an address, no ping)."""
    try:
        addr = str(ipaddress.IPv4Address(str(ip)))
    except ValueError:
        return None
    try:
        r = subprocess.run(["ping", "-c1", f"-W{PING_WAIT_S}", addr], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=PING_WAIT_S + 2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0


class _Online:
    """Thread ``show-rig-online``: every ``every_s``, one ``ping_fn`` per address ``ips_fn`` gives (the rig's node
    addresses), one after another; ``results`` (address -> answered) is swapped by reference after each round."""

    def __init__(self, ips_fn: Callable[[], Any], ping_fn: Callable[[str], Optional[bool]], every_s: float) -> None:
        self.ips_fn, self.ping_fn, self.every_s = ips_fn, ping_fn, max(0.05, float(every_s))
        self.results: dict[str, bool] = {}
        self.rounds = 0
        self._stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="show-rig-online", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ips = sorted({str(x) for x in self.ips_fn() or ()})
            except Exception:                                            # noqa: BLE001  the view moved under it
                ips = []
            got: dict[str, bool] = {}
            for ip in ips:
                if self._stop.is_set():
                    return
                try:
                    ok = self.ping_fn(ip)
                except Exception:                                        # noqa: BLE001
                    ok = None
                if isinstance(ok, bool):
                    got[ip] = ok
            self.results = got
            self.rounds += 1
            self._stop.wait(self.every_s)


# -- the runner (show-out-<id>) -----------------------------------------------------------------------------------------
class _Runner:
    """One output's deadline loop (module docstring). Owns its stats; reads the driver and engine by reference."""

    def __init__(self, oid: str, driver: Any, engine: Any, *, mono: Callable[[], float],
                 sleep: Callable[[float], None]) -> None:
        self.oid, self.driver, self.engine = oid, driver, engine
        self.mono, self.sleep = mono, sleep
        self.wake = threading.Event()
        self.hit = False                       # any thread: a music.hit for this output (set before wake)
        self.stopping = False
        self._plan_hit = getattr(driver, "plan_hit", None)
        self._on_hit = getattr(driver, "on_hit", None)
        self.frames = 0                        # set_frame calls
        self.hits = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self._run_errs: collections.deque = collections.deque(maxlen=RUNNER_ERRORS)
        self.backoff_until = 0.0
        self._log_next = 0.0
        self._late = [0.0] * RING
        self._late_n = 0
        self._sends = [-math.inf] * RING
        self._sends_n = 0
        self._eval = [0.0] * RING              # evaluate's wall time (s), GIL waits included
        self._eval_n = 0
        self.ahead_s = BUILD_AHEAD_S           # README S5.14: max(BUILD_AHEAD_S, evaluate p95 + the switch interval)
        self.last_frame: Optional[dict] = None   # the engine's Frame, swapped by reference (frame_view)
        self.last_frame_t: Optional[float] = None
        self.thread = threading.Thread(target=self._run, name=f"show-out-{oid}", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopping = True
        self.wake.set()

    def join(self, timeout: float = RUNNER_STOP_S) -> bool:
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)
        return not self.thread.is_alive()

    def _plan(self, t_last: float, now: float, active: bool) -> float:
        d = self.driver
        if self.hit:
            self.hit = False
            self.wake.clear()                          # this hit's wake is consumed here: the wait keeps the pull
            if (getattr(d, "caps", None) or {}).get("hits"):
                self.hits += 1
                if self._plan_hit is not None:
                    t = self._plan_hit(t_last, now)
                else:
                    if self._on_hit is not None:
                        self._on_hit()
                    t = d.plan(t_last, now, True)
                return self._sane(float(t), t_last, now)
        return self._sane(float(d.plan(t_last, now, active)), t_last, now)

    @staticmethod
    def _sane(t: float, t_last: float, now: float) -> float:
        if not math.isfinite(t) or t < now - REANCHOR_S:
            t = now                                    # a stall or a bad plan: from now, never a burst
        return max(t, t_last + MIN_STEP_S)             # never the same time twice: a runner can't spin

    def _run(self) -> None:
        mono, wake = self.mono, self.wake
        t_last = mono()
        active = False
        while not self.stopping:
            try:
                now = mono()
                if now < self.backoff_until:
                    wake.wait(min(WAIT_MAX_S, self.backoff_until - now))
                    wake.clear()
                    self.hit = False
                    continue
                t = self._plan(t_last, now, active)
                while not self.stopping:
                    left = t - self.ahead_s - mono()
                    if left <= 0.0:
                        break
                    if wake.wait(min(left, WAIT_MAX_S)):
                        wake.clear()
                        if self.stopping:
                            break
                        t = self._plan(t_last, mono(), active)     # a hit or a reconfigure: plan again
                if self.stopping:
                    break
                d = self.driver
                c0 = mono()
                frame = self.engine.evaluate(self.oid, t, d.caps)
                self._eval[self._eval_n % RING] = mono() - c0
                self._eval_n += 1
                if self._eval_n % BUILD_AHEAD_EVERY == 0:
                    self._set_ahead()
                left = t - mono()
                if left > 0.0:
                    self.sleep(left)
                d.set_frame(frame)
                sent = mono()
                self._late[self._late_n % RING] = (sent - t) * 1000.0
                self._late_n += 1
                self._sends[self._sends_n % RING] = sent
                self._sends_n += 1
                self.frames += 1
                self.last_frame, self.last_frame_t = frame, sent
                active = bool(frame.get("active"))
                t_last = t
                if self._run_errs:
                    self._run_errs.clear()
            except Exception as e:                             # noqa: BLE001  a bug here never takes the rig down
                self._failed(e)
                t_last = mono()
                active = False
                wake.wait(ERROR_PAUSE_S)
                wake.clear()

    def _set_ahead(self) -> None:
        """Build the frame this long before its send time: evaluate's own p95 (wall time, GIL waits included) plus
        one GIL switch interval for getting the GIL back after the wait, at least BUILD_AHEAD_S, at most
        BUILD_AHEAD_MAX_S. evaluate is still called for the planned time, so building earlier only moves a hit that
        lands in that window to the next frame (README S5.14)."""
        n = min(self._eval_n, RING)
        if not n:
            return
        p95 = sorted(self._eval[:n])[min(n - 1, int(0.95 * n))]
        self.ahead_s = min(BUILD_AHEAD_MAX_S, max(BUILD_AHEAD_S, p95 + sys.getswitchinterval()))

    def _failed(self, e: BaseException) -> None:
        self.errors += 1
        self.last_error = f"{type(e).__name__}: {e}"
        now = self.mono()
        self._run_errs.append(now)
        if len(self._run_errs) >= RUNNER_ERRORS and now - self._run_errs[0] <= RUNNER_ERROR_WINDOW_S:
            self.backoff_until = now + RUNNER_BACKOFF_S
            self._run_errs.clear()
        if now >= self._log_next:
            self._log_next = now + LOG_S
            log.error("rig: output %s: a frame failed (%d so far; logged at most every %.0f s)", self.oid,
                      self.errors, LOG_S, exc_info=(type(e), e, e.__traceback__))

    def stats(self) -> dict:
        now = self.mono()
        n = min(self._late_n, RING)
        sends = [t for t in self._sends if t > now - RATE_WINDOW_S]
        rate = 0.0
        if len(sends) >= 2:
            span = max(sends) - min(sends)
            rate = (len(sends) - 1) / span if span > 0 else 0.0
        return {"alive": self.thread.is_alive(), "frames": self.frames, "rate_hz": round(rate, 2),
                "build_ahead_ms": round(self.ahead_s * 1000.0, 2),
                "late_ms": _pcts(self._late[:n]), "errors": self.errors, "last_error": self.last_error,
                "hits": self.hits, "backoff_s": round(max(0.0, self.backoff_until - now), 2),
                "last_frame_age_s": None if self.last_frame_t is None else round(now - self.last_frame_t, 3)}


class _Out:
    """A running output: its driver and runner (show-brain swaps the table by reference)."""
    __slots__ = ("id", "kind", "driver", "runner", "config")

    def __init__(self, oid: str, kind: str, driver: Any, runner: Optional[_Runner], config: Any) -> None:
        self.id, self.kind, self.driver, self.runner, self.config = oid, kind, driver, runner, config


# -- the component --------------------------------------------------------------------------------------------------------
class Rig:
    """Component ``rig`` (contract.Component). README S5.4; module docstring."""

    name = "rig"
    tick_hz = contract.SLOW_TICK_HZ

    def __init__(self, ctx: contract.BrainContext, *, palette_fn: Optional[Callable] = None,
                 look_fn: Optional[Callable] = None, clock: Any = None, features_fn: Optional[Callable] = None,
                 neutral: Optional[tuple] = None, registry: Any = None, engine: Any = None,
                 driver_factories: Optional[Mapping[str, Callable[..., Any]]] = None,
                 driver_kw: Optional[Mapping[str, Mapping[str, Any]]] = None,
                 fixtures_path: Optional[Path] = None, types_dir: Optional[Path] = None,
                 latency_path: Optional[Path] = None, patch_path: Optional[Path] = None,
                 added_path: Optional[Path] = None, follow_file: Optional[Path] = None,
                 ping_fn: Optional[Callable[[str], Optional[bool]]] = None, online_check_s: float = ONLINE_CHECK_S,
                 mono: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.ctx = ctx
        self.clock = clock
        self.mono, self.sleep = mono, sleep
        self.modules: dict[str, str] = {}
        self.errors: dict[str, int] = {}
        self._log_next: dict[str, float] = {}
        # the registry
        self.registry = registry
        if registry is None:
            try:
                cls = _load(REGISTRY_MODULE)
                kw: dict[str, Any] = {}
                for k, v in (("types_dir", types_dir), ("latency_path", latency_path), ("patch_path", patch_path),
                             ("added_path", added_path)):
                    if v is not None:
                        kw[k] = Path(v)
                self.registry = cls(Path(fixtures_path) if fixtures_path is not None else contract.FIXTURES_FILE,
                                    **kw)
                self.modules["fixtures"] = "ok"
            except Exception as e:                                       # noqa: BLE001
                self.modules["fixtures"] = f"missing: {type(e).__name__}: {e}"
                log.error("rig: fixtures.py not loaded (%s): no registry, every output stays off", e)
        # the engine
        self.engine = engine
        if engine is None:
            try:
                cls = _load(ENGINE_MODULE)
                kw = {"palette_fn": palette_fn, "look_fn": look_fn, "clock": clock, "features_fn": features_fn,
                      "mono": mono}
                if neutral is not None:
                    kw["neutral"] = neutral
                self.engine = cls(**kw)
                self.modules["looks"] = "ok"
            except Exception as e:                                       # noqa: BLE001
                self.modules["looks"] = f"missing: {type(e).__name__}: {e}"
                log.error("rig: looks.py not loaded (%s): no output can run", e)
        self._factories: dict[str, Callable[..., Any]] = dict(driver_factories or {})
        self._driver_kw = {k: dict(v) for k, v in (driver_kw or {}).items()}
        self._shared: dict[str, Any] = {}
        # settings (show.yaml), on show-brain
        self.enabled = True
        self._rig: dict = {}
        self._looks: Optional[dict] = None
        self._lamps: Optional[dict] = None
        self._problems: list[str] = []
        self._engine_dirty = True
        self._settings_dirty = True
        self._started = False
        self._stopped = False
        # swapped by reference (any thread reads them)
        self._view: Optional[contract.RigView] = None
        self._outs: dict[str, _Out] = {}
        self._hit_runners: tuple[_Runner, ...] = ()
        self._why: dict[str, tuple[str, str]] = {}         # output -> (state, detail) for outputs with no driver
        self._snap: dict = {"enabled": True, "registry": None, "outputs": {}, "nodes": {}, "engine": None,
                            "errors": {}, "problems": [], "modules": dict(self.modules)}
        self._pending: set = set()                          # driver stop tasks still running (hue's PUT stop)
        self._saved: dict[str, dict] = {}                   # output -> its driver's saved state (restore, persist)
        self._hue_stop: Optional[dict] = None               # the hue driver last switched off: {"fut", "done_t"}
        takes = getattr(self.engine, "takes_hit", None)
        self._takes_hit = takes if callable(takes) else None
        self._takes_per_output = self._takes_hit is not None and _params(self._takes_hit) >= 2
        self.engine_problems: list[str] = []
        self.ticks = 0
        self.tick_ms: collections.deque = collections.deque(maxlen=120)
        # the Lights page (README S5.23)
        self._force_reload = False                          # an API write: the next registry poll reloads at once
        self.follow_file = Path(follow_file) if follow_file is not None else None   # None: kept in memory only
        self.follow_error: Optional[str] = None
        self._follow: dict[str, Any] = self._load_follow()  # fixture id -> FOLLOW_WORDS | {hex, bri}; swapped whole
        self._online = _Online(self._node_addresses, ping_fn or ping_once, online_check_s)

    # -- Component ----------------------------------------------------------------------------------------------------
    def provides(self) -> dict:
        return {"rig": self.view}

    def restore(self, saved: dict) -> None:
        """state.json's ``rig``: each output's driver state (hue: what room asked to follow), handed back when that
        output's driver is built (README S5.14). Everything else starts from config."""
        outs = saved.get("outputs") if isinstance(saved, dict) else None
        self._saved = {str(k): v for k, v in outs.items() if isinstance(v, dict)} if isinstance(outs, dict) else {}

    def persist(self) -> Optional[dict]:
        """``{"outputs": {output id: the driver's persist_state()}}``: running drivers' own, and the last state of
        outputs switched off since (so off and on again comes back as it was). None when there is nothing."""
        outs = dict(self._saved)
        for oid, o in list(self._outs.items()):
            st = self._driver_state(o.driver)
            if st is not None:
                outs[oid] = st
        return {"outputs": outs} if outs else None

    def _driver_state(self, d: Any) -> Optional[dict]:
        fn = getattr(d, "persist_state", None)
        got = self._guard("driver.persist_state", fn) if callable(fn) else None
        return got if isinstance(got, dict) else None

    def configure(self, settings: dict) -> None:
        """show.yaml ``rig``, ``looks`` and ``lamps`` (hot-reloaded)."""
        s = settings if isinstance(settings, dict) else {}
        probs: list[str] = []
        rig = s.get("rig")
        if rig is None:
            rig = {}
        elif not isinstance(rig, dict):
            probs.append("rig: not a mapping: the starting points are used")
            rig = {}
        en = rig.get("enabled", True)
        if not isinstance(en, bool):
            probs.append(f"rig.enabled: {en!r} isn't true or false: the rig is off (the rollback)")
            en = False
        looks = s.get("looks") if isinstance(s.get("looks"), dict) else None
        lamps = s.get("lamps") if isinstance(s.get("lamps"), dict) else None
        if looks != self._looks or lamps != self._lamps:
            self._engine_dirty = True
        if rig != self._rig:
            self._settings_dirty = True
        self.enabled, self._rig, self._looks, self._lamps = en, rig, looks, lamps
        self._problems = probs
        if self._started and not self._stopped:
            self._sync(self.mono())

    def start(self) -> None:
        self._started = True
        self._sync(self.mono())
        self._online.start()

    def tick(self, now_s: float) -> None:
        t0 = time.perf_counter()
        self._sync(now_s)
        self.ticks += 1
        self.tick_ms.append((time.perf_counter() - t0) * 1000.0)

    def stop(self) -> Any:
        """Runners, then drivers (module docstring). Returns an awaitable when a driver's stop is still running on
        show-brain (hue's PUT stop), which brain.py awaits."""
        self._stopped = True
        self._online.stop()
        outs = list(self._outs.values())
        for o in outs:                                     # the final state.json keeps what each driver had
            st = self._driver_state(o.driver)
            if st is not None:
                self._saved[o.id] = st
        self._hit_runners = ()
        for o in outs:
            if o.runner is not None:
                o.runner.stop()
        for o in outs:
            if o.runner is not None and not o.runner.join(RUNNER_STOP_S):
                log.warning("rig: output %s's runner didn't stop within %.1f s", o.id, RUNNER_STOP_S)
        aws = []
        for o in outs:
            got = self._guard("driver.stop", o.driver.stop)
            if inspect.isawaitable(got):
                aws.append(got)
        aws += [t for t in self._pending if not t.done()]
        self._outs = {}
        if not aws:
            self._close_nodes()
            return None

        async def _finish() -> None:
            try:
                await asyncio.gather(*aws, return_exceptions=True)
            finally:
                self._close_nodes()
        return _finish()

    def snapshot(self) -> dict:
        return self._snap

    # -- any thread ---------------------------------------------------------------------------------------------------
    def view(self) -> Optional[contract.RigView]:
        """ctx.rig: the current registry view; None while the rig is off (show.yaml rig.enabled) or not loaded."""
        return self._view

    def set_look(self, *, hue_depth: Optional[float] = None, beat_lock: Optional[bool] = None,
                 reset: bool = False, beat_depth: Optional[float] = None) -> None:
        """artnet_out's set_look signature, plus ``beat_depth`` (ctx.set_look_out fans out to it): to the look
        engine."""
        e = self.engine
        if e is not None:
            if beat_depth is None:
                e.set_look(hue_depth=hue_depth, beat_lock=beat_lock, reset=reset)
            else:
                e.set_look(hue_depth=hue_depth, beat_lock=beat_lock, reset=reset, beat_depth=beat_depth)

    def on_hit(self, d: Mapping[str, Any]) -> None:
        """A music.hit body (show-rx): the engine's ring, then wake each hits-capable runner the engine would flash
        it on now (``engine.takes_hit(d, output)``: the look, and that output's fixtures in the hits selection), a
        frame pulled forward. A hit the look or an output doesn't show never knocks that output off its beat grid."""
        e = self.engine
        if e is None:
            return
        e.on_hit(d)
        runners = self._hit_runners
        if not runners:
            return
        takes = self._takes_hit
        if takes is not None and not self._takes_per_output and not takes(d):
            return
        for r in runners:
            if takes is not None and self._takes_per_output and not takes(d, r.oid):
                continue
            r.hit = True
            r.wake.set()

    def drivers_of(self, kind: str) -> list[Any]:
        return [o.driver for _, o in sorted(self._outs.items()) if o.kind == kind]

    def frame_view(self, output_id: str) -> Optional[dict]:
        """GET /api/v1/rig/frame's body; None for an output the registry doesn't have."""
        v = self._view
        if v is None or output_id not in (v.get("outputs") or {}):
            return None
        o = self._outs.get(output_id)
        r = o.runner if o is not None else None
        frame = r.last_frame if r is not None else None
        t = r.last_frame_t if r is not None else None
        fixtures: dict[str, dict] = {}
        if isinstance(frame, dict):
            for fid, ff in (frame.get("fixtures") or {}).items():
                rgb = [round(float(c), 4) for c in ff.get("rgb") or (0.0, 0.0, 0.0)]
                dmx = ff.get("dmx")
                fixtures[fid] = {"intensity": round(float(ff.get("intensity", 0.0)), 4),
                                 "hex": "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in rgb),
                                 "rgb": rgb, "dmx": list(dmx) if dmx is not None else None,
                                 "t_eval_ms": round(float(ff.get("t_eval_ms", 0.0)), 1)}
        uv = getattr(o.driver, "universe_view", None) if o is not None else None
        universe = self._guard("universe_view", uv) if callable(uv) else None
        return {"output": output_id, "seq": frame.get("seq") if isinstance(frame, dict) else None,
                "t_send_ms": round(frame["t_send_ms"], 1) if isinstance(frame, dict) else None,
                "age_s": None if t is None else round(self.mono() - t, 3),
                "active": bool(frame.get("active")) if isinstance(frame, dict) else False,
                "why": frame.get("why") if isinstance(frame, dict) else self._why_of(output_id),
                "fixtures": fixtures, "universe": universe}

    def dmx_view(self) -> dict:
        """The virtual nodes' universes, decoded from the packets the drivers kept (previz)."""
        n = self._shared.get(NODES_KEY)
        fn = getattr(n, "dmx_view", None)
        got = self._guard("dmx_view", fn) if callable(fn) else None
        return got if isinstance(got, dict) else {}

    def has_node(self, node_id: str) -> bool:
        v = self._view
        return v is not None and node_id in (v.get("nodes") or {})

    def record_failsafe(self, node_id: str) -> dict:
        """show-brain (POST /api/v1/rig/nodes/{id}/record-failsafe): the node's universes resent, then AcFailRecord."""
        if not self.has_node(node_id):
            return {"recorded": False, "why": f"unknown node {node_id}"}
        n = self._shared.get(NODES_KEY)
        if n is None:
            return {"recorded": False, "why": "no enabled Art-Net output uses this node"}
        got = n.record_failsafe(node_id)
        return got if isinstance(got, dict) else {"recorded": False, "why": "no answer"}

    # -- Hue (the routes: request/release on show-brain, status from any thread) ---------------------------------------
    def _hue(self) -> Any:
        hs = self.drivers_of("hue")
        return hs[0] if hs else None

    def _hue_output_id(self) -> Optional[str]:
        v = self._view
        ids = sorted(oid for oid, oc in ((v or {}).get("outputs") or {}).items()
                     if isinstance(oc, dict) and oc.get("driver") == "hue")
        return ids[0] if ids else None

    def hue_off_detail(self) -> str:
        if not self.enabled:
            return "show.yaml rig.enabled is false: the rig is off"
        oid = self._hue_output_id()
        if oid is None:
            return f"no hue output in {FIXTURES_FILE_WORDS}"
        st = self._why.get(oid)
        if st is not None and st[0] == "not_built":
            return st[1]
        return f"outputs.{oid}.enabled is false in {FIXTURES_FILE_WORDS}"

    def hue_status(self) -> contract.HueStatus:
        d = self._hue()
        if d is None:
            oid = self._hue_output_id()
            return hue_off(oid, self.hue_off_detail(), self._saved_asked(oid))
        return d.hue_status()

    def hue_request(self, lights: Any, on: Any = True) -> contract.HueStatus:
        if not isinstance(lights, list) or not all(isinstance(x, str) for x in lights):
            raise ValueError("bad_request: lights must be a list of fixture ids")
        if not isinstance(on, bool):
            raise ValueError("bad_request: on must be true or false")
        d = self._hue()
        if d is None:
            self._keep_asked(list(dict.fromkeys(lights)) if on else [])
            return self.hue_status()
        return d.request(lights, on)

    def _saved_asked(self, oid: Optional[str]) -> list:
        """What room asked the hue output to follow while no driver runs (the routes' ``lights``): the saved set."""
        saved = self._saved.get(oid) if oid is not None else None
        asked = saved.get("asked") if isinstance(saved, dict) else None
        return [x for x in asked if isinstance(x, str)] if isinstance(asked, list) else []

    def hue_release(self, lights: Any = None) -> Any:
        """The hue driver's release (a coroutine, awaited by brain.call). With no hue output running: a coroutine
        that waits out a stop still settling (the output was just switched off), else None (the caller answers as
        Stage 3 did: released at once, nothing streams from here)."""
        if lights is not None and (not isinstance(lights, list) or not all(isinstance(x, str) for x in lights)):
            raise ValueError("bad_request: lights must be a list of fixture ids")
        d = self._hue()
        if d is not None:
            return d.release(lights)
        oid = self._hue_output_id()
        saved = self._saved.get(oid) if oid is not None else None
        if isinstance(saved, dict) and isinstance(saved.get("asked"), list):
            gone = None if lights is None else set(lights)
            self._keep_asked([] if gone is None else [a for a in saved["asked"] if a not in gone])
        if self._hue_settling():
            return self._released_after_stop()
        return None

    def hue_release_timeout_s(self) -> float:
        v = _num((self._rig.get("hue") or {}).get("release_timeout_s")) if isinstance(self._rig.get("hue"),
                                                                                       dict) else None
        return min(v if v is not None and v > 0 else HUE_RELEASE_TIMEOUT_S, contract.HUE_RELEASE_MAX_S)

    def hue_running(self) -> bool:
        """A hue driver runs here, or one just switched off is still giving the lamps back (hue_release waits)."""
        return self._hue() is not None or self._hue_settling()

    def _hue_settle_s(self) -> float:
        v = _num((self._rig.get("hue") or {}).get("release_settle_s")) if isinstance(self._rig.get("hue"),
                                                                                      dict) else None
        return v if v is not None and v >= 0 else HUE_SETTLE_S

    def _keep_asked(self, asked: list) -> None:
        """No hue driver runs: what room asks is kept for when the output is on again (restore_state)."""
        oid = self._hue_output_id()
        if oid is None:
            return
        st = dict(self._saved.get(oid) or {})
        if st.get("asked") == asked:
            return
        st["asked"] = asked
        self._saved[oid] = st
        self._guard("ctx.persist", getattr(self.ctx, "persist", None))

    def _note_hue_stop(self, fut: Any) -> None:
        rec: dict = {"fut": fut, "done_t": None}
        if fut is None or fut.done():
            rec["done_t"] = self.mono()
        else:
            fut.add_done_callback(lambda _f: rec.__setitem__("done_t", self.mono()))
        self._hue_stop = rec

    def _hue_settling(self) -> bool:
        r = self._hue_stop
        if r is None:
            return False
        return r["done_t"] is None or self.mono() - r["done_t"] < self._hue_settle_s()

    async def _released_after_stop(self) -> contract.HueRelease:
        """The hue output was just switched off: released once its stop has finished (the PUT stop, or the bridge
        unreachable: it ends an unfed area by itself) and ``release_settle_s`` has passed since."""
        t0 = self.mono()
        r = self._hue_stop or {"fut": None, "done_t": t0}
        fut = r["fut"]
        released, why = True, None
        if fut is not None and not fut.done():
            try:
                await asyncio.wait_for(asyncio.shield(fut), self.hue_release_timeout_s())
            except (asyncio.TimeoutError, TimeoutError):
                released, why = False, "timeout"
            except Exception:                                            # noqa: BLE001  the stop logs its own
                pass
        if released:
            done = r["done_t"] if r["done_t"] is not None else self.mono()
            wait = done + self._hue_settle_s() - self.mono()
            if wait > 0:
                await asyncio.sleep(wait)
        return {"released": released, "state": contract.hue_room_state(self.hue_status()), "why": why,
                "still_following": [], "waited_ms": round((self.mono() - t0) * 1000.0, 1), "via": "show"}

    # -- the Lights page (README S5.23) -------------------------------------------------------------------------------
    @staticmethod
    def _fixtures_module() -> Any:
        try:
            from . import fixtures as fx
        except Exception:                                                # noqa: BLE001  degrade: no labels
            return None
        return fx

    def _registrar(self) -> Any:
        """fixtures.Registrar over the registry's own files (show.yaml's looks and lamps for its check)."""
        fx = self._fixtures_module()
        reg = self.registry
        paths = [getattr(reg, k, None) for k in ("path", "added_path", "types_dir")]
        if fx is None or any(p is None for p in paths):
            raise ValueError("no_registry: The fixture registry isn't loaded here, so lights can't be added.")
        return fx.Registrar(paths[0], added_path=paths[1], types_dir=paths[2], looks=self._looks, lamps=self._lamps)

    def _reload_now(self) -> None:
        """After an API write (show-brain): reload the registry and reconcile now, so an output made for a new light
        starts here and a removed one stops."""
        self._force_reload = True
        self._sync(self.mono())

    def _row_after(self, fid: str, candidate: Any) -> Optional[dict]:
        """The written light's row: from the rig's view once it has it, else from the Registrar's check (the rig
        off)."""
        v = self._view
        if v is not None and fid in (v.get("fixtures") or {}):
            rows = self.fixture_rows()
        else:
            rows = self._rows(candidate, frozenset((fid,)))
        return next((r for r in rows if r["id"] == fid), None)

    def add_fixture(self, body: Any) -> dict:
        """POST /api/v1/rig/fixtures (show-brain): ``{fixture: RigFixtureRow, problems}``. fixtures.Refused (a
        ValueError with ``code``) when it can't be added as asked: nothing is written."""
        fid, probs, cand = self._registrar().add(body)
        self._reload_now()
        return {"fixture": self._row_after(fid, cand), "problems": probs}

    def update_fixture(self, fid: str, body: Any) -> dict:
        """PUT /api/v1/rig/fixtures/{id} (show-brain): the same keys, each optional."""
        fid, probs, cand = self._registrar().update(fid, body)
        self._reload_now()
        return {"fixture": self._row_after(fid, cand), "problems": probs}

    def remove_fixture(self, fid: str) -> dict:
        """DELETE /api/v1/rig/fixtures/{id} (show-brain): ``{removed: id, outputs, nodes, types}``; its follow entry
        goes too."""
        got = self._registrar().remove(fid)
        if fid in self._follow:
            self._follow = {k: v for k, v in self._follow.items() if k != fid}
            self._save_follow()
            self._apply_follow()
        self._reload_now()
        return got

    def types_view(self) -> list[dict]:
        """GET /api/v1/rig/types (any thread): fixtures.type_rows over the current view."""
        fx = self._fixtures_module()
        return fx.type_rows(self._view) if fx is not None else []

    def identify(self, fid: str, seconds: Any = None) -> dict:
        """POST /api/v1/rig/fixtures/{id}/identify (show-brain): the fixture shows full white for ``seconds``
        (IDENTIFY_S unset, at most IDENTIFY_MAX_S) whatever it was doing, through its running output: ``{ok: true,
        seconds}``, else ``{ok: false, why}`` in page words."""
        s = IDENTIFY_S if seconds is None else _num(seconds)
        if s is None or s <= 0:
            raise ValueError("bad_request: seconds must be a number above 0")
        s = min(s, IDENTIFY_MAX_S)
        v = self._view
        f = ((v or {}).get("fixtures") or {}).get(fid)
        if f is None:
            return {"ok": False, "why": f"There's no light {fid!r} in the rig." if v is not None else
                    "The rig is off (show.yaml rig.enabled), so nothing can flash."}
        if f.get("kind") == "screen":
            return {"ok": False, "why": "A screen isn't flashed from here."}
        oid = f.get("output")
        o = self._outs.get(oid) if oid else None
        if o is None:
            why = self._why.get(oid) if oid else None
            return {"ok": False, "why": f"Its output isn't running: {why[1]}" if why and why[1] else
                    "It isn't on an output that runs."}
        hs = getattr(o.driver, "hue_status", None)                       # a streamed lamp: only while it follows
        st = self._guard("driver.hue_status", hs) if callable(hs) else None
        if isinstance(st, dict) and fid not in (st.get("wanted") or []):
            return {"ok": False, "why": "This lamp isn't following the music from here right now, so it can't flash."}
        fn = getattr(self.engine, "identify", None)
        if not callable(fn):
            return {"ok": False, "why": "The look engine can't flash a light yet."}
        try:
            got = fn(fid, s)
        except Exception as e:                                           # noqa: BLE001
            log.exception("rig: identify %s failed", fid)
            return {"ok": False, "why": f"The flash didn't start ({type(e).__name__})."}
        if o.runner is not None:
            o.runner.wake.set()
        if got is False:                                                 # kept by the engine, not evaluated yet
            return {"ok": False, "why": "The look engine doesn't drive this light yet; it flashes once it does."}
        return {"ok": True, "seconds": s}

    def follow_view(self) -> dict:
        """GET /api/v1/rig/follow (any thread): ``{fixtures: {id: value}}``."""
        return {"fixtures": dict(self._follow)}

    def set_follow(self, fixtures: Any) -> dict:
        """POST /api/v1/rig/follow (show-brain): ``fixtures`` {id: "dmx" | "off" | {hex, bri} | null (forget it)} is
        merged into the mapping (all or nothing: one bad value is a ValueError and nothing changes), saved, and handed
        whole to the look engine; every runner re-plans. ``{accepted: true, fixtures: the whole mapping, unknown: ids
        not in the registry (kept), saved}``."""
        if not isinstance(fixtures, Mapping) or not all(isinstance(k, str) for k in fixtures):
            raise ValueError('bad_request: fixtures must be {<fixture id>: "dmx" | "off" | {"hex": "#rrggbb", '
                             '"bri": 0..100}}')
        new = dict(self._follow)
        for fid, val in fixtures.items():
            if val is None:
                new.pop(fid, None)
                continue
            try:
                new[fid] = parse_follow(val)
            except ValueError as e:
                raise ValueError(f"bad_follow: {fid}: {e}") from None
        if new != self._follow:
            self._follow = new
            self._save_follow()
        self._apply_follow()
        known = (self._view or {}).get("fixtures") or {}
        return {"accepted": True, "fixtures": dict(self._follow), "unknown": sorted(k for k in fixtures
                                                                                     if k not in known),
                "saved": self.follow_file is not None and self.follow_error is None}

    def _apply_follow(self) -> None:
        """The whole mapping to the look engine (``LookEngine.set_follow``, when it has it), then every runner wakes."""
        fn = getattr(self.engine, "set_follow", None)
        if callable(fn):
            self._guard("engine.set_follow", fn, dict(self._follow))
        for o in list(self._outs.values()):
            if o.runner is not None:
                o.runner.wake.set()

    def _load_follow(self) -> dict[str, Any]:
        p = self.follow_file
        if p is None:
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            self.follow_error = f"{p.name} not read ({e}): nobody follows until room says again"
            log.warning("rig: %s", self.follow_error)
            return {}
        got = raw.get("fixtures") if isinstance(raw, dict) else None
        out: dict[str, Any] = {}
        for fid, val in (got.items() if isinstance(got, dict) else ()):
            try:
                out[str(fid)] = parse_follow(val)
            except ValueError:
                log.warning("rig: %s: %s's %r isn't a follow value: dropped", p.name, fid, val)
        return out

    def _save_follow(self) -> None:
        """follow.json, atomically (tmp, fsync, os.replace). A failure keeps the mapping in memory."""
        p = self.follow_file
        if p is None:
            return
        try:
            p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "fixtures": self._follow}, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
            self.follow_error = None
        except OSError as e:
            self.follow_error = f"{p.name} not written ({e}): who follows holds until a restart"
            log.warning("rig: %s", self.follow_error)

    def _node_addresses(self) -> list[str]:
        """show-rig-online: every node address the view sends to (discovery's, else last_ip; virtual ones none)."""
        v = self._view
        n = self._shared.get(NODES_KEY)
        return [ip for nid, nc in ((v or {}).get("nodes") or {}).items()
                if (ip := self._node_ip(nid, nc, n)) is not None]

    def _node_ip(self, nid: Any, nc: Any, nodes: Any) -> Optional[str]:
        if not isinstance(nc, Mapping) or nc.get("virtual") is True:
            return None
        dest = getattr(nodes, "dest", None) if nodes is not None else None
        d = self._guard("nodes.dest", dest, nid) if callable(dest) else None
        if isinstance(d, tuple) and d and isinstance(d[0], str):
            return d[0]
        ip = nc.get("last_ip")
        return ip if isinstance(ip, str) and ip else None

    def fixture_rows(self) -> list[dict]:
        """GET /api/v1/rig ``fixtures`` (any thread): contract.RigFixtureRow per fixture of the current view."""
        return self._rows(self._view, getattr(self.registry, "added_ids", None) or frozenset())

    def _rows(self, v: Any, editable: Any) -> list[dict]:
        if not isinstance(v, Mapping):
            return []
        fx = self._fixtures_module()
        outs, nodes, types = (v.get(k) or {} for k in ("outputs", "nodes", "types"))
        n = self._shared.get(NODES_KEY)
        follow, online = self._follow, self._online.results
        rows = []
        for fid, f in (v.get("fixtures") or {}).items():
            if not isinstance(f, Mapping):
                continue
            oid = f.get("output")
            oc = outs.get(oid) if oid else None
            oc = oc if isinstance(oc, Mapping) else {}
            nid = oc.get("node")
            ip = self._node_ip(nid, nodes.get(nid), n) if nid else None
            t = types.get(f.get("type"))
            t = t if isinstance(t, Mapping) else {}
            role = f.get("role")
            if fx is not None:
                role = fx.role_of(dict(f), self._lamps)
            rows.append({"id": fid, "label": f.get("label") or fid, "kind": f.get("kind"), "type": f.get("type"),
                         "type_label": fx.type_label(dict(t)) if fx is not None and t else None, "output": oid,
                         "driver": oc.get("driver"), "node_ip": ip, "universe": oc.get("port_address") if nid else None,
                         "address": f.get("address"), "footprint": int(t.get("footprint") or 0),
                         "placement": f.get("placement"), "role": role, "zone": f.get("zone"),
                         "follow": follow.get(fid), "online": online.get(ip) if ip else None,
                         "last_rgb": self._last_hex(oid, fid), "editable": fid in editable})
        return rows

    def _last_hex(self, oid: Any, fid: str) -> Optional[str]:
        o = self._outs.get(oid) if oid else None
        frame = o.runner.last_frame if o is not None and o.runner is not None else None
        ff = (frame.get("fixtures") or {}).get(fid) if isinstance(frame, dict) else None
        rgb = ff.get("rgb") if isinstance(ff, dict) else None
        if not isinstance(rgb, (list, tuple)) or len(rgb) != 3 or any(_num(c) is None for c in rgb):
            return None
        return "#%02x%02x%02x" % tuple(max(0, min(255, round(float(c) * 255))) for c in rgb)

    # -- show-brain ---------------------------------------------------------------------------------------------------
    def _guard(self, site: str, fn: Optional[Callable], *a: Any, **kw: Any) -> Any:
        if fn is None:
            return None
        try:
            return fn(*a, **kw)
        except Exception as e:                                           # noqa: BLE001
            self.errors[site] = self.errors.get(site, 0) + 1
            now = time.monotonic()
            if now >= self._log_next.get(site, 0.0):
                self._log_next[site] = now + LOG_S
                log.error("rig: %s failed (%d so far; logged at most every %.0f s)", site, self.errors[site], LOG_S,
                          exc_info=(type(e), e, e.__traceback__))
            return None

    def _sync(self, now_s: float) -> None:
        """Poll the registry, recompile the engine on a change, reconcile the outputs, tick the drivers, and build the
        snapshot."""
        reg = self.registry
        if not self.enabled or reg is None:
            if self._outs:
                self._stop_all("show.yaml rig.enabled is false" if not self.enabled else "no registry")
            self._view = None
            self._why = {}
            self._build_snapshot(now_s)
            return
        poll_kw: dict[str, Any] = {"lamps": self._lamps}
        if self._force_reload:                               # an API write replaced fixtures.added.yaml whole
            poll_kw["force"] = True
            self._force_reload = False
        changed = bool(self._guard("registry.poll", reg.poll, self._looks, **poll_kw))
        view = reg.view()
        was = self._view
        self._view = view
        if view is not None and self.engine is not None and (changed or self._engine_dirty or view is not was):
            got = self._guard("engine.configure", self.engine.configure, self._looks, view, lamps=self._lamps)
            if got is not None:
                self.engine_problems = list(got)
                self._engine_dirty = False
            self._apply_follow()                             # README S5.23: the follow mapping, after every configure
        if view is not None:
            self._reconcile(view, reconfigure=changed or view is not was or self._settings_dirty)
        self._settings_dirty = False
        for o in list(self._outs.values()):
            self._guard("driver.tick", getattr(o.driver, "tick", None), now_s)
        self._pending = {t for t in self._pending if not t.done()}
        self._build_snapshot(now_s)

    def _make_driver(self, kind: str, oid: str) -> tuple[Any, Optional[str]]:
        fac = self._factories.get(kind)
        if fac is None:
            spec = DRIVER_MODULES.get(kind)
            if spec is None:
                return None, f"the {kind} driver isn't built in Stage 5"
            try:
                fac = _load(spec)
                self.modules[spec.split(":")[0].rsplit(".", 1)[-1]] = "ok"
            except Exception as e:                                       # noqa: BLE001
                why = f"the {kind} driver didn't load ({type(e).__name__}: {e})"
                self.modules[spec.split(":")[0].rsplit(".", 1)[-1]] = f"missing: {type(e).__name__}: {e}"
                return None, why
        kw = dict(self._driver_kw.get(kind, {}))
        try:
            d = fac(oid, ctx=self.ctx, clock=self.clock, settings=(self._rig.get(kind) if isinstance(
                self._rig.get(kind), dict) else None), shared=self._shared, mono=self.mono, **kw)
        except Exception as e:                                           # noqa: BLE001
            log.exception("rig: the %s driver for output %s couldn't be built", kind, oid)
            return None, f"the {kind} driver couldn't be built ({type(e).__name__}: {e})"
        return d, None

    def _reconcile(self, view: contract.RigView, *, reconfigure: bool) -> None:
        outs = dict(self._outs)
        why: dict[str, tuple[str, str]] = {}
        cfgs = {oid: oc for oid, oc in (view.get("outputs") or {}).items() if isinstance(oc, dict)}
        want: dict[str, dict] = {}
        for oid, oc in sorted(cfgs.items()):
            kind = str(oc.get("driver"))
            if oc.get("enabled") is not True:
                why[oid] = ("off", f"outputs.{oid}.enabled is false in {FIXTURES_FILE_WORDS}")
            elif kind not in contract.DRIVERS_BUILT or (kind not in self._factories and kind not in DRIVER_MODULES):
                why[oid] = ("not_built", f"the {kind} driver isn't built in Stage 5")
            elif self.engine is None:
                why[oid] = ("not_built", f"the look engine isn't loaded ({self.modules.get('looks')})")
            else:
                want[oid] = oc
        hues = [oid for oid, oc in want.items() if oc.get("driver") == "hue"]
        probs = [f"outputs: {len(hues)} hue outputs are enabled ({', '.join(hues)}): the Hue routes use {hues[0]} "
                 "(one hue output in Stage 5)"] if len(hues) > 1 else []
        # stop what is no longer wanted, or whose kind changed
        for oid in list(outs):
            oc = want.get(oid)
            if oc is None or str(oc.get("driver")) != outs[oid].kind:
                self._stop_out(outs.pop(oid))
        # build, configure and start
        for oid, oc in want.items():
            kind = str(oc.get("driver"))
            o = outs.get(oid)
            if o is None:
                d, err = self._make_driver(kind, oid)
                if d is None:
                    why[oid] = ("not_built", err or "not built")
                    continue
                self._guard("driver.configure", d.configure, oc, view)
                self._guard("driver.start", d.start)
                saved = self._saved.pop(oid, None)
                rs = getattr(d, "restore_state", None)
                if saved is not None and callable(rs):
                    self._guard("driver.restore_state", rs, saved)      # hue: what room asked, before the restart
                r = _Runner(oid, d, self.engine, mono=self.mono, sleep=self.sleep)
                outs[oid] = _Out(oid, kind, d, r, oc)
                r.start()
                log.info("rig: output %s (%s) on", oid, kind)
            elif reconfigure or o.config is not oc:
                if self._settings_dirty:
                    ss = getattr(o.driver, "set_settings", None)
                    if callable(ss):
                        self._guard("driver.set_settings", ss, self._rig.get(kind))
                self._guard("driver.configure", o.driver.configure, oc, view)
                o.config = oc
                if o.runner is not None:
                    o.runner.wake.set()                                  # re-plan at once (hue: idle -> 50 Hz)
        self._outs = outs
        self._why = why
        self._hit_runners = tuple(o.runner for o in outs.values()
                                  if o.runner is not None and (getattr(o.driver, "caps", None) or {}).get("hits"))
        self._problems = [p for p in self._problems if not p.startswith("outputs: ")] + probs

    def _stop_out(self, o: _Out) -> None:
        st = self._driver_state(o.driver)
        if st is not None:
            self._saved[o.id] = st                     # switched off: kept, so on again comes back as it was
        if o.runner is not None:
            o.runner.stop()
            o.runner.join(RUNNER_STOP_S)
        got = self._guard("driver.stop", o.driver.stop)
        fut = None
        if inspect.isawaitable(got):
            if isinstance(got, asyncio.Future) or asyncio.iscoroutine(got):
                fut = got if isinstance(got, asyncio.Future) else self._spawn(got)
                if fut is not None:
                    self._pending.add(fut)
        if o.kind == "hue":
            self._note_hue_stop(fut)                   # a release in the next moments waits for it (hue_release)
        log.info("rig: output %s (%s) off", o.id, o.kind)

    def _spawn(self, coro: Any) -> Any:
        loop = getattr(self.ctx, "loop", None)
        if loop is None or loop.is_closed():
            coro.close()
            return None
        try:
            return loop.create_task(coro)
        except RuntimeError:
            coro.close()
            return None

    def _stop_all(self, why: str) -> None:
        outs, self._outs = self._outs, {}
        self._hit_runners = ()
        for o in outs.values():
            self._stop_out(o)
        log.info("rig: every output stopped (%s)", why)

    def _close_nodes(self) -> None:
        n = self._shared.get(NODES_KEY)
        if n is not None:
            self._guard("nodes.close", getattr(n, "close", None))

    def _why_of(self, oid: str) -> Optional[str]:
        w = self._why.get(oid)
        return w[0] if w else None

    def _out_status(self, oid: str, oc: Mapping[str, Any]) -> dict:
        kind = str(oc.get("driver"))
        if not self.enabled:
            return off_status(oid, kind, "off", "show.yaml rig.enabled is false")
        o = self._outs.get(oid)
        if o is None:
            st, detail = self._why.get(oid, ("off", None))
            return off_status(oid, kind, st, detail, enabled=oc.get("enabled") is True)
        s = self._guard("driver.status", o.driver.status)
        s = dict(s) if isinstance(s, dict) else off_status(oid, kind, "error", "status failed", True)
        r = o.runner
        if r is not None:
            rs = r.stats()
            s["extra"] = {**(s.get("extra") or {}), "runner": rs}
            if rs["backoff_s"] > 0:
                s["state"], s["detail"] = "error", f"frames failed ({r.last_error}): retried in {rs['backoff_s']:g} s"
            elif not rs["alive"] and not self._stopped:
                s["state"], s["detail"] = "error", "its runner stopped"
        return s

    def snapshot_now(self) -> dict:
        """The snapshot, built now (any thread: GET /api/v1/rig)."""
        return self._make_snapshot()

    def _build_snapshot(self, now_s: float) -> None:
        self._snap = self._make_snapshot()

    def _make_snapshot(self) -> dict:
        v = self._view
        summary = None
        if v is not None:
            try:
                from . import fixtures as _fx                            # loaded already when there is a view
                summary = _fx.summary(v)
            except Exception:                                            # noqa: BLE001
                summary = {"revision": v.get("revision"), "path": v.get("path"), "error": v.get("error"),
                           "problems": list(v.get("problems") or []), "counts": {}}
        outputs = {oid: self._out_status(oid, oc) for oid, oc in sorted(((v or {}).get("outputs") or {}).items())
                   if isinstance(oc, dict)}
        n = self._shared.get(NODES_KEY)
        nodes: dict = {}
        if v is not None:
            ns = self._guard("nodes.snapshot", getattr(n, "snapshot", None)) if n is not None else None
            got = (ns or {}).get("nodes") if isinstance(ns, dict) else None
            for nid in sorted((v.get("nodes") or {})):
                nodes[nid] = (got or {}).get(nid) or {
                    "id": nid, "ip": None, "ip_from": "none", "matched": None, "replies": 0, "reply_age_s": None,
                    "short_name": None, "long_name": None, "mac": None, "refresh_hz": None, "failsafe": None,
                    "merging": None, "detail": "not polled: no enabled output uses it"}
        eng = self._guard("engine.stats", getattr(self.engine, "stats", None)) if self.engine is not None else None
        tick = sorted(self.tick_ms)
        return {"enabled": self.enabled, "registry": summary, "outputs": outputs, "nodes": nodes, "engine": eng,
                "errors": dict(self.errors), "problems": list(self._problems), "modules": dict(self.modules),
                "tick_ms_p95": round(tick[min(len(tick) - 1, int(0.95 * len(tick)))], 3) if tick else None,
                "hue": dict(self.hue_status()),
                # README S5.23: the Lights page's rows, and where who-follows is kept
                "fixtures": self.fixture_rows(),
                "follow": {"file": str(self.follow_file) if self.follow_file is not None else None,
                           "error": self.follow_error, "count": len(self._follow)},
                "online": {"rounds": self._online.rounds, "every_s": self._online.every_s}}


def attach(ctx: contract.BrainContext, **kw: Any) -> Rig:
    """The brain's ``rig`` (README S5.4). __main__ passes ``palette_fn`` (the director's), ``look_fn``, ``clock``,
    ``features_fn``, ``neutral`` (artnet_out's colour and depth), the registry's paths, ``driver_kw`` and
    ``follow_file`` (README S5.23); tests pass fakes (``registry``, ``engine``, ``driver_factories``, ``ping_fn``)."""
    return Rig(ctx, **kw)
