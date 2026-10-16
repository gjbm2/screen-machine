"""hue_out.py: Hue Entertainment streaming from show_service (README "Stage 5", S5.6; SHOW_ENGINE §9.4 P5, §5.4).

One ``HueOut`` per ``hue`` output in config/room/fixtures.yaml (contract.Driver, kind "hue"). It streams the lamps that
room's hue adapter asks for (``POST /api/v1/hue/stream``) to the bridge's Entertainment area, from the look engine's
frames, following room_service/hue/output.py's rules wherever they apply (68's hand-over contract, 16 Oct).

**Off by default.** Nothing talks to a bridge unless the output is ``enabled: true`` in fixtures.yaml **and** 68's
patch.yaml says ``hue.streamer: show`` **and** room has asked for at least one lamp **and** the look engine's frames
are active. Any one missing: the area is never started (or is stopped, if we hold it).

**Imported from room_service, not copied** (68's contract): ``room_service.hue.stream`` (``xy_bri``, ``xy_streamer``,
``GAMMA``, ``COLOR_SPACE_BYTE``), ``room_service.hue.client`` (``BridgeSecrets``, ``HueClient``, ``HueError``,
``resolve_area``) and ``room_service.hue.colour`` (``rgb_to_xy``). They need only the stdlib, aiohttp and
hue-entertainment 0.1.2 (all pinned in show_service/requirements.txt); importing them loads nothing else of
room_service. So both streamers encode, resolve and pair exactly alike.
- The pairing is room's, read-only, loaded as room's DmxStack loads it: ``BridgeSecrets.load(<hue.bridge>,
  <patch.yaml hue.host>)`` (app_key, clientkey and the address from ~/.config/screen-machine/hue/<bridge>.json;
  room_service.hue.pair writes it), with patch.yaml's ``hue.host`` over the file's address, so a bridge moved in
  patch.yaml alone moves both streamers. No address in code.
- Same pairing, same application key: the bridge names room and show_service as the same ``active_streamer``, so
  neither's takeover check can see the other. Only the exclusive patch.yaml ``hue.streamer`` switch keeps them apart
  (README S5.6a, S5.12).
- hue-entertainment loads lazily (``xy_streamer()`` imports it when a hue output first starts its area). Without it
  the output reads ``not_built``.

**Threads.**
- show-brain (asyncio, ``ctx.loop``): ``configure``, ``start``, ``tick``, ``request``, ``release``, and the control
  task, the only thing that changes the bridge (``release`` only reads the area).
- the runner ``show-out-<id>`` (outputs.py): ``plan`` and ``set_frame``; they never block, never lock and never do
  I/O beyond ``send_colors`` (which only queues for hue-entertainment's own ``hue-dtls-sender`` thread).
- any thread: ``status``, ``hue_status``. Everything they and the runner read is swapped by reference.

**The control task** (every CONTROL_WAKE_S, or at once on a request, a release, a reconfigure):
  1. output not enabled -> ``off`` (the area stopped if we hold it; ``configure`` already stopped the frames);
  2. patch.yaml ``hue.streamer`` isn't ``show`` -> ``waiting_room`` (room_service streams; never fight). The flip from
     ``show`` forgets what room asked (room re-sends it when it flips back to ``show``);
  3. ``taken_over`` stays until the wanted set changes (room re-applying the mode); the DTLS session is dropped, the
     area is never stopped (someone else holds it);
  4. no wanted lamp -> ``idle``; no pairing file -> ``no_config``;
  5. no active frame for ``stop_after_s`` (patch.yaml's, 10 s) -> stop the area, ``waiting_input`` (the bridge gives
     the lamps back to the house, as room's P0 path did); active again -> start;
  6. start: resolve the area's channels (room's ``resolve_area``), GET the area; already active -> wait up to
     ``start_wait_s`` for it to go inactive, else ``taken_over``; PUT start; GET the area at once: its
     ``active_streamer`` is us (output.py's capture, made before the handshake so nobody can slip in); DTLS connect in
     an executor; patch.yaml read again (the switch flipped back to room, or the output switched off, during the
     handshake: the area is stopped before a single frame); GET it again (another streamer, or not active:
     ``taken_over``); ``streaming``. The start wait re-reads patch.yaml on each poll too;
  7. while streaming, every ``takeover_check_s``: the area not active, or another ``active_streamer`` -> drop DTLS,
     ``taken_over`` (no stop: never fight). A dropped DTLS session is checked the same way, then restarted;
  8. any exception -> ``error`` (or ``no_config`` for a config mismatch, ``not_built`` without hue-entertainment),
     the area stopped if we hold it, retried after ``error_backoff_s``.
  Before any PUT stop the area is read: one that another app's streamer holds is never stopped.

**What room reads** (the routes, contract.hue_answer): ``state`` is one of ``idle | armed | streaming | taken_over``
(contract.HUE_ROOM_STATES), from the richer HueStatus (``/api/v1/rig`` keeps that): ``armed`` = following but silent
(no sound yet, starting, or retrying); words "Following the music" / "Ready: waiting for the music". Also ``lights``
(HueStatus ``asked``: what room asked, unfiltered) and the API's ``boot_id``, for room's re-sync (68's addendum).

**Frames.** ``plan``: a fixed grid at ``rate_hz`` (50, §5.4) while ``starting`` or ``streaming``, else every
IDLE_PLAN_S (only to track activity). ``set_frame``: while streaming, ``(channel, x, y, bri)`` for every wanted lamp
the area has a channel for, **every frame, changed or not** (a lost packet heals at the next). An inactive frame sends
the last active colours (room's input held its last values the same way) until step 5 stops the area. No hits ever
(caps). The engine evaluates each lamp at its own lead (``t_send + rise_ms``) and slews rises to ``min_attack_ms``;
the driver keeps the same rise limit on what it sends (max(rgb) never rises faster than contract.SLEW_PEAK x full
scale per ``min_attack_ms``, 80 ms, §5.4: a smoothstep's peak slope, the engine's own limit, so it never clips the
engine's swell), so a Hue lamp never pulses faster whatever reaches it. ``caps.min_period_ms`` (2 x the attack) asks
the engine to run no oscillator faster on this output.

**Across a restart** (README S5.14): what room asked to follow is persisted (``persist_state``, through the Rig into
state.json) and taken back at start (``restore_state``) only while patch.yaml says ``hue.streamer: show``, so a
restart, a crash or the output switched off and on comes back streaming without waiting for room's next mode apply.

**Release** (``POST /api/v1/hue/release {lights}``, §9.4 step 3; 68's contract): ``released: true`` only once the
lamps are writable: our stream is over (the control task stops the area when nothing we stream is left), the bridge
reads the area inactive, and ``release_settle_s`` (1 s, the bridge's post-entertainment write) has passed since.
Otherwise ``released: false`` with ``why``: ``shared_area`` (other lamps still follow: the bridge holds every lamp of
an area while it streams), ``taken_over`` (another app holds the area), ``bridge_unreachable``, ``timeout``. Never
longer than ``release_timeout_s`` (at most contract.HUE_RELEASE_MAX_S: room's adapter waits 20 s). With no stop of
ours pending the area is still read once (whenever a client and an area are known): one that reads active and isn't
ours is ``taken_over``, never a false ``released`` (room's order, the new set and then the release, clears our own
``taken_over`` state first; an area already active at our start was never ours either).

**Stop** (show_service stopping, or the output switched off): the DTLS session is closed at once and the area is
stopped (PUT stop), so the bridge gives the lamps back. On show-brain ``stop()`` returns the asyncio task that
finishes the PUT (awaitable); from another thread it waits up to ``timeout``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from room_service.hue.client import BridgeSecrets, HueClient, HueError, resolve_area
from room_service.hue.colour import rgb_to_xy
from room_service.hue.stream import COLOR_SPACE_BYTE, GAMMA, xy_bri, xy_streamer

from . import contract

log = logging.getLogger("show_service.hue_out")

__all__ = ["HueOut", "HueConfigError", "BridgeSecrets", "HueClient", "HueError", "resolve_area", "rgb_to_xy",
           "xy_bri", "xy_streamer", "GAMMA", "COLOR_SPACE_BYTE", "hue_settings", "default_client_factory",
           "default_streamer_factory", "default_secrets_loader"]

KIND = "hue"
RATE_HZ = 50.0                 # §5.4, §9.4: continuous 50 Hz, changed or not
MIN_ATTACK_MS = 80.0           # §5.4: Hue beat pulses attack >= 80 ms (the bridge's tick stays invisible)
MIN_PERIOD_ATTACKS = 2.0       # caps.min_period_ms = this x the attack: the look engine's 2 x attack rule, for every
                               # oscillator on a Hue output ("never strobes", §5.4; README S5.14)
IDLE_PLAN_S = 0.1              # S5.6: not starting or streaming, frames are planned this often (activity only)
CONTROL_WAKE_S = 0.5           # S5.6: the control task's own cadence (it is also woken at once)
RELEASE_POLL_S = 0.25          # S5.6: the area is read this often until it reads inactive
OLD_STOP_S = 5.0               # starting point: a stop this old when first seen inactive went inactive long before
SECRETS_RETRY_S = 5.0          # starting point: a missing pairing file is looked for again this often
CONNECT_WAIT_S = 5.0           # starting point: stop() waits this long for a DTLS handshake still in progress
LOG_EVERY_S = 30.0             # a repeating error is logged at most this often (S5.4)
LATE_RING = 256                # lateness samples kept for status()'s p50/p95
STOP_AFTER_S = 10.0            # room_service/hue/output.py's default; patch.yaml hue.stop_after_s wins
STREAMER_DEFAULT = "room"      # patch.yaml hue.streamer unset: room_service streams (its own default)
STREAMER_US = "show"           # the patch.yaml hue.streamer word that hands the area to show_service
#: show.yaml rig.hue fallbacks: the same numbers as show.yaml (S5.10), each a starting point with its source.
RIG_HUE_DEFAULTS: dict[str, float] = {
    "takeover_check_s": 2.0,   # room's own rule (room_service/hue/output.py STATUS_EVERY_S)
    "start_wait_s": 3.0,       # starting point
    "release_settle_s": 1.0,   # §9.4: the bridge's own post-entertainment write
    "release_timeout_s": 15.0, # starting point (room's hue adapter waits 20 s; capped at contract.HUE_RELEASE_MAX_S)
    "error_backoff_s": 5.0,    # room's own rule (room_service/hue/output.py)
}
PAIR_HINT = "isn't paired (python -m room_service.hue.pair)"
_AREA = "entertainment_configuration"    # CLIP v2 resource type


# -- the defaults: room_service's own (68's contract) ---------------------------------------------------------------------
def default_client_factory(secrets: BridgeSecrets) -> HueClient:
    """room's CLIP v2 client (at most 3 requests in flight, 429/503 retried; verification off; the host is the
    secrets', i.e. patch.yaml's hue.host over the pairing file's)."""
    return HueClient(secrets)


def default_streamer_factory() -> Any:
    """room's xy streamer: connect(host, app_key, clientkey, area_id) (blocking: the DTLS handshake), send_colors
    (queues), disconnect, is_connected. Imports hue-entertainment here, lazily."""
    return xy_streamer()


def default_secrets_loader(bridge_id: Optional[str], host: Optional[str] = None) -> Optional[BridgeSecrets]:
    """room's pairing, read-only, exactly as room_service/dmx/__init__.py loads it (``BridgeSecrets.load(hue.bridge,
    hue.host)``): the file for the bridge id, else for the host, else the only file, with patch.yaml's ``hue.host``
    over the file's address; None when nothing is paired."""
    return BridgeSecrets.load(bridge_id, host)


class HueConfigError(HueError):
    """What config names isn't on the bridge (no area to stream, or none of the lamps in it): state ``no_config``."""


def _rid(ref: Any) -> Optional[str]:
    return ref.get("rid") if isinstance(ref, dict) else None


# -- settings ---------------------------------------------------------------------------------------------------------------
class _PatchFile:
    """patch.yaml (68's), read-only: its ``hue`` block, re-read when its mtime changes. A bad edit keeps the last good."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.mtime: Optional[float] = None
        self.data: dict = {}
        self.error: Optional[str] = None

    def poll(self) -> bool:
        try:
            m = os.stat(self.path).st_mtime_ns
        except OSError as e:
            if self.mtime is not None or self.error is None:
                self.error = f"{self.path.name}: {type(e).__name__}"
                self.mtime = None
                return True
            return False
        if m == self.mtime:
            return False
        self.mtime = m
        try:
            import yaml
            d = yaml.safe_load(self.path.read_text()) or {}
            if not isinstance(d, dict):
                raise ValueError("not a mapping")
            self.data, self.error = d, None
        except Exception as e:                     # noqa: BLE001  keep the last good
            self.error = f"{self.path.name}: {type(e).__name__}: {e}"
        return True


def _fixtures_hue_settings() -> Optional[Callable]:
    try:
        from . import fixtures                     # B12's (README S5.4)
    except Exception:                              # noqa: BLE001
        return None
    return getattr(fixtures, "hue_settings", None)


def hue_settings(out: Optional[dict], patch: Optional[dict]) -> dict:
    """{bridge, area, gamma, stop_after_s, streamer, host}: fixtures.yaml's output settings over patch.yaml's hue
    block; ``streamer`` always from patch.yaml (room's switch); ``host`` patch.yaml's, only for patch.yaml's own bridge
    (room's ``BridgeSecrets.load(bridge, host)``). fixtures.hue_settings when it is there (one rule), else this."""
    fn = _fixtures_hue_settings()
    if fn is not None:
        try:
            got = fn(out or {}, patch)
            if isinstance(got, dict):
                return dict(got)
        except Exception as e:                     # noqa: BLE001  degrade: our own copy of the rule
            log.warning("fixtures.hue_settings failed (%s: %s); using hue_out's own", type(e).__name__, e)
    hue = (patch or {}).get("hue") if isinstance(patch, dict) else None
    hue = hue if isinstance(hue, dict) else {}
    s = (out or {}).get("settings") or {}
    s = s if isinstance(s, dict) else {}
    res = {k: s.get(k, hue.get(k)) for k in ("bridge", "area", "gamma", "stop_after_s")}
    res["streamer"] = hue.get("streamer")
    host = hue.get("host")
    same_bridge = res["bridge"] == hue.get("bridge")
    res["host"] = host.strip() if isinstance(host, str) and host.strip() and same_bridge else None
    return res


def _num(v: Any, default: float, lo: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) and f >= lo else default


def _rgb(v: Any) -> Optional[tuple[float, float, float]]:
    """A frame's rgb as three floats in 0..1 (NaN reads 0), or None."""
    try:
        r, g, b = v
        r, g, b = float(r), float(g), float(b)
    except (TypeError, ValueError):
        return None
    return (r if 0.0 < r < 1.0 else (1.0 if r >= 1.0 else 0.0), g if 0.0 < g < 1.0 else (1.0 if g >= 1.0 else 0.0),
            b if 0.0 < b < 1.0 else (1.0 if b >= 1.0 else 0.0))


XY_KEY_SCALE = 1_000_000       # the xy cache's key: the full-level colour to 1e-6 (far below xy's 4 dp)


# -- the driver ---------------------------------------------------------------------------------------------------------------
class HueOut:
    """contract.Driver, kind "hue": one Entertainment area on one bridge (README S5.6)."""

    kind = KIND
    caps: contract.DriverCaps = {"hits": False, "min_attack_ms": MIN_ATTACK_MS, "grid": "fixed", "rate_hz": RATE_HZ,
                                 "keepalive_s": None,   # §5.4, §9.4
                                 # "never strobes": no oscillator cycles faster than 2 x the attack (README S5.14)
                                 "min_period_ms": MIN_PERIOD_ATTACKS * MIN_ATTACK_MS,
                                 # room's hue adapter decides which lamps follow: no vibe keeps the neutral look
                                 "idle_without_vibe": False}

    def __init__(self, output_id: str, *, ctx: Any = None, clock: Any = None, settings: Optional[dict] = None,
                 shared: Optional[dict] = None,
                 client_factory: Optional[Callable[[BridgeSecrets], Any]] = None,
                 streamer_factory: Optional[Callable[[], Any]] = None,
                 secrets_loader: Optional[Callable[[Optional[str], Optional[str]], Optional[BridgeSecrets]]] = None,
                 patch_path: Optional[Path] = None, mono: Callable[[], float] = time.monotonic, **_kw: Any) -> None:
        self.output_id = output_id
        self.ctx = ctx
        self.clock = clock                          # unused: the engine reads the clock
        self.shared = shared if shared is not None else {}
        self._client_factory = client_factory
        self._streamer_factory = streamer_factory
        self._secrets_loader = secrets_loader
        self._patch = _PatchFile(Path(patch_path) if patch_path is not None else contract.PATCH_FILE)
        self.mono = mono
        self.caps = dict(HueOut.caps)               # type: ignore[assignment]   per instance: the output's own
        self._base_settings = settings if isinstance(settings, dict) else {}
        self._rs: dict[str, float] = dict(RIG_HUE_DEFAULTS)
        self._apply_rig_settings()
        # config (show-brain)
        self._enabled = False
        self._out: Optional[dict] = None
        self._hue: dict = {"bridge": None, "area": None, "gamma": GAMMA, "stop_after_s": STOP_AFTER_S,
                           "streamer": STREAMER_DEFAULT, "host": None}
        self._stop_after_s = STOP_AFTER_S
        self._known: dict[str, str] = {}            # fixture id -> light id (this output's hue fixtures)
        self._asked: list[str] = []                 # what room asked to follow (the full set, each time)
        self._wanted: frozenset[str] = frozenset()
        self._unknown: list[str] = []
        self._restart = False                       # the bridge or the area changed under a held area
        # the bridge (the control task)
        self._secrets: Optional[BridgeSecrets] = None
        self._secrets_for: Any = ()
        self._secrets_next = 0.0
        self._secrets_error: Optional[str] = None
        self._client: Any = None
        self._client_for: Any = None
        self._streamer: Any = None
        self._area: Optional[str] = None
        self._area_note: Optional[str] = None       # the area streamed isn't patch.yaml's (room's fallback rule)
        self._channels: dict[str, int] = {}
        self._our_streamer: Optional[str] = None
        self._holding = False                       # we PUT start and haven't stopped it since
        self._stop_t: Optional[float] = None        # our last PUT stop (mono), until we start again
        self._inactive_t: Optional[float] = None    # when that stop counts as inactive on the bridge (release)
        self._taken_wanted: Optional[frozenset] = None
        self._next_check = 0.0
        self._retry_at = 0.0
        self._connect_fut: Any = None
        self._state = "off"
        self._detail: Optional[str] = self._off_detail()
        self._log_t: dict[str, float] = {}
        # the control task
        self._loop: Any = None
        self._task: Any = None
        self._spawn_pending = False
        self._wake = asyncio.Event()
        self._waiters: list[asyncio.Future] = []
        self._stopping = False
        # the runner's (written there; read anywhere by reference)
        self._streaming = False
        self._send: tuple[tuple[str, int], ...] = ()   # (fixture id, channel): swapped by reference
        self._gamma = GAMMA
        self._held: dict[str, tuple[float, float, float]] = {}   # the last active frame's rgb per lamp
        self._last_cmd: dict[str, tuple[int, int, int, int]] = {}
        self._level: dict[str, tuple[float, float]] = {}   # fixture -> (t, level): the rise limit's state
        self._xy: dict[str, tuple[Any, tuple[int, int]]] = {}   # fixture -> (colour key, 16-bit xy)
        self._last_active_t: Optional[float] = None
        self._planned = 0.0
        self._grid_t = 0.0
        self.frames = 0
        self.errors = 0
        self._late = [0.0] * LATE_RING
        self._late_n = 0
        self._marks: deque = deque(maxlen=6)
        self._rate = 0.0
        self._last_send_t: Optional[float] = None

    # -- contract.Driver: show-brain ------------------------------------------------------------------------------------
    def configure(self, out: contract.OutputConfig, rig: Optional[contract.RigView]) -> None:
        out = out if isinstance(out, dict) else {}       # type: ignore[assignment]
        self._out = out                                  # type: ignore[assignment]
        self._enabled = out.get("enabled") is True
        if not self._enabled:
            self._streaming = False                      # stop the frames at once; the control task stops the area
        attack = max(MIN_ATTACK_MS, _num(out.get("min_attack_ms"), 0.0))    # it can only raise the driver's floor
        rate = _num(out.get("rate_hz"), RATE_HZ)
        self.caps = {"hits": False, "min_attack_ms": attack, "grid": "fixed",   # hits never reach Hue (§5.4)
                     "rate_hz": rate if rate > 0 else RATE_HZ, "keepalive_s": None,
                     "min_period_ms": MIN_PERIOD_ATTACKS * attack, "idle_without_vibe": False}
        known: dict[str, str] = {}
        for fid, f in ((rig or {}).get("fixtures") or {}).items():
            if isinstance(f, dict) and f.get("output") == self.output_id and f.get("light") \
                    and f.get("kind") != "screen":       # the screens rule: a screen never reaches a lamp's stream
                known[str(fid)] = str(f["light"])
        self._known = known
        self._apply_rig_settings()
        self._patch.poll()
        self._refresh_hue()
        self._recompute_wanted()
        if not self._holds():                        # the control task refines it at its next step
            if not self._enabled:
                self._set("off", self._off_detail())
            elif self._state == "off":
                self._set("idle", None)
        self._poke()

    def start(self) -> None:
        self._stopping = False
        self._loop = getattr(self.ctx, "loop", None)
        self._wake = asyncio.Event()                # bound to this loop at its first wait
        if self._loop is None:
            self._set("error", "no show-brain loop to run the hue control task on")
            return
        self._spawn()

    def tick(self, now_s: float) -> None:
        self._apply_rig_settings()
        if self._stopping or self._loop is None:
            return
        t = self._task
        if (t is None or t.done()) and not self._spawn_pending:
            if t is not None and not t.cancelled() and t.exception() is not None:
                log.warning("hue %s: the control task ended (%s); restarted", self.output_id, t.exception())
            self._spawn()

    def stop(self, timeout: float = 2.0) -> Any:
        """Drop the DTLS session at once and stop the area (the bridge gives the lamps back). On show-brain this
        returns the task that finishes the PUT (awaitable); from another thread it waits up to ``timeout``."""
        self._stopping = True
        self._streaming = False
        self._send = ()
        task, self._task = self._task, None
        loop = self._loop
        on_loop = self._on_loop()
        if task is not None and not task.done():
            if on_loop:
                task.cancel()
            elif loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass
        s = self._streamer
        if s is not None and getattr(s, "is_connected", False):
            try:
                s.disconnect()                      # quick: the sender thread wakes on its stop marker
            except Exception as e:                  # noqa: BLE001
                log.warning("hue %s: disconnect: %s", self.output_id, e)
        if loop is None or loop.is_closed():
            self._holding = False
            self._set("off", "stopped")
            return None
        if on_loop:
            return loop.create_task(self._shutdown(), name=f"hue-out-stop-{self.output_id}")
        if loop.is_running():
            cf = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                cf.result(timeout)
            except (concurrent.futures.TimeoutError, TimeoutError):
                log.warning("hue %s: stopping the area took over %.1f s", self.output_id, timeout)
            except Exception as e:                  # noqa: BLE001
                log.warning("hue %s: stop: %s", self.output_id, e)
            return None
        self._holding = False                       # no loop to reach the bridge on: it ends the unfed area itself
        self._set("off", "stopped")
        return None

    # -- contract.Driver: the runner -------------------------------------------------------------------------------------
    def plan(self, t_last_s: float, now_s: float, active: bool) -> float:
        if active:
            self._last_active_t = self.mono()
        period = 1.0 / self.caps["rate_hz"] if self._state in ("starting", "streaming") else IDLE_PLAN_S
        base = self._grid_t or t_last_s
        t = base + period if base else now_s
        if t < now_s - period:                      # fell behind by over a period: re-anchor, never a burst
            t = now_s
        self._planned = t
        return t

    def set_frame(self, frame: contract.Frame) -> None:
        now = self.mono()
        active = bool(frame.get("active"))
        if active:                                  # copied: the engine may reuse its frame dicts
            self._last_active_t = now
            fixtures = frame.get("fixtures") or {}
            held = {}
            for fid in self._known:
                ff = fixtures.get(fid)
                rgb = _rgb(ff.get("rgb")) if isinstance(ff, dict) else None
                if rgb is not None:
                    held[fid] = rgb
            self._held = held
        planned = self._planned
        self._grid_t = planned or now
        if not self._streaming:
            return
        send, streamer = self._send, self._streamer
        if not send or streamer is None:
            return
        if planned:
            self._late[self._late_n % LATE_RING] = max(0.0, now - planned) * 1000.0
            self._late_n += 1
        src = self._held                            # this frame's colours, or (inactive) the last active ones
        t = frame.get("t_send_ms")
        t = t / 1000.0 if isinstance(t, (int, float)) and math.isfinite(t) else now
        attack_s = self.caps["min_attack_ms"] / 1000.0
        gamma = self._gamma
        cmds = []
        for fid, ch in send:
            rgb = src.get(fid)
            if rgb is None:                         # not in this frame: the lamp's last command again
                last = self._last_cmd.get(fid)
                if last is not None and last[0] == ch:
                    cmds.append(last)
                continue
            lvl = max(rgb)
            prev = self._level.get(fid)
            if prev is not None and attack_s > 0:   # the rise limit: the engine's own (SLEW_PEAK / attack), so it
                cap = prev[1] + contract.SLEW_PEAK * max(0.0, t - prev[0]) / attack_s   # never clips the swell
                if lvl > cap:
                    k = cap / lvl
                    rgb = (rgb[0] * k, rgb[1] * k, rgb[2] * k)
                    lvl = cap
            self._level[fid] = (t, lvl)
            # room's xy_bri(rgb, gamma), with the xy of the full-level colour cached: a pulse only scales the colour
            if lvl > 0.0:
                n = (rgb[0] / lvl, rgb[1] / lvl, rgb[2] / lvl)
                key = (int(n[0] * XY_KEY_SCALE + 0.5), int(n[1] * XY_KEY_SCALE + 0.5), int(n[2] * XY_KEY_SCALE + 0.5))
            else:
                n, key = (0.0, 0.0, 0.0), None
            c = self._xy.get(fid)
            if c is not None and c[0] == key:
                x, y = c[1]
            else:
                xf, yf = rgb_to_xy(*n)
                x, y = round(xf * 0xFFFF), round(yf * 0xFFFF)
                self._xy[fid] = (key, (x, y))
            cmd = (ch, x, y, round(lvl ** gamma * 0xFFFF))
            self._last_cmd[fid] = cmd
            cmds.append(cmd)
        if not cmds:
            return
        try:
            streamer.send_colors(cmds)
        except Exception as e:                      # noqa: BLE001  counted, logged at most every 30 s
            self.errors += 1
            self._log_limited("send", "hue %s: send_colors: %s: %s", self.output_id, type(e).__name__, e)
            return
        self.frames += 1
        self._last_send_t = now
        if not self._marks or now - self._marks[-1][0] >= 1.0:
            self._marks.append((now, self.frames))
            t0, f0 = self._marks[0]
            if now > t0:
                self._rate = (self.frames - f0) / (now - t0)

    # -- any thread ---------------------------------------------------------------------------------------------------------
    def hue_status(self) -> contract.HueStatus:
        s = self._streamer
        dtls = bool(s is not None and getattr(s, "is_connected", False))
        st: dict = {"state": self._state, "detail": self._detail, "enabled": self._enabled, "output": self.output_id,
                    "wanted": sorted(self._wanted), "asked": list(self._asked),
                    "streaming": self._state == "streaming" and dtls,
                    "area": self._area, "channels": dict(self._channels), "frames": self.frames, "dtls": dtls,
                    "unknown": list(self._unknown)}
        rs = contract.hue_room_state(st)
        st["room_state"], st["words"] = rs, contract.HUE_WORDS[rs]
        return st                                   # type: ignore[return-value]

    def status(self) -> contract.OutputStatus:
        now = self.mono()
        last = self._last_send_t
        age = None if last is None else round(now - last, 3)
        n = min(self._late_n, LATE_RING)
        lat = sorted(self._late[:n])

        def pct(q: float) -> Optional[float]:
            return round(lat[min(n - 1, int(q * n))], 2) if n else None
        return {"id": self.output_id, "driver": KIND, "enabled": self._enabled, "state": self._state,
                "detail": self._detail, "frames": self.frames,
                "rate_hz": round(self._rate, 2) if age is not None and age < 5.0 else 0.0,
                "late_ms": {"p50": pct(0.5), "p95": pct(0.95)}, "errors": self.errors, "last_frame_age_s": age,
                "extra": dict(self.hue_status())}

    # -- the routes (show-brain) -----------------------------------------------------------------------------------------
    def request(self, lights: list[str], on: bool) -> contract.HueStatus:
        """POST /api/v1/hue/stream {lights, on}: the complete set of lamps that follow the show, every time (not a
        delta). wanted = the given hue fixtures of this output (others in ``unknown``); none when ``on`` is false or
        the list is empty. Answers at once; the state follows (the route maps it, contract.hue_answer)."""
        if not isinstance(lights, list) or not all(isinstance(x, str) for x in lights):
            raise ValueError("bad_request: lights must be a list of fixture ids")
        if self._patch.poll():                      # a fresh hue.streamer (room may have just flipped it)
            self._refresh_hue()
        self._asked = list(dict.fromkeys(lights)) if on else []
        self._recompute_wanted()
        self._persist()
        self._poke()
        return self.hue_status()

    # -- what room asked, across a restart (README S5.14) ----------------------------------------------------------------
    def persist_state(self) -> dict:
        """show-brain (Rig.persist): what room asked this output to follow, so a restart, a crash or the output
        switched off and on comes back streaming with no mode change (room re-sends only on a mode apply)."""
        return {"asked": list(self._asked), "streamer": self._hue.get("streamer") or STREAMER_DEFAULT}

    def restore_state(self, saved: Any) -> bool:
        """show-brain, after ``configure`` (Rig): take back a saved ``asked`` list, only while patch.yaml still says
        ``hue.streamer: show`` (after a rollback to ``room`` room streams, and its own HueOutput holds the lamps).
        True when it was taken."""
        asked = saved.get("asked") if isinstance(saved, dict) else None
        if not isinstance(asked, list) or not all(isinstance(x, str) for x in asked) or not asked:
            return False
        self._patch.poll()
        self._refresh_hue()
        if not self._ours():
            log.info("hue %s: not taking back %d asked lamp(s): patch.yaml hue.streamer isn't %s", self.output_id,
                     len(asked), STREAMER_US)
            return False
        self._asked = list(dict.fromkeys(asked))
        self._recompute_wanted()
        log.info("hue %s: following %s again (asked before the restart)", self.output_id, ", ".join(self._asked))
        self._poke()
        return True

    def _persist(self) -> None:
        fn = getattr(self.ctx, "persist", None)
        if callable(fn):
            try:
                fn()
            except Exception:                       # noqa: BLE001  persistence is best effort
                pass

    async def release(self, lights: Optional[list[str]] = None) -> contract.HueRelease:
        """POST /api/v1/hue/release {lights?}: the lamps leave the wanted set; ``released: true`` only once our stream
        is over, the bridge reads the area inactive and ``release_settle_s`` has passed (module docstring)."""
        t0 = self.mono()
        if lights is not None and (not isinstance(lights, list) or not all(isinstance(x, str) for x in lights)):
            raise ValueError("bad_request: lights must be a list of fixture ids")
        was_taken = self._state == "taken_over"
        gone = None if lights is None else set(lights)
        self._asked = [] if gone is None else [a for a in self._asked if a not in gone]
        self._recompute_wanted()
        self._persist()
        self._poke()
        deadline = t0 + self._release_timeout_s()
        if was_taken:                               # another app streams the area: the lamps aren't writable
            return self._release_answer(False, "taken_over", 0.0)
        if self._wanted and self._holds():
            return self._release_answer(False, "shared_area", 0.0, sorted(self._wanted))
        if self._holds():                           # the control task (woken above) stops the area
            fut = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            try:
                released, why = await asyncio.wait_for(asyncio.shield(fut), max(0.0, deadline - self.mono()))
            except (asyncio.TimeoutError, TimeoutError):
                released, why = False, "timeout"
            if not released:
                return self._release_answer(False, why, (self.mono() - t0) * 1000.0)
        why = await self._given_back(deadline)
        return self._release_answer(why is None, why, (self.mono() - t0) * 1000.0)

    async def _given_back(self, deadline: float) -> Optional[str]:
        """After our stop: None once the bridge reads the area inactive and ``release_settle_s`` has passed since it
        went inactive; else why not. With no stop of ours pending (none since our last start, or one already seen
        inactive) the area is still read once, whenever a client and an area are known: we don't hold it (``release``
        waited out our own stop), so an active area is someone else's (``taken_over``), never a false ``released``.
        Nothing to read before our first start (no client or area yet)."""
        if self._client is None or not self._area:
            return None
        stop_t, inactive_t = self._stop_t, self._inactive_t
        if stop_t is None or inactive_t is not None:
            try:
                area = await self._get_area()
            except HueConfigError:                  # the area is gone from the bridge: nothing streams it
                return None
            except HueError:
                return "bridge_unreachable"
            if area.get("status") == "active":      # not ours: another app's stream, or one we never started
                return "shared_area" if self._holds() else "taken_over"    # (ours again: a request since)
            if stop_t is None:
                return None
        else:
            while True:
                if self._client is None:            # show_service stopping: its own stop gives the lamps back
                    return None
                try:
                    area = await self._get_area()
                except HueConfigError:              # the area is gone from the bridge: nothing streams it
                    area = {"status": "inactive"}
                except HueError:
                    return "bridge_unreachable"
                if area.get("status") != "active":
                    break
                if self._held_by_other(area):
                    return "taken_over"
                if self.mono() >= deadline:
                    return "timeout"
                await asyncio.sleep(RELEASE_POLL_S)
            now = self.mono()                       # a stop first seen inactive long after went inactive long before
            inactive_t = now if now - stop_t <= OLD_STOP_S else stop_t
            if self._stop_t == stop_t:              # (not started again meanwhile)
                self._inactive_t = inactive_t
        wait = inactive_t + self._rs["release_settle_s"] - self.mono()
        if wait > 0:                                # the bridge's own post-entertainment write comes first
            if self.mono() + wait > deadline:
                await asyncio.sleep(max(0.0, deadline - self.mono()))
                return "timeout"
            await asyncio.sleep(wait)
        return None

    # -- the control task ------------------------------------------------------------------------------------------------
    async def _control(self) -> None:
        self._spawn_pending = False
        while not self._stopping:
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception as e:                  # noqa: BLE001  keep the loop alive; report and back off
                await self._fail(e)
            self._settle_waiters()
            try:
                await asyncio.wait_for(self._wake.wait(), CONTROL_WAKE_S)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._wake.clear()

    async def _step(self) -> None:
        now = self.mono()
        if self._patch.poll():
            self._refresh_hue()
        if not self._enabled:
            if await self._ensure_stopped("output off"):
                self._set("off", self._off_detail())
            return
        streamer_word = self._hue.get("streamer") or STREAMER_DEFAULT
        if streamer_word != STREAMER_US:
            if await self._ensure_stopped("hue.streamer isn't show"):
                self._set("waiting_room", f"patch.yaml hue.streamer is {streamer_word!r}: room_service streams the "
                                          f"area (show_service streams with {STREAMER_US!r})")
            return
        if self._state == "taken_over":
            await self._drop_dtls()                 # never stop it: someone else holds the area
            return
        if not self._wanted:
            if await self._ensure_stopped("no lamp follows"):
                self._set("idle", None if not self._unknown else f"not hue lamps of this output: "
                                                                  f"{', '.join(self._unknown)}")
            return
        if self._restart and self._holds():
            if await self._ensure_stopped("the bridge or the area changed"):
                self._restart = False
            return
        self._restart = False
        if now < self._retry_at:
            return
        secrets = self._load_secrets(now)
        if secrets is None:
            if await self._ensure_stopped("no pairing"):
                b = self._hue.get("bridge")
                self._set("no_config", self._secrets_error or
                          (f"the bridge {b} {PAIR_HINT}" if b else f"the bridge {PAIR_HINT}"))
            return
        last = self._last_active_t
        if last is None or now - last > self._stop_after_s:
            if await self._ensure_stopped(f"no look for {self._stop_after_s:g} s"):
                self._set("waiting_input", "no look to show yet" if last is None else
                          f"no look to show for {self._stop_after_s:g} s: the bridge has the lamps back")
            return
        streamer = self._get_streamer()
        if self._streaming and not streamer.is_connected:   # the DTLS session dropped under us
            log.warning("hue %s: the DTLS session dropped", self.output_id)
            self._streaming = False
            area = await self._get_area()
            if self._held_by_other(area):
                await self._taken(area)
                return
            if area.get("status") != "active":     # the bridge ended it already: restart (room's rule)
                self._holding = False
        if not self._streaming:
            await self._start(secrets, streamer)
            return
        if now >= self._next_check:
            await self._check_takeover(now)

    async def _resolve(self, client: Any) -> tuple[str, dict[str, int]]:
        """room's resolve_area: patch.yaml's ``hue.area`` when the bridge has it, else the area named "Screen
        Machine", else the only one (its rule, unchanged). None found: ``no_config``."""
        want = str(self._hue.get("area")) if self._hue.get("area") else None
        try:
            area_id, channels = await resolve_area(client, want)
        except HueError as e:
            if e.status == 404:
                raise HueConfigError(404, f"no entertainment area to stream (patch.yaml hue.area {want}): {e}") from e
            raise
        note = None
        if want and area_id != want:
            note = f"patch.yaml hue.area {want} isn't on the bridge: streaming area {area_id} (room's rule)"
            self._log_limited("area", "hue %s: %s", self.output_id, note)
        self._area_note = note
        return area_id, channels

    async def _start(self, secrets: BridgeSecrets, streamer: Any) -> None:
        if self._holding:                           # still ours from before (an error, a dropped session): stop it
            await self._stop_area("restarting")
        self._set("starting", None)
        client = self._get_client(secrets)
        area_id, channels = await self._resolve(client)
        self._area, self._channels = area_id, channels
        self._rebuild_send()
        if not self._send:
            raise HueConfigError(404, f"none of the lamps asked ({', '.join(sorted(self._wanted))}) has a channel "
                                      f"in entertainment area {area_id}")
        area = await self._get_area()
        if area.get("status") == "active":         # someone else, or room still stopping after the switch
            deadline = self.mono() + self._rs["start_wait_s"]
            while area.get("status") == "active":
                if self.mono() >= deadline:
                    self._taken_wanted = self._wanted
                    self._set("taken_over", "the entertainment area is already streaming (another app, or "
                                            "room_service not stopped yet): never fight")
                    log.warning("hue %s: area %s already active; not started", self.output_id, area_id)
                    return
                await asyncio.sleep(RELEASE_POLL_S)
                if not self._still_ours():
                    return
                area = await self._get_area()
        if not self._still_ours():
            return
        self._holding = True
        self._our_streamer = None
        await client.put(_AREA, area_id, {"action": "start"})
        self._stop_t = self._inactive_t = None
        # right after our own start, the area's active_streamer is us (room's output.py capture, made before the
        # handshake so another app taking the area meanwhile is never mistaken for us)
        self._our_streamer = _rid((await self._get_area()).get("active_streamer"))
        fut = asyncio.get_running_loop().run_in_executor(None, streamer.connect, secrets.host, secrets.app_key,
                                                         secrets.clientkey, area_id)
        self._connect_fut = fut                     # shielded: a stop mid-handshake waits for it, then closes it
        await asyncio.shield(fut)
        self._connect_fut = None
        if not self._still_ours():                  # flipped back to room (or switched off) during the handshake:
            await self._stop_area("switched during start")    # give the area back before a single frame (room's
            return                                  # 2 s patch watch starts its own stream after our stop, S5.12)
        area = await self._get_area()
        if self._our_streamer is None:              # the bridge named no streamer at once: the one now is us
            self._our_streamer = _rid(area.get("active_streamer"))
        if area.get("status") != "active" or self._held_by_other(area):
            await self._taken(area)                 # stopped or taken during the handshake: never fight
            return
        self._next_check = self.mono() + self._rs["takeover_check_s"]
        self._level = {}
        self._streaming = True
        self._set("streaming", self._area_note)
        log.info("hue %s: streaming area %s (%d channels, %d lamps)", self.output_id, area_id, len(channels),
                 len(self._send))

    async def _check_takeover(self, now: float) -> bool:
        self._next_check = now + self._rs["takeover_check_s"]
        area = await self._get_area()
        if area.get("status") != "active" or self._held_by_other(area):
            await self._taken(area)
            return True
        return False

    def _held_by_other(self, area: dict) -> bool:
        rid = _rid(area.get("active_streamer"))
        return area.get("status") == "active" and bool(self._our_streamer and rid and rid != self._our_streamer)

    async def _taken(self, area: dict) -> None:
        self._streaming = False
        await self._drop_dtls()
        self._holding = False                       # never stop it: it isn't ours any more
        self._taken_wanted = self._wanted
        self._set("taken_over", "another app stopped or took the entertainment area: never fight (nothing "
                                "restarts until room asks for a different set of lamps)")
        log.warning("hue %s: area %s taken over or stopped by another app (status %s)", self.output_id,
                    self._area, area.get("status"))

    async def _ensure_stopped(self, why: str) -> bool:
        """Stop the area if we hold it. False while a failed stop waits out its backoff (the state stays ``error``)."""
        if not self._holds():
            return True
        if self.mono() < self._retry_at:
            return False
        await self._stop_area(why)
        return True

    async def _stop_area(self, why: str) -> None:
        self._streaming = False
        await self._drop_dtls()
        if not self._holding or not self._area or self._client is None:
            self._holding = False
            return
        if self._waiters:
            self._set("releasing", why)
        area = await self._get_area()
        if self._held_by_other(area):
            log.warning("hue %s: area %s is held by another streamer: not stopped (never fight)", self.output_id,
                        self._area)
            self._holding = False
            return
        await self._client.put(_AREA, self._area, {"action": "stop"})
        self._holding = False
        self._stop_t, self._inactive_t = self.mono(), None
        log.info("hue %s: area %s stopped (%s)", self.output_id, self._area, why)

    async def _drop_dtls(self) -> None:
        s = self._streamer
        if s is not None and s.is_connected:
            await asyncio.get_running_loop().run_in_executor(None, s.disconnect)

    async def _fail(self, e: BaseException) -> None:
        self.errors += 1
        if isinstance(e, ImportError):
            state, detail = "not_built", f"hue-entertainment isn't installed in this venv ({e})"
        elif isinstance(e, HueConfigError):
            state, detail = "no_config", str(e)
        else:
            state, detail = "error", f"{type(e).__name__}: {e}"
        self._log_limited(f"fail:{state}", "hue %s: %s", self.output_id, detail)
        failed_stop = False
        try:
            await self._stop_area("after an error")
        except asyncio.CancelledError:
            raise
        except Exception as e2:                     # noqa: BLE001  retried after the backoff
            failed_stop = True
            self._log_limited("fail:stop", "hue %s: stopping after the error failed: %s", self.output_id, e2)
        if failed_stop or self._holds():            # a release can't be confirmed: say why
            self._resolve_waiters(False, "bridge_unreachable")
        self._set(state, detail)
        self._retry_at = self.mono() + self._rs["error_backoff_s"]

    async def _shutdown(self) -> None:
        try:
            cf = self._connect_fut
            if cf is not None and not cf.done():    # a handshake still in its executor: let it land, then close it
                try:
                    await asyncio.wait_for(asyncio.shield(cf), CONNECT_WAIT_S)
                except Exception:                   # noqa: BLE001
                    pass
            await self._stop_area("show_service stopping")
        except Exception as e:                      # noqa: BLE001  the bridge ends an unfed area by itself
            log.warning("hue %s: stopping the area: %s: %s", self.output_id, type(e).__name__, e)
        finally:
            self._holding = False
            self._resolve_waiters(True, None)
            self._set("off", "stopped")
            c, self._client = self._client, None
            if c is not None and hasattr(c, "close"):
                try:
                    await c.close()
                except Exception:                   # noqa: BLE001
                    pass

    # -- helpers ----------------------------------------------------------------------------------------------------------------
    def _holds(self) -> bool:
        s = self._streamer
        return self._holding or self._streaming or bool(s is not None and getattr(s, "is_connected", False))

    def _ours(self) -> bool:
        return (self._hue.get("streamer") or STREAMER_DEFAULT) == STREAMER_US

    def _should_stream(self) -> bool:
        return not self._stopping and self._enabled and bool(self._wanted) and self._ours()

    def _still_ours(self) -> bool:
        """``_should_stream`` on a fresh patch.yaml: room may flip ``hue.streamer`` back at any moment (the start)."""
        if self._patch.poll():
            self._refresh_hue()
        return self._should_stream()

    def _release_timeout_s(self) -> float:
        return min(self._rs["release_timeout_s"], contract.HUE_RELEASE_MAX_S)

    def _settle_waiters(self) -> None:
        if self._waiters and not self._holds():
            self._resolve_waiters(True, None)

    def _resolve_waiters(self, released: bool, why: Optional[str]) -> None:
        ws, self._waiters = self._waiters, []
        for f in ws:
            if not f.done():
                f.set_result((released, why))

    def _release_answer(self, released: bool, why: Optional[str], waited_ms: float,
                        still: Optional[list[str]] = None) -> contract.HueRelease:
        return {"released": released, "state": contract.hue_room_state(self.hue_status()), "why": why,
                "still_following": list(still or []), "waited_ms": round(waited_ms, 1), "via": "show"}

    def _recompute_wanted(self) -> None:
        known = self._known
        wanted = frozenset(a for a in self._asked if a in known)
        self._unknown = [a for a in self._asked if a not in known]
        if wanted != self._wanted:
            self._wanted = wanted
            if self._state == "taken_over" and wanted != self._taken_wanted:
                self._taken_wanted = None
                self._set("idle", None)
        self._rebuild_send()

    def _rebuild_send(self) -> None:
        ch = self._channels
        self._send = tuple((fid, ch[self._known[fid]]) for fid in sorted(self._wanted)
                           if self._known.get(fid) in ch)

    def _refresh_hue(self) -> None:
        h = hue_settings(self._out, self._patch.data)
        old = self._hue
        moved = h.get("bridge") != old.get("bridge") or h.get("host") != old.get("host")
        if self._holding and (moved or h.get("area") != old.get("area")):
            self._restart = True                    # (compared with the configured area: room's fallback may differ)
        if moved:
            self._secrets, self._secrets_for = None, ()
        was_ours = self._ours()
        self._hue = h
        self._gamma = _num(h.get("gamma"), GAMMA, lo=1e-6)
        self._stop_after_s = _num(h.get("stop_after_s"), STOP_AFTER_S)
        now_ours = self._ours()
        if was_ours and not now_ours:               # the rollback: room streams; what it asked of us is forgotten
            self._streaming = False                 # (it re-sends the set when it flips back to show)
            if self._asked:
                log.info("hue %s: patch.yaml hue.streamer is %r: forgetting %s", self.output_id, h.get("streamer"),
                         ", ".join(self._asked))
                self._asked = []
                self._recompute_wanted()
                self._persist()
        elif now_ours and not was_ours and self._state == "waiting_room":
            self._set("idle", None)                 # the control task refines it at its next step

    def _apply_rig_settings(self) -> None:
        src = self._base_settings
        settings = getattr(self.ctx, "settings", None)
        if isinstance(settings, dict):
            rig = settings.get("rig")
            hue = rig.get(KIND) if isinstance(rig, dict) else None
            if isinstance(hue, dict):
                src = hue
        self._rs = {k: _num(src.get(k), d) for k, d in RIG_HUE_DEFAULTS.items()}

    def _load_secrets(self, now: float) -> Optional[BridgeSecrets]:
        bridge, host = self._hue.get("bridge"), self._hue.get("host")
        key = (bridge, host)
        if self._secrets is not None and self._secrets_for == key:
            return self._secrets
        if self._secrets_for == key and now < self._secrets_next:
            return None
        self._secrets_next = now + SECRETS_RETRY_S
        self._secrets_for = key
        loader = self._secrets_loader or default_secrets_loader
        try:                                        # room's call: BridgeSecrets.load(hue.bridge, hue.host)
            self._secrets = loader(str(bridge) if bridge is not None else None, str(host) if host else None)
            self._secrets_error = None
        except Exception as e:                      # noqa: BLE001  a pairing file that doesn't read
            self._secrets = None
            self._secrets_error = f"the pairing file for bridge {bridge} doesn't read ({type(e).__name__})"
            self._log_limited("secrets", "hue %s: %s", self.output_id, self._secrets_error)
        return self._secrets

    def _get_client(self, secrets: BridgeSecrets) -> Any:
        k = (secrets.bridge_id, secrets.host)
        if self._client is None or self._client_for != k:
            old = self._client
            if old is not None and hasattr(old, "close"):
                asyncio.get_running_loop().create_task(old.close())
            factory = self._client_factory or default_client_factory
            self._client, self._client_for = factory(secrets), k
        return self._client

    def _get_streamer(self) -> Any:
        if self._streamer is None:
            factory = self._streamer_factory or default_streamer_factory
            self._streamer = factory()
        return self._streamer

    async def _get_area(self) -> dict:
        got = await self._client.get(_AREA, self._area)
        if not got or not isinstance(got[0], dict):
            raise HueConfigError(404, f"entertainment area {self._area} is gone from the bridge")
        return got[0]

    def _set(self, state: str, detail: Optional[str]) -> None:
        if state != self._state:
            log.info("hue %s: %s%s", self.output_id, state, f" ({detail})" if detail else "")
        self._state, self._detail = state, detail

    def _off_detail(self) -> str:
        return f"outputs.{self.output_id}.enabled is false in config/room/fixtures.yaml"

    def _on_loop(self) -> bool:
        try:
            return self._loop is not None and asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def _spawn(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if self._on_loop():
            self._task = loop.create_task(self._control(), name=f"hue-out-{self.output_id}")
            return
        self._spawn_pending = True

        def make() -> None:
            if not self._stopping and (self._task is None or self._task.done()):
                self._task = loop.create_task(self._control(), name=f"hue-out-{self.output_id}")
            else:
                self._spawn_pending = False
        try:
            loop.call_soon_threadsafe(make)
        except RuntimeError:
            self._spawn_pending = False

    def _poke(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        if self._on_loop():
            self._wake.set()
        else:
            try:
                loop.call_soon_threadsafe(self._wake.set)
            except RuntimeError:
                pass

    def _log_limited(self, key: str, msg: str, *args: Any) -> None:
        now = self.mono()
        if now - self._log_t.get(key, -math.inf) >= LOG_EVERY_S:
            self._log_t[key] = now
            log.warning(msg, *args)
