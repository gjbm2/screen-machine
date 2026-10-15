"""Run translated GLSL (translate.py) on our frames: compile, uniforms, samplers, ISF passes, show-bus textures.

* ``program(dialect, src, vs)``: translate (cached per process) and compile through the engine's ``raw_module``
  (CuPy compiles per device and caches on disk).  Uniforms live in a numpy structured scalar that mirrors the
  generated ``struct _U`` and is passed BY VALUE as the kernel argument, so two sessions on one GPU never share
  per-frame state (no module ``__constant__`` globals).
* ``sampler(buf)``: one of our (H, W, 4) uint8 (or float32) frames as a GLSL ``sampler2D`` (row 0 = top, so
  reads are flipped to GL's bottom-left origin).
* ``IsfPasses``: ISF PASSES / TARGET / PERSISTENT / FLOAT / WIDTH / HEIGHT, buffers allocated up front.
* ``ShowTextures``: audio-like textures synthesised from show-bus params (energy, pulse) -- generation, not
  analysis: the renderer does no audio DSP (owner ruling) -- plus a fixed RGBA noise texture for shaders that
  sample a stock Shadertoy texture.
"""
from __future__ import annotations

import functools
import math
import re
import time

from typing import Any

import numpy as np

from stream_service.gpu import raw_module

from . import translate as T

NVRTC_OPTIONS = ("-std=c++20", "--use_fast_math")

_SCALAR = {"float": "<f4", "int": "<i4", "bool": "<i4", "uint": "<u4"}
_VEC = {"vec": "<f4", "ivec": "<i4", "uvec": "<u4", "bvec": "u1"}
SAMPLER_DT = np.dtype([("p", "<u8"), ("w", "<i4"), ("h", "<i4"), ("fmt", "<i4"), ("wrap", "<i4"),
                       ("filter", "<i4"), ("flip", "<i4")], align=True)
WRAP = {"clamp": 0, "repeat": 1, "mirror": 2}
NULL_SAMPLER = (0, 1, 1, 0, 0, 1, 1)


def _field_dtype(u: T.Uniform):
    t = u.type
    if t in _SCALAR:
        base, shape = np.dtype(_SCALAR[t]), ()
    elif t == "sampler2D":
        base, shape = SAMPLER_DT, ()
    elif m := re.fullmatch(r"([iub]?vec)(\d)", t):
        base, shape = np.dtype(_VEC[m.group(1)]), (int(m.group(2)),)
    elif m := re.fullmatch(r"mat(\d)", t):
        n = int(m.group(1))
        base, shape = np.dtype("<f4"), (n * n,)
    else:
        raise TypeError(f"uniform type {t} not supported")
    if u.array:
        shape = (u.array,) + shape
    return (u.name, base, shape) if shape else (u.name, base)


def uniform_dtype(uniforms: list[T.Uniform]) -> np.dtype:
    return np.dtype([_field_dtype(u) for u in uniforms] + [("_g_pad_", "<i4")], align=True)


def sampler(buf, *, wrap: str = "clamp", filter: bool = True, flip: bool = True) -> tuple:
    """A sampler2D record for a CuPy (H, W, 4) uint8 or float32 buffer (row 0 = top -> flip=True)."""
    if buf is None:
        return NULL_SAMPLER
    h, w = buf.shape[:2]
    fmt = 1 if buf.dtype == np.float32 else 0
    return (int(buf.data.ptr), int(w), int(h), fmt, WRAP[wrap], 1 if filter else 0, 1 if flip else 0)


@functools.lru_cache(maxsize=None)
def translation(dialect: str, src: str, vs: str | None = None) -> T.Translation:
    if dialect == "glt":
        return T.translate_gl_transition(src)
    if dialect == "isf":
        return T.translate_isf(src, vs)
    if dialect == "st":
        return T.translate_shadertoy(src)
    raise ValueError(f"unknown GLSL dialect {dialect!r}")


class Program:
    """One compiled shader with its own uniform record (one per plug-in instance)."""

    def __init__(self, dialect: str, src: str, vs: str | None = None):
        self.tr = translation(dialect, src, vs)
        self.dtype = uniform_dtype(self.tr.uniforms)
        self.fn = raw_module(self.tr.cuda, NVRTC_OPTIONS).get_function(self.tr.entry)
        self.u = np.zeros((), self.dtype)
        self.names = set(self.dtype.names)
        self.types = {u.name: u.type for u in self.tr.uniforms}
        self.set_defaults()

    def set_defaults(self) -> None:
        """Upstream defaults (gl-transitions comment defaults, ISF DEFAULT / VVISF fallbacks)."""
        for p in self.tr.params:
            ty = p.type
            if ty == "sampler2D":
                continue
            v = p.default
            meta = p.meta or {}
            if v is None and meta.get("TYPE") == "float":
                lo, hi = meta.get("MIN", 0.0), meta.get("MAX", 1.0)
                v = (lo + hi) / 2 if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) else 0.5
            if v is None and meta.get("TYPE") == "color":
                v = [1.0, 1.0, 1.0, 1.0]
            if v is None and meta.get("TYPE") == "bool":
                v = True
            if v is None and meta.get("TYPE") == "long":
                vals = meta.get("VALUES")
                v = vals[0] if isinstance(vals, list) and vals else 0
            if v is None:
                continue
            if ty == "bool":
                v = int(bool(v))
            n = int(ty[-1]) if ty[-1].isdigit() and "vec" in ty else 0
            if n and isinstance(v, (int, float)):
                v = [v] * n
            if n and isinstance(v, list):
                v = (list(v) + [1.0] * n)[:n]
            try:
                self.u[p.name] = v
            except (ValueError, TypeError):
                pass

    def has(self, name: str) -> bool:
        return name in self.names

    def set(self, name: str, value) -> None:
        if name in self.names:
            self.u[name] = value

    def launch(self, out, w: int | None = None, h: int | None = None) -> None:
        h0, w0 = out.shape[:2]
        w, h = w or w0, h or h0
        fmt = 1 if out.dtype == np.float32 else 0
        # blockDim (16, 8) is required: derivatives use warp shuffles across 2x2 quads (glsl_compat.cuh)
        self.fn(((w + 15) // 16, (h + 7) // 8, 1), (16, 8, 1),
                (self.u[()], out, np.int32(w), np.int32(h), np.int32(fmt)))


# ------------------------------------------------------------------------------------------- params

def hex_rgb(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return int(v[0:2], 16) / 255.0, int(v[2:4], 16) / 255.0, int(v[4:6], 16) / 255.0


def apply_params(prog: Program, props: dict, values: dict) -> None:
    """Write parameter values into uniforms.  A prop's ``x-glsl`` says where: ``{"u": name}`` (scalar),
    ``{"u": name, "c": i}`` (one vector component) or ``{"u": name, "color": n, "alpha": a}`` (a '#rrggbb'
    colour into a vec3/vec4)."""
    for key, prop in props.items():
        g = prop.get("x-glsl")
        if not g or key not in values:
            continue
        name = g["u"]
        if not prog.has(name):
            continue
        v = values[key]
        if "color" in g:
            r, gg, b = hex_rgb(v)
            prog.u[name][:3] = (r, gg, b)
            if g["color"] == 4:
                prog.u[name][3] = g.get("alpha", 1.0)
        elif "c" in g:
            prog.u[name][g["c"]] = v
        elif prog.types.get(name) in ("int", "bool"):
            prog.u[name] = int(round(float(v))) if not isinstance(v, bool) else int(v)
        else:
            prog.u[name] = v


# ------------------------------------------------------------------------------------------- ISF passes

def _size_expr(expr, W: int, H: int, u) -> int | None:
    if expr is None:
        return None
    if isinstance(expr, (int, float)):
        return max(1, int(expr))
    e = str(expr).replace("$WIDTH", str(W)).replace("$HEIGHT", str(H))
    for name in re.findall(r"\$(\w+)", e):
        try:
            e = e.replace(f"${name}", repr(float(u[name])))
        except Exception:
            e = e.replace(f"${name}", "1")
    env = {"floor": math.floor, "ceil": math.ceil, "max": max, "min": min, "pow": pow, "sqrt": math.sqrt,
           "round": round, "abs": abs}
    try:
        return max(1, int(eval(e, {"__builtins__": {}}, env)))  # ISF size expressions are arithmetic only
    except Exception:
        return None


class IsfPasses:
    """Runs an ISF program: one launch per PASS; TARGET buffers double-buffered so a pass reads its own
    previous frame (PERSISTENT feedback).  Buffers are allocated in :meth:`allocate` (setup) and only
    reallocated if a size expression changes."""

    def __init__(self, prog: Program, cp, W: int, H: int):
        self.prog, self.cp, self.W, self.H = prog, cp, W, H
        self.passes = prog.tr.meta.get("passes") or [{}]
        self.targets: dict[str, list] = {}
        self.frame = 0

    def _size(self, p) -> tuple[int, int]:
        u = self.prog.u
        w = _size_expr(p.get("WIDTH"), self.W, self.H, u) or self.W
        h = _size_expr(p.get("HEIGHT"), self.W, self.H, u) or self.H
        return min(w, 4 * self.W), min(h, 4 * self.H)

    def allocate(self) -> None:
        for p in self.passes:
            if p.get("TARGET"):
                self._target(p)

    def _target(self, p):
        w, h = self._size(p)
        dt = np.float32 if p.get("FLOAT") else np.uint8
        bufs = self.targets.get(p["TARGET"])
        if bufs is None or bufs[0].shape[:2] != (h, w) or bufs[0].dtype != dt:
            bufs = [self.cp.zeros((h, w, 4), dt), self.cp.zeros((h, w, 4), dt)]
            self.targets[p["TARGET"]] = bufs
        return bufs

    def render(self, out, t: float, dt: float) -> None:
        k = self.prog
        k.set("TIME", t)
        k.set("TIMEDELTA", dt)
        k.set("FRAMEINDEX", self.frame)
        lt = time.localtime()
        k.set("DATE", (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec))
        for name, bufs in self.targets.items():
            k.set(name, sampler(bufs[0]))
        last = len(self.passes) - 1
        for i, p in enumerate(self.passes):
            k.set("PASSINDEX", i)
            if p.get("TARGET"):
                bufs = self._target(p)
                h, w = bufs[0].shape[:2]
                k.set("RENDERSIZE", (w, h))
                k.set(p["TARGET"], sampler(bufs[0]))        # this pass may read its own previous frame
                k.launch(bufs[1])
                if i == last:                               # the last pass is also the output (VVISF)
                    k.set("RENDERSIZE", (out.shape[1], out.shape[0]))
                    k.launch(out)
                bufs[0], bufs[1] = bufs[1], bufs[0]
                k.set(p["TARGET"], sampler(bufs[0]))        # later passes read what it just wrote
            else:
                k.set("RENDERSIZE", (out.shape[1], out.shape[0]))
                k.launch(out)
        self.frame += 1


# ------------------------------------------------------------------------------------------- show textures

def _audio_rows(t: float, energy: float, beat: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    """FFT-like and waveform rows (0..1) from show-bus values.  Spectral tilt scaled by energy, a kick bump in
    the low bins on the beat, a slow ripple so bands move; waveform amplitude follows energy and beat."""
    x = np.arange(n, dtype=np.float32) / n
    fft = energy * (0.9 - 0.7 * x) * (0.75 + 0.25 * np.sin(17 * x + 3 * t) * np.sin(5 * x - 1.3 * t))
    fft = fft + beat * np.exp(-((x - 0.02) / 0.03) ** 2) + 0.5 * beat * energy * np.exp(-((x - 0.12) / 0.05) ** 2)
    wave = 0.5 + 0.45 * (0.3 + 0.7 * energy + 0.3 * beat) * np.sin(2 * np.pi * (x * 6 + t)) * \
        (0.6 + 0.4 * np.sin(2 * np.pi * x * 23))
    return np.clip(fft, 0, 1), np.clip(wave, 0, 1)


def _to_rgba(rows: list[np.ndarray]) -> np.ndarray:
    tex = np.empty((len(rows), rows[0].shape[0], 4), np.uint8)
    for i, r in enumerate(rows):
        v = np.floor(r * 255 + 0.5).astype(np.uint8)
        tex[i, :, 0] = v
        tex[i, :, 1] = v
        tex[i, :, 2] = v
    tex[..., 3] = 255
    return tex


# show_service's spectrum32 (music.features): 32 log-spaced bands, 0-1 (smoothed upstream).  Edges assumed here
# until c8 publishes them (SHOW_ENGINE §4).  Shadertoy's FFT row is 512 linear bins up to 11 025 Hz.
SPEC_LO_HZ, SPEC_HI_HZ = 30.0, 16000.0
ST_FFT_HZ = 11025.0
NOTE_AGE_CAP_S = 1.0e4


def spectrum_row(spec: Any, n: int) -> np.ndarray:
    """32 log bands -> an n-bin linear-frequency row (0-1), interpolated in log frequency."""
    s = np.clip(np.asarray(spec, np.float32).ravel(), 0.0, 1.0)
    nb = s.shape[0]
    f = (np.arange(n, dtype=np.float32) + 0.5) * (ST_FFT_HZ / n)
    pos = (np.log(np.maximum(f, SPEC_LO_HZ)) - np.log(SPEC_LO_HZ)) / (np.log(SPEC_HI_HZ) - np.log(SPEC_LO_HZ)) * nb - 0.5
    return np.interp(np.clip(pos, 0, nb - 1), np.arange(nb, dtype=np.float32), s).astype(np.float32)


class ShowTextures:
    """Device textures fed from the show: ``music`` (Shadertoy 512x2: GL row y=0.25 FFT, y=0.75 wave), ``fft`` and
    ``wave`` (ISF audioFFT / audio, one row each), ``notes`` (128x1 float RGBA per MIDI pitch: level, velocity, s
    since the strike, s since key-up / -1 while held: VEDA-style) and a fixed 256x256 RGBA ``noise``.

    With sound (``sound``: the session's sound.Sound) the FFT rows are its real spectrum32 while it is present and
    the waveform follows its loudness; without, they are synthesised from energy and the beat as before.  The notes
    are copied on the GPU from ``sound.notes_gpu`` (zero without sound)."""

    def __init__(self, cp, n: int = 512, seed: int = 1):
        self.cp, self.n = cp, n
        self.music = cp.zeros((2, n, 4), cp.uint8)
        self.fft = cp.zeros((1, n, 4), cp.uint8)
        self.wave = cp.zeros((1, n, 4), cp.uint8)
        self.notes = cp.zeros((1, 128, 4), cp.float32)
        self._notes_live = False
        rng = np.random.default_rng(seed)
        self.noise = cp.asarray(rng.integers(0, 256, (256, 256, 4), dtype=np.uint8))

    def update(self, t: float, energy: float, beat: float, sound: Any = None) -> None:
        fft, wave = _audio_rows(t, energy, beat, self.n)
        fr = getattr(sound, "frame", sound) if sound is not None else None
        spec = getattr(fr, "spectrum", None) if fr is not None and getattr(sound, "present", False) else None
        if spec is not None and len(spec):
            real = spectrum_row(spec, self.n)
            fft = np.clip(real + 0.6 * beat * np.exp(-((np.arange(self.n) / self.n - 0.02) / 0.03) ** 2), 0, 1)
            loud = float(np.mean(real))
            wave = 0.5 + (wave - 0.5) * min(1.0, 0.25 + 1.5 * loud) / max(1e-3, 0.3 + 0.7 * energy + 0.3 * beat)
            wave = np.clip(wave, 0, 1)
        self.music.set(_to_rgba([wave, fft]))     # buffer row 0 = GL top = waveform
        self.fft.set(_to_rgba([fft]))
        self.wave.set(_to_rgba([wave]))
        gpu = getattr(sound, "notes_gpu", None) if sound is not None else None
        notes = None if gpu is not None or fr is None else getattr(fr, "notes", None)
        if gpu is not None:
            self.cp.minimum(gpu, NOTE_AGE_CAP_S, out=self.notes[0])        # on the GPU: ages inf -> a big number
            self._notes_live = True
        elif notes is not None:
            a = np.asarray(notes, np.float32).reshape(128, 4)
            self.notes.set(np.minimum(a, NOTE_AGE_CAP_S)[None, :, :])
            self._notes_live = True
        elif self._notes_live:
            self.notes.fill(0)
            self._notes_live = False
