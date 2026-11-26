"""Lyric stamp: each word lands full-screen exactly as it is sung, then pulls back into depth and fades as the next one
lands; a held word swells (karaoke; listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov ("reason through a few good visual concepts"; built: "build them all").  Words are known ahead, so the
landing is ON the word (the slam starts a few frames early and settles at its t0), not a late flash.  Each word is
placed a little off centre and turned a touch (by ``chaos``, seeded by the word, so a replay lands the same), and the
last three recede behind it.  ``invert`` morphs the letters from white to the scene inverted inside them.
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import ENV_MIN, LyricEffect, font_param, under_lyrics_param
from stream_service.plugins._lib.lyric_text import GLYPH_CUDA
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
extern "C" __global__
void stamp(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
           const float* __restrict__ G, const int n, const unsigned char* __restrict__ A,
           const float invert, const float shade, const float flash, const float btop, const float bfe, const float bk)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float4 s = ld(src, x, y, W, H);
    const float ir = 255.f - s.x, ig = 255.f - s.y, ib = 255.f - s.z;    // the scene inverted (the letters' option)
    for (int k = 0; k < n; ++k) {             // oldest first, each over the last: its own shadow, then its letters
        const float* g = sh + k * GI_N;
        const float f = gi_field(g, A, x + 0.5f, y + 0.5f);
        if (f <= 0.f) continue;
        const float a = gi_edge(g, f) * g[8];
        const float d = shade * clamp01(f * 1.7f) * g[8] * (1.f - a);
        s.x *= 1.f - d; s.y *= 1.f - d; s.z *= 1.f - d;
        const float lum = 0.72f + 0.28f * g[11];                          // older words a little greyer
        const float fl = flash * g[11];
        const float lr = fminf(255.f, (255.f + (ir - 255.f) * invert) * lum + fl);
        const float lg = fminf(255.f, (255.f + (ig - 255.f) * invert) * lum + fl);
        const float lb = fminf(255.f, (255.f + (ib - 255.f) * invert) * lum + fl);
        s.x += (lr - s.x) * a; s.y += (lg - s.y) * a; s.z += (lb - s.z) * a;
    }
    s.w = 255.f;
    { const float kp = band_keep(y + 0.5f, btop, bfe, bk);    // the screen's own lyrics band
      s = mix4(ld(src, x, y, W, H), s, kp); }
    out[y * W + x] = pack(s);
}
"""

LAND_S = 0.11          # the slam: from LAND_S before t0 to t0
RECEDE_S = 0.4        # after the next word lands, this one recedes and fades over this
KEEP = 3


def hash_seed(i: int, k: int) -> float:
    """0..1, fixed per (word, k): the same word lands in the same place every time."""
    h = (i * 0x9E3779B1 + k * 0x85EBCA77) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    return (h & 0xFFFFFF) / float(0xFFFFFF)


def _ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1.0 - (1.0 - x) ** 3


class LyricStamp(LyricEffect):
    name = "lyric_stamp"
    label = "Lyric stamp"
    description = ("Each word slams onto the screen as it is sung, then pulls back into depth as the next lands; "
                   "held words swell.")
    params = {
        **standard_effect_params(),
        "size": num(0.2, 0.75, 0.42, "Word size (of the screen's height)", tier="regional", basic=True),
        "opacity": num(0.2, 1.0, 0.85, "Opacity", tier="regional"),
        "slam": num(0.0, 1.0, 0.5, "Slam (how big it lands from)", tier="regional", basic=True),
        "chaos": num(0.0, 1.0, 0.35, "Off-centre and tilt", tier="regional"),
        "invert": num(0.0, 1.0, 0.0, "Letters: white -> the scene inverted", tier="regional"),
        "shade": num(0.0, 0.9, 0.35, "Shadow behind", tier="regional"),
        "punch": num(0.0, 1.0, 0.4, "Beat punch", tier="regional"),
        "under_lyrics": under_lyrics_param(),
        "font": font_param("black"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("stamp")

    def render(self, t_local: float, dt: float, src, out) -> None:
        p, lf, env = self.begin(dt)
        if lf is None or env < ENV_MIN:
            return self.passthrough(src, out)
        song, t = lf.song, lf.t
        self.want_lines(lf, behind=1, ahead=2)
        k = song.word_at(t + LAND_S)              # the newest word landing or landed
        if k < 0:
            return self.passthrough(src, out)
        punch = 1.0 + 0.08 * p["punch"] * p["pulse"]
        drawn = []
        for j in range(k, max(-1, k - KEEP), -1):
            w = song.words[j]
            nxt = song.words[j + 1] if j + 1 < len(song.words) else None
            since_next = t - (nxt.t0 - LAND_S) if nxt is not None and j < k else -1.0   # from the next one's slam
            if nxt is not None and j < k and since_next > RECEDE_S:
                break
            if t - w.t1 > 2.5 and j == k:          # a long gap after the line: it fades on its own
                fade = max(0.0, 1.0 - (t - w.t1 - 2.5) / 0.8)
            else:
                fade = 1.0
            slot = self.slot(song.lines[w.line].text)
            if slot is None:
                continue
            a0, a1 = self.word_px(slot, w.i)
            em = p["size"] * self.H
            sc = em / slot.em
            wpx = (a1 - a0) * sc
            if wpx > 0.9 * self.W:
                sc *= 0.9 * self.W / wpx
            land = _ease_out((t - (w.t0 - LAND_S)) / LAND_S)
            s = (1.0 + p["slam"] * 0.6 * (1.0 - land)) * punch
            a = min(1.0, land * 1.6) * fade
            held = max(0.0, min(t, w.t1) - w.t0)
            s *= 1.0 + 0.05 * min(held, 3.0)
            rx, ry, rr = (hash_seed(j, 1) - 0.5, hash_seed(j, 2) - 0.5, hash_seed(j, 3) - 0.5)
            cx = self.W * (0.5 + 0.12 * p["chaos"] * rx)
            cy = self.region()[0] + self.H * 0.06 * p["chaos"] * ry
            rot = 0.12 * p["chaos"] * rr
            if since_next >= 0.0:                  # pushed back by the next word
                r = _ease_out(since_next / RECEDE_S)
                s *= 1.0 - 0.55 * r
                a *= (1.0 - r) ** 2 * 0.8
                cy -= 0.08 * self.H * r
            drawn.append((slot, a0, a1, cx, cy, sc * s, rot, a, 1.0 if j == k else 0.0))
        for slot, a0, a1, cx, cy, sc, rot, a, newest in reversed(drawn):
            self.gl.add(slot, a0, a1, cx, cy, sc, rot=rot, alpha=p["opacity"] * env * a, user=newest,
                        height=slot.em * 0.62)
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        w = song.words[k]
        flash = 70.0 * max(0.0, 1.0 - abs(t - w.t0) / 0.18) * (1.0 - p["invert"])
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(p["invert"]), np.float32(p["shade"] * env), np.float32(flash), *self.band(p)))
