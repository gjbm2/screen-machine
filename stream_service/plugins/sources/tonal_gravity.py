"""Tonal gravity: the key as a glowing gravity well; the harmony is a bead that falls home at every cadence.

Source: Original (inspired by Elaine Chew's Spiral Array, Towards a Mathematical Model of Tonality, MIT 2000, and
        MuSA.RT, https://en.wikipedia.org/wiki/Spiral_array_model ; Herremans & Chew, Tension ribbons, TENOR 2016,
        https://www.dorienherremans.com/sites/default/files/paper_tenor_dh_preprint_small.pdf ; Chew & Chen's
        pitch spelling, https://music.informatics.indiana.edu/courses/I546/pdf/chew_chen.pdf ; Lerdahl's melodic
        attraction, Tonal Pitch Space, 2001.  Formulas only; no code was copied or ported.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
----------------------
You look down, from about 35 degrees above, into a large funnel drawn as a fine polar grid, deep blue-black, floating
over a slow palette nebula.  The funnel is the key.  Its floor is home, the tonic chord.  Going round it, the twelve
spokes are the circle of fifths counted from the key: straight ahead at the back is the tonic's own direction, the
dominant (sharp) side is to the right, the subdominant (flat) side to the left and the tritone, the furthest place
from home, nearest you at the front lip.  The tonic spoke is gold, the dominant spoke crimson, the subdominant teal.

Set into the wall are the 24 major and minor chords as small engraved stations, each labelled with its Roman numeral
in the current key (I, V, IV, vi, ii, V/V, bVI ...).  How far up the wall a station sits is how far that chord is
from home, and its angle is how far sharp or flat it has gone.  So in C major, I sits on the floor, V and IV on the
first terraces either side, vi and ii a little higher, the chromatic chords (bVI, V/V, bII) higher still and F sharp
major right up at the front rim.

The harmony being played now is a bright bead on the wall.  Twelve lamps round the rim, one per note name (also
placed by fifths from the key), light up with the notes that sound, and a thread runs from each lit lamp to the bead,
so you can see the notes pulling it.  A recognised chord puts the bead exactly on its station; a single note or a
cluster leaves it wherever its notes pull it.  The bead slides there on a soft spring (it never jumps on a strike) and
etches a fading trail into the surface.  When the music leans on a dominant (a leading tone wanting to rise to the
tonic, a seventh wanting to fall), a gold thread pulls taut from the bead to the floor; when V resolves to I, the bead
slides down and a slow gold ring spreads over the funnel.  A deceptive cadence (V to vi) sends a cool ring out from vi
instead.  When the key changes, the whole funnel turns under the bead (30 degrees per fifth of the move), so the bead
is suddenly up on a wall and rolls down to its new home.  With nothing playing the bead rests on the floor, the funnel
breathes, dust spirals in, and after 20 s of silence a faint ghost bead now and then tours I - IV - V - I.

The theory
----------
Chew's Spiral Array puts every pitch on a helix in 3D: P(k) = (sin k pi/2, cos k pi/2, k h), where k is the pitch's
place on the line of fifths (C 0, G 1, D 2, F -1, Bb -2) and h = sqrt(2/15) the rise per fifth.  A major triad is the
weighted centre of its root, fifth and third, C_M(k) = w1 P(k) + w2 P(k+1) + w3 P(k+4) (minor: P(k-3) for the
third), with w = (0.536, 0.274, 0.19); a key is the weighted centre of its tonic, dominant and subdominant chords
(minor keys mix the major and minor V and iv with alpha = beta = 0.75).  The notes sounding now have a "centre of
effect" CE, the energy-weighted average of their points, each note spelled (C# or Db) on the line of fifths nearest
the music's 8-second running centre (Chew & Chen), so enharmonic flips never throw the bead across the funnel.

The bead's target is X = kappa Q + (1 - kappa) CE: Q is the recognised triad's station and kappa how sure the chord
reader is (cosine match against the 24 triad templates, smoothstep(0.75, 0.95, cos)).  Herremans & Chew's tensile
strain S = |X - T_key| / N0 (N0 = 12 h, the length of one turn of the helix) is the bead's height: rho = S / 0.6, so
the tonic triad sits at S 0.08, V 0.20, vi 0.22, IV 0.23, ii 0.26, V/V 0.32 and F# major 0.56 in C (V really is the
nearest terrace).  The bearing, (X.z - z of the tonic triad) / h, is in fifths and becomes the angle, 30 degrees per
fifth.  The wall is a Plummer well, y = -D / sqrt(1 + (rho / 0.22)^2).

The gold thread is Lerdahl's melodic attraction: each pitch class has an anchoring strength from its degree (tonic 5,
fifth 4, third 3, other scale tones 2, chromatic 1); a note x is attracted to a more stable neighbour y one or two
semitones away by (s_y / s_x) / n^2.  The sum over the sounding notes (G7 in C: B->C 2.5, F->E 1.5, D->C 0.625; a
diminished seventh about 8.6) gives Pi = 1 - exp(-sum / 3), the thread's brightness; its tautness is how close the
bead is to the dominant chord.  A cadence is the chord reader landing on the tonic (or vi) with kappa > 0.7 soon after
Pi was high (> 0.45 within 1.5 s).  The key is the room's (show_service's detector) whenever it has one, else a
Krumhansl-Kessler key finder over this scene's own notes (the shared tonal helper), else C major with the stations at
40% until a key is known.

Macros: energy -> camera yaw drift (4 to 15 degrees), dust density and speed, grid glow, nebula motion, bead halo;
pulse -> ring and grid brightness kick (amplitude only); speed -> dust, yaw and breathing; intensity -> output gain;
palette -> nebula, grid, lamps and trail (the function tints are fixed hues, mixed 70/30 with the palette).

Host diet: per frame one H2D copy of a ~2 KB arena from a pinned ring slot, one event record, a tiny trail kernel
(decay a quarter of the 512x512 heat rows in rotation, splat the bead) and the main kernel (40 march steps + 6
bisections against the well, then the overlays in screen space with bounding-box early-outs) + the present.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.splat3d import look_at, orbit
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.tonal_gravity")

RHO_S = 0.6                            # strain at the rim (rho = S / RHO_S)
R_WORLD = 1.6                          # world radius of rho = 1
CORE = 0.22                            # Plummer core (rho)
R_CYL = 1.7                            # the bounding cylinder (world)
TRAIL_N, TRAIL_EXT = 512, 1.8          # heat texture: TRAIL_N^2 over xz -TRAIL_EXT..TRAIL_EXT (texture frame)
NRING = 4
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FUNC_RGB = np.array([(1.00, 0.74, 0.30),     # tonic: gold
                     (0.20, 0.78, 0.80),     # pre-dominant: teal
                     (0.95, 0.20, 0.30),     # dominant: crimson
                     (0.64, 0.42, 1.00)])    # chromatic: violet
COOL_RGB = (0.45, 0.68, 1.00)
CAM_DIST, CAM_FOV = 4.3, 34.0

# the arena (floats)
O_SC = 96                              # spoke colours: gold, crimson, teal (3 x 3)
O_RG = 112                             # rings: NRING x 8 (age amp cx cz r g b -)
O_LP = 144                             # rim lamps: 12 x 8 (sx sy depth level r g b -)
O_ST = 240                             # stations: 24 x 12 (sx sy depth glyph r g b bright radius label_a - -)
NGT = 9                                # the gold thread: NGT points (sx sy depth) on a curve, bead -> floor
O_GT = O_ST + 24 * 12
N_AR = O_GT + 3 * NGT + 1

_SRC = HELPERS + r"""
#define N_AR """ + str(N_AR) + r"""
#define O_SC """ + str(O_SC) + r"""
#define O_RG """ + str(O_RG) + r"""
#define O_LP """ + str(O_LP) + r"""
#define O_ST """ + str(O_ST) + r"""
#define O_GT """ + str(O_GT) + r"""
#define NGT """ + str(NGT) + r"""
#define TRAIL_N """ + str(TRAIL_N) + r"""
#define TRAIL_EXT """ + repr(float(TRAIL_EXT)) + r"""f
#define R_CYL """ + repr(float(R_CYL)) + r"""f
#define R_WORLD """ + repr(float(R_WORLD)) + r"""f
#define TWO_PI 6.2831853f
#define PI_F 3.14159265f
#define NOISE 256
#define MARCH 40
#define BISECT 6

// bilinear sample of the tiling noise texture (NOISE x NOISE floats 0..1)
__device__ __forceinline__ float noise_tex(const float* __restrict__ Tx, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const float fu = floorf(u), fv = floorf(v);
    const int x0 = ((int)fu) & (NOISE - 1), y0 = ((int)fv) & (NOISE - 1);
    const int x1 = (x0 + 1) & (NOISE - 1), y1 = (y0 + 1) & (NOISE - 1);
    const float tx = u - fu, ty = v - fv;
    const float a = Tx[y0 * NOISE + x0], b = Tx[y0 * NOISE + x1], c = Tx[y1 * NOISE + x0], d = Tx[y1 * NOISE + x1];
    return (1.f - ty) * ((1.f - tx) * a + tx * b) + ty * ((1.f - tx) * c + tx * d);
}
__device__ __forceinline__ float3 pal3(float t, const float* c) {
    return make_float3(clamp01(c[0] + c[3] * __cosf(TWO_PI * (c[6] * t + c[9]))),
                       clamp01(c[1] + c[4] * __cosf(TWO_PI * (c[7] * t + c[10]))),
                       clamp01(c[2] + c[5] * __cosf(TWO_PI * (c[8] * t + c[11]))));
}
// coverage of a line of half width hw at distance d (a 1 px box filter)
__device__ __forceinline__ float line_cov(float d, float hw) { return clamp01(hw + 0.5f - d); }
// the well's height at world radius r: -D / sqrt(1 + (r / a)^2)   (P[27] D, P[28] 1/a)
__device__ __forceinline__ float well(const float* P, float r) {
    const float q = r * P[28];
    return -P[27] * rsqrtf(1.f + q * q);
}
// the rings' ripple (height; and its gradient when g != 0) at world xz
__device__ float ripple(const float* P, float x, float z, int n, float* gx, float* gz) {
    float h = 0.f, ax = 0.f, az = 0.f;
    for (int i = 0; i < n; ++i) {
        const float* R = P + O_RG + 8 * i;
        const float age = R[0];
        const float env = R[1] * smooth(0.f, 0.15f, age) * __expf(-age / 1.2f);
        if (env < 1e-3f) continue;
        const float dx = x - R[2], dz = z - R[3];
        const float d = sqrtf(dx * dx + dz * dz) + 1e-5f;
        const float u = (d - 0.8f * age) * (1.f / 0.06f);
        if (fabsf(u) > 3.5f) continue;
        const float g = __expf(-u * u), cs = __cosf(1.6f * u), sn = __sinf(1.6f * u);
        h += env * g * cs;
        const float dw = env * g * (-2.f * u * cs - 1.6f * sn) * (1.f / 0.06f);
        ax += dw * dx / d; az += dw * dz / d;
    }
    const float A = P[71];
    if (gx) { *gx = A * ax; *gz = A * az; }
    return A * h;
}
__device__ __forceinline__ float g_at(const float* P, float ox, float oy, float oz, float3 rd, float t, int nring) {
    const float x = ox + t * rd.x, z = oz + t * rd.z;
    float h = well(P, sqrtf(x * x + z * z));
    if (nring > 0) h += ripple(P, x, z, nring, 0, 0);
    return oy + t * rd.y - h;
}
// distance from p to the segment a-b; *h = the parameter along it (0..1)
__device__ __forceinline__ float seg_dist(float px, float py, float ax, float ay, float bx, float by, float* h) {
    const float pax = px - ax, pay = py - ay, bax = bx - ax, bay = by - ay;
    const float t = clamp01((pax * bax + pay * bay) / fmaxf(bax * bax + bay * bay, 1e-4f));
    const float qx = pax - bax * t, qy = pay - bay * t;
    *h = t;
    return sqrtf(qx * qx + qy * qy);
}

// the background: a palette nebula in direction space, lensed toward the funnel's axis
__device__ float3 nebula(const float* P, float ox, float oz, float3 rd, const float* __restrict__ tex,
                         unsigned int seed) {
    const float a = rd.x * rd.x + rd.z * rd.z;
    const float tc = fmaxf(-(ox * rd.x + oz * rd.z) / fmaxf(a, 1e-6f), 0.f);
    const float cx = ox + tc * rd.x, cz = oz + tc * rd.z;
    const float rc = sqrtf(cx * cx + cz * cz) + 1e-4f;
    // a lens: bent toward the axis by ~ lens / b, smoothly to nothing inside the core (no seam at b = 0)
    const float k = P[84] * a / (rc * rc + 0.09f);   // steep rays (small a) barely bend
    float3 d = make_float3(rd.x - k * cx, rd.y, rd.z - k * cz);
    const float il = rsqrtf(d.x * d.x + d.y * d.y + d.z * d.z);
    d.x *= il; d.y *= il; d.z *= il;
    const float den = 1.f / (0.5f + fabsf(d.y));
    const float u = d.x * den * 150.f, v = d.z * den * 150.f;
    const float n = 0.55f * noise_tex(tex, u + P[42], v + P[43])
                  + 0.30f * noise_tex(tex, 2.13f * u - 1.7f * P[43] + 31.f, 2.13f * v + 1.3f * P[42] + 17.f)
                  + 0.15f * noise_tex(tex, 4.37f * u + 2.9f * P[42] + 77.f, 4.37f * v - 2.3f * P[43] + 5.f);
    const float3 c = pal3(n * P[44] + P[45] + 0.25f, P);
    const float m = smooth(0.38f, 0.92f, n);
    const float lum = 0.016f + 0.24f * m * m * m;
    float3 o = make_float3(P[78] + lum * c.x, P[79] + lum * c.y, P[80] + lum * c.z);
    // sparse stars, a little twinkle
    const float su = u * 0.9f, sv = v * 0.9f;
    const float iu = floorf(su), iv = floorf(sv);
    const unsigned int hs = hash_u(((unsigned int)(int)iu * 73856093u) ^ ((unsigned int)(int)iv * 19349663u) ^ seed);
    if ((hs >> 8) * (1.f / 16777216.f) > 0.985f) {
        const float fx = su - iu - 0.5f, fy = sv - iv - 0.5f;
        const float tw = 0.6f + 0.4f * __sinf(TWO_PI * ((hs & 255u) * (1.f / 256.f)) + 1.3f * P[38]);
        const float s = 0.5f * tw * __expf(-(fx * fx + fy * fy) * 900.f);
        o.x += s; o.y += s; o.z += 1.1f * s;
    }
    return o;
}

// P: [0..11]palette [12]W [13]H [14..16]eye [17..19]fwd [20..22]right [23..25]up [26]focal px [27]D [28]1/a
//    [29,30]cos, sin theta (world -> key frame) [31,32]cos, sin psi (world -> trail texture) [33]energy [34]kick
//    [35]gain [36]grid [37]labels [38]clock [39]dust radial phase [40]dust spin [41]dust threshold
//    [42,43]nebula offsets [44]palette period [45]drift [46]Pi [47]taut [48..50]bead world [51..53]bead sx sy depth
//    [54]bead lum [55]bead halo px [56..58]bead rgb [59..61]floor sx sy depth [62]ghost alpha [63..65]ghost sx sy
//    depth [66..68]ghost rgb [69]px scale [70]rings [71]ripple amp [72..74]grid tint [75..77]surface base
//    [78..80]background [81]glyph w [82]glyph h [83]home glow [84]lens [85]march top y [86]march bottom y
//    [90]trail gain [91]lamp threads gain [92]grid swell phase
extern "C" __global__
void gravity(uchar4* __restrict__ out, const float* __restrict__ A, const float* __restrict__ heat,
             const float* __restrict__ tex, const unsigned char* __restrict__ atlas, const unsigned int seed)
{
    __shared__ float S[N_AR];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nth = blockDim.x * blockDim.y;
    for (int i = tid; i < N_AR; i += nth) S[i] = A[i];
    __syncthreads();
    const float* P = S;
    const int W = (int)P[12], H = (int)P[13];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float f = P[26];
    const float ux = (px - 0.5f * W) / f, uy = (0.5f * H - py) / f;
    float3 rd = make_float3(P[17] + ux * P[20] + uy * P[23], P[18] + ux * P[21] + uy * P[24],
                            P[19] + ux * P[22] + uy * P[25]);
    {
        const float il = rsqrtf(rd.x * rd.x + rd.y * rd.y + rd.z * rd.z);
        rd.x *= il; rd.y *= il; rd.z *= il;
    }
    const float ox = P[14], oy = P[15], oz = P[16];
    const float e = P[33], kick = P[34], sc = P[69];
    const int nring = (int)P[70];

    // ---- the well: march g(t) = ray.y - y(r(t)) inside the bounding cylinder, then bisect ----------------------
    float thit = -1.f;
    {
        const float a = rd.x * rd.x + rd.z * rd.z, b = ox * rd.x + oz * rd.z, c = ox * ox + oz * oz - R_CYL * R_CYL;
        const float disc = b * b - a * c;
        if (a > 1e-8f && disc > 0.f && rd.y < -1e-4f) {
            const float sq = sqrtf(disc);
            float t0 = (-b - sq) / a, t1 = (-b + sq) / a;
            t0 = fmaxf(fmaxf(t0, (P[85] - oy) / rd.y), 0.f);
            t1 = fminf(t1, (P[86] - oy) / rd.y);
            if (t1 > t0 && g_at(P, ox, oy, oz, rd, t0, nring) >= 0.f) {
                const float dtm = (t1 - t0) * (1.f / MARCH);
                float lo = t0;
                for (int i = 1; i <= MARCH; ++i) {
                    const float t = t0 + dtm * i;
                    if (g_at(P, ox, oy, oz, rd, t, nring) < 0.f) {
                        float hi = t;
                        lo = t - dtm;
                        for (int j = 0; j < BISECT; ++j) {
                            const float m = 0.5f * (lo + hi);
                            if (g_at(P, ox, oy, oz, rd, m, nring) < 0.f) hi = m; else lo = m;
                        }
                        thit = 0.5f * (lo + hi);
                        break;
                    }
                }
            }
        }
    }

    float3 col;
    float zhit = 1e30f;                                   // camera depth of the surface here (overlay occlusion)
    if (thit > 0.f) {
        const float hx = ox + thit * rd.x, hy = oy + thit * rd.y, hz = oz + thit * rd.z;
        zhit = thit * (rd.x * P[17] + rd.y * P[18] + rd.z * P[19]);
        const float r = sqrtf(hx * hx + hz * hz) + 1e-6f, rho = r * (1.f / R_WORLD);
        const float ia = P[28], q2 = 1.f + r * r * ia * ia;
        const float dydr = P[27] * r * ia * ia * rsqrtf(q2) / q2;
        float gx = dydr * hx / r, gz = dydr * hz / r;
        if (nring > 0) {
            float rx, rz;
            ripple(P, hx, hz, nring, &rx, &rz);
            gx += rx; gz += rz;
        }
        float3 n = make_float3(-gx, 1.f, -gz);
        {
            const float il = rsqrtf(n.x * n.x + 1.f + n.z * n.z);
            n.x *= il; n.y *= il; n.z *= il;
        }
        const float ndv = fabsf(n.x * rd.x + n.y * rd.y + n.z * rd.z);
        const float fr = 1.f - ndv, fres = fr * fr * fr * fr;
        // dark glassy base: a soft key light from above and behind, a Fresnel sheen at grazing angles
        const float key = clamp01(0.55f * n.y + 0.45f * (n.x * 0.f + n.y * 0.6f - n.z * 0.8f));
        const float deep = (1.f - clamp01(rho)) * (1.f - clamp01(rho));
        col.x = P[75] * (0.55f + 0.9f * key) + 0.05f * deep * P[72] + 0.30f * fres * P[72];
        col.y = P[76] * (0.55f + 0.9f * key) + 0.05f * deep * P[73] + 0.30f * fres * P[73];
        col.z = P[77] * (0.55f + 0.9f * key) + 0.05f * deep * P[74] + 0.30f * fres * P[74];
        // home: a soft gold glow on the floor
        const float home = P[83] * __expf(-rho * rho * (1.f / 0.006f));
        col.x += home * P[O_SC]; col.y += home * P[O_SC + 1]; col.z += home * P[O_SC + 2];

        // grid: twelve spokes (the circle of fifths from the key) and rings every 0.1 rho
        const float fp = thit / f / fmaxf(ndv, 0.2f);       // a pixel's size on the surface (world)
        const float lx = hx * P[29] + hz * P[30], lz = -hx * P[30] + hz * P[29];
        const float phi = atan2f(-lx, lz);                  // 0: the tonic (back), +: sharp side (right)
        const float s = phi * (6.f / PI_F);
        const float si = rintf(s);
        const int spoke = (((int)si) % 12 + 12) % 12;
        const float dsp = fabsf(s - si) * (PI_F / 6.f) * r;
        const float rq = rho * 10.f, ri = rintf(rq);
        const float drg = fabsf(rq - ri) * (R_WORLD * 0.1f) * sqrtf(1.f + dydr * dydr);
        const float gamp = P[36] * (0.75f + 0.5f * e) * (1.f + 1.4f * kick);
        if (gamp > 0.f) {
            const bool special = spoke == 0 || spoke == 1 || spoke == 11;
            const float wl = special ? 0.0030f : 0.0017f;
            const float hw = fmaxf(wl, 0.6f * fp);
            const float cs = clamp01((hw - dsp) / fp + 0.5f) * (wl / hw);
            const float sfade = smooth(0.025f, 0.11f, rho) * (1.f - smooth(0.96f, 1.05f, rho));
            const float sk = gamp * cs * sfade * (special ? 0.9f : 0.6f);
            float3 sc3 = make_float3(P[72], P[73], P[74]);
            if (spoke == 0) sc3 = make_float3(P[O_SC], P[O_SC + 1], P[O_SC + 2]);
            else if (spoke == 1) sc3 = make_float3(P[O_SC + 3], P[O_SC + 4], P[O_SC + 5]);
            else if (spoke == 11) sc3 = make_float3(P[O_SC + 6], P[O_SC + 7], P[O_SC + 8]);
            col.x += sk * sc3.x; col.y += sk * sc3.y; col.z += sk * sc3.z;
            const float wr = ri >= 10.f ? 0.0030f : 0.0016f;
            const float hr = fmaxf(wr, 0.6f * fp);
            const float cr = clamp01((hr - drg) / fp + 0.5f) * (wr / hr);
            const float rk = gamp * cr * (ri >= 1.f && ri <= 10.f ? (ri >= 10.f ? 0.9f : 0.5f) : 0.f)
                           * (1.f - smooth(0.98f, 1.06f, rho))
                           * (0.7f + 0.6f * (0.5f + 0.5f * __sinf(TWO_PI * (2.5f * rho + P[92]))));   // a slow inward swell
            col.x += rk * P[72]; col.y += rk * P[73]; col.z += rk * P[74];
            // the glass glows faintly along the rings (deeper rings closer together: the funnel's shape)
            const float gl = gamp * 0.05f * __expf(-drg / 0.012f) * (1.f - smooth(0.9f, 1.05f, rho));
            col.x += gl * P[72]; col.y += gl * P[73]; col.z += gl * P[74];
        }

        // the trail: heat in the funnel's own frame (it turns with the funnel)
        if (P[90] > 0.f) {
            const float tx = hx * P[31] - hz * P[32], tz = hx * P[32] + hz * P[31];
            const float sc_t = TRAIL_N / (2.f * TRAIL_EXT);
            const float u = (tx + TRAIL_EXT) * sc_t - 0.5f, v = (tz + TRAIL_EXT) * sc_t - 0.5f;
            if (u > 0.f && v > 0.f && u < TRAIL_N - 1.f && v < TRAIL_N - 1.f) {
                const int x0 = (int)u, y0 = (int)v;
                const float fx = u - x0, fy = v - y0;
                const float* hp = heat + y0 * TRAIL_N + x0;
                const float h = (1.f - fy) * ((1.f - fx) * hp[0] + fx * hp[1])
                              + fy * ((1.f - fx) * hp[TRAIL_N] + fx * hp[TRAIL_N + 1]);
                if (h > 1e-4f) {
                    const float hv = 1.f - __expf(-1.1f * h);
                    const float3 c = pal3(0.62f + 0.22f * hv * P[44] + P[45], P);
                    const float k = P[90] * hv;
                    col.x += k * (0.25f + 0.75f * c.x); col.y += k * (0.25f + 0.75f * c.y);
                    col.z += k * (0.25f + 0.75f * c.z);
                }
            }
        }

        // rings: an annulus spreading at 0.8 / s, blooming in over 0.15 s and easing out
        for (int i = 0; i < nring; ++i) {
            const float* R = P + O_RG + 8 * i;
            const float age = R[0];
            const float env = R[1] * smooth(0.f, 0.15f, age) * __expf(-age / 1.2f) * (1.f + 0.5f * kick);
            if (env < 1e-3f) continue;
            const float dx = hx - R[2], dz = hz - R[3];
            const float u = (sqrtf(dx * dx + dz * dz) - 0.8f * age) * (1.f / 0.06f);
            const float k = env * (0.9f * __expf(-u * u * 2.f) + 0.25f * __expf(-fabsf(u) * 0.6f));
            col.x += k * R[4]; col.y += k * R[5]; col.z += k * R[6];
        }

        // the bead lights the wall around it
        {
            const float dx = hx - P[48], dy = hy - P[49], dz = hz - P[50];
            const float k = P[54] * 0.32f * __expf(-(dx * dx + dy * dy + dz * dz) * (1.f / 0.035f));
            col.x += k * P[56]; col.y += k * P[57]; col.z += k * P[58];
        }

        // dust: sparkles in log-polar cells, each ring of cells turning, all drifting inward
        if (rho > 0.04f) {
            const float lu = 7.f * __logf(rho) + P[39];
            const float iu = floorf(lu);
            const unsigned int ru = ((unsigned int)(int)iu) & 4095u;
            const float kr = 1.f + floorf(hash1(ru, 7u, seed) * 2.999f);
            const float phw = atan2f(-hx, hz);
            const float av = (phw + kr * P[40]) * (64.f / TWO_PI);
            const float iv = floorf(av);
            const unsigned int cv = ((unsigned int)(int)iv) & 63u;
            const float hh = hash1(ru, cv, seed + 3u);
            if (hh > P[41]) {
                const float ou = 0.2f + 0.6f * hash1(ru, cv, seed + 5u), ov = 0.2f + 0.6f * hash1(ru, cv, seed + 9u);
                const float wu = (lu - iu - ou) * (R_WORLD * rho / 7.f) * sqrtf(1.f + dydr * dydr);
                const float wv = (av - iv - ov) * (TWO_PI / 64.f) * r;
                const float rad = fmaxf(0.0045f, 0.8f * fp);
                const float d2 = (wu * wu + wv * wv) / (rad * rad);
                if (d2 < 9.f) {
                    const float tw = 0.5f + 0.5f * __sinf(TWO_PI * hash1(ru, cv, seed + 11u) + 2.1f * P[38]);
                    const float fade = smooth(0.04f, 0.14f, rho) * (1.f - smooth(0.9f, 1.04f, rho));
                    const float k = (0.5f + 0.6f * e) * tw * fade * __expf(-d2) * (0.0045f / rad);
                    col.x += k * (0.6f + 0.4f * P[72]); col.y += k * (0.6f + 0.4f * P[73]);
                    col.z += k * (0.7f + 0.3f * P[74]);
                }
            }
        }

        // the rim dissolves into the nebula
        const float alpha = 1.f - smooth(0.9f, 1.06f, rho);
        if (alpha < 1.f) {
            const float3 bg = nebula(P, ox, oz, rd, tex, seed);
            col.x = bg.x + (col.x - bg.x) * alpha; col.y = bg.y + (col.y - bg.y) * alpha;
            col.z = bg.z + (col.z - bg.z) * alpha;
        }
    } else {
        col = nebula(P, ox, oz, rd, tex, seed);
    }

    // ---- overlays (screen space): threads, lamps, stations, the bead -----------------------------------------
    #define OCC(dep) (1.f - 0.75f * smooth(0.03f, 0.14f, (dep) - zhit))
    const float bx = P[51], by = P[52], bd = P[53];
    // threads from the lit rim lamps to the bead
    if (P[91] > 0.f) {
        const float thw = 0.5f * sc, tsig = 2.2f * sc * (0.8f + 0.4f * e), reach = 9.f * tsig;
        for (int i = 0; i < 12; ++i) {
            const float* L = P + O_LP + 8 * i;
            const float lv = L[3];
            if (lv < 0.01f) continue;
            if (px < fminf(L[0], bx) - reach || px > fmaxf(L[0], bx) + reach ||
                py < fminf(L[1], by) - reach || py > fmaxf(L[1], by) + reach) continue;
            float h;
            const float d = seg_dist(px, py, L[0], L[1], bx, by, &h);
            if (d > reach) continue;
            const float w = P[91] * lv * OCC(L[2] + (bd - L[2]) * h) * (0.55f + 0.45f * (1.f - h));
            const float k = w * (0.55f * line_cov(d, thw) + 0.22f * __expf(-d / tsig));
            col.x += k * L[4]; col.y += k * L[5]; col.z += k * L[6];
        }
    }
    // the gold thread from the bead to the floor (a curve bowed up when slack, straight when taut)
    if (P[46] > 0.01f) {
        const float pi = P[46];
        const float thw = (0.35f + 1.1f * pi) * sc, tsig = (2.f + 3.f * pi) * sc, reach = 9.f * tsig + thw;
        const float* G = P + O_GT;
        float x0 = 1e30f, y0 = 1e30f, x1 = -1e30f, y1 = -1e30f;
        for (int j = 0; j < NGT; ++j) {
            x0 = fminf(x0, G[3 * j]); x1 = fmaxf(x1, G[3 * j]); y0 = fminf(y0, G[3 * j + 1]); y1 = fmaxf(y1, G[3 * j + 1]);
        }
        if (px > x0 - reach && px < x1 + reach && py > y0 - reach && py < y1 + reach) {
            float d = 1e30f, dep = 0.f;
            for (int j = 0; j < NGT - 1; ++j) {
                float h;
                const float dj = seg_dist(px, py, G[3 * j], G[3 * j + 1], G[3 * j + 3], G[3 * j + 4], &h);
                if (dj < d) { d = dj; dep = G[3 * j + 2] + (G[3 * j + 5] - G[3 * j + 2]) * h; }
            }
            const float k = pi * OCC(dep) * (1.0f * line_cov(d, thw) + 0.35f * __expf(-d / tsig));
            const float wh = 0.35f * pi * line_cov(d, 0.4f * thw);
            col.x += k * P[O_SC] + wh; col.y += k * P[O_SC + 1] + wh; col.z += k * P[O_SC + 2] + wh;
        }
    }
    // the rim lamps
    for (int i = 0; i < 12; ++i) {
        const float* L = P + O_LP + 8 * i;
        const float dx = px - L[0], dy = py - L[1];
        const float d2 = dx * dx + dy * dy;
        const float reach = 46.f * sc;
        if (d2 > reach * reach) continue;
        const float lv = L[3], d = sqrtf(d2);
        const float rc = sc * (2.6f + 3.2f * lv);
        const float occ = OCC(L[2]) * (1.f + 0.6f * kick);
        const float k = occ * ((0.22f + 0.9f * lv) * clamp01(rc - d + 0.5f)
                               + (0.05f + 0.55f * lv) * __expf(-fmaxf(d - rc, 0.f) / (sc * (4.f + 7.f * lv))));
        const float hot = occ * lv * 0.8f * clamp01(0.5f * rc - d + 0.5f);
        col.x += k * L[4] + hot; col.y += k * L[5] + hot; col.z += k * L[6] + hot;
    }
    // the stations: engraved discs with their Roman numerals
    {
        const int lw = (int)P[81], lh = (int)P[82];
        const float la = P[37];
        for (int i = 0; i < 24; ++i) {
            const float* Q = P + O_ST + 12 * i;
            const float rad = Q[8];
            const float reach = rad + 26.f * sc;
            const float top = Q[1] - rad - 3.f * sc - lh;
            if (px < Q[0] - fmaxf(reach, 0.5f * lw + 1.f) || px > Q[0] + fmaxf(reach, 0.5f * lw + 1.f) ||
                py < fminf(top, Q[1] - reach) || py > Q[1] + reach) continue;
            const float dx = px - Q[0], dy = py - Q[1];
            const float d = sqrtf(dx * dx + dy * dy);
            const float br = Q[7], occ = OCC(Q[2]) * (1.f + 0.4f * kick);
            const float ring = (0.3f + 0.5f * br) * la * line_cov(fabsf(d - rad), 0.55f * sc);
            const float core = br * clamp01(0.5f * rad - d + 0.5f);
            const float halo = 0.5f * br * br * __expf(-fmaxf(d - rad, 0.f) / (5.f * sc));
            float k = occ * (ring + 0.9f * core + halo);
            float wh = occ * 0.35f * br * br * core;
            // the numeral, centred above the disc
            if (lw > 0 && Q[9] > 0.003f) {
                const int gx = (int)floorf(px - (Q[0] - 0.5f * lw));
                const int gy = (int)floorf(py - top);
                if (gx >= 0 && gx < lw && gy >= 0 && gy < lh) {
                    const int ai = ((int)Q[3] * lh + gy) * lw + gx;
                    const float a = atlas[ai] * (1.f / 255.f), gl = atlas[ai + 48 * lw * lh] * (1.f / 255.f);
                    const float ka = occ * Q[9] * a;
                    k += 1.1f * ka + 0.45f * occ * Q[9] * gl; wh += 0.6f * ka;
                }
            }
            col.x += k * Q[4] + wh; col.y += k * Q[5] + wh; col.z += k * Q[6] + wh;
        }
    }
    // the ghost bead (idle)
    if (P[62] > 0.003f) {
        const float dx = px - P[63], dy = py - P[64];
        const float d = sqrtf(dx * dx + dy * dy);
        const float rc = 5.f * sc;
        const float k = P[62] * OCC(P[65]) * (clamp01(rc - d + 0.5f) + 0.8f * __expf(-fmaxf(d - rc, 0.f) / (9.f * sc)));
        col.x += k * P[66]; col.y += k * P[67]; col.z += k * P[68];
    }
    // the bead: core, hot centre, halo
    {
        const float dx = px - bx, dy = py - by;
        const float d = sqrtf(dx * dx + dy * dy);
        const float rc = sc * (6.f + 1.5f * e);
        const float occ = OCC(bd);
        const float core = clamp01(rc - d + 0.5f);
        const float halo = P[54] * (0.5f * __expf(-fmaxf(d - rc, 0.f) / P[55]) + 0.25f * __expf(-d / (3.f * P[55])));
        const float k = occ * (P[54] * core * (0.8f + 0.6f * d / rc) + halo);
        const float hot = occ * P[54] * 1.2f * core * __expf(-d * d / (0.3f * rc * rc + 1.f));
        col.x += k * P[56] + hot; col.y += k * P[57] + hot; col.z += k * P[58] + hot;
    }

    const float gn = 255.f * P[35];
    const float dth = (hash_u((unsigned int)(y * W + x) ^ (seed * 0x9E3779B1u)) >> 8) * (1.f / 16777216.f) - 0.5f;
    out[y * W + x] = pack(make_float4(gn * (1.f - __expf(-1.15f * fmaxf(col.x, 0.f))) + dth,
                                      gn * (1.f - __expf(-1.15f * fmaxf(col.y, 0.f))) + dth,
                                      gn * (1.f - __expf(-1.15f * fmaxf(col.z, 0.f))) + dth, 255.f));
}

// the trail: decay one row in four (in rotation), splat the bead (a Gaussian, sigma 2 texels)
extern "C" __global__
void trail(float* __restrict__ heat, const float bu, const float bv, const float amp, const float decay, const int phase)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= TRAIL_N || y >= TRAIL_N) return;
    const int i = y * TRAIL_N + x;
    float h = heat[i];
    if ((y & 3) == phase) h *= decay;
    const float dx = x - bu, dy = y - bv;
    const float d2 = dx * dx + dy * dy;
    if (amp > 0.f && d2 < 64.f) h += amp * __expf(-d2 * 0.125f);
    heat[i] = fminf(fmaxf(h, 0.f), 6.f);
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")
_RING = 16                             # pinned upload slots
_logged: set = set()


def _log_once(key: str, msg: str, *args) -> None:
    if key not in _logged:
        _logged.add(key)
        log.warning(msg, *args)


def _pal(t: float, c) -> np.ndarray:
    tp = 2.0 * math.pi
    return np.array([min(max(c[i] + c[3 + i] * math.cos(tp * (c[6 + i] * t + c[9 + i])), 0.0), 1.0)
                     for i in range(3)])


def _noise_texture(seed: int, n: int = 256) -> np.ndarray:
    """A tiling smooth noise in 0..1 (a sum of random low-frequency waves)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:n, 0:n] / n
    f = np.zeros((n, n))
    for octave, amp in ((1, 1.0), (2, 0.6), (3, 0.45), (5, 0.3), (8, 0.18)):
        for _ in range(3):
            kx, ky = rng.integers(-octave, octave + 1, 2)
            if kx == 0 and ky == 0:
                kx = octave
            f += amp * np.cos(2 * np.pi * (kx * x + ky * y) + rng.uniform(0, 2 * np.pi))
    f -= f.min()
    f /= max(f.max(), 1e-9)
    return f.astype(np.float32)


def glyph_labels() -> list[str]:
    """The 48 station numerals: [major key, minor key] x [major triads, minor triads] x semitones above the tonic."""
    out = []
    for key in (0, 12):
        for q in ("maj", "min"):
            out += [T.roman(r, q, key, unicode=True) for r in range(12)]
    return out


def bake_glyphs(height: float) -> tuple[np.ndarray, int, int]:
    """The 48 numerals as a uint8 atlas [2, 48, lh, lw] (the numerals, then their soft glow); (empty, 0, 0) without a font."""
    labels = glyph_labels()
    try:
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype(FONT, max(8, int(round(height * 0.72))))
        lh = max(8, int(round(height)))
        from PIL import ImageFilter
        pad = max(3, int(round(0.2 * lh)))
        lw = max(8, int(math.ceil(max(font.getlength(s) for s in labels))) + 2 * pad)
        out = np.zeros((2, len(labels), lh, lw), np.uint8)       # plane 0 the numerals, plane 1 their glow
        for i, s in enumerate(labels):
            img = Image.new("L", (lw, lh), 0)
            ImageDraw.Draw(img).text((lw / 2, lh / 2), s, fill=255, font=font, anchor="mm")
            out[0, i] = np.asarray(img, np.uint8)
            g = np.asarray(img.filter(ImageFilter.GaussianBlur(0.12 * lh)), np.float32)
            out[1, i] = np.clip(g * (255.0 / max(float(g.max()), 1.0)), 0, 255).astype(np.uint8)
        return out, lw, lh
    except Exception as exc:                          # degrade: no numerals
        _log_once("font", "tonal_gravity: cannot bake the numerals (%s); drawing none", exc)
        return np.zeros((2, 48, 1, 1), np.uint8), 0, 0


def _wrap(a: float) -> float:
    """An angle wrapped to (-pi, pi]."""
    return a - 2.0 * math.pi * math.ceil((a - math.pi) / (2.0 * math.pi))


class KeyLayout:
    """Everything the scene draws that depends on the key: the 24 stations' (rho, local angle), their function
    colours and numeral glyph ids, the rim lamps' local angles, Lerdahl's attraction weights by pitch class."""

    def __init__(self, key: int) -> None:
        self.key = key
        self.sk = T.SpiralKey(key)
        tonic, minor = key % 12, key >= 12
        self.tonic, self.minor = tonic, minor
        self.rho = np.clip(self.sk.strain / RHO_S, 0.0, 1.0)
        self.ang = np.array([_wrap(math.radians(30.0 * b)) for b in self.sk.bearing])
        fn = np.array([T.function_class(t % 12, "min" if t >= 12 else "maj", key) for t in range(24)])
        self.fn = fn
        rel = (np.arange(24) % 12 - tonic) % 12
        self.glyph = (24 if minor else 0) + 12 * (np.arange(24) >= 12) + rel
        self.lamp_ang = np.array([math.radians(30.0 * T.fifths_of(pc - tonic)) for pc in range(12)])
        self.lamp_fifths = np.array([T.fifths_of(pc - tonic) for pc in range(12)], np.float64)
        # Lerdahl's anchoring strength by degree, then each pc's attraction to a more stable neighbour
        third = 3 if minor else 4
        scale = T.MINOR_SCALE if minor else T.MAJOR_SCALE
        s = np.ones(12)
        for pc in range(12):
            d = (pc - tonic) % 12
            s[pc] = 5.0 if d == 0 else 4.0 if d == 7 else 3.0 if d == third else 2.0 if d in scale else 1.0
        att = np.zeros(12)
        for pc in range(12):
            best = 0.0
            for step in (1, -1, 2, -2):
                sy = s[(pc + step) % 12]
                if sy > s[pc]:
                    best = max(best, (sy / s[pc]) / (step * step))
            att[pc] = best
        self.attraction = att
        self.tonic_tid = T.triad_id(tonic, minor)
        self.vi_tid = T.triad_id((tonic + 8) % 12, False) if minor else T.triad_id((tonic + 9) % 12, True)
        self.iv_tid = T.triad_id((tonic + 5) % 12, minor)
        self.v_tid = T.triad_id((tonic + 7) % 12, False)


class TonalGravity(Source):
    """The key as a gravity well (module docstring).  Host: the chord, the key, the Spiral Array maths, the bead's
    spring, the rings and the projections (~0.2-0.4 ms); GPU: the trail kernel, the main kernel, the present."""
    name = "tonal_gravity"
    label = "Tonal gravity"
    description = ("The key as a glowing gravity well: the harmony is a bead on the funnel wall whose height is its "
                   "distance from home and whose angle is how far sharp or flat it has gone, falling to the tonic at "
                   "every cadence.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="dusk", render_scale=0.5, period=1.0, drift=0.02),
            "well_depth": num(0.3, 1.5, 0.9, "Well depth", basic=True),
            "tilt": num(20.0, 60.0, 35.0, "Tilt", unit="°"),
            "grid": num(0.0, 1.0, 0.7, "Grid"),
            "labels": num(0.0, 1.0, 0.85, "Numerals"),
            "chord_window": num(0.2, 1.5, 0.5, "Chord window", unit="s"),
            "trail_s": num(5.0, 60.0, 20.0, "Trail", unit="s", basic=True),
        }

    # -- setup ----------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.heat = cp.zeros(TRAIL_N * TRAIL_N, cp.float32)
        mod = raw_module(_SRC, _OPTS)
        self.k_main = mod.get_function("gravity")
        self.k_trail = mod.get_function("trail")
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.tex = cp.asarray(_noise_texture(self.seed))
        self.sc = self.h / 1080.0
        atlas, self.lw, self.lh = bake_glyphs(max(0.030 * self.h, 14.0))
        self.atlas = cp.asarray(np.ascontiguousarray(atlas).ravel())
        self.arena = cp.zeros(N_AR, cp.float32)
        try:
            self._pin = cp.cuda.alloc_pinned_memory(_RING * N_AR * 4)
            host = np.frombuffer(self._pin, np.float32, _RING * N_AR).reshape(_RING, N_AR)
        except Exception as exc:                          # pageable works too
            _log_once("pin", "tonal_gravity: no pinned memory (%s); uploading from pageable memory", exc)
            self._pin = None
            host = np.zeros((_RING, N_AR), np.float32)
        host[:] = 0.0
        self._spare = np.zeros(N_AR, np.float32)
        self._host = [host[i] for i in range(_RING)] + [self._spare]
        self._ev = [cp.cuda.Event(block=False, disable_timing=True, interprocess=False) for _ in range(_RING)]
        self._slot = 0

        # listening
        self.e_raw = np.zeros(12, np.float32)
        self.ease = T.AttackRelease(12, 0.12, float(params["chord_window"]))
        self.keyr = T.KeyResolver(hold_s=0.5)
        self.chords = T.ChordReader(rate_hz=15)
        self.ks = np.zeros(12, np.int64)
        self.ce = np.zeros(3)
        self.X = np.zeros(3)
        self.lay = KeyLayout(0)
        self.ce_long = self.lay.sk.point.copy()
        # stations shown (blended across a key change) and the blend's start
        self.st_rho_from = self.lay.rho.copy()
        self.st_ang_from = self.lay.ang.copy()
        self.st_rgb_from = FUNC_RGB[self.lay.fn].copy()
        self.glyph_from = self.lay.glyph.copy()
        self.blend_t = 1e9                                # s since the last key change
        self.theta_from = 0.0                             # the world turn at the change (eases to 0)
        self.trail_rot = 0.0                              # A: the texture frame's accumulated turn
        self.st_rho = self.st_rho_from.copy()
        self.st_ang = self.st_ang_from.copy()
        self.st_rgb = self.st_rgb_from.copy()
        self.st_hl = T.AttackRelease(24, 0.12, 0.5)       # the current chord's station highlight
        self._hl_in = np.zeros(24)
        self.known_k = 0.4                                # stations at 40% until a key is known
        # the bead: a critically damped spring in world xz
        self.bead = np.zeros(2)
        self.bead_v = np.zeros(2)
        self.pi = 0.0
        self.pi_hist = np.zeros(16)
        self.pi_t = 0.0
        self.pi_i = 0
        self.rings = np.zeros((NRING, 8))
        self.rings[:, 0] = 1e9
        self.silent_s = 0.0
        self.loud = 0.0
        self.bead_rgb = np.array([1.0, 0.92, 0.8])
        self.trail_phase = 0
        self.pts = np.zeros((39 + NGT, 3))
        self._bz = np.linspace(0.0, 1.0, NGT)
        self.swell = 0.0
        self.clock = self.breath = self.yaw_ph = self.dust_in = self.dust_spin = self.drift = self.kick = 0.0
        self.neb = np.zeros(2)

    # -- the music ------------------------------------------------------------------------------------------------
    def _listen(self, dt: float, window: float) -> None:
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        n = getattr(f, "notes", None) if f is not None else None
        key = mode = None
        bass = -1
        if n is not None and getattr(n, "shape", None) == (128, 4):
            T.pc_energy(n, self.e_raw)
            np.nan_to_num(self.e_raw, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            bass = T.bass_pitch(n)
            key, mode = getattr(f, "key", None), getattr(f, "mode", None)
        else:
            self.e_raw.fill(0.0)
        self.ease.release_s = window
        e = self.ease.step(self.e_raw, dt)
        ch = self.keyr.update(self.e_raw, dt, key, mode)
        if ch is not None:
            self._key_change(ch)
        changed = self.chords.update(e, dt, bass % 12 if bass >= 0 else -1, self.keyr.key)
        emax = float(e.max())
        self.loud = T.smoothstep(0.02, 0.25, emax)
        self.silent_s = 0.0 if emax > 0.02 else self.silent_s + dt

        # the bead's target on the Spiral Array: X = kappa Q + (1 - kappa) CE
        lay = self.lay
        T.spell_all(self.ce_long[2] / T.SPIRAL_H, self.ks)
        ce = T.centre_of_effect(e, self.ks, self.ce)
        if ce is None:
            ce = self.ce_long
        c = self.chords
        kap = c.conf if c.chord >= 0 and c.sounding else 0.0
        if kap > 0.0:
            np.multiply(lay.sk.stations[c.triad()], kap, out=self.X)
            self.X += (1.0 - kap) * ce
        else:
            self.X[:] = ce
        if emax > 0.02:
            self.ce_long += (self.X - self.ce_long) * T.ema_alpha(dt, 8.0)
        self.kappa = kap
        self.strain = lay.sk.strain_of(self.X)
        self.bearing = lay.sk.bearing_of(self.X)
        self.dom_d = lay.sk.dominant_distance(self.X)

        # Lerdahl's attraction -> Pi (the gold thread)
        raw = float(e @ lay.attraction) / emax * T.smoothstep(0.02, 0.15, emax) if emax > 1e-4 else 0.0
        pi_now = 1.0 - math.exp(-raw / 3.0) if math.isfinite(raw) else 0.0
        tau = 0.15 if pi_now > self.pi else 0.6
        self.pi += (pi_now - self.pi) * T.ema_alpha(dt, tau)
        self.pi_t += dt
        if self.pi_t >= 0.1:
            self.pi_t = 0.0
            self.pi_hist[self.pi_i] = pi_now
            self.pi_i = (self.pi_i + 1) % len(self.pi_hist)
        pi_peak = max(float(self.pi_hist.max()), pi_now)

        # cadences: a ring on landing on the tonic (gold) or vi (cool)
        if changed and c.conf > 0.7 and pi_peak > 0.45:
            tid = c.triad()
            if tid == lay.tonic_tid:
                self._ring(pi_peak, 0.0, 0.0, self.gold)
            elif tid == lay.vi_tid:
                x, z = self._station_xz(tid)
                self._ring(0.8 * pi_peak, x, z, np.array(COOL_RGB))
        # the station highlight follows the chord
        self._hl_in.fill(0.0)
        if c.chord >= 0:
            self._hl_in[c.triad()] = kap
        self.st_hl.step(self._hl_in, dt)
        # the bead's colour: its chord's function, by how sure the reader is
        tint = np.array([1.0, 0.93, 0.82])
        if c.chord >= 0:
            fc = T.function_class(c.root, c.quality, self.keyr.key)
            tint = tint + (0.55 * FUNC_RGB[fc] + 0.45 * tint - tint) * kap
        self.bead_rgb += (tint - self.bead_rgb) * T.ema_alpha(dt, 0.2)

    def _key_change(self, ch) -> None:
        """Turn the funnel by 30 degrees per fifth under the bead; ease the stations to the new key over 2 s."""
        rot = 0.0 if ch.first else math.radians(30.0 * ch.fifths)
        self.st_rho_from[:] = self.st_rho
        self.st_ang_from[:] = [_wrap(a - rot) for a in self.st_ang]
        self.st_rgb_from[:] = self.st_rgb
        self.glyph_from[:] = self.lay.glyph if self.blend_t >= 1.0 else self.glyph_from
        self.theta_from = self._theta() + rot
        self.trail_rot += rot
        self.blend_t = 0.0
        self.lay = KeyLayout(ch.to)
        if ch.first:
            self.ce_long[:] = self.lay.sk.point

    def _theta(self) -> float:
        return self.theta_from * (1.0 - T.smootherstep(0.0, 2.0, self.blend_t))

    def _ring(self, amp: float, x: float, z: float, rgb) -> None:
        i = int(np.argmax(self.rings[:, 0]))              # the oldest slot
        self.rings[i, :7] = (0.0, amp, x, z, *rgb)

    def _station_xz(self, tid: int) -> tuple[float, float]:
        r = R_WORLD * self.st_rho[tid]
        a = self.st_ang[tid] + self._theta()
        return -r * math.sin(a), r * math.cos(a)

    # -- render ---------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        cp = self.ctx.cp
        p = self.step_params(dt)
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0.0:
            dt = 0.0
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = max(float(p["speed"]), 0.0)
        coeffs = palette_coeffs(p["palette"])
        self.clock += dt
        self.kick += (pulse - self.kick) * (T.ema_alpha(dt, 0.1) if dt > 0 else 0.0)
        self.breath += 0.4 * spd * dt
        self.yaw_ph += (0.07 + 0.09 * e) * spd * dt
        self.dust_in = (self.dust_in + (0.6 + 0.9 * e) * spd * dt) % 4096.0
        self.dust_spin = (self.dust_spin + (0.04 + 0.07 * e) * spd * dt) % (2.0 * math.pi)
        self.neb += np.array([0.9, 0.55]) * (0.8 + 2.4 * e) * spd * dt
        self.neb %= 256.0
        self.drift += float(p["drift_speed"]) * spd * dt
        self.swell = (self.swell + (0.12 + 0.18 * e) * spd * dt) % 1.0
        drift = self.drift
        pal_c = _pal(0.5 + drift, coeffs)
        self.gold = 0.7 * FUNC_RGB[0] + 0.3 * pal_c

        self._listen(dt, float(p["chord_window"]))
        lay = self.lay

        # key change: stations ease to the new key, the world turn eases to 0
        self.blend_t += dt
        u = T.smootherstep(0.0, 2.0, self.blend_t)
        theta = self._theta()
        np.subtract(lay.rho, self.st_rho_from, out=self.st_rho)
        self.st_rho *= u
        self.st_rho += self.st_rho_from
        d_ang = lay.ang - self.st_ang_from
        d_ang -= 2.0 * np.pi * np.ceil((d_ang - np.pi) / (2.0 * np.pi))
        np.multiply(d_ang, u, out=self.st_ang)
        self.st_ang += self.st_ang_from
        self.st_rgb[:] = self.st_rgb_from + (FUNC_RGB[lay.fn] - self.st_rgb_from) * u
        glyph = lay.glyph if u >= 0.5 else self.glyph_from
        glyph_a = abs(2.0 * u - 1.0) if self.blend_t < 2.0 else 1.0
        kk = 1.0 if self.keyr.known else 0.4
        self.known_k += (kk - self.known_k) * T.ema_alpha(dt, 1.0)

        D = float(p["well_depth"]) * (1.0 + (0.03 + 0.02 * e) * math.sin(self.breath))
        ia = 1.0 / (R_WORLD * CORE)

        def well(r):
            return -D / np.sqrt(1.0 + (r * ia) ** 2)

        # the bead: target (rho, angle) -> world xz; a critically damped spring (exact step, omega 6/s)
        quiet = T.smoothstep(1.5, 4.0, self.silent_s)
        rho_t = min(max(self.strain / RHO_S, 0.0), 1.0) * (1.0 - quiet)
        ang_t = _wrap(math.radians(30.0 * self.bearing)) + theta
        tx, tz = -R_WORLD * rho_t * math.sin(ang_t), R_WORLD * rho_t * math.cos(ang_t)
        w = 6.0
        if dt > 0.0:
            ex = math.exp(-w * dt)
            for i, tgt in ((0, tx), (1, tz)):
                y0, v0 = self.bead[i] - tgt, self.bead_v[i]
                c1 = v0 + w * y0
                self.bead[i] = tgt + (y0 + c1 * dt) * ex
                self.bead_v[i] = (v0 - w * c1 * dt) * ex
        brw = math.hypot(self.bead[0], self.bead[1])
        bead_w = (self.bead[0], float(well(brw)) + 0.02, self.bead[1])

        # the trail: splat the bead in the funnel's frame (psi = A - theta), decay a quarter of the rows
        psi = self.trail_rot - theta
        cps, sps = math.cos(psi), math.sin(psi)
        txx = bead_w[0] * cps - bead_w[2] * sps
        tzz = bead_w[0] * sps + bead_w[2] * cps
        tsc = TRAIL_N / (2.0 * TRAIL_EXT)
        decay = math.exp(-4.0 * dt / max(float(p["trail_s"]), 0.5))
        amp = dt * 1.6 * self.loud
        launch_2d(self.k_trail, TRAIL_N, TRAIL_N,
                  (self.heat, np.float32((txx + TRAIL_EXT) * tsc - 0.5), np.float32((tzz + TRAIL_EXT) * tsc - 0.5),
                   np.float32(amp), np.float32(decay), np.int32(self.trail_phase)))
        self.trail_phase = (self.trail_phase + 1) & 3

        # rings age
        self.rings[:, 0] += dt
        live = self.rings[:, 0] < 6.0
        nring = int(live.sum())

        # camera
        tilt = math.radians(float(p["tilt"]))
        yaw = math.radians(4.0 + 11.0 * e) * (0.8 * math.sin(self.yaw_ph) + 0.2 * math.sin(2.3 * self.yaw_ph + 1.0))
        target = (0.0, -0.5 * D - 0.12, 0.05)
        dist = CAM_DIST * max(1.0, math.sin(tilt) / math.sin(math.radians(35.0))) ** 0.85   # a steeper view: further back
        cam = look_at(orbit(target, dist, yaw, tilt), target, fov_deg=CAM_FOV)
        eye, fwd, right, up = (np.asarray(v) for v in (cam.eye, cam.fwd, cam.right, cam.up))
        fpx = 0.5 * self.h / cam.tan_half

        # the points to project: 24 stations, 12 lamps, bead, floor, gold-thread mid, ghost
        pts = self.pts
        ang = np.empty(36)
        rr = np.empty(36)
        ang[:24] = self.st_ang + theta
        rr[:24] = R_WORLD * self.st_rho
        ang[24:] = lay.lamp_ang + theta
        rr[24:] = R_WORLD * 0.99
        pts[:36, 0] = -rr * np.sin(ang)
        pts[:36, 2] = rr * np.cos(ang)
        pts[:36, 1] = well(rr) + 0.012
        pts[36] = bead_w
        pts[37] = (0.0, -D, 0.0)
        taut = 1.0 - T.smoothstep(0.08, 0.45, self.dom_d)
        mid = 0.5 * (pts[36] + pts[37])
        sag = 0.18 * (1.0 - taut) * float(np.linalg.norm(pts[36] - pts[37]))
        ctrl = np.array([mid[0], mid[1] + 2.0 * sag, mid[2]])      # quadratic Bezier: peak lift = sag
        b = self._bz[:, None]
        pts[38:38 + NGT] = (1 - b) ** 2 * pts[36] + 2 * b * (1 - b) * ctrl + b * b * pts[37]
        ghost_a, ghost_xz = 0.0, (0.0, 0.0)
        if self.silent_s > 20.0:
            g = (self.silent_s - 20.0) % 40.0
            if g < 8.0:
                tour = (lay.tonic_tid, lay.iv_tid, lay.v_tid, lay.tonic_tid)
                s = g / 8.0 * 3.0
                k = min(int(s), 2)
                uu = T.smootherstep(0.0, 1.0, s - k)
                a0, a1 = self._station_xz(tour[k]), self._station_xz(tour[k + 1])
                ghost_xz = (a0[0] + (a1[0] - a0[0]) * uu, a0[1] + (a1[1] - a0[1]) * uu)
                ghost_a = 0.3 * T.smoothstep(0.0, 0.6, g) * (1.0 - T.smoothstep(7.4, 8.0, g))
        gr = math.hypot(*ghost_xz)
        pts[38 + NGT] = (ghost_xz[0], float(well(gr)) + 0.02, ghost_xz[1])
        dv = pts - eye
        zc = np.maximum(dv @ fwd, 0.05)
        sx = 0.5 * self.w + fpx * (dv @ right) / zc
        sy = 0.5 * self.h - fpx * (dv @ up) / zc

        # this frame's host slot: a ring slot whose last copy has run, else the pageable spare
        i = self._slot
        ev = self._ev[i]
        if ev.done:
            self._slot = (i + 1) % _RING
        else:
            ev, i = None, _RING
        hb = self._host[i]
        hb[:] = 0.0
        P = hb
        P[0:12] = coeffs
        P[12], P[13] = self.w, self.h
        P[14:17], P[17:20], P[20:23], P[23:26] = eye, fwd, right, up
        P[26], P[27], P[28] = fpx, D, ia
        P[29], P[30] = math.cos(theta), math.sin(theta)
        P[31], P[32] = cps, sps
        P[33], P[34], P[35] = e, self.kick, float(p["intensity"])
        P[36], P[37] = float(p["grid"]), float(p["labels"])
        P[38] = self.clock % 1000.0
        P[39], P[40] = self.dust_in, self.dust_spin
        P[41] = 1.0 - (0.08 + 0.14 * e)
        P[42], P[43] = self.neb
        P[44], P[45] = float(p["palette_period"]), drift
        P[46], P[47] = self.pi * (0.35 + 0.65 * self.loud) * (0.45 + 0.55 * taut), taut
        P[48:51] = bead_w
        P[51], P[52], P[53] = sx[36], sy[36], zc[36]
        P[54] = 0.45 + 0.55 * self.loud
        P[55] = self.sc * (5.0 + 5.0 * e) * (1.0 + 0.3 * self.kick)
        P[56:59] = self.bead_rgb
        P[59], P[60], P[61] = sx[37], sy[37], zc[37]
        P[62], P[63], P[64], P[65] = ghost_a, sx[38 + NGT], sy[38 + NGT], zc[38 + NGT]
        P[66:69] = (0.55, 0.75, 1.0)
        P[69] = self.sc
        P[71] = 0.02 * D
        grid_tint = 0.5 * _pal(0.3 + drift, coeffs) + 0.5 * np.array([0.45, 0.58, 0.95])
        P[72:75] = grid_tint
        P[75:78] = np.array([0.010, 0.014, 0.034]) + 0.018 * _pal(0.62 + drift, coeffs)
        P[78:81] = (0.004, 0.006, 0.016)
        P[81], P[82] = self.lw, self.lh
        P[83] = 0.22 + 0.18 * self.known_k
        P[84] = 0.035 * D
        P[85] = -D / math.sqrt(1.0 + (R_CYL * ia) ** 2) + 0.03 * D + 0.01
        P[86] = -1.06 * D
        G = P[O_GT:O_GT + 3 * NGT].reshape(NGT, 3)
        G[:, 0], G[:, 1], G[:, 2] = sx[38:38 + NGT], sy[38:38 + NGT], zc[38:38 + NGT]
        P[90] = 0.55
        P[92] = self.swell
        P[91] = 0.85
        P[O_SC:O_SC + 3] = self.gold
        P[O_SC + 3:O_SC + 6] = 0.7 * FUNC_RGB[2] + 0.3 * pal_c
        P[O_SC + 6:O_SC + 9] = 0.7 * FUNC_RGB[1] + 0.3 * pal_c
        # rings
        k = 0
        for r in self.rings[live]:
            P[O_RG + 8 * k:O_RG + 8 * k + 8] = r
            k += 1
        P[70] = k
        # rim lamps
        lv = 1.0 - np.exp(-1.6 * self.ease.value)
        lamp_rgb = np.array([_pal((f6 + 6.0) / 12.0 * float(p["palette_period"]) + drift, coeffs)
                             for f6 in lay.lamp_fifths])
        L = P[O_LP:O_LP + 96].reshape(12, 8)
        L[:, 0], L[:, 1], L[:, 2], L[:, 3] = sx[24:36], sy[24:36], zc[24:36], lv
        L[:, 4:7] = 0.35 + 0.65 * lamp_rgb
        # stations
        dxz = pts[:24, [0, 2]] - np.array([bead_w[0], bead_w[2]])
        prox = np.exp(-(dxz ** 2).sum(1) / 0.05)
        hl = self.st_hl.value
        bright = self.known_k * (0.35 + 0.65 * prox * (0.4 + 0.6 * self.loud)) + 0.6 * hl
        Q = P[O_ST:O_ST + 24 * 12].reshape(24, 12)
        Q[:, 0], Q[:, 1], Q[:, 2], Q[:, 3] = sx[:24], sy[:24], zc[:24], glyph
        Q[:, 4:7] = 0.7 * self.st_rgb + 0.3 * pal_c
        Q[:, 7] = np.minimum(bright, 1.4)
        Q[:, 8] = self.sc * (3.6 + 2.4 * np.minimum(bright, 1.2)) * np.clip(dist / zc[:24], 0.6, 1.6)
        Q[:, 9] = float(p["labels"]) * glyph_a * (0.5 + 0.5 * np.minimum(bright, 1.0))

        stream = cp.cuda.get_current_stream()
        rt = cp.cuda.runtime
        rt.memcpyAsync(self.arena.data.ptr, hb.ctypes.data, hb.nbytes, rt.memcpyHostToDevice, stream.ptr)
        if ev is not None:
            ev.record(stream)                             # the slot is free again once the copy has run
        launch_2d(self.k_main, self.w, self.h, (self.buf, self.arena, self.heat, self.tex, self.atlas,
                                                np.uint32(self.seed)))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self._ev = None
        self.buf = self.heat = self.arena = self.atlas = self.tex = None
