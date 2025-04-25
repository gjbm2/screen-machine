"""bucket_api.py – Flask blueprint for managing media buckets.

THIS IS THE **FINAL** VERSION REFERENCED BY app.register_blueprint(buckets_bp)
============================================================================

* Buckets live under **output/<bucket_name>/**.
* Currently-published asset is **output/<bucket_name>.<ext>** (jpg or mp4).
* Publishing uses routes.publisher.publish_local_file() – no duplicated logic.
* GET /published returns a base64 JPEG preview via utils.get_image_from_target().

Import example
--------------
```python
from routes.bucket_api import buckets_bp
app.register_blueprint(buckets_bp)
```
"""

from __future__ import annotations

import json, uuid, shutil
import base64
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from io import BytesIO
from PIL import Image, ExifTags
import subprocess
import cv2

from flask import Blueprint, abort, jsonify, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from routes.publisher import (
    publish_local_file,
    sidecar_path,
    ensure_sidecar_for,           # creates side-car if missing
    _extract_exif_json,           # internal helpers → ok to reuse
    _extract_mp4_comment_json,
    generate_thumbnail,           # new for thumbnail generation
)
from routes.utils import get_image_from_target
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

BASE_OUTPUT = Path("output").resolve()             # change to "/output" if needed
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}
ISO_NOW = lambda: datetime.utcnow().isoformat() + "Z"

# ----------------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------------

def bucket_path(name: str) -> Path:
    return BASE_OUTPUT / name


def meta_path(name: str) -> Path:
    return bucket_path(name) / "bucket.json"


def publish_path(bucket: str, filename: str) -> Path:
    return BASE_OUTPUT / f"{bucket}{Path(filename).suffix.lower()}"

# ----------------------------------------------------------------------------
# Metadata helpers
# ----------------------------------------------------------------------------

def load_meta(bucket: str) -> Dict[str, Any]:
    fp = meta_path(bucket)
    if fp.exists():
        return json.loads(fp.read_text("utf-8"))
    return {"sequence": [], "favorites": []}


def save_meta(bucket: str, meta: Dict[str, Any]):
    p = meta_path(bucket)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    

def infer_meta_from_file(file_path: Path) -> dict:
    """
    Extracts metadata from an image (EXIF) or video (OpenCV).
    Returns a dictionary suitable for JSON serialization.
    """
    if not file_path.exists():
        warning(f"[meta] File does not exist: {file_path}")
        return {}

    ext = file_path.suffix.lower()

    try:
        if ext in {".jpg", ".jpeg", ".png", ".webp"}:
            img = Image.open(file_path)
            meta = {
                "format": img.format,
                "mode": img.mode,
                "size": img.size,
            }

            exif = img._getexif()
            if exif:
                exif_data = {
                    ExifTags.TAGS.get(k, str(k)): (
                        v if not isinstance(v, bytes) else f"<{len(v)} bytes>"
                    )
                    for k, v in exif.items()
                    if isinstance(k, int) and k in ExifTags.TAGS
                }
                meta["exif"] = exif_data

            info(f"[meta] Inferred image metadata for {file_path.name}")
            return meta

        elif ext in {".mp4", ".mov", ".webm", ".mkv"}:
            cap = cv2.VideoCapture(str(file_path))
            if not cap.isOpened():
                warning(f"[meta] Could not open video file: {file_path}")
                return {}

            try:
                fps = cap.get(cv2.CAP_PROP_FPS)
                frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
                duration = frame_count / fps if fps > 0 else None
                width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
                height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)

                meta = {
                    "width": int(width),
                    "height": int(height),
                    "duration": round(duration, 2) if duration else None,
                    "fps": round(fps, 2),
                    "frame_count": int(frame_count),
                }

                info(f"[meta] Inferred video metadata for {file_path.name}")
                return meta

            finally:
                cap.release()

        else:
            warning(f"[meta] Unsupported extension: {ext}")
            return {}

    except Exception as e:
        warning(f"[meta] Failed to extract metadata from {file_path.name}: {e}")
        return {}

# ----------------------------------------------------------------------------
# Utility
# ----------------------------------------------------------------------------

def allowed_file(fname: str) -> bool:
    return Path(fname).suffix.lower() in ALLOWED_EXT


def unique_name(original: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{ts}-{uid}{Path(original).suffix.lower()}"

# ----------------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------------

buckets_bp = Blueprint("buckets", __name__, url_prefix="/api/buckets")

# -- list & create -----------------------------------------------------------

@buckets_bp.route("/", methods=["GET"])
def list_buckets():
    return jsonify([p.name for p in BASE_OUTPUT.iterdir() if p.is_dir()])


@buckets_bp.route("/", methods=["POST"])
def create_bucket():
    data = request.get_json(True) or {}
    name = data.get("bucket_name")
    if not name:
        abort(400, "bucket_name required")
    bucket_path(name).mkdir(parents=True, exist_ok=True)
    save_meta(name, {"sequence": [], "favorites": []})
    return jsonify({"status": "created", "bucket": name})

# -- Currently published

@buckets_bp.route("/<bucket>/published", methods=["GET"])
def get_published(bucket: str):
    """
    Returns JSON:
      {
        "published":    "<filename> or null",
        "published_at": "<ISO ts> or null",
        "raw_url":      "<URL> or null",
        "thumbnail":    "<base64 JPEG> or null",
        "meta":         {…} or {}
      }
    Always 200.
    """
    pm = load_meta(bucket).get("published_meta") or {}
    filename     = pm.get("filename")
    published_at = pm.get("published_at")

    # ─────────────────────────────────────────────────────────────
    # Case 1: No published_meta → fallback to inferred latest file
    # ─────────────────────────────────────────────────────────────
    if not filename:
        debug(f"[get_published] No published_meta — falling back to latest file")

        image_result = get_image_from_target(bucket, thumbnail=True)
        debug(f"[get_published] image_result keys: {list(image_result.keys()) if image_result else 'None'}")

        image_data = image_result.get("image") if image_result else None

        if not image_data:
            debug(f"[get_published] No image found for {bucket}")
            return jsonify({
                "published":     None,
                "published_at":  None,
                "raw_url":       None,
                "thumbnail":     None,
                "meta":          {},
            })

        raw_url    = image_result.get("raw_url")
        local_path = image_result.get("local_path")
        raw_name   = image_result.get("raw_name")

        try:
            meta_data = infer_meta_from_file(Path(local_path))
        except Exception as e:
            warning(f"Could not infer metadata from {local_path}: {e}")
            meta_data = {}

        return jsonify({
            "published":     raw_name,
            "published_at":  None,
            "raw_url":       raw_url,
            "thumbnail":     image_data,
            "meta":          meta_data,
        })

    # ─────────────────────────────────────────────────────────────
    # Case 2: published_meta exists → load and serve actual asset
    # ─────────────────────────────────────────────────────────────

    # Locate the actual asset (bucket-local file, or fallback screen asset)
    bucket_file = bucket_path(bucket) / filename
    if bucket_file.exists():
        asset_path = bucket_file
    else:
        ext = Path(filename).suffix
        screen_file = BASE_OUTPUT / f"{bucket}{ext}"
        asset_path = screen_file if screen_file.exists() else None

    if not asset_path:
        debug(f"[get_published] Published filename not found on disk")
        return jsonify({
            "published":     filename,
            "published_at":  published_at,
            "raw_url":       None,
            "thumbnail":     None,
            "meta":          pm,
        })

    # Ensure sidecar metadata exists
    ensure_sidecar_for(asset_path)

    # Ensure thumbnail exists
    thumb_dir  = bucket_path(bucket) / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_file = thumb_dir / f"{asset_path.stem}.jpg"
    if not thumb_file.exists():
        try:
            thumb_img = generate_thumbnail(asset_path)
            thumb_b64 = base64.b64encode(thumb_img.tobytes()).decode("ascii")
        except Exception as e:
            warning(f"Failed to generate inline thumbnail: {e}")
            thumb_b64 = None
    else:
        try:
            with open(thumb_file, "rb") as f:
                thumb_b64 = base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            warning(f"Failed to read thumbnail file: {e}")
            thumb_b64 = None

    debug(f"[get_published] thumbnail length: {len(thumb_b64) if thumb_b64 else 'None'}")

    # Build raw URL (served via Flask route)
    raw_url = url_for("buckets.raw", bucket=bucket, filename=asset_path.name, _external=True)

    return jsonify({
        "published":     asset_path.name,
        "published_at":  published_at,
        "raw_url":       raw_url,
        "thumbnail":     thumb_b64,
        "meta":          pm,
    })


# -- items -------------------------------------------------------------------

@buckets_bp.route("/<bucket>/items", methods=["GET"])
def list_items(bucket: str):
    return jsonify(load_meta(bucket))


@buckets_bp.route("/<bucket>/upload", methods=["POST"])
def upload(bucket: str):
    if "file" not in request.files:
        abort(400, "file field required")
    f = request.files["file"]
    if f.filename == "" or not allowed_file(f.filename):
        abort(400, "invalid file type")

    from routes.publisher import _append_to_bucket, warning

    # Save upload temporarily to disk
    temp_dir = Path("/tmp/uploads")
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / secure_filename(unique_name(f.filename))
    f.save(temp_path)

    # Ensure file was written
    if not temp_path.exists() or temp_path.stat().st_size == 0:
        abort(500, "Uploaded file failed to save")

    # Append to bucket (handles filename uniqueness, sidecar copy, thumbnail)
    target_path = _append_to_bucket(bucket, temp_path)

    # Ensure sidecar metadata exists
    ensure_sidecar_for(target_path)

    # If metadata is still missing, infer and inject it into the sidecar
    sidecar_file = target_path.with_suffix(target_path.suffix + ".json")
    if not sidecar_file.exists() or not sidecar_file.read_text("utf-8").strip():
        try:
            inferred = infer_meta_from_file(target_path)
            if inferred:
                sidecar_file.write_text(json.dumps(inferred, indent=2), encoding="utf-8")
        except Exception as e:
            warning(f"Metadata inference failed for {target_path.name}: {e}")

    # Confirm thumbnail was generated
    thumb_dir = bucket_path(bucket) / "thumbnails"
    thumb_path = thumb_dir / f"{target_path.stem}.jpg"
    thumbnail_exists = thumb_path.exists()

    debug(f"[upload] Stored: {target_path.name}, thumb: {'yes' if thumbnail_exists else 'no'}")

    debug(f"[upload] upload complete: {target_path}")
    debug(f"[upload] thumbnail exists: {(bucket_path(bucket) / 'thumbnails' / (target_path.stem + '.jpg')).exists()}")
    debug(f"[upload] sidecar exists: {target_path.with_suffix(target_path.suffix + '.json').exists()}")


    return jsonify({
        "status": "stored",
        "filename": target_path.name,
        "thumbnail": "ok" if thumbnail_exists else "missing",
        "meta": json.loads(sidecar_file.read_text("utf-8")) if sidecar_file.exists() else {}
    })

# -- publish -----------------------------------------------------------------

@buckets_bp.route("/<bucket>/publish/<filename>", methods=["POST"])
def publish(bucket: str, filename: str):
    src = bucket_path(bucket) / filename
    if not src.exists():
        abort(404, "file not in bucket")

    res = publish_local_file(src, bucket)
    if not res["success"]:
        abort(500, res.get("error", "publish failed"))

    meta = load_meta(bucket)
    meta["published_meta"] = res["meta"]
    save_meta(bucket, meta)
    return jsonify(res)

# Currently displayed

# Serve the 256×256 thumbnail for a given media filename
@buckets_bp.route("/<bucket>/thumbnail/<filename>")
def thumbnail(bucket: str, filename: str):
    thumb_dir = bucket_path(bucket) / "thumbnails"
    thumb_name = f"{Path(filename).stem}.jpg"
    return send_from_directory(thumb_dir, thumb_name)

# Provide current publish info (filename, when, raw URL, thumbnail URL)
@buckets_bp.route("/<bucket>/info", methods=["GET"])
def published_info(bucket: str):
    pm = load_meta(bucket).get("published_meta")
    if not pm:
        abort(404, "nothing published yet")
    filename    = pm["filename"]
    published_at= pm["published_at"]
    raw_url     = url_for("buckets.raw", bucket=bucket, filename=filename, _external=True)
    thumb_url   = url_for("buckets.thumbnail", bucket=bucket, filename=filename, _external=True)
    return jsonify({
        "filename": filename,
        "published_at": published_at,
        "raw_url": raw_url,
        "thumbnail_url": thumb_url,
    })

# -- favourites --------------------------------------------------------------

@buckets_bp.route("/<bucket>/favorite/<filename>", methods=["POST"])
def favorite(bucket: str, filename: str):
    meta = load_meta(bucket)
    if filename not in meta.get("sequence", []):
        abort(404, "not in bucket")
    meta.setdefault("favorites", []).append(filename)
    save_meta(bucket, meta)
    return jsonify({"status": "favorited"})


@buckets_bp.route("/<bucket>/favorite/<filename>", methods=["DELETE"])
def unfavorite(bucket: str, filename: str):
    meta = load_meta(bucket)
    if filename in meta.get("favorites", []):
        meta["favorites"].remove(filename)
        save_meta(bucket, meta)
    return jsonify({"status": "unfavorited"})

# -- delete ------------------------------------------------------------------

@buckets_bp.route("/<bucket>/<filename>", methods=["DELETE"])
def delete(bucket: str, filename: str):
    fp = bucket_path(bucket) / filename
    if not fp.exists():
        abort(404, "file missing")
    fp.unlink()

    # remove side-car
    sc = sidecar_path(fp)
    sc.unlink(missing_ok=True)

    # remove thumbnail
    thumb_fp = bucket_path(bucket) / "thumbnails" / f"{Path(filename).stem}.jpg"
    thumb_fp.unlink(missing_ok=True)

    meta = load_meta(bucket)
    for key in ("sequence", "favorites"):
        if filename in meta.get(key, []):
            meta[key].remove(filename)
    save_meta(bucket, meta)

    return jsonify({"status": "deleted"})

# -- move / copy -------------------------------------------------------------

@buckets_bp.route("/move", methods=["POST"])
def move():
    d = request.get_json(True) or {}
    src, dst, fname = d.get("source_bucket"), d.get("dest_bucket"), d.get("filename")
    copy = bool(d.get("copy", False))
    if not all([src, dst, fname]):
        abort(400, "source_bucket, dest_bucket, filename required")

    spath = bucket_path(src) / fname
    if not spath.exists():
        abort(404, "source file not found")

    dpath = bucket_path(dst) / fname
    dpath.parent.mkdir(parents=True, exist_ok=True)
    if dpath.exists():
        fname = unique_name(fname)
        dpath = bucket_path(dst) / fname
    shutil.copy2(spath, dpath)

    # copy side-car
    sc_src, sc_dst = sidecar_path(spath), sidecar_path(dpath)
    if sc_src.exists():
        shutil.copy2(sc_src, sc_dst)

    # generate thumbnail in destination bucket
    thumb_dir = bucket_path(dst) / 'thumbnails'
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / (dpath.stem + '.jpg')
    generate_thumbnail(dpath, thumb_path)

    dmeta = load_meta(dst)
    dmeta.setdefault("sequence", []).append(fname)
    save_meta(dst, dmeta)

    if not copy:
        delete(src, fname)
    return jsonify({"status": "copied" if copy else "moved", "filename": fname})

# -- sequence reorder --------------------------------------------------------

@buckets_bp.route("/<bucket>/move-up/<filename>", methods=["POST"])
def move_up(bucket: str, filename: str):
    meta = load_meta(bucket)
    seq = meta.get("sequence", [])
    if filename not in seq:
        abort(404, "file not in sequence")
    i = seq.index(filename)
    if i == 0:
        seq.append(seq.pop(0))
    else:
        seq[i - 1], seq[i] = seq[i], seq[i - 1]
    meta["sequence"] = seq
    save_meta(bucket, meta)
    return jsonify({"status": "moved-up", "index": seq.index(filename)})


@buckets_bp.route("/<bucket>/move-down/<filename>", methods=["POST"])
def move_down(bucket: str, filename: str):
    meta = load_meta(bucket)
    seq = meta.get("sequence", [])
    if filename not in seq:
        abort(404, "file not in sequence")
    i = seq.index(filename)
    if i == len(seq) - 1:
        seq.insert(0, seq.pop())
    else:
        seq[i + 1], seq[i] = seq[i], seq[i + 1]
    meta["sequence"] = seq
    save_meta(bucket, meta)
    return jsonify({"status": "moved-down", "index": seq.index(filename)})

# -- raw file helper ---------------------------------------------------------

@buckets_bp.route("/<bucket>/raw/<path:filename>")
def raw(bucket: str, filename: str):
    return send_from_directory(bucket_path(bucket), filename)

# ───────────────────────────── bucket maintenance ───────────────────────────

@buckets_bp.route("/<bucket>/purge", methods=["DELETE"])
def purge_bucket(bucket: str):
    """
    Delete every file in the bucket that is NOT marked favourite.
    Also removes matching side-cars and updates bucket.json,
    and deletes any thumbnails for files no longer in sequence.
    """
    info(f"[purge] running purge for {bucket}")
    meta = load_meta(bucket)
    favs = set(meta.get("favorites", []))
    seq  = meta.get("sequence", [])

    removed = []
    # 1️⃣ remove non-favourite media + side-cars + matching thumbs
    for fname in list(seq):  # iterate on a copy
        if fname in favs:
            continue
        fp = bucket_path(bucket) / fname
        fp.unlink(missing_ok=True)
        sidecar_path(fp).unlink(missing_ok=True)
        # remove its thumbnail
        thumb_fp = bucket_path(bucket) / "thumbnails" / f"{Path(fname).stem}.jpg"
        thumb_fp.unlink(missing_ok=True)
        seq.remove(fname)
        removed.append(fname)

    # 2️⃣ delete any orphaned thumbnails
    thumb_dir = bucket_path(bucket) / "thumbnails"
    if thumb_dir.exists():
        for thumb in thumb_dir.iterdir():
            if thumb.suffix.lower() == ".jpg":
                stem = thumb.stem
                # if no media file with this stem remains in seq, delete the thumb
                if stem + Path(thumb.name).suffix not in seq and stem not in [Path(f).stem for f in seq]:
                    thumb.unlink(missing_ok=True)

    # 3️⃣ persist updated sequence
    save_meta(bucket, {**meta, "sequence": seq})
    return jsonify({"status": "purged", "removed": removed})

@buckets_bp.route("/<bucket>/reindex", methods=["POST"])
def reindex_bucket(bucket: str):
    """
    Rebuild sequence[] from the files actually present on disk.
    Keeps favourites that still exist and re-generates thumbnails.
    """
    media_files = sorted(
        p.name for p in bucket_path(bucket).iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_EXT
    )
    # rebuild meta
    meta   = load_meta(bucket)
    favs   = [f for f in meta.get("favorites", []) if f in media_files]
    newmet = {"sequence": media_files, "favorites": favs}
    save_meta(bucket, newmet)

    # regenerate thumbnails for all
    thumb_dir = bucket_path(bucket) / 'thumbnails'
    thumb_dir.mkdir(parents=True, exist_ok=True)
    for fname in media_files:
        src_fp = bucket_path(bucket) / fname
        thumb_path = thumb_dir / (Path(fname).stem + '.jpg')
        generate_thumbnail(src_fp, thumb_path)

    return jsonify({"status": "reindexed", "count": len(media_files)})

@buckets_bp.route("/<bucket>/extractjson", methods=["POST"])
def extract_json(bucket: str):
    """
    Ensure every media file has a side-car.
    If one exists, merge in any new keys found in EXIF / ffprobe.
    Also generate thumbnails retroactively.
    """

    updated = []
    media_dir = bucket_path(bucket)
    thumb_dir = media_dir / 'thumbnails'
    thumb_dir.mkdir(parents=True, exist_ok=True)

    for fp in media_dir.iterdir():
        if not fp.is_file() or fp.suffix.lower() not in ALLOWED_EXT:
            continue

        sc = sidecar_path(fp)
        old = {}
        if sc.exists():
            try:
                old = json.loads(sc.read_text("utf-8"))
            except Exception as e:
                warning(f"[extract] Failed to parse existing sidecar for {fp.name}: {e}")

        # extract fresh metadata using universal inference
        new = infer_meta_from_file(fp)

        if new:
            merged = {**old, **new}
            try:
                sc.write_text(json.dumps(merged, indent=2), encoding="utf-8")
                updated.append(fp.name)
            except Exception as e:
                warning(f"[extract] Failed to write merged sidecar for {fp.name}: {e}")
        else:
            try:
                ensure_sidecar_for(fp)
            except Exception as e:
                warning(f"[extract] Failed to create fallback sidecar for {fp.name}: {e}")

        # generate thumbnail (overwrite or create new)
        thumb_path = thumb_dir / f"{fp.stem}.jpg"
        try:
            generate_thumbnail(fp, thumb_path)
        except Exception as e:
            warning(f"[extract] Failed to generate thumbnail for {fp.name}: {e}")

    return jsonify({"status": "extractjson-complete", "updated": updated})