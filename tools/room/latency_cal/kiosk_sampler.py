#!/usr/bin/env python3
"""Fast, timestamped Yocto-Light sampler for latency calibration.

Runs ON THE KIOSK BOX (with ~/.venv/bin/python, which has the yoctopuce package).

The Yoctopuce USB API is exclusive to one process, so light-relay.service must be
stopped first. The sampler:
  1. saves the sensor's current settings to a state file (kept if a run crashes, so
     `restore` can put them back later);
  2. switches the sensor to fast reporting (measure type, IMMEDIATE advertising,
     timed reports at --freq);
  3. logs every timed report as one JSON line with the box's wall-clock receive time
     (time.time_ns(), the same CLOCK_REALTIME that Chrome's Date.now() reads);
  4. restores the original settings on exit (deadline, SIGTERM, SIGINT or SIGHUP).
Nothing is saved to the module's flash.

Modes:
  probe    try several report frequencies and measure types; print achieved rates
  run      sample until --seconds elapse or a signal arrives
  restore  re-apply the settings saved in the state file
"""
import argparse
import json
import os
import signal
import statistics
import sys
import time

from yoctopuce.yocto_api import YAPI, YRefParam, YSensor
from yoctopuce.yocto_lightsensor import YLightSensor

DEFAULT_STATE = os.path.expanduser("~/latency_cal/sensor_settings.json")
MEASURE_TYPES = {
    "human_eye": YLightSensor.MEASURETYPE_HUMAN_EYE,
    "wide_spectrum": YLightSensor.MEASURETYPE_WIDE_SPECTRUM,
    "infrared": YLightSensor.MEASURETYPE_INFRARED,
    "high_rate": YLightSensor.MEASURETYPE_HIGH_RATE,
    "high_energy": YLightSensor.MEASURETYPE_HIGH_ENERGY,
    "high_resolution": YLightSensor.MEASURETYPE_HIGH_RESOLUTION,
}


def emit(obj, stream=sys.stdout):
    stream.write(json.dumps(obj) + "\n")
    stream.flush()


def connect():
    err = YRefParam()
    if YAPI.RegisterHub("usb", err) != YAPI.SUCCESS:
        sys.exit(f"USB init failed (is light-relay.service still running?): {err.value}")
    sensor = YLightSensor.FirstLightSensor()
    if sensor is None:
        YAPI.FreeAPI()
        sys.exit("no Yoctopuce light sensor found")
    return sensor


def read_settings(sensor):
    return {
        "measureType": sensor.get_measureType(),
        "advMode": sensor.get_advMode(),
        "reportFrequency": sensor.get_reportFrequency(),
    }


def apply_settings(sensor, cfg):
    """Apply each setting; return the ones the module rejected."""
    failed = {}
    setters = {
        "measureType": sensor.set_measureType,
        "advMode": sensor.set_advMode,
        "reportFrequency": sensor.set_reportFrequency,
    }
    for key, value in cfg.items():
        try:
            if setters[key](value) != YAPI.SUCCESS:
                failed[key] = value
        except Exception as exc:  # the API raises on some firmware for unsupported values
            failed[key] = f"{value}: {exc}"
    return failed


def save_originals(sensor, state_path):
    """Return the true original settings, persisting them before anything is changed.

    If a state file already exists, a previous run did not restore cleanly, so the
    file (not the module's current settings) holds the originals."""
    if os.path.exists(state_path):
        with open(state_path) as f:
            return json.load(f)["settings"]
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    original = read_settings(sensor)
    with open(state_path, "w") as f:
        json.dump({"serial": sensor.get_module().get_serialNumber(),
                   "saved_at": time.time(), "settings": original}, f)
    return original


def restore(sensor, original, state_path):
    failed = apply_settings(sensor, original)
    now = read_settings(sensor)
    ok = not failed and now == original
    if ok and os.path.exists(state_path):
        os.remove(state_path)
    return ok, now, failed


def sample(sensor, seconds, on_report, stop_flag):
    """Pump YAPI events until the deadline or a stop request."""
    sensor.registerTimedReportCallback(on_report)
    err = YRefParam()
    deadline = time.monotonic() + seconds
    try:
        while not stop_flag["stop"] and time.monotonic() < deadline:
            YAPI.Sleep(2, err)
    finally:
        sensor.registerTimedReportCallback(None)


def stats(times_ns):
    if len(times_ns) < 3:
        return {"n": len(times_ns)}
    gaps = sorted((b - a) / 1e6 for a, b in zip(times_ns, times_ns[1:]))
    q = lambda p: gaps[min(len(gaps) - 1, int(p * len(gaps)))]
    return {"n": len(times_ns), "rate_hz": round(1000 / statistics.median(gaps), 1),
            "gap_ms": {"p10": round(q(0.1), 2), "p50": round(q(0.5), 2),
                       "p90": round(q(0.9), 2), "max": round(gaps[-1], 2)}}


def cmd_probe(args):
    sensor = connect()
    original = save_originals(sensor, args.state)
    stop = {"stop": False}
    results = []
    try:
        for mtype in args.measure_types:
            for freq in args.freqs:
                got = []
                failed = apply_settings(sensor, {"measureType": MEASURE_TYPES[mtype],
                                                 "advMode": YSensor.ADVMODE_IMMEDIATE,
                                                 "reportFrequency": freq})
                sample(sensor, args.seconds,
                       lambda fn, m: got.append((time.time_ns(), m.get_averageValue())), stop)
                values = [v for _, v in got]
                results.append({"measure_type": mtype, "freq": freq, "rejected": failed,
                                "applied": read_settings(sensor),
                                "distinct_values": len(set(values)),
                                "lux_median": statistics.median(values) if values else None,
                                **stats([t for t, _ in got])})
                emit(results[-1])
    finally:
        ok, now, failed = restore(sensor, original, args.state)
        emit({"restored": ok, "settings": now, "failed": failed})
        YAPI.FreeAPI()


def cmd_run(args):
    sensor = connect()
    original = save_originals(sensor, args.state)
    stop = {"stop": False}

    def on_signal(signum, _frame):
        stop["stop"] = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, on_signal)
    if args.pidfile:
        with open(args.pidfile, "w") as f:
            f.write(str(os.getpid()))

    out = open(args.out, "w", buffering=1)
    times = []

    def on_report(_fn, measure):
        t = time.time_ns()
        times.append(t)
        out.write(json.dumps({"t_ns": t, "v": measure.get_averageValue(),
                              "lo": measure.get_minValue(), "hi": measure.get_maxValue(),
                              "m_end": measure.get_endTimeUTC()}) + "\n")

    try:
        failed = apply_settings(sensor, {"measureType": MEASURE_TYPES[args.measure_type],
                                         "advMode": YSensor.ADVMODE_IMMEDIATE,
                                         "reportFrequency": args.freq})
        emit({"kind": "header", "serial": sensor.get_module().get_serialNumber(),
              "firmware": sensor.get_module().get_firmwareRelease(),
              "original": original, "applied": read_settings(sensor), "rejected": failed,
              "t_ns": time.time_ns()}, out)
        sample(sensor, args.seconds, on_report, stop)
    finally:
        ok, now, failed = restore(sensor, original, args.state)
        emit({"kind": "footer", "restored": ok, "settings": now, "failed": failed,
              "t_ns": time.time_ns(), **stats(times)}, out)
        out.close()
        YAPI.FreeAPI()
        if args.pidfile and os.path.exists(args.pidfile):
            os.remove(args.pidfile)


def cmd_restore(args):
    if not os.path.exists(args.state):
        emit({"restored": True, "note": "no saved state; nothing to restore"})
        return
    sensor = connect()
    with open(args.state) as f:
        original = json.load(f)["settings"]
    try:
        ok, now, failed = restore(sensor, original, args.state)
        emit({"restored": ok, "settings": now, "failed": failed})
    finally:
        YAPI.FreeAPI()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=DEFAULT_STATE)
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("probe")
    p.add_argument("--seconds", type=float, default=3.0)
    p.add_argument("--freqs", nargs="+", default=["100/s", "50/s", "25/s", "10/s"])
    p.add_argument("--measure-types", nargs="+", default=["high_rate", "human_eye"],
                   choices=list(MEASURE_TYPES))

    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--pidfile")
    r.add_argument("--seconds", type=float, default=300.0,
                   help="hard deadline; the sensor is restored even if the caller dies")
    r.add_argument("--freq", default="100/s")
    r.add_argument("--measure-type", default="high_rate", choices=list(MEASURE_TYPES))

    sub.add_parser("restore")

    args = ap.parse_args()
    {"probe": cmd_probe, "run": cmd_run, "restore": cmd_restore}[args.mode](args)


if __name__ == "__main__":
    main()
