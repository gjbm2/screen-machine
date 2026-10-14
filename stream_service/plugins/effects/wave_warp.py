"""Wave warp: two crossed trains of travelling waves push the picture sideways and up and down.

Source: Original (inspired by https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Sine%20Warp%20Tile.fs ,
        https://github.com/Vidvox/ISF-Files/blob/master/LICENSE ,
        https://borisfx.com/documentation/sapphire/ofx/warpwaves2)
Author: smfx (original; prior art VIDVOX ISF 'Sine Warp Tile', Resolume 'Wave Warp', Boris FX Sapphire 'WarpWaves', names and ideas only)
License: MIT (original)
license_class: permissive

Written from scratch; no code copied.  Train 1 travels along the direction vector d and shifts the picture across it
(transverse), train 2 travels across d and shifts the picture along it, so cross_mix 0 is a single rolling train and
1 is the crossed "tile" grid.  The wave shape morphs continuously sine -> triangle -> soft square, the wavelength
drifts slowly (+-12 %, host-integrated), and samples fold back at the frame edges (mirror) so no border ever shows.
An optional sheen lights the wave slopes like folds in silk (neutral, multiplicative).  One full-res kernel, one
bilinear tap per pixel.

Realtime: amount -> displacement 0..depth x height through m = smoothstep(amount) (copy below 1e-4, so exact identity
at 0); pulse -> amplitude x (1 + 1.5*kick*pulse*m) and a kick into the host-integrated travel phases, so the waves
surge forward on the beat and never jump.  travel_speed is a rate.  direction eases as a unit vector (never spins
the long way round through the 0/360 wrap).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import enum, num, standard_effect_params

_SRC = HELPERS + r"""
#define WW_TAU 6.28318530718f

// Wave of phase u (cycles): 0 sine, 0.5 triangle, 1 soft square; all peak at u = 0.25, range [-1, 1].
__device__ __forceinline__ float ww_wave(float u, float shape) {
    const float f = u - floorf(u);
    const float s = __sinf(WW_TAU * f);
    if (shape <= 0.f) return s;
    const float g = f - 0.25f;
    const float tri = 4.f * fabsf(g - floorf(g) - 0.5f) - 1.f;
    if (shape <= 0.5f) return s + (tri - s) * (shape * 2.f);
    const float k = 5.f;
    const float sq = s * k * rsqrtf(1.f + s * s * k * k) * 1.0198039f;   // / (k / sqrt(1 + k^2))
    return tri + (sq - tri) * (shape * 2.f - 1.f);
}
__device__ __forceinline__ float ww_fold(float v, float n) {           // mirror at the frame edges
    v = fabsf(v);
    v = n - fabsf(n - fmodf(v, 2.f * n));
    return fminf(fmaxf(v, 0.f), n);
}

extern "C" __global__
void wave_warp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
               const float dx, const float dy, const float k1, const float k2, const float ph1, const float ph2,
               const float a1, const float a2, const float shape, const float sheen, const int mirror)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fW = (float)W, fH = (float)H;
    const float px = x + 0.5f, py = y + 0.5f;
    const float X = (px - 0.5f * fW) / fH, Y = (py - 0.5f * fH) / fH;   // height units, centred
    const float along = X * dx + Y * dy;          // position along d
    const float across = -X * dy + Y * dx;        // position along n = perp(d)
    const float u1 = along * k1 - ph1;            // train 1 travels along d
    const float u2 = across * k2 - ph2;           // train 2 travels along n
    const float w1 = ww_wave(u1, shape), w2 = ww_wave(u2, shape);
    // train 1 shifts across (along n), train 2 shifts along (along d); amplitudes in pixels
    const float sn = a1 * w1, sd = a2 * w2;
    float sx = px + sd * dx - sn * dy;
    float sy = py + sd * dy + sn * dx;
    if (mirror) { sx = ww_fold(sx, fW); sy = ww_fold(sy, fH); }
    float4 c = bilin(src, sx, sy, W, H);
    if (sheen > 1e-4f) {
        const float e = 0.02f;
        const float d1 = (ww_wave(u1 + e, shape) - ww_wave(u1 - e, shape)) * (1.f / (2.f * e * WW_TAU));
        const float d2 = (ww_wave(u2 + e, shape) - ww_wave(u2 - e, shape)) * (1.f / (2.f * e * WW_TAU));
        const float wsum = a1 + a2 + 1e-3f;
        float sl = (a1 * d1 + a2 * d2) / wsum;     // ~[-1, 1] (steeper for square fronts)
        sl = sl * rsqrtf(1.f + 0.25f * sl * sl);
        const float g = 1.f + sheen * 0.32f * sl;
        const float hl = sheen * 26.f * fmaxf(sl, 0.f) * fmaxf(sl, 0.f);
        c = make_float4(c.x * g + hl, c.y * g + hl, c.z * g + hl, 255.f);
    }
    out[y * W + x] = pack(c);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smoothstep(v: float) -> float:
    t = _clamp01(v)
    return t * t * (3.0 - 2.0 * t)


class WaveWarp(Effect):
    name = "wave_warp"
    stage = "warp"
    label = "Wave warp"
    description = ("Crossed trains of travelling waves push the picture sideways and up and down; sine to triangle "
                   "to square, mirror edges, surges on the beat.")
    params = {
        **standard_effect_params(),
        "wavelength": num(0.05, 1.0, 0.32, "Wavelength", unit="× height", step=0.01, tier="regional", basic=True),
        "direction": {**num(0.0, 360.0, 20.0, "Direction", unit="°", step=1.0, tier="regional"), "x-ease": False},
        "cross_mix": num(0.0, 1.0, 0.55, "Single train ↔ crossed grid", tier="regional", basic=True),
        "wave_shape": num(0.0, 1.0, 0.0, "Sine ↔ triangle ↔ square", tier="regional"),
        "depth": num(0.005, 0.08, 0.03, "Depth at amount 1", unit="× height", step=0.001, tier="regional"),
        "sheen": num(0.0, 1.0, 0.25, "Fold sheen", tier="regional"),
        "kick": num(0.0, 2.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "travel_speed": num(0.0, 2.0, 0.3, "Travel speed", unit="waves/s", tier="realtime", bind="rate"),
        "edge_mode": enum(["mirror", "clamp"], "mirror", "Edges", tier="structural"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("wave_warp")
        seed = 1
        if isinstance(params, dict):
            try:
                seed = int(params.get("seed", 1))
            except (TypeError, ValueError):
                seed = 1
        rng = np.random.default_rng(seed)
        self.ph1 = float(rng.uniform(0.0, 1.0))
        self.ph2 = float(rng.uniform(0.0, 1.0))
        self.lam_ph = float(rng.uniform(0.0, 2.0 * math.pi))
        th = math.radians(float(self.easer.values().get("direction", 20.0)))
        self.vx, self.vy = math.cos(th), math.sin(th)

    def _ease_direction(self, deg: float, dt: float) -> tuple[float, float]:
        th = math.radians(float(deg))
        tx, ty = math.cos(th), math.sin(th)
        if dt > 0.0:
            a = 1.0 - math.exp(-min(dt, 0.5) / 1.2)
            vx = self.vx + (tx - self.vx) * a
            vy = self.vy + (ty - self.vy) * a
            n = math.hypot(vx, vy)
            if n < 1e-3:            # exactly opposite target: step sideways instead of through zero
                vx, vy = self.vx - self.vy * a, self.vy + self.vx * a
                n = math.hypot(vx, vy)
            self.vx, self.vy = vx / n, vy / n
        return self.vx, self.vy

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        amount = _clamp01(p["amount"])
        m = _smoothstep(amount)
        pulse = _clamp01(p["pulse"])
        kick = max(0.0, float(p["kick"]))
        dt = max(0.0, float(dt))
        # host-integrated phases: travel surges on the beat, never jumps
        rate = max(0.0, float(p["travel_speed"])) * (1.0 + 2.0 * kick * pulse * m)
        self.ph1 = math.fmod(self.ph1 + rate * dt, 1.0)
        self.ph2 = math.fmod(self.ph2 + rate * 0.77 * dt, 1.0)
        self.lam_ph = math.fmod(self.lam_ph + 0.09 * dt, 2.0 * math.pi)
        dx, dy = self._ease_direction(p["direction"], dt)
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        lam = float(p["wavelength"]) * (1.0 + 0.12 * math.sin(self.lam_ph))
        lam = max(lam, 0.02)
        lam2 = lam * 0.83
        cross = _clamp01(p["cross_mix"])
        amp = float(p["depth"]) * m * (1.0 + 1.5 * kick * pulse * m)
        # soft limit against folding when the waves get very short (stays ~linear at sane settings)
        amp1 = amp / math.sqrt(1.0 + (amp / (0.35 * lam)) ** 2)
        amp2 = cross * amp / math.sqrt(1.0 + (amp / (0.35 * lam2)) ** 2)
        sheen = _clamp01(p["sheen"]) * m * (1.0 + 0.6 * kick * pulse * m)
        mirror = 1 if str(p.get("edge_mode", "mirror")) == "mirror" else 0
        launch_2d(self._k, W, H,
                  (src, out, np.int32(W), np.int32(H), np.float32(dx), np.float32(dy),
                   np.float32(1.0 / lam), np.float32(1.0 / lam2), np.float32(self.ph1), np.float32(self.ph2),
                   np.float32(amp1 * H), np.float32(amp2 * H), np.float32(_clamp01(p["wave_shape"])),
                   np.float32(sheen), np.int32(mirror)))
