"""smfx frame3d toolkit: map the INPUT FRAME onto 3D shapes in per-pixel CUDA kernels (projection effects, 3D
transitions).  Analytic ray casts, no SDF marching except the torus/heightfield helpers.

Source: Original (smfx).  Builds on smfx/_lib/sdf3d.py (float3 maths, Mat3, Cam, iBox/iSphere/iPlane, sdBox/sdTorus;
        MIT, see that file).  Textbook pieces implemented from scratch: Schlick's Fresnel approximation (1994), the
        GLSL refract() formula (Snell), YIQ hue rotation, mirror-ball environment lookup, sum-of-sines water.
Author: smfx
License: MIT
license_class: permissive

USE
---
FRAME3D needs HELPERS (bilin, ld, pack, hash1), PALETTE_CUDA (pal) and SDF3D (maths, Mat3, Cam, iBox, ...) before it:

    from stream_service.gpu import raw_module
    from stream_service.plugins._lib.cuda import HELPERS, launch_2d
    from stream_service.plugins._lib.palettes import PALETTE_CUDA
    from stream_service.plugins._lib.sdf3d import SDF3D
    from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, camera_orbit, flat_distance, ...
    _SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r'''  ...your kernel...  '''
    k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("my_effect")

WORLD CONVENTION (the "flat" identity)
--------------------------------------
y up, right-handed, the camera looks down -z (so screen-right = +x).  The canonical FRAME QUAD is the input frame
as a rectangle in the z = 0 plane: x in [-A, A], y in [-1, 1] (A = W / H, half-height 1), facing +z; uv (0, 0) is
the image's top-left, uv = (px / W, py / H).  The FLAT CAMERA sits at (0, 0, D), D = flat_distance(fov) =
1 / tan(fov / 2), looking at the origin: its ray through pixel (x + .5, y + .5) hits the frame quad exactly at that
pixel's uv, so sampling there reproduces the input bit-exactly (self-test: max error 0).  Morph from it smoothly:
orbit / dolly the camera (camera_orbit(target=(0, 0, -z0), dist=D + z0, yaw=0, pitch=0) is still the flat pose, so
orbit about an object's centre z0 behind the frame plane), or move the object with an Xf (identity Xf = flat).
Effects: at amount 0 skip the kernel and ``cp.copyto(out, src)`` (exact identity); make every 3D term (dolly,
rotation, lighting k, edge lines) scale with a morph m that has zero slope at 0 (m = smoothstep(amount)), so the
first kernel frames differ from the copy by < 1/255.

COLOUR: frame samples are sRGB-encoded 0..1 float3 (like pal()); relighting multiplies in sRGB space (cheap, and
exactly identity at k = 0).  Store with store_rgb (no dither) where you show the frame, store_srgb (sdf3d, +-1
LSB dither) for smooth generated gradients.

API (all __device__; "&" = out-param; t in the units of the ray you pass)
------------------------------------------------------------------------
Kernel args  FRAME_ARGS macro = const uchar4* src, int W, int H, int nlev, 7 x u64 level texture handles
             (ftx0..ftx6), u64 blur handle ftxb (host: FrameMips.args(src) gives exactly these 12);
             TEX_FROM_ARGS -> const Tex T.  Two frames (transitions): FRAME_ARGS_AS(a), FRAME_ARGS_AS(b) and
             TEX_FROM_ARGS_AS(a, TA); TEX_FROM_ARGS_AS(b, TB) (host: *mips_a.args(a), *mips_b.args(b)).
Texture      struct Tex {src, W, H, nlev, t0..t6, tb} (hardware textures: bilinear, clamp, normalised uv)
             tex_handle(T, k) (level k handle, 0 = src)  tex_rgb_at(handle, u, v) (any tex_object() texture)
             tex_rgb(float4 0..255) -> float3 0..1
             tex_fetch(T, x, y) (exact texel)   tex_sample0(T, u, v) (bilinear, level 0, clamp-to-edge)
             tex_sample(T, u, v, lod) (trilinear over the mip chain; lod <= 0 = level 0 bilinear)
             tex_sample_near(T, u, v, lod) (bilinear on the nearest level only: cheap blurry reflections)
             tex_blur(T, u, v) (the FrameMips Gaussian-blurred backdrop level, bilinear; falls back to coarsest mip)
             tex_bilin_uv(img, w, h, u, v) (software bilinear of a raw uchar4 buffer, no texture object)
             tex_lod(t, pa, cosa, ndv, texel, bias) -> mip level for a hit at ray distance t with pixel angle pa
                 (pixel_angle(cam, h_out)), cosa = dot(rd, cam.fwd), ndv = |dot(rd, n)|, texel = world size of one
                 src texel on that surface (frame quad / box face of height 2: 2 / H).  Exactly 0 + bias for the
                 flat camera on the frame quad; bias -0.25..-0.5 keeps it crisp, +1 for glossy reflections.
             frame_texel(T) = 2 / H (texel size on a height-2 surface)
UV           uv_repeat(u) uv_mirror(u) (mirrored repeat)  uv_cover(uv, face_aspect, img_aspect) (centre-crop the
             image to fill a face of another aspect)  uv_inside(uv)  uv_edge_px(uv, face_size, fp) (screen pixels
             from uv to the face border; fp = world size of a pixel at the hit = t * pa * cosa)
Camera       cam_flat(tan_half) (the identity camera above; or cam_load(P) of camera_flat()/camera_orbit())
             + sdf3d: Cam, cam_load, cam_dir(cam, px, py, w, h), cam_ray, pixel_angle(cam, h)
Transform    struct Xf {Mat3 R; float3 o; float s;}   world = R * (s * p_obj) + o
             xf_load(P) (13 floats, host xform_pack)  xf_identity()  xf_ray(X, ro, rd, oro&, ord&) (t_obj = t / s)
             xf_dir(X, v_obj) (normals to world)  xf_point(X, p_obj)  xf_inv_point(X, p_world)
Hits         struct FHit {float t; float2 uv; float3 n; int face;}  (t < 0 = miss; n faces the ray; uv 0..1,
             image upright as seen from the outside)  fhit_miss()  fhit_ok(h)
             ihit_quad(ro, rd, c, ex, ey, infinite=false): rectangle centred c, half-axis ex towards image-right,
                 ey towards image-DOWN; face 0 = front (normal cross(ey, ex)), 1 = back (seen mirrored)
             ihit_frame(ro, rd, A): the canonical frame quad (c 0, ex (A,0,0), ey (0,-1,0))
             ihit_box(ro, rd, hb): box of half size hb at the origin; face 0 +x 1 -x 2 +y 3 -y 4 +z (front) 5 -z;
                 side faces read upright with +y up, top seen from above (v grows towards +z), bottom from below;
                 adjacent side faces continue the image (+x face's u=0 edge meets the +z face's u=1 edge); a ray
                 starting inside gets the exit wall (n inward).  box_face_uv(p, hb, face)  box_face_size(hb, face)
                 box_face_normal(face)  box_around(face) -> 0 front, 1 right, 2 back, 3 left, 4 top, 5 bottom
             ihit_sphere(ro, rd, r) (equirect uv: u = 0.5 + atan2(x, z) / 2pi so u = .5 faces +z, v = acos(y/r)/pi)
                 uv_equirect(p) (unit-free)  uv_planar_z(p, hw, hh) (front decal: u = .5 + .5 x/hw, v = .5 - .5 y/hh)
             ihit_cylinder(ro, rd, r, hh, inside): y axis, |y| <= hh; outside: outer wall (face 0) or the inner wall
                 seen through an open end (face 1); inside = true: inner wall only (tunnels / panoramas), u flipped
                 so the image reads correctly from within; v = .5 - .5 y / hh
             ihit_torus(ro, rd, R, r, steps): ring in the xz plane about y (sdTorus march, 24-40 steps, bounded by
                 iSphere); u around the ring (u = .5 at +z), v around the tube (v = .5 on the outer equator)
             hf_march(hf, ro, rd, tmin, tmax, steps) (template; hf = lambda float(float x, float z); y = hf(x, z);
                 uniform steps + 4 secant refinements; -1 = none)  hf_normal(hf, x, z, e)
Shading      fresnel_schlick(cosi, f0)  refract3(i, n, eta) (GLSL refract; 0 on total internal reflection)
             spec_blinn(n, v, l, shininess) (v, l towards eye / light)
             light_frame(c, n, l, fwd, amb, k): relight a frame colour; a surface facing the camera head-on is
                 unchanged at any k, k = 0 is identity
             hue_rotate(c, radians) (YIQ)  saturation(c, s)  luma3(c)
             env_frame(T, dir): mirror-ball lookup of the blurred input (cheap glossy reflections of "the room")
             env_softbox(dir) -> 0..1: a soft studio light above/behind the camera (a sheen that sweeps over faces)
Background   bg_blur(T, u, v, zoom, dim, vig): darkened blurred input at screen uv (zoom >= 1 hides edges)
             bg_gradient(pal12, u, v, phase, dim): palette gradient  vignette_k(u, v, amt) -> multiplier
Shadow/occ   occ_box(p_obj, hb, radius) -> 0 touching .. 1 far (soft contact shadow under a box)
             occ_sphere(p, n, c, r) -> 0..1 (form-factor style sphere occlusion)
Water        WaterS {h; float2 g (gradient); lap (laplacian: caustic hint)}  water_eval(Wp, float2 q)
                 (Wp = host Water.buf: directional waves + expanding ripple rings spawned on the pulse)
             water_normal_y(s) (surface in world xz, y up)  water_normal_z(s) (surface over the frame plane, +z)
             water_refract_offset(n_z, depth, ior) -> float2 lateral shift of the floor lookup (picture under water,
                 looking down -z)  water_caustic(lap, depth, gain) -> brightness multiplier
AA / store   aa_cover4(cam, px, py, w, h, hit, sub_rd&) (template: fraction of 4 rotated-grid sub-rays with
                 hit(rd) true, sub_rd = one that hit; analytic silhouettes stop crawling)
             box_cover_class(oro, ord, hb, delta) -> 0 miss / 1 inside / 0.5 silhouette (only then pay aa_cover4;
                 delta ~ 1.5 * pa * length(oro), see effects/frame_cube.py)
             sphere_cover(ro, rd, r, fp) -> analytic ~1 px silhouette coverage of a sphere at the origin
             store_rgb(out, idx, c) (no dither)  store_rgb_dither(out, x, y, w, c, amt)

USAGE EXAMPLE (also exported as SKELETON and compiled by this module's self-test): the frame as a card that tilts
back and spins as amount rises, over a blurred backdrop, identity at m = 0.

    // P: [0..12] camera (camera_orbit / camera_flat)  [13..25] card xform (xform_pack)  [26] m  [27] backdrop dim
    SDF_KERNEL
    void frame_card(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P) {
        const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
        if (x >= w || y >= h) return;
        TEX_FROM_ARGS;
        const Cam cam = cam_load(P);
        const Xf X = xf_load(P + 13);
        const float m = P[26], A = (float)W / H, pa = pixel_angle(cam, h);
        const float px = x + 0.5f, py = y + 0.5f;
        const float3 rd = cam_dir(cam, px, py, w, h);
        float3 col = bg_blur(T, px / w, py / h, 1.1f, P[27], 0.8f);
        float3 sub = rd;
        auto hits = [&](float3 d) { float3 a, b; xf_ray(X, cam.eye, d, a, b); return fhit_ok(ihit_frame(a, b, A)); };
        const float cover = aa_cover4(cam, px, py, w, h, hits, sub);
        float3 ro_o, rd_o; xf_ray(X, cam.eye, hits(rd) ? rd : sub, ro_o, rd_o);
        const FHit hit = ihit_frame(ro_o, rd_o, A);
        if (fhit_ok(hit) && cover > 0.f) {
            const float3 n = xf_dir(X, hit.n);
            const float t = hit.t * X.s, cosa = dot(rd, cam.fwd);
            const float lod = tex_lod(t, pa, cosa, fabsf(dot(rd, n)), frame_texel(T) * X.s, -0.25f);
            float3 c = tex_sample(T, hit.face ? 1.f - hit.uv.x : hit.uv.x, hit.uv.y, lod);   // back side unmirrored
            c = light_frame(c, n, normalize(f3(-0.4f, 0.5f, 0.8f)), cam.fwd, 0.25f, m);
            col = mix(col, c, cover);
        }
        store_rgb(out, y * w + x, col);
    }

Host side (setup):  self.mips = FrameMips(ctx, levels=4)     (preallocates; one per input frame you sample)
Host side (render, per frame; nothing allocated):
    if p["amount"] <= 1e-4: cp.copyto(out, src); return
    m = smoothstep01(p["amount"]); self.mips.build(src)
    D = flat_distance(40.0)
    cam = camera_orbit((0, 0, 0), D * (1 + 0.8 * m), yaw=0.3 * m, pitch=0.15 * m, fov_deg=40.0)
    xf = xform_pack(rot_euler(yaw=self.spin.angle), (0, 0, 0), 1.0)
    self.P.set(np.asarray([*cam, *xf, m, 0.3], np.float32))
    launch_2d(k, w, h, (out, np.int32(w), np.int32(h), *self.mips.args(src), self.P))

Host helpers: flat_distance(fov) camera_flat(fov) camera_orbit(target, dist, yaw, pitch, roll, fov) (yaw 0 / pitch 0 =
on the +z side looking -z; yaw > 0 swings the camera to the right, pitch > 0 raises it) rot_euler(yaw, pitch,
roll) (R = Ry Rx Rz, 9 floats) xform_pack(rot9, origin, scale) box_support_y(rot9, hb) (floor height under a
rotated box) smoothstep01(x) FrameMips(ctx, levels=4, blur_level=None, blur_sigma=2.5) (.build(src), .args(src))
tex_object(arr) -> u64 texture handle of an (h, w, 4) uint8 buffer with 32-byte row pitch; tex_buffer(cp, w, h)
allocates such a buffer (an internal render target you then sample with tex_rgb_at, e.g. a quarter-res pass)
HomingAngle (a spin that integrates a rate and springs home to a multiple of `period` as a gate closes, so amount 0
is identity) Water(ctx, ...) (waves + pulse-spawned ripple rings -> .buf for water_eval).

Budget notes (P4000, 3840x2160): FrameMips.build(levels=4) ~0.2 ms (one read of the 4K frame); a cube with AA,
trilinear faces, a floor reflection and the blurred backdrop ~1 ms at full res.  Prefer full res for anything
that must morph from identity (a half-res buffer cannot match the crisp input at small amounts).
"""
from __future__ import annotations

import math
from collections import OrderedDict, deque
from typing import Iterable, Optional, Sequence

import numpy as np

from stream_service.plugins._lib.sdf3d import SDF3D_OPTIONS, camera_pack, rot_matrix

FRAME3D_OPTIONS: tuple[str, ...] = SDF3D_OPTIONS
XF_FLOATS = 13                      # xform_pack(): R (9, row-major) origin (3) scale (1)
WATER_HDR = 4
WAVE_FLOATS = 5
RING_FLOATS = 6
WATER_MAX_WAVES = 8
WATER_MAX_RINGS = 8
WATER_FLOATS = WATER_HDR + WATER_MAX_WAVES * WAVE_FLOATS + WATER_MAX_RINGS * RING_FLOATS
MAX_LEVELS = 6

FRAME3D = r"""
// ==================================================================== smfx frame3d (see smfx/_lib/frame3d.py)
#define F3D_MAXLEV 6
#define F3D_PI 3.14159265f
#define F3D_TAU 6.28318531f

// -------------------------------------------------------------------- texture: input frame + FrameMips chain
// Every level is a hardware texture (pitch-2D view of a frame buffer; bilinear, clamp-to-edge, normalised uv,
// 8-bit channels read as 0..1).  Level 0 is the input frame itself (FrameMips caches a texture object per input
// buffer); levels 1..nlev and the blurred backdrop belong to FrameMips.  One tex2D = one bilinear sample, so a
// trilinear sample is two texture instructions instead of ~120 ALU ops.  Handles are 64-bit kernel arguments.
#define FRAME_ARGS_AS(p) const uchar4* __restrict__ p##_src, const int p##_W, const int p##_H, const int p##_nlev, \
    const unsigned long long p##_t0, const unsigned long long p##_t1, const unsigned long long p##_t2, \
    const unsigned long long p##_t3, const unsigned long long p##_t4, const unsigned long long p##_t5, \
    const unsigned long long p##_t6, const unsigned long long p##_tb
#define TEX_FROM_ARGS_AS(p, NAME) const Tex NAME = tex_make(p##_src, p##_W, p##_H, p##_nlev, p##_t0, p##_t1, \
    p##_t2, p##_t3, p##_t4, p##_t5, p##_t6, p##_tb)
#define FRAME_ARGS const uchar4* __restrict__ src, const int W, const int H, const int nlev, \
    const unsigned long long ftx0, const unsigned long long ftx1, const unsigned long long ftx2, \
    const unsigned long long ftx3, const unsigned long long ftx4, const unsigned long long ftx5, \
    const unsigned long long ftx6, const unsigned long long ftxb
#define TEX_FROM_ARGS const Tex T = tex_make(src, W, H, nlev, ftx0, ftx1, ftx2, ftx3, ftx4, ftx5, ftx6, ftxb)

struct Tex { const uchar4* src; int W, H, nlev; unsigned long long t0, t1, t2, t3, t4, t5, t6, tb; };
__device__ __forceinline__ Tex tex_make(const uchar4* src, int W, int H, int nlev, unsigned long long t0,
                                        unsigned long long t1, unsigned long long t2, unsigned long long t3,
                                        unsigned long long t4, unsigned long long t5, unsigned long long t6,
                                        unsigned long long tb) {
    Tex T; T.src = src; T.W = W; T.H = H; T.nlev = min(max(nlev, 0), F3D_MAXLEV);
    T.t0 = t0; T.t1 = t1; T.t2 = t2; T.t3 = t3; T.t4 = t4; T.t5 = t5; T.t6 = t6; T.tb = tb;
    return T;
}
// Level k's texture handle (k clamped to 0..nlev; a select chain, no local-memory array).
__device__ __forceinline__ unsigned long long tex_handle(const Tex& T, int k) {
    k = min(max(k, 0), T.nlev);
    unsigned long long h = T.t0;
    h = k == 1 ? T.t1 : h; h = k == 2 ? T.t2 : h; h = k == 3 ? T.t3 : h;
    h = k == 4 ? T.t4 : h; h = k == 5 ? T.t5 : h; h = k == 6 ? T.t6 : h;
    return h;
}
// Bilinear sample of any FrameMips/tex_object() texture at uv (0..1, pixel centres at (i + .5) / w), 0..1 sRGB.
__device__ __forceinline__ float3 tex_rgb_at(unsigned long long h, float u, float v) {
    const float4 c = tex2D<float4>((cudaTextureObject_t)h, u, v);
    return make_float3(c.x, c.y, c.z);
}
__device__ __forceinline__ float3 tex_rgb(float4 c) { return make_float3(c.x, c.y, c.z) * (1.f / 255.f); }
// Exact texel of the input (integer pixel coords, clamped): the identity path.
__device__ __forceinline__ float3 tex_fetch(const Tex& T, int x, int y) {
    x = min(max(x, 0), T.W - 1); y = min(max(y, 0), T.H - 1);
    const uchar4 p = T.src[y * T.W + x];
    return make_float3(p.x, p.y, p.z) * (1.f / 255.f);
}
__device__ __forceinline__ float3 tex_sample0(const Tex& T, float u, float v) { return tex_rgb_at(T.t0, u, v); }
// Trilinear: bilinear on levels floor(lod) and floor(lod) + 1, blended (lod <= 0 gives level 0 exactly).
__device__ __forceinline__ float3 tex_sample(const Tex& T, float u, float v, float lod) {
    lod = fminf(fmaxf(lod, 0.f), (float)T.nlev);
    const int l0 = (int)lod;
    const float f = lod - (float)l0;
    const float3 a = tex_rgb_at(tex_handle(T, l0), u, v), b = tex_rgb_at(tex_handle(T, l0 + 1), u, v);
    return a + (b - a) * f;
}
// Cheaper: bilinear on the nearest level only (blurry reflections, backgrounds).
__device__ __forceinline__ float3 tex_sample_near(const Tex& T, float u, float v, float lod) {
    return tex_rgb_at(tex_handle(T, (int)fmaxf(lod + 0.5f, 0.f)), u, v);
}
// The Gaussian-blurred backdrop level (FrameMips blur_level; the coarsest mip when built without one).
__device__ __forceinline__ float3 tex_blur(const Tex& T, float u, float v) { return tex_rgb_at(T.tb, u, v); }
// Software bilinear of a raw (h, w) uchar4 buffer (no texture object needed), clamp-to-edge, 0..1.
__device__ __forceinline__ float3 tex_bilin_uv(const uchar4* __restrict__ img, int w, int h, float u, float v) {
    const float fx = u * (float)w - 0.5f, fy = v * (float)h - 0.5f;
    const float flx = floorf(fx), fly = floorf(fy);
    const float tx = fx - flx, ty = fy - fly;
    const int xi = (int)flx, yi = (int)fly;
    const int x0 = min(max(xi, 0), w - 1), x1 = min(max(xi + 1, 0), w - 1);
    const int y0 = min(max(yi, 0), h - 1) * w, y1 = min(max(yi + 1, 0), h - 1) * w;
    const uchar4 a = img[y0 + x0], b = img[y0 + x1], c = img[y1 + x0], d = img[y1 + x1];
    const float3 fa = make_float3(a.x, a.y, a.z), fb = make_float3(b.x, b.y, b.z);
    const float3 fc = make_float3(c.x, c.y, c.z), fd = make_float3(d.x, d.y, d.z);
    const float3 top = fa + (fb - fa) * tx, bot = fc + (fd - fc) * tx;
    return (top + (bot - top) * ty) * (1.f / 255.f);
}
__device__ __forceinline__ float frame_texel(const Tex& T) { return 2.f / (float)T.H; }
// Mip level for a hit at ray distance t: pa = pixel_angle(cam, h_out), cosa = dot(rd, cam.fwd) (off-axis pixels
// subtend less), ndv = |dot(rd, n)| (foreshortening), texel = world size of one src texel on the surface.  The
// anisotropic footprint (radial t pa cosa^2 / ndv, tangential t pa cosa) enters as its geometric mean, which is
// exact for the flat camera (0 at every pixel).
__device__ __forceinline__ float tex_lod(float t, float pa, float cosa, float ndv, float texel, float bias) {
    cosa = fmaxf(cosa, 0.05f);
    const float fp = t * pa * cosa * sqrtf(cosa / fmaxf(ndv, 0.03f));
    return log2f(fmaxf(fp, 1e-12f) / texel) + bias;
}

// -------------------------------------------------------------------- uv addressing
__device__ __forceinline__ float uv_repeat(float u) { return u - floorf(u); }
__device__ __forceinline__ float uv_mirror(float u) { const float f = u - 2.f * floorf(0.5f * u); return f > 1.f ? 2.f - f : f; }
__device__ __forceinline__ float2 uv_cover(float2 uv, float face_aspect, float img_aspect) {
    if (face_aspect < img_aspect) uv.x = 0.5f + (uv.x - 0.5f) * (face_aspect / img_aspect);
    else uv.y = 0.5f + (uv.y - 0.5f) * (img_aspect / face_aspect);
    return uv;
}
__device__ __forceinline__ bool uv_inside(float2 uv) { return uv.x >= 0.f && uv.x <= 1.f && uv.y >= 0.f && uv.y <= 1.f; }
__device__ __forceinline__ float uv_edge_px(float2 uv, float2 face_size, float fp) {
    const float du = fminf(uv.x, 1.f - uv.x) * face_size.x, dv = fminf(uv.y, 1.f - uv.y) * face_size.y;
    return fminf(du, dv) / fmaxf(fp, 1e-9f);
}

// -------------------------------------------------------------------- camera / transforms
__device__ __forceinline__ Cam cam_flat(float tan_half) {
    Cam c; c.eye = f3(0.f, 0.f, 1.f / tan_half); c.fwd = f3(0.f, 0.f, -1.f); c.right = f3(1.f, 0.f, 0.f);
    c.up = f3(0.f, 1.f, 0.f); c.tan_half = tan_half; return c;
}
struct Xf { Mat3 R; float3 o; float s; };
__device__ __forceinline__ Xf xf_load(const float* __restrict__ P) {
    Xf X; X.R = mat3_load(P); X.o = load3(P + 9); X.s = P[12]; return X;
}
__device__ __forceinline__ Xf xf_identity() {
    Xf X; X.R.r0 = f3(1.f, 0.f, 0.f); X.R.r1 = f3(0.f, 1.f, 0.f); X.R.r2 = f3(0.f, 0.f, 1.f); X.o = f3(0.f); X.s = 1.f;
    return X;
}
__device__ __forceinline__ void xf_ray(const Xf& X, float3 ro, float3 rd, float3& oro, float3& ord) {
    oro = mul_t(X.R, ro - X.o) * (1.f / X.s); ord = mul_t(X.R, rd);
}
__device__ __forceinline__ float3 xf_dir(const Xf& X, float3 v) { return mul(X.R, v); }
__device__ __forceinline__ float3 xf_point(const Xf& X, float3 p) { return mul(X.R, p * X.s) + X.o; }
__device__ __forceinline__ float3 xf_inv_point(const Xf& X, float3 p) { return mul_t(X.R, p - X.o) * (1.f / X.s); }

// -------------------------------------------------------------------- analytic hits
struct FHit { float t; float2 uv; float3 n; int face; };
__device__ __forceinline__ FHit fhit_miss() {
    FHit h; h.t = -1.f; h.uv = f2(0.f); h.n = f3(0.f, 0.f, 1.f); h.face = -1; return h;
}
__device__ __forceinline__ bool fhit_ok(const FHit& h) { return h.t > 0.f; }

__device__ FHit ihit_quad(float3 ro, float3 rd, float3 c, float3 ex, float3 ey, bool infinite = false) {
    const float3 n = cross(ey, ex);
    const float dn = dot(rd, n);
    if (fabsf(dn) < 1e-12f) return fhit_miss();
    const float t = dot(c - ro, n) / dn;
    if (!(t > 0.f)) return fhit_miss();
    const float3 q = ro + rd * t - c;
    const float u = dot(q, ex) / dot(ex, ex), v = dot(q, ey) / dot(ey, ey);
    if (!infinite && (fabsf(u) > 1.f || fabsf(v) > 1.f)) return fhit_miss();
    FHit h; h.t = t; h.uv = f2(0.5f + 0.5f * u, 0.5f + 0.5f * v);
    const float3 nn = normalize(n);
    h.n = dn < 0.f ? nn : -nn; h.face = dn < 0.f ? 0 : 1;
    return h;
}
__device__ __forceinline__ FHit ihit_frame(float3 ro, float3 rd, float A) {
    return ihit_quad(ro, rd, f3(0.f), f3(A, 0.f, 0.f), f3(0.f, -1.f, 0.f));
}

__device__ __forceinline__ float2 box_face_uv(float3 p, float3 hb, int face) {
    const float qx = p.x / hb.x, qy = p.y / hb.y, qz = p.z / hb.z;
    switch (face) {
        case 0: return f2(0.5f - 0.5f * qz, 0.5f - 0.5f * qy);     // +x: image-right = -z
        case 1: return f2(0.5f + 0.5f * qz, 0.5f - 0.5f * qy);     // -x: image-right = +z
        case 2: return f2(0.5f + 0.5f * qx, 0.5f + 0.5f * qz);     // +y (top, from above): down = +z
        case 3: return f2(0.5f + 0.5f * qx, 0.5f - 0.5f * qz);     // -y (bottom, from below): down = -z
        case 4: return f2(0.5f + 0.5f * qx, 0.5f - 0.5f * qy);     // +z front
        default: return f2(0.5f - 0.5f * qx, 0.5f - 0.5f * qy);    // -z back: image-right = -x
    }
}
__device__ __forceinline__ float2 box_face_size(float3 hb, int face) {
    return face < 2 ? f2(2.f * hb.z, 2.f * hb.y) : (face < 4 ? f2(2.f * hb.x, 2.f * hb.z) : f2(2.f * hb.x, 2.f * hb.y));
}
__device__ __forceinline__ float3 box_face_normal(int face) {
    const float s = (face & 1) ? -1.f : 1.f;
    return face < 2 ? f3(s, 0.f, 0.f) : (face < 4 ? f3(0.f, s, 0.f) : f3(0.f, 0.f, s));
}
// position of a face going round the y axis: 0 front (+z), 1 right (+x), 2 back (-z), 3 left (-x), 4 top, 5 bottom
__device__ __forceinline__ int box_around(int face) {
    return face == 4 ? 0 : face == 0 ? 1 : face == 5 ? 2 : face == 1 ? 3 : face == 2 ? 4 : 5;
}
__device__ FHit ihit_box(float3 ro, float3 rd, float3 hb) {
    const float2 tt = iBox(ro, rd, hb);
    if (tt.x > tt.y || tt.y <= 0.f) return fhit_miss();
    const bool inside = tt.x <= 0.f;
    FHit h; h.t = inside ? tt.y : tt.x;
    const float3 p = ro + rd * h.t;
    const float ax = fabsf(p.x / hb.x), ay = fabsf(p.y / hb.y), az = fabsf(p.z / hb.z);
    int face;
    if (ax >= ay && ax >= az) face = p.x > 0.f ? 0 : 1;
    else if (ay >= az) face = p.y > 0.f ? 2 : 3;
    else face = p.z > 0.f ? 4 : 5;
    h.face = face; h.uv = box_face_uv(p, hb, face);
    h.n = inside ? -box_face_normal(face) : box_face_normal(face);
    return h;
}

__device__ __forceinline__ float2 uv_equirect(float3 p) {
    const float r = length(p);
    return f2(0.5f + atan2f(p.x, p.z) * (1.f / F3D_TAU), acosf(clamp(p.y / fmaxf(r, 1e-9f), -1.f, 1.f)) * (1.f / F3D_PI));
}
__device__ __forceinline__ float2 uv_planar_z(float3 p, float hw, float hh) {
    return f2(0.5f + 0.5f * p.x / hw, 0.5f - 0.5f * p.y / hh);
}
__device__ FHit ihit_sphere(float3 ro, float3 rd, float r) {
    const float2 tt = iSphere(ro, rd, r);
    if (tt.x > tt.y || tt.y <= 0.f) return fhit_miss();
    const bool inside = tt.x <= 0.f;
    FHit h; h.t = inside ? tt.y : tt.x;
    const float3 p = ro + rd * h.t;
    h.n = p * (inside ? -1.f / r : 1.f / r); h.uv = uv_equirect(p); h.face = inside ? 1 : 0;
    return h;
}
__device__ FHit ihit_cylinder(float3 ro, float3 rd, float r, float hh, bool inside) {
    const float a = rd.x * rd.x + rd.z * rd.z;
    if (a < 1e-12f) return fhit_miss();
    const float b = ro.x * rd.x + ro.z * rd.z, c = ro.x * ro.x + ro.z * ro.z - r * r;
    const float disc = b * b - a * c;
    if (disc < 0.f) return fhit_miss();
    const float sq = sqrtf(disc), t0 = (-b - sq) / a, t1 = (-b + sq) / a;
    FHit h = fhit_miss();
    if (!inside && t0 > 0.f && fabsf(ro.y + rd.y * t0) <= hh) { h.t = t0; h.face = 0; }
    else if (t1 > 0.f && fabsf(ro.y + rd.y * t1) <= hh) { h.t = t1; h.face = 1; }
    else return h;
    const float3 p = ro + rd * h.t;
    const float3 nr = f3(p.x / r, 0.f, p.z / r);
    h.n = h.face == 0 ? nr : -nr;
    const float ang = atan2f(p.x, p.z) * (1.f / F3D_TAU);
    h.uv = f2(h.face == 0 ? 0.5f + ang : 0.5f - ang, 0.5f - 0.5f * p.y / hh);
    return h;
}
__device__ FHit ihit_torus(float3 ro, float3 rd, float R, float r, int steps) {
    const float2 bb = iSphere(ro, rd, R + r);
    if (bb.x > bb.y || bb.y <= 0.f) return fhit_miss();
    float t = fmaxf(bb.x, 0.f);
    const float eps = 1e-4f * (R + r);
    steps = min(steps, 96);
    bool hit = false;
    for (int i = 0; i < steps; ++i) {
        const float d = sdTorus(ro + rd * t, f2(R, r));
        if (d < eps) { hit = true; break; }
        t += d;
        if (t > bb.y) break;
    }
    if (!hit) return fhit_miss();
    FHit h; h.t = t; h.face = 0;
    const float3 p = ro + rd * t;
    const float l = sqrtf(p.x * p.x + p.z * p.z) + 1e-9f;
    const float3 ring = f3(p.x / l * R, 0.f, p.z / l * R);
    h.n = normalize(p - ring);
    h.uv = f2(0.5f + atan2f(p.x, p.z) * (1.f / F3D_TAU), 0.5f - atan2f(p.y, l - R) * (1.f / F3D_TAU));
    return h;
}
template <class HF>
__device__ float hf_march(HF hf, float3 ro, float3 rd, float tmin, float tmax, int steps) {
    steps = min(max(steps, 1), 256);
    const float dt = (tmax - tmin) / steps;
    float t0 = tmin;
    float3 p = ro + rd * t0;
    float f0 = p.y - hf(p.x, p.z);
    if (f0 < 0.f) return tmin;
    for (int i = 1; i <= steps; ++i) {
        const float t1 = tmin + dt * i;
        p = ro + rd * t1;
        const float f1 = p.y - hf(p.x, p.z);
        if (f1 < 0.f) {
            float a = t0, fa = f0, b = t1, fb = f1;
            #pragma unroll
            for (int j = 0; j < 4; ++j) {
                const float tm = a + (b - a) * fa / (fa - fb);
                const float3 pm = ro + rd * tm;
                const float fm = pm.y - hf(pm.x, pm.z);
                if (fm < 0.f) { b = tm; fb = fm; } else { a = tm; fa = fm; }
            }
            return a + (b - a) * fa / (fa - fb);
        }
        t0 = t1; f0 = f1;
    }
    return -1.f;
}
template <class HF>
__device__ float3 hf_normal(HF hf, float x, float z, float e) {
    return normalize(f3(hf(x - e, z) - hf(x + e, z), 2.f * e, hf(x, z - e) - hf(x, z + e)));
}

// -------------------------------------------------------------------- shading
__device__ __forceinline__ float fresnel_schlick(float cosi, float f0) {
    const float k = 1.f - saturate(cosi), k2 = k * k;
    return f0 + (1.f - f0) * k2 * k2 * k;
}
__device__ __forceinline__ float3 refract3(float3 i, float3 n, float eta) {
    const float d = dot(n, i), k = 1.f - eta * eta * (1.f - d * d);
    return k < 0.f ? f3(0.f) : i * eta - n * (eta * d + sqrtf(k));
}
__device__ __forceinline__ float spec_blinn(float3 n, float3 v, float3 l, float shin) {
    return powf(saturate(dot(n, normalize(l + v))), shin);
}
__device__ __forceinline__ float3 light_frame(float3 c, float3 n, float3 l, float3 fwd, float amb, float k) {
    const float ref = amb + (1.f - amb) * saturate(-dot(fwd, l));
    const float val = amb + (1.f - amb) * saturate(dot(n, l));
    return c * (1.f + k * (val / fmaxf(ref, 1e-3f) - 1.f));
}
__device__ __forceinline__ float luma3(float3 c) { return 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z; }
__device__ __forceinline__ float3 saturation(float3 c, float s) { const float l = luma3(c); return f3(l) + (c - f3(l)) * s; }
__device__ __forceinline__ float3 hue_rotate(float3 c, float a) {
    const float Y = 0.299f * c.x + 0.587f * c.y + 0.114f * c.z;
    const float I = 0.596f * c.x - 0.274f * c.y - 0.322f * c.z;
    const float Q = 0.211f * c.x - 0.523f * c.y + 0.312f * c.z;
    float s, k; sincosf(a, &s, &k);
    const float I2 = I * k - Q * s, Q2 = I * s + Q * k;
    return f3(Y + 0.956f * I2 + 0.621f * Q2, Y - 0.272f * I2 - 0.647f * Q2, Y - 1.106f * I2 + 1.703f * Q2);
}
__device__ __forceinline__ float3 env_frame(const Tex& T, float3 d) {
    const float m = 2.f * sqrtf(d.x * d.x + d.y * d.y + (d.z + 1.f) * (d.z + 1.f)) + 1e-6f;
    return tex_blur(T, 0.5f + d.x / m, 0.5f - d.y / m);
}
__device__ __forceinline__ float env_softbox(float3 d) {
    return smoothstep(0.25f, 0.65f, d.y) * smoothstep(-0.35f, 0.25f, d.z) * smoothstep(0.85f, 0.35f, fabsf(d.x));
}

// -------------------------------------------------------------------- backgrounds / occlusion
__device__ __forceinline__ float vignette_k(float u, float v, float amt) {
    const float ex = u - 0.5f, ey = v - 0.5f;
    return saturate(1.f - amt * (ex * ex + ey * ey) * 2.f);
}
__device__ __forceinline__ float3 bg_blur(const Tex& T, float u, float v, float zoom, float dim, float vig) {
    const float iz = 1.f / fmaxf(zoom, 1e-3f);
    return tex_blur(T, 0.5f + (u - 0.5f) * iz, 0.5f + (v - 0.5f) * iz) * (dim * vignette_k(u, v, vig));
}
__device__ __forceinline__ float3 bg_gradient(const float* __restrict__ pal12, float u, float v, float phase, float dim) {
    const float r = sqrtf((u - 0.5f) * (u - 0.5f) * 1.6f + (v - 0.5f) * (v - 0.5f));
    return pal(phase + 0.35f * v + 0.25f * r, pal12) * (dim * (1.f - 0.8f * r));
}
__device__ __forceinline__ float occ_box(float3 p, float3 hb, float radius) {
    return smoothstep(0.f, radius, sdBox(p, hb));
}
__device__ __forceinline__ float occ_sphere(float3 p, float3 n, float3 c, float r) {
    const float3 d = c - p;
    const float l2 = fmaxf(dot(d, d), 1e-9f);
    return saturate(1.f - (r * r / l2) * saturate(dot(n, d) * rsqrtf(l2)));
}

// -------------------------------------------------------------------- water (host: Water)
#define WATER_HDR 4
#define WAVE_FLOATS 5
#define RING_FLOATS 6
#define WATER_MAX_WAVES 8
#define WATER_MAX_RINGS 8
struct WaterS { float h; float2 g; float lap; };
__device__ WaterS water_eval(const float* __restrict__ Wp, float2 q) {
    WaterS s; s.h = 0.f; s.g = f2(0.f); s.lap = 0.f;
    const int nw = min((int)Wp[0], WATER_MAX_WAVES), nr = min((int)Wp[1], WATER_MAX_RINGS);
    const float* w = Wp + WATER_HDR;
    for (int i = 0; i < nw; ++i, w += WAVE_FLOATS) {      // dir.x dir.y k amp phase
        float sn, cs; sincosf(w[2] * (w[0] * q.x + w[1] * q.y) + w[4], &sn, &cs);
        const float ak = w[3] * w[2];
        s.h += w[3] * sn; s.g.x += ak * w[0] * cs; s.g.y += ak * w[1] * cs; s.lap -= ak * w[2] * sn;
    }
    const float* r = Wp + WATER_HDR + WATER_MAX_WAVES * WAVE_FLOATS;
    for (int j = 0; j < nr; ++j, r += RING_FLOATS) {      // cx cy radius amp k width
        const float ex = q.x - r[0], ey = q.y - r[1], d = sqrtf(ex * ex + ey * ey) + 1e-5f;
        const float sd = d - r[2], iw = 1.f / r[5], e = __expf(-sd * sd * iw * iw);
        if (e < 1e-3f) continue;
        float sn, cs; sincosf(r[4] * sd, &sn, &cs);
        const float hh = r[3] * sn * e, dh = r[3] * e * (r[4] * cs - 2.f * sd * iw * iw * sn);
        s.h += hh; s.g.x += dh * ex / d; s.g.y += dh * ey / d; s.lap += -r[4] * r[4] * hh + dh / d;
    }
    return s;
}
__device__ __forceinline__ float3 water_normal_y(const WaterS& s) { return normalize(f3(-s.g.x, 1.f, -s.g.y)); }
__device__ __forceinline__ float3 water_normal_z(const WaterS& s) { return normalize(f3(-s.g.x, -s.g.y, 1.f)); }
__device__ __forceinline__ float2 water_refract_offset(float3 n_z, float depth, float ior) {
    const float3 r = refract3(f3(0.f, 0.f, -1.f), n_z, 1.f / ior);
    return f2(r.x, r.y) * (depth / fmaxf(-r.z, 0.2f));
}
__device__ __forceinline__ float water_caustic(float lap, float depth, float gain) {
    return clamp(1.f - gain * depth * lap, 0.25f, 3.f);
}

// -------------------------------------------------------------------- AA / store
// Silhouette classification for a box (object space; delta = ~1.5 px footprint in object units, e.g.
// 1.5 * pa * length(oro) / X.s): the centre ray against the box grown and shrunk by delta.  Returns 0 (misses the
// grown box: background), 1 (hits the shrunk box: fully covered) or 0.5 (silhouette zone: refine with aa_cover4).
// Costs about one iBox; only ~1-2% of pixels then pay for the 4 sub-rays.
__device__ __forceinline__ float box_cover_class(float3 ro, float3 rd, float3 hb, float delta) {
    const float3 m = sdf_safe_rcp(rd), n = m * ro, am = abs(m), k = am * hb, e = am * delta;
    const float3 t1 = -n - k, t2 = -n + k;
    const float tng = vmax(t1 - e), tfg = vmin(t2 + e);
    if (tng > tfg || tfg <= 0.f) return 0.f;
    const float tns = vmax(t1 + e), tfs = vmin(t2 - e);
    return (tns <= tfs && tfs > 0.f && tns > 0.f) ? 1.f : 0.5f;
}
// Analytic coverage of a sphere's silhouette (radius r at the origin, object space): ~1 px smooth edge.
// fp = world size of a pixel near the sphere (pa * distance).
__device__ __forceinline__ float sphere_cover(float3 ro, float3 rd, float r, float fp) {
    const float b = dot(ro, rd);
    const float d = sqrtf(fmaxf(dot(ro, ro) - b * b, 0.f));          // ray-centre distance
    return b < 0.f ? saturate((r - d) / fmaxf(fp, 1e-9f) + 0.5f) : (dot(ro, ro) < r * r ? 1.f : 0.f);
}
template <class F>
__device__ __forceinline__ float aa_cover4(const Cam& c, float px, float py, int w, int h, F hit, float3& sub_rd) {
    const float ox[4] = {-0.125f, 0.375f, 0.125f, -0.375f}, oy[4] = {-0.375f, -0.125f, 0.375f, 0.125f};
    int n = 0;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float3 d = cam_dir(c, px + ox[i], py + oy[i], w, h);
        if (hit(d)) { ++n; sub_rd = d; }
    }
    return 0.25f * n;
}
__device__ __forceinline__ void store_rgb(uchar4* __restrict__ out, int idx, float3 c) {
    out[idx] = make_uchar4((unsigned char)fminf(fmaxf(c.x * 255.f + 0.5f, 0.f), 255.f),
                           (unsigned char)fminf(fmaxf(c.y * 255.f + 0.5f, 0.f), 255.f),
                           (unsigned char)fminf(fmaxf(c.z * 255.f + 0.5f, 0.f), 255.f), 255);
}
__device__ __forceinline__ void store_rgb_dither(uchar4* __restrict__ out, int x, int y, int w, float3 c, float amt) {
    const float d = (hash1(x, y, 0x5bd1e995u) + hash1(x, y, 0x1b873593u) - 1.f) * amt;
    out[y * w + x] = make_uchar4((unsigned char)fminf(fmaxf(c.x * 255.f + d + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.y * 255.f + d + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.z * 255.f + d + 0.5f, 0.f), 255.f), 255);
}
// ==================================================================== end frame3d
"""


# The documented usage example (compiles against HELPERS + PALETTE_CUDA + SDF3D + FRAME3D).
SKELETON = r"""
SDF_KERNEL
void frame_card(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const Xf X = xf_load(P + 13);
    const float m = P[26], A = (float)W / H, pa = pixel_angle(cam, h);
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 rd = cam_dir(cam, px, py, w, h);
    float3 col = bg_blur(T, px / w, py / h, 1.1f, P[27], 0.8f);
    float3 sub = rd;
    auto hits = [&](float3 d) { float3 a, b; xf_ray(X, cam.eye, d, a, b); return fhit_ok(ihit_frame(a, b, A)); };
    const float cover = aa_cover4(cam, px, py, w, h, hits, sub);
    float3 ro_o, rd_o; xf_ray(X, cam.eye, hits(rd) ? rd : sub, ro_o, rd_o);
    const FHit hit = ihit_frame(ro_o, rd_o, A);
    if (fhit_ok(hit) && cover > 0.f) {
        const float3 n = xf_dir(X, hit.n);
        const float t = hit.t * X.s, cosa = dot(rd, cam.fwd);
        const float lod = tex_lod(t, pa, cosa, fabsf(dot(rd, n)), frame_texel(T) * X.s, -0.25f);
        float3 c = tex_sample(T, hit.face ? 1.f - hit.uv.x : hit.uv.x, hit.uv.y, lod);
        c = light_frame(c, n, normalize(f3(-0.4f, 0.5f, 0.8f)), cam.fwd, 0.25f, m);
        col = mix(col, c, cover);
    }
    store_rgb(out, y * w + x, col);
}
"""


# ----------------------------------------------------------------------------- mip / blur kernels (host class)
_MIP_SRC = r"""
// Per-channel rounded average of 4 packed RGBA8 words (alpha forced to 255): packed 16-bit-lane adds, full rate.
__device__ __forceinline__ unsigned int f3d_avg4(unsigned int p, unsigned int q, unsigned int r, unsigned int s) {
    const unsigned int m = 0x00FF00FFu;
    const unsigned int lo = (p & m) + (q & m) + (r & m) + (s & m) + 0x00020002u;
    const unsigned int hi = ((p >> 8) & m) + ((q >> 8) & m) + ((r >> 8) & m) + ((s >> 8) & m) + 0x00020002u;
    return ((lo >> 2) & m) | (((hi >> 2) & m) << 8) | 0xFF000000u;
}
// Two mip levels per launch: one thread per 2x2 block of level k+1 reads the 4x4 block of level k (uint4 rows when
// aligned), writes those 4 level-(k+1) texels and the level-(k+2) texel (exact 16-texel average).  Row pitches (in
// texels) pa, p1, p2: levels live in 32-byte-pitched buffers so the hardware can sample them as textures.
extern "C" __global__
void f3d_down4(const unsigned int* __restrict__ a, unsigned int* __restrict__ b1, unsigned int* __restrict__ b2,
               const int wa, const int ha, const int pa, const int w1, const int h1, const int p1,
               const int w2, const int h2, const int p2, const int two)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= ((w1 + 1) >> 1) || y >= ((h1 + 1) >> 1)) return;
    unsigned int v[4][4];
    if (((pa & 3) == 0) && 4 * x + 3 < wa && 4 * y + 3 < ha) {
        #pragma unroll
        for (int r = 0; r < 4; ++r) {
            const uint4 q = __ldg(reinterpret_cast<const uint4*>(a + (4 * y + r) * pa + 4 * x));
            v[r][0] = q.x; v[r][1] = q.y; v[r][2] = q.z; v[r][3] = q.w;
        }
    } else {
        #pragma unroll
        for (int r = 0; r < 4; ++r)
            #pragma unroll
            for (int c = 0; c < 4; ++c) v[r][c] = __ldg(a + min(4 * y + r, ha - 1) * pa + min(4 * x + c, wa - 1));
    }
    #pragma unroll
    for (int dy = 0; dy < 2; ++dy) {
        const int yy = 2 * y + dy;
        if (yy >= h1) break;
        const unsigned int q0 = f3d_avg4(v[2 * dy][0], v[2 * dy][1], v[2 * dy + 1][0], v[2 * dy + 1][1]);
        const unsigned int q1 = f3d_avg4(v[2 * dy][2], v[2 * dy][3], v[2 * dy + 1][2], v[2 * dy + 1][3]);
        if (((p1 & 1) == 0) && 2 * x + 1 < w1) {
            *reinterpret_cast<uint2*>(b1 + yy * p1 + 2 * x) = make_uint2(q0, q1);
        } else {
            b1[yy * p1 + 2 * x] = q0;
            if (2 * x + 1 < w1) b1[yy * p1 + 2 * x + 1] = q1;
        }
    }
    if (two && x < w2 && y < h2) {
        const unsigned int m = 0x00FF00FFu;
        unsigned int lo = 0x00080008u, hi = 0x00080008u;
        #pragma unroll
        for (int r = 0; r < 4; ++r)
            #pragma unroll
            for (int c = 0; c < 4; ++c) { lo += v[r][c] & m; hi += (v[r][c] >> 8) & m; }
        b2[y * p2 + x] = ((lo >> 4) & m) | (((hi >> 4) & m) << 8) | 0xFF000000u;
    }
}
// Separable Gaussian (radius 6 taps), clamp-to-edge.  dx, dy = (1, 0) or (0, 1); p = row pitch in texels.
extern "C" __global__
void f3d_blur(const uchar4* __restrict__ a, uchar4* __restrict__ b, const int w, const int h, const int p,
              const int dx, const int dy, const float sigma)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float k = -0.5f / (sigma * sigma);
    float r = 0.f, g = 0.f, bl = 0.f, ws = 0.f;
    #pragma unroll
    for (int i = -6; i <= 6; ++i) {
        const int xx = min(max(x + i * dx, 0), w - 1), yy = min(max(y + i * dy, 0), h - 1);
        const float wt = __expf(k * (float)(i * i));
        const uchar4 q = a[yy * p + xx];
        r += wt * q.x; g += wt * q.y; bl += wt * q.z; ws += wt;
    }
    const float iw = 1.f / ws;
    b[y * p + x] = make_uchar4((unsigned char)(r * iw + 0.5f), (unsigned char)(g * iw + 0.5f), (unsigned char)(bl * iw + 0.5f), 255);
}
"""


def _level_dims(W: int, H: int, levels: int) -> list[tuple[int, int]]:
    dims, w, h = [], W, H
    for _ in range(levels):
        w, h = max(w >> 1, 1), max(h >> 1, 1)
        dims.append((w, h))
    return dims


def _pad8(n: int) -> int:
    return (int(n) + 7) & ~7                           # 8 texels = 32 bytes: the texture row-pitch alignment


# ----------------------------------------------------------------------------- hardware texture objects
_TEX_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_TEX_GRAVE: deque = deque(maxlen=64)                   # evicted objects outlive any kernel still queued on them
_TEX_CACHE_MAX = 48


def tex_object(arr) -> np.uint64:
    """64-bit texture handle (bilinear, clamp-to-edge, normalised uv, RGBA8 read as 0..1) for an (h, w, 4) uint8
    CuPy array (a frame, or a column slice of a row-pitched buffer); pass it as a kernel argument and sample with
    ``tex_rgb_at(handle, u, v)``.  Cached per (device, pointer, shape, pitch), so call it every frame: creation is a
    host-only driver call (no GPU allocation), and the texture keeps no reference to the array (re-allocated
    memory at the same address and layout is simply the new content).  Raises ValueError for a layout the
    hardware cannot sample: base not 512-byte aligned, row pitch not a multiple of 32 bytes, or not RGBA8 rows."""
    import cupy as cp
    from cupy.cuda import runtime, texture
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 4 or arr.strides[1:] != (4, 1):
        raise ValueError(f"tex_object: need (h, w, 4) uint8 rows, got {arr.shape} {arr.dtype} {arr.strides}")
    h, w = int(arr.shape[0]), int(arr.shape[1])
    pitch, ptr = int(arr.strides[0]), int(arr.data.ptr)
    if ptr % 512 or pitch % 32 or pitch < 4 * w:
        raise ValueError(f"tex_object: unaligned frame (ptr % 512 = {ptr % 512}, pitch {pitch} bytes)")
    dev = cp.cuda.Device().id
    key = (dev, ptr, h, w, pitch)
    hit = _TEX_CACHE.get(key)
    if hit is not None:
        _TEX_CACHE.move_to_end(key)
        return hit[0]
    mem = cp.cuda.UnownedMemory(ptr, h * pitch, None, dev)
    proxy = cp.ndarray((h * pitch,), cp.uint8, cp.cuda.MemoryPointer(mem, 0))
    ch = texture.ChannelFormatDescriptor(8, 8, 8, 8, runtime.cudaChannelFormatKindUnsigned)
    res = texture.ResourceDescriptor(runtime.cudaResourceTypePitch2D, arr=proxy, chDesc=ch, width=w, height=h,
                                     pitchInBytes=pitch)
    td = texture.TextureDescriptor((runtime.cudaAddressModeClamp, runtime.cudaAddressModeClamp),
                                   runtime.cudaFilterModeLinear, runtime.cudaReadModeNormalizedFloat,
                                   normalizedCoords=1)
    to = texture.TextureObject(res, td)
    handle = np.uint64(to.ptr)
    _TEX_CACHE[key] = (handle, to, res, td, proxy)
    while len(_TEX_CACHE) > _TEX_CACHE_MAX:
        _TEX_GRAVE.append(_TEX_CACHE.popitem(last=False)[1])
    return handle


def tex_buffer(cp, w: int, h: int):
    """A zeroed (h, w, 4) uint8 buffer whose rows are padded to 32 bytes, so tex_object() can sample it (internal
    render targets you read back with hardware bilinear, e.g. a quarter-res backdrop pass)."""
    return cp.zeros((int(h), _pad8(w), 4), cp.uint8)[:, : int(w)]


class FrameMips:
    """Mip chain (+ a Gaussian-blurred backdrop level) of one input frame as hardware textures, rebuilt per frame.

    ``FrameMips(ctx, levels=4, blur_level=None, blur_sigma=2.5)`` preallocates levels 1..levels (each halving w and
    h, 2x2 box filter, rows padded to 32 bytes) and, unless ``blur_level=0``, a Gaussian-blurred copy of level
    ``blur_level`` (default: the coarsest) for tex_blur / bg_blur / env_frame.  ``build(src)`` makes two levels per
    launch (+2 tiny blur passes): ~0.25 ms at 4K for levels=4.  ``args(src)`` returns the 12 FRAME_ARGS kernel
    arguments (src, W, H, nlev, 7 level handles, blur handle); level 0 is a cached texture of ``src`` itself.
    ``levels=0, blur_level=0`` gives just the hardware-bilinear input (build() is then a no-op).  Use one instance
    per frame you sample (transitions: one for a, one for b; FRAME_ARGS_AS(a), FRAME_ARGS_AS(b) in the kernel).
    """

    def __init__(self, ctx, levels: int = 4, blur_level: Optional[int] = None, blur_sigma: float = 2.5,
                 width: Optional[int] = None, height: Optional[int] = None) -> None:
        from stream_service.gpu import raw_module
        cp = self.cp = ctx.cp
        self.W, self.H = int(width or ctx.width), int(height or ctx.height)
        self.levels = max(0, min(int(levels), MAX_LEVELS))
        self.dims = _level_dims(self.W, self.H, self.levels)
        self.pitches = [_pad8(w) for w, _ in self.dims]
        self._bufs = [cp.zeros((h, p, 4), cp.uint8) for (w, h), p in zip(self.dims, self.pitches)]
        self._lv = [b[:, :w] for b, (w, _) in zip(self._bufs, self.dims)]
        bl = self.levels if blur_level is None else int(blur_level)
        self.blev = bl if 1 <= bl <= self.levels else -1
        self.sigma = float(blur_sigma)
        mod = raw_module(_MIP_SRC)
        self._down4 = mod.get_function("f3d_down4")
        self._blur = mod.get_function("f3d_blur")
        self._ht = [tex_object(v) for v in self._lv]
        self._hb = None
        if self.blev > 0:
            w, h = self.dims[self.blev - 1]
            p = self.pitches[self.blev - 1]
            self._blur_buf = cp.zeros((h, p, 4), cp.uint8)
            self._blur_tmp = cp.zeros((h, p, 4), cp.uint8)
            self.blur = self._blur_buf[:, :w]
            self._hb = tex_object(self.blur)
            ip, iw, ih = np.int32(p), np.int32(w), np.int32(h)
            self._blur_args = ((self._bufs[self.blev - 1], self._blur_tmp, iw, ih, ip, np.int32(1), np.int32(0),
                                np.float32(self.sigma)),
                               (self._blur_tmp, self._blur_buf, iw, ih, ip, np.int32(0), np.int32(1),
                                np.float32(self.sigma)))
        # prebuilt launches: (grid w, grid h, index of the source level, args without the source pointer)
        self._plan = []
        pw, ph, pp, src_k = self.W, self.H, self.W, 0
        for k in range(0, self.levels, 2):
            w1, h1 = self.dims[k]
            two = k + 1 < self.levels
            w2, h2 = self.dims[k + 1] if two else (1, 1)
            p2 = self.pitches[k + 1] if two else 1
            b2 = self._bufs[k + 1] if two else self._bufs[k]
            self._plan.append(((w1 + 1) // 2, (h1 + 1) // 2, src_k,
                               (self._bufs[k], b2),
                               (np.int32(pw), np.int32(ph), np.int32(pp), np.int32(w1), np.int32(h1),
                                np.int32(self.pitches[k]), np.int32(w2), np.int32(h2), np.int32(p2),
                                np.int32(1 if two else 0))))
            src_k = k + 2
            pw, ph, pp = w2, h2, p2
        self._iW, self._iH, self._il = np.int32(self.W), np.int32(self.H), np.int32(self.levels)
        self._fallback = None

    def level(self, k: int):
        """Level k (1..levels) as an (h, w, 4) view (0 is the input itself)."""
        return self._lv[k - 1]

    def _src_tex(self, src) -> np.uint64:
        """Texture handle of the input (cached per buffer); a padded copy only for a width the hardware cannot
        pitch (W * 4 not a multiple of 32 bytes) or an unaligned base."""
        if src.shape[:2] != (self.H, self.W) or not src.flags.c_contiguous:
            raise ValueError(f"FrameMips({self.W}x{self.H}) needs a contiguous (H, W, 4) frame, got {src.shape}")
        try:
            return tex_object(src)
        except ValueError:
            if self._fallback is None:
                self._fallback = self.cp.zeros((self.H, _pad8(self.W), 4), self.cp.uint8)[:, :self.W]
            self.cp.copyto(self._fallback, src)
            return tex_object(self._fallback)

    def build(self, src) -> None:
        from stream_service.plugins._lib.cuda import launch_2d
        for gw, gh, sk, (b1, b2), rest in self._plan:
            a = src if sk == 0 else self._bufs[sk - 1]
            launch_2d(self._down4, gw, gh, (a, b1, b2, *rest))
        if self.blev > 0:
            w, h = self.dims[self.blev - 1]
            launch_2d(self._blur, w, h, self._blur_args[0])
            launch_2d(self._blur, w, h, self._blur_args[1])

    def args(self, src) -> tuple:
        hs = [self._src_tex(src), *self._ht]
        hs += [hs[-1]] * (MAX_LEVELS + 1 - len(hs))
        hb = self._hb if self._hb is not None else hs[self.levels]
        return (src, self._iW, self._iH, self._il, *hs, hb)


# ----------------------------------------------------------------------------- host maths
def smoothstep01(x: float) -> float:
    x = min(max(float(x), 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def flat_distance(fov_deg: float = 40.0) -> float:
    """Distance from the flat camera to the frame quad (half-height 1) so the quad exactly fills the view."""
    return 1.0 / math.tan(math.radians(fov_deg) * 0.5)


def camera_flat(fov_deg: float = 40.0) -> list[float]:
    """13 floats (cam_load): the identity camera at (0, 0, D) looking at the origin (screen-right = +x)."""
    return camera_pack((0.0, 0.0, flat_distance(fov_deg)), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), fov_deg)


def camera_orbit(target: Sequence[float], dist: float, yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0,
                 fov_deg: float = 40.0) -> list[float]:
    """13 floats: a camera ``dist`` from ``target`` looking at it; yaw = pitch = 0 puts it on the +z side looking
    down -z (the flat pose when target = (0, 0, z0) and dist = flat_distance(fov) - z0).  yaw > 0 swings it to the
    right (+x), pitch > 0 raises it; roll banks it (radians)."""
    tx, ty, tz = (float(v) for v in target)
    cp_, sp = math.cos(pitch), math.sin(pitch)
    eye = (tx + dist * cp_ * math.sin(yaw), ty + dist * sp, tz + dist * cp_ * math.cos(yaw))
    return camera_pack(eye, (tx, ty, tz), (0.0, 1.0, 0.0), fov_deg, roll)


def rot_euler(yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> list[float]:
    """Row-major R = Ry(yaw) @ Rx(pitch) @ Rz(roll) (9 floats; radians, right-handed).  Ry(+a) turns the +z face
    towards +x (screen right), so a positive yaw rate spins the object to the right, showing its left (-x) face."""
    m = np.eye(3)
    for axis, a in (((0, 1, 0), yaw), ((1, 0, 0), pitch), ((0, 0, 1), roll)):
        if a:
            m = m @ np.asarray(rot_matrix(axis, a), np.float64).reshape(3, 3)
    return [float(v) for v in m.reshape(-1)]


def xform_pack(rot9: Optional[Sequence[float]] = None, origin: Sequence[float] = (0.0, 0.0, 0.0),
               scale: float = 1.0) -> list[float]:
    """13 floats for xf_load: world = R * (scale * p_obj) + origin."""
    r = list(rot9) if rot9 is not None else [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    return [*(float(v) for v in r), *(float(v) for v in origin), float(scale)]


def box_support_y(rot9: Sequence[float], hb: Sequence[float]) -> float:
    """Lowest extent below the centre of a box (half sizes hb) rotated by rot9: sum_j |R[1][j]| * hb[j]."""
    return abs(rot9[3]) * hb[0] + abs(rot9[4]) * hb[1] + abs(rot9[5]) * hb[2]


class HomingAngle:
    """An angle that spins at a rate while a gate (0..1, e.g. smoothstep of amount) is open and springs home to the
    nearest multiple of ``period`` as it closes, so a projection effect is the identity at amount 0 without a jump.

        self.spin = HomingAngle()                       # setup
        a = self.spin.step(dt, rate_rad_s, gate)        # render: integrates; returns the angle to draw
    ``display`` blends the residual to 0 below ``gate < 0.15`` so even a fast fade lands exactly home."""

    def __init__(self, period: float = 2.0 * math.pi, spring: float = 4.0) -> None:
        self.theta = 0.0
        self.period = float(period)
        self.spring = float(spring)

    def step(self, dt: float, rate: float, gate: float) -> float:
        gate = min(max(gate, 0.0), 1.0)
        self.theta += rate * gate * dt
        home = round(self.theta / self.period) * self.period
        if gate < 1.0 and dt > 0:
            self.theta += (home - self.theta) * (1.0 - math.exp(-self.spring * (1.0 - gate) * dt))
        # keep theta small (fast-math trig) without visible change: shift by whole periods
        if abs(self.theta) > 64 * self.period:
            k = round(self.theta / self.period)
            self.theta -= k * self.period
            home -= k * self.period
        return home + (self.theta - home) * smoothstep01(gate / 0.15)


class Water:
    """Host side of water_eval: ``n_waves`` seeded directional sine waves (deep-water dispersion, phases integrated
    so speed changes never jump) plus up to 8 expanding ripple rings spawned on pulse rising edges.

        self.water = Water(ctx, n_waves=6, seed=1, wavelength=0.5, amplitude=0.012, direction=0.4, spread=1.2)
        self.water.step(dt, speed=p["speed"], pulse=p["pulse"], amp_scale=..., ring_amp=0.02)   # per frame
        kernel arg: self.water.buf (float32[WATER_FLOATS]); positions in the surface frame (frame-plane x in
        [-A, A], y in [-1, 1], or world xz); ``bounds`` = (x0, y0, x1, y1) where rings may spawn.
    """

    def __init__(self, ctx, n_waves: int = 6, seed: int = 1, wavelength: float = 0.5, amplitude: float = 0.012,
                 direction: float = 0.4, spread: float = 1.2, ring_speed: float = 0.9, ring_wavelength: float = 0.12,
                 ring_width: float = 0.18, ring_life: float = 2.5, bounds: Sequence[float] = (-1.6, -0.9, 1.6, 0.9)):
        self.cp = ctx.cp
        rng = np.random.default_rng(int(seed))
        self.n = max(0, min(int(n_waves), WATER_MAX_WAVES))
        ang = direction + spread * (rng.random(WATER_MAX_WAVES) - 0.5)
        lam = wavelength * (0.45 + 1.1 * rng.random(WATER_MAX_WAVES)) ** 1.3
        self.dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
        self.k = 2 * math.pi / lam
        self.amp_rel = (lam / wavelength) * (amplitude)            # constant steepness: amp ~ wavelength
        self.omega = 1.7 * np.sqrt(self.k)                         # deep water: w = sqrt(g k) (scaled)
        self.phase = rng.random(WATER_MAX_WAVES) * 2 * math.pi
        self.ring_xy = rng.random((64, 2))
        self.ring_i = 0
        self.rings: list[list[float]] = []                         # [cx, cy, age, amp0]
        self.ring_speed, self.ring_k = ring_speed, 2 * math.pi / ring_wavelength
        self.ring_width, self.ring_life = ring_width, ring_life
        self.bounds = tuple(float(v) for v in bounds)
        self.prev_pulse = 0.0
        self.host = np.zeros(WATER_FLOATS, np.float32)
        self.buf = self.cp.zeros(WATER_FLOATS, self.cp.float32)

    def spawn(self, x: float, y: float, amp: float) -> None:
        if len(self.rings) >= WATER_MAX_RINGS:
            self.rings.pop(0)
        self.rings.append([x, y, 0.0, amp])

    def step(self, dt: float, speed: float = 1.0, pulse: float = 0.0, amp_scale: float = 1.0,
             ring_amp: float = 0.02, threshold: float = 0.25) -> None:
        self.phase = np.mod(self.phase - self.omega * speed * dt, 2 * math.pi)
        if pulse - self.prev_pulse > threshold and ring_amp > 0:
            x0, y0, x1, y1 = self.bounds
            u, v = self.ring_xy[self.ring_i % len(self.ring_xy)]
            self.ring_i += 1
            self.spawn(x0 + (x1 - x0) * u, y0 + (y1 - y0) * v, ring_amp * min(1.0, pulse))
        self.prev_pulse = pulse
        for r in self.rings:
            r[2] += dt * max(speed, 0.0)
        self.rings = [r for r in self.rings if r[2] < self.ring_life]
        h = self.host
        h[:] = 0.0
        h[0], h[1] = self.n, len(self.rings)
        o = WATER_HDR
        for i in range(self.n):
            h[o:o + 5] = (self.dirs[i, 0], self.dirs[i, 1], self.k[i], self.amp_rel[i] * amp_scale, self.phase[i])
            o += WAVE_FLOATS
        o = WATER_HDR + WATER_MAX_WAVES * WAVE_FLOATS
        for cx, cy, age, a0 in self.rings:
            rad = self.ring_speed * age
            fade = math.exp(-2.2 * age / self.ring_life) * (1.0 - age / self.ring_life)
            h[o:o + 6] = (cx, cy, rad, a0 * fade / (1.0 + 1.5 * rad), self.ring_k, self.ring_width)
            o += RING_FLOATS
        self.buf.set(h)


__all__: Iterable[str] = ("FRAME3D", "FRAME3D_OPTIONS", "SKELETON", "XF_FLOATS", "WATER_FLOATS", "FrameMips",
                          "tex_object", "tex_buffer",
                          "HomingAngle", "Water", "smoothstep01", "flat_distance", "camera_flat", "camera_orbit",
                          "rot_euler", "xform_pack", "box_support_y")


_SELFTEST_K = r"""
extern "C" __global__
void f3d_selftest_lv(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float lod, const int blur) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const float u = (x + 0.5f) / w, v = (y + 0.5f) / h;
    store_rgb(out, y * w + x, blur ? tex_blur(T, u, v) : tex_sample(T, u, v, lod));
}
"""


def _selftest() -> None:  # pragma: no cover - run via out/frame3d/selftest.py (GPU slot) on GPU 1
    """Compile FRAME3D + SKELETON; check the flat camera reproduces a random frame exactly, the mip levels match a
    2x2 box filter, and hardware sampling of every level / the blur level returns its texels at their centres."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_frozen"))
    from stream_service.gpu import RenderContext, raw_module
    from stream_service.plugins._lib.cuda import HELPERS, launch_2d
    from stream_service.plugins._lib.palettes import PALETTE_CUDA
    from stream_service.plugins._lib.sdf3d import SDF3D
    for W, H in ((960, 540), (1002, 563)):
        ctx = RenderContext(int(os.environ.get("FX_GPU", "1")), W, H, 30, prealloc_buffers=0)
        cp = ctx.cp
        with ctx.device, ctx.stream:
            mod = raw_module(HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + SKELETON + _SELFTEST_K, FRAME3D_OPTIONS)
            k = mod.get_function("frame_card")
            klv = mod.get_function("f3d_selftest_lv")
            rng = np.random.default_rng(0)
            src = cp.asarray(rng.integers(0, 255, (H, W, 4), dtype=np.uint8))
            out = ctx.new_frame()
            mips = FrameMips(ctx, levels=5)
            mips.build(src)
            P = cp.asarray(np.asarray([*camera_flat(40.0), *xform_pack(), 0.0, 0.3], np.float32))
            launch_2d(k, W, H, (out, np.int32(W), np.int32(H), *mips.args(src), P))
            d = cp.abs(out[..., :3].astype(cp.int16) - src[..., :3].astype(cp.int16))
            print(f"{W}x{H} regs", k.num_regs, "local", k.local_size_bytes, "flat identity: max", int(d.max()),
                  "mean", float(d.mean()))
            ref = cp.asnumpy(src).astype(np.float64)
            for lv in range(1, mips.levels + 1):
                w, h = mips.dims[lv - 1]
                hh, ww = ref.shape[0] // 2, ref.shape[1] // 2
                ref = ref[: 2 * hh, : 2 * ww].reshape(hh, 2, ww, 2, 4).mean(axis=(1, 3))
                got = cp.asnumpy(mips.level(lv)).astype(np.float64)
                o = cp.zeros((h, w, 4), cp.uint8)
                launch_2d(klv, w, h, (o, np.int32(w), np.int32(h), *mips.args(src), np.float32(lv), np.int32(0)))
                hw = np.abs(cp.asnumpy(o).astype(np.float64)[..., :3] - got[..., :3]).max()
                print(f"  mip {lv} {w}x{h} pitch {mips.pitches[lv - 1]}: box-filter err "
                      f"{np.abs(got[..., :3] - ref[..., :3]).max():.2f}, hw sample at centres err {hw:.0f}")
            w, h = mips.dims[mips.blev - 1]
            o = cp.zeros((h, w, 4), cp.uint8)
            launch_2d(klv, w, h, (o, np.int32(w), np.int32(h), *mips.args(src), np.float32(0), np.int32(1)))
            print("  blur level hw err", int(np.abs(cp.asnumpy(o).astype(int) - cp.asnumpy(mips.blur).astype(int))[..., :3].max()))


if __name__ == "__main__":  # pragma: no cover
    _selftest()
