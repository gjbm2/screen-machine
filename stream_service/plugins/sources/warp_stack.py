"""Warp stack: a layered generative scene.

    field (fbm | plasma | rings | cells | stripes)  <- coordinates through up to 3 warps
        (domain | swirl | kaleido | tunnel | ripple | rotozoom)
    -> cosine palette with drift (index = field * period + drift)
    -> optional iso-line "ink" coverage
    -> feedback: the previous frame, zoomed / rotated / shifted / chroma-split, decayed, blended under the new
    -> gain, vignette

Every *_speed, fb_zoom (a factor per second), fb_rotate, fb_dx and fb_dy is a rate, integrated on the host
each frame with ``dt``: the session's speed master (the scene clock) changes pace, never position.
``energy`` is a macro (0.5 = as set): it scales the warp amounts x0.6-1.4 and the pattern, warp and drift speeds
x0.5-1.5, so a preset can bind its liveliness to one signal (activity_slow).  Bindings are resolved by the base
class (``step_params``) before each frame; ``palette`` follows the session palette when one is set.
Renders at ``render_scale`` (but never below 720 lines unless the output is smaller) and upsamples bilinearly.
"""
from __future__ import annotations

import math

import numpy as np

from ...gpu import raw_module
from ...palettes import NAMES as PALETTE_NAMES
from ...palettes import cosine
from .._cuda import HELPERS, launch_2d
from ..base import Source

FIELDS = ("fbm", "plasma", "rings", "cells", "stripes")
WARPS = ("none", "domain", "swirl", "kaleido", "tunnel", "ripple", "rotozoom")
FB_MODES = ("mix", "lighten", "screen")
MIN_LINES = 720

_SRC = HELPERS + r"""
__device__ __forceinline__ float2 rot2(float2 p, float a) {
    const float c = cosf(a), s = sinf(a);
    return make_float2(c * p.x - s * p.y, s * p.x + c * p.y);
}
__device__ __forceinline__ float len2(float2 p) { return sqrtf(p.x * p.x + p.y * p.y); }
__device__ float fbmN(float2 p, unsigned int seed, int oct) {
    float s = 0.f, amp = 0.5f, norm = 0.f;
    for (int o = 0; o < oct; ++o) {
        s += amp * vnoise(p.x, p.y, seed + 31u * o); norm += amp;
        p = make_float2(1.97f * p.x + 0.31f * p.y, -0.31f * p.x + 1.97f * p.y); amp *= 0.5f;
    }
    return s / norm;
}
__device__ float2 warp(float2 p, const float* w, unsigned int seed) {
    const int type = (int)w[0];
    const float amt = w[1], sc = w[2], ph = w[3], n = w[4];
    if (type == 1) {                        // domain warp (one or two levels)
        const float2 a = make_float2(p.x * sc + ph, p.y * sc - 0.6f * ph);
        float2 q = make_float2(fbmN(a, seed, 4), fbmN(make_float2(a.x + 5.2f, a.y + 1.3f), seed, 4));
        if (n >= 2.f) {
            const float2 b = make_float2(a.x + 4.f * q.x + 1.7f - 0.4f * ph, a.y + 4.f * q.y + 9.2f);
            q = make_float2(fbmN(b, seed + 7u, 4), fbmN(make_float2(b.x + 8.3f, b.y + 2.8f), seed + 7u, 4));
        }
        return make_float2(p.x + amt * (q.x - 0.5f) * 2.f, p.y + amt * (q.y - 0.5f) * 2.f);
    } else if (type == 2) {                 // swirl: twist falling off with radius
        const float r = len2(p);
        return rot2(p, amt * expf(-r * sc) + ph);
    } else if (type == 3) {                 // kaleidoscope with n mirrored segments
        const float r = len2(p);
        const float seg = 6.2831853f / fmaxf(n, 1.f);
        float a = atan2f(p.y, p.x) + ph;
        a = a - seg * floorf(a / seg);
        a = fabsf(a - 0.5f * seg);
        return make_float2(r * cosf(a) * sc, r * sinf(a) * sc);
    } else if (type == 4) {                 // tunnel: (mirrored angle, depth) -- flies forward as phase grows
        const float r = len2(p) + 1e-3f;
        const float a = fabsf(atan2f(p.y, p.x)) / 3.14159265f;
        return make_float2(a * sc, amt / r + ph);
    } else if (type == 5) {                 // radial ripple
        const float r = len2(p);
        const float k = 1.f + amt * sinf(r * sc - ph);
        return make_float2(p.x * k, p.y * k);
    } else if (type == 6) {                 // rotate + breathe
        const float2 q = rot2(p, ph);
        const float k = sc * expf(amt * sinf(n * ph));
        return make_float2(q.x * k, q.y * k);
    }
    return p;
}
__device__ float field(int f, float2 q, float sc, float ph, float detail, unsigned int seed) {
    float v;
    if (f == 0) {
        v = clamp01((fbmN(make_float2(q.x * sc + ph, q.y * sc + 0.7f * ph), seed + 101u, 5) - 0.2f) / 0.6f);
    } else if (f == 1) {
        const float s = sc;
        v = (sinf(q.x * s + ph) + sinf(q.y * s * 1.3f - 0.7f * ph) + sinf((q.x + q.y) * s * 0.7f + 1.3f * ph)
             + sinf(len2(q) * s * 1.7f - ph)) * 0.125f + 0.5f;
    } else if (f == 2) {
        v = 0.5f + 0.5f * sinf(6.2831853f * (len2(q) * sc - ph));
    } else if (f == 3) {                    // animated Voronoi, F1 distance
        const float2 g = make_float2(q.x * sc, q.y * sc);
        const float gx = floorf(g.x), gy = floorf(g.y);
        float d1 = 9.f;
        for (int j = -1; j <= 1; ++j) for (int i = -1; i <= 1; ++i) {
            const int cx = (int)gx + i, cy = (int)gy + j;
            const float hx = hash1(cx, cy, seed), hy = hash1(cx, cy, seed + 1u);
            const float px = cx + 0.5f + 0.42f * sinf(ph + 6.2831853f * hx);
            const float py = cy + 0.5f + 0.42f * sinf(1.3f * ph + 6.2831853f * hy);
            const float dx = px - g.x, dy = py - g.y;
            d1 = fminf(d1, dx * dx + dy * dy);
        }
        v = clamp01(sqrtf(d1) * 1.25f);
    } else {
        v = 0.5f + 0.5f * sinf(q.x * sc + ph);
    }
    if (detail > 0.f)
        v += detail * (fbmN(make_float2(q.x * sc * 3.7f - 0.3f * ph, q.y * sc * 3.7f), seed + 55u, 3) - 0.5f);
    return v;
}

// P: [0]field [1]f_scale [2]f_phase [3]detail [4..15]palette a,b,c,d [16]period [17]drift [18]contrast
//    [19]ink [20]ink_w [21]fb_amount [22]fb_zoom [23]fb_rot [24]fb_dx [25]fb_dy [26]fb_chroma [27]fb_mode
//    [28]fb_decay [29]gain [30]vignette [31]nw
extern "C" __global__
void warp_stack(uchar4* __restrict__ out, const uchar4* __restrict__ prev, const int W, const int H,
                const float* __restrict__ P, const float* __restrict__ WP, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    float2 p = make_float2((x + 0.5f - 0.5f * W) / hh, -(y + 0.5f - hh) / hh);
    const int nw = (int)P[31];
    for (int i = 0; i < nw; ++i) p = warp(p, WP + 8 * i, seed + 1000u * i);
    const float v = field((int)P[0], p, P[1], P[2], P[3], seed);
    const float t = v * P[16] + P[17];
    float3 c;
    c.x = P[4] + P[7] * cosf(6.2831853f * (P[10] * t + P[13]));
    c.y = P[5] + P[8] * cosf(6.2831853f * (P[11] * t + P[14]));
    c.z = P[6] + P[9] * cosf(6.2831853f * (P[12] * t + P[15]));
    const float shade = 1.f - P[18] + P[18] * clamp01(v);
    const float g = 255.f * P[29] * shade;
    float4 N = make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f);
    float alpha = 1.f;
    if (P[19] > 0.f) {                      // iso-line ink: draw only near the contours of v
        const float s = v * P[19] - floorf(v * P[19]);
        alpha = 1.f - smooth(0.f, P[20], fminf(s, 1.f - s));
    }
    float4 F = make_float4(0.f, 0.f, 0.f, 255.f);
    const float fba = P[21];
    if (fba > 0.f) {
        const float cx = 0.5f * W + P[24] * hh, cy = hh - P[25] * hh;
        const float ux = x + 0.5f - cx, uy = y + 0.5f - cy;
        const float cr = cosf(P[23]), sr = sinf(P[23]);
        const float rx = cr * ux + sr * uy, ry = -sr * ux + cr * uy;
        const float z = 1.f / P[22], ch = P[26];
        const float zr = z * (1.f - ch), zb = z * (1.f + ch);
        F.x = bilin(prev, cx + rx * zr, cy + ry * zr, W, H).x;
        F.y = bilin(prev, cx + rx * z, cy + ry * z, W, H).y;
        F.z = bilin(prev, cx + rx * zb, cy + ry * zb, W, H).z;
        F.x *= P[28]; F.y *= P[28]; F.z *= P[28];
    }
    float4 O;
    const int mode = (int)P[27];
    if (mode == 1) {
        O = make_float4(fmaxf(F.x, N.x * alpha), fmaxf(F.y, N.y * alpha), fmaxf(F.z, N.z * alpha), 255.f);
    } else if (mode == 2) {
        O = make_float4(255.f - (255.f - F.x) * (255.f - N.x * alpha) / 255.f,
                        255.f - (255.f - F.y) * (255.f - N.y * alpha) / 255.f,
                        255.f - (255.f - F.z) * (255.f - N.z * alpha) / 255.f, 255.f);
    } else {
        const float k = P[19] > 0.f ? alpha : alpha * (1.f - fba);
        O = mix4(fba > 0.f ? F : make_float4(0.f, 0.f, 0.f, 255.f), N, k);
    }
    if (P[30] > 0.f) {
        const float ex = x + 0.5f - 0.5f * W, ey = y + 0.5f - hh;
        const float r2 = (ex * ex + ey * ey) / (0.25f * ((float)W * W + (float)H * H));
        const float k = 1.f - P[30] * r2;
        O.x *= k; O.y *= k; O.z *= k;
    }
    out[y * W + x] = pack(O);
}

extern "C" __global__
void upsample(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int w, const int h,
              const int W, const int H)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    dst[y * W + x] = pack(bilin(src, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h));
}
"""


def _num(lo, hi, default, label, step=0.01, unit=None, bind=False, basic=False):
    ui = {"label": label, "step": step, "widget": "slider"}
    if unit:
        ui["unit"] = unit
    if basic:
        ui["basic"] = True              # a headline param: the simple Room page shows only these
    d = {"type": "number", "minimum": lo, "maximum": hi, "default": default, "x-ui": ui}
    if bind:
        d["x-bindable"] = True
    return d


def _warp_slot(i: int) -> dict:
    return {
        f"w{i}_type": {"type": "string", "enum": list(WARPS), "default": "none",
                       "x-ui": {"label": f"Warp {i}", "widget": "select"}},
        f"w{i}_amount": _num(-8.0, 8.0, 0.5, f"Warp {i} amount", bind=True),
        f"w{i}_scale": _num(0.0, 20.0, 1.0, f"Warp {i} scale", bind=True),
        f"w{i}_speed": _num(-5.0, 5.0, 0.2, f"Warp {i} speed", unit="/s", bind=True),
        f"w{i}_n": _num(0.0, 24.0, 6.0, f"Warp {i} n (segments / levels / breathe)", step=1),
    }


class WarpStack(Source):
    name = "warp_stack"
    label = "Warp stack"
    description = "A pattern seen through up to three warps, coloured by a drifting palette, with feedback."
    params = {
        "field": {"type": "string", "enum": list(FIELDS), "default": "fbm",
                  "x-ui": {"label": "Pattern", "widget": "select"}},
        "field_scale": _num(0.05, 40.0, 2.0, "Pattern scale", bind=True),
        "field_speed": _num(-5.0, 5.0, 0.3, "Pattern speed", unit="/s", bind=True),
        "detail": _num(0.0, 1.0, 0.0, "Detail", bind=True),
        **_warp_slot(1), **_warp_slot(2), **_warp_slot(3),
        "energy": _num(0.0, 1.0, 0.5, "Energy (macro)", bind=True),
        "palette": {"type": "string", "enum": PALETTE_NAMES, "default": "neon", "x-palette": True,
                    "x-ui": {"label": "Palette", "widget": "select", "basic": True}},
        "palette_period": _num(0.05, 10.0, 1.0, "Palette period", bind=True),
        "drift_speed": _num(-2.0, 2.0, 0.03, "Colour drift", unit="/s", bind=True, basic=True),
        "contrast": _num(0.0, 1.0, 0.3, "Shade by pattern", bind=True),
        "ink": _num(0.0, 40.0, 0.0, "Ink lines (0 = solid)", step=0.5),
        "ink_width": _num(0.005, 0.5, 0.08, "Ink width", bind=True),
        "fb_amount": _num(0.0, 0.98, 0.0, "Trails", bind=True, basic=True),
        "fb_zoom": _num(0.2, 5.0, 1.0, "Feedback zoom", unit="×/s", bind=True),
        "fb_rotate": _num(-360.0, 360.0, 0.0, "Feedback rotate", step=1, unit="°/s", bind=True),
        "fb_dx": _num(-0.2, 0.2, 0.0, "Feedback drift x", step=0.001, bind=True),
        "fb_dy": _num(-0.2, 0.2, 0.0, "Feedback drift y", step=0.001, bind=True),
        "fb_chroma": _num(0.0, 0.05, 0.0, "Feedback chroma split", step=0.001, bind=True),
        "fb_mode": {"type": "string", "enum": list(FB_MODES), "default": "mix",
                    "x-ui": {"label": "Feedback blend", "widget": "select"}},
        "fb_decay": _num(0.5, 1.0, 0.96, "Feedback decay", step=0.001, bind=True),
        "gain": _num(0.0, 3.0, 1.0, "Gain", bind=True),
        "vignette": _num(0.0, 1.0, 0.25, "Vignette", bind=True),
        "render_scale": {**_num(0.25, 1.0, 1.0, "Render scale", step=0.05), "x-ease": False},
        "seed": {"type": "integer", "minimum": 0, "maximum": 2**31 - 1, "default": 1, "x-ease": False,
                 "x-ui": {"label": "Seed"}},
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        s = max(float(params["render_scale"]), min(1.0, MIN_LINES / ctx.height))
        self.w = max(16, int(round(ctx.width * s / 2)) * 2)
        self.h = max(16, int(round(ctx.height * s / 2)) * 2)
        self.cur = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.prev = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        self.k = mod.get_function("warp_stack")
        self.k_up = mod.get_function("upsample")
        self.phase = {"field": 0.0, "drift": 0.0, "w1": 0.0, "w2": 0.0, "w3": 0.0}
        sp = self.session_palette()
        self.pal = np.array(cosine(params["palette"]) if sp is None else sp, dtype=np.float64)   # (4, 3), eased
        self.P = cp.zeros(32, cp.float32)
        self.WP = cp.zeros(24, cp.float32)

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        ph = self.phase
        e_amt, e_spd = 0.6 + 0.8 * p["energy"], 0.5 + p["energy"]
        ph["field"] += p["field_speed"] * e_spd * dt
        ph["drift"] += p["drift_speed"] * e_spd * dt
        for i in (1, 2, 3):
            ph[f"w{i}"] += p[f"w{i}_speed"] * e_spd * dt
        # palette changes blend over ~0.8 s (like fractal_zoom); the session palette arrives already crossfaded
        sp = self.session_palette()
        tp = np.array(cosine(p["palette"]) if sp is None else sp, dtype=np.float64)
        if dt > 0 and not np.allclose(tp, self.pal, atol=1e-6):
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        fb_rot = math.radians(p["fb_rotate"]) * dt                 # per-frame increments from per-second rates
        fb_zoom = max(p["fb_zoom"], 1e-3) ** dt if dt > 0 else 1.0
        P = [FIELDS.index(p["field"]), p["field_scale"], ph["field"], p["detail"], *self.pal.reshape(-1),
             p["palette_period"], ph["drift"], p["contrast"], p["ink"], p["ink_width"], p["fb_amount"],
             fb_zoom, fb_rot, p["fb_dx"] * dt * 30, p["fb_dy"] * dt * 30, p["fb_chroma"],
             FB_MODES.index(p["fb_mode"]), p["fb_decay"] ** (dt * 60 if dt > 0 else 1), p["gain"], p["vignette"]]
        warps = []
        for i in (1, 2, 3):
            if p[f"w{i}_type"] != "none":
                warps += [WARPS.index(p[f"w{i}_type"]), p[f"w{i}_amount"] * e_amt, p[f"w{i}_scale"], ph[f"w{i}"],
                          p[f"w{i}_n"], 0, 0, 0]
        P.append(len(warps) // 8)
        cp = self.ctx.cp
        self.P.set(np.asarray(P, np.float32))
        if warps:
            self.WP[: len(warps)].set(np.asarray(warps, np.float32))
        launch_2d(self.k, self.w, self.h, (self.cur, self.prev, np.int32(self.w), np.int32(self.h), self.P, self.WP,
                                           np.uint32(p["seed"])))
        if (self.w, self.h) == (self.ctx.width, self.ctx.height):
            cp.copyto(out, self.cur)
        else:
            launch_2d(self.k_up, self.ctx.width, self.ctx.height,
                      (self.cur, out, np.int32(self.w), np.int32(self.h), np.int32(self.ctx.width),
                       np.int32(self.ctx.height)))
        self.cur, self.prev = self.prev, self.cur

    def close(self) -> None:
        self.cur = self.prev = self.P = self.WP = None
        self.k = self.k_up = None
