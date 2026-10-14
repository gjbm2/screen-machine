"""Infinite arcs: an endless logarithmic zoom through rings of spinning neon arc segments around a glowing core.

Source: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/infinite-arcs/fragment-0.hlsl
        (Shadertoy original: https://www.shadertoy.com/view/mlXGzs ; licence metadata:
        https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/infinite-arcs/metadata.json and
        https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/all_metadata.json)
Author: Mårten Rånge (mrange); arc distance after Inigo Quilez; HSV helper by Sam Hocevar
License: CC0-1.0 (core shader) + MIT (Inigo Quilez sdArc, https://iquilezles.org/articles/distfunctions2d/) +
         WTFPL (Sam Hocevar hsv2rgb, https://stackoverflow.com/a/17897228/418488)
         -- metadata.json "license-expressions": ["CC0-1.0", "MIT", "WTFPL"]; the gallery repo LICENSE is MIT,
         "Copyright (c) 2022 Mårten Rånge".  (The hsv2rgb helper is not used here: colour is the shared palette.)
license_class: permissive
Ported from HLSL/GLSL by smfx (ideas/changes: the shader's 'License: Unknown' fract(sin()) hash is rewritten as the
  lab's integer hash (hash_u, three hashes per ring); the forward/reverse/cell zoom helpers are rewritten as a
  host-integrated zoom phase wrapped modulo one ring (the pattern is log-periodic) plus an integer ring counter for
  the ring identity, so fp32 stays exact forever; iq's sdArc is evaluated in polar form (end-cap distance via the
  stable chord formula, no per-arc rotation); per-ring spin rates are quantised to 1/8 steps so the host spin phase
  wraps invisibly; colour via the shared cosine palette (index = ring position * period + drift) instead of HSV,
  composited in linear light with the original's sqrt encode; the core glow is tinted by the palette's brightest
  hue; the ring ratio, coverage, width and arcs-per-ring are continuous knobs (the next arc grows and fades in from
  the middle of the largest gap while the others spread out, SDF-unioned so outlines merge rather than cross); the
  nearer neighbouring ring is also evaluated so swollen arcs and halos never clip at ring borders; analytic
  box-filter AA in screen space and a pixel-footprint fade of sub-pixel rings near the core; optional translucent
  fill and a neon halo.)

Macros: energy (remapped e' = smoothstep(0.1, 0.85, e)) -> zoom rate and per-ring spin rates x(0.4 + 1.2 e'),
arc thickness and outline width (smoothstep, floor ~55 %), neon halo, core glow, and (slewed over ~2 s, e'^1.5)
a fraction of an extra arc per ring fading in.  pulse (k = pulse * (0.3 + 0.7 e)) -> a kick to the zoom *rate*
(an integrated forward lurch into the tunnel, gain zoom_kick) and a swell of arc thickness / brightness / core
glow (gain swell_kick, brightness at most +35 %).  speed multiplies the zoom, spin and colour-drift phases.
palette index = ring position (in ring units, continuous as rings fly out) * period + a per-ring hash + drift.
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

MAX_ARCS = 5
SPIN_WRAP = 16.0 * math.pi          # per-ring spin rates are j/8 (integer j): (j/8) * 16 pi = 2 pi j, an invisible wrap
DRIFT_WRAP = 20.0                   # every palette frequency c is a multiple of 0.05, so c * 20 is an integer: invisible
_OPTIONS = ("--std=c++14", "--use_fast_math")   # every trig/exp argument is small (phases wrapped on the host)

# P layout (floats)
#  0 zoom fract m | 1 ln(ring ratio) | 2 coverage | 3 arc half-thickness / ring half-width | 4 outline half-width / hw
#  5 arcs per ring (continuous) | 6 spin phase | 7 fill | 8 halo | 9 line gain | 10 core amp | 11 core falloff
#  12 period | 13 drift | 14 intensity | 15 vignette | 16..27 palette | 28 halo width / hw | 29..31 core colour (lin)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define TAU 6.2831853f
#define PI_F 3.14159265f
#define MAX_ARCS %(max_arcs)d

// Fraction of the pixel box [d - aa, d + aa] that lies inside the band [-hw, hw] (1D box-filter coverage).
__device__ __forceinline__ float boxcov(float d, float hw, float aa) {
    const float lo = fmaxf(d - aa, -hw), hi = fminf(d + aa, hw);
    return fmaxf(hi - lo, 0.f) / (2.f * aa);
}

// iq's sdArc in polar form: arc of radius mid, half-aperture `half` centred on angle th, half-thickness rb.
__device__ __forceinline__ float arc_d(const float dr, const float l0, const float mid, const float phi,
                                       const float th, const float half, const float rb) {
    float dl = phi - th;
    dl -= TAU * floorf((dl + PI_F) * (1.f / TAU));
    const float ex = fabsf(dl) - half;
    if (ex <= 0.f) return fabsf(dr) - rb;
    const float s = __sinf(0.5f * fminf(ex, PI_F));                  // distance to the end cap (stable chord form)
    return sqrtf(dr * dr + 4.f * l0 * mid * s * s) - rb;
}

// One ring (index n in zoom space): k = floor(N) arcs evenly spaced (unioned), plus the next arc growing and
// fading in from the middle of the largest gap while the others spread out (N = arcs per ring, continuous).
__device__ float3 ring_eval(const int n, const float l0, const float phi, const float z, const float aa,
                            const float pix, const unsigned int f, const unsigned int sh, const float* __restrict__ P)
{
    const float lnR = P[1];
    const float rn = __expf((float)n * lnR), rp = rn * __expf(-lnR);
    const float mid = 0.5f * (rn + rp), hw = 0.5f * (rn - rp);
    const float vis = smooth(2.f, 6.f, 2.f * hw * z / pix);        // fade rings narrower than a few pixels
    if (vis <= 0.f) return make_float3(0.f, 0.f, 0.f);
    // stable ring identity while it flies out; three integer hashes give all the per-ring randoms
    const unsigned int Ha = hash_u(((unsigned int)n - f) * 0x9E3779B1U ^ sh);   // modular: f may wrap freely
    const unsigned int Hb = hash_u(Ha ^ 0x85EBCA77U), Hc = hash_u(Hb + 0x632BE5ABU);
    const float h1 = (Ha & 0xFFFFu) * (1.f / 65536.f), h2 = (Ha >> 16) * (1.f / 65536.f);
    const float h3 = (Hb & 0xFFFFu) * (1.f / 65536.f), h4 = (Hb >> 16) * (1.f / 65536.f);
    const float c = P[2];
    const float spread = fminf(0.22f, 0.8f * fminf(c, 1.f - c));
    const float cov = c + (2.f * h1 - 1.f) * spread;
    const float u = 2.f * h4 - 1.f;
    const float j = copysignf(fmaxf(ceilf(8.f * sqrtf(fabsf(u))), 1.f), u);   // spin rate j/8, signed
    const float ang0 = TAU * h2 + j * 0.125f * P[6];
    const float N = P[5];
    const float kf = floorf(N), fa = N - kf;
    const int k = (int)kf;
    const float sp = TAU * (1.f / kf + (1.f / (kf + 1.f) - 1.f / kf) * fa);
    const float half = PI_F * cov / N;
    const float jw = (1.f - cov) * (TAU / N) * 0.8f / 256.f;
    const float rb = P[3] * hw, lt0 = P[4] * hw;
    const float dr = l0 - mid;
    float dm = 1e9f, dn = 1e9f;
    for (int i = 0; i < MAX_ARCS; ++i) {
        if (i >= k) break;
        const float jit = ((float)((Hc >> (6 * i)) & 0xFFu) - 128.f) * jw;
        dm = fminf(dm, arc_d(dr, l0, mid, phi, ang0 + (float)i * sp + jit, half, rb));
    }
    if (fa > 0.f && k < MAX_ARCS) {
        const float jit = ((float)((Hc >> (6 * k)) & 0xFFu) - 128.f) * jw;
        const float th = ang0 + TAU * ((kf - 0.5f) / kf + (kf / (kf + 1.f) - (kf - 0.5f) / kf) * fa) + jit;
        dn = arc_d(dr, l0, mid, phi, th, half * fa, rb * (0.4f + 0.6f * fa));
    }
    // union with the growing arc (so overlapping outlines merge instead of crossing); its fade weight blends in
    // smoothly across the union's bisector
    const float du = fminf(dm, dn);
    const float w = fa + (1.f - fa) * smooth(-0.3f * hw, 0.3f * hw, dn - dm);
    const float lt = lt0 * z, hwid = fmaxf(P[28] * hw * z, 1e-6f);
    const float d = du * z;                                          // screen units
    const float line = w * boxcov(d, lt, aa);
    const float fill = w * clamp01(0.5f - d / (2.f * aa));
    const float halo = w * __expf(-fmaxf(fabsf(d) - lt, 0.f) / hwid);
    const float t = P[12] * (0.07f * ((float)n + P[0] - 0.5f) + 0.18f * (h3 - 0.5f)) + P[13];
    const float3 col = pal(t, P + 16);
    const float v = vis * (P[9] * line + P[7] * 0.32f * fill + P[8] * halo);
    return make_float3(col.x * col.x * v, col.y * col.y * v, col.z * col.z * v);
}

extern "C" __global__
void infinite_arcs(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                   const unsigned int f, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float px = (x + 0.5f - 0.5f * W) / hh, py = -(y + 0.5f - hh) / hh;
    const float pix = 1.f / hh, aa = 0.7f * pix;
    const float dd = sqrtf(px * px + py * py) + 1e-6f;
    const float lnR = P[1];
    const float z = __expf(P[0] * lnR);                              // 1 .. ratio over one ring of zoom
    const float l0 = dd / z;
    const float uu = __logf(l0) / lnR;                               // continuous ring coordinate
    const float n0 = ceilf(uu);
    const int n = (int)n0;
    const int nb = (uu - (n0 - 1.f)) > 0.5f ? n + 1 : n - 1;         // the nearer neighbouring ring
    const float phi = atan2f(py, px);
    const unsigned int sh = hash_u(seed * 0x85EBCA77U + 0x27D4EB2DU);
    const float3 a = ring_eval(n, l0, phi, z, aa, pix, f, sh, P);
    const float3 b = ring_eval(nb, l0, phi, z, aa, pix, f, sh, P);
    const float fi = smooth(0.02f, 0.05f, dd);                       // original: smoothstep(0.25, 0.5, 10 * dd)
    float3 c = make_float3(fmaxf(a.x, b.x) * fi, fmaxf(a.y, b.y) * fi, fmaxf(a.z, b.z) * fi);
    const float cs = 2.f / (1.f + __expf(2.f * P[11] * dd));        // the core glow: 1 - tanh(k dd), tail tamed
    const float g = P[10] * cs * (0.3f + 0.7f * cs);
    c.x += g * P[29]; c.y += g * P[30]; c.z += g * P[31];
    const float ux = (x + 0.5f) / W * 2.f - 1.f, uy = (y + 0.5f) / H * 2.f - 1.f;
    const float vig = 1.f - P[15] * (1.f - smooth(1.5f, 0.5f, sqrtf(ux * ux + uy * uy)));
    const float k = 255.f * P[14] * vig;
    out[y * W + x] = make_uchar4((unsigned char)fminf(sqrtf(fmaxf(c.x, 0.f)) * k + 0.5f, 255.f),
                                 (unsigned char)fminf(sqrtf(fmaxf(c.y, 0.f)) * k + 0.5f, 255.f),
                                 (unsigned char)fminf(sqrtf(fmaxf(c.z, 0.f)) * k + 0.5f, 255.f), 255);
}
""" % {"max_arcs": MAX_ARCS}


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _pal_lin(coeffs: list[float], t: float) -> tuple[float, float, float]:
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    out = []
    for i in range(3):
        v = min(max(a[i] + b[i] * math.cos(2 * math.pi * (c[i] * t + d[i])), 0.0), 1.0)
        out.append(v * v)
    return tuple(out)


def _core_colour(coeffs: list[float], drift: float) -> tuple[float, float, float]:
    """The palette's brightest hue near the current drift (a soft-max over 8 samples, so it never jumps)."""
    acc, wsum = [0.0, 0.0, 0.0], 0.0
    for i in range(8):
        c = _pal_lin(coeffs, drift + i / 8.0)
        w = (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] + 1e-3) ** 4
        wsum += w
        for j in range(3):
            acc[j] += w * c[j]
    return tuple(v / wsum for v in acc)


class InfiniteArcs(Source):
    name = "infinite_arcs"
    label = "Infinite arcs"
    description = "An endless logarithmic zoom through rings of spinning neon arc segments around a glowing core."
    params = {
        "ring_ratio": num(1.1, 1.6, 1.25, "Ring spacing (ratio)", tier="regional", basic=True),
        "arc_coverage": num(0.1, 0.9, 0.5, "Arc coverage (of each ring)", tier="regional", basic=True),
        "arc_width": num(0.05, 0.6, 0.45, "Arc width (of ring width)", tier="regional"),
        "arcs_per_ring": num(1.0, 4.0, 1.0, "Arcs per ring", step=0.05, tier="regional"),
        "core_glow": num(0.0, 2.0, 1.0, "Core glow", tier="regional"),
        "fill_mix": num(0.0, 1.0, 0.25, "Arc fill (outline -> solid)", tier="regional"),
        "zoom_kick": num(0.0, 6.0, 3.0, "Beat zoom lurch", tier="regional"),
        "swell_kick": num(0.0, 1.0, 0.6, "Beat arc swell", tier="regional"),
        "zoom_speed": num(-1.0, 2.0, 0.5, "Zoom speed", unit="rings/s", tier="realtime", bind="rate"),
        "spin_speed": num(0.0, 2.0, 0.8, "Arc spin speed", unit="rad/s", tier="realtime", bind="rate"),
        **standard_scene_params(palette="neon", render_scale=0.75, period=1.0, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTIONS).get_function("infinite_arcs")
        self.P = cp.zeros(32, cp.float32)
        self.Ph = np.zeros(32, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.zoom = float(rng.uniform(0.0, 1.0))      # ring units; fract -> kernel, floor -> ring identity
        self.f = 0                                    # integer part of the zoom (ring counter)
        self.spin = float(rng.uniform(0.0, SPIN_WRAP))
        self.drift = 0.0
        self.es = None                                # slewed energy for density

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        e1 = _ss(0.1, 0.85, e)                        # Maestro activity mostly sits in 0.2-0.75
        amp = _ss(0.0, 1.0, e1)
        if self.es is None:
            self.es = e1
        elif dt > 0:
            self.es += (e1 - self.es) * (1.0 - math.exp(-dt / 2.0))
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = pulse * (0.3 + 0.7 * e)
        spd = float(p["speed"])
        rate = 0.4 + 1.2 * e1

        # host-integrated phases (rates only, so nothing jumps)
        self.zoom += p["zoom_speed"] * rate * (1.0 + p["zoom_kick"] * k) * spd * dt
        fl = math.floor(self.zoom)
        if fl != 0:
            self.f += int(fl)                         # unbounded int; uploaded mod 2^32 (exact)
            self.zoom -= fl
        self.spin = _wrap(self.spin + p["spin_speed"] * rate * spd * dt, SPIN_WRAP)
        self.drift = _wrap(self.drift + p["drift_speed"] * spd * dt, DRIFT_WRAP)   # keeps __cosf args small

        sw = p["swell_kick"] * k
        thick = (0.55 + 0.9 * amp) * (1.0 + 0.5 * sw)
        lt = 0.1 * (0.7 + 0.6 * amp) * (1.0 + 0.4 * sw)
        rb = min(p["arc_width"] * thick, 0.88 - lt)     # always a gap between neighbouring rings
        n_arcs = min(max(p["arcs_per_ring"] + 0.6 * self.es ** 1.5, 1.0), MAX_ARCS - 0.001)
        cg = p["core_glow"]
        coeffs = palette_coeffs(p["palette"])
        core = [2.2 * (0.3 + 0.7 * v) for v in _core_colour(coeffs, self.drift)]

        Ph = self.Ph
        Ph[0] = self.zoom
        Ph[1] = math.log(max(p["ring_ratio"], 1.01))
        Ph[2] = p["arc_coverage"]
        Ph[3] = rb
        Ph[4] = lt
        Ph[5] = n_arcs
        Ph[6] = self.spin
        Ph[7] = p["fill_mix"]
        Ph[8] = (0.10 + 0.25 * amp) * (1.0 + 0.5 * sw)
        Ph[9] = 1.0 + 0.35 * sw
        Ph[10] = cg * (0.6 + 0.6 * amp) * (1.0 + 0.6 * sw)
        Ph[11] = 12.0 / (0.6 + 0.4 * cg)
        Ph[12] = p["palette_period"]
        Ph[13] = self.drift
        Ph[14] = p["intensity"]
        Ph[15] = 1.0
        Ph[16:28] = coeffs
        Ph[28] = 0.18 + 0.2 * (1.0 - rb / 0.88)      # halo width (narrower when arcs fill the rings)
        Ph[29:32] = core
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P,
                                           np.uint32(self.f & 0xFFFFFFFF), np.uint32(int(p["seed"]) & 0xFFFFFFFF)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
