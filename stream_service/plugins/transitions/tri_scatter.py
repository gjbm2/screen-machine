"""Triangle scatter: the old picture shatters into thousands of triangles that stream away along staggered 3D
Bezier paths, spinning and shrinking, while the new picture assembles from triangles flying in from the other side.

Source: https://github.com/zadvorsky/three.bas/tree/master/examples/image_transition
        https://github.com/zadvorsky/three.bas/blob/master/LICENCE
Author: Szenia Zadvornykh (three.bas image_transition)
License: MIT   (repo LICENCE: "The MIT License (MIT) / Copyright (c) 2016 Szenia Zadvornykh")
license_class: permissive
Ported from three.js/GLSL by smfx (ideas/changes: the example's per-face animation (a delay mapped from the face
  centroid's x plus |y| (edges leave first, the centre arrives first), a random duration and stretch, a cubic Bezier
  between control points taken from its getControlPoint0/1 ranges, ease-in-out-cubic, and the face scaled by
  1 - t on the way out / t on the way in, mirrored control points for the incoming slide) is rebuilt as a small CUDA
  rasteriser.  Each picture is cut into a jittered triangle grid (triangle_density columns, random diagonals);
  pass 1 (one thread per triangle) moves, spins (Rodrigues about a random axis) and perspective-projects each
  triangle in flight; pass 2 (one block per triangle) scatters (depth << 16 | id) with atomicMin into a full-res id
  buffer, testing exact edge functions; pass 3 (per pixel) resolves the nearest of the flying triangle and the
  analytic "at rest" triangle at that pixel (a precomputed home-id map, so triangles that have not left or have
  already landed cost nothing), samples a or b through the winner's barycentric -> home mapping, and clears the
  buffer for the next frame.  Added: the out-path keeps going (a streaming end point instead of looping back home),
  depth (z) control points with a perspective camera, flat Lambert shading and a specular glint from the spinning
  normal (both exactly neutral at rest), a palette-coloured rim glow on shards in flight, and a dim palette
  backdrop where the frame is open.)

Endpoints: p <= 0 copies a, p >= 1 copies b; in between, every triangle's local time is clamp((tau - delay) / dur)
with tau = p * T_end and T_end the analytic upper bound of delay + duration, so at p = 0 every a-triangle is home
and at p = 1 every b-triangle is home (continuous at both ends).

MIT notice (three.bas): Permission is hereby granted, free of charge, to any person obtaining a copy of this
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
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_ST = 24          # static floats per triangle (home verts, centroid, randoms)
_DY = 16          # per-frame floats per triangle (local time, projected verts, key, shading)
_EMPTY = 0xFFFFFFFF
_MAX_ROWS = 128

_SRC = HELPERS + PALETTE_CUDA + r"""
#define S_ST 24
#define S_DY 16
#define EMPTY 0xFFFFFFFFu

__device__ __forceinline__ float ease_io(float t) {
    if (t < 0.5f) return 4.f * t * t * t;
    const float u = 2.f - 2.f * t;
    return 1.f - 0.5f * u * u * u;
}
__device__ __forceinline__ float bez(float p1, float p2, float p3, float t) {     // p0 = 0
    const float u = 1.f - t;
    return 3.f * u * u * t * p1 + 3.f * u * t * t * p2 + t * t * t * p3;
}

// Once per setup: which triangle of the jittered grid owns each pixel (at rest).
extern "C" __global__
void tri_home(const float* __restrict__ st, unsigned short* __restrict__ home, const int W, const int H,
              const int N, const int M)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const int ci = min((int)(px * N / W), N - 1), cj = min((int)(py * M / H), M - 1);
    float best = -1e30f;
    int bi = 2 * (cj * N + ci);
    for (int dj = -1; dj <= 1; ++dj) {
        const int j = cj + dj;
        if (j < 0 || j >= M) continue;
        for (int di = -1; di <= 1; ++di) {
            const int i = ci + di;
            if (i < 0 || i >= N) continue;
            for (int k = 0; k < 2; ++k) {
                const int id = 2 * (j * N + i) + k;
                const float* S = st + id * S_ST;
                const float X0 = S[0], Y0 = S[1], X1 = S[2], Y1 = S[3], X2 = S[4], Y2 = S[5];
                const float area = (X1 - X0) * (Y2 - Y0) - (Y1 - Y0) * (X2 - X0);
                if (fabsf(area) < 1e-6f) continue;
                const float inv = 1.f / area;
                const float l0 = ((X2 - X1) * (py - Y1) - (Y2 - Y1) * (px - X1)) * inv;
                const float l1 = ((X0 - X2) * (py - Y2) - (Y0 - Y2) * (px - X2)) * inv;
                const float l2 = ((X1 - X0) * (py - Y0) - (Y1 - Y0) * (px - X0)) * inv;
                const float m = fminf(l0, fminf(l1, l2));
                if (m > best) { best = m; bi = id; }
            }
        }
    }
    home[y * W + x] = (unsigned short)bi;
}

// Pass 1: one thread per triangle.  ids 0..T-1 are b (incoming), T..2T-1 are a (outgoing): b wins depth ties.
extern "C" __global__
void tri_update(const float* __restrict__ st, float* __restrict__ dy, const int T, const int W, const int H,
                const float tau, const float lag, const float dbase, const float stag_x, const float stag_y,
                const float spread_px, const float depth_px, const float spin, const float glow,
                const float focal, const float zmin, const float zspan,
                const float lx, const float ly, const float lz, const float hx, const float hy, const float hz,
                const float spec_base, const float spec_norm)
{
    const int id = blockIdx.x * blockDim.x + threadIdx.x;
    if (id >= 2 * T) return;
    const float* S = st + id * S_ST;
    float* D = dy + id * S_DY;
    const bool is_a = id >= T;
    const float cx = S[6], cy = S[7];
    const float xn = cx / W, yn = fabsf(cy - 0.5f * H) / (0.5f * H);
    const float dur = dbase * (0.8f + 0.4f * S[8]);
    float delay = stag_x * xn + 0.15f * dbase * S[9];
    delay += is_a ? stag_y * (1.f - yn) : lag + stag_y * yn;       // out: edges first; in: centre first
    const float t = ease_io(clamp01((tau - delay) / dur));
    D[0] = t;
    const float q = is_a ? t : 1.f - t;                               // 0 = home, 1 = far end of the path
    if (q <= 0.f || q >= 1.f) { D[1] = 0.f; return; }                 // at rest (analytic) or vanished

    // path offsets (pixels), after three.bas getControlPoint0/1, plus an end point that streams away
    const float sgy = (cy >= 0.5f * H) ? 1.f : -1.f;
    const float sg = is_a ? 1.f : -1.f;                               // the incoming slide mirrors its path
    const float o0x = (0.08f + 0.17f * S[10]) * spread_px, o0y = sgy * (0.12f + 0.23f * S[11]) * spread_px;
    const float o1x = (0.25f + 0.25f * S[13]) * spread_px, o1y = -sgy * (0.35f + 0.35f * S[14]) * spread_px;
    const float o3x = (0.45f + 0.60f * S[16]) * spread_px, o3y = sgy * (0.15f + 0.45f * S[17]) * spread_px;
    const float o0z = (2.f * S[12] - 1.f) * 0.35f * depth_px;
    const float o1z = (2.f * S[15] - 1.f) * 0.35f * depth_px;
    const float o3z = (1.4f * S[18] - 0.9f) * depth_px;
    const float px = cx + sg * bez(o0x, o1x, o3x, q);
    const float py = cy + sg * bez(o0y, o1y, o3y, q);
    const float pz = fmaxf(sg * bez(o0z, o1z, o3z, q), zmin);

    // spin about a random axis, shrink towards the far end
    float sn, cs;
    sincosf(spin * 6.2831853f * S[22] * q, &sn, &cs);
    const float kx = S[19], ky = S[20], kz = S[21];
    const float sc = 1.f - q * q;
    const float hW = 0.5f * W, hH = 0.5f * H;
    float X[3], Y[3];
    #pragma unroll
    for (int k = 0; k < 3; ++k) {
        const float vx = (S[2 * k] - cx) * sc, vy = (S[2 * k + 1] - cy) * sc;
        const float kv = kx * vx + ky * vy;
        const float rx = vx * cs - kz * vy * sn + kx * kv * (1.f - cs);
        const float ry = vy * cs + kz * vx * sn + ky * kv * (1.f - cs);
        const float rz = (kx * vy - ky * vx) * sn + kz * kv * (1.f - cs);
        const float pr = focal / fmaxf(focal + pz + rz, 0.35f * focal);
        X[k] = hW + (px + rx - hW) * pr;
        Y[k] = hH + (py + ry - hH) * pr;
    }
    const float area = (X[1] - X[0]) * (Y[2] - Y[0]) - (Y[1] - Y[0]) * (X[2] - X[0]);
    const float mnx = fminf(X[0], fminf(X[1], X[2])), mxx = fmaxf(X[0], fmaxf(X[1], X[2]));
    const float mny = fminf(Y[0], fminf(Y[1], Y[2])), mxy = fmaxf(Y[0], fmaxf(Y[1], Y[2]));
    const bool vis = fabsf(area) > 0.5f && mxx >= 0.f && mnx < W && mxy >= 0.f && mny < H;
    D[1] = vis ? 1.f : 0.f;
    if (!vis) return;
    D[2] = X[0]; D[3] = Y[0]; D[4] = X[1]; D[5] = Y[1]; D[6] = X[2]; D[7] = Y[2];
    D[8] = 1.f / area;
    const unsigned zq = (unsigned)(clamp01((pz - zmin) / zspan) * 65534.f);
    ((unsigned int*)D)[9] = (zq << 16) | (unsigned)id;

    // flat shading from the spun normal: exactly 1 at rest (normal = +z), two-sided
    const float nx = ky * sn + kx * kz * (1.f - cs);
    const float ny = -kx * sn + ky * kz * (1.f - cs);
    const float nz = cs + kz * kz * (1.f - cs);
    const float ndl = fabsf(nx * lx + ny * ly + nz * lz);
    D[10] = fminf(fmaxf(ndl / fabsf(lz), 0.3f), 1.35f);
    const float ndh = fabsf(nx * hx + ny * hy + nz * hz);
    D[11] = fmaxf(__powf(ndh, 48.f) - spec_base, 0.f) * spec_norm;
    D[12] = glow * 4.f * q * (1.f - q);
    D[13] = S[23];
}

// Pass 2: one block per triangle, scatter the nearest (depth, id) key over the covered pixels.
extern "C" __global__
void tri_raster(const float* __restrict__ dy, unsigned int* __restrict__ ids, const int W, const int H)
{
    const float* D = dy + blockIdx.x * S_DY;
    if (D[1] == 0.f) return;
    const float X0 = D[2], Y0 = D[3], X1 = D[4], Y1 = D[5], X2 = D[6], Y2 = D[7], inv = D[8];
    const unsigned key = __float_as_uint(D[9]);
    const int x0 = (int)floorf(fmaxf(fminf(X0, fminf(X1, X2)), 0.f));
    const int x1 = (int)ceilf(fminf(fmaxf(X0, fmaxf(X1, X2)), (float)(W - 1)));
    const int y0 = (int)floorf(fmaxf(fminf(Y0, fminf(Y1, Y2)), 0.f));
    const int y1 = (int)ceilf(fminf(fmaxf(Y0, fmaxf(Y1, Y2)), (float)(H - 1)));
    for (int y = y0 + threadIdx.y; y <= y1; y += blockDim.y) {
        const float py = y + 0.5f;
        for (int x = x0 + threadIdx.x; x <= x1; x += blockDim.x) {
            const float px = x + 0.5f;
            const float l0 = ((X2 - X1) * (py - Y1) - (Y2 - Y1) * (px - X1)) * inv;
            const float l1 = ((X0 - X2) * (py - Y2) - (Y0 - Y2) * (px - X2)) * inv;
            const float l2 = ((X1 - X0) * (py - Y0) - (Y1 - Y0) * (px - X0)) * inv;
            if (l0 >= 0.f && l1 >= 0.f && l2 >= 0.f) {
                unsigned int* dst = ids + y * W + x;
                if (*dst > key) atomicMin(dst, key);
            }
        }
    }
}

// Pass 3: per pixel, nearest of (flying key, a at rest, b at rest); sample through the winner; clear the buffer.
extern "C" __global__
void tri_resolve(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                 unsigned int* __restrict__ ids, const unsigned short* __restrict__ home,
                 const float* __restrict__ st, const float* __restrict__ dy, const float* __restrict__ P,
                 const int W, const int H, const int T, const unsigned int d0, const float p,
                 const float backdrop, const float drift, const float rim_w, const float tint, const float hue_span)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const unsigned int k = ids[i];
    ids[i] = EMPTY;
    const int h = home[i];
    const float ta = dy[(T + h) * S_DY], tb = dy[h * S_DY];
    const unsigned int ka = (ta <= 0.f) ? ((d0 << 16) | (unsigned)(T + h)) : EMPTY;
    const unsigned int kb = (tb >= 1.f) ? ((d0 << 16) | (unsigned)h) : EMPTY;
    const unsigned int best = min(k, min(ka, kb));
    if (best == EMPTY) {                                              // open frame: a dim palette backdrop
        const float u = (x + 0.5f) / W, v = (y + 0.5f) / H;
        const float dx = u - 0.5f, dv = v - 0.5f;
        const float vig = 1.f - clamp01(2.2f * (dx * dx + dv * dv));
        const float3 bc = pal(0.45f * v + 0.2f * u + drift + 0.5f, P);
        const float g = 255.f * backdrop * (0.25f + 0.75f * vig) * 0.55f;
        const float4 ghost = mix4(ld(a, x, y, W, H), ld(b, x, y, W, H), p);
        const float gk = 0.12f * backdrop;
        out[i] = pack(make_float4(bc.x * g + ghost.x * gk, bc.y * g + ghost.y * gk, bc.z * g + ghost.z * gk, 255.f));
        return;
    }
    if (best == kb) { out[i] = b[i]; return; }
    if (best == ka) { out[i] = a[i]; return; }
    const int id = (int)(best & 0xFFFFu);
    const float* D = dy + id * S_DY;
    const float* S = st + id * S_ST;
    const float X0 = D[2], Y0 = D[3], X1 = D[4], Y1 = D[5], X2 = D[6], Y2 = D[7], inv = D[8];
    const float px = x + 0.5f, py = y + 0.5f;
    const float l0 = ((X2 - X1) * (py - Y1) - (Y2 - Y1) * (px - X1)) * inv;
    const float l1 = ((X0 - X2) * (py - Y2) - (Y0 - Y2) * (px - X2)) * inv;
    const float l2 = 1.f - l0 - l1;
    const float su = l0 * S[0] + l1 * S[2] + l2 * S[4];
    const float sv = l0 * S[1] + l1 * S[3] + l2 * S[5];
    float4 c = bilin(id >= T ? a : b, su, sv, W, H);
    // rim: distance (px) to the nearest edge = barycentric * altitude
    const float aa = 1.f / fabsf(inv);                                // |2 * area|
    const float e0 = sqrtf((X2 - X1) * (X2 - X1) + (Y2 - Y1) * (Y2 - Y1)) + 1e-4f;
    const float e1 = sqrtf((X0 - X2) * (X0 - X2) + (Y0 - Y2) * (Y0 - Y2)) + 1e-4f;
    const float e2 = sqrtf((X1 - X0) * (X1 - X0) + (Y1 - Y0) * (Y1 - Y0)) + 1e-4f;
    const float dmin = fmaxf(fminf(fminf(l0 * aa / e0, l1 * aa / e1), l2 * aa / e2), 0.f);
    const float rim = __expf(-dmin / rim_w);
    const float g = D[12];
    const float3 pc = pal(D[13] * hue_span + drift, P);
    const float sh = D[10], spc = 190.f * D[11];
    const float ga = 255.f * g * (1.1f * rim + tint);
    c = make_float4(c.x * sh + spc + pc.x * ga, c.y * sh + spc + pc.y * ga, c.z * sh + spc * 0.96f + pc.z * ga, 255.f);
    out[i] = pack(c);
}
"""


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v)


_L = _norm((-0.40, -0.55, -0.73))                    # key light, upper left, in front (camera looks +z)
_HV = _norm((_L[0], _L[1], _L[2] - 1.0))              # half vector with the viewer at -z
_SPEC_BASE = abs(_HV[2]) ** 48                        # the flat (resting) shard gives nothing
_SPEC_NORM = 1.0 / (1.0 - _SPEC_BASE)


class TriScatter(Transition):
    name = "tri_scatter"
    label = "Triangle scatter"
    description = ("The old picture shatters into thousands of spinning triangles that stream away along staggered "
                   "3D Bezier paths while the new one assembles from triangles flying in.")
    default_duration_s = 3.0
    params = {
        "path_spread": num(0.0, 2.0, 1.0, "Path spread", tier="regional", basic=True),
        "stagger_x": num(0.0, 1.5, 0.9, "Sweep stagger (left to right)", tier="regional"),
        "stagger_y": num(0.0, 0.6, 0.15, "Vertical stagger (edges out, centre in)", tier="regional"),
        "depth_spread": num(0.0, 1.0, 0.5, "Depth spread", tier="regional"),
        "time_stretch": num(0.0, 1.0, 0.5, "Flight length", tier="regional"),
        "spin": num(0.0, 1.0, 0.4, "Spin", tier="regional", basic=True),
        "glow": num(0.0, 1.0, 0.55, "Shard rim glow", tier="regional"),
        "backdrop": num(0.0, 1.0, 0.35, "Backdrop light", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Glow palette", tier="regional"),
        "triangle_density": num(16, 64, 32, "Triangle density", unit="columns", tier="structural", integer=True),
        "jitter": num(0.0, 1.0, 0.7, "Shard irregularity", tier="structural"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._k_home = mod.get_function("tri_home")
        self._k_upd = mod.get_function("tri_update")
        self._k_ras = mod.get_function("tri_raster")
        self._k_res = mod.get_function("tri_resolve")
        w, h = ctx.width, ctx.height
        self._ids = cp.full(w * h, _EMPTY, dtype=cp.uint32)
        self._home = cp.zeros(w * h, dtype=cp.uint16)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._geo_key = None
        self._st = None
        self._dy = None
        self._build()

    # ------------------------------------------------------------------ triangulation (structural)
    def _build(self) -> None:
        p = self.params
        cp = self.ctx.cp
        w, h = self.ctx.width, self.ctx.height
        n = int(min(max(int(p["triangle_density"]), 16), 64))
        m = int(min(max(round(n * h / w), 1), _MAX_ROWS))
        jit = float(min(max(p["jitter"], 0.0), 1.0))
        key = (n, m, jit, self._seed, w, h)
        if key == self._geo_key:
            return
        rng = np.random.default_rng(self._seed)
        cw, ch = w / n, h / m
        gx, gy = np.meshgrid(np.arange(n + 1, dtype=np.float64) * cw, np.arange(m + 1, dtype=np.float64) * ch)
        amp = 0.3 * jit
        jx = rng.uniform(-amp, amp, gx.shape) * cw
        jy = rng.uniform(-amp, amp, gy.shape) * ch
        jx[:, 0] = jx[:, -1] = 0.0                               # left/right edges slide only vertically
        jy[0, :] = jy[-1, :] = 0.0                               # top/bottom edges slide only horizontally
        vx, vy = gx + jx, gy + jy
        t = 2 * n * m
        verts = np.zeros((t, 6), np.float64)
        diag = rng.integers(0, 2, (m, n))
        k = 0
        for j in range(m):
            for i in range(n):
                v00 = (vx[j, i], vy[j, i]); v10 = (vx[j, i + 1], vy[j, i + 1])
                v01 = (vx[j + 1, i], vy[j + 1, i]); v11 = (vx[j + 1, i + 1], vy[j + 1, i + 1])
                if diag[j, i]:
                    tris = ((v00, v10, v11), (v00, v11, v01))
                else:
                    tris = ((v00, v10, v01), (v10, v11, v01))
                for tri in tris:
                    verts[k] = (*tri[0], *tri[1], *tri[2])
                    k += 1
        st = np.zeros((2 * t, _ST), np.float32)
        for half in range(2):                                    # rows 0..t-1: b (incoming), t..2t-1: a
            blk = st[half * t:(half + 1) * t]
            blk[:, 0:6] = verts
            blk[:, 6] = (verts[:, 0] + verts[:, 2] + verts[:, 4]) / 3.0
            blk[:, 7] = (verts[:, 1] + verts[:, 3] + verts[:, 5]) / 3.0
            blk[:, 8:19] = rng.uniform(0.0, 1.0, (t, 11))
            ax = rng.normal(size=(t, 3))
            ax /= np.linalg.norm(ax, axis=1, keepdims=True) + 1e-9
            blk[:, 19:22] = ax
            blk[:, 22] = rng.uniform(0.5, 1.5, t) * rng.choice((-1.0, 1.0), t)
            blk[:, 23] = rng.uniform(0.0, 1.0, t)
        self._st = cp.asarray(st.ravel())
        self._dy = cp.zeros(2 * t * _DY, cp.float32)
        self._t = t
        launch_2d(self._k_home, w, h, (self._st, self._home, np.int32(w), np.int32(h), np.int32(n), np.int32(m)))
        self._geo_key = key

    def _sync_palette(self) -> None:
        name = self.params["palette"]
        if name != self._pal:
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
        self._build()
        self._sync_palette()
        w, h = self.ctx.width, self.ctx.height
        t = self._t
        stag_x = float(p["stagger_x"])
        stag_y = float(p["stagger_y"])
        dbase = 0.35 + 1.25 * float(p["time_stretch"])
        lag = 0.3 * dbase
        t_end = lag + stag_x + stag_y + 0.15 * dbase + 1.2 * dbase + 1e-4
        tau = float(progress) * t_end
        focal = 1.6 * h
        zmin = -0.55 * focal
        zspan = -zmin + 2.0 * h
        d0 = int(min(max((0.0 - zmin) / zspan, 0.0), 1.0) * 65534.0)
        n2 = 2 * t
        self._k_upd(((n2 + 127) // 128, 1, 1), (128, 1, 1),
                    (self._st, self._dy, np.int32(t), np.int32(w), np.int32(h), np.float32(tau), np.float32(lag),
                     np.float32(dbase), np.float32(stag_x), np.float32(stag_y),
                     np.float32(float(p["path_spread"]) * h), np.float32(float(p["depth_spread"]) * h),
                     np.float32(p["spin"]), np.float32(p["glow"]), np.float32(focal), np.float32(zmin),
                     np.float32(zspan), np.float32(_L[0]), np.float32(_L[1]), np.float32(_L[2]),
                     np.float32(_HV[0]), np.float32(_HV[1]), np.float32(_HV[2]),
                     np.float32(_SPEC_BASE), np.float32(_SPEC_NORM)))
        self._k_ras((n2, 1, 1), (16, 8, 1), (self._dy, self._ids, np.int32(w), np.int32(h)))
        drift = 0.07 * float(progress) * self.duration_s + (self._seed % 1000) / 1000.0
        launch_2d(self._k_res, w, h,
                  (a, b, out, self._ids, self._home, self._st, self._dy, self._P, np.int32(w), np.int32(h),
                   np.int32(t), np.uint32(d0), np.float32(progress), np.float32(p["backdrop"]),
                   np.float32(drift), np.float32(max(2.0, 3.0 * h / 2160.0)), np.float32(0.12),
                   np.float32(0.6)))
