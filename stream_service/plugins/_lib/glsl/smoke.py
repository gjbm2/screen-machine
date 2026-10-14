"""Batch crash check for imported GLSL plug-ins (the owner's 'does it crash / is it black' test, many at once).

  CUDA_VISIBLE_DEVICES=1 nice -n 15 ~/.venvs/sm-stream/bin/python -m stream_service.plugins._lib.glsl.smoke \
      <root with sources/ transitions/ effects/> --out results.jsonl [--workers 3] [--only name,name]

Each plug-in file is loaded by path, set up at 960x540, rendered for a few frames (scenes under a synthetic beat,
transitions at three progress values between two art stills, effects over an art still) and timed.  Workers run
in subprocesses: a kernel that faults poisons its CUDA context, so the parent restarts the worker after the
plug-in that crashed it.  One JSON line per plug-in: ok, why, mean/std of the output, ms per frame and, for
scenes, an estimate at 4K for the default render_scale.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import time

W, H = 960, 540
ART = pathlib.Path(__file__).resolve().parents[4] / "output"


def _art(cp, k: int):
    import numpy as np
    from PIL import Image
    files = []
    for p in sorted(ART.glob("*")) if ART.exists() else []:   # real artwork only (skip blanks / placeholders)
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
        img = Image.open(files[k]).convert("RGBA").resize((W, H))
        return cp.asarray(np.asarray(img).copy())
    y, x = np.mgrid[0:H, 0:W]
    a = np.stack([(x * 255 // W), (y * 255 // H), ((x + y) * 128 // (W + H)) + 60 * k, np.full_like(x, 255)], -1)
    return cp.asarray(a.astype(np.uint8))


def _load(path: pathlib.Path):
    from stream_service.plugins.base import Source, Transition
    from stream_service.plugins._lib.effect_base import Effect
    spec = importlib.util.spec_from_file_location(f"_glsl_smoke_.{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for v in vars(mod).values():
        if isinstance(v, type) and issubclass(v, (Source, Transition, Effect)) and getattr(v, "name", "") == path.stem:
            return v
    raise RuntimeError("no plug-in class")


THUMBS = pathlib.Path.home() / ".cache" / "sm-fxtest" / "out"   # the /dev Library reads <name>/sheet.png


def _thumb(name: str, frame, res: dict, kind: str) -> None:
    """One-tile sheet.png (480x270) plus a small summary.json for the /dev Library."""
    from PIL import Image
    d = THUMBS / name
    d.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(frame.get()[..., :3]).resize((480, 270), Image.BILINEAR)
    img.save(d / "sheet.png")
    (d / "summary.json").write_text(json.dumps({"name": name, "kind": kind, "ok": res.get("ok"), "harness": "glsl-smoke",
                                                "ms_960x540": res.get("ms"), "est_ms_4k": res.get("est_ms_4k")}))


def _gpu_ms(cp, fn, reps: int = 3, runs: int = 5) -> float:
    """GPU time of fn() (the shader's launches only): min over short back-to-back runs, which keeps Python
    overhead and other processes' kernels on the shared GPU out of the estimate."""
    best = 1e9
    for _ in range(runs):
        e0, e1 = cp.cuda.Event(), cp.cuda.Event()
        e0.record()
        for _ in range(reps):
            fn()
        e1.record()
        e1.synchronize()
        best = min(best, cp.cuda.get_elapsed_time(e0, e1) / reps)
    return best


def check_one(path: pathlib.Path, ctx, a, b) -> dict:
    from stream_service.plugins.base import Source, Transition
    cp = ctx.cp
    cls = _load(path)
    out = cp.zeros((H, W, 4), cp.uint8)
    t0 = time.perf_counter()
    ev0, ev1 = cp.cuda.Event(), cp.cuda.Event()
    res = {"name": path.stem}
    if issubclass(cls, Transition):
        tr = cls(cls.validate({}))
        tr.setup(ctx)
        frames = []
        tr.render(0.5, a, b, out)   # compile + warm
        ctx.stream.synchronize()
        res["setup_s"] = round(time.perf_counter() - t0, 2)
        ev0.record()
        for p in (0.25, 0.5, 0.75):
            tr.render(p, a, b, out)
            frames.append(out.copy())
        ev1.record()
        ev1.synchronize()
        res["ms"] = round(cp.cuda.get_elapsed_time(ev0, ev1) / 3, 3)
        tr.render(0.5, a, b, out)
        g = _gpu_ms(cp, lambda: tr.prog.launch(out))
        res["gpu_ms"] = round(g, 3)
        res["est_ms_4k"] = round(g * (3840 * 2160) / (W * H), 2)
        mid = frames[1].astype(cp.float32)
        d_a = float(cp.abs(mid - a.astype(cp.float32)).mean())
        d_b = float(cp.abs(mid - b.astype(cp.float32)).mean())
        res.update(mean=round(float(mid[..., :3].mean()), 1), std=round(float(mid[..., :3].std()), 1),
                   d_a=round(d_a, 1), d_b=round(d_b, 1))
        res["ok"] = bool(res["std"] > 2 and (d_a > 1 or d_b > 1))
        res["why"] = None if res["ok"] else "flat or no change"
        _thumb(path.stem, frames[1], res, "transition")
        tr.close()
        return res
    obj = cls()
    params = cls.validate({"amount": 1.0} if "amount" in cls.param_props() else {})
    obj.setup(ctx, params)
    is_src = issubclass(cls, Source)
    dt = 1.0 / 30
    n = 24 if is_src else 8

    srcs = [a] if is_src else [cp.roll(a, 9 * i, axis=1) for i in range(n)]   # effects see a moving input

    def frame(i):
        if is_src:
            obj.render(i * dt, dt if i else 0.0, out)
        else:
            obj.render(i * dt, dt if i else 0.0, srcs[i], out)

    frame(0)
    ctx.stream.synchronize()
    res["setup_s"] = round(time.perf_counter() - t0, 2)
    ev0.record()
    snaps = []
    for i in range(1, n):
        frame(i)
        if i in (n // 2, n - 1):
            snaps.append(out.copy())
    ev1.record()
    ev1.synchronize()
    ms = cp.cuda.get_elapsed_time(ev0, ev1) / (n - 1)
    res["ms"] = round(ms, 3)
    last = snaps[-1].astype(cp.float32)
    res.update(mean=round(float(last[..., :3].mean()), 1), std=round(float(last[..., :3].std()), 1))
    moving = float(cp.abs(snaps[0].astype(cp.float32) - last).mean())
    res["moving"] = round(moving, 2)
    if is_src:
        if obj.dialect == "st":
            g = _gpu_ms(cp, lambda: obj.prog.launch(obj.buf))
        else:
            g = _gpu_ms(cp, lambda: obj.passes.render(obj.buf, obj.t, 0.0))
        s = float(params.get("render_scale", 0.5))   # 4K at scale s has 3840*2160*s^2 internal pixels
        res["gpu_ms"] = round(g, 3)
        res["est_ms_4k"] = round(g * (3840 * 2160 * s * s) / (obj.w * obj.h), 2)
        res["ok"] = bool(res["mean"] > 3 or res["std"] > 3)
        res["why"] = None if res["ok"] else "black"
    else:
        g = _gpu_ms(cp, lambda: obj.passes.render(obj.tmp, obj.t, 0.0))
        res["gpu_ms"] = round(g, 3)
        res["est_ms_4k"] = round(g * (3840 * 2160 * obj.scale ** 2) / (obj.w * obj.h), 2)
        diff = float(cp.abs(last - srcs[-1].astype(cp.float32)).mean())
        res["d_src"] = round(diff, 2)
        res["ok"] = bool(res["mean"] > 3 or res["std"] > 3)
        res["why"] = None if res["ok"] else "black"
    _thumb(path.stem, snaps[-1], res, "source" if is_src else "effect")
    obj.close()
    return res


HANG_S = 90.0   # a worker with no new output line for this long is stuck (e.g. an endless loop in a kernel)


def _die_with_parent() -> None:
    """Linux: SIGKILL this worker when the parent exits, so a hung kernel can never outlive a stopped run."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGKILL)   # PR_SET_PDEATHSIG
    except Exception:
        pass


def worker(paths: list[str], out: str) -> None:
    _die_with_parent()
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
    from stream_service.gpu import RenderContext
    from stream_service.tools.fxtest import gpu_slots
    ctx = RenderContext(0, W, H, 30, prealloc_buffers=0)
    with ctx.device, ctx.stream:
        a, b = _art(ctx.cp, 0), _art(ctx.cp, 1)
        with open(out, "a") as f:
            for p in paths:
                f.write(json.dumps({"name": pathlib.Path(p).stem, "started": True}) + "\n")
                f.flush()
                try:
                    with gpu_slots(False):   # share GPU 1 fairly with fxtest runs (one slot per plug-in)
                        r = check_one(pathlib.Path(p), ctx, a, b)
                except Exception as ex:   # translation / compile / launch errors
                    msg = f"{type(ex).__name__}: {ex}"
                    r = {"name": pathlib.Path(p).stem, "ok": False, "why": msg[:600]}
                    if "cudaError" in msg or "CUDA_ERROR" in msg:
                        f.write(json.dumps(r) + "\n")
                        f.flush()
                        os._exit(3)   # context is poisoned: let the parent restart us
                f.write(json.dumps(r) + "\n")
                f.flush()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only", default=None)
    ap.add_argument("--worker", nargs="*", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker is not None:
        worker(a.worker, a.out)
        return
    root = pathlib.Path(a.root)
    paths = sorted(str(p) for k in ("transitions", "effects", "sources") for p in (root / k).glob("*.py")
                   if not p.name.startswith("_"))
    if a.only:
        keep = set(a.only.split(","))
        paths = [p for p in paths if pathlib.Path(p).stem in keep]
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out):
            d = json.loads(line)
            if "ok" in d:
                done.add(d["name"])
    todo = [p for p in paths if pathlib.Path(p).stem not in done]
    shards = [todo[i::a.workers] for i in range(a.workers)]
    print(f"{len(todo)} to check ({len(done)} done) in {a.workers} workers", flush=True)
    outs = [f"{a.out}.w{i}" for i in range(a.workers)]
    procs = {}

    def start(i, items):
        if not items:
            return None
        return subprocess.Popen([sys.executable, "-m", "stream_service.plugins._lib.glsl.smoke", a.root, "--out",
                                 outs[i], "--worker", *items], cwd=str(pathlib.Path(__file__).resolve().parents[4]))

    remaining = {i: list(s) for i, s in enumerate(shards)}
    for i, items in remaining.items():
        procs[i] = start(i, items)
    while any(p is not None for p in procs.values()):
        time.sleep(2)
        for i, p in list(procs.items()):
            if p is None:
                continue
            if p.poll() is None:
                try:
                    stale = time.time() - os.path.getmtime(outs[i])
                except OSError:
                    stale = 0.0
                if stale < HANG_S:
                    continue
                p.kill()          # hung: the started-but-unfinished plug-in is marked below
                p.wait()
            seen = {}
            if os.path.exists(outs[i]):
                for line in open(outs[i]):
                    d = json.loads(line)
                    seen[d["name"]] = d
            left = [x for x in remaining[i] if pathlib.Path(x).stem not in seen or "ok" not in seen[pathlib.Path(x).stem]]
            started = [x for x in left if pathlib.Path(x).stem in seen]   # crashed mid-way, no result line
            if started:
                with open(outs[i], "a") as f:
                    for x in started:
                        f.write(json.dumps({"name": pathlib.Path(x).stem, "ok": False, "why": "hung or crashed"}) + "\n")
                left = [x for x in left if x not in started]
            remaining[i] = left
            procs[i] = start(i, left) if left else None
    with open(a.out, "a") as f:
        for o in outs:
            if os.path.exists(o):
                for line in open(o):
                    d = json.loads(line)
                    if "ok" in d:
                        f.write(line)
                os.remove(o)
    res = [json.loads(x) for x in open(a.out)]
    bad = [r for r in res if not r["ok"]]
    print(f"{len(res)} checked, {len(bad)} failed")


if __name__ == "__main__":
    main()
