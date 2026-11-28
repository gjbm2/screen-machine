"""utils.gpu_watch: where this machine's GPU (and CPU) time goes, every 2 s, kept so a slow moment can be looked at
after it has passed (owner, 27 Nov: "when karaoke is parsing a couple of tracks, also rendering and playing, gpu gets
a bit hammered sometimes... monitoring so we can debug where the cycles are going and what to throttle").

Each sample has four views of the same 2 s:
  gpu    the card as a whole (nvidia-smi): busy %, memory, NVENC/NVDEC %, clock, temperature, power, throttle reasons
  win    Windows' own split by process and engine ("brave|3d", "dwm|3d", "vmwp|videoencode", ...) from
         tools/gpu_watch/engines.ps1. Everything WSL runs on the GPU (the renderer's CUDA, torch, NVENC) is one
         process there, vmwp; WSL's driver itself cannot say which Linux process used it.
  render each live stream session's GPU span x frame rate: the renderer's share of the GPU, measured by itself
  jobs   this repo's processes that use the GPU or a lot of CPU (karaoke prep, renderer, encoder, show), with CPU %
lanes() splits a sample into who-is-using-it: the renderer, the rest of WSL's GPU work (karaoke prep, the show's
models), the browser, the Windows desktop, other Windows, plus the video engines.

In memory: the last hour (GET /api/perf/gpu). On disk: logs/gpu_watch.jsonl, ~25 MB a day, one older file kept.
``python -m utils.gpu_watch report --minutes 60`` reads the file: shares, and each run at 95 %+ with what ran then.
Started by app.py; it does nothing where nvidia-smi is missing, and skips the Windows view where powershell.exe is.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import smconfig

SAMPLE_S = 2.0
RING = 1800                                  # 1 h in memory
LOG = smconfig.REPO_ROOT / "logs" / "gpu_watch.jsonl"
LOG_MAX = 32 * 1024 * 1024
ENGINES_PS1 = smconfig.REPO_ROOT / "tools" / "gpu_watch" / "engines.ps1"
HOT_PCT = 95                                 # the card this busy or more is "hammered"
JOB_CPU_MIN = 3.0                            # an unlabelled process is listed only above this CPU %

#: This repo's processes, by a pattern on the command line: (pattern, label, uses the GPU).
JOBS: list[tuple[re.Pattern, str, bool]] = [(re.compile(p), lab, g) for p, lab, g in [
    (r"tools/karaoke/separate\.py", "Removing vocals", True),
    (r"tools/karaoke/transcribe\.py", "Hearing words", True),
    (r"tools/karaoke/align\.py", "Timing words", True),
    (r"tools/karaoke/refine\.py", "Careful lyrics pass", True),
    (r"tools/karaoke/beats\.py", "Song beats", False),
    (r"-m utils\.song_shape", "Song shape", False),
    (r"-m show_service\.brief", "Song brief", False),
    (r"yt-dlp|yt_dlp", "Download", False),
    (r"-m stream_service\b", "Renderer", True),
    (r"ffmpeg .*_nvenc", "Stream encoder", True),
    (r"ffmpeg .*karaoke/pictures", "Song video decode", False),
    (r"ffmpeg", "ffmpeg", False),
    (r"-m show_service\.ml\.worker", "Show models", True),
    (r"-m show_service\.beatproc", "Beat tracker", False),
    (r"-m show_service\b", "Show", False),
    (r"-m dot_service\b", "Dot tags", False),
    (r"\bapp\.py\b", "Flask", False),
    (r"\bollama\b", "Local AI", True),
    (r"chrome.*--headless", "Headless browser", False),
]]
#: The karaoke preparation steps that use the GPU.
PREP = frozenset({"Removing vocals", "Hearing words", "Timing words", "Careful lyrics pass"})
_SID = re.compile(r"/songs/(k[0-9a-f]{6,})\b")

BROWSERS = ("brave", "chrome", "msedge", "firefox", "opera", "vivaldi")


# --- the card --------------------------------------------------------------------------------------------------

_SMI_FIELDS = ("utilization.gpu,memory.used,memory.total,utilization.encoder,utilization.decoder,clocks.sm,"
               "clocks.max.sm,temperature.gpu,power.draw,clocks_event_reasons.active")
_THROTTLE = {0x4: "power cap", 0x8: "slowdown", 0x20: "thermal", 0x40: "thermal", 0x80: "power brake",
             0x100: "display clock"}


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except ValueError:
        return None


def parse_smi(line: str) -> dict:
    """One nvidia-smi csv row (noheader, nounits) -> {util, mem, mem_total, enc, dec, mhz, max_mhz, temp, w, thr}."""
    f = [x.strip() for x in line.split(",")]
    if len(f) < 10:
        return {}
    keys = ("util", "mem", "mem_total", "enc", "dec", "mhz", "max_mhz", "temp", "w")
    out: dict[str, Any] = {k: _num(v) for k, v in zip(keys, f)}
    try:
        bits = int(f[9], 16)
    except ValueError:
        bits = 0
    out["thr"] = sorted({w for b, w in _THROTTLE.items() if bits & b})
    return out


def card() -> dict:
    if not shutil.which("nvidia-smi"):
        return {}
    try:
        p = subprocess.run(["nvidia-smi", f"--query-gpu={_SMI_FIELDS}", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    rows = [r for r in (p.stdout or "").splitlines() if r.strip()]
    return parse_smi(rows[0]) if rows else {}


# --- Windows' split --------------------------------------------------------------------------------------------

class WinEngines:
    """tools/gpu_watch/engines.ps1 kept running; latest() is its newest line, or {} if it is older than 6 s."""

    def __init__(self) -> None:
        self._latest: dict = {}
        self._p: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_try = 0.0

    def available(self) -> bool:
        return bool(shutil.which("powershell.exe") and shutil.which("wslpath") and ENGINES_PS1.exists())

    def _start(self) -> None:
        win = subprocess.run(["wslpath", "-w", str(ENGINES_PS1)], capture_output=True, text=True).stdout.strip()
        self._p = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self._p,), name="gpu-watch-win", daemon=True).start()

    def _read(self, p: subprocess.Popen) -> None:
        for line in p.stdout or []:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            with self._lock:
                self._latest = {"at": time.time(), "u": d.get("u") or {}}

    def latest(self) -> dict:
        now = time.time()
        if (self._p is None or self._p.poll() is not None) and now >= self._next_try and self.available():
            self._next_try = now + 30
            try:
                self._start()
            except OSError:
                pass
        with self._lock:
            d = self._latest
        return d["u"] if d and now - d["at"] < 6 else {}

    def stop(self) -> None:
        if self._p and self._p.poll() is None:
            self._p.terminate()


# --- the renderer's own measure -----------------------------------------------------------------------------

def render_share(diag: dict) -> dict:
    """{screen: {ms, fps, pct, late, scene}} from the renderer's GET /api/diag: pct = GPU span (p50) x frames a
    second, the share of the GPU's time its frames take."""
    out = {}
    for s in diag.get("sessions") or []:
        if s.get("state") != "live":
            continue
        w = s.get("window") or {}
        ms = ((w.get("render_ms") or {}).get("p50"))
        fps = w.get("content_fps") or s.get("fps")
        if ms is None or not fps:
            continue
        out[s.get("screen") or s.get("session")] = {
            "ms": round(float(ms), 2), "fps": round(float(fps), 1), "pct": round(min(100.0, ms * fps / 10.0), 1),
            "late": w.get("late_pct"), "scene": s.get("scene")}
    return out


def renderer() -> dict:
    try:
        with urllib.request.urlopen(smconfig.url("room") + "/renderer/api/diag", timeout=1.5) as r:
            return render_share(json.loads(r.read()))
    except Exception:
        return {}


# --- this repo's processes ---------------------------------------------------------------------------------

_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def label(cmd: str) -> Optional[tuple[str, bool]]:
    for pat, lab, gpu in JOBS:
        if pat.search(cmd):
            return lab, gpu
    return None


class Procs:
    """CPU % (of one core) since the last call, summed by label and song: the labelled processes, and any other
    busy one under its program's name."""

    def __init__(self) -> None:
        self._prev: dict[int, tuple[float, int]] = {}

    def sample(self, name_of: Callable[[str], Optional[str]] = lambda sid: None) -> list[dict]:
        now = time.monotonic()
        cur: dict[int, tuple[float, int]] = {}
        jobs: dict[tuple, dict] = {}
        me = os.getpid()
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            pid = int(d)
            try:
                with open(f"/proc/{d}/stat", "rb") as f:
                    st = f.read().rsplit(b")", 1)[1].split()
                ticks = int(st[11]) + int(st[12])
            except (OSError, IndexError, ValueError):
                continue
            cur[pid] = (now, ticks)
            p = self._prev.get(pid)
            if not p or now - p[0] <= 0:
                continue
            cpu = (ticks - p[1]) / _TCK / (now - p[0]) * 100.0
            try:
                with open(f"/proc/{d}/cmdline", "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
            except OSError:
                continue
            if not cmd:
                continue
            lab = label(cmd)
            if lab is None and (cpu < JOB_CPU_MIN or pid == me):
                continue
            if lab is None and ("vscode-server" in cmd or "/.vscode" in cmd):
                lab = ("Editor", False)
            name = lab[0] if lab else os.path.basename(cmd.split()[0])[:40]
            m = _SID.search(cmd)
            sid = m.group(1) if m else None
            if cpu < 0.5 and not (sid or name in PREP):
                continue
            j = jobs.get((name, sid))
            if j is None:
                j = jobs[(name, sid)] = {"l": name, "cpu": 0.0, "n": 0}
                if lab and lab[1]:
                    j["gpu"] = True
                if sid:
                    j["sid"] = sid
                    n = name_of(sid)
                    if n:
                        j["song"] = n
            j["cpu"] += cpu
            j["n"] += 1
        self._prev = cur
        out = sorted(jobs.values(), key=lambda j: -j["cpu"])
        for j in out:
            j["cpu"] = round(j["cpu"], 1)
        return out


# --- one sample, and who it was ----------------------------------------------------------------------------

def lanes(s: dict) -> dict:
    """A sample's GPU split into who used it (% of the GPU's time; 3D/compute engine), plus the video engines."""
    win = s.get("win") or {}
    rend = sum(r.get("pct") or 0 for r in (s.get("render") or {}).values())
    out: dict[str, float] = {}
    if win:
        wsl = sum(v for k, v in win.items() if k.startswith("vmwp|") and k.split("|")[1] in ("3d", "compute"))
        rend = min(rend, wsl) if wsl else rend
        out["Renderer"] = rend
        rest = max(0.0, wsl - rend)
        prep = any(j["l"] in PREP for j in s.get("jobs") or [])
        out["Karaoke prep" if prep else "Other WSL"] = rest
        b = d = o = 0.0
        for k, v in win.items():
            proc, eng = k.split("|", 1)
            if eng not in ("3d", "compute") or proc == "vmwp":
                continue
            if proc.lower() in BROWSERS:
                b += v
            elif proc.lower() == "dwm":
                d += v
            else:
                o += v
        out["Browser"], out["Desktop"], out["Other Windows"] = b, d, o
        out["Video encode"] = sum(v for k, v in win.items() if k.endswith("|videoencode"))
        out["Video decode"] = sum(v for k, v in win.items() if k.endswith("|videodecode"))
    else:
        out["Renderer"] = rend
        g = s.get("gpu") or {}
        out["Video encode"] = g.get("enc") or 0.0
        out["Video decode"] = g.get("dec") or 0.0
    return {k: round(min(100.0, v), 1) for k, v in out.items()}


class Watch:
    def __init__(self, name_of: Callable[[str], Optional[str]] = lambda sid: None, log: Path = LOG) -> None:
        self.ring: collections.deque = collections.deque(maxlen=RING)
        self.win = WinEngines()
        self.procs = Procs()
        self.name_of = name_of
        self.log = log
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def sample(self) -> dict:
        s = {"t": round(time.time(), 1), "gpu": card(), "win": self.win.latest(), "render": renderer(),
             "jobs": self.procs.sample(self.name_of)}
        return s

    def _write(self, s: dict) -> None:
        try:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            if self.log.exists() and self.log.stat().st_size > LOG_MAX:
                self.log.replace(self.log.with_suffix(".jsonl.1"))
            with self.log.open("a") as f:
                f.write(json.dumps(s, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _run(self) -> None:
        self.procs.sample()                             # the CPU baseline
        while not self._stop.wait(SAMPLE_S):
            try:
                s = self.sample()
            except Exception:
                continue
            if not s["gpu"] and not s["win"]:
                continue
            self.ring.append(s)
            self._write(s)

    def start(self) -> "Watch":
        if self._thread is None and shutil.which("nvidia-smi"):
            self._thread = threading.Thread(target=self._run, name="gpu-watch", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self.win.stop()

    def view(self, minutes: float = 15, points: int = 450) -> dict:
        """For the page: the newest sample, the series of lanes (at most ``points``) and the window's summary."""
        cut = time.time() - minutes * 60
        ss = [s for s in list(self.ring) if s["t"] >= cut]
        step = max(1, -(-len(ss) // points))
        series = [{"t": s["t"], "total": (s.get("gpu") or {}).get("util"), "lanes": lanes(s)} for s in ss[::step]]
        now = ss[-1] if ss else None
        return {"running": self._thread is not None, "sample_s": SAMPLE_S, "now": now and {**now, "lanes": lanes(now)},
                "series": series, "summary": summary(ss)}


# --- reading back ------------------------------------------------------------------------------------------

def summary(ss: list[dict]) -> dict:
    """Over some samples: the card's mean and p95 busy %, each lane's mean, the jobs that ran and their mean CPU,
    and each run of HOT_PCT %+ (>= 3 samples) with the lanes and jobs during it."""
    if not ss:
        return {"samples": 0}
    util = sorted((s.get("gpu") or {}).get("util") or 0 for s in ss)
    ln: dict[str, float] = collections.defaultdict(float)
    for s in ss:
        for k, v in lanes(s).items():
            ln[k] += v
    jobs: dict[str, list[float]] = collections.defaultdict(list)
    for s in ss:
        seen: dict[str, float] = collections.defaultdict(float)
        for j in s.get("jobs") or []:
            seen[j["l"]] += j["cpu"]
        for k, v in seen.items():
            jobs[k].append(v)
    hot = []
    run: list[dict] = []
    for s in ss + [{}]:
        if ((s.get("gpu") or {}).get("util") or 0) >= HOT_PCT:
            run.append(s)
            continue
        if len(run) >= 3:
            rl: dict[str, float] = collections.defaultdict(float)
            for r in run:
                for k, v in lanes(r).items():
                    rl[k] += v / len(run)
            names = collections.Counter(j["l"] + (f" ({j['song']})" if j.get("song") else "")
                                        for r in run for j in r.get("jobs") or [] if j.get("gpu") or j["cpu"] >= 20)
            late = max((x.get("late") or 0 for r in run for x in (r.get("render") or {}).values()), default=0)
            hot.append({"from": run[0]["t"], "s": round(run[-1]["t"] - run[0]["t"] + SAMPLE_S),
                        "lanes": {k: round(v, 1) for k, v in sorted(rl.items(), key=lambda kv: -kv[1]) if v >= 1},
                        "jobs": [k for k, _ in names.most_common(8)], "late_pct": late})
        run = []
    n = len(ss)
    return {"samples": n, "from": ss[0]["t"], "to": ss[-1]["t"],
            "util_mean": round(sum(util) / n, 1), "util_p95": util[min(n - 1, int(n * 0.95))],
            "hot_s": round(sum(1 for u in util if u >= HOT_PCT) * SAMPLE_S),
            "lanes": {k: round(v / n, 1) for k, v in sorted(ln.items(), key=lambda kv: -kv[1])},
            "jobs": {k: {"cpu": round(sum(v) / n, 1), "s": round(len(v) * SAMPLE_S)}
                     for k, v in sorted(jobs.items(), key=lambda kv: -sum(kv[1]))[:15]},
            "hot": hot[-20:]}


def read_log(minutes: float, path: Path = LOG) -> list[dict]:
    cut = time.time() - minutes * 60
    out = []
    for p in (path.with_suffix(".jsonl.1"), path):
        if not p.exists():
            continue
        with p.open() as f:
            for line in f:
                try:
                    s = json.loads(line)
                except ValueError:
                    continue
                if s.get("t", 0) >= cut:
                    out.append(s)
    return out


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def report(ss: Iterable[dict]) -> str:
    sm = summary(list(ss))
    if not sm["samples"]:
        return "no samples"
    out = [f"{_clock(sm['from'])}-{_clock(sm['to'])}  GPU busy mean {sm['util_mean']}%  p95 {sm['util_p95']}%"
           f"  at {HOT_PCT}%+ for {sm['hot_s']} s",
           "GPU by who (mean %): " + "  ".join(f"{k} {v}" for k, v in sm["lanes"].items() if v >= 0.5),
           "CPU by job (mean % of a core):"]
    out += [f"  {k:<22} {v['cpu']:>6}   ran {v['s']} s" for k, v in sm["jobs"].items()]
    if sm["hot"]:
        out.append(f"Runs at {HOT_PCT}%+:")
    for h in sm["hot"]:
        out.append(f"  {_clock(h['from'])} {h['s']:>4} s  " + "  ".join(f"{k} {v}" for k, v in h["lanes"].items())
                   + (f"  | screens late {h['late_pct']}%" if h["late_pct"] else "")
                   + ("\n      " + ", ".join(h["jobs"]) if h["jobs"] else ""))
    return "\n".join(out)


_watch: Optional[Watch] = None


def start(name_of: Callable[[str], Optional[str]] = lambda sid: None) -> Watch:
    global _watch
    if _watch is None:
        _watch = Watch(name_of).start()
    return _watch


def get() -> Optional[Watch]:
    return _watch


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m utils.gpu_watch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="shares and hot runs from logs/gpu_watch.jsonl")
    r.add_argument("--minutes", type=float, default=60)
    sub.add_parser("now", help="take two samples here and print the second")
    a = ap.parse_args()
    if a.cmd == "report":
        print(report(read_log(a.minutes)))
    else:
        w = Watch()
        w.procs.sample()
        w.win.latest()
        time.sleep(5)
        s = w.sample()
        w.stop()
        print(json.dumps({**s, "lanes": lanes(s)}, indent=1))


if __name__ == "__main__":
    main()
