"""karaoke.lyrics: LRC parsing, the display's lines, and lining the lyrics up with the audio (docs/KARAOKE.md).

LRCLIB's timings fit the released recording. A YouTube copy can start a little earlier or later (a longer intro, a
cut fade), so ``estimate_offset`` slides the line starts against the separated lead vocal and keeps the shift where
singing starts at the most lines. It only moves the lyrics when the evidence is clear.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
OFFSET_TAG = re.compile(r"\[offset:\s*([+-]?\d+)\]", re.I)


def parse_lrc(lrc: str) -> list[dict]:
    """[{t, text}] sorted by time. Empty texts are kept: they mark where a line stops (an instrumental gap)."""
    out: list[dict] = []
    offset_s = 0.0
    m = OFFSET_TAG.search(lrc or "")
    if m:
        offset_s = int(m.group(1)) / 1000.0        # LRC: a positive offset shows the lyrics earlier
    for raw in (lrc or "").splitlines():
        tags = list(TAG.finditer(raw))
        if not tags:
            continue
        text = TAG.sub("", raw).strip()
        text = re.sub(r"<\d+:\d+(?:\.\d+)?>", "", text).strip()   # enhanced-LRC word stamps: not used yet
        for tg in tags:
            t = int(tg.group(1)) * 60 + float(tg.group(2).replace(":", "."))
            out.append({"t": round(max(0.0, t - offset_s), 3), "text": text})
    out.sort(key=lambda x: x["t"])
    return out


def display_lines(entries: list[dict], duration: float | None = None) -> list[dict]:
    """The lines the display shows: [{start, end, text}]. A line ends at the next stamp (sung or empty), but never
    lingers more than a sensible time for its length, so a long instrumental gap shows the count-in instead."""
    lines = []
    for i, e in enumerate(entries):
        if not e["text"]:
            continue
        nxt = entries[i + 1]["t"] if i + 1 < len(entries) else (duration or e["t"] + 6)
        words = max(1, len(e["text"].split()))
        cap = 2.0 + 0.6 * words
        lines.append({"start": e["t"], "end": round(min(nxt, e["t"] + cap), 3), "text": e["text"]})
    return lines


def plain_lines(text: str) -> list[dict]:
    return [{"start": None, "end": None, "text": ln.strip()} for ln in (text or "").splitlines() if ln.strip()]


# ---------------------------------------------------------------- lining up with the vocal
FRAME_S = 0.02


def vocal_activity(wav: Path) -> np.ndarray:
    """Per 20 ms frame, True where the separated lead vocal is singing."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, timeout=120, check=True)
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(8000 * FRAME_S)
    frames = x[: len(x) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    loud = np.percentile(db, 95)
    return db > max(loud - 22.0, -50.0)


def onset_score(active: np.ndarray, starts: list[float], shift_s: float) -> float:
    """How well singing begins at the line starts moved by ``shift_s``: active just after each start, quieter just
    before it."""
    n, total = len(active), 0.0
    for s in starts:
        a = int(round((s + shift_s) / FRAME_S))
        if a < 0 or a >= n:
            continue
        after = active[a: min(n, a + 30)].mean()                      # 0.6 s after
        before = active[max(0, a - 15): a].mean() if a > 0 else 0.0   # 0.3 s before
        total += after - 0.5 * before
    return total / max(1, len(starts))


def estimate_offset(active: np.ndarray, lines: list[dict], span_s: float = 4.0, step_s: float = 0.05) -> tuple[float, float]:
    """(shift in seconds to add to every line, confidence 0..1). Returns (0, 0) unless a shift clearly beats none."""
    starts = [ln["start"] for ln in lines if ln.get("start") is not None]
    if len(starts) < 4 or not len(active):
        return 0.0, 0.0
    shifts = np.arange(-span_s, span_s + 1e-9, step_s)
    scores = np.array([onset_score(active, starts, float(s)) for s in shifts])
    best = int(scores.argmax())
    at_zero = onset_score(active, starts, 0.0)
    gain = float(scores[best] - at_zero)
    if abs(shifts[best]) < 0.1 or gain < 0.08:
        return 0.0, max(0.0, min(1.0, float(scores.max())))
    return round(float(shifts[best]), 2), max(0.0, min(1.0, gain * 2))


# ---------------------------------------------------------------- fitting the lyrics to what is actually sung
# LRCLIB is people's uploads: a song can have a dozen timed versions, for different edits, and some are wrong
# altogether (25 Nov: Happy's "Williams, Pharrell" upload put an intro and a "Bring me down" where the record has
# none, and stopped at 2:56 of a 4:00 song: the screen showed words nobody was singing). So the timings are never
# trusted: Whisper hears the separated vocal (tools/karaoke/transcribe.py), each candidate's words are matched to
# what is sung in order (the longest common subsequence of words), the candidate that matches the most sung words
# wins, and each of its lines starts when its words are really sung.
FIT_MIN_COVERAGE = 0.35      # the share of the sung words a candidate must match to be used at all
WORD_S = 0.3                 # a word's length, to place a line whose first words weren't heard
HEARD_GAP_S = 0.7            # lines from what was heard: a new line after this much quiet...
HEARD_MAX_WORDS = 9          # ...or this many words


def words_of(text: str) -> list[str]:
    """'(Clap along, if you feel)' -> ['clap', 'along', 'if', 'you', 'feel']: for matching only."""
    out = []
    for tok in (text or "").split():
        w = re.sub(r"[^a-z0-9']", "", tok.lower()).strip("'")
        if w:
            out.append(w)
    return out


def _lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """The in-order matches (i in a, j in b) of the longest common subsequence: a song's repeated choruses are
    matched to the right repeat, in order, never across."""
    n, m = len(a), len(b)
    if not n or not m:
        return []
    dp = np.zeros((n + 1, m + 1), dtype=np.int32)
    bj = np.array([hash(w) for w in b])
    for i in range(1, n + 1):
        eq = bj == hash(a[i - 1])
        prev, row = dp[i - 1], dp[i]
        diag = prev[:-1] + 1
        # row[j] = max(prev[j], row[j-1], prev[j-1] + 1 if equal): the left-to-right max needs a scan
        best = np.maximum(prev[1:], np.where(eq, diag, 0))
        row[1:] = np.maximum.accumulate(best)   # row[j-1] <= row[j] always holds in an LCS table
    pairs = []
    i, j = n, m
    while i and j:
        if a[i - 1] == b[j - 1] and dp[i, j] == dp[i - 1, j - 1] + 1:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif dp[i - 1, j] >= dp[i, j - 1]:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


STRAY_ANCHOR_S = 4.0        # a heard line start this far from its neighbours' shift against the upload: a stray match
STRAY_WINDOW = 5            # the neighbours: this many heard lines each side


def drop_stray_anchors(starts: list[float | None], times: list[float | None]) -> None:
    """A synced upload's lines keep their spacing in any copy of the record; only the shift between them drifts. A
    heard start whose shift (heard - upload) is far from its neighbours' is a word matched in the wrong place (27
    Nov, Hey Jude: one "Jude" pulled a coda line a minute early): it is forgotten, and placed like the unheard."""
    idx = [i for i, s in enumerate(starts) if s is not None and times[i] is not None]
    shift = {i: starts[i] - times[i] for i in idx}
    for n, i in enumerate(idx):
        near = [shift[k] for k in idx[max(0, n - STRAY_WINDOW): n + STRAY_WINDOW + 1] if k != i]
        if len(near) >= 3 and abs(shift[i] - float(np.median(near))) > STRAY_ANCHOR_S:
            starts[i] = None


def place_by_upload(starts: list[float | None], times: list[float | None], duration: float | None) -> None:
    """Lines nobody was heard singing, where the upload has a time for them: the upload's time, moved by the shift
    of the heard lines either side (a straight line between the two), so a long stretch the vocal stem doesn't carry
    (a choir in the backing track) keeps the upload's own rhythm instead of an even spread."""
    known = [i for i, s in enumerate(starts) if s is not None and times[i] is not None]
    if not known:
        return
    for i in range(len(starts)):
        if starts[i] is not None or times[i] is None:
            continue
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is not None and hi is not None and times[hi] > times[lo]:
            f = (times[i] - times[lo]) / (times[hi] - times[lo])
            t = starts[lo] + f * (starts[hi] - starts[lo])
            t = min(max(t, starts[lo]), starts[hi])
        else:                                    # past the last heard line (or before the first): the shift of the
            side = [k for k in known if k < i][-STRAY_WINDOW:] if lo is not None else \
                [k for k in known if k > i][:STRAY_WINDOW]           # nearest few, not one word's
            t = times[i] + float(np.median([starts[k] - times[k] for k in side]))
        if duration:
            t = min(t, duration - 0.5)
        starts[i] = max(0.0, t)


def _heard_starts(texts: list[str], sung: list[dict]) -> tuple[list[float | None], dict[int, float], dict]:
    """Where each line was heard to start (None: not heard), when its last heard word ends, and the match stats."""
    cand: list[str] = []
    owner: list[int] = []
    for li, t in enumerate(texts):
        for w in words_of(t):
            cand.append(w)
            owner.append(li)
    heard = [words_of(x["w"]) for x in sung]
    flat: list[str] = []
    at: list[int] = []
    for k, ws in enumerate(heard):
        for w in ws:
            flat.append(w)
            at.append(k)
    pairs = _lcs_pairs(cand, flat)
    stats = {"matched": len(pairs), "coverage": round(len(pairs) / max(1, len(flat)), 3),
             "precision": round(len(pairs) / max(1, len(cand)), 3)}
    first: dict[int, tuple[float, int]] = {}      # line -> (sung t0 of its first matched word, its index in the line)
    last: dict[int, float] = {}
    idx_in_line = []
    seen: dict[int, int] = {}
    for li in owner:
        idx_in_line.append(seen.get(li, 0))
        seen[li] = seen.get(li, 0) + 1
    for ci, fj in pairs:
        li, w = owner[ci], sung[at[fj]]
        if li not in first:
            first[li] = (w["t0"], idx_in_line[ci])
        last[li] = w["t1"]
    starts: list[float | None] = [None] * len(texts)
    for li, (t0, k) in first.items():
        starts[li] = max(0.0, t0 - k * WORD_S)
    return starts, last, stats


def fit_to_sung(texts: list[str], sung: list[dict], duration: float | None = None,
                times: list[float | None] | None = None) -> tuple[list[dict], dict]:
    """``texts``: a candidate's lines (in order); ``sung``: [{w, t0, t1}] heard; ``times``: the candidate's own line
    times (a synced upload's), if it has them. Returns the display lines [{start, end, text}] timed to the singing,
    and {matched, coverage, precision} (coverage: of the sung words)."""
    starts, last, stats = _heard_starts(texts, sung)
    if times is not None and len(times) == len(texts):
        drop_stray_anchors(starts, times)
        for li in [li for li in last if starts[li] is None]:   # a stray match's end goes with it
            del last[li]
        place_by_upload(starts, times, duration)
    # Lines nobody was heard singing (and no upload time to go by): between their neighbours, evenly.
    known = [i for i, s in enumerate(starts) if s is not None]
    for i in range(len(texts)):
        if starts[i] is not None or not known:
            continue
        lo = max((k for k in known if k < i), default=None)
        hi = min((k for k in known if k > i), default=None)
        if lo is not None and hi is not None:
            starts[i] = starts[lo] + (starts[hi] - starts[lo]) * (i - lo) / (hi - lo)
        elif lo is not None:
            starts[i] = starts[lo] + 3.0 * (i - lo)
        else:
            starts[i] = max(0.0, starts[hi] - 3.0 * (hi - i))
    for i in range(1, len(starts)):                 # never backwards
        if starts[i] is not None and starts[i - 1] is not None and starts[i] < starts[i - 1]:
            starts[i] = starts[i - 1]
    lines = []
    for i, text in enumerate(texts):
        if starts[i] is None:
            continue
        nxt = next((starts[k] for k in range(i + 1, len(texts)) if starts[k] is not None and starts[k] > starts[i]),
                   duration or starts[i] + 6)
        n = max(1, len(text.split()))
        end = min(nxt, last[i] + 0.5) if i in last else min(nxt, starts[i] + 2.0 + 0.6 * n)
        lines.append({"start": round(starts[i], 3), "end": round(max(end, starts[i] + 0.3), 3), "text": text})
    return lines, stats


def lines_from_sung(sung: list[dict]) -> list[dict]:
    """No lyrics fit: the lines are what was heard, broken at pauses, sentence ends and every few words."""
    lines: list[dict] = []
    cur: list[dict] = []

    def flush() -> None:
        if cur:
            lines.append({"start": cur[0]["t0"], "end": round(cur[-1]["t1"] + 0.4, 3),
                          "text": " ".join(x["w"] for x in cur)})
            cur.clear()
    for x in sung:
        if cur and (x["t0"] - cur[-1]["t1"] > HEARD_GAP_S or len(cur) >= HEARD_MAX_WORDS
                    or cur[-1]["w"].endswith((".", "!", "?", ","))):
            flush()
        cur.append(x)
    flush()
    for a, b in zip(lines, lines[1:]):
        a["end"] = min(a["end"], b["start"])
    return lines


def candidate_texts(c: dict) -> list[str]:
    """A lyrics candidate's sung lines, in order (synced or plain alike: only the words are used)."""
    if c.get("kind") == "synced":
        return [e["text"] for e in parse_lrc(c.get("lrc") or "") if e["text"]]
    return [ln.strip() for ln in (c.get("text") or "").splitlines() if ln.strip() and not re.fullmatch(r"\[.*\]", ln.strip())]


def candidate_times(c: dict) -> list[float | None] | None:
    """A synced candidate's own time for each line candidate_texts gives (None for a plain one)."""
    if c.get("kind") != "synced":
        return None
    return [e["t"] for e in parse_lrc(c.get("lrc") or "") if e["text"]]


def best_fit(cands: list[dict], sung: list[dict], duration: float | None = None) -> tuple[dict | None, list[dict], dict]:
    """The candidate whose words match the most of what is sung, its lines timed to the singing, and its stats;
    (None, [], best stats) when none reaches FIT_MIN_COVERAGE."""
    best: tuple[dict | None, list[dict], dict] = (None, [], {"matched": 0, "coverage": 0.0})
    for c in cands:
        texts = candidate_texts(c)
        if not texts:
            continue
        lines, st = fit_to_sung(texts, sung, duration, candidate_times(c))
        if st["matched"] > best[2]["matched"]:
            best = (c, lines, st)
    if best[2].get("coverage", 0) < FIT_MIN_COVERAGE:
        return None, [], best[2]
    return best


# ---------------------------------------------------------------- the careful pass (tools/karaoke/refine.py)
CAREFUL_AGREE_S = 2.0        # a line the careful pass places this close to where it was heard agrees
CAREFUL_MIN_AGREE = 0.6      # the share of heard lines that must agree, or the careful pass is not used
# Words any line can have: a line made of these ("Na, na, na, na-na-na-naa", "yeah, yeah") is matched to whichever
# repeat comes first, so where it was "heard" says nothing (27 Nov, Hey Jude's coda).
FILLER = {"na", "naa", "nah", "la", "da", "doo", "ba", "sha", "yeah", "yea", "oh", "ooh", "oo", "ah", "aah", "hey",
          "woah", "whoa", "wo", "mm", "mmm", "hmm", "uh", "huh", "ha", "eh", "baby", "yeh", "ay", "ayy"}


def telling(text: str) -> bool:
    """A line with at least three words that aren't FILLER: where it is heard is evidence."""
    return sum(1 for w in words_of(text.replace("-", " ")) if w not in FILLER) >= 3


def merge_heard(lead: list[dict], voices: list[dict]) -> list[dict]:
    """What was heard on the lead stem, and what was heard on the every-voice stem where the lead heard nothing."""
    taken = sorted((w["t0"], w["t1"]) for w in lead)
    out = list(lead)
    for w in voices:
        if not any(a < w["t1"] and w["t0"] < b for a, b in taken):
            out.append(w)
    return sorted(out, key=lambda w: w["t0"])


def careful_lines(texts: list[str], rows: list, duration: float | None = None) -> list[dict]:
    """The display lines from the careful pass's whole-song alignment: each line starts at its first word, ends at
    its last (or the next line), with every word's {t0, t1} (None for a token without letters)."""
    lines: list[dict] = []
    for text, row in zip(texts, rows):
        got = [w for w in (row or []) if w]
        if not got:
            continue
        words = [{"t0": w["t0"], "t1": w["t1"]} if w else None for w in row]
        lines.append({"start": round(got[0]["t0"], 3), "end": round(got[-1]["t1"] + 0.5, 3), "text": text,
                      "words": words})
    for a, b in zip(lines, lines[1:]):
        a["end"] = round(max(a["start"] + 0.3, min(a["end"], b["start"])), 3)
    if lines and duration:
        lines[-1]["end"] = round(min(lines[-1]["end"], duration), 3)
    return lines


def agreement(lines: list[dict], texts: list[str], heard: list[dict], times: list[float | None] | None = None) -> dict:
    """How well ``lines`` (one per text, in order) start where their words were heard: {heard, agree, share,
    median_s}. Only telling lines count, and not heard starts that are stray matches (drop_stray_anchors)."""
    starts, _, _ = _heard_starts(texts, heard)
    if times is not None and len(times) == len(texts):
        drop_stray_anchors(starts, times)
    by_text: dict[int, float] = {}
    j = 0
    for i, t in enumerate(texts):                   # the careful lines skip textless ones: pair them back by text
        if j < len(lines) and lines[j]["text"] == t:
            by_text[i] = lines[j]["start"]
            j += 1
    d = [abs(by_text[i] - s) for i, s in enumerate(starts) if s is not None and i in by_text and telling(texts[i])]
    ok = sum(1 for x in d if x <= CAREFUL_AGREE_S)
    return {"heard": len(d), "agree": ok, "share": round(ok / len(d), 3) if d else None,
            "median_s": round(float(np.median(d)), 2) if d else None}
