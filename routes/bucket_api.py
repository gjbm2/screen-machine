"""bucket_api.py – Flask blueprint for managing media buckets.

THIS IS THE VERSION REFERENCED BY app.register_blueprint(buckets_bp)
============================================================================

* Buckets live under **output/<bucket_name>/**.
* Currently-published asset is **output/<bucket_name>.<ext>** (jpg or mp4).
* Publishing uses routes.publisher.publish_to_destination() – no duplicated logic.
* GET /published returns a base64 JPEG preview via utils.get_image_from_target().

Import example
--------------
```python
from routes.bucket_api import buckets_bp
app.register_blueprint(buckets_bp)
```
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, request, send_from_directory, url_for
from werkzeug.utils import secure_filename
import json
import base64     # to send encoded thumbnails
from pathlib import Path

from routes.publisher import publish_to_destination
      # new for thumbnail generation

from routes.bucketer import (
    reindex_bucket,
    purge_bucket,
    extract_metadata,
    bucket_path,
    load_meta,
    save_meta,
    infer_meta_from_file,
    allowed_file,
    unique_name,
    extract_json,
    copy_image_from_bucket_to_bucket
)
from routes.utils import (
    get_image_from_target,
    _load_json_once,
    _extract_exif_json,           # internal helpers → ok to reuse
    _extract_mp4_comment_json,
    sidecar_path,
    ensure_sidecar_for,
    generate_thumbnail
)
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# ----------------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------------

buckets_bp = Blueprint("buckets", __name__, url_prefix="/api/buckets")

# -- list & create -----------------------------------------------------------

@buckets_bp.route("/", methods=["GET"])
def list_buckets():
    """List all buckets (only destinations with has_bucket=true)"""
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    #return jsonify([d["id"] for d in dests])
    return jsonify([d["id"] for d in dests if d.get("has_bucket")])

@buckets_bp.route("/", methods=["POST"])
def create_bucket():
    """Create a new bucket (only for destinations with has_bucket=true)"""
    data = request.get_json()
    bucket_id = data.get("bucket_id")
    if not bucket_id:
        abort(400, "bucket_id required")
    
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    bucket_path(bucket_id).mkdir(parents=True, exist_ok=True)
    return jsonify({"status": "created", "bucket_id": bucket_id})

# -- items -------------------------------------------------------------------

@buckets_bp.route("/<bucket_id>/items", methods=["GET"])
def list_items(bucket_id: str):
    """List items in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    bucket_meta = load_meta(bucket_id)
    
    # Get the paths for thumbnails
    thumb_dir = bucket_path(bucket_id) / "thumbnails"
    
    # Enhance response with embedded thumbnails
    if "sequence" in bucket_meta:
        items_with_thumbnails = []
        for filename in bucket_meta["sequence"]:
            # Get metadata from sidecar if it exists
            file_path = bucket_path(bucket_id) / filename
            metadata = {}
            sidecar = sidecar_path(file_path)
            if sidecar.exists():
                try:
                    metadata = json.loads(sidecar.read_text("utf-8"))
                except Exception as e:
                    warning(f"Failed to read sidecar for {filename}: {e}")
            
            # Create item with metadata and favorite status
            item = {
                "filename": filename,
                "metadata": metadata,
                "favorite": filename in bucket_meta.get("favorites", []),
                "thumbnail_url": url_for("buckets.thumbnail", bucket_id=bucket_id, filename=filename)
            }
            
            # Try to load and embed the thumbnail if needed
            thumb_path = thumb_dir / f"{Path(filename).stem}{Path(filename).suffix}.jpg"
            if thumb_path.exists():
                try:
                    with open(thumb_path, "rb") as f:
                        thumb_data = f.read()
                        thumb_b64 = base64.b64encode(thumb_data).decode("ascii")
                        item["thumbnail_embedded"] = thumb_b64
                except Exception as e:
                    warning(f"Failed to read thumbnail for {filename}: {e}")
            
            items_with_thumbnails.append(item)
        
        # Add the enhanced items to the response
        bucket_meta["items_with_thumbnails"] = items_with_thumbnails
    
    return jsonify(bucket_meta)

@buckets_bp.route("/<bucket_id>/upload", methods=["POST"])
def upload(bucket_id: str):
    """Upload a file to a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
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
    target_path = _append_to_bucket(bucket_id, temp_path)

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
    thumb_dir = bucket_path(bucket_id) / "thumbnails"
    thumb_path = thumb_dir / f"{target_path.stem}{target_path.suffix}.jpg"
    try:
        generate_thumbnail(target_path, thumb_path)
        debug(f"[upload] Generated thumbnail: {thumb_path}")
    except Exception as e:
        error(f"[upload] Failed to generate thumbnail: {str(e)}")
        raise

    thumbnail_exists = thumb_path.exists()

    debug(f"[upload] Stored: {target_path.name}, thumb: {'yes' if thumbnail_exists else 'no'}")

    debug(f"[upload] upload complete: {target_path}")
    debug(f"[upload] thumbnail exists: {(bucket_path(bucket_id) / 'thumbnails' / (target_path.stem + '.jpg')).exists()}")
    debug(f"[upload] sidecar exists: {target_path.with_suffix(target_path.suffix + '.json').exists()}")

    return jsonify({
        "status": "stored",
        "filename": target_path.name,
        "thumbnail": "ok" if thumbnail_exists else "missing",
        "meta": json.loads(sidecar_file.read_text("utf-8")) if sidecar_file.exists() else {}
    })

# -- publish -----------------------------------------------------------------

@buckets_bp.route("/<bucket_id>/publish/<filename>", methods=["POST"])
def publish(bucket_id: str, filename: str):
    """Publish a file from a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    src = bucket_path(bucket_id) / filename
    if not src.exists():
        abort(404, "file not in bucket")

    res = publish_to_destination(
        source=src,
        publish_destination_id=bucket_id,
        skip_bucket=True  # Skip bucket append since this is already in a bucket
    )
    if not res["success"]:
        abort(500, res.get("error", "publish failed"))

    meta = load_meta(bucket_id)
    meta["published_meta"] = res["meta"]
    save_meta(bucket_id, meta)
    return jsonify(res)

# Currently displayed

# Serve the 256×256 thumbnail for a given media filename
@buckets_bp.route("/<bucket_id>/thumbnail/<filename>")
def thumbnail(bucket_id: str, filename: str):
    """Get thumbnail for a file in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    thumb_dir = bucket_path(bucket_id) / "thumbnails"
    thumb_name = f"{Path(filename).stem}{Path(filename).suffix}.jpg"
    return send_from_directory(thumb_dir, thumb_name)

# Provide current publish info (filename, when, raw URL, thumbnail URL)
@buckets_bp.route("/<bucket_id>/info", methods=["GET"])
def published_info(bucket_id: str):
    """Get published info for a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    pm = load_meta(bucket_id).get("published_meta")
    if not pm:
        abort(404, "nothing published yet")
    filename    = pm["filename"]
    published_at= pm["published_at"]
    raw_url     = url_for("buckets.raw", bucket_id=bucket_id, filename=filename)
    thumb_url   = url_for("buckets.thumbnail", bucket_id=bucket_id, filename=filename)
    return jsonify({
        "filename": filename,
        "published_at": published_at,
        "raw_url": raw_url,
        "thumbnail_url": thumb_url,
    })

# -- favourites --------------------------------------------------------------

@buckets_bp.route("/<bucket_id>/favorite/<filename>", methods=["POST"])
def favorite(bucket_id: str, filename: str):
    """Favorite a file in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    if filename not in meta.get("sequence", []):
        abort(404, "not in bucket")
    meta.setdefault("favorites", []).append(filename)
    save_meta(bucket_id, meta)
    return jsonify({"status": "favorited"})


@buckets_bp.route("/<bucket_id>/favorite/<filename>", methods=["DELETE"])
def unfavorite(bucket_id: str, filename: str):
    """Unfavorite a file in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    if filename in meta.get("favorites", []):
        meta["favorites"].remove(filename)
        save_meta(bucket_id, meta)
    return jsonify({"status": "unfavorited"})

# -- delete ------------------------------------------------------------------

@buckets_bp.route("/<bucket_id>/<filename>", methods=["DELETE"])
def delete_file(bucket_id: str, filename: str):
    """Delete a file from a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    fp = bucket_path(bucket_id) / filename
    if not fp.exists():
        abort(404, "file missing")
    fp.unlink()

    # remove side-car
    sc = sidecar_path(fp)
    sc.unlink(missing_ok=True)

    # remove thumbnail
    thumb_fp = bucket_path(bucket_id) / "thumbnails" / f"{Path(filename).stem}{Path(filename).suffix}.jpg"
    thumb_fp.unlink(missing_ok=True)

    meta = load_meta(bucket_id)
    for key in ("sequence", "favorites"):
        if filename in meta.get(key, []):
            meta[key].remove(filename)
    save_meta(bucket_id, meta)

    return jsonify({"status": "deleted"})

# -- move / copy -------------------------------------------------------------

@buckets_bp.route("/add_image_to_new_bucket", methods=["POST"])
def add_image_to_new_bucket():
    """Add an image to a new bucket"""
    data = request.get_json()
    source_publish_destination = data.get("source_publish_destination")
    target_publish_destination = data.get("target_publish_destination")
    filename = data.get("filename")
    
    if not all([source_publish_destination, target_publish_destination, filename]):
        abort(400, "Missing required parameters")
    
    try:
        result = copy_image_from_bucket_to_bucket(
            source_publish_destination=source_publish_destination,
            target_publish_destination=target_publish_destination,
            filename=filename,
            copy=data.get("copy", True)  # Default to copy=True
        )
        return jsonify(result)
    except ValueError as e:
        abort(400, str(e))
    except FileNotFoundError as e:
        abort(404, str(e))
    except Exception as e:
        error(f"[add_image_to_new_bucket] Failed: {str(e)}")
        abort(500, str(e))

# -- sequence reorder --------------------------------------------------------

@buckets_bp.route("/<bucket_id>/move-to/<filename>", methods=["POST"])
def move_to(bucket_id: str, filename: str):
    """Move a file to a specific position in the sequence, after the specified file"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    data = request.get_json()
    insert_after = data.get("insert_after")
    
    meta = load_meta(bucket_id)
    seq = meta.get("sequence", [])
    
    # Verify the file exists in the sequence
    if filename not in seq:
        abort(404, "file not in sequence")
    
    # Remove the file from its current position
    seq.remove(filename)
    
    if insert_after:
        # Verify insert_after file exists in sequence
        if insert_after not in seq:
            abort(404, "insert_after file not in sequence")
        # Find the index to insert after
        insert_index = seq.index(insert_after) + 1
    else:
        # Move to top
        insert_index = 0
    
    # Insert the file at the new position
    seq.insert(insert_index, filename)
    
    # Update the metadata
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    
    return jsonify({
        "status": "moved",
        "index": insert_index,
        "filename": filename,
        "insert_after": insert_after
    })

@buckets_bp.route("/<bucket_id>/move-up/<filename>", methods=["POST"])
def move_up(bucket_id: str, filename: str):
    """Move a file up in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    seq = meta.get("sequence", [])
    if filename not in seq:
        abort(404, "file not in sequence")
    i = seq.index(filename)
    if i == 0:
        seq.append(seq.pop(0))
    else:
        seq[i - 1], seq[i] = seq[i], seq[i - 1]
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    return jsonify({"status": "moved-up", "index": seq.index(filename)})


@buckets_bp.route("/<bucket_id>/move-down/<filename>", methods=["POST"])
def move_down(bucket_id: str, filename: str):
    """Move a file down in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    seq = meta.get("sequence", [])
    if filename not in seq:
        abort(404, "file not in sequence")
    i = seq.index(filename)
    if i == len(seq) - 1:
        seq.insert(0, seq.pop())
    else:
        seq[i + 1], seq[i] = seq[i], seq[i + 1]
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    return jsonify({"status": "moved-down", "index": seq.index(filename)})

# -- raw file helper ---------------------------------------------------------

@buckets_bp.route("/<bucket_id>/raw/<path:filename>")
def raw(bucket_id: str, filename: str):
    """Get raw file from a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    file_path = bucket_path(bucket_id) / filename
    return send_from_directory(bucket_path(bucket_id), filename)

# ───────────────────────────── bucket maintenance ───────────────────────────

@buckets_bp.route("/<bucket_id>/purge", methods=["DELETE"])
def purge_bucket_endpoint(bucket_id: str):
    """Purge files from a bucket, optionally including favorites."""
    include_favorites = request.args.get("include_favorites", "false").lower() == "true"
    
    result = purge_bucket(bucket_id, include_favorites=include_favorites)
    return jsonify(result)

@buckets_bp.route("/reindex", methods=["POST"])
def reindex_all():
    """Reindex all buckets, optionally rebuilding metadata and thumbnails."""
    rebuild_all_sidecars = request.args.get("rebuild_all_sidecars", "false").lower() == "true"
    rebuild_all_thumbs = request.args.get("rebuild_all_thumbs", "false").lower() == "true"
    
    result = reindex_bucket(
        rebuild_all_sidecars=rebuild_all_sidecars,
        rebuild_all_thumbs=rebuild_all_thumbs
    )
    return jsonify(result)

@buckets_bp.route("/<bucket_id>/reindex", methods=["POST"])
def reindex_single(bucket_id: str):
    """Reindex a single bucket, optionally rebuilding metadata and thumbnails."""
    rebuild_all_sidecars = request.args.get("rebuild_all_sidecars", "false").lower() == "true"
    rebuild_all_thumbs = request.args.get("rebuild_all_thumbs", "false").lower() == "true"
    
    result = reindex_bucket(
        publish_destination_id=bucket_id,
        rebuild_all_sidecars=rebuild_all_sidecars,
        rebuild_all_thumbs=rebuild_all_thumbs
    )
    return jsonify(result)

@buckets_bp.route("/<bucket_id>/extractjson", methods=["POST"])
def extract_json(bucket_id: str):
    """Extract JSON metadata from all files in bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    result = extract_json(bucket_id)
    return jsonify(result)

@buckets_bp.route("/<bucket_id>/complete", methods=["GET"])
def get_bucket_complete(bucket_id: str):
    """Get complete bucket info"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    # Get bucket metadata
    meta = load_meta(bucket_id)
    
    # Get all files in the bucket
    bucket_dir = bucket_path(bucket_id)
    files = []
    
    # Only show files that are in the sequence list
    for filename in meta.get("sequence", []):
        file_path = bucket_dir / filename
        if not file_path.exists():
            continue

        # Get file metadata from sidecar if it exists
        sidecar = sidecar_path(file_path)
        file_meta = {}
        if sidecar.exists():
            try:
                file_meta = json.loads(sidecar.read_text("utf-8"))
            except Exception as e:
                warning(f"Failed to read sidecar for {file_path.name}: {e}")
        
        # Add file info to list
        files.append({
            "filename": file_path.name,
            "size": file_path.stat().st_size,
            "modified": file_path.stat().st_mtime,
            "metadata": file_meta,
            "favorite": file_path.name in meta.get("favorites", []),
            "sequence_index": meta.get("sequence", []).index(file_path.name),
            "thumbnail_url": url_for("buckets.thumbnail", bucket_id=bucket_id, filename=file_path.name)
        })
    
    # Sort files by sequence
    sequence_map = {f: i for i, f in enumerate(meta.get("sequence", []))}
    files.sort(key=lambda f: sequence_map.get(f["filename"], float("inf")))

    # Get published info if available
    published = None
    if "published_meta" in meta:
        pm = meta["published_meta"]
        published = {
            "filename": pm["filename"],
            "published_at": pm["published_at"],
            "raw_url": url_for("buckets.raw", bucket_id=bucket_id, filename=pm["filename"]),
            "thumbnail_url": url_for("buckets.thumbnail", bucket_id=bucket_id, filename=pm["filename"])
        }

    return jsonify({
        "bucket_id": bucket_id,
        "files": files,
        "published": published
    })
