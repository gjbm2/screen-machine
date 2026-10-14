"""Quaternion Julia: a morphing 3D slice of a quaternion Julia set, its constant c on a slow 4D Lissajous loop.

Source: Original (inspired by https://iquilezles.org/articles/juliasets3d/ , https://www.shadertoy.com/view/XslSWl)
Author: smfx (original; maths after iq's 'juliasets3d' article, idea only; TDM 'Hypercomplex' is NC and MacSlow's version GPL, neither used)
License: MIT (original)
license_class: permissive

Written from scratch: no shader code copied.  Building blocks (camera, bounding sphere, tetrahedral normal, AO,
light rig, tonemap) come from the MIT toolkit ``stream_service/plugins/_lib/sdf3d.py``.

The fractal: q -> q^2 + c (or q^3 + c) on quaternions q = (x, y, z, slice_w), distance estimate
0.5 |q| log|q| / |q'| with |q'| propagated analytically (2|q| or 3|q|^2 per step).  The power morphs 2 -> 3 by
blending the two DEs; the iteration count eases by blending the DE after floor(n) and floor(n) + 1 iterations.

Choosing c so the shape never falls apart into dust: a quaternion Julia set with c = a + b*u (u a unit imaginary
direction) is connected exactly when the complex Julia set of a + b*i is.  So c is built from the multiplier mu of
the attracting fixed point (|mu| < 1 keeps c inside the main component of the Mandelbrot / Multibrot-3 set):
power 2: c = mu/2 - mu^2/4;  power 3: z = sqrt(mu/3), c = z (1 - mu/3).  The 4D Lissajous loop then runs over
(arg mu, |mu| wobble, u azimuth, u elevation): |mu| -> 1 is coral and flame tongues, small |mu| smooth blobs,
arg mu near 0 or pi lathe-like solids, in between spirals.  arg mu stays in [0.3, 3.0] (never crossing the
principal-sqrt branch cut), which covers every shape up to mirror symmetry.

Shading: orbit-trap colour (min |q|^2 over the orbit) through pal(trap * period + drift), key/fill/rim rig with
palette-tinted fill and rim, a subsurface-like rim (fresnel glow plus a one-tap thickness probe that lets thin
fins glow when back-lit), trap glow where orbits pass near the origin, and a step-count aura round the silhouette.

Macros (e' = smoothstep(0.1, 0.85, energy), slewed): energy -> c-path angular speed (integrated), path radius
|mu| (wilder shapes), camera orbit and tumble speed, rim / trap / aura glow, AO contrast.  pulse (k = pulse *
(0.35 + 0.65 e)) -> a radial nudge to |mu| (~0.03 in c, decays with the envelope, so the shape breathes and never
snaps) plus a rim and trap flash.  speed scales the c-path and orbit rates; drift integrates the palette phase;
intensity is output gain.

Perf: render_scale 0.4, bounding sphere from the escape radius, at most 64 steps, 10 iterations by default.
"""
from __future__ import annotations

import cmath
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import (SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rig_pack, rot_matrix,
                                               smoothstep, wrap)
from stream_service.plugins._lib.upsample import internal_size, present

STEPS = 64
MAX_IT = 13
TAU = 2.0 * math.pi

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28..31 c (power 2) | 32..35 c (power 3)
#  36 power blend (0 = q^2, 1 = q^3) | 37 floor(iterations) | 38 frac(iterations) | 39 slice w
#  40..48 object rotation (world -> object, row-major) | 49 bound radius | 50 softness offset | 51 rim/sss gain
#  52 trap glow gain | 53 aura gain | 54 AO exponent | 55..71 rig | 72 trap index scale | 73 fog start
#  74 fog density | 75 hit epsilon scale (x pixel cone)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define QJ_STEPS %(steps)d
#define QJ_MAXIT %(maxit)d
#define QJ_BAIL 64.f

__device__ __forceinline__ float4 q4(float x, float y, float z, float w) { return make_float4(x, y, z, w); }
__device__ __forceinline__ float4 load4(const float* __restrict__ p) { return make_float4(p[0], p[1], p[2], p[3]); }
__device__ __forceinline__ float qdot(float4 a) { return a.x * a.x + a.y * a.y + a.z * a.z + a.w * a.w; }
// 0.5 |q| log|q| / |q'| written with m = |q|^2
__device__ __forceinline__ float qj_dist(float m, float md) {
    m = fmaxf(m, 1e-20f);
    return 0.25f * logf(m) * sqrtf(m) / fmaxf(md, 1e-20f);
}
// Quaternion Julia DE.  kf = floor(iterations), fr = its fraction: the DE after kf and kf + 1 iterations is blended
// so the iteration knob eases without popping.  trap = min |q|^2 over the orbit.
template <int POW>
__device__ __forceinline__ float qj_de(float4 q, const float4 c, const int kf, const float fr, float& trap) {
    float m2 = qdot(q), md = 1.f, ma = -1.f, mda = 1.f;
    trap = 1e10f;
    const int ni = kf + (fr > 1e-3f ? 1 : 0);
    #pragma unroll 1
    for (int i = 0; i < QJ_MAXIT; ++i) {
        if (i >= ni) break;
        if (i == kf) { ma = m2; mda = md; }
        if (m2 > QJ_BAIL) break;
        const float v2 = q.y * q.y + q.z * q.z + q.w * q.w;
        if (POW == 2) {                                     // (x + v)^2 = x^2 - |v|^2 + 2 x v
            md *= 2.f * sqrtf(m2);
            const float s = 2.f * q.x;
            q = q4(q.x * q.x - v2 + c.x, s * q.y + c.y, s * q.z + c.z, s * q.w + c.w);
        } else {                                            // (x + v)^3 = x (x^2 - 3|v|^2) + (3x^2 - |v|^2) v
            md *= 3.f * m2;
            const float xx = q.x * q.x, s = 3.f * xx - v2;
            q = q4(q.x * (xx - 3.f * v2) + c.x, s * q.y + c.y, s * q.z + c.z, s * q.w + c.w);
        }
        m2 = qdot(q);
        trap = fminf(trap, m2);
    }
    if (ma < 0.f) { ma = m2; mda = md; }
    const float da = qj_dist(ma, mda);
    return fr > 1e-3f ? mix(da, qj_dist(m2, md), fr) : da;
}
__device__ __forceinline__ float qj_scene(float3 p, const float* __restrict__ P, const Mat3& M, float& trap) {
    const float3 o = mul(M, p);
    const float4 q = q4(o.x, o.y, o.z, P[39]);
    const int kf = (int)P[37];
    const float fr = P[38], f = P[36];
    float d;
    if (f < 1e-3f) {
        d = qj_de<2>(q, load4(P + 28), kf, fr, trap);
    } else if (f > 0.999f) {
        d = qj_de<3>(q, load4(P + 32), kf, fr, trap);
    } else {                                                // power morph: blend the two DEs
        float t2, t3;
        const float d2 = qj_de<2>(q, load4(P + 28), kf, fr, t2);
        const float d3 = qj_de<3>(q, load4(P + 32), kf, fr, t3);
        d = mix(d2, d3, f);
        trap = mix(fminf(t2, 4.f), fminf(t3, 4.f), f);
    }
    return d - P[50];
}

SDF_KERNEL
void quaternion_julia(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const Mat3 M = mat3_load(P + 40);
    float tdummy;
    auto map = [&](float3 p) { return qj_scene(p, P, M, tdummy); };

    const float drift = P[26], period = P[25];
    const float* PAL = P + 13;
    const float3 pf = srgb_to_linear(pal(drift + 0.6f, PAL));
    const float3 fogc = mix(pf, f3(dot(pf, f3(0.333f))), 0.3f) * 0.07f + f3(0.003f, 0.003f, 0.006f);
    const float ex = (x + 0.5f) / W - 0.5f, ey = (y + 0.5f) / H - 0.5f;
    float3 col = fogc * (1.3f - 1.6f * (ex * ex + ey * ey) + 0.4f * ey);    // deep backdrop

    const float2 bs = iSphere(ro, rd, P[49]);
    if (bs.y > 0.f && bs.x < bs.y) {
        float t = fmaxf(bs.x, 0.01f);
        const float tmax = bs.y, epsk = P[75] * pix;
        float dmin = 1e9f, dl = 1e9f;
        int steps = 0;
        bool hit = false;
        for (int i = 0; i < QJ_STEPS; ++i) {
            const float d = map(ro + rd * t);
            dmin = fminf(dmin, d); dl = d; steps = i + 1;
            if (d < epsk * t) { hit = true; break; }
            t += d * 0.9f;
            if (t > tmax) break;
        }
        if (!hit && t < tmax && dl < 6.f * epsk * t) hit = true;     // budget ran out right at the surface
        const float sg = (float)steps * (1.f / QJ_STEPS);
        const float3 aura_c = srgb_to_linear(pal(drift + 0.18f + 0.3f * sg, PAL));
        if (hit) {
            const float3 pos = ro + rd * t;
            float trap;
            qj_scene(pos, P, M, trap);
            trap = fminf(trap, 4.f);
            const float3 n = calc_normal(map, pos, fmaxf(1.5f * pix * t, 2e-4f));
            const float ao = powf(fmaxf(calc_ao(map, pos, n, 0.12f, 3), 1e-4f), P[54]);
            const float idx = sqrtf(trap) * P[72];
            const float3 base = srgb_to_linear(pal(idx * period + drift, PAL));
            const float3 hot = srgb_to_linear(pal(idx * period + drift + 0.35f, PAL));
            const float3 sssc = srgb_to_linear(pal(idx * period + drift + 0.12f, PAL));
            const Rig L = rig_load(P + 55);
            col = shade(L, base * 0.85f, n, rd, 1.f, ao);
            // subsurface-like rim: fresnel glow + light bleeding through thin fins (one inward thickness tap)
            const float hth = 0.09f;
            const float din = map(pos - n * hth);
            const float thin = saturate(1.f + din / hth);              // 0 thick .. 1 thin
            const float fre = 1.f - saturate(dot(n, -rd));
            const float back = saturate(dot(rd, L.key_dir));
            col += sssc * (P[51] * (0.9f * fre * fre * (0.35f + 0.65f * ao) + thin * (0.2f + 1.1f * back * back)));
            // orbit-trap glow: points whose orbit dives close to the origin burn hot
            col += hot * (expf(-9.f * trap) * P[52]);
            col += aura_c * (P[53] * 0.5f * sg * sg);                  // grazing-step glow on the silhouette
            col = fog_exp2(col, fogc, fmaxf(t - P[73], 0.f), P[74]);
        } else {
            const float aura = expf(-10.f * fmaxf(dmin, 0.f));
            const float bc = length(ro - rd * dot(ro, rd));             // ray's closest approach to the centre
            const float fade = smoothstep(P[49], 0.7f * P[49], bc);     // no visible edge at the bound sphere
            col += aura_c * (P[53] * fade * (0.5f * aura + 0.6f * sg * sg));
        }
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"steps": STEPS, "maxit": MAX_IT}


def _c_complex(r: float, th: float, power: int) -> complex:
    """c on the main hyperbolic component: the attracting fixed point has multiplier mu = r e^{i th} (r < 1)."""
    mu = cmath.rect(r, th)
    if power == 2:
        return mu / 2.0 - mu * mu / 4.0
    z = cmath.sqrt(mu / 3.0)                      # th in (0, pi): principal branch, continuous
    return z * (1.0 - mu / 3.0)


def _escape_radius(cabs: float, power: int) -> float:
    """R with R^n - R = |c|: the filled Julia set lies inside |q| <= R."""
    if power == 2:
        return 0.5 * (1.0 + math.sqrt(1.0 + 4.0 * cabs))
    R = 1.5
    for _ in range(8):
        R -= (R ** 3 - R - cabs) / (3.0 * R * R - 1.0)
    return R


def _pal_host(t: float, P) -> tuple[float, float, float]:
    """Host copy of pal() returning approximately linear rgb, for palette-tinted rig colours."""
    out = []
    for i in range(3):
        c = P[i] + P[3 + i] * math.cos(TAU * (P[6 + i] * t + P[9 + i]))
        out.append(min(max(c, 0.0), 1.0) ** 2.2)
    return out[0], out[1], out[2]


# Lissajous rate ratios for (arg mu, |mu| wobble, u azimuth, u elevation): c_path_shape blends these two sets.
_RATIOS_A = (1.0, 0.71, 0.43, 0.31)
_RATIOS_B = (1.0, 1.618, 2.0, 1.33)


class QuaternionJulia(Source):
    name = "quaternion_julia"
    label = "Quaternion Julia"
    description = ("A morphing 3D slice of a quaternion Julia set whose constant travels a slow 4D loop, flowing "
                   "between coral, flame tongues and smooth blobs.")
    params = {
        **standard_scene_params(palette="candy", render_scale=0.4, period=1.2, drift=0.03),
        "c_path_radius": num(0.3, 0.99, 0.88, "Wildness (path radius)", tier="regional", basic=True),
        "c_path_shape": num(0.0, 1.0, 0.3, "Path shape (Lissajous ratios)", tier="regional"),
        "slice_w": num(-0.6, 0.6, 0.1, "4D slice", tier="regional"),
        "power": num(2.0, 3.0, 2.0, "Power (q² → q³)", tier="regional"),
        "iterations": num(6.0, 12.0, 10.0, "Iterations (smooth → detailed)", step=0.1, tier="regional"),
        "surface_softness": num(0.0, 1.0, 0.25, "Surface softness", tier="regional"),
        "camera_distance": num(2.2, 5.0, 3.2, "Camera distance", tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Rim and trap glow", tier="regional"),
        "morph_speed": num(0.0, 4.0, 1.0, "Morph speed", unit="×", tier="realtime", bind="rate"),
        "orbit_speed": num(-3.0, 3.0, 1.0, "Orbit speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("quaternion_julia")
        self.P = cp.zeros(80, cp.float32)
        self.Ph = np.zeros(80, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        r = rng.uniform(0.0, 1.0, 10)
        self.ph = [TAU * float(v) for v in r[0:4]]     # Lissajous phases (host float64, wrapped at 2 pi)
        self.yaw = TAU * float(r[4])
        self.rot = TAU * float(r[5])
        self.light = TAU * float(r[6])
        self.pitch_ph = TAU * float(r[7])
        self.slice_ph = TAU * float(r[8])
        self.drift = float(r[9])
        self.e_fast = None
        self.e_slow = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        if self.e_fast is None:
            self.e_fast = self.e_slow = e
        elif dt > 0.0:
            self.e_fast += (e - self.e_fast) * (1.0 - math.exp(-dt / 0.25))
            self.e_slow += (e - self.e_slow) * (1.0 - math.exp(-dt / 1.5))
        ef = smoothstep(0.1, 0.85, self.e_fast)
        es = smoothstep(0.1, 0.85, self.e_slow)
        k = pulse * (0.35 + 0.65 * e)

        # ---- integrate phases (host; kicks only touch rates)
        shape = float(p["c_path_shape"])
        ratios = [a + (b - a) * shape for a, b in zip(_RATIOS_A, _RATIOS_B)]
        w_c = float(p["morph_speed"]) * spd * (0.05 + 0.22 * ef + 0.15 * k)
        for i in range(4):
            self.ph[i] = wrap(self.ph[i] + ratios[i] * w_c * dt, TAU)
        orbit = float(p["orbit_speed"])
        self.yaw = wrap(self.yaw + orbit * spd * (0.05 + 0.17 * ef) * dt, TAU)
        self.rot = wrap(self.rot + spd * (0.02 + 0.06 * ef) * dt, TAU)
        self.light = wrap(self.light + spd * (0.06 + 0.10 * ef) * dt, TAU)
        self.pitch_ph = wrap(self.pitch_ph + spd * (0.04 + 0.05 * ef) * dt, TAU)
        self.slice_ph = wrap(self.slice_ph + spd * (0.05 + 0.10 * ef) * dt, TAU)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- c on the 4D Lissajous loop (multiplier mu inside the unit disc: always connected)
        th = 1.65 + 1.35 * math.sin(self.ph[0])
        r0 = float(p["c_path_radius"])
        rr = r0 * (0.84 + 0.16 * es) + 0.035 * math.sin(self.ph[1]) + 0.06 * k * (1.0 - 0.5 * r0)
        rr = min(max(rr, 0.05), 0.992)
        az = self.ph[2]
        el = 0.9 * math.sin(self.ph[3])
        u = (math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el))   # imaginary axis (y, z, w)
        c2 = _c_complex(rr, th, 2)
        c3 = _c_complex(rr, th, 3)
        q2 = (c2.real, c2.imag * u[0], c2.imag * u[1], c2.imag * u[2])
        q3 = (c3.real, c3.imag * u[0], c3.imag * u[1], c3.imag * u[2])

        f = min(max(float(p["power"]) - 2.0, 0.0), 1.0)
        f = f * f * (3.0 - 2.0 * f)
        iters = min(max(float(p["iterations"]), 6.0), 12.0)
        kf = math.floor(iters)
        fr = iters - kf
        w = float(p["slice_w"]) + 0.05 * math.sin(self.slice_ph)
        soft = float(p["surface_softness"])
        soft_off = 0.035 * soft

        R = 0.0
        if f < 0.999:
            R = max(R, _escape_radius(abs(c2), 2))
        if f > 1e-3:
            R = max(R, _escape_radius(abs(c3), 3))
        bound = math.sqrt(max(R * R - w * w, 0.04)) + 0.08 + soft_off * 2.0

        # ---- camera: orbit the sculpture, slow pitch sway; the sculpture tumbles too
        D = float(p["camera_distance"])
        pitch = 0.42 * math.sin(self.pitch_ph)
        eye = orbit_eye((0.0, 0.0, 0.0), D, self.yaw, pitch)
        roll = 0.05 * math.sin(1.3 * self.pitch_ph + 0.5)
        fov = 40.0 + 1.5 * ef * math.sin(2.0 * self.slice_ph)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=fov, roll=roll)
        M = rot_matrix((0.35, 1.0, 0.25), self.rot)

        # ---- light rig (palette-tinted fill and rim; the key light circles)
        pc = palette_coeffs(p["palette"])
        fill = _pal_host(self.drift + 0.6, pc)
        rimc = _pal_host(self.drift + 0.2, pc)
        kg = 0.9 + 0.3 * ef
        rig = rig_pack(key_dir=(0.8 * math.cos(self.light), 0.6, 0.8 * math.sin(self.light)),
                       key_col=(1.55 * kg, 1.42 * kg, 1.3 * kg),
                       fill_col=tuple(0.07 + 0.25 * c for c in fill), rim_col=tuple(0.12 + 0.45 * c for c in rimc),
                       amb_col=(0.025, 0.025, 0.032), spec=0.6, shininess=42.0)

        glow = float(p["glow"])
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = pc
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28:32] = q2
        Ph[32:36] = q3
        Ph[36] = f
        Ph[37] = kf
        Ph[38] = fr
        Ph[39] = w
        Ph[40:49] = M
        Ph[49] = bound
        Ph[50] = soft_off
        Ph[51] = glow * (0.35 + 0.55 * ef) * (1.0 + 1.6 * k)
        Ph[52] = glow * (0.25 + 0.5 * ef) * (1.0 + 2.5 * k)
        Ph[53] = glow * (0.3 + 0.45 * ef) * (1.0 + 0.4 * k)
        Ph[54] = 0.8 + 0.8 * ef
        Ph[55:72] = rig
        Ph[72] = 1.0
        Ph[73] = max(D - bound, 0.0)
        Ph[74] = 0.35
        Ph[75] = 0.7 + 2.0 * soft
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
