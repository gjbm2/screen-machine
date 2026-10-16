"""The scene's side of its workers: one subprocess each, requests and replies on pipes (4-byte length + JSON, then a
length-prefixed blob).

* Engraver (engrave.py): ``submit(key, mei, **extra)`` queues a render; only the newest per key is sent (a part
  re-engraved before the last render came back skips the stale one).  With ``space_px`` every render is at one
  fixed staff size.  ``results()`` returns finished renders without blocking: [(key, meta, ink bytes)].
* Analyser (analyse.py, PM2S + metre, run by ~/.venvs/sm-score-ml's python at nice 19): ``submit("a", request)``,
  replies the same way with an empty blob.  ``available()`` says whether its venv and weights are there at all.
If a worker can't start or dies, ``ok`` turns False (the scene goes on without it); it is never restarted more than
once a minute.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import queue
import shutil
import struct
import subprocess
import sys
import threading
import time
from typing import Optional

log = logging.getLogger("stream_service.score")
REPO = pathlib.Path(__file__).resolve().parents[2]
RESTART_S = 60.0


ANALYSE_PYTHON = pathlib.Path.home() / ".venvs" / "sm-score-ml" / "bin" / "python"
PM2S_WEIGHTS = pathlib.Path.home() / ".local" / "share" / "screen-machine" / "pm2s"


class _Worker:
    module = ""
    python = sys.executable
    nice = 0
    pin_last = 0                                # run on the host's last N CPUs (nice ranks only within WSL: the
                                                # Windows side, Chrome and the encoder, still starve without it)

    def __init__(self) -> None:
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
            cmd = [str(self.python), "-m", self.module]
            if self.nice:
                cmd = ["nice", "-n", str(self.nice)] + cmd
            n = os.cpu_count() or 0
            if self.pin_last and n > self.pin_last and shutil.which("taskset"):
                cmd = ["taskset", "-c", f"{n - self.pin_last}-{n - 1}"] + cmd
            self.proc = subprocess.Popen(cmd, cwd=str(REPO), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, env=env, start_new_session=True)
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True,
                             name=f"{self.module.rsplit('.', 1)[-1]}-reader").start()
            self.ok = True
        except OSError as e:
            log.warning("%s worker failed to start: %s", self.module, e)
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
                log.warning("%s failed: %s", self.module, str(meta.get("error"))[:300])
            self.out.put((key, meta, ink))
            self._pump()
        with self.lock:
            self.busy = False
        if self.proc is proc:
            log.warning("%s worker exited (code %s)", self.module, proc.poll())
            self.ok = False

    def _send(self, key: str, payload) -> None:
        with self.lock:
            self.pending[key] = payload
        self._pump()

    def _request(self, rid: int, payload) -> dict:
        raise NotImplementedError

    def _pump(self) -> None:
        with self.lock:
            if self.busy or not self.pending:
                return
            alive = self.proc is not None and self.proc.poll() is None
            if alive:
                key, payload = next(iter(self.pending.items()))
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
        req = json.dumps(self._request(rid, payload)).encode()
        try:
            self.proc.stdin.write(struct.pack(">I", len(req)) + req)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, AttributeError):
            with self.lock:
                self.busy = False
                self.inflight.pop(rid, None)
            self.ok = False

    def results(self, limit: int | None = None) -> list:
        out = []
        while limit is None or len(out) < limit:
            try:
                out.append(self.out.get_nowait())
            except queue.Empty:
                return out
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


class Engraver(_Worker):
    module = "stream_service.score.engrave"

    def __init__(self, width: int, page_width: int = 3800, scale: int = 50, space_px: float | None = None) -> None:
        self.width, self.page_width, self.scale, self.space_px = width, page_width, scale, space_px
        super().__init__()

    def submit(self, key: str, mei: str, **extra) -> None:
        """Queue a render; ``extra`` goes into the request (e.g. margin_left)."""
        self._send(key, (mei, extra))

    def _request(self, rid: int, payload) -> dict:
        mei, extra = payload
        return {"id": rid, "mei": mei, "width": self.width, "page_width": self.page_width, "scale": self.scale,
                "space_px": self.space_px, **extra}


class Analyser(_Worker):
    module = "stream_service.score.analyse"
    python = ANALYSE_PYTHON
    nice = 19
    pin_last = 4

    @staticmethod
    def available() -> bool:
        return ANALYSE_PYTHON.exists() and (PM2S_WEIGHTS / "RNNJointBeatModel.pth").exists()

    def submit(self, key: str, request: dict) -> None:
        self._send(key, request)

    def _request(self, rid: int, payload) -> dict:
        return {"id": rid, **payload}
