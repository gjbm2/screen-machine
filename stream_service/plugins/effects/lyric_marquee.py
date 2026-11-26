"""Lyric marquee: the song's words, huge and semi-transparent, racing right to left; each word crosses the lead
point exactly when it is sung (karaoke; listens "lyrics").

Source: Original
Author: screen-machine
License: MIT
license_class: permissive

Owner, 25 Nov: "one which moves the words semi transparent very large rapidly right to left (at 60fps it will look
good)".  The whole song is one tape of words (the typeface's widths, lines a gap apart); the tape's position at lyric
time t runs through each word's middle at that word's sung middle, so quick lines race and slow ones glide, and it
never drifts from the singing.  The position is a Gaussian-smoothed interpolation (no speed jumps between words);
horizontal motion blur follows the speed.  The word being sung takes the highlight colour.
"""
from __future__ import annotations

import numpy as np

from stream_service.gpu import raw_module
from stream_service.plugins._lib.cuda import HELPERS, launch_2d
from stream_service.plugins._lib.lyric_fx import ENV_MIN, LyricEffect, font_param, under_lyrics_param
from stream_service.plugins._lib.lyric_text import GLYPH_CUDA, font_path
from stream_service.plugins._lib.params import num, standard_effect_params

_SRC = HELPERS + GLYPH_CUDA + r"""
extern "C" __global__
void marquee(const uchar4* __restrict__ src, uchar4* __restrict__ out, const int W, const int H,
             const float* __restrict__ G, const int n, const unsigned char* __restrict__ A,
             const float blur_px, const float shade, const float glow, const float hr, const float hg, const float hb, const float btop, const float bfe, const float bk)
{
    GI_LOAD(G, n, sh)
    const int x = blockIdx.x * blockDim.x + threadIdx.x, y = blockIdx.y * blockDim.y + threadIdx.y;
    if (x >= W || y >= H) return;
    float cov = 0.f, gl = 0.f, us = 0.f;
    // one tap per ~10 px of travel (up to 24), each pixel's taps offset by a hash, so a fast word smears smoothly
    // instead of showing a few ghost copies
    const int taps = blur_px > 1.5f ? min(24, max(3, (int)(blur_px / 10.f))) : 1;
    const float jit = hash1((unsigned)x, (unsigned)y) - 0.5f;
    for (int k = 0; k < taps; ++k) {
        const float o = taps > 1 ? blur_px * (((float)k + 0.5f + jit) / taps - 0.5f) : 0.f;
        float c, g, u;
        gi_cover(sh, n, A, x + 0.5f + o, y + 0.5f, &c, &g, &u);
        cov += c; gl += g; us = fmaxf(us, u * c);
    }
    cov /= taps; gl /= taps;
    us = cov > 1e-3f ? clamp01(us / fmaxf(cov, 1e-3f)) : 0.f;
    float4 s = ld(src, x, y, W, H);
    const float d = shade * clamp01(gl * 1.8f) * (1.f - cov);          // a soft shadow behind the letters
    s.x *= 1.f - d; s.y *= 1.f - d; s.z *= 1.f - d;
    const float cr = 255.f + (hr - 255.f) * us, cg = 255.f + (hg - 255.f) * us, cb = 255.f + (hb - 255.f) * us;
    const float h = glow * gl * gl * 90.f;                               // a faint halo
    s.x = s.x + (cr - s.x) * cov + h; s.y = s.y + (cg - s.y) * cov + h; s.z = s.z + (cb - s.z) * cov + h;
    s.w = 255.f;
    { const float kp = band_keep(y + 0.5f, btop, bfe, bk);    // the screen's own lyrics band
      s = mix4(ld(src, x, y, W, H), s, kp); }
    out[y * W + x] = pack(s);
}
"""

SMOOTH_K = np.arange(-4, 5, dtype=np.float64)
SMOOTH_W = np.exp(-0.5 * (SMOOTH_K / 2.0) ** 2)
SMOOTH_W /= SMOOTH_W.sum()
SPACE_EM, LINE_GAP_EM = 0.3, 1.4


def _hue_rgb(h: float, sat: float) -> tuple[float, float, float]:
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, sat, 1.0)
    return 255.0 * r, 255.0 * g, 255.0 * b


class LyricMarquee(LyricEffect):
    name = "lyric_marquee"
    label = "Lyric marquee"
    description = ("The song's words, huge and see-through, race right to left; each word crosses the lead point "
                   "exactly as it is sung.")
    params = {
        **standard_effect_params(),
        "size": num(0.15, 1.05, 0.8, "Text size (the em, of the screen's height)", tier="regional", basic=True),
        "opacity": num(0.1, 0.9, 0.42, "Opacity", tier="regional", basic=True),
        "lead": num(0.25, 0.75, 0.55, "Where the sung word passes (across)", tier="regional"),
        "height": num(0.1, 0.8, 0.36, "Height on screen (centre)", tier="regional"),
        "under_lyrics": under_lyrics_param(),
        "blur": num(0.0, 1.0, 0.6, "Motion blur", tier="regional"),
        "shade": num(0.0, 0.8, 0.25, "Shadow behind", tier="regional"),
        "glow": num(0.0, 1.0, 0.3, "Glow", tier="regional"),
        "highlight": num(0.0, 1.0, 0.6, "Sung-word colour", tier="regional"),
        "hue": num(0.0, 1.0, 0.11, "Highlight hue", tier="regional"),
        "font": font_param("black"),
    }

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self._k = raw_module(_SRC).get_function("marquee")
        from PIL import ImageFont
        self._font = ImageFont.truetype(font_path(str(params.get("font") or "black")), 100)
        self._tape_key = None
        self._x = self._mid = self._w = None
        self._prev_x = None

    def _tape(self, song) -> None:
        if self._tape_key == song.key:
            return
        n = len(song.words)
        xs, ws, mids = np.zeros(n), np.zeros(n), np.zeros(n)
        x, line = 0.0, None
        for k, w in enumerate(song.words):
            if line is not None and w.line != line:
                x += LINE_GAP_EM
            line = w.line
            wd = self._font.getlength(w.text) / 100.0
            xs[k], ws[k], mids[k] = x + wd / 2.0, wd, 0.5 * (w.t0 + w.t1)
            x += wd + SPACE_EM
        self._x, self._w, self._mid, self._tape_key = xs, ws, mids, song.key
        self._prev_x = None

    def _pos(self, t: float) -> float:
        return float(np.dot(SMOOTH_W, np.interp(t + SMOOTH_K * 0.09, self._mid, self._x)))

    def render(self, t_local: float, dt: float, src, out) -> None:
        p, lf, env = self.begin(dt)
        if lf is None or env < ENV_MIN or not lf.song.words:
            self._prev_x = None
            return self.passthrough(src, out)
        song = lf.song
        self._tape(song)
        self.want_lines(lf, behind=1, ahead=3)
        X = self._pos(lf.t)
        vel = 0.0 if self._prev_x is None or dt <= 0 else (X - self._prev_x)     # em per frame
        self._prev_x = X
        em = p["size"] * self.H
        lead = p["lead"] * self.W
        cy = p["height"] * self.H
        op = min(1.0, p["opacity"] * (1.0 + 0.35 * p["pulse"])) * env
        lo, hi = np.searchsorted(self._x, [X - lead / em - 3.0, X + (self.W - lead) / em + 3.0])
        cur = lf.word
        for k in range(max(0, lo - 1), min(len(song.words), hi + 1)):
            w = song.words[k]
            slot = self.slot(song.lines[w.line].text)
            if slot is None:
                continue
            a0, a1 = self.word_px(slot, w.i)
            sc = em / slot.em
            cx = lead + (self._x[k] - X) * em
            if cx + (a1 - a0) * sc < -50 or cx - (a1 - a0) * sc > self.W + 50:
                continue
            sung = 1.0 if (k == cur and lf.t <= w.t1 + 0.25) else 0.0
            st = 1.0 if k == cur else (0.75 if k > cur else 0.6)
            if not self.gl.add(slot, a0, a1, cx, cy, sc, alpha=op * st, user=sung * p["highlight"],
                               height=slot.em * 0.62):
                break
        n = self.gl.upload()
        if n == 0:
            return self.passthrough(src, out)
        hr, hg, hb = _hue_rgb(p["hue"], 0.55)
        blur = abs(vel) * em * p["blur"] * 0.9
        launch_2d(self._k, self.W, self.H,
                  (src, out, np.int32(self.W), np.int32(self.H), self.gl.gpu, np.int32(n), self.atlas.gpu,
                   np.float32(min(blur, 400.0 * self.u)), np.float32(p["shade"] * env), np.float32(p["glow"] * env),
                   np.float32(hr), np.float32(hg), np.float32(hb), *self.band(p)))
