"""Matrix maelstrom: digital-rain glyphs streaming in a swirling Archimedean vortex around a dark central hole.

Source: https://github.com/mrange/windows-terminal-shader-gallery/blob/main/gallery/matrix-maelstrom/fragment-0.hlsl
        (also on Shadertoy as https://www.shadertoy.com/view/dlsGDf, "Matrix Maelstrom")
Author: Mårten Rånge (mrange); helpers by Pascal Gilcher (atan approximation, MIT; not used here),
        Inigo Quilez (2D box SDF, MIT), mercury / hg_sdf (mod1, "MIT OR CC-BY-NC-4.0": taken under MIT)
License: CC0-1.0 AND MIT
license_class: permissive
Ported from HLSL by smfx (ideas/changes: see below)

Licence evidence (fetched 2026-10-14):
  * gallery/matrix-maelstrom/metadata.json: "license-expressions": ["CC0-1.0", "CC-BY-NC-4.0", "MIT"],
    "authors": ["Mårten Rånge", "Inigo Quilez", "mercury", "Pascal Gilcher"].  CC0-1.0 covers mrange's own code;
    CC-BY-NC-4.0 only appears as the alternative of mercury's dual "MIT OR CC-BY-NC-4.0" helper, which we take
    under MIT.
  * In-source helper headers: "License: MIT, author: Pascal Gilcher", "License: MIT OR CC-BY-NC-4.0, author:
    mercury", "License: MIT, author: Inigo Quilez".  Two helpers say "License: Unknown, author: Unknown, found:
    don't remember" (fract(sin()) hashes): they are NOT ported; the lab's own integer hash is used instead.
  * Repository LICENSE (https://github.com/mrange/windows-terminal-shader-gallery/blob/main/LICENSE):

    MIT License

    Copyright (c) 2022 Mårten Rånge

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
    documentation files (the "Software"), to deal in the Software without restriction, including without
    limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the
    Software, and to permit persons to whom the Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be included in all copies or substantial
    portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED
    TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
    THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
    CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
    DEALINGS IN THE SOFTWARE.

What is ported faithfully: the Archimedean spiral domain repetition (spiralMod: arc length along the arm and
offset across it, so glyphs keep a constant size all the way into the hole), rows of glyphs per arm, the analytic
7-segment hex glyphs selected by bit ops (digit(): segment SDFs, the 16-entry segment table, faint unlit
segments), per-row rates, the bright head with a fading trail and a new symbol each time a head passes, the
hole fade, the green colour ramp (bcol, hcol = 1.5 sqrt(bcol) + 0.2, icol = 0.1 bcol) and the final sqrt.

Changes: the C# logo and the terminal-text compositing are dropped.  The two "License: Unknown" fract(sin())
hashes are rewritten as the lab's integer hash (hash1/hash_u).  Glyphs are stacked ALONG the stream (upright in
falling rain) instead of across it, so the vortex reads as Matrix rain bent into a whirlpool.  The centre is a
dark hole with a glowing rim instead of a lit disc.  New: a straight-rain field and a radial reveal between it and
the vortex (swirl_amount); every motion is a host-integrated phase, and the column phases (head position and head
count, glyph glide) are integrated PER COLUMN on the host in float64 and wrapped (fraction + wrapped integer) before
upload, so nothing loses fp32 precision however long the scene runs; heads fade in and out one by one with energy
(each head has its own alive threshold and trail-length jitter); per-cell symbol flicker (glyph_rate); a soft halo
per glyph; palette tint along the trail (tint_mix); and beat-spawned radial waves that run out from the hole.

Macros:
  energy (e' = smoothstep(0.1, 0.85, e)) -> rain fall rate and glyph glide x(0.4 + 1.2e'), vortex rotation rate,
    symbol change rate, glyph brightness (0.6 + 0.4e'), rim glow, and the fraction of live heads (0.42 + 0.58
    e'^1.5, slewed over ~1.5 s; each head fades in on its own threshold, so density never steps).
  pulse (k = pulse (0.3 + 0.7e)) -> a rate kick to the rain fall and glyph glide (rain_kick: phases lurch, never
    jump) and, on the host-detected rising edge (> 0.6 after < 0.3, >= 150 ms apart), a radial wave that runs out
    from the hole rim at its own rate and lights the whole glyph grid as it passes (wave_kick; the same gain also
    flashes the rim and the lit segments, capped at +35 %).
  The vortex rotates clockwise, the same way the glyphs stream along the arms (heads race inward).
  speed scales every phase; palette/palette_period/drift_speed colour the trail when tint_mix > 0 (the rim glow
    always takes the palette tint partially); intensity is output gain.
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

TAU = 2.0 * math.pi
ROWS_PER_ARM = 13            # glyph columns ("rows" in mrange's terms) side by side in one spiral arm
N_RAIN = 512                 # straight-rain columns in the table (>= 2x the widest visible count; index wraps)
N_COLS = N_RAIN + ROWS_PER_ARM
CELL_W = 1.5                 # glyph cell across the stream (glyph is 1 wide + gap), glyph units
PER_MIN, PER_MAX = 20.0, 42.0  # spacing between heads in a column (cells)
WRAP_I = float(1 << 20)      # integer phases wrap here (exact in fp32); a wrap only reshuffles symbols, ~hours apart
N_WAVES = 4
HEAD_RATE = 7.0              # cells / s at e' ~ 0.5 (column multipliers 0.55-1.45)
GLIDE_RATE = 0.9             # cells / s the glyphs themselves slide downstream
ROT_RATE = 0.16              # rad / s of the vortex: clockwise, the same way the glyphs stream along the arms
WAVE_SPEED = 1.15            # screen heights / s (half-heights units: 2.3)
REVEAL_BAND = 0.22           # half-width of the rain <-> vortex blend ring

# P layout (floats)
#  0 dz (glyph unit, screen half-heights) | 1 (unused) | 2 (unused) | 3 flicker phase fraction | 4 vortex rotation
#  5 reveal radius | 6 reveal band | 7 trail length (cells) | 8 hole radius | 9 tint_mix | 10 live-head level
#  11 gain | 12 palette period | 13 drift | 14..25 palette | 26 rim level | 27 arm pitch | 28..35 waves (r, amp)
#  36 wave width | 37 intensity | 38 aspect (W/H) | 39 beat flash on lit glyphs (<= +35 %)
#  40 flicker phase integer part (wrapped at 2^20)
# COLUMN TABLE CT (N_COLS x 8 floats; rows 0..N_RAIN-1 rain columns, then ROWS_PER_ARM vortex rows)
#  0 per (head spacing, cells) | 1 qrel: head phase in the local glyph frame, [0, per) | 2 qcyc: head cycles (int)
#  3 gfr: glide fraction [0, 1) | 4 gint: glide cells (int) | 5 palette offset | 6, 7 unused
_SRC = HELPERS + PALETTE_CUDA + r"""
#define MM_TAU 6.2831853f
#define CW 1.5f
#define CH 2.6f
#define SEG_L 0.72f
#define SEG_T 0.105f

__constant__ unsigned char MM_DIGITS[16] = {
    0x7D, 0x50, 0x4F, 0x57, 0x72, 0x37, 0x3F, 0x51, 0x7F, 0x77, 0x7B, 0x3E, 0x2D, 0x5E, 0x2F, 0x2B };

// mrange's dsegmentx: a capsule segment along x of length dx, half-thickness dy
__device__ __forceinline__ float seg_d(float px, float py, float dx, float dy) {
    px = fabsf(px);
    const float o = 0.5f * fmaxf(dx - dy, 0.f);
    if (px < o) return fabsf(py) - dy;
    const float qx = px - o;
    return sqrtf(qx * qx + py * py) - dy;
}

// mrange's digit(): 7-segment hex glyph, p in glyph units (x in [-0.5, 0.5], y in [-1, 1], y = up).
// Returns coverage of the nearest segment; lit = 1 if that segment is on in `bits`.
__device__ __forceinline__ float glyph7(float px, float py, unsigned int bits, float aa, float& lit) {
    lit = 0.f;
    if (fabsf(px) > 0.5f + SEG_T + aa || fabsf(py) > 1.f + SEG_T + aa) return 0.f;
    float p0y = py - 1.f;
    const float n0 = rintf(p0y);                         // 0 top, -1 middle, -2 bottom
    p0y -= n0;
    const float d0 = seg_d(px, p0y, SEG_L, SEG_T);       // horizontal segments
    const float ax = fabsf(px) - 0.5f, ay = fabsf(py) - 0.5f;
    const float d1 = seg_d(ay, ax, SEG_L, SEG_T);        // vertical segments
    const float d2 = fabsf(px) - fabsf(fabsf(py) - 0.5f);
    const float d = fminf(d0, d1);
    const int sx = (px > 0.f ? 1 : 0) + (py > 0.f ? 2 : 0);
    const int s = d2 > 0.f ? 3 + sx : (int)(-n0);
    lit = (float)((bits >> s) & 1u);
    return smooth(aa, -aa, d);
}

// Inigo Quilez's 2D box SDF (MIT)
__device__ __forceinline__ float box2(float px, float py, float bx, float by) {
    const float dx = fabsf(px) - bx, dy = fabsf(py) - by;
    const float mx = fmaxf(dx, 0.f), my = fmaxf(dy, 0.f);
    return sqrtf(mx * mx + my * my) + fminf(fmaxf(dx, dy), 0.f);
}

__device__ __forceinline__ float3 f3(float a, float b, float c) { return make_float3(a, b, c); }
__device__ __forceinline__ float3 f3mix(float3 a, float3 b, float t) {
    return f3(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t);
}

// One glyph stream field.  across: glyph units across the stream; along: glyph units downstream.
// Column c (rain: a column across the screen; vortex: a row within an arm) reads its host-integrated, wrapped
// phases from the column table CT (layout in the host code).
__device__ float3 stream_field(float across, float along, const float* __restrict__ CT, const int base,
                               const int ncol, unsigned int salt, const float* __restrict__ P, float aa, float wave)
{
    const float c = floorf(across * (1.f / CW) + 0.5f);
    const float lx = across - c * CW;
    int ci = ((int)c) % ncol;
    if (ci < 0) ci += ncol;
    const float* __restrict__ T = CT + 8 * (base + ci);
    const float per = T[0];
    const float ul = along * (1.f / CH) - T[3];          // glyphs glide downstream (fractional part only)
    const float kf = floorf(ul);
    const float ly = (0.5f - (ul - kf)) * CH;            // glyph-local y, up = upstream
    const float cf = (T[1] - kf) / per;
    const float cyl = floorf(cf);
    const float d = (cf - cyl) * per;                    // cells behind the nearest head
    const unsigned int cu = (unsigned int)(base + ci);
    const unsigned int cyc = (unsigned int)((int)cyl + (int)T[2]);   // identity of that head (stable as it moves)
    const unsigned int k = (unsigned int)((int)kf - (int)T[4]);      // identity of this glyph cell
    // per head: alive threshold (density fades heads in and out one by one) and trail-length jitter
    const float ha = hash1(cu ^ salt, cyc, 31u), hl = hash1(cu ^ salt, cyc, 32u);
    const float L = fminf(P[7] * (0.6f + 0.4f * hl), 0.92f * per);
    float tb = clamp01(1.f - d / L);
    tb = tb * tb * (3.f - 2.f * tb);
    const float act = smooth(ha - 0.06f, ha + 0.06f, P[10]);
    const float b = tb * act;
    if (b < 2e-3f && wave < 2e-3f) return f3(0.f, 0.f, 0.f);

    // symbol: a new one each time a head passes, plus random per-cell flicker at glyph_rate.  The flicker phase
    // is split on the host into an integer (P[40], wrapped at 2^20) and a fraction (P[3]); each cell runs at
    // j/10 of it (j = 4..15), evaluated in integer maths so it never loses fp32 precision.
    const float h5 = hash1(cu ^ 0x5bd1e995u, k, salt + 21u);
    const unsigned int j = 4u + min((unsigned int)(12.f * h5), 11u);
    const unsigned int fl = ((unsigned int)P[40] * j + (unsigned int)(P[3] * (float)j + 70.f * h5)) / 10u;
    const unsigned int gid = hash_u(cu * 0x9E3779B1u ^ hash_u(k * 0x85EBCA77u
                                    ^ hash_u(cyc * 0xC2B2AE3Du ^ fl * 0x27d4eb2fu ^ salt)));
    const unsigned int bits = MM_DIGITS[gid & 15u];
    float lit;
    const float cov = glyph7(lx, ly, bits, aa, lit);

    // colour: classic green ramp mixed toward the palette along the trail
    const float3 green = f3(0.f, 0.70710678f, 0.17677670f);
    const float3 pc = pal(d / per * P[12] + P[13] + T[5], P + 14);
    const float3 acol = f3mix(green, f3(pc.x * pc.x, pc.y * pc.y, pc.z * pc.z), P[9]);
    const float3 hcol = f3(clamp01(1.5f * sqrtf(acol.x) + 0.2f), clamp01(1.5f * sqrtf(acol.y) + 0.2f),
                           clamp01(1.5f * sqrtf(acol.z) + 0.2f));
    const float head = fmaxf(smooth(1.6f, 0.f, d), 0.7f * wave);
    const float3 on = f3mix(acol, hcol, head);
    const float g = P[11];
    const float halo = expf(-2.6f * fmaxf(box2(lx, ly, 0.45f, 0.95f), 0.f));
    const float wl = cov * (lit > 0.5f ? 0.9f : 0.22f) + 0.05f * halo;   // the wave lights the glyph grid
    const float sl = cov * (lit > 0.5f ? 1.f + P[39] : 0.08f) * b * g * (1.f + 1.5f * wave);
    const float hlo = halo * b * g * (0.03f + 0.16f * head);
    float3 col;
    col.x = (lit > 0.5f ? on.x : acol.x) * sl + acol.x * (hlo + wl * wave);
    col.y = (lit > 0.5f ? on.y : acol.y) * sl + acol.y * (hlo + wl * wave);
    col.z = (lit > 0.5f ? on.z : acol.z) * sl + acol.z * (hlo + wl * wave);
    return col;
}

// mrange's spiralMod: Archimedean spiral with arm pitch a -> (arc length along the arm, offset across it)
__device__ __forceinline__ float2 spiral_mod(float px, float py, float a) {
    const float b = a / MM_TAU;
    const float r = sqrtf(px * px + py * py);
    const float th = atan2f(py, px);
    float rr = r - th * b;
    const float nn = floorf((rr + 0.5f * a) / a);        // mercury's mod1 (hg_sdf, MIT)
    rr -= nn * a;
    const float sa = th + MM_TAU * nn;
    const float sl = 0.5f * b * (sa * sqrtf(1.f + sa * sa) + asinhf(sa));
    return make_float2(sl, rr);
}

extern "C" __global__
void matrix_maelstrom(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                      const float* __restrict__ CT, const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float px = (x + 0.5f - 0.5f * W) / hh, py = -(y + 0.5f - hh) / hh;   // y up, height 2
    const float r = sqrtf(px * px + py * py);
    const float dz = P[0];
    const float aa = 0.9f / (hh * dz);                   // one pixel in glyph units
    const float hole = P[8];
    const float vis = smooth(0.72f * hole, hole + 0.03f, r);

    float wave = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {
        const float q = (r - P[28 + 2 * i]) / P[36];
        wave = fmaxf(wave, P[29 + 2 * i] * expf(-q * q));
    }
    wave *= vis;

    float3 col = f3(0.f, 0.f, 0.f);
    if (vis > 1e-3f) {
        const float wv = 1.f - smooth(P[5] - P[6], P[5] + P[6], r);
        float3 rain = col, vort = col;
        if (wv < 0.999f)
            rain = stream_field(px / dz, -py / dz, CT, 0, """ + str(N_RAIN) + r""", seed, P, aa, wave);
        if (wv > 1e-3f) {
            const float cr = cosf(P[4]), sr = sinf(P[4]);
            const float2 s = spiral_mod(cr * px - sr * py, sr * px + cr * py, P[27]);
            vort = stream_field(s.y / dz, -s.x / dz, CT, """ + str(N_RAIN) + ", " + str(ROWS_PER_ARM) + r""",
                                seed ^ 0x9E3779B9u, P, aa, wave);
        }
        col = f3mix(rain, vort, wv);
        col.x *= vis; col.y *= vis; col.z *= vis;
    }
    // rim of the hole (palette-tinted) and a faint green haze around it
    const float3 pr = pal(P[13] + 0.5f, P + 14);
    const float tr = fmaxf(P[9], 0.45f);
    const float3 rc = f3mix(f3(0.f, 0.70710678f, 0.17677670f), f3(pr.x * pr.x, pr.y * pr.y, pr.z * pr.z), tr);
    const float rw = 0.03f + 0.08f * hole;
    const float qr = (r - hole) / rw;
    const float rim = P[26] * expf(-qr * qr) + 0.05f * P[26] * expf(-3.f * fmaxf(r - hole, 0.f)) * vis;
    col.x += rc.x * rim; col.y += rc.y * rim; col.z += rc.z * rim;
    // vignette on the unstretched frame
    const float vx = px / P[38], vr = sqrtf(vx * vx + py * py);
    const float vg = smooth(1.75f, 0.45f, vr);
    const float gi = 255.f * P[37];
    out[y * W + x] = pack(make_float4(sqrtf(fmaxf(col.x * vg, 0.f)) * gi, sqrtf(fmaxf(col.y * vg, 0.f)) * gi,
                                      sqrtf(fmaxf(col.z * vg, 0.f)) * gi, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class MatrixMaelstrom(Source):
    name = "matrix_maelstrom"
    label = "Matrix maelstrom"
    description = "Digital-rain glyphs stream in a swirling vortex around a dark central hole: the Matrix as a whirlpool."
    params = {
        "swirl_amount": num(0.0, 1.0, 1.0, "Swirl (straight rain -> vortex)", basic=True),
        "glyph_density": num(20.0, 120.0, 56.0, "Glyph columns per screen height", step=1, basic=True),
        "trail_length": num(0.0, 1.0, 0.55, "Trail length"),
        "hole_radius": num(0.0, 0.5, 0.3, "Hole radius"),
        "glyph_rate": num(0.0, 4.0, 0.8, "Symbol change rate", unit="/s"),
        "tint_mix": num(0.0, 1.0, 0.0, "Tint (classic green -> palette)"),
        "wave_kick": num(0.0, 2.0, 1.0, "Beat wave (pulse gain)"),
        "rain_kick": num(0.0, 3.0, 1.2, "Beat rain surge (pulse gain)"),
        **standard_scene_params(palette="acid", render_scale=0.75, period=0.6, drift=0.03),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, ("--std=c++14", "--use_fast_math")).get_function("matrix_maelstrom")
        self.P = cp.zeros(44, cp.float32)
        self.Ph = np.zeros(44, np.float32)
        rng = np.random.default_rng(int(params["seed"]))
        # per-column state, float64 on the host; uploaded wrapped (fractions + integers < 2^20, exact in fp32)
        self.m = rng.uniform(0.55, 1.45, N_COLS)                     # column rate multiplier
        self.per = rng.uniform(PER_MIN, PER_MAX, N_COLS)             # head spacing (cells)
        self.q = rng.uniform(0.0, 1.0, N_COLS) * self.per            # head phase in the local glyph frame
        self.qcyc = rng.integers(0, int(WRAP_I), N_COLS).astype(np.float64)
        self.g = rng.uniform(0.0, 1.0, N_COLS)                       # glide fraction
        self.gint = rng.integers(0, int(WRAP_I), N_COLS).astype(np.float64)
        self.CTh = np.zeros((N_COLS, 8), np.float32)
        self.CTh[:, 0] = self.per
        self.CTh[:, 5] = rng.uniform(0.0, 0.21, N_COLS)
        self.CT = cp.asarray(self.CTh)
        self.ff = float(rng.uniform(0.0, 1.0))                       # flicker phase: fraction + wrapped integer
        self.fi = float(rng.integers(0, int(WRAP_I)))
        self.rot = float(rng.uniform(0.0, TAU))
        self.drift = 0.0
        self.dens = None                  # slewed e'^1.5 (live-head fraction)
        self.clock = 0.0
        self.armed = True
        self.last_spawn = -1.0
        self.waves = np.zeros((N_WAVES, 2), np.float64)   # (radius, amplitude)
        self.wave_i = 0

    def _advance_columns(self, head_step: float, glide_step: float) -> None:
        """Integrate every column's glide and head phase (cells) and wrap them; identities stay continuous."""
        self.g += glide_step * self.m
        gi = np.floor(self.g)
        self.g -= gi
        self.gint = np.mod(self.gint + gi, WRAP_I)
        self.q += head_step * self.m + gi          # the local glyph frame shifted by gi whole cells
        cq = np.floor(self.q / self.per)
        self.q -= cq * self.per
        self.qcyc = np.mod(self.qcyc + cq, WRAP_I)
        np.clip(self.q, 0.0, self.per, out=self.q)
        self.CTh[:, 1] = self.q
        self.CTh[:, 2] = self.qcyc
        self.CTh[:, 3] = self.g
        self.CTh[:, 4] = self.gint

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = float(p["speed"])
        ep = _smoothstep(0.1, 0.85, e)
        target = ep ** 1.5
        if self.dens is None:
            self.dens = target
        else:
            self.dens += (target - self.dens) * (1.0 - math.exp(-dt / 1.5))
        k = pulse * (0.3 + 0.7 * e)
        rate = 0.4 + 1.2 * ep
        self.clock += dt

        # ---- phases (kicks add to rates only), all integrated here and wrapped before upload
        surge = 1.0 + float(p["rain_kick"]) * k
        self._advance_columns(HEAD_RATE * rate * surge * spd * dt, GLIDE_RATE * rate * surge * spd * dt)
        fl = self.ff + float(p["glyph_rate"]) * rate * spd * dt
        fli = math.floor(fl)
        self.ff = fl - fli
        self.fi = (self.fi + fli) % WRAP_I
        self.rot = (self.rot + ROT_RATE * rate * spd * dt) % TAU
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 1000.0

        # ---- beat waves: host rising-edge detector with hysteresis (fire > 0.6 after < 0.3, >= 150 ms apart)
        hole = float(p["hole_radius"])
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.clock - self.last_spawn >= 0.15:
            self.armed = False
            self.last_spawn = self.clock
            amp = float(p["wave_kick"]) * (0.35 + 0.65 * e)
            if amp > 0.0:
                self.waves[self.wave_i] = (hole, amp)
                self.wave_i = (self.wave_i + 1) % N_WAVES
        if dt > 0:
            self.waves[:, 0] += WAVE_SPEED * 2.0 * (0.75 + 0.5 * ep) * (0.5 + 0.5 * min(spd, 2.0)) * dt
            self.waves[:, 1] *= math.exp(-dt / 0.75)
        self.waves[self.waves[:, 0] > 3.0, 1] = 0.0

        # ---- uniforms
        dens = float(p["glyph_density"])
        dz = 2.0 / (dens * CELL_W)
        s = float(p["swirl_amount"])
        r_lo = hole - REVEAL_BAND
        r_hi = 2.15 + REVEAL_BAND
        Ph = self.Ph
        Ph[0] = dz
        Ph[3] = self.ff
        Ph[4] = self.rot
        Ph[5] = r_lo + (r_hi - r_lo) * s
        Ph[6] = REVEAL_BAND
        Ph[7] = 2.5 + 30.0 * float(p["trail_length"]) ** 1.2
        Ph[8] = max(hole, 0.03)
        Ph[9] = p["tint_mix"]
        Ph[10] = 0.42 + 0.58 * self.dens
        Ph[11] = 0.6 + 0.4 * ep
        Ph[12] = p["palette_period"]
        Ph[13] = self.drift
        Ph[14:26] = palette_coeffs(p["palette"])
        Ph[26] = (0.12 + 0.12 * ep) * (1.0 + 2.0 * float(p["wave_kick"]) * k)
        Ph[27] = ROWS_PER_ARM * CELL_W * dz
        Ph[28:36] = self.waves.reshape(-1)
        Ph[36] = 0.10 + 0.06 * ep
        Ph[37] = p["intensity"]
        Ph[38] = self.ctx.width / self.ctx.height
        Ph[39] = min(0.35, 0.35 * float(p["wave_kick"]) * k)
        Ph[40] = self.fi
        self.P.set(Ph)
        self.CT.set(self.CTh)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.CT,
                                           np.uint32(int(p["seed"]) & 0xFFFFFFFF)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.CT = None
