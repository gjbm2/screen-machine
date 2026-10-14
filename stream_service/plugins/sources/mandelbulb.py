"""Mandelbulb: the power-n Mandelbulb, orbited with orbit-trap colouring and glow, or flown through from inside.

Source: Original (inspired by https://github.com/Syntopia/FragmentariumWeb/blob/main/src/systems/fragmentarium/examples/Historical%203D%20Fractals/Mandelbulb.frag ,
        https://iquilezles.org/articles/mandelbulb/ , https://www.shadertoy.com/view/mtScRc)
Author: smfx (original implementation). Formula: Daniel White & Paul Nylander; distance estimate as described in
        Inigo Quilez's Mandelbulb article; reference look: Mikael Hvidtfeldt Christensen (Syntopia, FragmentariumWeb,
        MIT); "fly inside" idea: Marten Range (mrange, CC0).
License: MIT
license_class: permissive

Written from scratch (no code copied; mrange's shader credits an EvilRyu DE with "License: Unknown", so only the
idea of an inside flight is used).  Building blocks (camera, march helpers, AO, rig, tonemap) come from the MIT
toolkit ``stream_service/plugins/_lib/sdf3d.py``.

The fractal: z -> z^n + c in spherical coordinates (theta from +y, phi = atan2(x, z)), c = mix(p, julia_c,
julia_blend), 5 iterations, DE = 0.5 log r * r / dr.  The power morphs continuously; a plain fractional power tears
a seam along phi = +-pi (atan2's branch cut), so the phi rotation blends the two neighbouring *integer* powers
(sin/cos(floor(n) phi) -> sin/cos((floor(n) + 1) phi)) while theta and the radius use the true n.  At integer
powers that is exactly the classic bulb; between them the morph is seamless.

Camera: ``camera_distance`` 3.5 orbits outside and looks at the bulb; towards 0.6 the camera dives inside and looks
along its orbit, flying through a tunnel carved along the orbit circle (a torus subtracted from the fractal); the
cut faces glow with the interior orbit trap ("chambers").  A host numpy DE check at the eye and a look-ahead point
also widens the carve whenever the morphing bulb would swallow the eye, so the view never fills with solid.

Macros (e' = smoothstep(0.1, 0.85, energy)): energy -> power wobble amplitude (slewed ~1.5 s), orbit / tumble /
light-azimuth / morph rates x(0.4 + 1.2 e'), surface + aura glow gain, AO contrast, a small FOV breath.  pulse
(k = pulse (0.3 + 0.7 e)) -> a kick to the power-morph rate (``morph_kick``) and an emissive flash in the orbit-trap
crevices (``flash_kick``; local, in-world).  speed scales every phase; palette: albedo and emissive are
pal(trap * period + drift); intensity is output gain.

Perf: render_scale 0.35, 56 fixed march steps inside a bounding sphere (iSphere), 4-tap tetrahedral normal, 3-tap AO,
no shadows.
"""
from __future__ import annotations

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

ITERS = 5
STEPS = 56

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density | 29 power n | 30 floor(n)
#  31 frac(n) | 32 julia blend | 33..35 julia c | 36..44 object rotation (world -> object, row-major)
#  45 bound radius | 46 carve tube radius | 47 tube circle radius | 48 tube centre y | 49 trap mix
#  50 crevice glow | 51 pulse flash | 52 AO exponent | 53 aura | 54 cut-face glow | 55..71 rig | 72 fog start
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define MB_ITERS %(iters)d
#define MB_STEPS %(steps)d

// Mandelbulb / Julia-bulb DE with two orbit traps: ta = min |z|^2, tb = min distance to the coordinate planes.
__device__ __forceinline__ float de_bulb(float3 p, const float* __restrict__ P, float& ta, float& tb) {
    const float n = P[29], nf = P[30], fr = P[31], jb = P[32];
    const float3 c = mix(p, load3(P + 33), jb);
    const float dr_add = 1.f - jb;
    float3 z = p;
    float dr = 1.f;
    ta = dot(p, p); tb = 1e10f;
    #pragma unroll
    for (int i = 0; i < MB_ITERS; ++i) {
        const float r = fmaxf(length(z), 1e-6f);
        if (r > 2.f) break;
        const float th = acosf(clamp(z.y / r, -1.f, 1.f)) * n;
        const float ph = atan2f(z.x, z.z);
        const float rn1 = powf(r, n - 1.f);
        dr = rn1 * n * dr + dr_add;
        const float zr = rn1 * r;
        float st, ct, s1, c1, s2, c2;
        sincosf(th, &st, &ct);
        sincosf(ph * nf, &s1, &c1);                              // integer powers either side: no atan2 seam
        sincosf(ph * (nf + 1.f), &s2, &c2);
        const float sp = s1 + (s2 - s1) * fr, cp = c1 + (c2 - c1) * fr;
        z = f3(st * sp, ct, st * cp) * zr + c;
        ta = fminf(ta, dot(z, z));
        tb = fminf(tb, fminf(fabsf(z.x), fminf(fabsf(z.y), fabsf(z.z))));
    }
    const float r = fmaxf(length(z), 1e-6f);
    return 0.5f * logf(r) * r / fmaxf(dr, 1e-6f);
}
// world-space carve: a torus around the camera's orbit circle (radius P[46]; 0 = off). Returns +inside the tube.
__device__ __forceinline__ float carve(float3 p, const float* __restrict__ P) {
    const float q = length(f2(p.x, p.z)) - P[47];
    return P[46] - length(f2(q, p.y - P[48]));
}
__device__ __forceinline__ float scene(float3 p, const float* __restrict__ P, const Mat3& M) {
    float ta, tb;
    float d = de_bulb(mul(M, p), P, ta, tb);
    if (P[46] > 1e-3f) d = fmaxf(d, carve(p, P));
    return d;
}

SDF_KERNEL
void mandelbulb(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    const Mat3 M = mat3_load(P + 36);
    auto map = [&](float3 p) { return scene(p, P, M); };

    const float drift = P[26], period = P[25];
    const float* PAL = P + 13;
    const float3 pf = srgb_to_linear(pal(drift + 0.55f, PAL));
    const float3 fogc = mix(pf, f3(dot(pf, f3(0.333f))), 0.35f) * 0.06f + f3(0.002f, 0.002f, 0.005f);
    const float ex = (x + 0.5f) / W - 0.5f, ey = (y + 0.5f) / H - 0.5f;
    float3 col = fogc * (1.3f - 1.6f * (ex * ex + ey * ey));     // deep backdrop

    const float2 bs = iSphere(ro, rd, P[45]);
    if (bs.y > 0.f && bs.x < bs.y) {
        float t = fmaxf(bs.x, 0.002f);
        const float tmax = bs.y;
        float dmin = 1e9f, dl = 1e9f;
        int steps = 0;
        bool hit = false;
        for (int i = 0; i < MB_STEPS; ++i) {
            const float d = map(ro + rd * t);
            dmin = fminf(dmin, d); dl = d; steps = i + 1;
            if (d < 0.8f * pix * t) { hit = true; break; }
            t += d;
            if (t > tmax) break;
        }
        if (!hit && t < tmax && dl < 8.f * pix * t) hit = true;   // budget ran out right at the surface
        const float sg = (float)steps * (1.f / MB_STEPS);
        const float3 aura_c = srgb_to_linear(pal(drift + 0.12f + 0.35f * sg, PAL));
        if (hit) {
            const float3 pos = ro + rd * t;
            float ta, tb;
            const float db = de_bulb(mul(M, pos), P, ta, tb);
            const bool cut = P[46] > 1e-3f && carve(pos, P) > db;
            const float3 n = calc_normal(map, pos, fmaxf(1.5f * pix * t, 1e-4f));
            const float ia = sqrtf(ta), ib = tb * 2.5f;
            const float idx = mix(ia, ib, P[49]);
            const float3 base = srgb_to_linear(pal(idx * period + drift, PAL));
            const float3 hot = srgb_to_linear(pal(idx * period + drift + 0.3f, PAL));
            const float ao_g = calc_ao(map, pos, n, 0.12f, 3);
            const float ao = powf(fmaxf(ao_g * clamp(0.15f + 1.1f * ia, 0.f, 1.f), 1e-4f), P[52]);
            const Rig L = rig_load(P + 55);
            if (!cut) {
                col = shade(L, base * 0.9f, n, rd, 1.f, ao);
                const float crev = expf(-7.f * ta);                 // deep orbit-trap crevices glow
                col += hot * (crev * P[50] * (1.f + P[51]));
            } else {                                                // carved chamber wall: a glowing cross-section
                const float depth = fmaxf(-db, 0.f);                // how far below the fractal surface
                const float rim = expf(-30.f * depth);              // bright where the cut meets the surface
                const float3 hc = srgb_to_linear(pal(idx * period + drift + 0.3f + 2.f * depth, PAL));
                col = shade(L, base * 0.3f, n, rd, 1.f, ao)
                    + hc * (P[54] * (0.08f + 1.1f * rim) * (1.f + 0.35f * fminf(P[51], 1.f)));
            }
            col += aura_c * (P[53] * 0.6f * sg * sg);               // grazing-step glow on the silhouette
            col = fog_exp2(col, fogc, fmaxf(t - P[72], 0.f), P[28]);
        } else {
            const float aura = expf(-12.f * fmaxf(dmin, 0.f));
            const float bc = length(ro - rd * dot(ro, rd));          // ray's closest approach to the centre
            const float fade = smoothstep(P[45], 0.72f * P[45], bc); // no visible edge at the bound sphere
            col += aura_c * (P[53] * fade * (0.55f * aura + 0.6f * sg * sg));
        }
    }
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"iters": ITERS, "steps": STEPS}


def _de_host(p, n: float, nf: float, fr: float, jb: float, jc, M) -> float:
    """Scalar float64 copy of de_bulb (world point -> object space), for the eye-safety check."""
    x = M[0] * p[0] + M[1] * p[1] + M[2] * p[2]
    y = M[3] * p[0] + M[4] * p[1] + M[5] * p[2]
    z = M[6] * p[0] + M[7] * p[1] + M[8] * p[2]
    cx, cy, cz = x + (jc[0] - x) * jb, y + (jc[1] - y) * jb, z + (jc[2] - z) * jb
    zx, zy, zz = x, y, z
    dr = 1.0
    for _ in range(ITERS):
        r = max(math.sqrt(zx * zx + zy * zy + zz * zz), 1e-6)
        if r > 2.0:
            break
        th = math.acos(min(max(zy / r, -1.0), 1.0)) * n
        ph = math.atan2(zx, zz)
        rn1 = r ** (n - 1.0)
        dr = rn1 * n * dr + (1.0 - jb)
        zr = rn1 * r
        st, ct = math.sin(th), math.cos(th)
        s1, c1 = math.sin(ph * nf), math.cos(ph * nf)
        s2, c2 = math.sin(ph * (nf + 1.0)), math.cos(ph * (nf + 1.0))
        sp, cpp = s1 + (s2 - s1) * fr, c1 + (c2 - c1) * fr
        zx, zy, zz = st * sp * zr + cx, ct * zr + cy, st * cpp * zr + cz
    r = max(math.sqrt(zx * zx + zy * zy + zz * zz), 1e-6)
    return 0.5 * math.log(r) * r / max(dr, 1e-6)


def _pal_host(t: float, P) -> tuple[float, float, float]:
    """Host copy of pal() returning *linear* rgb (approx gamma 2.2), for palette-tinted rig colours."""
    out = []
    for i in range(3):
        c = P[i] + P[3 + i] * math.cos(6.2831853 * (P[6 + i] * t + P[9 + i]))
        out.append(min(max(c, 0.0), 1.0) ** 2.2)
    return out[0], out[1], out[2]


class Mandelbulb(Source):
    name = "mandelbulb"
    label = "Mandelbulb"
    description = ("The power-n Mandelbulb, orbited with glowing orbit-trap colour or flown through from inside, "
                   "its power morphing from blobby to spiky.")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.35, period=1.0, drift=0.03),
        "power": num(2.0, 12.0, 8.0, "Power (blobby → spiky)", step=0.05, tier="regional", basic=True),
        "camera_distance": num(0.6, 3.5, 3.0, "Camera (inside → orbit)", tier="regional", basic=True),
        "trap_colour_mix": num(0.0, 1.0, 0.35, "Colour trap (radial → axis)", tier="regional"),
        "julia_blend": num(0.0, 1.0, 0.0, "Julia blend", tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Glow", tier="regional"),
        "fog": num(0.0, 1.0, 0.3, "Depth fog", tier="regional"),
        "morph_kick": num(0.0, 2.0, 1.0, "Beat kick: morph", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat kick: crevice flash", tier="regional"),
        "orbit_speed": num(-3.0, 3.0, 1.0, "Orbit speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("mandelbulb")
        self.P = cp.zeros(80, cp.float32)
        self.Ph = np.zeros(80, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        r = rng.uniform(0.0, 1.0, 8)
        tau = 2.0 * math.pi
        self.yaw = tau * r[0]           # camera orbit
        self.rot = tau * r[1]           # object tumble
        self.light = tau * r[2]         # key-light azimuth
        self.morph = tau * r[3]         # power wobble phase
        self.pitch_ph = tau * r[4]
        self.jul = tau * r[5]           # julia c wander
        self.breath = tau * r[6]
        self.drift = float(r[7])
        self.e_fast = None
        self.e_slow = None
        self.carve_r = None             # smoothed carve-tube radius

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        if self.e_fast is None or dt <= 0.0:
            self.e_fast = e if self.e_fast is None else self.e_fast
            self.e_slow = e if self.e_slow is None else self.e_slow
        else:
            self.e_fast += (e - self.e_fast) * (1.0 - math.exp(-dt / 0.25))
            self.e_slow += (e - self.e_slow) * (1.0 - math.exp(-dt / 1.5))
        ef = smoothstep(0.1, 0.85, self.e_fast)
        es = smoothstep(0.1, 0.85, self.e_slow)
        k = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ef

        R0 = float(p["camera_distance"])
        s_in = smoothstep(1.5, 0.8, R0)                   # 1 = inside flight, 0 = outside orbit
        # below 1.5 the orbit is compressed into the bulb's outer shell (0.92..1.5), where the crevices are:
        # deeper than that the bulb is solid and there is nothing to fly through
        R_orb = R0 if R0 >= 1.5 else 0.92 + (R0 - 0.6) * (0.58 / 0.9)

        # ---- integrate phases (host, float64; kicks only touch rates)
        orbit = float(p["orbit_speed"])
        self.yaw = wrap(self.yaw + orbit * 0.16 * (1.0 + 0.6 * s_in) * rate * spd * dt, 2.0 * math.pi)
        self.rot = wrap(self.rot + 0.07 * rate * spd * dt, 2.0 * math.pi)
        self.light = wrap(self.light + 0.15 * rate * spd * dt, 2.0 * math.pi)
        self.morph = wrap(self.morph + 0.35 * rate * (1.0 + 3.0 * float(p["morph_kick"]) * k) * spd * dt,
                          2.0 * math.pi)
        self.pitch_ph = wrap(self.pitch_ph + 0.05 * (0.6 + 0.8 * ef) * spd * dt, 2.0 * math.pi)
        self.jul += 0.08 * rate * spd * dt
        self.breath = wrap(self.breath + 0.4 * (0.5 + 0.8 * ef) * spd * dt, 2.0 * math.pi)
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- power: base + energy-driven wobble (amplitude slewed; gentler at low powers, where it matters more)
        base = float(p["power"])
        amp = 1.4 * (0.5 + 0.5 * es ** 1.5) * (0.35 + 0.65 * smoothstep(2.0, 8.0, base))
        n = min(max(base + amp * math.sin(self.morph), 2.0), 12.0)
        nf = math.floor(n)
        fr = n - nf
        jb = float(p["julia_blend"])
        jc = (0.62 * math.sin(self.jul), 0.45 * math.cos(0.71 * self.jul + 1.0), 0.62 * math.sin(0.53 * self.jul + 2.0))
        M = rot_matrix((0.55, 1.0, 0.3), self.rot)
        bound = 1.35 + 0.65 * smoothstep(5.0, 2.0, n) + 0.3 * jb

        # ---- camera: orbit outside looking in, or fly the orbit circle inside looking ahead
        R = R_orb * (1.0 + 0.03 * math.sin(self.breath))
        pitch = (0.38 * (1.0 - s_in) + 0.12 * s_in) * math.sin(self.pitch_ph)
        eye = orbit_eye((0.0, 0.0, 0.0), R, self.yaw, pitch)
        sgn = 1.0 if orbit >= 0.0 else -1.0
        tx, tz = math.cos(self.yaw) * sgn, math.sin(self.yaw) * sgn
        hx, hz = -eye[0], -eye[2]
        hl = max(math.hypot(hx, hz), 1e-6)
        look_in = (eye[0] + tx + 0.35 * hx / hl, eye[1] - 0.05, eye[2] + tz + 0.35 * hz / hl)
        target = tuple(s_in * a for a in look_in)
        roll = (0.02 + 0.10 * s_in) * math.sin(1.3 * self.pitch_ph + 0.5)
        fov = 50.0 + 20.0 * s_in + 2.0 * ef * math.sin(self.breath * 2.0)
        cam = camera_pack(eye, target, fov_deg=fov, roll=roll)

        # ---- carve tube along the orbit circle: the inside flight's tunnel, widened if the bulb reaches the eye
        ahead = orbit_eye((0.0, 0.0, 0.0), R, self.yaw + 0.25 * sgn, pitch)
        de_min = min(_de_host(eye, n, nf, fr, jb, jc, M), _de_host(ahead, n, nf, fr, jb, jc, M))
        need = 0.16 * (1.0 - smoothstep(0.0, 0.08, de_min))
        c_tgt = max(0.15 * s_in, need)
        if self.carve_r is None or dt <= 0.0:
            self.carve_r = c_tgt if self.carve_r is None else self.carve_r
        else:
            tau_c = 0.12 if c_tgt > self.carve_r else 1.0
            self.carve_r += (c_tgt - self.carve_r) * (1.0 - math.exp(-dt / tau_c))

        # ---- light rig (palette-tinted fill/rim; key azimuth rotates)
        pc = palette_coeffs(p["palette"])
        fill = _pal_host(self.drift + 0.6, pc)
        rimc = _pal_host(self.drift + 0.25, pc)
        kg = 0.9 + 0.3 * ef
        rig = rig_pack(key_dir=(0.8 * math.cos(self.light), 0.55, 0.8 * math.sin(self.light)),
                       key_col=(1.6 * kg, 1.45 * kg, 1.3 * kg),
                       fill_col=tuple(0.08 + 0.25 * c for c in fill), rim_col=tuple(0.15 + 0.5 * c for c in rimc),
                       amb_col=(0.025, 0.025, 0.03), spec=0.55, shininess=36.0)

        glow = float(p["glow"])
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = pc
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = p["intensity"] * 1.1
        Ph[28] = float(p["fog"]) * 1.6 * (1.1 - 0.3 * ef)
        Ph[29] = n
        Ph[30] = nf
        Ph[31] = fr
        Ph[32] = jb
        Ph[33:36] = jc
        Ph[36:45] = M
        Ph[45] = bound
        Ph[46] = self.carve_r if self.carve_r > 2e-3 else 0.0
        Ph[47] = R * math.cos(pitch)
        Ph[48] = R * math.sin(pitch)
        Ph[49] = p["trap_colour_mix"]
        Ph[50] = glow * (0.35 + 0.5 * ef)
        Ph[51] = 3.0 * float(p["flash_kick"]) * k * k
        Ph[52] = 0.7 + 0.9 * ef
        Ph[53] = glow * (0.25 + 0.45 * ef) * (1.0 + 0.3 * k)
        Ph[54] = glow * (0.35 + 0.35 * ef)
        Ph[55:72] = rig
        Ph[72] = max(R - 1.3, 0.0)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
