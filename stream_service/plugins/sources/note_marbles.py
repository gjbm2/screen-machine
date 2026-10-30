"""Note marbles: every piano note drops a glowing glass marble into a keyboard-shaped tray at its own key.

Source: Original (inspired by DoodleChaos, "Impossible Piano", https://www.patreon.com/posts/65716919, and the
        marble machines of Animusic, https://en.wikipedia.org/wiki/Animusic.  Only the idea is taken: each note
        becomes a physical body that falls, bounces, rolls and piles up.  No code, shaders or assets from either.)
Author: smfx (original)
License: MIT (original)
license_class: permissive

What the picture shows
  A product shot.  A long tray of black lacquer, shaped like a piano keyboard, floats above the soft warm floor of a
  dark studio under a warm overhead softbox.  The camera is close, at a three-quarter angle from the treble side,
  looking down into the grooves: a section of about 18 keys around where the music is fills the frame (the lip
  along the bottom, the back wall across the middle), and it pans along the keyboard to follow the register.  The
  far end of the tray and the studio behind fall out of focus (depth of field), with slow coloured bokeh far off.
  The tray has 88 narrow grooves, one per key, bass on the left and treble on the right.
  The floor of each groove is its key: ivory for the white keys and smoked glass for the black ones, so the
  keyboard's pattern of twos and threes runs along the tray.  Each groove's key glows in its note's colour while
  that note sounds.
  Every note the piano plays drops a glass marble into its own groove.  The marble bounces, rolls back along the
  groove (the tray slopes gently away from the viewer) and settles against the marbles already there.  Marbles are
  clear coloured glass with a glowing twisted ribbon inside, like a cat's-eye, and the ribbon turns as they roll.
  Through each marble you see the lit keys behind it, magnified and upside down.  Each marble lights the lacquer
  and the keys around it in its colour, and casts a soft shadow under the overhead light.  The marbles' light also
  falls on the studio floor around the tray (over the lip in front, a wide soft glow behind), so the room takes the
  music's colours.
  Bass marbles and treble marbles differ in colour (the palette runs along the keyboard).  The newest marble of a
  note glows as long as the note sounds, older ones cool to embers.  The grooves fill and a bed of marbles builds.
  The tray leans a little towards the bass, so the bed fills up from the bass end.  When the bed reaches the front
  lip, marbles spill over it and tumble away into the dark.
  Lifting the damper pedal opens a grate along the back of the tray.  Warm light pours up through it, a curtain of
  light against the back wall, the tray tips back, and the marbles rumble backwards and pour through the gap,
  flaring as they drop and shrinking away into the dark.  A quick pedal change is a breath of that light (and, once
  the tray is full, gulps its back row, so it breathes with the pedal and never clogs).  The longer the foot stays
  up the wider the grate opens; a long lift, or the pause at the end of a phrase, empties the tray.

How the music drives it
  * Each onset is one marble at its key, blooming in from a quarter of its size over 0.08 s, so there is never a
    hard flash.  The marble is thrown down harder and from higher the louder the note, so a loud note bounces high
    and knocks its neighbours (2.9 key-widths at velocity 1), and a soft one lands tenderly (0.7).  Loud marbles
    are also bigger: their radius is 0.78 + 0.22 x vel^0.8 of the base, and every one still fits its groove.  Ages
    are at photon time, so a marble already has its fall advanced by the onset's age.  The lean towards the bass
    only moves slow marbles (the bed), never one in flight, so a high bounce lands back in its own groove.
  * A fast run is a wave of marbles sweeping along the grooves, and a big chord a burst.  More than 64 onsets in a
    frame are dropped, and a key takes at most 3 marbles a frame, stacked in the air.
  * The newest marble of each pitch glows with that note's LEVEL (bloom-in from AGE_ON), so held and pedal-held
    notes stay lit, and the key under it glows with it.  Impacts make a marble's glass glint and its core flare
    for a moment ("clicks").
  * Damper pedal down: resonance, so every marble glows up to 60 % brighter and the studio warms.  Pedal lift
    (falling edge): a flash of light from the grate.  While the foot stays up the grate opens (and the tray tips
    up to 8 degrees back) by smoothstep(0.3 - 0.25 drain, 0.9 - 0.5 drain, seconds up), for up to 0.35 + 2.4 x
    ``drain`` seconds: quick legato changes drain nothing, a held lift drains the back rows or all of it.  Once
    about 225-450 onsets have fallen since the last full drain, every lift also opens it to 0.65 for 0.3 s (a gulp
    of the back row).  A rest (no note for 4.5 s, pedal up) empties the tray.
  * ``sound.register`` (the ensemble's register split): notes outside this screen's window drop dimmer marbles and
    glow dimmer keys, never hidden.  ``sound.segment`` (k, n): the screens share one camera with a lens shift, so
    one long tray runs across the room's TVs (bass on the left).  Its drift runs on the shared photon clock.
  * No sound, or a pause of 3 s: an idle music box.  Soft marbles fall on a slow pentatonic random walk (``idle``),
    and the tray drains each time 45 of them have fallen.  The tray rocks gently, so the bed of marbles shifts like
    a slow tide.  The camera drifts and coloured bokeh float behind.  Alive with no notes at all.

Macros
  energy -> camera drift (amplitude 1 + 2.2 e, rate 0.4 + 1.1 e), the tray's rocking (0.5 to 1.8 degrees), marble
  and key glow (x 0.75 to 1.25), the bokeh, and the idle drop rate.
  pulse (note_on kick) -> impact sparkle: every marble's glass glints and its core flares, eased in over 0.05 s
  and out over 0.3 s.  It is an amplitude only, never a position.
  speed -> the physics' time step (slow motion to double speed), the camera drift, the rocking and the idle rate.
  intensity -> exposure.  palette / palette_period / drift_speed -> the marble and key colours along the
  keyboard: index = (pitch - 21) / 87 x period + drift.
  Regional: marble_size, bounciness, glow, camera, drain, lean, opal (clear crystal -> milky opal) and idle.
  camera: how many keys the shot spans, exponentially from the whole keyboard (0, a near-frontal overview) to about
  11 (1), lower, more three-quarter and with a wider lens the closer it is.  The section's box (lip to back wall,
  floor to drop height) is fitted in closed form, loosely sideways so the tray runs out of both sides of the frame;
  above 0.6 the lip and the drop height may crop.  The section follows where the music is (an eased,
  velocity-weighted average of recent notes) and never leaves the keyboard.  The depth of field grows with it.
  Default 0.75: about 18 keys.  On a shared camera across several screens: the whole keyboard, no depth of field.

The engine (all on the GPU, no host round trips)
  Up to 4096 marbles in preallocated float4 buffers: position + radius, velocity + sparkle, orientation
  quaternion, spin + age, and meta (pitch, size, velocity, dim).  The host keeps a ring head: each onset becomes
  one 16-float spawn row in a single small arena upload per frame (about 6 KB with the camera, the per-pitch
  owner, idle-glow and register tables).  The oldest 512 ring slots shrink away before they are reused, so a
  marble never pops.
  Physics runs in the tray's own frame, where the tray is axis-aligned boxes; the slope, lean, rocking and drain
  tilt are just the direction of gravity there.  Each substep (1/240 s, at most 8 a frame) does a counting sort
  of the marbles into a dense uniform grid (cell = the largest diameter; count, scan, scatter).  Then one Jacobi
  pass per marble against at most 64 neighbours in its 27 cells.  Contacts use split impulses: velocity impulses
  give restitution and friction, and overlaps are pushed apart in position only (clamped to 0.3 r), so a burst
  or a size change never pumps energy into the pile.  The marbles then collide with the floor plate, the two
  groove walls beside them, the lip, the back and the end walls (box closest points).  Rolling sets the spin
  (omega = n x v / r), which turns the quaternion.  Marbles that fall below the tray, or that go non-finite, die.
  Rendering: a visibility buffer, where each marble rasterises its exact projected bounds and does a ray-sphere
  test per pixel, keeping the nearest marble with a 64-bit atomicMin of (depth, id).  The tray is ray-traced
  analytically: box slabs, plus the comb of groove walls solved in closed form.  The per-pixel shade pass draws
  glass marbles (Schlick Fresnel, a studio softbox reflection, two analytic refractions through the sphere onto
  the lit keys behind, and the cat's-eye ribbon sampled in the marble's frame) and the lacquer and keys.  It adds
  ambient occlusion, soft shadows and coloured light pools from the marbles in the grid around each tray point.
  Rays that miss the tray hit the studio floor (a world plane: the overhead light's pool, the tray's soft shadow,
  the per-key marble light that mb_emit gathers, the drain's glow) or the dark backdrop.  The shade pass writes
  linear light and a signed circle of confusion; a quarter-resolution copy is blurred (two widths) and the
  composite mixes sharp to blurred by it (the near blur spreads over its edges), adds a quarter-resolution bloom
  (glow splats from the glowing marbles only, a separable blur) and a soft vignette, then ACES tonemap and present.
  About 31 launches a frame.  Measured 4.8 ms at 4K on the P4000 (render_scale 0.5, fxtest --bench, a sparse tray);
  about 5.3 ms a frame averaged over 40 s of the Tango filling the tray (wall clock, GPU 1).  The step kernel's rules were checked in a numpy port on the CPU: a settled bed (overlap < 2 % of r,
  no floor penetration, bottom marbles centred in their grooves), bounces of 0.7 / 1.2 / 3.0 key-widths at
  velocity 0.2 / 0.6 / 1.0, a size jump and a 30-marble burst resolved without blowing up, and a full drain in
  1.5 s.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES
from stream_service.plugins._lib.params import num, standard_scene_params
from stream_service.plugins._lib.upsample import internal_size, present
from stream_service.plugins.base import Source

log = logging.getLogger("stream_service.plugins.note_marbles")

# ---------------------------------------------------------------------------------------------- constants
N_MAX = 4096                    # marbles (ring buffer)
SPAWN_MAX = 64                  # onsets taken per frame
SPW = 16                        # floats per spawn row
RETIRE = 512                    # the oldest ring slots shrink away before they are reused
NB_CAP = 64                     # neighbour candidates per marble per substep
NEAR_CAP = 40                   # marbles a tray pixel looks at (shadow, AO, light pools)
LOW, HIGH = 21, 108             # the piano's keys (A0..C8): 88 grooves

# the tray (its own frame: key pitch = 1 unit, x along the keyboard, y up, +z towards the viewer)
XH = 44.0                       # half the keyboard (88 grooves)
ZH = 4.0                        # half a groove's length: front lip at +ZH, back wall at -ZH
HW, TW = 0.55, 0.04             # groove walls: height, half thickness (a 0.92 gap: every marble fits)
LH, LW = 0.78, 0.5              # front lip: height, depth
BH, BW = 3.2, 0.5               # back wall
EH, EW = 3.2, 0.6               # end walls
PT = 0.6                        # floor plate thickness
GATE = 2.2                      # how far the floor plate slides forward when the grate is fully open
SLOPE_DEG = 3.5                 # the floor falls away from the viewer: marbles roll back and queue
DRAIN_TILT_DEG = 8.0            # the tray tips back while the grate is open
LEAN_DEG = 3.0                  # lean towards the bass at lean = 1
GRAV = 90.0                     # key widths / s^2 (a slow, legible fall)
R_BASE = 0.42                   # marble radius at marble_size 1, before the velocity factor
KILL_Y = -3.6                   # below this a drained marble is gone (it shrinks away as it falls)
FLOOR_Y = -4.6                  # the studio floor (world), under the floating tray
SUB_H = 1.0 / 240.0             # physics substep
SUB_MAX = 8
VMIN, VMAX, SLEEP_V = 1.2, 70.0, 0.2
MU_MM = 0.03                    # marble-marble tangential damping
BLOOM_S = 0.08                  # a marble blooms in over this
N_KEYS = 88

# the collision grid (dense, over the tray's neighbourhood)
G_ORG = (-47.0, -3.0, -7.5)
G_EXT = (94.0, 12.5, 15.0)
CS_MIN = 0.5
G_DIM_MAX = tuple(int(math.ceil(e / CS_MIN)) for e in G_EXT)
G_CELLS = G_DIM_MAX[0] * G_DIM_MAX[1] * G_DIM_MAX[2]

# grate / rests / idle
REST_S = 4.5                    # no note this long, pedal up: the phrase is over, drain
IDLE_AFTER = 3.0                # the idle music box starts after this much quiet
IDLE_FILL = 45                  # idle drops between idle drains
FILL_N = 450                    # onsets since the last full drain at which the tray counts as full
AMB_DIM = 0.85
PENTA = (0, 2, 4, 7, 9)
LADDER = [n for n in range(43, 91) if n % 12 in PENTA]

# the arena (one float32 upload per frame)
_LAYOUT = (("GRAV", 3), ("H", 1), ("CS", 1), ("ICS", 1), ("GDIM", 3), ("GORG", 3), ("R0", 1), ("EFLOOR", 1),
           ("EMM", 1), ("ZG", 1), ("HEAD", 1), ("NSPAWN", 1), ("AIR", 1), ("FRIC", 1),
           ("EYE", 3), ("FW", 3), ("RT", 3), ("UP", 3), ("FOC", 1), ("CXG", 1), ("CY", 1), ("TWID", 1),
           ("WPX", 1), ("HPX", 1), ("GWPX", 1), ("GHPX", 1), ("GLOW", 1), ("OPAL", 1), ("ENERGY", 1), ("SPARK", 1),
           ("RES", 1), ("DRIFT", 1), ("PERIOD", 1), ("TIME", 1), ("EXPO", 1), ("KEYGLOW", 1), ("BLOOM", 1),
           ("SEED", 1), ("FOG0", 1), ("BG", 3), ("LIGHT", 3), ("LCOL", 3), ("FLN", 3), ("FLY", 1), ("SPOT", 3),
           ("OPEN", 1), ("GAPCOL", 3), ("FOCUS", 1), ("DOF", 1), ("KLK", 1),
           ("PAL", 12), ("OWN", 128), ("AMB", 128), ("REGW", 128),
           ("SPAWN", SPAWN_MAX * SPW))
OFF: dict[str, int] = {}
_o = 0
for _k, _n in _LAYOUT:
    OFF[_k] = _o
    _o += _n
N_AR = _o
_DEFS = "\n".join(f"#define A_{k} {v}" for k, v in OFF.items())

_SRC = HELPERS + PALETTE_CUDA + f"""
{_DEFS}
#define MB_N {N_MAX}
#define MB_SPMAX {SPAWN_MAX}
#define MB_SPW {SPW}
#define MB_RETIRE {float(RETIRE)}f
#define MB_NBCAP {NB_CAP}
#define MB_NEARCAP {NEAR_CAP}
#define T_XH {XH}f
#define T_ZH {ZH}f
#define T_HW {HW}f
#define T_TW {TW}f
#define T_LH {LH}f
#define T_LW {LW}f
#define T_BH {BH}f
#define T_BW {BW}f
#define T_EH {EH}f
#define T_EW {EW}f
#define T_PT {PT}f
#define MB_KILLY {KILL_Y}f
#define MB_VMIN {VMIN}f
#define MB_VMAX {VMAX}f
#define MB_SLEEPV {SLEEP_V}f
#define MB_MUMM {MU_MM}f
#define MB_BLOOMS {BLOOM_S}f
""" + r"""
// ------------------------------------------------------------------------------------------- vector helpers
__device__ __forceinline__ float3 operator+(float3 a, float3 b) { return make_float3(a.x + b.x, a.y + b.y, a.z + b.z); }
__device__ __forceinline__ float3 operator-(float3 a, float3 b) { return make_float3(a.x - b.x, a.y - b.y, a.z - b.z); }
__device__ __forceinline__ float3 operator-(float3 a) { return make_float3(-a.x, -a.y, -a.z); }
__device__ __forceinline__ float3 operator*(float3 a, float s) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float s, float3 a) { return make_float3(a.x * s, a.y * s, a.z * s); }
__device__ __forceinline__ float3 operator*(float3 a, float3 b) { return make_float3(a.x * b.x, a.y * b.y, a.z * b.z); }
__device__ __forceinline__ float mb_dot(float3 a, float3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
__device__ __forceinline__ float3 mb_cross(float3 a, float3 b) {
    return make_float3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
__device__ __forceinline__ float mb_len(float3 a) { return sqrtf(mb_dot(a, a)); }
__device__ __forceinline__ float3 mb_norm(float3 a) { return a * rsqrtf(fmaxf(mb_dot(a, a), 1e-20f)); }
__device__ __forceinline__ float3 mb_v3(const float* __restrict__ A, int o) { return make_float3(A[o], A[o + 1], A[o + 2]); }
__device__ __forceinline__ float3 mb_xyz(float4 a) { return make_float3(a.x, a.y, a.z); }
__device__ __forceinline__ float3 mb_f3(float s) { return make_float3(s, s, s); }
__device__ __forceinline__ bool mb_ok(float v) { return fabsf(v) < 1e4f; }       // false for NaN and inf
// rotate v by the unit quaternion q = (x, y, z, w): v + 2 u x (u x v + w v)
__device__ __forceinline__ float3 mb_qrot(float4 q, float3 v) {
    const float3 u = make_float3(q.x, q.y, q.z);
    const float3 t = mb_cross(u, v) + v * q.w;
    return v + mb_cross(u, t) * 2.f;
}

// a note's colour (linear, brightest channel 1): the palette along the keyboard
__device__ __forceinline__ float3 mb_note_col(const float* __restrict__ A, float pitch, float jit) {
    const float t = (pitch - 21.f) * (1.f / 87.f) * A[A_PERIOD] + A[A_DRIFT] + jit;
    float3 c = pal(t, A + A_PAL);
    c = c * c;
    const float m = fmaxf(fmaxf(c.x, c.y), fmaxf(c.z, 0.2f));          // a dark palette stretch stays dark, not a stray hue
    return c * (1.f / m);
}

// ------------------------------------------------------------------------------------------- the grid
__device__ __forceinline__ int mb_cell(const float* __restrict__ A, float3 p, int& cx, int& cy, int& cz) {
    const float ics = A[A_ICS];
    const float fx = fminf(fmaxf((p.x - A[A_GORG]) * ics, -8.f), 4000.f);
    const float fy = fminf(fmaxf((p.y - A[A_GORG + 1]) * ics, -8.f), 4000.f);
    const float fz = fminf(fmaxf((p.z - A[A_GORG + 2]) * ics, -8.f), 4000.f);
    cx = (int)floorf(fx); cy = (int)floorf(fy); cz = (int)floorf(fz);
    const int gx = (int)A[A_GDIM], gy = (int)A[A_GDIM + 1], gz = (int)A[A_GDIM + 2];
    if (cx < 0 || cy < 0 || cz < 0 || cx >= gx || cy >= gy || cz >= gz) return -1;
    return (cz * gy + cy) * gx + cx;
}

extern "C" __global__ void mb_hash(const float4* __restrict__ P, int* __restrict__ cell, int* __restrict__ rank,
                                   int* __restrict__ count, const float* __restrict__ A) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= MB_N) return;
    const float4 p = P[i];
    int c = -1;
    if (p.w > 0.f && mb_ok(p.x) && mb_ok(p.y) && mb_ok(p.z)) {
        int cx, cy, cz;
        c = mb_cell(A, mb_xyz(p), cx, cy, cz);
    }
    cell[i] = c;
    rank[i] = c >= 0 ? atomicAdd(&count[c], 1) : 0;
}

extern "C" __global__ void __launch_bounds__(1024) mb_scan(const int* __restrict__ count, int* __restrict__ start,
                                                           const float* __restrict__ A) {
    __shared__ int s[1024];
    const int nc = (int)A[A_GDIM] * (int)A[A_GDIM + 1] * (int)A[A_GDIM + 2];
    const int t = threadIdx.x, per = (nc + 1023) / 1024, b = t * per, e = min(b + per, nc);
    int sum = 0;
    for (int k = b; k < e; ++k) sum += count[k];
    s[t] = sum;
    __syncthreads();
    for (int off = 1; off < 1024; off <<= 1) {
        const int v = t >= off ? s[t - off] : 0;
        __syncthreads();
        s[t] += v;
        __syncthreads();
    }
    int run = s[t] - sum;
    for (int k = b; k < e; ++k) { start[k] = run; run += count[k]; }
}

extern "C" __global__ void mb_scatter(const int* __restrict__ cell, const int* __restrict__ rank,
                                      const int* __restrict__ start, int* __restrict__ sidx) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= MB_N) return;
    const int c = cell[i];
    if (c < 0) return;
    const int k = start[c] + rank[i];
    if (k >= 0 && k < MB_N) sidx[k] = i;
}

// ------------------------------------------------------------------------------------------- spawning
// row: slot x y z vx vy vz pitch size vel dim age rnd - - -
extern "C" __global__ void mb_spawn(float4* __restrict__ P, float4* __restrict__ V, float4* __restrict__ Q,
                                    float4* __restrict__ W, float4* __restrict__ M, const float* __restrict__ A) {
    const int j = threadIdx.x;
    const int n = (int)A[A_NSPAWN];
    if (j >= n || j >= MB_SPMAX) return;
    const float* s = A + A_SPAWN + MB_SPW * j;
    const int i = (int)s[0];
    if (i < 0 || i >= MB_N) return;
    const float sz = fminf(fmaxf(s[8], 0.2f), 2.f);
    P[i] = make_float4(s[1], s[2], s[3], fmaxf(A[A_R0] * sz * 0.25f, 1e-3f));
    V[i] = make_float4(s[4], s[5], s[6], 0.f);
    const unsigned int seed = (unsigned int)(fminf(fmaxf(s[12], 0.f), 1.f) * 16777215.f);
    const float u1 = hash1(seed, 11u), u2 = hash1(seed, 12u), u3 = hash1(seed, 13u);
    const float a = sqrtf(1.f - u1), b = sqrtf(u1);                    // a uniform random orientation (Shoemake)
    Q[i] = make_float4(a * __sinf(6.2831853f * u2), a * __cosf(6.2831853f * u2),
                       b * __sinf(6.2831853f * u3), b * __cosf(6.2831853f * u3));
    W[i] = make_float4((u2 - 0.5f) * 6.f, (u3 - 0.5f) * 6.f, (u1 - 0.5f) * 6.f, fmaxf(s[11], 0.f));
    M[i] = make_float4(s[7], sz, s[9], s[10]);
}

// ------------------------------------------------------------------------------------------- the tray (physics)
// push the sphere (c, r) out of the box [lo, hi]; n = the contact normal
__device__ __forceinline__ bool mb_box(float3& c, float r, float3 lo, float3 hi, float3& n) {
    const float3 q = make_float3(fminf(fmaxf(c.x, lo.x), hi.x), fminf(fmaxf(c.y, lo.y), hi.y),
                                 fminf(fmaxf(c.z, lo.z), hi.z));
    const float3 d = c - q;
    const float d2 = mb_dot(d, d);
    if (d2 >= r * r) return false;
    if (d2 > 1e-10f) {
        const float dist = sqrtf(d2);
        n = d * (1.f / dist);
        c = c + n * (r - dist);
        return true;
    }
    float best = c.x - lo.x; n = make_float3(-1.f, 0.f, 0.f);         // centre inside: out by the nearest face
    float t = hi.x - c.x; if (t < best) { best = t; n = make_float3(1.f, 0.f, 0.f); }
    t = c.y - lo.y; if (t < best) { best = t; n = make_float3(0.f, -1.f, 0.f); }
    t = hi.y - c.y; if (t < best) { best = t; n = make_float3(0.f, 1.f, 0.f); }
    t = c.z - lo.z; if (t < best) { best = t; n = make_float3(0.f, 0.f, -1.f); }
    t = hi.z - c.z; if (t < best) { best = t; n = make_float3(0.f, 0.f, 1.f); }
    c = c + n * (fmaxf(best, 0.f) + r);
    return true;
}

__device__ __forceinline__ void mb_contact(float3& v, float3 n, float e, float fr, float& imp, float3& nsum) {
    const float vn = mb_dot(v, n);
    if (vn < 0.f) {
        const float ee = -vn > MB_VMIN ? e : 0.f;
        v = v - n * ((1.f + ee) * vn);
        const float3 vt = v - n * mb_dot(v, n);
        v = v - vt * fr;
        imp = fmaxf(imp, -vn);
    }
    nsum = nsum + n;
}

__device__ void mb_tray(float3& c, float3& v, float r, const float* __restrict__ A, float& imp, float3& nsum) {
    const float zg = A[A_ZG], e = A[A_EFLOOR], fr = A[A_FRIC];
    const bool plate = zg < T_ZH - 1e-3f;
    float3 n;
    #pragma unroll 1
    for (int it = 0; it < 2; ++it) {
        if (plate) {
            if (mb_box(c, r, make_float3(-T_XH, -T_PT, zg), make_float3(T_XH, 0.f, T_ZH), n))
                mb_contact(v, n, e, fr, imp, nsum);
            const int m0 = (int)floorf(fminf(fmaxf(c.x, -100.f), 100.f));
            for (int m = m0; m <= m0 + 1; ++m) {                         // the groove walls either side
                if (m < -43 || m > 43) continue;
                if (mb_box(c, r, make_float3(m - T_TW, 0.f, zg), make_float3(m + T_TW, T_HW, T_ZH), n))
                    mb_contact(v, n, e, fr, imp, nsum);
            }
        }
        if (mb_box(c, r, make_float3(-T_XH, -T_PT, T_ZH), make_float3(T_XH, T_LH, T_ZH + T_LW), n))          // lip
            mb_contact(v, n, e, fr, imp, nsum);
        if (mb_box(c, r, make_float3(-T_XH - T_EW, -T_PT, -T_ZH - T_BW), make_float3(T_XH + T_EW, T_BH, -T_ZH), n))
            mb_contact(v, n, e, fr, imp, nsum);                                                                 // back
        if (mb_box(c, r, make_float3(-T_XH - T_EW, -T_PT, -T_ZH - T_BW), make_float3(-T_XH, T_EH, T_ZH + T_LW), n))
            mb_contact(v, n, e, fr, imp, nsum);                                                                 // bass end
        if (mb_box(c, r, make_float3(T_XH, -T_PT, -T_ZH - T_BW), make_float3(T_XH + T_EW, T_EH, T_ZH + T_LW), n))
            mb_contact(v, n, e, fr, imp, nsum);                                                                 // treble end
    }
}

// ------------------------------------------------------------------------------------------- one substep
extern "C" __global__ void mb_step(const float4* __restrict__ P0, const float4* __restrict__ V0,
                                   float4* __restrict__ P1, float4* __restrict__ V1,
                                   float4* __restrict__ Q, float4* __restrict__ W, const float4* __restrict__ M,
                                   const int* __restrict__ cell, const int* __restrict__ start,
                                   const int* __restrict__ count, const int* __restrict__ sidx,
                                   const float* __restrict__ A) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= MB_N) return;
    const float4 p = P0[i], vv = V0[i], m = M[i];
    const float4 w = W[i];
    const float h = A[A_H];
    const float age = w.w + h;
    const float ring = (float)((i - (int)A[A_HEAD] + 2 * MB_N) % MB_N);
    const float ret = smooth(0.f, MB_RETIRE, ring);
    const float bloom = 0.25f + 0.75f * smooth(0.f, MB_BLOOMS, age);
    const float fall = 0.08f + 0.92f * smooth(MB_KILLY, -0.9f, p.y);    // a drained marble shrinks away as it falls
    const float r = A[A_R0] * m.y * bloom * ret * fall;
    const bool fine = mb_ok(p.x) && mb_ok(p.y) && mb_ok(p.z) && mb_ok(vv.x) && mb_ok(vv.y) && mb_ok(vv.z) &&
                      mb_ok(w.x) && mb_ok(w.y) && mb_ok(w.z);
    if (!(p.w > 0.f) || !(m.y > 0.f) || !fine || ret < 0.04f || p.y < MB_KILLY || !(r > 1e-3f)) {
        P1[i] = make_float4(0.f, -1000.f, 0.f, 0.f);
        V1[i] = make_float4(0.f, 0.f, 0.f, 0.f);
        return;
    }
    const float3 x = mb_xyz(p), v0 = mb_xyz(vv);
    float3 g = mb_v3(A, A_GRAV);
    g.x *= smooth(3.f, 0.5f, mb_len(v0));                  // the lean moves the slow bed, never a marble in flight
    float3 v = (v0 + g * h) * A[A_AIR];
    float3 corr = mb_f3(0.f), dv = mb_f3(0.f);
    float imp = 0.f;
    if (cell[i] >= 0) {                                                  // neighbours (the grid was built from P0)
        int cx, cy, cz;
        mb_cell(A, x, cx, cy, cz);
        const int gx = (int)A[A_GDIM], gy = (int)A[A_GDIM + 1], gz = (int)A[A_GDIM + 2];
        const float emm = A[A_EMM];
        int seen = 0;
        for (int dz = -1; dz <= 1; ++dz)
        for (int dy = -1; dy <= 1; ++dy)
        for (int dx = -1; dx <= 1; ++dx) {
            const int X = cx + dx, Y = cy + dy, Z = cz + dz;
            if (X < 0 || Y < 0 || Z < 0 || X >= gx || Y >= gy || Z >= gz) continue;
            const int c = (Z * gy + Y) * gx + X;
            const int s0 = start[c], cnt = count[c];
            if (cnt <= 0 || s0 < 0 || s0 + cnt > MB_N) continue;
            for (int k = 0; k < cnt && seen < MB_NBCAP; ++k) {
                const int j = sidx[s0 + k];
                if (j == i || j < 0 || j >= MB_N) continue;
                ++seen;
                const float4 q = P0[j];
                const float rs = r + q.w;
                const float3 d = x - mb_xyz(q);
                const float d2 = mb_dot(d, d);
                if (d2 >= rs * rs) continue;
                float3 nn;
                float dist;
                if (d2 > 1e-10f) { dist = sqrtf(d2); nn = d * (1.f / dist); }
                else {                                                   // coincident: a hashed direction
                    dist = 0.f;
                    nn = mb_norm(make_float3(hash1(i, j, 1u) - 0.5f, hash1(i, j, 2u) - 0.2f, hash1(i, j, 3u) - 0.5f));
                }
                corr = corr + nn * (0.5f * (rs - dist));
                const float3 vj = mb_xyz(V0[j]);
                const float3 vr = v0 - vj;
                const float vn = mb_dot(vr, nn);
                if (vn < 0.f) {
                    const bool fast = -vn > MB_VMIN;
                    dv = dv + nn * (-vn * (fast ? 0.5f * (1.f + emm) : 1.f));
                    imp = fmaxf(imp, -vn);
                }
                dv = dv - (vr - nn * vn) * MB_MUMM;
            }
        }
    }
    const float cl = mb_len(corr), cmax = 0.3f * r;                      // split impulse: overlaps move positions only
    if (cl > cmax) corr = corr * (cmax / cl);
    v = v + dv;
    float3 xn = x + v * h + corr * 0.8f;
    float3 nsum = mb_f3(0.f);
    mb_tray(xn, v, r, A, imp, nsum);
    float3 om = make_float3(w.x, w.y, w.z);
    if (mb_dot(nsum, nsum) > 1e-6f) {                                    // rolling: spin follows the surface
        const float3 nr = mb_norm(nsum);
        const float3 ot = mb_cross(nr, v) * (1.f / r);
        om = om + (ot - om) * fminf(1.f, h * 25.f);
        if (mb_dot(v, v) < MB_SLEEPV * MB_SLEEPV) v = v * 0.9f;          // let a resting marble rest (a slope still rolls it)
    } else {
        om = om * (1.f - fminf(1.f, 0.3f * h));
    }
    const float sp = mb_len(v);
    if (sp > MB_VMAX) v = v * (MB_VMAX / sp);
    const float oml = mb_len(om);
    if (oml > 200.f) om = om * (200.f / oml);
    float4 q = Q[i];
    const float hh = 0.5f * h;
    q = make_float4(q.x + hh * (q.w * om.x + om.y * q.z - om.z * q.y),
                    q.y + hh * (q.w * om.y + om.z * q.x - om.x * q.z),
                    q.z + hh * (q.w * om.z + om.x * q.y - om.y * q.x),
                    q.w - hh * (om.x * q.x + om.y * q.y + om.z * q.z));
    const float ql = q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w;
    if (ql > 1e-8f && ql < 1e8f) {
        const float iq = rsqrtf(ql);
        q = make_float4(q.x * iq, q.y * iq, q.z * iq, q.w * iq);
    } else {
        q = make_float4(0.f, 0.f, 0.f, 1.f);
    }
    // a click: the glass glints with the impact, then eases out
    const float spark = fmaxf((mb_ok(vv.w) ? vv.w : 0.f) * __expf(-h * 4.5f),
                              fminf(fmaxf((imp - 2.f) * (1.f / 22.f), 0.f), 1.5f));
    P1[i] = make_float4(xn.x, xn.y, xn.z, r);
    V1[i] = make_float4(v.x, v.y, v.z, spark);
    W[i] = make_float4(om.x, om.y, om.z, fminf(age, 1e5f));
    Q[i] = q;
}

// ------------------------------------------------------------------------------------------- per-marble light
extern "C" __global__ void mb_emit(const float4* __restrict__ P, const float4* __restrict__ V,
                                   const float4* __restrict__ W, const float4* __restrict__ M,
                                   const float* __restrict__ notes, float4* __restrict__ E,
                                   float* __restrict__ KL, const float* __restrict__ A) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= MB_N) return;
    const float4 p = P[i];
    if (!(p.w > 0.f)) { E[i] = make_float4(0.f, 0.f, 0.f, 0.f); return; }
    const float4 m = M[i];
    const int pitch = min(max((int)(m.x + 0.5f), 0), 127);
    const float lev = fminf(fmaxf(notes[pitch * 4 + 0], 0.f), 1.f);
    const float aon = notes[pitch * 4 + 2];
    const float own = (A[A_OWN + pitch] == (float)i) ? 1.f : 0.f;
    const float sl = own * __powf(lev + 1e-6f, 0.6f) * smooth(0.f, 0.07f, aon);
    const float age = W[i].w;
    const float fresh = __expf(-fminf(age, 60.f) * 0.12f);
    const float ph = A[A_TIME] * (0.5f + 0.6f * hash1(i, 3u)) + hash1(i, 5u);
    const float tw = 0.5f + 0.5f * __sinf(6.2831853f * (ph - floorf(ph)));
    const float spk = A[A_SPARK] * (0.35f + 0.65f * tw);
    const float vw = V[i].w;
    float k = A[A_GLOW] * fmaxf(m.w, 0.f) * (0.12f + 0.40f * fresh + 1.1f * sl + 0.9f * (mb_ok(vw) ? vw : 0.f) + spk)
              * (1.f + 0.6f * A[A_RES]) * (0.75f + 0.5f * A[A_ENERGY]);
    // blooms in; a drained marble flares as it drops through the grate, then fades as it shrinks away
    k *= smooth(0.f, MB_BLOOMS, age) * (1.f + 2.2f * smooth(-0.4f, -1.4f, p.y)) * smooth(MB_KILLY, -1.6f, p.y);
    const float3 c = mb_note_col(A, m.x, 0.05f * (hash1(i, (unsigned int)pitch, 9u) - 0.5f));
    E[i] = make_float4(c.x * k, c.y * k, c.z * k, k);
    if (p.y > -0.6f && fabsf(p.x) < T_XH && k > 1e-3f) {                // the light each key's marbles throw
        const int key = min(max((int)floorf(p.x + T_XH), 0), 87);
        atomicAdd(KL + 4 * key, c.x * k);
        atomicAdd(KL + 4 * key + 1, c.y * k);
        atomicAdd(KL + 4 * key + 2, c.z * k);
    }
}

// ------------------------------------------------------------------------------------------- the camera
__device__ __forceinline__ float3 mb_ray(const float* __restrict__ A, float px, float py) {
    const float3 d = mb_v3(A, A_FW) * A[A_FOC] + mb_v3(A, A_RT) * (px + 0.5f - A[A_CXG])
                   + mb_v3(A, A_UP) * (A[A_CY] - py - 0.5f);
    return mb_norm(d);
}
// tangent extents of a sphere seen along one camera axis: (a, z) its centre, r its radius -> tan of both edges
__device__ __forceinline__ void mb_extent(float a, float z, float r, float& lo, float& hi) {
    const float L = sqrtf(a * a + z * z);
    const float th = atan2f(a, z), dl = asinf(fminf(r / fmaxf(L, 1e-4f), 0.999f));
    lo = tanf(fmaxf(th - dl, -1.45f));
    hi = tanf(fminf(th + dl, 1.45f));
}

// each marble writes (depth, id) into the pixels it covers: the nearest wins
extern "C" __global__ void mb_raster(const float4* __restrict__ P, unsigned long long* __restrict__ vis,
                                     const float* __restrict__ A) {
    const int i = blockIdx.x;
    if (i >= MB_N) return;
    const float4 p = P[i];
    if (!(p.w > 0.f) || !mb_ok(p.x) || !mb_ok(p.y) || !mb_ok(p.z)) return;
    const int Wd = (int)A[A_WPX], Hd = (int)A[A_HPX];
    const float3 E = mb_v3(A, A_EYE);
    const float3 rel = mb_xyz(p) - E;
    const float z = mb_dot(rel, mb_v3(A, A_FW));
    const float r = p.w;
    if (z < r + 0.3f) return;
    const float xr = mb_dot(rel, mb_v3(A, A_RT)), yr = mb_dot(rel, mb_v3(A, A_UP));
    const float f = A[A_FOC];
    float x0, x1, y0, y1;
    mb_extent(xr, z, r, x0, x1);
    mb_extent(yr, z, r, y0, y1);
    const float px0 = A[A_CXG] + f * x0, px1 = A[A_CXG] + f * x1;
    const float py0 = A[A_CY] - f * y1, py1 = A[A_CY] - f * y0;
    if (!(px1 >= -1.f && px0 <= Wd + 1.f && py1 >= -1.f && py0 <= Hd + 1.f)) return;
    const int ix0 = max((int)floorf(fmaxf(px0, -2.f)) - 1, 0), ix1 = min((int)ceilf(fminf(px1, Wd + 2.f)) + 1, Wd - 1);
    const int iy0 = max((int)floorf(fmaxf(py0, -2.f)) - 1, 0), iy1 = min((int)ceilf(fminf(py1, Hd + 2.f)) + 1, Hd - 1);
    if (ix0 > ix1 || iy0 > iy1) return;
    const int bw = min(ix1 - ix0 + 1, 320), bh = min(iy1 - iy0 + 1, 320);
    const int nb = bw * bh;
    const float c2 = mb_dot(rel, rel) - r * r;
    for (int k = threadIdx.x; k < nb; k += blockDim.x) {
        const int px = ix0 + k % bw, py = iy0 + k / bw;
        const float3 d = mb_ray(A, (float)px, (float)py);
        const float b = mb_dot(rel, d);
        const float disc = b * b - c2;
        if (disc < 0.f) continue;
        const float t = b - sqrtf(disc);
        if (!(t > 0.05f)) continue;
        const unsigned long long key = ((unsigned long long)__float_as_uint(t) << 32) | (unsigned long long)i;
        atomicMin(&vis[py * Wd + px], key);
    }
}

// ------------------------------------------------------------------------------------------- bloom
extern "C" __global__ void mb_glow(const float4* __restrict__ P, const float4* __restrict__ E, float* __restrict__ G,
                                   const float* __restrict__ A) {
    const int i = blockIdx.x;
    if (i >= MB_N) return;
    const float4 p = P[i], e = E[i];
    if (!(p.w > 0.f) || !(e.w > 0.003f) || !mb_ok(p.x) || !mb_ok(p.y) || !mb_ok(p.z) || !mb_ok(e.w)) return;
    const float3 rel = mb_xyz(p) - mb_v3(A, A_EYE);
    const float z = mb_dot(rel, mb_v3(A, A_FW));
    if (z < p.w + 0.3f) return;
    const int gw = (int)A[A_GWPX], gh = (int)A[A_GHPX];
    const float sc = (float)gw / A[A_WPX];
    const float iz = A[A_FOC] / z;
    const float gx = (A[A_CXG] + mb_dot(rel, mb_v3(A, A_RT)) * iz) * sc - 0.5f;
    const float gy = (A[A_CY] - mb_dot(rel, mb_v3(A, A_UP)) * iz) * sc - 0.5f;
    const float rq = p.w * iz * sc;
    const float sig = fminf(fmaxf(0.7f, 0.9f * rq), 5.5f);
    const int R = min((int)ceilf(2.5f * sig), 14);
    const int cx = (int)floorf(gx + 0.5f), cy = (int)floorf(gy + 0.5f);
    if (cx + R < 0 || cy + R < 0 || cx - R >= gw || cy - R >= gh) return;
    // the glowing marbles bloom; a bed of cooled embers does not haze the picture
    const float amp = 0.18f * (fminf(rq * rq, 36.f) + 0.5f) / (sig * sig) * smooth(0.08f, 0.55f, e.w);
    const float is2 = 1.f / (2.f * sig * sig);
    const int D = 2 * R + 1, nb = D * D;
    for (int k = threadIdx.x; k < nb; k += blockDim.x) {
        const int x = cx - R + k % D, y = cy - R + k / D;
        if (x < 0 || y < 0 || x >= gw || y >= gh) continue;
        const float dx = x - gx, dy = y - gy;
        const float wgt = amp * __expf(-(dx * dx + dy * dy) * is2);
        if (wgt < 1e-4f) continue;
        float* g = G + 4 * (y * gw + x);
        atomicAdd(g, e.x * wgt);
        atomicAdd(g + 1, e.y * wgt);
        atomicAdd(g + 2, e.z * wgt);
    }
}

extern "C" __global__ void mb_blur(const float* __restrict__ src, float* __restrict__ dst, const int w, const int h,
                                   const int sx, const int sy) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    float r = 0.f, g = 0.f, b = 0.f, a = 0.f, ws = 0.f;
    #pragma unroll
    for (int k = -6; k <= 6; ++k) {
        const int xx = min(max(x + k * sx, 0), w - 1), yy = min(max(y + k * sy, 0), h - 1);
        const float wt = __expf(-(float)(k * k) * (1.f / 12.5f));
        const float* s = src + 4 * (yy * w + xx);
        r += s[0] * wt; g += s[1] * wt; b += s[2] * wt; a += s[3] * wt; ws += wt;
    }
    float* d = dst + 4 * (y * w + x);
    const float iw = 1.f / ws;
    d[0] = r * iw; d[1] = g * iw; d[2] = b * iw; d[3] = a * iw;
}

// ------------------------------------------------------------------------------------------- shading
// the dark studio: a long softbox overhead (along the keyboard), a small warm panel, a cool rim band behind
__device__ float3 mb_env(float3 r, const float* __restrict__ A) {
    const float3 bg = mb_v3(A, A_BG);
    float3 c = bg * (0.5f + 0.8f * fmaxf(r.y, 0.f));
    if (r.y > 0.08f) {
        const float iy = 1.f / r.y;
        const float u = r.x * iy, w = r.z * iy - 0.18f;
        const float b = smooth(1.5f, 1.2f, fabsf(u)) * smooth(0.36f, 0.26f, fabsf(w));
        c = c + mb_v3(A, A_LCOL) * (2.4f * b);
        const float b2 = smooth(0.32f, 0.22f, fabsf(u - 0.9f)) * smooth(0.25f, 0.17f, fabsf(w + 0.7f));
        c = c + make_float3(1.1f, 0.78f, 0.48f) * b2;
    }
    const float band = smooth(0.25f, 0.05f, fabsf(r.y - 0.12f)) * smooth(0.f, -0.6f, r.z);
    const float lo = smooth(0.f, -0.5f, r.y);                            // the warm floor below
    return c + make_float3(0.16f, 0.17f, 0.22f) * band + make_float3(0.07f, 0.05f, 0.035f) * lo;
}

// a key's albedo and its light (the note sounding, the idle glow), by the floor point's x
__device__ __forceinline__ void mb_key(float px, const float* __restrict__ A, const float* __restrict__ notes,
                                       float3& alb, float3& em, bool& black) {
    const float u = px + T_XH;
    const int k = min(max((int)floorf(u), 0), 87);
    const int pitch = 21 + k, pc = pitch % 12;
    black = (pc == 1 || pc == 3 || pc == 6 || pc == 8 || pc == 10);
    const float fx = u - floorf(u);
    const float inset = 0.6f + 0.4f * smooth(0.f, 0.14f, fx) * smooth(1.f, 0.86f, fx);
    alb = (black ? make_float3(0.020f, 0.020f, 0.024f) : make_float3(0.22f, 0.20f, 0.165f)) * inset;
    const float lev = fminf(fmaxf(notes[pitch * 4 + 0], 0.f), 1.f);
    float kl = __powf(lev + 1e-6f, 0.7f) * smooth(0.f, 0.07f, notes[pitch * 4 + 2]) + A[A_AMB + pitch];
    kl *= A[A_REGW + pitch] * A[A_KEYGLOW] * (black ? 0.75f : 1.f);
    em = mb_note_col(A, (float)pitch, 0.f) * (kl * inset);
}

// the floor as seen through a marble (no neighbours: cheap)
__device__ float3 mb_floor_seen(float3 fp, const float* __restrict__ A, const float* __restrict__ notes) {
    float3 alb, em;
    bool black;
    mb_key(fp.x, A, notes, alb, em, black);
    const float front = 0.55f + 0.45f * smooth(-T_ZH, T_ZH, fp.z);
    return alb * (0.12f + 1.1f * A[A_LIGHT + 1]) + em * front;
}

// what a marble shows behind it: the exit ray onto the floor (or the studio)
__device__ float3 mb_behind(float3 xp, float3 t2, float3 C, float r, const float* __restrict__ A,
                            const float* __restrict__ notes) {
    if (t2.y < -0.02f) {
        const float s = fmaxf(xp.y, 0.f) / -t2.y;
        const float3 fp = xp + t2 * s;
        if (fabsf(fp.x) < T_XH && fp.z > A[A_ZG] && fp.z < T_ZH) {
            const float dx = fp.x - C.x, dz = fp.z - C.z;
            const float sh = 0.25f + 0.75f * smooth(r * 0.2f, r * 1.1f, sqrtf(dx * dx + dz * dz));
            return mb_floor_seen(fp, A, notes) * sh;
        }
        return mb_v3(A, A_BG) * 0.8f;
    }
    return mb_env(t2, A) * 0.6f;
}

__device__ float3 mb_marble(int id, float3 o, float3 d, float tm, float4 p, float4 q, float4 m, float4 e, float vspk,
                            const float* __restrict__ A, const float* __restrict__ notes) {
    const float r = p.w;
    const float3 C = mb_xyz(p);
    const float3 hp = o + d * tm;
    const float3 n = mb_norm(hp - C);
    const float cosi = fminf(fmaxf(-mb_dot(d, n), 0.f), 1.f);
    const float om = 1.f - cosi;
    const float fres = 0.04f + 0.96f * om * om * om * om * om;
    const float tph = A[A_TIME] * 0.9f + hash1(id, 21u);
    const float tw = 0.5f + 0.5f * __sinf(6.2831853f * (tph - floorf(tph)));
    const float spk = (mb_ok(vspk) ? vspk : 0.f) + A[A_SPARK] * (0.3f + 0.7f * tw);
    const float3 rd = d + n * (2.f * cosi);
    const float3 refl = mb_env(rd, A) * (1.f + 2.5f * spk);
    const float eta = 1.f / 1.5f;                                        // into the glass ...
    const float k1 = fmaxf(1.f - eta * eta * (1.f - cosi * cosi), 0.f);
    const float3 t1 = mb_norm(d * eta + n * (eta * cosi - sqrtf(k1)));
    const float cost = fmaxf(-mb_dot(t1, n), 0.f);
    const float chord = 2.f * r * cost;
    const float3 xp = hp + t1 * chord;
    const float3 n2 = mb_norm(xp - C);
    const float c2 = fminf(fmaxf(mb_dot(t1, n2), 0.f), 1.f);            // ... and out again
    const float k2 = 1.f - 2.25f * (1.f - c2 * c2);
    const float3 t2 = k2 > 0.f ? mb_norm(t1 * 1.5f - n2 * (1.5f * c2 - sqrtf(k2))) : mb_norm(t1 - n2 * (2.f * c2));
    const float3 behind = mb_behind(xp, t2, C, r, A, notes);
    const float em = fmaxf(e.w, 0.f);
    const float3 nc = em > 1e-5f ? make_float3(e.x, e.y, e.z) * (1.f / em) : mb_note_col(A, m.x, 0.f);
    const float opal = A[A_OPAL];
    // the cat's-eye: two twisted vanes in the marble's own frame (so they turn as it rolls)
    const float4 qc = make_float4(-q.x, -q.y, -q.z, q.w);
    float vane = 0.f;
    #pragma unroll
    for (int s = 0; s < 3; ++s) {
        const float fs = 0.25f + 0.25f * s;
        const float3 lp = mb_qrot(qc, (hp + t1 * (chord * fs) - C) * (1.f / r));
        float sn, cs;
        __sincosf(2.4f * lp.z, &sn, &cs);
        const float d1 = lp.x * cs + lp.y * sn, d2 = lp.y * cs - lp.x * sn;
        const float rr = fmaxf(1.f - mb_dot(lp, lp), 0.f);
        vane += (__expf(-d1 * d1 * 70.f) + 0.45f * __expf(-d2 * d2 * 140.f)) * rr;
    }
    vane *= 1.f / 3.f;
    const float thick = chord / (2.f * r);
    const float mt = 0.3f + 0.45f * opal;
    const float3 tint = mb_f3(1.f - mt) + nc * mt;
    const float3 trans = behind * tint * (__expf(-chord * 0.35f) * (1.f - 0.7f * opal));
    const float3 core = nc * (em * (2.2f * vane + 0.22f * thick + 0.9f * opal * thick));
    // the open grate lights the marbles near it from below
    const float gl = A[A_OPEN] * smooth(-T_ZH + 3.f, -T_ZH + 0.3f, C.z) * (0.3f + 0.7f * fmaxf(-n.y, 0.f));
    return refl * fres + (trans + core) * (1.f - fres) + mb_v3(A, A_GAPCOL) * (0.6f * gl);
}

// ray against the tray (its own frame): the floor plate, lip, back, ends (boxes) and the comb of groove walls
__device__ __forceinline__ bool mb_rbox(float3 o, float3 id, float3 lo, float3 hi, float& t, float3& n) {
    const float tx0 = (lo.x - o.x) * id.x, tx1 = (hi.x - o.x) * id.x;
    const float ty0 = (lo.y - o.y) * id.y, ty1 = (hi.y - o.y) * id.y;
    const float tz0 = (lo.z - o.z) * id.z, tz1 = (hi.z - o.z) * id.z;
    const float ax = fminf(tx0, tx1), ay = fminf(ty0, ty1), az = fminf(tz0, tz1);
    const float tn = fmaxf(fmaxf(ax, ay), az);
    const float tf = fminf(fminf(fmaxf(tx0, tx1), fmaxf(ty0, ty1)), fmaxf(tz0, tz1));
    if (!(tf >= tn) || !(tn > 1e-3f) || !(tn < t)) return false;
    t = tn;
    if (tn == ax) n = make_float3(id.x > 0.f ? -1.f : 1.f, 0.f, 0.f);
    else if (tn == ay) n = make_float3(0.f, id.y > 0.f ? -1.f : 1.f, 0.f);
    else n = make_float3(0.f, 0.f, id.z > 0.f ? -1.f : 1.f);
    return true;
}

__device__ void mb_trace_tray(float3 o, float3 d, const float* __restrict__ A, float& t, float3& n, int& mat) {
    t = 1e30f;
    mat = 0;
    n = make_float3(0.f, 1.f, 0.f);
    const float3 dd = make_float3(fabsf(d.x) < 1e-6f ? copysignf(1e-6f, d.x) : d.x,
                                  fabsf(d.y) < 1e-6f ? copysignf(1e-6f, d.y) : d.y,
                                  fabsf(d.z) < 1e-6f ? copysignf(1e-6f, d.z) : d.z);
    const float3 id = make_float3(1.f / dd.x, 1.f / dd.y, 1.f / dd.z);
    const float zg = A[A_ZG];
    const bool plate = zg < T_ZH - 1e-3f;
    float3 nn;
    if (plate && mb_rbox(o, id, make_float3(-T_XH, -T_PT, zg), make_float3(T_XH, 0.f, T_ZH), t, nn)) {
        n = nn; mat = nn.y > 0.5f ? 1 : 3;
    }
    if (mb_rbox(o, id, make_float3(-T_XH, -T_PT, T_ZH), make_float3(T_XH, T_LH, T_ZH + T_LW), t, nn)) { n = nn; mat = 3; }
    if (mb_rbox(o, id, make_float3(-T_XH - T_EW, -T_PT, -T_ZH - T_BW), make_float3(T_XH + T_EW, T_BH, -T_ZH), t, nn)) {
        n = nn; mat = 3;
    }
    if (mb_rbox(o, id, make_float3(-T_XH - T_EW, -T_PT, -T_ZH - T_BW), make_float3(-T_XH, T_EH, T_ZH + T_LW), t, nn)) {
        n = nn; mat = 3;
    }
    if (mb_rbox(o, id, make_float3(T_XH, -T_PT, -T_ZH - T_BW), make_float3(T_XH + T_EW, T_EH, T_ZH + T_LW), t, nn)) {
        n = nn; mat = 3;
    }
    // the groove walls: enter the slab 0 < y < HW from above, then the first wall face crossed (closed form)
    if (plate && dd.y < 0.f && o.y > T_HW) {
        const float ta = (T_HW - o.y) * id.y;
        if (ta > 0.f && ta < t) {
            const float3 pa = o + dd * ta;
            if (pa.x > -T_XH && pa.x < T_XH && pa.z > zg && pa.z < T_ZH) {
                const float fx = pa.x - floorf(pa.x);
                const int mw = (int)floorf(pa.x + 0.5f);
                if ((fx < T_TW || fx > 1.f - T_TW) && mw >= -43 && mw <= 43) {
                    t = ta; n = make_float3(0.f, 1.f, 0.f); mat = 2;
                } else {
                    const int m = (int)floorf(pa.x);
                    const int wall = dd.x > 0.f ? m + 1 : m;
                    const float xf = dd.x > 0.f ? (float)wall - T_TW : (float)wall + T_TW;
                    if (wall >= -43 && wall <= 43) {
                        const float tw = ta + (xf - pa.x) * id.x;
                        if (tw > ta && tw < t) {
                            const float3 pw = o + dd * tw;
                            if (pw.y > 0.f && pw.z > zg && pw.z < T_ZH) {
                                t = tw; n = make_float3(dd.x > 0.f ? -1.f : 1.f, 0.f, 0.f); mat = 2;
                            }
                        }
                    }
                }
            }
        }
    }
}

// marbles near a tray point: ambient occlusion, a soft shadow from the overhead light, their coloured light
__device__ void mb_near(float3 p, float3 n, const float* __restrict__ A, const float4* __restrict__ P,
                        const float4* __restrict__ E, const int* __restrict__ start, const int* __restrict__ count,
                        const int* __restrict__ sidx, float& ao, float& sh, float3& pool) {
    ao = 1.f; sh = 1.f; pool = mb_f3(0.f);
    const float ics = A[A_ICS];
    const int gx = (int)A[A_GDIM], gy = (int)A[A_GDIM + 1], gz = (int)A[A_GDIM + 2];
    const float fx = (p.x - A[A_GORG]) * ics, fy = (p.y - A[A_GORG + 1]) * ics, fz = (p.z - A[A_GORG + 2]) * ics;
    if (!(fx > -2.f && fy > -2.f && fz > -2.f && fx < gx + 1.f && fy < gy + 1.f && fz < gz + 1.f)) return;
    const int cx = (int)floorf(fx), cy = (int)floorf(fy), cz = (int)floorf(fz);
    const float3 L = mb_v3(A, A_LIGHT);
    float occ = 0.f;
    int seen = 0;
    for (int dz = -1; dz <= 1; ++dz)
    for (int dy = 0; dy <= 2; ++dy)
    for (int dx = -1; dx <= 1; ++dx) {
        const int X = cx + dx, Y = cy + dy, Z = cz + dz;
        if (X < 0 || Y < 0 || Z < 0 || X >= gx || Y >= gy || Z >= gz) continue;
        const int c = (Z * gy + Y) * gx + X;
        const int s0 = start[c], cnt = count[c];
        if (cnt <= 0 || s0 < 0 || s0 + cnt > MB_N) continue;
        for (int k = 0; k < cnt && seen < MB_NEARCAP; ++k) {
            const int j = sidx[s0 + k];
            if (j < 0 || j >= MB_N) continue;
            ++seen;
            const float4 q = P[j];
            if (!(q.w > 0.f)) continue;
            const float3 wv = mb_xyz(q) - p;
            const float d2 = mb_dot(wv, wv) + 1e-4f;
            const float cw = mb_dot(n, wv) * rsqrtf(d2);
            const float r2 = q.w * q.w;
            if (cw > 0.f) {
                occ += r2 / d2 * cw;
                const float4 e = E[j];
                pool = pool + make_float3(e.x, e.y, e.z) * (r2 * cw / (d2 + 0.3f * r2));
            }
            const float tl = mb_dot(wv, L);
            if (tl > 0.f) {
                const float3 pv = wv - L * tl;
                sh = fminf(sh, smooth(q.w * 0.5f, q.w * 1.05f + 0.22f * tl, mb_len(pv)));
            }
        }
    }
    ao = 1.f - fminf(occ * 0.6f, 0.85f);
}

__device__ float3 mb_tray_col(float3 p, float3 n, int mat, float3 d, const float* __restrict__ A,
                              const float* __restrict__ notes, const float4* __restrict__ P,
                              const float4* __restrict__ E, const int* __restrict__ start,
                              const int* __restrict__ count, const int* __restrict__ sidx) {
    float3 alb, em = mb_f3(0.f);
    float gloss, poolk;
    if (mat == 1) {
        bool black;
        mb_key(p.x, A, notes, alb, em, black);
        em = em * (0.55f + 0.45f * smooth(-T_ZH, T_ZH, p.z));
        gloss = black ? 0.8f : 0.25f;
        poolk = black ? 0.25f : 0.9f;
    } else {
        alb = mb_f3(mat == 2 ? 0.012f : 0.010f);
        gloss = 1.f;
        poolk = 0.3f;
    }
    float ao, sh;
    float3 pool;
    mb_near(p, n, A, P, E, start, count, sidx, ao, sh, pool);
    pool = pool * (1.f / (1.f + 0.7f * fmaxf(fmaxf(pool.x, pool.y), pool.z)));   // a dense bed never floods the keys
    const float3 L = mb_v3(A, A_LIGHT);
    const float ndl = fmaxf(mb_dot(n, L), 0.f);
    float3 c = alb * mb_v3(A, A_LCOL) * (0.12f * ao + 1.25f * ndl * sh * sqrtf(ao)) + em + pool * (0.55f * poolk);
    const float cosv = fmaxf(-mb_dot(d, n), 0.f);
    const float om = 1.f - cosv;
    const float fr = 0.04f + 0.96f * om * om * om * om * om;
    const float3 rd = d - n * (2.f * mb_dot(d, n));
    c = c + mb_env(rd, A) * (fr * gloss * ao) + alb * (0.6f * A[A_RES]) * mb_v3(A, A_BG) * 8.f;
    // the open grate: light from below on the back wall's face and the plate's edge
    const float op = A[A_OPEN];
    if (op > 0.005f) {
        const float zg = A[A_ZG];
        float gk = 0.f;
        if (mat == 3 && n.z > 0.5f && p.z < -T_ZH + 0.05f)
            gk = __expf(-fmaxf(p.y, 0.f) * 1.3f) * smooth(T_XH + 0.5f, T_XH - 1.f, fabsf(p.x));
        else if (mat <= 2)
            gk = 0.5f * __expf(-fmaxf(p.z - zg, 0.f) * 1.6f);
        c = c + mb_v3(A, A_GAPCOL) * (op * gk * 0.5f);
    }
    return c;
}

// the studio floor, a soft warm matte sweep under the floating tray (the tray's frame): the overhead light's pool
// around the shot, the tray's soft shadow and the dark straight under it, the marbles' coloured light spilling over
// the lip (per key), and the drain's light falling through the open grate
__device__ float3 mb_floor_col(float3 p, const float* __restrict__ A, const float* __restrict__ KL) {
    const float3 L = mb_v3(A, A_LIGHT);
    const float ndl = fmaxf(mb_dot(mb_v3(A, A_FLN), L), 0.f);
    const float s = (-0.3f - p.y) / fmaxf(L.y, 0.2f);                   // along the light up to the tray
    const float qx = p.x + L.x * s, qz = p.z + L.z * s;
    const float pen = 0.5f + 0.16f * s;
    const float shx = smooth(T_XH + T_EW + pen, T_XH + T_EW - pen, fabsf(qx));
    const float shz = smooth(T_ZH + T_LW + pen, T_ZH + T_LW - pen, qz) * smooth(-T_ZH - T_BW - pen, -T_ZH - T_BW + pen, qz);
    const float shadow = 1.f - 0.8f * shx * shz;
    const float aox = smooth(T_XH + 4.f, T_XH - 1.f, fabsf(p.x));
    const float aoz = smooth(T_ZH + 4.f, T_ZH - 1.f, fabsf(p.z));
    const float ao = 1.f - 0.55f * aox * aoz;
    const float3 sp = mb_v3(A, A_SPOT);
    const float dx = p.x - sp.x, dz = p.z - sp.z;
    const float pool = 0.2f + 0.8f * __expf(-(dx * dx + dz * dz) * (1.f / 700.f));
    const float lift = 1.f + 0.25f * A[A_RES];
    float3 c = make_float3(0.125f, 0.098f, 0.078f) * mb_v3(A, A_LCOL) * ((0.10f + 0.9f * ndl * shadow) * ao * pool * lift);
    // the marbles' light thrown on the floor around the tray (over the lip in front, a wide soft glow behind), by
    // the keys above it: the studio takes the music's colours
    const float zf = p.z - T_ZH - T_LW, zb = -T_ZH - T_BW - p.z;
    const float band = smooth(-1.5f, 1.5f, zf) * __expf(-fmaxf(zf, 0.f) * 0.22f)
                     + 0.55f * smooth(-1.f, 3.f, zb) * __expf(-fmaxf(zb, 0.f) * 0.05f);
    const float wide = 1.f + fmaxf(zb, 0.f) * 0.12f;                   // farther back, a wider spread along the keys
    if (band > 0.01f && fabsf(p.x) < T_XH + 6.f * wide) {
        const float kx = p.x + T_XH;
        const float step = fmaxf(1.f, floorf(wide));
        const float iw = 1.f / (10.f * wide * wide);
        float3 sum = mb_f3(0.f);
        float ws = 0.f;
        #pragma unroll 1
        for (int j = -5; j <= 5; ++j) {
            const int k = (int)floorf(kx + j * step);
            const float u = kx - ((float)k + 0.5f);
            const float w = __expf(-u * u * iw);
            ws += w;
            if (k < 0 || k > 87) continue;
            sum = sum + make_float3(KL[4 * k], KL[4 * k + 1], KL[4 * k + 2]) * w;
        }
        c = c + sum * (A[A_KLK] * band * 3.5f / fmaxf(ws, 1e-3f));
    }
    const float op = A[A_OPEN];
    if (op > 0.005f) {                                                  // the drain: light through the grate
        const float zc = 0.5f * (-T_ZH + A[A_ZG]);
        const float gz = p.z - zc;
        c = c + mb_v3(A, A_GAPCOL) * (op * 0.35f * __expf(-gz * gz * 0.25f) * smooth(T_XH + 2.f, T_XH - 2.f, fabsf(p.x)));
    }
    return c;
}

// slow out-of-focus lights far behind the tray, in screen space (shared across the room's screens)
__device__ float3 mb_bokeh(float gx, float gy, const float* __restrict__ A) {
    float3 c = mb_f3(0.f);
    const float H = A[A_HPX];
    const float u = gx / H, v = gy / H;
    const float span = A[A_TWID] / H + 0.4f;
    const float t = A[A_TIME];
    const unsigned int sd = (unsigned int)A[A_SEED];
    const float lift = 0.075f * (0.6f + 0.8f * A[A_ENERGY]) * (1.f + 0.8f * A[A_RES]);
    #pragma unroll 1
    for (int k = 0; k < 11; ++k) {
        const float h1 = hash1(k, 71u, sd), h2 = hash1(k, 72u, sd), h3 = hash1(k, 73u, sd), h4 = hash1(k, 74u, sd);
        const float bx = -0.2f + fmodf(h1 * span + t * (0.004f + 0.008f * h3), span);
        const float bph = 0.013f * t + h4;
        const float by = 0.02f + 0.28f * h2 + 0.03f * __sinf(6.2831853f * (bph - floorf(bph)));
        const float rad = 0.035f + 0.07f * h4;
        const float dx = u - bx, dy = v - by;
        const float dist = sqrtf(dx * dx + dy * dy);
        const float disc = smooth(rad, rad * 0.7f, dist) * (0.65f + 0.35f * smooth(rad * 0.4f, rad, dist));
        if (disc > 0.f) c = c + mb_note_col(A, 21.f + 87.f * h3, 0.f) * (disc * lift);
    }
    return c;
}

// the dark warm studio above the floor's horizon (brighter low, near black above)
__device__ float3 mb_sky(float3 d, float gx, float gy, const float* __restrict__ A) {
    return mb_v3(A, A_BG) * (0.3f + 1.5f * smooth(0.55f, -0.08f, d.y)) + mb_bokeh(gx, gy, A);
}

__device__ __forceinline__ float4 mb_gsample(const float* __restrict__ G, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const float4 a = reinterpret_cast<const float4*>(G)[ya * w + xa], b = reinterpret_cast<const float4*>(G)[ya * w + xb];
    const float4 c = reinterpret_cast<const float4*>(G)[yb * w + xa], e = reinterpret_cast<const float4*>(G)[yb * w + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * e.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * e.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * e.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * e.w));
}

// one pixel in linear light, with its signed circle of confusion (+ far, - near) for the depth of field
extern "C" __global__ void mb_shade(float4* __restrict__ F, unsigned long long* __restrict__ vis,
                                    const float4* __restrict__ P, const float4* __restrict__ V,
                                    const float4* __restrict__ Q, const float4* __restrict__ M,
                                    const float4* __restrict__ E, const float* __restrict__ notes,
                                    const float* __restrict__ KL, const int* __restrict__ start,
                                    const int* __restrict__ count, const int* __restrict__ sidx,
                                    const float* __restrict__ A) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int Wd = (int)A[A_WPX], Hd = (int)A[A_HPX];
    if (x >= Wd || y >= Hd) return;
    const int pix = y * Wd + x;
    const unsigned long long key = vis[pix];
    vis[pix] = 0xFFFFFFFFFFFFFFFFull;                                    // ready for the next frame's raster
    const float3 o = mb_v3(A, A_EYE);
    const float3 d = mb_ray(A, (float)x, (float)y);
    float tt;
    float3 tn;
    int mat;
    mb_trace_tray(o, d, A, tt, tn, mat);
    const float3 fln = mb_v3(A, A_FLN);                                 // the studio floor
    const float fdn = mb_dot(fln, d);
    if (fdn < -1e-4f) {
        const float tf = (A[A_FLY] - mb_dot(fln, o)) / fdn;
        if (tf > 0.f && tf < tt) { tt = tf; tn = fln; mat = 4; }
    }
    // the nearest marble, if it is in front of the tray and the floor
    int id = -1;
    float tm = 0.f, al = 0.f;
    float4 p = make_float4(0.f, 0.f, 0.f, 0.f);
    if (key != 0xFFFFFFFFFFFFFFFFull) {
        const unsigned int k = (unsigned int)(key & 0xFFFFFFFFull);
        if (k < MB_N) {
            p = P[k];
            if (p.w > 0.f) {
                const float3 rel = mb_xyz(p) - o;
                const float b = mb_dot(rel, d);
                const float l2 = mb_dot(rel, rel);
                const float disc = b * b - (l2 - p.w * p.w);
                if (disc > 0.f) {
                    const float t = b - sqrtf(disc);
                    if (t > 0.f && t < tt) {
                        const float perp = sqrtf(fmaxf(l2 - b * b, 0.f));
                        al = smooth(0.f, 1.25f, (p.w - perp) * A[A_FOC] / t);
                        id = (int)k;
                        tm = t;
                    }
                }
            }
        }
    }
    float3 col = mb_f3(0.f);
    if (id < 0 || al < 0.999f) {
        if (mat == 4) {
            const float3 fp = o + d * tt;
            col = mb_floor_col(fp, A, KL);
            if (fp.z < -T_ZH - T_BW)                                      // far behind the tray: the lights too
                col = col + mb_bokeh((float)x + 0.5f * A[A_TWID] - A[A_CXG], (float)y, A)
                          * smooth(1.15f, 1.9f, tt / A[A_FOCUS]);
        }
        else if (mat > 0) col = mb_tray_col(o + d * tt, tn, mat, d, A, notes, P, E, start, count, sidx);
        else col = mb_sky(d, (float)x + 0.5f * A[A_TWID] - A[A_CXG], (float)y, A);
    }
    float tdep = mat > 0 ? tt : 400.f;
    if (id >= 0) {
        const float3 mc = mb_marble(id, o, d, tm, p, Q[id], M[id], E[id], V[id].w, A, notes);
        col = col + (mc - col) * al;
        tdep = id >= 0 && al > 0.5f ? tm : tdep;
    }
    // the drain: a curtain of light rising from the open grate, in front of the back wall
    const float op = A[A_OPEN];
    if (op > 0.005f && fabsf(d.z) > 1e-4f) {
        const float zc = 0.5f * (-T_ZH + A[A_ZG]);
        const float tc = (zc - o.z) / d.z;
        if (tc > 0.f && tc < tdep) {
            const float3 pc = o + d * tc;
            const float hy = pc.y + 0.2f;
            const float sheet = (hy > 0.f ? __expf(-hy * 1.1f) : smooth(-3.5f, 0.f, hy))
                              * smooth(T_XH + 0.5f, T_XH - 1.5f, fabsf(pc.x));
            col = col + mb_v3(A, A_GAPCOL) * (op * 0.55f * sheet);
        }
    }
    if (mat > 0 || id >= 0) {                                            // the far end of the keyboard recedes
        const float fog = 1.f - __expf(-fmaxf(tdep - A[A_FOG0], 0.f) * 0.006f);
        col = col + (mb_v3(A, A_BG) * 0.6f - col) * fog;
    }
    const float coc = fminf(fmaxf(A[A_DOF] * (tdep - A[A_FOCUS]) / fmaxf(tdep, 1e-3f), -1.f), 1.f);
    F[pix] = make_float4(col.x, col.y, col.z, coc);
}

// a quarter-resolution copy for the depth of field: the colour, and how much of each block is near-field blur
extern "C" __global__ void mb_down(const float4* __restrict__ F, float* __restrict__ D, const int w, const int h,
                                   const int gw, const int gh) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= gw || y >= gh) return;
    const int sx = w / gw, sy = h / gh;
    float r = 0.f, g = 0.f, b = 0.f, nr = 0.f;
    int n = 0;
    for (int j = 0; j < sy; ++j)
        for (int i = 0; i < sx; ++i) {
            const int xx = min(x * sx + i, w - 1), yy = min(y * sy + j, h - 1);
            const float4 f = F[yy * w + xx];
            r += f.x; g += f.y; b += f.z; nr += fmaxf(-f.w, 0.f);
            ++n;
        }
    const float in = 1.f / (float)max(n, 1);
    float* d = D + 4 * (y * gw + x);
    d[0] = r * in; d[1] = g * in; d[2] = b * in; d[3] = nr * in;
}

// the depth of field (sharp to the blurred copy by the circle of confusion; the near blur spreads over its edges),
// the bloom, ACES tonemap, sRGB, dither
extern "C" __global__ void mb_comp(uchar4* __restrict__ out, const float4* __restrict__ F, const float* __restrict__ D,
                                   const float* __restrict__ G, const float* __restrict__ A) {
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    const int Wd = (int)A[A_WPX], Hd = (int)A[A_HPX];
    if (x >= Wd || y >= Hd) return;
    const int pix = y * Wd + x;
    const int gw = (int)A[A_GWPX], gh = (int)A[A_GHPX];
    const float fx = (x + 0.5f) * gw / (float)Wd, fy = (y + 0.5f) * gh / (float)Hd;
    const float4 f = F[pix];
    const float4 bl = mb_gsample(D, fx, fy, gw, gh);
    const float wf = smooth(0.f, 1.f, fminf(fmaxf(fmaxf(f.w, 0.35f * fmaxf(-f.w, 1.8f * bl.w)), 0.f), 1.f));
    float3 col = make_float3(f.x + (bl.x - f.x) * wf, f.y + (bl.y - f.y) * wf, f.z + (bl.z - f.z) * wf);
    const float4 gb = mb_gsample(G, fx, fy, gw, gh);
    col = col + make_float3(gb.x, gb.y, gb.z) * A[A_BLOOM];
    const float vu = ((float)x + 0.5f * A[A_TWID] - A[A_CXG]) / A[A_TWID] * 2.f - 1.f, vv = (y + 0.5f) / Hd * 2.f - 1.f;
    col = col * (1.f - 0.3f * fminf(0.55f * vu * vu + 0.45f * vv * vv, 1.f));          // a soft vignette
    const float ex = A[A_EXPO];
    float3 c = make_float3(fminf(fmaxf(col.x * ex, 0.f), 64.f), fminf(fmaxf(col.y * ex, 0.f), 64.f),
                           fminf(fmaxf(col.z * ex, 0.f), 64.f));
    c = make_float3((c.x * (2.51f * c.x + 0.03f)) / (c.x * (2.43f * c.x + 0.59f) + 0.14f),
                    (c.y * (2.51f * c.y + 0.03f)) / (c.y * (2.43f * c.y + 0.59f) + 0.14f),
                    (c.z * (2.51f * c.z + 0.03f)) / (c.z * (2.43f * c.z + 0.59f) + 0.14f));
    c = make_float3(__powf(clamp01(c.x) + 1e-7f, 0.4545f), __powf(clamp01(c.y) + 1e-7f, 0.4545f),
                    __powf(clamp01(c.z) + 1e-7f, 0.4545f));
    const float dth = hash1((unsigned int)pix, (unsigned int)A[A_SEED], 77u) - 0.5f;
    out[pix] = pack(make_float4(c.x * 255.f + dth, c.y * 255.f + dth, c.z * 255.f + dth, 255.f));
}
"""

_OPTS = ("--std=c++14", "--use_fast_math")


# ------------------------------------------------------------------------------------------------- host helpers
def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _ease(dt: float, tau: float) -> float:
    return 1.0 - math.exp(-dt / tau) if dt > 0.0 else 0.0


def tray_rotation(lean: float, tilt: float) -> np.ndarray:
    """The tray frame's rotation into the world (3x3): ``tilt`` (rad) lowers the back, ``lean`` the bass end."""
    c, s = math.cos(-tilt), math.sin(-tilt)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
    c, s = math.cos(lean), math.sin(lean)
    rz = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    return rz @ rx


# the points that must stay in frame for the whole keyboard: the tray's corners at the floor and at the drop height
_FIT_PTS = np.array([[sx * (XH + EW), y, sz] for sx in (-1.0, 1.0) for y in (0.0, 3.6) for sz in (-ZH - BW, ZH + LW)])
SPAN_WIDE, SPAN_CLOSE = 2.0 * (XH + EW) + 2.0, 11.0     # keys across the frame at camera 0 and at camera 1


def _fit(pts: np.ndarray, target: np.ndarray, rt, up, fw, mx: float, my: float) -> float:
    """The distance along ``-fw`` from ``target`` at which every point has |x| <= mx and |y| <= my (tangents)."""
    rel = pts - target
    x, y, z0 = np.abs(rel @ rt), np.abs(rel @ up), rel @ fw
    return float(np.max(np.maximum(x / mx, y / my) - z0))


def camera_rig(cam: float, drift: tuple[float, float, float], amp: float, follow_x: float, aspect: float,
               n_screens: int = 1):
    """Eye, target and vertical fov (deg) in the world.

    One screen: a product shot of a section of the tray around ``follow_x`` (where the music is), seen from a
    three-quarter angle on the treble side, looking down into the grooves.  ``cam`` sets how many keys the section
    spans, from the whole keyboard (0) to about 11 keys (1), exponentially; the closer, the lower and more
    three-quarter the angle and the wider the lens.  The section's box (lip to back wall, floor to drop height) is
    fitted in closed form (in camera space a point's sideways and vertical offsets do not depend on the distance);
    its sideways fit is loose when close, so the tray runs out of both sides of the frame, and the section never
    leaves the keyboard.  ``drift``: three phases (cycles) and ``amp`` their amplitude.
    ``n_screens`` > 1: one camera across the room's screens: the whole keyboard (no crop, no follow)."""
    c = min(max(cam, 0.0), 1.0)
    a, b, cc = drift
    if n_screens > 1:
        k = _smoothstep(0.0, 1.0, c)
        yaw = math.radians(6.0 + 44.0 * k) * 0.45
        pitch = math.radians(42.0 - 24.0 * k)
        fov = 32.0 + 8.0 * k
        target = np.array([0.0, 0.6, -0.3])
        pts, mx, my = _FIT_PTS, 0.94 * aspect, 0.9 / n_screens
    else:
        k = _smoothstep(0.0, 1.0, c)
        span = SPAN_WIDE * (SPAN_CLOSE / SPAN_WIDE) ** c
        yaw = math.radians(8.0 + 12.0 * k)
        pitch = math.radians(40.0 - 10.0 * k)
        fov = 30.0 + 10.0 * k
        lim = max(XH + EW - 0.5 * span, 0.0)
        fx = min(max(follow_x, -lim), lim)
        x0, x1 = max(fx - 0.5 * span, -XH - EW), min(fx + 0.5 * span, XH + EW)
        target = np.array([0.5 * (x0 + x1), 0.6 + 1.2 * k, -0.6 - 0.4 * k])
        pv = np.array([[target[0], y, z] for y in (0.0, 4.2) for z in (-ZH - BW, ZH + LW)])
        ph = np.array([[x, 0.3, z] for x in (x0, x1) for z in (-ZH, ZH)])
        pts = None
    yaw += math.radians(2.2 * amp) * math.sin(2 * math.pi * a)
    pitch += math.radians(1.2 * amp) * math.sin(2 * math.pi * b + 1.1)
    back = np.array([math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)])
    fw, rt, up = camera_basis(target + back, target)
    th = math.tan(math.radians(fov) / 2.0)
    if pts is not None:
        fit = _fit(pts, target, rt, up, fw, mx * th, my * th)
    else:                                   # one screen: the depth by the section's middle, the width by its ends
        my = 0.9 + 0.4 * _smoothstep(0.6, 1.0, c)          # close up, the lip and the drop height may crop
        fit = max(_fit(pv, target, rt, up, fw, 9.0, my * th), _fit(ph, target, rt, up, fw, aspect * th, 9.0))
    dist = max(fit, 6.0) * (1.0 + 0.035 * amp * math.sin(2 * math.pi * cc + 2.3))
    return target + dist * back, target, fov


def camera_basis(eye: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    fw = target - eye
    fw = fw / max(np.linalg.norm(fw), 1e-9)
    rt = np.cross(fw, np.array([0.0, 1.0, 0.0]))
    n = np.linalg.norm(rt)
    rt = rt / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
    up = np.cross(rt, fw)
    return fw, rt, up


def _pal_host(coeffs: np.ndarray, t: float) -> np.ndarray:
    a, b, c, d = coeffs[0:3], coeffs[3:6], coeffs[6:9], coeffs[9:12]
    return np.clip(a + b * np.cos(2 * math.pi * (c * t + d)), 0.0, 1.0)


def _segment(snd) -> tuple[int, int] | None:
    seg = getattr(snd, "segment", None) if snd is not None else None
    try:
        k, n = int(seg[0]), int(seg[1])
    except (TypeError, ValueError, IndexError):
        return None
    return (k, n) if n >= 2 and 0 <= k < n else None


# ------------------------------------------------------------------------------------------------- the scene
class NoteMarbles(Source):
    """Per frame: one arena upload, the spawn launch, 5 calls per physics substep (memset, hash, scan, scatter,
    step; 4 substeps at 60 fps), then emit, raster, glow, two blurs, shade, the depth of field's copy and four
    blurs, the composite and present."""
    name = "note_marbles"
    label = "Note marbles"
    description = ("Every piano note drops a glowing glass marble into a keyboard-shaped tray at its key: it bounces "
                   "with the velocity, rolls and settles, the tray fills from the bass and a pedal lift drains it.")
    listens = ("notes",)

    @classmethod
    def param_props(cls) -> dict:
        return {
            **standard_scene_params(palette="rainbow", render_scale=0.5, period=1.0, drift=0.01),
            "marble_size": num(0.5, 1.05, 0.95, "Marble size", basic=True),
            "bounciness": num(0.0, 1.0, 0.55, "Bounciness"),
            "glow": num(0.0, 1.5, 0.85, "Glow"),
            "camera": num(0.0, 1.0, 0.75, "Camera (whole keyboard to close-up)", basic=True),
            "drain": num(0.0, 1.0, 0.5, "Pedal drain"),
            "lean": num(0.0, 1.0, 0.35, "Lean to the bass"),
            "opal": num(0.0, 1.0, 0.3, "Opal (clear to milky)"),
            "idle": num(0.0, 1.0, 0.5, "Idle drops"),
        }

    # -- setup ---------------------------------------------------------------------------------------------------
    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self.w, self.h = internal_size(ctx, float(params["render_scale"]))
        self.gw, self.gh = max(4, self.w // 4), max(4, self.h // 4)
        self.seed = int(params["seed"]) & 0x7FFFFFFF
        self.rng = np.random.default_rng(self.seed)
        mod = raw_module(_SRC, _OPTS)
        self.k_spawn = mod.get_function("mb_spawn")
        self.k_hash = mod.get_function("mb_hash")
        self.k_scan = mod.get_function("mb_scan")
        self.k_scatter = mod.get_function("mb_scatter")
        self.k_step = mod.get_function("mb_step")
        self.k_emit = mod.get_function("mb_emit")
        self.k_raster = mod.get_function("mb_raster")
        self.k_glow = mod.get_function("mb_glow")
        self.k_blur = mod.get_function("mb_blur")
        self.k_shade = mod.get_function("mb_shade")
        self.k_down = mod.get_function("mb_down")
        self.k_comp = mod.get_function("mb_comp")
        f4 = (N_MAX, 4)
        self.P = [cp.zeros(f4, cp.float32), cp.zeros(f4, cp.float32)]
        self.V = [cp.zeros(f4, cp.float32), cp.zeros(f4, cp.float32)]
        self.Q = cp.zeros(f4, cp.float32)
        self.W = cp.zeros(f4, cp.float32)
        self.M = cp.zeros(f4, cp.float32)
        self.E = cp.zeros(f4, cp.float32)
        self.cell = cp.zeros(N_MAX, cp.int32)
        self.rank = cp.zeros(N_MAX, cp.int32)
        self.sidx = cp.zeros(N_MAX, cp.int32)
        self.count = cp.zeros(G_CELLS, cp.int32)
        self.start = cp.zeros(G_CELLS, cp.int32)
        self.vis = cp.zeros(self.w * self.h, cp.uint64)
        self.vis.data.memset_async(0xFF, self.vis.nbytes)
        self.G = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.G2 = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.D = cp.zeros((self.gh, self.gw, 4), cp.float32)          # the depth of field's blurred copy
        self.D2 = cp.zeros((self.gh, self.gw, 4), cp.float32)
        self.F = cp.zeros((self.h, self.w, 4), cp.float32)            # linear light + circle of confusion
        self.KL = cp.zeros((N_KEYS, 4), cp.float32)                   # each key's marble light (the floor's spill)
        self.buf = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.arena = cp.zeros(N_AR, cp.float32)
        self.A = np.zeros(N_AR, np.float32)
        self.SP = np.zeros((SPAWN_MAX, SPW), np.float32)
        self.silent = cp.zeros((128, 4), cp.float32)
        self.cur = 0
        self.head = 0
        self.owner = np.full(128, -1.0, np.float32)
        self.amb = np.zeros(128, np.float32)            # idle key glow (shown), eased towards amb_hit
        self.amb_hit = np.zeros(128, np.float32)
        self.regw = np.ones(128, np.float32)
        self._reg_key = None
        self._seen = None
        # the grate, the pedal, rests
        self.t = 0.0
        self.open = 0.0
        self.min_until = self.up_t0 = self.gulp_until = -1.0
        self.flash = self.gulp = 0.0
        self.pdown = False
        self.quiet = 1e9
        self.since_real = 0
        self.since_amb = 0
        self.res = 0.0
        # idle music box
        self.amb_t = 0.3
        self.amb_deg = len(LADDER) // 2
        # motion
        self.cam_ph = np.array([0.13, 0.41, 0.77]) + self.rng.random(3)
        self.rock_ph = float(self.rng.random())
        self.kick = 0.0
        self.e_s = None
        self.x_act = 0.0
        self.x_tgt = 0.0
        self.drift = 0.0
        self._prefill(float(params["marble_size"]))

    def _prefill(self, size: float) -> None:
        """A few settled marbles (an old C major ninth across the keyboard) so the tray is never bare at the start."""
        cp = self.ctx.cp
        P = np.zeros((N_MAX, 4), np.float32)
        P[:, 1] = -1000.0
        Q = np.zeros((N_MAX, 4), np.float32)
        Q[:, 3] = 1.0
        W = np.zeros((N_MAX, 4), np.float32)
        M = np.zeros((N_MAX, 4), np.float32)
        chord = [(36, 3), (43, 2), (48, 4), (52, 2), (55, 3), (59, 1), (62, 2), (64, 3), (67, 2), (72, 2), (74, 1),
                 (76, 2), (79, 1), (84, 1), (88, 1), (91, 1)]
        r = R_BASE * size
        i = 0
        for pitch, cnt in chord:
            for j in range(cnt):
                if i >= N_MAX:
                    break
                fac = 0.9 + 0.12 * float(self.rng.random())
                rr = r * fac
                P[i] = (pitch - LOW - 43.5, rr + 0.01, -ZH + rr + 0.02 + j * 2.05 * r * 1.02, rr)
                u = self.rng.random(3)
                a, b = math.sqrt(1.0 - u[0]), math.sqrt(u[0])
                Q[i] = (a * math.sin(2 * math.pi * u[1]), a * math.cos(2 * math.pi * u[1]),
                        b * math.sin(2 * math.pi * u[2]), b * math.cos(2 * math.pi * u[2]))
                W[i, 3] = 25.0 + 10.0 * float(self.rng.random())
                M[i] = (pitch, fac, 0.5, 0.8)
                i += 1
        self.P[0].set(P)
        self.P[1].set(P)
        self.Q.set(Q)
        self.W.set(W)
        self.M.set(M)
        self.head = i % N_MAX
        self.since_amb = i

    # -- listening ---------------------------------------------------------------------------------------------
    def _reg_weights(self, snd) -> np.ndarray:
        reg = getattr(snd, "register", None) if snd is not None else None
        try:
            reg = tuple(float(v) for v in reg) if reg is not None else None
        except (TypeError, ValueError):
            reg = None
        if not reg or len(reg) != 3:
            if self._reg_key is not None:
                self._reg_key = None
                self.regw.fill(1.0)
            return self.regw
        if self._reg_key != reg:
            try:
                from stream_service.ensemble import note_weights
                w = note_weights(reg, np.arange(128))
                self.regw[:] = 1.0 if w is None else np.clip(np.nan_to_num(np.asarray(w, np.float64), nan=1.0), 0, 1)
            except Exception:                         # a bad window never breaks a frame: draw everything at full
                self.regw.fill(1.0)
            self._reg_key = reg
        return self.regw

    def _listen(self, snd, f) -> tuple[list, float]:
        """This frame's onsets [(pitch, vel, age)] and the damper (0..1)."""
        spawns = []
        ons = getattr(f, "onsets", None) if f is not None else None
        if ons is not None and ons is not self._seen:
            self._seen = ons
            try:
                evs = list(ons)[:SPAWN_MAX]
            except TypeError:
                evs = []
            for ev in evs:
                try:
                    pitch, vel, age = int(ev[0]), float(ev[1]), float(ev[2])
                except (TypeError, ValueError, IndexError):
                    continue
                if not (0 <= pitch < 128) or not math.isfinite(vel):
                    continue
                age = min(max(age, 0.0), 0.25) if math.isfinite(age) else 0.0
                spawns.append((pitch, min(max(vel, 0.0), 1.0), age, False))
        damper = 0.0
        if f is not None:
            try:
                damper = float(f.pedals[0])
            except (AttributeError, TypeError, ValueError, IndexError):
                damper = 0.0
            if not math.isfinite(damper):
                damper = 0.0
        return spawns, min(max(damper, 0.0), 1.0)

    def _sounding(self, f) -> bool:
        notes = getattr(f, "notes", None) if f is not None else None
        if notes is None or getattr(notes, "shape", None) != (128, 4):
            return False
        lv = notes[LOW:HIGH + 1, 0]
        m = float(np.max(lv)) if lv.size else 0.0
        return m > 0.02 if math.isfinite(m) else False

    def _idle_drops(self, dt: float, rate: float, spawns: list) -> None:
        """The idle music box: soft marbles on a slow pentatonic random walk."""
        if rate <= 0.0 or dt <= 0.0:
            return
        self.amb_t -= dt * rate
        if self.amb_t > 0.0:
            return
        self.amb_t = float(self.rng.uniform(0.6, 1.5))
        step = int(self.rng.choice((-2, -1, -1, 1, 1, 2)))
        self.amb_deg = min(max(self.amb_deg + step, 0), len(LADDER) - 1)
        pitch = LADDER[self.amb_deg]
        if self.rng.random() < 0.15:
            pitch = max(LOW, pitch - 24)
        vel = float(0.22 + 0.4 * self.rng.random())
        spawns.append((pitch, vel, 0.0, True))
        self.amb_hit[pitch] = max(self.amb_hit[pitch], 0.8 * vel)

    def _spawn_rows(self, spawns: list, size: float) -> int:
        """Pack up to SPAWN_MAX spawns into the arena's rows; returns the count."""
        rows = self.SP
        n = 0
        dup: dict[int, int] = {}
        for pitch, vel, age, amb in spawns:
            if n >= SPAWN_MAX:
                break
            key = min(max(pitch, LOW), HIGH) - LOW
            d = dup.get(key, 0)
            if d >= 3:
                continue
            dup[key] = d + 1
            u = self.rng.random(4)
            fac = (0.78 + 0.22 * vel ** 0.8) * (0.96 + 0.06 * float(u[1]))
            r = R_BASE * size * fac
            x = key - 43.5 + (float(u[0]) - 0.5) * 0.1
            z = ZH - 0.9 - 1.6 * float(u[2])
            y = 2.6 + 1.6 * vel + d * 2.3 * r
            vy = -(2.0 + 34.0 * vel * vel)
            vz = -1.2 - 1.8 * vel
            a = min(age, 0.12)
            y = max(y + vy * a - 0.5 * GRAV * a * a, 1.2 + d * 2.3 * r)
            vy -= GRAV * a
            slot = self.head
            self.head = (self.head + 1) % N_MAX
            pk = float(key + LOW)
            dim = float(self.regw[key + LOW]) * (AMB_DIM if amb else 1.0)
            rows[n, :13] = (slot, x, y, z, 0.0, vy, vz, pk, fac, vel, dim, age, float(u[3]))
            self.owner[key + LOW] = slot
            if amb:
                self.since_amb += 1
            else:
                self.since_real += 1
                self.x_tgt += (x - self.x_tgt) * (0.04 + 0.08 * vel)
            n += 1
        return n

    def _grate(self, dt: float, damper: float, drain: float) -> None:
        """A quick pedal change is a breath of light from the grate; the longer the foot stays up, the wider the
        grate opens (fully after ~0.65 s at drain 0.5) and the more of the bed's back rows pour through, for up to
        0.35 + 2.4 x drain s.  A rest (the phrase is over) or the idle cycle opens it fully: the tray empties."""
        dur = 0.35 + 2.4 * drain
        if not self.pdown and damper > 0.5:
            self.pdown = True
        elif self.pdown and damper < 0.3:                 # the foot lifts: the dampers fall
            self.pdown = False
            self.up_t0 = self.t
            self.flash = 1.0
            # a filling tray gulps its back row at every pedal change, so it breathes with the pedal, never clogs
            self.gulp = 0.65 * _smoothstep(0.5, 1.0, self.since_real / FILL_N)
            self.gulp_until = self.t + 0.3
            self.since_real = max(self.since_real - int(round(16.0 * self.gulp)), 1)   # about a back row
        if self.quiet > REST_S and not self.pdown and self.since_real > 0:     # the phrase is over
            self.min_until = max(self.min_until, self.t + dur + 1.0)
            self.since_real = self.since_amb = 0
        if self.quiet > IDLE_AFTER and self.since_amb >= IDLE_FILL:            # the idle cycle
            self.min_until = max(self.min_until, self.t + dur + 1.0)
            self.since_real = self.since_amb = 0
        want = 0.0
        if not self.pdown and self.up_t0 >= 0.0 and self.t < self.up_t0 + dur:
            want = _smoothstep(0.3 - 0.25 * drain, 0.9 - 0.5 * drain, self.t - self.up_t0)
            if want > 0.9:
                self.since_real = self.since_amb = 0
        if self.t < self.gulp_until:
            want = max(want, self.gulp)
        if self.t < self.min_until:
            want = 1.0
        self.open += (want - self.open) * _ease(dt, 0.12 if want > self.open else 0.35)
        self.flash *= math.exp(-dt / 0.35) if dt > 0.0 else 1.0

    # -- render --------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, out) -> None:
        p = self.step_params(dt)
        dt = float(dt) if (dt == dt and 0.0 < dt < math.inf) else 0.0
        dt = min(dt, 0.25)
        e = min(max(float(p["energy"]), 0.0), 1.0)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        spd = min(max(float(p["speed"]), 0.0), 4.0)
        size = min(max(float(p["marble_size"]), 0.5), 1.05)
        self.t += dt
        self.e_s = e if self.e_s is None else self.e_s + (e - self.e_s) * _ease(dt, 1.5)
        self.kick += (pulse - self.kick) * _ease(dt, 0.05 if pulse > self.kick else 0.3)
        self.drift += float(p["drift_speed"]) * spd * dt

        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        notes = getattr(snd, "notes_gpu", None) if snd is not None else None
        if (notes is None or getattr(notes, "shape", None) != (128, 4) or notes.dtype != np.float32
                or not notes.flags.c_contiguous):
            notes = self.silent
        regw = self._reg_weights(snd)
        seg = _segment(snd)

        spawns, damper = self._listen(snd, f)
        real = bool(spawns) or self._sounding(f)
        self.quiet = 0.0 if real else self.quiet + dt
        if self.quiet > IDLE_AFTER:
            self._idle_drops(dt, float(p["idle"]) * (0.6 + 1.4 * e) * spd, spawns)
        self.amb_hit *= math.exp(-dt / 0.9) if dt > 0 else 1.0
        self.amb += (self.amb_hit - self.amb) * _ease(dt, 0.05)
        self._grate(dt, damper, min(max(float(p["drain"]), 0.0), 1.0))
        self.res += (damper - self.res) * _ease(dt, 0.25)
        n_sp = self._spawn_rows(spawns, size)
        self.x_act += (self.x_tgt - self.x_act) * _ease(dt, 3.0)

        # the tray's pose: slope + drain tilt + rocking; lean to the bass + rocking
        t_sh = None
        if seg is not None and f is not None:
            try:
                t_sh = float(f.t_ms) / 1000.0
            except (TypeError, ValueError, AttributeError):
                t_sh = None
            if t_sh is not None and not math.isfinite(t_sh):
                t_sh = None
        es = self.e_s
        if t_sh is not None:
            self.rock_ph = (t_sh * 0.05) % 1.0
        else:
            self.rock_ph = (self.rock_ph + (0.035 + 0.03 * es) * spd * dt) % 1.0
        rock = math.radians(0.5 + 1.3 * es) * math.sin(2 * math.pi * self.rock_ph)
        lean = math.radians(LEAN_DEG) * min(max(float(p["lean"]), 0.0), 1.0) + rock
        tilt = (math.radians(SLOPE_DEG) + math.radians(DRAIN_TILT_DEG) * self.open
                + math.radians(0.4 + 0.6 * es) * math.sin(2 * math.pi * (0.77 * self.rock_ph) + 1.3))
        R = tray_rotation(lean, tilt)
        RT = R.T
        g_f = RT @ np.array([0.0, -GRAV, 0.0])

        # the camera (world), then into the tray's frame
        kseg, nseg = seg if seg is not None else (0, 1)
        if t_sh is not None:
            ph = np.array([0.021, 0.017, 0.013]) * (t_sh * 0.6)
        else:
            self.cam_ph += np.array([0.021, 0.017, 0.013]) * (0.4 + 1.1 * es) * spd * dt
            self.cam_ph %= 1000.0
            ph = self.cam_ph
        eye, target, fov = camera_rig(float(p["camera"]), (float(ph[0]), float(ph[1]), float(ph[2])),
                                      1.0 + 2.2 * es, min(max(self.x_act, -XH), XH), self.w / self.h, nseg)
        fw, rt, up = camera_basis(eye, target)
        twid = float(nseg * self.w)
        foc = self.h * nseg / (2.0 * math.tan(math.radians(fov) / 2.0))

        # physics budget
        sim = min(dt * spd, 1.0 / 30.0)
        nsub = min(int(math.ceil(sim / SUB_H - 1e-6)), SUB_MAX) if sim > 0.0 else 0
        h = sim / nsub if nsub else 0.0
        cs = max(CS_MIN, 0.88 * size)
        gdim = [min(G_DIM_MAX[i], int(math.ceil(G_EXT[i] / cs))) for i in range(3)]
        nc = gdim[0] * gdim[1] * gdim[2]
        bnc = float(min(max(float(p["bounciness"]), 0.0), 1.0))

        # palette
        sess = self.session_palette()
        try:
            coeffs = np.asarray(PALETTES.get(p["palette"], PALETTES["rainbow"]) if sess is None else sess,
                                np.float64).reshape(-1)[:12]
            if coeffs.size != 12 or not np.all(np.isfinite(coeffs)):
                raise ValueError
        except (TypeError, ValueError):
            coeffs = np.asarray(PALETTES["rainbow"], np.float64).reshape(-1)
        period = float(p["palette_period"])
        tint = _pal_host(coeffs, 0.5 + self.drift) ** 2
        bg = np.array([0.030, 0.021, 0.015]) * (1.0 + 0.3 * self.res) + tint * 0.006   # warm studio, a hint of the palette
        mid = _pal_host(coeffs, (self.x_act + XH) / 87.0 * period + self.drift) ** 2
        gapcol = (np.array([1.0, 0.72, 0.42]) * 0.5 + mid / max(float(mid.max()), 0.05) * 0.5) * 1.8

        A = self.A
        O = OFF
        A[O["GRAV"]:O["GRAV"] + 3] = g_f
        A[O["H"]] = h
        A[O["CS"]], A[O["ICS"]] = cs, 1.0 / cs
        A[O["GDIM"]:O["GDIM"] + 3] = gdim
        A[O["GORG"]:O["GORG"] + 3] = G_ORG
        A[O["R0"]] = R_BASE * size
        A[O["EFLOOR"]], A[O["EMM"]] = 0.15 + 0.7 * bnc, 0.1 + 0.4 * bnc
        A[O["ZG"]] = -ZH + GATE * self.open
        A[O["HEAD"]] = self.head
        A[O["NSPAWN"]] = n_sp
        A[O["AIR"]] = math.exp(-0.08 * h)
        A[O["FRIC"]] = 0.005
        A[O["EYE"]:O["EYE"] + 3] = RT @ eye
        A[O["FW"]:O["FW"] + 3] = RT @ fw
        A[O["RT"]:O["RT"] + 3] = RT @ rt
        A[O["UP"]:O["UP"] + 3] = RT @ up
        A[O["FOC"]] = foc
        A[O["CXG"]] = 0.5 * twid - kseg * self.w
        A[O["CY"]] = 0.5 * self.h
        A[O["TWID"]] = twid
        A[O["WPX"]], A[O["HPX"]] = self.w, self.h
        A[O["GWPX"]], A[O["GHPX"]] = self.gw, self.gh
        A[O["GLOW"]] = min(max(float(p["glow"]), 0.0), 1.5)
        A[O["OPAL"]] = min(max(float(p["opal"]), 0.0), 1.0)
        A[O["ENERGY"]] = es
        A[O["SPARK"]] = self.kick
        A[O["RES"]] = self.res
        A[O["DRIFT"]] = self.drift % 1000.0
        A[O["PERIOD"]] = period
        A[O["TIME"]] = (t_sh if t_sh is not None else self.t) % 3600.0
        A[O["EXPO"]] = max(float(p["intensity"]), 0.0) * (0.92 + 0.16 * es)
        A[O["KEYGLOW"]] = 1.1 * (0.8 + 0.4 * es)
        A[O["BLOOM"]] = 0.75
        A[O["SEED"]] = float(self.seed % 65536)
        A[O["FOG0"]] = float(np.linalg.norm(eye - target)) + 6.0
        A[O["BG"]:O["BG"] + 3] = bg
        lt = np.array([0.15, 1.0, 0.35])
        A[O["LIGHT"]:O["LIGHT"] + 3] = lt / np.linalg.norm(lt)
        A[O["LCOL"]:O["LCOL"] + 3] = np.array([1.0, 0.87, 0.70]) * (1.0 + 0.12 * self.res)
        A[O["FLN"]:O["FLN"] + 3] = R[1, :]                       # the world floor's normal in the tray's frame
        A[O["FLY"]] = FLOOR_Y
        A[O["SPOT"]:O["SPOT"] + 3] = RT @ target
        A[O["OPEN"]] = max(self.open, 0.3 * self.flash)
        A[O["GAPCOL"]:O["GAPCOL"] + 3] = gapcol
        A[O["FOCUS"]] = float(np.linalg.norm(eye - target))
        A[O["DOF"]] = 0.0 if nseg > 1 else 0.3 + 1.3 * _smoothstep(0.2, 0.9, float(p["camera"]))
        A[O["KLK"]] = 0.10
        A[O["PAL"]:O["PAL"] + 12] = coeffs
        A[O["OWN"]:O["OWN"] + 128] = self.owner
        A[O["AMB"]:O["AMB"] + 128] = self.amb
        A[O["REGW"]:O["REGW"] + 128] = regw
        A[O["SPAWN"]:O["SPAWN"] + SPAWN_MAX * SPW] = self.SP.reshape(-1)
        np.nan_to_num(A, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        self.arena.set(A)

        # launches
        gN, bN = ((N_MAX + 127) // 128,), (128,)
        cur = self.cur
        if n_sp:
            self.k_spawn((1,), (SPAWN_MAX,), (self.P[cur], self.V[cur], self.Q, self.W, self.M, self.arena))
        for _ in range(nsub):
            P0, V0, P1, V1 = self.P[cur], self.V[cur], self.P[1 - cur], self.V[1 - cur]
            self.count.data.memset_async(0, nc * 4)
            self.k_hash(gN, bN, (P0, self.cell, self.rank, self.count, self.arena))
            self.k_scan((1,), (1024,), (self.count, self.start, self.arena))
            self.k_scatter(gN, bN, (self.cell, self.rank, self.start, self.sidx))
            self.k_step(gN, bN, (P0, V0, P1, V1, self.Q, self.W, self.M, self.cell, self.start, self.count,
                                 self.sidx, self.arena))
            cur = 1 - cur
        self.cur = cur
        Pc, Vc = self.P[cur], self.V[cur]
        self.KL.data.memset_async(0, self.KL.nbytes)
        self.k_emit(gN, bN, (Pc, Vc, self.W, self.M, notes, self.E, self.KL, self.arena))
        self.k_raster((N_MAX,), (128,), (Pc, self.vis, self.arena))
        self.G.data.memset_async(0, self.G.nbytes)
        self.k_glow((N_MAX,), (128,), (Pc, self.E, self.G, self.arena))
        gw, gh = np.int32(self.gw), np.int32(self.gh)
        launch_2d(self.k_blur, self.gw, self.gh, (self.G, self.G2, gw, gh, np.int32(1), np.int32(0)))
        launch_2d(self.k_blur, self.gw, self.gh, (self.G2, self.G, gw, gh, np.int32(0), np.int32(1)))
        launch_2d(self.k_shade, self.w, self.h, (self.F, self.vis, Pc, Vc, self.Q, self.M, self.E, notes, self.KL,
                                                 self.start, self.count, self.sidx, self.arena))
        launch_2d(self.k_down, self.gw, self.gh, (self.F, self.D, np.int32(self.w), np.int32(self.h), gw, gh))
        for step in (1, 2):
            launch_2d(self.k_blur, self.gw, self.gh, (self.D, self.D2, gw, gh, np.int32(step), np.int32(0)))
            launch_2d(self.k_blur, self.gw, self.gh, (self.D2, self.D, gw, gh, np.int32(0), np.int32(step)))
        launch_2d(self.k_comp, self.w, self.h, (self.buf, self.F, self.D, self.G, self.arena))
        present(self.ctx, self.buf, out)

    def close(self) -> None:
        self.P = self.V = self.Q = self.W = self.M = self.E = None
        self.cell = self.rank = self.sidx = self.count = self.start = self.vis = None
        self.G = self.G2 = self.D = self.D2 = self.F = self.KL = None
        self.buf = self.arena = self.silent = None
