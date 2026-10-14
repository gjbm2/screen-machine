"""smfx splat toolkit: GPU rendering of 3D POINTS, LINES, WIREFRAMES, POLYLINES/TRAILS and RIBBONS without OpenGL,
by pinhole projection + additive splatting into a float32 HDR accumulator, then glow + tonemap + sRGB resolve.
Also: particle integrators (strange attractors, curl-noise advection) and host shape generators.

Source: Original
Author: smfx
License: MIT
license_class: permissive
(Techniques are textbook: pinhole projection; additive splatting with screen-tile binning (count / scan / scatter
/ shared-memory accumulate, as in tile-based software rasterisers); column-stepped anti-aliased lines in the spirit
of Wu / Gupta-Sproull with a normalised perpendicular profile; Liang-Barsky clipping; gaussian glow; curl noise
(Bridson et al., SIGGRAPH 2007); analytic value-noise derivatives (iq's formula, written from scratch); attractor
ODEs as published: Lorenz 1963, Aizawa, Thomas 1999, Halvorsen, Dadras 2009, Rossler 1976, Chen 1999, Wang-Sun
four-wing, Sprott 2014; ACES fit by K. Narkowicz.)

QUICK START (a scene)
---------------------
    from stream_service.plugins._lib.splat3d import Splat3D, look_at, orbit, mat_euler, polyhedron
    from stream_service.plugins._lib.upsample import internal_size

    def setup(self, ctx, params):
        super().setup(ctx, params)
        w, h = internal_size(ctx, params["render_scale"])        # accumulate at 0.5 scale ...
        self.sp = Splat3D(ctx, w, h, max_points=1_000_000, max_lines=4096)   # ... resolve() writes 4K directly
        pts = np.random.default_rng(1).normal(size=(1_000_000, 4)).astype(np.float32); pts[:, 3] = 1
        self.pos = ctx.cp.asarray(pts)                             # (N,4): xyz + per-point weight
        V, E = polyhedron("icosahedron")
        self.V, self.E = ctx.cp.asarray(V), ctx.cp.asarray(E)

    def render(self, t, dt, out):
        p = self.step_params(dt)
        cam = look_at(orbit((0, 0, 0), 6.0, self.yaw, 0.35), (0, 0, 0), fov_deg=50)
        sp = self.sp
        sp.clear()
        sp.points(self.pos, cam=cam, intensity=0.05, size_px=1.5, palette=p["palette"], drift=self.drift,
                  weight_in_w=True, far_fade=(8, 14))
        sp.wire(self.V, self.E, cam=cam, model=mat_euler(0.3 * t, 0.5 * t, 0), colour=(1, .5, .2),
                intensity=2.0, width_px=2.0)
        sp.resolve(out, exposure=1.2 * p["intensity"], glow=0.6, tonemap="aces")   # out: any size (4K ok)

UNITS (resolution independent)
------------------------------
* Sizes and widths are in "ref px" = pixels of a 1080-line frame, whatever the accumulator size; the toolkit
  scales them by s = acc_h / 1080, so a look is identical at render_scale 0.5 or 1 and at 4K or 540p output.
* ``size_px`` / ``width_px`` ~ the footprint's full width at half maximum (separable biweight (1 - d^2/rho^2)^2,
  support rho = size, clamped to >= 1 accumulator px for anti-aliasing and <= 8 px).
* Points: ``intensity`` = the point's total energy (a bigger point is softer, not brighter).  A 1M-point cloud
  wants ~0.01-0.1 per point.  Lines: ``intensity`` = energy per ref px of screen length (angle and resolution
  independent); 1.0 is a clearly visible thin line.
* Perspective: sizes/widths scale by depth_ref / z (default depth_ref = the camera's eye->target distance), then
  clamp to ``size_range`` (ref px).  depth_ref=0: constant screen size.
* Accumulator ``acc``: float32 planes (C, h, w) = linear-light R, G, B (+ coverage = deposited luminance when
  alpha=True).  ``resolve`` tonemaps it.

CAMERA (13-float layout and conventions identical to sdf3d.camera_pack: y up, vertical fov; splats line up exactly
with an sdf3d raymarched backdrop rendered with the same eye/target/fov into a buffer of the same size)
    cam = look_at(eye, target, up=(0,1,0), fov_deg=50, roll=0, near=0.05)  -> Cam (pure Python, cheap)
    orbit(target, radius, yaw, pitch) -> eye  (yaw about +y, 0 = on the -z side looking +z; pitch up)
    cam.pack() -> 13 floats (sdf3d cam_load);  cam.project(xyz, w, h) -> (sx, sy, depth) on the host

MODEL MATRICES (3x4 numpy, applied in-kernel: free rotation / instancing of static device geometry)
    mat_identity() mat_rot(axis, angle) mat_euler(rx, ry, rz) mat_scale(s | (sx,sy,sz)) mat_translate(xyz)
    mat_mul(a, b) (a after b)  mat_trs(t, r=None, s=1)

Splat3D(ctx, w, h, max_points=1<<20, max_lines=1<<14, *, alpha=False, pool=None)
    clear()                       start a frame (zero the accumulator)
    fade(k)                       start a frame keeping acc * k (persistence / long-exposure trails; k=decay**(dt*60))
    points(pos, *, cam, n=None, model=None, colour=(1,1,1), intensity=1, size_px=1.5, size_range=(0.6, 12),
           depth_ref=None, rgb=None, index=None, weight=None, sizes=None, weight_in_w=False,
           palette=None, period=1, drift=0, near_fade=None, far_fade=None, fog=0)
        pos (N,3)|(N,4) float32 device array (4th float = per-point weight when weight_in_w).  Optional per-point
        rgb (N,3) linear colour, index (N,) palette index, weight (N,), sizes (N,) size multiplier.
        colour: sRGB tint (values > 1 allowed = HDR).  palette: name | 12 coeffs -> pal(index*period + drift)
        (or pal(drift) without index).  near_fade=(z0,z1): 0 at z<=z0 -> 1 at z>=z1; far_fade=(z0,z1): 1 -> 0;
        fog: exp2 density on depth.
    lines(p0, p1, **style)        N independent segments p0[i] -> p1[i]; attributes per segment
    wire(verts, edges, **style)   indexed: edges (E,2) int32 into verts (V,3|4); attributes per vertex
    polylines(verts, k=None, *, head=0, tail_fade=False, **style)
                                  strips of K vertices (verts (M,K,3|4) or flat (M*K,.) with k=K); head rotates a
                                  ring buffer (trails: oldest vertex); tail_fade ramps weight 0 -> 1 old -> new
    ribbons(a, b, strands=8, *, edge=2.0, **style)
                                  ruled sheets between rails a, b (M,K,3|4): ``strands`` lerped lines per ribbon,
                                  the two edge strands x ``edge``; attributes per rail-a vertex
        line style = the points keywords with width_px (default 1.5) instead of size_px, plus n (segment count)
    flush()                       accumulate pending binned items (resolve() does it; call before reading acc)
    resolve(out, *, exposure=1, tonemap="aces"|"exp"|"reinhard", saturation=1, glow=0, glow_radius_px=24,
            glow_wide=0.5, glow_threshold=0, background=None | (r,g,b) sRGB | uint8 frame, occlusion=0,
            blend="screen"|"add", dither=True)
        Writes ``out`` (any size, e.g. the 4K output: the accumulator is sampled bilinearly).  glow = bloom at
        glow_radius_px plus a 4x wider halo x glow_wide.  background is composited UNDER the light (linear
        screen/add); ``out`` may itself be the background (splats over a frame already in ``out``).
        occlusion (alpha=True only): background *= exp(-occlusion * coverage).
    Cost model: every point / line pixel-column becomes a 16-byte record binned into 32x32 tiles and accumulated
    in shared memory (fast for any distribution).  Footprints wider than the 4 px tile halo (points rho > 4 px,
    lines wider than ~2.5-3.5 accumulator px) and pool overflow are splatted directly with global atomics:
    correct but much slower; keep big bokeh points to a few thousand.

INTEGRATORS (device arrays (N,4) float32 = xyz + w, in place, no allocation, no cupy.random)
    sim = ParticleSim(ctx)
    sim.attractor(pos, name, dt, *, steps=4, method="rk2", params=None, seed=1, frame=0, fade_in=0.05)
        Advance native-coordinate states on ATTRACTORS[name]: lorenz aizawa thomas halvorsen dadras rossler chen
        four_wing sprott.  ATTRACTORS[name]["dt"] is a stable step and ["rate"] native time per second for a
        pleasant flow: steps = ceil(rate * speed * dt_frame / dt).  w ramps 0 -> 1 by fade_in per call; diverged
        or NaN states respawn in the seed box with w = 0.  seed_attractor(name, n, seed) -> (n,4) numpy states
        already on the attractor.  Draw with model=attractor_model(name, rot, scale, translate) (centred, ~unit
        radius).
    sim.curl(pos, dt, *, scale=1, strength=1, offset=(0,0,0), seed=1, octaves=2, method="rk2", steps=1,
             centre=(0,0,0), pull=0, swirl=0, radius=0, frame=0, fade_in=0.05)
        Curl-noise advection (divergence free) + pull towards centre + swirl about the +y axis through it;
        radius > 0 respawns particles that leave the sphere (w = 0, then fades in).  Move ``offset`` slowly
        (host-integrated) to evolve the field.
    sim.trail_push(trails (M,K,4), src (M,4), slot) / trail_fill(trails, src): ring-buffer trails; draw with
        splat.polylines(trails, head=(slot + 1) % K, tail_fade=True).
    Device code (prepend SPLAT3D_SRC; compile with SPLAT3D_OPTIONS) for fused scene kernels:
        float3 sp_attr_f(int kind, float3 p, const float* k6)   float3 sp_attr_step(kind, p, k6, dt, method 0|1|2)
        float4 sp_vnoise_d(float3 p, unsigned seed)  value noise 0..1 + analytic gradient (yzw)
        float3 sp_curl(float3 p, unsigned seed, int octaves)    divergence-free curl-noise velocity ~[-1, 1]
        float3 sp_hash3(unsigned i, unsigned seed)              uniform [0,1)^3
        void sp_splat(float* acc, int W, int H, float sx, float sy, float rho, float3 c, float cov, bool alpha)
             direct additive biweight footprint into the planar accumulator (global atomics: for a few items)

SHAPES (host numpy; upload once in setup)
    polyhedron(name) -> (V (n,3) float32 on the unit sphere, E (m,2) int32): tetrahedron cube octahedron
        dodecahedron icosahedron cuboctahedron icosidodecahedron stella
    geodesic(freq) -> (V, E)   torus_knot(p, q, n, R, r) -> (n,3)   ring(n, r) -> (n+1,3) closed
    fibonacci_sphere(n) -> (n,3)   grid_lines(nx, nz, size, y) -> (p0, p1)
    galaxy_disc(n, arms, seed, radius, pitch_deg, arm_spread, thickness, bulge) -> (n,4) xyz + weight

Performance (P4000, 1920x1080 accumulator): see bench() (pipelined clear + 1M points + 20k segments + glow + 4K
resolve).
"""
from __future__ import annotations

import math
from typing import Iterable, Optional, Sequence

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import launch_2d
from stream_service.plugins._lib.palettes import PALETTES

SPLAT3D_OPTIONS: tuple[str, ...] = ("--std=c++14", "--use_fast_math")
REF_H = 1080.0
SP_MAX_R = 8.0          # max point radius / line half-support in accumulator px
TILE = 32               # binning tile (px): SP_TILE
HALO = 4                # tile halo (px): SP_HALO
CHUNK = 2048            # items per tile block: SP_CH
PPT = 4                 # points per thread in the bin kernel: SP_PPT

# ----------------------------------------------------------------------------------------------- device code
SPLAT3D_SRC = r"""
// ==================================================================== splat3d (smfx/_lib/splat3d.py)
#ifndef SPLAT3D_INCLUDED
#define SPLAT3D_INCLUDED
#define SP_F_RGB    1
#define SP_F_IDX    2
#define SP_F_WT     4
#define SP_F_PAL    8
#define SP_F_ALPHA  16
#define SP_F_POS4W  32
#define SP_F_SIZE   64
#define SP_F_TAIL   128
#define SP_MAX_R    %(maxr)ff

struct SpDraw {
    float mv[12];          // model-view 3x4 (rows: right, up, forward)
    float pal[12];         // cosine palette a b c d
    float tint[3];         // linear rgb multiplier
    float period, drift, energy, size, depth_ref, rmin, rmax, near_z;
    float fn0, fn1, ff0, ff1, fog, edge, focal, cx, cy, cov;
    int W, H, n, stride0, stride1, mode, K, S, flags, head;
};

__device__ __forceinline__ float sp_clamp01(float v) { return fminf(fmaxf(v, 0.f), 1.f); }
__device__ __forceinline__ float sp_smooth(float e0, float e1, float x) {
    const float t = sp_clamp01((x - e0) / (e1 - e0)); return t * t * (3.f - 2.f * t);
}
__device__ __forceinline__ float3 sp_f3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 sp_add3(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 sp_sub3(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 sp_mul3(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 sp_mix3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
__device__ __forceinline__ float sp_dot3(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 sp_cross3(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float sp_lum(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
__device__ __forceinline__ float sp_lin(float c) {   // sRGB-encoded 0..1 -> linear
    return c <= 0.04045f ? c * (1.f / 12.92f) : __powf((c + 0.055f) * (1.f / 1.055f), 2.4f);
}
__device__ __forceinline__ float sp_srgb(float c) {  // linear 0..1 -> sRGB-encoded
    c = sp_clamp01(c);
    return c <= 0.0031308f ? c * 12.92f : 1.055f * __powf(c, 1.f / 2.4f) - 0.055f;
}
__device__ __forceinline__ unsigned int sp_hash(unsigned int x) {
    x ^= x >> 16; x *= 0x7feb352dU; x ^= x >> 15; x *= 0x846ca68bU; x ^= x >> 16; return x;
}
__device__ __forceinline__ float sp_hashf(unsigned int a, unsigned int b) {
    return (sp_hash(a * 0x9E3779B1U ^ sp_hash(b + 0x27d4eb2dU)) >> 8) * (1.f / 16777216.f);
}
__device__ __forceinline__ float3 sp_hash3(unsigned int i, unsigned int seed) {
    const unsigned int h = sp_hash(i * 0x9E3779B1U ^ sp_hash(seed + 0x27d4eb2dU));
    const unsigned int h2 = sp_hash(h + 0x632BE5ABU), h3 = sp_hash(h2 + 0x85EBCA77U);
    return make_float3((h >> 8) * (1.f / 16777216.f), (h2 >> 8) * (1.f / 16777216.f), (h3 >> 8) * (1.f / 16777216.f));
}

// ---- draw helpers (the SpDraw is a kernel argument passed by value)
__device__ __forceinline__ float3 sp_mv(const SpDraw& d, float x, float y, float z) {
    return make_float3(d.mv[0] * x + d.mv[1] * y + d.mv[2] * z + d.mv[3],
                       d.mv[4] * x + d.mv[5] * y + d.mv[6] * z + d.mv[7],
                       d.mv[8] * x + d.mv[9] * y + d.mv[10] * z + d.mv[11]);
}
__device__ __forceinline__ float sp_fade(const SpDraw& d, float z) {
    float f = 1.f;
    if (d.fn1 > d.fn0) f *= sp_smooth(d.fn0, d.fn1, z);
    if (d.ff1 > d.ff0) f *= 1.f - sp_smooth(d.ff0, d.ff1, z);
    if (d.fog > 0.f) { const float k = d.fog * z; f *= __expf(-k * k); }
    return f;
}
__device__ __forceinline__ float3 sp_pal(const SpDraw& d, float t) {
    const float k = 6.2831853f;
    return make_float3(sp_lin(sp_clamp01(d.pal[0] + d.pal[3] * __cosf(k * (d.pal[6] * t + d.pal[9])))),
                       sp_lin(sp_clamp01(d.pal[1] + d.pal[4] * __cosf(k * (d.pal[7] * t + d.pal[10])))),
                       sp_lin(sp_clamp01(d.pal[2] + d.pal[5] * __cosf(k * (d.pal[8] * t + d.pal[11])))));
}
// colour (linear, before energy) of element / vertex i
__device__ __forceinline__ float3 sp_col(const SpDraw& d, int i, const float* __restrict__ rgb,
                                         const float* __restrict__ idx, const float* __restrict__ wt) {
    float3 c = make_float3(d.tint[0], d.tint[1], d.tint[2]);
    if (d.flags & SP_F_RGB) { c.x *= rgb[3 * i]; c.y *= rgb[3 * i + 1]; c.z *= rgb[3 * i + 2]; }
    if (d.flags & SP_F_PAL) {
        const float3 p = sp_pal(d, ((d.flags & SP_F_IDX) ? idx[i] * d.period : 0.f) + d.drift);
        c.x *= p.x; c.y *= p.y; c.z *= p.z;
    }
    if (d.flags & SP_F_WT) c = sp_mul3(c, wt[i]);
    return c;
}
__device__ __forceinline__ float4 sp_load(const float* __restrict__ P, int i, int stride) {
    if (stride == 4) return reinterpret_cast<const float4*>(P)[i];
    const float* q = P + (size_t)i * stride;
    return make_float4(q[0], q[1], q[2], 1.f);
}

// ---- direct additive deposits into the PLANAR accumulator acc[c * W * H + y * W + x] (global atomics)
__device__ __forceinline__ void sp_put(float* __restrict__ acc, int W, int H, int x, int y, float3 c, float a, bool alpha) {
    const size_t plane = (size_t)W * H;
    float* p = acc + (size_t)y * W + x;
    atomicAdd(p, c.x); atomicAdd(p + plane, c.y); atomicAdd(p + 2 * plane, c.z);
    if (alpha) atomicAdd(p + 3 * plane, a);
}
// separable biweight (1 - (d/rho)^2)^2, support rho (accum px, >= 1), energy-conserving; (sx, sy) continuous
// pixel coords (pixel centres at i + 0.5); c = energy * colour; cov goes to the coverage plane if alpha.
__device__ void sp_splat(float* __restrict__ acc, int W, int H, float sx, float sy, float rho,
                         float3 c, float cov, bool alpha) {
    rho = fminf(fmaxf(rho, 1.f), SP_MAX_R);
    const float fx = sx - 0.5f, fy = sy - 0.5f, ir = 1.f / rho;
    const int ux0 = (int)ceilf(fx - rho), ux1 = (int)floorf(fx + rho);
    const int uy0 = (int)ceilf(fy - rho), uy1 = (int)floorf(fy + rho);
    const int x0 = max(ux0, 0), x1 = min(ux1, W - 1), y0 = max(uy0, 0), y1 = min(uy1, H - 1);
    if (x0 > x1 || y0 > y1) return;
    float sxw = 0.f, syw = 0.f;                                   // normalise over the full (unclipped) window
    for (int i = ux0; i <= ux1; ++i) { float t = (i - fx) * ir; t = fmaxf(1.f - t * t, 0.f); sxw += t * t; }
    for (int j = uy0; j <= uy1; ++j) { float t = (j - fy) * ir; t = fmaxf(1.f - t * t, 0.f); syw += t * t; }
    const float inv = 1.f / fmaxf(sxw * syw, 1e-12f);
    for (int j = y0; j <= y1; ++j) {
        float ty = (j - fy) * ir; ty = 1.f - ty * ty;
        if (ty <= 0.f) continue;
        const float wy = ty * ty * inv;
        for (int i = x0; i <= x1; ++i) {
            float tx = (i - fx) * ir; tx = 1.f - tx * tx;
            if (tx <= 0.f) continue;
            const float w = tx * tx * wy;
            sp_put(acc, W, H, i, j, sp_mul3(c, w), cov * w, alpha);
        }
    }
}

// ---- attractors: dp/dt in native coordinates; k = 6 params
__device__ __forceinline__ float3 sp_attr_f(int kind, float3 p, const float* k) {
    const float x = p.x, y = p.y, z = p.z;
    switch (kind) {
    case 0:  // Lorenz: sigma, rho, beta
        return make_float3(k[0] * (y - x), x * (k[1] - z) - y, x * y - k[2] * z);
    case 1:  // Aizawa: a b c d e f
        return make_float3((z - k[1]) * x - k[3] * y, k[3] * x + (z - k[1]) * y,
                           k[2] + k[0] * z - z * z * z * (1.f / 3.f) - (x * x + y * y) * (1.f + k[4] * z) + k[5] * z * x * x * x);
    case 2:  // Thomas: b
        return make_float3(__sinf(y) - k[0] * x, __sinf(z) - k[0] * y, __sinf(x) - k[0] * z);
    case 3:  // Halvorsen: a
        return make_float3(-k[0] * x - 4.f * y - 4.f * z - y * y, -k[0] * y - 4.f * z - 4.f * x - z * z,
                           -k[0] * z - 4.f * x - 4.f * y - x * x);
    case 4:  // Dadras: a b c d e
        return make_float3(y - k[0] * x + k[1] * y * z, k[2] * y - x * z + z, k[3] * x * y - k[4] * z);
    case 5:  // Rossler: a b c
        return make_float3(-y - z, x + k[0] * y, k[1] + z * (x - k[2]));
    case 6:  // Chen: a b c
        return make_float3(k[0] * (y - x), (k[2] - k[0]) * x - x * z + k[2] * y, x * y - k[1] * z);
    case 7:  // Wang-Sun four-wing: a b c
        return make_float3(k[0] * x + y * z, k[1] * x + k[2] * y - x * z, -z - x * y);
    default: // Sprott (2014): a b
        return make_float3(y + k[0] * x * y + x * z, 1.f - k[1] * x * x + y * z, x - x * x - y * y);
    }
}
__device__ __forceinline__ float3 sp_attr_step(int kind, float3 p, const float* k, float dt, int method) {
    const float3 k1 = sp_attr_f(kind, p, k);
    if (method == 0) return sp_add3(p, sp_mul3(k1, dt));
    const float3 k2 = sp_attr_f(kind, sp_add3(p, sp_mul3(k1, 0.5f * dt)), k);
    if (method == 1) return sp_add3(p, sp_mul3(k2, dt));
    const float3 k3 = sp_attr_f(kind, sp_add3(p, sp_mul3(k2, 0.5f * dt)), k);
    const float3 k4 = sp_attr_f(kind, sp_add3(p, sp_mul3(k3, dt)), k);
    return sp_add3(p, sp_mul3(sp_add3(sp_add3(k1, k4), sp_mul3(sp_add3(k2, k3), 2.f)), dt * (1.f / 6.f)));
}

// ---- value noise with analytic gradient (quintic fade): returns (n in 0..1, dn/dx, dn/dy, dn/dz)
__device__ __forceinline__ float sp_lat(int x, int y, int z, unsigned int seed) {
    return (sp_hash((unsigned int)x * 0x8da6b343U ^ (unsigned int)y * 0xd8163841U ^ (unsigned int)z * 0xcb1ab31fU ^ seed) >> 8)
           * (1.f / 16777216.f);
}
__device__ float4 sp_vnoise_d(float3 p, unsigned int seed) {
    const float fx = floorf(p.x), fy = floorf(p.y), fz = floorf(p.z);
    const int ix = (int)fx, iy = (int)fy, iz = (int)fz;
    const float wx = p.x - fx, wy = p.y - fy, wz = p.z - fz;
    const float ux = wx * wx * wx * (wx * (wx * 6.f - 15.f) + 10.f), dux = 30.f * wx * wx * (wx * (wx - 2.f) + 1.f);
    const float uy = wy * wy * wy * (wy * (wy * 6.f - 15.f) + 10.f), duy = 30.f * wy * wy * (wy * (wy - 2.f) + 1.f);
    const float uz = wz * wz * wz * (wz * (wz * 6.f - 15.f) + 10.f), duz = 30.f * wz * wz * (wz * (wz - 2.f) + 1.f);
    const float a = sp_lat(ix, iy, iz, seed), b = sp_lat(ix + 1, iy, iz, seed);
    const float c = sp_lat(ix, iy + 1, iz, seed), d = sp_lat(ix + 1, iy + 1, iz, seed);
    const float e = sp_lat(ix, iy, iz + 1, seed), f = sp_lat(ix + 1, iy, iz + 1, seed);
    const float g = sp_lat(ix, iy + 1, iz + 1, seed), h = sp_lat(ix + 1, iy + 1, iz + 1, seed);
    const float k0 = a, k1 = b - a, k2 = c - a, k3 = e - a, k4 = a - b - c + d, k5 = a - c - e + g, k6 = a - b - e + f;
    const float k7 = -a + b + c - d + e - f - g + h;
    return make_float4(k0 + k1 * ux + k2 * uy + k3 * uz + k4 * ux * uy + k5 * uy * uz + k6 * uz * ux + k7 * ux * uy * uz,
                       dux * (k1 + k4 * uy + k6 * uz + k7 * uy * uz),
                       duy * (k2 + k5 * uz + k4 * ux + k7 * uz * ux),
                       duz * (k3 + k6 * ux + k5 * uy + k7 * ux * uy));
}
// divergence-free velocity: curl of a 3-component value-noise potential, octaves 1..4, ~[-1, 1] per component
__device__ float3 sp_curl(float3 p, unsigned int seed, int octaves) {
    float3 v = make_float3(0.f, 0.f, 0.f);
    float amp = 1.f, fr = 1.f;
    for (int o = 0; o < octaves && o < 4; ++o) {
        const float3 q = sp_mul3(p, fr);
        const float4 a = sp_vnoise_d(q, seed + 1013u * o);
        const float4 b = sp_vnoise_d(sp_add3(q, sp_f3(31.4f, -17.3f, 9.7f)), seed + 1013u * o + 7u);
        const float4 c = sp_vnoise_d(sp_add3(q, sp_f3(-11.9f, 23.1f, -41.3f)), seed + 1013u * o + 13u);
        // psi = (a, b, c): curl = (dc/dy - db/dz, da/dz - dc/dx, db/dx - da/dy)
        const float s = amp * fr * 0.35f;
        v.x += s * (c.z - b.w); v.y += s * (a.w - c.y); v.z += s * (b.y - a.z);
        amp *= 0.5f; fr *= 2.03f;
    }
    return v;
}
#endif
// ==================================================================== end splat3d
""" % {"maxr": SP_MAX_R}

_KERNELS = SPLAT3D_SRC + r"""
// =========================================================================== binned pipeline
// Every point / line pixel-column "fragment" becomes a 16-byte record {sx, sy, half2(r, g), half2(b, q)} (colour
// x 256 in fp16; q > 0: point radius; q < 0: line fragment, |q| = perpendicular scale cos/rho, mantissa LSB set
// for a y-major line).  Records are appended to a pool and ranked per 32x32 tile (K1), the tile counts scanned
// (K2), records scattered into tile order (K3), and each tile (+4 px halo) accumulated in shared memory and
// flushed once with coalesced atomics (K4).  Items wider than the halo, or beyond the pool, go direct.
#define SP_TILE 32
#define SP_HALO 4
#define SP_TS (SP_TILE + 2 * SP_HALO)
#define SP_CH 2048
#define SP_PPT 4
#define SP_CSCALE 256.f

__device__ __forceinline__ unsigned short sp_f2h(float f) {
    unsigned short h; asm("cvt.rn.f16.f32 %0, %1;" : "=h"(h) : "f"(f)); return h;
}
__device__ __forceinline__ float sp_h2f(unsigned short h) {
    float f; asm("cvt.f32.f16 %0, %1;" : "=f"(f) : "h"(h)); return f;
}
__device__ __forceinline__ uint4 sp_pack(float sx, float sy, float3 c, unsigned short qbits) {
    const unsigned short r = sp_f2h(fminf(c.x * SP_CSCALE, 65000.f)), g = sp_f2h(fminf(c.y * SP_CSCALE, 65000.f));
    const unsigned short b = sp_f2h(fminf(c.z * SP_CSCALE, 65000.f));
    return make_uint4(__float_as_uint(sx), __float_as_uint(sy), (unsigned int)r | ((unsigned int)g << 16),
                      (unsigned int)b | ((unsigned int)qbits << 16));
}
__device__ __forceinline__ float3 sp_unpack_c(uint4 r) {
    return make_float3(sp_h2f((unsigned short)(r.z & 0xffffu)) * (1.f / SP_CSCALE),
                       sp_h2f((unsigned short)(r.z >> 16)) * (1.f / SP_CSCALE),
                       sp_h2f((unsigned short)(r.w & 0xffffu)) * (1.f / SP_CSCALE));
}
__device__ __forceinline__ int sp_rec_tile(uint4 r, int ntx, int nty) {
    const int tx = min(max((int)floorf(__uint_as_float(r.x)) >> 5, 0), ntx - 1);
    const int ty = min(max((int)floorf(__uint_as_float(r.y)) >> 5, 0), nty - 1);
    return ty * ntx + tx;
}
// 1D perpendicular deposit of a line fragment straight into the accumulator (wide lines / pool overflow)
__device__ void sp_frag_direct(float* __restrict__ acc, int W, int H, float sx, float sy, float kp, bool ymaj,
                               float3 col, bool alpha) {
    const float hs = 1.f / kp;
    const float mn = ymaj ? sx : sy;
    const int c = (int)floorf(ymaj ? sy : sx);
    const int nmin = ymaj ? W : H, nmaj = ymaj ? H : W;
    if (c < 0 || c >= nmaj) return;
    const int n0 = (int)ceilf(mn - 0.5f - hs), n1 = (int)floorf(mn - 0.5f + hs);
    float sum = 0.f;
    for (int k = n0; k <= n1; ++k) { float q = (k + 0.5f - mn) * kp; q = fmaxf(1.f - q * q, 0.f); sum += q * q; }
    if (sum <= 0.f) return;
    const float sc = 1.f / sum, cov = sp_lum(col);
    for (int k = max(n0, 0); k <= min(n1, nmin - 1); ++k) {
        float q = (k + 0.5f - mn) * kp; q = 1.f - q * q;
        if (q <= 0.f) continue;
        const float w = q * q * sc;
        if (ymaj) sp_put(acc, W, H, k, c, sp_mul3(col, w), cov * w, alpha);
        else      sp_put(acc, W, H, c, k, sp_mul3(col, w), cov * w, alpha);
    }
}

// ---- K1 points: project, cull, colour; append to the pool + rank in tile, or splat directly
extern "C" __global__ void __launch_bounds__(256)
sp_points_bin(float* __restrict__ acc, const SpDraw d, const float* __restrict__ pos, const float* __restrict__ rgb,
              const float* __restrict__ idx, const float* __restrict__ wt, const float* __restrict__ sz,
              uint4* __restrict__ rec, int* __restrict__ off, int* __restrict__ ctr, const int ntx, const int nty,
              const int cap)
{
    __shared__ int s_n, s_base;
    if (threadIdx.x == 0) s_n = 0;
    __syncthreads();
    const bool alpha = (d.flags & SP_F_ALPHA) != 0;
    uint4 r[SP_PPT];
    int li[SP_PPT];
    #pragma unroll
    for (int k = 0; k < SP_PPT; ++k) {
        li[k] = -1;
        const int i = blockIdx.x * (blockDim.x * SP_PPT) + k * blockDim.x + threadIdx.x;
        if (i >= d.n) continue;
        const float4 q = sp_load(pos, i, d.stride0);
        float e = d.energy;
        if (d.flags & SP_F_POS4W) e *= q.w;
        const float3 v = sp_mv(d, q.x, q.y, q.z);
        if (!(v.z > d.near_z) || !(e > 0.f)) continue;            // behind the near plane, NaN, or no energy
        const float iz = 1.f / v.z;
        const float sx = d.cx + d.focal * v.x * iz, sy = d.cy - d.focal * v.y * iz;
        float rho = d.size * (d.depth_ref > 0.f ? d.depth_ref * iz : 1.f);
        if (d.flags & SP_F_SIZE) rho *= sz[i];
        rho = fminf(fmaxf(rho, d.rmin), d.rmax);
        if (!(sx > -rho && sy > -rho && sx < d.W + rho && sy < d.H + rho)) continue;
        e *= sp_fade(d, v.z);
        if (e < 1e-12f) continue;
        const float3 c = sp_mul3(sp_col(d, i, rgb, idx, wt), e);
        if (rho > (float)SP_HALO) { sp_splat(acc, d.W, d.H, sx, sy, rho, c, sp_lum(c), alpha); continue; }
        r[k] = sp_pack(sx, sy, c, sp_f2h(rho));
        li[k] = atomicAdd(&s_n, 1);
    }
    __syncthreads();
    if (threadIdx.x == 0) s_base = s_n > 0 ? atomicAdd(&ctr[ntx * nty], s_n) : 0;
    __syncthreads();
    const int base = s_base;
    #pragma unroll
    for (int k = 0; k < SP_PPT; ++k) {
        if (li[k] < 0) continue;
        const int slot = base + li[k];
        if (slot < cap) {
            rec[slot] = r[k];
            off[slot] = atomicAdd(&ctr[sp_rec_tile(r[k], ntx, nty)], 1);
        } else {                                                  // pool full: splat directly
            const float3 c = sp_unpack_c(r[k]);
            sp_splat(acc, d.W, d.H, __uint_as_float(r[k].x), __uint_as_float(r[k].y),
                     sp_h2f((unsigned short)(r[k].w >> 16)), c, sp_lum(c), alpha);
        }
    }
}

// ---- K1 lines: one warp per segment
// mode 0 pairs (P0[i] -> P1[i], attributes per segment), 1 strips of K vertices (ring head d.head),
// 2 indexed (E[2i], E[2i+1] into P0), 3 ribbon strands (S lerps between rails P0, P1 of K vertices).
// The projected (near- and screen-clipped) segment is stepped along its major axis, one pixel column per lane;
// each column carries energy * coverage / cos(theta) spread across a normalised perpendicular biweight profile.
__device__ __forceinline__ void sp_clip1(float p, float q, float& u0, float& u1) {
    if (fabsf(p) < 1e-12f) { if (q < 0.f) { u0 = 1.f; u1 = 0.f; } return; }
    const float r = q / p;
    if (p < 0.f) { if (r > u0) u0 = r; } else { if (r < u1) u1 = r; }
}
extern "C" __global__ void __launch_bounds__(128)
sp_lines_bin(float* __restrict__ acc, const SpDraw d, const float* __restrict__ P0, const float* __restrict__ P1,
             const int* __restrict__ E, const float* __restrict__ rgb, const float* __restrict__ idx,
             const float* __restrict__ wt, uint4* __restrict__ rec, int* __restrict__ off, int* __restrict__ ctr,
             const int ntx, const int nty, const int cap)
{
    const int seg = (int)((blockIdx.x * blockDim.x + threadIdx.x) >> 5);
    const int lane = threadIdx.x & 31;
    if (seg >= d.n) return;                                       // warp-uniform
    int aa, ab;                                                   // attribute ids of the two ends
    float4 A, B;
    float boost = 1.f, ka = 0.f, kb = 0.f;                       // ka/kb: tail-fade position 0..1 of each end
    if (d.mode == 0) {
        aa = ab = seg;
        A = sp_load(P0, seg, d.stride0); B = sp_load(P1, seg, d.stride1);
    } else if (d.mode == 1) {
        const int km = d.K - 1, strip = seg / km, k = seg - strip * km;
        int sa = d.head + k; if (sa >= d.K) sa -= d.K;
        int sb = sa + 1; if (sb >= d.K) sb -= d.K;
        aa = strip * d.K + sa; ab = strip * d.K + sb;
        A = sp_load(P0, aa, d.stride0); B = sp_load(P0, ab, d.stride0);
        ka = (float)k / km; kb = (float)(k + 1) / km;
    } else if (d.mode == 2) {
        aa = E[2 * seg]; ab = E[2 * seg + 1];
        A = sp_load(P0, aa, d.stride0); B = sp_load(P0, ab, d.stride0);
    } else {
        const int km = d.K - 1, per = d.S * km, m = seg / per, r = seg - m * per, s = r / km, k = r - s * km;
        aa = m * d.K + k; ab = aa + 1;
        const float u = d.S > 1 ? (float)s / (float)(d.S - 1) : 0.5f;
        const float4 a0 = sp_load(P0, aa, d.stride0), a1 = sp_load(P1, aa, d.stride1);
        const float4 b0 = sp_load(P0, ab, d.stride0), b1 = sp_load(P1, ab, d.stride1);
        A = make_float4(a0.x + (a1.x - a0.x) * u, a0.y + (a1.y - a0.y) * u, a0.z + (a1.z - a0.z) * u, a0.w + (a1.w - a0.w) * u);
        B = make_float4(b0.x + (b1.x - b0.x) * u, b0.y + (b1.y - b0.y) * u, b0.z + (b1.z - b0.z) * u, b0.w + (b1.w - b0.w) * u);
        if (s == 0 || s == d.S - 1) boost = d.edge;
    }
    float wa = 1.f, wb = 1.f;
    if (d.flags & SP_F_POS4W) { wa = A.w; wb = B.w; }
    if (d.flags & SP_F_TAIL) { wa *= ka; wb *= kb; }
    float3 va = sp_mv(d, A.x, A.y, A.z), vb = sp_mv(d, B.x, B.y, B.z);
    float ta = 0.f, tb = 1.f;                                     // near-plane clip (3D param along the segment)
    if (!(va.z >= d.near_z) && !(vb.z >= d.near_z)) return;      // both behind (or NaN)
    if (!(va.z >= d.near_z)) { const float t = (d.near_z - va.z) / (vb.z - va.z); va = sp_mix3(va, vb, t); ta = t; }
    else if (!(vb.z >= d.near_z)) { const float t = (d.near_z - va.z) / (vb.z - va.z); vb = sp_mix3(va, vb, t); tb = t; }
    const float iza = 1.f / va.z, izb = 1.f / vb.z;
    const float ax = d.cx + d.focal * va.x * iza, ay = d.cy - d.focal * va.y * iza;
    const float bx = d.cx + d.focal * vb.x * izb, by = d.cy - d.focal * vb.y * izb;
    if (!(fabsf(ax) + fabsf(ay) + fabsf(bx) + fabsf(by) < 1e8f)) return;
    const float M = d.rmax * 1.5f + 2.f;                          // screen clip against the grown buffer
    const float dx = bx - ax, dy = by - ay;
    float u0 = 0.f, u1 = 1.f;
    sp_clip1(-dx, ax + M, u0, u1); sp_clip1(dx, d.W + M - ax, u0, u1);
    sp_clip1(-dy, ay + M, u0, u1); sp_clip1(dy, d.H + M - ay, u0, u1);
    if (u0 >= u1) return;
    const bool xmaj = fabsf(dx) >= fabsf(dy);
    const float ma = xmaj ? ax : ay, dm = xmaj ? dx : dy;
    const float na = xmaj ? ay : ax, dn = xmaj ? dy : dx;
    if (fabsf(dm) < 1e-4f) return;                                // no length, no energy
    const float lo = ma + dm * (dm > 0.f ? u0 : u1), hi = ma + dm * (dm > 0.f ? u1 : u0);
    const float cs = fabsf(dm) * rsqrtf(dm * dm + dn * dn);      // cos(angle to the major axis), >= 0.707
    const float idm = 1.f / dm;
    const int nmaj = xmaj ? d.W : d.H;
    const int c0 = max((int)floorf(lo), 0), c1 = min((int)floorf(hi), nmaj - 1);
    const bool alpha = (d.flags & SP_F_ALPHA) != 0;
    const float3 ca = sp_mul3(sp_col(d, aa, rgb, idx, wt), wa), cb = sp_mul3(sp_col(d, ab, rgb, idx, wt), wb);
    const float lam = d.energy * boost / cs;
    const int pool_ctr = ntx * nty;
    const unsigned int ltmask = (1u << lane) - 1u;
    for (int cb0 = c0; cb0 <= c1; cb0 += 32) {                    // warp-uniform loop (bounded by the buffer size)
        const int c = cb0 + lane;
        bool act = c <= c1;
        float sx = 0.f, sy = 0.f, kp = 1.f;
        float3 col = make_float3(0.f, 0.f, 0.f);
        if (act) {
            const float l0 = fmaxf((float)c, lo), l1 = fminf((float)(c + 1), hi);
            const float cov = l1 - l0;
            const float u = (0.5f * (l0 + l1) - ma) * idm;        // 0..1 along the projected segment
            const float mn = na + dn * u;                         // minor-axis centre
            const float iz = iza + (izb - iza) * u;
            const float t3 = ((1.f - u) * ta * iza + u * tb * izb) / iz;   // perspective-correct 3D param
            float rho = d.size * (d.depth_ref > 0.f ? d.depth_ref * iz : 1.f);
            rho = fminf(fmaxf(rho, d.rmin), d.rmax);
            const float e = lam * cov * sp_fade(d, 1.f / iz);
            act = cov > 0.f && e > 1e-12f;
            col = sp_mul3(sp_mix3(ca, cb, t3), e);
            kp = cs / rho;
            sx = xmaj ? c + 0.5f : mn; sy = xmaj ? mn : c + 0.5f;
            if (act && 1.f / kp > (float)SP_HALO - 0.5f) {        // too wide for the tile halo: direct
                sp_frag_direct(acc, d.W, d.H, sx, sy, kp, !xmaj, col, alpha);
                act = false;
            }
        }
        const unsigned int mb = __ballot_sync(0xffffffffu, act);
        if (mb == 0u) continue;
        int base = 0;
        if (lane == 0) base = atomicAdd(&ctr[pool_ctr], __popc(mb));
        base = __shfl_sync(0xffffffffu, base, 0);
        const int slot = base + __popc(mb & ltmask);
        const bool ok = act && slot < cap;
        if (act && !ok) sp_frag_direct(acc, d.W, d.H, sx, sy, kp, !xmaj, col, alpha);
        uint4 rr = make_uint4(0u, 0u, 0u, 0u);
        int tile = -1;
        if (ok) {
            unsigned short qb = sp_f2h(kp);
            qb = (unsigned short)((qb & 0x7ffeu) | 0x8000u | (xmaj ? 0u : 1u));
            rr = sp_pack(sx, sy, col, qb);
            tile = sp_rec_tile(rr, ntx, nty);
        }
        unsigned int m = __ballot_sync(0xffffffffu, ok);
        int rank = 0;
        while (m) {                                               // per-tile ranks, aggregated per warp
            const int leader = __ffs(m) - 1;
            const int lt = __shfl_sync(0xffffffffu, tile, leader);
            const unsigned int same = __ballot_sync(0xffffffffu, ok && tile == lt) & m;
            int b = 0;
            if (lane == leader) b = atomicAdd(&ctr[lt], __popc(same));
            b = __shfl_sync(0xffffffffu, b, leader);
            if ((same >> lane) & 1u) rank = b + __popc(same & ltmask);
            m &= ~same;
        }
        if (ok) { rec[slot] = rr; off[slot] = rank; }
    }
}

// ---- K2: exclusive scan of tile counts (and of 2048-item chunks per tile), then zero the counters for the
// next batch; start[nt + 1] keeps the pool count.  One block of 1024 threads.
extern "C" __global__ void __launch_bounds__(1024)
sp_scan(int* __restrict__ ctr, int* __restrict__ start, int* __restrict__ cst, const int nt)
{
    __shared__ int sa[1024], sb[1024];
    const int tid = threadIdx.x;
    const int per = (nt + 1023) / 1024;
    const int t0 = tid * per;
    int a = 0, b = 0;
    for (int k = 0; k < per; ++k) {
        const int t = t0 + k;
        if (t < nt) { const int c = ctr[t]; a += c; b += (c + SP_CH - 1) / SP_CH; }
    }
    sa[tid] = a; sb[tid] = b;
    __syncthreads();
    for (int o = 1; o < 1024; o <<= 1) {
        int va = 0, vb = 0;
        if (tid >= o) { va = sa[tid - o]; vb = sb[tid - o]; }
        __syncthreads();
        sa[tid] += va; sb[tid] += vb;
        __syncthreads();
    }
    int ea = sa[tid] - a, eb = sb[tid] - b;
    for (int k = 0; k < per; ++k) {
        const int t = t0 + k;
        if (t < nt) {
            const int c = ctr[t];
            start[t] = ea; cst[t] = eb; ea += c; eb += (c + SP_CH - 1) / SP_CH;
            ctr[t] = 0;
        }
    }
    if (tid == 1023) { start[nt] = sa[1023]; cst[nt] = sb[1023]; start[nt + 1] = ctr[nt]; ctr[nt] = 0; }
}

// ---- K3: scatter pooled records into tile order
extern "C" __global__ void __launch_bounds__(256)
sp_scatter(const uint4* __restrict__ rec, const int* __restrict__ off, const int* __restrict__ start,
           uint4* __restrict__ srt, const int ntx, const int nty, const int cap)
{
    const int n = min(start[ntx * nty + 1], cap);
    for (int i = blockIdx.x * blockDim.x + threadIdx.x; i < n; i += gridDim.x * blockDim.x) {
        const uint4 r = rec[i];
        srt[start[sp_rec_tile(r, ntx, nty)] + off[i]] = r;
    }
}

// ---- K4: one block per (tile, 2048-item chunk): accumulate in shared memory, flush with coalesced atomics
template <int C>
__device__ __forceinline__ void sp_tiles_impl(float* __restrict__ acc, const int W, const int H,
                                              const uint4* __restrict__ srt, const int* __restrict__ start,
                                              const int* __restrict__ cst, const int ntx, const int nty)
{
    __shared__ float s[C * SP_TS * SP_TS];
    __shared__ int s_t, s_lo, s_hi;
    const int nt = ntx * nty;
    const int b = blockIdx.x;
    if (b >= cst[nt]) return;                                     // block-uniform
    if (threadIdx.x == 0) {                                       // find tile t: cst[t] <= b < cst[t + 1]
        int lo = 0, hi = nt - 1;
        while (lo < hi) { const int mid = (lo + hi + 1) >> 1; if (cst[mid] <= b) lo = mid; else hi = mid - 1; }
        const int j = b - cst[lo];
        s_t = lo;
        s_lo = start[lo] + j * SP_CH;
        s_hi = min(start[lo + 1], s_lo + SP_CH);
    }
    for (int i = threadIdx.x; i < C * SP_TS * SP_TS; i += blockDim.x) s[i] = 0.f;
    __syncthreads();
    const int t = s_t;
    const int ox = (t % ntx) * SP_TILE - SP_HALO, oy = (t / ntx) * SP_TILE - SP_HALO;   // window origin (px)
    const int P = SP_TS * SP_TS;
    for (int i = s_lo + threadIdx.x; i < s_hi; i += blockDim.x) {
        const uint4 r = srt[i];
        const float fx = __uint_as_float(r.x) - 0.5f - ox, fy = __uint_as_float(r.y) - 0.5f - oy;  // window px
        const float3 c = sp_unpack_c(r);
        const float cov = C == 4 ? sp_lum(c) : 0.f;
        const unsigned short qb = (unsigned short)(r.w >> 16);
        if (!(qb & 0x8000u)) {                                    // point: separable biweight, radius rho
            const float rho = fmaxf(sp_h2f(qb), 1.f), ir = 1.f / rho;
            const int ux0 = (int)ceilf(fx - rho), ux1 = (int)floorf(fx + rho);
            const int uy0 = (int)ceilf(fy - rho), uy1 = (int)floorf(fy + rho);
            float sxw = 0.f, syw = 0.f;
            for (int k = ux0; k <= ux1; ++k) { float q = (k - fx) * ir; q = fmaxf(1.f - q * q, 0.f); sxw += q * q; }
            for (int k = uy0; k <= uy1; ++k) { float q = (k - fy) * ir; q = fmaxf(1.f - q * q, 0.f); syw += q * q; }
            const float inv = 1.f / fmaxf(sxw * syw, 1e-12f);
            for (int j = max(uy0, 0); j <= min(uy1, SP_TS - 1); ++j) {
                float qy = (j - fy) * ir; qy = 1.f - qy * qy;
                if (qy <= 0.f) continue;
                const float wy = qy * qy * inv;
                for (int k = max(ux0, 0); k <= min(ux1, SP_TS - 1); ++k) {
                    float qx = (k - fx) * ir; qx = 1.f - qx * qx;
                    if (qx <= 0.f) continue;
                    const float w = qx * qx * wy;
                    const int p = j * SP_TS + k;
                    atomicAdd(&s[p], c.x * w); atomicAdd(&s[P + p], c.y * w); atomicAdd(&s[2 * P + p], c.z * w);
                    if (C == 4) atomicAdd(&s[(C - 1) * P + p], cov * w);
                }
            }
        } else {                                                  // line fragment: 1D perpendicular profile
            const bool ymaj = (qb & 1u) != 0u;
            const float kp = sp_h2f((unsigned short)(qb & 0x7ffeu)), hs = 1.f / kp;
            const float mn = ymaj ? fx : fy;                      // window px, centre-based (pixel k centre = k)
            const int cc = (int)floorf((ymaj ? fy : fx) + 0.5f);
            if (cc < 0 || cc >= SP_TS) continue;
            const int n0 = (int)ceilf(mn - hs), n1 = (int)floorf(mn + hs);
            float sum = 0.f;
            for (int k = n0; k <= n1; ++k) { float q = (k - mn) * kp; q = fmaxf(1.f - q * q, 0.f); sum += q * q; }
            if (sum <= 0.f) continue;
            const float sc = 1.f / sum;
            for (int k = max(n0, 0); k <= min(n1, SP_TS - 1); ++k) {
                float q = (k - mn) * kp; q = 1.f - q * q;
                if (q <= 0.f) continue;
                const float w = q * q * sc;
                const int p = ymaj ? cc * SP_TS + k : k * SP_TS + cc;
                atomicAdd(&s[p], c.x * w); atomicAdd(&s[P + p], c.y * w); atomicAdd(&s[2 * P + p], c.z * w);
                if (C == 4) atomicAdd(&s[(C - 1) * P + p], cov * w);
            }
        }
    }
    __syncthreads();
    const size_t plane = (size_t)W * H;
    for (int p = threadIdx.x; p < P; p += blockDim.x) {
        const int x = ox + p % SP_TS, y = oy + p / SP_TS;
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        const float r = s[p], g = s[P + p], bb = s[2 * P + p];
        if (r == 0.f && g == 0.f && bb == 0.f) continue;
        float* q = acc + (size_t)y * W + x;
        atomicAdd(q, r); atomicAdd(q + plane, g); atomicAdd(q + 2 * plane, bb);
        if (C == 4) atomicAdd(q + 3 * plane, s[(C - 1) * P + p]);
    }
}
extern "C" __global__ void __launch_bounds__(256)
sp_tiles3(float* __restrict__ acc, const int W, const int H, const uint4* __restrict__ srt,
          const int* __restrict__ start, const int* __restrict__ cst, const int ntx, const int nty)
{ sp_tiles_impl<3>(acc, W, H, srt, start, cst, ntx, nty); }
extern "C" __global__ void __launch_bounds__(256)
sp_tiles4(float* __restrict__ acc, const int W, const int H, const uint4* __restrict__ srt,
          const int* __restrict__ start, const int* __restrict__ cst, const int ntx, const int nty)
{ sp_tiles_impl<4>(acc, W, H, srt, start, cst, ntx, nty); }

// =========================================================================== accumulator utilities
extern "C" __global__ void sp_scale(float4* __restrict__ acc, const int n4, const float k) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n4) return;
    float4 v = acc[i]; v.x *= k; v.y *= k; v.z *= k; v.w *= k; acc[i] = v;
}
// glow source: 4x4 box downsample of the planar accumulator with a soft luminance knee -> float4 (rgb, 0)
extern "C" __global__ void sp_down4(const float* __restrict__ acc, const int W, const int H,
                                    float4* __restrict__ dst, const int w, const int h, const float thr)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const size_t plane = (size_t)W * H;
    float3 s = make_float3(0.f, 0.f, 0.f);
    int n = 0;
    for (int j = 0; j < 4; ++j) {
        const int yy = 4 * y + j;
        if (yy >= H) break;
        for (int i = 0; i < 4; ++i) {
            const int xx = 4 * x + i;
            if (xx >= W) break;
            const size_t o = (size_t)yy * W + xx;
            s.x += acc[o]; s.y += acc[o + plane]; s.z += acc[o + 2 * plane]; ++n;
        }
    }
    s = sp_mul3(s, 1.f / fmaxf((float)n, 1.f));
    if (thr > 0.f) {
        const float L = sp_lum(s);
        s = sp_mul3(s, fmaxf(L - thr, 0.f) / fmaxf(L, 1e-6f));
    }
    dst[y * w + x] = make_float4(s.x, s.y, s.z, 0.f);
}
__device__ __forceinline__ float4 sp_bilin4(const float4* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int x0 = min(max((int)flx, 0), w - 1), x1 = min(max((int)flx + 1, 0), w - 1);
    const int y0 = min(max((int)fly, 0), h - 1), y1 = min(max((int)fly + 1, 0), h - 1);
    const float4 a = img[y0 * w + x0], b = img[y0 * w + x1], c = img[y1 * w + x0], d = img[y1 * w + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 0.f);
}
// 4x4 box downsample of a float4 image (glow pyramid: quarter -> 1/16 res)
extern "C" __global__ void sp_box4(const float4* __restrict__ src, const int sw, const int sh,
                                   float4* __restrict__ dst, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    int n = 0;
    for (int j = 0; j < 4; ++j) {
        const int yy = 4 * y + j;
        if (yy >= sh) break;
        for (int i = 0; i < 4; ++i) {
            const int xx = 4 * x + i;
            if (xx >= sw) break;
            const float4 v = src[yy * sw + xx];
            s.x += v.x; s.y += v.y; s.z += v.z; ++n;
        }
    }
    const float k = 1.f / fmaxf((float)n, 1.f);
    dst[y * w + x] = make_float4(s.x * k, s.y * k, s.z * k, 0.f);
}
// separable gaussian pass (dir 0 = horizontal, 1 = vertical) on a w x h float4 image; outside = black (the glow
// fades at the frame edge instead of piling up).  dst = gain * blur(src) [+ again * bilin(add, aw x ah)]
extern "C" __global__ void sp_blur(const float4* __restrict__ src, float4* __restrict__ dst, const int w, const int h,
                                   const int dir, const float inv2s2, const int taps, const float gain,
                                   const float4* __restrict__ add, const int aw, const int ah, const float again)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    float ws = 0.f;
    const int c = dir == 0 ? x : y, n = dir == 0 ? w : h;
    for (int k = -taps; k <= taps; ++k) {
        const float g = __expf(-(float)(k * k) * inv2s2);
        ws += g;
        const int cc = c + k;
        if (cc < 0 || cc >= n) continue;
        const float4 v = dir == 0 ? src[y * w + cc] : src[cc * w + x];
        s.x += g * v.x; s.y += g * v.y; s.z += g * v.z;
    }
    const float k = gain / ws;
    s = make_float4(s.x * k, s.y * k, s.z * k, 0.f);
    if (again > 0.f) {
        const float4 a = sp_bilin4(add, (x + 0.5f) * aw / w, (y + 0.5f) * ah / h, aw, ah);
        s.x += again * a.x; s.y += again * a.y; s.z += again * a.z;
    }
    dst[y * w + x] = s;
}

// =========================================================================== resolve
struct SpResolve {
    float exposure, glow1, glow2, sat, occl, bgr, bgg, bgb;
    int tonemap, bgmode, blend, dither, W, H, w1, h1, w2, h2, C, pad;
};
__device__ __forceinline__ float sp_bilin1(const float* __restrict__ img, int x0, int x1, int y0, int y1, int W,
                                           float tx, float ty) {
    return (1.f - ty) * ((1.f - tx) * img[y0 * W + x0] + tx * img[y0 * W + x1])
           + ty * ((1.f - tx) * img[y1 * W + x0] + tx * img[y1 * W + x1]);
}
extern "C" __global__ void sp_resolve(uchar4* __restrict__ out, const int Wo, const int Ho,
                                      const float* __restrict__ acc, const float4* __restrict__ g1,
                                      const float4* __restrict__ g2, const uchar4* __restrict__ bg, const SpResolve r)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= Wo || y >= Ho) return;
    const size_t plane = (size_t)r.W * r.H;
    float3 c;
    float cov = 0.f;
    if (Wo == r.W && Ho == r.H) {
        const size_t o = (size_t)y * r.W + x;
        c = make_float3(acc[o], acc[o + plane], acc[o + 2 * plane]);
        if (r.C == 4) cov = acc[o + 3 * plane];
    } else {
        const float fx = (x + 0.5f) * r.W / Wo - 0.5f, fy = (y + 0.5f) * r.H / Ho - 0.5f;
        const float flx = floorf(fx), fly = floorf(fy);
        const float tx = fx - flx, ty = fy - fly;
        const int x0 = min(max((int)flx, 0), r.W - 1), x1 = min(max((int)flx + 1, 0), r.W - 1);
        const int y0 = min(max((int)fly, 0), r.H - 1), y1 = min(max((int)fly + 1, 0), r.H - 1);
        c = make_float3(sp_bilin1(acc, x0, x1, y0, y1, r.W, tx, ty), sp_bilin1(acc + plane, x0, x1, y0, y1, r.W, tx, ty),
                        sp_bilin1(acc + 2 * plane, x0, x1, y0, y1, r.W, tx, ty));
        if (r.C == 4) cov = sp_bilin1(acc + 3 * plane, x0, x1, y0, y1, r.W, tx, ty);
    }
    if (r.glow1 > 0.f) {                                          // pre-combined bloom + wide halo (quarter res)
        const float4 g = sp_bilin4(g1, (x + 0.5f) * r.w1 / Wo, (y + 0.5f) * r.h1 / Ho, r.w1, r.h1);
        c.x += r.glow1 * g.x; c.y += r.glow1 * g.y; c.z += r.glow1 * g.z;
    }
    c = sp_mul3(c, r.exposure);
    if (r.sat != 1.f) {
        const float L = sp_lum(c);
        c = make_float3(fmaxf(L + (c.x - L) * r.sat, 0.f), fmaxf(L + (c.y - L) * r.sat, 0.f), fmaxf(L + (c.z - L) * r.sat, 0.f));
    }
    if (r.tonemap == 0) {                                         // exp: 1 - e^-x per channel (burns to white)
        c = make_float3(1.f - __expf(-c.x), 1.f - __expf(-c.y), 1.f - __expf(-c.z));
    } else if (r.tonemap == 1) {                                  // ACES (Narkowicz fit)
        #define SP_ACES(v) sp_clamp01((v * (2.51f * v + 0.03f)) / (v * (2.43f * v + 0.59f) + 0.14f))
        c = make_float3(SP_ACES(c.x), SP_ACES(c.y), SP_ACES(c.z));
        #undef SP_ACES
    } else {                                                      // Reinhard on luminance (keeps hue)
        c = sp_mul3(c, 1.f / (1.f + sp_lum(c)));
    }
    float3 b = make_float3(r.bgr, r.bgg, r.bgb);
    if (r.bgmode == 1) {
        const uchar4 p = bg[y * Wo + x];
        b = make_float3(sp_lin(p.x * (1.f / 255.f)), sp_lin(p.y * (1.f / 255.f)), sp_lin(p.z * (1.f / 255.f)));
    }
    if (r.occl > 0.f) b = sp_mul3(b, __expf(-r.occl * cov));
    if (r.blend == 0) c = make_float3(1.f - (1.f - sp_clamp01(b.x)) * (1.f - sp_clamp01(c.x)),
                                      1.f - (1.f - sp_clamp01(b.y)) * (1.f - sp_clamp01(c.y)),
                                      1.f - (1.f - sp_clamp01(b.z)) * (1.f - sp_clamp01(c.z)));
    else c = sp_add3(c, b);
    float dth = 0.f;
    if (r.dither) {
        const unsigned int h = sp_hash((unsigned int)x * 1973u + (unsigned int)y * 9277u + 0x5bd1e995u);
        dth = ((h & 0xffff) + ((h >> 16) & 0xffff)) * (1.f / 65536.f) - 1.f;   // triangular, +-1 LSB
    }
    out[y * Wo + x] = make_uchar4((unsigned char)fminf(fmaxf(sp_srgb(c.x) * 255.f + dth + 0.5f, 0.f), 255.f),
                                  (unsigned char)fminf(fmaxf(sp_srgb(c.y) * 255.f + dth + 0.5f, 0.f), 255.f),
                                  (unsigned char)fminf(fmaxf(sp_srgb(c.z) * 255.f + dth + 0.5f, 0.f), 255.f), 255);
}

// =========================================================================== integrators
struct SpSim {
    float k[6];             // attractor params
    float dt, fade_in, limit;
    float spawn_c[3], spawn_e[3];
    float scale, strength, ox, oy, oz, cx, cy, cz, pull, swirl, radius;
    int kind, method, steps, n, seed, frame, octaves, pad;
};
extern "C" __global__ void __launch_bounds__(256)
sp_attractor(float4* __restrict__ pos, const SpSim s)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= s.n) return;
    float4 q = pos[i];
    float3 p = make_float3(q.x, q.y, q.z);
    bool bad = !(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < s.limit);
    if (!bad) {
        float k[6];
        #pragma unroll
        for (int j = 0; j < 6; ++j) k[j] = s.k[j];
        for (int st = 0; st < s.steps && st < 64; ++st) p = sp_attr_step(s.kind, p, k, s.dt, s.method);
        bad = !(fabsf(p.x) + fabsf(p.y) + fabsf(p.z) < s.limit);
    }
    if (bad) {                                                    // respawn inside the seed box, invisible
        const float3 h = sp_hash3((unsigned int)i, (unsigned int)s.seed * 7919u + (unsigned int)s.frame);
        p = make_float3(s.spawn_c[0] + s.spawn_e[0] * (2.f * h.x - 1.f), s.spawn_c[1] + s.spawn_e[1] * (2.f * h.y - 1.f),
                        s.spawn_c[2] + s.spawn_e[2] * (2.f * h.z - 1.f));
        q.w = 0.f;
    } else {
        q.w = fminf(q.w + s.fade_in, 1.f);
    }
    pos[i] = make_float4(p.x, p.y, p.z, q.w);
}
__device__ __forceinline__ float3 sp_curl_vel(const SpSim& s, float3 p) {
    float3 v = sp_mul3(sp_curl(make_float3(p.x * s.scale + s.ox, p.y * s.scale + s.oy, p.z * s.scale + s.oz),
                               (unsigned int)s.seed, s.octaves), s.strength);
    const float3 r = make_float3(p.x - s.cx, p.y - s.cy, p.z - s.cz);
    v = sp_add3(v, sp_mul3(r, -s.pull));
    v = sp_add3(v, make_float3(-r.z * s.swirl, 0.f, r.x * s.swirl));
    return v;
}
extern "C" __global__ void __launch_bounds__(256)
sp_curl_step(float4* __restrict__ pos, const SpSim s)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= s.n) return;
    float4 q = pos[i];
    float3 p = make_float3(q.x, q.y, q.z);
    for (int st = 0; st < s.steps && st < 16; ++st) {
        const float3 k1 = sp_curl_vel(s, p);
        if (s.method == 0) p = sp_add3(p, sp_mul3(k1, s.dt));
        else p = sp_add3(p, sp_mul3(sp_curl_vel(s, sp_add3(p, sp_mul3(k1, 0.5f * s.dt))), s.dt));
    }
    const float3 r = make_float3(p.x - s.cx, p.y - s.cy, p.z - s.cz);
    const bool outside = !(sp_dot3(r, r) < (s.radius > 0.f ? s.radius * s.radius : 3e38f));
    if (outside) {                                                // respawn uniformly inside the sphere
        const float3 h = sp_hash3((unsigned int)i, (unsigned int)s.seed * 7919u + (unsigned int)s.frame);
        const float ct = 2.f * h.x - 1.f, st = sqrtf(fmaxf(1.f - ct * ct, 0.f)), ph = 6.2831853f * h.y;
        const float rr = (s.radius > 0.f ? s.radius : 1.f) * cbrtf(h.z);
        p = make_float3(s.cx + rr * st * __cosf(ph), s.cy + rr * ct, s.cz + rr * st * __sinf(ph));
        q.w = 0.f;
    } else {
        q.w = fminf(q.w + s.fade_in, 1.f);
    }
    pos[i] = make_float4(p.x, p.y, p.z, q.w);
}
// ring-buffer trails: trails (M, K, 4); push writes slot, fill writes every slot
extern "C" __global__ void sp_trail_push(float4* __restrict__ trails, const float4* __restrict__ src, const int m,
                                         const int K, const int slot, const int src_stride4)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= m) return;
    trails[(size_t)i * K + slot] = src[(size_t)i * src_stride4];
}
extern "C" __global__ void sp_trail_fill(float4* __restrict__ trails, const float4* __restrict__ src, const int m,
                                         const int K, const int src_stride4)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= m * K) return;
    trails[i] = src[(size_t)(i / K) * src_stride4];
}
"""

_DRAW_DTYPE = np.dtype([("mv", np.float32, 12), ("pal", np.float32, 12), ("tint", np.float32, 3)]
                       + [(k, np.float32) for k in ("period", "drift", "energy", "size", "depth_ref", "rmin", "rmax",
                                                    "near_z", "fn0", "fn1", "ff0", "ff1", "fog", "edge", "focal",
                                                    "cx", "cy", "cov")]
                       + [(k, np.int32) for k in ("W", "H", "n", "stride0", "stride1", "mode", "K", "S", "flags",
                                                  "head")])
_RESOLVE_DTYPE = np.dtype([(k, np.float32) for k in ("exposure", "glow1", "glow2", "sat", "occl", "bgr", "bgg", "bgb")]
                          + [(k, np.int32) for k in ("tonemap", "bgmode", "blend", "dither", "W", "H", "w1", "h1",
                                                     "w2", "h2", "C", "pad")])
_SIM_DTYPE = np.dtype([("k", np.float32, 6), ("dt", np.float32), ("fade_in", np.float32), ("limit", np.float32),
                       ("spawn_c", np.float32, 3), ("spawn_e", np.float32, 3)]
                      + [(k, np.float32) for k in ("scale", "strength", "ox", "oy", "oz", "cx", "cy", "cz", "pull",
                                                   "swirl", "radius")]
                      + [(k, np.int32) for k in ("kind", "method", "steps", "n", "seed", "frame", "octaves", "pad")])
_DRAW_NF = 45   # float32 slots before the int block in SpDraw

F_RGB, F_IDX, F_WT, F_PAL, F_ALPHA, F_POS4W, F_SIZE, F_TAIL = 1, 2, 4, 8, 16, 32, 64, 128
TONEMAPS = ("exp", "aces", "reinhard")
METHODS = ("euler", "rk2", "rk4")


def _module():
    return raw_module(_KERNELS, SPLAT3D_OPTIONS)


# ----------------------------------------------------------------------------------------------- host maths
# Pure-Python 3-vector maths: these run every frame on a busy host, where numpy's per-call overhead dominates.
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    n = math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (float(a[0]), float(a[1]), float(a[2]))


def srgb_to_linear(c: float) -> float:
    """sRGB-encoded -> linear (values > 1 extrapolate, so HDR tints like (3, 1.5, 0.5) work)."""
    c = max(float(c), 0.0)
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


class Cam:
    """Pinhole camera: eye, orthonormal fwd/right/up (y up, right-handed), vertical fov, near plane, and ``dist``
    (eye -> target; the default depth_ref for perspective sizes).  ``pack()`` = sdf3d.camera_pack's 13 floats."""

    __slots__ = ("eye", "fwd", "right", "up", "tan_half", "near", "dist", "_view")

    def __init__(self, eye, fwd, right, up, tan_half: float, near: float = 0.05, dist: float = 1.0) -> None:
        self.eye = tuple(float(v) for v in eye)
        self.fwd, self.right, self.up = tuple(fwd), tuple(right), tuple(up)
        self.tan_half, self.near, self.dist = float(tan_half), float(near), float(dist)
        r, u, f, e = self.right, self.up, self.fwd, self.eye
        self._view = (r[0], r[1], r[2], -_dot(r, e), u[0], u[1], u[2], -_dot(u, e), f[0], f[1], f[2], -_dot(f, e))

    def pack(self) -> list[float]:
        return [*self.eye, *self.fwd, *self.right, *self.up, self.tan_half]

    def view(self) -> np.ndarray:
        """3x4 world -> camera (x right, y up, z forward = depth)."""
        return np.asarray(self._view, np.float64).reshape(3, 4)

    def project(self, p, w: int, h: int) -> tuple[float, float, float]:
        """Host projection of world point p into a w x h buffer: (sx, sy, depth); depth <= near: behind."""
        v = self._view
        x = v[0] * p[0] + v[1] * p[1] + v[2] * p[2] + v[3]
        y = v[4] * p[0] + v[5] * p[1] + v[6] * p[2] + v[7]
        z = v[8] * p[0] + v[9] * p[1] + v[10] * p[2] + v[11]
        f = 0.5 * h / self.tan_half
        zz = z if abs(z) > 1e-9 else 1e-9
        return 0.5 * w + f * x / zz, 0.5 * h - f * y / zz, z


def look_at(eye: Sequence[float], target: Sequence[float], up: Sequence[float] = (0.0, 1.0, 0.0),
            fov_deg: float = 50.0, roll: float = 0.0, near: float = 0.05) -> Cam:
    """Camera at ``eye`` looking at ``target`` (vertical ``fov_deg``); ``roll`` banks it (radians)."""
    d = _sub(target, eye)
    fwd = _unit(d)
    right = _cross(fwd, _unit(up))
    if _dot(right, right) < 1e-12:
        right = _cross(fwd, (0.0, 0.0, 1.0) if abs(fwd[2]) < 0.9 else (1.0, 0.0, 0.0))
    right = _unit(right)
    upv = _cross(right, fwd)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        right, upv = (tuple(c * a + s * b for a, b in zip(right, upv)),
                      tuple(-s * a + c * b for a, b in zip(right, upv)))
    return Cam(eye, fwd, right, upv, math.tan(math.radians(fov_deg) * 0.5), near, math.sqrt(_dot(d, d)))


def orbit(target: Sequence[float], radius: float, yaw: float, pitch: float) -> tuple[float, float, float]:
    """Eye on a sphere around ``target``: yaw about +y (0 = on the -z side, looking +z), pitch up (radians)."""
    cp = math.cos(pitch)
    return (target[0] + radius * cp * math.sin(yaw), target[1] + radius * math.sin(pitch),
            target[2] - radius * cp * math.cos(yaw))


def mat_identity() -> np.ndarray:
    return np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]])


def mat_rot(axis: Sequence[float], angle: float) -> np.ndarray:
    x, y, z = _unit(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return np.array([[c + x * x * t, x * y * t - z * s, x * z * t + y * s, 0.0],
                     [y * x * t + z * s, c + y * y * t, y * z * t - x * s, 0.0],
                     [z * x * t - y * s, z * y * t + x * s, c + z * z * t, 0.0]])


def mat_euler(rx: float, ry: float, rz: float) -> np.ndarray:
    """Rotate about x, then y, then z (radians)."""
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    return np.array([[cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz, 0.0],
                     [cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz, 0.0],
                     [-sy, sx * cy, cx * cy, 0.0]])


def mat_scale(s) -> np.ndarray:
    sx, sy, sz = (s, s, s) if np.isscalar(s) else s
    return np.array([[float(sx), 0, 0, 0], [0, float(sy), 0, 0], [0, 0, float(sz), 0]])


def mat_translate(t: Sequence[float]) -> np.ndarray:
    return np.array([[1.0, 0, 0, float(t[0])], [0, 1.0, 0, float(t[1])], [0, 0, 1.0, float(t[2])]])


def mat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compose 3x4 affine matrices: (a after b)(p) = a(b(p))."""
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    out = np.empty((3, 4))
    out[:, :3] = a[:, :3] @ b[:, :3]
    out[:, 3] = a[:, :3] @ b[:, 3] + a[:, 3]
    return out


def mat_trs(t: Sequence[float] = (0.0, 0.0, 0.0), r: Optional[np.ndarray] = None, s=1.0) -> np.ndarray:
    """translate(t) after rotate(r) after scale(s)."""
    m = mat_scale(s)
    if r is not None:
        r = np.asarray(r, np.float64)
        if r.shape == (3, 3):
            r = np.concatenate([r, np.zeros((3, 1))], axis=1)
        m = mat_mul(r, m)
    return mat_mul(mat_translate(t), m)


_PAL_CACHE: dict[str, tuple[float, ...]] = {}


def palette_arg(palette) -> Optional[tuple[float, ...]]:
    """Palette name or 12 coefficients -> 12 floats (None passes through)."""
    if palette is None:
        return None
    if isinstance(palette, str):
        v = _PAL_CACHE.get(palette)
        if v is None:
            a, b, c, d = PALETTES[palette]
            v = _PAL_CACHE[palette] = (*a, *b, *c, *d)
        return v
    v = tuple(float(x) for x in palette)
    assert len(v) == 12, "palette needs 12 coefficients"
    return v


def _compose_view(view: tuple, model) -> list[float]:
    """view (12-tuple, 3x4 row-major) after model (3x4 array-like) -> 12 floats."""
    m = np.asarray(model, np.float64).reshape(-1).tolist()
    out = []
    for r in range(3):
        v0, v1, v2, v3 = view[4 * r: 4 * r + 4]
        out += [v0 * m[0] + v1 * m[4] + v2 * m[8], v0 * m[1] + v1 * m[5] + v2 * m[9],
                v0 * m[2] + v1 * m[6] + v2 * m[10], v0 * m[3] + v1 * m[7] + v2 * m[11] + v3]
    return out


# ----------------------------------------------------------------------------------------------- renderer
class Splat3D:
    """Additive 3D point / line splatter with a planar float32 RGB(+coverage) accumulator of w x h (module doc).

    Draw calls append 16-byte records to a pool binned by 32x32 screen tile; ``flush()`` (called by ``resolve``)
    accumulates them tile by tile in shared memory.  ``pool`` (default max(1M, max_points + 48 * max_lines))
    costs 36 bytes per item; items beyond it, and footprints wider than the 4 px tile halo, are splatted directly
    (correct, slower)."""

    def __init__(self, ctx, w: int, h: int, max_points: int = 1 << 20, max_lines: int = 1 << 14, *,
                 alpha: bool = False, pool: Optional[int] = None) -> None:
        cp = ctx.cp
        self.ctx, self.cp = ctx, cp
        self.w, self.h = int(w), int(h)
        self.alpha = bool(alpha)
        self.C = 4 if self.alpha else 3
        self.s = self.h / REF_H
        self.acc = cp.zeros((self.C, self.h, self.w), cp.float32)
        # glow pyramid: quarter-res source g1 -> separable blur (gt temp) -> ga; wide halo at 1/16 res (w1 -> w2 -> gb)
        # is blurred the same way and folded into ga, so resolve() makes a single glow fetch per output pixel
        self.gw1, self.gh1 = max(1, (self.w + 3) // 4), max(1, (self.h + 3) // 4)
        self.gwb, self.ghb = max(1, (self.gw1 + 3) // 4), max(1, (self.gh1 + 3) // 4)
        self.g1 = cp.zeros((self.gh1, self.gw1, 4), cp.float32)
        self.gt = cp.zeros_like(self.g1)
        self.ga = cp.zeros_like(self.g1)
        self.w1 = cp.zeros((self.ghb, self.gwb, 4), cp.float32)
        self.w2 = cp.zeros_like(self.w1)
        self.gb = cp.zeros_like(self.w1)
        self.max_points, self.max_lines = int(max_points), int(max_lines)
        self.pos_buf = None
        self.seg_buf = None
        self.ntx, self.nty = (self.w + TILE - 1) // TILE, (self.h + TILE - 1) // TILE
        self.nt = self.ntx * self.nty
        assert self.nt <= 1024 * 64, "accumulator too large for tile binning"
        self.cap = int(pool) if pool is not None else max(1 << 20, self.max_points + 48 * self.max_lines)
        self.ctr = cp.zeros(self.nt + 4, cp.int32)                     # per-tile counts + [nt] pool cursor
        self.start = cp.zeros(self.nt + 4, cp.int32)                   # + [nt+1] pool count of the last batch
        self.cst = cp.zeros(self.nt + 4, cp.int32)
        self.rec = cp.zeros((max(self.cap, 1), 4), cp.uint32)
        self.srt = cp.zeros((max(self.cap, 1), 4), cp.uint32)
        self.off = cp.zeros(max(self.cap, 1), cp.int32)
        self.pending = False
        self._dummy = cp.zeros(4, cp.float32)
        self._dummy_i = cp.zeros(2, cp.int32)
        self._dummy_u8 = cp.zeros((1, 1, 4), cp.uint8)
        self._dummy4 = cp.zeros((1, 1, 4), cp.float32)
        mod = _module()
        self.k_points = mod.get_function("sp_points_bin")
        self.k_lines = mod.get_function("sp_lines_bin")
        self.k_scan = mod.get_function("sp_scan")
        self.k_scatter = mod.get_function("sp_scatter")
        self.k_tiles = mod.get_function("sp_tiles4" if self.alpha else "sp_tiles3")
        self.k_scale = mod.get_function("sp_scale")
        self.k_down = mod.get_function("sp_down4")
        self.k_box = mod.get_function("sp_box4")
        self.k_blur = mod.get_function("sp_blur")
        self.k_resolve = mod.get_function("sp_resolve")
        # draw struct: one float32 buffer whose int tail is viewed as int32, passed by value as a numpy record
        self._dbuf = np.zeros(_DRAW_DTYPE.itemsize // 4, np.float32)
        self._dint = self._dbuf[_DRAW_NF:].view(np.int32)
        self._drec = self._dbuf.view(_DRAW_DTYPE)
        self._rbuf = np.zeros(_RESOLVE_DTYPE.itemsize // 4, np.float32)
        self._rint = self._rbuf[8:].view(np.int32)
        self._rrec = self._rbuf.view(_RESOLVE_DTYPE)
        self._bin_args = (np.int32(self.ntx), np.int32(self.nty), np.int32(self.cap))
        self._tiles_grid = self.nt + (self.cap + CHUNK - 1) // CHUNK + 1
        self._W, self._H, self._NT = np.int32(self.w), np.int32(self.h), np.int32(self.nt)

    def scratch(self, n_points: int = 0, n_segments: int = 0):
        """Allocate (once, in setup) optional geometry scratch: pos_buf (n,4) and seg_buf (n,2,4) float32."""
        if n_points:
            self.pos_buf = self.cp.zeros((int(n_points), 4), self.cp.float32)
        if n_segments:
            self.seg_buf = self.cp.zeros((int(n_segments), 2, 4), self.cp.float32)
        return self.pos_buf, self.seg_buf

    # -- frame
    def clear(self) -> None:
        """Start a frame: zero the accumulator (pending, unflushed items are dropped)."""
        self.acc.data.memset_async(0, self.acc.nbytes)
        self._reset_bins()

    def fade(self, k: float) -> None:
        """Start a frame keeping acc * k (0..1): persistence / long-exposure trails instead of clear()."""
        n4 = self.acc.size // 4
        self.k_scale(((n4 + 255) // 256,), (256,), (self.acc, np.int32(n4), np.float32(k)))
        self._reset_bins()

    def _reset_bins(self) -> None:
        if self.pending:
            self.ctr.data.memset_async(0, self.ctr.nbytes)
        self.pending = False

    def flush(self) -> None:
        """Accumulate every pending binned item (resolve() does this; call it yourself before reading ``acc``)."""
        if not self.pending:
            return
        self.k_scan((1,), (1024,), (self.ctr, self.start, self.cst, self._NT))      # also zeroes ctr
        self.k_scatter((512,), (256,), (self.rec, self.off, self.start, self.srt, *self._bin_args))
        self.k_tiles((self._tiles_grid,), (256,), (self.acc, self._W, self._H, self.srt, self.start, self.cst,
                                                   self._bin_args[0], self._bin_args[1]))
        self.pending = False

    # -- draw struct
    def _draw(self, cam: Cam, model, colour, intensity, size_px, size_range, depth_ref, palette, period, drift,
              near_fade, far_fade, fog, energy_scale, n, s0, s1, mode=0, K=0, S=0, flags=0, head=0, edge=1.0):
        f = self._dbuf
        f[0:12] = cam._view if model is None else _compose_view(cam._view, model)
        pal = palette_arg(palette)
        if pal is not None:
            f[12:24] = pal
            flags |= F_PAL
        if self.alpha:
            flags |= F_ALPHA
        s = self.s
        rmin = max(1.0, size_range[0] * s)
        fn0, fn1 = near_fade if near_fade else (0.0, 0.0)
        ff0, ff1 = far_fade if far_fade else (0.0, 0.0)
        f[24:_DRAW_NF] = (srgb_to_linear(colour[0]), srgb_to_linear(colour[1]), srgb_to_linear(colour[2]),
                          period, drift, intensity * energy_scale, size_px * s,
                          cam.dist if depth_ref is None else depth_ref, rmin,
                          min(SP_MAX_R, max(rmin, size_range[1] * s)), cam.near, fn0, fn1, ff0, ff1, fog, edge,
                          0.5 * self.h / cam.tan_half, 0.5 * self.w, 0.5 * self.h, 1.0)
        self._dint[:] = (self.w, self.h, n, s0, s1, mode, K, S, flags, head)
        return self._drec[0]

    @staticmethod
    def _stride(a) -> int:
        st = int(a.shape[-1]) if a.ndim >= 2 else 3
        assert st in (3, 4) and a.dtype == np.float32, "positions must be float32 (..., 3) or (..., 4)"
        assert a.flags.c_contiguous, "positions must be C-contiguous"
        return st

    def _attrs(self, rgb, index, weight):
        flags = (F_RGB if rgb is not None else 0) | (F_IDX if index is not None else 0) | \
                (F_WT if weight is not None else 0)
        return (flags, rgb if rgb is not None else self._dummy, index if index is not None else self._dummy,
                weight if weight is not None else self._dummy)

    # -- points
    def points(self, pos, *, cam: Cam, n: Optional[int] = None, model=None, colour=(1.0, 1.0, 1.0),
               intensity: float = 1.0, size_px: float = 1.5, size_range=(0.6, 12.0), depth_ref=None, rgb=None,
               index=None, weight=None, sizes=None, weight_in_w: bool = False, palette=None, period: float = 1.0,
               drift: float = 0.0, near_fade=None, far_fade=None, fog: float = 0.0) -> None:
        stride = self._stride(pos)
        count = int(pos.size // stride) if n is None else int(n)
        if count <= 0:
            return
        flags, rgb_, idx_, wt_ = self._attrs(rgb, index, weight)
        if weight_in_w and stride == 4:
            flags |= F_POS4W
        if sizes is not None:
            flags |= F_SIZE
        d = self._draw(cam, model, colour, intensity, size_px, size_range, depth_ref, palette, period, drift,
                       near_fade, far_fade, fog, self.s * self.s, count, stride, stride, flags=flags)
        per_block = 256 * PPT
        self.k_points(((count + per_block - 1) // per_block,), (256,),
                      (self.acc, d, pos, rgb_, idx_, wt_, sizes if sizes is not None else self._dummy,
                       self.rec, self.off, self.ctr, *self._bin_args))
        self.pending = True

    # -- lines (all modes)
    def _lines(self, mode: int, count: int, p0, p1, E, *, cam: Cam, model=None, colour=(1.0, 1.0, 1.0),
               intensity: float = 1.0, width_px: float = 1.5, size_range=(0.6, 12.0), depth_ref=None, rgb=None,
               index=None, weight=None, weight_in_w: bool = False, palette=None, period: float = 1.0,
               drift: float = 0.0, near_fade=None, far_fade=None, fog: float = 0.0,
               K: int = 0, S: int = 0, head: int = 0, edge: float = 1.0, tail_fade: bool = False) -> None:
        if count <= 0:
            return
        flags, rgb_, idx_, wt_ = self._attrs(rgb, index, weight)
        s0 = self._stride(p0)
        s1 = self._stride(p1) if p1 is not None else s0
        if weight_in_w and s0 == 4 and s1 == 4:
            flags |= F_POS4W
        if tail_fade:
            flags |= F_TAIL
        d = self._draw(cam, model, colour, intensity, width_px, size_range, depth_ref, palette, period, drift,
                       near_fade, far_fade, fog, self.s, count, s0, s1, mode, K, S, flags, head % max(K, 1), edge)
        threads = count * 32
        self.k_lines(((threads + 127) // 128,), (128,),
                     (self.acc, d, p0, p1 if p1 is not None else p0, E if E is not None else self._dummy_i,
                      rgb_, idx_, wt_, self.rec, self.off, self.ctr, *self._bin_args))
        self.pending = True

    def lines(self, p0, p1, *, n: Optional[int] = None, **style) -> None:
        """Independent segments p0[i] -> p1[i] ((N,3|4) each); rgb/index/weight are per segment."""
        count = int(p0.size // self._stride(p0)) if n is None else int(n)
        self._lines(0, count, p0, p1, None, **style)

    def wire(self, verts, edges, *, n: Optional[int] = None, **style) -> None:
        """Indexed wireframe: edges (E,2) int32 into verts (V,3|4); rgb/index/weight are per vertex."""
        assert edges.dtype == np.int32, "edges must be int32"
        count = int(edges.size // 2) if n is None else int(n)
        self._lines(2, count, verts, None, edges, **style)

    def polylines(self, verts, k: Optional[int] = None, *, n_strips: Optional[int] = None, head: int = 0,
                  tail_fade: bool = False, **style) -> None:
        """Strips of k vertices (verts (M,K,3|4), or flat with k given); attributes per vertex.  head: ring-buffer
        start (oldest vertex) for trails; tail_fade ramps the weight 0 (oldest) -> 1 (newest)."""
        K = int(verts.shape[-2]) if k is None else int(k)
        assert K >= 2
        stride = self._stride(verts)
        M = int(verts.size // (stride * K)) if n_strips is None else int(n_strips)
        self._lines(1, M * (K - 1), verts, None, None, K=K, head=head, tail_fade=tail_fade, **style)

    def ribbons(self, a, b, strands: int = 8, *, k: Optional[int] = None, n_ribbons: Optional[int] = None,
                edge: float = 2.0, **style) -> None:
        """Ruled sheets between rails a and b ((M,K,3|4) each): ``strands`` lines lerped across each ribbon
        (the two edge strands x ``edge``).  Attributes per rail-a vertex."""
        K = int(a.shape[-2]) if k is None else int(k)
        stride = self._stride(a)
        M = int(a.size // (stride * K)) if n_ribbons is None else int(n_ribbons)
        S = max(1, int(strands))
        self._lines(3, M * S * (K - 1), a, b, None, K=K, S=S, edge=edge, **style)

    # -- resolve
    def resolve(self, out, *, exposure: float = 1.0, tonemap: str = "aces", saturation: float = 1.0,
                glow: float = 0.0, glow_radius_px: float = 24.0, glow_wide: float = 0.5,
                glow_threshold: float = 0.0, background=None, occlusion: float = 0.0, blend: str = "screen",
                dither: bool = True) -> None:
        self.flush()
        Ho, Wo = int(out.shape[0]), int(out.shape[1])
        g1 = 0.0
        if glow > 0.0:
            launch_2d(self.k_down, self.gw1, self.gh1,
                      (self.acc, self._W, self._H, self.g1, np.int32(self.gw1), np.int32(self.gh1),
                       np.float32(glow_threshold)))
            add, again = self._dummy4, 0.0
            if glow_wide > 0.0:                                         # wide halo: 4x the radius at 1/16 res
                gw, gh = np.int32(self.gwb), np.int32(self.ghb)
                launch_2d(self.k_box, self.gwb, self.ghb, (self.g1, np.int32(self.gw1), np.int32(self.gh1),
                                                           self.w1, gw, gh))
                sigb = min(10.0, max(0.75, glow_radius_px * self.s / 4.0))
                ib, tb = np.float32(1.0 / (2 * sigb * sigb)), np.int32(min(24, math.ceil(2.5 * sigb)))
                launch_2d(self.k_blur, self.gwb, self.ghb, (self.w1, self.w2, gw, gh, np.int32(0), ib, tb,
                                                            np.float32(1.0), self._dummy4, gw, gh, np.float32(0)))
                launch_2d(self.k_blur, self.gwb, self.ghb, (self.w2, self.gb, gw, gh, np.int32(1), ib, tb,
                                                            np.float32(1.0), self._dummy4, gw, gh, np.float32(0)))
                add, again = self.gb, float(glow_wide)
            sig = min(10.0, max(0.75, glow_radius_px * self.s / 4.0))    # in quarter-res px
            ia, ta = np.float32(1.0 / (2 * sig * sig)), np.int32(min(24, math.ceil(2.5 * sig)))
            gw, gh = np.int32(self.gw1), np.int32(self.gh1)
            launch_2d(self.k_blur, self.gw1, self.gh1, (self.g1, self.gt, gw, gh, np.int32(0), ia, ta,
                                                        np.float32(1.0), self._dummy4, gw, gh, np.float32(0)))
            launch_2d(self.k_blur, self.gw1, self.gh1, (self.gt, self.ga, gw, gh, np.int32(1), ia, ta,
                                                        np.float32(1.0), add, np.int32(self.gwb), np.int32(self.ghb),
                                                        np.float32(again)))
            g1 = float(glow)
        bg = self._dummy_u8
        bgmode, bgc = 0, (0.0, 0.0, 0.0)
        if background is not None:
            if hasattr(background, "shape") and len(background.shape) == 3:
                assert background.shape[0] == Ho and background.shape[1] == Wo, "background must match out"
                bg, bgmode = background, 1
            else:
                bgc = tuple(srgb_to_linear(c) for c in background)
        self._rbuf[0:8] = (exposure, g1, 0.0, saturation, occlusion if self.alpha else 0.0, *bgc)
        self._rint[:] = (TONEMAPS.index(tonemap), bgmode, 0 if blend == "screen" else 1, 1 if dither else 0,
                         self.w, self.h, self.gw1, self.gh1, self.gwb, self.ghb, self.C, 0)
        launch_2d(self.k_resolve, Wo, Ho, (out, np.int32(Wo), np.int32(Ho), self.acc, self.ga, self.gb, bg,
                                           self._rrec[0]))

    def close(self) -> None:
        self.acc = self.g1 = self.gt = self.ga = self.w1 = self.w2 = self.gb = self.pos_buf = self.seg_buf = None
        self.rec = self.srt = self.off = self.ctr = self.start = self.cst = None


# ----------------------------------------------------------------------------------------------- integrators
# name: kind id, params (6), stable step dt, rate (native time / s for a pleasant ~0.3 unit/s flow), native centre
# and radius (-> unit cloud), seed box (centre, half extent), divergence limit (|x|+|y|+|z|).
ATTRACTORS: dict[str, dict] = {
    "lorenz": dict(kind=0, k=(10.0, 28.0, 8.0 / 3.0, 0, 0, 0), dt=0.005, rate=0.10, centre=(0.0, 0.0, 24.0),
                   radius=27.0, spawn=((0.0, 0.0, 20.0), (8.0, 8.0, 8.0)), limit=400.0),
    "aizawa": dict(kind=1, k=(0.95, 0.7, 0.6, 3.5, 0.25, 0.1), dt=0.01, rate=0.14, centre=(0.0, 0.0, 0.7),
                   radius=1.55, spawn=((0.0, 0.0, 0.5), (0.4, 0.4, 0.4)), limit=50.0),
    "thomas": dict(kind=2, k=(0.208186, 0, 0, 0, 0, 0), dt=0.06, rate=2.0, centre=(0.0, 0.0, 0.0), radius=4.5,
                   spawn=((0.0, 0.0, 0.0), (2.0, 2.0, 2.0)), limit=100.0),
    "halvorsen": dict(kind=3, k=(1.89, 0, 0, 0, 0, 0), dt=0.004, rate=0.13, centre=(-2.9, -2.9, -2.9), radius=12.5,
                      spawn=((-1.0, -1.0, -1.0), (2.0, 2.0, 2.0)), limit=200.0),
    "dadras": dict(kind=4, k=(3.0, 2.7, 1.7, 2.0, 9.0, 0), dt=0.004, rate=0.6, centre=(0.6, -1.1, 0.4), radius=12.0,
                   spawn=((0.0, 0.0, 0.0), (3.0, 3.0, 3.0)), limit=400.0),
    "rossler": dict(kind=5, k=(0.2, 0.2, 5.7, 0, 0, 0), dt=0.02, rate=0.55, centre=(1.0, -1.4, 4.0), radius=13.0,
                    spawn=((0.0, 0.0, 0.5), (4.0, 4.0, 0.5)), limit=400.0),
    "chen": dict(kind=6, k=(35.0, 3.0, 28.0, 0, 0, 0), dt=0.002, rate=0.07, centre=(0.0, 0.0, 25.0), radius=30.0,
                 spawn=((0.0, 0.0, 20.0), (8.0, 8.0, 8.0)), limit=500.0),
    "four_wing": dict(kind=7, k=(0.2, 0.01, -0.4, 0, 0, 0), dt=0.03, rate=6.0, centre=(0.0, 0.0, 0.0), radius=2.5,
                      spawn=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0)), limit=100.0),
    "sprott": dict(kind=8, k=(2.07, 1.79, 0, 0, 0, 0), dt=0.01, rate=0.57, centre=(0.45, 0.08, 0.11), radius=1.4,
                   spawn=((0.0, 0.5, 0.0), (0.4, 0.4, 0.4)), limit=50.0),
}


def attractor_model(name: str, rot: Optional[np.ndarray] = None, scale: float = 1.0,
                    translate: Sequence[float] = (0.0, 0.0, 0.0)) -> np.ndarray:
    """Model matrix mapping the attractor's native coordinates to a ~unit-radius cloud centred on ``translate``,
    optionally rotated (3x4 or 3x3) and scaled.  Lorenz/Chen/Rossler have z as their long axis: pass
    rot=mat_rot((1,0,0), -pi/2) to stand them up along +y."""
    a = ATTRACTORS[name]
    m = mat_mul(mat_scale(1.0 / a["radius"]), mat_translate([-c for c in a["centre"]]))
    if rot is not None:
        r = np.asarray(rot, np.float64)
        if r.shape == (3, 3):
            r = np.concatenate([r, np.zeros((3, 1))], axis=1)
        m = mat_mul(r, m)
    return mat_mul(mat_translate(translate), mat_mul(mat_scale(scale), m))


def _attr_f_np(kind: int, p: np.ndarray, k) -> np.ndarray:
    x, y, z = p[:, 0], p[:, 1], p[:, 2]
    if kind == 0:
        return np.stack([k[0] * (y - x), x * (k[1] - z) - y, x * y - k[2] * z], 1)
    if kind == 1:
        return np.stack([(z - k[1]) * x - k[3] * y, k[3] * x + (z - k[1]) * y,
                         k[2] + k[0] * z - z ** 3 / 3 - (x * x + y * y) * (1 + k[4] * z) + k[5] * z * x ** 3], 1)
    if kind == 2:
        return np.stack([np.sin(y) - k[0] * x, np.sin(z) - k[0] * y, np.sin(x) - k[0] * z], 1)
    if kind == 3:
        return np.stack([-k[0] * x - 4 * y - 4 * z - y * y, -k[0] * y - 4 * z - 4 * x - z * z,
                         -k[0] * z - 4 * x - 4 * y - x * x], 1)
    if kind == 4:
        return np.stack([y - k[0] * x + k[1] * y * z, k[2] * y - x * z + z, k[3] * x * y - k[4] * z], 1)
    if kind == 5:
        return np.stack([-y - z, x + k[0] * y, k[1] + z * (x - k[2])], 1)
    if kind == 6:
        return np.stack([k[0] * (y - x), (k[2] - k[0]) * x - x * z + k[2] * y, x * y - k[1] * z], 1)
    if kind == 7:
        return np.stack([k[0] * x + y * z, k[1] * x + k[2] * y - x * z, -z - x * y], 1)
    return np.stack([y + k[0] * x * y + x * z, 1 - k[1] * x * x + y * z, x - x * x - y * y], 1)


def seed_attractor(name: str, n: int, seed: int = 1, warm_steps: int = 600, params=None) -> np.ndarray:
    """(n, 4) float32 start states already on the attractor (host RK2 warm-up of up to 65536 states from the
    seed box, then tiled to n with a tiny jitter; w = 1).  Chaos spreads the copies apart within seconds."""
    a = ATTRACTORS[name]
    k = a["k"] if params is None else tuple(params) + (0,) * (6 - len(params))
    rng = np.random.default_rng(seed)
    c, e = np.asarray(a["spawn"][0]), np.asarray(a["spawn"][1])
    m = min(n, 65536)
    p = c + e * rng.uniform(-1, 1, (m, 3))
    dt = a["dt"]
    for _ in range(warm_steps):
        k1 = _attr_f_np(a["kind"], p, k)
        p = p + dt * _attr_f_np(a["kind"], p + 0.5 * dt * k1, k)
        bad = ~np.isfinite(p).all(1) | (np.abs(p).sum(1) > a["limit"])
        if bad.any():
            p[bad] = c + e * rng.uniform(-1, 1, (int(bad.sum()), 3))
    reps = p[rng.integers(0, m, n)] + rng.normal(0, 1e-3 * a["radius"], (n, 3))
    out = np.ones((n, 4), np.float32)
    out[:, :3] = reps
    return out


class ParticleSim:
    """GPU particle integrators over (N,4) float32 device arrays (xyz + w, w = fade-in weight 0..1)."""

    def __init__(self, ctx) -> None:
        mod = _module()
        self.k_attr = mod.get_function("sp_attractor")
        self.k_curl = mod.get_function("sp_curl_step")
        self.k_push = mod.get_function("sp_trail_push")
        self.k_fill = mod.get_function("sp_trail_fill")
        self._s = np.zeros(1, _SIM_DTYPE)

    def attractor(self, pos, name: str, dt: float, *, steps: int = 4, method: str = "rk2", params=None,
                  n: Optional[int] = None, seed: int = 1, frame: int = 0, fade_in: float = 0.05) -> None:
        """Advance ``steps`` x ``dt`` native time units in place (ATTRACTORS[name]['dt'] is a stable step)."""
        a = ATTRACTORS[name]
        s = self._s[0]
        k = a["k"] if params is None else tuple(params) + (0,) * (6 - len(params))
        s["k"] = k[:6]
        s["dt"], s["fade_in"], s["limit"] = dt, fade_in, a["limit"]
        s["spawn_c"], s["spawn_e"] = a["spawn"][0], a["spawn"][1]
        s["kind"], s["method"], s["steps"] = a["kind"], METHODS.index(method), min(int(steps), 64)
        count = int(pos.shape[0]) if n is None else int(n)
        s["n"], s["seed"], s["frame"] = count, seed, frame & 0x7FFFFFFF
        if count > 0 and steps > 0:
            self.k_attr(((count + 255) // 256,), (256,), (pos, s))

    def advance(self, pos, name: str, native_time: float, *, method: str = "rk2", params=None,
                n: Optional[int] = None, seed: int = 1, frame: int = 0, fade_in: float = 0.05) -> None:
        """Per frame: advance ``native_time`` (typically ATTRACTORS[name]['rate'] * speed * dt) in
        ceil(T / stable dt) equal steps (<= 64).  No-op for T <= 0 (dt = 0 first frame, speed 0)."""
        if not native_time > 0.0:
            return
        a = ATTRACTORS[name]
        steps = min(64, max(1, int(math.ceil(native_time / a["dt"]))))
        self.attractor(pos, name, native_time / steps, steps=steps, method=method, params=params, n=n, seed=seed,
                       frame=frame, fade_in=fade_in)

    def seed_attractor(self, pos, name: str, *, seed: int = 1, steps: int = 3000, params=None) -> None:
        """Setup-time: fill ``pos`` ((N,4) float32 device array) with states ON ATTRACTORS[name]: uniform starts
        in the seed box (numpy, one upload) integrated ``steps`` stable steps on the GPU (~ms for 1M); w = 1."""
        a = ATTRACTORS[name]
        n = int(pos.shape[0])
        rng = np.random.default_rng(seed)
        c, e = np.asarray(a["spawn"][0]), np.asarray(a["spawn"][1])
        h = np.ones((n, 4), np.float32)
        h[:, :3] = c + e * rng.uniform(-1, 1, (n, 3))
        pos.set(h)
        done = 0
        while done < steps:
            k = min(64, steps - done)
            self.attractor(pos, name, a["dt"], steps=k, params=params, seed=seed, frame=done, fade_in=1.0)
            done += k

    def curl(self, pos, dt: float, *, scale: float = 1.0, strength: float = 1.0, offset=(0.0, 0.0, 0.0),
             seed: int = 1, octaves: int = 2, method: str = "rk2", steps: int = 1, centre=(0.0, 0.0, 0.0),
             pull: float = 0.0, swirl: float = 0.0, radius: float = 0.0, n: Optional[int] = None, frame: int = 0,
             fade_in: float = 0.05) -> None:
        """Curl-noise advection in place: v = strength * curl(p * scale + offset) - pull * (p - centre)
        + swirl * (spin about +y through centre); radius > 0 respawns leavers inside the sphere."""
        s = self._s[0]
        s["dt"], s["fade_in"], s["scale"], s["strength"] = dt, fade_in, scale, strength
        s["ox"], s["oy"], s["oz"] = offset
        s["cx"], s["cy"], s["cz"] = centre
        s["pull"], s["swirl"], s["radius"] = pull, swirl, radius
        s["method"], s["steps"], s["octaves"] = min(METHODS.index(method), 1), min(int(steps), 16), int(octaves)
        count = int(pos.shape[0]) if n is None else int(n)
        s["n"], s["seed"], s["frame"] = count, seed, frame & 0x7FFFFFFF
        if count > 0:
            self.k_curl(((count + 255) // 256,), (256,), (pos, s))

    def trail_push(self, trails, src, slot: int, *, src_stride4: int = 1) -> None:
        """trails[i, slot] = src[i * src_stride4] (float4; trails (M,K,4)).  Draw with
        Splat3D.polylines(trails, head=(slot + 1) % K, tail_fade=True)."""
        m, K = int(trails.shape[0]), int(trails.shape[1])
        self.k_push(((m + 255) // 256,), (256,), (trails, src, np.int32(m), np.int32(K), np.int32(slot % K),
                                                  np.int32(src_stride4)))

    def trail_fill(self, trails, src, *, src_stride4: int = 1) -> None:
        """Every slot of trail i = src[i * src_stride4] (reset trails, e.g. in setup)."""
        m, K = int(trails.shape[0]), int(trails.shape[1])
        n = m * K
        self.k_fill(((n + 255) // 256,), (256,), (trails, src, np.int32(m), np.int32(K), np.int32(src_stride4)))


# ----------------------------------------------------------------------------------------------- shapes (host)
def _edges_by_length(V: np.ndarray, tol: float = 1e-3) -> np.ndarray:
    """All vertex pairs at the minimum pairwise distance (the edges of every uniform polyhedron)."""
    d = np.linalg.norm(V[:, None] - V[None], axis=2)
    iu = np.triu_indices(len(V), 1)
    dmin = d[iu].min()
    sel = np.abs(d[iu] - dmin) < tol * dmin
    return np.stack([iu[0][sel], iu[1][sel]], 1).astype(np.int32)


def polyhedron(name: str) -> tuple[np.ndarray, np.ndarray]:
    """(V (n,3) float32 on the unit circumsphere, E (m,2) int32) for a named solid."""
    phi = (1 + 5 ** 0.5) / 2
    if name == "tetrahedron":
        V = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], float)
    elif name == "stella":
        V = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1],
                      [-1, -1, -1], [-1, 1, 1], [1, -1, 1], [1, 1, -1]], float)
        V /= np.linalg.norm(V, axis=1, keepdims=True)
        E = np.array([[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3],
                      [4, 5], [4, 6], [4, 7], [5, 6], [5, 7], [6, 7]], np.int32)
        return V.astype(np.float32), E
    elif name == "cube":
        V = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    elif name == "octahedron":
        V = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], float)
    elif name == "icosahedron":
        V = np.array([[0, s1, s2 * phi] for s1 in (-1, 1) for s2 in (-1, 1)]
                     + [[s1, s2 * phi, 0] for s1 in (-1, 1) for s2 in (-1, 1)]
                     + [[s2 * phi, 0, s1] for s1 in (-1, 1) for s2 in (-1, 1)], float)
    elif name == "dodecahedron":
        V = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
                     + [[0, s1 / phi, s2 * phi] for s1 in (-1, 1) for s2 in (-1, 1)]
                     + [[s1 / phi, s2 * phi, 0] for s1 in (-1, 1) for s2 in (-1, 1)]
                     + [[s2 * phi, 0, s1 / phi] for s1 in (-1, 1) for s2 in (-1, 1)], float)
    elif name == "cuboctahedron":
        V = np.array([[a, b, 0] for a in (-1, 1) for b in (-1, 1)] + [[a, 0, b] for a in (-1, 1) for b in (-1, 1)]
                     + [[0, a, b] for a in (-1, 1) for b in (-1, 1)], float)
    elif name == "icosidodecahedron":
        V = [[0, 0, s * phi] for s in (-1, 1)] + [[0, s * phi, 0] for s in (-1, 1)] + [[s * phi, 0, 0] for s in (-1, 1)]
        for s1 in (-1, 1):
            for s2 in (-1, 1):
                for s3 in (-1, 1):
                    a, b, c = s1 * 0.5, s2 * phi / 2, s3 * phi * phi / 2
                    V += [[a, b, c], [c, a, b], [b, c, a]]
        V = np.array(V, float)
    else:
        raise KeyError(name)
    V /= np.linalg.norm(V, axis=1, keepdims=True)
    return V.astype(np.float32), _edges_by_length(V)


POLYHEDRA = ("tetrahedron", "cube", "octahedron", "dodecahedron", "icosahedron", "cuboctahedron",
             "icosidodecahedron", "stella")


def geodesic(freq: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Geodesic sphere: icosahedron faces subdivided ``freq`` times per edge, projected to the unit sphere."""
    V0 = polyhedron("icosahedron")[0].astype(np.float64)
    E0 = _edges_by_length(V0)
    adj: dict[int, set] = {i: set() for i in range(len(V0))}
    for a, b in E0:
        adj[int(a)].add(int(b))
        adj[int(b)].add(int(a))
    faces = sorted({tuple(sorted((a, b, c))) for a in adj for b in adj[a] for c in adj[a] & adj[b]})
    verts: list[np.ndarray] = []
    index: dict[tuple, int] = {}

    def vid(p):
        p = p / np.linalg.norm(p)
        key = tuple(np.round(p, 6))
        if key not in index:
            index[key] = len(verts)
            verts.append(p)
        return index[key]

    edges = set()
    n = max(1, int(freq))
    for a, b, c in faces:
        A, B, C = V0[a], V0[b], V0[c]
        grid = {}
        for i in range(n + 1):
            for j in range(n + 1 - i):
                grid[i, j] = vid(A + (B - A) * i / n + (C - A) * j / n)
        for i in range(n + 1):
            for j in range(n + 1 - i):
                for di, dj in ((1, 0), (0, 1), (-1, 1)):
                    if (i + di, j + dj) in grid:
                        u, v = grid[i, j], grid[i + di, j + dj]
                        edges.add((min(u, v), max(u, v)))
    return np.asarray(verts, np.float32), np.asarray(sorted(edges), np.int32)


def torus_knot(p: int = 2, q: int = 3, n: int = 512, R: float = 1.0, r: float = 0.4) -> np.ndarray:
    """(n, 3) points of a (p, q) torus knot (open: append the first point to close it)."""
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    rr = R + r * np.cos(q * t)
    return np.stack([rr * np.cos(p * t), r * np.sin(q * t), rr * np.sin(p * t)], 1).astype(np.float32)


def ring(n: int = 128, r: float = 1.0) -> np.ndarray:
    """Closed circle in the xz plane as an (n+1, 3) polyline (the last vertex repeats the first)."""
    t = np.linspace(0, 2 * np.pi, n + 1)
    return np.stack([r * np.cos(t), np.zeros_like(t), r * np.sin(t)], 1).astype(np.float32)


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    y = 1 - 2 * i / n
    rr = np.sqrt(1 - y * y)
    th = np.pi * (3 - 5 ** 0.5) * i
    return np.stack([rr * np.cos(th), y, rr * np.sin(th)], 1).astype(np.float32)


def grid_lines(nx: int = 20, nz: int = 20, size: float = 10.0, y: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    xs = np.linspace(-size, size, nx + 1)
    zs = np.linspace(-size, size, nz + 1)
    p0 = [[x, y, -size] for x in xs] + [[-size, y, z] for z in zs]
    p1 = [[x, y, size] for x in xs] + [[size, y, z] for z in zs]
    return np.asarray(p0, np.float32), np.asarray(p1, np.float32)


def galaxy_disc(n: int, arms: int = 2, seed: int = 1, radius: float = 1.0, pitch_deg: float = 14.0,
                arm_spread: float = 0.35, thickness: float = 0.04, bulge: float = 0.15, core: float = 0.15) -> np.ndarray:
    """Static spiral-galaxy star field (n,4): xyz (disc in the xz plane, ~unit radius) + weight (brightness).
    Exponential disc (scale 0.32, truncated at 1.2) with log-spiral arms that stop winding inside ``core`` (an
    unbounded log spiral packs into nested rings near r = 0, which project to bright stripes)."""
    rng = np.random.default_rng(seed)
    nb = int(n * bulge)
    nd = n - nb
    u = rng.uniform(0, 1, nd)
    r = -0.32 * np.log1p(-u * (1 - math.exp(-1.2 / 0.32))) * radius        # truncated exponential (no rim ring)
    arm = rng.integers(0, arms, nd)
    rn = r / radius
    wind = np.log(np.maximum(rn, core)) / math.tan(math.radians(pitch_deg))
    spread = arm_spread * (0.6 + 0.8 * rn) * (1.0 + 2.5 * np.exp(-rn / core))  # arms dissolve into the core
    th = wind + 2 * np.pi * arm / arms + rng.normal(0, 1, nd) * spread
    y = rng.normal(0, thickness, nd) * radius * (1.0 - 0.6 * np.minimum(r / radius, 1))
    out = np.ones((n, 4), np.float32)
    out[:nd, :3] = np.stack([r * np.cos(th), y, r * np.sin(th)], 1)
    out[nd:, :3] = rng.normal(0, 1, (nb, 3)) * np.array([0.12, 0.08, 0.12]) * radius
    out[:, 3] = rng.uniform(0.3, 1.0, n) ** 2
    return out


# ----------------------------------------------------------------------------------------------- bench
def bench(ctx=None, gpu: int = 1, out_w: int = 3840, out_h: int = 2160, scale: float = 0.5,
          n_points: int = 1_000_000, n_lines: int = 20_000, frames: int = 60) -> dict:
    """Pipelined ms/frame for: clear + 1M random-order galaxy points + 20k short segments + glow + 4K resolve."""
    import time
    if ctx is None:
        from stream_service.gpu import RenderContext
        ctx = RenderContext(gpu, out_w, out_h, 60, prealloc_buffers=0)
    cp = ctx.cp
    with ctx.device, ctx.stream:
        w, h = int(out_w * scale) // 2 * 2, int(out_h * scale) // 2 * 2
        sp = Splat3D(ctx, w, h, max_points=n_points, max_lines=n_lines)
        g = galaxy_disc(n_points, seed=3) * np.array([3, 3, 3, 1], np.float32)
        pts = cp.asarray(g[np.random.default_rng(0).permutation(n_points)])
        rng = np.random.default_rng(2)
        a = rng.normal(0, 1, (n_lines, 4)).astype(np.float32)
        a[:, :3] /= np.linalg.norm(a[:, :3], axis=1, keepdims=True)
        b = a + rng.normal(0, 0.08, (n_lines, 4)).astype(np.float32)
        a[:, 3] = b[:, 3] = 1
        p0, p1 = cp.asarray(a * 1.5), cp.asarray(b * 1.5)
        out = cp.empty((out_h, out_w, 4), cp.uint8)
        cam = look_at(orbit((0, 0, 0), 6.0, 0.4, 0.5), (0, 0, 0), fov_deg=50)

        def frame():
            sp.clear()
            sp.points(pts, cam=cam, intensity=0.05, size_px=1.0, palette="neon", weight_in_w=True)
            sp.lines(p0, p1, cam=cam, intensity=1.0, width_px=1.5, palette="ice")
            sp.resolve(out, exposure=1.0, glow=0.6)

        for _ in range(10):
            frame()
        e0, e1 = cp.cuda.Event(), cp.cuda.Event()
        e0.record()
        t0 = time.perf_counter()
        for _ in range(frames):
            frame()
        t1 = time.perf_counter()
        e1.record()
        e1.synchronize()
        res = {"ms_per_frame": cp.cuda.get_elapsed_time(e0, e1) / frames,
               "host_ms_per_frame": 1e3 * (t1 - t0) / frames}
        sp.close()
    return res


__all__: Iterable[str] = (
    "SPLAT3D_SRC", "SPLAT3D_OPTIONS", "Splat3D", "Cam", "look_at", "orbit", "mat_identity", "mat_rot", "mat_euler",
    "mat_scale", "mat_translate", "mat_mul", "mat_trs", "palette_arg", "srgb_to_linear", "ParticleSim",
    "ATTRACTORS", "attractor_model", "seed_attractor", "polyhedron", "POLYHEDRA", "geodesic", "torus_knot", "ring",
    "fibonacci_sphere", "grid_lines", "galaxy_disc", "bench")
