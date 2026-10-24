"""The piano on a phone or iPad (owner, 23 Oct: "synth on PC22 and then stream to local device browser"): the Room
page's "This device" output plays PC22's own piano sound, streamed.

  GET  /api/v1/piano/live.mp3?instrument=<id>   chunked audio/mpeg, endless: whatever the room's pianos play (the
                                                Steinway's MIDI, test pieces, recordings: every on_midi stream),
                                                synthesised by piano_render.live with that instrument (the default
                                                one for none, an unknown one, or windows-gs, which can't stream)
  HEAD /api/v1/piano/live.mp3                   200 with the headers: it's here (the page probes before offering it)
  GET  /api/v1/piano/live/clock?instrument=     the stream's clock (clock()): how far behind a listener hears; the
                                                stream's own reply carries it too (X-Piano-Clock: its join position)
  GET  /api/v1/piano/devices                    {"devices": [{"id": "device:<id>", "label", "streaming", "seen_s"}],
                                                 "output": the piano's output now, "hears": the device it plays on}
  POST /api/v1/piano/devices {id, name}         this device, by name (before it's chosen as the output)
  DELETE /api/v1/piano/devices/<id>             forget a device (it leaves the outputs until it's seen again)

Where it's heard (owner, 23 Oct: "I clicked play on PC22, and the sound came out of ipad... reason through how
connections are created and destroyed"): the piano's output is one choice for the whole room (sources' "device:<id>"
names a device; output_fn says the choice now). A device's stream (?device=<id>&name=<label>) gets the notes only
while the output is that device; otherwise it carries silence (the connection stays, so choosing it again is
instant), and notes still sounding are let go of at once when the output moves away; &also=1 hears whatever the
output ("as well as the speakers").  A stream that names no device (an older page) hears everything, as before.
Warm: while the output is a device, its sound is started even before the device connects (so Play doesn't wait for
the instrument to load), and kept WARM_S after its last listener leaves; otherwise LINGER_S.

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
import struct
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("show_service.piano_live")

REPO = pathlib.Path(__file__).resolve().parents[1]
PYTHON = pathlib.Path(os.environ.get("PIANO_PYTHON", pathlib.Path.home() / ".venvs" / "sm-piano" / "bin" / "python"))
INSTRUMENTS = REPO / "config" / "piano" / "instruments.json"
LINGER_S = 10.0
WARM_S = 900.0                       # kept this long after the last listener while the output is still a device
GATE_S = 0.25                        # how often the output is looked at (the reaper's pace)
DEVICE_KEEP_S = 12 * 3600.0          # a device stays among the outputs this long after it was last seen
DEVICES_MAX = 8
DEVICES_FILE = pathlib.Path.home() / ".local" / "state" / "screen-machine" / "show" / "piano-devices.json"
PANIC = b'{"panic": true}\n'
CHUNK = 4096
SR = 48000
PCM_FRAMES = 1024                    # a WebSocket message: 21 ms of s16le stereo
PCM_BYTES = PCM_FRAMES * 4
MAP_S = 0.5                          # how often a WebSocket listener is told where the piece is
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
        self.clients: dict = {}                          # listener queue -> (its device id or None: an older page,
        #                                                  also: it hears whatever the output, "as well as the speakers")
        self.feed: "queue.SimpleQueue" = queue.SimpleQueue()
        self.ready = False
        self.stop_at: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self.started = time.monotonic()
        self.t0: Optional[float] = None                  # host time of the stream's first audio frame
        self.delay_s = 0.25                              # piano_render.live's DELAY_S (its clock line says)
        self.bytes_out = 0                               # MP3 bytes made so far: the stream's position, CBR
        self.frames_out = 0                              # PCM frames made so far (the synth's own stream)
        self.enc: Optional[asyncio.subprocess.Process] = None   # the MP3 encoder, while an MP3 listener is there
        self.enc_t0: Optional[float] = None              # the MP3 stream's t0: the synth's at the frame it began
        self.heard = False                               # the notes go to it now (the output is its listener's)
        self.last_client = time.monotonic()


def _device_id(v: Any) -> Optional[str]:
    from .sources import DEVICE_ID_OK
    return v if isinstance(v, str) and 0 < len(v) <= 64 and set(v) <= DEVICE_ID_OK else None


def _label(v: Any) -> Optional[str]:
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())[:40]
    return v if v and v.isprintable() else None


class PianoLive:
    def __init__(self, output_fn: Optional[Callable[[], Optional[str]]] = None,
                 devices_file: Optional[pathlib.Path] = None,
                 pos_fn: Optional[Callable[[], Optional[dict]]] = None) -> None:
        self.synths: dict[str, _Synth] = {}
        self._feeds: tuple = ()                          # the heard synths' queues, read by on_midi (show-rx)
        self._reaper: Optional[asyncio.Task] = None
        self.output_fn = output_fn                       # the piano's output now (sources' / the player's)
        self.pos_fn = pos_fn                             # where the piece is: {piece, pos_s, at, speed, state}
        self.devices_file = devices_file if devices_file is not None else DEVICES_FILE
        self.devices: dict[str, dict] = self._load_devices()
        self._gate_task: Optional[asyncio.Task] = None

    # -- where it's heard ----------------------------------------------------------------------------------------
    def _output(self) -> Optional[str]:
        fn = self.output_fn
        if fn is None:
            return None
        try:
            v = fn()
        except Exception:                                # noqa: BLE001
            log.debug("piano live: output_fn failed", exc_info=True)
            return None
        return v if isinstance(v, str) else None

    def _target(self, out: Optional[str]) -> Optional[str]:
        """The device id ``out`` names, else None."""
        from .sources import DEVICE_PREFIX, is_device
        return out[len(DEVICE_PREFIX):] if is_device(out) else None

    def _hears(self, s: _Synth, out: Optional[str]) -> bool:
        """Do ``s``'s notes reach anyone who should hear them, with the output ``out``?"""
        if self.output_fn is None:
            return True                                  # nothing to say where: everyone hears (tests, older wiring)
        target = self._target(out)
        if target is not None and s.instrument == self._instrument_for(target) and not s.clients:
            return True                                  # warm for the chosen device: it's about to connect
        for dev, also, _kind in s.clients.values():
            if dev is None or also or dev == target:
                return True                              # an older page (hears everything, as before), "as well as
        return False                                     # the speakers", or the chosen device

    def _instrument_for(self, device: str) -> Optional[str]:
        return self._pick((self.devices.get(device) or {}).get("instrument"))

    def _regate(self) -> None:
        """Feed the synths whose listeners should hear now; let go of the notes in one that stops being heard."""
        out = self._output()
        feeds = []
        for s in self.synths.values():
            h = s.ready and self._hears(s, out)          # a loading synth gets nothing: stale notes come out in a burst
            if s.heard and not h:
                s.feed.put(PANIC)                        # the output moved away: nothing left ringing
            s.heard = h
            if h:
                feeds.append(s.feed)
        self._feeds = tuple(feeds)

    async def _gate(self) -> None:
        """Every GATE_S: the output now; the chosen device's sound warmed (started before it connects)."""
        while True:
            await asyncio.sleep(GATE_S)
            try:
                target = self._target(self._output())
                if target is not None:
                    inst = self._instrument_for(target)
                    if inst is not None and inst not in self.synths and len(self.synths) < MAX_SYNTHS \
                            and PYTHON.exists():
                        s = self.synths[inst] = _Synth(inst)
                        await self._start(s)
                        self._reap_soon()
                self._regate()
            except asyncio.CancelledError:
                raise
            except Exception:                            # noqa: BLE001
                log.exception("piano live: gate")

    def _gate_soon(self) -> None:
        if self.output_fn is not None and (self._gate_task is None or self._gate_task.done()):
            self._gate_task = asyncio.get_running_loop().create_task(self._gate())

    def _reap_soon(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.get_running_loop().create_task(self._reap())

    # -- the devices ---------------------------------------------------------------------------------------------
    def _load_devices(self) -> dict:
        try:
            doc = json.loads(self.devices_file.read_text())
        except (OSError, ValueError):
            return {}
        now_wall = time.time()
        out = {}
        for k, v in (doc.get("devices") or {}).items() if isinstance(doc, dict) else []:
            if _device_id(k) and isinstance(v, dict) and now_wall - float(v.get("seen_wall") or 0) < DEVICE_KEEP_S:
                out[k] = {"label": _label(v.get("label")) or "A phone or tablet", "instrument": v.get("instrument"),
                          "seen_wall": float(v.get("seen_wall") or 0)}
        return out

    def _save_devices(self) -> None:
        try:
            self.devices_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.devices_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({"devices": self.devices}))
            tmp.replace(self.devices_file)
        except OSError:
            log.debug("piano live: devices not saved", exc_info=True)

    def register(self, device: Any, name: Any = None, instrument: Optional[str] = None) -> Optional[str]:
        """A device seen (its stream, or its page saying who it is): the id, or None for a bad one."""
        d = _device_id(device)
        if d is None:
            return None
        row = self.devices.get(d) or {"label": "A phone or tablet", "instrument": None}
        row["label"] = _label(name) or row.get("label") or "A phone or tablet"
        if instrument:
            row["instrument"] = instrument
        row["seen_wall"] = time.time()
        self.devices[d] = row
        if len(self.devices) > DEVICES_MAX:
            for k in sorted(self.devices, key=lambda k: self.devices[k].get("seen_wall") or 0)[:-DEVICES_MAX]:
                del self.devices[k]
        self._save_devices()
        return d

    def device_outputs(self) -> list[dict]:
        """sources' outputs for the devices seen lately (newest first): {"id": "device:<id>", "label"}."""
        from .sources import DEVICE_PREFIX
        now_wall = time.time()
        rows = sorted(((k, v) for k, v in self.devices.items()
                       if now_wall - float(v.get("seen_wall") or 0) < DEVICE_KEEP_S),
                      key=lambda kv: -(kv[1].get("seen_wall") or 0))
        return [{"id": DEVICE_PREFIX + k, "label": v.get("label") or "A phone or tablet", "device": True}
                for k, v in rows]

    def _streaming(self, device: str) -> bool:
        return any(dev == device for s in self.synths.values() for dev, _also, _kind in s.clients.values())

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
        self._regate()

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
                str(PYTHON), "-m", "piano_render.live", "--instrument", s.instrument, "--format", "pcm",
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
                    self._regate()
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
        """The synth's PCM to every WebSocket listener (with its frame number) and the MP3 encoder, if any."""
        proc = s.proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                try:
                    chunk = await proc.stdout.readexactly(PCM_BYTES)
                except asyncio.IncompleteReadError:
                    break
                frame = s.frames_out
                s.frames_out += PCM_FRAMES
                mp3 = False
                for q, (_d, _a, kind) in list(s.clients.items()):
                    if kind == "mp3":
                        mp3 = True
                        continue
                    if q.qsize() >= CLIENT_QUEUE:
                        s.clients.pop(q, None)
                        q.put_nowait(None)               # too far behind: that listener ends
                    else:
                        q.put_nowait((frame, chunk))
                if mp3 and s.enc is None:
                    await self._encoder(s, frame)
                elif not mp3 and s.enc is not None:
                    await self._encoder_stop(s)
                if s.enc is not None and s.enc.stdin is not None:
                    try:
                        s.enc.stdin.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        await self._encoder_stop(s)
        finally:
            await self._encoder_stop(s)
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

    async def _encoder(self, s: _Synth, frame: int) -> None:
        """An MP3 encoder on the synth's PCM from ``frame`` on (an older page's stream: live.mp3)."""
        s.enc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "2",
            "-i", "pipe:0", "-c:a", "libmp3lame", "-b:a", f"{BITRATE}k", "-f", "mp3", "-flush_packets", "1", "pipe:1",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)
        s.enc_t0 = (s.t0 or 0.0) + frame / SR
        s.bytes_out = 0
        asyncio.get_running_loop().create_task(self._mp3_pump(s, s.enc))

    async def _mp3_pump(self, s: _Synth, enc: Any) -> None:
        try:
            while True:
                chunk = await enc.stdout.read(CHUNK)
                if not chunk:
                    break
                s.bytes_out += len(chunk)
                for q, (_d, _a, kind) in list(s.clients.items()):
                    if kind != "mp3":
                        continue
                    if q.qsize() >= CLIENT_QUEUE:
                        s.clients.pop(q, None)
                        q.put_nowait(None)
                    else:
                        q.put_nowait(chunk)
        finally:
            for q, (_d, _a, kind) in list(s.clients.items()):
                if kind == "mp3":
                    s.clients.pop(q, None)
                    q.put_nowait(None)                   # a new encoder is a new stream: the page reconnects
            if s.enc is enc:
                s.enc = None

    async def _encoder_stop(self, s: _Synth) -> None:
        enc, s.enc = s.enc, None
        s.enc_t0 = None
        if enc is None:
            return
        try:
            if enc.stdin is not None:
                enc.stdin.close()
            await asyncio.wait_for(enc.wait(), 3)
        except (asyncio.TimeoutError, OSError):
            enc.kill()

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
            target = self._target(self._output())
            for s in list(self.synths.values()):
                if s.clients:
                    s.stop_at = None
                    s.last_client = now
                    continue
                warm = target is not None and s.instrument == self._instrument_for(target)
                until = (max(s.last_client, s.started) + WARM_S) if warm else None
                if s.stop_at is None or (warm and s.stop_at < until):
                    s.stop_at = until if warm else now + LINGER_S
                elif not warm and s.stop_at > now + LINGER_S:
                    s.stop_at = now + LINGER_S           # no longer the output: the short linger
                if now >= s.stop_at:
                    await self._stop(s)

    # -- routes (show-bus) ---------------------------------------------------------------------------------------
    def routes(self, app: Any) -> None:
        app.router.add_get("/api/v1/piano/live.mp3", self._h_live)
        app.router.add_get("/api/v1/piano/live.ws", self._h_ws)
        app.router.add_get("/api/v1/piano/live/clock", self._h_clock)
        app.router.add_get("/api/v1/piano/devices", self._h_devices)
        app.router.add_post("/api/v1/piano/devices", self._h_device_post)
        app.router.add_delete("/api/v1/piano/devices/{id}", self._h_device_delete)

    def clock(self, s: _Synth, pos_s: Optional[float] = None) -> dict:
        """The stream's clock (owner, 23 Oct: "when playing from 'this device' we need to match latency with local
        score and/or pianola view because local device is quite a few seconds behind"): audio at stream second x
        carries the sound time t0 + x - delay_s (host CLOCK_MONOTONIC); host_now is that clock now; pos_s the
        stream second a listener joining now starts at. A listener whose audio element's currentTime is c (from its
        own start) hears sound time t0 + pos_s(at its join) + c - delay_s: it is host_now minus that behind."""
        return {"t0": s.enc_t0 if s.enc_t0 is not None else s.t0, "delay_s": s.delay_s,
                "host_now": round(time.monotonic(), 4),
                "pos_s": round(pos_s if pos_s is not None else s.bytes_out * 8 / (BITRATE * 1000), 3),
                "instrument": s.instrument}

    async def _h_clock(self, request: Any) -> Any:
        from aiohttp import web
        s = self.synths.get(self._pick(request.query.get("instrument")) or "")
        if s is None:
            return web.json_response({"error": "not_streaming", "detail": "No piano sound is streaming."}, status=404)
        return web.json_response(self.clock(s), headers={"Cache-Control": "no-store"})

    def devices_view(self) -> dict:
        out = self._output()
        now_wall = time.time()
        rows = [{**d, "streaming": self._streaming(d["id"].split(":", 1)[1]),
                 "seen_s": round(now_wall - float(self.devices[d["id"].split(":", 1)[1]].get("seen_wall") or 0), 1)}
                for d in self.device_outputs()]
        return {"devices": rows, "output": out, "hears": self._target(out)}

    async def _h_devices(self, request: Any) -> Any:
        from aiohttp import web
        self._gate_soon()
        return web.json_response(self.devices_view(), headers={"Cache-Control": "no-store"})

    async def _h_device_post(self, request: Any) -> Any:
        from aiohttp import web
        try:
            body = await request.json()
        except ValueError:
            body = None
        d = self.register((body or {}).get("id"), (body or {}).get("name"),
                          self._pick((body or {}).get("instrument")) if (body or {}).get("instrument") else None) \
            if isinstance(body, dict) else None
        if d is None:
            return web.json_response({"error": "bad_device", "detail": "Say which device: an id of letters, digits, "
                                      "- or _ (up to 64)."}, status=400)
        self._gate_soon()
        return web.json_response(self.devices_view(), headers={"Cache-Control": "no-store"})

    async def _h_device_delete(self, request: Any) -> Any:
        from aiohttp import web
        d = request.match_info.get("id", "")
        d = d.split(":", 1)[1] if d.startswith("device:") else d
        if self.devices.pop(d, None) is None:
            return web.json_response({"error": "unknown_device", "detail": "There's no such device."}, status=404)
        self._save_devices()
        return web.json_response(self.devices_view(), headers={"Cache-Control": "no-store"})

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
        dev = self.register(request.query.get("device"), request.query.get("name"), inst) \
            if request.query.get("device") else None
        self._gate_soon()
        s = await self._synth(inst)
        if s is None:
            return web.json_response({"error": "busy", "detail": "Two piano sounds are streaming already."},
                                     status=503)
        q: asyncio.Queue = asyncio.Queue()
        s.clients[q] = (dev, request.query.get("also") in ("1", "true", "yes"), "mp3")
        s.last_client = time.monotonic()
        self._regate()
        for _ in range(100):                             # its encoder starts on the synth's next chunk
            if s.enc_t0 is not None and s.enc is not None or s.proc is None:
                break
            await asyncio.sleep(0.05)
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
            s.clients.pop(q, None)
            s.last_client = time.monotonic()
            self._regate()
        return resp

    async def _synth(self, inst: str) -> Optional[_Synth]:
        """The running synth for ``inst``, started if need be; None when MAX_SYNTHS are running already."""
        s = self.synths.get(inst)
        if s is None:
            if len(self.synths) >= MAX_SYNTHS:
                return None
            s = self.synths[inst] = _Synth(inst)
            await self._start(s)
            self._reap_soon()
        return s

    async def _h_ws(self, request: Any) -> Any:
        """GET /api/v1/piano/live.ws?instrument=&device=&name=&also=: the piano as raw PCM over a WebSocket, each
        message stamped with the sound time it carries, so the page plays it with a small buffer it holds itself
        (owner, 23 Oct: "alignment between midi and 'this device' audio is utterly terrible ... when I hit 'pause'
        it pauses immediately ... rethink how to ensure sync"). Messages:
          text   {"hello": {sr, channels: 2, format: "s16le", instrument, delay_s, host_now}}  first
          binary <f8 T><f8 host_now><s16le stereo frames>   T: the host CLOCK_MONOTONIC sound time of the first
                 frame (the MIDI it carries was due then), host_now: that clock as it was sent
          text   {"map": {piece, pos_s, at, speed, state}, "host_now"} every MAP_S: the piece is at pos_s at host
                 time at (moving at speed while state is playing), so the frame of sound time T is at
                 pos_s + (T - at) * speed in the piece; {"host_now"} alone every MAP_S while nothing plays (so a
                 page always hears from a live socket)
          text   {"quiet": true} / {"quiet": false}  whether the room's output is elsewhere (the notes don't come)
        The page may send {"ping": <its clock>}; the answer is {"pong": <that>, "host_now"}."""
        from aiohttp import WSMsgType, web
        inst = self._pick(request.query.get("instrument"))
        if inst is None or not PYTHON.exists():
            return web.json_response({"error": "not_available", "detail": "There's no piano sound to stream."},
                                     status=404)
        dev = self.register(request.query.get("device"), request.query.get("name"), inst) \
            if request.query.get("device") else None
        self._gate_soon()
        s = await self._synth(inst)
        if s is None:
            return web.json_response({"error": "busy", "detail": "Two piano sounds are streaming already."},
                                     status=503)
        ws = web.WebSocketResponse(heartbeat=20.0, max_msg_size=1 << 16, compress=False)
        await ws.prepare(request)
        q: asyncio.Queue = asyncio.Queue()
        also = request.query.get("also") in ("1", "true", "yes")
        s.clients[q] = (dev, also, "pcm")
        s.last_client = time.monotonic()
        self._regate()
        # who listens, and when they leave: the evidence for "this device went quiet" (owner, 24 Oct)
        log.info("piano live: %s listening to %s%s (output %s)", dev or "a page", inst, ", also" if also else "",
                 self._output())
        joined = time.monotonic()

        async def reader() -> None:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(m, dict) and "ping" in m:
                    await ws.send_json({"pong": m["ping"], "host_now": time.monotonic()})
            await q.put(None)

        rd = asyncio.get_running_loop().create_task(reader())
        try:
            await ws.send_json({"hello": {"sr": SR, "channels": 2, "format": "s16le", "instrument": inst,
                                          "delay_s": s.delay_s, "host_now": time.monotonic()}})
            next_map = 0.0
            quiet: Optional[bool] = None
            while not ws.closed:
                try:
                    item = await asyncio.wait_for(q.get(), MAP_S)
                except asyncio.TimeoutError:
                    item = ()
                if item is None:
                    break
                now = time.monotonic()
                if now >= next_map:
                    next_map = now + MAP_S
                    pos = self._pos()
                    # nothing playing: the clock alone, so the page always hears from a live socket (its stall check)
                    await ws.send_json({"map": pos, "host_now": now} if pos is not None else {"host_now": now})
                    q_now = not s.heard
                    if q_now != quiet:
                        quiet = q_now
                        await ws.send_json({"quiet": quiet})
                if item and s.t0 is not None:
                    frame, chunk = item
                    t = s.t0 + frame / SR - s.delay_s
                    await ws.send_bytes(struct.pack("<dd", t, now) + chunk)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            rd.cancel()
            dropped = s.clients.pop(q, None) is None     # None: _pump let it go (too far behind, the synth ended)
            s.last_client = time.monotonic()
            self._regate()
            await ws.close()
            log.info("piano live: %s left %s after %.0f s (%s)", dev or "a page", inst, time.monotonic() - joined,
                     "dropped by the stream" if dropped else f"socket closed {ws.close_code}")
        return ws

    def _pos(self) -> Optional[dict]:
        fn = self.pos_fn
        if fn is None:
            return None
        try:
            v = fn()
        except Exception:                                # noqa: BLE001
            log.debug("piano live: pos_fn failed", exc_info=True)
            return None
        return v if isinstance(v, dict) else None
