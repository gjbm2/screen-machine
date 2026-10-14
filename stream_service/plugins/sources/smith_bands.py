"""Möbius ribbons: stacked colour bands, each a half-plane bent through a rotating Möbius (Smith-chart) map.

Source: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/pretty-sweet-colors/fragment-0.hlsl
        (also https://www.shadertoy.com/view/NlXBDn, "Pretty sweet colors")
Author: Mårten Rånge (mrange)
License: CC0-1.0   (the shader file states "// CC0: Pretty sweet colors"; the gallery repository's LICENSE is MIT,
                    "Copyright (c) 2022 Mårten Rånge",
                    https://github.com/mrange/windows-terminal-shader-gallery/blob/main/LICENSE)
license_class: permissive
Ported from HLSL by smfx (ideas/changes: see below)

Helpers: the original's hsv2rgb (sam hocevar, "License: WTFPL", https://stackoverflow.com/a/17897228/418488) is
not used; colours come from the shared cosine palette.  The original's sRGB encoder (nmz, "License: Unknown",
https://www.shadertoy.com/view/NdfyRM) is NOT copied: srgb_lin()/lin_srgb() below are written from scratch from
the IEC 61966-2-1 transfer function (License: MIT, smfx).

The algorithm (faithful to the original): for each layer i, map the pixel z through the Smith transform
w = (1+z)/(1-z), rotate w by a per-layer angle (0.1 i + a global phase) plus a y-dependent "shear" wobble
0.2 (i/8) sin(3 y + 2.1 i + phase), map back z' = (w-1)/(w+1), and test the half-plane
dot(z', -(1,1)/sqrt2) + (0.2 i - 0.3) < 0.  The map is an elliptic Möbius transform, so the straight half-plane
edges become circular arcs sweeping around the fixed points z = +-1 as the phase turns: diagonal ribbons, then
discs, then nested blobs.  Each layer darkens what lies under it near its edge (the original's contact shadow,
col *= 1 - 0.67 exp(-20 d), d in screen units), then covers it, anti-aliased over about one pixel.

smfx changes: the per-layer rotation spread, layer spacing, shear frequency and shadow length are knobs; a
continuous layer count (the top band fades in); an offset drop shadow (a third map evaluation per layer, falling
down-right) on top of the contact shadow; the composite map evaluated as one complex Möbius N/D (no infinity at
z = 1) with an analytic screen-space gradient for the anti-aliasing (the original used a finite difference);
a per-layer sway so the bands slide over one another; colours from the shared cosine palette, pushed to the
original's saturated look (linear-light HSV saturation boost) and its dark-to-bright stack (tone_ramp); all phases
integrated on the host.

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> rotation rate x(0.4+1.2e'), shear amplitude (floor 60%), sway
amplitude, layer spacing spread and rotation spread (slewed over 1.5 s), shadow depth.  pulse (k = pulse
(0.3+0.7e) x beat_kick) -> the stack breathes: shadow depth and length swell (the ribbons lift off the page), a
small layer-spacing swell and a lurch of the rotation rate (rates/amplitudes only, never the phase).  speed
scales every phase; palette index = (layer / top layer) * period + drift; intensity is output gain.
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

MAX_LAYERS = 12          # base + 11 bands
TAU = 2.0 * math.pi
_OPTIONS = ("--std=c++14", "--use_fast_math")

# P layout: 0 layer count L | 2 contact shadow length | 3 shadow depth | 4,5 drop-shadow offset | 6 shear freq
#   7 1/view_scale | 8 period | 9 drift | 10 gain | 11 vignette | 12 tone ramp | 13 saturation gamma | 14 base value
#   15 drop-shadow length | 16..27 palette
#   32.. per band i (1..11), stride 8: angle, shear amp, plane offset, alpha, cos(shear phase), stack position t,
#        (unused), sin(shear phase)
_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ __forceinline__ float srgb_lin(float c) {     // IEC 61966-2-1 decode
    return c <= 0.04045f ? c * (1.f / 12.92f) : __powf((c + 0.055f) * (1.f / 1.055f), 2.4f);
}
__device__ __forceinline__ float lin_srgb(float c) {     // IEC 61966-2-1 encode
    c = fmaxf(c, 0.f);
    return c <= 0.0031308f ? 12.92f * c : 1.055f * __powf(c, 1.f / 2.4f) - 0.055f;
}
// z -> (w' - 1)/(w' + 1), w' = e^{i a} w, w = (1 + z)/(1 - z), a = th + amp * sin(freq * z.y + ph)
// (sf, cf) = sincos(freq * z.y), per pixel; (cp, sp) = cos/sin(ph), per band: sin(freq z.y + ph) = sf cp + cf sp
__device__ __forceinline__ float2 mob(float2 z, float th, float amp, float sf, float cf, float cp, float sp) {
    float sn, c;
    __sincosf(th + amp * (sf * cp + cf * sp), &sn, &c);
    const float ux = 1.f + z.x, uy = z.y;
    const float ax = c * ux - sn * uy, ay = sn * ux + c * uy;      // e^{ia} (1 + z)
    const float nx = ax - 1.f + z.x, ny = ay + z.y;                // N = e^{ia}(1+z) - (1-z)
    const float dx = ax + 1.f - z.x, dy = ay - z.y;                // D = e^{ia}(1+z) + (1-z)
    const float inv = 1.f / fmaxf(dx * dx + dy * dy, 1e-20f);
    return make_float2((nx * dx + ny * dy) * inv, (ny * dx - nx * dy) * inv);
}
// mob() plus the gradient of d = n.z' (n = -(1,1)/sqrt2), analytic: for fixed a the map is the Mobius
// ((A+1)z + (A-1))/((A-1)z + (A+1)), A = e^{ia}, with f'(z) = 4A/D^2; a(y) adds dz'/da * a_y, dz'/da = 2iA(1-z^2)/D^2.
// Returns z' and writes |grad d| (per unit of z).
__device__ __forceinline__ float2 mob_g(float2 z, float th, float amp, float freq, float sf, float cf, float cp,
                                       float sp, float& gl) {
    float sn, c;
    const float sy = sf * cp + cf * sp, cy = cf * cp - sf * sp;
    __sincosf(th + amp * sy, &sn, &c);
    const float ux = 1.f + z.x, uy = z.y;
    const float ax = c * ux - sn * uy, ay = sn * ux + c * uy;
    const float nx = ax - 1.f + z.x, ny = ay + z.y;
    const float dx = ax + 1.f - z.x, dy = ay - z.y;
    const float inv = 1.f / fmaxf(dx * dx + dy * dy, 1e-20f);
    const float rx = (dx * dx - dy * dy) * inv * inv, ry = -2.f * dx * dy * inv * inv;     // 1/D^2
    const float fx = 4.f * (c * rx - sn * ry), fy = 4.f * (c * ry + sn * rx);           // f' = 4A/D^2
    const float wx = 1.f - (z.x * z.x - z.y * z.y), wy = -2.f * z.x * z.y;              // 1 - z^2
    const float iax = -2.f * sn, iay = 2.f * c;                                         // 2iA
    const float tx = iax * wx - iay * wy, ty = iax * wy + iay * wx;
    const float gax = tx * rx - ty * ry, gay = tx * ry + ty * rx;                        // dz'/da
    const float ayk = amp * freq * cy;                                                  // da/dy
    const float zyx = -fy + gax * ayk, zyy = fx + gay * ayk;                            // dz'/dy = i f' + dz'/da a_y
    const float gx = -0.70710678f * (fx + fy), gy = -0.70710678f * (zyx + zyy);
    gl = sqrtf(gx * gx + gy * gy);
    return make_float2((nx * dx + ny * dy) * inv, (ny * dx - nx * dy) * inv);
}
// Layer colour (linear light): palette hue, saturation boost (HSV, hue kept), then (tone ramp) the value set by
// the layer's place in the stack, dark base -> bright top, as in the original's colour list.
__device__ float3 layer_col(float t, const float* __restrict__ P) {
    const float3 s = pal(t * P[8] + P[9], P + 16);
    float r = srgb_lin(s.x), g = srgb_lin(s.y), b = srgb_lin(s.z);
    const float m = fmaxf(fmaxf(r, g), fmaxf(b, 1e-4f));
    const float s0 = 1.f - fminf(fminf(r, g), b) / m;                 // HSV saturation, pushed to 1 - (1 - s)^gamma
    const float f = s0 > 1e-4f ? (1.f - __powf(1.f - s0, P[13])) / s0 : P[13];
    r = m - (m - r) * f; g = m - (m - g) * f; b = m - (m - b) * f;   // keeps hue and the max channel
    const float v = P[14] + (1.f - P[14]) * __powf(fmaxf(t, 0.f), 1.6f);
    const float k = 1.f + (v / m - 1.f) * P[12];
    return make_float3(fminf(r * k, 1.f), fminf(g * k, 1.f), fminf(b * k, 1.f));
}

extern "C" __global__
void smith_bands(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    __shared__ float3 LC[12];                     // per-layer linear colours, evaluated once per block
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    if (tid < 12) LC[tid] = layer_col(tid == 0 ? 0.f : P[32 + 8 * (tid - 1) + 5], P);
    __syncthreads();
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float sx = (x + 0.5f - 0.5f * W) / hh, sy = -(y + 0.5f - hh) / hh;     // screen, y up, height 2
    const float isc = P[7];
    const float2 p = make_float2(sx * isc, sy * isc);
    const float2 ps = make_float2((sx - P[4]) * isc, (sy - P[5]) * isc);
    const float depth = P[3], freq = P[6];
    const float icontact = 1.f / P[2], idrop = 1.f / P[15];
    float sf, cf, sfs, cfs;
    __sincosf(freq * p.y, &sf, &cf);
    __sincosf(freq * ps.y, &sfs, &cfs);
    const float aa = 1.6f / H;                    // half the anti-alias ramp: ~0.8 px each side
    float3 col = LC[0];                           // base layer
    #pragma unroll 1
    for (int i = 1; i < 12; ++i) {
        const float* B = P + 32 + 8 * (i - 1);
        const float a = B[3];
        if (a <= 0.f) break;
        float gl;
        const float2 q = mob_g(p, B[0], B[1], freq, sf, cf, B[4], B[7], gl);
        const float J = fmaxf(gl * isc, 1e-6f);                                    // |grad d| per screen unit
        const float ds = (-0.70710678f * (q.x + q.y) + B[2]) / J;                 // screen-space distance
        const float cov = a * smooth(aa, -aa, ds);
        if (cov < 0.999f) {                     // shadows only matter where this band does not cover
            const float2 qs = mob(ps, B[0], B[1], sfs, cfs, B[4], B[7]);
            const float dss = (-0.70710678f * (qs.x + qs.y) + B[2]) / J;          // drop-shadow (offset) distance
            const float occ = fmaxf(__expf(-fmaxf(ds, 0.f) * icontact), 0.85f * __expf(-fmaxf(dss, 0.f) * idrop));
            const float k = 1.f - depth * occ * a;
            col.x *= k; col.y *= k; col.z *= k;
        }
        if (cov > 0.f) {
            const float3 bc = LC[i];
            col.x += (bc.x - col.x) * cov; col.y += (bc.y - col.y) * cov; col.z += (bc.z - col.z) * cov;
        }
    }
    // vignette (the original's smoothstep(1.8, 0.5, |uv|), blended by P[11])
    const float ux = 2.f * (x + 0.5f) / W - 1.f, uy = 2.f * (y + 0.5f) / H - 1.f;
    const float v = 1.f - P[11] * (1.f - smooth(1.8f, 0.5f, sqrtf(ux * ux + uy * uy)));
    const float g = P[10] * v;
    out[y * W + x] = make_uchar4((unsigned char)(255.f * fminf(lin_srgb(col.x * g), 1.f) + 0.5f),
                                 (unsigned char)(255.f * fminf(lin_srgb(col.y * g), 1.f) + 0.5f),
                                 (unsigned char)(255.f * fminf(lin_srgb(col.z * g), 1.f) + 0.5f), 255);
}
"""


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class SmithBands(Source):
    name = "smith_bands"
    label = "Möbius ribbons"
    description = "Stacked paper-cut colour bands bent through rotating Möbius (Smith-chart) maps, with soft shadows."
    params = {
        "layer_count": num(4.0, 12.0, 8.0, "Layers (top one fades in)", step=0.1, basic=True),
        "mobius_spread": num(0.0, 1.0, 0.2, "Twist between layers", basic=True),
        "layer_spacing": num(0.08, 0.4, 0.2, "Layer spacing"),
        "shear_frequency": num(0.0, 8.0, 3.0, "Ripple frequency"),
        "shadow_softness": num(0.0, 1.0, 0.3, "Shadow softness / lift"),
        "tone_ramp": num(0.0, 1.0, 0.85, "Dark-to-bright stack"),
        "view_scale": num(0.5, 2.0, 1.0, "Ribbon scale", unit="×"),
        "beat_kick": num(0.0, 2.0, 1.0, "Beat breathing", unit="×"),
        "flow_speed": num(0.0, 4.0, 1.0, "Flow speed", unit="×", tier="realtime", bind="rate"),
        **standard_scene_params(palette="dusk", render_scale=0.75, period=0.6, drift=0.008),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTIONS).get_function("smith_bands")
        self.P = cp.zeros(32 + 8 * (MAX_LAYERS - 1), cp.float32)
        self.Ph = np.zeros(32 + 8 * (MAX_LAYERS - 1), np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.rot = float(rng.uniform(0.0, TAU))     # Möbius rotation phase (the original's 0.2 t)
        self.shp = float(rng.uniform(0.0, TAU))     # shear phase
        self.sw = float(rng.uniform(0.0, TAU))      # per-layer sway phase (layers slide over one another)
        self.drift = 0.5                            # dusk from 0.5 runs navy -> violet -> pink (the original's)
        self.es = None                              # slewed e' for geometry
        self.ks = 0.0                               # attack-smoothed beat

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ep = _ss(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.es = ep if self.es is None else self.es + (ep - self.es) * a
        es = self.es
        # beat: soften the envelope's instant attack (~70 ms) so swells never step; the release follows the envelope
        kt = pulse * (0.3 + 0.7 * e) * float(p["beat_kick"])
        if dt <= 0 or kt < self.ks:
            self.ks = kt
        else:
            self.ks += (kt - self.ks) * (1.0 - math.exp(-dt / 0.07))
        k = self.ks
        # ---- phases (rates only; the beat lurches the rate)
        fr = (0.4 + 1.2 * ep)
        self.rot = _wrap(self.rot + 0.2 * float(p["flow_speed"]) * fr * (1.0 + 0.8 * k) * spd * dt, TAU)
        self.shp = _wrap(self.shp + 0.25 * fr * (1.0 + 0.4 * k) * spd * dt, TAU)
        self.sw = _wrap(self.sw + 0.3 * fr * spd * dt, TAU)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)
        # ---- amplitudes
        shear = 0.2 * (0.6 + 0.75 * es)
        spacing = float(p["layer_spacing"]) * (0.92 + 0.16 * es) * (1.0 + 0.08 * k)
        spread = 0.5 * float(p["mobius_spread"]) * (0.85 + 0.3 * es)
        sway = 0.1 * (0.5 + 0.9 * es)
        sk = float(p["shadow_softness"])
        lc = (0.012 + 0.13 * sk) * (1.0 + 0.4 * k)                    # contact shadow length (original 0.05)
        depth = min(0.9, (0.5 + 0.25 * es) * (1.0 + 0.35 * k))       # original 0.67
        L = float(p["layer_count"])
        period = float(p["palette_period"])
        Ph = self.Ph
        Ph[:] = 0.0
        Ph[0] = L
        Ph[2] = lc
        Ph[3] = depth
        so = (0.004 + 0.06 * sk) * (1.0 + 0.6 * k) / math.sqrt(2.0)   # drop shadow falls down-right, lifts on the beat
        Ph[4], Ph[5] = so, -so
        Ph[6] = float(p["shear_frequency"])
        Ph[7] = 1.0 / float(p["view_scale"])
        Ph[8] = period
        Ph[9] = self.drift
        Ph[10] = float(p["intensity"])
        Ph[11] = 0.65
        Ph[12] = float(p["tone_ramp"])
        Ph[13] = 2.0
        Ph[14] = 0.07
        Ph[15] = 1.3 * lc
        Ph[16:28] = palette_coeffs(p["palette"])
        top = max(L - 1.0, 1.0)
        for i in range(1, MAX_LAYERS):
            o = 32 + 8 * (i - 1)
            Ph[o + 0] = _wrap(-(spread * i + self.rot + sway * math.sin(self.sw + 1.7 * i)), TAU)
            Ph[o + 1] = shear * i / 8.0
            Ph[o + 2] = spacing * (i - 1.5)                    # the original's 0.2 i - 0.3: the base keeps its share
            Ph[o + 3] = min(max(L - i, 0.0), 1.0)
            sph = 2.1 * i + self.shp
            Ph[o + 4], Ph[o + 7] = math.cos(sph), math.sin(sph)
            Ph[o + 5] = min(i / top, 1.0)                      # the stack spans one palette range at any layer count
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
