"""Bulge / Pinch: a soft glass lens swells the picture outward (bulge) or sucks it in (pinch); lens_count fades in
a drifting lattice of up to 12 lenses for a bubble-wrap look, and the beat is a speaker-cone thump.

Source: https://github.com/evanw/glfx.js/blob/master/src/filters/warp/bulgepinch.js
        https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Bump%20Distortion.fs
Author: Evan Wallace (glfx.js bulgePinch); carter rosenberg (ISF Bump Distortion)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: glfx's radial remap kept exactly, bulge = mix(1, smoothstep((d/r)^2),
0.75 s), pinch = mix(1, (d/r)^(0.75 s), 1 - d/r), both identity at strength 0; up to 12 lenses whose displacements
add, faded in by continuous weight (not count) on a hex-offset 4x3 lattice ordered centre-out, so lens 0 glides
from the frame centre to its lattice slot as the count rises; per-lens Lissajous wander, a slow lattice sway and a
gentle per-lens breathing, all from host-integrated phases; aspect-correct (round) or frame-fitted (elliptical)
lenses; optional prism fringes and a palette-tinted glass sheen (a specular spot that sits upper-left on a dome and
lower-right in a dimple, plus a rim glint), screen-blended so bright stills do not clip; mirrored edge taps).

Realtime: m = smoothstep(amount); strength = clamp(m*bias + m*punch*cone, -1, 1), where cone is an under-damped
spring (about 4.5 Hz, zeta 0.4) driven by the beat envelope: it kicks out on the beat, overshoots slightly and
rebounds a touch into pinch, like a speaker cone.  amount 0 is an exact copy of the input.  One full-res pass,
lens loop bounded at 12 with a radius early-out; pixels outside every lens are a plain copy.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NL = 12   # max lenses
LF = 8    # floats per lens: cx, cy (px), R (px), strength, weight, hue, unused, unused

_SRC = HELPERS + PALETTE_CUDA + r"""
#define LF 8
__device__ __forceinline__ float bp_fold(float v, float n) {      // mirror at the frame edges
    v = fabsf(v);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

extern "C" __global__
void bulge_pinch(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                 const float* __restrict__ lens, const int n, const float ax,
                 const float sheen, const float fringe, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    float ox = 0.f, oy = 0.f;
    float lr = 0.f, lg = 0.f, lb = 0.f;
    bool hit = false;
    for (int i = 0; i < n && i < 12; ++i) {
        const float* q = lens + i * LF;
        const float R = q[2];
        const float dx = px - q[0], dy = py - q[1];
        const float lx = dx * ax;
        const float d2 = lx * lx + dy * dy;
        if (d2 >= R * R || R < 1.f) continue;
        const float s = q[3], w = q[4];
        if (w <= 0.f) continue;
        hit = true;
        const float p = sqrtf(d2) / R;
        float f;
        if (s >= 0.f) {                                   // glfx bulge
            const float t = p * p;
            const float sm = t * t * (3.f - 2.f * t);
            f = 1.f + (sm - 1.f) * 0.75f * s;
        } else {                                          // glfx pinch
            const float pp = fmaxf(p, 1e-4f);
            f = 1.f + (__powf(pp, 0.75f * s) - 1.f) * (1.f - p);
        }
        ox += w * dx * (f - 1.f);
        oy += w * dy * (f - 1.f);
        if (sheen > 1e-4f) {
            const float u = lx / R, v = dy / R;
            const float sg = s / (fabsf(s) + 0.08f);       // dome: light upper-left; dimple: lower-right
            const float hx = u + 0.34f * sg, hy = v + 0.40f * sg;
            const float spot = __expf(-(hx * hx + hy * hy) * 16.f);
            const float rr = clamp01((p - 0.70f) * (1.f / 0.30f));
            const float rs = __sinf(3.14159265f * rr);
            const float rim = rs * rs;
            const float a = fabsf(s) * w * sheen;
            const float3 c1 = pal(q[5], P);
            const float3 c2 = pal(q[5] + 0.33f + 0.5f * p, P);
            const float ks = 0.85f * spot * a, kr = 0.5f * rim * a;
            lr += ks * (0.7f + 0.3f * c1.x) + kr * c2.x;
            lg += ks * (0.7f + 0.3f * c1.y) + kr * c2.y;
            lb += ks * (0.7f + 0.3f * c1.z) + kr * c2.z;
        }
    }
    const int idx = y * W + x;
    if (!hit) { out[idx] = src[idx]; return; }
    const float fW = (float)W, fH = (float)H;
    float4 c;
    if (fringe > 1e-4f) {
        const float kr = 1.f + fringe, kb = 1.f - fringe;
        c.x = bilin(src, bp_fold(px + ox * kr, fW), bp_fold(py + oy * kr, fH), W, H).x;
        c.y = bilin(src, bp_fold(px + ox, fW), bp_fold(py + oy, fH), W, H).y;
        c.z = bilin(src, bp_fold(px + ox * kb, fW), bp_fold(py + oy * kb, fH), W, H).z;
        c.w = 255.f;
    } else {
        c = bilin(src, bp_fold(px + ox, fW), bp_fold(py + oy, fH), W, H);
    }
    // screen blend the glass light
    lr = clamp01(lr); lg = clamp01(lg); lb = clamp01(lb);
    c.x = 255.f - (255.f - c.x) * (1.f - lr);
    c.y = 255.f - (255.f - c.y) * (1.f - lg);
    c.z = 255.f - (255.f - c.z) * (1.f - lb);
    out[idx] = pack(c);
}
"""


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(float(v), lo), hi)


def _smooth01(t: float) -> float:
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class BulgePinch(Effect):
    name = "bulge_pinch"
    stage = "warp"
    label = "Bulge / Pinch"
    description = ("A soft glass lens swells the centre outward (bulge) or sucks it in (pinch); lens count fades in "
                   "a drifting bubble-wrap lattice of up to 12 lenses. The beat is a speaker-cone thump.")
    params = {
        **standard_effect_params(),
        # the beat drives a spring on the host: keep the envelope raw (easing would flatten the thump)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "bias": num(-1.0, 1.0, 0.6, "Pinch ↔ bulge", step=0.01, tier="regional", basic=True),
        "radius": num(0.2, 1.2, 0.75, "Lens radius", unit="× half-height", step=0.01, tier="regional"),
        "lens_count": num(1.0, 12.0, 1.0, "Lens count", step=0.05, tier="regional", basic=True),
        "wander": num(0.0, 2.0, 0.35, "Lens drift rate", unit="/s", step=0.01, tier="regional"),
        "aspect_correct": num(0.0, 1.0, 1.0, "Frame-fitted ↔ round", tier="regional"),
        "punch": num(0.0, 1.5, 0.7, "Beat thump", step=0.01, tier="regional"),
        "sheen": num(0.0, 1.0, 0.3, "Glass sheen", tier="regional"),
        "fringe": num(0.0, 1.0, 0.2, "Prism fringes", tier="regional"),
        **palette_params("neon", 1.0, 0.04),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("bulge_pinch")
        W, H = ctx.width, ctx.height
        self._lens_d = cp.zeros(NL * LF, dtype=cp.float32)
        self._lens_h = np.zeros(NL * LF, dtype=np.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        seed = int(params.get("seed", 7) or 7) if isinstance(params, dict) else 7
        rng = np.random.default_rng(seed)
        # hex-offset 4x3 lattice (uv), ordered centre-out so lenses fade in from the middle
        A = W / max(H, 1)
        slots = []
        for r in range(3):
            for c in range(4):
                sx = (c + 0.5 + (0.125 if r == 1 else -0.125)) / 4.0
                sy = (r + 0.5) / 3.0
                sx += float(rng.uniform(-0.02, 0.02))
                sy += float(rng.uniform(-0.02, 0.02))
                slots.append((sx, sy))
        slots.sort(key=lambda s: math.hypot((s[0] - 0.5) * A, s[1] - 0.5) + 1e-3 * rng.random())
        self._home = np.array(slots, dtype=np.float64)                  # (12, 2) uv
        self._var = rng.uniform(-1.0, 1.0, NL)                           # per-lens strength variety
        self._hue = (np.arange(NL) * 0.381966 + rng.random()) % 1.0
        self._rates = rng.uniform(0.55, 1.25, (NL, 3)) * np.where(rng.random((NL, 3)) < 0.5, -1.0, 1.0)
        self._ph = rng.uniform(0.0, 2.0 * math.pi, (NL, 3))              # wobble x, wobble y, breathing
        self._gph = rng.uniform(0.0, 2.0 * math.pi, 2)                   # whole-lattice sway
        self._drift = 0.0
        self._x = 0.0                                                    # speaker-cone spring
        self._v = 0.0

    def _cone(self, pulse: float, dt: float) -> None:
        if dt > 0.5:
            self._x, self._v = 0.0, 0.0
            return
        dt = min(max(dt, 0.0), 0.1)
        if dt <= 0.0:
            return
        w = 2.0 * math.pi * 4.5
        z = 0.4
        nsub = max(1, int(math.ceil(dt * 120.0)))
        h = dt / nsub
        for _ in range(nsub):
            self._v += (w * w * (pulse - self._x) - 2.0 * z * w * self._v) * h
            self._x += self._v * h
        self._x = _clamp(self._x, -0.5, 1.5)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        pulse = _clamp(p["pulse"], 0.0, 1.0)
        self._cone(pulse, dt)
        dts = min(max(dt, 0.0), 0.1)
        self._drift = (self._drift + float(p["drift_speed"]) * dts) % 1.0
        wander = max(float(p["wander"]), 0.0)
        self._ph = np.mod(self._ph + self._rates * wander * dts, 2.0 * math.pi)
        self._gph = np.mod(self._gph + np.array([0.23, 0.17]) * wander * dts, 2.0 * math.pi)
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            k = 1.0 - math.exp(-dt / 1.0)
            self._pal += (tgt - self._pal) * k
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        amount = _clamp(p["amount"], 0.0, 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        m = _smooth01(amount)
        s = _clamp(m * float(p["bias"]) + m * float(p["punch"]) * self._x, -1.0, 1.0)

        cnt = _clamp(p["lens_count"], 1.0, 12.0)
        n = min(NL, int(math.ceil(cnt - 1e-4)))
        lat = _smooth01(cnt - 1.0)                       # 0 = one centred lens, 1 = lattice layout
        ac = _clamp(p["aspect_correct"], 0.0, 1.0)
        ax = (H / W) + (1.0 - H / W) * ac                # distance metric: 1 = round lenses in pixels
        R = float(p["radius"]) * 0.5 * H / math.sqrt(1.0 + 0.3 * (cnt - 1.0))
        R *= 1.0 + 0.08 * m * float(p["punch"]) * max(self._x, 0.0)   # the cone swells a touch on the kick
        sway_x = 0.04 * lat * math.cos(self._gph[0])
        sway_y = 0.03 * lat * math.sin(self._gph[1])
        per = float(p["palette_period"])
        L = self._lens_h
        L[:] = 0.0
        for i in range(n):
            hx, hy = self._home[i]
            if i == 0:
                hx = 0.5 + (hx - 0.5) * lat
                hy = 0.5 + (hy - 0.5) * lat
            amp = 0.025 + 0.035 * lat
            cx = hx + sway_x + amp * math.cos(self._ph[i, 0]) * (H / W)
            cy = hy + sway_y + amp * math.sin(self._ph[i, 1])
            var = lat * (0.15 * self._var[i] + 0.12 * math.sin(self._ph[i, 2]))
            si = _clamp(s * (1.0 + var), -1.0, 1.0)
            wi = _smooth01(cnt - i)
            o = i * LF
            L[o + 0] = cx * W
            L[o + 1] = cy * H
            L[o + 2] = R
            L[o + 3] = si
            L[o + 4] = wi
            L[o + 5] = (self._hue[i] * per + self._drift) % 1.0
        self._lens_d.set(L)
        sheen = _clamp(p["sheen"], 0.0, 1.0)
        fringe = 0.12 * _clamp(p["fringe"], 0.0, 1.0)
        launch_2d(self._k, W, H,
                  (src, out, np.int32(W), np.int32(H), self._lens_d, np.int32(n), np.float32(ax),
                   np.float32(sheen), np.float32(fringe), self._P_d))
