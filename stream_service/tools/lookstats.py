"""lookstats: measure every scene and effect preset's picture, for the scene x effect interplay
(docs/SCENE_FX_INTERPLAY.md §2.1).  Writes config/live/look_stats.generated.json.

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.lookstats [--only scenes|effects] [--fresh] [names...]

Offline, on the harness GPU ($FX_GPU, default 1, the P4000) under fxtest's GPU slot lock.  It waits while any stream
session exists (two screens use GPU 1).  Low resolution (480x270) at 15 fps, every loop bounded: 8 s of a scene
(120 frames), 1.6 s of an effect on each of 4 inputs, 30 s of wall time per item at most.  Resumable: items already
in the file are kept unless --fresh or named.

Scene numbers (raw; ``*_pct`` = rank among all measured scenes, 0..1):
  busy        mean luma gradient (how much fine detail is on screen)
  motion      mean |frame - previous frame| at 15 fps (how much moves, camera travel included)
  brightness  mean luma;  highlights = share of pixels with luma > 0.85;  dark = share below 0.06
  symmetric   the best of corr(luma, mirrored left-right / up-down / rotated 180), averaged over frames
  colourful   Hasler-Suesstrunk colourfulness
  palette_driven  the plug-in has a palette param (drawn in the room's colours), from its schema
Effect numbers (mean over the 4 inputs, amount 0.8, after 1 s):
  change      mean |out - in| (0..1): how far it departs from the picture (its strength)
  detail      busy(out) - busy(in): adds detail > 0, smooths < 0
  light       mean luma(out) - mean luma(in)
  keeps       corr(luma in, luma out): a grade keeps the structure (~1), a warp or a glitch much less
  temporal    |out - previous out| / |in - previous in|: trails and stutter change how motion reads
These are coarse picture statistics of one short synthetic run, not judgements: the interplay table
(config/live/fx_interplay.yaml) turns them into gentle multipliers.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import pathlib
import sys
import time
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402

OUT = REPO / "config/live/look_stats.generated.json"
W, H, FPS = 480, 270, 15
SCENE_S, EFFECT_S, WARM_S = 8.0, 1.6, 1.0
ITEM_WALL_S = 30.0
RUN_WALL_S = 3 * 3600.0
SCENE_BINDINGS = {"energy": {"bind": "energy"}, "pulse": {"kick": "beat", "amount": 1.0}}
RANKED = {"scenes": ("busy", "motion", "brightness", "colourful"), "effects": ("change", "detail", "light")}


def live_sessions() -> bool:
    """Any stream session at all (fail closed: unknown = yes)."""
    try:
        with urllib.request.urlopen("http://localhost:8702/api/sessions", timeout=5) as r:
            return bool(json.load(r).get("sessions"))
    except Exception:
        return True


def wait_idle(log) -> float:
    """Wait while any stream session is up (GPU 1 serves a screen).  -> seconds waited."""
    t0 = time.monotonic()
    waited = False
    while live_sessions():
        if not waited:
            log("a stream session is up: waiting (GPU 1 serves a screen)")
            waited = True
        time.sleep(30)
    if waited:
        log("no session: measuring")
    return time.monotonic() - t0


class Stats:
    def __init__(self, cp):
        self.cp = cp

    def luma(self, frame):
        f = frame[..., :3].astype(self.cp.float32) / 255.0
        return f, 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]

    def busy(self, lum) -> float:
        cp = self.cp
        return float(cp.abs(lum[:, 1:] - lum[:, :-1]).mean() + cp.abs(lum[1:, :] - lum[:-1, :]).mean())

    def colourful(self, f) -> float:
        cp = self.cp
        rg = f[..., 0] - f[..., 1]
        yb = 0.5 * (f[..., 0] + f[..., 1]) - f[..., 2]
        return float(cp.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * cp.sqrt(rg.mean() ** 2 + yb.mean() ** 2))

    def symmetric(self, lum) -> float:
        small = self.cp.asnumpy(lum[::5, ::5])
        best = 0.0
        for g in (small[:, ::-1], small[::-1, :], small[::-1, ::-1]):
            a, b = small.ravel(), g.ravel()
            if a.std() > 1e-4 and b.std() > 1e-4:
                best = max(best, float(np.corrcoef(a, b)[0, 1]))
        return best

    def corr(self, a, b) -> float:
        x, y = self.cp.asnumpy(a[::3, ::3]).ravel(), self.cp.asnumpy(b[::3, ::3]).ravel()
        return float(np.corrcoef(x, y)[0, 1]) if x.std() > 1e-4 and y.std() > 1e-4 else 1.0


def measure_scene(ctx, cls, params: dict, st: Stats) -> dict:
    from stream_service.tools import fxtest as fx
    cp = ctx.cp
    s = cls()
    fx.listen(s, ctx)
    s.setup(ctx, cls.validate(params))
    fx.drive(s, SCENE_BINDINGS, fx.Signals())
    frame, prev = ctx.new_frame(), None
    acc = {"busy": [], "brightness": [], "highlights": [], "dark": [], "symmetric": [], "colourful": [], "motion": []}
    t0 = time.monotonic()
    try:
        n = int(SCENE_S * FPS)
        for i in range(n):
            if time.monotonic() - t0 > ITEM_WALL_S:
                raise TimeoutError(f"slower than {ITEM_WALL_S:.0f} s for {n} frames")
            t = i / FPS
            s._fx_t = t
            s.render(t, 0.0 if i == 0 else 1.0 / FPS, frame)
            f, lum = st.luma(frame)
            if prev is not None and t >= 1.0:
                acc["motion"].append(float(cp.abs(lum - prev).mean()))
            prev = lum
            if i % 8 == 7 and t >= 1.0:
                acc["busy"].append(st.busy(lum))
                acc["brightness"].append(float(lum.mean()))
                acc["highlights"].append(float((lum > 0.85).mean()))
                acc["dark"].append(float((lum < 0.06).mean()))
                acc["symmetric"].append(st.symmetric(lum))
                acc["colourful"].append(st.colourful(f))
        ctx.stream.synchronize()
    finally:
        try:
            s.close()
        except Exception:
            pass
    return {k: round(float(np.mean(v)), 4) for k, v in acc.items() if v}


def effect_inputs(ctx):
    """Four fixed inputs: a calm field (warp_stack A), a busy kaleidoscopic plasma (B) and two art stills."""
    from stream_service.tools import fxtest as fx
    A, B = fx.ref_scene(ctx, "A"), fx.ref_scene(ctx, "B")
    art1, art2 = fx.art_frame(ctx, 1), fx.art_frame(ctx, 3)
    cp = ctx.cp

    def scene(src):
        return lambda t, dt, out: src.render(t, dt, out)

    def still(img):
        return lambda t, dt, out: cp.copyto(out, img)
    return [("calm", scene(A)), ("busy", scene(B)), ("art", still(art1)), ("photo", still(art2))]


def measure_effect(ctx, cls, params: dict, inputs, st: Stats) -> dict:
    cp = ctx.cp
    props = cls.param_props()
    p = dict(params)
    if "amount" in props:
        p["amount"] = 0.8
    src, out = ctx.new_frame(), ctx.new_frame()
    acc = {"change": [], "detail": [], "light": [], "keeps": [], "temporal": []}
    t0 = time.monotonic()
    for _label, render_in in inputs:
        e = cls()
        e.setup(ctx, cls.validate(p))
        try:
            prev_in = prev_out = None
            n = int(EFFECT_S * FPS)
            for i in range(n):
                if time.monotonic() - t0 > ITEM_WALL_S:
                    raise TimeoutError(f"slower than {ITEM_WALL_S:.0f} s")
                t = 2.0 + i / FPS                       # the inputs are past their first frames
                dt = 0.0 if i == 0 else 1.0 / FPS
                render_in(t, dt, src)
                e.render(i / FPS, dt, src, out)
                fi, li = st.luma(src)
                fo, lo = st.luma(out)
                if i / FPS >= WARM_S:
                    acc["change"].append(float(cp.abs(fo - fi).mean()))
                    acc["detail"].append(st.busy(lo) - st.busy(li))
                    acc["light"].append(float(lo.mean() - li.mean()))
                    acc["keeps"].append(st.corr(li, lo))
                    if prev_in is not None:
                        din = float(cp.abs(li - prev_in).mean())
                        dout = float(cp.abs(lo - prev_out).mean())
                        if din > 1e-3:
                            acc["temporal"].append(dout / din)
                prev_in, prev_out = li, lo
            ctx.stream.synchronize()
        finally:
            try:
                e.close()
            except Exception:
                pass
    return {k: round(float(np.median(v) if k == "temporal" else np.mean(v)), 4) for k, v in acc.items() if v}


def rank(items: dict, keys: tuple) -> None:
    for k in keys:
        vals = sorted(v[k] for v in items.values() if isinstance(v, dict) and k in v)
        if not vals:
            continue
        for v in items.values():
            if isinstance(v, dict) and k in v:
                lo = sum(1 for x in vals if x < v[k])
                eq = sum(1 for x in vals if x == v[k])
                v[f"{k}_pct"] = round((lo + 0.5 * eq) / len(vals), 3)


def plain(params) -> dict:
    """A preset's plain param values (bindings live beside them as dicts: the run drives its own)."""
    return {k: v for k, v in (params or {}).items() if not isinstance(v, dict)}


def ok(sec: dict, name: str) -> bool:
    """Measured already (a failed item is tried again)."""
    return name in sec and "error" not in sec[name]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--only", choices=("scenes", "effects"))
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    logging.disable(logging.WARNING)
    log = lambda m: print(time.strftime("%H:%M:%S"), m, flush=True)       # noqa: E731
    from stream_service.gpu import RenderContext
    from stream_service.plugins import Registry
    from stream_service.presets import Presets
    from stream_service.tools import fxtest as fx

    reg = Registry().discover()
    P = Presets(REPO / "config/live", reg)
    out_path = pathlib.Path(args.out)
    try:
        doc = {} if args.fresh else json.loads(out_path.read_text())
    except (OSError, ValueError):
        doc = {}
    doc = {"format": 1, "gen": doc.get("gen") or {}, "scenes": doc.get("scenes") or {},
           "effects": doc.get("effects") or {}}

    def save() -> None:
        for sec, keys in RANKED.items():
            rank(doc[sec], keys)
        doc["gen"] = {"by": "screen-machine-2b (tools/lookstats.py)",
                      "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                      "size": [W, H], "fps": FPS, "n": {k: len(doc[k]) for k in ("scenes", "effects")}}
        tmp = out_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=0, sort_keys=True) + "\n")
        tmp.replace(out_path)

    jobs = []
    if args.only in (None, "scenes"):
        src = reg.table("source")
        for n, e in sorted(P.scenes.items()):
            tags = e.get("tags") or {}
            if not tags or tags.get("auto") is False or e.get("source") not in src:
                continue
            if (args.names and n not in args.names) or (ok(doc["scenes"], n) and not args.names and not args.fresh):
                continue
            jobs.append(("scenes", n, src[e["source"]], plain(e.get("params"))))
    if args.only in (None, "effects"):
        eff = reg.table("effect")
        for n, e in sorted(P.effects.items()):
            tags = e.get("tags") or {}
            if not tags or tags.get("auto") is False or e.get("effect") not in eff:
                continue
            if (args.names and n not in args.names) or (ok(doc["effects"], n) and not args.names and not args.fresh):
                continue
            jobs.append(("effects", n, eff[e["effect"]], plain(e.get("params"))))
    log(f"{len(jobs)} items to measure on GPU {fx.GPU} at {W}x{H}")
    t_run = time.monotonic()
    done = 0
    ctx = None
    inputs = None
    st = None
    for sec, name, cls, params in jobs:
        if time.monotonic() - t_run > RUN_WALL_S:
            log(f"run wall time reached: stopping with {len(jobs) - done} left (rerun to resume)")
            break
        t_run += wait_idle(log)                            # waiting doesn't count against the run's wall time
        with fx.gpu_slots(False):
            if ctx is None:
                ctx = RenderContext(fx.GPU, W, H, FPS, prealloc_buffers=0)
                st = Stats(ctx.cp)
            with ctx.device, ctx.stream:
                try:
                    if sec == "scenes":
                        res = measure_scene(ctx, cls, params, st)
                    else:
                        if inputs is None:
                            inputs = effect_inputs(ctx)
                        res = measure_effect(ctx, cls, params, inputs, st)
                    res["plugin"] = getattr(cls, "name", None) or cls.__name__
                    if sec == "scenes":                       # drawn in the room's palette (structural, not measured)
                        res["palette_driven"] = "palette" in cls.param_props()
                    doc[sec][name] = res
                except Exception as e:                      # report, don't hide
                    doc[sec][name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        done += 1
        if done % 25 == 0:
            save()
            log(f"{done}/{len(jobs)}")
    save()
    errs = [f"{s}/{n}" for s in ("scenes", "effects") for n, v in doc[s].items() if "error" in v]
    log(f"done: {doc['gen']['n']} measured, {len(errs)} errors {errs[:10]}")


if __name__ == "__main__":
    main()
