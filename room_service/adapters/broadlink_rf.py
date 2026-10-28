"""RF/IR remotes sent by the BroadLink RM4 Pro (python-broadlink, local UDP; no cloud).

One JSON file per remote-controlled device under config/room/rf/ (every file with a "device" block: the glitterball,
glitterball.json) says which subsystem it is, the RM4 Pro's address, and the learned codes (base64 python-broadlink
packets, Home Assistant's format). Devices on the same RM4 Pro share one connection. Devices are added and removed in
Settings › Lights (remotes_api.py): a new one starts as a copy of a device type, config/room/rf/types/<model>.json
(new_device()), and its "model" names that type.
A file with "type": "light" is a light worked by its remote (rf_light.py: colour, brightness steps, pulse, flash).
Otherwise the kind follows the codes, read live from the file:
  - "on" and "off"  -> edge_discrete (a latched receiver): each code sets the state, safe to re-send.
  - "toggle" only   -> edge_toggle: one code flips it; the coordinator never sends it blind.
  - neither         -> dry_run: the device is listed, its rows are skipped, nothing is sent.
Settings (the file's "settings": speed, direction) are a second subsystem ("settings_subsystem", mirror_ball_motor),
one member each, every value its own button; it reads back the room's own record of what was sent. Power on is a
fixed sequence: power, then each setting (last sent, else its default) spacing_s apart.
Any other learned button is an action ("press", {"button": name}) for modes and the Room page.

Facts (15 Oct): RM4 Pro at 192.168.1.178, mac 34:8e:89:b1:5d:b2, devtype 0x520b. Unicast hello from WSL works.
The device must be UNLOCKED in the BroadLink app (device > ... > Properties > Lock device: off) or auth fails.
The app keeps its own learned codes in its cloud; they can't be read back, so codes are learned here:

  .venv/bin/python -m room_service.adapters.broadlink_rf info
  .venv/bin/python -m room_service.adapters.broadlink_rf learn on off --freq 433.92 --timeout 300
      # each in turn: the RM4 Pro's light comes on, press that button once near it. Without --freq it sweeps
      # first (hold the button until "Found", then press once). --ir for an infrared remote.
  .venv/bin/python -m room_service.adapters.broadlink_rf send on          # transmits: someone watching the ball
  .venv/bin/python -m room_service.adapters.broadlink_rf set on <base64>  # a code from elsewhere

Codes can also be learned and tested from the Lights setup page (remotes_api.py: LearnJob below, one at a time per
RM4 Pro). An RF device's frequency is found once (the sweep: hold the button) and reused for its other buttons.

The file is re-read when it changes, so a code learned (or re-learned) while room_service runs is used at once:
no restart. A missing file, or no python-broadlink in the venv, leaves the built-in placeholder.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..roomstate.contract import Adapter, Outcome, Placeholder, Reading
from ..roomstate.schema import enum_schema

log = logging.getLogger("room_service.broadlink")

REPO = Path(__file__).resolve().parents[2]
RF_DIR = REPO / "config" / "room" / "rf"
TYPES_DIR = RF_DIR / "types"
DEFAULT_FILE = RF_DIR / "glitterball.json"
STATE_BUTTONS = ("on", "off", "toggle")
POWER_WORDS = {"on": "On", "off": "Off", "toggle": "On/off (one button)"}
LOCKED_HINT = "the RM4 Pro is locked: BroadLink app > the device > ... > Properties > Lock device: off"


class RFError(Exception):
    pass


class RM4:
    """One RM4 Pro. python-broadlink is blocking; calls run on a worker thread, one at a time (a threading lock, so a
    cancelled caller can't overlap the next). A failed call reconnects (hello + auth) once and tries again."""

    def __init__(self, host: str, timeout_s: float = 3.0) -> None:
        self.host, self.timeout_s = host, timeout_s
        self._dev: Any = None
        self._tlock = threading.Lock()

    def _connect(self) -> Any:
        import broadlink
        try:
            dev = broadlink.hello(self.host, timeout=self.timeout_s)
        except Exception as e:
            raise RFError(f"no RM4 Pro answering at {self.host}: {e}") from None
        if getattr(dev, "is_locked", False):
            raise RFError(LOCKED_HINT)
        try:
            dev.auth()
        except Exception as e:
            raise RFError(f"RM4 Pro {self.host} refused auth ({e}); is it locked in the BroadLink app?") from None
        self._dev = dev
        return dev

    def call(self, fn, retry: bool = True) -> Any:
        """fn(device) on this thread, connecting first if needed."""
        with self._tlock:
            last: Exception | None = None
            for _ in range(2 if retry else 1):
                try:
                    dev = self._dev or self._connect()
                    return fn(dev)
                except RFError:
                    raise
                except Exception as e:           # a stale session after the device rebooted, a lost packet
                    self._dev, last = None, e
            raise RFError(f"RM4 Pro {self.host}: {last}")

    def info(self) -> dict:
        def get(dev):
            out = {"host": self.host, "model": dev.model, "devtype": hex(dev.devtype), "mac": dev.mac.hex(":"),
                   "name": dev.name, "locked": dev.is_locked}
            try:
                out["firmware"] = dev.get_fwversion()
            except Exception as e:
                out["firmware"] = f"? ({e})"
            try:
                out["sensors"] = dev.check_sensors()
            except Exception:
                pass
            return out
        return self.call(get)

    async def send(self, packet: bytes) -> None:
        await asyncio.to_thread(self.call, lambda dev: dev.send_data(packet))


class RemoteFile:
    """The device file, re-read when it changes on disk (a code learned while room_service runs)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._mtime: float | None = None
        self.raw: dict = {}
        self.reload()

    def reload(self) -> dict:
        m = self.path.stat().st_mtime
        if m != self._mtime:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError(f"{self.path.name}: the top level must be an object")
            self.raw, self._mtime = raw, m
        return self.raw

    def fresh(self) -> dict:
        """reload(), keeping what was loaded when the file is briefly unreadable (mid-edit)."""
        try:
            return self.reload()
        except (OSError, ValueError) as e:
            log.warning("%s not re-read (%s); using the codes already loaded", self.path.name, e)
            return self.raw

    @property
    def codes(self) -> dict[str, str]:
        return {k: v for k, v in (self.fresh().get("codes") or {}).items() if isinstance(v, str) and v}

    def code(self, button: str) -> bytes:
        c = self.codes.get(button)
        if not c:
            raise RFError(f"no code learned for {button!r} ({self.path.name})")
        return base64.b64decode(c)

    @property
    def medium(self) -> str:
        """"rf" or "ir": the file's own word, else how its codes were learned (RF unless they were IR)."""
        raw = self.fresh()
        if raw.get("medium") in ("rf", "ir"):
            return raw["medium"]
        sources = {str(m.get("source")) for m in (raw.get("learned") or {}).values() if isinstance(m, dict)}
        return "ir" if "ir" in sources and "rf" not in sources else "rf"

    @property
    def freq_mhz(self) -> float | None:
        """The RF frequency a button was learned on (one remote, one frequency), so the next needs no sweep."""
        for m in (self.fresh().get("learned") or {}).values():
            if isinstance(m, dict) and m.get("freq_mhz"):
                return float(m["freq_mhz"])
        return None

    def update(self, fn) -> None:
        """fn(raw) changes the file's dict in place; written whole and atomically."""
        raw = json.loads(self.path.read_text())
        fn(raw)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def save_code(self, button: str, code: bytes, meta: dict | None = None) -> None:
        def put(raw: dict) -> None:
            raw.setdefault("codes", {})[button] = base64.b64encode(code).decode()
            if meta:
                raw.setdefault("learned", {})[button] = meta
        self.update(put)


class Remote:
    """One remote-controlled device: its file, the RM4 Pro, and the settings last sent (persisted in the state dir, so
    they survive a restart). Nothing reads an IR/RF device back: what was sent is all anyone knows."""

    def __init__(self, file: RemoteFile, rm: RM4, state_dir: str | Path | None = None) -> None:
        self.file, self.rm = file, rm
        self.id = file.path.stem
        self.job: LearnJob | None = None          # the last learn started from the Lights setup page
        self.lock = asyncio.Lock()                # one press at a time, power and settings alike
        self.state_path = Path(state_dir) / f"remote-{file.path.stem}.json" if state_dir else None
        self.sent: dict[str, str] = {}            # settings member -> value last sent
        if self.state_path and self.state_path.exists():
            try:
                self.sent = {str(k): str(v) for k, v in json.loads(self.state_path.read_text()).items()}
            except (OSError, ValueError) as e:
                log.warning("%s not read (%s); settings start unknown", self.state_path, e)

    @property
    def settings(self) -> dict[str, dict]:
        """member -> {"label", "buttons": {value: button}, "labels": {value: words}} from the file's "settings"."""
        return {str(m): spec for m, spec in (self.file.fresh().get("settings") or {}).items() if isinstance(spec, dict)}

    @property
    def spacing_s(self) -> float:
        return float(self.file.fresh().get("spacing_s", 1.0))

    def canonical(self) -> list[tuple[str, str, str]]:
        """(member, value, button) for every setting, in file order: the value last sent, else its default."""
        out = []
        for m, spec in self.settings.items():
            buttons = spec.get("buttons") or {}
            v = self.sent.get(m) if self.sent.get(m) in buttons else spec.get("default")
            if v in buttons:
                out.append((m, str(v), buttons[v]))
        return out

    def button_rows(self) -> list[dict]:
        """Every button the device has, learned or not, for the Lights setup page: {id, label, group, learned, at}.
        Power first, then the file's colours, its settings, its declared button groups ("buttons": {group: {id:
        label}}), then any other learned code."""
        raw, codes = self.file.fresh(), self.file.codes
        learned = raw.get("learned") or {}
        declared = {g: b for g, b in (raw.get("buttons") or {}).items() if isinstance(b, dict)}
        rows: list[dict] = []

        def add(group: str, bid: str, label: str) -> None:
            if any(r["id"] == bid for r in rows):
                return
            meta = learned.get(bid) if isinstance(learned.get(bid), dict) else {}
            rows.append({"id": bid, "label": label, "group": group, "learned": bid in codes, "at": meta.get("at")})

        power = declared.get("Power") or {}
        for bid in STATE_BUTTONS:
            if bid in power or bid in codes:
                add("Power", bid, str(power.get(bid) or POWER_WORDS[bid]))
        for cid, w in (raw.get("colours") or {}).items():
            add("Colour", str(cid), str(w))
        for spec in self.settings.values():
            for v, bid in (spec.get("buttons") or {}).items():
                add(str(spec.get("label") or "Settings"), str(bid), str((spec.get("labels") or {}).get(v, v)))
        for group, buttons in declared.items():
            for bid, w in buttons.items():
                add(group, str(bid), str(w))
        for bid in codes:
            add("Other", bid, bid)
        return rows

    def remember(self, member: str, value: str) -> None:
        self.sent[member] = value
        if self.state_path:
            try:
                tmp = self.state_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.sent))
                tmp.replace(self.state_path)
            except OSError as e:
                log.warning("%s not saved: %s", self.state_path, e)

    async def press(self, button: str, times: int = 1) -> None:
        packet = self.file.code(button)
        for i in range(times):
            if i:
                await asyncio.sleep(0.3)
            await self.rm.send(packet)


class RFRemoteAdapter(Adapter):
    """The device's power (contract v1.0). Edge kinds: "ok" means the RM4 Pro took the code; nothing reads back.
    Turning it on is a fixed sequence (owner, 15 Oct): power, then every setting (speed, then direction) spacing_s
    apart, each the value last sent or its default. So the room knows what it is doing whether or not the device
    remembers its settings across power."""

    change_time_s = 8.0                          # a reconnect (hello + auth), power, and the settings spaced out
    resend = True                                # on/off codes are safe to repeat, and the app or the remote may
                                                 # have changed it behind our back (never applies to a toggle)

    def __init__(self, remote: Remote) -> None:
        raw = remote.file.raw
        self.remote, self.file, self.rm = remote, remote.file, remote.rm
        self.subsystem = str(raw.get("subsystem") or "mirror_ball")
        self.member = str(raw.get("member") or "main")
        self.members = [self.member]
        v = raw.get("values") or {}
        self.on_value, self.off_value = str(v.get("on", "spinning")), str(v.get("off", "stopped"))
        self.value_labels = {self.on_value: str((raw.get("value_labels") or {}).get(self.on_value, "On")),
                             self.off_value: str((raw.get("value_labels") or {}).get(self.off_value, "Off"))}
        self._label = str(raw.get("label") or self.subsystem)
        self.subsystem_label = self._label
        self.send_times = max(1, int(raw.get("send_times", 1)))

    # Live from the file (a code learned while running counts at once).
    @property
    def kind(self) -> str:
        codes = self.file.codes
        return "edge_toggle" if "toggle" in codes and not {"on", "off"} <= codes.keys() else "edge_discrete"

    @property
    def dry_run(self) -> bool:
        codes = self.file.codes
        return not ({"on", "off"} <= codes.keys() or "toggle" in codes)

    @property
    def buttons(self) -> list[str]:
        """Buttons that are neither power nor a setting: the action press."""
        used = {b for spec in self.remote.settings.values() for b in (spec.get("buttons") or {}).values()}
        return sorted(k for k in self.file.codes if k not in STATE_BUTTONS and k not in used)

    @property
    def actions(self) -> dict[str, dict]:
        b = self.buttons
        return ({"press": {"label": "Press a button on the remote",
                           "schema": {"type": "object", "properties": {"button": {"enum": b}}}}} if b else {})

    def value_schema(self, member: str) -> dict:
        s = enum_schema([self.on_value, self.off_value], self._label, self.value_labels, default=self.off_value)
        s["x-ui"]["switch"] = {"on": self.on_value, "off": self.off_value}    # the Room page draws an on/off switch
        return s

    def label(self, member: str) -> str:
        return self._label

    def words(self, member: str, value: Any) -> str:
        return self.value_labels.get(value, str(value))

    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        value = desired.get(self.member)
        if value not in (self.on_value, self.off_value):
            return Outcome("failed", f"{value!r} is not {self.on_value!r} or {self.off_value!r}", retry=False)
        on = value == self.on_value
        if self.dry_run:
            return Outcome("failed", f"no codes learned yet ({self.file.path.name})", retry=False)
        if self.kind == "edge_discrete":
            button, times = ("on" if on else "off"), self.send_times
        else:                                    # the coordinator only asks when the state is known and differs
            button, times = "toggle", 1
        again = self.remote.canonical() if on else []
        try:
            async with self.remote.lock:
                await self.remote.press(button, times)
                for m, v, b in again:
                    await asyncio.sleep(self.remote.spacing_s)
                    await self.remote.press(b)
                    self.remote.remember(m, v)
        except RFError as e:
            return Outcome("failed", str(e))
        log.info("%s: sent %s (%s)%s", self._label, button, value,
                 f"; settings again: {', '.join(f'{m}={v}' for m, v, _ in again)}" if again else "")
        return Outcome("ok", f"sent {button}")

    async def signal(self, member: str | None, action: str, args: dict) -> Outcome:
        if action != "press":
            return Outcome("failed", f"{self.subsystem} has no action {action!r}")
        button = str((args or {}).get("button", ""))
        if button not in self.buttons:
            return Outcome("failed", f"no button {button!r}; learned: {', '.join(self.buttons) or 'none'}")
        try:
            async with self.remote.lock:
                await self.remote.press(button)
        except RFError as e:
            return Outcome("failed", str(e))
        return Outcome("ok", f"pressed {button}")


class RemoteSettingsAdapter(Adapter):
    """The device's settings, one member each (speed, direction), every value its own button. A level kind whose
    read() is the Remote's own record of what was last sent (persisted; the device can't report anything): the
    power-on sequence sends settings too, and this way the room sees those as well. Choosing a value sends its button,
    which also puts right a device someone changed with its own remote."""

    kind = "level"
    change_time_s = 4.0
    poll_s = 5.0
    read_timeout_s = 1.0
    poll_all = True                              # reads are free: refresh every member, not only those a mode sets
                                                 # (the power-on sequence sends direction, which no mode sets)

    def __init__(self, remote: Remote, subsystem: str) -> None:
        self.remote, self.subsystem = remote, subsystem
        self.members = list(remote.settings)
        self._label = str(remote.file.raw.get("label") or subsystem)
        self.subsystem_label = self._label                # the device's name: its settings sit with it

    @property
    def dry_run(self) -> bool:
        codes = self.remote.file.codes
        return not any(b in codes for spec in self.remote.settings.values() for b in (spec.get("buttons") or {}).values())

    def _spec(self, member: str) -> dict:
        return self.remote.settings.get(member) or {}

    def value_schema(self, member: str) -> dict:
        spec = self._spec(member)
        values = list(spec.get("buttons") or {})
        if not values:
            return {}
        return enum_schema(values, self.label(member), {v: str(w) for v, w in (spec.get("labels") or {}).items()},
                           default=values[0])

    def label(self, member: str) -> str:
        return f"{self._label} {str(self._spec(member).get('label') or member).lower()}"

    def words(self, member: str, value: Any) -> str:
        return str((self._spec(member).get("labels") or {}).get(value, value))

    async def read(self, members: list[str]) -> dict[str, Reading]:
        return {m: Reading(self.remote.sent.get(m)) for m in members}

    async def apply(self, desired: dict[str, Any], opts: dict) -> Outcome:
        out: dict[str, Outcome] = {}
        async with self.remote.lock:
            for i, (member, value) in enumerate(desired.items()):
                button = (self._spec(member).get("buttons") or {}).get(value)
                if not button:
                    out[member] = Outcome("failed", f"{member}: no button for {value!r}", retry=False)
                    continue
                try:
                    if i:
                        await asyncio.sleep(self.remote.spacing_s)
                    await self.remote.press(button)
                except RFError as e:
                    out[member] = Outcome("failed", str(e))
                    continue
                self.remote.remember(member, value)
                out[member] = Outcome("ok", f"sent {button}")
        worst = next((o.status for o in out.values() if o.status == "failed"), "ok")
        return Outcome(worst, "; ".join(f"{m}: {o.detail}" for m, o in out.items()), members=out)


def device_types(types_dir: Path | None = None) -> dict[str, dict]:
    """model -> the type's file (config/room/rf/types/<model>.json), for Settings' "Add a remote-controlled device"."""
    out = {}
    for p in sorted((types_dir or TYPES_DIR).glob("*.json")):
        try:
            raw = json.loads(p.read_text())
        except (OSError, ValueError) as e:
            log.error("%s not read: %s", p, e)
            continue
        if isinstance(raw, dict):
            out[p.stem] = raw
    return out


def new_device(model: str, label: str, host: str, rf_dir: Path | None = None, types_dir: Path | None = None) -> Path:
    """Write a new device file from its type: its own id (from the name, unique), subsystem (= the id), name and RM4 Pro.
    The type's own words (_note, type_label, description) stay in the type."""
    t = device_types(types_dir).get(model)
    if t is None:
        raise ValueError(f"no device type {model!r}")
    rf_dir = rf_dir or RF_DIR
    base = "".join(c if c.isalnum() else "_" for c in label.strip().lower()).strip("_") or model
    base = base if base[0].isalpha() else f"{model}_{base}"
    taken = {p.stem for p in rf_dir.glob("*.json")}
    rid, n = base, 2
    while rid in taken:
        rid, n = f"{base}_{n}", n + 1
    raw = {"model": model, **{k: v for k, v in t.items() if k not in ("_note", "type_label", "description")},
           "label": label.strip() or str(t.get("label") or model), "subsystem": rid, "device": {"host": host}, "codes": {}}
    if raw.get("settings"):
        raw["settings_subsystem"] = f"{rid}_settings"
    path = rf_dir / f"{rid}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path


def from_config(path: str | Path | None = None, state_dir: str | Path | None = None,
                rms: dict[str, RM4] | None = None) -> list[Adapter]:
    """Every device file in config/room/rf/ with a "device" block (or just `path`), in name order; devices on the same
    RM4 Pro share one connection. Each file gives its power adapter (dry_run until it has codes) and, with a
    "settings" block, a settings adapter ("settings_subsystem", e.g. mirror_ball_motor); a "type": "light" file gives
    one light adapter (rf_light.py). Placeholders with their values without python-broadlink; nothing for a file that
    is missing or unreadable (the glitterball's built-in placeholder stays). `rms` (host -> RM4) shares connections
    with devices already running (one added in Settings)."""
    rms = {} if rms is None else rms
    if path:
        return _from_file(Path(path), state_dir, rms)
    out: list[Adapter] = []
    for p in sorted(RF_DIR.glob("*.json")):
        try:
            if not isinstance(json.loads(p.read_text()).get("device"), dict):
                continue                         # blinds.json: the Bloc hub, not the RM4 Pro
        except (OSError, ValueError, AttributeError):
            pass                                 # _from_file says what's wrong with it
        out += _from_file(p, state_dir, rms)
    return out


def _from_file(p: Path, state_dir: str | Path | None, rms: dict[str, RM4]) -> list[Adapter]:
    if not p.exists():
        return []
    try:
        f = RemoteFile(p)
    except (OSError, ValueError) as e:
        log.error("%s not read: %s", p, e)
        return []
    raw = f.raw
    sub = str(raw.get("subsystem") or "mirror_ball")
    settings_sub = str(raw.get("settings_subsystem") or f"{sub}_motor")
    settings = {m: s for m, s in (raw.get("settings") or {}).items() if isinstance(s, dict)}
    light = raw.get("type") == "light"
    try:
        import broadlink  # noqa: F401
    except ImportError:
        log.error("%s: python-broadlink isn't installed in this venv; placeholder", sub)
        if light:
            from .rf_light import placeholder
            return [placeholder(raw)]
        v = raw.get("values") or {}
        out: list[Adapter] = [Placeholder(sub, [str(raw.get("member") or "main")],
                                          [str(v.get("on", "spinning")), str(v.get("off", "stopped"))])]
        if settings:
            out.append(Placeholder(settings_sub, list(settings),
                                   {m: list(s.get("buttons") or {}) for m, s in settings.items()}))
        for p in out:
            p.subsystem_label = raw.get("label") or None
        return out
    host = str((raw.get("device") or {}).get("host") or "")
    rm = rms.setdefault(host, RM4(host))
    remote = Remote(f, rm, state_dir)
    if light:
        from .rf_light import RFLightAdapter
        return [RFLightAdapter(remote, state_dir)]
    out = [RFRemoteAdapter(remote)]
    if settings:
        out.append(RemoteSettingsAdapter(remote, settings_sub))
    return out


# -- the CLI: learn and test codes (owner present for any send) ------------------------------------------------------

REARM_S = 20.0


def _poll(fn, timeout_s: float, every_s: float = 1.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        r = fn()
        if r is not None:
            return r
        time.sleep(every_s)
    return None


class Stopped(RFError):
    pass


def _say(phase: str, words: str) -> None:
    print(words, flush=True)


def _learn_rf(dev, freq: float | None, timeout_s: float, say=_say, stop: threading.Event | None = None
              ) -> tuple[float, bytes]:
    from broadlink.exceptions import ReadError, StorageError
    stop = stop or threading.Event()
    if freq is None:
        dev.sweep_frequency()
        say("sweep", "Hold the button down (keep holding) near the RM4 Pro ...")

        def found():
            if stop.is_set():
                return 0.0
            ok, f = dev.check_frequency()
            return f if ok else None
        freq = _poll(found, timeout_s)
        if not freq:
            dev.cancel_sweep_frequency()
            if stop.is_set():
                raise Stopped("stopped")
            raise RFError("no RF frequency found: hold the button closer to the RM4 Pro and try again")
        say("found", f"Found {freq:.2f} MHz. Let go.")
        time.sleep(1.5)
    say("press", "Now press the button once, briefly (the RM4 Pro's light is on while it listens) ...")

    def data():
        if stop.is_set():
            return b""
        try:
            return dev.check_data()
        except (ReadError, StorageError):
            return None
    # The RM4 leaves learning mode by itself after a while: re-arm it every REARM_S until the press comes.
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        dev.find_rf_packet(freq)
        packet = _poll(data, min(REARM_S, end - time.monotonic()), every_s=0.5)
        if packet == b"":
            raise Stopped("stopped")
        if packet is not None:
            return freq, packet
    raise RFError("no code captured")


def _learn_ir(dev, timeout_s: float, say=_say, stop: threading.Event | None = None) -> bytes:
    from broadlink.exceptions import ReadError, StorageError
    stop = stop or threading.Event()
    say("press", "Point the remote at the RM4 Pro and press the button once ...")

    def data():
        if stop.is_set():
            return b""
        try:
            return dev.check_data()
        except (ReadError, StorageError):
            return None
    end = time.monotonic() + timeout_s           # re-armed every REARM_S, as for RF
    while time.monotonic() < end:
        dev.enter_learning()
        packet = _poll(data, min(REARM_S, end - time.monotonic()), every_s=0.25)
        if packet == b"":
            raise Stopped("stopped")
        if packet is not None:
            return packet
    raise RFError("no IR code captured")


def learn(rm: RM4, f: RemoteFile, button: str, ir: bool, freq: float | None, timeout_s: float, say=_say,
          stop: threading.Event | None = None) -> int:
    """Capture one button's code into the file; its size in bytes."""
    if ir:
        packet = rm.call(lambda dev: _learn_ir(dev, timeout_s, say, stop), retry=False)
        meta: dict = {"source": "ir"}
    else:
        freq, packet = rm.call(lambda dev: _learn_rf(dev, freq, timeout_s, say, stop), retry=False)
        meta = {"source": "rf", "freq_mhz": round(freq, 2)}
    meta["at"] = time.strftime("%Y-%m-%d %H:%M")
    f.save_code(button, packet, meta)
    return len(packet)


class LearnJob:
    """One button being learned for the Lights setup page, on its own thread (python-broadlink blocks). It holds the
    RM4 Pro for up to timeout_s, so sends to its other devices wait. phase: starting | sweep | found | press | done |
    failed | stopped; words say what to do now."""

    def __init__(self, remote: "Remote", button: str, sweep: bool = False, timeout_s: float = 60.0) -> None:
        self.remote, self.button, self.timeout_s = remote, button, timeout_s
        f = remote.file
        self.ir = f.medium == "ir"
        self.freq = None if (sweep or self.ir) else f.freq_mhz
        self.phase, self.words, self.started, self.ended = "starting", "Waking the RM4 Pro ...", time.time(), None
        self.bytes = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"learn-{remote.id}-{button}", daemon=True)

    @property
    def running(self) -> bool:
        return self.ended is None

    def start(self) -> "LearnJob":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _say(self, phase: str, words: str) -> None:
        self.phase, self.words = phase, words

    def _run(self) -> None:
        try:
            self.bytes = learn(self.remote.rm, self.remote.file, self.button, self.ir, self.freq, self.timeout_s,
                               self._say, self._stop)
            self.phase, self.words = "done", "Learned."
            log.info("%s: learned %s (%d bytes)", self.remote.id, self.button, self.bytes)
        except Stopped:
            self.phase, self.words = "stopped", "Stopped; nothing learned."
        except RFError as e:
            self.phase, self.words = "failed", str(e)
        except Exception as e:                   # the device mid-learn: say it, never kill the thread silently
            log.exception("%s: learning %s failed", self.remote.id, self.button)
            self.phase, self.words = "failed", f"{type(e).__name__}: {e}"
        self.ended = time.time()

    def view(self) -> dict:
        return {"button": self.button, "phase": self.phase, "words": self.words, "running": self.running,
                "medium": "ir" if self.ir else "rf", "freq_mhz": self.freq, "started": self.started,
                "ended": self.ended, "timeout_s": self.timeout_s}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m room_service.adapters.broadlink_rf",
                                 description="Learn and test codes for a device on the BroadLink RM4 Pro.")
    ap.add_argument("--file", default=str(DEFAULT_FILE), help="the device file (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="connect, authenticate, print the RM4 Pro's details (sends nothing)")
    sub.add_parser("codes", help="list the learned codes")
    p = sub.add_parser("learn", help="learn buttons' codes into the file, one after another")
    p.add_argument("buttons", nargs="+", help="on, off, toggle, or any other button name (an action)")
    p.add_argument("--ir", action="store_true", help="an infrared remote (default: RF 433/315 MHz)")
    p.add_argument("--freq", type=float, help="skip the sweep: the RF frequency in MHz (e.g. 433.92)")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--gap", type=float, default=4.0, help="seconds between one capture and listening for the next")
    p = sub.add_parser("send", help="transmit one learned code (someone should be watching the device)")
    p.add_argument("button")
    p.add_argument("--times", type=int, default=1)
    p = sub.add_parser("set", help="store a code from elsewhere (base64, Home Assistant format)")
    p.add_argument("button")
    p.add_argument("code")
    a = ap.parse_args(argv)

    f = RemoteFile(Path(a.file))
    if a.cmd == "codes":
        for k, v in f.codes.items():
            print(f"{k:12} {len(base64.b64decode(v))} bytes  {f.raw.get('learned', {}).get(k, '')}")
        return 0
    if a.cmd == "set":
        f.save_code(a.button, base64.b64decode(a.code), {"source": "set", "at": time.strftime("%Y-%m-%d %H:%M")})
        print(f"stored {a.button}")
        return 0
    rm = RM4(str((f.raw.get("device") or {}).get("host") or ""))
    try:
        if a.cmd == "info":
            print(json.dumps(rm.info(), indent=2, default=str))
        elif a.cmd == "learn":
            for i, button in enumerate(a.buttons):
                if i:
                    time.sleep(a.gap)            # a gap with the light off, so each press is clearly separate
                print(f"[{i + 1}/{len(a.buttons)}] {button}", flush=True)
                n = learn(rm, f, button, a.ir, a.freq, a.timeout)
                print(f"Learned {button!r}: {n} bytes -> {f.path}", flush=True)
        elif a.cmd == "send":
            packet = f.code(a.button)
            for i in range(max(1, a.times)):
                if i:
                    time.sleep(0.3)
                rm.call(lambda dev: dev.send_data(packet))
            print(f"Sent {a.button!r}")
    except RFError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
