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
    if a.stream != "pc22-mic":
        sys.exit(f"stream {a.stream!r}: only pc22-mic can be recorded yet (the kiosk recorder comes with its mic)")
    prev = CURRENT.read_text().strip() if CURRENT.exists() else ""
    if prev and read_json(RUNS / prev / "run.json").get("state") == "recording":
        sys.exit(f"{prev} is still recording: 'record stop' first")
    device = a.device
    show = {}
    try:
        show = show_state()
        device = device or show["streams"][a.stream]["sender"]["dev"]
    except Exception as e:                                  # show_service restarting: the mic is still the mic
        print(f"show_service state unreadable ({e}); device {device or FALLBACK_DEVICE!r}")
    device = device or FALLBACK_DEVICE
    name = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + a.stream
    d = RUNS / name
    d.mkdir(parents=True)
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


def record_status(a) -> None:
    d = run_dir(a.run)
    run = read_json(d / "run.json")
    st = read_json(WIN_DIR_WSL / run["run"] / "rec.wav.json") or read_json(d / "rec.json")
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
    for n in ("status", "stop"):
        r.add_parser(n).add_argument("--run")
    f = sub.add_parser("fetch")
    f.add_argument("source", help="a URL, or search words (the first YouTube hit)")
    f.add_argument("--run")
    an = sub.add_parser("analyse")
    an.add_argument("--run")
    an.add_argument("--no-config", action="store_true")
    sub.add_parser("_buslog").add_argument("dir")
    a = ap.parse_args()
    if a.cmd == "record":
        {"start": record_start, "status": record_status, "stop": record_stop}[a.action](a)
    elif a.cmd == "fetch":
        fetch(a)
    elif a.cmd == "analyse":
        analyse(a)
    elif a.cmd == "_buslog":
        asyncio.run(_buslog(pathlib.Path(a.dir)))


if __name__ == "__main__":
    main()
