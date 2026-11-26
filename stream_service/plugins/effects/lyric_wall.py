"""Lyric wall: in the chorus, the screen fills with rows of the line being sung, faint, alternate rows scrolling
opposite ways; verses stay clean (karaoke; listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov ("build them all").  A chorus line is one sung more than once in the song (lyric_feed.Line.chorus: the
same words again), so the song's structure shows without any music analysis.  The wall rises as a chorus line comes in
and falls away after the chorus; each new chorus line cross-fades in.  ``ink`` morphs the letters from light
(lifting the scene) to dark (printing on it).
"""
from __future__ import annotations

import math

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import ENV_MIN, LyricEffect, font_param, under_lyrics_param
from stream_service.plugins._lib.lyric_text import GI_MAX, GLYPH_CUDA
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
extern "C" __global__
void wall(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
          const float* __restrict__ G, const int n, const unsigned char* __restrict__ A, const float ink, const float btop, const float bfe, const float bk)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float c, gl, us;
    gi_cover(sh, n, A, x + 0.5f, y + 0.5f, &c, &gl, &us);
    float4 s = ld(src, x, y, W, H);
    const float4 up = make_float4(s.x + (255.f - s.x) * c, s.y + (255.f - s.y) * c, s.z + (255.f - s.z) * c, 255.f);
    const float4 dn = make_float4(s.x * (1.f - c), s.y * (1.f - c), s.z * (1.f - c), 255.f);
    float4 r = mix4(up, dn, ink);
    { const float kp = band_keep(y + 0.5f, btop, bfe, bk);    // the screen's own lyrics band
      r = mix4(ld(src, x, y, W, H), r, kp); }
    out[y * W + x] = pack(r);
}
"""

RISE_S, FALL_S, XFADE_S = 0.6, 1.4, 0.5


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class LyricWall(LyricEffect):
    name = "lyric_wall"
    label = "Lyric chorus wall"
    description = ("In the chorus the screen fills with faint rows of the line being sung, alternate rows scrolling "
                   "opposite ways; verses stay clean.")
    params = {
        **standard_effect_params(),
        "rows": num(3, 10, 6, "Rows", step=1, tier="regional", basic=True, integer=True),
        "opacity": num(0.05, 0.6, 0.22, "Opacity", tier="regional", basic=True),
        "speed": num(0.1, 3.0, 0.8, "Scroll speed (letters a second)", step=0.05, tier="regional"),
        "ink": num(0.0, 1.0, 0.0, "Light -> dark letters", tier="regional"),
        "verses": num(0.0, 1.0, 0.0, "Also in the verses", tier="regional"),
        "under_lyrics": under_lyrics_param(0.0),
        "font": font_param("black"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("wall")
        self._wall = 0.0                     # the wall's level, 0..1 (eased toward the chorus)
        self._cur = self._prev = None        # (line index, lyric time it came in)
        self._key = None
        self._phase = 0.0                    # integrated scroll, em

    def _rows(self, line, a: float, p, nrows: int, em: float) -> None:
        slot = self.slot(line.text)
        if slot is None or a <= 0.002:
            return
        sc = em / slot.em
        gap = 0.9 * slot.em
        x0, x1 = slot.words[0][0] if slot.words else 24.0, slot.width - 24.0
        unit = (x1 - x0 + gap) * sc                      # one copy of the line, plus the gap, in screen px
        if unit <= 1.0:
            return
        pitch = self.region()[1] / nrows             # rows above the screen's own lyrics
        for r in range(nrows):
            d = 1.0 if r % 2 == 0 else -1.0
            v = 0.8 + 0.4 * ((r * 0.618) % 1.0)          # a little parallax between rows
            off = (d * self._phase * em * v + r * unit * 0.37) % unit
            cy = pitch * (r + 0.5)
            x = -unit + off
            while x < self.W + unit:
                if not self.gl.add(slot, x0, x1, x + unit / 2.0, cy, sc, alpha=a, height=slot.em * 0.62):
                    return
                x += unit

    def render(self, t_local: float, dt: float, src, out) -> None:
        p, lf, env = self.begin(dt)
        if lf is None:
            self._wall, self._cur, self._prev = 0.0, None, None
            return self.passthrough(src, out)
        song, t = lf.song, lf.t
        if song.key != self._key:
            self._cur = self._prev = None
            self._key = song.key
        self.want_lines(lf, behind=1, ahead=2)
        ln = lf.current
        want = 1.0 if ln is not None and ln.chorus else (p["verses"] if ln is not None else 0.0)
        rate = 1.0 / RISE_S if want > self._wall else 1.0 / FALL_S
        self._wall += max(-rate * dt, min(rate * dt, want - self._wall))
        if ln is not None and (self._cur is None or self._cur[0] != ln.i):
            self._prev, self._cur = self._cur, (ln.i, t)
        self._phase += p["speed"] * dt * 0.5
        lvl = env * self._wall
        if lvl < ENV_MIN or self._cur is None:
            return self.passthrough(src, out)
        nrows = int(round(p["rows"]))
        em = self.region()[1] / nrows * 0.82
        op = p["opacity"] * lvl * (1.0 + 0.3 * p["pulse"])
        x = _ease((t - self._cur[1]) / XFADE_S)
        cap = GI_MAX // 2
        if self._prev is not None and x < 1.0:
            self._rows(song.lines[self._prev[0]], op * (1.0 - x), p, nrows, em)
        self.gl.n = min(self.gl.n, cap)
        self._rows(song.lines[self._cur[0]], op * (x if self._prev is not None else 1.0), p, nrows, em)
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(p["ink"]), *self.band(p)))
