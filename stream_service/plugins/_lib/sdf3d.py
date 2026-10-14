"""smfx 3D toolkit: CUDA device code for signed-distance-field (SDF) raymarched scenes.

Source: https://mercury.sexy/hg_sdf/ (hg_sdf.glsl, version 2021-07-28);
        https://www.shadertoy.com/view/Xds3zN ("Raymarching - Primitives") and
        https://iquilezles.org/articles/distfunctions/ , /articles/normalsSDF/ , /articles/rmshadows/ ,
        /articles/smin/ ; https://knarkowicz.wordpress.com/2016/01/06/aces-filmic-tone-mapping-curve/
Author: Mercury Demogroup (hg_sdf); Inigo Quilez (iq primitives, tetrahedral normals, soft shadow, AO, iBox, smin);
        Krzysztof Narkowicz (ACES fit); smfx (CUDA port, camera, light rig, folds, fractal DEs, noise, I/O)
License: MIT (hg_sdf is dual-licensed "MIT OR CC-BY-NC-4.0"; used here under MIT, as its header allows.
         Xds3zN carries "The MIT License, Copyright (c) 2013 Inigo Quilez" in its header.  Both notices are
         reproduced at the end of this file.)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: inout swizzles -> C++ references, hg's pR(p.xz) -> pR(p.x, p.z, a);
        branch-free folds; templated march/normal/shadow/AO that take the scene's map as a lambda; camera,
        light rig, fog, tonemap, sRGB + dither store written for this renderer.)

USE
---
Prepend after the shared helpers and compile with the toolkit's NVRTC options (fast math matters here):

    from stream_service.gpu import raw_module
    from stream_service.plugins._lib.cuda import HELPERS, launch_2d
    from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
    from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, orbit_eye, rig_pack, rot_matrix, wrap
    _SRC = HELPERS + PALETTE_CUDA + SDF3D + r'''  ...your scene + kernel...  '''
    k = raw_module(_SRC, SDF3D_OPTIONS).get_function("my_scene")

SDF3D_OPTIONS adds --use_fast_math: sinf/cosf/expf/powf become 1-instruction intrinsics.  Their accuracy drops for
large arguments, so keep angles small: wrap host-integrated phases with ``wrap(x, period)`` before uploading.

Conventions: y is up, right-handed; distances in world units; colours are linear float3 until the end; ``pal()``
returns sRGB-encoded 0..1, so take ``srgb_to_linear(pal(...))`` as an albedo, light it, tonemap, then
``store_srgb``.  ``a(b)`` below means C++ overloads exist for those types.

API (all __device__; float3 = CUDA float3; "&" = modified in place)
-------------------------------------------------------------------
Kernel    SDF_KERNEL / SDF_KERNEL_TIGHT: use instead of extern "C" __global__ (register caps, ~25% faster)
Vectors   f2(x,y) f2(s) f3(x,y,z) f3(s) f3(float2,z) load3(const float*)   (float3/float2 operators + - * / and
          += -= *= /= and unary -, elementwise, with scalars on either side)
          dot dot2 length normalize(eps-safe) cross reflect(i,n)  mix(a,b,t)  clamp(x,lo,hi)  saturate
          abs floor fract mod(x,y)(GLSL mod) min max sin cos exp sqrt pow(v,e)   [float3/float2 overloads]
          step(e,x) smoothstep(e0,e1,x) sign sgn(never 0) vmax(v) vmin(v) (largest/smallest component)
Rotation  rotX(p,a) rotY(p,a) rotZ(p,a) rotAxis(p,unit_axis,a) rot2d(float2,a)
          pR(x&, y&, a) (hg: rotates "x towards y": pR(p.x, p.z, a))  pR45(x&, y&)
          struct Mat3 {float3 r0,r1,r2;}  mat3_load(const float* 9, row-major; host rot_matrix())
          mat3_rot(unit_axis, a)  mul(Mat3, float3)  mul_t(Mat3, float3) (= inverse for rotations)  mul(Mat3, Mat3)
Camera    struct Cam {eye, fwd, right, up; tan_half}  cam_look(eye, target, up, fov_deg) (vertical fov)
          cam_load(const float* 13) (host camera_pack())  cam_ray(cam, x, y, W, H) (int pixel, centre sampled)
          cam_dir(cam, px, py, W, H) (float pixel coords, for jitter)  pixel_angle(cam, H) (radians per pixel)
          camera_ray(x, y, W, H, eye, target, up, fov_deg) (all-in-one)
Bounds    iBox(ro, rd, half) -> float2(tnear, tfar)  iSphere(ro, rd, r) -> (tnear, tfar)  iPlane(ro, rd, n, h)
          (no hit: tnear > tfar, or tfar < 0; iPlane returns < 0)
SDF (iq)  sdSphere(p,r) sdBox(p,b) sdRoundBox(p,b,r) sdBoxFrame(p,b,e) sdTorus(p, float2(R,r)) sdLink(p,le,r1,r2)
          sdCylinder(p, float2(r, half_h)) sdCylinder(p,a,b,r) sdCapsule(p,a,b,r) sdVerticalCapsule(p,h,r)
          sdCappedCone(p,half_h,r1,r2) sdRoundCone(p,r1,r2,h) sdOctahedron(p,s) sdPlane(p,unit_n,h)
          sdHexPrism(p, float2(r, half_len)) (axis z) sdTriPrism(p, float2) sdEllipsoid(p, radii) (bound)
SDF (hg)  fSphere fPlane(p,n,dist) fBox fBoxCheap fBox2(float2,float2) fBox2Cheap fCorner(float2) fCylinder(p,r,h)
          fCapsule(p,r,c) fCapsule(p,a,b,r) fLineSegment(p,a,b) fTorus(p,small,large) fCircle(p,r) fDisc(p,r)
          fHexagonCircumcircle(p, float2) fHexagonIncircle(p, float2) fCone(p,r,h)
          fOctahedron fDodecahedron fIcosahedron fTruncatedOctahedron fTruncatedIcosahedron (p, r) and (p, r, e)
Combine   opUnion(a,b) opSubtract(a,b) (= a minus b) opIntersect(a,b)  smin(a,b,k) smax(a,b,k) ssub(a,b,k)
          smin_mix(a,b,k) -> float2(d, weight of b 0..1, for blending materials)  opOnion(d,thick)
          hg: fOpUnion/Intersection/DifferenceRound(a,b,r)  ...Chamfer(a,b,r)  ...Columns(a,b,r,n)
          ...Stairs(a,b,r,n)  fOpUnionSoft(a,b,r)  fOpPipe(a,b,r)  fOpEngrave(a,b,r)  fOpGroove(a,b,ra,rb)
          fOpTongue(a,b,ra,rb)
Domain    pMod1(x&, size) -> cell  pModMirror1  pModSingle1  pModInterval1(x&, size, start, stop)
          pModPolar(x&, y&, reps) -> cell  pMod2(x&, y&, sx, sy) -> float2 cell  pModMirror2  pModGrid2
          pMod3(p&, float3 size) -> float3 cell  pMirror(x&, dist) -> sign  pMirrorOctant(x&, y&, dx, dy)
          pReflect(p&, unit_n, offset) -> sign  elongate(p, h) -> q  twistY(p,k) bendX(p,k) (not Lipschitz:
          march with step_scale ~0.6)
Fractal   boxFold(p&, limit)  sphereFold(p&, dr&, minR2, fixR2)  foldPlane(p&, unit_n)  foldTetra(p&)
          foldMenger(p&) (abs + sort x>=y>=z)  foldAbsRot(p&, Mat3)
          deMandelbox(p, scale, minR, fixR, iters, trap&)  deMenger(p, iters, scale, offset, trap&)
          deSierpinski(p, iters, scale, offset, trap&)  deKifs(p, iters, scale, offset, Mat3 rot, trap&)
          deMandelbulb(p, power, iters, trap&)          (trap = min |z|^2 over the orbit, for colouring)
Noise     hash13(float3 cell) -> [0,1)  hash33(cell) -> float3  noise3(p, seed) value noise 0..1
          fbm3(p, octaves, seed) 0..1  (8 hashes per noise3: fine for shading, costly inside map)
March     struct Hit {t, d, steps, hit, glow}
          march(map, ro, rd, tmin, tmax, max_steps, eps, step_scale=1, glow_k=0)
              hit when |d| < eps * t (eps = pixel_angle for a pixel-cone stop); glow = sum 1/(1+glow_k d^2)
          march_relaxed(map, ro, rd, tmin, tmax, max_steps, eps, step_scale=1, omega=1.5, glow_k=0)
              over-relaxed variant (Keinert 2014); measure it: no gain in the sdf_demo column field
          calc_normal(map, p, h) (tetrahedral, 4 taps; h ~ eps * t)
          soft_shadow(map, ro, rd, mint, maxt, k, max_steps) -> 0..1 (k ~ 8 soft .. 32 hard)
          calc_ao(map, p, n, scale, samples) -> 0..1 (samples 3-5, scale ~ feature size)
Shading   struct Rig {key_dir (towards light), key_col, fill_col, rim_col, amb_col; spec, shin}
          rig_default() rig_load(const float* 17) (host rig_pack())
          shade(rig, albedo, n, rd, shadow, ao) -> linear rgb (key diffuse+spec, sky fill, rim, ambient)
          fog_exp(col, fogc, t, density) fog_exp2(col, fogc, t, density)
          tonemap_aces(c) tonemap_reinhard(c) srgb_to_linear(c) linear_to_srgb(c) vignette(c, x, y, W, H, amt)
          store_srgb(out, x, y, W, c_srgb01) (packs uchar4 with +-1 LSB triangular dither, alpha 255)

Lambda maps: ``auto map = [&](float3 p) { return scene(p, P); };`` defined inside the kernel is an implicit
device lambda; the templates inline it.  Material/ID pattern: the march only needs distance; after the hit,
call your own ``scene_id(pos, ...)`` once (or evaluate the parts again) to pick the material.

KERNEL SKELETON (also exported as SKELETON; compiled by this module's self-test)
--------------------------------------------------------------------------------
    // P: [0..12] camera (camera_pack)  [13..24] palette (palette_coeffs)  [25] period [26] drift [27] gain
    //    [28] fog density [29] time phase (wrapped)
    __device__ float scene(float3 p, const float* __restrict__ P) {
        float3 q = p;
        pMod2(q.x, q.z, 4.f, 4.f);                                   // a field of columns
        float d = sdCylinder(q, f2(0.5f, 3.f));
        d = opUnion(d, p.y + 1.f);                                   // floor
        const float3 c = f3(0.f, 0.f, 6.f + 2.f * sinf(P[29]));
        return smin(d, sdSphere(p - c, 1.f), 0.6f);                  // a blob melting into them
    }
    SDF_KERNEL                                                       // extern "C" __global__ + launch bounds
    void sdf_skeleton(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
        const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
        if (x >= W || y >= H) return;
        const Cam cam = cam_load(P);
        const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
        auto map = [&](float3 p) { return scene(p, P); };
        const float tmax = 40.f;
        const Hit h = march(map, ro, rd, 0.05f, tmax, 96, pixel_angle(cam, H));
        const float3 fogc = srgb_to_linear(pal(P[26] + 0.5f, P + 13)) * 0.15f;
        float3 col = fogc;
        if (h.hit) {
            const float3 pos = ro + rd * h.t;
            const float3 n = calc_normal(map, pos, 0.5f * pixel_angle(cam, H) * h.t + 1e-4f);
            const Rig L = rig_default();
            const float sh = soft_shadow(map, pos + n * 0.02f, L.key_dir, 0.02f, 10.f, 10.f, 16);
            const float ao = calc_ao(map, pos, n, 0.3f, 3);
            const float3 alb = srgb_to_linear(pal(pos.y * 0.2f * P[25] + P[26], P + 13));
            col = shade(L, alb, n, rd, sh, ao);
        }
        col = fog_exp2(col, fogc, fminf(h.t, tmax), P[28]);
        store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
    }

Host side, per frame (no allocation; phases integrated with dt and wrapped):
    self.t_ph = wrap(self.t_ph + 0.7 * p["speed"] * dt)
    eye = orbit_eye((0, 0, 6), radius, yaw, pitch)
    self.P.set(np.asarray([*camera_pack(eye, (0, 0, 6), fov_deg=55), *palette_coeffs(p["palette"]),
                           p["palette_period"], self.drift, gain, fog, self.t_ph], np.float32))
    launch_2d(k, w, h, (self.buf, np.int32(w), np.int32(h), self.P)); present(ctx, self.buf, out)

Budget notes (P4000, 1920x1080 internal): ~100 cheap map evals per pixel fit in ~4 ms.  In order of payoff:
  * SDF3D_OPTIONS (fast math): ~2.7x vs precise maths.
  * SDF_KERNEL / SDF_KERNEL_TIGHT (register caps -> more blocks per SM): ~25%.
  * Keep big flat planes (floors) OUT of map(): intersect them analytically and cap tmax there; grazing rays
    crawl along an SDF plane in tiny steps.  Same for anything you can trace analytically (iSphere/iBox, grids).
  * Bound expensive parts (``if (bound < best) { evaluate }``), clip tmax with iPlane/iBox, keep shadows <= 16
    steps and AO <= 3 taps, skip shadows/AO for far or fogged hits; second bounces (reflections) by SDF march
    are expensive (~2 ms): trace them analytically or against a simplified scene.
  * The P4000 holds an 83 C boost target: warm, it clocks ~1200-1500 MHz instead of 1708, so leave >= 20%
    headroom under the 6 ms bench.  Profile with a step-count heatmap (write h.steps as colour).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

# NVRTC options for modules that include SDF3D: pass to raw_module(src, SDF3D_OPTIONS).
SDF3D_OPTIONS: tuple[str, ...] = ("--std=c++14", "--use_fast_math")

CAM_FLOATS = 13      # camera_pack(): eye(3) fwd(3) right(3) up(3) tan_half(1)
RIG_FLOATS = 17      # rig_pack(): key_dir(3) key_col(3) fill_col(3) rim_col(3) amb_col(3) spec shin
MAT3_FLOATS = 9      # rot_matrix(): row-major


def _gdf_table() -> str:
    phi = math.sqrt(5.0) * 0.5 + 0.5
    vs = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (-1, 1, 1), (1, -1, 1), (1, 1, -1),
          (0, 1, phi + 1), (0, -1, phi + 1), (phi + 1, 0, 1), (-phi - 1, 0, 1), (1, phi + 1, 0), (-1, phi + 1, 0),
          (0, phi, 1), (0, -phi, 1), (1, 0, phi), (-1, 0, phi), (phi, 1, 0), (-phi, 1, 0)]
    out = []
    for v in vs:
        n = math.sqrt(sum(c * c for c in v))
        out += [f"{c / n:.9f}f" for c in v]
    return ", ".join(out)


SDF3D = r"""
// ==================================================================== smfx sdf3d (see smfx/_lib/sdf3d.py)
#define SDF_PI  3.14159265f
#define SDF_TAU 6.28318531f
#define SDF_PHI 1.61803399f
#define SDF_SQRT_HALF 0.70710678f
// Kernel qualifiers.  launch_2d uses 256-thread blocks; a raymarch kernel easily needs ~90 registers, which on
// Pascal (64K regs/SM) fits only 2 blocks per SM.  Capping registers is worth ~25% (sdf_demo: 6.2 -> 4.7 ms):
//   SDF_KERNEL        <= 85 regs, 3 blocks/SM          SDF_KERNEL_TIGHT  <= 64 regs, 4 blocks/SM
// Check k.num_regs / k.local_size_bytes (spills) on the CuPy function after compiling; use TIGHT if it doesn't spill.
#define SDF_KERNEL extern "C" __global__ __launch_bounds__(256, 3)
#define SDF_KERNEL_TIGHT extern "C" __global__ __launch_bounds__(256, 4)

// -------------------------------------------------------------------- constructors
__device__ __forceinline__ float2 f2(float x, float y) { return make_float2(x, y); }
__device__ __forceinline__ float2 f2(float s) { return make_float2(s, s); }
__device__ __forceinline__ float3 f3(float x, float y, float z) { return make_float3(x, y, z); }
__device__ __forceinline__ float3 f3(float s) { return make_float3(s, s, s); }
__device__ __forceinline__ float3 f3(float2 a, float z) { return make_float3(a.x, a.y, z); }
__device__ __forceinline__ float3 load3(const float* __restrict__ p) { return make_float3(p[0], p[1], p[2]); }

// -------------------------------------------------------------------- operators (elementwise, GLSL-like)
#define SDF_V3OP(OP) \
__device__ __forceinline__ float3 operator OP(float3 a, float3 b) { return make_float3(a.x OP b.x, a.y OP b.y, a.z OP b.z); } \
__device__ __forceinline__ float3 operator OP(float3 a, float b) { return make_float3(a.x OP b, a.y OP b, a.z OP b); } \
__device__ __forceinline__ float3 operator OP(float a, float3 b) { return make_float3(a OP b.x, a OP b.y, a OP b.z); } \
__device__ __forceinline__ float3& operator OP##=(float3& a, float3 b) { a.x OP##= b.x; a.y OP##= b.y; a.z OP##= b.z; return a; } \
__device__ __forceinline__ float3& operator OP##=(float3& a, float b) { a.x OP##= b; a.y OP##= b; a.z OP##= b; return a; }
#define SDF_V2OP(OP) \
__device__ __forceinline__ float2 operator OP(float2 a, float2 b) { return make_float2(a.x OP b.x, a.y OP b.y); } \
__device__ __forceinline__ float2 operator OP(float2 a, float b) { return make_float2(a.x OP b, a.y OP b); } \
__device__ __forceinline__ float2 operator OP(float a, float2 b) { return make_float2(a OP b.x, a OP b.y); } \
__device__ __forceinline__ float2& operator OP##=(float2& a, float2 b) { a.x OP##= b.x; a.y OP##= b.y; return a; } \
__device__ __forceinline__ float2& operator OP##=(float2& a, float b) { a.x OP##= b; a.y OP##= b; return a; }
SDF_V3OP(+) SDF_V3OP(-) SDF_V3OP(*) SDF_V3OP(/)
SDF_V2OP(+) SDF_V2OP(-) SDF_V2OP(*) SDF_V2OP(/)
#undef SDF_V3OP
#undef SDF_V2OP
__device__ __forceinline__ float3 operator-(float3 a) { return make_float3(-a.x, -a.y, -a.z); }
__device__ __forceinline__ float2 operator-(float2 a) { return make_float2(-a.x, -a.y); }

// -------------------------------------------------------------------- scalar GLSL-isms
__device__ __forceinline__ float fract(float x) { return x - floorf(x); }
__device__ __forceinline__ float mod(float x, float y) { return x - y * floorf(x / y); }
__device__ __forceinline__ float mix(float a, float b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float clamp(float x, float lo, float hi) { return fminf(fmaxf(x, lo), hi); }
__device__ __forceinline__ float saturate(float x) { return fminf(fmaxf(x, 0.f), 1.f); }
__device__ __forceinline__ float step(float e, float x) { return x < e ? 0.f : 1.f; }
__device__ __forceinline__ float smoothstep(float e0, float e1, float x) {
    const float t = saturate((x - e0) / (e1 - e0)); return t * t * (3.f - 2.f * t);
}
__device__ __forceinline__ float sign(float x) { return x > 0.f ? 1.f : (x < 0.f ? -1.f : 0.f); }
__device__ __forceinline__ float sgn(float x) { return x < 0.f ? -1.f : 1.f; }          // hg: never 0
__device__ __forceinline__ float square(float x) { return x * x; }

// -------------------------------------------------------------------- float3 / float2 functions
__device__ __forceinline__ float dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float dot(float2 a, float2 b) { return a.x * b.x + a.y * b.y; }
__device__ __forceinline__ float dot2(float3 a) { return dot(a, a); }
__device__ __forceinline__ float dot2(float2 a) { return dot(a, a); }
__device__ __forceinline__ float length(float3 a) { return sqrtf(dot(a, a)); }
__device__ __forceinline__ float length(float2 a) { return sqrtf(dot(a, a)); }
__device__ __forceinline__ float3 normalize(float3 a) { return a * rsqrtf(fmaxf(dot(a, a), 1e-30f)); }
__device__ __forceinline__ float2 normalize(float2 a) { return a * rsqrtf(fmaxf(dot(a, a), 1e-30f)); }
__device__ __forceinline__ float3 cross(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float3 reflect(float3 i, float3 n) { return i - n * (2.f * dot(n, i)); }
__device__ __forceinline__ float3 mix(float3 a, float3 b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float3 mix(float3 a, float3 b, float3 t) { return a + (b - a) * t; }
__device__ __forceinline__ float2 mix(float2 a, float2 b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float3 clamp(float3 v, float lo, float hi) {
    return make_float3(clamp(v.x, lo, hi), clamp(v.y, lo, hi), clamp(v.z, lo, hi));
}
__device__ __forceinline__ float3 clamp(float3 v, float3 lo, float3 hi) {
    return make_float3(clamp(v.x, lo.x, hi.x), clamp(v.y, lo.y, hi.y), clamp(v.z, lo.z, hi.z));
}
__device__ __forceinline__ float3 saturate(float3 v) { return clamp(v, 0.f, 1.f); }
__device__ __forceinline__ float3 abs(float3 a) { return make_float3(fabsf(a.x), fabsf(a.y), fabsf(a.z)); }
__device__ __forceinline__ float2 abs(float2 a) { return make_float2(fabsf(a.x), fabsf(a.y)); }
__device__ __forceinline__ float3 floor(float3 a) { return make_float3(floorf(a.x), floorf(a.y), floorf(a.z)); }
__device__ __forceinline__ float2 floor(float2 a) { return make_float2(floorf(a.x), floorf(a.y)); }
__device__ __forceinline__ float3 fract(float3 a) { return a - floor(a); }
__device__ __forceinline__ float2 fract(float2 a) { return a - floor(a); }
__device__ __forceinline__ float3 mod(float3 x, float3 y) { return x - y * floor(x / y); }
__device__ __forceinline__ float3 mod(float3 x, float y) { return x - y * floor(x / y); }
__device__ __forceinline__ float2 mod(float2 x, float2 y) { return x - y * floor(x / y); }
__device__ __forceinline__ float3 max(float3 a, float3 b) { return make_float3(fmaxf(a.x, b.x), fmaxf(a.y, b.y), fmaxf(a.z, b.z)); }
__device__ __forceinline__ float3 max(float3 a, float b) { return make_float3(fmaxf(a.x, b), fmaxf(a.y, b), fmaxf(a.z, b)); }
__device__ __forceinline__ float3 min(float3 a, float3 b) { return make_float3(fminf(a.x, b.x), fminf(a.y, b.y), fminf(a.z, b.z)); }
__device__ __forceinline__ float3 min(float3 a, float b) { return make_float3(fminf(a.x, b), fminf(a.y, b), fminf(a.z, b)); }
__device__ __forceinline__ float2 max(float2 a, float2 b) { return make_float2(fmaxf(a.x, b.x), fmaxf(a.y, b.y)); }
__device__ __forceinline__ float2 max(float2 a, float b) { return make_float2(fmaxf(a.x, b), fmaxf(a.y, b)); }
__device__ __forceinline__ float2 min(float2 a, float2 b) { return make_float2(fminf(a.x, b.x), fminf(a.y, b.y)); }
__device__ __forceinline__ float2 min(float2 a, float b) { return make_float2(fminf(a.x, b), fminf(a.y, b)); }
__device__ __forceinline__ float3 sin(float3 a) { return make_float3(sinf(a.x), sinf(a.y), sinf(a.z)); }
__device__ __forceinline__ float3 cos(float3 a) { return make_float3(cosf(a.x), cosf(a.y), cosf(a.z)); }
__device__ __forceinline__ float3 exp(float3 a) { return make_float3(expf(a.x), expf(a.y), expf(a.z)); }
__device__ __forceinline__ float3 sqrt(float3 a) { return make_float3(sqrtf(a.x), sqrtf(a.y), sqrtf(a.z)); }
__device__ __forceinline__ float3 pow(float3 a, float e) { return make_float3(powf(a.x, e), powf(a.y, e), powf(a.z, e)); }
__device__ __forceinline__ float vmax(float3 v) { return fmaxf(fmaxf(v.x, v.y), v.z); }
__device__ __forceinline__ float vmax(float2 v) { return fmaxf(v.x, v.y); }
__device__ __forceinline__ float vmin(float3 v) { return fminf(fminf(v.x, v.y), v.z); }
__device__ __forceinline__ float vmin(float2 v) { return fminf(v.x, v.y); }

// -------------------------------------------------------------------- rotations
__device__ __forceinline__ float3 rotX(float3 p, float a) {
    float s, c; sincosf(a, &s, &c); return make_float3(p.x, c * p.y - s * p.z, s * p.y + c * p.z);
}
__device__ __forceinline__ float3 rotY(float3 p, float a) {
    float s, c; sincosf(a, &s, &c); return make_float3(c * p.x + s * p.z, p.y, -s * p.x + c * p.z);
}
__device__ __forceinline__ float3 rotZ(float3 p, float a) {
    float s, c; sincosf(a, &s, &c); return make_float3(c * p.x - s * p.y, s * p.x + c * p.y, p.z);
}
// Rodrigues: rotate p about the unit axis k by angle a (right-handed).
__device__ __forceinline__ float3 rotAxis(float3 p, float3 k, float a) {
    float s, c; sincosf(a, &s, &c); return p * c + cross(k, p) * s + k * (dot(k, p) * (1.f - c));
}
__device__ __forceinline__ float2 rot2d(float2 p, float a) {
    float s, c; sincosf(a, &s, &c); return make_float2(c * p.x - s * p.y, s * p.x + c * p.y);
}
// hg_sdf pR: rotate the (x, y) pair "x towards y" by a.  Call as pR(p.x, p.z, a).
__device__ __forceinline__ void pR(float& x, float& y, float a) {
    float s, c; sincosf(a, &s, &c); const float nx = c * x + s * y; y = c * y - s * x; x = nx;
}
__device__ __forceinline__ void pR45(float& x, float& y) {
    const float nx = (x + y) * SDF_SQRT_HALF; y = (y - x) * SDF_SQRT_HALF; x = nx;
}
struct Mat3 { float3 r0, r1, r2; };          // rows
__device__ __forceinline__ float3 mul(const Mat3& M, float3 v) { return make_float3(dot(M.r0, v), dot(M.r1, v), dot(M.r2, v)); }
__device__ __forceinline__ float3 mul_t(const Mat3& M, float3 v) { return M.r0 * v.x + M.r1 * v.y + M.r2 * v.z; }
__device__ __forceinline__ Mat3 mul(const Mat3& A, const Mat3& B) {
    Mat3 R; R.r0 = mul_t(B, A.r0); R.r1 = mul_t(B, A.r1); R.r2 = mul_t(B, A.r2); return R;
}
__device__ __forceinline__ Mat3 mat3_load(const float* __restrict__ m) {
    Mat3 R; R.r0 = load3(m); R.r1 = load3(m + 3); R.r2 = load3(m + 6); return R;
}
__device__ __forceinline__ Mat3 mat3_rot(float3 k, float a) {      // same matrix as host rot_matrix(k, a)
    float s, c; sincosf(a, &s, &c); const float t = 1.f - c;
    Mat3 R;
    R.r0 = make_float3(c + k.x * k.x * t, k.x * k.y * t - k.z * s, k.x * k.z * t + k.y * s);
    R.r1 = make_float3(k.y * k.x * t + k.z * s, c + k.y * k.y * t, k.y * k.z * t - k.x * s);
    R.r2 = make_float3(k.z * k.x * t - k.y * s, k.z * k.y * t + k.x * s, c + k.z * k.z * t);
    return R;
}

// -------------------------------------------------------------------- camera
struct Cam { float3 eye, fwd, right, up; float tan_half; };
__device__ __forceinline__ Cam cam_look(float3 eye, float3 target, float3 up, float fov_deg) {
    Cam c; c.eye = eye; c.fwd = normalize(target - eye);
    c.right = normalize(cross(c.fwd, up)); c.up = cross(c.right, c.fwd);
    c.tan_half = tanf(0.5f * fov_deg * (SDF_PI / 180.f)); return c;
}
__device__ __forceinline__ Cam cam_load(const float* __restrict__ P) {
    Cam c; c.eye = load3(P); c.fwd = load3(P + 3); c.right = load3(P + 6); c.up = load3(P + 9); c.tan_half = P[12];
    return c;
}
// Direction through continuous pixel coords (px, py) (pixel centres at i + 0.5, y down), aspect from W/H.
__device__ __forceinline__ float3 cam_dir(const Cam& c, float px, float py, int W, int H) {
    const float s = c.tan_half / (0.5f * H);
    return normalize(c.fwd + c.right * ((px - 0.5f * W) * s) + c.up * ((0.5f * H - py) * s));
}
__device__ __forceinline__ float3 cam_ray(const Cam& c, int x, int y, int W, int H) {
    return cam_dir(c, x + 0.5f, y + 0.5f, W, H);
}
__device__ __forceinline__ float pixel_angle(const Cam& c, int H) { return 2.f * c.tan_half / H; }
__device__ __forceinline__ float3 camera_ray(int x, int y, int W, int H, float3 eye, float3 target, float3 up, float fov_deg) {
    return cam_ray(cam_look(eye, target, up, fov_deg), x, y, W, H);
}

// -------------------------------------------------------------------- ray / bound intersections
__device__ __forceinline__ float3 sdf_safe_rcp(float3 d) {
    return make_float3(1.f / (fabsf(d.x) > 1e-8f ? d.x : copysignf(1e-8f, d.x)),
                       1.f / (fabsf(d.y) > 1e-8f ? d.y : copysignf(1e-8f, d.y)),
                       1.f / (fabsf(d.z) > 1e-8f ? d.z : copysignf(1e-8f, d.z)));
}
// iq iBox: axis-aligned box of half-size rad at the origin -> (tnear, tfar); miss if tnear > tfar or tfar < 0.
__device__ __forceinline__ float2 iBox(float3 ro, float3 rd, float3 rad) {
    const float3 m = sdf_safe_rcp(rd), n = m * ro, k = abs(m) * rad;
    const float3 t1 = -n - k, t2 = -n + k;
    return make_float2(vmax(t1), vmin(t2));
}
__device__ __forceinline__ float2 iSphere(float3 ro, float3 rd, float r) {
    const float b = dot(ro, rd), c = dot(ro, ro) - r * r, h = b * b - c;
    if (h < 0.f) return make_float2(1e20f, -1e20f);
    const float s = sqrtf(h); return make_float2(-b - s, -b + s);
}
// plane dot(p, n) + h = 0 -> t (negative if behind or parallel)
__device__ __forceinline__ float iPlane(float3 ro, float3 rd, float3 n, float h) {
    const float dn = dot(rd, n);
    return fabsf(dn) < 1e-8f ? -1.f : -(dot(ro, n) + h) / dn;
}

// -------------------------------------------------------------------- primitives: iq (MIT, Xds3zN / distfunctions)
__device__ __forceinline__ float sdSphere(float3 p, float r) { return length(p) - r; }
__device__ __forceinline__ float sdBox(float3 p, float3 b) {
    const float3 d = abs(p) - b; return fminf(vmax(d), 0.f) + length(max(d, 0.f));
}
__device__ __forceinline__ float sdRoundBox(float3 p, float3 b, float r) {
    const float3 q = abs(p) - b + r; return length(max(q, 0.f)) + fminf(vmax(q), 0.f) - r;
}
__device__ __forceinline__ float sdBoxFrame(float3 p, float3 b, float e) {
    p = abs(p) - b; const float3 q = abs(p + e) - e;
    return fminf(fminf(
        length(max(f3(p.x, q.y, q.z), 0.f)) + fminf(fmaxf(p.x, fmaxf(q.y, q.z)), 0.f),
        length(max(f3(q.x, p.y, q.z), 0.f)) + fminf(fmaxf(q.x, fmaxf(p.y, q.z)), 0.f)),
        length(max(f3(q.x, q.y, p.z), 0.f)) + fminf(fmaxf(q.x, fmaxf(q.y, p.z)), 0.f));
}
__device__ __forceinline__ float sdTorus(float3 p, float2 t) {     // XZ plane, t = (major R, minor r)
    return length(f2(length(f2(p.x, p.z)) - t.x, p.y)) - t.y;
}
__device__ __forceinline__ float sdLink(float3 p, float le, float r1, float r2) {
    const float qy = fmaxf(fabsf(p.y) - le, 0.f);
    return length(f2(length(f2(p.x, qy)) - r1, p.z)) - r2;
}
__device__ __forceinline__ float sdCylinder(float3 p, float2 h) {  // vertical, h = (radius, half height)
    const float2 d = abs(f2(length(f2(p.x, p.z)), p.y)) - h;
    return fminf(fmaxf(d.x, d.y), 0.f) + length(max(d, 0.f));
}
__device__ __forceinline__ float sdCylinder(float3 p, float3 a, float3 b, float r) {   // arbitrary axis a->b
    const float3 pa = p - a, ba = b - a;
    const float baba = dot(ba, ba), paba = dot(pa, ba);
    const float x = length(pa * baba - ba * paba) - r * baba;
    const float y = fabsf(paba - baba * 0.5f) - baba * 0.5f;
    const float x2 = x * x, y2 = y * y * baba;
    const float d = (fmaxf(x, y) < 0.f) ? -fminf(x2, y2) : (((x > 0.f) ? x2 : 0.f) + ((y > 0.f) ? y2 : 0.f));
    return sign(d) * sqrtf(fabsf(d)) / baba;
}
__device__ __forceinline__ float sdCapsule(float3 p, float3 a, float3 b, float r) {
    const float3 pa = p - a, ba = b - a;
    const float h = saturate(dot(pa, ba) / dot(ba, ba));
    return length(pa - ba * h) - r;
}
__device__ __forceinline__ float sdVerticalCapsule(float3 p, float h, float r) {   // from y=0 to y=h
    p.y -= clamp(p.y, 0.f, h); return length(p) - r;
}
__device__ __forceinline__ float sdCappedCone(float3 p, float h, float r1, float r2) {
    const float2 q = f2(length(f2(p.x, p.z)), p.y);
    const float2 k1 = f2(r2, h), k2 = f2(r2 - r1, 2.f * h);
    const float2 ca = f2(q.x - fminf(q.x, (q.y < 0.f) ? r1 : r2), fabsf(q.y) - h);
    const float2 cb = q - k1 + k2 * saturate(dot(k1 - q, k2) / dot2(k2));
    const float s = (cb.x < 0.f && ca.y < 0.f) ? -1.f : 1.f;
    return s * sqrtf(fminf(dot2(ca), dot2(cb)));
}
__device__ __forceinline__ float sdRoundCone(float3 p, float r1, float r2, float h) {
    const float2 q = f2(length(f2(p.x, p.z)), p.y);
    const float b = (r1 - r2) / h, a = sqrtf(fmaxf(1.f - b * b, 0.f));
    const float k = dot(q, f2(-b, a));
    if (k < 0.f) return length(q) - r1;
    if (k > a * h) return length(q - f2(0.f, h)) - r2;
    return dot(q, f2(a, b)) - r1;
}
__device__ __forceinline__ float sdOctahedron(float3 p, float s) {  // exact
    p = abs(p);
    const float m = p.x + p.y + p.z - s;
    float3 q;
    if (3.f * p.x < m) q = p;
    else if (3.f * p.y < m) q = f3(p.y, p.z, p.x);
    else if (3.f * p.z < m) q = f3(p.z, p.x, p.y);
    else return m * 0.57735027f;
    const float k = clamp(0.5f * (q.z - q.y + s), 0.f, s);
    return length(f3(q.x, q.y - s + k, q.z - k));
}
__device__ __forceinline__ float sdPlane(float3 p, float3 n, float h) { return dot(p, n) + h; }
__device__ __forceinline__ float sdHexPrism(float3 p, float2 h) {   // axis z; h = (apothem, half length)
    const float kx = -0.8660254f, ky = 0.5f, kz = 0.57735f;
    p = abs(p);
    const float t = 2.f * fminf(kx * p.x + ky * p.y, 0.f);
    p.x -= t * kx; p.y -= t * ky;
    const float2 d = f2(length(f2(p.x - clamp(p.x, -kz * h.x, kz * h.x), p.y - h.x)) * sgn(p.y - h.x), p.z - h.y);
    return fminf(fmaxf(d.x, d.y), 0.f) + length(max(d, 0.f));
}
__device__ __forceinline__ float sdTriPrism(float3 p, float2 h) {   // axis z
    const float k = 1.7320508f;
    h.x *= 0.5f * k;
    float px = p.x / h.x, py = p.y / h.x;
    px = fabsf(px) - 1.f; py = py + 1.f / k;
    if (px + k * py > 0.f) { const float nx = (px - k * py) * 0.5f; py = (-k * px - py) * 0.5f; px = nx; }
    px -= clamp(px, -2.f, 0.f);
    const float d1 = length(f2(px, py)) * sgn(-py) * h.x, d2 = fabsf(p.z) - h.y;
    return length(max(f2(d1, d2), 0.f)) + fminf(fmaxf(d1, d2), 0.f);
}
__device__ __forceinline__ float sdEllipsoid(float3 p, float3 r) {  // bound, not exact
    const float k0 = length(p / r), k1 = length(p / (r * r));
    return k0 * (k0 - 1.f) / fmaxf(k1, 1e-8f);
}

// -------------------------------------------------------------------- primitives: hg_sdf (MIT, Mercury)
__constant__ float SDF_GDF[57] = { @GDF@ };
__device__ __forceinline__ float fSphere(float3 p, float r) { return length(p) - r; }
__device__ __forceinline__ float fPlane(float3 p, float3 n, float dist) { return dot(p, n) + dist; }
__device__ __forceinline__ float fBoxCheap(float3 p, float3 b) { return vmax(abs(p) - b); }
__device__ __forceinline__ float fBox(float3 p, float3 b) {
    const float3 d = abs(p) - b; return length(max(d, 0.f)) + vmax(min(d, 0.f));
}
__device__ __forceinline__ float fBox2Cheap(float2 p, float2 b) { return vmax(abs(p) - b); }
__device__ __forceinline__ float fBox2(float2 p, float2 b) {
    const float2 d = abs(p) - b; return length(max(d, 0.f)) + vmax(min(d, 0.f));
}
__device__ __forceinline__ float fCorner(float2 p) { return length(max(p, 0.f)) + vmax(min(p, 0.f)); }
__device__ __forceinline__ float fCylinder(float3 p, float r, float height) {
    return fmaxf(length(f2(p.x, p.z)) - r, fabsf(p.y) - height);
}
__device__ __forceinline__ float fCapsule(float3 p, float r, float c) {
    return fabsf(p.y) < c ? length(f2(p.x, p.z)) - r : length(f3(p.x, fabsf(p.y) - c, p.z)) - r;
}
__device__ __forceinline__ float fLineSegment(float3 p, float3 a, float3 b) {
    const float3 ab = b - a; const float t = saturate(dot(p - a, ab) / dot(ab, ab));
    return length((ab * t + a) - p);
}
__device__ __forceinline__ float fCapsule(float3 p, float3 a, float3 b, float r) { return fLineSegment(p, a, b) - r; }
__device__ __forceinline__ float fTorus(float3 p, float smallRadius, float largeRadius) {
    return length(f2(length(f2(p.x, p.z)) - largeRadius, p.y)) - smallRadius;
}
__device__ __forceinline__ float fCircle(float3 p, float r) { return length(f2(p.y, length(f2(p.x, p.z)) - r)); }
__device__ __forceinline__ float fDisc(float3 p, float r) {
    const float l = length(f2(p.x, p.z)) - r; return l < 0.f ? fabsf(p.y) : length(f2(p.y, l));
}
__device__ __forceinline__ float fHexagonCircumcircle(float3 p, float2 h) {
    const float3 q = abs(p);
    return fmaxf(q.y - h.y, fmaxf(q.x * 0.8660254f + q.z * 0.5f, q.z) - h.x);
}
__device__ __forceinline__ float fHexagonIncircle(float3 p, float2 h) {
    return fHexagonCircumcircle(p, f2(h.x * 0.8660254f, h.y));
}
__device__ __forceinline__ float fCone(float3 p, float radius, float height) {
    const float2 q = f2(length(f2(p.x, p.z)), p.y);
    const float2 tip = q - f2(0.f, height);
    const float2 mantleDir = normalize(f2(height, radius));
    const float mantle = dot(tip, mantleDir);
    float d = fmaxf(mantle, -q.y);
    const float projected = dot(tip, f2(mantleDir.y, -mantleDir.x));
    if ((q.y > height) && (projected < 0.f)) d = fmaxf(d, length(tip));
    if ((q.x > radius) && (projected > length(f2(height, radius)))) d = fmaxf(d, length(q - f2(radius, 0.f)));
    return d;
}
// Generalized Distance Functions (Akleman & Chen) as in hg_sdf; sharp (no exponent) and bulging (exponent e).
__device__ __forceinline__ float fGDF(float3 p, float r, int begin, int end) {
    float d = 0.f;
    for (int i = begin; i <= end; ++i)
        d = fmaxf(d, fabsf(p.x * SDF_GDF[3 * i] + p.y * SDF_GDF[3 * i + 1] + p.z * SDF_GDF[3 * i + 2]));
    return d - r;
}
__device__ __forceinline__ float fGDF(float3 p, float r, float e, int begin, int end) {
    float d = 0.f;
    for (int i = begin; i <= end; ++i)
        d += powf(fabsf(p.x * SDF_GDF[3 * i] + p.y * SDF_GDF[3 * i + 1] + p.z * SDF_GDF[3 * i + 2]), e);
    return powf(d, 1.f / e) - r;
}
__device__ __forceinline__ float fOctahedron(float3 p, float r) { return fGDF(p, r, 3, 6); }
__device__ __forceinline__ float fDodecahedron(float3 p, float r) { return fGDF(p, r, 13, 18); }
__device__ __forceinline__ float fIcosahedron(float3 p, float r) { return fGDF(p, r, 3, 12); }
__device__ __forceinline__ float fTruncatedOctahedron(float3 p, float r) { return fGDF(p, r, 0, 6); }
__device__ __forceinline__ float fTruncatedIcosahedron(float3 p, float r) { return fGDF(p, r, 3, 18); }
__device__ __forceinline__ float fOctahedron(float3 p, float r, float e) { return fGDF(p, r, e, 3, 6); }
__device__ __forceinline__ float fDodecahedron(float3 p, float r, float e) { return fGDF(p, r, e, 13, 18); }
__device__ __forceinline__ float fIcosahedron(float3 p, float r, float e) { return fGDF(p, r, e, 3, 12); }
__device__ __forceinline__ float fTruncatedOctahedron(float3 p, float r, float e) { return fGDF(p, r, e, 0, 6); }
__device__ __forceinline__ float fTruncatedIcosahedron(float3 p, float r, float e) { return fGDF(p, r, e, 3, 18); }

// -------------------------------------------------------------------- combination operators
__device__ __forceinline__ float opUnion(float a, float b) { return fminf(a, b); }
__device__ __forceinline__ float opSubtract(float a, float b) { return fmaxf(a, -b); }      // a minus b
__device__ __forceinline__ float opIntersect(float a, float b) { return fmaxf(a, b); }
__device__ __forceinline__ float opOnion(float d, float thick) { return fabsf(d) - thick; }
// iq polynomial smooth min: blends where |a - b| < k (max deviation k / 4).
__device__ __forceinline__ float smin(float a, float b, float k) {
    const float h = fmaxf(k - fabsf(a - b), 0.f) / fmaxf(k, 1e-6f);
    return fminf(a, b) - h * h * k * 0.25f;
}
__device__ __forceinline__ float smax(float a, float b, float k) { return -smin(-a, -b, k); }
__device__ __forceinline__ float ssub(float a, float b, float k) { return smax(a, -b, k); }  // a minus b, smooth
// smooth min that also returns the blend weight of b (0 = all a, 1 = all b) for mixing materials
__device__ __forceinline__ float2 smin_mix(float a, float b, float k) {
    const float h = saturate(0.5f + 0.5f * (b - a) / fmaxf(k, 1e-6f));
    return f2(mix(b, a, h) - k * h * (1.f - h), 1.f - h);
}
// hg_sdf operators (assume surfaces meet at right angles; r = feature size)
__device__ __forceinline__ float fOpUnionChamfer(float a, float b, float r) { return fminf(fminf(a, b), (a - r + b) * SDF_SQRT_HALF); }
__device__ __forceinline__ float fOpIntersectionChamfer(float a, float b, float r) { return fmaxf(fmaxf(a, b), (a + r + b) * SDF_SQRT_HALF); }
__device__ __forceinline__ float fOpDifferenceChamfer(float a, float b, float r) { return fOpIntersectionChamfer(a, -b, r); }
__device__ __forceinline__ float fOpUnionRound(float a, float b, float r) {
    return fmaxf(r, fminf(a, b)) - length(max(f2(r - a, r - b), 0.f));
}
__device__ __forceinline__ float fOpIntersectionRound(float a, float b, float r) {
    return fminf(-r, fmaxf(a, b)) + length(max(f2(r + a, r + b), 0.f));
}
__device__ __forceinline__ float fOpDifferenceRound(float a, float b, float r) { return fOpIntersectionRound(a, -b, r); }
__device__ __forceinline__ float pMod1(float& p, float size);
__device__ __forceinline__ float fOpUnionColumns(float a, float b, float r, float n) {
    if ((a < r) && (b < r)) {
        float px = a, py = b;
        const float colr = r * 1.41421356f / ((n - 1.f) * 2.f + 1.41421356f);
        pR45(px, py);
        px -= SDF_SQRT_HALF * r;
        px += colr * 1.41421356f;
        if (mod(n, 2.f) == 1.f) py += colr;
        pMod1(py, colr * 2.f);
        float res = length(f2(px, py)) - colr;
        res = fminf(res, px); res = fminf(res, a);
        return fminf(res, b);
    }
    return fminf(a, b);
}
__device__ __forceinline__ float fOpDifferenceColumns(float a, float b, float r, float n) {
    a = -a;
    const float m = fminf(a, b);
    if ((a < r) && (b < r)) {
        float px = a, py = b;
        const float colr = r * 1.41421356f / ((n - 1.f) * 2.f + 1.41421356f);
        pR45(px, py);
        py += colr;
        px -= SDF_SQRT_HALF * r;
        px += -colr * SDF_SQRT_HALF;
        if (mod(n, 2.f) == 1.f) py += colr;
        pMod1(py, colr * 2.f);
        float res = -length(f2(px, py)) + colr;
        res = fmaxf(res, px); res = fminf(res, a);
        return -fminf(res, b);
    }
    return -m;
}
__device__ __forceinline__ float fOpIntersectionColumns(float a, float b, float r, float n) { return fOpDifferenceColumns(a, -b, r, n); }
__device__ __forceinline__ float fOpUnionStairs(float a, float b, float r, float n) {
    const float s = r / n, u = b - r;
    return fminf(fminf(a, b), 0.5f * (u + a + fabsf(mod(u - a + s, 2.f * s) - s)));
}
__device__ __forceinline__ float fOpIntersectionStairs(float a, float b, float r, float n) { return -fOpUnionStairs(-a, -b, r, n); }
__device__ __forceinline__ float fOpDifferenceStairs(float a, float b, float r, float n) { return -fOpUnionStairs(-a, b, r, n); }
__device__ __forceinline__ float fOpUnionSoft(float a, float b, float r) {
    const float e = fmaxf(r - fabsf(a - b), 0.f); return fminf(a, b) - e * e * 0.25f / r;
}
__device__ __forceinline__ float fOpPipe(float a, float b, float r) { return length(f2(a, b)) - r; }
__device__ __forceinline__ float fOpEngrave(float a, float b, float r) { return fmaxf(a, (a + r - fabsf(b)) * SDF_SQRT_HALF); }
__device__ __forceinline__ float fOpGroove(float a, float b, float ra, float rb) { return fmaxf(a, fminf(a + ra, rb - fabsf(b))); }
__device__ __forceinline__ float fOpTongue(float a, float b, float ra, float rb) { return fminf(a, fmaxf(a - ra, fabsf(b) - rb)); }

// -------------------------------------------------------------------- domain operators (hg_sdf)
// Each returns the cell index (cell 0 is centred on the origin); p is modified in place.
__device__ __forceinline__ float pMod1(float& p, float size) {
    const float hs = size * 0.5f, c = floorf((p + hs) / size);
    p = mod(p + hs, size) - hs; return c;
}
__device__ __forceinline__ float pModMirror1(float& p, float size) {
    const float hs = size * 0.5f, c = floorf((p + hs) / size);
    p = mod(p + hs, size) - hs; p *= mod(c, 2.f) * 2.f - 1.f; return c;
}
__device__ __forceinline__ float pModSingle1(float& p, float size) {
    const float hs = size * 0.5f, c = floorf((p + hs) / size);
    if (p >= 0.f) p = mod(p + hs, size) - hs;
    return c;
}
__device__ __forceinline__ float pModInterval1(float& p, float size, float start, float stop) {
    const float hs = size * 0.5f; float c = floorf((p + hs) / size);
    p = mod(p + hs, size) - hs;
    if (c > stop) { p += size * (c - stop); c = stop; }
    if (c < start) { p += size * (c - start); c = start; }
    return c;
}
// Repeat around the origin of the (x, y) plane: pModPolar(p.x, p.z, 8.f) gives 8 sectors around the y axis.
__device__ __forceinline__ float pModPolar(float& x, float& y, float reps) {
    const float ang = SDF_TAU / reps;
    float a = atan2f(y, x) + ang * 0.5f;
    const float r = sqrtf(x * x + y * y);
    float c = floorf(a / ang);
    a = mod(a, ang) - ang * 0.5f;
    float s, co; sincosf(a, &s, &co); x = co * r; y = s * r;
    if (fabsf(c) >= reps * 0.5f) c = fabsf(c);
    return c;
}
__device__ __forceinline__ float2 pMod2(float& x, float& y, float sx, float sy) {
    return f2(pMod1(x, sx), pMod1(y, sy));
}
__device__ __forceinline__ float2 pModMirror2(float& x, float& y, float sx, float sy) {
    return f2(pModMirror1(x, sx), pModMirror1(y, sy));
}
__device__ __forceinline__ float2 pModGrid2(float& x, float& y, float sx, float sy) {
    const float cx = pModMirror1(x, sx), cy = pModMirror1(y, sy);
    x -= sx * 0.5f; y -= sy * 0.5f;
    if (x > y) { const float t = x; x = y; y = t; }
    return f2(floorf(cx * 0.5f), floorf(cy * 0.5f));
}
__device__ __forceinline__ float3 pMod3(float3& p, float3 size) {
    const float3 c = floor((p + size * 0.5f) / size);
    p = mod(p + size * 0.5f, size) - size * 0.5f; return c;
}
__device__ __forceinline__ float pMirror(float& p, float dist) { const float s = sgn(p); p = fabsf(p) - dist; return s; }
__device__ __forceinline__ float2 pMirrorOctant(float& x, float& y, float dx, float dy) {
    const float2 s = f2(sgn(x), sgn(y));
    pMirror(x, dx); pMirror(y, dy);
    if (y > x) { const float t = x; x = y; y = t; }
    return s;
}
__device__ __forceinline__ float pReflect(float3& p, float3 n, float offset) {
    const float t = dot(p, n) + offset;
    if (t < 0.f) p = p - n * (2.f * t);
    return sgn(t);
}
// iq elongation: q = p - clamp(p, -h, h); evaluate a primitive at q to stretch it by 2h.
__device__ __forceinline__ float3 elongate(float3 p, float3 h) { return p - clamp(p, -h, h); }
// Domain deformations (NOT distance preserving: march with step_scale ~0.5-0.7)
__device__ __forceinline__ float3 twistY(float3 p, float k) {
    float s, c; sincosf(k * p.y, &s, &c); return f3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
}
__device__ __forceinline__ float3 bendX(float3 p, float k) {
    float s, c; sincosf(k * p.x, &s, &c); return f3(c * p.x - s * p.y, s * p.x + c * p.y, p.z);
}

// -------------------------------------------------------------------- fractal folds and distance estimators
__device__ __forceinline__ void boxFold(float3& p, float limit) { p = clamp(p, -limit, limit) * 2.f - p; }
// Mandelbox sphere fold (branch-free): scales by fixR2/minR2 inside minR, by fixR2/r2 between, 1 outside fixR.
__device__ __forceinline__ void sphereFold(float3& p, float& dr, float minR2, float fixR2) {
    const float k = fmaxf(fixR2 / fmaxf(dot(p, p), minR2), 1.f);
    p *= k; dr *= k;
}
__device__ __forceinline__ void foldPlane(float3& p, float3 n) { p = p - n * (2.f * fminf(dot(p, n), 0.f)); }
__device__ __forceinline__ void foldTetra(float3& p) {         // Sierpinski tetrahedron folds
    if (p.x + p.y < 0.f) { const float t = -p.y; p.y = -p.x; p.x = t; }
    if (p.x + p.z < 0.f) { const float t = -p.z; p.z = -p.x; p.x = t; }
    if (p.y + p.z < 0.f) { const float t = -p.z; p.z = -p.y; p.y = t; }
}
__device__ __forceinline__ void foldMenger(float3& p) {        // abs, then sort so x >= y >= z
    p = abs(p);
    float a = fmaxf(p.x, p.y), b = fminf(p.x, p.y);
    const float c = fminf(b, p.z); b = fmaxf(b, p.z);
    p = f3(fmaxf(a, b), fminf(a, b), c);
}
__device__ __forceinline__ void foldAbsRot(float3& p, const Mat3& R) { p = mul(R, abs(p)); }
// Mandelbox (Tom Lowe): z = scale * sphereFold(boxFold(z)) + p.  Typical: scale -1.5 / 2..3, minR 0.5, fixR 1.
__device__ float deMandelbox(float3 p, float scale, float minR, float fixR, int iters, float& trap) {
    float3 z = p; float dr = 1.f; trap = 1e10f;
    const float minR2 = minR * minR, fixR2 = fixR * fixR;
    for (int i = 0; i < iters; ++i) {
        boxFold(z, 1.f);
        sphereFold(z, dr, minR2, fixR2);
        z = z * scale + p;
        dr = dr * fabsf(scale) + 1.f;
        trap = fminf(trap, dot(z, z));
    }
    return length(z) / fabsf(dr);
}
// Menger-style KIFS (scale 3, offset (1,1,1) = Menger sponge of half-size 1)
__device__ float deMenger(float3 p, int iters, float scale, float3 offset, float& trap) {
    float s = 1.f; trap = 1e10f;
    const float3 o = offset * (scale - 1.f);
    for (int i = 0; i < iters; ++i) {
        foldMenger(p);
        p = p * scale - o;
        if (p.z < -0.5f * o.z) p.z += o.z;
        s *= scale;
        trap = fminf(trap, dot(p, p));
    }
    return sdBox(p, f3(1.f)) / s;
}
// Sierpinski tetrahedron KIFS (scale 2, offset (1,1,1))
__device__ float deSierpinski(float3 p, int iters, float scale, float3 offset, float& trap) {
    float s = 1.f; trap = 1e10f;
    for (int i = 0; i < iters; ++i) {
        foldTetra(p);
        p = p * scale - offset * (scale - 1.f);
        s *= scale;
        trap = fminf(trap, dot(p, p));
    }
    return (length(p) - 1.f) / s;
}
// Generic rotating KIFS: per iteration abs-fold, tetra fold, rotate by R (host rot_matrix), scale about offset.
__device__ float deKifs(float3 p, int iters, float scale, float3 offset, const Mat3& R, float& trap) {
    float s = 1.f; trap = 1e10f;
    for (int i = 0; i < iters; ++i) {
        p = abs(p);
        foldTetra(p);
        p = mul(R, p);
        p = p * scale - offset * (scale - 1.f);
        s *= scale;
        trap = fminf(trap, dot(p, p));
    }
    return (length(p) - 1.f) / s;
}
// Mandelbulb (White / Nylander formula, y up).  Expensive (powf/acosf/atan2f per iteration): render_scale <= 0.5.
__device__ float deMandelbulb(float3 p, float power, int iters, float& trap) {
    float3 z = p; float dr = 1.f, r = length(z); trap = 1e10f;
    for (int i = 0; i < iters; ++i) {
        r = fmaxf(length(z), 1e-8f);
        if (r > 2.f) break;
        float theta = acosf(clamp(z.y / r, -1.f, 1.f)), phi = atan2f(z.x, z.z);
        dr = powf(r, power - 1.f) * power * dr + 1.f;
        const float zr = powf(r, power);
        theta *= power; phi *= power;
        float st, ct, sp, cp; sincosf(theta, &st, &ct); sincosf(phi, &sp, &cp);
        z = f3(st * sp, ct, st * cp) * zr + p;
        trap = fminf(trap, dot(z, z));
    }
    return 0.5f * logf(r) * r / dr;
}

// -------------------------------------------------------------------- hashing / noise (uses HELPERS' hash1)
__device__ __forceinline__ float hash13(float3 c) {
    return hash1((unsigned int)(int)floorf(c.x), (unsigned int)(int)floorf(c.y), (unsigned int)(int)floorf(c.z));
}
__device__ __forceinline__ float3 hash33(float3 c) {
    const unsigned int a = (unsigned int)(int)floorf(c.x), b = (unsigned int)(int)floorf(c.y), d = (unsigned int)(int)floorf(c.z);
    return f3(hash1(a, b, d), hash1(a, b, d + 0x51ed27u), hash1(a, b, d + 0xa3c59bu));
}
__device__ float noise3(float3 p, unsigned int seed = 0u) {
    const float3 i = floor(p); float3 f = p - i;
    f = f * f * (3.f - 2.f * f);
    const int ix = (int)i.x, iy = (int)i.y, iz = (int)i.z;
    const unsigned int s = seed * 0x9E3779B9u;
    const float a = hash1(ix, iy, iz + s), b = hash1(ix + 1, iy, iz + s);
    const float c = hash1(ix, iy + 1, iz + s), d = hash1(ix + 1, iy + 1, iz + s);
    const float e = hash1(ix, iy, iz + 1 + s), g = hash1(ix + 1, iy, iz + 1 + s);
    const float h = hash1(ix, iy + 1, iz + 1 + s), k = hash1(ix + 1, iy + 1, iz + 1 + s);
    return mix(mix(mix(a, b, f.x), mix(c, d, f.x), f.y), mix(mix(e, g, f.x), mix(h, k, f.x), f.y), f.z);
}
__device__ float fbm3(float3 p, int octaves, unsigned int seed = 0u) {
    float s = 0.f, amp = 0.5f, norm = 0.f;
    for (int o = 0; o < octaves; ++o) {
        s += amp * noise3(p, seed + 17u * o); norm += amp;
        p = f3(1.6f * p.x + 1.2f * p.z, 1.97f * p.y, -1.2f * p.x + 1.6f * p.z); amp *= 0.5f;
    }
    return s / fmaxf(norm, 1e-6f);
}

// -------------------------------------------------------------------- marching
struct Hit { float t; float d; int steps; bool hit; float glow; };
// Sphere tracing.  Stops when |d| < eps * t (eps = pixel_angle(cam, H) gives a one-pixel cone), when t > tmax,
// or after max_steps (then hit = false, t = where it got to: treat as far / fog).  step_scale < 1 for fields
// that overestimate (twists, displacement, some fractals).  glow_k > 0 accumulates sum 1 / (1 + glow_k d^2).
template <class F>
__device__ __forceinline__ Hit march(const F& map, float3 ro, float3 rd, float tmin, float tmax, int max_steps,
                                     float eps, float step_scale = 1.f, float glow_k = 0.f) {
    Hit h; h.t = tmin; h.d = 1e10f; h.steps = 0; h.hit = false; h.glow = 0.f;
    for (int i = 0; i < max_steps; ++i) {
        const float d = map(ro + rd * h.t);
        h.d = d; h.steps = i + 1;
        if (glow_k > 0.f) h.glow += 1.f / (1.f + glow_k * d * d);
        if (fabsf(d) < fmaxf(eps * h.t, 1e-5f)) { h.hit = true; break; }
        h.t += d * step_scale;
        if (h.t > tmax) break;
    }
    return h;
}
// Over-relaxed sphere tracing (Keinert et al. 2014, "Enhanced Sphere Tracing"): steps omega * d and falls back to
// plain steps (omega = 1) the first time consecutive unbounding spheres stop overlapping.  Can save steps on smooth
// open fields; on divergent scenes (sdf_demo) it gave no gain, so A/B it.  Same arguments as march(); step_scale
// scales the safe radius for overestimating fields.
template <class F>
__device__ __forceinline__ Hit march_relaxed(const F& map, float3 ro, float3 rd, float tmin, float tmax, int max_steps,
                                             float eps, float step_scale = 1.f, float omega = 1.5f, float glow_k = 0.f) {
    Hit h; h.t = tmin; h.d = 1e10f; h.steps = 0; h.hit = false; h.glow = 0.f;
    float prev_r = 0.f, step = 0.f;
    for (int i = 0; i < max_steps; ++i) {
        const float d = map(ro + rd * h.t) * step_scale;
        const float r = fabsf(d);
        h.steps = i + 1;
        const bool fail = omega > 1.f && (r + prev_r) < step;
        if (fail) {                                             // overshot: back up inside the previous sphere
            step -= omega * step; omega = 1.f;
        } else {
            h.d = d;
            if (glow_k > 0.f) h.glow += 1.f / (1.f + glow_k * d * d);
            if (r < fmaxf(eps * h.t, 1e-5f)) { h.hit = true; break; }
            step = d * omega;
        }
        prev_r = r;
        h.t += step;
        if (h.t > tmax) break;
    }
    return h;
}
// Tetrahedral normal (iq): 4 taps, kept in a loop so map() is inlined once.
template <class F>
__device__ __forceinline__ float3 calc_normal(const F& map, float3 p, float h) {
    float3 n = f3(0.f);
    #pragma unroll 1
    for (int i = 0; i < 4; ++i) {
        const float3 e = f3((float)((((i + 3) >> 1) & 1) * 2 - 1), (float)(((i >> 1) & 1) * 2 - 1),
                            (float)((i & 1) * 2 - 1)) * 0.5773f;
        n += e * map(p + e * h);
    }
    return normalize(n);
}
// Soft shadow (iq): k ~ 4 (very soft) .. 32 (hard).  Start at ro = pos + n * small.
template <class F>
__device__ __forceinline__ float soft_shadow(const F& map, float3 ro, float3 rd, float mint, float maxt, float k,
                                             int max_steps) {
    float res = 1.f, t = mint;
    const float smax_ = 2.5f * maxt / fmaxf((float)max_steps, 1.f);
    #pragma unroll 1
    for (int i = 0; i < max_steps; ++i) {
        const float h = map(ro + rd * t);
        res = fminf(res, k * h / t);
        t += clamp(h, 0.25f * mint, smax_);
        if (res < 0.004f || t > maxt) break;
    }
    res = saturate(res);
    return res * res * (3.f - 2.f * res);
}
// Ambient occlusion (iq, scaled): samples at 0.01 .. scale along the normal.
template <class F>
__device__ __forceinline__ float calc_ao(const F& map, float3 p, float3 n, float scale, int samples) {
    float occ = 0.f, sca = 1.f;
    const float inv = 1.f / fmaxf((float)(samples - 1), 1.f);
    #pragma unroll 1
    for (int i = 0; i < samples; ++i) {
        const float h = 0.01f + scale * i * inv;
        occ += (h - map(p + n * h)) * sca;
        sca *= 0.9f;
    }
    return saturate(1.f - 1.8f * occ / (scale * fmaxf((float)samples, 1.f)));
}

// -------------------------------------------------------------------- lighting
struct Rig { float3 key_dir, key_col, fill_col, rim_col, amb_col; float spec, shin; };
__device__ __forceinline__ Rig rig_default() {
    Rig L;
    L.key_dir = normalize(f3(0.55f, 0.75f, -0.35f)); L.key_col = f3(1.7f, 1.5f, 1.3f);
    L.fill_col = f3(0.20f, 0.26f, 0.38f); L.rim_col = f3(0.55f, 0.65f, 0.85f); L.amb_col = f3(0.04f);
    L.spec = 0.6f; L.shin = 40.f; return L;
}
__device__ __forceinline__ Rig rig_load(const float* __restrict__ P) {
    Rig L; L.key_dir = load3(P); L.key_col = load3(P + 3); L.fill_col = load3(P + 6); L.rim_col = load3(P + 9);
    L.amb_col = load3(P + 12); L.spec = P[15]; L.shin = P[16]; return L;
}
// key: Lambert * shadow + Blinn-Phong spec (Schlick-weighted); fill: sky hemisphere * ao; rim: fresnel edge * ao.
__device__ __forceinline__ float3 shade(const Rig& L, float3 alb, float3 n, float3 rd, float shadow, float ao) {
    const float dif = saturate(dot(n, L.key_dir)) * shadow;
    const float3 hv = normalize(L.key_dir - rd);
    const float fre = 1.f - saturate(dot(n, -rd));
    const float fre5 = fre * fre * fre * fre * fre;
    const float spe = powf(saturate(dot(n, hv)), L.shin) * dif * L.spec * (0.25f + 0.75f * fre5) * (L.shin + 8.f) * 0.04f;
    const float fill = (0.55f + 0.45f * n.y) * ao;
    const float rim = fre * fre * fre * ao;
    return alb * (L.key_col * dif + L.fill_col * fill + L.amb_col * ao) + L.rim_col * rim + L.key_col * spe;
}
__device__ __forceinline__ float3 fog_exp(float3 col, float3 fogc, float t, float density) {
    return mix(col, fogc, 1.f - expf(-density * t));
}
__device__ __forceinline__ float3 fog_exp2(float3 col, float3 fogc, float t, float density) {
    return mix(col, fogc, 1.f - expf(-density * density * t * t));
}

// -------------------------------------------------------------------- tonemap / encode / store
__device__ __forceinline__ float3 tonemap_aces(float3 x) {      // Narkowicz 2015 fit
    x = max(x, 0.f);
    return saturate((x * (2.51f * x + 0.03f)) / (x * (2.43f * x + 0.59f) + 0.14f));
}
__device__ __forceinline__ float3 tonemap_reinhard(float3 x) { x = max(x, 0.f); return x / (1.f + x); }
__device__ __forceinline__ float srgb_enc1(float c) {
    c = saturate(c); return c <= 0.0031308f ? 12.92f * c : 1.055f * powf(c, 1.f / 2.4f) - 0.055f;
}
__device__ __forceinline__ float srgb_dec1(float c) {
    c = saturate(c); return c <= 0.04045f ? c / 12.92f : powf((c + 0.055f) / 1.055f, 2.4f);
}
__device__ __forceinline__ float3 linear_to_srgb(float3 c) { return f3(srgb_enc1(c.x), srgb_enc1(c.y), srgb_enc1(c.z)); }
__device__ __forceinline__ float3 srgb_to_linear(float3 c) { return f3(srgb_dec1(c.x), srgb_dec1(c.y), srgb_dec1(c.z)); }
__device__ __forceinline__ float3 vignette(float3 c, int x, int y, int W, int H, float amt) {
    const float ex = (x + 0.5f) / W - 0.5f, ey = (y + 0.5f) / H - 0.5f;
    return c * (1.f - amt * (ex * ex + ey * ey) * 2.f);
}
// Pack sRGB-encoded 0..1 colour to uchar4 with +-1 LSB triangular dither (kills banding in fog gradients).
__device__ __forceinline__ void store_srgb(uchar4* __restrict__ out, int x, int y, int W, float3 c) {
    const float d = hash1(x, y, 0x5bd1e995u) + hash1(x, y, 0x1b873593u) - 1.f;
    out[y * W + x] = make_uchar4((unsigned char)fminf(fmaxf(c.x * 255.f + d + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.y * 255.f + d + 0.5f, 0.f), 255.f),
                                 (unsigned char)fminf(fmaxf(c.z * 255.f + d + 0.5f, 0.f), 255.f), 255);
}
// ==================================================================== end sdf3d
""".replace("@GDF@", _gdf_table())


# The documented minimal scene kernel (compiles against HELPERS + PALETTE_CUDA + SDF3D).
SKELETON = r"""
// P: [0..12] camera (camera_pack)  [13..24] palette (palette_coeffs)  [25] period [26] drift [27] gain
//    [28] fog density [29] time phase (wrapped)
__device__ float scene(float3 p, const float* __restrict__ P) {
    float3 q = p;
    pMod2(q.x, q.z, 4.f, 4.f);
    float d = sdCylinder(q, f2(0.5f, 3.f));
    d = opUnion(d, p.y + 1.f);
    const float3 c = f3(0.f, 0.f, 6.f + 2.f * sinf(P[29]));
    return smin(d, sdSphere(p - c, 1.f), 0.6f);
}
SDF_KERNEL
void sdf_skeleton(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    auto map = [&](float3 p) { return scene(p, P); };
    const float tmax = 40.f;
    const Hit h = march(map, ro, rd, 0.05f, tmax, 96, pixel_angle(cam, H));
    const float3 fogc = srgb_to_linear(pal(P[26] + 0.5f, P + 13)) * 0.15f;
    float3 col = fogc;
    if (h.hit) {
        const float3 pos = ro + rd * h.t;
        const float3 n = calc_normal(map, pos, 0.5f * pixel_angle(cam, H) * h.t + 1e-4f);
        const Rig L = rig_default();
        const float sh = soft_shadow(map, pos + n * 0.02f, L.key_dir, 0.02f, 10.f, 10.f, 16);
        const float ao = calc_ao(map, pos, n, 0.3f, 3);
        const float3 alb = srgb_to_linear(pal(pos.y * 0.2f * P[25] + P[26], P + 13));
        col = shade(L, alb, n, rd, sh, ao);
    }
    col = fog_exp2(col, fogc, fminf(h.t, tmax), P[28]);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
"""


# ----------------------------------------------------------------------------- host-side helpers
def _v(a) -> tuple[float, float, float]:
    return float(a[0]), float(a[1]), float(a[2])


def _norm(a) -> tuple[float, float, float]:
    x, y, z = _v(a)
    n = math.sqrt(x * x + y * y + z * z) or 1.0
    return x / n, y / n, z / n


def _cross(a, b) -> tuple[float, float, float]:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


def camera_pack(eye: Sequence[float], target: Sequence[float], up: Sequence[float] = (0.0, 1.0, 0.0),
                fov_deg: float = 60.0, roll: float = 0.0) -> list[float]:
    """13 floats for ``cam_load``: eye, forward, right, up (orthonormal) and tan(fov/2) (vertical fov).
    ``roll`` (radians) banks the camera about its forward axis."""
    eye = _v(eye)
    fwd = _norm([t - e for t, e in zip(_v(target), eye)])
    right = _cross(fwd, _norm(up))
    if sum(c * c for c in right) < 1e-12:                        # looking straight along up: pick any right
        right = _cross(fwd, (0.0, 0.0, 1.0) if abs(fwd[2]) < 0.9 else (1.0, 0.0, 0.0))
    right = _norm(right)
    upv = _cross(right, fwd)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        right, upv = (tuple(c * r + s * u for r, u in zip(right, upv)),
                      tuple(-s * r + c * u for r, u in zip(right, upv)))
    return [*eye, *fwd, *right, *upv, math.tan(math.radians(fov_deg) * 0.5)]


def orbit_eye(target: Sequence[float], radius: float, yaw: float, pitch: float) -> tuple[float, float, float]:
    """Camera position on a sphere around ``target``: yaw about +y (0 = on -z side, looking +z), pitch up (radians)."""
    tx, ty, tz = _v(target)
    cp = math.cos(pitch)
    return (tx + radius * cp * math.sin(yaw), ty + radius * math.sin(pitch), tz - radius * cp * math.cos(yaw))


def rot_matrix(axis: Sequence[float], angle: float) -> list[float]:
    """Row-major 3x3 rotation (9 floats) for ``mat3_load``; same as device ``mat3_rot``."""
    x, y, z = _norm(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return [c + x * x * t, x * y * t - z * s, x * z * t + y * s,
            y * x * t + z * s, c + y * y * t, y * z * t - x * s,
            z * x * t - y * s, z * y * t + x * s, c + z * z * t]


def rig_pack(key_dir: Sequence[float] = (0.55, 0.75, -0.35), key_col: Sequence[float] = (1.7, 1.5, 1.3),
             fill_col: Sequence[float] = (0.20, 0.26, 0.38), rim_col: Sequence[float] = (0.55, 0.65, 0.85),
             amb_col: Sequence[float] = (0.04, 0.04, 0.04), spec: float = 0.6, shininess: float = 40.0) -> list[float]:
    """17 floats for ``rig_load`` (linear colours; key_dir points towards the light)."""
    return [*_norm(key_dir), *_v(key_col), *_v(fill_col), *_v(rim_col), *_v(amb_col), float(spec), float(shininess)]


def wrap(x: float, period: float = 2.0 * math.pi * 64.0) -> float:
    """Wrap a host-integrated phase into [0, period).  Use a period that every frequency the kernel applies to the
    phase divides (2*pi*k for sin(k_i * phase) with integer k_i dividing k) so the wrap is invisible; small
    arguments keep fast-math sinf/cosf accurate."""
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


__all__: Iterable[str] = ("SDF3D", "SDF3D_OPTIONS", "SKELETON", "CAM_FLOATS", "RIG_FLOATS", "MAT3_FLOATS",
                          "camera_pack", "orbit_eye", "rot_matrix", "rig_pack", "wrap", "smoothstep")

# -----------------------------------------------------------------------------------------------------------------
# Licence notices for the ported code (MIT requires these to accompany substantial portions):
#
# hg_sdf (https://mercury.sexy/hg_sdf/), SPDX-License-Identifier: MIT OR CC-BY-NC-4.0 -- used under MIT:
#   MIT License
#   Copyright (c) 2011-2021 Mercury Demogroup
#   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
#   documentation files (the "Software"), to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
#   to permit persons to whom the Software is furnished to do so, subject to the following conditions:
#   The above copyright notice and this permission notice shall be included in all copies or substantial portions
#   of the Software.
#   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
#   THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
#   CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.
#
# Raymarching - Primitives (https://www.shadertoy.com/view/Xds3zN), header "The MIT License":
#   Copyright (c) 2013 Inigo Quilez
#   Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
#   documentation files (the "Software"), to deal in the Software without restriction, including without limitation
#   the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
#   to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above
#   copyright notice and this permission notice shall be included in all copies or substantial portions of the
#   Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
#   LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT
#   SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
#   OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
#   DEALINGS IN THE SOFTWARE.
#
# Mandelbox (Tom Lowe, 2010), Mandelbulb (White & Nylander, 2009), KIFS folds (Knighty / fractalforums) are
# mathematical constructions implemented here from scratch.  ACES fit coefficients: K. Narkowicz (2016 blog post).
