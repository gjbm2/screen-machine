"""Radial spectrum: an audio-visualiser look with no audio input -- a ring of glowing spectrum bars that unrolls
into a mirrored floor-line analyser and curls back up into a waveform ring.

Source: Original (inspired by https://en.wikipedia.org/wiki/Music_visualization)
Author: smfx (original)
License: MIT
license_class: permissive

Spectrum state (one block, 256 threads, one per bin, a float4 per bin in a tiny device array; never read back):
  target = level x bass-heavy tilt (1 - 0.82 f)^k x smooth per-bin noise of host-integrated time (a broad layer
  correlated across the spectrum + a fine per-bin layer, both value noise on an integer time lattice split
  int/frac on the host so precision never degrades) x an active-band mask that opens towards the treble with
  energy, plus the beat 'kick': the pulse envelope is recorded on the host and uploaded as 16 delayed samples
  (0..190 ms); bin f reads it at delay 0.17 f s with gain exp(-1.7 f), so every beat lands in the bass first and
  cascades up the spectrum like a kick drum.  Bars follow with fast attack / slower release; peak caps hold
  0.35 s then fall under gravity (semi-implicit, substepped to 1/120 s).

Draw (one analytic full-res pass): the baseline is a circular arc of curvature kappa and length L that bends
continuously between a closed ring (kappa = 1/R) and a straight floor line (kappa -> 0, written in a form that
stays exact as kappa -> 0); every pixel gets (s, d) = arc length and signed distance from it.  Bars are mirrored
about the top (bass), tiled by ``symmetry`` folds (integer folds cross-blended so a change eases), keep a
constant pixel width while the gaps open outward, and are drawn from a 3-bar neighbourhood: body + peak cap +
anisotropic glow (windowed so no seams), an ambient bloom over the bar envelope, a glowing baseline, an inner
haze in the ring, and a dimmer reflection on the inside / under the floor.  ``bar_style`` morphs blocks -> LED
segments (quantised, ghosted unlit cells) -> thin lines -> round dot columns.  In the waveform ring the bars
melt into a smooth filled band between two glowing curves through the bar heights, with a faint peak envelope.

Macros: energy (e' = smoothstep(0.05, 0.9, e)) -> overall level, spectral tilt (bass-heavy when calm, flat
when busy), active bin count, noise evolution rate, release time, glow gain and length, sway amplitude, ring
glow.  pulse (k = pulse * (0.35 + 0.65 e)) -> the kick cascade into the bins (amplitude only), plus gains on the
ring glow, inner haze, bar glow and bloom.  speed scales the noise evolution, the sway and the colour drift.
Colour: pal(mix(bin index, bar height, colour_by) * period + drift) folded to its bright side.
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

NB = 256                       # spectrum bins (one block)
ND = 16                        # delayed pulse samples
D_STEP = 0.0125                # s between delayed samples (0 .. 187.5 ms)
NP = 80

# P layout
#  0..11 palette | 12 period | 13 drift | 14 kappa | 15 half arc length | 16 vM (arc apex height)
#  17 Nh (bars per mirrored half) | 18 symmetry | 19 height scale | 20 bar width fraction | 21 quantised
#  22 round | 23 cos sway | 24 sin sway | 25 peak caps | 26 reflection | 27 colour_by | 28 wave mix
#  29 glow gain | 30 glow length (along) | 31 ring gain | 32 haze gain | 33 intensity | 34 ring radius
#  35 segment length / pitch | 36 ghost | 37 pulse flash | 38 reflection length | 39 wave inner ratio
#  40 wave line width | 41 bloom gain
#  48 dt | 49 T1 int | 50 T1 frac | 51 T2 int | 52 T2 frac | 53 level | 54 tilt exponent | 55 active edge
#  56 kick gain | 57 release tau | 58 hold | 59 gravity | 60 seed | 64..79 delayed pulse
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NB %(nb)d
__device__ __forceinline__ float3 bpal(float t, const float* __restrict__ PAL) {
    // the palette folded to its bright side (a dark phase swings to the opposite colour), max channel >= ~0.8
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.55f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
// value noise: continuous x, integer time lattice (host-split int/frac so it never loses precision)
__device__ __forceinline__ float vn(float x, unsigned int ti, float tf, unsigned int seed) {
    const float fx = floorf(x);
    const int ix = (int)fx;
    float tx = x - fx;
    tx = tx * tx * (3.f - 2.f * tx);
    const float ty = tf * tf * (3.f - 2.f * tf);
    const float a = hash1((unsigned int)ix, ti, seed), b = hash1((unsigned int)(ix + 1), ti, seed);
    const float c = hash1((unsigned int)ix, ti + 1u, seed), d = hash1((unsigned int)(ix + 1), ti + 1u, seed);
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
__device__ __forceinline__ float fold1(float x) { x = fmodf(fabsf(x), 2.f); return x > 1.f ? 2.f - x : x; }
__device__ __forceinline__ float2 spec(const float4* __restrict__ S, float f) {
    const float fi = clamp01(f) * (NB - 1);
    const int i0 = min((int)fi, NB - 2);
    const float t = fi - i0;
    const float4 a = S[i0], b = S[i0 + 1];
    return make_float2(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t);
}
// height, peak and colour index of the bar centred at mirrored arc position ac (0 = apex, 1 = arc end)
__device__ __forceinline__ float3 barlook(const float4* __restrict__ S, const float* __restrict__ P, float ac) {
    const float k = P[18], k0 = floorf(k);
    const float kw = smooth(0.15f, 0.85f, k - k0);
    const float f0 = fold1(ac * k0), f1 = fold1(ac * (k0 + 1.f));
    const float2 s0 = spec(S, f0), s1 = spec(S, f1);
    return make_float3(s0.x + (s1.x - s0.x) * kw, s0.y + (s1.y - s0.y) * kw, f0 + (f1 - f0) * kw);
}
__device__ __forceinline__ float sdbox(float ax, float ay) {
    const float mx = fmaxf(ax, 0.f), my = fmaxf(ay, 0.f);
    return sqrtf(mx * mx + my * my) + fminf(fmaxf(ax, ay), 0.f);
}

extern "C" __global__
void rs_state(float4* __restrict__ S, const float* __restrict__ P)
{
    const int i = threadIdx.x;
    if (i >= NB) return;
    const float dt = fminf(P[48], 0.1f);
    if (dt <= 0.f) return;
    const float f = (float)i / (float)(NB - 1);
    const unsigned int sd = (unsigned int)P[60];
    const float nb = vn(f * 7.f, (unsigned int)P[49], P[50], sd);
    const float nf = vn(i * 0.45f + 31.f, (unsigned int)P[51], P[52], sd + 101u);
    const float n = smooth(0.18f, 0.88f, 0.5f * nb + 0.5f * nf);
    const float tilt = powf(fmaxf(1.f - 0.82f * f, 1e-3f), P[54]);
    const float act = 0.12f + 0.88f * (1.f - smooth(P[55] - 0.14f, P[55] + 0.14f, f));
    float tgt = fmaxf(P[53] * tilt * (0.1f + 0.9f * n) * act, 0.022f + 0.02f * n);
    // kick: the pulse envelope read at a delay growing up the spectrum (bass first, cascading to the treble)
    const float di = f * 13.6f;
    const int d0 = min((int)di, %(nd)d - 2);
    const float kd = P[64 + d0] + (P[65 + d0] - P[64 + d0]) * (di - d0);
    tgt = fminf(tgt + kd * P[56] * __expf(-1.7f * f), 1.15f);
    float4 s = S[i];
    const int ns = min(12, max(1, (int)ceilf(dt * 120.f)));
    const float h = dt / ns;
    for (int k = 0; k < ns; ++k) {
        const float tau = tgt > s.x ? 0.022f : P[57];
        s.x += (tgt - s.x) * (1.f - __expf(-h / tau));
        if (s.x >= s.y) { s.y = s.x; s.z = P[58]; s.w = 0.f; }
        else if (s.z > 0.f) { s.z -= h; }
        else {
            s.w += P[59] * h;
            s.y -= s.w * h;
            if (s.y < s.x) { s.y = s.x; s.w = 0.f; }
        }
    }
    S[i] = s;
}

extern "C" __global__
void rs_draw(uchar4* __restrict__ out, const float4* __restrict__ S, const int W, const int H,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H, px = 1.f / hh;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (hh - (y + 0.5f)) / hh;      // y up
    const float* PAL = P;
    const float period = P[12], drift = P[13];
    // sway rotation about the centre, then arc coordinates: d = signed distance (outward +), s = arc length
    const float ur = P[23] * u - P[24] * v, vr = P[24] * u + P[23] * v;
    const float kap = P[14], halfL = P[15];
    const float qx = ur, qy = vr - P[16];
    const float kq = kap * qy + 1.f;
    const float d = (kap * (qx * qx + qy * qy) + 2.f * qy) / (sqrtf(kap * kap * qx * qx + kq * kq) + 1.f);
    const float s = kap > 1e-5f ? atan2f(kap * qx, kq) / kap : qx;
    const float aa = fabsf(s / halfL);
    const float Nh = P[17];
    const float pitch = halfL / Nh;
    const float aN = aa * Nh;
    const float gs = fmaxf(1.f + kap * d, 0.05f);
    const float hs = P[19];
    const float yy = fabsf(d);
    const float rg = d < 0.f ? P[26] * __expf(d / P[38]) : 1.f;                  // reflection gain
    const float endf = clamp01((1.f - aa) * Nh + 0.5f);                         // fade past the arc ends
    const float wm = P[28];
    const float flash = 1.f + P[37];

    // continuous envelope through the bar heights (waveform, bloom, baseline colour)
    const float j0 = floorf(aN), t = aN - j0, ts = t * t * (3.f - 2.f * t);
    const float3 b0 = barlook(S, P, fminf(j0 / Nh, 1.f)), b1 = barlook(S, P, fminf((j0 + 1.f) / Nh, 1.f));
    const float Hc = (b0.x + (b1.x - b0.x) * ts) * hs;
    const float Pc = (b0.y + (b1.y - b0.y) * ts) * hs;
    const float fc = b0.z + (b1.z - b0.z) * ts;
    const float3 cc = bpal(fc * period + drift, PAL);

    float3 col = make_float3(0.f, 0.f, 0.f);
    // ---------------------------------------------------------------- bars
    if (wm < 0.999f && rg > 0.002f) {
        const float wf = P[20], qw = P[21], rw = P[22];
        const float wh = 0.5f * wf * pitch;
        const float segL = fmaxf(pitch * P[35], 3.f * px);
        const float gwx = 0.32f * pitch, gwy = P[30];
        const float capth = fmaxf(0.2f * pitch, 1.5f * px);
        const float rdot = fminf(wh, 0.4f * segL);
        const float cby = P[27];
        const float sy = yy / segL, si = floorf(sy), yc = (si + 0.5f) * segL;
        float3 acc = make_float3(0.f, 0.f, 0.f);
        const int jc = (int)floorf(aN + 0.5f);
        for (int jn = jc - 1; jn <= jc + 1; ++jn) {
            const float jm = fabsf((float)jn);
            if (jm > Nh * 1.0001f) continue;                                     // past the arc end
            const float3 b = barlook(S, P, jm / Nh);
            const float h = fmaxf(b.x * hs, 0.5f * px), pk = b.y * hs;
            const float dl = aN - (float)jn;
            const float wwin = smooth(1.5f, 1.0f, fabsf(dl));
            const float perp = dl * pitch * gs;
            const float ax = fabsf(perp) - wh;
            const float ay = fabsf(yy - 0.5f * h) - 0.5f * h;
            const float sdr = sdbox(ax, ay);
            // quantised cells: LED rects or round dots, lit up to the bar height (top cell partially), ghosts above
            const float sdq_r = sdbox(ax, fabsf(yy - yc) - 0.31f * segL);
            const float sdq_c = sqrtf(perp * perp + (yy - yc) * (yy - yc)) - rdot;
            const float sdq = sdq_r + (sdq_c - sdq_r) * rw;
            const float lit = clamp01((h - si * segL) / segL);
            const float ghost = si * segL < hs ? P[36] : 0.f;
            const float covq = clamp01(0.5f - sdq / px) * (lit + ghost * (1.f - lit));
            const float covc = clamp01(0.5f - sdr / px);
            const float cov = covc + (covq - covc) * qw;
            const float body = (0.78f + 0.42f * clamp01(yy / h)) * flash;
            const float3 bc = bpal((b.z + (clamp01(yy / hs) - b.z) * cby) * period + drift, PAL);
            // peak cap (rect or dot)
            const float yk = pk + 0.6f * capth + px;
            const float sdk_r = sdbox(ax, fabsf(yy - yk) - 0.5f * capth);
            const float sdk_c = sqrtf(perp * perp + (yy - yk) * (yy - yk)) - fmaxf(rdot, 0.5f * capth);
            const float capc = clamp01(0.5f - (sdk_r + (sdk_c - sdk_r) * rw) / px) * P[25];
            // anisotropic glow from the whole bar, windowed to the 3-bar neighbourhood
            const float gx = fmaxf(ax, 0.f) / gwx, gy = fmaxf(ay, 0.f) / gwy;
            const float g = __expf(-sqrtf(gx * gx + gy * gy)) * wwin * P[29];
            const float kb = cov * body + g;
            acc.x += kb * bc.x + capc * (0.45f + 0.6f * bc.x);
            acc.y += kb * bc.y + capc * (0.45f + 0.6f * bc.y);
            acc.z += kb * bc.z + capc * (0.45f + 0.6f * bc.z);
        }
        // ambient bloom over the bar envelope
        const float bl = P[41] * (yy < Hc ? 0.35f + 0.65f * yy / fmaxf(Hc, 1e-4f) : __expf(-(yy - Hc) / (1.6f * gwy)));
        acc.x += bl * cc.x * endf; acc.y += bl * cc.y * endf; acc.z += bl * cc.z * endf;
        const float kb = rg * (1.f - wm);
        col.x += kb * acc.x; col.y += kb * acc.y; col.z += kb * acc.z;
    }
    // ---------------------------------------------------------------- waveform ring
    if (wm > 0.001f) {
        const float slope = (b1.x - b0.x) * hs * 6.f * t * (1.f - t) / (pitch * gs);
        const float ir = P[39];
        const float dout = (d - Hc) * rsqrtf(1.f + slope * slope);
        const float Hin = -ir * Hc;
        const float din = (d - Hin) * rsqrtf(1.f + ir * ir * slope * slope);
        const float lw = P[40];
        const float line = __expf(-(dout * dout) / (lw * lw)) + 0.8f * __expf(-(din * din) / (lw * lw));
        const float glw = P[29] * (__expf(-fabsf(dout) / (2.f * P[30])) + 0.7f * __expf(-fabsf(din) / (2.f * P[30])));
        const float inside = clamp01(0.5f - dout / px) * clamp01(0.5f + din / px);
        const float fill = inside * (0.1f + 0.38f * clamp01((d - Hin) / fmaxf(Hc - Hin, 1e-4f)));
        const float dp = (d - Pc - 2.f * lw);
        const float pl = __expf(-(dp * dp) / (0.36f * lw * lw)) * P[25] * 0.7f;
        const float kw = wm * endf;
        const float m = (line * flash + glw + fill) * kw;
        col.x += m * cc.x + pl * kw * (0.5f + 0.5f * cc.x);
        col.y += m * cc.y + pl * kw * (0.5f + 0.5f * cc.y);
        col.z += m * cc.z + pl * kw * (0.5f + 0.5f * cc.z);
    }
    // ---------------------------------------------------------------- baseline ring, inner haze, background
    {
        const float rlw = 0.0028f + 1.2f * px;
        const float ring = P[31] * endf * (__expf(-(d * d) / (rlw * rlw)) + 0.3f * __expf(-yy / 0.022f))
                           * (1.f - 0.6f * wm);
        const float3 rc = bpal(fc * period + drift + 0.08f, PAL);
        col.x += ring * (0.25f + 0.85f * rc.x);
        col.y += ring * (0.25f + 0.85f * rc.y);
        col.z += ring * (0.25f + 0.85f * rc.z);
        const float hz = P[32] * (d < 0.f ? __expf(d / (0.55f * P[34])) : __expf(-d / 0.07f));
        const float3 hc = bpal(drift + 0.3f, PAL);
        col.x += hz * hc.x; col.y += hz * hc.y; col.z += hz * hc.z;
        const float3 bg = pal(0.3f * drift + 0.55f, PAL);
        const float rr2 = u * u + v * v;
        const float bk = 0.012f + 0.028f * __expf(-0.6f * rr2);
        col.x += bk * bg.x; col.y += bk * bg.y; col.z += bk * bg.z;
    }
    // soft shoulder, gain, vignette
    const float asp = (float)W / (float)H;
    const float vg = (1.f - 0.25f * (u * u + v * v) / (1.f + asp * asp)) * 255.f * P[33];
    float c3[3] = {col.x, col.y, col.z};
    #pragma unroll
    for (int k = 0; k < 3; ++k) {
        const float c = c3[k];
        c3[k] = c < 0.75f ? c : 0.75f + 0.25f * (1.f - __expf(-(c - 0.75f) / 0.25f));
    }
    out[y * W + x] = pack(make_float4(c3[0] * vg, c3[1] * vg, c3[2] * vg, 255.f));
}
""" % {"nb": NB, "nd": ND}
_OPTIONS = ("--std=c++14", "--use_fast_math")

# bar_style anchors: blocks, LED segments, lines, dots
_ST_WF = (0.78, 0.8, 0.2, 0.72)       # bar width / pitch
_ST_Q = (0.0, 1.0, 0.0, 1.0)          # quantised cells
_ST_R = (0.0, 0.0, 0.0, 1.0)          # round cells
_ST_SEG = (0.42, 0.42, 0.95, 0.95)    # cell length / pitch


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


def _style(st: float, anchors: tuple) -> float:
    st = min(max(st, 0.0), 3.0)
    i0 = min(int(math.floor(st)), 2)
    w = _ss(0.1, 0.9, st - i0)
    return anchors[i0] + (anchors[i0 + 1] - anchors[i0]) * w


class RadialSpectrum(Source):
    name = "radial_spectrum"
    label = "Radial spectrum"
    description = ("An audio-visualiser look with no audio: a ring of glowing spectrum bars with falling peak caps "
                   "that kick on the beat, unrolling into a mirrored floor analyser and curling into a waveform ring.")
    tags = {"family": "light", "dims": "2d", "toolkit": "none", "technique": ["analytic", "light"],
            "moods": ["euphoric", "bright", "playful"], "style": "lively", "energy": [0.3, 1.0]}
    params = _order({
        "layout": num(0.0, 2.0, 0.0, "Layout (radial -> linear -> waveform ring)", tier="regional", basic=True),
        "bar_style": num(0.0, 3.0, 0.0, "Bars (blocks -> LED -> lines -> dots)", tier="regional", basic=True),
        "bar_count": num(64.0, 256.0, 128.0, "Bar count", step=2.0, tier="regional"),
        "ring_radius": num(0.2, 0.7, 0.4, "Ring radius", tier="regional"),
        "symmetry": num(1.0, 6.0, 2.0, "Symmetry (spectrum folds)", step=0.05, tier="regional"),
        "peak_caps": num(0.0, 1.0, 0.85, "Peak caps", tier="regional"),
        "reflection": num(0.0, 1.0, 0.35, "Reflection", tier="regional"),
        "colour_by": num(0.0, 1.0, 0.25, "Colour (bin -> height)", tier="regional"),
        **standard_scene_params(palette="neon", render_scale=1.0, period=0.8, drift=0.03),
    }, ["layout", "bar_style", "bar_count", "ring_radius", "symmetry", "peak_caps", "reflection", "colour_by",
        "energy", "pulse", "palette", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        self.S = cp.zeros((NB, 4), cp.float32)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_state = mod.get_function("rs_state")
        self.k_draw = mod.get_function("rs_draw")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.seed = int(rng.integers(0, 1 << 16))
        self.t1 = float(rng.uniform(0, 64))
        self.t2 = float(rng.uniform(0, 64))
        self.sway = float(rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.clock = 0.0
        self.hist: list[tuple[float, float]] = []
        self.pal = np.asarray(palette_coeffs(params["palette"]), np.float64)
        # settle the bars so the scene arrives formed
        for _ in range(12):
            self._frame(params, 1.0 / 30.0, prime=True)

    def _delayed(self, tq: float) -> float:
        h = self.hist
        if not h:
            return 0.0
        if tq <= h[0][0]:
            return h[0][1]
        for i in range(len(h) - 1, 0, -1):
            t0, v0 = h[i - 1]
            t1, v1 = h[i]
            if t0 <= tq <= t1:
                return v0 + (v1 - v0) * ((tq - t0) / (t1 - t0) if t1 > t0 else 1.0)
        return h[-1][1]

    def _frame(self, p: dict, dt: float, prime: bool = False) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = 0.0 if prime else min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = _ss(0.05, 0.9, e)
        k = pulse * (0.35 + 0.65 * e)

        # host-integrated phases
        if not prime:
            self.t1 += (0.45 + 0.9 * ep) * spd * dt
            self.t2 += (0.9 + 1.8 * ep) * spd * dt
            self.sway = _wrap(self.sway + 2 * math.pi * 0.035 * (0.5 + ep) * spd * dt, 2 * math.pi)
            self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
            self.clock += dt
            if dt > 0 or not self.hist:
                self.hist.append((self.clock, pulse))
            else:
                self.hist[-1] = (self.clock, pulse)
            while len(self.hist) > 2 and self.hist[1][0] < self.clock - 0.4:
                self.hist.pop(0)

        # palette (session palette wins; eased)
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / 1.5))

        # layout: radial (0) -> linear (1) -> waveform ring (2)
        L = min(max(float(p["layout"]), 0.0), 2.0)
        b = _ss(0.0, 1.0, L) if L <= 1.0 else _ss(0.0, 1.0, 2.0 - L)
        wm = _ss(1.2, 1.9, L)
        R = float(p["ring_radius"])
        asp = self.w / self.h
        w_lin = 2.0 * asp * 0.9
        Lt = 2 * math.pi * R * (1.0 - b) + w_lin * b
        kap = 2 * math.pi * (1.0 - b) / Lt
        v_lin = -0.3
        hs_r = min(max(0.97 - R, 0.15), 0.6)
        st = float(p["bar_style"])
        wf = _style(st, _ST_WF)

        P = self.Ph
        P[0:12] = self.pal
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = kap
        P[15] = 0.5 * Lt
        P[16] = R * (1.0 - b) + v_lin * b
        P[17] = 0.5 * float(p["bar_count"])
        P[18] = min(max(float(p["symmetry"]), 1.0), 6.0)
        P[19] = hs_r + (1.1 - hs_r) * b
        P[20] = wf
        P[21] = _style(st, _ST_Q)
        P[22] = _style(st, _ST_R)
        a = (0.12 + 0.25 * ep) * math.sin(self.sway) * (1.0 - b)
        P[23], P[24] = math.cos(a), math.sin(a)
        P[25] = float(p["peak_caps"])
        P[26] = float(p["reflection"])
        P[27] = float(p["colour_by"])
        P[28] = wm
        P[29] = (0.3 + 0.45 * ep) * (1.0 + 0.6 * k) * (1.0 + 1.2 * max(0.78 - wf, 0.0))
        P[30] = 0.03 + 0.05 * ep
        P[31] = (0.55 + 0.25 * ep) * (1.0 + 0.9 * k)
        P[32] = 0.1 * (0.6 + 0.4 * ep) * (1.0 + 1.5 * k) * (1.0 - b)
        P[33] = float(p["intensity"])
        P[34] = R
        P[35] = _style(st, _ST_SEG)
        P[36] = 0.06
        P[37] = 0.25 * k
        P[38] = 0.4 * R + (0.32 - 0.4 * R) * b
        P[39] = 0.4
        P[40] = max(0.004 + 0.003 * ep, 1.2 / (0.5 * self.h))
        P[41] = 0.1 * (0.5 + 0.5 * ep) * (1.0 + 0.5 * k)
        # spectrum state
        P[48] = dt
        t1i = math.floor(self.t1)
        t2i = math.floor(self.t2)
        P[49], P[50] = float(int(t1i) % (1 << 20)), self.t1 - t1i
        P[51], P[52] = float(int(t2i) % (1 << 20)), self.t2 - t2i
        P[53] = 0.3 + 0.7 * ep ** 0.9
        P[54] = 2.4 - 1.9 * ep
        P[55] = 0.38 + 0.8 * ep ** 1.2
        P[56] = 0.95 * (0.6 + 0.4 * ep)
        P[57] = 0.32 - 0.14 * ep
        P[58] = 0.35
        P[59] = 2.5
        P[60] = float(self.seed)
        for i in range(ND):
            P[64 + i] = self._delayed(self.clock - i * D_STEP)
        self.P.set(P)
        self.k_state((1, 1, 1), (NB, 1, 1), (self.S, self.P))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._frame(p, dt)
        tgt = out if self.direct else self.buf
        launch_2d(self.k_draw, self.w, self.h, (tgt, self.S, np.int32(self.w), np.int32(self.h), self.P))
        if not self.direct:
            present(self.ctx, self.buf, out)
