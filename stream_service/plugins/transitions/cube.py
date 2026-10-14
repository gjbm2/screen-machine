"""Cube: the classic VJ cube.  The two scenes are faces of a 3D cube that turns away from the viewer; the cube
pulls back (unzooms) at mid-turn to float over a glossy, palette-tinted floor that carries a soft reflection of
both faces, then pushes back in until the new scene fills the frame.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/cube.glsl
Author: gre (Gaetan Renaudeau)
License: MIT   (file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the xskew fake-perspective face mapping, the triangular unzoom
  `unzoom * 2 (0.5 - |0.5 - progress|)`, the from-over-to face priority and the floor reflection
  `project(p) = p * (1, -1.2) - (0, floating / 100)` weighted `reflection * (1 - y)` are kept.  Changes: the
  original's `persp` uniform (1 = flat) becomes `perspective` (0 = flat slide, 1 = strong turn; the skew is
  capped at 0.95 so the far edge never collapses to a degenerate point); the per-face skews are folded to
  k_from = 0.95 perspective progress and k_to = 0.95 perspective (1 - progress^2); frames are y-down pixel arrays,
  so the uv math runs in y-up 0..1 coordinates as in the GLSL; the divides by progress and (1 - progress) are
  guarded; face edges are anti-aliased from finite-difference face-coordinate gradients (the to-face extends
  one pixel under the shared cube edge so the seam never shows background); the black background becomes a
  palette-tinted backdrop (`palette`, `floor_tint`, drifting with progress): a soft glow behind the cube, a
  floor brightest at the cube's base with a faint horizon sheen; the reflection gets a 2-tap glossy blur that
  widens with distance and a soft fade-in under the gap; faces get turn shading (`shading`); `axis` adds a
  vertical tumble and `reverse` flips the turn direction; endpoints are forced (progress <= 0 copies a,
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

Binder: style lively, 2 beats, starting on a downbeat (the mid-turn pull-back peaks at progress 0.5).
"""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_SRC = HELPERS + PALETTE_CUDA + r"""
// Screen pixel (sx, sy) -> face coords of the from face (F) and the to face (T), canonical GLSL space (y-up 0..1).
__device__ __forceinline__ void cube_map(float sx, float sy, float iW, float iH, int vert, int rev,
                                         float prog, float uz, float kF, float kT, float2& F, float2& T)
{
    const float us = sx * iW, vs = 1.f - sy * iH;
    float qx = vert ? vs : us;
    const float qy = vert ? us : vs;
    if (rev) qx = 1.f - qx;
    const float px = -0.5f * uz + (1.f + uz) * qx, py = -0.5f * uz + (1.f + uz) * qy;
    // from face: xskew((p - (progress, 0)) / (1 - progress, 1), 1 - kF, center 0)
    const float X = (px - prog) / fmaxf(1.f - prog, 1e-5f);
    F.x = X;
    F.y = (py - 0.5f * kF * X) / fmaxf(1.f - kF * X, 1e-4f);
    // to face: xskew(p / (progress, 1), 1 - kT, center 1)
    const float U = px / fmaxf(prog, 1e-5f), xt = 1.f - U;
    T.x = U;
    T.y = (py - 0.5f * kT * xt) / fmaxf(1.f - kT * xt, 1e-4f);
}

// canonical face coords -> image uv (y-up)
__device__ __forceinline__ float2 to_img(float fx, float fy, int vert, int rev) {
    if (rev) fx = 1.f - fx;
    return vert ? make_float2(fy, fx) : make_float2(fx, fy);
}

__device__ __forceinline__ float4 scale4(float4 c, float s) { return make_float4(c.x * s, c.y * s, c.z * s, 255.f); }

// Glossy floor reflection of one face (image space), mixed into bg.
__device__ __forceinline__ void reflect_face(const uchar4* __restrict__ img, float2 iuv, float lat, float sh,
                                            float refl, float flt, int W, int H, float4& bg)
{
    const float ry = -1.2f * iuv.y - flt;
    if (ry <= 0.f || ry >= 1.f || lat <= 0.f) return;
    const float w = refl * (1.f - ry) * smooth(0.f, 0.025f, ry) * lat;
    if (w <= 0.f) return;
    const float bl = 0.002f + 0.03f * ry;                       // gloss: blur widens away from the face
    const float fx = iuv.x * W;
    const float4 c0 = bilin(img, fx, (1.f - (ry - bl)) * H, W, H);
    const float4 c1 = bilin(img, fx, (1.f - (ry + bl)) * H, W, H);
    const float4 c = scale4(mix4(c0, c1, 0.5f), sh);
    bg = mix4(bg, c, w);
}

extern "C" __global__
void cube(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
          const int W, const int H, const float prog, const float uz, const float kF, const float kT,
          const float refl, const float flt, const float shade, const float light, const float tint,
          const int vert, const int rev, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float iW = 1.f / (float)W, iH = 1.f / (float)H;
    const float sx = x + 0.5f, sy = y + 0.5f;
    float2 F0, T0, F1, T1, F2, T2;
    cube_map(sx, sy, iW, iH, vert, rev, prog, uz, kF, kT, F0, T0);
    cube_map(sx + 1.f, sy, iW, iH, vert, rev, prog, uz, kF, kT, F1, T1);
    cube_map(sx, sy + 1.f, iW, iH, vert, rev, prog, uz, kF, kT, F2, T2);
    // per-pixel footprint of each face coordinate (finite differences) -> analytic edge coverage
    const float gFx = fmaxf(hypotf(F1.x - F0.x, F2.x - F0.x), 1e-7f);
    const float gFy = fmaxf(hypotf(F1.y - F0.y, F2.y - F0.y), 1e-7f);
    const float gTx = fmaxf(hypotf(T1.x - T0.x, T2.x - T0.x), 1e-7f);
    const float gTy = fmaxf(hypotf(T1.y - T0.y, T2.y - T0.y), 1e-7f);
    const float cxF = clamp01(fminf(F0.x, 1.f - F0.x) / gFx + 0.5f);
    const float cyF = clamp01(fminf(F0.y, 1.f - F0.y) / gFy + 0.5f);
    const float cxT = clamp01(fminf(T0.x / gTx + 0.5f, (1.f - T0.x) / gTx + 1.5f));   // seam side tucked under
    const float cyT = clamp01(fminf(T0.y, 1.f - T0.y) / gTy + 0.5f);
    const float covF = cxF * cyF, covT = cxT * cyT;

    // turn shading: each face darkens as it turns away, more toward its far edge
    const float sF = 1.f - shade * prog * (0.45f + 0.35f * clamp01(F0.x));
    const float sT = 1.f - shade * (1.f - prog) * (0.45f + 0.35f * clamp01(1.f - T0.x));

    float4 col;
    if (covF >= 1.f) {
        const float2 fi = to_img(F0.x, F0.y, vert, rev);
        out[y * W + x] = pack(scale4(bilin(a, fi.x * W, (1.f - fi.y) * H, W, H), sF));
        return;
    }
    if (covT >= 1.f) {
        const float2 ti = to_img(T0.x, T0.y, vert, rev);
        col = scale4(bilin(b, ti.x * W, (1.f - ti.y) * H, W, H), sT);
    } else {
        // backdrop: palette-tinted glow behind the cube, a floor brightest at the cube's base, horizon sheen
        const float us = sx * iW, vs = 1.f - sy * iH;
        const float vh = 0.5f * uz / (1.f + uz);
        const float du = (us - 0.5f) * ((float)W * iH), dv = vs - 0.55f;
        const float3 pc = pal(tint + 0.22f * vs + 0.10f * fabsf(du), P);
        const float wall = 0.07f + 0.48f * __expf(-(du * du * 1.6f + dv * dv * 4.f));
        const float tf = clamp01((vh - vs) / fmaxf(vh, 0.02f));
        const float flo = 0.03f + 0.36f * (1.f - tf) * (1.f - tf);
        const float m = smooth(-0.004f, 0.004f, vs - vh);
        const float hz = (vs - vh) * (1.f / 0.012f);
        float lum = flo + (wall - flo) * m + 0.16f * smooth(0.f, 0.06f, uz) * __expf(-hz * hz);
        lum *= light * (1.f - 0.3f * clamp01(du * du));
        float4 bg = make_float4(255.f * pc.x * lum, 255.f * pc.y * lum, 255.f * pc.z * lum, 255.f);
        if (refl > 0.f) {
            reflect_face(b, to_img(T0.x, T0.y, vert, rev), vert ? cyT : cxT, sT, refl, flt, W, H, bg);
            reflect_face(a, to_img(F0.x, F0.y, vert, rev), vert ? cyF : cxF, sF, refl, flt, W, H, bg);
        }
        col = bg;
        if (covT > 0.f) {
            const float2 ti = to_img(T0.x, T0.y, vert, rev);
            col = mix4(col, scale4(bilin(b, ti.x * W, (1.f - ti.y) * H, W, H), sT), covT);
        }
    }
    if (covF > 0.f) {
        const float2 fi = to_img(F0.x, F0.y, vert, rev);
        col = mix4(col, scale4(bilin(a, fi.x * W, (1.f - fi.y) * H, W, H), sF), covF);
    }
    out[y * W + x] = pack(col);
}
"""


class Cube(Transition):
    name = "cube"
    label = "Cube"
    description = ("The two scenes are faces of a 3D cube that turns away and back, pulling back mid-turn to float "
                   "over a glossy, palette-tinted floor with a soft reflection.")
    default_duration_s = 1.2
    params = {
        "perspective": num(0.0, 1.0, 0.55, "Perspective (0 = flat slide)", tier="regional"),
        "unzoom": num(0.0, 0.8, 0.35, "Pull-back", tier="regional", basic=True),
        "reflection": num(0.0, 0.8, 0.45, "Floor reflection", tier="regional"),
        "floating": num(0.0, 10.0, 3.0, "Float height", tier="regional"),
        "shading": num(0.0, 1.0, 0.5, "Turn shading", tier="regional"),
        "floor_light": num(0.0, 1.0, 0.7, "Backdrop light", tier="regional"),
        "floor_tint": num(0.0, 1.0, 0.6, "Backdrop tint (palette index)", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Backdrop palette", tier="regional"),
        "axis": enum(["horizontal", "vertical"], "horizontal", "Turn axis", tier="structural"),
        "reverse": {"type": "boolean", "default": False, "x-ui": {"label": "Reverse direction"},
                    "x-tier": "structural"},
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("cube")
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
        persp = 0.95 * min(max(float(p["perspective"]), 0.0), 1.0)
        uz = float(p["unzoom"]) * (1.0 - abs(1.0 - 2.0 * t))   # unzoom * 2 (0.5 - |0.5 - progress|)
        kF = persp * t
        kT = persp * (1.0 - t * t)
        launch_2d(self._k, w, h,
                  (a, b, out, np.int32(w), np.int32(h), np.float32(t), np.float32(uz), np.float32(kF),
                   np.float32(kT), np.float32(p["reflection"]), np.float32(p["floating"] / 100.0),
                   np.float32(p["shading"]), np.float32(p["floor_light"]),
                   np.float32(p["floor_tint"] + 0.3 * t),                 # backdrop colour drifts through the turn
                   np.int32(1 if p["axis"] == "vertical" else 0), np.int32(1 if p["reverse"] else 0), self._P))
