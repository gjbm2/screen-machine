"""Score ribbon: the marks on stage, animated on the GPU.

Source: Original.  Author: smfx.  License: MIT (this file).  license_class: permissive

One object per mark, keyed as the layout keys it, from its birth to its death.  The host changes a mark only when
its target changes (a layout pass, a written bar, a death), by writing closed-form motion parameters into a table:
a hop (minimum-jerk quintic from where it is, keeping its velocity, to the new target), an alpha ease, a scale
spring (underdamped, solved exactly: the birth pop, the pulse as a head changes form), a glyph morph (signed
distances blended), eases for accidentals, dots, octave marks, stem direction and length, flags and beams.  The
tables go to the GPU only when they change; each frame small kernels evaluate every mark at the frame's time, draw
the dependents from their heads as they are now (stems from their chord's heads, beam lines from their stems, ties
between two heads, ledger lines from where a head is), write the primitive records (ribbon_kernel's layout) and
bin them for the drawing pass.  The host's per-frame work is a uniform upload and the launches.

Tables (float32, a row per mark, columns below), records in fixed slots: a head owns 5 (head, ledger lines,
accidental, dot, octave mark), a stem 2 (stem, flag), a beam segment, a tie, a rest 2 (rest, dot), a bar line 2
(thin, thick), a signature digit 1, the systems' headers a block of their own.
"""
from __future__ import annotations

import math

import numpy as np

try:
    from . import ribbon_glyphs as RG, ribbon_kernel as RK, ribbon_layout as RL, ribbon_rec as RR
except ImportError:
    import ribbon_glyphs as RG
    import ribbon_kernel as RK
    import ribbon_layout as RL
    import ribbon_rec as RR

GI = RG.GI
NF = RK.NF
HEAD_W = {n: RG.box(n)[2] for n, _c in RG.GLYPHS}

# capacities (degrade: the oldest marks go first when a table is full)
CAP = {"heads": 2048, "stems": 1024, "groups": 512, "segs": 1024, "ties": 768, "rests": 768, "bars": 384, "sigs": 192}
N_HDR = 256
SLOTS = {"heads": 5, "stems": 2, "segs": 1, "ties": 1, "rests": 2, "bars": 2, "sigs": 1}
BIN_CAP = 192
# motion constants (the kernels have the same numbers)
K_IN, K_OUT = 30.0, 9.0            # alpha eases in / out (1/s)
SPR_W, SPR_Z = 26.0, 0.5           # the scale spring
MORPH_R = 7.0                      # a glyph morph runs 1/7 s
K_EASE = 14.0
STEM_HW, LEDGER_EXT, LEDGER_HT, BEAM_HT, BAR_HT = 0.06, 0.4, 0.08, 0.25, 0.08

# -- columns ------------------------------------------------------------------------------------------------------------
# heads (stride 48)
H_ALIVE, H_X0, H_VX, H_X1, H_TX, H_TTX, H_ARC = 0, 1, 2, 3, 4, 5, 6
H_Y0, H_VY, H_Y1, H_TY, H_TTY = 7, 8, 9, 10, 11
H_A0, H_AT, H_TA = 12, 13, 14
H_S0, H_SV, H_ST, H_TS = 15, 16, 17, 18
H_GA, H_GB, H_TM = 19, 20, 21
H_ACG, H_ACGB, H_ACTM, H_ACS0, H_ACST, H_ACTS, H_ACX0, H_ACXT, H_ACTX = 22, 23, 24, 25, 26, 27, 28, 29, 30
H_DA0, H_DAT, H_DTA, H_DDX, H_DDY = 31, 32, 33, 34, 35
H_OG, H_OA0, H_OAT, H_OTA, H_OUP = 36, 37, 38, 39, 40
H_PC, H_TON, H_TOFF, H_VEL = 41, 42, 43, 44
HS = 48
# stems (stride 24)
S_ALIVE, S_LEAD, S_MOFF, S_MCNT = 0, 1, 2, 3
S_D0, S_DT, S_TD, S_L0, S_LT, S_TL, S_F0, S_FT, S_TF, S_B0, S_BT, S_TB = 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
S_FG, S_FGB, S_TFM, S_GROUP = 16, 17, 18, 19
SS = 24
# beam groups (stride 8), segments (stride 16)
G_ALIVE, G_MOFF, G_MCNT, G_L = 0, 1, 2, 3
GS = 8
B_ALIVE, B_GROUP, B_FIRST, B_LAST, B_LEVEL, B_LET, B_G0, B_GT, B_TG = 0, 1, 2, 3, 4, 5, 6, 7, 8
BS = 16
# ties (stride 16)
T_ALIVE, T_H0, T_H1, T_U0, T_UT, T_TU, T_BORN, T_A0, T_AT, T_TA = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9
TS = 16
# rests (stride 24): a hop, alpha, spring, morph, dot
R_ALIVE, R_X0, R_VX, R_X1, R_TX, R_TTX, R_ARC, R_Y0, R_VY, R_Y1, R_TY, R_TTY = range(12)
R_A0, R_AT, R_TA, R_S0, R_SV, R_ST, R_TS, R_GA, R_GB, R_TM, R_DOT = range(12, 23)
RS = 24
# bar lines (stride 16)
L_ALIVE, L_X0, L_VX, L_X1, L_TX, L_TTX, L_ARC, L_BORN, L_A0, L_AT, L_TA, L_KIND = range(12)
LS = 16
# signature digits (stride 16)
Q_ALIVE, Q_X0, Q_VX, Q_X1, Q_TX, Q_TTX, Q_ARC, Q_Y, Q_A0, Q_AT, Q_TA, Q_GA, Q_GB, Q_TF, Q_BORN = range(15)
QS = 16
STRIDE = {"heads": HS, "stems": SS, "groups": GS, "segs": BS, "ties": TS, "rests": RS, "bars": LS, "sigs": QS}

PREP_SRC = r"""
#define NF """ + str(NF) + r"""
#define HS """ + str(HS) + r"""
#define SS """ + str(SS) + r"""
#define GS """ + str(GS) + r"""
#define BS """ + str(BS) + r"""
#define TS """ + str(TS) + r"""
#define RS """ + str(RS) + r"""
#define LS """ + str(LS) + r"""
#define QS """ + str(QS) + r"""
#define BIN_CAP """ + str(BIN_CAP) + r"""
#define PI_F 3.14159265f
__constant__ float c_GB[""" + str(RG.NG * 4) + r"""];       // glyph boxes (sp, y down, padded)

// U: [0] t (s from the time base) [1] dS (the paper less the tape's origin, px) [2] sp [3] t1 [4] t2
//    [5] flourish [6] halo px [7] glyph id of flag8thUp [8] of flag16thUp ... see Stage.frame
__device__ __forceinline__ float qhop(const float p0, const float v0, const float p1, const float t0, const float T,
                                      const float t, float& u) {
    u = T > 1e-6f ? fminf(fmaxf((t - t0) / T, 0.f), 1.f) : 1.f;
    const float u2 = u * u, u3 = u2 * u, u4 = u3 * u, u5 = u4 * u;
    return p0 * (1.f - 10.f * u3 + 15.f * u4 - 6.f * u5) + T * v0 * (u - 6.f * u3 + 8.f * u4 - 3.f * u5)
           + p1 * (10.f * u3 - 15.f * u4 + 6.f * u5);
}
__device__ __forceinline__ float ease(const float v0, const float vt, const float t0, const float k, const float t) {
    return vt + (v0 - vt) * __expf(-k * fmaxf(t - t0, 0.f));
}
__device__ __forceinline__ float alpha_at(const float a0, const float at, const float ta, const float t) {
    return ease(a0, at, ta, at > a0 ? """ + f"{K_IN:.1f}f" + r""" : """ + f"{K_OUT:.1f}f" + r""", t);
}
__device__ __forceinline__ float spring(const float s0, const float sv0, const float st, const float t0,
                                        const float t) {
    const float w = """ + f"{SPR_W:.1f}f" + r""", z = """ + f"{SPR_Z:.2f}f" + r""";
    const float wd = w * sqrtf(1.f - z * z);
    const float tau = fmaxf(t - t0, 0.f);
    const float c1 = s0 - st, c2 = (sv0 + z * w * c1) / wd;
    return st + __expf(-z * w * tau) * (c1 * cosf(wd * tau) + c2 * sinf(wd * tau));
}
__device__ __forceinline__ void dead(float* R) { R[9] = 0.f; R[20] = 1e9f; R[22] = -1e9f; }
__device__ __forceinline__ void colour(float* R, const float pc, const float ton, const float toff, const float vel,
                                       const float hw) {
    R[10] = pc; R[11] = ton; R[12] = toff; R[13] = vel; R[14] = hw;
}
__device__ __forceinline__ void ink(float* R) { R[10] = -1.f; R[11] = -1e9f; R[12] = 1e9f; R[13] = 0.f; R[14] = 0.f; }
__device__ void glyph_rec(float* R, const float x, const float y, const int ga, const int gb, const float mt,
                          const float sx, const float sy, const float a, const float pad) {
    R[0] = 0.f; R[1] = -1.f; R[2] = x; R[3] = y; R[4] = sx; R[5] = sy; R[6] = (float)ga; R[7] = (float)gb; R[8] = mt;
    R[9] = a;
    float bx0 = c_GB[4 * ga], by0 = c_GB[4 * ga + 1], bx1 = c_GB[4 * ga + 2], by1 = c_GB[4 * ga + 3];
    if (gb >= 0) { bx0 = fminf(bx0, c_GB[4 * gb]); by0 = fminf(by0, c_GB[4 * gb + 1]);
                   bx1 = fmaxf(bx1, c_GB[4 * gb + 2]); by1 = fmaxf(by1, c_GB[4 * gb + 3]); }
    R[20] = x + fminf(bx0 * sx, bx1 * sx) - pad; R[22] = x + fmaxf(bx0 * sx, bx1 * sx) + pad;
    R[21] = y + fminf(by0 * sy, by1 * sy) - pad; R[23] = y + fmaxf(by0 * sy, by1 * sy) + pad;
}
__device__ void box_rec(float* R, const float x0, const float y0, const float x1, const float y1, const float a) {
    R[0] = 1.f; R[1] = -1.f; R[2] = x0; R[3] = y0; R[15] = x1; R[16] = y1; R[9] = a;
    R[20] = x0 - 1.5f; R[21] = y0 - 1.5f; R[22] = x1 + 1.5f; R[23] = y1 + 1.5f;
}

// heads: position, life, morph; their accidental, dot, ledger lines and octave mark.  HC (8 a head): x (from the
// paper) y alpha scale width(sp) and the note's colour (pc, strike, release) for the marks hanging off it
extern "C" __global__ void prep_heads(const float* __restrict__ HD, float* __restrict__ HC, float* __restrict__ REC,
                                      const float* __restrict__ U, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* E = HD + (size_t)i * HS;
    float* C = HC + i * 8;
    float* R = REC + (size_t)i * 5 * NF;
    if (E[0] < 0.5f) { C[2] = 0.f; for (int k = 0; k < 5; ++k) dead(R + k * NF); return; }
    const float t = U[0], dS = U[1], sp = U[2], t1 = U[3], t2 = U[4], hpx = U[6];
    float ux, uy;
    const float x = qhop(E[1], E[2], E[3], E[4], E[5], t, ux) - dS;
    const float y = qhop(E[7], E[8], E[9], E[10], E[11], t, uy) - E[6] * sinf(PI_F * ux) * (ux < 1.f ? 1.f : 0.f);
    const float a = fminf(fmaxf(alpha_at(E[12], E[13], E[14], t), 0.f), 1.f);
    const float s = spring(E[15], E[16], E[17], E[18], t);
    const int ga = (int)E[19], gb = (int)E[20];
    const float mt = gb >= 0 ? fminf(fmaxf((t - E[21]) * """ + f"{MORPH_R:.1f}f" + r""", 0.f), 1.f) : 0.f;
    const float hwa = c_GB[4 * ga + 2] - 0.08f, hwb = gb >= 0 ? c_GB[4 * gb + 2] - 0.08f : hwa;
    const float hw = hwa + (hwb - hwa) * mt;
    const float pc = E[41], ton = E[42], toff = E[43], vel = E[44];
    C[0] = x; C[1] = y; C[2] = a; C[3] = s; C[4] = hw; C[5] = pc; C[6] = ton; C[7] = toff;
    // the head, scaled about its centre, with its halo
    glyph_rec(R, x + (1.f - s) * 0.5f * hw * sp, y, ga, gb, mt, s * sp, s * sp, a, 1.5f + 3.f * hpx);
    colour(R, pc, ton, toff, vel, 1.f);
    // ledger lines from the nearer staff to the head, as it is now
    float* L = R + NF;
    const float top = y < 0.5f * (t1 + 4.f * sp + t2) ? t1 : t2;
    if (y <= top - 0.75f * sp || y >= top + 4.75f * sp) {
        const bool ab = y <= top - 0.75f * sp;
        const float cnt = ab ? floorf((top - y) / sp + 0.25f) : floorf((y - top - 4.f * sp) / sp + 0.25f);
        const float y0 = ab ? top - sp : top + 5.f * sp, step = ab ? -sp : sp;
        const float x0 = x - """ + f"{LEDGER_EXT}f" + r""" * sp, x1 = x + (hw + """ + f"{LEDGER_EXT}f" + r""") * sp;
        const float ht = """ + f"{LEDGER_HT}f" + r""" * sp, yl = y0 + step * fmaxf(cnt - 1.f, 0.f);
        L[0] = 4.f; L[1] = -1.f; L[2] = x0; L[3] = y0; L[15] = x1; L[16] = step; L[17] = cnt; L[18] = ht; L[9] = a;
        ink(L);
        L[20] = x0 - 1.5f - ht; L[22] = x1 + 1.5f + ht; L[21] = fminf(y0, yl) - 1.5f - ht; L[23] = fmaxf(y0, yl) + 1.5f + ht;
    } else dead(L);
    // the accidental: pops in and out, glides between columns, morphs between signs
    float* Ac = R + 2 * NF;
    const float ks = ease(E[25], E[26], E[27], """ + f"{K_EASE:.1f}f" + r""", t);
    if (ks > 0.02f && E[22] >= 0.f) {
        const float dx = ease(E[28], E[29], E[30], """ + f"{K_EASE:.1f}f" + r""", t);
        const int aa = (int)E[22], ab = (int)E[23];
        const float am = ab >= 0 ? fminf(fmaxf((t - E[24]) * """ + f"{MORPH_R:.1f}f" + r""", 0.f), 1.f) : 0.f;
        glyph_rec(Ac, x + dx * sp + (1.f - ks) * 0.4f * sp, y, aa, ab, am, ks * sp, ks * sp, a, 1.5f);
        colour(Ac, pc, ton, toff, vel, 0.f);
    } else dead(Ac);
    float* Dt = R + 3 * NF;
    const float da = ease(E[31], E[32], E[33], """ + f"{K_EASE:.1f}f" + r""", t);
    if (da > 0.02f) {
        const float cx = x + E[34] * sp + 0.21f * sp, cy = y + E[35] * sp, r = 0.2f * sp;
        Dt[0] = 5.f; Dt[1] = -1.f; Dt[2] = cx; Dt[3] = cy; Dt[15] = r; Dt[9] = a * da;
        colour(Dt, pc, ton, toff, vel, 0.f);
        Dt[20] = cx - r - 1.5f; Dt[22] = cx + r + 1.5f; Dt[21] = cy - r - 1.5f; Dt[23] = cy + r + 1.5f;
    } else dead(Dt);
    float* Ot = R + 4 * NF;
    const float oa = ease(E[37], E[38], E[39], """ + f"{K_EASE:.1f}f" + r""", t);
    if (oa > 0.02f && E[36] >= 0.f) {
        const float oy = E[40] > 0.5f ? fminf(y, t1 - 2.f * sp) - 1.6f * sp : fmaxf(y, t2 + 6.f * sp) + 2.4f * sp;
        glyph_rec(Ot, x - 0.2f * sp, oy, (int)E[36], -1, 0.f, 0.8f * sp, 0.8f * sp, a * oa, 1.5f);
        ink(Ot);
    } else dead(Ot);
}

// stems, first half: from their heads as they are now.  SC (8 a stem): centre x, top head y, bottom head y, the end
// on the heads, the free end, direction, alpha, beam weight
extern "C" __global__ void prep_stems(const float* __restrict__ ST, const int* __restrict__ SM,
                                      const float* __restrict__ HC, float* __restrict__ SC,
                                      const float* __restrict__ U, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* E = ST + (size_t)i * SS;
    float* C = SC + i * 8;
    if (E[0] < 0.5f) { C[6] = 0.f; return; }
    const float t = U[0], sp = U[2];
    const int m0 = (int)E[2], mc = (int)E[3];
    float ymin = 1e9f, ymax = -1e9f, amax = 0.f;
    for (int k = 0; k < mc; ++k) {
        const float* h = HC + SM[m0 + k] * 8;
        ymin = fminf(ymin, h[1]); ymax = fmaxf(ymax, h[1]); amax = fmaxf(amax, h[2]);
    }
    const float* lh = HC + (int)E[1] * 8;
    const float d = ease(E[4], E[5], E[6], 10.f, t), L = ease(E[7], E[8], E[9], 22.f, t) * sp;
    const float w = 0.5f * (d + 1.f);
    const float sxc = lh[0] + (""" + f"{STEM_HW}f" + r""" + (lh[4] - 2.f * """ + f"{STEM_HW}f" + r""") * w) * sp;
    C[0] = sxc; C[1] = ymin; C[2] = ymax;
    C[3] = (ymax - 0.16f * sp) * w + (ymin + 0.16f * sp) * (1.f - w);
    C[4] = (ymin - L) * w + (ymax + L) * (1.f - w);
    C[5] = d; C[6] = mc > 0 ? amax : 0.f; C[7] = ease(E[13], E[14], E[15], 12.f, t);
}

// beam groups: the line through their stems' free ends (slope capped, flat when an inner note reaches further),
// placed so no stem is shorter than the group's length.  GC (4): c, slope, up
extern "C" __global__ void prep_groups(const float* __restrict__ GT, const int* __restrict__ GM,
                                       const float* __restrict__ SC, float* __restrict__ GC,
                                       const float* __restrict__ U, const int n)
{
    const int g = blockIdx.x * blockDim.x + threadIdx.x;
    if (g >= n) return;
    const float* E = GT + (size_t)g * GS;
    float* C = GC + g * 4;
    if (E[0] < 0.5f || E[2] < 1.f) { C[0] = 0.f; C[1] = 0.f; C[2] = 0.f; return; }
    const float sp = U[2];
    const int m0 = (int)E[1], mc = (int)E[2];
    const float L = E[3] * sp;
    const float* f = SC + GM[m0] * 8;
    const float* l = SC + GM[m0 + mc - 1] * 8;
    const bool up = f[5] >= 0.f;
    const float tf = up ? f[1] - L : f[2] + L, tl = up ? l[1] - L : l[2] + L;
    const float dx = l[0] - f[0];
    float slope = dx > 1.f ? fminf(fmaxf((tl - tf) / dx, -0.18f), 0.18f) : 0.f;
    const float lo = fminf(tf, tl), hi = fmaxf(tf, tl);
    for (int k = 1; k < mc - 1; ++k) {
        const float* s = SC + GM[m0 + k] * 8;
        const float tt = up ? s[1] - L : s[2] + L;
        if ((up && tt < lo - 0.5f) || (!up && tt > hi + 0.5f)) slope = 0.f;
    }
    float c = up ? 1e9f : -1e9f;
    for (int k = 0; k < mc; ++k) {
        const float* s = SC + GM[m0 + k] * 8;
        const float tt = (up ? s[1] - L : s[2] + L) - slope * s[0];
        c = up ? fminf(c, tt) : fmaxf(c, tt);
    }
    C[0] = c; C[1] = slope; C[2] = up ? 1.f : -1.f;
}

// stems, second half: their records (a beamed stem ends on its beam's line) and their flags
extern "C" __global__ void rec_stems(const float* __restrict__ ST, const float* __restrict__ SC,
                                     const float* __restrict__ GC, const float* __restrict__ HC,
                                     float* __restrict__ REC, const float* __restrict__ U, const int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const float* E = ST + (size_t)i * SS;
    const float* C = SC + i * 8;
    float* R = REC + (size_t)i * 2 * NF;
    if (E[0] < 0.5f || C[6] <= 0.002f) { dead(R); dead(R + NF); return; }
    const float t = U[0], sp = U[2];
    float tip = C[4];
    const int g = (int)E[19];
    const float bw = C[7];
    if (g >= 0 && GC[g * 4 + 2] != 0.f) tip = tip * (1.f - bw) + (GC[g * 4] + GC[g * 4 + 1] * C[0]) * bw;
    const float base = C[3];
    const float* lh = HC + (int)E[1] * 8;
    const float L = ease(E[7], E[8], E[9], 22.f, t);
    if (fabsf(tip - base) > 0.5f && L > 0.02f) {
        box_rec(R, C[0] - """ + f"{STEM_HW}f" + r""" * sp, fminf(base, tip), C[0] + """ + f"{STEM_HW}f" + r""" * sp, fmaxf(base, tip), C[6]);
        colour(R, lh[5], lh[6], lh[7], 0.f, 0.f);
    } else dead(R);
    const float fl = ease(E[10], E[11], E[12], 12.f, t) * (1.f - bw);
    const float d = C[5];
    if (fl > 0.02f && fabsf(d) > 0.05f && bw < 0.98f) {
        const int down = d < 0.f ? 1 : 0;
        const int fa = (int)E[16] + down, fb = E[17] >= 0.f ? (int)E[17] + down : -1;
        const float fm = fb >= 0 ? fminf(fmaxf((t - E[18]) * """ + f"{MORPH_R:.1f}f" + r""", 0.f), 1.f) : 0.f;
        glyph_rec(R + NF, C[0] - """ + f"{STEM_HW}f" + r""" * sp, tip, fa, fb, fm, fl * sp, fl * fabsf(d) * sp, C[6], 1.5f);
        colour(R + NF, lh[5], lh[6], lh[7], 0.f, 0.f);
    } else dead(R + NF);
}

extern "C" __global__ void rec_segs(const float* __restrict__ BT, const float* __restrict__ SC,
                                    const float* __restrict__ GC, const float* __restrict__ ST,
                                    const float* __restrict__ HC, float* __restrict__ REC,
                                    const float* __restrict__ U, const int n)
{
    const int j = blockIdx.x * blockDim.x + threadIdx.x;
    if (j >= n) return;
    const float* E = BT + (size_t)j * BS;
    float* R = REC + (size_t)j * NF;
    const int g = (int)E[1];
    if (E[0] < 0.5f || g < 0 || GC[g * 4 + 2] == 0.f) { dead(R); return; }
    const float t = U[0], sp = U[2];
    const float gr = fminf(fmaxf(E[6] + (t - E[8]) * (E[7] > E[6] ? 1.f / 0.22f : -1.f / 0.18f) , 0.f), 1.f);
    const float grow = E[7] > E[6] ? fminf(gr, E[7]) : fmaxf(gr, E[7]);
    const float* f = SC + (int)E[2] * 8;
    const float* l = SC + (int)E[3] * 8;
    const float let = E[5];
    float x0, x1;
    if (let != 0.f) { x0 = fminf(f[0], f[0] + let * 1.1f * sp); x1 = fmaxf(f[0], f[0] + let * 1.1f * sp); }
    else { x0 = f[0] - """ + f"{STEM_HW}f" + r""" * sp; x1 = l[0] + """ + f"{STEM_HW}f" + r""" * sp; }
    x1 = x0 + (x1 - x0) * grow;
    const float c = GC[g * 4], slope = GC[g * 4 + 1], up = GC[g * 4 + 2];
    const float y0 = c + slope * x0 + up * (""" + f"{BEAM_HT}f" + r""" + 0.75f * E[4]) * sp;
    const float a = fmaxf(f[6], l[6]);
    if (grow <= 0.01f || a <= 0.002f) { dead(R); return; }
    const float half = """ + f"{BEAM_HT}f" + r""" * sp;
    R[0] = 2.f; R[1] = -1.f; R[2] = x0; R[3] = y0; R[15] = x1; R[16] = slope; R[17] = half; R[9] = a;
    const float* lh = HC + (int)ST[(size_t)((int)E[2]) * SS + 1] * 8;
    colour(R, lh[5], lh[6], lh[7], 0.f, 0.f);
    const float y1 = y0 + slope * (x1 - x0);
    R[20] = x0 - 1.5f - half; R[22] = x1 + 1.5f + half;
    R[21] = fminf(y0, y1) - 1.5f - half; R[23] = fmaxf(y0, y1) + 1.5f + half;
}

extern "C" __global__ void rec_ties(const float* __restrict__ TT, const float* __restrict__ HC,
                                    float* __restrict__ REC, const float* __restrict__ U, const int n)
{
    const int j = blockIdx.x * blockDim.x + threadIdx.x;
    if (j >= n) return;
    const float* E = TT + (size_t)j * TS;
    float* R = REC + (size_t)j * NF;
    if (E[0] < 0.5f) { dead(R); return; }
    const float t = U[0], sp = U[2];
    const float* a = HC + (int)E[1] * 8;
    const float* b = HC + (int)E[2] * 8;
    const float up = ease(E[3], E[4], E[5], 10.f, t);
    const float g = fminf(fmaxf((t - E[6]) / 0.25f, 0.f), 1.f);
    const float x0 = a[0] + (a[4] + 0.1f) * sp, x1e = b[0] - 0.1f * sp;
    const float x1 = x0 + (x1e - x0) * g;
    const float y0 = a[1] - up * 0.42f * sp;
    const float y1 = y0 + (b[1] - up * 0.42f * sp - y0) * g;
    const float sag = -up * fminf(fmaxf(0.3f + 0.07f * fabsf(x1e - x0) / sp, 0.3f), 0.9f) * sp;
    const float al = fminf(a[2], b[2]) * fminf(fmaxf(alpha_at(E[7], E[8], E[9], t), 0.f), 1.f);
    if (x1 - x0 <= 0.4f * sp || al <= 0.002f) { dead(R); return; }
    const float th = 0.2f * sp;
    R[0] = 3.f; R[1] = -1.f; R[2] = x0; R[3] = y0; R[15] = x1; R[16] = y1; R[17] = sag; R[18] = th; R[9] = al;
    colour(R, a[5], a[6], a[7], 0.f, 0.f);
    const float lo = fminf(y0, y1), hi = fmaxf(y0, y1);
    R[20] = fminf(x0, x1) - 1.5f; R[22] = fmaxf(x0, x1) + 1.5f;
    R[21] = fminf(lo, lo + sag) - 1.5f; R[23] = fmaxf(hi, hi + sag) + 1.5f;
}

extern "C" __global__ void rec_misc(const float* __restrict__ RT, const float* __restrict__ LT,
                                    const float* __restrict__ QT, float* __restrict__ RREC,
                                    float* __restrict__ LREC, float* __restrict__ QREC, const float* __restrict__ U,
                                    const int nr, const int nl, const int nq)
{
    const int j = blockIdx.x * blockDim.x + threadIdx.x;
    const float t = U[0], dS = U[1], sp = U[2], t1 = U[3], t2 = U[4], fl = U[5];
    if (j < nr) {                                                   // rests (and their dots)
        const float* E = RT + (size_t)j * RS;
        float* R = RREC + (size_t)j * 2 * NF;
        if (E[0] < 0.5f) { dead(R); dead(R + NF); }
        else {
            float ux, uy;
            const float x = qhop(E[1], E[2], E[3], E[4], E[5], t, ux) - dS;
            const float y = qhop(E[7], E[8], E[9], E[10], E[11], t, uy);
            const float a = fminf(fmaxf(alpha_at(E[12], E[13], E[14], t), 0.f), 1.f);
            const float s = spring(E[15], E[16], E[17], E[18], t);
            const int ga = (int)E[19], gb = (int)E[20];
            const float mt = gb >= 0 ? fminf(fmaxf((t - E[21]) * """ + f"{MORPH_R:.1f}f" + r""", 0.f), 1.f) : 0.f;
            glyph_rec(R, x + (1.f - s) * 0.6f * sp, y, ga, gb, mt, s * sp, s * sp, a, 1.5f);
            ink(R);
            if (E[22] > 0.5f) {
                float* D = R + NF;
                const float cx = x + 1.65f * sp, cy = y - 0.5f * sp, r = 0.2f * sp;
                D[0] = 5.f; D[1] = -1.f; D[2] = cx; D[3] = cy; D[15] = r; D[9] = a; ink(D);
                D[20] = cx - r - 1.5f; D[22] = cx + r + 1.5f; D[21] = cy - r - 1.5f; D[23] = cy + r + 1.5f;
            } else dead(R + NF);
        }
    }
    if (j < nl) {                                                   // bar lines: dropping in like gates
        const float* E = LT + (size_t)j * LS;
        float* R = LREC + (size_t)j * 2 * NF;
        if (E[0] < 0.5f) { dead(R); dead(R + NF); }
        else {
            float ux;
            const float x = qhop(E[1], E[2], E[3], E[4], E[5], t, ux) - dS;
            const float a = fminf(fmaxf(alpha_at(E[8], E[9], E[10], t), 0.f), 1.f);
            const float g = fminf(fmaxf((t - E[7]) / 0.28f, 0.f), 1.f);
            const float e = 1.f - (1.f - g) * (1.f - g) * (1.f - g);
            const float over = 0.06f * sinf(PI_F * g) * fl;
            const float yb = fminf(t1 + (t2 + 4.f * sp - t1) * fminf(e + over, 1.04f), t2 + 4.f * sp);
            box_rec(R, x - """ + f"{BAR_HT}f" + r""" * sp, t1, x + """ + f"{BAR_HT}f" + r""" * sp, yb, a); ink(R);
            if (E[11] > 0.5f) { box_rec(R + NF, x + 0.4f * sp, t1, x + 0.9f * sp, yb, a); ink(R + NF); }
            else dead(R + NF);
        }
    }
    if (j < nq) {                                                   // signature digits: split-flap
        const float* E = QT + (size_t)j * QS;
        float* R = QREC + (size_t)j * NF;
        if (E[0] < 0.5f) dead(R);
        else {
            float ux;
            const float x = qhop(E[1], E[2], E[3], E[4], E[5], t, ux) - dS;
            const float a = fminf(fmaxf(alpha_at(E[8], E[9], E[10], t), 0.f), 1.f);
            float f = fminf(fmaxf((t - E[14]) / 0.14f, 0.f), 1.f);       // flapping in
            int g = (int)E[11];
            if (E[12] >= 0.f) {                                          // flapping to another digit
                const float u = fminf(fmaxf((t - E[13]) / 0.28f, 0.f), 1.f);
                f = fminf(f, fabsf(1.f - 2.f * u));
                if (u >= 0.5f) g = (int)E[12];
            }
            glyph_rec(R, x, E[7], g, -1, 0.f, sp, fmaxf(f, 0.02f) * sp, a, 1.5f);
            ink(R);
        }
    }
}

// bins: one block per (row, 64-px column); the records whose boxes touch it (tape records where the row shows
// them, header records in their row), at most BIN_CAP (the rest dropped: degrade)
extern "C" __global__ void bin_recs(const float* __restrict__ REC, const int* __restrict__ rng, const int nrng,
                                    int* __restrict__ cnt, int* __restrict__ lst, const float* __restrict__ P)
{
    const int b = blockIdx.x;
    const int NC = (int)P[24], R = (int)P[4];
    const int r = b / NC, col = b % NC;
    if (r >= R) return;
    __shared__ int n;
    if (threadIdx.x == 0) n = 0;
    __syncthreads();
    const float BW = P[25], XL = P[7], WV = P[8];
    const float sx0 = col * BW, sx1 = sx0 + BW;
    const float lo = -(R - 1 - r) * WV;
    const float tx0 = fmaxf(sx0, XL) - XL + lo, tx1 = fminf(sx1, XL + WV) - XL + lo;
    const bool tape = tx1 > tx0;
    int total = 0;
    for (int k = 0; k < nrng; ++k) total += rng[2 * k + 1];
    for (int v = threadIdx.x; v < total; v += blockDim.x) {
        int i = v, k = 0;
        while (i >= rng[2 * k + 1]) { i -= rng[2 * k + 1]; ++k; }
        i += rng[2 * k];
        const float* Q = REC + (size_t)i * NF;
        if (Q[9] <= 0.002f) continue;
        const int row = (int)Q[1];
        bool hit;
        if (row < 0) hit = tape && Q[20] <= tx1 && Q[22] >= tx0;
        else hit = row == r && Q[20] <= sx1 && Q[22] >= sx0;
        if (hit) {
            const int k = atomicAdd(&n, 1);
            if (k < BIN_CAP) lst[b * BIN_CAP + k] = i;
        }
    }
    __syncthreads();
    if (threadIdx.x == 0) cnt[b] = min(n, BIN_CAP);
}
"""


# -- host-side closed forms (the kernels' own) -----------------------------------------------------------------------------
def qhop(p0, v0, p1, t0, T, t):
    u = min(max((t - t0) / T, 0.0), 1.0) if T > 1e-6 else 1.0
    u2, u3 = u * u, u * u * u
    u4, u5 = u3 * u, u3 * u2
    p = p0 * (1 - 10 * u3 + 15 * u4 - 6 * u5) + T * v0 * (u - 6 * u3 + 8 * u4 - 3 * u5) + p1 * (10 * u3 - 15 * u4 + 6 * u5)
    if u >= 1.0 or T <= 1e-6:
        return p, 0.0
    d0 = -30 * u2 + 60 * u3 - 30 * u4
    d1 = 1 - 18 * u2 + 32 * u3 - 15 * u4
    return p, (p0 * d0 + T * v0 * d1 - p1 * d0) / T


def ease_at(v0, vt, t0, k, t):
    return vt + (v0 - vt) * math.exp(-k * max(t - t0, 0.0))


def alpha_at(a0, at, ta, t):
    return min(max(ease_at(a0, at, ta, K_IN if at > a0 else K_OUT, t), 0.0), 1.0)


def spring_at(s0, sv0, st, t0, t):
    w, z = SPR_W, SPR_Z
    wd = w * math.sqrt(1 - z * z)
    tau = max(t - t0, 0.0)
    c1 = s0 - st
    c2 = (sv0 + z * w * c1) / wd
    e = math.exp(-z * w * tau)
    s = st + e * (c1 * math.cos(wd * tau) + c2 * math.sin(wd * tau))
    v = e * ((-z * w) * (c1 * math.cos(wd * tau) + c2 * math.sin(wd * tau))
             + (-c1 * wd * math.sin(wd * tau) + c2 * wd * math.cos(wd * tau)))
    return s, v


class Table:
    """A float32 table of rows (one per mark) with stable keys; ``dirty`` while it differs from the GPU's copy."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cap = CAP[name]
        self.a = np.zeros((self.cap, STRIDE[name]), np.float32)
        self.key: list = [None] * self.cap
        self.idx: dict = {}
        self.free = list(range(self.cap - 1, -1, -1))
        self.ph = np.full(self.cap, -1, np.int64)
        self.born = np.zeros(self.cap)
        self.dying = np.zeros(self.cap, bool)
        self.t_die = np.zeros(self.cap)
        self.dirty = True
        self.hi = 0                                      # rows in use are below this (the kernels' n)

    def alloc(self, key, now: float, serial: int) -> int:
        if not self.free:                                # full: the oldest mark makes room (degrade)
            old = int(np.argmin(np.where(self.a[:, 0] > 0.5, self.born, np.inf)))
            self.release(old)
        i = self.free.pop()
        self.a[i] = 0.0
        self.a[i, 0] = 1.0
        self.key[i] = key
        self.idx[key] = i
        self.ph[i] = serial
        self.born[i] = now
        self.dying[i] = False
        self.dirty = True
        self.hi = max(self.hi, i + 1)
        return i

    def rekey(self, old, new) -> int | None:
        """The mark ``old`` becomes ``new`` (it lives on, moving to the new target, instead of dying while its
        successor is born)."""
        i = self.idx.pop(old, None)
        if i is None:
            return None
        self.key[i] = new
        self.idx[new] = i
        self.dying[i] = False
        return i

    def release(self, i: int) -> None:
        if self.key[i] is None:
            return
        self.idx.pop(self.key[i], None)
        self.key[i] = None
        self.a[i] = 0.0
        self.free.append(i)
        self.ph[i] = -1
        self.dying[i] = False
        self.dirty = True


def hop(a: np.ndarray, i: int, c0: int, target: float, now: float, dur: float, arc_col: int = -1,
        arc: float = 0.0) -> None:
    """Re-plan the hop at columns c0..c0+4 (p0, v0, p1, t0, T) toward ``target`` from where it is now."""
    p, v = qhop(float(a[i, c0]), float(a[i, c0 + 1]), float(a[i, c0 + 2]), float(a[i, c0 + 3]), float(a[i, c0 + 4]),
                now)
    a[i, c0:c0 + 5] = (p, v, target, now, dur)
    if arc_col >= 0:
        a[i, arc_col] = arc


def place(a: np.ndarray, i: int, c0: int, value: float, now: float) -> None:
    a[i, c0:c0 + 5] = (value, 0.0, value, now, 0.0)


def ease_to(a: np.ndarray, i: int, c0: int, target: float, now: float, k: float) -> None:
    """An eased channel (v0, vt, t0) at c0: from where it is now toward ``target``."""
    if abs(float(a[i, c0 + 1]) - target) < 1e-6:
        return
    v = ease_at(float(a[i, c0]), float(a[i, c0 + 1]), float(a[i, c0 + 2]), k, now)
    a[i, c0:c0 + 3] = (v, target, now)


def alpha_to(a: np.ndarray, i: int, c0: int, target: float, now: float) -> None:
    if abs(float(a[i, c0 + 1]) - target) < 1e-6:
        return
    v = alpha_at(float(a[i, c0]), float(a[i, c0 + 1]), float(a[i, c0 + 2]), now)
    a[i, c0:c0 + 3] = (v, target, now)


def spring_kick(a: np.ndarray, i: int, c0: int, now: float, target: float | None = None, dv: float = 0.0) -> None:
    s, v = spring_at(float(a[i, c0]), float(a[i, c0 + 1]), float(a[i, c0 + 2]), float(a[i, c0 + 3]), now)
    a[i, c0:c0 + 4] = (s, v + dv, float(a[i, c0 + 2]) if target is None else target, now)


def morph_to(a: np.ndarray, i: int, cA: int, g: int, now: float) -> None:
    """Glyph columns (A, B, t0): morph toward g (a morph already done makes B the A first)."""
    gA, gB, tm = int(a[i, cA]), int(a[i, cA + 1]), float(a[i, cA + 2])
    if gB >= 0 and now - tm >= 1.0 / MORPH_R:
        gA, gB = gB, -1
    elif gB >= 0:                                   # mid-morph: from whichever shows more
        gA = gB if (now - tm) * MORPH_R > 0.5 else gA
        gB = -1
    if gA == g:
        a[i, cA:cA + 3] = (gA, -1, tm)
        return
    a[i, cA:cA + 3] = (gA, g, now)


class Stage:
    """Every mark of every phrase on the tape.  Host: targets, births, deaths (tape px relative to ``x_ref``,
    seconds relative to ``t_ref``).  GPU: ``frame()`` evaluates, draws the dependents, writes records and bins."""

    def __init__(self, sp: float, flourish: float = 0.7) -> None:
        self.sp = sp
        self.flourish = flourish
        self.T = {k: Table(k) for k in CAP}
        self.stem_heads: dict = {}                 # stem row -> [head rows]
        self.group_stems: dict = {}                # group row -> [stem rows]
        self.topo_dirty = True
        self.x_ref = 0.0
        self.t_ref = 0.0
        self.sm = np.zeros(1, np.int32)
        self.gm = np.zeros(1, np.int32)
        self.gpu = None
        self.hdr = np.zeros((0, NF), np.float32)
        self.hdr_dirty = True
        self.counts = {}
        self.last_t: dict = {}                     # key -> the target last applied (unchanged targets are skipped)
        self.released: set = set()                 # tables something left since the last membership pass
        self.dbg = None
        self.pend: dict = {}

    # -- time and place --------------------------------------------------------------------------------------------------
    def tx(self, x: float) -> float:
        return x - self.x_ref

    def tt(self, t: float) -> float:
        return t - self.t_ref

    def hop_dur(self, d_px: float) -> float:
        return min(0.55, 0.24 + 0.025 * abs(d_px) / self.sp)

    # -- applying a layout ----------------------------------------------------------------------------------------------
    def sync(self, serial: int, out: RL.Out, A: float, now_s: float, *, final: bool, mapver: int, regrid: bool,
             owned: set, note_info: dict, reuse: set | None = None) -> set:
        """Apply a phrase's targets (``final``: written bars, adopted at once; else provisional: a head moves only
        when two estimates on different mappings agree, or on a regrid).  ``owned``: the keys this region owned
        last time; returns the keys it owns now (those it no longer owns die)."""
        sp = self.sp
        now = self.tt(now_s)
        fl = self.flourish
        Hd, St, Gt, Bt, Tt = (self.T[k] for k in ("heads", "stems", "groups", "segs", "ties"))
        H = Hd.a
        new_keys = set()
        if not hasattr(self, "pend"):
            self.pend = {}                              # head row -> (x, y, mapver): an estimate waiting for a second
        # marks this layout drops whose successors it adds take them over (a tied piece renumbered, a chord whose
        # lowest note changed): one object moving, not one fading as another pops in
        spare = (owned | (reuse or set()))
        spare_heads: dict = {}
        for k in spare:
            if k[0] == "h" and k[3] > 0 and k not in out.heads and k in Hd.idx:
                spare_heads.setdefault(k[2], []).append(k)
        spare_stems = {k for k in spare if k[0] == "s" and k not in out.stems and k in St.idx}
        spare_beams = {k for k in spare if k[0] == "b" and k not in out.beams and k in Bt.idx}
        last = self.last_t
        pend = self.pend
        for key, tgt in out.heads.items():
            new_keys.add(key)
            x, y, glyph, staff, nid, piece, acc, acc_dx, dot, _lst = tgt
            i = Hd.idx.get(key)
            info = note_info.get(nid)
            lt = (A, tgt, final, info[2] if info else None)
            if i is not None and not regrid and i not in pend and not Hd.dying[i] and last.get(key) == lt:
                continue
            last[key] = lt
            X, Y = self.tx(A + x * sp), y * sp
            g = GI[glyph]
            i = Hd.idx.get(key)
            if i is None and piece > 0 and spare_heads.get(nid):
                cand = sorted(spare_heads[nid], key=lambda k: abs(k[3] - piece))[0]
                spare_heads[nid].remove(cand)
                i = Hd.rekey(cand, key)
            if i is None:
                i = Hd.alloc(key, now, serial)
                prev = Hd.idx.get(key[:3] + (piece - 1,)) if piece > 0 else None
                if prev is not None:                       # a tied piece grows out of the one before it
                    px, _v = qhop(*[float(v) for v in H[prev, H_X0:H_X0 + 5]], now)
                    py, _v = qhop(*[float(v) for v in H[prev, H_Y0:H_Y0 + 5]], now)
                    place(H, i, H_X0, px, now)
                    place(H, i, H_Y0, py, now)
                    hop(H, i, H_X0, X, now, self.hop_dur(X - px))
                    hop(H, i, H_Y0, Y, now, 0.25)
                    H[i, H_S0:H_TS + 1] = (0.6, 0.0, 1.0, now)
                    H[i, H_A0:H_TA + 1] = (0.3, 1.0, now)
                else:
                    place(H, i, H_X0, X, now)
                    place(H, i, H_Y0, Y, now)
                    H[i, H_S0:H_TS + 1] = (1.0 + 0.35 * fl, 0.0, 1.0, now)
                    H[i, H_A0:H_TA + 1] = (0.6, 1.0, now)      # notation on its first frame
                H[i, H_GA:H_TM + 1] = (g, -1, now)
                H[i, H_ACG] = -1
                H[i, H_ACGB] = -1
                H[i, H_OG] = -1
                info = note_info.get(nid)
                H[i, H_PC] = (info[0] % 12) if info else -1
                H[i, H_TON] = self.tt(info[1]) if info and info[1] is not None else -1e9
                H[i, H_TOFF] = self.tt(info[2]) if info and info[2] is not None else 1e9
                H[i, H_VEL] = 0.6
                if info and info[1] is None:
                    H[i, H_PC] = -1
            else:
                if Hd.dying[i]:
                    Hd.dying[i] = False
                    alpha_to(H, i, H_A0, 1.0, now)
                    spring_kick(H, i, H_S0, now, 1.0)
                elif float(H[i, H_AT]) < 1.0:              # an orphan written after all
                    alpha_to(H, i, H_A0, 1.0, now)
                cx, cy = float(H[i, H_X1]), float(H[i, H_Y1])
                if abs(X - cx) > 0.5 or abs(Y - cy) > 0.5:
                    adopt = final or regrid
                    if not adopt:
                        pd = self.pend.get(i)
                        if pd is not None and abs(pd[0] - X) < 0.5 and abs(pd[1] - Y) < 0.5:
                            adopt = pd[2] != mapver              # a second estimate agrees
                        else:
                            self.pend[i] = (X, Y, mapver)
                    if adopt:
                        if self.dbg is not None:
                            self.dbg.append((now, key, cx, X, final, regrid, mapver, (self.pend.get(i) or (0, 0, -1))[2]))
                        cur_x, _v = qhop(*[float(v) for v in H[i, H_X0:H_X0 + 5]], now)
                        dx = X - cur_x
                        arc = min(0.8 * sp, 0.25 * abs(dx)) * fl if abs(dx) > 0.3 * sp and final else 0.0
                        if abs(X - cx) > 0.5:
                            hop(H, i, H_X0, X, now, self.hop_dur(dx), H_ARC, arc)
                        if abs(Y - cy) > 0.5:
                            hop(H, i, H_Y0, Y, now, 0.3 if abs(Y - cy) > 3 * sp else 0.22)
                        self.pend.pop(i, None)
                else:
                    self.pend.pop(i, None)
                ga, gb = int(H[i, H_GA]), int(H[i, H_GB])
                shown = gb if (gb >= 0 and (now - float(H[i, H_TM])) * MORPH_R >= 0.5) else ga
                if shown != g or (gb >= 0 and gb != g):
                    morph_to(H, i, H_GA, g, now)
                    spring_kick(H, i, H_S0, now, dv=2.5 * fl)       # a little pulse as it changes form
                info = note_info.get(nid)
                if info:
                    H[i, H_TOFF] = self.tt(info[2]) if info[2] is not None else 1e9
            if acc is not None:
                ga = GI[acc]
                cur = int(H[i, H_ACG])
                if float(H[i, H_ACST]) < 0.5 or cur < 0:
                    H[i, H_ACG], H[i, H_ACGB] = ga, -1
                    H[i, H_ACX0:H_ACTX + 1] = (acc_dx, acc_dx, now)
                elif cur != ga and int(H[i, H_ACGB]) != ga:
                    morph_to(H, i, H_ACG, ga, now)
                ease_to(H, i, H_ACS0, 1.0, now, K_EASE)
                ease_to(H, i, H_ACX0, acc_dx, now, K_EASE)
            else:
                ease_to(H, i, H_ACS0, 0.0, now, K_EASE)
            if dot is not None:
                H[i, H_DDX], H[i, H_DDY] = dot
                ease_to(H, i, H_DA0, 1.0, now, K_EASE)
            else:
                ease_to(H, i, H_DA0, 0.0, now, K_EASE)
        Hd.dirty = True
        for key, (hk, name, above) in out.otts.items():
            i = Hd.idx.get(hk)
            if i is not None:
                H[i, H_OG] = GI[name]
                H[i, H_OUP] = 1.0 if above else 0.0
                ease_to(H, i, H_OA0, 1.0, now, K_EASE)
        # stems
        S = St.a
        for key, tgt in out.stems.items():
            new_keys.add(key)
            hkeys, lead, up, L, flags, beam = tgt
            j = St.idx.get(key)
            lt = (tuple(hkeys), tuple(Hd.idx.get(k, -1) for k in hkeys), lead, up, L, flags, beam)
            if j is not None and not St.dying[j] and last.get(key) == lt:
                continue
            last[key] = lt
            hs = [Hd.idx[k] for k in hkeys if k in Hd.idx]
            if not hs:
                continue
            i = St.idx.get(key)
            d = 1.0 if up else -1.0
            if i is None and spare_stems:
                hset = set(hs)
                for k in list(spare_stems):
                    if hset & set(self.stem_heads.get(St.idx[k], ())):
                        spare_stems.discard(k)
                        i = St.rekey(k, key)
                        break
            if i is None:
                i = St.alloc(key, now, serial)
                S[i, S_D0:S_TD + 1] = (d, d, now)
                S[i, S_L0:S_TL + 1] = (0.0, L, now)
                S[i, S_GROUP] = -1
                S[i, S_FGB] = -1
                S[i, S_FG] = GI["flag8thUp"]
            else:
                if St.dying[i]:
                    St.dying[i] = False
                ease_to(S, i, S_D0, d, now, 10.0)
                ease_to(S, i, S_L0, L, now, 22.0)
            S[i, S_LEAD] = Hd.idx.get(lead, hs[0])
            fg = GI["flag8thUp"] if flags <= 1 else GI["flag16thUp"]
            if flags:
                fl_now = ease_at(float(S[i, S_F0]), float(S[i, S_FT]), float(S[i, S_TF]), 12.0, now)
                if fl_now < 0.05:
                    S[i, S_FG], S[i, S_FGB] = fg, -1
                elif int(S[i, S_FG]) != fg and int(S[i, S_FGB]) != fg:
                    morph_to(S, i, S_FG, fg, now)
            ease_to(S, i, S_F0, 1.0 if flags else 0.0, now, 12.0)
            ease_to(S, i, S_B0, 1.0 if beam is not None else 0.0, now, 12.0)
            if self.stem_heads.get(i) != hs:
                self.stem_heads[i] = hs
                self.topo_dirty = True
        St.dirty = True
        # beams: a group per primary segment, segments hanging off it
        B = Bt.a
        groups = {}
        for key, (skeys, level, let) in out.beams.items():
            if level != 0:
                continue
            new_keys.add(key)
            ss = [St.idx[k] for k in skeys if k in St.idx]
            if not ss:
                continue
            gk = ("g",) + key[1:4]
            gi = Gt.idx.get(gk)
            if gi is None:
                gi = Gt.alloc(gk, now, serial)
            elif Gt.dying[gi]:
                Gt.dying[gi] = False
            Gt.a[gi, G_L] = 3.25
            groups[key[:4]] = gi
            if self.group_stems.get(gi) != ss:
                self.group_stems[gi] = ss
                self.topo_dirty = True
            for s_i in ss:
                if int(S[s_i, S_GROUP]) != gi:
                    S[s_i, S_GROUP] = gi
        for key, (skeys, level, let) in out.beams.items():
            new_keys.add(key)
            gi = groups.get(key[:4])
            ss = [St.idx[k] for k in skeys if k in St.idx]
            if gi is None or not ss:
                continue
            if level == 1:
                Gt.a[gi, G_L] = 3.75
            i = Bt.idx.get(key)
            if i is None and spare_beams:                  # a segment of the same level over one of these stems
                sset = set(ss)
                for k in list(spare_beams):
                    j = Bt.idx[k]
                    if int(Bt.a[j, B_LEVEL]) == level and {int(Bt.a[j, B_FIRST]), int(Bt.a[j, B_LAST])} & sset:
                        spare_beams.discard(k)
                        i = Bt.rekey(k, key)
                        break
            if i is None:
                i = Bt.alloc(key, now, serial)
                B[i, B_G0:B_TG + 1] = (0.0, 1.0, now)
            elif Bt.dying[i]:
                Bt.dying[i] = False
                g_now = self._grow(B, i, now)
                B[i, B_G0:B_TG + 1] = (g_now, 1.0, now)
            B[i, B_GROUP] = gi
            B[i, B_FIRST], B[i, B_LAST] = ss[0], ss[-1]
            B[i, B_LEVEL], B[i, B_LET] = level, let
        Gt.dirty = Bt.dirty = True
        # ties
        T = Tt.a
        for key, (k0, k1, up) in out.ties.items():
            new_keys.add(key)
            h0, h1 = Hd.idx.get(k0), Hd.idx.get(k1)
            if h0 is None or h1 is None:
                continue
            u = 1.0 if up else -1.0
            i = Tt.idx.get(key)
            if i is None:
                i = Tt.alloc(key, now, serial)
                T[i, T_U0:T_TU + 1] = (u, u, now)
                T[i, T_BORN] = now
                T[i, T_A0:T_TA + 1] = (1.0, 1.0, now)
            else:
                if Tt.dying[i]:
                    Tt.dying[i] = False
                    alpha_to(T, i, T_A0, 1.0, now)
                ease_to(T, i, T_U0, u, now, 10.0)
            T[i, T_H0], T[i, T_H1] = h0, h1
        Tt.dirty = True
        # rests, bar lines, signatures
        Rt, Lt, Qt = self.T["rests"], self.T["bars"], self.T["sigs"]
        for key, (x, y, glyph, dot) in out.rests.items():
            new_keys.add(key)
            X, Y = self.tx(A + x * sp), y * sp
            g = GI[glyph]
            i = Rt.idx.get(key)
            R = Rt.a
            if i is None:
                i = Rt.alloc(key, now, serial)
                place(R, i, R_X0, X, now)
                place(R, i, R_Y0, Y, now)
                R[i, R_A0:R_TA + 1] = (0.0, 1.0, now)
                R[i, R_S0:R_TS + 1] = (0.3, 0.0, 1.0, now)
                R[i, R_GA:R_TM + 1] = (g, -1, now)
            else:
                if Rt.dying[i]:
                    Rt.dying[i] = False
                    alpha_to(R, i, R_A0, 1.0, now)
                    spring_kick(R, i, R_S0, now, 1.0)
                if abs(X - float(R[i, R_X1])) > 0.5:
                    hop(R, i, R_X0, X, now, self.hop_dur(X - float(R[i, R_X1])))
                if abs(Y - float(R[i, R_Y1])) > 0.5:
                    hop(R, i, R_Y0, Y, now, 0.25)
                if int(R[i, R_GA]) != g and int(R[i, R_GB]) != g:
                    morph_to(R, i, R_GA, g, now)
            R[i, R_DOT] = 1.0 if dot else 0.0
        Rt.dirty = True
        for key, (x, kind) in out.bars.items():
            new_keys.add(key)
            X = self.tx(A + x * sp)
            Lb = Lt.a
            i = Lt.idx.get(key)
            if i is None:
                i = Lt.alloc(key, now, serial)
                place(Lb, i, L_X0, X, now)
                Lb[i, L_BORN] = now
                Lb[i, L_A0:L_TA + 1] = (1.0, 1.0, now)
            else:
                if Lt.dying[i]:
                    Lt.dying[i] = False
                    alpha_to(Lb, i, L_A0, 1.0, now)
                if abs(X - float(Lb[i, L_X1])) > 0.5:
                    hop(Lb, i, L_X0, X, now, self.hop_dur(X - float(Lb[i, L_X1])))
            Lb[i, L_KIND] = kind
        Lt.dirty = True
        for key, (x, y, glyph) in out.sigs.items():
            new_keys.add(key)
            X, Y = self.tx(A + x * sp), y * sp
            g = GI[glyph]
            Q = Qt.a
            i = Qt.idx.get(key)
            if i is None:
                i = Qt.alloc(key, now, serial)
                place(Q, i, Q_X0, X, now)
                Q[i, Q_Y] = Y
                Q[i, Q_A0:Q_TA + 1] = (1.0, 1.0, now)
                Q[i, Q_GA], Q[i, Q_GB], Q[i, Q_BORN] = g, -1, now
            else:
                if Qt.dying[i]:
                    Qt.dying[i] = False
                    alpha_to(Q, i, Q_A0, 1.0, now)
                if abs(X - float(Q[i, Q_X1])) > 0.5:
                    hop(Q, i, Q_X0, X, now, self.hop_dur(X - float(Q[i, Q_X1])))
                Q[i, Q_Y] = Y
                cur = int(Q[i, Q_GB]) if int(Q[i, Q_GB]) >= 0 and now - float(Q[i, Q_TF]) >= 0.14 else int(Q[i, Q_GA])
                if cur != g:
                    Q[i, Q_GA], Q[i, Q_GB], Q[i, Q_TF] = cur, g, now
        Qt.dirty = True
        for key in owned - new_keys:
            if key[0] == "h" and key[3] == 0:
                self.orphan(key, now)             # a note the room dropped unwritten: it stays, faint, where it was
            else:
                self.kill(key, now_s)
        return new_keys

    def orphan(self, key, now: float) -> None:
        Hd = self.T["heads"]
        i = Hd.idx.get(key)
        if i is None:
            return
        self.orphans = getattr(self, "orphans", 0) + 1
        alpha_to(Hd.a, i, H_A0, 0.45, now)
        ease_to(Hd.a, i, H_ACS0, 0.0, now, K_EASE)
        ease_to(Hd.a, i, H_DA0, 0.0, now, K_EASE)
        self.last_t.pop(key, None)
        Hd.dirty = True

    @staticmethod
    def _grow(B, i, now) -> float:
        g0, gt, t0 = float(B[i, B_G0]), float(B[i, B_GT]), float(B[i, B_TG])
        r = 1.0 / 0.22 if gt > g0 else -1.0 / 0.18
        g = min(max(g0 + (now - t0) * r, 0.0), 1.0)
        return min(g, gt) if gt > g0 else max(g, gt)

    def kill(self, key, now_s: float) -> None:
        now = self.tt(now_s)
        self.last_t.pop(key, None)
        name = {"h": "heads", "s": "stems", "b": "segs", "t": "ties", "r": "rests", "l": "bars", "ts": "sigs",
                "ks": "sigs"}.get(key[0])
        if name is None:
            return
        Tb = self.T[name]
        i = Tb.idx.get(key)
        if i is None or Tb.dying[i]:
            return
        Tb.dying[i] = True
        Tb.t_die[i] = now
        a = Tb.a
        if name == "heads":
            alpha_to(a, i, H_A0, 0.0, now)
            spring_kick(a, i, H_S0, now, 0.75)
            if key[3] > 0:                                  # a tied piece retracts into the piece before it
                j = Tb.idx.get(key[:3] + (key[3] - 1,))
                if j is not None:
                    px = float(a[j, H_X1])
                    py = float(a[j, H_Y1])
                    hop(a, i, H_X0, px, now, 0.22)
                    hop(a, i, H_Y0, py, now, 0.22)
        elif name == "stems":
            ease_to(a, i, S_L0, 0.0, now, 22.0)
            ease_to(a, i, S_F0, 0.0, now, 12.0)
        elif name == "segs":
            g = self._grow(a, i, now)
            a[i, B_G0:B_TG + 1] = (g, 0.0, now)
        elif name == "ties":
            alpha_to(a, i, T_A0, 0.0, now)
        elif name == "rests":
            alpha_to(a, i, R_A0, 0.0, now)
            spring_kick(a, i, R_S0, now, 0.75)
        elif name == "bars":
            alpha_to(a, i, L_A0, 0.0, now)
        elif name == "sigs":
            alpha_to(a, i, Q_A0, 0.0, now)
        Tb.dirty = True

    def drop_phrase(self, serial: int) -> None:
        for name, Tb in self.T.items():
            for i in np.flatnonzero(Tb.ph == serial):
                self._release(name, int(i))

    def _release(self, name: str, i: int) -> None:
        self.topo_dirty = True
        self.released.add(name)
        self.last_t.pop(self.T[name].key[i], None)
        if name == "stems":
            self.stem_heads.pop(i, None)
        if name == "groups":
            self.group_stems.pop(i, None)
        if name == "heads" and hasattr(self, "pend"):
            self.pend.pop(i, None)
        self.T[name].release(i)

    # -- housekeeping (the host's only per-frame work besides the launch) -------------------------------------------------
    def step(self, now_s: float, cull_x: float, every: int = 6) -> None:
        self.frame_n = getattr(self, "frame_n", 0) + 1
        if self.frame_n % every:
            return
        now = self.tt(now_s)
        cx = self.tx(cull_x)
        xcol = {"heads": H_X1, "rests": R_X1, "bars": L_X1, "sigs": Q_X1}
        for name, Tb in self.T.items():
            hi = Tb.hi
            if not hi:
                continue
            al = Tb.a[:hi, 0] > 0.5
            gone = al & Tb.dying[:hi] & (now - Tb.t_die[:hi] > 0.45)
            if name in xcol:
                gone |= al & (Tb.a[:hi, xcol[name]] < cx)
            for i in np.flatnonzero(gone):
                self._release(name, int(i))
        if self.topo_dirty:
            self._topology()

    def _topology(self) -> None:
        """Membership pruned of the dead (a stem with no heads left goes, and so on), as index arrays for the GPU."""
        Hd, St, Gt, Bt, Tt = (self.T[k] for k in ("heads", "stems", "groups", "segs", "ties"))
        for _ in range(3):
            self.topo_dirty = False
            rel, self.released = self.released, set()
            if not rel:
                break
            if rel & {"heads", "stems"}:
                for i in list(self.stem_heads):
                    hs = self.stem_heads[i]
                    if St.key[i] is None or any(Hd.key[h] is None for h in hs):
                        hs = [h for h in hs if Hd.key[h] is not None]
                        if not hs or St.key[i] is None:
                            self._release("stems", i)
                            continue
                        self.stem_heads[i] = hs
                    if Hd.key[int(St.a[i, S_LEAD])] is None:
                        St.a[i, S_LEAD] = hs[0]
            if rel & {"stems", "groups"}:
                for g in list(self.group_stems):
                    ss = [x for x in self.group_stems[g] if St.key[x] is not None]
                    if not ss or Gt.key[g] is None:
                        self._release("groups", g)
                    else:
                        self.group_stems[g] = ss
            if rel & {"stems", "groups", "segs"}:
                for key, i in list(Bt.idx.items()):
                    g = int(Bt.a[i, B_GROUP])
                    if Gt.key[g] is None or St.key[int(Bt.a[i, B_FIRST])] is None or St.key[int(Bt.a[i, B_LAST])] is None:
                        self._release("segs", i)
                for i in St.idx.values():
                    g = int(St.a[i, S_GROUP])
                    if g >= 0 and Gt.key[g] is None:
                        St.a[i, S_GROUP] = -1
            if "heads" in rel:
                for key, i in list(Tt.idx.items()):
                    if Hd.key[int(Tt.a[i, T_H0])] is None or Hd.key[int(Tt.a[i, T_H1])] is None:
                        self._release("ties", i)
        self.topo_dirty = False
        sm, off = [], 0
        for i, hs in self.stem_heads.items():
            St.a[i, S_MOFF], St.a[i, S_MCNT] = off, len(hs)
            sm += hs
            off += len(hs)
        gm, off = [], 0
        for g, ss in self.group_stems.items():
            Gt.a[g, G_MOFF], Gt.a[g, G_MCNT] = off, len(ss)
            gm += ss
            off += len(ss)
        self.sm = np.array(sm or [0], np.int32)
        self.gm = np.array(gm or [0], np.int32)
        St.dirty = Gt.dirty = True
        self.sm_dirty = True

    def rebase_x(self, dx: float) -> None:
        """Move the tape's origin on by ``dx`` px (positions are float32 from it: a long session would lose
        precision).  Every stored x, and every waiting estimate, shifts with it; nothing on screen moves."""
        self.x_ref += dx
        for name, cols in (("heads", (H_X0, H_X1)), ("rests", (R_X0, R_X1)), ("bars", (L_X0, L_X1)),
                           ("sigs", (Q_X0, Q_X1))):
            Tb = self.T[name]
            if Tb.hi:
                for c in cols:
                    Tb.a[:Tb.hi, c] -= dx
                Tb.dirty = True
        self.pend = {i: (x - dx, y, mv) for i, (x, y, mv) in self.pend.items()}
        self.last_t.clear()

    def set_header(self, rec: np.ndarray) -> None:
        self.hdr = rec[:N_HDR]
        self.hdr_dirty = True

    # -- the GPU ------------------------------------------------------------------------------------------------------------
    def gpu_setup(self, cp, raw_module, helpers: str, n_rows: int, NC: int, launch_2d) -> None:
        mod = raw_module(helpers + PREP_SRC)
        self.cp = cp
        self.k = {n: mod.get_function(n) for n in ("prep_heads", "prep_stems", "prep_groups", "rec_stems", "rec_segs",
                                                   "rec_ties", "rec_misc", "bin_recs")}
        gb = mod.get_global("c_GB")
        cp.ndarray((RG.NG * 4,), cp.float32, gb).set(np.ascontiguousarray(RR.GBOX.ravel(), np.float32))
        self.dev = {k: cp.zeros(CAP[k] * STRIDE[k], cp.float32) for k in CAP}
        self.HC = cp.zeros(CAP["heads"] * 8, cp.float32)
        self.SC = cp.zeros(CAP["stems"] * 8, cp.float32)
        self.GC = cp.zeros(CAP["groups"] * 4, cp.float32)
        self.SM = cp.zeros(CAP["heads"] * 2, cp.int32)
        self.GM = cp.zeros(CAP["stems"] * 2, cp.int32)
        # changed tables go up through pinned staging (two halves: one may still be copying)
        self.st_off = {}
        o = 0
        for name in CAP:
            self.st_off[name] = o
            o += CAP[name] * STRIDE[name]
        self.st_off["sm"] = o
        o += CAP["heads"] * 2
        self.st_off["gm"] = o
        o += CAP["stems"] * 2
        self.st_off["hdr"] = o
        o += N_HDR * NF
        self.st_n = o
        self._st_pin = [cp.cuda.alloc_pinned_memory(4 * o) for _ in range(2)]
        self._st_host = [np.frombuffer(m, np.float32, o) for m in self._st_pin]
        self._st_ev = [None, None]
        self._st_k = 0
        # the record regions
        self.reg = {}
        o = 0
        for name in ("heads", "stems", "segs", "ties", "rests", "bars", "sigs"):
            self.reg[name] = o
            o += CAP[name] * SLOTS[name]
        self.reg["hdr"] = o
        o += N_HDR
        self.n_rec = o
        self.REC = cp.zeros(o * NF, cp.float32)
        self.REC.reshape(o, NF)[:, 9] = 0.0
        self.n_bins = n_rows * NC
        self.CNT = cp.zeros(self.n_bins, cp.int32)
        self.LST = cp.zeros(self.n_bins * BIN_CAP, cp.int32)
        self.U = cp.zeros(16, cp.float32)
        self.RNG = cp.zeros(32, cp.int32)
        self._u_pin = [cp.cuda.alloc_pinned_memory(64 * 4) for _ in range(2)]
        self._u_host = [np.frombuffer(m, np.float32, 64) for m in self._u_pin]
        self._u_ev = [None, None]
        self._u_k = 0
        self.sm_dirty = True

    def _rec(self, name: str):
        return self.REC[self.reg[name] * NF:]

    def upload(self, S_px: float, now_s: float) -> None:
        """What changed goes up (tables, membership, the headers: through pinned staging, async), then the frame's
        uniforms and record ranges."""
        cp = self.cp
        st = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        if self.topo_dirty:
            self._topology()
        jobs = []
        for name, Tb in self.T.items():
            if Tb.dirty:
                jobs.append((name, Tb.a[:max(Tb.hi, 1)].ravel(), self.dev[name].data.ptr))
                Tb.dirty = False
        if self.sm_dirty:
            jobs.append(("sm", self.sm.view(np.float32), self.SM.data.ptr))
            jobs.append(("gm", self.gm.view(np.float32), self.GM.data.ptr))
            self.sm_dirty = False
        if self.hdr_dirty:
            h = np.zeros((N_HDR, NF), np.float32)
            h[:, 20] = 1e9
            h[:, 22] = -1e9
            if len(self.hdr):
                h[:len(self.hdr)] = self.hdr
            jobs.append(("hdr", h.ravel(), self._rec("hdr").data.ptr))
            self.hdr_dirty = False
        if jobs:
            k = self._st_k = 1 - self._st_k
            ev = self._st_ev[k]
            if ev is not None and not ev.done:
                ev.synchronize()                       # two uploads ago: long done in practice
            host = self._st_host[k]
            for name, arr, dst in jobs:
                o = self.st_off[name]
                n = min(len(arr), CAP.get(name, 0) * STRIDE.get(name, 0) or len(arr))
                host[o:o + n] = arr[:n]
                rt.memcpyAsync(dst, host[o:].ctypes.data, 4 * n, rt.memcpyHostToDevice, st.ptr)
            if ev is None:
                ev = self._st_ev[k] = cp.cuda.Event(block=False, disable_timing=True)
            ev.record(st)
        kk = self._u_k = 1 - self._u_k
        ev = self._u_ev[kk]
        if ev is not None and not ev.done:
            ev.synchronize()
        u = self._u_host[kk]
        sp = self.sp
        u[:16] = (self.tt(now_s), S_px - self.x_ref, sp, RL.T1 * sp, RL.T2 * sp, self.flourish, 0.32 * sp, 0, 0, 0,
                  0, 0, 0, 0, 0, 0)
        rng = u[16:48].view(np.int32)
        rng[:] = 0
        k = 0
        for name in ("heads", "stems", "segs", "ties", "rests", "bars", "sigs"):
            rng[2 * k], rng[2 * k + 1] = self.reg[name], max(self.T[name].hi, 1) * SLOTS[name]
            k += 1
        rng[2 * k], rng[2 * k + 1] = self.reg["hdr"], len(self.hdr)
        st = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.U.data.ptr, u.ctypes.data, 64, rt.memcpyHostToDevice, st.ptr)
        rt.memcpyAsync(self.RNG.data.ptr, u[16:].ctypes.data, 128, rt.memcpyHostToDevice, st.ptr)
        if ev is None:
            ev = self._u_ev[kk] = cp.cuda.Event(block=False, disable_timing=True)
        ev.record(st)
        self.counts = {k_: self.T[k_].hi for k_ in self.T}

    def launches(self, P_dev) -> None:
        """Every mark evaluated, its records written, the records binned: fixed arguments (whole tables), so the
        sequence can be captured once into a CUDA graph."""
        i32 = np.int32

        def L(kern, n, args):
            kern(((n + 127) // 128, 1, 1), (128, 1, 1), args + (i32(n),))
        nh, ns, ng, nb, nt = (CAP[k] for k in ("heads", "stems", "groups", "segs", "ties"))
        nr, nl, nq = CAP["rests"], CAP["bars"], CAP["sigs"]
        L(self.k["prep_heads"], nh, (self.dev["heads"], self.HC, self._rec("heads"), self.U))
        L(self.k["prep_stems"], ns, (self.dev["stems"], self.SM, self.HC, self.SC, self.U))
        L(self.k["prep_groups"], ng, (self.dev["groups"], self.GM, self.SC, self.GC, self.U))
        L(self.k["rec_stems"], ns, (self.dev["stems"], self.SC, self.GC, self.HC, self._rec("stems"), self.U))
        L(self.k["rec_segs"], nb, (self.dev["segs"], self.SC, self.GC, self.dev["stems"], self.HC, self._rec("segs"),
                                   self.U))
        L(self.k["rec_ties"], nt, (self.dev["ties"], self.HC, self._rec("ties"), self.U))
        nm = max(nr, nl, nq)
        self.k["rec_misc"](((nm + 127) // 128, 1, 1), (128, 1, 1),
                           (self.dev["rests"], self.dev["bars"], self.dev["sigs"], self._rec("rests"),
                            self._rec("bars"), self._rec("sigs"), self.U, i32(nr), i32(nl), i32(nq)))
        self.k["bin_recs"]((self.n_bins, 1, 1), (256, 1, 1), (self.REC, self.RNG, i32(8), self.CNT, self.LST, P_dev))
