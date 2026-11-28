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
NAMES_PITCH_S = 5.0                 # the two voices this many semitones apart (median), and the AI says a man and a
                                    # woman: the lower is the man (28 Nov, Endless Love: Lionel at MIDI 50, Diana at 63).
                                    # 3 to 7 scored the same on the eval
BOTH_BELOW = 0.1                    # a solo line this little nearer one voice than the other: both singing (eval 28 Nov:
                                    # +5 right lines; 0.2 the same, 0 fewer)
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


def two_voices(X: np.ndarray, seed: Optional[Sequence[Optional[int]]] = None,
               iters: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Unit rows split into two sounds (spherical 2-means): (each row's group 0 | 1, the two groups' centres).
    ``seed``: a starting group per row (None: no say), e.g. the AI's A and B; then the sound moves the lines that
    sound like the other voice. Without one, from the least alike pair. 28 Nov: from the least alike pair, a split
    could latch onto two odd lines (Breaking Free, You're The One That I Want: groups 0.6 alike that weren't the
    singers); seeded by the AI the lines agreed 93% and 70%, and every other duet as well or better."""
    sd = np.array([-1 if g is None else int(g) for g in seed]) if seed is not None else None
    if sd is not None and (sd == 0).any() and (sd == 1).any():
        c = np.stack([_unit(X[sd == k].mean(0)) for k in range(2)])
    else:
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
    solo_lines = [i for i in range(n) if emb[i] is not None and ai[i] not in ("both", "backing")]
    pool = [i for i in solo_lines if worded(texts[i])]       # the voices are learnt from lines with real words
    if len(pool) < 2 * DUET_MIN_LINES:
        shared = [p if p in ("both", "backing") else None for p in ai]
        if sum(1 for p in shared if p) >= 0.6 * n:      # sung together nearly throughout (Somethin' Stupid): shown so
            return {"duet": True, "shown": True, "voices": voices, "parts": shared, "echoes": {}, "stats": {"solo": len(pool)},
                    "why": "sung together nearly throughout"}
        return no("too few solo lines to hear", solo=len(pool))
    lab, cen = two_voices(np.stack([emb[i] for i in pool]),
                          [{"A": 0, "B": 1}.get(ai[i]) for i in pool])
    # Which group is A: the way round that agrees with more of the AI's solo lines (the AI's own naming of them).
    fit = [sum(1 for k, i in enumerate(pool) if ai[i] in SOLO and (ai[i] == "A") == (lab[k] == way)) for way in (0, 1)]
    a_grp = 0 if fit[0] >= fit[1] else 1
    # Then whether the AI's names belong the other way round (bb's duet eval, 28 Nov: Ain't No Mountain and Endless
    # Love lost every line to swapped names; the AI's line calls there were 18% and 13% right, so its vote named the
    # groups wrong): by pitch where it says a man and a woman and the groups are clearly apart; else by its own
    # opening solo line. (Eval 28 Nov, 587 lines: by its vote 358 right, by pitch then its opening line 382.)
    gp = [[midi[i] for k, i in enumerate(pool) if lab[k] == g and midi[i] is not None] for g in (0, 1)]
    gp = [float(np.median(x)) if len(x) >= DUET_MIN_LINES else None for x in gp]
    sexes = [v.get("sex") for v in voices]
    named, name_grp = "vote", a_grp
    if None not in gp and sorted(sexes) == ["man", "woman"] \
            and abs(gp[0] - gp[1]) >= NAMES_PITCH_S:
        low = 0 if gp[0] < gp[1] else 1                    # the man's group is the lower
        name_grp, named = (low if sexes[0] == "man" else 1 - low), "pitch"
    else:
        k0 = next((k for k, i in enumerate(pool) if ai[i] in SOLO), None)
        if k0 is not None:
            name_grp, named = (int(lab[k0]) if ai[pool[k0]] == "A" else 1 - int(lab[k0])), "first"
    if name_grp != a_grp:
        voices = voices[::-1]                              # the groups as the AI split them, its names swapped
        named += ", swapped"
    told = sum(1 for i in pool if ai[i] in SOLO)
    group = {i: ("A" if lab[k] == a_grp else "B") for k, i in enumerate(pool)}
    ca, cb = (cen[0], cen[1]) if a_grp == 0 else (cen[1], cen[0])
    m = [float(e @ ca - e @ cb) if e is not None else None for e in emb]      # > 0: sounds like A
    own = [m[i] if group[i] == "A" else -m[i] for i in pool]
    apart = float(np.mean(own))
    confirmed = {k: sum(1 for i in pool if ai[i] == k and group[i] == k) for k in SOLO}
    share = max(fit) / told if told else 0.0
    stats = {"solo": len(pool), "told": told, "agree": round(share, 3), "apart": round(apart, 3),
             "alike": round(float(ca @ cb), 3), "confirmed": confirmed, "named": named}
    if apart < DUET_MIN_APART:
        return no("the recording doesn't sound like two voices", **stats)
    if min(confirmed.values()) < DUET_MIN_LINES or share < DUET_MIN_AGREE:
        return no("the recording doesn't bear the AI out", **stats)

    # Each line is its group's voice (bb's duet eval, 28 Nov: the embedding alone, nearest voice, 95% right on solo
    # lines against the key; the AI's own line calls 69%, and taken where the two agreed, 68%). The AI keeps both
    # and backing (the lead stem carries only the louder voice of two).
    parts: list[Optional[str]] = [None] * n
    for i in range(n):
        p = ai[i]
        if p in ("both", "backing"):
            parts[i] = p
        elif m[i] is not None and (worded(texts[i]) or abs(m[i]) >= AGREE_MARGIN):
            parts[i] = "A" if m[i] > 0 else "B"
    # A line that sounds like neither voice more than the other: both singing (BOTH_BELOW).
    for i in range(n):
        if parts[i] in SOLO and m[i] is not None and abs(m[i]) < BOTH_BELOW:
            parts[i] = "both"
    # A line too short to hear keeps the AI's part when the lines either side of it are that voice too.
    for i in range(n):
        if parts[i] is None and ai[i] in SOLO and m[i] is None:
            before = next((parts[j] for j in range(i - 1, -1, -1) if parts[j]), None)
            after = next((parts[j] for j in range(i + 1, n) if parts[j]), None)
            if before == after == ai[i]:
                parts[i] = ai[i]
    tone = {k: [midi[i] for i in range(n) if parts[i] == k and midi[i] is not None] for k in SOLO}
    tone = {k: (float(np.median(v)) if len(v) >= DUET_MIN_LINES else None) for k, v in tone.items()}
    stats["pitch"] = {k: (None if v is None else round(v, 1)) for k, v in tone.items()}
    echoes = {int(i): e for i, e in (prior.get("echoes") or {}).items()
              if str(i).isdigit() and int(i) < n and parts[int(i)] is not None}
    # A is whoever sings first: by the first line either the sound or the AI gives to one voice (28 Nov, Don't Go
    # Breaking My Heart: Elton's first line unmarked made Kiki "first", and the first singer named got her part).
    first = next((parts[i] if parts[i] in SOLO else ai[i] for i in range(n) if parts[i] in SOLO or ai[i] in SOLO), "A")
    if first == "B":
        swap = {"A": "B", "B": "A"}
        parts = [swap.get(p, p) if p else p for p in parts]
        echoes = {i: swap.get(e, e) for i, e in echoes.items()}
        voices = voices[::-1]
        stats["pitch"] = {"A": stats["pitch"]["B"], "B": stats["pitch"]["A"]}
    stats["marked"] = sum(1 for p in parts if p)
    return {"duet": True, "shown": True, "voices": voices, "parts": parts, "echoes": echoes,
            "why": f"{stats['marked']}/{n} lines marked; the AI's lines agree with the sound for {share:.0%}",
            "stats": stats}


def redecide(song_dir: Path, lines: Sequence[Mapping[str, Any]]) -> Optional[dict]:
    """The verdict made again from what was kept (the AI's answer in duet.json, the sound in voices.json), without
    asking or listening again: for a better ``decide``. None if either is missing or for other lyrics."""
    d = Path(song_dir)
    old = load(d, lines)
    try:
        heard = json.loads((d / "voices.json").read_text())
    except (OSError, ValueError):
        heard = None
    if not old or not isinstance(old.get("ai"), Mapping) or not heard or heard.get("made_from") != key(lines):
        return None
    a = old["ai"]
    prior = {"duet": bool(a.get("duet")), "voices": a.get("voices") or [], "parts": a.get("parts") or [None] * len(lines),
             "echoes": {int(i): e for i, e in (a.get("echoes") or {}).items() if str(i).isdigit()}}
    texts = [str(x.get("text") or "") for x in lines]
    v = decide(len(lines), prior, heard.get("lines") or [], [text_tag(t, prior["voices"]) for t in texts], texts)
    v["ai"] = dict(a)
    return save(d, v, lines, by=str(old.get("by") or "ai"))


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
