"""The karaoke effects' common base (listens "lyrics": ``self.sound.lyrics``, stream_service/lyric_feed.py).

Each effect lays out glyph instances on the host from the song's words at photon time (lyric_text.Glyphs), and
composites them in its own kernel with lyric_text.GLYPH_CUDA.  With no song, no words near (``active`` 0) or amount
0, the frame passes through untouched.  Owner, 25 Nov: "karaoke effects ... which take the words at the relevant time
as input params"; tagged ``modes: [karaoke]`` and picked only while a song's synced lyrics play (fx_interplay
``listens.lyrics``).
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from stream_service.plugins._lib.effect_base import Effect
from stream_service.plugins._lib.lyric_text import FONT_NAMES, LyricAtlas, Glyphs, Slot, SLOT_H
from stream_service.plugins._lib.params import enum, num

ENV_MIN = 0.003
# The display page draws the readable lyrics itself (src/pages/DisplayPage/karaoke/KaraokeLayer.tsx: the sung line,
# up to two rows, and the next line, 7vh from the bottom): the bottom ~third.  Owner, 26 Nov: the effects "get in the
# way of the lyrics a bit. be careful."  So every effect lays its words out above LYRICS_BAND_TOP (a share of the
# height) and fades whatever still strays below it to ``under_lyrics`` (band_keep in its kernel).
LYRICS_BAND_TOP = 0.62
BAND_FEATHER = 0.05


def under_lyrics_param(default: float = 0.2) -> dict:
    return num(0.0, 1.0, default, "How much shows under the screen's own lyrics", tier="regional")


def font_param(default: str = "black") -> dict:
    return enum(FONT_NAMES, default, "Typeface")


class LyricEffect(Effect):
    stage = "overlay"
    listens = ("lyrics",)
    n_slots = 12
    repaints_colour = False

    def setup(self, ctx, params: dict) -> None:
        super().setup(ctx, params)
        self.cp = ctx.cp
        self.W, self.H = int(ctx.width), int(ctx.height)
        self.u = self.H / 2160.0                        # screen px per 4K px (sizes are tuned at 4K)
        self.atlas = LyricAtlas(ctx.cp, self.n_slots, font=str(params.get("font") or "black"))
        self.gl = Glyphs(ctx.cp)
        self._env = 0.0

    # -- per frame ----------------------------------------------------------------------------------------------------
    def lyrics(self) -> Any:
        snd = getattr(self, "sound", None)
        lf = getattr(snd, "lyrics", None) if snd is not None else None
        return lf if lf is not None and getattr(lf, "song", None) is not None else None

    def begin(self, dt: float) -> tuple[dict, Any, float]:
        """-> (params, the LyricFrame or None, the envelope: amount x active)."""
        p = self.step_params(dt)
        self.atlas.pump()
        self.gl.clear()
        lf = self.lyrics()
        env = float(p.get("amount", 1.0)) * (float(lf.active) if lf is not None else 0.0)
        self._env = env
        return p, lf, env

    def band(self, p: dict) -> tuple:
        """The kernel args for band_keep: (top px, feather px, k)."""
        return (np.float32(LYRICS_BAND_TOP * self.H), np.float32(BAND_FEATHER * self.H),
                np.float32(1.0 - float(p.get("under_lyrics", 0.2))))

    def region(self) -> tuple[float, float]:
        """Where the words go: (centre y, height) in px, the screen above the lyrics band."""
        h = LYRICS_BAND_TOP * self.H
        return h / 2.0, h

    def passthrough(self, src, out) -> None:
        self.cp.copyto(out, src)

    def slot(self, text: str) -> Optional[Slot]:
        return self.atlas.get(text)

    def want_lines(self, lf: Any, behind: int = 1, ahead: int = 2) -> None:
        """Bake the current line, ``ahead`` coming lines (nearest first) and ``behind`` past ones."""
        s = lf.song
        k = lf.line
        if k < 0:
            nxt = lf.next_line()
            k = nxt.i if nxt is not None else len(s.lines) - 1
        idx = [k] + [k + j for j in range(1, ahead + 1)] + [k - j for j in range(1, behind + 1)]
        self.atlas.want([s.lines[i].text for i in idx if 0 <= i < len(s.lines)])

    @staticmethod
    def word_px(slot: Slot, i: int) -> tuple[float, float]:
        """A word's slot-px span (the whole line's when the bake split differently)."""
        if 0 <= i < len(slot.words):
            return slot.words[i]
        return (float(slot.words[0][0]) if slot.words else 0.0, slot.width)

    @staticmethod
    def em_scale(slot: Slot, em_px: float) -> float:
        """The scale (screen px per slot px) that draws ``slot``'s text at an em of ``em_px`` screen px."""
        return em_px / max(1.0, slot.em)

    def close(self) -> None:
        try:
            self.atlas.close()
        except Exception:                                # noqa: BLE001
            pass


def layout_rows(widths: list[float], space: float, max_w: float, rows: int) -> list[list[int]]:
    """Greedy-wrap words (widths in any unit) into at most ``rows`` rows no wider than max_w where possible,
    balanced: the target width is total / rows."""
    total = sum(widths) + space * max(0, len(widths) - 1)
    target = max(max(widths, default=0.0), min(max_w, total / max(1, rows) * 1.08))
    out: list[list[int]] = [[]]
    w = 0.0
    for i, x in enumerate(widths):
        add = x if not out[-1] else space + x
        if out[-1] and w + add > target and len(out) < rows:
            out.append([i])
            w = x
        else:
            out[-1].append(i)
            w += add
    return out


def fit_line(slot: Slot, box_w: float, box_h: float, max_rows: int = 3, row_gap: float = 0.95
             ) -> tuple[float, list[list[int]]]:
    """Lay a line's words in 1..max_rows rows filling a box_w x box_h box as large as possible
    -> (scale, rows of word indices).  Row pitch is row_gap em."""
    ws = [b - a for a, b in slot.words]
    if not ws:
        return 0.0, []
    space = 0.28 * slot.em
    best = (0.0, [list(range(len(ws)))])
    for r in range(1, max_rows + 1):
        rows = layout_rows(ws, space, float("inf"), r)
        widest = max(sum(ws[i] for i in row) + space * (len(row) - 1) for row in rows)
        tall = slot.em * (0.8 + row_gap * (len(rows) - 1))
        sc = min(box_w / max(1.0, widest), box_h / max(1.0, tall))
        if sc > best[0] * 1.06:                          # more rows only when clearly bigger
            best = (sc, rows)
    return best


def place_rows(gl: Glyphs, slot: Slot, rows: list[list[int]], scale: float, cx: float, cy: float, *,
               row_gap: float = 0.95, alpha_of=lambda i: 1.0, user_of=lambda i: 0.0, soft: float = 0.0,
               dx: float = 0.0, dy: float = 0.0) -> None:
    """Draw ``rows`` of a line's words centred on (cx, cy) at ``scale``; per word alpha and user values."""
    space = 0.28 * slot.em
    pitch = row_gap * slot.em * scale
    y0 = cy - pitch * (len(rows) - 1) / 2.0
    for r, row in enumerate(rows):
        widths = [slot.words[i][1] - slot.words[i][0] for i in row]
        total = (sum(widths) + space * (len(row) - 1)) * scale
        x = cx - total / 2.0
        for i, w in zip(row, widths):
            a0, a1 = slot.words[i]
            gl.add(slot, a0, a1, x + w * scale / 2.0 + dx, y0 + r * pitch + dy, scale, alpha=alpha_of(i),
                   user=user_of(i), soft=soft, height=SLOT_H * 0.42)
            x += (w + space) * scale


__all__ = ["LyricEffect", "font_param", "layout_rows", "fit_line", "place_rows", "ENV_MIN"]
