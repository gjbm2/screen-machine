"""Phase portrait: the chord the piano is holding, drawn as its own just-intonation Lissajous figure in glowing wire,
beside the room's real waveform drawn in phase space.

Source: Original (inspired by Lissajous figures; John Whitney's "digital harmony",
        https://en.wikipedia.org/wiki/John_Whitney_(animator) and https://archive.bridgesmathart.org/1999/bridges1999-137.html;
        Takens delay embedding of musical sound, https://ericpnichols.com/papers/AESMusicalAttractors.pdf and
        https://arxiv.org/pdf/2211.05821; Tenney harmonic distance, https://en.xen.wiki/w/Tenney_norm)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
----------------------
Over any scene, a thin glowing wire figure turns slowly in 3D.  It is the chord the piano is sounding, drawn as
the shape its notes would trace on an oscilloscope if they were tuned in pure (just) ratios:

  * one note is a circle;
  * a fifth (2:3) is a ring with three lobes wound twice round it, a small knot;
  * a major triad (4:5:6) is a cage; a minor triad (10:12:15) is a denser cage; a dominant seventh (20:25:30:36)
    is denser still.  How busy the wire looks is how complex the harmony is.
  * When a chord holds a note whose ratio would need more than 48 turns to close (a tritone in a diminished chord,
    a cluster), that note is drawn at its real equal-tempered ratio instead, so the figure no longer closes: a
    frayed, unresolved wire.  Dissonance looks frayed; consonance looks clean.

The figures are not quite still.  A piano is tuned in equal temperament, not in pure ratios, so each interval is a
little off (a tempered fifth is 0.44 Hz flat of a pure one at middle C; a major third 2.6 Hz sharp).  On a real
oscilloscope that mistuning makes the figure roll.  Here each voice's phase rolls at its true mistuning rate
(slowed down by ``spin``), so fifths turn very slowly and thirds turn faster: the temperament is the motion.

As a chord dies away each note's wire shrinks with its loudness (sqrt of the level), and high notes die first, so
the figure flattens back towards the bass's circle.  A new chord blooms in over ~0.1 s while the old one fades.

The second figure (``reality``) is the room's real sound: the microphone's waveform w(t) plotted in 3D as
(w(t), w(t + tau), w(t + 2 tau)), a Takens delay embedding, turned so that a pure tone draws a circle:
x = (w(t) - w(t + 2 tau)) / 2, y = w(t + tau), z = (w(t) + w(t + 2 tau)) / 2.  tau is a quarter of the bass note's
period (the bass folded into 80-320 Hz, so tau is 37-150 samples at 48 kHz; with no MIDI, the loudest FFT band's),
so a piano tone, with its overtones, draws a looping knot that breathes and precesses.  Two periods of the bass
(8 tau samples) are drawn each frame, from the wave's trigger, from a copy low-passed with a triangle of half-width
tau / 2 (the bass and its first few partials stay; the treble's scribble, which made a chord's trace an unreadable
hairball, goes).  A chord's trace still does not close (its notes are not harmonics of the bass): with persistence
it builds a soft tangle around the loop.  It is drawn thicker and warmer than the ideal figure
and in the same rotation, so the ideal and the real can be compared.  (The real wave runs ~0.25 s ahead of the
photon-aligned notes; the phosphor afterglow hides it.)

The maths
---------
Voices: the bass is the lowest note sounding (LEVEL > 0.04), loud or not, and the 3 loudest notes above it with
LEVEL > 0.03 join it (the current set is preferred a little, and a new set must hold for 50 ms, so the figure does
not flicker between near-equal notes).  Each upper voice's
interval above the bass, i = (p - p_b) mod 12, takes the just ratio 1/1 16/15 9/8 6/5 5/4 4/3 45/32 3/2 8/5 5/3 9/5
(7/4 with ``septimal``) 15/8, times 2 for a voice an octave or more above the bass (octaves beyond the first are
folded: a wide piano voicing would otherwise need 40-turn figures that read as a smudge).  Equal ratios are kept
once.  The ratios are scaled by the lcm of their denominators and divided by their gcd: integers n_i (C E G ->
4:5:6, C Eb G -> 10:12:15, C E G B -> 8:10:12:15).  While max n > 48 the voice with the largest denominator is
dropped to the "fuzz" (drawn at its tempered ratio n_b 2^(semitones/12), amplitude x 0.6: the open figure).

The signal is a closed curve in R^5, s in [0, 2 pi):
    u(s) = (A_b sin(n_b s), A_b cos(n_b s), A_2 sin(n_2 s + phi_2), A_3 sin(n_3 s + phi_3), A_4 sin(n_4 s + phi_4))
with A = sqrt(LEVEL), scaled by a peak follower of |A| (fast up, 6 s down, floor 0.35) so a quiet player still
draws a full-size figure but a dying chord shrinks.  A slowly turning orthonormal 3x5 matrix M(t) (four plane rotations of R^5 whose angles
sway at incommensurate rates set by ``spin``, about a small fixed tilt: the bass's circle stays mostly in the
picture plane and the upper voices lean into it from depth) takes it to 3D; then a camera with a gentle yaw / pitch
sway, a slow roll (0.03 + 0.6 spin rad/s) and perspective.  Precession:
    phi_i += 2 pi k (f_i,ET - f_b n_i / n_b) dt,  k = 0.06 + 0.5 spin,  each rate clamped to +-1.5 Hz.
Line weight: the Tenney height T = log2(prod n_i) (major triad 6.9, minor 10.8) widens the glow by (1 + 0.08 T),
thins the core by 1 / (1 + 0.04 T) and moves the colour along the palette by 0.05 T, and the brightness per unit
length falls as the wire gets longer (a dense cage does not white out).

How it is drawn (three kernels a frame, no host round trips)
-------------------------------------------------------------
  1. ``pp_fold``: the phosphor.  Two float RGB buffers, the core at ``render_scale`` (default 0.5: 1080p at 4K) and
     a soft halo at half of that: P <- P exp(-dt / persistence).  A frame's deposit is weighted by
     (1 - exp(-dt / persistence)) x (persistence / 0.2)^0.1, so a still figure settles to the same brightness at
     any frame rate and almost any persistence (persistence is the trail, not the exposure), and dt = 0 changes
     nothing.  (``pp_smooth``, the real trace's low-pass, is a fourth tiny kernel over the 1024-sample wave.)
  2. ``pp_splat``: one thread per (segment, sub-splat) of the two ideal figures (the incoming chord and the
     outgoing one, 1024-4096 samples of s each by complexity) and the real trace (8 tau + 1 samples): the segment
     is a Catmull-Rom curve through its neighbours, cut into sub-splats ~0.9 sigma apart (at most 32), each an
     energy-normalised gaussian (atomicAdd) into the core and, sparser, the halo.  Energy follows segment length
     (an even line), nearer parts are brighter (depth cue).  The ideal is thin and coloured by the palette along s;
     the real trace thick and warm (amber, a quarter palette).  A palette's dark stretches give a dimmer stretch of
     wire (palette_period sets how many times the colour cycles along the closed path).
  3. ``pp_comp`` (output size): bilinear core + halo; soft saturation (1 - e^-m) on the brightest channel, which
     keeps the palette's hue, and the hottest crossings whitened; a dark underlay where the light falls, stronger
     on bright art (light on a white sky would not read); a slight refraction of the scene by the halo's gradient
     (two extra taps); then a screen blend at amount (2 - amount).  Unlit pixels copy the scene.

Placement: 0 = the frame's centre; 1 = across the frame by the bass's register (low left), or, when the room's
screens share one picture (``sound.segment``, south on the left), across both TVs (eased, so it glides).

Idle: no notes and a quiet room (the loudest FFT band under about -65 dBFS): nothing is deposited, the afterglow
fades, and after 6 x persistence the buffers are cleared and the scene passes through untouched (a copy).  pulse:
a deposit-brightness kick (an amplitude, never a position).  amount 0: out = src exactly.  sound None, no notes, no
fast feed: a pass-through.

Cost (P4000, 4K): splats ~0.1-0.4 ms (by the figure's length), fold ~0.35 ms, composite ~0.6 ms.
"""
from __future__ import annotations

import math
from fractions import Fraction

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import flag, num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size
from stream_service.plugins._lib import tonal as T

WAVE_N = 1024
NS_MAX = 4096                 # ideal samples per figure, at most
NR_MAX = 900                  # real-trace samples, at most
S_MAX = 32                    # sub-splats per segment, at most
N_CAP = 48                    # largest integer in a figure's ratio vector
OCT_CAP = 1                   # octaves above the bass kept in a ratio (beyond: folded)
FLOOR = 0.03                  # a voice's LEVEL floor
BASS_FLOOR = 0.04             # the bass's (the lowest note sounding above it)
HOLD_S = 0.05                 # a new voice set must hold this long (a fresh start takes it at once)
STICKY = 1.25                 # the current set's notes, x this when ranking
IN_TAU, OUT_TAU = 0.08, 0.14  # a new figure blooms in / the old one fades out
RATE_CAP_HZ = 1.5             # precession, at most
NP = 100
JUST = (Fraction(1), Fraction(16, 15), Fraction(9, 8), Fraction(6, 5), Fraction(5, 4), Fraction(4, 3),
        Fraction(45, 32), Fraction(3, 2), Fraction(8, 5), Fraction(5, 3), Fraction(9, 5), Fraction(15, 8))
SEPT = Fraction(7, 4)
_E = np.geomspace(40.0, 16000.0, 49)
BAND_HZ = np.sqrt(_E[:-1] * _E[1:])  # the fast feed's 48 FFT band centres
AMBER = (1.0, 0.56, 0.24)

# P layout (floats)
#  0..14 M (3x5, view x 5D) | 15 cx 16 cy (core px) | 17 R (px per world unit) | 18 camera distance
#  20..31 palette | 32 colour index base | 33 palette period
#  figure slots at 40 and 60, the real trace at 80 (20 floats each):
#   ideal: 0..3 freq (s-units) | 4..7 amplitude | 8..11 phase / 2 pi | 12 N samples | 13 core sigma (px)
#          14 halo sigma (halo px) | 15 core energy per px | 16 halo energy per halo px | 17 colour offset
#          18 amplitude scale | 19 warm (0)
#   real:  0 n0 | 1 tau | 2 count | 13..18 as above | 19 warm (1)
_SRC = HELPERS + PALETTE_CUDA + r"""
#define S_MAX %(s)d
#define WN %(wn)d
#define TWO_PI 6.28318531f

// the palette, lifted part of the way to full value (light takes its hue and most of its shade: a dark
// palette region reads as a dimmer stretch of wire, never as nothing)
__device__ __forceinline__ float3 pp_col(float t, const float* __restrict__ P) {
    const float3 c = pal(t, P);
    const float m = fmaxf(fmaxf(c.x, c.y), fmaxf(c.z, 0.f));
    const float s = 0.6f + 0.4f / fmaxf(m, 0.3f);
    return make_float3(fminf(c.x * s + 0.08f, 1.f), fminf(c.y * s + 0.08f, 1.f), fminf(c.z * s + 0.08f, 1.f));
}

// sample i (any float, s = 2 pi i / N) of an ideal figure -> R^5
__device__ __forceinline__ void pp_ideal(const float* __restrict__ S, float i, float v[5]) {
    const float u = i / S[12];
    float t = S[0] * u;
    t -= rintf(t);
    float sn, cs;
    __sincosf(TWO_PI * t, &sn, &cs);
    v[0] = S[4] * sn;
    v[1] = S[4] * cs;
    #pragma unroll
    for (int k = 1; k < 4; ++k) {
        float q = S[k] * u + S[8 + k];
        q -= rintf(q);
        v[k + 1] = S[4 + k] * __sinf(TWO_PI * q);
    }
}

// sample i of the real trace (Takens: w[n], w[n + tau], w[n + 2 tau]) -> R^5, turned so a pure tone is a circle
__device__ __forceinline__ void pp_real(const float* __restrict__ wave, const float* __restrict__ S, int i,
                                        float v[5]) {
    i = min(max(i, 0), (int)S[2] - 1);
    const int n = (int)S[0] + i, tau = (int)S[1];
    const float a = wave[min(max(n, 0), WN - 1)];
    const float b = wave[min(max(n + tau, 0), WN - 1)];
    const float c = wave[min(max(n + 2 * tau, 0), WN - 1)];
    v[0] = 0.5f * (a - c);
    v[1] = b;
    v[2] = 0.5f * (a + c);
    v[3] = 0.f;
    v[4] = 0.f;
}

// R^5 -> core px (x, y) and view depth z (toward the camera +)
__device__ __forceinline__ float3 pp_proj(const float* __restrict__ P, const float v[5], const float g) {
    float x = 0.f, y = 0.f, z = 0.f;
    #pragma unroll
    for (int k = 0; k < 5; ++k) { x += P[k] * v[k]; y += P[5 + k] * v[k]; z += P[10 + k] * v[k]; }
    x *= g; y *= g; z *= g;
    const float D = P[18];
    const float sc = D / fmaxf(D - z, 0.4f * D);
    return make_float3(P[15] + x * sc * P[17], P[16] - y * sc * P[17], z);
}

__device__ __forceinline__ float2 pp_cr(const float3* q, float t) {   // uniform Catmull-Rom through q1..q2
    const float t2 = t * t, t3 = t2 * t;
    const float a = -0.5f * t3 + t2 - 0.5f * t, b = 1.5f * t3 - 2.5f * t2 + 1.f;
    const float c = -1.5f * t3 + 2.f * t2 + 0.5f * t, d = 0.5f * t3 - 0.5f * t2;
    return make_float2(a * q[0].x + b * q[1].x + c * q[2].x + d * q[3].x,
                       a * q[0].y + b * q[1].y + c * q[2].y + d * q[3].y);
}

// energy-normalised gaussian splat of colour c x e into an RGB(A) float plane (<= 10 x 10 taps)
__device__ void pp_put(float4* __restrict__ buf, const int w, const int h, const float x, const float y,
                       const float sig, const float rmax, const float e, const float3 c) {
    if (!(e > 0.f) || !(fabsf(x) < 1e6f) || !(fabsf(y) < 1e6f)) return;     // also NaN / inf
    const float fx = x - 0.5f, fy = y - 0.5f;
    const float rr = fminf(2.3f * sig, rmax);
    const int ux0 = (int)ceilf(fx - rr), uy0 = (int)ceilf(fy - rr);
    const int nx = min((int)floorf(fx + rr) - ux0 + 1, 10), ny = min((int)floorf(fy + rr) - uy0 + 1, 10);
    if (nx <= 0 || ny <= 0 || ux0 + nx <= 0 || uy0 + ny <= 0 || ux0 >= w || uy0 >= h) return;
    const float k = -0.5f / (sig * sig);
    float wx[10], wy[10], sx = 0.f, sy = 0.f;
    #pragma unroll
    for (int i = 0; i < 10; ++i) {
        const float dx = ux0 + i - fx, dy = uy0 + i - fy;
        const float a = i < nx ? __expf(k * dx * dx) : 0.f;
        const float b = i < ny ? __expf(k * dy * dy) : 0.f;
        wx[i] = a; wy[i] = b; sx += a; sy += b;
    }
    const float s = e / fmaxf(sx * sy, 1e-12f);
    for (int j = 0; j < ny; ++j) {
        const int yy = uy0 + j;
        if (yy < 0 || yy >= h) continue;
        const float wyj = wy[j] * s;
        for (int i = 0; i < nx; ++i) {
            const int xx = ux0 + i;
            if (xx < 0 || xx >= w) continue;
            const float q = wx[i] * wyj;
            float* p = (float*)(buf + (yy * w + xx));
            atomicAdd(p, c.x * q);
            atomicAdd(p + 1, c.y * q);
            atomicAdd(p + 2, c.z * q);
        }
    }
}

// the wave, low-passed (a triangle of half-width L): the bass and its first few partials stay, the treble's
// scribble goes, so the delay embedding reads as loops
extern "C" __global__ void pp_smooth(const float* __restrict__ wave, float* __restrict__ ws, const int L) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= WN) return;
    float acc = 0.f, wsum = 0.f;
    for (int k = -L; k <= L; ++k) {                    // bounded: L <= 48
        const int j = i + k;
        if (j < 0 || j >= WN) continue;
        const float wt = (float)(L + 1 - abs(k));
        const float v = wave[j];
        acc += wt * (fabsf(v) < 1e3f ? v : 0.f);
        wsum += wt;
    }
    ws[i] = acc / fmaxf(wsum, 1.f);
}

extern "C" __global__ void pp_fold(float4* __restrict__ core, const int w, const int h,
                                   float4* __restrict__ halo, const int gw, const int gh, const float k) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float4 a = core[y * w + x];
    a.x = a.x * k > 1e-5f ? a.x * k : 0.f;
    a.y = a.y * k > 1e-5f ? a.y * k : 0.f;
    a.z = a.z * k > 1e-5f ? a.z * k : 0.f;
    core[y * w + x] = a;
    if (x < gw && y < gh) {
        float4 g = halo[y * gw + x];
        g.x = g.x * k > 1e-5f ? g.x * k : 0.f;
        g.y = g.y * k > 1e-5f ? g.y * k : 0.f;
        g.z = g.z * k > 1e-5f ? g.z * k : 0.f;
        halo[y * gw + x] = g;
    }
}

// blockIdx.y: 0, 1 the ideal figures (incoming, outgoing), 2 the real trace.  x: segment x S_MAX + sub-splat.
extern "C" __global__ void pp_splat(const float* __restrict__ P, const float* __restrict__ wave,
                                    float4* __restrict__ core, const int w, const int h,
                                    float4* __restrict__ halo, const int gw, const int gh) {
    const int which = blockIdx.y;
    const float* S = P + 40 + 20 * which;
    if (!(S[15] > 0.f)) return;
    const int gid = blockIdx.x * blockDim.x + threadIdx.x;
    const int seg = gid / S_MAX, j = gid - seg * S_MAX;
    const bool real = which == 2;
    const int nseg = real ? (int)S[2] - 1 : (int)S[12];
    if (seg >= nseg) return;
    float3 q[4];
    float v[5];
    #pragma unroll
    for (int k = 0; k < 4; ++k) {
        if (real) pp_real(wave, S, seg - 1 + k, v);
        else pp_ideal(S, (float)(seg - 1 + k), v);
        q[k] = pp_proj(P, v, S[18]);
    }
    const float dx = q[2].x - q[1].x, dy = q[2].y - q[1].y;
    const float len = sqrtf(dx * dx + dy * dy);
    if (!(len < 1e5f)) return;
    const float sig = S[13];
    const int n = min(max((int)ceilf(len / (0.9f * sig)), 1), S_MAX);
    if (j >= n) return;
    const float tt = (seg + (j + 0.5f) / n) / (float)nseg;
    const float zm = 0.5f * (q[1].z + q[2].z);
    float wgt = fminf(fmaxf(1.f + 0.3f * zm, 0.5f), 1.5f);              // nearer is brighter
    float3 c;
    if (real) {
        wgt *= smooth(0.f, 0.06f, tt) * smooth(0.f, 0.06f, 1.f - tt);  // the open trace's ends fade in
        const float3 pc = pp_col(P[32] + S[17], P + 20);
        c = make_float3(AMBER_R * 0.75f + 0.25f * pc.x, AMBER_G * 0.75f + 0.25f * pc.y, AMBER_B * 0.75f + 0.25f * pc.z);
    } else {
        const float3 pc = pp_col(P[32] + S[17] + P[33] * tt, P + 20);
        c = make_float3(0.88f * pc.x + 0.12f, 0.88f * pc.y + 0.12f, 0.88f * pc.z + 0.12f);
    }
    const float2 p = pp_cr(q, (j + 0.5f) / n);
    pp_put(core, w, h, p.x, p.y, sig, 3.5f, S[15] * wgt * len / n, c);
    const float hs = (float)gw / w;
    const int nG = min(max((int)ceilf(len * hs / S[14]), 1), n);
    if (j < nG) {
        const float2 pg = pp_cr(q, (j + 0.5f) / nG);
        pp_put(halo, gw, gh, pg.x * hs, pg.y * hs, S[14], 4.5f, S[16] * wgt * len * hs / nG, c);
    }
}

__device__ __forceinline__ float4 pp_bil(const float4* __restrict__ b, const int w, const int h, float fx, float fy) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const float4 a = b[ya * w + xa], c = b[ya * w + xb], d = b[yb * w + xa], e = b[yb * w + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * c.x) + ty * (u * d.x + tx * e.x),
                       v * (u * a.y + tx * c.y) + ty * (u * d.y + tx * e.y),
                       v * (u * a.z + tx * c.z) + ty * (u * d.z + tx * e.z), 0.f);
}

__device__ __forceinline__ float pp_lum(const float4 a) { return 0.3f * a.x + 0.55f * a.y + 0.15f * a.z; }

extern "C" __global__ void pp_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                                   const float4* __restrict__ core, const int w, const int h,
                                   const float4* __restrict__ halo, const int gw, const int gh,
                                   const float alpha, const float hgain, const float under, const float refr) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float sxh = (float)gw / W, syh = (float)gh / H;
    const float4 c = pp_bil(core, w, h, px * w / W, py * h / H);
    const float4 g = pp_bil(halo, gw, gh, px * sxh, py * syh);
    float Lr = c.x + hgain * g.x, Lg = c.y + hgain * g.y, Lb = c.z + hgain * g.z;
    const float m = fmaxf(fmaxf(Lr, Lg), Lb);
    if (!(m >= 2e-4f && m < 1e30f)) { out[y * W + x] = src[y * W + x]; return; }   // unlit (or non-finite)
    const float gl = pp_lum(g);
    float4 s;
    if (refr > 0.f && gl > 0.003f) {                       // a slight lens: the scene bent by the glow's slope
        const float gx = pp_lum(pp_bil(halo, gw, gh, px * sxh + 1.f, py * syh)) - gl;
        const float gy = pp_lum(pp_bil(halo, gw, gh, px * sxh, py * syh + 1.f)) - gl;
        s = bilin(src, px + refr * gx, py + refr * gy, W, H);
    } else {
        s = ld(src, x, y, W, H);
    }
    // soft saturation on the brightest channel (keeps the palette's hue and shade), then the hot core whitens
    const float sc = alpha * (1.f - __expf(-m)) / m;
    const float wh = 0.75f * smooth(1.4f, 4.5f, m);
    const float lr = sc * (Lr + (m - Lr) * wh), lg = sc * (Lg + (m - Lg) * wh), lb = sc * (Lb + (m - Lb) * wh);
    // a dark underlay where the light falls, strongest on bright art (light on a white sky would not read)
    const float sh = 1.f - under * (0.25f + 0.75f * smooth(0.35f, 0.9f, luma(s))) * (1.f - __expf(-2.5f * (gl + 0.5f * m)));
    const float r = s.x * sh, gg = s.y * sh, bb = s.z * sh;
    out[y * W + x] = pack(make_float4(r + lr * (255.f - r), gg + lg * (255.f - gg), bb + lb * (255.f - bb), 255.f));
}
""".replace("AMBER_R", "%.3ff" % AMBER[0]).replace("AMBER_G", "%.3ff" % AMBER[1]).replace(
    "AMBER_B", "%.3ff" % AMBER[2]) % {"s": S_MAX, "wn": WAVE_N}


def _finite(v, default: float = 0.0) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _ease(cur: float, target: float, dt: float, tau: float) -> float:
    return cur + (target - cur) * T.ema_alpha(dt, tau)


def _givens(i: int, j: int, a: float) -> np.ndarray:
    r = np.eye(5)
    c, s = math.cos(a), math.sin(a)
    r[i, i] = c
    r[j, j] = c
    r[i, j] = -s
    r[j, i] = s
    return r


def figure(pitches: tuple, septimal: bool) -> dict:
    """The just-intonation figure of a voice set (MIDI pitches, low to high; the first is the bass).

    Returns {"bass", "voices": [(pitch, freq_in_s_units, kind)], "n": [ints], "tenney"}: kind 0 the bass (its
    circle, freq n_b), 1 a voice at its integer n_i, 2 a fuzz voice at n_b x its tempered ratio (the figure is open).
    """
    pb = int(pitches[0])
    ups, seen = [], {Fraction(1)}
    for p in pitches[1:]:
        d = int(p) - pb
        ic, o = d % 12, min(d // 12, OCT_CAP)
        r = (SEPT if (septimal and ic == 10) else JUST[ic]) * (2 ** o)
        if r in seen:
            continue
        seen.add(r)
        ups.append((int(p), r, ic + 12 * o))
    fuzz = []
    while True:
        rs = [Fraction(1)] + [u[1] for u in ups]
        L = 1
        for r in rs:
            L = L * r.denominator // math.gcd(L, r.denominator)
        n = [int(r * L) for r in rs]
        g = 0
        for v in n:
            g = math.gcd(g, v)
        n = [v // g for v in n]
        if max(n) <= N_CAP or not ups:
            break
        k = max(range(len(ups)), key=lambda i: (ups[i][1].denominator, n[i + 1]))
        fuzz.append(ups.pop(k))
    nb = n[0]
    voices = [(pb, float(nb), 0)] + [(u[0], float(v), 1) for u, v in zip(ups, n[1:])]
    voices += [(u[0], nb * 2.0 ** (u[2] / 12.0), 2) for u in fuzz]
    voices = voices[:4]
    return {"bass": pb, "voices": voices, "n": n, "tenney": math.log2(max(1, math.prod(n))),
            "semis": {u[0]: u[2] for u in ups + fuzz}}


class PhasePortrait(Effect):
    name = "phase_portrait"
    stage = "overlay"
    label = "Phase portrait"
    description = ("A glowing wire figure over any scene draws the chord as its own just-intonation Lissajous figure "
                   "(a circle for one note, a 2:3 knot for a fifth, a 4:5:6 cage for a triad), beside the room's "
                   "real waveform drawn in phase space with a delay set by the MIDI bass.")
    listens = ("notes", "scope", "fft")
    params = {
        **standard_effect_params(),
        "size": num(0.2, 0.8, 0.6, "Size", tier="regional", basic=True),
        "reality": num(0.0, 1.0, 0.2, "Real sound (ideal · real)", tier="regional", basic=True),
        "persistence": num(0.05, 0.6, 0.2, "Persistence", unit="s", tier="regional"),
        "spin": num(0.0, 1.0, 0.35, "Spin", tier="regional"),
        "placement": num(0.0, 1.0, 0.0, "Placement (centre · by the bass)", tier="regional"),
        "septimal": flag(False, "Septimal sevenths (7/4)"),
        **palette_params("rainbow", period=0.8, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    # ------------------------------------------------------------------------------------------------ setup
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._kf = mod.get_function("pp_fold")
        self._ks = mod.get_function("pp_splat")
        self._kc = mod.get_function("pp_comp")
        self._kw = mod.get_function("pp_smooth")
        self.wave_s = cp.zeros(WAVE_N, cp.float32)
        self.A = ctx.width / ctx.height
        self.zeros = cp.zeros(WAVE_N, cp.float32)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        rng = np.random.default_rng(int(_finite(params.get("seed", 1), 1)) & 0x7FFFFFFF)
        self.rot = rng.uniform(0.0, 2.0 * math.pi, 7)          # sway phases: 4 in R^5, yaw, pitch, roll
        self.phase = np.zeros(128)                             # each pitch's precession phase (radians)
        self._lv = np.zeros(128, np.float32)
        self._cache: dict = {}
        self.cur: tuple = ()
        self.pend: tuple = ()
        self.pend_s = 0.0
        self.slots = [None, None]                              # [incoming, outgoing] figures
        self.wts = [0.0, 0.0]
        self.peak = 0.0
        self.xc = None
        self.drift = 0.0
        self.clock = 0.0
        self.real_g = 0.0
        self.rms_e = 0.0
        self.quiet_s = 1e9
        self.t_last = None
        self.clean = True
        self.bass_hz = 0.0
        self._rs = None
        self._alloc(_finite(params.get("render_scale", 0.5), 0.5))

    def _alloc(self, rs: float) -> None:
        rs = min(max(rs, 0.25), 1.0)
        if self._rs is not None and abs(rs - self._rs) < 1e-6:
            return
        cp = self.ctx.cp
        self.w, self.h = internal_size(self.ctx, rs)
        self.gw, self.gh = max(8, self.w // 2), max(8, self.h // 2)
        self.core = cp.zeros((self.h, self.w, 4), cp.float32)
        self.halo = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.clean = True
        self._rs = rs

    def _clear(self) -> None:
        if not self.clean:
            self.core.fill(0)
            self.halo.fill(0)
            self.clean = True

    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES.get(name, PALETTES["ice"]) if rows is None else rows, np.float32).reshape(-1)[:12]

    def _figure(self, pitches: tuple, sept: bool) -> dict:
        key = (pitches, sept)
        f = self._cache.get(key)
        if f is None:
            if len(self._cache) > 512:
                self._cache.clear()
            f = self._cache[key] = figure(pitches, sept)
        return f

    # ------------------------------------------------------------------------------------------------ voices
    def _voices(self, lv: np.ndarray, dt: float, sept: bool) -> None:
        """Pick this frame's voice set (with hysteresis), and swap the figure slots when it changes."""
        bass = T.bass_pitch(lv, floor=BASS_FLOOR)            # the bass is the lowest sounding note, loud or not
        if bass >= 0:
            on = T.loudest(lv, 7, floor=FLOOR)
            on = on[on > bass]
            if on.size > 3:
                sc = lv[on] * np.where(np.isin(on, self.cur), STICKY, 1.0)
                on = np.sort(on[np.argsort(sc)[-3:]])
            cand = (int(bass),) + tuple(int(p) for p in on)
        else:
            cand = ()
        if cand == self.cur:
            self.pend, self.pend_s = (), 0.0
            return
        if cand != self.pend:
            self.pend, self.pend_s = cand, 0.0
        else:
            self.pend_s += dt
        if self.cur and cand and self.pend_s < HOLD_S:
            return
        # swap: the incoming figure becomes the outgoing one
        self.slots[1], self.wts[1] = self.slots[0], self.wts[0]
        self.slots[0] = self._figure(cand, sept) if cand else None
        self.wts[0] = 0.0
        self.cur, self.pend, self.pend_s = cand, (), 0.0

    def _pack_slot(self, k: int, fig, wt: float, lv, p: dict, base: dict) -> float:
        """Pack figure slot k; returns its deposit weight (0 = nothing drawn)."""
        h = self.host
        o = 40 + 20 * k
        h[o:o + 20] = 0.0
        if fig is None or wt <= 1e-3:
            return 0.0
        amps = np.zeros(4)
        for i, (pitch, fq, kind) in enumerate(fig["voices"]):
            a = math.sqrt(max(float(lv[pitch]), 0.0))
            amps[i] = a * (0.6 if kind == 2 else 1.0)
            h[o + i] = fq
            h[o + 4 + i] = amps[i]
            h[o + 8 + i] = math.fmod(self.phase[pitch], 2.0 * math.pi) / (2.0 * math.pi) if i else 0.0
        if amps.max() < 0.05:
            return 0.0
        g = base["g"]
        fr = np.array([v[1] for v in fig["voices"]] + [0.0] * (4 - len(fig["voices"])))
        speed = math.sqrt(amps[0] ** 2 * fr[0] ** 2 + 0.5 * float(np.sum((amps[1:] * fr[1:]) ** 2))) * g
        dens = min(max((1.0 / max(0.85 * speed, 1e-3)) ** 0.6, 0.2), 1.0)
        nmax = float(max(fr.max(), 1.0))
        N = 1024 if nmax <= 4.5 else (2048 if nmax <= 16.5 else NS_MAX)
        Tn = fig["tenney"]
        loud = T.smoothstep(0.0, 0.5, math.sqrt(float(np.sum(amps ** 2))))
        sig_c = max(1.0 / (1.0 + 0.04 * Tn) * base["hs"], 0.6)
        sig_h = min(1.7 * (1.0 + 0.08 * Tn) * base["hs"], 2.4)
        e = wt * dens * (0.6 + 0.4 * loud) * base["dep"]
        h[o + 12] = N
        h[o + 13] = sig_c
        h[o + 14] = sig_h
        h[o + 15] = 2.0 * math.sqrt(2.0 * math.pi) * sig_c * e * base["w_ideal"]
        h[o + 16] = 0.7 * math.sqrt(2.0 * math.pi) * sig_h * e * base["w_ideal"] * base["glow"]
        h[o + 17] = 0.05 * Tn
        h[o + 18] = g
        return N if h[o + 15] > 0.0 else 0.0

    # ------------------------------------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        amount = min(max(_finite(p["amount"]), 0.0), 1.0)
        pulse = min(max(_finite(p["pulse"]), 0.0), 1.0)
        dtc = min(max(_finite(dt), 0.0), 0.5)
        tl = _finite(t_local)
        el = dtc
        if self.t_last is not None:
            gap = tl - self.t_last
            if gap < -1e-3 or gap > 0.5:
                self._clear()
            elif gap > dtc:
                el = gap
        self.t_last = tl
        f = lambda k, d: _finite(p.get(k, d), d)            # noqa: E731
        spin = min(max(f("spin", 0.35), 0.0), 1.0)
        tau = min(max(f("persistence", 0.2), 0.03), 2.0)
        size = min(max(f("size", 0.6), 0.05), 1.2)
        reality = min(max(f("reality", 0.2), 0.0), 1.0)
        place = min(max(f("placement", 0.0), 0.0), 1.0)
        sept = bool(p.get("septimal", False))
        self.clock = math.fmod(self.clock + dtc, 3600.0)
        self.drift = math.fmod(self.drift + f("drift_speed", 0.02) * dtc, 10.0)
        sm = 0.15 + 4.0 * spin
        self.rot[:6] += np.array([0.21, 0.143, 0.097, 0.071, 0.083, 0.059]) * sm * dtc
        self.rot[6] = math.fmod(self.rot[6] + (0.03 + 0.6 * spin) * dtc, 2.0 * math.pi)     # the slow roll

        # ---- what is sounding
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        notes = getattr(fr, "notes", None) if fr is not None else None
        lv = self._lv
        if notes is not None and getattr(notes, "shape", None) == (128, 4):
            lv[:] = notes[:, 0]
            np.nan_to_num(lv, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
        else:
            lv.fill(0.0)
        self._voices(lv, dtc, sept)
        k_in, k_out = T.ema_alpha(dtc, IN_TAU), T.ema_alpha(dtc, OUT_TAU)
        self.wts[0] += (1.0 - self.wts[0]) * k_in
        self.wts[1] -= self.wts[1] * k_out
        if self.wts[1] < 1e-3:
            self.slots[1], self.wts[1] = None, 0.0

        # precession: each upper voice's phase rolls at its true equal-temperament mistuning (x k)
        ks = 0.06 + 0.5 * spin
        done = set()
        for fig in self.slots:
            if fig is None:
                continue
            nb = fig["voices"][0][1]
            fb = T.midi_hz(fig["bass"])
            for pitch, fq, kind in fig["voices"][1:]:
                if pitch in done:
                    continue
                done.add(pitch)
                semis = fig["semis"].get(pitch, 0)
                f_et = fb * 2.0 ** (semis / 12.0)
                rate = (f_et - fb * fq / nb) if kind == 1 else 0.35
                rate = min(max(ks * rate, -RATE_CAP_HZ), RATE_CAP_HZ)
                self.phase[pitch] = math.fmod(self.phase[pitch] + 2.0 * math.pi * rate * dtc, 2.0 * math.pi)

        # the amplitude normaliser: a fast-up, slow-down peak of the incoming figure's |A|
        a0 = 0.0
        if self.slots[0] is not None:
            a0 = math.sqrt(sum(float(lv[v[0]]) for v in self.slots[0]["voices"]))
        if a0 > self.peak:
            self.peak = _ease(self.peak, a0, dtc, 0.04) if self.peak > 0.0 else a0
        else:
            self.peak = _ease(self.peak, a0, dtc, 6.0)
        g = 1.0 / max(self.peak, 0.35) * (1.0 + 0.025 * math.sin(2.0 * math.pi * 0.17 * self.clock))
        if self.slots[0] is not None:
            self.bass_hz = T.midi_hz(self.slots[0]["bass"])

        # ---- the room's real sound
        wave = getattr(snd, "wave_gpu", None) if snd is not None else None
        fftmax, trig, rms = 0.0, 0, 0.0
        if wave is not None:
            try:
                hf = snd.fft
                fftmax = _finite(float(np.max(hf)))
                trig = int(snd.wave_trigger)
                hw = snd.wave
                rms = _finite(math.sqrt(max(float(np.dot(hw, hw)), 0.0) / max(1, hw.size)))
            except Exception:
                fftmax, trig, rms = 0.0, 0, 0.0
        else:
            wave = self.zeros
        room = T.smoothstep(0.22, 0.45, fftmax)
        self.real_g = _ease(self.real_g, room, dtc, 0.05 if room > self.real_g else 0.3)
        self.rms_e = _ease(self.rms_e, rms, dtc, 0.08 if rms > self.rms_e else 0.8) if self.rms_e > 0 else rms
        w_ideal = min(1.0, 2.0 * (1.0 - reality))
        # a near-silent wave would pile every point onto the centre (a hot dot): the trace needs some size
        w_real = min(1.0, 2.0 * reality) * self.real_g * T.smoothstep(0.02, 0.08, self.rms_e)
        fb_hz = self.bass_hz if self.cur else 0.0
        if fb_hz <= 0.0 and fftmax > 0.0:
            try:
                fb_hz = float(BAND_HZ[int(np.argmax(snd.fft))])
            except Exception:
                fb_hz = 0.0
        if fb_hz <= 0.0:
            fb_hz = 160.0
        while fb_hz < 80.0:
            fb_hz *= 2.0
        while fb_hz >= 320.0:
            fb_hz *= 0.5
        rtau = int(min(max(round(48000.0 / (4.0 * fb_hz)), 37), 150))
        rcount = min(NR_MAX, 8 * rtau + 1, WAVE_N - 2 * rtau - 1)   # two periods: one clean loop
        n0 = min(max(trig, 0), WAVE_N - 1 - 2 * rtau - rcount)

        # ---- the frame: deposit weights, placement, M(t), camera
        if amount <= 1e-4:
            self._clear()
            cp.copyto(out, src)
            return
        self._alloc(_finite(self.easer.target.get("render_scale", self._rs), self._rs))
        k = math.exp(-min(el, 0.5) / tau)
        dep = (1.0 - k) * (tau / 0.2) ** 0.1 * (1.0 + 0.9 * pulse)
        hs = self.h / 1080.0
        base = {"g": g, "dep": dep, "hs": hs, "w_ideal": w_ideal, "glow": 1.0 + 0.6 * pulse}
        hst = self.host
        lit = False
        nmax = 0
        for s in range(2):
            n = self._pack_slot(s, self.slots[s], self.wts[s], lv, p, base)
            if n > 0 and w_ideal > 0.0:
                lit = True
                nmax = max(nmax, int(n))
        o = 80
        hst[o:o + 20] = 0.0
        if w_real > 1e-3:
            lit = True
            e = w_real * dep
            sig_c = max(1.5 * hs, 0.7)
            sig_h = min(2.0 * hs, 2.6)
            hst[o + 0] = n0
            hst[o + 1] = rtau
            hst[o + 2] = rcount
            hst[o + 12] = rcount
            hst[o + 13] = sig_c
            hst[o + 14] = sig_h
            hst[o + 15] = 1.6 * math.sqrt(2.0 * math.pi) * sig_c * e
            hst[o + 16] = 0.4 * math.sqrt(2.0 * math.pi) * sig_h * e * base["glow"]
            hst[o + 17] = 0.5
            hst[o + 18] = min(max(0.75 / max(self.rms_e, 1e-3), 0.8), 1.8)
            hst[o + 19] = 1.0
        self.quiet_s = 0.0 if lit else self.quiet_s + dtc
        if self.quiet_s > max(6.0 * tau, 0.4):
            self._clear()
            cp.copyto(out, src)
            return

        A = self.A
        seg = getattr(snd, "segment", None) if snd is not None else None
        try:
            sk, sn = (int(seg[0]), int(seg[1])) if seg is not None else (0, 1)
        except Exception:
            sk, sn = 0, 1
        if not (sn >= 1 and 0 <= sk < sn):
            sk, sn = 0, 1
        half = max(sn * A - 1.3 * size, 0.0)
        u = 0.5
        if self.slots[0] is not None:
            u = min(max((self.slots[0]["bass"] - 28) / 60.0, 0.0), 1.0)
        xg = (u - 0.5) * 2.0 * half * place
        xt = xg - (-sn * A + (2 * sk + 1) * A)
        if sn == 1 and place <= 0.0:
            xt = 0.0
        if self.xc is None or not lit and self.clean:
            self.xc = xt
        else:
            self.xc = _ease(self.xc, xt, dtc, 0.5)

        r = self.rot
        # the bass's circle stays mostly in the picture plane; the upper voices lean into it from depth
        M5 = (_givens(0, 3, 0.36 + 0.24 * math.sin(r[0])) @ _givens(1, 4, 0.3 + 0.24 * math.sin(r[1]))
              @ _givens(0, 2, 0.32 + 0.24 * math.sin(r[2])) @ _givens(2, 4, 0.4 + 0.6 * math.sin(r[3])))[:3]
        yaw = 0.5 * math.sin(r[4])
        pitch = 0.3 + 0.3 * math.sin(r[5])
        roll = r[6]
        cy_, sy_ = math.cos(yaw), math.sin(yaw)
        cx_, sx_ = math.cos(pitch), math.sin(pitch)
        cz_, sz_ = math.cos(roll), math.sin(roll)
        Ry = np.array([[cy_, 0, sy_], [0, 1, 0], [-sy_, 0, cy_]])
        Rx = np.array([[1, 0, 0], [0, cx_, -sx_], [0, sx_, cx_]])
        Rz = np.array([[cz_, -sz_, 0], [sz_, cz_, 0], [0, 0, 1]])
        hst[0:15] = (Rx @ Ry @ Rz @ M5).reshape(-1)
        hst[15] = 0.5 * self.w + self.xc * 0.5 * self.h
        hst[16] = 0.5 * self.h
        hst[17] = size * 0.62 * self.h
        hst[18] = 4.5
        hst[20:32] = self._palette(p["palette"])
        hst[32] = self.drift
        hst[33] = f("palette_period", 0.6)
        if not np.all(np.isfinite(hst)):
            np.nan_to_num(hst, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.P.set(hst)

        launch_2d(self._kf, self.w, self.h, (self.core, np.int32(self.w), np.int32(self.h), self.halo,
                                             np.int32(self.gw), np.int32(self.gh), np.float32(k)))
        nseg = max(nmax, rcount if w_real > 1e-3 else 0)
        if w_real > 1e-3:
            L = int(min(max(round(rtau / 2), 1), 48))
            self._kw(((WAVE_N + 255) // 256,), (256,), (wave, self.wave_s, np.int32(L)))
        wave = self.wave_s
        if nseg > 0 and dep > 0.0:
            bx = (nseg * S_MAX + 255) // 256
            self._ks((bx, 3, 1), (256, 1, 1), (self.P, wave, self.core, np.int32(self.w), np.int32(self.h),
                                               self.halo, np.int32(self.gw), np.int32(self.gh)))
        self.clean = False
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._kc, W, H, (src, out, np.int32(W), np.int32(H), self.core, np.int32(self.w), np.int32(self.h),
                                   self.halo, np.int32(self.gw), np.int32(self.gh), np.float32(amount * (2.0 - amount)),
                                   np.float32(1.0 + 1.2 * pulse), np.float32(0.6 * amount),
                                   np.float32(26.0 * amount * W / 3840.0)))

    def close(self) -> None:
        self.core = self.halo = self.P = self.zeros = self.wave_s = None
