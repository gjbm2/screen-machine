"""Pendulum wave: 88 glass pendulums, one per piano key, whose swings wind into travelling snakes and gather into one still, glowing line on the phrase.

Source: Original (inspired by the Harvard Natural Sciences Lecture Demonstrations' "Pendulum Waves",
        https://sciencedemonstrations.fas.harvard.edu/presentations/pendulum-waves, and Luke Twyman's (Whitevinyl)
        musical orrery SolarBeat, https://coolhunting.com/tech/solar-beat.  Only the ideas are used; no code.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A brass rod hangs in a dark room above a black lacquered floor.  From it hang 88 threads, one for each key of the
  piano, with a glass ball at the end of each.  The bass is on the left and the treble on the right; the bass threads
  are longer and the bass balls a little heavier.  Every ball swings out from the rod and back, and leaves a comet
  tail of light along its arc, like a long-exposure photograph; its glow pools on the floor and shows in the floor's
  reflection.  The camera stands off the treble end of the row and looks down it at a 3/4 angle, high enough that the
  swings read across the picture, so the whole row fills the frame and the waves run along it.  The ``camera`` knob
  goes from low and end-on (the row receding, its arcs stacked like ribs) to high and nearly overhead (the snake drawn
  flat).  The camera sways slowly and is framed every frame to the row's swing envelope, as tight as the picture allows.
  The ``layout`` knob bends the rod.  At 0 it is straight, as in the Harvard demonstration.  In between it is an
  amphitheatre arc (a slight bow fills a 16:9 frame best).  At 1 it closes into a wider ring of shorter threads,
  like an orrery, and the balls swing in and out from its centre.

  The pattern.  Every pendulum swings with the same reach, so at rest the row is one straight line.  Over one cycle
  the swings slide out of step along the row: a long, slow snake appears, winds tighter into ``waves`` crests along
  the row (3 by default) travelling from the bass to the treble, then unwinds and gathers again.  At the end of the
  cycle every ball reaches the far end of its swing at the same instant: the row is one still line.  The pattern
  lingers there (the held breath): the threads and rod brighten, a line of light joins the balls, the trails lengthen
  into a curtain of identical arcs, the room's haze lifts, and then it lets go.  The swing itself is a whole number of
  swings per cycle, so it is always in step with the realignment.

  Colour is the pitch class: a hue wheel from C round to B (each octave one rainbow, so neighbouring keys are
  neighbouring hues and the row reads as bands of colour, not confetti), tinted by the palette (``pitch_colour`` 1 =
  the pure wheel, 0 = the palette alone).  Every key is equally bright.  The room is a muted tone of the palette.

  The wave line: a thin line of light from each ball to its neighbour, bright where the neighbours swing together (the
  snake, and all of it at the realignment) and gone where a struck key has swung out of the row.

  Bloom in, ease out: a ball's swing grows over about 0.1 s and dies over about 0.5 s, so nothing jumps.

How the music drives it
  * A struck key swings its own pendulum wider (CONVENTIONS 2b: the strike changes the reach, never the position, so
    the wave pattern stays intact): the ball leaps out of the snake on a long bright arc and settles back into it as
    the note dies, because the reach follows the note's sounding LEVEL, which includes the piano's decay.  Loud is a
    wide swing, a big ball (it swells up to 1.5x) and a big halo; soft is a small, tender sway.
  * The ball lights from inside in its pitch class's colour.  The light blooms over 40 ms of the strike, settles to
    the note's sounding level and fades as the note does.  Its comet tail draws the swing in light; an idle ball's
    tail is faint, a sounding one's bright.
  * The pedal is resonance.  A held damper keeps ringing notes swinging, through LEVEL.  It also lengthens every
    trail (a longer exposure) and makes the whole row swing a little wider.  Pendulums of the same pitch class as a
    sounding note join in, by sympathy: hold the pedal and play a C, and every C sways.  The soft pedal dims the light.
  * Phrases have a shape.  When show_service's music clock is sure, the cycle is pulled onto the bar grid, so the
    row realigns on a bar line.  The cycle is a whole number of bars (1, 2, 4, 8 or 16), the one nearest ``cycle_s``,
    and each swing is a whole number of beats (the one nearest 1.3 s), so the balls turn on the beat.  A
    slew-limited pull moves the cycle and the swing onto the grid and never runs them backwards.  Without a clock the
    cycle is ``cycle_s`` long (16 s), locked to the shared photon clock.
  * The room ensemble: ``sound.register`` dims the keys outside this screen's window but never hides them.
    ``sound.segment`` (k, n) with span: room (the default, span: screen, gives each TV the whole row) spreads one
    longer row across the screens: this screen draws its slice of a picture n
    screens wide, with the bass on the left TV.  The cycle and the camera are pulled onto shared clocks, so the
    slices meet at the seam.

  With no notes at all, every pendulum keeps its idle swing and the snake winds and realigns on its own; the reach
  grows with energy, so the room is never still.

Macros: energy -> the idle reach, ball glow, trail brightness, the halo, the haze and the camera's sway and closeness;
pulse (bind it to note_on) -> every ball's glow; speed -> the cycle (and so every swing), the camera and the colour
drift; intensity -> gain.  Palette, palette_period and drift_speed tint the pitch colours, the room and the rod.
Regional: layout (row -> arc -> ring), trail (exposure), swing_scale, camera (low and end-on -> high and overhead),
cycle_s, waves (crests along the row at the most wound point), pitch_colour, line (the wave line), floor (pools and
reflections), halo.

Rendering: one kernel at render_scale (0.5 by default), then an upsample.  The kernel ray-traces the balls
analytically, draws the threads, rod and wave line as anti-aliased lines with depth, and adds the halos.  The trails
live in a float accumulation buffer (max-blend, decay exp(-dt / tau), an age gradient inside each frame's stroke).  The
floor is lit from 22 emission-weighted clusters of four balls.  The host does the per-key shaping, the cycle, the
swing and the camera (a closed-form tightest framing of the swing envelope) in numpy over 88 keys and uploads 15 KB
once per frame.  Every loop is bounded (88 keys, 22 clusters).
"""
from __future__ import annotations

import logging
import math
import time

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import palette_coeffs
from stream_service.plugins._lib.params import enum, num, standard_scene_params
from stream_service.plugins._lib.sdf3d import SDF3D, SDF3D_OPTIONS
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.pendulum_wave")

N = 88                          # pendulums, A0 .. C8
LOW = 21
L_BASS, L_TREBLE = 2.8, 1.5     # thread lengths (world units)
Y_BAR = 1.4
Y_FLOOR = Y_BAR - L_BASS - 0.4
SPACING = 0.092                 # along the rod
BOB_R = 0.054
BAR_R = 0.02
FOV_DEG = 38.0
NEAR = 0.05
SWING_S = 1.3                   # the swing's period at speed 1 (s); on the beat grid, the nearest whole beats
WARP = 0.35                     # the pattern lingers near the realignment (1 - WARP speed) and hurries mid-cycle
BREATH_S = 1.1                  # s either side of the realignment that glows
D_ENV = 0.85                    # the swing envelope the camera frames (world units)
CAM_PERIOD_S = 96.0             # one camera sway cycle at speed 1
NBARS = (1, 2, 4, 8, 16)
BEATS = (1, 2, 3, 4, 6, 8)      # beats per swing, on the beat grid
CLUSTER = 4
NCL = N // CLUSTER              # floor light clusters

# arena: globals, then N pendulum records, then NCL floor clusters (floats)
NG, PS, CS = 64, 40, 8
O_P = NG
O_CL = NG + N * PS
N_AR = O_CL + NCL * CS
G = dict(EYE=0, FWD=3, RIGHT=6, UP=9, S=12, XOFF=13, WV=14, HV=15, YFLOOR=16, FLOOR=17, BARR=18, THREADW=19,
         DECAY=20, DTTAU=21, TRAILK=22, HALOK=23, SKYTOP=24, SKYHOR=27, FLOORC=30, GAIN=33, BREATH=34, BOBR=35,
         KEY=36, RIM=39, BARC=42, GLOWC=45, TRAILW=48, VIG=49, HAZE=50, FOG=51, THRC=52, LINEW=55)
R = dict(PIV=0, BOB=3, THR=6, EM=7, TINT=10, RPX=13, SPIV=14, SBOB=16, SPREV=18, SNEXT=20, BARW=22, ZPIV=23,
         ZBOB=24, ZNEXT=25, BB=26, TRAIL=30, HALO=31, HALOR=32, VALID=33, RAD=34, LINE=35, LB=36)

_DEFS = "\n".join([f"#define G_{k} {v}" for k, v in G.items()] + [f"#define R_{k} {v}" for k, v in R.items()])

_SRC = HELPERS + SDF3D + r"""
#define NPEND """ + str(N) + r"""
#define NG """ + str(NG) + r"""
#define PS """ + str(PS) + r"""
#define CS """ + str(CS) + r"""
#define NCL """ + str(NCL) + r"""
#define N_AR """ + str(N_AR) + r"""
""" + _DEFS + r"""

__device__ __forceinline__ float3 pw_ld3(const float* p) { return make_float3(p[0], p[1], p[2]); }

// distance from (px, py) to the screen segment a -> b; u = where along it (0..1), sd = signed distance to its line
__device__ __forceinline__ float pw_seg(float px, float py, const float* a, const float* b, float& u, float& sd) {
    const float dx = b[0] - a[0], dy = b[1] - a[1];
    const float l2 = dx * dx + dy * dy;
    const float rx = px - a[0], ry = py - a[1];
    u = l2 > 1e-6f ? clamp01((rx * dx + ry * dy) / l2) : 0.f;
    const float qx = rx - dx * u, qy = ry - dy * u;
    sd = (dx * ry - dy * rx) * rsqrtf(fmaxf(l2, 1e-6f));
    return sqrtf(qx * qx + qy * qy);
}

__device__ float3 pw_sky(float3 d, const float* __restrict__ g) {
    const float3 top = pw_ld3(g + G_SKYTOP), hor = pw_ld3(g + G_SKYHOR);
    const float h = clamp01(d.y * 1.4f + 0.08f);
    float3 c = mix(hor, top, sqrtf(h));
    const float band = (d.y + 0.03f) * 7.f;
    c += hor * (g[G_HAZE] * __expf(-band * band));          // a soft glow along the horizon behind the row
    return c;
}

// the black lacquered floor: the sky's reflection, glow pools and a blurred mirror of the balls (22 clusters)
__device__ float3 pw_floor(float3 eye, float3 rd, float tf, const float* __restrict__ g, const float* __restrict__ cl) {
    const float3 P = eye + rd * tf;
    const float om = 1.f - fminf(fabsf(rd.y), 1.f);
    const float om2 = om * om;
    const float fres = 0.04f + 0.96f * om2 * om2 * om;
    const float3 rr = make_float3(rd.x, -rd.y, rd.z);
    float3 c = pw_ld3(g + G_FLOORC) + pw_sky(rr, g) * (0.12f + 0.6f * fres);
    const float amt = g[G_FLOOR];
    if (amt > 1e-3f) {
        float3 pool = f3(0.f), mir = f3(0.f);
        const float yf = g[G_YFLOOR], rb = g[G_BOBR];
        for (int j = 0; j < NCL; ++j) {
            const float* C = cl + j * CS;
            const float3 e = pw_ld3(C + 3);
            const float3 b = pw_ld3(C);
            const float hgt = fmaxf(b.y - yf, 0.15f);
            const float dx = P.x - b.x, dz = P.z - b.z;
            const float q = hgt * hgt + dx * dx + dz * dz;
            pool += e * (hgt * rsqrtf(q) / q);
            const float3 v = b - P;
            const float tp = dot(v, rr);
            if (tp > 0.f) {
                const float dd = fmaxf(dot(v, v) - tp * tp, 0.f);
                const float sg = rb * (4.5f + 0.12f * tp);
                mir += e * __expf(-dd / (sg * sg));
            }
        }
        c += (pool * 0.045f + mir * (0.06f + 0.2f * fres)) * amt;
    }
    const float fog = 1.f - __expf(-tf * g[G_FOG]);
    return mix(c, pw_ld3(g + G_SKYHOR) * (1.f + g[G_HAZE]), fog);
}

// a glossy glass ball lit from inside
__device__ float3 pw_ball(const float* __restrict__ r, float3 eye, float3 rd, float t, const float* __restrict__ g) {
    const float3 C = pw_ld3(r + R_BOB);
    const float3 n = normalize(eye + rd * t - C);
    const float ndv = clamp01(-dot(n, rd));
    const float om = 1.f - ndv, om2 = om * om;
    const float F = 0.04f + 0.96f * om2 * om2 * om;
    const float3 rv = reflect(rd, n);
    const float3 env = rv.y >= 0.f ? pw_sky(rv, g) * 2.0f
                                   : pw_ld3(g + G_FLOORC) * 2.f + pw_ld3(g + G_SKYHOR) * 0.25f;
    const float3 L1 = pw_ld3(g + G_KEY), L2 = pw_ld3(g + G_RIM);
    const float s1 = __powf(clamp01(dot(rv, L1)), 90.f), s2 = __powf(clamp01(dot(rv, L2)), 22.f);
    const float3 em = pw_ld3(r + R_EM), tint = pw_ld3(r + R_TINT);
    const float3 body = tint * (0.015f + 0.05f * clamp01(dot(n, L1))) + em * (0.32f + 0.68f * ndv * ndv);
    return body * (1.f - F) + env * F + make_float3(3.0f * s1 + 0.55f * s2, 2.9f * s1 + 0.6f * s2, 2.7f * s1 + 0.7f * s2)
           * (0.55f + 0.45f * F);
}

extern "C" __global__ __launch_bounds__(256, 2)
void pw_render(uchar4* __restrict__ out, float4* __restrict__ acc, const float* __restrict__ A, const int W, const int H) {
    __shared__ float sA[N_AR];
    __shared__ unsigned char sList[NPEND];
    __shared__ int sCnt[4];
    const int tid = threadIdx.y * blockDim.x + threadIdx.x;
    const int nthr = blockDim.x * blockDim.y;
    for (int j = tid; j < N_AR; j += nthr) sA[j] = A[j];
    __syncthreads();
    const float* g = sA;
    const float* P = sA + NG;
    const float* CL = sA + NG + NPEND * PS;
    // the pendulums whose box meets this block, in row order (a ballot per warp, so the order is deterministic)
    const int warp = tid >> 5, lane = tid & 31;
    bool hit = false;
    unsigned mask = 0u;
    if (warp * 32 < NPEND) {
        const int i = warp * 32 + lane;
        if (i < NPEND) {
            const float* r = P + i * PS;
            const float tx0 = (float)(blockIdx.x * blockDim.x), ty0 = (float)(blockIdx.y * blockDim.y);
            hit = r[R_VALID] > 0.5f && r[R_BB] <= tx0 + blockDim.x && r[R_BB + 2] >= tx0
                  && r[R_BB + 1] <= ty0 + blockDim.y && r[R_BB + 3] >= ty0;
        }
        mask = __ballot_sync(0xffffffffu, hit);
        if (lane == 0) sCnt[warp] = __popc(mask);
    }
    __syncthreads();
    if (hit) {
        int base = 0;
        for (int k = 0; k < warp; ++k) base += sCnt[k];
        sList[base + __popc(mask & ((1u << lane) - 1u))] = (unsigned char)(warp * 32 + lane);
    }
    __syncthreads();
    int nlist = 0;
    for (int k = 0; k * 32 < NPEND; ++k) nlist += sCnt[k];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;

    const float s = g[G_S];
    const float fx = x + 0.5f, fy = y + 0.5f;
    const float pxv = fx + g[G_XOFF];
    const float3 eye = pw_ld3(g + G_EYE), fwd = pw_ld3(g + G_FWD), right = pw_ld3(g + G_RIGHT), up = pw_ld3(g + G_UP);
    const float3 rd = normalize(fwd + right * ((pxv - 0.5f * g[G_WV]) * s) + up * ((0.5f * g[G_HV] - fy) * s));

    // pass 1: the nearest ball (analytic, anti-aliased by the ray's closest approach)
    float best_t = 1e30f, best_cov = 0.f;
    int best = -1;
    for (int j = 0; j < nlist; ++j) {
        const int i = sList[j];
        const float* r = P + i * PS;
        if (fx < r[R_LB] || fx > r[R_LB + 2] || fy < r[R_LB + 1] || fy > r[R_LB + 3]) continue;
        const float ddx = fx - r[R_SBOB], ddy = fy - r[R_SBOB + 1];
        const float rp = r[R_RPX] + 1.5f;
        if (ddx * ddx + ddy * ddy > rp * rp) continue;
        const float rb = r[R_RAD];
        const float3 oc = pw_ld3(r + R_BOB) - eye;
        const float tc = dot(oc, rd);
        if (tc <= NEAR_T) continue;
        const float d2 = fmaxf(dot(oc, oc) - tc * tc, 0.f);
        const float cov = clamp01(0.5f - (sqrtf(d2) - rb) / fmaxf(s * tc, 1e-6f));
        if (cov <= 0.f) continue;
        const float th = tc - sqrtf(fmaxf(rb * rb - d2, 0.f));
        if (th < best_t) { best_t = th; best_cov = cov; best = i; }
    }

    // the room behind
    float3 col;
    if (rd.y < -1e-4f) {
        const float tf = (g[G_YFLOOR] - eye.y) / rd.y;
        col = tf > 0.f ? pw_floor(eye, rd, tf, g, CL) : pw_sky(rd, g);
    } else {
        col = pw_sky(rd, g);
    }

    // pass 2: rod and threads (depth-sorted against the nearest ball), halos, the trail exposure
    const float zs = best >= 0 ? best_t * dot(rd, fwd) : 1e30f;
    float Tb = 1.f, Tf = 1.f;
    float3 cb = f3(0.f), cf = f3(0.f), halo = f3(0.f), lb = f3(0.f), lf = f3(0.f);
    const int idx = y * W + x;
    float4 ac = acc[idx];
    const float decay = g[G_DECAY], dttau = g[G_DTTAU];
    ac.x *= decay; ac.y *= decay; ac.z *= decay;
    const float3 barc = pw_ld3(g + G_BARC), glowc = pw_ld3(g + G_GLOWC), thrc = pw_ld3(g + G_THRC);
    const float breath = g[G_BREATH], thw = g[G_THREADW], trw = g[G_TRAILW];
    for (int j = 0; j < nlist; ++j) {
        const int i = sList[j];
        const float* r = P + i * PS;
        if (fx < r[R_BB] || fx > r[R_BB + 2] || fy < r[R_BB + 1] || fy > r[R_BB + 3]) continue;
        const float3 em = pw_ld3(r + R_EM);
        float u, sd;
        // the rod: pivot i -> pivot i + 1, a brushed metal cylinder (shaded across its width)
        if (r[R_BARW] > 0.f) {
            const float d = pw_seg(fx, fy, r + R_SPIV, r + R_SNEXT, u, sd);
            const float z = fmaxf(r[R_ZPIV] + (r[R_ZNEXT] - r[R_ZPIV]) * u, NEAR_T);
            const float hw = g[G_BARR] / fmaxf(z * s, 1e-6f);
            const float cov = clamp01(hw + 0.5f - d) * r[R_BARW] * fminf(1.f, 2.f * hw);
            if (cov > 0.f) {
                const float q = fminf(fmaxf(sd / fmaxf(hw, 0.5f), -1.f), 1.f);
                const float hl = (q + 0.45f) / 0.22f;
                const float3 bc = barc * (0.25f + 0.75f * sqrtf(fmaxf(1.f - q * q, 0.f)))
                                  + f3(0.85f * __expf(-hl * hl)) * (0.4f + 0.6f * barc.x) + glowc * breath;
                if (z > zs) { cb = cb * (1.f - cov) + bc * cov; Tb *= 1.f - cov; }
                else        { cf = cf * (1.f - cov) + bc * cov; Tf *= 1.f - cov; }
            }
        }
        // the thread: pivot -> ball, lit by its ball near the end
        {
            const float d = pw_seg(fx, fy, r + R_SPIV, r + R_SBOB, u, sd);
            const float cov = clamp01(thw + 0.5f - d) * fminf(1.f, 2.f * thw);
            if (cov > 0.f) {
                const float z = r[R_ZPIV] + (r[R_ZBOB] - r[R_ZPIV]) * u;
                const float u2 = u * u, u4 = u2 * u2;
                const float3 tc = thrc * r[R_THR] + em * (0.4f * u4 * u4);
                if (z > zs) { cb = cb * (1.f - cov) + tc * cov; Tb *= 1.f - cov; }
                else        { cf = cf * (1.f - cov) + tc * cov; Tf *= 1.f - cov; }
            }
        }
        // the ball's own light (halo, trail, wave line) lives in a smaller box round the ball
        if (fx < r[R_LB] || fx > r[R_LB + 2] || fy < r[R_LB + 1] || fy > r[R_LB + 3]) continue;
        // the halo (light in the lens: never occluded)
        {
            const float dx = fx - r[R_SBOB], dy = fy - r[R_SBOB + 1];
            const float hr = fmaxf(r[R_HALOR], 0.5f);
            const float q = (dx * dx + dy * dy) / (hr * hr);
            if (q < 5.f) halo += em * (r[R_HALO] * __expf(-q));
        }
        // the long exposure: this frame's stroke from the previous screen position, older end already decayed
        if (r[R_TRAIL] > 0.f) {
            const float d = pw_seg(fx, fy, r + R_SPREV, r + R_SBOB, u, sd);
            const float tw = fmaxf(r[R_RPX] * trw, 0.7f);
            const float q = (d * d) / (tw * tw);
            if (q < 9.f) {
                const float k = r[R_TRAIL] * __expf(-q - dttau * (1.f - u));
                ac.x = fmaxf(ac.x, em.x * k); ac.y = fmaxf(ac.y, em.y * k); ac.z = fmaxf(ac.z, em.z * k);
            }
        }
        // the wave line: this ball -> the next one along the row, bright where the row is in order (the held breath)
        if (r[R_LINE] > 0.f && i + 1 < NPEND) {
            const float* rn = r + PS;
            const float d = pw_seg(fx, fy, r + R_SBOB, rn + R_SBOB, u, sd);
            const float lw = fmaxf(fminf(r[R_RPX], rn[R_RPX]) * g[G_LINEW], 0.6f);
            const float q = (d * d) / (lw * lw);
            if (q < 9.f) {
                const float k = r[R_LINE] * __expf(-q);
                const float3 lc = mix(pw_ld3(r + R_TINT), pw_ld3(rn + R_TINT), u) * k;
                if (r[R_ZBOB] + (rn[R_ZBOB] - r[R_ZBOB]) * u > zs) lb += lc; else lf += lc;
            }
        }
    }
    ac.x = fminf(fmaxf(ac.x, 0.f), 64.f); ac.y = fminf(fmaxf(ac.y, 0.f), 64.f); ac.z = fminf(fmaxf(ac.z, 0.f), 64.f);
    ac.w = 0.f;
    acc[idx] = ac;

    col = col * Tb + cb + lb;
    if (best >= 0) col = mix(col, pw_ball(P + best * PS, eye, rd, best_t, g), best_cov);
    col = col * Tf + cf + lf;
    col += halo * g[G_HALOK] + make_float3(ac.x, ac.y, ac.z) * g[G_TRAILK];
    col *= g[G_GAIN];
    // vignette over the whole (virtual) picture, so a segment's slices share one
    const float vx = pxv / fmaxf(g[G_WV], 1.f) - 0.5f, vy = fy / fmaxf(g[G_HV], 1.f) - 0.5f;
    col *= fmaxf(1.f - g[G_VIG] * (vx * vx * 1.1f + vy * vy * 1.6f), 0.f);
    col = make_float3(isfinite(col.x) ? col.x : 0.f, isfinite(col.y) ? col.y : 0.f, isfinite(col.z) ? col.z : 0.f);
    store_srgb(out, x, y, W, linear_to_srgb(tonemap_aces(col)));
}
""".replace("NEAR_T", f"{NEAR:.3f}f")
_OPTS = SDF3D_OPTIONS


def _c01(v: float) -> float:
    return 0.0 if not math.isfinite(v) else min(max(v, 0.0), 1.0)


def _advance(phase: float, rate: float, dt: float, target, tau: float) -> float:
    """``phase`` (cycles) advanced by ``rate`` x ``dt`` and pulled toward ``target`` (mod 1), slew-limited: the pull
    never runs the phase backwards nor faster than double (as plugins/_lib/beat.advance)."""
    step = rate * dt
    if target is None or dt <= 0.0 or rate <= 0.0 or not math.isfinite(target):
        return phase + step
    err = (target - (phase + step) + 0.5) % 1.0 - 0.5
    pull = err * (1.0 - math.exp(-dt / tau))
    pull = max(-0.8 * step, min(1.0 * step, pull))
    return phase + step + pull


def _lum(c: np.ndarray) -> np.ndarray:
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _even(c: np.ndarray, y: float = 0.25) -> np.ndarray:
    """Linear RGB rows scaled to luminance ``y`` (every key equally bright; the gain is capped for deep blues)."""
    return c * np.minimum(y / np.maximum(_lum(c), 1e-4), 4.0)[:, None]


def _wheel(turn: np.ndarray) -> np.ndarray:
    """An OKLCh hue wheel (L 0.74, C 0.15) at ``turn`` (0..1 round the circle): linear sRGB rows, clipped."""
    h = 2.0 * np.pi * (turn + 0.09)                          # C sits at a warm red-orange
    L, a, b = 0.74, 0.15 * np.cos(h), 0.15 * np.sin(h)
    l_, m_, s_ = L + 0.3963377774 * a + 0.2158037573 * b, L - 0.1055613458 * a - 0.0638541728 * b, \
        L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return np.clip(np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                             -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                             -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s], 1), 0.0, 1.0)


SPANS = ("screen", "room")    # screen: each TV the whole picture (default; owner, 30 Oct: the TVs aren't adjacent);
                              # room: one picture across the room's screens (sound.segment)


def _fit(pts: np.ndarray, fwd: np.ndarray, right: np.ndarray, upv: np.ndarray, kx: float, ky: float):
    """The nearest eye (orientation fixed) that keeps every point of ``pts`` inside the frame, |x| <= kx z and
    |y| <= ky z (tangents), centred on the free axis: closed form.  Returns the eye and each point's depth."""
    a, b, c = pts @ right, pts @ upv, pts @ fwd
    ez = min((np.min(a + kx * c) - np.max(a - kx * c)) / (2.0 * kx),
             (np.min(b + ky * c) - np.max(b - ky * c)) / (2.0 * ky))
    ex = 0.5 * (np.max(a - kx * (c - ez)) + np.min(a + kx * (c - ez)))
    ey = 0.5 * (np.max(b - ky * (c - ez)) + np.min(b + ky * (c - ez)))
    return ex * right + ey * upv + ez * fwd, c - ez


class PendulumWave(Source):
    """Per frame: per-key shaping, the cycle and the camera in numpy (88 keys), one 15 KB arena upload, one kernel
    launch at render_scale and the upsample."""
    name = "pendulum_wave"
    label = "Pendulum wave"
    description = ("A pendulum wave played by the piano: 88 glass pendulums, one per key and coloured by pitch class, "
                   "seen from the end of the row; their swings wind into travelling snakes and gather into one still, "
                   "glowing line on the phrase, and each struck note swings its own pendulum wide.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="ember", render_scale=0.5, period=1.0, drift=0.01),
            "layout": num(0.0, 1.0, 0.14, "Row / arc / ring", basic=True),
            "trail": num(0.0, 1.0, 0.55, "Light trails", basic=True),
            "swing_scale": num(0.3, 1.6, 1.0, "Swing"),
            "camera": num(0.0, 1.0, 0.8, "Camera (low, end-on / high, overhead)"),
            "cycle_s": num(8.0, 40.0, 16.0, "Realign every", unit="s"),
            "waves": num(0.5, 6.0, 3.0, "Waves along the row"),
            "pitch_colour": num(0.0, 1.0, 0.8, "Pitch colours"),
            "line": num(0.0, 1.0, 0.5, "Wave line"),
            "floor": num(0.0, 1.0, 0.6, "Floor reflections"),
            "halo": num(0.0, 1.5, 0.8, "Glow"),
            "span": enum(SPANS, "screen", "Span (this screen, or one picture across the room)"),
        }

    # -- lifecycle --------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.span_room = params.get("span") == "room"
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        mod = raw_module(_SRC, _OPTS)
        self.k_render = mod.get_function("pw_render")
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.acc = cp.zeros((self.h, self.w, 4), cp.float32)
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        try:
            from stream_service.effects import beat_clock
            self._beat_clock = beat_clock
        except Exception:                                   # no clock: the free cycle
            self._beat_clock = None
        pitches = np.arange(LOW, LOW + N)
        pc = pitches % 12
        self.pc = pc
        t_reg = np.arange(N) / (N - 1.0)
        self.t_reg = t_reg
        self.c_row = 0.5 - t_reg                              # place along the row: the crests travel bass -> treble
        self.length = L_TREBLE + (L_BASS - L_TREBLE) * (1.0 - t_reg) ** 1.35
        self.size = 1.12 - 0.24 * t_reg                       # heavier balls in the bass
        self.hue = pc / 12.0                                   # chromatic: neighbours are neighbouring hues
        self.s_idx = (0.5 * (N - 1) - np.arange(N, dtype=np.float64))     # A0 at +x: screen left
        self.silent = np.zeros((128, 4), np.float32)
        self.silent[:, 2:] = np.inf
        self.amp = np.zeros(N)                # each swing's reach (world units)
        self.prev_sx = np.zeros(N)
        self.prev_sy = np.zeros(N)
        self.prev_ok = np.zeros(N, bool)
        self.phi = 0.0                       # the cycle (cycles, mod 1)
        self.psi = 0.0                       # the common swing (cycles, mod 1)
        self.K = None                        # swings per cycle
        self.cam = 0.0                       # the camera sway (cycles, mod 1)
        self.drift = 0.0
        self.e_s = float(params.get("energy", 0.5))
        self.damp_s = 0.0
        self.nv, self.kv = 1.0, 0.0          # the segment window (eased)
        self.nbars = None
        self.pal = None
        self._reg_key, self._reg_w = None, None
        self._first = True
        self._clock_errors = 0

    def close(self) -> None:
        self.buf = self.acc = self.arena = None

    # -- helpers --------------------------------------------------------------------------------------------------
    def _reg_weights(self, snd):
        reg = getattr(snd, "register", None) if snd is not None else None
        if not reg:
            return None
        if self._reg_key != reg:
            try:
                from stream_service.ensemble import note_weights
                w = note_weights(reg, np.arange(128))
                self._reg_w = None if w is None else np.asarray(w, np.float64)[LOW:LOW + N]
            except Exception:
                self._reg_w = None
            self._reg_key = reg
        return self._reg_w

    def _segment(self, snd):
        seg = getattr(snd, "segment", None) if snd is not None and self.span_room else None
        try:
            k, n = int(seg[0]), int(seg[1])
            if 1 <= n <= 4 and 0 <= k < n:
                return float(k), float(n)
        except Exception:
            pass
        return 0.0, 1.0

    def _clock(self):
        if self._beat_clock is None:
            return None
        try:
            return self._beat_clock(getattr(self.modulation, "runtime", None))
        except Exception:
            self._clock_errors += 1
            return None

    def _pal_lin(self, t: np.ndarray) -> np.ndarray:
        P = self.pal
        srgb = np.clip(P[0:3] + P[3:6] * np.cos(2.0 * np.pi * (P[6:9] * t[:, None] + P[9:12])), 0.0, 1.0)
        return np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)

    # -- the frame --------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if (dt is not None and math.isfinite(dt) and dt > 0.0) else 0.0
        first = self._first
        self._first = False
        spd = max(0.0, float(p["speed"])) if math.isfinite(p["speed"]) else 1.0
        e = _c01(float(p["energy"]))
        pulse = _c01(float(p["pulse"]))
        layout = _c01(float(p["layout"]))
        trail = _c01(float(p["trail"]))
        swing = min(max(float(p["swing_scale"]), 0.0), 2.0)
        camp = _c01(float(p["camera"]))
        cycle_s = min(max(float(p["cycle_s"]), 2.0), 120.0)
        waves = min(max(float(p["waves"]), 0.0), 8.0)
        hue_mix = _c01(float(p["pitch_colour"]))
        line_p = _c01(float(p["line"]))
        floor_amt = _c01(float(p["floor"]))
        halo_p = min(max(float(p["halo"]), 0.0), 2.0)

        # ---- the sound
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes, damper, soft, t_abs = self.silent, 0.0, 0.0, None
        if f is not None:
            try:
                n = np.asarray(f.notes)
                if n.shape == (128, 4):
                    notes = n
                pd = f.pedals
                damper, soft = _c01(float(pd[0])), _c01(float(pd[1]))
                tm = float(f.t_ms)
                if math.isfinite(tm) and tm > 0.0:
                    t_abs = tm / 1000.0
            except Exception:
                notes = self.silent
        if t_abs is None:
            t_abs = time.monotonic()
        seg = notes[LOW:LOW + N]
        lv = np.clip(np.nan_to_num(seg[:, 0].astype(np.float64), nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.5)
        vel = np.clip(np.nan_to_num(seg[:, 1].astype(np.float64), nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)
        age = seg[:, 2].astype(np.float64)
        age = np.clip(np.where(np.isfinite(age), age, 1e3), 0.0, 1e3)
        wreg = self._reg_weights(snd)
        if wreg is not None:
            lv = lv * wreg
            vel = vel * wreg

        # ---- smoothed drivers
        a_e = 1.0 - math.exp(-dt / 2.5) if dt > 0 else 0.0
        self.e_s += (e - self.e_s) * (1.0 if first else a_e)
        e_s = self.e_s
        a_d = 1.0 - math.exp(-dt / 0.3) if dt > 0 else 0.0
        self.damp_s += (damper - self.damp_s) * (1.0 if first else a_d)
        d_s = self.damp_s
        kseg, nseg = self._segment(snd)
        a_seg = 1.0 if first else (1.0 - math.exp(-dt / 1.0) if dt > 0 else 0.0)
        self.nv += (nseg - self.nv) * a_seg
        self.kv += (kseg - self.kv) * a_seg
        rows = self.session_palette()
        try:
            tgt = (np.asarray(rows, np.float64).reshape(12) if rows is not None
                   else np.asarray(palette_coeffs(p["palette"]), np.float64))
        except Exception:
            tgt = np.asarray(palette_coeffs("ember"), np.float64)
        if self.pal is None or first:
            self.pal = tgt.copy()
        elif dt > 0:
            self.pal += (tgt - self.pal) * (1.0 - math.exp(-dt / 1.0))
        self.drift = (self.drift + float(p["drift_speed"]) * spd * dt) % 1000.0

        # ---- the cycle: on the bar grid when the music clock is sure, else cycle_s on the shared photon clock
        t_want = cycle_s / max(spd, 1e-3)
        clock = self._clock() if spd > 0.05 else None
        target = None
        rate = spd / cycle_s
        span = None
        beat_s = None
        if clock is not None:
            try:
                bpm, bpb = float(clock.bpm), max(1, int(clock.beats_per_bar))
                bar_s = bpb * 60.0 / bpm
                if bpm > 0.0 and math.isfinite(bar_s) and bar_s > 0.0:
                    x = math.log2(t_want / bar_s)
                    if self.nbars is None or abs(x - math.log2(self.nbars)) > 0.75:
                        self.nbars = min(NBARS, key=lambda c: abs(x - math.log2(c)))
                    span = bpb * self.nbars
                    beat_s = 60.0 / bpm
                    rate = (bpm / 60.0) / span
                    target = ((float(clock.beats) - float(clock.bar_beat)) / span) % 1.0
            except Exception:
                target, rate, span = None, spd / cycle_s, None
        if target is None:
            self.nbars = None
            span = None
            target = (t_abs * rate) % 1.0 if rate > 0.0 else None
        if first and target is not None:
            self.phi = target
        else:
            self.phi = _advance(self.phi, rate, dt, target, 1.0 if clock is not None else 3.0)
        self.phi %= 1.0
        f_c = self.phi
        t_cycle = 1.0 / rate if rate > 1e-6 else cycle_s
        near = min(f_c, 1.0 - f_c) * t_cycle
        breath = math.exp(-(near / BREATH_S) ** 2)
        fw = f_c - WARP / (2.0 * math.pi) * math.sin(2.0 * math.pi * f_c)   # the pattern lingers at the alignment
        wind = math.sin(math.pi * fw) ** 2                                   # 0 aligned -> 1 mid-cycle -> 0

        # ---- the common swing: a whole number K of swings per cycle (on the beat grid: one every m beats), so every
        # ball is at the far end of its swing at the realignment; pulled softly when K changes, never backwards
        if span is not None and beat_s is not None:
            cands = [m for m in BEATS if span % m == 0] or [1]
            m = min(cands, key=lambda c: abs(math.log(c * beat_s / SWING_S)))
            K = max(1, span // m)
            if self.K is not None and span % self.K == 0 and \
                    abs(math.log((t_cycle / self.K) / SWING_S)) < 0.45:
                K = self.K
        else:
            K = max(2, int(round(cycle_s / SWING_S)))
            if self.K is not None and abs(math.log((cycle_s / self.K) / SWING_S)) < 0.3:
                K = self.K
        self.K = K
        tgt_psi = (K * f_c) % 1.0 if rate > 0.0 else None
        if first and tgt_psi is not None:
            self.psi = tgt_psi
        else:
            self.psi = _advance(self.psi, K * rate, dt, tgt_psi, 1.0)
        self.psi %= 1.0

        # ---- the camera sway, on the shared clock too (so a segment's screens agree)
        rate_c = spd / CAM_PERIOD_S
        tgt_c = (t_abs * rate_c) % 1.0 if rate_c > 0 else None
        if first and tgt_c is not None:
            self.cam = tgt_c
        else:
            self.cam = _advance(self.cam, rate_c, dt, tgt_c, 4.0)
        self.cam %= 1.0
        c2 = 2.0 * math.pi * self.cam

        # ---- per-key swing (a reach in world units, so the idle row lines up straight) and light
        pc = self.pc
        L = self.length * (1.0 - 0.3 * layout)                                  # a ring: shorter threads
        sym_c = np.bincount(pc, weights=lv, minlength=12)[pc] - lv               # same pitch class, sounding
        sym = d_s * np.minimum(np.maximum(sym_c, 0.0), 1.0)
        idle = (0.42 + 0.14 * e_s) * (1.0 + 0.3 * d_s) \
            * (0.93 + 0.07 * np.sin(2.0 * np.pi * 1.3 * self.t_reg + 2.0 * c2))
        lvp = np.power(lv, 0.7)
        tgt_amp = swing * (idle + 0.95 * lvp + 0.2 * sym)
        if first:
            self.amp[:] = tgt_amp
        elif dt > 0:
            a_up, a_dn = 1.0 - math.exp(-dt / 0.12), 1.0 - math.exp(-dt / 0.5)
            self.amp += (tgt_amp - self.amp) * np.where(tgt_amp > self.amp, a_up, a_dn)
        np.nan_to_num(self.amp, copy=False)
        reach = np.minimum(np.maximum(self.amp, 0.0), 0.85 * L)
        th_amp = np.arcsin(reach / L)
        bloom = vel * (1.0 - np.exp(-age / 0.04)) * np.exp(-age / 0.55)
        glow = (0.36 + 0.2 * e) + 1.5 * np.power(lv, 0.8) + 1.8 * bloom + 0.35 * sym + 0.5 * pulse + 0.5 * breath
        glow *= 1.0 - 0.25 * soft
        # colour: the pitch class, round a hue wheel tinted by the palette (each octave one rainbow, C to B)
        wheel = _even(_wheel(self.hue + 0.25 * self.drift))
        palc = _even(self._pal_lin(self.hue * float(p["palette_period"]) + self.drift))
        col = _even((1.0 - hue_mix) * palc + hue_mix * wheel)
        col = 0.9 * col + 0.1 * 0.25
        em = col * glow[:, None]

        # ---- geometry: the rod bends from a row into an arc into a ring
        s_scale = max(self.nv, 1.0) ** 0.6                                      # a longer row across n screens
        spacing = SPACING * s_scale * (1.0 + 0.5 * layout)                       # ... and a wider ring
        s = self.s_idx * spacing
        total = N * spacing
        kap = layout * 2.0 * math.pi / total
        if kap > 1e-6:
            xs, zs = np.sin(kap * s) / kap, (1.0 - np.cos(kap * s)) / kap
        else:
            xs, zs = s.copy(), 0.5 * kap * s * s
        xs -= xs.mean()
        zs -= zs.mean()
        nx, nz = -np.sin(kap * s), np.cos(kap * s)
        piv = np.stack([xs, np.full(N, Y_BAR), zs], 1)
        phase = 2.0 * np.pi * (self.psi + self.c_row * waves * wind)
        th = th_amp * np.cos(phase)
        sw = L * np.sin(th)
        bob = np.stack([xs + sw * nx, Y_BAR - L * np.cos(th), zs + sw * nz], 1)
        rad = BOB_R * math.sqrt(s_scale) * self.size * (1.0 + 0.5 * lvp + 0.35 * bloom)

        # ---- the camera: from the treble end of the row, a 3/4 view down its length (low and end-on -> high and
        # overhead), framed as tightly as the swing envelope allows on this (virtual) picture
        az0 = 1.08 - 1.0 * camp
        az = -(az0 + (0.10 + 0.45 * layout) * (0.7 + 0.6 * e_s) * math.sin(c2))
        el = 0.24 + 0.85 * camp + 0.10 * layout + 0.04 * (0.5 + e_s) * math.sin(2.0 * c2 + 1.1)
        el = min(max(el, 0.08), 1.35)
        dirv = np.array([math.sin(az) * math.cos(el), math.sin(el), -math.cos(az) * math.cos(el)])
        fwd = -dirv
        right = np.cross(fwd, np.array([0.0, 1.0, 0.0]))
        right /= max(np.linalg.norm(right), 1e-9)
        upv = np.cross(right, fwd)
        tan_v = math.tan(math.radians(FOV_DEG) * 0.5)
        w, h = self.w, self.h
        aspect = self.nv * w / h
        tan_h = tan_v * aspect
        de = np.minimum(D_ENV * max(swing, 0.3), 0.85 * L)
        ye = Y_BAR - np.sqrt(np.maximum(L * L - de * de, 0.0))
        env = np.concatenate([piv, np.stack([xs, Y_BAR - L, zs], 1),
                              np.stack([xs + de * nx, ye, zs + de * nz], 1),
                              np.stack([xs - de * nx, ye, zs - de * nz], 1)])
        eye, depth = _fit(env, fwd, right, upv, 0.96 * tan_h, 0.92 * tan_v)
        zoom = (1.0 + 0.06 * e_s) * (1.0 + 0.015 * math.sin(3.0 * c2))
        eye = eye + fwd * ((1.0 - 1.0 / zoom) * max(float(np.min(depth)), 0.0))
        sp = tan_v / (0.5 * h)                       # tangent per internal pixel
        x_off = self.kv * w
        wv = self.nv * w

        def project(pts):
            d = pts - eye
            z = d @ fwd
            zc = np.maximum(z, NEAR)
            sx = np.clip(0.5 * wv + (d @ right) / (zc * sp) - x_off, -2e4, 2e4)
            sy = np.clip(0.5 * h - (d @ upv) / (zc * sp), -2e4, 2e4)
            return sx, sy, z

        spx, spy, zp = project(piv)
        sbx, sby, zb = project(bob)
        nxt = np.roll(np.arange(N), -1)
        snx, sny, zn = spx[nxt], spy[nxt], zp[nxt]
        ok = (zp > NEAR) & (zb > NEAR)
        if first:
            self.prev_sx[:], self.prev_sy[:], self.prev_ok[:] = sbx, sby, ok
        pvx = np.where(self.prev_ok & ok, self.prev_sx, sbx)
        pvy = np.where(self.prev_ok & ok, self.prev_sy, sby)
        self.prev_sx[:], self.prev_sy[:], self.prev_ok[:] = sbx, sby, ok
        rpx = rad / (np.maximum(zb, NEAR) * sp)
        barw = np.ones(N)
        barw[N - 1] = min(max((layout - 0.85) / 0.15, 0.0), 1.0) ** 2           # the ring closes
        bar_hw = BAR_R / (np.maximum(np.minimum(zp, zn), NEAR) * sp)
        gl = np.minimum(glow, 3.0)
        halo_r = rpx * (1.5 + 0.8 * gl)
        # the wave line: ball -> next ball, lit where neighbours swing together (the snake; all of it at the breath)
        dsw = np.abs(sw - sw[nxt])
        sig = 0.05 + 0.3 * float(np.median(reach))
        line = line_p * (1.8 + 20.0 * breath) * (0.7 + 0.5 * e_s) * np.exp(-(dsw / sig) ** 2)
        line[N - 1] = 0.0
        line = np.where(ok & ok[nxt], line, 0.0)
        lnx = np.where(line > 0, sbx[nxt], sbx)
        lny = np.where(line > 0, sby[nxt], sby)
        lpad = np.maximum.reduce([2.25 * halo_r, 3.0 * np.maximum(rpx * 0.6, 0.7) + 1.0,
                                  3.0 * np.maximum(rpx * 0.35, 0.6) + 1.0, rpx + 2.5])
        lx0 = np.minimum.reduce([sbx, pvx, lnx]) - lpad                         # the ball's own light
        lx1 = np.maximum.reduce([sbx, pvx, lnx]) + lpad
        ly0 = np.minimum.reduce([sby, pvy, lny]) - lpad
        ly1 = np.maximum.reduce([sby, pvy, lny]) + lpad
        pad = bar_hw + 2.0                                                      # rod and thread
        x0 = np.minimum.reduce([lx0, spx - pad, np.where(barw > 0, snx, spx) - pad])
        x1 = np.maximum.reduce([lx1, spx + pad, np.where(barw > 0, snx, spx) + pad])
        y0 = np.minimum.reduce([ly0, spy - pad, np.where(barw > 0, sny, spy) - pad])
        y1 = np.maximum.reduce([ly1, spy + pad, np.where(barw > 0, sny, spy) + pad])
        valid = ok & (x1 >= 0) & (x0 <= w) & (y1 >= 0) & (y0 <= h)

        # ---- the arena
        A = self.A
        A[:] = 0.0
        Pr = A[O_P:O_CL].reshape(N, PS)
        Pr[:, R["PIV"]:R["PIV"] + 3] = piv
        Pr[:, R["BOB"]:R["BOB"] + 3] = bob
        Pr[:, R["THR"]] = 0.05 + 0.10 * breath + 0.03 * d_s
        Pr[:, R["EM"]:R["EM"] + 3] = em
        Pr[:, R["TINT"]:R["TINT"] + 3] = col * 0.25
        Pr[:, R["RPX"]] = rpx
        Pr[:, R["SPIV"]], Pr[:, R["SPIV"] + 1] = spx, spy
        Pr[:, R["SBOB"]], Pr[:, R["SBOB"] + 1] = sbx, sby
        Pr[:, R["SPREV"]], Pr[:, R["SPREV"] + 1] = pvx, pvy
        Pr[:, R["SNEXT"]], Pr[:, R["SNEXT"] + 1] = snx, sny
        Pr[:, R["BARW"]] = barw
        Pr[:, R["ZPIV"]], Pr[:, R["ZBOB"]], Pr[:, R["ZNEXT"]] = zp, zb, zn
        Pr[:, R["BB"]], Pr[:, R["BB"] + 1], Pr[:, R["BB"] + 2], Pr[:, R["BB"] + 3] = x0, y0, x1, y1
        Pr[:, R["LB"]], Pr[:, R["LB"] + 1], Pr[:, R["LB"] + 2], Pr[:, R["LB"] + 3] = lx0, ly0, lx1, ly1
        Pr[:, R["TRAIL"]] = np.where(ok, (0.03 + 0.6 * glow) / np.maximum(glow, 1e-3), 0.0)  # a comet tail
        Pr[:, R["HALO"]] = 1.0
        Pr[:, R["HALOR"]] = halo_r
        Pr[:, R["VALID"]] = valid.astype(np.float32)
        Pr[:, R["RAD"]] = rad
        Pr[:, R["LINE"]] = line
        lumw = np.maximum(_lum(em), 1e-4).reshape(NCL, CLUSTER)
        Cl = A[O_CL:N_AR].reshape(NCL, CS)
        Cl[:, 0:3] = (bob.reshape(NCL, CLUSTER, 3) * lumw[:, :, None]).sum(1) / lumw.sum(1)[:, None]
        Cl[:, 3:6] = em.reshape(NCL, CLUSTER, 3).sum(1)

        tau = (0.05 + 0.55 * trail ** 1.5) * (1.0 + 0.8 * d_s) * (1.0 + 1.2 * breath)
        sky = self._pal_lin(np.array([0.62 + self.drift, 0.35 + self.drift]))
        sky = _lum(sky)[:, None] + 0.45 * (sky - _lum(sky)[:, None])           # a muted room: the balls carry colour
        sky_top = sky[0] * 0.010 + np.array([0.002, 0.002, 0.003])
        sky_hor = sky[1] * 0.035 + np.array([0.004, 0.004, 0.005])
        metal = self._pal_lin(np.array([0.10 + self.drift]))[0]
        barc = (0.7 * np.array([0.62, 0.55, 0.42]) + 0.3 * metal / max(float(metal.max()), 0.05)) * 0.30
        glowc = self._pal_lin(np.array([0.15 + self.drift]))[0]
        glowc = glowc / max(float(_lum(glowc)), 0.05) * 0.12
        key = np.array([-0.45, 0.80, -0.40])
        rim = np.array([0.55, 0.35, 0.75])
        gv = A[:NG]
        gv[G["EYE"]:G["EYE"] + 3] = eye
        gv[G["FWD"]:G["FWD"] + 3] = fwd
        gv[G["RIGHT"]:G["RIGHT"] + 3] = right
        gv[G["UP"]:G["UP"] + 3] = upv
        gv[G["S"]] = sp
        gv[G["XOFF"]] = x_off
        gv[G["WV"]] = wv
        gv[G["HV"]] = h
        gv[G["YFLOOR"]] = Y_FLOOR
        gv[G["FLOOR"]] = floor_amt
        gv[G["BARR"]] = BAR_R
        gv[G["THREADW"]] = 0.45
        gv[G["DECAY"]] = math.exp(-dt / tau)
        gv[G["DTTAU"]] = dt / tau
        gv[G["TRAILK"]] = (0.4 + 0.9 * e) * (0.7 + 0.6 * trail)
        gv[G["HALOK"]] = 0.20 * halo_p * (0.6 + 0.6 * e)
        gv[G["SKYTOP"]:G["SKYTOP"] + 3] = sky_top
        gv[G["SKYHOR"]:G["SKYHOR"] + 3] = sky_hor
        gv[G["FLOORC"]:G["FLOORC"] + 3] = sky_hor * 0.15
        gv[G["GAIN"]] = 1.1 * min(max(float(p["intensity"]), 0.0), 2.0)
        gv[G["BREATH"]] = breath
        gv[G["BOBR"]] = float(rad.mean())
        gv[G["KEY"]:G["KEY"] + 3] = key / np.linalg.norm(key)
        gv[G["RIM"]:G["RIM"] + 3] = rim / np.linalg.norm(rim)
        gv[G["BARC"]:G["BARC"] + 3] = barc
        gv[G["GLOWC"]:G["GLOWC"] + 3] = glowc
        gv[G["TRAILW"]] = 0.6
        gv[G["VIG"]] = 0.45
        gv[G["HAZE"]] = 0.5 + 0.6 * e_s + 0.4 * breath
        gv[G["FOG"]] = 0.045
        gv[G["THRC"]:G["THRC"] + 3] = np.array([0.55, 0.58, 0.62])
        gv[G["LINEW"]] = 0.35
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.arena.set(A)
        launch_2d(self.k_render, w, h, (self.buf, self.acc, self.arena, np.int32(w), np.int32(h)))
        present(self.ctx, self.buf, out)
