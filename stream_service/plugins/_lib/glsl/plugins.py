"""Base classes that turn a GLSL shader into an ordinary plug-in.  A generated plug-in file is a docstring
(source, author, licence), the verbatim GLSL and a class with ``name``, ``params`` and ``GLSL``.

* :class:`GlslTransition`: gl-transitions (``vec4 transition(vec2 uv)``).  Endpoints are pure (p<=0 copies a,
  p>=1 copies b).  Shader params are uploaded once in setup (transition params never change mid-run).
* :class:`GlslSource`: a Shadertoy image pass (``mainImage``) or an ISF generator.  The wrapper supplies the
  standard macro interface: the shader clock is integrated (``speed`` x energy x beat kick, so nothing jumps),
  ``pulse`` also flashes, ``intensity`` is gain, ``palette_mix`` remaps luma through the (session) palette,
  ``render_scale`` renders small and upsamples.  Audio inputs get textures synthesised from energy / pulse.
* :class:`GlslEffect`: an ISF filter.  ``amount`` mixes src -> filtered (0 = exact copy), ``pulse`` pushes
  amount on the beat; ISF ``event`` inputs fire on the beat.

Parameters map to uniforms through each prop's ``x-glsl`` entry (see runtime.apply_params).
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, ClassVar, Optional

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_effect_params, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size
from stream_service.plugins.base import Source, Transition

from .runtime import IsfPasses, Program, ShowTextures, apply_params, sampler

_POST_SRC = HELPERS + PALETTE_CUDA + r"""
extern "C" __global__
void glsl_post(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int w, const int h,
               const int W, const int H, const float gain, const float contrast, const float pmix,
               const float period, const float drift, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 c = (w == W && h == H) ? ld(src, x, y, W, H) : bilin(src, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    float r = c.x / 255.f, g = c.y / 255.f, b = c.z / 255.f;
    r = (r - 0.5f) * contrast + 0.5f; g = (g - 0.5f) * contrast + 0.5f; b = (b - 0.5f) * contrast + 0.5f;
    if (pmix > 0.f) {
        const float l = clamp01(0.2126f * r + 0.7152f * g + 0.0722f * b);
        const float3 q = pal(l * period + drift, P);
        const float k = 0.15f + 0.85f * sqrtf(l);
        r += (q.x * k - r) * pmix; g += (q.y * k - g) * pmix; b += (q.z * k - b) * pmix;
    }
    out[y * W + x] = pack(make_float4(r * gain * 255.f, g * gain * 255.f, b * gain * 255.f, 255.f));
}

extern "C" __global__
void glsl_upmix(const uchar4* __restrict__ src, const uchar4* __restrict__ small, uchar4* __restrict__ out,
                const int w, const int h, const int W, const int H, const float t)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 a = ld(src, x, y, W, H);
    const float4 b = bilin(small, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    out[y * W + x] = pack(mix4(a, b, t));
}

extern "C" __global__
void glsl_mix(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out, const int n,
              const float t)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const uchar4 p = a[i], q = b[i];
    out[i] = make_uchar4((unsigned char)(p.x + (q.x - p.x) * t + 0.5f), (unsigned char)(p.y + (q.y - p.y) * t + 0.5f),
                         (unsigned char)(p.z + (q.z - p.z) * t + 0.5f), 255);
}
"""


def _kernels():
    mod = raw_module(_POST_SRC)
    return mod.get_function("glsl_post"), mod.get_function("glsl_mix"), mod.get_function("glsl_upmix")


def _smooth_noise(cp, seed: int = 7, n: int = 24):
    """A small random RGBA texture; sampled with bilinear filtering + repeat it reads as smooth blobs (for
    gl-transitions' luma / displacement maps)."""
    rng = np.random.default_rng(seed)
    return cp.asarray(rng.integers(0, 256, (n, n, 4), dtype=np.uint8))


def _mix(cp, kmix, a, b, out, t: float) -> None:
    n = out.size // 4
    kmix(((n + 255) // 256,), (256,), (a, b, out, np.int32(n), np.float32(t)))


log = logging.getLogger("stream_service.plugins.glsl")

# Frame-time thresholds (GPU ms per render call).  Imported shaders are untrusted for cost: a shader over its
# "slow" threshold is shrunk (render scale) until it fits.  At the minimum scale it keeps running (owner, 15 Oct:
# degrade, never refuse): the engine then lowers a scene's frame rate (layers.py rate_div) and its guard (guard.py)
# decides.  Only a shader over "hard" for a few frames in a row raises GpuTooSlow here, and the engine substitutes it
# (scene -> black + recovery cue, transition -> crossfade, effect -> passthrough).  The kernels themselves are bounded
# too: every loop carries a per-pixel iteration cap and an SM-clock budget (glsl_compat.cuh), so no launch can hang
# the GPU.
SCENE_SLOW_MS, EFFECT_SLOW_MS, TRANSITION_SLOW_MS, HARD_MS = 20.0, 12.0, 25.0, 200.0
SCENE_MIN_SCALE, EFFECT_MIN_SCALE = 0.25, 0.35


class GpuTooSlow(RuntimeError):
    pass


class _Watch:
    """GPU time of one plug-in's render calls, read from CUDA events one frame late (never syncs the render
    thread)."""

    def __init__(self, cp, name: str, slow_ms: float):
        self.cp, self.name, self.slow_ms = cp, name, slow_ms
        self.pending = None
        self.strikes = 0

    def begin(self):
        e = self.cp.cuda.Event()
        e.record()
        return e

    def end(self, e0) -> None:
        e1 = self.cp.cuda.Event()
        e1.record()
        self.pending = (e0, e1)

    def poll(self) -> Optional[float]:
        if self.pending is None or not self.pending[1].done:
            return None
        ms = self.cp.cuda.get_elapsed_time(*self.pending)
        self.pending = None
        return ms

    def judge(self, scale: float, min_scale: float) -> Optional[float]:
        """None = fine; a smaller scale to switch to; raises GpuTooSlow when shrinking cannot help."""
        ms = self.poll()
        if ms is None or ms <= self.slow_ms:
            if ms is not None:
                self.strikes = 0
            return None
        if scale > min_scale + 1e-3:
            new = max(min_scale, round(scale * (self.slow_ms / ms) ** 0.5 * 0.9, 2))
            log.warning("%s: %.1f ms on the GPU, render scale %.2f -> %.2f", self.name, ms, scale, new)
            return new
        if ms <= HARD_MS:                      # slow but workable: the engine degrades the frame rate instead
            self.strikes = 0
            return None
        self.strikes += 1
        if self.strikes >= 3:
            raise GpuTooSlow(f"{self.name}: {ms:.1f} ms on the GPU at render scale {scale:.2f}")
        return None


def _date() -> tuple[float, float, float, float]:
    lt = time.localtime()
    return (lt.tm_year, lt.tm_mon - 1, lt.tm_mday, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)


# ------------------------------------------------------------------------------------------- transitions

class GlslTransition(Transition):
    dialect: ClassVar[str] = "glt"
    GLSL: ClassVar[str] = ""
    default_duration_s: ClassVar[float] = 1.5

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self.prog = Program(self.dialect, self.GLSL)
        apply_params(self.prog, self.param_props(), self.params)
        self.prog.set("ratio", ctx.width / ctx.height)
        self._watch = _Watch(cp, self.name, TRANSITION_SLOW_MS)
        self._maps = []
        for u in self.prog.tr.uniforms:     # gl-transitions luma / displacement maps: procedural stand-ins
            if u.type == "sampler2D" and u.name not in ("from", "to"):
                tex = _smooth_noise(cp, seed=len(self._maps) + 7)
                self._maps.append(tex)
                self.prog.set(u.name, sampler(tex, wrap="repeat"))

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        self._watch.judge(1.0, 1.0)       # transitions run at full size: too slow -> raise (crossfade)
        p = self.prog
        p.set("progress", progress)
        p.set("from", sampler(a))
        p.set("to", sampler(b))
        e0 = self._watch.begin()
        p.launch(out)
        self._watch.end(e0)


# ------------------------------------------------------------------------------------------- scenes

def scene_params(shader_params: dict, *, render_scale: float = 0.5) -> dict:
    """Shader params, then the standard macros, plus ``palette_mix`` (0 = the shader's own colours)."""
    d = {**shader_params, **standard_scene_params(palette="neon", render_scale=render_scale, drift=0.03)}
    d["palette"] = {**d["palette"], "x-palette": True}
    d["palette_mix"] = num(0.0, 1.0, 0.0, "Palette remap", tier="regional")
    return d


class GlslSource(Source):
    dialect: ClassVar[str] = "st"          # "st" (Shadertoy image pass) | "isf" (ISF generator)
    GLSL: ClassVar[str] = ""
    VS: ClassVar[Optional[str]] = None     # ISF vertex shader, if any
    channels: ClassVar[dict] = {}          # Shadertoy: {0: "music" | "noise" | "notes", ...}
    listens: ClassVar[tuple] = ("spectrum", "notes")    # self.sound (sound.py): the audio and notes textures
    events: ClassVar[tuple] = ()           # ISF event inputs: fired on the beat

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.prog = Program(self.dialect, self.GLSL, self.VS)
        self._post, self._kmix, _ = _kernels()
        self.tex = ShowTextures(cp)
        self._pal = cp.zeros(12, cp.float32)
        self._watch = _Watch(cp, self.name, SCENE_SLOW_MS)
        self.scale_cap = 1.0
        self._alloc(params["render_scale"])
        self.t = float(params.get("seed", 1) % 997) * 3.17   # seed = a different moment of the shader
        self.drift = 0.0
        self.frame = 0
        self._beat_was = 0.0
        self.prog.set("iMouse", (0.0, 0.0, 0.0, 0.0))
        self.prog.set("iSampleRate", 44100.0)

    def _alloc(self, scale: float) -> None:
        cp = self.ctx.cp
        self.scale = scale
        w, h = internal_size(self.ctx, scale)
        w = max(16, (w + 7) // 8 * 8)
        self.w, self.h = w, h
        self.buf = cp.zeros((h, w, 4), cp.uint8)
        self.passes = IsfPasses(self.prog, cp, w, h) if self.dialect == "isf" else None
        if self.passes:
            self.passes.allocate()

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        shrink = self._watch.judge(self.scale, SCENE_MIN_SCALE)
        if shrink is not None:
            self.scale_cap = shrink
        scale = min(p["render_scale"], self.scale_cap)
        if abs(scale - self.scale) > 0.02:
            self._alloc(scale)
        e0 = self._watch.begin()
        energy, pulse = p["energy"], p["pulse"]
        rate = p["speed"] * (0.35 + 1.3 * energy) * (1.0 + 0.8 * pulse)
        step = dt * rate
        self.t += step
        self.drift += p["drift_speed"] * p["speed"] * dt
        k = self.prog
        apply_params(k, self.param_props(), p)
        self.tex.update(self.t, energy, pulse, sound_frame(self))
        if self.dialect == "st":
            k.set("iTime", self.t)
            k.set("iTimeDelta", step)
            k.set("iFrame", self.frame)
            k.set("iFrameRate", float(self.ctx.fps))
            k.set("iResolution", (self.w, self.h, 1.0))
            k.set("iDate", _date())
            for i in range(4):
                kind = self.channels.get(i) or self.channels.get(str(i))
                tex = {"music": self.tex.music, "noise": self.tex.noise, "notes": self.tex.notes}.get(kind)
                k.set(f"iChannel{i}", sampler(tex, wrap="repeat" if kind == "noise" else "clamp"))
                if tex is not None:
                    k.u["iChannelResolution"][i] = (tex.shape[1], tex.shape[0], 1.0)
            k.launch(self.buf)
        else:
            self._isf_inputs(k, pulse)
            self.passes.render(self.buf, self.t, step)
        self.frame += 1
        rows = self.session_palette()
        coeffs = palette_coeffs(p["palette"]) if rows is None else np.asarray(rows, np.float32).ravel()
        self._pal.set(np.asarray(coeffs, np.float32))
        gain = p["intensity"] * (1.0 + 0.35 * pulse)
        contrast = 0.85 + 0.3 * energy
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._post, W, H, (self.buf, out, np.int32(self.w), np.int32(self.h), np.int32(W), np.int32(H),
                                     np.float32(gain), np.float32(contrast), np.float32(p["palette_mix"]),
                                     np.float32(p["palette_period"]), np.float32(self.drift), self._pal))
        self._watch.end(e0)

    def _isf_inputs(self, k: Program, pulse: float) -> None:
        for u in k.tr.params:
            ty = (u.meta or {}).get("TYPE")
            if ty == "audioFFT":
                k.set(u.name, sampler(self.tex.fft))
            elif ty == "audio":
                k.set(u.name, sampler(self.tex.wave))
            elif ty == "image":
                k.set(u.name, sampler(self.tex.noise, wrap="repeat"))
        fire = pulse > 0.5 and self._beat_was <= 0.5
        self._beat_was = pulse
        for name in self.events:
            k.set(name, 1 if fire else 0)


def sound_frame(plugin: Any) -> Any:
    """The session's sound on a plug-in (sound.Sound, set for classes that declare ``listens``), or None."""
    s = getattr(plugin, "sound", None)
    return None if s is None or isinstance(s, (tuple, list, str)) else s


# ------------------------------------------------------------------------------------------- effects

def effect_params(shader_params: dict) -> dict:
    return {**standard_effect_params(), **shader_params}


PULSE_LIFT = 0.6           # an effect's beat: the mix lifts by up to this share of its amount ...
PULSE_HEADROOM = 0.25      # ... from a base this much lower while beats come, so an authored 1.0 pulses too
PULSE_HOLD_S = 3.0         # "beats are coming" fades over this long after the last one


def pulse_mix(amount: float, pulse: float, hold: float) -> float:
    """An effect's mix this frame (75, 17 Oct: a pulse must show at amount 1.0).  ``hold`` (0-1) says beats are
    coming: the base dips by PULSE_HEADROOM x hold and each pulse lifts it back and beyond, capped at 1.  With no
    beats (hold 0, pulse 0) it is exactly the authored amount."""
    h = min(1.0, max(0.0, hold))
    base = amount * (1.0 - PULSE_HEADROOM * h)
    return min(1.0, base + amount * (PULSE_LIFT + PULSE_HEADROOM * h) * pulse)


class GlslEffect(Effect):
    dialect: ClassVar[str] = "isf"
    GLSL: ClassVar[str] = ""
    VS: ClassVar[Optional[str]] = None
    stage: ClassVar[str] = "stylise"
    events: ClassVar[tuple] = ()
    scale: ClassVar[float] = 1.0           # < 1: heavy shader, rendered small then upsampled into the mix
    listens: ClassVar[tuple] = ("spectrum", "notes")    # self.sound (sound.py): the audio textures

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.prog = Program(self.dialect, self.GLSL, self.VS)
        self._alloc(self.scale)
        self._watch = _Watch(cp, self.name, EFFECT_SLOW_MS)
        self.tex = ShowTextures(cp)
        _, self._kmix, self._kup = _kernels()
        self.t = 0.0
        self._beat_was = 0.0
        self._hold = 0.0

    def _alloc(self, scale: float) -> None:
        ctx, cp = self.ctx, self.ctx.cp
        self.cur_scale = scale
        self.w, self.h = (ctx.width, ctx.height) if scale >= 0.999 else internal_size(ctx, scale)
        self.passes = IsfPasses(self.prog, cp, self.w, self.h)
        self.passes.allocate()
        self.tmp = cp.zeros((self.h, self.w, 4), cp.uint8)

    def render(self, t_local: float, dt: float, src, out) -> None:
        cp = self.ctx.cp
        shrink = self._watch.judge(self.cur_scale, EFFECT_MIN_SCALE)
        if shrink is not None:
            self._alloc(shrink)
        p = self.step_params(dt)
        decay = math.exp(-dt / PULSE_HOLD_S) if dt > 0.0 else 1.0
        self._hold = max(float(p["pulse"]), self._hold * decay)
        amount = pulse_mix(float(p["amount"]), float(p["pulse"]), self._hold)
        self.t += dt
        if amount <= 1e-3:
            cp.copyto(out, src)
            return
        k = self.prog
        apply_params(k, self.param_props(), p)
        self.tex.update(self.t, amount, p["pulse"], sound_frame(self))
        for u in k.tr.params:
            ty = (u.meta or {}).get("TYPE")
            if ty == "image":
                k.set(u.name, sampler(src))
            elif ty == "audioFFT":
                k.set(u.name, sampler(self.tex.fft))
            elif ty == "audio":
                k.set(u.name, sampler(self.tex.wave))
        fire = p["pulse"] > 0.5 and self._beat_was <= 0.5
        self._beat_was = p["pulse"]
        for name in self.events:
            k.set(name, 1 if fire else 0)
        e0 = self._watch.begin()
        if (self.w, self.h) != (self.ctx.width, self.ctx.height):
            self.passes.render(self.tmp, self.t, dt)
            W, H = self.ctx.width, self.ctx.height
            launch_2d(self._kup, W, H, (src, self.tmp, out, np.int32(self.w), np.int32(self.h), np.int32(W),
                                        np.int32(H), np.float32(amount)))
        elif amount >= 0.999:
            self.passes.render(out, self.t, dt)
        else:
            self.passes.render(self.tmp, self.t, dt)
            _mix(cp, self._kmix, src, self.tmp, out, amount)
        self._watch.end(e0)
