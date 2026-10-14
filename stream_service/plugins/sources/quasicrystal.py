"""Quasicrystal: 5-13 plane waves at evenly spaced angles, summed and folded, scrolling into aperiodic rosettes.

Source: Original (inspired by https://mainisusuallyafunction.blogspot.com/2011/10/quasicrystals-as-sums-of-waves-in-plane.html)
Author: smfx (original; after jwz's xscreensaver quasicrystal and the blog 'Quasicrystals as sums of waves in the plane', maths only)
License: MIT (original)
license_class: permissive

Written from the maths alone (nothing copied).  For N waves with unit directions d_k at angles k pi / N:
    S(p) = sum_k w_k (0.5 + 0.5 cos(2 pi (f d_k . p + phi_k)))          in [0, W],  W = sum_k w_k
then folded: v = tri(F S), the period-2 triangle wave ("wrap": floor even -> frac, odd -> 1 - frac).  Any N >= 5
gives a pattern with N-fold (2N for odd N) rosettes that never repeats; scrolling the phases makes them flow.

``wave_count`` is continuous: N(t) = n + f puts the waves at angles k pi / N(t) and fades the (n+1)-th wave in
with weight f, so a count change is a smooth moire morph (at f = 0 the extra wave sits at pi with zero weight,
at f = 1 it is a full member of the n + 1 set).  ``fold`` morphs the luminance smooth (the raw normalised sum
S / W) -> soft cosine folds -> the crisp triangle wrap.  Both folds are box-filtered over the pixel footprint
using the analytic gradient of S (through the radial-warp Jacobian), so dense folds fade to grey, never alias.
``angle_jitter`` detunes each wave's angle by a slow bounded wander (breaks the perfect symmetry into living
moire); ``radial_warp`` is a barrel lens p -> p (1 + a |p|^2) (waves bend and compress towards the edges);
``per_wave_hue`` layers a palette hue per wave, weighted by its local crest strength (rosettes go white-hot
where many crests meet), mixed with the summed-field palette colour.

Colour: palette index = period * (0.9 * (2 S/W - 1) + 0.22 v) + drift (summed field + drift), lifted to a bright
hue, times a contrast curve of v plus a white-tinted glow on the fold crests.  Renders at render_scale 1.0 by
default (analytic, ~0.3 ms at 4K).

Macros: energy (e' = smoothstep(0.1, 0.9, e), slewed 0.6 s for the fold count) -> common phase speed
(0.12 + 0.5 e' turns/s), contrast (curve gamma 1.1 -> 2.0, crest glow), fold count F (0.8 + 1.1 e'^1.3), the
zoom-breathing amplitude (+-2% -> +-12%) and rate, phase-wander depth and the slow global rotation rate.
pulse (k = pulse * (0.3 + 0.7 e)) -> a contrast flash (lum gain <= +55%, crest glow <= x2.5) and a phase-
velocity kick (rate x (1 + 2.5 k)); it never moves positions.  speed scales every rate (0 freezes it);
palette/palette_period/drift_speed colour it (session palette wins when set); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

MAXW = 14                                # wave slots (13 + the fading-in one)
NP = 144
TAU = 2.0 * math.pi
BASE_FREQ = 6.5                          # waves per half frame height at scale 1

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 cos rot | 15 sin rot | 16 freq (waves per half-height unit)
#  17 W (total weight) | 18 fold blend (smooth -> folded) | 19 fold sharpness (cos -> tri) | 20 fold count F
#  21 gamma | 22 glow gain | 23 flash | 24 radial warp a | 25 per-wave hue mix | 26 output gain
#  27 active wave count (int) | 28 1 / half-height (px -> units)
#  32.. dir x | 48.. dir y | 64.. phase (turns) | 80.. weight | 96.. per-wave rgb (3 x MAXW)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAXW %(maxw)d
__device__ __forceinline__ float tri2(float x) {               // period-2 triangle: 0 at even, 1 at odd
    return fabsf(x - 2.f * rintf(0.5f * x));
}
__device__ __forceinline__ float tri_int(float x) {            // antiderivative of tri2
    const float fl = floorf(0.5f * x), u = x - 2.f * fl;
    const float g = u <= 1.f ? 0.5f * u * u : 2.f * u - 0.5f * u * u - 1.f;
    return fl + g;
}
__device__ __forceinline__ float3 lift(float3 c, float amt) {   // keep hue, raise dark palette phases
    const float mx = fmaxf(c.x, fmaxf(c.y, c.z));
    const float k = 1.f + amt * (1.f / fmaxf(mx, 0.3f) - 1.f);
    return make_float3(fminf(c.x * k, 1.f), fminf(c.y * k, 1.f), fminf(c.z * k, 1.f));
}

extern "C" __global__
void quasicrystal(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (y + 0.5f - hh) / hh;
    float px = P[14] * u - P[15] * v, py = P[15] * u + P[14] * v;
    const float r2 = px * px + py * py, a = P[24];
    const float m = 1.f + a * r2;
    px *= m; py *= m;
    const float stretch = 1.f + 3.f * a * r2;                   // radial derivative of the barrel map
    const float fq = P[16];
    const int n = (int)P[27];
    const float hue = P[25];
    float S = 0.f, gx = 0.f, gy = 0.f, hr = 0.f, hg = 0.f, hb = 0.f, hs = 0.f;
    for (int k = 0; k < n; ++k) {
        const float dx = P[32 + k], dy = P[48 + k], w = P[80 + k];
        float t = (dx * px + dy * py) * fq + P[64 + k];
        t -= rintf(t);
        float sn, cs;
        __sincosf(6.2831853f * t, &sn, &cs);
        const float ck = 0.5f + 0.5f * cs;
        S += w * ck;
        gx -= w * sn * dx; gy -= w * sn * dy;
        if (hue > 0.f) {
            const float c2 = ck * ck, wc = w * c2 * c2;
            hr += wc * P[96 + 3 * k]; hg += wc * P[97 + 3 * k]; hb += wc * P[98 + 3 * k]; hs += wc;
        }
    }
    const float Wt = P[17];
    const float sn01 = S / Wt;
    // pixel footprint of F*S (|grad S| = pi fq |sum w sin d|, through the warp, per internal px)
    const float F = P[20];
    const float xs = F * S;
    const float h = fminf(F * 3.1415927f * fq * sqrtf(gx * gx + gy * gy) * stretch * P[28], 8.f);
    const float ftri = h < 1e-3f ? tri2(xs) : (tri_int(xs + 0.5f * h) - tri_int(xs - 0.5f * h)) / h;
    float tc = 0.5f * xs; tc -= rintf(tc);
    const float z = 1.5707963f * h;
    const float fcos = 0.5f - 0.5f * __cosf(6.2831853f * tc) * __expf(-z * z * (1.f / 6.f));
    const float fold = fcos + P[19] * (ftri - fcos);
    const float vv = clamp01(sn01 + P[18] * (fold - sn01));
    // luminance: contrast curve + crest glow, flashed by the pulse
    const float base = __powf(fmaxf(vv, 1e-5f), P[21]);
    const float v4 = vv * vv * vv * vv, glow = P[22] * v4 * v4 * vv * vv;
    const float lum = 0.035f + 0.965f * base * (1.f + P[23]);
    // colour: summed field + fold + drift through the palette, mixed with the per-wave hue layering
    const float idx = P[13] + P[12] * (0.9f * (2.f * sn01 - 1.f) + 0.22f * vv);
    float3 c = lift(pal(idx, P), 0.75f);
    if (hue > 0.f) {
        const float ih = 1.f / fmaxf(hs, 1e-5f);
        const float3 hc = lift(make_float3(hr * ih, hg * ih, hb * ih), 0.85f);
        c.x += hue * (hc.x - c.x); c.y += hue * (hc.y - c.y); c.z += hue * (hc.z - c.z);
    }
    const float rr = u * u + v * v;
    const float vg = 1.f - 0.2f * rr / (1.f + (float)W * W / ((float)H * H));
    const float g = 255.f * P[26] * vg;
    out[y * W + x] = pack(make_float4((c.x * lum + glow * (0.5f + 0.5f * c.x)) * g,
                                      (c.y * lum + glow * (0.5f + 0.5f * c.y)) * g,
                                      (c.z * lum + glow * (0.5f + 0.5f * c.z)) * g, 255.f));
}
""" % {"maxw": MAXW}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _pal_np(coeffs: np.ndarray, t: np.ndarray) -> np.ndarray:
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return np.clip(a[None] + b[None] * np.cos(TAU * (c[None] * t[:, None] + d[None])), 0.0, 1.0)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class Quasicrystal(Source):
    name = "quasicrystal"
    label = "Quasicrystal"
    description = ("Quasicrystal interference: 5-13 plane waves summed and folded, with scrolling phases so "
                   "aperiodic 5-, 7- and 9-fold rosettes flow forever. Crisp, infinite and non-repeating.")
    tags = {"family": "geometric", "dims": "2d", "toolkit": "none", "technique": ["analytic", "field", "optical"],
            "moods": ["hypnotic", "geometric", "cosmic"], "style": "lively", "energy": [0.1, 0.9]}
    params = _order({
        "wave_count": num(5.0, 13.0, 7.0, "Waves (symmetry)", step=0.05, tier="regional", basic=True),
        "scale": num(0.4, 2.5, 1.0, "Scale", tier="regional", basic=True),
        "fold": num(0.0, 1.0, 0.85, "Fold (smooth -> wrapped)", tier="regional"),
        "angle_jitter": num(0.0, 1.0, 0.15, "Angle jitter", tier="regional"),
        "per_wave_hue": num(0.0, 1.0, 0.3, "Per-wave hue", tier="regional"),
        "radial_warp": num(0.0, 1.0, 0.0, "Radial warp", tier="regional"),
        **standard_scene_params(palette="rainbow", render_scale=1.0, period=0.8, drift=0.03),
    }, ["wave_count", "scale", "fold", "angle_jitter", "per_wave_hue", "radial_warp",
        "energy", "pulse", "palette", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTIONS).get_function("quasicrystal")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.common = float(rng.uniform(0.0, 1.0))                  # shared scrolling phase (turns)
        self.wander = float(rng.uniform(0.0, TAU))                  # phase / angle wander clock (rad)
        self.w_rate = rng.uniform(0.6, 1.4, MAXW)                   # per-wave wander rates
        self.w_off = rng.uniform(0.0, TAU, MAXW)                    # per-wave phase-wander offsets
        self.j_rate = rng.uniform(0.5, 1.3, MAXW)                   # per-wave angle-jitter rates
        self.j_off = rng.uniform(0.0, TAU, MAXW)
        self.breath = float(rng.uniform(0.0, TAU))
        self.rot = float(rng.uniform(0.0, 1.0))
        self.drift = 0.0
        self.e_s = None
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))

    def close(self) -> None:
        self.buf = None
        self.P = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        e1 = _ss(0.1, 0.9, e)
        a = (1.0 - math.exp(-dt / 0.6)) if dt > 0 else 1.0
        self.e_s = e1 if self.e_s is None else self.e_s + (e1 - self.e_s) * a
        es = self.e_s
        k = pulse * (0.3 + 0.7 * e)

        # palette (session palette wins; eased so a change never snaps) and drift
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # host-integrated clocks (kicks add to rates only)
        self.common = _wrap(self.common + (0.12 + 0.5 * e1) * (1.0 + 2.5 * k) * spd * dt, 1.0)
        self.wander = _wrap(self.wander + (0.05 + 0.07 * e1) * spd * dt, TAU * 1000.0)
        self.breath = _wrap(self.breath + TAU * (0.05 + 0.06 * e1) * spd * dt, TAU)
        self.rot = _wrap(self.rot + (0.003 + 0.009 * e1) * spd * dt, 1.0)

        # continuous wave count: n full waves + the (n+1)-th fading in, at angles k pi / N
        N = min(max(float(p["wave_count"]), 5.0), 13.0)
        n = int(math.floor(N))
        f = N - n
        cnt = n + (1 if f > 1e-4 else 0)
        ks = np.arange(MAXW, dtype=np.float64)
        jit = float(p["angle_jitter"]) * 0.45 * (math.pi / N) * np.sin(self.wander * self.j_rate + self.j_off)
        ang = ks * math.pi / N + jit
        wts = np.where(ks < n, 1.0, np.where(ks == n, f, 0.0))
        dep = 0.06 + 0.14 * es
        phase = self.common + dep * np.sin(self.wander * 1.7 * self.w_rate + self.w_off)
        phase = phase - np.round(phase)

        # zoom breathing (amplitude from energy) around the regional scale
        amp = 0.02 + 0.10 * es ** 1.2
        zoom = math.exp(amp * math.sin(self.breath))
        fq = BASE_FREQ / float(p["scale"]) * zoom

        P = self.Ph
        P[0:12] = self.pal
        period = float(p["palette_period"])
        P[12] = period
        P[13] = self.drift
        P[14], P[15] = math.cos(TAU * self.rot), math.sin(TAU * self.rot)
        P[16] = fq
        P[17] = n + f
        fd = float(p["fold"])
        P[18] = _ss(0.0, 0.5, fd)
        P[19] = _ss(0.5, 1.0, fd)
        P[20] = 0.8 + 1.1 * es ** 1.3
        P[21] = 1.1 + 0.9 * e1
        P[22] = (0.25 + 0.35 * e1) * (1.0 + 1.5 * k)
        P[23] = 0.55 * k
        P[24] = 0.35 * float(p["radial_warp"])
        P[25] = float(p["per_wave_hue"])
        P[26] = float(p["intensity"])
        P[27] = cnt
        P[28] = 2.0 / self.h
        P[32:32 + MAXW] = np.cos(ang)
        P[48:48 + MAXW] = np.sin(ang)
        P[64:64 + MAXW] = phase
        P[80:80 + MAXW] = wts
        hues = _pal_np(self.pal, self.drift + period * ks / N)
        P[96:96 + 3 * MAXW] = hues.reshape(-1)
        self.P.set(P)

        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
