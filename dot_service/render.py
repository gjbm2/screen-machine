"""The tag's card: exactly 296x152, black and white (Dot. Quote/0), drawn 1:1 at that size.

Type is bitmap-native: fonts drawn on a pixel grid at the one size they are used at, so every glyph is the designer's
pixels, not an outline rasterised and hinted into 1 bit (that left lopsided o's and 0's). OFL fonts, Latin subsets
in dot_service/fonts: Galmuri 9 at 10 px (text, switches), Galmuri 11 and 11 Bold at 12 px (the mode, the bottom
line), Galmuri 14 at 15 px, pixel-doubled bold (headlines). Glyphs are laid out with FreeType directly (freetype-py) at whole-pixel
advances. Sent with ditherType NONE so the pixels arrive as drawn.

    screen/machine | Today's art          │  Normal      the right-hand column, the QR code's width, all
    · · · · · · · · · · · · · · · · · · · │ ◷ 20:58      centred, behind a rule: the mode, when this was drawn,
    The Turning of the Hour               │  ■▫ Auto     Auto / Mic / AI as small switches (knob right
    in the style of Harry Callahan, using    ■▫ Mic      on black = on); the mode may wrap to two lines
    high-contrast silhouettes and light      ▫■ AI       and overhang the column (Lights / only)
    trails to suggest the movement of time   ┌────┐      the site, for any phone camera (no app)
                                             └────┘
    ██████ Say 'Computer, use AI' to interact █████    how to use the room (every mode)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import freetype
import qrcode
from PIL import Image, ImageChops, ImageDraw

W, H = 296, 152
PAD = 8
FONTS = Path(__file__).resolve().parent / "fonts"
LIBRARY = Path.home() / ".local" / "share" / "screen-machine" / "dot-fonts"   # fonts to try (the type lab)
# The type: three roles. font = a file in dot_service/fonts or the library, or an absolute path ("#n" = face n);
# px = the size drawn (a bitmap font's design size); line = line height (default: from the font's ink);
# bold = how the role's bold is made: "double" (each stroke drawn twice, a pixel apart), "none", or the bold
# cut's font; scale = each font pixel drawn as scale x scale; serif_i = a serifed capital I ("AI", not "Al").
_ARIAL = "/mnt/c/Windows/Fonts/arial.ttf"   # Windows', used in place (never copied into the repo)
DEFAULT_TYPES = {
    # headlines (always bold): Arial Bold 17 is the standard (owner, 27 Oct: legibility over one family);
    # Galmuri 14, pixel-doubled, where Windows' fonts aren't mounted
    "head": ({"font": _ARIAL, "px": 17, "bold": _ARIAL.replace("arial.ttf", "arialbd.ttf")} if os.path.exists(_ARIAL)
             else {"font": "Galmuri14.ttf", "px": 15, "line": 19, "bold": "double"}),
    "text": {"font": "Galmuri9.ttf", "px": 10, "line": 12, "serif_i": True},       # text, labels, switches
    "ui": {"font": "Galmuri11.ttf", "px": 12, "line": 13, "bold": "Galmuri11-Bold.ttf"},  # mode, bottom line
}
FALLBACK = {"font": "Galmuri11.ttf", "px": 12, "line": 13}
# The left column's gaps (px), also set in the type lab: above the dotted rule, between it and the headline,
# and under the headline (the art card) / under the progress bar (music).
DEFAULT_SPACE = {"above_rule": 0, "above_head": 3, "below_head": 3}   # glyphs a chosen font lacks (·, →, …)
COL_W = 50                                 # the right-hand column's width when there is no QR code
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
    ai_heard: str = ""              # live: the AI listener's words for the music (at most 12)
    theme: str = ""                 # today's art theme
    style: str = ""                 # today's art style
    hint: str = ""                  # bottom line: "Say 'Computer, use AI' to interact"
    qr: str = ""                    # bottom-right QR code: this URL ("" = none)
    qr_px: int = 2                  # pixels per QR module
    stamp: str = field(default="", compare=False)   # "19:42"; not part of what changed


class _Face:
    """A bitmap-grid font at its design size: glyph masks at whole-pixel advances, with kerning; `fallback`
    supplies glyphs the font lacks. scale > 1 draws every font pixel as a scale x scale block (still exact)."""

    FLAGS = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO

    def __init__(self, path: str, px: int, line: Optional[int] = None, fallback: Optional["_Face"] = None,
                 scale: int = 1, bold: Optional["_Face"] = None, glyphs: Optional[dict] = None, index: int = 0,
                 double: bool = True) -> None:
        self.bold = bold                          # the bold cut; without one, bold = pixel-doubled (if double)
        self.double = double
        self.face = freetype.Face(path, index)
        if self.face.is_scalable:
            self.face.set_pixel_sizes(0, px)
        else:                                     # a bitmap-only font: its nearest strike
            sizes = [s.y_ppem >> 6 or s.height for s in self.face.available_sizes]
            self.face.select_size(min(range(len(sizes)), key=lambda i: abs(sizes[i] - px)))
        self.fallback, self.scale = fallback, scale
        self.px = px * scale
        self.ascender = (self.face.size.ascender >> 6) * scale
        self.glyphs: dict = {}
        for ch, (rows, adv) in (glyphs or {}).items():   # hand-drawn replacements: (rows, advance), top = cap top
            mask = Image.new("1", (len(rows[0]), len(rows)), 0)
            for y, row in enumerate(rows):
                for x, c in enumerate(row):
                    if c == "x":
                        mask.putpixel((x, y), 1)
            self.glyphs[ch] = (self, self.face.get_char_index(ch), adv, 0, len(rows), mask)
        self.line = line or self._natural_line()

    def _natural_line(self) -> int:
        """Cap top to descender bottom, plus 2."""
        top = self._glyph("H")[4]
        g = self._glyph("g")
        desc = (g[5].height - g[4]) if g[5] is not None else 0
        return max(top + desc + 2, 6)
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
                k = self.scale
                mask = (Image.frombytes("1", (bm.width, bm.rows), bytes(bm.buffer), "raw", "1", bm.pitch, 1)
                        if bm.width and bm.rows else None)
                if mask is not None and k > 1:
                    mask = mask.resize((mask.width * k, mask.height * k), Image.NEAREST)
                g = (self, gi, ((slot.advance.x + 32) >> 6) * k, slot.bitmap_left * k, slot.bitmap_top * k, mask)
            self.glyphs[ch] = g
        return g

    def _kern(self, prev, cur) -> int:
        if prev is None or prev[0] is not cur[0] or not cur[0].face.has_kerning:
            return 0
        return ((cur[0].face.get_kerning(prev[1], cur[1], freetype.FT_KERNING_DEFAULT).x + 32) >> 6) * cur[0].scale

    def length(self, text: str, bold: bool = False) -> int:
        if bold and self.bold is not None:
            return self.bold.length(text)
        bold = bold and self.double
        x, prev = 0, None
        for ch in text:
            g = self._glyph(ch)
            x += self._kern(prev, g) + g[2] + (1 if bold else 0)
            prev = g
        return x

    def draw(self, img: Image.Image, xy: tuple[float, float], text: str, fill: int = 0, bold: bool = False) -> None:
        """Text with its ascender line at y."""
        if bold and self.bold is not None:
            return self.bold.draw(img, xy, text, fill)
        bold = bold and self.double
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


def font_path(font: str) -> tuple[str, int]:
    """(path, face index) for a font name: dot_service/fonts, then the library, or an absolute path; "#n" = face n."""
    name, _, idx = font.partition("#")
    for p in ([Path(name)] if Path(name).is_absolute() else [FONTS / name, LIBRARY / name]):
        if p.exists():
            return str(p), int(idx or 0)
    raise FileNotFoundError(font)


def _serif_i(f: "_Face") -> dict:
    """A serifed capital I as tall as the font's H (3 px wide)."""
    h = f._glyph("H")[4] // f.scale
    return {"I": (["xxx"] + [".x."] * max(h - 2, 1) + ["xxx"], 4)}


_cache: dict = {}


def _role(spec: dict, fallback: Optional["_Face"]) -> "_Face":
    path, idx = font_path(spec["font"])
    bold = spec.get("bold", "double")
    twin = None
    if bold not in ("double", "none", None):
        bpath, bidx = font_path(bold)
        twin = _Face(bpath, int(spec["px"]), spec.get("line"), fallback, int(spec.get("scale", 1)), index=bidx)
    f = _Face(path, int(spec["px"]), spec.get("line"), fallback, int(spec.get("scale", 1)), bold=twin, index=idx,
              double=bold == "double")
    if spec.get("serif_i"):
        f = _Face(path, int(spec["px"]), spec.get("line"), fallback, int(spec.get("scale", 1)), bold=twin,
                  index=idx, double=bold == "double", glyphs=_serif_i(f))
    return f


def _fonts(types: Optional[dict] = None) -> tuple["_Face", "_Face", "_Face"]:
    """(text, ui, head) for a type spec (DEFAULT_TYPES when None); built once per spec."""
    t = {**DEFAULT_TYPES, **{k: v for k, v in (types or {}).items() if k in DEFAULT_TYPES}}
    key = json.dumps(t, sort_keys=True)
    if key not in _cache:
        fb = _Face(font_path(FALLBACK["font"])[0], FALLBACK["px"], FALLBACK["line"])
        ui = _role(t["ui"], fb)
        _cache[key] = (_role(t["text"], ui), ui, _role(t["head"], ui))
        if len(_cache) > 32:
            _cache.pop(next(iter(_cache)))
    return _cache[key]


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


_SWITCH_ON = [".xxxxxxxxxxxxx.", "xxxxxxxxxx...xx", "xxxxxxxxx.....x", "xxxxxxxxx.....x", "xxxxxxxxx.....x",
              "xxxxxxxxx.....x", "xxxxxxxxx.....x", "xxxxxxxxxx...xx", ".xxxxxxxxxxxxx."]   # black, knob right
_SWITCH_OFF = [".xxxxxxxxxxxxx.", "x.xxx.........x", "xx...x........x", "xx...x........x", "xx...x........x",
               "xx...x........x", "xx...x........x", "x.xxx.........x", ".xxxxxxxxxxxxx."]  # hollow, knob left
_CLOCK_S = [".xxxxx.", "x..x..x", "x..x..x", "x..xx.x", "x.....x", "x.....x", ".xxxxx."]


class _Flow:
    """Lines stacked down from the top, flowing round the QR code (top right): a line in its rows stops left of
    it. What doesn't fit above `bottom` is cut, the last line ellipsised."""

    def __init__(self, img: Image.Image, faces: tuple, bottom: int, narrow_until: int, narrow_right: int) -> None:
        self.img, self.d, self.y, self.bottom = img, ImageDraw.Draw(img), 4, bottom
        self.faces, self.narrow_until, self.narrow_right = faces, narrow_until, narrow_right

    def right(self, h: int) -> int:
        return self.narrow_right if self.y < self.narrow_until else W - PAD

    def text(self, text: str, kind: str = "small", lines: int = 1, bold: bool = False) -> None:
        if not text:
            return
        small, body, head = self.faces
        f = {"small": small, "body": body, "head": head}[kind]
        bold = bold or kind == "head"
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
        """Progress: a hairline track, the part played a 3-px bar."""
        if self.y + 6 > self.bottom:
            return
        x0, x1 = PAD, self.right(6)
        y = self.y + 2
        self.d.line((x0, y + 1, x1, y + 1), fill=0)
        fx = x0 + round((x1 - x0) * max(0.0, min(1.0, frac)))
        if fx > x0:
            self.d.rectangle((x0, y, fx, y + 2), fill=0)
        self.d.line((x1, y - 1, x1, y + 3), fill=0)      # the end of the piece
        self.y += 8

    def space(self, px: int) -> None:
        self.y += px

    def rule(self) -> None:
        """A light dotted rule (every third pixel)."""
        if self.y + 6 > self.bottom:
            return
        for x in range(PAD, self.right(4) + 1, 3):
            self.img.putpixel((x, self.y + 2), 0)
        self.y += 6

    def brand(self, label: str) -> None:
        """The app's name, styled like its logo (screen and machine bold, the slash light), a thin divider, then
        label (e.g. "Today's art")."""
        small, body, _ = self.faces
        x, y = PAD, self.y
        base = y - 1 + body.ascender                   # one baseline for the name and the label
        for part, bold in (("screen", True), ("/", False), ("machine", True)):
            body.draw(self.img, (x, base - body.ascender), part, bold=bold)
            x += body.length(part, bold)
        x += 6
        cap = body._glyph("H")[4]
        self.d.line((x, base - cap + 1, x, base), fill=0)
        small.draw(self.img, (x + 6, base - small.ascender), _ellipsis(small, label, self.right(12) - x - 6))
        self.y += body.line + 1


def _qr(url: str, px: int) -> Image.Image:
    """The smallest QR for url. A bare scheme + host is case-free, so it goes in upper case: QR's alphanumeric
    mode packs it denser (https://screen.finsburybankforsavings.com: 25 modules, not 29)."""
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0, box_size=px)
    bare = url.split("://", 1)[-1].rstrip("/")
    q.add_data(url.rstrip("/").upper() if "/" not in bare else url)
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("1")


def _ink(f: _Face, text: str, bold: bool) -> tuple[int, int, int, int]:
    """The ink box of text drawn at (0, 10)."""
    probe = Image.new("1", (f.length(text, bold) + 20, 60), 1)
    f.draw(probe, (0, 10), text, bold=bold)
    return ImageChops.invert(probe.convert("L")).getbbox() or (0, 10, 0, 10)


def _fit_lines(text: str, faces: list, width: int, max_lines: int) -> tuple:
    """(face, bold, lines): text in the first face (with its weight) whose words wrap into max_lines lines of
    width, else the last face's lines, the last ellipsised."""
    for f, bold in faces:
        lines: list[str] = []
        for word in text.split():
            if lines and f.length(lines[-1] + " " + word, bold) <= width:
                lines[-1] += " " + word
            else:
                lines.append(word)
        if len(lines) <= max_lines and all(f.length(ln, bold) <= width for ln in lines):
            return f, bold, lines
    f, bold = faces[-1]
    return f, bold, [_ellipsis(f, ln, width, bold) for ln in lines[:max_lines - 1]] + \
        [_ellipsis(f, " ".join(lines[max_lines - 1:]), width, bold)]


def _centred(img: Image.Image, f: _Face, text: str, box: tuple[int, int, int, int], fill: int = 0,
             bold: bool = False, left: bool = False) -> None:
    """text centred in box (x0, y0, x1, y1): across by its ink (or from x0, left), down by the capitals
    (descenders hang below)."""
    x0, _, x1, _ = _ink(f, text, bold)
    _, c0, _, c1 = _ink(f, "H", bold)
    bx0, by0, bx1, by1 = box
    x = bx0 - x0 if left else bx0 + (bx1 - bx0 + 1 - (x1 - x0)) // 2 - x0
    y = by0 + (by1 - by0 + 1 - (c1 - c0)) // 2 - (c0 - 10)
    f.draw(img, (x, y), text, fill=fill, bold=bold)


def render(card: Card, types: Optional[dict] = None) -> Image.Image:
    """Left: what matters now. Right-hand bar: the mode, when this was drawn, Auto / Mic / AI, the QR code.
    Bottom: how to use the room, across the tag (the tag lies loose under the screens, in every mode)."""
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    faces = _fonts(types)
    small, body, head = faces

    # Bottom line: how to use the room, white on black, centred.
    bottom = H
    if card.hint:
        bottom = H - 18
        d.rectangle((0, bottom, W - 1, H - 1), fill=0)
        _centred(img, body, _ellipsis(body, card.hint, W - 2 * PAD, True), (0, bottom + 3, W - 1, H - 4), fill=1,
                 bold=True)

    # The right-hand column, one width (the QR code's), centred on one axis: the mode, when this was drawn,
    # Auto / Mic / AI as small switches, the QR code at its foot.
    q = _qr(card.qr, card.qr_px) if card.qr else None
    cw = q.width if q is not None else COL_W
    cx0, cx1 = W - EDGE - cw, W - EDGE - 1
    qy = H
    if q is not None:   # the foot of the column, against the glass edge (the white bezel is more quiet zone)
        qy = (bottom - 3 * card.qr_px - q.height) if card.hint else H - EDGE - q.height
        img.paste(q, (cx0, qy))

    d.line((cx0 - 8, 3, cx0 - 8, (bottom - 5) if card.hint else H - 4), fill=0)   # the column's rule

    # The mode may overhang the column into the gutter, and wraps to two lines (Lights / only), else smaller.
    mw = cw + 6                            # the column's rule is 8 px to its left
    mf, mbold, lines = _fit_lines(card.mode, [(body, True), (small, False)], mw, 2)
    if card.changing_from and len(lines) == 1 and mf.length("→" + lines[0], mbold) <= mw:
        lines = ["→" + lines[0]]           # switching: the arrow if it fits
    toggles = [("Auto", card.auto), ("Mic", card.mic), ("AI", card.ai)]
    step = max(13, small.line + 1)         # a switch row
    show_time = bool(card.stamp) and 2 + mf.line * len(lines) + small.line + 3 + step * len(toggles) <= qy - 4
    y = 2
    for ln in lines:   # centred on the column; a wider line overhangs left only (never off the glass)
        lx0, _, lx1, _ = _ink(mf, ln, mbold)
        x = min(cx0 + (cw - (lx1 - lx0)) // 2, cx1 + 1 - (lx1 - lx0)) - lx0
        mf.draw(img, (x, y), ln, bold=mbold)
        y += mf.line
    if show_time:   # a small clock and the time it was drawn
        tw = 10 + small.length(card.stamp)
        tx = cx0 + (cw - tw) // 2
        _pixels(img, tx, y + 2, _CLOCK_S)
        _centred(img, small, card.stamp, (tx + 10, y + 2, tx + tw, y + 8))
        y += small.line
    y += 3
    lw = max(small.length(label) for label, _ in toggles)
    tx = cx0 + (cw - (len(_SWITCH_ON[0]) + 4 + lw)) // 2
    for label, on in toggles:   # a switch drawing (knob right on black = on) and its name, as a left-aligned block
        _pixels(img, tx, y + 1, _SWITCH_ON if on else _SWITCH_OFF)
        _centred(img, small, label, (tx + len(_SWITCH_ON[0]) + 4, y + 1, tx + len(_SWITCH_ON[0]) + 4, y + 9),
                 left=True)
        y += step

    # What matters now, left of the bar, in one pattern whatever the mode: screen/machine | what this is, a
    # dotted rule, the headline, then the detail.
    flow = _Flow(img, faces, bottom=bottom - 4, narrow_until=H, narrow_right=cx0 - 15)
    paused = f"Paused: {card.title}" + (f" · {card.by}" if card.by else "") if card.title and card.paused else ""
    sp = {**DEFAULT_SPACE, **((types or {}).get("space") or {})}

    def rule() -> None:
        flow.space(int(sp["above_rule"]))
        flow.rule()
        flow.space(int(sp["above_head"]))

    if card.title and not card.paused:      # music playing (the player or the room's Sonos), any mode
        flow.brand("Now playing")
        rule()
        flow.text(card.title, "head", lines=2)
        flow.text(" · ".join(x for x in (card.by, card.left) if x))
        if card.progress is not None:
            flow.bar(card.progress)
        flow.space(int(sp["below_head"]))
        if card.live:                       # the screens are live: what they show and the room's feel
            flow.text(f"Scene: {card.scene}" if card.scene else "")
            flow.text(card.feel, lines=2)
            flow.text(f"AI: {card.ai_heard}" if card.ai_heard else "", lines=2)
        else:                               # the screens show the art
            flow.text(f"Today's art: {card.theme}" if card.theme else "")
            flow.text(card.style, lines=3)
    elif card.live:                         # live, nothing playing (a paused piece is a line, not the headline)
        flow.brand("Hearing")
        rule()
        flow.text(card.hearing or "—", "head")
        flow.space(int(sp["below_head"]))
        flow.text(f"Scene: {card.scene}" if card.scene else "")
        flow.text(card.feel, lines=2)
        flow.text(paused)
        flow.text(f"AI: {card.ai_heard}" if card.ai_heard else "", lines=3)
    else:                                   # the art
        flow.brand("Today's art")
        rule()
        flow.text(card.theme, "head", lines=2)
        flow.space(int(sp["below_head"]))
        flow.text(card.style, lines=6 if not paused else 4)
        if paused:
            flow.space(3)
            flow.text(paused)

    return img
