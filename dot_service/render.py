"""The tag's card: 296x152, black and white (Dot. Quote/0).

    Drawing room                 [AUTO]
    Normal                       (the mode, as big as fits)
    ─────────────────────────────────
    ♪ Etude in G-flat, Op. 10 No. 5
      Chopin                     (nothing playing: the quiet line, e.g. today's art theme)

Drawn in greyscale and thresholded here, then sent with ditherType NONE, so text stays crisp.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

W, H = 296, 152
PAD = 10
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@dataclass(frozen=True)
class Card:
    room: str                 # "Drawing room"
    mode: str                 # "Normal"; the target while a switch runs
    auto: bool
    changing_from: str = ""   # set while a mode switch runs
    title: str = ""           # what is playing ("" = nothing)
    by: str = ""              # composer / artist
    paused: bool = False
    quiet_label: str = ""     # shown when nothing plays, e.g. "Today's theme"
    quiet_text: str = ""      # e.g. the scheduler's art theme of the day


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _fit(draw: ImageDraw.ImageDraw, text: str, path: str, sizes: range, width: int):
    """The biggest font in sizes that fits text in width, else the smallest with text ellipsised."""
    for s in sizes:
        f = _font(path, s)
        if draw.textlength(text, font=f) <= width:
            return f, text
    f = _font(path, sizes[-1])
    return f, _ellipsis(draw, text, f, width)


def _ellipsis(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, lines: int) -> list[str]:
    """Text in at most `lines` lines of width, the last one ellipsised if it runs over."""
    out: list[str] = []
    words = text.split()
    while words and len(out) < lines:
        line = words.pop(0)
        while words and draw.textlength(line + " " + words[0], font=font) <= width:
            line += " " + words.pop(0)
        out.append(line)
    if words:
        out[-1] = _ellipsis(draw, out[-1] + " " + " ".join(words), font, width)
    return [_ellipsis(draw, l, font, width) for l in out]


def render(card: Card) -> Image.Image:
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    inner = W - 2 * PAD

    # Header: room name, and AUTO as a black pill when Auto is on.
    small = _font(FONT, 14)
    d.text((PAD, 7), card.room, font=small, fill=0)
    if card.auto:
        pill = _font(FONT_BOLD, 11)
        tw = d.textlength("AUTO", font=pill)
        x1 = W - PAD
        x0 = x1 - tw - 12
        d.rounded_rectangle((x0, 7, x1, 23), radius=8, fill=0)
        d.text((x0 + 6, 9), "AUTO", font=pill, fill=255)

    # The mode, as big as fits.
    f, text = _fit(d, card.mode, FONT_BOLD, range(40, 21, -2), inner)
    top = 30 if not card.changing_from else 27
    d.text((PAD, top), text, font=f, fill=0)
    if card.changing_from:
        sub = _font(FONT, 12)
        d.text((PAD, top + f.size + 8), _ellipsis(d, f"changing from {card.changing_from}", sub, inner),
               font=sub, fill=0)

    d.line((PAD, 94, W - PAD, 94), fill=0, width=1)

    # What is playing.
    if card.title:
        note = _font(FONT_BOLD, 17)
        glyph = "‖" if card.paused else "♪"
        d.text((PAD, 102), glyph, font=note, fill=0)
        gx = PAD + 18
        t = _font(FONT_BOLD, 16)
        d.text((gx, 102), _ellipsis(d, card.title, t, W - PAD - gx), font=t, fill=0)
        if card.by:
            b = _font(FONT, 14)
            d.text((gx, 124), _ellipsis(d, card.by, b, W - PAD - gx), font=b, fill=0)
    elif card.quiet_text:
        d.text((PAD, 99), card.quiet_label or "Today", font=_font(FONT, 12), fill=0)
        t = _font(FONT_BOLD, 15)
        for i, line in enumerate(_wrap(d, card.quiet_text, t, inner, 2)):
            d.text((PAD, 114 + i * 18), line, font=t, fill=0)
    else:
        d.text((PAD, 112), "Nothing playing", font=_font(FONT, 15), fill=0)

    return img.point(lambda v: 255 if v >= 128 else 0).convert("1")
