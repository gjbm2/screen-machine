"""Lyric text for the karaoke effects (listens "lyrics"; stream_service/lyric_feed.py): lines baked ahead, drawn crisp.

A line is rasterised ONCE (Pillow, on a background thread: the words are known ahead, so the next lines bake before
they are sung) into a slot of a GPU atlas: uint8 [n_slots, SLOT_H, SLOT_W], the glyph mask Gaussian-blurred by
BLUR_SIGMA.  That blur is a cheap distance field: its 0.5 level is the glyph's edge, and near it the value is linear
in the distance (v - 0.5) * 2.5 * sigma slot px, so a word scaled up to a whole 4K screen keeps a sharp, anti-aliased
edge (the kernel thresholds at screen resolution), and the low values give a free glow; its gradient bends light
(lyric_glass).  The render thread only uploads finished bakes (one per frame at most, ~1 MB) and draws.

Drawing: an effect lays out GLYPH INSTANCES on the host (one word or line each: its slot, its slot-px span, where on
screen, how big, rotation, alpha, softness, a free per-effect value), uploads them (``Glyphs.set``: a small array, no
allocation) and calls the shared device function ``gi_cover`` from its own kernel (``GLYPH_CUDA``): for one pixel ->
coverage (0..1, union of the instances), glow (the field's soft halo), and the dominant instance's free value.

Fonts are the machine's (deployment.yaml host.fonts_dir: Windows' on the laptop and PC22), used in place, never
copied; without them DejaVu.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger("stream_service.lyric_text")

SLOT_H, SLOT_W = 256, 4096
FONT_PX = 160            # the em in slot px (a longer line bakes smaller: Slot.em)
PAD = 24                 # slot px of field around the text
BLUR_SIGMA = 5.0
SYNC_BAKE = False        # the harness (fxtest): bake inline and upload at once, so a 10-frame render has its text
GI_N = 12                # floats per glyph instance (GLYPH_CUDA's layout)
GI_MAX = 48

FONTS = {   # the machine's fonts first (host.fonts_dir), then the system's
    "black": ("seguibl.ttf", "ariblk.ttf", "impact.ttf", "arialbd.ttf"),
    "bold": ("segoeuib.ttf", "arialbd.ttf", "bahnschrift.ttf"),
    "light": ("segoeuil.ttf", "segoeuisl.ttf", "segoeui.ttf"),
    "serif": ("georgiab.ttf", "timesbd.ttf"),
    "italic": ("georgiai.ttf", "Gabriola.ttf"),
}
FALLBACK = {"serif": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "italic": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "light": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}
FALLBACK_ANY = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_NAMES = tuple(FONTS)


def font_path(kind: str) -> str:
    try:
        import smconfig
        d = smconfig.deployment().host_path("fonts_dir")
    except Exception:                                    # noqa: BLE001
        d = None
    if d is not None and d.is_dir():
        for n in FONTS.get(kind, FONTS["bold"]):
            p = d / n
            if p.is_file():
                return str(p)
    p = FALLBACK.get(kind, FALLBACK_ANY)
    return p if Path(p).is_file() else FALLBACK_ANY


@dataclass
class Slot:
    index: int
    text: str
    width: float                     # slot px used, PAD to PAD + text
    em: float                        # the font size it was baked at (slot px)
    words: list                      # [(x0, x1)] per word (the text split on spaces), slot px
    used: int = 0                    # the frame it was last drawn (eviction: least recently drawn)


def bake(text: str, font, em: float) -> tuple[np.ndarray, float, list]:
    """text -> (field uint8 [SLOT_H, SLOT_W], width used, word spans) at font size em (Pillow; any thread)."""
    from PIL import Image, ImageDraw, ImageFilter
    toks = text.split(" ")
    img = Image.new("L", (SLOT_W, SLOT_H), 0)
    d = ImageDraw.Draw(img)
    top, bot = font.getbbox("Hgjy", anchor="ls")[1::2]
    base = SLOT_H / 2.0 - (top + bot) / 2.0
    d.text((PAD, base), text, fill=255, font=font, anchor="ls")
    spans, x = [], float(PAD)
    space = font.getlength(" ")
    for t in toks:
        w = font.getlength(t)
        spans.append((x, x + w))
        x += w + space
    img = img.filter(ImageFilter.GaussianBlur(BLUR_SIGMA))
    return np.asarray(img, np.uint8).copy(), min(float(SLOT_W), x - space + PAD), spans


class LyricAtlas:
    """A GPU atlas of baked lines (one per effect instance).  ``want`` on the render thread queues bakes (nearest
    first), ``pump`` uploads at most one finished bake per frame, ``get`` -> the Slot or None (not baked yet)."""

    def __init__(self, cp, n_slots: int = 12, font: str = "black") -> None:
        from PIL import ImageFont
        self.cp = cp
        self.n = int(n_slots)
        self.gpu = cp.zeros((self.n, SLOT_H, SLOT_W), cp.uint8)
        self.font_kind = font
        self._path = font_path(font)
        self._fonts: dict[int, object] = {}
        self._ImageFont = ImageFont
        self.slots: dict[str, Slot] = {}
        self._free = list(range(self.n))
        self._queued: set[str] = set()
        self._todo: "queue.Queue[Optional[str]]" = queue.Queue()
        self._done: collections.deque = collections.deque()
        self.frame = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="lyric-bake", daemon=True)
        self._thread.start()

    def _font(self, px: int):
        f = self._fonts.get(px)
        if f is None:
            f = self._fonts[px] = self._ImageFont.truetype(self._path, px)
        return f

    def _run(self) -> None:
        while True:
            text = self._todo.get()
            if text is None or self._closed:
                return
            self._bake(text)

    def _bake(self, text: str) -> None:
        try:
            f = self._font(FONT_PX)
            w = f.getlength(text)
            em = FONT_PX if w <= SLOT_W - 2 * PAD else max(24, int(FONT_PX * (SLOT_W - 2 * PAD) / max(1.0, w)))
            arr, width, spans = bake(text, self._font(int(em)), em)
            self._done.append((text, arr, width, float(em), spans))
        except Exception:                            # noqa: BLE001 - that line draws nothing
            log.exception("lyric bake failed: %r", text[:60])
            self._done.append((text, None, 0.0, float(FONT_PX), []))

    def want(self, texts) -> None:
        """Render thread: bake these (in order) if they aren't baked or queued."""
        for t in texts:
            if t and t not in self.slots and t not in self._queued:
                self._queued.add(t)
                if SYNC_BAKE:
                    self._bake(t)
                else:
                    self._todo.put(t)

    def pump(self) -> None:
        """Render thread, once per frame: the frame counter, and at most one finished bake into a slot."""
        self.frame += 1
        for _ in range(len(self._done) if SYNC_BAKE else 1):
            self._upload_one()

    def _upload_one(self) -> None:
        try:
            text, arr, width, em, spans = self._done.popleft()
        except IndexError:
            return
        self._queued.discard(text)
        if arr is None:
            return
        if not self._free:
            old = min((s for s in self.slots.values() if s.used < self.frame - 1), key=lambda s: s.used, default=None)
            if old is None:                              # all drawn last frame: try again next frame
                self._done.appendleft((text, arr, width, em, spans))
                self._queued.add(text)
                return
            del self.slots[old.text]
            self._free.append(old.index)
        i = self._free.pop()
        self.gpu[i].set(arr)
        self.slots[text] = Slot(i, text, width, em, spans, self.frame)

    def get(self, text: str) -> Optional[Slot]:
        s = self.slots.get(text)
        if s is not None:
            s.used = self.frame
        return s

    def close(self) -> None:
        self._closed = True
        self._todo.put(None)


class Glyphs:
    """The frame's glyph instances (host array -> one small upload).  ``add`` returns False once full."""

    def __init__(self, cp, cap: int = GI_MAX) -> None:
        self.cap = int(cap)
        self.host = np.zeros((self.cap, GI_N), np.float32)
        self.gpu = cp.zeros((self.cap, GI_N), cp.float32)
        self.n = 0

    def clear(self) -> None:
        self.n = 0

    def add(self, slot: Slot, x0: float, x1: float, cx: float, cy: float, scale: float, *, rot: float = 0.0,
            alpha: float = 1.0, soft: float = 0.0, user: float = 0.0, height: Optional[float] = None) -> bool:
        """One word or line: slot px x0..x1 of ``slot``, its centre at screen (cx, cy), ``scale`` screen px per slot
        px, rotated ``rot`` rad, at ``alpha`` (0..1), ``soft`` extra screen px of edge blur, ``user`` for the
        kernel.  ``height``: the drawn rows' half height in slot px (default the slot's)."""
        if self.n >= self.cap or alpha <= 0.002 or scale <= 1e-4:
            return self.n < self.cap
        hw = ((x1 - x0) / 2.0 + PAD) * scale
        hh = (height if height is not None else SLOT_H / 2.0) * scale
        r = float(np.hypot(hw, hh)) + soft * 2.0 + 2.0
        self.host[self.n] = (slot.index, x0, x1, cx, cy, scale, np.cos(rot), np.sin(rot), alpha, soft, r, user)
        self.n += 1
        return True

    def upload(self) -> int:
        if self.n:
            self.gpu[: self.n].set(self.host[: self.n])
        return self.n


GLYPH_CUDA = r"""
#define GI_N 12
#define GI_MAX 48
#define SLOT_H 256
#define SLOT_W 4096
#define GPAD 24.f
#define SIGMA 5.0f
// The display page's own lyrics band (lyric_fx.LYRICS_BAND_TOP): 1 above it, 1 - k inside (eased over feather px).
__device__ __forceinline__ float band_keep(float y, float top, float feather, float k) {
    return 1.f - k * smooth(top - feather, top, y);
}
// one glyph instance: slot, x0, x1 (slot px), cx, cy (screen px), scale (screen px / slot px), cos, sin, alpha, soft,
// radius (screen px bound), user
__device__ __forceinline__ float atlas_v(const unsigned char* __restrict__ A, int slot, float u, float v) {
    u -= 0.5f; v -= 0.5f;
    const int x = (int)floorf(u), y = (int)floorf(v);
    const float fx = u - x, fy = v - y;
    const unsigned char* S = A + (size_t)slot * SLOT_H * SLOT_W;
    const int x0 = max(0, min(SLOT_W - 1, x)), x1 = max(0, min(SLOT_W - 1, x + 1));
    const int y0 = max(0, min(SLOT_H - 1, y)), y1 = max(0, min(SLOT_H - 1, y + 1));
    const float a = S[y0 * SLOT_W + x0], b = S[y0 * SLOT_W + x1], c = S[y1 * SLOT_W + x0], d = S[y1 * SLOT_W + x1];
    return ((a + (b - a) * fx) * (1.f - fy) + (c + (d - c) * fx) * fy) * (1.f / 255.f);
}

// The block's instances into shared memory (call at the top of the kernel, every thread).
#define GI_LOAD(G, n, sh) \
    __shared__ float sh[GI_MAX * GI_N]; \
    { const int tid = threadIdx.y * blockDim.x + threadIdx.x, nt = blockDim.x * blockDim.y; \
      for (int q = tid; q < (n) * GI_N; q += nt) sh[q] = (G)[q]; } \
    __syncthreads();

// One instance's field at a screen pixel: v (0..1, 0.5 at the glyph's edge) or -1 outside its box.
__device__ __forceinline__ float gi_field(const float* g, const unsigned char* __restrict__ A, float px, float py) {
    const float dx = px - g[3], dy = py - g[4];
    if (fabsf(dx) > g[10] || fabsf(dy) > g[10]) return -1.f;
    const float lx = dx * g[6] + dy * g[7], ly = -dx * g[7] + dy * g[6];
    const float u = 0.5f * (g[1] + g[2]) + lx / g[5], v = 0.5f * SLOT_H + ly / g[5];
    if (u < g[1] - GPAD * 0.6f || u > g[2] + GPAD * 0.6f || v < 0.f || v > SLOT_H) return -1.f;
    // fade the field to 0 at the window's edge (a neighbour word's halo never shows as a hard line)
    const float e = fminf(u - (g[1] - GPAD * 0.6f), (g[2] + GPAD * 0.6f) - u);
    return atlas_v(A, (int)g[0], u, v) * clamp01(e / 6.f);
}

// Edge coverage of a field value for instance g (scale, soft): a sharp anti-aliased edge at screen resolution.
__device__ __forceinline__ float gi_edge(const float* g, float f) {
    const float d = (f - 0.5f) * 2.5f * SIGMA * g[5];           // signed screen px from the edge (+ inside)
    return clamp01(d / (1.f + g[9]) + 0.5f);
}

// All n instances at a pixel: union coverage (alpha-weighted), the strongest glow (the field, alpha-weighted) and
// the user value of the instance that covers most.
__device__ __forceinline__ void gi_cover(const float* sh, int n, const unsigned char* __restrict__ A, float px, float py,
                                         float* cover, float* glow, float* user) {
    float c = 0.f, gl = 0.f, best = 0.f, us = 0.f;
    for (int k = 0; k < n; ++k) {
        const float* g = sh + k * GI_N;
        const float f = gi_field(g, A, px, py);
        if (f <= 0.f) continue;
        const float a = gi_edge(g, f) * g[8];
        c = 1.f - (1.f - c) * (1.f - a);
        gl = fmaxf(gl, f * g[8]);
        if (a > best) { best = a; us = g[11]; }
    }
    *cover = c; *glow = gl; *user = us;
}
"""


def word_span(slot: Slot, i: int) -> Optional[tuple[float, float]]:
    return slot.words[i] if 0 <= i < len(slot.words) else None


__all__ = ["LyricAtlas", "Glyphs", "Slot", "GLYPH_CUDA", "SLOT_H", "SLOT_W", "FONT_PX", "PAD", "FONT_NAMES",
           "word_span", "font_path", "bake"]
