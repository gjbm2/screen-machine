"""Cube wall: the frame cut into a wall of N x M cubes whose front faces carry its tiles; waves of cubes flip round.

Source: Original (inspired by https://github.com/gl-transitions/gl-transitions/blob/master/transitions/cube.glsl
        and https://raw.githubusercontent.com/Vidvox/ISF-Files/master/ISF/cube.fs; projection idea only, no code)
Author: smfx (original; gl-transitions cube.glsl by gre, MIT, used as a projection reference only)
License: MIT (original)
license_class: permissive

A Rubik's-style kinetic video wall, distinct from frame_cube's single cube.  Every cell of an N x M grid is a box
whose front face shows the part of the picture under it; its side faces carry the NEIGHBOURING tiles (right face =
the tile to the right, top = the tile above, ...), so a quarter turn slides the neighbour's tile into place, and
the back face carries the cell's own tile graded through the palette.  Rotation waves sweep across the wall in a
row, diagonal or radial pattern (continuous blend); each wave turns every cube by the flip angle (closed-form
damped-spring step up, hold, spring back), so no per-cube state is kept and every cube returns to front-facing.
Waves launch on pulse rising edges and on an idle auto-launch counter; a slow travelling ripple keeps the wall
alive between waves.  Rotating cubes lift forward so their corners clear the neighbours.

amount: m = smoothstep(amount) ramps the gaps, the camera (flat identity pose -> slight orbit and dolly), the
rotation amplitude, side shading, palette tint and edge glints from a flat full frame; amount 0 is an exact copy.
cells eases by cross-blending the neighbouring integer grids.  pulse: launches a wave (rising edge) and brightens
the edge glints and gap glow (scaled by m; it never moves geometry directly).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips, camera_orbit, flat_distance, smoothstep01
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.sdf3d import SDF3D

FOV = 40.0
CMAX = 25           # max columns / rows of one grid (cells 24 + 1 for the integer cross-blend)
CF = 12             # floats per cube: rotation (9, row-major) + centre (3)
MAXW = 12           # live waves
WF = 5              # floats per wave: tau, axis (0 = y, 1 = x), sign, reversed, amp (rad)
NP = 128
ZETA = 0.45
OMEGA = 2.0 * math.pi * 1.5
SETTLE = 9.0 / (ZETA * OMEGA)      # spring step response is exactly 1 after this (residual e^-9)

# P layout (floats)
#  0..12 camera | 13 m | 14 A | 15 side shading k | 16 tint | 17 edge | 18 drift | 19 palette period | 20 backdrop
#  21 gap glow | 22 lod bias | 23 w1 (grid-1 weight) | 24..26 key light | 27 AA weight | 28..39 palette
#  40..47 grid 0 (N, M, cw, ch, hx, hy, dz, -) | 48..55 grid 1 | 56..65 waves header | 66.. waves (MAXW x WF)
_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define P_M 13
#define P_A 14
#define P_SIDE 15
#define P_TINT 16
#define P_EDGE 17
#define P_DRIFT 18
#define P_PERIOD 19
#define P_BG 20
#define P_GLOW 21
#define P_BIAS 22
#define P_W1 23
#define P_KEY 24
#define P_AA 27
#define P_PAL 28
#define P_G0 40
#define P_G1 48
#define P_WV 56
#define P_WAVES 66
#define CG_MAXW 12
#define CG_WF 5
#define CG_CMAX 25
#define CG_CF 12

// Damped-spring step response 0 -> 1 (with overshoot); exactly 1 once settled.
__device__ __forceinline__ float cg_spring(float t, float om, float ze) {
    if (t <= 0.f) return 0.f;
    if (t * ze * om > 9.f) return 1.f;
    const float q = sqrtf(1.f - ze * ze);
    float s, c; sincosf(om * q * t, &s, &c);
    return 1.f - __expf(-ze * om * t) * (c + (ze / q) * s);
}
// Wave delay 0..1 of a cell centre (ux, uy in 0..1): row (left->right) .. diagonal .. radial (centre out).
__device__ __forceinline__ float cg_delay(float ux, float uy, float A, float pat, float rev) {
    const float dr = ux, dd = 0.5f * (ux + uy);
    const float ex = (ux - 0.5f) * A, ey = uy - 0.5f;
    const float drad = sqrtf(ex * ex + ey * ey) / (0.5f * sqrtf(A * A + 1.f));
    const float w0 = saturate(1.f - pat), w2 = saturate(pat - 1.f), w1 = 1.f - w0 - w2;
    const float d = w0 * dr + w1 * dd + w2 * drad;
    return rev > 0.5f ? 1.f - d : d;
}

// Per-cube pose for both grids: rotation from the live waves + idle ripple, centre with the forward lift.
extern "C" __global__
void cube_grid_prep(float* __restrict__ C, const float* __restrict__ P)
{
    const int k = blockIdx.x * blockDim.x + threadIdx.x, g = blockIdx.y * blockDim.y + threadIdx.y;
    if (g > 1 || k >= CG_CMAX * CG_CMAX) return;
    const float* G = P + (g ? P_G1 : P_G0);
    const int N = (int)G[0], M = (int)G[1];
    const int i = k % CG_CMAX, j = k / CG_CMAX;
    if (i >= N || j >= M) return;
    const float A = P[P_A], cw = G[2], ch = G[3], dz = G[6];
    const float ux = (i + 0.5f) / N, uy = (j + 0.5f) / M;
    const float* V = P + P_WV;
    const int nw = min((int)V[0], CG_MAXW);
    const float sweep = V[1], hold = V[2], pat = V[3], om = V[6], ze = V[7];
    const float jit = (hash1((unsigned)i, (unsigned)j, 0x9e3779b9u) - 0.5f) * 0.08f;
    float ty = 0.f, tx = 0.f;
    for (int w = 0; w < nw; ++w) {
        const float* Wv = P + P_WAVES + w * CG_WF;
        const float tau = Wv[0] - (cg_delay(ux, uy, A, pat, Wv[3]) + jit) * sweep;
        const float e = cg_spring(tau, om, ze) - cg_spring(tau - hold, om, ze);
        const float a = Wv[2] * Wv[4] * e;
        if (Wv[1] > 0.5f) tx += a; else ty += a;
    }
    const float r = V[4] * sinf(V[5] - 6.2831853f * 0.75f * cg_delay(ux, uy, A, pat, 0.f));
    ty += r * (1.f - V[9]);
    tx += r * V[9];
    float sy, cy, sx, cx;
    sincosf(ty, &sy, &cy);
    sincosf(tx, &sx, &cx);
    // R = Ry(ty) Rx(tx), row-major
    float* o = C + (g * CG_CMAX * CG_CMAX + k) * CG_CF;
    o[0] = cy;  o[1] = sy * sx; o[2] = sy * cx;
    o[3] = 0.f; o[4] = cx;      o[5] = -sx;
    o[6] = -sy; o[7] = cy * sx; o[8] = cy * cx;
    const float lift = V[8] * dz * fmaxf(fabsf(2.f * sy * cy), fabsf(2.f * sx * cx));
    o[9] = -A + (i + 0.5f) * cw;
    o[10] = 1.f - (j + 0.5f) * ch;
    o[11] = -dz + lift;
}

struct CGHit { float t; int k; };

// Closest cube along the ray (3x3 cells around where it crosses the wall's mid plane); sil = 1 when a cube
// silhouette passes within ~1.5 px of the ray (only those pixels pay for supersampling).
__device__ __forceinline__ CGHit cg_trace(const float* __restrict__ Cg, const float* __restrict__ G, float A,
                                          float3 ro, float3 rd, float pa, int& sil) {
    CGHit best; best.t = 1e30f; best.k = -1;
    if (rd.z > -1e-4f) return best;
    const int N = (int)G[0], M = (int)G[1];
    const float cw = G[2], ch = G[3];
    const float3 hb = f3(G[4], G[5], G[6]);
    const float tp = (-G[6] - ro.z) / rd.z;
    const float qx = ro.x + rd.x * tp, qy = ro.y + rd.y * tp;
    const int i0 = (int)floorf(fminf(fmaxf((qx + A) / cw, -4.f), 60.f));
    const int j0 = (int)floorf(fminf(fmaxf((1.f - qy) / ch, -4.f), 60.f));
    #pragma unroll 1
    for (int dj = -1; dj <= 1; ++dj) {
        const int j = j0 + dj;
        if (j < 0 || j >= M) continue;
        #pragma unroll 1
        for (int di = -1; di <= 1; ++di) {
            const int i = i0 + di;
            if (i < 0 || i >= N) continue;
            const int k = j * CG_CMAX + i;
            const float* q = Cg + k * CG_CF;
            const Mat3 R = mat3_load(q);
            const float3 oro = mul_t(R, ro - load3(q + 9)), ord = mul_t(R, rd);
            const float cls = box_cover_class(oro, ord, hb, 1.5f * pa * length(oro));
            if (cls == 0.f) continue;
            if (cls == 0.5f) sil = 1;
            const float2 tt = iBox(oro, ord, hb);
            if (tt.x <= tt.y && tt.x > 0.f && tt.x < best.t) { best.t = tt.x; best.k = k; }
        }
    }
    return best;
}

__device__ __forceinline__ float3 cg_background(const Tex& T, const float* __restrict__ P, float u, float v) {
    const float3 b = bg_blur(T, u, v, 1.08f, P[P_BG], 0.85f);
    return b + pal(P[P_DRIFT] + 0.25f * u + 0.35f * v, P + P_PAL) * P[P_GLOW];
}

// Shade one ray against one grid.  edge = 1 if the pixel sits on a silhouette or a face border.
__device__ float3 cg_shade(const Tex& T, const float* __restrict__ P, const float* __restrict__ Cg,
                           const float* __restrict__ G, const Cam& cam, float3 rd, float u, float v, float pa, int h,
                           int& edge) {
    const float A = P[P_A];
    int sil = 0;
    const CGHit gh = cg_trace(Cg, G, A, cam.eye, rd, pa, sil);
    edge = sil;
    if (gh.k < 0) return cg_background(T, P, u, v);
    const int N = (int)G[0], M = (int)G[1];
    const int bi = gh.k % CG_CMAX, bj = gh.k / CG_CMAX;
    const float cw = G[2], ch = G[3];
    const float3 hb = f3(G[4], G[5], G[6]);
    const float* q = Cg + gh.k * CG_CF;
    const Mat3 R = mat3_load(q);
    const float3 oro = mul_t(R, cam.eye - load3(q + 9)), ord = mul_t(R, rd);
    const FHit hit = ihit_box(oro, ord, hb);
    if (!fhit_ok(hit)) return cg_background(T, P, u, v);
    const int ar = box_around(hit.face);
    const float drift = P[P_DRIFT];
    float2 uv;
    int ti = bi, tj = bj;
    if (ar == 0) {
        // front: the part of the picture under the face (exact identity for the flat pose)
        const float3 p = oro + ord * hit.t;
        uv = f2((bi + 0.5f + p.x / cw) / N, (bj + 0.5f - p.y / ch) / M);
    } else {
        // sides carry the neighbouring tiles (right face = tile to the right, ...); back = own tile
        if (ar == 1) ti = bi + 1; else if (ar == 3) ti = bi - 1; else if (ar == 4) tj = bj - 1; else if (ar == 5) tj = bj + 1;
        ti = (ti + N) % N; tj = (tj + M) % M;
        const float2 fs = box_face_size(hb, hit.face);
        const float2 f = uv_cover(hit.uv, fs.x / fs.y, cw / ch);
        uv = f2((ti + f.x) / N, (tj + f.y) / M);
    }
    const float3 n = mul(R, hit.n);
    const float t = hit.t, cosa = dot(rd, cam.fwd), ndv = fabsf(dot(rd, n));
    const float lod = tex_lod(t, pa, cosa, ndv, frame_texel(T), P[P_BIAS]);
    float3 col = tex_sample(T, uv.x, uv.y, lod);
    if (ar != 0) {
        // palette duotone of the neighbour tile (stronger on the back face); front faces stay the picture
        const float l = luma3(col);
        const float3 gc = pal(0.55f * l * P[P_PERIOD] + drift + 0.045f * (float)(ti + 2 * tj), P + P_PAL);
        const float3 duo = gc * (0.18f + 1.05f * l);
        const float tk = ar == 2 ? fminf(1.f, 2.5f * P[P_TINT]) : P[P_TINT];
        col = mix(col, duo, tk);
    }
    const float k = P[P_SIDE];
    col = light_frame(col, n, load3(P + P_KEY), cam.fwd, 0.22f, k);
    col += f3(env_softbox(reflect(rd, n))) * (fresnel_schlick(ndv, 0.04f) * 0.45f * k);
    // glassy edge glint along every face border (AA by construction, palette-tinted, flashes on the beat)
    const float e = uv_edge_px(hit.uv, box_face_size(hb, hit.face), t * pa * fmaxf(cosa, 0.05f));
    if (e < 1.25f) edge = 1;
    const float bev = (1.f - smoothstep(0.f, fmaxf(1.f, 0.0011f * h), e)) * P[P_EDGE];
    const float3 bc = f3(0.45f) + pal(drift + 0.25f, P + P_PAL) * 0.5f;
    return mix(col, bc, saturate(bev));
}

SDF_KERNEL
void cube_grid(uchar4* __restrict__ out, const int w, const int h, FRAME_ARGS, const float* __restrict__ P,
               const float* __restrict__ C)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    TEX_FROM_ARGS;
    const Cam cam = cam_load(P);
    const float pa = pixel_angle(cam, h);
    const float px = x + 0.5f, py = y + 0.5f;
    const float w1 = P[P_W1], aa = P[P_AA];
    float3 col = f3(0.f);
    #pragma unroll 1
    for (int g = 0; g < 2; ++g) {
        const float wg = g ? w1 : 1.f - w1;
        if (wg < 0.002f) continue;
        const float* G = P + (g ? P_G1 : P_G0);
        const float* Cg = C + g * CG_CMAX * CG_CMAX * CG_CF;
        int edge = 0;
        float3 c = cg_shade(T, P, Cg, G, cam, cam_dir(cam, px, py, w, h), px / w, py / h, pa, h, edge);
        if (edge && aa > 0.f) {
            // rotated-grid 4x supersample, only on silhouettes / face borders
            float3 s = f3(0.f);
            #pragma unroll 1
            for (int k = 0; k < 4; ++k) {
                const float ox = k == 0 ? -0.125f : k == 1 ? 0.375f : k == 2 ? 0.125f : -0.375f;
                const float oy = k == 0 ? -0.375f : k == 1 ? -0.125f : k == 2 ? 0.375f : 0.125f;
                int e2 = 0;
                s += cg_shade(T, P, Cg, G, cam, cam_dir(cam, px + ox, py + oy, w, h), (px + ox) / w, (py + oy) / h,
                              pa, h, e2);
            }
            c = mix(c, s * 0.25f, aa);
        }
        col += c * wg;
    }
    store_rgb(out, y * w + x, col);
}
"""


def _clamp01(x: float) -> float:
    return min(max(float(x), 0.0), 1.0)


class CubeGrid(Effect):
    name = "cube_grid"
    stage = "projection"
    label = "Cube wall"
    description = "The picture cut into a wall of cubes that flip in staggered waves, side faces carrying neighbouring tiles."
    params = {
        **standard_effect_params(),
        "cells": num(4, 24, 10, "Columns", step=1, tier="regional", basic=True),
        "wave_pattern": num(0.0, 2.0, 1.0, "Wave pattern (row .. diagonal .. radial)", tier="regional"),
        "flip": num(0.0, 0.5, 0.25, "Flip angle", unit="turn", step=0.005, tier="regional"),
        "gap": num(0.0, 0.10, 0.04, "Gap", step=0.002, tier="regional"),
        "perspective": num(0.0, 1.0, 0.5, "Perspective", tier="regional"),
        "side_shading": num(0.0, 1.0, 0.7, "Side shading", tier="regional"),
        "axis_mix": num(0.0, 1.0, 0.3, "Flip axis (sideways .. vertical)", tier="regional"),
        "hold": num(0.0, 2.0, 0.5, "Hold", unit="s", tier="regional"),
        "sweep": num(0.2, 3.0, 1.0, "Wave sweep time", unit="s", tier="regional"),
        "auto": num(0.0, 1.0, 0.25, "Auto waves", unit="/s", tier="regional"),
        "ripple": num(0.0, 20.0, 5.0, "Idle ripple", unit="°", step=0.5, tier="regional"),
        "tint": num(0.0, 1.0, 0.4, "Palette tint (side faces)", tier="regional"),
        "edge": num(0.0, 1.0, 0.5, "Edge glints", tier="regional"),
        "glow": num(0.0, 1.0, 0.5, "Gap glow", tier="regional"),
        "backdrop": num(0.0, 1.0, 0.35, "Backdrop", tier="regional"),
        **palette_params("neon", 1.0, 0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._k = mod.get_function("cube_grid")
        self._kp = mod.get_function("cube_grid_prep")
        self.mips = FrameMips(ctx, levels=4, blur_sigma=2.5)
        self.P = cp.zeros(NP, cp.float32)
        self.C = cp.zeros(2 * CMAX * CMAX * CF, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.A = ctx.width / ctx.height
        self.rng = np.random.default_rng(7)
        self.waves: list[list[float]] = []     # [tau, axis, sign, reversed]
        self.sign = 1.0
        self.prev_pulse = 0.0
        self.refr = 0.0
        self.auto_ctr = 0.999                  # first idle wave launches straight away
        self.drift = 0.0
        self.ph1 = 0.0
        self.ph2 = 1.7
        self.rph = 0.0
        key = np.asarray([-0.45, 0.6, 0.66])
        self.key = key / np.linalg.norm(key)

    def _launch(self, axis_mix: float) -> None:
        if len(self.waves) >= MAXW:
            return
        axis = 1.0 if self.rng.random() < axis_mix else 0.0
        rev = 1.0 if self.rng.random() < 0.5 else 0.0
        self.waves.append([0.0, axis, self.sign, rev])
        self.sign = -self.sign

    def _grid(self, h, off: int, n: int, gap_m: float) -> None:
        A = self.A
        n = int(min(max(n, 1), CMAX))
        rows = int(min(max(round(n / A), 1), CMAX))
        cw, ch = 2.0 * A / n, 2.0 / rows
        s = 1.0 - gap_m
        hx, hy = 0.5 * cw * s, 0.5 * ch * s
        h[off:off + 8] = (n, rows, cw, ch, hx, hy, 0.5 * (hx + hy), 0.0)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = min(max(float(dt), 0.0), 0.1)
        a = _clamp01(p["amount"])
        pulse = _clamp01(p["pulse"])
        m = smoothstep01(a)
        # host-integrated phases (wrapped) and wave bookkeeping run even at amount 0, so nothing jumps
        self.drift = math.fmod(self.drift + p["drift_speed"] * dt, 10.0)
        self.ph1 = math.fmod(self.ph1 + 0.29 * dt, 2.0 * math.pi)
        self.ph2 = math.fmod(self.ph2 + 0.17 * dt, 2.0 * math.pi)
        self.rph = math.fmod(self.rph + 2.0 * math.pi * 0.22 * dt, 2.0 * math.pi)
        if dt > 0:
            self.refr -= dt
            rising = pulse - self.prev_pulse > 0.15 and self.refr <= 0.0
            self.prev_pulse = pulse
            self.auto_ctr += p["auto"] * dt
            launch = rising
            if self.auto_ctr >= 1.0:
                self.auto_ctr -= math.floor(self.auto_ctr)
                launch = True
            for wv in self.waves:
                wv[0] += dt
            life = p["sweep"] * 1.1 + p["hold"] + SETTLE + 0.1
            self.waves = [wv for wv in self.waves if wv[0] < life]
            if launch and m > 0.02:
                self._launch(p["axis_mix"])
                self.refr = 0.25
        if a <= 1e-4:
            self.ctx.cp.copyto(out, src)
            return
        A = self.A
        h = self.host
        h[:] = 0.0
        # camera: flat pose -> dolly back + slow orbit (perspective)
        D = flat_distance(FOV)
        persp = p["perspective"] * m
        cells = float(p["cells"])
        n0 = int(math.floor(cells))
        f = cells - n0
        if f < 2e-3:
            n0, w1 = n0, 0.0
        elif f > 0.998:
            n0, w1 = n0 + 1, 0.0
        else:
            w1 = f
        gap_m = p["gap"] * m
        self._grid(h, 40, n0, gap_m)
        self._grid(h, 48, n0 + 1, gap_m)
        z0 = float(h[46])
        yaw = persp * 0.2 * math.sin(self.ph1)
        pitch = persp * (0.08 + 0.07 * math.sin(self.ph2))
        cam = camera_orbit((0.0, 0.0, -z0), (D + z0) * (1.0 + 0.14 * persp), yaw, pitch, 0.0, FOV)
        h[0:13] = cam
        h[13] = m
        h[14] = A
        h[15] = p["side_shading"] * m
        h[16] = p["tint"] * m
        h[17] = p["edge"] * m * (0.45 + 0.55 * pulse)
        h[18] = self.drift
        h[19] = p["palette_period"]
        h[20] = p["backdrop"]
        h[21] = p["glow"] * m * (0.3 + 0.25 * pulse)
        h[22] = -0.25
        h[23] = w1
        h[24:27] = self.key
        h[27] = smoothstep01(m / 0.12)
        h[28:40] = palette_coeffs(p["palette"])
        nw = len(self.waves)
        h[56] = nw
        h[57] = p["sweep"]
        h[58] = p["hold"]
        h[59] = p["wave_pattern"]
        h[60] = math.radians(p["ripple"]) * m
        h[61] = self.rph
        h[62] = OMEGA
        h[63] = ZETA
        h[64] = 1.05
        h[65] = p["axis_mix"]
        amp = p["flip"] * 2.0 * math.pi * m
        for i, (tau, axis, sgn, rev) in enumerate(self.waves):
            o = 66 + i * WF
            h[o:o + WF] = (tau, axis, sgn, rev, amp)
        self.P.set(h)
        self.mips.build(src)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._kp, CMAX * CMAX, 2, (self.C, self.P))
        launch_2d(self._k, W, H, (out, np.int32(W), np.int32(H), *self.mips.args(src), self.P, self.C))
