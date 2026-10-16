#!/usr/bin/env python3
"""Fetch the test-audio library: a few Creative Commons / public-domain tracks, one or more per vocab genre.

    python tools/show/fetch_test_music.py                  # into ~/.cache/screen-machine/test-music/
    python tools/show/fetch_test_music.py --list           # what it would fetch (no network)
    python tools/show/fetch_test_music.py --only jazz-doxent-1973,house-galeria-disco-memorias --force
    python tools/show/fetch_test_music.py --dir /tmp/music

show_service's test audio (POST /show/api/v1/test {"source": "audio"}) plays these as the VBAN stream ``test-audio``,
so the room can be exercised with real music from another room (show_service/README.md, S3.10). The tracks are not
in git: this script downloads them, unchanged, with the standard library only (urllib), to ``D/<id>.<ext>`` and
writes ``D/index.json`` = {id: {title, artist, genre, license, license_url, page, credit, file, bytes, sha256,
duration_s, fetched_at}}, which show_service reads for labels.

Every licence below was checked by hand on the page in ``page`` on 16 Oct 2026 (config/live/test-music/SOURCES.md
lists them with their attribution lines). Only CC0, CC BY, CC BY-SA and public domain; nothing NonCommercial or
NoDerivatives, and nothing whose licence was ambiguous. ``sha256`` pins each file as fetched then: a file whose
checksum differs is not installed (the upstream file changed: check its page again before taking it with
``--no-verify``). A present file with the right checksum is skipped, so re-running is cheap.

Downloads send the track's ``page`` as the Referer (ccMixter refuses hotlinks without one) and a descriptive
User-Agent (Wikimedia's policy).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Optional

DEFAULT_DIR = pathlib.Path.home() / ".cache" / "screen-machine" / "test-music"   # = contract.TEST_MUSIC_DIR
USER_AGENT = "screen-machine-test-music/1.0 (private test rig; python-urllib)"
MAX_BYTES = 30 * 1024 * 1024        # one file; every track below is well under it
TIMEOUT_S = 60.0
GENRES = ("pop", "techno", "house", "jazz", "ambient", "classical", "rock", "soul")   # config/live/vocab.yaml genre
LICENSES = ("CC0", "CC BY", "CC BY-SA", "public domain")

# Each entry: id (the file stem), title, artist, genre (a vocab genre), license (one of LICENSES), license_url (the
# exact version), url (the audio file), page (where the licence is stated), credit (the attribution line), bytes
# (as published), sha256 (as fetched 16 Oct 2026). Sizes total about 40 MB.
SOURCES: list[dict] = [
    {"id": "jazz-doxent-1973",
     "title": "1973", "artist": "Doxent Zsigmond (doxent)", "genre": "jazz",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "url": "https://ccmixter.org/content/doxent/doxent_-_1.mp3",
     "page": "https://ccmixter.org/files/doxent/41411",
     "credit": "\"1973\" by Doxent Zsigmond (doxent), ccMixter, CC BY 3.0",
     "note": "piano, upright bass and drums in 6/8, 73 bpm", "bytes": 3176534, "sha256":
     "20081378b985d5603101d1c0679612ba88b4011efd988e1407f8a3ed13146954"},
    {"id": "techno-mr-dee-storm",
     "title": "Storm", "artist": "Mr.Dee", "genre": "techno",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "url": "https://archive.org/download/shoki005g/shoki005g-03_-_Mr.Dee_-_Storm.ogg",
     "page": "https://archive.org/details/shoki005g",
     "credit": "\"Storm\" by Mr.Dee, from Grey EP [shoki005g], Shoki Recordings, CC BY 3.0",
     "note": "minimal techno", "bytes": 4478365, "sha256":
     "9f7b02724e800f1b81042cddcd94cb97805677a705686556ff26f20f4c4eca80"},
    {"id": "house-galeria-disco-memorias",
     "title": "Memorias", "artist": "Galería Disco", "genre": "house",
     "license": "CC BY-SA", "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
     "url": "https://archive.org/download/MNF015_Galeria_Disco--Memorias_EP/02_Galeria_Disco_-_Memorias.ogg",
     "page": "https://archive.org/details/MNF015_Galeria_Disco--Memorias_EP",
     "credit": "\"Memorias\" by Galería Disco, from Memorias EP [MNF015], Monofónicos Netlabel, CC BY-SA 3.0",
     "note": "deep house", "bytes": 4361765, "sha256":
     "42ae03c5ba2d65e4216bfb853f572e0b5cb016cb8e4dc06124ebc7fb129ee686"},
    {"id": "ambient-lee-rosevere-decompress",
     "title": "Decompress", "artist": "Lee Rosevere", "genre": "ambient",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/4.0/",
     "url": "https://archive.org/download/leerosevere_musicforpodcasts_5/"
            "Lee%20Rosevere%20-%20Music%20For%20Podcasts%205%20-%2013%20Decompress.ogg",
     "page": "https://archive.org/details/leerosevere_musicforpodcasts_5",
     "credit": "\"Decompress\" by Lee Rosevere, from Music For Podcasts 5, CC BY 4.0",
     "note": "ambient electronic", "bytes": 2649629, "sha256":
     "6a33fcd1a584bff7fc873c9faf5c2fd61e027e20873ca761fa87b3dbaa3e3a12"},
    {"id": "classical-mendelssohn-saltarello",
     "title": "Symphony No. 4 in A major 'Italian', IV. Saltarello: Presto (Mendelssohn)",
     "artist": "Musopen Symphony (Musopen, 2012)", "genre": "classical",
     "license": "public domain", "license_url": "https://creativecommons.org/publicdomain/mark/1.0/",
     "url": "https://upload.wikimedia.org/wikipedia/commons/transcoded/f/f7/"
            "Mendelssohn_-_Symphony_No._4_in_A_major%2C_Op._90_%27Italian%27_-_IV._Saltarello._Presto_%28Musopen_"
            "Symphony%29.flac/Mendelssohn_-_Symphony_No._4_in_A_major%2C_Op._90_%27Italian%27_-_IV._Saltarello."
            "_Presto_%28Musopen_Symphony%29.flac.ogg",
     "page": "https://commons.wikimedia.org/wiki/File:Mendelssohn_-_Symphony_No._4_in_A_major,_Op._90_"
             "%27Italian%27_-_IV._Saltarello._Presto_(Musopen_Symphony).flac",
     "credit": "Mendelssohn, Symphony No. 4, IV (Musopen Symphony recording), public domain, via Wikimedia Commons",
     "note": "orchestra; Wikimedia Commons' Ogg Vorbis transcode of the FLAC", "bytes": 4145231, "sha256":
     "ae9250e09939e10685f13d791e7095095d144a9ca04cc77732dff0706961909c"},
    {"id": "rock-josh-woodward-airplane-mode",
     "title": "Airplane Mode", "artist": "Josh Woodward", "genre": "rock",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "url": "https://archive.org/download/jamendo-124697/01-1057411-Josh%20Woodward-Airplane%20Mode.mp3",
     "page": "https://archive.org/details/jamendo-124697",
     "credit": "\"Airplane Mode\" by Josh Woodward, CC BY 3.0 (Jamendo album 124697, Internet Archive mirror)",
     "note": "indie rock: guitars, drums, vocals. The artist's own page "
             "(https://www.joshwoodward.com/song/AirplaneMode, checked 16 Oct) licenses it CC BY 4.0; Jamendo track "
             "1057411", "bytes": 5408954, "sha256":
     "3025e6a27c03a589e64c5822c522c9c6425ddeb6194cebbd6e0e36ff70b8e909"},
    {"id": "pop-lilly-wolf-play-the-game",
     "title": "Play the Game", "artist": "Lilly Wolf", "genre": "pop",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "url": "https://archive.org/download/jamendo-130349/03-1093616-Lilly%20Wolf-Play%20the%20Game.mp3",
     "page": "https://archive.org/details/jamendo-130349",
     "credit": "\"Play the Game\" by Lilly Wolf, from Play Loud, CC BY 3.0 (Jamendo album 130349, Internet Archive "
               "mirror)",
     "note": "synth-pop with vocals. Jamendo track 1093616 (https://www.jamendo.com/track/1093616): its licence is "
             "drawn in the browser only, not yet read by a person; the mirror's metadata says CC BY 3.0",
     "bytes": 6032164, "sha256":
     "6698a4847e8848148bea00f6d17e03e951f4fb02e91b85dd84aeeb20d72384d0"},
    {"id": "soul-vj-memes-peacelovesoul",
     "title": "PeaceLoveSoul", "artist": "VJ_Memes (Jeris), vocals by KungFuFrijters (Joel Frijters)", "genre": "soul",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/3.0/",
     "url": "https://ccmixter.org/content/VJ_Memes/VJ_Memes_-_PeaceLoveSoul_1.mp3",
     "page": "https://ccmixter.org/files/VJ_Memes/35859",
     "credit": "\"PeaceLoveSoul\" by VJ_Memes (Jeris) feat. KungFuFrijters (Joel Frijters), ccMixter, CC BY 3.0",
     "note": "soul: Rhodes, clavinet, Hammond, horns, male vocals", "bytes": 5664436, "sha256":
     "92729a6f9eb17106aa84401115af56a6c5f8c7e21872daedabbf5b254e1213aa"},
    {"id": "soul-admiral-bob-light-me-on-fire",
     "title": "Light Me On Fire", "artist": "Admiral Bob (admiralbob77), sax samples by stefsax", "genre": "soul",
     "license": "CC BY", "license_url": "https://creativecommons.org/licenses/by/2.5/",
     "url": "https://ccmixter.org/content/admiralbob77/admiralbob77_-_Light_Me_On_Fire.mp3",
     "page": "https://ccmixter.org/files/admiralbob77/30046",
     "credit": "\"Light Me On Fire\" by Admiral Bob (admiralbob77) feat. stefsax, ccMixter, CC BY 2.5",
     "note": "funk / boogie: guitar, saxophone, vocals", "bytes": 3333552, "sha256":
     "d2a4d7112205045a11dd8873f8fff962f8b76c0976585f0473b13980e1410f52"},
]


# -- helpers ----------------------------------------------------------------------------------------------------------
def ext_of(url: str) -> str:
    """The audio file's extension from its URL ("ogg" for Commons' "X.flac.ogg" transcodes)."""
    name = urllib.parse.unquote(urllib.parse.urlsplit(url).path).rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext if ext.isalnum() and len(ext) <= 5 else "bin"


def file_name(src: dict) -> str:
    return f"{src['id']}.{ext_of(src['url'])}"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_duration(path: pathlib.Path) -> Optional[float]:
    """Seconds, from ffprobe when it is installed (else None)."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", str(path)],
                             capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        return round(float(out), 2)
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def check_sources(sources: Iterable[dict]) -> list[str]:
    """Problems with a SOURCES list (empty = fine): fields, licences, genres, ids."""
    problems, seen = [], set()
    for i, s in enumerate(sources):
        where = s.get("id") or f"#{i}"
        for key in ("id", "title", "artist", "genre", "license", "url", "page"):
            if not isinstance(s.get(key), str) or not s[key].strip():
                problems.append(f"{where}: {key} missing")
        if s.get("license") not in LICENSES:
            problems.append(f"{where}: licence {s.get('license')!r} not one of {LICENSES}")
        if s.get("genre") not in GENRES:
            problems.append(f"{where}: genre {s.get('genre')!r} not a vocab genre")
        sid = s.get("id", "")
        if not sid or "/" in sid or sid.startswith(".") or sid in seen:
            problems.append(f"{where}: bad or duplicate id")
        seen.add(sid)
        sha = s.get("sha256")
        if sha is not None and (not isinstance(sha, str) or len(sha) != 64):
            problems.append(f"{where}: sha256 must be 64 hex characters or None")
    return problems


def _download(url: str, dest: pathlib.Path, *, referer: Optional[str], timeout: float) -> tuple[int, str]:
    """url -> dest (written whole or not at all); (bytes, sha256)."""
    headers = {"User-Agent": USER_AGENT}
    if referer and urllib.parse.urlsplit(url).scheme in ("http", "https"):
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    part = dest.with_name(dest.name + ".part")
    h, n = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(part, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_BYTES:
                    raise ValueError(f"larger than {MAX_BYTES} bytes")
                h.update(chunk)
                out.write(chunk)
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    return n, h.hexdigest()


def _read_index(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_index(path: pathlib.Path, index: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# -- the work ---------------------------------------------------------------------------------------------------------
def fetch(sources: list[dict], dest: pathlib.Path, *, force: bool = False, verify: bool = True,
          timeout: float = TIMEOUT_S, log: Callable[[str], None] = print) -> int:
    """Download ``sources`` into ``dest`` and update dest/index.json; returns the number of failures."""
    dest = pathlib.Path(dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    index_path = dest / "index.json"
    index = _read_index(index_path)
    failures = 0
    for src in sources:
        sid, want = src["id"], (src.get("sha256") if verify else None)
        path = dest / file_name(src)
        entry = index.get(sid) if isinstance(index.get(sid), dict) else {}
        if path.is_file() and not force:
            have = sha256_file(path)
            if (want and have == want) or (not want and entry.get("sha256") == have):
                log(f"ok      {sid} ({path.name}, already here)")
                index[sid] = _entry(src, path, have, entry)
                continue
        t0 = time.monotonic()
        try:
            tmp = dest / (path.name + ".new")
            n, sha = _download(src["url"], tmp, referer=src.get("page"), timeout=timeout)
            if want and sha != want:
                tmp.unlink(missing_ok=True)
                raise ValueError(f"sha256 {sha[:12]}… is not the pinned {want[:12]}… (the file changed upstream: "
                                 f"check {src['page']} before taking it with --no-verify)")
            os.replace(tmp, path)
        except Exception as e:                     # noqa: BLE001  one track failing never stops the rest
            failures += 1
            (dest / (path.name + ".new")).unlink(missing_ok=True)
            log(f"FAILED  {sid}: {e}")
            continue
        index[sid] = _entry(src, path, sha, {})
        log(f"fetched {sid} ({n / 1e6:.1f} MB in {time.monotonic() - t0:.1f} s, sha256 {sha})")
    _write_index(index_path, index)
    return failures


def _entry(src: dict, path: pathlib.Path, sha: str, old: dict) -> dict:
    dur = old.get("duration_s") if old.get("sha256") == sha else None
    return {"title": src["title"], "artist": src["artist"], "genre": src["genre"], "license": src["license"],
            "license_url": src.get("license_url"), "page": src["page"], "credit": src.get("credit"),
            "file": path.name, "bytes": path.stat().st_size, "sha256": sha,
            "duration_s": dur if dur is not None else probe_duration(path),
            "fetched_at": old.get("fetched_at") if old.get("sha256") == sha and old.get("fetched_at")
            else _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}


def main(argv: Optional[list[str]] = None, sources: Optional[list[dict]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(DEFAULT_DIR), help=f"where to put them (default {DEFAULT_DIR})")
    ap.add_argument("--only", default=None, help="comma-separated ids")
    ap.add_argument("--list", action="store_true", help="list the tracks and their licences; no network")
    ap.add_argument("--force", action="store_true", help="download again even when present and correct")
    ap.add_argument("--no-verify", action="store_true", help="install even when the sha256 differs from the pin")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    a = ap.parse_args(argv)
    sources = SOURCES if sources is None else sources
    problems = check_sources(sources)
    if problems:
        for p in problems:
            print(f"SOURCES: {p}", file=sys.stderr)
        return 2
    chosen = sources
    if a.only:
        ids = [s.strip() for s in a.only.split(",") if s.strip()]
        unknown = sorted(set(ids) - {s["id"] for s in sources})
        if unknown:
            print(f"unknown id(s): {', '.join(unknown)}; choose from {', '.join(s['id'] for s in sources)}",
                  file=sys.stderr)
            return 2
        chosen = [s for s in sources if s["id"] in ids]
    if a.list:
        total = 0
        for s in chosen:
            total += int(s.get("bytes") or 0)
            print(f"{s['id']:<38} {s['genre']:<10} {s['license']:<14} {s['artist']} - {s['title']}\n"
                  f"{'':<38} {s['page']}")
        print(f"{len(chosen)} tracks, about {total / 1e6:.0f} MB")
        return 0
    failures = fetch(chosen, pathlib.Path(a.dir), force=a.force, verify=not a.no_verify, timeout=a.timeout)
    print(f"{len(chosen) - failures} of {len(chosen)} tracks in {pathlib.Path(a.dir).expanduser()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
