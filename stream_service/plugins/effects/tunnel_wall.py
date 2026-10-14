"""Picture tunnel: the frame tiles the inside of a round or squarish tube that you fly through, fogging to a
glowing vanishing point, with slow banking turns.  Any scene or art still becomes a journey.

Source: Original (inspired by https://iquilezles.org/articles/deform/)
Author: smfx (original classic demoscene tunnel; iq 'deform' article as an idea only)
License: MIT (original)
license_class: permissive

amount 0 is the untouched frame (copied).  The flat camera looks down a tube whose superellipse cross-section just
contains the frame, and whose END CAP is the frame itself at the flat distance, so at m = 0 the cap fills the
screen exactly.  As m = smoothstep(amount) rises the cap recedes (exponential dolly, so the shrink reads evenly)
and the walls appear from the corners inwards: mirrored tiles of the live frame, u around the tube (repeats,
continuous: the angular seam is a mirror axis, so any real repeat count is seamless), v along it.  The walls
stream past (host-integrated flight phase; the beat kicks the flight speed), carry palette-coloured ring lights
at the tile seams, are lit by a headlight (grazing walls darken) and fog by depth into palette colour that glows
white-hot at the vanishing point; the cap ends up a small lit "exit" deep in that glow.  Banking turns: the tube
centre drifts sideways with depth (a saturating bend, solved per pixel by a contracting fixed point: 5 closed-form
superellipse evaluations), the camera rolls into the turn and looks part-way round it.  Pulse adds to the flight
rate, flashes the walls (screen-style, bright stills never clip), the rings and the glow.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, flat_distance, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
TWO_PI = 2.0 * math.pi
NP = 48
# P layout (floats)
#  0 D (flat distance)  1 m  2,3 cos/sin roll  4,5 lens shift (q units)  6,7 bend B (q units)  8 bend s0
#  9,10 tube half axes a, b  11 n  12 1/n  13 repeats around  14 tile length L  15 flight phase (tiles, mod 2)
#  16 rotation phase (rad)  17 twist (rad / tile)  18 cap depth  19 fog density  20 glow  21 light k
#  22 wall flash  23 rings  24 palette drift  25 palette period  26 lod bias  27 far depth  28..39 palette
P_PAL = 28

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_PAL 28
// superellipse "radius" of (ex, ey) for half axes 1/ia, 1/ib and exponent n: 1 on the wall.  Normalised by the
// larger component first, so a big n never overflows (one pow for the smaller term, one for the root).
__device__ __forceinline__ float tw_norm(float ex, float ey, float ia, float ib, float n, float inv_n) {
    const float x = fabsf(ex) * ia, y = fabsf(ey) * ib;
    const float mx = fmaxf(fmaxf(x, y), 1e-20f);
    const float lo = fmaxf(fminf(x, y) / mx, 1e-12f);
    return mx * __powf(1.f + __powf(lo, n), inv_n);
}
__device__ __forceinline__ float3 tw_screen(float3 c, float3 add) {
    return f3(1.f) - (f3(1.f) - c) * (f3(1.f) - saturate(add));
}

SDF_KERNEL
void tunnel_wall(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float D = P[0], m = P[1];
    const float A = (float)W / (float)H;
    const float pa = 2.f / (D * (float)h);                     // ray slope per pixel (= pixel angle on axis)
    const float qx = (x + 0.5f - 0.5f * w) * pa, qy = (0.5f * h - (y + 0.5f)) * pa;
    // bank roll + look into the turn (lens shift): both 0 at m = 0
    const float2 q = f2(P[2] * qx - P[3] * qy + P[4], P[3] * qx + P[2] * qy + P[5]);
    const float2 B = f2(P[6], P[7]);
    const float s0 = P[8], ia = 1.f / P[9], ib = 1.f / P[10], n = P[11], inv_n = P[12];
    const float sfar = P[27], inv_far = 1.f / sfar;

    // wall depth s along the tube: || (q - B g(s)) s ||_SE = 1 with the saturating bend g(s) = s / (s + s0)
    float s = 1.f / fmaxf(tw_norm(q.x, q.y, ia, ib, n, inv_n), inv_far);
#pragma unroll
    for (int i = 0; i < 5; ++i) {
        const float g = s / (s + s0);
        s = 1.f / fmaxf(tw_norm(q.x - B.x * g, q.y - B.y * g, ia, ib, n, inv_n), inv_far);
    }
    const float g = s / (s + s0);
    const float2 e = f2(q.x - B.x * g, q.y - B.y * g);

    const float rl = sqrtf(1.f + q.x * q.x + q.y * q.y), cosa = 1.f / rl;
    const float scap = P[18];
    const float dsp = fmaxf(s * s * pa * ib, 1e-5f);           // depth change per pixel on the wall near the cap
    const float wc = saturate(0.5f + (s - scap) / dsp);       // 1: the cap is in front of the wall
    const float fd = P[19], drift = P[24], per = P[25];
    const float* pal12 = P + P_PAL;

    float3 col = f3(0.f);
    float fv = 0.f;
    if (wc < 1.f) {
        // ---- wall: hit point on the unit superellipse, angle around (seam = mirror axis), depth along
        const float X = e.x * s * ia, Y = e.y * s * ib;
        const float L = P[14];
        float th = atan2f(X, -Y) + P[16] + P[17] * (s / L);
        th -= F3D_TAU * floorf((th + F3D_PI) * (1.f / F3D_TAU));
        const float vt = s / L + P[15];
        const float u = uv_mirror(1.f + P[13] * th * (1.f / F3D_TAU)), v = uv_mirror(vt);
        // inward wall normal (xy) and the ray's slope relative to the (bent) tube axis -> |n.d|
        const float gx = copysignf(__powf(fmaxf(fabsf(X), 1e-12f), n - 1.f), X) * ia;
        const float gy = copysignf(__powf(fmaxf(fabsf(Y), 1e-12f), n - 1.f), Y) * ib;
        const float gl = rsqrtf(gx * gx + gy * gy + 1e-20f);
        const float sg = g + s * s0 / ((s + s0) * (s + s0));
        const float ndv = fmaxf(fabsf(gx * (q.x - B.x * sg) + gy * (q.y - B.y * sg)) * gl * cosa, 0.02f);
        const float lod = tex_lod(s * rl, pa, cosa, ndv, L / (float)H, P[26]);
        float3 c = tex_sample(T, u, v, lod);
        // headlight: grazing walls darken (k = 0 identity); beat flash brightens without clipping
        c = c * (1.f + P[21] * (saturate(0.3f + 1.7f * ndv) - 1.f));
        c = c + c * (f3(1.f) - c) * P[22];
        // fog (and a guaranteed fade where the mip chain runs out, so the far walls never shimmer)
        const float f = m * fmaxf(1.f - __expf(-fd * s), saturate((lod - 4.5f) * 0.7f));
        const float pidx = drift + per * 0.12f * __log2f(1.f + s);
        c = mix(c, pal(pidx, pal12) * (0.3f + 0.7f * f), f);
        // ring lights on the tile seams (box-filtered: they thin out instead of aliasing with depth)
        const float dv = fabsf(vt - rintf(vt)) * L;
        const float fpz = fmaxf(s * pa / ndv, 1e-6f), wr = 0.035f * L;
        const float core = saturate(1.f - dv / fmaxf(wr, fpz)) * fminf(1.f, wr / fpz);
        const float halo = 0.3f * __expf(-dv / (0.18f * L));
        const float rk = P[23] * (core + halo) * (1.f - f) * saturate(1.2f - fpz / (0.4f * L));
        c = tw_screen(c, pal(pidx + 0.5f, pal12) * rk);
        col = c; fv = f;
    }
    if (wc > 0.f) {
        // ---- end cap: the frame on the plane at depth scap, centred on the bent axis there; mirrored outside
        const float gc = scap / (scap + s0);
        const float X = (q.x - B.x * gc) * scap, Y = (q.y - B.y * gc) * scap;
        const float lod = tex_lod(scap * rl, pa, cosa, cosa, frame_texel(T), P[26]);
        float3 c = tex_sample(T, uv_mirror(0.5f + 0.5f * X / A), uv_mirror(0.5f - 0.5f * Y), lod);
        const float f = m * (1.f - __expf(-fd * scap));
        c = mix(c, pal(drift + per * 0.12f * __log2f(1.f + scap), pal12) * (0.3f + 0.7f * f), f);
        col = wc < 1.f ? mix(col, c, wc) : c;
        fv = fv + (f - fv) * wc;
    }
    // portal rim where the walls meet the cap, and the vanishing-point glow (screen blend)
    const float rim = P[23] * 0.8f * __expf(-fabsf(s - scap) / (1.5f * dsp)) * (1.f - fv);
    col = tw_screen(col, pal(drift + 0.25f, pal12) * rim);
    const float f2_ = fv * fv;
    const float3 gcol = pal(drift, pal12) * 0.55f + f3(0.45f);
    col = tw_screen(col, gcol * (P[20] * f2_ * f2_));
    store_rgb_dither(out, x, y, w, col, fv);
}
"""


def _perimeter(a: float, b: float, n: float) -> float:
    """Approximate perimeter of the superellipse |x/a|^n + |y/b|^n = 1 (ellipse at n = 2 -> rectangle)."""
    pe = math.pi * (3.0 * (a + b) - math.sqrt((3.0 * a + b) * (a + 3.0 * b)))
    pr = 4.0 * (a + b)
    w = math.sqrt(min(max((n - 2.0) / 14.0, 0.0), 1.0))
    return pe + (pr - pe) * w


class TunnelWall(Effect):
    name = "tunnel_wall"
    stage = "projection"
    label = "Picture tunnel"
    description = "The frame tiles the inside of a tube you fly through, fogging to a glowing vanishing point."
    params = {
        **standard_effect_params(),
        # the beat envelope kicks speed and flashes directly: keep it raw (easing would flatten the punch)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "repeats": num(1.0, 4.0, 2.0, "Repeats around", step=0.05, tier="regional", basic=True),
        "shape": num(0.0, 1.0, 0.65, "Square ↔ round", tier="regional", basic=True),
        "twist": num(-1.0, 1.0, 0.2, "Twist", tier="regional"),
        "fog": num(0.0, 1.0, 0.5, "Fog", tier="regional"),
        "bank": num(0.0, 1.0, 0.5, "Banking turns", tier="regional"),
        "speed": num(0.0, 4.0, 1.0, "Flight speed", unit="×", tier="realtime", bind="rate"),
        "kick": num(0.0, 3.0, 1.2, "Beat speed kick", step=0.05, tier="regional"),
        "rings": num(0.0, 1.0, 0.45, "Ring lights", tier="regional"),
        "glow": num(0.0, 1.0, 0.6, "Vanishing-point glow", tier="regional"),
        **palette_params("neon", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("tunnel_wall")
        self.mips = FrameMips(ctx, levels=6, blur_level=0)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self.A = ctx.width / ctx.height
        self.D = flat_distance(FOV)
        self.fly = 0.0          # flight phase in tiles, wrapped mod 2 (the mirror period)
        self.rot = 0.0          # texture rotation phase (moves with the walls when twisted)
        self.ph1 = 0.7
        self.ph2 = 2.1
        self.drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dts = min(max(float(dt), 0.0), 0.1)
        a = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        speed = max(float(p["speed"]), 0.0)
        A, D = self.A, self.D

        # tube geometry (also needed to integrate the flight in tile units)
        n = 2.0 * 2.0 ** (3.0 * (1.0 - min(max(float(p["shape"]), 0.0), 1.0)))
        kk = 2.0 ** (1.0 / n) * 1.02                   # superellipse through the frame corners, 2% margin
        ax, bx = A * kk, kk
        reps = min(max(float(p["repeats"]), 1.0), 4.0)
        L = _perimeter(ax, bx, n) / reps / A             # tiles keep the frame's aspect

        # host-integrated phases (always, so nothing jumps when amount returns)
        vel = 2.2 * speed + 5.0 * float(p["kick"]) * pulse          # world units / s; the beat adds to the rate
        dfly = vel * dts / L
        twist_k = float(p["twist"]) * TWO_PI * 0.12                  # rad per tile
        self.fly = (self.fly + dfly) % 2.0
        self.rot = (self.rot + twist_k * dfly) % TWO_PI
        wob = 0.5 + 0.5 * min(speed, 2.0)
        self.ph1 = (self.ph1 + TWO_PI * 0.045 * wob * dts) % TWO_PI
        self.ph2 = (self.ph2 + TWO_PI * 0.031 * wob * dts) % TWO_PI
        self.drift = (self.drift + float(p["drift_speed"]) * dts) % 10.0
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dts > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt.copy()

        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        m = smoothstep01(a)

        # banking turns: the axis bends towards B (projected vanishing-point offset), camera rolls into the turn
        bank = float(p["bank"]) * m
        bxv = bank * 0.2 * (0.75 * math.sin(self.ph1) + 0.25 * math.sin(2.3 * self.ph1 + 1.0))
        byv = bank * 0.08 * math.sin(self.ph2)
        roll = -1.6 * bxv
        # fog: depth where it reaches 50 %; the cap recedes into it (exponential dolly from the flat distance)
        fog = min(max(float(p["fog"]), 0.0), 1.0)
        s50 = 60.0 * math.exp(-2.7 * fog)
        scap_max = min(4.0 * s50, 140.0)
        scap = D * math.exp(m * math.log(max(scap_max, D * 1.01) / D))

        h = self.host
        h[0] = D
        h[1] = m
        h[2] = math.cos(roll)
        h[3] = math.sin(roll)
        h[4] = 0.45 * bxv
        h[5] = 0.45 * byv
        h[6] = bxv
        h[7] = byv
        h[8] = 2.5
        h[9] = ax
        h[10] = bx
        h[11] = n
        h[12] = 1.0 / n
        h[13] = reps
        h[14] = L
        h[15] = self.fly
        h[16] = self.rot
        h[17] = twist_k
        h[18] = scap
        h[19] = math.log(2.0) / s50
        h[20] = float(p["glow"]) * m * (1.0 + 0.6 * pulse)
        h[21] = 0.65 * m
        h[22] = 0.55 * pulse * m
        h[23] = float(p["rings"]) * m * (0.5 + 1.5 * pulse)
        h[24] = self.drift
        h[25] = float(p["palette_period"])
        h[26] = -0.35
        h[27] = 400.0
        h[P_PAL:P_PAL + 12] = self._pal
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P))
