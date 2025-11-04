"""
publisher.py – one canonical place for every way we can publish an asset.

• publish_to_destination(...) – URL or local file ➜  destination (with optional bucket update)

─── New in this version ────────────────────────────────────────────────
1. Side-car handling  (media.ext.json) – created from EXIF if missing.
2. Touch() after copy so watchdogs always see fresh mtime.
3. Optional bucket append (controlled by skip_bucket parameter).
4. Generate 256×256 JPEG thumbnails for all media (first frame for videos), stored under output/thumbnails/<screen>/
"""

from __future__ import annotations

import json, shutil, subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import uuid
import os
import logging
import tempfile
import random
import re 
import sys

import requests
from utils.logger import info, error, warning, debug
from routes.utils import (
    _load_json_once, 
    detect_file_type,
    save_jpeg_with_metadata, 
    save_video_with_metadata,
    findfile,
    sidecar_path,
    ensure_sidecar_for,
    _extract_exif_json,
    _extract_mp4_comment_json,
    generate_thumbnail,
    get_image_from_target
)


from routes.bucketer import (
    _append_to_bucket,
    bucket_path,
    load_meta,
    save_meta
)


OUTPUT_ROOT = Path("output")         # centralised
ISO_NOW     = lambda: datetime.utcnow().isoformat() + "Z"

# Load publish destinations from JSON file
PUBLISH_DESTINATIONS_FILE = findfile("publish-destinations.json")
publish_destinations = {}

def _load_publish_destinations():
    """Load publish destinations from JSON file"""
    global publish_destinations
    if not publish_destinations:
        try:
            with open(PUBLISH_DESTINATIONS_FILE, 'r') as f:
                publish_destinations = json.load(f)
        except Exception as e:
            logging.error(f"Error loading publish destinations: {str(e)}")
            publish_destinations = {}



# ─────────────────────────────────────────────────────────────────────────────
# Side-car helpers
# ─────────────────────────────────────────────────────────────────────────────

# ─── Name management ─────────────────────────────
def _unique_name(original: str) -> str:
    """timestamp-uuid + same extension, e.g. 20250423-230102-abcd1234.jpg"""
    stem  = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    uid   = uuid.uuid4().hex[:8]
    return f"{stem}-{uid}{Path(original).suffix.lower()}"

# ─── Destination ─────────────────────────────────────────────────────────────
def get_destination(dest_id: str) -> dict[str, Any]:
    """Lookup a publish-destination by id (raises KeyError if unknown)."""
    _load_publish_destinations()
    for d in publish_destinations:
        if d["id"] == dest_id:
            return d
    raise KeyError(f"Unknown publish_destination_id '{dest_id}'")

# ─── Public wrapper ─────────────────────────────────────────────────────
def publish_to_destination(
    source: str | Path,
    publish_destination_id: str,
    metadata: dict | None = None,
    skip_bucket: bool | None = None,
    silent: bool = False,
    blank: bool = False,
    cross_bucket_mode: bool = False,
    batch_id: str | None = None,
    update_published: bool = True,
    is_history_navigation: bool = False,
) -> dict:
    """
    Publish an image to a destination, optionally saving to a bucket.

    Args:
        source: URL or local file path to the image
        publish_destination_id: ID of the destination to publish to
        metadata: Optional metadata to include with the image
        skip_bucket: If True, don't save to bucket. If None, determine based on source type.
        silent: If True, suppress overlay display
        blank: If True, display blank screen instead of source image
        cross_bucket_mode: If True, use cross-bucket URL format with /output/<id>.<ext>
        batch_id: Optional batch ID for the publish operation
        update_published: If False, save to bucket but don't update the published pointer

    Returns:
        dict with success status and optional error message
    """
    # Log incoming parameters
    info(f"[publish_to_destination] PARAMS: source={source}, dest={publish_destination_id}, skip_bucket={skip_bucket}")
    
    try:
        # Get destination config
        dest = get_destination(publish_destination_id)
        
        # In blank mode, publish /output/_blank.jpg to the destination (no bucket save)
        if blank:
            from routes.utils import findfile
            from PIL import Image
            blank_path = Path("output/_blank.jpg")
            if not blank_path.exists():
                # Create a simple black JPEG
                blank_path.parent.mkdir(parents=True, exist_ok=True)
                img = Image.new("RGB", (100, 100), color="black")
                img.save(blank_path, "JPEG", quality=95)
            # Publish the blank image to the destination, skip bucket
            result = _publish_to_destination(
                source=blank_path,
                publish_destination_id=publish_destination_id,
                metadata={},
                skip_bucket=True,
                silent=True,
                cross_bucket_mode=False,
                batch_id=batch_id,
                update_published=update_published,
                is_history_navigation=is_history_navigation
            )
            if result["success"]:
                _update_published_info(publish_destination_id, None, None)
            return result
            
        # Determine if source is a URL or local path
        is_url = (isinstance(source, str) and 
                (source.startswith('http://') or source.startswith('https://') or source.startswith('/api/')))
        
        info(f"[publish_to_destination] Source type: {'URL' if is_url else 'Local file'}")
        
        # Check for metadata. If none provided, try to get it from sidecar if source is a local file
        if metadata is None and not is_url:
            # Convert string path to Path object if needed
            source_path = Path(source) if isinstance(source, str) else source
            
            # Check for sidecar file and read metadata if it exists
            from routes.utils import sidecar_path
            sidecar_file = sidecar_path(source_path)
            if sidecar_file.exists():
                try:
                    import json
                    metadata = json.loads(sidecar_file.read_text("utf-8"))
                    info(f"[publish_to_destination] Loaded metadata from sidecar: {sidecar_file}")
                except Exception as e:
                    warning(f"[publish_to_destination] Failed to read sidecar for {source_path.name}: {e}")
            
            # If still no metadata, try to extract it directly from the file
            if metadata is None:
                file_suffix = source_path.suffix.lower()
                if file_suffix in ['.jpg', '.jpeg', '.png']:
                    try:
                        metadata = _extract_exif_json(source_path)
                        if metadata:
                            info(f"[publish_to_destination] Extracted EXIF metadata from {source_path.name}")
                    except Exception as e:
                        warning(f"[publish_to_destination] Failed to extract EXIF from {source_path.name}: {e}")
                elif file_suffix in ['.mp4', '.webm', '.mov']:
                    try:
                        metadata = _extract_mp4_comment_json(source_path)
                        if metadata:
                            info(f"[publish_to_destination] Extracted metadata from video {source_path.name}")
                    except Exception as e:
                        warning(f"[publish_to_destination] Failed to extract metadata from video {source_path.name}: {e}")
            
            # Initialize metadata as empty dict if all extraction methods failed
            if metadata is None:
                metadata = {}
                info(f"[publish_to_destination] No metadata found for {source_path.name}, using empty metadata")
        
        # Default skip_bucket behavior if not specified
        if skip_bucket is None:
            # For URLs, save to bucket by default
            # For local files, only save if not already in a bucket
            skip_bucket = (not is_url) and str(source).find(f"{dest.get('file', publish_destination_id)}") >= 0
            info(f"[publish_to_destination] Determined skip_bucket={skip_bucket} automatically")

        # Default cross_bucket_mode if not specified
        if not cross_bucket_mode and not is_url:
            # Check if source is from a different bucket
            source_str = str(source) if isinstance(source, str) else str(source)
            dest_bucket_path = str(bucket_path(publish_destination_id))
            cross_bucket_mode = (not source_str.startswith(dest_bucket_path)) and '/bucket_' in source_str
            if cross_bucket_mode:
                info(f"[publish_to_destination] Detected cross-bucket publishing from source path")

        # Handle URL sources
        if is_url:
            try:
                # Handle API-relative URLs and normalize them for local server access
                if source.startswith('/api/'):
                    # This is a relative URL - rewrite it to a local file path if possible
                    info(f"[publish_to_destination] Converting API-relative URL to local path: {source}")
                    
                    # Extract bucket and filename from URL like /api/buckets/bucket_id/raw/filename
                    bucket_pattern = r'/api/buckets/([^/]+)/raw/([^/]+)'
                    match = re.match(bucket_pattern, source)
                    
                    if match:
                        bucket_id, filename = match.groups()
                        local_path = bucket_path(bucket_id) / filename
                        info(f"[publish_to_destination] Converted to local path: {local_path}")
                        
                        if local_path.exists():
                            # Use the local file directly instead of downloading
                                                    return publish_to_destination(
                            source=local_path,
                            publish_destination_id=publish_destination_id,
                            metadata=metadata or {},
                            skip_bucket=skip_bucket,
                            silent=silent,
                            cross_bucket_mode=cross_bucket_mode or True,  # URL sources are like cross-bucket
                            batch_id=batch_id,
                            update_published=update_published,
                            is_history_navigation=is_history_navigation
                        )
                
                # If source comes from a full URL that contains buckets/raw/
                # it could be an absolute URL to our own API
                bucket_pattern = r'(https?://.*?)/api/buckets/([^/]+)/raw/([^/]+)'
                match = re.match(bucket_pattern, source)
                
                if match:
                    _, bucket_id, filename = match.groups()
                    local_path = bucket_path(bucket_id) / filename
                    info(f"[publish_to_destination] Found full URL to local bucket: {local_path}")
                    
                    if local_path.exists():
                        # Use the local file directly instead of downloading
                        return publish_to_destination(
                            source=local_path,
                            publish_destination_id=publish_destination_id,
                            metadata=metadata or {},
                            skip_bucket=skip_bucket,
                            silent=silent,
                            cross_bucket_mode=cross_bucket_mode or True,  # URL sources are like cross-bucket
                            batch_id=batch_id,
                            update_published=update_published,
                            is_history_navigation=is_history_navigation
                        )

                # If we get here, we need to download the remote URL
                info(f"[publish_to_destination] Downloading remote URL: {source}")
                response = requests.get(source, stream=True)
                response.raise_for_status()
                
                # --------------------------------------------------
                # Detect the correct file extension for the downloaded content
                # --------------------------------------------------
                from urllib.parse import urlparse, unquote
                parsed_url = urlparse(source)
                # Get last segment of the path (ignoring query/fragment) and unquote it
                path_tail = Path(unquote(parsed_url.path)).name  # e.g. "image.jpg"
                ext = Path(path_tail).suffix.lower()

                valid_exts = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp4', '.webm', '.mov'}
                file_extension = ext if ext in valid_exts else ''

                # Fallback: look at Content-Type header
                if not file_extension:
                    content_type = response.headers.get('Content-Type', '').lower()
                    if 'video' in content_type:
                        file_extension = '.mp4'
                    elif 'image/jpeg' in content_type:
                        file_extension = '.jpg'
                    elif 'image/png' in content_type:
                        file_extension = '.png'
                    elif 'image/gif' in content_type:
                        file_extension = '.gif'
                    elif 'image/webp' in content_type:
                        file_extension = '.webp'
                    else:
                        # Default safe fallback
                        file_extension = '.jpg'

                debug(f"Determined file extension: {file_extension} (url='{path_tail}', content-type='{response.headers.get('Content-Type', '')}')")
                
                # If the file is an image but not already JPEG, convert to JPEG for consistency
                image_exts = {'.png', '.gif', '.webp', '.jpeg'}
                is_video   = file_extension in {'.mp4', '.webm', '.mov'}

                if file_extension in image_exts and not is_video:
                    try:
                        from PIL import Image
                        from io import BytesIO

                        img = Image.open(BytesIO(response.content)).convert('RGB')
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
                            img.save(temp_file, format='JPEG', quality=95)
                            temp_path = Path(temp_file.name)
                        debug(f"Image converted to JPEG for publishing (original ext {file_extension}) -> {temp_path.name}")
                        file_extension = '.jpg'  # normalise
                    except Exception as e:
                        warning(f"JPEG conversion failed ({file_extension}), falling back to original content: {e}")
                        with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                            temp_file.write(response.content)
                            temp_path = Path(temp_file.name)
                else:
                    # Create a temporary file with determined extension (video or already jpg)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                        temp_file.write(response.content)
                        temp_path = Path(temp_file.name)
                
                # Publish the downloaded file
                result = _publish_to_destination(
                    source=temp_path,
                    publish_destination_id=publish_destination_id,
                    metadata=metadata or {},
                    skip_bucket=skip_bucket,
                    silent=silent,
                    cross_bucket_mode=cross_bucket_mode or True,  # URL sources are like cross-bucket
                    batch_id=batch_id,
                    update_published=update_published,
                    is_history_navigation=is_history_navigation
                )
                
                # Clean up the temporary file
                temp_path.unlink()
                
                return result
                
            except Exception as e:
                return {"success": False, "error": f"Failed to download image: {str(e)}"}

        # Handle local file sources
        try:
            # Convert source to Path if it's a string
            source_path = Path(source) if isinstance(source, str) else source
            
            # Publish the local file
            result = _publish_to_destination(
                source=source_path,
                publish_destination_id=publish_destination_id,
                metadata=metadata or {},
                skip_bucket=skip_bucket,
                silent=silent,
                cross_bucket_mode=cross_bucket_mode,
                batch_id=batch_id,
                update_published=update_published,
                is_history_navigation=is_history_navigation
            )
            
            return result
            
        except Exception as e:
            return {"success": False, "error": f"Failed to publish image: {str(e)}"}
    except Exception as e:
        error(f"Publish failed: {e}")
        return {"success": False, "error": str(e)}

# ─── overlay helper ────────────────────────────────────────────────────────
def _send_overlay_prompt(screen_id: str, metadata: dict[str, Any]) -> None:
    """
    Fire an overlay to the given screen with whatever fields we can find
    in the metadata dict.  Missing keys are simply rendered blank.
    """
    from routes.display import send_overlay           # late import = no circular deps
    dest = get_destination(screen_id)                 # already cached by _load_json_once

    substitutions = {
        "PROMPT_TEXT"   : metadata.get("prompt", ""),
        "WORKFLOW_TEXT" : metadata.get("workflow", ""),
        "SEED"          : metadata.get("seed", ""),
        "GENERATION_TIME_SECONDS": metadata.get("generation_time_seconds", ""),
        "GENERATION_COST_GBP": metadata.get("generation_cost_gbp", ""),
        "DURATION"      : 30,
        "WHEN_GENERATED": metadata.get("when_generated", ""),
        "TIME_TO_GENERATE": metadata.get("time_to_generate", ""),
    }

    send_overlay(
        html="overlay_prompt.html.j2",
        screens=[screen_id],
        substitutions=substitutions,
        duration=30_000,        # 30 s
        clear=True
    )
    
# ─────────────────────────────────────────────────────────────────────────────
# Internal helper – bucket append
# ─────────────────────────────────────────────────────────────────────────────
def _record_publish(bucket: str, filename: str, when: str, source_metadata: dict = None, cross_bucket_mode: bool = False, file_extension: str = None, is_history_navigation: bool = False) -> None:
    """
    Persist publish information in multiple locations to maintain consistency:
    1. Update the bucket's bucket.json with published_meta
    2. Create/update the published info in a separate file at /output/<id>.<ext>.json
    3. Record in history stack if this is a new publish (not undo/redo)
    
    Args:
        bucket: The bucket ID
        filename: The published file name 
        when: ISO timestamp of when the publish happened
        source_metadata: Complete metadata for the published file
        cross_bucket_mode: If True, use cross-bucket URL format with /output/<id>.<ext>
        file_extension: Explicit file extension to use (including the dot, e.g. '.mp4')
        is_history_navigation: True if this is an undo/redo operation, False for new publishes
    """
    # Get API URL from environment
    VITE_API_URL = os.environ.get("VITE_API_URL", "http://localhost:5000/api").rstrip("/")
    
    # Determine if running in test mode
    is_test_mode = 'pytest' in sys.modules
    
    # Determine the extension to use for building URLs
    extension = ".jpg"  # Default
    bucket_dir = bucket_path(bucket)
    file_path = bucket_dir / filename if filename else None
    
    # Log information to help debug extension detection
    info(f"Publishing: bucket={bucket}, filename={filename}, cross_bucket_mode={cross_bucket_mode}, explicit_extension={file_extension}")
    
    if file_extension is not None:
        # Use explicit extension if provided
        extension = file_extension
    
    info(f"Final extension determined: {extension}")
    
    if not cross_bucket_mode:
        raw_url = f"/output/{bucket}/{filename}"
        thumbnail_url = f"/output/{bucket}/thumbnails/{filename}"
    else:
        raw_url = f"/output/{bucket}{extension}"
        
        if extension.lower() in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            thumbnail_url = raw_url
        elif extension.lower() in ['.mp4', '.webm', '.mov']:
            if is_test_mode:
                thumbnail_url = f"/api/generate/jpg_from_mp4?file={raw_url}"
            else:
                thumbnail_url = f"{VITE_API_URL}/generate/jpg_from_mp4?file={raw_url}"
            info(f"Using API URL for thumbnail: {thumbnail_url}")
        else:
            thumbnail_url = "/static/placeholder.jpg"
    
    info(f"Generated URLs: raw_url={raw_url}, thumbnail_url={thumbnail_url}")
    
    # Handle history tracking
    if not is_history_navigation:
        # This is a new publish - record in history stack
        from routes.publish_utils import record_new_publish
        try:
            history_result = record_new_publish(
                destination_id=bucket,
                filename=filename,
                published_at=when,
                raw_url=raw_url,
                thumbnail_url=thumbnail_url,
                metadata=source_metadata
            )
            info(f"[history] Recorded new publish in history: {history_result.get('success', False)}")
        except Exception as e:
            warning(f"[history] Failed to record new publish in history: {e}")
    
    # Always update the legacy published_meta for backward compatibility
    meta = load_meta(bucket)
    
    # Merge with existing published_meta instead of overwriting
    if "published_meta" not in meta:
        meta["published_meta"] = {}
    
    # Update the current published image info without destroying history
    meta["published_meta"]["filename"] = filename
    meta["published_meta"]["published_at"] = when
    meta["published_meta"]["raw_url"] = raw_url
    meta["published_meta"]["thumbnail_url"] = thumbnail_url
    
    if source_metadata:
        meta["published_meta"]["metadata"] = source_metadata
    
    # When this is history navigation, don't touch the history fields
    # (they were already updated by the history manager)
    if not is_history_navigation:
        # This is a regular publish, so the history fields should be updated by record_new_publish above
        pass
        
    save_meta(bucket, meta)
    
    base_output_dir = Path(bucket_dir).parent
    published_info_path = base_output_dir / f"{bucket}{extension}.json"
    
    if file_path and file_path.exists():
        source_sidecar = sidecar_path(file_path)
        
        if source_sidecar.exists():
            try:
                shutil.copy2(source_sidecar, published_info_path)
                info(f"Copied sidecar from {source_sidecar} to {published_info_path}")
                return
            except Exception as e:
                warning(f"Failed to copy sidecar: {e}")
    
    if source_metadata:
        try:
            with open(published_info_path, 'w', encoding='utf-8') as f:
                json.dump(source_metadata, indent=2, sort_keys=True, ensure_ascii=False, fp=f)
            info(f"Created published sidecar with provided metadata at {published_info_path}")
            return
        except Exception as e:
            warning(f"Failed to write metadata to published sidecar: {e}")
    
    target_file = base_output_dir / f"{bucket}{extension}"
    if os.path.exists(target_file):
        try:
            ensure_sidecar_for(target_file)
            created_sidecar = sidecar_path(target_file)
            if created_sidecar.exists() and created_sidecar != published_info_path:
                shutil.move(created_sidecar, published_info_path)
            info(f"Created published sidecar using ensure_sidecar_for at {published_info_path}")
        except Exception as e:
            error(f"Failed to create sidecar using ensure_sidecar_for: {e}")
            try:
                minimal_meta = {
                    "type": "image" if extension.lower() in [".jpg", ".jpeg", ".png"] else "video",
                    "published_at": when
                }
                with open(published_info_path, 'w', encoding='utf-8') as f:
                    json.dump(minimal_meta, indent=2, sort_keys=True, ensure_ascii=False, fp=f)
                info(f"Created minimal fallback sidecar at {published_info_path}")
            except Exception as e2:
                error(f"Failed even to create minimal fallback sidecar: {e2}")

def _publish_to_destination(
    source: Path,
    publish_destination_id: str,
    metadata: dict,
    skip_bucket: bool,
    silent: bool,
    cross_bucket_mode: bool = False,
    batch_id: str | None = None,
    update_published: bool = True,
    is_history_navigation: bool = False,
) -> dict:
    """Internal helper for publish_to_destination."""
    try:
        dest = get_destination(publish_destination_id)

        if dest.get("headless", False):
            bucket_filename = None
            if not skip_bucket:
                bucket_file_path = _append_to_bucket(
                    publish_destination_id,
                    source,
                    batch_id=batch_id,
                    metadata=metadata,
                )
                bucket_filename = bucket_file_path.name
            return {
                "success": True,
                "meta": {
                    "filename": bucket_filename or source.name,
                    "published_at": datetime.utcnow().isoformat() + "Z",
                    "headless": True
                }
            }

        source_filepath = source
        bucket_filename = None
        
        if not skip_bucket:
            bucket_file_path = _append_to_bucket(
                publish_destination_id,
                source,
                batch_id=batch_id,
                metadata=metadata,
            )
            source_filepath = bucket_file_path
            bucket_filename = bucket_file_path.name
        else:
            source_str = str(source)
            bucket_dir_str = str(bucket_path(publish_destination_id))
            is_from_same_bucket = source_str.startswith(bucket_dir_str)
            
            if is_from_same_bucket:
                bucket_filename = source.name
                info(f"Source is from same bucket, using filename: {bucket_filename}")

        file_extension = source_filepath.suffix if isinstance(source_filepath, Path) else None
        
        if (not file_extension or file_extension == "") and metadata and metadata.get("type", "").lower() == "video":
            file_extension = ".mp4" 
            info(f"Using .mp4 extension based on video metadata type")
        
        info(f"Detected file extension for publishing: {file_extension}")
            
        base_output_dir = Path(bucket_path(publish_destination_id)).parent
        
        if cross_bucket_mode:
            display_path = base_output_dir / f"{dest['id']}{file_extension}"
        else:
            display_path = source_filepath
            
            debug(f"Same-bucket publishing - source file is already in place at {display_path}")
            
            # Only copy to direct output path if we're updating the published pointer
            if update_published:
                direct_output_path = base_output_dir / f"{dest['id']}{file_extension}"
                debug(f"Same-bucket publishing - also copying to direct output path: {direct_output_path}")
                
                try:
                    shutil.copy2(source_filepath, direct_output_path)
                    direct_output_path.touch()
                except Exception as e:
                    error(f"Failed to copy to direct output path: {e}")
        
        # Only copy to display path if we're updating the published pointer
        if update_published and source_filepath != display_path:
            display_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_filepath, display_path)
            display_path.touch()

        effective_metadata = None
        
        if metadata:
            effective_metadata = metadata
            debug(f"Using explicitly provided metadata for publishing")
            
        elif source_filepath.exists():
            source_sidecar = sidecar_path(source_filepath)
            if source_sidecar.exists():
                try:
                    with open(source_sidecar, 'r', encoding='utf-8') as f:
                        effective_metadata = json.load(f)
                        debug(f"Using metadata from source sidecar: {source_sidecar}")
                except Exception as e:
                    warning(f"Failed to read metadata from source sidecar: {e}")
                    
        publish_time = datetime.utcnow().isoformat() + "Z"
        
        # Only update the published pointer if requested
        if update_published:
            # CRITICAL FIX: For same-bucket publishing, cross_bucket_mode should ALWAYS be False
            # to ensure immutable bucket URLs are generated correctly
            actual_cross_bucket_mode = cross_bucket_mode and (source_filepath != display_path)
            
            _record_publish(
                bucket=publish_destination_id,
                filename=bucket_filename or source_filepath.name, 
                when=publish_time,
                source_metadata=effective_metadata,
                cross_bucket_mode=actual_cross_bucket_mode,
                file_extension=file_extension,
                is_history_navigation=is_history_navigation
            )

            if not silent and effective_metadata:
                _send_overlay_prompt(publish_destination_id, effective_metadata)

        return {
            "success": True,
            "meta": {
                "filename": bucket_filename or source_filepath.name,
                "published_at": publish_time
            }
        }
    except Exception as e:
        error(f"Publish failed: {e}")
        return {"success": False, "error": str(e)}

def get_published_info(publish_destination_id: str) -> dict:
    """
    Get information about the currently published image for a destination.
    This function is for internal use by other modules.
    
    Args:
        publish_destination_id: ID of the destination to check
        
    Returns:
        Dictionary with published info or empty dict if not found
    """
    debug(f"get_published_info called for destination: {publish_destination_id}")
    try:
        VITE_API_URL = os.environ.get("VITE_API_URL", "http://localhost:5000/api").rstrip("/")
        
        dest = get_destination(publish_destination_id)
        
        from routes.bucketer import meta_path
        bucket_meta_path = meta_path(publish_destination_id)
        debug(f"Bucket meta path: {bucket_meta_path}")
        
        if bucket_meta_path and os.path.exists(bucket_meta_path):
            debug(f"Bucket meta exists, attempting to read")
            try:
                with open(bucket_meta_path, 'r') as f:
                    bucket_meta = json.load(f)
                    debug(f"Bucket meta loaded successfully")
                    
                    if "published_meta" in bucket_meta and bucket_meta["published_meta"]:
                        published_meta = bucket_meta["published_meta"]
                        published_file = published_meta.get("filename")
                        published_at = published_meta.get("published_at", "")
                        raw_url = published_meta.get("raw_url")
                        thumbnail_url = published_meta.get("thumbnail_url")
                        
                        debug(f"Found published file in published_meta: {published_file}")
                        debug(f"URLs from published_meta: raw_url={raw_url}, thumbnail_url={thumbnail_url}")
                        
                        from routes.bucketer import bucket_path as get_bucket_path
                        bucket_dir = get_bucket_path(publish_destination_id)
                        base_output_dir = Path(bucket_dir).parent
                        
                        file_ext = Path(published_file).suffix if published_file else ".jpg"
                        
                        published_info_path = base_output_dir / f"{publish_destination_id}{file_ext}.json"
                        meta = {}
                        
                        if published_info_path.exists():
                            try:
                                with open(published_info_path, 'r') as f:
                                    meta = json.load(f)
                                    debug(f"Using metadata from published info file at {published_info_path}")
                            except Exception as e:
                                debug(f"Error reading published info file: {e}")
                                
                                try:
                                    published_source_path = bucket_dir / published_file
                                    if published_source_path.exists():
                                        source_sidecar = sidecar_path(published_source_path)
                                        if source_sidecar.exists():
                                            with open(source_sidecar, 'r') as f:
                                                meta = json.load(f)
                                                debug(f"Using metadata from source sidecar: {source_sidecar}")
                                except Exception as nested_e:
                                    debug(f"Error reading source sidecar: {nested_e}")
                        
                        if not meta and "metadata" in published_meta:
                            meta = published_meta.get("metadata", {})
                            debug(f"Using metadata from published_meta")
                        
                        return {
                            "published": published_file,
                            "published_at": published_at,
                            "raw_url": raw_url,
                            "thumbnail_url": thumbnail_url,
                            "meta": meta
                        }
                    
                    else:
                        debug(f"No published_meta in bucket_meta or it's empty")
            except Exception as e:
                error(f"Error reading bucket metadata for {publish_destination_id}: {e}")
        else:
            debug(f"No bucket meta found, falling back to get_image_from_target")
        
        debug(f"Using get_image_from_target as fallback")
        image_info = get_image_from_target(publish_destination_id, thumbnail=False)
        debug(f"get_image_from_target returned {'success' if image_info else 'None'}")
        
        if image_info and "local_path" in image_info:
            file_path = Path(image_info["local_path"])
            raw_name = image_info.get("raw_name", file_path.name)
            debug(f"Found image: {raw_name} at {file_path}")
            
            meta = {}
            base_output_dir = file_path.parent
            published_info_path = base_output_dir / f"{publish_destination_id}{file_path.suffix}.json"
            
            if published_info_path.exists():
                try:
                    with open(published_info_path, 'r') as f:
                        meta = json.load(f)
                        debug(f"Using metadata from published info file")
                except Exception as e:
                    debug(f"Error reading published info file: {e}")
            
            if not meta:
                from routes.utils import sidecar_path
                sc_path = sidecar_path(file_path)
                
                if sc_path and os.path.exists(sc_path):
                    debug(f"Found sidecar at {sc_path}")
                    try:
                        with open(sc_path, 'r') as f:
                            meta = json.load(f)
                    except Exception as e:
                        debug(f"Error reading sidecar: {e}")
                        pass
            
            raw_url = image_info.get("raw_url", str(file_path))
            
            file_suffix = file_path.suffix.lower()
            if file_suffix in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                thumbnail_url = raw_url
            elif file_suffix in ['.mp4', '.webm', '.mov']:
                thumbnail_url = f"{VITE_API_URL}/generate/jpg_from_mp4?file={raw_url}"
                info(f"Using API URL for thumbnail: {thumbnail_url}")
            else:
                thumbnail_url = "/static/placeholder.jpg"
            
            try:
                from routes.bucketer import load_meta
                bucket_meta = load_meta(publish_destination_id)
                if "published_meta" in bucket_meta and bucket_meta["published_meta"]:
                    debug(f"Replacing raw_name with published_meta filename")
                    raw_name = bucket_meta["published_meta"]["filename"]
                    if "raw_url" in bucket_meta["published_meta"]:
                        raw_url = bucket_meta["published_meta"]["raw_url"]
                    if "thumbnail_url" in bucket_meta["published_meta"]:
                        thumbnail_url = bucket_meta["published_meta"]["thumbnail_url"]
            except Exception as e:
                debug(f"Error accessing bucket metadata: {e}")
            
            return {
                "published": raw_name,
                "published_at": meta.get("when_generated", ""),
                "raw_url": raw_url,
                "thumbnail_url": thumbnail_url,
                "meta": meta
            }
        else:
            debug(f"No image found for {publish_destination_id}")
        
        debug(f"No published info found for {publish_destination_id}")
        return {}
        
    except Exception as e:
        error(f"Error in get_published_info: {e}")
        return {}

def display_from_bucket(
    publish_destination_id: str,
    mode: str,
    silent: bool = False
) -> dict:
    """
    Display an image from a bucket based on the specified mode.
    
    Args:
        publish_destination_id: ID of the destination to display to
        mode: Display mode - "Next", "Random", "Previous", or "Blank"
        silent: If True, suppress overlay display
        
    Returns:
        dict with success status and optional error message
    """
    debug(f"display_from_bucket called for {publish_destination_id} with mode={mode}, silent={silent}")
    
    if mode not in ["Next", "Random", "Previous", "Blank"]:
        error(f"Invalid display mode: {mode}. Must be 'Next', 'Random', 'Previous', or 'Blank'.")
        return {"success": False, "error": f"Invalid display mode: {mode}. Must be 'Next', 'Random', 'Previous', or 'Blank'."}

    if mode == "Blank":
        debug(f"Using blank mode for {publish_destination_id}")
        return publish_to_destination(
            source="",
            publish_destination_id=publish_destination_id,
            blank=True,
            silent=silent
        )

    from routes.bucket_api import load_meta
    meta = load_meta(publish_destination_id)
    favorites = meta.get("favorites", [])
    debug(f"Found {len(favorites)} favorites in bucket {publish_destination_id}")
    
    if not favorites:
        error(f"No favorites found in bucket {publish_destination_id}.")
        return {"success": False, "error": f"No favorites found in bucket {publish_destination_id}."}

    published_info = get_published_info(publish_destination_id)
    current_image = published_info.get("published")
    debug(f"Current published image for {publish_destination_id}: {current_image}")
    
    if mode == "Next" or mode == "Previous":
        debug(f"{mode} mode - checking if current image {current_image} is in favorites")
        
        current_pos = -1
        
        if current_image in favorites:
            current_pos = favorites.index(current_image)
            debug(f"Found current image directly at position {current_pos}")
        else:
            for i, fav in enumerate(favorites):
                if os.path.basename(fav) == current_image:
                    current_pos = i
                    debug(f"Found current image by basename at position {current_pos}")
                    break
        
        if current_pos >= 0:
            if mode == "Next":
                next_pos = (current_pos + 1) % len(favorites)
                filename = favorites[next_pos]
                debug(f"Current image found at position {current_pos} in favorites. Next position is {next_pos}, filename: {filename}")
            else:  # mode == "Previous"
                prev_pos = (current_pos - 1) % len(favorites)
                filename = favorites[prev_pos]
                debug(f"Current image found at position {current_pos} in favorites. Previous position is {prev_pos}, filename: {filename}")
        else:
            filename = favorites[0]
            debug(f"Current image not found in favorites. Using first favorite: {filename}")
    else:  # mode == "Random"
        filename = random.choice(favorites)
        debug(f"Random mode - selected: {filename}")

    from routes.bucket_api import bucket_path
    image_path = bucket_path(publish_destination_id) / filename
    debug(f"Image path constructed: {image_path}")

    if not image_path.exists():
        error(f"Selected file {filename} does not exist at path {image_path}")
        return {"success": False, "error": f"Selected file {filename} not found in bucket"}

    result = publish_to_destination(
        source=image_path,
        publish_destination_id=publish_destination_id,
        skip_bucket=True,
        silent=silent
    )
    
    debug(f"publish_to_destination result: {'success' if result.get('success') else 'failed'}")
    return result
