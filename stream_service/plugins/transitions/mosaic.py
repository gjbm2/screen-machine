"""Mosaic: the camera pulls back to reveal a wall of tiles -- copies of the old and new scenes, some turned by
90-degree steps -- pans across it, and dives into one of the new-scene tiles, which becomes the full screen.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/Mosaic.glsl
Author: Xaychru (ported to gl-transitions by gre, from https://gist.github.com/Xaychru/130bb7b7affedbda9df5)
License: MIT   (file header says "// License: MIT"; repo LICENSE: "MIT License, Copyright (c) 2017-present
                gl-transitions contributors", https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: kept the original's camera -- zoom z = 1 at both ends rising
  parabolically to `pull_back` (3 in the original) at the midpoint, and a pan from the start tile to the end tile
  eased by CosInterpolation(progress)^2 -- and its random choice of old/new scene and 90-degree turn per tile.
  Changes: the integer endx/endy uniforms become a continuous `pan_angle_deg` + `pan_distance` (rounded to the
  nearest tile; the defaults reproduce the original endx = 2, endy = -1); the share of new-scene tiles is
  `b_ratio` (0.5 in the original) and the share of turned tiles `tile_rotation` (0.75 effective in the original);
  turned tiles rotate in aspect-correct space and cover-fit (the original rotated in 0..1 uv, which squashes a
  16:9 picture anamorphically at 90/270 degrees); the start tile and end tile are forced to the old / new scene
  unturned (the original relies on Rand(0, 0) = 0); fract(sin()) is replaced by hash1 with a seed; mod is
  x - floor(x); pulled-back tiles are minified with a 4-tap rotated-grid box filter so they do not shimmer;
  new: palette-tinted grout lines (`grout`) and a bevel + per-tile shade (`relief`) that make the wall read as
  physical tiles, both faded in with the pull-back so they vanish as the camera lands; endpoints are forced on
  the host (progress <= 0 copies a, >= 1 copies b).)

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

Binder: style lively, 4 beats starting on a downbeat.
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
// Sample the source picture at aspect-space coords s (picture spans x in +-ratio/2, y in +-0.5), with a
// 4-tap rotated-grid box filter when the pixel footprint `foot` (in source pixels) is large.
__device__ __forceinline__ float4 tap(const uchar4* __restrict__ img, float sx, float sy, float foot, int W, int H) {
    const float X = sx * H + 0.5f * W, Y = sy * H + 0.5f * H;
    if (foot < 1.6f) return bilin(img, X, Y, W, H);
    const float o1 = 0.125f * foot, o3 = 0.375f * foot;
    const float4 c0 = bilin(img, X + o1, Y + o3, W, H), c1 = bilin(img, X - o3, Y + o1, W, H);
    const float4 c2 = bilin(img, X + o3, Y - o1, W, H), c3 = bilin(img, X - o1, Y - o3, W, H);
    return make_float4(0.25f * (c0.x + c1.x + c2.x + c3.x), 0.25f * (c0.y + c1.y + c2.y + c3.y),
                       0.25f * (c0.z + c1.z + c2.z + c3.z), 255.f);
}

extern "C" __global__
void mosaic(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
            const float* __restrict__ P, const int W, const int H,
            const float z, const float cx, const float cy, const int ex, const int ey,
            const float ratio, const float b_ratio, const float rot_p,
            const float grout, const float relief, const float g, const float pal_off, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    // wall coordinates: tile (i, j) spans [i, i+1] x [j, j+1]; y down
    const float rx = ((x + 0.5f) / W - 0.5f) * z + cx;
    const float ry = ((y + 0.5f) / H - 0.5f) * z + cy;
    const float tx = floorf(rx), ty = floorf(ry);
    const float fx = rx - tx, fy = ry - ty;                       // mod(rp, 1) as x - floor(x)
    const int ix = (int)tx, iy = (int)ty;
    const unsigned int ux = (unsigned int)(ix + 65536), uy = (unsigned int)(iy + 65536);
    const bool is_start = (ix == 0 && iy == 0), is_end = (ix == ex && iy == ey);
    const float hb = hash1(ux, uy, seed), hr = hash1(ux, uy, seed ^ 0x51ED27u);
    const float hk = hash1(ux, uy, seed + 7u), hs = hash1(ux, uy, seed + 13u);
    const bool use_b = is_end || (!is_start && hb < b_ratio);
    int k = 0;
    if (!is_start && !is_end && hr < rot_p) k = 1 + min((int)(hk * 3.f), 2);

    // tile-local aspect space, then the tile's quarter turn (odd turns cover-fit the rotated picture)
    const float qx = (fx - 0.5f) * ratio, qy = fy - 0.5f;
    float sx, sy, sc = 1.f;
    if (k == 0)      { sx = qx;  sy = qy; }
    else if (k == 2) { sx = -qx; sy = -qy; }
    else {
        sc = fminf(ratio, 1.f / ratio);
        if (k == 1) { sx = -qy * sc; sy = qx * sc; }
        else        { sx = qy * sc;  sy = -qx * sc; }
    }
    const float foot = z * sc;                                    // source pixels per screen pixel
    float4 c = use_b ? tap(b, sx, sy, foot, W, H) : tap(a, sx, sy, foot, W, H);

    if (g > 0.f) {
        // distance to this tile's edge in screen pixels, and which edge is nearest (for the bevel light)
        const float dxp = fminf(fx, 1.f - fx) * W / z, dyp = fminf(fy, 1.f - fy) * H / z;
        const float d = fminf(dxp, dyp);
        const bool lit = (dxp < dyp) ? (fx < 0.5f) : (fy < 0.5f);   // top / left edges catch the light
        const float tile_h = H / z;
        // bevel + per-tile shade
        if (relief > 0.f) {
            const float gh0 = 0.5f * grout * tile_h * g;
            const float bw = fmaxf(0.07f * tile_h, 2.f);
            const float edge = 1.f - smooth(0.f, bw, d - gh0);
            const float shade = 1.f + relief * g * ((lit ? 0.30f : -0.32f) * edge + 0.18f * (hs - 0.5f));
            c.x *= shade; c.y *= shade; c.z *= shade;
        }
        // grout: half the width on each side of the seam, anti-aliased over ~1 px
        const float gh = 0.5f * grout * tile_h * g;
        if (gh > 0.f) {
            const float on = clamp01(gh * 2.f);
            const float cov = 1.f - on * (1.f - clamp01(d - gh + 0.5f));
            if (cov < 1.f) {
                const float3 pc = pal(pal_off + 0.06f * (rx + 0.6f * ry), P);
                const float4 gc = make_float4(pc.x * 150.f + 10.f, pc.y * 150.f + 10.f, pc.z * 150.f + 12.f, 255.f);
                c = mix4(gc, c, cov);
            }
        }
    }
    out[y * W + x] = pack(c);
}
"""


class Mosaic(Transition):
    name = "mosaic"
    label = "Mosaic"
    description = ("The camera pulls back to a wall of randomly turned tiles of the old and new scenes, pans "
                   "across it and dives into a new-scene tile.")
    default_duration_s = 2.0
    params = {
        "pull_back": num(2.0, 6.0, 3.0, "Pull back", step=0.05, unit="×", tier="regional", basic=True),
        "pan_distance": num(1.0, 4.0, 2.24, "Pan distance", step=0.05, unit="tiles", tier="regional"),
        "pan_angle_deg": num(-180.0, 180.0, -26.57, "Pan direction", step=1.0, unit="°", tier="regional"),
        "tile_rotation": num(0.0, 1.0, 0.75, "Turned tiles", tier="regional"),
        "b_ratio": num(0.2, 0.8, 0.5, "New-scene tiles", tier="regional"),
        "grout": num(0.0, 0.12, 0.035, "Grout width", step=0.005, tier="regional"),
        "relief": num(0.0, 1.0, 0.5, "Tile relief", tier="regional"),
        "palette": enum(list(PALETTES), "dusk", "Grout palette", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng(None if s < 0 else s).integers(0, 2**31 - 1))
        self._k = raw_module(_SRC).get_function("mosaic")

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        if p["palette"] != self._pal:
            self._P.set(np.asarray(palette_coeffs(p["palette"]), np.float32))
            self._pal = p["palette"]
        w, h = self.ctx.width, self.ctx.height
        t = float(progress)
        zmax = min(max(float(p["pull_back"]), 1.0), 8.0)
        r = 2.0 * t - 1.0
        g = 1.0 - r * r                                   # 0 at both ends, 1 at the midpoint
        z = 1.0 + (zmax - 1.0) * g
        # end tile from a continuous direction + distance (screen y down; positive angle pans up)
        dist = min(max(float(p["pan_distance"]), 1.0), 6.0)
        ang = math.radians(float(p["pan_angle_deg"]))
        ex = int(math.floor(dist * math.cos(ang) + 0.5))
        ey = -int(math.floor(dist * math.sin(ang) + 0.5))
        if ex == 0 and ey == 0:
            ex = 1
        ci = 0.5 - 0.5 * math.cos(t * math.pi)            # CosInterpolation
        s = ci * ci
        cx = 0.5 + ex * s
        cy = 0.5 + ey * s
        pal_off = (self._seed % 997) / 997.0 + 0.4 * t    # grout colour drifts along the transition
        launch_2d(self._k, w, h,
                  (a, b, out, self._P, np.int32(w), np.int32(h),
                   np.float32(z), np.float32(cx), np.float32(cy), np.int32(ex), np.int32(ey),
                   np.float32(w / h), np.float32(p["b_ratio"]), np.float32(p["tile_rotation"]),
                   np.float32(p["grout"]), np.float32(p["relief"]), np.float32(g), np.float32(pal_off),
                   np.uint32(self._seed & 0xFFFFFFFF)))
