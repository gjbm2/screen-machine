"""Live art feed: each live-enabled screen's art as the TV's art page shows it, light mask included, every second.

The renderer's `live_art` scene polls GET /api/v1/screens/{id}/art (live_art.py), which serves `LiveArtFeed.lookup`.
Owner (15 Oct): Live Art follows the TV's art every second (a new publish, or a playing clip, must show up) and
carries the light mask the room had in Normal, frozen for the show. The renderer never masks and the live video on
the TV is unmasked, so the mask is baked in here and the effects go on top of it unmasked. Checking costs three
HEADs; work happens only on a change:

* **Pick** (every 1.0 s, on a fixed monotonic grid): HEAD /output/<id>.jpg|.JPG|.mp4 on Flask and choose exactly
  as the TV page does (useFilePolling.ts): the newest Last-Modified wins, ties mp4 > JPG > jpg. The identity is
  (ext, Flask's ETag, else Last-Modified + Content-Length), so a same-second republish is caught. A failed HEAD
  keeps the last frame.
* **Still**: ONE capture in this feed's own headless Chrome (not the cut snapshotter's: its own browser, contexts,
  pages and lock), launched through a wrapper that runs it at `nice -n 15`: the art-only page renders the still
  with the screen's mask and the TV's flags (nomask/nocolour off, fit), then CDP Page.captureScreenshot as JPEG.
  Viewport = the size the screen's live session renders at (ScreenController._render_size), else the display
  size, else 3840x2160.
* **Clip**: the TV page's slow-motion loop (ClipCycle.ts), ported below as `ClipTimeline`, sampled once per wall
  second. One ffprobe and ONE ffmpeg pass per publish (nice 19, 2 threads, bounded) decode the frames the samples
  need, letterboxed to the viewport, as JPEG files under the room state dir (BT.709 clips converted to the JPEG's
  BT.601 matrix, as Chrome shows them), with the mask applied in RGB (`mask_factors`); the cross-fade to the first
  frame is blended from those (already masked) frames by a second niced ffmpeg pass (never in this process: a 4K
  blend holds the GIL). The cycle clock lives here (not in the renderer, whose
  director rebuilds the scene every dwell): it starts when the clip is first seen, and an entry cut's anchor
  (ctl._shot, read only) for the same clip moves it so that the first frame served after the cut is the grid frame
  nearest the anchor's clip_t. An exit's snapshot never moves it (the stream is still on the glass then).
* **CPU**: one heavy job (a capture or a decode) at a time across all screens, and none while any screen is in a
  cut (a handshake or a cut phase): a new job waits for the next tick, and a job already running when a cut starts
  is cancelled (ffmpeg killed) and redone after it. A still (about a second's work) goes before another screen's
  mask-only clip re-decode (~10 s at 4K): it cancels it, and the re-decode is redone when the lock is free. A
  failure keeps the last frame and retries with a backoff (1 s, doubling, at most 30 s) per art and size (a
  moving mask never resets it).
* **Mask**: the screen's pushed mask (what room pushes to the TV page: `MaskPusher.pushed`), quantised (`mask_key`:
  brightness and warm_alpha to 0.005, warm_hex exact) and part of each frame's identity and ETag. The page draws
  it as a black layer at opacity 1 - brightness under a warm_hex layer (mix-blend-mode: multiply) at opacity
  warm_alpha (MaskController.ts, MaskLayers.tsx, art/styles.ts), composited in sRGB, so per channel
  out = in * brightness * (1 - warm_alpha + warm_alpha * W). An art change is worked with the mask current then.
  A mask-only change is worked only when the value is the one the show keeps: the screen is live or heading live
  (desired live), or the mask is held (sensor_mask: suspended, the live modes' first step, or a sensor hold); in
  Normal (sensor following) it waits for the next art change. A held value is latched: the first one seen is kept
  until the hold ends (owner: the Normal value, fixed for the show), so it is picked up at the next tick and the
  frame is usually ready before the cut (~2 s after the suspend); a following one is redone at most every
  MASK_FOLLOW_REDO_S. With nothing pushed (a room restart mid-show: nothing is pushed while suspended, and the
  page follows Flask's /mask by itself), the value is what the page last reported drawing, else Flask's last
  fetched one, latched the same way.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import logging
import math
import re
import secrets
import shlex
import shutil
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import aiohttp

from .snapshot import CLIP_PROTOCOLS, origin_of, safe_art_state
from .util import describe_exc, fit_pixels, spawn

if TYPE_CHECKING:
    from .config import RoomConfig

log = logging.getLogger("room_service.live_art_feed")

TICK_S = 1.0
HEAD_TIMEOUT_S = 2.0
EXTS = ("jpg", "JPG", "mp4")
NEUTRAL_MASK = {"brightness": 1.0, "warm_hex": "#FFFFFF", "warm_alpha": 0.0}
TV_FLAGS = {"nomask": False, "nocolour": False, "fit": "fit"}   # what the TVs' pages run with (their hello flags)
MASK_STEPS = 200             # brightness and warm_alpha are quantised to 1 / 200 = 0.005
MASK_FOLLOW_REDO_S = 30.0    # a mask-only redo while the mask still follows the sensor (live, not suspended)
DEFAULT_VIEWPORT = (3840, 2160)
MAX_SAMPLES = 240            # clip frames kept per publish; a longer cycle is sampled every ceil(cycle / 240) s
PIN_MAX_S = 30.0             # a cut's anchor waits this long for the renderer's first fetch before the cycle runs
RETRY_MAX_S = 30.0
CHROME_NICE = 15
FFMPEG_NICE = 19
JPEG_QUALITY = 90
STILL_TIMEOUT_S = 45.0       # page (re)load + render + capture, niced
RENDER_TIMEOUT_S = 15.0
CAPTURE_TIMEOUT_S = 10.0
PAGE_RECYCLE_AFTER = 200     # captures per page before it is rebuilt (bounds Chrome's image cache)

# ---------------------------------------------------------------------------------------------------------------
# ClipCycle.ts, ported. Constants as exported there (ms -> s).
# ---------------------------------------------------------------------------------------------------------------

EASE_IN_S = 2.5              # EASE_IN_DURATION
EASE_OUT_S = 2.5             # EASE_OUT_DURATION
HOLD_FINAL_S = 4.0           # HOLD_FINAL_FRAME
PRIMER_S = 1.5               # PRIMER_PLAYBACK_DURATION
RESTART_DELAY_S = 2.0        # LOOP_RESTART_DELAY
CROSSFADE_S = 8.0            # CROSSFADE_DURATION
FIRST_FRAME_FADE_OUT_S = 0.5  # FIRST_FRAME_FADE_OUT_DURATION: the overlay (frame 0) fades off over the restarted
#                               clip, which is on frame 0 too: no visible change, so not modelled.
PACE = 0.6
SLOW = 0.065
CROSSFADE_EASING = (0.65, 0.0, 0.35, 1.0)   # ArtMedia.tsx OPACITY_EASING, cubic-bezier(0.65, 0, 0.35, 1)


def ease_in_out_quad(p: float) -> float:
    return 2 * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 2 / 2


def ease_area(p: float) -> float:
    """The integral of easeInOutQuad from 0 to p (p in [0, 1]); 0.5 at p = 1."""
    p = min(1.0, max(0.0, p))
    return 2 * p ** 3 / 3 if p < 0.5 else p - 0.5 + 2 * (1 - p) ** 3 / 3


def cubic_bezier(x1: float, y1: float, x2: float, y2: float, x: float) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2) at progress x (bisection on the curve's x)."""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        s = (lo + hi) / 2
        if 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3 < x:
            lo = s
        else:
            hi = s
    s = (lo + hi) / 2
    return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3


class ClipTimeline:
    """One cycle of ClipCycle.begin() for a clip of `duration` s, as a function of wall time t in the cycle.

    seek to 0 -> primer at SLOW for PRIMER_S -> ease SLOW..PACE over EASE_IN_S (easeInOutQuad on the rate) ->
    cruise at PACE for `cruise_s` (ClipCycle's playUntil formula, unchanged) -> ease PACE..SLOW over EASE_OUT_S ->
    pause on that frame for HOLD_FINAL_S -> the first frame fades in over it for CROSSFADE_S (the CSS easing) ->
    RESTART_DELAY_S on the first frame -> the next cycle. The <video> stops at its end (no loop attribute), so the
    media position is clamped to the duration. The seek and rAF latencies (tens of ms) are not modelled."""

    def __init__(self, duration: float) -> None:
        d = float(duration)
        if not math.isfinite(d) or d <= 0:
            raise ValueError(f"clip duration {duration!r}")
        self.duration = d
        primer_media = PRIMER_S * SLOW
        ease_in_media = EASE_IN_S * (PACE + SLOW) / 2
        self.cruise_s = max(0.0, d / PACE - (primer_media + ease_in_media) - EASE_OUT_S)
        self.t_ease_in = PRIMER_S
        self.t_cruise = self.t_ease_in + EASE_IN_S
        self.t_ease_out = self.t_cruise + self.cruise_s
        self.t_hold = self.t_ease_out + EASE_OUT_S
        self.t_crossfade = self.t_hold + HOLD_FINAL_S
        self.t_restart = self.t_crossfade + CROSSFADE_S
        self.cycle = self.t_restart + RESTART_DELAY_S

    def _raw(self, t: float) -> float:
        if t <= 0:
            return 0.0
        if t < self.t_ease_in:
            return SLOW * t
        m = SLOW * PRIMER_S
        if t < self.t_cruise:
            u = t - self.t_ease_in
            return m + SLOW * u + (PACE - SLOW) * EASE_IN_S * ease_area(u / EASE_IN_S)
        m += EASE_IN_S * (PACE + SLOW) / 2
        if t < self.t_ease_out:
            return m + PACE * (t - self.t_cruise)
        m += PACE * self.cruise_s
        u = min(t, self.t_hold) - self.t_ease_out
        return m + PACE * u + (SLOW - PACE) * EASE_OUT_S * ease_area(u / EASE_OUT_S)

    def media_time(self, t: float) -> float:
        """The clip's position at cycle time t (wraps at `cycle`)."""
        t = t % self.cycle
        return min(self.duration, self._raw(min(t, self.t_hold)))

    def first_frame_weight(self, t: float) -> float:
        """Opacity of the first-frame overlay at cycle time t (0 outside the cross-fade and the restart delay)."""
        t = t % self.cycle
        if t < self.t_crossfade:
            return 0.0
        if t < self.t_restart:
            return cubic_bezier(*CROSSFADE_EASING, (t - self.t_crossfade) / CROSSFADE_S)
        return 1.0

    def phase_of(self, clip_t: float) -> float:
        """The earliest cycle time at which the clip reaches clip_t (the hold's start when clip_t is past it)."""
        target = min(float(clip_t), self.media_time(self.t_hold))
        if target <= 0:
            return 0.0
        lo, hi = 0.0, self.t_hold
        for _ in range(60):
            mid = (lo + hi) / 2
            if self._raw(mid) < target:
                lo = mid
            else:
                hi = mid
        return hi


def frame_index(media_t: float, fps: float, nb_frames: int) -> int:
    """The frame a <video> shows at media_t (the last one starting at or before it)."""
    return max(0, min(nb_frames - 1, int(math.floor(media_t * fps + 1e-6))))


def plan_video(tl: ClipTimeline, fps: float, nb_frames: int,
               max_samples: int = MAX_SAMPLES) -> tuple[float, list[tuple[int, float]]]:
    """(step_s, samples): one sample per `step_s` of wall time over one cycle, each (frame index, first-frame
    weight). A weight of 0 is that frame; 1 is the first frame; between, a blend of that frame and the first."""
    step = 1.0 if tl.cycle <= max_samples else float(math.ceil(tl.cycle / max_samples))
    n = max(1, math.ceil(tl.cycle / step - 1e-9))
    samples = []
    for k in range(n):
        t = k * step
        w = round(tl.first_frame_weight(t), 4)
        if w >= 1.0:
            samples.append((0, 0.0))
        else:
            samples.append((frame_index(tl.media_time(t), fps, nb_frames), w))
    return step, samples


# ---------------------------------------------------------------------------------------------------------------
# The art pick (useFilePolling.ts)
# ---------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Art:
    sid: str
    ext: str
    ident: str      # changes whenever the file does
    lm_ms: int      # Last-Modified in ms: the TV page's ?t= cache-buster

    @property
    def kind(self) -> str:
        return "video" if self.ext == "mp4" else "image"

    @property
    def base_url(self) -> str:
        return f"/output/{self.sid}.{self.ext}?t={self.lm_ms}"


UNKNOWN = object()   # a HEAD failed: keep what we had


def _lm_ms(h: dict) -> int:
    try:
        return int(parsedate_to_datetime(h.get("lm")).timestamp() * 1000) if h.get("lm") else 0
    except (TypeError, ValueError, IndexError):
        return 0


def ident_of(ext: str, h: dict) -> str:
    return f"{ext}|{h['etag']}" if h.get("etag") else f"{ext}|{_lm_ms(h)}:{h.get('len')}"


def choose_art(sid: str, heads: dict[str, tuple[int, dict] | None]) -> Any:
    """The TV page's pick from HEAD results {ext: (status, {lm, etag, len}) | None (failed)}:
    an Art, None (no art), or UNKNOWN (a HEAD failed)."""
    if any(heads.get(x) is None for x in EXTS):
        return UNKNOWN
    hs = {x: (heads[x][1] if heads[x][0] == 200 else {}) for x in EXTS}
    jpg, JPG, mp4 = (_lm_ms(hs[x]) for x in EXTS)
    # useFilePolling: max(jpg, JPG) > mp4 ? (jpg > JPG ? "jpg" : "JPG") : "mp4"
    ext = ("jpg" if jpg > JPG else "JPG") if max(jpg, JPG) > mp4 else "mp4"
    latest = max(jpg, JPG, mp4)
    if latest <= 0:
        return None
    return Art(sid, ext, ident_of(ext, hs[ext]), latest)


def path_t(url: Any) -> tuple[str, str] | None:
    """(path, ?t=) of an /output URL: how a cut's anchor state names the same clip as a pick."""
    if not isinstance(url, str):
        return None
    p = urlsplit(url)
    return p.path, (parse_qs(p.query).get("t") or [""])[0]


# ---------------------------------------------------------------------------------------------------------------
# The light mask, as the page draws it (MaskController.ts, MaskLayers.tsx, art/styles.ts)
# ---------------------------------------------------------------------------------------------------------------

_HEX6 = re.compile(r"#[0-9A-Fa-f]{6}")


def _unit(v: Any, fallback: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return fallback
    return min(1.0, max(0.0, f)) if math.isfinite(f) else fallback


def mask_key(m: Any) -> tuple[int, str, int]:
    """A mask as the page would take it (normaliseMask: clamped to 0-1; a bad colour is neutral here), quantised so
    sensor noise never churns: (brightness, warm_hex, warm_alpha), the two levels in steps of 1 / MASK_STEPS and the
    colour exact. No value (nothing pushed yet) is neutral."""
    m = m if isinstance(m, dict) else {}
    hexv = m.get("warm_hex")
    hexv = hexv.upper() if isinstance(hexv, str) and _HEX6.fullmatch(hexv) else "#FFFFFF"
    return (int(round(_unit(m.get("brightness"), 1.0) * MASK_STEPS)), hexv,
            int(round(_unit(m.get("warm_alpha"), 0.0) * MASK_STEPS)))


def mask_of(key: tuple[int, str, int]) -> dict:
    return {"brightness": key[0] / MASK_STEPS, "warm_hex": key[1], "warm_alpha": key[2] / MASK_STEPS}


def mask_tag(key: tuple[int, str, int]) -> str:
    """A short id of a quantised mask, for ETags and the status: m<brightness>/<hex>/<alpha> in 1/200 steps."""
    return f"m{key[0]:03d}{key[1][1:]}{key[2]:03d}"


def mask_factors(m: dict) -> tuple[float, float, float]:
    """The page's mask as a gain per sRGB channel. Over the art: the brightness layer (black, opacity 1 - b) leaves
    x = in * b; the warm layer (warm_hex W, mix-blend-mode multiply, opacity a, on the opaque art stack) composites
    x * W at a over x, i.e. x * (1 - a) + a * x * W. So out = in * b * (1 - a + a * W), W per channel in 0..1. The
    order of the two layers does not matter (both multiply); the TVs run without nomask/nocolour."""
    b = _unit(m.get("brightness"), 1.0)
    a = _unit(m.get("warm_alpha"), 0.0)
    hexv = m.get("warm_hex")
    hexv = hexv if isinstance(hexv, str) and _HEX6.fullmatch(hexv) else "#FFFFFF"
    return tuple(b * (1 - a + a * int(hexv[i:i + 2], 16) / 255) for i in (1, 3, 5))   # type: ignore[return-value]


def mask_filter(m: dict | None) -> str:
    """The mask as ffmpeg filters on RGB (each followed by a comma), or "" for a mask that draws nothing."""
    f = mask_factors(m) if m else (1.0, 1.0, 1.0)
    if min(f) >= 1.0 - 1e-6:
        return ""
    return f"format=rgb24,colorchannelmixer=rr={f[0]:.6f}:gg={f[1]:.6f}:bb={f[2]:.6f},"


# ---------------------------------------------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class StillFrame:
    ident: str
    vp: tuple[int, int]
    jpeg: bytes
    etag: str


@dataclass
class VideoFrames:
    ident: str
    key: tuple[str, str] | None      # path_t of the clip (matches a cut anchor's base_url)
    vp: tuple[int, int]
    timeline: ClipTimeline
    step: float
    frames: list[tuple[Path, str]]   # per sample: (JPEG file, ETag)
    medias: list[float]              # per sample: the clip position (anchor snapping)
    gen_dir: Path

    @property
    def cycle(self) -> float:
        return self.timeline.cycle

    def grid_index_for(self, clip_t: float) -> int:
        """The sample whose clip position is nearest clip_t, around the moment the cycle first reaches it."""
        lo = min(len(self.frames) - 1, int((self.timeline.phase_of(clip_t) + 1e-9) // self.step))
        hi = min(lo + 1, len(self.frames) - 1)
        return min((lo, hi), key=lambda k: (abs(self.medias[k] - clip_t), k))


@dataclass
class Served:
    status: str                      # ok | warming | no_art | off
    detail: str = ""
    kind: str | None = None          # still | video
    etag: str | None = None
    body: bytes | None = None
    path: Path | None = None
    next_s: float | None = None


@dataclass
class _Screen:
    sid: str
    ctl: Any
    art: Any = None                  # Art | None
    known: bool = False              # a pick has completed (art None then means "no art")
    committed: Any = None            # StillFrame | VideoFrames
    have: Any = None                 # (ident, vp, mask_key) of `committed`
    committed_at: float = -math.inf  # loop time of the last commit
    job: asyncio.Task | None = None
    job_key: Any = None
    clocks: dict[str, float] = field(default_factory=dict)   # clip ident -> loop time of its cycle's start
    anchor: dict | None = None       # an entry's anchor, applied at the next lookup: {key, clip_t, seen_at}
    shot_seen: Any = None
    hs_prev: str | None = None       # ctl._handshake at the last tick (an entry's start re-arms the anchor)
    latch: Any = None                # while the mask is held: (source rank, mask_key, suspended) of the value kept
    fails: int = 0
    fail_key: Any = None             # the want of the last failure (the backoff is per art and size: want[:2])
    retry_at: float = 0.0
    last_error: str | None = None
    skips: int = 0
    cache: tuple[str, bytes] | None = None
    gens: list[Path] = field(default_factory=list)
    vp: tuple[int, int] | None = None   # the viewport of the current art (art_viewport), from the last tick


_UNSET = object()


class CutStarted(Exception):
    """A cut began during a heavy job: it is abandoned (no backoff) and redone after the cut."""


def _cut_phases() -> frozenset[str]:
    from .screens import PHASES       # lazily: screens imports live_art, which imports this module
    return frozenset(PHASES) - {"idle", "live", "failed"}


async def art_viewport(ctl: Any, art: Any) -> tuple[int, int]:
    """The art's own shape at the live profile's pixel count, read from the /output/ file (ctl._art_shape_of, cached by
    mtime): exactly the size the screens give the session at an entry, so a frame captured before the cut already
    matches the session and nothing is re-captured or letterboxed twice. Falls back to viewport_for (the configured
    display size until an entry has set the screen's art shape)."""
    shape_of = getattr(ctl, "_art_shape_of", None)
    prof = (getattr(ctl, "session", None) or {}).get("profile") or getattr(ctl.cfg, "profile", None)
    try:
        shape = await shape_of({"base_url": art.base_url}) if shape_of is not None else None
        cat = ctl.mgr.stream.cached_catalog() or {}
        p = (cat.get("profiles") or {}).get(prof) or {}
        pw, ph = p.get("w"), p.get("h")
        if shape and isinstance(pw, int) and isinstance(ph, int) and pw > 0 and ph > 0:
            size = fit_pixels(shape[0], shape[1], pw * ph)
            return size["width"], size["height"]
    except Exception as e:                  # an unreadable file or no catalog yet: the screens' own sizing
        log.debug("live art %s: art shape unknown (%s)", getattr(ctl, "id", "?"), describe_exc(e))
    return viewport_for(ctl)


def viewport_for(ctl: Any) -> tuple[int, int]:
    """The size the screen's live session renders at (the glass's shape at the profile's pixel count), as the
    screens compute it for POST /api/sessions; else the display's size; else 3840x2160."""
    prof = (getattr(ctl, "session", None) or {}).get("profile") or getattr(ctl.cfg, "profile", None)
    try:
        size = ctl._render_size(prof) or {}
    except Exception:
        size = {}
    w, h = size.get("width"), size.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return DEFAULT_VIEWPORT


# ---------------------------------------------------------------------------------------------------------------
# Heavy backends: our own niced Chrome, and ffmpeg
# ---------------------------------------------------------------------------------------------------------------

class ChromeArtCapturer:
    """This feed's own headless Chrome (never the snapshotter's): one context + page per screen at the art-only URL."""

    def __init__(self, cfg: "RoomConfig", work_dir: Path) -> None:
        self.cfg, self.scfg = cfg, cfg.snapshot
        self.base = cfg.art_page_base.rstrip("/")
        self.work_dir = Path(work_dir)
        self._pw: Any = None
        self._browser: Any = None
        self._starting: asyncio.Task | None = None
        self._pages: dict[str, dict] = {}

    def page_url(self, sid: str) -> str:
        return f"{self.base}/display/{sid}?artonly=1"

    def _wrapper(self) -> str:
        """A tiny launcher that execs the configured Chrome at nice 15 (Playwright needs one executable path)."""
        real = str(Path(self.scfg.executable_path).expanduser())
        nice = shutil.which("nice")
        if not nice:
            return real
        path = self.work_dir / "chrome-nice.sh"
        body = f"#!/bin/sh\nexec {shlex.quote(nice)} -n {CHROME_NICE} {shlex.quote(real)} \"$@\"\n"
        self.work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists() or path.read_text() != body:
            path.write_text(body)
        path.chmod(0o700)
        return str(path)

    async def _ensure_browser(self) -> Any:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        if self._starting is None or self._starting.done():
            self._starting = asyncio.get_running_loop().create_task(self._start_browser(),
                                                                     name="live-art-chrome-start")
            self._starting.add_done_callback(lambda t: t.cancelled() or t.exception())   # retrieved: no warning
        # Shielded: a capture cancelled here (newer art, a cut, its timeout) leaves the start to finish and be
        # reused. Cancelling the start itself would orphan a Playwright driver or a Chrome nothing closes.
        return await asyncio.shield(self._starting)

    async def _start_browser(self) -> Any:
        self._pages.clear()
        from playwright.async_api import async_playwright   # lazily: tests use fakes
        if self._pw is None:
            self._pw = await async_playwright().start()
        exe = self._wrapper()
        log.info("launching the live art Chrome (%s, nice %d)", exe, CHROME_NICE)
        self._browser = await self._pw.chromium.launch(executable_path=exe, headless=True,
                                                       args=list(self.scfg.headless_args))
        return self._browser

    async def _page(self, sid: str, vp: tuple[int, int]) -> dict:
        size = {"width": vp[0], "height": vp[1]}
        rec = self._pages.get(sid)
        if rec is not None and not rec["page"].is_closed() and not rec["crashed"]:
            if rec["vp"] != vp:
                await rec["page"].set_viewport_size(size)
                rec["vp"] = vp
            return rec
        await self._drop(sid)
        browser = await self._ensure_browser()
        ctx = await browser.new_context(viewport=size, device_scale_factor=1, color_scheme="dark")
        try:
            page = await ctx.new_page()
            rec = {"ctx": ctx, "page": page, "cdp": None, "vp": vp, "uses": 0, "crashed": False}
            page.on("crash", lambda *_: rec.__setitem__("crashed", True))
            ms = self.scfg.page_load_timeout_s * 1000
            await page.goto(self.page_url(sid), wait_until="load", timeout=ms)
            await page.wait_for_function("window.__smArtReady === true", timeout=ms)
            rec["cdp"] = await ctx.new_cdp_session(page)
        except BaseException:
            try:
                await ctx.close()
            except Exception:
                pass
            raise
        self._pages[sid] = rec
        return rec

    async def _drop(self, sid: str) -> None:
        rec = self._pages.pop(sid, None)
        if rec is not None:
            try:
                await rec["ctx"].close()
            except Exception:
                pass

    async def capture(self, sid: str, state: dict, vp: tuple[int, int]) -> bytes:
        rec = await self._page(sid, vp)
        try:
            await asyncio.wait_for(rec["page"].evaluate("s => window.__smRenderArt(s)", state), RENDER_TIMEOUT_S)
            shot = await asyncio.wait_for(rec["cdp"].send("Page.captureScreenshot", {
                "format": "jpeg", "quality": JPEG_QUALITY, "optimizeForSpeed": True, "fromSurface": True,
                "captureBeyondViewport": False}), CAPTURE_TIMEOUT_S)
        except BaseException:
            await self._drop(sid)        # a render or capture that failed leaves the page in an unknown state
            raise
        rec["uses"] += 1
        if rec["uses"] >= PAGE_RECYCLE_AFTER:
            await self._drop(sid)
        return base64.b64decode(shot["data"])

    async def close(self) -> None:
        start = self._starting
        if start is not None and not start.done():
            try:                                 # let a start in flight finish, so what it launched is ours to stop
                await asyncio.wait_for(asyncio.shield(start), 3.0)
            except Exception:
                start.cancel()
        for sid in list(self._pages):
            await self._drop(sid)
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None


async def _run(name: str, cmd: list[str], timeout_s: float) -> bytes:
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL,
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s)
    except BaseException:                # a timeout or a cancel (new art): never leave it running
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        raise
    if proc.returncode != 0:
        raise RuntimeError(f"{name} rc={proc.returncode}: {err.decode(errors='replace')[-200:].strip()}")
    return out


class FfmpegDecoder:
    def __init__(self, ffmpeg: str = "ffmpeg") -> None:
        self.ffmpeg = ffmpeg
        p = Path(ffmpeg)
        self.ffprobe = str(p.with_name("ffprobe")) if p.parent != Path(".") else "ffprobe"

    def _nice(self) -> list[str]:
        nice = shutil.which("nice")
        return [nice, "-n", str(FFMPEG_NICE)] if nice else []

    async def probe(self, url: str) -> dict:
        out = await _run("ffprobe", [*self._nice(), self.ffprobe, "-v", "error", "-protocol_whitelist", CLIP_PROTOCOLS,
                          "-select_streams", "v:0", "-show_entries",
                          "stream=avg_frame_rate,r_frame_rate,nb_frames,duration,height,color_space:format=duration",
                          "-of", "json", url], 20.0)
        info = json.loads(out or b"{}")
        st = (info.get("streams") or [{}])[0]
        fmt = info.get("format") or {}

        def num(v: Any) -> float:
            try:
                if isinstance(v, str) and "/" in v:
                    a, b = v.split("/", 1)
                    return float(a) / float(b) if float(b) else 0.0
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        dur = num(fmt.get("duration")) or num(st.get("duration"))     # <video>.duration is the container's
        fps = num(st.get("avg_frame_rate")) or num(st.get("r_frame_rate"))
        nb = int(num(st.get("nb_frames"))) or int(dur * fps)
        if dur <= 0 or fps <= 0 or nb <= 0:
            raise RuntimeError(f"ffprobe: no usable video stream (duration {dur}, fps {fps}, frames {nb})")
        # The source's Y'CbCr matrix: its tag, else Chrome's guess for an untagged clip (BT.601 below 720 lines).
        cs = str(st.get("color_space") or "unknown")
        in_matrix = "auto" if cs not in ("unknown", "reserved", "unspecified") else (
            "bt601" if 0 < num(st.get("height")) < 720 else "bt709")
        return {"duration": dur, "fps": fps, "nb_frames": nb, "in_matrix": in_matrix}

    async def decode(self, url: str, indices: list[int], vp: tuple[int, int], out_dir: Path,
                     timeout_s: float, in_matrix: str = "auto", mask: dict | None = None) -> list[Path]:
        """Frames `indices` (sorted, distinct) of `url`, letterboxed onto black at vp, as JPEG files, in one pass.
        The scale converts the clip's matrix (BT.709 for HD) to JPEG's full-range BT.601: swscale would otherwise
        change only the range, and every JPEG reader would show BT.709 values as BT.601 (saturated colours off by up
        to ~20/255 against what Chrome shows on the TV). A `mask` that draws anything is applied as the page draws
        it (mask_factors) on RGB: the scale then converts the clip's matrix straight to RGB, and the encoder's input
        is converted back to full-range BT.601 (both measured within ~1/255 of the unmasked path times the gains)."""
        w, h = vp
        sel = "+".join(f"eq(n,{i})" for i in indices)
        vf = (f"select='{sel}',scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2"
              f":in_color_matrix={in_matrix}:out_color_matrix=bt601:out_range=pc,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,{mask_filter(mask)}format=yuvj420p")
        await _run("ffmpeg", [*self._nice(), self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-protocol_whitelist", CLIP_PROTOCOLS, "-threads", "2", "-i", url,
                    "-an", "-sn", "-dn", "-filter_threads", "1", "-vf", vf, "-fps_mode", "passthrough",
                    "-frames:v", str(len(indices)), "-threads", "2", "-q:v", "3", "-f", "image2",
                    str(out_dir / "s%05d.jpg")], timeout_s)
        files = sorted(out_dir.glob("s*.jpg"))
        if not files:
            raise RuntimeError("ffmpeg decoded no frames")
        return files

    async def blend(self, a: Path, b: Path, weights: list[float], outs: list[Path], timeout_s: float) -> None:
        """outs[i] = a * (1 - weights[i]) + b * weights[i], as JPEG files, in ONE niced ffmpeg pass: off this
        process, whose loop (and Flask) a 4K Pillow blend would stall ~70 ms per frame (it holds the GIL). Both are
        our own frames (yuvj420p, one size); a linear mix of Y'CbCr is the same mix of R'G'B' (Chrome's opacity).
        Both are already masked, and a per-channel gain commutes with the mix (the page's mask sits above the
        cross-fade), so no mask here: it would be applied twice."""
        n = len(weights)
        fc = (f"[0:v]split={n}" + "".join(f"[a{i}]" for i in range(n)) + f";[1:v]split={n}"
              + "".join(f"[b{i}]" for i in range(n))
              + "".join(f";[a{i}][b{i}]mix=inputs=2:weights='{1 - w:.6f} {w:.6f}'[o{i}]"
                        for i, w in enumerate(weights)))
        cmd = [*self._nice(), self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-threads", "1", "-i", str(a), "-threads", "1", "-i", str(b),
               "-filter_complex_threads", "1", "-filter_complex", fc]
        for i, out in enumerate(outs):
            cmd += ["-map", f"[o{i}]", "-frames:v", "1", "-threads", "1", "-q:v", "3", "-update", "1",
                    "-f", "image2", str(out)]
        await _run("ffmpeg blend", cmd, timeout_s)
        missing = [o.name for o in outs if not o.exists()]
        if missing:
            raise RuntimeError(f"ffmpeg blended no {', '.join(missing)}")


# ---------------------------------------------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------------------------------------------

class LiveArtFeed:
    def __init__(self, svc: Any, *, capturer: Any = None, decoder: Any = None, work_dir: Path | None = None,
                 http: aiohttp.ClientSession | None = None, tick_s: float = TICK_S) -> None:
        self.svc = svc
        self.cfg = svc.cfg
        self.work_dir = Path(work_dir) if work_dir else Path(self.cfg.state_dir).expanduser() / "live_art"
        self.capturer = capturer if capturer is not None else ChromeArtCapturer(self.cfg, self.work_dir)
        self.decoder = decoder if decoder is not None else FfmpegDecoder(self.cfg.snapshot.ffmpeg)
        self._http = http
        self.flask = self.cfg.flask_url.rstrip("/")
        self.origins = {origin_of(self.cfg.art_page_base), origin_of(self.cfg.flask_url)}
        self.tick_s = tick_s
        self.cut_phases = _cut_phases()
        self.epoch = secrets.token_hex(3)          # frame ids never repeat across restarts
        self._gen = itertools.count(1)
        self._heavy = asyncio.Lock()               # one capture or decode at a time, all screens
        self._heavy_job: tuple | None = None       # (screen, task, want) of the last job to take _heavy
        self.screens: dict[str, _Screen] = {}
        self._tasks: list[asyncio.Task] = []
        self.captures = self.decodes = 0

    @property
    def http(self) -> aiohttp.ClientSession:
        return self._http or self.svc.http

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    # -- lifecycle ------------------------------------------------------------------------------------------
    async def start(self) -> None:
        for sid, ctl in self.svc.screens.controllers.items():
            if getattr(ctl.cfg, "live_enabled", False):
                self.screens[sid] = _Screen(sid, ctl)
        frames = self.work_dir / "frames"
        await asyncio.get_running_loop().run_in_executor(None, lambda: shutil.rmtree(frames, ignore_errors=True))
        for st in self.screens.values():
            self._tasks.append(spawn(self._loop(st), f"live-art-{st.sid}"))
        log.info("live art feed: %s", ", ".join(self.screens) or "no live-enabled screens")

    async def close(self) -> None:
        tasks = [*self._tasks, *(st.job for st in self.screens.values() if st.job)]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks = []
        close = getattr(self.capturer, "close", None)
        if close is not None:
            try:
                await asyncio.wait_for(close(), 5.0)
            except Exception as e:
                log.warning("live art Chrome did not close cleanly: %s", describe_exc(e))

    def status(self) -> dict:
        return {sid: {"art": st.art.base_url if st.art else None, "kind": st.art.kind if st.art else None,
                      "frame": type(st.committed).__name__ if st.committed else None,
                      "current": (st.have is not None and st.have[:2] == (st.art.ident, st.vp))
                      if st.art else None,
                      "mask": mask_tag(st.have[2]) if st.have else None,
                      "skips": st.skips, "error": st.last_error}
                for sid, st in self.screens.items()} | {"captures": self.captures, "decodes": self.decodes}

    # -- the 1 s tick ---------------------------------------------------------------------------------------
    async def _loop(self, st: _Screen) -> None:
        next_at = self._now()
        quiet = False
        while True:
            try:
                await self._tick(st)
                quiet = False
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not quiet:
                    log.warning("live art %s: tick failed: %s", st.sid, describe_exc(e))
                quiet = True
            next_at += self.tick_s
            now = self._now()
            if next_at < now:                      # missed ticks (a slow HEAD) are skipped; the grid is kept
                next_at += math.ceil((now - next_at) / self.tick_s) * self.tick_s
            await asyncio.sleep(next_at - now)

    def cut_busy(self) -> bool:
        """Any screen in a cut (a handshake, or a cut phase): the cut's snapshot owns the CPU."""
        for c in self.svc.screens.controllers.values():
            if getattr(c, "_handshake", None) is not None or getattr(c, "phase", "idle") in self.cut_phases:
                return True
        return False

    async def _head(self, sid: str, ext: str) -> tuple[int, dict] | None:
        try:
            async with self.http.head(f"{self.flask}/output/{sid}.{ext}", allow_redirects=False,
                                      timeout=aiohttp.ClientTimeout(total=HEAD_TIMEOUT_S)) as r:
                return r.status, {"lm": r.headers.get("Last-Modified"), "etag": r.headers.get("ETag"),
                                  "len": r.headers.get("Content-Length")}
        except asyncio.CancelledError:
            raise
        except Exception:
            return None

    async def pick(self, sid: str) -> Any:
        res = await asyncio.gather(*(self._head(sid, x) for x in EXTS))
        return choose_art(sid, dict(zip(EXTS, res)))

    def _mask_now(self, st: _Screen) -> tuple[tuple[int, str, int] | None, bool]:
        """(the screen's mask as a mask_key, whether it is held: sensor_mask suspended, a sensor hold or a hold
        tier). The best value known, ranked: the pushed one (what the TV page shows; nothing is pushed while
        suspended, so it is the Normal value then), else what the page last reported drawing, else the value last
        fetched from Flask (status()). The last two move: after a room restart mid-show nothing is pushed (the
        suspension is restored) and the page follows Flask's /mask by itself. So a held value is latched: the
        first one seen is kept until the hold ends (or a suspension starts), replaced only by a better-ranked
        source, so a moving fallback never churns the work. The mask key is None if no value is known or the
        mask cannot be read: nothing is redone for it."""
        m = getattr(self.svc, "mask", None)
        s: dict = {}
        if m is not None:
            try:
                s = m.status(st.sid) or {}
            except Exception:
                return None, False
        susp = bool(s.get("suspended"))
        held = susp or s.get("held") is True or s.get("tier") == "hold"
        pushed = getattr(m, "pushed", None)
        p = pushed.get(st.sid) if isinstance(pushed, dict) else None
        ks = getattr(st.ctl, "kiosk_state", None)
        ks = ks.get("mask") if isinstance(ks, dict) else None
        if p is not None:
            cur = (2, mask_key(p.as_dict() if hasattr(p, "as_dict") else p))
        elif isinstance(ks, dict) and ks.get("brightness") is not None:
            cur = (1, mask_key(ks))
        elif s.get("brightness") is not None:
            cur = (0 if isinstance(pushed, dict) else 2, mask_key(s))   # no `pushed` map: status() is the push
        else:
            cur = None
        if not held:
            st.latch = None
            return (cur[1] if cur else None), False
        if st.latch is not None and susp and not st.latch[2]:
            st.latch = None                        # a sensor hold turned into the show's suspension: take it now
        if cur is not None and (st.latch is None or cur[0] > st.latch[0]):
            st.latch = (cur[0], cur[1], susp)
        return (st.latch[1] if st.latch else None), True

    @staticmethod
    def _show_bound(ctl: Any) -> bool:
        """Live, or heading live (desired live): the frame's mask is the one the show keeps."""
        return getattr(ctl, "layer", "art") == "live" or getattr(ctl, "desired", "art") != "art"

    def _note_anchor(self, st: _Screen, now: float) -> None:
        """A new entry anchor (ctl._shot, read only) for a clip: the next lookup of that clip starts from it."""
        shot = getattr(st.ctl, "_shot", None)
        if shot is st.shot_seen:
            return
        st.shot_seen = shot
        if not shot:
            return
        if getattr(st.ctl, "layer", "art") == "live" and getattr(st.ctl, "_handshake", None) != "entry":
            # An exit's snapshot (taken while the stream is still on the glass; the renderer converges to it by
            # itself): re-anchoring on it would rewind the live art under the stream. Entries snapshot on art.
            return
        try:
            s = json.loads(shot[0])
            clip_t = float(s.get("clip_t") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return
        if isinstance(s, dict) and s.get("kind") == "video" and math.isfinite(clip_t):
            st.anchor = {"key": path_t(s.get("base_url")), "clip_t": max(0.0, clip_t), "seen_at": now}

    async def _tick(self, st: _Screen) -> None:
        now = self._now()
        hs = getattr(st.ctl, "_handshake", None)
        if hs == "entry" and st.hs_prev != "entry":
            st.shot_seen = _UNSET                  # an entry began: its anchor counts even if it reuses the last shot
        st.hs_prev = hs
        self._note_anchor(st, now)
        if st.job is not None and not st.job.done() and self.cut_busy():
            st.job.cancel()                        # a cut began mid-job (checked before the HEADs: Flask may lag)
            st.skips += 1
            return
        art = await self.pick(st.sid)
        if art is not UNKNOWN:
            st.art, st.known = art, True
        art = st.art
        if art is None:
            return
        if art.kind == "video" and art.ident not in st.clocks:
            st.clocks[art.ident] = now             # the TV starts its cycle when it sees the new clip
            for k in list(st.clocks)[:-4]:
                del st.clocks[k]
        st.vp = await art_viewport(st.ctl, art)
        base = (art.ident, st.vp)
        mk, held = self._mask_now(st)
        running = st.job is not None and not st.job.done()
        if running and st.job_key[:2] == base and (mk is None or not held):
            mk = st.job_key[2]                     # a mask-only change drops a job only for a held (final) value
        elif (st.have is not None and st.have[:2] == base
              and (mk is None or not (held or self._show_bound(st.ctl)))):
            mk = st.have[2]                        # Normal (sensor following): a mask-only change waits for new art
        elif mk is None:
            mk = mask_key(None)
        want = (*base, mk)
        replaced = False
        if running:
            if self.cut_busy():
                st.job.cancel()                    # a cut began during the HEADs: the cut owns the CPU; redone after
                st.skips += 1
                return
            if st.job_key == want:
                return
            st.job.cancel()                        # newer art, a new size or a held mask: drop the stale job
            replaced = True
        if st.have == want:
            return
        if (not held and st.have is not None and st.have[:2] == base
                and now - st.committed_at < MASK_FOLLOW_REDO_S):
            return                                 # live on a following mask: a mask-only redo now and then
        if st.fail_key is None or want[:2] != st.fail_key[:2]:
            st.retry_at = 0.0                      # new art or size; a moving mask keeps the backoff
        if now < st.retry_at:
            return
        if self.cut_busy():
            st.skips += 1
            return
        if self._heavy.locked():
            # The lock may be held by the job just dropped (the new one waits for it, not for the next tick), or by
            # another screen's mask-only clip re-decode, which a still puts back.
            preempted = art.kind == "image" and self._preempt_redecode(st)
            if not (replaced or preempted):
                st.skips += 1
                return
        st.job_key = want
        st.job = spawn(self._work(st, art, want), f"live-art-work-{st.sid}")

    def _preempt_redecode(self, st: _Screen) -> bool:
        """For a still on `st`: cancel another screen's running mask-only clip re-decode (its frames for that clip
        exist; only the mask differs). On a live mode's freeze every screen wants its held value; a 4K re-decode
        (~10 s) cannot finish before the cut (~2 s later), which cancels it anyway, while a still (~1 s) is then
        ready for the cut's first fetch. The re-decode is redone at its screen's first tick with the lock free."""
        job = self._heavy_job
        if job is None:
            return False
        o, task, want = job
        if (o is st or task.done() or not isinstance(o.committed, VideoFrames) or o.have is None
                or o.have[:2] != want[:2]):
            return False
        task.cancel()
        o.skips += 1
        log.info("live art %s: mask-only re-decode put back for a still on %s", o.sid, st.sid)
        return True

    async def _work(self, st: _Screen, art: Art, want: tuple) -> None:
        async with self._heavy:
            if self.cut_busy():                    # a cut started while we waited for the lock
                st.skips += 1
                return
            self._heavy_job = (st, asyncio.current_task(), want)   # (a finished task is never preempted)
            t0 = time.monotonic()
            frame: Any = None
            try:
                if art.kind == "video":
                    frame = await self._decode(st, art, want[1], want[2])
                else:
                    frame = await asyncio.wait_for(self._capture(st, art, want[1], want[2]), STILL_TIMEOUT_S)
                # Changed while we worked (a half-written publish, say)? Discard; the next tick has the new art.
                h = await self._head(st.sid, art.ext)
                if h is None or h[0] != 200 or ident_of(art.ext, h[1]) != art.ident:
                    log.info("live art %s: %s changed during the %s; retrying", st.sid, art.base_url,
                             "decode" if art.kind == "video" else "capture")
                    self._drop_frames(frame)
                    return
            except CutStarted:                     # _decode removed its files; no backoff
                st.skips += 1
                return
            except asyncio.CancelledError:         # newer art, a new size or a cut: a finished decode goes too
                self._drop_frames(frame)
                raise
            except Exception as e:
                self._drop_frames(frame)
                st.fails = st.fails + 1 if st.fail_key is not None and st.fail_key[:2] == want[:2] else 1
                st.fail_key = want
                st.retry_at = self._now() + min(RETRY_MAX_S, 2.0 ** (st.fails - 1))
                if st.fails == 1 or st.fails % 10 == 0:
                    log.warning("live art %s: %s of %s failed (%d): %s", st.sid,
                                "decode" if art.kind == "video" else "capture", art.base_url, st.fails,
                                describe_exc(e))
                st.last_error = describe_exc(e)
                return
            old = st.committed
            st.committed, st.have, st.fails, st.last_error = frame, want, 0, None
            st.committed_at = self._now()
            st.cache = None
            log.info("live art %s: %s %s at %dx%d, mask %s, in %.0f ms", st.sid,
                     "decoded" if art.kind == "video" else "captured", art.base_url, *want[1], mask_tag(want[2]),
                     (time.monotonic() - t0) * 1000)
            if isinstance(frame, VideoFrames):
                st.gens.append(frame.gen_dir)
            if isinstance(old, VideoFrames) or isinstance(frame, VideoFrames):
                while len(st.gens) > 2:            # keep the previous clip's files: a read may be in flight
                    self._discard(st.gens.pop(0))

    def _drop_frames(self, frame: Any) -> None:
        """An uncommitted decode's files (nothing else tracks them)."""
        if isinstance(frame, VideoFrames):
            self._discard(frame.gen_dir)

    def _discard(self, d: Path) -> None:
        async def rm() -> None:
            await asyncio.get_running_loop().run_in_executor(None, lambda: shutil.rmtree(d, ignore_errors=True))
        spawn(rm(), "live-art-rm")

    async def _capture(self, st: _Screen, art: Art, vp: tuple[int, int], mk: tuple[int, str, int]) -> StillFrame:
        v = hashlib.sha1(art.ident.encode()).hexdigest()[:10]     # a same-second republish is a new URL
        state = safe_art_state({"base_url": f"{art.base_url}&v={v}", "kind": "image", "clip_t": None,
                                "mask": mask_of(mk), "flags": dict(TV_FLAGS),
                                "viewport": {"w": vp[0], "h": vp[1]}}, self.origins)
        jpeg = await self.capturer.capture(st.sid, state, vp)
        if not jpeg or not jpeg.startswith(b"\xff\xd8"):
            raise RuntimeError("the capture returned no JPEG")
        self.captures += 1
        return StillFrame(art.ident, vp, jpeg, f"s{mask_tag(mk)}-{hashlib.sha1(jpeg).hexdigest()[:16]}")

    async def _decode(self, st: _Screen, art: Art, vp: tuple[int, int], mk: tuple[int, str, int]) -> VideoFrames:
        loop = asyncio.get_running_loop()
        url = f"{self.flask}/output/{st.sid}.mp4"
        info = await self.decoder.probe(url)
        tl = ClipTimeline(info["duration"])
        step, samples = plan_video(tl, info["fps"], info["nb_frames"])
        if step > 1:
            log.info("live art %s: %.0f s cycle sampled every %g s (at most %d frames)", st.sid, tl.cycle, step,
                     MAX_SAMPLES)
        indices = sorted({i for i, _ in samples} | {0})
        if self.cut_busy():                        # a cut began during the probe
            raise CutStarted()
        gen = f"{self.epoch}-{next(self._gen)}"
        gdir = self.work_dir / "frames" / st.sid / gen
        await loop.run_in_executor(None, lambda: gdir.mkdir(parents=True, exist_ok=True, mode=0o700))
        try:
            files = await self.decoder.decode(url, indices, vp, gdir, min(600.0, 60.0 + 4 * tl.duration),
                                              in_matrix=info.get("in_matrix", "auto"), mask=mask_of(mk))
            by_idx = {idx: files[min(i, len(files) - 1)] for i, idx in enumerate(indices)}

            def name(idx: int, w: float) -> str:
                return f"b{idx}-{int(round(w * 10000)):05d}"

            fades: dict[int, list[float]] = {}     # the cross-fade (of masked frames): held frame -> distinct weights
            for idx, w in samples:
                if w > 0 and w not in fades.get(idx, ()):
                    fades.setdefault(idx, []).append(w)
            for idx, ws in fades.items():
                if self.cut_busy():                # a cut began during the decode pass
                    raise CutStarted()
                await self.decoder.blend(by_idx[idx], by_idx[0], ws, [gdir / f"{name(idx, w)}.jpg" for w in ws],
                                         min(300.0, 30.0 + 5 * len(ws)))
            mt = mask_tag(mk)
            frames = [(by_idx[idx], f"v{gen}-{mt}-f{idx}") if w <= 0 else
                      (gdir / f"{name(idx, w)}.jpg", f"v{gen}-{mt}-{name(idx, w)}") for idx, w in samples]
        except BaseException:
            self._discard(gdir)
            raise
        self.decodes += 1
        return VideoFrames(art.ident, path_t(art.base_url), vp, tl, step, frames,
                           [tl.media_time(k * step) for k in range(len(samples))], gdir)

    # -- serving --------------------------------------------------------------------------------------------
    def lookup(self, sid: str, now: float | None = None) -> Served:
        """The frame to serve now (a request: it also starts a pending cut anchor's cycle)."""
        st = self.screens.get(sid)
        if st is None:
            return Served("off", "live art is off for this screen (not live-enabled)")
        now = self._now() if now is None else now
        self._note_anchor(st, now)
        c = st.committed
        if c is None:
            if st.known and st.art is None:
                return Served("no_art", f"no /output/{sid}.jpg|.JPG|.mp4 on Flask")
            return Served("warming", st.last_error or "the first frame for this art is being prepared")
        if isinstance(c, StillFrame):
            return Served("ok", kind="still", etag=c.etag, body=c.jpeg)
        clock0 = st.clocks.setdefault(c.ident, now)
        a = st.anchor
        if a is not None and a["key"] == c.key:
            # A cut's anchor: the cycle restarts at the sample nearest the anchor's frame, from this request (the
            # renderer's first fetch after the cut), or from PIN_MAX_S after the anchor if nothing fetched.
            k = c.grid_index_for(a["clip_t"])
            clock0 = min(now, a["seen_at"] + PIN_MAX_S) - k * c.step
            st.clocks[c.ident] = clock0
            st.anchor = None
        phase = (now - clock0) % c.cycle
        k = min(len(c.frames) - 1, int((phase + 1e-6) // c.step))
        next_s = max(0.0, min((k + 1) * c.step, c.cycle) - phase)
        path, etag = c.frames[k]
        return Served("ok", kind="video", etag=etag, path=path, next_s=next_s)

    async def read(self, sid: str, s: Served) -> bytes:
        if s.body is not None:
            return s.body
        st = self.screens[sid]
        if st.cache is not None and st.cache[0] == s.etag:
            return st.cache[1]
        body = await asyncio.get_running_loop().run_in_executor(None, s.path.read_bytes)
        st.cache = (s.etag, body)
        return body


def feed_off_reason(svc: Any) -> str | None:
    """Why this service runs no feed (it drives a real Chrome and ffmpeg, like the snapshotter), or None."""
    if not svc.cfg.snapshot.enabled:
        return "snapshots are off (snapshot.enabled: false)"
    if getattr(svc, "_snapshotter_override", None) is not None:
        return "a test snapshotter is installed"
    return None
