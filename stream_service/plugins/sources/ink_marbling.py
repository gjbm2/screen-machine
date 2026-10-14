"""Ink marbling: suminagashi / ebru paper marbling.  Ink drops land and push the existing rings outward, then tine
combs, wavy rakes and vortex strokes drag them into stone, feathered, nonpareil and peacock patterns.

Source: Original (inspired by https://people.csail.mit.edu/jaffer/Marbling/ and https://en.wikipedia.org/wiki/Paper_marbling)
Author: smfx (original; published maths from S. Lu, A. Jaffer et al., 'Mathematical Marbling', IEEE CG&A 32(6), 2012)
License: MIT (original)
license_class: permissive

Written from scratch from the closed-form marbling maps in the paper (no code copied).  Every operation is an
exactly invertible map of the bath surface, so each pixel walks BACK through the last <= 64 operations and never
resamples (crisp, unlike fluid_ink's advected turbulence):
  * drop at c, radius r:      outside, p' = c + v sqrt(1 + r^2/|v|^2);  inverse q = c + v sqrt(1 - r^2/|v|^2)
                              (inside |v| < r the pixel shows the drop's ink)
  * tine comb (spacing s):    p' = p + z(d) M,  z = alpha lambda / (d + lambda),  d = distance to the nearest tine
                              (d is unchanged by a shift along M, so the inverse is exact: q = p - z(d) M)
  * wavy rake:                p' = p + A sin(omega (p.N) + phi) M          (same exact-inverse argument)
  * vortex stroke:            rotate about c by theta(|v|) = A w^2 / ((|v| - R)^2 + w^2)  (|v| preserved)
The 2x2 Jacobian of the composed inverse is carried along analytically, so every ink edge is anti-aliased in
SCREEN pixels however hard later drops compress it or combs stretch it: front-to-back coverage per drop, plus an
ink-pooling rim whose width lives in the drop's own space (thin rings get thin lines) but is widened to >= 0.7 px
with its darkness conserved, so baking never pops.

Pipeline (internal buffer at render_scale, then present()):
  1. ops ring (host): drops grow over ~0.2-0.45 s (ease-out), strokes ramp their amplitude over 1.5-5 s
     (smoothstep), so nothing jumps.  Up to 64 ops x 12 floats are uploaded per frame.
  2. ink_render: a slow 'bath ripple' (energy/pulse amplitude, not baked) then the inverse ops newest -> oldest,
     then whatever lies beneath: the baked sheet (a margin-padded colour texture, 1.25x the frame) or, beyond
     it, a procedural ground of ink bands.  Paper grain (precomputed fibre + mottle) and a soft vignette.
  3. bake: when the ring holds >= 48 ops, the oldest finished ops (<= 32) are composed into the sheet in one
     pass (ping-pong) and dropped from the ring; the image is unchanged except one bilinear resample.
  Setup primes a full sheet (a stone field, a gel-git comb pass, ring sites, a vortex) so frame 0 is formed.

Each drop's colour is fixed when it lands: pal(ink index / colour_count * palette_period + drift), with inks
folded away from the ground tone so they always read.  So palette, drift and session-palette changes arrive as
NEW ink (the sheet re-inks itself as drops push the old colours out), exactly like a real bath.

Macros (e' = smoothstep(0, 0.65, energy) slewed over 1 s; the scene is tuned for energy 0..0.6):
  energy -> ambient drop rate (0.3 + 1.5 e'^1.3 /s) and drop size, comb/vortex stroke rate, stroke speed
            (duration 3.6 -> 1.4 s) and pull, vortex twist, bath-ripple amplitude and rate;
  pulse  -> a host-detected rising edge (> 0.15, 120 ms refractory) appends a drop at the current ring site
            whose radius grows over ~200 ms (so beats build concentric rings that shove the sheet outward);
            k = pulse (0.35 + 0.65 e) adds to the ripple amplitude and a +3.5% gain (never moves anything);
  speed scales every rate (drop growth, strokes, spawns, ripple, drift); intensity is output gain.
Regional: drop_vs_comb (basic) balances drops against comb strokes, drop_size, tine_spacing, stroke_direction,
vortex_amount, colour_count (applies to new drops, so it never jumps), paper_tone (ink-black -> cream ground),
paper_texture (paper grain, ink-edge pooling, ring shading).

Perf plan: render_scale 0.5, <= 64 inverse ops per pixel with early-out once a pixel is fully inked, one bake
pass every ~10-20 s.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

TAU = 2.0 * math.pi
NOPS = 64                 # ops ring capacity (inverse ops per pixel)
NOPF = 12                 # floats per op
NP = 72                   # global params
MARGIN = 1.25             # the baked sheet covers the frame x this (content is pulled in from beyond the edges)
BAKE_AT = 48              # bake when the ring holds this many ops ...
BAKE_MAX = 32             # ... composing at most this many of the oldest finished ones
DROP, COMB, WAVY, VORTEX = 0, 1, 2, 3
DARK = np.array([0.035, 0.032, 0.042])
CREAM = np.array([0.94, 0.905, 0.82])
LUMA = np.array([0.2126, 0.7152, 0.0722])

# P layout (floats)
#  0 px size (normalised units: half frame height = 1) | 1 sheet half-width BX | 2 sheet half-height BY
#  3 rim strength | 4 rim width (local units) | 5 ring shading | 6 paper grain | 7 output gain | 8 ripple amp
#  9..16 ripple wave vectors (4 x 2) | 17..20 ripple phases | 21 vignette
#  32..34 paper rgb | 35 ink count | 36, 37 ground band normal | 38 band width | 39 band wave amp
#  40 band wave freq | 41 band wave phase | 42..65 ink table (8 x rgb)
# OPS layout (12 floats each, oldest first)
#  drop:   0, cx, cy, r, R, G, B, edge factor (1 ink, 0.25 clear)
#  comb:   1, Bx, By, Mx, My, spacing, lambda, alpha
#  wavy:   2, Bx, By, Mx, My, omega, phi, amplitude
#  vortex: 3, cx, cy, R, width, twist
_SRC = HELPERS + r"""
#define NOPF %(nopf)d
struct J2 { float a, b, c, d; };                       // [[a, b], [c, d]]: dq = J dp (p = screen)
__device__ __forceinline__ J2 jmul(const J2 m, const J2 n) {
    J2 r;
    r.a = m.a * n.a + m.b * n.c; r.b = m.a * n.b + m.b * n.d;
    r.c = m.c * n.a + m.d * n.c; r.d = m.c * n.b + m.d * n.d;
    return r;
}
// |J^T u|: screen-space gradient magnitude of the local scalar u . q
__device__ __forceinline__ float jtn(const J2 J, float ux, float uy) {
    const float gx = J.a * ux + J.c * uy, gy = J.b * ux + J.d * uy;
    return sqrtf(gx * gx + gy * gy);
}
__device__ __forceinline__ float3 ink_of(const float* __restrict__ P, int band) {
    const int m = 2 * max((int)P[35], 1);
    int k = band %% m;
    if (k < 0) k += m;
    if (k & 1) return make_float3(P[32], P[33], P[34]);
    const int j = k >> 1;
    return make_float3(P[42 + 3 * j], P[43 + 3 * j], P[44 + 3 * j]);
}
// procedural ground beyond the baked sheet: wavy bands of ink and paper, anti-aliased through J
__device__ float3 ground(float qx, float qy, const J2 J, const float* __restrict__ P) {
    const float nx = P[36], ny = P[37], bwid = P[38];
    const float along = -qx * ny + qy * nx;
    const float gi = fminf(fmaxf((qx * nx + qy * ny) / bwid + P[39] * __sinf(P[40] * along + P[41]), -1e6f), 1e6f);
    const float fk = floorf(gi), f = gi - fk;
    const float g = jtn(J, nx, ny) / bwid * P[0] + 1e-9f;           // band index per screen pixel
    const float a = clamp01(0.5f + fminf(f, 1.f - f) / g);
    const int k = (int)fk;
    const float3 c0 = ink_of(P, k), c1 = ink_of(P, f < 0.5f ? k - 1 : k + 1);
    return make_float3(c1.x + (c0.x - c1.x) * a, c1.y + (c0.y - c1.y) * a, c1.z + (c0.z - c1.z) * a);
}

// walk back through ops n-1 .. 0 from point q (Jacobian J wrt screen), compositing drop inks front to back
__device__ float3 marble(float qx, float qy, J2 J, const float* __restrict__ O, const int n,
                         const uchar4* __restrict__ B, const int bw, const int bh, const int use_base,
                         const float* __restrict__ P)
{
    const float pxn = P[0];
    float cr = 0.f, cg = 0.f, cb = 0.f, T = 1.f;
    for (int i = n - 1; i >= 0; --i) {
        const float* o = O + i * NOPF;
        const int ty = (int)o[0];
        if (ty == 0) {                                                  // ---- ink drop
            const float r = o[3];
            if (r < 1e-5f) continue;
            const float vx = qx - o[1], vy = qy - o[2];
            const float L = sqrtf(vx * vx + vy * vy) + 1e-7f;
            const float nx = vx / L, ny = vy / L;
            const float g = jtn(J, nx, ny) * pxn + 1e-9f;              // local units per screen pixel
            const float sd = (r - L) / g;                               // signed px to the edge, + inside
            const float a = clamp01(0.5f + sd);
            if (a > 0.f) {
                const float wl = P[4] / g, we = fmaxf(wl, 0.7f);        // ink pools at the edge (darkness kept)
                const float rim = o[7] * P[3] * (wl / we) * __expf(-fmaxf(sd, 0.f) / we);
                const float rr = fminf(L / r, 1.f), rr2 = rr * rr;
                const float m = (1.f - rim) * (1.f - o[7] * P[5] * rr2 * rr2);
                const float w = T * a * m;
                cr += w * o[4]; cg += w * o[5]; cb += w * o[6];
                T *= 1.f - a;
                if (T < 0.004f) {
                    const float s = 1.f / (1.f - T);
                    return make_float3(cr * s, cg * s, cb * s);
                }
            }
            // continue with the point half a pixel outside the rim (the ink this drop pushed aside)
            const float Lm = fmaxf(L, r + 0.5f * g);
            const float wx = nx * Lm, wy = ny * Lm;
            const float s = sqrtf(fmaxf(1.f - (r * r) / (Lm * Lm), 1e-6f));
            qx = o[1] + s * wx; qy = o[2] + s * wy;
            const float k = (r * r) / (s * Lm * Lm * Lm * Lm);
            const J2 Jo = {s + k * wx * wx, k * wx * wy, k * wx * wy, s + k * wy * wy};
            J = jmul(Jo, J);
        } else if (ty == 1) {                                           // ---- tine comb
            const float amp = o[7];
            if (amp == 0.f) continue;
            const float mx = o[3], my = o[4], nx = -my, ny = mx, sp = o[5], lam = o[6];
            const float u = (qx - o[1]) * nx + (qy - o[2]) * ny;
            const float um = u - sp * floorf(u / sp);
            const bool lo = um < 0.5f * sp;
            const float d = lo ? um : sp - um;
            const float den = d + lam;
            const float z = amp * lam / den;
            const float zp = (lo ? -1.f : 1.f) * amp * lam / (den * den);   // dz/du
            qx -= z * mx; qy -= z * my;
            const J2 Jo = {1.f - mx * zp * nx, -mx * zp * ny, -my * zp * nx, 1.f - my * zp * ny};
            J = jmul(Jo, J);
        } else if (ty == 2) {                                           // ---- wavy rake
            const float amp = o[7];
            if (amp == 0.f) continue;
            const float mx = o[3], my = o[4], nx = -my, ny = mx, om = o[5];
            const float u = (qx - o[1]) * nx + (qy - o[2]) * ny;
            float sn, cs;
            sincosf(om * u + o[6], &sn, &cs);
            const float z = amp * sn, zp = amp * om * cs;
            qx -= z * mx; qy -= z * my;
            const J2 Jo = {1.f - mx * zp * nx, -mx * zp * ny, -my * zp * nx, 1.f - my * zp * ny};
            J = jmul(Jo, J);
        } else {                                                        // ---- vortex stroke
            const float amp = o[5];
            if (amp == 0.f) continue;
            const float vx = qx - o[1], vy = qy - o[2];
            const float d = sqrtf(vx * vx + vy * vy) + 1e-7f;
            const float dr = d - o[3], w2 = o[4] * o[4];
            const float D = dr * dr + w2;
            const float th = -amp * w2 / D;
            const float dth = amp * w2 * 2.f * dr / (D * D);            // d theta / d |v|
            float sn, cs;
            sincosf(th, &sn, &cs);
            const float rx = cs * vx - sn * vy, ry = sn * vx + cs * vy;
            qx = o[1] + rx; qy = o[2] + ry;
            const float gx = dth * vx / d, gy = dth * vy / d;
            const J2 Jo = {cs - ry * gx, -sn - ry * gy, sn + rx * gx, cs + rx * gy};
            J = jmul(Jo, J);
        }
    }
    // beneath the ring: the baked sheet, fading to the procedural ground beyond its margin
    const float ew = use_base ? smooth(0.f, 0.05f, fminf(P[1] - fabsf(qx), P[2] - fabsf(qy))) : 0.f;
    float3 c = make_float3(0.f, 0.f, 0.f);
    if (ew < 1.f) c = ground(qx, qy, J, P);
    if (ew > 0.f) {
        const float4 s = bilin(B, (qx + P[1]) / pxn, (qy + P[2]) / pxn, bw, bh);
        const float k = 1.f / 255.f;
        c.x += (s.x * k - c.x) * ew; c.y += (s.y * k - c.y) * ew; c.z += (s.z * k - c.z) * ew;
    }
    return make_float3(cr + T * c.x, cg + T * c.y, cb + T * c.z);
}

extern "C" __global__
void ink_render(uchar4* __restrict__ out, const uchar4* __restrict__ B, const float* __restrict__ PA,
                const float* __restrict__ O, const int n, const int W, const int H, const int bw, const int bh,
                const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float pxn = P[0];
    const float px = (x + 0.5f - 0.5f * W) * pxn, py = (y + 0.5f - 0.5f * H) * pxn;
    // bath ripple: a slow live wobble of the whole surface (newest of all ops, never baked)
    const float A = P[8];
    float s1, c1, s2, c2, s3, c3, s4, c4;
    __sincosf(P[9] * px + P[10] * py + P[17], &s1, &c1);
    __sincosf(P[11] * px + P[12] * py + P[18], &s2, &c2);
    __sincosf(P[13] * px + P[14] * py + P[19], &s3, &c3);
    __sincosf(P[15] * px + P[16] * py + P[20], &s4, &c4);
    const float qx = px + A * (s1 + 0.6f * s2), qy = py + A * (c3 + 0.6f * c4);
    const J2 J = {1.f + A * (c1 * P[9] + 0.6f * c2 * P[11]), A * (c1 * P[10] + 0.6f * c2 * P[12]),
                  -A * (s3 * P[13] + 0.6f * s4 * P[15]), 1.f - A * (s3 * P[14] + 0.6f * s4 * P[16])};
    const float3 c = marble(qx, qy, J, O, n, B, bw, bh, 1, P);
    const int idx = y * W + x;
    const float pf = 1.f + P[6] * PA[idx];
    const float rr = (px * px + py * py) / (1.f + (float)W * W / ((float)H * H));
    const float g = 255.f * P[7] * pf * (1.f - P[21] * rr);
    out[idx] = pack(make_float4(c.x * g, c.y * g, c.z * g, 255.f));
}

extern "C" __global__
void ink_bake(uchar4* __restrict__ dst, const uchar4* __restrict__ src, const float* __restrict__ O, const int n,
              const int bw, const int bh, const int use_base, const float* __restrict__ P)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i >= bw || j >= bh) return;
    const float pxn = P[0];
    const J2 J = {1.f, 0.f, 0.f, 1.f};
    const float3 c = marble(-P[1] + (i + 0.5f) * pxn, -P[2] + (j + 0.5f) * pxn, J, O, n, src, bw, bh, use_base, P);
    dst[j * bw + i] = pack(make_float4(c.x * 255.f, c.y * 255.f, c.z * 255.f, 255.f));
}

extern "C" __global__
void ink_paper(float* __restrict__ PA, const int W, const int H, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float u = (x + 0.5f) / H, v = (y + 0.5f) / H;                // height-normalised
    const float fine = vnoise(u * 620.f, v * 620.f, seed) - 0.5f;
    const float fa = vnoise((0.906f * u + 0.423f * v) * 55.f, (-0.423f * u + 0.906f * v) * 640.f, seed + 3u) - 0.5f;
    const float fb = vnoise((0.940f * u - 0.342f * v) * 40.f, (0.342f * u + 0.940f * v) * 480.f, seed + 7u) - 0.5f;
    const float mott = fbm(u * 3.f, v * 3.f, seed + 11u) - 0.5f;
    PA[y * W + x] = 0.13f * fine + 0.09f * (fa + fb) + 0.24f * mott;
}
""" % {"nopf": NOPF}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _pal(t: float, c: np.ndarray) -> np.ndarray:
    return np.clip(c[0:3] + c[3:6] * np.cos(TAU * (c[6:9] * t + c[9:12])), 0.0, 1.0)


class InkMarbling(Source):
    name = "ink_marbling"
    label = "Ink Marbling"
    description = ("Suminagashi / ebru paper marbling: ink drops land on the beat and shove the rings outward, "
                   "then combs, rakes and vortex strokes drag them into stone, feathered and peacock patterns.")
    params = {
        "drop_vs_comb": num(0.0, 1.0, 0.4, "Drops -> combing", tier="regional", basic=True),
        "drop_size": num(0.4, 2.0, 1.0, "Drop size", tier="regional"),
        "tine_spacing": num(0.04, 0.5, 0.15, "Tine spacing", tier="regional"),
        "stroke_direction": num(0.0, 180.0, 0.0, "Stroke direction", step=1.0, unit="°", tier="regional"),
        "vortex_amount": num(0.0, 1.0, 0.3, "Vortex swirls", tier="regional"),
        "colour_count": num(2.0, 8.0, 5.0, "Ink colours", step=1.0, tier="regional"),
        "paper_tone": num(0.0, 1.0, 0.8, "Ground (ink-black -> cream)", tier="regional"),
        "paper_texture": num(0.0, 1.0, 0.5, "Paper texture", tier="regional"),
        **standard_scene_params(palette="ocean", render_scale=0.5, period=1.0, drift=0.02),
    }

    # ------------------------------------------------------------------ lifecycle
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.pxn = 2.0 / self.h
        self.asp = self.w / self.h
        self.bw = int(round(self.w * MARGIN / 2)) * 2
        self.bh = int(round(self.h * MARGIN / 2)) * 2
        self.BX = 0.5 * self.bw * self.pxn
        self.BY = 0.5 * self.bh * self.pxn
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.base = [cp.zeros((self.bh, self.bw, 4), cp.uint8) for _ in range(2)]
        self.bi = 0
        self.paper = cp.zeros((self.h, self.w), cp.float32)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_render = mod.get_function("ink_render")
        self.k_bake = mod.get_function("ink_bake")
        k_paper = mod.get_function("ink_paper")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.Oh = np.zeros(NOPS * NOPF, np.float32)
        self.O = cp.zeros(NOPS * NOPF, cp.float32)

        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed)
        rng = self.rng
        launch_2d(k_paper, self.w, self.h, (self.paper, np.int32(self.w), np.int32(self.h),
                                            np.uint32((seed * 2654435761) & 0xFFFFFFFF)))
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12].copy() if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))
        self.ink_off = float(rng.uniform(0.0, 1.0))
        self.drift = 0.0
        self.e_s = None
        self.ops: list[dict] = []
        self.drop_acc = float(rng.uniform(0.0, 1.0))
        self.comb_acc = float(rng.uniform(0.2, 0.7))
        self.vort_acc = float(rng.uniform(0.0, 0.5))
        self.prog = 0
        self.theta_jit = 0.0
        self.comb_off = 0.0
        self.prev_pulse = 0.0
        self.since_beat = 1.0
        self.stone_ink = int(rng.integers(0, 64))
        ang = rng.uniform(0.0, TAU, 4)
        mag = rng.uniform(1.4, 2.8, 4)
        self.rip_f = np.stack([mag * np.cos(ang), mag * np.sin(ang)], 1).reshape(-1)
        self.rip_rate = rng.uniform(0.6, 1.3, 4) * np.array([1.0, -1.0, 1.0, -1.0])
        self.rip_ph = rng.uniform(0.0, TAU, 4)
        self.band_ph = float(rng.uniform(0.0, TAU))
        self.site = np.zeros(2)
        self.site_left = 0
        self._prime(params)

    def close(self) -> None:
        self.buf = None
        self.base = None
        self.paper = None
        self.P = None
        self.O = None

    # ------------------------------------------------------------------ colour
    def _paper_rgb(self, p: dict) -> np.ndarray:
        tone = min(max(float(p["paper_tone"]), 0.0), 1.0)
        base = DARK + (CREAM - DARK) * tone
        tint = _pal(0.5 + self.drift + self.ink_off, self.pal)
        return np.clip(base * 0.9 + tint * 0.1 * (0.35 + 0.65 * tone), 0.0, 1.0)

    def _count(self, p: dict) -> int:
        return int(min(max(round(float(p["colour_count"])), 2), 8))

    def _ink(self, j: int, p: dict) -> np.ndarray:
        n = self._count(p)
        t = (j % n) / n * float(p["palette_period"]) + self.drift + self.ink_off
        c = _pal(t, self.pal)
        # keep every ink readable against the ground: fold it towards the palette's opposite side if too close
        paper = self._paper_rgb(p)
        li, lp = float(c @ LUMA), float(paper @ LUMA)
        if abs(li - lp) < 0.24:
            alt = np.clip(2.0 * self.pal[0:3] - c, 0.0, 1.0)
            if abs(float(alt @ LUMA) - lp) > abs(li - lp):
                c = c + (alt - c) * (1.0 - abs(li - lp) / 0.24)
            else:                                       # still too close: push the ink's value away from the ground
                c = np.clip(c * (0.45 if lp > 0.4 else 1.0) + (0.0 if lp > 0.4 else 0.35), 0.0, 1.0)
        return c

    # ------------------------------------------------------------------ op spawning
    def _add(self, op: dict) -> bool:
        if len(self.ops) >= NOPS:
            return False
        self.ops.append(op)
        return True

    def _drop(self, x: float, y: float, r: float, rgb: np.ndarray, clear: bool, tau: float, done=False) -> None:
        self._add({"ty": DROP, "x": float(x), "y": float(y), "r": float(r), "c": tuple(float(v) for v in rgb),
                   "k": 0.25 if clear else 1.0, "age": tau if done else 0.0, "dur": tau})

    def _new_site(self, p: dict) -> None:
        rng = self.rng
        best, bd = None, -1.0
        for _ in range(5):
            c = np.array([rng.uniform(-0.8, 0.8) * self.asp, rng.uniform(-0.78, 0.78)])
            d = float(np.hypot(*(c - self.site)))
            if d > bd:
                best, bd = c, d
        self.site = best
        self.site_left = int(rng.integers(6, 15))
        self.site_rings = bool(rng.random() < 0.5)
        self.site_ink = int(rng.integers(0, 64))
        self.site_n = 0

    def _site_drop(self, p: dict, r: float, tau: float, done=False) -> None:
        rng = self.rng
        if self.site_left <= 0:
            self._new_site(p)
        self.site_left -= 1
        n = self.site_n
        self.site_n += 1
        if self.site_rings:                       # suminagashi: one ink alternating with clear water
            clear = n % 2 == 1
            ink = self.site_ink
            jit = 0.012
        else:                                     # ebru: a run of inks, the odd clear drop
            clear = bool(rng.random() < 0.2)
            ink = self.site_ink + n
            jit = 0.05
        rgb = self._paper_rgb(p) if clear else self._ink(ink, p)
        j = rng.normal(0.0, jit, 2) * (0.5 + r / 0.12)
        self._drop(self.site[0] + j[0], self.site[1] + j[1], r, rgb, clear, tau, done)

    def _stone_drop(self, p: dict, r: float, tau: float, xr: float, yr: float, done=False) -> None:
        rng = self.rng
        self.stone_ink += 1 + int(rng.integers(0, 2))
        clear = bool(rng.random() < 0.15)
        rgb = self._paper_rgb(p) if clear else self._ink(self.stone_ink, p)
        self._drop(rng.uniform(-xr, xr), rng.uniform(-yr, yr), r, rgb, clear, tau, done)

    def _comb_stroke(self, p: dict, e1: float, scale: float = 1.0, done=False) -> None:
        rng = self.rng
        dvc = float(p["drop_vs_comb"])
        s = float(p["tine_spacing"])
        th0 = math.radians(float(p["stroke_direction"])) + self.theta_jit
        amp = (0.16 + 0.22 * e1) * (0.55 + 0.6 * dvc) * scale
        dur = (3.6 - 2.2 * e1) * float(rng.uniform(0.85, 1.15))
        step = self.prog % 4
        self.prog += 1
        if self.prog % 4 == 0:
            self.theta_jit = math.radians(float(rng.uniform(-15.0, 15.0)))
        if step == 0:                              # gel-git: a comb along the stroke direction
            th, sp, a = th0, s, amp
            self.comb_off = float(rng.uniform(0.0, s))
            off = self.comb_off
        elif step == 1:                            # feather: back through the gaps, the other way
            th, sp, a, off = th0 + math.pi, s, 0.8 * amp, self.comb_off + 0.5 * s
        elif step == 2:                            # nonpareil: a fine comb across
            th, sp, a, off = th0 + 0.5 * math.pi, 0.45 * s, 0.6 * amp, float(rng.uniform(0.0, s))
        else:                                      # peacock / serpentine: a wavy rake across
            th = th0 - 0.5 * math.pi
            mx, my = math.cos(th), math.sin(th)
            self._add({"ty": WAVY, "x": 0.0, "y": 0.0, "mx": mx, "my": my, "om": TAU / (2.2 * s),
                       "ph": float(rng.uniform(0.0, TAU)), "amp": (0.25 + 0.15 * e1) * s * (0.6 + 0.6 * dvc) * scale,
                       "age": dur if done else 0.0, "dur": dur})
            return
        mx, my = math.cos(th), math.sin(th)
        nx, ny = -my, mx
        self._add({"ty": COMB, "x": nx * off, "y": ny * off, "mx": mx, "my": my, "s": sp,
                   "lam": max(0.14 * sp, 0.004), "amp": a, "age": dur if done else 0.0, "dur": dur})

    def _vortex(self, p: dict, e1: float, done=False) -> None:
        rng = self.rng
        va = float(p["vortex_amount"])
        if va <= 1e-3:
            return
        dur = (5.0 - 2.4 * e1) * float(rng.uniform(0.85, 1.15))
        self._add({"ty": VORTEX, "x": float(rng.uniform(-0.7, 0.7) * self.asp), "y": float(rng.uniform(-0.65, 0.65)),
                   "R": float(rng.uniform(0.0, 0.3)),
                   "wd": float(rng.uniform(0.15, 0.38)) * (0.7 + 0.3 * float(p["drop_size"])),
                   "amp": float(rng.choice([-1.0, 1.0])) * va * float(rng.uniform(1.6, 3.6)) * (0.7 + 0.5 * e1),
                   "age": dur if done else 0.0, "dur": dur})

    # ------------------------------------------------------------------ packing / baking
    def _pack(self, ops: list[dict]) -> None:
        O = self.Oh
        for i, o in enumerate(ops):
            b = i * NOPF
            u = min(o["age"] / o["dur"], 1.0) if o["dur"] > 0 else 1.0
            ty = o["ty"]
            if ty == DROP:
                g = 1.0 - (1.0 - u) ** 3
                O[b:b + 8] = (0.0, o["x"], o["y"], o["r"] * g, *o["c"], o["k"])
            else:
                g = u * u * (3.0 - 2.0 * u)
                if ty == COMB:
                    O[b:b + 8] = (1.0, o["x"], o["y"], o["mx"], o["my"], o["s"], o["lam"], o["amp"] * g)
                elif ty == WAVY:
                    O[b:b + 8] = (2.0, o["x"], o["y"], o["mx"], o["my"], o["om"], o["ph"], o["amp"] * g)
                else:
                    O[b:b + 6] = (3.0, o["x"], o["y"], o["R"], o["wd"], o["amp"] * g)

    def _globals(self, p: dict, e1: float, k: float) -> None:
        P = self.Ph
        pt = min(max(float(p["paper_texture"]), 0.0), 1.0)
        P[0] = self.pxn
        P[1] = self.BX
        P[2] = self.BY
        P[3] = 0.06 + 0.42 * pt
        P[4] = 0.0055
        P[5] = 0.04 + 0.22 * pt
        P[6] = pt
        P[7] = float(p["intensity"]) * (1.0 + 0.035 * k)
        P[8] = (0.0025 + 0.009 * e1) * (1.0 + 0.5 * k)
        P[9:17] = self.rip_f
        P[17:21] = self.rip_ph
        P[21] = 0.22
        P[32:35] = self._paper_rgb(p)
        n = self._count(p)
        P[35] = n
        th = math.radians(float(p["stroke_direction"]))
        P[36], P[37] = -math.sin(th), math.cos(th)
        P[38] = 0.09 * float(p["drop_size"])
        P[39] = 0.3
        P[40] = 2.5
        P[41] = self.band_ph
        for j in range(8):
            P[42 + 3 * j:45 + 3 * j] = self._ink(j, p) if j < n else 0.0

    def _bake(self, n: int, use_base: bool, dst: int) -> None:
        launch_2d(self.k_bake, self.bw, self.bh, (self.base[dst], self.base[1 - dst], self.O, np.int32(n),
                                                  np.int32(self.bw), np.int32(self.bh), np.int32(1 if use_base else 0),
                                                  self.P))

    def _prime(self, p: dict) -> None:
        """Lay down a full sheet so the scene arrives formed: a stone field, a gel-git comb, ring sites, a swirl."""
        rng = self.rng
        e1 = _ss(0.0, 0.65, float(p["energy"]))
        ds = float(p["drop_size"])
        dvc = float(p["drop_vs_comb"])
        for _ in range(24):
            self._stone_drop(p, ds * float(rng.uniform(0.14, 0.28)), 0.4, self.BX * 0.95, self.BY * 0.95, done=True)
        if dvc > 0.02:
            self._comb_stroke(p, e1, 1.2, done=True)
            self._comb_stroke(p, e1, 1.2, done=True)
        for _ in range(3):
            self._new_site(p)
            self.site_left = 6
            for _ in range(6):
                self._site_drop(p, ds * float(rng.uniform(0.06, 0.12)), 0.4, done=True)
        self._vortex(p, e1, done=True)
        if dvc > 0.02:
            self._comb_stroke(p, e1, 0.8, done=True)
            self._comb_stroke(p, e1, 0.8, done=True)
        self._new_site(p)
        for _ in range(3):
            self._site_drop(p, ds * float(rng.uniform(0.07, 0.12)), 0.4, done=True)
        self._globals(p, e1, 0.0)
        self.P.set(self.Ph)
        self._pack(self.ops)
        self.O.set(self.Oh)
        self._bake(len(self.ops), False, 0)
        self.bi = 0
        self.ops = []

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        ep = _ss(0.0, 0.65, e)
        a = (1.0 - math.exp(-dt / 1.0)) if dt > 0 else 1.0
        self.e_s = ep if self.e_s is None else self.e_s + (ep - self.e_s) * a
        e1 = self.e_s
        k = pulse * (0.35 + 0.65 * e)
        st = dt * spd
        rng = self.rng

        # palette (session palette wins; eased; only new ink picks it up) and drift
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * st, 1000.0)
        rip = (0.12 + 0.3 * e1) * st
        self.rip_ph = np.mod(self.rip_ph + self.rip_rate * rip, TAU)

        for o in self.ops:
            o["age"] += st

        ds = float(p["drop_size"])
        dvc = float(p["drop_vs_comb"])
        # beat: a rising edge drops ink at the ring site (radius grows over ~200 ms)
        self.since_beat += dt
        if pulse - self.prev_pulse > 0.15 and self.since_beat > 0.12 and spd > 0.05:
            jump = min(pulse - self.prev_pulse, 1.0)
            self._site_drop(p, ds * (0.05 + 0.08 * e1) * (0.75 + 0.5 * jump), 0.2)
            self.since_beat = 0.0
        self.prev_pulse = pulse
        # ambient drops (half at the ring site, half scattered 'stone' drops)
        self.drop_acc += (0.3 + 1.5 * e1 ** 1.3) * (1.0 - 0.7 * dvc) * st
        while self.drop_acc >= 1.0:
            self.drop_acc -= 1.0
            r = ds * (0.07 + 0.12 * e1) * float(rng.uniform(0.6, 1.4))
            tau = 0.45 * float(rng.uniform(0.8, 1.2))
            if rng.random() < 0.55:
                self._site_drop(p, r, tau)
            else:
                self._stone_drop(p, r, tau, 0.92 * self.asp, 0.9)
        # strokes: the comb programme (gel-git, feather, nonpareil, wavy) and vortex swirls
        self.comb_acc += dvc ** 0.8 * (0.07 + 0.30 * e1) * st
        if self.comb_acc >= 1.0:
            self.comb_acc -= 1.0
            self._comb_stroke(p, e1)
        self.vort_acc += float(p["vortex_amount"]) * (0.025 + 0.07 * e1) * st
        if self.vort_acc >= 1.0:
            self.vort_acc -= 1.0
            self._vortex(p, e1)

        # bake the oldest finished ops into the sheet once the ring is getting full
        nb = 0
        if len(self.ops) >= BAKE_AT:
            while nb < min(BAKE_MAX, len(self.ops)) and self.ops[nb]["age"] >= self.ops[nb]["dur"]:
                nb += 1

        self._globals(p, e1, k)
        self.P.set(self.Ph)
        self._pack(self.ops)
        self.O.set(self.Oh)
        if nb > 0:
            self._bake(nb, True, 1 - self.bi)
            self.bi = 1 - self.bi
        n = len(self.ops) - nb
        launch_2d(self.k_render, self.w, self.h, (self.buf, self.base[self.bi], self.paper, self.O[nb * NOPF:],
                                                  np.int32(n), np.int32(self.w), np.int32(self.h),
                                                  np.int32(self.bw), np.int32(self.bh), self.P))
        if nb > 0:
            del self.ops[:nb]
        present(self.ctx, self.buf, out)
