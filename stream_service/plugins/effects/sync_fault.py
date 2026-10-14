"""Sync fault: analogue broadcast sync failure (not tape damage; vhs owns that).  A V-hold roll with a black
blanking bar, H-hold shear and tear bands, scanline jitter, interlace combing from the previous field,
horizontal shake and colour drift.  The frame slips, and catches on the downbeat.

Source: https://github.com/keijiro/KinoGlitch/blob/master/Assets/Kino/Glitch/Shader/AnalogGlitch.shader
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Bad%20TV.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Interlace.fs
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Vertical%20Tearing.fs
Author: Keijiro Takahashi (KinoGlitch AnalogGlitch); Felix Turner (BadTVShader via ISF Bad TV); Carter Rosenberg (ISF Interlace); VIDVOX (Vertical Tearing)
License: MIT
license_class: permissive
Ported to CUDA by smfx (ideas/changes: KinoGlitch's thresholded per-line jitter, horizontal shake and
two-tap colour drift; ISF Interlace's alternate lines from the previous frame; the rest is new: an Adler
phase-locked V-hold, a real blanking interval with teletext dashes, H-blanking wrap, tear bands with a
palette-tinted colour-burst loss, and top-of-picture hooking).

How it works.
* V-hold.  The roll phase phi (frames) follows Adler's phase-locking equation, integrated on the host in
  sub-steps: ``dphi/dt = dw - K sin(2 pi phi)``.  ``dw = m * (roll_speed + kick)`` is the frequency mismatch and
  ``K = hold`` the sync pull.  Below lock (dw < K) the picture sits displaced by ``asin(dw / K) / 2 pi`` with the
  blanking bar in view; above it the picture rolls, hesitating near the lock point and racing through the rest.
  The beat adds a momentary mismatch, so the frame slips and then catches at the next lock point.  The signal
  has period 1 + bar (picture + vertical blanking), so a full roll returns exactly to the start: no jumps.
  As amount falls the sync pull rises sharply, so the phase settles to 0 before the copy cut.
* H-hold.  Each row gets a horizontal offset: whole-frame shake (smooth noise of an integrated clock), a slow
  shear (noise along the picture), hooking at the top of the picture, up to three tear bands (fade in, shear
  progressively, tear at their bottom edge, fade out, reseed at zero strength) and KinoGlitch's thresholded
  scanline jitter (reseeded from an integrated 24 Hz counter, never the frame count).  Lines wrap with a black
  horizontal blanking gap, as a real set does.
* Interlace.  Odd lines show the previous frame through the previous frame's geometry, slightly
  mis-registered and dimmer (combing, line twitter).  The previous frame lives in a buffer from setup.
* Colour.  A second tap offset along the line feeds the channels picked by the palette (pal(idx) chroma,
  drifting), the KinoGlitch colour drift generalised; tear bands lose colour burst and go palette-tinted.
Every amplitude is scaled by m = smoothstep(amount), so amount 0 is an exact copy.  Pulse kicks the shear,
jitter, shake and roll-speed amplitudes (rates and gains only).  A row kernel (2 x H threads) plus one 4K pass
with 2-4 bilinear taps, and a 0.3 ms field copy.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NG = 16  # floats per field geometry block

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NG 16
#define HBLANK 0.18f

// Per-row geometry for one field.  G: 0 phi, 1 T (1 + bar), 2 shake px, 3 shear px, 4 shear clock, 5 hook px,
// 6 tear px, 7 tear counter, 8 jitter px, 9 jitter threshold, 10 jitter seed, 11 colour drift px,
// 12 colour drift phase, 13 line height px, 14 seed, 15 unused.
// rows[(f * H + y) * 8 + ...] = xoff, ysrc, vblank, band, barpos, cdrift, 0, 0
extern "C" __global__
void sync_rows(const float* __restrict__ Gall, float* __restrict__ rows, const int W, const int H)
{
    const int y = blockIdx.x * blockDim.x + threadIdx.x;
    const int f = blockIdx.y;
    if (y >= H) return;
    const float* G = Gall + f * NG;
    const float phi = G[0], T = G[1];
    const unsigned int seed = (unsigned int)G[14];
    const float v = (y + 0.5f) / H;
    // vertical: the signal is picture (u in [0,1)) + blanking (u in [1,T)), rolled by phi * T
    float u = v - phi * T;
    u -= floorf(u / T) * T;
    float vb = 0.f, barpos = 0.f;
    const float bh = T - 1.f;
    if (bh > 1e-5f) {
        if (u < 1.f) vb = clamp01(0.5f - fminf(u, 1.f - u) * H);
        else { vb = clamp01(0.5f + fminf(u - 1.f, T - u) * H); barpos = (u - 1.f) / bh; }
        vb *= smooth(0.f, 3.f, bh * H);
    }
    const float up = fminf(u, 1.f);
    const float ysrc = fminf(fmaxf(up * H, 0.5f), H - 0.5f);

    // horizontal
    float xoff = G[2];
    xoff += G[3] * (2.f * vnoise(up * 2.5f, G[4], seed + 3u) - 1.f
                    + 0.35f * (2.f * vnoise(up * 11.f, G[4] * 1.7f, seed + 5u) - 1.f));
    if (u < 1.f) xoff += G[5] * __expf(-u * 16.f);                  // hooking at the top of the picture
    float band = 0.f, jboost = 0.f;
    #pragma unroll
    for (int i = 0; i < 3; ++i) {
        const float cnt = G[7] + i * (1.f / 3.f);
        const float cyc = floorf(cnt), fr = cnt - cyc;
        const unsigned int ci = (unsigned int)(int)cyc;
        const float s0 = __sinf(3.14159265f * fr);
        const float env = s0 * s0;
        const float h = 0.035f + 0.13f * hash1(ci, 11u + i, seed);
        const float dir = hash1(ci, 23u + i, seed) < 0.5f ? -1.f : 1.f;
        const float y0 = 0.08f + 0.84f * hash1(ci, 37u + i, seed) + dir * 0.12f * (fr - 0.5f);
        const float amp = (0.35f + 0.65f * hash1(ci, 51u + i, seed)) * (hash1(ci, 67u + i, seed) < 0.5f ? -1.f : 1.f);
        const float s = (v - (y0 - 0.5f * h)) / h;
        if (s >= 0.f && s < 1.f) {
            xoff += G[6] * amp * env * s * s;
            band = fmaxf(band, env * smooth(0.f, 0.25f, s));
            jboost = fmaxf(jboost, env);
        }
    }
    // KinoGlitch scanline jitter: thresholded per line, reseeded from an integrated counter
    const int lh = max((int)G[13], 1);
    const unsigned int L = (unsigned int)(y / lh);
    float j = 2.f * hash1(L, (unsigned int)G[10], seed + 101u) - 1.f;
    const float th = fmaxf(G[9] - 0.35f * jboost * clamp01(3.f * (1.f - G[9])), 0.f);
    j = (fabsf(j) >= th && G[8] > 0.f) ? j : 0.f;
    xoff += j * G[8] * (1.f + 1.5f * jboost);
    // colour drift along the line, undulating down the picture
    const float cd = G[11] * __sinf(6.2831853f * (up * 1.7f + G[12]));

    float* r = rows + ((size_t)f * H + y) * 8;
    r[0] = xoff; r[1] = ysrc; r[2] = vb; r[3] = band; r[4] = barpos; r[5] = cd; r[6] = 0.f; r[7] = 0.f;
}

__device__ float4 fetch_line(const uchar4* __restrict__ img, const float* __restrict__ r, const int x, const int y,
                             const int W, const int H, const float* __restrict__ P, const float drift,
                             const float period, const float tint, const unsigned int ttx, const int lh)
{
    const float period_x = W * (1.f + HBLANK);
    float xs = x + 0.5f - r[0];
    xs -= floorf(xs / period_x) * period_x;                         // [0, period_x): picture then h-blank
    const float cover = clamp01(fminf(xs, (float)W - xs) + 0.5f);
    const float xc = fminf(fmaxf(xs, 0.5f), W - 0.5f);
    float4 c = bilin(img, xc, r[1], W, H);
    // colour drift: a second tap feeds the channels the palette picks
    if (fabsf(r[5]) > 0.01f) {
        const float4 c2 = bilin(img, fminf(fmaxf(xc + r[5], 0.5f), W - 0.5f), r[1], W, H);
        const float3 pc = pal(drift + 0.3f * period * (r[1] / H), P);
        float ex = pc.x, ey = pc.y, ez = pc.z;
        const float mu = (ex + ey + ez) * (1.f / 3.f);
        ex -= mu; ey -= mu; ez -= mu;
        const float mx = fmaxf(fabsf(ex), fmaxf(fabsf(ey), fabsf(ez)));
        const float k = smooth(0.03f, 0.2f, mx);                    // greyscale palette -> classic green drift
        const float inv = k / fmaxf(mx, 1e-4f);
        const float wr = clamp01(ex * inv), wg = clamp01(ey * inv + (1.f - k)), wb = clamp01(ez * inv);
        c.x += (c2.x - c.x) * wr; c.y += (c2.y - c.y) * wg; c.z += (c2.z - c.z) * wb;
    }
    // tear bands: colour burst lost, the band goes palette-tinted
    const float bt = r[3] * tint;
    if (bt > 1e-3f) {
        const float3 pc = pal(drift + 0.5f + period * (y / (float)H), P);
        const float t = luma(c);
        const float k = 0.25f + 1.1f * t;
        c = mix4(c, make_float4(255.f * fminf(pc.x * k, 1.f), 255.f * fminf(pc.y * k, 1.f),
                                255.f * fminf(pc.z * k, 1.f), 255.f), 0.6f * bt);
    }
    c.x *= cover; c.y *= cover; c.z *= cover;
    // vertical blanking bar: black, with teletext dashes on a few lines
    if (r[2] > 0.f) {
        float lev = 0.f;
        const float b = r[4];
        if (b > 0.55f && b < 0.85f) {
            const float q = (b - 0.55f) * (4.f / 0.3f);
            const int dl = (int)q;
            if (q - dl < 0.55f) {
                const float dw = fmaxf(W / 190.f, 2.f);
                const unsigned int cx = (unsigned int)(int)floorf((x + 0.5f - 0.25f * r[0]) / dw);
                lev = hash1(cx, (unsigned int)dl + 7u, ttx) > 0.5f ? 150.f : 0.f;
            }
        }
        c = mix4(c, make_float4(lev, lev, lev, 255.f), r[2]);
    }
    return c;
}

extern "C" __global__
void sync_fault(const uchar4* __restrict__ src, const uchar4* __restrict__ prev, uchar4* __restrict__ out,
                const float* __restrict__ rows, const int W, const int H, const float il, const float* __restrict__ P,
                const float drift, const float period, const float tint, const unsigned int ttx,
                const unsigned int ttxp, const int lh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float4 c = fetch_line(src, rows + (size_t)y * 8, x, y, W, H, P, drift, period, tint, ttx, lh);
    if (il > 1e-4f && ((y / lh) & 1)) {
        const float4 cp = fetch_line(prev, rows + ((size_t)H + y) * 8, x, y, W, H, P, drift, period, tint, ttxp, lh);
        c = mix4(c, cp, il);
        const float g = 1.f - 0.18f * il;
        c.x *= g; c.y *= g; c.z *= g;
    }
    out[y * W + x] = pack(c);
}
"""


def _smoothstep(x: float) -> float:
    t = min(max(x, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _snoise(t: float, seed: int) -> float:
    """Smooth 1-D value noise in [-1, 1] of an integrated clock (host side)."""
    i = math.floor(t)
    f = t - i
    f = f * f * (3.0 - 2.0 * f)

    def h(n: int) -> float:
        x = (n * 374761393 + seed * 668265263) & 0xFFFFFFFF
        x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((x ^ (x >> 16)) & 0xFFFFFF) / float(0xFFFFFF) * 2.0 - 1.0

    return h(i) * (1.0 - f) + h(i + 1) * f


class SyncFault(Effect):
    name = "sync_fault"
    label = "Sync fault"
    stage = "glitch"
    description = ("Analogue broadcast sync faults: V-hold roll with a black blanking bar, H-hold shear and tear "
                   "bands, scanline jitter, interlace combing, shake and colour drift. Slips, then catches on the beat.")
    tags = {"family": ["glitch"], "dims": ["2d"], "technique": ["glitch", "retro"]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives our own fast-attack envelope: keep it raw
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "roll_speed": num(0.0, 4.0, 1.0, "V-hold roll (sync mismatch)", step=0.01, unit="Hz", tier="realtime",
                          bind="rate", basic=True),
        "hold": num(0.05, 3.0, 0.8, "Sync pull (catch strength)", step=0.01, tier="regional"),
        "blanking_bar": num(0.0, 1.0, 0.5, "Blanking bar height", tier="regional"),
        "tear_amount": num(0.0, 1.0, 0.5, "H-hold tear and shear", tier="regional", basic=True),
        "scanline_jitter": num(0.0, 1.0, 0.35, "Scanline jitter", tier="regional"),
        "interlace": num(0.0, 1.0, 0.5, "Interlace combing", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.4, "Colour drift", tier="regional"),
        "kick": num(0.0, 3.0, 1.0, "Beat kick (slip and shear)", step=0.05, tier="regional"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
        **palette_params("neon", 1.0, 0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        W, H = ctx.width, ctx.height
        mod = raw_module(_SRC)
        self._k_rows = mod.get_function("sync_rows")
        self._k = mod.get_function("sync_fault")
        self._prev = cp.zeros((H, W, 4), dtype=cp.uint8)
        self._rows = cp.zeros(2 * H * 8, dtype=cp.float32)
        self._G_h = np.zeros(2 * NG, dtype=np.float32)
        self._G_d = cp.zeros(2 * NG, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        self._seed = int(p0.get("seed", 1) or 1) & 0xFFFF
        self._phi = 0.0         # roll phase, frames in [0, 1)
        self._env = 0.0         # beat envelope
        self._shake_t = 0.0     # integrated clocks
        self._shear_t = 0.0
        self._tear_c = 0.0
        self._jit_c = 0.0
        self._cd_ph = 0.0
        self._ttx_c = 0.0
        self._field_t = 0.0
        self._drift = 0.0
        self._fresh = True
        self._G_prev = None
        self._ttx_prev = 0

    def _geometry(self, p: dict, m: float, W: int, H: int) -> np.ndarray:
        env, k = self._env, float(p["kick"])
        tear = float(p["tear_amount"]) * m
        bh = 0.16 * float(p["blanking_bar"])
        jl = min(float(p["scanline_jitter"]) * m * (1.0 + 1.5 * k * env), 1.0)
        G = np.zeros(NG, dtype=np.float32)
        G[0] = self._phi
        G[1] = 1.0 + bh
        G[2] = tear * (1.0 + 2.5 * k * env) * 0.006 * W * _snoise(self._shake_t, self._seed + 1)
        G[3] = tear * (1.0 + 1.5 * k * env) * 0.02 * W
        G[4] = self._shear_t
        G[5] = tear * 0.035 * W * (0.6 + 0.4 * _snoise(self._shear_t * 2.0, self._seed + 2))
        G[6] = tear * (1.0 + 2.0 * k * env) * 0.16 * W
        G[7] = self._tear_c
        G[8] = (0.002 * min(jl / 0.2, 1.0) + jl ** 3 * 0.05) * W if jl > 1e-4 else 0.0
        G[9] = min(max(1.0 - jl * 1.2, 0.0), 1.0)
        G[10] = float(math.floor(self._jit_c) % 65536)
        G[11] = float(p["colour_drift"]) * m * (1.0 + 1.5 * k * env) * 0.018 * W
        G[12] = self._cd_ph
        G[13] = float(max(1, H // 540))
        G[14] = float(self._seed)
        return G

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        dt = max(float(dt), 0.0)
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        m = _smoothstep(amount)
        reset = dt > 0.5
        dt = min(dt, 0.1)

        # beat envelope: fast attack, ~180 ms decay; dt = 0 means no step
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        if dt > 0.0:
            if pulse > self._env:
                self._env += (pulse - self._env) * (1.0 - math.exp(-dt / 0.015))
            else:
                self._env *= math.exp(-dt / 0.18)
            env, k = self._env, float(p["kick"])
            # V-hold: Adler phase locking, sub-stepped; sync pull rises as amount falls (settle before the cut)
            dw = m * (float(p["roll_speed"]) + 6.0 * k * env)
            K = float(p["hold"]) + 4.0 * (1.0 - m) ** 4
            n = max(1, min(24, int(math.ceil(dt * 240.0))))
            h = dt / n
            phi = self._phi
            for _ in range(n):
                phi += h * (dw - K * math.sin(2.0 * math.pi * phi))
            self._phi = phi - math.floor(phi)
            # integrated clocks (rates, never positions)
            self._shake_t = (self._shake_t + dt * (14.0 + 10.0 * k * env)) % 4096.0
            self._shear_t = (self._shear_t + dt * (0.35 + 1.2 * k * env)) % 4096.0
            self._tear_c = (self._tear_c + dt * (0.3 + 0.4 * m + 0.8 * k * env)) % 4096.0
            self._jit_c = (self._jit_c + dt * (24.0 + 12.0 * k * env)) % 65536.0
            self._cd_ph = (self._cd_ph + dt * 0.45) % 1.0
            self._ttx_c = (self._ttx_c + dt * 9.0) % 65536.0
            self._field_t = (self._field_t + dt * 3.0) % 4096.0
            self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0
            tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
            if not np.array_equal(tgt, self._pal):
                self._pal += (tgt - self._pal) * (1.0 - math.exp(-dt / 1.0))
                if np.max(np.abs(tgt - self._pal)) < 1e-4:
                    self._pal = tgt
                self._P_d.set(self._pal.astype(np.float32))

        if amount <= 1e-4:
            cp.copyto(out, src)
            self._fresh = True
            return
        if self._fresh or reset:
            cp.copyto(self._prev, src)

        G = self._geometry(p, m, W, H)
        if self._fresh or reset or self._G_prev is None:
            self._G_prev = G.copy()
            self._ttx_prev = int(self._ttx_c)
        Gp = self._G_prev.copy()
        il = float(p["interlace"]) * m
        Gp[2] += il * 0.008 * W * (0.6 + 0.4 * _snoise(self._field_t, self._seed + 3)) * (1.0 + float(p["kick"]) * self._env)
        self._G_h[:NG] = G
        self._G_h[NG:] = Gp
        self._G_d.set(self._G_h)
        lh = max(1, H // 540)
        self._k_rows(((H + 127) // 128, 2, 1), (128, 1, 1), (self._G_d, self._rows, np.int32(W), np.int32(H)))
        ttx = int(self._ttx_c) & 0xFFFFFFFF
        tint = min(1.0, 2.0 * float(p["tear_amount"]) * m)
        launch_2d(self._k, W, H, (src, self._prev, out, self._rows, np.int32(W), np.int32(H), np.float32(il),
                                  self._P_d, np.float32(self._drift), np.float32(p["palette_period"]),
                                  np.float32(tint), np.uint32(ttx), np.uint32(self._ttx_prev), np.int32(lh)))
        if dt > 0.0 or self._fresh:
            cp.copyto(self._prev, src)
            self._G_prev = G
            self._ttx_prev = ttx
        self._fresh = False

    def close(self) -> None:
        self._prev = None
        self._rows = None
        self._G_d = None
        self._P_d = None
