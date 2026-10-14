"""Warp sweep: a soft front crosses the screen (any direction, or grows out of a point as an iris); behind it the old
picture rushes toward the viewer while the new one settles into place, so the swap reads as a camera move.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/crosswarp.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/directionalwarp.glsl
Author: Eke Péter <peterekepeter@gmail.com> (crosswarp); pschroen (directionalwarp)
License: MIT   (both file headers say "// License: MIT"; repo LICENSE: "MIT License, Copyright (c)
                2017-present gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the two shaders are one family -- crosswarp is directionalwarp with
  the edge softness = 1 and the direction = right-to-left -- so they are merged into one plug-in with a free
  direction angle in aspect-correct (height-normalised) space instead of 0..1 uv; a front_curvature knob blends the
  straight front into a radial iris about (center_x, center_y) that keeps a slight lean along the direction (so
  direction_deg stays live in iris mode), with the front's travel renormalised to the real
  min/max over the frame so it never idles; the zoom is exponential, scale = exp(-zoom * ln4 * w) with w the local
  mix weight (the originals' (1 - m) / m factors collapse to a point at the ends; at zoom 1 the midpoint is the
  same 2x magnification), so zoom_amount spans 0.1 (a subtle 15% swell; 0 would be the plain wipe plug-in and kill the centre/camera
  knobs) to 1.5 (8x rush) without flips; the zoom
  origin is the centre knob; camera_mix (new) spreads the push from the edge band to the whole frame, so at 0
  pixels outside the band copy a/b exactly, as in the originals; y-down pixel frames.)

Geometry: d(x) in [0, 1] is the order in which pixels are reached: (1 - c) * proj + c * (dist + 0.35 * proj), with
proj the projection on the direction and dist the distance to the centre (each 0..1 over the frame), renormalised
over the frame and clamped.  The front e = progress * (1 + soft) and the local mix
m = 1 - smoothstep(e - soft, e, d) (directionalwarp's formula).  Inside the band the old picture is sampled at
C + (q - C) * exp(-Z m) (magnified, rushing toward the viewer) and the new one at C + (q - C) * exp(-Z (1 - m))
(arrives magnified and settles to 1:1).  A straight front crosses the frame centre at progress 0.5, so a binder
can land the centre crossing on a downbeat.  direction_deg: 0 = the front travels left -> right, 90 = top ->
bottom (y-down, like the wipe plug-in), 180 = right -> left (crosswarp), 225 = up-left (directionalwarp default).

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
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num

_SRC = HELPERS + r"""
extern "C" __global__
void warp_sweep(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                const int W, const int H, const float e, const float soft,
                const float ux, const float uy, const float lin_lo, const float lin_inv,
                const float cxn, const float cyn, const float rad_inv, const float curv, const float lean,
                const float dmin, const float dinv, const float Z, const float P, const float cam)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const float invH = 1.f / H;
    const float px = (x + 0.5f) * invH, py = (y + 0.5f) * invH;          // height-normalised: circles stay round
    const float dl = (px * ux + py * uy - lin_lo) * lin_inv;            // straight front: projection, 0..1
    const float dx = px - cxn, dy = py - cyn;
    const float dr = sqrtf(dx * dx + dy * dy) * rad_inv;                // iris: distance to the centre, 0..1
    // reach order over the frame, 0..1: straight front blended into an iris that still leans along the direction
    const float d = clamp01(((1.f - curv) * dl + curv * (dr + lean * dl) - dmin) * dinv);
    const float m = 1.f - smooth(e - soft, e, d);                       // directionalwarp's edge
    // zoom weights: the local edge weight (directionalwarp) blended toward the global progress (a camera push)
    const float wa = m + (P - m) * cam, wb = (1.f - m) + (m - P) * cam;
    if (m <= 0.f && wa <= 0.f) { out[i] = a[i]; return; }              // untouched old picture
    if (m >= 1.f && wb <= 0.f) { out[i] = b[i]; return; }              // settled new picture
    const float Cx = cxn * H, Cy = cyn * H;                             // zoom origin in pixels
    const float qx = x + 0.5f - Cx, qy = y + 0.5f - Cy;
    float4 c;
    if (m <= 0.f) { const float sa = __expf(-Z * wa); c = bilin(a, Cx + qx * sa, Cy + qy * sa, W, H); }
    else if (m >= 1.f) { const float sb = __expf(-Z * wb); c = bilin(b, Cx + qx * sb, Cy + qy * sb, W, H); }
    else {
        const float sa = __expf(-Z * wa), sb = __expf(-Z * wb);         // old rushes in, new settles
        c = mix4(bilin(a, Cx + qx * sa, Cy + qy * sa, W, H), bilin(b, Cx + qx * sb, Cy + qy * sb, W, H), m);
    }
    out[i] = pack(c);
}
"""

_LN4 = math.log(4.0)
_LEAN = 0.35   # at front_curvature 1 the iris still leans along direction_deg (an eccentric, focus-centred ellipse)


class WarpSweep(Transition):
    name = "warp_sweep"
    label = "Warp sweep"
    description = ("A soft front sweeps across (or opens as an iris); the old picture rushes toward you while the "
                   "new one settles into place, like a camera move.")
    default_duration_s = 1.0
    params = {
        "direction_deg": num(0.0, 360.0, 180.0, "Direction", step=1.0, unit="°", tier="regional", basic=True),
        "edge_softness": num(0.02, 1.0, 0.4, "Edge softness", step=0.01, tier="regional"),
        "zoom_amount": num(0.1, 1.5, 1.0, "Zoom", step=0.01, tier="regional"),
        "front_curvature": num(0.0, 1.0, 0.0, "Front curvature (straight -> iris)", step=0.01, tier="regional"),
        "camera_mix": num(0.0, 1.0, 0.4, "Camera push (edge only -> whole frame)", step=0.01, tier="regional"),
        "center_x": num(0.0, 1.0, 0.5, "Centre x", step=0.01, tier="regional"),
        "center_y": num(0.0, 1.0, 0.5, "Centre y", step=0.01, tier="regional"),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        self._k = raw_module(_SRC).get_function("warp_sweep")
        self._geo_key = None
        self._geo = None

    def _geometry(self, w: int, h: int, ang: float, curv: float, cx: float, cy: float):
        """Host-side front geometry (cached: transition params are fixed while it runs)."""
        key = (w, h, ang, curv, cx, cy)
        if key == self._geo_key:
            return self._geo
        aspect = w / h
        ux, uy = math.cos(ang), math.sin(ang)
        corners = [(x, y) for x in (0.0, aspect) for y in (0.0, 1.0)]
        proj = [x * ux + y * uy for x, y in corners]
        lin_lo, span = min(proj), max(proj) - min(proj)
        cxn, cyn = cx * aspect, cy
        rmax = max(math.hypot(x - cxn, y - cyn) for x, y in corners)
        lin_inv, rad_inv = 1.0 / max(span, 1e-6), 1.0 / max(rmax, 1e-6)
        # the blended field is convex: max at a corner, min found on a coarse grid (plus the centre itself)
        gx, gy = np.meshgrid(np.linspace(0.0, aspect, 97), np.linspace(0.0, 1.0, 55))
        gx = np.append(gx.ravel(), min(max(cxn, 0.0), aspect))
        gy = np.append(gy.ravel(), min(max(cyn, 0.0), 1.0))
        dl = (gx * ux + gy * uy - lin_lo) * lin_inv
        dg = (1 - curv) * dl + curv * (np.hypot(gx - cxn, gy - cyn) * rad_inv + _LEAN * dl)
        dmin, dmax = float(dg.min()), float(dg.max())
        # the grid may overestimate the true min by ~1%: the kernel clamps d to [0, 1] so nothing pre-mixes at p=0+
        self._geo = (ux, uy, lin_lo, lin_inv, cxn, cyn, rad_inv, dmin, 1.0 / max(dmax - dmin, 1e-3))
        self._geo_key = key
        return self._geo

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        curv = float(min(max(p["front_curvature"], 0.0), 1.0))
        ux, uy, lin_lo, lin_inv, cxn, cyn, rad_inv, dmin, dinv = self._geometry(
            w, h, math.radians(p["direction_deg"]), curv, float(p["center_x"]), float(p["center_y"]))
        soft = float(max(p["edge_softness"], 0.005))
        e = float(progress) * (1.0 + soft)
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(e), np.float32(soft),
                   np.float32(ux), np.float32(uy), np.float32(lin_lo), np.float32(lin_inv),
                   np.float32(cxn), np.float32(cyn), np.float32(rad_inv), np.float32(curv), np.float32(_LEAN),
                   np.float32(dmin), np.float32(dinv), np.float32(p["zoom_amount"] * _LN4),
                   np.float32(progress), np.float32(min(max(p["camera_mix"], 0.0), 1.0))))
