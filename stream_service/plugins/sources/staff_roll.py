"""Staff roll: the piano's playing as proportional notation on a page of grand staves, written while it is played.

Source: Original.  Glyphs: the Leipzig music font (SIL OFL 1.1) from Verovio's data, baked to a distance atlas
        (stream_service/plugins/_lib/smufl_atlas.py).
Author: smfx
License: MIT (this file)
license_class: permissive

Listens to the piano (``listens = ("notes",)``).  x is time: a stationary page of NSYS grand-staff systems is written
left to right by a "now" cursor, wrapping to the next system; the oldest system is overwritten (its old ink fades out
just ahead of the cursor), so 30-45 s of music stays on the page.  Each system's seconds adapt to the playing (the
recent onset spacing per staff, 6.5-12 s a system), and silences longer than IDLE_S are compressed (the cursor parks;
the map from sound time to page time keeps its history, so a note heard late still lands at its exact time).

ONE flow, no second layer: a note is born, on the frame its onset arrives (self.sound's frame: SoundView onsets), at
its exact onset x and staff step, with its accidental, and a duration tail grows from the head while the key is held.
Its x never changes (except one optional short hop, below).  Then it settles in place, morphing smoothly:

1. Spelling: the key heard so far (the room builder's analysed key signature, else its Krumhansl estimate; a change
   must read the same twice, 0.5 s apart) spells the current system: the key signature fades in, and steps and signs
   glide/morph (the accidental's distance field morphs into the new sign).
2. Values: when the room's quantiser writes the note's bar (score/room.py RemoteRoom: Bar events carry the note ids),
   its head morphs to its value (filled -> hollow -> whole by interpolating the glyphs' distance fields), the chord's
   stem grows in, then flags and dots; seconds in a chord step to the stem's other side, the chord's heads align.
3. Grid: the head hops (eased, <= HOP_MAX_SP) to its quantised x only if the quantiser's provisional reading of it
   (builder.where, sampled while it waits) agreed with the committed bar (LocalAgreement-2).
4. Bar lines draw in (top to bottom) as bars are committed; the time signature fades into the system's header once the
   metre is known.  A rewrite (the metre turned) fades its phrase's old bar lines out as the new ones draw in.
5. Duration tails fade to a faint line once the value glyph carries the duration.

Drawing: every primitive (glyph, stem, ledger, tail, bar line) is a record; a splat kernel rasterises each record's box
into ink and glow coverage (atomic max), then one compose kernel lays paper, staff lines, ink, glow and the cursor.
Records are rebuilt only while a note animates (or on an event); glows (strike, held, release) and open tails are
evaluated on the GPU from the record's times.  Per frame host work is O(animating notes) + one concatenation.

Macros: energy -> glow halo size; pulse (bind note_on) -> a cursor kick; intensity -> output gain; palette / period /
drift colour the glows and cursor.
"""
from __future__ import annotations

import bisect
import collections
import logging
import math
import time

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.staff_roll")

LOOKS = ("paper", "night")
ROOM_KEY = "room"
# page geometry (staff spaces)
SYS_SP, TOP_SP, GAP_SP = 24.0, 6.5, 10.5     # a system's pitch, its treble top line, treble top -> bass top
HDR_SP, LM_SP, RM_SP = 12.0, 1.2, 2.0        # header width, margins
STEM_SP, STEM_HW, LEDGER_EXT, LEDGER_HW = 3.5, 0.065, 0.42, 0.08
TAIL_HW, TAIL_A0, TAIL_A1, BAR_HW = 0.10, 0.55, 0.16, 0.075
# motion (s)
Y_S, HOP_S, MORPH_S, STEM_S, FLAG_DELAY, FLAG_S, ACC_S, TAIL_S, BAR_S, HDR_S = (
    0.28, 0.25, 0.35, 0.30, 0.15, 0.25, 0.30, 0.6, 0.45, 0.5)
HOP_MAX_SP = 1.0
# time
IDLE_S, PARK_TAU = 1.2, 0.45                 # silence (nothing held) before the cursor parks, its slowing
S_MIN, S_MAX, S0 = 5.5, 10.0, 8.0            # seconds per system (dense ... sparse), the first
MIN_GAP_SP = 2.3                             # the spacing a system aims to give successive chords in a staff
CHORD_S = 0.06
CLOCK_JUMP_S = 5.0                           # the sound clock moving more than this in a frame (or back) is a jump
ERASE_W, OLD_DIM = 0.10, 0.6                 # old ink fades over this fraction of a system ahead of the cursor
KEY_EVERY = 0.5
MAP_KEEP_S = 150.0
STRIDE = 32                                  # floats per record (see splat: animation channels)
SPLIT = 4                                    # blocks per record (a long tail's pixels over several blocks)
MAX_RECS = 16000
NBUF = 3                                     # pinned upload buffers
PENDING = -1.0                               # an animation's start, stamped when its records are built
BUILD_BUDGET_S = 0.0012                      # per frame (s of thread time): changed notes' records rebuilt (births and
BAR_BUDGET_S = 0.0008                        # releases always), bars taken, chords settled: a burst (a rewrite, a late
SETTLE_BUDGET_S = 0.0010                     # metre: 250 notes at once) spreads over frames, oldest first

GL_BLACK, GL_HALF, GL_WHOLE, GL_SHARP, GL_FLAT, GL_NAT = 0, 1, 2, 3, 4, 5
GL_F8U, GL_F8D, GL_F16U, GL_F16D, GL_GCLEF, GL_FCLEF, GL_BRACE, GL_DOT, GL_T0 = 6, 7, 8, 9, 10, 11, 12, 13, 14
ACC_GL = {1: GL_SHARP, 2: GL_FLAT, 3: GL_NAT}
LETTERS = "cdefgab"
ORDER_SHARP, ORDER_FLAT = "fcgdaeb", "beadgcf"
SHARP_STEPS = (38, 35, 39, 36, 33, 37, 34)   # treble key-signature steps (bass: -14)
FLAT_STEPS = (34, 37, 33, 36, 32, 35, 31)
# 16ths -> (head, dots, flags); others take the longest value not over them
VALUES = {1: (GL_BLACK, 0, 2), 2: (GL_BLACK, 0, 1), 3: (GL_BLACK, 1, 1), 4: (GL_BLACK, 0, 0), 6: (GL_BLACK, 1, 0),
          8: (GL_HALF, 0, 0), 12: (GL_HALF, 1, 0), 16: (GL_WHOLE, 0, 0)}
_VKEYS = sorted(VALUES)

_SRC = HELPERS + r"""
#define STRIDE """ + str(STRIDE) + r"""
#define TPS 32.0f
#define PADSP 0.9f
#define FAR 1.0e4f

extern "C" __global__
void bake_page(unsigned char* __restrict__ page, const int W, const int H, const int night, const unsigned int seed)
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

__device__ __forceinline__ float box_d(const float dx, const float dy) {
    return dx > 0.f && dy > 0.f ? sqrtf(dx * dx + dy * dy) : fmaxf(dx, dy);
}
// the atlas's distance (sp) at (u, v) sp from glyph g's origin; FAR outside its rect
__device__ __forceinline__ float atlas_d(const float* __restrict__ A, const int AW, const float* __restrict__ GT,
                                         const int g, const float u, const float v) {
    const float* T = GT + 12 * g;
    const float tx = T[4] + u * TPS - 0.5f, ty = T[5] + v * TPS - 0.5f;
    if (tx < 0.f || ty < 0.f || tx > T[2] - 1.001f || ty > T[3] - 1.001f) return FAR;
    const int ix = (int)tx, iy = (int)ty;
    const float fx = tx - ix, fy = ty - iy;
    const float* p = A + ((int)T[1] + iy) * AW + (int)T[0] + ix;
    const float a = p[0] + (p[1] - p[0]) * fx, b = p[AW] + (p[AW + 1] - p[AW]) * fx;
    const float d = a + (b - a) * fy;
    return d >= PADSP - 1e-3f ? FAR : d;
}

// an animation channel (v0, dv, t0, dur): v0 + dv * smoothstep((now - t0) / dur); dur <= 0: v0 + dv
__device__ __forceinline__ float chan(const float* __restrict__ c, const float now) {
    if (c[3] <= 0.f) return c[0] + c[1];
    float t = fminf(fmaxf((now - c[2]) / c[3], 0.f), 1.f);
    return c[0] + c[1] * t * t * (3.f - 2.f * t);
}

// one block per record: its box's pixels -> ink and glow coverage (16-bit fixed point, atomic max).  A record:
// [0] kind (0 glyph, 1 hbox, 2 vbox, 3 open tail) [1-4] x channel [5-8] y [9-12] alpha [13] sx (glyph px/sp; box
// half thickness) [14] sy [15-18] length channel (box; glyph: the morph a -> b) [19] glyph a [20] glyph b
// [21] glow velocity [22] t_on [23] t_off (-1 held) [24] slot [25] system gen [26-29] box over the whole motion
extern "C" __global__
void splat(const float* __restrict__ R, const int n, const float* __restrict__ P, const float* __restrict__ S,
           const float* __restrict__ A, const int AW, const float* __restrict__ GT, const int atlas_ok,
           int* __restrict__ ink, int* __restrict__ glow, const int W, const int H)
{
    const int r = blockIdx.x;
    if (r >= n) return;
    const int part = blockIdx.y, nparts = gridDim.y;
    const float* Q = R + r * STRIDE;
    const int kind = (int)Q[0];
    const float sp = P[0], now = P[1], halo = P[2], gk = P[3], an = P[20];
    const int slot = (int)Q[24];
    const float* SL = S + slot * 8;
    const bool old = Q[25] < SL[0] - 0.5f;
    if (old && Q[28] < SL[1]) return;                    // old ink the cursor's erase has passed
    float alpha = chan(Q + 9, an) * (old ? SL[2] : SL[3]);
    if (alpha <= 0.002f) return;
    const float qx = chan(Q + 1, an), qy = chan(Q + 5, an);
    float bx1 = Q[28];
    float len = chan(Q + 15, an);
    if (kind == 3) {                                       // an open tail: to the slot's tail end (the cursor)
        len = fmaxf(0.f, SL[4] - qx);
        bx1 = qx + len + 2.f;
    }
    // the glow from the record's times: a strike flash, held, then a release fade
    float g = 0.f;
    const float vel = Q[21];
    if (vel > 0.f) {
        const float age = now - Q[22];
        if (Q[23] < 0.f) g = vel * (0.55f + 0.45f * __expf(-fmaxf(age, 0.f) / 0.3f));
        else {
            const float ra = now - Q[23];
            g = ra < 4.f ? vel * (0.55f * __expf(-fmaxf(ra, 0.f) / 0.6f) + 0.45f * __expf(-fmaxf(age, 0.f) / 0.3f)) : 0.f;
        }
        g *= gk;
    }
    const float hl = kind == 0 ? halo : halo * 0.5f;      // tails glow tighter than heads
    const float pad = g > 0.004f ? hl * 3.f : 1.f;
    const int x0 = max(0, (int)floorf(Q[26] - pad)), y0 = max(0, (int)floorf(Q[27] - pad));
    const int x1 = min(W - 1, (int)ceilf(bx1 + pad)), y1 = min(H - 1, (int)ceilf(Q[29] + pad));
    if (x1 < x0 || y1 < y0) return;
    const int bw = x1 - x0 + 1, npx = bw * (y1 - y0 + 1);
    const float ex = SL[1], ew = fmaxf(P[4], 1.f);
    for (int i = part * blockDim.x + threadIdx.x; i < npx; i += blockDim.x * nparts) {
        const int X = x0 + i % bw, Y = y0 + i / bw;
        const float px = X + 0.5f, py = Y + 0.5f;
        float d;
        if (kind == 0) {
            const float sx = Q[13], sy = Q[14];
            const float u = (px - qx) / sx, v = (py - qy) / sy;
            const int ga = (int)Q[19], gb = (int)Q[20];
            const float m = gb >= 0 ? len : 0.f;
            if (atlas_ok) {
                float da = m < 1.f ? atlas_d(A, AW, GT, ga, u, v) : FAR;
                if (gb >= 0 && m > 0.f) {
                    const float db = atlas_d(A, AW, GT, gb, u, v);
                    da = (da >= FAR || db >= FAR) ? (m < 0.5f ? (da >= FAR ? db : da) : (db >= FAR ? da : db))
                                                  : da + (db - da) * m;
                    if (m >= 1.f) da = db;
                }
                d = da >= FAR ? FAR : da * fminf(sx, sy);
            } else {                                        // no atlas: round heads only
                const float ux = u - 0.59f;
                d = (ga <= 2) ? (sqrtf(ux * ux + v * v * 1.4f) - 0.5f) * sx : FAR;
            }
        } else if (kind == 2) {                             // vertical box: x centre, y .. y + len
            const float ya = fminf(qy, qy + len), yb = fmaxf(qy, qy + len);
            d = box_d(fabsf(px - qx) - Q[13], fmaxf(ya - py, py - yb));
        } else {                                            // horizontal box: x .. x + len, y centre
            d = box_d(fmaxf(qx - px, px - (qx + len)), fabsf(py - qy) - Q[13]);
        }
        float a = alpha;
        if (old) a *= clamp01((px - ex) / ew);              // old ink: gone just ahead of the cursor
        if (a <= 0.002f) continue;
        const float cov = clamp01(0.5f - d) * a;
        const int idx = Y * W + X;
        if (cov > 0.002f) atomicMax(ink + idx, (int)(cov * 65535.f));
        if (g > 0.004f && d < 3.f * hl) {
            const float h = (d <= 0.f ? 1.f : __expf(-d / hl)) * g * a;
            if (h > 0.004f) atomicMax(glow + idx, (int)(fminf(h, 1.f) * 65535.f));
        }
    }
}

// paper, staff lines, ink, glow, cursor; clears the coverage buffers as it reads them
extern "C" __global__
void compose(uchar4* __restrict__ out, const unsigned char* __restrict__ page, int* __restrict__ ink,
             int* __restrict__ glow, const float* __restrict__ P, const float* __restrict__ S, const int nslot,
             const int W, const int H)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float X = x + 0.5f, Y = y + 0.5f;
    const float sp = P[0], th = P[5];
    const int night = (int)P[6];
    const float k = page[y * W + x] / 255.f * 1.2f;
    float3 c = night ? make_float3(0.040f * k, 0.044f * k, 0.060f * k) : make_float3(0.965f * k, 0.925f * k, 0.845f * k);
    const float3 inkc = make_float3(P[7], P[8], P[9]);
    const float3 ac = make_float3(P[10], P[11], P[12]);
    const float xl = P[13], xr = P[14];
    float s = 0.f;
    for (int j = 0; j < nslot; ++j) {
        const float* SL = S + j * 8;
        const float T = SL[5], B = SL[6], sa = SL[7];
        if (Y < T - th - 1.f || Y > B + 4.f * sp + th + 1.f || X < xl - 1.f || X > xr + 1.f) continue;
        for (int st = 0; st < 2; ++st) {
            const float d0 = Y - (st ? B : T);
            if (d0 < -th - 1.f || d0 > 4.f * sp + th + 1.f) continue;
            const float m = fmodf(d0 + 0.5f * sp, sp) - 0.5f * sp;
            s = fmaxf(s, clamp01(0.5f * th + 0.5f - fabsf(m)) * sa * clamp01(X - xl + 0.5f) * clamp01(xr - X + 0.5f));
        }
        if (Y >= T && Y <= B + 4.f * sp)                    // the system's opening line, joining the staves
            s = fmaxf(s, clamp01(0.5f * (th * 1.6f) + 0.5f - fabsf(X - xl)) * sa);
    }
    const float3 stc = night ? make_float3(0.50f, 0.50f, 0.56f) : make_float3(0.30f, 0.27f, 0.24f);
    const float sw = night ? 0.55f : 0.75f;
    c.x += (stc.x - c.x) * s * sw; c.y += (stc.y - c.y) * s * sw; c.z += (stc.z - c.z) * s * sw;
    const int idx = y * W + x;
    const int iv = ink[idx], gv = glow[idx];
    float i = 0.f, g = 0.f;
    if (iv) { i = iv / 65535.f; ink[idx] = 0; }
    if (gv) { g = gv / 65535.f; glow[idx] = 0; }
    c.x += (inkc.x - c.x) * i; c.y += (inkc.y - c.y) * i; c.z += (inkc.z - c.z) * i;
    if (g > 0.f) {
        if (night) {
            const float t = g * i * 0.9f, h = g * (1.f - i) * 0.6f;
            c.x += (ac.x - c.x) * t + ac.x * h; c.y += (ac.y - c.y) * t + ac.y * h; c.z += (ac.z - c.z) * t + ac.z * h;
        } else {
            const float t = g * (0.22f + 0.6f * i);
            const float3 a2 = make_float3(ac.x * 0.85f, ac.y * 0.72f, ac.z * 0.6f);
            c.x += (a2.x - c.x) * t; c.y += (a2.y - c.y) * t; c.z += (a2.z - c.z) * t;
        }
    }
    // the cursor
    const int cs = (int)P[15];
    if (cs >= 0 && cs < nslot) {
        const float* SL = S + cs * 8;
        const float top = SL[5] - 2.5f * sp, bot = SL[6] + 6.5f * sp;
        if (Y > top && Y < bot) {
            const float d = fabsf(X - P[16]);
            const float ve = smooth(top, top + 2.f * sp, Y) * (1.f - smooth(bot - 2.f * sp, bot, Y));
            const float line = clamp01(0.5f * fmaxf(1.5f, 0.11f * sp) + 0.5f - d);
            const float hh = __expf(-d / (0.9f * sp)) * (0.22f + 0.4f * P[18]);
            const float a = (line * 0.8f + hh) * ve * P[17];
            if (night) { c.x += ac.x * a; c.y += ac.y * a; c.z += ac.z * a; }
            else { const float t = fminf(a, 1.f) * 0.7f;
                   c.x += (ac.x * 0.8f - c.x) * t; c.y += (ac.y * 0.62f - c.y) * t; c.z += (ac.z * 0.45f - c.z) * t; }
        }
    }
    const float gain = P[19];
    out[idx] = make_uchar4((unsigned char)(clamp01(c.x * gain) * 255.f + 0.5f),
                           (unsigned char)(clamp01(c.y * gain) * 255.f + 0.5f),
                           (unsigned char)(clamp01(c.z * gain) * 255.f + 0.5f), 255);
}
"""


_ONE = (1.0, 0.0, 0.0, 0.0)
_ZERO = (0.0, 0.0, 0.0, 0.0)


def _S(v: float) -> tuple:
    """A still channel."""
    return (v, 0.0, 0.0, 0.0)


def _ease(t: float) -> float:
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _pal3(c, t):
    c = np.asarray(c, np.float64)
    return np.clip(c[0:3] + c[3:6] * np.cos(2.0 * math.pi * (c[6:9] * t + c[9:12])), 0.0, 1.0)


def _signature(fifths: int) -> dict:
    sig = {c: 0 for c in LETTERS}
    if fifths > 0:
        for c in ORDER_SHARP[:fifths]:
            sig[c] = 1
    elif fifths < 0:
        for c in ORDER_FLAT[:-fifths]:
            sig[c] = -1
    return sig


_SPELL: dict = {}


def spell(pitch: int, fifths: int) -> tuple[int, int]:
    """(diatonic step: letter + 7 x octave, C4 = 28; the sign shown against the key's signature: 0 none, 1 sharp,
    2 flat, 3 natural).  Accidentals apply to their own note only (proportional notation's convention)."""
    key = (pitch, fifths)
    v = _SPELL.get(key)
    if v is None:
        from stream_service.score.mei import spell as mspell
        sig = _signature(fifths)
        letter, alt, octave = mspell(pitch, fifths, sig)
        d = LETTERS.index(letter) + 7 * octave
        acc = 0 if alt == sig[letter] else (1 if alt > 0 else 2 if alt < 0 else 3)
        v = _SPELL[key] = (d, acc)
    return v


def value_of(dur16: int) -> tuple[int, int, int]:
    if dur16 >= 16:
        return VALUES[16]
    i = bisect.bisect_right(_VKEYS, max(1, dur16)) - 1
    return VALUES[_VKEYS[i]]


class TimeMap:
    """Sound time -> page time: page time runs with the music and parks in silences.  Breakpoints keep the history,
    so a note heard late (the bus delivers in clumps) lands at its exact time; a late onset into a parked stretch
    re-anchors the map from that onset (nothing was born there)."""

    def __init__(self, t: float) -> None:
        self.t = [t]
        self.u = [0.0]
        self.rate = 0.0

    def at(self, t: float) -> float:
        ts = self.t
        if t >= ts[-1]:
            return self.u[-1] + (t - ts[-1]) * self.rate
        i = bisect.bisect_right(ts, t)
        if i == 0:
            return self.u[0] - (ts[0] - t)
        t0, t1, u0, u1 = ts[i - 1], ts[i], self.u[i - 1], self.u[i]
        return u0 + (u1 - u0) * (t - t0) / max(t1 - t0, 1e-9)

    def advance(self, now: float, target: float, dt: float) -> None:
        r0 = self.rate
        r = target if dt <= 0 else r0 + (target - r0) * (1.0 - math.exp(-dt / PARK_TAU))
        if r < 1e-3:
            r = 0.0
        if r > 0.999:
            r = 1.0
        if now > self.t[-1]:
            u = self.u[-1] + (now - self.t[-1]) * r0
            if (r == r0 and r0 in (0.0, 1.0) and len(self.t) >= 2
                    and abs(self.u[-1] - self.u[-2] - (self.t[-1] - self.t[-2]) * r0) < 1e-6):
                self.t[-1], self.u[-1] = now, u            # a straight run: extend its last point
            else:
                self.t.append(now)
                self.u.append(u)
        self.rate = r
        if len(self.t) > 4 and now - self.t[1] > MAP_KEEP_S:
            del self.t[0], self.u[0]

    def anchor(self, t_on: float, now: float) -> None:
        """An onset at ``t_on``: page time runs at full speed from there."""
        if t_on >= self.t[-1]:                          # full speed from the onset, not from the last breakpoint
            if t_on > self.t[-1] and self.rate != 1.0:
                u0 = self.at(t_on)
                self.t.append(t_on)
                self.u.append(u0)
            self.rate = 1.0
            return
        if self.at(now) - self.at(t_on) >= (now - t_on) * 0.98:
            self.rate = 1.0
            return
        u0 = self.at(t_on)
        i = bisect.bisect_right(self.t, t_on)
        del self.t[i:], self.u[i:]
        self.t.append(t_on)
        self.u.append(u0)
        self.t.append(now)
        self.u.append(u0 + (now - t_on))
        self.rate = 1.0

    def shift(self, d: float) -> None:
        """The sound clock jumped by ``d`` (s): the history moves with it."""
        self.t = [t + d for t in self.t]


class _Sys:
    __slots__ = ("gen", "slot", "u0", "pxs", "u1", "fifths", "metre", "hdr_t", "hdr_from", "recs", "dirty", "born",
                 "expire")

    def __init__(self, gen, slot, u0, pxs, width):
        self.gen, self.slot, self.u0, self.pxs = gen, slot, u0, pxs
        self.u1 = u0 + width / pxs
        self.fifths = 0
        self.metre = None
        self.hdr_t = -1e9
        self.hdr_from = (0, None)
        self.recs = None
        self.dirty = True
        self.expire = 0.0


class _Note:
    __slots__ = ("pitch", "vel", "t_on", "t_off", "u", "gen", "slot", "x", "x_from", "t_x", "staff", "d", "acc",
                 "y_from", "t_y", "led_from", "acc_from", "t_acc", "acc_col", "shift", "bid", "bnote", "q",
                 "head", "head_from", "dots", "flags", "t_val", "lead", "stem", "where", "recs", "expire", "fifths",
                 "conf", "x_exact")

    def __init__(self, pitch, vel, t_on):
        self.pitch, self.vel, self.t_on, self.t_off = pitch, vel, t_on, None
        self.t_x = self.t_y = self.t_acc = self.t_val = None
        self.acc_from = 0
        self.led_from = None
        self.acc_col = 0
        self.shift = 0.0
        self.bid = None
        self.bnote = None
        self.q = None
        self.head = self.head_from = GL_BLACK
        self.dots = self.flags = 0
        self.lead = None
        self.stem = None
        self.where = None
        self.recs = None
        self.expire = 0.0
        self.conf = False


class StaffRoll(Source):
    name = "staff_roll"
    label = "Staff roll"
    description = ("The piano's playing written as proportional notation on a page of grand staves: each note appears "
                   "at its exact time as it is played, then settles into its written value.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=1.0, period=1.0, drift=0.01),
            "look": enum(LOOKS, "paper", "Look", tier="structural", basic=True),
            "systems": num(3, 4, 4, "Systems on the page", tier="structural", integer=True),
            "note_glow": num(0.0, 1.5, 0.8, "Note glow", basic=True),
            "span": num(0.6, 1.6, 1.0, "Seconds per line (x)", tier="structural"),
        }

    # -- setup ------------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.direct = (self.w, self.h) == (ctx.width, ctx.height)
        self.buf = None if self.direct else cp.zeros((self.h, self.w, 4), cp.uint8)
        mod = raw_module(_SRC)
        self.k_splat = mod.get_function("splat")
        self.k_comp = mod.get_function("compose")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.look = params["look"] if params["look"] in LOOKS else "paper"
        self.nsys = int(params["systems"])
        self.span = float(params["span"])
        n = self.nsys
        self.sp = sp = max(4.0, round(0.955 * self.h / (n * SYS_SP), 2))
        self.pitch_px = SYS_SP * sp
        top = 0.5 * (self.h - n * self.pitch_px)
        self.T = [top + j * self.pitch_px + TOP_SP * sp for j in range(n)]
        self.B = [t + GAP_SP * sp for t in self.T]
        self.x_l = LM_SP * sp + 1.6 * sp                    # the system's opening line (the brace left of it)
        self.x_r = self.w - RM_SP * sp
        self.x0 = self.x_l + HDR_SP * sp                    # where writing starts
        self.width = self.x_r - 1.5 * sp - self.x0
        self.th = max(1.0, 0.1 * sp)
        self.page = cp.zeros((self.h, self.w), cp.uint8)
        launch_2d(mod.get_function("bake_page"), self.w, self.h,
                  (self.page, np.int32(self.w), np.int32(self.h), np.int32(LOOKS.index(self.look)),
                   np.uint32(self.seed)))
        self.ink = cp.zeros(self.h * self.w, cp.int32)
        self.glow = cp.zeros(self.h * self.w, cp.int32)
        # the glyph atlas (Leipzig distance field)
        self.atlas_ok = 0
        self.GT = np.zeros((24, 12), np.float32)
        try:
            from stream_service.plugins._lib.smufl_atlas import atlas
            self.A, self.GT = atlas(cp, raw_module)
            self.atlas_ok = 1
        except Exception as e:                              # degrade: round heads, no clefs or signs
            log.warning("staff_roll: no glyph atlas (%s); drawing round heads", e)
            self.A = cp.zeros((2, 2), cp.float32)
            self.GT[:, 10] = 1.18
        self.AW = int(self.A.shape[1])
        self.GT_dev = cp.asarray(self.GT.ravel())
        T = self.GT
        self.grect = [(-T[g, 4] / 32.0, -T[g, 5] / 32.0, (T[g, 2] - T[g, 4]) / 32.0, (T[g, 3] - T[g, 5]) / 32.0)
                      for g in range(len(T))]
        self.adv = [float(T[g, 10]) or 1.0 for g in range(len(T))]
        self.head_w = self.adv[GL_BLACK] or 1.18
        # uploads: params, slots, records in one pinned buffer, double-buffered
        self.NP, self.NS = 32, 8 * n
        self.n_up = self.NP + self.NS + MAX_RECS * STRIDE
        self.up_dev = cp.zeros(self.n_up, cp.float32)
        # three pinned halves: a GPU two frames behind never makes the render thread wait (it waited, spinning,
        # 11-13 ms on a busy GPU: the stress run's last spikes); a wait, if any, sleeps (a blocking-sync event)
        self._pin = [cp.cuda.alloc_pinned_memory(4 * self.n_up) for _ in range(NBUF)]
        self.up_host = [np.frombuffer(m, np.float32, self.n_up) for m in self._pin]
        self.up_ev = [None] * NBUF
        self.up_k = 0
        self.P_dev = self.up_dev[:self.NP]
        self.S_dev = self.up_dev[self.NP:self.NP + self.NS]
        self.R_dev = self.up_dev[self.NP + self.NS:]
        # first uses here, not on the first frame with notes (a cold worker's first frame: the spelling's import of
        # score.mei was 50 ms of it, the record kernel's first launch a few more)
        for p_ in (60, 61):
            spell(p_, 0)
        self.k_splat((1, SPLIT, 1), (128, 1, 1),
                     (self.R_dev, np.int32(0), self.P_dev, self.S_dev, self.A, np.int32(self.AW), self.GT_dev,
                      np.int32(self.atlas_ok), self.ink, self.glow, np.int32(self.w), np.int32(self.h)))
        np.array([(0.0,) * STRIDE], np.float32).reshape(-1, STRIDE)
        # the room's transcription (bars, metre, key, note ids), shared with the other screens
        self.room = None
        self.builder = None
        try:
            from stream_service.score.room import RemoteRoom
            self.room = RemoteRoom.join(ROOM_KEY, self)
            self.builder = self.room.builder
        except Exception as e:
            log.warning("staff_roll: no room transcription (%s); heads and tails only", e)
        self.seen_rev = 0
        # state
        self.t_sound = None
        self.t_base = None
        self.map = None
        self.systems: dict[int, _Sys] = {}
        self.gen = -1
        self.cursor = None
        self.notes: list[_Note] = []
        self.open: dict[int, _Note] = {}                    # pitch -> the note held
        self.by_bid: dict[int, _Note] = {}
        self.by_pitch: dict[int, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=64))
        self.last_bid = 0
        self.recent_on = collections.deque(maxlen=256)      # (t_on, staff) for the density
        self.last_activity = None
        self.key_t = -1e9
        self.key_cand = None
        self.fifths = 0
        self.metre = None
        self.phrase = 0
        self.bars: dict = {}                                # (phrase, pos) -> bar line record state
        self.chords: dict = {}                              # chord key -> {"notes": [...], "pending": set()}
        self.staff_t = -1e9
        self.staff_ver = None
        self.where_t = -1e9
        self.clock = 0.0
        self.drift = 0.0
        self.kick = 0.0
        self.stats = collections.Counter()
        self.n_recs = 0
        self.fresh: list = []                               # notes born this frame (built at once)
        self.urgent: list = []                              # releases, held notes on a new line (built at once)
        self.stale: collections.OrderedDict = collections.OrderedDict()   # changed notes, rebuilt in turn
        self.expiring: list = []
        self.gen_notes: dict = collections.defaultdict(list)
        self.gen_blk: dict = {}
        self.gen_dirty: set = set()
        self.bar_q: collections.deque = collections.deque()
        self.settle_q: collections.deque = collections.deque()
        self.bar_blk = None
        self.bars_dirty = True
        self.dying: list = []
        self.dbg: dict = {}
        self.dbg_built = 0
        self.last_settle = None

    # -- geometry ---------------------------------------------------------------------------------------------------
    def _step_y(self, slot: int, staff: int, d: float) -> float:
        sp = self.sp
        if staff == 0:
            return self.T[slot] + (38 - d) * 0.5 * sp
        return self.B[slot] + (26 - d) * 0.5 * sp

    def _ledgers(self, staff: int, d: int) -> list:
        if staff == 0:
            if d >= 40:
                return list(range(40, d + 1, 2))
            if d <= 28:
                return list(range(28, d - 1, -2))
            return []
        if d >= 28:
            return list(range(28, d + 1, 2))
        if d <= 16:
            return list(range(16, d - 1, -2))
        return []

    def _sys_at(self, u: float):
        s = self.systems.get(self.gen)
        while s is not None and u < s.u0:
            s = self.systems.get(s.gen - 1)
        return s

    def _ux(self, u: float):
        """Page time -> (system, x px), or (None, None) before the page's oldest system."""
        s = self._sys_at(u)
        if s is None:
            return None, None
        return s, self.x0 + (min(u, s.u1) - s.u0) * s.pxs

    # -- records ----------------------------------------------------------------------------------------------------
    def _glyph(self, out, X, Y, ga, gb=-1, M=None, A=None, sx=None, sy=None, slot=0, gen=0, vel=0.0, t_on=0.0,
               t_off=-1.0):
        """A glyph record: X, Y, A, M are channels (v0, dv, t0, dur); M morphs glyph a into b."""
        sx = self.sp if sx is None else sx
        sy = sx if sy is None else sy
        r = self.grect[ga]
        x0, y0, x1, y1 = r
        if gb >= 0:
            q = self.grect[gb]
            x0, y0, x1, y1 = min(x0, q[0]), min(y0, q[1]), max(x1, q[2]), max(y1, q[3])
        xa, xb = (X[0], X[0] + X[1]) if X[1] >= 0 else (X[0] + X[1], X[0])
        ya, yb = (Y[0], Y[0] + Y[1]) if Y[1] >= 0 else (Y[0] + Y[1], Y[0])
        A = A or _ONE
        M = M or _ZERO
        out.append((0.0, *X, *Y, *A, sx, sy, *M, ga, gb, vel, t_on, t_off, slot, gen,
                    xa + x0 * sx, ya + y0 * sy, xb + x1 * sx, yb + y1 * sy, 0.0, 0.0))

    def _hbox(self, out, X, Y, L, hw, A=None, slot=0, gen=0, kind=1.0, vel=0.0, t_on=0.0, t_off=-1.0):
        xa, xb = (X[0], X[0] + X[1]) if X[1] >= 0 else (X[0] + X[1], X[0])
        ya, yb = (Y[0], Y[0] + Y[1]) if Y[1] >= 0 else (Y[0] + Y[1], Y[0])
        lmax = max(L[0], L[0] + L[1], 0.0)
        out.append((kind, *X, *Y, *(A or _ONE), hw, 0.0, *L, 0, 0, vel, t_on, t_off, slot, gen,
                    xa - hw, ya - hw, xb + lmax + hw, yb + hw, 0.0, 0.0))

    def _vbox(self, out, X, y, L, hw, A=None, slot=0, gen=0):
        xa, xb = (X[0], X[0] + X[1]) if X[1] >= 0 else (X[0] + X[1], X[0])
        lo, hi = min(0.0, L[0], L[0] + L[1]), max(0.0, L[0], L[0] + L[1])
        out.append((2.0, *X, y, 0.0, 0.0, 0.0, *(A or _ONE), hw, 0.0, *L, 0, 0, 0.0, 0.0, -1.0, slot, gen,
                    xa - hw, y + lo - hw, xb + hw, y + hi + hw, 0.0, 0.0))

    def _stamp(self, n: _Note) -> None:
        """An event's animations start when its records are built (a burst is spread over frames)."""
        c = self.clock
        if n.t_x == PENDING:
            n.t_x = c
        if n.t_y == PENDING:
            n.t_y = c
        if n.t_acc == PENDING:
            n.t_acc = c
        if n.t_val == PENDING:
            n.t_val = c

    def _note_recs(self, n: _Note):
        """The note's records, built once per event: every motion (hop, glide, morph, stem growth, fades) is a
        channel the kernel evaluates on the scene clock."""
        self._stamp(n)
        sp = self.sp
        out: list = []
        slot, gen = n.slot, n.gen
        tb = self.t_base
        ton = n.t_on - tb
        toff = -1.0 if n.t_off is None else n.t_off - tb
        c = self.clock
        xt = n.x + n.shift
        X = (n.x_from, xt - n.x_from, n.t_x, HOP_S) if n.t_x is not None else (xt, 0.0, 0.0, 0.0)
        y_to = self._step_y(slot, n.staff, n.d)
        Y = (n.y_from, y_to - n.y_from, n.t_y, Y_S) if n.t_y is not None else (y_to, 0.0, 0.0, 0.0)
        expire = 0.0

        def off(dx):
            return (X[0] + dx, X[1], X[2], X[3])

        lead = n.lead if n.lead is not None else n
        # ledger lines (the old ones fade as the new ones come)
        hw = self.head_w * sp
        lx = off(-LEDGER_EXT * sp)
        ll = (hw + 2 * LEDGER_EXT * sp, 0.0, 0.0, 0.0)
        gliding = n.t_y is not None and c < n.t_y + Y_S
        a_in = (0.0, 1.0, n.t_y, Y_S) if gliding else _ONE
        for dd in self._ledgers(n.staff, n.d):
            self._hbox(out, lx, _S(self._step_y(slot, n.staff, dd)), ll, LEDGER_HW * sp, a_in, slot, gen)
        if gliding and n.led_from is not None:
            st0, d0 = n.led_from
            for dd in self._ledgers(st0, d0):
                self._hbox(out, lx, _S(self._step_y(slot, st0, dd)), ll, LEDGER_HW * sp, (1.0, -1.0, n.t_y, Y_S),
                           slot, gen)
            expire = max(expire, n.t_y + Y_S)
        # the tail: strong while provisional, faint once the value carries the duration
        TA = (TAIL_A0, TAIL_A1 - TAIL_A0, n.t_val, TAIL_S) if n.t_val is not None else (TAIL_A0, 0.0, 0.0, 0.0)
        tdx = hw * 0.92
        TX = off(tdx)
        thw = TAIL_HW * sp
        if n.t_off is None:
            if gen == self.gen:
                self._hbox(out, TX, Y, _ZERO, thw, TA, slot, gen, 3.0, n.vel, ton, toff)
            else:
                xs = X[0] + tdx
                self._hbox(out, TX, Y, (self.x_r - xs, -X[1], X[2], X[3]), thw, TA, slot, gen, 1.0, n.vel, ton,
                           toff)
                for g in range(gen + 1, self.gen + 1):
                    s = self.systems.get(g)
                    if s is None:
                        continue
                    yy = _S(self._step_y(s.slot, n.staff, n.d))
                    xc = _S(self.x0 - 0.3 * sp)
                    if g == self.gen:
                        self._hbox(out, xc, yy, _ZERO, thw, TA, s.slot, g, 3.0, n.vel, ton, toff)
                    else:
                        self._hbox(out, xc, yy, _S(self.x_r - self.x0 + 0.3 * sp), thw, TA, s.slot, g, 1.0, n.vel,
                                   ton, toff)
        else:
            se, xe = self._ux(self.map.at(n.t_off))
            xs = X[0] + tdx
            if se is None or se.gen <= gen:
                xe = xs if xe is None else xe
                self._hbox(out, TX, Y, (max(0.0, xe - xs), -X[1], X[2], X[3]), thw, TA, slot, gen, 1.0, n.vel, ton,
                           toff)
            else:
                self._hbox(out, TX, Y, (self.x_r - xs, -X[1], X[2], X[3]), thw, TA, slot, gen, 1.0, n.vel, ton,
                           toff)
                for g in range(gen + 1, se.gen + 1):
                    s = self.systems.get(g)
                    if s is None:
                        continue
                    yy = _S(self._step_y(s.slot, n.staff, n.d))
                    end = xe if g == se.gen else self.x_r
                    self._hbox(out, _S(self.x0 - 0.3 * sp), yy, _S(end - self.x0 + 0.3 * sp), thw, TA, s.slot, g,
                               1.0, n.vel, ton, toff)
        # the head (its glow from its times, on the GPU)
        if n.t_val is not None and n.head_from != n.head:
            self._glyph(out, X, Y, n.head_from, n.head, (0.0, 1.0, n.t_val, MORPH_S), slot=slot, gen=gen,
                        vel=n.vel, t_on=ton, t_off=toff)
        else:
            self._glyph(out, X, Y, n.head, slot=slot, gen=gen, vel=n.vel, t_on=ton, t_off=toff)
        # the accidental
        if n.acc or n.acc_from:
            moving = n.t_acc is not None and c < n.t_acc + ACC_S
            base = -n.acc_col * 1.05 * sp - 0.2 * sp
            if moving and n.acc_from and n.acc and n.acc_from != n.acc:
                ga, gb = ACC_GL[n.acc_from], ACC_GL[n.acc]
                self._glyph(out, off(base - self.adv[gb] * sp), Y, ga, gb, (0.0, 1.0, n.t_acc, ACC_S), slot=slot,
                            gen=gen)
            elif moving and n.acc_from and n.acc_from != n.acc:
                g = ACC_GL[n.acc_from]
                self._glyph(out, off(base - self.adv[g] * sp), Y, g, A=(1.0, -1.0, n.t_acc, ACC_S), slot=slot,
                            gen=gen)
                expire = max(expire, n.t_acc + ACC_S)
            elif n.acc:
                g = ACC_GL[n.acc]
                A = (0.0, 1.0, n.t_acc, ACC_S) if moving and n.acc_from == 0 else _ONE
                self._glyph(out, off(base - self.adv[g] * sp), Y, g, A=A, slot=slot, gen=gen)
        # value: dots on every head, the stem and flags on the chord's lead
        if n.t_val is not None:
            AS = (0.0, 1.0, n.t_val + 0.05, STEM_S)
            if n.dots:
                dy = -0.5 * sp if self._on_line(n.staff, n.d) else 0.0
                dx = ((self.head_w if n.head != GL_WHOLE else self.adv[GL_WHOLE]) + 0.35) * sp
                self._glyph(out, off(dx), (Y[0] + dy, Y[1], Y[2], Y[3]), GL_DOT, A=AS, slot=slot, gen=gen)
            if n.stem is not None and lead is n:
                up, y0, tip = n.stem
                ga = self.GT[GL_BLACK]
                sdx = ga[6] * sp - STEM_HW * sp if up else ga[8] * sp + STEM_HW * sp
                self._vbox(out, off(sdx), y0, (0.0, tip - y0, n.t_val + 0.05, STEM_S), STEM_HW * sp, None, slot, gen)
                if n.flags:
                    g = (GL_F16U if up else GL_F16D) if n.flags >= 2 else (GL_F8U if up else GL_F8D)
                    self._glyph(out, off(sdx - STEM_HW * sp), _S(tip), g, A=(0.0, 1.0, n.t_val + FLAG_DELAY, FLAG_S),
                                slot=slot, gen=gen)
        n.expire = expire if expire > c else 0.0
        return out

    @staticmethod
    def _on_line(staff: int, d: int) -> bool:
        return (d - (30 if staff == 0 else 18)) % 2 == 0

    def _hdr_recs(self, s: _Sys, now: float):
        sp = self.sp
        out: list = []
        j, g = s.slot, s.gen
        T, B = self.T[j], self.B[j]
        act = now < s.hdr_t + HDR_S
        hgt = B + 4 * sp - T
        self._glyph(out, _S(self.x_l - 1.15 * sp), _S(B + 4 * sp), GL_BRACE, sx=sp * 1.7, sy=hgt / 4.0, slot=j,
                    gen=g)
        self._glyph(out, _S(self.x_l + 0.9 * sp), _S(T + 3 * sp), GL_GCLEF, slot=j, gen=g)
        self._glyph(out, _S(self.x_l + 0.9 * sp), _S(B + 1 * sp), GL_FCLEF, slot=j, gen=g)
        kx = self.x_l + 4.3 * sp
        a_out = (1.0, -1.0, s.hdr_t, HDR_S)
        a_in = (0.0, 1.0, s.hdr_t, HDR_S)

        def keysig(f, A):
            n = abs(f)
            if n == 0:
                return 0.0
            step = min(1.0, 5.2 / n) * sp
            gl = GL_SHARP if f > 0 else GL_FLAT
            steps = SHARP_STEPS if f > 0 else FLAT_STEPS
            for i in range(n):
                for st, o in ((0, 0), (1, -14)):
                    self._glyph(out, _S(kx + i * step), _S(self._step_y(j, st, steps[i] + o)), gl, A=A, slot=j, gen=g)
            return n * step

        def timesig(m, x, A):
            if m is None:
                return
            beats, unit = m
            num_, den = (beats * 3, 8) if unit == 6 else (beats, 4)
            for _st, base in ((0, T), (1, B)):
                for val, yy in ((num_, base + 1.0 * sp), (den, base + 3.0 * sp)):
                    ds = str(val)
                    w = 1.9 * sp * len(ds)
                    for i, ch in enumerate(ds):
                        self._glyph(out, _S(x + (w * 0.5 - 0.95 * sp * len(ds)) + i * 1.9 * sp), _S(yy),
                                    GL_T0 + int(ch), A=A, slot=j, gen=g)

        f0, m0 = s.hdr_from
        if act and f0 != s.fifths:
            w0 = keysig(f0, a_out)
            w1 = keysig(s.fifths, a_in)
        else:
            w0 = w1 = keysig(s.fifths, _ONE)
        if act and (m0 != s.metre or f0 != s.fifths):
            timesig(m0, kx + w0 + 0.5 * sp, a_out)
            timesig(s.metre, kx + w1 + 0.5 * sp, a_in)
        else:
            timesig(s.metre, kx + w1 + 0.5 * sp, _ONE)
        s.dirty = False
        s.expire = s.hdr_t + HDR_S + 0.02 if act else 0.0
        return out

    # -- time and systems --------------------------------------------------------------------------------------------
    def _pxs(self) -> float:
        """Pixels a second for a new system: successive chords in a staff ~MIN_GAP_SP apart at the playing's quick
        end (its 25th percentile spacing over the last 12 s), within S_MIN..S_MAX seconds a system."""
        span = self.span
        lo, hi = self.width / (S_MAX * span), self.width / (S_MIN * span)
        now = self.t_sound or 0.0
        iois = []
        for st in (0, 1):
            ts = [t for t, s in self.recent_on if s == st and now - t < 12.0]
            last = None
            for t in ts:
                if last is not None and t - last > CHORD_S:
                    iois.append(t - last)
                if last is None or t - last > CHORD_S:
                    last = t
        if len(iois) < 4:
            return min(hi, max(lo, self.width / (S0 * span)))
        iois.sort()
        q = iois[len(iois) // 6]
        return min(hi, max(lo, MIN_GAP_SP * self.sp / max(q, 1e-3)))

    def _new_system(self, u0: float) -> _Sys:
        self.gen += 1
        g = self.gen
        s = _Sys(g, g % self.nsys, u0, self._pxs(), self.width)
        s.fifths = self.fifths
        s.metre = self.metre
        prev = self.systems.get(g - self.nsys)
        s.hdr_from = (prev.fifths, prev.metre) if prev is not None else (0, None)
        s.hdr_t = s.born = self.clock
        self.systems[g] = s
        self.systems.pop(g - self.nsys - 1, None)
        for n in self.open.values():                       # held notes continue on the new line
            self._touch(n, True)
        lo = g - self.nsys
        for og in [k for k in self.gen_notes if k < lo]:   # a system left the page: its notes go, in one step
            del self.gen_notes[og]
            self.gen_blk.pop(og, None)
        if self.notes and self.notes[0].gen < lo:
            self.notes = [n for n in self.notes if n.gen >= lo]
            self.stale = collections.OrderedDict((n, None) for n in self.stale if n.gen >= lo)
            self.expiring = [n for n in self.expiring if n.gen >= lo]
        self.bars_dirty = True
        self.stats["systems"] += 1
        return s

    def _advance(self, now: float, dt: float, held: bool) -> None:
        if self.map is None:
            return
        active = held or (self.last_activity is not None and now - self.last_activity < IDLE_S)
        self.map.advance(now, 1.0 if active else 0.0, dt)
        u = self.map.at(now)
        s = self.systems.get(self.gen)
        k = 0
        while s is not None and u >= s.u1:
            k += 1
            if k > self.nsys:                               # far behind (it never should be): a fresh line here,
                s = self._new_system(u)                     # never a loop over every line between
                break
            s = self._new_system(s.u1)

    # -- the music --------------------------------------------------------------------------------------------------
    def _listen(self, dt: float):
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        if f is None:
            return None
        t = float(f.t_ms) / 1000.0
        if not (t > 0.0 and math.isfinite(t)):              # a Sound not updated yet (the renderer primes a scene
            return None                                     # before its session's first frame: t_ms 0): nothing heard
        if self.t_base is None:                             # the page starts at this frame's first onset (the
            t0 = min([t] + [t - float(a) for _p, _v, a in f.onsets if float(a) < 2.0])   # bus delivers in clumps)
            self.t_base = t0
            self.map = TimeMap(t0)
            self._new_system(0.0)
        elif t < self.t_sound - 0.25 or t - self.t_sound > CLOCK_JUMP_S:
            self._shift_clock(t - self.t_sound - dt)        # the sound clock jumped (a new feed): fold the jump out
        self.t_sound = t
        bars, rev = ([], 0)
        if self.room is not None:
            try:
                bars, rev = self.room.tick(self, f, t)
            except Exception as e:                          # never let the transcription stop the drawing
                log.warning("staff_roll: room tick: %s", e)
        held = bool(self.open)
        evs = sorted([(t - float(a), 1, int(p_), float(v)) for p_, v, a in f.onsets]
                     + [(t - float(a), 0, int(p_), 0.0) for p_, _v, a in f.releases])   # in time order
        if f.onsets:
            self.map.anchor(min(e[0] for e in evs if e[1]), t)
            held = True
        self._advance(t, dt, held)
        self._key(t)
        for te, on, p_, v in evs:
            if on:
                self._birth(p_, v, te)
            else:
                n = self.open.pop(p_, None)
                if n is not None:
                    n.t_off = max(n.t_on + 0.02, te)
                    self._touch(n, True)
                self.last_activity = max(self.last_activity or te, te)
        if self.open:
            self.last_activity = t
            nt = f.notes                                    # heal: a key up that we never saw released
            for p in [p for p in self.open if nt[p, 3] >= 0.0 and math.isfinite(float(nt[p, 3]))]:
                n = self.open.pop(p)
                n.t_off = max(n.t_on + 0.02, t - float(nt[p, 3]))
                self._touch(n, True)
        if self.builder is not None:
            self._match()
            self._staves(t)
            self._wheres(t)
            self.bar_q.extend(bars)
            if self.bar_q:                                   # a burst (a rewrite, a late metre) over frames
                end = time.thread_time() + BAR_BUDGET_S
                while self.bar_q:
                    self._bar(self.bar_q.popleft())
                    if time.thread_time() > end:
                        break
            if self.settle_q:
                end = time.thread_time() + SETTLE_BUDGET_S
                while self.settle_q:
                    key, notes = self.settle_q.popleft()
                    self._settle(key, notes)
                    if time.thread_time() > end:
                        break
        return f

    def _shift_clock(self, d: float) -> None:
        """Every time the scene holds moves by ``d`` (s), so the page carries on as if the sound clock had not jumped
        (the glows' times are relative to t_base, which moves too: no records change)."""
        self.stats["clock_jumps"] += 1
        log.warning("staff_roll: the sound clock jumped %.1f s; folded out", d)
        self.t_base += d
        self.map.shift(d)
        if self.last_activity is not None:
            self.last_activity += d
        self.key_t += d
        self.staff_t += d
        self.where_t += d
        seen = set()
        for ns in self.gen_notes.values():
            for n in ns:
                if id(n) in seen:
                    continue
                seen.add(id(n))
                n.t_on += d
                if n.t_off is not None:
                    n.t_off += d
        self.recent_on = collections.deque(((t + d, st) for t, st in self.recent_on), maxlen=self.recent_on.maxlen)

    def _key(self, t: float) -> None:
        """The key the current system is spelled in: the builder's (analysed signature, else Krumhansl), a change
        read twice KEY_EVERY apart (LocalAgreement-2)."""
        b = self.builder
        if b is None or t - self.key_t < KEY_EVERY:
            return
        self.key_t = t
        try:
            f = b.key_fifths
            if f is None:
                if sum(b.pc_weight) <= 0.0:
                    return
                f = b.key()[2]
            else:
                f = b._shown(f)
        except Exception:
            return
        if f == self.fifths:
            self.key_cand = None
            return
        if self.key_cand != f:
            self.key_cand = f
            return
        self.key_cand = None
        self.fifths = f
        s = self.systems.get(self.gen)
        if s is None:
            return
        s.hdr_from = (s.fifths, s.metre)
        s.fifths = f
        s.hdr_t = self.clock
        s.dirty = True
        for n in self.gen_notes.get(s.gen, ()):             # re-spell the current system (glides, sign morphs)
            self._respell(n, f)
        self.stats["key_changes"] += 1

    def _respell(self, n: _Note, f: int) -> None:
        d, acc = spell(n.pitch, f)
        if d == n.d and acc == n.acc:
            n.fifths = f
            return
        if d != n.d:
            n.y_from = self._cur_y(n)
            n.led_from = (n.staff, n.d)
            n.t_y = PENDING
            n.d = d
        if acc != n.acc:
            n.acc_from = n.acc
            n.acc = acc
            n.t_acc = PENDING
        n.fifths = f
        self._touch(n)
        r = n.lead
        if n.conf and r is not None and r.stem is not None and isinstance(r.where, list):
            r.stem = self._stem(r.where, n.staff, r.slot, r.head, r.stem[0])
            self._touch(r)

    def _x_now(self, n: _Note) -> float:
        xt = n.x + n.shift
        if n.t_x is None:
            return xt
        if n.t_x == PENDING:
            return n.x_from
        return n.x_from + (xt - n.x_from) * _ease((self.clock - n.t_x) / HOP_S)

    def _cur_y(self, n: _Note) -> float:
        y_to = self._step_y(n.slot, n.staff, n.d)
        if n.t_y is None:
            return y_to
        if n.t_y == PENDING:
            return n.y_from
        return n.y_from + (y_to - n.y_from) * _ease((self.clock - n.t_y) / Y_S)

    def _birth(self, p: int, vel: float, t_on: float) -> None:
        self.last_activity = max(self.last_activity or t_on, t_on)
        old = self.open.pop(p, None)
        if old is not None:                                # struck again while held
            old.t_off = max(old.t_on + 0.02, t_on)
            self._touch(old, True)
        s, x = self._ux(self.map.at(t_on))
        if s is None:
            return
        n = _Note(p, vel, t_on)
        n.u = self.map.at(t_on)
        n.gen, n.slot = s.gen, s.slot
        n.x = n.x_from = n.x_exact = x
        n.staff = self._guess_staff(p, t_on)
        n.fifths = s.fifths
        n.d, n.acc = spell(p, s.fifths)
        n.y_from = self._step_y(n.slot, n.staff, n.d)
        # a chord's seconds and signs don't pile up: the upper of a second steps right, signs take columns
        cols = []
        for m in reversed(self.notes[-12:]):
            if t_on - m.t_on > CHORD_S:
                break
            if m.staff != n.staff or m.gen != n.gen:
                continue
            if abs(m.d - n.d) == 1 and m.shift == 0.0 and n.d > m.d:
                n.shift = self.head_w * self.sp
            if m.acc and abs(m.d - n.d) < 6:
                cols.append(m.acc_col)
        if n.acc:
            c = 0
            while c in cols:
                c += 1
            n.acc_col = c
        self.notes.append(n)
        self.gen_notes[n.gen].append(n)
        self.fresh.append(n)
        self.open[p] = n
        self.by_pitch[p].append(n)
        self.recent_on.append((t_on, n.staff))
        self.stats["births"] += 1

    def _guess_staff(self, p: int, t_on: float) -> int:
        """Before the room's hands: a lone line keeps its staff within 2 ledger lines, otherwise split at middle C."""
        for m in reversed(self.notes[-4:]):
            if t_on - m.t_on < 0.5 and abs(m.pitch - p) <= 9:
                if m.staff == 0 and p >= 57 or m.staff == 1 and p <= 64:
                    return m.staff
        return 0 if p >= 60 else 1

    def _match(self) -> None:
        """The room's builder note ids -> our notes (same pitch, same onset: both clocks are the bus's)."""
        notes = self.builder.notes
        if not notes or notes[-1].id <= self.last_bid:
            return
        i = len(notes)
        while i > 0 and notes[i - 1].id > self.last_bid:
            i -= 1
        for bn in notes[i:]:
            best = None
            for n in reversed(self.by_pitch.get(bn.pitch, ())):
                if n.bid is None and abs(n.t_on - bn.t_on) < 0.03:
                    best = n
                    break
                if n.t_on < bn.t_on - 1.0:
                    break
            if best is not None:
                best.bid, best.bnote = bn.id, bn
                self.by_bid[bn.id] = best
        self.last_bid = notes[-1].id
        if len(self.by_bid) > 4096:
            self.by_bid = {k: v for k, v in self.by_bid.items() if v.t_on > self.t_sound - 90.0}

    def _staves(self, t: float) -> None:
        """Unwritten notes follow the builder's staff (its hands) while they wait for their bar (a glide)."""
        b = self.builder
        if t - self.staff_t < 0.4 or b.ver == self.staff_ver:
            return
        self.staff_t, self.staff_ver = t, b.ver
        ws = [n for n in self.notes[-24:] if n.bnote is not None and not n.conf and t - n.t_on < 5.0]
        if not ws:
            return
        try:
            sts = b.staves([n.bnote for n in ws])
        except Exception:
            return
        for n, st in zip(ws, sts):
            if st != n.staff:
                self._restaff(n, st)

    def _restaff(self, n: _Note, st: int) -> None:
        n.y_from = self._cur_y(n)
        n.led_from = (n.staff, n.d)
        n.t_y = PENDING
        n.staff = st
        self._touch(n)

    def _wheres(self, t: float) -> None:
        """The quantiser's provisional reading of each waiting note (bar, 16th), for LocalAgreement-2."""
        if t - self.where_t < 0.12:
            return
        self.where_t = t
        b = self.builder
        for n in self.notes[-48:]:
            if n.conf or n.bnote is None:
                continue
            try:
                w = b.where(n.t_on)
            except Exception:
                return
            if w is not None and not w[2]:
                n.where = (w[0], int(round(w[1] * b.bar_units)))

    def _bar(self, bar) -> None:
        if bar.marker:
            if bar.rewrite:                                 # the phrase is written again: its bar lines go
                for k, bl in self.bars.items():
                    if k[0] == self.phrase and bl["die"] is None:
                        bl["die"] = self.clock
                        bl["recs"] = None
                        self.bars_dirty = True
                self.phrase += 0.001                         # (a sub-phrase: the new bar lines are new records)
            else:
                self.phrase = math.floor(self.phrase) + 1
            return
        self.metre = (bar.beats, bar.unit)
        s = self.systems.get(self.gen)
        if s is not None and s.metre != self.metre:
            s.hdr_from = (s.fifths, s.metre)
            s.metre = self.metre
            s.hdr_t = self.clock
            s.dirty = True
        ub = self.map.at(bar.t_start)
        sb, xb = self._ux(ub)
        if sb is not None and sb.metre is None:
            sb.metre = self.metre
            sb.hdr_from = (sb.fifths, None)
            sb.hdr_t = self.clock
            sb.dirty = True
        if sb is not None:
            self._barline(sb, xb, bar.pos)
        units = bar.beats * bar.unit
        span = bar.t_end - bar.t_start
        touched = []
        for si, evs in enumerate(bar.staves):
            for ev in evs:
                if not ev.pitches:
                    continue
                tq = bar.t_start + span * ev.start / units
                key = (self.phrase, bar.pos, si, ev.start)
                for j, (p, i) in enumerate(zip(ev.pitches, ev.ids)):
                    tie_in = ev.tie_in[j] if j < len(ev.tie_in) else False
                    tie_out = ev.tie_out[j] if j < len(ev.tie_out) else False
                    n = self.by_bid.get(i)
                    if n is None:
                        n = self._find(p, tq, bar)
                        if n is None:
                            continue
                        n.bid = i
                        self.by_bid[i] = n
                    if not tie_in:
                        if n.q is not None and n.q["done"] and n.q["key"] == key:
                            continue
                        n.q = {"key": key, "tq": tq, "dur": 0, "staff": si, "pending": True,
                               "agree": n.where == (bar.pos, ev.start), "done": False}
                        ch = self.chords.setdefault(key, {"notes": [], "t": self.clock})
                        if n not in ch["notes"]:
                            ch["notes"].append(n)
                    q = n.q
                    if q is None or q["done"]:
                        continue
                    q["dur"] += ev.dur
                    q["pending"] = bool(tie_out)
                    touched.append(q["key"])
        for key in dict.fromkeys(touched):
            ch = self.chords.get(key)
            if ch is not None and all(not m.q["pending"] for m in ch["notes"] if m.q is not None):
                self.settle_q.append((key, ch["notes"]))
                del self.chords[key]
        if len(self.chords) > 256:                          # ties that never closed
            for key in list(self.chords)[:64]:
                self.settle_q.append((key, self.chords.pop(key)["notes"]))

    def _find(self, p: int, tq: float, bar):
        best, bd = None, 0.35
        for n in reversed(self.by_pitch.get(p, ())):
            if n.conf:
                continue
            dd = abs(n.t_on - tq)
            if dd < bd and bar.t_start - 0.3 <= n.t_on <= bar.t_end:
                best, bd = n, dd
            if n.t_on < bar.t_start - 1.0:
                break
        return best

    def _settle(self, key, notes: list) -> None:
        """A chord's value is known: heads align (and hop to the grid if the quantiser agreed twice), seconds step
        to the stem's other side, the stem grows, then flags and dots."""
        notes = [n for n in notes if n.q is not None and n.gen > self.gen - self.nsys]
        if not notes:
            return
        si = notes[0].q["staff"]
        for n in notes:
            if n.staff != si:
                self._restaff(n, si)
        dur = max(n.q["dur"] for n in notes)
        head, dots, flags = value_of(dur)
        sp = self.sp
        lead = min(notes, key=lambda n: n.x_exact)
        x = lead.x_exact
        tq = notes[0].q["tq"]
        if all(n.q["agree"] for n in notes):
            s, xq = self._ux(self.map.at(tq))
            if s is not None and s.gen == lead.gen and abs(xq - x) <= HOP_MAX_SP * sp:
                x = xq
                self.stats["snaps"] += 1
        mid = 34 if si == 0 else 22
        ds = [n.d for n in notes]
        up = (max(ds) - mid) <= (mid - min(ds))
        # seconds: from the stem's root, a head a step from an unshifted one goes to the other side
        order = sorted(notes, key=lambda n: n.d, reverse=not up)
        prev_d, prev_shift = None, False
        hw = self.head_w * sp
        for n in order:
            sh = False
            if prev_d is not None and abs(n.d - prev_d) == 1 and not prev_shift:
                sh = True
            n.x_from = self._x_now(n)
            n.shift = (hw if up else -hw) if sh else 0.0
            n.x = x
            n.t_x = PENDING
            prev_d, prev_shift = n.d, sh
        stem = self._stem(notes, si, lead.slot, head, up)
        # the stem's lead: the unshifted head at its root
        root = order[0]
        for n in notes:
            n.head_from = n.head
            n.head = head
            n.dots = dots
            n.flags = flags if n is root else 0
            n.t_val = PENDING
            n.lead = root
            n.stem = stem if n is root else None
            n.where = notes if n is root else None          # (the root keeps its chord, to re-stem on a respell)
            n.conf = True
            n.q["done"] = True
            self._touch(n)
        if stem is not None and root.shift != 0.0:
            root.shift = 0.0
        self.stats["settled"] += len(notes)
        self.last_settle = (x, self._step_y(lead.slot, si, notes[0].d), lead.slot, self.clock)

    def _stem(self, notes: list, si: int, slot: int, head: int, up: bool):
        """(up, root y, tip y) for a chord: 3.5 spaces past its far head, at least to the middle line."""
        if head == GL_WHOLE:
            return None
        sp = self.sp
        y_lo = self._step_y(slot, si, min(n.d for n in notes))
        y_hi = self._step_y(slot, si, max(n.d for n in notes))
        ymid = self._step_y(slot, si, 34 if si == 0 else 22)
        if up:
            return (True, y_lo, min(y_hi - STEM_SP * sp, ymid))
        return (False, y_hi, max(y_lo + STEM_SP * sp, ymid))

    def _barline(self, s: _Sys, x: float, pos: int) -> None:
        key = (self.phrase, pos)
        bl = self.bars.get(key)
        if bl is not None and bl["die"] is None:
            return
        sp = self.sp
        xb = x - 0.55 * sp
        if xb < self.x0 - 0.5 * sp:
            return
        for k, o in self.bars.items():                      # a bar line within a head of another (a parked
            if o["gen"] == s.gen and o["die"] is None and abs(o["x"] - xb) < 1.5 * sp:   # silence) is one
                return
        self.bars[key] = {"gen": s.gen, "slot": s.slot, "x": xb, "t": self.clock, "die": None, "recs": None}
        self.bars_dirty = True
        self.stats["barlines"] += 1

    def _bar_recs(self, bl: dict):
        sp = self.sp
        j = bl["slot"]
        T, B = self.T[j], self.B[j]
        out: list = []
        A = (0.85, -0.85, bl["die"], 0.4) if bl["die"] is not None else (0.85, 0.0, 0.0, 0.0)
        self._vbox(out, _S(bl["x"]), T, (0.0, B + 4 * sp - T, bl["t"], BAR_S), BAR_HW * sp, A, j, bl["gen"])
        return out

    # -- per frame --------------------------------------------------------------------------------------------------
    def _touch(self, n: _Note, urgent: bool = False) -> None:
        """The note changed: its records are rebuilt (urgent: this frame; else in turn, within the frame budget)."""
        if n.recs is None:
            return                                          # not built yet: it will be, this frame
        if urgent:
            self.urgent.append(n)
        else:
            self.stale[n] = None

    def _build(self, n: _Note) -> None:
        if n.gen < self.gen - self.nsys:
            return
        r = self._note_recs(n)
        n.recs = np.array(r, np.float32).reshape(-1, STRIDE) if r else np.zeros((0, STRIDE), np.float32)
        self.gen_dirty.add(n.gen)
        if n.expire:
            self.expiring.append(n)
        self.dbg_built += 1

    def _collect(self) -> np.ndarray:
        """The frame's records: cached per system, rebuilt only for what changed.  New notes and releases are
        built at once; other changes (settles, re-spellings, a rewrite's burst) in order within BUILD_BUDGET_S."""
        now = self.clock
        lo_gen = self.gen - self.nsys
        self.dbg_built = 0
        fresh, self.fresh = self.fresh, []
        for n in fresh:
            self._build(n)
        urgent, self.urgent = self.urgent, []
        for n in urgent:
            self.stale.pop(n, None)
            self._build(n)
        if self.expiring:                                   # finished fades: drop their spent records
            due = [n for n in self.expiring if n.expire and now > n.expire]
            if due:
                self.expiring = [n for n in self.expiring if not (n.expire and now > n.expire)]
                for n in due:
                    n.expire = 0.0
                    self._touch(n)
        if self.stale:
            end = time.thread_time() + BUILD_BUDGET_S
            st = self.stale
            while st:
                n, _ = st.popitem(last=False)
                self._build(n)
                if time.thread_time() > end:
                    break
        parts = []
        for g, s in self.systems.items():
            if g <= lo_gen:                                 # (the old line in this slot: its header is gone)
                continue
            if s.dirty or s.recs is None or (s.expire and now > s.expire):
                s.recs = np.array(self._hdr_recs(s, now), np.float32).reshape(-1, STRIDE)
            parts.append(s.recs)
        for g in sorted(self.gen_notes):
            if g < lo_gen:
                continue
            if g in self.gen_dirty or g not in self.gen_blk:
                rs = [n.recs for n in self.gen_notes[g] if n.recs is not None and len(n.recs)]
                self.gen_blk[g] = np.concatenate(rs) if rs else np.zeros((0, STRIDE), np.float32)
            parts.append(self.gen_blk[g])
        self.gen_dirty.clear()
        if self.bars_dirty or any(bl["die"] is not None and now > bl["die"] + 0.45 for bl in self.dying):
            for k in [k for k, bl in self.bars.items()
                      if bl["gen"] < lo_gen or (bl["die"] is not None and now > bl["die"] + 0.45)]:
                del self.bars[k]
            self.dying = [bl for bl in self.bars.values() if bl["die"] is not None]
            rs = []
            for bl in self.bars.values():
                if bl["recs"] is None:
                    bl["recs"] = self._bar_recs(bl)
                rs.extend(bl["recs"])
            self.bar_blk = np.array(rs, np.float32).reshape(-1, STRIDE)
            self.bars_dirty = False
        if self.bar_blk is not None:
            parts.append(self.bar_blk)
        self.dbg = {"built": self.dbg_built, "stale": len(self.stale), "barq": len(self.bar_q),
                    "settleq": len(self.settle_q)}
        return parts

    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = min(max(dt, 0.0), 0.1)
        self.clock += dt
        spd = max(float(p["speed"]), 0.0)
        self.drift += float(p["drift_speed"]) * spd * dt
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        self.kick += (pulse - self.kick) * (1.0 - math.exp(-dt / 0.15) if dt > 0 else 1.0)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        self._listen(dt)
        now_s = (self.t_sound - self.t_base) if self.t_base is not None else 0.0
        cur = self.systems.get(self.gen)
        if cur is not None and self.map is not None:
            tx = self.x0 + (min(self.map.at(self.t_sound), cur.u1) - cur.u0) * cur.pxs
            if self.cursor is None or tx < self.cursor - 4 * self.sp:
                self.cursor = tx
            else:
                self.cursor += (tx - self.cursor) * (1.0 - math.exp(-dt / 0.06) if dt > 0 else 1.0)
        R = self._collect() if cur is not None else []
        nr = min(MAX_RECS, sum(len(b) for b in R))
        # params
        P = np.zeros(self.NP, np.float32)
        S = np.zeros(self.NS, np.float32)
        sp = self.sp
        P[0], P[1], P[2], P[3] = sp, now_s, (0.25 + 0.35 * e) * sp, float(p["note_glow"])
        P[4] = ERASE_W * self.width
        P[5] = self.th
        night = self.look == "night"
        P[6] = 1.0 if night else 0.0
        P[7:10] = (0.90, 0.86, 0.78) if night else (0.10, 0.085, 0.075)
        c = palette_coeffs(p["palette"]) if p.get("palette") else palette_coeffs("ember")
        acc = _pal3(c, self.drift % 10.0 + 0.1)
        lum = float(0.2126 * acc[0] + 0.7152 * acc[1] + 0.0722 * acc[2])
        if lum < 0.5:                                       # a dark palette stop still reads as a glow
            acc = np.clip(acc * (0.5 / max(lum, 0.05)), 0.0, 1.0)
        if not night:                                       # paper: a warm, ink-like accent
            acc = 0.5 * acc + 0.5 * np.array((0.80, 0.33, 0.14))
        P[10:13] = acc
        P[13], P[14] = self.x_l, self.x_r
        P[15] = cur.slot if cur is not None else -1
        P[16] = self.cursor if self.cursor is not None else self.x0
        P[17] = 0.9 if (self.map is not None and self.map.rate > 0.5) else 0.45
        P[18] = self.kick
        P[19] = float(p["intensity"])
        P[20] = self.clock
        for j in range(self.nsys):
            S[8 * j + 5], S[8 * j + 6], S[8 * j + 7] = self.T[j], self.B[j], 1.0
            S[8 * j + 0] = -1e9
            S[8 * j + 3] = 1.0
            S[8 * j + 2] = OLD_DIM
            S[8 * j + 1] = -1e9
            S[8 * j + 4] = self.x_r
        for g, s in self.systems.items():
            if g <= self.gen - self.nsys:
                continue
            j = 8 * s.slot
            S[j + 0] = g
            age = self.gen - g
            S[j + 3] = (1.0, 1.0, 0.9, 0.78)[min(age, 3)]
            if g == self.gen:
                S[j + 1] = (self.cursor or self.x0) + 1.5 * sp
                S[j + 4] = self.cursor or self.x0
                S[j + 2] = OLD_DIM + (1.0 - OLD_DIM) * (1.0 - _ease((self.clock - s.born) / 0.8))
        self._upload(P, S, R)
        cp = self.ctx.cp
        if nr:
            self.k_splat((nr, SPLIT, 1), (128, 1, 1),
                         (self.R_dev, np.int32(nr), self.P_dev, self.S_dev, self.A, np.int32(self.AW), self.GT_dev,
                          np.int32(self.atlas_ok), self.ink, self.glow, np.int32(self.w), np.int32(self.h)))
        dst = out if self.direct else self.buf
        launch_2d(self.k_comp, self.w, self.h, (dst, self.page, self.ink, self.glow, self.P_dev, self.S_dev,
                                                np.int32(self.nsys), np.int32(self.w), np.int32(self.h)))
        if not self.direct:
            present(self.ctx, self.buf, out)
        self.n_recs = nr

    def _upload(self, P, S, R) -> None:
        cp = self.ctx.cp
        k = self.up_k = (self.up_k + 1) % NBUF
        ev = self.up_ev[k]
        if ev is not None and not ev.done:
            ev.synchronize()
        h = self.up_host[k]
        h[:self.NP] = P
        h[self.NP:self.NP + self.NS] = S
        o = self.NP + self.NS                               # the record blocks straight into pinned memory
        end = o + MAX_RECS * STRIDE
        for blk in R:
            if not blk.size:
                continue
            c = min(blk.size, end - o)
            if c <= 0:
                self.stats["recs_capped"] += 1
                break
            h[o:o + c] = blk.ravel()[:c]
            o += c
        n = (o - self.NP - self.NS) // STRIDE
        st = cp.cuda.get_current_stream()
        m = self.NP + self.NS + n * STRIDE
        self.up_dev[:m].set(h[:m], stream=st)
        if ev is None:
            ev = self.up_ev[k] = cp.cuda.Event(block=True, disable_timing=True)
        ev.record(st)

    def close(self) -> None:
        room, self.room = getattr(self, "room", None), None
        if room is not None:
            try:
                room.leave(self)
            except Exception:
                pass
        self.buf = self.page = self.ink = self.glow = self.up_dev = None
        self.up_host = self._pin = self.up_ev = None
