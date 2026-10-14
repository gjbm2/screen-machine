"""Polar wrap / tiny planet: rolls the frame into a ring or a little planet (rect -> polar), or unrolls the centre
into horizontal bands (polar -> rect), with mirrored angular repeats, a spiral twist, a log "tunnel" radius and an
endless radial scroll.  A landscape becomes a little planet, or an endless tunnel made of the picture.

Source: https://godotshaders.com/shader/uv-polar-coordinates/
        https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Circle%20Wrap%20Distortion.fs
Author: Riko (godotshaders UV Polar Coordinates); VIDVOX (ISF Circle Wrap Distortion)
License: CC0-1.0 + MIT
license_class: permissive

Ported from GLSL by smfx (ideas/changes: the ISF/godot mapping (x, y) -> (theta, r) and its inverse; changes:
the angular coordinate goes through an EVEN mirrored repeat u = fold(repeats * theta / pi), so any fractional
repeat count meets itself at the atan2 cut with no seam; ring rotation and the spiral twist rotate the output
coordinate before atan2 (the seam turns with the picture and stays invisible); radius morphs linear <-> log2
("tunnel": the picture repeats in mirrored rings into the centre); the radial coordinate scrolls through a
mirrored repeat so rings stream outward forever; trilinear sampling over a FrameMips chain with the LOD from the
mapping's finite-difference Jacobian, so the pinched planet core does not sparkle; pal()-coloured glow lines on
the mirror creases (rings and spokes, constant width in screen pixels) and a "light at the end of the tunnel" core,
screen-blended so bright stills never clip.)

Realtime: m = smoothstep(amount); the sample coordinate is mix(pixel, polar(pixel), m), so the frame curls
progressively rather than crossfading (amount 0 is an exact copy).  spin_rate / scroll_rate are host-integrated,
wrapped phases; pulse (attack-smoothed) kicks both rates (x kick x m) and the glow gain, never a position.
One full-res kernel: three map evaluations (centre + two finite-difference neighbours), one trilinear tap.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

TWO_PI = 2.0 * math.pi
NQ = 32           # Q: [0..11] palette  [12..] mapping/glow params (see the kernel)
LOG_K = 0.55      # rings per octave of radius in tunnel mode

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define PW_PI 3.14159265f
#define PW_TAU 6.28318531f
#define PW_INV_PI 0.318309886f
#define PW_INV_TAU 0.159154943f
#define PW_LOGK 0.55f

// mirrored repeat with period 2 -> [0, 1] (continuous, even)
__device__ __forceinline__ float pw_fold(float s) {
    s = s - 2.f * floorf(s * 0.5f);
    return s > 1.f ? 2.f - s : s;
}
// mirror at the frame edges (pixel coords)
__device__ __forceinline__ float pw_edge(float v, float n) {
    const float n2 = 2.f * n;
    v = v - n2 * floorf(v / n2);
    return v > n ? n2 - v : v;
}

struct PwMap { float sx, sy, su, sv, r; };

// Q (after the 12 palette floats): 0 m, 1 repeats, 2 twist, 3 inner, 4 outer, 5 tunnel, 6 rot (rad, wrapped),
// 7 scroll (wrapped mod 2), 8 glow gain, 9 period, 10 drift, 11 core gain, 12 direction, 13 line width px
__device__ __forceinline__ PwMap pw_map(float px, float py, int W, int H, const float* __restrict__ Q) {
    const float m = Q[0], rep = Q[1], twist = Q[2], inner = Q[3], outer = Q[4], tun = Q[5];
    const float rot = Q[6], scroll = Q[7];
    const float hh = 0.5f * (float)H;
    PwMap o;
    float tx, ty;
    if (Q[12] < 0.5f) {
        // rect -> polar: output (r, theta) reads the picture at (u = angle, v = radius); ground at the core
        const float cx = (px - 0.5f * W) / hh, cy = (py - 0.5f * H) / hh;
        const float r = sqrtf(cx * cx + cy * cy);
        const float lin = (r - inner) / fmaxf(outer - inner, 0.05f);
        const float lg = 1.f + PW_LOGK * __log2f(fmaxf(r, 1e-4f) / outer);
        const float rr = lin + (lg - lin) * tun;
        float a = rot + twist * PW_TAU * rr;
        a -= PW_TAU * rintf(a * PW_INV_TAU);
        float s, c;
        __sincosf(a, &s, &c);
        const float qx = cx * c - cy * s, qy = cx * s + cy * c;
        const float th = atan2f(qx, -qy);                         // 0 at the top, +-pi at the bottom
        o.su = rep * th * PW_INV_PI;                              // even in theta: no seam at the cut
        o.sv = 1.f - rr + scroll;                                 // picture top on the outer rim
        o.r = r;
        tx = pw_fold(o.su) * W;
        ty = pw_fold(o.sv) * H;
    } else {
        // polar -> rect: output x = angle, y = radius; reads the picture around the frame centre
        const float ux = px / W, uy = py / H;
        o.su = (ux - 0.5f) * rep;                                 // half-turns across the width
        o.sv = 1.f - uy + scroll;
        const float rr = pw_fold(o.sv);
        const float rl = inner + rr * (outer - inner);
        const float rg = outer * exp2f((rr - 1.f) * (1.f / PW_LOGK));
        const float r = rl + (rg - rl) * tun;
        float a = o.su * PW_PI + rot + twist * PW_TAU * rr;
        a -= PW_TAU * rintf(a * PW_INV_TAU);
        float s, c;
        __sincosf(a, &s, &c);
        o.r = r;
        tx = 0.5f * W + r * s * hh;
        ty = 0.5f * H - r * c * hh;
    }
    o.sx = px + (tx - px) * m;
    o.sy = py + (ty - py) * m;
    return o;
}

extern "C" __global__
void polar_wrap(uchar4* __restrict__ out, FRAME_ARGS, const float* __restrict__ QA)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    TEX_FROM_ARGS;
    const float* P = QA;
    const float* Q = QA + 12;
    const float px = x + 0.5f, py = y + 0.5f;
    const PwMap a = pw_map(px, py, W, H, Q);
    const PwMap b = pw_map(px + 1.f, py, W, H, Q);
    const PwMap c = pw_map(px, py + 1.f, W, H, Q);
    // LOD from the Jacobian (source pixels per output pixel); identity -> level 0 bilinear at texel centres
    const float dxx = b.sx - a.sx, dxy = b.sy - a.sy, dyx = c.sx - a.sx, dyy = c.sy - a.sy;
    const float d2 = fmaxf(dxx * dxx + dxy * dxy, dyx * dyx + dyy * dyy);
    const float lod = 0.5f * __log2f(fmaxf(d2, 1e-8f)) - 0.35f;
    const float u = pw_edge(a.sx, (float)W) / (float)W, v = pw_edge(a.sy, (float)H) / (float)H;
    float3 col = tex_sample(T, u, v, lod);

    const float gg = Q[8];
    float3 add = make_float3(0.f, 0.f, 0.f);
    if (gg > 1e-4f) {
        const float rep = Q[1], lw = Q[13];
        // rings: creases of the radial mirror (sv integer), in screen pixels via the gradient
        const float gvx = b.sv - a.sv, gvy = c.sv - a.sv;
        const float gv = fmaxf(sqrtf(gvx * gvx + gvy * gvy), 1e-6f);
        const float dv = fabsf(a.sv - rintf(a.sv)) / gv;
        const float ring_fade = smooth(6.f * lw, 18.f * lw, 1.f / gv);   // rings packed tighter than this melt away
        float g = (__expf(-(dv * dv) / (lw * lw)) + 0.3f * __expf(-dv / (7.f * lw))) * ring_fade;
        if (Q[12] < 0.5f) {
            // spokes: creases of the angular mirror (su integer) and the mirror seam at the atan2 cut (|su| = rep)
            float gux = b.su - a.su, guy = c.su - a.su;
            if (gux > rep) gux -= 2.f * rep; else if (gux < -rep) gux += 2.f * rep;
            if (guy > rep) guy -= 2.f * rep; else if (guy < -rep) guy += 2.f * rep;
            const float gu = fmaxf(sqrtf(gux * gux + guy * guy), 1e-6f);
            const float du = fminf(fabsf(a.su - rintf(a.su)), fmaxf(rep - fabsf(a.su), 0.f)) / gu;
            const float spoke_fade = smooth(6.f * lw, 18.f * lw, 1.f / gu);
            g += 0.55f * (__expf(-(du * du) / (lw * lw)) + 0.25f * __expf(-du / (6.f * lw))) * spoke_fade;
        }
        const float3 pc = pal(Q[10] + Q[9] * (0.25f * a.sv + 0.2f * pw_fold(a.su)), P);
        const float k = gg * g;
        add = add + pc * k;
    }
    if (Q[11] > 1e-4f) {
        // the light at the end of the tunnel (hides the infinitely dense core)
        const float cx = (px - 0.5f * W) / (0.5f * H), cy = (py - 0.5f * H) / (0.5f * H);
        const float r2 = cx * cx + cy * cy;
        const float3 pc = pal(Q[10] + 0.5f, P);
        add = add + pc * (Q[11] * __expf(-r2 * 70.f));
    }
    // screen blend: never clips a bright still
    col.x += (1.f - col.x) * fminf(add.x, 1.f);
    col.y += (1.f - col.y) * fminf(add.y, 1.f);
    col.z += (1.f - col.z) * fminf(add.z, 1.f);
    store_rgb(out, y * W + x, col);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smooth01(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


class PolarWrap(Effect):
    name = "polar_wrap"
    stage = "warp"
    label = "Polar wrap / tiny planet"
    description = ("Rolls the frame into a little planet or ring (or unrolls the centre into bands), with mirrored "
                   "repeats, a spiral twist and an endless radial scroll; a tunnel made of the picture.")
    params = {
        **standard_effect_params(),
        # the beat envelope kicks rates and glow; keep it raw (easing flattens the punch)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "direction": enum(["rect_to_polar", "polar_to_rect"], "rect_to_polar", "Direction", tier="structural"),
        "repeats": num(1.0, 6.0, 1.0, "Angular repeats", step=0.05, tier="regional", basic=True),
        "twist": num(0.0, 2.0, 0.3, "Spiral twist", unit="turns", step=0.01, tier="regional", basic=True),
        "inner_radius": num(0.0, 0.5, 0.08, "Inner radius", unit="× half-height", tier="regional"),
        "radius": num(0.4, 2.2, 1.0, "Planet size", unit="× half-height", tier="regional"),
        "tunnel": num(0.0, 1.0, 0.0, "Linear ↔ tunnel (log) radius", tier="regional"),
        "glow": num(0.0, 1.0, 0.35, "Crease glow", tier="regional"),
        "kick": num(0.0, 3.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "spin_rate": num(-0.5, 0.5, 0.03, "Ring rotation", unit="turns/s", step=0.005, tier="realtime", bind="rate"),
        "scroll_rate": num(-1.0, 1.0, 0.06, "Radial scroll", unit="/s", step=0.01, tier="realtime", bind="rate"),
        **palette_params("neon", 1.0, 0.04),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("polar_wrap")
        self.mips = FrameMips(ctx, levels=4, blur_level=0)
        self._Q_h = np.zeros(NQ, dtype=np.float32)
        self._Q_d = cp.zeros(NQ, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._rot = 0.0          # integrated ring rotation (rad, wrapped)
        self._scroll = 0.0       # integrated radial scroll (picture heights, wrapped mod 2)
        self._drift = 0.0
        self._pk = 0.0           # attack-smoothed pulse

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dts = min(max(float(dt), 0.0), 0.1)
        amount = _clamp01(p["amount"])
        m = _smooth01(amount)
        kick = max(float(p["kick"]), 0.0)
        # pulse: ~25 ms attack so nothing snaps, then follows the envelope down
        pulse = _clamp01(p["pulse"])
        if pulse > self._pk and dts > 0:
            self._pk += (pulse - self._pk) * (1.0 - math.exp(-dts / 0.025))
        elif pulse <= self._pk:
            self._pk = pulse
        pk = self._pk * m
        # host-integrated, wrapped phases; the beat adds to the rates (in the direction of travel)
        spin = float(p["spin_rate"])
        scr = float(p["scroll_rate"])
        sg1 = 1.0 if spin >= 0.0 else -1.0
        sg2 = 1.0 if scr >= 0.0 else -1.0
        self._rot = math.fmod(self._rot + TWO_PI * (spin + sg1 * 0.12 * kick * pk) * dts, TWO_PI)
        self._scroll = math.fmod(self._scroll + (scr + sg2 * 0.45 * kick * pk) * dts, 2.0)
        if self._scroll < 0.0:
            self._scroll += 2.0
        self._drift += float(p["drift_speed"]) * dts
        self._drift -= math.floor(self._drift)
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dts > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dts / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        inner = min(max(float(p["inner_radius"]), 0.0), 0.5)
        outer = max(float(p["radius"]), inner + 0.1)
        tunnel = _clamp01(p["tunnel"])
        glow = _clamp01(p["glow"])
        Q = self._Q_h
        Q[0:12] = self._pal
        Q[12] = m
        Q[13] = min(max(float(p["repeats"]), 1.0), 6.0)
        Q[14] = float(p["twist"])
        Q[15] = inner
        Q[16] = outer
        Q[17] = tunnel
        Q[18] = self._rot
        Q[19] = self._scroll
        Q[20] = 0.9 * glow * m * m * (1.0 + 1.2 * kick * self._pk)
        Q[21] = float(p["palette_period"])
        Q[22] = self._drift
        Q[23] = (tunnel * m * m * (0.6 + 0.35 * glow) * (1.0 + 0.6 * kick * self._pk)
                 if p["direction"] == "rect_to_polar" else 0.0)
        Q[24] = 0.0 if p["direction"] == "rect_to_polar" else 1.0
        Q[25] = max(1.0, 2.5 * H / 2160.0)
        self._Q_d.set(Q)
        self.mips.build(src)
        launch_2d(self._k, W, H, (out, *self.mips.args(src), self._Q_d))

    def close(self) -> None:
        self._Q_d = None
        self.mips = None
