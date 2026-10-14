"""Kaliset nebula: a volumetric kaliset-style fractal flown around in slow orbit.

Source: https://www.shadertoy.com/view/NljfzV
Author: Danil (morimea), NljfzV ("License - CC0 or use as you wish")
License: CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the map() fold, the density-adaptive volume march and the colour
        function are ported faithfully; the eight hand-tuned parameter sets become the preset_a / preset_b
        enums with a continuous morph between them (plus p0 scale / p1 / p2 / p3 offsets and a fractional
        iteration count, so every knob eases); the tile slider, god-ray overlay and timers of the original are
        dropped.  New: a palette colour path blended with the original colour function, per-pixel blue-noise
        start jitter instead of the original's fixed slices, host-integrated orbit / elevation / parameter
        wander, energy- and beat-driven density, iteration fade and brightness.)
Licence seen: the header of the shader source, "// Created by Danil (2022+) https://github.com/danilw" /
        "// License - CC0 or use as you wish" (local copy: out/research-3d-abstract/code/NljfzV_morimea.glsl).

The fractal: for each sample point c, iterate p <- p0 * |p| / max(dot(p, p + p2), 1e-4) + p1, then swizzle
p = p.zxy, and accumulate density exp(-33 * |dot(p, p3 * c)|): thin sheets wherever the orbit is orthogonal to
the start point.  The march takes a fixed number of steps whose length shrinks in dense regions
(dt * exp(-1.5 * density)), accumulating emission.  Tiny changes to p1 restructure the whole image, so the
eight parameter sets look nothing alike (nebulae, corals, crystals, flowers) and morphing between them is a
journey through very different structures.

Macros: energy -> parameter-wander speed, orbit and elevation speed, density gain, the fade-in of one extra
fractal iteration, brightness.  pulse -> a density and brightness flash (gains only).  speed scales the orbit
and wander rates; palette / palette_period / drift_speed colour the palette path (index = density and the
diagonal colour coordinate * period + drift); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

# The eight hand-tuned parameter sets of the original (p0 scale, p1 offset, p2 inversion bias, p3 sheet
# frequency, iterations), in the original's order.
SETS = {
    "nebula":    (0.70, 0.07, -0.15, 0.465, 5),
    "veil":      (0.70, 0.07, -0.15, 0.184, 5),
    "crystal":   (0.70, -0.107, 1.84, 0.465, 10),
    "coral":     (0.70, -0.07, -0.15, 0.465, 5),
    "flower":    (0.57, -0.69, -0.015, 0.2465, 5),
    "lotus":     (0.697, -0.69, 0.015, 0.1465, 5),
    "filament":  (0.09, -0.1069, -0.02015, 1.465, 10),
    "bloom":     (2.57, -0.69, -0.015, 0.2465, 5),
}
SET_NAMES = tuple(SETS)
BN = 64          # blue-noise tile size

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 density scale | 29..32 k0..k3
#  33 iterations (fractional) | 34 colour shift | 35 steps | 36 step length | 37 palette mix | 38 colour centre
#  39 jitter | 40 hot (dense-core whitening)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define BN 64

// morimea's fold (NljfzV map()): returns the accumulated sheet density at c.
__device__ __forceinline__ float kmap(const float3 c, const float k0, const float k1, const float k2,
                                      const float k3, const int nfull, const float wlast) {
    const float3 cc = c * k3;
    float3 p = c;
    float res = 0.f;
    for (int i = 0; i < nfull; ++i) {
        const float den = fmaxf(dot(p, p) + k2 * (p.x + p.y + p.z), 1e-4f);
        p = abs(p) * (k0 / den) + k1;
        p = f3(p.z, p.x, p.y);
        res += __expf(-33.f * fabsf(dot(p, cc)));
    }
    if (wlast > 0.f) {                                           // fractional extra iteration, faded in
        const float den = fmaxf(dot(p, p) + k2 * (p.x + p.y + p.z), 1e-4f);
        p = abs(p) * (k0 / den) + k1;
        p = f3(p.z, p.x, p.y);
        res += wlast * __expf(-33.f * fabsf(dot(p, cc)));
    }
    return res;
}

__device__ __forceinline__ float3 brb(float3 c) { return f3(c.z, c.x, c.z); }
__device__ __forceinline__ float3 rrb(float3 c) { return f3(c.x, c.x, c.z); }
__device__ __forceinline__ float3 ggr(float3 c) { return f3(c.y, c.y, c.x); }

// morimea's postProcess (NljfzV): a nonlinear grade; ct 0..1 sweeps two colour variants.
__device__ __forceinline__ float3 post_process(float3 col, float ct) {
    col = col * 0.85f + 0.85f * col * brb(col);
    col = col * 0.6f + 0.64f * col * col * (3.f - 2.f * col) + 0.5f * col * col;
    col = col + 0.4f * (rrb(col) - f3(dot(col, f3(0.33f))));
    const float3 c1 = col - 0.344f * (brb(col) - f3(dot(col, f3(0.33f))));
    const float3 c2 = col + 0.64f * (ggr(col) - f3(dot(col, f3(0.33f))));
    col = mix(col, c1, fminf(ct * 2.f, 1.f));
    col = mix(col, c2, -clamp(ct * 2.f - 1.f, 0.f, 1.f));
    return col * 1.5f + 0.5f * col * col;
}

SDF_KERNEL
void kaliset_nebula(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                    const float* __restrict__ bn)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float k0 = P[29], k1 = P[30], k2 = P[31], k3 = P[32];
    const float itf = P[33];
    const int nfull = (int)floorf(itf);
    const float wlast = itf - (float)nfull;
    const int steps = (int)P[35];
    const float dt0 = P[36], cscale = P[28], center = P[38];
    const float* PAL = P + 13;
    const float period = P[25], drift = P[26], hot = P[40];

    float t = P[39] * dt0 * bn[(y & (BN - 1)) * BN + (x & (BN - 1))];
    float c = 0.f;
    float3 accN = f3(0.f), accP = f3(0.f);
    for (int i = 0; i < steps; ++i) {
        t += dt0 * __expf(-1.5f * c);
        const float3 pos = ro + rd * t;
        c = kmap(pos, k0, k1, k2, k3, nfull, wlast) * cscale;
        const float dist = fabsf(pos.x + pos.y + center) * 2.5f;
        const float c2 = c * c;
        accN += f3(1.5f * c2 - c * dist, c2 - c, c);
        const float3 pc = srgb_to_linear(pal((0.22f * dist + 0.10f * c) * period + drift, PAL));
        accP += pc * (c * (1.f + hot * c));
    }
    const float wstep = dt0 * (1.f / (0.152f * 48.f));          // the original's 48 x 0.152 normalisation
    // original colour path (sRGB-ish values, as in the GLSL)
    float3 colN = saturate(accN * (0.18f * wstep));
    colN = saturate(post_process(colN, P[34]));
    // palette path: linear emission, Reinhard-ish shoulder
    float3 colP = accP * (0.075f * wstep);
    colP = linear_to_srgb(tonemap_aces(colP));
    float3 col = mix(colN, colP, P[37]) * P[27];
    // soft shoulder so the gain never clips to flat white
    col = f3(1.f) - exp(-col * 1.1f);
    col = col * (1.f / (1.f - __expf(-1.1f)));
    store_srgb(out, x, y, W, saturate(col));
}
"""


def _blue_noise(seed: int, n: int = BN) -> np.ndarray:
    """A 64x64 blue-ish noise tile in [0, 1): white noise, high-passed in the frequency domain, rank-uniformised."""
    rng = np.random.default_rng(seed + 7919)
    w = rng.standard_normal((n, n))
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.fftfreq(n)[None, :]
    r = np.sqrt(fx * fx + fy * fy)
    hp = 1.0 - np.exp(-(r / 0.22) ** 2 * 2.0)
    f = np.real(np.fft.ifft2(np.fft.fft2(w) * hp))
    ranks = np.argsort(np.argsort(f.ravel()))
    return ((ranks + 0.5) / ranks.size).reshape(n, n).astype(np.float32)


def _vnoise1(x: float, seed: int) -> float:
    """Smooth 1D value noise in [-1, 1] (host side, deterministic)."""
    i = math.floor(x)
    f = x - i
    f = f * f * (3.0 - 2.0 * f)

    def h(k: int) -> float:
        v = (k * 374761393 + seed * 668265263) & 0xFFFFFFFF
        v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
        return ((v ^ (v >> 16)) & 0xFFFFFF) / float(0xFFFFFF) * 2.0 - 1.0

    return h(i) + (h(i + 1) - h(i)) * f


class KalisetNebula(Source):
    name = "kaliset_nebula"
    label = "Kaliset nebula"
    description = ("A volumetric kaliset-style fractal (nebulae, corals, crystals, flowers) in slow orbit, "
                   "morphing between eight hand-tuned parameter sets (3D volumetric).")
    params = {
        **standard_scene_params(palette="dusk", render_scale=0.4, period=1.0, drift=0.02),
        "preset_a": enum(SET_NAMES, "nebula", "Structure A", tier="structural"),
        "preset_b": enum(SET_NAMES, "coral", "Structure B", tier="structural"),
        "morph": num(0.0, 1.0, 0.0, "Morph A -> B", tier="regional", basic=True),
        "p0_scale": num(0.7, 1.3, 1.0, "Fold scale", unit="×", tier="regional"),
        "p1": num(-0.06, 0.06, 0.0, "Fold offset (shift)", step=0.001, tier="regional"),
        "p2": num(-0.3, 0.3, 0.0, "Inversion bias (shift)", step=0.005, tier="regional"),
        "p3": num(0.5, 2.0, 1.0, "Sheet frequency", unit="×", tier="regional"),
        "iterations": num(5.0, 10.0, 5.0, "Iterations (minimum)", step=0.1, tier="regional"),
        "colour_shift": num(0.0, 1.0, 0.3, "Colour grade shift", tier="regional"),
        "palette_mix": num(0.0, 1.0, 0.5, "Palette vs original colour", tier="regional"),
        "orbit_speed": num(0.0, 4.0, 1.0, "Orbit speed", unit="×", tier="realtime", bind="rate"),
        "wander": num(0.0, 2.0, 1.0, "Parameter wander", unit="×", tier="realtime", bind="level"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("kaliset_nebula")
        self.P = cp.zeros(48, cp.float32)
        self.Ph = np.zeros(48, np.float32)
        self.seed = int(params["seed"])
        self.bn = cp.asarray(_blue_noise(self.seed))
        rng = np.random.default_rng(self.seed)
        self.yaw = float(rng.uniform(0.0, 2.0 * math.pi))
        self.el_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        self.wd = float(rng.uniform(0.0, 100.0))
        self.drift = 0.0
        self.e_s = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        a = 1.0 - math.exp(-dt / 0.4) if dt > 0 else 1.0
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * a
        es = self.e_s

        # ---- integrate rates (pulse only adds to rates)
        self.yaw += p["orbit_speed"] * spd * (0.035 + 0.10 * es + 0.05 * pulse) * dt
        self.el_ph += p["orbit_speed"] * spd * (0.025 + 0.05 * es) * dt
        self.wd += spd * (0.04 + 0.22 * es ** 1.2 + 0.06 * pulse) * dt
        self.drift = wrap(self.drift + p["drift_speed"] * spd * dt, 1000.0)

        # ---- the morph point: mix of two sets + offsets + a slow wander around it
        A, B = SETS[p["preset_a"]], SETS[p["preset_b"]]
        m = float(p["morph"])
        m = m * m * (3.0 - 2.0 * m)
        k0, k1, k2, k3, it = (a_ + (b_ - a_) * m for a_, b_ in zip(A, B))
        wa = float(p["wander"])
        k0 = k0 * float(p["p0_scale"]) * (1.0 + 0.02 * wa * _vnoise1(self.wd, self.seed))
        k1 = k1 + float(p["p1"]) + 0.010 * wa * _vnoise1(self.wd * 1.3 + 17.0, self.seed + 1)
        k2 = k2 + float(p["p2"]) + 0.03 * wa * _vnoise1(self.wd * 0.9 + 41.0, self.seed + 2)
        k3 = k3 * float(p["p3"]) * (1.0 + 0.06 * wa * _vnoise1(self.wd * 1.1 + 73.0, self.seed + 3))
        itf = max(it, float(p["iterations"])) + 0.9 * es - 0.45
        itf = min(max(itf, 1.0), 12.0)

        # ---- camera: orbit around the origin (z up, as in the original), elevation sways slowly
        el = 0.36 + 0.40 * math.sin(self.el_ph)
        R = 2.05
        yaw = self.yaw
        eye = (R * math.cos(yaw) * math.cos(el), R * math.sin(yaw) * math.cos(el), R * math.sin(el))
        cam = camera_pack(eye, (0.0, 0.0, 0.0), up=(0.0, 0.0, 1.0), fov_deg=80.0)

        steps = 44
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = p["intensity"] * (0.85 + 0.3 * es) * (1.0 + 0.3 * pulse)
        Ph[28] = (0.55 + 0.6 * es) * (1.0 + 0.25 * pulse)
        Ph[29:33] = (k0, k1, k2, k3)
        Ph[33] = itf
        Ph[34] = p["colour_shift"]
        Ph[35] = steps
        Ph[36] = 0.152 * 48.0 / steps
        Ph[37] = p["palette_mix"]
        Ph[38] = -0.35
        Ph[39] = 1.0
        Ph[40] = 0.35
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.bn))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.bn = None
