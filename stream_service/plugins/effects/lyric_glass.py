"""Lyric glass: the line being sung, large, as thick glass lettering pressed onto the picture: the scene bends through
the letters' bevelled edges and a light catches them; sung words are clear glass, words to come are faint
(karaoke; listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov ("build them all").  The lettering's field (lyric_text: the blurred glyph mask) is the glass's height:
its gradient bends the scene (refraction, ``bend`` screen px at most) and lights a specular edge from the top left.
Subtle: the scene stays the star.  Lines cross-fade; a long gap fades it away.
"""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import (ENV_MIN, LyricEffect, fit_line, font_param, place_rows,
                                                  under_lyrics_param)
from stream_service.plugins._lib.lyric_text import GLYPH_CUDA
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
__device__ __forceinline__ float glass_h(const float* sh, int n, const unsigned char* __restrict__ A, float px, float py) {
    float h = 0.f;
    for (int k = 0; k < n; ++k) {
        const float f = gi_field(sh + k * GI_N, A, px, py);
        if (f > 0.f) h = fmaxf(h, f * sh[k * GI_N + 8] * (0.35f + 0.65f * sh[k * GI_N + 11]));
    }
    return h;
}

extern "C" __global__
void glass(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
           const float* __restrict__ G, const int n, const unsigned char* __restrict__ A,
           const float step, const float bend, const float spec, const float tint, const float shadow, const float btop, const float bfe, const float bk)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    const float px = x + 0.5f, py = y + 0.5f;
    const float h = glass_h(sh, n, A, px, py);
    if (h <= 0.f) { out[y * W + x] = src[y * W + x]; return; }
    const float kp = band_keep(y + 0.5f, btop, bfe, bk);    // the screen's own lyrics band
    const float gx = glass_h(sh, n, A, px + step, py) - glass_h(sh, n, A, px - step, py);
    const float gy = glass_h(sh, n, A, px, py + step) - glass_h(sh, n, A, px, py - step);
    const float body = smooth(0.2f, 0.5f, h);                          // inside the glass (soft edge)
    float4 c = bilin(src, px - gx * bend, py - gy * bend, W, H);       // the scene, bent by the slope
    const float l = rsqrtf(gx * gx + gy * gy + 0.02f);
    const float nd = fmaxf(0.f, (-gx * 0.6f - gy * 0.8f) * l);          // the slope facing the light (top left)
    const float sp = spec * powf(nd, 6.f) * clamp01((gx * gx + gy * gy) * 60.f) * 255.f;
    const float4 s = ld(src, x, y, W, H);
    const float dk = shadow * clamp01(h * 1.6f) * (1.f - body);         // a faint shadow round the glass
    const float4 base = make_float4(s.x * (1.f - dk), s.y * (1.f - dk), s.z * (1.f - dk), 255.f);
    const float e = (h - 0.42f) * 9.f;
    const float rim = spec * 70.f * __expf(-e * e);                     // a thin line of light round the glass
    c.x = fminf(255.f, c.x * (1.f + tint) + sp + rim);
    c.y = fminf(255.f, c.y * (1.f + tint) + sp + rim);
    c.z = fminf(255.f, c.z * (1.f + tint) + sp + rim);
    c.w = 255.f;
    out[y * W + x] = pack(mix4(s, mix4(base, c, body), kp));
}
"""

XFADE_S = 0.4


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


class LyricGlass(LyricEffect):
    name = "lyric_glass"
    label = "Lyric glass"
    description = ("The line being sung as thick glass lettering on the picture: the scene bends through the letters' "
                   "edges and a light catches them.")
    params = {
        **standard_effect_params(),
        "bend": num(0.0, 1.0, 0.5, "Bend (refraction)", tier="regional", basic=True),
        "shine": num(0.0, 1.0, 0.6, "Shine", tier="regional", basic=True),
        "tint": num(-0.3, 0.4, 0.06, "Glass brightness", tier="regional"),
        "shadow": num(0.0, 0.8, 0.25, "Shadow", tier="regional"),
        "fill": num(0.4, 0.95, 0.8, "How much of the screen the line fills", tier="regional"),
        "under_lyrics": under_lyrics_param(),
        "font": font_param("black"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("glass")
        self._cur = self._prev = None
        self._key = None
        self._scale = 1.0

    def _layout(self, line, a: float, t: float, p) -> None:
        slot = self.slot(line.text)
        if slot is None or a <= 0.002:
            return
        cy, room = self.region()                     # above the screen's own lyrics (lyric_fx.LYRICS_BAND_TOP)
        sc, rows = fit_line(slot, p["fill"] * self.W, p["fill"] * 0.92 * room, max_rows=2)
        if sc <= 0:
            return
        self._scale = sc

        def clear(i: int) -> float:
            w = line.words[i] if i < len(line.words) else None
            return 1.0 if w is None else _ease((t - w.t0 + 0.1) / 0.3)
        place_rows(self.gl, slot, rows, sc, self.W / 2.0, cy, alpha_of=lambda i: a, user_of=clear)

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
            self._layout(song.lines[self._prev[0]], (1.0 - x) * env, t, p)
        self._layout(song.lines[self._cur[0]], (x if self._prev is not None else 1.0) * env, t, p)
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        step = max(1.5, 2.0 * self._scale)                 # about the field's own scale, in screen px
        bend = (100.0 + 500.0 * p["bend"]) * self.u * (1.0 + 0.3 * p["pulse"])
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(step), np.float32(bend), np.float32(p["shine"] * env), np.float32(p["tint"] * env),
                   np.float32(p["shadow"] * env), *self.band(p)))
