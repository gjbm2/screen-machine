"""The scene's side of the engraving worker (engrave.py): one subprocess per scene, requests and replies on pipes.

``submit(key, mei)`` queues a request; only the newest per key is sent (a system re-engraved before the last
render came back skips the stale one).  ``results()`` returns finished renders without blocking: [(key, meta,
ink bytes)].  If the worker can't start (Verovio / resvg not installed) or dies, ``ok`` turns False and the scene
draws without engraving; it is never restarted more than once a minute.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import queue
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

log = logging.getLogger("stream_service.score")
REPO = pathlib.Path(__file__).resolve().parents[2]
RESTART_S = 60.0


class Engraver:
    def __init__(self, width: int, page_width: int = 3800, scale: int = 50) -> None:
        self.width, self.page_width, self.scale = width, page_width, scale
        self.proc: Optional[subprocess.Popen] = None
        self.out: "queue.Queue" = queue.Queue()
        self.pending: dict = {}                 # key -> newest mei not yet sent
        self.busy = False
        self.lock = threading.Lock()
        self.started_at = 0.0
        self.ok = True
        self.next_id = 0
        self.inflight: dict[int, str] = {}
        self._start()

    def _start(self) -> None:
        self.started_at = time.monotonic()
        try:
            env = {**os.environ, "PYTHONPATH": str(REPO)}
            self.proc = subprocess.Popen([sys.executable, "-m", "stream_service.score.engrave"], cwd=str(REPO),
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env=env, start_new_session=True)
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True, name="engrave-reader").start()
            self.ok = True
        except OSError as e:
            log.warning("engraving worker failed to start: %s", e)
            self.proc, self.ok = None, False

    def _reader(self, proc: subprocess.Popen) -> None:
        f = proc.stdout
        while True:
            head = f.read(4)
            if len(head) < 4:
                break
            meta = json.loads(f.read(struct.unpack(">I", head)[0]))
            n = struct.unpack(">I", f.read(4))[0]
            ink = f.read(n) if n else b""
            with self.lock:
                key = self.inflight.pop(meta.get("id"), None)
                self.busy = False
            if not meta.get("ok"):
                log.warning("engraving failed: %s", str(meta.get("error"))[:300])
            self.out.put((key, meta, ink))
            self._pump()
        with self.lock:
            self.busy = False
        if self.proc is proc:
            log.warning("engraving worker exited (code %s)", proc.poll())
            self.ok = False

    def submit(self, key: str, mei: str) -> None:
        with self.lock:
            self.pending[key] = mei
        self._pump()

    def _pump(self) -> None:
        with self.lock:
            if self.busy or not self.pending:
                return
            alive = self.proc is not None and self.proc.poll() is None
            if alive:
                key, mei = next(iter(self.pending.items()))
                del self.pending[key]
                self.next_id += 1
                rid = self.next_id
                self.inflight[rid] = key
                self.busy = True                    # claimed under the lock: one request in flight
        if not alive:
            if time.monotonic() - self.started_at >= RESTART_S:
                self._start()
                if self.proc is not None:
                    self._pump()
            return
        req = json.dumps({"id": rid, "mei": mei, "width": self.width, "page_width": self.page_width,
                          "scale": self.scale}).encode()
        try:
            self.proc.stdin.write(struct.pack(">I", len(req)) + req)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, AttributeError):
            with self.lock:
                self.busy = False
                self.inflight.pop(rid, None)
            self.ok = False

    def results(self) -> list:
        out = []
        while True:
            try:
                out.append(self.out.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.stdin.close()
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
