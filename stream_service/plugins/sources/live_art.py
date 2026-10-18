"""Live art: the screen's current art as a scene, so the effects director can put effects on the art itself.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

room_service serves each screen's current art frame at ``GET {SM_ROOM_URL}/api/v1/screens/{screen}/art``: with the
light mask the TV page draws, frozen at the room's Normal value for the show (the renderer never masks; effects on
top), JPEG normally (PNG allowed), ETag = the frame (304 on a matching If-None-Match), ``X-Art-Kind: still |
video`` and, for video, ``X-Art-Next-S`` = seconds until the next frame; JSON 404 for an unknown screen / no art;
503 + Retry-After while the first frame is prepared.  An older room has only ``/art.png``: a text/plain 404
(aiohttp's "no such route") on ``/art`` switches this instance to it.  SM_ROOM_URL defaults to the loopback door
(room_service runs inside the Flask app on this machine).

* A daemon thread (started in setup, stopped in close and by a finalizer) polls on fixed deadlines, never "1 s
  after the last fetch": after a video frame at arrival + clamp(X-Art-Next-S + 0.03, 0.05, 2) s, stills on a 1 s
  monotonic grid, a 503 after its Retry-After (at most 2 s), any other error after 5 s (one log line per streak;
  the last frame stays up).  It decodes off the render thread (``image.load_image``: letterboxed at the profile
  size) into one of two pinned host buffers and hands it over by version under a lock.  It never touches CUDA.
* Pinned host memory comes from a process-wide free list that is never freed (``_pinned_take``): cudaHostAlloc and
  cudaFreeHost hold the driver's lock for tens of ms, stalling every render thread's CUDA calls (a hitch on a live
  show), and the renderer empties CuPy's default pinned pool whenever a session ends.  So a block is allocated
  only at a new peak of live_art instances (or ahead of time by ``reserve_pinned``), and the poller hands its
  blocks back when it exits (a block whose last upload may still be running waits until its event is done).
* render (render thread) uploads a handed-over frame once: an async H2D copy from the pinned buffer on the
  current (render) stream, then a CUDA event.  The buffer goes back to the poller only once render has seen that
  event complete (``Event.done``, a query: never a synchronize).  No per-frame allocation, no host round trip.
* Fades: a video frame blends LINEARLY until the next frame arrives (X-Art-Next-S + the poll lead, 0.3-2 s;
  1 s without the header), so the motion is continuous; no history, so a fetch that lands mid-frame or a run of
  identical frames (304s) never skews the next fades.  A still (an art change) crossfades over ``crossfade_s``
  (smoothstep).  A frame arriving mid-fade freezes what is on screen as the new start, so nothing jumps.
  Otherwise a device copy.
* Host diet (each CUDA call costs ~45-70 us under WSL/WDDM and holds the GIL): ONE CUDA call per frame (a mix or a
  copy kernel: a kernel launch is cheaper on the host than a D2D cudaMemcpyAsync), and three on a frame where new
  art arrives (the H2D copy, its event, then one ``live_art_arrive`` kernel that freezes the running fade, expands
  RGB -> RGBA and writes the output in one pass; it was five).  The per-frame values (fade, gain, the freeze flag)
  ride as kernel arguments, which cost no extra call, so there is no uniform upload and no CUDA graph: a graph
  launch plus its parameter upload would be two calls where one launch does today.
* setup tries one synchronous fetch so the first frame is already the art (room_service has normally just
  prepared the same art for the cut, so it is cached); otherwise the scene starts black and the poller fills it.
* The session puts its screen id on the context (``ctx.screen``); without one (fxtest, previews) the scene is
  black and nothing is polled.

Macros: intensity = gain; pulse = a small brightness kick (+15 % at 1).  energy, speed, render_scale and seed do
nothing here: the art is a still at full resolution and the effects bring the motion.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import weakref
from collections import deque
from typing import NamedTuple, Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins.base import Source
from stream_service.plugins.sources.image import load_image

log = logging.getLogger("stream_service.plugins.live_art")

DEFAULT_ROOM = "http://127.0.0.1:8700"
POLL_S = 1.0                     # stills: a 1 s monotonic grid
ERROR_POLL_S = 5.0               # any other error: back off
WARMING_MAX_S = 2.0              # 503: honour Retry-After up to this
VIDEO_LEAD_S = 0.03              # poll this long after the next video frame is due ...
VIDEO_MIN_S, VIDEO_MAX_S = 0.05, 2.0   # ... clamped to this
PERIOD_DEFAULT_S, PERIOD_MIN_S, PERIOD_MAX_S = 1.0, 0.3, 2.0   # the video fade = the time to the next frame
FADE_SLACK_S = 0.02              # ... plus this (request latency, decode jitter, the render tick): never a hold
TIMEOUT_S = 4.0
MAX_BYTES = 64 * 1024 * 1024
PULSE_KICK = 0.15
N_BUFFERS = 2                    # pinned host buffers per instance (double-buffered hand-over)

FREE, FILLING, READY, INFLIGHT = range(4)   # a host buffer's state (changed under the feed lock)

# out = (a + (b - a) * k) * gain.  No __restrict__: `out` may alias `a` (freezing a fade in place).
_SRC = HELPERS + r"""
__device__ __forceinline__ uchar4 la_mix(const uchar4 pa, const uchar4 pb, const float k, const float gain)
{
    const float q = 1.f - k;
    return pack(make_float4((q * pa.x + k * pb.x) * gain, (q * pa.y + k * pb.y) * gain,
                            (q * pa.z + k * pb.z) * gain, 255.f));
}

extern "C" __global__
void live_art_mix(const uchar4* a, const uchar4* b, uchar4* out, const int W, const int H,
                  const float k, const float gain)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    out[i] = la_mix(a[i], b[i], k, gain);
}

// A new frame, in one pass: prev = what is on screen (the running fade, frozen) when `freeze`, else prev is
// already the old picture (render swapped the buffers); cur = the uploaded RGB frame as RGBA; out = the mix.
// Each thread reads and writes only its own pixel, so the in-place writes are safe.
extern "C" __global__
void live_art_arrive(uchar4* prev, uchar4* cur, const unsigned char* __restrict__ rgb, uchar4* out,
                     const int W, const int H, const int freeze, const float kf, const float k, const float gain)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    uchar4 pa = prev[i];
    if (freeze) { pa = la_mix(pa, cur[i], kf, 1.f); prev[i] = pa; }
    const uchar4 pb = make_uchar4(rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2], 255);
    cur[i] = pb;
    out[i] = la_mix(pa, pb, k, gain);
}

// out = src, 16 bytes per thread (n16 of them), then the < 16-byte tail one byte per thread of the first block.
extern "C" __global__
void live_art_copy(const uint4* __restrict__ src, uint4* __restrict__ out, const long long n16,
                   const unsigned char* __restrict__ src_b, unsigned char* __restrict__ out_b, const int tail)
{
    const long long i = (long long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n16) out[i] = src[i];
    if (blockIdx.x == 0 && threadIdx.x < tail) out_b[n16 * 16 + threadIdx.x] = src_b[n16 * 16 + threadIdx.x];
}
"""


def room_url() -> str:
    return (os.environ.get("SM_ROOM_URL") or DEFAULT_ROOM).rstrip("/")


def _header(hdrs, name: str) -> str:
    return ((hdrs.get(name) if hdrs is not None else None) or "").strip()


def _art_headers(hdrs) -> tuple[str, Optional[float]]:
    """(kind, seconds until the next video frame or None) from X-Art-Kind / X-Art-Next-S.  No headers (an
    older room) = a still."""
    if _header(hdrs, "X-Art-Kind").lower() != "video":
        return "still", None
    try:
        nxt = float(_header(hdrs, "X-Art-Next-S"))
    except ValueError:
        return "video", None
    return "video", (max(0.0, nxt) if math.isfinite(nxt) else None)


def _retry_after(hdrs) -> float:
    try:
        s = float(_header(hdrs, "Retry-After"))
    except ValueError:
        s = 1.0
    return min(WARMING_MAX_S, max(VIDEO_MIN_S, s if math.isfinite(s) else 1.0))


def _video_fade_s(next_s: Optional[float]) -> float:
    """A video frame's linear fade: until the next frame arrives (the next poll is due X-Art-Next-S + the lead
    after this one), so each blend ends as the next begins.  The room's own answer, not a measured average: a fetch
    that lands mid-frame fades over what is left of that frame, and a run of identical frames (304s) cannot
    stretch the fades that follow it.  1 s (the still grid) when the room sends no X-Art-Next-S."""
    if next_s is None:
        return PERIOD_DEFAULT_S
    return min(PERIOD_MAX_S, max(PERIOD_MIN_S, next_s + VIDEO_LEAD_S + FADE_SLACK_S))


# Pinned host blocks, process-wide and never freed (see the module docstring): nbytes -> blocks nobody holds, and
# blocks handed back while their last upload may still run (nbytes, block, event).  deque append / pop are atomic,
# so no lock: blocks come back from a poller thread as it exits.  Kept across a hot reload (importlib.reload re-runs
# this module in the same namespace): fresh lists would orphan the reserved blocks (pinned memory never freed) and make
# the next live_art cudaHostAlloc during a show.
_PINNED_FREE: dict[int, deque] = globals().get("_PINNED_FREE", {})
_PINNED_PENDING: deque = globals().get("_PINNED_PENDING", deque())


def _pinned_sweep() -> None:
    """Pending blocks whose last upload has completed join the free list (event queries: never blocks)."""
    for _ in range(len(_PINNED_PENDING)):
        try:
            item = _PINNED_PENDING.popleft()
        except IndexError:
            return
        if item[2].done:
            _PINNED_FREE.setdefault(item[0], deque()).append(item[1])
        else:
            _PINNED_PENDING.append(item)


def _pinned_new(cp, nbytes: int):
    """A fresh pinned block (cudaHostAlloc: stalls the driver, so only when the free list is empty).  Portable, so
    a block can serve a source on any GPU; outside CuPy's default pool, which the renderer empties."""
    return cp.cuda.PinnedMemoryPointer(cp.cuda.PinnedMemory(nbytes, cp.cuda.runtime.hostAllocPortable), 0)


def _pinned_take(cp, nbytes: int):
    """A pinned block of ``nbytes`` (setup, worker thread on the source's device)."""
    _pinned_sweep()
    try:
        return _PINNED_FREE.setdefault(nbytes, deque()).pop()
    except IndexError:
        return _pinned_new(cp, nbytes)


def _pinned_give_back(bufs) -> None:
    """Return host buffers' blocks to the free list (an upload still in flight: pending until its event is done)."""
    for b in bufs:
        if b.mem is None:
            continue
        if b.state == INFLIGHT:
            _PINNED_PENDING.append((b.host.nbytes, b.mem, b.event))
        else:
            _PINNED_FREE.setdefault(b.host.nbytes, deque()).append(b.mem)
        b.mem = None


def reserve_pinned(cp, width: int, height: int, count: int = 2 * N_BUFFERS) -> None:
    """Make sure ``count`` blocks for width x height are free (call before any render thread runs, e.g. when a
    GPU is warmed), so not even the first live_art allocates during a show."""
    nbytes = width * height * 3
    free = _PINNED_FREE.setdefault(nbytes, deque())
    while len(free) < count:
        free.append(_pinned_new(cp, nbytes))


class _HostBuf:
    """One (H, W, 3) uint8 host buffer: the poller fills it, render uploads from it.  In the source ``host`` is a
    view over pinned memory (``mem`` keeps it alive) and ``event`` a CUDA event recorded after its upload; the
    feed only reads ``event.done``."""

    __slots__ = ("host", "ptr", "mem", "event", "state", "version", "kind", "fade_s")

    def __init__(self, host: np.ndarray, event, mem=None) -> None:
        self.host, self.ptr, self.mem, self.event = host, host.ctypes.data, mem, event
        self.state, self.version, self.kind, self.fade_s = FREE, 0, "still", PERIOD_DEFAULT_S


class _Poll(NamedTuple):
    status: str                  # new | same (304) | busy (no free buffer) | warming (503) | fallback | error
    t: float                     # monotonic time the response (its headers) arrived
    kind: str = "still"
    next_s: Optional[float] = None
    retry_s: float = 0.0


class _ArtFeed:
    """Polls one screen's art and hands the newest decoded frame over to render through ``bufs``
    (FREE -> FILLING (poller) -> READY -> INFLIGHT (render: copy issued) -> FREE once its event is done)."""

    def __init__(self, url: str, fallback_url: str, width: int, height: int, bufs) -> None:
        self.url, self.fallback_url, self.w, self.h = url, fallback_url, width, height
        self.bufs = list(bufs)
        self.etag: Optional[str] = None
        self.lock = threading.Lock()
        self.version = 0                            # of the newest handed-over frame
        self.stop = threading.Event()
        self.closed = False
        self.next_t = time.monotonic() + POLL_S     # the poller's next deadline (setup sets it from its fetch)
        self._streak: Optional[str] = None          # the current error streak (logged once)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # loopback: never a proxy

    # -- poller thread -------------------------------------------------------------------
    def fetch(self) -> _Poll:
        """One GET (decoded and handed over if new).  Never raises."""
        headers = {"Accept": "image/jpeg, image/png"}
        if self.etag:
            headers["If-None-Match"] = self.etag
        try:
            with self._opener.open(urllib.request.Request(self.url, headers=headers), timeout=TIMEOUT_S) as r:
                t = time.monotonic()
                kind, next_s = _art_headers(r.headers)
                etag = r.headers.get("ETag")
                data = r.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError(f"the art frame is over {MAX_BYTES} bytes")
            pixels = load_image(data, self.w, self.h)
        except urllib.error.HTTPError as e:
            t, code, hdrs = time.monotonic(), e.code, e.headers
            e.close()
            if code == 304:
                self._ok()
                return _Poll("same", t, *_art_headers(hdrs))
            if (code == 404 and self.url != self.fallback_url
                    and _header(hdrs, "Content-Type").split(";")[0].strip().lower() == "text/plain"):
                log.info("live_art %s: no such route (an older room); using %s", self.url, self.fallback_url)
                self.url, self.etag = self.fallback_url, None
                return _Poll("fallback", t)
            if code == 503:
                self._fail("HTTP 503 (warming)", quiet=True)
                return _Poll("warming", t, retry_s=_retry_after(hdrs))
            self._fail(f"HTTP {code}", quiet=code == 404)
            return _Poll("error", t)
        except Exception as e:  # URLError, timeouts, a bad image (ParamError), ...: keep the last frame
            self._fail(f"{type(e).__name__}: {e}")
            return _Poll("error", time.monotonic())
        if not self._stage(pixels, kind, _video_fade_s(next_s)):
            return _Poll("busy", t, kind, next_s)   # ETag not taken: the next poll fetches it again
        self.etag = etag
        self._ok()
        return _Poll("new", t, kind, next_s)

    def _stage(self, pixels: np.ndarray, kind: str, fade_s: float = PERIOD_DEFAULT_S) -> bool:
        """Copy a decoded frame into a FREE buffer (else supersede the oldest READY one) and hand it over;
        ``fade_s`` = its linear fade if it is a video frame."""
        with self.lock:
            if self.closed:
                return False
            buf = next((b for b in self.bufs if b.state == FREE), None)
            if buf is None:
                ready = [b for b in self.bufs if b.state == READY]
                buf = min(ready, key=lambda b: b.version) if ready else None
            if buf is None:
                return False                        # both in flight (render has not reaped them yet)
            buf.state = FILLING
        np.copyto(buf.host, pixels)
        with self.lock:
            if self.closed:
                buf.state = FREE                    # (the poller hands every buffer back as it exits)
                return False
            self.version += 1
            buf.version, buf.kind, buf.fade_s = self.version, kind, fade_s
            buf.state = READY
        return True

    def schedule(self, res: _Poll, sched: float) -> float:
        """The next poll's deadline after ``res`` (``sched`` = the deadline that poll was made on)."""
        if res.status == "fallback":
            return res.t
        if res.status == "warming":
            return res.t + res.retry_s
        if res.status == "error":
            return res.t + ERROR_POLL_S
        if res.kind == "video" and res.next_s is not None:
            return res.t + min(VIDEO_MAX_S, max(VIDEO_MIN_S, res.next_s + VIDEO_LEAD_S))
        nxt, now = sched + POLL_S, time.monotonic()
        if nxt <= now:                              # fell behind: the next slot on the grid, never a burst
            nxt += POLL_S * math.floor((now - nxt) / POLL_S + 1.0)
        return nxt

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                sched = self.next_t
                if self.stop.wait(max(0.0, sched - time.monotonic())):
                    break
                try:
                    self.next_t = self.schedule(self.fetch(), sched)
                except Exception:   # never let the poller die (the scene would freeze): log and back off
                    log.exception("live_art %s: poll failed", self.url)
                    self.next_t = time.monotonic() + ERROR_POLL_S
        finally:
            if self.closed:     # only on close: render has stopped, and nothing fills a buffer after this
                self.give_back()

    def give_back(self) -> None:
        """The buffers' pinned blocks go back to the process-wide free list (the poller, as it exits on close)."""
        with self.lock:
            bufs, self.bufs = self.bufs, []
        _pinned_give_back(bufs)

    # -- render thread -------------------------------------------------------------------
    def reap(self) -> None:
        """Buffers whose upload has completed go back to the poller (an event query: never blocks)."""
        with self.lock:
            for b in self.bufs:
                if b.state == INFLIGHT and b.event.done:
                    b.state = FREE

    def take(self, seen: int) -> Optional[_HostBuf]:
        """The newest READY buffer newer than ``seen``, now INFLIGHT (older READY ones are dropped), or None."""
        with self.lock:
            if self.closed:
                return None
            best = None
            for b in self.bufs:
                if b.state == READY and b.version > seen and (best is None or b.version > best.version):
                    best = b
            if best is None:
                return None
            for b in self.bufs:
                if b.state == READY and b is not best:
                    b.state = FREE
            best.state = INFLIGHT
            return best

    def close(self) -> None:
        """Stop the poller: it wakes at once (or after the fetch it is in), hands the buffers back and exits;
        never joined.  Lock-free: it is also the finalizer, which the GC may run on a thread that holds ``lock``."""
        self.closed = True
        self.stop.set()

    def _fail(self, why: str, quiet: bool = False) -> None:
        if self._streak is None:
            (log.info if quiet else log.warning)("live_art %s: %s (keeping the last frame; retrying)",
                                                 self.url, why)
        self._streak = why

    def _ok(self) -> None:
        if self._streak is not None:
            log.info("live_art %s: back (%s cleared)", self.url, self._streak)
            self._streak = None


class LiveArtSource(Source):
    name = "live_art"
    label = "Live art"
    description = "The screen's current art, live from the art page, as a scene for effects to play on."
    params = {
        **standard_scene_params(palette=None, render_scale=1.0),
        "crossfade_s": {**num(0.0, 5.0, 1.0, "Crossfade on art change", step=0.1, unit="s", basic=True),
                        "x-ease": False},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.W, self.H = ctx.width, ctx.height
        self.cur = ctx.new_frame()       # the art being shown (the fade's end)
        self.prev = ctx.new_frame()      # the fade's start
        ctx.kernels.fill(self.cur, (0, 0, 0))
        ctx.kernels.fill(self.prev, (0, 0, 0))
        self.staging = cp.zeros((self.H, self.W, 3), cp.uint8)   # the H2D target, expanded into cur
        mod = raw_module(_SRC)
        self.k = mod.get_function("live_art_mix")
        self.k_arrive = mod.get_function("live_art_arrive")
        self.k_copy = mod.get_function("live_art_copy")
        nbytes = self.H * self.W * 4
        self._n16, self._tail = nbytes // 16, nbytes % 16
        self._copy_grid = (max(1, (self._n16 + 255) // 256), 1, 1)
        self._iW, self._iH = np.int32(self.W), np.int32(self.H)
        self.fade = 1.0                  # 1 = no fade running
        self.fade_linear = False         # this fade: linear over fade_len (video) or smoothstep over crossfade_s
        self.fade_len = PERIOD_DEFAULT_S
        self._kf = 1.0                   # a new frame's frozen fade shape (render passes it to live_art_arrive)
        self.seen = 0
        self.feed: Optional[_ArtFeed] = None
        self.thread: Optional[threading.Thread] = None
        screen = getattr(ctx, "screen", None)
        if not (isinstance(screen, str) and screen):
            return                       # fxtest / preview: black, no polling
        n = self.H * self.W * 3
        bufs: list[_HostBuf] = []
        try:
            for _ in range(N_BUFFERS):   # pinned (process-wide blocks), so render's upload is a true async copy
                ev = cp.cuda.Event(block=False, disable_timing=True)
                mem = _pinned_take(cp, n)
                bufs.append(_HostBuf(np.frombuffer(mem, np.uint8, n).reshape(self.H, self.W, 3), ev, mem))
            base = f"{room_url()}/api/v1/screens/{urllib.parse.quote(screen, safe='')}"
            self.feed = feed = _ArtFeed(f"{base}/art", f"{base}/art.png", self.W, self.H, bufs)
            res = feed.fetch()
            if res.status == "fallback":     # an older room: /art.png straight away
                res = feed.fetch()
            buf = feed.take(0)
            if buf is not None:              # first frame is already the art (no fade)
                self._upload(buf)
            feed.next_t = feed.schedule(res, time.monotonic())
            self.thread = threading.Thread(target=feed.run, name=f"live-art-{screen}", daemon=True)
            weakref.finalize(self, feed.close)   # never leak a poller, even if close() is missed
            self.thread.start()                  # from here the poller hands the blocks back as it exits
        except BaseException:
            if self.feed is not None:
                self.feed.close()
            self.feed = self.thread = None
            _pinned_give_back(bufs)
            raise

    def _copy_in(self, buf: _HostBuf) -> None:
        """Pinned buffer -> staging (RGB), stream-ordered on the current stream; the event releases ``buf``
        (reap).  Two CUDA calls."""
        cp = self.ctx.cp
        stream = cp.cuda.get_current_stream()
        cp.cuda.runtime.memcpyAsync(self.staging.data.ptr, buf.ptr, buf.host.nbytes,
                                    cp.cuda.runtime.memcpyHostToDevice, stream.ptr)
        buf.event.record(stream)
        self.seen = buf.version

    def _upload(self, buf: _HostBuf) -> None:
        """setup's first frame: straight into cur (no fade)."""
        self._copy_in(buf)
        self.ctx.kernels.rgb_to_rgba(self.staging, self.cur)

    def _k(self) -> float:
        f = self.fade
        return f if self.fade_linear else f * f * (3.0 - 2.0 * f)

    def _next(self, buf: _HostBuf) -> int:
        """A new art frame: the current picture becomes the fade's start, the new one (in staging; render's
        ``live_art_arrive`` expands it into cur) its end.  Returns 1 if the running fade must be frozen into prev
        (mid-fade), with its shape in ``self._kf``; else the buffers are swapped and 0."""
        freeze = 0
        if self.fade < 1.0:              # mid-fade: freeze what is on screen in place (the running fade's shape)
            freeze, self._kf = 1, self._k()
        else:
            self.prev, self.cur = self.cur, self.prev
            self._kf = 1.0
        self._copy_in(buf)
        self.fade_linear = buf.kind == "video"
        self.fade_len = buf.fade_s
        self.fade = 0.0
        return freeze

    def _copy_out(self, out) -> None:
        """out = cur: one kernel (cheaper on the host than a D2D memcpy); cp.copyto for an odd ``out``."""
        if out.flags.c_contiguous and out.nbytes == self._n16 * 16 + self._tail and out.data.ptr % 16 == 0:
            self.k_copy(self._copy_grid, (256, 1, 1), (self.cur, out, np.int64(self._n16), self.cur, out,
                                                       np.int32(self._tail)))
        else:
            self.ctx.cp.copyto(out, self.cur)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        feed = self.feed
        freeze = -1                      # -1: no new frame this tick
        if feed is not None:
            feed.reap()
            buf = feed.take(self.seen)
            if buf is not None:
                freeze = self._next(buf)
        if self.fade < 1.0:
            length = self.fade_len if self.fade_linear else float(p["crossfade_s"])
            self.fade = 1.0 if length <= 1e-3 else min(1.0, self.fade + max(dt, 0.0) / length)
        gain = max(0.0, float(p["intensity"]) * (1.0 + PULSE_KICK * float(p["pulse"])))
        if freeze >= 0:                  # new art: freeze + expand + mix in one launch
            launch_2d(self.k_arrive, self.W, self.H,
                      (self.prev, self.cur, self.staging, out, self._iW, self._iH, np.int32(freeze),
                       np.float32(self._kf), np.float32(self._k()), np.float32(gain)))
        elif self.fade >= 1.0 and abs(gain - 1.0) < 1e-4:
            self._copy_out(out)
        else:
            launch_2d(self.k, self.W, self.H, (self.prev, self.cur, out, self._iW, self._iH,
                                               np.float32(self._k()), np.float32(gain)))

    def close(self) -> None:
        if self.feed is not None:
            self.feed.close()            # the daemon thread exits at its next wake-up (no join: never block)
        self.feed = self.thread = None
        self.cur = self.prev = self.staging = None
        self.k = self.k_arrive = self.k_copy = None
