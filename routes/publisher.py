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
    _record_publish
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
    blank: bool = False
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

    Returns:
        dict with success status and optional error message
    """
    # Handle blank screen case first
    if blank:
        blank_path = Path("output/_blank.jpg")
        if not blank_path.exists():
            # Try to create a blank image if it doesn't exist
            try:
                from PIL import Image
                # Ensure output directory exists
                os.makedirs("output", exist_ok=True)
                Image.new('RGB', (1, 10), color='black').save(blank_path)
                info(f"Created blank screen image at {blank_path}")
            except Exception as e:
                error(f"Failed to create blank screen image: {e}")
                return {"success": False, "error": f"Blank screen image not found and could not be created: {e}"}
        
        # Publish blank screen, always skip bucket and suppress overlay
        result = _publish_to_destination(
            source=blank_path,
            publish_destination_id=publish_destination_id,
            metadata={},
            skip_bucket=True,
            silent=silent
        )
        return result

    # Determine if source is a URL or local file
    is_url = isinstance(source, str) and source.startswith(("http://", "https://"))
    is_local = not is_url

    # If skip_bucket is None, determine based on source type
    if skip_bucket is None:
        skip_bucket = is_local  # Skip bucket for local files by default

    # Handle URL sources
    if is_url:
        try:
            # Download the image
            response = requests.get(source)
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
                silent=silent
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
            silent=silent
        )
        
        return result
        
    except Exception as e:
        return {"success": False, "error": f"Failed to publish image: {str(e)}"}

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
        "WIDTH"         : metadata.get("width", ""),
        "HEIGHT"        : metadata.get("height", ""),
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
def _publish_to_destination(
    source: Path,
    publish_destination_id: str,
    metadata: dict,
    skip_bucket: bool,
    silent: bool
) -> dict:
    """Internal helper for publish_to_destination."""
    try:
        dest = get_destination(publish_destination_id)

        # If headless, skip publishing to output, but still append to bucket
        if dest.get("headless", False):
            bucket_filename = None
            if not skip_bucket:
                bucket_path = _append_to_bucket(publish_destination_id, source, metadata)
                bucket_filename = bucket_path.name
            return {
                "success": True,
                "meta": {
                    "filename": bucket_filename or source.name,
                    "published_at": datetime.utcnow().isoformat() + "Z",
                    "headless": True
                }
            }

        # First, optionally append to bucket
        bucket_filename = None
        if not skip_bucket:
            bucket_path = _append_to_bucket(publish_destination_id, source, metadata)
            # Use the bucket version as the source for publishing
            source = bucket_path
            bucket_filename = bucket_path.name

        # Copy to /output/<id>.ext for display
        display_path = Path(f"output/{dest['id']}{source.suffix}")
        shutil.copy2(source, display_path)
        display_path.touch()  # Ensure fresh mtime for watchdogs

        # Create sidecar with provided metadata
        sc_path = sidecar_path(display_path)
        if not sc_path.exists() and metadata:
            # Use the provided metadata to create the sidecar
            try:
                with open(sc_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=2)
                info(f"Created sidecar with provided metadata: {sc_path}")
            except Exception as e:
                warning(f"Failed to create sidecar with provided metadata: {e}")
                # Fall back to standard extraction if direct creation fails
                ensure_sidecar_for(display_path)
        else:
            # Fall back to standard extraction if no metadata was provided
            ensure_sidecar_for(display_path)

        # Record publish with bucket filename
        _record_publish(publish_destination_id, bucket_filename or source.name, datetime.utcnow().isoformat() + "Z")

        # Show overlay if not silent
        if not silent:
            _send_overlay_prompt(publish_destination_id, metadata)

        return {
            "success": True,
            "meta": {
                "filename": bucket_filename or source.name,
                "published_at": datetime.utcnow().isoformat() + "Z"
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
        # Validate the destination exists
        dest = get_destination(publish_destination_id)
        
        # First try to get info from the bucket
        from routes.bucketer import meta_path
        bucket_meta_path = meta_path(publish_destination_id)
        debug(f"Bucket meta path: {bucket_meta_path}")
        
        if bucket_meta_path and os.path.exists(bucket_meta_path):
            debug(f"Bucket meta exists, attempting to read")
            try:
                with open(bucket_meta_path, 'r') as f:
                    bucket_meta = json.load(f)
                    # Check if there's a current published file
                    debug(f"Bucket meta content: {bucket_meta}")
                    
                    # First check published_meta
                    if "published_meta" in bucket_meta and bucket_meta["published_meta"]:
                        published_meta = bucket_meta["published_meta"]
                        published_file = published_meta.get("filename")
                        published_at = published_meta.get("published_at", "")
                        debug(f"Found published file in published_meta: {published_file}")
                        
                        # Get metadata about the file
                        from routes.bucket_api import bucket_path
                        full_path = bucket_path(publish_destination_id) / published_file
                        debug(f"Full path to published file: {full_path}")
                        
                        # Check for sidecar metadata
                        from routes.utils import sidecar_path
                        sc_path = sidecar_path(full_path)
                        meta = {}
                        
                        if sc_path and os.path.exists(sc_path):
                            debug(f"Sidecar found at {sc_path}, reading metadata")
                            try:
                                with open(sc_path, 'r') as f:
                                    meta = json.load(f)
                            except Exception as e:
                                debug(f"Error reading sidecar: {e}")
                                pass
                                
                        return {
                            "published": published_file,
                            "published_at": published_at,
                            "raw_url": str(full_path),
                            "meta": meta
                        }
                    # Fallback to deprecated "published" key
                    elif "published" in bucket_meta and bucket_meta["published"]:
                        published_file = bucket_meta["published"]
                        published_at = bucket_meta.get("published_at", "")
                        debug(f"Found published file in bucket meta: {published_file}")
                        
                        # Get metadata about the file
                        from routes.bucket_api import bucket_path
                        full_path = bucket_path(publish_destination_id) / published_file
                        debug(f"Full path to published file: {full_path}")
                        
                        # Check for sidecar metadata
                        from routes.utils import sidecar_path
                        sc_path = sidecar_path(full_path)
                        meta = {}
                        
                        if sc_path and os.path.exists(sc_path):
                            debug(f"Sidecar found at {sc_path}, reading metadata")
                            try:
                                with open(sc_path, 'r') as f:
                                    meta = json.load(f)
                            except Exception as e:
                                debug(f"Error reading sidecar: {e}")
                                pass
                                
                        return {
                            "published": published_file,
                            "published_at": published_at,
                            "raw_url": str(full_path),
                            "meta": meta
                        }
                    else:
                        debug(f"No published key in bucket meta or it's empty")
            except Exception as e:
                error(f"Error reading bucket metadata for {publish_destination_id}: {e}")
        else:
            debug(f"No bucket meta found, falling back to get_image_from_target")
        
        # If no bucket info, use get_image_from_target as a fallback
        # This properly checks output directories and understands the system conventions
        debug(f"Using get_image_from_target as fallback")
        image_info = get_image_from_target(publish_destination_id, thumbnail=False)
        debug(f"get_image_from_target returned: {image_info}")
        
        if image_info and "local_path" in image_info:
            file_path = Path(image_info["local_path"])
            raw_name = image_info.get("raw_name", file_path.name)
            debug(f"Found image: {raw_name} at {file_path}")
            
            # Check for sidecar metadata
            from routes.utils import sidecar_path
            sc_path = sidecar_path(file_path)
            meta = {}
            
            if sc_path and os.path.exists(sc_path):
                debug(f"Found sidecar at {sc_path}")
                try:
                    with open(sc_path, 'r') as f:
                        meta = json.load(f)
                except Exception as e:
                    debug(f"Error reading sidecar: {e}")
                    pass
            
            # Use the raw URL if available, otherwise use the local path
            raw_url = image_info.get("raw_url", str(file_path))
            
            return {
                "published": raw_name,
                "published_at": meta.get("when_generated", ""),
                "raw_url": raw_url,
                "meta": meta
            }
        else:
            debug(f"No image found for {publish_destination_id}")
        
        # Nothing found
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
        mode: Display mode - "Next", "Random", or "Blank"
        silent: If True, suppress overlay display
        
    Returns:
        dict with success status and optional error message
    """
    debug(f"display_from_bucket called for {publish_destination_id} with mode={mode}, silent={silent}")
    
    if mode not in ["Next", "Random", "Blank"]:
        error(f"Invalid display mode: {mode}. Must be 'Next', 'Random', or 'Blank'.")
        return {"success": False, "error": f"Invalid display mode: {mode}. Must be 'Next', 'Random', or 'Blank'."}

    # Handle Blank mode
    if mode == "Blank":
        debug(f"Using blank mode for {publish_destination_id}")
        return publish_to_destination(
            source="",  # Source is ignored when blank=True
            publish_destination_id=publish_destination_id,
            blank=True,
            silent=silent
        )

    # Get the bucket's metadata to find favorites
    from routes.bucket_api import load_meta
    meta = load_meta(publish_destination_id)
    favorites = meta.get("favorites", [])
    debug(f"Found {len(favorites)} favorites in bucket {publish_destination_id}")
    
    if not favorites:
        error(f"No favorites found in bucket {publish_destination_id}.")
        return {"success": False, "error": f"No favorites found in bucket {publish_destination_id}."}

    # Get the currently published image
    published_info = get_published_info(publish_destination_id)
    current_image = published_info.get("published")
    debug(f"Current published image for {publish_destination_id}: {current_image}")
    
    # Select the next image based on mode
    if mode == "Next":
        debug(f"Next mode - checking if current image {current_image} is in favorites")
        
        # The current_image might be a simple filename or the full path
        # Try to match it in different ways
        current_pos = -1
        
        # First try direct match
        if current_image in favorites:
            current_pos = favorites.index(current_image)
            debug(f"Found current image directly at position {current_pos}")
        else:
            # Try to match just the filename part (without directory)
            for i, fav in enumerate(favorites):
                if os.path.basename(fav) == current_image:
                    current_pos = i
                    debug(f"Found current image by basename at position {current_pos}")
                    break
        
        if current_pos >= 0:
            # Found current position, get next favorite
            next_pos = (current_pos + 1) % len(favorites)
            filename = favorites[next_pos]
            debug(f"Current image found at position {current_pos} in favorites. Next position is {next_pos}, filename: {filename}")
        else:
            # If current image not in favorites, start from first favorite
            filename = favorites[0]
            debug(f"Current image not found in favorites. Using first favorite: {filename}")
    else:  # Random
        # For random, we can just pick any favorite
        filename = random.choice(favorites)
        debug(f"Random mode - selected: {filename}")

    # Get the full path to the image
    from routes.bucket_api import bucket_path
    image_path = bucket_path(publish_destination_id) / filename
    debug(f"Image path constructed: {image_path}")

    # Check if the selected file actually exists
    if not image_path.exists():
        error(f"Selected file {filename} does not exist at path {image_path}")
        return {"success": False, "error": f"Selected file {filename} not found in bucket"}

    # Publish the image to the destination
    debug(f"Publishing image {filename} to {publish_destination_id}")
    result = publish_to_destination(
        source=image_path,
        publish_destination_id=publish_destination_id,
        skip_bucket=True,  # Always skip bucket since we're displaying from a bucket
        silent=silent
    )
    
    debug(f"publish_to_destination result: {result}")
    return result
