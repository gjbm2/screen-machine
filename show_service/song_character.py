"""A karaoke song's character from what is known about it: its catalogue genre and year, and its lyrics' words.

Owner, 26 Nov: "karaoke song context details should be doing more work to shape a.d. decisions ... pacing / styling
/ tonality of music".  The room mic hears a backing track and an amateur singer (often quietly), so the director's
moods came from its energy band ("mellow, elegant, warm" through Creep and Dancing Queen alike); the song's own facts
say far more.  This is the no-AI reading (the treatment's AI, when it has a key, reads the same facts and the lyrics
themselves): deterministic, instant, no network.

  * the genre (a vocabulary word: director.SONG_GENRES) gives a base feel: descriptive words, valence, arousal and
    how acoustic it is (GENRE_FEEL);
  * the lyrics' words shade it (LEXICON: joy, heartbreak, darkness, romance, the epic, the sacred, the dreamy): the strongest
    theme leads the words, and joy / heartbreak / darkness move valence and arousal;
  * the year: before 1990 adds "nostalgic" (retro).
The descriptive words map onto the renderer's moods with vocab.yaml's own ``music_moods`` table.  Masked lyrics
(child mode: dashes) simply carry fewer words.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Deezer's genre names (routes/karaoke/store.py FACTS "genre") -> the vocabulary's genre words (config/live/vocab.yaml)
SONG_GENRES = {"pop": "pop", "rock": "rock", "alternative": "indie", "indie pop": "indie", "indie rock": "indie",
               "metal": "metal", "punk": "punk", "dance": "electronic", "electro": "electronic",
               "techno/house": "house", "rap/hip hop": "hip-hop", "hip hop": "hip-hop", "r&b": "rnb",
               "soul & funk": "soul", "soul": "soul", "funk": "funk", "disco": "disco", "jazz": "jazz",
               "blues": "blues", "reggae": "reggae", "latin music": "latin", "latin": "latin", "country": "country",
               "folk": "folk", "singer & songwriter": "singer-songwriter", "singer/songwriter": "singer-songwriter",
               "r&b/soul": "soul", "soul/funk": "soul", "classical": "classical",
               "films/games": "soundtrack", "film scores": "soundtrack", "african music": "afrobeat",
               "brazilian music": "world", "asian music": "world", "kids": "pop", "christian": "gospel"}

# genre word -> (descriptive words, valence, arousal, organic)
GENRE_FEEL: dict[str, tuple[tuple[str, ...], float, float, float]] = {
    "pop": (("happy", "uplifting"), 0.7, 0.65, 0.4),
    "rock": (("energetic", "driving"), 0.55, 0.75, 0.5),
    "indie": (("wistful", "dreamy"), 0.45, 0.5, 0.55),
    "metal": (("intense", "aggressive"), 0.3, 0.9, 0.4),
    "punk": (("energetic", "aggressive"), 0.5, 0.9, 0.5),
    "electronic": (("driving", "hypnotic"), 0.6, 0.75, 0.1),
    "techno": (("driving", "hypnotic"), 0.5, 0.8, 0.05),
    "house": (("groovy", "uplifting"), 0.7, 0.75, 0.1),
    "trance": (("epic", "hypnotic"), 0.65, 0.8, 0.05),
    "drum-and-bass": (("energetic", "intense"), 0.55, 0.85, 0.1),
    "dubstep": (("intense", "dark"), 0.4, 0.85, 0.05),
    "ambient": (("peaceful", "dreamy"), 0.55, 0.15, 0.4),
    "downtempo": (("chill", "dreamy"), 0.55, 0.35, 0.4),
    "hip-hop": (("groovy", "intense"), 0.5, 0.7, 0.3),
    "rnb": (("sensual", "groovy"), 0.6, 0.55, 0.5),
    "soul": (("romantic", "groovy"), 0.65, 0.55, 0.7),
    "funk": (("funky", "groovy"), 0.8, 0.8, 0.6),
    "disco": (("funky", "joyful"), 0.85, 0.8, 0.4),
    "jazz": (("romantic", "elegant"), 0.55, 0.4, 0.85),
    "blues": (("melancholic", "groovy"), 0.4, 0.45, 0.85),
    "gospel": (("uplifting", "triumphant"), 0.8, 0.65, 0.8),
    "reggae": (("chill", "groovy"), 0.7, 0.5, 0.7),
    "latin": (("joyful", "groovy"), 0.8, 0.75, 0.6),
    "afrobeat": (("joyful", "groovy"), 0.8, 0.75, 0.6),
    "folk": (("nostalgic", "tender"), 0.55, 0.35, 0.95),
    "country": (("nostalgic", "happy"), 0.6, 0.5, 0.85),
    "singer-songwriter": (("tender", "wistful"), 0.5, 0.35, 0.9),
    "classical": (("elegant", "peaceful"), 0.5, 0.25, 0.95),
    "piano": (("tender", "elegant"), 0.5, 0.3, 0.95),
    "soundtrack": (("epic", "dreamy"), 0.55, 0.55, 0.5),
    "world": (("joyful", "peaceful"), 0.65, 0.5, 0.85),
}
DEFAULT_FEEL = (("uplifting",), 0.6, 0.55, 0.5)

# theme -> (words in the lyrics, the descriptive words it brings, valence push, arousal push)
LEXICON: dict[str, tuple[frozenset, tuple[str, ...], float, float]] = {
    "joy": (frozenset("happy dance dancing party sun sunshine smile golden shine shining celebrate alive free fun "
                      "jump high yeah woo hey bright gold".split()), ("joyful", "uplifting"), 0.25, 0.2),
    "heartbreak": (frozenset("cry crying tears alone lonely goodbye lost cold hurt pain broken sorry miss gone "
                             "rain sad empty leave left apart never".split()), ("melancholic", "wistful"), -0.25, -0.15),
    "dark": (frozenset("dark darkness devil blood kill scream war hell fear shadow shadows creep weirdo burn "
                       "monster".split()), ("dark", "intense"), -0.2, 0.15),
    "romance": (frozenset("love lover kiss hold darling heart together forever arms baby honey".split()),
                ("romantic", "tender"), 0.1, -0.05),
    "epic": (frozenset("glory hero heroes king queen kings fly rise champion champions world forever".split()),
             ("epic", "triumphant"), 0.1, 0.15),
    "sacred": (frozenset("hallelujah heaven god angel angels holy pray prayer lord soul grace".split()),
               ("tender", "peaceful"), 0.05, -0.15),
    "dreamy": (frozenset("dream dreams dreaming night moon stars sky float ocean sea stardust".split()),
               ("dreamy", "mysterious"), 0.0, -0.1),
}
THEME_MIN = 0.012          # a theme leads only above this share of the lyrics' words
WORD_RE = re.compile(r"[a-z']+")


def _c01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def themes(text: str) -> dict[str, float]:
    """The share of the lyrics' words in each LEXICON theme."""
    words = [w.strip("'") for w in WORD_RE.findall(text.lower())]
    n = max(1, len(words))
    return {t: sum(1 for w in words if w in row[0]) / n for t, row in LEXICON.items()}


def character(genre: Optional[str], year: Any, lyrics_text: str, music_moods: dict[str, list[str]],
              moods_vocab: list[str]) -> dict:
    """-> {moods (vocabulary words, at most 3), words (descriptive), valence, arousal, organic, theme, why}."""
    feel = GENRE_FEEL.get(genre or "", DEFAULT_FEEL)
    words: list[str] = []
    th = themes(lyrics_text or "")
    top = max(th, key=lambda t: th[t]) if th else None
    val, aro = feel[1], feel[2]
    for t, share in th.items():
        k = min(1.0, share / 0.05)                   # 5% of the words: the theme at full push
        val += LEXICON[t][2] * k
        aro += LEXICON[t][3] * k
    if top is not None and th[top] >= THEME_MIN:
        words += list(LEXICON[top][1])
    else:
        top = None
    words += [w for w in feel[0] if w not in words]
    try:
        old = int(year) < 1990
    except (TypeError, ValueError):
        old = False
    if old and "nostalgic" not in words:
        words.append("nostalgic")
    moods: list[str] = []
    for w in words:
        for m in music_moods.get(w, [w] if w in moods_vocab else []):
            if m in moods_vocab and m not in moods:
                moods.append(m)
    why = ", ".join(x for x in [genre, str(year) if year else None,
                                f"the lyrics: {top}" if top else None] if x)
    return {"moods": moods[:3], "words": words[:4], "valence": round(_c01(val), 3), "arousal": round(_c01(aro), 3),
            "organic": round(feel[3], 3), "theme": top, "why": why}


def excerpt(lines: list[str], limit: int = 1200) -> str:
    """The lyrics for the treatment's AI: each distinct line once, in order, within ``limit`` characters."""
    seen: set[str] = set()
    out: list[str] = []
    n = 0
    for ln in lines:
        k = " ".join(ln.lower().split())
        if not k or k in seen:
            continue
        seen.add(k)
        if n + len(ln) + 3 > limit:
            break
        out.append(ln)
        n += len(ln) + 3
    return " / ".join(out)


_VOCAB: dict = {}


def vocab_moods(path: Any) -> tuple[dict[str, list[str]], list[str]]:
    """vocab.yaml's music_moods table and moods list (cached by the file's mtime); ({}, []) when unreadable."""
    import os
    try:
        mt = os.stat(path).st_mtime
        if _VOCAB.get("key") != (str(path), mt):
            import yaml
            with open(path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            mm = {k: [w for w in v if isinstance(w, str)] for k, v in (raw.get("music_moods") or {}).items()
                  if isinstance(v, list)}
            _VOCAB.update(key=(str(path), mt), mm=mm, moods=[w for w in raw.get("moods") or [] if isinstance(w, str)])
        return _VOCAB["mm"], _VOCAB["moods"]
    except Exception:                                    # noqa: BLE001 - no vocabulary: no moods from the song
        return {}, []


def genre_word(deezer: Any) -> Optional[str]:
    return SONG_GENRES.get(deezer.strip().lower()) if isinstance(deezer, str) else None


__all__ = ["character", "themes", "excerpt", "vocab_moods", "genre_word", "SONG_GENRES", "GENRE_FEEL", "LEXICON"]
