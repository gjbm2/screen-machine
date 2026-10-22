#!/usr/bin/env python3
"""Assemble the live MIDI library from the staged shelves (the music SPEC §1; 8f's show_service/library.py reads it).

Input: the four fetchers' staging folders, ``LIB_DIR/_staging/{classical,ragtime,jazz,pop}/`` (manifest.json,
files/, SOURCES.md). A shelf is a source set, not a genre: every manifest item carries its own ``genre``.

For every staged item:
  1. the raw file is cleaned with show_service.library.clean_midi (the one cleaner, also used for uploads): one piano
     on channel 1, drums and (when a piano part exists) other instruments dropped, CC64/66/67 and tempo / time /
     key signatures kept, everything else dropped. A file it rejects is skipped and listed. ``duration_s`` and
     ``notes`` come from the cleaned file.
  2. one take per title within a genre (key: genre + title + composer surname), best first: take quality
     performance > transcription > roll > arrangement > sequenced, then rank. The same tune in two genres (a jazz
     take and a song take) is two items.
  3. ids: ``<genre>-<composer surname>-<title>`` slugged, <= 80 chars, from those fields only (stable across
     rebuilds); a clash adds the performer's surname, then ``-2``, ``-3`` (best take keeps the plain id).

Output (LIB_DIR = ~/.local/share/screen-machine/midi-library/):
  files/<genre>/<id>.mid   cleaned MIDI (rewritten only when the bytes change)
  catalog.json             {"v":1,"built_at","genres":[{id,label}],"items":{id: {...}}} (SPEC §1), written
                           atomically and only when its content changes (the running show_service reloads it on
                           mtime change); built_at moves only then
  SOURCES.md               the four shelves' SOURCES.md merged
Then files/<genre>/*.mid no longer in the catalogue are removed (only inside files/), after the catalogue is in place.

Re-runnable and idempotent. Usage: assemble.py [--lib-dir DIR] [--dry-run]
Run it niced: flock ~/.cache/screen-machine/cpu.lock nice -n 19 ~/.venvs/sm-show/bin/python tools/music_library/assemble.py
"""
from __future__ import annotations

import argparse
import collections
import datetime
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from show_service import library as L  # noqa: E402  (the library's own cleaner, slug, writers, genres)

SHELVES = ("classical", "ragtime", "jazz", "pop")
TAKE_ORDER = ("performance", "transcription", "roll", "arrangement", "sequenced")
TAKE_TAG = {"performance": "performance", "transcription": "transcription", "roll": "piano roll",
            "arrangement": "arrangement", "sequenced": "sequenced"}
ID_MAX = 80
CATALOG_FIELDS = ("title", "composer", "performer", "genre", "era", "year", "duration_s", "notes", "source",
                  "licence", "tags", "rank")


def surname(name: object) -> str:
    """The first-named person's surname as a slug: "Burt Bacharach & Hal David" -> "bacharach",
    "Traditional (Londonderry Air)" -> "traditional"; "" when there is none."""
    s = re.sub(r"\([^)]*\)", " ", str(name or ""))
    first = re.split(r",|&|\band\b|/", s)[0]
    words = L.tokens(first)
    return L.slug(words[-1]) if words else ""


def base_id(genre: str, composer: object, title: str) -> str:
    head = "-".join(x for x in (genre, surname(composer)) if x)
    t = L.slug(title, 200) or "untitled"
    return f"{head}-{t}"[:ID_MAX].strip("-")


def quality(it: dict) -> tuple:
    take = it.get("take")
    t = TAKE_ORDER.index(take) if take in TAKE_ORDER else len(TAKE_ORDER)
    return (t, -(L._num(it.get("rank")) or 0.0), it["_shelf"], it["src"])


def load_staged(staging: pathlib.Path) -> tuple[list[dict], list[str]]:
    items, problems = [], []
    for shelf in SHELVES:
        mf = staging / shelf / "manifest.json"
        try:
            doc = json.loads(mf.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append(f"{shelf}: manifest not read ({type(e).__name__}: {e})")
            continue
        for x in doc.get("items") or []:
            if not isinstance(x, dict) or not isinstance(x.get("src"), str) or not isinstance(x.get("title"), str):
                problems.append(f"{shelf}: an item without src/title skipped")
                continue
            items.append({**x, "_shelf": shelf})
    return items, problems


def clean_all(items: list[dict], staging: pathlib.Path) -> tuple[list[dict], list[dict]]:
    kept, rejected = [], []
    for it in items:
        genre = L.clean_genre(it.get("genre"))
        where = f"{it['_shelf']}/{it['src']}"
        if genre is None:
            rejected.append({"where": where, "title": it["title"], "why": f"unknown genre {it.get('genre')!r}"})
            continue
        path = (staging / it["_shelf"] / it["src"]).resolve()
        if not path.is_relative_to((staging / it["_shelf"]).resolve()):
            rejected.append({"where": where, "title": it["title"], "why": "file outside its shelf"})
            continue
        try:
            body, report = L.clean_midi(path.read_bytes(), it["title"])
            cut_body, cut_report, cut_s = L.trim_tail(body, report, it["title"])
            if cut_s:
                body, report = cut_body, cut_report
        except L.Rejected as e:
            rejected.append({"where": where, "title": it["title"], "why": f"{e.code}: {e.words}"})
            continue
        except OSError as e:
            rejected.append({"where": where, "title": it["title"], "why": f"not read: {e}"})
            continue
        kept.append({**it, "genre": genre, "_body": body, "_report": report, "_cut_s": cut_s})
    return kept, rejected


def choose(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """One take per (genre, title, composer surname); returns (chosen, dropped duplicates)."""
    groups: dict[tuple, list[dict]] = collections.defaultdict(list)
    for it in items:
        groups[(it["genre"], L.slug(it["title"], 200), surname(it.get("composer")))].append(it)
    chosen, dropped = [], []
    for _k, g in groups.items():
        g.sort(key=quality)
        chosen.append(g[0])
        dropped += g[1:]
    return chosen, dropped


def assign_ids(items: list[dict]) -> None:
    by_base: dict[str, list[dict]] = collections.defaultdict(list)
    for it in items:
        by_base[base_id(it["genre"], it.get("composer"), it["title"])].append(it)
    taken: set = set()
    for b in sorted(by_base):                          # the best take keeps the plain id
        for it in sorted(by_base[b], key=quality):
            cand = b
            if cand in taken:
                p = surname(it.get("performer"))
                cand = f"{b[:ID_MAX - len(p) - 1].strip('-')}-{p}" if p else cand
            n = 2
            while cand in taken:
                suf = f"-{n}"
                cand = f"{b[:ID_MAX - len(suf)].strip('-')}{suf}"
                n += 1
            if not L.SLUG_RE.match(cand):
                raise SystemExit(f"bad id {cand!r} for {it['title']!r}")
            taken.add(cand)
            it["_id"] = cand


def entry(it: dict) -> dict:
    rep = it["_report"]
    tags = [t for t in (it.get("tags") or []) if isinstance(t, str) and t.strip()]
    tw = TAKE_TAG.get(it.get("take"))
    if tw and tw not in tags:
        tags.append(tw)
    year = it.get("year")
    rank = L._num(it.get("rank"))
    e = {"id": it["_id"], "file": f"files/{it['genre']}/{it['_id']}.mid", "title": " ".join(it["title"].split()),
         "composer": L._str(it.get("composer")), "performer": L._str(it.get("performer")), "genre": it["genre"],
         "era": L._str(it.get("era")), "year": year if isinstance(year, int) and not isinstance(year, bool) else None,
         "duration_s": rep["duration_s"], "notes": rep["notes"], "source": L._str(it.get("source")),
         "licence": L._str(it.get("licence")), "tags": list(dict.fromkeys(tags)),
         "rank": round(min(1.0, max(0.0, rank)), 3) if rank is not None else 0.0}
    return {k: e[k] for k in ("id", "file", *CATALOG_FIELDS)}


def merged_sources(staging: pathlib.Path, catalog: dict, rejected: list[dict], dropped: list[dict]) -> str:
    counts = collections.Counter(x["genre"] for x in catalog["items"].values())
    total = sum(x["duration_s"] or 0 for x in catalog["items"].values())
    out = ["# MIDI library: sources and licences", "",
           f"{len(catalog['items'])} pieces ({', '.join(f'{lab} {counts.get(g, 0)}' for g, lab in L.GENRES)}), "
           f"{total / 3600:.1f} hours, assembled by tools/music_library/assemble.py from the four staged shelves "
           "below (each staged by its own tools/music_library/fetch_*.py). Every file was cleaned to one piano on "
           "channel 1 (show_service.library.clean_midi), and a run of pedal-only events after the last note (the "
           "PIAST transcriptions carry their video's pedal noise to its end) was cut where the sound ends. "
           "Private, non-commercial use in this house only; never "
           "redistributed. Licences are recorded per item in catalog.json.", ""]
    if rejected or dropped:
        out += ["Left out at assembly:"]
        out += [f"- {r['title']} ({r['where']}): {r['why']}" for r in rejected]
        out += [f"- {d['title']} ({d['_shelf']}/{d['src']}): a better take of the same title is in" for d in dropped]
        out += [""]
    for shelf in SHELVES:
        try:
            text = (staging / shelf / "SOURCES.md").read_text(encoding="utf-8").strip()
        except OSError:
            out += [f"## {shelf} shelf", "", "(no SOURCES.md staged)", ""]
            continue
        demoted = [("#" + ln) if re.match(r"^#{1,5} ", ln) else ln for ln in text.splitlines()]
        out += ["---", "", *demoted, ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--lib-dir", type=pathlib.Path, default=L.LIB_DIR)
    ap.add_argument("--dry-run", action="store_true", help="clean and report; write nothing")
    args = ap.parse_args()
    lib: pathlib.Path = args.lib_dir.expanduser()
    staging = lib / "_staging"

    staged, problems = load_staged(staging)
    cleaned, rejected = clean_all(staged, staging)
    chosen, dropped = choose(cleaned)
    assign_ids(chosen)
    gorder = {g: i for i, (g, _l) in enumerate(L.GENRES)}
    chosen.sort(key=lambda it: (gorder[it["genre"]], it["_id"]))
    items = {it["_id"]: entry(it) for it in chosen}
    genres = [{"id": g, "label": lab} for g, lab in L.GENRES]
    cat_path = lib / L.CATALOG
    try:
        old = json.loads(cat_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = None
    same = isinstance(old, dict) and old.get("v") == 1 and old.get("genres") == genres and old.get("items") == items
    built_at = old.get("built_at") if same else L._now_iso()
    catalog = {"v": 1, "built_at": built_at, "genres": genres, "items": items}

    written = unchanged = removed = 0
    if not args.dry_run:
        for it in chosen:                              # files first: the catalogue never names a missing file
            p = lib / items[it["_id"]]["file"]
            try:
                if p.read_bytes() == it["_body"]:
                    unchanged += 1
                    continue
            except OSError:
                pass
            L.atomic_write(p, it["_body"])
            written += 1
        if not same:
            L.write_json(cat_path, catalog)
        src_md = merged_sources(staging, catalog, rejected, dropped)
        sp = lib / "SOURCES.md"
        try:
            src_same = sp.read_text(encoding="utf-8") == src_md
        except OSError:
            src_same = False
        if not src_same:
            L.atomic_write(sp, src_md.encode("utf-8"))
        want = {(lib / x["file"]).resolve() for x in items.values()}
        files_root = (lib / "files").resolve()
        for d in sorted(p for p in files_root.iterdir() if p.is_dir()) if files_root.is_dir() else []:
            for f in sorted(d.glob("*.mid")):
                if f.resolve() not in want and f.resolve().is_relative_to(files_root):
                    f.unlink()
                    removed += 1

    counts = collections.Counter(x["genre"] for x in items.values())
    total = sum(x["duration_s"] or 0 for x in items.values())
    print(json.dumps({
        "staged": len(staged), "kept": len(items), "per_genre": {g: counts.get(g, 0) for g, _l in L.GENRES},
        "total_duration_s": round(total, 1), "total_hours": round(total / 3600, 2),
        "tails_trimmed": sum(1 for it in chosen if it["_cut_s"]),
        "tail_seconds_trimmed": round(sum(it["_cut_s"] for it in chosen), 1),
        "rejected": rejected, "duplicates_dropped": [f"{d['_shelf']}/{d['src']}" for d in dropped],
        "problems": problems, "catalog": "unchanged" if same else ("would change" if args.dry_run else "written"),
        "built_at": built_at, "files_written": written, "files_unchanged": unchanged, "files_removed": removed,
        "dry_run": args.dry_run, "lib_dir": str(lib)}, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
