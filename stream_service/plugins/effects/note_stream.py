"""Note stream: the piano's notes as glowing streaks flowing across the picture, like a living piano roll.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

Listens to the piano (``listens = ("notes",)``, CONVENTIONS §2b).  Every strike starts a rounded streak at the
edge the stream comes from (``angle``: 90 = rising from the bottom).  Pitch sets its lane across the stream (keys
21-108 evenly over ``spread`` of the frame), time since the strike is the distance travelled (``travel`` screen
heights per second), so a held note stretches into a long streak anchored at the edge, and on key-up it detaches
and floats on.  Velocity sets width and brightness; the sounding level (pedal holds included) keeps the halo
alive; brightness along a streak follows the piano's decay (bright head at the strike, dimmer towards the
release), and a released streak trails a soft comet glow that widens as it fades (``trail``, ``linger``).

Geometry (one pass per pixel at ``render_scale``): screen -> the stream's frame (along / across the motion) ->
optionally a plane tilted into depth (``depth``: the roll recedes into the distance along its direction of
travel; inverse perspective, anisotropic pixel metric for anti-aliasing, depth fog) -> a light domain warp
(``warp``: a few slow sines that ride along with the notes and grow with distance from the edge, so
streaks leave the edge straight and bend as they float) -> the pitch lane.

Host side: a preallocated ring of 256 note events (pitch, strike / release times on the sound clock, velocity,
integrated head / tail distances so a ``travel`` change never makes notes jump, eased bloom / sounding glow /
visibility).  Each frame it is binned into 88 pitch lanes of up to 8 streaks (newest first; the two oldest
slots fade out before a lane overflows) and uploaded as one small table; every pixel checks only its own lane
and up to 3 lanes either side (bounded; the halo is windowed to the lanes it can reach, so no seams).

Composite: the overlay (8-bit, tonemapped) is screen-blended in sRGB onto the full-resolution input with
bilinear sampling, over a light backing shade where streaks are (so they read on bright pictures), scaled by
the eased amount (1 - (1 - amount)^2) x ``opacity``.  amount 0, no sound or no notes -> the input is copied
exactly.  pulse -> a soft glow kick (eased; amplitude only).  Strikes bloom in over ~0.2 s and ease out.
Colour: the shared cosine palette along the keyboard (``palette_period``), drifting (``drift_speed``), ``hue``.

Cost (P4000, 4K, render_scale 0.5, kernels timed alone): draw ~0.6-0.95 ms + composite ~0.67 ms for normal
playing; ~2.5 + 0.7 ms in a synthetic storm (all 256 streaks over all 88 keys at maximum width, glow, warp and
trail).  Host ~1-1.5 ms per frame (numpy on <= 256 events), which overlaps the GPU.  fxtest's effect path does
not attach the test piano, so its --bench (~0.34 ms) times the pass-through copy only.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTES
from stream_service.plugins._lib.params import flag, num, palette_params, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

N_EVENTS = 256                   # live streaks (when full, the one released longest ago is dropped)
K = 8                            # streaks per pitch lane (newest first)
LOW, HIGH = 21, 108              # A0 .. C8
N_LANE = HIGH - LOW + 1          # 88
NS = 8                           # floats per slot
CAM_D = 1.5                      # camera distance (screen heights) for the depth tilt
MAX_TILT = math.radians(62.0)
NP = 48
# P layout: 0 w | 1 h | 2 A | 3,4 d (along) | 5,6 n (across) | 7 D | 8 cos phi | 9 sin phi | 10 s0 | 11 z0
#  12 lane span | 13 lane spacing | 14 R lanes | 15 Rmax | 16 warp amp | 17,18 (unused)
#  19 glow radius | 20 core white | 21 trail len | 22 gain | 23 exposure | 24 palette period | 25,27 (unused)
#  26 fog | 28 flat? | 29..37 palette a, b, c | 38..40 palette d + c x colour phase (mod 1, on the host)
#  41 lane margin of the early-out (R + warp sway) | 42..46 the warp sines' phases (mod 2 pi, on the host)

TAU_A0_S, TAU_HALVING, TAU_MIN_S = 5.0, 24.0, 0.3          # the piano's sustain (as sound_view's note model)

_SRC = HELPERS + r"""
#define K """ + str(K) + r"""
#define NL """ + str(N_LANE) + r"""
#define NS """ + str(NS) + r"""
#define NP """ + str(NP) + r"""

extern "C" __global__
void ns_draw(uchar4* __restrict__ ov, const float* __restrict__ B, const unsigned int seed)
{
    const float* __restrict__ P = B;                    // params
    const float* __restrict__ cnt = B + NP;             // streaks per lane
    const float* __restrict__ pre = B + NP + 96;        // occupied lanes below lane i (prefix count)
    const float* __restrict__ tab = B + NP + 192;       // the lane table
    const int w = (int)P[0], h = (int)P[1];
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float A = P[2];
    const float qx = ((x + 0.5f) / w - 0.5f) * A, qy = 0.5f - (y + 0.5f) / h;      // screen heights, y up
    const float Yr = qx * P[3] + qy * P[4];                                        // along the motion
    const float Xr = qx * P[5] + qy * P[6];                                        // across it
    float s, u, ku, ks, z;
    const float D = P[7], cph = P[8], sph = P[9];
    if (P[28] > 0.5f) {
        s = Yr; u = Xr; z = D; ku = (float)h; ks = (float)h;
    } else {
        const float den = D * cph - Yr * sph;
        if (den <= 2e-3f) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }   // beyond the horizon
        s = Yr * D / den;
        z = D + s * sph;
        u = Xr * z / D;
        ku = h * D / z;
        ks = h * D * D * cph / (z * z);
    }
    const float s0 = P[10];
    {   // nothing within reach of this pixel (the lanes it could see, plus the warp's sway): transparent
        const int c0 = (int)floorf((u / P[12] + 0.5f) * (NL - 1) + 0.5f), M = (int)P[41];
        const int lo = max(c0 - M, 0), hi = min(c0 + M, NL - 1);
        if (lo > hi || pre[hi + 1] - pre[lo] < 0.5f) { ov[y * w + x] = make_uchar4(0, 0, 0, 0); return; }
    }
    // light domain warp: rides with the notes, grows away from the edge
    const float g = P[16] * smooth(0.0f, 0.5f, s - s0);
    if (g > 1e-5f) {
        // a sum of slow sines (three bands; phases ride along with the notes, drift slowly, seeded; reduced
        // mod 2 pi on the host so they never wrap or lose precision): smooth sway, cheap on the SFU
        const float n1 = 0.5f * __sinf(2.3f * s + 1.1f * u + P[42])
                       + 0.3f * __sinf(4.7f * s - 2.3f * u + P[43])
                       + 0.2f * __sinf(7.9f * s + 3.7f * u + P[44]);
        const float m1 = 0.6f * __sinf(2.9f * s - 1.7f * u + P[45])
                       + 0.4f * __sinf(5.3f * s + 2.9f * u + P[46]);
        u += 1.1f * g * n1;
        s += 0.4f * g * m1;
    }
    const float d = s - s0;                                     // distance from the edge the notes come from
    const float span = P[12], ls = P[13];
    const int R = (int)P[14];
    const float Rmax = P[15];
    const float lanef = (u / span + 0.5f) * (NL - 1);
    const int c = (int)floorf(lanef + 0.5f);
    const float gr = P[19], tl = P[21];
    const float gpx = fmaxf(gr * ku, 0.5f);
    const float Rpx = Rmax * ku;                                // the glow's reach (px): every lane in it is checked
    const float rs = Rmax * ku / ks;                            // ... and along the motion (plane units)
    float3 acc = make_float3(0.f, 0.f, 0.f);
    float cov = 0.f;                                            // how much streak is here (for the backing shade)
    for (int j = -R; j <= R; ++j) {
        const int L = c + j;
        if (L < 0 || L >= NL) continue;
        const int n = min((int)cnt[L], K);
        if (n <= 0) continue;
        const float uL = ((float)L / (NL - 1) - 0.5f) * span;
        const float du = u - uL, adu = fabsf(du);
        if (adu > Rmax) continue;
        const float wl = 1.f - smooth(0.5f * Rmax, Rmax, adu);
        float3 col;
        {   // the shared cosine palette along the keyboard (phase = c x drift + d, reduced on the host)
            const float lt = (float)L * (1.f / (NL - 1)) * P[24];
            float x0 = P[35] * lt + P[38], x1 = P[36] * lt + P[39], x2 = P[37] * lt + P[40];
            x0 -= rintf(x0); x1 -= rintf(x1); x2 -= rintf(x2);
            col = make_float3(fminf(fmaxf(P[29] + P[32] * __cosf(6.2831853f * x0), 0.f), 1.f),
                              fminf(fmaxf(P[30] + P[33] * __cosf(6.2831853f * x1), 0.f), 1.f),
                              fminf(fmaxf(P[31] + P[34] * __cosf(6.2831853f * x2), 0.f), 1.f));
        }
        const float lat = du * ku;                                      // px across
        for (int k = 0; k < n; ++k) {
            const float* S = tab + (L * K + k) * NS;
            const float dtl = S[0], dh = S[1], hw = S[2];
            if (d > dh + hw + rs || d < dtl - hw - rs - (S[7] > 0.f ? 5.f * tl : 0.f)) continue;
            float a = dtl + hw, b = dh - hw;
            if (b < a) { a = b = 0.5f * (dtl + dh); }
            const float sc = fminf(fmaxf(d, a), b);
            const float al = (d - sc) * ks;
            const float hpx = hw * ku;
            const float dpx = sqrtf(lat * lat + al * al) - hpx;
            const float core = 1.f - smooth(-0.8f, 0.8f, dpx);
            const float dp = fmaxf(dpx, 0.f);
            // two-scale glow with compact support inside the reach (round, never a box)
            const float re = fmaxf(Rpx - hpx, 1.0f);
            const float halo = (__expf(-dp / gpx) + 0.45f * __expf(-dp / (2.5f * gpx)))
                               * (1.f - smooth(0.3f * re, re, dp));
            // brightness along the streak follows the note's decay from the strike (head) to the release (tail)
            const float prof = 0.42f + 0.58f * __expf(-fmaxf(dh - d, 0.f) * S[6]);
            const float hd = (dh - hw - d) / fmaxf(2.2f * hw, 1e-5f);
            const float cap = S[5] * __expf(-hd * hd);
            const float br = prof + cap;
            const float kc = S[3] * core * br;
            float kk = kc + S[4] * halo * (0.45f + 0.55f * br);
            if (S[7] > 0.f && d < dtl && tl > 0.f) {                    // a soft comet trail behind a released note
                const float tb = (dtl - d) / tl;
                const float wid = fminf(1.0f + 1.2f * tb, 0.4f * Rpx / fmaxf(hpx, 0.5f));
                const float lt = lat / fmaxf(hpx * wid, 0.5f);
                kk += S[7] * __expf(-tb - lt * lt) * smooth(0.f, 0.03f, d) * wl / sqrtf(wid);
            }
            const float wh = kc * P[20];
            acc.x += kk * col.x + wh; acc.y += kk * col.y + wh; acc.z += kk * col.z + wh;
            cov += kk;
        }
    }
    float k = P[22];
    if (P[28] < 0.5f) k *= __powf(fminf(P[11] / z, 1.f), P[26]);      // depth fog
    k *= smooth(0.5f, 2.0f, ls * ku);                                  // lanes under ~1 px apart (far away): fade
    const float e = P[23] * k;
    // fmaxf(NaN, 0) = 0: a bad value can never reach the byte conversion
    ov[y * w + x] = make_uchar4((unsigned char)(255.f * (1.f - __expf(-fmaxf(e * acc.x, 0.f))) + 0.5f),
                                (unsigned char)(255.f * (1.f - __expf(-fmaxf(e * acc.y, 0.f))) + 0.5f),
                                (unsigned char)(255.f * (1.f - __expf(-fmaxf(e * acc.z, 0.f))) + 0.5f),
                                (unsigned char)(255.f * (1.f - __expf(-fmaxf(1.4f * e * cov, 0.f))) + 0.5f));
}

// bilinear sample of all four channels (the overlay's alpha is its coverage), pixel centres at i + 0.5
__device__ __forceinline__ float4 ns_bilin4(const uchar4* __restrict__ img, float fx, float fy, int w, int h) {
    fx -= 0.5f; fy -= 0.5f;
    const int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int xa = min(max(x0, 0), w - 1), xb = min(max(x0 + 1, 0), w - 1);
    const int ya = min(max(y0, 0), h - 1), yb = min(max(y0 + 1, 0), h - 1);
    const uchar4 a = img[ya * w + xa], b = img[ya * w + xb], c = img[yb * w + xa], d = img[yb * w + xb];
    const float u = 1.f - tx, v = 1.f - ty;
    return make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                       v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                       v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z),
                       v * (u * a.w + tx * b.w) + ty * (u * c.w + tx * d.w));
}

// a soft backing shade under the streaks (so they read on bright pictures), then a screen blend, in sRGB
extern "C" __global__
void ns_comp(const uchar4* __restrict__ src, const uchar4* __restrict__ ov, uchar4* __restrict__ out,
             const int W, const int H, const int w, const int h, const float k, const float shade)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float4 o = ns_bilin4(ov, (x + 0.5f) * w / W, (y + 0.5f) * h / H, w, h);
    const uchar4 s = src[y * W + x];
    const float kr = k * (1.f / 255.f);
    const float dk = 1.f - shade * o.w * kr;
    const float r = s.x * dk, gg = s.y * dk, b = s.z * dk;
    out[y * W + x] = pack(make_float4(r + (255.f - r) * o.x * kr, gg + (255.f - gg) * o.y * kr,
                                      b + (255.f - b) * o.z * kr, 255.f));
}
"""


def _ease_amount(x: float) -> float:
    """0 -> 0 (the input exactly), 1 -> 1; eases out so a half amount already reads clearly."""
    x = min(max(x, 0.0), 1.0)
    return x * (2.0 - x)


def _tau_sus(pitch: np.ndarray) -> np.ndarray:
    return np.maximum(TAU_MIN_S, TAU_A0_S * np.power(0.5, (pitch - 21.0) / TAU_HALVING))


_TAU = _tau_sus(np.arange(128, dtype=np.float64)).astype(np.float32)


class NoteStream(Effect):
    name = "note_stream"
    stage = "overlay"
    label = "Note stream"
    description = ("The piano's notes as glowing streaks flowing across the picture like a living piano roll: "
                   "pitch picks the lane, a held note stretches into a long streak and floats off on key-up, "
                   "lightly warped and optionally tilted into depth.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "travel": num(0.04, 1.0, 0.22, "Travel speed", unit="screens/s", tier="regional"),
        "angle": num(0.0, 315.0, 90.0, "Direction", unit="°", step=1.0, tier="regional", basic=True),
        "warp": num(0.0, 1.0, 0.25, "Warp", tier="regional", basic=True),
        "depth": num(0.0, 1.0, 0.3, "Depth (flat · tilted into 3D)", tier="regional"),
        "spread": num(0.3, 1.2, 0.92, "Spread (keyboard width)", tier="regional"),
        "width": num(3.0, 36.0, 16.0, "Streak width", unit="px", step=0.5, tier="regional"),
        "glow": num(0.0, 1.5, 0.7, "Glow", tier="regional"),
        "trail": num(0.0, 0.25, 0.07, "Trail", unit="screens", step=0.005, tier="regional"),
        "linger": num(0.5, 8.0, 4.0, "Fade after release", unit="s", step=0.1, tier="regional"),
        "opacity": num(0.0, 1.0, 0.85, "Opacity", tier="regional"),
        "hue": num(0.0, 0.75, 0.0, "Hue shift", tier="regional"),
        "mirror": flag(False, "Mirror the keyboard (low notes on the other side)", tier="structural"),
        **palette_params("neon", period=0.8, drift=0.02),
        "render_scale": num(0.25, 1.0, 0.5, "Render scale", step=0.05, tier="quality"),
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kd = mod.get_function("ns_draw")
        self._kc = mod.get_function("ns_comp")
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.ov = cp.zeros((self.h, self.w, 4), cp.uint8)
        # one upload per frame: params | streaks per lane | occupied-lane prefix | the lane table
        nb = NP + 192 + N_LANE * K * NS
        self.B = cp.zeros(nb, cp.float32)
        self.Hb = np.zeros(nb, np.float32)
        self.Ph = self.Hb[:NP]
        self.Ch = self.Hb[NP:NP + N_LANE]
        self.Pre = self.Hb[NP + 96:NP + 96 + N_LANE + 1]
        self.Th = self.Hb[NP + 192:NP + 192 + N_LANE * K * NS].reshape(N_LANE, K, NS)
        # the event ring (host)
        n = N_EVENTS
        self.alive = np.zeros(n, bool)
        self.pitch = np.zeros(n, np.int32)
        self.t_on = np.zeros(n, np.float64)
        self.t_off = np.full(n, np.nan)
        self.vel = np.zeros(n, np.float32)
        self.d_head = np.zeros(n, np.float32)
        self.d_tail = np.zeros(n, np.float32)
        self.bloom = np.zeros(n, np.float32)
        self.glow = np.zeros(n, np.float32)
        self.vis = np.ones(n, np.float32)
        self.open_idx = np.full(128, -1, np.int64)      # the event of a key that is down
        self.last_idx = np.full(128, -1, np.int64)      # the newest event of each pitch (it carries the sound)
        self.seed = np.uint32(int(params.get("seed", 1)) & 0xFFFFFFFF)
        self.q = float(int(self.seed) & 1023) * 0.6180339
        self.t_ms = None
        self.frame_t = None
        self.drift = 0.0
        self.ph_s = 0.0
        self.ph_t = 0.0
        self.kick = 0.0
        self.travel = float(params.get("travel", 0.22))

    # -- palette (the session's shared palette when the colour layer takes over, as spark_overlay) --------------
    def _palette(self, name: str):
        mod = getattr(self, "modulation", None)
        rows = None
        if mod is not None:
            try:
                rows = mod.palette_rows()
            except Exception:
                rows = None
        return np.asarray(PALETTES[name] if rows is None else rows, np.float32).reshape(-1)[:12]

    # -- the event ring -----------------------------------------------------------------------------------------
    def _slot(self) -> int:
        free = np.flatnonzero(~self.alive)
        if free.size:
            return int(free[0])
        # full: drop the note released longest ago (the faintest, farthest gone); a held key only if all are held
        rel = np.where(self.alive & ~np.isnan(self.t_off), self.t_off, np.inf)
        i = int(np.argmin(rel))
        if not math.isfinite(float(rel[i])):
            i = int(np.argmin(np.where(self.alive, self.t_on, np.inf)))
        self._kill(i)
        return i

    def _kill(self, i: int) -> None:
        self.alive[i] = False
        p = self.pitch[i]
        if self.open_idx[p] == i:
            self.open_idx[p] = -1
        if self.last_idx[p] == i:
            self.last_idx[p] = -1

    def _close(self, i: int, age_s: float) -> None:
        age_s = max(0.0, age_s)
        self.t_off[i] = max(self.t_on[i], self.t_ms - age_s * 1000.0)
        self.d_tail[i] = min(self.d_head[i], self.travel * age_s)
        if self.open_idx[self.pitch[i]] == i:
            self.open_idx[self.pitch[i]] = -1

    def _strike(self, p: int, vel: float, age_s: float) -> None:
        age_s = max(0.0, age_s)
        o = int(self.open_idx[p])
        if o >= 0 and self.alive[o]:
            self._close(o, age_s)                       # re-struck while held: the old streak ends here
        i = self._slot()
        self.alive[i] = True
        self.pitch[i] = p
        self.t_on[i] = self.t_ms - age_s * 1000.0
        self.t_off[i] = np.nan
        self.vel[i] = min(max(vel, 0.0), 1.0) if math.isfinite(vel) else 0.5
        self.d_head[i] = self.travel * age_s
        self.d_tail[i] = 0.0
        self.bloom[i] = 0.0
        self.glow[i] = 0.0
        self.vis[i] = 1.0
        self.open_idx[p] = i
        self.last_idx[p] = i

    def _reset(self) -> None:
        self.alive[:] = False
        self.open_idx[:] = -1
        self.last_idx[:] = -1

    def _listen(self, dt: float) -> np.ndarray | None:
        """Advance the clock and fold this frame's notes into the ring; returns the notes table (or None)."""
        snd = getattr(self, "sound", None)
        f = getattr(snd, "frame", None) if snd is not None else None
        t = getattr(f, "t_ms", None) if f is not None else None
        if t is None or not math.isfinite(float(t)):
            self.t_ms = (self.t_ms or 0.0) + max(dt, 0.0) * 1000.0
            for q in np.flatnonzero(self.open_idx >= 0):        # the sound went away: let go of held keys
                self._close(int(self.open_idx[q]), 0.0)
            return None
        t = float(t)
        if self.t_ms is not None and (t < self.t_ms - 1000.0 or t > self.t_ms + 60000.0):
            self._reset()                               # a new clock (session restart, a loop): start clean
            self.t_ms = None                            # ... on the new clock (else every frame resets again)
        self.t_ms = t if self.t_ms is None else max(t, self.t_ms)
        notes = getattr(f, "notes", None)
        if self.frame_t == t:                           # the same sound frame again (prime, a second render)
            return notes
        self.frame_t = t
        for ev in getattr(f, "onsets", ()) or ():
            try:
                p, vel, age = int(ev[0]), float(ev[1]), float(ev[2])
            except (TypeError, ValueError, IndexError):
                continue
            if LOW <= p <= HIGH and math.isfinite(vel) and math.isfinite(age):
                self._strike(p, vel, age)
        for ev in getattr(f, "releases", ()) or ():
            try:
                p, age = int(ev[0]), float(ev[2])
            except (TypeError, ValueError, IndexError):
                continue
            if LOW <= p <= HIGH and self.open_idx[p] >= 0 and math.isfinite(age):
                self._close(int(self.open_idx[p]), age)
        if notes is not None and getattr(notes, "shape", None) == (128, 4):
            # heal against the table: a key down that we never saw struck, or one that went up unseen
            held = notes[:, 3] < 0.0
            for p in np.flatnonzero(held[LOW:HIGH + 1] & (self.open_idx[LOW:HIGH + 1] < 0)) + LOW:
                a = float(notes[p, 2])
                self._strike(int(p), float(notes[p, 1]), a if math.isfinite(a) else 0.0)
            for p in np.flatnonzero(~held[LOW:HIGH + 1] & (self.open_idx[LOW:HIGH + 1] >= 0)) + LOW:
                a = float(notes[p, 3])
                self._close(int(self.open_idx[p]), a if math.isfinite(a) else 0.0)
            return notes
        return None

    # -- render -------------------------------------------------------------------------------------------------
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        dt = max(float(dt), 0.0)
        dtc = min(dt, 1.0)
        amt = min(max(float(p["amount"]), 0.0), 1.0)
        m = _ease_amount(amt)
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        self.travel = trav = float(p["travel"])
        linger = max(float(p["linger"]), 0.05)

        # geometry of the stream this frame
        th = math.radians(float(p["angle"]))
        dx, dy = math.cos(th), math.sin(th)
        nx, ny = (dy, -dx) if not p.get("mirror") else (-dy, dx)
        A = self.w / self.h
        span_d = abs(dx) * A + abs(dy)
        span_n = abs(nx) * A + abs(ny)
        phi = MAX_TILT * min(max(float(p["depth"]), 0.0), 1.0)
        cph, sph = math.cos(phi), math.sin(phi)
        flat = phi < 1e-3
        yb = -0.5 * span_d - 0.02                        # the edge the notes come from (just outside the frame)
        yf = 0.5 * span_d
        if flat:
            s0, z0, s_far = yb, CAM_D, yf
        else:
            s0 = yb * CAM_D / (CAM_D * cph - yb * sph)
            z0 = CAM_D + s0 * sph
            den = CAM_D * cph - yf * sph
            s_far = yf * CAM_D / den if den > 0.05 else 30.0
        d_max = (s_far - s0) + 0.25 + 5.0 * max(float(p["trail"]), 0.0)   # the comet trail leaves the frame too

        notes = self._listen(dt)
        idx = np.flatnonzero(self.alive)
        if idx.size:
            # integrate the travel (a speed change never moves a note), ease bloom / glow / visibility
            t_off = self.t_off[idx]
            closed = ~np.isnan(t_off)
            dh = self.d_head[idx] + trav * dtc
            dtl = np.minimum(self.d_tail[idx] + np.where(closed, trav * dtc, 0.0), dh)
            self.d_head[idx] = dh
            self.d_tail[idx] = dtl
            a_off = np.where(closed, (self.t_ms - np.where(closed, t_off, 0.0)) / 1000.0, 0.0)
            fade = np.exp(-np.maximum(a_off, 0.0) / linger)
            if dt > 0.0:
                pit = self.pitch[idx]
                lvl = np.zeros(idx.size, np.float32)
                if notes is not None:
                    cur = self.last_idx[pit] == idx
                    lvl[cur] = np.clip(np.nan_to_num(notes[pit[cur], 0], nan=0.0), 0.0, 1.0)
                self.bloom[idx] += (1.0 - self.bloom[idx]) * (1.0 - math.exp(-dt / 0.085))
                self.glow[idx] += (lvl - self.glow[idx]) * (1.0 - math.exp(-dt / 0.12))
            dead = (closed & (fade < 0.004)) | (dtl > d_max) | ((self.t_ms - self.t_on[idx]) > 600000.0)
            if dead.any():
                for i in idx[dead]:
                    self._kill(int(i))
                idx, fade = idx[~dead], fade[~dead]
            # bin into pitch lanes, newest first; the two oldest slots of a full lane fade before it overflows
            srt = np.lexsort((-self.t_on[idx], self.pitch[idx]))
            order, fade = idx[srt], fade[srt]
            ps = self.pitch[order]
            start = np.r_[True, ps[1:] != ps[:-1]]
            ar = np.arange(order.size)
            rank = ar - np.maximum.accumulate(np.where(start, ar, 0))
            over = rank >= K
            for i in order[over]:
                self._kill(int(i))                      # (only ever the oldest of a lane, already faded)
            if over.any():
                keep = ~over
                order, rank, ps, fade = order[keep], rank[keep], ps[keep], fade[keep]
            if dt > 0.0:
                tv = np.where(rank < K - 2, 1.0, 0.0)
                self.vis[order] += (tv - self.vis[order]) * (1.0 - math.exp(-dt / 0.15))
        else:
            order = np.zeros(0, np.int64)

        self.drift += float(p["drift_speed"]) * dtc
        self.ph_s += 0.72 * trav * dtc
        self.ph_t += 0.11 * dtc
        if dt > 0.0:
            ak = 1.0 - math.exp(-dt / (0.05 if pulse > self.kick else 0.18))
            self.kick += (pulse - self.kick) * ak

        if amt <= 1e-4 or order.size == 0:
            cp.copyto(out, src)
            return

        # the lane table
        T = self.Th
        T.fill(0.0)
        lane = ps - LOW
        v = self.vel[order]
        b = self.bloom[order] * self.vis[order] * fade
        gl = self.glow[order]
        span = float(p["spread"]) * span_n * (z0 / CAM_D)
        ls = span / (N_LANE - 1)
        r_cap = 2.5 * ls                                    # the farthest a pixel looks: 3 lanes either side
        # the widest streak (x1.38 at full velocity and level) keeps room for its edge and halo inside that reach,
        # so a wide streak on a narrow keyboard is never clipped by the lane window (no hard seams)
        hw_ref = min(0.5 * float(p["width"]) / 1080.0, 0.6 * r_cap / 1.38)
        a_on = (self.t_ms - self.t_on[order]) / 1000.0
        T[lane, rank, 0] = self.d_tail[order]
        T[lane, rank, 1] = self.d_head[order]
        T[lane, rank, 2] = hw_ref * (0.45 + 0.75 * v) * (1.0 + 0.15 * gl)
        T[lane, rank, 3] = b * (0.55 + 0.45 * v) * (0.85 + 0.3 * gl)
        T[lane, rank, 4] = b * float(p["glow"]) * (0.6 + 0.5 * v + 1.0 * gl)
        T[lane, rank, 5] = b * (0.35 + 0.9 * v * np.exp(-np.maximum(a_on, 0.0) / 1.2))
        T[lane, rank, 6] = 1.0 / np.maximum(trav * _TAU[ps], 1e-3)
        T[lane, rank, 7] = np.where(np.isnan(self.t_off[order]), 0.0, 1.6 * b * (0.5 + 0.5 * v))
        self.Ch[:] = np.bincount(lane, minlength=N_LANE)
        self.Pre[0] = 0.0
        np.cumsum(self.Ch > 0, out=self.Pre[1:])

        gr0 = 0.003 + 0.016 * float(p["glow"])
        gr = gr0 * (1.0 + 0.25 * self.kick)
        # the glow's reach: continuous (eases with glow / width / spread) and sized for a full kick, so the pulse
        # never changes it; R only says how many lanes cover it (lanes beyond the reach are skipped anyway)
        reach = min(hw_ref * 1.38 + 6.0 * 1.25 * gr0, r_cap)
        R = int(min(3, max(1, math.ceil(reach / ls + 0.5))))
        Ph = self.Ph
        Ph[0], Ph[1], Ph[2] = self.w, self.h, A
        Ph[3], Ph[4], Ph[5], Ph[6] = dx, dy, nx, ny
        Ph[7], Ph[8], Ph[9] = CAM_D, cph, sph
        Ph[10], Ph[11] = s0, z0
        Ph[12], Ph[13] = span, ls
        Ph[14], Ph[15] = R, reach
        Ph[16] = 0.06 * float(p["warp"])
        Ph[19] = gr
        Ph[20] = 0.22
        Ph[21] = float(p["trail"])
        Ph[22] = 1.0 + 0.45 * self.kick
        Ph[23] = 2.1
        Ph[24] = float(p["palette_period"])
        Ph[26] = 1.1
        Ph[28] = 1.0 if flat else 0.0
        pc = self._palette(p["palette"])
        Ph[29:38] = pc[0:9]
        cph_ = self.drift + float(p["hue"])                 # colour phase, reduced per channel in float64
        for i in range(3):
            Ph[38 + i] = math.fmod(float(pc[6 + i]) * cph_ + float(pc[9 + i]), 1.0)
        Ph[41] = R + math.ceil(1.45 * Ph[16] / ls) + 1
        q, ps_, pt_ = self.q, self.ph_s, self.ph_t          # warp phases: k x (-ride) + m x drift + seed
        for i, (ks_, mt_, jq_) in enumerate(((2.3, 1.3, 1.0), (4.7, -2.1, 2.0), (7.9, 3.3, 3.0),
                                            (2.9, 1.7, 5.0), (5.3, -2.6, 7.0))):
            Ph[42 + i] = math.fmod(-ks_ * ps_ + mt_ * pt_ + jq_ * q, 6.283185307179586)
        self.B.set(self.Hb)
        launch_2d(self._kd, self.w, self.h, (self.ov, self.B, self.seed))
        W, H = self.ctx.width, self.ctx.height
        k = m * min(max(float(p["opacity"]), 0.0), 1.0)
        launch_2d(self._kc, W, H, (src, self.ov, out, np.int32(W), np.int32(H), np.int32(self.w), np.int32(self.h),
                                   np.float32(k), np.float32(0.42)))

    def close(self) -> None:
        self.ov = self.B = None
