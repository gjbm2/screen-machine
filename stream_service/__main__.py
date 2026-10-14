"""python -m stream_service [--host 0.0.0.0] [--port 8082] [--public-base ws://host:port]
                          [--config-dir config/live] [--token-file ~/.config/screen-machine/stream_service.token]
                          [--show-bus ws://127.0.0.1:8701/v1/show] [--show-bus-token-file FILE]
                          [--latency-file config/room/latency.json] [--art-dir output]
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

# CUDA (CuPy) and NVENC (ffmpeg's -gpu, a child process) must number the GPUs the same way, and the same way
# as nvidia-smi / LIVE_V1 (0 = RTX 4060, 1 = Quadro P4000).  Must be set before CUDA initialises.
os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")

REPO = pathlib.Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m stream_service", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8082)
    ap.add_argument("--public-base", default=None,
                    help="base for attach URLs, e.g. ws://<lan-host>:8702 in production "
                         "(default: the request's Host)")
    ap.add_argument("--config-dir", default=str(REPO / "config" / "live"))
    ap.add_argument("--token-file", default="~/.config/screen-machine/stream_service.token")
    ap.add_argument("--max-sessions", type=int, default=4)
    ap.add_argument("--show-bus", default=None,
                    help="show bus to subscribe to (K3), e.g. ws://127.0.0.1:8701/v1/show; show-synth: :8711")
    ap.add_argument("--show-bus-token-file", default="~/.config/screen-machine/show_bus.token",
                    help="the bus token (sent in hello when the file exists)")
    ap.add_argument("--latency-file", default=str(REPO / "config" / "room" / "latency.json"),
                    help="render->photon calibration (presented->photon per screen)")
    ap.add_argument("--art-dir", default=str(REPO / "output"),
                    help="dev page only: where <screen>.jpg (the current artwork) is read from")
    ap.add_argument("--local-control", action="store_true",
                    help="answer /api/* and /dev* only from this machine (the app's /renderer/* pass-through); "
                         "/healthz, /media/* and /static/* stay open")
    ap.add_argument("--app-url", default="",
                    help="the renderer's address in the app, e.g. http://<lan-host>:8700/renderer: a browser "
                         "on /dev is sent there (with --local-control)")
    ap.add_argument("--warm", action="store_true",
                    help="at start, load the usual transitions on every GPU in the background (production)")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)  # access lines would log ?token= / ?k=

    from aiohttp import web

    from .server import make_app
    from .tokens import load_or_create_token

    token = load_or_create_token(args.token_file)
    app = make_app(token=token, config_dir=pathlib.Path(args.config_dir), public_base=args.public_base,
                   max_sessions=args.max_sessions, show_bus=args.show_bus,
                   show_bus_token_file=args.show_bus_token_file, latency_file=pathlib.Path(args.latency_file),
                   art_dir=pathlib.Path(args.art_dir), local_control=args.local_control,
                   app_url=args.app_url, warm=args.warm)
    logging.getLogger("stream_service").info("listening on %s:%d (dev page: /dev)", args.host, args.port)
    web.run_app(app, host=args.host, port=args.port, print=None, access_log=None,
                shutdown_timeout=10.0)


if __name__ == "__main__":
    main()
