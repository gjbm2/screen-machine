"""Lyric knockout: the line being sung, filling the screen, becomes a window: the scene shows at full colour through
its letters while the rest of the frame dims and greys; each word opens as it is sung (karaoke; listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov ("build them all").  The line is laid out in one to three balanced rows as large as the frame allows;
words not yet sung are a faint window (``preview``), the sung word opens across its own duration, sung words stay
open.  A thin rim of light traces the letters.  Lines cross-fade; in a long gap the whole effect fades away.
"""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import ENV_MIN, LyricEffect, fit_line, font_param, place_rows
from stream_service.plugins._lib.lyric_text import GLYPH_CUDA
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
extern "C" __global__
void knockout(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
              const float* __restrict__ G, const int n, const unsigned char* __restrict__ A,
              const float dim, const float desat, const float lift, const float rim)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float open = 0.f, rv = 0.f;
    for (int k = 0; k < n; ++k) {
        const float* g = sh + k * GI_N;
        const float f = gi_field(g, A, x + 0.5f, y + 0.5f);
        if (f <= 0.f) continue;
        const float a = gi_edge(g, f) * g[8] * g[11];
        open = 1.f - (1.f - open) * (1.f - a);
        const float e = (f - 0.5f) * 2.5f * SIGMA * g[5];              // screen px from the edge
        rv = fmaxf(rv, __expf(-e * e / 18.f) * g[8] * (0.3f + 0.7f * g[11]));
    }
    const float4 s = ld(src, x, y, W, H);
    const float l = 0.2126f * s.x + 0.7152f * s.y + 0.0722f * s.z;
    const float k = 1.f - dim;
    const float4 d = make_float4((s.x + (l - s.x) * desat) * k, (s.y + (l - s.y) * desat) * k,
                                 (s.z + (l - s.z) * desat) * k, 255.f);
    const float b = 1.f + lift;
    const float4 o = make_float4(fminf(255.f, s.x * b), fminf(255.f, s.y * b), fminf(255.f, s.z * b), 255.f);
    float4 r = mix4(d, o, open);
    const float h = rim * rv * 200.f;
    r.x += h; r.y += h; r.z += h; r.w = 255.f;
    out[y * W + x] = pack(r);
}
"""

XFADE_S = 0.35


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class LyricKnockout(LyricEffect):
    name = "lyric_knockout"
    label = "Lyric knockout"
    description = ("The line being sung fills the screen as a window: the scene shows through its letters, the rest "
                   "dims; each word opens as it is sung.")
    params = {
        **standard_effect_params(),
        "dim": num(0.2, 0.95, 0.7, "Dim outside the letters", tier="regional", basic=True),
        "desat": num(0.0, 1.0, 0.7, "Grey outside the letters", tier="regional"),
        "lift": num(0.0, 0.6, 0.15, "Brighten inside", tier="regional"),
        "preview": num(0.0, 0.8, 0.3, "Words to come (how open)", tier="regional", basic=True),
        "rim": num(0.0, 1.0, 0.35, "Rim light", tier="regional"),
        "fill": num(0.5, 0.98, 0.88, "How much of the screen the line fills", tier="regional"),
        "font": font_param("black"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("knockout")
        self._cur = self._prev = None              # (line index, the lyric time it came in)
        self._key = None

    def _layout(self, line, a: float, t: float, p) -> None:
        slot = self.slot(line.text)
        if slot is None or a <= 0.002:
            return
        cy, room = self.region()                     # above the screen's own lyrics (lyric_fx.LYRICS_BAND_TOP)
        sc, rows = fit_line(slot, p["fill"] * self.W, p["fill"] * 0.92 * room, max_rows=3)
        if sc <= 0:
            return
        pre = p["preview"]

        def opened(i: int) -> float:
            w = line.words[i] if i < len(line.words) else None
            if w is None:
                return 1.0
            if t < w.t0:
                return pre
            return pre + (1.0 - pre) * _ease((t - w.t0) / max(0.12, w.t1 - w.t0))
        place_rows(self.gl, slot, rows, sc, self.W / 2.0, cy, alpha_of=lambda i: a, user_of=opened)

    def render(self, t_local: float, dt: float, src, out) -> None:
        p, lf, env = self.begin(dt)
        if lf is None:
            self._cur = self._prev = None
            return self.passthrough(src, out)
        song, t = lf.song, lf.t
        if song.key != self._key:
            self._cur = self._prev = None
            self._key = song.key
        self.want_lines(lf, behind=1, ahead=2)
        k = lf.line
        if k >= 0 and (self._cur is None or self._cur[0] != k):
            self._prev, self._cur = self._cur, (k, t)
        if env < ENV_MIN or self._cur is None:
            return self.passthrough(src, out)
        x = _ease((t - self._cur[1]) / XFADE_S)
        if self._prev is not None and x < 1.0:
            self._layout(song.lines[self._prev[0]], 1.0 - x, t, p)
        self._layout(song.lines[self._cur[0]], x if self._prev is not None else 1.0, t, p)
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(p["dim"] * env), np.float32(p["desat"] * env), np.float32(p["lift"] * env),
                   np.float32(p["rim"] * env)))
