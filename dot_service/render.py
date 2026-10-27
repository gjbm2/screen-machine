"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Text is laid out and rasterised here with FreeType directly (freetype-py): each glyph rendered monochrome with the
font's hinting (DejaVu Sans is fully hinted) and placed by its hinted whole-pixel advance plus kerning, the way
1-bit screens were always done. Pillow's own text path places hinted glyphs at unhinted fractional advances
(uneven gaps: "m otion"), so it isn't used for text. Nothing is drawn larger and scaled, or anti-aliased and
thresholded. Sent with ditherType NONE so the pixels arrive as drawn.

    Normal                       (Auto)(Mic)(AI)     status: filled = on, outlined = off
    ────────────────────────────────────────────
    ♪ Etude in G-flat, Op. 10 No. 5              what's playing, else what the room hears (live),
      Chopin · 1:12 left                         else today's art theme and style
      ▬▬▬▬▬▬▬▬────────────
      Scene: Apollonian foam                     live: the screens' scene and the room's feel
      Dusk · mellow, warm · 86 bpm
    Mic: listening for Auto              19:42   why the mic is on (else the room); when this was drawn
    ███ Say “Computer, use AI”   Tap phone ▶ ███   art mode: how to use the room (the tap area is right of the screen)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import freetype
from PIL import Image, ImageDraw

W, H = 296, 152
PAD = 8
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
BODY_TOP, BODY_BOTTOM = 27, 136
STRIP_TOP = 134                     # the art-mode instruction strip: 134..151


@dataclass(frozen=True)
class Card:
    room: str                       # footer: "Drawing room"
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
    note: str = ""                  # footer: why the mic is on ("Mic: listening for Auto"), else the room
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


def _font(bold: bool, size: int) -> _Face:
    k = (bold, size)
    if k not in _fonts:
        _fonts[k] = _Face(FONT_BOLD if bold else FONT, size)
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


def _pills(img: Image.Image, items: list[tuple[str, bool]]) -> int:
    """Right-aligned status pills in the header: filled = on, outlined = off. Returns their left edge."""
    d, f = ImageDraw.Draw(img), _font(True, 10)
    x = W - PAD
    for label, on in reversed(items):
        w = f.length(label) + 10
        box = (x - w, 3, x, 17)
        if on:
            d.rounded_rectangle(box, radius=7, fill=0)
        else:
            d.rounded_rectangle(box, radius=7, outline=0, fill=1)
        f.draw(img, (x - w + 5, 4), label, fill=1 if on else 0)
        x -= w + 4
    return x


def render(card: Card) -> Image.Image:
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)

    # Header: the mode (small), and the status that matters in the room.
    left = _pills(img, [("Auto", card.auto), ("Mic", card.mic), ("AI", card.ai)])
    mode = f"{card.changing_from} → {card.mode}" if card.changing_from else card.mode
    _font(True, 13).draw(img, (PAD, 3), _ellipsis(d, mode, _font(True, 13), left - PAD - 6))
    d.line((PAD, 21, W - PAD, 21), fill=0)

    # Bottom strip (art mode): how to use the room, for whoever picks the tag up.
    strip = bool(card.say or card.tap)
    if strip:
        d.rectangle((0, STRIP_TOP, W - 1, H - 1), fill=0)
        f = _font(True, 11)
        if card.say:
            f.draw(img, (PAD, STRIP_TOP + 3), _ellipsis(d, f"Say “{card.say}”", f, 180), fill=1)
        if card.tap:
            t = f"{card.tap} ▶"
            f.draw(img, (W - PAD - f.length(t), STRIP_TOP + 3), t, fill=1)
    foot_y = STRIP_TOP - 15 if strip else 139

    flow = _Flow(img, bottom=foot_y - 3)
    if card.title:
        lead = "‖" if card.paused else "♪"
        flow.text(card.title, True, 16, lines=2, indent=16, lead=lead)
        flow.text(" · ".join(x for x in (card.by, card.left) if x), False, 12, indent=16)
        if card.progress is not None:
            flow.bar(card.progress, indent=16)
        flow.space(2)
        if card.live:
            flow.text(f"Scene: {card.scene}" if card.scene else "", False, 12)
            flow.text(card.feel, False, 12)
        flow.text(f"Art: {card.theme}" if card.theme else "", False, 12)
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

    # Footer: why the mic is on (else whose tag), and when it was drawn.
    small = _font(False, 11)
    small.draw(img, (PAD, foot_y), _ellipsis(d, card.note or card.room, small, W - 2 * PAD - 40))
    if card.stamp:
        small.draw(img, (W - PAD - small.length(card.stamp), foot_y), card.stamp)
    return img
