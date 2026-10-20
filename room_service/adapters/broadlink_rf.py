"""RF/IR remotes sent by the BroadLink RM4 Pro (python-broadlink, local UDP; no cloud).

One JSON file per remote-controlled device under config/room/rf/ (the glitterball: glitterball.json) says which
subsystem it is, the RM4 Pro's address, and the learned codes (base64 python-broadlink packets, Home Assistant's
format). The kind follows the codes, read live from the file:
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
DEFAULT_FILE = REPO / "config" / "room" / "rf" / "glitterball.json"
STATE_BUTTONS = ("on", "off", "toggle")
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

    def save_code(self, button: str, code: bytes, meta: dict | None = None) -> None:
        raw = json.loads(self.path.read_text())
        raw.setdefault("codes", {})[button] = base64.b64encode(code).decode()
        if meta:
            raw.setdefault("learned", {})[button] = meta
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2) + "\n")
        tmp.replace(self.path)


class Remote:
    """One remote-controlled device: its file, the RM4 Pro, and the settings last sent (persisted in the state dir, so
    they survive a restart). Nothing reads an IR/RF device back: what was sent is all anyone knows."""

    def __init__(self, file: RemoteFile, rm: RM4, state_dir: str | Path | None = None) -> None:
        self.file, self.rm = file, rm
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


def from_config(path: str | Path | None = None, state_dir: str | Path | None = None) -> list[Adapter]:
    """The device in `path` (default config/room/rf/glitterball.json): its power adapter (dry_run until it has codes)
    and, with a "settings" block, a settings adapter ("settings_subsystem", e.g. mirror_ball_motor). Placeholders with
    their values without python-broadlink; [] if the file is missing or unreadable (the built-in placeholder stays)."""
    p = Path(path) if path else DEFAULT_FILE
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
    try:
        import broadlink  # noqa: F401
    except ImportError:
        log.error("%s: python-broadlink isn't installed in this venv; placeholder", sub)
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
    remote = Remote(f, RM4(host), state_dir)
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


def _learn_rf(dev, freq: float | None, timeout_s: float) -> tuple[float, bytes]:
    from broadlink.exceptions import ReadError, StorageError
    if freq is None:
        dev.sweep_frequency()
        print("Hold the button down (keep holding) ...", flush=True)

        def found():
            ok, f = dev.check_frequency()
            return f if ok else None
        freq = _poll(found, timeout_s)
        if freq is None:
            dev.cancel_sweep_frequency()
            raise RFError("no RF frequency found: hold the button closer to the RM4 Pro and try again")
        print(f"Found {freq:.2f} MHz. Let go.", flush=True)
        time.sleep(1.5)
    print("Now press the button once, briefly (the RM4 Pro's light is on while it listens) ...", flush=True)

    def data():
        try:
            return dev.check_data()
        except (ReadError, StorageError):
            return None
    # The RM4 leaves learning mode by itself after a while: re-arm it every REARM_S until the press comes.
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        dev.find_rf_packet(freq)
        packet = _poll(data, min(REARM_S, end - time.monotonic()), every_s=0.5)
        if packet is not None:
            return freq, packet
    raise RFError("no code captured")


def _learn_ir(dev, timeout_s: float) -> bytes:
    from broadlink.exceptions import ReadError, StorageError
    print("Point the remote at the RM4 Pro and press the button once ...", flush=True)

    def data():
        try:
            return dev.check_data()
        except (ReadError, StorageError):
            return None
    end = time.monotonic() + timeout_s           # re-armed every REARM_S, as for RF
    while time.monotonic() < end:
        dev.enter_learning()
        packet = _poll(data, min(REARM_S, end - time.monotonic()), every_s=0.25)
        if packet is not None:
            return packet
    raise RFError("no IR code captured")


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
                if a.ir:
                    packet = rm.call(lambda dev: _learn_ir(dev, a.timeout), retry=False)
                    meta = {"source": "ir"}
                else:
                    freq, packet = rm.call(lambda dev: _learn_rf(dev, a.freq, a.timeout), retry=False)
                    meta = {"source": "rf", "freq_mhz": round(freq, 2)}
                meta["at"] = time.strftime("%Y-%m-%d %H:%M")
                f.save_code(button, packet, meta)
                print(f"Learned {button!r}: {len(packet)} bytes -> {f.path}", flush=True)
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
