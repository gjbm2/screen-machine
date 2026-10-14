"""Rain ripples: the picture becomes a puddle the music rains into.

Source: https://github.com/evanw/webgl-water
        http://madebyevan.com/webgl-water/
Author: Evan Wallace (WebGL Water wave simulation); Hugo Elias two-buffer method (idea)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: Wallace's one-texture height+velocity update (velocity += (neighbour
average - height) * k; velocity *= damping; height += velocity) and his raised-cosine drop are kept; the wave
constant k is driven by a CFL-clamped wave_speed knob, damping is expressed as a ring lifetime in seconds and a slow
height bleed removes the DC that drops leave behind; the sim runs at a fixed 240 Hz on a 540-row grid (960x540 at
16:9) whatever the output size, so its look does not depend on resolution; raindrops come from a seeded host table
at an energy-scaled rate with random sign; the beat drop is a velocity impulse (it blooms over ~80 ms instead of
popping); the water is not drawn at all: the input frame is refracted by the soft-limited height gradient, crests
focus light (laplacian shading) and the slopes facing a fixed light pick up palette-tinted sheen and white-hot
specular glints, all screen-blended so bright stills do not clip).

amount 0 is an exact copy (the sim keeps running underneath, so fading in shows rings already in flight).
amount scales refraction, shading and glints through smoothstep(amount).  energy drives drop rate (e^1.3), drop
strength, drop size, refraction depth and glint gain.  pulse is the beat: the host detects a rising edge
(pulse - prev > 0.15, 120 ms refractory) and injects one large drop; pulse also lifts glint and refraction gains.
dt = 0 skips the sim; long frames take up to 8 substeps.  There is no image history, so content cuts are safe.
Cost at 4K: ~4 tiny sim steps at 960x540 plus one full-res pass with one bilinear tap (~1 ms on a P4000).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

SIM_ROWS = 540        # sim grid height (cells); width follows the output aspect
SIM_HZ = 240.0        # fixed sim rate: 4 substeps per 60 fps frame
MAX_STEPS = 8         # per frame (long frames)
MAXD = 32             # drops per frame (incl. the beat drop)
DF = 8                # floats per drop: x, y, r, dh, dv, unused...
TAB = 4096            # host table of hashed raindrops
WARM_S = 1.5          # sim time pre-run in setup so the effect reads at once

_SRC = HELPERS + PALETTE_CUDA + r"""
#define DF 8
extern "C" __global__
void rr_drops(float2* __restrict__ s, const int SW, const int SH, const float* __restrict__ D, const int n)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= SW || y >= SH) return;
    const float px = x + 0.5f, py = y + 0.5f;
    float dh = 0.f, dv = 0.f;
    for (int i = 0; i < n; ++i) {
        const float* d = D + i * DF;
        const float ex = px - d[0], ey = py - d[1], r = d[2];
        if (fabsf(ex) >= r || fabsf(ey) >= r) continue;
        const float q = sqrtf(ex * ex + ey * ey) / r;
        if (q >= 1.f) continue;
        const float b = 0.5f + 0.5f * __cosf(3.14159265f * q);     // Wallace's raised-cosine drop
        dh += d[3] * b; dv += d[4] * b;
    }
    if (dh != 0.f || dv != 0.f) {
        float2 c = s[y * SW + x];
        c.x += dh; c.y += dv;
        s[y * SW + x] = c;
    }
}

// One wave-equation step (Wallace): v += (avg4 - h) * k; v *= damp; h += v.  Clamped edges = reflecting walls.
extern "C" __global__
void rr_step(const float2* __restrict__ a, float2* __restrict__ b, const int SW, const int SH,
             const float k, const float damp, const float hdecay)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= SW || y >= SH) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, SW - 1), ym = max(y - 1, 0), yp = min(y + 1, SH - 1);
    const float2 c = a[y * SW + x];
    const float avg = 0.25f * (a[y * SW + xm].x + a[y * SW + xp].x + a[ym * SW + x].x + a[yp * SW + x].x);
    float v = (c.y + (avg - c.x) * k) * damp;
    float h = (c.x + v) * hdecay;
    if (!isfinite(h) || !isfinite(v)) { h = 0.f; v = 0.f; }
    b[y * SW + x] = make_float2(h, v);
}

// Gradient (heights are in cell units, so slopes are dimensionless), laplacian and height.
extern "C" __global__
void rr_grad(const float2* __restrict__ a, float4* __restrict__ g, const int SW, const int SH)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= SW || y >= SH) return;
    const int xm = max(x - 1, 0), xp = min(x + 1, SW - 1), ym = max(y - 1, 0), yp = min(y + 1, SH - 1);
    const float h = a[y * SW + x].x;
    const float l = a[y * SW + xm].x, r = a[y * SW + xp].x, u = a[ym * SW + x].x, d = a[yp * SW + x].x;
    g[y * SW + x] = make_float4(0.5f * (r - l), 0.5f * (d - u), l + r + u + d - 4.f * h, h);
}

__device__ __forceinline__ float4 rr_ldg(const float4* __restrict__ g, int x, int y, int SW, int SH) {
    x = min(max(x, 0), SW - 1); y = min(max(y, 0), SH - 1);
    return g[y * SW + x];
}

extern "C" __global__
void rr_render(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
               const float4* __restrict__ G, const int SW, const int SH, const float* __restrict__ P,
               const float refr_px, const float slope_gain, const float shade_k, const float sheen_k,
               const float spec_k, const float drift, const float period)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    // bilinear sample of the sim field
    const float fx = px * ((float)SW / (float)W) - 0.5f, fy = py * ((float)SH / (float)H) - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const float4 a = rr_ldg(G, x0, y0, SW, SH), b = rr_ldg(G, x0 + 1, y0, SW, SH);
    const float4 c = rr_ldg(G, x0, y0 + 1, SW, SH), d = rr_ldg(G, x0 + 1, y0 + 1, SW, SH);
    const float u = 1.f - tx, v = 1.f - ty;
    const float gx0 = v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x);
    const float gy0 = v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y);
    const float lap = v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z);
    const float hh = v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w);
    // soft-limited slope (fresh drops are steep; keep the lens bounded)
    float gx = gx0 * slope_gain, gy = gy0 * slope_gain;
    const float f = rsqrtf(1.f + 0.25f * (gx * gx + gy * gy));
    gx *= f; gy *= f;
    float4 o = bilin(src, px - gx * refr_px, py - gy * refr_px, W, H);
    o.w = 255.f;
    // crests focus light, troughs spread it
    const float sh = fminf(fmaxf(-lap * slope_gain * 0.8f, -0.5f), 0.5f) * shade_k;
    const float k = 1.f + sh;
    o.x *= k; o.y *= k; o.z *= k;
    // surface normal and a fixed light from the upper left
    const float nx0 = -0.4f * gx, ny0 = -0.4f * gy;
    const float inv = rsqrtf(1.f + nx0 * nx0 + ny0 * ny0);
    const float nx = nx0 * inv, ny = ny0 * inv, nz = inv;
    const float Lx = -0.4537f, Ly = -0.6049f, Lz = 0.6544f;                 // normalize(-0.45, -0.6, 0.65)
    const float Hx = -0.2496f, Hy = -0.3328f, Hz = 0.9094f;                 // normalize(L + view)
    const float dif = clamp01((nx * Lx + ny * Ly + nz * Lz - Lz) * 2.5f);  // 0 on flat water
    const float sp = __powf(fmaxf(nx * Hx + ny * Hy + nz * Hz, 0.f), 70.f);
    const float3 col = pal(drift + period * (0.35f * px / W + 0.2f * py / H + 0.05f * hh), P);
    const float sa = sheen_k * dif, ga = spec_k * sp;
    const float tr = clamp01(sa * col.x + ga * (0.45f + 0.55f * col.x));
    const float tg = clamp01(sa * col.y + ga * (0.45f + 0.55f * col.y));
    const float tb = clamp01(sa * col.z + ga * (0.45f + 0.55f * col.z));
    o.x = fminf(o.x, 255.f); o.y = fminf(o.y, 255.f); o.z = fminf(o.z, 255.f);
    o.x += (255.f - o.x) * tr; o.y += (255.f - o.y) * tg; o.z += (255.f - o.z) * tb;    // screen blend
    out[y * W + x] = pack(o);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class RainRipples(Effect):
    name = "rain_ripples"
    stage = "warp"
    label = "Rain ripples"
    description = "The picture becomes a puddle the music rains into: interfering rings, a big drop on each beat."
    params = {
        **standard_effect_params(),
        # the beat envelope is edge-detected on the host: keep it raw (easing would flatten the peaks)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "energy": num(0.0, 1.0, 0.5, "Energy", tier="realtime", bind="level"),
        "drop_rate": num(0.0, 40.0, 10.0, "Drop rate", unit="/s", step=0.5, tier="regional", basic=True),
        "drop_size": num(0.0, 1.0, 0.35, "Drop size", tier="regional"),
        "damping": num(0.0, 1.0, 0.4, "Damping (long ↔ short-lived rings)", tier="regional"),
        "wave_speed": num(0.3, 1.0, 0.75, "Wave speed", tier="regional"),
        "refraction": num(0.0, 0.03, 0.012, "Refraction", unit="× height", step=0.001, tier="regional"),
        "glint": num(0.0, 1.0, 0.5, "Glints and sheen", tier="regional"),
        "beat_drop": num(0.0, 2.0, 1.0, "Beat drop", step=0.05, tier="regional"),
        **palette_params("ice", 1.0, 0.04),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_drops = mod.get_function("rr_drops")
        self._k_step = mod.get_function("rr_step")
        self._k_grad = mod.get_function("rr_grad")
        self._k_render = mod.get_function("rr_render")
        W, H = ctx.width, ctx.height
        self._SH = int(SIM_ROWS)
        self._SW = max(2, int(round(SIM_ROWS * W / max(H, 1))))
        self._A = cp.zeros((self._SH, self._SW, 2), dtype=cp.float32)
        self._B = cp.zeros_like(self._A)
        self._G = cp.zeros((self._SH, self._SW, 4), dtype=cp.float32)
        self._D_h = np.zeros(MAXD * DF, dtype=np.float32)
        self._D_d = cp.zeros(MAXD * DF, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "ice")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        rng = np.random.default_rng(int(p0.get("seed", 1) or 1))
        self._tab = rng.random((TAB, 4))
        self._ti = 0
        self._acc_drop = float(rng.random())
        self._acc_step = 0.0
        self._t = 0.0
        self._prev_pulse = 0.0
        self._last_beat = -10.0
        self._drift = float(rng.random())
        # pre-run the puddle so rings are already in flight when the effect is faded in
        q = {**p0, "pulse": 0.0}
        for _ in range(int(WARM_S * 60)):
            self._advance(q, 1.0 / 60.0, beats=False)
        self._prev_pulse = 0.0
        self._dirty = True

    def _next(self) -> np.ndarray:
        u = self._tab[self._ti]
        self._ti = (self._ti + 1) % TAB
        return u

    def _advance(self, p: dict, dt: float, beats: bool = True) -> bool:
        if dt <= 0.0:
            return False
        dt = min(dt, 0.25)
        self._t += dt
        SW, SH = self._SW, self._SH
        e = min(max(float(p["energy"]), 0.0), 1.0)
        strength = 0.7 + 0.6 * e
        r0 = (2.5 + 13.5 * float(p["drop_size"])) * (0.85 + 0.3 * e)
        rate = float(p["drop_rate"]) * (0.3 + 1.7 * e ** 1.3)
        self._acc_drop += rate * dt
        n = int(self._acc_drop)
        self._acc_drop -= n
        n = min(n, MAXD - 1)
        h = self._D_h
        for i in range(n):
            u = self._next()
            r = r0 * (0.6 + 0.8 * u[2])
            sign = 1.0 if u[3] < 0.5 else -1.0
            o = i * DF
            h[o:o + 5] = (u[0] * SW, u[1] * SH, r, 0.5 * r * strength * sign * (0.75 + 0.5 * ((u[3] * 7.31) % 1.0)),
                          0.0)
        # beat: one large drop on the rising edge, injected as a velocity impulse so it blooms rather than pops
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        bd = float(p["beat_drop"])
        if (beats and bd > 1e-3 and pulse - self._prev_pulse > 0.15 and self._t - self._last_beat >= 0.12):
            u = self._next()
            R = min(r0 * (2.0 + 1.5 * bd), 0.12 * SH)
            o = n * DF
            h[o:o + 5] = ((0.2 + 0.6 * u[0]) * SW, (0.2 + 0.6 * u[1]) * SH, R, 0.0,
                          0.035 * R * bd * strength * (0.5 + 0.5 * pulse))
            n += 1
            self._last_beat = self._t
        self._prev_pulse = pulse
        if n > 0:
            self._D_d.set(h)
            launch_2d(self._k_drops, SW, SH, (self._A, np.int32(SW), np.int32(SH), self._D_d, np.int32(n)))
        # wave steps at a fixed rate (CFL: k <= 2)
        ws = min(max(float(p["wave_speed"]), 0.3), 1.0)
        k = 1.95 * ws * ws
        life = 8.0 * 0.1 ** min(max(float(p["damping"]), 0.0), 1.0)
        damp = math.exp(-2.0 / (life * SIM_HZ))
        hdec = math.exp(-1.0 / (15.0 * SIM_HZ))
        self._acc_step += dt * SIM_HZ
        ns = int(self._acc_step + 1e-6)
        self._acc_step -= ns
        if ns > MAX_STEPS:
            ns, self._acc_step = MAX_STEPS, 0.0
        args_k = (np.int32(SW), np.int32(SH), np.float32(k), np.float32(damp), np.float32(hdec))
        for _ in range(ns):
            launch_2d(self._k_step, SW, SH, (self._A, self._B) + args_k)
            self._A, self._B = self._B, self._A
        self._dirty = True
        return True

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        dt = max(float(dt), 0.0)
        self._advance(p, dt)
        self._drift += float(p["drift_speed"]) * dt
        self._drift -= math.floor(self._drift)
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            a = 1.0 - math.exp(-dt / 1.0)
            self._pal += (tgt - self._pal) * a
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        if self._dirty:
            launch_2d(self._k_grad, self._SW, self._SH, (self._A, self._G, np.int32(self._SW), np.int32(self._SH)))
            self._dirty = False
        m = _smooth(0.0, 1.0, amount)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        refr_px = float(p["refraction"]) * H * m * (0.8 + 0.4 * e) * (1.0 + 0.2 * pulse)
        g = float(p["glint"]) * m * (0.6 + 0.8 * e) * (1.0 + 0.6 * pulse)
        launch_2d(self._k_render, W, H,
                  (src, out, np.int32(W), np.int32(H), self._G, np.int32(self._SW), np.int32(self._SH), self._P_d,
                   np.float32(refr_px), np.float32(6.0), np.float32(0.55 * g), np.float32(0.35 * g),
                   np.float32(1.1 * g), np.float32(self._drift), np.float32(p["palette_period"])))

    def close(self) -> None:
        self._A = self._B = self._G = None
        self._D_d = None
        self._P_d = None
