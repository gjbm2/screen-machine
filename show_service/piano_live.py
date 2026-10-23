"""The piano on a phone or iPad (owner, 23 Oct: "synth on PC22 and then stream to local device browser"): the Room
page's "This device" output plays PC22's own piano sound, streamed.

  GET  /api/v1/piano/live.mp3?instrument=<id>   chunked audio/mpeg, endless: whatever the room's pianos play (the
                                                Steinway's MIDI, test pieces, recordings: every on_midi stream),
                                                synthesised by piano_render.live with that instrument (the default
                                                one for none, an unknown one, or windows-gs, which can't stream)
  HEAD /api/v1/piano/live.mp3                   200 with the headers: it's here (the page probes before offering it)
  GET  /api/v1/piano/live/clock?instrument=     the stream's clock (clock()): how far behind a listener hears; the
                                                stream's own reply carries it too (X-Piano-Clock: its join position)

One synth process per instrument with listeners (sm-piano's python, nice 5, its own memory cap: stream.py's
prepare_process), started by the first and stopped LINGER_S after the last leaves; every listener of it shares its
one MP3 encode.  A listener joins mid-stream (MP3 frames resync).  The notes reach it from on_midi (show-rx thread,
non-blocking: a queue and a writer thread per synth) with their sound time, so the rhythm is the player's;
piano_render.live plays them DELAY_S later.  How far behind the screens a device hears it: that delay, the encode
(~50 ms) and the browser's own buffer (~1-2 s).  A listener that falls CLIENT_QUEUE chunks behind is dropped.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import pathlib
import queue
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("show_service.piano_live")

REPO = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(os.environ.get("PIANO_PYTHON", pathlib.Path.home() / ".venvs" / "sm-piano" / "bin" / "python"))
INSTRUMENTS = REPO / "config" / "piano" / "instruments.json"
LINGER_S = 10.0
CHUNK = 4096
CLIENT_QUEUE = 256                   # chunks (~64 s at 128 kb/s) a listener may fall behind before it's dropped
BITRATE = 128
MAX_SYNTHS = 2                       # instruments streaming at once (each ~0.3-1 GB, ~0.5 core)


def _instrument_ids() -> tuple[set, Optional[str]]:
    try:
        doc = json.loads(INSTRUMENTS.read_text())
    except (OSError, ValueError):
        return set(), None
    items = doc.get("instruments") or {}
    ids = set(items) if isinstance(items, dict) else {i.get("id") for i in items if isinstance(i, dict)}
    return {i for i in ids if i and i != "windows-gs"}, doc.get("default")


class _Synth:
    def __init__(self, instrument: str) -> None:
        self.instrument = instrument
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.clients: set = set()
        self.feed: "queue.SimpleQueue" = queue.SimpleQueue()
        self.ready = False
        self.stop_at: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.started = time.monotonic()
        self.t0: Optional[float] = None                  # host time of the stream's first audio frame
        self.delay_s = 0.25                              # piano_render.live's DELAY_S (its clock line says)
        self.bytes_out = 0                               # MP3 bytes made so far: the stream's position, CBR


class PianoLive:
    def __init__(self) -> None:
        self.synths: dict[str, _Synth] = {}
        self._feeds: tuple = ()                          # the synths' queues, read by on_midi (show-rx)
        self._reaper: Optional[asyncio.Task] = None

    # -- the notes (show-rx thread) ------------------------------------------------------------------------------
    def on_midi(self, stream: str, msg: Any, arrival_s: float, to_host: Callable[[int], float]) -> None:
        feeds = self._feeds
        if not feeds:
            return
        ev = getattr(msg, "ev", None)
        if not ev:
            return
        lines = []
        for t_ns, status, d1, d2 in ev:
            kind = status & 0xF0
            if kind not in (0x80, 0x90, 0xB0):
                continue
            t = to_host(t_ns)
            lines.append(json.dumps({"t": t if isinstance(t, float) and math.isfinite(t) else arrival_s,
                                     "s": status, "a": d1, "b": d2}) + "\n")
        if lines:
            blob = "".join(lines).encode()
            for q in feeds:
                q.put(blob)

    def _refeed(self) -> None:
        self._feeds = tuple(s.feed for s in self.synths.values())

    # -- the synths (the bus's loop) -------------------------------------------------------------------------------
    def _pick(self, asked: Optional[str]) -> Optional[str]:
        ids, default = _instrument_ids()
        if asked in ids:
            return asked
        return default if default in ids else (sorted(ids)[0] if ids else None)

    async def _start(self, s: _Synth) -> None:
        r, w = os.pipe()                                 # the notes' pipe: blocking, the writer thread's own
        try:
            s.proc = await asyncio.create_subprocess_exec(
                str(PYTHON), "-m", "piano_render.live", "--instrument", s.instrument, "--bitrate", str(BITRATE),
                cwd=str(REPO), stdin=r, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONPATH": str(REPO)}, start_new_session=True)
        except BaseException:
            os.close(w)
            raise
        finally:
            os.close(r)
        threading.Thread(target=self._writer, args=(s, w), daemon=True, name=f"live-feed-{s.instrument}").start()
        loop = asyncio.get_running_loop()
        loop.create_task(self._stderr(s))
        s.task = loop.create_task(self._pump(s))
        log.info("piano live: %s started for %d listener(s)", s.instrument, len(s.clients))

    def _writer(self, s: _Synth, fd: int) -> None:
        """The notes into the synth's stdin (its own thread: on_midi never waits on a pipe); None closes it."""
        try:
            with os.fdopen(fd, "wb", buffering=0) as f:
                while True:
                    try:
                        blob = s.feed.get(timeout=1.0)
                    except queue.Empty:
                        if s.proc is None or s.proc.returncode is not None:
                            return
                        continue
                    if blob is None:
                        return
                    f.write(blob)
        except (BrokenPipeError, OSError):
            pass

    async def _stderr(self, s: _Synth) -> None:
        proc = s.proc
        assert proc is not None and proc.stderr is not None
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line or line.startswith("[sfizz]") or line.startswith("Changing default sample path"):
                continue
            if line.startswith("{"):
                try:
                    m = json.loads(line)
                except ValueError:
                    m = {}
                if m.get("ready"):
                    s.ready = True
                    log.info("piano live: %s ready (load %.1f s, room %s)", s.instrument, m.get("load_s") or 0,
                             m.get("room"))
                    continue
                if isinstance(m.get("clock"), dict):
                    s.t0 = float(m["clock"].get("t0") or 0.0) or None
                    s.delay_s = float(m["clock"].get("delay_s") or s.delay_s)
                    continue
                if "stats" in m:
                    log.info("piano live: %s ended: %s", s.instrument, json.dumps(m["stats"])[:300])
                    continue
            log.warning("piano live %s: %s", s.instrument, line[:300])

    async def _pump(self, s: _Synth) -> None:
        """The MP3 out to every listener."""
        proc = s.proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                chunk = await proc.stdout.read(CHUNK)
                if not chunk:
                    break
                s.bytes_out += len(chunk)
                for q in list(s.clients):
                    if q.qsize() >= CLIENT_QUEUE:
                        s.clients.discard(q)
                        q.put_nowait(None)               # too far behind: that listener ends
                    else:
                        q.put_nowait(chunk)
        finally:
            for q in list(s.clients):
                q.put_nowait(None)
            s.clients.clear()
            if self.synths.get(s.instrument) is s:
                del self.synths[s.instrument]
                self._refeed()
            s.feed.put(None)
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            log.info("piano live: %s stopped", s.instrument)

    async def _stop(self, s: _Synth) -> None:
        if self.synths.get(s.instrument) is s:
            del self.synths[s.instrument]
            self._refeed()
        s.feed.put(None)                                 # piano_render.live ends when its input does
        proc = s.proc
        if proc is not None and proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()

    async def _reap(self) -> None:
        while self.synths:
            await asyncio.sleep(1.0)
            now = time.monotonic()
            for s in list(self.synths.values()):
                if s.clients:
                    s.stop_at = None
                elif s.stop_at is None:
                    s.stop_at = now + LINGER_S
                elif now >= s.stop_at:
                    await self._stop(s)

    # -- routes (show-bus) ---------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        app.router.add_get("/api/v1/piano/live.mp3", self._h_live)
        app.router.add_get("/api/v1/piano/live/clock", self._h_clock)

    def clock(self, s: _Synth, pos_s: Optional[float] = None) -> dict:
        """The stream's clock (owner, 23 Oct: "when playing from 'this device' we need to match latency with local
        score and/or pianola view because local device is quite a few seconds behind"): audio at stream second x
        carries the sound time t0 + x - delay_s (host CLOCK_MONOTONIC); host_now is that clock now; pos_s the
        stream second a listener joining now starts at. A listener whose audio element's currentTime is c (from its
        own start) hears sound time t0 + pos_s(at its join) + c - delay_s: it is host_now minus that behind."""
        return {"t0": s.t0, "delay_s": s.delay_s, "host_now": round(time.monotonic(), 4),
                "pos_s": round(pos_s if pos_s is not None else s.bytes_out * 8 / (BITRATE * 1000), 3),
                "instrument": s.instrument}

    async def _h_clock(self, request: Any) -> Any:
        from aiohttp import web
        s = self.synths.get(self._pick(request.query.get("instrument")) or "")
        if s is None:
            return web.json_response({"error": "not_streaming", "detail": "No piano sound is streaming."}, status=404)
        return web.json_response(self.clock(s), headers={"Cache-Control": "no-store"})

    async def _h_live(self, request: Any) -> Any:
        from aiohttp import web
        inst = self._pick(request.query.get("instrument"))
        if inst is None or not PYTHON.exists():
            return web.json_response({"error": "not_available", "detail": "There's no piano sound to stream."},
                                     status=404)
        headers = {"Content-Type": "audio/mpeg", "Cache-Control": "no-store", "X-Piano-Instrument": inst,
                   "Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "X-Piano-Instrument"}
        if request.method == "HEAD":
            return web.Response(headers=headers)
        s = self.synths.get(inst)
        if s is None:
            if len(self.synths) >= MAX_SYNTHS:
                return web.json_response({"error": "busy", "detail": "Two piano sounds are streaming already."},
                                         status=503)
            s = self.synths[inst] = _Synth(inst)
            self._refeed()
            await self._start(s)
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.get_running_loop().create_task(self._reap())
        q: asyncio.Queue = asyncio.Queue()
        s.clients.add(q)
        # where in the stream this listener starts (its audio element's currentTime 0), and the stream's clock
        headers = {**headers, "X-Piano-Clock": json.dumps(self.clock(s)),
                   "Access-Control-Expose-Headers": "X-Piano-Instrument, X-Piano-Clock"}
        resp = web.StreamResponse(status=200, headers=headers)
        resp.enable_chunked_encoding()
        try:
            await resp.prepare(request)
            while True:
                chunk = await q.get()
                if chunk is None:
                    break
                await resp.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            s.clients.discard(q)
        return resp
