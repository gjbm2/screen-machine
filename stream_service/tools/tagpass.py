"""tagpass: merge a tagging pass's verdicts into config/live/tags.generated.json (tag_overlay.py).

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.tagpass RESULTS.json [--out config/live/tags.generated.json]

RESULTS.json is what the tagging agents returned: {"batches": [{"kind": "scene"|"effect"|"transition", "items": [...]}]},
one item per preset, judged against tagpass_rubric.md (next to this file) by looking at the preset's render and
source: look, energy [lo, hi], moods, style, genre (10 groups, 0..1) + optional genre_words, valence, organic,
density, harsh (effects, transitions), quality, auto_ok (+ not_auto_why), confidence, saw.

Each verdict becomes the preset's overlay entry ("presets"[kind][name]):
  * genre: the 10 group scores spread onto the vocab's genre words (a word takes the best of its groups), then
    genre_words override single words; tag_overlay keeps the map as genre_fit and the words >= 0.5 as genre;
  * transitions: style -> transition_style [style]; scenes and effects keep style;
  * weight = 0.15 + 0.85 x quality (the static prior the owner's stars multiply);
  * auto_ok false -> auto: false (the directors never pick it; it stays playable by hand), with the reason;
  * the one-line description, confidence and what was seen go in "_" notes (kept in the file for a future LLM
    art director; dropped at load, so they never become Room page tag words).
Presets with no tags today (e.g. black, cal-flash: manual-only utilities) are left alone.  Entries already in the
file for presets this pass did not judge are kept, so a later pass over new plug-ins only adds.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

GROUP_WORDS = {
    "ambient": ["ambient", "downtempo"],
    "classical": ["classical", "piano", "soundtrack"],
    "jazz": ["jazz", "blues"],
    "soul": ["soul", "rnb", "funk", "gospel", "disco"],
    "pop": ["pop", "indie", "singer-songwriter", "folk", "country"],
    "rock": ["rock", "metal", "punk"],
    "house": ["house", "disco", "trance", "electronic"],
    "techno": ["techno", "trance", "drum-and-bass", "dubstep", "electronic"],
    "hiphop": ["hip-hop"],
    "world": ["world", "latin", "afrobeat", "reggae"],
}
KINDS = ("scene", "effect", "transition")


def genre_map(groups: dict, words: dict | None, vocab: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for g, ws in GROUP_WORDS.items():
        a = float(groups.get(g, 0.0))
        for w in ws:
            out[w] = max(out.get(w, 0.0), a)
    for w, a in (words or {}).items():
        if w in vocab:
            out[w] = float(a)
    return {w: round(a, 2) for w, a in sorted(out.items()) if w in vocab and a > 0}


def entry(kind: str, v: dict, vocab: dict) -> dict:
    lo, hi = sorted(float(x) for x in v["energy"])
    e: dict = {"_look": v["look"], "energy": [round(lo, 2), round(hi, 2)],
               "moods": [m for m in dict.fromkeys(v["moods"]) if m in vocab.get("moods", [])],
               "genre": genre_map(v["genre"], v.get("genre_words"), vocab.get("genre", [])),
               "valence": round(v["valence"], 2), "organic": round(v["organic"], 2),
               "density": round(v["density"], 2), "quality": round(v["quality"], 2),
               "weight": round(0.15 + 0.85 * float(v["quality"]), 2)}
    if kind == "transition":
        e["transition_style"] = [v["style"]]
    else:
        e["style"] = v["style"]
    if kind != "scene" and v.get("harsh") is not None:
        e["harsh"] = round(v["harsh"], 2)
    if not v.get("auto_ok", True):
        e["auto"] = False
        e["_why"] = v.get("not_auto_why") or "not show art"
    e["_conf"] = round(v.get("confidence", 0.5), 2)
    e["_saw"] = v.get("saw")
    return e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default=str(REPO / "config/live/tags.generated.json"))
    ap.add_argument("--rev", default=None)
    args = ap.parse_args()
    logging.disable(logging.CRITICAL)
    from stream_service.plugins import Registry
    from stream_service.presets import Presets
    presets = Presets(REPO / "config/live", Registry().discover())
    tables = {"scene": presets.scenes, "effect": presets.effects, "transition": presets.transitions}
    vocab = presets.vocab.words
    out_path = pathlib.Path(args.out)
    try:
        old = json.loads(out_path.read_text())
    except (OSError, ValueError):
        old = {}
    doc = {"format": 1, "gen": {}, "plugins": old.get("plugins") or {k: {} for k in KINDS},
           "presets": {k: dict((old.get("presets") or {}).get(k) or {}) for k in KINDS}}
    res = json.loads(pathlib.Path(args.results).read_text())
    n, skipped, unknown = 0, [], []
    for b in res["batches"]:
        kind = b["kind"]
        for v in b["items"]:
            name = v["name"]
            have = tables[kind].get(name)
            if have is None:
                unknown.append(f"{kind}/{name}")
                continue
            if not have.get("tags"):
                skipped.append(f"{kind}/{name}")
                continue
            doc["presets"][kind][name] = entry(kind, v, vocab)
            n += 1
    doc["gen"] = {"rev": args.rev or datetime.datetime.now().strftime("tagpass-%Y%m%d-%H%M"),
                  "by": "screen-machine-2b (tagging pass: renders + source, tagpass_rubric.md)",
                  "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                  "n": {k: len(doc["presets"][k]) for k in KINDS}}
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    tmp.replace(out_path)
    print(f"wrote {out_path}: {n} presets from this pass, totals {doc['gen']['n']}; "
          f"left alone (untagged) {skipped}; unknown {unknown[:10]}")


if __name__ == "__main__":
    main()
