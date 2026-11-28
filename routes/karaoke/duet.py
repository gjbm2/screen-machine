"""karaoke.duet: who sings which line of a duet, for the karaoke screen (owner, 28 Nov 2026; docs/KARAOKE.md "Duets").

Each line gets a part: A (the voice that sings first), B (the other), both, or backing; or none (not known). A song is
shown as a duet only when it confidently is one: a wrong duet display is worse than none.

Three sources, each run once a song:

1. The lyrics' own tags, when an upload has them ("[Elton:]", "Him:", "(Both)"). Rare (none in the library, 28 Nov).
2. The AI (``ask``): is the recording a duet, its two voices (names, man / woman / child), and each line's part. It
   knows famous duets but can be confidently wrong line by line: a prior, never the truth.
3. The recording (tools/karaoke/voices.py, on the lead stem): each line's median pitch and a speaker embedding.

``decide`` puts them together. From the lines the AI gives to A and to B it builds each voice's sound (the mean of
their embeddings, each line left out of its own); a line is marked only when its sound agrees with the AI, or when
the sound is clear on its own. The song is a duet only when the two voices sound different and most of the AI's lines
are borne out (DUET_MIN_*). Both and backing lines keep the AI's word (shown centred: the safe side to be wrong on).

Saved beside the song (duet.json) with the lyrics it was made from (``key``): new lyrics (the careful pass, another
upload) make it again. ``apply`` puts the parts on a lyrics document as it is served (each line's ``voice``, and
``voices`` per word for a line traded word by word).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

VERSION = 1
FILE = "duet.json"
TASK = "duet"                       # config/llm.yaml's task
SOLO = ("A", "B")
PARTS = ("A", "B", "both", "backing")
SEXES = ("man", "woman", "child", "unknown")

AGREE_MARGIN = 0.03                 # a line sounds like a voice when it is this much more like it than the other
CLEAR_MARGIN = 0.30                 # ... and clearly so: marked even against the AI, or where it didn't say. 28 Nov:
                                    # Ain't No Mountain's lines the AI had the wrong way round were 0.3-0.57 clear;
                                    # Shallow's wordless wail, which the sound put with Cooper, 0.17-0.25
WORDS_TO_HEAR = 2                   # the sound overrules the AI only on a line with this many real words: a wail
                                    # ("ha-ah-ah") doesn't sound like its singer's voice
DUET_MIN_LINES = 3                  # each voice confirmed (the AI and the sound agree) on at least this many lines
DUET_MIN_AGREE = 0.6                # and the AI's solo lines agree with the sound this often
DUET_MIN_APART = 0.15               # the solo lines this much more like their own voice than the other, on average.
                                    # Only a floor: one singer's sections split about as far as two singers do
                                    # (Hey Jude 0.38; Shallow 0.32, Breaking Free 0.25), so the AI says IF it's a duet


# -- the lyrics' own tags ------------------------------------------------------------------------------------------

TAG_BRACKET = re.compile(r"^\s*[\[(]\s*([A-Za-z][\w .&'/-]{0,30}?)\s*:?\s*[\])]\s*:?\s*(.*)$")
TAG_COLON = re.compile(r"^\s*([A-Za-z][\w.&'-]*(?: [A-Za-z&][\w.&'-]*){0,3})\s*:\s+(.+)$")
TOGETHER = {"both", "all", "together", "duet", "everyone", "everybody", "all together"}
HIM = {"him", "he", "man", "male", "boy", "guy"}
HER = {"her", "she", "woman", "female", "girl", "lady"}


def text_tag(text: str, voices: Sequence[Mapping[str, Any]] = ()) -> Optional[str]:
    """A line's part from its own tag, if it has one that names a voice (or "Both"): "[Elton:] ..." -> that voice.
    A bracketed word that isn't a voice ("(Hey) Huntrix") is not a tag."""
    m = TAG_BRACKET.match(text or "") or TAG_COLON.match(text or "")
    if not m:
        return None
    who = m.group(1).strip().lower()
    if who in TOGETHER:
        return "both"
    for k, v in zip(SOLO, voices):
        name = str(v.get("name") or "").lower()
        sex = str(v.get("sex") or "")
        parts = [p for p in re.split(r"[\s&,]+", name) if len(p) > 2]
        if who and (who == name or who in parts or (sex == "man" and who in HIM) or (sex == "woman" and who in HER)):
            return k
    return None


# -- the AI's view -------------------------------------------------------------------------------------------------

SYSTEM = (
    "You know popular songs and who sings what in them. You are given one recording: its title, its artist, and its "
    "lyrics line by line with the time each line starts. Say whether this recording is a duet: two lead singers who "
    "each have lines of their own, taking turns or singing together. A lead singer with backing vocals, a choir or "
    "a group singing in unison, or a featured artist who only adds a spoken line or a few ad libs, is not a duet. If "
    "it is a duet: name the two voices as the recording credits them (a character's or the singer's name), say "
    "whether each is a man, a woman or a child, and for every line say who sings it: A (the voice that sings first "
    "in this recording), B (the other), both (the two together), or backing (backing singers or a choir, neither "
    "lead). If a line's words in brackets are sung by someone other than the rest of the line, give that as its "
    "echo. Where you don't know who sings a line, say unknown: a wrong answer is worse than none."
)


def tool() -> dict:
    return {
        "name": "song_duet",
        "description": "Whether this recording is a duet, its two voices, and who sings each line.",
        "parameters": {
            "type": "object",
            "properties": {
                "duet": {"type": "boolean"},
                "voices": {"type": "array", "maxItems": 2, "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "sex": {"type": "string", "enum": list(SEXES)}},
                    "required": ["name", "sex"]}},
                "lines": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"i": {"type": "integer"},
                                   "part": {"type": "string", "enum": [*PARTS, "unknown"]},
                                   "echo": {"type": "string", "enum": list(PARTS)}},
                    "required": ["i", "part"]}},
            },
            "required": ["duet", "voices", "lines"],
        },
    }


def prompt(song: Mapping[str, Any], lines: Sequence[Mapping[str, Any]]) -> str:
    rows = [f"{i}\t{'' if l.get('start') is None else round(float(l['start']))}\t{l.get('text') or ''}"
            for i, l in enumerate(lines)]
    return (f"Title: {song.get('title') or ''}\nArtist: {song.get('artist') or ''}\n\n"
            "Lines (index, start in seconds, text):\n" + "\n".join(rows))


def parse(data: Any, n: int) -> Optional[dict]:
    """The AI's answer, checked: {duet, voices: [{name, sex}] x2, parts: [A|B|both|backing|None] x n,
    echoes: {i: part}}; None if it isn't an answer."""
    if not isinstance(data, Mapping) or not isinstance(data.get("duet"), bool):
        return None
    voices = [{"name": " ".join(str(v.get("name") or "").split())[:40] or f"Voice {k}",
               "sex": v.get("sex") if v.get("sex") in SEXES else "unknown"}
              for k, v in zip(SOLO, data.get("voices") or ()) if isinstance(v, Mapping)]
    parts: list[Optional[str]] = [None] * n
    echoes: dict[int, str] = {}
    for row in data.get("lines") or ():
        if not isinstance(row, Mapping) or not isinstance(row.get("i"), int) or not 0 <= row["i"] < n:
            continue
        if row.get("part") in PARTS:
            parts[row["i"]] = row["part"]
        if row.get("echo") in PARTS and row.get("echo") != row.get("part"):
            echoes[row["i"]] = row["echo"]
    duet = bool(data["duet"]) and len(voices) == 2
    return {"duet": duet, "voices": voices if duet else [], "parts": parts if duet else [None] * n,
            "echoes": echoes if duet else {}}


def ask(song: Mapping[str, Any], lines: Sequence[Mapping[str, Any]], llm: Any = None) -> tuple[Optional[dict], str]:
    """(the AI's view or None, which model / why not)."""
    m = llm
    if m is None:
        from utils import llm as m                       # noqa: PLC0415 - only where an AI is asked
    res = m.call_json(TASK, system=SYSTEM, user=prompt(song, lines), tool=tool(), lenient=True)
    if not getattr(res, "ok", False):
        return None, str(getattr(res, "error", None) or "no answer")
    got = parse(res.data, len(lines))
    return got, str(getattr(res, "model", None) or "") if got else "an answer that didn't parse"


# -- the recording's view, and the verdict ---------------------------------------------------------------------------

def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def two_voices(X: np.ndarray, iters: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Unit rows split into the two most different sounds (spherical 2-means from the least alike pair):
    (each row's group 0 | 1, the two groups' centres)."""
    S = X @ X.T
    i, j = np.unravel_index(int(np.argmin(S)), S.shape)
    c = np.stack([X[i], X[j]])
    lab = np.argmax(X @ c.T, axis=1)
    for _ in range(iters):
        new = np.stack([_unit(X[lab == k].mean(0)) if (lab == k).any() else c[k] for k in range(2)])
        lab2 = np.argmax(X @ new.T, axis=1)
        c = new
        if (lab2 == lab).all():
            break
        lab = lab2
    return lab, c


def worded(text: str) -> bool:
    """A line with WORDS_TO_HEAR words that aren't singing sounds ("oh", "na", "ha-ah")."""
    from .lyrics import FILLER, words_of
    return sum(1 for w in words_of(str(text or "").replace("-", " ")) if w not in FILLER) >= WORDS_TO_HEAR


def decide(n: int, prior: Optional[Mapping[str, Any]], heard: Sequence[Optional[Mapping[str, Any]]],
           tags: Sequence[Optional[str]] = (), texts: Sequence[str] = ()) -> dict:
    """The verdict: {duet, shown, voices, parts: [part | None] x n, echoes, why, stats}. ``duet``: it is one (the
    AI's word: the library's Duets tag); ``shown``: its parts are sure enough for the screen. ``prior`` is ``parse``'s answer;
    ``heard`` voices.py's lines; ``tags`` each line's own tag (``text_tag``).

    The recording says who sounds like whom: the lines not sung together (by the AI's word) split into the two most
    different sounds. The AI says which of the two is which voice (the way round that agrees with more of its
    lines), and whether it is a duet at all. Its line-by-line word is taken where the sound agrees, and the sound's
    where it is clear on its own (28 Nov, Ain't No Mountain High Enough: two clean voices, ~0.6 alike within and
    ~0.2 across, where the AI had a third of Marvin's and Tammi's lines the wrong way round)."""
    if not prior or not prior.get("duet"):
        return {"duet": False, "shown": False, "voices": [], "parts": [None] * n, "echoes": {}, "stats": {},
                "why": "the AI doesn't know it as a duet" if prior else "no AI answer"}
    voices = [dict(v) for v in prior["voices"]]
    # A duet the parts of which aren't sure enough to show: tagged a duet, shown as one voice.
    no = lambda why, **st: {"duet": True, "shown": False, "voices": voices, "parts": [None] * n,  # noqa: E731
                            "echoes": {}, "why": why, "stats": st}
    texts = list(texts) + [""] * n
    tags = list(tags) + [None] * n
    ai = [tags[i] or prior["parts"][i] for i in range(n)]
    heard = (list(heard) + [None] * n)[:n]
    emb = [_unit(np.asarray(h["emb"], dtype=np.float64)) if isinstance(h, Mapping) and h.get("emb") else None
           for h in heard]
    midi = [h.get("midi") if isinstance(h, Mapping) else None for h in heard]
    pool = [i for i in range(n) if emb[i] is not None and ai[i] not in ("both", "backing")]
    if len(pool) < 2 * DUET_MIN_LINES:
        return no("too few solo lines to hear", solo=len(pool))
    lab, cen = two_voices(np.stack([emb[i] for i in pool]))
    # Which group is A: the way round that agrees with more of the AI's solo lines.
    fit = [sum(1 for k, i in enumerate(pool) if ai[i] in SOLO and (ai[i] == "A") == (lab[k] == way)) for way in (0, 1)]
    a_grp = 0 if fit[0] >= fit[1] else 1
    told = sum(1 for i in pool if ai[i] in SOLO)
    group = {i: ("A" if lab[k] == a_grp else "B") for k, i in enumerate(pool)}
    ca, cb = (cen[0], cen[1]) if a_grp == 0 else (cen[1], cen[0])
    m = [float(e @ ca - e @ cb) if e is not None else None for e in emb]      # > 0: sounds like A
    own = [m[i] if group[i] == "A" else -m[i] for i in pool]
    apart = float(np.mean(own))
    confirmed = {k: sum(1 for i in pool if ai[i] == k and group[i] == k) for k in SOLO}
    share = max(fit) / told if told else 0.0
    pitch = {k: [midi[i] for i in pool if group[i] == k and midi[i] is not None] for k in SOLO}
    pitch = {k: (round(float(np.median(v)), 1) if v else None) for k, v in pitch.items()}
    stats = {"solo": len(pool), "told": told, "agree": round(share, 3), "apart": round(apart, 3),
             "alike": round(float(ca @ cb), 3), "confirmed": confirmed, "pitch": pitch}
    if apart < DUET_MIN_APART:
        return no("the recording doesn't sound like two voices", **stats)
    if min(confirmed.values()) < DUET_MIN_LINES or share < DUET_MIN_AGREE:
        return no("the recording doesn't bear the AI out", **stats)

    parts: list[Optional[str]] = [None] * n
    for i in range(n):
        p = ai[i]
        if p in ("both", "backing"):
            parts[i] = p
        elif m[i] is not None:
            heard_as = "A" if m[i] > 0 else "B"
            if (p == heard_as and abs(m[i]) >= AGREE_MARGIN) or (abs(m[i]) >= CLEAR_MARGIN and worded(texts[i])):
                parts[i] = heard_as
    # A line too short to hear keeps the AI's part when the lines either side of it are that voice too.
    for i in range(n):
        if parts[i] is None and ai[i] in SOLO and m[i] is None:
            before = next((parts[j] for j in range(i - 1, -1, -1) if parts[j]), None)
            after = next((parts[j] for j in range(i + 1, n) if parts[j]), None)
            if before == after == ai[i]:
                parts[i] = ai[i]
    echoes = {int(i): e for i, e in (prior.get("echoes") or {}).items()
              if str(i).isdigit() and int(i) < n and parts[int(i)] is not None}
    # A is whoever sings first.
    first = next((p for p in parts if p in SOLO), "A")
    if first == "B":
        swap = {"A": "B", "B": "A"}
        parts = [swap.get(p, p) if p else p for p in parts]
        echoes = {i: swap.get(e, e) for i, e in echoes.items()}
        voices = voices[::-1]
    stats["marked"] = sum(1 for p in parts if p)
    return {"duet": True, "shown": True, "voices": voices, "parts": parts, "echoes": echoes,
            "why": f"{stats['marked']}/{n} lines marked; the AI's lines agree with the sound for {share:.0%}",
            "stats": stats}


# -- kept beside the song ------------------------------------------------------------------------------------------

def key(lines: Sequence[Mapping[str, Any]]) -> str:
    """What a verdict was made from: the lines' words (a new timing of the same words keeps who sings them)."""
    return hashlib.sha1(json.dumps([str(l.get("text") or "") for l in lines]).encode()).hexdigest()[:16]


def load(song_dir: Path, lines: Optional[Sequence[Mapping[str, Any]]] = None) -> Optional[dict]:
    """The saved verdict (for these lines, when given; else any)."""
    try:
        d = json.loads((Path(song_dir) / FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("version") != VERSION:
        return None
    if "shown" not in d:            # saved before 28 Nov 01:00: "duet" was "shown", the AI's word is in "ai"
        d["shown"] = bool(d.get("duet"))
        d["duet"] = bool(d.get("duet") or (d.get("ai") or {}).get("duet"))
    if lines is not None and d.get("made_from") != key(lines):
        return None
    return d


def save(song_dir: Path, verdict: Mapping[str, Any], lines: Sequence[Mapping[str, Any]], by: str,
         wall: Callable[[], float] = time.time) -> dict:
    out = {**verdict, "echoes": {str(i): e for i, e in (verdict.get("echoes") or {}).items()},
           "version": VERSION, "by": by, "made_from": key(lines), "made_at": round(wall(), 1)}
    p = Path(song_dir) / FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1))
    tmp.replace(p)
    return out


def _paren_mask(text: str) -> list[bool]:
    """Per space-separated token: inside brackets?"""
    out, depth = [], 0
    for tok in text.split():
        opens, closes = tok.count("("), tok.count(")")
        out.append(depth > 0 or opens > 0)
        depth = max(0, depth + opens - closes)
    return out


def apply(doc: Mapping[str, Any], verdict: Optional[Mapping[str, Any]]) -> dict:
    """The lyrics document with each line's ``voice`` (and ``voices`` per word where its brackets are sung by
    another part), and ``duet`` {voices: [names, sexes]}: only for a duet made from these lines."""
    lines = list(doc.get("lines") or ())
    if not verdict or not verdict.get("shown") or verdict.get("made_from") != key(lines) \
            or len(verdict.get("parts") or ()) != len(lines):
        return dict(doc)
    echoes = verdict.get("echoes") or {}
    out = []
    for i, (line, part) in enumerate(zip(lines, verdict["parts"])):
        line = dict(line)
        if part:
            line["voice"] = part
            echo = echoes.get(str(i))
            if echo:
                line["voices"] = [echo if inside else part for inside in _paren_mask(str(line.get("text") or ""))]
        out.append(line)
    return {**doc, "lines": out, "duet": {"voices": verdict.get("voices") or []}}
