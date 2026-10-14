"""Point Morph: up to a million glowing points flowing between mathematical shapes.

Source: Original (inspired by https://github.com/mrdoob/three.js/blob/dev/examples/webgl_points_dynamic.html)
Author: smfx (original; all shapes generated in-kernel, no external assets)
License: MIT (original)
license_class: permissive
Notes: written from scratch.  Every point owns a fixed parametric coordinate (u, v) from the R2 low-discrepancy
sequence (so the first n points are always a uniform subsample) plus two per-point hashes; one fused generator kernel
evaluates the point on BOTH target shapes in-kernel (no double buffers), blends them with a per-point staggered
morph wave, displaces it with curl noise (splat3d sp_curl), the pulse "breath" and the drop explosion, and writes
pos (xyz + weight), the palette index (along u) and a depth-of-field size multiplier.  Splat3D then bins and
splats the cloud at 0.5 scale and resolve() blooms and tonemaps straight into the 4K output.

Shapes (all ~unit radius): sphere, (2,3) torus knot tube, Klein bottle (the classic "bottle" immersion), animated
spherical-harmonic blob, gyroid shell clipped to a ball (points Newton-projected onto the surface), (3,2,7)
Lissajous knot tube.

Macros (es = energy smoothed ~0.3 s):
  energy -> curl-dispersion amplitude (e^1.3), noise flow rate, spin and camera orbit rate, sparkle amount and
            rate, point count (density x (0.55 + 0.45 es)), brightness and bloom;
  pulse  -> explosive radial breath (displacement AMPLITUDE x envelope, so it decays with the beat), spin-rate
            kick, flash and bloom gain;
  drops  -> a fast rise of energy (fast vs slow smoothed energy) explodes the cloud (brief streak trails) and it
            reassembles into the NEXT shape pair (shape_a + k, shape_b + k); ``cycle`` also advances it on a timer;
  speed scales every rate; drift_speed moves the palette along u; intensity is exposure.
Perf plan: 1M-point generator (~0.6 ms) + splat3d binned points at 0.5 scale + glow + 4K resolve.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.splat3d import (SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D, fibonacci_sphere, look_at,
                                                 mat_euler, mat_mul, mat_rot, mat_trs, orbit)
from stream_service.plugins._lib.upsample import internal_size

TAU = 2.0 * math.pi
SHAPES = ["sphere", "torus_knot", "klein_bottle", "harmonic", "gyroid", "lissajous"]
N_MAX = 1_000_000
N_DUST = 30_000
NP = 40

# P layout (floats)
#  0..3 shape ids: old pair A, B | new pair A, B   4 morph   5 transition T (old -> new pair)   6 stagger spread
#  7 dispersion amplitude   8 noise scale   9..11 noise offset   12 explode envelope   13 breath (pulse)
#  14 swirl   15 twist phase   16 transit amount   17, 18 blob phases   19 sparkle amount   20 sparkle phase (0..1)
#  21 dof amount   22 focus depth   23..26 depth row (model-view row 2)   28 gyroid frequency   29 tube scale
#  30 fraction of points per line family (two families; the rest is surface haze)
_SRC = SPLAT3D_SRC + r"""
#define PM_TAU 6.28318530718f
#define PM_PI 3.14159265359f

__device__ __forceinline__ float4 pm_mix4(float4 a, float4 b, float t) {
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t);
}
// tube around a curve point c with tangent T (unnormalised): angle ang, radius rad.  The frame may flip where the
// helper axis switches, which is invisible because points are spread uniformly round the tube.
__device__ __forceinline__ float3 pm_tube(float3 c, float3 T, float ang, float rad) {
    T = sp_mul3(T, rsqrtf(fmaxf(sp_dot3(T, T), 1e-14f)));
    const float3 a = fabsf(T.y) < 0.9f ? sp_f3(0.f, 1.f, 0.f) : sp_f3(1.f, 0.f, 0.f);
    float3 N = sp_cross3(T, a);
    N = sp_mul3(N, rsqrtf(fmaxf(sp_dot3(N, N), 1e-14f)));
    const float3 B = sp_cross3(T, N);
    float s, c2;
    __sincosf(ang, &s, &c2);
    return sp_add3(c, sp_add3(sp_mul3(N, rad * c2), sp_mul3(B, rad * s)));
}
__device__ __forceinline__ float3 pm_tknot(float t) {          // (2,3) torus knot in the xz plane, radius ~1.1
    float s2, c2, s3, c3;
    __sincosf(2.f * t, &s2, &c2);
    __sincosf(3.f * t, &s3, &c3);
    const float r = (2.f + c3) * 0.37f;
    return sp_f3(r * c2, -0.42f * s3, r * s2);
}
__device__ __forceinline__ float3 pm_liss(float t) {           // Lissajous knot (3, 2, 7)
    return sp_f3(1.05f * __cosf(3.f * t + 0.7f), 1.05f * __cosf(2.f * t + 0.2f), 1.05f * __cosf(7.f * t));
}

__device__ __forceinline__ float pm_q(float x, float S) { return (floorf(x * S) + 0.5f) / S; }

// shape id -> xyz + weight; (u, v) fixed parametric coordinate, w3 a fixed per-point hash; cls 0 = surface haze,
// 1 / 2 = the point sits on a parametric line family (ribs, meridians, latitudes, contours): crisp filaments
__device__ float4 pm_shape(int id, float u, float v, float w3, int cls, const float* __restrict__ P) {
    const float tube = P[29];
    if (id == 1) {                                              // torus knot tube (ribs)
        if (cls) u = pm_q(u, 110.f);
        const float t = PM_TAU * u;
        const float3 T = sp_sub3(pm_tknot(t + 2e-3f), pm_tknot(t - 2e-3f));
        const float3 p = pm_tube(pm_tknot(t), T, PM_TAU * v, 0.15f * tube * (0.86f + 0.14f * w3));
        return make_float4(p.x, p.y, p.z, 1.f);
    }
    if (id == 2) {                                              // Klein bottle (classic bottle immersion)
        if (cls == 1) u = pm_q(u, 34.f);
        if (cls == 2) v = pm_q(v, 18.f);
        float su, cu, sv, cv;
        __sincosf(PM_PI * u, &su, &cu);
        __sincosf(PM_TAU * v, &sv, &cv);
        const float c2 = cu * cu, c3 = c2 * cu, c4 = c2 * c2, c5 = c4 * cu, c6 = c4 * c2, c7 = c6 * cu;
        const float x = -2.f / 15.f * cu * (3.f * cv - 30.f * su + 90.f * c4 * su - 60.f * c6 * su + 5.f * cu * cv * su);
        const float y = -1.f / 15.f * su * (3.f * cv - 3.f * c2 * cv - 48.f * c4 * cv + 48.f * c6 * cv - 60.f * su
                        + 5.f * cu * cv * su - 5.f * c3 * cv * su - 80.f * c5 * cv * su + 80.f * c7 * cv * su);
        const float z = 2.f / 15.f * (3.f + 5.f * cu * su) * sv;
        const float k = 1.f / 1.9f;
        return make_float4((x - 0.153f) * k, (2.103f - y) * k, z * k, 1.f);
    }
    if (id == 4) {                                              // gyroid shell inside a ball
        const float k = P[28];
        const float ph = PM_TAU * u, ct = 1.f - 2.f * v, st = sqrtf(fmaxf(1.f - ct * ct, 0.f));
        float sp, cq;
        __sincosf(ph, &sp, &cq);
        const float rr = 1.1f * (cls ? pm_q(cbrtf(fmaxf(w3, 1e-4f)), 7.f) : cbrtf(fmaxf(w3, 1e-4f))) * k;
        float3 x = sp_f3(rr * st * cq, rr * ct, rr * st * sp);
        float f = 0.f;
        #pragma unroll
        for (int it = 0; it < 4; ++it) {                        // Newton steps onto sin x cos y + ... = 0
            float sx, cx, sy, cy, sz, cz;
            __sincosf(x.x, &sx, &cx);
            __sincosf(x.y, &sy, &cy);
            __sincosf(x.z, &sz, &cz);
            f = sx * cy + sy * cz + sz * cx;
            const float gx = cx * cy - sz * sx, gy = cy * cz - sx * sy, gz = cz * cx - sy * sz;
            const float s = f / (gx * gx + gy * gy + gz * gz + 0.05f);
            x.x -= s * gx; x.y -= s * gy; x.z -= s * gz;
        }
        const float3 q = sp_mul3(x, 1.f / k);
        const float rq = sqrtf(sp_dot3(q, q));
        const float wt = (1.f - sp_smooth(0.8f, 1.12f, rq)) * sp_clamp01(1.f - 2.5f * fabsf(f));
        return make_float4(q.x, q.y, q.z, wt);
    }
    if (id == 5) {                                              // Lissajous knot tube (ribs)
        if (cls) u = pm_q(u, 300.f);
        const float t = PM_TAU * u;
        const float3 T = sp_sub3(pm_liss(t + 2e-3f), pm_liss(t - 2e-3f));
        const float3 p = pm_tube(pm_liss(t), T, PM_TAU * v, 0.075f * tube * (0.86f + 0.14f * w3));
        return make_float4(p.x, p.y, p.z, 1.f);
    }
    // sphere (0) and spherical-harmonic blob (3): meridians / latitudes
    if (cls == 1) u = pm_q(u, 28.f);
    if (cls == 2) v = pm_q(v, 15.f);
    const float ph = PM_TAU * u, ct = 1.f - 2.f * v, st = sqrtf(fmaxf(1.f - ct * ct, 0.f));
    float sp, cq;
    __sincosf(ph, &sp, &cq);
    float r = 1.f + 0.025f * (w3 - 0.5f);
    if (id == 3) {
        const float a = P[17], b = P[18];
        r = 0.8f + 0.28f * st * st * st * __cosf(3.f * ph + a) + 0.22f * (1.5f * ct * ct - 0.5f) * __cosf(b)
            + 0.18f * st * ct * __sinf(2.f * ph - a) + 0.012f * (w3 - 0.5f);
    }
    return make_float4(r * st * cq, r * ct, r * st * sp, 1.f);
}

// blend of a shape pair by a per-point staggered morph wave (bottom -> top with random spread)
__device__ float4 pm_pair(int a, int b, float m, float spr, float hs, float u, float v, float w3, int cls,
                          const float* __restrict__ P, float& bump) {
    if (a == b || m <= 1e-4f) return pm_shape(a, u, v, w3, cls, P);
    if (m >= 0.9999f) return pm_shape(b, u, v, w3, cls, P);
    const float4 A = pm_shape(a, u, v, w3, cls, P);
    const float hh = 0.5f * hs + 0.5f * sp_clamp01(0.5f + 0.42f * A.y);
    float mi = sp_clamp01(m * (1.f + spr) - spr * hh);
    mi = mi * mi * (3.f - 2.f * mi);
    if (mi <= 0.f) return A;
    const float4 B = pm_shape(b, u, v, w3, cls, P);
    bump = fmaxf(bump, 4.f * mi * (1.f - mi));
    return pm_mix4(A, B, mi);
}

extern "C" __global__ void __launch_bounds__(256)
pm_gen(float4* __restrict__ pos, float* __restrict__ idx, float* __restrict__ sz, const float* __restrict__ P,
       const int n, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const unsigned int ui = (unsigned int)i;
    const float u = (float)((ui * 3242174889u) >> 8) * (1.f / 16777216.f);     // R2 sequence, exact in fixed point
    const float v = (float)((ui * 2447445414u) >> 8) * (1.f / 16777216.f);
    const float3 h = sp_hash3(ui, seed);
    const float3 g = sp_hash3(ui ^ 0x5bd1e995u, seed + 17u);
    const float w3 = g.x;
    const float hc = sp_hashf(ui, seed + 31u);
    const int cls = hc < P[30] ? 1 : (hc < 2.f * P[30] ? 2 : 0);
    const float m = P[4], T = P[5], spr = P[6];

    // ---- target: old pair -> new pair (each a morph between shape A and B)
    float bump = 0.f;
    float4 q;
    if (T >= 0.9999f) {
        q = pm_pair((int)P[2], (int)P[3], m, spr, h.x, u, v, w3, cls, P, bump);
    } else {
        q = pm_pair((int)P[0], (int)P[1], m, spr, h.x, u, v, w3, cls, P, bump);
        if (T > 1e-4f) {
            const float hh = 0.55f * h.x + 0.45f * sp_clamp01(0.5f + 0.42f * q.y);
            float st = sp_clamp01(T * (1.f + spr) - spr * hh);
            st = st * st * (3.f - 2.f * st);
            if (st > 0.f) {
                const float4 q1 = pm_pair((int)P[2], (int)P[3], m, spr, h.x, u, v, w3, cls, P, bump);
                bump = fmaxf(bump, 4.f * st * (1.f - st));
                q = pm_mix4(q, q1, st);
            }
        }
    }
    float3 p = sp_f3(q.x, q.y, q.z);
    float wt = q.w;

    // ---- transit (bulge + spiral about y) and the swirl twist
    const float tr = P[16] * bump, swirl = P[14];
    p = sp_mul3(p, 1.f + 0.3f * tr);
    {
        const float rl = sqrtf(sp_dot3(p, p));
        const float ang = tr * (0.8f + 2.4f * swirl) * (0.6f + 0.4f * h.y)
                        + swirl * (1.1f * p.y + 0.6f * __sinf(P[15] + 2.2f * rl));
        float s, c;
        __sincosf(ang, &s, &c);
        p = sp_f3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
    }
    // ---- pulse breath (radial amplitude) and drop explosion
    {
        const float rl = sqrtf(sp_dot3(p, p));
        const float3 dir = sp_mul3(p, 1.f / fmaxf(rl, 1e-3f));
        p = sp_mul3(p, 1.f + P[13] * (0.55f + 0.9f * h.y));
        const float X = P[12];
        if (X > 1e-4f) {
            const float3 r3 = sp_hash3(ui, seed + 5u);
            float3 d = sp_add3(dir, sp_f3(1.4f * (r3.x - 0.5f), 1.4f * (r3.y - 0.5f), 1.4f * (r3.z - 0.5f)));
            d = sp_mul3(d, rsqrtf(fmaxf(sp_dot3(d, d), 1e-8f)));
            p = sp_add3(p, sp_mul3(d, X * (0.45f + 2.6f * h.y * h.y)));
        }
    }
    // ---- curl-noise dispersion (energy), stronger in transit and in the explosion
    const float amp = P[7] + 0.32f * tr + 0.5f * P[12];
    if (amp > 1e-4f) {
        const float ns = P[8];
        const float3 c = sp_curl(sp_f3(p.x * ns + P[9], p.y * ns + P[10], p.z * ns + P[11]), seed, 2);
        p = sp_add3(p, sp_mul3(c, amp));
    }
    // ---- sparkle (smooth per-point twinkle; integer frequencies so the wrapped phase never jumps)
    {
        const float kf = 1.f + floorf(g.z * 3.f);
        float c = 0.5f + 0.5f * __cosf(PM_TAU * (P[20] * kf + g.y));
        c = c * c; c = c * c; c = c * c; c = c * c; c = c * c;   // ^32
        wt *= (0.5f + 0.5f * h.z) * (1.f + 5.f * P[19] * c);
    }
    // ---- depth of field: size multiplier from the distance to the focus plane
    const float z = P[23] * p.x + P[24] * p.y + P[25] * p.z + P[26];
    const float fz = fmaxf(P[22], 0.1f);
    float sm = 1.f + P[21] * 14.f * fabsf(z - fz) / fz;
    sm = fminf(sm, 7.f);
    if (!(p.x == p.x && p.y == p.y && p.z == p.z && wt == wt)) { p = sp_f3(0.f, 0.f, 0.f); wt = 0.f; }
    pos[i] = make_float4(p.x, p.y, p.z, fmaxf(wt, 0.f));
    idx[i] = u + 0.35f * v;
    sz[i] = sm;
}
"""


def _pal_host(coeffs, t: float) -> tuple[float, float, float]:
    """Cosine palette on the host (sRGB 0..1), for the background tint."""
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return tuple(min(max(a[i] + b[i] * math.cos(TAU * (c[i] * t + d[i])), 0.0), 1.0) for i in range(3))


class PointMorph(Source):
    name = "point_morph"
    label = "Point Morph"
    description = ("A million glowing points flow between sphere, torus knot, Klein bottle, harmonic blob, gyroid "
                   "and Lissajous knot on curl-noise currents, with depth-of-field bokeh; drops explode the cloud "
                   "and it reassembles into the next shape.")
    tags = {"family": "abstract", "dims": "3d", "toolkit": "splat3d", "technique": ["points", "particles"],
            "moods": ["hypnotic", "elegant", "cosmic"], "style": "lively", "energy": [0.1, 1.0]}
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.03),
        "morph": num(0.0, 1.0, 0.0, "Morph (shape A -> B)", tier="regional", basic=True),
        "density": num(0.15, 1.0, 0.85, "Point density", tier="regional", basic=True),
        "swirl": num(0.0, 1.0, 0.35, "Swirl", tier="regional"),
        "dof_amount": num(0.0, 1.0, 0.45, "Depth of field (bokeh)", tier="regional"),
        "point_size": num(1.0, 5.0, 2.0, "Point size", unit="px", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Glow", tier="regional"),
        "camera_distance": num(2.6, 7.0, 4.0, "Camera distance", tier="regional"),
        "cycle": num(0.0, 1.0, 0.5, "Auto shape cycling (0 = drops only)", tier="regional"),
        "shape_a": enum(SHAPES, "torus_knot", "Shape A", tier="structural"),
        "shape_b": enum(SHAPES, "harmonic", "Shape B", tier="structural"),
        "spin_speed": num(0.0, 4.0, 1.0, "Spin", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        rng = np.random.default_rng(self.seed)
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.sp = Splat3D(ctx, self.w, self.h, max_points=N_MAX + N_DUST, max_lines=64)
        self.k_gen = raw_module(_SRC, SPLAT3D_OPTIONS).get_function("pm_gen")
        self.pos = cp.zeros((N_MAX, 4), cp.float32)
        self.idx = cp.zeros(N_MAX, cp.float32)
        self.sz = cp.ones(N_MAX, cp.float32)
        self.P = np.zeros(NP, np.float32)
        self.Pd = cp.zeros(NP, cp.float32)

        # faint dust shell around the scene for parallax (static, uploaded once)
        d = np.ones((N_DUST, 4), np.float32)
        d[:, :3] = fibonacci_sphere(N_DUST)[rng.permutation(N_DUST)] * rng.uniform(2.2, 11.0, (N_DUST, 1))
        d[:, 3] = 0.15 + 0.85 * rng.uniform(0.0, 1.0, N_DUST) ** 5
        self.dust = cp.asarray(d)
        self.dust_idx = cp.asarray(rng.uniform(0.0, 1.0, N_DUST).astype(np.float32))

        self.spin = 0.0
        self.wob = 0.0
        self.yaw = 0.0
        self.bob = 0.0
        self.twist = 0.0
        self.blob_a = 0.0
        self.blob_b = 0.0
        self.spark = 0.0
        self.focus_ph = 0.0
        self.noff = [float(v) for v in rng.uniform(0.0, 50.0, 3)]
        self.drift = 0.0
        self.e_s = None
        self.e_fast = None
        self.e_slow = None
        # shape sequencer: pair offset k, transition T from pair k-1 to pair k, hold timer, explode envelope
        self.k = 0
        self.T = 1.0
        self.t_dur = 3.0
        self.hold = 0.0
        self.since_drop = 99.0
        self.X = 0.0
        self.x_peak = 0.0
        self.x_att = 0.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s
        self.e_fast = e if self.e_fast is None else self.e_fast + (e - self.e_fast) * (1.0 - math.exp(-dt / 0.2))
        self.e_slow = e if self.e_slow is None else self.e_slow + (e - self.e_slow) * (1.0 - math.exp(-dt / 4.0))

        # ---- rates (pulse kicks rates only)
        self.spin += p["spin_speed"] * spd * (0.07 + 0.3 * e + 0.45 * pulse) * dt
        self.wob += spd * (0.1 + 0.2 * e) * dt
        self.yaw += spd * (0.03 + 0.09 * e) * dt
        self.bob += spd * (0.15 + 0.25 * e) * dt
        self.twist = math.fmod(self.twist + spd * (0.35 + 0.8 * e) * dt, TAU)
        self.blob_a = math.fmod(self.blob_a + spd * (0.25 + 0.6 * e) * dt, TAU)
        self.blob_b = math.fmod(self.blob_b + spd * (0.17 + 0.4 * e) * dt, TAU)
        self.spark = math.fmod(self.spark + spd * (0.35 + 1.0 * e) * dt, 1.0)
        self.focus_ph = math.fmod(self.focus_ph + spd * 0.13 * dt, TAU)
        flow = spd * (0.06 + 0.32 * es) * dt
        self.noff = [math.fmod(o + flow * k, 4096.0) for o, k in zip(self.noff, (0.62, 0.38, 0.51))]
        self.drift = math.fmod(self.drift + p["drift_speed"] * spd * dt, 1000.0)

        # ---- shape sequencer: drops (fast energy rise) explode + advance; ``cycle`` advances on a timer
        self.since_drop += dt
        if self.T >= 1.0:
            self.hold += dt * spd
            cyc = float(p["cycle"])
            auto = cyc > 0.01 and self.hold >= 56.0 - 46.0 * cyc
            drop = dt > 0 and self.since_drop > 6.0 and self.e_fast > 0.5 and self.e_fast - self.e_slow > 0.2
            if drop or auto:
                self.k += 1
                self.T = 0.0
                self.hold = 0.0
                self.t_dur = 2.4 if drop else 4.5
                self.x_peak = 1.0 if drop else 0.28
                self.x_att = 0.14
                if drop:
                    self.since_drop = 0.0
        else:
            self.T = min(1.0, self.T + dt * max(spd, 0.15) / self.t_dur)
        if self.x_att > 0.0:
            self.X += (self.x_peak - self.X) * (1.0 - math.exp(-dt / 0.045))
            self.x_att -= dt
        else:
            self.X *= math.exp(-dt / 0.75)
            if self.X < 1e-4:
                self.X = 0.0

        ns = len(SHAPES)
        ia, ib = SHAPES.index(p["shape_a"]), SHAPES.index(p["shape_b"])
        k0, k1 = self.k - 1, self.k

        # ---- camera and model
        D = float(p["camera_distance"]) * (1.0 - 0.05 * es)
        pitch = 0.2 + (0.06 + 0.08 * es) * math.sin(self.bob)
        cam = look_at(orbit((0.0, 0.0, 0.0), D, self.yaw, pitch), (0.0, 0.0, 0.0), fov_deg=44.0)
        rot = mat_euler(0.32 * math.sin(self.wob), self.spin, 0.18 * math.sin(0.71 * self.wob + 1.0))
        model = mat_trs(r=rot)
        row = mat_mul(cam.view(), model)[2]

        P = self.P
        P[0:4] = ((ia + k0) % ns, (ib + k0) % ns, (ia + k1) % ns, (ib + k1) % ns)
        P[4] = min(max(float(p["morph"]), 0.0), 1.0)
        P[5] = self.T
        P[6] = 0.6
        P[7] = 0.02 + 0.14 * es ** 1.3
        P[8] = 1.25
        P[9:12] = self.noff
        P[12] = self.X
        P[13] = 0.09 * pulse
        P[14] = float(p["swirl"])
        P[15] = self.twist
        P[16] = 0.9
        P[17], P[18] = self.blob_a, self.blob_b
        P[19] = 0.15 + 0.85 * e
        P[20] = self.spark
        P[21] = float(p["dof_amount"])
        P[22] = cam.dist * (1.0 - 0.1 * float(p["dof_amount"])) * (1.0 + 0.06 * math.sin(self.focus_ph))
        P[23:27] = row
        P[28] = 6.2
        P[29] = 1.0
        P[30] = 0.28
        self.Pd.set(P)

        n = max(20_000, min(N_MAX, int(N_MAX * float(p["density"]) * (0.55 + 0.45 * es))))
        self.k_gen(((n + 255) // 256,), (256,), (self.pos, self.idx, self.sz, self.Pd, np.int32(n),
                                                 np.uint32(self.seed)))

        sp = self.sp
        if dt > 0 and self.X > 0.03:
            sp.fade(min(0.8, 0.85 * self.X) ** (dt * 60.0))        # brief streaks while the cloud explodes
        else:
            sp.clear()
        sess = self.session_palette()
        coeffs = np.asarray(PALETTES[p["palette"]] if sess is None else sess, np.float64).reshape(-1)
        pal = tuple(coeffs.tolist())
        period, drift = float(p["palette_period"]), self.drift

        # dust (parallax), then the cloud
        sp.points(self.dust, cam=cam, model=mat_rot((0.0, 1.0, 0.0), 0.25 * self.spin), intensity=0.5 * (0.6 + 0.6 * e),
                  size_px=1.6, weight_in_w=True, palette=pal, index=self.dust_idx, period=0.4, drift=drift + 0.5,
                  near_fade=(0.6, 1.6), far_fade=(D + 4.0, D + 11.0))
        gain = (0.6 + 0.55 * e) * (1.0 + 0.35 * pulse) * (500_000.0 / n) ** 0.75
        sp.points(self.pos, cam=cam, n=n, model=model, intensity=0.05 * gain, size_px=float(p["point_size"]),
                  size_range=(0.6, 8.0), sizes=self.sz, weight_in_w=True, palette=pal, index=self.idx,
                  period=period, drift=drift, near_fade=(0.25, 0.8))

        bg = _pal_host(coeffs, drift + 0.6)
        bgk = 0.05
        sp.resolve(out, exposure=1.1 * float(p["intensity"]), tonemap="aces", saturation=1.15,
                   glow=float(p["glow"]) * (0.6 + 0.6 * e) * (1.0 + 0.5 * pulse), glow_radius_px=20.0,
                   glow_wide=0.5, background=(bg[0] * bgk, bg[1] * bgk, bg[2] * bgk + 0.01))

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = self.pos = self.idx = self.sz = self.Pd = self.dust = self.dust_idx = None
