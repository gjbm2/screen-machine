"""Fisheye lens: an optical radial lens that runs from pincushion through barrel to a full fisheye / peephole.

Source: https://godotshaders.com/shader/2d-radial-distortion-fisheye-barrel/
Author: jamesfrize (godotshaders, based on a gist by aggregate1166877)
License: CC0-1.0
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the shader's angular remap z = sqrt(k - k d^2), r = atan(d, z)/pi is
recast as a continuous signed lens family -- tan(theta*rho)/theta (barrel -> fisheye, k > 0) and
sin(theta*rho)/theta (pincushion, k < 0), identical at k = 0 -- so strength morphs smoothly through zero; adds an
exact host-side auto-zoom (autofit 0 = whole picture inside the frame, the circular fisheye / peephole look;
1 = fill, corners never go black) that stays exact when the lens centre wanders, a lens rim vignette, a 3-tap
lateral colour fringe and a palette-tinted coating glow on the rim.)

How it works: per output pixel, rho = distance from the (wandering) lens centre / distance to the farthest corner.
The source radius is mix(rho, sc * f_k(rho), m) along the same ray, where m = smoothstep(amount), so the effect is
the identity at amount 0 and every output sample stays on the segment between the centre and the frame edge
whenever autofit = 1 (convexity), i.e. no black corners.  sc is solved on the host each frame from the two binding
constraints (nearest edge, farthest corner) and log-interpolated by autofit.  Samples that land outside the source
(autofit < 1) fade to black over a short feather and pick up a soft halo in the rim palette colour.

Realtime: amount -> radius mix, vignette, fringe, rim glow (all x m).  pulse -> adds `punch` to k (a decaying
"lens punch" zoom into the bulge) and briefly widens the fringe and lifts the rim glow (all x m).  drift_speed and
wander_speed are host-integrated rates.  One full-res kernel, three bilinear taps (~0.8 ms at 4K).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

_SRC = HELPERS + PALETTE_CUDA + r"""
// Signed lens family: th > 0 barrel/fisheye (tan, convex), th < 0 pincushion (sin, concave), th = 0 identity.
__device__ __forceinline__ float lf_shape(float rho, float th) {
    if (th > 1e-5f) return tanf(th * rho) / th;
    if (th < -1e-5f) { const float a = -th; return sinf(a * rho) / a; }
    return rho;
}

extern "C" __global__
void lens_fisheye(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                  const float* __restrict__ P,
                  const float cx, const float cy, const float invR, const float th, const float sc, const float m,
                  const float fringe, const float vig, const float tint, const float period, const float drift,
                  const float feather, const float halo_px)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float dx = px - cx, dy = py - cy;
    const float dpx = sqrtf(dx * dx + dy * dy);
    const float rho = dpx * invR;

    // radius ratio g = r'/rho, r' = mix(rho, sc * f(rho), m); limit at rho -> 0 is 1 + m (sc - 1)
    float g;
    if (rho > 1e-5f) {
        const float rl = rho + (sc * lf_shape(rho, th) - rho) * m;
        g = rl / rho;
    } else {
        g = 1.f + m * (sc - 1.f);
    }
    const float sx = cx + dx * g, sy = cy + dy * g;

    // lateral colour fringe: red pushed out, blue pulled in, growing with the field radius
    const float fr = fringe * rho;
    float4 c;
    if (fr > 1e-6f) {
        const float gr = g * (1.f + fr), gb = g * (1.f - fr);
        const float r = bilin(src, cx + dx * gr, cy + dy * gr, W, H).x;
        const float4 gg = bilin(src, sx, sy, W, H);
        const float b = bilin(src, cx + dx * gb, cy + dy * gb, W, H).z;
        c = make_float4(r, gg.y, b, 255.f);
    } else {
        c = bilin(src, sx, sy, W, H);
    }

    // outside-the-source mask (only reachable when autofit < 1): 1 inside, fades to 0 over `feather` px
    const float din = fminf(fminf(sx, (float)W - sx), fminf(sy, (float)H - sy));
    const float mask = clamp01(1.f + din / feather);

    // lens rim vignette (natural fall-off toward the corners)
    const float sv = smooth(0.28f, 1.02f, rho);
    const float v = 1.f - vig * m * 0.88f * sv * sv;
    const float k = v * mask;
    c = make_float4(c.x * k, c.y * k, c.z * k, 255.f);

    // coating glow: palette-tinted light where the rim darkens and a soft halo just outside the picture edge
    if (tint > 1e-4f) {
        const float dout = fmaxf(-din, 0.f);
        const float halo = (1.f - mask) * __expf(-dout / halo_px);
        const float w = clamp01(tint * (0.55f * (1.f - v) + 0.95f * halo));
        if (w > 1e-4f) {
            const float inv = 1.f / (dpx + 1e-3f);
            const float idx = period * (0.55f * rho + 0.18f * dx * inv - 0.08f * dy * inv) + drift;
            const float3 pc = pal(idx, P);
            const float gl = 0.35f * 255.f * w;           // glow capped at ~35 % brightness
            const float gr = pc.x * gl, gg = pc.y * gl, gb = pc.z * gl;
            c.x = 255.f - (255.f - c.x) * (255.f - gr) * (1.f / 255.f);
            c.y = 255.f - (255.f - c.y) * (255.f - gg) * (1.f / 255.f);
            c.z = 255.f - (255.f - c.z) * (255.f - gb) * (1.f / 255.f);
        }
    }
    out[y * W + x] = pack(c);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smoothstep(v: float) -> float:
    t = _clamp01(v)
    return t * t * (3.0 - 2.0 * t)


def _theta(k: float) -> float:
    """Strength k (-1 pincushion .. +1 barrel .. +2 fisheye, pulse may overshoot) -> signed lens angle."""
    if k >= 0.0:
        tl = 0.72 * k
        return tl / (1.0 + (tl / 1.52) ** 6) ** (1.0 / 6.0)      # soft cap below pi/2 (tan stays finite)
    tl = -1.0 * k
    return -tl / (1.0 + (tl / 1.40) ** 6) ** (1.0 / 6.0)        # cos(theta) stays > 0 (monotonic)


def _shape(rho: float, th: float) -> float:
    if th > 1e-5:
        return math.tan(th * rho) / th
    if th < -1e-5:
        return math.sin(-th * rho) / (-th)
    return rho


# palette drives only the rim glow here, so it is not a headline (basic) knob
_PAL_PARAMS = {**palette_params("dusk", 1.0, 0.03),
               "palette": enum(list(PALETTES), "dusk", "Rim glow palette", tier="regional")}


class LensFisheye(Effect):
    name = "lens_fisheye"
    stage = "warp"
    label = "Fisheye lens"
    description = ("Optical lens from pincushion through barrel to full fisheye or peephole; auto-zooms so corners "
                   "never go black, rim vignette, colour fringe; punches into the bulge on the beat.")
    params = {
        **standard_effect_params(),
        "strength": num(-1.0, 2.0, 1.4, "Pincushion ↔ barrel ↔ fisheye", step=0.01, tier="regional", basic=True),
        "autofit": num(0.0, 1.0, 1.0, "Fit whole picture ↔ fill frame", tier="regional"),
        "vignette": num(0.0, 1.0, 0.45, "Rim vignette", tier="regional"),
        "fringe": num(0.0, 0.01, 0.004, "Colour fringe", step=0.0005, tier="regional"),
        "centre_drift": num(0.0, 0.2, 0.05, "Lens centre wander", unit="× height", step=0.005, tier="regional"),
        "punch": num(0.0, 1.5, 0.6, "Beat lens punch", step=0.05, tier="regional"),
        "rim_glow": num(0.0, 1.0, 0.3, "Rim coating glow", tier="regional"),
        "wander_speed": num(0.0, 2.0, 0.15, "Wander speed", unit="/s", tier="realtime", bind="rate"),
        **_PAL_PARAMS,
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("lens_fisheye")
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        self._drift = 0.0
        self._wander = 0.0
        rng = np.random.default_rng(7)
        self._wph = [float(v) for v in rng.uniform(0.0, 2.0 * math.pi, 4)]

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(0.0, float(dt)), 0.5)
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0
        self._wander += max(0.0, float(p["wander_speed"])) * dt          # host double: no wrap needed
        amount = _clamp01(p["amount"])
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = _smoothstep(amount)
        pulse = _clamp01(p["pulse"])
        W, H = self.ctx.width, self.ctx.height

        # lens centre: smooth integrated wander (sum of incommensurate sines), kept well inside the frame
        w, ph = self._wander, self._wph
        ox = 0.62 * math.sin(1.00 * w + ph[0]) + 0.38 * math.sin(1.73 * w + ph[1])
        oy = 0.62 * math.sin(0.83 * w + ph[2]) + 0.38 * math.sin(2.11 * w + ph[3])
        dr = float(p["centre_drift"]) * H
        cx = min(max(0.5 * W + dr * ox, 0.15 * W), 0.85 * W)
        cy = min(max(0.5 * H + dr * oy, 0.15 * H), 0.85 * H)

        # lens: strength + decaying beat punch -> signed angle
        k = float(p["strength"]) + float(p["punch"]) * pulse * m
        th = _theta(k)

        # exact auto-zoom: rho = 1 at the farthest corner; nearest edge at rho_min
        R = max(math.hypot(cx, cy), math.hypot(W - cx, cy), math.hypot(cx, H - cy), math.hypot(W - cx, H - cy))
        rho_min = max(min(cx, W - cx, cy, H - cy) / R, 1e-3)
        q1 = rho_min / max(_shape(rho_min, th), 1e-6)
        q2 = 1.0 / max(_shape(1.0, th), 1e-6)
        sc_fill, sc_in = min(q1, q2), max(q1, q2)
        af = _clamp01(p["autofit"])
        sc = math.exp(math.log(sc_in) + af * (math.log(sc_fill) - math.log(sc_in)))

        fringe = float(p["fringe"]) * m * (1.0 + 2.5 * pulse * m)
        vig = _clamp01(p["vignette"])
        tint = _clamp01(p["rim_glow"]) * m * (1.0 + 0.5 * pulse * m)

        if self._pal_name != p["palette"]:
            self._pal_name = p["palette"]
            self._P.set(np.asarray(palette_coeffs(p["palette"]), np.float32))

        launch_2d(self._k, W, H,
                  (src, out, np.int32(W), np.int32(H), self._P,
                   np.float32(cx), np.float32(cy), np.float32(1.0 / R), np.float32(th), np.float32(sc),
                   np.float32(m), np.float32(fringe), np.float32(vig), np.float32(tint),
                   np.float32(p["palette_period"]), np.float32(self._drift),
                   np.float32(max(0.006 * H, 1.0)), np.float32(0.05 * H)))
