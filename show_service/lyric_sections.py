"""A karaoke song's sections from its lyrics: verse, chorus and the instrumental breaks, known before they are heard.

Owner, 26 Nov: "do we not have verse / chorus etc. triggers we can give to the a.d. to help it control pacing / scene
triggers, etc.? surely that's trivially available with the karaoke machinery?"  It is: lyrics.json carries every
line's sung start and end (routes/karaoke/lyrics.py fits them to the vocal), and the lines that are sung more than
once are the chorus.  karaoke_feed.KaraokeFeed hands these to structure through ctx.now_playing (``sections``), and
structure announces each boundary as a predicted section_change, on the line, with its letter (structure.py
``_lyric_plan``), so the director lands its look exactly there and brings the chorus's look back for every chorus.

Rules (song seconds, the lyrics' own clock):
  * a line is "chorus" when the same words (case, punctuation and spacing aside) are sung at least twice in the song,
    else "verse"; a lone line whose neighbours on both sides agree takes theirs (a one-off ad lib in a chorus);
  * a section starts where the kind changes, and after a gap of INSTR_GAP_S or more between lines (that gap is its
    own "instrumental" section, from the last line's end + TAIL_S); the song's end (``dur``) after the last line, if
    INSTR_GAP_S or more, is the "outro";
  * a section shorter than MIN_SECTION_S joins the one before it (no churn on short tags);
  * letters, as structure's: verse "A", chorus "B", instrumental and outro "C" (every chorus is one letter: the
    director remembers a letter's look).  A song whose lines never repeat has no chorus: it is all verse.
"""
from __future__ import annotations

from typing import Any, Optional

INSTR_GAP_S = 8.0        # a gap between sung lines this long is an instrumental break (its own section)
TAIL_S = 0.5             # the break starts this long after the last word (the line's held note)
MIN_SECTION_S = 6.0      # a section shorter than this joins the one before it
LETTERS = {"verse": "A", "chorus": "B", "instrumental": "C", "outro": "C"}


def _norm(text: str) -> str:
    return " ".join("".join(c for c in w.lower() if c.isalnum() or c == "'") for w in str(text).split())


def sections_from_lyrics(lyr: Any, dur: Optional[float] = None) -> list[dict]:
    """lyrics.json ({kind: "synced", lines: [{start, end, text}]}) -> [{start_s, kind, letter, line}] in song order
    (``line``: the index of its first sung line, None for a break).  [] when the lyrics aren't synced or are empty:
    then nothing is known ahead and structure listens as usual."""
    if not isinstance(lyr, dict) or lyr.get("kind") != "synced":
        return []
    lines = []
    for ln in lyr.get("lines") or []:
        if not isinstance(ln, dict) or not str(ln.get("text") or "").strip():
            continue
        try:
            s = float(ln["start"])
            e = float(ln.get("end") if ln.get("end") is not None else s + 2.0)
        except (KeyError, TypeError, ValueError):
            continue
        lines.append((s, max(s, e), _norm(ln["text"])))
    if not lines:
        return []
    lines.sort(key=lambda x: x[0])
    counts: dict[str, int] = {}
    for _, _, n in lines:
        counts[n] = counts.get(n, 0) + 1
    kinds = ["chorus" if counts[n] >= 2 else "verse" for _, _, n in lines]
    for i in range(1, len(kinds) - 1):               # a lone line inside a run of the other kind takes the run's
        if kinds[i - 1] == kinds[i + 1] != kinds[i]:
            kinds[i] = kinds[i - 1]
    out: list[dict] = []
    for i, (s, e, _) in enumerate(lines):
        if i > 0 and s - lines[i - 1][1] >= INSTR_GAP_S:
            out.append({"start_s": lines[i - 1][1] + TAIL_S, "kind": "instrumental", "line": None})
            out.append({"start_s": s, "kind": kinds[i], "line": i})
        elif not out or out[-1]["kind"] != kinds[i]:
            out.append({"start_s": s, "kind": kinds[i], "line": i})
    if dur is not None and dur - lines[-1][1] >= INSTR_GAP_S:
        out.append({"start_s": lines[-1][1] + TAIL_S, "kind": "outro", "line": None})
    merged: list[dict] = []
    for k, sec in enumerate(out):                    # too short: joins the one before (the first always stays)
        nxt = out[k + 1]["start_s"] if k + 1 < len(out) else (dur if dur is not None else lines[-1][1])
        if merged and nxt - sec["start_s"] < MIN_SECTION_S and sec["kind"] not in ("instrumental", "outro"):
            continue
        if merged and merged[-1]["kind"] == sec["kind"] and sec["kind"] not in ("instrumental",):
            continue                                 # two of a kind in a row (after a merge): one section
        merged.append(sec)
    for sec in merged:
        sec["letter"] = LETTERS[sec["kind"]]
        sec["start_s"] = round(sec["start_s"], 3)
    return merged


__all__ = ["sections_from_lyrics", "INSTR_GAP_S", "MIN_SECTION_S", "LETTERS"]
