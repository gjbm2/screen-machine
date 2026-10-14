"""fxtest: compile, preview, check and benchmark one plug-in (source, transition or effect).

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.fxtest stream_service.plugins.sources.warp_stack:WarpStack \
      --preset '{"field": "plasma"}' [--bindings '{...}'] [--bench] [--video 8] [--name NAME]

Writes out/<name>/: sheet.png (contact sheet), summary.json, and video.mp4 with --video.
Run from the repo root (python -m stream_service.tools.fxtest).  GPU: $FX_GPU (default 1, the P4000).  A GPU slot lock (3 slots) keeps
previews from piling up; --bench takes all slots, so its timings are uncontended by other fx agents.

Sources are driven by a synthetic song (124 bpm pop, show_synth.Song): signals energy (smoothed activity),
beat (decaying envelope on each beat), pulse (raw), level, rgb.  Default bindings follow the standard macro
interface: energy<-energy, pulse<-beat.  Bindings: {"param": {"bind": sig, "range": [lo, hi]}} sets;
{"param": {"kick": sig, "amount": a}} adds.

Checks reported in summary.json:
  * dead_params: realtime/regional numeric params whose min vs max render differs by < 0.75/255 mean (a dead knob)
  * jumpy: for sources, the largest frame-to-frame mean change during the run vs the median (flicker / jumps)
  * endpoints (transitions): mean |out - A| at p=0 and |out - B| at p=1 (should be ~0)
  * bench_ms (with --bench): 4K ms/frame on the GPU; scenes at their render_scale (budget <= 6 ms on the P4000),
    transitions (budget <= 3 ms), effects (budget <= 2 ms)
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib
import json
import math
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
WORK = pathlib.Path.home() / ".cache" / "sm-fxtest"     # previews, sheets, videos, GPU slot locks
WORK.mkdir(parents=True, exist_ok=True)
ART = REPO / "output"                                      # the real art stills (read-only)

import numpy as np  # noqa: E402

GPU = int(os.environ.get("FX_GPU", "1"))
SLOTS = 3
FPS = 30


@contextlib.contextmanager
def gpu_slots(exclusive: bool):
    lockdir = WORK / ".gpulock"
    lockdir.mkdir(exist_ok=True)
    held = []
    try:
        if exclusive:
            for i in range(SLOTS):
                f = open(lockdir / f"gpu{GPU}.slot{i}", "w")
                fcntl.flock(f, fcntl.LOCK_EX)
                held.append(f)
        else:
            while not held:
                for i in range(SLOTS):
                    f = open(lockdir / f"gpu{GPU}.slot{i}", "w")
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held.append(f)
                        break
                    except BlockingIOError:
                        f.close()
                if not held:
                    time.sleep(0.5)
        yield
    finally:
        for f in held:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()


def load(spec: str):
    mod, cls = spec.split(":")
    return getattr(importlib.import_module(mod), cls)


def kind_of(cls) -> str:
    from stream_service.plugins.base import Source, Transition
    from stream_service.plugins._lib.effect_base import Effect
    if issubclass(cls, Source):
        return "source"
    if issubclass(cls, Transition):
        return "transition"
    if issubclass(cls, Effect):
        return "effect"
    raise SystemExit(f"{cls} is not a Source, Transition or Effect")


class Signals:
    """Synthetic show signals at time t (deterministic)."""

    def __init__(self, bpm=124.0):
        from stream_service.tools.show_synth import Song
        self.song = Song("pop", bpm, 7)
        self.song.t0 = 0.0
        self.energy = None
        self.last_t = None

    def at(self, t: float) -> dict:
        st = self.song.state(t)
        fx = st["fixtures"]["screen.north"]
        a = st["activity"]
        if self.energy is None:
            self.energy = a
        else:
            dt = max(0.0, t - self.last_t)
            self.energy += (a - self.energy) * (1 - math.exp(-dt / 2.0))
        self.last_t = t
        spb = self.song.spb
        since = (t - self.song.t0) % spb
        beat = math.exp(-since / 0.15)
        return {"energy": self.energy, "activity": a, "beat": beat, "pulse": fx["pulse"], "level": fx["level"],
                "rgb": fx["rgb"], "pan": fx["pan"], "tilt": fx["tilt"], "zoom": fx["zoom"]}


def resolve(params: dict, bindings: dict, sig: dict) -> dict:
    q = dict(params)
    for k, b in bindings.items():
        if k in q and "bind" in b:
            s = min(max(float(sig.get(b["bind"], 0.0)), 0.0), 1.0) ** b.get("curve", 1.0)
            lo, hi = b.get("range", [0.0, 1.0])
            q[k] = lo + (hi - lo) * s
    for k, b in bindings.items():
        if k in q and "kick" in b:
            q[k] = q[k] + b.get("amount", 1.0) * float(sig.get(b["kick"], 0.0))
    return q


def drive(obj, bindings: dict, sigsrc: Signals | None, fixed: dict | None = None):
    """Wrap obj.step_params so each frame's params get bindings (from sigsrc) or fixed overrides applied."""
    orig = obj.step_params
    obj._fx_t = 0.0

    def step(dt):
        p = orig(dt)
        if fixed:
            p = {**p, **fixed}
        if sigsrc is not None and bindings:
            p = resolve(p, bindings, sigsrc.at(obj._fx_t))
        return p

    obj.step_params = step


def to_img(cp, frame, w=480):
    from PIL import Image
    im = Image.fromarray(cp.asnumpy(frame)[:, :, :3])
    return im.resize((w, int(w * im.height / im.width)))


def sheet(rows: list[list], labels: list[list[str]], path: pathlib.Path, title: str):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    tw, th = rows[0][0].size
    cols = max(len(r) for r in rows)
    img = Image.new("RGB", (cols * tw, len(rows) * (th + 18) + 22), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.text((6, 4), title, fill=(230, 230, 230), font=font)
    for r, (row, labs) in enumerate(zip(rows, labels)):
        for c, (tile, lab) in enumerate(zip(row, labs)):
            y = 22 + r * (th + 18)
            img.paste(tile, (c * tw, y))
            d.text((c * tw + 4, y + th + 1), lab, fill=(200, 200, 200), font=font)
    img.save(path)


def numeric_knobs(cls) -> list[tuple[str, dict]]:
    out = []
    for k, prop in cls.param_props().items():
        if prop.get("type") in ("number", "integer") and prop.get("x-tier") in ("realtime", "regional") \
                and k not in ("duration_s", "seed", "render_scale"):
            out.append((k, prop))
    return out


def SWEEP_HOLD(cls) -> dict:
    """Standard macros held mid-way during knob sweeps (pulse 0.5 so beat-response knobs show)."""
    props = cls.param_props()
    return {k: v for k, v in (("pulse", 0.5), ("energy", 0.5), ("amount", 0.7)) if k in props}


def mad(cp, a, b) -> float:
    return float(cp.abs(a.astype(cp.int16) - b.astype(cp.int16)).mean())


# ----------------------------------------------------------------------------- sources
def ref_scene(ctx, which: str):
    from stream_service.plugins.sources.warp_stack import WarpStack
    presets = {
        "A": dict(field="fbm", field_scale=1.2, field_speed=0.15, w1_type="domain", w1_amount=0.9, w1_scale=1.1,
                  w1_speed=0.12, w1_n=2, palette="dusk", palette_period=1.3, drift_speed=0.04, contrast=0.5,
                  render_scale=0.5),
        "B": dict(field="plasma", field_scale=5, field_speed=0.6, w1_type="swirl", w1_amount=2, w1_scale=1.5,
                  w1_speed=0.1, w2_type="kaleido", w2_n=6, w2_scale=1, w2_speed=0.15, palette="acid",
                  fb_amount=0.55, fb_zoom=1.15, fb_rotate=10, fb_chroma=0.004, render_scale=0.5),
    }
    s = WarpStack()
    s.setup(ctx, WarpStack.validate(presets[which]))
    return s


def preview_source(cls, preset, bindings, ctx, outdir, video_s):
    cp = ctx.cp
    summary = {}
    sig = Signals()
    s = cls()
    s.setup(ctx, cls.validate(preset))
    drive(s, bindings, sig)
    frame, prev = ctx.new_frame(), ctx.new_frame()
    T = 8.0
    n = int(T * FPS)
    times = {int(k * FPS) for k in (1, 2.5, 4, 5.5, 7, 8 - 1 / FPS)}
    tiles, labs, diffs = [], [], []
    ff = None
    if video_s:
        n = int(video_s * FPS)
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s",
                               f"{ctx.width}x{ctx.height}", "-r", str(FPS), "-i", "-", "-i", str(outdir / "click.wav"),
                               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-shortest", str(outdir / "video.mp4")], stdin=subprocess.PIPE)
        click_track(sig.song, video_s, outdir / "click.wav")
    for i in range(n):
        s._fx_t = i / FPS
        s.render(i / FPS, 0.0 if i == 0 else 1 / FPS, frame)
        if i > FPS:
            diffs.append(mad(cp, frame, prev))
        cp.copyto(prev, frame)
        if i in times:
            tiles.append(to_img(cp, frame))
            labs.append(f"t={i / FPS:.1f}s e={sig.at(i / FPS)['energy']:.2f}")
        if ff:
            ff.stdin.write(cp.asnumpy(frame).tobytes())
    if ff:
        ff.stdin.close()
        ff.wait()
    d = np.array(diffs)
    summary["motion_mad_median"] = round(float(np.median(d)), 3)
    summary["motion_mad_max"] = round(float(d.max()), 3)
    summary["jumpy_ratio"] = round(float(d.max() / max(np.median(d), 1e-3)), 2)
    summary["static_warning"] = bool(np.median(d) < 0.15)
    # knob sweep: each numeric realtime/regional param at min and max (fresh instance, 1 s warm-up, no signals)
    rows2, labs2, dead = [], [], []
    for k, prop in numeric_knobs(cls)[:10]:
        pair = []
        for v in (prop["minimum"], prop["maximum"]):
            if prop.get("x-tier") == "realtime" and prop.get("x-bindable") == "rate" and v > 4 * max(1.0, abs(prop.get("default", 1))):
                v = 4 * max(1.0, abs(prop.get("default", 1))) * (1 if v > 0 else -1)
            x = cls()
            x.setup(ctx, cls.validate({**SWEEP_HOLD(cls), **preset, k: v}))
            f = ctx.new_frame()
            for i in range(FPS):
                x.render(i / FPS, 0.0 if i == 0 else 1 / FPS, f)
            pair.append(f)
            x.close()
        m = mad(cp, pair[0], pair[1])
        if m < 0.75:
            dead.append(k)
        rows2 += [to_img(cp, pair[0], 240), to_img(cp, pair[1], 240)]
        labs2 += [f"{k}=min", f"{k}=max d={m:.1f}"]
    summary["dead_params"] = dead
    rows = [tiles]
    labels = [labs]
    per = 8
    for j in range(0, len(rows2), per):
        rows.append(rows2[j:j + per])
        labels.append(labs2[j:j + per])
    # normalise tile sizes for the sheet (sweep tiles are half size)
    from PIL import Image
    tw, th = rows[0][0].size
    rows = [[t if t.size == (tw, th) else _pad(t, tw // 2, th // 2) for t in r] for r in rows]
    _sheet_mixed(rows, labels, outdir / "sheet.png", f"{cls.name}: run (top, synthetic 124 bpm song) + knob sweeps")
    s.close()
    return summary


def _pad(t, w, h):
    return t.resize((w, h))


def _sheet_mixed(rows, labels, path, title):
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)
    width = max(sum(t.size[0] for t in r) for r in rows)
    height = 22 + sum(r[0].size[1] + 16 for r in rows)
    img = Image.new("RGB", (width, height), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.text((6, 4), title, fill=(230, 230, 230), font=font)
    y = 22
    for row, labs in zip(rows, labels):
        x = 0
        for tile, lab in zip(row, labs):
            img.paste(tile, (x, y))
            d.text((x + 3, y + tile.size[1] + 1), lab, fill=(200, 200, 200), font=font)
            x += tile.size[0]
        y += row[0].size[1] + 16
    img.save(path)


def click_track(song, seconds, path):
    import wave
    sr = 48000
    n = int(seconds * sr)
    a = np.zeros(n, np.float32)
    rng = np.random.default_rng(3)
    t = np.arange(int(0.4 * sr)) / sr
    kick = np.sin(2 * np.pi * (45 * t + (120 / 18) * (1 - np.exp(-18 * t)))) * np.exp(-t / 0.18)
    snare = (rng.standard_normal(t.size) * 0.6 + np.sin(2 * np.pi * 190 * t) * 0.4) * np.exp(-t / 0.07)
    hat = rng.standard_normal(t.size) * np.exp(-t / 0.015) * 0.25
    beat = 0
    while True:
        tb = song.t0 + beat * song.spb
        if tb >= seconds:
            break
        i = int(tb * sr)
        bib = beat % 4
        for snd, on in ((kick, bib in song.kick), (snare, bib in song.snare), (hat, True)):
            if on:
                m = min(snd.size, n - i)
                a[i:i + m] += snd[:m] * 0.5
        j = int((tb + song.spb / 2) * sr)
        if j < n:
            m = min(hat.size, n - j)
            a[j:j + m] += hat[:m] * 0.5
        beat += 1
    a = np.clip(a, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((a * 32000).astype(np.int16).tobytes())


# ----------------------------------------------------------------------------- transitions
def preview_transition(cls, preset, ctx, outdir):
    cp = ctx.cp
    from stream_service.easing import ease
    tr = cls(cls.validate(preset))
    tr.setup(ctx)
    A, B = ref_scene(ctx, "A"), ref_scene(ctx, "B")
    a, b, out = ctx.new_frame(), ctx.new_frame(), ctx.new_frame()
    for i in range(FPS):                       # warm both reference scenes (feedback)
        A.render(i / FPS, 1 / FPS, a)
        B.render(i / FPS, 1 / FPS, b)
    ps = [0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0]
    tiles, labs = [], []
    endpoints = {}
    for k, p in enumerate(ps):
        t = 1.0 + p * tr.duration_s
        A.render(t, 1 / FPS, a)
        B.render(t, 1 / FPS, b)
        tr.render(ease(tr.easing, p), a, b, out)
        if p == 0.0:
            endpoints["p0_vs_A"] = round(mad(cp, out, a), 3)
        if p == 1.0:
            endpoints["p1_vs_B"] = round(mad(cp, out, b), 3)
        tiles.append(to_img(cp, out, 320))
        labs.append(f"p={p:.2f}")
    # a dense strip to show motion through the transition
    _sheet_mixed([tiles[:5], tiles[5:]], [labs[:5], labs[5:]], outdir / "sheet.png",
                 f"{cls.name} ({tr.duration_s:.1f}s, {tr.easing}): A = liquid warp, B = kaleido plasma")
    tr.close()
    return {"endpoints": endpoints, "endpoint_ok": endpoints.get("p0_vs_A", 9) < 0.6 and endpoints.get("p1_vs_B", 9) < 0.6}


def video_transition(cls, preset, ctx, outdir, seconds):
    """A-hold, transition, B-hold, transition back (B->A), A-hold: for eyeballing."""
    cp = ctx.cp
    from stream_service.easing import ease
    A, B = ref_scene(ctx, "A"), ref_scene(ctx, "B")
    a, b, out = ctx.new_frame(), ctx.new_frame(), ctx.new_frame()
    tr = cls(cls.validate(preset))
    tr.setup(ctx)
    d = tr.duration_s
    hold = max(1.0, (seconds - 2 * d) / 3)
    total = int((3 * hold + 2 * d) * FPS)
    ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s",
                           f"{ctx.width}x{ctx.height}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "fast",
                           "-crf", "20", "-pix_fmt", "yuv420p", str(outdir / "video.mp4")], stdin=subprocess.PIPE)
    for i in range(total):
        t = i / FPS
        A.render(t, 1 / FPS, a)
        B.render(t, 1 / FPS, b)
        if t < hold:
            cp.copyto(out, a)
        elif t < hold + d:
            tr.render(ease(tr.easing, (t - hold) / d), a, b, out)
        elif t < 2 * hold + d:
            cp.copyto(out, b)
        elif t < 2 * hold + 2 * d:
            tr.render(ease(tr.easing, (t - 2 * hold - d) / d), b, a, out)
        else:
            cp.copyto(out, a)
        ff.stdin.write(cp.asnumpy(out).tobytes())
    ff.stdin.close()
    ff.wait()


# ----------------------------------------------------------------------------- effects
def art_frame(ctx, k: int = 1):
    """An art still from assets/art<k>.jpg, letterboxed to the frame (the effect must look right on art too)."""
    from PIL import Image
    im = Image.open(ART / ["north-screen.jpg", "south-screen.jpg", "cloud.jpg"][(k - 1) % 3]).convert("RGBA")
    im.thumbnail((ctx.width, ctx.height))
    canvas = Image.new("RGBA", (ctx.width, ctx.height), (0, 0, 0, 255))
    canvas.paste(im, ((ctx.width - im.width) // 2, (ctx.height - im.height) // 2))
    f = ctx.new_frame()
    f[...] = ctx.cp.asarray(np.asarray(canvas, dtype=np.uint8))
    return f


def effect_inputs(ctx):
    """Mixed effect input over 8 s: generative scene A (0-2.5 s), an art still (2.5-5 s), then a melt-ish
    crossfade from scene B to another still (5-8 s).  Returns render(t, out)."""
    cp = ctx.cp
    A, B = ref_scene(ctx, "A"), ref_scene(ctx, "B")
    art1, art2 = art_frame(ctx, 1), art_frame(ctx, 2)
    tmp = ctx.new_frame()

    def render(t, dt, out):
        if t < 2.5:
            A.render(t, dt, out)
        elif t < 5.0:
            cp.copyto(out, art1)
        else:
            B.render(t, dt, tmp)
            w = min(max((t - 5.0) / 3.0, 0.0), 1.0)
            out[...] = (tmp.astype(cp.float32) * (1 - w) + art2.astype(cp.float32) * w).astype(cp.uint8)
    return render


def preview_effect(cls, preset, bindings, ctx, outdir, video_s=0.0):
    cp = ctx.cp
    sig = Signals()
    inp = effect_inputs(ctx)
    src, out = ctx.new_frame(), ctx.new_frame()
    # identity check: amount = 0 on each input class
    ident = []
    for t in (1.0, 3.0, 6.5):
        x = cls()
        x.setup(ctx, cls.validate({**preset, "amount": 0.0}))
        f = ctx.new_frame()
        for i in range(6):
            inp(t, 1 / FPS, src)
            x.render(t + i / FPS, 1 / FPS, src, f)
        ident.append(mad(cp, f, src))
    e = cls()
    e.setup(ctx, cls.validate(preset))
    drive(e, bindings, sig)
    base_amount = float(cls.validate(preset).get("amount", 1.0))
    orig = e.step_params

    def env(t):  # amount envelope: off, fade in, hold, fade out (tests the 0->1 morph)
        T = 8.0
        return min(max((t - 0.4) / 1.2, 0.0), 1.0) * min(max((T - 0.3 - t) / 1.0, 0.0), 1.0)

    def step(dt):
        p = orig(dt)
        return {**p, "amount": base_amount * env(e._fx_t)} if "amount" in p else p
    e.step_params = step
    tiles, labs = [], []
    T = 8.0
    n = int(max(T, video_s or 0) * FPS)
    shown = {int(k * FPS) for k in (1.0, 2.2, 3.5, 4.6, 6.0, 7.2)}
    ff = None
    if video_s:
        click_track(sig.song, n / FPS, outdir / "click.wav")
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s",
                               f"{ctx.width}x{ctx.height}", "-r", str(FPS), "-i", "-", "-i", str(outdir / "click.wav"),
                               "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-shortest", str(outdir / "video.mp4")], stdin=subprocess.PIPE)
    for i in range(n):
        t = i / FPS
        inp(t % T, 1 / FPS, src)
        e._fx_t = t % T
        e.render(t, 1 / FPS, src, out)
        if i in shown:
            tiles += [to_img(cp, src, 320), to_img(cp, out, 320)]
            labs += [f"t={t:.1f} input", f"output a={base_amount * env(t % T):.2f}"]
        if ff:
            ff.stdin.write(cp.asnumpy(out).tobytes())
    if ff:
        ff.stdin.close()
        ff.wait()
    # knob sweeps on the generative input and dead knobs
    rows2, labs2, dead = [], [], []
    inp(1.0, 1 / FPS, src)
    for k, prop in numeric_knobs(cls)[:8]:
        pair = []
        for v in (prop["minimum"], prop["maximum"]):
            x = cls()
            x.setup(ctx, cls.validate({**SWEEP_HOLD(cls), **preset, k: v}))
            f = ctx.new_frame()
            for i in range(10):
                x.render(i / FPS, 1 / FPS, src, f)
            pair.append(f)
        m = mad(cp, pair[0], pair[1])
        if m < 0.75:
            dead.append(k)
        rows2 += [to_img(cp, pair[0], 240), to_img(cp, pair[1], 240)]
        labs2 += [f"{k}=min", f"{k}=max d={m:.1f}"]
    rows = [tiles[:4], tiles[4:8], tiles[8:]] + [rows2[j:j + 8] for j in range(0, len(rows2), 8)]
    labels = [labs[:4], labs[4:8], labs[8:]] + [labs2[j:j + 8] for j in range(0, len(labs2), 8)]
    _sheet_mixed(rows, labels, outdir / "sheet.png",
                 f"{cls.name}: input vs output (scene / art still / crossfade; amount fades in and out) + knob sweeps")
    return {"dead_params": dead, "identity_mad": round(max(ident), 3), "identity_ok": max(ident) <= 1.0}


# ----------------------------------------------------------------------------- bench
def bench(cls, kind, preset, size):
    from stream_service.gpu import RenderContext
    w, h = (int(v) for v in size.split("x"))
    ctx = RenderContext(GPU, w, h, 60, prealloc_buffers=0)
    cp = ctx.cp
    res = []
    with ctx.device, ctx.stream:
        out = ctx.new_frame()
        ev0, ev1 = cp.cuda.Event(), cp.cuda.Event()
        if kind == "source":
            s = cls()
            s.setup(ctx, cls.validate(preset))
            for i in range(30):
                s.render(i / 60, 1 / 60, out)
            for _ in range(3):
                ev0.record()
                for i in range(60):
                    s.render(1 + i / 60, 1 / 60, out)
                ev1.record()
                ev1.synchronize()
                res.append(cp.cuda.get_elapsed_time(ev0, ev1) / 60)
        else:
            rng = np.random.default_rng(0)
            a = cp.asarray(rng.integers(0, 255, (h, w, 4), dtype=np.uint8))
            b = cp.asarray(rng.integers(0, 255, (h, w, 4), dtype=np.uint8))
            if kind == "transition":
                x = cls(cls.validate(preset))
                x.setup(ctx)
                run = lambda i: x.render(0.2 + 0.6 * (i % 60) / 60, a, b, out)
            else:
                x = cls()
                x.setup(ctx, cls.validate(preset))
                run = lambda i: x.render(i / 60, 1 / 60, a, out)
            for i in range(10):
                run(i)
            for _ in range(3):
                ev0.record()
                for i in range(60):
                    run(i)
                ev1.record()
                ev1.synchronize()
                res.append(cp.cuda.get_elapsed_time(ev0, ev1) / 60)
    return round(float(np.median(res)), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--preset", default="{}")
    ap.add_argument("--bindings", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--size", default="960x540")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--bench-size", default="3840x2160")
    ap.add_argument("--video", type=float, default=0.0)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()
    cls = load(args.spec)
    kind = kind_of(cls)
    preset = json.loads(args.preset)
    bindings = json.loads(args.bindings) if args.bindings else (
        {"energy": {"bind": "energy"}, "pulse": {"kick": "beat", "amount": 1.0}} if kind == "source" else
        {"pulse": {"kick": "beat", "amount": 1.0}} if kind == "effect" else {})
    name = args.name or f"{kind}-{cls.name}"
    outdir = WORK / "out" / name
    outdir.mkdir(parents=True, exist_ok=True)
    summary = {"name": name, "class": args.spec, "kind": kind, "preset": preset, "bindings": bindings, "gpu": GPU}
    t0 = time.time()
    try:
        if not args.no_preview:
            from stream_service.gpu import RenderContext
            w, h = (int(v) for v in args.size.split("x"))
            with gpu_slots(False):
                ctx = RenderContext(GPU, w, h, FPS, prealloc_buffers=0)
                with ctx.device, ctx.stream:
                    if kind == "source":
                        summary.update(preview_source(cls, preset, bindings, ctx, outdir, args.video))
                    elif kind == "transition":
                        summary.update(preview_transition(cls, preset, ctx, outdir))
                        if args.video:
                            video_transition(cls, preset, ctx, outdir, args.video)
                    else:
                        summary.update(preview_effect(cls, preset, bindings, ctx, outdir, args.video))
                    ctx.stream.synchronize()
            summary["sheet"] = str(outdir / "sheet.png")
            if args.video:
                summary["video"] = str(outdir / "video.mp4")
        if args.bench:
            with gpu_slots(True):
                summary["bench_ms_4k"] = bench(cls, kind, preset, args.bench_size)
            budget = {"source": 6.0, "transition": 3.0, "effect": 2.0}[kind]
            summary["bench_budget_ms"] = budget
            summary["bench_ok"] = summary["bench_ms_4k"] <= budget
        summary["ok"] = True
    except Exception as e:  # report, don't hide
        import traceback
        summary["ok"] = False
        summary["error"] = f"{type(e).__name__}: {e}"
        summary["traceback"] = traceback.format_exc()[-3000:]
    summary["wall_s"] = round(time.time() - t0, 1)
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "traceback"}, indent=1))
    if not summary["ok"]:
        print(summary["traceback"], file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
