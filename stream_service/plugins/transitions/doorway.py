"""Doorway: the old picture splits down the middle and swings open like a pair of double doors, hinged at the
frame edges and turning away from the viewer in true perspective while the camera pushes through the doorway.
A palette-coloured light spills through the crack first, fans across the floor, rims the doors' free edges, and
the new scene rushes forward out of that light from depth, standing on a glossy floor that reflects it (and the
doors).

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/doorway.glsl
Author: gre (Gaetan Renaudeau)
License: MIT   (file header says "// Author: gre // License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors",
                https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: kept the middle split into two doors, the door priority
  (from-doors over the incoming picture over the background), the incoming picture's approach from depth
  `size = mix(1, depth, 1 - progress)` (here eased: `1 + (depth - 1)(1 - progress)^1.6`, so it rushes in then
  settles), and the floor reflection `project(p) = p * (1, -1.2) - (0, 0.02)` weighted
  `reflection * (1 - y)`.  Changes: the original's slide-plus-skew doors become real hinged panels (rotation
  about the frame edges, exact inverse perspective mapping, `perspective` 0 = orthographic foreshortening,
  1 = strong) with a camera push-through so the opened doors sweep out of frame; frames are y-down pixel arrays
  so the uv math runs in y-up, height-normalised, centred coordinates; edges are anti-aliased from analytic
  footprints; the black background becomes a palette-coloured light spill (`gap_glow`, `glow_tint`,
  `palette`, colour drifting with progress): a white-hot core in the gap that widens as the doors open, a halo,
  a fan of light across the floor, a rim light on the doors' free edges and a haze the incoming scene emerges
  from; doors darken as they turn (`shading`) and also reflect on the floor; the reflection gets a 2-tap gloss;
  `door_axis` adds a horizontal split (top/bottom doors); endpoints are forced (progress <= 0 copies a,
  >= 1 copies b).)

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

Binder: style lively, 2-4 beats, ideal on a section change, starting on a downbeat.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_SRC = HELPERS + PALETTE_CUDA + r"""
// Inverse door mapping.  Screen point (ac across the split, al along it), centred, height units, y-up.
// The door on that side is hinged at |across| = A, turned by angle (c = cos, s = sin) away from the viewer;
// camera push q (1 = none), perspective k.  Returns the distance from the hinge w and the along coordinate Y,
// plus the analytic per-pixel footprints of both (fw, fy, in units of one pixel = pxu).
__device__ __forceinline__ bool door_map(float ac, float al, float c, float s, float q, float k, float A, float pxu,
                                         float& w, float& Y, float& fw, float& fy)
{
    const float ax = -fabsf(ac);
    const float den = c - ax * k * s;                    // >= c >= 0
    if (den <= 1e-5f) return false;
    const float id = 1.f / den;
    w = (ax * q + A) * id;
    const float z = q + k * w * s;
    if (z <= 1e-4f) return false;
    Y = al * z;
    const float dw = (q * c + A * k * s) * id * id;     // dw/d|ac|
    fw = fmaxf(pxu * dw, 1e-7f);
    fy = fmaxf(pxu * hypotf(z, al * k * s * dw), 1e-7f);
    return true;
}

__device__ __forceinline__ float4 scale4(float4 c, float s) { return make_float4(c.x * s, c.y * s, c.z * s, 255.f); }

__device__ __forceinline__ float3 bright_pal(float t, const float* __restrict__ P) {
    float3 g = pal(t, P);
    const float m = 1.f / fmaxf(fmaxf(g.x, fmaxf(g.y, g.z)), 0.15f);   // light is always vivid
    return make_float3(fminf(g.x * m, 1.f), fminf(g.y * m, 1.f), fminf(g.z * m, 1.f));
}

// door-local (across_img, along) in centred height units -> image pixel coords
__device__ __forceinline__ float2 door_px(float acr, float alg, int horiz, float r, int W, int H) {
    const float Xi = horiz ? alg : acr, Yi = horiz ? acr : alg;
    return make_float2((Xi / r + 0.5f) * W, (0.5f - Yi) * H);
}

extern "C" __global__
void doorway(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
             const int W, const int H, const float prog, const float c, const float s, const float q,
             const float k, const float A, const float B, const int horiz, const float size, const float refl,
             const float shade, const float G, const float fog, const float gw, const float tint,
             const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float r = (float)W / (float)H, pxu = 1.f / (float)H;
    const float sx = x + 0.5f, sy = y + 0.5f;
    const float X = (sx / W - 0.5f) * r, Yc = 0.5f - sy / H;
    const float ac = horiz ? Yc : X, al = horiz ? X : Yc;
    const float sgn = ac < 0.f ? -1.f : 1.f;

    // ---- doors
    float w = -1.f, Y = 0.f, fw = 1.f, fy = 1.f;
    const bool dv = door_map(ac, al, c, s, q, k, A, pxu, w, Y, fw, fy);
    float covD = 0.f, cw = 0.f;
    if (dv) {
        cw = clamp01(fminf(w, A - w) / fw + 0.5f);
        covD = cw * clamp01((B - fabsf(Y)) / fy + 0.5f);
    }
    const float wc = fminf(fmaxf(w, 0.f), A);
    const float dsh = 1.f - shade * s * (0.25f + 0.55f * wc / A);   // fronts darken as they turn from the light

    // ---- light spill through the gap
    const float d = fabsf(ac);
    float prof = 0.f, core = 0.f;
    if (G > 0.f) {
        const float wa = 0.004f + 0.55f * gw;
        core = __expf(-d * d / (wa * wa));
        const float halo = 0.45f * __expf(-d / (0.06f + 0.9f * gw));
        prof = (core + halo) / (1.f + 1.5f * al * al);
        if (!horiz) {                                        // a fan of light across the floor
            const float tf = -0.5f / size - al;
            if (tf > 0.f) {
                const float wf = wa + 0.05f + 1.2f * tf;
                prof += 0.55f * __expf(-d * d / (wf * wf)) * smooth(0.f, 0.08f, tf) * __expf(-2.f * tf);
            }
        }
        prof *= G;
    }
    float3 gc = bright_pal(tint + 0.35f * d, P);
    const float wh = 0.6f * core;
    gc = make_float3(gc.x + (1.f - gc.x) * wh, gc.y + (1.f - gc.y) * wh, gc.z + (1.f - gc.z) * wh);
    const float gl = 255.f * 1.15f * prof;
    const float4 glow = make_float4(gc.x * gl, gc.y * gl, gc.z * gl, 0.f);

    if (covD >= 1.f) {                                       // fully on a door: 1 tap
        const float2 dp = door_px(sgn * (A - w), Y, horiz, r, W, H);
        float4 col = scale4(bilin(a, dp.x, dp.y, W, H), dsh);
        const float rim = G * 0.9f * __expf(-((A - w) / fw) * (1.f / 12.f));
        col.x += 0.2f * glow.x + 255.f * rim * gc.x;
        col.y += 0.2f * glow.y + 255.f * rim * gc.y;
        col.z += 0.2f * glow.z + 255.f * rim * gc.z;
        out[y * W + x] = pack(col);
        return;
    }

    // ---- incoming picture, approaching from depth
    const float ux = (sx / W - 0.5f) * size + 0.5f, uy = (0.5f - sy / H) * size + 0.5f;
    const float cTx = clamp01(fminf(ux, 1.f - ux) / (size / W) + 0.5f);
    const float cTy = clamp01(fminf(uy, 1.f - uy) / (size / H) + 0.5f);
    const float covT = cTx * cTy;

    float4 col = make_float4(0.f, 0.f, 0.f, 255.f);
    if (covT < 1.f) {
        // background: floor reflections, then the light
        if (refl > 0.f) {
            const float ry = -1.2f * uy - 0.02f;
            if (ry > 0.f && ry < 1.f && cTx > 0.f) {
                const float wt = refl * (1.f - ry) * smooth(0.f, 0.025f, ry) * cTx;
                const float bl = 0.002f + 0.03f * ry;       // gloss: blur widens away from the picture
                const float4 c0 = bilin(b, ux * W, (1.f - (ry - bl)) * H, W, H);
                const float4 c1 = bilin(b, ux * W, (1.f - (ry + bl)) * H, W, H);
                col = mix4(col, mix4(c0, c1, 0.5f), wt);
            }
            if (!horiz && dv && cw > 0.f) {                  // the doors reflect too (nearer: on top)
                const float v = (Y + B) / (2.f * B);
                const float vr = -1.2f * v - 0.02f;
                if (v < 0.f && vr > 0.f && vr < 1.f) {
                    const float wt = refl * 0.85f * (1.f - vr) * smooth(0.f, 0.025f, vr) * cw;
                    const float2 dp = door_px(sgn * (A - wc), vr * 2.f * B - B, horiz, r, W, H);
                    col = mix4(col, scale4(bilin(a, dp.x, dp.y, W, H), dsh), wt);
                }
            }
        }
        col.x += glow.x; col.y += glow.y; col.z += glow.z;
    }
    if (covT > 0.f) {
        float4 t = bilin(b, ux * W, (1.f - uy) * H, W, H);
        if (fog > 0.f) {                                     // emerges out of the light
            const float3 fc = bright_pal(tint, P);
            t = mix4(t, make_float4(255.f * fc.x, 255.f * fc.y, 255.f * fc.z, 255.f), fog);
        }
        t.x += 0.5f * glow.x; t.y += 0.5f * glow.y; t.z += 0.5f * glow.z;
        col = mix4(col, t, covT);
    }
    if (covD > 0.f) {
        const float2 dp = door_px(sgn * (A - wc), Y, horiz, r, W, H);
        float4 dc = scale4(bilin(a, dp.x, dp.y, W, H), dsh);
        const float rim = G * 0.9f * __expf(-(fmaxf(A - w, 0.f) / fw) * (1.f / 12.f));
        dc.x += 0.2f * glow.x + 255.f * rim * gc.x;
        dc.y += 0.2f * glow.y + 255.f * rim * gc.y;
        dc.z += 0.2f * glow.z + 255.f * rim * gc.z;
        col = mix4(col, dc, covD);
    }
    out[y * W + x] = pack(col);
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class Doorway(Transition):
    name = "doorway"
    label = "Doorway"
    description = ("The old picture splits and swings open like double doors in perspective; light spills through "
                   "the gap and the new scene rushes forward from depth over a glossy floor reflection.")
    default_duration_s = 1.6
    params = {
        "perspective": num(0.0, 1.0, 0.5, "Perspective (0 = flat)", tier="regional"),
        "depth": num(1.0, 6.0, 3.0, "Depth of the new scene", tier="regional"),
        "reflection": num(0.0, 0.8, 0.4, "Floor reflection", tier="regional"),
        "gap_glow": num(0.0, 1.0, 0.7, "Light through the gap", tier="regional", basic=True),
        "shading": num(0.0, 1.0, 0.5, "Door shading", tier="regional"),
        "glow_tint": num(0.0, 1.0, 0.0, "Light tint (palette index)", tier="regional"),
        "palette": enum(list(PALETTES), "dusk", "Light palette", tier="regional"),
        "door_axis": enum(["vertical", "horizontal"], "vertical", "Door split", tier="structural"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("doorway")
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        self._sync_palette()

    def _sync_palette(self) -> None:
        name = self.params["palette"]
        if name != self._pal:                                   # regional: follow palette changes
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        self._sync_palette()
        w, h = self.ctx.width, self.ctx.height
        t = float(progress)
        ratio = w / h
        horiz = p["door_axis"] == "horizontal"
        A, B = (0.5, 0.5 * ratio) if horiz else (0.5 * ratio, 0.5)
        k = 0.9 * min(max(float(p["perspective"]), 0.0), 1.0)
        th = 0.5 * math.pi * _smoothstep(0.0, 0.88, t)          # doors crack, burst, settle fully open
        c, s = math.cos(th), math.sin(th)
        q = 1.0 - (k * A + 0.08) * _smoothstep(0.03, 0.92, t)   # camera pushes through: doors leave frame
        gw = min(A * (1.0 - c) / max(q + k * A * s, 1e-3), 2.0)  # gap half-width on screen
        size = 1.0 + (max(float(p["depth"]), 1.0) - 1.0) * (1.0 - t) ** 1.6
        glow = float(p["gap_glow"])
        env = _smoothstep(0.0, 0.1, t) * (1.0 - _smoothstep(0.45, 0.97, t))
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(t), np.float32(c), np.float32(s),
                   np.float32(q), np.float32(k), np.float32(A), np.float32(B), np.int32(1 if horiz else 0),
                   np.float32(size), np.float32(p["reflection"]), np.float32(p["shading"]),
                   np.float32(glow * env), np.float32(0.6 * glow * (1.0 - t) ** 2), np.float32(gw),
                   np.float32(p["glow_tint"] + 0.25 * t),          # the light's colour drifts through the move
                   self._P))
