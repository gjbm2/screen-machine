"""Drift clouds: fluffy 2D clouds over a palette-driven sky, scrolling with the wind; lightning glows inside them on the beat.

Source: https://www.shadertoy.com/view/4tdSWr (as relicensed in xscreensaver driftclouds.glsl; https://man.archlinux.org/man/driftclouds.6.en)
Author: drift (2016, Shadertoy 4tdSWr, as relicensed in xscreensaver driftclouds.glsl)
License: LicenseRef-drift-permission ('Anyone and everyone who wishes to use this shader I give my permission to use it in any way that you choose.')
license_class: permissive
Ported from GLSL by smfx (ideas/changes: see below)

The cloud model is drift's: a small fbm warp q, a ridged stack r and a plain fbm stack f sampled along the same
octave chain (one simplex lookup serves both), f *= r + f, a colour stack c plus a ridged colour stack c1 at 2x and
3x the scale, and the mask clamp(cover + alpha * f * r + c).  Changes for the live renderer:
  * simplex gradients from an integer hash over a 256-periodic skewed lattice (no sin-hash), so every per-octave
    offset is host-integrated and wrapped along the lattice's period vectors: the wind never loses precision and
    never jumps, however long the scene runs;
  * each octave gets its own offset: the common wind (rotated/scaled through the octave matrix) plus a per-octave
    'boil' drift (replaces drift's per-octave '+ time'), so fine detail evolves faster than the big forms;
  * the sky is palette-driven and morphs continuously day -> sunset (low sun, hot horizon, clouds with lit thin
    edges) -> night neon (near-black sky with stars, neon horizon, clouds glowing in palette colours at their
    thin edges); ``sky`` is one continuous knob that cross-weights the three shadings;
  * an optional second, higher cloud deck (``layering``), stretched along the wind, slower and hazier (parallax);
  * lightning: on each detected beat onset a strike centre is placed at random (host numpy rng); its glow lights
    the clouds from inside (modulated by the ridged colour stack, so internal structure flashes), plus a faint
    sheet flash over all cloud.  The previous strike keeps its own decaying amplitude, so nothing pops.

Macros: energy -> wind speed (0.012 + 0.06 e^1.2 uv/s), boil rate (turbulent evolution), octave gain (turbulence:
fine octaves fade out at low energy, full at high), cover (+-0.07), cloud light contrast and lightning reach.
pulse -> in-cloud lightning (amplitude only, gain ``lightning``) and a brightness flash.  speed scales the wind and
boil; drift moves the sky hue through the palette; intensity is output gain.
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

# octave stacks: (name, octaves, scale factor x cloud_scale, screen-wind factor)
STACKS = [("q", 5, 0.5, 0.7), ("rf", 7, 1.0, 1.0), ("c", 6, 2.0, 1.12), ("c1", 6, 3.0, 1.25), ("back", 5, 1.7, 0.4)]
OFF_BASE = 48                      # P index of the first offset pair
N_OFF = sum(s[1] for s in STACKS)  # 29 pairs
NP = OFF_BASE + 2 * N_OFF + 4
LAT = 256.0                        # simplex lattice period (hash uses ix & 255)
K1 = 0.366025404
K2 = 0.211324865
_M = np.array([[1.6, -1.2], [1.2, 1.6]])       # GLSL mat2(1.6, 1.2, -1.2, 1.6) applied as m * v
MPOW = [np.linalg.matrix_power(_M, k) for k in range(8)]

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 cloud scale | 15 cover | 16 cloud alpha | 17 dark | 18 light
#  19 sky tint | 20 w_day | 21 w_sunset | 22 w_night | 23 octave gain | 24 output gain
#  25 strike A amp | 26 Ax | 27 Ay | 28 strike B amp | 29 Bx | 30 By | 31 1/R^2 | 32 sheet flash
#  33 layering | 34 back cover | 35..38 back stretch matrix S (row major) | 39 twinkle phase
#  40 sun x | 41 sun y | 42 flash gain
#  48.. offset pairs, stacks in STACKS order
_SRC = HELPERS + PALETTE_CUDA + r"""
#define OQ  %(oq)d
#define ORF %(orf)d
#define OC  %(oc)d
#define OC1 %(oc1)d
#define OB  %(ob)d
__device__ __forceinline__ float2 mrot(float2 v) { return make_float2(1.6f * v.x - 1.2f * v.y, 1.2f * v.x + 1.6f * v.y); }
__device__ __forceinline__ float2 grad2(int ix, int iy, unsigned int sd) {
    const unsigned int h = hash_u(((unsigned int)(ix & 255) | ((unsigned int)(iy & 255) << 8)) ^ sd);
    return make_float2((float)(h & 0xffffu) * (2.f / 65535.f) - 1.f, (float)(h >> 16) * (2.f / 65535.f) - 1.f);
}
// 2D simplex noise (drift's form), gradients from a 256-periodic skewed lattice
__device__ float snoise(float2 p, unsigned int sd) {
    const float K1 = 0.366025404f, K2 = 0.211324865f;
    const float s = (p.x + p.y) * K1;
    const float fx = floorf(p.x + s), fy = floorf(p.y + s);
    const float t = (fx + fy) * K2;
    const float ax = p.x - fx + t, ay = p.y - fy + t;
    const int ox = ax > ay ? 1 : 0, oy = 1 - ox;
    const float bx = ax - ox + K2, by = ay - oy + K2;
    const float cx = ax - 1.f + 2.f * K2, cy = ay - 1.f + 2.f * K2;
    const int ix = (int)fx, iy = (int)fy;
    float ha = fmaxf(0.5f - ax * ax - ay * ay, 0.f), hb = fmaxf(0.5f - bx * bx - by * by, 0.f);
    float hc = fmaxf(0.5f - cx * cx - cy * cy, 0.f);
    ha *= ha; hb *= hb; hc *= hc;
    float n = 0.f;
    if (ha > 0.f) { const float2 g = grad2(ix, iy, sd); n += ha * ha * (ax * g.x + ay * g.y); }
    if (hb > 0.f) { const float2 g = grad2(ix + ox, iy + oy, sd); n += hb * hb * (bx * g.x + by * g.y); }
    if (hc > 0.f) { const float2 g = grad2(ix + 1, iy + 1, sd); n += hc * hc * (cx * g.x + cy * g.y); }
    return 70.f * n;
}
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
__device__ __forceinline__ float3 f3(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float3 add3(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 mul3(float3 a, float k) { return make_float3(a.x * k, a.y * k, a.z * k); }
__device__ __forceinline__ float3 lerp3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float3 sat3(float3 a) { return make_float3(clamp01(a.x), clamp01(a.y), clamp01(a.z)); }

// cloud colour for the three sky moods, cross-weighted.  lit = drift's clamp(dark + light * c); thin = light passing
// through thin cloud; hz = 1 at the horizon; sunk = proximity to the low sun
__device__ float3 cloud_col(float lit, float thin, float c, float hz, float sunk, float3 sky,
                            const float* __restrict__ P) {
    const float* PAL = P;
    const float period = P[12], drift = P[13];
    float3 col = f3(0.f, 0.f, 0.f);
    if (P[20] > 0.f) {                                    // day: drift's white clouds, faintly palette-tinted
        const float3 tc = pal(drift + 0.5f + 0.2f * period * c, PAL);
        const float3 base = lerp3(f3(1.1f, 1.1f, 0.95f), mul3(tc, 1.25f), 0.14f);
        col = add3(col, mul3(sat3(add3(mul3(sky, P[19]), mul3(base, lit))), P[20]));
    }
    if (P[21] > 0.f) {                                    // sunset: dark warm body, thin parts lit by the low sun
        const float3 top = mul3(pal(drift + 0.5f + 0.45f * period, PAL), 0.42f);
        const float3 hor = bright_pal(drift + 0.04f, PAL);
        const float3 body = mul3(lerp3(top, mul3(hor, 0.55f), 0.25f + 0.45f * hz), 0.35f + 0.75f * lit);
        const float3 lin = bright_pal(drift + 0.08f + 0.12f * period * c, PAL);
        const float lk = thin * (0.55f + 0.45f * hz) * (0.75f + 1.6f * sunk);
        const float3 s = add3(add3(body, mul3(lin, lk)), mul3(sky, 0.18f));
        col = add3(col, mul3(sat3(s), P[21]));
    }
    if (P[22] > 0.f) {                                    // night neon: dark silhouettes, neon glow in thin cloud
        const float3 nc = bright_pal(drift + 0.3f + period * (0.45f * c + 0.2f * (1.f - hz)), PAL);
        const float3 body = add3(mul3(sky, 0.7f), f3(0.035f * lit, 0.035f * lit, 0.045f * lit));
        const float g = thin * (0.45f + 0.9f * hz) * 0.95f + 0.16f * lit * lit;
        col = add3(col, mul3(sat3(add3(body, mul3(nc, g))), P[22]));
    }
    return col;
}

extern "C" __global__
void drift_clouds(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                  const unsigned int sd)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float* PAL = P;
    const float period = P[12], drift = P[13];
    const float invH = 1.f / (float)H, asp = (float)W * invH;
    const float2 uv = make_float2((x + 0.5f) * invH - 0.5f * asp, 0.5f - (y + 0.5f) * invH);   // centred, y up
    const float hu = 1.f - (y + 0.5f) * invH, hz = 1.f - hu;
    const float cs = P[14], g = P[23];
    const unsigned int s1 = hash_u(sd ^ 0x1234567u), s2 = hash_u(sd ^ 0x89abcdeu), s3 = hash_u(sd ^ 0x5bd1e99u);

    // q: small fbm warp
    float q = 0.f;
    {
        float2 b = make_float2(uv.x * cs * 0.5f, uv.y * cs * 0.5f);
        float a = 0.1f;
        #pragma unroll
        for (int k = 0; k < 5; ++k) {
            q += a * snoise(make_float2(b.x + P[OQ + 2 * k], b.y + P[OQ + 2 * k + 1]), s1);
            b = mrot(b); a *= 0.4f;
        }
    }
    // ridged (r) + noise (f) shape, one lookup per octave
    float r = 0.f, f = 0.f;
    {
        float2 b = make_float2(uv.x * cs - q, uv.y * cs - q);
        float wr = 0.8f, wf = 0.7f;
        #pragma unroll
        for (int k = 0; k < 7; ++k) {
            const float n = snoise(make_float2(b.x + P[ORF + 2 * k], b.y + P[ORF + 2 * k + 1]), sd);
            r += fabsf(wr * n); f += wf * n;
            b = mrot(b); wr *= 0.7f * g; wf *= 0.6f * g;
        }
    }
    f *= r + f;
    // colour (c) and ridged colour (c1)
    float c = 0.f, c1 = 0.f;
    {
        float2 b = make_float2(uv.x * cs * 2.f - q, uv.y * cs * 2.f - q);
        float w = 0.4f;
        #pragma unroll
        for (int k = 0; k < 6; ++k) {
            c += w * snoise(make_float2(b.x + P[OC + 2 * k], b.y + P[OC + 2 * k + 1]), s2);
            b = mrot(b); w *= 0.6f * g;
        }
        b = make_float2(uv.x * cs * 3.f - q, uv.y * cs * 3.f - q);
        w = 0.4f;
        #pragma unroll
        for (int k = 0; k < 6; ++k) {
            c1 += fabsf(w * snoise(make_float2(b.x + P[OC1 + 2 * k], b.y + P[OC1 + 2 * k + 1]), s3));
            b = mrot(b); w *= 0.6f * g;
        }
    }
    c += c1;
    const float dens = P[15] + P[16] * f * r + c;
    const float mask = clamp01(dens);
    const float lit = clamp01(P[17] + P[18] * c);
    const float thin = mask * __expf(-1.1f * fmaxf(dens - 0.15f, 0.f));

    // sky: palette sample indexed by height, three moods
    const float sdx = uv.x - P[40], sdy = (hu - 0.5f) - P[41];
    const float sd2 = sdx * sdx + sdy * sdy * 1.6f;
    const float sunk = __expf(-sd2 * 3.5f);
    float3 sky = f3(0.f, 0.f, 0.f);
    {
        const float3 p0 = pal(drift + period * 0.3f * hu, PAL);
        if (P[20] > 0.f) {
            const float3 d = mul3(lerp3(p0, f3(1.f, 1.f, 1.f), 0.22f + 0.28f * hz), 0.78f + 0.3f * hz);
            sky = add3(sky, mul3(sat3(d), P[20]));
        }
        if (P[21] > 0.f) {
            const float3 hor = bright_pal(drift + 0.04f, PAL);
            const float3 top = mul3(pal(drift + 0.5f + 0.45f * period, PAL), 0.42f);
            float3 s = lerp3(hor, top, powf(smooth(0.f, 0.9f, hu), 0.75f));
            const float3 sc = bright_pal(drift + 0.1f, PAL);
            const float sun = __expf(-sd2 * 9.f) * 0.55f + __expf(-sd2 * 140.f) * 0.6f + smooth(0.0026f, 0.0016f, sd2);
            s = add3(s, mul3(lerp3(sc, f3(1.f, 0.97f, 0.88f), 0.5f), sun));
            sky = add3(sky, mul3(sat3(s), P[21]));
        }
        if (P[22] > 0.f) {
            float3 s = mul3(pal(drift + 0.25f + period * 0.3f * hu, PAL), 0.03f + 0.11f * hz * hz);
            const float3 nh = bright_pal(drift + 0.6f, PAL);
            s = add3(s, mul3(nh, 0.38f * __expf(-hu * 5.5f)));
            // stars (static sky, slow twinkle)
            const float h = hash1((unsigned int)x, (unsigned int)y, sd ^ 0x51u);
            if (h > 0.9972f) {
                const float tw = 0.6f + 0.4f * __sinf(P[39] + 40.f * h * 6.2831853f);
                const float st = (h - 0.9972f) * (1.f / 0.0028f) * tw * smooth(0.05f, 0.4f, hu) * 0.9f;
                s = add3(s, f3(st, st, st * 1.05f));
            }
            sky = add3(sky, mul3(sat3(s), P[22]));
        }
    }

    // optional higher deck, stretched along the wind, slower and hazier
    float3 col = sky;
    if (P[33] > 0.001f) {
        float2 b = make_float2((P[35] * uv.x + P[36] * uv.y) * cs * 1.7f - 0.5f * q,
                               (P[37] * uv.x + P[38] * uv.y) * cs * 1.7f - 0.5f * q);
        float rb = 0.f, fb = 0.f, wr = 0.8f, wf = 0.7f;
        #pragma unroll
        for (int k = 0; k < 5; ++k) {
            const float n = snoise(make_float2(b.x + P[OB + 2 * k], b.y + P[OB + 2 * k + 1]), s3 ^ 0x77u);
            rb += fabsf(wr * n); fb += wf * n;
            b = mrot(b); wr *= 0.7f * g; wf *= 0.6f * g;
        }
        fb *= rb + fb;
        const float db = P[34] + 6.f * fb * rb + 0.4f * c;
        const float mb = clamp01(db) * P[33] * 0.85f;
        const float litb = clamp01(P[17] + 0.08f + P[18] * (1.4f * fb + 0.3f * c));
        const float thinb = clamp01(db) * __expf(-1.1f * fmaxf(db - 0.15f, 0.f));
        const float3 cb = cloud_col(litb, thinb, 0.6f * c, hz, sunk, sky, P);
        col = lerp3(col, lerp3(cb, sky, 0.38f), mb);
    }
    // front deck
    const float3 cf = cloud_col(lit, thin, c, hz, sunk, sky, P);
    col = lerp3(col, cf, mask);

    // lightning inside the clouds (screen blend, amplitude-only beat response)
    const float ax = uv.x - P[26], ay = uv.y - P[27], bx = uv.x - P[29], by = uv.y - P[30];
    float L = P[25] * __expf(-(ax * ax + ay * ay * 1.3f) * P[31]) + P[28] * __expf(-(bx * bx + by * by * 1.3f) * P[31]);
    L = L * (0.2f + 0.8f * mask) * (0.45f + 1.1f * clamp01(c1 * 2.2f)) + P[32] * mask * (0.6f + 0.6f * clamp01(c));
    if (L > 0.f) {
        const float3 lc0 = bright_pal(drift + 0.62f, PAL);
        const float3 lc = lerp3(f3(0.86f, 0.9f, 1.f), lc0, 0.35f);
        const float l = clamp01(L);
        col.x = 1.f - (1.f - col.x) * (1.f - l * lc.x);
        col.y = 1.f - (1.f - col.y) * (1.f - l * lc.y);
        col.z = 1.f - (1.f - col.z) * (1.f - l * lc.z);
    }
    const float gg = 255.f * P[24] * P[42];
    out[y * W + x] = pack(make_float4(col.x * gg, col.y * gg, col.z * gg, 255.f));
}
"""

_offs = {}
_i = OFF_BASE
for _n, _k, _s, _w in STACKS:
    _offs[_n] = _i
    _i += 2 * _k
_SRC = _SRC % {"oq": _offs["q"], "orf": _offs["rf"], "oc": _offs["c"], "oc1": _offs["c1"], "ob": _offs["back"]}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _wrap_lattice(o: np.ndarray) -> np.ndarray:
    """Wrap offset pairs (N, 2) along the simplex lattice's period vectors (noise is 256-periodic in skewed space)."""
    s = o + (o[:, :1] + o[:, 1:]) * K1
    s -= LAT * np.floor(s / LAT)
    return s - (s[:, :1] + s[:, 1:]) * K2


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


class DriftClouds(Source):
    name = "drift_clouds"
    label = "Drift clouds"
    description = ("Fluffy 2D clouds drifting over a palette sky that morphs from day to sunset to night neon, with "
                   "lightning glowing inside the clouds on the beat.")
    params = _order({
        "cloud_cover": num(0.0, 1.0, 0.5, "Cloud cover", tier="regional", basic=True),
        "sky": num(0.0, 1.0, 0.5, "Sky (day -> sunset -> night neon)", tier="regional", basic=True),
        "cloud_scale": num(0.4, 3.0, 1.1, "Cloud scale (big -> small)", tier="regional"),
        "dark_light": num(0.0, 1.0, 0.5, "Dark / light balance", tier="regional"),
        "layering": num(0.0, 1.0, 0.5, "Upper cloud layer", tier="regional"),
        "wind_direction": num(0.0, 360.0, 160.0, "Wind direction", step=1.0, unit="°", tier="regional"),
        "lightning": num(0.0, 2.0, 1.0, "Beat lightning", tier="regional"),
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.015),
    }, ["cloud_cover", "sky", "cloud_scale", "dark_light", "layering", "wind_direction", "lightning",
        "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _OPTIONS)
        self.k = mod.get_function("drift_clouds")
        self.Ph = np.zeros(NP, np.float32)
        self.P = cp.zeros(NP, cp.float32)
        seed = int(params["seed"])
        self.rng = np.random.default_rng(seed)
        self.sd = np.uint32((seed * 2654435761) & 0xFFFFFFFF)
        # per-octave offsets (random start so seeds differ) and per-octave boil directions (golden-angle spread)
        self.off = self.rng.uniform(0.0, LAT, (N_OFF, 2))
        self.off = _wrap_lattice(self.off)
        ang = self.rng.uniform(0, 2 * math.pi) + np.arange(N_OFF) * 2.39996323
        self.bdir = np.stack([np.cos(ang), np.sin(ang)], axis=1)
        self.drift = 0.0
        self.twinkle = 0.0
        self.clock = 0.0
        self.prev_pulse = 0.0
        self.last_strike = -1.0
        asp = self.w / self.h
        self.strike_a = (0.25 * asp, 0.18)
        self.strike_b = (-0.3 * asp, 0.1)
        self.amp_a = 0.0
        self.amp_b = 0.0

    def _new_strike(self, asp: float) -> tuple[float, float]:
        return (float(self.rng.uniform(-0.8, 0.8)) * 0.5 * asp, float(self.rng.uniform(-0.2, 0.4)))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        cs = float(p["cloud_scale"])
        asp = self.w / self.h
        self.clock += dt

        # wind (uv units / s) and per-octave boil (noise units / s), host-integrated per octave, lattice-wrapped
        wind = (0.012 + 0.06 * e ** 1.2) * spd
        boil = (0.025 + 0.09 * e ** 1.3) * spd
        th = math.radians(float(p["wind_direction"]))
        wv = np.array([math.cos(th), math.sin(th)]) * wind
        # stretch matrix for the upper deck: compress noise coords along the wind (features elongate along it)
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        S = R @ np.diag([0.45, 1.0]) @ R.T
        if dt > 0:
            i = 0
            for name, n, sig, wf in STACKS:
                base = (S @ wv) if name == "back" else wv
                v0 = base * (sig * cs * wf)
                for k in range(n):
                    self.off[i] -= (MPOW[k] @ v0) * dt
                    self.off[i] += self.bdir[i] * boil * dt
                    i += 1
            self.off = _wrap_lattice(self.off)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)
        self.twinkle = _wrap(self.twinkle + 1.3 * dt, 2 * math.pi)

        # lightning: new strike centre only on a beat onset; the old one keeps its own decaying amplitude
        if pulse - self.prev_pulse > 0.15 and self.clock - self.last_strike > 0.12:
            self.strike_b = self.strike_a
            self.amp_b = self.amp_a
            self.strike_a = self._new_strike(asp)
            self.last_strike = self.clock
        self.prev_pulse = pulse
        if dt > 0:
            self.amp_b *= math.exp(-dt / 0.12)
        ts = self.clock - self.last_strike
        flick = 0.8 + 0.2 * math.cos(2 * math.pi * 8.5 * min(ts, 4.0))
        lg = float(p["lightning"])
        self.amp_a = (pulse ** 1.3) * lg * (0.6 + 0.5 * e) * flick * 1.15

        sky = min(max(float(p["sky"]), 0.0), 1.0)
        wd = 1.0 - _ss(0.0, 0.5, sky)
        wn = _ss(0.5, 1.0, sky)
        ws = max(1.0 - wd - wn, 0.0)
        bal = float(p["dark_light"])

        P = self.Ph
        P[0:12] = palette_coeffs(p["palette"])
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = cs
        P[15] = -0.38 + 1.0 * float(p["cloud_cover"]) + 0.14 * (e - 0.5)
        P[16] = 8.0
        P[17] = 0.18 + 0.62 * bal
        P[18] = (0.45 - 0.2 * bal) * (0.85 + 0.3 * e)
        P[19] = 0.5
        P[20], P[21], P[22] = wd, ws, wn
        P[23] = 0.78 + 0.32 * e
        P[24] = float(p["intensity"])
        P[25] = self.amp_a
        P[26], P[27] = self.strike_a
        P[28] = self.amp_b
        P[29], P[30] = self.strike_b
        rad = 0.2 + 0.14 * e
        P[31] = 1.0 / (rad * rad)
        P[32] = 0.16 * pulse * lg
        P[33] = float(p["layering"])
        P[34] = -0.55 + 0.8 * float(p["cloud_cover"])
        P[35:39] = S.reshape(-1)
        P[39] = self.twinkle
        P[40], P[41] = 0.32 * asp, -0.36
        P[42] = 1.0 + 0.1 * pulse * min(lg, 1.0)
        P[OFF_BASE:OFF_BASE + 2 * N_OFF] = self.off.reshape(-1)
        self.P.set(P)

        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.sd))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
