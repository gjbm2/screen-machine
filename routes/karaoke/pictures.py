"""karaoke.pictures: pictures of each song's artist, for the renderer's photo scenes (owner, 26 Nov: "photo processing
scenes ... which use publicly-available-from-the-web images of the singer and/or band ... save them and tag them
locally in a 'karaoke' bucket so that it effectively gets cached for any taylor swift track").

Per ARTIST, once, kept: ``~/.local/state/screen-machine/karaoke/pictures/<artist-slug>/`` (smconfig.state) holds the
pictures (JPEG, longest side SAVE_PX) and ``index.json`` (where each came from, its credit and licence, and what the
vetting said).  ``songs.json`` maps each song to its artists.  Never in git, never a screen's bucket: this house's
karaoke screen only.

Where they come from (looked up by catalogue identity, never a loose web image search: wrong people, rights, and
this is a children's party):
  * Deezer (no key): the artist's own picture, the song's album cover and the artist's best-known album covers;
  * Wikimedia Commons (no key, freely licensed): a file search for the artist's name, and one for the song's year
    (so a 1985 song can show 1985 pictures); MusicBrainz -> Wikidata's own picture of the artist first;
  * fanart.tv (FANARTTV_API_KEY in .env, optional): screen-shaped 1920x1080 artist backgrounds, by MusicBrainz id.

The vetting (task ``song_picture`` in config/llm.yaml: Gemini, one 768 px look per picture): does it show this artist
or their album's cover, is it sharp, is it suitable for a children's party (strict), where are the faces, what era,
which moods.  A picture that fails is deleted and remembered (never fetched again).  Without the AI (no key, over the
day's budget), only Deezer's own pictures of the artist and covers Deezer marks clean are used until it can look.

One background thread (``run``): the playing song's and the queue's artists first, then the library's; one artist
at a time; an artist that failed is tried again after RETRY_S.
"""
from __future__ import annotations

import hashlib
import html
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

import smconfig
from flask import abort, jsonify, request, send_file
from utils.logger import error, info

from .api import karaoke_api, store
from .store import norm, root

UA = "screen-machine-karaoke/1.0 (a private home karaoke screen)"
TIMEOUT_S = 20
DEEZER = "https://api.deezer.com"
COMMONS = "https://commons.wikimedia.org/w/api.php"
FANART = "https://webservice.fanart.tv/v3/music"
TASK = "song_picture"
VERSION = 1

TARGET = 14              # kept pictures an artist's deck aims for
COVERS_MAX = 4           # of which album covers at most
CANDIDATES_MAX = 40      # pictures looked at per artist per fetch
MIN_SHORT_SIDE = 560     # smaller is too soft even under the effects
MAX_BYTES = 15 << 20
SAVE_PX = 2048
COMMONS_STEPS = (1920, 1280, 960)   # Wikimedia's standard thumbnail widths: only these are fetched (an original
                                    # file, or another width, is answered with 429 and a 10 minute wait)
COMMONS_GAP_S = 12.0   # between two Commons downloads: an unnamed client gets a slow trickle, a burst a 429 and 10 min
VET_PX = 768
MIN_QUALITY = 0.45
RETRY_S = 1800.0
LOOP_S = 10.0
ERA_FETCH = True         # a Commons search for the song's year as well (pictures of that era)
SPLIT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with|vs\.?|x)\s+|\s*[,;/]\s*|\s+&\s+", re.I)
# Deezer's explicit_content_cover: 0 not explicit, 3 edited ("clean"). Anything else needs the AI's look.
CLEAN_COVER = (0, 3)

_lock = threading.RLock()
_thread: Optional[threading.Thread] = None
_failed: dict[str, float] = {}
_busy: dict[str, str] = {}            # slug -> what it is doing now ("fetching", "vetting")
_commons = {"pause_until": 0.0, "last": 0.0}   # Wikimedia's rate limit: no Commons download until pause_until


class Later(Exception):
    """A download to try again later (rate-limited, the server busy, a timeout), not a bad picture."""


# ---------------------------------------------------------------- where things live
def pictures_root() -> Path:
    return root() / "pictures"


def slug(name: str) -> str:
    return norm(name).replace(" ", "-")[:80] or "unknown"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def artist_index(s: str) -> dict:
    return _read(pictures_root() / s / "index.json", {})


def _songs_map() -> dict:
    return _read(pictures_root() / "songs.json", {})


# ---------------------------------------------------------------- HTTP
def _get(url: str, params: Optional[dict] = None, *, raw: bool = False, limit: int = MAX_BYTES) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:   # noqa: S310 - fixed public APIs
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("too big")
    return data if raw else json.loads(data.decode("utf-8"))


# ---------------------------------------------------------------- who the artists are
def deezer_artist(name: str) -> Optional[dict]:
    """Deezer's artist whose name is this name (the best known of them), else None. Strict: a near name is no one."""
    o = _get(f"{DEEZER}/search/artist", {"q": name, "limit": 10}) or {}
    same = [a for a in o.get("data") or [] if a.get("id") and norm(a.get("name")) == norm(name)]
    return max(same, key=lambda a: a.get("nb_fan") or 0) if same else None


def resolve_artists(artist: str) -> list[dict]:
    """A song's artist line -> its artists [{name, deezer}]: the whole line when Deezer knows it as one artist
    ("Simon & Garfunkel", "Crosby, Stills, Nash & Young"), else each name split at feat. / & / , ("Calvin Harris
    feat. Rihanna")."""
    artist = (artist or "").strip()
    if not artist:
        return []
    whole = deezer_artist(artist)
    if whole is not None:
        return [{"name": whole["name"], "deezer": whole}]
    out = []
    for part in [p.strip() for p in SPLIT.split(artist) if p and p.strip()][:3]:
        a = deezer_artist(part)
        out.append({"name": a["name"] if a else part, "deezer": a})
    return out


def _mbid(name: str) -> Optional[str]:
    from .sources import _mb
    d = _mb("artist", query=f'artist:"{name}"', limit=5) or {}
    same = [a for a in d.get("artists") or [] if norm(a.get("name")) == norm(name) and (a.get("score") or 0) >= 90]
    return max(same, key=lambda a: a.get("score") or 0)["id"] if same else None


def _wikidata_image(mbid: str) -> Optional[str]:
    """Wikidata's own picture of the artist (P18), as a Commons file title, via MusicBrainz's link."""
    from .sources import _mb
    d = _mb(f"artist/{mbid}", inc="url-rels") or {}
    q = next((r["url"]["resource"].rsplit("/", 1)[-1] for r in d.get("relations") or []
              if r.get("type") == "wikidata" and (r.get("url") or {}).get("resource")), None)
    if not q:
        return None
    e = _get(f"https://www.wikidata.org/wiki/Special:EntityData/{q}.json")
    claims = ((e.get("entities") or {}).get(q) or {}).get("claims") or {}
    for c in claims.get("P18") or []:
        v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(v, str):
            return "File:" + v
    return None


# ---------------------------------------------------------------- candidates
def _big(url: Optional[str]) -> Optional[str]:
    """A catalogue thumbnail's URL at its largest (Deezer 1000 px, iTunes 1200 px)."""
    if not url:
        return None
    url = re.sub(r"/\d+x\d+-000000-80-0-0\.jpg$", "/1000x1000-000000-80-0-0.jpg", url)
    return re.sub(r"/\d+x\d+bb\.(jpg|png)$", r"/1200x1200bb.\1", url)


def deezer_candidates(a: dict, album: Optional[str]) -> list[dict]:
    out = []
    pic = a.get("picture_xl") or ""
    if pic and "/artist//" not in pic:                      # Deezer's blank silhouette has no image hash
        out.append({"url": pic, "source": "deezer", "kind_hint": "portrait", "credit": "Deezer",
                    "license": "catalogue", "title": f"{a.get('name')} (Deezer)", "trusted": True})
    try:
        albums = (_get(f"{DEEZER}/artist/{a['id']}/albums", {"limit": 100}) or {}).get("data") or []
    except Exception:                                   # noqa: BLE001
        albums = []
    seen: set[str] = set()
    ranked = sorted([x for x in albums if x.get("cover_xl")],
                    key=lambda x: (not album or norm(album) not in norm(x.get("title")), x.get("record_type") != "album",
                                   -(x.get("fans") or 0)))
    for x in ranked:
        k = re.sub(r"\b(deluxe|edition|remaster(ed)?|version|expanded|anniversary|platinum|international|the more|"
                   r"chapter|taylor s|live|acoustic|collection|\d+)\b", "",
                   norm(re.sub(r"[\(\[].*?[\)\]]", "", x["title"]))).strip()
        if not k or any(k in o or o in k for o in seen):     # one cover per album (its editions are the same)
            continue
        seen.add(k)
        out.append({"url": x["cover_xl"], "source": "deezer", "kind_hint": "cover", "credit": "Deezer",
                    "license": "catalogue", "title": f"{x.get('title')} (album cover)",
                    "trusted": x.get("explicit_content_cover") in CLEAN_COVER, "year": (x.get("release_date") or "")[:4]})
        if len(seen) >= COVERS_MAX + 2:
            break
    return out


def _strip_html(s: Any) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", str(s or ""))).strip()[:120]


def _commons_rows(params: dict) -> list[dict]:
    d = _get(COMMONS, {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
                       "iiextmetadatafilter": "LicenseShortName|Artist", **params}) or {}
    pages = sorted(((d.get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = _strip_html((meta.get("LicenseShortName") or {}).get("value"))
        w, h = int(ii.get("width") or 0), int(ii.get("height") or 0)
        steps = [x for x in COMMONS_STEPS if x < w]
        orig = str(ii.get("url") or "")
        m = re.match(r"(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?]+)", orig)
        if ii.get("mime") not in ("image/jpeg", "image/png") or not steps or not m:
            continue
        tw = steps[0]
        if min(tw, round(h * tw / w)) < MIN_SHORT_SIDE:
            continue
        thumb = f"{m.group(1)}thumb/{m.group(2)}{m.group(3)}/{tw}px-{m.group(3)}"
        if not re.search(r"\b(cc|public domain|pd)\b|cc0", lic, re.I):
            continue
        out.append({"url": thumb, "source": "commons", "kind_hint": None,
                    "credit": _strip_html((meta.get("Artist") or {}).get("value")) or "Wikimedia Commons",
                    "license": lic, "title": str(p.get("title") or "")[5:], "page": ii.get("descriptionurl"),
                    "trusted": False})
    return out


def commons_candidates(name: str, year: Optional[int], wikidata_file: Optional[str]) -> list[dict]:
    out = []
    if wikidata_file:
        try:
            out += _commons_rows({"titles": wikidata_file})
        except Exception:                               # noqa: BLE001
            pass
    queries = [f'"{name}" filetype:bitmap']
    if ERA_FETCH and isinstance(year, int) and year > 1900:
        queries.insert(0, f'"{name}" {year} filetype:bitmap')     # the song's year first: pictures of its era
    for q in queries:
        try:
            out += _commons_rows({"generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30})
        except Exception as e:                          # noqa: BLE001
            error(f"karaoke pictures: Commons search for {name}: {e}")
    nk = set(norm(name).split())
    keep, seen = [], set()
    for c in out:
        t = norm(re.sub(r"\((?:[^)]*crop[^)]*|enhanced[^)]*|\d+)\)", "", c["title"]))
        base = re.sub(r"\b(cropped|jpg|png|\d+)\b", "", t).strip()
        if base in seen or not nk & set(t.split()) or re.search(r"\b(logo|signature|autograph|wordmark)\b", t):
            continue
        seen.add(base)
        keep.append(c)
    return keep


def fanart_candidates(mbid: Optional[str]) -> list[dict]:
    key = smconfig.env("FANARTTV_API_KEY")
    if not key or not mbid:
        return []
    try:
        d = _get(f"{FANART}/{mbid}", {"api_key": key}) or {}
    except Exception:                                   # noqa: BLE001 - not known there, or the key is wrong
        return []
    out = []
    for kind, hint in (("artistbackground", "live"), ("artistthumb", "portrait")):
        for x in sorted(d.get(kind) or [], key=lambda x: -int(x.get("likes") or 0))[:6]:
            if x.get("url"):
                out.append({"url": x["url"], "source": "fanart.tv", "kind_hint": hint, "credit": "fanart.tv",
                            "license": "fanart.tv", "title": f"{d.get('name') or ''} ({kind})", "trusted": False})
    return out


# ---------------------------------------------------------------- the AI's look
def _moods() -> list[str]:
    try:
        import yaml
        return [m for m in (yaml.safe_load((smconfig.REPO_ROOT / "config/live/vocab.yaml").read_text()) or {})
                .get("moods") or [] if isinstance(m, str)]
    except Exception:                                   # noqa: BLE001
        return []


def tool() -> dict:
    box = {"type": "array", "items": {"type": "integer", "minimum": 0, "maximum": 1000}, "minItems": 4, "maxItems": 4}
    return {"name": "vet_picture",
            "description": "Judge one picture found for a music artist, for a family karaoke screen.",
            "parameters": {"type": "object", "properties": {
                "shows": {"type": "string", "enum": ["the_artist", "album_cover", "other_people", "no_people",
                                                     "logo_or_text", "unsure"]},
                "kind": {"type": "string", "enum": ["portrait", "live", "group", "cover", "other"]},
                "quality": {"type": "number", "minimum": 0, "maximum": 1,
                            "description": "Sharp, well lit, well framed, no watermark: 1. Blurry, tiny, cluttered: 0."},
                "child_safe": {"type": "boolean"},
                "faces": {"type": "array", "items": box, "maxItems": 4,
                          "description": "Each main person's head (hair included) as [ymin, xmin, ymax, xmax] on a "
                                         "0-1000 scale."},
                "era": {"type": "string", "description": "The decade it was taken, like 1980s; empty if unknown."},
                "moods": {"type": "array", "items": {"type": "string", "enum": _moods() or ["warm"]}, "maxItems": 3},
                "caption": {"type": "string", "description": "At most 12 words."}},
                "required": ["shows", "kind", "quality", "child_safe", "faces", "moods"]}}


SYSTEM = ("You check pictures for a family karaoke screen at a child's birthday party. Each picture was found for "
          "one music artist (a singer or a band). Say whether it really shows that artist (or its members), or is "
          "one of their album covers; how good it is; and whether it is suitable for young children. child_safe "
          "is false for any nudity or near-nudity, sexual poses, violence, weapons, drugs, alcohol as the subject, "
          "rude gestures, or offensive words. When unsure about safety, say false. Find the main faces.")


def _data_url(path: Path) -> str:
    import base64
    from PIL import Image
    im = Image.open(path)
    im.draft("RGB", (VET_PX, VET_PX))
    im = im.convert("RGB")
    im.thumbnail((VET_PX, VET_PX))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def ai_ready() -> bool:
    """The vetting AI can be asked now (a model with its key, and the day's budget not reached)."""
    try:
        import utils.llm as llm
        usable = ((llm.status().get("tasks") or {}).get(TASK) or {}).get("usable")
        return bool(usable) and not llm.over_budget(TASK)
    except Exception:                                   # noqa: BLE001
        return False


def vet(path: Path, artist: str, c: dict) -> Optional[dict]:
    """The AI's look at one saved picture -> its answer (cleaned), or None when the AI couldn't answer."""
    import utils.llm as llm
    text = (f"Artist: {artist}\nFound on: {c.get('source')}\nIts title there: {c.get('title') or '-'}\n"
            f"{'Deezer gave it as the artist picture.' if c.get('kind_hint') == 'portrait' and c.get('source') == 'deezer' else ''}"
            f"{'Deezer gave it as an album cover.' if c.get('kind_hint') == 'cover' else ''}")
    res = llm.call_json(TASK, system=SYSTEM, tool=tool(), lenient=True,
                        user=[text, {"type": "image_url", "image_url": {"url": _data_url(path), "detail": "low"}}])
    if not res.ok or not isinstance(res.data, dict):
        return None
    d = res.data
    faces = []
    for f in d.get("faces") or []:
        try:                                            # Gemini's own box: [ymin, xmin, ymax, xmax] in 0..1000
            y0, x0, y1, x1 = (min(1.0, max(0.0, float(v) / 1000.0)) for v in f)
        except (TypeError, ValueError):
            continue
        x, y, w, h = x0, y0, x1 - x0, y1 - y0
        if w > 0.01 and h > 0.01:
            faces.append({"x": round(x, 3), "y": round(y, 3), "w": round(w, 3), "h": round(h, 3)})
    moods = set(_moods())
    era = str(d.get("era") or "").strip()
    return {"shows": d.get("shows"), "kind": d.get("kind") if d.get("kind") in ("portrait", "live", "group", "cover",
                                                                                 "other") else "other",
            "quality": round(min(1.0, max(0.0, float(d.get("quality") or 0.0))), 2),
            "child_safe": d.get("child_safe") is True, "faces": faces[:4],
            "era": era if re.fullmatch(r"(19|20)\d0s", era) else None,
            "moods": [m for m in d.get("moods") or [] if m in moods][:3],
            "caption": str(d.get("caption") or "")[:100], "model": res.model, "cost_usd": float(res.cost_usd or 0.0)}


def keep(v: dict) -> tuple[bool, str]:
    if not v["child_safe"]:
        return False, "not for children"
    if v["shows"] not in ("the_artist", "album_cover"):
        return False, f"shows {v['shows']}"
    if v["quality"] < MIN_QUALITY:
        return False, f"quality {v['quality']}"
    return True, "ok"


# ---------------------------------------------------------------- one artist
def _save(url: str, folder: Path) -> tuple[str, int, int]:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 80_000_000
    commons = "wikimedia.org" in url
    if commons:
        now = time.time()
        if now < _commons["pause_until"]:
            raise Later("Commons is pausing us")
        time.sleep(max(0.0, _commons["last"] + COMMONS_GAP_S - now))
        _commons["last"] = time.time()
    try:
        data = _get(url, raw=True)
    except urllib.error.HTTPError as e:
        if e.code == 429 or e.code >= 500:
            if commons:
                try:
                    wait = float(e.headers.get("Retry-After") or 600)
                except ValueError:
                    wait = 600.0
                _commons["pause_until"] = time.time() + min(max(wait, 60.0), 3600.0)
            raise Later(f"HTTP {e.code}") from None
        raise
    except (TimeoutError, urllib.error.URLError) as e:
        raise Later(type(e).__name__) from None
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (0, 0, 0, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert("RGB")
    if min(im.size) < MIN_SHORT_SIDE:
        raise ValueError(f"small ({im.size[0]}x{im.size[1]})")
    im.thumbnail((SAVE_PX, SAVE_PX), Image.LANCZOS)
    name = hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg"
    folder.mkdir(parents=True, exist_ok=True)
    im.save(folder / name, "JPEG", quality=90)
    return name, im.size[0], im.size[1]


def _decade(v: Any) -> Optional[str]:
    try:
        return f"{int(str(v)[:4]) // 10 * 10}s"
    except (TypeError, ValueError):
        return None


def fetch_artist(name: str, dz: Optional[dict], *, album: Optional[str] = None, year: Optional[int] = None) -> dict:
    """Fetch, vet and keep one artist's pictures (more of them if the index already has some). Returns the index."""
    s = slug(name)
    folder = pictures_root() / s
    idx = artist_index(s) or {"artist": name, "slug": s, "version": VERSION, "pictures": [], "rejected": {}}
    idx.setdefault("decades", [])
    known = {p["url"] for p in idx["pictures"]} | set(idx.get("rejected") or {})
    with _lock:
        _busy[s] = "fetching"
    try:
        cands: list[dict] = []
        if dz:
            idx["deezer_id"] = dz.get("id")
            cands += deezer_candidates(dz, album)
        mbid = idx.get("mbid")
        if mbid is None:
            try:
                mbid = _mbid(name)
            except Exception as e:                      # noqa: BLE001 - MusicBrainz busy: Commons still works
                error(f"karaoke pictures: MusicBrainz for {name}: {e}")
        idx["mbid"] = mbid
        wd = None
        if mbid and not idx["pictures"]:
            try:
                wd = _wikidata_image(mbid)
            except Exception:                           # noqa: BLE001
                wd = None
        cands += fanart_candidates(mbid) if not idx["pictures"] else []
        dec = _decade(year)
        era = year if dec and (dec not in idx["decades"] or idx.get("incomplete")) else None
        cands += commons_candidates(name, era, wd)
        if dec and dec not in idx["decades"]:
            idx["decades"].append(dec)
        cands = [c for c in cands if c.get("url") and c["url"] not in known][:CANDIDATES_MAX]
        ai = ai_ready()
        covers = sum(1 for p in idx["pictures"] if p.get("kind") == "cover")
        kept_now, later = 0, 0
        with _lock:
            _busy[s] = "vetting"
        for c in cands:
            if len(idx["pictures"]) >= TARGET + (4 if era else 0):
                break
            if c.get("kind_hint") == "cover" and covers >= COVERS_MAX:
                continue
            if not ai and not c.get("trusted"):
                continue                                # only the catalogue's own until the AI can look
            try:
                fname, w, h = _save(c["url"], folder)
            except Later:
                later += 1
                continue
            except Exception as e:                      # noqa: BLE001
                idx.setdefault("rejected", {})[c["url"]] = f"download: {type(e).__name__}"[:80]
                continue
            v = None
            if ai:
                try:
                    v = vet(folder / fname, name, c)
                except Exception as e:                  # noqa: BLE001
                    error(f"karaoke pictures: vetting {fname}: {e}")
                if v is None and not c.get("trusted"):
                    (folder / fname).unlink(missing_ok=True)    # unvetted and untrusted: later, when the AI answers
                    continue
            if v is not None:
                ok, why = keep(v)
                if not ok:
                    (folder / fname).unlink(missing_ok=True)
                    idx.setdefault("rejected", {})[c["url"]] = why
                    continue
            kind = (v or {}).get("kind") or c.get("kind_hint") or "other"
            covers += kind == "cover"
            idx["pictures"].append({
                "file": fname, "url": c["url"], "source": c["source"], "credit": c.get("credit"),
                "license": c.get("license"), "page": c.get("page"), "title": c.get("title"), "w": w, "h": h,
                "kind": kind, "vetted": v is not None, "quality": (v or {}).get("quality"),
                "faces": (v or {}).get("faces") or [], "era": (v or {}).get("era") or _decade(c.get("year")),
                "moods": (v or {}).get("moods") or [], "caption": (v or {}).get("caption"),
                "hidden": False, "at": time.time()})
            kept_now += 1
            _write(folder / "index.json", {**idx, "version": VERSION})   # the song has it now, not when all are done
        full = len(idx["pictures"]) >= TARGET
        idx.update(fetched_at=time.time(), ai=ai, version=VERSION, incomplete=bool(later) and not full)
        _write(folder / "index.json", idx)
        info(f"karaoke pictures: {name}: kept {kept_now} new ({len(idx['pictures'])} in all, "
             f"{len(cands)} looked at{f', {later} for later' if later else ''}{'' if ai else ', no AI: catalogue pictures only'})")
        return idx
    finally:
        with _lock:
            _busy.pop(s, None)


def _unvetted(idx: dict) -> bool:
    return any(not p.get("vetted") for p in idx.get("pictures") or [])


def revet(s: str) -> None:
    """The AI looks at pictures kept without it (catalogue pictures kept while it was away)."""
    idx = artist_index(s)
    folder = pictures_root() / s
    changed = False
    for p in list(idx.get("pictures") or []):
        if p.get("vetted"):
            continue
        v = vet(folder / p["file"], idx.get("artist") or s, {"source": p["source"], "title": p.get("title"),
                                                             "kind_hint": p.get("kind")})
        if v is None:
            break
        changed = True
        ok, why = keep(v)
        if not ok:
            (folder / p["file"]).unlink(missing_ok=True)
            idx["pictures"].remove(p)
            idx.setdefault("rejected", {})[p["url"]] = why
            continue
        p.update(vetted=True, kind=v["kind"], quality=v["quality"], faces=v["faces"], era=v["era"] or p.get("era"),
                 moods=v["moods"], caption=v["caption"])
    if changed:
        _write(folder / "index.json", idx)


# ---------------------------------------------------------------- a song's pictures
def song_artists(sid: str, song: dict) -> Optional[list[dict]]:
    """The song's artists (resolved once, kept in songs.json): [{name, slug, deezer}], or None if not resolved yet."""
    m = _songs_map().get(sid)
    if m and m.get("artist") == song.get("artist"):
        return m["artists"]
    return None


def _resolve_song(sid: str, song: dict) -> list[dict]:
    arts = resolve_artists(song.get("artist") or "")
    rows = [{"name": a["name"], "slug": slug(a["name"]),
             "deezer": {k: (a["deezer"] or {}).get(k) for k in ("id", "name", "picture_xl", "nb_fan")}
             if a.get("deezer") else None} for a in arts]
    with _lock:
        m = _songs_map()
        m[sid] = {"artist": song.get("artist"), "artists": rows, "at": time.time()}
        _write(pictures_root() / "songs.json", m)
    return rows


def song_pictures(sid: str) -> dict:
    """What the renderer and the Room page read: {state, artists, era, pictures}. ``state``: "fetching" (more may come:
    the pictures so far are listed), "ready" (done, pictures kept) or "none" (done, nothing found)."""
    st = store()
    with st.lock:
        song = dict(st.s["songs"].get(sid) or {})
    if not song:
        return {"state": "none", "artists": [], "pictures": []}
    arts = song_artists(sid, song)
    if arts is None:
        return {"state": "fetching", "artists": [], "pictures": []}
    out, pending = [], False
    for a in arts:
        idx = artist_index(a["slug"])
        pending = pending or not idx.get("fetched_at") or a["slug"] in _busy or bool(idx.get("incomplete"))
        for p in idx.get("pictures") or []:
            if p.get("hidden"):
                continue
            path = pictures_root() / a["slug"] / p["file"]
            if not path.is_file():
                continue
            out.append({"id": f"{a['slug']}/{p['file']}", "artist": a["name"], "path": str(path),
                        "url": f"/api/karaoke/pictures/{a['slug']}/{p['file']}",
                        **{k: p.get(k) for k in ("w", "h", "kind", "quality", "faces", "era", "moods", "caption",
                                                 "source", "credit", "license", "vetted")}})
    year = song.get("year")
    dec = _decade(year)
    out.sort(key=lambda p: (p.get("era") != dec, -(p.get("quality") or 0.5)))      # the song's era first
    return {"state": "fetching" if pending else ("ready" if out else "none"),
            "artists": [a["name"] for a in arts], "era": dec, "pictures": out}


# ---------------------------------------------------------------- the thread
def _todo() -> list[tuple[str, dict]]:
    """Songs in the order their pictures are wanted: the one playing, the queue after it, then the library."""
    st = store()
    with st.lock:
        songs = {k: dict(v) for k, v in st.s["songs"].items()}
        q = [e["song_id"] for e in st.s["queue"]]
        cur = (st.s["player"] or {}).get("song_id")
        qid = (st.s["player"] or {}).get("qid")
        at = next((i for i, e in enumerate(st.s["queue"]) if e["qid"] == qid), 0)
    order = ([cur] if cur else []) + q[at:] + q[:at] + sorted(songs, key=lambda k: -(songs[k].get("created_at") or 0))
    seen, out = set(), []
    for sid in order:
        if sid in songs and sid not in seen:
            seen.add(sid)
            out.append((sid, songs[sid]))
    return out


def step() -> bool:
    """One unit of work (one song resolved, or one artist fetched or re-vetted). True if something was done."""
    now = time.time()
    for sid, song in _todo():
        if not song.get("artist") or now - _failed.get(f"song:{sid}", 0) < RETRY_S:
            continue
        arts = song_artists(sid, song)
        if arts is None:
            try:
                _resolve_song(sid, song)
            except Exception as e:                      # noqa: BLE001 - Deezer down: later
                _failed[f"song:{sid}"] = now
                error(f"karaoke pictures: who sings {song.get('title')}: {e}")
            return True
        for a in arts:
            s = a["slug"]
            if now - _failed.get(s, 0) < RETRY_S:
                continue
            idx = artist_index(s)
            dec = _decade(song.get("year"))
            again = (idx.get("incomplete") and time.time() >= _commons["pause_until"]
                     and now - float(idx.get("fetched_at") or 0) > 60)
            if (not idx or idx.get("version") != VERSION or again
                    or (ERA_FETCH and dec and dec not in (idx.get("decades") or []))):
                try:
                    fetch_artist(a["name"], a.get("deezer"), album=song.get("album"), year=song.get("year"))
                except Exception as e:                  # noqa: BLE001
                    _failed[s] = now
                    error(f"karaoke pictures: {a['name']}: {e}")
                return True
            if _unvetted(idx) and ai_ready():
                try:
                    revet(s)
                except Exception as e:                  # noqa: BLE001
                    _failed[s] = now
                    error(f"karaoke pictures: re-vetting {a['name']}: {e}")
                return True
    return False


def run() -> None:
    while True:
        try:
            if not step():
                time.sleep(LOOP_S)
        except Exception as e:                          # noqa: BLE001 - never stops
            error(f"karaoke pictures: {e}")
            time.sleep(LOOP_S)


def start() -> None:
    global _thread
    if _thread is None:
        _thread = threading.Thread(target=run, name="karaoke-pictures", daemon=True)
        _thread.start()


# ---------------------------------------------------------------- routes (on the karaoke blueprint)
@karaoke_api.get("/karaoke/songs/<sid>/pictures")
def api_song_pictures(sid: str):
    return jsonify(song_pictures(sid))


def _file(s: str, name: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", s) or not re.fullmatch(r"[0-9a-f]{16}\.jpg", name):
        abort(404)
    p = pictures_root() / s / name
    if not p.is_file():
        abort(404)
    return p


@karaoke_api.get("/karaoke/pictures/<s>/<name>")
def api_picture(s: str, name: str):
    return send_file(_file(s, name), mimetype="image/jpeg", max_age=86400)


@karaoke_api.post("/karaoke/pictures/<s>/<name>/hide")
def api_hide(s: str, name: str):
    """Hide (or show again: {"hidden": false}) one picture, for every song of that artist."""
    _file(s, name)
    hidden = (request.get_json(silent=True) or {}).get("hidden", True) is not False
    with _lock:
        idx = artist_index(s)
        for p in idx.get("pictures") or []:
            if p["file"] == name:
                p["hidden"] = hidden
        _write(pictures_root() / s / "index.json", idx)
    return jsonify({"ok": True, "hidden": hidden})


__all__ = ["start", "song_pictures", "fetch_artist", "resolve_artists", "step"]
