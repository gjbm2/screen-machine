"""The live input meter: ``WS /v1/levels`` (owner, 16 Oct: a moving level, near real time; built by 05).

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
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from typing import Any, Optional

import numpy as np
from aiohttp import WSMsgType, web

log = logging.getLogger("show_service.levels")

PATH = "/v1/levels"
MAX_HZ = 60.0
QUEUE_MAX = 64
FLOOR_DBFS = -120.0
FFT_N = 2048
SPEC_BANDS = 48
SPEC_LO_HZ, SPEC_HI_HZ = 40.0, 16000.0
RING_N = 4096


class LevelFeed:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._clients: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._slot: Optional[list] = None                # [stream, counter, peak, packets, t_cap_ns, arrival]
        self._ring = np.zeros(RING_N, np.float32)       # the newest mono samples (show-rx writes, the loop reads)
        self._ring_w = 0
        self._ring_fill = 0
        self._ring_stream: Optional[str] = None
        self._sr = 48000
        self._win = np.hanning(FFT_N).astype(np.float32)
        self._bins: Optional[list[tuple[int, int]]] = None
        self._last_flush = 0.0
        self._scheduled = False
        self.sent = 0
        self.dropped = 0

    # -- show-rx: store only ----------------------------------------------------------------------------------------
    def push(self, stream: str, counter: int, peak: float, t_cap_ns: Optional[float], arrival_s: float,
             pcm: Any = None, sr: Optional[int] = None) -> None:
        if not self._clients or self._loop is None:
            return
        with self._lock:
            if pcm is not None:                          # store the samples for the spectrum (no FFT here)
                m = pcm[:, 0] if getattr(pcm, "ndim", 1) == 2 else pcm
                if stream != self._ring_stream or (sr and sr != self._sr):
                    self._ring_stream, self._ring_fill, self._ring_w = stream, 0, 0
                    if sr:
                        self._sr, self._bins = int(sr), None
                k = min(len(m), RING_N)
                end = self._ring_w + k
                if end <= RING_N:
                    self._ring[self._ring_w:end] = m[-k:]
                else:
                    cut = RING_N - self._ring_w
                    self._ring[self._ring_w:] = m[-k:][:cut]
                    self._ring[:end - RING_N] = m[-k:][cut:]
                self._ring_w = end % RING_N
                self._ring_fill = min(RING_N, self._ring_fill + k)
            s = self._slot
            if s is None or s[0] != stream:
                self._slot = [stream, counter, peak, 1, t_cap_ns, arrival_s]
            else:
                s[1], s[2], s[3], s[4], s[5] = counter, max(s[2], peak), s[3] + 1, t_cap_ns, arrival_s
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
    def _band_bins(self) -> list[tuple[int, int]]:
        """Each band's FFT bin range [lo, hi) (at least one bin), log-spaced SPEC_LO_HZ..SPEC_HI_HZ."""
        if self._bins is None:
            edges = np.geomspace(SPEC_LO_HZ, min(SPEC_HI_HZ, self._sr / 2 * 0.98), SPEC_BANDS + 1)
            hz = self._sr / FFT_N
            out = []
            for lo, hi in zip(edges[:-1], edges[1:]):
                a, b = int(round(lo / hz)), int(round(hi / hz))
                out.append((a, max(a + 1, b)))
            self._bins = out
        return self._bins

    def _spectrum(self) -> Optional[list[int]]:
        """The newest FFT_N samples' spectrum in SPEC_BANDS bands, dBFS (bus loop; ~0.1 ms)."""
        with self._lock:
            if self._ring_fill < FFT_N:
                return None
            idx = (np.arange(self._ring_w - FFT_N, self._ring_w)) % RING_N
            x = self._ring[idx]
        mag = np.abs(np.fft.rfft(x * self._win)) / (FFT_N / 4.0)     # a full-scale sine's bin reads 1
        out = []
        for a, b in self._band_bins():
            v = float(mag[a:b].max()) if b > a else 0.0
            out.append(int(round(max(FLOOR_DBFS, 20.0 * math.log10(v)) if v > 1e-6 else FLOOR_DBFS)))
        return out

    def _flush(self) -> None:
        with self._lock:
            s, self._slot, self._scheduled = self._slot, None, False
        self._last_flush = time.monotonic()
        if s is None:
            return
        stream, counter, peak, n, t, a = s
        body: dict[str, Any] = {"s": stream, "c": counter, "n": n,
                                "pk": round(20.0 * math.log10(peak), 1) if peak > 1e-6 else FLOOR_DBFS,
                                "t": int(t) if t is not None and math.isfinite(t) else None, "a": round(a, 6)}
        spec = self._spectrum()
        if spec is not None:
            body["f"] = spec
            body["fl"], body["fh"] = SPEC_LO_HZ, round(min(SPEC_HI_HZ, self._sr / 2 * 0.98))
        frame = json.dumps(body, separators=(",", ":"))
        for q in self._clients:
            if q.full():
                self.dropped += 1
            else:
                q.put_nowait(frame)

    def routes(self, app: web.Application) -> None:
        app.router.add_get(PATH, self._ws)

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        self._loop = asyncio.get_running_loop()
        ws = web.WebSocketResponse(heartbeat=10.0)
        await ws.prepare(request)
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._clients.add(q)

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
            rd.cancel()
            await ws.close()
        return ws

    def snapshot(self) -> dict[str, Any]:
        return {"clients": len(self._clients), "sent": self.sent, "dropped": self.dropped}
