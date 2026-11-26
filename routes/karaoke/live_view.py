"""The karaoke screen's live picture follows the player (docs/KARAOKE.md; owner, 26 Nov: "when we start karaoke, I
think live view should trigger too. when we hit karaoke stop, I think live view should stop").

* **A song starts from stopped** (Play, an add to an idle player, its autostart): the karaoke screen goes live, the art
  director picking its scenes (room_service ``PUT /api/v1/screens/<id> {desired: {live: {auto: true}}}``, as a mode
  does), unless it is live already.
* **Stop** (the panel's Stop button: ``Store.control("stop")`` with ``by="you"``): back to art. The queue running out
  (Store.tick's stop, ``by="end"``) leaves it live: the next singer is choosing.
* Pause, resume and song changes leave it be, and so does a person's own Go live / Back to art in between: only the
  player's own changes move it.

The karaoke screen is settings.screen, else the deployment's own choice (the first live screen of its default room,
else the first live screen: shared/api/karaoke.ts karaokeScreen). A screen that can't go live is left alone.
Polled at POLL_S on its own thread.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

import smconfig
from utils.logger import error, info

from .store import Store

POLL_S = 0.5
TIMEOUT_S = 5.0
LIVE = {"live": {"auto": True}}


def karaoke_screen(setting: Optional[str]) -> Optional[str]:
    if setting:
        return setting
    from routes.deployment_api import deployment_info
    d = deployment_info()
    live = [s for s in d.get("screens") or [] if s.get("live")]
    pick = next((s for s in live if s.get("room") == d.get("default_room")), live[0] if live else None)
    return pick["id"] if pick else None


def _room(method: str, path: str, body: Any = None) -> Any:
    req = urllib.request.Request(smconfig.url("room").rstrip("/") + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:     # noqa: S310 - room_service on this machine
        return json.loads(r.read() or b"null")


class LiveView:
    def __init__(self, store: Store, *, room: Callable[..., Any] = _room,
                 screen_of: Callable[[Optional[str]], Optional[str]] = karaoke_screen) -> None:
        self.store, self._room, self._screen_of = store, room, screen_of
        self._seen: Optional[tuple] = None            # (rev, state, stop) of the player last looked at
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="karaoke-live-view", daemon=True).start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:  # noqa: BLE001 - room_service restarting: next change
                error(f"karaoke live view: {e}")
            self._stop.wait(POLL_S)

    def step(self) -> None:
        with self.store.lock:
            p = dict(self.store.s["player"])
            setting = self.store.s["settings"].get("screen")
        now = (p.get("rev"), p.get("state"), p.get("stop"))
        was, self._seen = self._seen, now
        if was is None or was[0] == now[0]:
            return                                    # the first look (a Flask restart) or nothing new: leave it be
        if now[1] == "playing" and was[1] == "stopped":
            self._set(setting, LIVE, "a song started")
        elif now[1] == "stopped" and now[2] == "you":   # Stop pressed (even on a stopped player: the show ends)
            self._set(setting, "art", "karaoke stopped")

    def _set(self, setting: Optional[str], desired: Any, why: str) -> None:
        screen = self._screen_of(setting)
        if not screen:
            return
        st = (self._room("GET", "/api/v1/status") or {}).get("screens", {}).get(screen)
        if st is None:
            return
        cur = st.get("desired")
        if (desired == "art") == (cur == "art" or cur is None):
            return                                    # already so
        try:
            self._room("PUT", f"/api/v1/screens/{screen}", {"desired": desired})
            info(f"karaoke: {screen} {'live' if desired != 'art' else 'back to art'} ({why})")
        except urllib.error.HTTPError as e:           # live_disabled, a mode's policy: not ours to push
            error(f"karaoke live view: {screen}: {e.code} {e.read()[:200]!r}")
