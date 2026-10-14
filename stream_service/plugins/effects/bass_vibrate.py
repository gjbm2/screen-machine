"""Bass vibrate: a speaker-cone buzz.  On every kick the picture vibrates at 15-60 Hz along an axis (or breathes
radially like a cone), rendered as the motion-blurred double image of a vibrating string whose two extremes carry
a slight, palette-tinted RGB separation.

Source: Original
Author: smfx (original)
License: MIT (original)
license_class: permissive

How it works.  The oscillation phase is integrated on the host (``phase += f * dt``, wrapped) and the frame is
the time average of the vibration over one frame interval (the shutter): 6 bilinear taps at sub-frame phases
``phase - span + span * (k + 0.5) / 6`` with ``span = min(f / fps, 1)`` cycles.  At 60 Hz a frame sees a whole
cycle, so the image settles into the classic vibrating-string double image (a sine spends most of its time at the
extremes); lower frequencies sweep part of a cycle per frame and read as a blurred wobble.  Nothing is sampled
from per-frame noise, so there is no 60 fps aliasing jitter and frame skips do not change the look.

Colour.  Each tap's channels are weighted by ``1 + split * s_k * v`` (``s_k`` the tap's excursion, ``v`` the chroma
part of ``pal(i) - pal(i + 0.5)``, normalised), so the positive extreme leans to one palette hue and the negative
extreme to its opposite: an RGB separation in the blur that costs no extra taps.  The palette index drifts with
``drift_speed`` and varies across the frame with ``palette_period``.  A greyscale palette falls back to red/blue.

Signals.  ``amount`` (through smoothstep, zero slope at 0) scales the amplitude and the overscan zoom that keeps
the edges covered; amount 0 is an exact copy.  ``pulse`` (raw, not eased) drives a buzz envelope with a fast
attack (~15 ms) and a ``decay_ms`` release; the envelope also bends the pitch up a little on the hit (a rate, never
a position).  ``idle_buzz`` keeps a faint hum between beats.  ``string`` shapes the linear vibration like a string
pinned at the frame edges (nodes at the edges, antinode in the middle); ``axis`` morphs horizontal -> vertical
(via the diagonals) -> radial.  One 4K pass, 6 bilinear taps.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.palettes import PALETTE_CUDA, palette_coeffs
from stream_service.plugins._lib.params import num, palette_params, standard_effect_params

NT = 6  # time taps across the frame interval

_SRC = HELPERS + PALETTE_CUDA + r"""
#define NT 6
extern "C" __global__
void bass_vibrate(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
                  const float* __restrict__ tp, const float A, const float iz, const float dc, const float ds,
                  const float wr, const float str, const float split, const float* __restrict__ P,
                  const float drift, const float period)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float cx = 0.5f * W, cy = 0.5f * H;
    const float rx = x + 0.5f - cx, ry = y + 0.5f - cy;
    const float bx = cx + rx * iz, by = cy + ry * iz;          // overscanned rest position
    // linear axis (dc, ds); u = position across the axis in -0.5..0.5 (perpendicular = (-ds, dc))
    const float ext = fabsf(ds) * W + fabsf(dc) * H;
    const float u = (-ds * rx + dc * ry) / fmaxf(ext, 1.f);
    const float prof = 1.f + str * (__cosf(3.14159265f * u) - 1.f);   // string: nodes at the frame edges
    const float invR = 2.f * rsqrtf((float)W * W + (float)H * H);
    const float Dx = A * ((1.f - wr) * prof * dc + wr * rx * invR);
    const float Dy = A * ((1.f - wr) * prof * ds + wr * ry * invR);
    // colour axis of the two extremes: chroma part of pal(i) - pal(i + 0.5)
    float vr = 0.f, vg = 0.f, vb = 0.f;
    if (split > 1e-4f) {
        const float rn = sqrtf(rx * rx + ry * ry) * invR;
        const float idx = drift + 0.5f * period * ((1.f - wr) * u + wr * rn);
        const float3 a = pal(idx, P), b = pal(idx + 0.5f, P);
        float ex = a.x - b.x, ey = a.y - b.y, ez = a.z - b.z;
        const float mu = (ex + ey + ez) * (1.f / 3.f);
        ex -= mu; ey -= mu; ez -= mu;
        const float mx = fmaxf(fabsf(ex), fmaxf(fabsf(ey), fabsf(ez)));
        const float k = smooth(0.03f, 0.2f, mx);              // greyscale palette -> classic red/blue split
        const float inv = k / fmaxf(mx, 1e-4f);
        vr = split * (ex * inv + (1.f - k));
        vg = split * (ey * inv);
        vb = split * (ez * inv - (1.f - k));
    }
    float r = 0.f, g = 0.f, bl = 0.f;
    #pragma unroll
    for (int k = 0; k < NT; ++k) {
        const float s = tp[k];
        const float4 c = bilin(src, bx + s * Dx, by + s * Dy, W, H);
        r += (1.f + s * vr) * c.x; g += (1.f + s * vg) * c.y; bl += (1.f + s * vb) * c.z;
    }
    const float ssum = tp[NT];
    r /= fmaxf((float)NT + vr * ssum, 0.1f);
    g /= fmaxf((float)NT + vg * ssum, 0.1f);
    bl /= fmaxf((float)NT + vb * ssum, 0.1f);
    out[y * W + x] = pack(make_float4(r, g, bl, 255.f));
}
"""


def _smoothstep(x: float) -> float:
    t = min(max(x, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class BassVibrate(Effect):
    name = "bass_vibrate"
    label = "Bass vibrate"
    stage = "motion"
    description = ("Speaker-cone buzz: every kick makes the picture vibrate at 15-60 Hz as a motion-blurred "
                   "vibrating-string double image with palette-tinted RGB fringes at its two extremes.")
    tags = {"family": ["motion"], "dims": ["2d"], "technique": ["displacement", "optical"]}
    params = {
        **standard_effect_params(),
        # the beat envelope drives our own fast-attack envelope: keep it raw (easing would flatten the hits)
        "pulse": {**standard_effect_params()["pulse"], "x-ease": False},
        "frequency": num(15.0, 60.0, 42.0, "Buzz frequency", step=0.5, unit="Hz", tier="regional", basic=True),
        "amplitude_px": num(2.0, 80.0, 28.0, "Amplitude (px at 4K)", step=0.5, unit="px", tier="regional",
                            basic=True),
        "axis": num(0.0, 2.0, 0.0, "Axis (0 horizontal, 1 vertical, 2 radial)", step=0.01, tier="regional"),
        "string": num(0.0, 1.0, 0.3, "String shape (nodes at the edges)", tier="regional"),
        "rgb_split": num(0.0, 0.85, 0.4, "RGB split at the extremes", tier="regional"),
        "idle_buzz": num(0.0, 0.3, 0.1, "Idle buzz (between beats)", tier="regional"),
        "decay_ms": num(80.0, 600.0, 220.0, "Buzz decay", step=5.0, unit="ms", tier="regional"),
        **palette_params("neon", 1.0, 0.05),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("bass_vibrate")
        self._taps_h = np.zeros(NT + 2, dtype=np.float32)
        self._taps_d = cp.zeros(NT + 2, dtype=cp.float32)
        self._P_d = cp.zeros(12, dtype=cp.float32)
        p0 = self.easer.values()
        self._pal = np.array(palette_coeffs(p0.get("palette", "neon")), dtype=np.float64)
        self._P_d.set(self._pal.astype(np.float32))
        self._phase = 0.0     # vibration phase, cycles in [0, 1)
        self._env = 0.0       # buzz envelope
        self._drift = 0.0

    def render(self, t_local: float, dt: float, src, out) -> None:
        p = self.step_params(dt)
        cp = self.ctx.cp
        W, H = self.ctx.width, self.ctx.height
        dt = min(max(float(dt), 0.0), 0.25)
        # buzz envelope: fast attack, decay_ms release (to ~5 %); dt = 0 means no step
        pulse = min(max(float(p["pulse"]), 0.0), 1.0)
        if dt > 0.0:
            if pulse > self._env:
                self._env += (pulse - self._env) * (1.0 - math.exp(-dt / 0.015))
            else:
                self._env *= math.exp(-dt * 3000.0 / max(float(p["decay_ms"]), 1.0))
        env = self._env
        # the hit bends the pitch up (a rate), then it settles as the envelope decays
        f = float(p["frequency"]) * (1.0 + 0.35 * env)
        self._phase = (self._phase + f * dt) % 1.0
        self._drift = (self._drift + float(p["drift_speed"]) * dt) % 1.0
        # palette coefficients ease (the enum switches at once)
        tgt = np.array(palette_coeffs(p["palette"]), dtype=np.float64)
        if dt > 0 and not np.array_equal(tgt, self._pal):
            self._pal += (tgt - self._pal) * (1.0 - math.exp(-dt / 1.0))
            if np.max(np.abs(tgt - self._pal)) < 1e-4:
                self._pal = tgt
            self._P_d.set(self._pal.astype(np.float32))

        amount = min(max(float(p["amount"]), 0.0), 1.0)
        if amount <= 1e-4:
            cp.copyto(out, src)
            return
        m = _smoothstep(amount)
        cap = m * float(p["amplitude_px"]) * (W / 3840.0)          # peak excursion in output pixels
        idle = float(p["idle_buzz"])
        A = cap * (idle + (1.0 - idle) * env)
        iz = 1.0 - 2.0 * cap * 1.02 / min(W, H)                    # overscan grows with the displacement
        # time taps across one frame interval (the shutter), ending at the current phase
        fps = float(getattr(self.ctx, "fps", 60) or 60)
        span = min(f / fps, 1.0)
        ph = self._phase - span + span * (np.arange(NT) + 0.5) / NT
        s = np.sin(2.0 * np.pi * ph)
        th = self._taps_h
        th[:NT] = s
        th[NT] = float(s.sum())
        th[NT + 1] = 0.0
        self._taps_d.set(th)
        ax = float(p["axis"])
        ang = 0.5 * math.pi * min(max(ax, 0.0), 1.0)
        wr = min(max(ax - 1.0, 0.0), 1.0)
        split = float(p["rgb_split"]) * m
        launch_2d(self._k, W, H, (src, out, np.int32(W), np.int32(H), self._taps_d, np.float32(A),
                                  np.float32(iz), np.float32(math.cos(ang)), np.float32(math.sin(ang)),
                                  np.float32(wr), np.float32(p["string"]), np.float32(split), self._P_d,
                                  np.float32(self._drift), np.float32(p["palette_period"])))

    def close(self) -> None:
        self._taps_d = None
        self._P_d = None
