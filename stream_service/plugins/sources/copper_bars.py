"""Copper & Kefrens bars: Amiga raster bars, chrome-shaded, sweeping on summed-sine paths with depth ordering.

Source: Original (inspired by https://en.wikipedia.org/wiki/Raster_bar and https://en.wikipedia.org/wiki/Demo_effect)
Author: smfx (original; from public demo-effect descriptions only)
License: MIT
license_class: permissive

Written from scratch from the public descriptions of the effects (no code was read or copied).

Model (screen units = half the frame height; s = across the bars, r = along them, rotated by ``angle``):
  * bar i has phase theta_i(r) = phi - i * spacing + kk * r.  Its centre is c_i = A * S(theta_i) with
    S = sin(th) + c2 sin(2 th + p2) + c3 sin(3 th + p3) (wave_complexity fades the 2nd and 3rd sines in), and its
    depth is z_i = cos(theta_i): the stack is a ring of bars on a rotating drum, front bars occlude back ones,
    back bars are thinner and dimmer.
  * copper (angle 0): kk = 0, straight horizontal bars sweeping up and down.  The angle rotates them through
    diagonal to vertical; towards vertical a twist kk > 0 fades in, so each bar's position varies per scanline
    (the Kefrens stamp, analytic: centre is a sine of the row) and the ring of bars becomes a twisting ribbon wall
    whose wave travels down the screen as phi advances.
  * per pixel the kernel loops over the (<= 64, incl. mirror twins) bars held in shared memory, keeping the two
    front-most covering bars (by z) with analytic AA coverage (slope-corrected for twisted bars), then composites
    back -> front over a dark palette-tinted background plus a soft halo.  Per-bar trig is hoisted to the host;
    the r-dependent twist uses angle addition, so the inner loop is ~20 FMAs per bar.  No state.
  * shading: chrome (sky/ground environment reflection with a horizon line and a specular glint) -> classic
    copper (stepped dark-bright-dark raster ramp with a white-hot core) -> flat slabs.
  * mirror_symmetry fades in a twin of every bar reflected across the centre line.

Macros: energy -> bars fade in (bar_count * (0.4 + 0.6 e^1.2)), sine amplitude (0.6 + 0.4 e), phase speed
(0.25 + 0.75 e), gloss (0.45 + 0.55 e), halo glow and Kefrens twist frequency.  pulse -> a thickness and brightness
kick, delayed per bar (looked up in a short time-stamped history) so it ripples down the stack; amplitude only,
never position.  speed scales every phase; palette by bar index (x period) + integrated drift; intensity = gain.
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

MAXN = 32                 # bars
MAXB = 2 * MAXN           # bars + mirror twins
BS = 16                   # floats per bar record

# P layout: 0..11 palette | 12 period | 13 drift | 14 cos a | 15 sin a | 17 c2 | 19 c3 | 21 kk (twist rad/unit)
#  22 shading | 23 gloss | 25 bg level | 26 intensity | 27 px (pixel in screen units) | 28 perspective
#  29 aspect | 30 glow | 31 halo reach (x hw)
# bar record: 0 sin th | 1 cos th | 2 sin(2th+p2) | 3 cos(2th+p2) | 4 sin(3th+p3) | 5 cos(3th+p3)
#  6 signed amplitude (twin < 0) | 7 half-thickness | 8 gain | 9 alpha | 10..12 colour A | 13..15 colour B
_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAXB %(maxb)d
#define BS %(bs)d

__device__ __forceinline__ float3 shade_bar(const float* __restrict__ Bb, float d, float z,
                                            const float* __restrict__ P) {
    d = fminf(fmaxf(d, -1.f), 1.f);
    const float cyl = sqrtf(fmaxf(1.f - d * d, 0.f));
    const float sh = P[22], gloss = P[23];
    const float3 A = make_float3(Bb[10], Bb[11], Bb[12]);
    const float3 G = make_float3(Bb[13], Bb[14], Bb[15]);
    const float w0 = 1.f - smooth(0.f, 0.5f, sh), w2 = smooth(0.5f, 1.f, sh), w1 = 1.f - w0 - w2;
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (w0 > 0.f) {                                   // chrome: sky above the horizon, ground below, glint
        const float hz = 0.12f;
        const float f = clamp01((hz - d) / (1.f + hz)), g = clamp01((d - hz) / (1.f - hz));
        const float t = smooth(hz - 0.035f, hz + 0.035f, d);
        const float sk = 0.5f + 0.5f * f, wh = 0.5f * f * f;
        const float3 sky = make_float3(A.x * sk + wh, A.y * sk + wh, A.z * sk + wh);
        const float gg = 0.1f + 0.8f * g * g;
        const float3 gr = make_float3(G.x * gg + 0.12f * g * A.x, G.y * gg + 0.12f * g * A.y, G.z * gg + 0.12f * g * A.z);
        const float edge = 0.3f + 0.7f * sqrtf(cyl);
        const float sp = gloss * (1.1f * __expf(-((d + 0.45f) * (d + 0.45f)) * 70.f)
                                  + 0.35f * __expf(-((d - 0.62f) * (d - 0.62f)) * 120.f));
        c.x += w0 * ((sky.x + (gr.x - sky.x) * t) * edge + sp);
        c.y += w0 * ((sky.y + (gr.y - sky.y) * t) * edge + sp);
        c.z += w0 * ((sky.z + (gr.z - sky.z) * t) * edge + sp);
    }
    if (w1 > 0.f) {                                   // copper: stepped raster ramp, white-hot core
        const float q = powf(cyl, 0.85f) * 6.f;
        const float fq = floorf(q);
        const float qq = (fq + smooth(0.42f, 0.58f, q - fq)) * (1.f / 6.f);
        const float lv = 0.12f + 0.98f * qq, wh = (0.35f + 0.45f * gloss) * smooth(0.66f, 1.f, qq);
        c.x += w1 * (A.x * lv + wh);
        c.y += w1 * (A.y * lv + wh);
        c.z += w1 * (A.z * lv + wh);
    }
    if (w2 > 0.f) {                                   // flat slab with a lit top edge and a shadowed bottom edge
        const float lit = 1.f + 0.35f * smooth(-0.8f, -0.95f, d) - 0.45f * smooth(0.75f, 0.95f, d);
        const float lv = (0.88f + 0.08f * cyl) * lit;
        c.x += w2 * A.x * lv;
        c.y += w2 * A.y * lv;
        c.z += w2 * A.z * lv;
    }
    const float dep = Bb[8] * (0.42f + 0.58f * (0.5f + 0.5f * z));
    return make_float3(c.x * dep, c.y * dep, c.z * dep);
}

extern "C" __global__
void copper_draw(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                 const float* __restrict__ B, const int nb)
{
    __shared__ float sb[MAXB * BS];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int k = tid; k < nb * BS; k += blockDim.x * blockDim.y) sb[k] = B[k];
    __syncthreads();
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (y + 0.5f - hh) / hh;
    const float ca = P[14], sa = P[15];
    const float s = v * ca - u * sa;
    const float r = u * ca + v * sa;
    const float kk = P[21];
    float sR = 0.f, cR = 1.f;
    if (kk != 0.f) __sincosf(kk * r, &sR, &cR);
    const float s2R = 2.f * sR * cR, c2R = cR * cR - sR * sR;
    const float s3R = s2R * cR + c2R * sR, c3R = c2R * cR - s2R * sR;
    const float c2 = P[17], c3 = P[19];
    const float px = P[27], persp = P[28], glow = P[30], reach = P[31];

    int i1 = -1, i2 = -1;
    float z1 = -1e9f, z2 = -1e9f, a1 = 0.f, a2 = 0.f, d1 = 0.f, d2 = 0.f;
    float hx = 0.f, hy = 0.f, hzz = 0.f;
    for (int b = 0; b < nb; ++b) {
        const float* Bb = sb + b * BS;
        const float st = Bb[0] * cR + Bb[1] * sR;
        const float ct = Bb[1] * cR - Bb[0] * sR;
        const float st2 = Bb[2] * c2R + Bb[3] * s2R;
        const float st3 = Bb[4] * c3R + Bb[5] * s3R;
        const float c = Bb[6] * (st + c2 * st2 + c3 * st3);
        const float z = ct;
        const float hw = Bb[7] * (1.f + persp * z);
        const float dist = fabsf(s - c);
        if (dist > hw * reach + 2.f * px) continue;
        float fp = px;
        if (kk != 0.f) {                              // twisted bar: widen the AA footprint by the slope
            const float ct2 = Bb[3] * c2R - Bb[2] * s2R;
            const float ct3 = Bb[5] * c3R - Bb[4] * s3R;
            const float sl = Bb[6] * kk * (ct + 2.f * c2 * ct2 + 3.f * c3 * ct3);
            fp = px * sqrtf(1.f + sl * sl);
        }
        const float dep = 0.4f + 0.6f * (0.5f + 0.5f * z);
        if (glow > 0.f) {
            const float hd = fmaxf(dist - hw, 0.f) / hw;
            const float hw8 = glow * Bb[8] * Bb[9] * dep * __expf(-2.4f * hd);
            hx += hw8 * Bb[10]; hy += hw8 * Bb[11]; hzz += hw8 * Bb[12];
        }
        const float cov = clamp01((hw - dist) / fp + 0.5f) * Bb[9];
        if (cov <= 0.f) continue;
        const float d = (s - c) / hw * (Bb[6] < 0.f ? -1.f : 1.f);
        if (z > z1) { i2 = i1; z2 = z1; a2 = a1; d2 = d1; i1 = b; z1 = z; a1 = cov; d1 = d; }
        else if (z > z2) { i2 = b; z2 = z; a2 = cov; d2 = d; }
    }
    // background: deep palette-tinted gradient across the bars, vignetted
    const float* PAL = P;
    const float asp = P[29];
    const float3 bgc = pal(P[13] * 0.5f + 0.55f + 0.18f * s * P[12], PAL);
    const float vg = 1.f - 0.22f * (u * u / (asp * asp) + v * v);
    const float bl = P[25] * vg;
    float3 col = make_float3(bgc.x * bl, bgc.y * bl, bgc.z * bl);
    // soft halo (saturating so bunched bars do not blow out)
    const float hm = fmaxf(hx, fmaxf(hy, hzz));
    const float hs = 1.f / (1.f + hm);
    col.x += hx * hs; col.y += hy * hs; col.z += hzz * hs;
    if (i2 >= 0) {
        const float3 cc = shade_bar(sb + i2 * BS, d2, z2, P);
        col.x += (cc.x - col.x) * a2; col.y += (cc.y - col.y) * a2; col.z += (cc.z - col.z) * a2;
    }
    if (i1 >= 0) {
        const float3 cc = shade_bar(sb + i1 * BS, d1, z1, P);
        col.x += (cc.x - col.x) * a1; col.y += (cc.y - col.y) * a1; col.z += (cc.z - col.z) * a1;
    }
    const float g = 255.f * P[26];
    out[y * W + x] = pack(make_float4(col.x * g, col.y * g, col.z * g, 255.f));
}
""" % {"maxb": MAXB, "bs": BS}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


def _bright_pal(t: np.ndarray, co: np.ndarray) -> np.ndarray:
    """Host copy of the shared cosine palette, folded towards its bright side so emissive bars never go muddy."""
    a, b, c, d = co[0:3], co[3:6], co[6:9], co[9:12]
    col = np.clip(a + b * np.cos(2.0 * np.pi * (c * t[:, None] + d)), 0.0, 1.0)
    m0 = col.max(axis=1, keepdims=True)
    x = np.clip((m0 - 0.12) / 0.33, 0.0, 1.0)
    w = 1.0 - x * x * (3.0 - 2.0 * x)
    col = col + w * (np.clip(2.0 * a - col, 0.0, 1.0) - col)
    k = 1.0 / np.maximum(col.max(axis=1, keepdims=True), 0.6)
    return col * k


class CopperBars(Source):
    name = "copper_bars"
    label = "Copper & Kefrens bars"
    description = ("Amiga raster bars: chrome-shaded copper bars sweeping on summed-sine paths behind and in front "
                   "of each other, turning through diagonal into a twisting Kefrens ribbon wall.")
    params = _order({
        "bar_count": num(1.0, 32.0, 12.0, "Bars", step=0.1, tier="regional", basic=True),
        "angle": num(0.0, 1.0, 0.0, "Angle (horizontal -> diagonal -> vertical Kefrens)", tier="regional",
                     basic=True),
        "thickness": num(0.3, 2.5, 1.0, "Thickness", tier="regional"),
        "wave_complexity": num(1.0, 3.0, 2.0, "Wave complexity (summed sines)", tier="regional"),
        "spread": num(0.15, 1.0, 0.7, "Spread round the drum", tier="regional"),
        "shading": num(0.0, 1.0, 0.25, "Shading (chrome -> copper -> flat)", tier="regional"),
        "mirror_symmetry": num(0.0, 1.0, 0.0, "Mirror symmetry", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=1.0, period=1.0, drift=0.05),
    }, ["bar_count", "angle", "thickness", "wave_complexity", "spread", "shading", "mirror_symmetry",
        "energy", "pulse", "palette", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k_draw = raw_module(_SRC, _OPTIONS).get_function("copper_draw")
        self.Ph = np.zeros(32, np.float32)
        self.P = cp.zeros(32, cp.float32)
        self.Bh = np.zeros((MAXB, BS), np.float32)
        self.B = cp.zeros((MAXB, BS), cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.ph = float(rng.uniform(0, 2 * math.pi))
        self.p2 = float(rng.uniform(0, 2 * math.pi))
        self.p3 = float(rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.tm = 0.0
        self.hist: deque = deque()

    def _delayed_pulse(self, delays: np.ndarray) -> np.ndarray:
        ts = np.fromiter((h[0] for h in self.hist), np.float64, len(self.hist))
        vs = np.fromiter((h[1] for h in self.hist), np.float64, len(self.hist))
        return np.interp(self.tm - delays, ts, vs)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(float(dt), 0.0)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)

        # host-integrated phases (kicks never touch them)
        rate = 0.25 + 0.75 * e
        tau = 2 * math.pi
        self.ph = _wrap(self.ph + tau * 0.3 * rate * spd * dt, tau)
        self.p2 = _wrap(self.p2 + tau * 0.085 * (0.5 + 0.5 * e) * spd * dt, tau)
        self.p3 = _wrap(self.p3 - tau * 0.055 * (0.5 + 0.5 * e) * spd * dt, tau)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)

        # time-stamped pulse history for the per-bar ripple delay
        self.tm += dt
        if self.hist and self.hist[-1][0] >= self.tm:
            self.hist.pop()
        self.hist.append((self.tm, pulse))
        while len(self.hist) > 2 and self.hist[1][0] < self.tm - 0.6:
            self.hist.popleft()

        bc = float(p["bar_count"])
        n_eff = min(max(1.0, bc * (0.4 + 0.6 * e ** 1.2)), float(MAXN))
        n = min(MAXN, int(math.ceil(n_eff - 1e-6)))
        idx = np.arange(n, dtype=np.float64)
        spacing = tau * float(p["spread"]) / max(bc, 1.0)
        theta = self.ph - idx * spacing

        ang = float(p["angle"]) * 0.5 * math.pi
        ca, sa = math.cos(ang), math.sin(ang)
        asp = self.w / self.h
        ext = abs(ca) + asp * abs(sa)
        wc = float(p["wave_complexity"])
        c2 = 0.55 * min(max(wc - 1.0, 0.0), 1.0)
        c3 = 0.35 * min(max(wc - 2.0, 0.0), 1.0)
        amp = 0.7 * ext * (0.6 + 0.4 * e) / (1.0 + 0.45 * (c2 + c3))
        kef = _ss(0.55, 0.95, float(p["angle"]))
        hw = float(p["thickness"]) * 0.06 * (12.0 / max(bc, 3.0)) ** 0.35

        delay = min(0.03, 0.36 / max(bc - 1.0, 1.0))
        kick = self._delayed_pulse(idx * delay) * (0.55 + 0.45 * e)
        alpha = np.clip(n_eff - idx, 0.0, 1.0)

        co = np.asarray(palette_coeffs(p["palette"]), np.float64)
        period = float(p["palette_period"])
        tcol = self.drift + period * idx / max(bc, 1.0)
        colA = _bright_pal(tcol, co)
        colB = _bright_pal(tcol + 0.5, co) * 0.75

        Bh = self.Bh
        Bh[:n, 0] = np.sin(theta)
        Bh[:n, 1] = np.cos(theta)
        Bh[:n, 2] = np.sin(2 * theta + self.p2)
        Bh[:n, 3] = np.cos(2 * theta + self.p2)
        Bh[:n, 4] = np.sin(3 * theta + self.p3)
        Bh[:n, 5] = np.cos(3 * theta + self.p3)
        Bh[:n, 6] = amp
        Bh[:n, 7] = hw * (1.0 + 0.5 * kick)
        Bh[:n, 8] = (0.82 + 0.18 * e) * (1.0 + 0.6 * kick)
        Bh[:n, 9] = alpha
        Bh[:n, 10:13] = colA
        Bh[:n, 13:16] = colB
        nb = n
        mirror = float(p["mirror_symmetry"])
        if mirror > 1e-3:
            Bh[n:2 * n] = Bh[:n]
            Bh[n:2 * n, 6] = -amp
            Bh[n:2 * n, 9] = alpha * min(mirror, 1.0)
            nb = 2 * n
        self.B.set(Bh)

        P = self.Ph
        P[0:12] = co
        P[12] = period
        P[13] = self.drift
        P[14], P[15] = ca, sa
        P[17] = c2
        P[19] = c3
        P[21] = kef * (2.4 + 1.6 * e)
        P[22] = min(max(float(p["shading"]), 0.0), 1.0)
        P[23] = 0.45 + 0.55 * e
        P[25] = 0.07 + 0.04 * e
        P[26] = float(p["intensity"])
        P[27] = 2.0 / self.h
        P[28] = 0.3
        P[29] = asp
        P[30] = 0.1 + 0.2 * e
        P[31] = 3.5
        self.P.set(P)

        dst = out if self.direct else self.buf
        launch_2d(self.k_draw, self.w, self.h, (dst, np.int32(self.w), np.int32(self.h), self.P, self.B,
                                                np.int32(nb)))
        if not self.direct:
            present(self.ctx, self.buf, out)
