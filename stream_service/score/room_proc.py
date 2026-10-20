"""The room's live-score transcription in a process of its own, shared by the screens' render workers (proc workers,
one render process per screen, can't share score/room.py's RoomScore in memory).  RemoteRoom starts it.

    python -m stream_service.score.room_proc --key room [--sock PATH] [--music-bus ws://127.0.0.1:8703/v1/show]
                                             [--linger 30]

It hears the music bus itself (showbus.run into a SoundView, the path a scene's notes take) and feeds the room's one
RoomScore (its builder and the PM2S analysis worker) every TICK_S, on host monotonic time: the bus's t_ms is
CLOCK_MONOTONIC, so this is the workers' clock too.  On its unix socket (room.sock_path, 0600) each client is sent the
phrase's bars so far and the builder's state (room.view_of), then each tick's new bars and the state whenever it
changes.  A client that stops reading (MAX_BACKLOG queued) is dropped and reconnects.  It exits LINGER_S after its
last client leaves, or if none comes.  Logs: ~/.local/state/screen-machine/logs/score_room.log.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import logging
import os
import pathlib
import pickle
import signal
import time

from stream_service import showbus
from stream_service.score.room import LINGER_S, MUSIC_BUS, RoomScore, pack, sock_path, try_connect, view_of
from stream_service.sound_view import SoundView

log = logging.getLogger("stream_service.score.room_proc")

TICK_S = 0.01                          # a note's glow is born from the builder: this adds at most 10 ms to it
MAX_BACKLOG = 4 << 20                  # bytes queued to one client before it's dropped
STATS_S = 60.0


async def serve(key: str, music_bus: str, linger_s: float, analysis: bool = True, sock: str | None = None) -> None:
    path = pathlib.Path(sock) if sock else sock_path(key)       # (the screen that starts it names its path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = try_connect(path)
    if s is not None:                                         # another one is serving this room
        s.close()
        log.info("score room %s: already served; exiting", key)
        return
    path.unlink(missing_ok=True)                              # a stale socket from one that died
    room = RoomScore(key, analysis=analysis)
    me = object()                                             # the room's only feeder: it always leads
    room.members.append(me)
    room.queues[id(me)] = collections.deque()
    sv = SoundView()
    clients: set = set()
    stop = asyncio.Event()

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(pack({"bars": list(room.phrase_bars), "rev": room.rev, "view": view_of(room.builder)}))
        clients.add(writer)
        log.info("score room %s: a screen joined (%d now)", key, len(clients))
        try:
            while await reader.read(4096):
                pass
        except (ConnectionError, OSError):
            pass
        finally:
            clients.discard(writer)
            writer.close()
            log.info("score room %s: a screen left (%d now)", key, len(clients))

    server = await asyncio.start_unix_server(client, path=str(path))
    os.chmod(path, 0o600)
    ino = path.stat().st_ino                                  # a successor may bind the path before we're gone
    pid_file = path.with_suffix(".pid")                     # its exact PID, for whoever has to stop it
    pid_file.write_text(str(os.getpid()))
    bus = asyncio.create_task(showbus.run(music_bus, None, sound_sink=sv.on_message, name="score room music bus"))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("score room %s: serving on %s (music bus %s, analysis %s)", key, path, music_bus,
             "on" if room.analyser is not None else "off")
    last_view, last_rev = None, -1
    idle_since = stats_t = time.monotonic()
    n_ticks, busy_s, worst_s, sent = 0, 0.0, 0.0, 0
    try:
        while not stop.is_set():
            await asyncio.sleep(TICK_S)
            t0 = time.monotonic()
            f = sv.update(t0 * 1000.0)
            with room.lock:
                room.feed(f, float(f.t_ms) / 1000.0)
                bars = room.take(me)
                blob = pickle.dumps(view_of(room.builder), protocol=pickle.HIGHEST_PROTOCOL)
            changed = blob != last_view
            if bars or changed or room.rev != last_rev:
                msg = pack({"bars": bars, "rev": room.rev, "view": pickle.loads(blob) if changed else None})
                last_view, last_rev = blob, room.rev
                for w in list(clients):
                    if w.transport.get_write_buffer_size() > MAX_BACKLOG:
                        log.warning("score room %s: a screen stopped reading; dropped (it reconnects)", key)
                        clients.discard(w)
                        w.close()
                    else:
                        w.write(msg)
                        sent += len(msg)
            dt = time.monotonic() - t0
            n_ticks, busy_s, worst_s = n_ticks + 1, busy_s + dt, max(worst_s, dt)
            if clients:
                idle_since = t0
            elif t0 - idle_since > linger_s:
                log.info("score room %s: no screens for %.0f s; exiting", key, linger_s)
                break
            if t0 - stats_t >= STATS_S:
                log.info("score room %s: %d screens, tick %.2f ms mean %.2f max, %.0f KB/s sent", key, len(clients),
                         1000.0 * busy_s / max(1, n_ticks), 1000.0 * worst_s, sent / 1024.0 / (t0 - stats_t))
                stats_t, n_ticks, busy_s, worst_s, sent = t0, 0, 0.0, 0.0, 0
    finally:
        try:                                                  # first, and only if it's still ours: the screens
            if path.stat().st_ino == ino:                     # reconnect at once and may start a successor
                path.unlink()
            if pid_file.read_text() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass
        server.close()
        for w in list(clients):
            w.close()
        bus.cancel()
        if room.analyser is not None:
            room.analyser.close()
            room.analyser = None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--key", default="room")
    ap.add_argument("--sock", default=None, help="the socket to serve (default: room.sock_path(key))")
    ap.add_argument("--music-bus", default=os.environ.get("SM_MUSIC_BUS") or MUSIC_BUS)
    ap.add_argument("--linger", type=float, default=LINGER_S)
    ap.add_argument("--no-analysis", action="store_true", help="the beat clock only (tests)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(serve(a.key, a.music_bus, a.linger, analysis=not a.no_analysis, sock=a.sock))


if __name__ == "__main__":
    main()
