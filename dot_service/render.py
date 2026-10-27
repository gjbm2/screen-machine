"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Type is bitmap-native: fonts drawn on a pixel grid at the one size they are used at, so every glyph is the designer's
pixels, not an outline rasterised and hinted into 1 bit (that left lopsided o's and 0's): Ark Pixel proportional
(OFL, Latin subsets in dot_service/fonts) at 10 px (text), 12 px (status, bottom line) and 16 px (headlines); 12 px
bold = the same glyphs drawn twice a pixel apart (the classic bitmap bold). Glyphs are laid out with FreeType directly (freetype-py)
at whole-pixel advances. Sent with ditherType NONE so the pixels arrive as drawn.

    Today's art                           ┌────┐ what matters now, flowing round the room page's QR code
    The Turning of the Hour               │ QR │ (any phone camera, no app): what's playing, else what the
    Long-exposure, motion-blur photo-     └────┘ room hears (live: scene, feel), else today's art
    graphy in the style of Harry Callahan, using high-contrast silhouettes and light trails to suggest the
    ──────────────────────────────────────────────
    Normal ◷20:58           ● Auto  ● Mic  ○ AI     the mode, when drawn; ● = on
    ███████ Say 'Computer, use AI' to interact ███  art mode: how to use the room
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import freetype
import qrcode
from PIL import Image, ImageChops, ImageDraw

W, H = 296, 152
PAD = 8
FONTS = Path(__file__).resolve().parent / "fonts"
SMALL = (str(FONTS / "ark-pixel-10px-proportional-latin.ttf"), 10, 12)  # path, px, line height: text
BODY = (str(FONTS / "ark-pixel-12px-proportional-latin.ttf"), 12, 14)   # the status row, the bottom line
HEAD = (str(FONTS / "ark-pixel-16px-proportional-latin.ttf"), 16, 19)   # headlines (regular: doubling fills its M, B, s)
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


_faces: dict = {}


def _fonts() -> tuple[_Face, _Face, _Face]:
    """(small, body, head)."""
    if not _faces:
        _faces["body"] = _Face(*BODY)
        _faces["small"] = _Face(*SMALL, fallback=_faces["body"])
        _faces["head"] = _Face(*HEAD, fallback=_faces["body"])
    return _faces["small"], _faces["body"], _faces["head"]


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
    """Lines stacked down from the top, flowing round the QR code (top right): a line in its rows stops left of
    it. What doesn't fit above `bottom` is cut, the last line ellipsised."""

    def __init__(self, img: Image.Image, bottom: int, narrow_until: int, narrow_right: int) -> None:
        self.img, self.d, self.y, self.bottom = img, ImageDraw.Draw(img), 4, bottom
        self.narrow_until, self.narrow_right = narrow_until, narrow_right

    def right(self, h: int) -> int:
        return self.narrow_right if self.y < self.narrow_until else W - PAD

    def text(self, text: str, kind: str = "small", lines: int = 1, bold: bool = False) -> None:
        if not text:
            return
        small, body, head = _fonts()
        f = {"small": small, "body": body, "head": head}[kind]
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
    """The smallest QR for url. A bare scheme + host is case-free, so it goes in upper case: QR's alphanumeric
    mode packs it denser (https://screen.finsburybankforsavings.com: 25 modules, not 29)."""
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0, box_size=px)
    bare = url.split("://", 1)[-1].rstrip("/")
    q.add_data(url.rstrip("/").upper() if "/" not in bare else url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("1")


def _strip_text(img: Image.Image, f: _Face, text: str, top: int, bottom: int) -> None:
    """White bold text centred, by its ink, in the black band top..bottom."""
    probe = Image.new("1", (W, 40), 1)
    f.draw(probe, (0, 10), text, bold=True)
    x0, y0, x1, y1 = ImageChops.invert(probe.convert("L")).getbbox() or (0, 10, 0, 10)
    y = top + (bottom - top + 1 - (y1 - y0)) // 2 - (y0 - 10)
    f.draw(img, ((W - (x1 - x0)) // 2 - x0, y), text, fill=1, bold=True)


def render(card: Card) -> Image.Image:
    """Top to bottom: what matters now (flowing round the room page's QR code, top right), the room's status,
    then (art mode) how to use the room."""
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    small, body, head = _fonts()

    # The QR code, top right against the glass edge (the white bezel is the rest of its quiet zone).
    right, qr_until = W - PAD, 0
    if card.qr:
        q = _qr(card.qr, card.qr_px)
        qx = W - EDGE - q.width
        img.paste(q, (qx, EDGE))
        right = qx - 4 * card.qr_px                  # a 4-module quiet zone on its text side
        qr_until = EDGE + q.height + 4 * card.qr_px  # and below it

    # Bottom line (art mode): how to use the room, white on black, centred.
    y = H
    if card.hint:
        y = H - 18
        d.rectangle((0, y, W - 1, H - 1), fill=0)
        t = _ellipsis(body, card.hint, W - 2 * PAD, True)
        _strip_text(img, body, t, y, H - 1)

    # Status row, under a rule: the mode, when this was drawn, Auto / Mic / AI.
    row = y - 18 if card.hint else H - 17          # descenders end above the band / on the last row
    d.line((PAD, row - 3, W - PAD, row - 3), fill=0)
    x = W - PAD
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
    flow = _Flow(img, bottom=row - 5, narrow_until=qr_until, narrow_right=right)
    if card.title:
        flow.text(card.title, "head", lines=2)
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
            flow.text(card.style, lines=4)
    elif card.live:
        flow.text("Hearing")
        flow.text(card.hearing or "—", "head")
        flow.space(2)
        flow.text(f"Scene: {card.scene}" if card.scene else "")
        flow.text(card.feel)
        flow.text(f"Art: {card.theme}" if card.theme else "")
    else:
        flow.text("Today's art")
        flow.text(card.theme, "head", lines=2)
        flow.space(2)
        flow.text(card.style, lines=6)

    return img
