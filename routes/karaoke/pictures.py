"""karaoke.pictures: pictures of each song's artist, for the renderer's photo scenes (owner, 26 Nov: "photo processing
scenes ... which use publicly-available-from-the-web images of the singer and/or band ... save them and tag them
locally in a 'karaoke' bucket so that it effectively gets cached for any taylor swift track").

Per ARTIST, once, kept: ``~/.local/state/screen-machine/karaoke/pictures/<artist-slug>/`` (smconfig.state) holds the
pictures (JPEG, longest side SAVE_PX) and ``index.json`` (where each came from, its credit and licence, and what the
vetting said).  ``songs.json`` maps each song to its artists.  Never in git, never a screen's bucket: this house's
karaoke screen only.

Where they come from (looked up by catalogue identity, never a loose web image search: wrong people, rights, and
this is a children's party):
  * Deezer (no key): the artist's own picture (no album covers: owner, 26 Nov, "album cover art isn't a very
    charismatic thing to show ... we need artists performing");
  * Wikimedia Commons (no key, freely licensed): file searches for the artist in concert / live / performing (and
    in the song's year, so a 1985 song can show 1985), then any picture of them, stage titles first; MusicBrainz ->
    Wikidata's own picture of the artist first;
  * fanart.tv (FANARTTV_API_KEY in .env, optional): screen-shaped 1920x1080 artist backgrounds, by MusicBrainz id;
  * TheAudioDB (its free public key): the artist's fanart (1280x720) and photo;
  * YouTube (owner, 26 Nov: "surely there are photos trivially available. we're not broadcasting -- this is just for
    a private party"): frames from the artist's concert films and live videos (the song's year first), taken from a
    2 s clip yt-dlp fetches (the karaoke venv), and those videos' thumbnails.

The vetting (task ``song_picture`` in config/llm.yaml: Gemini, one 768 px look per picture): does it show this artist
or their album's cover, is it sharp, is it suitable for a children's party (strict), where are the faces, what era,
which moods.  A picture that fails is deleted and remembered (never fetched again).  Without the AI (no key, over the
day's budget), only Deezer's own picture of the artist is used until it can look.  Kept: the artist performing
(live), the band (group), and at most PORTRAITS_MAX posed portraits; never covers, objects or other people.

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
AUDIODB = "https://www.theaudiodb.com/api/v1/json/123"     # TheAudioDB's free public key
YT_VIDEOS = 4            # live videos per artist whose frames are looked at
YT_FRAMES_LONG = 6       # frames from a concert film (20 min or more)
YT_FRAMES_SHORT = 3      # frames from a single live song
YT_WORDS = re.compile(r"\b(live|concert|tour|performance|performing|unplugged|festival|gig|session|stage)\b")
TASK = "song_picture"
VERSION = 3               # 2: people only, performing first (owner, 26 Nov: covers aren't charismatic); 3: YouTube
                          # concert frames, TheAudioDB, band members

TARGET = 14              # kept pictures of the artist an artist's deck aims for
PORTRAITS_MAX = 5        # of which posed portraits at most: the rest performing (live) or the band (group)
PEOPLE = ("live", "group", "portrait")   # what the photo scenes show (owner, 26 Nov: "album cover art isn't a very
                                         # charismatic thing to show ... we need artists performing etc.")
CANDIDATES_MAX = 70      # pictures looked at per artist per fetch
STAGE = re.compile(r"\b(concert|live|tour|performing|performs|performance|stage|festival|gig|on stage|in concert)\b")
MIN_SHORT_SIDE = 560     # smaller is too soft even under the effects
MAX_BYTES = 15 << 20
SAVE_PX = 2048
COMMONS_STEPS = (1920, 1280, 960)   # Wikimedia's standard thumbnail widths: only these are fetched (an original
                                    # file, or another width, is answered with 429 and a 10 minute wait)
COMMONS_GAP_S = 12.0   # between two Commons downloads: an unnamed client gets a slow trickle, a burst a 429 and 10 min
VET_PX = 768
MIN_QUALITY = 0.45
MIN_APPEAL = 0.35        # duller than this on a big screen: not kept (owner, 26 Nov: "engaging photos to look at")
RETRY_S = 1800.0
LOOP_S = 10.0
ERA_FETCH = True         # a Commons search for the song's year as well (pictures of that era)
SPLIT_MIN_FANS = 20000   # an artist named by splitting a line ("A feat. B") must be this well known on Deezer
MEMBERS_MAX = 4          # a band's members searched for by name on Commons (their photos are filed under them)
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
    if whole is None:
        m = re.fullmatch(r"([^,&/;]+),\s*([^,&/;]+)", artist)        # "Williams, Pharrell": a catalogue's surname first
        if m and len(m.group(1).split()) <= 2 and len(m.group(2).split()) <= 2:
            whole = deezer_artist(f"{m.group(2).strip()} {m.group(1).strip()}")
    if whole is not None:
        return [{"name": whole["name"], "deezer": whole}]
    out = []
    for part in [p.strip() for p in SPLIT.split(artist) if p and p.strip()][:3]:
        a = deezer_artist(part)
        # a split-off name only when Deezer knows a well-known artist by it ("Williams" alone is someone else)
        if a is not None and (a.get("nb_fan") or 0) >= SPLIT_MIN_FANS:
            out.append({"name": a["name"], "deezer": a})
    return out


def _mbid(name: str) -> Optional[str]:
    from .sources import _mb
    d = _mb("artist", query=f'artist:"{name}"', limit=5) or {}
    same = [a for a in d.get("artists") or [] if norm(a.get("name")) == norm(name) and (a.get("score") or 0) >= 90]
    return max(same, key=lambda a: a.get("score") or 0)["id"] if same else None


def _mb_artist(mbid: str) -> dict:
    from .sources import _mb
    return _mb(f"artist/{mbid}", inc="url-rels+artist-rels") or {}


def members(d: dict) -> list[str]:
    """A band's members (MusicBrainz "member of band"), the longest-serving first, at most MEMBERS_MAX."""
    if d.get("type") != "Group":
        return []
    rows = []
    for r in d.get("relations") or []:
        if r.get("type") == "member of band" and r.get("direction") == "backward" and (r.get("artist") or {}).get("name"):
            y0 = int(str(r.get("begin") or "9999")[:4] or 9999)
            y1 = int(str(r.get("end") or "2100")[:4] or 2100)
            rows.append((-(y1 - y0), r["artist"]["name"]))
    out = []
    for _, n in sorted(rows, key=lambda r: r[0]):        # longest-serving first, else MusicBrainz's order
        if n not in out:
            out.append(n)
    return out[:MEMBERS_MAX]


def _wikidata_image(d: dict) -> Optional[str]:
    """Wikidata's own picture of the artist (P18), as a Commons file title, via MusicBrainz's link."""
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
    out: list[dict] = []
    pic = a.get("picture_xl") or ""
    if pic and "/artist//" not in pic:                      # Deezer's blank silhouette has no image hash
        out.append({"url": pic, "source": "deezer", "kind_hint": "portrait", "credit": "Deezer",
                    "license": "catalogue", "title": f"{a.get('name')} (Deezer)", "trusted": True})
    return out                                          # no album covers (owner, 26 Nov)


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


def commons_candidates(name: str, year: Optional[int], wikidata_file: Optional[str],
                       band: tuple = ()) -> list[dict]:
    """``band``: the members' names (a band's photos are mostly filed under its members)."""
    out = []
    if wikidata_file:
        try:
            out += _commons_rows({"titles": wikidata_file})
        except Exception:                               # noqa: BLE001
            pass
    # performing first: the song's year on stage, then on stage at any time, then anything of them
    queries = [f'"{name}" {w} filetype:bitmap' for w in ("concert", "live", "performing")] + [f'"{name}" filetype:bitmap']
    if ERA_FETCH and isinstance(year, int) and year > 1900:
        queries[:0] = [f'"{name}" {year} concert filetype:bitmap', f'"{name}" {year} filetype:bitmap']
    queries[len(queries) - 1:len(queries) - 1] = [f'"{m}" {w} filetype:bitmap' for m in band for w in ("concert", "live")]
    for q in queries:
        try:
            out += _commons_rows({"generator": "search", "gsrsearch": q, "gsrnamespace": 6, "gsrlimit": 30})
        except Exception as e:                          # noqa: BLE001
            error(f"karaoke pictures: Commons search for {name}: {e}")
    nk = set(norm(name).split()) | {w for m in band for w in norm(m).split() if len(w) > 3}
    keep, seen = [], set()
    for c in out:
        t = norm(re.sub(r"\((?:[^)]*crop[^)]*|enhanced[^)]*|\d+)\)", "", c["title"]))
        base = re.sub(r"\b(cropped|jpg|png|\d+)\b", "", t).strip()
        if base in seen or not nk & set(t.split()) or re.search(r"\b(logo|signature|autograph|wordmark)\b", t):
            continue
        seen.add(base)
        keep.append(c)
    keep.sort(key=lambda c: not STAGE.search(norm(c["title"])))       # stable: stage titles first, in search order
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


def audiodb_candidates(name: str) -> list[dict]:
    try:
        d = _get(f"{AUDIODB}/search.php", {"s": name}) or {}
    except Exception:                                   # noqa: BLE001
        return []
    a = next((x for x in d.get("artists") or [] if norm(x.get("strArtist")) == norm(name)), None)
    if a is None:
        return []
    out = []
    for k, hint in (("strArtistFanart", "live"), ("strArtistFanart2", "live"), ("strArtistFanart3", "live"),
                    ("strArtistFanart4", "live"), ("strArtistWideThumb", "live"), ("strArtistThumb", "portrait")):
        if a.get(k):
            out.append({"url": a[k], "source": "theaudiodb", "kind_hint": hint, "credit": "TheAudioDB",
                        "license": "theaudiodb", "title": f"{name} ({k[9:]})", "trusted": False})
    return out


def youtube_candidates(name: str, year: Optional[int]) -> list[dict]:
    """Frames from the artist's live videos (concert films first, the song's year first), then their thumbnails."""
    from .sources import _run, _ytdlp_cmd
    queries = ([f"{name} live {year}"] if isinstance(year, int) and year > 1900 else []) + [
        f"{name} live concert", f"{name} live performance"]
    vids: dict[str, dict] = {}
    nk = set(norm(name).split())
    for q in queries:
        try:
            out = _run(_ytdlp_cmd() + ["--flat-playlist", "-j", f"ytsearch8:{q}"], 25)
        except Exception as e:                          # noqa: BLE001
            error(f"karaoke pictures: YouTube search for {name}: {e}")
            continue
        for line in out.splitlines():
            try:
                v = json.loads(line)
            except ValueError:
                continue
            t = norm(v.get("title"))
            if v.get("id") and v["id"] not in vids and nk <= set(t.split()) and YT_WORDS.search(t) \
                    and not re.search(r"\b(karaoke|cover|tribute|reaction|lyrics|tutorial|lesson)\b", t) \
                    and float(v.get("duration") or 0) >= 120:
                vids[v["id"]] = v
    ranked = sorted(vids.values(), key=lambda v: -min(float(v.get("duration") or 0), 3600.0))[:YT_VIDEOS]
    frames, thumbs = [], []
    for v in ranked:
        dur = float(v.get("duration") or 0)
        n = YT_FRAMES_LONG if dur >= 1200 else YT_FRAMES_SHORT
        for i in range(n):
            sec = int(dur * (0.12 + 0.76 * i / max(1, n - 1)))
            frames.append((i, {"url": f"yt:{v['id']}@{sec}", "source": "youtube", "kind_hint": "live",
                               "credit": "YouTube", "license": "youtube",
                               "title": f"a frame from the video '{v.get('title')}'", "trusted": False}))
        thumbs.append({"url": f"https://i.ytimg.com/vi/{v['id']}/maxresdefault.jpg", "source": "youtube",
                       "kind_hint": "live", "credit": "YouTube", "license": "youtube",
                       "title": f"the thumbnail of the video '{v.get('title')}'", "trusted": False})
    return [c for _, c in sorted(frames, key=lambda x: x[0])] + thumbs     # one from each video in turn


def _yt_frame(spec: str) -> bytes:
    """``yt:<id>@<sec>`` -> one frame as JPEG bytes (a 2 s clip fetched by yt-dlp, the frame taken locally)."""
    import subprocess
    import tempfile
    from .sources import _run, _ytdlp_cmd
    vid, sec = spec[3:].split("@")
    with tempfile.TemporaryDirectory() as d:
        try:
            _run(_ytdlp_cmd() + ["-f", "bv*[height<=1080][protocol=https]/bv*[height<=1080]", "--download-sections",
                                 f"*{sec}-{int(sec) + 2}", "-o", f"{d}/clip.%(ext)s",
                                 f"https://www.youtube.com/watch?v={vid}"], 90)
        except Exception as e:                          # noqa: BLE001 - busy, blocked or gone: try later
            raise Later(f"YouTube: {e}"[:80]) from None
        clip = next(Path(d).glob("clip.*"), None)
        if clip is None:
            raise ValueError("no clip")
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "1", "-q:v", "2", "-y", f"{d}/f.jpg"],
                       capture_output=True, timeout=30)
        f = Path(d) / "f.jpg"
        if not f.is_file():
            raise ValueError("no frame")
        return f.read_bytes()


def _trim(im):
    """Cut dark bars (a 4:3 concert film in a 16:9 frame, letterboxing) off a picture."""
    import numpy as np
    g = np.asarray(im.convert("L").resize((max(1, im.width // 8), max(1, im.height // 8))), np.float32)
    cols, rows = np.where(g.mean(axis=0) > 14)[0], np.where(g.mean(axis=1) > 14)[0]
    if not len(cols) or not len(rows):
        return im
    l, r, t, b = cols[0] * 8, (cols[-1] + 1) * 8, rows[0] * 8, (rows[-1] + 1) * 8
    if (r - l) < 0.5 * im.width or (b - t) < 0.5 * im.height or (l <= 8 and t <= 8 and r >= im.width - 8
                                                                and b >= im.height - 8):
        return im
    return im.crop((l, t, min(r, im.width), min(b, im.height)))


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
                "kind": {"type": "string", "enum": ["live", "group", "portrait", "cover", "other"],
                         "description": "live: performing (on stage, singing, playing); group: the band or several "
                                        "members; portrait: one person posing (red carpet, press, close-up); cover: "
                                        "an album cover; other: objects, merchandise, fans, places."},
                "quality": {"type": "number", "minimum": 0, "maximum": 1,
                            "description": "Sharp, well lit, well framed, no watermark: 1. Blurry, tiny, cluttered: 0."},
                "appeal": {"type": "number", "minimum": 0, "maximum": 1,
                           "description": "How engaging it is to look at on a big screen at a party: a striking, "
                                          "dynamic, well-lit moment of them performing, or a great close portrait "
                                          "with character: near 1. Distant, dull, cluttered, awkward or ordinary "
                                          "snapshots: low."},
                "child_safe": {"type": "boolean"},
                "faces": {"type": "array", "items": box, "maxItems": 4,
                          "description": "Each main person's head (hair included) as [ymin, xmin, ymax, xmax] on a "
                                         "0-1000 scale."},
                "era": {"type": "string", "description": "The decade it was taken, like 1980s; empty if unknown."},
                "moods": {"type": "array", "items": {"type": "string", "enum": _moods() or ["warm"]}, "maxItems": 3},
                "caption": {"type": "string", "description": "At most 12 words."}},
                "required": ["shows", "kind", "quality", "appeal", "child_safe", "faces", "moods"]}}


SYSTEM = ("You check pictures for a family karaoke screen at a child's birthday party. Each picture was found for "
          "one music artist (a singer or a band). Say whether it really shows that artist (or its members), and "
          "what kind of picture it is; how good it is; and whether it is suitable for young children. child_safe "
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


def vet(path: Path, artist: str, c: dict, band: Any = ()) -> Optional[dict]:
    """The AI's look at one saved picture -> its answer (cleaned), or None when the AI couldn't answer."""
    import utils.llm as llm
    who = (f"{artist} (a band; a picture of one of its members, {', '.join(band)}, counts as the band)"
           if band else artist)
    text = (f"Artist: {who}\nFound on: {c.get('source')}\nIts title there: {c.get('title') or '-'}\n"
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
            "appeal": round(min(1.0, max(0.0, float(d.get("appeal") if d.get("appeal") is not None else 0.5))), 2),
            "child_safe": d.get("child_safe") is True, "faces": faces[:4],
            "era": era if re.fullmatch(r"(19|20)\d0s", era) else None,
            "moods": [m for m in d.get("moods") or [] if m in moods][:3],
            "caption": str(d.get("caption") or "")[:100], "model": res.model, "cost_usd": float(res.cost_usd or 0.0)}


def keep(v: dict) -> tuple[bool, str]:
    if not v["child_safe"]:
        return False, "not for children"
    if v["shows"] != "the_artist":
        return False, f"shows {v['shows']}"
    if v["kind"] not in PEOPLE:
        return False, f"a {v['kind']}"
    if v["quality"] < MIN_QUALITY:
        return False, f"quality {v['quality']}"
    if v.get("appeal", 1.0) < MIN_APPEAL:
        return False, f"appeal {v['appeal']}"
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
        data = _yt_frame(url) if url.startswith("yt:") else _get(url, raw=True)
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
    im = _trim(im.convert("RGB"))
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
        if mbid and (not idx["pictures"] or idx.get("members") is None):
            try:
                md = _mb_artist(mbid)
                idx["members"] = members(md)
                wd = _wikidata_image(md) if not idx["pictures"] else None
            except Exception:                           # noqa: BLE001
                wd = None
        cands += fanart_candidates(mbid) if not idx["pictures"] else []
        cands += audiodb_candidates(name) if not idx["pictures"] else []
        dec = _decade(year)
        era = year if dec and (dec not in idx["decades"] or idx.get("incomplete")) else None
        try:
            cands += youtube_candidates(name, era)
        except Exception as e:                          # noqa: BLE001 - no karaoke venv here: the rest still work
            error(f"karaoke pictures: YouTube for {name}: {e}")
        cands += commons_candidates(name, era, wd, tuple(idx.get("members") or ()))
        if dec and dec not in idx["decades"]:
            idx["decades"].append(dec)
        cands = [c for c in cands if c.get("url") and c["url"] not in known][:CANDIDATES_MAX]
        ai = ai_ready()
        people = sum(1 for p in idx["pictures"] if p.get("kind") in PEOPLE and not p.get("hidden"))
        portraits = sum(1 for p in idx["pictures"] if p.get("kind") == "portrait")
        kept_now, later = 0, 0
        with _lock:
            _busy[s] = "vetting"
        for c in cands:
            if people >= TARGET + (4 if era else 0):
                break
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
                    v = vet(folder / fname, name, c, idx.get("members") or ())
                except Exception as e:                  # noqa: BLE001
                    error(f"karaoke pictures: vetting {fname}: {e}")
                if v is None and not c.get("trusted"):
                    (folder / fname).unlink(missing_ok=True)    # unvetted and untrusted: later, when the AI answers
                    continue
            if v is not None:
                ok, why = keep(v)
                if ok and v["kind"] == "portrait" and portraits >= PORTRAITS_MAX:
                    ok, why = False, "enough portraits"
                if not ok:
                    (folder / fname).unlink(missing_ok=True)
                    idx.setdefault("rejected", {})[c["url"]] = why
                    continue
            kind = (v or {}).get("kind") or c.get("kind_hint") or "other"
            people += kind in PEOPLE
            portraits += kind == "portrait"
            idx["pictures"].append({
                "file": fname, "url": c["url"], "source": c["source"], "credit": c.get("credit"),
                "license": c.get("license"), "page": c.get("page"), "title": c.get("title"), "w": w, "h": h,
                "kind": kind, "vetted": v is not None, "quality": (v or {}).get("quality"),
                "appeal": (v or {}).get("appeal"),
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
    """Pictures the AI hasn't looked at, or looked at before it scored appeal."""
    return any(not p.get("vetted") or p.get("appeal") is None for p in idx.get("pictures") or [])


def revet(s: str) -> None:
    """The AI looks at pictures kept without it (catalogue pictures kept while it was away)."""
    idx = artist_index(s)
    folder = pictures_root() / s
    changed = False
    for p in list(idx.get("pictures") or []):
        if p.get("vetted") and p.get("appeal") is not None:
            continue
        v = vet(folder / p["file"], idx.get("artist") or s, {"source": p["source"], "title": p.get("title"),
                                                             "kind_hint": p.get("kind")}, idx.get("members") or ())
        if v is None:
            break
        changed = True
        ok, why = keep(v)
        if not ok:
            (folder / p["file"]).unlink(missing_ok=True)
            idx["pictures"].remove(p)
            idx.setdefault("rejected", {})[p["url"]] = why
            continue
        p.update(vetted=True, kind=v["kind"], quality=v["quality"], appeal=v["appeal"], faces=v["faces"],
                 era=v["era"] or p.get("era"),
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
            if p.get("hidden") or p.get("kind") not in PEOPLE:
                continue
            path = pictures_root() / a["slug"] / p["file"]
            if not path.is_file():
                continue
            out.append({"id": f"{a['slug']}/{p['file']}", "artist": a["name"], "path": str(path),
                        "url": f"/api/karaoke/pictures/{a['slug']}/{p['file']}",
                        **{k: p.get(k) for k in ("w", "h", "kind", "quality", "appeal", "faces", "era", "moods", "caption",
                                                 "source", "credit", "license", "vetted")}})
    year = song.get("year")
    dec = _decade(year)
    out.sort(key=lambda p: (p.get("era") != dec, -(p.get("appeal") if p.get("appeal") is not None else 0.4)))
    # the song's era first, then the most engaging
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
            again = ((idx.get("incomplete") or idx.get("members") is None) and time.time() >= _commons["pause_until"]
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
