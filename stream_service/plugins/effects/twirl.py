"""Twirl vortex: one to three slowly orbiting whirlpools stir the picture like wet paint; it twists on the beat and
unwinds as the envelope decays.

Source: https://github.com/evanw/glfx.js/blob/master/src/filters/warp/swirl.js
        https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Twirl.fs
Author: Evan Wallace (glfx.js swirl); VIDVOX (ISF Twirl); multi-vortex extension smfx
License: MIT
license_class: permissive

Ported from GLSL by smfx.  The glfx/ISF swirl rotates each pixel around a centre by theta = angle * (1 - r/R)^2
inside radius R.  Changes: up to three vortices applied in sequence to the sample coordinate, the 2nd and 3rd
fading in by weight as the (continuous) vortex_count eases, their layout morphing from one centred whirlpool to a
pair to a triangle; spin_mix morphs the odd vortex from the same spin to the opposite spin (counter-rotating pairs
stir like paint); centres wander on host-integrated Lissajous orbits and each vortex's twist sways slowly so the
picture breathes with no beat; mirrored edges instead of clamped streaks; an optional "paint tint": spiral arms
fixed in source space (cos 2*phi of the rotated coordinate, so they wind with the twist) screen-blended in pal()
colours with an integrated drift.

Realtime: m = smoothstep(amount); angle = m * max_angle * (1 + punch * pulse) (amount 0 is an exact copy, and the
twist decays with the beat envelope so the picture unwinds).  orbit_speed is the integrated wander/sway rate.
One full-res pass, at most 3 vortex evaluations and one bilinear tap.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NV = 3
VF = 8  # floats per vortex: cx, cy (px), R (px), theta (rad, signed), weight, hue, unused, unused
TWO_PI = 2.0 * math.pi

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NV 3
#define VF 8
__device__ __forceinline__ float tw_fold(float v, float n) {         // mirror at the frame edges
    v = fabsf(v);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

extern "C" __global__
void twirl(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
           const float* __restrict__ V, const float* __restrict__ P,
           const float tint, const float period, const float acos_, const float asin_)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float qx = x + 0.5f, qy = y + 0.5f;
    float ta = 0.f, tr = 0.f, tg = 0.f, tb = 0.f;
    #pragma unroll
    for (int i = 0; i < NV; ++i) {
        const float* v = V + i * VF;
        const float th = v[3];
        if (fabsf(th) < 1e-6f) continue;
        const float ex = qx - v[0], ey = qy - v[1];
        const float R = v[2];
        const float d2 = ex * ex + ey * ey;
        if (d2 >= R * R) continue;
        const float k = 1.f - sqrtf(d2) / R;
        const float f = k * k;
        float a = f * th;
        a -= 6.2831853f * rintf(a * 0.15915494f);                      // range-reduce for fast-math sincos
        float s, c;
        __sincosf(a, &s, &c);
        const float rx = ex * c - ey * s, ry = ex * s + ey * c;
        qx = v[0] + rx; qy = v[1] + ry;
        if (tint > 0.f) {
            // spiral arms fixed in source space: cos(2*phi_src + drift) from the rotated vector, no atan
            const float inv = 1.f / fmaxf(d2, 1e-3f);
            const float c2 = (rx * rx - ry * ry) * inv, s2 = 2.f * rx * ry * inv;
            const float arm = 0.5f + 0.5f * (c2 * acos_ - s2 * asin_);
            const float w = tint * v[4] * f * arm * arm;
            if (w > 1e-5f) {
                const float3 col = pal(v[5] + period * (0.45f * k + 0.2f * arm), P);
                tr += col.x * w; tg += col.y * w; tb += col.z * w; ta += w;
            }
        }
    }
    float4 o = bilin(src, tw_fold(qx, (float)W), tw_fold(qy, (float)H), W, H);
    if (ta > 1e-5f) {
        const float A = fminf(ta, 1.f) / ta;                               // screen blend, never clips a bright still
        o.x += (255.f - o.x) * fminf(tr * A, 1.f);
        o.y += (255.f - o.y) * fminf(tg * A, 1.f);
        o.z += (255.f - o.z) * fminf(tb * A, 1.f);
    }
    out[y * W + x] = pack(o);
}
"""

# vortex layouts (x, y in heights from the frame centre, y down) for 1, 2 and 3 vortices
_LAYOUT = np.array([
    [[0.0, 0.0], [0.45, 0.0], [0.0, 0.3]],
    [[-0.42, 0.04], [0.42, -0.04], [0.0, 0.3]],
    [[-0.55, 0.14], [0.55, 0.14], [0.0, -0.22]],
], dtype=np.float64)
_ORBIT_RATES = np.array([[0.37, 0.29], [0.31, 0.43], [0.41, 0.23]], dtype=np.float64)   # Hz at orbit_speed 1


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smooth01(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


class Twirl(Effect):
    name = "twirl"
    stage = "warp"
    label = "Twirl vortex"
    description = "Whirlpools rotate the picture around 1-3 orbiting centres; stirs like wet paint, twists on the beat."
    params = {
        **standard_effect_params(),
        # the beat envelope drives the twist directly: keep it raw (easing would flatten the punch)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "max_angle": num(0.0, 4.0 * math.pi, 5.0, "Max twist", unit="rad", step=0.05, tier="regional", basic=True),
        "radius": num(0.2, 1.5, 0.7, "Vortex radius", unit="× height", step=0.01, tier="regional", basic=True),
        "vortex_count": num(1.0, 3.0, 2.0, "Vortices", step=0.05, tier="regional"),
        "spin_mix": num(0.0, 1.0, 1.0, "Same ↔ alternating spin", tier="regional"),
        "orbit": num(0.0, 0.3, 0.1, "Centre wander", unit="× height", step=0.005, tier="regional"),
        "sway": num(0.0, 1.0, 0.35, "Slow twist sway", tier="regional"),
        "punch": num(0.0, 3.0, 0.9, "Beat twist", step=0.05, tier="regional"),
        "tint": num(0.0, 1.0, 0.2, "Paint tint", tier="regional"),
        "orbit_speed": num(0.0, 2.0, 0.35, "Orbit speed", unit="/s", tier="realtime", bind="rate"),
        **palette_params("candy", 1.0, 0.04),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("twirl")
        self._V_h = np.zeros(NV * VF, dtype=np.float32)
        self._V_d = cp.zeros(NV * VF, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "candy")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        seed = int(params.get("seed", 3) or 3) if isinstance(params, dict) else 3
        rng = np.random.default_rng(seed)
        self._orb = rng.uniform(0.0, TWO_PI, size=(NV, 2))       # integrated orbit phases (x, y per vortex)
        self._sw = float(rng.uniform(0.0, TWO_PI))                # integrated sway phase
        self._arm = float(rng.uniform(0.0, TWO_PI))               # tint arm rotation
        self._drift = float(rng.uniform(0.0, 1.0))
        self._pk = 0.0                                            # attack-smoothed pulse

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dts = min(max(float(dt), 0.0), 0.1)
        # host-integrated phases, wrapped (fast-math sin is poor for large arguments)
        rate = float(p["orbit_speed"])
        self._orb = np.mod(self._orb + TWO_PI * _ORBIT_RATES * rate * dts, TWO_PI)
        self._sw = math.fmod(self._sw + TWO_PI * 0.09 * rate * dts, TWO_PI)
        self._arm = math.fmod(self._arm + TWO_PI * 0.05 * (0.3 + rate) * dts, TWO_PI)
        self._drift += float(p["drift_speed"]) * dts
        self._drift -= math.floor(self._drift)
        # pulse: ~25 ms attack so the twist never snaps, then follows the envelope down (the picture unwinds)
        pulse = _clamp01(p["pulse"])
        if pulse > self._pk and dts > 0:
            self._pk += (pulse - self._pk) * (1.0 - math.exp(-dts / 0.025))
        elif pulse <= self._pk:
            self._pk = pulse
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dts > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        amount = _clamp01(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smooth01(amount)
        W, H = self.ctx.width, self.ctx.height
        n = min(max(float(p["vortex_count"]), 1.0), 3.0)
        w1, w2 = _smooth01(n - 1.0), _smooth01(n - 2.0)
        layout = _LAYOUT[0] + (_LAYOUT[1] - _LAYOUT[0]) * w1
        layout = layout + (_LAYOUT[2] - layout) * w2
        weights = (1.0, w1, w2)
        R = float(p["radius"]) * H / (1.0 + 0.2 * w1 + 0.15 * w2)
        orbit = float(p["orbit"])
        sway = _clamp01(p["sway"])
        mix = _clamp01(p["spin_mix"])
        base = m * float(p["max_angle"]) * (1.0 + float(p["punch"]) * self._pk)
        V = self._V_h
        for i in range(NV):
            o = i * VF
            ox = orbit * math.sin(self._orb[i, 0])
            oy = 0.8 * orbit * math.sin(self._orb[i, 1])
            V[o + 0] = 0.5 * W + (layout[i, 0] + ox) * H
            V[o + 1] = 0.5 * H + (layout[i, 1] + oy) * H
            V[o + 2] = R
            sign = 1.0 - 2.0 * mix if i % 2 else 1.0
            V[o + 3] = base * weights[i] * sign * (1.0 + 0.45 * sway * math.sin(self._sw + 2.09 * i))
            V[o + 4] = weights[i]
            V[o + 5] = (self._drift + 0.29 * i) % 1.0
        self._V_d.set(V)
        tint = _clamp01(p["tint"]) * 0.6 * m * (1.0 + 0.5 * float(p["punch"]) * self._pk)
        launch_2d(self._k, W, H,
                  (src, out, np.int32(W), np.int32(H), self._V_d, self._P_d,
                   np.float32(tint), np.float32(p["palette_period"]),
                   np.float32(math.cos(self._arm)), np.float32(math.sin(self._arm))))

    def close(self) -> None:
        self._V_d = None
        self._P_d = None
