#!/usr/bin/env python3
"""Framebuffer probe: when does each frame's barcode actually land in the X scanout buffer?

Independent cross-check of Chrome's own requestVideoFrameCallback timing. Polls a small region
of the X root window (Xorg :0, no compositor => the root pixmap is what gets scanned out) with
XGetSubImage at a few hundred Hz, decodes the frame-id barcode, and reports first-seen times to
the lab server, already converted to the SERVER clock (skew-aware sync, same algorithm as
static/sync.js; the WSL server clock runs ~2 % fast, so a constant offset would be stale in seconds).
What it cannot see: the panel's scanout position within the refresh and the TV's own processing.

It shares Xorg with the production kiosk Chromes, so it is deliberately frugal:
  * ONE X connection for the life of the process, used only by the poll thread (Xlib is not
    thread-safe); images are allocated once and reused via XGetSubImage, and freed on exit.
  * X protocol errors (e.g. --screen-x off the root window after a hotplug) are caught and backed
    off instead of Xlib's default exit(); a failed barcode search backs off 0.5 s -> 5 s.
  * nice +10, 200 Hz polling by default (5 ms first-seen quantisation; a 30 Hz panel needs >= 120).
  * no X traffic at all while there is no server / session; reconnects after a server restart.
  * exits when its stdout goes away (ssh dropped), on SIGTERM/SIGHUP or Ctrl-C.

Run on the mini-PC as the kiosk user (no installs; uses libX11 via ctypes + websockets from ~/.venv):
  ~/.venv/bin/python fb_probe.py ws://192.168.1.205:8081/ws --screen-x 3840 [--hz 200]
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.util
import json
import os
import signal
import threading
import time

import websockets

BITS = 40
SYNC_WINDOW_MS = 20000  # clock-sync samples kept (decision shared with static/sync.js)

# CLOCK_MONOTONIC plus a fixed epoch anchor: smooth, never stepped by timesyncd (time.time_ns() is).
_ANCHOR_NS = time.time_ns() - time.monotonic_ns()


def now_ms() -> float:
    return (time.monotonic_ns() + _ANCHOR_NS) / 1e6


class _Funcs(ctypes.Structure):
    _fields_ = [("create_image", ctypes.c_void_p), ("destroy_image", ctypes.c_void_p),
                ("get_pixel", ctypes.c_void_p), ("put_pixel", ctypes.c_void_p),
                ("sub_image", ctypes.c_void_p), ("add_pixel", ctypes.c_void_p)]


class XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
                ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
                ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong),
                ("obdata", ctypes.c_void_p), ("f", _Funcs)]


class XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p), ("resourceid", ctypes.c_ulong),
                ("serial", ctypes.c_ulong), ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
_DESTROY_IMAGE = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(XImage))


def crc8(v: int) -> int:
    crc = 0
    for byte in v.to_bytes(4, "big"):
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class XError(Exception):
    pass


class XConn:
    """The probe's single X connection. While polling, only the poll thread may touch it."""
    ZPixmap = 2

    def __init__(self, display: bytes):
        self.xlib = xlib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        xlib.XDefaultRootWindow.restype = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XSetErrorHandler.restype = ctypes.c_void_p
        xlib.XSetErrorHandler.argtypes = [_ERROR_HANDLER]
        xlib.XGetImage.restype = ctypes.POINTER(XImage)
        xlib.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        if hasattr(xlib, "XDestroyImage"):
            xlib.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
        xlib.XGetSubImage.restype = ctypes.POINTER(XImage)
        xlib.XGetSubImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
                                      ctypes.POINTER(XImage), ctypes.c_int, ctypes.c_int]
        self.errors = 0
        self.last_error = None
        # Xlib's default handler calls exit() on e.g. BadMatch (grab outside the root window).
        # Keep a reference: the ctypes callback must outlive the connection.
        self._handler = _ERROR_HANDLER(self._on_error)
        xlib.XSetErrorHandler(self._handler)
        self.dpy = xlib.XOpenDisplay(display)
        if not self.dpy:
            raise SystemExit(f"cannot open X display {display.decode()}")
        self.root = xlib.XDefaultRootWindow(self.dpy)
        self.images: list[XImg] = []

    def _on_error(self, _dpy, ev) -> int:
        self.errors += 1
        e = ev.contents
        self.last_error = f"X error code {e.error_code} (request {e.request_code})"
        return 0

    def image(self, x: int, y: int, w: int, h: int) -> "XImg":
        """Allocate a w x h image (filled from the root at x,y); reuse it with XImg.grab()."""
        before = self.errors
        p = self.xlib.XGetImage(self.dpy, self.root, x, y, w, h, 0xFFFFFFFF, self.ZPixmap)
        if not p or self.errors != before:
            if p:
                self._destroy(p)
            raise XError(f"XGetImage {w}x{h}+{x}+{y} failed: {self.last_error}")
        if p.contents.bits_per_pixel != 32:
            self._destroy(p)
            raise XError("unexpected X image format (need 32 bpp)")
        img = XImg(self, p, w, h)
        self.images.append(img)
        return img

    def _destroy(self, p) -> None:
        # XDestroyImage is a real export in every current libX11; older ones only have the macro,
        # which calls the image's own destroy_image function.
        if hasattr(self.xlib, "XDestroyImage"):
            self.xlib.XDestroyImage(p)
        else:
            _DESTROY_IMAGE(p.contents.f.destroy_image)(p)

    def close(self) -> None:
        for img in self.images:
            img.free()
        self.images.clear()
        if self.dpy:
            self.xlib.XCloseDisplay(self.dpy)
            self.dpy = None


class XImg:
    def __init__(self, conn: XConn, ptr, w: int, h: int):
        self.conn, self.ptr, self.w, self.h = conn, ptr, w, h
        self.bpl = ptr.contents.bytes_per_line

    def grab(self, x: int, y: int, w: int | None = None, h: int | None = None) -> bytes:
        """Read root (x, y, w, h) into this image (w, h <= its size) and return a copy of the bytes."""
        w, h = w or self.w, h or self.h
        c = self.conn
        before = c.errors
        r = c.xlib.XGetSubImage(c.dpy, c.root, x, y, w, h, 0xFFFFFFFF, c.ZPixmap, self.ptr, 0, 0)
        if not r or c.errors != before:
            raise XError(f"XGetSubImage {w}x{h}+{x}+{y} failed: {c.last_error}")
        return ctypes.string_at(self.ptr.contents.data, self.bpl * (h - 1) + w * 4)

    def free(self) -> None:
        if self.ptr:
            self.conn._destroy(self.ptr)
            self.ptr = None


class ClockSync:
    """Skew-aware NTP-style sync to the server clock (same algorithm as static/sync.js).

    Keep {c0, c1, s} from the last 20 s; take the low-RTT envelope (rtt <= min + max(1 ms, min/2)),
    and with >= 5 such samples spanning >= 2 s fit (s - c_mid) = a + b*(c_mid - c_ref) by least
    squares, else b = 0 and a from the single minimum-RTT sample. A new connection or server epoch
    starts from scratch (a restarted server has a different clock origin)."""

    def __init__(self, epoch=None):
        self.reset(epoch)

    def reset(self, epoch) -> None:
        self.epoch = epoch
        self.samples: list[tuple[float, float, float]] = []  # (c0, c1, s)
        self.a = self.b = self.c_ref = None
        self.rtt = self.resid = None
        self.n = 0

    @property
    def ready(self) -> bool:
        return self.a is not None

    def set_epoch(self, epoch) -> None:
        # config/pong carry the server's epoch id: a different one means a different server clock
        if epoch is not None and epoch != self.epoch:
            self.reset(epoch)

    def add(self, c0: float, c1: float, s: float, epoch) -> None:
        self.set_epoch(epoch)
        if c1 < c0:
            return
        self.samples = [x for x in self.samples if x[1] > c1 - SYNC_WINDOW_MS]
        self.samples.append((c0, c1, s))
        pts = [((c0 + c1) / 2, c1 - c0, s - (c0 + c1) / 2) for c0, c1, s in self.samples]  # (c_mid, rtt, off)
        min_rtt = min(p[1] for p in pts)
        env = [p for p in pts if p[1] <= min_rtt + max(1.0, 0.5 * min_rtt)]
        span = max(p[0] for p in env) - min(p[0] for p in env)
        if len(env) >= 5 and span >= 2000:
            c_ref = sum(p[0] for p in env) / len(env)
            y_mean = sum(p[2] for p in env) / len(env)
            sxx = sum((p[0] - c_ref) ** 2 for p in env)
            b = sum((p[0] - c_ref) * (p[2] - y_mean) for p in env) / sxx if sxx > 0 else 0.0
            a = y_mean  # the line passes through (mean x, mean y) and x is centred on c_ref
        else:
            best = min(env, key=lambda p: p[1])
            a, b, c_ref = best[2], 0.0, best[0]
        self.a, self.b, self.c_ref = a, b, c_ref
        self.rtt = min_rtt
        self.resid = max(abs(p[2] - (a + b * (p[0] - c_ref))) for p in env)
        self.n = len(env)

    def to_server(self, t: float) -> float | None:
        if self.a is None:
            return None
        return t + self.a + self.b * (t - self.c_ref)

    def summary(self) -> dict:
        r = (lambda v, k=3: None if v is None else round(v, k))
        return {"rtt": r(self.rtt), "ppm": r(None if self.b is None else self.b * 1e6, 1),
                "resid": r(self.resid), "n": self.n}


def decode(buf: bytes, bpl: int, block: float, row: int) -> int | None:
    # sample the centre of each block on the given row; green channel of BGRx
    def g(i):
        x = int((i + 0.5) * block)
        return buf[row * bpl + x * 4 + 1]
    black, white = g(0), g(1)
    if white - black < 60:
        return None
    thr = (black + white) / 2
    word = 0
    for i in range(BITS):
        word = (word << 1) | (1 if g(i + 2) > thr else 0)
    fid, crc = word >> 8, word & 0xFF
    return fid if crc8(fid) == crc else None


_stdout_ok = True
_on_stdout_gone = None  # set by amain(): stop the probe when nobody is watching any more


def log(msg: str) -> None:
    global _stdout_ok
    if not _stdout_ok:
        return
    try:
        print(msg, flush=True)
    except OSError:  # stdout gone (the operator's ssh session ended): do not keep polling X unattended
        _stdout_ok = False
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), 1)  # let the final flush at exit succeed quietly
        except OSError:
            pass
        if _on_stdout_gone:
            try:
                _on_stdout_gone()
            except RuntimeError:  # loop already closed
                pass


class Probe:
    LOCATE_ROWS = 400

    def __init__(self, args, xconn: XConn):
        self.args = args
        self.x = xconn
        self.big: XImg | None = None  # screen_w x 400, for locating the barcode
        self.row: XImg | None = None  # screen_w x 1, for confirming and polling it
        self.cfg = None               # current 'config' message (set by the asyncio thread)
        self.session = None
        self.found: list[tuple[int, int, float]] = []  # (session, frame id, first seen, local ms)
        self.lock = threading.Lock()
        self.stats = self._new_stats()
        self.stop = threading.Event()

    def _new_stats(self) -> dict:
        return {"polls": 0, "decoded": 0, "changes": 0, "grab_ms_max": 0.0, "jumps": 0, "stale": 0,
                "locates": 0, "locate_candidates": None, "located": None, "low_rate_relocates": 0,
                "x_errors": 0, "hz": self.args.hz}

    def stats_copy(self) -> dict:
        with self.lock:
            return dict(self.stats)

    # -- X side (poll thread only) ------------------------------------------------
    def images(self) -> None:
        a = self.args
        if self.big is None:
            self.big = self.x.image(a.screen_x, a.screen_y, a.screen_w, self.LOCATE_ROWS)
        if self.row is None:
            self.row = self.x.image(a.screen_x, a.screen_y, a.screen_w, 1)

    def candidates(self, cfg: dict):
        """Yield plausible barcode positions: a black calibration block (which may merge with a black
        letterbox bar on its left) then a white block of ~equal width, with a CRC-valid decode.
        Scaling blurs each edge over a few grey pixels, so a short transition zone is allowed.
        CRC-8 alone gives false positives across thousands of candidates; confirm() filters them.
        The CRC (zero init, no xorout) is shift-invariant: a block size that drifts by one bit over the
        leading zero bits decodes id>>1 (or id<<1) with a valid CRC for half the frames. Hence block sizes
        nearest the measured width are tried first, and poll_loop re-locates when the decode rate drops."""
        sw = self.args.screen_w  # a 42-block strip at 2x scale is ~2000 px wide
        sh = self.LOCATE_ROWS
        buf = self.big.grab(self.args.screen_x, self.args.screen_y)
        bpl = self.big.bpl
        count = cfg["barcode"]["count"]
        nominal = cfg["barcode"]["block"] * self.args.screen_w / cfg["width"]
        n = min(sw, 900)
        for y in range(0, sh, 2):
            row = buf[y * bpl:y * bpl + sw * 4]
            G = row[1:n * 4:4]  # green channel
            x = 0
            while x < n:
                if G[x] >= 70:
                    x += 1
                    continue
                x0 = x
                while x < n and G[x] < 70:
                    x += 1
                dark = x - x0
                t = x
                while x < n and x - t < 4 and 70 <= G[x] <= 180:
                    x += 1
                x1 = x
                while x < n and G[x] > 180:
                    x += 1
                bright = x - x1
                width = bright + (x1 - t)  # white block incl. one blurred edge
                if not (0.5 * nominal <= width <= 1.5 * nominal and dark >= 0.6 * width):
                    continue
                edge = (t + x1) / 2
                for k in sorted(range(-8, 9), key=abs):
                    block = width + k * 0.25
                    xs0 = edge - block
                    for xs in (int(xs0), int(xs0) + 1, int(xs0) - 1):
                        if xs < 0 or xs + block * count >= sw:
                            continue
                        if decode(row[xs * 4:], 0, block, 0) is not None:
                            yield xs, y, block

    def strip_width(self, cfg: dict, xs: int, block: float) -> int:
        return min(int(block * cfg["barcode"]["count"]) + 2, self.args.screen_w - xs)

    def confirm(self, cfg: dict, loc) -> bool:
        xs, y, block = loc
        w = self.strip_width(cfg, xs, block)
        ids = []
        for _ in range(8):
            fid = decode(self.row.grab(self.args.screen_x + xs, self.args.screen_y + y, w, 1), 0, block, 0)
            if fid is not None:
                ids.append(fid)
            time.sleep(0.02)
        return len(ids) >= 5 and ids == sorted(ids) and ids[-1] - ids[0] <= 30

    def locate(self, cfg: dict):
        self.images()
        self.stats["locates"] += 1
        tried = 0
        for cand in self.candidates(cfg):
            if self.stop.is_set() or self.cfg is not cfg:
                break
            tried += 1
            if self.confirm(cfg, cand):
                self.stats["locate_candidates"] = tried
                return cand
            if tried > 40:
                break
        self.stats["locate_candidates"] = tried
        return None

    def poll_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self._poll_loop()
            except Exception as e:  # never die silently: the process would keep reporting nothing
                log(f"probe: poll thread error {type(e).__name__}: {e}; restarting in 2 s")
                self.stop.wait(2)

    def _poll_loop(self) -> None:
        """The only thread that touches X, for the life of the process. Follows self.cfg across
        sessions and reconnects, and is idle (no X requests) while there is no session."""
        period = 1.0 / self.args.hz
        cur = geom = None
        loc = prev = None  # prev: last good position, re-checked cheaply (1-row grabs) before a full locate
        quick_at = 0.0
        last = pending = lost_since = None  # pending = (fid, t): a new id must decode identically twice
        max_fid = None   # (t0, fps): bound on ids in a session that started while we watched
        win_polls = win_ok = 0  # decode rate since the last check: a true lock decodes ~every poll
        skip_first = True  # the first id after a (re)lock or a mid-session join was on screen before we looked
        retry_at, backoff = 0.0, 0.5
        while not self.stop.is_set():
            cfg = self.cfg
            if cfg is not cur:
                new_geom = None if cfg is None else (cfg["width"], cfg["height"], json.dumps(cfg["barcode"]))
                if new_geom != geom:
                    loc, prev, geom, retry_at, backoff = None, None, new_geom, 0.0, 0.5
                if cfg is None or cur is None or cfg["session"] != cur["session"]:
                    # (a repeated config for the same session keeps `last`: the picture on screen now
                    # is not new, and reporting it again would give it a late first-seen time)
                    last, pending, lost_since = None, None, None
                if cfg is not None and (cur is None or cfg["session"] != cur["session"]):
                    with self.lock:
                        self.stats = self._new_stats()
                    if loc is not None:  # same geometry: keep the barcode position
                        self.stats["located"] = {"x": x0, "y": y0, "block": round(block, 2)}
                    # The previous session's last frames can still be on screen for a moment; in a
                    # session that started while we watched, ids are bounded by its age.
                    max_fid = (time.monotonic(), cfg["fps"]) if cfg.get("_started_live") else None
                    skip_first = skip_first or not cfg.get("_started_live")
                cur = cfg
            if cfg is None:
                self.stop.wait(0.1)
                continue
            try:
                if loc is None:
                    now = time.monotonic()
                    if prev is not None and now >= quick_at:
                        quick_at = now + 0.5
                        if self.confirm(cfg, prev):
                            loc = prev
                    if loc is None and now < retry_at:
                        self.stop.wait(0.05)
                        continue
                    if loc is None:
                        loc = self.locate(cfg)
                    if loc is None:
                        retry_at, backoff = time.monotonic() + backoff, min(backoff * 2, 5.0)
                        continue
                    backoff = 0.5
                    win_polls = win_ok = 0
                    skip_first = True
                    x0, y0, block = loc
                    width = self.strip_width(cfg, x0, block)
                    self.stats["located"] = {"x": x0, "y": y0, "block": round(block, 2)}
                    log(f"probe: barcode at +{x0},+{y0} block {block:.2f}px")
                t0 = time.perf_counter()
                a = now_ms()
                buf = self.row.grab(self.args.screen_x + x0, self.args.screen_y + y0, width, 1)
                b = now_ms()
            except XError as e:  # e.g. the root window shrank (HDMI hotplug): a full locate later
                prev = None
                self.stats["x_errors"] += 1
                log(f"probe: {e}; retrying in {backoff:.1f} s")
                loc, retry_at, backoff = None, time.monotonic() + backoff, min(backoff * 2, 5.0)
                continue
            self.stats["polls"] += 1
            self.stats["grab_ms_max"] = max(self.stats["grab_ms_max"], round(b - a, 3))
            fid = decode(buf, 0, block, 0)
            if fid is not None and max_fid is not None:
                t_start, fps = max_fid
                if fid > (time.monotonic() - t_start + 2.0) * fps:
                    self.stats["stale"] += 1  # still the previous session's picture: not lost, not new
                    lost_since, fid = None, -1
                else:
                    max_fid = None
            win_polls += 1
            win_ok += fid is not None
            if win_polls >= 2 * self.args.hz:  # every ~2 s
                gone = lost_since is not None and time.monotonic() - lost_since > 0.25  # not just one bad frame
                if 0 < win_ok < 0.7 * win_polls and not gone:  # ~50 %: a shifted (wrong block size) lock
                    self.stats["low_rate_relocates"] += 1
                    log(f"probe: only {win_ok}/{win_polls} polls decoded; locating again")
                    loc, prev, lost_since, fid = None, None, None, -1
                win_polls = win_ok = 0
            if fid == -1:
                pass
            elif fid is not None:
                lost_since = None
                self.stats["decoded"] += 1
                if fid != last:
                    # the same id on two consecutive polls is a real new picture, not a torn read; it is
                    # accepted even when it jumps (live catch-up, page reload, stream restart), otherwise
                    # one big jump would stall the probe for good
                    if pending and pending[0] == fid:
                        if skip_first:
                            skip_first = False
                        else:
                            if last is not None and not 0 < fid - last <= 4 * cfg["fps"]:
                                self.stats["jumps"] += 1
                            self.stats["changes"] += 1
                            if self.args.debug:
                                log(f"fb {fid} at {pending[1]:.1f} (grab {b - a:.2f} ms)")
                            with self.lock:
                                self.found.append((cfg["session"], fid, pending[1]))
                        last, pending = fid, None
                    else:
                        pending = (fid, (a + b) / 2)
            else:
                lost_since = lost_since or time.monotonic()
                if time.monotonic() - lost_since > 2.0:  # gone (page reload, new run) or layout changed
                    loc, prev, lost_since = None, loc, None
                    retry_at, backoff = time.monotonic() + 2.0, 2.0  # quick re-checks of prev first
            dt = period - (time.perf_counter() - t0)
            if dt > 0:
                time.sleep(dt)

    # -- network side (asyncio thread) ----------------------------------------------
    async def run(self) -> None:
        """Connect; after a disconnect (e.g. server restarted for --matrix) reconnect every 2 s.
        Each connection starts a fresh clock sync."""
        last_err = None
        while not self.stop.is_set():
            try:
                await self.connection()
                last_err = None
            except Exception as e:  # network errors, but also a malformed message: never exit unattended
                err = f"{type(e).__name__}: {e}"
                if err != last_err:
                    log(f"probe: connection: {err} (retrying every 2 s)")
                last_err = err
            finally:
                self.cfg = self.session = None  # no X polling while disconnected
                with self.lock:
                    self.found = []
            if not self.stop.is_set():
                await asyncio.sleep(2)

    async def connection(self) -> None:
        sync = ClockSync()
        async with websockets.connect(self.args.ws, max_size=None, compression=None) as ws:
            log(f"probe: connected to {self.args.ws}")
            await ws.send(json.dumps({"type": "hello", "role": "probe", "mode": "fb",
                                      "info": {"screen_x": self.args.screen_x, "hz": self.args.hz,
                                               "clock": "monotonic+anchor"}}))
            sent = 0

            async def pinger():
                n = 0
                while True:
                    n += 1
                    await ws.send(json.dumps({"type": "ping", "id": n, "c0": now_ms()}))
                    await asyncio.sleep(0.04 if n < 10 else 0.25)

            async def reporter():
                nonlocal sent
                ticks = 0
                while True:
                    await asyncio.sleep(0.25)
                    ticks += 1
                    session = self.session
                    with self.lock:
                        items = [i for i in self.found if i[0] == session]
                        self.found = [] if sync.ready else items[-4000:]  # hold until synced
                    if session is not None and sync.ready and (items or ticks % 4 == 0):
                        # timestamps go out in server-clock ms: the server applies no offset
                        await ws.send(json.dumps({
                            "type": "fb", "session": session, "epoch": sync.epoch, "clock": sync.summary(),
                            "items": [[fid, round(sync.to_server(t), 3)] for _, fid, t in items],
                            "stats": self.stats_copy()}))
                        sent += len(items)
                    if ticks % 40 == 0:  # also notices a dead stdout (ssh gone) within 10 s
                        s, st = sync.summary(), self.stats_copy()
                        log(f"probe: session {session} sync rtt {s['rtt']} ms skew {s['ppm']} ppm "
                            f"resid {s['resid']} ms n {s['n']} | polls {st['polls']} decoded {st['decoded']} "
                            f"fb sent {sent} grab_max {st['grab_ms_max']} ms x_errors {st['x_errors']}")

            async def receiver():
                async for raw in ws:
                    if not isinstance(raw, str):
                        continue
                    msg = json.loads(raw)
                    if msg.get("type") == "pong":
                        c1 = now_ms()
                        if isinstance(msg.get("c0"), (int, float)) and isinstance(msg.get("s"), (int, float)):
                            sync.add(float(msg["c0"]), c1, float(msg["s"]), msg.get("epoch"))
                    elif msg.get("type") == "config":
                        sync.set_epoch(msg.get("epoch"))
                        # a session that starts while we are connected begins at frame 0
                        msg["_started_live"] = self.session is not None and msg["session"] != self.session
                        self.session = msg["session"]
                        self.cfg = msg
                        log(f"probe: session {msg['session']} {msg['width']}x{msg['height']} -> polling")

            tasks = [asyncio.create_task(f()) for f in (receiver, pinger, reporter)]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t.exception() and not isinstance(t.exception(), websockets.exceptions.ConnectionClosed):
                        raise t.exception()
                log("probe: disconnected")
            finally:
                for t in tasks:
                    t.cancel()


async def amain(probe: Probe) -> None:
    global _on_stdout_gone
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGHUP):
        loop.add_signal_handler(sig, main_task.cancel)
    _on_stdout_gone = lambda: loop.call_soon_threadsafe(main_task.cancel)  # noqa: E731
    await probe.run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ws")
    ap.add_argument("--screen-x", type=int, default=3840, help="0 for HDMI-1/north, 3840 for HDMI-2/south")
    ap.add_argument("--screen-y", type=int, default=0)
    ap.add_argument("--screen-w", type=int, default=3840)
    ap.add_argument("--hz", type=float, default=200, help="poll rate; keep >= 4x the panel refresh")
    ap.add_argument("--nice", type=int, default=10, help="lower our own CPU priority (production shares the box)")
    ap.add_argument("--display", default=":0")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    if args.nice:
        os.nice(args.nice)  # before any thread starts: Linux nice is per thread and inherited
    xconn = XConn(args.display.encode())
    probe = Probe(args, xconn)
    poller = threading.Thread(target=probe.poll_loop, name="x-poll", daemon=True)
    poller.start()
    try:
        asyncio.run(amain(probe))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        probe.stop.set()
        poller.join(timeout=3)
        if not poller.is_alive():  # never close the display under a thread that is still using it
            xconn.close()
        log("probe: stopped")


if __name__ == "__main__":
    main()
