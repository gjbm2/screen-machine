"""Jelly mesh: MilkDrop-style per-vertex motion (zoom, radial zoom weighting, rotation, squash, drift and an animated
sine warp) evaluated on a coarse control grid and applied to the frame once, with no feedback.

Source: Original (inspired by https://sourceforge.net/projects/milkdrop2/ and https://geisswerks.com/milkdrop/)
Author: smfx (original; motion model after Ryan Geiss's MilkDrop2 per-vertex equations, BSD-3-Clause)
License: MIT (original; if any MilkDrop2 formula is copied verbatim, add its BSD-3-Clause notice)
license_class: permissive

Written from scratch; no MilkDrop code or formula copied verbatim (the idea only: a per-vertex UV mapping on a coarse
mesh, interpolated per pixel).  Pass 1 evaluates the mapping on a (grid_res+1) x (rows+1) vertex lattice (48 x 27
cells at 16:9, ~1.3k vertices): radially weighted zoom exp(zoom * zoomexp^(2r-1)) about a wandering centre, squash
and stretch (one axis grows while the other shrinks), a warp made of four seeded plane waves (two transverse, two
longitudinal) whose directions slowly turn, then rotation and translation.  Each vertex stores the displacement and
the trace and determinant of (Jacobian - I), so the full-res pass can evaluate the local area change of the
amount-scaled field exactly.  Pass 2 interpolates the lattice bilinearly (coarse grids give the faceted,
lattice-coherent MilkDrop bends), takes one mirrored bilinear tap with a little overscan that grows with the
displacement, and adds an optional palette-tinted "jelly sheen" where the mesh is stretched (screen blend, so
bright stills do not clip) and a soft shade where it is squeezed.

Realtime: amount scales the whole per-vertex displacement field through m = smoothstep(amount) (copy below 1e-4,
so exact identity at 0); pulse kicks the zoom and the warp amplitude (decaying with the envelope) and adds to the
warp rate, all scaled by m; warp_speed is a rate.  Every phase is host-integrated (rate x dt) and wrapped.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

_TAU = 2.0 * math.pi
_MAX_COLS = 96

# Grid parameter block (G[]):
#  0 A (aspect W/H)   1 cx  2 cy (centre, height units)   3 zl (log zoom)   4 zexp   5 rnorm (1 / half-diagonal)
#  6 log squash a (sx = e^a, sy = e^-0.8a)   7 Aw (warp amplitude, height units)   8 kf (1 / warp_scale)
#  9 cos rot  10 sin rot  11 tx  12 ty
#  13..16 wave phases   17..20 wave frequencies   21..24 wave direction cos   25..28 wave direction sin
#  29..32 push mix (1 = transverse, 0 = longitudinal) per wave
_NG = 36
# Warp parameter block (Q[]): 0..11 palette, 12 s (displacement scale), 13 overscan factor, 14 sheen, 15 drift,
#  16 period, 17 A
_NQ = 20

_SRC = HELPERS + PALETTE_CUDA + r"""
__device__ float2 mw_map(float X, float Y, const float* __restrict__ G) {
    const float cx = G[1], cy = G[2];
    float qx = X - cx, qy = Y - cy;
    // radially weighted zoom: weight zexp^(2r-1) (centre 1/zexp, corners zexp)
    const float r = sqrtf(X * X + Y * Y) * G[5];
    const float w = exp2f((2.f * r - 1.f) * __log2f(fmaxf(G[4], 1e-3f)));
    const float iz = expf(-G[3] * w);
    qx *= iz; qy *= iz;
    // squash and stretch (rubber: one axis in, the other out)
    qx *= expf(-G[6]); qy *= expf(0.8f * G[6]);
    // warp: four plane waves on the vertex position, pushing across (transverse) or along (longitudinal) themselves
    const float a = G[7], kf = G[8];
    #pragma unroll
    for (int n = 0; n < 4; ++n) {
        const float c = G[21 + n], s = G[25 + n], t = G[29 + n];
        const float v = a * __sinf(G[13 + n] + kf * G[17 + n] * (X * c + Y * s));
        qx += v * (t * -s + (1.f - t) * c);
        qy += v * (t * c + (1.f - t) * s);
    }
    // rotation about the centre, then translation
    const float rx = qx * G[9] - qy * G[10], ry = qx * G[10] + qy * G[9];
    return make_float2(rx + cx - G[11], ry + cy - G[12]);
}

// Pass 1: per-vertex displacement and (trace, det) of (J - I).
extern "C" __global__
void mw_grid(float4* __restrict__ g, const int nc, const int nr, const float* __restrict__ G)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x, j = blockIdx.y * blockDim.y + threadIdx.y;
    if (i > nc || j > nr) return;
    const float A = G[0];
    const float X = ((float)i / nc - 0.5f) * A, Y = (float)j / nr - 0.5f;
    const float e = 2e-3f, ie = 1.f / (2.f * e);
    const float2 f0 = mw_map(X, Y, G);
    const float2 fxp = mw_map(X + e, Y, G), fxm = mw_map(X - e, Y, G);
    const float2 fyp = mw_map(X, Y + e, G), fym = mw_map(X, Y - e, G);
    const float a00 = (fxp.x - fxm.x) * ie - 1.f, a10 = (fxp.y - fxm.y) * ie;
    const float a01 = (fyp.x - fym.x) * ie,       a11 = (fyp.y - fym.y) * ie - 1.f;
    g[j * (nc + 1) + i] = make_float4(f0.x - X, f0.y - Y, a00 + a11, a00 * a11 - a01 * a10);
}

__device__ __forceinline__ float mw_fold(float v, float n) {          // mirror at the frame edges
    v = fabsf(v);
    v = fmodf(v, 2.f * n);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}

// Pass 2 (full res): bilinear lattice interpolation, one mirrored tap, jelly sheen.
extern "C" __global__
void mw_warp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float4* __restrict__ g, const int nc, const int nr, const float* __restrict__ Q)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float fW = (float)W, fH = (float)H;
    const float gx = px / fW * nc, gy = py / fH * nr;
    const int i0 = min(max((int)floorf(gx), 0), nc - 1), j0 = min(max((int)floorf(gy), 0), nr - 1);
    const float tx = gx - i0, ty = gy - j0;
    const int s1 = nc + 1;
    const float4 a = g[j0 * s1 + i0], b = g[j0 * s1 + i0 + 1], c = g[(j0 + 1) * s1 + i0], d = g[(j0 + 1) * s1 + i0 + 1];
    const float u = 1.f - tx, v = 1.f - ty;
    const float dx = v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x);
    const float dy = v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y);
    const float tr = v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z);
    const float dt = v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w);
    const float s = Q[12], osk = Q[13], A = Q[17];
    const float X = px / fH - 0.5f * A, Y = py / fH - 0.5f;
    const float qx = (X + s * dx) * osk, qy = (Y + s * dy) * osk;
    float4 col = bilin(src, mw_fold((qx + 0.5f * A) * fH, fW), mw_fold((qy + 0.5f) * fH, fH), W, H);
    const float sheen = Q[14];
    if (sheen > 1e-4f) {
        const float det = 1.f + s * tr + s * s * dt;                 // source area per output area
        const float st = clamp01((1.f - det) * 1.8f);                 // stretched membrane: glows
        const float sq = clamp01((det - 1.f) * 1.4f);                 // squeezed: soft shade
        const float3 pc = pal(Q[15] + Q[16] * (0.45f * X / fmaxf(A, 1e-3f) + 0.3f * Y + 1.5f * (det - 1.f)), Q);
        const float h = sheen * st * (0.6f + 0.4f * st);
        const float k = 1.f - 0.35f * sheen * sq;
        col.x = 255.f - (255.f - col.x * k) * (1.f - h * pc.x);
        col.y = 255.f - (255.f - col.y * k) * (1.f - h * pc.y);
        col.z = 255.f - (255.f - col.z * k) * (1.f - h * pc.z);
    }
    out[y * W + x] = pack(col);
}
"""


def _clamp01(v: float) -> float:
    return min(max(float(v), 0.0), 1.0)


def _smoothstep(v: float) -> float:
    t = _clamp01(v)
    return t * t * (3.0 - 2.0 * t)


class MeshWobble(Effect):
    name = "mesh_wobble"
    stage = "warp"
    label = "Jelly mesh"
    description = ("MilkDrop-style per-vertex motion on a coarse mesh, applied once: radial zoom, rotation, squash "
                   "and an animated sine warp give large, rubbery, lattice-coherent jelly bends.")
    params = {
        **standard_effect_params(),
        "warp": num(0.0, 2.0, 1.0, "Warp", tier="regional", basic=True),
        "warp_scale": num(0.3, 3.0, 1.0, "Warp scale (bend size)", step=0.05, tier="regional"),
        "zoom_bias": num(-0.4, 0.4, 0.07, "Zoom", step=0.005, tier="regional"),
        "rot_bias": num(-0.5, 0.5, 0.0, "Rotation", unit="rad", step=0.005, tier="regional"),
        "zoomexp": num(0.3, 3.0, 1.6, "Zoom radial weighting", step=0.05, tier="regional"),
        "sway": num(0.0, 1.0, 0.5, "Sway (zoom / turn / drift wobble)", tier="regional"),
        "squash": num(0.0, 1.0, 0.4, "Squash & stretch", tier="regional"),
        "sheen": num(0.0, 1.0, 0.3, "Jelly sheen", tier="regional"),
        "kick": num(0.0, 2.0, 1.0, "Beat kick", step=0.05, tier="regional"),
        "warp_speed": num(0.0, 3.0, 1.0, "Warp speed", unit="×", tier="realtime", bind="rate"),
        **palette_params("candy", 1.0, 0.04),
        "grid_res": num(6, _MAX_COLS, 48, "Mesh columns", tier="structural", integer=True),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kg = mod.get_function("mw_grid")
        self._kw = mod.get_function("mw_warp")
        W, H = ctx.width, ctx.height
        self.A = W / H
        rows_max = max(3, int(round(_MAX_COLS * H / W))) + 2
        self.grid = cp.zeros(((rows_max + 1) * (_MAX_COLS + 1), 4), cp.float32)
        self._rows_max = rows_max
        self._G = cp.zeros(_NG, cp.float32)
        self._Gh = np.zeros(_NG, np.float32)
        self._Q = cp.zeros(_NQ, cp.float32)
        self._Qh = np.zeros(_NQ, np.float32)
        seed = 1
        if isinstance(params, dict):
            try:
                seed = int(params.get("seed", 1))
            except (TypeError, ValueError):
                seed = 1
        rng = np.random.default_rng(seed)
        # four plane waves: base direction, frequency (rad per height unit at warp_scale 1), rate, push mix
        base = rng.uniform(0.0, _TAU)
        self.wdir = np.array([base + k * (math.pi / 4.0) + rng.uniform(-0.35, 0.35) for k in range(4)])
        self.wfreq = np.array([8.4, 10.1, 9.2, 11.6]) * rng.uniform(0.9, 1.1, 4)
        self.wrate = np.array([0.83, 1.07, 1.31, 1.69]) * rng.uniform(0.9, 1.1, 4)
        self.wpush = np.array([1.0, 0.0, 1.0, 0.25])
        self.wph = rng.uniform(0.0, _TAU, 4)
        # slow motion phases: zoom, rotation, squash, tx, ty, cx, cy, wave-direction turn, frequency breathing
        self.sph = rng.uniform(0.0, _TAU, 9)
        self.srate = np.array([0.37, 0.29, 0.61, 0.23, 0.31, 0.17, 0.21, 0.05, 0.13])
        self.drift = 0.0
        self._pal = np.asarray(palette_coeffs(str(self.easer.values().get("palette", "candy"))), np.float64)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        amount = _clamp01(p["amount"])
        m = _smoothstep(amount)
        pulse = _clamp01(p["pulse"])
        kick = max(0.0, float(p["kick"]))
        dt = min(max(0.0, float(dt)), 0.5)
        spd = max(0.0, float(p["warp_speed"]))
        # host-integrated phases (wrapped): the warp surges on the beat, never jumps
        wr = spd * (1.0 + 1.5 * kick * pulse * m)
        self.wph = np.fmod(self.wph + self.wrate * wr * dt, _TAU)
        self.sph = np.fmod(self.sph + self.srate * spd * dt, _TAU)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dt, 1000.0)
        if dt > 0.0:
            a_pal = 1.0 - math.exp(-dt / 0.9)
            self._pal += (np.asarray(palette_coeffs(str(p["palette"])), np.float64) - self._pal) * a_pal
        if amount <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        A = self.A
        sway = _clamp01(p["sway"])
        warp = max(0.0, float(p["warp"]))
        sph = self.sph
        kp = kick * pulse
        zl = float(p["zoom_bias"]) + sway * 0.10 * math.sin(sph[0]) + 0.16 * kp
        rot = float(p["rot_bias"]) + sway * 0.085 * math.sin(sph[1])
        sq = _clamp01(p["squash"]) * 0.09 * math.sin(sph[2])
        tx = sway * 0.03 * math.sin(sph[3])
        ty = sway * 0.022 * math.sin(sph[4])
        cx = sway * 0.16 * math.sin(sph[5])
        cy = sway * 0.09 * math.sin(sph[6])
        aw = warp * 0.024 * (1.0 + 0.8 * kp)
        turn = 0.6 * math.sin(sph[7])
        fbreath = 1.0 + 0.08 * math.sin(sph[8])

        G = self._Gh
        G[0] = A
        G[1], G[2] = cx, cy
        G[3] = zl
        G[4] = max(0.05, float(p["zoomexp"]))
        G[5] = 1.0 / (0.5 * math.sqrt(A * A + 1.0))
        G[6] = sq
        G[7] = aw
        G[8] = 1.0 / max(0.05, float(p["warp_scale"]))
        G[9], G[10] = math.cos(rot), math.sin(rot)
        G[11], G[12] = tx, ty
        G[13:17] = self.wph
        G[17:21] = self.wfreq * fbreath
        ang = self.wdir + turn * np.array([1.0, -0.7, 0.5, -1.2])
        G[21:25] = np.cos(ang)
        G[25:29] = np.sin(ang)
        G[29:33] = self.wpush
        self._G.set(G)

        nc = int(round(min(max(float(p["grid_res"]), 6.0), float(_MAX_COLS))))
        nr = min(max(3, int(round(nc * H / W))), self._rows_max)
        launch_2d(self._kg, nc + 1, nr + 1, (self.grid, np.int32(nc), np.int32(nr), self._G))

        # overscan grows with the displacement (mirror fold handles the rest)
        est = 2.0 * aw + 0.6 * abs(tx) + 0.6 * abs(ty) + 0.55 * abs(rot) + 0.5 * max(0.0, -zl) * G[4] + 0.3 * abs(sq)
        os_ = m * min(0.08, 0.5 * est)
        Q = self._Qh
        Q[0:12] = self._pal
        Q[12] = m
        Q[13] = 1.0 / (1.0 + os_)
        Q[14] = _clamp01(p["sheen"]) * m * (1.0 + 0.5 * kp * m)
        Q[15] = self.drift
        Q[16] = float(p["palette_period"])
        Q[17] = A
        self._Q.set(Q)
        launch_2d(self._kw, W, H,
                  (src, out, np.int32(W), np.int32(H), self.grid, np.int32(nc), np.int32(nr), self._Q))
