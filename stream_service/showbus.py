"""Show-bus client: subscribes to room_service's show bus (or show-synth in development) (LIVE_V1 1.1 §1.9).

  --show-bus ws://127.0.0.1:8701/v1/show   [--show-bus-token-file ~/.config/screen-machine/show_bus.token]
  --music-bus ws://127.0.0.1:8703/v1/show  (repeatable; show_service: sound only, no fixtures)

K3 handshake: send {"type": "hello", "v": 1, "role": "renderer", "client_id": ..., "token"?: ...}, expect
{"type": "welcome", ...} within 5 s, then receive ``show.state`` frames and hand each to
:meth:`ShowInputs.on_frame` on arrival (every frame: the follower needs each pulse edge).  On any error or close
the client reconnects with full-jitter backoff (0.5 s doubling to 5 s).  A 4401 close (bad token) re-reads the
token file once.  The token is never logged.  A music bus runs the same client with ``inputs=None``: its
messages reach only the sound views, so it never touches the fixtures or the bus state the directors read.  Losing the bus never changes session state: signals ease to
their idle values (inputs.py).  Runs as a task on the asyncio loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import random
from typing import Callable, Optional

import aiohttp

from .inputs import ShowInputs

log = logging.getLogger("stream_service.showbus")

BACKOFF_MIN_S = 0.5
BACKOFF_MAX_S = 5.0
HELLO_TIMEOUT_S = 5.0


def read_token(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    p = pathlib.Path(path).expanduser()
    try:
        return p.read_text().strip() or None
    except OSError:
        return None


async def run(url: str, inputs: Optional[ShowInputs], token_file: Optional[str] = None,
              sound_sink: Optional[Callable[[dict], None]] = None, name: str = "show bus") -> None:
    """``sound_sink``: every music.* and show.state message, for the sessions' sound views (sound.py).
    ``inputs`` None: a sound-only bus (--music-bus)."""
    token = read_token(token_file)
    reread = False
    backoff = BACKOFF_MIN_S
    was_up = None          # log only state changes, not every retry
    while True:
        reason = ""
        try:
            async with aiohttp.ClientSession() as http:
                async with http.ws_connect(url, heartbeat=10.0, max_msg_size=1 << 20) as ws:
                    hello = {"type": "hello", "v": 1, "role": "renderer", "client_id": f"stream_service-{os.getpid()}"}
                    if token:
                        hello["token"] = token
                    await ws.send_str(json.dumps(hello))
                    msg = await asyncio.wait_for(ws.receive(), HELLO_TIMEOUT_S)
                    welcome = json.loads(msg.data) if msg.type == aiohttp.WSMsgType.TEXT else {}
                    if welcome.get("type") != "welcome":
                        code = ws.close_code
                        if code == 4401 and not reread:
                            token, reread = read_token(token_file), True
                        raise ConnectionError(f"no welcome (close code {code})")
                    reread = False
                    if inputs is not None:
                        inputs.on_connected(welcome)
                    log.info("%s up: %s (%s)", name, url, welcome.get("source") or "room_service")
                    was_up, backoff = True, BACKOFF_MIN_S
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                frame = json.loads(msg.data)
                            except ValueError:
                                continue
                            if not isinstance(frame, dict):
                                continue
                            kind = str(frame.get("type") or "")
                            if kind == "show.state" and inputs is not None:
                                inputs.on_frame(frame)
                            if sound_sink is not None and (kind == "show.state" or kind.startswith("music.")):
                                try:
                                    sound_sink(frame)
                                except Exception:
                                    log.exception("sound sink failed")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    reason = f"closed ({ws.close_code})"
        except asyncio.CancelledError:
            if inputs is not None:
                inputs.on_disconnected()
            raise
        except Exception as e:  # connection refused, timeout, bad frame...: retry
            reason = f"{type(e).__name__}: {e}"
        if inputs is not None:
            inputs.on_disconnected()
        if was_up is not False:
            log.warning("%s down (%s); retrying", name, reason)
            was_up = False
        await asyncio.sleep(random.uniform(0, backoff))
        backoff = min(BACKOFF_MAX_S, backoff * 2)
