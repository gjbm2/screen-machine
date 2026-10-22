"""Score ribbon: the one per-pixel pass that draws the whole score (CUDA source and its buffer layout).

Source: Original.  Author: smfx.  License: MIT (this file).  license_class: permissive

Per pixel: the page (paper or night, baked once), the staff lines of the row it is in, then the primitives of its bin.
Bins are the screen's 64-px columns per row, filled on the host each frame (a primitive sits in every bin its box
touches, in either row it shows in), so a pixel tests a dozen boxes and evaluates two or three distances.  Every mark
is a distance in pixels; coverage is clamp(0.5 - d), the engraved score's own edge, and where marks overlap the ink
is their union (max), so nothing depends on drawing order.  A halo in the note's colour uses the same distance.

The rows are one tape folded: row r shows tape v in [-(R-1-r)Wv, -(R-1-r)Wv + Wv) at screen x_left + (v - lo).
A tape primitive's x is v (tape px from the paper's position S); a header primitive (record[1] = its row) is in
screen px.  Crossing a fold a mark fades out of one row as it fades into the next (F px each side).

Records (NF float32 each):
  [0] kind  [1] row (-1: on the tape, else fixed to that row, screen x)  [2] x [3] y (row-local px)
  [4] sx [5] sy (px per staff space)  [6] glyph A [7] glyph B [8] morph A->B  [9] alpha
  [10] pitch class (-1: plain ink)  [11] its note's strike [12] release (s, from the frame's time base; release 1e9
  while held) [13] velocity [14] halo weight  [15..19] kind data  [20..23] box x0 y0 x1 y1
  The kernel colours a note's marks from these each frame: the palette around the circle of fifths while it sounds,
  cooling to ink after release; a halo on a fresh head.
kinds:
  0 GLYPH   origin (x, y), scale (sx, sy), glyphs A/B and the morph
  1 BOX     x0 = x, y0 = y, x1 = [15], y1 = [16]                         (stems, bar lines)
  2 BEAM    x0 = x, centre y at x0 = y, x1 = [15], slope [16], half thickness (vertical) [17]
  3 TIE     p0 = (x, y), p1 = ([15], [16]), sagitta [17] (+ down / - up), thickness at the middle [18]
  4 LEDGERS x0 = x, first line y = y, x1 = [15], step [16], count [17], half thickness [18]
  5 DOT     centre (x, y), radius [15]
"""
from __future__ import annotations

try:
    from .ribbon_glyphs import GT_W, NG, PAD, TPS
except ImportError:                                   # the scratch layout: helpers side by side
    from ribbon_glyphs import GT_W, NG, PAD, TPS

NF = 24
NP = 64
BIN_W = 64
GLYPH, BOX, BEAM, TIE, LEDGERS, DOT = range(6)

# uniforms
U_W, U_H, U_NIGHT, U_SP, U_R, U_Y0, U_PITCH, U_XL, U_WV, U_SX0, U_SX1, U_TH, U_T1, U_T2, U_FOLD = range(15)
U_GAIN, U_BREATH, U_PX, U_PA, U_KICK, U_PR, U_PG, U_PB, U_HALO_R, U_NC, U_BW, U_NREC, U_ROWH = range(15, 28)
U_STAFF_A, U_BG_TINT = 28, 29
U_PAL, U_TNOW, U_PERIOD, U_DRIFT, U_GLOW, U_ENERGY, U_FRESH, U_RELTAU, U_MIXMAX = 32, 44, 45, 46, 47, 48, 49, 50, 51

SRC = r"""
#define NF """ + str(NF) + r"""
#define G_TPS """ + f"{TPS:.1f}f" + r"""
#define G_PAD """ + f"{PAD:.3f}f" + r"""
#define G_FAR 1.0e4f
#define NG """ + str(NG) + r"""
__constant__ float c_GT[""" + str(NG * GT_W) + r"""];

// the page's grain, fibre and vignette, baked once in setup (as live_score's)
extern "C" __global__
void ribbon_bake_page(unsigned char* __restrict__ page, const int W, const int H, const int night,
                      const unsigned int seed)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f, u = X / W, v = Y / H;
    const float grain = fbm(u * 9.f, v * 5.f, seed + 3u) - 0.5f;
    const float fibre = vnoise(X * 0.35f, Y * 0.02f + 3.f, seed + 9u) - 0.5f;
    const float vig = 1.f - 0.35f * ((u - 0.5f) * (u - 0.5f) * 1.6f + (v - 0.5f) * (v - 0.5f) * 2.f);
    const float k = night ? (0.9f + 0.25f * grain) * vig : (0.93f + 0.05f * grain + 0.015f * fibre) * vig;
    page[y * W + x] = (unsigned char)(clamp01(k / 1.2f) * 255.f + 0.5f);
}

// a glyph's signed distance (sp) and its gradient (per sp) at (u, v) sp from its origin; G_FAR outside its rect
__device__ __forceinline__ float3 glyph_sd(const float* __restrict__ A, const int AW, const int g, const float u,
                                          const float v)
{
    const float* T = c_GT + 12 * g;
    const float tx = T[4] + u * G_TPS - 0.5f, ty = T[5] + v * G_TPS - 0.5f;
    if (tx < 0.f || ty < 0.f || tx >= T[2] - 1.f || ty >= T[3] - 1.f) return make_float3(G_FAR, 0.f, 0.f);
    const int ix = (int)tx, iy = (int)ty;
    const float fx = tx - ix, fy = ty - iy;
    const float* a = A + ((int)T[1] + iy) * AW + (int)T[0] + ix;
    const float c00 = __ldg(a), c10 = __ldg(a + 1), c01 = __ldg(a + AW), c11 = __ldg(a + AW + 1);
    const float top = c00 + (c10 - c00) * fx, bot = c01 + (c11 - c01) * fx;
    return make_float3(top + (bot - top) * fy, ((c10 - c00) * (1.f - fy) + (c11 - c01) * fy) * G_TPS,
                       (bot - top) * G_TPS);
}

// sp distance + gradient -> px under the glyph's (sx, sy) scale (exact for a true distance field, |grad| = 1)
__device__ __forceinline__ float sd_px(const float3 s, const float sx, const float sy) {
    if (s.x >= G_FAR) return G_FAR;
    const float gx = s.y / sx, gy = s.z / sy;
    return s.x * rsqrtf(fmaxf(gx * gx + gy * gy, 1e-6f));
}

__device__ __forceinline__ float box_d(const float dx, const float dy) {      // signed slab distances -> box
    return dx > 0.f && dy > 0.f ? sqrtf(dx * dx + dy * dy) : fmaxf(dx, dy);
}

__device__ float prim_d(const float* __restrict__ Q, const float px, const float py,
                        const float* __restrict__ A, const int AW)
{
    const int kind = (int)Q[0];
    if (kind == 0) {                                              // GLYPH, morphing A -> B
        const float sx = Q[4], sy = Q[5];
        const float u = (px - Q[2]) / sx, v = (py - Q[3]) / sy;
        const int ga = (int)Q[6], gb = (int)Q[7];
        const float mt = Q[8];
        float3 s = mt < 0.999f ? glyph_sd(A, AW, ga, u, v) : make_float3(G_FAR, 0.f, 0.f);
        if (mt > 0.001f && gb >= 0) {
            const float3 t = glyph_sd(A, AW, gb, u, v);
            if (mt >= 0.999f) s = t;
            else if (s.x >= G_FAR || t.x >= G_FAR) s = (mt < 0.5f) ? s : t;
            else s = make_float3(s.x + (t.x - s.x) * mt, s.y + (t.y - s.y) * mt, s.z + (t.z - s.z) * mt);
        }
        return sd_px(s, sx, sy);
    }
    if (kind == 1)                                                // BOX
        return box_d(fmaxf(Q[2] - px, px - Q[15]), fmaxf(Q[3] - py, py - Q[16]));
    if (kind == 2) {                                              // BEAM: vertical ends, vertical thickness
        const float yc = Q[3] + Q[16] * (px - Q[2]);
        return box_d(fmaxf(Q[2] - px, px - Q[15]), (fabsf(py - yc) - Q[17]) * rsqrtf(1.f + Q[16] * Q[16]));
    }
    if (kind == 3) {                                              // TIE: the lens between two arcs on one chord
        const float ax = Q[2], ay = Q[3], bx = Q[15], by = Q[16];
        const float dx = bx - ax, dy = by - ay;
        const float L = sqrtf(dx * dx + dy * dy);
        if (L < 0.5f) return G_FAR;
        const float tx = dx / L, ty = dy / L;
        const float nx = -ty, ny = tx;                            // the bulge side is +n for sagitta > 0
        const float hc = 0.5f * L;
        const float s1 = Q[17], s2 = Q[17] - copysignf(fminf(Q[18], fabsf(Q[17]) * 0.9f), Q[17]);
        // local coordinates: along the chord from its middle, across it toward the bulge
        const float lx = (px - ax) * tx + (py - ay) * ty - hc;
        float ly = (px - ax) * nx + (py - ay) * ny;
        const float sg = s1 >= 0.f ? 1.f : -1.f;
        ly *= sg;
        const float S1 = fabsf(s1), S2 = fmaxf(fabsf(s2), 1e-3f);
        const float R1 = (hc * hc + S1 * S1) / (2.f * S1), R2 = (hc * hc + S2 * S2) / (2.f * S2);
        const float d1 = sqrtf(lx * lx + (ly - (S1 - R1)) * (ly - (S1 - R1))) - R1;   // centre at S - R across
        const float d2 = sqrtf(lx * lx + (ly - (S2 - R2)) * (ly - (S2 - R2))) - R2;
        return fmaxf(fmaxf(d1, -d2), -ly);
    }
    if (kind == 4) {                                              // LEDGERS
        const int n = (int)Q[17];
        if (n <= 0) return G_FAR;
        const float step = Q[16];
        const float k = fminf(fmaxf(rintf((py - Q[3]) / step), 0.f), (float)(n - 1));
        return box_d(fmaxf(Q[2] - px, px - Q[15]), fabsf(py - (Q[3] + k * step)) - Q[18]);
    }
    if (kind == 5) {                                              // DOT
        const float dx = px - Q[2], dy = py - Q[3];
        return sqrtf(dx * dx + dy * dy) - Q[15];
    }
    return G_FAR;
}

// a note's colour and its halo, from the frame's uniforms
// the palette's hue at the note's place on the circle of fifths, at full strength (a palette's dark shades would
// sink a sounding note into the night; the look then sets how bright it is drawn)
__device__ __forceinline__ float3 note_rgb(const float* __restrict__ P, const float pc) {
    const float t = (float)(((int)pc * 7) % 12) * (1.f / 12.f) * P[45] + P[46];
    float3 c = make_float3(clamp01(P[32] + P[35] * cosf(6.2831853f * (P[38] * t + P[41]))),
                           clamp01(P[33] + P[36] * cosf(6.2831853f * (P[39] * t + P[42]))),
                           clamp01(P[34] + P[37] * cosf(6.2831853f * (P[40] * t + P[43]))));
    const float m = fmaxf(fmaxf(c.x, c.y), fmaxf(c.z, 0.05f));
    return make_float3(c.x / m, c.y / m, c.z / m);
}
__device__ __forceinline__ float note_mix(const float* __restrict__ P, const float ton, const float toff) {
    if (ton < -1e8f) return 0.f;
    const float t = P[44];
    return P[51] * (toff > t ? 1.f : __expf(-(t - toff) / P[50]));
}

// P: see ribbon_kernel.py (U_*).  bins: off[R * NC + 1], idx[]; recs: NF floats each; A: the atlas (sp).
// Launch: 32 x 8 blocks; rows start and step on multiples of 8 px and bins are 64 px wide, so a block lies in one row
// band and one bin: its threads first gather the bin's records whose boxes touch the block into shared memory (a
// handful), then every pixel loops over those.
#define MAXC 64
extern "C" __global__ void __launch_bounds__(256)
ribbon(uchar4* __restrict__ out, const float* __restrict__ P, const int* __restrict__ cnt,
       const int* __restrict__ lst, const float* __restrict__ recs, const float* __restrict__ A, const int AW,
       const unsigned char* __restrict__ page, const int bin_cap)
{
    __shared__ float s_rec[MAXC * NF];
    __shared__ int s_n;
    const int W = (int)P[0], H = (int)P[1];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const float X = x + 0.5f, Y = y + 0.5f;
    const int R = (int)P[4];
    const float pitch = P[6];
    const float by0 = blockIdx.y * blockDim.y + 0.5f;
    const int r = (int)floorf((by0 - P[5]) / pitch);                 // the block's row band (all 8 lines in it)
    const float lyb = by0 - (P[5] + r * pitch);
    const bool in_row = r >= 0 && r < R && lyb < P[27];
    const float XL = P[7], WV = P[8], F = P[14];
    const float bx0 = blockIdx.x * blockDim.x, bx1 = bx0 + blockDim.x;
    if (tid == 0) s_n = 0;
    __syncthreads();
    bool overflow = false;
    int i0 = 0, i1 = 0;
    if (in_row) {
        const int NC = (int)P[24];
        const int col = min(max((int)((bx0 + 0.5f) / P[25]), 0), NC - 1);
        const int b = r * NC + col;
        i0 = b * bin_cap; i1 = i0 + cnt[b];
        const float vb0 = bx0 - XL - (R - 1 - r) * WV, vb1 = vb0 + blockDim.x;
        const float ly0 = lyb - 0.5f, ly1 = ly0 + blockDim.y;
        const bool tape_any = bx1 > XL && bx0 < XL + WV;
        for (int i = i0 + tid; i < i1; i += 256) {
            const float* Q = recs + NF * lst[i];
            const int row = (int)Q[1];
            float a0, a1;
            if (row < 0) { if (!tape_any) continue; a0 = vb0; a1 = vb1; }
            else { if (row != r) continue; a0 = bx0; a1 = bx1; }
            if (Q[9] <= 0.002f || Q[22] < a0 || Q[20] > a1 || Q[23] < ly0 || Q[21] > ly1) continue;
            const int k = atomicAdd(&s_n, 1);
            if (k < MAXC) for (int j = 0; j < NF; ++j) s_rec[k * NF + j] = Q[j];
        }
    }
    __syncthreads();
    const int nc = s_n;
    if (nc > MAXC) overflow = true;
    if (x >= W || y >= H) return;
    const int night = (int)P[2];
    const float sp = P[3];
    // the page (P[16]: its slow breath, a gain near 1)
    const float k = page[y * W + x] * (1.2f / 255.f) * P[16];
    float3 inkc, c;
    if (!night) { inkc = make_float3(0.11f, 0.085f, 0.07f); c = make_float3(0.95f * k, 0.905f * k, 0.80f * k); }
    else { inkc = make_float3(0.92f, 0.82f, 0.58f);
           c = make_float3(0.028f * k, 0.034f * k, 0.055f * k + 0.01f * (1.f - Y / H)); }
    if (in_row) {
        const float ly = Y - (P[5] + r * pitch);
        if (ly < P[27]) {
            const float th = P[11], t1 = P[12], t2 = P[13];
            float cov = 0.f, best = 0.f, bpc = -1.f, bton = -1e9f, btoff = 1e9f;
            float3 glow = make_float3(0.f, 0.f, 0.f);
            // staff lines: two staves of five
            if (X >= P[9] && X <= P[10]) {
                const float ends = clamp01(X - P[9]) * clamp01(P[10] - X);
                const float isp = 1.f / sp;
                for (int st = 0; st < 2; ++st) {
                    const float d0 = ly - (st ? t2 : t1);
                    if (d0 < -th - 1.f || d0 > 4.f * sp + th + 1.f) continue;
                    const float m = d0 - sp * fminf(fmaxf(rintf(d0 * isp), 0.f), 4.f);
                    cov = fmaxf(cov, clamp01(0.5f * th + 0.5f - fabsf(m)) * P[28] * ends);
                }
            }
            // the playhead: a soft column of light on the last row, where the notes are born
            if (r == R - 1 && P[18] > 0.f && fabsf(X - P[17]) < 3.f * sp) {
                const float dx = (X - P[17]) / (0.9f * sp);
                const float yy = ly < t1 - 2.f * sp ? (t1 - 2.f * sp - ly) / (2.f * sp)
                                 : (ly > t2 + 6.f * sp ? (ly - t2 - 6.f * sp) / (2.f * sp) : 0.f);
                const float g = P[18] * __expf(-dx * dx - yy * yy) * (1.f + 0.6f * P[19]);
                glow.x += P[20] * g; glow.y += P[21] * g; glow.z += P[22] * g;
            }
            const bool tape = X >= XL && X < XL + WV;
            const float v = X - XL - (R - 1 - r) * WV;
            float fa = 0.f;
            if (tape) {
                fa = clamp01((X - XL) / F);
                if (r < R - 1) fa *= clamp01((XL + WV - X) / F);
            }
            const float HR = P[23];
            const int n = overflow ? i1 - i0 : nc;
            for (int i = 0; i < n; ++i) {
                const float* Q = overflow ? recs + NF * lst[i0 + i] : s_rec + NF * i;
                const int row = (int)Q[1];
                float px, a = Q[9];
                if (row < 0) { if (!tape) continue; px = v; a *= fa; }
                else { if (row != r) continue; px = X; }
                if (a <= 0.002f || px < Q[20] || px > Q[22] || ly < Q[21] || ly > Q[23]) continue;
                const float d = prim_d(Q, px, ly, A, AW);
                const float cv = clamp01(0.5f - d) * a;
                if (cv > best) { best = cv; bpc = Q[10]; bton = Q[11]; btoff = Q[12]; }
                if (Q[14] > 0.f && Q[10] >= 0.f && d < 3.f * HR) {
                    const float age = P[44] - Q[11];
                    const float m = note_mix(P, Q[11], Q[12]);
                    const float h = Q[14] * P[47] * ((0.55f + 0.6f * P[48]) * (1.f + 0.6f * P[19]) * (0.5f + 0.6f * Q[13])
                                                     * __expf(-fmaxf(age, 0.f) / P[49]) + 0.25f * m);
                    if (h > 0.003f) {
                        const float g = h * a * __expf(-fmaxf(d, 0.f) / HR) * (1.f - smooth(2.f * HR, 3.f * HR, d));
                        const float3 rgb = note_rgb(P, Q[10]);
                        glow.x += rgb.x * g; glow.y += rgb.y * g; glow.z += rgb.z * g;
                    }
                }
            }
            // halo, then the ink (staff lines and marks: one union)
            if (night) { c.x += glow.x; c.y += glow.y; c.z += glow.z; }
            else {
                const float gm = fmaxf(glow.x, fmaxf(glow.y, glow.z));
                const float gl = fminf(gm, 1.f);
                if (gl > 0.f) {
                    const float s = 1.f / fmaxf(gm, 1e-4f);
                    c.x += (glow.x * s * 0.85f - c.x) * gl * 0.55f; c.y += (glow.y * s * 0.85f - c.y) * gl * 0.55f;
                    c.z += (glow.z * s * 0.85f - c.z) * gl * 0.55f;
                }
            }
            if (best > 0.f) {
                const float mix = bpc >= 0.f ? note_mix(P, bton, btoff) : 0.f;
                const float3 acc = mix > 0.003f ? note_rgb(P, bpc) : make_float3(0.f, 0.f, 0.f);
                const float kk = night ? 0.97f : 0.52f;      // on paper a coloured ink is a dark one
                const float3 col3 = make_float3(inkc.x + (acc.x * kk - inkc.x) * mix, inkc.y + (acc.y * kk - inkc.y) * mix,
                                                inkc.z + (acc.z * kk - inkc.z) * mix);
                c.x += (col3.x - c.x) * best; c.y += (col3.y - c.y) * best; c.z += (col3.z - c.z) * best;
            }
            const float sc = fmaxf(cov - best, 0.f);           // the staff lines where no mark already inked
            if (sc > 0.f) { c.x += (inkc.x - c.x) * sc; c.y += (inkc.y - c.y) * sc; c.z += (inkc.z - c.z) * sc; }
        }
    }
    const float g = 255.f * P[15];
    out[y * W + x] = pack(make_float4(clamp01(c.x) * g, clamp01(c.y) * g, clamp01(c.z) * g, 255.f));
}
"""
