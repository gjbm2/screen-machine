"""gallery_match_eval: what the Ambient gallery would show for a kind of music, as contact sheets.

  ~/.venvs/sm-stream/bin/python -m stream_service.tools.gallery_match_eval [--screen north-screen] [--picks 16]
      [--scenarios jazz,pop] [--dir output/<screen>] [--seed 1] --out DIR

For each scenario (a realistic music direction, as show_service's ML / AI listener and the session's effective
direction produce it: genres map, moods, valence, organic, slow energy) it runs the gallery's OWN selection code
(plugins/sources/gallery.py: the decode worker's index with the art tags, _Deck.draw with match "music", the
library pool and its continuity) for N picks in a row, and writes:
  * <out>/<scenario>.png: the picks in order, each from output/<screen>/thumbnails/<file>.jpg, labelled with its
    subject, medium and mood tags and its weight for the music;
  * <out>/summary.txt (also printed): tag frequencies per scenario, and how many picks were untagged.
The renderer's decode-time filter (sidecar with a prompt and a workflow, at least 1920 wide, 16:9 within 10 %) is
applied the same way, from the image header.  No GPU; reads only; the prompt is never printed.  Until the library
is tagged every pick is "untagged" (neutral): the sheets only mean something once config/art_tags.yaml's tagger has run.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from stream_service.plugins.sources import gallery as g   # noqa: E402

SCENARIOS = {
    "jazz": {"genres": {"jazz": 0.85, "blues": 0.15}, "genre": "jazz",
             "moods": ["mellow", "elegant", "warm", "organic"], "music_moods": [], "valence": 0.45, "organic": 0.8,
             "density": 0.4, "energy": 0.4, "energy_slow": 0.38},
    "lyrical-classical": {"genres": {"classical": 0.75, "piano": 0.25}, "genre": "classical",
                          "moods": ["calm", "dreamy", "elegant"], "music_moods": [], "valence": 0.55, "organic": 0.9,
                          "density": 0.3, "energy": 0.3, "energy_slow": 0.25},
    "pop": {"genres": {"pop": 0.7, "indie": 0.2, "rnb": 0.1}, "genre": "pop", "moods": ["bright", "playful", "warm"],
            "music_moods": [], "valence": 0.8, "organic": 0.45, "density": 0.6, "energy": 0.65, "energy_slow": 0.65},
    "ambient-calm": {"genres": {"ambient": 0.8, "downtempo": 0.2}, "genre": "ambient",
                     "moods": ["calm", "mellow", "dreamy"], "music_moods": [], "valence": 0.5, "organic": 0.6,
                     "density": 0.2, "energy": 0.2, "energy_slow": 0.15},
    "techno": {"genres": {"techno": 0.6, "electronic": 0.3, "house": 0.1}, "genre": "techno",
               "moods": ["hypnotic", "geometric", "cool"], "music_moods": [], "valence": 0.5, "organic": 0.1,
               "density": 0.8, "energy": 0.8, "energy_slow": 0.8},
}
TW, TH, LABEL_H, COLS = 320, 180, 54, 4


def eligible(path: pathlib.Path) -> bool:
    """The decode worker's filter (gallery._WORKER decode with filter), from the sidecar and the image header."""
    from PIL import Image
    try:
        side = json.loads(path.with_name(path.name + ".json").read_text())
        if not (isinstance(side, dict) and side.get("prompt") and side.get("workflow")):
            return False
        with Image.open(path) as im:
            w, h = im.size
            return im.format in ("JPEG", "PNG") and w >= 1920 and h > 0 and abs((w / h) / (16 / 9) - 1) <= 0.10
    except Exception:
        return False


def picks(deck: "g._Deck", music: dict, n: int) -> list[str]:
    out = []
    for _ in range(n):
        for _ in range(8):                         # as the decoder: a filtered-out pick is marked bad, draw again
            name = deck.draw("library", "music", music)
            if name is None:
                break
            if eligible(deck.dir / name):
                out.append(name)
                break
            deck.mark_bad(name)
    return out


def label(f: dict | None, w: float) -> list[str]:
    if f is None:
        return ["untagged", f"w {w:.2f}"]
    return [" ".join(sorted(f["subject"])) or "-", " ".join(sorted(f["moods"])) or "-",
            f"q {f['quality'] if f['quality'] is not None else '?'}  w {w:.2f}"]


def sheet(deck: "g._Deck", names: list[str], music: dict, title: str, path: pathlib.Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
        big = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        font = big = ImageFont.load_default()
    rows = max(1, (len(names) + COLS - 1) // COLS)
    im = Image.new("RGB", (COLS * TW, 34 + rows * (TH + LABEL_H)), (24, 22, 20))
    dr = ImageDraw.Draw(im)
    dr.text((8, 8), title, fill=(240, 230, 210), font=big)
    favs = set(deck.fav)
    for i, n in enumerate(names):
        x, y = (i % COLS) * TW, 34 + (i // COLS) * (TH + LABEL_H)
        th = deck.dir / "thumbnails" / f"{n}.jpg"
        try:
            with Image.open(th if th.exists() else deck.dir / n) as t:
                t = t.convert("RGB")
                t.thumbnail((TW - 6, TH - 6))
                im.paste(t, (x + 3 + (TW - 6 - t.width) // 2, y + 3 + (TH - 6 - t.height) // 2))
        except Exception:
            dr.rectangle((x + 3, y + 3, x + TW - 3, y + TH - 3), outline=(90, 80, 70))
        f = deck.facets.get(n)
        lines = label(f, g.music_weight(f, music, n in favs))
        lines[0] = f"{i + 1}. {'* ' if n in favs else ''}{lines[0]}"
        for j, s in enumerate(lines):
            dr.text((x + 6, y + TH + 2 + 16 * j), s[:44], fill=(220, 210, 190), font=font)
    im.save(path)


def summary(deck: "g._Deck", scen: str, names: list[str]) -> str:
    subj, moods, med = collections.Counter(), collections.Counter(), collections.Counter()
    untagged = 0
    for n in names:
        f = deck.facets.get(n)
        if f is None or not f["ai"]:
            untagged += 1
        if f is not None:
            subj.update(f["subject"])
            moods.update(f["moods"])
        r = deck.raw.get(n) or {}
        m = (r.get("ai") or {}).get("medium")
        if isinstance(m, str):
            med[m] += 1
    fmt = lambda c: ", ".join(f"{w} {k}" for w, k in c.most_common(8)) or "-"      # noqa: E731
    return (f"{scen}: {len(names)} picks, {untagged} untagged\n  subject: {fmt(subj)}\n  medium:  {fmt(med)}\n"
            f"  moods:   {fmt(moods)}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--screen", default="north-screen")
    ap.add_argument("--dir", help="the bucket directory (default: the renderer's output/<screen>)")
    ap.add_argument("--scenarios", default=",".join(SCENARIOS))
    ap.add_argument("--picks", type=int, default=16)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    bucket = pathlib.Path(a.dir) if a.dir else g.output_dir() / a.screen
    meta = g.index_once(bucket)
    if not meta.get("ok"):
        print(f"index of {bucket} failed: {meta.get('error')}", file=sys.stderr)
        return 1
    tagged = len(meta.get("tags") or {})
    print(f"{bucket}: {len(meta['fav'])} favourites, {len(meta['seq'])} in sequence, {tagged} with art tags")
    text = []
    for scen in a.scenarios.split(","):
        music = SCENARIOS[scen]
        deck = g._Deck(a.screen)                  # a fresh deck per scenario: the same index, its own memory
        deck.dir, deck.rng = bucket, random.Random(a.seed)
        deck.apply_index(meta)
        names = picks(deck, music, a.picks)
        sheet(deck, names, music, f"{a.screen} - {scen}: {', '.join(f'{w} {p}' for w, p in music['genres'].items())}",
              out / f"{scen}.png")
        text.append(summary(deck, scen, names))
    (out / "summary.txt").write_text("".join(text))
    print("".join(text), end="")
    print(f"sheets in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
