"""Virtual DMX devices: the patch (config/room/patch.yaml), the Art-Net receiver, the show bus and the Hue output.

DmxStack owns their lifecycle inside room_service: Art-Net packets are decoded through the patch; screen.* devices
reach the renderer on the show bus (127.0.0.1:8701); hue.* devices stream to the bridge while the room-state `hue`
targets say "dmx" (adapters/hue.py).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .artnet import ArtNetReceiver
from .bus import ShowBus
from .fake_artnet import FakeArtNet
from .patch import Patch, PatchError, load_patch

log = logging.getLogger("room_service.dmx")


class DmxStack:
    def __init__(self, patch_file: Path, show_bus_port: int = 8701, mode_fn=None) -> None:
        self.patch_file = Path(patch_file)
        self.patch: Patch = load_patch(self.patch_file)
        self.patch_error: str | None = None
        self._mtime = self.patch_file.stat().st_mtime
        a = self.patch.artnet
        self.rx = ArtNetReceiver(a.universe, a.stale_after_ms)
        # Always in-process (WS /api/v1/dmx/live); bound on :8701 only when show_bus_port is set (0: show_brain's).
        self.bus = ShowBus(self.patch, self.rx, port=show_bus_port or 0, mode_fn=mode_fn)
        self.fake = FakeArtNet(lambda: self.patch, self.rx)   # the Room console's test show (stand-in for Maestro)
        self.hue = None                          # HueOutput, when a bridge is paired
        self.hue_error: str | None = None
        self.hue_adapter = None
        self._watch: asyncio.Task | None = None
        self._build_hue()

    def _build_hue(self) -> None:
        if not self.patch.of_kind("hue"):
            return
        try:
            from ..hue.client import BridgeSecrets, HueClient
            from ..hue.output import HueOutput
        except ImportError as e:                 # hue-entertainment not installed in this venv
            self.hue_error = f"hue support not installed: {e}"
            return
        h = self.patch.hue
        sec = BridgeSecrets.load(h.get("bridge"), h.get("host"))
        if sec is None:
            self.hue_error = "no paired bridge (python -m room_service.hue.pair <ip>)"
            return
        self.hue = HueOutput(HueClient(sec), self.patch, self.rx, area_id=h.get("area"),
                             stop_after_s=float(h.get("stop_after_s", 10)))

    def adapter(self):
        """The room-state `hue` adapter, or a placeholder naming the patched members when there is no bridge."""
        from ..roomstate.contract import Placeholder
        if self.hue is not None:
            from ..adapters.hue import HueAdapter
            from ..hue.client import HueClient
            self.hue_adapter = HueAdapter(self.hue, self.patch, client=HueClient(self.hue.secrets))
            return self.hue_adapter
        members = [d.id.split(".", 1)[1] for d in self.patch.of_kind("hue")]
        return Placeholder("hue", members) if members else None

    async def start(self) -> None:
        a = self.patch.artnet
        await self.rx.start(a.bind_host, a.bind_port)
        if self.bus is not None:
            await self.bus.start()
        if self.hue is not None:
            self.hue.start()
        self._watch = asyncio.create_task(self._watch_patch(), name="patch-watch")

    async def stop(self) -> None:
        if self._watch is not None:
            self._watch.cancel()
        self.fake.stop("room_service stopping")
        if self.hue is not None:
            await self.hue.stop()
            await self.hue.client.close()
        if self.hue_adapter is not None:
            await self.hue_adapter.close()
        if self.bus is not None:
            await self.bus.stop()
        self.rx.close()

    async def _watch_patch(self) -> None:
        """Re-read patch.yaml when it changes: addresses and profiles apply at once; a new universe, bind or
        hue member needs a restart (status says so)."""
        while True:
            await asyncio.sleep(2.0)
            try:
                mtime = self.patch_file.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime == self._mtime:
                continue
            self._mtime = mtime
            try:
                new = load_patch(self.patch_file)
            except (PatchError, ValueError, OSError) as e:
                self.patch_error = f"{self.patch_file.name} not loaded: {e}"
                log.error("%s", self.patch_error)
                continue
            self.patch, self.patch_error = new, None
            self.rx.universe, self.rx.stale_after_ms = new.artnet.universe, new.artnet.stale_after_ms
            if self.bus is not None:
                self.bus.set_patch(new)
            if self.hue is not None:
                self.hue.patch = new
            if self.hue_adapter is not None:
                self.hue_adapter.patch = new         # hue.streamer room|show switches live
            log.info("patch.yaml reloaded: %d devices", len(new.devices))

    def status(self) -> dict:
        fixtures, glob = self.patch.decode(self.rx.dmx)
        end = max((d.end for d in self.patch.devices.values()), default=0)
        return {"patch": self.patch.describe(), "patch_error": self.patch_error, "input": self.rx.status(),
                "channels": list(self.rx.dmx[:end]) if self.rx.packets else [],   # raw bytes, 1..last patched
                "values": fixtures if self.rx.packets else {}, "global": glob if self.rx.packets else {},
                "bus": self.bus.status() if self.bus else None,
                "hue": self.hue.status() if self.hue else {"state": "unavailable", "detail": self.hue_error}}


def hue_placeholder(patch_file: Path):
    """With dmx off, the hue members still exist (as a placeholder) so modes.json can name them."""
    from ..roomstate.contract import Placeholder
    try:
        members = [d.id.split(".", 1)[1] for d in load_patch(patch_file).of_kind("hue")]
    except (OSError, PatchError, ValueError):
        return None
    return Placeholder("hue", members) if members else None
