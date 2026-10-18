"""Note ripples: every piano strike drops a glowing ripple on the picture, which bends the scene like water.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  Each onset in ``self.sound.frame.onsets`` (every
strike exactly once; the list object is remembered so a frame is never taken twice) takes a slot in a host ring of
64 ripples: its source point is mapped from the pitch (low notes left, high right) along a gentle arc at ``height``,
or, with ``layout``, at scattered points seeded per pitch (the point is fixed when the note is struck, so a layout
change eases in with the next notes).  A ripple is 1-4 concentric rings (``rings``, fractional fades the last in):
the leading ring's radius grows fast and settles (1 - e^-2.2a, a = age / life), rings widen as they spread, trailing
rings follow inside, each 0.7x the one outside.  Each ring is a thin bright core (with a little white: a hot line)
plus a soft halo 3.2x wider.  The leading wavefront also carries a wide, smooth swell of "water" (its width grows
with the radius; a compact gaussian-like bump, (1 - x^2/4)^4, that ends smoothly at 2 widths) whose slope refracts
the scene: ``refraction`` 1 bends it by up to ~0.39x the swell's width (below the fold, a full pulse included), the
default a gentle lens.  Brightness is from the velocity; a ripple blooms in over 0.16 s (the strike
reaches the screen late, so no flash) and eases out over its life (1.5 s for the top keys to 3 s for the bass, times
``fade``); the swell outlasts the glow a little.  Colour: the cosine palette indexed by pitch, drifting a little as
the ripple ages (``palette_period`` scales both), plus ``hue``.  A note still sounding (LEVEL, which includes the
pedal, read in dB: -40 dB -> nothing) keeps a small soft breathing glow and a little lens bulge at its source point,
carried by the pitch's newest ripple and eased on the host (a re-strike crossfades).  The ring full: the least
visible ripple is replaced.

GPU, per frame: the host packs the live slots (24 floats each: centre, radius, inverse widths, glow and lens
amplitudes, spacing, rings, held glow, colour, cull radii, the rings' band) and uploads them (``arr.set``, 6 KB).
``nr_field`` runs at ``render_scale`` (default 0.5): each 32 x 8 block tests every ripple's annulus against its
rectangle (ripples in shared memory), flags its tile and compacts the ripples that reach it into a list (a warp
ballot); an untouched tile returns at once.  Each pixel then loops over that list only (at most 64; early-out on
distance, then the rings, the swell and the held glow each test their own band) and at most 4 rings each
(gaussians cut at 3 sigma), summing the glow colour, a scalar glow (for the scene tint) and the slope of the swell
(a vector, in frame heights).  ``nr_comp`` runs at full size: bilinear taps of both buffers (a tap in an unflagged
tile is zero; a pixel whose taps are all unflagged is copied), the source looked up displaced along the slope (the
"lightly warped" look; the halo's slope also lights a glint on each ring's up-left flank, the 3D water look) and
the glow screen-blended over it (soft-clipped 1 - e^-x, in sRGB) at ``opacity``; ``scene_tint`` takes the glow's
colour from the picture underneath instead of the palette.  Both kernels compile with ``--use_fast_math``.

amount scales the glow and the displacement together (amount 0 copies the input exactly; ripples keep their state).
pulse adds glow and a little lens (amplitude kicks, never a position).  No sound, or nothing sounding: the input
passes through untouched (live ripples finish fading).  dt = 0: nothing ages; dt is clamped to 1 s.

Cost (P4000, 4K, GPU, field + composite; measured 18 Oct on a card thermally throttled to 1.2 GHz and shared with
the live renderer, so upper bounds; random notes over the keyboard, held): no ripples = a copy (~0.35 ms).  Defaults
~1.3 ms at 5 notes/s, ~1.6 ms at 12/s, ~2.2 ms with the ring full (30/s).  Presets at 6 / 12 / 20 notes/s: pond
~1.4 / 2.0 / 2.7 ms, rain ~1.5 / 1.7 / 1.9 ms, lens (render_scale 0.35: its swells are wide and soft and look the
same as at 0.5) ~2.1 / 2.7 / 3.3 ms.  The cost grows with the ripples' area x count: size 2, 4 rings, refraction 1
and the ring full is ~6 ms at render_scale 0.5 (lower render_scale for that look).  Plus ~1 ms of host numpy per
frame (more on a loaded CPU).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

MAXR = 64                     # ripple slots (the host ring)
RW = 24                       # floats per packed ripple row
CUT = 3.0                     # gaussian cut-off (sigmas): beyond it a ring adds < 1/255
MAX_ONSETS = 16               # onsets taken per frame
BLOOM_S = 0.16                # bloom-in of a strike
LOW, HIGH = 21, 108           # A0 .. C8
GROW_K = 2.2                  # radius growth: 1 - e^(-k a), normalised (fast out, settling)
HALO_W = 3.2                  # halo width, x the core width
HALO_A = 0.3                  # halo brightness, x the core's
TRAIL = 0.7                   # each trailing ring, x the one outside it
CORE_W = 0.35                 # white in the core (a hot line)
LENS_R = 0.22                 # wavefront swell width: LENS_R x the radius + LENS_H x the halo width
LENS_H = 2.0
LENS_A = 0.41                 # swell height (slope scale), x its width: max bend 0.39 x width at refraction 1
LENS_CUT = 2.0                # the swell's support (x its width): the profile (1 - x^2/4)^4 ends there, smoothly
SUS_GAIN = 0.9                # held-note glow
SUS_LENS = 1.1                # held-note bulge, x its radius
SUS_TAU_S = 0.35              # held-note glow easing
BREATH_HZ = 0.42              # held-note glow breathing
AGE_HUE = 0.18                # palette index drift over a ripple's life (x palette_period)
GLINT = 0.5                   # glint gain at refraction 1
LIGHT = (-0.6, -0.8)          # glint light direction (screen, y down): up-left

# row layout: 0 cx | 1 cy | 2 R | 3 1/core width | 4 glow amp | 5 spacing | 6 rings | 7 held glow
#             8 1/held radius | 9..11 rgb | 12 inner cull radius | 13 outer cull radius | 14 held lens (height units)
#             15 swell lens (height units) | 16 1/halo width | 17 2 x core width (a young ring's fade-in)
#             18 1/swell width | 19, 20 the rings' band (inner, outer radius) | 21..23 -
_SRC = HELPERS + f"""
#define MAXR {MAXR}
#define RW {RW}
#define HALO_A {HALO_A}f
#define TRAIL {TRAIL}f
#define CORE_W {CORE_W}f
#define CUT {CUT}f
#define TW 32
#define TH 8
""" + r"""
// Field at the internal size.  Blocks are TW x TH; each culls the ripples against its rectangle and records in
// tile[] whether any reached it.  An empty tile writes nothing: nr_comp reads its texels as zero.
extern "C" __global__
void nr_field(const float* __restrict__ rows, const int n, float4* __restrict__ glow, float2* __restrict__ slope,
              unsigned char* __restrict__ tile, const int w, const int h, const float glint, const float lx,
              const float ly)
{
    static_assert(MAXR == 64, "the block cull ballots in two whole warps");
    __shared__ float s[MAXR * RW];
    __shared__ unsigned int bal[2];
    __shared__ unsigned char lst[MAXR];
    const int tid = threadIdx.y * TW + threadIdx.x;
    const float inv = 1.f / (float)h;                         // height units per internal pixel
    const float bx0 = blockIdx.x * TW * inv, by0 = blockIdx.y * TH * inv;
    const float bx1 = bx0 + TW * inv, by1 = by0 + TH * inv;
    const int nn = min(max(n, 0), MAXR);
    for (int i = tid; i < nn * RW; i += TW * TH) s[i] = rows[i];
    __syncthreads();
    if (tid < MAXR) {                                         // warps 0-1 (whole): cull each ripple vs this block
        int mine = 0;
        if (tid < nn) {                                       // its annulus vs the block's rectangle
            const float* r = s + tid * RW;
            const float cx = r[0], cy = r[1];
            const float qx = fminf(fmaxf(cx, bx0), bx1) - cx, qy = fminf(fmaxf(cy, by0), by1) - cy;
            const float fx = fmaxf(fabsf(cx - bx0), fabsf(cx - bx1)), fy = fmaxf(fabsf(cy - by0), fabsf(cy - by1));
            const float dmin = sqrtf(qx * qx + qy * qy), dmax = sqrtf(fx * fx + fy * fy);
            mine = (dmin <= r[13] && dmax >= r[12]) ? 1 : 0;
        }
        const unsigned int b = __ballot_sync(0xffffffffu, mine);
        if ((tid & 31) == 0) bal[tid >> 5] = b;
    }
    __syncthreads();
    const int c0 = __popc(bal[0]), cnt = c0 + __popc(bal[1]);   // the same in every thread of the block
    if (tid == 0) tile[blockIdx.y * gridDim.x + blockIdx.x] = cnt > 0 ? 1 : 0;
    if (cnt == 0) return;
    if (tid < MAXR) {                                         // compact the hits, in slot order
        const unsigned int b = bal[tid >> 5];
        const int lane = tid & 31;
        if ((b >> lane) & 1u) lst[((tid >> 5) ? c0 : 0) + __popc(b & ((1u << lane) - 1u))] = (unsigned char)tid;
    }
    __syncthreads();
    const int x = blockIdx.x * TW + threadIdx.x, y = blockIdx.y * TH + threadIdx.y;
    if (x >= w || y >= h) return;
    const float px = (x + 0.5f) * inv, py = (y + 0.5f) * inv;
    float cr = 0.f, cg = 0.f, cb = 0.f, I = 0.f, gx = 0.f, gy = 0.f;
    for (int j = 0; j < cnt; ++j) {                           // bounded: at most 64 ripples (those reaching the block)
        const float* r = s + (int)lst[j] * RW;
        const float dx = px - r[0], dy = py - r[1];
        const float d2 = dx * dx + dy * dy, o = r[13];
        if (d2 > o * o) continue;
        const float d = sqrtf(d2) + 1e-6f, id = 1.f / d;
        const float nx = dx * id, ny = dy * id;
        const float R = r[2];
        float ring = 0.f, core = 0.f, sl = 0.f, gl = 0.f;
        if (r[4] > 0.f && d >= r[19] && d <= r[20]) {         // inside the rings' band
            const float iw = r[3], sp = r[5], nr = r[6], ih = r[16], e2 = r[17];
            const float nl = nx * lx + ny * ly;
            float tr = 1.f;
            #pragma unroll
            for (int k = 0; k < 4; ++k, tr *= TRAIL) {        // bounded: at most 4 rings per ripple
                const float wk = clamp01(nr - (float)k);      // fractional ring count fades the last ring in
                const float rk = R - k * sp;
                if (wk <= 0.f || rk <= 0.f) break;
                const float th = (d - rk) * ih;
                if (fabsf(th) > CUT) continue;
                const float a = r[4] * wk * tr * smooth(0.f, e2, rk);
                const float gh = __expf(-th * th);
                const float t = (d - rk) * iw;
                const float gc = t * t < 12.f ? __expf(-t * t) : 0.f;
                ring += a * (gc + HALO_A * gh);
                core += a * gc;
                const float l = fmaxf(2.f * th * gh * nl, 0.f);   // the halo's up-left flank catches the light
                gl += a * l * l;
            }
        }
        if (r[15] > 0.f) {                                    // the wavefront's swell: a wide, smooth lens
            const float tl = (d - R) * r[18];
            const float q = 1.f - 0.25f * tl * tl;            // height (1 - tl^2/4)^4: gaussian-like, compact
            if (q > 0.f) sl += r[15] * (-2.f * tl) * (q * q * q);
        }
        if (r[7] > 0.f) {                                     // a held note's breathing glow (a soft lens)
            const float u = d * r[8];
            if (u < CUT) {
                const float e = __expf(-u * u);
                ring += r[7] * e;
                core += 0.3f * r[7] * e;
                sl += r[14] * (-2.f * u) * e;
            }
        }
        if (ring <= 0.f && sl == 0.f) continue;               // nothing here (core and glint need a ring)
        const float wh = CORE_W * core + glint * gl;          // the hot core and the glint are near white
        const float rg = ring + 0.5f * glint * gl;
        cr += r[9] * rg + wh; cg += r[10] * rg + wh; cb += r[11] * rg + wh;
        I += rg + wh;
        gx += nx * sl; gy += ny * sl;
    }
    glow[y * w + x] = make_float4(cr, cg, cb, I);
    slope[y * w + x] = make_float2(gx, gy);
}

// Full size: bilinear taps of the field (a tap in an empty tile is zero), the source looked up displaced along
// the slope, the glow screen-blended over it (soft-clipped, sRGB).
extern "C" __global__
void nr_comp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float4* __restrict__ glow, const float2* __restrict__ slope,
             const unsigned char* __restrict__ tile, const int w, const int h, const int tw,
             const float gk, const float dk, const float tint)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float fx = fminf(fmaxf((x + 0.5f) * ((float)w / W) - 0.5f, 0.f), (float)(w - 1));
    float fy = fminf(fmaxf((y + 0.5f) * ((float)h / H) - 0.5f, 0.f), (float)(h - 1));
    const int x0 = (int)fx, y0 = (int)fy, x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const int t00 = tile[(y0 / TH) * tw + x0 / TW], t10 = tile[(y0 / TH) * tw + x1 / TW];
    const int t01 = tile[(y1 / TH) * tw + x0 / TW], t11 = tile[(y1 / TH) * tw + x1 / TW];
    if (!(t00 | t10 | t01 | t11)) { out[y * W + x] = src[y * W + x]; return; }
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const float w00 = t00 ? u * v : 0.f, w10 = t10 ? tx * v : 0.f, w01 = t01 ? u * ty : 0.f, w11 = t11 ? tx * ty : 0.f;
    const float4 z4 = make_float4(0.f, 0.f, 0.f, 0.f);
    const float2 z2 = make_float2(0.f, 0.f);
    const float4 a = t00 ? glow[y0 * w + x0] : z4, b = t10 ? glow[y0 * w + x1] : z4;
    const float4 c = t01 ? glow[y1 * w + x0] : z4, d = t11 ? glow[y1 * w + x1] : z4;
    const float2 sa = t00 ? slope[y0 * w + x0] : z2, sb = t10 ? slope[y0 * w + x1] : z2;
    const float2 sc = t01 ? slope[y1 * w + x0] : z2, sd = t11 ? slope[y1 * w + x1] : z2;
    const float I = w00 * a.w + w10 * b.w + w01 * c.w + w11 * d.w;
    const float ox = dk * (w00 * sa.x + w10 * sb.x + w01 * sc.x + w11 * sd.x);
    const float oy = dk * (w00 * sa.y + w10 * sb.y + w01 * sc.y + w11 * sd.y);
    const float4 s = (fabsf(ox) + fabsf(oy) > 0.004f) ? bilin(src, x + 0.5f + ox, y + 0.5f + oy, W, H)
                                                     : ld(src, x, y, W, H);
    if (I <= 1e-6f) { out[y * W + x] = pack(s); return; }
    float r = w00 * a.x + w10 * b.x + w01 * c.x + w11 * d.x;
    float g = w00 * a.y + w10 * b.y + w01 * c.y + w11 * d.y;
    float bl = w00 * a.z + w10 * b.z + w01 * c.z + w11 * d.z;
    if (tint > 0.f) {                                         // the scene's own colour, lifted to a glow
        const float m = fmaxf(fmaxf(s.x, s.y), fmaxf(s.z, 1.f));
        const float k = tint * smooth(8.f, 48.f, m), q = I / m;
        r += (I * 0.18f + 0.82f * s.x * q - r) * k;
        g += (I * 0.18f + 0.82f * s.y * q - g) * k;
        bl += (I * 0.18f + 0.82f * s.z * q - bl) * k;
    }
    const float er = 1.f - __expf(-fmaxf(r, 0.f) * gk);
    const float eg = 1.f - __expf(-fmaxf(g, 0.f) * gk);
    const float eb = 1.f - __expf(-fmaxf(bl, 0.f) * gk);
    out[y * W + x] = pack(make_float4(s.x + (255.f - s.x) * er, s.y + (255.f - s.y) * eg,
                                      s.z + (255.f - s.z) * eb, 255.f));
}
"""


def _smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class NoteRipples(Effect):
    name = "note_ripples"
    stage = "overlay"
    label = "Note ripples"
    description = ("Every piano strike drops a glowing ripple on the picture at its key's place, low notes left and "
                   "high right, that spreads, gently bends the scene like water and fades; held notes keep "
                   "breathing.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "refraction": num(0.0, 1.0, 0.35, "Refraction (water warp)", tier="regional", basic=True),
        "size": num(0.3, 2.0, 1.0, "Ripple size", step=0.05, tier="regional", basic=True),
        "opacity": num(0.0, 1.0, 0.7, "Glow opacity", tier="regional"),
        "rings": num(1.0, 4.0, 2.0, "Rings per note", step=0.1, tier="regional"),
        "layout": num(0.0, 1.0, 0.0, "Layout (arc · scattered)", tier="regional"),
        "height": num(0.15, 0.9, 0.6, "Arc height (top · bottom)", tier="regional"),
        "fade": num(0.4, 2.0, 1.0, "Fade time", unit="×", step=0.05, tier="regional"),
        "sustain": num(0.0, 1.5, 0.6, "Held-note glow", tier="regional"),
        "hue": num(0.0, 0.75, 0.0, "Hue shift", tier="regional"),
        "scene_tint": num(0.0, 1.0, 0.0, "Scene colours", tier="regional"),
        **palette_params("neon", period=0.7, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._kf = mod.get_function("nr_field")
        self._kc = mod.get_function("nr_comp")
        W, H = ctx.width, ctx.height
        self.A = W / H
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.glow = cp.zeros((self.h, self.w, 4), cp.float32)
        self.slope = cp.zeros((self.h, self.w, 2), cp.float32)
        self.tw, self.th = (self.w + 31) // 32, (self.h + 7) // 8
        self.tile = cp.zeros(self.tw * self.th, cp.uint8)
        self.rows = cp.zeros(MAXR * RW, cp.float32)
        self.rows_h = np.zeros((MAXR, RW), np.float32)
        seed = int(params.get("seed", 1)) & 0x7FFFFFFF
        self.scatter = np.random.default_rng(seed).random((128, 2))
        self.rng = np.random.default_rng(seed + 7919)
        # the ring of ripples (host)
        self.act = np.zeros(MAXR, bool)
        self.pitch = np.zeros(MAXR, np.int64)
        self.u = np.zeros(MAXR)
        self.vel = np.zeros(MAXR)
        self.age = np.zeros(MAXR)
        self.life = np.ones(MAXR)
        self.x = np.zeros(MAXR)
        self.y = np.zeros(MAXR)
        self.rmax = np.zeros(MAXR)
        self.sus = np.zeros(MAXR)
        self.ph = np.zeros(MAXR)
        self.owner = np.full(128, -1, np.int64)      # pitch -> its newest slot (the one that carries the held glow)
        self.clock = 0.0
        self.drift = 0.0
        self._seen = None                            # the onsets list already taken (SoundView makes one per update)

    # ---------------------------------------------------------------------------------------------------- host side
    def _palette(self, name: str) -> np.ndarray:
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES.get(name, PALETTES["ice"]) if rows is None else rows, np.float64).reshape(-1)[:12]

    def _spawn(self, pitch: int, vel: float, age: float, p: dict) -> None:
        free = np.flatnonzero(~self.act)
        if free.size:
            i = int(free[0])
        else:                                        # full: replace the ripple nearest its end
            left = np.clip(1.0 - self.age / self.life, 0.0, 1.0) ** 1.4 * (0.3 + 0.7 * self.vel)
            i = int(np.argmin(left + self.sus))
        old = int(self.pitch[i])
        if self.act[i] and self.owner[old] == i:
            self.owner[old] = -1
        u = min(max((pitch - LOW) / float(HIGH - LOW), 0.0), 1.0)
        A = self.A
        lay = float(_smoothstep(0.0, 1.0, p["layout"]))
        ax = A * (0.07 + 0.86 * u)
        ay = float(p["height"]) - 0.09 * (1.0 - (2.0 * u - 1.0) ** 2)
        sx = A * (0.08 + 0.84 * (0.3 * u + 0.7 * self.scatter[pitch, 0]))
        sy = 0.12 + 0.76 * self.scatter[pitch, 1]
        jx, jy = self.rng.uniform(-0.006, 0.006, 2)
        self.act[i] = True
        self.pitch[i] = pitch
        self.u[i] = u
        self.vel[i] = vel
        self.age[i] = age
        self.life[i] = (1.5 + 1.5 * (1.0 - u)) * float(p["fade"]) * (0.8 + 0.3 * vel)
        self.x[i] = ax + (sx - ax) * lay + jx
        self.y[i] = ay + (sy - ay) * lay + jy
        self.rmax[i] = 0.28 * float(p["size"]) * (1.25 - 0.6 * u) * (0.6 + 0.5 * vel)
        self.sus[i] = 0.0
        self.ph[i] = self.rng.uniform(0.0, 2.0 * math.pi)
        self.owner[pitch] = i

    def _step(self, p: dict, dt: float, lens_on: bool) -> int:
        """Age the ring, take this frame's onsets, ease the held glows, pack the live rows.  Returns the count."""
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(snd is not None and fr is not None and getattr(snd, "present", False))
        act = self.act
        self.age[act] += dt
        if heard:
            ons = getattr(fr, "onsets", None)
            if ons is not None and ons is not self._seen:
                for ev in list(ons)[:MAX_ONSETS]:
                    try:
                        pitch, vel, age = int(ev[0]), float(ev[1]), float(ev[2])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if 0 <= pitch < 128 and math.isfinite(vel) and math.isfinite(age):
                        self._spawn(pitch, min(max(vel, 0.0), 1.0), min(max(age, 0.0), 0.5), p)
            self._seen = ons
        idx = np.flatnonzero(act)
        if idx.size == 0:
            return 0
        # held glow: only a pitch's newest ripple carries it; eased so a re-strike crossfades
        tgt = np.zeros(idx.size)
        if heard:
            notes = getattr(fr, "notes", None)
            if notes is not None:
                lev = np.nan_to_num(np.asarray(notes[self.pitch[idx], 0], np.float64), nan=0.0)
                own = self.owner[self.pitch[idx]] == idx
                loud = np.clip(1.0 + 0.5 * np.log10(np.maximum(lev, 1e-6)), 0.0, 1.0)   # -40 dB -> 0, 0 dB -> 1
                tgt = own * _smoothstep(0.0, 0.6, loud) * _smoothstep(0.04, 0.5, self.age[idx])
        k = 1.0 - math.exp(-dt / SUS_TAU_S) if dt > 0.0 else 0.0
        self.sus[idx] += (tgt - self.sus[idx]) * k
        done = (self.age[idx] >= self.life[idx]) & (self.sus[idx] < 0.003) & (tgt <= 0.0)
        if done.any():
            gone = idx[done]
            act[gone] = False
            for i in gone:
                if self.owner[self.pitch[i]] == i:
                    self.owner[self.pitch[i]] = -1
            idx = idx[~done]
            if idx.size == 0:
                return 0
        # pack
        size, rings = float(p["size"]), float(p["rings"])
        u, vel, age, life = self.u[idx], self.vel[idx], self.age[idx], self.life[idx]
        a = np.clip(age / life, 0.0, 1.0)
        bloom = _smoothstep(0.0, BLOOM_S, age)
        env = bloom * (1.0 - a) ** 1.4
        R = 0.006 + self.rmax[idx] * (1.0 - np.exp(-GROW_K * a)) / (1.0 - math.exp(-GROW_K))
        wd = (0.0018 + 0.012 * R) * (0.85 + 0.35 * (1.0 - u)) * (0.75 + 0.25 * size)
        amp = (0.3 + 0.7 * vel) * env
        hw = HALO_W * wd
        lw = LENS_R * R + LENS_H * hw                                   # the wavefront swell's width
        lens = (0.3 + 0.7 * vel) * bloom * (1.0 - a) ** 0.8 * LENS_A * lw if lens_on else np.zeros(idx.size)
        sp = 3.6 * wd + 0.01
        br = 0.78 + 0.22 * np.sin(2.0 * math.pi * BREATH_HZ * self.clock + self.ph[idx])
        sus = self.sus[idx] * float(p["sustain"]) * br * SUS_GAIN
        rs = (0.012 + 0.012 * (1.0 - u)) * size * (0.94 + 0.06 * br) * (0.7 + 0.4 * float(p["sustain"]))
        P = self._palette(p["palette"])
        per = float(p["palette_period"])
        t = (u + AGE_HUE * a) * per + self.drift + float(p["hue"])
        col = np.clip(P[0:3] + P[3:6] * np.cos(2.0 * math.pi * (P[6:9] * t[:, None] + P[9:12])), 0.0, 1.0)
        col = col / np.maximum(col.max(axis=1, keepdims=True), 0.25)      # bright hues, never a dark ring
        nrc = math.ceil(rings - 1e-6)
        ring_on = amp > 1e-4
        lens_live = lens > 1e-5
        sus_on = sus > 1e-4
        reach = np.where(lens_live, np.maximum(LENS_CUT * lw, CUT * hw), CUT * hw)
        ring_lo = np.maximum(R - (nrc - 1) * sp - CUT * hw, 0.0)
        ring_hi = R + CUT * hw
        inner_r = np.where(ring_on, ring_lo, np.inf)
        inner_l = np.where(lens_live, np.maximum(R - LENS_CUT * lw, 0.0), np.inf)
        inner = np.where(sus_on, 0.0, np.minimum(inner_r, inner_l))
        outer = np.maximum(np.where(ring_on | lens_live, R + reach, 0.0), np.where(sus_on, CUT * rs, 0.0))
        vis = ring_on | sus_on | lens_live
        n = int(vis.sum())
        if n == 0:
            return 0
        h = self.rows_h
        h[:n, 0] = self.x[idx][vis]
        h[:n, 1] = self.y[idx][vis]
        h[:n, 2] = R[vis]
        h[:n, 3] = 1.0 / wd[vis]
        h[:n, 4] = np.where(ring_on, amp, 0.0)[vis]
        h[:n, 5] = sp[vis]
        h[:n, 6] = rings
        h[:n, 7] = np.where(sus_on, sus, 0.0)[vis]
        h[:n, 8] = 1.0 / rs[vis]
        h[:n, 9:12] = col[vis]
        h[:n, 12] = np.minimum(inner, outer)[vis]
        h[:n, 13] = outer[vis]
        h[:n, 14] = (SUS_LENS * rs * self.sus[idx] * float(p["sustain"]))[vis]
        h[:n, 15] = np.where(lens_live, lens, 0.0)[vis]
        h[:n, 16] = 1.0 / hw[vis]
        h[:n, 17] = 2.0 * wd[vis]
        h[:n, 18] = 1.0 / lw[vis]
        h[:n, 19] = ring_lo[vis]
        h[:n, 20] = ring_hi[vis]
        h[:n, 21:] = 0.0
        h[n:, :] = 0.0
        self.rows.set(h.reshape(-1))
        return n

    # ---------------------------------------------------------------------------------------------------- render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dtc = min(max(float(dt), 0.0), 1.0)
        self.clock = math.fmod(self.clock + dtc, 3600.0)
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 100.0)
        amount = min(max(float(p["amount"]), 0.0), 1.0)
        refr = min(max(float(p["refraction"]), 0.0), 1.0)
        n = self._step(p, dtc, refr * amount > 1e-4)
        if amount <= 1e-4 or n == 0:
            cp.copyto(out, src)
            return
        W, H = self.ctx.width, self.ctx.height
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        self._kf((self.tw, self.th, 1), (32, 8, 1),
                 (self.rows, np.int32(n), self.glow, self.slope, self.tile, np.int32(self.w), np.int32(self.h),
                  np.float32(GLINT * refr), np.float32(LIGHT[0]), np.float32(LIGHT[1])))
        gk = 2.4 * amount * float(p["opacity"]) * (1.0 + 0.6 * pulse)
        dk = amount * refr * H * (1.0 + 0.2 * pulse)          # height units -> output pixels (no fold on a beat)
        launch_2d(self._kc, W, H, (src, out, np.int32(W), np.int32(H), self.glow, self.slope, self.tile,
                                   np.int32(self.w), np.int32(self.h), np.int32(self.tw), np.float32(gk),
                                   np.float32(dk), np.float32(p["scene_tint"])))

    def close(self) -> None:
        self.glow = self.slope = self.rows = self.tile = None
