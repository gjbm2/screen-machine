"""Key weather: the harmony as a sky.  Cloud decks at four heights carry the key of the music at four timescales,
blowing in from the horizon as the piano plays.

What the picture shows
  You lie on the grass looking up and a little forward at a dusk sky (or, with ``view`` up, you fly between the cloud
  decks).  The wind comes from the horizon in front of you.  What the piano has just played condenses into cloud
  along a line low in the sky (the front) and then sails toward you and overhead, so the sky above is what was
  heard a little while ago and the clouds at the front are what is being heard now.  Beyond the front the weather
  thins to a clear glowing horizon: what has not been played yet.

  The four decks are four ways of listening at once:
  * the low scud, racing past, is the key of each second or two (chord by chord);
  * the middle deck, slower, is the key of each phrase (a few bars);
  * the high deck is the key of each section (half a minute to a minute);
  * the thin cirrus at the top is the key of the whole piece so far, from its start (far behind you) to now (at its
    front).
  A cloud's colour is its key: hue by the circle of fifths (C rose, G orange, D gold ... close keys look alike),
  major keys light, minor keys deep and darker.  How clearly the music sits in one key makes a cloud solid and
  crisp; an ambiguous passage gives soft grey cloud.  How much was played makes it thick; quiet passages leave
  thin wisps and haze, and pauses leave gaps of plain pale cloud drifting by.  Keys far round the circle of fifths
  from the piece's home key build darker, taller storm clouds; the home key is fair weather.  Chromatic passages
  (little diatonic order) tear the clouds ragged, triads and scales leave them smooth.  The sun and the sky's tint
  take the colour of the whole piece's key, so the light itself says where home is.

How the analysis maps onto it (stream_service/plugins/_lib/keyfield.py)
  The shared KeyTimeline is baked twice as TRAILING key fields (each texel: the key of the window that ENDS at that
  moment, i.e. what had been heard by then): a near field (the last 160 s) for the two low decks, and the whole
  piece for the two high ones.  The field's v (window length) becomes altitude: each deck reads its own band of
  rows (the middle of the band, wavering a little with the clouds' warp), so ``log_rows`` sets which window lengths
  the decks show.  The field's u (time) runs along each deck's wind: the low decks map
  display time onto distance behind the front through a ring of records (display time -> piece time, and whether
  the piano was playing then), so pauses become gaps that drift away with the rest; the high decks spread the whole
  piece from the front (now) to far behind you (its start).  Each deck's wind speed falls with its timescale.
  The clouds: each deck is one smooth 2-D density field at its middle height: a domain-warped fbm (the shape
  texture's own warp plus a slow warp that boils) with two finer octaves of fbm and billows, cut by a coverage
  threshold (cover x the deck's weight x the music's amount: window density x clarity), then a density ramp that
  climbs through the whole cloud to its heart (curved by clarity: crisp for a clear key, soft for an ambiguous one;
  low diatonicity and energy weight the fine octaves up, so the edges go ragged).  Its depth is in the light, not a
  march: the column's height grows with the density (hearts thick, edges thin) and sets both the ray's opacity and
  the sunlight's path; two looks toward the sun (a puff's and a cloud's reach) shade the far sides and light the
  sides that face it; the deck above casts its shadow; Henyey-Greenstein forward and back lobes with two softer
  multiple-scattering octaves give dark backlit hearts and bright rims, and a narrow silver lining near the sun.
  Colour = the field's key colour in two tones: lit, paled toward the sunlight (the sun coloured by the summit key);
  shadowed, turned toward the sky's cooler hue under the sky's light (a painter's shadow, never brown paint).  The
  low decks get less sun than the high ones (dusk light lingers aloft), so heights read; the octaves fade with the
  pixel's footprint (no shimmer toward the horizon), and edge-on the cover rises so distant clouds close into
  banks as real ones do.  The key planes are smoothed along time (a tent of +-8 texels near, +-1 for the whole
  piece) so chords wash into each other instead of striping a cloud; the field's fog thickens the haze in each deck.
  When a new piece begins, the old piece's low weather keeps blowing away overhead (a frozen copy of its field),
  and the high decks cross-fade over 6 s.  Before any note the timeline holds keyscape's example (C, G, back to C),
  played round in a loop, so the sky is never empty.

Macros: energy -> turbulence (erosion, warp of the fronts), wind (the display clock and the high decks' drift) and
light shafts through the low deck's gaps; pulse (bind it to note_on) -> a soft glow inside the newest clouds that
blooms in and eases out (never a flash); speed -> every motion (the wind, the boil, the sun and the camera's sway;
the record of piece time keeps the colours where the music put them); intensity -> output gain.  No palette: the
key colours are the content; ``hue_shift`` turns them (0..1 = 0..180 degrees).  Regional: ``deck_mix`` (which
timescales are shown: 0 the moment .. 1 the whole piece), ``cover``, ``view`` (0 on the ground looking up .. 1
between the decks), ``log_rows`` (0 long memory .. 1 seconds-to-piece), ``hue_shift``.  Alive with no notes: the
example or the frozen piece drifts, the clouds boil and breathe, the sun wanders and the camera sways.

Cost: per frame two key-field bakes (KeyField: ~0.1 ms), two pack launches (tens of thousands of texels, a short
tent each), one sky pass at render_scale 0.5 (per deck one field look and up to three noise octaves; where there is
cloud, two sun taps and one look at the deck above; 4 shaft taps only while energy is up; no march, so no jitter and
no history) and the upsample: 4.8 ms at 4K on the P4000 (fxtest --bench).  Host: a 768-byte arena and one or two
16-byte records; the 256 x 256 noise texture is built once per seed (numpy FFT, ~0.3 s).

Source: Original (inspired by C. S. Sapp, "Visual hierarchical key analysis", ACM Computers in Entertainment 3(4),
        2005, and C. Viaccoz et al., "Wavescapes", Musicae Scientiae 2022, through _lib/keyfield.py; cloud lighting
        and the coverage / erosion model after the published descriptions in A. Schneider & N. Vos, "The real-time
        volumetric cloudscapes of Horizon Zero Dawn", SIGGRAPH 2015 Advances in Real-Time Rendering; the
        Henyey-Greenstein phase function (1941); the multiple-scattering octaves after M. Wrenninge et al., "Oz: the
        great and volumetric", SIGGRAPH 2013 Talks; J. Jimenez's interleaved gradient noise (SIGGRAPH 2014).  No code
        copied; the repo's cloud_flyover.py (MIT, smfx) was read for scale only.)
Author: smfx (original)
License: MIT (original)
license_class: permissive
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import keyfield as KF
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.key_weather")

SPAN_N = 320.0                  # bins the near field spans (the low decks' history: 160 s)
NEAR_FW, NEAR_FH = 512, 96      # the near field's planes
FAR_FW, FAR_FH = 384, 96        # the whole piece's planes
R = 1024                        # display records (a ring; a power of two)
DELTA = 0.25                    # display bins per record (1/8 s at the nominal wind)
JUMP = 16.0                     # bins: records further apart than this are not interpolated (loop wrap, new piece)
EX_T0, EX_LEN = 20.0, 100.0     # the cold-start example plays round [20, 120) bins
GHOST_S = 6.0                   # the high decks cross-fade to a new piece over this
NT = 256                        # the noise texture (tileable, uchar4)
VFOV = 62.0                     # degrees
PACK_RAD = (8, 1)               # texels the near / whole-piece planes are smoothed along time (a tent of +-rad)

# the decks, low to high: core base / top, haze band lo / hi, field (0 near, 1 whole piece), v0 / v1 (rows: window
# length), front (along-wind distance of 'now'), scale (near: display bins per unit; far: 1 / the deck's extent), wind
# (deg from the view axis), ns (noise texels per unit), stretch (> 1: drawn out along the wind, the cirrus), sigma,
# fwarp (the field's warp, units), light tap, max distance, glow, idle wind (units/s, far decks), warp (the
# shapes' slow warp, texels), tower (how tall the hearts build), soft (the part of the cover over which the density
# climbs to the heart: low is crisp), erode (ragged edges), cap (the
# longest view path through one layer, units), sun (the sunlight it gets: the low decks lie in more shadow), cells (how
# much the gaps between small billows cut the shape: altocumulus), lx (light extinction: thin high clouds glow evenly)
DECKS = (
    dict(base=1.00, top=1.50, lo=0.90, hi=1.60, far=0, v0=0.00, v1=0.12, front=9.5, scale=6.5, wind=24.0, ns=19.0,
         stretch=1.0, sigma=6.0, fwarp=0.6, light=1, maxd=30.0, glow=1.0, idle=0.0, warp=9.0, tower=1.0,
         soft=0.95, erode=0.35, cap=1.6, sun=0.62, cells=0.2, lx=1.0),
    dict(base=2.05, top=2.70, lo=1.95, hi=2.80, far=0, v0=0.30, v1=0.52, front=17.8, scale=10.0, wind=9.0, ns=13.0,
         stretch=1.15, sigma=5.0, fwarp=1.0, light=1, maxd=45.0, glow=0.6, idle=0.0, warp=10.0,
         tower=0.75, soft=1.0, erode=0.40, cap=2.4, sun=0.82, cells=0.25, lx=0.9),
    dict(base=3.55, top=3.95, lo=3.45, hi=4.05, far=1, v0=0.50, v1=0.72, front=17.0, scale=1.0 / 15.5, wind=-6.0,
         ns=15.0, stretch=1.0, sigma=2.6, fwarp=1.4, light=1, maxd=60.0, glow=0.0, idle=0.045, warp=12.0,
         tower=0.5, soft=1.0, erode=0.35, cap=2.5, sun=1.0, cells=0.55, lx=0.45),
    dict(base=5.90, top=6.15, lo=5.85, hi=6.25, far=1, v0=0.88, v1=1.00, front=30.0, scale=1.0 / 28.0, wind=-20.0,
         ns=8.0, stretch=1.3, sigma=1.6, fwarp=2.5, light=0, maxd=80.0, glow=0.0, idle=0.025, warp=16.0,
         tower=0.25, soft=1.2, erode=0.45, cap=3.5, sun=1.15, cells=0.1, lx=0.35),
)
N_DECK = len(DECKS)

# arena layout (floats)
DK = 32                         # floats per deck
O_DK = 64                       # the decks
N_AR = O_DK + N_DECK * DK

_SRC = HELPERS + KF.KEYFIELD_CUDA + r"""
#define KW_R """ + str(R) + r"""
#define KW_ND """ + str(N_DECK) + r"""
#define KW_DK """ + str(DK) + r"""
#define KW_ODK """ + str(O_DK) + r"""
#define KW_NAR """ + str(N_AR) + r"""
#define KW_JUMP """ + repr(JUMP) + r"""f
#define KW_IA 0.35f
#define KW_WF 0.37f
#define KW_LX 0.55f
#define KW_IR 0.56f
#define KW_IG 0.53f
#define KW_IB 0.57f
#define KW_SAT 1.15f
#define KW_ALB 1.25f

// ---- small vector maths ----
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
__device__ __forceinline__ float kw_dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 kw_mix3(float3 a, float3 b, float t) { return a + (b - a) * t; }
__device__ __forceinline__ float4 kw_mix4(float4 a, float4 b, float t) {
    return make_float4(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t, a.w + (b.w - a.w) * t);
}
__device__ __forceinline__ float3 kw_ld3(const float* p) { return make_float3(p[0], p[1], p[2]); }
__device__ __forceinline__ float3 kw_norm(float3 a) { return a * rsqrtf(fmaxf(kw_dot(a, a), 1e-12f)); }
__device__ __forceinline__ float4 kw_c4(uchar4 q) { return make_float4((float)q.x, (float)q.y, (float)q.z, (float)q.w); }
__device__ __forceinline__ unsigned char kw_q(float v) { return (unsigned char)(255.f * clamp01(kf_fin(v)) + 0.5f); }
// sRGB-encoded 0..1 -> linear
__device__ __forceinline__ float kw_lin(float c) {
    c = clamp01(kf_fin(c));
    return c <= 0.04045f ? c * (1.f / 12.92f) : __powf((c + 0.055f) * (1.f / 1.055f), 2.4f);
}
// the Henyey-Greenstein phase function (isotropic = 1)
__device__ __forceinline__ float kw_hg(float mu, float g) {
    const float g2 = g * g;
    const float den = fmaxf(1.f + g2 - 2.f * g * mu, 1e-4f);
    return (1.f - g2) * rsqrtf(den * den * den);
}
// a static per-pixel start jitter (interleaved gradient noise)
__device__ __forceinline__ float kw_ign(int x, int y) {
    const float f = 0.06711056f * (float)x + 0.00583715f * (float)y;
    const float g = 52.9829189f * (f - floorf(f));
    return g - floorf(g);
}

// bilinear sample of a uchar4 plane at (u, v) in 0..1 (texel centres at (i + 0.5) / size), clamped, -> 0..1
__device__ float4 kw_plane(const uchar4* __restrict__ Pl, int fw, int fh, float u, float v) {
    u = kf_fin(u); v = kf_fin(v);
    const float fx = fminf(fmaxf(u * (float)fw - 0.5f, 0.f), (float)(fw - 1));
    const float fy = fminf(fmaxf(v * (float)fh - 0.5f, 0.f), (float)(fh - 1));
    const int x0 = (int)fx, y0 = (int)fy;
    const int x1 = min(x0 + 1, fw - 1), y1 = min(y0 + 1, fh - 1);
    const float tx = fx - (float)x0, ty = fy - (float)y0;
    const float4 a = kw_c4(Pl[y0 * fw + x0]), b = kw_c4(Pl[y0 * fw + x1]);
    const float4 c = kw_c4(Pl[y1 * fw + x0]), d = kw_c4(Pl[y1 * fw + x1]);
    const float sx = 1.f - tx, sy = 1.f - ty, k = 1.f / 255.f;
    return make_float4((sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x)) * k,
                       (sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y)) * k,
                       (sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z)) * k,
                       (sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w)) * k);
}
// bilinear, wrapping sample of the 256 x 256 noise texture at texel coordinates -> 0..1
// channels: x billow shape, y fine detail, z puffs, w low warp
__device__ float4 kw_noise(const uchar4* __restrict__ T, float x, float y) {
    x = kf_fin(x) - 0.5f; y = kf_fin(y) - 0.5f;
    const float fx = floorf(x), fy = floorf(y);
    const int x0 = ((int)fx) & 255, y0 = ((int)fy) & 255;
    const int x1 = (x0 + 1) & 255, y1 = (y0 + 1) & 255;
    const float tx = x - fx, ty = y - fy;
    const float4 a = kw_c4(T[y0 * 256 + x0]), b = kw_c4(T[y0 * 256 + x1]);
    const float4 c = kw_c4(T[y1 * 256 + x0]), d = kw_c4(T[y1 * 256 + x1]);
    const float sx = 1.f - tx, sy = 1.f - ty, k = 1.f / 255.f;
    return make_float4((sy * (sx * a.x + tx * b.x) + ty * (sx * c.x + tx * d.x)) * k,
                       (sy * (sx * a.y + tx * b.y) + ty * (sx * c.y + tx * d.y)) * k,
                       (sy * (sx * a.z + tx * b.z) + ty * (sx * c.z + tx * d.z)) * k,
                       (sy * (sx * a.w + tx * b.w) + ty * (sx * c.w + tx * d.w)) * k);
}

// ---- pass 1: the key-field planes -> the decks' small textures --------------------------------------------------
// X = (cloud amount, clarity, home distance, diatonicity), Y = (linear albedo, fog); cells outside the piece ease to
// the idle weather.  Each texel averages +-rad texels along time (a tent), so a chord's colour washes into its
// neighbours' instead of striping the clouds.  The whole-piece field also cross-fades from a ghost of the previous
// piece (sampled at u + du).
extern "C" __global__
void kw_pack(uchar4* __restrict__ X, uchar4* __restrict__ Y, const float4* __restrict__ A,
             const float4* __restrict__ B, const float4* __restrict__ C, const uchar4* __restrict__ GX,
             const uchar4* __restrict__ GY, const int fw, const int fh, const float wg, const float du, const int rad)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= fw || y >= fh) return;
    const int i = y * fw + x;
    float4 a = make_float4(0.f, 0.f, 0.f, 0.f), b = a, c = a;
    float ws = 0.f;
    const int rr = min(max(rad, 0), 8);
    for (int k = -rr; k <= rr; ++k) {
        const int j = y * fw + min(max(x + k, 0), fw - 1);
        const float w = (float)(rr + 1 - abs(k));
        const float4 ta = A[j], tb = B[j], tc = C[j];
        a = make_float4(a.x + w * ta.x, a.y + w * ta.y, a.z + w * ta.z, a.w + w * ta.w);
        b = make_float4(b.x + w * tb.x, b.y + w * tb.y, b.z + w * tb.z, b.w + w * tb.w);
        c = make_float4(c.x + w * tc.x, c.y + w * tc.y, c.z + w * tc.z, c.w + w * tc.w);
        ws += w;
    }
    const float iw = 1.f / ws;
    a = make_float4(a.x * iw, a.y * iw, a.z * iw, a.w * iw);
    b = make_float4(b.x * iw, b.y * iw, b.z * iw, b.w * iw);
    c = make_float4(c.x * iw, c.y * iw, c.z * iw, c.w * iw);
    const float in = clamp01(kf_fin(a.w));
    const float clar = clamp01(kf_fin(a.y)), fog = clamp01(kf_fin(a.z));
    const float dn = clamp01(kf_fin(c.w)), dia = clamp01(kf_fin(c.z)), home = clamp01(kf_fin(b.w));
    const float amt = dn * dn * (3.f - 2.f * dn) * (0.3f + 0.7f * clar);
    float3 lin = make_float3(kw_lin(b.x), kw_lin(b.y), kw_lin(b.z));
    const float lu = 0.2126f * lin.x + 0.7152f * lin.y + 0.0722f * lin.z;
    lin = make_float3(clamp01((lu + (lin.x - lu) * KW_SAT) * KW_ALB), clamp01((lu + (lin.y - lu) * KW_SAT) * KW_ALB),
                      clamp01((lu + (lin.z - lu) * KW_SAT) * KW_ALB));
    float4 xo = make_float4(KW_IA + (amt - KW_IA) * in, 0.35f + (clar - 0.35f) * in, home * in,
                            0.55f + (dia - 0.55f) * in);
    float4 yo = make_float4(KW_IR + (lin.x - KW_IR) * in, KW_IG + (lin.y - KW_IG) * in, KW_IB + (lin.z - KW_IB) * in,
                            0.45f + (fog - 0.45f) * in);
    const float w = clamp01(kf_fin(wg));
    if (w > 0.f) {
        const float u = ((float)x + 0.5f) / (float)fw + kf_fin(du), v = ((float)y + 0.5f) / (float)fh;
        const float e = clamp01(fminf(u, 1.f - u) * (float)fw + 0.5f);
        const float4 ix = make_float4(KW_IA, 0.35f, 0.f, 0.55f), iy = make_float4(KW_IR, KW_IG, KW_IB, 0.45f);
        const float4 gx = kw_mix4(ix, kw_plane(GX, fw, fh, u, v), e);
        const float4 gy = kw_mix4(iy, kw_plane(GY, fw, fh, u, v), e);
        xo = kw_mix4(xo, gx, w);
        yo = kw_mix4(yo, gy, w);
    }
    X[i] = make_uchar4(kw_q(xo.x), kw_q(xo.y), kw_q(xo.z), kw_q(xo.w));
    Y[i] = make_uchar4(kw_q(yo.x), kw_q(yo.y), kw_q(yo.z), kw_q(yo.w));
}

// ---- pass 2: the sky ---------------------------------------------------------------------------------------------
struct KwPl {
    const uchar4* Xn; const uchar4* Yn; const uchar4* GXn; const uchar4* GYn;
    const uchar4* Xf; const uchar4* Yf; const uchar4* NT; const float4* RG;
};
// one sample's reading of the field: amount, clarity, home distance, diatonicity (all eased to idle where unheard),
// validity, the plane coordinates, the display back (bins, near decks), the noise coordinates, the slow warp and the
// grazing boost of the cover (set by the caller)
struct KwF { float a, c, hm, dia, val, u, v, b, cx, cy, wx, wy, g; int pl; };

// arena P: [0]W [1]H [3]gain [4..6]eye [7..9]right [10..12]up [13..15]forward [16]tan(vfov/2) [17]aspect
//   [18..20]sun dir [21..23]sun colour [24..26]zenith [27..29]horizon [30..32]ambient [33]glow [34]shafts
//   [35]aerial haze /unit [36]cover x breath [37]turbulence [38]dither seed [39]record head slot [40]head fraction
//   [41]display bins per record [43]near org [44]near span [45]near fw [46]near fh [47]ghost org [48]ghost span
//   [49]far c [50]far fw [51]far fh [52]shaft deck [53]the shafts' floor (the deck below, or the ground)
//   [54..56]ground bounce colour [57]deck haze [58]silver lining [59]sun disc
// deck D: [0]core base [1]core top [2]band lo [3]band hi [4]weight [5]field [6]v lo [7]v hi [8]front (along-wind)
//   [9]near: display bins per unit, far: 1 / extent [10]wind sin [11]wind cos [12]noise texels/unit across
//   [13]along [14]noise offset along [15]across [16]warp offset x [17]warp offset y [18]sigma [19]light extinction (thin high clouds glow evenly)
//   [20]the field's warp (units) [21]light tap [22]max distance [23]glow [24]shape warp (texels) [25]tower
//   [26]edge softness [27]erosion [28]view path cap (units) [29]sunlight (low decks lie in more shadow)
//   [30]cells (how much the billows' gaps cut the shape) [31]the finest octave on (1) or off (0)

// the piece time, validity and source (0 this piece, 1 the previous piece's ghost) of display back b (bins)
__device__ __forceinline__ void kw_rec(const float* P, const float4* __restrict__ RG, float b,
                                       float& t, float& val, int& src) {
    float rx = P[40] - b / fmaxf(P[41], 1e-4f);
    if (!(rx == rx)) rx = -1e9f;
    if (rx < -(float)(KW_R - 3)) { t = 0.f; val = 0.f; src = 0; return; }
    float jf = floorf(rx), fr = rx - jf;
    if (jf > 0.f) { jf = 0.f; fr = 1.f; }
    const int kh = min(max((int)P[39], 0), KW_R - 1);
    const int j = (int)jf;
    const float4 r0 = RG[(kh + j + KW_R) & (KW_R - 1)], r1 = RG[(kh + j + 1 + KW_R) & (KW_R - 1)];
    if (fabsf(r1.x - r0.x) > KW_JUMP || fabsf(r1.z - r0.z) > 0.5f) {
        const float4 r = fr < 0.5f ? r0 : r1;
        t = r.x; val = r.y; src = r.z > 0.5f ? 1 : 0;
    } else {
        t = r0.x + (r1.x - r0.x) * fr; val = r0.y + (r1.y - r0.y) * fr; src = r0.z > 0.5f ? 1 : 0;
    }
    t = kf_fin(t); val = clamp01(kf_fin(val));
}

// what the music put at world point p of deck D (and the slow warp of the cloud shapes there, unless warp is false)
__device__ KwF kw_field(const float* P, const float* D, float3 p, const KwPl& pl, bool warp = true) {
    KwF f;
    const float al = p.x * D[10] + p.z * D[11];       // along the wind, toward where it comes from
    const float ac = p.x * D[11] - p.z * D[10];
    f.cx = ac * D[12] + D[15];
    f.cy = al * D[13] + D[14];
    const float4 w = warp ? kw_noise(pl.NT, f.cx * KW_WF + D[16], f.cy * KW_WF + D[17]) : make_float4(0.f, 0.f, 0.5f, 0.5f);
    f.wx = D[24] * (2.f * w.z - 1.f);
    f.wy = D[24] * (2.f * w.w - 1.f);
    f.g = 0.f;
    const float h01 = clamp01((p.y - D[0]) / fmaxf(D[1] - D[0], 1e-3f));
    f.v = D[6] + (D[7] - D[6]) * h01 + 0.05f * (w.z - 0.5f);
    const float dist = D[8] - al + D[20] * (2.f * w.w - 1.f);     // behind the front (units), swirled with the clouds
    float val;
    float4 X;
    if (D[5] < 0.5f) {                                // the low decks: display back -> the records -> piece time
        const float b = dist * D[9];
        float t; int src;
        kw_rec(P, pl.RG, b, t, val, src);
        const float org = src ? P[47] : P[43], span = fmaxf(src ? P[48] : P[44], 1e-3f);
        f.u = kf_fin((t - org) / span);
        f.b = b;
        f.pl = src;
        val *= smooth(-0.5f, 1.f, b) * clamp01(fminf(f.u, 1.f - f.u) * P[45] + 0.5f);
        X = kw_plane(src ? pl.GXn : pl.Xn, (int)P[45], (int)P[46], f.u, f.v);
    } else {                                          // the high decks: the whole piece, now at the front
        const float back = dist * D[9];
        f.u = kf_fin(P[49] - back);
        f.b = 1e3f;
        f.pl = 2;
        val = smooth(-0.02f, 0.06f, back) * smooth(-0.01f, 0.07f, fminf(f.u, 1.f - f.u));     // soft ends
        X = kw_plane(pl.Xf, (int)P[50], (int)P[51], f.u, f.v);
    }
    val = clamp01(val);
    f.val = val;
    const float ia = KW_IA * smooth(-0.3f * D[8], 0.f, dist);     // unplayed weather thins out past the front
    f.a = ia + (X.x - ia) * val;
    f.c = 0.35f + (X.y - 0.35f) * val;
    f.hm = X.z * val;
    f.dia = 0.55f + (X.w - 0.55f) * val;
    return f;
}
// the sample's albedo (linear) and fog
__device__ __forceinline__ float4 kw_ycol(const float* P, const KwPl& pl, const KwF& f) {
    float4 y;
    if (f.pl == 2) y = kw_plane(pl.Yf, (int)P[50], (int)P[51], f.u, f.v);
    else y = kw_plane(f.pl == 1 ? pl.GYn : pl.Yn, (int)P[45], (int)P[46], f.u, f.v);
    return kw_mix4(make_float4(KW_IR, KW_IG, KW_IB, 0.45f), y, f.val);
}
// the deck's coverage here (the cloudy fraction of the sky): the cover knob x the deck's weight x the music's amount;
// seen edge-on (g) the clouds' sides close the gaps between them, as real ones stack up toward the horizon
__device__ __forceinline__ float kw_cover(const float* P, const float* D, const KwF& f) {
    const float c = clamp01(P[36] * D[4] * (0.16f + 0.45f * f.a));
    return c + (1.f - c) * c * f.g;
}
// how ragged the edges are: chromatic music (little diatonic order) and energy weight the fine octaves up
__device__ __forceinline__ float kw_rough(const float* P, const float* D, const KwF& f) {
    return clamp01(D[27] * (0.5f + clamp01(1.f - 1.6f * f.dia)) + 0.5f * P[37]);
}
// the cloud noise at noise texel (x, y) (the warp already added): the warped shape plus two finer octaves of fbm and
// billows (puffs add, the gaps between them subtract), which boil a little faster than the shape.  fp is the pixel's
// footprint in shape texels: an octave fades out before it would shimmer.  ~0..1
__device__ float kw_fbm(const float* D, const uchar4* __restrict__ NT, float x, float y, float fp, float rough) {
    float v = kw_noise(NT, x, y).x;
    const float w1 = (0.42f + 0.25f * rough) * (1.f - smooth(0.6f, 1.4f, fp));
    if (w1 > 0.f) {
        const float4 b = kw_noise(NT, x * 2.13f + 71.f + 0.6f * D[16], y * 2.13f + 29.f + 0.6f * D[17]);
        v += w1 * ((1.f - D[30]) * (b.x - 0.5f) - D[30] * (b.y - 0.5f));
        const float w2 = (0.18f + 0.18f * rough) * (1.f - smooth(0.3f, 0.65f, fp)) * D[31];
        if (w2 > 0.f) {
            const float4 c = kw_noise(NT, x * 4.71f + 13.f + 1.3f * D[17], y * 4.71f + 157.f - 1.3f * D[16]);
            v += w2 * (0.7f * (c.x - 0.5f) - 0.3f * (c.y - 0.5f));
        }
    }
    return v;
}
// the cloud's density 0..1 from the noise: the coverage threshold, then a ramp that climbs through the cloud to its
// heart (so the inside keeps its shape for the light), curved by the key's clarity: a clear key rises fast (crisp
// edges), an ambiguous one slowly (soft, hazy edges); a coarse footprint softens it too
__device__ __forceinline__ float kw_core(const float* P, const float* D, const KwF& f, float v, float fp) {
    const float cov = kw_cover(P, D, f);
    const float c = clamp01((v - (1.f - cov)) / (fmaxf(cov, 0.1f) * D[26] * (1.f + 0.5f * fp)));
    return c > 0.f ? __powf(c, 1.75f - 0.7f * f.c) : 0.f;
}
// the column's height (units) at strength c: thin at the edges, the hearts build up (strong music and stormy keys
// build taller)
__device__ __forceinline__ float kw_colh(const float* D, const KwF& f, float c) {
    return (D[1] - D[0]) * (0.30f + 0.70f * clamp01(D[25] * (0.7f + 0.3f * f.a + 0.4f * f.hm)) * sqrtf(c));
}
// the ray's stretch inside lo <= y <= hi, clipped to [0, tmax] (empty when y <= x)
__device__ __forceinline__ float2 kw_band(float3 ro, float3 rd, float lo, float hi, float tmax) {
    if (fabsf(rd.y) < 1e-5f) return (ro.y >= lo && ro.y <= hi) ? make_float2(0.f, tmax) : make_float2(1.f, 0.f);
    const float a = (lo - ro.y) / rd.y, b = (hi - ro.y) / rd.y;
    return make_float2(fmaxf(fminf(a, b), 0.f), fminf(fmaxf(a, b), tmax));
}
// the sky: horizon -> zenith, tinted by the piece's key (host), the sun's glow
__device__ float3 kw_sky(const float* P, float3 rd, float3 sd) {
    const float e = sqrtf(fmaxf(rd.y, 0.f));
    float3 c = kw_mix3(kw_ld3(P + 27), kw_ld3(P + 24), e);
    const float m = clamp01(kw_dot(rd, sd)), m2 = m * m, m4 = m2 * m2, m8 = m4 * m4, m16 = m8 * m8;
    const float m32 = m16 * m16, m64 = m32 * m32, m256 = (m64 * m64) * (m64 * m64);
    return c + kw_ld3(P + 21) * (0.035f * m4 * m2 + 0.12f * m32 + 0.35f * m256);
}

extern "C" __global__
void kw_render(uchar4* __restrict__ out, const float* __restrict__ Ar, const float4* __restrict__ RG,
               const uchar4* __restrict__ Xn, const uchar4* __restrict__ Yn, const uchar4* __restrict__ GXn,
               const uchar4* __restrict__ GYn, const uchar4* __restrict__ Xf, const uchar4* __restrict__ Yf,
               const uchar4* __restrict__ NT)
{
    __shared__ float P[KW_NAR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    for (int i = tid; i < KW_NAR; i += blockDim.x * blockDim.y) P[i] = Ar[i];
    __syncthreads();
    const int W = (int)P[0], H = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    KwPl pl;
    pl.Xn = Xn; pl.Yn = Yn; pl.GXn = GXn; pl.GYn = GYn; pl.Xf = Xf; pl.Yf = Yf; pl.NT = NT; pl.RG = RG;

    const float sx = (2.f * ((float)x + 0.5f) / (float)W - 1.f) * P[16] * P[17];
    const float sy = (1.f - 2.f * ((float)y + 0.5f) / (float)H) * P[16];
    const float3 ro = kw_ld3(P + 4);
    const float3 rd = kw_norm(kw_ld3(P + 13) + kw_ld3(P + 7) * sx + kw_ld3(P + 10) * sy);
    const float3 sd = kw_ld3(P + 18), sunc = kw_ld3(P + 21), amb = kw_ld3(P + 30), gbc = kw_ld3(P + 54);
    const float mu = kw_dot(rd, sd);
    // phase: a strong forward lobe (the silver lining toward the sun) and a soft back lobe; the multiple-scattering
    // octaves use a flattened lobe
    const float ph1 = fminf(0.62f * kw_hg(mu, 0.75f) + 0.38f * kw_hg(mu, -0.2f), 6.f);
    const float ph2 = fminf(0.7f * kw_hg(mu, 0.4f) + 0.3f * kw_hg(mu, -0.1f), 3.f);
    const float sm = clamp01(mu), s2 = sm * sm, s4 = s2 * s2, fwl = s4 * s4;
    const float3 hazec = kw_sky(P, kw_norm(make_float3(rd.x, 0.04f + 0.25f * fmaxf(rd.y, 0.f), rd.z)), sd);
    const float3 mistc = amb * 0.85f + sunc * (0.06f * fminf(ph1, 2.f));
    const float sdy = fmaxf(sd.y, 0.2f);
    const float pa = 2.f * P[16] / (float)H;          // one pixel's angle (rad)
    const float alu = fmaxf(0.2126f * amb.x + 0.7152f * amb.y + 0.0722f * amb.z, 1e-4f);
    const float3 ambn = amb * (1.f / alu);            // the sky's hue at unit luminance
    const float3 coolv = make_float3(ambn.x * 0.78f, ambn.y * 0.9f, ambn.z * 1.4f);
    const float3 coolh = coolv * (1.f / fmaxf(0.2126f * coolv.x + 0.7152f * coolv.y + 0.0722f * coolv.z, 1e-4f));
    float3 col = make_float3(0.f, 0.f, 0.f);
    float T = 1.f;

    // light shafts: sunlight through the gaps of the lowest deck above the camera, in the air under it
    if (P[34] > 0.01f) {
        const int si = min(max((int)P[52], 0), KW_ND - 1);
        const float* D = P + KW_ODK + KW_DK * si;
        float tend = 14.f;
        if (rd.y > 1e-4f) tend = fminf(tend, fmaxf((D[2] - ro.y) / rd.y, 0.f));
        else if (rd.y < -1e-4f) tend = fminf(tend, fmaxf((P[53] - ro.y) / rd.y, 0.f));   // down to the floor
        if (tend > 0.05f && D[4] > 0.005f && ro.y < D[2]) {
            const float hm = 0.5f * (D[0] + D[1]), th = D[1] - D[0];
            const float jit = kw_ign(x, y);
            float acc = 0.f;
            for (int j = 0; j < 4; ++j) {
                const float tq = ((float)j + jit) * 0.25f * tend;
                const float3 q = ro + rd * tq;
                const float3 c = q + sd * ((hm - q.y) / fmaxf(sd.y, 0.05f));
                const KwF f = kw_field(P, D, c, pl, false);
                const float cq = kw_core(P, D, f, kw_fbm(D, NT, f.cx + f.wx, f.cy + f.wy, 9.f, 0.f), 1.f);
                acc += __expf(-D[18] * cq * kw_colh(D, f, cq) / sdy);
            }
            const float hs = fminf(kw_hg(mu, 0.78f), 6.f);
            col = col + sunc * (P[34] * hs * acc * 0.25f * fminf(tend, 8.f) * 0.012f);
        }
    }

    // the decks, near to far.  Each deck is one smooth 2-D density field (domain-warped fbm and billows over a
    // coverage threshold) at its middle height; its depth is in the light: the column's height (the hearts are thick,
    // the edges thin) sets how much the ray and the sunlight cross, and one look toward the sun lights the sides that
    // face it.  No march: nothing to grain, smear or stack.
    const bool up = rd.y >= 0.f;
    const float ary = fmaxf(fabsf(rd.y), 1e-3f);
    const float hx = sd.x, hz = sd.z, hl = rsqrtf(fmaxf(hx * hx + hz * hz, 1e-6f));
    for (int k = 0; k < KW_ND; ++k) {
        if (T < 0.02f) break;
        const int i = up ? k : KW_ND - 1 - k;
        const float* D = P + KW_ODK + KW_DK * i;
        if (P[36] * D[4] < 0.02f || fabsf(rd.y) < 1e-5f) continue;     // no cloud can form here
        const float ym = 0.5f * (D[0] + D[1]);
        const float t = (ym - ro.y) / rd.y;
        if (!(t > 0.f) || t > D[22]) continue;
        const float3 p = ro + rd * t;
        KwF f = kw_field(P, D, p, pl);
        f.g = 1.6f * smooth(0.3f, 1.5f, (D[1] - D[0]) * D[13] / (40.f * ary));
        const float sal = (hx * D[10] + hz * D[11]) * hl, sac = (hx * D[11] - hz * D[10]) * hl;
        // the deck's haze (its windows' fog: what little was played, from X as plane Y holds it) over the ray's
        // stretch in its band; half before the clouds, half after
        const float fog = clamp01(1.f - f.a / fmaxf(0.3f + 0.7f * f.c, 0.3f));
        const float2 sg = kw_band(ro, rd, D[2], D[3], D[22]);
        const float tau = fminf(P[57] * D[4] * (0.2f + fog) * fmaxf(sg.y - sg.x, 0.f), 0.6f) * 0.5f;
        const float3 hc = kw_mix3(mistc, hazec, 1.f - __expf(-t * P[35]));
        const float ah = 1.f - __expf(-tau);
        col = col + hc * (T * ah);
        T *= 1.f - ah;
        // the shape; the finer octaves fade as the pixel's footprint grows (far off and toward the horizon, where the
        // deck is seen edge-on), and far enough out only the average is left (no shimmer at the horizon)
        const float fp = t * pa * D[13] / ary;
        const float lod = smooth(2.5f, 6.f, fp) + (1.f - smooth(2.5f, 6.f, fp)) * smooth(0.6f * D[22], D[22], t);
        const float rough = kw_rough(P, D, f);
        const float nx = f.cx + f.wx, ny = f.cy + f.wy;
        const float cov = kw_cover(P, D, f);
        float c = 0.f;
        if (lod < 0.999f) c = kw_core(P, D, f, kw_fbm(D, NT, nx, ny, fp, rough), fp);
        c += (0.18f * cov - c) * lod;
        if (c > 0.003f) {
            const float d = c;
            const float sig = D[18] * (1.f + 0.6f * f.hm);
            const float hcol = kw_colh(D, f, c);
            const float odv = sig * d * fminf(hcol / ary, D[28]);
            const float a = 1.f - __expf(-odv);
            // where in the column the light we see scatters: near our side when it is thick, its middle when thin
            const float e = __expf(-0.5f * odv);
            const float hv = up ? 0.12f + 0.38f * e : 0.88f - 0.38f * e;
            const float lt = (1.f - hv) * hcol / sdy + 0.04f;
            // one look toward the sun (about a puff's reach): the sides that face it are lit, the far sides and
            // what lies in another cloud's shadow are shaded
            float d1 = d;
            if (D[21] > 0.5f && lod < 0.999f) {
                const float r1 = 11.f / D[13];
                d1 = kw_core(P, D, f, kw_fbm(D, NT, nx + sac * D[12] * r1, ny + sal * D[13] * r1, 9.f, rough), fp);
                d1 += (d - d1) * lod;
            }
            const float od = KW_LX * D[19] * sig * (lt * d + hcol / sdy * (0.85f * d1));
            // the deck above shades this one where its clouds cross the sun (its shape alone, one look)
            float sh = 1.f;
            if (i < 2 && lod < 0.999f) {                // the two low decks, under the next one up
                const float* U = D + KW_DK;
                const float cu0 = P[36] * U[4] * (0.16f + 0.45f * f.a);
                if (cu0 > 0.02f) {
                    const float3 q = p + sd * ((0.5f * (U[0] + U[1]) - ym) / fmaxf(sd.y, 0.05f));
                    const float qal = q.x * U[10] + q.z * U[11], qac = q.x * U[11] - q.z * U[10];
                    const float vu = kw_noise(NT, qac * U[12] + U[15], qal * U[13] + U[14]).x;
                    const float cu = clamp01((vu - (1.f - clamp01(cu0))) / (fmaxf(cu0, 0.1f) * U[26]));
                    sh = __expf(-0.35f * U[18] * cu * (U[1] - U[0]) / sdy);
                }
            }
            // relief: a slope that faces the sun (thinning toward it) catches more of it, the far slope less; strong
            // on tops seen from above, gentle on bases seen from below
            const float rel = clamp01(0.5f + 1.2f * (d - d1));
            sh *= fmaxf(1.f + (up ? 0.35f : 1.1f) * (2.f * rel - 1.f), 0.2f);
            const float4 yc = kw_ycol(P, pl, f);
            const float S = D[29] * (((ph1 + (ph2 - ph1) * lod) * __expf(-od) + 0.5f * ph2 * __expf(-0.35f * od)) * sh
                                     + 0.3f * __expf(-0.1f * od) * (0.5f + 0.5f * sh));
            float3 alb = make_float3(yc.x, yc.y, yc.z) * (1.f - 0.45f * f.hm);
            const float lu = 0.2126f * alb.x + 0.7152f * alb.y + 0.0722f * alb.z;
            alb = kw_mix3(alb, make_float3(lu, lu, lu), 0.65f * lod);     // far off: the key's colour, washed
            // lit tone: the key paled toward the sunlight; shadow tone: the key turned toward the sky's own hue (a
            // painter's cool shadow), lit by the sky above and a little warm light from the ground below
            const float3 lit = kw_mix3(alb, make_float3(lu, lu, lu), 0.35f) * 1.1f;
            const float3 shad = kw_mix3(alb, coolh * lu, 0.55f);
            const float ao = (0.40f + 0.60f * hv) * (0.55f + 0.45f * __expf(-0.6f * sig * d * hcol));
            float3 cs = lit * sunc * S + shad * (amb * (1.25f * ao) + gbc * (0.6f * (1.f - hv)));
            // the silver lining: thin edges toward the sun glow
            cs = cs + sunc * (P[58] * (fwl * fwl) * (fwl * fwl) * (4.f * d * (1.f - d)) * __expf(-0.5f * od));
            if (D[23] > 0.f && P[33] > 0.f) {         // the pulse: a soft glow inside the newest clouds
                const float g = P[33] * D[23] * (0.35f + 0.65f * f.val) * __expf(-0.05f * fmaxf(f.b, 0.f));
                cs = cs + (lit * 1.8f + make_float3(0.12f, 0.11f, 0.10f)) * (g * d);
            }
            cs = kw_mix3(cs, hazec, 1.f - __expf(-t * P[35]));
            col = col + cs * (T * a);
            T *= 1.f - a;
        }
        col = col + hc * (T * ah);
        T *= 1.f - ah;
    }

    // behind it all: the ground (with the low decks' shadows) or the sky and the sun
    if (T > 0.01f) {
        float3 bg;
        if (rd.y < -1e-4f && ro.y > 0.f) {
            const float tg = fminf(-ro.y / rd.y, 400.f);
            const float3 g = ro + rd * tg;
            float sh = 1.f;
            for (int k = 0; k < 2; ++k) {
                const float* D = P + KW_ODK + KW_DK * k;
                if (D[4] < 0.005f) continue;
                const float hm = 0.5f * (D[0] + D[1]);
                const float3 c = g + sd * (hm / fmaxf(sd.y, 0.05f));
                const KwF f = kw_field(P, D, c, pl);
                const float cq = kw_core(P, D, f, kw_fbm(D, NT, f.cx + f.wx, f.cy + f.wy, 9.f, 0.f), 1.f);
                sh *= __expf(-D[18] * cq * kw_colh(D, f, cq) / sdy);
            }
            const float4 gn = kw_noise(NT, g.x * 6.f + 33.f, g.z * 6.f + 91.f);
            const float3 galb = make_float3(0.030f, 0.040f, 0.028f) * (0.7f + 0.6f * gn.y);
            bg = galb * (sunc * (fmaxf(sd.y, 0.f) * sh * 1.3f) + amb * 0.6f);
            bg = kw_mix3(bg, hazec, 1.f - __expf(-tg * P[35] * 4.f));
        } else {
            bg = kw_sky(P, rd, sd) + sunc * (P[59] * smooth(0.99962f, 0.99985f, mu));
        }
        col = col + bg * T;
    }

    // tone, gain, dither
    const float3 c = make_float3(1.f - __expf(-1.3f * fmaxf(col.x, 0.f)), 1.f - __expf(-1.3f * fmaxf(col.y, 0.f)),
                                 1.f - __expf(-1.3f * fmaxf(col.z, 0.f)));
    const int pi = y * W + x;
    const float g = P[3];
    const float dth = (hash_u((unsigned int)pi ^ ((unsigned int)P[38] * 0x9E3779B1u)) >> 8)
                      * (1.f / 16777216.f) - 0.5f;
    out[pi] = pack(make_float4(255.f * kf_enc(c.x * g) + dth, 255.f * kf_enc(c.y * g) + dth,
                               255.f * kf_enc(c.z * g) + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------- host helpers
def _c01(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.0
    return min(max(x, 0.0), 1.0) if x == x else 0.0


def _ease(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0


def _lin(c) -> np.ndarray:
    c = np.clip(np.asarray(c, np.float64), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _rank01(a: np.ndarray) -> np.ndarray:
    flat = a.ravel()
    r = np.empty(flat.size, np.float64)
    r[np.argsort(flat, kind="stable")] = np.linspace(0.0, 1.0, flat.size)
    return r.reshape(a.shape)


_NOISE: dict = {}


def _fft_noise(rng, n: int, f_lo: float, f_hi: float, beta: float) -> np.ndarray:
    """Tileable band-limited noise: white noise filtered to an amplitude spectrum f^-beta between f_lo and f_hi cycles
    per tile (soft band edges); zero mean, unit variance."""
    w = rng.standard_normal((n, n))
    k = np.fft.fftfreq(n) * n
    f = np.sqrt(k[None, :] ** 2 + k[:, None] ** 2)
    amp = np.where(f > 0.0, np.maximum(f, 1e-6) ** -beta, 0.0)
    amp *= 1.0 / (1.0 + np.exp(-(f - f_lo) * 3.0)) / (1.0 + np.exp(f - f_hi))
    out = np.real(np.fft.ifft2(np.fft.fft2(w) * amp))
    return (out - out.mean()) / (out.std() + 1e-12)


def _worley(rng, n: int, cells: int) -> np.ndarray:
    """Tileable distance to the nearest of one jittered point per cell (in cell units)."""
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64) / n
    jx, jy = rng.random((cells, cells)), rng.random((cells, cells))
    ci = np.floor(xx * cells).astype(np.int64)
    cj = np.floor(yy * cells).astype(np.int64)
    best = np.full((n, n), 9.0)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            ii, jj = ci + di, cj + dj
            px = (ii + jx[jj % cells, ii % cells]) / cells
            py = (jj + jy[jj % cells, ii % cells]) / cells
            best = np.minimum(best, (xx - px) ** 2 + (yy - py) ** 2)
    return np.sqrt(best) * cells


def _wrap_sample(a: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    n = a.shape[0]
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    tx, ty = x - x0, y - y0
    x0 %= n
    y0 %= n
    x1, y1 = (x0 + 1) % n, (y0 + 1) % n
    return (1 - ty) * ((1 - tx) * a[y0, x0] + tx * a[y0, x1]) + ty * ((1 - tx) * a[y1, x0] + tx * a[y1, x1])


def _noise_texture(seed: int) -> np.ndarray:
    """A tileable 256 x 256 uchar4 noise for the clouds: x the shape (domain-warped fbm and billows: soft heaped
    masses), y the gaps between small puffs (what erosion eats, so edges break into billows), z and w two smooth slow
    fields (the kernel's warp, which boils).  Each channel rank-equalised to 0..1, so the coverage threshold is the
    cloudy fraction.  Every channel tiles (an FFT filter, wrapped cells, a wrapped warp)."""
    got = _NOISE.get(seed)
    if got is not None:
        return got
    n = NT
    rng = np.random.default_rng((seed * 7919 + 17) & 0x7FFFFFFF)
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64)
    wx, wy = _fft_noise(rng, n, 1.0, 5.0, 1.5), _fft_noise(rng, n, 1.0, 5.0, 1.5)
    fbm = _fft_noise(rng, n, 2.0, 40.0, 1.45)
    bill = -(0.55 * _worley(rng, n, 6) + 0.30 * _worley(rng, n, 12) + 0.15 * _worley(rng, n, 24))
    xw, yw = xx + 4.0 * wx, yy + 4.0 * wy
    shape = _rank01(0.5 * _rank01(_wrap_sample(fbm, xw, yw)) + 0.5 * _rank01(_wrap_sample(bill, xw, yw)))
    gaps = _rank01(0.6 * _worley(rng, n, 20) + 0.4 * _worley(rng, n, 40) + 0.08 * _fft_noise(rng, n, 20.0, 64.0, 1.0))
    warp_a = _rank01(_fft_noise(rng, n, 1.0, 6.0, 2.0))
    warp_b = _rank01(_fft_noise(rng, n, 1.0, 6.0, 2.0))
    tex = np.clip(np.stack([shape, gaps, warp_a, warp_b], axis=-1) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    if len(_NOISE) >= 4:
        _NOISE.clear()
    _NOISE[seed] = tex
    return tex


def deck_weights(m: float) -> list[float]:
    """deck_mix -> each deck's weight (0.15..1): 0 favours the low scud (the moment), 1 the cirrus (the piece)."""
    m = _c01(m)
    return [0.15 + 0.85 * math.exp(-((i / (N_DECK - 1) - m) / 0.45) ** 2) for i in range(N_DECK)]


# ------------------------------------------------------------------------------------------------- the scene
class KeyWeather(Source):
    """Per frame: two key-field bakes, two pack launches, one raymarch at render_scale and the upsample; one arena
    upload and one or two display records."""
    name = "key_weather"
    label = "Key weather"
    description = ("The harmony as a sky: four cloud decks carry the key of the last second, phrase, section and "
                   "whole piece, coloured by key and blowing in from the horizon as the piano plays.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette=None, render_scale=0.5),
            "deck_mix": num(0.0, 1.0, 0.35, "Timescales (moment .. whole piece)", basic=True),
            "cover": num(0.0, 1.0, 0.5, "Cloud cover"),
            "view": num(0.0, 1.0, 0.0, "View (ground .. between the decks)", basic=True),
            "log_rows": num(0.0, 1.0, 1.0, "Timescale curve (long .. log)"),
            "hue_shift": num(0.0, 1.0, 0.0, "Hue shift"),
            "piece_gap": num(30.0, 300.0, 90.0, "New piece after", unit="s", tier="structural"),
        }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.rs = float(params["render_scale"])
        self.w, self.h = internal_size(ctx, self.rs)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        mod = raw_module(_SRC, _OPTS)
        self.k_pack = mod.get_function("kw_pack")
        self.k_render = mod.get_function("kw_render")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.near = KF.KeyField(ctx, NEAR_FW, NEAR_FH)
        self.far = KF.KeyField(ctx, FAR_FW, FAR_FH)
        self.Xn = cp.zeros((NEAR_FH, NEAR_FW, 4), cp.uint8)
        self.Yn = cp.zeros((NEAR_FH, NEAR_FW, 4), cp.uint8)
        self.GXn = cp.zeros((NEAR_FH, NEAR_FW, 4), cp.uint8)
        self.GYn = cp.zeros((NEAR_FH, NEAR_FW, 4), cp.uint8)
        self.Xf = cp.zeros((FAR_FH, FAR_FW, 4), cp.uint8)
        self.Yf = cp.zeros((FAR_FH, FAR_FW, 4), cp.uint8)
        self.GXf = cp.zeros((FAR_FH, FAR_FW, 4), cp.uint8)
        self.GYf = cp.zeros((FAR_FH, FAR_FW, 4), cp.uint8)
        self.ring = cp.zeros((R, 4), cp.float32)
        self.rec = np.zeros((R, 4), np.float32)
        self.NT = cp.asarray(_noise_texture(self.seed))
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        self._tl_priv = KF.KeyTimeline(self.seed)          # used while self.sound is None (the example, looped)
        # samples per deck and pixel: one look at its middle (the decks are smooth 2-D fields lit as columns)
        self.steps = [1] * N_DECK
        # the display clock (display bins; advances with the wind) and its records
        self.D = 0.0
        self.k_head = 0
        self.D_prev = 0.0
        self.t_prev = 0.0
        self.v_prev = 0.0
        self.t_last = 0.0
        self.vs = 0.0                                      # the smoothed 'the piano is playing' validity
        self.ex_t = 0.0                                    # the example's loop phase (bins)
        self.piece_key = None
        self.near_geom = (0.0, SPAN_N)
        self.g_org, self.g_span = 0.0, SPAN_N
        self.far_c = 1.0
        self.c_old = 1.0
        self.wg = 0.0
        # motion
        self.es = 0.5
        self.glow = 0.0
        self.cam_ph = 0.0
        self.sun_ph = 0.13 * (self.seed % 7)
        self.breath = 0.0
        self.off_al = [0.0] * N_DECK
        self.off_ac = [float((37 * i + self.seed) % 256) for i in range(N_DECK)]
        self.boil = [[float((53 * i + 3 * self.seed) % 97), float((29 * i + 5 * self.seed) % 89)]
                     for i in range(N_DECK)]
        self.tint = None
        self.frames = 0
        self.sun_k = 0.0

    # -- the display records ------------------------------------------------------------------------------------
    def _upload(self, k0: int, k1: int) -> None:
        """Upload records k0..k1 (inclusive, global indices) of the ring."""
        if k1 - k0 + 1 >= R:
            self.ring.set(self.rec)
            return
        a, b = k0 % R, k1 % R
        if a <= b:
            self.ring[a:b + 1].set(self.rec[a:b + 1])
        else:
            self.ring[a:].set(self.rec[a:])
            self.ring[:b + 1].set(self.rec[:b + 1])

    def _prefill(self, tl, t_cur: float, rate_d: float) -> None:
        """The first frame: records as if the display had been running, so a scene put on mid-piece shows its
        recent history at once (pauses already gone from piece time; the current silence as a gap)."""
        k_new = int(math.floor(self.D / DELTA))
        self.k_head = k_new
        ks = np.arange(k_new - R + 2, k_new + 2, dtype=np.int64)
        back = np.maximum(self.D - ks * DELTA, 0.0)
        fd = min(max(2.0 / rate_d, 0.25), 4.0) if rate_d > 1e-6 else 1.0
        if tl.example:
            t = EX_T0 + np.mod(self.ex_t - back * fd, EX_LEN)
            v = np.ones_like(t)
        else:
            qd = min(max(float(tl.quiet) - KF.QUIET_HOLD, 0.0), 1e5) * max(rate_d, 1e-6)
            heard = back >= qd
            t = np.where(heard, t_cur - (back - qd) * fd, t_cur)
            lo = max(0.0, t_cur - KF.CAP)
            v = np.where(heard & (t >= lo) & bool(tl.started), 1.0, 0.0)
        idx = ks % R
        self.rec[idx, 0] = t
        self.rec[idx, 1] = v
        self.rec[idx, 2] = 0.0
        self.rec[idx, 3] = 0.0
        self.ring.set(self.rec)
        self.D_prev, self.t_prev, self.v_prev = self.D, t_cur, float(v[-1]) if v.size else 0.0

    def _write(self, t_cur: float, v_cur: float) -> None:
        """Records up to the display clock, piece time interpolated between frames; the provisional one after."""
        k_new = int(math.floor(self.D / DELTA))
        if k_new > self.k_head:
            k0 = max(self.k_head + 1, k_new - R + 3)
            ks = np.arange(k0, k_new + 1, dtype=np.int64)
            span = self.D - self.D_prev
            if span > 1e-9 and abs(t_cur - self.t_prev) <= JUMP:
                f = np.clip((ks * DELTA - self.D_prev) / span, 0.0, 1.0)
                t = self.t_prev + (t_cur - self.t_prev) * f
                v = self.v_prev + (v_cur - self.v_prev) * f
            else:
                t = np.full(ks.shape, t_cur)
                v = np.full(ks.shape, v_cur)
            idx = ks % R
            self.rec[idx, 0] = t
            self.rec[idx, 1] = v
            self.rec[idx, 2] = 0.0
            self.rec[(k_new + 1) % R] = (t_cur, v_cur, 0.0, 0.0)
            self._upload(k0, k_new + 1)
            self.k_head = k_new
        else:
            self.rec[(k_new + 1) % R] = (t_cur, v_cur, 0.0, 0.0)
            self._upload(k_new + 1, k_new + 1)
        self.D_prev, self.t_prev, self.v_prev = self.D, t_cur, v_cur

    def _new_piece(self) -> None:
        """A new piece (or the example giving way to the first): the low decks' old records point at a frozen copy
        of the old near field and keep blowing away; the high decks cross-fade from a copy of the old whole piece."""
        cp = self.ctx.cp
        cp.copyto(self.GXn, self.Xn)
        cp.copyto(self.GYn, self.Yn)
        cp.copyto(self.GXf, self.Xf)
        cp.copyto(self.GYf, self.Yf)
        self.g_org, self.g_span = self.near_geom
        old = self.rec[:, 2] > 0.5
        self.rec[old, 1] = 0.0                        # an older ghost's records: its copy is gone
        self.rec[~old, 2] = 1.0
        self.ring.set(self.rec)
        self.c_old = self.far_c
        self.wg = 1.0

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        dt = dt if dt == dt and 0.0 < dt < 1e4 else 0.0
        e = _c01(p["energy"])
        pulse = _c01(p["pulse"])
        spd = min(max(float(p["speed"]), 0.0), 4.0) if p["speed"] == p["speed"] else 1.0
        gain = min(max(float(p["intensity"]), 0.0), 2.0) if p["intensity"] == p["intensity"] else 1.0
        mix = _c01(p["deck_mix"])
        cover = _c01(p["cover"])
        view = _c01(p["view"])
        logr = _c01(p["log_rows"])
        hue_deg = 180.0 * _c01(p["hue_shift"])

        # the piece: one shared timeline per screen (a private one, the example, while there is no sound)
        snd = self.sound
        tl = KF.KeyTimeline.shared(snd, self.seed) if snd is not None else self._tl_priv
        tl.update(snd, dt, float(p["piece_gap"]))

        # macros
        self.es += (e - self.es) * _ease(dt, 0.8)
        tau = 0.12 if pulse > self.glow else 0.7          # blooms in, eases out
        self.glow += (pulse - self.glow) * _ease(dt, tau)
        windf = 0.75 + 0.5 * self.es
        rate_d = 2.0 * spd * windf                        # display bins / s (2 = the music's own pace)
        dD = rate_d * dt
        self.D += dD

        # piece time now, and whether the piano is playing (the clock moves)
        key = (id(tl), int(tl.pieces), bool(tl.example))
        if tl.example:
            self.ex_t = (self.ex_t + 2.0 * spd * dt) % EX_LEN
            t_cur, v_tgt = EX_T0 + self.ex_t, 1.0
        else:
            t_cur = float(tl.now())
            if key == self.piece_key and t_cur < self.t_last - 0.5 * KF.DROP:     # the timeline re-based
                shift = KF.DROP * round((self.t_last - t_cur) / KF.DROP)
                cur = self.rec[:, 2] < 0.5
                self.rec[cur, 0] -= shift
                self.t_prev -= shift
                self.t_last -= shift
                self.ring.set(self.rec)
            v_tgt = (1.0 if t_cur > self.t_last + 1e-7 else 0.0) if dt > 0.0 else self.vs
        if key != self.piece_key:
            first = self.piece_key is None
            self.piece_key = key
            self.t_last = t_cur
            if first:
                self.vs = 1.0 if (tl.example or (tl.started and not tl.frozen and tl.quiet < KF.QUIET_HOLD)) else 0.0
                self._prefill(tl, t_cur, rate_d)
            else:
                self._new_piece()
                self.vs = 1.0 if tl.example else 0.0
                self.D_prev, self.t_prev, self.v_prev = self.D, t_cur, self.vs
        self.vs += (v_tgt - self.vs) * _ease(dt, 0.4)
        self.t_last = t_cur
        self._write(t_cur, self.vs)

        # the key fields, then the decks' textures
        self.near.bake(tl, KF.TRAILING, SPAN_N, logr, hue_deg)
        self.far.bake(tl, KF.TRAILING, None, logr, hue_deg)
        self.near_geom = (float(self.near.org), float(self.near.span))
        fsp = max(float(self.far.span), 1e-3)
        self.far_c = (float(self.far.now) - float(self.far.org)) / fsp
        wg = self.wg * self.wg * (3.0 - 2.0 * self.wg)
        du = self.c_old - self.far_c
        launch_2d(self.k_pack, NEAR_FW, NEAR_FH, (self.Xn, self.Yn, self.near.A, self.near.B, self.near.C, self.GXn,
                                                  self.GYn, np.int32(NEAR_FW), np.int32(NEAR_FH), np.float32(0.0),
                                                  np.float32(0.0), np.int32(PACK_RAD[0])))
        launch_2d(self.k_pack, FAR_FW, FAR_FH, (self.Xf, self.Yf, self.far.A, self.far.B, self.far.C, self.GXf,
                                                self.GYf, np.int32(FAR_FW), np.int32(FAR_FH), np.float32(wg),
                                                np.float32(du), np.int32(PACK_RAD[1])))
        if dt > 0.0:
            self.wg = max(0.0, self.wg - dt / GHOST_S)

        # light and sky: the sun and the tint take the whole piece's key
        sk, sclar = tl.summit()
        if sk is not None and sk >= 0:
            kl = _lin(KF.key_rgb(int(sk), hue_deg, 0.13))
            tgt = np.append(kl, min(max(float(sclar), 0.0), 1.0))
        else:
            tgt = np.append(_lin(KF.key_rgb(-1)), 0.0)
        if self.tint is None:
            self.tint = tgt.copy()
        self.tint += (tgt - self.tint) * _ease(dt, 3.0)
        kl, kc = self.tint[:3], float(self.tint[3])
        klu = max(float(0.2126 * kl[0] + 0.7152 * kl[1] + 0.0722 * kl[2]), 1e-3)
        warm = np.array((1.46, 1.30, 1.10))
        sunc = warm * (1.0 + (np.clip(kl / klu, 0.0, 2.5) - 1.0) * 0.25 * kc) * (0.9 + 0.25 * self.es)
        zen = np.array((0.016, 0.036, 0.115)) + 0.035 * kl
        hor = np.array((0.220, 0.180, 0.170)) + 0.08 * kl
        amb = (0.45 * zen + 0.55 * hor) * 1.4

        # motion
        self.cam_ph = (self.cam_ph + 0.035 * spd * dt) % 1000.0
        self.sun_ph = (self.sun_ph + 0.008 * spd * dt) % 1000.0
        self.breath = (self.breath + 0.05 * spd * dt) % 1000.0
        c = 2.0 * math.pi * self.cam_ph
        yaw = 0.06 * math.sin(c) + 0.025 * math.sin(2.3 * c + 1.0)
        pitch = math.radians(36.0 - 27.0 * view) + 0.02 * math.sin(1.7 * c + 2.0)
        roll = 0.012 * math.sin(1.1 * c + 0.5) * (0.4 + 0.6 * view)
        cy = 0.06 + 1.66 * view + 0.04 * view * math.sin(0.8 * c + 1.3)
        fwd = np.array((math.cos(pitch) * math.sin(yaw), math.sin(pitch), math.cos(pitch) * math.cos(yaw)))
        r0 = np.array((math.cos(yaw), 0.0, -math.sin(yaw)))
        u0 = np.cross(fwd, r0)
        right = r0 * math.cos(roll) + u0 * math.sin(roll)
        upv = u0 * math.cos(roll) - r0 * math.sin(roll)
        sp = 2.0 * math.pi * self.sun_ph
        az = 0.38 * math.sin(sp)
        el = math.radians(19.0 + 4.0 * math.sin(0.7 * sp + 1.0))
        sd = np.array((math.cos(el) * math.sin(az), math.sin(el), math.cos(el) * math.cos(az)))

        # the wind: the low decks' noise moves with the display clock (a cloud keeps its key), the high decks drift;
        # the slow warp of every deck boils (faster with energy), so the shapes change as they sail
        boil = (0.6 + 1.6 * self.es) * spd * dt
        for i, d in enumerate(DECKS):
            nal = d["ns"] / math.sqrt(d["stretch"])
            if d["far"] == 0:
                self.off_al[i] = (self.off_al[i] + dD / d["scale"] * nal) % 256.0
            else:
                self.off_al[i] = (self.off_al[i] + d["idle"] * spd * windf * dt * nal) % 256.0
            self.off_ac[i] = (self.off_ac[i] + 0.015 * spd * dt * d["ns"]) % 256.0
            self.boil[i][0] = (self.boil[i][0] + 1.3 * boil) % 256.0
            self.boil[i][1] = (self.boil[i][1] + 0.9 * boil) % 256.0

        A = self.A
        A.fill(0.0)
        A[0], A[1], A[3] = self.w, self.h, gain
        A[4:7] = (0.0, cy, 0.0)
        A[7:10], A[10:13], A[13:16] = right, upv, fwd
        A[16], A[17] = math.tan(math.radians(0.5 * VFOV)), self.w / max(self.h, 1)
        A[18:21], A[21:24] = sd, sunc
        A[24:27], A[27:30], A[30:33] = zen, hor, amb
        A[33] = 0.9 * self.glow
        A[34] = 0.55 * self.es ** 1.3
        A[35] = 0.024
        A[36] = 1.35 * cover * (1.0 + 0.05 * math.sin(2.0 * math.pi * self.breath))
        A[37] = 0.15 + 0.85 * self.es
        A[38] = self.seed % 65521
        A[39] = self.k_head % R
        A[40] = self.D / DELTA - self.k_head
        A[41] = DELTA
        A[43], A[44] = self.near_geom
        A[45], A[46] = NEAR_FW, NEAR_FH
        A[47], A[48] = self.g_org, self.g_span
        A[49] = self.far_c
        A[50], A[51] = FAR_FW, FAR_FH
        A[52] = 0 if cy < DECKS[0]["lo"] else 1
        A[53] = 0.0 if cy < DECKS[0]["lo"] else DECKS[0]["hi"]
        A[54:57] = np.array((0.060, 0.055, 0.040)) * (0.5 + 0.5 * float(sunc.mean()) / 1.3)
        A[57] = 0.025
        A[58] = 0.8
        A[59] = 14.0
        wts = deck_weights(mix)
        warpf = 0.7 + 0.6 * self.es
        for i, d in enumerate(DECKS):
            B = A[O_DK + DK * i:O_DK + DK * (i + 1)]
            phi = math.radians(d["wind"])
            st = math.sqrt(d["stretch"])
            B[0:4] = d["base"], d["top"], d["lo"], d["hi"]
            B[4] = wts[i] ** 1.5
            B[5] = d["far"]
            B[6], B[7] = d["v0"], d["v1"]
            B[8], B[9] = d["front"], d["scale"]
            B[10], B[11] = math.sin(phi), math.cos(phi)
            B[12], B[13] = d["ns"] * st, d["ns"] / st
            B[14], B[15] = self.off_al[i], self.off_ac[i]
            B[16], B[17] = self.boil[i]
            B[18] = d["sigma"]
            B[19] = d["lx"]
            B[20] = d["fwarp"] * warpf
            B[21] = d["light"]
            B[22] = d["maxd"]
            B[23] = d["glow"]
            B[24] = d["warp"]
            B[25] = d["tower"]
            B[26] = d["soft"]
            B[27] = d["erode"]
            B[28] = d["cap"]
            B[29] = d["sun"]
            B[30] = d["cells"]
            B[31] = 1.0 if d["far"] == 0 else 0.0     # the finest octave: the low decks only
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.arena.set(A)
        launch_2d(self.k_render, self.w, self.h, (self.buf, self.arena, self.ring, self.Xn, self.Yn, self.GXn, self.GYn,
                                                  self.Xf, self.Yf, self.NT))
        self.frames += 1
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        for f in (getattr(self, "near", None), getattr(self, "far", None)):
            if f is not None:
                f.close()
        self.near = self.far = None
        self.buf = self.arena = self.ring = self.NT = None
        self.Xn = self.Yn = self.GXn = self.GYn = self.Xf = self.Yf = self.GXf = self.GYf = None
