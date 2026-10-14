"""TV static: a set losing its signal.  Tracking lines tear and roll sideways, a VHS tracking bar crawls up the
screen, the picture loses vertical hold and rolls (with its black blanking bar), scanlines darken, and a band of
animated snow wipes the new picture in from the top, or a full screen of snow swallows the old picture and
hands over to the new one.

Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/TVStatic.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/static_wipe.glsl
        https://github.com/gl-transitions/gl-transitions/blob/master/transitions/old_tv_lost_signal.glsl
Author: Brandon Anzaldi (TVStatic); Ben Lucas (static_wipe); mernking, gitlab.com/Godswork (old_tv_lost_signal)
License: MIT   (each file header says "// License: MIT" / "// license: MIT"; repo LICENSE: "MIT License,
                Copyright (c) 2017-present gl-transitions contributors",
                https://github.com/gl-transitions/gl-transitions/blob/master/LICENSE)
license_class: permissive
Ported from GLSL by smfx (ideas/changes: the three sources are merged into one continuous style space.
  static_wipe's top-to-bottom wipe with a snow band at its edge (band height ~ sqrt(sin(pi p))) is the core;
  static_span morphs it continuously into TVStatic's full screen of snow (a plateau of snow over the middle of
  the transition, with the wipe compressed into the plateau so the swap happens under the snow).
  old_tv_lost_signal's tracking lines (120 row groups, hashed, shifted sideways), its sine drift and its
  scanline darkening are kept, with these changes: the torn rows roll and are re-picked at 15 Hz, a rolling
  VHS tracking bar, an RGB split on torn rows and a multipath ghost echo are added (all scaled by tracking);
  a vertical-hold roll with a blanking bar is added (vertical_roll); the wipe edge is torn per row block;
  fract(sin()) hashes are replaced by the integer hash1 seeded by the frame index floor(p * duration_s * 60);
  snow is elongated horizontally like real off-air snow, has rolling interference bars and a light per-frame
  flicker, grain is sized in 4K pixels (grain_size), a faint ghost of the picture survives inside the snow, and
  chroma_noise tints the snow through the shared cosine palette with drift.  Scanlines are smooth (cosine)
  rather than sin(900 y).  Endpoints are forced: p <= 0 copies a, p >= 1 copies b.)

MIT notice (gl-transitions): Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following
conditions: The above copyright notice and this permission notice shall be included in all copies or substantial
portions of the Software.  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins.base import Transition

from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.palettes import PALETTE_CUDA, PALETTES, palette_coeffs
from stream_service.plugins._lib.params import enum, num

_TAU = 2.0 * math.pi

_SRC = HELPERS + PALETTE_CUDA + r"""
extern "C" __global__
void tv_static(const uchar4* __restrict__ a, const uchar4* __restrict__ b, uchar4* __restrict__ out,
               const float* __restrict__ P, const int W, const int H, const unsigned int fi,
               const unsigned int seed,
               const float edge, const float hw, const float benv, const float full, const float jag_amp,
               const float tear_p, const float tear_amp, const float tear_roll, const float wob_amp,
               const float wob_ph, const float tb_pos, const float tb_h, const float tb_k,
               const float roll_a, const float roll_b, const float vjit,
               const float split_px, const int echo_px, const float echo_k, const float desat,
               const float scan_k, const float scan_per, const float gx, const float gy,
               const float chroma, const float period, const float drift, const float bar_ph,
               const float flick)
{
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float v = (y + 0.5f) / H;

    // ---- torn wipe edge (row blocks re-picked every frame): new picture above, old below
    const int jrow = y / max(1, (int)(gy * 3.f));
    const float jag = (hash1((unsigned)jrow, fi, seed + 11u) - 0.5f) * jag_amp;
    const float de = v + jag - edge;
    const bool use_b = de < 0.f;

    // ---- snow envelope: the band around the edge, the full-screen plateau, the tracking bar
    const float band = benv * (1.f - smooth(hw * 0.4f, hw + 1e-4f, fabsf(de)));
    const float tb = tb_k * (1.f - smooth(tb_h * 0.3f, tb_h + 1e-4f, fabsf(v - tb_pos)));
    const float s = fmaxf(fmaxf(band, full), 0.7f * tb);

    // ---- horizontal: hold wobble, torn tracking lines (rolling), tracking-bar shred
    const float lv = v - tear_roll;
    const int line = (int)floorf(lv * 120.f);
    const unsigned int fl = fi >> 2;
    const float torn = hash1((unsigned)line, fl, seed + 3u) > 1.f - tear_p ? 1.f : 0.f;
    float dx = wob_amp * __sinf(v * 30.f + wob_ph);
    dx += torn * (hash1((unsigned)line, fl, seed + 5u) - 0.5f) * 2.f * tear_amp;
    dx += tb * (hash1((unsigned)(y / max(1, (int)gy)), fi, seed + 9u) - 0.5f) * 0.08f * W;

    // ---- vertical hold: roll with a black blanking bar at the seam
    const float off = use_b ? roll_b : roll_a;
    float vs = v + off + vjit;
    vs -= floorf(vs);
    const float sd = fminf(vs, 1.f - vs);
    const float bar = smooth(0.f, 0.03f, fabsf(off)) * (1.f - smooth(0.022f, 0.03f, sd));
    const int sy = min((int)(vs * H), H - 1);
    const uchar4* __restrict__ src = use_b ? b : a;
    int sx = (int)floorf(x + dx);
    sx = ((sx % W) + W) % W;
    const uchar4 c0 = src[sy * W + sx];
    float r = c0.x, g = c0.y, bl = c0.z;
    const float spl = split_px * fminf(torn + tb, 1.f);
    if (spl >= 1.f) {                                              // RGB split on torn rows
        const int sx2 = (sx + (int)spl) % W;
        r = src[sy * W + sx2].x;
    }
    if (echo_k > 0.f) {                                            // multipath ghost to the right
        const int sx3 = ((sx - echo_px) % W + W) % W;
        const uchar4 e = src[sy * W + sx3];
        r += (e.x - r) * echo_k; g += (e.y - g) * echo_k; bl += (e.z - bl) * echo_k;
    }
    const float lum0 = 0.2126f * r + 0.7152f * g + 0.0722f * bl;
    r += (lum0 - r) * desat; g += (lum0 - g) * desat; bl += (lum0 - bl) * desat;
    const float kb = 1.f - 0.94f * bar;
    r *= kb; g *= kb; bl *= kb;

    // ---- snow: horizontally elongated cells, rolling interference bars, palette tint
    float o0 = r, o1 = g, o2 = bl;
    if (s > 0.f) {
        const unsigned int cx = (unsigned)((x + 0.5f) / gx), cy = (unsigned)((y + 0.5f) / gy);
        const unsigned int fs = fi * 977u + seed;
        float n = hash1(cx, cy, fs);
        n = n * n * (3.f - 2.f * n);
        const float lum = n * (0.78f + 0.32f * __sinf(6.2831853f * v * 3.5f + bar_ph)) * flick;
        const float3 pc = pal(hash1(cx, cy, fs + 1u) * period + drift + v * 0.25f, P);
        const float cs = 0.35f + 1.1f * lum;
        const float sr = 255.f * (lum + (pc.x * cs - lum) * chroma);
        const float sg = 255.f * (lum + (pc.y * cs - lum) * chroma);
        const float sb = 255.f * (lum + (pc.z * cs - lum) * chroma);
        const float ks = 0.78f * s;                                // a faint ghost of the picture survives
        o0 += (sr - o0) * ks; o1 += (sg - o1) * ks; o2 += (sb - o2) * ks;
    }

    // ---- scanlines over everything
    const float sc = 1.f - scan_k * (0.5f + 0.5f * __cosf(6.2831853f * (y + 0.5f) / scan_per));
    out[y * W + x] = pack(make_float4(o0 * sc, o1 * sc, o2 * sc, 255.f));
}
"""


def _smooth(e0: float, e1: float, x: float) -> float:
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def _hash01(a: int, b: int) -> float:
    h = (a * 2654435761 ^ (b + 0x9E3779B9) * 2246822519) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2654435761) & 0xFFFFFFFF
    h ^= h >> 13
    return (h & 0xFFFFFF) / 16777216.0


def _wrap(ph: float) -> float:
    return (ph + math.pi) % _TAU - math.pi


class TvStatic(Transition):
    name = "tv_static"
    label = "TV static"
    description = ("A TV losing signal: tracking lines tear and roll, scanlines darken, and a band of snow (or a "
                   "full screen of it) carries the swap.")
    default_duration_s = 1.2
    params = {
        "static_span": num(0.0, 1.0, 0.35, "Static span (band wipe -> full snow)", tier="regional", basic=True),
        "tracking": num(0.0, 1.0, 0.6, "Tracking tear", tier="regional"),
        "scanlines": num(0.0, 1.0, 0.5, "Scanlines", tier="regional"),
        "grain_size": num(1.0, 8.0, 3.0, "Grain size", step=0.1, unit="px", tier="regional"),
        "chroma_noise": num(0.0, 1.0, 0.3, "Colour snow", tier="regional"),
        "vertical_roll": num(0.0, 1.0, 0.35, "Vertical roll", tier="regional"),
        "palette": enum(list(PALETTES), "neon", "Snow palette", tier="regional"),
        "palette_period": num(0.1, 3.0, 1.0, "Palette period", tier="regional"),
        "colour_drift": num(0.0, 1.0, 0.15, "Colour drift", unit="/s", tier="regional"),
        "seed": num(-1, 2**31 - 1, -1, "Seed (-1 random)", tier="structural", integer=True),
    }

    def setup(self, ctx) -> None:
        super().setup(ctx)
        cp = ctx.cp
        self._k = raw_module(_SRC).get_function("tv_static")
        self._P = cp.zeros(12, dtype=cp.float32)
        self._pal_name = None
        s = int(self.params.get("seed", -1))
        self._seed = int(np.random.default_rng().integers(0, 2**31)) if s < 0 else s
        self._drift0 = _hash01(self._seed, 7)

    def _sync_palette(self) -> None:
        name = self.params.get("palette", "neon")
        if name not in PALETTES:
            name = "neon"
        if name != self._pal_name:
            self._P.set(np.asarray(palette_coeffs(name), np.float32))
            self._pal_name = name

    def render(self, progress: float, a, b, out) -> None:
        cp = self.ctx.cp
        if progress <= 0.0:
            cp.copyto(out, a)
            return
        if progress >= 1.0:
            cp.copyto(out, b)
            return
        p = self.params
        w, h = self.ctx.width, self.ctx.height
        q = float(progress)
        dur = max(self.duration_s, 0.05)
        t = q * dur
        fi = int(math.floor(t * 60.0))
        env = math.sin(math.pi * q)
        self._sync_palette()

        span = min(max(float(p["static_span"]), 0.0), 1.0)
        trk = min(max(float(p["tracking"]), 0.0), 1.0)
        vr = min(max(float(p["vertical_roll"]), 0.0), 1.0)
        gate = _smooth(0.2, 0.8, span)

        # full-screen snow plateau (TVStatic) over the middle, widening with span
        wf = 0.45 * span
        ramp = 0.12 + 0.1 * (1.0 - span)
        full = gate * _smooth(0.5 - wf / 2 - ramp, 0.5 - wf / 2, q) * (1.0 - _smooth(0.5 + wf / 2, 0.5 + wf / 2 + ramp, q))
        # the wipe (static_wipe) is compressed into the plateau as span grows, so the swap hides under the snow
        wc = 1.0 - 0.75 * gate
        qe = min(max((q - (0.5 - wc / 2)) / wc, 0.0), 1.0)
        benv = math.sqrt(max(math.sin(math.pi * qe), 0.0))
        hw = (0.035 + 0.45 * span ** 1.5) * benv
        jag_amp = (0.015 + 0.5 * hw) * benv

        # tracking: torn lines, hold wobble, rolling tracking bar, RGB split, ghost echo
        tear_p = (0.04 + 0.22 * trk) * min(1.0, env * 3.0) if trk > 0.0 else 0.0
        tear_amp = 0.045 * w * trk * env
        tear_roll = 0.8 * q
        wob_amp = 0.012 * w * trk * env
        wob_ph = _wrap(q * 10.0 + t * 2.0)
        rolls = 1.0 + 2.0 * trk
        tb_pos = 1.15 - ((q * rolls) % 1.0) * 1.3
        tb_h = 0.025 + 0.05 * trk
        tb_k = trk * env
        split_px = 0.006 * w * trk
        echo_k = 0.22 * trk * env
        echo_px = int(round(0.018 * w))

        # vertical hold: a rolls away faster and faster, b rolls in and settles; both continue the same way
        roll_a = vr * 1.2 * q * q
        roll_b = -vr * 1.2 * (1.0 - q) ** 2
        vjit = (_hash01(fi, self._seed) - 0.5) * 0.012 * vr * env

        desat = 0.5 * env
        scan_k = float(p["scanlines"]) * 0.45 * min(1.0, env * 2.5)
        scan_per = max(3.0, 6.0 * h / 2160.0)
        gy = max(1.0, float(p["grain_size"]) * h / 2160.0)
        gx = gy * 1.6
        drift = (self._drift0 + float(p["colour_drift"]) * t) % 1.0
        bar_ph = _wrap(t * 5.5)
        flick = 0.9 + 0.2 * _hash01(fi, self._seed + 1)

        f32 = np.float32
        launch_2d(self._k, w, h,
                  (a, b, out, self._P, np.int32(w), np.int32(h), np.uint32(fi & 0xFFFFFFFF),
                   np.uint32(self._seed & 0xFFFFFFFF),
                   f32(qe), f32(hw), f32(benv), f32(full), f32(jag_amp),
                   f32(tear_p), f32(tear_amp), f32(tear_roll), f32(wob_amp), f32(wob_ph),
                   f32(tb_pos), f32(tb_h), f32(tb_k),
                   f32(roll_a), f32(roll_b), f32(vjit),
                   f32(split_px), np.int32(echo_px), f32(echo_k), f32(desat),
                   f32(scan_k), f32(scan_per), f32(gx), f32(gy),
                   f32(p["chroma_noise"]), f32(p["palette_period"]), f32(drift), f32(bar_ph), f32(flick)))
