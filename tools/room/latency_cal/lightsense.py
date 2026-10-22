"""Yoctopuce light-sensor WebSocket relay, with an on-demand calibration capture.

Deployed on the kiosk box as /home/gjbm2/lightsense.py (light-relay.service, User=gjbm2).
This file is the repo copy. RP P4b's relay v2 (deploy/sensors/light-relay/) supersedes it.

Normal behaviour is unchanged from the 2025 relay: every 0.5 s it reads the sensor and sends
{"sensor_name": "living-room", "lux": <value>} to WS_URL, reconnecting every 5 s on failure.

Calibration capture (no sudo needed: the service runs as the same user who signals it):
  1. Write CAPTURE_REQUEST (JSON): {"out": <path>, "seconds": <max>, "freq": "100/s",
     "hold_output": true, "sensor": {"measureType": <int>}}. "sensor" is optional; its
     settings (allowlisted in SENSOR_SETTINGS) are applied for the capture and restored after.
  2. kill -USR1 <relay pid>. The relay switches the sensor's timed reports on at "freq" and
     logs every report as a JSON line {t_ns, v, lo, hi, m_end} to "out", using the box's
     wall-clock receive time. While "hold_output" is set, it keeps sending the lux value
     from the moment the capture started, so the screens' auto-brightness holds steady.
  3. The capture stops after "seconds", on kill -USR2, or when the relay exits. The sensor's
     original report frequency is then restored. Nothing is written to the module's flash.
STATUS_FILE records the relay's PID and version, so a caller can check it is signalling a
relay that understands the request (an older relay would treat SIGUSR1 as fatal).

Room ingest (optional second sink, LIVE_V1 §2.2b; off unless ROOM_CONFIG exists):
  ROOM_CONFIG (JSON, mode 0600): {"url": "ws://<room host>:<port>/v1/ingest/lux", "key": "..."}.
  Read at start and on kill -HUP, so changing the port or key needs no sudo. When it is on,
  every new measurement (~10 Hz, from the sensor's value callback, so no extra USB polling)
  goes to room_service as ingest.lux, with a 1 Hz heartbeat reading while the light is steady.
  After a step of more than 0.75 EV it re-reads the sensor once, ~120 ms later, so the
  room-side filter's two-sample confirmation (DCL §2.6) doesn't wait for the heartbeat.
  It keeps only the latest reading, reconnects with backoff, and drops a link that sends no
  lux.ack for 5 s. During a capture with hold_output it sends the held value with "held": true.
  The legacy sink above is unchanged and keeps running either way.
"""
import asyncio
import json
import math
import os
import signal
import time

import websockets
from yoctopuce.yocto_api import YAPI, YRefParam
from yoctopuce.yocto_lightsensor import YLightSensor

SENSOR_NAME = "living-room"
WS_URL = "ws://95.141.21.170:8765/"
RETRY_DELAY = 5  # seconds

VERSION = "calib-3"
SENSOR_SETTINGS = ("measureType",)       # what a capture request may change, temporarily
CAL_DIR = os.path.expanduser("~/latency_cal")
CAPTURE_REQUEST = os.path.join(CAL_DIR, "capture.json")
STATUS_FILE = os.path.join(CAL_DIR, "relay.json")
MAX_CAPTURE_S = 900

ROOM_CONFIG = os.path.expanduser("~/.config/light-relay/room.json")
ROOM_HEARTBEAT_S = 1.0
ROOM_ACK_TIMEOUT_S = 5.0
ROOM_BACKOFF_MAX_S = 30.0
ROOM_PUMP_S = 0.005                      # how often value callbacks are delivered
ROOM_CONFIRM_EV = 0.75                   # a step this big gets one confirming re-read...
ROOM_CONFIRM_S = 0.12                    # ...this long after it was sent


class Capture:
    """One calibration capture at a time; never allowed to break the relay."""

    def __init__(self, sensor):
        self.sensor = sensor
        self.active = False
        self.held_lux = None
        self.hold_output = False
        self.out = None
        self.original_freq = None
        self.original_settings = {}
        self.count = 0
        self.task = None

    def start(self, last_lux):
        if self.active:
            return
        try:
            with open(CAPTURE_REQUEST) as f:
                req = json.load(f)
            seconds = min(float(req.get("seconds", 300)), MAX_CAPTURE_S)
            self.out = open(req["out"], "w", buffering=1)
            self.hold_output = bool(req.get("hold_output", True))
            self.held_lux = last_lux
            self.original_freq = self.sensor.get_reportFrequency()
            self.count = 0
            requested = {k: v for k, v in (req.get("sensor") or {}).items() if k in SENSOR_SETTINGS}
            self.original_settings = {k: getattr(self.sensor, f"get_{k}")() for k in requested}
            for k, v in requested.items():
                getattr(self.sensor, f"set_{k}")(v)
            self.sensor.registerTimedReportCallback(self._on_report)
            self.sensor.set_reportFrequency(req.get("freq", "100/s"))
            self.active = True
            time.sleep(0.05)                     # let the module apply the settings before reading back
            self.sensor.load(0)
            module = self.sensor.get_module()
            self._write({"kind": "header", "version": VERSION, "t_ns": time.time_ns(),
                         "module": {"serial": module.get_serialNumber(),
                                    "firmware": module.get_firmwareRelease()},
                         "requested": requested,
                         "applied": {"reportFrequency": self.sensor.get_reportFrequency(),
                                     "measureType": self.sensor.get_measureType(),
                                     "advMode": self.sensor.get_advMode()},
                         "original": {"reportFrequency": self.original_freq, **self.original_settings},
                         "hold_output": self.hold_output, "held_lux": self.held_lux,
                         "seconds": seconds})
            print(f"Calibration capture started: {req['out']} for up to {seconds:.0f}s", flush=True)
            self.task = asyncio.get_running_loop().create_task(self._pump(seconds))
        except Exception as e:
            print(f"Calibration capture failed to start: {e}", flush=True)
            self.stop(reason=f"start failed: {e}")

    async def _pump(self, seconds):
        """Deliver timed-report callbacks promptly while the capture runs."""
        err = YRefParam()
        deadline = time.monotonic() + seconds
        try:
            while self.active and time.monotonic() < deadline:
                YAPI.HandleEvents(err)
                await asyncio.sleep(0.002)
        finally:
            if self.active:
                self.stop(reason="deadline")

    def _on_report(self, _fn, measure):
        self.count += 1
        self._write({"t_ns": time.time_ns(), "v": measure.get_averageValue(),
                     "lo": measure.get_minValue(), "hi": measure.get_maxValue(),
                     "m_end": measure.get_endTimeUTC()})

    def _write(self, obj):
        if self.out:
            self.out.write(json.dumps(obj) + "\n")

    def stop(self, reason="signal"):
        was_active = self.active
        self.active = False
        restored = None
        try:
            self.sensor.registerTimedReportCallback(None)
            for k, v in self.original_settings.items():
                getattr(self.sensor, f"set_{k}")(v)
            if self.original_freq is not None:
                self.sensor.set_reportFrequency(self.original_freq)
                time.sleep(0.05)
                self.sensor.load(0)
                restored = (self.sensor.get_reportFrequency() == self.original_freq and
                            all(getattr(self.sensor, f"get_{k}")() == v
                                for k, v in self.original_settings.items()))
        except Exception as e:
            print(f"Calibration capture: restore failed: {e}", flush=True)
            restored = False
        if self.out:
            self._write({"kind": "footer", "t_ns": time.time_ns(), "reason": reason,
                         "reports": self.count, "restored": restored})
            self.out.close()
            self.out = None
        self.original_freq = None
        self.original_settings = {}
        if was_active:
            print(f"Calibration capture stopped ({reason}); {self.count} reports; "
                  f"sensor restored={restored}", flush=True)

    def output_lux(self, live_lux):
        return self.held_lux if (self.active and self.hold_output) else live_lux

    def holding(self):
        return self.active and self.hold_output


def load_room_config():
    """ROOM_CONFIG as {"url", "key"}, or None to leave the room sink off."""
    try:
        with open(ROOM_CONFIG) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"Room ingest: ignoring unreadable {ROOM_CONFIG}: {e}", flush=True)
        return None
    url = cfg.get("url") if isinstance(cfg, dict) else None
    if not (isinstance(url, str) and url.startswith(("ws://", "wss://"))):
        print(f"Room ingest: no ws:// url in {ROOM_CONFIG}", flush=True)
        return None
    return {"url": url, "key": cfg.get("key")}


class RoomSink:
    """Optional second sink to room_service. Never allowed to block or break the relay."""

    def __init__(self, sensor, capture):
        self.sensor = sensor
        self.capture = capture
        self.cfg = None
        self.tasks = []
        self.latest = None                   # {"lux", "mono_ms", "held"}
        self.fresh = asyncio.Event()
        self.unacked_since = None            # monotonic time of the oldest send not yet acked
        self.acked = False                   # this connection has had at least one lux.ack
        self.seq = time.time_ns() // 1_000_000   # keeps rising across relay restarts
        self.prev_sent = None
        self.confirm = None                  # pending confirming re-read (TimerHandle)

    def configure(self, cfg):
        if cfg == self.cfg:
            return
        self.stop()
        if cfg is None:
            print("Room ingest: off", flush=True)
            return
        self.cfg = cfg
        print(f"Room ingest: on, {cfg['url']}", flush=True)
        try:
            self.sensor.registerValueCallback(self._on_value)
        except Exception as e:
            print(f"Room ingest: value callback failed: {e}", flush=True)
        loop = asyncio.get_running_loop()
        self.tasks = [loop.create_task(self._pump()), loop.create_task(self._run(cfg))]

    def stop(self):
        if self.cfg is not None:
            try:
                self.sensor.registerValueCallback(None)
            except Exception as e:
                print(f"Room ingest: value callback removal failed: {e}", flush=True)
        if self.confirm:
            self.confirm.cancel()
            self.confirm = None
        for task in self.tasks:
            task.cancel()
        self.tasks = []
        self.cfg = None
        self.latest = None

    async def close(self):
        tasks = self.tasks
        self.stop()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _offer(self, lux, confirm=False):
        held = self.capture.holding()
        lux = self.capture.output_lux(lux)
        if lux is None:                      # holding, but nothing was read before the capture
            return
        self.latest = {"lux": lux, "mono_ms": time.monotonic_ns() // 1_000_000, "held": held,
                       "confirm": confirm}
        self.fresh.set()

    def _on_value(self, _fn, value):
        """Value callback: one per new measurement, delivered by HandleEvents."""
        try:
            self._offer(float(value))
        except Exception:
            pass

    def _heartbeat_read(self):
        """No new measurement for a second (steady light): read the sensor once, so a dead
        sensor goes quiet instead of repeating its last value."""
        try:
            lux = self.sensor.get_currentValue() if self.sensor.isOnline() else None
        except Exception:
            lux = None
        if lux is None or lux == YLightSensor.CURRENTVALUE_INVALID:
            self.latest = None
        else:
            self._offer(lux)

    def _maybe_confirm(self, r):
        """After a large step, schedule one re-read so the step is confirmed promptly."""
        prev, self.prev_sent = self.prev_sent, r["lux"]
        if r["held"] or r["confirm"] or prev is None or self.confirm is not None:
            return
        if abs(math.log2((max(r["lux"], 0) + 1) / (max(prev, 0) + 1))) > ROOM_CONFIRM_EV:
            self.confirm = asyncio.get_running_loop().call_later(ROOM_CONFIRM_S, self._confirm_read)

    def _confirm_read(self):
        self.confirm = None
        if self.cfg is None or self.capture.holding():
            return
        try:
            lux = self.sensor.get_currentValue() if self.sensor.isOnline() else None
        except Exception:
            lux = None
        if lux is not None and lux != YLightSensor.CURRENTVALUE_INVALID:
            self._offer(lux, confirm=True)     # sent even if unchanged

    async def _pump(self):
        err = YRefParam()
        while True:
            try:
                YAPI.HandleEvents(err)
            except Exception as e:
                print(f"Room ingest: HandleEvents failed: {e}", flush=True)
                await asyncio.sleep(1)
            await asyncio.sleep(ROOM_PUMP_S)

    async def _read_acks(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "lux.ack":
                self.unacked_since = None
                self.acked = True

    async def _run(self, cfg):
        backoff = 1.0
        while True:
            self.unacked_since = None
            self.acked = False
            try:
                async with websockets.connect(cfg["url"], open_timeout=5, close_timeout=1,
                                              max_size=65536) as ws:
                    hello = {"role": "sensor", "sensor": SENSOR_NAME}
                    if cfg.get("key"):
                        hello["key"] = cfg["key"]
                    await ws.send(json.dumps({"v": 1, "type": "hello", "data": hello}))
                    print("Room ingest: connected", flush=True)
                    reader = asyncio.get_running_loop().create_task(self._read_acks(ws))
                    try:
                        await self._send_loop(ws, reader)
                    finally:
                        reader.cancel()
            except asyncio.CancelledError:
                raise
            except websockets.exceptions.ConnectionClosed as e:
                if e.rcvd and e.rcvd.code == 4401:
                    print("Room ingest: key rejected (4401); check ROOM_CONFIG, then kill -HUP",
                          flush=True)
                    backoff = ROOM_BACKOFF_MAX_S
                else:
                    print(f"Room ingest: connection closed: {e}", flush=True)
            except Exception as e:
                print(f"Room ingest: {type(e).__name__}: {e}", flush=True)
            if self.acked and backoff < ROOM_BACKOFF_MAX_S:
                backoff = 1.0                # the link worked; retry promptly
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, ROOM_BACKOFF_MAX_S)

    async def _send_loop(self, ws, reader):
        while True:
            try:
                await asyncio.wait_for(self.fresh.wait(), ROOM_HEARTBEAT_S)
            except asyncio.TimeoutError:
                self._heartbeat_read()
            self.fresh.clear()
            if reader.done():
                reader.result()              # re-raises the reader's ConnectionClosed
                raise ConnectionError("room_service closed the socket")
            if self.unacked_since and time.monotonic() - self.unacked_since > ROOM_ACK_TIMEOUT_S:
                raise TimeoutError(f"no lux.ack for {ROOM_ACK_TIMEOUT_S:.0f}s")
            r = self.latest
            if r is None:
                continue
            self.seq += 1
            data = {"sensor": SENSOR_NAME, "lux": r["lux"], "seq": self.seq, "mono_ms": r["mono_ms"]}
            if r["held"]:
                data["held"] = True
            await ws.send(json.dumps({"v": 1, "type": "ingest.lux", "data": data}))
            self._maybe_confirm(r)
            if self.unacked_since is None:
                self.unacked_since = time.monotonic()


async def relay(sensor, capture, state):
    module = sensor.get_module()
    module.set_luminosity(0)

    while True:
        try:
            print("Attempting WebSocket connection...")
            async with websockets.connect(WS_URL) as ws:
                print("Connected. Streaming lux...")
                while True:
                    if sensor.isOnline():
                        lux = sensor.get_currentValue()
                        state["last_lux"] = lux
                        await ws.send(json.dumps({
                            "sensor_name": SENSOR_NAME,
                            "lux": capture.output_lux(lux)
                        }))
                    await asyncio.sleep(0.5)

        except (websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.InvalidStatusCode,
                OSError) as e:
            print(f"WS connection lost: {e}. Retrying in {RETRY_DELAY}s...")
            await asyncio.sleep(RETRY_DELAY)


async def main():
    errmsg = YRefParam()
    if YAPI.RegisterHub("usb", errmsg) != YAPI.SUCCESS:
        raise RuntimeError(f"USB init failed: {errmsg.value}")

    sensor = YLightSensor.FirstLightSensor()
    if sensor is None:
        raise RuntimeError("No Yoctopuce light sensor found")

    # The relay itself never uses timed reports, so a non-OFF frequency here is left over
    # from a capture that was killed without cleanup.
    if sensor.get_reportFrequency() != "OFF":
        print(f"Resetting leftover reportFrequency {sensor.get_reportFrequency()} to OFF", flush=True)
        sensor.set_reportFrequency("OFF")

    capture = Capture(sensor)
    room = RoomSink(sensor, capture)
    state = {"last_lux": None}
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    loop.add_signal_handler(signal.SIGUSR1, lambda: capture.start(state["last_lux"]))
    loop.add_signal_handler(signal.SIGUSR2, lambda: capture.stop(reason="SIGUSR2"))
    loop.add_signal_handler(signal.SIGHUP, lambda: room.configure(load_room_config()))
    room.configure(load_room_config())
    # Let systemd's SIGTERM unwind through the finally below, so a capture is cleaned up.
    loop.add_signal_handler(signal.SIGTERM, main_task.cancel)
    try:
        os.makedirs(CAL_DIR, exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            json.dump({"pid": os.getpid(), "version": VERSION, "started": time.time()}, f)
    except OSError as e:
        print(f"Could not write {STATUS_FILE}: {e}", flush=True)

    try:
        await relay(sensor, capture, state)
    except asyncio.CancelledError:
        print("Stopping (SIGTERM).", flush=True)
    finally:
        await room.close()
        capture.stop(reason="relay exit")
        YAPI.FreeAPI()

asyncio.run(main())
