#!/usr/bin/env python3
"""The owner's ready-made playlists (22 Oct), created on the live system through the Music page's own API.

"build a handful of sensible ready-to-go playlists from the midi files we have" and "let's have a virtuoso one too
(lots of liszt, hard chopin, rach, etc.)". Ten lists, each 15-25 pieces and about 45-75 minutes, best-known
first-ish in a listening order (varied keys and tempi, a strong opener, a good closer); a piece sits in at most two
lists. Only catalogue (lib:) pieces and the curated test-piano (file:) pieces; no test patterns.

What it does:
  1. checks every piece resolves to a file (show_service.library.resolve) and reports count + minutes per list;
  2. upserts each list by name through the API (room passes /show/* to show_service): GET /api/v1/playlists, then
     PUT /api/v1/playlists/{id} when a list of that name exists (a re-run), else POST; it never touches the player;
  3. with --stress-out, writes the overnight score stress list: the 14 densest Virtuoso pieces by notes per second
     (at most 5 Chopin and 4 Liszt), one absolute path a line, each after a comment line (title, composer, notes/s,
     duration). Notes and duration are the catalogue's (from the cleaned file); a file: piece is measured with the
     library's own clean_midi (in memory, nothing written).

Usage: ready_playlists.py [--api http://127.0.0.1:8700/show] [--dry-run] [--stress-out PATH]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.parse
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from show_service import library as L  # noqa: E402  (resolve, clean_midi, the catalogue location)

STRESS_N = 14
STRESS_CAPS = {"chopin": 5, "liszt": 4}
STRESS_FROM = "Virtuoso"

PLAYLISTS: dict[str, list[str]] = {
    "Classical favourites": [
        "lib:classical-debussy-suite-bergamasque-iii-clair-de-lune",
        "lib:classical-mozart-piano-sonata-no-11-in-a-major-k-331-iii-rondo-alla-turca",
        "lib:classical-bach-prelude-in-c-major-bwv-846-the-well-tempered-clavier-book-i",
        "lib:classical-beethoven-fur-elise-bagatelle-in-a-minor-woo-59",
        "file:Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid",
        "lib:classical-grieg-peer-gynt-in-the-hall-of-the-mountain-king-piano",
        "lib:classical-satie-gymnopedie-no-1",
        "lib:classical-mozart-piano-sonata-no-16-in-c-major-k-545-sonata-facile-i-allegro",
        "lib:classical-beethoven-piano-sonata-no-14-in-c-sharp-minor-op-27-no-2-moonlight-i-a",
        "lib:classical-chopin-waltz-in-d-flat-major-op-64-no-1-minute-waltz",
        "lib:classical-schumann-kinderszenen-op-15-no-7-traumerei",
        "lib:classical-pachelbel-canon-in-d-arr-for-piano",
        "lib:classical-beethoven-piano-sonata-no-8-in-c-minor-op-13-pathetique-ii-adagio-cant",
        "lib:classical-mendelssohn-song-without-words-in-a-major-op-62-no-6-spring-song",
        "lib:classical-debussy-arabesque-no-1-in-e-major",
        "lib:classical-bach-jesu-joy-of-mans-desiring-from-bwv-147-arr-for-piano",
        "lib:classical-rachmaninoff-prelude-in-c-sharp-minor-op-3-no-2",
        "lib:classical-liszt-liebestraum-no-3-in-a-flat-major-s-541",
    ],
    "Sunday morning": [
        "lib:classical-debussy-reverie-l-68",
        "lib:classical-bach-goldberg-variations-bwv-988-aria",
        "lib:classical-einaudi-nuvole-bianche",
        "lib:classical-grieg-lyric-pieces-op-12-no-1-arietta",
        "lib:classical-schubert-impromptu-in-g-flat-major-d-899-no-3",
        "lib:classical-satie-gymnopedie-no-3",
        "lib:classical-chopin-waltz-in-a-flat-major-op-69-no-1-farewell",
        "lib:pop-yiruma-river-flows-in-you",
        "lib:classical-debussy-preludes-book-1-la-fille-aux-cheveux-de-lin",
        "lib:classical-mendelssohn-song-without-words-in-f-sharp-minor-op-30-no-6-venetian-go",
        "lib:classical-liszt-consolation-no-3-in-d-flat-major-s-172",
        "lib:film-hisaishi-one-summers-day-spirited-away",
        "lib:classical-grieg-lyric-pieces-op-54-no-4-notturno",
        "lib:classical-saens-the-swan-le-carnaval-des-animaux-arr-for-piano",
        "lib:classical-einaudi-le-onde",
        "file:Classical - Chopin - Berceuse, Op. 57.mid",
        "lib:film-morricone-cinema-paradiso-love-theme",
        "lib:classical-elgar-salut-damour-op-12",
    ],
    "Chopin": [
        "file:Classical - Chopin - Nocturne in E-flat, Op. 9 No. 2.mid",
        "lib:classical-chopin-fantaisie-impromptu-in-c-sharp-minor-op-66",
        "lib:classical-chopin-waltz-in-a-minor-b-150-op-posth",
        "lib:classical-chopin-ballade-no-1-in-g-minor-op-23",
        "lib:classical-chopin-prelude-in-e-minor-op-28-no-4",
        "lib:classical-chopin-grande-valse-brillante-in-e-flat-major-op-18",
        "lib:classical-chopin-nocturne-in-c-sharp-minor-b-49-op-posth",
        "lib:classical-chopin-etude-in-c-major-op-10-no-1-waterfall",
        "lib:classical-chopin-prelude-in-d-flat-major-op-28-no-15-raindrop",
        "lib:classical-chopin-polonaise-in-a-major-op-40-no-1-military",
        "lib:classical-chopin-etude-in-e-major-op-10-no-3-tristesse",
        "lib:classical-chopin-waltz-in-c-sharp-minor-op-64-no-2",
        "lib:classical-chopin-nocturne-in-b-flat-minor-op-9-no-1",
        "lib:classical-chopin-prelude-in-a-major-op-28-no-7",
        "lib:classical-chopin-prelude-in-c-minor-op-28-no-20",
        "lib:classical-chopin-ballade-no-4-in-f-minor-op-52",
    ],
    "Virtuoso": [
        "lib:classical-liszt-la-campanella-grandes-etudes-de-paganini-s-141-no-3",
        "lib:classical-chopin-etude-in-c-minor-op-10-no-12-revolutionary",
        "lib:classical-rachmaninoff-prelude-in-g-minor-op-23-no-5",
        "lib:classical-ravel-le-tombeau-de-couperin-vi-toccata",
        "lib:classical-chopin-fantaisie-impromptu-in-c-sharp-minor-op-66",
        "lib:classical-liszt-gnomenreigen-concert-etude-s-145-no-2",
        "lib:classical-scriabin-etude-in-d-sharp-minor-op-8-no-12",
        "lib:classical-rachmaninoff-prelude-in-g-sharp-minor-op-32-no-12",
        "lib:classical-balakirev-islamey-oriental-fantasy-op-18",
        "file:Classical - Chopin - Etude in G-flat, Op. 10 No. 5 (Black Keys).mid",
        "lib:classical-liszt-mephisto-waltz-no-1-s-514",
        "lib:classical-prokofiev-toccata-in-d-minor-op-11",
        "lib:classical-korsakov-flight-of-the-bumblebee-arr-for-piano",
        "lib:classical-chopin-polonaise-in-a-flat-major-op-53-heroic",
        "lib:classical-chopin-etude-in-a-minor-op-25-no-11-winter-wind",
        "lib:classical-liszt-hungarian-rhapsody-no-2-in-c-sharp-minor-s-244",
    ],
    "Joplin & ragtime": [
        "lib:ragtime-joplin-maple-leaf-rag",
        "lib:ragtime-joplin-the-entertainer",
        "lib:ragtime-joplin-elite-syncopations",
        "lib:ragtime-joplin-solace",
        "lib:ragtime-joplin-the-easy-winners",
        "lib:ragtime-joplin-pine-apple-rag",
        "lib:ragtime-joplin-bethena",
        "lib:ragtime-joplin-gladiolus-rag",
        "lib:ragtime-joplin-peacherine-rag",
        "lib:ragtime-joplin-the-cascades",
        "lib:ragtime-joplin-weeping-willow",
        "lib:ragtime-lamb-american-beauty-rag",
        "lib:ragtime-scott-frog-legs-rag",
        "lib:ragtime-lamb-ragtime-nightingale",
        "lib:ragtime-scott-grace-and-beauty",
        "lib:ragtime-johnson-dill-pickles",
        "lib:ragtime-scott-climax-rag",
        "lib:ragtime-morton-jelly-roll-blues",
        "lib:ragtime-morton-king-porter-stomp",
    ],
    "Stride & swing": [
        "lib:jazz-gershwin-i-got-rhythm",
        "lib:ragtime-johnson-carolina-shout",
        "file:Jazz - Art Tatum - Ain't Misbehavin'.mid",
        "lib:ragtime-confrey-kitten-on-the-keys",
        "lib:jazz-arlen-that-old-black-magic",
        "lib:jazz-pinkard-sweet-georgia-brown",
        "lib:ragtime-waller-handful-of-keys",
        "lib:jazz-waller-honeysuckle-rose",
        "lib:ragtime-blake-charleston-rag",
        "lib:jazz-ellington-it-dont-mean-a-thing-if-it-aint-got-that-swing",
        "lib:jazz-noble-cherokee",
        "lib:ragtime-mayerl-marigold",
        "file:Jazz - Oscar Peterson - Take The A Train (live).mid",
        "lib:jazz-berlin-blue-skies",
        "lib:ragtime-waller-smashing-thirds",
        "file:Jazz - Teddy Wilson - Tea for Two.mid",
        "lib:ragtime-confrey-dizzy-fingers",
        "lib:jazz-arlen-its-only-a-paper-moon",
        "lib:jazz-sampson-stompin-at-the-savoy",
        "lib:jazz-porter-youd-be-so-nice-to-come-home-to",
    ],
    "Late-night jazz piano": [
        "lib:jazz-garner-misty",
        "file:Jazz - Bill Evans - Here's That Rainy Day.mid",
        "lib:jazz-davis-blue-in-green",
        "lib:jazz-monk-ruby-my-dear",
        "file:Jazz - Keith Jarrett - Someone To Watch Over Me.mid",
        "lib:jazz-ellington-in-a-sentimental-mood",
        "lib:jazz-green-body-and-soul",
        "lib:jazz-heusen-darn-that-dream",
        "lib:jazz-monk-round-midnight",
        "lib:jazz-gershwin-embraceable-you",
        "lib:jazz-young-when-i-fall-in-love",
        "file:Jazz - McCoy Tyner - Naima (live).mid",
        "lib:jazz-strayhorn-lush-life",
        "file:Jazz - Thelonious Monk - Pannonica.mid",
        "lib:jazz-ellington-come-sunday",
        "lib:jazz-evans-peace-piece",
    ],
    "The Great American Songbook": [
        "lib:jazz-howard-fly-me-to-the-moon",
        "lib:jazz-porter-night-and-day",
        "lib:jazz-gershwin-summertime",
        "lib:jazz-ellington-satin-doll",
        "lib:standards-arlen-over-the-rainbow",
        "lib:jazz-kern-all-the-things-you-are",
        "lib:jazz-berlin-cheek-to-cheek",
        "lib:standards-rodgers-my-funny-valentine",
        "lib:jazz-gershwin-s-wonderful",
        "lib:jazz-porter-begin-the-beguine",
        "lib:jazz-ellington-sophisticated-lady",
        "lib:jazz-arlen-stormy-weather",
        "lib:jazz-kern-smoke-gets-in-your-eyes",
        "lib:jazz-rodgers-lover",
        "lib:jazz-gershwin-the-man-i-love",
        "lib:jazz-porter-what-is-this-thing-called-love",
        "lib:jazz-hupfeld-as-time-goes-by",
        "lib:jazz-carmichael-stardust",
    ],
    "Film & TV themes": [
        "lib:film-williams-star-wars-main-title",
        "lib:film-rota-love-theme-from-the-godfather-speak-softly-love",
        "lib:film-williams-hedwigs-theme-harry-potter",
        "lib:film-tiersen-comptine-dun-autre-ete-lapres-midi-amelie",
        "lib:film-badelt-hes-a-pirate-pirates-of-the-caribbean",
        "lib:film-horner-my-heart-will-go-on-titanic",
        "lib:film-mancini-the-pink-panther-theme",
        "lib:film-williams-theme-from-schindlers-list",
        "lib:film-djawadi-game-of-thrones-main-title",
        "lib:film-hisaishi-merry-go-round-of-life-howls-moving-castle",
        "lib:film-vangelis-chariots-of-fire",
        "lib:film-webber-memory-cats",
        "lib:film-shore-the-lord-of-the-rings-main-theme",
        "lib:film-menken-beauty-and-the-beast",
        "lib:film-zimmer-interstellar-main-theme-first-step",
        "lib:film-lai-love-story-where-do-i-begin",
        "lib:film-lopez-let-it-go-frozen",
        "lib:film-sakamoto-merry-christmas-mr-lawrence",
        "lib:film-williams-the-imperial-march-star-wars",
        "lib:film-williams-raiders-march-marions-theme-indiana-jones",
    ],
    "Pop on the piano": [
        "lib:pop-joel-piano-man",
        "lib:pop-lennon-imagine",
        "lib:pop-andersson-dancing-queen",
        "lib:pop-adele-someone-like-you",
        "lib:pop-mccartney-let-it-be",
        "lib:pop-coldplay-clocks",
        "lib:pop-mccartney-yesterday",
        "lib:pop-sting-every-breath-you-take",
        "lib:pop-cohen-hallelujah",
        "lib:pop-wonder-isnt-she-lovely",
        "lib:pop-john-your-song",
        "lib:pop-ha-take-on-me",
        "lib:pop-peretti-cant-help-falling-in-love",
        "lib:pop-mccartney-hey-jude",
        "lib:pop-simon-bridge-over-troubled-water",
        "lib:pop-weeknd-blinding-lights",
        "lib:pop-simon-the-sound-of-silence",
        "lib:pop-mercury-bohemian-rhapsody",
    ],
}


def _get(api: str, path: str) -> dict:
    with urllib.request.urlopen(api + path, timeout=15) as r:
        return json.load(r)


def _send(api: str, method: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(api + path, data=json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def piece_info(piece: str, catalog: dict, api: str) -> dict:
    """{title, composer, duration_s, notes, path} for a lib: or file: piece; raises when it can't be played."""
    path = L.resolve(piece)
    if path is None:
        raise SystemExit(f"{piece}: does not resolve to a file")
    kind, rest = L.split_id(piece)
    if kind == "lib":
        it = catalog[rest]
        return {"title": it["title"], "composer": it.get("composer") or "", "duration_s": float(it["duration_s"]),
                "notes": int(it["notes"]), "path": path}
    if kind != "file":
        raise SystemExit(f"{piece}: only lib: and file: pieces belong in these lists")
    item = _get(api, "/api/v1/library/item/" + urllib.parse.quote(piece, safe=""))
    _, rep = L.clean_midi(path.read_bytes(), item.get("title"))
    return {"title": item.get("title") or rest, "composer": item.get("composer") or "",
            "duration_s": float(rep["duration_s"]), "notes": int(rep["notes"]), "path": path}


def stress_list(infos: list[dict]) -> list[dict]:
    """The densest pieces by notes per second, spread across composers (STRESS_CAPS)."""
    picked: list[dict] = []
    used: dict[str, int] = {}
    for x in sorted(infos, key=lambda x: x["notes"] / x["duration_s"], reverse=True):
        key = next((k for k in STRESS_CAPS if k in x["composer"].lower()), None)
        if key is not None and used.get(key, 0) >= STRESS_CAPS[key]:
            continue
        if key is not None:
            used[key] = used.get(key, 0) + 1
        picked.append(x)
        if len(picked) == STRESS_N:
            break
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8700/show", help="show_service's API root (via room)")
    ap.add_argument("--dry-run", action="store_true", help="check and report; create nothing")
    ap.add_argument("--stress-out", type=pathlib.Path, help="write the Virtuoso stress list here")
    a = ap.parse_args()
    api = a.api.rstrip("/")

    catalog = json.loads((L.LIB_DIR / "catalog.json").read_text())["items"]
    infos: dict[str, dict] = {}
    seen: dict[str, list[str]] = {}
    for name, pieces in PLAYLISTS.items():
        if len(set(pieces)) != len(pieces):
            raise SystemExit(f"{name}: a piece is listed twice")
        for p in pieces:
            if p not in infos:
                infos[p] = piece_info(p, catalog, api)
            seen.setdefault(p, []).append(name)
    shared = {p: names for p, names in seen.items() if len(names) > 1}
    if any(len(n) > 2 for n in shared.values()):
        raise SystemExit("a piece is in more than two lists")

    existing = {} if a.dry_run else {p["name"]: p["id"] for p in _get(api, "/api/v1/playlists")["playlists"]}
    report = []
    for name, pieces in PLAYLISTS.items():
        minutes = sum(infos[p]["duration_s"] for p in pieces) / 60
        action = "checked"
        if not a.dry_run:
            if name in existing:
                got = _send(api, "PUT", f"/api/v1/playlists/{urllib.parse.quote(existing[name])}",
                            {"name": name, "items": pieces})
                action = "updated"
            else:
                got = _send(api, "POST", "/api/v1/playlists", {"name": name, "items": pieces})
                action = "created"
            missing = [x["piece"] for x in got.get("items", []) if x.get("missing")]
            if len(got.get("items", [])) != len(pieces) or missing:
                raise SystemExit(f"{name}: the service holds {len(got.get('items', []))} pieces, missing {missing}")
        report.append({"name": name, "action": action, "count": len(pieces), "minutes": round(minutes, 1),
                       "first": [infos[p]["title"] for p in pieces[:5]]})
    print(json.dumps({"playlists": report, "shared": shared}, ensure_ascii=False, indent=1))

    if a.stress_out:
        picks = stress_list([infos[p] for p in PLAYLISTS[STRESS_FROM]])
        lines = [f"# {STRESS_N} densest pieces from the '{STRESS_FROM}' playlist, by notes per second "
                 f"(caps: {', '.join(f'{k} {v}' for k, v in STRESS_CAPS.items())})"]
        for x in picks:
            nps = x["notes"] / x["duration_s"]
            m, s = divmod(round(x["duration_s"]), 60)
            lines.append(f"# {x['title']} | {x['composer']} | {nps:.1f} notes/s | {m}:{s:02d}")
            lines.append(str(x["path"]))
        a.stress_out.parent.mkdir(parents=True, exist_ok=True)
        a.stress_out.write_text("\n".join(lines) + "\n")
        print(f"stress list: {len(picks)} pieces -> {a.stress_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
