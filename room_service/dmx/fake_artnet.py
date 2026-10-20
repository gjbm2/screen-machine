"""Fake Art-Net: a stand-in for Maestro until the real unit is set up. The Room console starts and stops it.

It plays show_synth's synthetic song (stream_service/tools/show_synth.py `Song`: tempo, beats with accents,
sections with their own energy, colours and effect) and sends it as real ArtDmx packets over UDP to this
room_service's own Art-Net receiver (127.0.0.1:<artnet bind port>, the patch's universe). Everything downstream
(the patch decode, the show bus to the renderer, the Hue stream) is the path Maestro will use, so replacing the
fake with the real unit changes nothing else.

* **Encoding** (through the patch, patch.encode): a screen device gets the song's fixture values (rgb, pulse,
  pan/tilt/zoom, effect, variant, global.activity). A Hue lamp follows the screen it belongs to (`screen:` in
  patch.yaml), pulsed on the beat: rgb x (HUE_FLOOR + (1 - HUE_FLOOR) x pulse). MASTER DIMMER stays at 255, as
  Maestro's rule has it (v1 K2), so both see the same beat edge in the same packet.
* **Rate:** 40 packets/s with +-3 ms jitter, like show_synth.
* **Real Art-Net wins:** an ArtDmx packet for our universe from any other sender stops the fake at once
  (ArtNetReceiver calls `yield_to`), so the two never interleave. Starting is refused while real Art-Net is live.
* **Controls** are show_synth's: bpm, ramp, song (the style), pulses (false = a breakdown), next_section.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

from .artnet import build_artdmx
from .patch import Patch, encode

log = logging.getLogger("room_service.dmx.fake")

RATE_HZ = 40.0
JITTER_S = 0.003
HUE_FLOOR = 0.3                 # a lamp's brightness between beats, as a share of its colour


class FakeArtNet:
    def __init__(self, patch_fn, receiver) -> None:
        self.patch_fn = patch_fn                # -> the current Patch (it can be reloaded under us)
        self.rx = receiver
        self.song = None                        # show_synth.Song while running
        self.addr: tuple[str, int] | None = None   # our sending socket, so the receiver can tell us apart
        self.target: tuple[str, int] | None = None
        self.packets = 0
        self.started_at: float | None = None
        self.stopped: str | None = None         # why the last run ended
        self.last = ("pop", None)               # (style, bpm) of the last run: the next start's defaults
        self._seq = 0
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        receiver.fake = self

    @property
    def running(self) -> bool:
        return self._task is not None

    # -- lifecycle -------------------------------------------------------------------------------------------
    async def start(self, song: str = "pop", bpm: float | None = None) -> None:
        from stream_service.tools.show_synth import STYLES, Song
        if self.running:
            return
        if song not in STYLES:
            raise ValueError(f"song must be one of {sorted(STYLES)}")
        if self.rx.online and self.rx.source == "artnet":
            raise RuntimeError(f"real Art-Net is arriving (from {self.rx.last_from}): the fake stays off")
        a = self.patch_fn().artnet
        host = "127.0.0.1" if a.bind_host in ("", "0.0.0.0") else a.bind_host
        self.song = Song(song, bpm, seed=random.randrange(1 << 30))
        self._transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=(host, 0))
        self.addr = tuple(self._transport.get_extra_info("sockname")[:2])
        self.target = (host, a.bind_port)
        self.packets, self.stopped, self.started_at = 0, None, time.monotonic()
        self._task = asyncio.create_task(self._run(), name="fake-artnet")
        log.info("fake Art-Net: started (%s, %.0f bpm) -> udp %s:%d universe %d",
                 self.song.style, self.song.bpm, host, a.bind_port, a.universe)

    def stop(self, why: str = "stopped") -> None:
        if self.song is not None:
            self.last, self.song = (self.song.style, self.song.bpm), None
        if self._task is not None:
            self._task.cancel()
            self._task = None
            log.info("fake Art-Net: %s after %d packets", why, self.packets)
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self.addr = None
        self.stopped = why

    def yield_to(self, sender: str) -> None:
        """Real Art-Net arrived from `sender`: stop now (called by the receiver)."""
        log.warning("fake Art-Net: real Art-Net from %s, stopping the fake", sender)
        self.stop(f"real Art-Net arrived from {sender}")

    def control(self, body: dict) -> dict:
        """show_synth's controls (bpm, ramp, song, pulses, next_section). ValueError on a bad body."""
        if self.song is None:
            raise ValueError("the test show isn't running")
        return self.song.control(time.monotonic(), body)

    # -- sending ---------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        period = 1.0 / RATE_HZ
        nxt = time.monotonic()
        try:
            while True:
                nxt += period
                await asyncio.sleep(max(0.0, nxt - time.monotonic() + random.uniform(-JITTER_S, JITTER_S)))
                now = time.monotonic()
                if now - nxt > 0.5:              # the loop stalled: carry on from now, don't burst
                    nxt = now
                self._send(now)
        except asyncio.CancelledError:
            raise
        except Exception as e:                   # a bug here must not take room_service down
            log.exception("fake Art-Net failed")
            self._task = None
            self.stop(f"error: {type(e).__name__}: {e}")

    def _send(self, t: float) -> None:
        patch = self.patch_fn()
        dmx = bytearray(512)
        for ch, v in self.universe(patch, t).items():
            dmx[ch - 1] = v
        self._seq = self._seq % 255 + 1          # 1-255 (0 = "sequence off" in Art-Net)
        if self._transport is not None:
            self._transport.sendto(build_artdmx(patch.artnet.universe, bytes(dmx), self._seq), self.target)
            self.packets += 1

    def universe(self, patch: Patch, t: float) -> dict[int, int]:
        """1-based channel -> value for every patched device at time t."""
        st = self.song.state(t)
        songfx = st["fixtures"]
        first = next(iter(songfx.values()))
        screens = {d.extra.get("screen"): songfx.get(d.id, first) for d in patch.of_kind("screen")}
        out: dict[int, int] = {}
        for dev in patch.devices.values():
            if dev.kind == "screen":
                vals = {**songfx.get(dev.id, first), "global.activity": st["activity"]}
            elif dev.kind == "hue":
                fx = screens.get(dev.extra.get("screen")) or first
                k = HUE_FLOOR + (1.0 - HUE_FLOOR) * fx["pulse"]
                vals = {"rgb": [c * k for c in fx["rgb"]], "dimmer": 1.0}
            else:
                continue
            out.update(encode(dev, vals))
        return out

    def status(self) -> dict:
        t = time.monotonic()
        a = self.patch_fn().artnet
        out = {"running": self.running, "packets": self.packets, "rate_hz": RATE_HZ, "universe": a.universe,
               "target": f"{self.target[0]}:{self.target[1]}" if self.target else None, "stopped": self.stopped,
               "running_s": round(t - self.started_at, 1) if self.running and self.started_at else None}
        if self.song is not None:
            out["song"] = self.song.status(t)
        else:
            from stream_service.tools.show_synth import BPM_MAX, BPM_MIN, STYLES
            style, bpm = self.last
            out["song"] = {"song": style, "bpm": bpm or STYLES[style]["bpm"], "songs": sorted(STYLES),
                           "bpm_range": [BPM_MIN, BPM_MAX]}
        return out
