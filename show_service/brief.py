"""brief: the strategy for one karaoke song, made before it plays and kept with the song (owner, 27 Nov 2026).

"for karaoke songs, we could literally just throw the song to AI and ask it for tonal context to pick the _type_ of
scene and pacing pattern that was appropriate. that's the 'narrative shape' piece that I think we lack competently
today (this is independent of the verse/chorus triggers, more about the overall shape/tone/style of aesthetic feel
this song demands). the a.d.'s job is then to execute that strategy properly."

A brief says what kind of show the song asks for, and the shape of the song itself:

  tone       one line, in words ("reverent and spare; it never raises its voice")
  idiom      one of config/live/idioms.yaml's: the vocabulary the art director is held to (idiom.py)
  moods      up to three of vocab.yaml's mood words, and ``genre``: what the scene draws score against
  palette    the song's one palette (one of the feel's), and ``palette_alt``, a second choice: the art director
             takes that when the song before had the first (two golden songs running look like one)
  sections   the song's own map: [{start_s, kind, size, arrive, final?, peak?}], kinds intro | verse | prechorus |
             chorus | bridge | instrumental | outro. ``size`` 0..1 is how big that section should feel in this
             song; ``arrive`` is how the music gets there from the one before: none | swell | bump | snap
  ending     how the song goes out: fade | hold | snap
  why        one plain line for the Room page

It replaces three guesses. The lyric map took a verse sung twice for a chorus (Mr. Brightside: one "chorus" for the
first 76 s, so nothing changed when the real one arrived). The character came from the catalogue's genre and a count
of lyric words (Livin' On a Prayer: "warm, calm, elegant; sacred"). And the feel was the same for every karaoke song.

**Who makes it.** ``ask`` gives an AI the song's facts (``facts``: title, artist, year, the timed lyrics, the
loudness of each stretch of the recording, its tempo and how hard it hits) and the options (the idioms and palettes
in words), and checks every part of the answer against them (``parse``). ``local`` makes one with no AI from the
same facts: the lyric sections as they are, the idiom nearest the recording's sound, the idiom's first palette.
Either is saved beside the song (``save``: brief.json) with what it was made from, so a change to the lyrics or to
this module's VERSION makes it again.

Pure apart from load/save and the one call. Nothing here knows the director.
"""
from __future__ import annotations

import json
import pathlib
import time
from typing import Any, Callable, Mapping, Optional

from . import idiom as idiom_mod

VERSION = 2                            # 2: a section names its first lyric line, whose time is its start (28 Nov)
FILE = "brief.json"
TASK = "brief"                         # config/llm.yaml's task
KINDS = ("intro", "verse", "prechorus", "chorus", "bridge", "instrumental", "outro")
ARRIVE = idiom_mod.ARRIVE
ENDINGS = ("fade", "hold", "snap")
MIN_SECTION_S = 5.0                    # a shorter section joins the one before it
SNAP_S = 1.5                           # a section's start may move this far to a downbeat
#: A sung section starts on its first line: the AI names it (``line``, the lyrics' index) and that line's own time is
#: the start. Its own start_s drifted seconds both ways (nightly eval 28 Nov: Hallelujah's refrains at 42.8 / 97.7 /
#: 142.3 / 197.3 s against the lines at 38.3 / 93.4 / 148.2 / 202.7; |key - brief| medians 3.6-5.2 s in four more
#: songs; 60 of 160 lyric-plan looks off a key boundary). Words are what it reads well; numbers it copies loosely.
#: The art director's own section kinds (lyric_sections / moves.yaml), from the brief's.
DIRECTOR_KIND = {"intro": "instrumental", "verse": "verse", "prechorus": "verse", "chorus": "chorus",
                 "bridge": "verse", "instrumental": "instrumental", "outro": "outro"}
#: The look each kind of section shares (structure's letter: a section with a letter seen before gets that look
#: back). A song has five looks at most: the verse's, the prechorus's (its own: sharing the verse's, it grew the
#: verse's memory into the build, so the second verse came back as the prechorus; eval 27 Nov), the chorus's, the
#: bridge's, and one for where nobody sings.
LETTER = {"verse": "A", "prechorus": "E", "chorus": "B", "intro": "C", "instrumental": "C", "outro": "C", "bridge": "D"}

#: With no AI: the idioms a catalogue genre (Deezer's names, lower case, matched as a part) could be.
GENRE_IDIOMS = {"singer": ("hush", "ballad", "story"), "folk": ("hush", "story"), "jazz": ("ballad", "hush", "groove"),
                "classical": ("hush", "ballad"), "country": ("story", "ballad"), "blues": ("story", "groove", "dark"),
                "soul": ("groove", "ballad", "disco"), "funk": ("groove", "disco"), "r&b": ("groove", "ballad"),
                "disco": ("disco", "groove"), "dance": ("dance", "disco"), "electro": ("dance", "disco"),
                "techno": ("dance",), "house": ("dance",), "metal": ("anthem", "dark"), "punk": ("anthem", "dark"),
                "alternative": ("anthem", "dark", "story"), "indie": ("anthem", "story", "dark"),
                "rock": ("anthem", "story", "ballad", "dark"), "rap": ("groove", "dark"), "hip": ("groove", "dark"),
                "film": ("ballad", "anthem"), "pop": ("groove", "anthem", "disco", "ballad")}

SYSTEM = (
    "You are the lighting and visuals designer for a karaoke party. You are given one song: its title and artist, "
    "its lyrics with the time each line starts in this recording, how loud the recording is through time, and its "
    "tempo. From what you know of the song and what you are given, say what kind of show it asks for and lay out its "
    "shape. Think as a designer would: a serious ballad and a disco hit must not look alike, the look should be held "
    "back until the song earns it, and the biggest look belongs to the song's one high point. Sections: use the "
    "lyrics' own times for where each begins: line is the number of the section's first lyric line (each lyric is "
    "[number, time, text]), words that line's first few words as written, and start_s that line's time; a section "
    "nobody sings (intro, instrumental, a solo) has line -1, no words, and its own start_s. A section that only "
    "repeats the one "
    "before is not a new section; a verse whose words come back later is still a verse. size is how big each "
    "section should feel within THIS song (its quietest about 0.2, its high point 1.0). arrive is how the music "
    "gets to that section: none, swell (a gradual rise), bump (a clear lift on the downbeat) or snap (a sudden hit, "
    "or a sudden drop to quiet). palette is the colour of this song's world; palette_alt a different second choice "
    "that would also suit it. Many songs will be played in one night, so use the whole range of palettes: do not "
    "reach for gold unless the song is truly about glamour or celebration. Choose only from the options given. "
    "Answer with the tool."
)


def tool(options: Mapping[str, Any]) -> dict:
    """The answer's JSON schema, its choices taken from ``options``."""
    return {
        "name": "song_brief",
        "description": "The show this song asks for, and the song's shape.",
        "parameters": {
            "type": "object",
            "properties": {
                "tone": {"type": "string", "description": "One line: the feel of the song, in a designer's words."},
                "idiom": {"type": "string", "enum": list(options.get("idioms") or {})},
                "genre": {"type": "string", "enum": list(options.get("genres") or [])},
                "moods": {"type": "array", "items": {"type": "string", "enum": list(options.get("moods") or [])},
                          "maxItems": 3},
                "palette": {"type": "string", "enum": list(options.get("palettes") or {})},
                "palette_alt": {"type": "string", "enum": list(options.get("palettes") or {}),
                                "description": "a second choice, different from palette"},
                "sections": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"start_s": {"type": "number"},
                                   "line": {"type": "integer", "description": "the number of the section's "
                                            "first lyric line; -1 when nobody sings in it"},
                                   "words": {"type": "string", "description": "that line's first few words"},
                                   "kind": {"type": "string", "enum": list(KINDS)},
                                   "size": {"type": "number"}, "arrive": {"type": "string", "enum": list(ARRIVE)},
                                   "final": {"type": "boolean", "description": "the last full chorus"},
                                   "peak": {"type": "boolean", "description": "the song's one high point"}},
                    "required": ["start_s", "line", "words", "kind", "size", "arrive"]}},
                "ending": {"type": "string", "enum": list(ENDINGS)},
                "why": {"type": "string", "description": "One plain sentence a guest could read."},
            },
            "required": ["tone", "idiom", "moods", "palette", "palette_alt", "sections", "ending", "why"],
        },
    }


# -- what the AI is told ---------------------------------------------------------------------------------------------
def loudness_lines(shape: Optional[Mapping[str, Any]], step_s: float = 8.0) -> list[list[float]]:
    """The song's shape (utils/song_shape: ``v`` 0..1 every ``hop`` s) as [start_s, level] every ``step_s``."""
    v = list((shape or {}).get("v") or ()) if isinstance(shape, Mapping) else []
    hop = float((shape or {}).get("hop") or 0.5) if isinstance(shape, Mapping) else 0.5
    if len(v) < 4 or hop <= 0:
        return []
    k = max(1, int(round(step_s / hop)))
    return [[round(i * hop, 1), round(sum(float(x) for x in v[i:i + k]) / len(v[i:i + k]), 2)]
            for i in range(0, len(v), k)]


def facts(song: Mapping[str, Any], lines: list, shape: Optional[Mapping[str, Any]] = None,
          sound: Optional[Mapping[str, Any]] = None, dur_s: Optional[float] = None) -> dict:
    """What a brief is made from. ``song``: {title, artist, year, genre}; ``lines``: [(start_s, text)] in order;
    ``shape``: utils/song_shape's; ``sound``: {bpm, punch, range_db} where measured."""
    ls = [[round(float(t), 1), str(x)[:120]] for t, x in lines or () if isinstance(t, (int, float)) and str(x).strip()]
    return {"title": song.get("title"), "artist": song.get("artist"), "year": song.get("year"),
            "catalogue_genre": song.get("genre"), "length_s": round(float(dur_s), 1) if dur_s else None,
            "sound": {k: v for k, v in (sound or {}).items() if isinstance(v, (int, float))},
            "loudness": loudness_lines(shape), "climax_s": list((shape or {}).get("climax") or []) or None
            if isinstance(shape, Mapping) else None,
            "lyrics": ls}


def facts_key(f: Mapping[str, Any]) -> str:
    """What a saved brief is good for: the song's lyrics and length (a refine that changes the lines makes it again)."""
    import hashlib
    return hashlib.sha1(json.dumps([f.get("title"), f.get("artist"), f.get("length_s"), f.get("lyrics")],
                                   sort_keys=True, default=str).encode()).hexdigest()[:12]


def options(idioms: Mapping[str, Any], palettes: Mapping[str, str], feel_palettes: Optional[list] = None,
            moods: Optional[list] = None, genres: Optional[list] = None) -> dict:
    """What the AI may choose from, each with its words."""
    pal = {n: palettes.get(n, n) for n in (feel_palettes or list(palettes)) if n != "art"}
    return {"idioms": {n: e.get("for") or e.get("label") or n for n, e in idioms.items()}, "palettes": pal,
            "moods": list(moods or []), "genres": list(genres or [])}


def prompt(f: Mapping[str, Any], opts: Mapping[str, Any]) -> str:
    """The facts and options as the AI reads them: each lyric numbered ([n, time, text]), so a section names its line."""
    song = {**f, "lyrics": [[i, *x] for i, x in enumerate(f.get("lyrics") or ())]}
    return json.dumps({"song": song, "options": opts}, separators=(",", ":"), default=str)


# -- checking an answer ------------------------------------------------------------------------------------------------
def _c01(x: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(x)))
    except (TypeError, ValueError):
        return default


def _words(x: Any) -> str:
    return " ".join("".join(c for c in str(x or "").lower() if c.isalnum() or c.isspace()).split())


def line_of(n: Any, words: Any, texts: list[str], after: int = -1) -> Optional[int]:
    """The lyric line a section means: of the lines (after ``after``) that begin with its ``words``, the one nearest
    the number it gave (it counts loosely: one off, either way; eval 28 Nov); the number itself where its words
    match none; None where it gives neither."""
    w = _words(words)
    n = int(n) if isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0 else None
    if w:
        hits = [i for i, t in enumerate(texts) if i > after and _words(t).startswith(w[:40])]
        if hits:
            return min(hits, key=lambda i: (abs(i - n), i) if n is not None else (i,))
    return n


def line_starts(raw: Any, lyrics: Optional[list], probs: Optional[list[str]] = None) -> list[Optional[float]]:
    """Each answered section's start from the lyric line it names (``line``, an index into ``lyrics``, the facts'
    [[t, text]], and ``words``, its first words: line_of); None where it names none, or a line out of range, or one
    not after the line before it named."""
    probs = probs if probs is not None else []
    ok = [x for x in lyrics or () if isinstance(x, (list, tuple)) and isinstance(x[0], (int, float))]
    times = [float(x[0]) for x in ok]
    texts = [str(x[1]) if len(x) > 1 else "" for x in ok]
    out: list[Optional[float]] = []
    last = -1
    for s in raw if isinstance(raw, (list, tuple)) else ():
        n = line_of(s.get("line"), s.get("words"), texts, last) if isinstance(s, Mapping) else None
        if n is None:
            out.append(None)
            continue
        if n >= len(times) or n <= last:
            probs.append(f"sections: line {n} is {'past the lyrics' if n >= len(times) else 'not after the last'}")
            out.append(None)
            continue
        last = n
        out.append(times[n])
    return out


def clean_sections(raw: Any, dur_s: Optional[float], downbeats: Optional[list] = None,
                   probs: Optional[list[str]] = None, lyrics: Optional[list] = None) -> list[dict]:
    """An answer's sections made safe: in time order from 0.0, kinds and arrivals from the lists, a sung section's
    start its first line's time (``line_starts``; its own start_s where it names none), none shorter than
    MIN_SECTION_S (it joins the one before), each start moved to the nearest downbeat within SNAP_S, sizes 0..1,
    one ``final`` at most (the last chorus marked) and exactly one ``peak`` (the biggest, the later of equals)."""
    probs = probs if probs is not None else []
    rows = []
    raw = list(raw) if isinstance(raw, (list, tuple)) else []
    at_line = line_starts(raw, lyrics, probs)
    for s, lt in zip(raw, at_line):
        if not isinstance(s, Mapping) or not isinstance(s.get("start_s"), (int, float)):
            continue
        kind = s.get("kind") if s.get("kind") in KINDS else None
        if kind is None:
            probs.append(f"sections: kind {s.get('kind')!r} is not one of {', '.join(KINDS)}")
            continue
        t = max(0.0, float(lt if lt is not None else s["start_s"]))
        if dur_s and t >= float(dur_s) - 1.0:
            continue
        rows.append({"start_s": t, "kind": kind, "size": _c01(s.get("size"), 0.5),
                     "arrive": s.get("arrive") if s.get("arrive") in ARRIVE else "none",
                     "final": bool(s.get("final")), "peak": bool(s.get("peak"))})
    rows.sort(key=lambda r: r["start_s"])
    db = sorted(float(x) for x in downbeats or () if isinstance(x, (int, float)))
    out: list[dict] = []
    for r in rows:
        if db and r["start_s"] > 0.0:
            near = min(db, key=lambda d: abs(d - r["start_s"]))
            if abs(near - r["start_s"]) <= SNAP_S:
                r["start_s"] = near
        r["start_s"] = round(r["start_s"], 2)
        if out and r["start_s"] - out[-1]["start_s"] < MIN_SECTION_S:
            if not out[-1]["start_s"] and out[-1]["kind"] == "intro" and r["kind"] != "intro":
                out[-1].update({**r, "start_s": 0.0})            # a song that starts singing at once has no intro
            else:
                out[-1]["peak"] = out[-1]["peak"] or r["peak"]
                out[-1]["final"] = out[-1]["final"] or r["final"]
            continue
        out.append(r)
    if not out:
        return []
    out[0]["start_s"] = 0.0
    out[0]["arrive"] = "none"
    finals = [i for i, r in enumerate(out) if r["final"] and r["kind"] == "chorus"]
    for i, r in enumerate(out):
        r["final"] = bool(finals) and i == finals[-1]
    peaks = [i for i, r in enumerate(out) if r["peak"]]
    top = peaks[-1] if peaks else max(range(len(out)), key=lambda i: (out[i]["size"], i))
    for i, r in enumerate(out):
        r["peak"] = i == top
    out[top]["size"] = max(out[top]["size"], max(r["size"] for r in out))
    return out


def parse(data: Any, opts: Mapping[str, Any], f: Mapping[str, Any], downbeats: Optional[list] = None,
          probs: Optional[list[str]] = None) -> Optional[dict]:
    """An AI's answer checked against the options it was given. None when it has no usable idiom or sections (the
    caller keeps the local brief)."""
    probs = probs if probs is not None else []
    if not isinstance(data, Mapping):
        probs.append("the answer is not an object")
        return None
    idi = data.get("idiom")
    if idi not in (opts.get("idioms") or {}):
        probs.append(f"idiom {idi!r} is not one of the options")
        return None
    secs = clean_sections(data.get("sections"), f.get("length_s"), downbeats, probs, f.get("lyrics"))
    if len(secs) < 2:
        probs.append("fewer than two usable sections")
        return None
    pal = data.get("palette") if data.get("palette") in (opts.get("palettes") or {}) else None
    alt = data.get("palette_alt") if data.get("palette_alt") in (opts.get("palettes") or {}) else None
    moods = [m for m in data.get("moods") or () if m in (opts.get("moods") or ())][:3]
    return {"tone": str(data.get("tone") or "")[:200], "idiom": idi,
            "genre": data.get("genre") if data.get("genre") in (opts.get("genres") or ()) else None,
            "moods": moods, "palette": pal, "palette_alt": alt if alt != pal else None, "sections": secs,
            "ending": data.get("ending") if data.get("ending") in ENDINGS else "fade",
            "why": str(data.get("why") or "")[:200]}


# -- with no AI ----------------------------------------------------------------------------------------------------------
def local(f: Mapping[str, Any], idioms: Mapping[str, Any], lyric_secs: Optional[list] = None,
          character: Optional[Mapping[str, Any]] = None) -> dict:
    """A brief from the facts alone: the lyric sections as they are (sized by the recording's loudness there), the
    idiom nearest the recording's sound (else by the catalogue genre's arousal), that idiom's first palette."""
    # the catalogue's genre says which idioms could fit; the recording's sound picks among those (a tempo counted
    # in double time, Hallelujah at 167, would otherwise make a ballad a club track)
    g = str(f.get("catalogue_genre") or "").lower()
    could = next((v for k, v in GENRE_IDIOMS.items() if k in g), None)
    pool = {n: e for n, e in idioms.items() if could is None or n in could} or idioms
    name = idiom_mod.nearest(pool, f.get("sound")) or (next(iter(pool)) if could else None)
    if name is None and idioms:
        ar = float((character or {}).get("arousal") or 0.5)
        order = [n for n in ("hush", "ballad", "story", "groove", "anthem", "dance") if n in idioms] or list(idioms)
        name = order[min(len(order) - 1, int(ar * len(order)))]
    loud = f.get("loudness") or []

    def level(t0: float, t1: float) -> float:
        xs = [v for t, v in loud if t0 <= t < t1] or [v for t, v in loud if t <= t0][-1:]
        return sum(xs) / len(xs) if xs else 0.5
    rows = []
    src = [s for s in lyric_secs or () if isinstance(s, Mapping) and isinstance(s.get("start_s"), (int, float))]
    end = float(f.get("length_s") or (src[-1]["start_s"] + 20.0 if src else 0.0))
    for i, s in enumerate(src):
        t0 = float(s["start_s"])
        t1 = float(src[i + 1]["start_s"]) if i + 1 < len(src) else end
        k = s.get("kind")
        kind = k if k in KINDS else "chorus" if k == "final_chorus" else "verse"
        rows.append({"start_s": t0, "kind": kind, "size": level(t0, t1), "arrive": "none",
                     "final": k == "final_chorus" or bool(s.get("final"))})
    if not rows:
        rows = [{"start_s": 0.0, "kind": "verse", "size": 0.5, "arrive": "none"}]
    if rows[0]["start_s"] > MIN_SECTION_S:
        rows.insert(0, {"start_s": 0.0, "kind": "intro", "size": level(0.0, rows[0]["start_s"]), "arrive": "none"})
    lo, hi = min(r["size"] for r in rows), max(r["size"] for r in rows)
    for i, r in enumerate(rows):                                 # within this song: its quietest 0.2, its loudest 1
        r["size"] = round(0.2 + 0.8 * (r["size"] - lo) / (hi - lo), 2) if hi - lo > 0.05 else 0.6
        if i and r["size"] - rows[i - 1]["size"] >= 0.15:
            r["arrive"] = (idioms.get(name) or {}).get("arrive", "bump") if name else "bump"
    secs = clean_sections(rows, f.get("length_s"))
    e = idioms.get(name) or {}
    return {"tone": "", "idiom": name, "genre": None, "moods": list((character or {}).get("moods") or [])[:3],
            "palette": (e.get("palettes") or [None])[0], "palette_alt": (e.get("palettes") or [None, None])[1:2]
            and (e.get("palettes") or [None, None])[1], "sections": secs, "ending": "fade",
            "why": f"{e.get('label') or 'A song'}: by its sound" if name else "No idiom fits"}


# -- kept with the song --------------------------------------------------------------------------------------------------
def stamp(b: dict, by: str, key: str, model: Optional[str] = None, wall: Callable[[], float] = time.time) -> dict:
    return {**b, "version": VERSION, "by": by, "model": model, "made_from": key, "made_at": round(wall(), 1)}


def save(song_dir: pathlib.Path, b: Mapping[str, Any]) -> None:
    p = pathlib.Path(song_dir) / FILE
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(b, indent=1))
    tmp.replace(p)


def load(song_dir: pathlib.Path, key: Optional[str] = None) -> Optional[dict]:
    """The song's saved brief; None when there is none, it is another VERSION's, or (with ``key``) it was made from
    other lyrics."""
    try:
        b = json.loads((pathlib.Path(song_dir) / FILE).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(b, dict) or b.get("version") != VERSION or not b.get("sections"):
        return None
    if key is not None and b.get("made_from") != key:
        return None
    return b


# -- what the art director reads from it ---------------------------------------------------------------------------------
def level_of(sec: Mapping[str, Any]) -> str:
    """A section's place in the song for idiom.plays: verse | chorus | peak."""
    if sec.get("peak"):
        return "peak"
    if sec.get("kind") == "chorus" or float(sec.get("size") or 0.0) >= 0.7:
        return "chorus"
    return "verse"


def plan(b: Mapping[str, Any], idioms: Optional[Mapping[str, Any]] = None,
         shape: Optional[Mapping[str, Any]] = None, dur_s: Optional[float] = None) -> list[dict]:
    """The brief's map as the song's plan for structure (songplan.lay_out's shape: start_s, kind, letter, level,
    peak), in the art director's own section kinds. Sections share a letter, and so a look that comes back, by
    LETTER. ``level`` is the brief's size within the idiom's ``level`` range (the designer's intent for the section,
    where lay_out gave the recording's loudness there; that is kept as ``level_raw``). Each also carries ``size``,
    ``arrive``, ``brief_kind`` and ``final``."""
    e = (idioms or {}).get(b.get("idiom")) or idiom_mod.OPEN
    lo, hi = float(e["level"][0]), float(e["level"][1])
    v = list((shape or {}).get("v") or ()) if isinstance(shape, Mapping) else []
    hop = float((shape or {}).get("hop") or 0.5) if isinstance(shape, Mapping) else 0.5
    secs = list(b.get("sections") or ())
    out = []
    for i, s in enumerate(secs):
        bk = s["kind"]
        t0 = float(s["start_s"])
        t1 = float(secs[i + 1]["start_s"]) if i + 1 < len(secs) else float(dur_s or t0 + 20.0)
        size = _c01(s.get("size"), 0.5)
        raw = None
        if len(v) >= 8 and hop > 0:
            i0 = max(0, min(len(v) - 1, int(t0 / hop)))
            i1 = max(i0 + 1, min(len(v), int(t1 / hop)))
            raw = round(sum(float(x) for x in v[i0:i1]) / (i1 - i0), 3)
        out.append({"start_s": t0, "kind": DIRECTOR_KIND.get(bk, "verse"), "letter": LETTER.get(bk, "A"),
                    "level": round(lo + (hi - lo) * size, 3), "level_raw": raw,
            "peak": bool(s.get("peak")), "final": bool(s.get("final")), "size": size,
            "arrive": s.get("arrive") or "none", "brief_kind": bk})
    return out


def view(b: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """What the art director is told of a brief besides its sections."""
    if not isinstance(b, Mapping):
        return None
    return {k: b.get(k) for k in ("idiom", "tone", "palette", "palette_alt", "moods", "genre", "ending", "by", "why")}


# -- making one for a song of the karaoke library -----------------------------------------------------------------------
def song_facts(song_dir: pathlib.Path, song: Mapping[str, Any], shape: Optional[Mapping[str, Any]] = None) -> dict:
    """``facts`` for a library song, from its folder: lyrics.json's lines (their own times, before any offset),
    beats.json's tempo, and its measured shape."""
    d = pathlib.Path(song_dir)
    lines = []
    try:
        lyr = json.loads((d / "lyrics.json").read_text())
        lines = [(x.get("start"), x.get("text") or "") for x in lyr.get("lines") or () if isinstance(x, dict)]
    except (OSError, ValueError):
        pass
    sound: dict[str, float] = {}
    try:
        from .karaoke_feed import FileGrid
        g = FileGrid(json.loads((d / "beats.json").read_text()))
        if g.period > 0:
            sound["bpm"] = round(60.0 / g.period, 1)
    except Exception:                                            # noqa: BLE001 - no beats yet: no tempo
        pass
    loud = sorted(float(x) for x in (shape or {}).get("loud") or () if isinstance(x, (int, float)) and x > -50.0)
    if len(loud) > 20:
        sound["range_db"] = round(loud[int(0.95 * (len(loud) - 1))] - loud[int(0.10 * (len(loud) - 1))], 1)
    return facts(song, lines, shape, sound, song.get("audio_s") or song.get("duration"))


def make(song_dir: pathlib.Path, song: Mapping[str, Any], *, shape: Optional[Mapping[str, Any]] = None,
         catalogue: Optional[Mapping[str, Any]] = None, feel_palettes: Optional[list] = None,
         moods: Optional[list] = None, genres: Optional[list] = None, ai: bool = True, force: bool = False,
         llm: Any = None, log: Optional[Callable[[str], None]] = None) -> dict:
    """The song's brief: the saved one while it still fits the lyrics; else a new one (the AI's, or the local one
    when ``ai`` is off, no model answers or its answer doesn't pass ``parse``), saved beside the song. A local brief
    is replaced by an AI one the next time one can be made."""
    say = log or (lambda _m: None)
    cat = catalogue if catalogue is not None else idiom_mod.load()
    idioms = cat.get("idioms") or {}
    f = song_facts(song_dir, song, shape)
    key = facts_key(f)
    have = None if force else load(song_dir, key)
    if have is not None and (have.get("by") == "ai" or not ai):
        return have
    from .lyric_sections import sections_from_lyrics
    try:
        lsecs = sections_from_lyrics(json.loads((pathlib.Path(song_dir) / "lyrics.json").read_text()),
                                     f.get("length_s"))
    except Exception:                                            # noqa: BLE001 - no lyrics: one section
        lsecs = []
    b, by, model = None, "local", None
    if ai and idioms and f.get("lyrics"):
        opts = options(idioms, cat.get("palettes") or {}, feel_palettes, moods, genres)
        try:
            m = llm
            if m is None:
                from utils import llm as m                       # noqa: PLC0415 - only where an AI is asked
            res = m.call_json(TASK, system=SYSTEM, user=prompt(f, opts), tool=tool(opts), lenient=True)
            probs: list[str] = []
            if getattr(res, "ok", False):
                b = parse(res.data, opts, f, None, probs)
                by, model = ("ai", getattr(res, "model", None)) if b is not None else ("local", None)
            say(f"brief: {song.get('title')}: " + (f"by {model}" if b is not None else
                                                   f"no AI brief ({getattr(res, 'error', None) or '; '.join(probs)})"))
        except Exception as e:                                   # noqa: BLE001 - the local brief stands
            say(f"brief: {song.get('title')}: the AI call failed ({type(e).__name__}: {e})")
    if b is None:
        if have is not None:
            return have
        b = local(f, idioms, lsecs)
    out = stamp(b, by, key, model)
    try:
        save(song_dir, out)
    except OSError as e:
        say(f"brief: {song.get('title')}: not saved ({e})")
    return out


def main() -> int:
    """python -m show_service.brief [song id ...] [--all] [--force] [--local]: make (or show) library songs' briefs."""
    import argparse
    import smconfig
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--all", action="store_true", help="every ready song of the library")
    ap.add_argument("--force", action="store_true", help="make it again")
    ap.add_argument("--local", action="store_true", help="no AI")
    a = ap.parse_args()
    lib = json.loads(smconfig.state("karaoke", "state.json").read_text()).get("songs") or {}
    ids = a.ids or ([k for k, s in lib.items() if s.get("status") == "ready"] if a.all else [])
    import yaml
    vocab = yaml.safe_load((idiom_mod.PATH.parent / "vocab.yaml").read_text()) or {}
    vibes = yaml.safe_load((idiom_mod.PATH.parents[1] / "room" / "vibes.yaml").read_text()) or {}
    pals = ((vibes.get("vibes") or {}).get("singalong") or {}).get("palettes")
    from utils import song_shape
    for sid in ids:
        s = lib.get(sid)
        d = smconfig.state("karaoke", "songs", sid)
        if not isinstance(s, dict) or not d.is_dir():
            print(f"{sid}: not in the library")
            continue
        try:
            shape = song_shape.load_or_measure(d, smconfig.state("show", "shapes", f"{sid}.json"))
        except Exception:                                        # noqa: BLE001
            shape = None
        b = make(d, s, shape=shape, feel_palettes=pals, moods=vocab.get("moods"), genres=vocab.get("genre"),
                 ai=not a.local, force=a.force, log=print)
        print(f"{sid} {str(s.get('title'))[:28]:28s} {b.get('by'):5s} {str(b.get('idiom')):7s} {str(b.get('palette')):8s} "
              + " ".join(f"{x['kind'][:3]}@{x['start_s']:.0f}={x['size']:.1f}{'*' if x.get('peak') else ''}"
                         f"{'' if x['arrive'] == 'none' else '^' + x['arrive'][:2]}" for x in b["sections"]))
        if b.get("tone"):
            print(f"    {b['tone']}  |  {b.get('why')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
