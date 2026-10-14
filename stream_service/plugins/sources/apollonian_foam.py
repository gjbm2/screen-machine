"""Apollonian foam: an inversion sphere-packing fractal lifted to 4D and sliced by a slowly rotating hyperplane.

Source: Original (inspired by https://www.shadertoy.com/view/wl3fR7)
Author: smfx (original; fold formula reimplemented; the 4D-slice idea from mrange's 'Slicing a 4D apollian', wl3fR7, CC0; iq's Apollonian shaders are forbidden)
License: MIT (original; add CC0-1.0 if any of mrange's own slice or camera code is copied)
license_class: permissive
Notes: the fold (fract fold to [-1, 1]^4, then sphere inversion k = s / r^2) is the textbook Apollonian/inversion
packing construction, written here from scratch in 4D.  Only the *idea* of embedding the 3D point as (x, y, z, w)
and rotating it in the xw / yw / zw planes comes from mrange's CC0 shader; no shader code, camera or lighting was
copied.  SDF helpers from stream_service/plugins/_lib/sdf3d.py (MIT, see that file).

Look: a big orb of foam (the fractal clipped to a bounding sphere) floating in coloured fog.  Because the slice
through the 4D packing rotates, clusters of bubbles grow, merge and pop like soap foam or coral.  Colour comes from
an orbit trap (min |z|^2 over the orbit) plus the log of the accumulated inversion scale (bubble size), through the
shared palette, with a soap-film fresnel sheen, travelling emissive bands along trap level sets and a near-miss
glow on silhouettes.  The camera orbits the orb and dollies in and out.

Macros (e' = smoothstep(0.05, 0.9, energy), slewed over ~1.2 s for geometry):
  energy -> 4D slice rotation rate (integrated), camera dolly + orbit speed and dolly amplitude, trap glow and
            emissive band gain, inversion s (knob x 1.36 x 0.92 .. 1.1: more chaotic foam), fog thinning;
  pulse  (k = pulse * (0.35 + 0.65 e)) -> trap emissive flash (``flash_kick``), glow lift, a small inversion-s
            bump (+3% x k, decays with the envelope) and a slice-rate lurch (rates only, never positions);
  speed scales every rate; ``slice_speed`` scales the 4D morph only; drift integrates the palette; intensity is
  output gain.  ``iterations`` (5-8) eases by cross-blending the neighbouring integer depths; ``slice_plane``
  (0 = xw, 1 = yw, 2 = zw) continuously re-weights the three rotation rates.

Perf plan: render_scale 0.45, bounding sphere, <= 80 steps with a pixel-cone epsilon, step-count AO + 3-tap AO,
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
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, wrap
from stream_service.plugins._lib.upsample import internal_size, present

STEPS = 80         # march steps
MAXIT = 9          # fractal loop bound (iterations knob <= 8, +1 for the cross-blend)
NP = 64            # P floats
TAU = 2.0 * math.pi
S_4D = 1.36       # knob -> 4D inversion factor (default 1.1 -> s ~1.5 at mid energy)

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog density (per world unit)
#  29 zoom (fractal units per world unit) | 30 inversion s | 31 sign offset | 32 iterations (float)
#  33 tube vs sheet | 34..39 cos/sin of the xw, yw, zw slice rotations | 40 w offset | 41 bound radius
#  42 tmax | 43 glow gain | 44 band base | 45 band flash | 46 band phase | 47 band frequency | 48 key gain
#  49 fog brightness | 50 vignette | 51 step scale | 52 pixel-eps factor | 53 headlight | 54 fog start
#  55 AO scale
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define AF_STEPS %(steps)d
#define AF_MAXIT %(maxit)d

__device__ __forceinline__ float af_fold(float v) { const float h = 0.5f * v + 0.5f; return 2.f * (h - floorf(h)) - 1.f; }
// smooth "v - off * sign(v)": pulls the folded point towards the cell centre without a seam at v = 0
__device__ __forceinline__ float af_soff(float v, float off) { return v - off * v * rsqrtf(v * v + 0.0025f); }

// 4D Apollonian (inversion) packing, sliced by the rotated hyperplane.  Returns the world-space distance
// estimate; trap = min |z|^2 over the orbit, lsc = log2 of the accumulated inversion scale (bubble size).
__device__ float af_de(float3 p, const float* __restrict__ P, float& trap, float& lsc) {
    const float zm = P[29];
    float x = p.x * zm, y = p.y * zm, z = p.z * zm, w = P[40];
    { const float c = P[34], s = P[35]; const float nx = c * x + s * w; w = -s * x + c * w; x = nx; }
    { const float c = P[36], s = P[37]; const float ny = c * y + s * w; w = -s * y + c * w; y = ny; }
    { const float c = P[38], s = P[39]; const float nz = c * z + s * w; w = -s * z + c * w; z = nz; }
    const float sk = P[30], off = P[31], tube = P[33];
    const float itf = P[32];
    const int n = max(1, min((int)itf, AF_MAXIT - 1));
    const float fr = fminf(fmaxf(itf - (float)n, 0.f), 1.f);
    const bool blend = fr > 1e-3f;
    float scale = 1.f, tr = 1e9f;
    float d_lo = 0.f, t_lo = 1.f, s_lo = 1.f, d_hi = 0.f, t_hi = 1.f, s_hi = 1.f;
    for (int i = 0; i < AF_MAXIT; ++i) {
        x = af_fold(x); y = af_fold(y); z = af_fold(z); w = af_fold(w);
        if (off > 0.f) { x = af_soff(x, off); y = af_soff(y, off); z = af_soff(z, off); w = af_soff(w, off); }
        const float r2 = x * x + y * y + z * z + w * w;
        const float k = sk / fmaxf(r2, 1e-5f);
        x *= k; y *= k; z *= k; w *= k; scale *= k;
        tr = fminf(tr, r2);
        if (i == n - 1 || i == n) {
            // sheets (inverted images of the y = 0 hyperplane) <-> tubes (inverted images of the x = z = 0 plane)
            const float term = (1.f - tube) * fabsf(y) + tube * 0.8f * fabsf(sqrtf(x * x + z * z) - 0.18f);
            if (i == n - 1) { d_lo = term / scale; t_lo = tr; s_lo = scale; if (!blend) break; }
            else { d_hi = term / scale; t_hi = tr; s_hi = scale; break; }
        }
    }
    float d = d_lo;
    trap = t_lo;
    lsc = log2f(fmaxf(s_lo, 1e-6f));
    if (blend) {
        d = d_lo + (d_hi - d_lo) * fr;
        trap = t_lo + (t_hi - t_lo) * fr;
        lsc = lsc + (log2f(fmaxf(s_hi, 1e-6f)) - lsc) * fr;
    }
    return 0.42f * d / zm;
}

SDF_KERNEL
void apollonian_foam(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pa = pixel_angle(cam, H) * P[52];
    const float R = P[41];
    const float drift = P[26];
    const float2 bs = iSphere(ro, rd, R);
    const bool inb = !(bs.x > bs.y || bs.y < 0.f);
    const float3 fogc = srgb_to_linear(pal(drift + 0.6f, P + 13)) * P[49];
    float3 col = fogc * (0.55f + 0.45f * saturate(0.5f + 0.7f * rd.y));
    const float tmax = fminf(bs.y, P[42]);
    float t = fmaxf(bs.x, 0.f);
    bool hit = false;
    int steps = 0;
    float trap = 1.f, lsc = 0.f, glow = 0.f, gtrap = 1.f, glsc = 0.f, best = 1e9f;
    if (inb) {
        for (int i = 0; i < AF_STEPS; ++i) {
            const float3 p = ro + rd * t;
            float tr, ls;
            const float d = af_de(p, P, tr, ls);
            trap = tr; lsc = ls; steps = i + 1;
            const float pe = fmaxf(pa * t, 1e-6f);
            const float ratio = d / pe;                               // distance in pixel footprints
            glow += saturate(1.f - ratio * 0.1f);
            if (ratio < best) { best = ratio; gtrap = tr; glsc = ls; }
            if (d < pe) { hit = true; break; }
            t += d * P[51];
            if (t > tmax) break;
        }
    }
    const float per = P[25];
    if (inb && (hit || t < tmax)) {                                  // step-limited rays: shade where they stopped
        const float3 pos = ro + rd * t;
        auto map = [&](float3 q) { float a, b; return af_de(q, P, a, b); };
        const float3 n = calc_normal(map, pos, 0.5f * pa * t + 1e-5f);
        const float ci = 0.11f * lsc + 0.55f * sqrtf(fmaxf(trap, 0.f));
        const float3 alb = srgb_to_linear(pal(ci * per + drift, P + 13));
        float ao = 1.f - (float)steps / (float)AF_STEPS;
        ao = saturate(ao * ao * 1.2f + 0.1f);
        ao *= 0.35f + 0.65f * calc_ao(map, pos, n, P[55], 3);
        const float3 kd = normalize(f3(0.5f, 0.75f, -0.4f));
        const float dif = saturate(dot(n, kd));
        const float hd = saturate(dot(n, -rd));
        const float fre = 1.f - hd;
        const float3 hv = normalize(kd - rd);
        const float spe = powf(saturate(dot(n, hv)), 40.f) * dif;
        const float3 key = f3(1.2f, 1.1f, 1.0f) * P[48];
        col = alb * (key * dif * 0.85f + f3(P[53]) * (0.3f + 0.7f * hd * hd) + fogc * 2.2f * (0.6f + 0.4f * n.y)) * ao;
        // soap-film sheen: the fresnel rim shifts along the palette
        const float3 sheen = srgb_to_linear(pal(ci * per + drift + 0.33f + 0.5f * fre, P + 13));
        col += sheen * (fre * fre * fre * 0.9f + spe * 0.6f) * ao;
        // trap emissive: bands travelling along orbit-trap level sets, flashing on the beat
        const float v = 0.5f + 0.5f * cosf(SDF_TAU * (ci * P[47] - P[46]));
        const float v2 = v * v, v4 = v2 * v2;
        col += srgb_to_linear(pal(ci * per + drift + 0.5f, P + 13)) * (v4 * v4) * (P[44] + P[45]) * (0.5f + 0.5f * ao);
        col = fog_exp(col, fogc, fmaxf(t - P[54], 0.f), P[28]);
    }
    // near-miss glow on silhouettes, coloured by the orbit trap
    const float g = saturate(glow * (1.f / 12.f));
    const float3 gcol = srgb_to_linear(pal((0.11f * glsc + 0.55f * sqrtf(fmaxf(gtrap, 0.f))) * per + drift + 0.25f, P + 13));
    col += gcol * (g * g * P[43]);
    col = vignette(col, x, y, W, H, P[50]);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"steps": STEPS, "maxit": MAXIT}


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class ApollonianFoam(Source):
    name = "apollonian_foam"
    label = "Apollonian Foam"
    description = ("An Apollonian sphere-packing fractal lifted to 4D and sliced by a slowly rotating hyperplane: "
                   "bubbles grow, merge and pop like soap foam or coral, with orbit-trap glow (3D raymarched).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.45, period=1.0, drift=0.03),
        "inversion_factor": num(0.9, 1.3, 1.1, "Inversion factor", tier="regional", basic=True),
        "sign_offset": num(0.0, 0.08, 0.02, "Fold offset", tier="regional"),
        "slice_plane": num(0.0, 2.0, 0.6, "Slice plane (xw .. yw .. zw)", tier="regional"),
        "iterations": num(5.0, 8.0, 7.0, "Iterations (detail)", step=0.1, tier="regional"),
        "tube_vs_sheet": num(0.0, 1.0, 0.0, "Sheets -> tubes", tier="regional", basic=True),
        "density": num(0.5, 1.6, 0.85, "Foam density", tier="regional"),
        "glow": num(0.0, 2.0, 1.0, "Trap glow", tier="regional"),
        "flash_kick": num(0.0, 2.0, 1.0, "Beat flash", tier="regional"),
        "slice_speed": num(0.0, 3.0, 1.0, "Slice morph speed", unit="×", tier="realtime", bind="rate"),
    }

    # ------------------------------------------------------------------ lifecycle
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("apollonian_foam")
        self.P = cp.zeros(NP, cp.float32)
        self.Ph = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        # host-integrated phases (seeded starting points so different seeds show different slices/angles)
        self.ang = rng.uniform(0.0, TAU, 3)
        self.wph = float(rng.uniform(0.0, TAU))
        self.yaw = float(rng.uniform(0.0, TAU))
        self.dolly = float(rng.uniform(0.0, TAU))
        self.pitch_ph = float(rng.uniform(0.0, TAU))
        self.band = 0.0
        self.drift = 0.0
        self.e_s = None
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))

    def close(self) -> None:
        self.buf = None
        self.P = None

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.5)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        e1 = _ss(0.05, 0.9, e)
        a = (1.0 - math.exp(-dt / 1.2)) if dt > 0 else 1.0
        self.e_s = e1 if self.e_s is None else self.e_s + (e1 - self.e_s) * a
        es = self.e_s
        k = pulse * (0.35 + 0.65 * e)

        # palette (session palette wins; eased so a change never snaps)
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # 4D slice: three plane rotations, re-weighted continuously by slice_plane (0 xw, 1 yw, 2 zw)
        spl = float(p["slice_plane"])
        wts = [max(0.0, 1.0 - abs(spl - c)) for c in (0.0, 1.0, 2.0)]
        base = (0.035 + 0.13 * e1) * (1.0 + 0.8 * k) * spd * float(p["slice_speed"])
        for i, mul in enumerate((1.0, 0.83, 0.71)):
            self.ang[i] = wrap(self.ang[i] + base * mul * (0.25 + 0.75 * wts[i]) * dt, TAU)
        self.wph = wrap(self.wph + 0.6 * base * dt, TAU)

        # camera: orbit + dolly (energy drives both rates and the dolly amplitude)
        self.yaw = wrap(self.yaw + spd * (0.03 + 0.07 * e1) * dt, TAU)
        self.dolly = wrap(self.dolly + spd * (0.05 + 0.2 * e1) * (1.0 + 0.5 * k) * dt, TAU)
        self.pitch_ph = wrap(self.pitch_ph + spd * 0.037 * dt, TAU)
        self.band = wrap(self.band + spd * (0.1 + 0.3 * e1) * (1.0 + 2.0 * k) * dt, 1.0)

        R = 2.0
        amp = 0.22 + 0.2 * es
        dist = R * (1.42 + amp * (0.5 - 0.5 * math.cos(self.dolly)))
        pitch = 0.2 + 0.25 * math.sin(self.pitch_ph)
        eye = orbit_eye((0.0, 0.0, 0.0), dist, self.yaw, pitch)
        roll = 0.04 * math.sin(0.61 * self.pitch_ph + 0.7)
        cam = camera_pack(eye, (0.0, 0.0, 0.0), fov_deg=50.0, roll=roll)

        # In 4D the extra w^2 in r^2 weakens the inversion: the knob's 0.9-1.3 (3D convention) maps to s ~1.2-1.8
        s = float(p["inversion_factor"]) * S_4D * (0.92 + 0.18 * es) * (1.0 + 0.03 * k)
        glow = float(p["glow"])

        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = self.pal
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * (0.9 + 0.25 * e1)
        Ph[28] = 0.32 * (1.2 - 0.5 * es)
        Ph[29] = p["density"]
        Ph[30] = s
        Ph[31] = p["sign_offset"]
        Ph[32] = min(max(float(p["iterations"]), 1.0), MAXIT - 1.0)
        Ph[33] = min(max(float(p["tube_vs_sheet"]), 0.0), 1.0)
        for i in range(3):
            Ph[34 + 2 * i] = math.cos(self.ang[i])
            Ph[35 + 2 * i] = math.sin(self.ang[i])
        Ph[40] = 0.125 + 0.35 * math.sin(self.wph)
        Ph[41] = R
        Ph[42] = dist + R + 0.5
        Ph[43] = glow * (0.4 + 0.6 * e1) * 0.6 * (1.0 + 0.6 * k)
        Ph[44] = glow * (0.08 + 0.3 * e1)
        Ph[45] = float(p["flash_kick"]) * 1.6 * k
        Ph[46] = self.band
        Ph[47] = 3.0
        Ph[48] = 0.85 + 0.2 * e1
        Ph[49] = 0.12 + 0.04 * e1
        Ph[50] = 0.35
        Ph[51] = 0.85
        Ph[52] = 1.5
        Ph[53] = 0.55
        Ph[54] = dist - R
        Ph[55] = 0.12
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
