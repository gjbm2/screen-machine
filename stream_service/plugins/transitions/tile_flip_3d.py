"""Tile flip 3D: a wall of screens.  The frame splits into tiles with dark dividers; each tile is a thin slab
that flips over in true perspective (with anticipation and a little overshoot) to show the new scene on its
back, staggered as a random scatter, a diagonal wave or a radial burst.  The slab rims are palette-coloured
and catch the light as they turn.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/GridFlip.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/TilesWave.glsl
Author: TimDonselaar (GridFlip, ported to gl-transitions by gre); numb3r23 (TilesWave)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: kept GridFlip's grid of tiles with dark dividers that grow in at the
  start and close at the end (its `pause` phases), the per-tile random delay and the "front shows from, back shows
  to" flip; kept TilesWave's diagonal wave ordering.  Changes: the 2D squash of both originals becomes a true 3D
  flip: each tile is a slab (thickness `thickness`) rotated about an in-plane axis (`flip_axis_deg`, 0 = vertical
  axis, 90 = horizontal, anything between = diagonal) and ray-cast analytically (a ray/oriented-box slab test in
  the tile's own frame, per-tile pinhole camera whose distance is set by `perspective`); the tile is pushed back by
  exactly its nearest-corner depth so it never grows past its cell, and uniformly shrunk when a diagonal axis or
  the slab rim would poke out of it; the back face samples `to` mirrored across the axis so it reads upright at
  180 degrees; rows follow from the aspect ratio; the delay order is a continuous `stagger` morph random ->
  diagonal -> radial (min-max normalised on the host) with `stagger_spread` setting how far apart tiles start;
  the flip curve is an ease-in-out-back with `snap` controlling anticipation/overshoot; faces get Lambert turn
  shading (exactly 1 at rest) and a specular glint that sweeps across them mid-turn; the slab rims and the wall
  behind the tiles use the shared cosine palette (`palette`, `backing`) drifting with progress; face edges are
  anti-aliased from an analytic pixel footprint; fract(sin()) is replaced by hash1; endpoints are forced
  (progress <= 0 copies a, >= 1 copies b).)

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Binder: style lively, 2-4 beats starting on a downbeat; with stagger_spread around 0.5-0.75 tile groups land
across the beats.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, flag, num

_SRC = HELPERS + PALETTE_CUDA + r"""
// light (surface -> light), unit length: upper-left, in front of the wall
#define LX (-0.5014f)
#define LY (-0.6017f)
#define LZ (-0.6217f)

__device__ __forceinline__ float ease_back(float u, float k) {      // ease-in-out-back, k = 0 -> cubic in-out
    const float c2 = 1.70158f * 1.525f * k;
    if (u < 0.5f) { const float t = 2.f * u; return 0.5f * t * t * ((c2 + 1.f) * t - c2); }
    const float t = 2.f * u - 2.f;
    return 0.5f * (t * t * ((c2 + 1.f) * t + c2) + 2.f);
}

__device__ __forceinline__ void slab(float o, float d, float h, int id, float& tn, float& tf, int& ax, float& sg) {
    if (fabsf(d) < 1e-9f) { if (fabsf(o) > h) { tn = 1e30f; tf = -1e30f; } return; }
    const float inv = 1.f / d;
    float t1 = (-h - o) * inv, t2 = (h - o) * inv, s = -1.f;     // d > 0: enter at -h
    if (t1 > t2) { const float tt = t1; t1 = t2; t2 = tt; s = 1.f; }
    if (t1 > tn) { tn = t1; ax = id; sg = s; }
    tf = fminf(tf, t2);
}

// tile-local normal (nx, ny in the tile plane, nw through it) -> world normal for a flip of (sn, cs) about u
__device__ __forceinline__ float3 world_n(float nx, float ny, float nw, float ux, float uy, float vx, float vy,
                                          float sn, float cs) {
    const float al = nx * ux + ny * uy, be = nx * vx + ny * vy;
    const float vc = be * cs - nw * sn, nz = be * sn + nw * cs;
    return make_float3(al * ux + vc * vx, al * uy + vc * vy, nz);
}

__device__ __forceinline__ float4 rgb4(float3 c, float s) {
    return make_float4(255.f * c.x * s, 255.f * c.y * s, 255.f * c.z * s, 255.f);
}

extern "C" __global__
void tile_flip(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
               const float* __restrict__ key, const float* __restrict__ P,
               const int W, const int H, const int N, const int M,
               const float prog, const float g0, const float span, const float fl,
               const float div_px, const float d_rel, const float thick_rel,
               const float ux, const float uy, const float snap, const float bidx, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float tw = (float)W / (float)N, th = (float)H / (float)M;
    const int ix = min((int)((x + 0.5f) / tw), N - 1), iy = min((int)((y + 0.5f) / th), M - 1);
    const float ccx = (ix + 0.5f) * tw, ccy = (iy + 0.5f) * th;
    const float qx0 = x + 0.5f - ccx, qy0 = y + 0.5f - ccy;
    const float hx = fmaxf(0.5f * tw - 0.5f * div_px, 1.f), hy = fmaxf(0.5f * th - 0.5f * div_px, 1.f);
    const int tid = iy * N + ix;

    // the wall behind the tiles: deep palette colour with a soft central glow
    const float gx = (x + 0.5f) / (float)W - 0.5f, gy = (y + 0.5f) / (float)H - 0.5f;
    const float glow = __expf(-(gx * gx * 3.f + gy * gy * 5.f));
    const float3 wc = pal(bidx + 0.5f + 0.18f * gy + 0.06f * gx, P);
    const float4 wall = rgb4(wc, 0.07f + 0.13f * glow);
    const float3 ec = pal(bidx + 0.22f * hash1((unsigned)tid, seed, 7u), P);    // this tile's rim colour

    const float u = clamp01((prog - (g0 + key[tid] * (span - fl))) / fl);
    if (u <= 0.f || u >= 1.f) {                     // at rest: flat tile (a or b) inside its divider frame
        const float cov = clamp01(hx - fabsf(qx0) + 0.5f) * clamp01(hy - fabsf(qy0) + 0.5f);
        if (cov <= 0.f) { out[y * W + x] = pack(wall); return; }
        const float4 c = ld(u <= 0.f ? a : b, x, y, W, H);
        out[y * W + x] = pack(cov >= 1.f ? c : mix4(wall, c, cov));
        return;
    }

    const float ang = 3.14159265f * ease_back(u, snap);
    float sn, cs;
    sincosf(ang, &sn, &cs);
    const float vx = uy, vy = -ux;                  // in-plane perpendicular of the flip axis
    const float bmax = hx * fabsf(vx) + hy * fabsf(vy);
    const float D = d_rel * bmax;
    const float hT = fmaxf(0.5f * thick_rel * fminf(tw, th), 0.5f);
    const float z0 = bmax * fabsf(sn) + hT * fabsf(cs);   // push back so the nearest corner sits on the wall plane

    // uniform shrink so the projected slab stays inside its cell (diagonal axes, thick rims)
    float fit = 1.f;
    #pragma unroll
    for (int c = 0; c < 8; ++c) {
        const float px = (c & 1) ? hx : -hx, py = (c & 2) ? hy : -hy, pw = (c & 4) ? hT : -hT;
        const float al = px * ux + py * uy, be = px * vx + py * vy;
        const float vc = be * cs - pw * sn, zc = z0 + be * sn + pw * cs;
        const float sc = D / fmaxf(D + zc, 1e-3f);
        fit = fmaxf(fit, fmaxf(fabsf(al * ux + vc * vx) * sc / hx, fabsf(al * uy + vc * vy) * sc / hy));
    }
    const float qx = qx0 * fit, qy = qy0 * fit;

    // ray from the tile's pinhole (0, 0, -D) through (qx, qy, 0), into the slab's local frame
    const float bo = -(D + z0);
    const float be_o = bo * sn, w_o = bo * cs;
    const float al_d = qx * ux + qy * uy, bv_d = qx * vx + qy * vy;
    const float be_d = bv_d * cs + D * sn, w_d = -bv_d * sn + D * cs;
    const float ox = be_o * vx, oy = be_o * vy;
    const float dx = al_d * ux + be_d * vx, dy = al_d * uy + be_d * vy;
    float tn = -1e30f, tf = 1e30f, sg = 0.f;
    int ax = -1;
    slab(ox, dx, hx, 0, tn, tf, ax, sg);
    slab(oy, dy, hy, 1, tn, tf, ax, sg);
    slab(w_o, w_d, hT, 2, tn, tf, ax, sg);
    if (ax < 0 || tn > tf || tf <= 0.f) { out[y * W + x] = pack(wall); return; }

    const float Px = ox + tn * dx, Py = oy + tn * dy;
    const float il = rsqrtf(qx * qx + qy * qy + D * D);
    const float Vx = -qx * il, Vy = -qy * il, Vz = -D * il;          // hit -> eye
    float Hx = LX + Vx, Hy = LY + Vy, Hz = LZ + Vz;
    const float ih = rsqrtf(Hx * Hx + Hy * Hy + Hz * Hz + 1e-12f);
    Hx *= ih; Hy *= ih; Hz *= ih;
    const float turn = sn * sn;

    float4 col;
    if (ax == 2) {                                                    // a face: front = a, back = b (mirrored)
        const float3 Nw = world_n(0.f, 0.f, sg, ux, uy, vx, vy, sn, cs);
        const float ndl = Nw.x * LX + Nw.y * LY + Nw.z * LZ;
        const float ndh = fmaxf(Nw.x * Hx + Nw.y * Hy + Nw.z * Hz, 0.f);
        const float shade = fminf(fmaxf((1.f + 0.85f * (ndl + LZ)) * (0.62f + 0.38f * fabsf(Nw.z)), 0.1f), 1.4f);
        float4 c;
        if (sg < 0.f) {
            c = bilin(a, ccx + Px, ccy + Py, W, H);
        } else {
            const float pu = Px * ux + Py * uy;                       // reflect across the flip axis
            c = bilin(b, ccx + 2.f * pu * ux - Px, ccy + 2.f * pu * uy - Py, W, H);
        }
        const float gl = 0.75f * turn * __powf(ndh, 28.f);
        c = make_float4(c.x * shade + 255.f * gl * (0.55f + 0.45f * ec.x),
                        c.y * shade + 255.f * gl * (0.55f + 0.45f * ec.y),
                        c.z * shade + 255.f * gl * (0.55f + 0.45f * ec.z), 255.f);
        // analytic edge AA: local units per screen pixel, foreshortened across the axis
        const float cf = fmaxf(fabsf(Nw.z), 0.04f), s0 = fit * tn;
        const float fpx = s0 * sqrtf(ux * ux + vx * vx / (cf * cf)), fpy = s0 * sqrtf(uy * uy + vy * vy / (cf * cf));
        const float ex = (hx - fabsf(Px)) / fpx, ey = (hy - fabsf(Py)) / fpy;
        const float cov = clamp01(ex + 0.5f) * clamp01(ey + 0.5f);
        if (cov < 1.f) {
            // what lies under the edge: the rim on that side if it faces the eye, else the wall
            const float3 Ns = ex < ey ? world_n(Px < 0.f ? -1.f : 1.f, 0.f, 0.f, ux, uy, vx, vy, sn, cs)
                                      : world_n(0.f, Py < 0.f ? -1.f : 1.f, 0.f, ux, uy, vx, vy, sn, cs);
            float4 under = wall;
            if (Ns.x * Vx + Ns.y * Vy + Ns.z * Vz > 0.f) {
                const float l = 0.3f + 0.95f * fmaxf(Ns.x * LX + Ns.y * LY + Ns.z * LZ, 0.f);
                under = rgb4(ec, l);
            }
            c = mix4(under, c, cov);
        }
        col = c;
    } else {                                                          // a rim: bold palette colour, lit
        const float3 Nw = ax == 0 ? world_n(sg, 0.f, 0.f, ux, uy, vx, vy, sn, cs)
                                  : world_n(0.f, sg, 0.f, ux, uy, vx, vy, sn, cs);
        const float ndl = fmaxf(Nw.x * LX + Nw.y * LY + Nw.z * LZ, 0.f);
        const float ndh = fmaxf(Nw.x * Hx + Nw.y * Hy + Nw.z * Hz, 0.f);
        const float l = 0.3f + 0.95f * ndl;
        const float sp = 0.6f * __powf(ndh, 20.f);
        col = make_float4(255.f * (ec.x * l + sp), 255.f * (ec.y * l + sp), 255.f * (ec.z * l + sp), 255.f);
    }
    out[y * W + x] = pack(col);
}
"""


def _smooth(e0: float, e1: float, v: float) -> float:
    t = min(max((v - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class TileFlip3d(Transition):
    name = "tile_flip_3d"
    label = "Tile flip"
    description = ("A wall of screens: the frame splits into tiles with dark dividers and each tile flips over "
                   "in true perspective to reveal the new scene, as a random, diagonal or radial wave.")
    default_duration_s = 1.6
    params = {
        "tile_count": num(2, 24, 8, "Tiles across", tier="regional", integer=True, basic=True),
        "stagger": num(0.0, 1.0, 0.5, "Wave (0 random, 0.5 diagonal, 1 radial)", tier="regional", basic=True),
        "stagger_spread": num(0.0, 1.0, 0.6, "Wave spread", tier="regional"),
        "divider_width": num(0.0, 0.1, 0.04, "Divider width", tier="regional"),
        "flip_axis_deg": num(0.0, 90.0, 0.0, "Flip axis (0 vertical, 90 horizontal)", step=1.0, unit="°",
                             tier="regional"),
        "perspective": num(0.0, 1.0, 0.6, "Perspective", tier="regional"),
        "thickness": num(0.0, 0.2, 0.06, "Tile thickness", tier="regional"),
        "snap": num(0.0, 1.0, 0.4, "Snap (anticipation/overshoot)", tier="regional"),
        "backing": num(0.0, 1.0, 0.3, "Rim / backing colour (palette index)", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "reverse": flag(False, "Reverse wave direction", tier="structural"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        w, h = ctx.width, ctx.height
        self._mmax = max(1, int(24 * h / w + 0.5))
        self._key = cp.zeros(24 * self._mmax, cp.float32)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng(None if s < 0 else s).integers(0, 2**31 - 1))
        self._key_sig = None
        self._k = raw_module(_SRC).get_function("tile_flip")

    def _grid(self) -> tuple[int, int]:
        w, h = self.ctx.width, self.ctx.height
        n = int(min(max(round(float(self.params["tile_count"])), 2), 24))
        m = int(min(max(int(n * h / w + 0.5), 1), self._mmax))
        return n, m

    def _sync(self, n: int, m: int) -> None:
        p = self.params
        if p["palette"] != self._pal:
            self._P.set(np.asarray(palette_coeffs(p["palette"]), np.float32))
            self._pal = p["palette"]
        st = min(max(float(p["stagger"]), 0.0), 1.0)
        sig = (n, m, round(st, 4), bool(p["reverse"]))
        if sig == self._key_sig:
            return
        self._key_sig = sig
        w, h = self.ctx.width, self.ctx.height
        aspect = w / h
        rng = np.random.default_rng(self._seed + 977 * n + m)
        cx = (np.arange(n, dtype=np.float32) + 0.5) / n
        cy = (np.arange(m, dtype=np.float32) + 0.5) / m
        X, Y = np.meshgrid(cx, cy)                       # (m, n), row-major like tid = iy * n + ix
        rnd = rng.random((m, n)).astype(np.float32)
        jit = rng.random((m, n)).astype(np.float32)
        diag = X * aspect + Y + 0.12 * jit
        rad = np.hypot((X - 0.5) * aspect, Y - 0.5) + 0.06 * jit

        def norm(v):
            lo, hi = float(v.min()), float(v.max())
            return (v - lo) / max(hi - lo, 1e-6)

        diag, rad = norm(diag), norm(rad)
        key = rnd + (diag - rnd) * (2 * st) if st < 0.5 else diag + (rad - diag) * (2 * st - 1)
        key = norm(key)
        if p["reverse"]:
            key = 1.0 - key
        buf = np.zeros(self._key.size, np.float32)
        buf[: n * m] = key.reshape(-1)
        self._key.set(buf)

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        n, m = self._grid()
        self._sync(n, m)
        w, h = self.ctx.width, self.ctx.height
        t = float(progress)
        g0, g1 = 0.04, 0.96
        span = g1 - g0
        fl = max(span * (1.0 - 0.92 * min(max(float(p["stagger_spread"]), 0.0), 1.0)), 0.04)
        env = _smooth(0.0, 0.12, t) * _smooth(1.0, 0.88, t)        # dividers grow in, then close at the end
        cell = min(w / n, h / m)
        div_px = float(p["divider_width"]) * cell * env
        persp = min(max(float(p["perspective"]), 0.0), 1.0)
        d_rel = 1.4 + 24.0 * (1.0 - persp) ** 2
        axis = math.radians(min(max(float(p["flip_axis_deg"]), 0.0), 90.0))
        ux, uy = math.sin(axis), math.cos(axis)                      # 0 deg = vertical axis
        launch_2d(self._k, w, h,
                  (a, b, out, self._key, self._P, np.int32(w), np.int32(h), np.int32(n), np.int32(m),
                   np.float32(t), np.float32(g0), np.float32(span), np.float32(fl), np.float32(div_px),
                   np.float32(d_rel), np.float32(p["thickness"]), np.float32(ux), np.float32(uy),
                   np.float32(p["snap"]), np.float32(float(p["backing"]) + 0.35 * t),   # colour drifts with progress
                   np.uint32(self._seed & 0xFFFFFFFF)))
