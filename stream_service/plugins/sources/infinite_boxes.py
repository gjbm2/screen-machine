"""Infinite boxes: a zoom-invariant grid of boxes that zooms in forever while boxes keep splitting.

Source: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/infinite-boxes/fragment-0.hlsl
        (Shadertoy https://www.shadertoy.com/view/dtXGWj, "Infinite boxes" by mrange)
Author: Mårten Rånge (mrange); box SDF by Inigo Quilez; mod1 idea from Mercury hg_sdf
License: MIT AND CC0-1.0
license_class: permissive
Ported from HLSL by smfx (ideas/changes: see below)

Licence record (checked 2026-10-14):
  * The gallery's metadata.json for infinite-boxes lists "license-expressions": ["CC0-1.0", "CC-BY-NC-4.0", "MIT"],
    authors ["Mårten Rånge", "Inigo Quilez", "mercury"].  mrange's own code is CC0-1.0; the repository LICENSE is MIT
    ("MIT License, Copyright (c) 2022 Mårten Rånge", https://github.com/mrange/windows-terminal-shader-gallery/blob/main/LICENSE).
  * box(): "License: MIT, author: Inigo Quilez, found: https://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm".
    Used here as MIT:  Copyright (c) Inigo Quilez.  Permission is hereby granted, free of charge, to any person
    obtaining a copy of this software ... (standard MIT terms; THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY).
  * mod1(): "License: MIT OR CC-BY-NC-4.0, author: mercury, found: https://mercury.sexy/hg_sdf/".  Taken under the
    MIT option; in any case rewritten here from scratch (rep1).
  * hash() and tanh_approx() are marked "License: Unknown" in the original: NOT used.  The cell hash is the lab's
    integer hash1 (smfx/_lib/cuda.py) and tanh is CUDA's tanhf.

The original: space is cut into shells along each axis whose widths grow geometrically (x 1.2 per level), so each
quadrant is a grid of rectangles that grow away from the axes; each rectangle is cut along its long side into
near-square boxes; the whole thing is self-similar under a 1.2x zoom, so zooming by fract(t) and offsetting the
cell ids by floor(t) gives a seamless endless zoom.  Colour = cosine palette of (-radius + cell hash) with drift,
a soft glow at the vanishing point, a vignette and a sqrt "gamma".

smfx additions: Mondrian-style recursive splits (up to 6 levels; a box splits once it is big enough on screen, with
split_probability; one child keeps the parent's colour so a split reads as a new box carving out, and every split
line fades in, so nothing pops); line width, outline <-> solid fill blend with "paper" cells, corner rounding,
grid rotation and radial twist; resolution-aware anti-aliasing (sub-pixel boxes near the axes fade to their mean).

Macros: energy -> zoom rate, split density (probability and size threshold, slewed), fraction of lit (coloured)
cells, line glow / halo, vanishing-point glow, rotation rate.  pulse -> a host beat counter (rising edges with
hysteresis) picks a fresh ~20-35 % of cells each beat that flash with the envelope (flash_kick), plus a kick to the
zoom rate (zoom_kick).  speed scales every rate; palette indexes cell colour (palette_period = colour spread per
cell id); intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present

# P layout (floats)
#  0..11 palette | 12 period | 13 drift | 14 zoom fract m | 15 angle (rad) | 16 twist (rad per unit radius)
#  17 split probability (eff) | 18 split size threshold S0 (screen units) | 19 line half-width (screen units)
#  20 corner round | 21 fill mix | 22 lit fraction | 23 halo strength | 24 halo width | 25 line brightness
#  26 flash amp (this beat's set) | 27 flash amp (previous beat's set) | 28 flash fraction | 29 flash fraction (prev)
#  30 centre glow | 31 gain | 32 px (screen units per internal pixel) | 33 log2(cell ratio)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define LMAX 6

// shell boundaries: b^n with log2(b) = eb (b = 1.2 in the original)
__device__ __forceinline__ float fwd(float n, float eb) { return exp2f(eb * n); }
__device__ __forceinline__ float rev(float v, float eb) { return log2f(v) / eb; }
__device__ __forceinline__ float3 mix3(float3 a, float3 b, float t) {
    return make_float3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}
// 2D box distance with corner radius r (Inigo Quilez, MIT), r <= min(bx, by)
__device__ __forceinline__ float rbox(float px, float py, float bx, float by, float r) {
    const float dx = fabsf(px) - bx + r, dy = fabsf(py) - by + r;
    const float ox = fmaxf(dx, 0.f), oy = fmaxf(dy, 0.f);
    return sqrtf(ox * ox + oy * oy) + fminf(fmaxf(dx, dy), 0.f) - r;
}
// Repeat v with period s, cells centred on 0; returns the cell index (rewrite of the hg_sdf pMod1 idea)
__device__ __forceinline__ float rep1(float& v, float s) {
    const float c = floorf(v / s + 0.5f);
    v -= s * c;
    return c;
}

// Look of one box: fill (incl. inner halo) and line colours (linear-ish: squared palette) and gap mask g (1 = line).
__device__ __forceinline__ void box_look(float lx, float ly, float hx, float hy, unsigned int id, float z,   // z: screen units per local unit
                                         float qx, float qy, float res, const float* __restrict__ P, unsigned int seed,
                                         unsigned int beat, float& g, float3& fill, float3& linec)
{
    const float px = P[32];
    const float mh = fminf(hx, hy) * z;                          // box half-size on screen
    const float lw = fminf(P[19], 0.3f * mh);                    // line half-width, never eats a small box
    const float bx = hx - lw / z, by = hy - lw / z;
    const float r = P[20] * fminf(bx, by);
    const float d = rbox(lx, ly, bx, by, r) * z;                 // screen units
    const float gt = 1.f - clamp01(0.5f - d / px);
    const float gavg = clamp01(lw / fmaxf(mh, 1e-6f));           // mean coverage once the box is sub-pixel
    g = gavg + (gt - gavg) * res;

    const unsigned int hc = hash_u(id ^ 0xC01DC0DEu);
    const float hv = 0.5f + ((hc >> 12) * (1.f / 1048575.f) - 0.5f) * res;
    const float cx = qx - lx * z, cy = qy - ly * z;              // box centre (screen units): flat fills
    const float t = (0.8f * hv - 0.45f * sqrtf(cx * cx + cy * cy)) * P[12] + P[13];
    float3 C = pal(t, P);
    C = make_float3(C.x * C.x, C.y * C.y, C.z * C.z);
    const float hl = (hc & 4095u) * (1.f / 4095.f);
    const float lit = P[22] + (smooth(hl - 0.08f, hl + 0.08f, P[22]) - P[22]) * res;
    float fl = 0.f;
    if (P[26] > 0.f && hash1(id, beat, seed ^ 0xF1A5u) < P[28]) fl = P[26];
    if (P[27] > 0.f && hash1(id, beat - 1u, seed ^ 0xF1A5u) < P[29]) fl = fmaxf(fl, P[27]);
    fl *= res;
    const float F = clamp01(2.f * P[21] - 1.f + lit);            // fill: 0 outline, 1 solid (lit cells fill first)
    const float lum = 0.2126f * C.x + 0.7152f * C.y + 0.0722f * C.z;
    // unlit "paper": a near-neutral grey with a hint of the cell colour, its shade varying per cell
    const float pk = 0.6f + 0.8f * ((hash_u(hc) & 255u) * (1.f / 255.f) - 0.5f) * res;
    const float3 paper = make_float3(pk * (0.04f + 0.36f * (0.15f * C.x + 0.85f * lum)),
                                     pk * (0.04f + 0.36f * (0.15f * C.y + 0.85f * lum)),
                                     pk * (0.04f + 0.36f * (0.15f * C.z + 0.85f * lum)));
    float3 in = mix3(paper, C, lit);
    in = make_float3(in.x * F, in.y * F, in.z * F);
    const float lb = P[25] * (0.75f + 0.5f * lit);
    const float3 lo = make_float3(C.x * lb, C.y * lb, C.z * lb);
    const float hw = fminf(P[24], 0.6f * mh) + 1e-6f;
    const float halo = P[23] * (1.f - F) * (1.f - F) * res * __expf(-fmaxf(-d, 0.f) / hw);   // glow inside outlined boxes
    in = make_float3(in.x + lo.x * halo, in.y + lo.y * halo, in.z + lo.z * halo);
    fill = mix3(in, make_float3(1.5f * C.x + 0.18f, 1.5f * C.y + 0.18f, 1.5f * C.z + 0.18f), fl);   // pastel pop
    linec = mix3(lo, make_float3(0.003f, 0.003f, 0.003f), F);
}

extern "C" __global__
void infinite_boxes(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                    const unsigned int seed, const unsigned int zf, const unsigned int beat)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    float2 p = make_float2((x + 0.5f - 0.5f * W) / hh, -(y + 0.5f - hh) / hh);   // y up, height spans [-1, 1]
    const float rr = sqrtf(p.x * p.x + p.y * p.y);
    {   // grid rotation + radial twist
        const float a = P[15] + P[16] * rr;
        const float c = cosf(a), s = sinf(a);
        p = make_float2(c * p.x - s * p.y, s * p.x + c * p.y);
    }
    const float px = P[32];
    const float eb = P[33];
    const float z = fwd(P[14], eb);                              // zoom within the current level, 1 .. b
    const float p0x = p.x / z, p0y = p.y / z;
    const unsigned int quad = (p0x < 0.f ? 1u : 0u) + (p0y < 0.f ? 2u : 0u);
    const float ax = fmaxf(fabsf(p0x), 1e-7f), ay = fmaxf(fabsf(p0y), 1e-7f);
    const float qx = fabsf(p.x), qy = fabsf(p.y);               // |p| in the rotated frame (screen units)
    // geometric shells along each axis: shell n spans (b^(n-1), b^n]
    const float nx = ceilf(rev(ax, eb)), ny = ceilf(rev(ay, eb));
    const float ex1 = fwd(nx, eb), ex0 = fwd(nx - 1.f, eb), ey1 = fwd(ny, eb), ey0 = fwd(ny - 1.f, eb);
    float lx = ax - 0.5f * (ex1 + ex0), ly = ay - 0.5f * (ey1 + ey0);
    // Work in units of the short shell's half-width: every box shape (and every split decision below) then comes
    // from integers only, bit-identical before and after the per-level zoom wrap (no pops at the wrap).
    const float sh = fminf(ex1 - ex0, ey1 - ey0) * 0.5f;
    const float zs = sh * z;                                     // screen units per local unit
    lx /= sh; ly /= sh;
    const float R = fwd(fabsf(nx - ny), eb);                     // long / short shell width
    const float f2 = floorf(R);                                  // cut the long side into f2 near-square boxes
    float hx = 1.f, hy = 1.f, n2 = 0.f;
    if (nx < ny) {
        hy = R / f2;
        if (fmodf(f2, 2.f) < 0.5f) ly += hy;                     // even count: align the tiling to the edges
        n2 = rep1(ly, 2.f * hy);
    } else if (nx > ny) {
        hx = R / f2;
        if (fmodf(f2, 2.f) < 0.5f) lx += hx;
        n2 = rep1(lx, 2.f * hx);
    }
    // cell id: shell indices minus the zoom level (seamless across the per-level wrap), sub-box, quadrant
    const unsigned int ix = (unsigned int)(int)nx - zf, iy = (unsigned int)(int)ny - zf;
    unsigned int id = hash_u(ix * 0x9E3779B1u ^ hash_u(iy * 0x85EBCA77u ^ hash_u((unsigned int)(int)n2 * 0xC2B2AE3Du
                             ^ hash_u(quad + 4u * seed))));
    const float res = smooth(1.0f * px, 3.5f * px, zs);         // base boxes near the axes go sub-pixel

    // Mondrian splits: descend while a box is big enough on screen (and wins its split hash); A fades each split
    // line in.  final = sum_k look_k * A_k * prod_{j>k} (1 - A_j); only the last three levels are kept (a sliding
    // window in registers; anything above the window is folded into its top slot, exact whenever that slot is fully
    // split, which is nearly always), so each pixel shades at most three boxes.
    float x0 = lx, y0 = ly, w0 = hx, v0 = hy; unsigned int i0 = id;               // slot 0 (oldest)
    float x1 = 0.f, y1 = 0.f, w1 = 1.f, v1 = 1.f, a1 = 0.f; unsigned int i1 = 0u;  // slot 1
    float x2 = 0.f, y2 = 0.f, w2 = 1.f, v2 = 1.f, a2 = 0.f; unsigned int i2 = 0u;  // slot 2 (deepest)
    int L = 0;
    const float S0 = P[18], prob = P[17];
    float a = 1.f;
    #pragma unroll
    for (int k = 1; k <= LMAX; ++k) {
        const float mh = fminf(hx, hy) * zs;
        const unsigned int h = hash_u(id ^ hash_u((unsigned int)k * 0x68E31DA4u));
        const float hk = (h & 1023u) * (1.f / 1023.f);
        a *= smooth(hk - 0.06f, hk + 0.06f, prob) * smooth(S0, 1.8f * S0, mh);
        if (a < 2e-3f) break;
        const float ha = ((h >> 10) & 255u) * (1.f / 255.f);
        const float hp = ((h >> 18) & 127u) * (1.f / 127.f);
        const float hq = ((h >> 25) & 127u) * (1.f / 127.f);
        const float rx = hx / hy;
        const bool spx = rx > 1.3f || (rx > 0.77f && ha < 0.667f);
        const bool spy = rx < 0.77f || (rx <= 1.3f && ha > 0.333f);
        unsigned int side = 0u;
        if (spx) {
            const float c = hx * (hp - 0.5f);                    // cut at 25..75 % of the width
            if (lx < c) { lx -= 0.5f * (c - hx); hx = 0.5f * (c + hx); }
            else { lx -= 0.5f * (c + hx); hx = 0.5f * (hx - c); side |= 1u; }
        }
        if (spy) {
            const float c = hy * (hq - 0.5f);
            if (ly < c) { ly -= 0.5f * (c - hy); hy = 0.5f * (c + hy); }
            else { ly -= 0.5f * (c + hy); hy = 0.5f * (hy - c); side |= 2u; }
        }
        // child 0 keeps the parent's id (and colour): the split reads as new boxes carving out of it
        if (side) id = hash_u(id ^ hash_u(side + 4u * (unsigned int)k + 0x3C6EF372u));
        if (k >= 3) {                                            // window full: shift up one level
            x0 = x1; y0 = y1; w0 = w1; v0 = v1; i0 = i1;
            x1 = x2; y1 = y2; w1 = w2; v1 = v2; a1 = a2; i1 = i2;
        }
        if (k == 1) { x1 = lx; y1 = ly; w1 = hx; v1 = hy; a1 = a; i1 = id; }
        else { x2 = lx; y2 = ly; w2 = hx; v2 = hy; a2 = a; i2 = id; }
        L = k;
    }
    // weights: deepest first; whatever is left goes to slot 0
    const float wt2 = a2, wt1 = a1 * (1.f - a2), wt0 = 1.f - wt2 - wt1;
    float g, gc;
    float3 fill, linec, fc, lc;
    box_look(x0, y0, w0, v0, i0, zs, qx, qy, L <= 2 ? res : 1.f, P, seed, beat, g, fill, linec);
    if (wt1 > 1e-3f) {
        box_look(x1, y1, w1, v1, i1, zs, qx, qy, 1.f, P, seed, beat, gc, fc, lc);
        g = g * wt0 + gc * wt1;
        fill = make_float3(fill.x * wt0 + fc.x * wt1, fill.y * wt0 + fc.y * wt1, fill.z * wt0 + fc.z * wt1);
        linec = make_float3(linec.x * wt0 + lc.x * wt1, linec.y * wt0 + lc.y * wt1, linec.z * wt0 + lc.z * wt1);
    } else {
        g *= wt0 + wt1; fill = make_float3(fill.x * (wt0 + wt1), fill.y * (wt0 + wt1), fill.z * (wt0 + wt1));
        linec = make_float3(linec.x * (wt0 + wt1), linec.y * (wt0 + wt1), linec.z * (wt0 + wt1));
    }
    if (wt2 > 1e-3f) {
        box_look(x2, y2, w2, v2, i2, zs, qx, qy, 1.f, P, seed, beat, gc, fc, lc);
        g += gc * wt2;
        fill = make_float3(fill.x + fc.x * wt2, fill.y + fc.y * wt2, fill.z + fc.z * wt2);
        linec = make_float3(linec.x + lc.x * wt2, linec.y + lc.y * wt2, linec.z + lc.z * wt2);
    }

    float3 col = mix3(fill, linec, g);
    const float seam = 0.3f + 0.7f * res;                        // unresolved boxes along the axes: a dim seam
    col = make_float3(col.x * seam, col.y * seam, col.z * seam);
    {   // vanishing-point glow (as the original: palette colour, falling off with tanh(2 sqrt(r)))
        const float3 Cc = pal((0.4f - 0.45f * rr) * P[12] + P[13], P);
        const float k = P[30] * (1.f - tanhf(2.f * sqrtf(rr)));
        col.x += k * Cc.x; col.y += k * Cc.y; col.z += k * Cc.z;
    }
    const float vx = (x + 0.5f) / W * 2.f - 1.f, vy = (y + 0.5f) / H * 2.f - 1.f;
    const float v = 1.f - smooth(0.5f, 1.5f, sqrtf(vx * vx + vy * vy));
    const float gn = 255.f * P[31];
    out[y * W + x] = pack(make_float4(sqrtf(fmaxf(col.x * v, 0.f)) * gn, sqrtf(fmaxf(col.y * v, 0.f)) * gn,
                                      sqrtf(fmaxf(col.z * v, 0.f)) * gn, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


class InfiniteBoxes(Source):
    name = "infinite_boxes"
    label = "Infinite boxes"
    description = "A Mondrian-like grid zooms in forever while its boxes keep splitting into smaller boxes."
    params = {
        "split_probability": num(0.0, 1.0, 0.55, "Box splitting", basic=True),
        "fill_mix": num(0.0, 1.0, 0.8, "Fill (outlines -> solid)", basic=True),
        "line_width": num(0.5, 12.0, 4.0, "Line width", unit="px@1080p", step=0.1),
        "corner_round": num(0.0, 1.0, 0.0, "Corner rounding"),
        "twist": num(0.0, 1.0, 0.0, "Twist"),
        "grid_rotation": num(-45.0, 45.0, 0.0, "Grid rotation", unit="°", step=0.5),
        "flash_kick": num(0.0, 1.5, 0.8, "Beat cell flash"),
        "zoom_kick": num(0.0, 4.0, 2.0, "Beat zoom kick"),
        "zoom_speed": num(-1.0, 1.0, 0.13, "Zoom speed", unit="oct/s", step=0.005, tier="realtime", bind="rate"),
        "rotate_speed": num(-30.0, 30.0, 2.0, "Rotation speed", unit="°/s", step=0.5, tier="realtime", bind="rate"),
        "cell_ratio": num(1.15, 1.6, 1.3, "Cell growth ratio (box size)", step=0.01, tier="structural"),
        **standard_scene_params(palette="rainbow", render_scale=0.75, period=0.7, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, ("--std=c++14", "--use_fast_math")).get_function("infinite_boxes")
        self.P = cp.zeros(40, cp.float32)
        self.Ph = np.zeros(40, np.float32)
        self.zm = 0.0                 # zoom phase within the level, [0, 1)
        self.zf = 0                   # whole zoom levels (uint32 wrap is seamless: ids use n - zf mod 2^32)
        self.rot = 0.0
        self.drift = 0.0
        self.es = None                # slewed energy' for density-type targets
        self.beat = 0
        self.armed = True
        self.since = 1.0
        self.amp_last = 0.0
        self.amp_old = 0.0
        self.ffrac = 0.3
        self.ffrac_old = 0.3

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        e1 = _smoothstep(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.5) if dt > 0 else 1.0
        self.es = e1 if self.es is None else self.es + (e1 - self.es) * a
        es = self.es
        k = pulse * (0.3 + 0.7 * e)

        # beat counter: rising edge with hysteresis (> 0.6 after < 0.3, >= 150 ms apart)
        self.since += dt
        self.amp_old *= math.exp(-dt / 0.12) if dt > 0 else 1.0
        if self.armed and pulse > 0.6 and self.since >= 0.15:
            self.beat = (self.beat + 1) & 0xFFFFFFFF
            self.amp_old, self.ffrac_old = self.amp_last, self.ffrac
            self.ffrac = 0.18 + 0.17 * e1
            self.armed = False
            self.since = 0.0
        if pulse < 0.3:
            self.armed = True
        amp = min(1.0, float(p["flash_kick"]) * k ** 0.7)
        self.amp_last = amp

        # rates (kicks add to the zoom rate only)
        eb = math.log2(float(p["cell_ratio"]))
        zr = float(p["zoom_speed"]) / eb * (0.4 + 1.2 * e1) * (1.0 + float(p["zoom_kick"]) * k)   # levels/s
        self.zm += zr * spd * dt
        whole = math.floor(self.zm)
        if whole:
            self.zm -= whole
            self.zf = (self.zf + int(whole)) & 0xFFFFFFFF
        self.rot = _wrap(self.rot + math.radians(float(p["rotate_speed"])) * (0.4 + 1.2 * e1) * spd * dt,
                         2.0 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 2.0)   # every palette c is 0.5/1/2

        Ph = self.Ph
        Ph[0:12] = palette_coeffs(p["palette"])
        Ph[12] = p["palette_period"]
        Ph[13] = self.drift
        Ph[14] = self.zm
        Ph[15] = math.radians(float(p["grid_rotation"])) + self.rot
        Ph[16] = 1.1 * float(p["twist"])
        Ph[17] = min(0.98, float(p["split_probability"]) * (0.65 + 0.6 * es ** 1.5))
        Ph[18] = max(0.022 * (1.2 - 0.6 * es ** 1.5), 12.0 / self.h)   # >= 5 internal px
        Ph[19] = float(p["line_width"]) / 1080.0
        Ph[20] = p["corner_round"]
        Ph[21] = p["fill_mix"]
        Ph[22] = 0.3 + 0.6 * es
        Ph[23] = 0.3 + 0.7 * _smoothstep(0.0, 1.0, e1)
        Ph[24] = 0.006 + 0.014 * e1
        Ph[25] = 0.8 + 0.6 * e1
        Ph[26] = amp
        Ph[27] = self.amp_old
        Ph[28] = self.ffrac
        Ph[29] = self.ffrac_old
        Ph[30] = 0.55 + 0.6 * e1
        Ph[31] = p["intensity"]
        Ph[32] = 2.0 / self.h
        Ph[33] = eb
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P,
                                           np.uint32(int(p["seed"]) & 0xFFFFFFFF), np.uint32(self.zf),
                                           np.uint32(self.beat)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
