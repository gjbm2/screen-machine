#!/usr/bin/env python3
"""Stand-in for room_service / stream_service in the scripts/ctl tests (stdlib only).

Serves GET /healthz -> 200 {"ok": true, "version": "fake-<name>"} on --port, like the real services.
Test-only switches:
  --exit-code N       exit immediately with status N (a crashing service)
  --healthz-status S  answer /healthz with status S (an active-but-unhealthy service)
  --spawn-child TAG   start a sleeping child whose argv carries TAG (checks KillMode=control-group)
Unknown arguments are accepted and ignored, so the real services' flags can pass through.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--name", default="fake")
    ap.add_argument("--exit-code", type=int, default=None)
    ap.add_argument("--healthz-status", type=int, default=200)
    ap.add_argument("--spawn-child", default=None)
    args, unknown = ap.parse_known_args()

    print(f"fake {args.name}: pid {os.getpid()} argv {sys.argv[1:]} ignored {unknown}", flush=True)
    if args.exit_code is not None:
        print(f"fake {args.name}: exiting with {args.exit_code} as asked", flush=True)
        return args.exit_code

    if args.spawn_child:
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(3600)", args.spawn_child],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    status = args.healthz_status
    body = json.dumps({"ok": status == 200, "version": f"fake-{args.name}"}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            if self.path == "/healthz":
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, fmt: str, *a: object) -> None:
            print(f"fake {args.name}: {self.address_string()} {fmt % a}", flush=True)

    def on_term(*_: object) -> None:
        raise SystemExit(0)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    signal.signal(signal.SIGTERM, on_term)
    print(f"fake {args.name}: listening on {args.host}:{args.port}", flush=True)
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        srv.server_close()
        print(f"fake {args.name}: stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
