"""Penrose tiling: an endless rhombus tiling computed per pixel from de Bruijn's pentagrid, bevelled like glazed
ceramic, inlaid with arcs that join into smooth circles across tiles, while slow grid drift flips tiles one by one.

Source: Original (inspired by https://en.wikipedia.org/wiki/Penrose_tiling)
Author: smfx (original; de Bruijn pentagrid; xscreensaver penrose.c is a permissive reference only)
License: MIT
license_class: permissive

Written from scratch from de Bruijn's construction (nothing copied):
  * F grid families (F = 5 for Penrose, 7 for a heptagonal tiling, 4 at 45 degrees for the 8-fold
    Ammann-Beenker tiling) with unit normals e_j and offsets g_j.  A point z of grid space lies in the mesh
    K_j = ceil(<z, e_j> + g_j); a mesh maps to the tiling vertex V = sum K_j e_j, and each crossing of line
    k_r of family r with line k_s of family s maps to the rhombus V00 + [0,1] e_r + [0,1] e_s.
  * Per pixel point location: the tiling point p corresponds to roughly z0 = (2/F)(p - sum (g_j + 1/2) e_j) in
    grid space.  For every family pair a fixed-point step (take the other K_j at z0, solve p for (k_r, k_s),
    recompute the other K_j at that crossing, repeat <= 3x) finds the tile ~95% of the time; a 3 x 3 search of
    crossings around z0 per pair (exact in testing) catches the rest.  A candidate counts only when its own
    crossing reproduces its vertex and p lies inside it, so any hit is the true tile.
  * Decoration.  Every edge joins vertices whose index I = sum K_j differs by one, so crossing every edge at
    d(lower index) from its lower end (d = a for odd, 1 - a for even indices) and drawing arcs around the
    acute-corner pair of each rhomb (radius d(i), 1 - d(i+1)) or its other corner pair (radius 1 - d(i)) makes
    arcs that always meet across edges at right angles: continuous curves that close into circles around stars.
    The 'ribbon' decoration joins the midpoints of each tile's opposite edges: the de Bruijn ribbons (worms),
    straight inside every tile and continuous across the tiling, five families of zig-zag bars (the slot the
    spec gives to Ammann bars, which this does not reproduce exactly).
  * Phason drift: the offsets move as g_j = g0_j + <phi(t), e'_j> where e'_j are the directions at k_perp
    times the grid angle (perpendicular space), so the tiling never translates but tiles flip one hexagon at a
    time as phi wanders on a slow Lissajous path.

Look: bevelled (mitred) raised rhombs with a soft top light and a specular rim, dark grout seams that glow faintly
with energy, glowing inlays.  Tile colour = pal(type / orientation (colour_mode) + per-tile hash + a colour wave
sweeping across the tiles (tile-quantised) + distance from the centre) * period + drift.

Macros: energy -> phason flip rate (e^1.3), colour-wave speed and depth, inlay glow (0.4 + 0.6 e), seam glow,
pan and rotation speed.  pulse -> on each rising edge a ring of light expands from the centre and lights the tiles
it passes (tile by tile, brightness only), plus an inlay gain kick (1 + 0.7 pulse).  speed scales every rate;
palette colours tiles and inlays; intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

FMAX = 7
NRING = 4
PAIR0, PSTR = 96, 12
PSIZE = PAIR0 + PSTR * (FMAX * (FMAX - 1) // 2) + 8

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 F | 15 npairs | 16..22 ex | 23..29 ey | 30..36 g | 37,38 G | 39 2/F
#  40 cos view | 41 sin view | 42 tiling units per internal px | 43,44 pan | 45 1 / (units per half-height)
#  48 wave phase | 49,50 wave dir (tiling) | 51 wave freq | 52 wave amp | 53 colour mode | 54 arc weight
#  55 ribbon weight | 56 arc radius a | 57 inlay half width | 58 glow | 59 halo width | 60 bevel width
#  61 bevel depth | 62 grout half width | 63,64,65 light (tiling xy, z) | 66 output gain | 67 inlay gain
#  68 tile gain | 69 seam glow | 70..77 rings (radius, amp) x 4 | 78 ring width | 79 ntypes - 1
#  PAIR0 + q * PSTR: r, s, Minv (4: p - B -> (a, b)), Ainv (4: (k_r - g_r, k_s - g_s) -> z), type, acute
_SRC = HELPERS + PALETTE_CUDA + r"""
#define FMAX %(fmax)d
#define PAIR0 %(pair0)d
#define PSTR %(pstr)d

struct Tile { int q; float kr, ks, u, v, bx, by, isum; };

// try the rhombus at crossing (kr, ks) of pair q: true when p lies inside it.  u, v always come back so the
// caller can take a fixed-point step (the next guess is kr + floor(u), ks + floor(v)).
__device__ __forceinline__ bool try_tile(const float* __restrict__ P, int q, float kr, float ks, float px,
                                         float py, int F, Tile& T) {
    const float* Q = P + PAIR0 + q * PSTR;
    const int r = (int)Q[0], s = (int)Q[1];
    const float ar = kr - P[30 + r], as = ks - P[30 + s];
    const float zx = Q[6] * ar + Q[7] * as, zy = Q[8] * ar + Q[9] * as;
    float bx = kr * P[16 + r] + ks * P[16 + s], by = kr * P[23 + r] + ks * P[23 + s], isum = kr + ks;
    #pragma unroll
    for (int j = 0; j < FMAX; ++j) {
        if (j < F && j != r && j != s) {
            const float K = ceilf(zx * P[16 + j] + zy * P[23 + j] + P[30 + j]);
            bx += K * P[16 + j]; by += K * P[23 + j]; isum += K;
        }
    }
    const float dx = px - bx, dy = py - by;
    const float u = Q[2] * dx + Q[3] * dy, v = Q[4] * dx + Q[5] * dy;
    T.q = q; T.kr = kr; T.ks = ks; T.u = u; T.v = v; T.bx = bx; T.by = by; T.isum = isum;
    return u >= 0.f && u < 1.f && v >= 0.f && v < 1.f;
}

__device__ bool locate(const float* __restrict__ P, float px, float py, Tile& T) {
    const int F = (int)P[14], NP = (int)P[15];
    const float zx = P[39] * (px - P[37]), zy = P[39] * (py - P[38]);
    float x[FMAX];
    #pragma unroll
    for (int j = 0; j < FMAX; ++j) x[j] = (j < F) ? zx * P[16 + j] + zy * P[23 + j] + P[30 + j] : 0.f;
    // 1. fixed point per pair
    for (int q = 0; q < NP; ++q) {
        const float* Q = P + PAIR0 + q * PSTR;
        const int r = (int)Q[0], s = (int)Q[1];
        float bx = 0.f, by = 0.f;
        #pragma unroll
        for (int j = 0; j < FMAX; ++j) {
            if (j < F && j != r && j != s) { const float K = ceilf(x[j]); bx += K * P[16 + j]; by += K * P[23 + j]; }
        }
        const float dx = px - bx, dy = py - by;
        float kr = floorf(Q[2] * dx + Q[3] * dy), ks = floorf(Q[4] * dx + Q[5] * dy);
        #pragma unroll 1
        for (int it = 0; it < 3; ++it) {
            if (try_tile(P, q, kr, ks, px, py, F, T)) return true;
            kr += floorf(T.u); ks += floorf(T.v);
        }
    }
    // 2. exhaustive 3 x 3 crossings around the estimate, per pair
    for (int q = 0; q < NP; ++q) {
        const float* Q = P + PAIR0 + q * PSTR;
        const int r = (int)Q[0], s = (int)Q[1];
        float xr = 0.f, xs = 0.f;
        #pragma unroll
        for (int j = 0; j < FMAX; ++j) { if (j == r) xr = x[j]; if (j == s) xs = x[j]; }
        const float cr = floorf(xr), cs = floorf(xs);
        #pragma unroll 1
        for (int k = 0; k < 9; ++k) {
            const int dr = (k == 0) ? 0 : ((k - 1) %% 3) - 1, ds = (k == 0) ? 0 : ((k - 1) / 3) - 1;
            if (k > 0 && dr == 0 && ds == 0) continue;
            if (try_tile(P, q, cr + dr, cs + ds, px, py, F, T)) return true;
        }
    }
    return false;
}

// palette colour kept luminous for emissive inlays: lift the max channel to >= ~0.85
__device__ __forceinline__ float3 lum_pal(float t, const float* __restrict__ PAL) {
    const float3 c = pal(t, PAL);
    const float m = fmaxf(c.x, fmaxf(c.y, c.z));
    const float k = fminf(fmaxf(0.85f / fmaxf(m, 1e-3f), 1.f), 4.f);
    const float g = 0.08f * (1.f - smooth(0.2f, 0.6f, m));             // dark phases: a little white
    return make_float3(fminf(c.x * k + g, 1.f), fminf(c.y * k + g, 1.f), fminf(c.z * k + g, 1.f));
}

extern "C" __global__
void penrose_draw(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float* PAL = P;
    const float S = P[42];
    const float sx = (x + 0.5f - 0.5f * W) * S, sy = (y + 0.5f - 0.5f * H) * S;
    const float px = P[43] + P[40] * sx - P[41] * sy, py = P[44] + P[41] * sx + P[40] * sy;
    const float aa = 0.85f * S;
    const float vg_r2 = ((x + 0.5f - 0.5f * W) * (x + 0.5f - 0.5f * W) + (y + 0.5f - 0.5f * H) * (y + 0.5f - 0.5f * H))
                        / (0.25f * ((float)W * W + (float)H * H));
    const float vg = 1.f - 0.3f * vg_r2;

    Tile T;
    if (!locate(P, px, py, T)) {
        const float3 gc = pal(P[13] + 0.5f, PAL);
        const float g = 255.f * P[66] * 0.05f * vg;
        out[y * W + x] = pack(make_float4(gc.x * g, gc.y * g, gc.z * g, 255.f));
        return;
    }
    const float* Q = P + PAIR0 + T.q * PSTR;
    const int r = (int)Q[0], s = (int)Q[1];
    const float erx = P[16 + r], ery = P[23 + r], esx = P[16 + s], esy = P[23 + s];
    const float vx = T.bx, vy = T.by;                      // V00
    const float u = T.u, v = T.v;
    const float cth = erx * esx + ery * esy;
    const float hgt = sqrtf(fmaxf(1.f - cth * cth, 1e-4f));   // rhomb height (side 1)
    const float type = Q[10];
    // tile centre, its distance from the view centre (half-heights) and a per-tile hash
    const float cx = vx + 0.5f * (erx + esx), cy = vy + 0.5f * (ery + esy);
    const float dcx = cx - P[43], dcy = cy - P[44];
    const float dc = sqrtf(dcx * dcx + dcy * dcy) * P[45];
    const float th = hash1((unsigned int)(T.kr + 4096.f) * 7919u + (unsigned int)(T.ks + 4096.f),
                           (unsigned int)T.q, (unsigned int)(T.isum + 64.f));

    // ---- colour index: type / orientation + hash + travelling wave (tile-quantised) + distance + drift
    const float F = P[14];
    const float t_type = type * 0.31f;
    const float rs = (float)(r + s);
    const float t_or = (rs - F * floorf(rs / F)) / F + 0.13f * type;
    const float wave = __sinf(6.2831853f * (P[51] * (cx * P[49] + cy * P[50])) - P[48]);
    const float tb = t_type + (t_or - t_type) * P[53];
    const float tcol = P[13] + P[12] * (tb + 0.05f * th + P[52] * wave + 0.12f * dc);
    float3 base = pal(tcol, PAL);
    const float bl = (0.62f + 0.16f * wave + 0.08f * (th - 0.5f));

    // ---- pulse rings: tiles light as a ring sweeps over their centre
    float ring = 0.f;
    #pragma unroll
    for (int k = 0; k < %(nring)d; ++k) {
        const float a = P[71 + 2 * k];
        if (a > 1e-3f) { const float d = (dc - P[70 + 2 * k]) / P[78]; ring += a * __expf(-d * d); }
    }
    const float tg = P[68] * bl * (1.f + 1.1f * ring);

    // ---- bevel: nearest edge (mitred), its inward normal, slope profile
    const float du0 = u * hgt, du1 = (1.f - u) * hgt, dv0 = v * hgt, dv1 = (1.f - v) * hgt;
    float dmin = du0, nx, ny;
    // inward normals: edge u = 0 (along e_s): perp to e_s towards e_r; edge v = 0 (along e_r): towards e_s
    const float ix_u = (erx - cth * esx) / hgt, iy_u = (ery - cth * esy) / hgt;
    const float ix_v = (esx - cth * erx) / hgt, iy_v = (esy - cth * ery) / hgt;
    nx = ix_u; ny = iy_u;
    if (du1 < dmin) { dmin = du1; nx = -ix_u; ny = -iy_u; }
    if (dv0 < dmin) { dmin = dv0; nx = ix_v; ny = iy_v; }
    if (dv1 < dmin) { dmin = dv1; nx = -ix_v; ny = -iy_v; }
    const float gw = P[62];
    const float bw = P[60];
    const float xb = clamp01((dmin - gw) / bw);
    const float slope = 2.f * (1.f - xb) / bw * P[61] * 0.15f;     // h = 1 - (1 - x)^2: steepest at the seam
    float Nx = -nx * slope, Ny = -ny * slope, Nz = 1.f;
    const float nl = rsqrtf(Nx * Nx + Ny * Ny + 1.f);
    Nx *= nl; Ny *= nl; Nz *= nl;
    const float Lx = P[63], Ly = P[64], Lz = P[65];
    const float diff = fmaxf(Nx * Lx + Ny * Ly + Nz * Lz, 0.f);
    float Hx = Lx, Hy = Ly, Hz = Lz + 1.f;
    const float hl = rsqrtf(Hx * Hx + Hy * Hy + Hz * Hz);
    const float nh = fmaxf((Nx * Hx + Ny * Hy + Nz * Hz) * hl, 0.f);
    const float spec = __powf(nh, 36.f) * (0.25f + 0.5f * P[61]);
    const float shade = 0.3f + 0.85f * diff / fmaxf(Lz, 0.3f) * 0.8f;
    const float tile_in = smooth(gw - aa, gw + aa, dmin);    // 0 in the grout, 1 on the tile

    float3 c;
    c.x = base.x * tg * shade + spec * 0.9f;
    c.y = base.y * tg * shade + spec * 0.88f;
    c.z = base.z * tg * shade + spec * 0.85f;

    // ---- inlays
    const float lw = P[57], hw = P[59];
    const float ig = P[67] * (1.f + 0.6f * ring);
    float3 add = make_float3(0.f, 0.f, 0.f);
    float cover = 0.f;
    float3 icol = make_float3(0.f, 0.f, 0.f);
    if (P[54] > 1e-3f) {
        const float A = P[56];
        const float par = T.isum - 2.f * floorf(T.isum * 0.5f);          // 1 odd, 0 even
        const float di = par > 0.5f ? A : 1.f - A;                       // d(i)
        float c1x, c1y, c2x, c2y, r1, r2;
        if (Q[11] > 0.5f) {               // acute corners V00, V11
            c1x = vx; c1y = vy; c2x = vx + erx + esx; c2y = vy + ery + esy;
            r1 = di; r2 = 1.f - (1.f - di);                              // 1 - d(i+1), d(i+1) = 1 - d(i)
        } else {                          // other corners V10, V01
            c1x = vx + erx; c1y = vy + ery; c2x = vx + esx; c2y = vy + esy;
            r1 = 1.f - di; r2 = r1;
        }
        const float d1x = px - c1x, d1y = py - c1y, d2x = px - c2x, d2y = py - c2y;
        const float a1 = fabsf(sqrtf(d1x * d1x + d1y * d1y) - r1), a2 = fabsf(sqrtf(d2x * d2x + d2y * d2y) - r2);
        const float da = fminf(a1, a2);
        const float rr = (a1 < a2) ? r1 : r2;
        const float kind = rr < 0.5f ? 0.f : 1.f;
        const float3 lc = lum_pal(P[13] + P[12] * (0.5f + 0.27f * kind + 0.1f * wave + 0.12f * dc) + 0.18f, PAL);
        const float core = 1.f - smooth(lw - aa, lw + aa, da);
        const float halo = __expf(-da / hw) * P[58];
        const float w = P[54];
        cover += w * core;
        icol.x += w * core * lc.x; icol.y += w * core * lc.y; icol.z += w * core * lc.z;
        add.x += w * halo * lc.x; add.y += w * halo * lc.y; add.z += w * halo * lc.z;
    }
    if (P[55] > 1e-3f) {
        const float d1 = fabsf(u - 0.5f) * hgt, d2 = fabsf(v - 0.5f) * hgt;   // ribbon r (u = 1/2), ribbon s
        const float dr = fminf(d1, d2);
        const float fam = (d1 < d2) ? (float)r : (float)s;
        const float3 lc = lum_pal(P[13] + P[12] * (fam / F + 0.1f * wave + 0.12f * dc) + 0.35f, PAL);
        const float core = 1.f - smooth(lw * 0.8f - aa, lw * 0.8f + aa, dr);
        const float halo = __expf(-dr / hw) * P[58];
        const float w = P[55];
        cover += w * core;
        icol.x += w * core * lc.x; icol.y += w * core * lc.y; icol.z += w * core * lc.z;
        add.x += w * halo * lc.x; add.y += w * halo * lc.y; add.z += w * halo * lc.z;
    }
    cover = clamp01(cover) * tile_in;
    // inlay: raised glossy strip (lit by the same shade), plus glow over tile and grout
    const float ih = ig * (0.85f + 0.3f * diff);
    c.x = c.x * (1.f - cover) + icol.x * tile_in * ih + 0.18f * cover * ig;
    c.y = c.y * (1.f - cover) + icol.y * tile_in * ih + 0.18f * cover * ig;
    c.z = c.z * (1.f - cover) + icol.z * tile_in * ih + 0.18f * cover * ig;

    // ---- grout: dark seam that glows faintly with energy
    const float3 sc = lum_pal(tcol + 0.5f, PAL);
    const float sg = P[69] * (1.f + 1.5f * ring);
    const float gb = 0.035f;
    c.x = c.x * tile_in + (1.f - tile_in) * (gb * base.x + sg * sc.x);
    c.y = c.y * tile_in + (1.f - tile_in) * (gb * base.y + sg * sc.y);
    c.z = c.z * tile_in + (1.f - tile_in) * (gb * base.z + sg * sc.z);
    // inlay halo (screen-ish so it never clips hard)
    const float hk = ig * 0.55f;
    c.x += (1.f - fminf(c.x, 1.f)) * add.x * hk;
    c.y += (1.f - fminf(c.y, 1.f)) * add.y * hk;
    c.z += (1.f - fminf(c.z, 1.f)) * add.z * hk;

    const float g = 255.f * P[66] * vg;
    out[y * W + x] = pack(make_float4(c.x * g, c.y * g, c.z * g, 255.f));
}
""" % {"fmax": FMAX, "pair0": PAIR0, "pstr": PSTR, "nring": NRING}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _order(d: dict, first: list[str]) -> dict:
    return {**{k: d[k] for k in first if k in d}, **{k: v for k, v in d.items() if k not in first}}


def _grid(nfold: str):
    """(F, angles, k_perp) for the n-fold choice."""
    if nfold == "8":
        return 4, [math.pi * j / 4 for j in range(4)], 3
    F = 7 if nfold == "7" else 5
    return F, [2 * math.pi * j / F for j in range(F)], 2


class PenroseTiling(Source):
    name = "penrose_tiling"
    label = "Penrose tiling"
    description = ("An endless Penrose rhombus tiling from de Bruijn's pentagrid: bevelled thick and thin rhombs "
                   "inlaid with arcs that join into circles, colour waves sweeping across, tiles flipping one by "
                   "one as the grid drifts.")
    params = _order({
        "decoration": num(0.0, 2.0, 1.0, "Decoration (none, arcs, ribbons)", tier="regional", basic=True),
        "scale": num(3.0, 24.0, 8.0, "Tiles across the height", step=0.1, tier="regional", basic=True),
        "bevel_depth": num(0.0, 1.0, 0.6, "Bevel depth", tier="regional"),
        "grout_width": num(0.0, 1.0, 0.35, "Grout width", tier="regional"),
        "colour_mode": num(0.0, 1.0, 0.3, "Colour (by tile type -> by orientation)", tier="regional"),
        "inlay_width": num(0.0, 1.0, 0.4, "Inlay width", tier="regional"),
        "arc_radius": num(0.2, 0.5, 0.382, "Arc radius", step=0.005, tier="regional"),
        "n_fold": enum(["5", "7", "8"], "5", "Symmetry (n-fold)", tier="structural"),
        **standard_scene_params(palette="dusk", render_scale=0.75, period=1.0, drift=0.02),
    }, ["decoration", "scale", "bevel_depth", "grout_width", "colour_mode", "inlay_width", "arc_radius", "n_fold",
        "energy", "pulse", "palette_period", "intensity", "speed", "drift_speed"])

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _OPTIONS)
        self.k_draw = mod.get_function("penrose_draw")
        self.Ph = np.zeros(PSIZE, np.float32)
        self.P = cp.zeros(PSIZE, cp.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.nfold = None
        self._g_rand = rng.uniform(-0.5, 0.5, FMAX)
        self._build_grid(str(params["n_fold"]))
        self.ph_phase = float(rng.uniform(0, 2 * math.pi))     # phason Lissajous
        self.pan_phase = float(rng.uniform(0, 2 * math.pi))
        self.rot = float(rng.uniform(0, 2 * math.pi))
        self.wave = 0.0
        self.wdir = float(rng.uniform(0, 2 * math.pi))
        self.drift = 0.0
        self.prev_pulse = 0.0
        self.refr = 0.0
        self.rings = [[0.0, 0.0] for _ in range(NRING)]
        self.es = None
        sp = self.session_palette()
        self.pal = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
                    else np.asarray(palette_coeffs(params["palette"]), np.float64))

    def _build_grid(self, nfold: str) -> None:
        if nfold == self.nfold:
            return
        self.nfold = nfold
        F, ang, kp = _grid(nfold)
        self.F = F
        self.e = np.array([[math.cos(a), math.sin(a)] for a in ang])
        self.eperp = np.array([[math.cos(kp * a), math.sin(kp * a)] for a in ang])
        g0 = self._g_rand[:F].copy()
        if F % 2 == 1:
            g0 -= g0.mean()                    # sum g = 0: the Penrose (regular) class for F = 5
        self.g0 = g0
        pairs = []
        for r in range(F):
            for s in range(r + 1, F):
                M = np.array([[self.e[r, 0], self.e[s, 0]], [self.e[r, 1], self.e[s, 1]]])   # (a, b) -> p
                A = np.array([self.e[r], self.e[s]])                                           # z -> (<z,e_r>, <z,e_s>)
                Mi, Ai = np.linalg.inv(M), np.linalg.inv(A)
                d = s - r
                typ = min(d, F - d)
                acute = 1.0 if float(self.e[r] @ self.e[s]) > 1e-6 else 0.0
                pairs.append([r, s, Mi[0, 0], Mi[0, 1], Mi[1, 0], Mi[1, 1], Ai[0, 0], Ai[0, 1], Ai[1, 0], Ai[1, 1],
                              typ - 1, acute])
        self.pairs = np.array(pairs, np.float64)
        self.ntypes = max(int(self.pairs[:, 10].max()), 0)

    def close(self) -> None:
        self.buf = None
        self.P = None

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(dt, 0.0), 0.5)
        self._build_grid(str(p["n_fold"]))
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        a = 1.0 - math.exp(-dt / 1.2) if dt > 0 else 0.0
        self.es = e if self.es is None else self.es + (e - self.es) * a
        es = self.es

        # palette (session palette wins, eased) and drift
        sp = self.session_palette()
        tp = (np.asarray(sp, np.float64).reshape(-1)[:12] if sp is not None
              else np.asarray(palette_coeffs(p["palette"]), np.float64))
        if dt > 0:
            self.pal += (tp - self.pal) * (1.0 - math.exp(-dt / self.ease_tau_s))
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # host-integrated phases (energy on different curves; pulse kicks rates only)
        self.ph_phase = _wrap(self.ph_phase + (0.02 + 0.14 * es ** 1.3) * spd * dt, 2000 * math.pi)
        self.pan_phase = _wrap(self.pan_phase + (0.0035 + 0.009 * es) * spd * dt, 2000 * math.pi)
        self.rot = _wrap(self.rot + (0.006 + 0.016 * es) * spd * dt, 2 * math.pi)
        self.wave = _wrap(self.wave + 2 * math.pi * (0.05 + 0.22 * es) * (1.0 + 0.8 * pulse) * spd * dt, 2 * math.pi)
        self.wdir = _wrap(self.wdir + 0.013 * spd * dt, 2 * math.pi)

        # pulse rings: spawn on a rising edge (refractory 120 ms), expand, fade
        self.refr = max(self.refr - dt, 0.0)
        if pulse - self.prev_pulse > 0.15 and self.refr <= 0.0:
            k = min(range(NRING), key=lambda i: self.rings[i][1])
            self.rings[k] = [0.0, 0.45 + 0.55 * pulse]
            self.refr = 0.12
        self.prev_pulse = pulse
        rs_speed = (0.9 + 0.6 * es) * max(spd, 0.25)
        for rg in self.rings:
            if rg[1] > 0.0:
                rg[0] += rs_speed * dt
                rg[1] *= math.exp(-dt / 1.3)
                if rg[0] > 3.0 or rg[1] < 0.01:
                    rg[0], rg[1] = 0.0, 0.0

        F = self.F
        P = self.Ph
        P[:] = 0.0
        P[0:12] = self.pal
        P[12] = float(p["palette_period"])
        P[13] = self.drift
        P[14] = F
        P[15] = len(self.pairs)
        P[16:16 + F] = self.e[:, 0]
        P[23:23 + F] = self.e[:, 1]
        # phason: offsets wander in perpendicular space on a slow Lissajous (no net translation)
        ph = self.ph_phase
        amp = 0.55
        phi = np.array([amp * math.sin(ph), amp * math.sin(0.618 * ph + 1.3)])
        g = self.g0 + self.eperp @ phi
        P[30:30 + F] = g
        G = ((g + 0.5)[:, None] * self.e).sum(0)
        P[37], P[38] = G[0], G[1]
        P[39] = 2.0 / F
        # view: rotation, scale, Lissajous pan through the infinite tiling (bounded, never repeats)
        P[40], P[41] = math.cos(self.rot), math.sin(self.rot)
        units_per_h = float(p["scale"])
        S = units_per_h / self.h
        P[42] = S
        P[43] = 40.0 * math.sin(self.pan_phase)
        P[44] = 28.0 * math.sin(0.7071 * self.pan_phase + 0.9)
        P[45] = 1.0 / (S * 0.5 * self.h)
        # colour wave (tiling space direction, rotates slowly)
        wd = self.wdir
        P[48] = self.wave
        P[49], P[50] = math.cos(wd), math.sin(wd)
        P[51] = 1.0 / (2.5 + 0.35 * units_per_h)
        P[52] = 0.1 + 0.12 * es
        P[53] = min(max(float(p["colour_mode"]), 0.0), 1.0)
        dec = float(p["decoration"])
        P[54] = _ss(0.1, 0.85, dec) * (1.0 - _ss(1.15, 1.9, dec))
        P[55] = _ss(1.1, 1.9, dec)
        P[56] = float(p["arc_radius"])
        px_unit = S                                           # tiling units per internal pixel
        P[57] = 0.012 + 0.05 * float(p["inlay_width"]) + 0.6 * px_unit
        glow = 0.4 + 0.6 * es
        P[58] = 0.35 * glow
        P[59] = 0.03 + 0.05 * glow
        P[60] = 0.05 + 0.13 * float(p["bevel_depth"])
        P[61] = float(p["bevel_depth"])
        P[62] = 0.004 + 0.045 * float(p["grout_width"]) + 0.5 * px_unit
        # light from the upper left of the screen, expressed in tiling space
        lx, ly, lz = -0.5, -0.62, 0.6
        n = math.sqrt(lx * lx + ly * ly + lz * lz)
        lx, ly, lz = lx / n, ly / n, lz / n
        c, s = P[40], P[41]
        P[63], P[64], P[65] = c * lx - s * ly, s * lx + c * ly, lz
        P[66] = float(p["intensity"])
        P[67] = (0.85 + 0.25 * es) * (1.0 + 0.7 * pulse)
        P[68] = 1.0 + 0.12 * pulse
        P[69] = 0.03 + 0.16 * es ** 1.2
        for i, (rad, am) in enumerate(self.rings):
            P[70 + 2 * i] = rad
            P[71 + 2 * i] = am
        P[78] = 0.16
        P[79] = self.ntypes
        P[PAIR0:PAIR0 + PSTR * len(self.pairs)] = self.pairs.reshape(-1)
        self.P.set(P)

        launch_2d(self.k_draw, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P))
        present(self.ctx, self.buf, out)
