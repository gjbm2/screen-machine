"""Picks the room's mask object at start-up and fronts the ingest endpoint.

Kept free of `lightmask` imports, so service.py and api.py import it unconditionally: if
lightmask (or its `astral` dependency) is missing from the venv, room_service still starts,
with the Flask-only MaskPusher, and `/v1/ingest/lux` closes every socket with 4401.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from aiohttp import web

from .mask import MaskPusher
from .util import describe_exc

if TYPE_CHECKING:
    import aiohttp

    from .config import RoomConfig
    from .pages import PageHub

log = logging.getLogger("room_service.mask")
_warned: set[str] = set()


def make_mask_source(cfg: "RoomConfig", http: "aiohttp.ClientSession", pages: "PageHub", *,
                     is_frozen: Callable[[str], bool],
                     on_push: Callable[[str, dict], None] | None = None,
                     on_change: Callable[[], None] | None = None,
                     is_live: Callable[[str], bool] | None = None) -> MaskPusher:
    """MaskArbiter (Flask + sensor ingest) when ingest is enabled, else today's MaskPusher.
    Without a sensor keys file the arbiter behaves exactly like MaskPusher."""
    icfg = getattr(cfg, "ingest", None)
    if icfg is not None and icfg.enabled and cfg.mask.enabled:
        try:
            from .mask_arbiter import MaskArbiter
        except ImportError as e:
            log.error("sensor ingest unavailable: %s (lightmask needs astral==3.2 in this venv); "
                      "masks come from Flask only", describe_exc(e))
        else:
            return MaskArbiter(cfg, http, pages, is_frozen=is_frozen, on_push=on_push,
                               on_change=on_change, is_live=is_live)
    return MaskPusher(cfg, http, pages, is_frozen=is_frozen, on_push=on_push, on_change=on_change)


async def handle_ingest(mask: MaskPusher, request: web.Request) -> web.StreamResponse:
    """`WS /v1/ingest/lux` (LIVE_V1 §2.2b)."""
    hub = getattr(mask, "hub", None)
    if hub is not None:
        return await hub.handle(request)
    ws = web.WebSocketResponse(heartbeat=None, timeout=2.0)
    await ws.prepare(request)
    if "off" not in _warned:
        _warned.add("off")
        log.warning("sensor ingest is not available on this server (ingest disabled or lightmask "
                    "missing); closing relay sockets with 4401")
    await ws.close(code=4401, message=b"ingest disabled on this server")
    return ws


async def close_ingest(mask: MaskPusher) -> None:
    """At shutdown, before aiohttp waits for open handlers."""
    hub = getattr(mask, "hub", None)
    if hub is not None:
        await hub.close_all()


def mask_overview(mask: MaskPusher) -> dict:
    """{"mask": …} for /api/v1/status when the arbiter is in use, else {}."""
    fn = getattr(mask, "overview", None)
    return {"mask": fn()} if fn is not None else {}
