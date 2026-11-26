"""Child mode (docs/KARAOKE.md; owner, 25 Nov: "censors any obvious profanity (and mutes the lyrics when those words
play). e.g. 'you're so fucking special' ... would sound like 'you're so ---- special'").

``mark(doc, sung)`` marks a lyrics doc's swear words (each line's ``bad``: the indices of its space-separated tokens)
and lists the times to mute (``mutes``: [t0, t1] in song seconds, padded by MUTE_PAD_S): every marked word that has a
word timing, and every swear word Whisper heard in the lead vocal (``sung.json``; it catches a line whose words have no
timing). The output window, in child mode, shows a marked word as dashes and mutes the lead vocal over ``mutes``.

Obvious profanity only: the strong swear words and slurs and their forms. Mild words (damn, hell, crap) stay.
"""
from __future__ import annotations

import re
from typing import Any

MUTE_PAD_S = 0.08

# Word stems: a token is a swear word when, lowercased and stripped of punctuation, it matches one of these whole.
_PATTERNS = [
    r"(mother|mutha)?f+u+c*k+(a|ah|ed|er|ers|in|ing|ingly|s| face|head|wit|witted|off|up|boy|ery)?",
    r"(mother|mutha)?f+u+(k|kk|ck)a?",
    r"f+(a|e)ck(ing|in)?",
    r"(bull|horse|dip|holy)?s+h+i+t+(s|ty|ted|ting|tin|head|hole|face|less|e)?",
    r"(dumb|jack|smart|bad)?ass(hole|holes|hat|wipe)?",
    r"arse(hole|holes)?",
    r"bitch(es|in|ing|y|ass)?",
    r"(mother)?cunts?",
    r"dick(head|heads|s)?",
    r"cocks?(sucker|suckers|sucking)?",
    r"puss(y|ies)",
    r"twats?",
    r"wank(er|ers|ing|s)?",
    r"bastards?",
    r"slut(s|ty)?",
    r"whores?",
    r"goddamn(ed|it)?",   # goddamn only (plain damn stays)
    r"nigg(a|as|az|er|ers|ah)",
    r"fag(s|got|gots)?",
    r"retard(s|ed)?",
    r"tits?",
    r"titties",
    r"jizz",
    r"pricks?",
]
_RE = re.compile(r"^(?:" + "|".join(_PATTERNS) + r")$")


def norm_token(tok: str) -> str:
    return re.sub(r"[^a-z]", "", tok.lower().replace("’", "'"))


def is_profane(tok: str) -> bool:
    w = norm_token(tok)
    return bool(w) and bool(_RE.match(w))


def mask(tok: str) -> str:
    """A swear word as dashes (its punctuation kept): "fucking," -> "-------,"."""
    return re.sub(r"[A-Za-z’']", "-", tok)


def mark(doc: dict, sung: list[dict] | None = None) -> dict:
    """``doc`` with each line's ``bad`` token indices and the doc's ``mutes`` (module docstring). Doesn't change
    ``doc`` itself."""
    lines = []
    mutes: list[list[float]] = []
    for ln in doc.get("lines") or []:
        toks = str(ln.get("text") or "").split()
        bad = [k for k, tok in enumerate(toks) if is_profane(tok)]
        words = ln.get("words")
        if bad and isinstance(words, list) and len(words) == len(toks):
            for k in bad:
                w = words[k]
                if isinstance(w, dict) and w.get("t0") is not None and w.get("t1") is not None:
                    mutes.append([float(w["t0"]) - MUTE_PAD_S, float(w["t1"]) + MUTE_PAD_S])
        lines.append({**ln, "bad": bad} if bad else ln)
    for w in sung or []:
        if isinstance(w, dict) and is_profane(str(w.get("w") or "")) and w.get("t0") is not None:
            mutes.append([float(w["t0"]) - MUTE_PAD_S, float(w.get("t1") or w["t0"]) + MUTE_PAD_S])
    return {**doc, "lines": lines, "mutes": _merge(mutes)}


def _merge(spans: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        a = max(0.0, a)
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([round(a, 3), round(b, 3)])
    return [[round(a, 3), round(b, 3)] for a, b in out]


def mask_text(text: Any) -> str:
    """Free text with its swear words as dashes (a title, an artist)."""
    return " ".join(mask(t) if is_profane(t) else t for t in str(text or "").split(" "))


def censor(doc: dict, sung: list[dict] | None = None) -> dict:
    """The lyrics doc as child mode serves it: ``mark``'s ``bad`` and ``mutes``, and each line's swear words already
    as dashes in its ``text`` (every reader gets clean words: the output window and the lyric effects alike)."""
    d = mark(doc, sung)
    lines = []
    for ln in d["lines"]:
        if ln.get("bad"):
            toks = str(ln.get("text") or "").split()
            ln = {**ln, "text": " ".join(mask(t) if k in ln["bad"] else t for k, t in enumerate(toks))}
        lines.append(ln)
    return {**d, "lines": lines, "child": True}


def any_profane(text: Any) -> bool:
    return any(is_profane(t) for t in str(text or "").split())
