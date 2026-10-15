"""live_art's feed and image.load_image (no GPU), plus a tiny opt-in GPU check of the upload and the fades.

The poller runs against a throwaway local "room" that serves /api/v1/screens/s1/art (and /art.png) with scripted
behaviour: JPEG video frames on a 1 s cadence (X-Art-Kind / X-Art-Next-S), a still with an ETag, aiohttp's text 404
for a missing route, a JSON 404, 503 + Retry-After.

  SM_GPU_TESTS=1 [SM_TEST_GPU=1] pytest stream_service/tests/test_live_art.py
"""
from __future__ import annotations

import io
import json
import logging
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from stream_service.plugins.base import ParamError
from stream_service.plugins.sources import live_art as la
from stream_service.plugins.sources.image import load_image, load_png

from .conftest import GPU

W, H = 64, 36


def _encode(arr: np.ndarray, fmt: str, **kw) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, fmt, **kw)
    return b.getvalue()


def _solid(w: int, h: int, rgb, mode: str = "RGB") -> np.ndarray:
    a = np.zeros((h, w, len(mode)), np.uint8)
    a[...] = rgb
    return a


# ---------------------------------------------------------------------------------------------------------------
# image.load_image
# ---------------------------------------------------------------------------------------------------------------

def test_load_image_png_and_jpeg_at_size():
    rng = np.random.default_rng(1)
    px = rng.integers(0, 256, (H, W, 3), dtype=np.uint8)
    assert np.array_equal(load_image(_encode(px, "PNG"), W, H), px)
    flat = _solid(W, H, (30, 140, 220))
    out = load_image(_encode(flat, "JPEG", quality=95), W, H)
    assert out.shape == (H, W, 3) and out.dtype == np.uint8 and out.flags.c_contiguous
    assert np.abs(out.astype(int) - flat.astype(int)).max() <= 4


def test_load_image_letterboxes_like_load_png():
    sq = _solid(100, 100, (250, 250, 250))
    for data in (_encode(sq, "PNG"), _encode(sq, "JPEG", quality=95)):
        out = load_image(data, W, H)          # 100x100 -> 36x36, centred: x 14..49 lit, the rest black
        assert out.shape == (H, W, 3)
        assert out[:, :12].max() == 0 and out[:, 52:].max() == 0
        assert out[:, 16:48].min() >= 240
    png = _encode(sq, "PNG")
    assert np.array_equal(load_image(png, W, H), load_png(png, W, H))


def test_load_image_alpha_on_black_and_rejects_other_formats():
    rgba = _solid(W, H, (255, 0, 0, 0), "RGBA")   # fully transparent red
    rgba[:, : W // 2, 3] = 255                   # left half opaque
    out = load_image(_encode(rgba, "PNG"), W, H)
    assert out[:, : W // 2, 0].min() == 255 and out[:, W // 2:].max() == 0
    for data in (_encode(_solid(8, 8, (1, 2, 3)), "GIF"), b"not an image", b""):
        with pytest.raises(ParamError) as e:
            load_image(data, W, H)
        assert e.value.code == "bad_image"
    with pytest.raises(ParamError):              # anchors stay PNG-only
        load_png(_encode(_solid(8, 8, (1, 2, 3)), "JPEG"), W, H)


def test_load_image_applies_jpeg_exif_orientation():
    src = np.zeros((20, 40, 3), np.uint8)        # 40 wide x 20 high: top half red, bottom half blue
    src[:10] = (255, 0, 0)
    src[10:] = (0, 0, 255)
    exif = Image.Exif()
    exif[0x0112] = 6                             # "rotate 90 degrees clockwise to display"
    out = load_image(_encode(src, "JPEG", quality=95, exif=exif.tobytes()), 20, 40)
    # displayed upright it is 20 wide x 40 high, with the original top (red) on the right
    assert out.shape == (40, 20, 3)
    assert out[20, 16, 0] > 200 and out[20, 16, 2] < 60
    assert out[20, 3, 2] > 200 and out[20, 3, 0] < 60


# ---------------------------------------------------------------------------------------------------------------
# A throwaway room
# ---------------------------------------------------------------------------------------------------------------

class _Room:
    """/api/v1/screens/s1/art and /art.png.  mode: video (sample k = whole periods since t0; frame id = ids(k),
    default k, so ids can repeat a frame like a clip's hold) | still | no_art (JSON 404).  art_route=False: /art is
    aiohttp's text 404 (an older room).  warming: that many 503s first."""

    def __init__(self, mode: str = "video", period: float = 1.0, art_route: bool = True, warming: int = 0,
                 retry_after: str = "1", size=(W, H), frame=None, ids=None):
        self.ids = ids or (lambda k: k)
        self.mode, self.period, self.art_route = mode, period, art_route
        self.warming, self.retry_after, self.size = warming, retry_after, size
        self.frame = frame or (lambda fid: _solid(size[0], size[1], ((fid * 70) % 256, 40, 90)))
        self.log: list[tuple[float, str, int]] = []   # (monotonic, path, status)
        self.t0 = time.monotonic()
        self._jpeg: dict[int, bytes] = {}
        room = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                room.handle(self)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def _send(self, h, status: int, body: bytes = b"", ctype: str = "", headers: dict | None = None):
        self.log.append((time.monotonic(), h.path, status))
        h.send_response(status)
        if ctype:
            h.send_header("Content-Type", ctype)
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        if body:
            h.wfile.write(body)

    def handle(self, h):
        path = h.path
        if path == "/api/v1/screens/s1/art" and not self.art_route or not path.startswith("/api/v1/screens/s1/art"):
            return self._send(h, 404, b"404: Not Found", "text/plain; charset=utf-8")
        if path not in ("/api/v1/screens/s1/art", "/api/v1/screens/s1/art.png"):
            return self._send(h, 404, b"404: Not Found", "text/plain; charset=utf-8")
        if self.warming > 0:
            self.warming -= 1
            return self._send(h, 503, json.dumps({"error": "warming"}).encode(), "application/json",
                              {"Retry-After": self.retry_after})
        if self.mode == "no_art":
            return self._send(h, 404, json.dumps({"error": "no_art"}).encode(), "application/json")
        now = time.monotonic()
        if self.mode == "video":
            k = int((now - self.t0) / self.period)
            fid = self.ids(k)
            hdrs = {"X-Art-Kind": "video", "X-Art-Next-S": f"{self.t0 + (k + 1) * self.period - now:.4f}"}
        else:
            fid, hdrs = 0, {"X-Art-Kind": "still"}
        hdrs.update({"ETag": f'"f{fid}"', "Cache-Control": "no-store"})
        if h.headers.get("If-None-Match") == hdrs["ETag"]:
            return self._send(h, 304, headers=hdrs)
        if fid not in self._jpeg:
            self._jpeg[fid] = _encode(self.frame(fid), "JPEG", quality=92)
        self._send(h, 200, self._jpeg[fid], "image/jpeg", hdrs)

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def room():
    rooms: list[_Room] = []

    def make(**kw) -> _Room:
        r = _Room(**kw)
        rooms.append(r)
        return r

    yield make
    for r in rooms:
        r.close()


@pytest.fixture(autouse=True)
def _fresh_pinned_pool(monkeypatch):
    """Each test gets its own process-wide pinned free list."""
    monkeypatch.setattr(la, "_PINNED_FREE", {})
    monkeypatch.setattr(la, "_PINNED_PENDING", deque())


class _Ev:
    """A CUDA event stand-in for host-only tests (``done`` is all the feed reads)."""

    def __init__(self, done: bool = True):
        self.done = done


def _fake_cp():
    """A ``cp`` stand-in for the pinned pool: each cudaHostAlloc is logged (nbytes, flags) and is a bytearray."""
    allocs: list = []

    def pinned(n, flags=0):
        allocs.append((n, flags))
        return bytearray(n)

    cuda = SimpleNamespace(PinnedMemory=pinned, PinnedMemoryPointer=lambda mem, off: mem,
                           runtime=SimpleNamespace(hostAllocPortable=1))
    return SimpleNamespace(cuda=cuda), allocs


def _pinned_bufs(cp, n_bufs: int = la.N_BUFFERS, w: int = W, h: int = H) -> list:
    n = w * h * 3
    return [la._HostBuf(np.frombuffer(m, np.uint8, n).reshape(h, w, 3), _Ev(), m)
            for m in (la._pinned_take(cp, n) for _ in range(n_bufs))]


def _feed(base: str, w: int = W, h: int = H) -> la._ArtFeed:
    bufs = [la._HostBuf(np.zeros((h, w, 3), np.uint8), _Ev()) for _ in range(la.N_BUFFERS)]
    return la._ArtFeed(f"{base}/api/v1/screens/s1/art", f"{base}/api/v1/screens/s1/art.png", w, h, bufs)


def _consume(feed: la._ArtFeed, stop: threading.Event, got: list):
    """Render-thread stand-in: reap + take every 5 ms, recording (monotonic, version, kind, fade_s)."""
    seen = 0
    while not stop.wait(0.005):
        feed.reap()
        b = feed.take(seen)
        if b is not None:
            seen = b.version
            got.append((time.monotonic(), b.version, b.kind, b.fade_s))


# ---------------------------------------------------------------------------------------------------------------
# The feed (no GPU)
# ---------------------------------------------------------------------------------------------------------------

def test_feed_video_polls_on_the_frame_cadence(room):
    r = room(mode="video", period=1.0)
    feed = _feed(r.base)
    feed.next_t = time.monotonic()
    got: list = []
    stop = threading.Event()
    threads = [threading.Thread(target=feed.run, daemon=True),
               threading.Thread(target=_consume, args=(feed, stop, got), daemon=True)]
    for t in threads:
        t.start()
    time.sleep(4.4)
    stop.set()
    feed.close()
    for t in threads:
        t.join(6)
    assert not any(t.is_alive() for t in threads)          # the poller stops on close
    assert len(got) >= 4, got
    arrivals = [g[0] for g in got]
    iv = np.diff(arrivals[1:])                             # (the first is just "now")
    assert np.all(np.abs(iv - 1.0) < 0.15), iv
    assert all(g[2] == "video" for g in got)
    # each video frame fades until the next one arrives (X-Art-Next-S): the blend ends as the next one begins
    for (ta, _, _, fade), (tb, _, _, _) in zip(got[1:], got[2:]):
        assert abs(fade - (tb - ta)) < 0.08, (fade, tb - ta)
    # fixed deadlines: each frame is fetched just after the room's frame boundary, not "1 s after the last fetch"
    oks = [t for t, _, s in r.log if s == 200]
    lag = [((t - r.t0) % 1.0) for t in oks[1:]]
    assert all(x < 0.2 for x in lag), lag
    assert len(r.log) <= 2 * len(oks) + 2, r.log           # no hammering: about one request per frame


def _run_feed(feed: la._ArtFeed, seconds: float) -> list:
    got: list = []
    stop = threading.Event()
    threads = [threading.Thread(target=feed.run, daemon=True),
               threading.Thread(target=_consume, args=(feed, stop, got), daemon=True)]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    feed.close()
    for t in threads:
        t.join(6)
    assert not any(t.is_alive() for t in threads)
    return got


def test_feed_video_fade_after_a_mid_frame_fetch_is_not_shortened(room):
    """setup's fetch lands mid-frame (phase 0.85): that frame fades over what is left of it, and every later fade
    lasts the full frame (a measured average would dip to ~0.75 s, then hold the picture ~0.2 s per frame)."""
    r = room(mode="video", period=1.0)
    r.t0 = time.monotonic() - 0.85
    feed = _feed(r.base)
    res = feed.fetch()                                     # what setup does: one fetch, then schedule, then run
    first = feed.take(0)
    assert res.status == "new" and 0.0 < res.next_s < 0.2, res    # (less if this machine is busy)
    assert first.fade_s == pytest.approx(la.PERIOD_MIN_S)  # 0.15 s left (+ lead + slack): the 0.3 s floor
    feed.next_t = feed.schedule(res, time.monotonic())
    got = _run_feed(feed, 4.4)
    assert len(got) >= 4, got
    fades = [g[3] for g in got]
    assert all(abs(f - 1.0) < 0.06 for f in fades), fades
    iv = np.diff([g[0] for g in got])
    assert np.all(np.abs(iv - 1.0) < 0.15), iv


def test_feed_video_fades_ignore_runs_of_identical_frames(room):
    """A clip's hold (one frame for several samples: 304s) does not stretch the fades that follow it."""
    seq = [0, 1, 2, 2, 2, 2, 3, 4, 5, 6, 7, 8]
    r = room(mode="video", period=0.4, ids=lambda k: seq[min(k, len(seq) - 1)])
    feed = _feed(r.base)
    feed.next_t = time.monotonic()
    got = _run_feed(feed, 4.3)
    assert sum(1 for _, _, s in r.log if s == 304) >= 3, r.log   # the hold was polled through (304s)
    assert len(got) >= 7, got
    fades = [g[3] for g in got]
    assert all(abs(f - 0.4) < 0.06 for f in fades), fades     # 0.4 s throughout, before and after the hold


def test_feed_still_etag_304_and_a_fixed_grid(room, monkeypatch):
    r = room(mode="still")
    feed = _feed(r.base)
    res = feed.fetch()
    assert res.status == "new" and res.kind == "still" and feed.etag == '"f0"' and feed.version == 1
    res = feed.fetch()
    assert res.status == "same" and feed.version == 1 and r.log[-1][2] == 304
    now = time.monotonic()
    assert feed.schedule(res, now) == pytest.approx(now + 1.0)
    late = feed.schedule(res, now - 2.5)                   # fell behind: the next grid slot, not a burst
    assert late == pytest.approx(now + 0.5, abs=0.02)
    # the poller keeps a monotonic grid (measured with a faster grid for speed)
    monkeypatch.setattr(la, "POLL_S", 0.2)
    n0 = len(r.log)
    feed.next_t = time.monotonic()
    t = threading.Thread(target=feed.run, daemon=True)
    t.start()
    time.sleep(1.1)
    feed.close()
    t.join(5)
    times = [x[0] for x in r.log[n0:]]
    assert 5 <= len(times) <= 7 and all(s == 304 for _, _, s in r.log[n0:])
    assert np.all(np.abs(np.diff(times) - 0.2) < 0.06), np.diff(times)


def test_feed_falls_back_to_art_png_on_a_text_404(room):
    r = room(mode="still", art_route=False)
    feed = _feed(r.base)
    res = feed.fetch()
    assert res.status == "fallback" and feed.url.endswith("/art.png")
    assert feed.schedule(res, res.t) == res.t               # straight away
    assert feed.fetch().status == "new"
    assert [p for _, p, _ in r.log] == ["/api/v1/screens/s1/art", "/api/v1/screens/s1/art.png"]
    # a JSON 404 (no art yet) is an answer from a current room: no fallback, a 5 s back-off
    r2 = room(mode="no_art")
    feed2 = _feed(r2.base)
    res = feed2.fetch()
    assert res.status == "error" and feed2.url.endswith("/art")
    assert feed2.schedule(res, res.t) == pytest.approx(res.t + 5.0)


def test_feed_honours_retry_after_on_503(room):
    r = room(mode="still", warming=1, retry_after="1")
    feed = _feed(r.base)
    feed.next_t = time.monotonic()
    t = threading.Thread(target=feed.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and not any(s == 200 for _, _, s in r.log):
        time.sleep(0.02)
    feed.close()
    t.join(5)
    (t503, _, s1), (t200, _, s2) = r.log[:2]
    assert (s1, s2) == (503, 200) and 0.9 < t200 - t503 < 1.25
    assert feed.version == 1
    r2 = room(mode="still", warming=1, retry_after="7")    # capped at 2 s
    feed2 = _feed(r2.base)
    res = feed2.fetch()
    assert res.status == "warming" and res.retry_s == 2.0
    assert feed2.schedule(res, res.t) == pytest.approx(res.t + 2.0)


def test_feed_error_backoff_logs_once_per_streak(caplog):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                             # nothing listens there now
    feed = _feed(f"http://127.0.0.1:{port}")
    with caplog.at_level(logging.INFO, logger="stream_service.plugins.live_art"):
        a, b = feed.fetch(), feed.fetch()
    assert a.status == b.status == "error"
    assert feed.schedule(b, b.t) == pytest.approx(b.t + 5.0)
    assert len([rec for rec in caplog.records if "retrying" in rec.getMessage()]) == 1


def test_handover_never_reuses_a_buffer_in_flight():
    feed = _feed("http://127.0.0.1:9")
    for b in feed.bufs:
        b.event = _Ev(done=False)
    px = [_solid(W, H, (v, v, v)) for v in (10, 20, 30, 40, 50)]
    assert feed._stage(px[0], "still")
    a = feed.take(0)
    assert a.version == 1 and a.state == la.INFLIGHT
    assert feed._stage(px[1], "video", 0.9)
    assert feed._stage(px[2], "video", 0.8)               # supersedes the READY one, never the one in flight
    assert a.host[0, 0, 0] == 10
    b = feed.take(1)
    assert b is not a and b.version == 3 and b.host[0, 0, 0] == 30 and b.kind == "video" and b.fade_s == 0.8
    assert feed.take(3) is None
    assert not feed._stage(px[3], "still")                 # both in flight: nothing is overwritten
    assert a.host[0, 0, 0] == 10 and b.host[0, 0, 0] == 30
    feed.reap()
    assert a.state == b.state == la.INFLIGHT               # events not done: still held
    a.event.done = True
    feed.reap()
    assert a.state == la.FREE and b.state == la.INFLIGHT
    assert feed._stage(px[3], "still") and a.version == 4 and a.host[0, 0, 0] == 40
    feed.close()
    assert not feed._stage(px[4], "still") and feed.take(0) is None


def test_video_fade_is_the_time_to_the_next_frame():
    assert la._video_fade_s(None) == la.PERIOD_DEFAULT_S                 # no X-Art-Next-S: the 1 s grid
    assert la._video_fade_s(0.97) == pytest.approx(0.97 + la.VIDEO_LEAD_S + la.FADE_SLACK_S)
    assert la._video_fade_s(0.0) == la.PERIOD_MIN_S and la._video_fade_s(9.0) == la.PERIOD_MAX_S


def test_pinned_blocks_are_pooled_never_freed_and_wait_for_their_upload():
    cp, allocs = _fake_cp()
    n = W * H * 3
    bufs = _pinned_bufs(cp)
    assert allocs == [(n, 1), (n, 1)]                      # portable cudaHostAlloc, outside CuPy's default pool
    a, b = (x.mem for x in bufs)
    bufs[0].state, bufs[0].event = la.INFLIGHT, _Ev(done=False)   # its last upload may still be running
    la._pinned_give_back(bufs)
    la._pinned_give_back(bufs)                             # (twice: a no-op)
    assert all(x.mem is None for x in bufs) and len(la._PINNED_PENDING) == 1
    assert la._pinned_take(cp, n) is b and len(allocs) == 2          # the free one: reused, nothing allocated
    assert la._pinned_take(cp, n) is not a and len(allocs) == 3      # the pending one waits for its event
    bufs[0].event.done = True
    assert la._pinned_take(cp, n) is a and len(allocs) == 3 and not la._PINNED_PENDING
    la.reserve_pinned(cp, W, H, 2)                         # ahead of time (e.g. when a GPU is warmed)
    assert len(allocs) == 5
    assert la._pinned_take(cp, n) is not None and la._pinned_take(cp, n) is not None and len(allocs) == 5


def test_poller_hands_its_blocks_back_when_closed(room):
    r = room(mode="still")
    cp, allocs = _fake_cp()
    n = W * H * 3
    feed = la._ArtFeed(f"{r.base}/api/v1/screens/s1/art", f"{r.base}/api/v1/screens/s1/art.png", W, H,
                       _pinned_bufs(cp))
    feed.next_t = time.monotonic()
    t = threading.Thread(target=feed.run, daemon=True)
    t.start()
    deadline = time.monotonic() + 3
    while feed.version < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    inflight = feed.take(0)
    assert inflight is not None and inflight.host[0, 0, 1] > 20      # the art (green 40) was decoded into it
    inflight.event.done = False                            # its upload is still running
    feed.close()
    t.join(3)
    assert not t.is_alive() and feed.bufs == []
    assert len(la._PINNED_FREE[n]) == 1 and len(la._PINNED_PENDING) == 1
    inflight.event.done = True
    assert la._pinned_take(cp, n) is not None and la._pinned_take(cp, n) is not None and len(allocs) == 2


# ---------------------------------------------------------------------------------------------------------------
# GPU (opt-in): pinned async upload + linear / smoothstep fades
# ---------------------------------------------------------------------------------------------------------------

GW, GH, GFPS = 320, 180, 30


def _gpu_source(room_base: str, monkeypatch):
    pytest.importorskip("cupy")
    from stream_service.gpu import RenderContext

    monkeypatch.setenv("SM_ROOM_URL", room_base)
    ctx = RenderContext(GPU, GW, GH, GFPS, prealloc_buffers=0)
    ctx.screen = "s1"
    src = la.LiveArtSource()
    with ctx.device:
        st = ctx.cp.cuda.Stream(non_blocking=True)        # like engine.prepare: setup on its own stream
        with st:
            src.setup(ctx, la.LiveArtSource.validate({}))
        st.synchronize()
        out = ctx.new_frame()
    return ctx, src, out


def _render(ctx, src, out, n: int, dt: float = 1.0 / GFPS) -> list[float]:
    means = []
    with ctx.device, ctx.stream:
        for _ in range(n):
            src.render(0.0, dt, out)
            means.append(float(ctx.cp.asnumpy(out[..., 0]).mean()))   # (test only: a host read-back)
    return means


@pytest.mark.gpu
def test_gpu_linear_video_fade_smoothstep_still_and_pinned_upload(room, monkeypatch):
    r = room(mode="no_art", size=(GW, GH))                # setup's fetch finds nothing: starts black
    monkeypatch.setattr(la, "ERROR_POLL_S", 60.0)          # ... and the poller sleeps: drive the hand-over by hand
    allocs: list = []
    real_new = la._pinned_new
    monkeypatch.setattr(la, "_pinned_new", lambda cp, n: allocs.append(n) or real_new(cp, n))
    ctx, src, out = _gpu_source(r.base, monkeypatch)
    feed, thread = src.feed, src.thread
    try:
        assert all(b.mem is not None for b in feed.bufs) and len(allocs) == la.N_BUFFERS   # pinned host blocks
        assert _render(ctx, src, out, 2) == [0.0, 0.0]
        white, black = _solid(GW, GH, (255, 255, 255)), _solid(GW, GH, (0, 0, 0))
        assert feed._stage(white, "video", 1.0)            # a video frame: linear over its fade (1 s)
        ramp = _render(ctx, src, out, GFPS + 2)
        steps = np.diff([0.0] + ramp[:GFPS])
        assert np.all(np.abs(steps - 255.0 / GFPS) < 1.0), steps     # linear: equal steps, intermediate frames
        assert ramp[-1] == 255.0
        assert all(b.state == la.FREE for b in feed.bufs)  # its event completed: the buffer went back
        # a frame arriving mid-fade starts from what is on screen: no jump
        assert feed._stage(black, "video", 1.0)
        down = _render(ctx, src, out, GFPS // 2)
        assert feed._stage(white, "video", 1.0)
        back = _render(ctx, src, out, GFPS + 2)
        seq = [ramp[-1]] + down + back
        assert np.max(np.abs(np.diff(seq))) < 255.0 / (la.PERIOD_MIN_S * GFPS) + 1.0, seq
        assert min(down) > 100 and back[-1] == 255.0
        # a still: smoothstep over crossfade_s (1 s): slow at the ends, fastest in the middle
        assert feed._stage(black, "still")
        fade = _render(ctx, src, out, GFPS + 2)
        d = -np.diff([255.0] + fade[:GFPS])
        assert d[GFPS // 2] > 1.3 * 255.0 / GFPS and d[0] < 0.5 * 255.0 / GFPS and fade[-1] == 0.0
        blocks = {id(b.mem) for b in feed.bufs}
    finally:
        src.close()
    thread.join(3)                                         # close wakes the poller: it hands its blocks back
    assert not thread.is_alive() and src.feed is None and feed.closed and feed.bufs == []
    n = GW * GH * 3
    assert {id(m) for m in la._PINNED_FREE[n]} == blocks and not la._PINNED_PENDING
    # the next live_art reuses them: no cudaHostAlloc while a show renders
    ctx.cp.get_default_pinned_memory_pool().free_all_blocks()   # (what the renderer does when a session ends)
    src2 = la.LiveArtSource()
    with ctx.device:
        st = ctx.cp.cuda.Stream(non_blocking=True)
        with st:
            src2.setup(ctx, la.LiveArtSource.validate({}))
        st.synchronize()
    try:
        assert {id(b.mem) for b in src2.feed.bufs} == blocks and len(allocs) == la.N_BUFFERS
        assert _render(ctx, src2, out, 1) == [0.0]
    finally:
        thread2 = src2.thread
        src2.close()
    thread2.join(3)
    assert not thread2.is_alive() and len(la._PINNED_FREE[n]) == la.N_BUFFERS


@pytest.mark.gpu
def test_gpu_live_art_end_to_end_with_the_poller(room, monkeypatch):
    lit = lambda fid: _solid(GW, GH, (255, 255, 255) if fid % 2 else (0, 0, 0))   # noqa: E731
    r = room(mode="video", period=1.0, size=(GW, GH), frame=lit)
    ctx, src, out = _gpu_source(r.base, monkeypatch)
    feed, thread = src.feed, src.thread
    try:
        assert feed.version == 1 and thread.is_alive()     # setup's synchronous fetch: first frame is the art
        means = []
        t_end = time.monotonic() + 3.3
        next_t = time.monotonic()
        while time.monotonic() < t_end:                    # paced like the render loop (30 fps)
            means += _render(ctx, src, out, 1)
            next_t += 1.0 / GFPS
            time.sleep(max(0.0, next_t - time.monotonic()))
        assert min(means[0], 255.0 - means[0]) < 3.0         # the art itself (black or white), not a blend
        assert feed.version >= 3, feed.version             # the poller kept up with the 1 s frames
        inter = [m for m in means if 20.0 < m < 235.0]
        assert len(inter) > GFPS, len(inter)               # mostly blending: continuous motion
        assert np.max(np.abs(np.diff(means))) < 255.0 / (la.PERIOD_MIN_S * GFPS) + 1.0
        moving = means[next(i for i, m in enumerate(means) if abs(m - means[0]) > 0.5):]
        holds = sum(1 for a, b in zip(moving, moving[1:]) if abs(a - b) < 0.5)
        assert holds <= 3, moving                          # each blend runs into the next: the motion never stops
    finally:
        src.close()
    thread.join(3)
    assert not thread.is_alive()
    assert len(la._PINNED_FREE.get(GW * GH * 3, ())) + len(la._PINNED_PENDING) == la.N_BUFFERS   # handed back
