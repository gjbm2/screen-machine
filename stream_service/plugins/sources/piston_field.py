"""Piston field: an endless hexagonal deck of rounded rhombic-capped pistons that pop out on the beat (3D).

Source: Original (inspired by https://www.shadertoy.com/view/WtXcWB , Blackle Mori, "Whack-A-Mole Pistons";
        mirror evidence https://github.com/nmbr73/Shaderfuse/blob/main/Shaders/ShaderOfTheWeek/WhackAMolePistons.fuse ;
        the original's header reads "CC0 1.0 Universal ... Blackle Mori has waived all copyright".  No code was
        copied: the idea was reimplemented from scratch on smfx/_lib/sdf3d.py.)
Author: smfx (idea: Blackle Mori)
License: MIT
license_class: permissive

Every cell of a hexagonal lattice holds a piston: a head whose top is the three-rhombus cap of a rhombic
dodecahedron seen down its 3-fold axis (so the resting deck reads as an isometric "tumbling blocks" pattern), on a
round rod that slides out of a hex hole in the deck.  ``piston_profile`` morphs the head from faceted hex + rhombic
cap to a round cylinder + dome.  A small pre-pass kernel computes every visible cell's extension into a 256x256
ring buffer around the camera, so the march only looks up 2 heights per step (own cell + the cell across the
nearest edge, with the step clamped by the second-nearest edge).

Beats: the host counts rising edges of ``pulse`` (hysteresis, >= 150 ms apart) and keeps the last 14 as
(beat id, time since, fire fraction, stroke).  A cell fires on a beat when hash(cell, beat) < fraction; its
extension is the sum of spring(time since) over the beats it fired on, so pistons pop, overshoot back into the
deck, bounce and settle: they never jump.  A quarter of the fired cells get whacked DOWN instead.  Without beats
an internal metronome keeps a sparser whack-a-mole going so the field is never frozen.

Macros: energy -> fraction of cells firing per beat (e'^1.5, slewed), stroke amplitude, camera drift speed
x(0.4+1.2e'), rolling deck-wave amplitude/rate, tip emissive.  pulse -> beat spawns (above), tip emissive flash
and a kick into the wave phase rate.  speed scales camera drift, waves and the spring time scale.  palette: tip,
rim and hole-glow colour pal(hash(cell) * period + drift).  intensity is output gain.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Source

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS, camera_pack, rig_pack, wrap
from stream_service.plugins._lib.upsample import internal_size, present

NSLOT = 14             # beat slots (life 2 s at >= 150 ms spacing -> <= 14 alive)
SLOT_F = 6             # floats per slot: beat id, spring value, fire fraction, stroke, flash, alive
WIN = 256              # cell ring buffer is WIN x WIN around the camera
GAP = 0.035            # head inset from the cell edge (cell centres are 1 apart)
APO = 0.5 - GAP        # head apothem
CAP_H = 0.8165 * APO   # rhombic cap apex height above the rim
BOTTOM_Y = -3.0
SPRING_LIFE = 2.0      # seconds (speed-scaled) a beat stays in a slot

# P layout (floats)
#  0..12 camera | 13..24 palette | 25 period | 26 drift | 27 gain | 28 fog | 29 profile | 30 material
#  31 tip emissive base | 32 pulse flash | 33 top bound (max piston top y) | 34 tmax | 35 seed
#  36 origin col | 37 origin row | 38 detail phase | 40..56 rig
_SRC = HELPERS + PALETTE_CUDA + SDF3D + r"""
#define GAP (%(gap)ff)
#define APO (%(apo)ff)
#define HEAD_H 0.42f
#define ROD_R 0.29f
#define DECK_Y (-HEAD_H)
#define HOLE_A (APO + 0.012f)
#define BOTTOM_Y (%(bottom)ff)

// iq's hexagon SDF: flat edges at y = +-r
__device__ __forceinline__ float sd_hex(float2 p, float r) {
    p = abs(p);
    const float dd = fminf(-0.866025404f * p.x + 0.5f * p.y, 0.f);
    p.x += 2.f * dd * 0.866025404f; p.y -= 2.f * dd * 0.5f;
    p.x -= clamp(p.x, -0.577350269f * r, 0.577350269f * r); p.y -= r;
    return copysignf(length(p), p.y);
}
// head cross-section: hex with flat walls facing the +-x neighbours, rounded towards a circle by the profile
__device__ __forceinline__ float head_wall(float qx, float qz, float prof) {
    const float rr = prof * APO * 0.98f;
    return sd_hex(f2(qz, qx), APO - rr) - rr;
}
// one piston in local coords (origin = cell centre at the rim height)
__device__ __forceinline__ float piston_d(float3 q, float prof) {
    const float wall = head_wall(q.x, q.z, prof);
    // rhombic-dodecahedron cap: three faces inclined 35.26 deg, apex over the centre; -> dome as profile -> 1
    const float c0 = 0.8165f * q.y + 0.57735f * q.z;
    const float c1 = 0.8165f * q.y - 0.5f * q.x - 0.288675f * q.z;
    const float c2 = 0.8165f * q.y + 0.5f * q.x - 0.288675f * q.z;
    const float kc = 0.02f + 0.12f * prof;
    float cap = smax(smax(c0, c1, kc), c2, kc) - 0.6667f * APO;
    const float dome = length(f3(q.x, q.y + 0.75f * APO, q.z)) - 1.25f * APO;
    cap = mix(cap, dome, prof);
    float d = smax(wall, cap, 0.02f + 0.06f * prof);
    d = fmaxf(d, -HEAD_H - q.y);                                  // underside of the head
    const float rod = fmaxf(length(f2(q.x, q.z)) - ROD_R, q.y + 0.5f * HEAD_H);
    return fminf(d, rod);
}

struct Cell { float2 c; float H; float glow; int col, row; int id; };   // id 0 deck, 1 piston

__device__ __forceinline__ int cell_index(float2 c, const float* __restrict__ P, int& colg, int& rowg) {
    const int row = __float2int_rn(c.y * 1.15470054f);
    const int col = __float2int_rn(c.x - 0.5f * (float)(row & 1));
    colg = col + (int)P[36]; rowg = row + (int)P[37];
    return ((rowg & 255) << 8) | (colg & 255);
}

__device__ __forceinline__ float scene(float3 p, const float* __restrict__ P, const float2* __restrict__ cells,
                                       Cell& ci) {
    const float above = p.y - P[33];
    if (above > 0.6f) { ci.id = -1; return above; }              // over every piston: the bound plane is enough
    // hex lattice (iq): two candidate centres, keep the nearer
    const float SZ = 1.7320508f;
    const float ax = floorf(p.x) + 0.5f, az = (floorf(p.z / SZ) + 0.5f) * SZ;
    const float bx = floorf(p.x - 0.5f) + 1.0f, bz = (floorf((p.z - 1.f) / SZ) + 1.0f) * SZ;
    const float2 hA = f2(p.x - ax, p.z - az), hB = f2(p.x - bx, p.z - bz);
    const bool useA = dot(hA, hA) < dot(hB, hB);
    const float2 c = useA ? f2(ax, az) : f2(bx, bz);
    const float2 h = useA ? hA : hB;
    // nearest edge -> neighbour; second-nearest edge -> step clamp
    const float t0 = h.x, t1 = 0.5f * h.x + 0.8660254f * h.y, t2 = -0.5f * h.x + 0.8660254f * h.y;
    const float a0 = fabsf(t0), a1 = fabsf(t1), a2 = fabsf(t2);
    float2 nd; float m2;
    if (a0 >= a1 && a0 >= a2) { nd = f2(copysignf(1.f, t0), 0.f); m2 = fmaxf(a1, a2); }
    else if (a1 >= a2)        { nd = f2(0.5f, 0.8660254f) * copysignf(1.f, t1); m2 = fmaxf(a0, a2); }
    else                      { nd = f2(-0.5f, 0.8660254f) * copysignf(1.f, t2); m2 = fmaxf(a0, a1); }
    const float2 cn = c + nd;
    int colA, rowA, colB, rowB;
    const float2 vA = cells[cell_index(c, P, colA, rowA)];
    const float2 vB = cells[cell_index(cn, P, colB, rowB)];
    const float prof = P[29];
    const float dA = piston_d(f3(p.x - c.x, p.y - vA.x, p.z - c.y), prof);
    const float dB = piston_d(f3(p.x - cn.x, p.y - vB.x, p.z - cn.y), prof);
    const float dD = fmaxf(p.y - DECK_Y, -sd_hex(f2(h.y, h.x), HOLE_A));
    float d = dA; ci.c = c; ci.H = vA.x; ci.glow = vA.y; ci.col = colA; ci.row = rowA; ci.id = 1;
    if (dB < d) { d = dB; ci.c = cn; ci.H = vB.x; ci.glow = vB.y; ci.col = colB; ci.row = rowB; }
    if (dD < d) { d = dD; ci.c = c; ci.H = vA.x; ci.glow = vA.y; ci.col = colA; ci.row = rowA; ci.id = 0; }
    d = fminf(d, 0.5f - m2 + GAP);                              // other cells start beyond the 2nd edge
    return fmaxf(d, above);
}

__device__ __forceinline__ float3 sky(float3 r, float3 fogc, float3 tint) {
    const float up = saturate(r.y);
    float3 c = mix(fogc, fogc * 0.2f, saturate(up * 2.5f));
    const float box = smoothstep(0.45f, 0.52f, r.y) * (1.f - smoothstep(0.70f, 0.78f, r.y));   // softbox band
    const float box2 = smoothstep(0.86f, 0.92f, r.y);
    return c + tint * (1.6f * box + 0.9f * box2) + f3(0.9f, 0.85f, 0.8f) * (0.5f * box);
}

SDF_KERNEL
void piston_field(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                  const float2* __restrict__ cells)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const Cam cam = cam_load(P);
    const float3 ro = cam.eye, rd = cam_ray(cam, x, y, W, H);
    const float pix = pixel_angle(cam, H);
    Cell ci;
    auto map = [&](float3 p) { return scene(p, P, cells, ci); };

    const float* PAL = P + 13;
    const float drift = P[26], period = P[25];
    const float3 hz = srgb_to_linear(pal(drift + 0.5f, PAL));
    const float3 fogc = mix(hz, f3(dot(hz, f3(0.333f))), 0.4f) * 0.07f + f3(0.006f, 0.006f, 0.010f);
    const float3 tint = mix(hz, f3(1.f), 0.6f) * 0.35f;

    const float top = P[33] + 0.05f;
    float tmin = 0.02f, tmax = P[34];
    if (ro.y > top) tmin = rd.y < -1e-4f ? (ro.y - top) / -rd.y : 1e9f;
    if (rd.y < -1e-4f) tmax = fminf(tmax, (BOTTOM_Y - ro.y) / rd.y);
    bool hit = false; float t = tmax;
    if (tmin < tmax) {
        const Hit hh = march(map, ro, rd, tmin, tmax, 110, 0.6f * pix, 0.9f);
        hit = hh.hit;
        t = hit ? hh.t : tmax;
    }
    float3 col = sky(rd, fogc, tint * 0.25f);
    if (hit) {
        const float3 pos = ro + rd * t;
        const float3 n = calc_normal(map, pos, fmaxf(0.5f * pix * t, 1e-3f));
        const float ao = calc_ao(map, pos, n, 0.45f, 3);
        scene(pos, P, cells, ci);
        const float fw = fmaxf(pix * t, 1e-3f);
        const float m = P[30];
        const float wm = 1.f - smoothstep(0.f, 0.5f, m), wn = smoothstep(0.5f, 1.f, m), wg = 1.f - wm - wn;
        const float hc = hash1((unsigned int)ci.col, (unsigned int)ci.row, (unsigned int)P[35]);
        const float3 tc = srgb_to_linear(pal(hc * period + 0.05f * ci.H + drift, PAL));
        const float3 q = f3(pos.x - ci.c.x, pos.y - ci.H, pos.z - ci.c.y);
        const float fre = 1.f - saturate(dot(n, -rd));
        const float fre5 = fre * fre * fre * fre * fre;
        float3 alb, emis = f3(0.f);
        float spec, shin, refl;
        if (ci.id == 0) {                                        // deck: dark plate, glowing hole lips
            const float hd = sd_hex(f2(q.z, q.x), HOLE_A);
            const float lip = expf(-fmaxf(hd, 0.f) * 22.f) * (1.f - smoothstep(0.f, 0.02f, -hd));
            const float out_ = smoothstep(0.05f, 0.7f, ci.H);
            alb = f3(0.03f, 0.031f, 0.034f);
            emis = tc * (lip * (0.15f + 2.2f * out_) * (0.6f + 0.6f * wn));
            spec = 0.4f; shin = 30.f; refl = 0.15f;
        } else {
            const float capw = smoothstep(0.35f, 0.7f, n.y);
            const float rodw = 1.f - smoothstep(-HEAD_H - 0.03f, -HEAD_H + 0.01f, q.y);
            const float wall2 = head_wall(q.x, q.z, P[29]);
            // brushed streaks (concentric on the cap, horizontal on the walls), faded by pixel footprint
            const float sf = 1.f - smoothstep(0.004f, 0.02f, fw);
            const float br = 1.f + 0.12f * sf * (hash1((unsigned int)(int)(length(f2(q.x, q.z)) * 90.f), 3u) - 0.5f)
                                 + 0.10f * sf * (hash1((unsigned int)(int)((q.y + 8.f) * 70.f), 5u) - 0.5f);
            const float3 steel = f3(0.34f, 0.35f, 0.37f) * br;
            const float3 cap_alb = steel * mix(f3(1.f), tc * 1.6f, 0.55f) * wm + tc * wg + tc * 0.05f * wn;
            const float3 wall_alb = steel * 0.85f * wm + (tc * 0.3f + f3(0.02f)) * wg + f3(0.018f) * wn;
            const float3 rod_alb = f3(0.55f, 0.56f, 0.58f);
            alb = mix(mix(wall_alb, cap_alb, capw), rod_alb, rodw);
            spec = (0.9f * wm + 1.3f * wg + 0.6f * wn) * (1.f + 0.6f * rodw);
            shin = 26.f * wm + 110.f * wg + 60.f * wn + 60.f * rodw;
            refl = (0.30f + 0.4f * fre5) * wm + (0.05f + 0.9f * fre5) * wg + 0.08f * wn + 0.35f * rodw;
            // tips: palette glow on the cap, flashing when the cell fired
            const float pulse = P[32];
            const float tipe = (P[31] * (0.45f + 0.75f * wn) + 2.4f * ci.glow) * (1.f + 0.35f * pulse);
            emis = tc * (tipe * capw * (1.f - rodw));
            // neon rim: cap edge + a collar band just under the rim (+ a faint one for gloss/metal)
            const float edge = (1.f - smoothstep(0.f, 0.045f + fw, -wall2)) * capw;
            const float collar = smoothstep(-0.19f - fw, -0.16f, q.y) * (1.f - smoothstep(-0.11f, -0.08f + fw, q.y))
                                 * (1.f - capw) * (1.f - rodw);
            emis += tc * ((edge * 2.2f + collar * 2.8f) * (0.12f + 0.88f * wn) * (1.f + 0.6f * ci.glow + 0.3f * pulse));
            // rods glow from below when the piston is out of its hole
            emis += tc * (rodw * 0.35f * smoothstep(0.05f, 0.8f, ci.H) * saturate(1.f + (pos.y - DECK_Y) * 0.8f));
        }
        Rig L = rig_load(P + 40);
        L.spec = spec; L.shin = shin;
        col = shade(L, alb, n, rd, 1.f, ao) + emis;
        col += sky(reflect(rd, n), fogc, tint) * (refl * ao);
        // keep hole interiors dark with depth
        col *= saturate(1.f + (pos.y - DECK_Y) * 0.55f);
    }
    col = fog_exp2(col, fogc, t, P[28]);
    if (hit) col = mix(col, fogc, smoothstep(0.03f, 0.09f, pix * t));   // sub-pixel cells -> haze, no shimmer
    col = vignette(col, x, y, W, H, 0.3f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col * P[27])));
}
""" % {"gap": GAP, "apo": APO, "bottom": BOTTOM_Y}

# Cell pre-pass (precise maths: per-cell phases can be large).  Q layout:
#  0 camera col (global) | 1 camera row | 2 origin col | 3 origin row | 4 seed | 5 wave amp | 6,7 wave vector
#  8 wave phase | 9,10 wave2 vector | 11 wave2 phase | 12 idle amp | 13 idle phase | 14,15 eye x,z (local)
#  16 eye y | 17 down fraction | 20.. slots (NSLOT x SLOT_F)
_CELL_SRC = HELPERS + r"""
#define NSLOT %(nslot)d
#define SLOT_F %(slotf)d
__device__ __forceinline__ float smin_p(float a, float b, float k) {
    const float h = fmaxf(k - fabsf(a - b), 0.f) / k;
    return fminf(a, b) - h * h * k * 0.25f;
}
extern "C" __global__
void piston_cells(float2* __restrict__ cells, const float* __restrict__ Q)
{
    const int a = blockIdx.x * blockDim.x + threadIdx.x, b = blockIdx.y * blockDim.y + threadIdx.y;
    if (a >= 256 || b >= 256) return;
    const int cc = (int)Q[0], rc = (int)Q[1];
    const int col = cc + ((a - cc + 128) & 255) - 128;
    const int row = rc + ((b - rc + 128) & 255) - 128;
    const int lc = col - (int)Q[2], lr = row - (int)Q[3];
    const float cx = (float)lc + 0.5f * (float)(lr & 1), cz = (float)lr * 0.8660254f;
    const unsigned int seed = (unsigned int)Q[4];
    const unsigned int uc = (unsigned int)col, ur = (unsigned int)row;
    const float h1 = hash1(uc, ur, seed), h2 = hash1(uc, ur, seed + 101u);
    // rolling deck waves + per-cell idle bob (integer multiples of a 2 pi-wrapped phase: seamless)
    float H = Q[5] * (0.75f + 0.25f * h1) * sinf(cx * Q[6] + cz * Q[7] - Q[8])
            + 0.5f * Q[5] * sinf(cx * Q[9] + cz * Q[10] - Q[11])
            + Q[12] * sinf(Q[13] * (1.f + floorf(h1 * 3.f)) + 6.2831853f * h2);
    float glow = 0.f;
    for (int k = 0; k < NSLOT; ++k) {
        const float* s = Q + 20 + SLOT_F * k;
        if (s[5] <= 0.f) continue;
        const unsigned int bid = (unsigned int)s[0] * 7919u + seed;
        const float u = hash1(uc, ur, bid);
        if (u < s[2]) {
            const bool down = hash1(ur, uc, bid + 3u) < Q[17];
            const float v = 0.7f + 0.6f * hash1(uc ^ 0x55u, ur, bid + 9u);
            H += s[3] * s[1] * v * (down ? -0.5f : 1.f);
            glow = fmaxf(glow, s[4] * (down ? 0.35f : 1.f));
        }
    }
    // keep pistons clear of a low camera (soft cone under the eye)
    const float dx = cx - Q[14], dz = cz - Q[15];
    const float lim = Q[16] - 0.95f + 0.8f * fmaxf(sqrtf(dx * dx + dz * dz) - 0.9f, 0.f);
    H = smin_p(H, lim, 0.3f);
    cells[b * 256 + a] = make_float2(H, glow);
}
""" % {"nslot": NSLOT, "slotf": SLOT_F}


def _spring_raw(t: float) -> float:
    return (1.0 - math.exp(-14.0 * t)) * math.exp(-2.0 * t) * math.cos(3.6 * t)


_SPRING_NORM = max(_spring_raw(i / 1000.0) for i in range(1, 1000))


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def spring(t: float) -> float:
    """Pop out fast, overshoot back into the deck, bounce, settle; 0 at t = 0 and (windowed) at SPRING_LIFE."""
    if t <= 0.0:
        return 0.0
    return _spring_raw(t) / _SPRING_NORM * (1.0 - _smooth(1.35, SPRING_LIFE, t))


class PistonField(Source):
    name = "piston_field"
    label = "Piston field"
    description = "An endless deck of rounded rhombic pistons popping in and out with springy overshoot on the beat (3D raymarched)."
    params = {
        **standard_scene_params(palette="neon", render_scale=0.5, period=1.0, drift=0.03),
        "cell_scale": num(0.4, 2.5, 1.0, "Cell size", tier="regional"),
        "piston_profile": num(0.0, 1.0, 0.3, "Piston profile (faceted -> round)", tier="regional"),
        "extension": num(0.1, 1.0, 0.6, "Stroke", tier="regional", basic=True),
        "camera_height": num(0.5, 4.0, 1.8, "Camera height", tier="regional"),
        "material": num(0.0, 1.0, 0.45, "Material (metal - gloss - neon)", tier="regional", basic=True),
        "down_mix": num(0.0, 0.6, 0.25, "Whack-down share", tier="regional"),
        "wave_kick": num(0.0, 3.0, 1.2, "Wave beat kick", tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, SDF3D_OPTIONS).get_function("piston_field")
        self.kc = raw_module(_CELL_SRC).get_function("piston_cells")
        self.cells = cp.zeros((WIN, WIN, 2), cp.float32)
        self.P = cp.zeros(64, cp.float32)
        self.Ph = np.zeros(64, np.float32)
        self.Q = cp.zeros(20 + NSLOT * SLOT_F, cp.float32)
        self.Qh = np.zeros(20 + NSLOT * SLOT_F, np.float32)
        self.seed = int(params["seed"]) & 0xFFFF
        rng = np.random.default_rng(int(params["seed"]))
        # beat slots: [beat id, tau (speed-scaled s), fire fraction, stroke, alive]
        self.slots = [[0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(NSLOT)]
        self.beat_id = int(rng.integers(0, 1 << 16))
        self.armed = True
        self.since_fire = 10.0         # real seconds since any fire
        self.since_beat = 10.0         # real seconds since a real beat
        self.metro = 0.0               # internal metronome phase (fires at 1)
        # camera (cell units, global), origin cell (row even) for float precision
        self.psi0 = float(rng.uniform(0.0, 2.0 * math.pi))
        self.cx, self.cz = float(rng.uniform(-50, 50)), float(rng.uniform(-50, 50))
        self.col0, self.row0 = 0, 0
        self._rebase()
        self.v = None
        self.cam_ph = float(rng.uniform(0.0, 100.0))
        self.wph = 0.0
        self.wph2 = 0.0
        self.iph = 0.0
        self.drift = 0.0
        self.e_s = None
        self.e_d = None

    def _rebase(self) -> None:
        r = int(round(self.cz / 0.8660254 / 2.0)) * 2
        self.row0, self.col0 = r, int(round(self.cx))

    def _fire(self, frac: float, stroke: float) -> None:
        # reuse a dead slot, else the oldest
        idx = min(range(NSLOT), key=lambda i: (self.slots[i][4] > 0.0, -self.slots[i][1]))
        self.beat_id = (self.beat_id + 1) % (1 << 20)
        self.slots[idx] = [float(self.beat_id), 0.0, frac, stroke, 1.0]
        self.since_fire = 0.0

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        cs = float(p["cell_scale"])
        ep = _smooth(0.1, 0.85, e)
        # slewed energy: a quick one for amplitudes/rates, a slow one for the firing density
        a1 = 1.0 - math.exp(-dt / 0.4) if dt > 0 else 1.0
        a2 = 1.0 - math.exp(-dt / 1.8) if dt > 0 else 1.0
        self.e_s = ep if self.e_s is None else self.e_s + (ep - self.e_s) * a1
        self.e_d = ep if self.e_d is None else self.e_d + (ep - self.e_d) * a2
        es, ed = self.e_s, self.e_d
        k = pulse * (0.3 + 0.7 * e)

        # ---- beats: rising edges (hysteresis, >= 150 ms), else an internal metronome while the music has none
        frac = 0.08 + 0.42 * ed ** 1.5
        stroke = float(p["extension"]) * 1.8 * (0.5 + 0.5 * _smooth(0.0, 1.0, es))
        self.since_fire += dt
        self.since_beat += dt
        if pulse < 0.3:
            self.armed = True
        if spd > 0.05:
            if self.armed and pulse > 0.6 and self.since_fire >= 0.15:
                self.armed = False
                self.since_beat = 0.0
                self._fire(frac, stroke)
            elif self.since_beat > 2.5:
                self.metro += dt * spd * (0.55 + 1.1 * es)
                if self.metro >= 1.0 and self.since_fire >= 0.15:
                    self.metro = 0.0
                    self._fire(frac * 0.6, stroke * 0.85)
        # ---- spring slots (time scale = speed)
        Qh = self.Qh
        hmax = 0.0
        for i, s in enumerate(self.slots):
            if s[4] > 0.0:
                s[1] += dt * spd
                if s[1] >= SPRING_LIFE:
                    s[4] = 0.0
            sv = spring(s[1]) if s[4] > 0.0 else 0.0
            flash = math.exp(-4.5 * s[1]) * min(1.0, s[1] * 30.0) if s[4] > 0.0 else 0.0
            Qh[20 + SLOT_F * i: 20 + SLOT_F * (i + 1)] = (s[0], sv, s[2], s[3], flash, s[4])
            hmax += s[3] * abs(sv) * 1.3 * (1.0 if s[4] > 0.0 else 0.0)

        # ---- waves / idle (phases wrapped at 2 pi; kicks go into rates only)
        wave_rate = (0.6 + 1.4 * es) * (1.0 + float(p["wave_kick"]) * k)
        self.wph = wrap(self.wph + wave_rate * spd * dt, 2.0 * math.pi)
        self.wph2 = wrap(self.wph2 + 0.7 * wave_rate * spd * dt, 2.0 * math.pi)
        self.iph = wrap(self.iph + (0.9 + 1.2 * es) * spd * dt, 2.0 * math.pi)
        wave_amp = 0.12 + 0.2 * _smooth(0.0, 1.0, es)
        idle_amp = 0.04 + 0.05 * es
        self.drift = wrap(self.drift + float(p["drift_speed"]) * spd * dt, 1000.0)

        # ---- camera: drift across the deck, heading wanders; spring-damped speed
        self.cam_ph += spd * (0.4 + 1.2 * es) * dt
        cph = self.cam_ph
        psi = self.psi0 + 0.9 * math.sin(0.11 * cph) + 0.4 * math.sin(0.047 * cph + 1.3)
        v_t = 0.5 * (0.4 + 1.2 * es) / cs
        av = 1.0 - math.exp(-dt / 1.2) if dt > 0 else 1.0
        self.v = v_t if self.v is None else self.v + (v_t - self.v) * av
        self.cx += self.v * spd * dt * math.cos(psi)
        self.cz += self.v * spd * dt * math.sin(psi)
        if abs(self.cx - self.col0) > 48.0 or abs(self.cz - self.row0 * 0.8660254) > 48.0:
            self._rebase()
        ch = float(p["camera_height"])
        eye_y = max(ch / cs, 0.95) + 0.08 * math.sin(0.37 * cph)
        pitch = -math.radians(12.0 + 36.0 * _smooth(0.4, 4.0, ch))
        psil = psi + 0.12 * math.sin(0.23 * cph)
        ox, oz = float(self.col0), self.row0 * 0.8660254
        ex, ez = self.cx - ox, self.cz - oz
        eye = (ex, eye_y, ez)
        tgt = (ex + math.cos(psil) * math.cos(pitch), eye_y + math.sin(pitch), ez + math.sin(psil) * math.cos(pitch))
        cam = camera_pack(eye, tgt, fov_deg=55.0, roll=0.025 * math.sin(0.19 * cph))

        # ---- cell pre-pass parameters
        rowc = int(round(self.cz / 0.8660254))
        colc = int(round(self.cx - 0.5 * (rowc & 1)))
        th = 0.3 + 0.05 * cph
        kw, kw2 = 2.0 * math.pi / 9.0, 2.0 * math.pi / 13.0
        w1 = (kw * math.cos(th), kw * math.sin(th))
        w2 = (kw2 * math.cos(th + 2.1), kw2 * math.sin(th + 2.1))
        Qh[0], Qh[1], Qh[2], Qh[3], Qh[4] = colc, rowc, self.col0, self.row0, self.seed
        Qh[5], Qh[6], Qh[7] = wave_amp, w1[0], w1[1]
        Qh[8] = wrap(self.wph - (ox * w1[0] + oz * w1[1]), 2.0 * math.pi)
        Qh[9], Qh[10] = w2
        Qh[11] = wrap(self.wph2 - (ox * w2[0] + oz * w2[1]), 2.0 * math.pi)
        Qh[12], Qh[13] = idle_amp, self.iph
        Qh[14], Qh[15], Qh[16] = ex, ez, eye_y
        Qh[17] = float(p["down_mix"])
        self.Q.set(Qh)
        launch_2d(self.kc, WIN, WIN, (self.cells, self.Q))

        # ---- main pass
        fog = 0.035 * cs ** 0.6
        top = wave_amp * 1.5 + idle_amp + hmax + CAP_H + 0.02
        rig = rig_pack(key_dir=(0.55, 0.8, -0.3), key_col=(1.45, 1.35, 1.2), fill_col=(0.10, 0.12, 0.17),
                       rim_col=(0.30, 0.33, 0.42), amb_col=(0.025, 0.025, 0.03), spec=1.0, shininess=40.0)
        Ph = self.Ph
        Ph[0:13] = cam
        Ph[13:25] = palette_coeffs(p["palette"])
        Ph[25] = p["palette_period"]
        Ph[26] = self.drift
        Ph[27] = float(p["intensity"]) * 1.1
        Ph[28] = fog
        Ph[29] = p["piston_profile"]
        Ph[30] = p["material"]
        Ph[31] = 0.04 + 0.4 * es
        Ph[32] = k
        Ph[33] = top
        Ph[34] = min(2.3 / fog, 110.0)
        Ph[35] = self.seed
        Ph[36], Ph[37] = self.col0, self.row0
        Ph[40:57] = rig
        self.P.set(Ph)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.cells))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.Q = None
        self.cells = None
