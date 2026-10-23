"""Joplin from the sheet music (owner, 23 Oct: "a purist ... replace all the joplin midis with ones that are faithful").

Swaps every Scott Joplin item in the staged ragtime shelf (_staging/ragtime/manifest.json, written by
fetch_ragtime.py) for Colin D. MacDonald's ragtimemusic.com sequence of the same piece: the complete Joplin, sequenced
note for note from the original sheet music. Titles it doesn't have keep their current take (A Real Slow Drag).
Re-runnable: run after fetch_ragtime.py, then assemble.py.

    ~/.venvs/sm-musiclib/bin/python -I tools/music_library/joplin_rtm.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

import mido

SHELF = pathlib.Path.home() / ".local/share/screen-machine/midi-library/_staging/ragtime"
BASE = "https://ragtimemusic.com/"
UA = "Mozilla/5.0 (X11; Linux x86_64) screen-machine music library"
SOURCE = "Colin D. MacDonald's ragtimemusic.com"
LICENCE = "Copyright 1994-2001 Colin D. MacDonald"

# catalogue title -> the file on https://ragtimemusic.com/joplin.html
FILES = {
    "A Breeze from Alabama": "midifile/breezefa.mid", "Antoinette": "midifile/antointe.mid",
    "Augustan Club Waltz": "midifile/augustan.mid", "Bethena": "midifile/bethena.mid",
    "Bink's Waltz": "midifile/binkswlz.mid", "Cleopha": "midifile/cleopha.mid",
    "Combination March": "midifile/combntnm.mid", "Country Club": "midifile/cntryclb.mid",
    "Elite Syncopations": "midifile/elitesyn.mid", "Eugenia": "midifile/eugenia.mid",
    "Euphonic Sounds": "midifile/euphonic.mid", "Felicity Rag": "midifile/felicity.mid",
    "Fig Leaf Rag": "midifile/figleaf.mid", "Gladiolus Rag": "midifile/gladiols.mid",
    "Harmony Club Waltz": "midifile/harmonyc.mid", "Heliotrope Bouquet": "midifile/heliotrp.mid",
    "Kismet Rag": "midifile/kismet.mid", "Leola": "midifile/leola.mid", "Lily Queen": "midifile/lilyquen.mid",
    "Magnetic Rag": "midifile/magnetic.mid",
    "Maple Leaf Rag": "midifile/Maple_Leaf_Rag_[1899]_composed_by_Scott_Joplin_www.ragtimemusic.com.mid",
    "March Majestic": "midifile/mmajstic.mid", "Original Rags": "midifile/original.mid",
    "Palm Leaf Rag": "midifile/palmleaf.mid", "Paragon Rag": "midifile/paragon.mid",
    "Peacherine Rag": "midifile/peachern.mid", "Pine Apple Rag": "midifile/pineappl.mid",
    "Pleasant Moments": "midifile/plsntmmt.mid", "Reflection Rag": "midifile/reflectn.mid",
    "Rose Leaf Rag": "midifile/roseleaf.mid", "Scott Joplin's New Rag": "midifile/newrag.mid",
    "Search-Light Rag": "midifile/srchlite.mid", "Silver Swan Rag": "midifile/slvrswan.mid",
    "Solace": "midifile/solace.mid", "Something Doing": "midifile/somthngd.mid",
    "Stoptime Rag": "midifile/stoptime.mid", "Sugar Cane": "midifile/sugrcane.mid",
    "Sunflower Slow Drag": "midifile/sunflwrd.mid", "Swipesy": "midifile/swipesy.mid",
    "The Cascades": "midifile/cascades.mid", "The Chrysanthemum": "midifile/chrysnth.mid",
    "The Easy Winners": "midifile/ezwnnrs.mid", "The Entertainer": "midifile/entertnr.mid",
    "The Favorite": "midifile/favorite.mid", "The Great Crush Collision March": "midifile/crushcm.mid",
    "The Nonpareil": "midifile/nonparel.mid", "The Ragtime Dance": "midifile/ragdance.mid",
    "The Rosebud March": "midifile/rosebudm.mid", "The Strenuous Life": "midifile/strelife.mid",
    "The Sycamore": "midifile/sycamore.mid", "Wall Street Rag": "midifile/wallst.mid",
    "Weeping Willow": "midifile/weepingw.mid",
}


def fetch(path: str, dest: pathlib.Path) -> bytes:
    if dest.exists():
        return dest.read_bytes()
    req = urllib.request.Request(BASE + urllib.parse.quote(path), headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            data = urllib.request.urlopen(req, timeout=30).read()
            if data[:4] != b"MThd":
                raise ValueError("not a MIDI file")
            dest.write_bytes(data)
            time.sleep(1.0)
            return data
        except Exception:                              # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 ** attempt * 2)
    raise RuntimeError("unreachable")


def main() -> int:
    dry = "--dry-run" in sys.argv
    man_path = SHELF / "manifest.json"
    man = json.loads(man_path.read_text())
    swapped, kept = [], []
    for it in man["items"]:
        if "joplin" not in (it.get("composer") or "").lower():
            continue
        path = FILES.get(it["title"])
        if path is None:
            kept.append(it["title"])
            continue
        name = "Joplin-RTM-" + pathlib.Path(path).name.replace("[", "").replace("]", "")
        dest = SHELF / "files" / name
        if not dry:
            fetch(path, dest)
            m = mido.MidiFile(str(dest), clip=True)
            notes = sum(1 for tr in m.tracks for e in tr if e.type == "note_on" and e.velocity > 0)
            it.update({"src": f"files/{name}", "performer": None, "source": SOURCE, "source_ref": BASE + path,
                       "licence": LICENCE, "take": "sequenced", "duration_s": round(m.length, 1), "notes": notes,
                       "tags": [t for t in (it.get("tags") or []) if t not in ("performance", "transcription", "roll",
                                                                                "liberal")] + ["from the score"]})
        swapped.append(it["title"])
    if not dry:
        tmp = man_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(man, ensure_ascii=False, indent=1))
        os.replace(tmp, man_path)
    print(json.dumps({"swapped": len(swapped), "kept": kept, "dry_run": dry}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
