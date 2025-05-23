from typing import Dict, Any, List
from pathlib import Path
import json
import base64
from datetime import datetime, timedelta
import uuid
import shutil
from PIL import Image, ExifTags
import subprocess
import cv2
import os
import time
import re

from utils.logger import info, error, warning, debug
from routes.utils import (
    sidecar_path,
    ensure_sidecar_for,
    _extract_exif_json,
    _extract_mp4_comment_json,
    seq_to_filenames,
    upsert_seq
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

def purge_bucket(publish_destination_id: str, include_favorites: bool = False, days: int = None) -> Dict[str, Any]:
    """
    Purge files from a bucket, optionally including favorites and filtering by age.
    
    Args:
        publish_destination_id: The bucket to purge
        include_favorites: If True, remove all files including favorites. If False, keep favorites.
        days: If specified, only remove files older than this many days. If None, remove all files.
    
    Returns:
        Dict with status and details of what was purged
    """
    from routes.utils import _load_json_once
    from datetime import datetime, timedelta
    
    # Verify this is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == publish_destination_id and d.get("has_bucket", False)), None)
    if not dest:
        raise ValueError("Invalid bucket_id or destination does not support buckets")
    
    info(f"[purge] running purge for {publish_destination_id}")
    meta = load_meta(publish_destination_id)
    favs = set(meta.get("favorites", []))
    seq = meta.get("sequence", [])
    
    # Get a list of just the filenames from the sequence
    filenames = seq_to_filenames(seq)
    
    # Create a mapping of filenames to their sequence entries for favorite checking
    filename_to_entry = {}
    for entry in seq:
        if isinstance(entry, dict):
            filename_to_entry[entry["file"]] = entry
        else:
            filename_to_entry[entry] = entry

    # Calculate cutoff time if days is specified
    cutoff_time = None
    if days is not None:
        cutoff_time = datetime.now() - timedelta(days=days)

    removed = []
    # Remove media + side-cars + matching thumbs
    # Create a copy of the sequence to iterate over while we modify the original
    for i, fname in enumerate(filenames):
        # Skip favorites unless include_favorites is True
        if not include_favorites and fname in favs:
            info(f"[purge] Skipping favorite file: {fname}")
            continue
            
        fp = bucket_path(publish_destination_id) / fname
        
        # Skip if file doesn't exist
        if not fp.exists():
            warning(f"[purge] File does not exist, skipping: {fname}")
            continue
            
        # Check file age if days parameter is specified
        if cutoff_time is not None:
            file_mtime = datetime.fromtimestamp(fp.stat().st_mtime)
            if file_mtime > cutoff_time:
                info(f"[purge] File is too new, skipping: {fname} (modified {file_mtime})")
                continue
        
        # Remove the file and related files
        info(f"[purge] Removing file: {fname}")
        fp.unlink(missing_ok=True)
        sidecar_path(fp).unlink(missing_ok=True)
        # remove its thumbnail
        thumb_fp = bucket_path(publish_destination_id) / "thumbnails" / f"{Path(fname).stem}{Path(fname).suffix}.jpg"
        thumb_fp.unlink(missing_ok=True)
        
        # Add to the removed list
        removed.append(fname)

    # Remove entries from the sequence that were removed
    if removed:
        # Create a set of removed filenames for faster lookup
        removed_set = set(removed)
        # Filter out removed entries, handling both string and dict formats
        meta["sequence"] = [
            entry for entry in seq 
            if (entry["file"] if isinstance(entry, dict) else entry) not in removed_set
        ]

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
    save_meta(publish_destination_id, meta)

    info(f"[purge] Completed purge for {publish_destination_id}. Removed {len(removed)} files: {removed}")
    return {
        "status": "purged",
        "removed": removed,
        "favorites_removed": include_favorites,
        "days_filter": days
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

def _append_to_bucket(screen: str, published_path: Path, batch_id: str = None, metadata: dict = None) -> Path:
    """
    Copy *published_path* plus side-car into <bucket>/ preserving history:
    • If that exact filename already exists in the bucket, create a
      timestamp-uuid filename instead and store that.
    • Preserve original timestamps during cross-bucket operations
    • Copy the sidecar if it exists
    • Update bucket metadata sequence
    • Generate thumbnail for the bucket copy
    """
    bucket_dir = bucket_path(screen)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # Check if this is a cross-bucket operation
    is_cross_bucket = not str(published_path).startswith(str(bucket_dir))
    
    # Try to extract timestamp from original filename (format: YYYYMMDD-HHMMSS)
    timestamp_match = re.match(r"(\d{8}-\d{6})", published_path.name)
    
    if timestamp_match:
        # Use the original timestamp but generate a new UUID
        ts = timestamp_match.group(1)
        uid = uuid.uuid4().hex[:8]
        target_name = f"{ts}-{uid}{published_path.suffix.lower()}"
    else:
        # If no timestamp in original name, use the original name as is for same-bucket,
        # or generate a new timestamped name for cross-bucket
        if is_cross_bucket:
            target_name = unique_name(published_path.name)
        else:
            target_name = published_path.name
    
    target_path = bucket_dir / target_name
    
    # If target exists, generate a new unique name while still trying to preserve timestamp
    if target_path.exists():
        if timestamp_match:
            # Keep using the same timestamp, just generate a new UUID
            uid = uuid.uuid4().hex[:8]
            target_name = f"{ts}-{uid}{published_path.suffix.lower()}"
            target_path = bucket_dir / target_name
            # If still exists, then fall back to new timestamp
            if target_path.exists():
                target_name = unique_name(published_path.name)
                target_path = bucket_dir / target_name
        else:
            target_name = unique_name(published_path.name)
            target_path = bucket_dir / target_name
    
    # If a batchId is provided, insert it into the filename
    if batch_id:
        # Strip any file extensions from batch_id to prevent concatenation
        clean_batch_id = batch_id
        if '.' in clean_batch_id:
            clean_batch_id = clean_batch_id.split('.')[0]
            
        # Extract timestamp part and suffix
        parts = target_name.split('-', 2)
        if len(parts) >= 2:
            date_part = parts[0]
            time_part = parts[1]
            remaining = parts[2] if len(parts) > 2 else ''
            
            # Format with clean batch ID
            target_name = f"{date_part}-{time_part}-{remaining}_batch-{clean_batch_id}{published_path.suffix}"
        else:
            # If name doesn't have expected format, just append batch-id to it
            base, ext = os.path.splitext(target_name)
            target_name = f"{base}_batch-{clean_batch_id}{ext}"
        
        target_path = bucket_dir / target_name

    # copy media + side-car
    shutil.copy2(published_path, target_path)
    sc_src, sc_dst = sidecar_path(published_path), sidecar_path(target_path)
    
    # Handle sidecar creation/copying
    if sc_src.exists():
        # If source sidecar exists, copy it
        shutil.copy2(sc_src, sc_dst)
        debug(f"Copied sidecar: {sc_src} -> {sc_dst}")
    elif metadata:
        # If we have metadata but no source sidecar, create a new one
        try:
            with open(sc_dst, 'w', encoding='utf-8') as f:
                # Ensure metadata is JSON serializable. Fall back to string conversion for unsupported types.
                try:
                    json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)
                except TypeError as te:
                    # As a fallback, convert everything to string representation
                    from routes.utils import truncate_element
                    safe_meta = truncate_element(metadata)
                    json.dump(safe_meta, f, indent=2, ensure_ascii=False, default=str)
            debug(f"Created new sidecar with provided metadata: {sc_dst}")
        except Exception as e:
            warning(f"Failed to create sidecar with provided metadata: {e}")
    else:
        # Try to extract metadata from the file itself
        try:
            extract_metadata(target_path)
            debug(f"Extracted metadata for: {target_path}")
        except Exception as e:
            warning(f"Failed to extract metadata for {target_path.name}: {e}")

    # update bucket metadata
    meta = load_meta(screen)
    
    # Create the entry for sequence.json
    sequence_entry = {
        "file": target_path.name
    }
    
    # Add batchId to the entry if provided
    if batch_id:
        sequence_entry["batchId"] = batch_id
        
    # Update the sequence
    seq = meta.setdefault("sequence", [])
    # Ensure we're storing the filename and batch ID separately in the metadata
    if isinstance(seq, list):
        if batch_id:
            # Clean batch_id to remove any file extensions
            clean_batch_id = batch_id
            if '.' in clean_batch_id:
                clean_batch_id = clean_batch_id.split('.')[0]
                
            if all(isinstance(item, dict) for item in seq):
                # Dictionary format - add a new entry
                sequence_entry = {
                    "file": target_path.name,
                    "batchId": clean_batch_id
                }
                seq.append(sequence_entry)
            else:
                # Simple string format - convert to dictionary format
                # First convert existing entries if needed
                new_seq = []
                for item in seq:
                    if isinstance(item, str):
                        new_seq.append({"file": item})
                    else:
                        new_seq.append(item)
                new_seq.append({
                    "file": target_path.name,
                    "batchId": clean_batch_id
                })
                meta["sequence"] = new_seq
        else:
            # No batch_id, simpler handling
            if all(isinstance(item, dict) for item in seq):
                seq.append({"file": target_path.name})
            else:
                seq.append(target_path.name)
    else:
        # Fallback if sequence is not a list
        meta["sequence"] = [{"file": target_path.name, "batchId": batch_id}] if batch_id else [target_path.name]
        
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

def preserve_timestamp_name(original_filename: str, force_unique: bool = True) -> str:
    """
    Generate a new filename while preserving the timestamp from the original if it exists.
    If the original doesn't have a timestamp or force_unique is True, generate a new unique name.
    """
    # Try to extract timestamp from original filename (format: YYYYMMDD-HHMMSS)
    timestamp_match = re.match(r"(\d{8}-\d{6})", original_filename)
    
    if timestamp_match and not force_unique:
        # Preserve the original timestamp
        ts = timestamp_match.group(1)
    else:
        # Generate new timestamp
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    
    uid = uuid.uuid4().hex[:8]
    return f"{ts}-{uid}{Path(original_filename).suffix.lower()}"

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
    
    # Log incoming parameters
    debug(f"[copy_image_from_bucket_to_bucket] PARAMS: src={source_publish_destination}, target={target_publish_destination}, filename={filename}, copy={copy}")
    
    # Verify target is a valid destination with has_bucket=true
    dests = _load_json_once("publish_destinations", "publish-destinations.json")
    dest = next((d for d in dests if d["id"] == target_publish_destination and d.get("has_bucket", False)), None)
    if not dest:
        error(f"[copy_image_from_bucket_to_bucket] Invalid target or no bucket support: {target_publish_destination}")
        raise ValueError("Invalid target_publish_destination or destination does not support buckets")
    
    debug(f"[copy_image_from_bucket_to_bucket] Starting operation: src_id={source_publish_destination}, dst_id={target_publish_destination}, fname={filename}")

    # Get source and target paths
    src_path = bucket_path(source_publish_destination) / filename
    if not src_path.exists():
        error(f"[copy_image_from_bucket_to_bucket] Source file not found: {src_path}")
        raise FileNotFoundError("source file not found")

    # Try to extract timestamp from original filename (format: YYYYMMDD-HHMMSS)
    timestamp_match = re.match(r"(\d{8}-\d{6})", filename)
    
    if timestamp_match:
        # Use the original timestamp but generate a new UUID
        ts = timestamp_match.group(1)
        uid = uuid.uuid4().hex[:8]
        target_filename = f"{ts}-{uid}{Path(filename).suffix.lower()}"
    else:
        # If no timestamp in original name, use the original name as is
        target_filename = filename
        
    dst_path = bucket_path(target_publish_destination) / target_filename
    
    # If target exists, then generate a completely new name with current timestamp
    if dst_path.exists():
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

    # Handle sidecar metadata
    sc_src, sc_dst = sidecar_path(src_path), sidecar_path(dst_path)
    src_meta = {}
    if sc_src.exists():
        try:
            # Read source sidecar metadata
            with open(sc_src, 'r', encoding='utf-8') as f:
                src_meta = json.load(f)
            
            # Write to destination sidecar, preserving original metadata
            with open(sc_dst, 'w', encoding='utf-8') as f:
                json.dump(src_meta, f, indent=2, ensure_ascii=False)
            debug(f"[copy_image_from_bucket_to_bucket] Copied sidecar metadata: {sc_src} -> {sc_dst}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to copy sidecar metadata: {str(e)}")
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
        # Add the new file to the sequence with the correct format
        dmeta.setdefault("sequence", []).append({
            "file": target_filename
        })
        save_meta(target_publish_destination, dmeta)
        debug(f"[copy_image_from_bucket_to_bucket] Updated destination metadata: added {target_filename} to sequence")
    except Exception as e:
        error(f"[copy_image_from_bucket_to_bucket] Failed to update destination metadata: {str(e)}")
        raise

    # Handle source deletion if this is a move (not copy)
    if not copy:
        try:
            from routes.bucket_api import delete_file
            debug(f"[copy_image_from_bucket_to_bucket] Will DELETE source file: bucket={source_publish_destination}, file={filename}")
            delete_file(source_publish_destination, filename)
            debug(f"[copy_image_from_bucket_to_bucket] Deleted source files for {filename}")
        except Exception as e:
            error(f"[copy_image_from_bucket_to_bucket] Failed to delete source files: {str(e)}")
            raise

    return {
        "status": "copied" if copy else "moved",
        "filename": target_filename
    } 