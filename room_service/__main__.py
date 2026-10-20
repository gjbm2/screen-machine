"""python -m room_service [--port 8700] [--config config/room/room.prod.yaml] (a dev instance: a temp copy with its own state_dir)"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from aiohttp import web

from .api import SafeAccessLogger
from .config import DEFAULT_CONFIG, load_config
from .service import RoomService
from .util import AlreadyRunning, RedactFilter


def setup_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


async def serve(svc: RoomService, host: str, port: int) -> None:
    runner = web.AppRunner(svc.app, access_log_class=SafeAccessLogger,
                           access_log=logging.getLogger("room_service.access"), shutdown_timeout=3.0)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logging.getLogger("room_service").info("listening on http://%s:%d", host, port)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.cleanup()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m room_service", description=__doc__)
    p.add_argument("--port", type=int, default=8700)
    p.add_argument("--host", default="0.0.0.0", help="bind address (kiosks reach it over the LAN)")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--state-dir", help="override service.state_dir")
    p.add_argument("--token-file", help="override service.token_file")
    p.add_argument("--no-snapshots", action="store_true", help="disable the headless snapshotter")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config(args.config)
    if args.state_dir:
        cfg.state_dir = Path(args.state_dir).expanduser()
    if args.token_file:
        cfg.token_file = Path(args.token_file).expanduser()
    if args.no_snapshots:
        cfg.snapshot.enabled = False
    svc = RoomService(cfg)
    try:
        asyncio.run(serve(svc, args.host, args.port))
    except AlreadyRunning as e:
        logging.getLogger("room_service").error("%s", e)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
