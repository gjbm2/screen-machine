from typing import Dict, Any, List
from pathlib import Path
import json
import base64
from datetime import datetime
import uuid
import shutil
from PIL import Image, ExifTags
import subprocess
import cv2

from utils.logger import info, error, warning, debug
from routes.utils import (
    sidecar_path,
    ensure_sidecar_for,
    _extract_exif_json,
    _extract_mp4_comment_json
)

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

def extract_json(bucket_id: str) -> Dict[str, Any]:
    """
    Extract JSON metadata from all files in a bucket.
    
    Args:
        bucket_id: The bucket to extract metadata from
    
    Returns:
        Dict with status and list of extracted files
    """
    info(f"[extract] running extract for {bucket_id}")
    extracted = []

    for f in bucket_path(bucket_id).iterdir():
        if f.is_file() and f.suffix.lower() in ALLOWED_EXT:
            try:
                inferred = infer_meta_from_file(f)
                if inferred:
                    sidecar_file = sidecar_path(f)
                    sidecar_file.write_text(json.dumps(inferred, indent=2), encoding="utf-8")
                    extracted.append(f.name)
            except Exception as e:
                warning(f"Metadata inference failed for {f.name}: {e}")

    return {
        "status": "extracted",
        "files": extracted
    }

def reindex_bucket(publish_destination_id: str = None, rebuild_all_sidecars: bool = False, rebuild_all_thumbs: bool = False) -> Dict[str, Any]:
    """
    Reindex one or all buckets, optionally rebuilding metadata and thumbnails.
    
    Args:
        publish_destination_id: If provided, only reindex this destination. Otherwise reindex all.
        rebuild_all_sidecars: If True, rebuild all metadata sidecars. If False, only rebuild missing ones.
        rebuild_all_thumbs: If True, rebuild all thumbnails. If False, only rebuild missing ones.
    
    Returns:
        Dict with status and details of what was reindexed
    """
    from routes.utils import _load_json_once
    from routes.publisher import generate_thumbnail
    
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    if publish_destination_id:
        dests = [d for d in dests if d["id"] == publish_destination_id]
    else:
        dests = [d for d in dests if d.get("has_bucket", False)]
    
    reindexed = []
    for dest in dests:
        bucket_id = dest["id"]
        try:
            meta = load_meta(bucket_id)
            favs = set(meta.get("favorites", []))
            seq = meta.get("sequence", [])
            
            # Get all files in bucket
            files = [f for f in bucket_path(bucket_id).iterdir() if f.is_file() and f.suffix.lower() in ALLOWED_EXT]
            file_names = [f.name for f in files]
            
            # Update sequence to include new files
            for fname in file_names:
                if fname not in seq:
                    seq.append(fname)
            
            # Remove files from sequence that no longer exist
            seq[:] = [f for f in seq if f in file_names]
            
            # Ensure favorites still exist
            favs = [f for f in favs if f in file_names]
            
            # Rebuild metadata sidecars
            if rebuild_all_sidecars:
                extract_result = extract_json(bucket_id)
                rebuilt_sidecars = extract_result["files"]
            else:
                rebuilt_sidecars = []
                for f in files:
                    if extract_metadata(f, force_rebuild=False):
                        rebuilt_sidecars.append(f.name)
            
            # Rebuild thumbnails
            rebuilt_thumbs = []
            thumb_dir = bucket_path(bucket_id) / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            for f in files:
                # Include extension in thumbnail name to keep them separate
                thumb_path = thumb_dir / f"{f.stem}{f.suffix}.jpg"
                if rebuild_all_thumbs or not thumb_path.exists():
                    try:
                        generate_thumbnail(f, thumb_path)
                        rebuilt_thumbs.append(f.name)
                    except Exception as e:
                        warning(f"Failed to rebuild thumbnail for {f.name}: {e}")
            
            meta["sequence"] = seq
            meta["favorites"] = list(favs)
            save_meta(bucket_id, meta)
            
            reindexed.append({
                "bucket_id": bucket_id,
                "files": file_names,
                "rebuilt_sidecars": rebuilt_sidecars,
                "rebuilt_thumbs": rebuilt_thumbs
            })
        except Exception as e:
            warning(f"Failed to reindex {bucket_id}: {e}")
    
    return {
        "status": "reindexed",
        "buckets": reindexed
    }

def purge_bucket(publish_destination_id: str, include_favorites: bool = False) -> Dict[str, Any]:
    """
    Purge files from a bucket, optionally including favorites.
    
    Args:
        publish_destination_id: The bucket to purge
        include_favorites: If True, remove all files including favorites. If False, keep favorites.
    
    Returns:
        Dict with status and details of what was purged
    """
    from routes.utils import _load_json_once
    
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == publish_destination_id and d.get("has_bucket", False)), None)
    if not dest:
        raise ValueError("Invalid bucket_id or destination does not support buckets")
    
    info(f"[purge] running purge for {publish_destination_id}")
    meta = load_meta(publish_destination_id)
    favs = set(meta.get("favorites", []))
    seq = meta.get("sequence", [])

    removed = []
    # Remove media + side-cars + matching thumbs
    for fname in list(seq):  # iterate on a copy
        if not include_favorites and fname in favs:
            continue
        fp = bucket_path(publish_destination_id) / fname
        fp.unlink(missing_ok=True)
        sidecar_path(fp).unlink(missing_ok=True)
        # remove its thumbnail
        thumb_fp = bucket_path(publish_destination_id) / "thumbnails" / f"{Path(fname).stem}{Path(fname).suffix}.jpg"
        thumb_fp.unlink(missing_ok=True)
        seq.remove(fname)
        removed.append(fname)

    # Delete any orphaned thumbnails
    thumb_dir = bucket_path(publish_destination_id) / "thumbnails"
    if thumb_dir.exists():
        for thumb in thumb_dir.iterdir():
            if thumb.is_file() and thumb.suffix == ".jpg":
                stem = thumb.stem
                if not any(f.stem == stem for f in bucket_path(publish_destination_id).iterdir() if f.is_file()):
                    thumb.unlink()

    # Update metadata
    if include_favorites:
        meta["favorites"] = []
    meta["sequence"] = seq
    save_meta(publish_destination_id, meta)

    return {
        "status": "purged",
        "removed": removed,
        "favorites_removed": include_favorites
    }

def extract_metadata(file_path: Path, force_rebuild: bool = False) -> bool:
    """
    Extract metadata from a file and create/update its sidecar.
    
    Args:
        file_path: Path to the file to extract metadata from
        force_rebuild: If True, rebuild even if sidecar exists
    
    Returns:
        True if metadata was extracted, False otherwise
    """
    sidecar = sidecar_path(file_path)
    if not force_rebuild and sidecar.exists():
        return False
        
    try:
        inferred = infer_meta_from_file(file_path)
        if inferred:
            sidecar.write_text(json.dumps(inferred, indent=2), encoding="utf-8")
            return True
    except Exception as e:
        warning(f"Metadata inference failed for {file_path.name}: {e}")
    return False

def _append_to_bucket(screen: str, published_path: Path) -> Path:
    """
    Copy *published_path* plus side-car into <bucket>/ preserving history:
    • If that exact filename already exists in the bucket, create a
      timestamp-uuid filename instead and store that.
    • Copy the sidecar if it exists
    • Update bucket metadata sequence
    • Generate thumbnail for the bucket copy
    """
    bucket_dir = bucket_path(screen)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # choose filename (avoid collisions)
    target_path = bucket_dir / published_path.name
    if target_path.exists():
        target_path = bucket_dir / unique_name(published_path.name)

    # copy media + side-car
    shutil.copy2(published_path, target_path)
    sc_src, sc_dst = sidecar_path(published_path), sidecar_path(target_path)
    if sc_src.exists():
        shutil.copy2(sc_src, sc_dst)

    # update bucket metadata
    meta = load_meta(screen)
    seq  = meta.setdefault("sequence", [])
    seq.append(target_path.name)
    save_meta(screen, meta)
    
    # now generate the thumbnail for *that* bucket copy
    from routes.publisher import generate_thumbnail
    thumb_dir = bucket_dir / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"{target_path.stem}{target_path.suffix}.jpg"
    try:
        generate_thumbnail(target_path, thumb_path)
    except Exception as e:
        warning(f"Bucket thumbnail failed for {target_path.name}: {e}")
    return target_path

def _record_publish(bucket: str, filename: str, when: str) -> None:
    """Persist 'published_meta' to the bucket JSON."""
    meta = load_meta(bucket)
    meta["published_meta"] = {"filename": filename, "published_at": when}
    save_meta(bucket, meta) 

def _extract_exif_json(img_path: Path) -> dict[str, Any] | None:
    """Extract EXIF metadata from an image file."""
    try:
        img = Image.open(img_path)
        exif = img._getexif()
        if not exif:
            return None

        meta = {}
        for tag_id in exif:
            tag = ExifTags.TAGS.get(tag_id, tag_id)
            data = exif.get(tag_id)
            if isinstance(data, bytes):
                data = data.decode()
            meta[tag] = data

        return meta
    except Exception as e:
        warning(f"Failed to extract EXIF from {img_path}: {e}")
        return None

def _extract_mp4_comment_json(video_path: Path) -> dict[str, Any] | None:
    """Extract comment metadata from an MP4 file."""
    try:
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        comment = data.get("format", {}).get("tags", {}).get("comment")
        if not comment:
            return None

        return json.loads(comment)
    except Exception as e:
        warning(f"Failed to extract comment from {video_path}: {e}")
        return None 

def copy_image_from_bucket_to_bucket(source_publish_destination: str, target_publish_destination: str, filename: str, copy: bool = False) -> Dict[str, Any]:
    """
    Copy or move an image from one bucket to another.
    
    Args:
        source_publish_destination: Source bucket ID
        target_publish_destination: Target bucket ID
        filename: Name of the file to copy/move
        copy: If True, copy the file instead of moving it
    
    Returns:
        Dict with status and new filename
    """
    from routes.utils import _load_json_once
    
    # Verify target is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == target_publish_destination and d.get("has_bucket", False)), None)
    if not dest:
        raise ValueError("Invalid target_publish_destination or destination does not support buckets")
    
    debug(f"[copy_image_from_bucket_to_bucket] Starting operation: src_id={source_publish_destination}, dst_id={target_publish_destination}, fname={filename}")

    # Get source and target paths
    src_path = bucket_path(source_publish_destination) / filename
    if not src_path.exists():
        error(f"[copy_image_from_bucket_to_bucket] Source file not found: {src_path}")
        raise FileNotFoundError("source file not found")

    # Generate unique name for target
    target_filename = unique_name(filename)
    dst_path = bucket_path(target_publish_destination) / target_filename
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    # Copy main file
    try:
        shutil.copy2(src_path, dst_path)
        debug(f"[copy_image_from_bucket_to_bucket] Copied main file: {src_path} -> {dst_path}")
    except Exception as e:
        error(f"[copy_image_from_bucket_to_bucket] Failed to copy main file: {str(e)}")
        raise

    # Copy sidecar
    sc_src, sc_dst = sidecar_path(src_path), sidecar_path(dst_path)
    if sc_src.exists():
        try:
            shutil.copy2(sc_src, sc_dst)
            debug(f"[copy_image_from_bucket_to_bucket] Copied sidecar: {sc_src} -> {sc_dst}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to copy sidecar: {str(e)}")
            raise

    # Copy thumbnail
    src_thumb_dir = bucket_path(source_publish_destination) / 'thumbnails'
    dst_thumb_dir = bucket_path(target_publish_destination) / 'thumbnails'
    dst_thumb_dir.mkdir(parents=True, exist_ok=True)
    
    src_thumb_path = src_thumb_dir / f"{Path(filename).stem}{Path(filename).suffix}.jpg"
    dst_thumb_path = dst_thumb_dir / f"{Path(target_filename).stem}{Path(target_filename).suffix}.jpg"
    
    if src_thumb_path.exists():
        try:
            shutil.copy2(src_thumb_path, dst_thumb_path)
            debug(f"[copy_image_from_bucket_to_bucket] Copied thumbnail: {src_thumb_path} -> {dst_thumb_path}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to copy thumbnail: {str(e)}")
            raise
    else:
        try:
            generate_thumbnail(dst_path, dst_thumb_path)
            debug(f"[copy_image_from_bucket_to_bucket] Generated new thumbnail: {dst_thumb_path}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to generate thumbnail: {str(e)}")
            raise

    # Update destination metadata
    try:
        dmeta = load_meta(target_publish_destination)
        dmeta.setdefault("sequence", []).append(target_filename)
        save_meta(target_publish_destination, dmeta)
        debug(f"[copy_image_from_bucket_to_bucket] Updated destination metadata: added {target_filename} to sequence")
    except Exception as e:
        error(f"[copy_image_from_bucket_to_bucket] Failed to update destination metadata: {str(e)}")
        raise

    # Handle source deletion if this is a move (not copy)
    if not copy:
        try:
            delete_file(source_publish_destination, filename)
            debug(f"[copy_image_from_bucket_to_bucket] Deleted source files for {filename}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to delete source files: {str(e)}")
            raise

    return {
        "status": "copied" if copy else "moved",
        "filename": target_filename
    } 