"""Echo arcs: whenever a melodic phrase comes back, a translucent arch springs from its first appearance to now.

Source: Original (inspired by Martin Wattenberg, "The Shape of Song", https://www.bewitched.com/song.html and
        https://eliterature.org/turbulence/Works/song/method/method.html; no code taken)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
----------------------
A hair-thin timeline runs along the bottom of the frame: the present sits at the right, the past scrolls away to
the left, and every note struck leaves a tiny tick on it (warm for the melody, cool for the rest).  Whenever the
music repeats itself, a wide translucent arch springs up above the timeline joining the earlier passage to its
repeat: its two feet ARE the two passages (a long phrase makes thick feet), and the further apart they are, the
wider and taller the arch.  An arch is born at its present-day foot and sweeps over to the past one in half a
second; while the repeat goes on, its right foot keeps widening, then it settles and slowly fades (``memory_s``).
Overlapping arches add up into glowing lattices: an ostinato makes a regular fringe of small arches, a returning
theme throws one big arch across the screen, a sequence makes a staircase of arches in neighbouring colours.

Colour says HOW the phrase came back.  An exact repeat (same notes) is a pale gold-white.  A repeat moved to
another pitch (a sequence, a fugal answer, a theme in a new key) takes a hue from the interval it moved by, placed
on the circle of fifths with gold at the top: up a fifth (+P5) sits just on the green-yellow side of gold, up a
fourth (+P4) just on the orange side, a whole tone two steps out, and a tritone (+TT) opposite, in blue.  With
``labels`` the interval is written above wide arches ('+P5', '+m3', ...; '=' for exact repeats only near 1, and a
label never overprints a stronger one).

The theory (Wattenberg's arc diagram, made transposition- and tempo-invariant)
---------------------------------------------------------------------------------
Wattenberg drew a translucent arc between every pair of identical passages in a piece (maximal pairs only).
Identical PITCHES miss most of what a listener hears as a repeat, so here the melody is turned into a string of
tokens that forget the key and the tempo:
  1. Onsets within 40 ms are one event.  Two melody streams are read: the TOP voice (the highest note of an event,
     kept only if it is near the recent top line, a "skyline" that falls 24 semitones/s, so left-hand notes slipped
     between melody notes are left out) and the BASS (the lowest note of a chord, or a note the top line refused).
  2. Token n = (dp, rr): dp = p_n - p_(n-1) clamped to +-24 semitones (the interval: transposition-invariant) and
     rr = floor(log2(IOI_n / IOI_(n-1)) / 2 + 1/2), clamped (the rhythm ratio: tempo-invariant).  The research
     spec had half-octave steps (round(2 log2 r) / 2), but on the real performances in config/live/test-piano
     rubato broke most matches (a Chopin nocturne made 15 arcs in 3 minutes, a Bill Evans ballad 1); with only a
     doubling or a halving of the note length counting (a long note after short ones still differs) the nocturne
     makes 44 and Bach's F minor fugue 23, its answers a fifth and a fourth away.  A gap over 3 s starts a new
     phrase (a break token).  Encoded (dp + 24) x 9 + (rr + 4).
  3. A hash of the last 5 tokens indexes up to 4 earlier places.  On each token a live arc at lag l extends if the
     token l back matches (a repeat at most one lag long, so an ostinato makes a fringe of arcs, each one period,
     rather than one smeared dome); otherwise it ends.  When no live arc explains the present, the most recent
     earlier place of the same 5-gram (5 back or more, with at least 2 different tokens, so a repeated note or a
     trill is no match) starts a new arc: passages A = notes j-5..j and B = n-5..n, lag l = n - j, transposition
     T = (p_n - p_j) mod 12.  At most 64 arcs; the faintest goes first.
  4. Hue position = (7 T) mod 12 steps of 30 deg (the circle of fifths: 7 semitones = one step), gold at 0.

Geometry: x(t) maps a note's time to the timeline (the present 3% in from the right edge).  The window opens at
8 s (or 0.08 x ``window_s``) when a piece starts and widens with the music (1.15 x the time since its first note) up to
``window_s``, like Wattenberg's diagram, which always fits the whole piece: the first repeats of a piece are big,
not specks in the corner.  On the two TVs side by side (``span`` pair, ``ctx.screen`` south-screen / north-screen,
south on the left) the timeline runs across both: the south TV shows the older half, the north TV the newer.
Outer half-ellipse: centre (x(A0) + x(B1)) / 2, rx_o = (x(B1) - x(A0)) / 2, ry_o = min(0.42 H, arch_height x
rx_o); inner: rx_i = (x(B0) - x(A1)) / 2, ry_i = ry_o x rx_i / rx_o.  The band between them (soft 1.5 px edges,
brighter 2.6 px rims) is drawn above the baseline at 0.96 H.

Rendering: the host bins the arcs into 64 x-columns (at most 12 each: strongest first, an arc is shown only if it
fits in every column it spans, easing in and out over 0.25 s, so a full column never cuts an arch with a seam) and
uploads them with the
onset ticks (<= 256); one kernel at full size copies the rows above the arches and, below, loops over its column's
arcs: band x alpha x colour summed, then screen-blended over the picture in sRGB (soft-clipped), over a soft dark
under-glow (up to 40%, only where the picture is bright) that keeps pale arches legible over white art.  Labels come from a 12-entry glyph atlas
baked with PIL at setup.

amount: opacity (0 copies the input exactly; the matcher keeps listening).  pulse: a brightness kick on live,
growing arcs (an amplitude, never a position).  The feet ease to new notes (tau 0.12 s), so nothing jumps.
No notes: no new arches; the old ones fade (``memory_s``), the timeline fades out, and the effect becomes a pure
copy.  dt = 0: nothing moves; dt is clamped to 1 s.

Cost (P4000, 4K, 29 Oct): fxtest --bench 0.44 ms (a fresh instance, a few arcs); with the ring full (64 arcs after
80 s of a Schubert impromptu) the kernel is ~1.8 ms (the early-outs skip pixels well outside an arch or deep inside
its hole), plus ~0.9 ms of host numpy per frame (geometry, the column fit, the ticks) and ~0.1 ms per frame for the
matcher.  No arcs: a copy.
"""
from __future__ import annotations

import collections
import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import enum, num, standard_effect_params

log = logging.getLogger(__name__)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LABELS = ("=", "+m2", "+M2", "+m3", "+M3", "+P4", "+TT", "+P5", "+m6", "+M6", "+m7", "+M7")

RING = 4096                  # tokens kept per stream
K = 5                        # n-gram length (tokens)
RR_Q = 0.5                   # rhythm-ratio steps per octave of time (0.5: only a doubling or halving counts)
CANDS = 4                    # earlier places kept per n-gram
MAX_KEYS = 8192              # n-gram index size (oldest evicted)
GROUP_S = 0.04               # onsets this close are one event
PHRASE_GAP_S = 3.0           # a longer gap starts a new phrase (break token; live arcs end)
SKY_FALL = 24.0              # skyline fall (semitones / s)
SKY_TOL = 9                  # a top note within this of the skyline is melody
MAXA = 64                    # arcs kept
NCOL = 64                    # x-columns
PERCOL = 12                  # arcs per column
ARW = 24                     # floats per packed arc
MAXT = 256                   # ticks uploaded
TRING = 512                  # onset ring (ticks)
BLOOM_S = 0.5                # reveal sweep
FOOT_TAU = 0.12              # foot easing
GLOW_TAU = 0.6               # live-glow easing
SHOW_TAU = 0.25              # an arc fitting / not fitting its columns, eased
LINE_TAU = 1.5               # timeline fade
FILL_A = 0.18                # band alpha
RIM_A = 0.42                 # rim alpha
RY_MAX = 0.42                # arch height cap (x H)
BASE_Y = 0.96                # baseline (x H)
MARGIN = 0.03                # the present, in from the right edge (x W)
SHADE = 0.4                  # under-glow depth (scaled by the picture's brightness)
GOLD_DEG = 46.0              # hue of an exact repeat's neighbours' centre (gold)

_SRC = HELPERS + f"""
#define NCOL {NCOL}
#define PERCOL {PERCOL}
#define ARW {ARW}
#define SHADE {SHADE}f
""" + r"""
// One pass at full size.  Rows above ytop are copied; below, each pixel loops over its column's arcs (<= PERCOL),
// then the onset ticks (a bounded binary search) and the timeline, and screen-blends the light over the picture.
// Arc row: 0 cx | 1 rx_o | 2 ry_o | 3 rx_i | 4 ry_i | 5..7 rgb | 8 alpha | 9 reveal | 10 rim gain | 11 shade
//          12 label alpha | 13 label id | 14 label x0 | 15 label y0       (pixels; y0 = baseline, up = +Y)
//          16 1/rx_o | 17 1/ry_o | 18 min(rx_o, ry_o) | 19 1/rx_i | 20 1/ry_i | 21 min(rx_i, ry_i) | 22, 23 -
extern "C" __global__
void ea_draw(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H, const int ytop,
             const float y0, const float* __restrict__ arcs, const short* __restrict__ table,
             const float4* __restrict__ ticks, const int nt, const float tick_h,
             const float line_a, const float line_x0, const float now_x, const float now_a,
             const unsigned char* __restrict__ atlas, const int lw, const int lh,
             const float gain, const float shade_k, const float s)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    if (y < ytop) { out[i] = src[i]; return; }
    const float px = x + 0.5f, py = y + 0.5f;
    const float Y = y0 - py;                                   // height above the baseline (pixels)
    const float edge = 0.75f * s, rw = 1.3f * s, sw = 0.012f * H;
    float ar = 0.f, ag = 0.f, ab = 0.f, sh = 0.f;
    const int col = min(x * NCOL / W, NCOL - 1);
    const short* tb = table + col * (PERCOL + 1);
    const int cnt = min((int)tb[0], PERCOL);
    if (Y > -sw) {
        for (int k = 0; k < cnt; ++k) {                       // bounded: <= PERCOL arcs
            const float* a = arcs + (int)tb[1 + k] * ARW;
            const float dx = px - a[0], rxo = a[1], ryo = a[2];
            const float ady = fmaxf(Y, 0.f);
            float lab = 0.f;
            if (a[12] > 0.f) {                                // the apex label (atlas, at integer pixels)
                const int gx = x - (int)a[14], gy = y - (int)a[15];
                const int id = min(max((int)a[13], 0), 11);
                if (gx >= 0 && gx < lw && gy >= 0 && gy < lh) lab = atlas[(id * lh + gy) * lw + gx] * (1.f / 255.f);
                const int hx = gx - (int)(2.f * s), hy = gy - (int)(2.f * s);   // a soft drop shadow
                if (hx >= 0 && hx < lw && hy >= 0 && hy < lh) sh += 0.8f * a[12] * atlas[(id * lh + hy) * lw + hx] * (1.f / 255.f);
                if (lab > 0.f) {
                    const float la = a[12] * lab;
                    ar += la * (0.45f * a[5] + 0.55f); ag += la * (0.45f * a[6] + 0.55f); ab += la * (0.45f * a[7] + 0.55f);
                }
            }
            if (fabsf(dx) > rxo + sw || Y > ryo + sw) continue;
            // |grad e| <= 1 / min(rx, ry), so (e - 1) x min(rx, ry) bounds the distance: cheap early-outs
            const float ux = dx * a[16], uy = ady * a[17];
            const float eo = sqrtf(ux * ux + uy * uy);
            if ((eo - 1.f) * a[18] > sw) continue;             // well outside the arch
            float din = 1e6f;                                  // signed distance to the inner edge (+ outside the hole)
            if (a[3] > 0.5f && a[4] > 0.5f) {
                const float vx = dx * a[19], vy = ady * a[20];
                const float ei = sqrtf(vx * vx + vy * vy);
                if ((1.f - ei) * a[21] > sw) continue;         // deep inside the hole
                const float gi = sqrtf(vx * vx * a[19] * a[19] + vy * vy * a[20] * a[20]) / fmaxf(ei, 1e-4f);
                din = (ei - 1.f) / fmaxf(gi, 1e-6f);
            }
            const float go = sqrtf(ux * ux * a[16] * a[16] + uy * uy * a[17] * a[17]) / fmaxf(eo, 1e-4f);
            const float dout = (eo - 1.f) / fmaxf(go, 1e-6f);  // signed distance to the outer edge (px, - inside)
            const float base = clamp01(Y / (2.f * edge) + 0.5f);   // nothing below the baseline
            float vis = 1.f, lead = 0.f;
            const float rev = a[9];
            if (rev < 0.999f) {                                // the sweep from the present foot to the past one
                const float th = atan2f(uy, ux) * 0.31830988f;   // 0 at the right foot, 1 at the left
                vis = clamp01((rev - th) * 22.f + 0.5f);
                const float q = (th - rev) * 18.f;
                lead = (1.f - rev) * __expf(-q * q);
            }
            const float cov = clamp01(0.5f - dout / (2.f * edge)) * clamp01(0.5f + din / (2.f * edge));
            const float ro = dout / rw, ri = din / rw;
            const float rim = (ro * ro < 16.f ? __expf(-ro * ro) * clamp01(0.5f + din / (2.f * rw)) : 0.f)
                            + (ri * ri < 16.f ? 0.7f * __expf(-ri * ri) * clamp01(0.5f - dout / (2.f * rw)) : 0.f);
            const float al = a[8] * base * (vis * (cov + a[10] * rim) + 1.6f * lead * cov);
            ar += al * a[5]; ag += al * a[6]; ab += al * a[7];
            const float so = smooth(sw, -sw, dout) * smooth(-sw, sw, din) * smooth(-sw, 0.f, Y);
            sh += a[11] * so * vis;
        }
    }
    if (nt > 0 && Y > -3.f * s && Y < tick_h + 3.f * s) {    // onset ticks: nearest few by binary search
        int lo = 0, hi = nt;
        for (int it = 0; it < 10 && lo < hi; ++it) {          // bounded: log2(256) + 1
            const int mid = (lo + hi) >> 1;
            if (ticks[mid].x < px) lo = mid + 1; else hi = mid;
        }
        for (int j = max(lo - 2, 0); j < min(lo + 2, nt); ++j) {
            const float4 t = ticks[j];                         // x, height (px), alpha, warm (0..1)
            const float d = (px - t.x) / (0.9f * s);
            if (d * d > 9.f) continue;
            const float v = __expf(-d * d) * clamp01(Y / (2.f * edge) + 0.5f) * clamp01((t.y - Y) / (2.f * s) + 0.5f);
            const float al = t.z * v;
            ar += al * (0.62f + 0.38f * t.w); ag += al * (0.72f + 0.16f * t.w); ab += al * (0.95f - 0.35f * t.w);
        }
    }
    if (line_a > 0.f) {                                        // the hair-thin timeline and the present's spark
        const float q = Y / (0.7f * s);
        if (q * q < 16.f) {
            const float l = line_a * 0.32f * __expf(-q * q) * smooth(line_x0, line_x0 + 0.18f * W, px)
                          * smooth(now_x + 2.f * s, now_x - 2.f * s, px);
            ar += l; ag += 0.93f * l; ab += 0.82f * l;
        }
        if (now_a > 0.f) {
            const float dx = (px - now_x) / (5.f * s), dy = Y / (5.f * s);
            const float r2 = dx * dx + dy * dy;
            if (r2 < 16.f) { const float g = now_a * __expf(-r2); ar += g; ag += 0.9f * g; ab += 0.75f * g; }
        }
    }
    const float4 c = ld(src, x, y, W, H);
    if (ar + ag + ab <= 1e-5f && sh <= 1e-5f) { out[i] = src[i]; return; }
    const float dk = 1.f - SHADE * shade_k * fminf(sh, 1.f) * smooth(0.3f, 0.85f, luma(c));   // bright art only
    const float r = c.x * dk, g = c.y * dk, b = c.z * dk;
    const float er = 1.f - __expf(-ar * gain), eg = 1.f - __expf(-ag * gain), eb = 1.f - __expf(-ab * gain);
    out[i] = pack(make_float4(r + (255.f - r) * er, g + (255.f - g) * eg, b + (255.f - b) * eb, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hsv(h_deg: float, s: float, v: float) -> tuple[float, float, float]:
    h = (h_deg % 360.0) / 60.0
    c = v * s
    xx = c * (1.0 - abs(h % 2.0 - 1.0))
    r, g, b = [(c, xx, 0), (xx, c, 0), (0, c, xx), (0, xx, c), (xx, 0, c), (c, 0, xx)][int(h) % 6]
    m = v - c
    return r + m, g + m, b + m


def interval_rgb(T: int) -> tuple[float, float, float]:
    """A transposition's colour: 0 = pale gold-white; else 30 deg per step on the circle of fifths, gold at 0."""
    T %= 12
    if T == 0:
        return 1.0, 0.9, 0.7
    pos = (7 * T) % 12                     # +P5 -> 1, +P4 -> 11, +M2 -> 2, +TT -> 6
    return _hsv(GOLD_DEG + 30.0 * pos, 0.72, 1.0)


_RGB = np.array([interval_rgb(t) for t in range(12)], np.float64)


def bake_labels(height: int) -> tuple[np.ndarray, int, int]:
    """The twelve interval labels as a uint8 atlas [12, lh, lw]; (zeros, 0, 0) without a font."""
    try:
        from PIL import Image, ImageDraw, ImageFont
        lh = max(10, int(height))
        font = ImageFont.truetype(FONT, max(8, int(round(lh * 0.78))))
        lw = max(10, int(math.ceil(max(font.getlength(t) for t in LABELS))) + 6)
        out = np.zeros((12, lh, lw), np.uint8)
        for i, t in enumerate(LABELS):
            img = Image.new("L", (lw, lh), 0)
            ImageDraw.Draw(img).text((lw / 2, lh / 2), t, fill=255, font=font, anchor="mm")
            out[i] = np.asarray(img, np.uint8)
        return out, lw, lh
    except Exception as exc:                # degrade: no labels
        log.warning("echo_arcs: cannot bake the labels (%s); drawing none", exc)
        return np.zeros((12, 1, 1), np.uint8), 0, 0


# ------------------------------------------------------------------------------------------------ the matcher
class Stream:
    """One melody stream: its tokens (a ring), the 5-gram index, and the next token's state."""

    def __init__(self, sid: int) -> None:
        self.sid = sid
        self.tok = np.full(RING, -1, np.int32)
        self.t = np.zeros(RING, np.float64)
        self.p = np.zeros(RING, np.int16)
        self.n = 0                          # tokens pushed
        self.prev_p = -1
        self.prev_t = -1e9
        self.prev_ioi = -1.0
        self.index: collections.OrderedDict = collections.OrderedDict()

    def valid(self, k: int) -> bool:
        return 0 <= k < self.n and k > self.n - RING

    def push(self, t: float, pitch: int) -> int:
        """Append a note; returns its token index."""
        ioi = t - self.prev_t
        if self.prev_p < 0 or not (ioi < PHRASE_GAP_S):
            tok = -1                        # a new phrase: no interval
            self.prev_ioi = -1.0
        else:
            dp = min(max(pitch - self.prev_p, -24), 24)
            ioi = max(ioi, 0.02)
            if self.prev_ioi > 0.0:
                rr = int(math.floor(RR_Q * math.log2(ioi / self.prev_ioi) + 0.5))
                rr = min(max(rr, -4), 4)
            else:
                rr = 0
            tok = (dp + 24) * 9 + (rr + 4)
            self.prev_ioi = ioi
        self.prev_p, self.prev_t = pitch, t
        n = self.n
        k = n % RING
        self.tok[k], self.t[k], self.p[k] = tok, t, pitch
        self.n = n + 1
        return n

    def gram(self, n: int):
        """The hash key of tokens n-4..n, or None (a break inside, too early, or fewer than 2 distinct tokens)."""
        if n < K - 1 or not self.valid(n - K + 1):
            return None
        key = 0
        first, mixed = -2, False
        for k in range(n - K + 1, n + 1):
            v = int(self.tok[k % RING])
            if v < 0:
                return None
            if first == -2:
                first = v
            elif v != first:
                mixed = True
            key = key * 441 + v
        return key if mixed else None

    def remember(self, key, n: int) -> None:
        d = self.index.get(key)
        if d is None:
            d = collections.deque(maxlen=CANDS)
            self.index[key] = d
            if len(self.index) > MAX_KEYS:
                self.index.popitem(last=False)
        else:
            self.index.move_to_end(key)
        d.append(n)


class Matcher:
    """Groups onsets into events, feeds the top and bass streams, and keeps the arcs (host arrays)."""

    def __init__(self) -> None:
        self.streams = (Stream(0), Stream(1))
        z = lambda dt=np.float64: np.zeros(MAXA, dt)   # noqa: E731
        self.act = z(bool)
        self.live = z(bool)
        self.sid = z(np.int64)
        self.lag = z(np.int64)
        self.b0 = z(np.int64)
        self.e = z(np.int64)
        self.T = z(np.int64)
        self.tc = z()                       # creation (note time)
        self.tA0, self.tA1, self.tB0, self.tB1 = z(), z(), z(), z()
        self.dA1, self.dB1 = z(), z()       # the displayed (eased) inner feet
        self.fade = z()                     # strength while fading (1 while live)
        self.glow = z()                     # live glow, eased
        self.show = z()                     # shown (fits in its columns), eased
        self.group = []                     # [(t, pitch, vel)] open event
        self.sky, self.sky_t = -1.0, -1e9
        self.floor, self.floor_t = 200.0, -1e9
        self.ticks = np.zeros((TRING, 3))   # t, vel, warm
        self.nticks = 0
        self.t_first = None                 # the piece's first event
        self.t_last = -1e9                  # the latest event
        self.made = 0                       # arcs made (stats)

    # ---- events
    def onset(self, t: float, pitch: int, vel: float) -> None:
        if self.group and t - self.group[0][0] > GROUP_S:
            self.close()
        self.group.append((t, pitch, vel))

    def flush(self, now: float) -> None:
        if self.group and now - self.group[0][0] > GROUP_S:
            self.close()

    def close(self) -> None:
        g, self.group = self.group, []
        if not g:
            return
        t = g[0][0]
        ps = [q[1] for q in g]
        top, low = max(ps), min(ps)
        if self.t_first is None or t - self.t_last > self.window_hint:
            self.t_first = t
        self.t_last = t
        decayed = self.sky - SKY_FALL * max(t - self.sky_t, 0.0)
        top_ok = top >= decayed - SKY_TOL
        self.sky, self.sky_t = max(float(top), decayed), t
        risen = self.floor + SKY_FALL * max(t - self.floor_t, 0.0)
        low_ok = low <= risen + SKY_TOL and (len(set(ps)) >= 2 or not top_ok)
        self.floor, self.floor_t = min(float(low), risen), t
        for q in g:                         # ticks: one per onset
            k = self.nticks % TRING
            warm = 1.0 if (q[1] == top and top_ok) else 0.0
            self.ticks[k] = (q[0], q[2], warm)
            self.nticks += 1
        if top_ok:
            self._token(self.streams[0], t, top)
        if low_ok:
            self._token(self.streams[1], t, low)

    # ---- arcs
    def _free(self) -> int:
        free = np.flatnonzero(~self.act)
        if free.size:
            return int(free[0])
        done = np.flatnonzero(~self.live)
        if done.size == 0:
            return -1
        return int(done[np.argmin(self.fade[done])])

    def _new(self, s: Stream, b0: int, n: int, lag: int, now: float) -> None:
        i = self._free()
        if i < 0:
            return
        R = RING
        self.act[i], self.live[i] = True, True
        self.sid[i], self.lag[i], self.b0[i], self.e[i] = s.sid, lag, b0, n
        self.T[i] = (int(s.p[n % R]) - int(s.p[(n - lag) % R])) % 12
        self.tB0[i] = s.t[(b0 - 1) % R]
        self.tA0[i] = s.t[(b0 - 1 - lag) % R]
        self.tB1[i] = self.dB1[i] = s.t[n % R]
        self.tA1[i] = self.dA1[i] = s.t[(n - lag) % R]
        self.tc[i] = s.t[n % R]
        self.fade[i] = 1.0
        self.glow[i] = 1.0
        self.show[i] = 0.0
        self.made += 1

    def _finish(self, i: int) -> None:
        self.live[i] = False
        same = np.flatnonzero(self.act & (self.sid == self.sid[i]) & (self.lag == self.lag[i]))
        for j in same:                      # contained in another pair at the same lag: drop (maximal pairs only)
            if j != i and self.b0[j] <= self.b0[i] and self.e[j] >= self.e[i]:
                self.act[i] = False
                return

    def _token(self, s: Stream, t: float, pitch: int) -> None:
        n = s.push(t, pitch)
        tok = int(s.tok[n % RING])
        covered = False
        for i in np.flatnonzero(self.act & self.live & (self.sid == s.sid)):
            l = int(self.lag[i])
            if tok >= 0 and s.valid(n - l) and s.valid(self.b0[i] - 1 - l) and int(s.tok[(n - l) % RING]) == tok:
                if n - int(self.b0[i]) + 1 <= l:          # extend (a repeat at most one lag long)
                    self.e[i] = n
                    self.tB1[i] = t
                    self.tA1[i] = s.t[(n - l) % RING]
                else:                                     # one period done: finish it, chain the next
                    self._finish(int(i))
                    if s.valid(n - 1 - l):
                        self._new(s, n, n, l, t)
                covered = True
            else:
                self._finish(int(i))
        key = s.gram(n)
        if key is None:
            return
        if not covered:
            cands = s.index.get(key)
            if cands:
                for j in reversed(cands):
                    l = n - j
                    if l >= K and s.valid(n - K - l):
                        self._new(s, n - K + 1, n, l, t)
                        break
        s.remember(key, n)

    def step(self, now: float, dt: float, memory_s: float) -> None:
        """Age the arcs: live ones end after a phrase gap; finished ones fade; the feet and glow ease."""
        a = self.act
        if not a.any():
            return
        stale = a & self.live & (now - self.tB1 > PHRASE_GAP_S)
        for i in np.flatnonzero(stale):
            self._finish(int(i))
        if dt > 0.0:
            done = a & ~self.live
            self.fade[done] *= math.exp(-dt / max(memory_s, 1.0))
            kf = 1.0 - math.exp(-dt / FOOT_TAU)
            self.dB1[a] += (self.tB1[a] - self.dB1[a]) * kf
            self.dA1[a] += (self.tA1[a] - self.dA1[a]) * kf
            kg = 1.0 - math.exp(-dt / GLOW_TAU)
            self.glow[a] += (self.live[a].astype(np.float64) - self.glow[a]) * kg
        a &= ~((~self.live) & (self.fade < 0.015))

    window_hint = 90.0


# ------------------------------------------------------------------------------------------------ the effect
class EchoArcs(Effect):
    name = "echo_arcs"
    stage = "overlay"
    label = "Echo arcs"
    description = ("Shape of Song played live over any scene: whenever a melodic phrase comes back, a translucent "
                   "arch springs from its first appearance to now, coloured by the interval it returned at, so "
                   "motifs and sequences build cathedrals of light.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "window_s": num(30.0, 300.0, 90.0, "Time shown", unit="s", step=1.0, tier="regional", basic=True),
        "memory_s": num(20.0, 300.0, 90.0, "Memory (fade time)", unit="s", step=1.0, tier="regional"),
        "arch_height": num(0.4, 1.2, 0.8, "Arch height", tier="regional"),
        "labels": num(0.0, 1.0, 0.5, "Interval labels", tier="regional"),
        "voice": num(0.0, 1.0, 0.0, "Voices (top · top and bass)", tier="regional"),
        "span": enum(("pair", "single"), "pair", "Timeline across both TVs", tier="structural"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._k = mod.get_function("ea_draw")
        W, H = ctx.width, ctx.height
        self.s = max(H / 2160.0, 0.35)                  # pixel scale (sizes are tuned at 4K)
        atlas, self.lw, self.lh = bake_labels(round(0.034 * H))
        self.atlas = cp.asarray(atlas.reshape(-1))
        self.arcs = cp.zeros(MAXA * ARW, cp.float32)
        self.table = cp.zeros(NCOL * (PERCOL + 1), cp.int16)
        self.ticks = cp.zeros(MAXT * 4, cp.float32)
        self.arcs_h = np.zeros((MAXA, ARW), np.float32)
        self.table_h = np.zeros((NCOL, PERCOL + 1), np.int16)
        self.ticks_h = np.zeros((MAXT, 4), np.float32)
        self.cols = np.arange(NCOL)
        self.show_t = np.zeros(MAXA)
        self.tick_ar = np.arange(TRING)
        self._dt = 0.0
        screen = getattr(ctx, "screen", None)
        pair = str(params.get("span", "pair")) == "pair" and screen in ("south-screen", "north-screen")
        self.span_w = (2.0 if pair else 1.0) * W        # the timeline's width (both TVs side by side)
        self.x_off = W if (pair and screen == "north-screen") else 0.0
        self.m = Matcher()
        self.now = 0.0
        self.line = 0.0
        self.win = None
        self._seen = None

    # ---------------------------------------------------------------------------------------------------- host
    def _ingest(self) -> bool:
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        if fr is None:
            return False
        ons = getattr(fr, "onsets", None)
        if ons is not None and ons is not self._seen:
            self._seen = ons
            evs = []
            for ev in list(ons)[:128]:
                try:
                    pitch, vel, age = int(ev[0]), float(ev[1]), float(ev[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if 0 <= pitch < 128 and math.isfinite(vel) and math.isfinite(age):
                    evs.append((self.now - min(max(age, 0.0), 10.0), pitch, min(max(vel, 0.0), 1.0)))
            evs.sort()
            for t, pitch, vel in evs:
                self.m.onset(t, pitch, vel)
        return bool(getattr(snd, "present", False))

    def _xmap(self, t, win: float):
        """Time -> x on this screen (pixels)."""
        P = self.span_w - MARGIN * self.ctx.width
        return P * (1.0 - (self.now - np.asarray(t, np.float64)) / win) - self.x_off

    def _pack(self, p: dict) -> tuple[int, int]:
        """Geometry, colours, binning and ticks for this frame.  Returns (arcs drawn, ticks)."""
        m = self.m
        W, H = self.ctx.width, self.ctx.height
        win = self.win
        y0 = BASE_Y * H
        idx = np.flatnonzero(m.act)
        voice = min(max(float(p["voice"]), 0.0), 1.0)
        n = 0
        self.table_h[:, 0] = 0
        if idx.size:
            wt = np.where(m.sid[idx] == 0, 1.0, voice)
            xA0, xB1 = self._xmap(m.tA0[idx], win), self._xmap(np.maximum(m.dB1[idx], m.tB0[idx]), win)
            xA1, xB0 = self._xmap(np.minimum(m.dA1[idx], m.tB0[idx]), win), self._xmap(m.tB0[idx], win)
            cx = 0.5 * (xA0 + xB1)
            rxo = np.maximum(0.5 * (xB1 - xA0), 1.0)
            ryo = np.maximum(np.minimum(RY_MAX * H, float(p["arch_height"]) * rxo), 1.0)
            rxi = np.maximum(0.5 * (xB0 - xA1), 0.0)
            ryi = ryo * rxi / rxo
            reveal = np.clip((self.now - m.tc[idx]) / BLOOM_S, 0.0, 1.0)
            reveal = reveal * reveal * (3.0 - 2.0 * reveal)
            pulse = min(max(float(p["pulse"]), 0.0), 1.0)
            glow = m.glow[idx]
            alpha = FILL_A * m.fade[idx] * wt * (0.9 + 0.1 * glow) * (1.0 + pulse * glow)
            sw = 0.012 * H
            left, right = cx - rxo - sw, cx + rxo + sw
            vis = (alpha > 2e-4) & (right >= 0.0) & (left < W)
            # Columns hold at most PERCOL arcs.  Rather than cut an arc off where a column is full (a hard seam),
            # an arc either fits in every column it spans or is not shown at all; shown-ness eases (no pops).
            cand = np.flatnonzero(vis)
            cand = cand[np.argsort(-alpha[cand], kind="stable")]
            cA = np.clip(np.floor(left[cand] * NCOL / W), 0, NCOL - 1).astype(np.int64)
            cB = np.clip(np.floor(right[cand] * NCOL / W), 0, NCOL - 1).astype(np.int64) + 1
            counts = [0] * NCOL                              # plain lists: ~64 short steps, cheaper than numpy
            ca, cb = cA.tolist(), cB.tolist()
            take = np.zeros(cand.size, bool)
            for j in range(cand.size):                       # the strongest first
                a0, b0 = ca[j], cb[j]
                if max(counts[a0:b0]) < PERCOL:
                    for c in range(a0, b0):
                        counts[c] += 1
                    take[j] = True
            sh_t = self.show_t
            sh_t[:] = 0.0
            sh_t[idx[cand[take]]] = 1.0
            kshow = 1.0 - math.exp(-self._dt / SHOW_TAU) if self._dt > 0.0 else 0.0
            m.show += (sh_t - m.show) * kshow
            fading = m.show[idx[cand]] > 0.01
            for j in np.flatnonzero(~take & fading).tolist():   # fading out: drawn while there is room
                a0, b0 = ca[j], cb[j]
                if max(counts[a0:b0]) < PERCOL:
                    for c in range(a0, b0):
                        counts[c] += 1
                    take[j] = True
            alpha = alpha * m.show[idx]
            vis = np.zeros(idx.size, bool)
            vis[cand[take]] = True
            vis &= alpha > 2e-4
            if vis.any():
                order = np.flatnonzero(vis)
                order = order[np.argsort(-alpha[order], kind="stable")][:MAXA]
                n = order.size
                h = self.arcs_h
                h[:n, 0] = cx[order]
                h[:n, 1] = rxo[order]
                h[:n, 2] = ryo[order]
                h[:n, 3] = rxi[order]
                h[:n, 4] = ryi[order]
                h[:n, 5:8] = _RGB[m.T[idx][order]]
                h[:n, 8] = alpha[order]
                h[:n, 9] = reveal[order]
                h[:n, 10] = (RIM_A / FILL_A) * (0.8 + 0.4 * glow[order])
                h[:n, 11] = np.minimum(alpha[order] / FILL_A, 1.0) * 0.6
                lab = float(p["labels"])
                if lab > 0.0 and self.lw > 0:
                    gate = np.clip((2.0 * rxo[order] - 2.5 * self.lw) / (1.5 * self.lw), 0.0, 1.0)
                    la = lab * gate * np.minimum(alpha[order] / FILL_A, 1.0) * reveal[order]
                    la = la * np.where(m.T[idx][order] == 0, _smoothstep(0.6, 1.0, lab), 1.0)   # '=' only near 1
                    lx = np.round(cx[order] - 0.5 * self.lw)
                    ly = np.round(y0 - ryo[order] - 1.15 * self.lh)
                    la = np.where((lx >= 0) & (lx + self.lw <= W), la, 0.0)
                    boxes = []                              # strongest first: a label never overprints another
                    lxl, lyl = lx.tolist(), ly.tolist()
                    for j in np.flatnonzero(la > 1e-3).tolist():
                        bx, by = lxl[j], lyl[j]
                        if any(abs(bx - qx) < 1.1 * self.lw and abs(by - qy) < 1.1 * self.lh for qx, qy in boxes):
                            la[j] = 0.0
                        else:
                            boxes.append((bx, by))
                    h[:n, 12] = la
                    h[:n, 14] = lx
                    h[:n, 15] = ly
                else:
                    h[:n, 12] = 0.0
                h[:n, 13] = m.T[idx][order]
                h[:n, 16] = 1.0 / rxo[order]
                h[:n, 17] = 1.0 / np.maximum(ryo[order], 1.0)
                h[:n, 18] = np.minimum(rxo[order], ryo[order])
                h[:n, 19] = 1.0 / np.maximum(rxi[order], 0.5)
                h[:n, 20] = 1.0 / np.maximum(ryi[order], 0.5)
                h[:n, 21] = np.minimum(rxi[order], ryi[order])
                # bin by span into NCOL columns, strongest first, <= PERCOL each
                c0 = np.clip(np.floor(left[order] * NCOL / W), 0, NCOL - 1).astype(np.int64)
                c1 = np.clip(np.floor(right[order] * NCOL / W), 0, NCOL - 1).astype(np.int64)
                cov = (self.cols[None, :] >= c0[:, None]) & (self.cols[None, :] <= c1[:, None])
                rank = np.cumsum(cov, axis=0) - 1
                keep = cov & (rank < PERCOL)
                r, c = np.nonzero(keep)
                self.table_h[c, 1 + rank[r, c]] = r
                self.table_h[:, 0] = np.minimum(cov.sum(axis=0), PERCOL)
        # ticks: the onsets inside this screen's part of the window, oldest first
        nt = 0
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        k = min(m.nticks, TRING)
        if k:
            chron = (m.nticks - k + self.tick_ar[:k]) % TRING        # the ring in time order: x ascends with it
            tk = m.ticks[chron]
            x = self._xmap(tk[:, 0], win)
            age = self.now - tk[:, 0]
            sel = (x > -4.0) & (x < W + 4.0) & (age < win) & (age >= -0.5)
            if sel.any():
                o = np.flatnonzero(sel)[-MAXT:]                    # the newest MAXT, oldest first
                nt = o.size
                fade = np.exp(-age[o] / max(float(p["memory_s"]), 1.0)) * np.clip(1.0 - age[o] / win, 0.0, 1.0) ** 0.5
                t = self.ticks_h
                t[:nt, 0] = x[o]
                t[:nt, 1] = (0.004 + 0.012 * tk[o, 1]) * H
                t[:nt, 2] = (0.35 + 0.45 * tk[o, 1]) * fade * self.line * (1.0 + 0.6 * pulse)
                t[:nt, 3] = tk[o, 2]
        return n, nt

    # ---------------------------------------------------------------------------------------------------- render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        try:
            dtc = float(dt)
        except (TypeError, ValueError):
            dtc = 0.0
        dtc = min(max(dtc, 0.0), 1.0) if math.isfinite(dtc) else 0.0
        self.now += dtc
        m = self.m
        window = min(max(float(p["window_s"]), 30.0), 300.0)
        m.window_hint = window
        present = self._ingest()
        m.flush(self.now)
        m.step(self.now, dtc, float(p["memory_s"]))
        # the window opens with the piece and widens with it up to window_s
        el = 0.0 if m.t_first is None else max(self.now - m.t_first, 0.0)
        self.win = min(max(1.15 * el, 8.0, 0.08 * window), window)
        busy = present or bool(m.act.any()) or (self.now - m.t_last < 2.0)
        if dtc > 0.0:
            self.line += (float(busy) - self.line) * (1.0 - math.exp(-dtc / LINE_TAU))
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4 or (not m.act.any() and self.line < 2e-3):
            cp.copyto(out, src)
            return
        self._dt = dtc
        n, nt = self._pack(p)
        if n == 0 and nt == 0 and self.line < 2e-3:
            cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        self.arcs.set(self.arcs_h.reshape(-1))
        self.table.set(self.table_h.reshape(-1))
        self.ticks.set(self.ticks_h.reshape(-1))
        y0 = BASE_Y * H
        ytop = int(max(0.0, math.floor(y0 - RY_MAX * H - 1.4 * max(self.lh, 1) - 0.012 * H - 4.0)))
        if n == 0:
            ytop = int(max(0.0, math.floor(y0 - 0.03 * H)))
        now_x = self.span_w - MARGIN * W - self.x_off
        now_a = 0.0
        if -20.0 < now_x < W + 20.0:
            hot = 0.0
            if m.t_last > -1e8:
                hot = math.exp(-max(self.now - m.t_last, 0.0) / 0.6)
            now_a = self.line * (0.25 + 0.35 * hot) * (1.0 + 1.2 * min(max(float(p["pulse"]), 0.0), 1.0))
        line_x0 = 0.0 if self.x_off == 0.0 else -float(W)
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), np.int32(ytop), np.float32(y0),
                                  self.arcs, self.table, self.ticks, np.int32(nt), np.float32(0.018 * H),
                                  np.float32(self.line), np.float32(line_x0), np.float32(now_x), np.float32(now_a),
                                  self.atlas, np.int32(self.lw), np.int32(self.lh),
                                  np.float32(1.5 * amount), np.float32(amount), np.float32(self.s)))

    def close(self) -> None:
        self.arcs = self.table = self.ticks = self.atlas = None
