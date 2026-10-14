"""Plexus: a drifting constellation / neural network in parallax depth layers that slowly zoom past.

Source: Original (inspired by https://www.shadertoy.com/view/lscczl and https://www.youtube.com/watch?v=3CycKKJiwis)
Author: smfx (original; inspired by Martijn Steinrucken / BigWings, 'The Universe Within')
License: MIT
license_class: permissive

No code was copied or ported.  The inspiration shader (Shadertoy lscczl) states no licence that we could verify
(Shadertoy's default would be CC-BY-NC-SA-3.0), so only the *idea* is reused: nodes jittered on a grid, each joined
to its 8 neighbours by lines that fade with length, several such layers at different depths zooming past.
Everything here (layer/depth model, node motion, beat-fired signals running along the lines, flares, tone mapping)
is written from scratch for smfx.

How it works.  Layer n of an endless stack sits at depth u = n - zoom (u = 0 at the camera, larger = further); the
host integrates ``zoom`` so layers glide towards the viewer, scale up (cells per height = density * R^(u - 1)),
rotate a little and fade in at the far end of the visible range (``layers``, continuous) and out as they pass the
camera.  Per pixel and per visible layer, the kernel finds its grid cell, builds the 3x3 neighbouring nodes
(hash-jittered home position + a Lissajous orbit) and tests 12 segments: the centre node to its 8 neighbours plus
the 4 diagonals between the side neighbours (with node jitter <= 0.38 cell this covers every line that can cross
the cell).  Lines fade with length (``connection_reach``) and brighten when two nodes pass close.  Light is
accumulated in linear space, tinted with the shared cosine palette (index = layer * period + drift, plus a small
per-node spread blended along each line), tone-mapped and written as sRGB.

Macros.  energy (e' = smoothstep(0.1, 0.85, e)): node orbit rate and radius, layer zoom / rotation / sway rates,
twinkle rate, line and node brightness, connection reach (+20%, slewed) and the share of nodes that fire on a beat
(e'^1.5, slewed).  pulse (k = pulse * (0.3 + 0.7 e)): a zoom-rate kick (``zoom_kick``), a node gain flash (<= +35%)
and line flash (<= +20%) (``flash_kick``), and on each beat onset (host rising-edge detector) a random subset of
nodes fires: they flare and send bright signals running out along their lines (the spawn owns its lifetime).
speed scales every phase; palette / palette_period / drift_speed colour the layers; intensity is output gain.
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

NSLOT = 5            # layer slots evaluated per pixel: u in (0, 5], enough for ``layers`` <= 5
SLOT = 16            # floats per slot
GOFF = NSLOT * SLOT  # global params start here
R_DEPTH = 1.7        # scale ratio between adjacent layers
LAT = 4096           # node lattice period (cells): offsets wrap by this, invisibly
TAU = 2.0 * math.pi
ORB_WRAP = 4.0 * TAU  # orbit frequencies are k/4 (integer k): sin(f * ph) is periodic in 8 pi -> wrap at 8 pi
FIRE_TAU = 0.42      # s: lifetime constant of a beat-fired node
SIG_SPEED = 2.6      # cells/s: how fast a fired signal runs along a line

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NSLOT %(nslot)d
#define SLOT %(slot)d
#define GOFF %(goff)d
#define LATM %(latm)du

__device__ __forceinline__ float sq(float x) { return x * x; }
__device__ __forceinline__ float u16(unsigned int h) { return (float)(h & 0xffffu) * (1.f / 65536.f); }
__device__ __forceinline__ float3 lin3(float3 c) { return make_float3(c.x * c.x, c.y * c.y, c.z * c.z); }

// G layout (global, from GOFF): 0..11 palette | 12 reach | 13 line gain | 14 node gain | 15 sparkle | 16 twinkle ph
// 17 orbit ph | 18 orbit radius | 19 home jitter | 20 fire fraction | 21 beat id (cur) | 22 fire amp (cur)
// 23 fire age (cur) | 24 beat id (prev) | 25 fire amp (prev) | 26 fire age (prev) | 27 signal speed
// 28 intensity | 29 background level | 30 drift | 31 bg palette index
// Slot k (16 floats): 0 weight | 1 cos*cells | 2 sin*cells | 3 off x | 4 off y | 5 layer id | 6 palette index
// 7 cell units per px | 8 line half width | 9 node radius | 10 flare length | 11 u | 12 table cx0 | 13 table cy0
// 14 table width | 15 table height

// Pass 1: one thread per node of each visible layer's table (the cells covering the screen + a 2-cell margin).
// NA = (x, y in own cell, fire code, size); fire code = 0 (idle) or round(255 amp) + signal position / 4
// NB = (coloured gain, white gain, flare amp, -)
extern "C" __global__
void plexus_nodes(float4* __restrict__ NA, float4* __restrict__ NB, const float* __restrict__ P, const int side,
                  const int tmax, const unsigned int seed)
{
    const int tx = blockIdx.x * blockDim.x + threadIdx.x, yy = blockIdx.y * blockDim.y + threadIdx.y;
    const int k = yy / side, ty = yy - k * side;
    if (k >= NSLOT) return;
    const float* S = P + k * SLOT;
    const int tw = (int)S[14], th = (int)S[15];
    if (S[0] <= 1e-3f || tx >= tw || ty >= th) return;
    const float* G = P + GOFF;
    const int cx = (int)S[12] + tx, cy = (int)S[13] + ty;
    const unsigned int lseed = hash_u((unsigned int)S[5] * 0x9E3779B1u ^ seed);
    const unsigned int ix = (unsigned int)cx & LATM, iy = (unsigned int)cy & LATM;
    const unsigned int h0 = hash_u(ix * 0x8da6b343u ^ hash_u(iy * 0xd8163841u ^ lseed));
    const unsigned int h1 = hash_u(h0 ^ 0x9e3779b9u);
    const unsigned int h2 = hash_u(h1 + 0x632be5abu);
    // orbit frequencies k/4 (integer k, either sign): periodic in the host's 8 pi wrap of the orbit phase
    const float fxo = (float)(2 + (h2 & 3u)) * 0.25f * ((h2 & 4u) ? -1.f : 1.f);
    const float fyo = (float)(2 + ((h2 >> 3) & 3u)) * 0.25f * ((h2 & 32u) ? -1.f : 1.f);
    const float jit = G[19], orbr = G[18], orbph = G[17];
    const float px = 0.5f + jit * (2.f * u16(h0) - 1.f) + orbr * __sinf(orbph * fxo + 6.2831853f * u16(h1));
    const float py = 0.5f + jit * (2.f * u16(h0 >> 16) - 1.f) + orbr * __cosf(orbph * fyo + 6.2831853f * u16(h1 >> 16));
    const float rs = u16(h2 >> 8);
    const float size = 0.55f + 1.25f * rs * rs * rs;                    // a few bright hub nodes
    // beat firing: a fresh random subset per beat (two overlapping generations, each owning its lifetime)
    const float ffrac = G[20];
    const float fcur = hash1(h0, (unsigned int)G[21], 77u) < ffrac ? G[22] : 0.f;
    const float fprv = hash1(h0, (unsigned int)G[24], 77u) < ffrac ? G[25] : 0.f;
    const bool usec = fcur >= fprv;
    const float famp = usec ? fcur : fprv;
    const float fage = usec ? G[23] : G[26];
    const float nf = famp * __expf(-fage * 3.0f);                         // node flash decays faster than signals
    // signal code only while the comet is still on a line (pos < max reach), bright enough to see, and on the
    // nearer layers (u < 2.6) where it is big enough to read
    const float spos = fage * G[27];
    const float ai = rintf(famp * 255.f);
    const float code = (ai >= 6.f && spos < G[12] + 0.2f && S[11] < 2.6f) ? ai + fminf(spos, 3.99f) * 0.25f : 0.f;
    float twk = 0.5f + 0.5f * __sinf(G[16] * (float)(1 + ((h2 >> 6) & 1u)) + 6.2831853f * u16(h1 >> 8));
    twk = twk * twk; twk = twk * twk; twk = twk * twk * twk;              // sharp occasional twinkles
    const float spark = G[15];
    const float ng = G[14] * size * (0.75f + 0.5f * spark * twk);
    const int o = k * tmax + ty * tw + tx;
    NA[o] = make_float4(px, py, code, size);
    const float hub = fmaxf(size - 1.f, 0.f);
    NB[o] = make_float4(ng + 2.5f * nf, 0.3f * ng + 3.f * nf, (spark * (twk + 0.3f * hub) + 1.6f * nf) * size, 0.f);
}

__device__ __forceinline__ float signal_at(float code, float along, float len, float sg) {
    const float ai = floorf(code);
    const float pos = (code - ai) * 4.f;
    const float behind = pos - along;                     // > 0: the line the signal has already lit
    const float tail = behind > 0.f ? 0.45f * __expf(-3.5f * behind) : 0.f;
    return ai * (1.f / 255.f) * (__expf(-sq(behind / sg)) + tail) * smooth(len + 0.1f, len - 0.2f, pos);
}

// Pass 2: per pixel, per visible layer: the 3x3 nodes around the pixel's cell, 12 segments.
extern "C" __global__ __launch_bounds__(256, 4)
void plexus(uchar4* __restrict__ out, const float4* __restrict__ NA, const float4* __restrict__ NB, const int W,
            const int H, const float* __restrict__ P, const int tmax)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float* G = P + GOFF;
    const float invH = 1.f / (float)H;
    const float ux = (x + 0.5f - 0.5f * W) * invH, uy = (y + 0.5f - 0.5f * H) * invH;

    // background: a faint palette-tinted glow towards the vanishing point
    const float r2 = ux * ux + uy * uy;
    const float3 bgp = lin3(pal(G[31], G));
    const float bgl = 0.33f * (bgp.x + bgp.y + bgp.z);
    const float bgk = G[29] * (0.3f + 0.7f * __expf(-2.2f * r2));     // desaturated: a hint of the palette only
    float3 col = make_float3((0.4f * bgp.x + 0.6f * bgl) * bgk + 0.0003f, (0.4f * bgp.y + 0.6f * bgl) * bgk + 0.0004f,
                             (0.4f * bgp.z + 0.6f * bgl) * bgk + 0.0011f);

    const float reach = G[12], reach_in = 0.5f * G[12], reach2 = G[12] * G[12], lg = G[13];

    #pragma unroll 1
    for (int k = 0; k < NSLOT; ++k) {
        const float* S = P + k * SLOT;
        const float lw = S[0];
        if (lw <= 1e-3f) continue;
        const float gx = S[1] * ux - S[2] * uy + S[3];
        const float gy = S[2] * ux + S[1] * uy + S[4];
        const float fx = floorf(gx), fy = floorf(gy);
        const float lx = gx - fx, ly = gy - fy;           // position inside the cell, [0, 1)
        const int tw = (int)S[14];
        const int tx = min(max((int)fx - (int)S[12], 1), tw - 2), ty = min(max((int)fy - (int)S[13], 1), (int)S[15] - 2);
        const int base = k * tmax + ty * tw + tx;
        const float pxc = S[7], hw = S[8], rn = S[9], fr = S[10];

        float nx[9], ny[9];
        unsigned int fmask = 0u;                          // which of the 9 nodes are firing
        float Lc = 0.f, Lw = 0.f;                         // coloured light, white light
        const float rn2 = rn * rn;
        const float wr = fmaxf(0.55f * pxc, 0.22f * rn);  // flare ray half width
        #pragma unroll
        for (int j = 0; j < 9; ++j) {
            const int dx = j %% 3 - 1, dy = j / 3 - 1;
            const int o = base + dy * tw + dx;
            const float4 A = __ldg(NA + o);
            nx[j] = A.x + dx; ny[j] = A.y + dy;
            if (A.z > 0.f) fmask |= 1u << j;
            const float ddx = lx - nx[j], ddy = ly - ny[j];
            const float d2 = ddx * ddx + ddy * ddy;
            const float rr = rn2 * A.w * A.w;
            const bool near_core = d2 < 100.f * rr;
            const float axx = fabsf(ddx), ayy = fabsf(ddy);
            const bool near_ray = fminf(axx, ayy) < 4.f * wr && fmaxf(axx, ayy) < fr;
            if (near_core || near_ray) {
                const float4 B = __ldg(NB + o);
                if (near_core) {
                    const float core = __expf(-d2 / rr);
                    const float glow = 0.3f * rr / (d2 + rr) * sq(1.f - d2 / (100.f * rr));
                    Lc += B.x * (0.45f * core + glow);
                    Lw += B.y * core;
                }
                if (near_ray && B.z > 0.01f) {             // star flare: 4 rays along the layer's axes
                    const float fl = fminf(fr * (0.6f + 0.4f * A.w), 0.98f);
                    const float ray = __expf(-sq(ddy / wr)) * sq(clamp01(1.f - axx / fl))
                                    + __expf(-sq(ddx / wr)) * sq(clamp01(1.f - ayy / fl));
                    Lw += 0.5f * B.z * ray;
                    Lc += 0.35f * B.z * ray;
                }
            }
        }
        // lines: centre -> 8 neighbours, plus the 4 diagonals between the side neighbours (with node offsets
        // <= 0.38 cell from the cell centre these are all the lines that can come near this cell)
        const float hwe = fmaxf(hw, 0.6f * pxc);
        const float cov = hw / hwe;                       // sub-pixel lines keep their energy, not their width
        const float hr = fminf(4.f * hwe, 0.11f);         // halo radius
        const float dmax = fmaxf(hr, 2.5f * hwe), dmax2 = dmax * dmax;
        const float ih2 = 1.f / (hwe * hwe);
        #pragma unroll
        for (int s = 0; s < 12; ++s) {
            const int a = s < 8 ? 4 : (s < 10 ? 1 : 7);
            const int b = s < 8 ? (s < 4 ? s : s + 1) : ((s & 1) ? 5 : 3);
            if (lx < fminf(nx[a], nx[b]) - dmax || lx > fmaxf(nx[a], nx[b]) + dmax ||
                ly < fminf(ny[a], ny[b]) - dmax || ly > fmaxf(ny[a], ny[b]) + dmax) continue;   // cheap reject
            const float bax = nx[b] - nx[a], bay = ny[b] - ny[a];
            const float L2 = bax * bax + bay * bay;
            if (L2 >= reach2) continue;
            const float pax = lx - nx[a], pay = ly - ny[a];
            const float t = clamp01((pax * bax + pay * bay) / fmaxf(L2, 1e-6f));
            const float qx = pax - bax * t, qy = pay - bay * t;
            const float d2 = qx * qx + qy * qy;
            if (d2 > dmax2) continue;
            const float len = sqrtf(L2);
            const float fade = smooth(reach, reach_in, len) * (1.f + 0.8f * smooth(0.42f, 0.12f, len));
            const float core = __expf(-d2 * ih2) * cov;
            const float halo = 0.10f * sq(clamp01(1.f - sqrtf(d2) / hr));
            Lc += lg * fade * (core + halo);
        }
        // fired signals running out from either end of the lines touching a firing node (rare: separate loop)
        if (fmask) {
            const float sg = 0.06f + 0.5f * hwe;
            #pragma unroll 1
            for (int s = 0; s < 12; ++s) {
                const int a = s < 8 ? 4 : (s < 10 ? 1 : 7);
                const int b = s < 8 ? (s < 4 ? s : s + 1) : ((s & 1) ? 5 : 3);
                if (!(fmask & ((1u << a) | (1u << b)))) continue;
                const int oa = base + (a / 3 - 1) * tw + (a %% 3 - 1), ob = base + (b / 3 - 1) * tw + (b %% 3 - 1);
                const float4 A = __ldg(NA + oa), B = __ldg(NA + ob);
                const float ax = A.x + (float)(a %% 3 - 1), ay = A.y + (float)(a / 3 - 1);
                const float bx = B.x + (float)(b %% 3 - 1), by = B.y + (float)(b / 3 - 1);
                if (lx < fminf(ax, bx) - dmax || lx > fmaxf(ax, bx) + dmax ||
                    ly < fminf(ay, by) - dmax || ly > fmaxf(ay, by) + dmax) continue;
                const float bax = bx - ax, bay = by - ay;
                const float L2 = bax * bax + bay * bay;
                if (L2 >= reach2) continue;
                const float pax = lx - ax, pay = ly - ay;
                const float t = clamp01((pax * bax + pay * bay) / fmaxf(L2, 1e-6f));
                const float qx = pax - bax * t, qy = pay - bay * t;
                const float d2 = qx * qx + qy * qy;
                if (d2 > dmax2) continue;
                const float len = sqrtf(L2);
                const float fade = smooth(reach, reach_in, len);
                float sgl = 0.f;
                if (A.z > 0.f) sgl += signal_at(A.z, t * len, len, sg);
                if (B.z > 0.f) sgl += signal_at(B.z, (1.f - t) * len, len, sg);
                sgl *= __expf(-d2 * ih2 * 0.45f) * fade;
                Lw += 1.6f * sgl;
                Lc += 1.2f * sgl;
            }
        }
        if (Lc + Lw > 1e-5f) {
            // hue: the layer's palette index plus a slow, smooth spread across the layer
            const float hx = gx - S[3], hy = gy - S[4];  // layer coords about its (continuous) centre
            const float hue = S[6] + 0.07f * (__sinf(0.23f * hx + 0.18f * hy) + __sinf(0.19f * hy - 0.11f * hx + 1.7f));
            const float3 pc = lin3(pal(hue, G));
            const float wk = Lw * lw;
            const float ck = Lc * lw;
            col.x += pc.x * ck + wk * (0.35f * pc.x + 0.65f);
            col.y += pc.y * ck + wk * (0.35f * pc.y + 0.65f);
            col.z += pc.z * ck + wk * (0.35f * pc.z + 0.65f);
        }
    }
    // tone map (soft shoulder) and encode (gamma 2.2)
    const float g = G[28];
    const float rr = 1.f - __expf(-col.x * g), gg = 1.f - __expf(-col.y * g), bb = 1.f - __expf(-col.z * g);
    out[y * W + x] = make_uchar4((unsigned char)(255.f * __powf(fmaxf(rr, 1e-6f), 0.4545f) + 0.5f),
                                 (unsigned char)(255.f * __powf(fmaxf(gg, 1e-6f), 0.4545f) + 0.5f),
                                 (unsigned char)(255.f * __powf(fmaxf(bb, 1e-6f), 0.4545f) + 0.5f), 255);
}
""" % {"nslot": NSLOT, "slot": SLOT, "goff": GOFF, "latm": LAT - 1}

_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash01(*vals: int) -> float:
    """Deterministic host hash -> [0, 1) (layer identities: angle, offset, spin)."""
    h = 0x811C9DC5
    for v in vals:
        h = ((h ^ (int(v) & 0xFFFFFFFF)) * 0x01000193) & 0xFFFFFFFF
        h ^= h >> 15
        h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
        h ^= h >> 12
    return (h & 0xFFFFFF) / float(1 << 24)


class PlexusNetwork(Source):
    name = "plexus_network"
    label = "Plexus"
    description = ("A constellation / neural network: drifting nodes joined by thin lines that fade with length, "
                   "sparkling and firing on the beat, in parallax layers slowly zooming past.")
    _std = standard_scene_params(palette="ocean", render_scale=0.5, period=1.0, drift=0.02)
    params = {
        "node_density": num(4.0, 16.0, 7.0, "Node density (cells per height)", step=0.1, basic=True),
        "layers": num(2.0, 5.0, 4.0, "Depth layers", step=0.05, basic=True),
        "connection_reach": num(0.8, 1.5, 1.15, "Connection reach (cells)"),
        "line_width": num(0.5, 4.0, 1.5, "Line width", unit="px", step=0.05),
        "sparkle": num(0.0, 1.0, 0.5, "Sparkle"),
        "palette_period": _std["palette_period"],
        "energy": _std["energy"],
        "pulse": _std["pulse"],
        "flash_kick": num(0.0, 1.5, 1.0, "Beat flash / firing"),
        "zoom_kick": num(0.0, 8.0, 4.0, "Beat zoom push"),
        "zoom_speed": num(-1.0, 3.0, 1.0, "Zoom speed", unit="×", tier="realtime", bind="rate"),
        **{k: v for k, v in _std.items() if k not in ("palette_period", "energy", "pulse")},
    }
    del _std

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC, _OPTIONS)
        self.k = mod.get_function("plexus")
        self.k_nodes = mod.get_function("plexus_nodes")
        # node tables: worst case = the densest visible layer (u <= 5) covering the rotated screen + margins
        aspect = self.w / self.h
        max_cells = float(self.params["node_density"]["maximum"]) * R_DEPTH ** 4.0
        self.side = int(math.ceil(math.hypot(aspect, 1.0) * max_cells)) + 8
        self.tmax = self.side * self.side
        self.NA = cp.zeros((NSLOT * self.tmax, 4), cp.float32)
        self.NB = cp.zeros((NSLOT * self.tmax, 4), cp.float32)
        self.P = cp.zeros(GOFF + 32, cp.float32)
        self.Ph = np.zeros(GOFF + 32, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        self.zoom = float(rng.uniform(0.0, 1.0)) + 64.0
        self.orb = float(rng.uniform(0.0, ORB_WRAP))
        self.tw = float(rng.uniform(0.0, TAU))
        self.rot = float(rng.uniform(0.0, TAU))
        self.sway = float(rng.uniform(0.0, TAU))
        self.drift = 0.0
        self.t = 0.0
        self.es = None                    # slewed energy (complexity / reach)
        self.armed = True
        self.last_fire = -1.0
        self.beat_id = 1
        self.cur = [0.0, 9.0]             # fire amp, age of the current generation
        self.prev = [0.0, 9.0]

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = max(0.0, float(dt))
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ep = _ss(0.1, 0.85, e)
        a = 1.0 - math.exp(-dt / 1.8) if dt > 0 else 1.0
        self.es = ep if self.es is None else self.es + (ep - self.es) * a
        es15 = self.es ** 1.5
        kk = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ep
        self.t += dt

        # ---- phases (host-integrated; kicks add to rates only)
        self.zoom += 0.11 * float(p["zoom_speed"]) * (0.4 + 1.2 * ep) * (1.0 + float(p["zoom_kick"]) * kk) * spd * dt
        self.orb = math.fmod(self.orb + 0.55 * rate * spd * dt, ORB_WRAP)
        self.tw = math.fmod(self.tw + 1.6 * (0.5 + 1.0 * ep) * spd * dt, TAU)
        self.rot += 0.028 * rate * spd * dt
        self.sway += 0.05 * rate * spd * dt
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * spd * dt, 2.0)

        # ---- beat spawns: rising edge with hysteresis on the (eased) pulse
        if pulse < 0.3:
            self.armed = True
        for g in (self.cur, self.prev):
            g[1] += dt
        if self.armed and pulse > 0.6 and self.t - self.last_fire >= 0.15:
            self.armed = False
            self.last_fire = self.t
            self.prev = self.cur
            self.cur = [min(1.0, float(p["flash_kick"]) * (0.4 + 0.6 * e)), 0.0]
            self.beat_id = (self.beat_id + 1) % (1 << 20)
        env = lambda g: g[0] * math.exp(-g[1] / FIRE_TAU) if g[1] < 6 * FIRE_TAU else 0.0

        flash = min(1.0, float(p["flash_kick"]) * kk)
        L = float(p["layers"])
        dens = float(p["node_density"])
        H = float(self.h)
        Ph = self.Ph
        Ph[:] = 0.0
        n0 = math.floor(self.zoom)
        fz = self.zoom - n0
        pan_x = 0.9 * math.sin(self.sway)
        pan_y = 0.55 * math.sin(2.0 * self.sway + 1.1)
        lwpx = float(p["line_width"])
        seed = int(p["seed"])
        for k in range(NSLOT):
            n = n0 + k + 1
            u = (k + 1) - fz
            w = _ss(0.0, 0.9, u) * (1.0 - _ss(L - 0.85, L, u))
            if w <= 1e-3:
                continue
            cells = dens * R_DEPTH ** (u - 1.0)
            ppc = H / cells                                     # internal px per cell
            w *= _ss(9.0, 18.0, ppc)                            # footprint fade: no shimmering sub-Nyquist meshes
            w *= math.exp(-0.55 * (u - 1.0)) if u > 1.0 else 1.0  # depth dimming
            if w <= 1e-3:
                continue
            spin = 1.0 if _hash01(seed, n, 3) < 0.5 else -1.0
            ang = TAU * _hash01(seed, n, 1) + spin * self.rot + 0.12 * u
            ca, sa = math.cos(ang), math.sin(ang)
            ox = _hash01(seed, n, 5) * LAT + ca * pan_x - sa * pan_y
            oy = _hash01(seed, n, 7) * LAT + sa * pan_x + ca * pan_y
            thick = max(0.55, 1.6 - 0.25 * u)
            pxc = 1.0 / ppc
            ox, oy = ox % LAT, oy % LAT
            # table bounding box: the screen corners in this layer's cell space, plus a 2-cell margin
            m11, m12, m21, m22 = ca * cells, -sa * cells, sa * cells, ca * cells
            hx, hy = 0.5 * self.w / self.h, 0.5
            gxs = [m11 * cx_ + m12 * cy_ + ox for cx_ in (-hx, hx) for cy_ in (-hy, hy)]
            gys = [m21 * cx_ + m22 * cy_ + oy for cx_ in (-hx, hx) for cy_ in (-hy, hy)]
            cx0, cy0 = math.floor(min(gxs)) - 2, math.floor(min(gys)) - 2
            tw = min(math.floor(max(gxs)) + 3 - cx0, self.side)
            th = min(math.floor(max(gys)) + 3 - cy0, self.side)
            s = k * SLOT
            Ph[s + 0] = w
            Ph[s + 1] = ca * cells
            Ph[s + 2] = sa * cells
            Ph[s + 3] = ox
            Ph[s + 4] = oy
            Ph[s + 5] = n % (1 << 20)
            Ph[s + 6] = math.fmod(n * float(p["palette_period"]) * 0.25, 2.0) + self.drift
            Ph[s + 7] = pxc
            Ph[s + 8] = min(0.5 * lwpx * thick * pxc, 0.06)
            Ph[s + 9] = min(1.4 * thick * thick * pxc * (0.8 + 0.25 * lwpx), 0.12)
            Ph[s + 10] = min((0.2 + 0.45 * float(p["sparkle"])) * (0.7 + 0.3 * thick), 0.95)
            Ph[s + 11] = u
            Ph[s + 12] = cx0
            Ph[s + 13] = cy0
            Ph[s + 14] = tw
            Ph[s + 15] = th
        G = GOFF
        Ph[G:G + 12] = palette_coeffs(p["palette"])
        Ph[G + 12] = float(p["connection_reach"]) * (1.0 + 0.2 * es15)
        Ph[G + 13] = 0.75 * (0.55 + 0.45 * ep) * (1.0 + 0.2 * flash)
        Ph[G + 14] = 1.0 * (0.7 + 0.3 * ep) * (1.0 + 0.35 * flash)
        Ph[G + 15] = float(p["sparkle"])
        Ph[G + 16] = self.tw
        Ph[G + 17] = self.orb
        Ph[G + 18] = 0.075 + 0.055 * ep
        Ph[G + 19] = 0.25
        Ph[G + 20] = (0.04 + 0.12 * es15) * min(max(7.0 / dens, 0.45), 1.5)   # ~constant firings per screen
        Ph[G + 21] = self.beat_id
        Ph[G + 22] = env(self.cur)
        Ph[G + 23] = self.cur[1]
        Ph[G + 24] = (self.beat_id - 1) % (1 << 20)
        Ph[G + 25] = env(self.prev)
        Ph[G + 26] = self.prev[1]
        Ph[G + 27] = SIG_SPEED
        Ph[G + 28] = float(p["intensity"]) * 1.1
        Ph[G + 29] = 0.005
        Ph[G + 30] = self.drift
        Ph[G + 31] = self.drift + 0.5
        self.P.set(Ph)
        launch_2d(self.k_nodes, self.side, self.side * NSLOT,
                  (self.NA, self.NB, self.P, np.int32(self.side), np.int32(self.tmax), np.uint32(seed)))
        launch_2d(self.k, self.w, self.h, (self.buf, self.NA, self.NB, np.int32(self.w), np.int32(self.h), self.P,
                                           np.int32(self.tmax)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = self.P = self.NA = self.NB = None
