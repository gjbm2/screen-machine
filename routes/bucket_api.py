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
    generate_thumbnail,
    seq_to_filenames,             # Add this new import
    upsert_seq                    # Add this new import
)
from utils.logger import log_to_console, info, error, warning, debug, console_logs

# ----------------------------------------------------------------------------
# Blueprint
# ----------------------------------------------------------------------------

buckets_bp = Blueprint("buckets", __name__)

# -- list & create -----------------------------------------------------------

@buckets_bp.route("/buckets/", methods=["GET"])
def list_buckets():
    """List all buckets (only destinations with has_bucket=true)"""
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    #return jsonify([d["id"] for d in dests])
    return jsonify([d["id"] for d in dests if d.get("has_bucket")])

@buckets_bp.route("/buckets/", methods=["POST"])
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

@buckets_bp.route("/buckets/<bucket_id>/items", methods=["GET"])
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

@buckets_bp.route("/buckets/<bucket_id>/upload", methods=["POST"])
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

@buckets_bp.route("/buckets/<bucket_id>/publish/<filename>", methods=["POST"])
def publish(bucket_id: str, filename: str):
    """
    DEPRECATED: This endpoint is deprecated. Use /api/publish instead.
    
    This endpoint will trigger an error but will still function to prevent
    breaking existing functionality. It will be removed in a future version.
    
    Use the new unified endpoint with the appropriate parameters:
    - For bucket-to-bucket: dest_bucket_id, src_bucket_id, filename
    """
    # Log a warning about using deprecated endpoint
    from utils.logger import warning, error
    warning(f"[DEPRECATED ENDPOINT] /api/buckets/{bucket_id}/publish/{filename} - Use /api/publish instead")
    error("[DEPRECATED ENDPOINT] Using deprecated bucket-specific publish route. Please update to use /api/publish")
    
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    src = bucket_path(bucket_id) / filename
    if not src.exists():
        abort(404, "file not in bucket")

    data = request.get_json() or {}
    
    res = publish_to_destination(
        source=src,
        publish_destination_id=bucket_id,
        metadata=data.get('generation_info'),
        skip_bucket=data.get('skip_bucket', True)  # Skip bucket append since this is already in a bucket
    )
    if not res["success"]:
        abort(500, res.get("error", "publish failed"))

    meta = load_meta(bucket_id)
    meta["published_meta"] = res["meta"]
    save_meta(bucket_id, meta)
    return jsonify(res)

# Currently displayed

# Serve the 256×256 thumbnail for a given media filename
@buckets_bp.route("/buckets/<bucket_id>/thumbnail/<filename>")
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
@buckets_bp.route("/buckets/<bucket_id>/info", methods=["GET"])
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
    raw_url     = f"/output/{bucket_id}/{filename}"
    thumb_url   = f"/output/{bucket_id}/thumbnails/{Path(filename).stem}{Path(filename).suffix}.jpg"
    return jsonify({
        "filename": filename,
        "published_at": published_at,
        "raw_url": raw_url,
        "thumbnail_url": thumb_url,
    })

# -- favourites --------------------------------------------------------------

@buckets_bp.route("/buckets/<bucket_id>/favorite/<filename>", methods=["POST"])
def favorite(bucket_id: str, filename: str):
    """Favorite a file in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    # Use seq_to_filenames helper to handle both string and dict entries
    if filename not in seq_to_filenames(meta.get("sequence", [])):
        abort(404, "not in bucket")
    meta.setdefault("favorites", []).append(filename)
    save_meta(bucket_id, meta)
    return jsonify({"status": "favorited"})


@buckets_bp.route("/buckets/<bucket_id>/favorite/<filename>", methods=["DELETE"])
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

@buckets_bp.route("/buckets/<bucket_id>/<filename>", methods=["DELETE"])
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
    
    # Clean up reference images before deleting main file
    try:
        from routes.bucket_utils import ReferenceImageStorage
        ref_storage = ReferenceImageStorage()
        base_filename = Path(filename).stem
        ref_storage.cleanup_reference_images(base_filename, bucket_id)
    except Exception as e:
        error(f"Failed to cleanup reference images for {filename}: {e}")
    
    fp.unlink()

    # remove side-car
    sc = sidecar_path(fp)
    sc.unlink(missing_ok=True)

    # remove thumbnail
    thumb_fp = bucket_path(bucket_id) / "thumbnails" / f"{Path(filename).stem}{Path(filename).suffix}.jpg"
    thumb_fp.unlink(missing_ok=True)

    meta = load_meta(bucket_id)
    # --- clean up sequence entries that can be strings or dicts ---
    seq = meta.get("sequence", [])
    new_seq = []
    for entry in seq:
        if isinstance(entry, dict):
            if entry.get("file") != filename:
                new_seq.append(entry)
        else:
            if entry != filename:
                new_seq.append(entry)
    if len(new_seq) != len(seq):
        meta["sequence"] = new_seq

    # Clean up favorites (array of filenames only)
    favs = meta.get("favorites", [])
    if filename in favs:
        favs.remove(filename)
        meta["favorites"] = favs

    save_meta(bucket_id, meta)

    return jsonify({"status": "deleted"})

# -- move / copy -------------------------------------------------------------

@buckets_bp.route("/buckets/add_image_to_new_bucket", methods=["POST"])
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

@buckets_bp.route("/buckets/<bucket_id>/move-to/<filename>", methods=["POST"])
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
    filenames = seq_to_filenames(seq)
    
    # Verify the file exists in the sequence
    if filename not in filenames:
        abort(404, "file not in sequence")
    
    # Get the index of the file in the sequence
    idx = filenames.index(filename)
    # Get the actual entry
    entry = seq[idx]
    # Remove the file from its current position
    seq.pop(idx)
    
    if insert_after:
        # Verify insert_after file exists in sequence
        if insert_after not in filenames:
            abort(404, "insert_after file not in sequence")
        # Find the index to insert after
        insert_index = filenames.index(insert_after) + 1
    else:
        # Move to top
        insert_index = 0
    
    # Insert the file at the new position
    seq.insert(insert_index, entry)
    
    # Update the metadata
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    
    return jsonify({
        "status": "moved",
        "index": insert_index,
        "filename": filename,
        "insert_after": insert_after
    })

@buckets_bp.route("/buckets/<bucket_id>/move-up/<filename>", methods=["POST"])
def move_up(bucket_id: str, filename: str):
    """Move a file up in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    seq = meta.get("sequence", [])
    filenames = seq_to_filenames(seq)
    
    if filename not in filenames:
        abort(404, "file not in sequence")
    
    # Get the index in the filenames list
    i = filenames.index(filename)
    
    # Swap the entries in the original sequence
    if i == 0:
        # Move from first to last
        seq.append(seq.pop(0))
    else:
        # Swap with previous item
        seq[i - 1], seq[i] = seq[i], seq[i - 1]
    
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    
    # Return the new index in the updated filenames list
    updated_filenames = seq_to_filenames(seq)
    return jsonify({"status": "moved-up", "index": updated_filenames.index(filename)})


@buckets_bp.route("/buckets/<bucket_id>/move-down/<filename>", methods=["POST"])
def move_down(bucket_id: str, filename: str):
    """Move a file down in a bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    meta = load_meta(bucket_id)
    seq = meta.get("sequence", [])
    filenames = seq_to_filenames(seq)
    
    if filename not in filenames:
        abort(404, "file not in sequence")
    
    # Get the index in the filenames list
    i = filenames.index(filename)
    
    # Swap the entries in the original sequence
    if i == len(seq) - 1:
        # Move from last to first
        seq.insert(0, seq.pop())
    else:
        # Swap with next item
        seq[i + 1], seq[i] = seq[i], seq[i + 1]
    
    meta["sequence"] = seq
    save_meta(bucket_id, meta)
    
    # Return the new index in the updated filenames list
    updated_filenames = seq_to_filenames(seq)
    return jsonify({"status": "moved-down", "index": updated_filenames.index(filename)})

# -- raw file helper ---------------------------------------------------------

@buckets_bp.route("/buckets/<bucket_id>/raw/<path:filename>")
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

@buckets_bp.route("/buckets/<bucket_id>/purge", methods=["DELETE"])
def purge_bucket_endpoint(bucket_id: str):
    """Purge files from a bucket, optionally including favorites and filtering by age."""
    include_favorites = request.args.get("include_favorites", "false").lower() == "true"
    days = request.args.get("days")
    if days is not None:
        try:
            days = int(days)
        except ValueError:
            abort(400, "days parameter must be an integer")
    
    try:
        result = purge_bucket(bucket_id, include_favorites=include_favorites, days=days)
        return jsonify(result)
    except ValueError as e:
        abort(400, str(e))
    except Exception as e:
        error(f"Error purging bucket {bucket_id}: {str(e)}")
        abort(500, str(e))

@buckets_bp.route("/buckets/reindex", methods=["POST"])
def reindex_all():
    """Reindex all buckets, optionally rebuilding metadata and thumbnails."""
    rebuild_all_sidecars = request.args.get("rebuild_all_sidecars", "false").lower() == "true"
    rebuild_all_thumbs = request.args.get("rebuild_all_thumbs", "false").lower() == "true"
    
    result = reindex_bucket(
        rebuild_all_sidecars=rebuild_all_sidecars,
        rebuild_all_thumbs=rebuild_all_thumbs
    )
    return jsonify(result)

@buckets_bp.route("/buckets/<bucket_id>/reindex", methods=["POST"])
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

@buckets_bp.route("/buckets/<bucket_id>/extractjson", methods=["POST"])
def extract_json(bucket_id: str):
    """Extract JSON metadata from all files in bucket"""
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == bucket_id and d.get("has_bucket", False)), None)
    if not dest:
        abort(400, "Invalid bucket_id or destination does not support buckets")
    
    result = extract_json(bucket_id)
    return jsonify(result)

@buckets_bp.route("/buckets/<bucket_id>/complete", methods=["GET"])
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
    
    # Prepare a flat list of filenames from the sequence metadata (string or dict)
    raw_sequence = meta.get("sequence", [])
    filenames_only = []
    sequence_entries = {}  # Map filenames to their full entry data
    
    for entry in raw_sequence:
        if isinstance(entry, dict):
            fname = entry.get("file")
            if fname:
                filenames_only.append(fname)
                sequence_entries[fname] = entry
        else:
            fname = entry
            if fname:
                filenames_only.append(fname)
                sequence_entries[fname] = {"file": fname}
    
    # Only show files that are in the sequence list
    for filename in filenames_only:
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
        
        # Get file stats for timestamps
        file_stats = file_path.stat()
        
        # Get sequence entry data
        seq_entry = sequence_entries.get(filename, {})
        
        # Prefer explicit timestamp from sidecar metadata or sequence entry
        # Fallback to file modification time (st_mtime) which is preserved by shutil.copy2.
        # We avoid st_ctime because on Unix-like systems it represents the inode change time
        # and will always be updated on copy, leading to "Today" grouping.
        timestamp = (
            file_meta.get("timestamp")
            or seq_entry.get("timestamp")
            or file_stats.st_mtime
        )

        # Get reference images for this file
        reference_images = []
        try:
            from routes.bucket_utils import ReferenceImageStorage
            ref_storage = ReferenceImageStorage()
            base_filename = file_path.stem
            ref_images = ref_storage.get_reference_images(base_filename, bucket_id)
            reference_images = [ref.to_dict(bucket_id) for ref in ref_images]  # Pass bucket_id for URL conversion
        except Exception as e:
            error(f"Failed to get reference images for {file_path.name}: {e}")
        
        files.append({
            "filename": file_path.name,
            "size": file_stats.st_size,
            "modified": file_stats.st_mtime,
            "created_at": timestamp,
            "metadata": {
                **file_meta,
                "timestamp": timestamp,
                "batchId": seq_entry.get("batchId")
            },
            "favorite": file_path.name in meta.get("favorites", []),
            "sequence_index": filenames_only.index(file_path.name),
            "thumbnail_url": f"/output/{bucket_id}/thumbnails/{file_path.stem}{file_path.suffix}.jpg",
            "raw_url": f"/output/{bucket_id}/{file_path.name}",
            "reference_images": reference_images
        })
    
    # Sort files by sequence (using our normalised list)
    sequence_map = {f: i for i, f in enumerate(filenames_only)}
    files.sort(key=lambda f: sequence_map.get(f["filename"], float("inf")))

    # Get published info if available
    published = None
    if "published_meta" in meta:
        pm = meta["published_meta"]
        published_filename = pm.get("filename")
        
        # Check if file was published from this bucket
        published_in_this_bucket = any(f["filename"] == published_filename for f in files)
        
        # Create published object with data directly from published_meta
        published = {
            "filename": published_filename,
            "published_at": pm.get("published_at"),
            "raw_url": pm.get("raw_url"),
            "thumbnail_url": pm.get("thumbnail_url"),
            "from_bucket": published_in_this_bucket,
            "metadata": pm.get("metadata", {})
        }
        
        # Log what we're returning for debugging
        info(f"Published info for bucket {bucket_id}: {published}")

    return jsonify({
        "bucket_id": bucket_id,
        "files": files,
        "published": published
    })
