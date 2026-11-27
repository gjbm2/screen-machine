"""The karaoke screen's live picture follows the player (docs/KARAOKE.md; owner, 26 Nov: "when we start karaoke, I
think live view should trigger too. when we hit karaoke stop, I think live view should stop").

* **A song starts from stopped** (Play, an add to an idle player, its autostart): the karaoke screen goes live, the art
  director picking its scenes (room_service ``PUT /api/v1/screens/<id> {desired: {live: {auto: true}}}``, as a mode
  does), unless it is live already.
* **Stop** (the panel's Stop button: ``Store.control("stop")`` with ``by="you"``): back to art. The queue running out
  (Store.tick's stop, ``by="end"``) leaves it live: the next singer is choosing.
* **Karaoke is a switch on every mode** (owner, 26 Nov: "karaoke mode is essentially a switch on every mode, but
  it's false on every mode apart from one that happens also to be labelled 'karaoke'"): a mode with ``"karaoke":
  true`` in the deployment's room/modes.json is a karaoke mode.
  - Entering one (from the Room page or anywhere) readies the room for karaoke (the mode's own state: the screen
    live, the mic, its feel) and leaves the music to the singers: nothing plays until Play (owner, 26 Nov: "turning
    on a karaoke mode shouldn't start playing music immediately. user needs to press play"). Leaving it for a mode
    without the switch stops the player.
  - A song started from the panel while the room is in another mode takes the room into the karaoke mode
    (room_service ``PUT /api/v1/mode``), remembering the mode it was in; Stop puts that one back (the modes' default
    after a Flask restart) while the room is still in it.
  - With no karaoke mode in the file: the screen alone, as above.
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


def modes_with_karaoke() -> list[str]:
    """The deployment's karaoke modes: room/modes.json's modes with ``"karaoke": true`` (file order)."""
    try:
        modes = json.loads(smconfig.path("room", "modes.json").read_text()).get("modes") or {}
    except (OSError, ValueError):
        return []
    return [name for name, m in modes.items() if isinstance(m, dict) and m.get("karaoke") is True]


class LiveView:
    def __init__(self, store: Store, *, room: Callable[..., Any] = _room,
                 screen_of: Callable[[Optional[str]], Optional[str]] = karaoke_screen,
                 karaoke_modes: Optional[Callable[[], list[str]]] = None) -> None:
        self.store, self._room, self._screen_of = store, room, screen_of
        self._karaoke_modes = karaoke_modes or modes_with_karaoke
        self._mode_seen: Optional[str] = None         # the room mode last looked at (None: not yet)
        self._before: Optional[str] = None            # the room mode a song took the room from (Stop puts it back)
        self._moving_to: Optional[str] = None         # a mode change of our own, not to be answered
        self._seen: Optional[tuple] = None            # (rev, state, stop) of the player last looked at
        self._kmodes: list[str] = []                  # the karaoke modes, as last read
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

    def in_karaoke_mode(self) -> bool:
        """The room is in a karaoke mode, as last looked at (the screens' join QR code: join.py)."""
        return self._mode_seen is not None and self._mode_seen in self._kmodes

    def step(self) -> None:
        kmodes = self._kmodes = self._karaoke_modes()
        if kmodes:
            self._follow_mode(kmodes)
        with self.store.lock:
            p = dict(self.store.s["player"])
            setting = self.store.s["settings"].get("screen")
        now = (p.get("rev"), p.get("state"), p.get("stop"))
        was, self._seen = self._seen, now
        if was is None or was[0] == now[0]:
            return                                    # the first look (a Flask restart) or nothing new: leave it be
        if now[1] == "playing" and was[1] == "stopped":
            if kmodes:
                self._enter(kmodes)
            else:
                self._set(setting, LIVE, "a song started")
        elif now[1] == "stopped" and now[2] in ("you", "mode"):   # Stop pressed, or the mode left karaoke
            if kmodes:
                if now[2] == "you":
                    self._leave(kmodes)
            else:
                self._set(setting, "art", "karaoke stopped")

    def _follow_mode(self, kmodes: list[str]) -> None:
        """The room's mode switches karaoke off: out of a karaoke mode stops the player (into one plays nothing)."""
        cur, _default = self._mode_now()
        was, self._mode_seen = self._mode_seen, cur
        if was is None or cur is None or cur == was:
            return                                    # the first look, or no change
        ours, self._moving_to = self._moving_to == cur, None
        if cur in kmodes and was not in kmodes and not ours:
            info(f"karaoke: on ({cur}): ready, waiting for Play")
        elif was in kmodes and cur not in kmodes and not ours:
            with self.store.lock:
                state = self.store.s["player"]["state"]
            if state != "stopped":
                self.store.control("stop", by="mode")
                info(f"karaoke: off ({cur})")

    def _mode_now(self) -> tuple[Optional[str], Optional[str]]:
        m = (self._room("GET", "/api/v1/status") or {}).get("mode") or {}
        return m.get("target") or m.get("current"), m.get("default")

    def _put_mode(self, mode: str, why: str) -> None:
        try:
            self._room("PUT", "/api/v1/mode", {"mode": mode, "source": "karaoke", "reason": why})
            info(f"karaoke: the room to {mode} ({why})")
        except urllib.error.HTTPError as e:           # an unknown mode, a refusal: not ours to push
            error(f"karaoke mode: {mode}: {e.code} {e.read()[:200]!r}")

    def _enter(self, kmodes: list[str]) -> None:
        cur, _default = self._mode_now()
        if cur in kmodes:
            return                                    # already in one
        self._before = cur
        self._moving_to = self._mode_seen = kmodes[0]
        self._put_mode(kmodes[0], "a song started")

    def _leave(self, kmodes: list[str]) -> None:
        cur, default = self._mode_now()
        if cur not in kmodes:
            return                                    # someone changed the mode in between: theirs
        back = self._before if self._before and self._before not in kmodes else default
        self._before = None
        if back and back not in kmodes:
            self._moving_to = self._mode_seen = back
            self._put_mode(back, "karaoke stopped")

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
