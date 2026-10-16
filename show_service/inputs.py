"""The Room page's Mic choice (the owner's ask via 05, 16 Oct): every room sound input the senders offer, and which
one the room listens to. Nothing is hard-coded: the inputs are show.yaml's senders and what their heartbeats report.

* **Inputs** are the streams of the receiver's permanent senders (show.yaml, not ``on_demand``), less a sender with
  ``room_sound: false``, test streams, and MIDI (health kind ``midi``, or ``contract.MIDI_STREAMS`` before any data).
  Each carries its sender's ``label`` (show.yaml ``senders.<name>.label``, else the sender's name), its health state,
  the device in use (the heartbeat's ``dev``) and the devices it could use (the heartbeat's ``devices``; [] when the
  sender doesn't say).
* **The choice** ``{stream, device}`` ranks that stream after a playing test stream and before show.yaml's order
  (``Receiver.set_choice``; the usual 3 s switch-up rule applies), and puts the device in that sender's SUBSCRIBEs
  (``Receiver.set_device_hints``; a sender that can't find the name keeps its default). No choice = automatic.
  Each stream keeps its own device, so going back to a mic gets the device it had.
* **Persisted** in ``STATE_DIR/input.json`` (``{"v": 1, "stream", "devices": {stream: device}}``), written atomically,
  read at start. A bad file starts automatic; nothing here raises into the service.

Routes, on show-bus (pages reach them through room's ``/show/*`` pass-through):
  GET /api/v1/inputs   {active, choice: {stream, device} | null, inputs: [{stream, sender, label, state, device,
                       devices}]}
  PUT /api/v1/input    {stream: str | null, device?: str | null} -> the GET body. ``stream`` null: automatic.
                       ``device`` absent or null: that sender's default. 400 for a stream that isn't an input.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Optional

from aiohttp import web

from . import contract

log = logging.getLogger("show_service.inputs")

INPUT_FILE = contract.STATE_DIR / "input.json"
DEVICE_NAME_MAX = 128                       # vban.DEVICE_NAME_MAX


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


class Inputs:
    def __init__(self, health: Callable[[], dict], show_file: Path | str = contract.SHOW_FILE,
                 state_file: Path | str = INPUT_FILE) -> None:
        self._health = health
        self._show_file = Path(show_file)
        self._file = Path(state_file)
        self._rx: Any = None
        self._stream: Optional[str] = None
        self._devices: dict[str, str] = {}
        self._show: tuple[Optional[tuple[int, int]], dict] = (None, {})
        self.error: Optional[str] = None
        self._load()

    # -- the receiver ------------------------------------------------------------------------------------------------
    def attach(self, rx: Any) -> None:
        self._rx = rx
        self._apply()

    def _apply(self) -> None:
        rx = self._rx
        if rx is None:
            return
        try:
            rx.set_choice(self._stream)
            rx.set_device_hints(self._devices)
        except Exception as e:                           # noqa: BLE001
            self.error = f"the receiver didn't take the choice: {type(e).__name__}: {e}"
            log.warning("%s", self.error)

    # -- reading ---------------------------------------------------------------------------------------------------
    def _settings(self) -> dict:
        try:
            st = self._show_file.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        if key != self._show[0]:
            try:
                import yaml
                data = yaml.safe_load(self._show_file.read_text(encoding="utf-8"))
            except Exception as e:                       # noqa: BLE001
                log.warning("show.yaml not read for input labels (%s)", e)
                data = {}
            self._show = (key, data if isinstance(data, dict) else {})
        return self._show[1]

    def _h(self) -> dict:
        try:
            return _d(self._health())
        except Exception:                                # noqa: BLE001
            return {}

    def inputs(self, h: Optional[dict] = None) -> list[dict]:
        h = self._h() if h is None else h
        spec = _d(self._settings().get("senders"))
        streams = _d(h.get("streams"))
        out = []
        for name, s in _d(h.get("senders")).items():
            if not isinstance(s, dict) or s.get("on_demand"):
                continue
            sp = _d(spec.get(name))
            if sp.get("room_sound") is False:
                continue
            label = sp.get("label")
            label = label.strip() if isinstance(label, str) and label.strip() else name
            for st in s.get("streams") or []:
                if not isinstance(st, str) or st in contract.TEST_STREAMS:
                    continue
                m = _d(streams.get(st))
                kind = m.get("kind")
                if kind == "midi" or (kind in (None, "unknown") and st in contract.MIDI_STREAMS) or m.get("test"):
                    continue
                snd = _d(m.get("sender"))
                devs = snd.get("devices")
                out.append({"stream": st, "sender": name, "label": label, "state": m.get("state") or "down",
                            "device": snd.get("dev") or None,
                            "devices": [x for x in devs if isinstance(x, str)] if isinstance(devs, list) else []})
        return out

    def choice(self) -> Optional[dict]:
        return {"stream": self._stream, "device": self._devices.get(self._stream)} if self._stream else None

    def body(self) -> dict:
        h = self._h()
        return {"active": h.get("active_audio"), "choice": self.choice(), "inputs": self.inputs(h)}

    def mic(self, h: Optional[dict] = None) -> Optional[dict]:
        """The card's hearing.mic: the active stream and the device its sender says it uses; ``chosen`` when that is
        the Room page's pick (else automatic). None without an active stream."""
        h = self._h() if h is None else h
        a = h.get("active_audio")
        if not isinstance(a, str):
            return None
        snd = _d(_d(_d(h.get("streams")).get(a)).get("sender"))
        return {"stream": a, "device": snd.get("dev") or None, "chosen": a == self._stream}

    # -- the choice ------------------------------------------------------------------------------------------------
    def put(self, stream: Any, device: Any = None) -> dict:
        """Set the choice (ValueError in plain words for a bad one); persists, applies, returns the GET body."""
        if stream is None:
            self._stream = None
        else:
            if not isinstance(stream, str) or stream not in {i["stream"] for i in self.inputs()}:
                raise ValueError(f"{stream!r} isn't one of the room's inputs")
            if device is not None and (not isinstance(device, str) or len(device) > DEVICE_NAME_MAX):
                raise ValueError("device is a name of at most 128 characters, or null for the sender's default")
            self._stream = stream
            if device:
                self._devices[stream] = device
            else:
                self._devices.pop(stream, None)
        self._save()
        self._apply()
        log.info("input: %s%s", self._stream or "automatic",
                 f" ({self._devices[self._stream]})" if self._stream in self._devices else "")
        return self.body()

    def _load(self) -> None:
        try:
            o = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:                           # noqa: BLE001
            self.error = f"{self._file.name} unreadable ({type(e).__name__}): automatic"
            log.warning("%s", self.error)
            return
        o = _d(o)
        st = o.get("stream")
        self._stream = st if isinstance(st, str) and st else None
        self._devices = {k: v for k, v in _d(o.get("devices")).items()
                         if isinstance(k, str) and isinstance(v, str) and 0 < len(v) <= DEVICE_NAME_MAX}

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "stream": self._stream, "devices": self._devices}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._file)
            self.error = None
        except OSError as e:
            self.error = f"the mic choice isn't saved ({e}); it holds until a restart"
            log.warning("%s", self.error)

    # -- routes ----------------------------------------------------------------------------------------------------
    def routes(self, app: web.Application) -> None:
        app.router.add_get("/api/v1/inputs", self._get)
        app.router.add_put("/api/v1/input", self._put)

    async def _get(self, request: web.Request) -> web.Response:
        return web.json_response(self.body())

    async def _put(self, request: web.Request) -> web.Response:
        try:
            o = await request.json()
        except Exception:                                # noqa: BLE001
            o = None
        if not isinstance(o, dict) or "stream" not in o:
            return web.json_response({"error": "bad_request", "detail": 'send {"stream": name or null, "device"?}'},
                                     status=400)
        try:
            return web.json_response(self.put(o.get("stream"), o.get("device")))
        except ValueError as e:
            return web.json_response({"error": "bad_input", "detail": str(e)}, status=400)
