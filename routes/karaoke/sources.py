"""karaoke.sources: turning a request into a song's audio and lyrics (docs/KARAOKE.md).

- **LRCLIB** (lrclib.net, free, no key): song search and synced lyrics (LRC). Its artist, title and duration also pick
  the matching recording on YouTube, so the lyrics' timings fit the audio.
- **Spotify** track links: the public page's og: tags give the title, artist and length (no API key). Spotify's audio is
  DRM'd, so the audio comes from YouTube, matched on those (spotDL's approach).
- **YouTube and any other yt-dlp site**: yt-dlp, run in the karaoke venv (deployment host.karaoke_python).
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import smconfig

from .store import norm

UA = "screen-machine-karaoke/1.0"
LRCLIB = "https://lrclib.net/api"
HTTP_TIMEOUT_S = 15


class SourceError(Exception):
    """Something a person can read: "No song on YouTube matched ...", "That Spotify link isn't a track"."""


# ---------------------------------------------------------------- HTTP
def _get_json(url: str, params: dict | None = None) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


# ---------------------------------------------------------------- the song search (iTunes + Deezer)
# The panel's live search (owner, 25 Nov: "it should cope better with misspellings, partial words"): two music
# catalogues that need no key and forgive spelling ("hapy farell", "mr brightsid"), asked at once, merged (iTunes'
# popularity order first), one row per artist + title. LRCLIB's own search, which needs the words right, is the
# fallback. The worker finds the recording and the lyrics from the row's artist, title and length.
ITUNES = "https://itunes.apple.com/search"
DEEZER = "https://api.deezer.com/search"
SEARCH_TIMEOUT_S = 6
VARIANTS = ("karaoke", "instrumental", "cover", "nightcore", "sped up", "slowed", "remix", "tribute", "made famous",
            "originally performed", "piano version", "acoustic version", "8-bit", "lullaby", "ensemble", "medley",
            "(live", "live at", "live from", "demo)", " mix)", "mashup", "parody")
EDITION = re.compile(r"\s*(?:[\(\[][^\)\]]*\b(?:single|radio|album|edit|version|remaster(?:ed)?|mono|stereo|"
                     r"from [^\)\]]*|feat\.?|ft\.?|with)\b[^\)\]]*[\)\]]|\s+-\s+(?:single|radio|remaster(?:ed)?)\b.*)$", re.I)


def _get_json_quick(url: str, params: dict) -> Any:
    url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT_S) as r:
        return json.load(r)


def _year(s: Any) -> int | None:
    m = re.match(r"(\d{4})", str(s or ""))
    return int(m.group(1)) if m else None


def _itunes(q: str, limit: int) -> list[dict]:
    o = _get_json_quick(ITUNES, {"term": q, "entity": "song", "media": "music", "limit": limit * 3}) or {}
    return [{"artist": r.get("artistName") or "", "title": r.get("trackName") or "", "album": r.get("collectionName"),
             "duration": round(r["trackTimeMillis"] / 1000) if r.get("trackTimeMillis") else None,
             "art": r.get("artworkUrl100"), "genre": r.get("primaryGenreName"), "year": _year(r.get("releaseDate")),
             "rank": None, "from": "itunes"} for r in o.get("results") or []]


def _deezer_row(r: dict) -> dict:
    return {"artist": (r.get("artist") or {}).get("name") or "", "title": r.get("title") or "",
            "album": (r.get("album") or {}).get("title"), "duration": r.get("duration") or None,
            "art": (r.get("album") or {}).get("cover_small"), "genre": None, "year": None,
            "rank": r.get("rank") or None, "from": "deezer"}


def _deezer(q: str, limit: int) -> list[dict]:
    o = _get_json_quick(DEEZER, {"q": q, "limit": limit * 3}) or {}
    return [_deezer_row(r) for r in o.get("data") or []]


def artist_top(name: str, limit: int = 50) -> dict:
    """An artist's songs, most popular first (Deezer's top tracks: its own play counts, the cheap popularity proxy):
    {artist, picture, fans, results: [rows as catalog_search's]}. The artist is the best-known one whose name fits
    (a misspelling finds the real one: "tayler swift" has 0 fans, Taylor Swift 12.8 M)."""
    o = _get_json_quick(DEEZER + "/artist", {"q": name, "limit": 8}) or {}
    cands = [a for a in o.get("data") or [] if a.get("id")]
    if not cands:
        return {"artist": name, "picture": None, "fans": None, "results": []}
    a = max(cands[:5], key=lambda x: x.get("nb_fan") or 0)
    top = _get_json_quick(f"https://api.deezer.com/artist/{a['id']}/top", {"limit": limit * 2}) or {}
    rows: dict[str, dict] = {}
    for r in top.get("data") or []:
        row = _deezer_row(r)
        if _variant(row, ""):
            continue
        row["title"] = _clean_title(EDITION.sub("", row["title"]).strip()) or row["title"]
        key = f"{norm(row['artist'])}|{norm(row['title'])}"
        if key not in rows:
            rows[key] = {**row, "key": key}
    return {"artist": a.get("name") or name, "picture": a.get("picture_medium"), "fans": a.get("nb_fan"),
            "results": list(rows.values())[:limit]}


def song_facts(artist: str, title: str) -> dict:
    """What the catalogues say about one song, for the library's tags and sorting: {genre, year, art, rank, album}
    (each None when unknown). Two quick lookups; nothing raises."""
    want = {"artist": artist, "title": title}
    out: dict = {"genre": None, "year": None, "art": None, "rank": None, "album": None}
    try:
        it = next((r for r in _itunes(f"{title} {artist}", 4) if _same_song(r, artist, title) and not _variant(r, title)), None)
    except Exception:  # noqa: BLE001
        it = None
    try:
        dz = next((r for r in _deezer(f'artist:"{artist}" track:"{title}"', 3) if _same_song(r, artist, title)), None)
    except Exception:  # noqa: BLE001
        dz = None
    for r in (it, dz):
        if r:
            for k in out:
                if out[k] is None and r.get(k):
                    out[k] = r[k]
    del want
    return out


def _youtube_guesses(q: str, n: int = 4) -> list[tuple[str, str]]:
    """YouTube's best guesses for free text as (artist, title): its search forgives spelling far better than the
    catalogues' ("hapy farell" -> Pharrell Williams, Happy). A flat search: about a second."""
    out = _run(_ytdlp_cmd() + ["--flat-playlist", "-j", f"ytsearch{n}:{q}"], 10)
    rows = []
    for line in out.splitlines():
        try:
            v = json.loads(line)
        except ValueError:
            continue
        a, t = split_video_title(v.get("title") or "", v.get("channel") or v.get("uploader") or "")
        if a and t and norm(a) != norm(t):
            rows.append((a, t))
    return rows


def _variant(r: dict, q: str) -> bool:
    """A karaoke, cover, live... version the person didn't ask for."""
    raw = f"{r['title']} {r['artist']} {r.get('album') or ''}".lower()
    return any(w in raw and w not in q.lower() for w in VARIANTS)


def _same_song(r: dict, artist: str, title: str) -> bool:
    nt, na = norm(r["title"]), norm(r["artist"])
    return bool(nt) and (nt == norm(title) or nt in norm(title) or norm(title) in nt) and (
        norm(artist) in na or na in norm(artist) or bool(set(na.split()) & set(norm(artist).split())))


def catalog_search(q: str, limit: int = 8) -> list[dict]:
    """Songs for free text, best first: [{artist, title, album, duration, art, from, key}]. Raises only if every
    catalogue failed. YouTube's guesses (asked at the same time) put the song the person meant first, and when the
    catalogues missed it (a misspelling), its own row comes from iTunes by the corrected name."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(3) as ex:
        futs = [ex.submit(_itunes, q, limit), ex.submit(_deezer, q, limit)]
        yt_f = ex.submit(_youtube_guesses, q)
        try:
            guesses = yt_f.result(timeout=8)
        except Exception:  # noqa: BLE001 - no YouTube: the catalogues alone
            guesses = []
        extra: list[dict] = []
        if guesses:
            ga, gt = guesses[0]
            try:
                first = futs[0].result() + futs[1].result()
            except Exception:  # noqa: BLE001
                first = []
            if not any(_same_song(r, ga, gt) for r in first if not _variant(r, q)):
                try:
                    extra = [r for r in _itunes(f"{gt} {ga}", 3) if _same_song(r, ga, gt) and not _variant(r, q)][:1]
                except Exception:  # noqa: BLE001
                    extra = []
    got, errors = [extra] if extra else [], []
    for f in futs:
        try:
            got.append(f.result())
        except Exception as e:  # noqa: BLE001 - one catalogue down: the other
            errors.append(e)
    if not any(got):
        try:
            rows = lrclib_search(q, limit)
        except Exception:  # noqa: BLE001
            if errors:
                raise errors[0]
            raise
        return [{**r, "album": r.get("album"), "art": None, "from": "lrclib",
                 "key": f"{norm(r['artist'])}|{norm(r['title'])}"} for r in rows]
    out: dict[str, dict] = {}
    score: dict[str, float] = {}
    for rows in got:
        for rank, r in enumerate(rows):
            if _variant(r, q):
                continue
            title = _clean_title(EDITION.sub("", r["title"]).strip()) or r["title"]
            artist = r["artist"].strip()
            if not title:
                continue
            key = f"{norm(artist)}|{norm(title)}"
            have = out.setdefault(key, {**r, "title": title, "artist": artist, "key": key})
            for k in ("genre", "year", "rank", "art", "album"):          # each catalogue fills what the other lacks
                if have.get(k) is None and r.get(k) is not None:
                    have[k] = r[k]
            # Each catalogue's place for it, added up: the song both put near the top (the original) comes first;
            # YouTube's guesses count most (they know what was meant).
            score[key] = score.get(key, 0.0) + 1.0 / (1 + rank) + sum(
                2.0 / (1 + j) for j, (ga, gt) in enumerate(guesses) if _same_song(out[key], ga, gt))
    ranked = sorted(out, key=lambda k: -score[k])        # stable: iTunes' order breaks ties
    return [out[k] for k in ranked[:limit]]


# ---------------------------------------------------------------- who wrote it (MusicBrainz)
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
MB_GAP_S = 1.2          # MusicBrainz asks for at most one request a second
WRITER_ROLES = ("composer", "lyricist", "writer", "librettist")


def _mb(path: str, **q: Any) -> Any:
    import time as _t
    _t.sleep(MB_GAP_S)
    url = f"{MUSICBRAINZ}/{path}?{urllib.parse.urlencode({**q, 'fmt': 'json'})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=10) as r:
        return json.load(r)


def song_writers(artist: str, title: str) -> list[str]:
    """Who wrote the song (the intro card's "Written by"; owner, 25 Nov: "song name, composer, artist name"):
    MusicBrainz's recording -> its work -> the work's composers, lyricists and writers, in its order. [] when
    MusicBrainz knows the song but not its writers, or doesn't know it. Raises when MusicBrainz doesn't answer (try
    again later)."""
    d = _mb("recording", query=f'recording:"{title}" AND artist:"{artist}"', limit=8)
    for r in [r for r in d.get("recordings") or [] if (r.get("score") or 0) >= 90][:3]:
        rr = _mb(f"recording/{r['id']}", inc="work-rels")
        work = next((x["work"]["id"] for x in rr.get("relations") or []
                     if x.get("type") == "performance" and x.get("work")), None)
        if not work:
            continue
        wd = _mb(f"work/{work}", inc="artist-rels")
        names = list(dict.fromkeys(x["artist"]["name"] for x in wd.get("relations") or []
                                   if x.get("artist") and x.get("type") in WRITER_ROLES))
        if names:
            return names
    return []


# ---------------------------------------------------------------- LRCLIB
def lrclib_search(q: str, limit: int = 8) -> list[dict]:
    """Songs matching free text, best first, one per artist+title (the version with synced lyrics preferred)."""
    rows = _get_json(f"{LRCLIB}/search", {"q": q}) or []
    out: dict[str, dict] = {}
    unwanted = [w for w in ("karaoke", "instrumental", "cover", "nightcore", "sped up", "remix") if w not in norm(q)]
    for r in rows:
        if r.get("instrumental"):
            continue
        artist = r.get("artistName") or ""
        title = _clean_title(strip_artist(_clean_title(r.get("trackName") or ""), artist))
        if any(w in norm(title) or w in norm(artist) for w in unwanted):
            continue
        key = f"{norm(artist)}|{norm(title)}"
        cand = {"lrclib_id": r.get("id"), "artist": artist, "title": title,
                "album": r.get("albumName"), "duration": r.get("duration"), "synced": bool(r.get("syncedLyrics"))}
        if key not in out or (cand["synced"] and not out[key]["synced"]):
            out[key] = cand
    ranked = sorted(out.values(), key=lambda c: not c["synced"])     # stable: LRCLIB's order, synced first
    return ranked[:limit]


def lrclib_lyrics(artist: str, title: str, duration: float | None) -> dict | None:
    """The best lyrics for this recording: {kind: synced|plain, lrc|text, lrclib_id, duration}, or None."""
    if duration:
        r = _get_json(f"{LRCLIB}/get", {"artist_name": artist, "track_name": title, "duration": int(round(duration))})
        if r and (r.get("syncedLyrics") or r.get("plainLyrics")):
            return _lyrics_of(r)
    rows = _get_json(f"{LRCLIB}/search", {"artist_name": artist, "track_name": title}) or []
    if not rows:
        rows = _get_json(f"{LRCLIB}/search", {"q": f"{artist} {title}"}) or []
    want = norm(title)
    rows = [r for r in rows if not r.get("instrumental") and (want in norm(r.get("trackName")) or norm(r.get("trackName")) in want)]

    def dist(r: dict) -> float:
        return abs((r.get("duration") or 0) - duration) if duration else 0.0
    synced = sorted((r for r in rows if r.get("syncedLyrics") and dist(r) <= 6), key=dist)
    if synced:
        return _lyrics_of(synced[0])
    plain = sorted((r for r in rows if r.get("plainLyrics")), key=dist)
    return _lyrics_of(plain[0]) if plain else None


def lrclib_candidates(artist: str, title: str, limit: int = 12) -> list[dict]:
    """Every distinct lyrics upload LRCLIB has for the song (synced first), as _lyrics_of gives them: the worker
    keeps the one whose words fit what is actually sung (lyrics.best_fit), never just the first."""
    rows = _get_json(f"{LRCLIB}/search", {"artist_name": artist, "track_name": title}) or []
    rows += _get_json(f"{LRCLIB}/search", {"q": f"{artist} {title}"}) or []
    want = norm(title)
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("instrumental") or not (want in norm(r.get("trackName")) or norm(r.get("trackName")) in want):
            continue
        if not (r.get("syncedLyrics") or r.get("plainLyrics")):
            continue
        c = _lyrics_of(r)
        key = norm(re.sub(r"\[[^\]]*\]", " ", c.get("lrc") or c.get("text") or ""))
        if key and (key not in out or (c["kind"] == "synced" and out[key]["kind"] != "synced")):
            out[key] = c
    return sorted(out.values(), key=lambda c: c["kind"] != "synced")[:limit]


def _lyrics_of(r: dict) -> dict:
    if r.get("syncedLyrics"):
        return {"kind": "synced", "lrc": r["syncedLyrics"], "lrclib_id": r.get("id"), "duration": r.get("duration")}
    return {"kind": "plain", "text": r.get("plainLyrics") or "", "lrclib_id": r.get("id"), "duration": r.get("duration")}


# ---------------------------------------------------------------- Spotify
SPOTIFY_TRACK = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]+)")


SPOTIFY_LIST = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?(?:embed/)?(playlist|album)/([A-Za-z0-9]+)")


def is_spotify_list(url: str) -> bool:
    return bool(SPOTIFY_LIST.search(url or ""))


def spotify_list(url: str) -> dict:
    """A Spotify playlist's or album's songs, without a Spotify key (owner, 26 Nov: "import a spotify playlist"): its
    public embed page carries the list (name, then each track's title, artists and length) as its page data.
    {kind, name, tracks: [{title, artist, duration}]} (the first artist named; the title without its "(feat. ...)" or
    edition words). SourceError when it can't be read."""
    m = SPOTIFY_LIST.search(url or "")
    if not m:
        raise SourceError("That isn't a Spotify playlist or album link.")
    kind, lid = m.group(1), m.group(2)
    req = urllib.request.Request(f"https://open.spotify.com/embed/{kind}/{lid}", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
            html = r.read().decode("utf-8", "replace")
        data = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
        ent = json.loads(data.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"Couldn't read that Spotify {kind} (is it public?): {e}") from e
    tracks = []
    for t in ent.get("trackList") or []:
        title = _clean_title(EDITION.sub("", str(t.get("title") or "")).strip())
        title = re.sub(r"\s*[\(\[](?:feat|ft|with)\.?[^\)\]]*[\)\]]", "", title, flags=re.I).strip() or str(t.get("title") or "")
        artist = str(t.get("subtitle") or "").split(",")[0].strip()
        dur = t.get("duration")
        if title:
            tracks.append({"title": title, "artist": artist, "duration": round(dur / 1000) if dur else None})
    return {"kind": kind, "name": str(ent.get("name") or ent.get("title") or "Spotify " + kind), "tracks": tracks}


def spotify_track(url: str) -> dict:
    m = SPOTIFY_TRACK.search(url)
    if not m:
        raise SourceError("Only Spotify links to a single track work (not albums or playlists).")
    req = urllib.request.Request(f"https://open.spotify.com/track/{m.group(1)}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:
        html = r.read().decode("utf-8", "replace")

    def meta(name: str) -> str | None:
        mm = re.search(rf'<meta (?:property|name)="{re.escape(name)}" content="([^"]*)"', html)
        return _unescape(mm.group(1)) if mm else None
    title, desc, dur = meta("og:title"), meta("og:description") or "", meta("music:duration")
    artist = desc.split(" · ")[0].strip() if " · " in desc else None
    if not title or not artist:
        raise SourceError("Couldn't read that Spotify track's title and artist.")
    return {"artist": artist, "title": title, "duration": float(dur) if dur and dur.isdigit() else None}


def _unescape(s: str) -> str:
    import html
    return html.unescape(s)


# ---------------------------------------------------------------- yt-dlp
def _ytdlp_cmd() -> list[str]:
    dep = smconfig.deployment()
    py = dep.host_path("karaoke_python")
    if not py or not Path(py).exists():
        raise SourceError("The karaoke tools aren't installed on this machine (deployment host.karaoke_python).")
    cmd = [str(py), "-m", "yt_dlp", "--no-playlist", "--no-warnings"]
    js = dep.host.get("karaoke_js_runtime")
    if js:
        cmd += ["--js-runtimes", str(js)]
    return cmd


def _run(cmd: list[str], timeout: float) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SourceError("The download took too long.") from None
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        raise SourceError(f"yt-dlp failed: {tail[0][:300]}")
    return p.stdout


def ytdlp_info(url: str) -> dict:
    """One video's metadata: {video_id, url, title, artist, duration, channel}, artist/title parsed when YouTube
    doesn't tag them."""
    d = json.loads(_run(_ytdlp_cmd() + ["-J", url], 90))
    artist, title = d.get("artist") or d.get("creator"), d.get("track")
    if not (artist and title):
        artist, title = split_video_title(d.get("title") or "", d.get("channel") or d.get("uploader") or "")
    return {"video_id": d.get("id"), "url": d.get("webpage_url") or url, "title": title, "artist": (artist or "").split(",")[0].strip(),
            "duration": d.get("duration"), "channel": d.get("channel")}


def youtube_search(artist: str, title: str, duration: float | None, n: int = 8) -> list[dict]:
    q = f"ytsearch{n}:{artist} {title} audio"
    d = json.loads(_run(_ytdlp_cmd() + ["--flat-playlist", "-J", q], 90))
    return [{"video_id": e.get("id"), "url": e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}",
             "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or "",
             "duration": e.get("duration")} for e in d.get("entries") or [] if e.get("id")]


BAD_WORDS = {"live": 30, "cover": 40, "karaoke": 60, "instrumental": 60, "remix": 25, "sped up": 60, "slowed": 60,
             "nightcore": 60, "8d": 50, "reaction": 60, "acoustic": 20, "tutorial": 60, "piano": 30, "lesson": 60,
             "extended": 20, "mashup": 50, "1 hour": 80, "10 hours": 80}


def score_video(v: dict, artist: str, title: str, duration: float | None) -> float:
    """Higher is a better stand-in for the released recording (the one LRCLIB's timings fit)."""
    t, ch, want = norm(v.get("title")), norm(v.get("channel")), norm(f"{artist} {title}")
    s = 0.0
    if duration and v.get("duration"):
        d = abs(v["duration"] - duration)
        s -= d * 2 + (100 if d > 15 else 0)
    for w, pen in BAD_WORDS.items():
        if re.search(rf"\b{re.escape(w)}\b", t) and not re.search(rf"\b{re.escape(w)}\b", want):
            s -= pen
    s += 8 if ch.endswith(" topic") else 0
    s += 5 if "audio" in t else 0
    s += 3 if "lyric" in t else 0
    s += 2 if "official" in t else 0
    s -= 4 if re.search(r"\b(music video|official video)\b", t) else 0
    s += 5 if norm(artist) and (norm(artist) in t or norm(artist) in ch) else 0
    s += 5 if norm(title) and norm(title) in t else 0
    return s


def best_video(artist: str, title: str, duration: float | None) -> dict:
    cands = youtube_search(artist, title, duration)
    if not cands:
        raise SourceError(f"Nothing on YouTube matched “{artist} – {title}”.")
    best = max(cands, key=lambda v: score_video(v, artist, title, duration))
    if duration and best.get("duration") and abs(best["duration"] - duration) > 20:
        raise SourceError(f"No YouTube recording of “{title}” was close to its {int(duration)} s length.")
    return best


def download_audio(url: str, out_dir: Path) -> Path:
    """The best audio of ``url`` as out_dir/source.<ext>."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("source.*"):
        f.unlink()
    _run(_ytdlp_cmd() + ["-f", "bestaudio/best", "-x", "-o", str(out_dir / "source.%(ext)s"), url], 600)
    got = [f for f in out_dir.glob("source.*") if f.suffix not in (".part", ".ytdl")]
    if not got:
        raise SourceError("The download finished but left no audio file.")
    return got[0]


# ---------------------------------------------------------------- titles
NOISE = re.compile(r"\s*[\(\[][^\)\]]*\b(official|video|audio|lyrics?|lyric video|hd|hq|4k|remaster(ed)?|visuali[sz]er|"
                   r"music video|clip officiel|explicit|clean)\b[^\)\]]*[\)\]]", re.I)


def _clean_title(t: str) -> str:
    t = NOISE.sub("", t)
    t = re.sub(r"_(copy|\d)$|\s+copy$", "", t, flags=re.I)  # LRCLIB uploads: "Dancing Queen_copy", "_2" (not "9 to 5", "Song 2")
    t = re.sub(r"^\d{1,2}\s*[-.]\s+", "", t)                 # '01 - Dancing Queen'
    return re.sub(r"\s{2,}", " ", t).strip(" -–—|")


def strip_artist(title: str, artist: str) -> str:
    """'ABBA - Dancing Queen' by ABBA -> 'Dancing Queen'; 'Dancing Queen - Abba' -> 'Dancing Queen'."""
    a = re.escape(artist.strip())
    if not a:
        return title
    t = re.sub(rf"^{a}\s*[-–—:|/]\s*", "", title, flags=re.I)
    t = re.sub(rf"\s*[-–—|]\s*{a}$", "", t, flags=re.I)
    return t.strip() or title


def split_video_title(title: str, channel: str) -> tuple[str, str]:
    """'ABBA - Dancing Queen (Official Music Video)' -> ('ABBA', 'Dancing Queen'); no dash -> the channel's name."""
    t = _clean_title(title)
    m = re.match(r"^(.+?)\s+[-–—|]\s+(.+)$", t)
    if m:
        return m.group(1).strip(), _clean_title(m.group(2))
    ch = re.sub(r"\s*(- topic|vevo|official)$", "", channel, flags=re.I).strip()
    return ch, t


def is_url(s: str) -> bool:
    return bool(re.match(r"^https?://", s.strip(), re.I))


def classify_url(url: str) -> str:
    if SPOTIFY_TRACK.search(url) or "open.spotify.com" in url:
        return "spotify"
    if re.search(r"(youtube\.com|youtu\.be)/", url):
        return "youtube"
    return "url"
