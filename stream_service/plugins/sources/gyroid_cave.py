"""Gyroid cave: a flight through caves of triply-periodic minimal surfaces (gyroid / Schwarz-P / diamond blend).

Source: Original (inspired by https://en.wikipedia.org/wiki/Gyroid, https://www.shadertoy.com/view/NsBfW1 and
        https://www.shadertoy.com/view/WtscW4; TPMS formulas are public maths, no shader code copied)
Author: smfx
License: MIT
license_class: permissive

The cave wall is the sheet |f| < thickness of a blended TPMS field f (gyroid -> Schwarz-P -> diamond, each laid out
so that one of its labyrinth channels runs along the flight axis with f ~ 1).  The domain is bent along a gently
meandering centre line, sine-warped, and a soft tube is carved along the path so the eye is always in open space
(this replaces a host DE check: the carve guarantees clearance whatever the morph/warp/thickness).  An optional
inner lattice (double_layer) grows a skeletal network of the same surface at twice the frequency from nothing.
Walls are lit by a head lamp, with an iridescent fresnel sheen and flowing contour lines; exp2 fog in a
palette colour gives the glowing depth.

All field-space phases are host-integrated in float64 and wrapped by the field period (2*pi), and every in-kernel
use of the field z coordinate is periodic in 2*pi, so frequency changes and long runs never jump.

Macros: energy (e' = smoothstep(0.1, 0.85, e)) -> flight speed x(0.4 + 1.2e'), breathing rate and amplitude
(shell thickness wave), warp amplitude and phase rate, contour glow and flow rate, inner-lattice glow, depth glow,
camera sway/roll.  pulse (k = pulse * (0.3 + 0.7e)) -> contour line flash near the camera and breathing-amplitude
kick (flash_kick), dolly-rate lurch (lurch_kick).  speed scales every rate; palette indexes walls, contours,
lattice and fog via pal(field * period + drift); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, smoothstep, wrap
from stream_service.plugins._lib.upsample import internal_size, present

TAU = 2.0 * math.pi
MAX_STEPS = 90
TMAX = 30.0
TUBE_R = 0.8          # carved flight tube radius (world units)
LOOK = 3.0            # look-ahead distance along the path
N_P = 64

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density | 29 freq | 30 field z offset
#  31 wG | 32 wP | 33 wD | 34 thickness | 35 breathe amp | 36 breathe phase | 37 warp amp | 38 warp phase
#  39 strut level (>= 1.55: off) | 40 tube radius | 41 step scale | 42 inv Lipschitz main | 43 meander ax
#  44 ay | 45 kx | 46 ky | 47 phx | 48 phy | 49 contour flow | 50 contour glow | 51 contour flash
#  52 fog glow | 53 lamp | 54 strut inv Lipschitz | 55 strut emissive | 56 tmax | 57 ao scale
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define CAVE_STEPS %(steps)d

// Blended TPMS field.  Gyroid shifted by (pi/2, 0, 0) and Schwarz-P (x 0.5) at the origin each have a channel along
// z with f >= 0.5; the diamond is rotated 45 deg about y and scaled by sqrt(2) so its [110] channel lies on z with
// D = 1 there (and stays 2*pi periodic in z).
__device__ __forceinline__ float tpms(float3 q, const float* __restrict__ P) {
    float f = 0.f;
    if (P[31] > 0.f || P[32] > 0.f) {
        float sx, cx, sy, cy, sz, cz;
        sincosf(q.x, &sx, &cx); sincosf(q.y, &sy, &cy); sincosf(q.z, &sz, &cz);
        f += P[31] * (cx * cy + sy * cz - sz * sx);
        f += P[32] * 0.5f * (cx + cy + cz);
    }
    if (P[33] > 0.f) {
        float sa, ca;
        sincosf(1.41421356f * q.y, &sa, &ca);
        f += P[33] * (cosf(2.f * q.x) * ca - sa * sinf(2.f * q.z));
    }
    return f;
}
__device__ __forceinline__ float gyroid(float3 q) {
    float sx, cx, sy, cy, sz, cz;
    sincosf(q.x, &sx, &cx); sincosf(q.y, &sy, &cy); sincosf(q.z, &sz, &cz);
    return sx * cy + sy * cz + sz * cx;
}
// World -> bent (tube-relative xy) and warped field coordinates.
__device__ __forceinline__ float3 cave_q(float3 p, const float* __restrict__ P, float2& pb) {
    const float bx = p.x - P[43] * sinf(P[45] * p.z + P[47]);
    const float by = p.y - P[44] * sinf(P[46] * p.z + P[48]);
    pb = f2(bx, by);
    const float fr = P[29];
    float3 q = f3(bx * fr, by * fr, p.z * fr + P[30]);
    const float A = P[37], ph = P[38];
    if (A > 0.f) {
        const float3 w = f3(sinf(q.y + ph), sinf(q.z + ph + 2.1f), sinf(q.x - ph + 4.2f)) * A;
        q += w;
    }
    return q;
}
__device__ __forceinline__ float scene(float3 p, const float* __restrict__ P, float& ds, float3& qo) {
    float2 pb;
    const float3 q = cave_q(p, P, pb);
    qo = q;
    const float f = tpms(q, P);
    const float th = P[34] + P[35] * (0.65f * sinf(q.z - P[36]) + 0.35f * sinf(q.x + q.y + 2.f * P[36]));
    float d = (fabsf(f) - th) * P[42];
    ds = 1e9f;
    if (P[39] < 1.55f) {                                         // inner skeletal lattice at 2x frequency
        const float f2v = tpms(q * 2.f + f3(1.3f, 0.7f, 2.1f), P);
        ds = (P[39] - f2v) * P[54];
        d = smin(d, ds, 0.12f);
    }
    return smax(d, P[40] - length(pb), 0.35f);                   // carve the flight tube
}

SDF_KERNEL
void gyroid_cave(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    auto map = [&](float3 p) { float ds_; float3 q_; return scene(p, P, ds_, q_); };

    const float tmax = P[56];
    const Hit h = march(map, ro, rd, 0.04f, tmax, CAVE_STEPS, pix, P[41]);
    const bool hit = h.hit || (h.steps >= CAVE_STEPS && h.t < tmax);
    const float t = hit ? h.t : tmax;

    const float drift = P[26], period = P[25];
    const float* PAL = P + 13;
    const float3 fogc = srgb_to_linear(pal(drift + 0.55f, PAL)) * P[52] + f3(0.003f, 0.004f, 0.008f);
    float3 col = fogc;
    if (hit) {
        const float3 pos = ro + rd * t;
        float ds; float3 q;
        const float dh = scene(pos, P, ds, q);
        const float3 n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 1e-3f));
        const float ao = calc_ao(map, pos, n, P[57], 3);
        const float ndv = saturate(dot(n, -rd));
        const float fre = 1.f - ndv;

        // palette / contour coordinates (periodic in the field: no jumps when the z offset wraps)
        const float gc = gyroid(f3(q.z + 1.1f, q.x + 0.4f, q.y + 2.0f));
        const float idx = 0.32f * gc + 0.22f * sinf(q.z + 0.6f * gc) + 0.012f * t;
        const float u = 0.3f * gc + q.z * (1.f / SDF_PI);
        const float fw = 2.f * P[29] * 0.8f * t * pix / fmaxf(ndv, 0.2f);
        const float g = fract(2.f * u - P[49]);
        const float dl = fminf(g, 1.f - g);
        const float line = (1.f - smoothstep(0.045f, 0.045f + fw + 1e-3f, dl)) * saturate(1.6f - 2.5f * fw);

        // head lamp just above the eye
        const float3 Lp = ro + cam.up * 0.3f;
        float3 lv = Lp - pos;
        const float ld = length(lv);
        lv = lv / fmaxf(ld, 1e-4f);
        const float att = P[53] / (1.f + 0.035f * ld * ld);
        const float dif = saturate(dot(n, lv));
        const float spe = powf(saturate(dot(n, normalize(lv - rd))), 40.f) * (0.3f + 0.7f * fre);

        const float3 alb = srgb_to_linear(pal(idx * period + drift, PAL));
        const float3 iri = srgb_to_linear(pal(idx * period * 0.5f + 1.2f * fre + drift + 0.3f, PAL));
        const float3 lc = srgb_to_linear(pal(idx * period + 0.5f + drift, PAL));
        float3 c = alb * (dif * att * 0.9f + 0.05f) * ao
                 + iri * (fre * fre * 0.55f * ao * (0.4f + 0.6f * fminf(att, 1.5f)))
                 + f3(spe * att * 0.55f);
        c += lc * (line * (P[50] + P[51] * expf(-0.12f * t)));
        if (ds < dh + 0.08f) {                                   // inner lattice: glowing veins
            const float wv = saturate(1.f - (ds - dh) * 12.5f);
            const float3 sc = srgb_to_linear(pal(idx * period + 0.33f + 0.4f * fre + drift, PAL));
            const float3 cs = sc * ((dif * att + 0.08f) * ao + P[55] * (0.4f + 0.6f * fre)) + f3(spe * att * 0.8f);
            c = mix(c, cs, wv);
        }
        col = c;
    }
    float ff = 1.f - expf(-P[28] * P[28] * t * t);
    ff = fmaxf(ff, smoothstep(0.65f * tmax, tmax, t));
    col = mix(col, fogc, ff);
    col = vignette(col, x, y, W, H, 0.35f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"steps": MAX_STEPS}


def _e_curve(e: float) -> float:
    return smoothstep(0.1, 0.85, e)


class GyroidCave(Source):
    name = "gyroid_cave"
    label = "Gyroid cave"
    description = "Fly through breathing caves of gyroid / Schwarz-P / diamond minimal surfaces with iridescent contour lines and glowing fog."
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.03),
        "surface_morph": num(0.0, 1.0, 0.0, "Surface (gyroid / Schwarz-P / diamond)", tier="regional", basic=True),
        "frequency": num(0.4, 2.5, 0.8, "Cell frequency", tier="regional"),
        "shell_thickness": num(0.05, 0.6, 0.22, "Wall thickness", tier="regional", basic=True),
        "warp_amount": num(0.0, 1.0, 0.35, "Warp", tier="regional"),
        "double_layer": num(0.0, 1.0, 0.3, "Inner lattice", tier="regional"),
        "fog": num(0.0, 1.0, 0.4, "Fog", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat flash", tier="regional"),
        "lurch_kick": num(0.0, 2.0, 1.0, "Beat lurch", tier="regional"),
        "travel_speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("gyroid_cave")
        self.P = cp.zeros(N_P, cp.float32)
        self.Ph = np.zeros(N_P, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.ph0 = rng.uniform(0.0, TAU, 4)
        self.zw = 0.0                     # world distance flown (float64, unbounded)
        self.Q = float(rng.uniform(0.0, TAU))   # field-space z (float64, unbounded; wrapped on upload)
        self.breath = 0.0
        self.warp_ph = 0.0
        self.flow = 0.0
        self.cam_ph = float(self.ph0[2])
        self.roll_ph = float(self.ph0[3])
        self.drift = 0.0
        self.e_s = None

    def _centre(self, z: float, ax: float, ay: float, kx: float, ky: float) -> tuple[float, float]:
        return (ax * math.sin(kx * (self.zw + z) + self.ph0[0]), ay * math.sin(ky * (self.zw + z) + self.ph0[1]))

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ec = _e_curve(e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = _e_curve(self.e_s)
        k = pulse * (0.3 + 0.7 * e)
        fk = float(p["flash_kick"])
        lk = float(p["lurch_kick"])

        freq = float(p["frequency"])
        rate = 0.4 + 1.2 * ec

        # ---- integrate rates (kicks only ever add to rates / amplitudes)
        dz = float(p["travel_speed"]) * spd * (1.25 / math.sqrt(freq)) * rate * (1.0 + 1.5 * lk * k) * dt
        self.zw += dz
        self.Q += freq * dz
        self.breath = wrap(self.breath + spd * TAU * 0.2 * rate * dt, TAU)
        self.warp_ph = wrap(self.warp_ph + spd * 0.35 * rate * dt, TAU)
        self.flow = wrap(self.flow + spd * 0.22 * rate * (1.0 + 0.8 * fk * k) * dt, 1.0)
        self.cam_ph += spd * (0.12 + 0.25 * ec) * dt
        self.roll_ph += spd * (0.05 + 0.12 * ec) * dt
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- morph weights and Lipschitz bounds
        m = min(max(float(p["surface_morph"]), 0.0), 1.0)
        wG = max(0.0, 1.0 - 2.0 * m)
        wD = max(0.0, 2.0 * m - 1.0)
        wP = 1.0 - wG - wD
        Lf = 1.6 * wG + 0.9 * wP + 2.4 * wD
        th = float(p["shell_thickness"])
        Ab = th * (0.15 + 0.30 * es) * (1.0 + 0.5 * fk * k)
        Aw = float(p["warp_amount"]) * 0.55 * (0.5 + 0.5 * es)
        inv_l = 1.0 / (freq * (Lf * (1.0 + Aw) + 1.2 * Ab))
        dl = float(p["double_layer"])
        strut_level = 1.6 - 0.75 * dl
        inv_l2 = 1.0 / (2.0 * freq * Lf * (1.0 + Aw))

        # ---- camera: follow the meandering centre line, sway and roll a little
        ax, ay, kx, ky = 0.75, 0.5, 0.15, 0.11
        c0 = self._centre(0.0, ax, ay, kx, ky)
        c1 = self._centre(LOOK, ax, ay, kx, ky)
        sw = 0.10 + 0.10 * es
        eye = (c0[0] + sw * math.sin(self.cam_ph), c0[1] + 0.7 * sw * math.sin(0.73 * self.cam_ph + 1.1), 0.0)
        tgt = (c1[0] + 0.5 * sw * math.sin(self.cam_ph + 0.9), c1[1], LOOK)
        roll = (0.25 + 0.25 * es) * math.sin(self.roll_ph)
        cam = camera_pack(eye, tgt, fov_deg=72.0, roll=roll)

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = 0.02 + 0.12 * float(p["fog"]) * (1.1 - 0.2 * es)
        Ph[29] = freq
        Ph[30] = math.fmod(self.Q, TAU)
        Ph[31], Ph[32], Ph[33] = wG, wP, wD
        Ph[34] = th
        Ph[35] = Ab
        Ph[36] = self.breath
        Ph[37] = Aw
        Ph[38] = self.warp_ph
        Ph[39] = strut_level
        Ph[40] = TUBE_R
        Ph[41] = 0.7
        Ph[42] = inv_l
        Ph[43], Ph[44], Ph[45], Ph[46] = ax, ay, kx, ky
        Ph[47] = wrap(kx * self.zw + self.ph0[0], TAU)
        Ph[48] = wrap(ky * self.zw + self.ph0[1], TAU)
        Ph[49] = self.flow
        Ph[50] = 0.25 + 0.75 * es ** 1.5
        Ph[51] = 2.2 * fk * k
        Ph[52] = 0.12 + 0.14 * es
        Ph[53] = 1.3 + 0.4 * es
        Ph[54] = inv_l2
        Ph[55] = (0.10 + 0.35 * es) * (1.0 + 1.2 * fk * k)
        Ph[56] = TMAX
        Ph[57] = min(max(0.5 / freq, 0.2), 0.9)
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
