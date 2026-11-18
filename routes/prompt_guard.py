"""Keeps a refined prompt from quietly losing what the request asked for (owner, 17 Nov 2026: no implicit censorship
outside the workflows that mean it, e.g. Nous). The refiner lists what must survive (must_keep); this also looks
for the sensitive words and named people in the request itself, since a model that softens may leave them off its
own list. If any is missing from the refined prompt, the request's own words go in front of it, so the image model
still gets them.

A picture that is chiefly a figure's pose or act, a body's injury, or a named person (the refiner's focus "figure")
is cut to its first sentences, about FIGURE_WORDS words: image models follow a pose, an act or a likeness less the
more is written around it (18 Nov 2026 tests: the same act drawn from ~60 words, lost at ~170), and the prompt
model does not keep to a word limit it is given."""
import re

FIGURE_WORDS = 100
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")

# Words whose loss is softening: bodies, sex, injury, death, violence
SENSITIVE = re.compile(
    r"\b(nude|nudes|naked|nudity|topless|bare[- ]breasted|breasts?|nipples?|genitals?|penis|vagina|vulva|buttocks|"
    r"erotic|sexual|sex|intercourse|orgasm|"
    r"blood|bloody|bleeding|gore|wounds?|wounded|injur\w*|corpses?|dead|death|severed|decapitat\w*|behead\w*|"
    r"execution|executed|torture\w*|murder\w*|massacre|crucif\w*|war|rape|suicide)\b", re.I)
# Two or more capitalised words in a row, e.g. "Keir Starmer", "Xi Jinping", "Francis Bacon"
NAME = re.compile(r"\b([A-Z][a-z'\-]+(?:\s+(?:de|van|von|da|di|le|la)?\s*[A-Z][a-z'\-]+)+)")


def protected(request, must_keep=None):
    """Terms the refined prompt must still contain: the refiner's must_keep, sensitive words and named people."""
    terms = {t.strip() for t in (must_keep or []) if isinstance(t, str) and t.strip()}
    terms |= {m.group(0) for m in SENSITIVE.finditer(request or "")}
    terms |= {m.group(1) for m in NAME.finditer(request or "")}
    return sorted(terms, key=str.lower)


def missing(refined, terms):
    text = (refined or "").lower()
    return [t for t in terms if t.lower() not in text]


def guard(request, refined, must_keep=None):
    """(prompt to use, terms that were missing). Missing terms put the request's own words in front of the
    refined prompt; nothing missing returns the refined prompt unchanged."""
    lost = missing(refined, protected(request, must_keep))
    if not lost or not request:
        return refined, []
    return f"{request.strip().rstrip('.')}. {refined}", lost


def trim_figure(refined, limit=FIGURE_WORDS):
    """The refined prompt cut to whole sentences within ``limit`` words; the first two sentences (the request and its
    concrete restatement) are always kept."""
    sentences = SENTENCE.split((refined or "").strip())
    out, n = [], 0
    for i, s in enumerate(sentences):
        w = len(s.split())
        if i >= 2 and n + w > limit:
            break
        out.append(s)
        n += w
    return " ".join(out)
