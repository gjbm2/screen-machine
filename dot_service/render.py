"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Type is bitmap-native: fonts drawn on a pixel grid at the one size they are used at, so every glyph is the designer's
pixels, not an outline rasterised and hinted into 1 bit (that left lopsided o's and 0's). Body: Ark Pixel 12 px
proportional (OFL); bold = the same glyphs drawn twice a pixel apart (the classic bitmap bold). Headlines: Pixel
Operator Bold 16 px (CC0). Both live in dot_service/fonts. Glyphs are laid out with FreeType directly (freetype-py)
at whole-pixel advances. Sent with ditherType NONE so the pixels arrive as drawn.

    Etude in G-flat, Op. 10 No. 5                what matters now: what's playing, else what the room
    Chopin · 1:12 left                           hears (live), else today's art theme and style
    ▬▬▬▬▬▬▬▬────────────
    Scene: Apollonian foam                ┌────┐ live: the screens' scene and the room's feel
    ───────────────────────────────────── │ QR │ the room page, for any phone camera (no app)
    Normal ◷20:58     ● Auto ● Mic ○ AI   └────┘ the mode, when drawn; ● = on
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
          Say 'Computer, use AI' to interact       art mode: how to use the room
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import freetype
import qrcode
from PIL import Image, ImageDraw

W, H = 296, 152
PAD = 8
FONTS = Path(__file__).resolve().parent / "fonts"
BODY = (str(FONTS / "ark-pixel-12px-proportional-latin.ttf"), 12, 14)   # path, px, line height
HEAD = (str(FONTS / "PixelOperator-Bold.ttf"), 16, 17)
EDGE = 4                                   # the QR's white margin to the glass edge (the white bezel adds more)


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
    hint: str = ""                  # bottom line, art mode: "Say 'Computer, use AI' to interact"
    qr: str = ""                    # bottom-right QR code: this URL ("" = none)
    qr_px: int = 2                  # pixels per QR module
    stamp: str = field(default="", compare=False)   # "19:42"; not part of what changed


class _Face:
    """A bitmap-grid font at its design size: glyph masks at whole-pixel advances, with kerning; `fallback`
    supplies glyphs the font lacks."""

    FLAGS = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO

    def __init__(self, path: str, px: int, line: int, fallback: Optional["_Face"] = None) -> None:
        self.face = freetype.Face(path)
        self.face.set_pixel_sizes(0, px)
        self.px, self.line, self.fallback = px, line, fallback
        self.ascender = self.face.size.ascender >> 6
        self.glyphs: dict = {}

    def _glyph(self, ch: str):
        """(face, glyph index, advance, left, top, mask or None); top is relative to this face's ascender."""
        g = self.glyphs.get(ch)
        if g is None:
            gi = self.face.get_char_index(ch)
            if gi == 0 and self.fallback is not None:
                f, fgi, adv, left, top, mask = self.fallback._glyph(ch)
                g = (f, fgi, adv, left, top - self.fallback.ascender + self.ascender, mask)
            else:
                self.face.load_glyph(gi, self.FLAGS)
                slot = self.face.glyph
                bm = slot.bitmap
                mask = (Image.frombytes("1", (bm.width, bm.rows), bytes(bm.buffer), "raw", "1", bm.pitch, 1)
                        if bm.width and bm.rows else None)
                g = (self, gi, (slot.advance.x + 32) >> 6, slot.bitmap_left, slot.bitmap_top, mask)
            self.glyphs[ch] = g
        return g

    def _kern(self, prev, cur) -> int:
        if prev is None or prev[0] is not cur[0] or not cur[0].face.has_kerning:
            return 0
        return (cur[0].face.get_kerning(prev[1], cur[1], freetype.FT_KERNING_DEFAULT).x + 32) >> 6

    def length(self, text: str, bold: bool = False) -> int:
        x, prev = 0, None
        for ch in text:
            g = self._glyph(ch)
            x += self._kern(prev, g) + g[2] + (1 if bold else 0)
            prev = g
        return x

    def draw(self, img: Image.Image, xy: tuple[float, float], text: str, fill: int = 0, bold: bool = False) -> None:
        """Text with its ascender line at y."""
        x, y = int(xy[0]), int(xy[1])
        prev = None
        for ch in text:
            g = self._glyph(ch)
            x += self._kern(prev, g)
            _, _, adv, left, top, mask = g
            if mask is not None:
                for dx in ((0, 1) if bold else (0,)):
                    img.paste(fill, (x + left + dx, y + self.ascender - top), mask)
            x += adv + (1 if bold else 0)
            prev = g


_body: Optional[_Face] = None
_head: Optional[_Face] = None


def _fonts() -> tuple[_Face, _Face]:
    global _body, _head
    if _body is None:
        _body = _Face(*BODY)
        _head = _Face(*HEAD, fallback=_body)
    return _body, _head


def _ellipsis(f: _Face, text: str, width: int, bold: bool = False) -> str:
    if f.length(text, bold) <= width:
        return text
    while text and f.length(text + "…", bold) > width:
        text = text[:-1]
    return text.rstrip(" ,·") + "…"


def _pixels(img: Image.Image, x: int, y: int, rows: list[str]) -> None:
    """A small icon drawn pixel by pixel (Pillow's small shapes come out lumpy at 1 bit)."""
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == "x":
                img.putpixel((x + c, y + r), 0)


_DOT_ON = ["..xxx..", ".xxxxx.", "xxxxxxx", "xxxxxxx", "xxxxxxx", ".xxxxx.", "..xxx.."]
_DOT_OFF = ["..xxx..", ".x...x.", "x.....x", "x.....x", "x.....x", ".x...x.", "..xxx.."]
_CLOCK = ["..xxxxx..", ".x.....x.", "x...x...x", "x...x...x", "x...xxx.x", "x.......x", "x.......x", ".x.....x.",
          "..xxxxx.."]


class _Flow:
    """Lines stacked down from the top; a line that reaches the QR's rows is kept left of it; what doesn't fit
    above `bottom` is cut, the last line ellipsised."""

    def __init__(self, img: Image.Image, bottom: int, narrow_from: int, narrow_right: int) -> None:
        self.img, self.d, self.y, self.bottom = img, ImageDraw.Draw(img), 5, bottom
        self.narrow_from, self.narrow_right = narrow_from, narrow_right

    def right(self, h: int) -> int:
        return self.narrow_right if self.y + h > self.narrow_from else W - PAD

    def text(self, text: str, head: bool = False, lines: int = 1, bold: bool = False) -> None:
        if not text:
            return
        body, hd = _fonts()
        f = hd if head else body
        words = text.split()
        n = 0
        while words and n < lines and self.y + f.px <= self.bottom:
            width = self.right(f.line) - PAD
            line = words.pop(0)
            while words and f.length(line + " " + words[0], bold) <= width:
                line += " " + words.pop(0)
            last = n == lines - 1 or self.y + f.line + f.px > self.bottom
            if words and last:
                line = _ellipsis(f, line + " " + " ".join(words), width, bold)
                words = []
            f.draw(self.img, (PAD, self.y), _ellipsis(f, line, width, bold), bold=bold)
            self.y += f.line
            n += 1

    def bar(self, frac: float) -> None:
        if self.y + 6 > self.bottom:
            return
        x0, x1 = PAD, self.right(6)
        y = self.y + 1
        self.d.rectangle((x0, y, x1, y + 3), outline=0, fill=1)
        fx = x0 + round((x1 - x0) * max(0.0, min(1.0, frac)))
        if fx > x0:
            self.d.rectangle((x0, y, fx, y + 3), fill=0)
        self.y += 9

    def space(self, px: int) -> None:
        self.y += px


def _qr(url: str, px: int) -> Image.Image:
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0, box_size=px)
    q.add_data(url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("1")


def render(card: Card) -> Image.Image:
    """Top to bottom: what matters now, then the room's status, then (art mode) how to use the room; the room
    page's QR code in the bottom-right corner."""
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    body, _ = _fonts()

    # Bottom line (art mode): how to use the room, centred across the tag, under a heavy rule.
    y = H
    if card.hint:
        y = H - 20                                   # the rule; the text's descenders end on the last row
        d.rectangle((PAD, y, W - PAD, y + 1), fill=0)
        bold = body.length(card.hint, True) <= W - 2 * PAD
        t = _ellipsis(body, card.hint, W - 2 * PAD, bold)
        body.draw(img, ((W - body.length(t, bold)) // 2, H - 17), t, bold=bold)

    # The room page's QR code, right, above the bottom line (against the glass edge when there is none: the
    # white bezel is the rest of its quiet zone).
    right, qr_top = W - PAD, H
    if card.qr:
        q = _qr(card.qr, card.qr_px)
        qx = W - EDGE - q.width
        qr_top = (y - 2 * card.qr_px - q.height) if card.hint else H - EDGE - q.height
        img.paste(q, (qx, qr_top))
        right = qx - 4 * card.qr_px                 # a 4-module quiet zone on its text side

    # Status row, under a rule: the mode, when this was drawn, Auto / Mic / AI.
    row = y - 18 if card.hint else H - 17           # descenders end just above the rule / on the last row
    d.line((PAD, row - 3, right, row - 3), fill=0)
    x = right
    for label, on in reversed([("Auto", card.auto), ("Mic", card.mic), ("AI", card.ai)]):
        x0 = x - body.length(label, on) - 10
        _pixels(img, x0, row + 4, _DOT_ON if on else _DOT_OFF)
        body.draw(img, (x0 + 10, row), label, bold=on)
        x = x0 - 8
    stamp_w = body.length(card.stamp) + 12 if card.stamp else 0
    mode = f"→ {card.mode}" if card.changing_from else card.mode
    mode = _ellipsis(body, mode, x - PAD - stamp_w - 6, True)
    body.draw(img, (PAD, row), mode, bold=True)
    if card.stamp:   # a small clock and the time it was drawn
        sx = PAD + body.length(mode, True) + 7
        _pixels(img, sx, row + 3, _CLOCK)
        body.draw(img, (sx + 12, row), card.stamp)

    # What matters now, from the top.
    flow = _Flow(img, bottom=row - 6, narrow_from=qr_top - 4 * card.qr_px, narrow_right=right)
    if card.title:
        flow.text(card.title, head=True, lines=2)
        flow.text(" · ".join(x for x in (card.by, "paused" if card.paused else card.left) if x))
        if card.progress is not None:
            flow.bar(card.progress)
        flow.space(3)
        if card.live:
            flow.text(f"Scene: {card.scene}" if card.scene else "")
            flow.text(card.feel)
            flow.text(f"Art: {card.theme}" if card.theme else "")
        else:
            flow.text(f"Today's art: {card.theme}" if card.theme else "")
            flow.text(card.style, lines=3)
    elif card.live:
        flow.text("Hearing")
        flow.text(card.hearing or "—", head=True)
        flow.space(2)
        flow.text(f"Scene: {card.scene}" if card.scene else "")
        flow.text(card.feel)
        flow.text(f"Art: {card.theme}" if card.theme else "")
    else:
        flow.text("Today's art")
        flow.text(card.theme, head=True, lines=2)
        flow.space(2)
        flow.text(card.style, lines=4)

    return img
