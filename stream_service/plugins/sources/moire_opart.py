"""Moire op-art: two or three fine gratings, slightly offset, rotated and warped, interfering into shimmering moire.

Source: Original (inspired by https://en.wikipedia.org/wiki/Moir%C3%A9_pattern and Bridget Riley's op-art)
Author: smfx
License: MIT
license_class: permissive

Each grating is the square wave of an analytic phase field
    phi = f * w * (|q + (D, 0)| - D) + N * atan2(q.y, q.x) / 2pi
in its own frame q = scale * R(rot) * (warp(p) - centre).  The ``grating`` knob morphs continuously through
lines (D = inf) -> rings (D = 0) -> radial spokes (w = 0, N) -> spirals (w + N): lines->rings bends the lines
into circles whose centre arrives from infinity, rings->radial passes through spirals of growing arm count,
radial->spiral twists the spokes.  N is kept an integer (no seam at the branch cut); a fractional N
crossfades floor/ceil inside a narrow window only.  Every stripe is box-filtered by its analytic pixel
footprint (gradient through the warp Jacobian), so fine lines never alias and singular centres fade to grey;
line_frequency is also capped at 3.5 px per period.  Gratings combine by overlay (ink darkens: a * b) through
to XNOR (``interference_mix``); the third grating fades in with energy.  Colour is mono (faint palette tint)
or a palette duotone whose hue rides the low-frequency moire field cos(2 pi (phi_A - phi_B)), itself faded by
its own footprint.  Renders at render_scale 1.0 by default because resampling destroys the moire (~2 ms at 4K).

Macros: energy (remapped e' = smoothstep(0.1, 0.85, e)) -> relative-rotation and scroll rates x(0.4+1.2e'),
warp amplitude and warp phase rate, centre separation and third-grating fade (e'^1.5, slewed), base contrast.
pulse (k = pulse * (0.3 + 0.7e), 45 ms attack) -> contrast kick (<= +35%), a localised glow on the
opposed-phase moire bands and a small offset-scale swell (amplitude), all scaled by ``bloom_kick``.
speed scales every phase; palette gives the duotone stops pal(drift) and pal(drift + 0.5 * period), brightness-
normalised to a deep and a full stop; intensity is output gain.
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

TAU = 2.0 * math.pi
N_REF_R = 0.30          # radial spoke count N = 2 pi * N_REF_R * f: spokes match the line density at r = 0.3 H

# P layout (floats)
#  0 f | 1 duty | 2 conic a | 3 conic b | 4 N_lo | 5 N blend | 6 pixel footprint (height units) | 7 warp amp | 8 warp k
#  9 warp ph1 | 10 warp ph2 | 11 xor mix | 12 contrast | 13 third-grating fade | 14 vignette | 15 gain
#  16 colour mix | 17 drift | 18 period | 19 fringe hue | 20 bloom (fringe glow) | 21 aspect W/H | 22 conic weight
#  24..35 palette | 40 + 12 i: grating i: cx, cy, cos, sin, scale, phase, cos/sin(warp offset), cos/sin(0.7 offset)
_SRC = HELPERS + PALETTE_CUDA + r"""
struct Phi { float v, gx, gy; };

// Phase of one grating at local point q, with its gradient w.r.t. q.
// Conic term C = |q + (D, 0)| - D with D = a / b, written stably: (2a qx + b r^2) / (a + |(a + b qx, b qy)|).
// b = 0 gives lines (C = qx), a = 0 rings (C = |q|); in between, circles whose centre comes in from infinity, so
// the line density stays f everywhere (|grad C| = 1).
__device__ __forceinline__ Phi grating_phi(float qx, float qy, float f, float ca, float cb, float cw, float n) {
    const float r2 = qx * qx + qy * qy + 1e-12f;
    const float ux = ca + cb * qx, uy = cb * qy;
    const float s = sqrtf(ux * ux + uy * uy) + 1e-7f;
    const float fw = f * cw;
    Phi o;
    o.v = fw * (2.f * ca * qx + cb * r2) / (ca + s);
    o.gx = fw * ux / s;
    o.gy = fw * uy / s;
    if (n != 0.f) {
        const float k = n * 0.15915494f;
        o.v += k * atan2f(qy, qx);
        o.gx += -k * qy / r2;
        o.gy += k * qx / r2;
    }
    return o;
}

// Mean ink coverage (ink where frac(t) < d) over [phi - w/2, phi + w/2]: box-filtered square wave.
__device__ __forceinline__ float sqbox(float phi, float w, float d) {
    w = fmaxf(w, 1e-4f);
    const float a = phi - 0.5f * w, b = phi + 0.5f * w;
    const float fa = floorf(a), fb = floorf(b);
    const float Sa = fa * d + fminf(a - fa, d);
    const float Sb = fb * d + fminf(b - fb, d);
    return clamp01((Sb - Sa) / w);
}

// White (paper) fraction of grating i at screen point p (height units, y down); also returns its phase and the
// phase gradient in screen space (for the moire-field footprint).  sA/cA: sin/cos of the shared warp arguments.
__device__ float grating(const float* __restrict__ P, int i, float px, float py, float sA1, float cA1, float sA2,
                         float cA2, float& phase, float& pgx, float& pgy) {
    const float* G = P + 40 + 12 * i;
    const float amp = P[7], kw = P[8];
    // sinusoidal warp (Riley "Current"-style waves), shifted by this grating's own warp phase offset
    const float s1 = sA1 * G[6] + cA1 * G[7], c1 = cA1 * G[6] - sA1 * G[7];
    const float s2 = sA2 * G[8] - cA2 * G[9], c2 = cA2 * G[8] + sA2 * G[9];
    const float wx = px + amp * s1, wy = py + 0.6f * amp * s2;
    const float j01 = amp * kw * c1, j10 = 0.6f * amp * 0.83f * kw * c2;   // d(wx)/dy, d(wy)/dx
    const float dx = wx - G[0], dy = wy - G[1];
    const float c = G[2], s = G[3], sc = G[4];
    const float qx = sc * (c * dx - s * dy), qy = sc * (s * dx + c * dy);
    const float f = P[0], ca = P[2], cb = P[3], cw = P[22], nlo = P[4], nb = P[5];
    const float pix = P[6], duty = P[1];
    Phi a = grating_phi(qx, qy, f, ca, cb, cw, nlo);
    // gradient w.r.t. screen: sc * Jw^T * R^T * g
    float rx = sc * (c * a.gx + s * a.gy), ry = sc * (-s * a.gx + c * a.gy);
    float gx = rx + j10 * ry, gy = j01 * rx + ry;
    float ink = sqbox(a.v + G[5], sqrtf(gx * gx + gy * gy) * pix, duty);
    phase = a.v + G[5]; pgx = gx; pgy = gy;
    if (nb > 0.f) {                               // fractional spoke count: crossfade N_lo -> N_lo + 1
        Phi b = grating_phi(qx, qy, f, ca, cb, cw, nlo + 1.f);
        rx = sc * (c * b.gx + s * b.gy); ry = sc * (-s * b.gx + c * b.gy);
        gx = rx + j10 * ry; gy = j01 * rx + ry;
        const float inkb = sqbox(b.v + G[5], sqrtf(gx * gx + gy * gy) * pix, duty);
        ink += (inkb - ink) * nb;
        if (nb > 0.5f) { phase = b.v + G[5]; pgx = gx; pgy = gy; }
    }
    return 1.f - ink;
}

// Paper fractions a, b -> combined: overlay (ink darkens: a * b) at xm 0; where b has ink, xm shows a inverted
// instead, reaching XNOR at xm 1.  Stays in [0, 1] for every xm.
__device__ __forceinline__ float combine(float a, float b, float xm) {
    return a * b + xm * (1.f - a) * (1.f - b);
}

extern "C" __global__
void moire_opart(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = (x + 0.5f - 0.5f * W) / H, py = (y + 0.5f - 0.5f * H) / H;
    float sA1, cA1, sA2, cA2;
    sincosf(P[8] * py + P[9], &sA1, &cA1);
    sincosf(0.83f * P[8] * px + P[10], &sA2, &cA2);
    float phA, phB, phC, axg, ayg, bxg, byg, cxg, cyg;
    const float a = grating(P, 0, px, py, sA1, cA1, sA2, cA2, phA, axg, ayg);
    const float b = grating(P, 1, px, py, sA1, cA1, sA2, cA2, phB, bxg, byg);
    const float xm = P[11];
    float v = combine(a, b, xm);
    if (P[13] > 0.002f) {
        const float c = grating(P, 2, px, py, sA1, cA1, sA2, cA2, phC, cxg, cyg);
        v += (combine(v, c, xm) - v) * P[13];
    }
    // the low-frequency moire field: +1 where A and B are in phase, -1 where they are opposed (faded to 0 where
    // it varies faster than the pixel grid, e.g. near radial centres, so the hue shift and beat glow never alias)
    float dph = phA - phB;
    dph -= floorf(dph);
    const float fx = axg - bxg, fy = ayg - byg;
    const float m = cosf(6.2831853f * dph) * (1.f - smooth(0.12f, 0.35f, sqrtf(fx * fx + fy * fy) * P[6]));
    const float con = P[12];
    float t = clamp01(0.5f + (v - 0.5f) * con);
    // duotone stops (mono at colour_mix 0, with a faint palette tint); hue rides the moire fringes
    const float* PAL = P + 24;
    const float cm = P[16], drift = P[17], period = P[18];
    const float hs = P[19] * period * m;
    float3 ca = pal(drift + hs, PAL), cb = pal(drift + 0.5f * period + hs, PAL);
    // normalise the stops' brightness so the duotone keeps its value contrast: a deep dark stop, a full light one
    // (a little grey is added first so a near-black palette colour normalises to grey, not to a random hue)
    ca.x += 0.1f; ca.y += 0.1f; ca.z += 0.1f; cb.x += 0.1f; cb.y += 0.1f; cb.z += 0.1f;
    const float na = 0.24f / fmaxf(fmaxf(ca.x, ca.y), ca.z);
    const float nb = 0.96f / fmaxf(fmaxf(cb.x, cb.y), cb.z);
    ca.x *= na; ca.y *= na; ca.z *= na; cb.x *= nb; cb.y *= nb; cb.z *= nb;
    const float tl = 0.08f + 0.92f * cm, th = 0.06f + 0.94f * cm;
    const float lr = 0.012f + (ca.x - 0.012f) * tl, lg = 0.012f + (ca.y - 0.012f) * tl, lb = 0.014f + (ca.z - 0.014f) * tl;
    const float hr = 0.94f + (cb.x - 0.94f) * th, hg = 0.94f + (cb.y - 0.94f) * th, hb = 0.93f + (cb.z - 0.93f) * th;
    float r = lr + (hr - lr) * t, g = lg + (hg - lg) * t, bl = lb + (hb - lb) * t;
    // beat bloom: a glow on the opposed-phase fringe bands (localised, not full-frame)
    const float bloom = P[20] * smooth(0.2f, 1.f, -m);
    r += bloom * (0.35f + 0.65f * cb.x) * (0.4f + 0.6f * t);
    g += bloom * (0.35f + 0.65f * cb.y) * (0.4f + 0.6f * t);
    bl += bloom * (0.35f + 0.65f * cb.z) * (0.4f + 0.6f * t);
    // vignette
    const float vx = px / (0.5f * P[21]), vy = py * 2.f;
    const float vg = 1.f - P[14] * fminf(0.5f * (vx * vx + vy * vy), 1.f);
    const float gn = 255.f * P[15] * vg;
    out[y * W + x] = pack(make_float4(r * gn, g * gn, bl * gn, 255.f));
}
"""


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _morph(s: float) -> tuple[float, float, float, float]:
    """grating 0..1 -> (conic a, conic b, conic weight, n): lines (0) -> rings (1/3) -> radial (2/3) -> spiral (1).
    lines -> rings bends the lines into circles whose centre arrives from infinity (D = R0 (1 - u) / u);
    rings -> radial trades ring weight for spokes (spirals in between); radial -> spiral adds the rings back."""
    s = min(max(s, 0.0), 1.0) * 3.0
    if s <= 1.0:
        return 1.0 - s, s / 0.5, 1.0, 0.0
    if s <= 2.0:
        u = s - 1.0
        return 0.0, 1.0, 1.0 - u, u
    u = s - 2.0
    return 0.0, 1.0, 0.75 * u, 1.0


class MoireOpart(Source):
    name = "moire_opart"
    label = "Moire op-art"
    description = ("Bridget Riley-style op-art: fine line, ring, radial or spiral gratings, offset and warped, "
                   "interfering into shimmering moire waves in black-and-white or duotone.")
    params = {
        "grating": num(0.0, 1.0, 0.33, "Grating (lines - rings - radial - spiral)", tier="regional", basic=True),
        "line_frequency": num(20.0, 120.0, 56.0, "Line frequency", step=1.0, unit="lines/H", tier="regional"),
        "offset_scale": num(0.95, 1.05, 1.015, "Second grating scale", step=0.001, tier="regional", basic=True),
        "warp_amount": num(0.0, 0.3, 0.1, "Warp", tier="regional"),
        "line_thickness": num(0.2, 0.8, 0.5, "Line thickness (duty)", tier="regional"),
        "interference_mix": num(0.0, 1.0, 0.85, "Interference (overlay - XOR)", tier="regional"),
        "colour_mix": num(0.0, 1.0, 0.3, "Colour (mono - duotone)", tier="regional"),
        "bloom_kick": num(0.0, 1.0, 0.6, "Beat bloom", tier="regional"),
        **standard_scene_params(palette="ice", render_scale=1.0, period=1.0, drift=0.02),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, ("--std=c++14", "--use_fast_math")).get_function("moire_opart")
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.P = cp.zeros(80, cp.float32)
        self.Ph = np.zeros(80, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.rnd = rng.uniform(0.0, 1.0, 16)
        self.rot_ph = TAU * self.rnd[0]         # relative-rotation oscillation
        self.glob = TAU * self.rnd[1]           # global rotation
        self.sep_ph = TAU * self.rnd[2]         # direction of the centre separation
        self.wander = TAU * self.rnd[3]         # centre wander
        self.scroll = [float(v) for v in self.rnd[4:7]]   # grating stripe phases (periods)
        self.warp1, self.warp2 = TAU * self.rnd[7], TAU * self.rnd[8]
        self.breath = TAU * self.rnd[9]         # slow breathing of the scale offset (fringes flow in / out)
        self.drift = 0.0
        self.es = None                          # slewed remapped energy (amplitudes / density)
        self.ks = 0.0                           # fast-attack smoothed pulse

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        e1 = _smoothstep(0.1, 0.85, e)
        if self.es is None:
            self.es = e1
        elif dt > 0:
            self.es += (e1 - self.es) * (1.0 - math.exp(-dt / 1.5))
        es = self.es
        k = pulse * (0.3 + 0.7 * e)
        if k > self.ks:
            self.ks += (k - self.ks) * (1.0 - math.exp(-dt / 0.045)) if dt > 0 else k - self.ks
        else:
            self.ks = k
        bloom = float(p["bloom_kick"]) * self.ks

        # ---- integrate phases (rates only; energy fans out on separate curves)
        rate = 0.4 + 1.2 * e1
        sd = spd * dt
        self.rot_ph = _wrap(self.rot_ph + 0.22 * rate * sd, TAU)
        self.glob = _wrap(self.glob + 0.035 * (0.5 + 0.8 * e1) * sd, TAU)
        self.sep_ph = _wrap(self.sep_ph + 0.15 * rate * sd, TAU)
        self.wander = _wrap(self.wander + 0.2 * rate * sd, TAU * 10.0)
        self.breath = _wrap(self.breath + 0.33 * rate * sd, TAU)
        for i, r in enumerate((0.12, -0.17, 0.08)):
            self.scroll[i] = _wrap(self.scroll[i] + r * rate * sd, 1.0)
        self.warp1 = _wrap(self.warp1 + 0.55 * rate * sd, TAU)
        self.warp2 = _wrap(self.warp2 + 0.41 * rate * sd, TAU)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * sd, 1000.0)

        # ---- geometry
        f = min(float(p["line_frequency"]), self.h / 3.5)          # keep >= 3.5 px per line period
        ca, cb, cw, nf = _morph(float(p["grating"]))
        n_cont = nf * TAU * N_REF_R * f
        n_lo = math.floor(n_cont)
        n_bl = _smoothstep(0.35, 0.65, n_cont - n_lo)
        alpha = math.radians(0.6 + 5.4 * (0.5 + 0.5 * math.sin(self.rot_ph)))   # relative rotation 0.6..6 deg
        g0 = self.glob
        sep = 0.045 + 0.04 * es ** 1.5
        wa = 0.05 + 0.07 * (0.5 + 0.5 * math.sin(self.wander))
        cA = (wa * math.sin(0.7 * self.wander) * 0.9, wa * math.sin(0.5 * self.wander + 1.0) * 0.6)
        cB = (cA[0] + sep * math.cos(self.sep_ph), cA[1] + sep * math.sin(self.sep_ph))
        cC = (cA[0] - 0.8 * sep * math.cos(self.sep_ph + 2.1), cA[1] - 0.8 * sep * math.sin(self.sep_ph + 2.1))
        os_ = float(p["offset_scale"])
        breathe = 0.005 * (0.6 + 0.4 * es) * math.sin(self.breath)
        sB = os_ + breathe + 0.007 * bloom                    # + beat swell of the scale offset (amplitude)
        sC = 2.0 - os_ + 0.012 * (es - 0.5)
        grats = ((cA, g0, 1.0, self.scroll[0], 0.0),
                 (cB, g0 + alpha, sB, self.scroll[1], 0.7),
                 (cC, g0 - 0.8 * alpha + 0.03, sC, self.scroll[2], -0.9))
        warp_amp = float(p["warp_amount"]) * 0.12 * (0.5 + 0.7 * _smoothstep(0.0, 1.0, es))
        fade3 = 0.85 * es ** 1.5
        con = (0.86 + 0.14 * es) * (1.0 + 0.35 * bloom)

        Ph = self.Ph
        Ph[0] = f
        Ph[1] = float(p["line_thickness"])
        Ph[2], Ph[3], Ph[4], Ph[5], Ph[22] = ca, cb, float(n_lo), n_bl, cw
        Ph[6] = 1.25 / self.h
        Ph[7], Ph[8], Ph[9], Ph[10] = warp_amp, TAU / 0.42, self.warp1, self.warp2
        Ph[11] = float(p["interference_mix"])
        Ph[12] = con
        Ph[13] = fade3
        Ph[14] = 0.3
        Ph[15] = float(p["intensity"])
        Ph[16] = float(p["colour_mix"])
        Ph[17] = self.drift
        Ph[18] = float(p["palette_period"])
        Ph[19] = 0.08
        Ph[20] = 0.5 * bloom
        Ph[21] = self.w / self.h
        Ph[24:36] = palette_coeffs(p["palette"])
        for i, (c, rot, sc, ph, wo) in enumerate(grats):
            Ph[40 + 12 * i: 50 + 12 * i] = (c[0], c[1], math.cos(rot), math.sin(rot), sc, ph,
                                            math.cos(wo), math.sin(wo), math.cos(0.7 * wo), math.sin(0.7 * wo))
        self.P.set(Ph)
        if self.direct:                         # render_scale 1.0: draw straight into the output
            launch_2d(self.k, self.w, self.h, (out, np.int32(self.w), np.int32(self.h), self.P))
        else:
            launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
            present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
