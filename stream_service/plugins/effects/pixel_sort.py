"""Pixel sort: glitch-art sorting of a luma (or hue / saturation) band along columns or rows into melting,
pouring streaks that grow over frames and surge on the beat.

Source: Original (inspired by https://github.com/GarrettGunnell/AcerolaFX/blob/main/Shaders/AcerolaFX_PixelSort.fx
        and https://godotshaders.com/shader/pseudo-pixel-sorting-v2/ ,
        https://www.alanzucconi.com/2017/12/13/gpu-sorting-1/)
Author: smfx (original; inspired by Garrett Gunnell / Acerola, 'AcerolaFX PixelSort', and Ahopness,
        'Pseudo Pixel Sorting V2')
License: MIT
license_class: permissive

Inspiration only.  AcerolaFX is MIT (https://github.com/GarrettGunnell/AcerolaFX/blob/main/LICENSE.md) and the
godotshaders pseudo-sort is CC0-1.0; no code from either is copied.  The shared idea (mask pixels inside a
threshold band, split the masked runs into spans of bounded length, sort each span by a key) is common knowledge.
Written from scratch here:
  * a half-res persistent buffer, re-seeded every frame as mix(src, last sorted, persistence) with truncating
    integer decay (so it converges exactly back to src and leaves no 8-bit residue);
  * K odd-even transposition passes per frame inside the spans (a pixel moves at most one place per pass, so
    streaks visibly pour and grow over frames), with a hash-gated fractional last pass so the pass count is
    continuous;
  * fully parallel span breaks (a per-line jittered grid plus sparse random breaks; no mask scan), stable under
    sorting because sorting only permutes pixels inside a span;
  * a detail-preserving composite at 4K: full-res src wherever the sorted buffer still equals the downsampled
    src, sorted streaks (tinted through the shared cosine palette, drifting) wherever pixels have moved.
amount widens the threshold band and adds passes; pulse bursts extra passes and widens the band, so streaks surge
on the beat and then relax as the buffer re-blends toward src.  amount = 0 copies src and resets the buffer.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, flag, num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

_SRC = HELPERS + PALETTE_CUDA + r"""
// Sort key in [0, 1]; -1 = never in band (greys have no hue).
__device__ __forceinline__ float sort_key(uchar4 p, int mode) {
    const float r = p.x * (1.f / 255.f), g = p.y * (1.f / 255.f), b = p.z * (1.f / 255.f);
    if (mode == 0) return 0.2126f * r + 0.7152f * g + 0.0722f * b;
    const float mx = fmaxf(r, fmaxf(g, b)), mn = fminf(r, fminf(g, b)), c = mx - mn;
    if (mode == 2) return c / fmaxf(mx, 1e-4f);
    if (c < 0.06f) return -1.f;
    float h;
    if (mx == r)      h = (g - b) / c;
    else if (mx == g) h = 2.f + (b - r) / c;
    else              h = 4.f + (r - g) / c;
    h *= (1.f / 6.f);
    return h - floorf(h);
}

// True when position i of a line starts a new span (no swap across the i-1 | i boundary).
__device__ __forceinline__ bool span_break(int line, int i, int span, float jitter, unsigned int seed) {
    const float hl = hash1((unsigned)line, seed, 11u), hl2 = hash1((unsigned)line, seed, 23u);
    const int L = max(4, (int)(span * (1.f - 0.6f * jitter * hl2)));
    const int off = (int)(hl * jitter * (float)L);
    if ((i + off) % L == 0) return true;
    return hash1((unsigned)line, (unsigned)i, seed ^ 0x5bd1e995u) < jitter * 1.2f / (float)span;
}

// Half-res re-seed: cur = downsampled src; buf = cur + trunc((buf - cur) * persist)  (or = cur when fresh).
extern "C" __global__
void ps_prep(const uchar4* __restrict__ src, uchar4* __restrict__ cur, uchar4* __restrict__ buf,
             const int W, const int H, const int w, const int h, const float persist, const int fresh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const uchar4 c = pack(bilin(src, (x + 0.5f) * W / w, (y + 0.5f) * H / h, W, H));
    const int idx = y * w + x;
    cur[idx] = c;
    if (fresh) { buf[idx] = c; return; }
    const uchar4 b = buf[idx];
    const int dx = (int)((b.x - (int)c.x) * persist), dy = (int)((b.y - (int)c.y) * persist),
              dz = (int)((b.z - (int)c.z) * persist);
    buf[idx] = make_uchar4((unsigned char)(c.x + dx), (unsigned char)(c.y + dy), (unsigned char)(c.z + dz), 255);
}

// One odd-even transposition pass. axis 0: pairs along y (columns); axis 1: pairs along x (rows).
extern "C" __global__
void ps_pass(uchar4* __restrict__ buf, const int w, const int h, const int axis, const int parity,
             const float lo, const float hi, const int keymode, const int desc,
             const int span, const float jitter, const unsigned int seed, const float gate, const unsigned int salt)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    int i0, i1, line, i;
    if (axis == 0) {
        if (gx >= w) return;
        i = 2 * gy + parity;
        if (i + 1 >= h) return;
        line = gx; i0 = i * w + gx; i1 = i0 + w;
    } else {
        if (gy >= h) return;
        i = 2 * gx + parity;
        if (i + 1 >= w) return;
        line = gy; i0 = gy * w + i; i1 = i0 + 1;
    }
    if (span_break(line, i + 1, span, jitter, seed)) return;
    if (gate < 1.f && hash1((unsigned)line, (unsigned)i, salt) >= gate) return;
    const uchar4 a = buf[i0], b = buf[i1];
    const float ka = sort_key(a, keymode), kb = sort_key(b, keymode);
    if (ka < lo || ka > hi || kb < lo || kb > hi) return;
    if (desc ? (ka < kb) : (ka > kb)) { buf[i0] = b; buf[i1] = a; }
}

// 4K composite: src where nothing moved, tinted sorted streaks where it did.
extern "C" __global__
void ps_comp(const uchar4* __restrict__ src, const uchar4* __restrict__ buf, const uchar4* __restrict__ cur,
             uchar4* __restrict__ out, const int W, const int H, const int w, const int h,
             const float gain, const float tint, const float period, const float drift, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int idx = y * W + x;
    const float fx = (x + 0.5f) * w / W, fy = (y + 0.5f) * h / H;
    const float4 B = bilin(buf, fx, fy, w, h), C = bilin(cur, fx, fy, w, h);
    const float d = fmaxf(fabsf(B.x - C.x), fmaxf(fabsf(B.y - C.y), fabsf(B.z - C.z))) * (1.f / 255.f);
    const float wgt = smooth(0.012f, 0.07f, d) * gain;
    if (wgt <= 0.f) { out[idx] = src[idx]; return; }
    const float l = luma(B);
    const float3 pc = pal(l * period + drift, P);
    const float s = 255.f * (0.2f + 0.95f * l);
    const float4 Bt = mix4(B, make_float4(pc.x * s, pc.y * s, pc.z * s, 255.f), tint);
    out[idx] = pack(mix4(ld(src, x, y, W, H), Bt, wgt));
}
"""

_DIRS = ("down", "up", "left", "right")
_KEYS = ("luma", "hue", "saturation")


class PixelSort(Effect):
    name = "pixel_sort"
    label = "Pixel sort"
    description = ("Glitch pixel sorting: a luma/hue band sorts along columns or rows into pouring, melting "
                   "streaks that grow over frames and surge on the beat.")
    stage = "stylise"
    params = {
        **standard_effect_params(),
        "high_threshold": num(0.0, 1.0, 0.88, "High threshold (band top)", tier="regional", basic=True),
        "low_threshold": num(0.0, 1.0, 0.32, "Low threshold (band bottom)", tier="regional"),
        "span_limit": num(16, 256, 150, "Max span length", unit="px@1080", step=1, tier="regional"),
        "span_jitter": num(0.0, 1.0, 0.55, "Span-start randomness", tier="regional"),
        "pour_speed": num(0.0, 1.0, 0.5, "Pour speed (sort passes/frame)", tier="regional"),
        "persistence": num(0.5, 0.99, 0.93, "Streak persistence", tier="regional"),
        "surge_kick": num(0.0, 3.0, 1.2, "Beat surge", step=0.05, tier="regional"),
        "tint_mix": num(0.0, 1.0, 0.35, "Palette tint on streaks", tier="regional"),
        "reverse": flag(False, "Reverse order (bright first)", tier="regional"),
        "direction": enum(_DIRS, "down", "Direction"),
        "sort_key": enum(_KEYS, "luma", "Sort key"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
        **palette_params("acid", 1.0, 0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kprep = mod.get_function("ps_prep")
        self._kpass = mod.get_function("ps_pass")
        self._kcomp = mod.get_function("ps_comp")
        self._w, self._h = internal_size(ctx, 0.5)
        self._cur = cp.zeros((self._h, self._w, 4), dtype=cp.uint8)
        self._buf = cp.zeros((self._h, self._w, 4), dtype=cp.uint8)
        self._P = cp.asarray(np.zeros(12, np.float32))
        self._pal_name = None
        self._fresh = True
        self._parity = 0
        self._frame = 0
        self._drift = 0.0
        self._fps = float(getattr(ctx, "fps", 60) or 60)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H, w, h = self.ctx.width, self.ctx.height, self._w, self._h
        amount = float(p["amount"])
        if amount <= 1e-3:
            cp.copyto(out, src)
            self._fresh = True
            return
        if p["palette"] != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(p["palette"]), np.float32))
            self._pal_name = p["palette"]
        self._drift = (self._drift + p["drift_speed"] * dt) % 1000.0
        self._frame += 1

        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * float(p["surge_kick"])
        eff = min(max(amount * (1.0 + 0.5 * k), 0.0), 1.0)

        # Threshold band: amount widens it from its centre; the beat widens it a little more.
        lo, hi = sorted((float(p["low_threshold"]), float(p["high_threshold"])))
        c, half = 0.5 * (lo + hi), 0.5 * (hi - lo)
        half = half * (0.35 + 0.65 * eff) + 0.08 * min(k, 1.5)
        blo, bhi = max(0.0, c - half), min(1.0, c + half)

        # Re-seed the persistent buffer toward src (frame-rate independent decay).
        persist = 0.0 if self._fresh else float(p["persistence"]) ** max(dt * 60.0, 0.0)
        launch_2d(self._kprep, w, h, (src, self._cur, self._buf, np.int32(W), np.int32(H), np.int32(w),
                                      np.int32(h), np.float32(persist), np.int32(1 if self._fresh else 0)))

        # Passes this frame: continuous (fractional last pass is hash-gated), burst on the beat.
        frames = min(max(dt * self._fps, 1.0 if self._fresh else 0.0), 2.0)
        self._fresh = False
        npass = (2.0 + 14.0 * float(p["pour_speed"])) * (0.3 + 0.7 * eff) * (1.0 + 1.2 * k) * frames
        npass = min(npass, 32.0)
        full = int(math.floor(npass))
        frac = npass - full

        d = p["direction"]
        axis = 0 if d in ("down", "up") else 1
        flip = d in ("up", "left")
        desc = int(bool(p["reverse"]) != flip)
        keymode = _KEYS.index(p["sort_key"]) if p["sort_key"] in _KEYS else 0
        span = int(round(float(p["span_limit"])))
        jitter = float(p["span_jitter"])
        seed = int(p["seed"]) & 0x7FFFFFFF
        gw, gh = (w, (h + 1) // 2) if axis == 0 else ((w + 1) // 2, h)
        for n in range(full + (1 if frac > 1e-3 else 0)):
            gate = 1.0 if n < full else frac
            salt = (self._frame * 7919 + n * 104729 + seed) & 0xFFFFFFFF
            launch_2d(self._kpass, gw, gh, (self._buf, np.int32(w), np.int32(h), np.int32(axis),
                                            np.int32(self._parity), np.float32(blo), np.float32(bhi),
                                            np.int32(keymode), np.int32(desc), np.int32(span), np.float32(jitter),
                                            np.uint32(seed), np.float32(gate), np.uint32(salt)))
            self._parity ^= 1

        gain = min(eff * 2.5, 1.0)
        launch_2d(self._kcomp, W, H, (src, self._buf, self._cur, out, np.int32(W), np.int32(H), np.int32(w),
                                      np.int32(h), np.float32(gain), np.float32(p["tint_mix"]),
                                      np.float32(p["palette_period"]), np.float32(self._drift), self._P))
