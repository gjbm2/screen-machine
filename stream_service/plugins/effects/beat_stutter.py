"""Beat stutter: a video beat-repeat.  On a beat the whole frame (or a set of random rectangles) is grabbed into a
small half-res ring and held or repeated in 1-8 step patterns locked to the beat grid, with a palette-tinted hold,
a flash on every repeat and an optional zoom-per-repeat, then it crossfades softly back to live.  It reads as a
DJ stutter, not a broken stream.

Source: Original (inspired by https://github.com/Vidvox/ISF-Files/blob/master/ISF/Random%20Freeze.fs
        and https://github.com/Vidvox/ISF-Files/blob/master/ISF/Freeze%20Frame.fs)
Author: smfx (original; ideas from VIDVOX ISF Random Freeze and Freeze Frame, MIT)
License: MIT (original)
license_class: permissive

How it works: a ring of 4 half-res RGBA slots (1920x1080 at 4K, about 33 MB, allocated in setup).  A rising edge
of ``pulse`` (trough-relative, 120 ms refractory, so it works on raw or eased envelopes) may start a stutter
event (``chance``): the current frame is box-downsampled into the oldest slot on the device, a pattern of
``repeat_length`` steps is drawn per rectangle (step 0 always holds the new grab; later steps hold it again, go
live, or rewind to an older grab), and the pattern plays ``2 * repeat_length + 1`` steps on a host-integrated
step counter (``steps_per_beat`` subdivisions of the measured beat period), ending on the new grab, which then
fades back to live over ``fade`` seconds.  Up to 5 rectangles are composited front to back; ``region`` morphs
them from all-full-frame (one whole-frame stutter) to random rectangles (a freeze collage).

Realtime: amount 0 -> out == src exactly (early copy; the ring and event state reset when amount leaves 0 or
dt > 0.5 s).  Hold strength = smoothstep(amount / 0.6) x the event envelope, so the picture always returns smoothly to
live.  pulse only triggers grabs and scales the repeat flash and tint punch; it never moves anything.
dt == 0 -> no step (the counters and the beat clock freeze; a grab on that frame is still allowed).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num, palette_params, standard_effect_params

NSLOT = 4      # ring slots (half-res)
NR = 5         # rectangles per event (rect 0 on top)
RS = 16        # floats per rect
NP = 32 + RS * NR
# P layout: 0 nrects | 1 tint | 2 palette period | 3 drift | 4..15 palette coeffs
# rect r at 32 + 16 r: 0 x0 | 1 y0 | 2 x1 | 3 y1 (uv) | 4 slotA | 5 zoomA | 6 slotB | 7 zoomB | 8 t (A->B)
#                      9 strength | 10 flash | 11 ageA | 12 ageB | 13 cx | 14 cy | 15 unused

_SRC = HELPERS + PALETTE_CUDA + r"""
extern "C" __global__
void bs_capture(const uchar4* __restrict__ src, uchar4* __restrict__ dst,
                const int W, const int H, const int W2, const int H2)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W2 || y >= H2) return;
    const int sx = 2 * x, sy = 2 * y;
    const float4 a = ld(src, sx, sy, W, H), b = ld(src, sx + 1, sy, W, H);
    const float4 c = ld(src, sx, sy + 1, W, H), d = ld(src, sx + 1, sy + 1, W, H);
    dst[y * W2 + x] = pack(make_float4(0.25f * (a.x + b.x + c.x + d.x), 0.25f * (a.y + b.y + c.y + d.y),
                                       0.25f * (a.z + b.z + c.z + d.z), 255.f));
}

// One held (or live) entry for a rect: zoom about the rect centre, palette tint by luma, screen flash.
__device__ float3 bs_entry(const uchar4* __restrict__ ring, const float slotf, const float zoom, const float age,
                           const float rr, const float u, const float v, const float cx, const float cy,
                           const int W2, const int H2, const float fl, const float3 live,
                           const float* __restrict__ P)
{
    if (slotf < -0.5f) return live;
    const int slot = min(max((int)(slotf + 0.5f), 0), 3);
    const float iz = 1.f / fmaxf(zoom, 0.2f);
    const float uz = cx + (u - cx) * iz, vz = cy + (v - cy) * iz;
    const float4 c4 = bilin(ring + (size_t)slot * (size_t)W2 * (size_t)H2, uz * W2, vz * H2, W2, H2);
    float3 c = make_float3(c4.x * (1.f / 255.f), c4.y * (1.f / 255.f), c4.z * (1.f / 255.f));
    const float l = 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    const float tn = P[1];
    if (tn > 0.f) {
        const float3 pc = pal(l * P[2] + P[3] + 0.23f * age + 0.31f * rr, P + 4);
        const float k = 0.22f + 0.95f * l;
        c.x += (pc.x * k - c.x) * tn; c.y += (pc.y * k - c.y) * tn; c.z += (pc.z * k - c.z) * tn;
    }
    if (fl > 0.f) {
        const float3 pf = pal(P[3] + 0.5f + 0.23f * age + 0.31f * rr, P + 4);
        const float fx = fl * (0.35f + 0.65f * pf.x), fy = fl * (0.35f + 0.65f * pf.y), fz = fl * (0.35f + 0.65f * pf.z);
        c.x = 1.f - (1.f - clamp01(c.x)) * (1.f - clamp01(fx));
        c.y = 1.f - (1.f - clamp01(c.y)) * (1.f - clamp01(fy));
        c.z = 1.f - (1.f - clamp01(c.z)) * (1.f - clamp01(fz));
    }
    return c;
}

extern "C" __global__
void bs_apply(const uchar4* __restrict__ src, const uchar4* __restrict__ ring, uchar4* __restrict__ out,
              const int W, const int H, const int W2, const int H2, const float* __restrict__ P)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const int i = y * W + x;
    const uchar4 sp = src[i];
    const float3 s = make_float3(sp.x * (1.f / 255.f), sp.y * (1.f / 255.f), sp.z * (1.f / 255.f));
    const float px = x + 0.5f, py = y + 0.5f;
    const float u = px / (float)W, v = py / (float)H;
    float3 acc = make_float3(0.f, 0.f, 0.f);
    float rem = 1.f;
    const int nr = min((int)P[0], 5);
    for (int r = 0; r < nr; ++r) {
        const float* R = P + 32 + 16 * r;
        const float dx = fminf(px - R[0] * W, R[2] * W - px);
        const float dy = fminf(py - R[1] * H, R[3] * H - py);
        const float cov = clamp01(fminf(dx, dy) + 0.5f);
        if (cov <= 0.f) continue;
        const float rr = (float)r;
        const float t = R[8];
        float3 c = bs_entry(ring, R[4], R[5], R[11], rr, u, v, R[13], R[14], W2, H2, R[10], s, P);
        if (t > 0.f) {
            const float3 cb = bs_entry(ring, R[6], R[7], R[12], rr, u, v, R[13], R[14], W2, H2, 0.f, s, P);
            c.x += (cb.x - c.x) * t; c.y += (cb.y - c.y) * t; c.z += (cb.z - c.z) * t;
        }
        const float st = R[9];
        c.x = s.x + (c.x - s.x) * st; c.y = s.y + (c.y - s.y) * st; c.z = s.z + (c.z - s.z) * st;
        const float w = rem * cov;
        acc.x += w * c.x; acc.y += w * c.y; acc.z += w * c.z;
        rem *= 1.f - cov;
        if (rem < 1e-3f) break;
    }
    out[i] = pack(make_float4(255.f * clamp01(acc.x + rem * s.x), 255.f * clamp01(acc.y + rem * s.y),
                              255.f * clamp01(acc.z + rem * s.z), 255.f));
}
"""


def _ss(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _h(*keys: int) -> float:
    """Deterministic hash of integers -> [0, 1) (host side; no global RNG, no frame count)."""
    x = 0x9E3779B97F4A7C15
    for k in keys:
        x ^= (int(k) & 0xFFFFFFFFFFFFFFFF) + 0x9E3779B97F4A7C15 + ((x << 6) & 0xFFFFFFFFFFFFFFFF) + (x >> 2)
        x &= 0xFFFFFFFFFFFFFFFF
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 31
    return (x >> 11) * (1.0 / 9007199254740992.0)


class BeatStutter(Effect):
    name = "beat_stutter"
    stage = "feedback"
    label = "Beat stutter"
    description = ("Video beat-repeat: on the beat the frame (or random rectangles of it) is grabbed and held or "
                   "repeated in 1-8 step patterns on the beat grid, tinted and flashed on each repeat, then it "
                   "crossfades back to live, like a DJ stutter.")
    _pal = palette_params("neon", 1.2, 0.05)
    params = {
        **standard_effect_params(),
        "region": num(0.0, 1.0, 0.0, "Region (whole frame .. random rects)", tier="regional", basic=True),
        "repeat_length": num(1, 8, 2, "Pattern length", unit="steps", tier="regional", integer=True),
        "steps_per_beat": num(1, 8, 4, "Steps per beat", tier="regional", integer=True),
        "fade": num(0.05, 2.0, 0.45, "Fade back to live", unit="s", tier="regional"),
        "rect_size": num(0.1, 0.7, 0.34, "Rect size", tier="regional"),
        "chance": num(0.0, 1.0, 0.7, "Stutter chance per beat", tier="regional"),
        "repeat_zoom": num(0.0, 0.2, 0.04, "Zoom per repeat", step=0.005, tier="regional"),
        "hold_tint": num(0.0, 1.0, 0.35, "Hold tint (palette)", tier="regional"),
        "flash": num(0.0, 1.0, 0.35, "Repeat flash", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Palette", tier="regional"),
        "palette_period": _pal["palette_period"],
        "drift_speed": _pal["drift_speed"],
        "seed": num(0, 2**31 - 1, 1, "Seed", tier="structural", integer=True),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        mod = raw_module(_SRC)
        self._kcap = mod.get_function("bs_capture")
        self._kapp = mod.get_function("bs_apply")
        W, H = ctx.width, ctx.height
        self.W2, self.H2 = (W + 1) // 2, (H + 1) // 2
        self.ring = cp.zeros((NSLOT, self.H2, self.W2, 4), cp.uint8)
        self.P = cp.zeros(NP, cp.float32)
        self.host = np.zeros(NP, np.float32)
        self.pal_name = None
        self.coeffs = palette_coeffs("neon")
        self.drift = 0.0
        self.clock = 0.0
        self.beat_T = 0.5
        self.last_edge = None
        self.floor = 0.0
        self.refract = 0.0
        self.beats = 0
        self.ev_id = 0
        self.order = list(range(NSLOT))       # slot indices, newest grab first
        self.need_reset = True
        self._clear_event()

    # ------------------------------------------------------------------ helpers
    def _clear_event(self) -> None:
        self.ev_active = False
        self.ev_s = 0.0
        self.ev_after = 0.0
        self.ev_L = 1
        self.ev_spb = 4
        self.ev_len = 3
        self.ev_pat = np.zeros((NR, 1), np.int32)
        self.ev_rnd = np.zeros((NR, 4), np.float64)
        self.ev_order = list(self.order)

    def _grab(self, src, slot: int) -> None:
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._kcap, self.W2, self.H2,
                  (src, self.ring[slot], np.int32(W), np.int32(H), np.int32(self.W2), np.int32(self.H2)))

    def _start_event(self, src, p: dict) -> None:
        self.ev_id += 1
        head = self.order[-1]                 # overwrite the oldest grab
        self._grab(src, head)
        self.order = [head] + self.order[:-1]
        self.ev_order = list(self.order)
        seed = int(p["seed"])
        L = min(max(int(round(float(p["repeat_length"]))), 1), 8)
        self.ev_L = L
        self.ev_spb = min(max(int(round(float(p["steps_per_beat"]))), 1), 8)
        self.ev_len = 2 * L + 1               # play the pattern twice and land on the new grab
        pat = np.zeros((NR, L), np.int32)
        for r in range(NR):
            for k in range(L):
                u = _h(seed, self.ev_id, r, k, 1)
                if k == 0:
                    lag = 0 if r == 0 else int(_h(seed, self.ev_id, r, 7) * NSLOT) % NSLOT
                elif L == 2 and r == 0:
                    lag = -1                  # the classic hold / live chop
                elif u < 0.5:
                    lag = 0
                elif u < 0.78:
                    lag = -1
                else:
                    lag = 1 + int(_h(seed, self.ev_id, r, k, 2) * (NSLOT - 1)) % (NSLOT - 1)
                pat[r, k] = lag
        self.ev_pat = pat
        self.ev_rnd = np.array([[_h(seed, self.ev_id, r, 11 + j) for j in range(4)] for r in range(NR)])
        self.ev_s = 0.0
        self.ev_after = 0.0
        self.ev_active = True

    # ------------------------------------------------------------------ render
    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        a = min(max(float(p["amount"]), 0.0), 1.0)
        kp = min(max(float(p["pulse"]), 0.0), 1.0)
        dt = max(float(dt), 0.0)

        if a <= 1e-4:
            cp.copyto(out, src)
            self.need_reset = True
            self.floor = kp
            return
        if dt > 0.5:
            self.need_reset = True
        if self.need_reset:
            # fill every slot with the current frame so a rewind never shows black or stale content
            self._grab(src, self.order[0])
            for s in self.order[1:]:
                cp.copyto(self.ring[s], self.ring[self.order[0]])
            self._clear_event()
            self.floor = 0.0                  # a pulse already high on entry grabs at once (m is tiny then)
            self.refract = 0.0
            self.need_reset = False
        m = _ss(a)
        dtc = min(dt, 0.1)
        self.clock += dtc
        self.drift = math.fmod(self.drift + float(p["drift_speed"]) * dtc, 1000.0)

        # rising-edge detector, relative to the trough since the last edge (works on raw or eased envelopes)
        self.floor = min(self.floor, kp)
        self.refract = max(self.refract - dtc, 0.0)
        if kp - self.floor > 0.3 and self.refract <= 0.0:
            self.floor = kp
            self.refract = 0.12
            if self.last_edge is not None:
                per = self.clock - self.last_edge
                if 0.25 <= per <= 1.2:
                    self.beat_T += (per - self.beat_T) * 0.4
            self.last_edge = self.clock
            self.beats += 1
            in_phrase = self.ev_active and self.ev_s < self.ev_len
            if not in_phrase and _h(int(p["seed"]), self.beats, 99) < float(p["chance"]):
                self._start_event(src, p)

        if not self.ev_active:
            cp.copyto(out, src)
            return

        # advance the event on the beat grid (dt == 0: no step)
        step_T = max(self.beat_T / self.ev_spb, 1e-3)
        if self.ev_s < self.ev_len:
            self.ev_s = min(self.ev_s + dtc / step_T, float(self.ev_len))
        else:
            self.ev_after += dtc
        fade = max(float(p["fade"]), 0.05)
        env = 1.0 if self.ev_s < self.ev_len else 1.0 - _ss(self.ev_after / fade)
        if env <= 1e-4:
            self._clear_event()
            cp.copyto(out, src)
            return
        strength = _ss(a / 0.6) * env         # hold mix: zero slope at amount 0, full from amount 0.6

        L, n = self.ev_L, self.ev_len
        k = int(math.floor(self.ev_s))
        in_phrase = k < n
        kA = k if in_phrase else n - 1
        frac = self.ev_s - k if in_phrase else 0.0
        kB = min(kA + 1, n - 1)
        t = _ss((frac - 0.86) / 0.14) if (in_phrase and kB != kA) else 0.0
        rz = float(p["repeat_zoom"]) * m
        fl = float(p["flash"]) * m * math.exp(-5.0 * frac) * (0.6 + 0.4 * kp) if in_phrase else 0.0
        rs = float(p["rect_size"])
        rg = min(max(float(p["region"]), 0.0), 1.0)

        h = self.host
        if p["palette"] != self.pal_name:
            self.pal_name = p["palette"]
            self.coeffs = palette_coeffs(self.pal_name)
        h[0] = NR
        h[1] = float(p["hold_tint"]) * m * (0.75 + 0.25 * kp)
        h[2] = float(p["palette_period"])
        h[3] = self.drift
        h[4:16] = self.coeffs
        for r in range(NR):
            ux, uy, uw, uh = self.ev_rnd[r]
            w = min(0.95, rs * (0.45 + 1.1 * uw))
            hh = min(0.95, rs * (0.45 + 1.1 * uh))
            x0r, y0r = ux * (1.0 - w), uy * (1.0 - hh)
            x0, y0 = rg * x0r, rg * y0r
            x1, y1 = 1.0 + rg * (x0r + w - 1.0), 1.0 + rg * (y0r + hh - 1.0)
            lagA = int(self.ev_pat[r, kA % L])
            lagB = int(self.ev_pat[r, kB % L])
            o = 32 + RS * r
            h[o + 0], h[o + 1], h[o + 2], h[o + 3] = x0, y0, x1, y1
            h[o + 4] = -1.0 if lagA < 0 else float(self.ev_order[lagA])
            h[o + 5] = 1.0 + rz * (kA % 4)
            h[o + 6] = -1.0 if lagB < 0 else float(self.ev_order[lagB])
            h[o + 7] = 1.0 + rz * (kB % 4)
            h[o + 8] = t
            h[o + 9] = strength
            h[o + 10] = fl if lagA >= 0 else 0.0
            h[o + 11] = max(lagA, 0)
            h[o + 12] = max(lagB, 0)
            h[o + 13], h[o + 14] = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
        self.P.set(h)
        W, H = self.ctx.width, self.ctx.height
        launch_2d(self._kapp, W, H, (src, self.ring, out, np.int32(W), np.int32(H),
                                     np.int32(self.W2), np.int32(self.H2), self.P))

    def close(self) -> None:
        self.ring = None
