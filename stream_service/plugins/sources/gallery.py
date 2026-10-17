"""Gallery: the screen's own pictures as a scene, paged in time with the music.

Source: Original
Author: smfx (screen-machine)
License: MIT
license_class: permissive

Each TV shows only its own bucket (``ctx.screen`` = the bucket id). Only the TV screens' buckets are read: the
publish-destinations entries with a ``live`` block (Flask's display config, as room_service reads it), so _recent,
greg-mobile, the-beast and the like never are; anything else, fxtest and Library previews included, falls back to the
first TV screen. The art directory is the renderer's own ``--art-dir``.  Pictures: DATE-HEX stills (``yyyymmdd-
hhmmss-<8 hex>.jpg|png``) with a sidecar holding a prompt and a workflow, at least 1920 wide, 16:9 within 10 %.  No
mp4s.  The prompt text is never read into this process, shown or logged
(the decode worker checks the sidecar and answers yes or no).  Never masks, never publishes, takes no palette (a tint).

* Deck (module level, per screen, survives the 20 s rescan reload and the director's rebuilds): the worker indexes
  bucket.json (the last good index is kept: the file is rewritten in place), pools favourites / 30-day / 7-day
  recents, and draws from shuffled bags per category with a shown-memory, so pictures don't repeat until most
  have been seen.  Missing or filtered-out files are skipped and remembered.
* Decoder (module level, one per screen; its worker stops after IDLE_KILL_S with no gallery on that screen): a worker SUBPROCESS (``python -I -c <inline source>``, nice 19 inside,
  its own GIL) decodes with PIL (JPEG draft to the size needed, so 1080 drafts at rush pace) and sends raw RGB down a
  length-prefixed pipe; a daemon thread reads it with readinto straight into one pinned host block (no tobytes or
  np.asarray of a picture in this process).  One decode in flight per screen.  The pinned blocks (one per
  screen, 3840x2160x3 each) are the gallery's own: allocated on a screen's first gallery setup in the process
  (one cudaHostAlloc hitch per screen per process life) and never freed; live_art's reserved blocks are left alone.  If the
  pinned allocation fails it degrades to pageable host memory.
* Ring (per instance): 3 packed-RGB device slots plus 2 RGBA work frames for the hosted transitions.  setup checks
  free VRAM first (memGetInfo): 4K slots on GPU 1, 1080 slots on GPU 0 unless memory is ample, 1080 always at
  draft quality; every allocation is wrapped and steps down (1080, then no work frames = in-kernel dissolves and
  cuts only).  Uploads are async H2D copies on the instance's own stream, used once their event is done (a query,
  never a synchronize in render).  One kernel cover-fits a slot with a Ken Burns affine, tint and gain; a second
  dissolves two slots directly.
* Timing: show_service's beat clock read directly from the session runtime (``self.modulation.runtime``), at
  photon time (rt.now + sampler.lead), usable only on the session's own test (gate beat, state locked or holding,
  follow extrapolate).  Lines come from beat numbers: phrase lines, bar lines counted down an energy ladder
  (4 -> 2 -> 1 bars) or beat lines (every 2 beats, every beat at peak); a dissolve is centred on its line; a clock
  re-anchor (epoch, bar anchor, metre or phrase length) re-baselines with no change until the next whole beat.  If
  the next picture isn't ready the current one holds to the next line.  Without a usable clock (fxtest, gate dmx,
  silence) a timer scaled by energy runs.  The ladder reads the session's raw energy_slow when it has one.
* Transitions: CUDA-native ones only (cut, melt, luma, wipe, pixelate, glitch) hosted in setup with fixed seeds,
  plus an in-kernel dissolve.  An exception in one becomes a cut and benches it for the instance; any exception in
  render draws the current picture (or black) and never escapes.  A guard trip of the whole scene (a GPU stall)
  can still black it, cue a recovery and mark it slow for the process life: keep it cheap (about 1-2 ms held,
  3-5 ms in a transition at 4K; no per-frame allocation).

Macros: energy = Ken Burns rate, timer pace, transition length, the ladder fallback; pulse = zoom-rate kick and a
small brightness lift (at most +12 %); speed = drift rate; intensity = gain.  render_scale does nothing (pictures
are drawn at output size).
"""
from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import random
import re
import select
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from stream_service.easing import ease
from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import enum, flag, num, standard_scene_params
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.gallery")

IDLE_KILL_S = 60.0              # a screen's decode worker stops after this long with no gallery using it
DATE_HEX = r"^\d{8}-\d{6}-[0-9a-f]{8}\.(jpg|png)$"
BLOCK_W, BLOCK_H = 3840, 2160
BLOCK_BYTES = BLOCK_W * BLOCK_H * 3
DRAFT_W, DRAFT_H = 1920, 1080
MB = 1 << 20
GPU0_AMPLE = 2048 * MB          # GPU 0 (desktop card, WDDM): 4K slots only with this much free beyond our need
MARGIN = 512 * MB               # always leave this free for the other sessions on the card
SETUP_WAIT_S = 2.5              # setup waits this long (in all) for the opening pictures
IO_TIMEOUT_S = 10.0             # a worker reply slower than this = a hung worker: killed, restarted
HANDOVER_S = 1.5                # a decoded block nobody takes within this goes back to the decoder
RESTART_S = 10.0                # worker restarts at most this often
INDEX_S = 60.0                  # re-index a bucket at most this often
RETRY_S = 1.0                   # an instance re-requests a failed picture after this
PULSE_LIFT = 0.12
LADDER_BAR = ((0.55, 2), (0.80, 1))   # energy_slow edges for the bar ladder (4 bars below the first)
BEAT_PEAK = 0.80                # beat pace: every beat at or above, every 2 beats below
RUSH_CUT = 0.95                 # rush transitions: a plain cut at or above
HYST = 0.04

# ----------------------------------------------------------------------------- the decode worker (own process)
_WORKER = r'''
import json, os, re, struct, sys
try:
    os.nice(max(0, 19 - os.nice(0)))
except Exception:
    pass
from PIL import Image, ImageOps
Image.MAX_IMAGE_PIXELS = 64000000
fin, fout = sys.stdin.buffer, sys.stdout.buffer
SIDE = {}

def rd(n):
    b = b""
    while len(b) < n:
        c = fin.read(n - len(b))
        if not c:
            return None
        b += c
    return b

def send(meta, data=b""):
    m = json.dumps(meta).encode()
    fout.write(struct.pack(">I", len(m)) + m + struct.pack(">I", len(data)))
    if data:
        fout.write(data)
    fout.flush()

def sidecar_ok(path):
    v = SIDE.get(path)
    if v is None:
        try:
            with open(path + ".json", "rb") as f:
                d = json.load(f)
            v = isinstance(d, dict) and bool(d.get("prompt")) and bool(d.get("workflow"))
        except Exception:
            v = False
        SIDE[path] = v
    return v

def index(req):
    d = req["dir"]
    with open(os.path.join(d, "bucket.json"), "rb") as f:
        b = json.load(f)
    pat = re.compile(req["pattern"])
    seq = [e.get("file") if isinstance(e, dict) else e for e in (b.get("sequence") or [])]
    fav = list(b.get("favorites") or [])
    def keep(xs):
        return [x for x in xs if isinstance(x, str) and pat.match(x) and os.path.isfile(os.path.join(d, x))]
    return {"ok": True, "fav": keep(fav), "seq": keep(seq)}, b""

def decode(req):
    path, tw, th = req["path"], int(req["w"]), int(req["h"])
    filt = bool(req.get("filter"))
    if filt and not sidecar_ok(path):
        return {"ok": False, "skip": "sidecar"}, b""
    with Image.open(path) as img:
        if img.format not in ("JPEG", "PNG"):
            return {"ok": False, "skip": "format"}, b""
        w0, h0 = img.size
        if filt and (w0 < 1920 or h0 <= 0 or abs((w0 / h0) / (16.0 / 9.0) - 1.0) > 0.10):
            return {"ok": False, "skip": "shape"}, b""
        if img.format == "JPEG":
            img.draft("RGB", (tw, th))
        img.load()
        im = img
        try:
            if img.format == "JPEG" and img.getexif().get(0x0112, 1) not in (None, 1):
                im = ImageOps.exif_transpose(im)
        except Exception:
            pass
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
            im = Image.alpha_composite(bg, rgba).convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if im.width > tw or im.height > th:
            s = min(tw / im.width, th / im.height)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                           Image.Resampling.BILINEAR, reducing_gap=2.0)
        return {"ok": True, "w": im.width, "h": im.height}, im.tobytes()

while True:
    head = rd(4)
    if head is None:
        break
    raw = rd(struct.unpack(">I", head)[0])
    if raw is None:
        break
    try:
        req = json.loads(raw)
        meta, data = (index if req.get("op") == "index" else decode)(req)
    except Exception as e:
        meta, data = {"ok": False, "error": type(e).__name__}, b""
    send(meta, data)
'''


def _repo() -> Path:
    import stream_service
    return Path(stream_service.__file__).resolve().parent.parent


def output_dir() -> Path:
    """The art directory: SM_OUTPUT_DIR, else the renderer's own --art-dir (stream_service/__main__.py), else its
    default (<repo>/output)."""
    env = os.environ.get("SM_OUTPUT_DIR")
    if env:
        return Path(env)
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--art-dir" and i + 1 < len(argv):
            return Path(argv[i + 1]).resolve()
        if a.startswith("--art-dir="):
            return Path(a.split("=", 1)[1]).resolve()
    return _repo() / "output"


_SCREENS_CACHE: dict = {"mtime": None, "ids": ()}


def screens() -> tuple:
    """The TV screens: publish-destinations entries with a live block (Flask's display config, the same file
    room_service reads), in file order. Their buckets are the only ones the gallery reads, so phone wallpapers,
    work content and upload buckets never are. The last good list is kept if the file can't be read."""
    path = Path(os.environ.get("SM_DESTINATIONS") or _repo() / "routes" / "data" / "publish-destinations.json")
    try:
        m = path.stat().st_mtime
        if m != _SCREENS_CACHE["mtime"]:
            data = json.loads(path.read_text())
            ids = tuple(str(d["id"]) for d in data
                        if isinstance(d, dict) and d.get("id") and isinstance(d.get("live"), dict))
            _SCREENS_CACHE.update(mtime=m, ids=ids)
    except Exception as e:
        log.info("gallery: screens from %s unavailable (%s); keeping %s", path.name, type(e).__name__,
                 list(_SCREENS_CACHE["ids"]))
    return _SCREENS_CACHE["ids"]


# ticket states
QUEUED, READY, TAKEN, FAILED, DONE = range(5)


class _Ticket:
    """One picture asked of a decoder: a pool pick (deck + pool) or a path (the wall art)."""
    __slots__ = ("deck", "pool", "path", "w", "h", "state", "name", "pw", "ph", "event", "dev", "t_ready",
                 "t_taken", "abandoned", "done")

    def __init__(self, w: int, h: int, deck=None, pool: str = "", path: str = ""):
        self.deck, self.pool, self.path, self.w, self.h = deck, pool, path, w, h
        self.state, self.name, self.pw, self.ph = QUEUED, "", 0, 0
        self.event, self.dev, self.t_ready, self.t_taken = None, 0, 0.0, 0.0
        self.abandoned = False
        self.done = threading.Event()


class _Block:
    def __init__(self, cp):
        self.pinned = True
        try:
            self.mem = cp.cuda.PinnedMemoryPointer(
                cp.cuda.PinnedMemory(BLOCK_BYTES, cp.cuda.runtime.hostAllocPortable), 0)
            self.host = np.frombuffer(self.mem, np.uint8, BLOCK_BYTES)
        except Exception as e:  # degrade: pageable memory (the async copy then runs synchronously)
            log.warning("gallery: pinned host block failed (%s); using pageable memory", type(e).__name__)
            self.pinned, self.mem = False, None
            self.host = np.zeros(BLOCK_BYTES, np.uint8)
        self.ptr = self.host.ctypes.data
        self.view = memoryview(self.host)


_BANK_LOCK = globals().get("_BANK_LOCK") or threading.Lock()
_BANK: dict = globals().get("_BANK") or {}          # screen -> _Block (both made at once: one hitch per process)


def _block(cp, screen: str) -> "_Block":
    """The screen's pinned staging block: made on the screen's first gallery setup (one hitch per screen per
    process) and kept."""
    with _BANK_LOCK:
        b = _BANK.get(screen)
        if b is None:
            b = _BANK[screen] = _Block(cp)
        return b


class _Deck:
    """A screen's pictures: the index (last good kept), pools and shuffled bags with a shown-memory."""

    def __init__(self, screen: str):
        self.screen = screen
        self.dir = output_dir() / screen
        self.lock = threading.Lock()
        self.fav: list[str] = []
        self.seq: list[str] = []
        self.bad: set[str] = set()
        self.bags: dict[str, list[str]] = {}
        self.shown: deque = deque(maxlen=24)
        self.t_index = -1e9
        self.indexed = False
        self.rng = random.Random()

    def stale(self) -> bool:
        return time.monotonic() - self.t_index > INDEX_S

    def set_index(self, fav: list, seq: list) -> None:
        with self.lock:
            if fav != self.fav or seq != self.seq:
                self.fav, self.seq = list(fav), list(seq)
                self.bags.clear()
            self.indexed = True

    def _cats(self) -> dict[str, list[str]]:
        now = time.time()
        favs = set(self.fav)
        r7, r30 = [], []
        for n in self.seq:
            if n in favs:
                continue
            try:
                age = (now - time.mktime(time.strptime(n[:8], "%Y%m%d"))) / 86400.0
            except ValueError:
                continue
            if age <= 7.0:
                r7.append(n)
            elif age <= 30.0:
                r30.append(n)
        return {"fav": list(self.fav), "r7": r7, "r30": r30}

    def draw(self, pool: str):
        """The next name for a pool (decoder thread), or None when nothing is left."""
        with self.lock:
            cats = self._cats()
            if pool == "favourites":
                w = {"fav": 1.0}
            elif pool == "mixed":
                w = {"fav": 3.0, "r7": 1.0, "r30": 1.0}
            else:  # fresh: newest week x3
                w = {"r7": 3.0, "r30": 1.0, "fav": 1.0}
            weights = {k: w[k] * len([n for n in cats[k] if n not in self.bad]) for k in w}
            if sum(weights.values()) <= 0:
                weights = {k: float(len([n for n in v if n not in self.bad])) for k, v in cats.items()}
                if sum(weights.values()) <= 0:
                    return None
            r = self.rng.random() * sum(weights.values())
            cat = next(iter(weights))
            for k, v in weights.items():
                if v <= 0:
                    continue
                cat = k
                if r < v:
                    break
                r -= v
            bag = self.bags.get(cat)
            for _ in range(2):
                while bag:
                    n = bag.pop()
                    if n not in self.bad:
                        self.shown.append(n)
                        return n
                live = [n for n in cats[cat] if n not in self.bad]
                k = min(len(self.shown), len(live) // 2)
                avoid = set(list(self.shown)[len(self.shown) - k:]) if k > 0 else set()
                bag = [n for n in live if n not in avoid] or live
                self.rng.shuffle(bag)
                self.bags[cat] = bag
            return None

    def mark_bad(self, name: str) -> None:
        with self.lock:
            self.bad.add(name)


class _Decoder:
    """One screen's decode worker: a subprocess, a daemon thread serving tickets in order, one pinned block."""

    def __init__(self, screen: str, block: _Block):
        self.screen = screen
        self.block = block
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.queue: deque = deque()
        self.proc = None
        self.started_at = -1e9
        self.holder = None
        self.users = 0                     # live gallery instances on this screen (setup +1, close -1)
        self.idle_since = time.monotonic()
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"gallery-{screen}")
        self.thread.start()

    # -- client side (any thread)
    def submit(self, t: _Ticket) -> _Ticket:
        with self.cond:
            self.queue.append(t)
            self.cond.notify()
        return t

    def take(self, t: _Ticket, cp, dst, stream, event) -> bool:
        """Copy a READY ticket's block into the device slot ``dst`` on ``stream``; record ``event`` after it."""
        with self.lock:
            if t.state != READY:
                return False
            n = t.pw * t.ph * 3
            cp.cuda.runtime.memcpyAsync(dst.data.ptr, self.block.ptr, n, cp.cuda.runtime.memcpyHostToDevice,
                                        stream.ptr)
            event.record(stream)
            t.event, t.dev, t.state, t.t_taken = event, dst.device.id, TAKEN, time.monotonic()
            return True

    # -- the thread
    def _loop(self) -> None:
        while True:
            try:
                idle = False
                with self.cond:
                    while not self.queue and not idle:
                        self.cond.wait(5.0)
                        idle = (not self.queue and self.users <= 0 and self.proc is not None
                                and time.monotonic() - self.idle_since > IDLE_KILL_S)
                    t = self.queue.popleft() if self.queue else None
                if t is None:          # nobody has used this screen's worker for a while: stop it (off the lock)
                    self._kill()
                    continue
                if t.abandoned:
                    t.state = FAILED
                    t.done.set()
                    continue
                self._free_block()
                self._serve(t)
            except Exception:  # never dies
                log.exception("gallery decoder %s", self.screen)
                time.sleep(0.5)

    def _free_block(self) -> None:
        t, self.holder = self.holder, None
        while t is not None:
            now = time.monotonic()
            with self.lock:
                if t.state == READY and (t.abandoned or now - t.t_ready > HANDOVER_S):
                    t.state = FAILED      # nobody took it: reclaimed
                    return
                state = t.state
            if state == TAKEN:
                try:
                    import cupy as cp
                    with cp.cuda.Device(t.dev):
                        if t.event is None or t.event.done:
                            t.state = DONE
                            return
                except Exception:
                    if now - t.t_taken > 1.0:
                        return
                if now - t.t_taken > 5.0:
                    return
            elif state != READY:
                return
            time.sleep(0.003)

    def _serve(self, t: _Ticket) -> None:
        try:
            if t.deck is not None:
                d = t.deck
                if d.stale() or not d.indexed:
                    d.t_index = time.monotonic()
                    try:
                        meta, _ = self._call({"op": "index", "dir": str(d.dir), "pattern": DATE_HEX}, None)
                        if meta.get("ok"):
                            d.set_index(meta.get("fav") or [], meta.get("seq") or [])
                        else:
                            log.info("gallery %s: bucket index failed (%s); keeping the last good one",
                                     self.screen, meta.get("error"))
                    except Exception as e:
                        log.info("gallery %s: bucket index failed (%s)", self.screen, type(e).__name__)
                for _ in range(8):
                    name = d.draw(t.pool)
                    if name is None:
                        break
                    meta, n = self._call({"op": "decode", "path": str(d.dir / name), "w": t.w, "h": t.h,
                                          "filter": True}, self.block)
                    if meta.get("ok"):
                        self._ready(t, name, meta)
                        return
                    if meta.get("skip") or meta.get("error") in ("FileNotFoundError", "UnidentifiedImageError",
                                                                 "OSError", "SyntaxError"):
                        d.mark_bad(name)
                t.state = FAILED
            else:
                meta, n = self._call({"op": "decode", "path": t.path, "w": t.w, "h": t.h, "filter": False},
                                     self.block)
                if meta.get("ok"):
                    self._ready(t, "", meta)
                    return
                t.state = FAILED
        except Exception as e:
            log.info("gallery %s: decode failed (%s)", self.screen, type(e).__name__)
            t.state = FAILED
        finally:
            t.done.set()

    def _ready(self, t: _Ticket, name: str, meta: dict) -> None:
        with self.lock:
            t.name, t.pw, t.ph = name, int(meta["w"]), int(meta["h"])
            t.t_ready = time.monotonic()
            t.state = READY
        self.holder = t

    # -- the pipe
    def _ensure(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self._kill()
        if time.monotonic() - self.started_at < RESTART_S:
            raise RuntimeError("worker restarting")
        self.started_at = time.monotonic()
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", _WORKER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                                     start_new_session=True, close_fds=True)
        try:
            fcntl.fcntl(self.proc.stdout.fileno(), 1031, 1 << 20)   # F_SETPIPE_SZ: 1 MB
        except OSError:
            pass

    def _kill(self) -> None:
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.kill()
                p.wait(timeout=1)
            except Exception:
                pass

    def _read_into(self, mv, deadline: float) -> None:
        f = self.proc.stdout
        fd = f.fileno()
        got, n = 0, len(mv)
        while got < n:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("worker reply")
            r, _, _ = select.select([fd], [], [], min(left, 1.0))
            if not r:
                continue
            k = f.readinto(mv[got:])
            if not k:
                raise EOFError("worker exited")
            got += k

    def _call(self, req: dict, block):
        self._ensure()
        try:
            raw = json.dumps(req).encode()
            data = struct.pack(">I", len(raw)) + raw
            fd = self.proc.stdin.fileno()
            while data:
                data = data[os.write(fd, data):]
            deadline = time.monotonic() + IO_TIMEOUT_S
            head = bytearray(4)
            self._read_into(memoryview(head), deadline)
            mb = bytearray(struct.unpack(">I", head)[0])
            self._read_into(memoryview(mb), deadline)
            meta = json.loads(mb)
            self._read_into(memoryview(head), deadline)
            n = struct.unpack(">I", head)[0]
            if n:
                if block is None or n > BLOCK_BYTES or "w" not in meta or n != meta["w"] * meta["h"] * 3:
                    raise ValueError("unexpected payload")
                self._read_into(block.view[:n], deadline)
            return meta, n
        except Exception:
            self._kill()
            raise


_DECKS: dict = globals().get("_DECKS") or {}
_DECODERS: dict = globals().get("_DECODERS") or {}
_REG_LOCK = globals().get("_REG_LOCK") or threading.Lock()


def _deck(screen: str) -> _Deck:
    with _REG_LOCK:
        d = _DECKS.get(screen)
        if d is None:
            d = _DECKS[screen] = _Deck(screen)
        return d


def _decoder(screen: str, cp) -> _Decoder:
    block = _block(cp, screen)
    with _REG_LOCK:
        d = _DECODERS.get(screen)
        if d is None or not d.thread.is_alive():
            d = _DECODERS[screen] = _Decoder(screen, block)
        d.users += 1
        return d


_GRAVE: deque = globals().get("_GRAVE") or deque()   # (event, device array) kept until an upload into it is done


def _purge_grave() -> None:
    for _ in range(len(_GRAVE)):
        try:
            ev, arr = _GRAVE.popleft()
        except IndexError:
            return
        try:
            done = ev.done
        except Exception:
            done = True
        if not done:
            _GRAVE.append((ev, arr))


def _wall_art(screen: str):
    """output/<screen>.jpg when it is the current art (newest of jpg / JPG / mp4) and a JPEG, else None."""
    base = output_dir()
    best, path = -1.0, None
    for ext in ("jpg", "JPG", "mp4"):
        p = base / f"{screen}.{ext}"
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best:
            best, path = m, p
    if path is None or path.suffix == ".mp4":
        return None
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"\xff\xd8":
                return None      # PNG bytes under a .jpg name (a legacy publish): too slow to open on
    except OSError:
        return None
    return str(path)


# ----------------------------------------------------------------------------- kernels
_SRC = HELPERS + r"""
__device__ __forceinline__ float3 ld3(const unsigned char* __restrict__ s, int x, int y, int sw, int sh) {
    x = min(max(x, 0), sw - 1); y = min(max(y, 0), sh - 1);
    const unsigned char* p = s + 3 * (y * sw + x);
    return make_float3(p[0], p[1], p[2]);
}
__device__ __forceinline__ float3 samp3(const unsigned char* __restrict__ s, int sw, int sh,
                                        float inv_s, float cx, float cy, int x, int y, int W, int H) {
    const float fx = cx + ((float)x + 0.5f - 0.5f * W) * inv_s - 0.5f;
    const float fy = cy + ((float)y + 0.5f - 0.5f * H) * inv_s - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const float3 a = ld3(s, x0, y0, sw, sh), b = ld3(s, x0 + 1, y0, sw, sh);
    const float3 c = ld3(s, x0, y0 + 1, sw, sh), d = ld3(s, x0 + 1, y0 + 1, sw, sh);
    return make_float3(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z));
}
extern "C" __global__
void gallery_draw(const unsigned char* __restrict__ src, const int sw, const int sh,
                  uchar4* __restrict__ out, const int W, const int H,
                  const float inv_s, const float cx, const float cy,
                  const float tr, const float tg, const float tb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float3 c = samp3(src, sw, sh, inv_s, cx, cy, x, y, W, H);
    out[y * W + x] = pack(make_float4(c.x * tr, c.y * tg, c.z * tb, 255.f));
}
extern "C" __global__
void gallery_mix(const unsigned char* __restrict__ sa, const int aw, const int ah,
                 const float ainv, const float acx, const float acy,
                 const unsigned char* __restrict__ sb, const int bw, const int bh,
                 const float binv, const float bcx, const float bcy,
                 uchar4* __restrict__ out, const int W, const int H, const float k,
                 const float tr, const float tg, const float tb)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float3 a = samp3(sa, aw, ah, ainv, acx, acy, x, y, W, H);
    const float3 b = samp3(sb, bw, bh, binv, bcx, bcy, x, y, W, H);
    const float q = 1.f - k;
    out[y * W + x] = pack(make_float4((q * a.x + k * b.x) * tr, (q * a.y + k * b.y) * tg,
                                      (q * a.z + k * b.z) * tb, 255.f));
}
"""

# hosted transitions per style: (key, plug-in, params); "dissolve" = the in-kernel mix, "cut" = a plain cut
_STYLES = {
    "soft": [("luma", "luma", {"softness": 0.35}), ("melt", "melt", {"spread": 0.3, "roughness": 0.2}),
             ("dissolve", None, {})],
    "lively": [("wipe-line", "wipe", {"shape": "linear", "softness": 0.08}),
               ("wipe-iris", "wipe", {"shape": "iris", "softness": 0.06}),
               ("pixelate", "pixelate", {"max_block": 0.04, "steps": 10}),
               ("luma", "luma", {"softness": 0.25}), ("melt", "melt", {"spread": 0.25}),
               ("glitch", "glitch", {"intensity": 0.6, "slices": 24, "duration_s": 0.9}),
               ("dissolve", None, {})],
    "rush": [("pixelate", "pixelate", {"max_block": 0.05, "steps": 6}),
             ("glitch", "glitch", {"intensity": 0.85, "slices": 32, "duration_s": 0.3, "rate_hz": 30.0}),
             ("pixelate-fine", "pixelate", {"max_block": 0.02, "steps": 4})],
}
_MODULES = {"cut": "cut", "melt": "melt", "luma": "luma", "wipe": "wipe", "pixelate": "pixelate",
            "glitch": "glitch"}
_CLASSES = {"cut": "Cut", "melt": "Melt", "luma": "Luma", "wipe": "Wipe", "pixelate": "Pixelate",
            "glitch": "Glitch"}


def _transition_class(plugin: str):
    import importlib
    mod = importlib.import_module(f"stream_service.plugins.transitions.{_MODULES[plugin]}")
    return getattr(mod, _CLASSES[plugin])


def _hash(a: int, b: int) -> int:
    x = (a * 0x9E3779B1 ^ (b + 0x7F4A7C15) * 0x85EBCA77) & 0xFFFFFFFF
    x ^= x >> 15
    x = (x * 0x2C1B3C6D) & 0xFFFFFFFF
    return x ^ (x >> 12)


def _rgb(hexs: str) -> tuple[float, float, float]:
    try:
        v = hexs.lstrip("#")
        return int(v[0:2], 16) / 255.0, int(v[2:4], 16) / 255.0, int(v[4:6], 16) / 255.0
    except (ValueError, AttributeError, IndexError):
        return 1.0, 1.0, 1.0


EMPTY, LOADING, UPLOADING, READY_S, SHOWN = range(5)


class _Slot:
    __slots__ = ("arr", "state", "ticket", "event", "w", "h", "name", "phase", "ang", "off", "push_in", "t_fail")

    def __init__(self, arr, event):
        self.arr, self.event = arr, event
        self.state, self.ticket = EMPTY, None
        self.w = self.h = 0
        self.name = ""
        self.phase = self.ang = self.off = 0.0
        self.push_in = True
        self.t_fail = -1e9


class Gallery(Source):
    name = "gallery"
    label = "Gallery"
    description = "The screen's own pictures, drifting and turning on the music's phrases, bars or beats."
    tags = {"family": "light", "dims": "2d", "technique": ["projection"], "moods": ["elegant", "dreamy"]}
    params = {
        "pace": enum(["phrase", "bar", "beat"], "phrase", "Changes on"),
        "pool": enum(["favourites", "mixed", "fresh"], "favourites", "Pictures"),
        "style": enum(["soft", "lively", "rush"], "soft", "Transitions"),
        "quality": enum(["full", "draft"], "full", "Decode"),
        "open_on_art": {**flag(False, "Open on the wall's art"), "x-tier": "structural"},
        "hold_s": num(1.0, 60.0, 16.0, "Hold without a beat (calm)", unit="s"),
        "hold_fast_s": num(0.3, 30.0, 6.0, "Hold without a beat (busy)", unit="s"),
        "transition_s": num(0.0, 4.0, 3.0, "Transition (calm)", unit="s"),
        "transition_fast_s": num(0.0, 4.0, 2.0, "Transition (busy)", unit="s"),
        "drift": num(0.0, 1.0, 0.5, "Drift", basic=True),
        "drift_rate": num(0.1, 8.0, 1.0, "Drift rate", unit="x"),
        "tint": {"type": "string", "format": "color", "default": "#ffffff", "x-tier": "regional",
                 "x-ui": {"label": "Tint", "widget": "color", "basic": True}},
        **standard_scene_params(palette=None, render_scale=1.0),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self.ok = False
        self.slots: list[_Slot] = []
        self.work: list = []
        self.trans: dict = {}
        self.benched: set = set()
        self.cur = None
        self.nxt = None
        self.ready: deque = deque()
        self.tp = 0.0
        self.td = 0.0
        self.tkey = ""
        self.timer_f = 0.0
        self.n_changes = 0
        self.ckey = None
        self.mute_b = -1e18
        self.last_line = None
        self.rung = None
        self.last_now = None
        self.tint = list(_rgb(params.get("tint", "#ffffff")))
        self.err_logged = False
        self.params0 = dict(params)
        self.rng = random.Random(int(params.get("seed", 1)) * 7919 + id(self) % 1000)
        screen = getattr(ctx, "screen", None)
        tvs = screens()
        self.screen = screen if screen in tvs else (tvs[0] if tvs else "north-screen")
        self.dec = None
        self._drift = 0.5
        _purge_grave()
        try:
            self._setup(ctx, params)
            self.ok = True
        except Exception as e:
            for s in self.slots:
                if s.ticket is not None:
                    s.ticket.abandoned = True
            self.slots, self.work = [], []
            log.warning("gallery %s: setup degraded to black (%s: %s)", self.screen, type(e).__name__, e)

    # ------------------------------------------------------------------ setup
    def _setup(self, ctx, params: dict) -> None:
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        self.deck = _deck(self.screen)
        self.dec = _decoder(self.screen, cp)
        draft = params.get("quality") == "draft"
        nw = max(W, int(math.ceil(H * 16.0 / 9.0)))
        cover = (nw, int(math.ceil(nw * 9.0 / 16.0)))

        def box(cap):
            return min(cap[0], cover[0]), min(cap[1], cover[1])

        work_b = 2 * W * H * 4
        with ctx.device:
            try:
                free = cp.cuda.runtime.memGetInfo()[0] + cp.get_default_memory_pool().free_bytes()
            except Exception:
                free = 0
        b4k, b1080 = box((BLOCK_W, BLOCK_H)), box((DRAFT_W, DRAFT_H))
        need4k = 3 * b4k[0] * b4k[1] * 3 + work_b
        want = b4k
        if draft:
            want = b1080
        elif ctx.gpu == 0 and free < 2 * need4k + GPU0_AMPLE:
            want = b1080
        elif free < need4k + MARGIN:
            want = b1080
        plans = [(want, True), (b1080, True), (b1080, False), (box((960, 540)), False)]
        seen = set()
        for bx, with_work in plans:
            if (bx, with_work) in seen:
                continue
            seen.add((bx, with_work))
            try:
                with ctx.device:
                    slots = [_Slot(cp.empty(bx[0] * bx[1] * 3, cp.uint8),
                                   cp.cuda.Event(block=False, disable_timing=True)) for _ in range(3)]
                    work = [ctx.new_frame() for _ in range(2)] if with_work else []
                self.slots, self.work, self.box = slots, work, bx
                break
            except Exception as e:
                slots = work = None
                log.info("gallery %s: allocation at %s%s failed (%s); stepping down", self.screen, bx,
                         "" if with_work else " (no work frames)", type(e).__name__)
        if not self.slots:
            raise RuntimeError("no GPU memory for the picture slots")
        mod = raw_module(_SRC)
        self.k_draw = mod.get_function("gallery_draw")
        self.k_mix = mod.get_function("gallery_mix")
        with ctx.device:
            self.up = cp.cuda.Stream(non_blocking=True)
        self.style = params.get("style", "soft")
        seed = int(params.get("seed", 1))
        if self.work:
            for i, (key, plugin, tp) in enumerate(_STYLES.get(self.style, _STYLES["soft"])):
                if plugin is None:
                    continue
                try:
                    cls = _transition_class(plugin)
                    q = dict(tp)
                    if "seed" in cls.param_props():
                        q["seed"] = (seed * 131 + i) % (2**31 - 1)
                    t = cls(cls.validate(q))
                    t.setup(ctx)
                    self.trans[key] = t
                except Exception as e:
                    log.warning("gallery: transition %s unavailable (%s)", key, type(e).__name__)
        # the opening pictures: the wall's art (if asked and it is the current art), then pool picks
        tickets = []
        if params.get("open_on_art"):
            art = _wall_art(self.screen)
            if art:
                tickets.append(self.dec.submit(_Ticket(self.box[0], self.box[1], path=art)))
        while len(tickets) < 2:
            tickets.append(self.dec.submit(self._pick_ticket()))
        for s, t in zip(self.slots, tickets):
            s.state, s.ticket = LOADING, t
        deadline = time.monotonic() + SETUP_WAIT_S
        for s in self.slots[:2]:
            s.ticket.done.wait(max(0.0, deadline - time.monotonic()))
            if s.ticket.state == FAILED and s.ticket.path:   # the wall art failed: a pool pick instead
                s.ticket = self.dec.submit(self._pick_ticket())
                s.ticket.done.wait(max(0.0, deadline - time.monotonic()))
            self._poll_slot(s, cp)
        try:
            self.up.synchronize()     # setup only: the opening uploads are in place before the first frame
        except Exception:
            pass
        for s in self.slots:
            self._poll_slot(s, cp)
            if s.state == LOADING:    # not decoded in time: never leave a block waiting on an unrendered scene
                s.ticket.abandoned = True
                s.state, s.ticket, s.t_fail = EMPTY, None, -1e9

    def _pick_ticket(self) -> _Ticket:
        return _Ticket(self.box[0], self.box[1], deck=self.deck, pool=self.params0.get("pool", "favourites"))

    # ------------------------------------------------------------------ slots
    def _poll_slot(self, s: _Slot, cp) -> None:
        if s.state == LOADING:
            t = s.ticket
            if t.state == READY:
                if self.dec.take(t, cp, s.arr, self.up, s.event):
                    s.state, s.w, s.h, s.name = UPLOADING, t.pw, t.ph, t.name
            elif t.state in (FAILED, DONE):
                s.state, s.ticket, s.t_fail = EMPTY, None, time.monotonic()
        if s.state == UPLOADING and s.event.done:
            s.state, s.ticket = READY_S, None
            s.phase, s.ang = 0.0, self.rng.uniform(0.0, 2 * math.pi)
            s.off, s.push_in = self.rng.uniform(-1.0, 1.0), (not s.name) or self.rng.random() < 0.7
            idx = self.slots.index(s)
            if self.cur is None:
                self.cur = idx
                s.state = SHOWN
            else:
                self.ready.append(idx)

    def _housekeep(self, cp, dt: float) -> None:
        now = time.monotonic()
        for s in self.slots:
            self._poll_slot(s, cp)
            if s.state == EMPTY and dt > 0.0 and now - s.t_fail >= RETRY_S:
                s.ticket = self.dec.submit(self._pick_ticket())
                s.state = LOADING

    def _release(self, idx: int) -> None:
        s = self.slots[idx]
        s.state, s.ticket, s.name = EMPTY, None, ""
        s.t_fail = -1e9

    # ------------------------------------------------------------------ timing
    def _grid(self):
        m = self.modulation
        rt = getattr(m, "runtime", None) if m is not None else None
        if rt is None:
            return None
        feed = getattr(rt, "feed", None)
        now = float(getattr(rt, "now", 0.0) or 0.0)
        if feed is None or now <= 0.0:
            return None
        sm = getattr(rt, "sampler", None)
        if sm is not None and getattr(sm, "follow", "extrapolate") != "extrapolate":
            return None
        c = feed.clock(now)
        if not c or c.get("gate") != "beat" or c.get("state") not in ("locked", "holding"):
            return None
        try:
            bpm = float(c.get("bpm") or 0.0)
            bpb = max(1, int(c.get("beats_per_bar") or 4))
            pb = max(1, int(c.get("phrase_bars") or 4))
            bar0 = float(c.get("bar_anchor_beat") or 0.0)
            lead = float(getattr(sm, "lead", 0.0) or 0.0) if sm is not None else 0.0
            from stream_service import music_feed
            b = float(music_feed.beat_at(c, now + lead))
        except (TypeError, ValueError, KeyError):
            return None
        if not (math.isfinite(bpm) and bpm > 0.0 and math.isfinite(b)):
            return None
        return (c.get("epoch"), bar0, bpb, pb), b, bpm, bpb, pb, bar0

    def _real_dt(self, dt: float) -> float:
        m = self.modulation
        rt = getattr(m, "runtime", None) if m is not None else None
        now = float(getattr(rt, "now", 0.0) or 0.0) if rt is not None else 0.0
        if now <= 0.0:
            return dt
        last, self.last_now = self.last_now, now
        if last is None:
            return 0.0
        return min(0.5, max(0.0, now - last))

    def _ladder_energy(self, p: dict) -> float:
        m = self.modulation
        rt = getattr(m, "runtime", None) if m is not None else None
        ex = getattr(rt, "extra", None) if rt is not None else None
        v = ex.get("energy_slow") if isinstance(ex, dict) else None
        if isinstance(v, (int, float)) and math.isfinite(v):
            return min(1.0, max(0.0, float(v)))
        return min(1.0, max(0.0, float(p.get("energy", 0.5))))

    def _unit(self, pace: str, e: float, bpb: int, pb: int) -> int:
        """The line spacing in beats for this pace and energy (a ladder with hysteresis)."""
        if pace == "phrase":
            return bpb * pb
        r = self.rung
        if pace == "bar":
            lo, hi = LADDER_BAR[0][0], LADDER_BAR[1][0]
            if r not in (4, 2, 1):
                r = 4 if e < lo else (2 if e < hi else 1)
            elif r == 4:
                r = 1 if e >= hi + HYST else (2 if e >= lo + HYST else 4)
            elif r == 2:
                r = 1 if e >= hi + HYST else (4 if e < lo - HYST else 2)
            else:
                r = 1 if e >= hi - HYST else (2 if e >= lo - HYST else 4)
            self.rung = r
            return r * bpb
        if r not in (2, 1):
            r = 1 if e >= BEAT_PEAK else 2
        elif r == 2 and e >= BEAT_PEAK + HYST:
            r = 1
        elif r == 1 and e < BEAT_PEAK - HYST:
            r = 2
        self.rung = r
        return r

    def _duration(self, p: dict, e: float) -> float:
        return max(0.0, p["transition_s"] + (p["transition_fast_s"] - p["transition_s"]) * e)

    def _pick_transition(self, k: int, e: float) -> str:
        if not self.work:
            return "dissolve" if self.style != "rush" else "cut"
        if self.style == "rush" and e >= RUSH_CUT:
            return "cut"
        keys = [key for key, plugin, _ in _STYLES.get(self.style, _STYLES["soft"])
                if key not in self.benched and (plugin is None or key in self.trans)]
        if not keys:
            return "dissolve"
        return keys[_hash(int(self.params0.get("seed", 1)), k) % len(keys)]

    def _start(self, d: float, p0: float, k: int, e: float) -> None:
        self.nxt = self.ready.popleft()
        self.slots[self.nxt].state = SHOWN
        self.tkey = self._pick_transition(k, e)
        self.td = d if self.tkey != "cut" else 0.0
        self.tp = min(1.0, max(0.0, p0)) if self.td > 0 else 1.0
        self.timer_f = 0.0
        self.n_changes += 1

    def _finish(self) -> None:
        old, self.cur, self.nxt = self.cur, self.nxt, None
        if old is not None:
            self._release(old)
        self.tp, self.td = 0.0, 0.0

    def _schedule(self, p: dict, dt: float, dts: float) -> None:
        pace = self.params0.get("pace", "phrase")
        e = self._ladder_energy(p)
        hold = max(0.2, p["hold_s"] + (p["hold_fast_s"] - p["hold_s"]) * e)
        self.timer_f += dt / hold
        if dt <= 0.0:
            return                     # the prime render and first frames: never a change
        g = self._grid()
        if g is None:
            if self.nxt is None and self.ready and self.timer_f >= 1.0:
                self._start(min(self._duration(p, e), 0.8 * hold), 0.0, self.n_changes, e)
            return
        key, b, bpm, bpb, pb, bar0 = g
        if key != self.ckey:
            if self.ckey is not None:
                self.mute_b = math.floor(b) + 1.0      # a re-anchor: no change until the next whole beat
            self.ckey = key
        unit = self._unit(pace, e, bpb, pb)
        if self.nxt is not None or not self.ready or b < self.mute_b:
            return
        spb = 60.0 / bpm
        line_k = math.floor((b - bar0) / unit) + 1
        line_b = bar0 + line_k * unit
        if self.last_line == (key, line_b):
            return
        lead_s = (line_b - b) * spb
        d = min(self._duration(p, e), 0.8 * unit * spb)
        frame = max(dts, dt, 1.0 / max(1, self.ctx.fps)) * 1.5
        if lead_s <= max(0.5 * d, frame):
            self.last_line = (key, line_b)
            self._start(d, 0.5 - lead_s / d if d > 0 else 1.0, int(line_b), e)

    # ------------------------------------------------------------------ drawing
    def _xf(self, s: _Slot, drift: float):
        W, H = self.ctx.width, self.ctx.height
        sw, sh = max(1, s.w), max(1, s.h)
        base = max(W / sw, H / sh)
        u = 0.5 - 0.5 * math.cos(s.phase)
        if not s.push_in:
            u = 1.0 - u
        z = 1.0 + (0.03 + 0.17 * drift) * u
        inv = 1.0 / (base * z)
        hx = max(0.0, 0.5 * sw - 0.5 * W * inv)
        hy = max(0.0, 0.5 * sh - 0.5 * H * inv)
        w = math.sin(0.5 * s.phase + s.off)
        cx = 0.5 * sw + math.cos(s.ang) * w * hx * (0.4 + 0.6 * drift)
        cy = 0.5 * sh + math.sin(s.ang) * w * hy * (0.4 + 0.6 * drift)
        return np.float32(inv), np.float32(cx), np.float32(cy)

    def _draw(self, idx: int, dst, tint) -> None:
        s = self.slots[idx]
        inv, cx, cy = self._xf(s, self._drift)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self.k_draw, W, H, (s.arr, np.int32(s.w), np.int32(s.h), dst, np.int32(W), np.int32(H),
                                      inv, cx, cy, *tint))

    def _mix(self, a: int, b: int, k: float, out, tint) -> None:
        sa, sb = self.slots[a], self.slots[b]
        ia, xa, ya = self._xf(sa, self._drift)
        ib, xb, yb = self._xf(sb, self._drift)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self.k_mix, W, H, (sa.arr, np.int32(sa.w), np.int32(sa.h), ia, xa, ya,
                                     sb.arr, np.int32(sb.w), np.int32(sb.h), ib, xb, yb,
                                     out, np.int32(W), np.int32(H), np.float32(k), *tint))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        try:
            self._render(p, dt, out)
        except Exception as e:
            if not self.err_logged:
                log.warning("gallery %s: render error (%s: %s); holding", self.screen, type(e).__name__, e)
                self.err_logged = True
            try:
                if self.nxt is not None:
                    self._finish()
                if self.cur is not None:
                    self._draw(self.cur, out, self._tintv(p))
                else:
                    self.ctx.kernels.fill(out, (0, 0, 0))
            except Exception:
                pass

    def _tintv(self, p: dict):
        g = float(p.get("intensity", 1.0)) * (1.0 + PULSE_LIFT * min(1.0, max(0.0, float(p.get("pulse", 0.0)))))
        return tuple(np.float32(c * g) for c in self.tint)

    def _render(self, p: dict, dt: float, out) -> None:
        if not self.ok:
            self.ctx.kernels.fill(out, (0, 0, 0))
            return
        cp = self.ctx.cp
        self._housekeep(cp, dt)
        dts = self._real_dt(dt)
        e = min(1.0, max(0.0, float(p.get("energy", 0.5))))
        pulse = min(1.0, max(0.0, float(p.get("pulse", 0.0))))
        self._drift = min(1.0, max(0.0, float(p.get("drift", 0.5))))
        # tint eases (1 s)
        tgt = _rgb(p.get("tint", "#ffffff"))
        a = 1.0 - math.exp(-dts / 1.0) if dts > 0 else 0.0
        self.tint = [c + (t - c) * a for c, t in zip(self.tint, tgt)]
        tint = self._tintv(p)
        rate = float(p.get("drift_rate", 1.0)) * float(p.get("speed", 1.0)) * (0.10 + 0.30 * e) * (1.0 + 1.5 * pulse)
        for idx in (self.cur, self.nxt):
            if idx is not None:
                self.slots[idx].phase += rate * dt
        if self.cur is None:
            self.ctx.kernels.fill(out, (0, 0, 0))
            return
        self._schedule(p, dt, dts)
        if self.nxt is not None:
            if self.td > 0:
                self.tp += dts / self.td
            else:
                self.tp = 1.0
            if self.tp >= 1.0:
                self._finish()
        if self.nxt is None:
            self._draw(self.cur, out, tint)
            return
        k = self.tkey
        if k == "dissolve" or (k != "cut" and k not in self.trans):
            q = self.tp
            self._mix(self.cur, self.nxt, q * q * (3.0 - 2.0 * q), out, tint)
            return
        if k == "cut":
            self._draw(self.nxt, out, tint)
            return
        t = self.trans[k]
        w0, w1 = self.work
        self._draw(self.cur, w0, tint)
        self._draw(self.nxt, w1, tint)
        try:
            t.render(ease(t.easing, self.tp), w0, w1, out)
        except Exception as ex:
            log.warning("gallery: transition %s failed (%s); benched, cutting", k, type(ex).__name__)
            self.benched.add(k)
            self._finish()
            self._draw(self.cur, out, tint)

    def close(self) -> None:
        for s in self.slots:
            t = s.ticket
            if t is not None:
                t.abandoned = True
            if s.state == UPLOADING:  # a copy into this slot may still run: keep it alive until its event is done
                _GRAVE.append((s.event, s.arr))
        for t in self.trans.values():
            try:
                t.close()
            except Exception:
                pass
        self.trans = {}
        self.slots, self.work = [], []
        self.ok = False
        dec, self.dec = getattr(self, "dec", None), None
        if dec is not None:
            with _REG_LOCK:
                dec.users = max(0, dec.users - 1)
                dec.idle_since = time.monotonic()
