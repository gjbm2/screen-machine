"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Text is rasterised by FreeType's monochrome renderer with the font's hinting (DejaVu Sans is fully hinted), so
every stem lands on whole pixels; nothing is drawn larger and scaled, or anti-aliased and thresholded. Sent with
ditherType NONE so the pixels arrive as drawn.

    Normal                       (Auto)(Mic)(AI)     status: filled = on, outlined = off
    ────────────────────────────────────────────
    ♪ Etude in G-flat, Op. 10 No. 5              what's playing, else what the room hears (live),
      Chopin · 1:12 left                         else today's art theme and style
      ▬▬▬▬▬▬▬▬────────────
      Scene: Apollonian foam                     live: the screens' scene and the room's feel
      Dusk · mellow, warm · 86 bpm
    Mic: listening for Auto              19:42   why the mic is on (else the room); when this was drawn
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

W, H = 296, 152
PAD = 8
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
BODY_TOP, BODY_BOTTOM = 27, 136


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
    stamp: str = field(default="", compare=False)   # "19:42"; not part of what changed


_fonts: dict = {}


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    k = (bold, size)
    if k not in _fonts:
        _fonts[k] = ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    return _fonts[k]


def _ellipsis(d: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip(" ,·") + "…"


def _wrap(d: ImageDraw.ImageDraw, text: str, font, width: int, lines: int) -> list[str]:
    """Text in at most `lines` lines of width, the last one ellipsised if it runs over."""
    out: list[str] = []
    words = text.split()
    while words and len(out) < lines:
        line = words.pop(0)
        while words and d.textlength(line + " " + words[0], font=font) <= width:
            line += " " + words.pop(0)
        out.append(line)
    if words:
        out[-1] = out[-1] + " " + " ".join(words)
    return [_ellipsis(d, ln, font, width) for ln in out]


class _Flow:
    """Lines stacked down the body; a line that would cross BODY_BOTTOM is dropped."""

    def __init__(self, d: ImageDraw.ImageDraw) -> None:
        self.d, self.y = d, BODY_TOP

    def text(self, text: str, bold: bool, size: int, lines: int = 1, indent: int = 0, gap: int = 2,
             lead: str = "") -> None:
        if not text:
            return
        f = _font(bold, size)
        x = PAD + indent
        for i, ln in enumerate(_wrap(self.d, text, f, W - PAD - x, lines)):
            if self.y + size > BODY_BOTTOM:
                return
            if lead and i == 0:
                self.d.text((PAD, self.y), lead, font=f, fill=0)
            self.d.text((x, self.y), ln, font=f, fill=0)
            self.y += size + gap

    def bar(self, frac: float, indent: int = 0) -> None:
        if self.y + 6 > BODY_BOTTOM:
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


def _pills(d: ImageDraw.ImageDraw, items: list[tuple[str, bool]]) -> int:
    """Right-aligned status pills in the header: filled = on, outlined = off. Returns their left edge."""
    f = _font(True, 10)
    x = W - PAD
    for label, on in reversed(items):
        w = int(d.textlength(label, font=f)) + 10
        box = (x - w, 3, x, 17)
        if on:
            d.rounded_rectangle(box, radius=7, fill=0)
        else:
            d.rounded_rectangle(box, radius=7, outline=0, fill=1)
        d.text((x - w + 5, 4), label, font=f, fill=1 if on else 0)
        x -= w + 4
    return x


def render(card: Card) -> Image.Image:
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"   # FreeType mono + hinting: crisp at native size

    # Header: the mode (small), and the status that matters in the room.
    left = _pills(d, [("Auto", card.auto), ("Mic", card.mic), ("AI", card.ai)])
    mode = f"{card.changing_from} → {card.mode}" if card.changing_from else card.mode
    d.text((PAD, 3), _ellipsis(d, mode, _font(True, 13), left - PAD - 6), font=_font(True, 13), fill=0)
    d.line((PAD, 21, W - PAD, 21), fill=0)

    flow = _Flow(d)
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
    d.text((PAD, 139), _ellipsis(d, card.note or card.room, small, W - 2 * PAD - 40), font=small, fill=0)
    if card.stamp:
        d.text((W - PAD - d.textlength(card.stamp, font=small), 139), card.stamp, font=small, fill=0)
    return img
