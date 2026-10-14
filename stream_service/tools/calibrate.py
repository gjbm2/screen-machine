"""calibrate: what every plug-in costs on each GPU at production size (an overnight job).

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.calibrate [--gpus 0,1] [--only a,b] [--full] [--deadline 06:00]

For each plug-in (sources, transitions, effects) and each GPU, render at 3840x2160 for 2 s of stream time:
scenes under the synthetic 124 bpm song (energy and pulse bound as presets do), transitions sweeping progress
between two art stills, effects at amount 1 over a moving still.  Per-frame GPU time comes from CUDA events.
Scenes over budget are re-measured at the render_scale that should fit; the result says whether it does.

Results merge into ~/.cache/sm-fxtest/calibration.json:
  {"gpus": {"0": {"name": ...}}, "plugins": {name: {"kind": ..., "sha1": ..., "gpu0": {...}, "gpu1": {...}}}}
  per GPU: status ok|error|hung|too_slow, p50/p95/max ms, mem_mb, budget_ms, render_scale, and for scenes
  fit_scale + fit_p95 + fits.  One data set: a (plug-in, GPU) already measured is never measured again (--only /
  --full to force); the nightly run therefore only picks up plug-ins added since.

Safety (GPU 1 is shared with the live demo, GPU 0 with production): one worker process per GPU; a watchdog kills
a worker whose plug-in makes no progress for 120 s (that plug-in is recorded as hung); workers die with the
parent; before each plug-in a worker waits while its GPU is busy with someone else; nothing new starts after
--deadline.  Imported GLSL shaders are also bounded in-kernel (plugins/_lib/glsl).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGINS = REPO / "stream_service" / "plugins"
OUT = pathlib.Path.home() / ".cache" / "sm-fxtest"
RESULTS = OUT / "calibration.json"
RUNS = OUT / "calibration"
W, H, FPS = 3840, 2160, 60
BUDGET_MS = {"source": 6.0, "transition": 3.0, "effect": 2.0}     # CONVENTIONS section 4 (P4000, 4K)
HANG_S = 120.0
BUSY_PCT = 15
# cron's PATH has no /usr/lib/wsl/lib: find nvidia-smi explicitly, or the busy-GPU guard would silently pass
NVSMI = next((p for p in ("/usr/lib/wsl/lib/nvidia-smi", "/usr/bin/nvidia-smi") if os.path.exists(p)), "nvidia-smi")


# ------------------------------------------------------------------------------------------- discovery

def plugin_files() -> list[tuple[str, pathlib.Path]]:
    out = []
    for kind in ("sources", "transitions", "effects"):
        for f in sorted((PLUGINS / kind).glob("*.py")):
            if not f.name.startswith("_"):
                out.append((kind, f))
    return out


def sha1(path: pathlib.Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def load_class(kind: str, path: pathlib.Path):
    from stream_service.plugins.base import Source, Transition
    from stream_service.plugins._lib.effect_base import Effect
    mod = importlib.import_module(f"stream_service.plugins.{kind}.{path.stem}")
    cands = [v for v in vars(mod).values() if isinstance(v, type) and issubclass(v, (Source, Transition, Effect))
             and v.__module__ == mod.__name__ and getattr(v, "name", "")]
    for c in cands:
        if c.name == path.stem:
            return c
    if cands:
        return cands[0]
    raise RuntimeError("no plug-in class")


# ------------------------------------------------------------------------------------------- GPU helpers

def smi_index(cuda_index: int) -> int | None:
    """nvidia-smi index of a CUDA device (matched by PCI bus id; the two orders can differ)."""
    try:
        import cupy as cp
        bus = cp.cuda.Device(cuda_index).pci_bus_id.lower()
        rows = subprocess.run([NVSMI, "--query-gpu=index,pci.bus_id", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=10).stdout.splitlines()
        for r in rows:
            i, b = (x.strip() for x in r.split(","))
            if b.lower().endswith(bus[-12:]):
                return int(i)
    except Exception:
        pass
    return None


def gpu_util(smi: int | None) -> int:
    if smi is None:
        return 100
    try:
        v = subprocess.run([NVSMI, "-i", str(smi), "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10).stdout.strip()
        return int(v)
    except Exception:
        return 100   # cannot tell: treat as busy (fail closed), the run waits rather than intrudes


def wait_quiet(smi: int | None, deadline: dt.datetime, log) -> bool:
    """Wait (while our worker is idle) until the GPU is not busy with someone else; False if past deadline."""
    while dt.datetime.now() < deadline:
        time.sleep(1.5)            # let our own last frames leave nvidia-smi's averaging window
        samples = []
        for _ in range(4):
            samples.append(gpu_util(smi))
            time.sleep(0.5)
        if sum(samples) / len(samples) < BUSY_PCT:
            return True
        log(f"GPU busy ({samples}%), waiting 30 s")
        time.sleep(30)
    return False


def _die_with_parent() -> None:
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)   # PR_SET_PDEATHSIG
    except Exception:
        pass


# ------------------------------------------------------------------------------------------- measuring

def _stats(ms: list[float]) -> dict:
    import numpy as np
    a = np.asarray(ms, dtype=float)
    return {"p50": round(float(np.percentile(a, 50)), 3), "p95": round(float(np.percentile(a, 95)), 3),
            "max": round(float(a.max()), 3), "n": int(a.size)}


_SPIN = r"""
extern "C" __global__ void cal_spin(const long long cycles) {
    const long long t0 = clock64();
    while (clock64() - t0 < cycles) { }
}
"""


def _timed(cp, n: int, frame) -> list[float]:
    """Per-frame GPU time.  A ~3 ms spin kernel ahead of each frame keeps the GPU busy while Python enqueues
    the frame, so the events time the plug-in's GPU work, not host submission gaps."""
    from stream_service.gpu import raw_module
    spin = raw_module(_SPIN).get_function("cal_spin")
    cycles = 3 * int(cp.cuda.runtime.getDeviceProperties(cp.cuda.Device().id)["clockRate"])   # kHz * 3 ms
    import numpy as np
    evs = [(cp.cuda.Event(), cp.cuda.Event()) for _ in range(n)]
    for i, (e0, e1) in enumerate(evs):
        spin((1,), (1,), (np.int64(cycles),))
        e0.record()
        frame(i)
        e1.record()
    evs[-1][1].synchronize()
    return [cp.cuda.get_elapsed_time(e0, e1) for e0, e1 in evs]


def _unwatch(obj) -> None:
    """GLSL wrappers trip on slow frames at runtime; calibration wants the raw cost."""
    w = getattr(obj, "_watch", None)
    if w is not None:
        w.slow_ms = 1e9


def _art(cp, k: int, w: int = W, h: int = H):
    import numpy as np
    from PIL import Image
    art = REPO / "output"
    files = []
    for p in sorted(art.glob("*")) if art.exists() else []:
        if p.suffix.lower() in (".jpg", ".jpeg", ".png") and not p.name.startswith("_"):
            try:
                with Image.open(p) as im:
                    if im.width >= 640:
                        files.append(p)
            except OSError:
                pass
        if len(files) > k:
            break
    if len(files) > k:
        return cp.asarray(np.asarray(Image.open(files[k]).convert("RGBA").resize((w, h))).copy())
    rng = np.random.default_rng(k)
    return cp.asarray(rng.integers(0, 255, (h, w, 4), dtype=np.uint8))


def measure(kind: str, cls, ctx, a, b) -> dict:
    from stream_service.tools.fxtest import Signals, drive
    cp = ctx.cp
    pool = cp.get_default_memory_pool()
    used0 = pool.used_bytes()
    budget = BUDGET_MS["source" if kind == "sources" else kind[:-1]]
    res = {"budget_ms": budget}
    out = cp.zeros((H, W, 4), cp.uint8)
    n = 2 * FPS
    t0 = time.perf_counter()
    if kind == "transitions":
        x = cls(cls.validate({}))
        x.setup(ctx)
        _unwatch(x)
        frame = lambda i: x.render(0.02 + 0.96 * (i % n) / n, a, b, out)   # noqa: E731
        _timed(cp, 10, frame)
        res["setup_s"] = round(time.perf_counter() - t0, 2)
        res.update(_stats(_timed(cp, n, frame)))
        x.close()
    elif kind == "effects":
        props = cls.param_props()
        x = cls()
        x.setup(ctx, cls.validate({"amount": 1.0} if "amount" in props else {}))
        _unwatch(x)
        if "pulse" in props:
            drive(x, {"pulse": {"kick": "beat", "amount": 1.0}}, Signals())
        srcs = [cp.roll(a, 24 * i, axis=1) for i in range(8)]

        def frame(i):
            if hasattr(x, "_fx_t"):
                x._fx_t = i / FPS
            x.render(i / FPS, 1 / FPS if i else 0.0, srcs[i % 8], out)
        _timed(cp, 10, frame)
        res["setup_s"] = round(time.perf_counter() - t0, 2)
        res.update(_stats(_timed(cp, n, frame)))
        x.close()
    else:
        props = cls.param_props()
        bindings = {k: v for k, v in (("energy", {"bind": "energy"}), ("pulse", {"kick": "beat", "amount": 1.0}))
                    if k in props}

        def run_scene(params: dict) -> tuple[dict, object]:
            s = cls()
            s.setup(ctx, cls.validate(params))
            _unwatch(s)
            drive(s, bindings, Signals())

            def frame(i):
                s._fx_t = 1.0 + i / FPS
                s.render(1.0 + i / FPS, 1 / FPS if i else 0.0, out)
            _timed(cp, 20, frame)
            st = _stats(_timed(cp, n, frame))
            return st, s

        st, s = run_scene({})
        res["setup_s"] = round(time.perf_counter() - t0, 2)
        res.update(st)
        scale0 = s.easer.values().get("render_scale") if getattr(s, "easer", None) else None
        res["render_scale"] = scale0
        s.close()
        if scale0 and st["p95"] > budget and "render_scale" in props:
            lo = float(props["render_scale"].get("minimum", 0.25))
            fit = max(lo, min(1.0, round(scale0 * (budget / st["p95"]) ** 0.5 * 0.95, 2)))
            st2, s2 = run_scene({"render_scale": fit})
            s2.close()
            res.update(fit_scale=fit, fit_p95=st2["p95"], fits=st2["p95"] <= budget * 1.15)
        else:
            res["fits"] = st["p95"] <= budget * 1.15
    res["mem_mb"] = round((pool.used_bytes() - used0) / 2 ** 20, 1)
    if kind != "sources":
        res["fits"] = res["p95"] <= res["budget_ms"] * 1.15
    res["status"] = "ok" if res["fits"] else "too_slow"
    return res


# ------------------------------------------------------------------------------------------- worker / parent

def worker(gpu: int, items: list[str], out: str, deadline: str) -> None:
    _die_with_parent()
    sys.path.insert(0, str(REPO))
    from stream_service.gpu import RenderContext
    end = dt.datetime.fromisoformat(deadline)
    smi = smi_index(gpu)
    log = lambda m: print(f"[gpu{gpu}] {m}", flush=True)   # noqa: E731
    ctx = RenderContext(gpu, W, H, FPS, prealloc_buffers=0)
    cp = ctx.cp
    with ctx.device, ctx.stream, open(out, "a") as f:
        a, b = _art(cp, 0), _art(cp, 1)
        for item in items:
            kind, path = item.split(":", 1)
            path = pathlib.Path(path)
            if not wait_quiet(smi, end, log):
                log("deadline reached; stopping")
                return
            f.write(json.dumps({"name": path.stem, "started": True}) + "\n")
            f.flush()
            rec = {"name": path.stem, "kind": kind[:-1], "sha1": sha1(path), "at": dt.datetime.now().isoformat(timespec="seconds")}
            try:
                rec.update(measure(kind, load_class(kind, path), ctx, a, b))
            except Exception as ex:   # noqa: BLE001 -- one plug-in must never stop the run
                msg = f"{type(ex).__name__}: {ex}"
                rec.update(status="too_slow" if type(ex).__name__ == "GpuTooSlow" else "error", error=msg[:400])
                if "cudaError" in msg or "CUDA_ERROR" in msg:
                    f.write(json.dumps(rec) + "\n")
                    f.flush()
                    os._exit(3)   # poisoned context: the parent restarts this worker
            cp.get_default_memory_pool().free_all_blocks()
            f.write(json.dumps(rec) + "\n")
            f.flush()


def merge(paths: list[str], gpu_names: dict) -> int:
    data = {"gpus": {}, "plugins": {}}
    if RESULTS.exists():
        try:
            data = json.loads(RESULTS.read_text())
        except json.JSONDecodeError:
            pass
    data.setdefault("gpus", {}).update(gpu_names)
    n = 0
    for p in paths:
        gpu = pathlib.Path(p).stem.rsplit("gpu", 1)[1]
        if not os.path.exists(p):
            continue
        for line in open(p):
            d = json.loads(line)
            if "status" not in d:
                continue
            e = data["plugins"].setdefault(d["name"], {})
            e["kind"], e["sha1"] = d["kind"], d["sha1"]
            e[f"gpu{gpu}"] = {k: v for k, v in d.items() if k not in ("name", "kind", "sha1")}
            n += 1
    data["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(RESULTS)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpus", default="0,1")
    ap.add_argument("--only", default=None)
    ap.add_argument("--full", action="store_true", help="re-measure everything (manual use only)")
    ap.add_argument("--deadline", default="06:00", help="HH:MM (next occurrence) after which nothing new starts")
    ap.add_argument("--worker", nargs=4, help=argparse.SUPPRESS)   # gpu items_file out deadline
    a = ap.parse_args()
    if a.worker:
        gpu, items_file, out, deadline = a.worker
        worker(int(gpu), json.loads(pathlib.Path(items_file).read_text()), out, deadline)
        return
    RUNS.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now()
    hh, mm = (int(x) for x in a.deadline.split(":"))
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if deadline <= now:
        deadline += dt.timedelta(days=1)
    gpus = [int(g) for g in a.gpus.split(",") if g.strip()]
    prev = json.loads(RESULTS.read_text()).get("plugins", {}) if RESULTS.exists() else {}
    files = plugin_files()
    if a.only:
        keep = set(a.only.split(","))
        files = [(k, f) for k, f in files if f.stem in keep]
    by_name = {f.stem: (k, f) for k, f in files}
    import cupy as cp
    gpu_names = {str(g): {"name": cp.cuda.runtime.getDeviceProperties(g)["name"].decode()} for g in gpus}
    stamp = now.strftime("%Y%m%d-%H%M")
    procs, outs, todo = {}, {}, {}
    for g in gpus:
        items = [f"{k}:{f}" for k, f in files                      # measured once: only plug-ins with no entry
                 if a.full or a.only or f"gpu{g}" not in prev.get(f.stem, {})]
        todo[g] = items
        outs[g] = str(RUNS / f"run-{stamp}-gpu{g}.jsonl")
        print(f"gpu{g} ({gpu_names[str(g)]['name']}): {len(items)} plug-ins to measure", flush=True)

    def start(g):
        if not todo[g]:
            return None
        items_file = RUNS / f".items-{stamp}-gpu{g}.json"
        items_file.write_text(json.dumps(todo[g]))
        return subprocess.Popen([sys.executable, "-m", "stream_service.tools.calibrate", "--worker", str(g),
                                 str(items_file), outs[g], deadline.isoformat()], cwd=str(REPO))

    for g in gpus:
        procs[g] = start(g)
    try:
        while any(p is not None for p in procs.values()):
            time.sleep(5)
            for g, p in list(procs.items()):
                if p is None:
                    continue
                seen = {}
                if os.path.exists(outs[g]):
                    for line in open(outs[g]):
                        d = json.loads(line)
                        seen[d["name"]] = d
                if p.poll() is None:
                    try:
                        stale = time.time() - os.path.getmtime(outs[g])
                    except OSError:
                        stale = 0.0
                    if stale < HANG_S:
                        continue
                    p.kill()
                    p.wait()
                started = [n for n, d in seen.items() if "status" not in d]
                if started:   # the plug-in the worker was on when it died or hung
                    with open(outs[g], "a") as f:
                        for n in started:
                            kind, path = by_name[n]
                            f.write(json.dumps({"name": n, "kind": kind[:-1], "sha1": sha1(path), "status": "hung",
                                                "at": dt.datetime.now().isoformat(timespec="seconds")}) + "\n")
                            seen[n] = {"status": "hung"}
                todo[g] = [i for i in todo[g] if pathlib.Path(i.split(":", 1)[1]).stem not in seen]
                if dt.datetime.now() >= deadline or not todo[g]:
                    procs[g] = None
                else:
                    procs[g] = start(g)
    finally:
        for p in procs.values():
            if p is not None and p.poll() is None:
                p.kill()
        n = merge(list(outs.values()), gpu_names)
        print(f"merged {n} measurements into {RESULTS}", flush=True)


if __name__ == "__main__":
    main()
