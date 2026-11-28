"""karaoke.singer_photos: a singer's own photo (owner, 28 Nov: "possible optionally for singers to upload photo (e.g.
from phone camera), exposed in the singer creation flow by e.g. clicking their lozenge. then we can show those on the
on-screen 'up next' etc. overlays, and also inject the current singer into the photostream for the vj machinery on
photo scenes").

One photo per name (tonight's names: store.singers), kept in ``<karaoke state>/singers/``: the JPEG (upright, longest
side SAVE_PX, the file name carrying a version so a changed photo is a new URL) and ``index.json`` {lower name: {name,
file, w, h, at, faces}}. ``faces`` are found after the upload by the pictures' vetting AI (when it can be asked), so
the photo scenes frame the singer's face as they do an artist's; until then the scenes frame the middle.

A photo goes with its name: removing the name from tonight's (POST /karaoke/singers remove) or a new session drops it.
Never in git, never a screen's bucket.

  POST   /api/karaoke/singers/photo           multipart {name, photo}: set (or replace) that name's photo -> {photos}
  DELETE /api/karaoke/singers/photo?name=..   drop it -> {photos}
  GET    /api/karaoke/singers/photo/<file>    the JPEG
"""
from __future__ import annotations

import io
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from flask import abort, jsonify, request, send_file
from utils.logger import error, info

from . import api, singer_riffs
from .api import _err, karaoke_api
from .store import clean_singer, root

SAVE_PX = 1600            # the photo's longest side as kept
MAX_UPLOAD = 15 * 1024 * 1024
_lock = threading.Lock()


def folder() -> Path:
    return root() / "singers"


def _index() -> dict:
    try:
        d = json.loads((folder() / "index.json").read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(idx: dict) -> None:
    f = folder()
    f.mkdir(parents=True, exist_ok=True)
    tmp = f / "index.json.tmp"
    tmp.write_text(json.dumps(idx, indent=1))
    tmp.replace(f / "index.json")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "singer"


def url(row: dict) -> str:
    return f"/api/karaoke/singers/photo/{row['file']}"


def photos() -> dict[str, str]:
    """Every name with a photo -> its URL (the panel's chips)."""
    return {r["name"]: url(r) for r in _index().values() if (folder() / r["file"]).is_file()}


def riffs() -> dict[str, list[dict]]:
    """Every name with riffs planned -> [{scene, label, state, url (made ones)}] (the chip's gallery)."""
    out = {}
    for r in _index().values():
        rows = []
        for x in r.get("riffs") or []:
            made = x.get("state") == "done" and (folder() / x["file"]).is_file()
            rows.append({"scene": x["scene"], "label": singer_riffs.LABELS.get(x["scene"], x["scene"]),
                         "state": "done" if made else x.get("state"),
                         **({"url": f"/api/karaoke/singers/photo/{x['file']}"} if made else {})})
        if rows:
            out[r["name"]] = rows
    return out


def photos_of(names: Iterable[Any]) -> dict[str, str]:
    """The photo URLs of these names (the screens' overlays): {name: url}, names without one left out."""
    idx = _index()
    out = {}
    for n in names or []:
        r = idx.get(str(n).lower())
        if r and (folder() / r["file"]).is_file():
            out[str(n)] = url(r)
    return out


def pictures_of(names: Iterable[Any]) -> list[dict]:
    """The renderer's rows for the singers on now (stream_service lyric_feed: put first among the song's pictures)."""
    idx = _index()
    out = []
    for n in names or []:
        r = idx.get(str(n).lower())
        path = folder() / r["file"] if r else None
        if r and path.is_file():
            out.append({"id": f"singer/{r['file']}", "path": str(path), "w": r["w"], "h": r["h"], "kind": "singer",
                        "quality": 0.9, "appeal": 1.0, "faces": r.get("faces") or [], "name": r["name"]})
            for x in r.get("riffs") or []:                  # the AI's riffs on it (singer_riffs), as they are made
                if x.get("state") == "done" and (folder() / x["file"]).is_file():
                    out.append({"id": f"singer/{x['file']}", "path": str(folder() / x["file"]), "w": x["w"],
                                "h": x["h"], "kind": "singer", "quality": 0.9, "appeal": 0.95, "faces": [],
                                "name": r["name"], "riff": x["scene"]})
    return out


def _files(row: dict) -> list[str]:
    """A row's photo and its made riffs."""
    return [row["file"], *(x["file"] for x in row.get("riffs") or [] if x.get("file"))]


def put(name: str, data: bytes) -> dict:
    """Keep ``data`` (any image the phone sends) as ``name``'s photo, upright and at most SAVE_PX. -> the row."""
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")      # a phone's photo is often stored on its side
    im.thumbnail((SAVE_PX, SAVE_PX))
    f = folder()
    f.mkdir(parents=True, exist_ok=True)
    file = f"{_slug(name)}-{int(time.time() * 1000)}.jpg"
    im.save(f / file, "JPEG", quality=88)
    with _lock:
        idx = _index()
        old = idx.get(name.lower())
        row = {"name": name, "file": file, "w": im.width, "h": im.height, "at": time.time(), "faces": [],
               "riffs": singer_riffs.plan()}
        idx[name.lower()] = row
        _write(idx)
    for x in _files(old) if old else []:
        (f / x).unlink(missing_ok=True)
    singer_riffs.poke()
    threading.Thread(target=_find_faces, args=(name.lower(), file), name="karaoke-singer-faces", daemon=True).start()
    return row


def drop(names: Iterable[Any]) -> None:
    """Forget these names' photos (files and all)."""
    gone = []
    with _lock:
        idx = _index()
        for n in names or []:
            r = idx.pop(str(n).lower(), None)
            if r:
                gone += _files(r)
        if gone:
            _write(idx)
    for file in gone:
        (folder() / file).unlink(missing_ok=True)


def clear() -> None:
    """A new session: every photo goes."""
    with _lock:
        idx = _index()
        if idx:
            _write({})
    for r in idx.values():
        for x in _files(r):
            (folder() / x).unlink(missing_ok=True)


def _find_faces(key: str, file: str) -> None:
    """Ask the pictures' vetting AI where the faces are (it reports them for every picture it looks at)."""
    from . import pictures
    if not pictures.ai_ready():
        return
    try:
        v = pictures.vet(folder() / file, "a guest at the party (not a famous artist)", {"source": "a guest's phone"})
    except Exception as e:                              # noqa: BLE001 - a photo without faces is still a photo
        error(f"karaoke singer photo: finding faces: {e}")
        return
    if not v:
        return
    with _lock:
        idx = _index()
        r = idx.get(key)
        if r and r["file"] == file:
            r["faces"] = v.get("faces") or []
            _write(idx)
    info(f"karaoke singer photo: {file}: {len(v.get('faces') or [])} faces")


@karaoke_api.post("/karaoke/singers/photo")
def api_put_photo():
    name = clean_singer(request.form.get("name"))
    f = request.files.get("photo")
    if not name or f is None:
        return _err(400, "name and photo are needed.")
    data = f.read(MAX_UPLOAD + 1)
    if not data or len(data) > MAX_UPLOAD:
        return _err(400, "That photo is too big.")
    try:
        row = put(name, data)
    except Exception as e:                              # noqa: BLE001 - not an image PIL can read
        return _err(400, f"That isn't a photo this can read ({type(e).__name__}).")
    api.store().roster(add=[row["name"]])                   # a photo puts the name among tonight's
    return jsonify({"photos": photos()})


@karaoke_api.delete("/karaoke/singers/photo")
def api_drop_photo():
    drop([request.args.get("name") or ""])
    return jsonify({"photos": photos()})


@karaoke_api.get("/karaoke/singers/photo/<file>")
def api_photo(file: str):
    if not re.fullmatch(r"[a-z0-9-]+-\d+(-[a-z]+)?\.jpg", file) or not (folder() / file).is_file():
        abort(404)
    return send_file(folder() / file, mimetype="image/jpeg", max_age=86400 * 30)
