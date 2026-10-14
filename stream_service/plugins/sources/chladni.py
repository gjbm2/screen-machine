"""Chladni plate: the nodal patterns of a vibrating plate, as glowing lines or as half a million grains of sand.

Source: Original (inspired by https://paulbourke.net/geometry/chladni/)
Author: smfx (original; Chladni figures as documented by Paul Bourke)
License: MIT
license_class: permissive

Written from scratch from the textbook plate formulas (the Bourke page states no licence, so nothing was copied):
  * square plate (constrained at the centre), plate coords X, Y in [0, 1]:
        f = 0.5 * (cos(n pi X) cos(m pi Y) -/+ cos(m pi X) cos(n pi Y))
  * circular plate: J_n(K r) cos(n theta) with K = the m-th zero of J_n, so the rim is a nodal circle.  J_n is
    replaced by a cheap Debye-phase profile env(x) cos(sqrt(x^2 - n^2) - n acos(n / x) - pi / 4), whose zeros sit
    within ~2% of the true Bessel zeros (no spurious rings inside the turning point x < n); each circular entry
    is a superposition of two near-degenerate modes, which gives the organic figures real plates show.

Pipeline (internal buffer at render_scale, then present()):
  1. field: the blended plate displacement f and its central-difference gradient on a coarse grid (every 4
     internal px; the figures are band-limited, >= ~80 px wavelengths, so bilinear lookup is accurate to ~0.15 px).
     Both passes below sample this grid, which keeps the circular (Bessel) path cheap.
  2. sand: 524288 grains (float2, screen coords) bounce with a gaussian step growing with the local amplitude |f|
     (they dance where the plate moves and come to rest on the nodes), plus a clamped Newton step towards the
     nearest nodal line and a small absolute jitter that sets the width of the sand bands; a faint drizzle of
     respawned grains keeps it alive.  Each grain (with its own brightness) is splatted bilinearly with float
     atomics into a density buffer that decays like pow(0.55, dt * 60).
  3. draw: distance to the nodal line |f| / |grad f| gives an anti-aliased core line + halo; a dark plate tinted
     by pal(|f| * period + drift) heaves with the vibration phase (signed, so + and - lobes trade brightness and
     the frame's mean luma stays put); the sand density is tone-mapped on top.  The same pass decays the density.

``mode`` is a continuous index along a curated list of 12 figures per plate shape (square (n, m) pairs; circular
two-mode superpositions); neighbouring figures cross-fade with a short dwell on the pure figure.  ``plate_shape``
morphs square -> circular, ``sand_mix`` glowing lines -> sand.  A 'sway' adds the next figure along at
beta * sin(vibration phase), so the nodal lines breathe in step with the plate's heave (and the sand follows);
it stays continuous across index steps because the sway partner is itself the cross-fade one step ahead.

Macros: energy (remapped e' = smoothstep(0.1, 0.85, e)) -> width of a slow wander of the mode index around the
regional ``mode`` (e'^1.5, slewed 1.5 s), wander and vibration rates x(0.4 + 1.2 e'), sway depth, line halo glow,
plate heave depth, a brightness ripple radiating from the plate's centre along the lines (rate and depth), grain
bounce, sand band width and the fresh-grain drizzle.  pulse (k = pulse * (0.3 + 0.7 e))
-> a 'shake' (gain ``shake_kick``) that throws the sand off the lines to resettle and buzzes the glow lines wider,
a nodal-line flash (<= +30%, lines only) and a rate kick on the vibration and ripple phases.  speed scales every rate and the
sand's motion (0 freezes it); palette colours lines, plate and sand; intensity is output gain.
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

# curated figures, simple -> complex.  square: (n, m, sign); circle: ((n1, zero1), (n2, zero2), weight2)
# (the '-' figures all share the plate diagonal X = Y as a nodal line; the '+' ones (n + m even) break it up)
SQUARE_MODES = [(1, 2, -1), (1, 3, 1), (2, 3, -1), (2, 4, -1), (1, 4, -1), (3, 5, 1), (2, 5, -1), (2, 6, 1),
                (3, 7, 1), (2, 7, -1), (4, 7, -1), (5, 8, -1)]
CIRCLE_MODES = [((2, 1), (0, 2), 0.5), ((3, 1), (0, 2), 0.4), ((2, 2), (0, 3), 0.6), ((3, 2), (1, 3), 0.7),
                ((4, 2), (2, 3), 0.7), ((6, 2), (0, 4), 0.5), ((5, 2), (1, 4), 0.6), ((4, 3), (2, 4), 0.6),
                ((3, 3), (6, 2), 0.7), ((5, 3), (3, 4), 0.6), ((6, 3), (0, 5), 0.5), ((8, 3), (4, 4), 0.5)]
NMODES = len(SQUARE_MODES)
GW, GH = 1024, 512                       # grains, as a 2D launch grid (524288)
REF_PIX = 1920 * 1080                    # grain weight is normalised to this internal pixel count


def _phase(x: float, n: float) -> float:
    x = max(x, 1e-6)
    return math.sqrt(max(x * x - n * n, 0.0)) - n * math.acos(min(n / x, 1.0)) - math.pi / 4


def _kzero(n: int, m: int) -> float:
    """x where the Debye phase reaches its m-th zero crossing (pi/2 + (m-1) pi): the radial wavenumber that puts
    the m-th nodal circle of the device profile exactly on the rim r = 1."""
    target = math.pi / 2 + (m - 1) * math.pi
    lo, hi = max(float(n), 1e-3), 200.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if _phase(mid, n) < target else (lo, mid)
    return 0.5 * (lo + hi)


def _mode_pack(i: int) -> list[float]:
    n, m, sg = SQUARE_MODES[i]
    (n1, z1), (n2, z2), w = CIRCLE_MODES[i]
    return [n, m, sg, n1, _kzero(n1, z1), n2, _kzero(n2, z2), w]


MODE_PACKS = [_mode_pack(i) for i in range(NMODES)]

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 cos rot | 15 sin rot | 16 1/plate scale | 17 shape | 18 mode blend
#  19 sway (signed) | 20..27 mode A (sq n, m, sign, c n1, K1, n2, K2, w) | 28..35 mode B | 64..71 mode C (sway)
#  36 core width px | 37 line gain | 38 halo gain | 39 halo width px | 40 sand weight | 41 line weight
#  42 density display scale | 43 density decay | 44 heave (signed) | 45 plate level | 46 output gain
#  47 ripple depth | 48 ripple phase
#  50 grain bounce | 51 shake | 52 settle alpha | 53 settle max step | 54 respawn prob | 56 jitter floor (abs)
#  57 splat weight
GS = 4                                   # field grid spacing (internal px); the field is band-limited (>= ~80 px waves)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define GS %(gs)d
__device__ __forceinline__ float bes(float x, float n) {          // Debye-phase stand-in for J_n (unit envelope)
    x = fmaxf(x, 1e-4f);
    const float s = sqrtf(fmaxf(x * x - n * n, 0.f));
    const float a = acosf(fminf(n / x, 1.f));
    const float env = n > 0.f ? powf(x * x / (x * x + 0.5f * n * n), 0.5f * n) : 1.f;
    return env * cosf(s - n * a - 0.78539816f);
}
__device__ __forceinline__ float mode_field(float px, float py, const float* __restrict__ M, float shape) {
    float f = 0.f;
    if (shape < 0.999f) {
        const float X = 0.5f * (px + 1.f), Y = 0.5f * (py + 1.f);
        const float fs = 0.5f * (cospif(M[0] * X) * cospif(M[1] * Y) + M[2] * cospif(M[1] * X) * cospif(M[0] * Y));
        f = (1.f - shape) * fs;
    }
    if (shape > 0.001f) {
        const float r = sqrtf(px * px + py * py), th = atan2f(py, px);
        const float fc = (bes(M[4] * r, M[3]) * cosf(M[3] * th) + M[7] * bes(M[6] * r, M[5]) * cosf(M[5] * th))
                         / (1.f + M[7]);
        f += shape * fc;
    }
    return f;
}
// palette colour kept bright for emissive lines and sand: a dark phase of the cosine palette folds continuously
// towards its opposite colour 2a - pal(t) (always the bright side), then the max channel is lifted to >= ~0.75
__device__ __forceinline__ float3 bright_pal(float t, const float* __restrict__ PAL) {
    float3 c = pal(t, PAL);
    const float m0 = fmaxf(c.x, fmaxf(c.y, c.z));
    const float w = 1.f - smooth(0.12f, 0.45f, m0);
    c.x += w * (clamp01(2.f * PAL[0] - c.x) - c.x);
    c.y += w * (clamp01(2.f * PAL[1] - c.y) - c.y);
    c.z += w * (clamp01(2.f * PAL[2] - c.z) - c.z);
    const float k = 1.f / fmaxf(fmaxf(c.x, fmaxf(c.y, c.z)), 0.6f);
    return make_float3(c.x * k, c.y * k, c.z * k);
}
// plate displacement at screen point (u, v) (units of half the frame height, y down)
__device__ __forceinline__ float plate(float u, float v, const float* __restrict__ P) {
    const float px = (P[14] * u - P[15] * v) * P[16], py = (P[15] * u + P[14] * v) * P[16];
    const float fa = mode_field(px, py, P + 20, P[17]);
    const float fb = (P[18] > 0.001f || P[19] != 0.f) ? mode_field(px, py, P + 28, P[17]) : 0.f;
    float f = fa + P[18] * (fb - fa);
    if (P[19] != 0.f) {                       // sway: the next figure along, breathing in and out with the plate
        const float fc = mode_field(px, py, P + 64, P[17]);
        f += P[19] * (fb + P[18] * (fc - fb));
    }
    return f;
}
// bilinear lookup of (f, df/dx, df/dy per internal px) at internal pixel coords (X, Y)
__device__ __forceinline__ float3 fsample(const float4* __restrict__ F, int gw, int gh, float X, float Y) {
    const float fx = X * (1.f / GS) + 1.f, fy = Y * (1.f / GS) + 1.f;
    const int i0 = min(max((int)floorf(fx), 0), gw - 2), j0 = min(max((int)floorf(fy), 0), gh - 2);
    const float tx = clamp01(fx - i0), ty = clamp01(fy - j0);
    const float4 a = F[j0 * gw + i0], b = F[j0 * gw + i0 + 1], c = F[(j0 + 1) * gw + i0], d = F[(j0 + 1) * gw + i0 + 1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float3(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z));
}

extern "C" __global__
void chladni_field(float4* __restrict__ F, const int gw, const int gh, const int W, const int H,
                   const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= gw || j >= gh) return;
    const float hh = 0.5f * H, h = 1.f / hh;
    const float u = ((i - 1) * GS - 0.5f * W) / hh, v = ((j - 1) * GS - hh) / hh;
    const float f = plate(u, v, P);
    const float gx = 0.5f * (plate(u + h, v, P) - plate(u - h, v, P));
    const float gy = 0.5f * (plate(u, v + h, P) - plate(u, v - h, P));
    F[j * gw + i] = make_float4(f, gx, gy, 0.f);
}

extern "C" __global__
void chladni_sand(float2* __restrict__ G, float* __restrict__ D, const float4* __restrict__ F, const int gw,
                  const int gh, const int W, const int H, const float* __restrict__ P, const unsigned int frame,
                  const int nsub, const int splat)
{
    const int gx = blockIdx.x * blockDim.x + threadIdx.x, gy = blockIdx.y * blockDim.y + threadIdx.y;
    if (gx >= %(gw)d || gy >= %(gh)d) return;
    const unsigned int i = gy * %(gw)d + gx;
    const float asp = (float)W / (float)H, hh = 0.5f * H;
    float2 q = G[i];
    for (int s = 0; s < nsub; ++s) {
        const unsigned int fs = frame * 4u + (unsigned int)s;
        if (hash1(i, fs, 11u) < P[54] || fabsf(q.x) > asp + 0.03f || fabsf(q.y) > 1.03f) {
            q.x = (2.f * hash1(i, fs, 12u) - 1.f) * asp;
            q.y = 2.f * hash1(i, fs, 13u) - 1.f;
            continue;
        }
        const float3 fg = fsample(F, gw, gh, q.x * hh + 0.5f * W, q.y * hh + hh);
        const float f = fg.x;
        // settle: clamped Newton step towards the nearest nodal line (pixel units -> screen units)
        const float g2 = fg.y * fg.y + fg.z * fg.z + 1e-8f;
        float sx = -f * fg.y / (g2 * hh), sy = -f * fg.z / (g2 * hh);
        const float sl = sqrtf(sx * sx + sy * sy);
        if (sl > P[53]) { sx *= P[53] / sl; sy *= P[53] / sl; }
        // bounce: gaussian step growing with the local amplitude (+ a floor, + the beat shake)
        const float u1 = fmaxf(hash1(i, fs, 14u), 1e-7f), u2 = hash1(i, fs, 15u);
        const float rad = sqrtf(-2.f * logf(u1));
        const float j = (P[50] * powf(fabsf(f), 0.8f) + P[56] + P[51]) * rad;
        float sn, cs;
        sincosf(6.2831853f * u2, &sn, &cs);
        q.x += P[52] * sx + j * cs;
        q.y += P[52] * sy + j * sn;
    }
    G[i] = q;
    if (!splat) return;
    const float fx = q.x * hh + 0.5f * W - 0.5f, fy = q.y * hh + hh - 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    if (x0 < -1 || y0 < -1 || x0 >= W || y0 >= H) return;
    const float hb = hash1(i, 77u);
    const float tx = fx - x0, ty = fy - y0, w = P[57] * (0.3f + 1.4f * hb * hb * 1.5f);
    if (x0 >= 0 && y0 >= 0) atomicAdd(D + y0 * W + x0, w * (1.f - tx) * (1.f - ty));
    if (x0 + 1 < W && y0 >= 0) atomicAdd(D + y0 * W + x0 + 1, w * tx * (1.f - ty));
    if (x0 >= 0 && y0 + 1 < H) atomicAdd(D + (y0 + 1) * W + x0, w * (1.f - tx) * ty);
    if (x0 + 1 < W && y0 + 1 < H) atomicAdd(D + (y0 + 1) * W + x0 + 1, w * tx * ty);
}

extern "C" __global__
void chladni_draw(uchar4* __restrict__ out, float* __restrict__ D, const float4* __restrict__ F, const int gw,
                  const int gh, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float u = (x + 0.5f - 0.5f * W) / hh, v = (y + 0.5f - hh) / hh;
    const float3 fg = fsample(F, gw, gh, x + 0.5f, y + 0.5f);
    const float f0 = fg.x;
    const float gl = sqrtf(fg.y * fg.y + fg.z * fg.z) + 1e-6f;
    const float A = fabsf(f0);
    const float d = A / gl;                                                       // px to the nodal line
    const float* PAL = P;
    const float period = P[12], drift = P[13];
    const float rr = sqrtf(u * u + v * v);
    // plate: dark, tinted by the amplitude, heaving with the vibration phase (signed: lobes trade brightness)
    const float3 pc = pal(A * period + drift + 0.5f, PAL);
    const float pl = P[45] * (0.25f + 0.75f * A) * fmaxf(1.f + P[44] * f0, 0.f);
    float3 c = make_float3(pc.x * pl, pc.y * pl, pc.z * pl);
    // nodal lines: core + halo
    const float lw = P[41];
    if (lw > 0.f) {
        const float w = P[36];
        const float core = __expf(-(d * d) / (w * w));
        const float halo = __expf(-d / P[39]) * P[38];
        const float3 lc = bright_pal(drift + 0.12f * period * rr, PAL);
        const float3 hc = bright_pal(drift + 0.35f * period * fminf(A * 4.f, 1.f) + 0.05f, PAL);
        const float lg = P[37] * (1.f + P[47] * __sinf(5.5f * rr - P[48]));     // drive ripple from the centre
        const float wc = 0.35f * core * core;                                     // white-hot centre
        c.x += lw * (lg * (core * lc.x + wc) + halo * hc.x);
        c.y += lw * (lg * (core * lc.y + wc) + halo * hc.y);
        c.z += lw * (lg * (core * lc.z + wc) + halo * hc.z);
    }
    // sand: pale grains tinted by the palette, white where they pile up
    const int idx = y * W + x;
    const float dn = D[idx];
    D[idx] = dn * P[43];
    if (P[40] > 0.f) {
        const float sd = 1.f - __expf(-dn * P[42]);
        const float3 sp = bright_pal(drift + 0.06f + 0.15f * period * sd, PAL);
        const float sw = P[40] * sd * (1.f + 0.5f * P[47] * __sinf(5.5f * rr - P[48])), wh = 0.25f * sd * sd;
        c.x = c.x * (1.f - 0.7f * sw) + sw * (0.8f * sp.x + 0.16f + wh);
        c.y = c.y * (1.f - 0.7f * sw) + sw * (0.8f * sp.y + 0.15f + wh);
        c.z = c.z * (1.f - 0.7f * sw) + sw * (0.8f * sp.z + 0.13f + wh);
    }
    const float vg = 1.f - 0.28f * rr * rr / (1.f + (float)W * W / ((float)H * H));
    const float g = 255.f * P[46] * vg;
    out[idx] = pack(make_float4(c.x * g, c.y * g, c.z * g, 255.f));
}
""" % {"gw": GW, "gh": GH, "gs": GS}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class Chladni(Source):
    name = "chladni"
    label = "Chladni plate"
    description = ("Sound made visible: the nodal figures of a vibrating plate as glowing lines or half a million "
                   "grains of sand dancing onto the nodes, morphing between modes.")
    params = _order({
        "mode": num(0.0, 1.0, 0.4, "Figure (simple -> complex)", tier="regional", basic=True),
        "sand_mix": num(0.0, 1.0, 0.65, "Lines -> sand", tier="regional", basic=True),
        "plate_shape": num(0.0, 1.0, 0.0, "Plate (square -> circular)", tier="regional"),
        "line_sharpness": num(0.0, 1.0, 0.6, "Line sharpness", tier="regional"),
        "rotation": num(0.0, 45.0, 0.0, "Rotation", step=0.5, unit="°", tier="regional"),
        "plate_scale": num(0.5, 2.5, 1.1, "Plate size", tier="regional"),
        "shake_kick": num(0.0, 2.0, 1.0, "Beat shake", tier="regional"),
        **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.02),
    }, ["mode", "sand_mix", "plate_shape", "line_sharpness", "rotation", "plate_scale", "shake_kick",
        "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw, self.gh = self.w // GS + 3, self.h // GS + 3
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.dens = cp.zeros((self.h, self.w), cp.float32)
        self.field = cp.zeros((self.gh, self.gw, 4), cp.float32)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_field = mod.get_function("chladni_field")
        self.k_sand = mod.get_function("chladni_sand")
        self.k_draw = mod.get_function("chladni_draw")
        self.Ph = np.zeros(72, np.float32)
        self.P = cp.zeros(72, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        asp = self.w / self.h
        g = np.empty((GH, GW, 2), np.float32)
        g[..., 0] = rng.uniform(-asp, asp, (GH, GW))
        g[..., 1] = rng.uniform(-1.0, 1.0, (GH, GW))
        self.grains = cp.asarray(g)
        self.wander = float(rng.uniform(0, 2 * math.pi))
        self.vib = 0.0
        self.ripple = 0.0
        self.drift = 0.0
        self.es = None
        self.frame = 0
        # settle the sand onto the opening figure so the scene arrives formed, then prime the density buffer
        for i in range(50):
            self._frame(params, 1.0 / 60.0, splat=(i == 49), prime=True)

    # ------------------------------------------------------------------ per-frame host maths + field + sand
    def _frame(self, p: dict, dt: float, splat: bool = True, prime: bool = False) -> None:
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = _ss(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.es = ep if self.es is None else self.es + (ep - self.es) * a
        k = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ep

        # phases (host-integrated; kicks add to rates only)
        if not prime:
            self.wander += 2 * math.pi * 0.09 * rate * spd * dt
        self.vib = _wrap(self.vib + 2 * math.pi * 0.28 * rate * (1.0 + 1.5 * k) * spd * dt, 2 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        self.ripple = _wrap(self.ripple + 2 * math.pi * 0.25 * rate * (1.0 + 1.5 * k) * spd * dt, 2 * math.pi)

        # mode index: regional mode + an energy-wide slow wander, reflected into range
        amp = 0.5 + 1.1 * self.es ** 1.5
        w = self.wander
        idx = float(p["mode"]) * (NMODES - 1) + amp * (0.62 * math.sin(w) + 0.38 * math.sin(1.618 * w + 1.1))
        top = NMODES - 1
        idx = _wrap(abs(idx), 2 * top)
        if idx > top:
            idx = 2 * top - idx
        i0 = min(int(math.floor(idx)), top - 1)
        blend = _ss(0.12, 0.88, idx - i0)

        P = self.Ph
        P[0:12] = palette_coeffs(p["palette"])
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        rot = math.radians(float(p["rotation"]))
        P[14], P[15] = math.cos(rot), math.sin(rot)
        P[16] = 1.0 / float(p["plate_scale"])
        P[17] = min(max(float(p["plate_shape"]), 0.0), 1.0)
        P[18] = blend
        P[20:28] = MODE_PACKS[i0]
        P[28:36] = MODE_PACKS[i0 + 1]
        P[64:72] = MODE_PACKS[(i0 + 2) % NMODES]
        P[19] = (0.09 + 0.17 * ep) * math.sin(self.vib)

        ls = float(p["line_sharpness"])
        shake = float(p["shake_kick"]) * k
        sm = float(p["sand_mix"])
        px_scale = self.h / 1080.0                         # line widths are tuned at 1080p internal
        P[36] = (3.2 + (0.8 - 3.2) * ls) * px_scale * (1.0 + 0.35 * shake)
        P[37] = (0.85 + 0.15 * ep) * (1.0 + 0.3 * k)
        P[38] = 0.25 + 0.45 * ep + 0.1 * shake
        P[39] = (16.0 + (5.0 - 16.0) * ls) * px_scale * (1.0 + 0.3 * shake)
        P[40] = _ss(0.0, 0.6, sm)
        P[41] = 1.0 - 0.9 * _ss(0.1, 0.9, sm)
        dec = 0.55 ** (max(dt, 1.0 / 60.0) * 60.0)
        P[42] = 0.7 * (1.0 - dec)
        P[43] = dec
        P[44] = (0.35 + 0.45 * ep) * math.cos(self.vib)
        P[45] = 0.11
        P[46] = float(p["intensity"])
        P[47] = 0.18 + 0.17 * ep
        P[48] = self.ripple

        # sand dynamics, per 60 fps frame-equivalent; substeps keep a big dt stable
        f60 = min(dt * 60.0 * spd, 3.0)
        nsub = 1 if f60 <= 1.5 else 2
        fs = f60 / nsub
        P[50] = 0.0045 * (0.55 + 0.9 * ep) * math.sqrt(fs)              # bounce where the plate moves
        P[51] = 0.026 * shake * math.sqrt(fs)                           # beat shake: scatter off the lines
        P[52] = 1.0 - (1.0 - 0.08) ** fs                                # settle towards the nodes
        P[53] = 0.05
        P[54] = (0.004 + 0.03 * self.es ** 1.5) * dt * spd / nsub       # drizzle of fresh grains
        P[56] = (0.0034 + (0.0012 - 0.0034) * ls) * (0.8 + 0.4 * ep) * math.sqrt(fs)   # sand band width
        P[57] = float(self.w * self.h) / REF_PIX * (dec / (1.0 - dec) if prime else 1.0)
        self.P.set(P)

        self.frame += 1
        launch_2d(self.k_field, self.gw, self.gh, (self.field, np.int32(self.gw), np.int32(self.gh),
                                                  np.int32(self.w), np.int32(self.h), self.P))
        launch_2d(self.k_sand, GW, GH, (self.grains, self.dens, self.field, np.int32(self.gw), np.int32(self.gh),
                                        np.int32(self.w), np.int32(self.h), self.P,
                                        np.uint32(self.frame & 0x3FFFFFFF), np.int32(nsub), np.int32(1 if splat else 0)))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        self._frame(p, dt)
        launch_2d(self.k_draw, self.w, self.h, (self.buf, self.dens, self.field, np.int32(self.gw), np.int32(self.gh),
                                                np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
