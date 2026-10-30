"""Electric storm: crackling plasma filaments writhing over a dark field, with branching lightning strikes.

Source: Original (inspired by https://www.shadertoy.com/view/ldlXRS and
        https://github.com/NatronGitHub/openfx-misc/blob/master/Shadertoy/presets/default/noiseanimelectric.frag.glsl)
Author: smfx (original; inspired by nimitz, 'Noise animation - Electric')
License: MIT
license_class: permissive

Inspiration only.  ldlXRS carries no licence line, so Shadertoy's default (CC-BY-NC-SA-3.0) applies to it, and
none of its code is copied or ported.  The shared idea is common knowledge: brightness ~ 1 / ridged turbulence,
so the zero crossings of a noise sum become thin glowing filaments.  The noise, the double domain warp, the
ring/horizon/full mask morph, the colouring and the host-built lightning are all written from scratch here.

Look
  * Two ridged-turbulence layers (sum of |gradient noise|, up to 6 octaves, each octave rotated and sliding on its
    own host-integrated offset so the field writhes instead of scrolling).  Layer A sits on a smooth fbm domain
    warp.  Layer B is warped again by A along the perpendicular of the first warp (the second domain warp).
    Filament intensity is (width / turbulence) ^ sharpness, so filaments are thin cores with a soft electric halo.
  * mask morphs continuously: 0 = a crackling ring (the ring itself is drawn through the warped coordinates, so
    it writhes), 0.5 = a horizon band (the ring's radius runs to infinity while its top sinks to the horizon),
    1 = full screen.
  * Lightning: the host builds each bolt by midpoint displacement into a <=64-segment buffer (a 32-segment main
    channel plus branches and sub-branches), with per-8-segment bounding boxes.  The kernel tests only bolts
    and chunks whose (glow-expanded) box contains the pixel.  A bolt grows down its path for ~45 ms (the
    leader), then flashes (the return stroke), sometimes re-strikes, and fades over 150-300 ms.  Its glow is
    local (core, bloom and a short halo that also lights the nearby plasma), never a full-frame flash.
  * Colour: per-channel exposure 1 - exp(-E * pal(index)), with index = filament intensity * period + drift, so
    halos take the palette colour and the hottest cores burn to white.

Macros (e' = smoothstep(0.1, 0.85, energy))
  energy -> noise clock rate x(0.4 + 1.2 e'), warp displacement (both levels), +1 octave blended in (slewed
            ~2 s), filament brightness, ambient strike rate (x(0.15 + 1.85 e'^1.5)), strike count per beat.
  pulse  -> k = pulse * (0.3 + 0.7 e).  writhe_kick: a rate kick into the noise clock (lurch, then ease out).
            flash_kick: filament gain flash (<= +35%) and a thickness kick (lower ridge exponent).  The rising
            edge (above 0.6 after dropping below 0.3, >= 150 ms apart) spawns 1-3 bolts.
  speed  -> noise clock, spin, colour drift and the ambient strike clock.  Bolt lifetimes are real time.
  palette / palette_period / drift_speed -> pal(filament_intensity * period + drift).  intensity -> exposure.

Per note (note_drive, listens "notes", CONVENTIONS §2b; the idea of lightning that plays the music, after ArcAttack's
singing Tesla coils, https://en.wikipedia.org/wiki/ArcAttack , written from scratch here).  At note_drive 0 the storm
is exactly the one above.  Above 0, every piano note strikes its own bolt at its key's place: full screen, from the
sky down to the key's x (A0 at the left, C8 at the right); horizon band, from the sky to the horizon at that x; ring,
a spoke out of the ring at the key's angle (A0 lower left, round over the top, C8 lower right).  Velocity sets its
brightness and reach: a soft note is a dim, short, single channel that barely flickers; a loud one a long white
return stroke that may re-strike.  Chord size sets the branching: a single note is one clean channel, a full chord
forks wide.  Every note bolt grows down its path for 50-80 ms (the leader blooms in, no hard flash) and then, while
its note still sounds, leaves a dim flickering ghost channel that lights the plasma round it: it lasts with the held
key or the damper pedal (the piano's own decay, up to 8 s) and dies when the dampers fall, so pedalled harmony
glows on.  The pedal also lifts the plasma haze.  The ambient and beat strikes fade toward zero as note_drive -> 1
while notes sound (in ~0.4 s) and come back over a few seconds once the piano rests, so a silent room still has its
storm.  A burst of notes strikes at most 6 bolts a frame (neighbouring keys share one, at their mean place, with
the loudest velocity), and 16 bolts at most are alive (a new one takes the dimmest ghost's slot).  The room
ensemble's register window (sound.register) draws notes outside it dimmer, never hidden.  When the room's screens
share one picture (sound.segment) and the preset asks for it (span: room), the keyboard spans them: low notes on
the left screen, high on the right.  The default is span: screen (each screen the whole keyboard; owner, 30 Oct:
the TVs aren't adjacent, so one picture stretched over both rarely coheres).
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

MAXB = 16           # concurrent bolts (per-note ghosts need more than the beat storm's 8)
SEGS = 64           # segments per bolt
SEGF = 6            # floats per segment: x0, y0, x1, y1, weight, u (reveal order 0..1)
CHUNK = 4           # segments per bounding-box chunk
NCH = SEGS // CHUNK
BSTRIDE = SEGS * SEGF + NCH * 4
SPANS = ("screen", "room")  # screen: each TV the whole picture (default; owner, 30 Oct: the TVs aren't adjacent);
                            # room: one picture across the room's screens (sound.segment)
HDR = 8             # per-bolt header: intensity, reveal, nseg, hue, bbox x0, y0, x1, y1
RING_R = 0.62       # ring radius (units of half the frame height)
BAND_W = 0.30       # mask band half-width scale
HALO_R = 0.085      # bolt halo radius (units)
CORE_W = 0.0032     # bolt core width (units; >= 1.2 internal px)
LATTICE = 1024.0    # noise lattice period: octave offsets wrap at this (the hash is periodic)
BASE_FREQ = 1.5     # base filament frequency (lattice cells per unit) at filament_scale 1
HAZE = 0.10         # faint background plasma haze
INDEX_K = 0.12      # palette index per unit filament intensity (x palette_period)
HALO_K = 0.2        # soft 1/x halo added under the sharp filament core
JITTER = 0.008      # in-kernel bolt jitter amplitude (units), finer than the 32-segment polyline
BOLT_HUE = 0.15     # bolt halo palette index (x palette_period, + drift)
MAXOCT = 6
# per note (note_drive > 0)
NOTE_BOLTS = 6      # note bolts struck per frame, at most (a burst of notes groups by neighbouring keys)
NOTE_X = 0.9        # the keyboard across the frame: A0 .. C8 -> x = -NOTE_X .. +NOTE_X (x the aspect)
GHOST = 0.2         # a sounding note's ghost channel, x the bolt's intensity x the note's LEVEL
GHOST_MAX_S = 8.0   # a ghost fades out by this age even if the note still sounds
NOTE_HUE = 0.12     # pitch-class tint of a note bolt around BOLT_HUE (circle of fifths)

# P layout (floats)
P_FREQ, P_FW, P_WARP1, P_WARP2, P_OCT, P_SHARP, P_WIDTH, P_GAIN, P_BRANCH = 0, 1, 2, 3, 4, 5, 6, 7, 8
P_MRING, P_MFULL, P_R, P_CY, P_BW, P_HAZE, P_PERIOD, P_DRIFT, P_SPIN = 9, 10, 11, 12, 13, 14, 15, 16, 17
P_EXPO, P_CORE, P_HALO, P_ILLUM, P_VIGN, P_LM = 18, 19, 20, 21, 22, 23
P_OG = 72
P_IDXK = 73
P_HALOK = 74
P_JIT = 75
P_PAL = 24          # 12 floats
P_OFFW = 36         # warp fbm: 2 components x 3 octaves x 2 floats = 12
P_OFFA = 48         # ridge A: 6 octaves x 2 = 12
P_OFFB = 60         # ridge B: 6 octaves x 2 = 12
P_N = 80

_SRC = HELPERS + PALETTE_CUDA + r"""
#define MAXB %(maxb)d
#define SEGS %(segs)d
#define SEGF %(segf)d
#define CHUNK %(chunk)d
#define NCH %(nch)d
#define BSTRIDE %(bstride)d
#define HDR %(hdr)d

// Gradient noise on a lattice that repeats every 1024 cells, so host offsets can wrap at 1024 without a jump.
__device__ __forceinline__ float gdot(int ix, int iy, unsigned int s, float dx, float dy) {
    const unsigned int h = hash_u((((unsigned int)ix & 1023u) | (((unsigned int)iy & 1023u) << 10)) ^ s);
    // gradient in [-1, 1)^2 straight from the hash bits (mantissa trick: no int->float conversions)
    const float gx = __uint_as_float(0x40000000u | ((h & 0xffffu) << 7)) - 3.f;
    const float gy = __uint_as_float(0x40000000u | ((h >> 16) << 7)) - 3.f;
    return gx * dx + gy * dy;
}
__device__ __forceinline__ float gnoise(float x, float y, unsigned int s) {
    const float fx = floorf(x), fy = floorf(y);
    const int ix = (int)fx, iy = (int)fy;
    const float tx = x - fx, ty = y - fy;
    const float ux = tx * tx * tx * (tx * (tx * 6.f - 15.f) + 10.f);
    const float uy = ty * ty * ty * (ty * (ty * 6.f - 15.f) + 10.f);
    const float a = gdot(ix, iy, s, tx, ty), b = gdot(ix + 1, iy, s, tx - 1.f, ty);
    const float c = gdot(ix, iy + 1, s, tx, ty - 1.f), d = gdot(ix + 1, iy + 1, s, tx - 1.f, ty - 1.f);
    return (a + (b - a) * ux) + ((c + (d - c) * ux) - (a + (b - a) * ux)) * uy;   // ~[-1, 1], std ~0.25
}

// 3-octave smooth fbm (signed), for the first domain warp
__device__ float fbm3(float qx, float qy, const float* __restrict__ off, const unsigned int* __restrict__ salt) {
    float s = 0.f, amp = 1.f, nrm = 0.f;
    #pragma unroll
    for (int o = 0; o < 3; ++o) {
        s += amp * gnoise(qx + off[2 * o], qy + off[2 * o + 1], salt[o]);
        nrm += amp;
        const float rx = 1.6f * qx - 1.2f * qy, ry = 1.2f * qx + 1.6f * qy;
        qx = rx; qy = ry; amp *= 0.5f;
    }
    return s / nrm;
}

// Ridged turbulence in [0, ~1]: sum of |noise| with a fractional octave count; octaves whose features would be
// finer than ~3 pixels (cycles per pixel f*px > 0.2) fade out so nothing shimmers at render_scale 0.5.
__device__ float ridged(float qx, float qy, float f, const float* __restrict__ off, float oct, float px,
                        const unsigned int* __restrict__ salt, float lm, float og) {
    float s = 0.f, sg = 0.f, amp = 1.f, nrm = 0.f;
    qx *= f; qy *= f;
    for (int o = 0; o < 6; ++o) {
        float w = clamp01(oct - (float)o);
        w *= 1.f - smooth(0.17f, 0.34f, f * px);
        if (w <= 0.f) break;
        const float n = gnoise(qx + off[2 * o], qy + off[2 * o + 1], salt[o]);
        s += amp * w * fabsf(n);
        sg += amp * w * n;
        nrm += amp * w;
        const float rx = 1.6f * qx - 1.2f * qy, ry = 1.2f * qx + 1.6f * qy;   // rotate ~37 deg, x2
        qx = rx; qy = ry; f *= 2.f; amp *= og;
    }
    return 2.2f * (s + (fabsf(sg) - s) * lm) / fmaxf(nrm, 1e-4f);
}

__device__ __forceinline__ float seg_dist2(float px, float py, const float* __restrict__ g) {
    const float ax = g[0], ay = g[1], bx = g[2] - ax, by = g[3] - ay;
    const float qx = px - ax, qy = py - ay;
    const float h = clamp01((qx * bx + qy * by) / (bx * bx + by * by + 1e-9f));
    const float dx = qx - bx * h, dy = qy - by * h;
    return dx * dx + dy * dy;
}

extern "C" __global__
void electric_storm(uchar4* __restrict__ out, const int W, const int H, const float* __restrict__ P,
                    const float* __restrict__ BH, const float* __restrict__ BS, const unsigned int* __restrict__ SALT)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float hh = 0.5f * H;
    const float px = 1.f / hh;                                   // units per internal pixel
    const float X = (x + 0.5f - 0.5f * W) * px, Y = -(y + 0.5f - hh) * px;

    // ---- first domain warp: smooth fbm vector
    const float fw = P[%(fw)d];
    const float wx = fbm3(X * fw, Y * fw, P + %(offw)d, SALT + 16);
    const float wy = fbm3(X * fw + 17.3f, Y * fw - 9.1f, P + %(offw)d + 6, SALT + 32);
    const float x1 = X + P[%(warp1)d] * wx, y1 = Y + P[%(warp1)d] * wy;

    // ---- ridged layer A on the warped coordinates
    const float f0 = P[%(freq)d], oct = P[%(oct)d];
    const float A = ridged(x1, y1, f0, P + %(offa)d, oct, px, SALT + 48, P[%(lm)d], P[%(og)d]);

    // ---- second domain warp: push along the perpendicular of the first warp by A, then a slow spin
    const float k2 = P[%(warp2)d] * (A - 0.35f);
    float x2 = x1 - k2 * wy * 2.f, y2 = y1 + k2 * wx * 2.f;
    {
        const float c = cosf(P[%(spin)d]), s = sinf(P[%(spin)d]);
        const float rx = c * x2 - s * y2, ry = s * x2 + c * y2;
        x2 = rx; y2 = ry;
    }
    const float B = ridged(x2, y2, f0 * 1.13f, P + %(offb)d, oct, px, SALT + 64, P[%(lm)d], P[%(og)d]);

    // branching: A's filament network joins B's (union), so filaments fork and cross
    float rz = B + (fminf(B, A * 1.1f) - B) * P[%(branch)d];

    // ---- mask: ring -> horizon band -> full screen, measured on the warped coordinates (the ring writhes)
    const float R = P[%(R)d], cy = P[%(cy)d];
    const float dyc = y1 - cy;
    const float dc = fabsf(sqrtf(x1 * x1 + dyc * dyc) - R);
    const float d = dc + (fabsf(y1) - dc) * smooth(0.9f, 1.f, P[%(mring)d]);
    const float t = d / P[%(bw)d];
    float mf = 0.5f + 2.6f * t * sqrtf(t);                      // no cap: a cap would draw a visible edge
    mf += (1.f - mf) * P[%(mfull)d];
    rz *= mf;

    const float ir = P[%(width)d] / (rz + 0.003f);
    float I = __powf(ir, P[%(sharp)d]) + P[%(halok)d] * __powf(ir, 1.2f);
    I = fminf(I, 60.f);

    // ---- lightning bolts (host-built polylines; bbox and chunk culling).  A small noise displacement of the
    //      lookup point adds fine jaggedness below the polyline's segment length.
    float br = 0.f, bgc = 0.f, bb = 0.f, illum = 0.f;
    const float cw = fmaxf(P[%(core)d], 1.2f * px), hr = P[%(halo)d], hr2 = hr * hr;
    bool jit = false;
    float JX = X, JY = Y;
    for (int b = 0; b < MAXB; ++b) {
        const float* __restrict__ h = BH + HDR * b;
        const float Ib = h[0];
        if (Ib <= 0.f) continue;
        if (X < h[4] || X > h[6] || Y < h[5] || Y > h[7]) continue;
        const float reveal = h[1];
        const int ns = (int)h[2];
        const float* __restrict__ S = BS + BSTRIDE * b;
        const float* __restrict__ C = S + SEGS * SEGF;
        float bc_ = 0.f, bg_ = 0.f, bh_ = 0.f;
        for (int c = 0; c < NCH; ++c) {
            if (c * CHUNK >= ns) break;
            const float* __restrict__ cb = C + 4 * c;
            if (X < cb[0] || X > cb[2] || Y < cb[1] || Y > cb[3]) continue;
            if (!jit) {                                  // fine jaggedness: displace the lookup point (lazily)
                jit = true;
                const float jf = 30.f, ja = P[%(jit)d];
                JX = X + ja * (gnoise(X * jf, Y * jf, SALT[80]) + 0.4f * gnoise(X * 2.7f * jf, Y * 2.7f * jf, SALT[82]));
                JY = Y + ja * (gnoise(X * jf + 31.7f, Y * jf - 12.9f, SALT[81])
                               + 0.4f * gnoise(X * 2.7f * jf - 7.1f, Y * 2.7f * jf + 3.3f, SALT[83]));
            }
            const int s1 = min(ns, c * CHUNK + CHUNK);
            for (int si = c * CHUNK; si < s1; ++si) {
                const float* __restrict__ g = S + SEGF * si;
                if (g[5] > reveal) continue;
                const float d2 = seg_dist2(JX, JY, g);
                if (d2 >= hr2) continue;
                const float dd = sqrtf(d2);
                const float wgt = g[4];
                const float cwi = cw * (0.5f + 0.5f * wgt);
                const float q = dd / cwi;
                const float hl = 1.f - dd / hr;
                bc_ = fmaxf(bc_, wgt * __expf(-q * q));
                bg_ = fmaxf(bg_, wgt * hl * (__expf(-dd / (4.f * cwi)) + (0.25f * __expf(-dd * 40.f) + 0.15f * hl) * hl));
                bh_ = fmaxf(bh_, wgt * hl * hl * hl);
            }
        }
        if (bg_ > 0.f) {
            const float3 bc = pal(h[3] + P[%(drift)d], P + %(pal)d);
            // the core burns white while the stroke is hot and cools to the palette colour as it fades (a dim
            // leader or a dying channel reads as a blue ghost, never a grey line)
            const float hot = clamp01(0.7f * Ib);
            const float ec = 3.f * Ib * bc_, eg = 1.4f * Ib * bg_;
            br += ec * (bc.x + (1.f - bc.x) * hot) + eg * bc.x;
            bgc += ec * (bc.y + (1.f - bc.y) * hot) + eg * bc.y;
            bb += ec * (bc.z + (1.f - bc.z) * hot) + eg * bc.z;
            illum = fmaxf(illum, Ib * bh_);
        }
    }

    // ---- colour: palette by filament intensity, per-channel exposure (hot cores burn white)
    const float g = P[%(gain)d] * (1.f + P[%(illum)d] * illum);
    const float Ef = I * g;
    const float idx = P[%(period)d] * P[%(idxk)d] * fminf(I, 2.2f) + P[%(drift)d];
    const float3 pc = pal(idx, P + %(pal)d);
    const float hz = P[%(haze)d] * (0.45f + 1.1f * clamp01(0.5f + wx + 0.5f * wy)) / mf;
    const float3 hc = pal(0.08f * P[%(period)d] + P[%(drift)d], P + %(pal)d);
    const float ex = P[%(expo)d];
    float r = 1.f - __expf(-ex * (Ef * pc.x + hz * hc.x + br));
    float gg = 1.f - __expf(-ex * (Ef * pc.y + hz * hc.y + bgc));
    float bl = 1.f - __expf(-ex * (Ef * pc.z + hz * hc.z + bb));
    const float vx = X / (1.78f * 1.15f), vy = Y / 1.15f;
    const float vg = 1.f - P[%(vign)d] * (vx * vx + vy * vy);
    out[y * W + x] = pack(make_float4(255.f * r * vg, 255.f * gg * vg, 255.f * bl * vg, 255.f));
}
""" % {"maxb": MAXB, "segs": SEGS, "segf": SEGF, "chunk": CHUNK, "nch": NCH, "bstride": BSTRIDE, "hdr": HDR,
       "freq": P_FREQ, "fw": P_FW, "warp1": P_WARP1, "warp2": P_WARP2, "oct": P_OCT, "sharp": P_SHARP,
       "width": P_WIDTH, "gain": P_GAIN, "branch": P_BRANCH, "mring": P_MRING, "mfull": P_MFULL, "R": P_R,
       "cy": P_CY, "bw": P_BW, "haze": P_HAZE, "period": P_PERIOD, "drift": P_DRIFT, "spin": P_SPIN,
       "expo": P_EXPO, "core": P_CORE, "halo": P_HALO, "illum": P_ILLUM, "vign": P_VIGN, "pal": P_PAL,
       "offw": P_OFFW, "offa": P_OFFA, "offb": P_OFFB, "lm": P_LM, "og": P_OG, "idxk": P_IDXK, "halok": P_HALOK, "jit": P_JIT}

_OPTIONS = ("--std=c++14", "--use_fast_math")
_OPTS = _OPTIONS


def _ss(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _wrap(x: float, period: float) -> float:
    return math.fmod(x, period) if x >= 0 else period - math.fmod(-x, period)


def _mask_shape(mask: float) -> tuple[float, float, float, float]:
    """mask -> (ring->band morph m', full-screen blend, circle radius, circle centre y).  The circle keeps its
    top on a line that sinks from y = RING_R to the horizon (y = 0) while its radius runs to infinity."""
    mr = min(max(mask * 2.0, 0.0), 1.0)
    full = _ss(0.0, 1.0, min(max(mask * 2.0 - 1.0, 0.0), 1.0))
    top = RING_R * (1.0 - mr)
    R = RING_R / max(1.0 - mr, 0.02)
    return mr, full, R, top - R


def _mpd(a, b, depth: int, rough: float, rng) -> np.ndarray:
    """Midpoint displacement: 2**depth segments from a to b, each midpoint pushed sideways by N(0, rough * len)."""
    pts = np.array([a, b], np.float64)
    for _ in range(depth):
        d = pts[1:] - pts[:-1]
        L = np.hypot(d[:, 0], d[:, 1])[:, None] + 1e-9
        nrm = np.stack([-d[:, 1], d[:, 0]], 1) / L
        off = np.clip(rng.normal(0.0, rough, (len(d), 1)), -2.2 * rough, 2.2 * rough) * L
        mid = 0.5 * (pts[1:] + pts[:-1]) + nrm * off
        new = np.empty((len(pts) * 2 - 1, 2))
        new[0::2] = pts
        new[1::2] = mid
        pts = new
    return pts


class ElectricStorm(Source):
    name = "electric_storm"
    label = "Electric storm"
    description = ("Thin electric filaments writhe over a dark field (twice domain-warped ridged turbulence) while "
                   "branching lightning strikes across the frame on the beat.")
    listens = ("notes",)
    params = {
        "note_drive": num(0.0, 1.0, 0.0, "Notes strike bolts (0 = beat storm)", tier="regional", basic=True),
        "turbulence": num(0.0, 1.5, 0.8, "Turbulence (warp displacement)", tier="regional", basic=True),
        "mask": num(0.0, 1.0, 1.0, "Mask (ring -> horizon band -> full screen)", tier="regional", basic=True),
        "filament_sharpness": num(0.0, 1.0, 0.7, "Filament sharpness", tier="regional"),
        "octaves": num(2.0, 5.0, 4.0, "Octaves (detail)", step=0.05, tier="regional"),
        "bolt_branching": num(0.0, 1.0, 0.5, "Branching (bolts and filament forks)", tier="regional"),
        "filament_scale": num(0.4, 2.5, 1.0, "Filament scale", tier="regional"),
        "writhe_kick": num(0.0, 4.0, 2.0, "Beat writhe kick", tier="regional"),
        "flash_kick": num(0.0, 1.0, 0.7, "Beat flash kick", tier="regional"),
        **standard_scene_params(palette="ice", render_scale=0.5, period=1.0, drift=0.02),
        "bolt_rate": num(0.0, 3.0, 0.7, "Ambient strikes", unit="/s", tier="realtime", bind="rate"),
        "span": enum(SPANS, "screen", "Span (this screen, or one picture across the room)"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.span_room = params.get("span") == "room"
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.aspect = ctx.width / ctx.height
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.k = raw_module(_SRC, _OPTS).get_function("electric_storm")
        self.P = cp.zeros(P_N, cp.float32)
        self.Ph = np.zeros(P_N, np.float32)
        self.BH = cp.zeros(MAXB * HDR, cp.float32)
        self.BHh = np.zeros(MAXB * HDR, np.float32)
        self.BS = cp.zeros(MAXB * BSTRIDE, cp.float32)
        seed = int(params["seed"])
        rng = np.random.default_rng(seed)
        self.SALT = cp.asarray(rng.integers(0, 2**32, 128, dtype=np.uint64).astype(np.uint32))   # per-layer/octave noise seeds
        self.rng = np.random.default_rng(seed + 7919)          # bolts (host-side randomness only)
        # per-octave lattice velocities (cells per clock unit): random directions, finer octaves boil faster
        def vel(n, base, grow):
            ang = rng.uniform(0.0, 2.0 * math.pi, n)
            mag = base * grow ** np.arange(n) * rng.uniform(0.8, 1.2, n)
            return np.stack([np.cos(ang) * mag, np.sin(ang) * mag], 1).reshape(-1)
        self.vw = np.concatenate([vel(3, 0.22, 1.7), vel(3, 0.22, 1.7)])
        self.va = vel(MAXOCT, 0.55, 2.0)
        self.vb = vel(MAXOCT, 0.55, 2.0)
        self.ow0 = rng.uniform(0, LATTICE, self.vw.size)
        self.oa0 = rng.uniform(0, LATTICE, self.va.size)
        self.ob0 = rng.uniform(0, LATTICE, self.vb.size)
        self.clock = 0.0            # noise clock (host float64, never wrapped; offsets wrap per octave)
        self.spin = 0.0
        self.drift = 0.0
        self.time = 0.0             # real time (bolt ages, beat spacing)
        self.e_a = None             # energy slewed for amplitudes (~0.4 s)
        self.e_c = None             # energy slewed for complexity (~2 s)
        self.armed = True
        self.last_fire = -1.0
        # bolts: slot -> dict(age, life, lead, I0, restrike, nseg, hue, bbox) (+ pitches, spike, ph for a note's)
        self.bolts: list = [None] * MAXB
        self.ped_s = 0.0            # damper pedal, slewed
        self.note_act = 0.0         # recent notes on this screen (decays ~2 s)
        self.np_s = 0.0             # notes present, eased: the ambient and beat strikes yield to them
        self._reg_key, self._reg_w = None, None

    # ------------------------------------------------------------------ bolts
    def _endpoints(self, mask: float):
        rng, asp = self.rng, self.aspect
        wr = max(0.0, 1.0 - 2.0 * mask)
        wf = max(0.0, 2.0 * mask - 1.0)
        wb = max(0.0, 1.0 - wr - wf)
        mode = rng.choice(3, p=np.array([wr, wb, wf]) / (wr + wb + wf))
        if mode == 0:                                   # ring: a chord across it, or outward from it
            th = rng.uniform(0, 2 * math.pi)
            a = (RING_R * math.cos(th), RING_R * math.sin(th))
            if rng.random() < 0.5:
                th2 = th + math.pi + rng.uniform(-1.1, 1.1)
                b = (RING_R * math.cos(th2), RING_R * math.sin(th2))
            else:
                th2 = th + rng.uniform(-0.5, 0.5)
                r2 = rng.uniform(1.3, 2.1)
                b = (r2 * math.cos(th2), r2 * math.sin(th2))
            return a, b
        if mode == 1:                                   # sky to the horizon / arc top
            mr, _full, R, cy = _mask_shape(mask)
            x0 = rng.uniform(-0.92 * asp, 0.92 * asp)
            x1 = x0 + rng.uniform(-0.6, 0.6)
            xc = min(max(x1, -0.95 * R), 0.95 * R)
            yt = cy + math.sqrt(max(R * R - xc * xc, 0.0))
            yt = yt + (0.0 - yt) * _ss(0.9, 1.0, mr)
            return (x0, 1.06), (x1, yt + rng.uniform(-0.04, 0.02))
        if rng.random() < 0.7:                          # full: from the sky, down across the frame
            x0 = rng.uniform(-0.95 * asp, 0.95 * asp)
            return (x0, 1.06), (x0 + rng.uniform(-0.9, 0.9), 1.06 - rng.uniform(1.0, 2.0))
        y0 = rng.uniform(-0.7, 0.7)                     # full: a side-to-side arc
        side = 1.0 if rng.random() < 0.5 else -1.0
        return (side * (asp + 0.05), y0), (-side * rng.uniform(0.0, asp), y0 + rng.uniform(-0.5, 0.5))

    def _note_ends(self, mask: float, ux: float, vel: float):
        """A note bolt's ends: at its key's place (``ux`` 0..1 across this screen's keyboard), reach by velocity."""
        rng, asp = self.rng, self.aspect
        wr = max(0.0, 1.0 - 2.0 * mask)
        wf = max(0.0, 2.0 * mask - 1.0)
        wb = max(0.0, 1.0 - wr - wf)
        mode = rng.choice(3, p=np.array([wr, wb, wf]) / (wr + wb + wf))
        x = (2.0 * ux - 1.0) * NOTE_X * asp
        if mode == 0:                                   # ring: a spoke out at the key's angle (A0 lower left -> C8)
            th = math.pi * (1.25 - 1.5 * ux)
            th2 = th + rng.uniform(-0.25, 0.25)
            r2 = RING_R + 0.4 + 0.8 * vel
            return (RING_R * math.cos(th), RING_R * math.sin(th)), (r2 * math.cos(th2), r2 * math.sin(th2))
        if mode == 1:                                   # sky to the horizon / arc top, landing at the key's x
            mr, _full, R, cy = _mask_shape(mask)
            xc = min(max(x, -0.95 * R), 0.95 * R)
            yt = cy + math.sqrt(max(R * R - xc * xc, 0.0))
            yt = yt + (0.0 - yt) * _ss(0.9, 1.0, mr)
            y1 = 1.06 + (yt - 1.06) * (0.45 + 0.55 * vel)          # soft notes stop short
            return (x + rng.uniform(-0.35, 0.35), 1.06), (x, y1 + rng.uniform(-0.03, 0.02))
        return (x + rng.uniform(-0.35, 0.35), 1.06), (x, 1.06 - (0.7 + 1.3 * vel) * rng.uniform(0.9, 1.1))

    def _build_bolt(self, mask: float, branching: float, ends=None):
        """-> (segments (n, 6), n) with a 32-segment main channel and branches (total <= SEGS)."""
        rng = self.rng
        a, b = self._endpoints(mask) if ends is None else ends
        a, b = np.array(a), np.array(b)
        main = _mpd(a, b, 5, 0.2, rng)
        Lm = float(np.hypot(*(b - a)))
        segs = []
        nm = len(main) - 1
        for i in range(nm):
            u = (i + 1) / nm
            segs.append((*main[i], *main[i + 1], 1.0 - 0.25 * u, u))
        # branches: count and length grow with branching (spawn-time only, so the knob never jumps a bolt)
        nb = int(rng.binomial(4, min(1.0, branching * 0.9)))
        direction = (b - a) / (Lm + 1e-9)
        roots = sorted(rng.choice(np.arange(3, int(nm * 0.8)), size=min(nb, int(nm * 0.8) - 3), replace=False)) \
            if nb > 0 else []
        branches = []
        for r in roots:
            if len(segs) + 8 > SEGS:
                break
            ang = rng.uniform(0.35, 0.95) * (1 if rng.random() < 0.5 else -1)
            c, s = math.cos(ang), math.sin(ang)
            dvec = np.array([c * direction[0] - s * direction[1], s * direction[0] + c * direction[1]])
            ln = Lm * rng.uniform(0.16, 0.32) * (0.6 + 0.7 * branching)
            pts = _mpd(main[r], main[r] + dvec * ln, 3, 0.24, rng)
            u0 = (r + 1) / nm
            for j in range(len(pts) - 1):
                segs.append((*pts[j], *pts[j + 1], 0.55 - 0.15 * j / 8, u0 + (j + 1) / 8 * ln / Lm))
            branches.append((pts, u0, ln, dvec))
        # sub-branches when branching is high
        for pts, u0, ln, dvec in branches:
            if len(segs) + 4 > SEGS or rng.random() > (branching - 0.4) * 1.4:
                continue
            j = int(rng.integers(2, 6))
            ang = rng.uniform(0.4, 0.9) * (1 if rng.random() < 0.5 else -1)
            c, s = math.cos(ang), math.sin(ang)
            d2 = np.array([c * dvec[0] - s * dvec[1], s * dvec[0] + c * dvec[1]])
            l2 = ln * rng.uniform(0.35, 0.6)
            sp = _mpd(pts[j], pts[j] + d2 * l2, 2, 0.26, rng)
            uj = u0 + j / 8 * ln / Lm
            for q in range(len(sp) - 1):
                segs.append((*sp[q], *sp[q + 1], 0.32, uj + (q + 1) / 4 * l2 / Lm))
        return np.array(segs[:SEGS], np.float32)

    def _spawn(self, mask: float, branching: float, e1: float, note=None) -> None:
        """A bolt: ambient / beat (note None), or a note's (note = dict(pitches, ux, vel, age, wgt), struck at its
        key's place, brightness and reach by velocity, a ghost channel while it sounds)."""
        free = [i for i, b in enumerate(self.bolts) if b is None]
        if free:
            slot = free[0]
        else:                                           # full: the dimmest (a fading ghost), then the oldest
            slot = min(range(MAXB), key=lambda i: (self.bolts[i].get("inten", 1e9), -self.bolts[i]["age"]))
        rng = self.rng
        ends = None if note is None else self._note_ends(mask, note["ux"], note["vel"])
        segs = self._build_bolt(mask, branching, ends)
        n = len(segs)
        static = np.zeros(BSTRIDE, np.float32)
        static[: n * SEGF] = segs.reshape(-1)
        chunks = np.zeros((NCH, 4), np.float32)
        chunks[:] = (1e9, 1e9, -1e9, -1e9)
        for c in range(NCH):
            sl = segs[c * CHUNK: (c + 1) * CHUNK]
            if len(sl) == 0:
                continue
            xs = np.concatenate([sl[:, 0], sl[:, 2]])
            ys = np.concatenate([sl[:, 1], sl[:, 3]])
            m = HALO_R + 0.02                           # glow radius + the in-kernel jitter amplitude
            chunks[c] = (xs.min() - m, ys.min() - m, xs.max() + m, ys.max() + m)
        static[SEGS * SEGF:] = chunks.reshape(-1)
        self.BS[slot * BSTRIDE:(slot + 1) * BSTRIDE].set(static)
        valid = chunks[chunks[:, 0] < 1e8]
        bbox = (float(valid[:, 0].min()), float(valid[:, 1].min()), float(valid[:, 2].max()), float(valid[:, 3].max()))
        if note is None:
            self.bolts[slot] = dict(age=0.0, life=float(rng.uniform(0.15, 0.3)),
                                    lead=float(rng.uniform(0.035, 0.055)),
                                    I0=float(rng.uniform(0.8, 1.15) * (0.75 + 0.35 * e1)),
                                    restrike=float(rng.uniform(0.07, 0.13)) if rng.random() < 0.45 else -1.0,
                                    nseg=n, hue=float(BOLT_HUE + rng.uniform(-0.04, 0.04)), bbox=bbox)
            return
        vel, pitches = note["vel"], note["pitches"]
        lead = float(rng.uniform(0.05, 0.08))           # the leader blooms down its path: no hard flash
        pc = int(pitches[int(np.argmax(note["vels"]))]) % 12
        self.bolts[slot] = dict(age=min(note["age"], 0.5 * lead), life=float(rng.uniform(0.15, 0.3)) * (0.8 + 0.5 * vel),
                                lead=lead,
                                I0=float((0.3 + 0.95 * vel) * (0.8 + 0.3 * e1) * (0.4 + 0.6 * note["wgt"])
                                         * rng.uniform(0.92, 1.08)),
                                restrike=float(rng.uniform(0.07, 0.13)) if rng.random() < 0.45 * vel else -1.0,
                                nseg=n, hue=float(BOLT_HUE + NOTE_HUE * ((pc * 7 % 12) / 12.0 - 0.5)), bbox=bbox,
                                pitches=np.asarray(pitches, np.int64), spike=1.6 * (0.35 + 0.65 * vel),
                                ph=float(rng.uniform(0.0, 2.0 * math.pi)))

    def _bolt_headers(self, dt: float, period: float, lv=None) -> None:
        """Bolt intensities this frame; ``lv``: the notes' LEVEL (128) for note bolts' ghost channels, or None."""
        H = self.BHh
        H[:] = 0.0
        for i, b in enumerate(self.bolts):
            if b is None:
                continue
            b["age"] += dt
            age, lead, life = b["age"], b["lead"], b["life"]
            if b.get("pitches") is not None:            # a note's bolt: the stroke, then a ghost while it sounds
                g = 0.0
                if lv is not None and age < GHOST_MAX_S:
                    g = float(lv[b["pitches"]].max())
                    g = min(max(g, 0.0), 1.0) if math.isfinite(g) else 0.0
                fl = 0.8 + 0.2 * math.sin(11.0 * self.time + b["ph"]) * math.sin(4.3 * self.time + 2.0 * b["ph"])
                ghost = (GHOST * b["I0"] * g * fl * _ss(lead, lead + 0.12, age)
                         * (1.0 - _ss(GHOST_MAX_S - 2.0, GHOST_MAX_S, age)))
                if age > lead + life and ghost <= 0.004:
                    self.bolts[i] = None
                    continue
                if age < lead:
                    reveal, stroke = age / lead, 0.45 * b["I0"] * _ss(0.0, lead, age)
                else:
                    reveal = 2.0
                    a2 = age - lead
                    stroke = 0.0
                    if a2 < life:                       # the return stroke swells out of the leader over ~2 frames
                        r = 1.0 - math.exp(-a2 / 0.012)
                        stroke = b["I0"] * (0.45 * math.exp(-a2 / 0.02)
                                            + r * (b["spike"] * math.exp(-a2 / 0.03) + math.exp(-a2 / (life * 0.35))))
                        if b["restrike"] > 0 and a2 > b["restrike"]:
                            stroke += 0.9 * b["I0"] * math.exp(-(a2 - b["restrike"]) / 0.04)
                        stroke *= min(1.0, 3.0 * (1.0 - a2 / life))
                inten = max(stroke, 0.0) + ghost
                b["inten"] = inten
                if inten > 0.0:
                    H[i * HDR:(i + 1) * HDR] = (inten, reveal, b["nseg"], b["hue"] * period, *b["bbox"])
                continue
            if age > lead + life:
                self.bolts[i] = None
                continue
            if age < lead:                              # stepped leader: grows down the path, dim
                reveal = age / lead
                inten = 0.45 * b["I0"]
            else:                                       # return stroke, decay, optional re-strike
                reveal = 2.0
                a2 = age - lead
                inten = b["I0"] * (1.6 * math.exp(-a2 / 0.03) + math.exp(-a2 / (life * 0.35)))
                if b["restrike"] > 0 and a2 > b["restrike"]:
                    inten += 0.9 * b["I0"] * math.exp(-(a2 - b["restrike"]) / 0.04)
                inten *= min(1.0, 3.0 * (1.0 - a2 / life))      # reach exactly 0 at end of life
            b["inten"] = inten
            if inten <= 0.0:
                continue
            H[i * HDR:(i + 1) * HDR] = (inten, reveal, b["nseg"], b["hue"] * period, *b["bbox"])

    def _reg_weights(self, snd):
        """Per-pitch weights (128) under the room ensemble's register window (``sound.register``), cached; None
        without one (all at full)."""
        reg = getattr(snd, "register", None) if snd is not None else None
        if not reg:
            return None
        if self._reg_key != reg:
            try:
                from stream_service.ensemble import note_weights
                w = note_weights(reg, np.arange(128))
                w = None if w is None else np.nan_to_num(np.asarray(w, np.float64), nan=1.0).clip(0.0, 1.0)
            except Exception:
                w = None
            self._reg_key, self._reg_w = reg, w
        return self._reg_w

    def _slice(self, snd) -> tuple:
        """This screen's part of the keyboard as register (lo, hi) and whether it is the last (rightmost) part: the
        whole keyboard, or 1/n of it when the room's screens share one picture (``sound.segment`` (k, n)) and
        span is room."""
        seg = getattr(snd, "segment", None) if snd is not None and self.span_room else None
        try:
            k, n = (int(seg[0]), int(seg[1])) if seg is not None else (0, 1)
        except Exception:
            k, n = 0, 1
        if n >= 2 and 0 <= k < n:
            return k / n, (k + 1) / n, k == n - 1
        return 0.0, 1.0, True

    def _notes(self, dt: float, drive: float, mask: float, branching: float, e1: float):
        """The piano: a bolt per onset (note_drive > 0; a burst groups neighbouring keys into <= NOTE_BOLTS bolts),
        and the eased presence of notes on this screen.  Returns (the ambient / beat strikes' gain, 1 - drive x
        presence; the notes' LEVEL (128) for the ghost channels, or None)."""
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(fr is not None and getattr(snd, "present", False))
        ped, lv, notes = 0.0, None, None
        if heard:
            try:
                ped = float(fr.pedals[0])
            except Exception:
                ped = 0.0
            ped = min(max(ped, 0.0), 1.0) if math.isfinite(ped) else 0.0
            try:
                notes = np.asarray(fr.notes, np.float64)
                lv = np.nan_to_num(notes[:, 0], nan=0.0, posinf=1.0, neginf=0.0) if notes.shape == (128, 4) else None
            except Exception:
                notes, lv = None, None
        if dt > 0:
            self.ped_s += (ped - self.ped_s) * (1.0 - math.exp(-dt / (0.15 if ped > self.ped_s else 0.35)))
            self.note_act *= math.exp(-dt / 2.0)
        lo, hi, last = self._slice(snd)
        if heard and drive > 0.0:
            picked = []
            try:
                ons = list(fr.onsets or ())
            except Exception:
                ons = []
            for o in ons:
                try:
                    pitch, vel, age = int(o[0]), float(o[1]), float(o[2])
                except Exception:
                    continue
                if not (0 <= pitch < 128 and math.isfinite(vel) and math.isfinite(age)):
                    continue
                ux = (min(max((pitch - 21.0) / 87.0, 0.0), 1.0) - lo) / (hi - lo)
                if ux < 0.0 or ux > 1.0 or (ux == 1.0 and not last):
                    continue                            # another screen's part of the keyboard
                picked.append((pitch, min(max(vel, 0.0), 1.0), min(max(age, 0.0), 0.2), ux))
            if picked:
                picked.sort()
                chord = len(picked)
                if lv is not None and notes is not None:     # the chord: notes struck in the last 0.1 s
                    with np.errstate(invalid="ignore"):
                        chord = max(chord, int(np.count_nonzero(notes[:, 2] < 0.1)))
                br = min(max((0.1 + 0.25 * (chord - 1)) * (0.5 + branching), 0.0), 1.0)
                w = self._reg_weights(snd)
                for grp in np.array_split(np.arange(len(picked)), min(len(picked), NOTE_BOLTS)):
                    g = [picked[j] for j in grp]
                    vels = [q[1] for q in g]
                    pit = [q[0] for q in g]
                    wgt = 1.0 if w is None else float(max(w[q] for q in pit))
                    self._spawn(mask, br, e1, note=dict(pitches=pit, vels=vels, vel=max(vels),
                                                        ux=float(np.mean([q[3] for q in g])),
                                                        age=min(q[2] for q in g), wgt=wgt))
                    self.note_act += 0.5 + 0.5 * max(vels)
        lvl = 0.0
        if lv is not None:
            L = lv[21 + int(round(lo * 87.0)):22 + int(round(hi * 87.0))]
            lvl = float(L.max()) if L.size else 0.0
        target = min(1.0, self.note_act + 2.0 * max(lvl, 0.0)) if heard else 0.0
        if dt > 0:
            self.np_s += (target - self.np_s) * (1.0 - math.exp(-dt / (0.4 if target > self.np_s else 2.0)))
        return 1.0 - drive * self.np_s, lv

    # ------------------------------------------------------------------ frame
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        e1 = _ss(0.1, 0.85, e)
        if self.e_a is None:
            self.e_a = self.e_c = e1
        elif dt > 0:
            self.e_a += (e1 - self.e_a) * (1.0 - math.exp(-dt / 0.4))
            self.e_c += (e1 - self.e_c) * (1.0 - math.exp(-dt / 2.0))
        ea, ec = self.e_a, self.e_c
        k = pulse * (0.3 + 0.7 * e)

        # ---- integrate rates (kicks go into rates only)
        self.time += dt
        rate = 0.42 * (0.4 + 1.2 * e1) * (1.0 + float(p["writhe_kick"]) * k)
        self.clock += rate * spd * dt
        self.spin = _wrap(self.spin + 0.05 * (0.4 + 1.2 * e1) * spd * dt, 2.0 * math.pi)
        self.drift = _wrap(self.drift + float(p["drift_speed"]) * spd * dt, 10.0)

        # ---- the piano: a bolt per note (note_drive > 0); the ambient and beat strikes yield to notes by auto_gain
        mask, branching = float(p["mask"]), float(p["bolt_branching"])
        drive = min(max(float(p["note_drive"]), 0.0), 1.0)
        auto_gain, lv = self._notes(dt, drive, mask, branching, e1)

        # ---- spawns: beat rising edge (hysteresis 0.6 / 0.3, >= 150 ms apart) -> 1-3 bolts; ambient strikes
        if pulse < 0.3:
            self.armed = True
        if self.armed and pulse > 0.6 and self.time - self.last_fire >= 0.15:
            self.armed = False
            self.last_fire = self.time
            n = 1 + int(self.rng.random() < 0.8 * e1) + int(self.rng.random() < 0.6 * e1 * e1)
            for _ in range(n):
                if drive <= 0.0 or self.rng.random() < auto_gain:
                    self._spawn(mask, branching, e1)
        amb = float(p["bolt_rate"]) * (0.15 + 1.85 * e1 ** 1.5) * spd * auto_gain
        if dt > 0 and amb > 0 and self.rng.random() < 1.0 - math.exp(-amb * dt):
            self._spawn(mask, branching, e1)
        period = float(p["palette_period"])
        self._bolt_headers(dt, period, lv)

        # ---- field parameters
        scale = float(p["filament_scale"])
        turb = float(p["turbulence"])
        sh = float(p["filament_sharpness"])
        sharp = 2.0 + 1.5 * sh
        lm = 0.25 + 0.65 * sh
        og = 0.55 + 0.1 * sh
        fk = float(p["flash_kick"])
        sharp *= 1.0 - 0.3 * fk * math.sqrt(k)                      # thickness kick: broader halos on the beat
        width = 0.075 - 0.045 * sh
        gain = (0.62 + 0.55 * _ss(0.0, 1.0, ea)) * (1.0 + 0.35 * fk * k)
        oct_eff = min(float(p["octaves"]) + ec ** 1.5, float(MAXOCT))
        mr, full, R, cy = _mask_shape(mask)

        Ph = self.Ph
        Ph[P_FREQ] = BASE_FREQ / scale
        Ph[P_FW] = 0.55 / scale
        Ph[P_WARP1] = turb * 0.3 * (0.55 + 0.45 * ea)
        Ph[P_WARP2] = turb * 0.28 * (0.5 + 0.5 * ea) * scale ** 0.5
        Ph[P_OCT] = oct_eff
        Ph[P_SHARP] = sharp
        Ph[P_WIDTH] = width
        Ph[P_GAIN] = gain
        Ph[P_BRANCH] = branching
        Ph[P_MRING] = mr
        Ph[P_MFULL] = full
        Ph[P_R] = R
        Ph[P_CY] = cy
        Ph[P_BW] = BAND_W * (1.0 + 3.0 * full)
        Ph[P_HAZE] = HAZE * (0.7 + 0.6 * ea) * (1.0 + 0.6 * drive * self.ped_s)     # the pedal: resonance
        Ph[P_PERIOD] = period
        Ph[P_DRIFT] = self.drift
        Ph[P_SPIN] = self.spin
        Ph[P_EXPO] = 1.4 * float(p["intensity"])
        Ph[P_CORE] = CORE_W
        Ph[P_HALO] = HALO_R
        Ph[P_ILLUM] = 2.5
        Ph[P_VIGN] = 0.22
        Ph[P_LM] = lm
        Ph[P_OG] = og
        Ph[P_IDXK] = INDEX_K
        Ph[P_HALOK] = HALO_K
        Ph[P_JIT] = JITTER
        Ph[P_PAL:P_PAL + 12] = palette_coeffs(p["palette"])
        c = self.clock
        Ph[P_OFFW:P_OFFW + 12] = np.fmod(self.ow0 + self.vw * c, LATTICE)
        Ph[P_OFFA:P_OFFA + 12] = np.fmod(self.oa0 + self.va * c, LATTICE)
        Ph[P_OFFB:P_OFFB + 12] = np.fmod(self.ob0 + self.vb * c, LATTICE)
        self.P.set(Ph)
        self.BH.set(self.BHh)
        launch_2d(self.k, self.w, self.h, (self.buf, np.int32(self.w), np.int32(self.h), self.P, self.BH, self.BS,
                                           self.SALT))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.buf = None
        self.P = None
        self.BH = None
        self.BS = None
