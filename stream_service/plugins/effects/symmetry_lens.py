"""Symmetry lens: the picture takes on the symmetry of the chord being played.

Source: Original (inspired by the theory in
        https://en.wikipedia.org/wiki/Mode_of_limited_transposition,
        https://mtosmt.org/issues/mto.17.23.3/mto.17.23.3.yust.html (Yust, "Applications of DFT to the theory of
        twentieth-century harmony"), https://arxiv.org/pdf/2008.11936, https://people.bu.edu/jyust/parfumsVer2.pdf,
        https://infoscience.epfl.ch/record/291958 (Amiot, Music Through Fourier Space))
Author: smfx (original)
License: MIT (original)
license_class: permissive

WHAT YOU SEE
Over any scene or art still, a round lens opens from the middle of the screen whenever the piano plays a chord that
is symmetric (the clearer the symmetry, and the higher amount, the wider it opens: fold_radius at full), and the
picture inside it folds into a pinwheel with the chord's symmetry; outside it the scene is untouched:
  - an augmented triad (C-E-G#) folds it three ways,
  - a diminished seventh (C-Eb-F#-A) or Messiaen's octatonic scale four ways,
  - a whole-tone run (Debussy's Voiles) six ways,
  - a bare tritone (C-F#) or a French sixth two ways.
Ordinary tonal harmony (major and minor triads, scales, a melody) is not symmetric, and the lens stays shut: the
scene passes through untouched.  This is deliberate and honest.  The lens opens exactly at the ambiguous pivot
chords composers use to slip between keys, and closes again when the music lands.
  - Moving a symmetric chord up one semitone turns the pinwheel 30 degrees; a diminished seventh moved up a minor
    third is the same chord, and the picture is the same too (a quarter turn of a four-fold pattern).
  - A key change turns the whole lens (key_spin): up a fifth = 30 degrees round, eased over 1.5 s.
  - A chromatic cluster (C-C#-D-D#) smears the scene sideways a little, in a direction set by the cluster's pitch.
  - Each new note gives the folded content a little spin (a rate kick; nothing jumps), and the damper pedal makes
    the fold linger (its fade-out doubles from 1.2 s to 2.4 s).

THE THEORY (all exact; verified numbers in tonal.py's tests)
Take the chord's pitch-class profile c[0..11] (how loud each of the twelve notes is, octaves merged) and its
discrete Fourier transform F_k = sum_n c_n exp(-2 pi i k n / 12).  A chord that maps onto itself when transposed
by 12/n semitones has F_k = 0 for every k that n does not divide (transposing by t multiplies F_k by
exp(-2 pi i k t / 12)).  So the share of the profile's (non-constant) energy |F_k|^2 that sits on multiples of n,
s_n, measures n-fold symmetry; a random profile would put (12/n - 1)/11 of it there, and
sigma_n = (s_n - chance) / (1 - chance), clamped to 0..1, is 0 for no symmetry and 1 for perfect symmetry.
Triads score sigma_3 0.19, the dominant seventh 0.31 (two-fold), the diminished triad 0.6, the diminished seventh,
the augmented triad, the whole-tone scale and the tritone 1.  A gate (sensitivity) keeps triads out; the highest
order wins (whole-tone shows 6, not 2 and 3 as well).  The fold's angle is -arg(F_n)/n: it turns by exactly 30
degrees per semitone of transposition, and is smoothed as a complex number (never as an angle, so a coefficient
passing near zero fades instead of spinning).  The key's turn is arg F_5 of an 8 s average of the profile: F_5
lines the keys up round the circle of fifths.  The cluster smear is |F_1| (pitches bunched together on the
chromatic circle, with the single-note case taken out by 1 - |F_5|), in direction arg F_1.

The fold is a ROTATIONAL one (a pinwheel, not mirror kaleidoscope), because transposition is rotation of the pitch
circle, not reflection: angle' = mod(angle - psi, 2 pi / n), with a 5 degree cross-fade at each seam.  style blends
from that crisp pinwheel to the orbit average (the mean of the picture turned by 2 pi j / n, j < n: the exact
projection of the image onto its n-fold-symmetric part, the picture's own "F_n"), a soft halo.

GPU: sl_fold renders the folded lens at render_scale (default 0.5: 1080p for a 4K frame; 1-2 bilinear taps of the
full-size scene per active order for the crisp fold, n for the halo; texels outside the lens are skipped), then
sl_comp lays it over the full-size scene inside the lens's soft rim (one bilinear tap; outside the lens a copy, or a
3-tap smear during a cluster).  Nothing active (silence, plain tonal music, amount 0): a plain
copy.  Cost (P4000, 4K, 29 Oct, a shared card at ~1.24 GHz): fxtest --bench 1.34 ms at the defaults (a full
four-fold lens, crisp); the halo adds n nearest taps per texel (style 0.5 ~1.7-2.0 ms, so the mandala preset renders
its fold at 0.4); a plain copy when nothing is folded.  Host: ~0.2 ms of numpy on 12-vectors per frame (on a loaded
CPU) and a 24-float upload.  No feedback buffers; dt = 0 changes
nothing; NaN or negative dt is treated as 0 and a huge dt settles.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib import tonal as T
from stream_service.plugins._lib.beat import paced
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params
from stream_service.plugins._lib.upsample import internal_size

ORDERS = (2, 3, 4, 6)                 # T.SYMMETRY_ORDERS
GATE_W = 0.5                          # the gate's width above sensitivity
ATTACK_S, RELEASE_S, DAMPER_RELEASE_S = 0.12, 1.2, 2.4
PHASE_TAU_S = 0.2                     # complex EMA of F_n (and F_1)
KEY_TAU_S, KEY_EASE_S = 8.0, 1.5      # key: the profile's long average, the turn's easing
SEAM_RAD = math.radians(5.0)
SMEAR_H = 0.05                        # full smear: x frame height (px along the cluster's direction)
KICK_TAU_S = 0.5                      # onset spin kick decay
KICK_RAD_S = math.radians(14.0)       # spin added by a full-velocity onset
NP = 24
# P layout (floats)
#  0 cx px | 1 cy px | 2 R (H units) | 3 style | 4 seam rad | 5 content angle A | 6 sample radius scale
#  7 smear dx px | 8 smear dy px | 9 smear on | 10..13 share of the lens (n = 2, 3, 4, 6, sum 1)
#  14..17 phi (display angle per order) | 18 lens opacity
_SRC = HELPERS + r"""
#define SL_TAU 6.28318531f

__device__ __forceinline__ float sl_reflect(float x, float n) {   // mirror-repeat into [0, n]
    if (x >= 0.f && x < n) return x;                     // the common case
    const float p = 2.f * n;
    x = x - p * floorf(x / p);
    return x < n ? x : p - x;
}

// the source at polar (rr px, angle a) about the centre, mirror-repeated off the frame
__device__ __forceinline__ float4 sl_tap(const uchar4* __restrict__ src, int W, int H, float cx, float cy,
                                         float rr, float a) {
    float sn, cs;
    __sincosf(a, &sn, &cs);
    return bilin(src, sl_reflect(cx + rr * cs, (float)W), sl_reflect(cy + rr * sn, (float)H), W, H);
}

// the source at (cx + rr cs, cy + rr sn), nearest texel (the halo averages n of them, which hides the difference
// at a quarter of the cost)
__device__ __forceinline__ float4 sl_tap_near(const uchar4* __restrict__ src, int W, int H, float cx, float cy,
                                              float rr, float cs, float sn) {
    const int ix = min(max((int)sl_reflect(cx + rr * cs, (float)W), 0), W - 1);
    const int iy = min(max((int)sl_reflect(cy + rr * sn, (float)H), 0), H - 1);
    const uchar4 p = __ldg(src + iy * W + ix);
    return make_float4(p.x, p.y, p.z, 255.f);
}

// The fold at the internal size (render_scale): each active order's pinwheel, mixed by its share of the lens.
// Only texels inside the lens (plus a 2-texel margin for the composite's bilinear taps) are written.
extern "C" __global__
void sl_fold(const uchar4* __restrict__ src, uchar4* __restrict__ fold, const int W, const int H, const int w,
             const int h, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= w || y >= h) return;
    const float cx = P[0], cy = P[1], R = P[2];
    const float X = (x + 0.5f) * ((float)W / (float)w), Y = (y + 0.5f) * ((float)H / (float)h);
    const float invH = 1.f / (float)H;
    const float dx = (X - cx) * invH, dy = (Y - cy) * invH;
    const float r = sqrtf(dx * dx + dy * dy);
    if (!(R > 0.f) || r > R + 2.f / (float)h) return;
    const float ang = atan2f(dy, dx);
    const float rr = r * P[6] * (float)H;
    const float style = clamp01(P[3]), seam = P[4], A = P[5];
    float ax = 0.f, ay = 0.f, az = 0.f, wsum = 0.f;
    #pragma unroll
    for (int i = 0; i < 4; ++i) {                         // bounded: the four symmetry orders
        const float wi = P[10 + i];                       // this order's share of the lens
        if (!(wi > 0.002f)) continue;
        const int n = i == 0 ? 2 : (i == 1 ? 3 : (i == 2 ? 4 : 6));
        const float wd = SL_TAU / (float)n;
        const float ph = ang - P[14 + i];                 // the angle in the pattern's frame
        float4 f = make_float4(0.f, 0.f, 0.f, 255.f);
        if (style < 0.995f) {                             // crisp pinwheel: one wedge, turned n times
            const float d = ph - wd * floorf(ph / wd);    // [0, wd)
            float4 c = sl_tap(src, W, H, cx, cy, rr, A + d);
            if (d < seam)                                 // seam: continue the previous wedge, cross-faded
                c = mix4(sl_tap(src, W, H, cx, cy, rr, A + d + wd), c, smooth(0.f, seam, d));
            f = c;
        }
        if (style > 0.005f) {                             // orbit average: the image's n-fold-symmetric part
            float hx = 0.f, hy = 0.f, hz = 0.f, cs, sn, cw, sw;
            __sincosf(A + ph, &sn, &cs);
            __sincosf(wd, &sw, &cw);
            for (int j = 0; j < n; ++j) {                 // bounded: n <= 6; each tap turned a further 2 pi / n
                const float4 t = sl_tap_near(src, W, H, cx, cy, rr, cs, sn);
                hx += t.x; hy += t.y; hz += t.z;
                const float c2 = cs * cw - sn * sw;
                sn = sn * cw + cs * sw;
                cs = c2;
            }
            const float inv = 1.f / (float)n;
            const float4 hh = make_float4(hx * inv, hy * inv, hz * inv, 255.f);
            f = style < 0.995f ? mix4(f, hh, style) : hh;
        }
        ax += wi * f.x; ay += wi * f.y; az += wi * f.z; wsum += wi;
    }
    const float iw = wsum > 1e-6f ? 1.f / wsum : 0.f;
    fold[y * w + x] = pack(make_float4(ax * iw, ay * iw, az * iw, 255.f));
}

// Full size: the scene (smeared by a cluster), with the fold laid over it inside the lens (soft rim).
template <bool SMEAR>
__device__ __forceinline__ void sl_comp_t(const uchar4* __restrict__ src, const uchar4* __restrict__ fold, uchar4* __restrict__ out,
             const int W, const int H, const int w, const int h, const float* __restrict__ P, const int x, const int y)
{
    float4 s;
    if (SMEAR) {                                          // cluster smear: 3 taps back along the direction
        const float sx = P[7], sy = P[8];
        const float4 a = ld(src, x, y, W, H);
        const float4 b = bilin(src, sl_reflect(x + 0.5f - sx, (float)W), sl_reflect(y + 0.5f - sy, (float)H), W, H);
        const float4 c = bilin(src, sl_reflect(x + 0.5f - 2.f * sx, (float)W),
                               sl_reflect(y + 0.5f - 2.f * sy, (float)H), W, H);
        s = make_float4(0.5f * a.x + 0.3f * b.x + 0.2f * c.x, 0.5f * a.y + 0.3f * b.y + 0.2f * c.y,
                        0.5f * a.z + 0.3f * b.z + 0.2f * c.z, 255.f);
    } else {
        const uchar4 p = __ldg(src + y * W + x);
        s = make_float4(p.x, p.y, p.z, 255.f);
    }
    const float R = P[2];
    const float invH = 1.f / (float)H;
    const float dx = (x + 0.5f - P[0]) * invH, dy = (y + 0.5f - P[1]) * invH;
    const float r = sqrtf(dx * dx + dy * dy);
    const float m = R > 0.f ? P[18] * (1.f - smooth(0.7f * R, R, r)) : 0.f;   // the lens: opacity x a soft rim
    if (!(m > 0.f)) { out[y * W + x] = pack(s); return; }
    const float fx = fminf(fmaxf((x + 0.5f) * ((float)w / (float)W) - 0.5f, 0.f), (float)(w - 1));
    const float fy = fminf(fmaxf((y + 0.5f) * ((float)h / (float)H) - 0.5f, 0.f), (float)(h - 1));
    const int x0 = (int)fx, y0 = (int)fy, x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const float tx = fx - x0, ty = fy - y0, u = 1.f - tx, v = 1.f - ty;
    const uchar4 a = __ldg(fold + y0 * w + x0), b = __ldg(fold + y0 * w + x1);
    const uchar4 c = __ldg(fold + y1 * w + x0), d = __ldg(fold + y1 * w + x1);
    const float4 f = make_float4(v * (u * a.x + tx * b.x) + ty * (u * c.x + tx * d.x),
                                 v * (u * a.y + tx * b.y) + ty * (u * c.y + tx * d.y),
                                 v * (u * a.z + tx * b.z) + ty * (u * c.z + tx * d.z), 255.f);
    out[y * W + x] = pack(mix4(s, f, fminf(m, 1.f)));
}

// two instantiations, so the plain one carries none of the smear's registers
extern "C" __global__
void sl_comp(const uchar4* __restrict__ src, const uchar4* __restrict__ fold, uchar4* __restrict__ out,
             const int W, const int H, const int w, const int h, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    sl_comp_t<false>(src, fold, out, W, H, w, h, P, x, y);
}

extern "C" __global__
void sl_comp_smear(const uchar4* __restrict__ src, const uchar4* __restrict__ fold, uchar4* __restrict__ out,
                   const int W, const int H, const int w, const int h, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    sl_comp_t<true>(src, fold, out, W, H, w, h, P, x, y);
}
"""


def _wrap(a: float) -> float:
    """An angle into (-pi, pi]."""
    return a - 2.0 * math.pi * math.floor((a + math.pi) / (2.0 * math.pi))


def _fin(x: float, d: float = 0.0) -> float:
    x = float(x)
    return x if math.isfinite(x) else d


class SymmetryLens(Effect):
    name = "symmetry_lens"
    stage = "warp"
    label = "Symmetry lens"
    description = ("Any scene takes on the symmetry of the chord being played: an augmented triad folds it three "
                   "ways, a diminished seventh four, a whole-tone run six, a tritone two, while ordinary tonal "
                   "harmony leaves it alone.")
    listens = ("notes",)
    params = {
        **standard_effect_params(),
        "sensitivity": num(0.1, 0.6, 0.3, "Symmetry threshold (low: folds more often)", tier="regional"),
        "fold_radius": num(0.4, 1.6, 1.1, "Lens size", step=0.01, tier="regional", basic=True),
        "style": num(0.0, 1.0, 0.0, "Style (crisp pinwheel · soft halo)", tier="regional"),
        "zoom": num(0.6, 1.4, 0.9, "Content zoom", step=0.01, unit="×", tier="regional"),
        "turn_speed": paced(num(-30.0, 30.0, 5.0, "Turn speed", step=0.5, unit="°/s", tier="regional",
                                bind="rate"), 360),
        "smear": num(0.0, 1.0, 0.5, "Cluster smear", tier="regional"),
        "key_spin": num(0.0, 1.0, 0.5, "Key spin (turn with the key)", tier="regional"),
        "render_scale": num(0.25, 1.0, 0.5, "Fold render scale", step=0.05, tier="quality"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC, ("--std=c++14", "--use_fast_math"))
        self._kf = mod.get_function("sl_fold")
        self._kc = mod.get_function("sl_comp")
        self._kcs = mod.get_function("sl_comp_smear")
        self.w, self.h = internal_size(ctx, float(params.get("render_scale", 0.5)))
        self.fold = cp.zeros((self.h, self.w, 4), cp.uint8)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.e = np.zeros(12, np.float32)                 # pitch-class energy this frame (from LEVEL)
        self.ease = T.AttackRelease(12, 0.12, 0.4)
        self.F = np.zeros(7, np.complex128)
        self.sig = np.zeros(4)
        self.Fs = np.zeros(4, np.complex128)              # smoothed F_n / sum c, n = 2, 3, 4, 6
        self.F1s = 0j                                     # smoothed F_1 / sum c (the smear's direction)
        self.env = np.zeros(4)                            # the orders' eased weights
        self.clu = 0.0                                    # eased cluster measure
        self.cl = np.zeros(12)                            # the profile's 8 s average (key)
        self.cl_started = False
        self.key_tgt = 0.0                                # unwrapped arg F_5 of the long average
        self.key_eased = 0.0
        self.rho = 0.0                                    # the lens's key turn (integrated, x key_spin)
        self.delta = 0.0                                  # the content's drift angle
        self.kick = 0.0
        self._seen = None
        self._rows5 = T.DFT12[5]
        self.Fn = np.zeros(4, np.complex128)
        self.psi = np.zeros(4)                            # the orders' fold angles (kept while F_n is ~0)

    # ------------------------------------------------------------------------------------------------ host side
    def _listen(self, p: dict, dt: float) -> None:
        snd = getattr(self, "sound", None)
        fr = getattr(snd, "frame", None) if snd is not None else None
        heard = bool(fr is not None and getattr(snd, "present", False))
        notes = getattr(fr, "notes", None) if heard else None
        if notes is not None:
            T.pc_energy(notes, self.e)
            np.nan_to_num(self.e, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            self.e.fill(0.0)
        damper = 0.0
        if heard:
            try:
                damper = _fin(fr.pedals[0])
            except (AttributeError, IndexError, TypeError):
                damper = 0.0
            ons = getattr(fr, "onsets", None)
            if ons is not None and ons is not self._seen:
                for ev in list(ons)[:16]:
                    try:
                        v = float(ev[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if math.isfinite(v):
                        self.kick = max(self.kick, min(max(v, 0.0), 1.0))
            self._seen = ons
        c = self.ease.step(self.e, dt)
        sc = float(c.sum())
        sig = self.sig
        Fn = self.Fn
        Fn.fill(0.0)
        f1 = 0j
        clu_t = 0.0
        if sc > 1e-4:
            F = T.pc_dft(c, self.F)
            T.symmetry(c, sig, F)
            # guards: a whisper of sound, a near-flat profile (noise) and bare dyads (a third is "a third of an
            # augmented triad") count for less; a tritone (two-fold) still passes the default gate
            ee = np.abs(F[1:]) ** 2
            q = float(2.0 * ee[:5].sum() + ee[5]) / (sc * sc)
            neff = sc * sc / max(float((c * c).sum()), 1e-12)
            loud = T.smoothstep(0.02, 0.12, sc)
            sig *= loud * T.smoothstep(0.12, 0.35, q) * (0.75 + 0.25 * T.smoothstep(2.0, 3.0, neff))
            inv = 1.0 / sc
            Fn[0], Fn[1], Fn[2], Fn[3] = F[2] * inv, F[3] * inv, F[4] * inv, F[6] * inv
            f1 = F[1] * inv
            clu_t = T.smoothstep(0.2, 0.6, abs(f1) * (1.0 - abs(F[5]) * inv)) * T.smoothstep(2.2, 3.2, neff) * loud
            # key: the long average's F_5 (circle of fifths), unwrapped and weighted by how clearly it points
            if not self.cl_started:
                self.cl[:] = c
                self.cl_started = True
            else:
                self.cl += (c - self.cl) * T.ema_alpha(dt, KEY_TAU_S)
        else:
            sig.fill(0.0)
            if self.cl_started:
                self.cl *= 1.0 - T.ema_alpha(dt, KEY_TAU_S)
        scl = float(self.cl.sum())
        if scl > 1e-3:
            f5 = complex(np.dot(self._rows5, self.cl))
            conf = T.smoothstep(0.12, 0.3, abs(f5) / scl)
            if conf > 0.0:
                self.key_tgt += conf * _wrap(math.atan2(f5.imag, f5.real) - self.key_tgt) \
                    * T.ema_alpha(dt, 0.25)
        # gates -> exclusive weights (the highest order wins) -> eased envelopes
        th = float(p["sensitivity"])
        g = [T.smoothstep(th, th + GATE_W, float(s)) for s in sig]          # n = 2, 3, 4, 6
        w = np.array([g[0] * (1.0 - g[2]) * (1.0 - g[3]), g[1] * (1.0 - g[3]), g[2] * (1.0 - g[3]), g[3]])
        rel = DAMPER_RELEASE_S if damper > 0.5 else RELEASE_S
        aa, ar = T.ema_alpha(dt, ATTACK_S), T.ema_alpha(dt, rel)
        d = w - self.env
        self.env += np.where(d > 0.0, d * aa, d * ar)
        a2 = T.ema_alpha(dt, PHASE_TAU_S)
        self.Fs += (Fn - self.Fs) * a2
        self.F1s += (f1 - self.F1s) * a2
        self.clu += (clu_t - self.clu) * (aa if clu_t > self.clu else T.ema_alpha(dt, 0.6))
        k_prev = self.key_eased
        self.key_eased += (self.key_tgt - self.key_eased) * T.ema_alpha(dt, KEY_EASE_S)
        self.rho = math.fmod(self.rho + float(p["key_spin"]) * (self.key_eased - k_prev), 2.0 * math.pi)
        self.kick *= math.exp(-dt / KICK_TAU_S) if dt > 0.0 else 1.0
        if not np.all(np.isfinite(self.env)) or not np.all(np.isfinite(self.Fs)):   # never let NaN stick
            self.env.fill(0.0)
            self.Fs.fill(0.0)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        dt = float(dt)
        dt = min(dt, 1.0) if dt > 0.0 and math.isfinite(dt) else 0.0
        self._listen(p, dt)
        W, H = self.ctx.width, self.ctx.height
        amount = min(max(_fin(p["amount"]), 0.0), 1.0)
        pulse = min(max(_fin(p["pulse"]), 0.0), 1.0)
        # the content's turn: integrated rate; the beat and each onset kick the rate, never the angle
        ts = math.radians(_fin(p["turn_speed"], 5.0))
        sgn = 1.0 if ts >= 0.0 else -1.0
        rate = ts * (1.0 + 2.0 * pulse) + sgn * (KICK_RAD_S * self.kick + math.radians(4.0) * pulse)
        self.delta = math.fmod(self.delta + rate * dt, 2.0 * math.pi)
        Wn = amount * self.env
        tot = float(Wn.sum())
        if tot > 1.0:
            Wn = Wn / tot
            tot = 1.0
        sm = amount * min(max(_fin(p["smear"]), 0.0), 1.0) * self.clu
        if tot < 0.005 and sm * SMEAR_H * H <= 1.5:
            self.ctx.cp.copyto(out, src)
            return
        h = self.host
        h[0] = 0.5 * W
        h[1] = 0.5 * H
        # the lens opens from the centre as the symmetry (x amount) grows: fully folded inside, a soft rim, the
        # scene untouched outside; a weak reading is a small, faint lens, never a full-frame double exposure
        h[2] = float(p["fold_radius"]) * (0.3 + 0.7 * math.sqrt(tot)) if tot >= 0.005 else 0.0
        h[18] = T.smoothstep(0.02, 0.5, tot)
        h[3] = float(p["style"])
        h[4] = SEAM_RAD
        h[5] = self.delta
        h[6] = 1.0 / max(float(p["zoom"]), 0.1)
        L = sm * SMEAR_H * H
        if L > 1.5:
            a = math.atan2(self.F1s.imag, self.F1s.real) + self.rho
            h[7], h[8], h[9] = L * math.cos(a), L * math.sin(a), 1.0
        else:
            h[7] = h[8] = h[9] = 0.0
        h[10:14] = Wn / tot if tot >= 0.005 else 0.0
        for i, n in enumerate(ORDERS):
            z = self.Fs[i]
            if abs(z) > 1e-6:
                self.psi[i] = -math.atan2(z.imag, z.real) / n
            h[14 + i] = math.fmod(self.psi[i] + self.rho, 2.0 * math.pi)
        self.P.set(h)
        iW, iH, w, h_ = np.int32(W), np.int32(H), np.int32(self.w), np.int32(self.h)
        if h[2] > 0.0:
            # square 8 x 8 blocks (a warp covers 8 x 4 texels): the fold's taps land in turned, scattered places,
            # and a compact warp footprint keeps them in few cache lines whatever the angle (32 x 1 rows did not)
            self._kf(((self.w + 7) // 8, (self.h + 7) // 8, 1), (8, 8, 1), (src, self.fold, iW, iH, w, h_, self.P))
        launch_2d(self._kcs if h[9] > 0.5 else self._kc, W, H, (src, self.fold, out, iW, iH, w, h_, self.P))
