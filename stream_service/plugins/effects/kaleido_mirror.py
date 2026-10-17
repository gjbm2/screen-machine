"""Kaleido mirror: folds any input into an N-segment mirrored mandala that rotates and breathes.

Source: https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/KaleidoShader.js
        https://github.com/mrdoob/three.js/blob/dev/examples/jsm/shaders/MirrorShader.js
        https://github.com/Vidvox/ISF-Files/blob/master/ISF/Kaleidoscope.fs (inspiration)
Author: three.js authors (KaleidoShader after Toby Schachman; MirrorShader); VIDVOX (ISF Kaleidoscope)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: polar fold as in KaleidoShader, "sides" = segments/2 so 2 segments is the
plain MirrorShader mirror; continuous segment count by crossfading the floor/ceil folds; mirror-wrapped sampling
instead of clamped edges; amount grows a feathered radial fold mask out from the centre with a palette-coloured
ripple at its edge; palette-coloured glow along the mirror seams; beat kicks the integrated rotation rate and the
zoom breath.)

amount 0 is the untouched input (copied).  As amount rises a soft disc of folded image grows from the centre until
it covers the frame.  segments (2-12) counts mirrored wedges: 2 = a plain mirror, 12 = a 6-fold mandala; values
between neighbouring even counts crossfade the two folds.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NP = 32
# P layout (floats)
#  0..11 palette | 12 cx px | 13 cy px | 14 rot (rad) | 15 inv zoom | 16 mirror angle (rad) | 17 sides0 | 18 sides1
#  19 crossfade | 20 mask radius (H units) | 21 feather | 22 seam glow | 23 drift | 24 period | 25 glow width
#  26 ring amp
_SRC = HELPERS + PALETTE_CUDA + r"""
#define KM_PI 3.14159265f

// reflect a pixel coordinate into [0, n] (mirror-repeat edges: seamless, unlike clamping)
__device__ __forceinline__ float km_reflect(float x, float n) {
    const float p = 2.f * n;
    x = x - p * floorf(x / p);
    return x < n ? x : p - x;
}

// One polar fold (KaleidoShader): `sides` mirrored pairs.  Returns the folded sample and the seam distance (H units).
__device__ __forceinline__ float4 km_fold(const uchar4* __restrict__ src, int W, int H, float r, float ang,
                                          float sides, const float* __restrict__ P, float& seam, float& fa) {
    const float w = KM_PI / sides;                     // half-wedge
    float a = ang + P[14];
    const float pd = 2.f * w;
    a = a - pd * floorf(a / pd);
    a = fabsf(a - w);                                  // [0, w]: mirror lines at 0 and w
    seam = r * __sinf(fminf(fminf(a, w - a), 1.5f));
    fa = a / w;
    const float as = a + P[16];
    const float rr = r * P[15] * (float)H;
    float sn, cs;
    __sincosf(as, &sn, &cs);
    const float sx = km_reflect(P[12] + rr * cs, (float)W);
    const float sy = km_reflect(P[13] + rr * sn, (float)H);
    return bilin(src, sx, sy, W, H);
}

extern "C" __global__
void kaleido_mirror(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                    const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float invH = 1.f / (float)H;
    const float dx = (x + 0.5f - P[12]) * invH, dy = (y + 0.5f - P[13]) * invH;
    const float r = sqrtf(dx * dx + dy * dy);
    const float R = P[20], fe = P[21];
    const float mask = 1.f - smooth(R - fe, R, r);
    const float ring = P[26] * __expf(-((r - (R - 0.5f * fe)) * (r - (R - 0.5f * fe))) / (0.0004f));
    const uchar4 s0 = src[y * W + x];
    float4 c = make_float4(s0.x, s0.y, s0.z, 255.f);
    if (mask <= 0.f && ring < 0.002f) { out[y * W + x] = s0; return; }

    const float ang = atan2f(dy, dx);
    float seamA, faA;
    float4 f = km_fold(src, W, H, r, ang, P[17], P, seamA, faA);
    const float xf = P[19];
    if (xf > 0.001f) {
        float seamB, faB;
        const float4 g = km_fold(src, W, H, r, ang, P[18], P, seamB, faB);
        f = mix4(f, g, xf);
        seamA = seamA + (seamB - seamA) * xf;
        faA = faA + (faB - faA) * xf;
    }
    // glow along the mirror seams: a 2-4 px core plus a soft halo, faded out where the seams converge at the centre
    const float gw = P[25];
    const float q = seamA / gw;
    float glow = (__expf(-q * q) + 0.3f * __expf(-q * q * 0.04f)) * P[22] * smooth(0.f, 0.12f, r);
    const float3 gc = pal(r * P[24] - P[23], P);
    f.x += glow * gc.x * 255.f; f.y += glow * gc.y * 255.f; f.z += glow * gc.z * 255.f;
    c = mix4(c, f, mask);
    // the ripple at the edge of the growing fold disc
    if (ring > 0.002f) {
        const float3 rc = pal(faA * 0.5f * P[24] + P[23], P);
        c.x += ring * rc.x * 255.f; c.y += ring * rc.y * 255.f; c.z += ring * rc.z * 255.f;
    }
    out[y * W + x] = pack(c);
}
"""


def _smooth01(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


class KaleidoMirror(Effect):
    name = "kaleido_mirror"
    stage = "stylise"
    label = "Kaleido mirror"
    description = "Folds the picture into a rotating, breathing mirrored mandala (2 segments = a plain mirror)."
    params = {
        **standard_effect_params(),
        "segments": num(2.0, 12.0, 8.0, "Segments", step=0.1, tier="regional", basic=True),
        "center_x": num(0.0, 1.0, 0.5, "Centre X", tier="regional"),
        "center_y": num(0.0, 1.0, 0.5, "Centre Y", tier="regional"),
        "zoom": num(0.5, 2.0, 1.0, "Zoom", step=0.01, unit="×", tier="regional"),
        "mirror_angle_deg": num(0.0, 180.0, 30.0, "Mirror angle", step=1.0, unit="°", tier="regional"),
        "rotate_speed": paced(num(-60.0, 60.0, 8.0, "Rotate speed", step=0.5, unit="°/s", tier="regional", bind="rate"), 360),
        "rotate_kick": num(0.0, 240.0, 70.0, "Beat rotation kick", step=1.0, unit="°/s", tier="regional"),
        "zoom_kick": num(0.0, 0.3, 0.08, "Beat zoom breath", step=0.005, tier="regional"),
        "seam_glow": num(0.0, 1.0, 0.3, "Seam glow", tier="regional"),
        **palette_params("neon", period=1.6, drift=0.06),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("kaleido_mirror")
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.rot = 0.0
        self.breath = 0.0
        self.drift = 0.0
        self.kick = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        W, H = self.ctx.width, self.ctx.height
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        # rotation: integrated rate, the beat lurches it forward in the spin direction and it eases back out
        rs = float(p["rotate_speed"])
        sgn = 1.0 if rs >= 0.0 else -1.0
        rate = math.radians(rs + sgn * float(p["rotate_kick"]) * pulse)
        self.rot = math.fmod(self.rot + rate * dt, 2.0 * math.pi)
        self.breath = math.fmod(self.breath + 2.0 * math.pi * 0.12 * dt, 2.0 * math.pi)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 1000.0)
        # beat envelope for the breath amplitude: max-combine with a short release (never sums)
        self.kick = max(pulse, self.kick * math.pow(0.86, dt * 60.0))
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        eff = min(max(amount * (1.0 + 0.25 * pulse), 0.0), 1.0)
        cx, cy = float(p["center_x"]) * W, float(p["center_y"]) * H
        rmax = max(math.hypot(cx - ex, cy - ey) for ex in (0.0, W) for ey in (0.0, H)) / H
        feather = 0.25
        # segments -> mirrored pairs ("sides"); crossfade the floor and ceil folds (eased in the middle)
        sides = min(max(float(p["segments"]), 2.0), 12.0) * 0.5
        s0 = math.floor(sides)
        xf = _smooth01((sides - s0 - 0.2) / 0.6)
        if s0 >= 6:
            s0, xf = 6, 0.0
        zoom = float(p["zoom"]) * (1.0 + (0.025 + float(p["zoom_kick"]) * self.kick) * math.sin(self.breath)
                                   + 0.5 * float(p["zoom_kick"]) * self.kick)
        glow = float(p["seam_glow"]) * (1.0 + 0.35 * pulse)
        ring = float(p["seam_glow"]) * 0.9 * _smooth01(amount / 0.08) * (1.0 - _smooth01((amount - 0.7) / 0.3))
        h = self.host
        h[0:12] = palette_coeffs(p["palette"])
        h[12] = cx
        h[13] = cy
        h[14] = self.rot
        h[15] = 1.0 / max(zoom, 0.05)
        h[16] = math.radians(float(p["mirror_angle_deg"]))
        h[17] = s0
        h[18] = s0 + 1
        h[19] = xf
        h[20] = eff * (rmax + feather)
        h[21] = feather
        h[22] = glow
        h[23] = self.drift
        h[24] = float(p["palette_period"])
        h[25] = max(2.2 / H, 0.0011)
        h[26] = ring
        self.P.set(h)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self.P))
