"""Overtone beats: the overtones of the notes being played, drawn as fine threads over any scene.  Where two notes'
overtones coincide they fuse gold; where they nearly miss, the picture beneath wobbles at their real beat rate.

Source: Original (inspired by W. A. Sethares, "Tuning, Timbre, Spectrum, Scale" and his dissonance-curve notes,
        https://sethares.engr.wisc.edu/files/disscurve2.cpp (formulas only, no code taken); R. Plomp & W. J. M.
        Levelt, "Tonal consonance and critical bandwidth" (JASA 1965); Plomp 1962; H. Fletcher's stiff-string
        inharmonicity and the Railsback curve, https://en.wikipedia.org/wiki/Piano_acoustics)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
----------------------
A piano string does not make one frequency, it makes a whole series: the fundamental f, then 2f, 3f, 4f ... (the
overtones, or partials), each quieter than the last.  Every note the pianist plays (exact pitches from the MIDI)
becomes a comb of up to ten thin glowing threads, one per partial, laid out on a logarithmic frequency axis: bass
on the left, treble on the right (``layout`` 0), or as concentric rings from the centre outwards (``layout`` 1).
Because the axis is logarithmic the threads of one note crowd together to the right: 2f is an octave above f, but
10f is only a semitone and a bit above 9f.  Each note's threads take its pitch class's colour, round the circle of
fifths through the palette, so notes a fifth apart sit side by side in hue.

Why some intervals sound smooth and others rough (Helmholtz): two notes sound together smoothly when their
overtones land on top of each other.  An octave's partials coincide exactly; a fifth (3:2) lines up the 3rd
partial of the lower note with the 2nd of the upper; a major third (5:4) lines up the 5th with the 4th.  Where
two notes' partials (nearly) coincide the threads fuse into a gold bond.

The price of equal temperament: a piano is not tuned in pure ratios.  Its fifth is 2 cents narrow and its major
third 14 cents wide, so "coinciding" partials actually miss by a little, and two frequencies that nearly match
beat: their sum swells and fades at the difference frequency.  Middle C plus G: 3 x 261.63 = 784.9 Hz against
2 x 392.00 = 784.0 Hz, a slow 0.89 Hz beat.  Middle C plus E: 5 x 261.63 = 1308.1 Hz against 4 x 329.63 =
1318.5 Hz, a fast 10.4 Hz beat (both checked).  Every near-miss pair bends the scene under it (and its own
threads, like vibrating strings) back and forth at that beat rate, times ``slow`` (default 0.5, so a fifth
breathes at ~0.45 Hz; 1 is the true rate).  The wobble's strength is the pair's beat depth: the quieter partial's
amplitude x [exp(-(b / 12 Hz)^2) - exp(-(b / 0.5 Hz)^2)], so pairs that have fused (gold) or are far apart (rough,
not beating) barely move.  Beats faster than ~8 Hz after ``slow`` (a third at slow 1) become a fine fizzing grain
instead of a wobble (fast wobble would read as flicker).  The gold bonds breathe by at most +/-15 % at their own
beat, so nothing large ever flashes.  Each string's partials start in phase at its strike (phase = 2 pi slow f x
the note's age, then integrated as a rate), so two notes struck together start their beat together, as real
strings do, and a change of ``slow`` or ``stretch`` changes a rate, never jumps a phase.

Roughness (Plomp & Levelt, in Sethares' fitted form): two partials close enough to fall in one critical band but
too far apart to be heard as a beat sound rough.  For partials of amplitudes a_i, a_j and frequencies f_i, f_j:
    rho = min(a_i, a_j) [exp(-3.5 s b) - exp(-5.75 s b)],  b = |f_i - f_j|,  s = 0.24 / (0.0207 min(f) + 18.96)
The sum over all pairs, taken relative to the loudest note (a quiet cluster is still a cluster), is the chord's
roughness R, and R_hat = 1 - exp(-R / 0.6): a fifth ~.07, a third ~.2, C major ~.4, a cluster or dim7 ~.7, a
dense jazz voicing ~.9.  With ``focus`` up, dissonance literally defocuses the picture: the frame blurs (most at
the edges) and picks up a little colour fringing, by smoothstep(.08, .8, R_hat), eased in over 0.2 s and out over
0.9 s (a plain triad stays nearly sharp, a cluster goes soft).  Fusion, for the gold: phi = sqrt(a_i a_j) exp(-(b / 1.5 Hz)^2).

The partial model (display models, not measurements): f_n = n f0 sqrt(1 + B n^2), where f0 is the equal-tempered
pitch (A4 = 440 Hz) shifted by a Railsback-shaped stretch c(p) = 30 ((p - 64) / 44)^3 cents, and the stiffness
B(p) = max(1e-4, 2e-4 x 10^((p - 60) / 48)).  ``stretch`` scales both (0: ideal strings, exact harmonics, pure
equal-temperament beats; 1: a real piano's stretched, slightly sharp upper partials).  Partial amplitude a = LEVEL
x n^-gamma with gamma = 1.6 - 0.8 x velocity (a harder strike is brighter in its upper partials).  LEVEL includes
the piano's decay and the damper / sostenuto holds, so pedalled notes keep their combs.  The room's FFT (the fast
feed) is only a faint haze along the same axis: it trails the ear by ~0.25 s, so it never drives anything.

``span``: ``full`` shows 40 Hz-8 kHz on each screen; ``pair`` splits one axis over the two TVs side by side (south,
on the left: 27-600 Hz; north: 600 Hz-14 kHz).  Without a known screen (fxtest, previews) ``pair`` shows full.

How it is drawn
---------------
Host, per frame (numpy, measured 0.3-0.5 ms on this shared box at nice 19, over the 0.2 ms the design hoped
for): the up-to-12 loudest sounding notes (LEVEL > 0.02, faded in over
0.02-0.06 and bloomed in over 0.12 s after the strike: no flash; past 4 notes each thread dims by sqrt(4 / N), so a
dense pedalled texture stays a texture, not a white sheet) x 10 partials, from per-pitch frequency / axis
tables rebuilt only when ``stretch`` moves; every partial's phase as above.  Pairs come from the partials sorted by frequency (each against its next 24): different notes, |b| < 0.25 x their
mean frequency.  Up to 256 pairs (the strongest) and the partials go up in one ``.set`` (10 KB).  Bonds,
wobble and grain are scaled by 1 / max(loudest LEVEL, 0.08)^0.7, so a soft passage still shows its beats.  If the
host work averages over 0.45 ms it drops to 8 notes x 8 partials until it is back under 0.25 ms.
GPU: ``ob_strip`` builds a 2048-column strip along the axis, one thread per column: the threads' colour and white
core, the gold fusion and its beat phasor, the grain, the wobble phasor sum (beat depth x e^(i psi)) and the room's
FFT at that column's frequency.  ``ob_layer`` at ``render_scale`` (default 0.5: 1920 x 1080 for a 4K frame): each
pixel finds u on the axis (lines: x; rings: the radius; ``layout`` blends the two continuously), reads the wobble
phasor and turns it into a displacement of up to ~26 px (at 4K) x wobble, in bands ~200 px wide, with a slow wave
along the thread (Im(Z e^(i k y)), or whole lobes round a ring: overlapping pairs blend, no phase jumps;
tanh-clamped), then samples the threads, gold, grain and haze at the displaced place (so they wobble with the
picture, like vibrating strings) and stores the tonemapped glow plus the displacement.  ``ob_comp`` at full size:
the scene displaced along the axis direction (bilinear), the soft copy mixed in (focus), the glow screen-blended
on top; a pixel whose layer taps are all empty is copied.  ``focus``: four tiny kernels at 1/8 size make the soft
copy (8x8 box, a separable 7-tap Gaussian, then the radial red / blue fringe and the blur weight baked into
alpha); only while amount x focus x smoothstep(.08, .8, R_hat) > 0.01.
Cost (P4000, 4K, measured on a shared card): ~1.6 ms with the blur on (strip 0.06, soft copy 0.2, layer 0.3,
composite 1.0); fxtest --bench reports the render() wall time, host included.

amount 0, or nothing sounding (and nothing left to fade): ``cp.copyto`` (exactly the scene).  pulse: thread
brightness (+60 %), never a position.  dt = 0 / NaN: nothing integrates.  No picture history.
"""
from __future__ import annotations

import math
import time

import numpy as np

from stream_service import ensemble
from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

NPART = 10                     # partials per note
MAXN = 12                      # notes
MAXP = MAXN * NPART            # partials
KNB = 24                       # each sorted partial is paired with its next KNB
MAXQ = 256                     # pairs uploaded
NS = 2048                      # strip columns
NCH = 12                       # strip floats per column (3 x float4, interleaved)
PW, QW = 5, 7                  # floats per partial / pair row
OFFP = 32
OFFQ = OFFP + MAXP * PW
NQ = OFFQ + MAXQ * QW
LEVEL_FLOOR = 0.02
BLOOM_S = 0.12
R0 = 0.6                       # roughness scale: R_hat = 1 - exp(-R / R0) (a fifth .07, C major .42, a cluster .73)
Z0 = 0.05                      # wobble phasor (beat depth) that reaches ~76 % of the displacement (tanh)
F_GAIN = 6.0                   # gold
G0 = 0.05                      # grain
DMAX_H = 0.012                 # max displacement, frame heights (~26 px at 4K)
FULL_SPAN = (40.0, 8000.0)
PAIR_SPAN = {"south": (27.5, 600.0), "north": (600.0, 14000.0)}
FFT_LO, FFT_HI, FFT_N = 40.0, 16000.0, 48

# P (main kernel) layout
#  0 layout | 1 ring radius px | 2 k (rad per px along a line) | 3 ring lobes | 4 displacement px (signed scale)
#  5 1/Z0 | 6 threads gain | 7 gold gain | 8 grain gain | 9 haze gain | 10 log f_lo | 11 log(f_hi/f_lo)
#  12 blur weight | 13 chroma px | 14 time (grain) | 15 grain cell px | 16..18 gold rgb | 19 haze presence
#  20 white gain | 21 blur on
_SRC = HELPERS + PALETTE_CUDA + r"""
#define NS %(ns)d
#define MAXP %(maxp)d
#define MAXQ %(maxq)d
#define OFFP %(offp)d
#define OFFQ %(offq)d
#define PW %(pw)d
#define QW %(qw)d

// one thread per strip column: the threads' colour and white core, gold fusion (and its beat phasor), the wobble
// phasor and grain, as sums of gaussians along the axis
extern "C" __global__
void ob_strip(float* __restrict__ strip, const float* __restrict__ Q, const float* __restrict__ fft, const int haze_on)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= NS) return;
    const float u = (i + 0.5f) * (1.f / NS);
    const int np = min((int)Q[0], MAXP), nq = min((int)Q[1], MAXQ);
    const float isc = Q[2], ish = Q[3], hamp = Q[4], isf = Q[5], isfh = Q[6], fhamp = Q[7], isz = Q[8];
    const float cutp = Q[9], cutq = Q[10];            // |du| beyond which a row adds nothing
    float r = 0.f, g = 0.f, b = 0.f, w = 0.f;
    for (int k = 0; k < np; ++k) {
        const float* p = Q + OFFP + k * PW;
        const float du = u - p[0];
        if (fabsf(du) > cutp) continue;
        const float c = du * isc, h = du * ish;
        const float gc = __expf(-0.5f * c * c);
        const float wt = gc + hamp * __expf(-0.5f * h * h);
        r += wt * p[1]; g += wt * p[2]; b += wt * p[3]; w += gc * p[4];
    }
    float F = 0.f, Fc = 0.f, Fs = 0.f, Zc = 0.f, Zs = 0.f, G = 0.f;
    for (int k = 0; k < nq; ++k) {
        const float* q = Q + OFFQ + k * QW;
        const float du = u - q[0];
        if (fabsf(du) > cutq) continue;
        const float a = du * isz, f = du * isf, fh = du * isfh;
        const float gz = __expf(-0.5f * a * a);
        const float gf = __expf(-0.5f * f * f) + fhamp * __expf(-0.5f * fh * fh);
        F += gf * q[1]; Fc += gf * q[2]; Fs += gf * q[3];
        Zc += gz * q[4]; Zs += gz * q[5]; G += gf * q[6];     // the grain hugs the pair, like the gold
    }
    float4* s4 = reinterpret_cast<float4*>(strip) + 3 * i;      // interleaved: 3 float4 per column
    s4[0] = make_float4(r, g, b, w);
    s4[1] = make_float4(F, Fc, Fs, G);
    // the room's FFT at this column's frequency (40 Hz .. 16 kHz log bands): the haze, ~0.25 s late, never a driver
    float hv = 0.f;
    if (haze_on) {
        const float lf = Q[11] + u * Q[12];                                  // ln f
        const float bp = (lf - 3.6888794f) * (48.f / 5.9914645f) - 0.5f;     // band position
        if (bp > -1.f && bp < 48.f) {
            const int b0 = (int)floorf(bp);
            const float bt = bp - b0;
            const float v0 = (b0 >= 0) ? fft[b0] : 0.f, v1 = (b0 + 1 < 48) ? fft[b0 + 1] : 0.f;
            hv = smooth(0.12f, 0.9f, v0 + (v1 - v0) * bt);
        }
    }
    s4[2] = make_float4(Zc, Zs, hv, 0.f);
}

struct Tap { int i0; float t0, t1; };
__device__ __forceinline__ Tap tap_at(float u) {
    const float f = u * NS - 0.5f;
    const float fl = floorf(f);
    Tap tp; tp.i0 = (int)fl;
    const float t = f - fl;
    tp.t0 = (tp.i0 >= 0 && tp.i0 < NS) ? 1.f - t : 0.f;
    tp.t1 = (tp.i0 + 1 >= 0 && tp.i0 + 1 < NS) ? t : 0.f;
    return tp;
}
// one float4 group (0: threads rgb + white core, 1: gold F, Fc, Fs + grain, 2: wobble Zc, Zs) at a tap
__device__ __forceinline__ float4 rd4(const float4* __restrict__ S, int grp, const Tap& tp) {
    const int a = min(max(tp.i0, 0), NS - 1), b = min(max(tp.i0 + 1, 0), NS - 1);
    const float4 A = S[3 * a + grp], B = S[3 * b + grp];
    return make_float4(tp.t0 * A.x + tp.t1 * B.x, tp.t0 * A.y + tp.t1 * B.y,
                       tp.t0 * A.z + tp.t1 * B.z, tp.t0 * A.w + tp.t1 * B.w);
}

// 1/8-size blur: an 8x8 box (16 bilinear taps of 2x2 blocks)
extern "C" __global__
void ob_down(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int W, const int H, const int w, const int h)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int j = 0; j < 4; ++j) {
        #pragma unroll
        for (int i = 0; i < 4; ++i) {
            const float4 c = bilin(src, 8.f * x + 2.f * i + 1.f, 8.f * y + 2.f * j + 1.f, W, H);
            s.x += c.x; s.y += c.y; s.z += c.z;
        }
    }
    dst[y * w + x] = pack(make_float4(s.x * 0.0625f, s.y * 0.0625f, s.z * 0.0625f, 255.f));
}

// separable 7-tap gaussian (sigma ~1.4 cells) at 1/8 size
extern "C" __global__
void ob_gauss(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int w, const int h, const int dx, const int dy)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float K[4] = {0.2841f, 0.2232f, 0.1083f, 0.0325f};
    float4 s = make_float4(0.f, 0.f, 0.f, 0.f);
    #pragma unroll
    for (int k = -3; k <= 3; ++k) {
        const float4 c = ld(src, x + k * dx, y + k * dy, w, h);
        const float kk = K[abs(k)];
        s.x += kk * c.x; s.y += kk * c.y; s.z += kk * c.z;
    }
    dst[y * w + x] = pack(make_float4(s.x, s.y, s.z, 255.f));
}

// the soft copy's last step at 1/8 size: red and blue pulled apart along the radius (fringing that grows to the
// edges) and the blur weight in alpha (a floor of 0.3, the rest rising towards the edges)
extern "C" __global__
void ob_fringe(const uchar4* __restrict__ src, uchar4* __restrict__ dst, const int w, const int h, const float co,
               const float wmax)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float fx = x + 0.5f, fy = y + 0.5f;
    const float dx = fx - 0.5f * w, dy = fy - 0.5f * h;
    const float rr = sqrtf(dx * dx + dy * dy);
    const float rn = rr * 2.f / sqrtf((float)w * w + (float)h * h);         // 0 centre .. 1 corner
    const float inv = rr > 1e-3f ? 1.f / rr : 0.f;
    const float o = co * rn * rn, ex = dx * inv * o, ey = dy * inv * o;
    float4 c = ld(src, x, y, w, h);
    if (o > 0.04f) {
        c.x = bilin(src, fx + ex, fy + ey, w, h).x;
        c.z = bilin(src, fx - ex, fy - ey, w, h).z;
    }
    const float wb = clamp01(wmax * (0.3f + 0.7f * smooth(0.15f, 0.95f, rn)));
    dst[y * w + x] = make_uchar4((unsigned char)fminf(c.x + 0.5f, 255.f), (unsigned char)fminf(c.y + 0.5f, 255.f),
                                 (unsigned char)fminf(c.z + 0.5f, 255.f), (unsigned char)(wb * 255.f + 0.5f));
}

// the overlay at render_scale: each layer pixel (at its place in the full frame) finds u on the axis, the wobble
// there (the displacement, px of the full frame) and the threads, gold, grain and haze at the displaced place.
// Out: rgb = the glow already tonemapped (0..1), a = the displacement, 128 + 127 x d / dmax.
extern "C" __global__
void ob_layer(uchar4* __restrict__ L, const int lw, const int lh, const int W, const int H,
              const float4* __restrict__ S, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= lw || y >= lh) return;
    const float fx = (x + 0.5f) * (float)W / (float)lw, fy = (y + 0.5f) * (float)H / (float)lh;
    const float lay = P[0], R = P[1];
    const float dx = fx - 0.5f * W, dy = fy - 0.5f * H;
    const float rr = sqrtf(dx * dx + dy * dy);
    const float ul = fx / (float)W, ur = rr / R;
    const float u = ul + (ur - ul) * lay;
    // the wobble: Im(Z e^(i k s)) with s along the thread (lines: y; rings: the angle, whole lobes, no seam)
    const Tap t0 = tap_at(u);
    const float4 zz = rd4(S, 2, t0);
    const float zc = zz.x, zs = zz.y;
    float dpx = 0.f;
    if (fabsf(zc) + fabsf(zs) > 1e-7f && P[4] != 0.f) {
        float zl = 0.f, zr = 0.f;
        if (lay < 0.999f) { float sl, cl; __sincosf(P[2] * fy, &sl, &cl); zl = zs * cl + zc * sl; }
        if (lay > 0.001f) { float sr, cr; __sincosf(P[3] * atan2f(dy, dx), &sr, &cr); zr = zs * cr + zc * sr; }
        dpx = P[4] * tanhf((zl + (zr - zl) * lay) * P[5]);
    }
    // the overlays move with the picture (vibrating strings)
    const float uo = u + dpx * ((1.f - lay) / (float)W + lay / R);
    const Tap t1 = tap_at(uo);
    const float4 th = rd4(S, 0, t1), gb = rd4(S, 1, t1);
    const float F = gb.x, Fs = gb.z, Gr = gb.w;
    const float env = 0.6f + 0.4f * __sinf(3.14159265f * fy / (float)H);
    const float tk = P[6] * (env + (1.f - env) * lay);
    const float wg = P[20] * tk * th.w;
    float orr = tk * th.x + wg, og = tk * th.y + wg, ob = tk * th.z + wg;
    // grain: the fast beats as a fine fizz
    if (Gr > 0.03f) {                                    // below: under 0.3 / 255
        const float cell = P[15];
        const float n = vnoise(fx / cell + 0.37f * P[14], fy / cell - P[14] * 7.f, 911u);
        const float gv = P[8] * (1.f - __expf(-Gr)) * smooth(0.45f, 0.85f, n);
        orr += gv; og += gv * 0.95f; ob += gv * 0.85f;
    }
    // the room's FFT as a faint haze (lines: a fog rising from the floor as tall as the band is loud)
    const float v = zz.z;
    if (v > 1e-3f) {
        const float rise = __expf(-(1.f - fy / (float)H) / (0.1f + 0.45f * v));
        const float hz = P[9] * P[19] * v * (rise + (1.f - rise) * lay);
        orr += hz * 0.55f; og += hz * 0.68f; ob += hz;
    }
    float o1 = 1.f - __expf(-orr), o2 = 1.f - __expf(-og), o3 = 1.f - __expf(-ob);
    // gold bonds: F (1 + 0.15 sin psi), the beat as a gentle breathing; tonemapped on their own so they stay gold
    const float gold = P[7] * fmaxf(F + 0.15f * Fs, 0.f);
    if (gold > 1e-4f) {
        const float gg = 1.f - __expf(-gold), gw = 0.35f * gg * gg;       // a white-hot centre where it is strong
        o1 += fminf(gg * P[16] + gw, 1.f) * (1.f - o1);
        o2 += fminf(gg * P[17] + gw, 1.f) * (1.f - o2);
        o3 += fminf(gg * P[18] + gw, 1.f) * (1.f - o3);
    }
    const float dm = fmaxf(fabsf(P[4]), 1e-3f);
    L[y * lw + x] = make_uchar4((unsigned char)(clamp01(o1) * 255.f + 0.5f), (unsigned char)(clamp01(o2) * 255.f + 0.5f),
                                (unsigned char)(clamp01(o3) * 255.f + 0.5f),
                                (unsigned char)(128.f + 127.f * fminf(fmaxf(dpx / dm, -1.f), 1.f) + 0.5f));
}

// full resolution: the scene displaced along the axis direction, the soft fringed copy mixed in (focus), then the
// overlay screen-blended on top (sRGB)
extern "C" __global__
void ob_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const uchar4* __restrict__ L,
             const uchar4* __restrict__ blur, const float* __restrict__ P,
             const int W, const int H, const int lw, const int lh, const int bw, const int bh)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const size_t idx = (size_t)y * W + x;
    const float lx = (x + 0.5f) * (float)lw / (float)W - 0.5f, ly = (y + 0.5f) * (float)lh / (float)H - 0.5f;
    const int x0 = (int)floorf(lx), y0 = (int)floorf(ly);
    const float tx = lx - x0, ty = ly - y0;
    const int xa = min(max(x0, 0), lw - 1), xb = min(max(x0 + 1, 0), lw - 1);
    const int ya = min(max(y0, 0), lh - 1), yb = min(max(y0 + 1, 0), lh - 1);
    const unsigned int* Lu = reinterpret_cast<const unsigned int*>(L);
    const unsigned int ra = Lu[ya * lw + xa], rb = Lu[ya * lw + xb], rc = Lu[yb * lw + xa], rd_ = Lu[yb * lw + xb];
    const bool blur_on = P[21] > 0.5f;
    const unsigned int EMPTY = 0x80000000u;              // rgb 0, displacement 0
    if (ra == EMPTY && rb == EMPTY && rc == EMPTY && rd_ == EMPTY && !blur_on) { out[idx] = src[idx]; return; }
    const uchar4 A = L[ya * lw + xa], B = L[ya * lw + xb], C = L[yb * lw + xa], D = L[yb * lw + xb];
    const float u0 = (1.f - tx) * (1.f - ty), u1 = tx * (1.f - ty), u2 = (1.f - tx) * ty, u3 = tx * ty;
    const float o1 = (u0 * A.x + u1 * B.x + u2 * C.x + u3 * D.x) * (1.f / 255.f);
    const float o2 = (u0 * A.y + u1 * B.y + u2 * C.y + u3 * D.y) * (1.f / 255.f);
    const float o3 = (u0 * A.z + u1 * B.z + u2 * C.z + u3 * D.z) * (1.f / 255.f);
    const float dpx = ((u0 * A.w + u1 * B.w + u2 * C.w + u3 * D.w) - 128.f) * (1.f / 127.f) * fabsf(P[4]);
    float4 c;
    if (fabsf(dpx) < 0.02f) {
        c = ld(src, x, y, W, H);
    } else {
        const float lay = P[0];
        const float fx = x + 0.5f, fy = y + 0.5f, dx = fx - 0.5f * W, dy = fy - 0.5f * H;
        const float inv = rsqrtf(fmaxf(dx * dx + dy * dy, 1e-6f));
        c = bilin(src, fx + ((1.f - lay) + lay * dx * inv) * dpx, fy + lay * dy * inv * dpx, W, H);
    }
    if (blur_on) {                                       // the soft, fringed copy; its weight is in alpha
        const float bx = (x + 0.5f) * 0.125f - 0.5f, by = (y + 0.5f) * 0.125f - 0.5f;
        const int bx0 = (int)floorf(bx), by0 = (int)floorf(by);
        const float sx = bx - bx0, sy = by - by0;
        const int xa2 = min(max(bx0, 0), bw - 1), xb2 = min(max(bx0 + 1, 0), bw - 1);
        const int ya2 = min(max(by0, 0), bh - 1), yb2 = min(max(by0 + 1, 0), bh - 1);
        const uchar4 E = blur[ya2 * bw + xa2], F2 = blur[ya2 * bw + xb2], G = blur[yb2 * bw + xa2], K = blur[yb2 * bw + xb2];
        const float v0 = (1.f - sx) * (1.f - sy), v1 = sx * (1.f - sy), v2 = (1.f - sx) * sy, v3 = sx * sy;
        const float wb = (v0 * E.w + v1 * F2.w + v2 * G.w + v3 * K.w) * (1.f / 255.f);
        c.x += wb * (v0 * E.x + v1 * F2.x + v2 * G.x + v3 * K.x - c.x);
        c.y += wb * (v0 * E.y + v1 * F2.y + v2 * G.y + v3 * K.y - c.y);
        c.z += wb * (v0 * E.z + v1 * F2.z + v2 * G.z + v3 * K.z - c.z);
    }
    c.x += o1 * (255.f - c.x); c.y += o2 * (255.f - c.y); c.z += o3 * (255.f - c.z);
    out[idx] = pack(c);
}
""" % {"ns": NS, "nch": NCH, "maxp": MAXP, "maxq": MAXQ, "offp": OFFP, "offq": OFFQ, "pw": PW, "qw": QW}
_OPTIONS = ("--std=c++14", "--use_fast_math")


def _ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def partial_table(stretch: float, f_lo: float, f_hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Per MIDI pitch (128) x partial (1..10): frequency in Hz and the axis position u (0..1 inside the span)."""
    p = np.arange(128, dtype=np.float64)[:, None]
    n = np.arange(1, NPART + 1, dtype=np.float64)[None, :]
    st = min(max(float(stretch), 0.0), 1.0)
    cents = st * 30.0 * ((p - 64.0) / 44.0) ** 3
    B = st * np.maximum(1e-4, 2e-4 * 10.0 ** ((p - 60.0) / 48.0))
    f0 = 440.0 * 2.0 ** ((p - 69.0) / 12.0) * 2.0 ** (cents / 1200.0)
    f = n * f0 * np.sqrt(1.0 + B * n * n)
    u = np.log(f / f_lo) / math.log(f_hi / f_lo)
    return f, u


_IJ: dict = {}


def _pair_index(P: int, k_max: int):
    """Cached (I, J) of each sorted partial against its next k_max (J < P)."""
    key = (P, k_max)
    ij = _IJ.get(key)
    if ij is None:
        I = np.repeat(np.arange(P), k_max)
        J = I + np.tile(np.arange(1, k_max + 1), P)
        ok = J < P
        ij = _IJ[key] = (I[ok], J[ok])
    return ij


def roughness_pairs(f, a, nid, ph, slow, k_max=KNB):
    """Pairs of partials from different notes with |df| < 0.25 x their mean frequency (each sorted partial against
    its next k_max).  Returns i, j (into the inputs), the beat b (Hz), rho (Sethares roughness), phi (fusion), psi
    (the beat's phase, rad), the beat depth bd (min amplitude x [exp(-(b / 12 Hz)^2) - exp(-(b / 0.5 Hz)^2)]: how
    strongly the pair audibly beats; a fused pair barely does) and the wobble weight w (1 for visual beats slow x b <= 6 Hz, 0 from 9 Hz).  Pure numpy."""
    P = f.size
    if P < 2:
        e = np.zeros(0)
        return np.zeros(0, np.intp), np.zeros(0, np.intp), e, e, e, e, e, e
    o = np.argsort(f, kind="stable")
    fs = f[o]
    I, J = _pair_index(P, k_max)
    fi, fj = fs[I], fs[J]
    df = fj - fi
    keep = (df < 0.125 * (fi + fj)) & (nid[o[I]] != nid[o[J]])
    k = np.flatnonzero(keep)
    I, J, df, fi = o[I[k]], o[J[k]], df[k], fi[k]
    x = df * (0.24 / (0.0207 * fi + 18.96))
    ai, aj = a[I], a[J]
    amin = np.minimum(ai, aj)
    rho = amin * (np.exp(-3.5 * x) - np.exp(-5.75 * x))
    phi = np.sqrt(ai * aj) * np.exp(-(df / 1.5) ** 2)
    bd = amin * (np.exp(-(df / 12.0) ** 2) - np.exp(-(df / 0.5) ** 2))   # audible beating; ~0 once fused
    psi = ph[J] - ph[I]
    w = 1.0 - _ss(6.0, 9.0, slow * df)
    return I, J, df, rho, phi, psi, bd, w


class OvertoneBeats(Effect):
    name = "overtone_beats"
    stage = "overlay"
    label = "Overtone beats"
    description = ("The overtones of the notes being played become fine lines across any scene: where two notes' "
                   "overtones coincide they fuse gold, and where they nearly miss, the picture beneath shimmers at "
                   "the true beat rate of equal temperament.")
    listens = ("notes", "fft")
    params = {
        **standard_effect_params(),
        "threads": num(0.0, 1.0, 0.7, "Overtone threads", tier="regional", basic=True),
        "wobble": num(0.0, 1.0, 0.6, "Beat wobble", tier="regional", basic=True),
        "slow": num(0.1, 1.0, 0.5, "Beat speed (1 = the true rate)", tier="regional"),
        "layout": num(0.0, 1.0, 0.0, "Layout (lines -> rings)", tier="regional"),
        "stretch": num(0.0, 1.0, 0.7, "String stiffness (ideal -> real piano)", tier="regional"),
        "focus": num(0.0, 1.0, 0.3, "Dissonance blurs", tier="regional"),
        **palette_params("rainbow", period=1.0, drift=0.0),
        "span": enum(["full", "pair"], "full", "Axis (each TV / split over the pair)"),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale (the overlay layer)", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, _OPTIONS)
        self._ks = mod.get_function("ob_strip")
        self._kl = mod.get_function("ob_layer")
        self._kc = mod.get_function("ob_comp")
        self._kd = mod.get_function("ob_down")
        self._kg = mod.get_function("ob_gauss")
        self._kf = mod.get_function("ob_fringe")
        W, H = ctx.width, ctx.height
        self.bw, self.bh = max(1, (W + 7) // 8), max(1, (H + 7) // 8)
        self.b1 = cp.zeros((self.bh, self.bw, 4), cp.uint8)
        self.b2 = cp.zeros((self.bh, self.bw, 4), cp.uint8)
        self.strip = cp.zeros(NCH * NS, cp.float32)
        self.lw, self.lh = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.layer = cp.zeros((self.lh, self.lw, 4), cp.uint8)
        self.Q = cp.zeros(NQ, cp.float32)
        self.P = cp.zeros(32, cp.float32)
        self.zero_fft = cp.zeros(FFT_N, cp.float32)
        self.qh = np.zeros(NQ, np.float32)
        self.ph_host = np.zeros(32, np.float32)
        # span
        span = str(params.get("span", "full"))
        scr = str(getattr(ctx, "screen", "") or "").lower()
        self.f_lo, self.f_hi = FULL_SPAN
        if span == "pair":
            for k, v in PAIR_SPAN.items():
                if k in scr:
                    self.f_lo, self.f_hi = v
        self._tab_st = None
        self.ftab = self.utab = None
        # per (pitch, partial) phase, integrated at slow x f: a pair's difference turns at slow x its beat
        self.phase = np.zeros((128, NPART))
        self.last_age = np.full(128, np.inf)               # each pitch's AGE_ON when last seen (it falls: a re-strike)
        self._dphi = np.zeros((128, NPART))
        self.lnn = np.log(np.arange(1, NPART + 1, dtype=np.float64))
        self.rh = 0.0          # eased R_hat
        self.pres = 0.0        # eased presence (for the haze)
        self.drift = 0.0
        self.clock = 0.0
        self.fizz = 0.0
        self._first = True
        self._host_ms = 0.0
        self._lite = False
        self._col_key = None
        self._col = None

    # ------------------------------------------------------------------------------------------------ host side
    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = np.asarray(mod.palette_rows(), np.float64).reshape(-1)
                if rows.size < 12 or not np.isfinite(rows[:12]).all():
                    rows = None
            except Exception:
                rows = None
        if rows is None:
            rows = np.asarray(PALETTES.get(name, PALETTES["rainbow"]), np.float64).reshape(-1)
        return rows[:12]

    def _pc_colours(self, name: str, period: float) -> np.ndarray:
        """12 x rgb: each pitch class round the circle of fifths through the palette, folded to its bright side
        (cached while the palette, period and drift stand still)."""
        P = self._palette(name)
        key = (P.tobytes(), round(period, 4), round(self.drift, 4))
        if key == self._col_key:
            return self._col
        t = ((np.arange(12) * 7) % 12) / 12.0 * period + self.drift
        c = P[0:3][None, :] + P[3:6][None, :] * np.cos(2.0 * math.pi * (P[6:9][None, :] * t[:, None] + P[9:12][None, :]))
        c = np.clip(c, 0.0, 1.0)
        m = np.maximum(c.max(axis=1, keepdims=True), 0.4)
        self._col, self._col_key = np.minimum(c * (0.95 / m), 1.0), key
        return self._col

    def _tables(self, stretch: float) -> None:
        if self._tab_st is None or abs(stretch - self._tab_st) > 2e-3:
            self.ftab, self.utab = partial_table(stretch, self.f_lo, self.f_hi)
            self._tab_st = stretch

    def _notes(self):
        s = getattr(self, "sound", None)
        fr = getattr(s, "frame", None) if s is not None else None
        notes = getattr(fr, "notes", None) if fr is not None else None
        if notes is None or getattr(notes, "shape", None) != (128, 4):
            return None, None
        reg = None
        try:
            reg = ensemble.note_weights(getattr(s, "register", None), np.arange(128))
        except Exception:
            reg = None
        return notes, reg

    def _analyse(self, p: dict, slow: float) -> tuple[int, int, float, float]:
        """The notes -> partial rows and pair rows in self.qh.  Returns (partials, pairs, roughness R, top weight)."""
        notes, reg = self._notes()
        npart = 8 if self._lite else NPART
        maxn = 8 if self._lite else MAXN
        pts = np.zeros(0, np.intp)
        if notes is not None:
            L = np.fmax(notes[:, 0], 0.0)                     # NaN -> 0
            if reg is not None:
                L = L * reg
            L[L > 1.5] = 0.0                                  # +inf / nonsense: ignore that pitch
            pts = np.flatnonzero(L > LEVEL_FLOOR)
            if pts.size > maxn:
                pts = pts[np.argpartition(L[pts], -maxn)[-maxn:]]
        N = pts.size
        R = 0.0
        npp = nq = 0
        top = 0.0
        if N:
            lev = L[pts].astype(np.float64)
            vel = np.fmin(np.fmax(notes[pts, 1], 0.0), 1.0)    # NaN -> 0
            # fade in over LEVEL 0.02..0.06 and bloom in over BLOOM_S after the strike (age NaN / inf: settled)
            x = np.fmin(np.fmax(np.fmin(notes[pts, 2], 10.0), 0.0) * (1.0 / BLOOM_S), 1.0) \
                * np.minimum((lev - LEVEL_FLOOR) * (0.5 / LEVEL_FLOOR), 1.0)
            le = lev * x * x * (3.0 - 2.0 * x)
            gam = 1.6 - 0.8 * vel
            amp = le[:, None] * np.exp(-gam[:, None] * self.lnn[None, :npart])          # (N, npart)
            f = self.ftab[pts, :npart].reshape(-1)
            u = self.utab[pts, :npart].reshape(-1)
            # a (re)struck string starts its partials at phase 0 at the strike: phase = 2 pi slow f age, then integrated
            ag = np.fmin(np.fmax(notes[pts, 2], 0.0), 1e4)     # NaN -> 0
            new = ag < self.last_age[pts] - 0.05                  # first seen, or struck again (its age fell)
            if new.any():
                pn = pts[new]
                self.phase[pn] = np.remainder(self.ftab[pn] * (2.0 * math.pi * slow * ag[new])[:, None], 2.0 * math.pi)
            self.last_age[pts] = ag
            ph = self.phase[pts, :npart].reshape(-1)
            a = amp.reshape(-1)
            nid = np.repeat(np.arange(N), npart)
            # threads: the display weight (a^0.55: upper partials dim but visible; / sqrt(N / 4) past 4 notes)
            vis = (u > -0.02) & (u < 1.02) & (a > 1e-4)
            k = np.flatnonzero(vis)
            npp = min(k.size, MAXP)
            k = k[:npp]
            wd = np.power(a[k], 0.55) * min(1.0, math.sqrt(4.0 / N))   # a dense pedalled texture: each thread dimmer
            col = self._pc_colours(p["palette"], float(p["palette_period"]))[pts % 12]   # (N, 3)
            ck = col[nid[k]]
            qh = self.qh
            rows = qh[OFFP:OFFP + npp * PW].reshape(npp, PW)
            rows[:, 0] = u[k]
            rows[:, 1:4] = ck * wd[:, None]
            rows[:, 4] = wd * (0.25 + 0.75 * (1.0 - (k % npart) / max(npart - 1, 1)))   # white core, fuller low
            top = float(wd.max()) if npp else 0.0
            # pairs
            I, J, b, rho, phi, psi, bd, w = roughness_pairs(f, a, nid, ph, slow)
            if rho.size:
                # the harmony's roughness, by the loudest note (a quiet cluster is still a cluster), a little
                # less while everything is very quiet
                lmax = float(le.max())
                R = float(rho.sum()) / max(lmax, 1e-3) * (0.6 + 0.4 * min(lmax / 0.4, 1.0))
                um = 0.5 * (u[I] + u[J])
                sel = np.flatnonzero((um > -0.05) & (um < 1.05) & ((rho > 1e-5) | (phi > 1e-4)))
                if sel.size > MAXQ:
                    sel = sel[np.argpartition((rho + phi)[sel], -MAXQ)[-MAXQ:]]
                nq = sel.size
                if nq:
                    # the bonds, wobble and grain are drawn relative to the loudest note (partly: a quiet passage
                    # still shows its beats, a loud one a little more)
                    gn = 1.0 / max(lmax, 0.08) ** 0.7
                    q = qh[OFFQ:OFFQ + nq * QW].reshape(nq, QW)
                    cs, sn = np.cos(psi[sel]), np.sin(psi[sel])
                    fz = phi[sel] * gn
                    rw = bd[sel] * w[sel] * gn
                    q[:, 0] = um[sel]
                    q[:, 1] = fz
                    q[:, 2] = fz * cs
                    q[:, 3] = fz * sn
                    q[:, 4] = rw * cs
                    q[:, 5] = rw * sn
                    q[:, 6] = (rho[sel] + bd[sel] * (1.0 - w[sel])) * (gn / G0)
        return npp, nq, R, top

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dtc = float(dt)
        dtc = min(max(dtc, 0.0), 1.0) if math.isfinite(dtc) else 0.0
        h0 = time.perf_counter()
        a_amt = min(max(float(p["amount"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        slow = min(max(float(p["slow"]), 0.1), 1.0)
        lay = min(max(float(p["layout"]), 0.0), 1.0)
        focus = min(max(float(p["focus"]), 0.0), 1.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)
        self.clock = math.fmod(self.clock + dtc, 3600.0)
        self.fizz = math.fmod(self.fizz + (0.3 + 0.7 * slow) * dtc, 3600.0)   # the grain's drift, a rate
        self._tables(min(max(float(p["stretch"]), 0.0), 1.0))
        # phases turn at slow x f (wrapped); only differences are ever used
        np.multiply(self.ftab, 2.0 * math.pi * slow * dtc, out=self._dphi)
        self.phase += self._dphi
        np.remainder(self.phase, 2.0 * math.pi, out=self.phase)

        npp, nq, R, top = self._analyse(p, slow)
        # eased dissonance and presence
        rt = 1.0 - math.exp(-R / R0)
        if self._first:                                   # an effect that just arrived: no ease-in from nothing
            self.rh, self.pres, self._first = rt, min(top * 2.0, 1.0), False
        self.rh += (rt - self.rh) * (1.0 - math.exp(-dtc / (0.2 if rt > self.rh else 0.9)))
        self.pres += (min(top * 2.0, 1.0) - self.pres) * (1.0 - math.exp(-dtc / 0.4))
        if not math.isfinite(self.rh):
            self.rh = 0.0
        if not math.isfinite(self.pres):
            self.pres = 0.0
        # host time: drop to 8 notes x 8 partials if it runs long, back when it is quick again
        hm = (time.perf_counter() - h0) * 1000.0
        self._host_ms += (hm - self._host_ms) * 0.05
        if self._host_ms > 0.45:
            self._lite = True
        elif self._host_ms < 0.25:
            self._lite = False

        blur_w = a_amt * focus * float(_ss(0.08, 0.8, self.rh))    # consonance stays (nearly) sharp
        if a_amt <= 1e-4 or (npp == 0 and nq == 0 and self.pres < 0.01 and blur_w <= 0.01):
            cp.copyto(out, src)
            return

        W, H = self.ctx.width, self.ctx.height
        Rring = 0.5 * math.hypot(W, H)
        upH = (1.0 - lay) * H / W + lay * H / Rring           # axis u per frame height
        qh = self.qh
        qh[0] = npp
        qh[1] = nq
        sc, shh, sf, sfh, sz = 0.0017 * upH, 0.009 * upH, 0.003 * upH, 0.015 * upH, 0.045 * upH
        qh[2], qh[3], qh[4] = 1.0 / sc, 1.0 / shh, 0.3
        qh[5], qh[6], qh[7], qh[8] = 1.0 / sf, 1.0 / sfh, 0.35, 1.0 / sz
        qh[9] = 4.0 * shh
        qh[10] = 4.0 * max(sz, sfh)
        qh[11] = math.log(self.f_lo)
        qh[12] = math.log(self.f_hi / self.f_lo)
        self.Q.set(qh)

        P = self.ph_host
        P[0] = lay
        P[1] = Rring
        P[2] = 32.0 / H                                       # ~5 waves down the frame
        P[3] = 6.0                                            # ring lobes
        P[4] = a_amt * float(p["wobble"]) * DMAX_H * H
        P[5] = 1.0 / Z0
        P[6] = a_amt * float(p["threads"]) * 2.6 * (1.0 + 0.6 * pulse)
        P[7] = a_amt * F_GAIN * (0.5 + 0.5 * float(p["threads"]))
        P[8] = a_amt * 0.1
        P[9] = a_amt * 0.12
        P[10] = math.log(self.f_lo)
        P[11] = math.log(self.f_hi / self.f_lo)
        P[12] = blur_w
        P[13] = blur_w * 12.0 * H / 2160.0                    # colour fringe at the corners, px
        P[14] = self.fizz
        P[15] = max(1.5, 0.0035 * H)
        P[16], P[17], P[18] = 1.0, 0.68, 0.18
        P[19] = self.pres
        P[20] = 0.35
        P[21] = 1.0 if blur_w > 0.01 else 0.0
        self.P.set(P)

        s = getattr(self, "sound", None)
        fft = getattr(s, "fft_gpu", None) if s is not None else None
        haze_on = 1 if (fft is not None and self.pres > 0.01) else 0
        if fft is None:
            fft = self.zero_fft
        if npp or nq or haze_on:
            self._ks(((NS + 255) // 256, 1, 1), (256, 1, 1), (self.strip, self.Q, fft, np.int32(haze_on)))
        else:
            self.strip.fill(0)
        if P[21] > 0.5:
            launch_2d(self._kd, self.bw, self.bh, (src, self.b1, np.int32(W), np.int32(H), np.int32(self.bw), np.int32(self.bh)))
            launch_2d(self._kg, self.bw, self.bh, (self.b1, self.b2, np.int32(self.bw), np.int32(self.bh), np.int32(1), np.int32(0)))
            launch_2d(self._kg, self.bw, self.bh, (self.b2, self.b1, np.int32(self.bw), np.int32(self.bh), np.int32(0), np.int32(1)))
            launch_2d(self._kf, self.bw, self.bh, (self.b1, self.b2, np.int32(self.bw), np.int32(self.bh),
                                                   np.float32(P[13] / 8.0), np.float32(blur_w)))
        launch_2d(self._kl, self.lw, self.lh, (self.layer, np.int32(self.lw), np.int32(self.lh), np.int32(W),
                                               np.int32(H), self.strip, self.P))
        launch_2d(self._kc, W, H, (src, out, self.layer, self.b2, self.P, np.int32(W), np.int32(H),
                                   np.int32(self.lw), np.int32(self.lh), np.int32(self.bw), np.int32(self.bh)))

    def close(self) -> None:
        self.strip = self.Q = self.P = self.b1 = self.b2 = self.zero_fft = self.layer = None
