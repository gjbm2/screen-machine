"""Venetian Blinds 3D: the frame becomes N slats (horizontal, vertical or angled) that each turn 180 degrees about
their long axis in a travelling wave.  The front faces carry the old picture and the back faces the new one, so the
new image is assembled slat by slat as the wave passes.  The slat edges are palette-coloured metal that catches a
key light, and the gaps open onto a dark recess where the light falling between the slats paints soft stripes.
A perspective camera tilts in slightly while the slats are turning and returns to the flat pose at both ends.

Source: Original (inspired by https://paulbourke.net/stereoscopy/transitions/ and
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/windowblinds.glsl)
Author: smfx (original; inspired by Paul Bourke's 'Ripple slats'; gl-transitions windowblinds by Fabien Benetou,
        MIT, as a 2D reference)
License: MIT (original)
license_class: permissive

How it works: everything happens in the frame's slat-aligned cross-section.  Slat k is a slab (half width hw = half
the pitch, half thickness th) whose centre sits th behind the frame plane, so its front face lies exactly on the
frame quad at rest (and its back face lands there after a half turn).  Per pixel the camera ray is clipped to the
frame prism (|x| <= A, |y| <= 1) and to the z band the turning slabs can occupy; only the 2-8 slats inside that
band are tested, each with an analytic slab test in its own rotated frame.  The front face samples `a` at the
rest position of the hit, the back face samples `b` mirrored across the slat axis so it reads upright after the
half turn; both use FrameMips trilinear lookups (the lod follows the foreshortening) and are relit with
light_frame plus a soft-box sheen that only exists while a slat is turning.  Misses land on a back wall: blurred
`a`/`b` mixed with the palette, darkened, with each nearby slat projected onto it along the light for soft
shadow stripes (penumbra grows with depth).  Silhouettes and face creases of turning slats get 4 rotated-grid
sub-rays; everything else is one ray.  Slat k starts at stagger * order(k) (order = distance from the wave origin
plus a little seeded jitter) and turns over 1 - stagger with an ease-in-out-back (overshoot).  At progress 0 and
1 every slat is coplanar and the camera is flat; the endpoints are early-out copies.

Binder: style lively, 1-2 bars; geometric / elegant.  wave_origin 0 sweeps top to bottom, 0.5 opens from the
middle; angle 90 gives vertical blinds.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import (FRAME3D, FRAME3D_OPTIONS, FrameMips, camera_orbit, flat_distance,
                                                 smoothstep01)
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params
from stream_service.plugins._lib.sdf3d import SDF3D

_MAXS = 64            # slat table capacity (float4 each)
_HDR = 48             # header floats before the slat table (16-byte aligned)
_NU = _HDR + 4 * _MAXS

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define VB_MAXC 8

// U header: 0..12 camera | 13 A | 14 ux | 15 uy | 16 rmin | 17 pitch | 18 hw | 19 th | 20 zc | 21 zw | 22 N
//           23..25 light | 26 edge light | 27 gap shadow | 28 palette phase | 29 relight k | 30 sheen
//           31 camera envelope | 32 progress | 33 interior level | 34 penumbra | 35 palette period | 36 R (slab radius)
// U + 48: float4 per slat (cos theta, sin theta, aa gate (slat or neighbour turning), sin^2 theta)

struct VBHit { float t; int k; int ax; float sg; float a; float b; };   // ax 0/1 end cap (prism x/y), 2 edge, 3 face

__device__ __forceinline__ void vb_slab(float o, float d, float hh, int id, float& tn, float& tf, int& ax, float& sg) {
    const float inv = 1.f / (fabsf(d) > 1e-9f ? d : copysignf(1e-9f, d));
    float t1 = (-hh - o) * inv, t2 = (hh - o) * inv, s = -1.f;
    if (t1 > t2) { const float tt = t1; t1 = t2; t2 = tt; s = 1.f; }
    if (t1 > tn) { tn = t1; ax = id; sg = s; }
    tf = fminf(tf, t2);
}

__device__ __forceinline__ float3 vb_screen(float3 a, float3 b) {
    b = clamp(b, 0.f, 1.f);
    return 1.f - (1.f - a) * (1.f - b);
}

// Nearest slat hit.  flags: also report whether the pixel sits on a silhouette / crease of a turning slat.
__device__ VBHit vb_trace(const float* __restrict__ U, const float4* __restrict__ S, float3 ro, float3 rd, float pa,
                          bool flags, bool& edge)
{
    const float A = U[13], ux = U[14], uy = U[15], vx = -uy, vy = ux;
    const float rmin = U[16], pitch = U[17], hw = U[18], th = U[19], zc = U[20], R = U[36];
    const int N = (int)U[22];
    VBHit h; h.t = 1e30f; h.k = -1; h.ax = -1; h.sg = 0.f; h.a = 0.f; h.b = 0.f;
    float pn = -1e30f, pf = 1e30f, psg = 0.f;
    int pax = -1;
    vb_slab(ro.x, rd.x, A, 0, pn, pf, pax, psg);
    vb_slab(ro.y, rd.y, 1.f, 1, pn, pf, pax, psg);
    if (pn > pf || pf <= 0.f) return h;
    const float rz = rd.z < -1e-6f ? rd.z : -1e-6f;
    float t0 = (zc + R - ro.z) / rz, t1 = (zc - R - ro.z) / rz;
    t0 = fmaxf(t0, pn); t1 = fminf(t1, pf);
    if (t0 > t1 || t1 <= 0.f) return h;
    const float orr = ro.x * vx + ro.y * vy, drr = rd.x * vx + rd.y * vy;
    const float r0 = orr + t0 * drr, r1 = orr + t1 * drr;
    const float ip = 1.f / pitch;
    int k0 = (int)floorf((fminf(r0, r1) - R - rmin) * ip), k1 = (int)floorf((fmaxf(r0, r1) + R - rmin) * ip);
    k0 = max(k0, 0); k1 = min(k1, N - 1);
    if (k1 - k0 >= VB_MAXC) {                         // grazing: keep the slats nearest the camera
        const int kc = (int)floorf((r0 - rmin) * ip);
        k0 = max(k0, kc - VB_MAXC / 2); k1 = min(k1, k0 + VB_MAXC - 1);
    }
    const float delta = 1.25f * pa * fmaxf(t0, 1e-3f);
    const float dz = ro.z - zc;
    for (int k = k0; k <= k1; ++k) {
        const float4 s4 = S[k];
        const float c = s4.x, s = s4.y;
        const float dr = orr - (rmin + (k + 0.5f) * pitch);
        const float oa = dr * c + dz * s, ob = -dr * s + dz * c;
        const float da = drr * c + rd.z * s, db = -drr * s + rd.z * c;
        float tn = pn, tf = pf, sg = psg;
        int ax = pax;
        vb_slab(oa, da, hw, 2, tn, tf, ax, sg);
        vb_slab(ob, db, th, 3, tn, tf, ax, sg);
        if (tn <= tf && tn > 0.f && tn < h.t) {
            h.t = tn; h.k = k; h.ax = ax; h.sg = sg; h.a = oa + tn * da; h.b = ob + tn * db;
        }
        if (flags && !edge && s4.z > 0.5f) {         // silhouette zone: hits the grown slab but not the shrunk one
            float gn = pn, gf = pf, gs = 0.f;
            int gx = 0;
            vb_slab(oa, da, hw + delta, 2, gn, gf, gx, gs);
            vb_slab(ob, db, th + delta, 3, gn, gf, gx, gs);
            if (gn <= gf && gf > 0.f) {
                bool inner = false;
                if (th > delta) {
                    float sn = pn, sf = pf;
                    vb_slab(oa, da, hw - delta, 2, sn, sf, gx, gs);
                    vb_slab(ob, db, th - delta, 3, sn, sf, gx, gs);
                    inner = sn <= sf && sf > 0.f;
                }
                edge = !inner;
            }
        }
    }
    if (flags && !edge && h.k >= 0 && S[h.k].z > 0.5f) {   // creases between faces of a turning slat
        const float4 s4 = S[h.k];
        const float c = s4.x, s = s4.y;
        float d, dot_t;
        if (h.ax == 3) { d = hw - fabsf(h.a); dot_t = (rd.x * vx + rd.y * vy) * c + rd.z * s; }
        else if (h.ax == 2) { d = th - fabsf(h.b); dot_t = -(rd.x * vx + rd.y * vy) * s + rd.z * c; }
        else { d = fminf(hw - fabsf(h.a), th - fabsf(h.b)); dot_t = 0.f; }
        const float px_per = sqrtf(fmaxf(1.f - dot_t * dot_t, 0.f)) / fmaxf(h.t * pa, 1e-9f);
        edge = d * px_per < 1.25f;
    }
    return h;
}

// The recess behind the slats: blurred pictures + palette, dark, with soft light stripes from between the slats.
__device__ float3 vb_wall(const float* __restrict__ U, const float4* __restrict__ S, const float* __restrict__ P,
                          const Tex& TA, const Tex& TB, float3 ro, float3 rd)
{
    const float A = U[13], ux = U[14], uy = U[15], vx = -uy, vy = ux;
    const float rmin = U[16], pitch = U[17], hw = U[18], th = U[19], zc = U[20], zw = U[21];
    const int N = (int)U[22];
    const float rz = rd.z < -1e-5f ? rd.z : -1e-5f;
    const float t = (zw - ro.z) / rz;
    const float3 pw = ro + rd * t;
    const float u = 0.5f + 0.5f * pw.x / A, v = 0.5f - 0.5f * pw.y;
    const float pr = U[32];
    const float3 bl = mix(tex_blur(TA, u, v), tex_blur(TB, u, v), pr);
    const float3 pc = pal(U[28] + 0.5f + 0.35f * v * U[35], P);
    const float3 base = (0.55f * bl + 0.45f * pc) * U[33];
    // light stripes: each nearby slat's mid-line projected onto the wall along the light
    const float3 L = load3(U + 23);
    const float Lr = L.x * vx + L.y * vy, kL = Lr / fmaxf(L.z, 0.05f);
    const float rw = pw.x * vx + pw.y * vy;
    const float depth = zc - zw;
    const int kc = (int)floorf((rw + depth * kL - rmin) / pitch);
    const float pen = U[34] * depth + 0.3f * th + 0.002f;
    float lit = 1.f;
    #pragma unroll
    for (int j = -2; j <= 2; ++j) {
        const int k = kc + j;
        if (k < 0 || k >= N) continue;
        const float4 s4 = S[k];
        const float rc = rmin + (k + 0.5f) * pitch;
        const float ra = rc + hw * s4.x - (zc + hw * s4.y - zw) * kL;
        const float rb = rc - hw * s4.x - (zc - hw * s4.y - zw) * kL;
        const float lo = fminf(ra, rb) - 0.5f * th, hi = fmaxf(ra, rb) + 0.5f * th;
        const float cov = smooth(lo - pen, lo + pen, rw) * (1.f - smooth(hi - pen, hi + pen, rw));
        lit *= 1.f - cov;
    }
    const float inside = smooth(-0.03f, 0.03f, A - fabsf(pw.x)) * smooth(-0.03f, 0.03f, 1.f - fabsf(pw.y));
    lit *= inside;
    const float g = U[27];
    const float shade = mix(0.5f, 0.05f + 1.35f * lit, g);
    return base * shade * mix(0.3f, 1.f, inside);
}

__device__ float3 vb_shade(const VBHit& h, const float* __restrict__ U, const float4* __restrict__ S,
                           const float* __restrict__ P, const Tex& TA, const Tex& TB, const Cam& cam,
                           float3 ro, float3 rd, float pa)
{
    if (h.k < 0) return vb_wall(U, S, P, TA, TB, ro, rd);
    const float A = U[13], ux = U[14], uy = U[15], vx = -uy, vy = ux;
    const float rmin = U[16], pitch = U[17], hw = U[18];
    const float4 s4 = S[h.k];
    const float c = s4.x, s = s4.y;
    float3 n;
    if (h.ax == 0) n = f3(h.sg, 0.f, 0.f);
    else if (h.ax == 1) n = f3(0.f, h.sg, 0.f);
    else {
        const float na = h.ax == 2 ? h.sg : 0.f, nb = h.ax == 3 ? h.sg : 0.f;
        const float nr = na * c - nb * s, nz = na * s + nb * c;
        n = f3(nr * vx, nr * vy, nz);
    }
    const float3 L = load3(U + 23);
    const float3 p = ro + rd * h.t;
    const float sl = p.x * ux + p.y * uy;                 // position along the slat
    const float3 rf = reflect(rd, n);
    if (h.ax == 3) {
        const float rr = rmin + (h.k + 0.5f) * pitch + (h.sg > 0.f ? h.a : -h.a);
        const float x = sl * ux + rr * vx, y = sl * uy + rr * vy;
        const float u = 0.5f + 0.5f * x / A, v = 0.5f - 0.5f * y;
        const float lod = tex_lod(h.t, pa, dot(rd, cam.fwd), fabsf(dot(rd, n)), frame_texel(TA), -0.25f);
        float3 col = h.sg > 0.f ? tex_sample(TA, u, v, lod) : tex_sample(TB, u, v, lod);
        col = light_frame(col, n, L, cam.fwd, 0.35f, U[29]);
        const float w = s4.w;
        // a little occlusion toward the long edges while turning (the neighbours crowd the light)
        const float ed = 1.f - fabsf(h.a) / hw;
        col = col * (1.f - 0.3f * w * (1.f - smooth(0.f, 0.4f, ed)));
        const float sh = (0.6f * env_softbox(rf) + 0.5f * spec_blinn(n, -rd, L, 40.f)) * w * U[30];
        const float3 tint = mix(f3(1.f), pal(U[28] + 0.05f * h.k * U[35], P), 0.3f);
        return vb_screen(col, tint * sh);
    }
    // slat edges (ax 2) and the end caps at the frame border (ax 0/1): palette metal
    const float A2 = fmaxf(A, 1.f);
    const float3 base = pal(U[28] + (0.045f * h.k + 0.12f * sl / A2) * U[35], P);
    const float lam = saturate(dot(n, L));
    const float sp = spec_blinn(n, -rd, L, 28.f);
    const float fr = fresnel_schlick(fabsf(dot(rd, n)), 0.3f);
    const float gain = U[26];
    float3 col = base * (0.1f + 0.25f * lam)
               + gain * (base * (0.45f * lam) + mix(f3(1.f), base, 0.35f) * (0.9f * sp + 0.5f * env_softbox(rf) * fr));
    if (h.ax != 2) col = col * 0.55f;
    return col;
}

SDF_KERNEL
void venetian_blinds(uchar4* __restrict__ out, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b), const float* __restrict__ U,
                     const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int W = a_W, H = a_H;
    if (x >= W || y >= H) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    const float4* S = reinterpret_cast<const float4*>(U + 48);
    const Cam cam = cam_load(U);
    const float pa = pixel_angle(cam, H);
    const float px = x + 0.5f, py = y + 0.5f;
    const float3 rd = cam_dir(cam, px, py, W, H);
    bool edge = false;
    const VBHit h = vb_trace(U, S, cam.eye, rd, pa, true, edge);
    if (!edge && U[31] > 1e-3f) {                       // the frame border once the camera has moved
        const float3 hb = f3(U[13], 1.f, U[36]);
        const float delta = 1.25f * pa * fmaxf(cam.eye.z, 0.1f);
        edge = box_cover_class(cam.eye - f3(0.f, 0.f, U[20]), rd, hb, delta) == 0.5f;
    }
    float3 col;
    if (!edge) {
        col = vb_shade(h, U, S, P, TA, TB, cam, cam.eye, rd, pa);
    } else {
        const float ox[4] = {-0.125f, 0.375f, 0.125f, -0.375f}, oy[4] = {-0.375f, -0.125f, 0.375f, 0.125f};
        col = f3(0.f);
        #pragma unroll 1
        for (int i = 0; i < 4; ++i) {
            const float3 d = cam_dir(cam, px + ox[i], py + oy[i], W, H);
            bool dummy = false;
            const VBHit hs = vb_trace(U, S, cam.eye, d, pa, false, dummy);
            col += vb_shade(hs, U, S, P, TA, TB, cam, cam.eye, d, pa);
        }
        col = col * 0.25f;
    }
    store_rgb(out, y * W + x, col);
}
"""

_LIGHT = np.asarray([-0.35, 0.62, 0.70], np.float64)
_LIGHT = _LIGHT / np.linalg.norm(_LIGHT)


def _ease_back(u: np.ndarray, k: float) -> np.ndarray:
    """Ease-in-out-back on [0, 1] (k = 0: cubic in-out; k = 1: ~10% anticipation and overshoot)."""
    c2 = 1.70158 * 1.525 * k
    t = 2.0 * u
    lo = 0.5 * t * t * ((c2 + 1.0) * t - c2)
    t2 = 2.0 * u - 2.0
    hi = 0.5 * (t2 * t2 * ((c2 + 1.0) * t2 + c2) + 2.0)
    return np.where(u < 0.5, lo, hi)


class VenetianBlinds(Transition):
    name = "venetian_blinds"
    label = "Venetian blinds 3D"
    description = ("The frame becomes slats that turn over in a travelling wave, old picture on the front, new on "
                   "the back; metal edges catch the light and the gaps show a dark recess with soft light stripes.")
    tags = {"family": "3d", "dims": "3d", "toolkit": "frame3d", "technique": ["projection", "analytic"],
            "moods": ["geometric", "elegant"], "style": "lively", "energy": [0.2, 0.9]}
    license_class = "permissive"
    default_duration_s = 3.0
    params = {
        "slats": num(6, 40, 14, "Slats", tier="regional", integer=True, basic=True),
        "angle": num(-90.0, 90.0, 0.0, "Slat angle", step=1.0, unit="°", tier="regional"),
        "wave_stagger": num(0.0, 0.85, 0.55, "Wave stagger", tier="regional"),
        "wave_origin": num(0.0, 1.0, 0.0, "Wave origin (0 top, 0.5 middle, 1 bottom)", tier="regional"),
        "jitter": num(0.0, 1.0, 0.15, "Timing jitter", tier="regional"),
        "thickness": num(0.02, 0.4, 0.12, "Slat thickness", tier="regional"),
        "perspective": num(0.0, 1.0, 0.5, "Perspective", tier="regional"),
        "tilt": num(0.0, 1.0, 0.5, "Camera tilt", tier="regional"),
        "gap_shadow": num(0.0, 1.0, 0.8, "Gap shadow", tier="regional"),
        "edge_light": num(0.0, 2.0, 1.0, "Edge light", tier="regional"),
        "overshoot": num(0.0, 1.0, 0.35, "Overshoot", tier="regional"),
        **palette_params("dusk", period=1.0, drift=0.05),
        "seed": num(0, 2**31 - 1, 7, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC, FRAME3D_OPTIONS).get_function("venetian_blinds")
        self._mips_a = FrameMips(ctx, levels=4)
        self._mips_b = FrameMips(ctx, levels=4)
        self._U = cp.zeros(_NU, dtype=cp.float32)
        self._Uh = np.zeros(_NU, np.float32)
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        seed = int(self.params.get("seed", 7)) & 0x7FFFFFFF
        self._rnd = np.random.default_rng(seed).random(_MAXS)

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "dusk")
        if name not in PALETTES:
            name = "dusk"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def _f(self, key: str, lo: float, hi: float) -> float:
        return min(max(float(self.params.get(key, self.params_default(key))), lo), hi)

    @classmethod
    def params_default(cls, key: str) -> float:
        return float(cls.params[key]["default"])

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        p = float(progress)
        if p <= 1e-4:
            cp.copyto(out, a)
            return
        if p >= 0.9999:
            cp.copyto(out, b)
            return
        w, h = self.ctx.width, self.ctx.height
        self._sync_palette()
        f = self._f
        A = w / h
        N = int(round(f("slats", 6, 40)))
        alpha = math.radians(f("angle", -90.0, 90.0))
        ux, uy = math.cos(alpha), math.sin(alpha)
        span = A * abs(math.sin(alpha)) + abs(math.cos(alpha))     # frame extent across the slats (half)
        pitch = 2.0 * span / N
        hw = 0.5 * pitch
        th = max(f("thickness", 0.02, 0.4), 0.02) * hw
        zc = -th
        R = math.hypot(hw, th)
        zw = zc - R - 0.6 * hw - 0.02

        # ---- per-slat schedule: a travelling wave from wave_origin, seeded jitter, ease-in-out-back half turn
        k = np.arange(N, dtype=np.float64)
        pos = 1.0 - (k + 0.5) / N                                   # 0 at the top (angle 0), 1 at the bottom
        org = f("wave_origin", 0.0, 1.0)
        order = np.abs(pos - org) / max(org, 1.0 - org, 1e-3)
        order = order + f("jitter", 0.0, 1.0) * 0.5 * (self._rnd[:N] - 0.5)
        rng_ = float(np.ptp(order))
        order = (order - order.min()) / rng_ if rng_ > 1e-6 else np.zeros_like(order)
        stagger = f("wave_stagger", 0.0, 0.85)
        dur = 1.0 - stagger
        u = np.clip((p - stagger * order) / dur, 0.0, 1.0)
        theta = math.pi * _ease_back(u, f("overshoot", 0.0, 1.0))
        turning = (u > 0.0) & (u < 1.0)
        gate = turning.copy()
        gate[1:] |= turning[:-1]
        gate[:-1] |= turning[1:]
        sn = np.sin(theta)

        # ---- camera: flat at both ends, a tilt across the slats and a slight dolly while they turn
        fov = 18.0 + 44.0 * f("perspective", 0.0, 1.0)
        D = flat_distance(fov)
        e = smoothstep01(math.sin(math.pi * p))
        T = f("tilt", 0.0, 1.0) * e
        z0 = 0.25
        cam = camera_orbit((0.0, 0.0, -z0), D + z0 + 0.12 * T * D,
                           yaw=-0.30 * T * uy + 0.06 * T, pitch=0.30 * T * ux, roll=0.0, fov_deg=fov)

        dur_s = self.duration_s
        U = self._Uh
        U[:13] = cam
        U[13] = A
        U[14] = ux
        U[15] = uy
        U[16] = -span
        U[17] = pitch
        U[18] = hw
        U[19] = th
        U[20] = zc
        U[21] = zw
        U[22] = N
        U[23:26] = _LIGHT
        U[26] = f("edge_light", 0.0, 2.0)
        U[27] = f("gap_shadow", 0.0, 1.0)
        U[28] = (0.15 + float(self.params.get("drift_speed", 0.05)) * p * dur_s) % 1.0
        U[29] = 0.9
        U[30] = 0.85
        U[31] = e
        U[32] = p
        U[33] = 0.42
        U[34] = 0.18
        U[35] = f("palette_period", 0.05, 10.0)
        U[36] = R
        S = U[_HDR:_HDR + 4 * N].reshape(N, 4)
        S[:, 0] = np.cos(theta)
        S[:, 1] = sn
        S[:, 2] = gate.astype(np.float32)
        S[:, 3] = sn * sn
        self._U.set(U)

        self._mips_a.build(a)
        self._mips_b.build(b)
        launch_2d(self._k, w, h, (out, *self._mips_a.args(a), *self._mips_b.args(b), self._U, self._P))

    def close(self) -> None:
        self._mips_a = None
        self._mips_b = None
        self._U = None
        super().close()
