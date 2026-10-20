#!/usr/bin/env python3
"""Room audio calibration: what the room, the PA and the mic do to music before show_service hears it.

The light-latency calibration's sibling (tools/room/latency_cal). We can't control the source or the room, but we can
measure them: play a known track over the PA, record what the mic hears, fetch the original, and compare. The
comparison (analyse.py) gives the chain's levels, frequency response, reverb, noise, dynamics (AGC, noise
suppression) and what survives for beat detection, plus a room model (roomsim.py) that applies the same degradation
to any audio, so tools/room/beat_eval can test the beat stack on annotated music as this room would deliver it.
Run it now and then, per room and mic.

One run, start to finish (run with ~/.venvs/sm-roundtrip/bin/python):
  calibrate.py record start              # starts recording the mic + logging the live beat clock; play the track now
  calibrate.py record status             # elapsed time, the last second's level, overflows, bus messages
  calibrate.py record stop               # finishes the WAV, moves it into the run dir
  calibrate.py fetch "<YouTube URL or search words>"     # the original, into the run's original/
  calibrate.py analyse                   # analysis/ + model/ in the run dir, and config/room/audio_cal.json
Each takes --run <dir name> to act on a run other than the latest (runs/CURRENT).

What ``record start`` runs, both detached (they outlive this command, and stop on ``record stop``):
- **The recorder**, pc22_record.py on PC22's Windows Python through powershell.exe: the mic show_service's stream
  is using right now (its ``sender.dev``), opened as the agent opens it (WASAPI shared, 48 kHz mono float32), so
  it records what the beat clock hears, and the agent carries on. It writes on C: and ``record stop`` moves it.
- **The bus logger** (``calibrate.py _buslog``): show_service's music.clock, music.hit, music.rhythm, music.features
  and music.context from ws://127.0.0.1:8703/v1/show into bus.jsonl, and GET /api/v1/state at 1 Hz into
  state.jsonl, each line stamped ``rx_ms`` (CLOCK_MONOTONIC ms, show_service's own time base). It reconnects
  through show_service restarts. Those lines let analyse.py score the live clock on the real room's audio.

A run dir (runs/<UTC stamp>-<stream>/): rec.wav (48 kHz mono float32), rec.json (the recorder's sidecar), bus.jsonl,
state.jsonl, run.json, recorder.log, buslog.log, original/, then analysis/ and model/. Recordings never go in git.
Only stream pc22-mic is recordable yet; room-mic (the kiosk) comes with the new mic.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNS = HERE / "runs"
CURRENT = RUNS / "CURRENT"
SHOW_API = "http://127.0.0.1:8703/api/v1"
SHOW_BUS = "ws://127.0.0.1:8703/v1/show"
BUS_WANT = ["music.clock", "music.hit", "music.rhythm", "music.features", "music.context"]
WIN_DIR = r"C:\Users\Greg\AppData\Local\screen-machine\audio-cal"     # the recorder's home on PC22 (LOCALAPPDATA)
WIN_DIR_WSL = pathlib.Path("/mnt/c/Users/Greg/AppData/Local/screen-machine/audio-cal")
MAX_S = 3600.0
FALLBACK_DEVICE = "Echo Cancelling Speakerphone"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_json(p: pathlib.Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def write_json(p: pathlib.Path, obj: dict) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def show_state() -> dict:
    with urllib.request.urlopen(SHOW_API + "/state", timeout=3) as r:
        return json.load(r)


def run_dir(name: str | None) -> pathlib.Path:
    if not name:
        try:
            name = CURRENT.read_text().strip()
        except OSError:
            sys.exit("no current run: start one with 'record start' or pass --run")
    d = RUNS / name
    if not d.is_dir():
        sys.exit(f"no run {name!r} in {RUNS}")
    return d


# ---------------------------------------------------------------------------------------------------- record

def record_start(a) -> None:
    via = a.via if a.via != "auto" else ("pc22" if a.stream == "pc22-mic" else "show")
    if via == "pc22" and a.stream != "pc22-mic":
        sys.exit(f"--via pc22 records PC22's own mic only, not {a.stream!r}: use --via show")
    prev = CURRENT.read_text().strip() if CURRENT.exists() else ""
    if prev and read_json(RUNS / prev / "run.json").get("state") == "recording":
        sys.exit(f"{prev} is still recording: 'record stop' first")
    device = a.device
    show = {}
    try:
        show = show_state()
        device = device or show["streams"][a.stream]["sender"]["dev"]
    except Exception as e:                                  # show_service restarting: the mic is still the mic
        if via == "show":
            sys.exit(f"show_service state unreadable ({e}): --via show records through it, so it must be up")
        print(f"show_service state unreadable ({e}); device {device or FALLBACK_DEVICE!r}")
    device = device or FALLBACK_DEVICE
    name = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + a.stream
    d = RUNS / name
    d.mkdir(parents=True)
    if via == "show":
        _record_start_show(a, name, d, device, show)
        return
    wdir = WIN_DIR_WSL / name
    wdir.mkdir(parents=True)
    shutil.copy2(HERE / "pc22_record.py", WIN_DIR_WSL / "pc22_record.py")
    win_out = f"{WIN_DIR}\\{name}\\rec.wav"
    mic = show.get("mic") or {}
    run = {"run": name, "stream": a.stream, "device": device, "state": "recording", "started_utc": utc_now(),
           "note": a.note, "win_out": win_out, "mic_switch": {k: mic.get(k) for k in ("on", "muted", "why")},
           "original": None}
    write_json(d / "run.json", run)
    CURRENT.write_text(name + "\n")

    cmd = (f"py -3.12 {WIN_DIR}\\pc22_record.py --out '{win_out}' --device '{device}' --max-s {a.max_s:.0f}")
    with open(d / "recorder.log", "w") as log:
        rec = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", cmd], cwd="/mnt/c", stdout=log,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    with open(d / "buslog.log", "w") as log:
        bus = subprocess.Popen([sys.executable, str(HERE / "calibrate.py"), "_buslog", str(d)], stdout=log,
                               stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    run.update(recorder_pid=rec.pid, buslog_pid=bus.pid)
    write_json(d / "run.json", run)
    for _ in range(50):                                     # wait for the first status line (the mic is open)
        st = read_json(wdir / "rec.wav.json")
        if st.get("state") == "recording" or rec.poll() is not None:
            break
        time.sleep(0.2)
    if rec.poll() is not None:
        run["state"] = "failed"
        write_json(d / "run.json", run)
        sys.exit(f"recorder exited: {(d / 'recorder.log').read_text()[-800:]}")
    print(f"recording {name}: {device}, up to {a.max_s / 60:.0f} min. Play the track now; 'record stop' when done.")


def _record_start_show(a, name: str, d: pathlib.Path, device: str, show: dict) -> None:
    """``--via show``: record the stream as show_service hears it, from its levels feed (``_showtap``)."""
    mic = show.get("mic") or {}
    run = {"run": name, "stream": a.stream, "device": device, "via": "show", "state": "recording",
           "started_utc": utc_now(), "note": a.note, "mic_switch": {k: mic.get(k) for k in ("on", "muted", "why")},
           "original": None}
    write_json(d / "run.json", run)
    CURRENT.write_text(name + "\n")
    procs = {}
    for key, args in (("recorder_pid", ["_showtap", str(d), a.stream, str(a.max_s)]), ("buslog_pid", ["_buslog", str(d)])):
        with open(d / ("recorder.log" if key == "recorder_pid" else "buslog.log"), "w") as log:
            procs[key] = subprocess.Popen([sys.executable, str(HERE / "calibrate.py"), *args], stdout=log,
                                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    run.update({k: p.pid for k, p in procs.items()})
    write_json(d / "run.json", run)
    rec = procs["recorder_pid"]
    for _ in range(50):                                     # wait for the first second of audio
        st = read_json(d / "rec.json")
        if st.get("state") == "recording" and st.get("frames", 0) > 0 or rec.poll() is not None:
            break
        time.sleep(0.2)
    st = read_json(d / "rec.json")
    if rec.poll() is not None or not st.get("frames"):
        run["state"] = "failed"
        write_json(d / "run.json", run)
        rec.poll() is None and rec.terminate()
        (d / "buslog.stop").touch()
        sys.exit(f"no audio from show_service's levels feed for {a.stream!r} (is it the active stream?): "
                 f"{(d / 'recorder.log').read_text()[-800:]}")
    print(f"recording {name}: {a.stream} ({device}) through show_service, up to {a.max_s / 60:.0f} min. "
          f"Play the track now; 'record stop' when done.")


def record_status(a) -> None:
    d = run_dir(a.run)
    run = read_json(d / "run.json")
    st = read_json(d / "rec.json") if run.get("via") == "show" else \
        read_json(WIN_DIR_WSL / run["run"] / "rec.wav.json") or read_json(d / "rec.json")
    n = sum(1 for _ in open(d / "bus.jsonl")) if (d / "bus.jsonl").exists() else 0
    print(f"{run['run']}: {run.get('state')}; {st.get('seconds', 0):.0f} s recorded, last second peak "
          f"{st.get('last_peak_dbfs')} dBFS / rms {st.get('last_rms_dbfs')} dBFS, {st.get('overflows')} overflows; "
          f"{n} bus lines")


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def record_stop(a) -> None:
    d = run_dir(a.run)
    run = read_json(d / "run.json")
    if run.get("via") == "show":
        (d / "tap.stop").touch()
        (d / "buslog.stop").touch()
        t_end = time.monotonic() + 15
        while time.monotonic() < t_end and (_alive(run.get("recorder_pid")) or _alive(run.get("buslog_pid"))):
            time.sleep(0.25)
        st = read_json(d / "rec.json")
        if st.get("state") != "stopped":
            print("warning: the recorder did not confirm its stop; taking rec.wav as it is (its header is patched "
                  "every second)")
        run.update(state="stopped", stopped_utc=utc_now(), seconds=st.get("seconds"), gaps=st.get("gaps"),
                   gap_s=st.get("gap_s"))
        write_json(d / "run.json", run)
        n = sum(1 for _ in open(d / "bus.jsonl")) if (d / "bus.jsonl").exists() else 0
        print(f"stopped {run['run']}: {st.get('seconds', 0):.1f} s, {st.get('gaps')} gaps ({st.get('gap_s')} s), "
              f"{n} bus lines. Next: fetch \"<the track's URL or name>\"")
        return
    wdir = WIN_DIR_WSL / run["run"]
    (wdir / "rec.wav.stop").touch()
    (d / "buslog.stop").touch()
    t_end = time.monotonic() + 15
    while time.monotonic() < t_end:
        if read_json(wdir / "rec.wav.json").get("state") == "stopped" and not _alive(run.get("buslog_pid")):
            break
        time.sleep(0.25)
    st = read_json(wdir / "rec.wav.json")
    if st.get("state") != "stopped":
        print("warning: the recorder did not confirm its stop; taking the file as it is (its header is patched "
              "every second)")
    for src, dst in ((wdir / "rec.wav", d / "rec.wav"), (wdir / "rec.wav.json", d / "rec.json")):
        if src.exists():
            shutil.move(str(src), str(dst))
    for p in wdir.glob("*"):
        p.unlink()
    wdir.rmdir()
    run.update(state="stopped", stopped_utc=utc_now(), seconds=st.get("seconds"), overflows=st.get("overflows"))
    write_json(d / "run.json", run)
    n = sum(1 for _ in open(d / "bus.jsonl")) if (d / "bus.jsonl").exists() else 0
    print(f"stopped {run['run']}: {st.get('seconds', 0):.1f} s, {st.get('overflows')} overflows, {n} bus lines. "
          f"Next: fetch \"<the track's URL or name>\"")


# ---------------------------------------------------------------------------------------------------- show tap

TAP_N = 2048                # samples a levels frame carries (levels.WAVE_MAX)
TAP_PKT = 128               # samples a VBAN packet carries (the counter "c" counts packets)
TAP_MIN_OVERLAP = 64        # samples two frames must share to be stitched on content


def tap_shift(prev, x, est: int):
    """How many new samples frame ``x`` carries after frame ``prev`` (both the newest TAP_N samples of the ring): the
    shift k at which x's head equals prev's tail exactly over at least TAP_MIN_OVERLAP samples, found by locating
    prev's last 32 samples in x and verifying the whole overlap; of several (a silent or periodic tail), the one
    nearest the counter's ``est``. 0 if nothing is new, None if no shift fits (a splice or a gap)."""
    import numpy as np
    n = len(x)
    if np.array_equal(x, prev):
        return 0
    sig = prev[-32:]
    win = np.lib.stride_tricks.sliding_window_view(x[:n - TAP_MIN_OVERLAP + 32], 32)
    pos = np.flatnonzero((win == sig).all(axis=1))
    ks = [n - 32 - int(p) for p in pos]
    ks = [k for k in ks if 0 < k <= n - TAP_MIN_OVERLAP and np.array_equal(x[:n - k], prev[k:])]
    return min(ks, key=lambda k: abs(k - est)) if ks else None


async def _showtap(d: pathlib.Path, stream: str, max_s: float) -> None:
    """Record ``stream`` as show_service hears it: its levels feed (``WS /v1/levels?wave=2048``, show_service/
    levels.py), up to 60 frames a second, each the newest 2048 samples of the active stream (INT16, exactly what the
    receiver decoded), with the newest packet's counter ``c`` and its arrival time ``a`` (CLOCK_MONOTONIC s).

    Frames overlap (60 Hz x 800 new samples against 2048 carried), so they are stitched on content: the new frame's
    head must equal the last frame's tail sample for sample. ``c`` gives the expected shift (packets x 128); it is
    read a moment before the samples (levels' slot, then its ring), so the true shift can differ by whole packets,
    and those are tried next (``race``). No exact match: a gap (frames dropped, a stall over 43 ms), filled with
    zeros for the samples ``c`` says are missing (``gaps``, ``gap_s``). Frames of another stream are skipped and a
    reconnect after a show_service restart is a gap timed by the clock. Writes rec.wav (48 kHz mono float32), rec.json
    (status every second, then ``state: stopped``) and tapmap.jsonl (each frame's end sample, ``c`` and ``a``: the
    map from recording time to show_service's clock, the one bus.jsonl's ``rx_ms`` uses)."""
    import base64
    import aiohttp
    import numpy as np
    sys.path.insert(0, str(HERE))
    from pc22_record import FloatWav                         # the same incremental float WAV (header patched)

    fs = 48000
    wav = FloatWav(str(d / "rec.wav"), fs)
    tmap = open(d / "tapmap.jsonl", "w", buffering=1)
    stop = d / "tap.stop"
    st = {"state": "recording", "stream": stream, "via": "show", "fs": fs, "started_utc": utc_now(), "frames": 0,
          "gaps": 0, "gap_s": 0.0, "race": 0, "other_stream_frames": 0, "reconnects": 0,
          "overflows": 0}
    prev = prev_c = None
    t_prev = None
    t0 = time.monotonic()
    sec: list = []
    last_status = 0.0
    url = SHOW_API.replace("http://", "ws://").replace("/api/v1", "") + "/v1/levels?wave=%d" % TAP_N

    def write(x) -> None:
        y = x.astype(np.float32) / 32767.0
        wav.write(y)
        sec.append(y)

    def status(state: str) -> None:
        nonlocal sec
        if sec:
            y = np.concatenate(sec)
            sec = []
            st["last_peak_dbfs"] = round(20 * np.log10(max(float(np.max(np.abs(y))), 1e-9)), 1)
            st["last_rms_dbfs"] = round(10 * np.log10(max(float(np.mean(y.astype(np.float64) ** 2)), 1e-18)), 1)
        st.update(state=state, seconds=round(wav.frames / fs, 3), t_utc=utc_now(), overflows=st["gaps"])
        if state == "stopped":
            st["stopped_utc"] = utc_now()
        write_json(d / "rec.json", st)

    async with aiohttp.ClientSession() as http:
        while not stop.exists() and time.monotonic() - t0 < max_s:
            try:
                async with http.ws_connect(url, heartbeat=10.0, max_msg_size=1 << 22) as ws:
                    while not stop.exists() and time.monotonic() - t0 < max_s:
                        try:
                            m = await ws.receive(timeout=1.0)
                        except asyncio.TimeoutError:
                            m = None
                        now = time.monotonic()
                        if now - last_status >= 1.0:
                            last_status = now
                            wav.patch()
                            status("recording")
                        if m is None:
                            continue
                        if m.type != aiohttp.WSMsgType.TEXT:
                            break
                        f = json.loads(m.data)
                        if "wb" not in f:
                            continue
                        if f.get("s") != stream:
                            st["other_stream_frames"] += 1
                            prev = None                      # levels' ring restarts on a stream change
                            continue
                        x = np.frombuffer(base64.b64decode(f["wb"]), "<i2")
                        c = int(f["c"])
                        if prev is None:
                            if t_prev is not None:           # after a reconnect or another stream: timed by the clock
                                miss = max(0, int(round((now - t_prev) * fs)) - len(x))
                                if miss:
                                    st["gaps"] += 1
                                    st["gap_s"] = round(st["gap_s"] + miss / fs, 3)
                                    wav.write(np.zeros(miss, np.float32))
                            write(x)
                        else:
                            est = ((c - prev_c) % (1 << 32)) * TAP_PKT
                            k = tap_shift(prev, x, est)
                            if k is not None:
                                st["race"] += k != est
                                if k:
                                    write(x[TAP_N - k:])
                            elif est <= TAP_N - TAP_MIN_OVERLAP:   # should overlap but doesn't (a receiver splice):
                                write(x[TAP_N - max(est, 0):])     # trust the counter's shift
                                st["unmatched"] = st.get("unmatched", 0) + 1
                            else:                            # no overlap: frames dropped or a stall
                                miss = max(0, est - TAP_N)
                                st["gaps"] += 1
                                st["gap_s"] = round(st["gap_s"] + miss / fs, 3)
                                wav.write(np.zeros(miss, np.float32))
                                write(x)
                        prev, prev_c, t_prev = x, c, now
                        st["frames"] += 1
                        tmap.write(json.dumps({"n_end": wav.frames, "c": c, "a": f.get("a"), "t": f.get("t"),
                                               "rx": round(now, 6)}) + "\n")
            except Exception as e:
                print(f"{utc_now()} levels feed: {type(e).__name__}: {e}; retrying", flush=True)
            if not stop.exists():
                st["reconnects"] += 1
                prev = None
                await asyncio.sleep(1.0)
    wav.close()
    tmap.close()
    status("stopped")


# ---------------------------------------------------------------------------------------------------- bus logger

async def _buslog(d: pathlib.Path) -> None:
    import aiohttp

    stop = d / "buslog.stop"
    out = open(d / "bus.jsonl", "a", buffering=1)
    state_out = open(d / "state.jsonl", "a", buffering=1)

    def rx_ms() -> float:
        return time.monotonic() * 1000.0

    async def poll_state(http) -> None:
        stream = read_json(d / "run.json").get("stream", "pc22-mic")
        while not stop.exists():
            try:
                async with http.get(SHOW_API + "/state", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    s = await r.json()
                sm = dict((s.get("streams") or {}).get(stream) or {})
                if isinstance(sm.get("audio"), dict):
                    sm["audio"] = {k: v for k, v in sm["audio"].items() if k != "trace"}
                state_out.write(json.dumps({"rx_ms": rx_ms(), "clock": s.get("clock"), "stream": sm,
                                            "mic": s.get("mic")}) + "\n")
            except Exception as e:
                state_out.write(json.dumps({"rx_ms": rx_ms(), "error": f"{type(e).__name__}: {e}"}) + "\n")
            await asyncio.sleep(1.0)

    async def bus(http) -> None:
        while not stop.exists():
            try:
                async with http.ws_connect(SHOW_BUS, heartbeat=10.0, max_msg_size=1 << 22) as ws:
                    await ws.send_json({"type": "hello", "v": 1, "role": "renderer",
                                        "client_id": f"audio-cal-{os.getpid()}", "want": BUS_WANT})
                    print(f"{utc_now()} bus connected", flush=True)
                    while not stop.exists():
                        try:
                            m = await ws.receive(timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if m.type != aiohttp.WSMsgType.TEXT:
                            break
                        out.write('{"rx_ms":%.3f,"msg":%s}\n' % (rx_ms(), m.data))
            except Exception as e:
                print(f"{utc_now()} bus: {type(e).__name__}: {e}; retrying", flush=True)
            await asyncio.sleep(1.0)

    async with aiohttp.ClientSession() as http:
        await asyncio.gather(poll_state(http), bus(http))
    out.close()
    state_out.close()


# ---------------------------------------------------------------------------------------------------- fetch

def fetch(a) -> None:
    import yt_dlp

    d = run_dir(a.run)
    od = d / "original"
    od.mkdir(exist_ok=True)
    src = a.source if a.source.startswith(("http://", "https://")) else f"ytsearch1:{a.source}"
    opts = {"format": "bestaudio/best", "outtmpl": str(od / "source.%(ext)s"), "noplaylist": True, "quiet": True,
            "no_warnings": True, "noprogress": True}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(src, download=True)
        if "entries" in info:
            info = info["entries"][0]
    got = next(od.glob("source.*"))
    wav = od / "original-48k.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(got), "-ar", "48000", "-c:a", "pcm_f32le",
                    str(wav)], check=True)
    run = read_json(d / "run.json")
    run["original"] = {"source": info.get("webpage_url") or a.source, "query": a.source, "title": info.get("title"),
                       "uploader": info.get("uploader"), "duration_s": info.get("duration"),
                       "file": f"original/{got.name}", "wav": "original/original-48k.wav", "fetched_utc": utc_now()}
    write_json(d / "run.json", run)
    print(f"original: {info.get('title')!r} ({info.get('duration')} s) from {run['original']['source']}")


def analyse(a) -> None:
    sys.path.insert(0, str(HERE))
    import analyse as an                                    # analyse.py, next to this file

    an.analyse(run_dir(a.run), write_config=not a.no_config, cpu_lock=True)    # ~10 min of CPU: one at a time


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record").add_subparsers(dest="action", required=True)
    s = r.add_parser("start")
    s.add_argument("--stream", default="pc22-mic")
    s.add_argument("--device", help="the WASAPI input (default: what show_service's stream is using now)")
    s.add_argument("--max-s", type=float, default=MAX_S)
    s.add_argument("--note", default="", help="a line for run.json, e.g. where the PA and the mic are")
    s.add_argument("--via", choices=("auto", "pc22", "show"), default="auto",
                   help="pc22: PC22's recorder (pc22-mic only); show: show_service's levels feed (any active "
                        "stream, e.g. room-mic on the kiosk); auto: pc22 for pc22-mic, else show")
    for n in ("status", "stop"):
        r.add_parser(n).add_argument("--run")
    f = sub.add_parser("fetch")
    f.add_argument("source", help="a URL, or search words (the first YouTube hit)")
    f.add_argument("--run")
    an = sub.add_parser("analyse")
    an.add_argument("--run")
    an.add_argument("--no-config", action="store_true")
    sub.add_parser("_buslog").add_argument("dir")
    t = sub.add_parser("_showtap")
    t.add_argument("dir")
    t.add_argument("stream")
    t.add_argument("max_s", type=float)
    a = ap.parse_args()
    if a.cmd == "record":
        {"start": record_start, "status": record_status, "stop": record_stop}[a.action](a)
    elif a.cmd == "fetch":
        fetch(a)
    elif a.cmd == "analyse":
        analyse(a)
    elif a.cmd == "_buslog":
        asyncio.run(_buslog(pathlib.Path(a.dir)))
    elif a.cmd == "_showtap":
        asyncio.run(_showtap(pathlib.Path(a.dir), a.stream, a.max_s))


if __name__ == "__main__":
    main()
