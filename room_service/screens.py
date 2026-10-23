"""Screens subsystem: one state machine per screen (AV_PROGRAMME §3.3, LIVE_V1 §2).

Each `ScreenController` owns one task (`run`) that reconciles *desired* (art, or
a live spec) with what it believes the screen shows, by running the handshakes:

  entry: freeze -> ack frozen -> snapshot -> POST session (anchor, display size) -> session.ready
         -> live.attach -> ack live_visible -> settle (~1 s) -> POST go -> unfreeze
  exit:  freeze -> ack frozen -> snapshot -> POST exit (target) -> session.held
         -> live.leave{hold_pts} -> ack art_visible -> DELETE session -> unfreeze

Every wait has the programme's timeout. A failure aborts cleanly (live.abort if
attached, DELETE the session, unfreeze) and leaves the screen in art.

Inbound page messages and stream events arrive on other tasks. They go into the
controller's Mailbox (the handshake scans it from a mark taken before each
request, so nothing racing an HTTP response is lost) and, for unsolicited
events while live (a stall, a stream error), set `_fallback_pending` and wake
the run task. All state changes happen on the one event loop, so there are no
data races, only interleavings, which the run task serialises.

Desired changes mid-handshake are queued: the run task re-reads desired after
each handshake. The one exception is switching to art during an entry before
`go`, which aborts the entry promptly (an invisible abort: the page is still on
art, or on the anchor that matches it).
"""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import math
import time
from collections import deque
from pathlib import Path
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Callable

import aiohttp

from .pages import CLOSE_REPLACED, PageClient, PageGone
from .live_art import NEUTRAL_DIRECTION, auto_block, carried_look
from .snapshot import SnapshotError, origin_of, safe_art_state
from .stream_client import StreamError, StreamUnreachable
from .util import (FileWriter, Mailbox, Notifier, describe_exc, fit_pixels, media_size, read_json, spawn, utc_iso,
                   wait_until)

if TYPE_CHECKING:
    from .config import RoomConfig, ScreenConfig
    from .events import EventHub
    from .mask import MaskPusher
    from .pages import PageHub
    from .snapshot import Snapshotter
    from .stream_client import StreamClient

log = logging.getLogger("room_service.screens")

SHOT_REUSE_S = 600.0     # reuse a snapshot for an identical ArtState at most this old
RENDERER_GONE_S = 30.0    # a fallback this soon after the renderer dropped is its restart, not the screen's fault
CARRY_S = 600.0          # a re-created session takes over the old one's look if it ended at most this long ago

async def bounded(coro: Any, timeout_s: float) -> Any:
    """Await ``coro`` as its own task for at most ``timeout_s`` (asyncio.TimeoutError after).  On a timeout the task is
    cancelled but never awaited, so a call that ignores its cancel (a wedged browser call or child process; 23 Oct:
    south's exit sat in phase "snapshot" for 2+ min) can't hold a handshake: it carries on without it."""
    task = asyncio.ensure_future(coro)
    try:
        done, _ = await asyncio.wait({task}, timeout=timeout_s)
    except asyncio.CancelledError:
        task.cancel()
        raise
    if not done:
        task.cancel()
        task.add_done_callback(_reap)
        raise asyncio.TimeoutError
    return task.result()


def _reap(task: "asyncio.Future") -> None:
    if not task.cancelled() and task.exception() is not None:
        log.info("an abandoned call finished late: %s", describe_exc(task.exception()))


PHASES = ("idle", "freezing", "snapshot", "starting", "attaching", "entering", "live",
          "exiting", "leaving", "failed")


# ---------------------------------------------------------------------------
# Desired-state values
# ---------------------------------------------------------------------------

class LiveDisabled(Exception):
    pass


def normalize_desired(value: Any) -> Any:
    """'art' or {'live': {scene?, scene_tags?, auto?, transitions?{in,out}, params?, profile?}}.

    Accepts the LIVE_V1 form `{live: {scene, transitions, params, profile}}`, the
    programme's short form `{live: "fractal-zoom", in: "...", out: "..."}`, and
    `{live: {scene, in, out}}`. A mode names no scene (owner, 15 Oct: "It's the Art Director's
    job to determine Scene"): `{live: {scene_tags: [...]}}` or `{live: {auto: true}}` is valid, and
    the first scene is the director's pick from that pool. Raises ValueError otherwise.
    """
    if value is None or value == "art":
        return "art"
    if not isinstance(value, dict) or "live" not in value:
        raise ValueError("desired must be 'art' or {'live': {...}}")
    live = value["live"]
    if isinstance(live, str):              # {live: "fractal-zoom", in: ..., out: ...}
        src: dict = {"scene": live, "in": value.get("in"), "out": value.get("out"),
                     "transitions": value.get("transitions"),
                     "params": value.get("params"), "profile": value.get("profile")}
    elif isinstance(live, dict) and "scene" in live:
        src = dict(live)
    elif isinstance(live, dict) and ("preset" in live or "source" in live):
        src = {"scene": live}              # a bare scene spec ({preset|source, params})
    elif isinstance(live, dict) and (live.get("scene_tags") or live.get("direction")
                                     or live.get("auto") not in (None, False)):
        src = dict(live)                   # no scene: the director picks from the tags' (or its whole) pool
    else:
        raise ValueError("live needs a scene, or scene_tags / direction / auto for the art director to pick one")
    scene = src.get("scene")
    if scene is not None and (not scene or not isinstance(scene, (str, dict))):
        raise ValueError("live scene must be a preset name or a spec")
    out: dict[str, Any] = {"scene": copy.deepcopy(scene)} if scene is not None else {}
    tr = dict(src.get("transitions") or {})
    for k in ("in", "out"):
        if src.get(k) is not None and k not in tr:
            tr[k] = src[k]
    tr = {k: copy.deepcopy(v) for k, v in tr.items() if k in ("in", "out") and v is not None}
    if tr:
        out["transitions"] = tr
    if src.get("params"):
        if not isinstance(src["params"], dict):
            raise ValueError("params must be an object")
        out["params"] = copy.deepcopy(src["params"])
    if src.get("profile"):
        out["profile"] = str(src["profile"])
    if src.get("auto") is not None:            # the director: true | false | an auto block (live_art.auto_block)
        if not isinstance(src["auto"], (bool, dict)):
            raise ValueError("auto must be true, false or an auto block")
        out["auto"] = copy.deepcopy(src["auto"])
    if src.get("scene_tags"):                  # the director picks only scenes tagged modes: [<one of these>]
        tags = [src["scene_tags"]] if isinstance(src["scene_tags"], str) else src["scene_tags"]
        if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
            raise ValueError("scene_tags must be a list of tag names")
        out["scene_tags"] = list(tags)
    if src.get("direction") is not None:      # the art director's steer (energy, pace, moods, style...: catalog.vocab)
        if not isinstance(src["direction"], dict):
            raise ValueError("direction must be an object")
        out["direction"] = copy.deepcopy(src["direction"])
    return {"live": out}


def spec_name(spec: Any) -> str:
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        return str(spec.get("preset") or spec.get("source") or spec.get("transition") or "custom")
    return "?"


def desired_label(d: Any) -> str:
    if d == "art":
        return "art"
    live = d["live"]
    if live.get("scene") is not None:
        return f"live:{spec_name(live['scene'])}"
    tags = live.get("scene_tags")
    return f"live:#{'+'.join(tags)}" if tags else "live:director"


def scene_with_params(live: dict) -> Any:
    """The scene spec to send, with `params` folded in (LIVE_V1 §1.1 spec forms)."""
    scene, params = live.get("scene"), live.get("params")
    if scene is None:
        return None                        # no scene named: the create sends "auto" (the director's first pick)
    if not params:
        return copy.deepcopy(scene)
    if isinstance(scene, str):
        return {"preset": scene, "params": copy.deepcopy(params)}
    merged = dict(scene)
    merged["params"] = {**(scene.get("params") or {}), **params}
    return merged


# ---------------------------------------------------------------------------
# Handshake exceptions
# ---------------------------------------------------------------------------

class HandshakeError(Exception):
    def __init__(self, step: str, detail: str, *, transient: bool = False) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail
        self.transient = transient      # stream_service unreachable: not the screen's fault


class EntryAborted(Exception):
    pass


class PageInArt(Exception):
    """During an exit the page reported that it is already showing art."""


# ---------------------------------------------------------------------------
# One screen
# ---------------------------------------------------------------------------

class ScreenController:
    def __init__(self, mgr: "ScreensManager", sc: "ScreenConfig", saved: dict | None) -> None:
        self.mgr = mgr
        self.cfg = sc
        self.id = sc.id
        self.t = mgr.cfg.timeouts
        self.desired: Any = "art"
        self.phase = "idle"
        self.detail = ""
        self.layer = "art"
        self.session: dict | None = None   # {id, attach, profile, live, state, created_out}
        self.frozen_fid: str | None = None
        self.last_error: str | None = None
        self.last_ack_at: str | None = None
        self.kiosk_state: dict | None = None
        self.art_shape: tuple[int, int] | None = None   # the art's own size at the last entry (live takes its shape)
        # Live diagnostics burned into the stream (docs/LIVE_DIAGNOSTICS.md): wanted by the diag.<screen> target (aa's
        # DiagAdapter calls set_hud), applied to the live session, and the renderer's words if it stopped the panel.
        self.hud = False
        self.hud_applied = False
        self.hud_failed: str | None = None
        self._hud_task: asyncio.Task | None = None
        self._art_sizes: dict[tuple[str, int], tuple[int, int] | None] = {}
        self.box = Mailbox()
        self.changed = Notifier()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._handshake: str | None = None
        self._abort_entry = False
        self._fallback_pending: str | None = None
        self._stale_session = False
        self._after_fallback = False        # the next entry is a re-anchor
        self.fallbacks: deque[float] = deque()
        self._retry_idx = 0
        self._entry_failures = 0
        self._next_attempt_at = 0.0
        self._live_since: float | None = None
        self._attempt = 0
        self._driven: PageClient | None = None
        self.page_load_id: str | None = None
        self._page_lost_at: float | None = None
        self._adopt_pending = True          # until the startup session listing has run
        self._adopt: dict | None = None     # {"cands": {sid: summary}, "deadline": t}
        self._timer: asyncio.TimerHandle | None = None
        self._entering_timer: asyncio.TimerHandle | None = None
        self._warm_task: asyncio.Task | None = None
        self.anchor_png: bytes | None = None
        self.target_png: bytes | None = None
        self._prefetched: str | None = None
        self._stale_fid: str | None = None   # a freeze left open by a previous process
        self._hello_same_page: bool | None = None   # the last glass hello: same page_load_id as persisted?
        self.timed_reload: bool = sc.timed_reload   # page policy (a mode can change it)
        self._shot: tuple[str, bytes, float] | None = None   # (ArtState json, PNG, loop time)
        # Show continuity: the look of this screen's last live session, read just before room deleted it while the
        # screen was still meant to be live; the next session it creates starts from it (_session_look).
        self._carry: dict | None = None
        # A screen renders live whether or not a page shows it: a session started with no page (or whose page
        # left for good) carries session["headless"]; a page that connects joins it (attach, no re-entry).
        self._join_cid: str | None = None    # the page sent the attach for the headless session
        # Pub/sub: every page but the driven glass (standby glass, viewers) is a subscriber, shown what this screen
        # shows. The live session they were last sent ({session, attach}); None: art (_publish).
        self._sub: dict | None = None
        # This screen's own glass: the device (pages.device_of) of the page its live shows are shaped for, the TV.
        # A page from another device (a phone, a laptop) never takes the glass from it (on_glass_hello). Persisted.
        self.glass_device: str | None = None
        # PUT /screens/{id} {desired, for_s}: {value, revert, deadline (unix s)}; persisted, so a restart keeps it.
        self.expiry: dict | None = None
        self._expiry_handle: asyncio.TimerHandle | None = None
        self._restore(saved or {})

    # -- persistence -------------------------------------------------------
    def _restore(self, s: dict) -> None:
        try:
            self.desired = normalize_desired(s.get("desired", "art"))
        except ValueError:
            self.desired = "art"
        if self.desired != "art" and not self.cfg.live_enabled:
            self.desired = "art"
        sess = s.get("session")
        if isinstance(sess, dict) and sess.get("id"):
            self.session = sess
            self.layer = "live" if s.get("layer") == "live" else "art"
            att = self._attach_fields(sess) if self.layer == "live" else None
            # Subscribers may still be playing it: if adoption drops it, they are told (live.abort; _publish).
            self._sub = {"session": sess["id"], "attach": att} if att else None
        self.page_load_id = s.get("page_load_id")
        self.glass_device = s.get("glass_device") or None
        self._stale_fid = s.get("frozen_fid")
        self.last_error = s.get("last_error")
        self.fallbacks = deque(float(x) for x in (s.get("fallbacks") or []))
        if isinstance(s.get("timed_reload"), bool):
            self.timed_reload = s["timed_reload"]
        e = s.get("expiry")
        if isinstance(e, dict):
            try:
                self.expiry = {"value": normalize_desired(e["value"]), "revert": normalize_desired(e["revert"]),
                               "deadline": float(e["deadline"])}
            except (KeyError, TypeError, ValueError):
                self.expiry = None
        sd = self.mgr.snapshots_dir
        for kind in ("anchor", "target"):
            p = sd / f"{self.id}-{kind}.png"
            if p.exists():
                try:
                    setattr(self, f"{kind}_png", p.read_bytes())
                except OSError:
                    pass

    def snapshot_state(self) -> dict:
        return {"desired": self.desired, "layer": self.layer, "phase": self.phase,
                "session": self.session, "page_load_id": self.page_load_id, "glass_device": self.glass_device,
                "frozen_fid": self.frozen_fid or self._stale_fid,
                "last_error": self.last_error, "fallbacks": list(self.fallbacks),
                "timed_reload": self.timed_reload, "expiry": self.expiry,
                "saved_at": utc_iso()}

    def _changed(self) -> None:
        self._publish()                      # subscribers follow every state change (idempotent)
        self.mgr.persist()
        self.mgr.events.mark_dirty()
        self.changed.notify()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        self._task = spawn(self.run(), f"screen-{self.id}")
        self._arm_expiry(min_delay=1.0)      # a restored for_s deadline (past: revert just after start)
        self.wake()

    async def stop(self) -> None:
        for h in (self._timer, self._entering_timer, self._expiry_handle):
            if h:
                h.cancel()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    def _wake_at(self, when: float) -> None:
        loop = asyncio.get_running_loop()
        if self._timer:
            self._timer.cancel()
        self._timer = loop.call_at(when, self.wake)

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("%s: reconcile crashed", self.id)
                self.last_error = f"internal: {describe_exc(e)}"
                self._changed()

    # -- small helpers -----------------------------------------------------
    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _set_phase(self, phase: str, detail: str = "") -> None:
        if phase == self.phase and detail == self.detail:
            return
        self.phase, self.detail = phase, detail
        log.info("%s: phase %s%s", self.id, phase, f" ({detail})" if detail else "")
        self.mgr.events.emit("screen.phase", {"screen": self.id, "phase": phase, "detail": detail})
        self._changed()

    def _page(self) -> PageClient | None:
        """The driven glass client, if it is connected (see on_glass_hello)."""
        d = self._driven
        return d if d is not None and not d.closed and d.hello is not None else None

    def _send(self, page: PageClient | None, type_: str, data: dict) -> None:
        if page is None:
            raise PageGone("no page")
        page.send(type_, data)

    def _log(self, level: str, msg: str) -> None:
        getattr(log, level)("%s: %s", self.id, msg)
        self.mgr.events.log_event(level, f"{self.id}: {msg}")

    def _scan(self, since: int, pred: Callable[[dict], bool]) -> dict | None:
        for seq, item in list(self.box._items):
            if seq > since and pred(item):
                return item
        return None

    async def _wait(self, since: int, pred: Callable[[dict], bool], timeout: float, what: str, *,
                    page: PageClient | None = None, abortable: bool = False, sid: str | None = None,
                    page_art_sid: str | None = None) -> dict:
        """Scan the mailbox from `since` for an item matching pred, with a timeout.

        Raises HandshakeError on timeout or a stream error for `sid`, PageGone if `page`
        goes, EntryAborted if `abortable` and desired switched to art, PageInArt if the
        page reports art_visible for `page_art_sid`.
        """
        deadline = self._now() + timeout
        while True:
            if abortable and self._abort_entry:
                raise EntryAborted()
            if page is not None and page.closed:
                raise PageGone(page.cid)
            remaining = deadline - self._now()
            if remaining <= 0:
                raise HandshakeError(what, f"timeout after {timeout:g} s")
            got = await self.box.next(since, remaining)
            if got is None:
                continue
            since, item = got
            if pred(item):
                return item
            src, typ = item.get("src"), item.get("type")
            if src == "internal":
                if typ == "page_gone" and page is not None and item.get("cid") == page.cid:
                    raise PageGone(page.cid)
                if typ == "abort_entry" and abortable:
                    raise EntryAborted()
            elif src == "stream" and sid is not None and item.get("session") == sid:
                if typ in ("session.error", "session.ended") or (
                        typ == "session.state" and item.get("state") in ("failed", "ended")):
                    raise HandshakeError(what, f"stream {typ}: {item.get('detail') or item.get('reason') or item.get('state')}")
            elif (src == "page" and page_art_sid is not None and typ == "ack"
                  and item["data"].get("kind") == "art_visible" and item["data"].get("session") == page_art_sid):
                raise PageInArt(item["data"].get("reason"))

    @staticmethod
    def _is_ack(item: dict, kind: str | tuple, **match: Any) -> bool:
        if item.get("src") != "page" or item.get("type") != "ack":
            return False
        d = item.get("data") or {}
        kinds = (kind,) if isinstance(kind, str) else kind
        return d.get("kind") in kinds and all(d.get(k) == v for k, v in match.items())

    @staticmethod
    def _is_stream(item: dict, types: tuple, sid: str) -> bool:
        return item.get("src") == "stream" and item.get("type") in types and item.get("session") == sid

    async def _art_shape_of(self, state: Any) -> tuple[int, int] | None:
        """The art's own pixel size: the file behind the page's base_url (/output/<file>), read from its header."""
        url = state.get("base_url") if isinstance(state, dict) else None
        if not isinstance(url, str) or not url.startswith("/output/"):
            return None
        root = Path(self.mgr.cfg.output_dir).resolve()
        path = (root / url[len("/output/"):].split("?", 1)[0]).resolve()
        if root not in path.parents:
            return None
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        if key not in self._art_sizes:
            if len(self._art_sizes) > 32:
                self._art_sizes.clear()
            self._art_sizes[key] = await asyncio.get_running_loop().run_in_executor(None, media_size, path)
        return self._art_sizes[key]

    def _shaped(self, state: Any) -> Any:
        """The ArtState to snapshot, in the live render's shape (the art's own, else the display's): the anchor is
        then exactly the art, and the page fits the video into the same box as the art (object-fit contain)."""
        if not isinstance(state, dict):
            return state
        return {**state, "viewport": {"w": self.art_shape[0], "h": self.art_shape[1]} if self.art_shape else None}

    def _render_size(self, profile: str) -> dict[str, int]:
        """POST /api/sessions {width, height}: the art's shape at the profile's pixel count, so live sits where the
        art sat (a browser window's size never matters). No art size known: the display's maxwidth x maxheight
        (the art generator's bounds), scaled down to the profile when larger."""
        prof = ((self.mgr.stream.cached_catalog() or {}).get("profiles") or {}).get(profile) or {}
        pw, ph = prof.get("w"), prof.get("h")
        budget = pw * ph if isinstance(pw, int) and isinstance(ph, int) and pw > 0 and ph > 0 else None
        if self.art_shape and budget:
            return fit_pixels(*self.art_shape, budget)
        w, h = self.cfg.width, self.cfg.height
        if not w or not h:
            return {}
        return fit_pixels(w, h, min(w * h, budget or w * h))

    def _create_failed(self, e: StreamError) -> HandshakeError:
        """A failed POST /api/sessions as a handshake error (a timeout: the renderer is busy, e.g. a cold GPU)."""
        detail = f"renderer busy ({e.detail})" if e.code == "timeout" else str(e)
        # Only "stream_service is down" is transient; a timeout while its events socket is up counts as an
        # entry failure (else a stuck service would loop forever).
        return HandshakeError("create session", detail,
                              transient=isinstance(e, StreamUnreachable) and not self.mgr.stream.reachable)

    async def _settle(self, since: int, seconds: float, *, page: PageClient | None, sid: str | None) -> None:
        """Wait `seconds` with nothing to wait for, still aborting on desired art, the page going or a stream
        error for `sid`."""
        if seconds <= 0:
            return
        try:
            await self._wait(since, lambda i: False, seconds, "settle", page=page, abortable=True, sid=sid)
        except HandshakeError as e:
            if not e.detail.startswith("timeout after"):
                raise

    async def _snapshotter_ready(self) -> None:
        """Before a freeze: make sure the art-only page is loaded, so the 3 s snapshot
        budget covers only render + capture (a cold Chrome takes longer than that)."""
        ready = getattr(self.mgr.snapshotter, "ensure_ready", None)
        if ready is None:
            return
        self.detail = "waiting for the snapshotter"
        self._changed()
        scfg = getattr(getattr(self.mgr, "cfg", None), "snapshot", None)
        bound = float(getattr(scfg, "page_load_timeout_s", 20.0)) + 5.0 + self.t.snapshot_s   # its own + its lock
        try:
            await bounded(ready(self.id), bound)
        except asyncio.TimeoutError:
            raise HandshakeError("snapshotter", f"not ready after {bound:g} s") from None
        except Exception as e:
            raise HandshakeError("snapshotter", describe_exc(e)) from None

    def _prefetch(self, client: PageClient, data: dict) -> None:
        """kiosk.state shows a new still: decode it in the snapshot page ahead of any cut."""
        url = data.get("base_url")
        if not url or url == self._prefetched or not self.cfg.live_enabled or self._handshake is not None:
            return                       # (mid-handshake a prefetch would only hold up the snapshot)
        fn = getattr(self.mgr.snapshotter, "prefetch", None)
        if fn is None:
            return
        self._prefetched = url
        flags = dict((client.hello or {}).get("flags") or {})
        kind = "video" if str(url).split("?", 1)[0].lower().endswith((".mp4", ".webm", ".mov")) else "image"
        try:
            state = safe_art_state({"base_url": url, "kind": kind, "clip_t": None, "mask": data.get("mask") or {},
                                    "flags": {"nomask": bool(flags.get("nomask")),
                                              "nocolour": bool(flags.get("nocolour")),
                                              "fit": flags.get("fit") or "fit"}}, self.mgr.art_origins)
        except SnapshotError:
            return
        fn(self.id, state)

    async def _snapshot(self, state: Any) -> bytes:
        if not isinstance(state, dict) or not state.get("base_url"):
            raise HandshakeError("snapshot", "ack frozen carried no usable ArtState")
        try:
            # Any LAN client can send this ack: only /output/ stills reach the snapshotter.
            state = safe_art_state(state, self.mgr.art_origins)
        except SnapshotError as e:
            raise HandshakeError("snapshot", str(e)) from None
        # The snapshot is a pure function of the ArtState (LIVE_V1 §2.3), so an identical
        # state (same base URL, which the page cache-busts, same mask, flags and viewport)
        # reuses the last PNG instead of another 1-3 s 4K capture. Typical on exit with a
        # held mask (sensor_mask: suspended) or steady light. Bounded in age, to be safe.
        key = json.dumps(state, sort_keys=True, default=str)
        if self._shot is not None and self._shot[0] == key and self._now() - self._shot[2] < SHOT_REUSE_S:
            self.detail = "snapshot reused (identical ArtState)"
            return self._shot[1]
        try:
            png = await bounded(self.mgr.snapshotter.snapshot(self.id, state), self.t.snapshot_s)
        except asyncio.TimeoutError:
            raise HandshakeError("snapshot", f"timeout after {self.t.snapshot_s:g} s") from None
        except Exception as e:
            raise HandshakeError("snapshot", describe_exc(e)) from None
        if not png or not png.startswith(b"\x89PNG"):
            raise HandshakeError("snapshot", "snapshotter returned no PNG")
        self._shot = (key, png, self._now())
        return png

    def _keep_png(self, kind: str, png: bytes) -> None:
        setattr(self, f"{kind}_png", png)
        # ~6 MB at 4K: written in a worker thread, never on the loop mid-handshake.
        self.mgr.files.write(self.mgr.snapshots_dir / f"{self.id}-{kind}.png", png, mode=0o644)

    def _new_fid(self) -> str:
        self._attempt += 1
        return f"f-{self.mgr.epoch}-{self.id}-{self._attempt}"

    def _unfreeze(self, page: PageClient | None) -> None:
        fid, self.frozen_fid = self.frozen_fid, None
        if fid is None:
            return
        target = page if (page is not None and not page.closed) else self._page()
        sent = False
        if target is not None:
            try:
                target.send("unfreeze", {"fid": fid})
                sent = True
            except PageGone:
                pass
        if not sent:
            # No page to tell right now (its socket dropped mid-handshake). The page stays
            # frozen across a reconnect, so release it on the next hello rather than leaving
            # it to its 30 s safety timeout (on_page_hello sends it; a reloaded page ignores it).
            self._stale_fid = fid
        self.mgr.mask.on_unfrozen(self.id)

    async def _stash_look(self, sid: str) -> None:
        """Read the look of our live session before it is deleted while the screen should stay live."""
        try:
            sess = await asyncio.wait_for(self.mgr.stream.get_session(sid), 3.0)
        except (StreamError, asyncio.TimeoutError):
            return
        if sess:
            cur = self.session if (self.session or {}).get("id") == sid else None
            live = (cur or {}).get("live") or (self.desired.get("live") if isinstance(self.desired, dict) else None)
            self._carry = {"at": self._now(), "live": copy.deepcopy(live), **carried_look(sess)}

    async def _session_look(self, live: dict) -> tuple[dict, Any, list]:
        """(look fields for the create body, a scene to start on or None, FX stack entries to restore).

        The display's default director (live.auto) overlaid by the mode's auto/scene_tags; or, when this screen
        was live a moment ago, the old session's look: direction, palette and hand-placed FX always, and its
        director block and current scene too when the live value is unchanged (a reload, fallback or restart)."""
        out: dict[str, Any] = {}
        scene, fx = None, []
        c = self._carry
        if c is not None and self._now() - c["at"] <= CARRY_S:
            out.update(copy.deepcopy(c["look"]))
            if c.get("live") != live:
                # A different mode: the old mode's direction doesn't carry (2b's audit, 17 Oct); its own, or neutral.
                out.pop("direction", None)
            fx = copy.deepcopy(c["fx"])
            if c.get("live") == live:
                if c.get("auto") is not None:
                    out["auto"] = copy.deepcopy(c["auto"])
                scene = c.get("scene")
        if live.get("direction") is not None and not (c is not None and c.get("live") == live and "direction" in out):
            out["direction"] = copy.deepcopy(live["direction"])
        if "auto" not in out:
            try:
                auto = await auto_block(live, self.cfg.auto, self.mgr.stream)
            except StreamError as e:
                self._log("warning", f"scene_tags not resolved ({e}); using the display's default director")
                auto = copy.deepcopy(self.cfg.auto)
            if auto is not None:
                out["auto"] = auto
        return out, scene, fx

    async def _restore_fx(self, sid: str, fx: list) -> None:
        if not fx:
            return
        try:
            await self.mgr.stream.put_fx(sid, {"stack": fx, "fade_s": 0})
            self._log("info", f"carried {len(fx)} effect(s) over to {sid}")
        except StreamError as e:
            self._log("warning", f"effects not carried over to {sid}: {e}")

    async def _delete_session(self, sid: str) -> None:
        # Ours (owned by this screen) and the current one, or the one just dropped (a fallback clears it first);
        # never a stray the sweep finds while another session is live.
        if (self.desired != "art" and (self.mgr.owned.get(sid) or {}).get("screen") == self.id
                and (self.session is None or self.session.get("id") == sid)):
            await self._stash_look(sid)
        try:
            await self.mgr.stream.delete(sid)       # True, or False for a 404: gone either way
            self.mgr.disown(sid)
        except StreamError as e:
            self._log("warning", f"DELETE {sid} failed: {e}; the orphan sweep (or its TTL) will end it")
            self.mgr.request_sweep(5.0)

    def live_info(self) -> dict | None:
        """welcome.live: what room_service believes the page plays.

        `attach` is deliberately null. A page that reconnects while still playing keeps
        playing whatever welcome says (its attach is idempotent), so attach adds nothing
        there; but a page that fell back to art while its socket was down (its stall ack
        lost) would warm the stream up and cut straight into the running scene: an
        unmatched, visible cut. Without attach it stays in art, its next kiosk.state says
        so, and re-entry runs through the full freeze/anchor handshake instead.
        """
        if not self.session or self.layer != "live":
            return None
        return {"session": self.session["id"], "attach": None, "phase": self.phase}

    # -- headless live: the session runs whether or not a page shows it --------------
    def _headless(self) -> bool:
        return bool(self.session and self.session.get("headless"))

    @staticmethod
    def _attach_fields(sess: dict) -> dict | None:
        a = sess.get("attach") or {}
        if not a.get("url"):
            return None
        return {k: a.get(k) for k in ("url", "codec", "w", "h", "fps", "target_buffer_ms")}

    def running_live(self) -> dict | None:
        """welcome.live *with* attach, for a page joining a session that runs on its own (a headless one, or
        any running one for a viewer): it attaches to the media socket and cuts in; no re-entry, no new session."""
        s = self.session
        if not s or self.layer != "live" or self._handshake is not None:
            return None
        att = self._attach_fields(s)
        return {"session": s["id"], "attach": att, "phase": self.phase} if att else None

    def _join_page(self) -> None:
        """A headless session and a driven page that hasn't been given it yet: attach it (the welcome normally does)."""
        page = self._page()
        if not self._headless() or page is None or self._join_cid == page.cid:
            return
        info = self.running_live()
        if info is None:
            return
        self._join_cid = page.cid
        try:
            page.send("live.attach", {"session": info["session"], **info["attach"]})
            self._log("info", f"page {page.cid} joins the running session {info['session']}")
        except PageGone:
            self._join_cid = None

    def _joined(self, how: str) -> None:
        """The driven page shows the headless session: from now on it is an ordinary page-backed live session."""
        if not self._headless():
            return
        self.session.pop("headless", None)
        self._join_cid = None
        self._log("info", f"page joined live session {self.session['id']} ({how})")
        self._changed()

    # -- subscribers (pub/sub): every page of the screen but the driven glass, any number --------------
    # They are shown what the screen shows: the running session (attach) from `go` until the screen leaves live,
    # art otherwise. They drive nothing (on_subscriber_message): an ack only earns a page that cut to art by itself
    # a rejoin offer, and kiosk.state only lets this screen's own glass take the glass back.
    def _adopting(self) -> bool:
        return self._adopt_pending or self._adopt is not None or self._handshake == "adopt"

    def _sub_target(self) -> dict | None:
        s = self.session
        if not s or self.layer != "live" or s.get("gone"):
            return None
        if self._handshake is not None or self._adopting():
            # Mid-entry: nothing until `go`. Mid-exit, and while a restart adopts the session: subscribers keep what
            # they were shown until the screen cuts to art or the session is dropped.
            keep = self._sub if (self._handshake == "exit" or self._adopting()) else None
            return keep if keep is not None and keep["session"] == s["id"] else None
        att = self._attach_fields(s)
        return {"session": s["id"], "attach": att} if att else None

    @staticmethod
    def _sub_attach(sub: dict) -> dict:
        """live.attach for a subscriber. `rejoin`: it plays the session even if it ended it itself (DisplayController);
        the driven glass never gets it."""
        return {"session": sub["session"], **sub["attach"], "rejoin": True}

    def _to_subscribers(self, type_: str, data: dict) -> int:
        n = 0
        for c in self.mgr.pages.hello_clients(self.id):
            if c is self._driven or not c.welcomed:      # (a page still being welcomed gets it in its welcome)
                continue
            try:
                c.send(type_, data)
                n += 1
            except PageGone:
                pass
        return n

    def _publish(self, reason: str = "", *, drop: bool = False, resend: bool = False) -> None:
        """Bring the subscribers to what the screen shows: live.attach when a session goes live or replaces another
        (the page swaps sessions itself), live.abort when the screen leaves live (a clean cut to art; no ack is
        needed or read). Idempotent: run on every _changed() and right where a session is dropped.
        `drop`: the session they were shown is being dropped (cut them now, before the DELETE). `resend`: send the
        attach even if unchanged (after restart adoption: a no-op for a page playing it; pages that connected
        meanwhile, or ended it, join)."""
        want, had = (None if drop else self._sub_target()), self._sub
        if (want or {}).get("session") == (had or {}).get("session") and not (resend and want is not None):
            return
        self._sub = want
        if want is not None:
            type_, data = "live.attach", self._sub_attach(want)
        else:
            type_, data = "live.abort", {"session": had["session"], "reason": (reason or "left live")[:80]}
        n = self._to_subscribers(type_, data)
        if n:
            log.info("%s: %s %s to %d subscriber page(s)", self.id, type_, data["session"], n)

    def subscriber_live(self) -> dict | None:
        """welcome.live for a subscriber: the running session with attach (it joins and cuts in), else None (art).
        A page arriving mid-exit stays in art, where the screen is going; one arriving while a restart adopts the
        session waits for the adoption (_finish_adoption resends the attach). welcome.live None never stops a page
        that is still playing."""
        self._publish()
        a = self._sub
        if a is None or self._handshake is not None or self._adopting():
            return None
        att = self._sub_attach(a)
        att.pop("session")
        return {"session": a["session"], "attach": att, "phase": self.phase}

    # A subscriber that cut to art by itself while the screen stays live (a stall: a hidden tab, a Wi-Fi drop; a
    # warm-up timeout; a player error) is offered the session again: live.attach{rejoin} to that page only, after a
    # backoff that grows with each failure in a row, topping out at 30 s, never giving up (S14). A page that can't
    # play it at all is not asked again.
    REJOIN_BACKOFF_S: tuple[float, ...] = (2.0, 4.0, 8.0, 15.0, 30.0)
    REJOIN_NEVER = ("unsupported-codec", "live_disabled", "session-ended")   # session-ended: a build without rejoin

    def on_subscriber_message(self, client: PageClient, mtype: str, data: dict) -> None:
        """A message from a page that isn't the driven glass. It drives nothing: an ack may earn a rejoin offer, and
        a standby glass page's kiosk.state may give it the glass back (_reclaim)."""
        if mtype == "ack":
            self._rejoin_later(client, data)
        elif mtype == "kiosk.state" and client.glass:
            self._reclaim(client, data)

    def _rejoin_later(self, client: PageClient, ack: dict) -> None:
        sub, sid = self._sub, ack.get("session")
        if sub is None or sid != sub["session"]:
            return
        if client.rejoin_sid != sid:
            client.rejoin_sid, client.rejoins = sid, 0
        kind, reason = ack.get("kind"), str(ack.get("reason") or "")
        if kind == "live_visible":
            client.rejoins = 0
            return
        if (kind not in ("art_visible", "live_failed") or reason in ("exit", "abort") or reason in self.REJOIN_NEVER
                or self._handshake is not None or client.rejoin_timer is not None):
            return
        delay = self.REJOIN_BACKOFF_S[min(client.rejoins, len(self.REJOIN_BACKOFF_S) - 1)]
        client.rejoins += 1
        client.rejoin_timer = asyncio.get_running_loop().call_later(delay, self._rejoin, client, sid)

    def _rejoin(self, client: PageClient, sid: str) -> None:
        client.rejoin_timer = None
        sub = self._sub
        if (client.closed or client is self._driven or sub is None or sub["session"] != sid
                or self._handshake is not None):
            return
        try:
            client.send("live.attach", self._sub_attach(sub))
            log.info("%s: %s cut to art by itself; offered %s again (%d)", self.id, client.cid, sid, client.rejoins)
        except PageGone:
            pass

    def policy(self) -> dict:
        """The page Policy (LIVE_V1 §2.1); always sent whole."""
        return {"live_enabled": self.cfg.live_enabled, "mask_push": self.cfg.mask_push,
                "timed_reload": self.timed_reload}

    def set_timed_reload(self, value: bool) -> int:
        """Mode target page.<screen>.timed_reload: record it and tell every page (`policy`)."""
        value = bool(value)
        changed = value != self.timed_reload
        self.timed_reload = value
        n = self.mgr.pages.broadcast(self.id, "policy", self.policy())
        if changed:
            self._log("info", f"page policy timed_reload={str(value).lower()} (sent to {n} page(s))")
        self._changed()
        return n

    def page_timed_reload(self) -> bool | None:
        """timed_reload as the glass page last reported applying it (kiosk.state.policy), if it did."""
        pol = (self.kiosk_state or {}).get("policy")
        if isinstance(pol, dict) and isinstance(pol.get("timed_reload"), bool):
            return pol["timed_reload"]
        return None

    # -- public API (called from the HTTP API, coordinator and hubs) ------
    def set_desired(self, value: Any, source: str = "manual") -> Any:
        """Validate and record a new desired state; the run task acts on it.

        Any explicit request clears a `failed` phase and the retry backoff.
        """
        d = normalize_desired(value)
        if d != "art" and not self.cfg.live_enabled:
            raise LiveDisabled(self.id)
        if self.expiry is not None and d != self.expiry["value"]:
            self.cancel_expiry(f"desired changed ({source})")
        prev = self.desired
        self.desired = d
        if d == "art":
            self._carry = None                   # a fresh show starts from the mode, not the last one's look
        self._entry_failures = 0
        self._retry_idx = 0
        self._next_attempt_at = 0.0
        self._after_fallback = False
        self.fallbacks.clear()
        self.last_error = None                   # S11: a new request starts clean (persisted by _changed)
        if self.phase == "failed":
            self._set_phase("idle", "cleared by a new request")
        if d == "art" and self._handshake == "entry":
            # Abort the entry promptly (it only aborts before `go`; after that it completes
            # and the exit follows).
            self._abort_entry = True
            self.box.put({"src": "internal", "type": "abort_entry"})
        if prev != d:
            log.info("%s: desired %s -> %s (%s)", self.id, desired_label(prev), desired_label(d), source)
        self._changed()
        self.wake()
        return d

    def believed(self) -> Any:
        """What the coordinator should believe this screen is doing (readback)."""
        return "art" if self.phase == "failed" else self.desired

    # -- timed desired: PUT /screens/{id} {desired, for_s} ---------------------------
    def set_expiry(self, value: Any, for_s: float, revert: Any) -> None:
        """After for_s, if desired is still `value` (no other request since: a later PUT without for_s or any other
        value cancels it), put `revert` back through the room state model. A closed tab never leaves a TV live."""
        if self._expiry_handle:
            self._expiry_handle.cancel()
        self.expiry = {"value": normalize_desired(value), "revert": normalize_desired(revert),
                       "deadline": time.time() + float(for_s)}
        log.info("%s: %s for %g s, then back to %s", self.id, desired_label(self.expiry["value"]), for_s,
                 desired_label(self.expiry["revert"]))
        self._arm_expiry()
        self._changed()

    def cancel_expiry(self, why: str) -> None:
        e, self.expiry = self.expiry, None
        if self._expiry_handle:
            self._expiry_handle.cancel()
            self._expiry_handle = None
        if e is not None:
            if not e.get("firing"):
                log.info("%s: for_s timer cancelled (%s)", self.id, why)
            self._changed()

    def expires_in_s(self) -> float | None:
        return None if self.expiry is None else round(max(0.0, self.expiry["deadline"] - time.time()), 1)

    def _arm_expiry(self, min_delay: float = 0.0) -> None:
        if self._expiry_handle:
            self._expiry_handle.cancel()
            self._expiry_handle = None
        if self.expiry is None:
            return
        delay = max(min_delay, self.expiry["deadline"] - time.time())
        self._expiry_handle = asyncio.get_running_loop().call_later(
            delay, lambda: spawn(self._expire(), f"expire-{self.id}"))

    async def _expire(self) -> None:
        self._expiry_handle = None
        e = self.expiry
        if e is None:
            return
        e["firing"] = True
        self._log("info", f"for_s is up: back to {desired_label(e['revert'])}")
        try:
            ok = await self.mgr.revert_desired(self.id, e["revert"])
        except Exception as ex:
            self._log("warning", f"for_s revert failed ({describe_exc(ex)}); setting desired directly")
            try:
                self.set_desired(e["revert"], source="for_s")
            except (LiveDisabled, ValueError):
                self.set_desired("art", source="for_s")
            ok = True
        if self.expiry is e:
            if ok:
                self.expiry = None
                self._changed()
            else:                                  # a mode switch is running: try again shortly
                e.pop("firing", None)
                self._arm_expiry(min_delay=2.0)

    async def wait_settled(self, target: Any, timeout: float) -> tuple[str, str]:
        target = normalize_desired(target)
        # (No page is no reason to wait: a live screen renders whether or not a page shows it.)

        def settled() -> bool:
            if self.phase == "failed" or self.desired != target:
                return True
            if target == "art":
                return self.layer == "art" and self.session is None and self._handshake is None
            return self.layer == "live" and self.phase in ("entering", "live")

        if not await wait_until(settled, self.changed, timeout):
            return "unconfirmed", f"not settled after {timeout:g} s (phase {self.phase})"
        if self.desired != target:
            return "cancelled", "desired changed meanwhile"
        if self.phase == "failed":
            return "failed", self.last_error or "failed"
        return "ok", self.phase

    # -- live diagnostics (docs/LIVE_DIAGNOSTICS.md) ----------------------------------------------------------
    def set_hud(self, value: Any) -> None:
        """diag.<screen>: show the diagnostics panel burned into this screen's live stream (now if live, else on the next
        session). Never blocks; the renderer's own words land in hud_failed."""
        self.hud = bool(value)
        if not self.hud:
            self.hud_failed = None
        sess = self.session
        if sess and sess.get("id") and self.layer == "live":
            spawn(self._hud_apply(sess["id"], self.hud), f"hud-{self.id}")
        else:
            self.hud_applied = False
        if self.hud and self._hud_task is None:
            self._hud_task = spawn(self._hud_watch(), f"hud-watch-{self.id}")
        self._changed()

    async def _hud_apply(self, sid: str, on: bool) -> None:
        try:
            summ = await self.mgr.stream.patch(sid, {"hud": on})
            h = (summ or {}).get("hud") if isinstance(summ, dict) else None
            self.hud_applied = bool(h.get("on")) if isinstance(h, dict) else on
            self.hud_failed = (h or {}).get("failed") if isinstance(h, dict) else None
        except StreamError as e:
            self.hud_applied = False
            self.hud_failed = f"the renderer didn't take it ({e})"
        self._changed()

    async def _hud_watch(self) -> None:
        """While the panel is wanted: read the session's hud summary every 5 s (it may stop itself: "too slow")."""
        try:
            while self.hud:
                sess = self.session
                if sess and sess.get("id") and self.layer == "live":
                    try:
                        summ = await self.mgr.stream.get_session(sess["id"])
                        h = (summ or {}).get("hud")
                        if isinstance(h, dict):
                            applied, failed = bool(h.get("on")), h.get("failed")
                            if (applied, failed) != (self.hud_applied, self.hud_failed):
                                self.hud_applied, self.hud_failed = applied, failed
                                self._changed()
                    except StreamError:
                        pass
                await asyncio.sleep(5.0)
        finally:
            self._hud_task = None

    async def patch_live(self, body: dict) -> Any:
        """PATCH /api/v1/screens/<id>/live: pass the edit to the session, record it in desired."""
        if (not self.session or self.layer != "live" or self.desired == "art"
                or self._handshake is not None):
            raise StreamError("not_live", f"{self.id} is not live", 409)
        sess, before = self.session, self.desired
        res = await self.mgr.stream.patch(sess["id"], body)
        live = copy.deepcopy(before["live"])
        if "scene" in body:
            live["scene"] = body["scene"]
            live.pop("params", None)
        if isinstance(body.get("params"), dict):
            live["params"] = {**(live.get("params") or {}), **body["params"]}
        # The PATCH took a round trip: if desired changed meanwhile (e.g. back to art), that
        # newer request wins; writing ours over it would bring the screen straight back live.
        if self.desired is before:
            self.desired = {"live": live}
        if self.session is sess:
            sess["live"] = copy.deepcopy(live)
        self._changed()
        return res

    # -- inbound: page ----------------------------------------------------
    #
    # Driven glass: identity before recency (DCL v3 §3.2 G1/G2/G4/G5, minimum form). Only the
    # driven client gets commands and has its acks/kiosk.state read; other glass clients are
    # standby: subscribers, like viewers (mask.set, policy, live.attach/leave/abort, hb; see _publish).
    #   G1  no driven glass                         -> the hello becomes driven
    #   G2  same page_load_id as the driven glass   -> replaces its socket (closed 4009), silently
    #   G4  another page load, screen live or in a cut -> standby + screen.duplicate_glass;
    #       it never re-runs entry or orphans the session
    #       (in art and idle, a new page load takes over only from the same device, i.e. a TV reload
    #       whose old socket lingers, or when it is this screen's own glass device coming back; a
    #       phone or laptop opening /display/<id> watches as a subscriber)
    #   G5  the driven glass leaves while live/in a cut -> hold adopt_wait_s for it to come
    #       back, then promote a standby (the screen's own device first, else the newest)
    #   Own glass (glass_device, persisted): the device of the page the last show was shaped for. While the
    #   screen is busy or a restart adopts its session, only that device (or the page load that drove before)
    #   may take an empty glass; others wait on standby until the hold is over. A standby from it takes the
    #   glass back from another device once that is seamless (_reclaim).

    def _busy(self) -> bool:
        """Live, in a cut, or adopting: a different page load must not take the glass now."""
        return (self.session is not None or self._handshake is not None
                or self.frozen_fid is not None or self._adopt is not None)

    def _standbys(self) -> list[PageClient]:
        return [c for c in self.mgr.pages.hello_clients(self.id) if c.glass and c is not self._driven]

    def _duplicate(self, client: PageClient, what: str) -> None:
        n = self.mgr.pages.glass_count(self.id)
        self._log("warning", f"screen.duplicate_glass: {n} glass clients ({client.cid} from "
                             f"{client.remote}, page {client.page_load_id}): {what}")

    def _own(self, client: PageClient) -> bool:
        """Is `client` on this screen's own glass device (the TV)?"""
        return bool(self.glass_device) and client.device == self.glass_device

    def _may_take_empty_glass(self, client: PageClient) -> bool:
        """G1, with no driven page: in art and idle any page may drive. While live, in a cut, or adopting after a
        restart, only this screen's own device or the page load that drove before; another device waits on standby
        (the supervisor promotes it once the hold is over)."""
        if not (self._busy() or self._adopt_pending):
            return True
        if client.page_load_id and client.page_load_id == self.page_load_id:
            return True
        if self.glass_device:
            return self._own(client)
        return not (self._adopting() and self.page_load_id)      # no own device known yet: wait for the last page

    def on_glass_hello(self, client: PageClient) -> dict | None:
        """A glass page said hello. Returns welcome.live (None for a standby: it gets subscriber_live's)."""
        cur = self._page()
        if cur is not None and cur is not client:
            if client.page_load_id and client.page_load_id == cur.page_load_id:
                # G2: the same page load on a new socket (the old one may be half-open).
                log.info("%s: page %s reconnected; %s replaces %s", self.id, client.page_load_id,
                         client.cid, cur.cid)
                self._driven = None
                cur.closed = True
                spawn(cur.ws.close(code=CLOSE_REPLACED, message=b"replaced"), "page-close")
            elif self._busy():
                client.standby = True             # G4
                if client.device == cur.device:  # a second tab on the TV itself is worth a warning; a phone isn't
                    self._duplicate(client, "kept on standby (the screen is live or in a cut)")
                else:
                    self._log("info", f"{client.cid} (another device, page {client.page_load_id}) watches as a "
                                      f"subscriber; {cur.cid} drives the screen")
                self._changed()
                return None
            elif client.device == cur.device or (self._own(client) and not self._own(cur)):
                cur.standby = True                # in art and idle: a reload on the same device, or the TV is back
                if self._own(client) and not self._own(cur):    # the TV coming back is normal, not a duplicate
                    self._log("info", f"this screen's own glass is back: {client.cid} drives, {cur.cid} on standby")
                else:
                    self._duplicate(client, f"{client.cid} is now driven, {cur.cid} on standby")
            else:
                client.standby = True             # another device (a phone, a laptop): it watches, the TV drives
                self._log("info", f"{client.cid} (another device, page {client.page_load_id}) watches as a "
                                  f"subscriber; {cur.cid} drives the screen")
                self._changed()
                return None
        elif cur is None and not self._may_take_empty_glass(client):
            client.standby = True
            self._log("info", f"{client.cid} (another device, page {client.page_load_id}) waits on standby for "
                              "this screen's own glass")
            self._changed()
            return None
        return self._drive(client)

    def _drive(self, client: PageClient) -> dict | None:
        """Make `client` the driven glass; the LIVE_V1 reload/orphan rules apply."""
        prev = self._driven
        prev_load = self.page_load_id
        same_page = bool(client.page_load_id) and client.page_load_id == prev_load
        self._hello_same_page = same_page
        self._driven = client
        client.standby = False
        self.page_load_id = client.page_load_id
        if not self.glass_device:
            self.glass_device = client.device or None     # until a show is shaped for one (_entry)
        self._page_lost_at = None
        if prev is not None and prev is not client:
            self.box.put({"src": "internal", "type": "page_gone", "cid": prev.cid})
        # A freeze left open is released only once nothing persisted can still be adopted
        # (DCL v3 §4.7.6): adoption releases it itself, after any live.abort.
        if not (self._adopt is not None or (self._adopt_pending and self.session is not None)):
            self._release_stale_fid()
        info = None
        if self._adopt is not None:
            info = ({"session": self.session["id"], "attach": None, "phase": "live"}
                    if self.session and self.session["id"] in self._adopt["cands"] and same_page else None)
            if self._adopt.get("page_state") is None:
                self._adopt["page_state"] = self._adoption_evidence()
        elif self.session and self.layer == "live":
            if self._headless():
                # The session runs without a page: this one joins it (attach in the welcome, no re-entry).
                info = self.running_live()
                self._join_cid = client.cid if info else None
            elif same_page:
                info = self.live_info()      # the page carries on playing (see live_info)
            else:
                # A fresh page load starts in art. Sessions run whether or not a page shows them, so it joins the
                # running one (attach in the welcome, no re-entry, no new session: the show carries on); the
                # matched art->live cut is only needed on a real art->live switch.
                self.session["headless"] = True
                info = self.running_live()
                if info is not None:
                    self._join_cid = client.cid
                    self._log("info", f"page reloaded while live: joins the running session {self.session['id']}")
                else:
                    self.session.pop("headless", None)
                    self._log("info", f"page reloaded while live ({self.session['id']}); re-entering")
                    self.layer = "art"
                    self._stale_session = True
        self._changed()
        self.wake()
        return info

    def _release_stale_fid(self) -> None:
        """Send `unfreeze` for a freeze we couldn't release (we stopped, or the socket dropped,
        mid-handshake), rather than leaving the page to its 30 s safety timeout. A reloaded
        page isn't frozen, so the unfreeze is then a no-op. Deferred one loop turn so that,
        on a hello, it goes out after the welcome."""
        page = self._page()
        if not self._stale_fid or self.frozen_fid is not None or page is None:
            return
        fid, self._stale_fid = self._stale_fid, None

        def release() -> None:
            try:
                page.send("unfreeze", {"fid": fid})
            except PageGone:
                self._stale_fid = self._stale_fid or fid
            self._changed()
        asyncio.get_running_loop().call_soon(release)

    def promote_standby(self, why: str) -> bool:
        sb = self._standbys()
        if not sb:
            return False
        c = max(sb, key=lambda x: (self._own(x), x.connected_at))     # the screen's own device first, else newest
        self._promote(c, why)
        return True

    def _promote(self, c: PageClient, why: str) -> None:
        # the screen's own glass (the TV) taking it back is normal (info); another device driving is worth a warning
        self._log("info" if self._own(c) else "warning", f"promoting standby glass {c.cid} (page {c.page_load_id}): {why}")
        info = self._drive(c)
        if info and info.get("attach"):          # a headless session: no welcome now, so attach it here
            try:
                # rejoin: as a subscriber it may have ended this very session (a stall); it plays it again.
                c.send("live.attach", {"session": info["session"], **info["attach"], "rejoin": True})
            except PageGone:
                self._join_cid = None

    def _reclaim(self, client: PageClient, state: dict) -> None:
        """A standby page on this screen's own glass device (the TV) while another device drives, or none does:
        it takes the glass back as soon as that is seamless: in art and idle, or while live once it plays the
        running session itself (it is joined: no new session, no freeze). Never mid-handshake or mid-adoption."""
        cur = self._page()
        if (not self._own(client) or client is cur or (cur is not None and self._own(cur))
                or self._handshake is not None or self.frozen_fid is not None or self._adopting()):
            return
        s = self.session
        if s is not None and (self.layer != "live" or s.get("gone") or not self._attach_fields(s)
                              or state.get("layer") != "live" or (state.get("live") or {}).get("session") != s["id"]):
            return
        if cur is not None:
            cur.standby = True
        self._promote(client, f"this screen's own glass takes it back from {cur.cid if cur else 'nobody'}")
        if s is not None:
            self._joined("kiosk.state")          # it already shows the session

    def on_page_closed(self, client: PageClient) -> None:
        if client is not self._driven:
            self._changed()               # a standby (or a replaced socket) left
            return
        self._driven = None
        self.box.put({"src": "internal", "type": "page_gone", "cid": client.cid})
        # S13: the screen's own glass (the TV) dropping is a reload or a network blip: hold for it to come back rather
        # than handing the glass (and the show's shape) to a phone that happens to be watching.
        if self._own(client) or self._busy() or not self.promote_standby("the driven page left"):
            # G5: hold for the page to come back (the supervisor promotes after adopt_wait_s).
            self._page_lost_at = self._now()
        self._changed()

    def on_page_message(self, client: PageClient, mtype: str, data: dict) -> None:
        if client is not self._page():
            self.on_subscriber_message(client, mtype, data)   # it drives nothing
            return                       # only the driven glass client counts
        if mtype == "kiosk.state":
            self.kiosk_state = {**data, "received_at": utc_iso()}
            self._prefetch(client, data)
        elif mtype == "ack":
            self.last_ack_at = utc_iso()
        self.box.put({"src": "page", "cid": client.cid, "type": mtype, "data": data})
        if (mtype == "ack" and self._headless() and data.get("session") == self.session["id"]
                and self._handshake is None):
            if data.get("kind") == "live_visible":
                self._joined("live_visible")
            elif data.get("kind") == "live_failed":
                # The page couldn't join: start over with it (the full entry, now that a page is here).
                self.layer = "art"
                self._fallback_pending = f"page could not join: {data.get('reason') or 'live_failed'}"
                self.wake()
        if mtype == "ack" and data.get("kind") == "art_visible":
            sid = data.get("session")
            if (self.session and sid == self.session["id"] and self.layer == "live"
                    and self._handshake != "exit"):
                # Unplanned: the page cut to art by itself (stall, error).
                self.layer = "art"
                self._fallback_pending = f"page {data.get('reason') or 'art_visible'}"
                self.wake()
        elif mtype == "kiosk.state":
            self._check_kiosk_state(data)
        self._changed()

    def _check_kiosk_state(self, data: dict) -> None:
        if self._adopt is not None:
            # Restart adoption is resolved by the run task (see _reconcile).
            self._adopt["page_state"] = data
            self.wake()
            return
        if self._handshake is not None or self._adopt_pending:
            return
        page_layer = data.get("layer")
        page_sid = (data.get("live") or {}).get("session")
        if self._headless() and self.layer == "live":
            if page_layer == "live" and page_sid == self.session.get("id"):
                self._joined("kiosk.state")
                return
            if page_layer == "art":
                return                   # not joined (yet): the session runs on regardless
        if page_layer == "art" and self.layer == "live" and self.session:
            self.layer = "art"
            self._fallback_pending = "kiosk.state reports art"
            self.wake()
        elif page_layer == "live" and (not self.session or page_sid != self.session.get("id")):
            # The page plays a session we don't own: stop it (hard cut to its art).
            page = self._page()
            if page is not None and page_sid:
                self._log("warning", f"page is playing unknown session {page_sid}; sending live.abort")
                try:
                    page.send("live.abort", {"session": page_sid, "reason": "unknown session"})
                except PageGone:
                    pass

    # -- inbound: stream ----------------------------------------------------
    def on_stream_event(self, evt: dict) -> None:
        self.box.put({"src": "stream", **evt})
        if not self.session or evt.get("session") != self.session["id"]:
            return
        typ = evt.get("type")
        if typ == "session.state":
            self.session["state"] = evt.get("state")
        if self._handshake is not None:
            return                       # the handshake watches its own session
        lost = typ in ("session.error", "session.ended") or (
            typ == "session.state" and evt.get("state") in ("failed", "ended"))
        if lost:
            self.session_lost(f"stream {typ}: {evt.get('detail') or evt.get('reason') or evt.get('state')}",
                              gone=typ == "session.ended")
        elif self.phase == "entering" and (
                (typ == "session.transition" and evt.get("kind") == "in" and evt.get("phase") == "done")
                or (typ == "session.state" and evt.get("state") == "live")):
            self._set_phase("live")

    def session_lost(self, reason: str, gone: bool = False) -> None:
        if not self.session:
            return
        if gone:
            self.session["gone"] = True
        if self.layer == "live":
            self._fallback_pending = reason
        else:
            self._stale_session = True
        self.wake()

    # -- restart adoption ---------------------------------------------------
    def begin_adoption(self, cands: dict[str, dict]) -> None:
        self._adopt_pending = False
        if not cands:
            if self.session:
                log.info("%s: persisted session %s no longer exists", self.id, self.session["id"])
            self.session = None
            self.layer = "art"
            if self.phase not in ("failed",):
                self._set_phase("idle")
            self._release_stale_fid()
            self._changed()
            self.wake()
            return
        self._adopt = {"cands": cands, "deadline": self._now() + self.t.adopt_wait_s, "page_state": None}
        self._set_phase(self.phase if self.phase != "failed" else "idle", "adopting after restart")
        self._adopt["page_state"] = self._adoption_evidence()
        self.wake()

    def _adoption_evidence(self) -> dict | None:
        """What the glass page shows, as far as we can tell yet (None: wait for kiosk.state).

        kiosk.state is authoritative. Before it arrives, a page that reconnected with the
        persisted page_load_id never reloaded, so it still plays what we persisted; a new
        page_load_id is a fresh load, which starts in art.
        A headless session needed no page before the restart and needs none now: it is adopted as it is.
        """
        if self._headless() and self.layer == "live":
            return {"layer": "live", "live": {"session": self.session["id"]}, "headless": True}
        if self._page() is None:
            return None
        if self.kiosk_state is not None:
            return self.kiosk_state
        if self._hello_same_page is False:
            return {"layer": "art", "live": None}
        if self._hello_same_page and self.session and self.layer == "live":
            return {"layer": "live", "live": {"session": self.session["id"]}}
        return None

    async def _finish_adoption(self, page_state: dict | None) -> None:
        if self._adopt is None:
            return
        cands, self._adopt = self._adopt["cands"], None
        # Busy until done: the orphan sweep and stream-event handling keep off this screen
        # while sessions are deleted/adopted (it awaits in between).
        self._handshake = "adopt"
        mark = self.box.mark()
        try:
            await self._adopt_sessions(page_state, cands)
        finally:
            self._handshake = None
            self._changed()
        # Subscribers: a kept session is attached again (a no-op for a page still playing it; pages that connected
        # during the adoption, or ended it meanwhile, join it).
        self._publish("adopted", resend=True)
        # Only now (after any live.abort, so the abort cut lands on the frozen art stack).
        self._release_stale_fid()
        # Stream events are not acted on while busy: catch up on a loss meanwhile.
        sid = self.session["id"] if self.session else None
        if sid and self._scan(mark, lambda i: self._is_stream(i, ("session.error", "session.ended"), sid) or (
                self._is_stream(i, ("session.state",), sid) and i.get("state") in ("failed", "ended"))):
            self.session_lost("stream session lost during adoption", gone=True)

    async def _adopt_sessions(self, page_state: dict | None, cands: dict[str, dict]) -> None:
        keep = None
        if self._headless() and self.session["id"] in cands:
            keep = self.session["id"]            # it ran without a page: whatever a page shows doesn't matter
        elif page_state and page_state.get("layer") == "live":
            sid = (page_state.get("live") or {}).get("session")
            if sid in cands:
                keep = sid
        if (self._sub or {}).get("session") not in (None, keep):
            self._publish("dropped after a restart", drop=True)    # subscribers still playing it cut to art first
        for sid in cands:
            if sid != keep:
                log.info("%s: deleting orphan session %s", self.id, sid)
                await self._delete_session(sid)
        if keep is None:
            self.session = None
            self.layer = "art"
            self._set_phase("idle", "no session to adopt" if not page_state else "page shows art")
        else:
            summ = cands[keep]
            if not self.session or self.session.get("id") != keep:
                live = self.desired.get("live") if isinstance(self.desired, dict) else None
                self.session = {"id": keep, "attach": None, "profile": summ.get("profile"),
                                "live": copy.deepcopy(live), "state": summ.get("state")}
            self.session["state"] = summ.get("state")
            self.layer = "live"
            if summ.get("state") in ("exiting", "held"):
                # An exit was in flight when we stopped: finish it with a hard cut.
                self._log("warning", f"adopted {keep} mid-exit; aborting it")
                await self._abort_live("adopted mid-exit")
            elif summ.get("state") in ("starting", "holding"):
                # We stopped between the entry cut and `go`: the page shows the held anchor.
                try:
                    await self.mgr.stream.go(keep)
                    self._live_since = self._now()
                    self._set_phase("entering", f"adopted {keep}; go sent")
                except StreamError as e:
                    self._log("warning", f"adopted {keep} in {summ.get('state')} but go failed ({e}); aborting")
                    await self._abort_live("adopted before go")
            else:
                self._live_since = self._now()
                self._set_phase("live", f"adopted {keep}")
                self._log("info", f"adopted session {keep} after restart")
        self._changed()
        self.wake()

    async def _abort_live(self, reason: str) -> None:
        sid = self.session["id"] if self.session else None
        page = self._page()
        if sid and page is not None:
            try:
                page.send("live.abort", {"session": sid, "reason": reason})
            except PageGone:
                pass
        self.layer = "art"
        self._publish(reason)                       # subscribers cut too
        if sid:
            await self._delete_session(sid)
        self.session = None
        self._set_phase("idle", reason)

    # -- the reconcile loop -----------------------------------------------
    async def _reconcile(self) -> None:
        for _ in range(50):          # bounded; each pass either acts or returns
            if self._fallback_pending:
                reason, self._fallback_pending = self._fallback_pending, None
                await self._handle_fallback(reason)
                continue
            if self._adopt is not None:
                ps = self._adopt.get("page_state")
                if ps is None and self._now() < self._adopt["deadline"]:
                    self._wake_at(self._adopt["deadline"])
                    return
                if ps is None:
                    self._log("warning", "no kiosk.state from the page after restart; dropping its sessions")
                await self._finish_adoption(ps)
                continue
            if self._adopt_pending and self.session is not None:
                # After a restart nothing touches a persisted session until the startup
                # listing has run (it may be adopted: see begin_adoption).
                if self.detail != "waiting for stream_service (restart adoption)":
                    self._set_phase(self.phase, "waiting for stream_service (restart adoption)")
                return
            if self._stale_session and self.session and self._handshake is None:
                self._stale_session = False
                sid = self.session["id"]
                if not self.session.get("gone"):
                    await self._delete_session(sid)
                self.session = None
                self.layer = "art"
                self._changed()
                continue
            self._stale_session = False
            d = self.desired
            if d == "art":
                if self.session is not None:
                    await self._exit()
                    continue
                if self.phase != "idle":
                    self._set_phase("idle")
                return
            # desired live
            if self.phase == "failed":
                return
            if not self.cfg.live_enabled:
                self._fail("live_disabled")
                return
            if self.session is not None and self.layer == "live":
                cur = self.session.get("live")
                if cur != d["live"]:
                    if self._same_stream(cur, d["live"]):
                        await self._apply_live_change(cur, d["live"])
                    else:
                        await self._exit()
                    continue
                self._join_page()
                if self.phase not in ("entering", "live"):
                    self._set_phase("live")
                return
            if self.session is not None:
                self._stale_session = True
                continue
            if self._adopt_pending:
                self._set_phase("idle", "waiting for stream_service")
                return
            page = self._page()
            if not self.mgr.stream.reachable:
                self._set_phase("idle", "waiting for stream_service")
                return
            if self._now() < self._next_attempt_at:
                if not self.detail.startswith(("retry in", "the renderer is restarting", "live keeps stopping")):
                    self._set_phase("idle", f"retry in {self._next_attempt_at - self._now():.1f} s")
                self._wake_at(self._next_attempt_at)
                return
            self._warm()                         # the worker's cold start overlaps the freeze and the snapshot
            if page is None:
                await self._entry_headless()     # live renders whether or not a page shows it
            else:
                await self._entry(page)
        log.warning("%s: reconcile did not settle in 50 passes", self.id)

    def _warm(self) -> None:
        """POST /api/workers/warm {screen, gpu}, not awaited: stream_service starts this screen's render worker now,
        not cold at the session create. Best effort: a failure is logged at debug and the entry goes on."""
        warm = getattr(self.mgr.stream, "warm", None)
        if warm is None or (self._warm_task is not None and not self._warm_task.done()):
            return

        async def go() -> None:
            try:
                log.debug("%s: warm hint: %s", self.id, await warm(self.id, self.cfg.gpu))
            except Exception as e:
                log.debug("%s: warm hint failed: %s", self.id, describe_exc(e))
        self._warm_task = spawn(go(), f"warm-{self.id}")

    def _same_stream(self, cur: dict | None, new: dict) -> bool:
        if not cur:
            return False
        return (cur.get("profile") or self.cfg.profile) == (new.get("profile") or self.cfg.profile)

    async def _apply_live_change(self, cur: dict, new: dict) -> None:
        """Live -> live on the same stream: PATCH the scene/params in place."""
        body: dict[str, Any] = {}
        if new.get("scene") is None:
            pass                           # the director keeps going; a changed pool is handled below
        elif cur.get("scene") != new.get("scene"):
            body["scene"] = scene_with_params(new)
            tin = (new.get("transitions") or {}).get("in")
            if tin is not None:
                body["transition"] = tin
        elif (cur.get("params") or {}) != (new.get("params") or {}):
            body["params"] = new.get("params") or {}
        if (cur.get("direction") or None) != (new.get("direction") or None):
            # A mode without a direction after one with: back to neutral (a PATCHed direction merges over the old).
            body["direction"] = copy.deepcopy(new.get("direction") or NEUTRAL_DIRECTION)
        recue = False
        if (cur.get("auto"), cur.get("scene_tags")) != (new.get("auto"), new.get("scene_tags")):
            try:
                auto = await auto_block(new, self.cfg.auto, self.mgr.stream)
            except StreamError as e:
                auto = None
                self._log("warning", f"director not changed: scene_tags not resolved ({e})")
            if auto is not None:
                # A mode change never turns effect picking back on, nor resets How many (owner, 17 Oct): unless the new
                # mode sets effects itself, the PATCH carries no auto.fx, and the renderer keeps the session's own.
                mode_fx = (new.get("auto") or {}).get("fx") if isinstance(new.get("auto"), dict) else None
                if "fx" in auto and not isinstance(mode_fx, dict):
                    auto = {k: v for k, v in auto.items() if k != "fx"}
                body["auto"] = auto
                pool = auto.get("pool")
                on_glass = spec_name(cur.get("scene")) if cur.get("scene") is not None else None
                # A new pool that the scene on glass isn't in (or a closed pool opening up, Ambient -> Party): the
                # director picks the next scene from it now, with its own weights and ratings.
                was_closed = isinstance(cur.get("scene_tags"), list) and bool(cur.get("scene_tags"))
                recue = new.get("scene") is None and (
                    (isinstance(pool, list) and on_glass not in pool) or (pool == "all" and was_closed))
        if body:
            try:
                await self.mgr.stream.patch(self.session["id"], body)
                self._log("info", f"live change applied in-stream ({', '.join(body)})")
                if recue:
                    tin = (new.get("transitions") or {}).get("in")
                    cue = {"to": "auto", "source": "mode", **({"via": tin} if tin is not None else {})}
                    try:
                        await self.mgr.stream.cue(self.session["id"], cue)
                    except StreamError as e:
                        self._log("warning", f"next scene from the new pool not cued ({e}); the director moves on by itself")
            except StreamError as e:
                # Keep desired honest: it reverts to what the stream is actually doing.
                self.last_error = f"live change refused: {e}"
                self._log("warning", self.last_error)
                self.desired = {"live": copy.deepcopy(cur)}
                self._changed()
                return
        self.session["live"] = copy.deepcopy(new)
        self._changed()

    def _fail(self, why: str) -> None:
        self.last_error = why
        self._set_phase("failed", why)
        self._log("warning", f"failed: {why} (stays in art until desired changes)")

    def _schedule_retry(self, why: str) -> None:
        if self._live_since is not None and self._now() - self._live_since >= self.t.stable_reset_s:
            self._retry_idx = 0
        self._live_since = None
        b = self.t.backoff_s
        delay = b[min(self._retry_idx, len(b) - 1)]
        self._retry_idx += 1
        self._next_attempt_at = self._now() + delay
        self._set_phase("idle", f"retry in {delay:g} s after {why}")
        self._wake_at(self._next_attempt_at)

    def _record_fallback(self) -> int:
        now = time.time()
        self.fallbacks.append(now)
        while self.fallbacks and now - self.fallbacks[0] > self.t.fallback_window_s:
            self.fallbacks.popleft()
        return len(self.fallbacks)

    async def _handle_fallback(self, reason: str) -> None:
        sess = self.session
        sid = sess["id"] if sess else None
        was_live = self.layer == "live"
        # Detach first: further events about this session (a stream shutting down sends both
        # session.state ended and session.ended) must not count as more fallbacks.
        self.session = None
        self.layer = "art"
        self._fallback_pending = None
        self._publish(f"fallback: {reason}")         # subscribers cut to art now (the re-entry re-attaches them)
        self._log("warning", f"fallback to art ({reason})")
        self.last_error = f"fallback: {reason}"
        if sid and was_live:
            # A stream-side failure: cut the page to its art now rather than waiting for
            # its 2 s stall detector.
            page = self._page()
            if page is not None:
                mark = self.box.mark()
                try:
                    page.send("live.abort", {"session": sid, "reason": reason[:80]})
                    await self._wait(mark, lambda i: self._is_ack(i, "art_visible", session=sid),
                                     self.t.abort_ack_s, "ack art_visible (abort)", page=page)
                except (HandshakeError, PageGone, PageInArt):
                    pass
        if sid and not sess.get("gone"):
            await self._delete_session(sid)
        self._after_fallback = True
        # The renderer restarting (a deploy ends every session with "service shutdown", and the page stalls as its
        # stream goes) isn't this screen failing: don't count it, don't climb the backoff, go back as soon as it's up.
        stream = getattr(self.mgr, "stream", None)
        if "service shutdown" in reason or (stream is not None and stream.went_away_within(RENDERER_GONE_S)):
            self._log("info", f"fallback was the renderer restarting ({reason}): not counted")
            if self.desired == "art":
                self._set_phase("idle")
                return
            self._next_attempt_at = self._now() + self.t.backoff_s[0]
            self._set_phase("idle", "the renderer is restarting; back live as soon as it's up")
            self._wake_at(self._next_attempt_at)
            return
        n = self._record_fallback()             # the window's count, for the page's words; never a limit
        if self.desired == "art":
            self._set_phase("idle")
            return
        self._schedule_retry(f"fallback {n}")     # degrade, never refuse (owner): the backoff tops out at 60 s (S2)

    # -- entry handshake ----------------------------------------------------
    async def _entry(self, page: PageClient) -> None:
        d = self.desired
        live = copy.deepcopy(d["live"])
        reanchor = self._after_fallback
        fid = self._new_fid()
        self._handshake = "entry"
        self._abort_entry = False
        sid: str | None = None
        attached = False
        mark = self.box.mark()
        t = self.t
        try:
            await self.mgr.refresh_catalog()
            await self._snapshotter_ready()
            if self._abort_entry:
                raise EntryAborted()
            # 1. freeze -> ack frozen [3 s]
            self.frozen_fid = fid
            self._set_phase("freezing", fid)
            self._send(page, "freeze", {"fid": fid, "reason": "reanchor" if reanchor else "enter"})
            ack = await self._wait(mark, lambda i: self._is_ack(i, "frozen", fid=fid), t.freeze_ack_s,
                                   "ack frozen", page=page, abortable=True)
            # 2. snapshot [3 s]
            self._set_phase("snapshot")
            self.art_shape = await self._art_shape_of(ack["data"].get("state"))
            png = await self._snapshot(self._shaped(ack["data"].get("state")))
            self._keep_png("anchor", png)
            if self._abort_entry:
                raise EntryAborted()
            # 3. POST session -> session.ready [5 s]
            self._set_phase("starting")
            profile = live.get("profile") or self.cfg.profile
            body: dict[str, Any] = {
                "screen": self.id, "gpu": self.cfg.gpu, "profile": profile, **self._render_size(profile),
                "anchor": {"png_b64": base64.b64encode(png).decode()},
                **({"hud": True} if self.hud else {}),
                "scene": scene_with_params(live),
                "ttl_s": self.mgr.cfg.session_ttl_s,
                "idempotency_key": f"{self.id}-{self.mgr.epoch}-{self._attempt}",
            }
            if live.get("transitions"):
                body["transitions"] = live["transitions"]
            look, carried_scene, carried_fx = await self._session_look(live)
            body.update(look)
            if carried_scene:
                body["scene"] = carried_scene
            if body.get("scene") is None:
                body["scene"] = "auto"     # the renderer's director picks (weighted, ratings; a room's leader first)
            try:
                # Bounded: the page is frozen meanwhile, and its own freeze safety is 30 s.
                resp = await self.mgr.stream.create_session(body, timeout=t.create_session_s)
            except StreamError as e:
                if isinstance(e, StreamUnreachable):
                    # It may still be created server-side (a slow start): reclaim it by its
                    # idempotency key and delete it, rather than leave it on the GPU until its TTL.
                    self.mgr.reclaim(body)
                raise self._create_failed(e) from None
            sid = str((resp or {}).get("session") or "")
            self.hud_applied, self.hud_failed = self.hud, None
            attach = (resp or {}).get("attach") or {}
            if sid:
                self.mgr.own(sid, self.id)
            if not sid or not attach.get("url"):
                if sid:
                    await self._delete_session(sid)
                    sid = None
                raise HandshakeError("create session", "response had no session/attach")
            self.session = {"id": sid, "attach": attach, "profile": profile, "live": live,
                            "state": resp.get("state"),
                            "created_out": (live.get("transitions") or {}).get("out")}
            await self._restore_fx(sid, carried_fx)
            self._changed()
            if resp.get("state") not in ("holding",):
                await self._wait(mark, lambda i: self._is_stream(i, ("session.ready",), sid) or (
                    self._is_stream(i, ("session.state",), sid) and i.get("state") == "holding"),
                    t.session_ready_s, "session.ready", page=page, abortable=True, sid=sid)
            # 4-5. live.attach -> ack live_visible [10 s] (the page gives up at 8 s: live_failed)
            self._set_phase("attaching", sid)
            self._send(page, "live.attach", {"session": sid, **{k: attach.get(k) for k in (
                "url", "codec", "w", "h", "fps", "target_buffer_ms")}})
            attached = True
            ack = await self._wait(mark, lambda i: self._is_ack(i, ("live_visible", "live_failed"), session=sid),
                                   t.live_visible_s, "ack live_visible", page=page, abortable=True, sid=sid)
            if ack["data"].get("kind") == "live_failed":
                raise HandshakeError("live.attach", f"page live_failed: {ack['data'].get('reason')}")
            self.layer = "live"
            # 5b. settle: the page shows the held anchor (nothing visible changes) while its decoder warms up,
            # so the in-fade that `go` starts plays smoothly from its first frame.
            await self._settle(mark, t.settle_before_go_s, page=page, sid=sid)
            if self._abort_entry:
                raise EntryAborted()
            # 6. go: the renderer runs `in` from the anchor into the scene
            try:
                await self.mgr.stream.go(sid, timeout=t.go_s)
            except StreamError as e:
                raise HandshakeError("go", str(e), transient=isinstance(e, StreamUnreachable)
                                     and not self.mgr.stream.reachable) from None
            self._live_since = self._now()
            if not self.glass_device or self._own(page):   # S13: another device driving never takes the TV's place
                self.glass_device = page.device or self.glass_device    # this show is shaped for this page's device
            self._set_phase("entering", sid)
            # 7. unfreeze
            self._unfreeze(page)
            self._entry_failures = 0
            self._after_fallback = False
            self.last_error = None
            self._log("info", f"live on {sid} ({desired_label(d)})")
            in_s = self.mgr.transition_seconds((live.get("transitions") or {}).get("in"))
            if self._scan(mark, lambda i: self._is_stream(i, ("session.transition",), sid)
                          and i.get("kind") == "in" and i.get("phase") == "done"):
                self._set_phase("live")
            else:
                self._arm_entering_timer(sid, in_s + t.entering_extra_s)
            if self._scan(mark, lambda i: self._is_stream(i, ("session.error", "session.ended"), sid)):
                self.session_lost("stream error during entry", gone=True)
        except EntryAborted:
            self._log("info", "entry aborted (desired changed to art)")
            await self._abort_entry_cleanup(page, sid, attached, "desired art")
        except PageGone:
            self._log("warning", "page went away during entry; aborting")
            await self._abort_entry_cleanup(page, sid, attached, "page gone")
        except (HandshakeError, StreamUnreachable) as e:
            self.last_error = f"entry {e}"
            self._log("warning", f"entry failed: {e}; aborting cleanly")
            await self._abort_entry_cleanup(page, sid, attached, str(e)[:80])
            if not getattr(e, "transient", isinstance(e, StreamUnreachable)):
                self._entry_failures += 1       # an unreachable stream_service just waits for it
            if self.desired != "art":           # never parked: the backoff tops out and keeps trying (S1)
                self._schedule_retry(f"entry failure {self._entry_failures}")
        finally:
            self._handshake = None
            self._abort_entry = False
            self._changed()

    # -- headless entry: no page connected ----------------------------------------
    async def _current_art_state(self) -> dict | None:
        """The screen's current art as an ArtState, found the way the display page finds it (the newest of
        /output/<id>.jpg|.JPG|.mp4 on Flask, cache-busted by its mtime), with the current mask. None if unknown."""
        http = self.mgr.http
        if http is None:
            return None
        base = self.mgr.cfg.flask_url.rstrip("/")

        async def mtime(ext: str) -> tuple[float, str]:
            try:
                async with http.head(f"{base}/output/{self.id}.{ext}",
                                     timeout=aiohttp.ClientTimeout(total=2.0)) as r:
                    lm = r.headers.get("Last-Modified") if r.status == 200 else None
                    return (parsedate_to_datetime(lm).timestamp() if lm else 0.0), ext
            except Exception:
                return 0.0, ext

        best = max(await asyncio.gather(*(mtime(x) for x in ("jpg", "JPG", "mp4"))))
        if best[0] <= 0:
            return None
        ms, ext = int(best[0] * 1000), best[1]
        m = self.mgr.mask.status(self.id)
        return {"base_url": f"/output/{self.id}.{ext}?t={ms}", "kind": "video" if ext == "mp4" else "image",
                "clip_t": 0.0 if ext == "mp4" else None,
                "mask": {k: m.get(k) for k in ("brightness", "warm_hex", "warm_alpha")},
                "flags": {"nomask": False, "nocolour": False, "fit": "fit"}}

    async def _headless_anchor(self) -> dict:
        """The anchor for a session with no page: a snapshot of the current art if the art-only page can render
        it, else black."""
        try:
            state = await self._current_art_state()
            if state is None:
                raise HandshakeError("snapshot", "the current art is unknown")
            await self._snapshotter_ready()
            png = await self._snapshot(state)
        except HandshakeError as e:
            self._log("info", f"no art snapshot for the anchor ({e.detail}); starting from black")
            return {"rgb": [0, 0, 0]}
        self._keep_png("anchor", png)
        return {"png_b64": base64.b64encode(png).decode()}

    async def _entry_headless(self) -> None:
        """Start the session with no page: anchor, POST session, session.ready, go. No freeze/attach/live_visible;
        a page that connects later joins it (welcome.live with attach)."""
        d = self.desired
        live = copy.deepcopy(d["live"])
        self._new_fid()                          # (the attempt number, for the idempotency key)
        self._handshake = "entry"                # set_desired(art) aborts it like a page entry, before `go`
        self._abort_entry = False
        sid: str | None = None
        mark = self.box.mark()
        t = self.t
        try:
            await self.mgr.refresh_catalog()
            self._set_phase("snapshot", "no page: anchoring on the current art")
            anchor = await self._headless_anchor()
            if self._abort_entry:
                raise EntryAborted()
            self._set_phase("starting", "no page")
            self.art_shape = await self._art_shape_of(self.kiosk_state)
            profile = live.get("profile") or self.cfg.profile
            body: dict[str, Any] = {
                "screen": self.id, "gpu": self.cfg.gpu, "profile": profile, **self._render_size(profile),
                "anchor": anchor, **({"hud": True} if self.hud else {}),
                "scene": scene_with_params(live), "ttl_s": self.mgr.cfg.session_ttl_s,
                "idempotency_key": f"{self.id}-{self.mgr.epoch}-{self._attempt}",
            }
            if live.get("transitions"):
                body["transitions"] = live["transitions"]
            look, carried_scene, carried_fx = await self._session_look(live)
            body.update(look)
            if carried_scene:
                body["scene"] = carried_scene
            if body.get("scene") is None:
                body["scene"] = "auto"     # the renderer's director picks (weighted, ratings; a room's leader first)
            try:
                resp = await self.mgr.stream.create_session(body, timeout=t.create_session_s)
            except StreamError as e:
                if isinstance(e, StreamUnreachable):
                    self.mgr.reclaim(body)
                raise self._create_failed(e) from None
            sid = str((resp or {}).get("session") or "")
            self.hud_applied, self.hud_failed = self.hud, None
            attach = (resp or {}).get("attach") or {}
            if sid:
                self.mgr.own(sid, self.id)
            if not sid or not attach.get("url"):
                if sid:
                    await self._delete_session(sid)
                    sid = None
                raise HandshakeError("create session", "response had no session/attach")
            self.session = {"id": sid, "attach": attach, "profile": profile, "live": live,
                            "state": resp.get("state"), "headless": True,
                            "created_out": (live.get("transitions") or {}).get("out")}
            await self._restore_fx(sid, carried_fx)
            self._changed()
            if resp.get("state") not in ("holding",):
                await self._wait(mark, lambda i: self._is_stream(i, ("session.ready",), sid) or (
                    self._is_stream(i, ("session.state",), sid) and i.get("state") == "holding"),
                    t.session_ready_s, "session.ready", abortable=True, sid=sid)
            if self._abort_entry:
                raise EntryAborted()
            try:
                await self.mgr.stream.go(sid, timeout=t.go_s)
            except StreamError as e:
                raise HandshakeError("go", str(e), transient=isinstance(e, StreamUnreachable)
                                     and not self.mgr.stream.reachable) from None
            self.layer = "live"
            self._join_cid = None
            self._live_since = self._now()
            self._set_phase("entering", f"{sid} (no page)")
            self._entry_failures = 0
            self._after_fallback = False
            self.last_error = None
            self._log("info", f"live on {sid} with no page ({desired_label(d)})")
            in_s = self.mgr.transition_seconds((live.get("transitions") or {}).get("in"))
            if self._scan(mark, lambda i: self._is_stream(i, ("session.transition",), sid)
                          and i.get("kind") == "in" and i.get("phase") == "done"):
                self._set_phase("live")
            else:
                self._arm_entering_timer(sid, in_s + t.entering_extra_s)
            if self._scan(mark, lambda i: self._is_stream(i, ("session.error", "session.ended"), sid)):
                self.session_lost("stream error during entry", gone=True)
        except (EntryAborted, HandshakeError, StreamUnreachable) as e:
            aborted = isinstance(e, EntryAborted)
            if not aborted:
                self.last_error = f"entry {e}"
            self._log("info" if aborted else "warning",
                      "entry aborted (desired changed to art)" if aborted else f"entry (no page) failed: {e}")
            self.layer = "art"
            if sid:
                await self._delete_session(sid)
            self.session = None
            self._set_phase("idle", "entry aborted: desired art" if aborted else f"entry failed: {str(e)[:80]}")
            if not aborted:
                if not getattr(e, "transient", isinstance(e, StreamUnreachable)):
                    self._entry_failures += 1
                if self.desired != "art":       # never parked: the backoff tops out and keeps trying (S1)
                    self._schedule_retry(f"entry failure {self._entry_failures}")
        finally:
            self._handshake = None
            self._abort_entry = False
            self._changed()

    async def _abort_entry_cleanup(self, page: PageClient, sid: str | None, attached: bool, reason: str) -> None:
        cur = self._page() or page
        if attached and sid:
            try:
                self._send(cur, "live.abort", {"session": sid, "reason": reason})
            except PageGone:
                pass
        self.layer = "art"
        if sid:
            await self._delete_session(sid)
        self.session = None
        self._unfreeze(cur)
        self._set_phase("idle", f"entry aborted: {reason}")

    def _arm_entering_timer(self, sid: str, after_s: float) -> None:
        if self._entering_timer:
            self._entering_timer.cancel()

        def fire() -> None:
            if self.phase == "entering" and self.session and self.session["id"] == sid:
                self._set_phase("live", "no 'in done' event; assumed")

        self._entering_timer = asyncio.get_running_loop().call_later(after_s, fire)

    # -- exit handshake -----------------------------------------------------
    async def _exit(self) -> None:
        sess = self.session
        if sess is None:
            return
        sid = sess["id"]
        page = self._page()
        if page is None or self.layer != "live" or sess.get("headless"):
            # Nobody is showing it (a headless session: at most a page still joining it): just end the session.
            if page is not None and sess.get("headless") and self._join_cid == page.cid:
                try:
                    page.send("live.abort", {"session": sid, "reason": "left live"})
                except PageGone:
                    pass
            self._join_cid = None
            # Detach first: the session.ended that the DELETE causes must not count as a fallback.
            self.session = None
            self.layer = "art"
            self._publish("left live")
            if not sess.get("gone"):
                await self._delete_session(sid)
            self._set_phase("idle", "session ended (no page showing it)")
            return
        fid = self._new_fid()
        self._handshake = "exit"
        mark = self.box.mark()
        t = self.t
        live = sess.get("live") or {}
        out_spec = (live.get("transitions") or {}).get("out")
        try:
            await self.mgr.refresh_catalog()
            out_s = self.mgr.transition_seconds(out_spec)
            await self._snapshotter_ready()
            if page.closed:
                raise PageGone(page.cid)
            # 1. freeze -> ack frozen [3 s] (the hidden art stack's state)
            self.frozen_fid = fid
            self._set_phase("freezing", fid)
            self._send(page, "freeze", {"fid": fid, "reason": "exit"})
            ack = await self._wait(mark, lambda i: self._is_ack(i, "frozen", fid=fid), t.freeze_ack_s,
                                   "ack frozen", page=page, page_art_sid=sid, sid=sid)
            # 2. snapshot [3 s]. A failure here would end in a hard (visible) cut, and the page
            # can stay frozen well inside its 30 s safety, so one more attempt comes first.
            self._set_phase("snapshot")
            try:
                png = await self._snapshot(self._shaped(ack["data"].get("state")))
            except HandshakeError as e:
                self._log("warning", f"exit snapshot failed ({e.detail}); retrying once before a hard cut")
                await self._snapshotter_ready()
                png = await self._snapshot(self._shaped(ack["data"].get("state")))
            self._keep_png("target", png)
            # 3. exit(target) -> session.held [out + 5 s]
            self._set_phase("exiting", sid)
            transition = out_spec if out_spec != sess.get("created_out") else None
            try:
                await self.mgr.stream.exit(sid, {"png_b64": base64.b64encode(png).decode()}, transition,
                                           timeout=t.exit_session_s)
            except StreamError as e:
                raise HandshakeError("exit", str(e)) from None
            held = await self._wait(mark, lambda i: self._is_stream(i, ("session.held",), sid),
                                    out_s + t.held_extra_s, "session.held", page=page, sid=sid, page_art_sid=sid)
            hold_pts = held.get("hold_pts")
            # 4. live.leave{hold_pts} -> ack art_visible [out + 10 s]
            self._set_phase("leaving", f"hold_pts {hold_pts}")
            self._send(page, "live.leave", {"session": sid, "hold_pts": hold_pts})
            if (self._sub or {}).get("session") == sid:
                self._to_subscribers("live.leave", {"session": sid, "hold_pts": hold_pts})   # the same exit cut
            ack = await self._wait(mark, lambda i: self._is_ack(i, "art_visible", session=sid),
                                   out_s + t.art_visible_extra_s, "ack art_visible", page=page)
            reason = ack["data"].get("reason")
            if reason != "exit":
                self._log("warning", f"exit cut was unmatched (reason {reason})")
            self.layer = "art"
            self._publish("exit")
            # 5. DELETE, unfreeze
            await self._delete_session(sid)
            self.session = None
            self._unfreeze(page)
            self._set_phase("idle", "exit complete")
            self._log("info", f"back to art from {sid}")
        except PageInArt as e:
            self._log("warning", f"page already showed art during exit ({e}); finishing")
            self.layer = "art"
            self._publish("exit")
            await self._delete_session(sid)
            self.session = None
            self._unfreeze(page)
            self._set_phase("idle", "exit: page was already in art")
        except (HandshakeError, PageGone, StreamUnreachable) as e:
            what = "page went away" if isinstance(e, PageGone) else str(e)
            self.last_error = f"exit {what}"
            self._log("warning", f"exit failed: {what}; hard cut to art")
            cur = self._page()
            if cur is not None:
                try:
                    cur.send("live.abort", {"session": sid, "reason": what[:80]})
                except PageGone:
                    pass
            self.layer = "art"
            self._publish(what)
            await self._delete_session(sid)
            self.session = None
            self._unfreeze(cur)
            self._set_phase("idle", "exit aborted (hard cut)")
        finally:
            self._handshake = None
            self._changed()

    # -- status -------------------------------------------------------------
    def status(self) -> dict:
        page = self._page()
        pages = self.mgr.pages
        retry_in = max(0.0, self._next_attempt_at - self._now()) if self._next_attempt_at else 0.0
        return {
            "name": self.cfg.label,                              # the destination's name ("North screen")
            "desired": self.desired,
            "desired_expires_in_s": self.expires_in_s(),         # PUT {desired, for_s}: seconds left, else null
            "desired_expires_to": self.expiry["revert"] if self.expiry else None,
            "hud": {"wanted": self.hud, "applied": bool(self.hud_applied and self.session and self.layer == "live"),
                    "failed": self.hud_failed},
            "observed": {
                "layer": self.layer, "phase": self.phase, "detail": self.detail,
                "session": self.session["id"] if self.session else None,
                "headless": self._headless(),                    # live with no page on it (yet)
                "page_connected": page is not None,
                "page_role": page.role if page else None,
                "kiosk_state": self.kiosk_state, "last_ack_at": self.last_ack_at,
                "frozen": self.frozen_fid is not None,
                "glass_clients": pages.glass_count(self.id),
                "duplicate_glass": pages.glass_count(self.id) > 1,   # screen.duplicate_glass (§2.2a.3)
                "viewers": sum(1 for c in pages.hello_clients(self.id) if not c.glass),
                "page": page.info() if page else None,
                "fallbacks_in_window": len(self.fallbacks),
                "retry_in_s": round(retry_in, 1) if retry_in else None,
            },
            "mask": self.mgr.mask.status(self.id),
            "last_error": self.last_error,
            "policy": {**self.policy(), "timed_reload_applied": self.page_timed_reload(),
                       "page_key_required": pages.key_required(self.id)},
            "snapshots": {"anchor": self.anchor_png is not None, "target": self.target_png is not None},
        }


# ---------------------------------------------------------------------------
# All screens
# ---------------------------------------------------------------------------

class ScreensManager:
    def __init__(self, cfg: "RoomConfig", *, epoch: str, pages: "PageHub", stream: "StreamClient",
                 snapshotter: "Snapshotter", mask: "MaskPusher", events: "EventHub",
                 http: aiohttp.ClientSession | None = None) -> None:
        self.cfg = cfg
        self.epoch = epoch
        self.http = http                     # finds the current art for a headless anchor (None: black)
        # for_s expiry: puts the old value back through the room state model (service.py); None: set directly.
        self.revert_hook: Callable[[str, Any], Any] | None = None
        self.pages = pages
        self.stream = stream
        self.snapshotter = snapshotter
        self.mask = mask
        self.events = events
        self.state_path = Path(cfg.state_dir) / "screens.json"
        self.snapshots_dir = Path(cfg.state_dir) / "snapshots"
        self.art_origins = {origin_of(cfg.art_page_base), origin_of(cfg.flask_url)}
        raw = read_json(self.state_path, {}) or {}
        saved = raw.get("screens", {})
        # Sessions this instance created (DCL v3 §4.7.6 owner tags). stream_service rejects
        # unknown fields on POST /api/sessions, so there is no owner tag on its side yet: the
        # record lives here, persisted with the screens. Adoption, orphan deletion and the
        # sweep touch only these; anything else is another instance's and is left alone.
        self.owned: dict[str, dict] = {str(k): dict(v) for k, v in (raw.get("owned") or {}).items()
                                       if isinstance(v, dict)}
        self.foreign: list[dict] = []        # live sessions we don't own (last listing)
        self.controllers: dict[str, ScreenController] = {
            sid: ScreenController(self, sc, saved.get(sid)) for sid, sc in cfg.screens.items()}
        for c in self.controllers.values():  # state written before ownership was recorded
            if c.session and c.session.get("id"):
                self.owned.setdefault(c.session["id"], {"screen": c.id, "since": utc_iso()})
        self._bg: set[asyncio.Task] = set()
        self._tasks: list[asyncio.Task] = []
        self._adopted = False
        self._persist_handle: asyncio.Handle | None = None
        self._started = False
        self._conn_lock = asyncio.Lock()     # one session listing at a time
        self.files = FileWriter("screens")   # state + snapshot PNGs, off the event loop
        self._sweep_at: float | None = None

    def __getitem__(self, sid: str) -> ScreenController:
        return self.controllers[sid]

    def get(self, sid: str) -> ScreenController | None:
        return self.controllers.get(sid)

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        self.stream.on_event(self._on_stream_event)
        self.stream.on_connection(self._on_stream_connection)
        for c in self.controllers.values():
            c.start()
        self._tasks.append(spawn(self._supervise(), "screens-supervisor"))
        self._started = True

    async def stop(self) -> None:
        """Idempotent. Live sessions and open handshakes are left for the next process."""
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        for t in list(self._bg):
            t.cancel()
        await asyncio.gather(*self._bg, return_exceptions=True)
        for c in self.controllers.values():
            await c.stop()
        self._started = False            # no more coalesced writes; this one is final
        self._write()
        await self.files.close()

    def persist(self) -> None:
        """Coalesce state writes to one per loop iteration."""
        if not self._started:
            return
        if self._persist_handle is None:
            self._persist_handle = asyncio.get_running_loop().call_soon(self._write)

    def _write(self) -> None:
        self._persist_handle = None
        self.files.write_json(self.state_path, {
            "version": 1, "epoch": self.epoch, "owned": self.owned,
            "screens": {sid: c.snapshot_state() for sid, c in self.controllers.items()}})

    # -- ownership ----------------------------------------------------------------
    def own(self, sid: str, screen: str) -> None:
        self.owned[sid] = {"screen": screen, "since": utc_iso()}
        self.persist()

    def disown(self, sid: str) -> None:
        if self.owned.pop(sid, None) is not None:
            self.persist()

    def reclaim(self, body: dict) -> None:
        """A create whose response we never got (timeout) may still have made a session.
        Ask again with the same idempotency_key (stream_service then returns that session;
        a solid-colour anchor keeps the request tiny) and delete it. If the first create
        never happened, this makes a session that is deleted at once."""
        t = spawn(self._reclaim(body), f"reclaim-{body.get('idempotency_key')}")
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    async def _reclaim(self, body: dict) -> None:
        key = body.get("idempotency_key")
        small = {k: body[k] for k in ("screen", "gpu", "profile", "width", "height", "scene", "transitions",
                                      "ttl_s", "idempotency_key") if k in body}
        small["anchor"] = {"rgb": [0, 0, 0]}
        for delay in (self.cfg.timeouts.sweep_delay_s, 5.0, 15.0):
            await asyncio.sleep(delay)
            if not self.stream.reachable:
                continue
            try:
                resp = await self.stream.create_session(small, timeout=30.0)
            except StreamUnreachable:
                continue
            except StreamError as e:
                log.info("reclaim %s: %s; nothing to clean up", key, e)
                return
            sid = str((resp or {}).get("session") or "")
            if sid:
                log.info("%s: deleting session %s from a create we gave up on (%s)", small["screen"], sid, key)
                self.own(sid, str(small["screen"]))
                try:
                    await self.stream.delete(sid)
                    self.disown(sid)
                except StreamError as e:
                    log.warning("reclaim %s: DELETE %s failed (%s); the sweep retries", key, sid, e)
            return

    # -- page hub callbacks ---------------------------------------------------
    def welcome(self, client: PageClient) -> dict:
        c = self.controllers[client.screen]
        live = c.on_glass_hello(client) if client.glass else None
        if client is not c._driven:
            # A subscriber (standby glass or viewer, any number): it is shown the running session (attach), else art.
            live = c.subscriber_live()
        return {"epoch": self.epoch, "screen": client.screen, "policy": c.policy(),
                "mask": self.mask.welcome_mask(client.screen), "live": live}

    async def revert_desired(self, screen: str, value: Any) -> bool:
        """for_s is up: put `value` back. False: refused for now (a mode switch is running), try again."""
        if self.revert_hook is not None:
            return bool(await self.revert_hook(screen, value))
        self.controllers[screen].set_desired(value, source="for_s")
        return True

    def page_message(self, client: PageClient, mtype: str, data: dict) -> None:
        self.controllers[client.screen].on_page_message(client, mtype, data)

    def page_closed(self, client: PageClient) -> None:
        if client.glass:
            self.controllers[client.screen].on_page_closed(client)
        self.events.mark_dirty()

    def is_frozen(self, screen: str) -> bool:
        c = self.controllers.get(screen)
        return bool(c and c.frozen_fid)

    # -- stream ---------------------------------------------------------------
    def _on_stream_event(self, evt: dict) -> None:
        self.events.emit("stream", evt)
        if evt.get("type") == "session.ended" and evt.get("session") in self.owned:
            self.disown(str(evt["session"]))
        c = self.controllers.get(str(evt.get("screen")))
        if c is not None:
            c.on_stream_event(evt)

    async def _on_stream_connection(self, up: bool) -> None:
        self.events.mark_dirty()
        if not up:
            return
        async with self._conn_lock:
            await self._reconcile_sessions()

    async def _reconcile_sessions(self) -> None:
        """After (re)connecting to stream_service: adopt (first time) or verify our sessions."""
        try:
            sessions = await self.stream.list_sessions()
        except StreamError as e:
            log.warning("could not list stream sessions: %s", e)
            sessions = None
        if sessions is not None:
            live = [s for s in sessions if s.get("state") not in ("ended", "failed")]
            self._note_foreign(live)
            if not self._adopted:
                self._adopted = True
                by_screen: dict[str, dict[str, dict]] = {sid: {} for sid in self.controllers}
                for s in live:
                    sid, scr = s.get("session"), s.get("screen")
                    if not sid or sid not in self.owned:
                        continue                 # not ours: another instance's (or a manual one)
                    if scr in by_screen:
                        by_screen[scr][sid] = s
                    else:
                        log.info("deleting our session %s for screen %r (no longer configured)", sid, scr)
                        try:
                            await self.stream.delete(sid)
                            self.disown(sid)
                        except StreamError:
                            pass
                listed = {s.get("session") for s in live}
                for sid in [k for k in self.owned if k not in listed]:
                    self.disown(sid)             # gone from stream_service: nothing to clean up
                for scr, cands in by_screen.items():
                    self.controllers[scr].begin_adoption(cands)
            else:
                ids = {s.get("session") for s in live}
                for c in self.controllers.values():
                    if c.session and c.session["id"] not in ids and c._handshake is None:
                        c.session_lost("session gone after stream_service reconnect", gone=True)
        for c in self.controllers.values():
            c.wake()

    def _note_foreign(self, live: list[dict]) -> None:
        foreign = [{"session": s.get("session"), "screen": s.get("screen"), "state": s.get("state")}
                   for s in live if s.get("session") and s.get("session") not in self.owned]
        mine = [f for f in foreign if f["screen"] in self.controllers]
        new = {f["session"] for f in mine} - {f["session"] for f in self.foreign}
        if new:
            log.warning("room.instance_conflict: %d stream session(s) for our screens belong to another "
                        "room_service instance (%s); left alone", len(mine),
                        ", ".join(f"{f['session']} ({f['screen']})" for f in mine))
            self.events.log_event("warning", f"room.instance_conflict: {len(mine)} session(s) for our "
                                             "screens are not ours; left alone")
        self.foreign = foreign
        self.events.mark_dirty()

    async def refresh_catalog(self) -> None:
        try:
            await asyncio.wait_for(self.stream.catalog(), 2.0)
        except Exception:
            pass                        # durations fall back to defaults

    def transition_seconds(self, spec: Any) -> float:
        """Length of a transition spec, from the catalog's presets/schemas (default 3 s)."""
        default = self.cfg.timeouts.default_transition_s
        if spec is None:
            return default
        cat = self.stream.cached_catalog() or {}
        presets = (cat.get("presets") or {}).get("transitions") or {}
        name, params = None, {}
        if isinstance(spec, str):
            p = presets.get(spec) or {}
            name, params = p.get("transition"), dict(p.get("params") or {})
        elif isinstance(spec, dict):
            if "preset" in spec:
                p = presets.get(spec["preset"]) or {}
                name, params = p.get("transition"), {**(p.get("params") or {}), **(spec.get("params") or {})}
            else:
                name, params = spec.get("transition"), dict(spec.get("params") or {})
        try:
            if "duration_s" in params:
                return max(0.0, float(params["duration_s"]))
        except (TypeError, ValueError):
            pass
        if name == "cut":
            return 0.0
        for tr in cat.get("transitions") or []:
            if isinstance(tr, dict) and tr.get("name") == name:
                d = (((tr.get("schema") or {}).get("properties") or {}).get("duration_s") or {}).get("default")
                if d is not None:
                    return float(d)
        return default

    # -- orphan sweep ----------------------------------------------------------
    def request_sweep(self, delay_s: float | None = None) -> None:
        """Sweep soon (e.g. after a create that may have succeeded server-side)."""
        delay = self.cfg.timeouts.sweep_delay_s if delay_s is None else delay_s
        loop = asyncio.get_running_loop()
        self._sweep_at = min(self._sweep_at or float("inf"), loop.time() + delay)

    async def _sweep_orphans(self) -> None:
        """Delete sessions *we created* that no controller uses any more (e.g. a DELETE
        that failed), rather than leaving them on the GPU (and an NVENC session) until their
        TTL. Other instances' sessions are never touched. Only settled screens are swept:
        none mid-handshake, adopting, or awaiting adoption."""
        if not (self.cfg.sweep_orphans and self._adopted and self.stream.reachable):
            return
        try:
            sessions = await self.stream.list_sessions()
        except StreamError as e:
            log.info("orphan sweep: could not list sessions (%s)", e)
            return
        self._note_foreign([s for s in sessions if s.get("state") not in ("ended", "failed")])
        for summ in sessions:
            sid, scr = summ.get("session"), summ.get("screen")
            c = self.controllers.get(str(scr))
            if not sid or sid not in self.owned or c is None or summ.get("state") in ("ended", "failed"):
                continue                         # only sessions we created are ever swept
            if c._handshake is not None or c._adopt is not None or c._adopt_pending:
                continue
            if c.session and c.session.get("id") == sid:
                continue
            c._log("warning", f"deleting orphan stream session {sid} ({summ.get('state')})")
            await c._delete_session(sid)

    # -- supervision: TTL renew, pages lost while live ------------------------
    async def _supervise(self) -> None:
        last_renew = 0.0
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(1.0)
            now = loop.time()
            if not self._adopted and self.stream.reachable:
                await self._on_stream_connection(True)     # the first listing failed: retry
            if self._sweep_at is not None and now >= self._sweep_at:
                self._sweep_at = None
                await self._sweep_orphans()
            for c in self.controllers.values():
                if c._page() is not None or c._handshake is not None:
                    continue
                expired = (c._page_lost_at is not None
                           and now - c._page_lost_at > self.cfg.timeouts.adopt_wait_s)
                if expired and c.session and not c._stale_session:
                    if c.layer == "live" and c.desired != "art":
                        # Live renders whether or not a page shows it: the session runs on; a page that comes
                        # back (or any other) joins it.
                        if not c._headless():
                            c._log("info", "page gone for too long while live; the session runs on without it")
                            c.session["headless"] = True
                            c._join_cid = None
                            c._changed()
                    else:
                        c._log("warning", "page gone for too long while live; ending the session")
                        c.layer = "art"
                        c._stale_session = True
                        c.wake()
                headless = c._headless()
                waiting = c._page_lost_at is not None and not expired     # S13: the lost page may come back
                if not waiting and (expired or headless or not c._busy()) and c.promote_standby(
                        "the driven page did not come back" if expired else "no driven page"):
                    continue
                if expired and (not c.session or headless):
                    c._page_lost_at = None
            if now - last_renew >= self.cfg.renew_every_s and self.stream.reachable:
                last_renew = now
                await self._sweep_orphans()
                for c in self.controllers.values():
                    sids = set()
                    if c.session and not c.session.get("gone"):
                        sids.add(c.session["id"])
                    if c._adopt:
                        sids |= set(c._adopt["cands"])
                    for sid in sids:
                        try:
                            await self.stream.renew(sid)
                        except StreamError as e:
                            if e.status == 404 and c.session and c.session["id"] == sid and c._handshake is None:
                                c.session_lost("session expired (renew 404)", gone=True)

    def status(self) -> dict:
        return {sid: c.status() for sid, c in self.controllers.items()}
