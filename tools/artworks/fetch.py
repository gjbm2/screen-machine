"""Download the famous-artworks library (config/artworks/catalogue.yaml) for the generator's Upload menu.

Each work's picture is the lead image of its English Wikipedia article (the `wiki` title, else the best search hit for
`search` or "<title> <artist>"), a Wikimedia Commons file; failing that the article's Wikidata image, and for a work
without one (or whose article names none), the first Commons file found by its `commons` words or title and artist. It is fetched at most 3840 px on its long edge (no
upscaling; a 4K frame's width), saved as JPEG under 4.5 MB, with a 512 px thumbnail for the picker, into
output/_artworks/ (served by Flask as /output/_artworks/, not in git). library.json there is what the page reads.

    ~/.venvs/sm-room/bin/python tools/artworks/fetch.py              # fetch what's missing, rewrite library.json
    ~/.venvs/sm-room/bin/python tools/artworks/fetch.py --only vermeer-pearl --refresh
    ~/.venvs/sm-room/bin/python tools/artworks/fetch.py --sheet      # also a contact sheet to check them by eye

A work that can't be resolved is reported and left out of library.json; fix its `wiki`/`search` and run again.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import requests
import yaml
from PIL import Image, ImageDraw, ImageOps

REPO = Path(__file__).resolve().parents[2]
CATALOGUE = REPO / "config" / "artworks" / "catalogue.yaml"
OUT = REPO / "output" / "_artworks"
API = "https://en.wikipedia.org/w/api.php"
# Wikimedia's robot policy: a named client with a way to reach its owner, else it throttles hard (429)
UA = f"screen-machine-artworks/1.0 (https://screen.finsburybankforsavings.com) python-requests/{requests.__version__}"
DOWNLOAD_GAP_S = 3          # between pictures: upload.wikimedia.org answers bursts with 429s and a 2-minute wait
MAX_EDGE = 3840
MAX_BYTES = 4_500_000      # under the upload form's 5 MB
THUMB_EDGE = 512

# The picker's period chips: each movement in the catalogue under one broad period
PERIODS = {
    "Ancient": ["Ancient Roman"],
    "Medieval": ["Gothic", "Byzantine"],
    "Renaissance": ["Early Renaissance", "High Renaissance", "Northern Renaissance", "Mannerism"],
    "Baroque": ["Baroque", "Dutch Golden Age"],
    "18th century": ["Rococo", "Neoclassicism"],
    "Romantic": ["Romanticism", "Hudson River School"],
    "Victorian & Realist": ["Realism", "Pre-Raphaelite", "Academic", "Victorian", "Tonalism"],
    "Impressionist": ["Impressionism"],
    "Post-Impressionist": ["Post-Impressionism", "Naïve art"],
    "Symbolist & Nouveau": ["Symbolism", "Art Nouveau"],
    "Modern": ["Expressionism", "Fauvism", "Cubism", "Futurism", "Orphism", "Suprematism", "De Stijl", "Bauhaus",
               "Abstract art", "Modernism", "Regionalism"],
    "Asian": ["Song dynasty", "Ukiyo-e", "Rinpa", "Momoyama", "Persian miniature"],
}
PERIOD_OF = {m: p for p, ms in PERIODS.items() for m in ms}

session = requests.Session()
session.headers["User-Agent"] = UA


def api(**params) -> dict:
    params.update(format="json", formatversion=2)
    for attempt in range(5):
        r = session.get(API, params=params, timeout=30)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.3)
        return r.json()
    raise RuntimeError(f"Wikipedia API kept refusing: {params}")


STOP = {"with", "from", "over", "under", "after", "portrait", "painting", "still", "life", "self"}


def fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", text)
    return "".join(c if c.isalnum() else " " for c in text if not unicodedata.combining(c)).lower()


def article(work: dict) -> str | None:
    """The English Wikipedia article for a work."""
    if work.get("wiki"):
        return work["wiki"]
    q = work.get("search") or f"{work['title']} {work['artist']} painting"
    hits = api(action="query", list="search", srsearch=q, srlimit=1).get("query", {}).get("search", [])
    if not hits:
        return None
    # only an article about the work itself (it shares a word of the title), not the artist's or a list's
    words = {w for w in fold(work["title"]).split() if len(w) >= 4 and w not in STOP}
    return hits[0]["title"] if words & set(fold(hits[0]["title"]).split()) else None


def lead_image(page: str) -> tuple[str, str] | None:
    """(resolved article title, Commons file name) of an article's lead image."""
    data = api(action="query", titles=page, prop="pageimages", piprop="name", redirects=1)
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("pageimage"):
        return None
    return pages[0]["title"], pages[0]["pageimage"]


def wikidata_image(page: str) -> tuple[str, str] | None:
    """The article's Wikidata item's image (P18), for articles whose lead image Wikipedia doesn't name."""
    data = api(action="query", titles=page, prop="pageprops", ppprop="wikibase_item", redirects=1)
    pages = data.get("query", {}).get("pages", [])
    qid = pages[0].get("pageprops", {}).get("wikibase_item") if pages else None
    if not qid:
        return None
    r = session.get("https://www.wikidata.org/w/api.php", timeout=30, params={
        "action": "wbgetclaims", "entity": qid, "property": "P18", "format": "json"})
    time.sleep(0.3)
    claims = r.json().get("claims", {}).get("P18", [])
    if not claims:
        return None
    return pages[0]["title"], claims[0]["mainsnak"]["datavalue"]["value"].replace(" ", "_")


def commons_search(work: dict) -> tuple[str, str] | None:
    """A Commons file found by its `commons` words, else title and artist, for works without an article of their own
    or whose article leads with something else (a study, the artist, a gallery photo)."""
    q = work.get("commons") or f"{work['title']} {work['artist']}"
    r = session.get("https://commons.wikimedia.org/w/api.php", timeout=30, params={
        "action": "query", "list": "search", "srnamespace": 6, "srsearch": q, "srlimit": 5, "format": "json"})
    time.sleep(0.3)
    for hit in r.json().get("query", {}).get("search", []):
        name = hit["title"].removeprefix("File:")
        if name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "tif", "tiff"):
            return f"Commons: {hit['title']}", name.replace(" ", "_")
    return None


def image_info(file: str) -> dict:
    data = api(action="query", titles=f"File:{file}", prop="imageinfo",
               iiprop="url|size|mime|extmetadata", iiurlwidth=MAX_EDGE, iiurlheight=MAX_EDGE)
    info = data["query"]["pages"][0]["imageinfo"][0]
    meta = info.get("extmetadata") or {}
    info["license"] = (meta.get("LicenseShortName") or {}).get("value")
    return info


def download(url: str) -> bytes:
    for attempt in range(6):
        time.sleep(DOWNLOAD_GAP_S)
        r = session.get(url, timeout=120)
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("retry-after") or 0) or 30 * (attempt + 1)
            print(f"     {r.status_code}: waiting {wait} s", flush=True)
            time.sleep(wait + 5)
            continue
        r.raise_for_status()
        return r.content
    raise RuntimeError(f"download kept failing: {url}")


def save(raw: bytes, dest: Path, thumb: Path) -> tuple[int, int]:
    """Write the picture (long edge <= MAX_EDGE, under MAX_BYTES) and its thumbnail; returns the saved size."""
    Image.MAX_IMAGE_PIXELS = None   # some Commons scans are huge; they're trusted and scaled straight down
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        img = background
    img = img.convert("RGB")
    img.thumbnail((MAX_EDGE, MAX_EDGE), Image.LANCZOS)
    for quality in (90, 86, 82, 78, 72):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
        if buf.tell() <= MAX_BYTES:
            break
    dest.write_bytes(buf.getvalue())
    small = img.copy()
    small.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
    small.save(thumb, "JPEG", quality=82, optimize=True)
    return img.size


def contact_sheet(entries: list[dict], path: Path) -> None:
    cell, cols = 220, 10
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 28)), "white")
    draw = ImageDraw.Draw(sheet)
    for i, e in enumerate(entries):
        im = Image.open(OUT / "thumbs" / f"{e['id']}.jpg")
        im.thumbnail((cell - 8, cell - 8))
        x, y = (i % cols) * cell, (i // cols) * (cell + 28)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        draw.text((x + 4, y + cell + 2), e["id"][:30], fill="black")
    sheet.save(path, quality=80)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", help="just these ids")
    ap.add_argument("--refresh", action="store_true", help="fetch again even if already here")
    ap.add_argument("--sheet", action="store_true", help="write output/_artworks/contact-sheet.jpg")
    a = ap.parse_args()

    works = yaml.safe_load(CATALOGUE.read_text())["works"]
    (OUT / "thumbs").mkdir(parents=True, exist_ok=True)
    sources_path = OUT / "sources.json"
    sources = json.loads(sources_path.read_text()) if sources_path.exists() else {}

    failed = []
    for work in works:
        wid = work["id"]
        dest, thumb = OUT / f"{wid}.jpg", OUT / "thumbs" / f"{wid}.jpg"
        if a.only and wid not in a.only:
            continue
        if dest.exists() and thumb.exists() and wid in sources and not a.refresh:
            continue
        try:
            page = None if work.get("commons") else article(work)
            # the article's lead image, else its Wikidata image, else (no article of its own) a Commons search
            lead = lead_image(page) if page else None
            if not lead and work.get("wiki"):
                lead = wikidata_image(page)
            if not lead:
                lead = commons_search(work)
            if not lead:
                failed.append((wid, f"no lead image (article: {page})"))
                continue
            title, file = lead
            info = image_info(file)
            url = info.get("thumburl") if info.get("width", 0) > MAX_EDGE or info.get("height", 0) > MAX_EDGE else info["url"]
            if not str(info.get("mime", "")).startswith("image/"):
                failed.append((wid, f"lead file is {info.get('mime')}: {file}"))
                continue
            size = save(download(url or info["url"]), dest, thumb)
            sources[wid] = {"article": title, "file": file, "license": info.get("license"),
                            "original": [info.get("width"), info.get("height")], "saved": list(size)}
            print(f"ok   {wid:34} {size[0]}x{size[1]}  {title} <- {file}", flush=True)
            sources_path.write_text(json.dumps(sources, indent=1, ensure_ascii=False))
        except Exception as e:   # one bad work doesn't stop the rest
            failed.append((wid, repr(e)[:200]))

    entries = []
    for work in works:
        wid = work["id"]
        if wid not in sources or not (OUT / f"{wid}.jpg").exists():
            continue
        src = sources[wid]
        entries.append({
            **{k: work.get(k) for k in ("id", "title", "artist", "year", "movement", "genre", "medium", "people")},
            "period": PERIOD_OF.get(work["movement"], "Other"),
            "tags": work.get("tags", []), "aka": work.get("aka", []),
            "width": src["saved"][0], "height": src["saved"][1],
            "image": f"/output/_artworks/{wid}.jpg", "thumb": f"/output/_artworks/thumbs/{wid}.jpg",
            "source": {"article": src["article"], "file": src["file"], "license": src["license"]},
        })
    (OUT / "library.json").write_text(json.dumps(
        {"periods": list(PERIODS), "works": entries}, indent=1, ensure_ascii=False))
    print(f"library.json: {len(entries)} of {len(works)} works")
    for wid, why in failed:
        print(f"FAIL {wid}: {why}")
    if a.sheet:
        contact_sheet(entries, OUT / "contact-sheet.jpg")
        print(f"contact sheet: {OUT / 'contact-sheet.jpg'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
