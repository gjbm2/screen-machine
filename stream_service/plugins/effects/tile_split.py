"""Video wall split: the picture repeats on an N x N wall of screens (grid, brick or hex), with dark bezels,
mirrored alternate screens, and rows or columns that slide like a sliding puzzle on the beat and spring back.

Source: https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/Quad%20Tile.fs
        https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/MultiFrame%203x3.fs
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/hexagonalize.glsl
        https://www.redblobgames.com/grids/hexagons/ (hex lattice maths)
Author: VIDVOX (ISF Quad Tile, MultiFrame 3x3); Fernando Kuteken (gl-transitions hexagonalize)
License: MIT
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the ISF quad/3x3 tiling and the hexagonalize cell lookup reimplemented as
one CUDA kernel; continuous cell count N = mix(1, cells, amount) zooming out about the centre, with a grid phase that
eases so every integer N shows exactly N x N whole screens; rect / brick / pointy-hex lattices share one row-based
lookup (the hex rows meet on a zig-zag boundary) and morph into each other on the host; cells keep the frame's
aspect; continuous mirror of alternate screens (crossfade); per-screen rotation that swings; anti-aliased bezels
measured in screen pixels, dark at rest and flashing a palette colour on the beat with a screen-blended halo;
per-screen palette tint with drift; beat rising edges throw alternate rows or columns sideways (axis picked by
weight) and a substepped semi-implicit damped spring pulls them back onto the grid.)

amount 0 is the untouched input (copied).  As amount rises the single full-frame screen zooms out into the wall,
so small amounts are a near-identity zoom.  cells (2-8) is how many screens across and down at amount 1.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

NP = 32
# P layout (floats)
#  0..11 palette | 12 N (cells across) | 13 grid phase | 14 row offset (brick/hex, 0..0.5 cell) | 15 hex zig-zag amp
#  16 row slide (cells, odd rows +, even rows -) | 17 column slide (cells, odd cols +, even -) | 18 mirror weight
#  19 bezel half-width px | 20 bezel colour gain | 21 tint | 22 drift | 23 palette period | 24 rotation (rad)
#  25 rotation swing phase | 26 content inverse zoom | 27 halo gain
_SRC = HELPERS + PALETTE_CUDA + r"""
// reflect a pixel coordinate into [0, n] (mirror-repeat: seamless when a rotated / hex cell reads past the edge)
__device__ __forceinline__ float ts_reflect(float x, float n) {
    const float p = 2.f * n;
    x = x - p * floorf(x / p);
    return x < n ? x : p - x;
}
// triangle wave: +1 at t = 0 (a row's own screen centres), -1 at t = 0.5 (between them)
__device__ __forceinline__ float ts_tri(float t) {
    const float f = t + 0.5f - floorf(t + 0.5f);
    return 1.f - 4.f * fabsf(f - 0.5f);
}
__device__ __forceinline__ float ts_rowc(int j, const float* __restrict__ P) {
    const bool odd = (j & 1) != 0;
    return (odd ? P[14] : 0.f) + (odd ? P[16] : -P[16]);
}

extern "C" __global__
void tile_split(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float fW = (float)W, fH = (float)H;
    const float N = P[12], phi = P[13], a = P[15];
    // cell space: one unit = one screen (the screen has the frame's aspect)
    float fx = ((x + 0.5f) / fW - 0.5f) * N + phi;
    float fy = ((y + 0.5f) / fH - 0.5f) * N + phi;
    // column slide (vertical, alternate columns opposite ways)
    const int ic = (int)floorf(fx + 0.5f);
    fy += (ic & 1) ? P[17] : -P[17];
    // row lookup; hex rows meet on a zig-zag (pointy-top cells)
    int j = (int)floorf(fy + 0.5f);
    float ly = fy - (float)j;
    if (a > 1e-5f) {
        if (ly >= 0.f) {
            const float B = 0.5f + a * ts_tri(fx - ts_rowc(j, P));
            if (ly > B) { j += 1; ly -= 1.f; }
        } else {
            const float B = -0.5f + a * ts_tri(fx - ts_rowc(j - 1, P));
            if (ly < B) { j -= 1; ly += 1.f; }
        }
    }
    const float gx = fx - ts_rowc(j, P);
    const int i = (int)floorf(gx + 0.5f);
    const float lx = gx - (float)i;

    // distance to this screen's edge, in output pixels (zig-zag edges via the gradient)
    const float cwp = fW / N, chp = fH / N;
    const float slope = 4.f * a * fH / fW;
    const float inv = rsqrtf(1.f + slope * slope);
    const float Bb = 0.5f + a * ts_tri(fx - ts_rowc(j, P));
    const float Bt = -0.5f + a * ts_tri(fx - ts_rowc(j - 1, P));
    float d = (0.5f - fabsf(lx)) * cwp;
    d = fminf(d, (Bb - ly) * chp * inv);
    d = fminf(d, (ly - Bt) * chp * inv);

    // content: the whole frame per screen, optionally rotated, zoom-punched, mirrored on alternate screens
    const bool oi = (i & 1) != 0, oj = (j & 1) != 0;
    float px = lx * fW * P[26], py = ly * fH * P[26];
    float4 c = make_float4(0.f, 0.f, 0.f, 255.f);
    {
        float th = 0.f;
        if (P[24] != 0.f) {
            const float sg = ((i + j) & 1) ? 1.f : -1.f;
            th = P[24] * sg * (0.75f + 0.25f * __sinf(P[25] + 6.2831853f * hash1((unsigned)(i + 4096), (unsigned)(j + 4096), 11u)));
        }
        float sn = 0.f, cs = 1.f;
        if (th != 0.f) __sincosf(th, &sn, &cs);
        const float mw = (oi || oj) ? P[18] : 0.f;
        const float qx = oi ? -px : px, qy = oj ? -py : py;
        if (mw < 0.999f) {
            const float rx = cs * px - sn * py, ry = sn * px + cs * py;
            c = bilin(src, ts_reflect(0.5f * fW + rx, fW), ts_reflect(0.5f * fH + ry, fH), W, H);
        }
        if (mw > 0.001f) {
            const float rx = cs * qx - sn * qy, ry = sn * qx + cs * qy;
            const float4 m = bilin(src, ts_reflect(0.5f * fW + rx, fW), ts_reflect(0.5f * fH + ry, fH), W, H);
            c = (mw < 0.999f) ? mix4(c, m, mw) : m;
        }
    }
    // per-screen palette tint (each screen "calibrated" a little differently; drifts)
    const float per = P[23];
    if (P[21] > 0.001f) {
        const float h = hash1((unsigned)(i + 4096), (unsigned)(j + 4096), 3u);
        const float3 tc = pal(per * (0.17f * (float)i + 0.29f * (float)j + 0.45f * h) + P[22], P);
        const float t = P[21];
        c.x *= 1.f + t * (0.85f * tc.x - 0.45f);
        c.y *= 1.f + t * (0.85f * tc.y - 0.45f);
        c.z *= 1.f + t * (0.85f * tc.z - 0.45f);
    }
    // bezels: dark, with a palette-coloured light that flashes on the beat and a soft halo into the screen
    const float hw = P[19];
    if (hw > 0.05f || P[27] > 0.001f) {
        const float3 g = pal(per * (0.11f * fx + 0.07f * fy) + P[22] + 0.5f, P);
        const float inside = smooth(hw - 0.75f, hw + 0.75f, d);
        const float gg = P[20] * 255.f;
        const float4 bez = make_float4(c.x * 0.06f + gg * g.x, c.y * 0.06f + gg * g.y, c.z * 0.06f + gg * g.z, 255.f);
        c = mix4(bez, c, inside);
        if (P[27] > 0.001f) {
            const float hl = P[27] * __expf(-fmaxf(d - hw, 0.f) / (2.f * hw + 6.f)) * inside;
            c.x += hl * g.x * (255.f - c.x);
            c.y += hl * g.y * (255.f - c.y);
            c.z += hl * g.z * (255.f - c.z);
        }
    }
    out[y * W + x] = pack(c);
}
"""

_LATTICE = {"rect": (0.0, 0.0), "brick": (0.5, 0.0), "hex": (0.5, 0.22)}   # (row offset, zig-zag amp)
_ZETA = 0.32          # spring damping ratio: a visible bounce as the rows settle
_SWAY_HZ = 0.11


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smooth01(x: float) -> float:
    x = _clamp01(x)
    return x * x * (3.0 - 2.0 * x)


class TileSplit(Effect):
    name = "tile_split"
    stage = "warp"
    label = "Video wall split"
    description = ("Repeats the picture on a wall of screens (grid, brick or hex) with dark bezels and mirrored "
                   "screens; rows or columns slide like a sliding puzzle on the beat and spring back.")
    tags = {"family": ["warp"], "dims": ["2d"], "technique": ["tiling", "kaleido"]}
    params = {
        **standard_effect_params(),
        # rising-edge detection wants the raw beat envelope
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "cells": num(2.0, 8.0, 3.0, "Screens across", step=0.05, tier="regional", basic=True),
        "lattice": enum(["rect", "brick", "hex"], "rect", "Lattice", tier="structural"),
        "mirror": num(0.0, 1.0, 0.0, "Mirror alternate screens", tier="regional"),
        "gutter": num(0.0, 2.0, 0.6, "Bezel width", step=0.05, unit="%", tier="regional"),
        "slide_axis": num(0.0, 1.0, 0.3, "Slide rows ↔ columns", tier="regional"),
        "slide_kick": num(0.0, 1.0, 0.45, "Beat slide", step=0.01, unit="screens", tier="regional"),
        "spring_hz": num(0.5, 6.0, 2.2, "Spring snap", step=0.05, unit="Hz", tier="regional"),
        "sway": num(0.0, 0.3, 0.05, "Idle sway", step=0.005, unit="screens", tier="regional"),
        "cell_rotation": num(0.0, 1.0, 0.0, "Screen rotation", tier="regional"),
        "bezel_glow": num(0.0, 1.0, 0.4, "Bezel light", tier="regional"),
        "tint": num(0.0, 1.0, 0.18, "Screen tint", tier="regional"),
        **palette_params("neon", period=1.0, drift=0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("tile_split")
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        seed = int(params.get("seed", 7) or 7) if isinstance(params, dict) else 7
        self.rng = np.random.default_rng(seed)
        p0 = self.easer.values()
        self.lat = np.array(_LATTICE.get(p0.get("lattice", "rect"), (0.0, 0.0)), dtype=np.float64)
        self.pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        # springs: row slide (x, v) and column slide (x, v), in screens
        self.xr = self.vr = self.xc = self.vc = 0.0
        self.prev_pulse = 0.0
        self.clock = 0.0
        self.last_kick = -1.0
        self.sway_ph = float(self.rng.uniform(0.0, 2.0 * math.pi))
        self.rot_ph = float(self.rng.uniform(0.0, 2.0 * math.pi))
        self.drift = float(self.rng.uniform(0.0, 1.0))

    def _springs(self, dt: float, w: float) -> None:
        n = max(1, int(math.ceil(dt * 120.0)))
        h = dt / n
        k = w * w
        c = 2.0 * _ZETA * w
        for _ in range(n):
            self.vr += (-k * self.xr - c * self.vr) * h
            self.xr += self.vr * h
            self.vc += (-k * self.xc - c * self.vc) * h
            self.xc += self.vc * h

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        W, H = self.ctx.width, self.ctx.height
        dts = min(max(float(dt), 0.0), 0.1)
        amount = _clamp01(p["amount"])
        m = _smooth01(amount)
        pulse = _clamp01(p["pulse"])
        w = 2.0 * math.pi * min(max(float(p["spring_hz"]), 0.3), 8.0)

        if dts > 0.0:
            self.clock += dts
            # beat rising edge -> throw alternate rows or columns (axis by weight, random direction)
            if pulse - self.prev_pulse > 0.15 and self.clock - self.last_kick > 0.12:
                self.last_kick = self.clock
                v0 = float(p["slide_kick"]) * w / 0.6 * (0.5 + 0.5 * pulse)
                sgn = 1.0 if self.rng.random() < 0.5 else -1.0
                if self.rng.random() < _clamp01(p["slide_axis"]):
                    self.vc += sgn * v0
                else:
                    self.vr += sgn * v0
            self._springs(dts, w)
            self.sway_ph = math.fmod(self.sway_ph + 2.0 * math.pi * _SWAY_HZ * dts, 2.0 * math.pi)
            self.rot_ph = math.fmod(self.rot_ph + 2.0 * math.pi * 0.07 * dts, 2.0 * math.pi)
            self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dts, 1000.0)
            # lattice and palette morph on the host (the enums switch at once)
            tl = np.array(_LATTICE.get(p["lattice"], (0.0, 0.0)), dtype=np.float64)
            self.lat += (tl - self.lat) * (1.0 - math.exp(-dts / 0.6))
            tp = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dts / 1.0))
        self.prev_pulse = pulse
        # keep the springs bounded whatever the input does
        self.xr = min(max(self.xr, -3.0), 3.0)
        self.xc = min(max(self.xc, -3.0), 3.0)

        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return

        cells = min(max(float(p["cells"]), 1.0), 8.0)
        N = 1.0 + (cells - 1.0) * m
        # grid phase: 0 at odd N (a screen centred), 0.5 at even N (a bezel centred) -> whole screens at integers
        s = math.sin(0.5 * math.pi * (N - 1.0))
        phi = 0.5 * s * s
        sway = float(p["sway"])
        row_slide = (self.xr + sway * math.sin(self.sway_ph)) * m
        col_slide = (self.xc + 0.7 * sway * math.sin(0.83 * self.sway_ph + 1.3)) * m
        bg = float(p["bezel_glow"])
        h = self.host
        h[0:12] = self.pal
        h[12] = N
        h[13] = phi
        h[14] = self.lat[0]
        h[15] = self.lat[1] * m
        h[16] = row_slide
        h[17] = col_slide
        h[18] = _clamp01(p["mirror"])
        h[19] = 0.5 * float(p["gutter"]) * 0.01 * H * m
        h[20] = bg * m * (0.12 + 0.88 * pulse)
        h[21] = float(p["tint"]) * m
        h[22] = self.drift
        h[23] = float(p["palette_period"])
        h[24] = float(p["cell_rotation"]) * m * 0.5 * math.pi
        h[25] = self.rot_ph
        h[26] = 1.0 / (1.0 + 0.05 * pulse * m)
        h[27] = 0.6 * bg * m * pulse
        self.P.set(h)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self.P))
