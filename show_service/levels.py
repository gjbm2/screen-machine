"""The live input meter: ``WS /v1/levels`` (owner, 16 Oct: a moving level, near real time; built by screen-machine-d4).

The receive path only stores: :meth:`push` (show-rx, per audio packet of the active stream) folds the packet's peak
into a slot. A frame goes out as soon as 1/MAX_HZ has passed since the last one (so at most ~60 a second, one per
display refresh, as c8 asked to spare room_service's pass-through), carrying the loudest packet since the last frame:

    {"s": "pc22-mic", "c": 15566, "pk": -23.4, "n": 6, "t": 8063918580729833, "a": 1234.567891}

``pk``: the peak in dBFS over the ``n`` packets folded in (-120 floor). ``f``: the spectrum now, raw (no gate, no
smoothing, no normalisation: a monitor, unlike music.features' spectrum32): a 2048-point Hann FFT (43 ms at 48 kHz)
of the newest samples, in SPEC_BANDS log-spaced bands from SPEC_LO_HZ to SPEC_HI_HZ, each its loudest bin in dBFS
(a full-scale sine reads 0), rounded, floored at -120; ``fl``/``fh`` give the edges once per connection's frames. ``t``: the newest packet's first-sample
capture time on the SENDER's clock in ns (the heartbeats' capture map; null until it has a fit), so a client on the
sender's own clock (PC22: QPC = Python's perf_counter_ns) measures capture -> client exactly. ``a``: show_service's
arrival time of that packet (time.monotonic). Pages reach it through room_service's pass-through at /show/v1/levels.
Nothing is sent while no client is connected. A client may send ``{"ping": x}`` (any number of its own clock); the
reply ``{"pong": x, "srv": <time.monotonic()>}`` lets it align clocks and show how far behind the room each sample
reaches it (``a`` is on the same clock as ``srv``).

Opt-in waveform (18 Oct, for the renderer's scope effects: stream_service/scope_feed.py): a client that connects
with ``?wave=N`` (an int, clamped to WAVE_MIN..WAVE_MAX) also gets ``"wb"`` in every frame: the newest N mono
samples of the ring, oldest first, as little-endian int16 (scaled to +-32767, clipped; samples the ring does not hold
yet read 0), base64-encoded.  Each distinct N is built once per flush, however many clients ask for it, and is
spliced onto the frame already serialised (no second json.dumps; 3a, 18 Oct: a JSON int list cost ~11 % of the bus
loop).  Clients without ``wave`` get exactly the frames they always did.

A named input (23 Oct, 1c's latency calibration: the drawing-room mic while the active input is PC22's): a client
that connects with ``?stream=<name>`` gets that stream's frames (its own peak, spectrum and ``wave``) instead of the
active stream's. While anyone watches a name, show-rx also pushes that stream's packets here (``on_watch``: the
receiver's ``set_level_watch``); the active stream, and everything that listens to it, is unchanged. A watched
stream that isn't sending (off, or not subscribed) simply sends no frames. Clients without ``stream`` get exactly
the frames they always did.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import pathlib
import threading
import time
from typing import Any, Optional

import numpy as np
from aiohttp import WSMsgType, web

log = logging.getLogger("show_service.levels")

PATH = "/v1/levels"
MONITOR_PATH = "/dev/dsp"                             # the DSP monitor page (static/dsp_monitor.html; d4, 16 Oct)
MONITOR_FILE = pathlib.Path(__file__).resolve().parent / "static" / "dsp_monitor.html"
MAX_HZ = 60.0
QUEUE_MAX = 64
FLOOR_DBFS = -120.0
FFT_N = 2048
SPEC_BANDS = 48
SPEC_LO_HZ, SPEC_HI_HZ = 40.0, 16000.0
RING_N = 4096
WAVE_MIN, WAVE_MAX = 64, 2048                         # ?wave=N clamp (WAVE_MAX <= RING_N)


class _Tap:
    """One feed's state: the active stream's (key None) or a named stream's. Written on show-rx under the feed's
    lock, read on the bus loop."""

    def __init__(self) -> None:
        self.slot: Optional[list] = None                 # [stream, counter, peak, packets, t_cap_ns, arrival]
        self.ring = np.zeros(RING_N, np.float32)        # the newest mono samples
        self.ring_w = 0
        self.ring_fill = 0
        self.ring_stream: Optional[str] = None
        self.sr = 48000
        self.bins: Optional[list[tuple[int, int]]] = None

    def store(self, stream: str, counter: int, peak: float, t_cap_ns: Optional[float], arrival_s: float,
              pcm: Any, sr: Optional[int]) -> None:
        if pcm is not None:                              # store the samples for the spectrum (no FFT here)
            m = pcm[:, 0] if getattr(pcm, "ndim", 1) == 2 else pcm
            if stream != self.ring_stream or (sr and sr != self.sr):
                self.ring_stream, self.ring_fill, self.ring_w = stream, 0, 0
                if sr:
                    self.sr, self.bins = int(sr), None
            k = min(len(m), RING_N)
            end = self.ring_w + k
            if end <= RING_N:
                self.ring[self.ring_w:end] = m[-k:]
            else:
                cut = RING_N - self.ring_w
                self.ring[self.ring_w:] = m[-k:][:cut]
                self.ring[:end - RING_N] = m[-k:][cut:]
            self.ring_w = end % RING_N
            self.ring_fill = min(RING_N, self.ring_fill + k)
        s = self.slot
        if s is None or s[0] != stream:
            self.slot = [stream, counter, peak, 1, t_cap_ns, arrival_s]
        else:
            s[1], s[2], s[3], s[4], s[5] = counter, max(s[2], peak), s[3] + 1, t_cap_ns, arrival_s


class LevelFeed:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._clients: set[asyncio.Queue] = set()
        self._wave: dict[asyncio.Queue, int] = {}        # clients that asked for ?wave=N -> N
        self._key: dict[asyncio.Queue, Optional[str]] = {}   # client -> its tap (None: the active stream)
        self._lock = threading.Lock()
        self._taps: dict[Optional[str], _Tap] = {None: _Tap()}
        self._win = np.hanning(FFT_N).astype(np.float32)
        self._last_flush = 0.0
        self._scheduled = False
        self.on_watch: Optional[Any] = None              # receiver.set_level_watch(frozenset): __main__ wires it
        self.watched: frozenset = frozenset()            # the named streams someone watches (?stream=)
        self.sent = 0
        self.dropped = 0

    # back-compat for readers of the active tap (tests, the monitor page's code)
    @property
    def _ring(self) -> np.ndarray:
        return self._taps[None].ring

    @property
    def _ring_fill(self) -> int:
        return self._taps[None].ring_fill

    @property
    def _ring_w(self) -> int:
        return self._taps[None].ring_w

    @property
    def _sr(self) -> int:
        return self._taps[None].sr

    # -- show-rx: store only ----------------------------------------------------------------------------------------
    def push(self, stream: str, counter: int, peak: float, t_cap_ns: Optional[float], arrival_s: float,
             pcm: Any = None, sr: Optional[int] = None, active: bool = True) -> None:
        """One live packet (show-rx): the active stream's (``active``) to the default tap, and a watched stream's
        to its own tap (both, for an active stream someone also names)."""
        if not self._clients or self._loop is None:
            return
        with self._lock:
            if active:
                self._taps[None].store(stream, counter, peak, t_cap_ns, arrival_s, pcm, sr)
            tap = self._taps.get(stream) if stream in self.watched else None
            if tap is not None:
                tap.store(stream, counter, peak, t_cap_ns, arrival_s, pcm, sr)
            if self._scheduled:
                return
            self._scheduled = True
        wait = self._last_flush + 1.0 / MAX_HZ - time.monotonic()
        try:
            if wait <= 0:
                self._loop.call_soon_threadsafe(self._flush)
            else:
                self._loop.call_soon_threadsafe(self._loop.call_later, wait, self._flush)
        except RuntimeError:                              # the loop closed (shutting down)
            pass

    # -- bus loop ---------------------------------------------------------------------------------------------------
    @staticmethod
    def _band_bins(tap: _Tap) -> list[tuple[int, int]]:
        """Each band's FFT bin range [lo, hi) (at least one bin), log-spaced SPEC_LO_HZ..SPEC_HI_HZ."""
        if tap.bins is None:
            edges = np.geomspace(SPEC_LO_HZ, min(SPEC_HI_HZ, tap.sr / 2 * 0.98), SPEC_BANDS + 1)
            hz = tap.sr / FFT_N
            out = []
            for lo, hi in zip(edges[:-1], edges[1:]):
                a, b = int(round(lo / hz)), int(round(hi / hz))
                out.append((a, max(a + 1, b)))
            tap.bins = out
        return tap.bins

    def _spectrum(self, tap: Optional[_Tap] = None) -> Optional[list[int]]:
        """The newest FFT_N samples' spectrum in SPEC_BANDS bands, dBFS (bus loop; ~0.1 ms)."""
        tap = tap if tap is not None else self._taps[None]
        with self._lock:
            if tap.ring_fill < FFT_N:
                return None
            idx = (np.arange(tap.ring_w - FFT_N, tap.ring_w)) % RING_N
            x = tap.ring[idx]
        mag = np.abs(np.fft.rfft(x * self._win)) / (FFT_N / 4.0)     # a full-scale sine's bin reads 1
        out = []
        for a, b in self._band_bins(tap):
            v = float(mag[a:b].max()) if b > a else 0.0
            out.append(int(round(max(FLOOR_DBFS, 20.0 * math.log10(v)) if v > 1e-6 else FLOOR_DBFS)))
        return out

    def _wave_b64(self, n: int, tap: Optional[_Tap] = None) -> str:
        """The newest ``n`` ring samples, oldest first, as base64 little-endian int16 scaled to +-32767 (zeros where
        the ring is empty)."""
        tap = tap if tap is not None else self._taps[None]
        with self._lock:
            fill, w = min(tap.ring_fill, n), tap.ring_w
            x = np.zeros(n, np.float32)
            if fill:
                idx = np.arange(w - fill, w) % RING_N
                x[n - fill:] = tap.ring[idx]
        np.multiply(x, 32767.0, out=x)
        np.clip(x, -32767.0, 32767.0, out=x)
        return base64.b64encode(np.rint(x).astype("<i2").tobytes()).decode("ascii")

    @staticmethod
    def wave_param(raw: Optional[str]) -> Optional[int]:
        """``?wave=N`` -> N clamped to WAVE_MIN..WAVE_MAX, or None (absent or not an int)."""
        if raw is None:
            return None
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return None
        return max(WAVE_MIN, min(WAVE_MAX, n))

    def _flush(self) -> None:
        with self._lock:
            slots = {k: tap.slot for k, tap in self._taps.items() if tap.slot is not None}
            for tap in self._taps.values():
                tap.slot = None
            self._scheduled = False
        self._last_flush = time.monotonic()
        for key, s in slots.items():
            tap = self._taps.get(key)
            if tap is None:
                continue
            stream, counter, peak, n, t, a = s
            body: dict[str, Any] = {"s": stream, "c": counter, "n": n,
                                    "pk": round(20.0 * math.log10(peak), 1) if peak > 1e-6 else FLOOR_DBFS,
                                    "t": int(t) if t is not None and math.isfinite(t) else None, "a": round(a, 6)}
            spec = self._spectrum(tap)
            if spec is not None:
                body["f"] = spec
                body["fl"], body["fh"] = SPEC_LO_HZ, round(min(SPEC_HI_HZ, tap.sr / 2 * 0.98))
            frame = json.dumps(body, separators=(",", ":"))
            mine = [q for q in self._clients if self._key.get(q) == key]
            waved: dict[int, str] = {}
            for wn in {self._wave[q] for q in mine if q in self._wave}:   # each distinct N once per flush, spliced
                waved[wn] = f'{frame[:-1]},"wb":"{self._wave_b64(wn, tap)}"}}'
            for q in mine:
                if q.full():
                    self.dropped += 1
                else:
                    wn = self._wave.get(q)
                    q.put_nowait(frame if wn is None else waved.get(wn, frame))

    @staticmethod
    def stream_param(raw: Optional[str]) -> Optional[str]:
        """``?stream=<name>`` -> the name (1-16 printable characters, VBAN's limit), or None."""
        if raw is None:
            return None
        name = raw.strip()
        return name if 0 < len(name) <= 16 and name.isprintable() else None

    def _rewatch(self) -> None:
        """The watched names changed (bus loop): taps for them, and the receiver told (swapped by reference)."""
        names = frozenset(k for k in self._key.values() if k is not None)
        if names == self.watched:
            return
        with self._lock:
            for k in names:
                self._taps.setdefault(k, _Tap())
            for k in [k for k in self._taps if k is not None and k not in names]:
                del self._taps[k]
            self.watched = names
        fn = self.on_watch
        if fn is not None:
            try:
                fn(names)
            except Exception:                             # noqa: BLE001
                log.exception("levels: the receiver didn't take the watched streams")

    def routes(self, app: web.Application) -> None:
        app.router.add_get(PATH, self._ws)
        app.router.add_get(MONITOR_PATH, self._monitor)

    async def _monitor(self, request: web.Request) -> web.Response:
        """The DSP monitor page (static/dsp_monitor.html): read on each request, so an edit needs no restart."""
        try:
            body = MONITOR_FILE.read_text(encoding="utf-8")
        except OSError as e:
            return web.Response(status=500, text=f"dsp monitor page missing: {e}")
        return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        self._loop = asyncio.get_running_loop()
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        n_wave = self.wave_param(request.query.get("wave"))
        if n_wave is not None:
            self._wave[q] = n_wave
        self._key[q] = self.stream_param(request.query.get("stream"))
        self._clients.add(q)
        self._rewatch()

        async def reader() -> None:                       # pings (clock alignment); notice the close
            async for msg in ws:
                if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
                if msg.type == WSMsgType.TEXT:
                    try:
                        x = json.loads(msg.data).get("ping")
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(x, (int, float)) and not q.full():
                        q.put_nowait(json.dumps({"pong": x, "srv": time.monotonic()}))

        rd = asyncio.ensure_future(reader())
        try:
            while not ws.closed and not rd.done():
                get = asyncio.ensure_future(q.get())
                done, _ = await asyncio.wait({get, rd}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    break
                await ws.send_str(get.result())
                self.sent += 1
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._clients.discard(q)
            self._wave.pop(q, None)
            self._key.pop(q, None)
            self._rewatch()
            rd.cancel()
            await ws.close()
        return ws

    def snapshot(self) -> dict[str, Any]:
        return {"clients": len(self._clients), "sent": self.sent, "dropped": self.dropped,
                "watched": sorted(self.watched)}
