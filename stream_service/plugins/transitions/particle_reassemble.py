"""Particle reassemble: the old picture crumbles into particles (one per grain x grain block) that are released
in a noisy directional sweep and blown away by wind and curl turbulence, while the new picture's particles fly in
from the opposite side and gather behind a warped boundary.  The backdrop crossfades old -> new only where
particles have left or arrived; optional depth throws the dust towards the viewer with a slight camera orbit.

Source: https://github.com/nanfengw0w/varyloom/blob/main/src/transitions/particle-shift.ts
        https://github.com/Yalantis/StarWars.Android   (crumble idea)
        https://github.com/Tolexia/threejs-image-particles-transition   (idea)
Author: Varyloom contributors (particle-shift.ts); Yalantis (StarWars.Android crumble); Axel C (Tolexia
        threejs-image-particles-transition)
License: MIT   (varyloom README: "Licensed under MIT.")
license_class: permissive
Ported from WGSL by smfx (ideas/changes: varyloom's stateful compute sim (velocity += wind + pseudo-curl, damped)
  is rewritten as a stateless closed form so any progress renders directly: each old particle's motion clock is
  tau = max(exit_clock - release_order, 0) with a linear exit clock, its position is home + wind * tau^2 + a
  sideways flicker + divergence-free curl noise (splat3d sp_curl) sampled where the particle is, plus a z throw for
  depth.  Release order = direction order along the wind + per-particle noise + a warped front; the incoming side
  keeps varyloom's mask state (enter clock smoothstep(0.26, 1) * 1.5, gather duration 0.46 + noise, the warped
  boundary sines) and its incoming flow (mix(dispersed, home, smoothstep(arrival)) + curl * sin(pi * arrival)).
  Particles are splatted in 3D with smfx splat3d (one fused generator kernel writes positions, colours and sizes;
  colours are pre-inverted for the exp tonemap so a packed cloud reproduces the picture), over a backdrop that the
  same camera projects onto the picture plane.  Mid-flight particles drift towards a cosine-palette ember colour
  with drift; the twinkle uses progress * duration_s.  Endpoints are exact copies.)

MIT notice (varyloom): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params
from stream_service.plugins._lib.splat3d import SPLAT3D_OPTIONS, SPLAT3D_SRC, Splat3D, look_at, orbit
from stream_service.plugins._lib.upsample import internal_size

FOV_DEG = 40.0
GRAIN_MIN = 3.0
_NP = 16

_SRC = HELPERS + PALETTE_CUDA + SPLAT3D_SRC + r"""
// direction order across the frame (uv: 0..1, v down; d: direction with y up), 0 upwind .. 1 downwind
__device__ __forceinline__ float pr_order(float u, float v, float dx, float dy) {
    const float span = fmaxf(fabsf(dx) + fabsf(dy), 1e-3f);
    return ((u - 0.5f) * dx + (0.5f - v) * dy) / span + 0.5f;
}
// varyloom's warped boundary (three sines across the front), with a seeded phase
__device__ __forceinline__ float pr_front(float cc, float ph) {
    return __sinf(cc * 8.f + 0.85f + ph) * 0.12f + __sinf(cc * 19.f - 1.7f + 1.9f * ph) * 0.06f
         + __sinf(cc * 43.f + 2.2f + 3.1f * ph) * 0.025f;
}
// inverse of the exp tonemap (1 - e^-x) so a packed cloud reproduces the colour
__device__ __forceinline__ float pr_inv(float c) { return -__logf(1.f - fminf(fmaxf(c * 0.97f, 0.f), 0.985f)); }
// mean of 4 taps inside block (bx, by), linear light
__device__ float3 pr_block(const uchar4* __restrict__ img, int bx, int by, int gp, int W, int H) {
    const int q = gp / 4;
    const int x0 = bx * gp + q, x1 = bx * gp + gp - 1 - q, y0 = by * gp + q, y1 = by * gp + gp - 1 - q;
    const float4 c0 = ld(img, x0, y0, W, H), c1 = ld(img, x1, y0, W, H);
    const float4 c2 = ld(img, x0, y1, W, H), c3 = ld(img, x1, y1, W, H);
    const float k = 1.f / (4.f * 255.f);
    return make_float3(sp_lin((c0.x + c1.x + c2.x + c3.x) * k), sp_lin((c0.y + c1.y + c2.y + c3.y) * k),
                       sp_lin((c0.z + c1.z + c2.z + c3.z) * k));
}
__device__ __forceinline__ float3 pr_pal_lin(float t, const float* __restrict__ P) {
    const float3 c = pal(t, P);
    return make_float3(sp_lin(c.x), sp_lin(c.y), sp_lin(c.z));
}

// P: 0 p, 1 secs, 2 aspect, 3 wind x, 4 wind y, 5 scatter, 6 turbulence, 7 depth, 8 warp, 9 ember, 10 period,
//    11 drift, 12 boost, 13 turbulence frequency, 14 front phase
extern "C" __global__ void pr_gen(const uchar4* __restrict__ a, const uchar4* __restrict__ b,
                                  float4* __restrict__ pos, float* __restrict__ rgb, float* __restrict__ sz,
                                  float2* __restrict__ state, const float* __restrict__ P,
                                  const float* __restrict__ pal12, const int W, const int H, const int gp,
                                  const int nbx, const int nby, const unsigned int seed)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int nb = nbx * nby;
    if (i >= nb) return;
    const float p = P[0], secs = P[1], asp = P[2], wx = P[3], wy = P[4], scatter = P[5], turb = P[6];
    const float depth = P[7], warp = P[8], ember = P[9], period = P[10], drift = P[11], boost = P[12];
    const float tf = P[13], fph = P[14];
    const int bx = i % nbx, by = i / nbx;
    const float u = fminf((bx + 0.5f) * gp, W - 0.5f) / W, v = fminf((by + 0.5f) * gp, H - 0.5f) / H;
    const float hx = (2.f * u - 1.f) * asp, hy = 1.f - 2.f * v;
    const float3 h1 = sp_hash3((unsigned int)i, seed), h2 = sp_hash3((unsigned int)i, seed ^ 0x5bd1e995u);
    const float nz = h1.x, ws = 0.62f + 0.7f * h1.y, flick = h1.z - 0.5f, zr = h2.x, spark = h2.y, trav = h2.z;
    const float sx = -wy, sy = wx;                                     // across the wind
    float ph = secs * (3.f + 6.f * spark) + 19.f * spark;              // twinkle phase, wrapped
    ph -= 6.2831853f * floorf(ph * 0.15915494f);
    const float tw = 0.78f + 0.32f * __sinf(ph);
    const float szb = 0.7f + 0.6f * fmodf(spark * 7.31f + trav, 1.f);

    // ---- incoming (B) mask state: varyloom's enter clock behind a warped boundary, from the upwind side
    const float ex = -wx, ey = -wy, sex = -ey, sey = ex;
    const float ccB = (u - 0.5f) * sex + (0.5f - v) * sey;
    const float oB = fminf(fmaxf(1.f - pr_order(u, v, ex, ey) + warp * pr_front(ccB, fph) + (nz - 0.5f) * 0.08f,
                                 0.025f), 0.975f);
    const float gB = smooth(oB, oB + 0.46f + 0.04f * nz, 1.5f * smooth(0.26f, 1.f, p));

    // ---- outgoing (A): noisy directional sweep, then wind + flicker + curl turbulence (closed form in tau)
    const float ccA = (u - 0.5f) * sx + (0.5f - v) * sy;
    const float oA = fminf(fmaxf(pr_order(u, v, wx, wy) + (nz - 0.5f) * 0.18f + 0.6f * warp * pr_front(1.3f * ccA, fph + 2.3f),
                                 0.025f), 0.975f);
    const float eclk = 2.05f * (p - 0.015f);
    const float relA = smooth(oA, oA + 0.14f, eclk);
    const float tau = fmaxf(eclk - oA, 0.f), t2 = tau * tau;
    float3 A = make_float3(hx, hy, 0.f);
    {
        const float dw = scatter * ws * 1.25f * t2, df = flick * 0.45f * scatter * t2;
        A.x += wx * dw + sx * df;
        A.y += wy * dw + sy * df;
        A.z += depth * (1.2f * zr - 0.2f) * scatter * 0.9f * t2;
        if (turb > 0.f && tau > 0.f) {
            const float3 c = sp_curl(make_float3(A.x * tf, A.y * tf, A.z * tf + 0.25f * secs + 0.4f * tau), seed + 11u, 2);
            const float amp = turb * 1.6f * scatter * t2 / (tau + 0.3f);
            A.x += c.x * amp; A.y += c.y * amp; A.z += c.z * amp * depth;
        }
    }
    const float flyA = smooth(0.f, 0.4f, tau);
    const float wA = relA * (1.f - smooth(0.5f, 0.98f, gB)) * (1.f - smooth(0.82f, 0.98f, p))
                   * (1.f + (tw * (1.f + boost) - 1.f) * flyA);
    float3 ca = pr_block(a, bx, by, gp, W, H);
    {
        const float k = ember * smooth(0.05f, 0.6f, tau) * (0.35f + 0.65f * spark);
        const float L = 0.2126f * ca.x + 0.7152f * ca.y + 0.0722f * ca.z;
        const float3 pc = pr_pal_lin(oA * period + 0.35f * spark + drift, pal12);
        const float g = 0.3f + 1.1f * L;
        ca = make_float3(ca.x + (pc.x * g - ca.x) * k, ca.y + (pc.y * g - ca.y) * k, ca.z + (pc.z * g - ca.z) * k);
    }

    // ---- incoming (B) flight: from home + enterDir * travel (and out of the plane) to home, curl along the way
    float3 B;
    {
        const float span = fabsf(ex) * asp * 2.2f + fabsf(ey) * 2.2f;
        const float td = span * (0.72f + 0.58f * trav) * scatter;
        const float dx = hx + ex * td, dy = hy + ey * td, dz = depth * (1.6f * zr - 0.5f) * 1.2f * scatter;
        const float ct = gB * gB * (3.f - 2.f * gB), env = __sinf(3.14159265f * gB);
        B = make_float3(dx + (hx - dx) * ct, dy + (hy - dy) * ct, dz * (1.f - ct));
        if (env > 0.f) {
            const float3 c = sp_curl(make_float3(hx * tf, hy * tf, 3.7f + 1.2f * gB + 0.25f * secs), seed + 29u, 2);
            const float sc = 0.5f + 0.5f * scatter;
            const float side = (c.x * sex + c.y * sey) * turb * 0.7f + flick * 0.12f;
            const float fwd = -(c.x * ex + c.y * ey) * turb * 0.18f;
            B.x += (sex * side - ex * fwd) * sc * env;
            B.y += (sey * side - ey * fwd) * sc * env;
            B.z += c.z * turb * depth * 0.8f * env;
        }
    }
    const float gg = smooth(0.04f, 0.46f, gB) * (1.f - smooth(0.8f, 1.f, gB));
    const float wB = smooth(0.015f, 0.14f, gB) * (1.f - smooth(0.86f, 1.f, gB)) * 0.9f * (1.f + 0.58f * gg)
                   * (1.f + (tw * (1.f + boost) - 1.f) * (1.f - smooth(0.6f, 0.95f, gB)));
    float3 cb = pr_block(b, bx, by, gp, W, H);
    {
        const float k = ember * (0.35f + 0.65f * spark) * (1.f - smooth(0.45f, 0.9f, gB));
        const float L = 0.2126f * cb.x + 0.7152f * cb.y + 0.0722f * cb.z;
        const float3 pc = pr_pal_lin(oB * period + 0.35f * spark + drift + 0.5f, pal12);
        const float g = 0.3f + 1.1f * L;
        cb = make_float3(cb.x + (pc.x * g - cb.x) * k, cb.y + (pc.y * g - cb.y) * k, cb.z + (pc.z * g - cb.z) * k);
    }

    pos[i] = make_float4(A.x, A.y, A.z, wA);
    pos[nb + i] = make_float4(B.x, B.y, B.z, wB);
    rgb[3 * i] = pr_inv(ca.x); rgb[3 * i + 1] = pr_inv(ca.y); rgb[3 * i + 2] = pr_inv(ca.z);
    rgb[3 * (nb + i)] = pr_inv(cb.x); rgb[3 * (nb + i) + 1] = pr_inv(cb.y); rgb[3 * (nb + i) + 2] = pr_inv(cb.z);
    sz[i] = szb;
    sz[nb + i] = szb * (1.f + 0.2f * gg);
    state[i] = make_float2(1.f - relA, smooth(0.72f, 0.9f, gB));
}

// backdrop: the picture plane seen by the same camera; old where particles have not left, new where they have
// arrived, a dim crossfading ghost in between
extern "C" __global__ void pr_bg(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
                                 const float2* __restrict__ state, const float* __restrict__ C, const int W,
                                 const int H, const int gp, const int nbx, const int nby, const float asp,
                                 const float backdrop, const float vmix)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float f = C[12];
    const float dx = (x + 0.5f - 0.5f * W) / f, dy = (y + 0.5f - 0.5f * H) / f;
    const float rx = C[3] + C[6] * dx - C[9] * dy, ry = C[4] + C[7] * dx - C[10] * dy, rz = C[5] + C[8] * dx - C[11] * dy;
    if (rz > -1e-4f) { out[y * W + x] = make_uchar4(0, 0, 0, 255); return; }
    const float t = -C[2] / rz;
    const float X = C[0] + t * rx, Y = C[1] + t * ry;
    const float u = (X / asp + 1.f) * 0.5f * W, v = (1.f - Y) * 0.5f * H;
    const int bx = min(max((int)floorf(u / gp), 0), nbx - 1), by = min(max((int)floorf(v / gp), 0), nby - 1);
    const float2 st = state[by * nbx + bx];
    const float4 ca = bilin(a, u, v, W, H), cb = bilin(b, u, v, W, H);
    const float kA = st.x * (1.f - st.y), kB = st.y, k0 = (1.f - st.x) * (1.f - st.y) * backdrop;
    const float e = fminf(fminf(u, W - u), fminf(v, H - v));
    const float edge = clamp01(1.f + e / (0.01f * H));
    float4 c;
    c.x = (ca.x * kA + cb.x * kB + (ca.x + (cb.x - ca.x) * vmix) * k0) * edge;
    c.y = (ca.y * kA + cb.y * kB + (ca.y + (cb.y - ca.y) * vmix) * k0) * edge;
    c.z = (ca.z * kA + cb.z * kB + (ca.z + (cb.z - ca.z) * vmix) * k0) * edge;
    c.w = 255.f;
    out[y * W + x] = pack(c);
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _grid(ctx, grain: float) -> tuple[int, int, int, float]:
    """Block size in output px, block counts, and the actual grain in 1080-line px."""
    gp = max(2, int(round(grain * ctx.height / 1080.0)))
    return gp, (ctx.width + gp - 1) // gp, (ctx.height + gp - 1) // gp, gp * 1080.0 / ctx.height


class ParticleReassemble(Transition):
    name = "particle_reassemble"
    label = "Particle reassemble"
    description = ("The old picture crumbles into glowing particles that a gust of wind and curl turbulence blow "
                   "away, while the new picture's particles stream in from the other side and lock into place.")
    tags = {"family": "fragment", "dims": "3d", "toolkit": "splat3d", "technique": ["particles", "points"],
            "moods": ["euphoric", "cosmic"], "style": "wild", "energy": [0.5, 1.0]}
    license_class = "permissive"
    default_duration_s = 4.0
    params = {
        "grain_size": num(GRAIN_MIN, 12.0, 4.0, "Grain (particle block, 1080p px)", step=0.5, unit="px",
                          tier="regional", basic=True),
        "turbulence": num(0.0, 2.0, 1.0, "Turbulence", tier="regional", basic=True),
        "wind_angle": num(-180.0, 180.0, 15.0, "Wind direction", unit="°", step=1.0, tier="regional"),
        "scatter_distance": num(0.3, 2.0, 1.0, "Scatter distance", tier="regional"),
        "depth": num(0.0, 1.0, 0.5, "Depth (3D throw + camera orbit)", tier="regional"),
        "glow": num(0.0, 2.0, 0.8, "Glow", tier="regional"),
        "boundary_warp": num(0.0, 2.0, 1.0, "Boundary warp", tier="regional"),
        "backdrop": num(0.0, 0.8, 0.2, "Backdrop ghost (between old and new)", tier="regional"),
        "ember": num(0.0, 1.0, 0.45, "Ember colour (palette tint in flight)", tier="regional"),
        **palette_params("ember", period=1.2, drift=0.06),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC, SPLAT3D_OPTIONS)
        self._k_gen = mod.get_function("pr_gen")
        self._k_bg = mod.get_function("pr_bg")
        _, nbx, nby, _ = _grid(ctx, GRAIN_MIN)                     # sized for the finest grain (live-safe)
        nbmax = nbx * nby
        self._nbmax = nbmax
        self._pos = cp.zeros((2 * nbmax, 4), cp.float32)
        self._rgb = cp.zeros((2 * nbmax, 3), cp.float32)
        self._sz = cp.ones(2 * nbmax, cp.float32)
        self._state = cp.zeros((nbmax, 2), cp.float32)
        w, h = internal_size(ctx, float(self.params.get("render_scale", 0.5)))
        self.sp = Splat3D(ctx, w, h, max_points=2 * nbmax, max_lines=16)
        self._P = cp.zeros(_NP, cp.float32)
        self._Ph = np.zeros(_NP, np.float32)
        self._C = cp.zeros(16, cp.float32)
        self._Ch = np.zeros(16, np.float32)
        self._pal = cp.zeros(12, cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        seed = int(np.random.default_rng().integers(0, 2**31 - 1)) if s < 0 else s
        rng = np.random.default_rng(seed)
        self._seed = np.uint32(int(rng.integers(1, 2**31 - 1)))
        self._fph = float(rng.uniform(0.0, 2 * math.pi))
        self._drift0 = float(rng.uniform(0.0, 1.0))
        self._orbit_dir = 1.0 if rng.uniform() < 0.5 else -1.0

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ember")
        if name not in PALETTES:
            name = "ember"
        if name != self._pal_name:
            self._pal.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 1e-4:
            cp.copyto(out, a)
            return
        if progress >= 0.9999:
            cp.copyto(out, b)
            return
        self._sync_palette()
        prm = self.params
        ctx = self.ctx
        W, H = ctx.width, ctx.height
        asp = W / H
        p = float(progress)
        secs = p * self.duration_s
        env = math.sin(math.pi * p)
        gp, nbx, nby, g_ref = _grid(ctx, max(GRAIN_MIN, float(prm.get("grain_size", 4.0))))
        nb = min(nbx * nby, self._nbmax)
        depth = float(prm.get("depth", 0.5))
        glow = float(prm.get("glow", 0.8))
        ang = math.radians(float(prm.get("wind_angle", 15.0)))

        # camera: flat at both ends; mid-transition a slight orbit and dolly for parallax (scaled by depth)
        tan_half = math.tan(math.radians(FOV_DEG) * 0.5)
        D = 1.0 / tan_half
        yaw = self._orbit_dir * depth * 0.16 * env
        pitch = depth * 0.06 * env * math.sin(ang + 0.7)
        R = D * (1.0 - 0.08 * depth * env)
        cam = look_at(orbit((0.0, 0.0, 0.0), R, math.pi + yaw, pitch), (0.0, 0.0, 0.0), fov_deg=FOV_DEG)

        P = self._Ph
        P[:15] = (p, secs, asp, math.cos(ang), math.sin(ang), float(prm.get("scatter_distance", 1.0)),
                  float(prm.get("turbulence", 1.0)), depth, float(prm.get("boundary_warp", 1.0)),
                  float(prm.get("ember", 0.45)), float(prm.get("palette_period", 1.2)),
                  (self._drift0 + float(prm.get("drift_speed", 0.06)) * secs) % 1.0, 0.5 * glow, 1.4, self._fph)
        self._P.set(P)
        C = self._Ch
        C[0:3], C[3:6], C[6:9], C[9:12] = cam.eye, cam.fwd, cam.right, cam.up
        C[12] = 0.5 * H / cam.tan_half
        self._C.set(C)

        self._k_gen(((nb + 255) // 256,), (256,),
                    (a, b, self._pos, self._rgb, self._sz, self._state, self._P, self._pal, np.int32(W), np.int32(H),
                     np.int32(gp), np.int32(nbx), np.int32(nby), self._seed))
        launch_2d(self._k_bg, W, H,
                  (a, b, out, self._state, self._C, np.int32(W), np.int32(H), np.int32(gp), np.int32(nbx),
                   np.int32(nby), np.float32(asp), np.float32(float(prm.get("backdrop", 0.2))),
                   np.float32(_smooth(0.3, 0.75, p))))

        sp = self.sp
        size_ref = 0.8 * g_ref
        rmax_ref = 4.0 / sp.s                                          # stay on the fast binned path
        rho = min(size_ref, rmax_ref)
        intensity = min(g_ref * g_ref, (1.0667 * rho) ** 2)
        sp.clear()
        sp.points(self._pos, cam=cam, n=2 * nb, intensity=intensity, size_px=size_ref, size_range=(0.6, rmax_ref),
                  rgb=self._rgb, sizes=self._sz, weight_in_w=True, near_fade=(0.15, 0.7))
        sp.resolve(out, exposure=1.0, tonemap="exp", saturation=1.0 + 0.1 * env, glow=0.7 * glow * env,
                   glow_radius_px=14.0, glow_wide=0.6, glow_threshold=0.1, background=out, blend="screen")

    def close(self) -> None:
        if getattr(self, "sp", None) is not None:
            self.sp.close()
        self.sp = None
        self._pos = self._rgb = self._sz = self._state = None
