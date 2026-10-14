"""Live art: the screen's current art as a scene, so the effects director can put effects on the art itself.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

room_service keeps a persistent headless Chrome on each screen's art page and serves a 4K PNG snapshot of it at
``GET {SM_ROOM_URL}/api/v1/screens/{screen}/art.png`` (ETag; 304 on a matching If-None-Match; 404 for an unknown
screen or no art yet; 503 while the snapshotter is busy or off).  SM_ROOM_URL defaults to the loopback door
(room_service runs inside the Flask app on this machine).

* A daemon thread (started in setup, stopped in close) polls every 1 s (every 5 s during an error streak, which is
  logged once), decodes a new PNG off the render thread (``image.load_png``: letterboxed at the profile size) and
  stages it.  On an error the last frame stays up.
* setup tries one synchronous fetch so the first frame is already the art (room_service has normally just
  snapshotted the same art for the cut, so it is cached); otherwise the scene starts black and the poller fills it.
* render uploads a staged frame once (``staging.set``, no per-frame allocation), expands it to RGBA and crossfades
  to it over ``crossfade_s`` (smoothstep).  A change arriving mid-fade freezes what is on screen as the new start,
  so nothing jumps.  Otherwise a device-to-device copy.
* The session puts its screen id on the context (``ctx.screen``); without one (fxtest, previews) the scene is
  black and nothing is polled.

Macros: intensity = gain; pulse = a small brightness kick (+15 % at 1).  energy, speed, render_scale and seed do
nothing here: the art is a still at full resolution and the effects bring the motion.
"""
from __future__ import annotations

import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import weakref
from typing import Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins.base import Source
from stream_service.plugins.sources.image import load_png

log = logging.getLogger("stream_service.plugins.live_art")

DEFAULT_ROOM = "http://127.0.0.1:8700"
POLL_S = 1.0
ERROR_POLL_S = 5.0
TIMEOUT_S = 4.0
MAX_BYTES = 64 * 1024 * 1024
PULSE_KICK = 0.15

# out = (a + (b - a) * k) * gain.  No __restrict__: `out` may alias `a` (freezing a fade in place).
_SRC = HELPERS + r"""
extern "C" __global__
void live_art_mix(const uchar4* a, const uchar4* b, uchar4* out, const int W, const int H,
                  const float k, const float gain)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const uchar4 pa = a[i], pb = b[i];
    const float q = 1.f - k;
    out[i] = pack(make_float4((q * pa.x + k * pb.x) * gain, (q * pa.y + k * pb.y) * gain,
                              (q * pa.z + k * pb.z) * gain, 255.f));
}
"""


def room_url() -> str:
    return (os.environ.get("SM_ROOM_URL") or DEFAULT_ROOM).rstrip("/")


class _ArtFeed:
    """Polls one screen's art.png and stages the newest decoded frame (thread-safe hand-over by version)."""

    def __init__(self, url: str, width: int, height: int) -> None:
        self.url, self.w, self.h = url, width, height
        self.etag: Optional[str] = None
        self.lock = threading.Lock()
        self.pixels: Optional[np.ndarray] = None   # newest staged (H, W, 3) uint8, None once taken
        self.version = 0
        self.stop = threading.Event()
        self._streak: Optional[str] = None          # the current error streak (logged once)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # loopback: never a proxy

    def fetch(self) -> str:
        """One GET.  -> "new" (staged), "same" (304) or "error".  Never raises."""
        headers = {"Accept": "image/png"}
        if self.etag:
            headers["If-None-Match"] = self.etag
        try:
            with self._opener.open(urllib.request.Request(self.url, headers=headers), timeout=TIMEOUT_S) as r:
                data = r.read(MAX_BYTES + 1)
                etag = r.headers.get("ETag")
            if len(data) > MAX_BYTES:
                raise ValueError(f"art.png is over {MAX_BYTES} bytes")
            pixels = load_png(data, self.w, self.h)
        except urllib.error.HTTPError as e:
            code = e.code
            e.close()
            if code == 304:
                self._ok()
                return "same"
            self._fail(f"HTTP {code}", quiet=code in (404, 503))
            return "error"
        except Exception as e:  # URLError, timeouts, a bad PNG (ParamError), ...: keep the last frame
            self._fail(f"{type(e).__name__}: {e}")
            return "error"
        with self.lock:
            self.pixels = pixels
            self.version += 1
        self.etag = etag
        self._ok()
        return "new"

    def take(self, seen: int) -> tuple[Optional[np.ndarray], int]:
        """The staged frame if it is newer than ``seen`` (else None) and the current version."""
        with self.lock:
            if self.version == seen or self.pixels is None:
                return None, self.version
            px, self.pixels = self.pixels, None
            return px, self.version

    def run(self) -> None:
        delay = POLL_S
        while not self.stop.wait(delay):
            delay = ERROR_POLL_S if self.fetch() == "error" else POLL_S

    def _fail(self, why: str, quiet: bool = False) -> None:
        if self._streak is None:
            (log.info if quiet else log.warning)("live_art %s: %s (keeping the last frame; retrying every %.0f s)",
                                                 self.url, why, ERROR_POLL_S)
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
        self.staging = cp.zeros((self.H, self.W, 3), cp.uint8)
        self.k = raw_module(_SRC).get_function("live_art_mix")
        self.fade = 1.0                  # 1 = no fade running
        self.seen = 0
        self.feed: Optional[_ArtFeed] = None
        self.thread: Optional[threading.Thread] = None
        screen = getattr(ctx, "screen", None)
        if not (isinstance(screen, str) and screen):
            return                       # fxtest / preview: black, no polling
        url = f"{room_url()}/api/v1/screens/{urllib.parse.quote(screen, safe='')}/art.png"
        self.feed = _ArtFeed(url, self.W, self.H)
        if self.feed.fetch() == "new":   # first frame is already the art
            px, self.seen = self.feed.take(0)
            if px is not None:
                self.staging.set(px)
                ctx.kernels.rgb_to_rgba(self.staging, self.cur)
        self.thread = threading.Thread(target=self.feed.run, name=f"live-art-{screen}", daemon=True)
        self.thread.start()
        weakref.finalize(self, self.feed.stop.set)   # never leak a poller, even if close() is missed

    def _next(self, px: np.ndarray) -> None:
        """A new art frame: the current picture becomes the fade's start, the new one its end."""
        if self.fade < 1.0:              # mid-fade: freeze what is on screen in place
            f = self.fade
            launch_2d(self.k, self.W, self.H, (self.prev, self.cur, self.prev, np.int32(self.W), np.int32(self.H),
                                               np.float32(f * f * (3.0 - 2.0 * f)), np.float32(1.0)))
        else:
            self.prev, self.cur = self.cur, self.prev
        self.staging.set(px)
        self.ctx.kernels.rgb_to_rgba(self.staging, self.cur)
        self.fade = 0.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        if self.feed is not None:
            px, ver = self.feed.take(self.seen)
            if px is not None:
                self.seen = ver
                self._next(px)
        if self.fade < 1.0:
            xf = float(p["crossfade_s"])
            self.fade = 1.0 if xf <= 1e-3 else min(1.0, self.fade + max(dt, 0.0) / xf)
        gain = max(0.0, float(p["intensity"]) * (1.0 + PULSE_KICK * float(p["pulse"])))
        if self.fade >= 1.0 and abs(gain - 1.0) < 1e-4:
            self.ctx.cp.copyto(out, self.cur)
            return
        f = self.fade
        launch_2d(self.k, self.W, self.H, (self.prev, self.cur, out, np.int32(self.W), np.int32(self.H),
                                           np.float32(f * f * (3.0 - 2.0 * f)), np.float32(gain)))

    def close(self) -> None:
        if self.feed is not None:
            self.feed.stop.set()         # the daemon thread exits at its next wake-up (no join: never block)
        self.feed = self.thread = None
        self.cur = self.prev = self.staging = None
        self.k = None
