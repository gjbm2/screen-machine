"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Text is laid out and rasterised here with FreeType directly (freetype-py): each glyph rendered monochrome with the
font's hinting (DejaVu Sans is fully hinted) and placed by its hinted whole-pixel advance plus kerning, the way
1-bit screens were always done. Pillow's own text path places hinted glyphs at unhinted fractional advances
(uneven gaps: "m otion"), so it isn't used for text. Nothing is drawn larger and scaled, or anti-aliased and
thresholded. Sent with ditherType NONE so the pixels arrive as drawn.

    Etude in G-flat, Op. 10 No. 5                what matters now: what's playing, else what the room
      Chopin · 1:12 left                         hears (live), else today's art theme and style
      ▬▬▬▬▬▬▬▬────────────
      Scene: Apollonian foam                     live: the screens' scene and the room's feel
      Dusk · mellow, warm · 86 bpm
    ────────────────────────────────────────────
    Normal updated 19:42       ● Auto  ● Mic  ○ AI    the mode (→ Party while switching), when drawn; ● = on
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Say "Computer, use AI"         Tap phone ▶     art mode: how to use the room (the tap area is right of the screen)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import freetype
from PIL import Image, ImageDraw

W, H = 296, 152
PAD = 8
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
# Small labels (the strip, the status switches): Verdana, drawn for small sizes on screens (wide spacing, open
# counters, so it survives e-ink bleed); DejaVu if Windows' fonts aren't mounted.
_VERDANA_BOLD = "/mnt/c/Windows/Fonts/verdanab.ttf"
_VERDANA = "/mnt/c/Windows/Fonts/verdana.ttf"
FONT_LABEL = _VERDANA_BOLD if os.path.exists(_VERDANA_BOLD) else FONT_BOLD
FONT_LABEL_REGULAR = _VERDANA if os.path.exists(_VERDANA) else FONT
BODY_TOP, BODY_BOTTOM = 5, H - 3
STRIP_TOP = 131                     # the art-mode instruction strip: 131..151


@dataclass(frozen=True)
class Card:
    room: str                       # "Drawing room" (the task name in the Dot. App)
    mode: str                       # "Normal"; the target while a switch runs
    auto: bool = False
    mic: bool = False               # the room mic is capturing
    ai: bool = False                # AI listening is on
    live: bool = False              # a screen of the room is live
    changing_from: str = ""
    title: str = ""                 # what is playing ("" = nothing)
    by: str = ""
    left: str = ""                  # "1:12 left"
    progress: Optional[float] = None
    paused: bool = False
    hearing: str = ""               # live, nothing playing: what the room hears ("Piano", "Quiet")
    scene: str = ""                 # live: "Apollonian foam" or "A / B"
    feel: str = ""                  # live: "Dusk · mellow, warm · 86 bpm"
    theme: str = ""                 # today's art theme
    style: str = ""                 # today's art style
    say: str = ""                   # bottom strip, art mode: the voice command ('Computer, use AI')
    tap: str = ""                   # bottom strip: the NFC hint, pointing right to the tag's tap area
    stamp: str = field(default="", compare=False)   # "19:42"; not part of what changed


class _Face:
    """One font at one pixel size: hinted monochrome glyphs at whole-pixel advances, with kerning."""

    FLAGS = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO

    def __init__(self, path: str, px: int) -> None:
        self.face = freetype.Face(path)
        self.face.set_pixel_sizes(0, px)
        self.ascender = self.face.size.ascender >> 6
        self.glyphs: dict[str, tuple[int, int, int, int, Optional[Image.Image]]] = {}

    def _glyph(self, ch: str) -> tuple[int, int, int, int, Optional[Image.Image]]:
        """(glyph index, advance px, left bearing, top bearing, 1-bit mask or None)."""
        g = self.glyphs.get(ch)
        if g is None:
            gi = self.face.get_char_index(ch)
            self.face.load_glyph(gi, self.FLAGS)
            slot = self.face.glyph
            bm = slot.bitmap
            mask = (Image.frombytes("1", (bm.width, bm.rows), bytes(bm.buffer), "raw", "1", bm.pitch, 1)
                    if bm.width and bm.rows else None)
            g = (gi, (slot.advance.x + 32) >> 6, slot.bitmap_left, slot.bitmap_top, mask)
            self.glyphs[ch] = g
        return g

    def _kern(self, prev: int, gi: int) -> int:
        if not self.face.has_kerning:
            return 0
        return (self.face.get_kerning(prev, gi, freetype.FT_KERNING_DEFAULT).x + 32) >> 6

    def length(self, text: str) -> int:
        x, prev = 0, None
        for ch in text:
            gi, adv, *_ = self._glyph(ch)
            x += (self._kern(prev, gi) if prev is not None else 0) + adv
            prev = gi
        return x

    def draw(self, img: Image.Image, xy: tuple[float, float], text: str, fill: int = 0) -> None:
        """Text with its ascender line at y (Pillow's default anchor)."""
        x, y = int(xy[0]), int(xy[1])
        prev = None
        for ch in text:
            gi, adv, left, top, mask = self._glyph(ch)
            if prev is not None:
                x += self._kern(prev, gi)
            if mask is not None:
                img.paste(fill, (x + left, y + self.ascender - top), mask)
            x += adv
            prev = gi


_fonts: dict = {}


def _font(bold: bool, size: int, path: str = "") -> _Face:
    path = path or (FONT_BOLD if bold else FONT)
    k = (path, size)
    if k not in _fonts:
        _fonts[k] = _Face(path, size)
    return _fonts[k]


def _ellipsis(d: ImageDraw.ImageDraw, text: str, font: _Face, width: int) -> str:
    if font.length(text) <= width:
        return text
    while text and font.length(text + "…") > width:
        text = text[:-1]
    return text.rstrip(" ,·") + "…"


def _wrap(d: ImageDraw.ImageDraw, text: str, font: _Face, width: int, lines: int) -> list[str]:
    """Text in at most `lines` lines of width, the last one ellipsised if it runs over."""
    out: list[str] = []
    words = text.split()
    while words and len(out) < lines:
        line = words.pop(0)
        while words and font.length(line + " " + words[0]) <= width:
            line += " " + words.pop(0)
        out.append(line)
    if words:
        out[-1] = out[-1] + " " + " ".join(words)
    return [_ellipsis(d, ln, font, width) for ln in out]


class _Flow:
    """Lines stacked down the body; what doesn't fit above `bottom` is cut, the last line kept ellipsised."""

    def __init__(self, img: Image.Image, bottom: int = BODY_BOTTOM) -> None:
        self.img, self.d, self.y, self.bottom = img, ImageDraw.Draw(img), BODY_TOP, bottom

    def text(self, text: str, bold: bool, size: int, lines: int = 1, indent: int = 0, gap: int = 2,
             lead: str = "") -> None:
        if not text:
            return
        f = _font(bold, size)
        x = PAD + indent
        lines = min(lines, (self.bottom - self.y + gap) // (size + gap))
        if lines < 1:
            return
        for i, ln in enumerate(_wrap(self.d, text, f, W - PAD - x, lines)):
            if lead and i == 0:
                f.draw(self.img, (PAD, self.y), lead)
            f.draw(self.img, (x, self.y), ln)
            self.y += size + gap

    def bar(self, frac: float, indent: int = 0) -> None:
        if self.y + 6 > self.bottom:
            return
        x0, x1 = PAD + indent, W - PAD
        y = self.y + 2
        self.d.rectangle((x0, y, x1, y + 3), outline=0, fill=1)
        fx = x0 + round((x1 - x0) * max(0.0, min(1.0, frac)))
        if fx > x0:
            self.d.rectangle((x0, y, fx, y + 3), fill=0)
        self.y += 10

    def space(self, px: int) -> None:
        self.y += px


_DOT_ON = ["..xxxxx..", ".xxxxxxx.", "xxxxxxxxx", "xxxxxxxxx", "xxxxxxxxx", "xxxxxxxxx", "xxxxxxxxx",
           ".xxxxxxx.", "..xxxxx.."]
_DOT_OFF = ["..xxxxx..", ".xx...xx.", "xx.....xx", "x.......x", "x.......x", "x.......x", "xx.....xx",
            ".xx...xx.", "..xxxxx.."]


def _dot(img: Image.Image, x: int, y: int, on: bool) -> None:
    """A 9-px dot drawn pixel by pixel (Pillow's small ellipses come out lumpy at 1 bit)."""
    for r, row in enumerate(_DOT_ON if on else _DOT_OFF):
        for c, ch in enumerate(row):
            if ch == "x":
                img.putpixel((x + c, y + r), 0)


def _pills(img: Image.Image, items: list[tuple[str, bool]], top: int) -> int:
    """Right-aligned status switches, black on white (white-on-black small text bleeds shut on e-ink): on = a
    filled dot and bold label, off = a hollow dot and plain label. Returns their left edge."""
    x = W - PAD
    for label, on in reversed(items):
        f = _font(on, 11, FONT_LABEL if on else FONT_LABEL_REGULAR)
        x0 = x - f.length(label) - 13
        _dot(img, x0, top + 4, on)
        f.draw(img, (x0 + 13, top + 1), label)
        x = x0 - 10
    return x


def render(card: Card) -> Image.Image:
    """Top to bottom: what matters now, then the room's status, then (art mode) how to use the room."""
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)

    # Bottom strip (art mode): how to use the room, for whoever picks the tag up.
    strip = bool(card.say or card.tap)
    if strip:   # black on white under a heavy rule: reversed small text bleeds shut on e-ink
        d.rectangle((0, STRIP_TOP, W - 1, STRIP_TOP + 2), fill=0)
        f = _font(True, 12, FONT_LABEL)
        ty = STRIP_TOP + 5
        if card.say:
            f.draw(img, (PAD, ty), _ellipsis(d, f'Say "{card.say}"', f, 185))
        if card.tap:   # an arrow at the right edge, at the tag's NFC area
            ax = W - PAD
            d.polygon([(ax - 6, ty + 2), (ax, ty + 6), (ax - 6, ty + 10)], fill=0)
            f.draw(img, (ax - 11 - f.length(card.tap), ty), card.tap)

    # Status row, under a rule: the mode, when this was drawn, and Auto / Mic / AI.
    row = (STRIP_TOP if strip else H) - 19
    d.line((PAD, row - 4, W - PAD, row - 4), fill=0)
    left = _pills(img, [("Auto", card.auto), ("Mic", card.mic), ("AI", card.ai)], row)
    mode = f"→ {card.mode}" if card.changing_from else card.mode
    mf, tf = _font(True, 13), _font(False, 11)
    stamp = f"updated {card.stamp}" if card.stamp else ""
    if stamp and mf.length(mode) + 6 + tf.length(stamp) > left - PAD - 6:
        stamp = card.stamp
    room_for = left - PAD - 6 - (tf.length(stamp) + 6 if stamp else 0)
    mode = _ellipsis(d, mode, mf, room_for)
    mf.draw(img, (PAD, row), mode)
    if stamp:
        tf.draw(img, (PAD + mf.length(mode) + 6, row + 2), stamp)

    # What matters now, from the top.
    flow = _Flow(img, bottom=row - 8)
    if card.title:
        flow.text(card.title, True, 16, lines=2)
        flow.text(" · ".join(x for x in (card.by, "paused" if card.paused else card.left) if x), False, 12)
        if card.progress is not None:
            flow.bar(card.progress)
        flow.space(3)
        if card.live:
            flow.text(f"Scene: {card.scene}" if card.scene else "", False, 12)
            flow.text(card.feel, False, 12)
            flow.text(f"Art: {card.theme}" if card.theme else "", False, 12)
        else:
            flow.text(f"Today's art: {card.theme}" if card.theme else "", False, 12)
            flow.text(card.style, False, 12, lines=3)
    elif card.live:
        flow.text("Hearing", False, 11)
        flow.text(card.hearing or "—", True, 16)
        flow.space(2)
        flow.text(f"Scene: {card.scene}" if card.scene else "", False, 12)
        flow.text(card.feel, False, 12)
        flow.text(f"Art: {card.theme}" if card.theme else "", False, 12)
    else:
        flow.text("Today's art", False, 11)
        flow.text(card.theme, True, 16, lines=2)
        flow.space(2)
        flow.text(card.style, False, 12, lines=4)

    return img
