"""The mic on demand (the owner's rule, 16 Oct; README S5.16): the room's mic runs only while something needs it.

* **Wanted** while Auto is on (the autopilot's own on/off, read live: the room target ``autopilot.main``), the
  mode follows the music (a per-mode config row: room_service's device ``show.mic`` PUTs ``{"mode": true}`` here and
  ``{"mode": false}`` when it puts it back), or the Room page's AI listening switch is on (owner, 21 Oct: switching
  it on claims the mic; ``set_listening``, the listener's ``on``, read live and woken on a set). **On** = wanted and not muted: the Room page's Mute always wins.
* **Off** means the senders' capture devices are closed, not just ignored: ``Receiver.set_off_streams`` lists the
  room-sound streams under each SUBSCRIBE's ``off`` (the senders keep their off heartbeats, so the Mic list still
  shows every mic), the receiver drops anything of theirs that still arrives, and ``Rings.set_off`` makes the
  listener's ring forget them (``off_gen`` lets it drop a clip it cut before).
* **Which streams.** The room-sound inputs (``inputs.Inputs``: the permanent senders' streams less test, MIDI and
  ``room_sound: false``). On with a Mic choice: only the chosen stream captures; if it hasn't been live for
  FALLBACK_S since the mic came on, the choice was made or it was last live, every room-sound stream captures until
  it is (degrade, never refuse). On with no choice: all of them. Off or muted: none. The piano's MIDI and the test
  streams are never the mic: they stay as they were.
* **When.** :meth:`Mic.evaluate` runs every EVAL_S on its own thread ``show-mic``, at once on a PUT and on
  :meth:`Mic.wake` (Auto may have toggled: the autopilot calls it). The receiver is told only when the off set (or
  the streams it covers) changes. ``attach`` decides before the receiver starts, so the first SUBSCRIBE already
  carries it (and the receiver holds every possible mic off until a decision covers it). Without the brain
  (``--no-brain``) there is no Auto: the mode claim alone decides.
* **Persisted** in ``STATE_DIR/mic.json`` (``{"v": 1, "muted", "mode"}``), written atomically, read at start. A bad
  file starts unmuted with no mode claim and says so in ``error``. Nothing here raises into the service.

Routes, on show-bus (pages reach them through room's ``/show/*`` pass-through):
  GET /api/v1/mic   {on, muted, wanted, why, claims: [{by: auto | mode, label}], mode, streams: {capturing, off},
                    error}
  PUT /api/v1/mic   {muted?: bool, mode?: bool} (at least one) -> the GET body; else 400 {error, detail}. ``muted`` is
                    the Room page's Mute; ``mode`` is room_service's ``show.mic``.
The card carries the same body as ``mic_state`` (api.card), and says "Room mic switched off" / "Mic off" when that is
why no room sound comes in.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from aiohttp import web

from . import contract

log = logging.getLogger("show_service.mic")

MIC_FILE = contract.STATE_DIR / "mic.json"
EVAL_S = 0.5                                     # re-evaluated at 2 Hz (and at once on a PUT or a wake)
FALLBACK_S = 10.0                                # a chosen mic not live this long: every room mic captures
LIVE = ("streaming", "quiet")
ERROR_LOG_S = 30.0
THIS_MODE = "this mode"                          # the mode's label when the room doesn't say it


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def why_words(on: bool, muted: bool, auto: bool, mode: bool, label: Optional[str], listening: bool = False) -> str:
    """The Room page's words for the mic's state (plain words; ``label`` the mode's, else "this mode")."""
    lab = label or THIS_MODE
    if listening and not auto and not mode:
        return "Listening for AI listening" if on else "Switched off: AI listening can't hear the room"
    if on:
        if auto and mode:
            return f"Listening for Auto and {lab}"
        if auto:
            return "Listening for Auto"
        return f"Listening: {lab} follows the music"
    if not muted:
        return "Off: nothing needs it"
    if auto and mode:                                   # 09's audit H7: "Switched off", the Room page's own word
        return f"Switched off: Auto and {lab} can't hear the room"
    if auto:
        return "Switched off: Auto can't hear the room"
    if mode:
        return f"Switched off: {lab} can't follow the music"
    return "Switched off"


class Mic:
    """The mic's state and decision (module docstring). ``evaluate``, ``put``, ``state`` and ``wake`` are safe from
    any thread (one lock; nothing in it blocks)."""

    def __init__(self, inputs: Any, health: Callable[[], dict], *, state_file: Path | str = MIC_FILE,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self._inputs = inputs
        self._health = health
        self._file = Path(state_file)
        self._mono = mono
        self._rx: Any = None
        self._rings: Any = None
        self._auto_fn: Optional[Callable[[], Optional[bool]]] = None
        self._listening_fn: Optional[Callable[[], Optional[bool]]] = None
        self._room_fn: Optional[Callable[[], Any]] = None
        self._notify: Optional[Callable[[str, Optional[dict]], None]] = None
        self.muted = False
        self.mode = False
        self.error: Optional[str] = None
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._on_since: Optional[float] = None          # when the mic last came on (None while off)
        self._choice: Optional[str] = None
        self._choice_since = -math.inf
        self._live_at = -math.inf                       # the chosen stream was last seen live (while on)
        self._sent: Any = None                          # (off, covered) the receiver has
        self._body: dict = {}
        self._errors = 0
        self._err_logged = -math.inf
        self.evaluations = 0
        self.fallback = False
        self._load()
        self._body = self._build(False, False, None, [], [])

    # -- wiring (main thread, before start) ---------------------------------------------------------------------------
    def attach(self, rx: Any) -> None:
        """The receiver; decides at once, so the first SUBSCRIBE carries the decision."""
        self._rx = rx
        self.evaluate()

    def set_rings(self, rings: Any) -> None:
        self._rings = rings
        self.evaluate()

    def set_auto(self, fn: Optional[Callable[[], Optional[bool]]]) -> None:
        """Auto on/off now (autopilot.Autopilot.is_on: any thread, no side effects); None until the brain runs."""
        self._auto_fn = fn

    def set_listening(self, fn: Optional[Callable[[], Optional[bool]]]) -> None:
        """The AI listening switch now (the listener's ``on``: any thread, no side effects); the listener wakes the
        mic when it is set (``Listener.add_on_listener``)."""
        self._listening_fn = fn

    def set_room(self, fn: Optional[Callable[[], Any]]) -> None:
        """The room view (autopilot.RoomLink.view), for the mode's label."""
        self._room_fn = fn

    def set_notify(self, fn: Optional[Callable[[str, Optional[dict]], None]]) -> None:
        """api.notify: the card is refreshed at once when the mic's state changes."""
        self._notify = fn

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="show-mic", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout)

    def wake(self) -> None:
        """Re-evaluate now (any thread, never blocks): Auto may have toggled."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(EVAL_S)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.evaluate()
            except Exception:                           # noqa: BLE001 - never stop deciding
                self._failed("evaluate")

    def _failed(self, what: str) -> None:
        self._errors += 1
        now = time.monotonic()
        if now - self._err_logged >= ERROR_LOG_S:
            self._err_logged = now
            log.exception("mic: %s failed (%d so far)", what, self._errors)

    # -- reading ------------------------------------------------------------------------------------------------------
    def _h(self) -> dict:
        try:
            return _d(self._health())
        except Exception:                               # noqa: BLE001
            return {}

    def _auto(self) -> Optional[bool]:
        return self._read(self._auto_fn)

    def _listening(self) -> Optional[bool]:
        return self._read(self._listening_fn)

    @staticmethod
    def _read(fn: Optional[Callable[[], Optional[bool]]]) -> Optional[bool]:
        if fn is None:
            return None
        try:
            v = fn()
        except Exception:                               # noqa: BLE001
            return None
        return v if isinstance(v, bool) else None

    def _mode_label(self) -> Optional[str]:
        fn = self._room_fn
        if fn is None:
            return None
        try:
            view = _d(fn())
        except Exception:                               # noqa: BLE001
            return None
        name = view.get("target") or view.get("current")
        if not isinstance(name, str) or not name:
            return None
        lab = _d(_d(view.get("modes")).get(name)).get("label")
        return lab if isinstance(lab, str) and lab.strip() else None

    def state(self) -> dict:
        """The last GET body (any thread; the card reads it)."""
        return self._body

    # -- the decision -------------------------------------------------------------------------------------------------
    def evaluate(self) -> dict:
        """Decide which room-sound streams capture, tell the receiver and the rings what changed; the GET body."""
        with self._lock:
            now = self._mono()
            self.evaluations += 1
            h = self._h()
            auto = self._auto() is True
            listening = self._listening() is True
            wanted = auto or self.mode or listening
            on = wanted and not self.muted
            if on and self._on_since is None:
                self._on_since = now
            elif not on:
                self._on_since = None
            covered = sorted({st for snd in _d(h.get("senders")).values() if isinstance(snd, dict)
                              for st in snd.get("streams") or [] if isinstance(st, str)})
            broken = False
            try:
                inputs = list(self._inputs.inputs(h))
                choice = self._inputs.choice()
                room = [i["stream"] for i in inputs if isinstance(i, dict) and isinstance(i.get("stream"), str)]
            except Exception:                           # noqa: BLE001 - not classified: anything that could be a mic
                self._failed("inputs")                  # is off (privacy first), as the receiver's own hold
                inputs, choice, on, broken = [], None, False, True
                room = [st for st in covered if st not in contract.MIDI_STREAMS and st not in contract.TEST_STREAMS
                        and not st.startswith("test-")]
            states = {i["stream"]: i.get("state") for i in inputs if isinstance(i, dict) and i.get("stream") in room}
            chosen = choice.get("stream") if isinstance(choice, dict) else None
            if chosen != self._choice:
                self._choice, self._choice_since = chosen, now
            fallback = False
            if not on:
                capturing: list[str] = []
            elif not chosen or chosen not in room:
                capturing = list(room)
            else:
                if states.get(chosen) in LIVE:
                    self._live_at = now
                since = max(self._on_since if self._on_since is not None else now, self._choice_since, self._live_at)
                fallback = states.get(chosen) not in LIVE and now - since >= FALLBACK_S
                capturing = list(room) if fallback else [chosen]
            if fallback != self.fallback:
                self.fallback = fallback
                log.info("mic: %s", f"{chosen} hasn't been live for {FALLBACK_S:.0f} s: every room mic listens"
                         if fallback else f"{chosen} is live: only it listens")
            off = [s for s in room if s not in capturing]
            covered = sorted(set(covered) | set(room))
            self._tell(off, covered)
            body = self._build(on, auto, self._mode_label(), capturing, off, listening)
            if broken and wanted and not self.muted:
                body["why"] = "Off: the room's mics can't be listed (see the log)"
            changed = body != self._body
            if changed and body.get("on") != self._body.get("on"):
                log.info("mic %s: %s", "on" if on else "off", body["why"])
            self._body = body
        if changed and self._notify is not None:
            try:
                self._notify("card", None)
            except Exception:                           # noqa: BLE001
                pass
        return body

    def _tell(self, off: list[str], covered: list[str]) -> None:
        key = (frozenset(off), frozenset(covered))
        rx = self._rx
        if rx is not None and key != self._sent:
            fn = getattr(rx, "set_off_streams", None)
            if fn is not None:
                try:
                    fn(off, covered)
                    self._sent = key
                except Exception:                       # noqa: BLE001
                    self._failed("set_off_streams")
            else:
                self._sent = key                        # a receiver without the seam (tests' fakes)
        rings = self._rings
        fn = getattr(rings, "set_off", None) if rings is not None else None
        if fn is not None:
            try:
                fn(off)                                 # (a no-op when nothing changed)
            except Exception:                           # noqa: BLE001
                self._failed("rings.set_off")

    def _build(self, on: bool, auto: bool, label: Optional[str], capturing: list[str], off: list[str],
               listening: bool = False) -> dict:
        wanted = auto or self.mode or listening
        claims = ([{"by": "auto", "label": "Auto"}] if auto else []) + \
                 ([{"by": "mode", "label": label or THIS_MODE}] if self.mode else []) + \
                 ([{"by": "listening", "label": "AI listening"}] if listening else [])
        return {"on": bool(on), "muted": self.muted, "wanted": bool(wanted),
                "why": why_words(bool(on), self.muted, auto, self.mode, label, listening), "claims": claims,
                "mode": self.mode,
                "streams": {"capturing": list(capturing), "off": list(off)}, "error": self.error}

    # -- PUT ----------------------------------------------------------------------------------------------------------
    def put(self, muted: Any = None, mode: Any = None) -> dict:
        """Set ``muted`` and/or ``mode`` (bools; ValueError in plain words otherwise); persists, decides at once,
        returns the GET body."""
        if muted is None and mode is None:
            raise ValueError('send {"muted": true|false} and/or {"mode": true|false}')
        for name, v in (("muted", muted), ("mode", mode)):
            if v is not None and not isinstance(v, bool):
                raise ValueError(f"{name} is true or false")
        with self._lock:
            if muted is not None and muted != self.muted:
                self.muted = muted
                log.info("mic: %s by the Room page", "muted" if muted else "unmuted")
            if mode is not None and mode != self.mode:
                self.mode = mode
                log.info("mic: the mode %s", "follows the music" if mode else "no longer needs it")
            self._save()
            return self.evaluate()

    # -- persistence --------------------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            o = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:                          # noqa: BLE001
            self.error = f"{self._file.name} unreadable ({type(e).__name__}): unmuted, no mode claim"
            log.warning("%s", self.error)
            return
        if (not isinstance(o, dict) or not isinstance(o.get("muted", False), bool)
                or not isinstance(o.get("mode", False), bool)):
            self.error = f"{self._file.name} isn't {{v, muted, mode}}: unmuted, no mode claim"
            log.warning("%s", self.error)
            return
        self.muted, self.mode = bool(o.get("muted", False)), bool(o.get("mode", False))

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "muted": self.muted, "mode": self.mode}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._file)
            self.error = None
        except OSError as e:
            self.error = f"the mic setting isn't saved ({e}); it holds until a restart"
            log.warning("%s", self.error)

    # -- routes -------------------------------------------------------------------------------------------------------
    def routes(self, app: web.Application) -> None:
        app.router.add_get("/api/v1/mic", self._get)
        app.router.add_put("/api/v1/mic", self._put)

    async def _get(self, request: web.Request) -> web.Response:
        return web.json_response(self.evaluate())

    async def _put(self, request: web.Request) -> web.Response:
        try:
            o = await request.json()
        except Exception:                               # noqa: BLE001
            o = None
        if not isinstance(o, dict) or ("muted" not in o and "mode" not in o):
            return web.json_response({"error": "bad_request",
                                      "detail": 'send {"muted": true|false} and/or {"mode": true|false}'}, status=400)
        try:
            return web.json_response(self.put(o.get("muted"), o.get("mode")))
        except ValueError as e:
            return web.json_response({"error": "bad_request", "detail": str(e)}, status=400)
