"""songplan: a karaoke song laid out before it plays (owner, 27 Nov 2026).

"upgrade your handling logic so it drives -- in aggregate -- a dynamic, varied and compelling show, which is
integrated by the a.d. and informed by song structure and shape metadata (where available)."

For a karaoke song both are available before a note sounds: its sections from its lyrics (lyric_sections.py) and its
shape from its audio (utils/song_shape.py: its intensity 0..1 every half second within its own range, and the 20 s
where it is highest). ``lay_out`` puts them together, so each section the art director is told about carries:

  level   the song's mean intensity over that section, 0..1 (None: no shape). The director hears a section's
          energy only once it is playing; this says how big it is going to be, so the look it lands with fits.
  peak    True for the one section that holds the song's climax, False for the others; None for all when the song has
          no shape or its climax doesn't stand out (the last chorus is then the big one, as before).

A climax that begins well inside a long section (a ballad's last verse that swells into its ending) starts a section
of its own there: same kind, same letter, so the look changes ON the swell, not at the line before it.

Pure. The karaoke feed calls it once per song; structure announces the sections and passes ``level`` and ``peak`` on
in its section_change events (detail "lyrics").
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

#: split_min_s: the climax starts its own section only when it begins at least this far from both ends of the one it
#: is in. peak_margin: the climax section's level must be this far over the song's middle (median) section for the
#: song to have a peak at all (a song that is level all the way through has none). peak_after: and it must begin
#: after this share of the song (an early loud intro is not the climax of the show). Starting points.
#: spread_w: contrast added round each section's level by its place in the song's range (+-spread_w/2); 0 (27 Nov:
#: any downward contrast darkened every song's first verse; the shape is already the song's own range);
#: spread_min: a song whose sections lie closer together than this keeps its absolute levels (it really is level).
FALLBACK: dict[str, float] = {"split_min_s": 12.0, "peak_margin": 0.12, "peak_after": 0.35, "spread_w": 0.0,
                              "spread_min": 0.05}


def lay_out(secs: Any, shape: Optional[Mapping[str, Any]], dur: Optional[float],
            cfg: Optional[Mapping[str, Any]] = None) -> list[dict]:
    """``secs`` (lyric_sections: [{start_s, kind, letter, ...}], song order) with ``level`` and ``peak`` added, and
    the climax's own section split out. Never raises on an odd shape: the sections come back with level None."""
    out = [dict(x) for x in secs or () if isinstance(x, Mapping) and isinstance(x.get("start_s"), (int, float))]
    c = {**FALLBACK, **{k: float(v) for k, v in (cfg or {}).items() if k in FALLBACK and isinstance(v, (int, float))}}
    v = list((shape or {}).get("v") or ()) if isinstance(shape, Mapping) else []
    if not out or len(v) < 8:
        return [{**x, "level": None, "peak": None} for x in out]
    hop = float(shape.get("hop") or 0.5) or 0.5                                   # type: ignore[union-attr]
    end_all = max(float(dur) if dur else 0.0, len(v) * hop, out[-1]["start_s"] + hop)
    cl = shape.get("climax")                                                       # type: ignore[union-attr]
    cl = (float(cl[0]), float(cl[1])) if isinstance(cl, (list, tuple)) and len(cl) == 2 else None

    def end(i: int) -> float:
        return float(out[i + 1]["start_s"]) if i + 1 < len(out) else end_all

    def mean(t0: float, t1: float) -> float:
        i0 = max(0, min(len(v) - 1, int(t0 / hop)))
        i1 = max(i0 + 1, min(len(v), int(t1 / hop)))
        return sum(float(x) for x in v[i0:i1]) / (i1 - i0)

    def peak_of() -> Optional[int]:
        """The section holding most of the climax, if it stands out over the song's middle section and comes late
        enough; else None (the song is level, or its loudest stretch is an early intro)."""
        if cl is None:
            return None
        over = [max(0.0, min(end(i), cl[1]) - max(float(x["start_s"]), cl[0])) for i, x in enumerate(out)]
        bi = max(range(len(out)), key=lambda i: over[i])
        mid = sorted(x["level"] for x in out)[len(out) // 2]
        if over[bi] > 0 and out[bi]["level"] - mid >= c["peak_margin"] \
                and float(out[bi]["start_s"]) >= c["peak_after"] * end_all:
            return bi
        return None

    def levels() -> None:
        for i, x in enumerate(out):
            x["level"] = mean(float(x["start_s"]), end(i))

    # The climax that begins well inside a section is tried as a section of its own; the split is kept only if that
    # part is then the song's peak (27 Nov: the split came before the peak test and left false same-letter boundaries
    # in songs with no peak: Happy at 186 s, The Elements at 22 s, Common People at 274 s).
    split_at = None
    if cl is not None:
        for i, x in enumerate(out):
            if x["start_s"] + c["split_min_s"] <= cl[0] <= end(i) - c["split_min_s"]:
                out.insert(i + 1, {**x, "start_s": round(cl[0], 3), "line": None, "split": True})
                split_at = i + 1
                break
    levels()
    peak = peak_of()
    if split_at is not None and peak != split_at:
        del out[split_at]
        levels()
        peak = peak_of()
    # Levels spread within the song (27 Nov: an 8 s-smoothed, percentile-normalised curve leaves section means bunched
    # near the top, 0.82-0.97 in Don't Stop Me Now): part the song's own range, part the absolute.
    # The contrast is added round each section's own level, at most +-spread_w/2 (27 Nov: mapping onto the song's
    # whole range put every song's quietest section near 0, so a loud song's first verse got the smallest line-up and
    # the lamps sat dark through it: Common People's first 1:48).
    raw = [float(x["level"]) for x in out]
    lo, hi = min(raw), max(raw)
    for x, r in zip(out, raw):
        x["level_raw"] = round(r, 3)
        x["level"] = round(r if hi - lo < c["spread_min"] else
                           min(1.0, max(0.0, r + c["spread_w"] * ((r - lo) / (hi - lo) - 0.5))), 3)
    for i, x in enumerate(out):
        x["peak"] = (i == peak) if peak is not None else None
    return out
