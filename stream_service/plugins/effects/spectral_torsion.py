"""Spectral torsion: the picture is cut into 48 concentric rings, one per band of the room's FFT (bass outermost,
treble innermost).  Each ring is a little torsion pendulum on a spring: the music kicks it round, it swings back,
overshoots and settles, and because neighbouring rings are tied together the twist travels in and out across the
picture like a wave along a rope.  With no sound every ring is at rest and the scene passes through untouched.

Source: Original (inspired by MilkDrop's per-vertex rotation, https://www.geisswerks.com/milkdrop/milkdrop_preset_authoring.html,
        and Butterchurn, https://github.com/jberg/butterchurn, MIT; Synesthesia's audio-uniform advice,
        https://app.synesthesia.live/docs/ssf/audio_uniforms.html)
Author: smfx (screen-machine)
License: MIT
license_class: permissive

What you see
------------
Any scene underneath, twisted ring by ring around the centre of the screen.  A low note or a bass thump yanks the
outer rings (the bass lives at the rim), they spring back with a damped wobble; a treble chord twirls the inner
rings; a loud broadband hit shivers everything at once.  "Counter" chooses how the rings turn: all one way (a
whirlpool, the picture winds into a vortex) through to alternating neighbours (a combination lock, rings sliding
against each other).  "Ring edges" morphs from a smooth, liquid twist to sharp concentric cuts.  Trails leave a soft
light ghost wherever a ring is moving fast.

The theory
----------
The 48 rings form a damped torsional chain, a discretised wave equation along the radius:

    theta_b'' = -w0^2 theta_b - 2 zeta w0 theta_b' + c (theta_{b-1} + theta_{b+1} - 2 theta_b)
                + eta (theta'_{b-1} + theta'_{b+1} - 2 theta'_b) + s_b * force_b

w0 = 2 pi x springiness (0.4-1.2 Hz), zeta = 0.3, c = 40 x coupling (the spring between neighbours), eta a little
viscous coupling (so the fast "every other ring" mode is damped like the slow ones), free ends at both rims.
s_b = 1 - counter (1 - cos(pi b / g)) is each ring's direction: g = 1 is the plain alternation 1 - 2 counter
(b mod 2); "lock width" g up to 6 turns the rings in wider bands, which reads better from across a room.  Sound enters only as force or as an impulse on the angular
VELOCITY, never as an angle, so the motion is always smooth (the music pushes, the physics moves):
  * the room mic: spectral flux of each band, max(0, fft[now] - fft[67 ms ago]), a force (onsets, not loudness);
  * the piano's MIDI (photon-aligned, so it is preferred and the mic's share is lowered while the piano plays):
    each strike gives its band (b = 48 log(f0/40)/log 400) a velocity impulse midi_drive x velocity, spread over
    neighbouring bands by a Gaussian (sigma 1 band); notes still sounding lean their rings gently (a small steady
    torque from LEVEL), which springs back when the damper falls;
  * pulse (the beat envelope): raises the gain and adds a bass-weighted push (a rate, never a position).
Integration is semi-implicit Euler with up to 8 sub-steps per frame (stable at the stiffest coupling, dt = 0 does
nothing, a long stall is damped analytically).  The angle shown is soft-limited to pi/3 (tanh) and scaled by
amount.

GPU: one full-resolution pass (ring radius -> fractional band -> Catmull-Rom of the 48 angles, or a hard step
between bands, tapered to zero over 0.05 inside the innermost ring and 0.22 beyond the outermost, so the
twisted rim never shows a hard circular seam; rotate the sample point; one bilinear tap, mirrored at
the frame edges).  With trails, a half-resolution 16-bit feedback buffer holds an EMA of the twisted picture and a
"ghost" weight set by how fast each ring moves; the full pass lightens (with a touch of screen) toward it.  Rings
are circles on the 16:9 frame (radius in units of the half-diagonal).  Nothing moving and the ghosts gone: the
scene is copied untouched.  amount 0: the scene untouched.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.params import num, standard_effect_params

NB = 48                        # FFT bands = rings
R_IN = 0.08                    # innermost ring radius (half-diagonals)
MAX_TWIST = math.pi / 3.0      # soft limit of a ring's angle
ZETA = 0.3
LO_HZ, HI_HZ = 40.0, 16000.0
TWO_PI = 2.0 * math.pi

_SRC = HELPERS + r"""
#define NB 48
__device__ __forceinline__ float st_fold(float v, float n) {          // mirror at the frame edges
    v = fabsf(v);
    v = n - fabsf(n - v);
    return fminf(fmaxf(v, 0.f), n);
}
// Catmull-Rom through the 48 ring values at fractional band b (0..47), clamped ends.
__device__ __forceinline__ float st_cr(const float* __restrict__ T, float b) {
    int i = (int)floorf(b);
    i = min(max(i, 0), NB - 2);
    const float t = fminf(fmaxf(b - (float)i, 0.f), 1.f);
    const float p0 = T[max(i - 1, 0)], p1 = T[i], p2 = T[i + 1], p3 = T[min(i + 2, NB - 1)];
    const float t2 = t * t, t3 = t2 * t;
    return 0.5f * ((2.f * p1) + (-p0 + p2) * t + (2.f * p0 - 5.f * p1 + 4.f * p2 - p3) * t2
                   + (-p0 + 3.f * p1 - 3.f * p2 + p3) * t3);
}
// The ring field at radius r (half-diagonals): fractional band, its taper weight.
__device__ __forceinline__ float st_band(float r, float rin, float rout, float* w) {
    const float span = fmaxf(rout - rin, 1e-3f);
    float wt = 1.f;
    if (r < rin) wt = smooth(rin - 0.05f, rin, r);
    else if (r > rout) wt = 1.f - smooth(rout, rout + 0.22f, r);   // wide: no hard seam round the rim
    *w = wt;
    return fminf(fmaxf(47.f * (1.f - (r - rin) / span), 0.f), 47.f);
}
__device__ __forceinline__ float st_theta(const float* __restrict__ T, float b, float edges, float ew) {
    const float cr = st_cr(T, b);
    if (edges <= 0.f) return cr;
    int i = (int)floorf(b);
    i = min(max(i, 0), NB - 2);
    const float f = b - (float)i;
    const float s = smooth(0.5f - ew, 0.5f + ew, f);
    const float hard = T[i] + (T[i + 1] - T[i]) * s;
    return cr + (hard - cr) * edges;
}
__device__ __forceinline__ float4 st_bilin16(const ushort4* __restrict__ img, float fx, float fy, int W, int H) {
    fx -= 0.5f; fy -= 0.5f;
    int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
    const float tx = fx - x0, ty = fy - y0;
    const int x1 = min(max(x0 + 1, 0), W - 1), y1 = min(max(y0 + 1, 0), H - 1);
    x0 = min(max(x0, 0), W - 1); y0 = min(max(y0, 0), H - 1);
    const ushort4 a = img[y0 * W + x0], b = img[y0 * W + x1], c = img[y1 * W + x0], d = img[y1 * W + x1];
    const float u = 1.f - tx, v = 1.f - ty;
    const float wa = u * v, wb = tx * v, wc = u * ty, wd = tx * ty;
    return make_float4(wa * a.x + wb * b.x + wc * c.x + wd * d.x, wa * a.y + wb * b.y + wc * c.y + wd * d.y,
                       wa * a.z + wb * b.z + wc * c.z + wd * d.z, wa * a.w + wb * b.w + wc * c.w + wd * d.w);
}

// Full-size pass: twist the sample point by the ring angle, one bilinear tap; optional ghost lighten.
extern "C" __global__
void st_warp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float* __restrict__ T, const float cx, const float cy, const float inv_hd,
             const float rin, const float rout, const float edges, const float ew,
             const ushort4* __restrict__ G, const int GW, const int GH, const float gk)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float qx = x + 0.5f - cx, qy = y + 0.5f - cy;
    const float r = sqrtf(qx * qx + qy * qy) * inv_hd;
    float w;
    const float b = st_band(r, rin, rout, &w);
    float th = st_theta(T, b, edges, ew) * w;
    if (!isfinite(th)) th = 0.f;
    float4 o;
    if (fabsf(th) < 1e-5f) {
        const uchar4 p = src[y * W + x];
        o = make_float4(p.x, p.y, p.z, 255.f);
    } else {
        float s, c;
        __sincosf(th, &s, &c);
        const float sx = cx + c * qx + s * qy, sy = cy - s * qx + c * qy;    // sample at Rot(-theta) q
        o = bilin(src, st_fold(sx, (float)W), st_fold(sy, (float)H), W, H);
    }
    if (gk > 0.f) {
        const float4 g = st_bilin16(G, (x + 0.5f) * (float)GW / (float)W, (y + 0.5f) * (float)GH / (float)H, GW, GH);
        const float k = fminf(gk * g.w * (1.f / 65535.f), 1.f);
        if (k > 1e-4f) {
            const float sc = 255.f / 65535.f;
            const float gr = g.x * sc, gg = g.y * sc, gb = g.z * sc;
            // lighten, with a touch of screen (a soft light trail where the rings move)
            const float lr = fmaxf(o.x, gr), lg = fmaxf(o.y, gg), lb = fmaxf(o.z, gb);
            const float tr = lr + 0.3f * (o.x + gr - o.x * gr * (1.f / 255.f) - lr);
            const float tg = lg + 0.3f * (o.y + gg - o.y * gg * (1.f / 255.f) - lg);
            const float tb = lb + 0.3f * (o.z + gb - o.z * gb * (1.f / 255.f) - lb);
            o.x += (tr - o.x) * k; o.y += (tg - o.y) * k; o.z += (tb - o.z) * k;
        }
    }
    out[y * W + x] = pack(o);
}

// Half-size pass: the trail buffer.  rgb <- EMA of the twisted picture; a <- max(ring speed here, a * decay).
extern "C" __global__
void st_trail(const uchar4* __restrict__ src, ushort4* __restrict__ G, const int W, const int H, const int GW,
              const int GH, const float* __restrict__ T, const float cx, const float cy, const float inv_hd,
              const float rin, const float rout, const float edges, const float ew, const float decay,
              const int reset)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= GW || y >= GH) return;
    const float fx = (x + 0.5f) * (float)W / (float)GW, fy = (y + 0.5f) * (float)H / (float)GH;
    const float qx = fx - cx, qy = fy - cy;
    const float r = sqrtf(qx * qx + qy * qy) * inv_hd;
    float w;
    const float b = st_band(r, rin, rout, &w);
    float th = st_theta(T, b, edges, ew) * w;
    float om = st_cr(T + NB, b) * w;                         // angular speed (rad/s, amount-scaled)
    if (!isfinite(th)) th = 0.f;
    if (!isfinite(om)) om = 0.f;
    float s, c;
    __sincosf(th, &s, &c);
    const float sx = cx + c * qx + s * qy, sy = cy - s * qx + c * qy;
    const float4 v = bilin(src, st_fold(sx, (float)W), st_fold(sy, (float)H), W, H);
    const float m = smooth(0.03f, 0.45f, fabsf(om) * r);     // rim speed, half-diagonals per second
    const int i = y * GW + x;
    const ushort4 g = G[i];
    const float k = reset ? 0.f : decay;
    const float s16 = 65535.f / 255.f;
    const float nr = v.x * s16 + (g.x - v.x * s16) * k;
    const float ng = v.y * s16 + (g.y - v.y * s16) * k;
    const float nb = v.z * s16 + (g.z - v.z * s16) * k;
    const float na = fmaxf(m * 65535.f, (float)g.w * k);
    G[i] = make_ushort4((unsigned short)fminf(fmaxf(nr + 0.5f, 0.f), 65535.f),
                        (unsigned short)fminf(fmaxf(ng + 0.5f, 0.f), 65535.f),
                        (unsigned short)fminf(fmaxf(nb + 0.5f, 0.f), 65535.f),
                        (unsigned short)fminf(fmaxf(na + 0.5f, 0.f), 65535.f));
}
"""


def _clamp01(v) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return min(max(v, 0.0), 1.0)


def _band_of_pitch(p: np.ndarray) -> np.ndarray:
    """The fractional FFT band (centre = integer) of a MIDI pitch's fundamental."""
    f0 = 440.0 * 2.0 ** ((p - 69.0) / 12.0)
    return NB * np.log(np.maximum(f0, 1e-3) / LO_HZ) / math.log(HI_HZ / LO_HZ) - 0.5


class SpectralTorsion(Effect):
    name = "spectral_torsion"
    stage = "warp"
    label = "Spectral torsion"
    description = ("The picture cut into 48 concentric rings, one per FFT band (bass outermost), each a damped "
                   "torsion pendulum kicked by its band's spectral flux and by piano notes at its pitch; the music "
                   "twists any scene ring by ring and the twists ripple between neighbours.")
    listens = ("fft", "notes")
    params = {
        **standard_effect_params(),
        # the beat envelope is a force gain: keep it raw (easing would flatten it)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "springiness": num(0.4, 1.2, 0.7, "Springiness", unit="Hz", step=0.01, tier="regional"),
        "coupling": num(0.0, 1.0, 0.45, "Neighbour coupling", tier="regional"),
        "counter": num(0.0, 1.0, 0.0, "Vortex ↔ combination lock", tier="regional", basic=True),
        "rings": num(0.4, 1.0, 0.85, "Ring span (outer radius)", step=0.01, tier="regional"),
        "trails": num(0.0, 1.0, 0.3, "Trails", tier="regional"),
        "midi_drive": num(0.0, 1.0, 0.7, "Piano drive", tier="regional"),
        "mic_drive": num(0.0, 1.0, 0.6, "Room sound drive", tier="regional"),
        "ring_edges": num(0.0, 1.0, 0.25, "Ring edges (smooth ↔ hard)", tier="regional"),
        "lock_width": num(1.0, 6.0, 3.0, "Lock ring width", unit="bands", step=0.05, tier="regional"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kw = mod.get_function("st_warp")
        self._kt = mod.get_function("st_trail")
        W, H = ctx.width, ctx.height
        self.gw, self.gh = max(1, W // 2), max(1, H // 2)
        self.G = cp.zeros((self.gh, self.gw, 4), cp.uint16)
        self.T_d = cp.zeros(2 * NB, cp.float32)
        self.T_h = np.zeros(2 * NB, np.float32)
        # physics state (float64 on the host; 48 elements)
        self.th = np.zeros(NB)
        self.v = np.zeros(NB)
        self.F = np.zeros(NB)
        self.acc = np.zeros(NB)
        self.lap = np.zeros(NB)
        self.lapv = np.zeros(NB)
        self.flux = np.zeros(NB)
        self.imp = np.zeros(NB)
        self.disp = np.zeros(NB)
        self.dispv = np.zeros(NB)
        self.tmp = np.zeros(NB)
        b = np.arange(NB, dtype=np.float64)
        self.bidx = b
        self.sgn = np.ones(NB)
        self.bass_w = 0.35 + 0.65 * (1.0 - b / (NB - 1))           # the beat push leans on the outer (bass) rings
        # pitch -> band Gaussian (sigma 1 band), peak 1
        pb = _band_of_pitch(np.arange(128, dtype=np.float64))
        self.kern = np.exp(-0.5 * (b[None, :] - np.clip(pb, 0.0, NB - 1.0)[:, None]) ** 2)   # [128, 48]
        self.kern[:21] = 0.0                                         # below the piano's A0: ignored
        self.kern[109:] = 0.0
        self._seen = None
        self._ghost = 0.0                                            # host estimate of the strongest ghost weight
        self._reset = 1

    # -- host ---------------------------------------------------------------------------------------------------
    def _listen(self, p: dict) -> float:
        """Fill self.flux (mic force, 0.. per band), self.imp (velocity impulses) and self.F's sustain part.
        Returns 1.0 while the piano is present, else 0.0."""
        snd = getattr(self, "sound", None)
        self.flux.fill(0.0)
        self.imp.fill(0.0)
        self.tmp.fill(0.0)
        if snd is None:
            return 0.0
        present = 0.0
        try:
            present = 1.0 if getattr(snd, "present", False) else 0.0
        except Exception:
            present = 0.0
        # the room mic: spectral flux over two history rows (~67 ms)
        try:
            hist, row = snd.fft_history, int(snd.fft_row)
        except Exception:
            hist, row = None, 0
        if hist is not None and getattr(hist, "shape", None) is not None and len(hist.shape) == 2 \
                and hist.shape[1] == NB and hist.shape[0] > 2:
            n = hist.shape[0]
            np.subtract(hist[row % n], hist[(row - 2) % n], out=self.flux)
            np.nan_to_num(self.flux, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.subtract(self.flux, 0.025, out=self.flux)            # the room's jitter floor
            np.clip(self.flux, 0.0, 1.0, out=self.flux)
        # the piano: one velocity impulse per strike, and a gentle lean from what still sounds
        fr = getattr(snd, "frame", None)
        if fr is not None and present > 0.0:
            ons = getattr(fr, "onsets", None)
            if ons is not None and ons is not self._seen:
                for ev in list(ons)[:32]:
                    try:
                        pitch, vel, age = int(ev[0]), float(ev[1]), float(ev[2])
                    except (TypeError, ValueError, IndexError):
                        continue
                    if not (21 <= pitch <= 108 and math.isfinite(vel) and math.isfinite(age)) or age > 1.5:
                        continue
                    k = min(max(vel, 0.0), 1.0) * math.exp(-max(age, 0.0) / 0.6)
                    self.imp += k * self.kern[pitch]
                self._seen = ons
            notes = getattr(fr, "notes", None)
            if notes is not None and getattr(notes, "shape", (0,))[0] == 128:
                lev = np.nan_to_num(np.asarray(notes[:, 0], np.float64), nan=0.0)
                np.dot(np.clip(lev, 0.0, 1.0), self.kern, out=self.tmp)
                np.minimum(self.tmp, 1.5, out=self.tmp)
        return present

    def _physics(self, p: dict, dt: float, present: float) -> None:
        w0 = TWO_PI * min(max(float(p["springiness"]), 0.2), 2.0)
        cpl = _clamp01(p["coupling"])
        c = 40.0 * cpl
        eta = 0.3 * math.sqrt(c)
        # each ring's direction: s_b = 1 - counter (1 - cos(pi b / g)); g = 1 is the spec's 1 - 2 counter (b mod 2),
        # wider g turns rings in bands of g (a legible combination lock across a room)
        g = min(max(float(p["lock_width"]), 1.0), 6.0)
        np.multiply(self.bidx, math.pi / g, out=self.sgn)
        np.cos(self.sgn, out=self.sgn)
        self.sgn -= 1.0
        self.sgn *= _clamp01(p["counter"])
        self.sgn += 1.0
        pulse = _clamp01(p["pulse"])
        gain = 1.0 + 1.5 * pulse
        midi = _clamp01(p["midi_drive"])
        mic = _clamp01(p["mic_drive"]) * (1.0 - 0.6 * present)       # MIDI is exact and photon-aligned: preferred
        # force: mic flux + the sounding notes' lean + the beat's push, all through each ring's direction
        F = self.F
        np.multiply(self.flux, 160.0 * mic, out=F)
        F += (4.0 * midi) * self.tmp
        F += (7.0 * pulse) * self.bass_w
        F *= gain
        F *= self.sgn
        # strikes: velocity impulses (kicks go to rates, never positions)
        if dt > 0.0 and midi > 0.0:
            self.v += (6.0 * midi * gain) * self.imp * self.sgn
        if dt <= 0.0:
            return
        if dt > 0.5:                                                  # a long stall: the rings have settled
            k = math.exp(-ZETA * w0 * dt)
            self.th *= k
            self.v *= k
        dt = min(dt, 0.1)
        n = min(8, max(1, int(math.ceil(dt * 240.0))))
        h = dt / n
        th, v, acc, lap, lapv = self.th, self.v, self.acc, self.lap, self.lapv
        damp = 2.0 * ZETA * w0
        for _ in range(n):
            lap[1:-1] = th[:-2]
            lap[1:-1] += th[2:]
            lap[1:-1] -= 2.0 * th[1:-1]
            lap[0] = th[1] - th[0]
            lap[-1] = th[-2] - th[-1]
            lapv[1:-1] = v[:-2]
            lapv[1:-1] += v[2:]
            lapv[1:-1] -= 2.0 * v[1:-1]
            lapv[0] = v[1] - v[0]
            lapv[-1] = v[-2] - v[-1]
            np.multiply(th, -w0 * w0, out=acc)
            acc -= damp * v
            acc += c * lap
            acc += eta * lapv
            acc += F
            v += h * acc
            th += h * v
        np.clip(th, -3.0 * MAX_TWIST, 3.0 * MAX_TWIST, out=th)       # the state stays bounded
        np.clip(v, -60.0, 60.0, out=v)
        if not (np.isfinite(th).all() and np.isfinite(v).all()):
            th.fill(0.0)
            v.fill(0.0)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        try:
            dt = float(dt)
        except (TypeError, ValueError):
            dt = 0.0
        if not math.isfinite(dt) or dt < 0.0:
            dt = 0.0
        present = self._listen(p)
        self._physics(p, dt, present)
        amount = _clamp01(p["amount"])
        trails = _clamp01(p["trails"])
        # shown angle: soft-limited to pi/3, scaled by amount; its rate by the chain rule
        np.divide(self.th, MAX_TWIST, out=self.disp)
        np.tanh(self.disp, out=self.disp)
        np.multiply(self.disp, self.disp, out=self.dispv)            # d/dx tanh = 1 - tanh^2
        np.subtract(1.0, self.dispv, out=self.dispv)
        self.dispv *= self.v
        self.disp *= MAX_TWIST
        self.disp *= amount
        self.dispv *= amount
        top = float(np.abs(self.disp).max())
        rout = min(max(float(p["rings"]), 0.3), 1.2)
        span = max(rout - R_IN, 1e-3)
        rb = R_IN + span * (1.0 - np.arange(NB) / (NB - 1.0))
        speed = float(np.max(np.abs(self.dispv) * rb)) if trails > 0.0 else 0.0
        decay = math.exp(-dt / (0.08 + 0.5 * trails)) if dt > 0.0 else 1.0
        m_now = min(max((speed - 0.03) / 0.42, 0.0), 1.0)
        self._ghost = max(m_now, self._ghost * decay) if trails > 0.0 else 0.0
        if amount <= 1e-4 or (top < 1e-4 and self._ghost < 2e-3):
            cp.copyto(out, src)
            self._reset = 1
            return
        W, H = self.ctx.width, self.ctx.height
        hd = 0.5 * math.hypot(W, H)
        band_px = span * hd / (NB - 1.0)
        ew = min(0.5, 0.75 / max(band_px, 1e-3))                     # ~1.5 px antialiased ring cut
        edges = _clamp01(p["ring_edges"])
        self.T_h[:NB] = self.disp
        self.T_h[NB:] = self.dispv
        self.T_d.set(self.T_h)
        cx, cy = 0.5 * W, 0.5 * H
        args_geo = (self.T_d, np.float32(cx), np.float32(cy), np.float32(1.0 / hd), np.float32(R_IN),
                    np.float32(rout), np.float32(edges), np.float32(ew))
        gk = 0.0
        if trails > 0.0:
            launch_2d(self._kt, self.gw, self.gh,
                      (src, self.G, np.int32(W), np.int32(H), np.int32(self.gw), np.int32(self.gh)) + args_geo
                      + (np.float32(decay), np.int32(self._reset)))
            self._reset = 0
            gk = 0.85 * trails * amount
        else:
            self._reset = 1
        launch_2d(self._kw, W, H,
                  (src, out, np.int32(W), np.int32(H)) + args_geo
                  + (self.G, np.int32(self.gw), np.int32(self.gh), np.float32(gk)))

    def close(self) -> None:
        self.G = None
        self.T_d = None
