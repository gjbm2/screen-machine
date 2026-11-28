"""The app's speaker calibration wants the room mic (docs/SOUND.md; routes/sound/calibrate.py times chirps with it).

Flask runs the calibration; this reads its GET /api/sound/calibrate once a second on its own thread
(``show-soundcal``; the brain never waits on Flask) and offers ``running()``: the mic's "Calibrating" want
(mic.Mic.add_want), so the mic is on while an output is measured, whatever the deployment has (karaoke's own want,
karaoke_feed.py, is only for karaoke). Flask down or a bad reply: False until it answers again.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("show.soundcal")

POLL_S = 1.0
STALE_S = 5.0
TIMEOUT_S = 2.0
PATH = "/api/sound/calibrate"


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as r:   # noqa: S310 - loopback, our own Flask
        return json.loads(r.read().decode("utf-8"))


class SoundCalFeed:
    def __init__(self, base_url: str, *, fetch: Callable[[str], Any] = _fetch_json,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.url = base_url.rstrip("/") + PATH
        self._fetch, self._clock = fetch, clock
        self._last: Optional[tuple[float, bool]] = None
        self._wakes: list[Callable[[], None]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def poll_once(self) -> None:
        try:
            o = self._fetch(self.url)
            on = isinstance(o, dict) and o.get("state") == "running"
        except Exception:  # noqa: BLE001 - Flask restarting: not running
            on = False
        was = self._last[1] if self._last else False
        self._last = (self._clock(), on)
        if on != was:
            for fn in list(self._wakes):
                try:
                    fn()
                except Exception:  # noqa: BLE001
                    log.debug("soundcal wake failed", exc_info=True)

    def running(self) -> bool:
        """A calibration is measuring an output now (and the read is fresh)."""
        if self._last is None or self._clock() - self._last[0] > STALE_S:
            return False
        return self._last[1]

    def add_on_change(self, fn: Callable[[], None]) -> None:
        self._wakes.append(fn)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="show-soundcal", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(POLL_S)
