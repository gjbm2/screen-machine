"""Frost spread: the old picture freezes from the frame edges into jittered Voronoi ice cells (frosted blur, blue-white
tint, crystal feathers, bright crack lines with twinkling star glints, per-cell displacement and RGB fringing).
Each fully iced cell swaps old -> new under the frost (hashed delay around the midpoint), then the ice thaws from
the centre outward, each cell melting from its middle to its rim, and the cracks fade last.

Source: https://godotshaders.com/?p=16390
Author: TTien63 (godotshaders 'Frostbite')
License: CC0-1.0   (godotshaders.com listing licence: CC0)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the Frostbite frost look (Voronoi ice cells, frosted blur, tint, bright
  crack lines, per-cell offset and colour fringing) rebuilt as a two-input transition: a jittered-grid Voronoi
  field (cell id, border neighbour, exact border distance via the bisector form) and static frost noise (haze
  fbm + per-cell oriented ridged "feathers") are precomputed per seed in setup; per frame the kernel schedules a
  freeze front from the frame edges (cracks race ahead of the frost along cell borders, the frost fills each
  cell rim -> middle), a per-cell old -> new swap under full ice with a cold flash, and a centre-out thaw whose
  crack lines fade last.  The blur is an 8-tap Poisson disc on frame3d FrameMips trilinear levels (one chain
  per input, built only while that input is visible); the R and B channels come from the two interleaved
  4-tap subsets offset along the cell's displacement direction, so the fringing costs no extra taps.  Crack
  and glint light are screen-blended through the shared cosine palette with a drifting index; glints are
  host-placed on the crack network (snapped to the nearest cell border) and twinkle on a smooth clock.
  Endpoints are early-out copies.)

Binder: style lively, 3-5 s, a breakdown or a cold change of key; tense / icy / elegant.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.frame3d import FRAME3D, FRAME3D_OPTIONS, FrameMips
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num
from stream_service.plugins._lib.sdf3d import SDF3D

# schedule (progress units)
_T0, _TF = 0.045, 0.27          # freeze front: first cells at _T0, last at _T0 + _TF
_FILL, _RAMP = 0.08, 0.05       # rim -> middle fill time, frost ramp
_LEAD = 0.025                   # cracks run ahead of the frost
_SW0, _SWS, _SWW = 0.47, 0.07, 0.035   # per-cell swap start window and its fade width
_TH0, _THS = 0.56, 0.26         # thaw: centre at _TH0, the corners by _TH0 + _THS
_NU = 20                        # floats in the per-frame uniform block

_SRC = HELPERS + PALETTE_CUDA + SDF3D + FRAME3D + r"""
#define FS_TAU 6.2831853f

// -------------------------------------------------------------------------------- setup: Voronoi + frost noise
// fld = (cell id, border neighbour id, border distance * 16 px, haze << 8 | feather)
extern "C" __global__
void frost_field(ushort4* __restrict__ fld, const float* __restrict__ cells, const int gx, const int gy,
                 const int W, const int H, const float cw, const float ch, const float hscale,
                 const float fscale, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + .5f, py = y + .5f;
    const int ci = min(max((int)(px / cw), 0), gx - 1), cj = min(max((int)(py / ch), 0), gy - 1);
    float best = 1e30f, bx = px, by = py;
    int bi = cj * gx + ci;
    for (int j = -2; j <= 2; ++j) {
        const int jj = cj + j;
        if (jj < 0 || jj >= gy) continue;
        for (int i = -2; i <= 2; ++i) {
            const int ii = ci + i;
            if (ii < 0 || ii >= gx) continue;
            const int k = jj * gx + ii;
            const float sx = cells[8 * k], sy = cells[8 * k + 1];
            const float d = (sx - px) * (sx - px) + (sy - py) * (sy - py);
            if (d < best) { best = d; bi = k; bx = sx; by = sy; }
        }
    }
    float md = 1e30f;
    int nb = bi;
    for (int j = -2; j <= 2; ++j) {
        const int jj = cj + j;
        if (jj < 0 || jj >= gy) continue;
        for (int i = -2; i <= 2; ++i) {
            const int ii = ci + i;
            if (ii < 0 || ii >= gx) continue;
            const int k = jj * gx + ii;
            if (k == bi) continue;
            const float sx = cells[8 * k], sy = cells[8 * k + 1];
            const float ex = sx - bx, ey = sy - by;
            const float el = sqrtf(ex * ex + ey * ey);
            if (el < 1e-3f) continue;
            const float d = ((0.5f * (sx + bx) - px) * ex + (0.5f * (sy + by) - py) * ey) / el;
            if (d < md) { md = d; nb = k; }
        }
    }
    md = fminf(fmaxf(md, 0.f), 4095.f);
    // haze: soft fbm; feathers: ridged fbm stretched along each cell's own crystal axis
    const float hz = clamp01((fbm(px * hscale, py * hscale, seed) - 0.22f) * 1.8f);
    const float ang = cells[8 * bi + 7] * 3.1415926f;
    float sa, ca;
    __sincosf(ang, &sa, &ca);
    const float u = (ca * px + sa * py) * fscale, v = (-sa * px + ca * py) * fscale * 0.22f;
    const float r = 1.f - fabsf(2.f * fbm(u, v, seed + 911u) - 1.f);
    const float fe = clamp01((r - 0.45f) * 1.9f);
    const unsigned int hzq = (unsigned int)(hz * 255.f + 0.5f), feq = (unsigned int)(fe * 255.f + 0.5f);
    fld[y * W + x] = make_ushort4((unsigned short)bi, (unsigned short)nb, (unsigned short)(md * 16.f + 0.5f),
                                  (unsigned short)((hzq << 8) | feq));
}

// -------------------------------------------------------------------------------- per frame
__device__ __forceinline__ float3 fs_screen(float3 a, float3 b) {
    b = clamp(b, 0.f, 1.f);
    return 1.f - (1.f - a) * (1.f - b);
}

// U: 0 p | 1 fill | 2 ramp | 3 edge_bias | 4 interior px | 5 blur px | 6 displacement px | 7 fringe px | 8 tint
//    9 crack brightness | 10 crack width px | 11 halo px | 12 glint gain | 13 glint grid px | 14 twinkle phase
//    15 palette index offset (drift) | 16 thaw0 | 17 thaw span | 18 crack lead
extern "C" __global__
void frost_spread(uchar4* __restrict__ out, FRAME_ARGS_AS(a), FRAME_ARGS_AS(b),
                  const ushort4* __restrict__ fld, const float4* __restrict__ cells,
                  const float4* __restrict__ glints, const int GX, const int GY,
                  const float* __restrict__ U, const float* __restrict__ P, const unsigned int seed,
                  const float t0, const float tf)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int W = a_W, H = a_H;
    if (x >= W || y >= H) return;
    TEX_FROM_ARGS_AS(a, TA);
    TEX_FROM_ARGS_AS(b, TB);
    const float p = U[0];
    const ushort4 f = fld[y * W + x];
    const int c = f.x, n = f.y;
    const float bd = (float)f.z * (1.f / 16.f);
    const float hz = (float)(f.w >> 8) * (1.f / 255.f), fe = (float)(f.w & 255u) * (1.f / 255.f);
    const float4 C0 = cells[2 * c], C1 = cells[2 * c + 1];
    const float4 N0 = cells[2 * n], N1 = cells[2 * n + 1];
    const float px = x + .5f, py = y + .5f;
    const float hw = 0.5f * W, hh = 0.5f * H;
    const float epix = clamp01(fminf(fminf(px, W - px), fminf(py, H - py)) / hh);
    const float rpix = clamp01(sqrtf((px - hw) * (px - hw) + (py - hh) * (py - hh)) * rsqrtf(hw * hw + hh * hh));
    const float interior = clamp01(bd / U[4]);
    const float eb = U[3];

    // ---- schedule: freeze from the edges (cracks lead along the borders), swap under the ice, thaw centre-out
    const float pe = eb * epix;
    const float phc = 0.7f * C0.z + 0.3f * (pe + (1.f - eb) * C0.z) + 0.06f * (hz - 0.5f);
    const float phn = 0.7f * N0.z + 0.3f * (pe + (1.f - eb) * N0.z) + 0.06f * (hz - 0.5f);
    const float tfr = t0 + tf * phc + U[1] * interior;
    const float Fin = smooth(tfr, tfr + U[2], p);
    const float tcr = t0 + tf * fminf(phc, phn) - U[18];
    const float Cin = smooth(tcr, tcr + 0.02f, p);
    const float thc = U[16] + U[17] * (0.55f * C1.z + 0.3f * rpix + 0.15f * C1.w);
    const float thn = U[16] + U[17] * (0.55f * N1.z + 0.3f * rpix + 0.15f * N1.w);
    const float thf = thc + 0.05f * (1.f - interior);            // each cell melts middle -> rim
    const float Fout = smooth(thf, thf + 0.05f, p);
    const float thk = fmaxf(thc, thn) + 0.08f;                     // the cracks go last
    const float Cout = smooth(thk, thk + 0.04f, p);
    const float F = Fin * (1.f - Fout), C = Cin * (1.f - Cout);
    const float s = smooth(C0.w, C0.w + 0.035f, p);               // this cell's old -> new swap

    const float h2 = fract(C1.w * 7.131f + 0.37f);
    const float3 tc = pal(U[15] + 0.35f * hz + 0.22f * C1.w, P);
    const float3 tl = 0.35f + 0.65f * tc;                          // lifted tint (ice is light)

    // ---- the picture under the ice
    float3 col = make_float3(0.f, 0.f, 0.f);
    if (s < 1.f) col = tex_fetch(TA, x, y) * (1.f - s);
    if (s > 0.f) col += tex_fetch(TB, x, y) * s;

    const float glow = __expf(-bd / U[11]);
    if (F > 1e-3f) {
        // per-cell displacement (+ a little haze wobble), Poisson-disc frosted blur, fringing from the two subsets
        const float dsc = U[6] * F;
        const float dvx = C1.x * dsc + (hz - 0.5f) * 0.3f * dsc, dvy = C1.y * dsc + (fe - 0.5f) * 0.3f * dsc;
        const float iW = 1.f / W, iH = 1.f / H;
        const float u0 = (px + dvx) * iW, v0 = (py + dvy) * iH;
        const float R = U[5] * F;
        const float lod = fminf(log2f(fmaxf(R * 0.4f, 1.f)), (float)TA.nlev);
        float sa, ca;
        __sincosf(FS_TAU * hash1(x, y, seed), &sa, &ca);
        const float fr = U[7] * F * (0.4f + 0.6f * glow);
        float dl = rsqrtf(fmaxf(C1.x * C1.x + C1.y * C1.y, 1e-6f));
        const float fdx = C1.x * dl * fr, fdy = C1.y * dl * fr;
        const float PX[8] = {0.33f, 0.672f, 0.f, -0.601f, -0.45f, -0.707f, 0.f, 0.354f};
        const float PY[8] = {0.f, 0.672f, 0.62f, 0.601f, 0.f, -0.707f, -0.7f, -0.354f};
        float3 e3 = make_float3(0.f, 0.f, 0.f), o3 = make_float3(0.f, 0.f, 0.f);
        #pragma unroll
        for (int k = 0; k < 8; ++k) {
            const float sg = (k & 1) ? -1.f : 1.f;                  // even taps +fringe, odd taps -fringe
            const float ox = (PX[k] * ca - PY[k] * sa) * R + sg * fdx;
            const float oy = (PX[k] * sa + PY[k] * ca) * R + sg * fdy;
            const float uu = u0 + ox * iW, vv = v0 + oy * iH;
            float3 t = make_float3(0.f, 0.f, 0.f);
            if (s < 1.f) t = tex_sample(TA, uu, vv, lod) * (1.f - s);
            if (s > 0.f) t += tex_sample(TB, uu, vv, lod) * s;
            if (k & 1) o3 += t; else e3 += t;
        }
        float3 ice = (e3 + o3) * 0.125f;
        const float fw = smooth(0.f, 1.5f, fr);
        ice.x = mix(ice.x, e3.x * 0.25f, fw);
        ice.z = mix(ice.z, o3.z * 0.25f, fw);
        // blue-white toning, frost haze + crystal feathers, whiter rims, per-cell facets, the swap's cold flash
        const float tint = U[8];
        const float l = luma3(ice);
        ice = mix(ice, f3(l) * (0.45f + 0.75f * tl), 0.7f * tint);
        const float fz = tint * (0.08f + 0.3f * hz + 0.38f * fe * fe) * (0.85f + 0.3f * h2);
        ice = fs_screen(ice, tl * fz);
        ice = fs_screen(ice, tl * (0.24f * tint * glow));
        ice = ice * (0.92f + 0.16f * h2);
        ice = fs_screen(ice, tl * (0.2f * __sinf(3.1415926f * s)));
        col = mix(col, ice, F);
    }

    // ---- cracks: a bright core, a soft halo and a thin dark groove beside it
    if (C > 1e-3f) {
        const float cw = U[10];
        const float line = 1.f - smooth(0.5f * cw, 1.5f * cw, bd);
        const float k = clamp01(U[9] * C * (0.9f * line + 0.35f * glow));
        col = fs_screen(col, mix(f3(1.f), tl, 0.35f) * k);
        const float groove = smooth(1.2f * cw, 2.2f * cw, bd) * (1.f - smooth(2.6f * cw, 5.f * cw, bd));
        col = col * (1.f - 0.3f * C * groove * fminf(U[9], 1.f));
    }

    // ---- glints: four-point stars sitting on the crack network, twinkling on a smooth clock
    if (U[12] > 0.f && C > 1e-3f) {
        const float G = U[13];
        const int gi0 = (int)floorf(px / G), gj0 = (int)floorf(py / G);
        float gs = 0.f;
        for (int j = -1; j <= 1; ++j) {
            const int gj = gj0 + j;
            if (gj < 0 || gj >= GY) continue;
            for (int i = -1; i <= 1; ++i) {
                const int gi = gi0 + i;
                if (gi < 0 || gi >= GX) continue;
                const float4 g1 = glints[2 * (gj * GX + gi) + 1];
                if (g1.y <= 0.f) continue;
                const float4 g0 = glints[2 * (gj * GX + gi)];
                const float dx = px - g0.x, dy = py - g0.y;
                const float r2 = dx * dx + dy * dy, L2 = g1.w * g1.w;
                if (r2 >= L2) continue;
                const float env = smooth(g0.z, g0.z + 0.03f, p) * (1.f - smooth(g0.w, g0.w + 0.04f, p));
                if (env <= 0.f) continue;
                const float tw = 0.5f + 0.5f * __cosf(FS_TAU * (U[14] + g1.x));
                const float twk = 0.2f + 0.8f * tw * tw * tw * tw;
                float sa, ca;
                __sincosf(g1.z, &sa, &ca);
                const float ax = dx * ca + dy * sa, ay = -dx * sa + dy * ca;
                const float w2 = fmaxf(U[10] * U[10], 0.5f);
                const float arm = 0.22f * g1.w;
                const float star = 1.3f * __expf(-r2 / (4.f * w2))
                                 + __expf(-fabsf(ax) / arm - ay * ay / w2) + __expf(-fabsf(ay) / arm - ax * ax / w2);
                gs += g1.y * env * twk * star * (1.f - r2 / L2);
            }
        }
        col = fs_screen(col, mix(f3(1.f), tl, 0.2f) * (U[12] * gs));
    }
    store_rgb(out, y * W + x, col);
}
"""


class FrostSpread(Transition):
    name = "frost_spread"
    label = "Frost spread"
    description = ("The old picture freezes from the edges into cracked Voronoi ice cells, each cell swaps to the new "
                   "picture under the frost, then the ice thaws from the centre out and the cracks fade last.")
    tags = {"family": "organic", "dims": "2d", "toolkit": "none", "technique": ["tiling", "optical"],
            "moods": ["icy", "tense", "elegant"], "style": "lively", "energy": [0.2, 0.8]}
    license_class = "permissive"
    default_duration_s = 4.0
    params = {
        "cells": num(4, 24, 9, "Ice cells (rows)", tier="regional", integer=True, basic=True),
        "edge_bias": num(0.0, 1.0, 0.85, "Freeze from the edges", tier="regional"),
        "crack_brightness": num(0.0, 2.0, 1.0, "Crack brightness", tier="regional"),
        "ice_tint": num(0.0, 1.0, 0.65, "Ice tint", tier="regional"),
        "blur": num(0.0, 1.0, 0.6, "Frost blur", tier="regional"),
        "displacement": num(0.0, 1.0, 0.5, "Cell displacement", tier="regional"),
        "fringe": num(0.0, 1.0, 0.5, "RGB fringing", tier="regional"),
        "glints": num(0.0, 1.0, 0.7, "Glints", tier="regional"),
        "palette": enum(list(PALETTES), "ice", "Ice palette", tier="regional"),
        "drift_speed": num(-1.0, 1.0, 0.08, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        mod = raw_module(_SRC, FRAME3D_OPTIONS)
        self._kf = mod.get_function("frost_field")
        self._k = mod.get_function("frost_spread")
        self.mips_a = FrameMips(ctx, levels=4, blur_level=0)
        self.mips_b = FrameMips(ctx, levels=4, blur_level=0)
        self._U = cp.zeros(_NU, cp.float32)
        self._Uh = np.zeros(_NU, np.float32)
        self._P = cp.zeros(12, cp.float32)
        self._pal = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31 - 1)) if s < 0 else s
        self._fld = cp.zeros((ctx.height, ctx.width, 4), cp.uint16)
        self._build(ctx.width, ctx.height)

    # ------------------------------------------------------------------ host: per-seed cells, field, glints
    def _build(self, w: int, h: int) -> None:
        cp = self.ctx.cp
        p = self.params
        rows = int(min(max(int(p.get("cells", 9)), 4), 24))
        gy = rows
        gx = max(1, int(round(rows * w / h)))
        cw, ch = w / gx, h / gy
        eb = float(min(max(float(p.get("edge_bias", 0.85)), 0.0), 1.0))
        rng = np.random.default_rng(self._seed)
        n = gx * gy
        jj, ii = np.divmod(np.arange(n), gx)
        jit = 0.85
        sx = (ii + 0.5 + jit * (rng.random(n) - 0.5)) * cw
        sy = (jj + 0.5 + jit * (rng.random(n) - 0.5)) * ch
        e_site = np.clip(np.minimum(np.minimum(sx, w - sx), np.minimum(sy, h - sy)) / (0.5 * h), 0.0, 1.0)
        hsh = rng.random(n)
        raw = eb * e_site + (1.0 - eb) * hsh + 0.08 * rng.random(n)
        phi = (raw - raw.min()) / max(float(np.ptp(raw)), 1e-6)
        rs = np.clip(np.hypot(sx - 0.5 * w, sy - 0.5 * h) / math.hypot(0.5 * w, 0.5 * h), 0.0, 1.0)
        thmin = _TH0 + _THS * (0.55 * rs + 0.15 * hsh)
        ts = np.minimum(_SW0 + _SWS * rng.random(n), thmin - 0.05)
        ang = rng.uniform(0, 2 * math.pi, n)
        mag = rng.uniform(0.35, 1.0, n)
        crystal = rng.random(n)
        tab = np.stack([sx, sy, phi, ts, np.cos(ang) * mag, np.sin(ang) * mag, rs, hsh], 1).astype(np.float32)
        # the setup kernel reads slot 7 as each cell's crystal-axis angle; the frame kernel reads it as the hash
        setup_tab = tab.copy()
        setup_tab[:, 7] = crystal
        self._cells = cp.asarray(tab.reshape(-1))
        self._ts_min, self._ts_max = float(ts.min()), float(ts.max())
        setup_cells = cp.asarray(setup_tab.reshape(-1))
        hscale = 1.0 / (0.3 * ch)
        fscale = 1.0 / max(0.008 * h, 2.0)
        launch_2d(self._kf, w, h, (self._fld, setup_cells, np.int32(gx), np.int32(gy), np.int32(w), np.int32(h),
                                   np.float32(cw), np.float32(ch), np.float32(hscale), np.float32(fscale),
                                   np.uint32(self._seed & 0x7FFFFFFF)))
        self._interior = 0.45 * min(cw, ch)

        # glints: one candidate per grid cell, snapped onto the nearest cell border (a crack)
        G = max(12.0, 64.0 * h / 2160.0)
        GX, GY = int(math.ceil(w / G)), int(math.ceil(h / G))
        m = GX * GY
        gj, gi = np.divmod(np.arange(m), GX)
        qx = np.clip((gi + rng.random(m)) * G, 1.0, w - 1.0)
        qy = np.clip((gj + rng.random(m)) * G, 1.0, h - 1.0)
        S = np.stack([sx, sy], 1)
        d2 = (qx[:, None] - sx[None, :]) ** 2 + (qy[:, None] - sy[None, :]) ** 2
        c = np.argmin(d2, 1)
        ex = sx[None, :] - sx[c][:, None]
        ey = sy[None, :] - sy[c][:, None]
        el = np.hypot(ex, ey)
        with np.errstate(divide="ignore", invalid="ignore"):
            bd = ((0.5 * (sx[None, :] + sx[c][:, None]) - qx[:, None]) * ex
                  + (0.5 * (sy[None, :] + sy[c][:, None]) - qy[:, None]) * ey) / el
        bd = np.where(el < 1e-3, np.inf, bd)
        nb = np.argmin(bd, 1)
        dist = bd[np.arange(m), nb]
        ux = ex[np.arange(m), nb] / np.maximum(el[np.arange(m), nb], 1e-6)
        uy = ey[np.arange(m), nb] / np.maximum(el[np.arange(m), nb], 1e-6)
        gx_ = qx + dist * ux
        gy_ = qy + dist * uy
        margin = 0.15 * G
        act = ((rng.random(m) < 0.42) & np.isfinite(dist) & (dist < 0.6 * G)
               & (gx_ > margin) & (gx_ < w - margin) & (gy_ > margin) & (gy_ < h - margin))
        epg = np.clip(np.minimum(np.minimum(gx_, w - gx_), np.minimum(gy_, h - gy_)) / (0.5 * h), 0.0, 1.0)
        rpg = np.clip(np.hypot(gx_ - 0.5 * w, gy_ - 0.5 * h) / math.hypot(0.5 * w, 0.5 * h), 0.0, 1.0)
        pc = 0.7 * phi[c] + 0.3 * (eb * epg + (1.0 - eb) * phi[c])
        pn = 0.7 * phi[nb] + 0.3 * (eb * epg + (1.0 - eb) * phi[nb])
        t_on = _T0 + _TF * np.minimum(pc, pn) - _LEAD + 0.03
        thc = _TH0 + _THS * (0.55 * rs[c] + 0.3 * rpg + 0.15 * hsh[c])
        thn = _TH0 + _THS * (0.55 * rs[nb] + 0.3 * rpg + 0.15 * hsh[nb])
        t_off = np.maximum(thc, thn) + 0.05
        gt = np.zeros((m, 8), np.float32)
        gt[:, 0] = gx_
        gt[:, 1] = gy_
        gt[:, 2] = t_on
        gt[:, 3] = t_off
        gt[:, 4] = rng.random(m)
        gt[:, 5] = np.where(act, rng.uniform(0.6, 1.0, m), 0.0)
        gt[:, 6] = rng.uniform(0.0, 0.5 * math.pi, m)
        gt[:, 7] = (0.3 + 0.12 * rng.random(m)) * G
        gt[~np.isfinite(gt)] = 0.0
        self._glints = cp.asarray(gt.reshape(-1))
        self._GX, self._GY = GX, GY

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "ice")
        if name not in PALETTES:
            name = "ice"
        if name != self._pal:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        P = float(progress)
        if P <= 1e-4:
            cp.copyto(out, a)
            return
        if P >= 0.9999:
            cp.copyto(out, b)
            return
        w, h = self.ctx.width, self.ctx.height
        self._sync_palette()
        p = self.params
        sc = h / 2160.0
        dur = self.duration_s
        U = self._Uh
        U[0] = P
        U[1] = _FILL
        U[2] = _RAMP
        U[3] = min(max(float(p["edge_bias"]), 0.0), 1.0)
        U[4] = self._interior
        U[5] = min(max(float(p["blur"]), 0.0), 1.0) * 0.016 * h
        U[6] = min(max(float(p["displacement"]), 0.0), 1.0) * 0.018 * h
        U[7] = min(max(float(p["fringe"]), 0.0), 1.0) * 0.005 * h
        U[8] = min(max(float(p["ice_tint"]), 0.0), 1.0)
        U[9] = min(max(float(p["crack_brightness"]), 0.0), 2.0)
        U[10] = max(1.0, 1.5 * sc)
        U[11] = max(2.0, 7.0 * sc)
        U[12] = 1.2 * min(max(float(p["glints"]), 0.0), 1.0)
        U[13] = max(12.0, 64.0 * sc)
        U[14] = (P * dur * 1.4) % 1.0
        U[15] = (0.45 + float(p["drift_speed"]) * P * dur) % 1.0
        U[16] = _TH0
        U[17] = _THS
        U[18] = _LEAD
        self._U.set(U)
        # mip chains only while an input can be under the frost
        if P < self._ts_max + _SWW + 0.01:
            self.mips_a.build(a)
        if P > self._ts_min - 0.01:
            self.mips_b.build(b)
        launch_2d(self._k, w, h,
                  (out, *self.mips_a.args(a), *self.mips_b.args(b), self._fld, self._cells, self._glints,
                   np.int32(self._GX), np.int32(self._GY), self._U, self._P, np.uint32(self._seed & 0x7FFFFFFF),
                   np.float32(_T0), np.float32(_TF)))

    def close(self) -> None:
        self._fld = None
        self._cells = None
        self._glints = None
        self.mips_a = None
        self.mips_b = None
        super().close()
