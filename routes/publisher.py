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
    generate_thumbnail
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
        blank_path = Path("/output/_blank.jpg")
        if not blank_path.exists():
            return {"success": False, "error": "Blank screen image not found"}
        
        # Publish blank screen, always skip bucket and suppress overlay
        result = _publish_to_destination(
            source=blank_path,
            publish_destination_id=publish_destination_id,
            metadata={},
            skip_bucket=True,
            silent=True
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
            
            # Create a temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as temp_file:
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
        
        # First, optionally append to bucket
        bucket_filename = None
        if not skip_bucket:
            bucket_path = _append_to_bucket(publish_destination_id, source)
            # Use the bucket version as the source for publishing
            source = bucket_path
            bucket_filename = bucket_path.name

        # Copy to /output/<id>.ext for display
        display_path = Path(f"output/{dest['id']}{source.suffix}")
        shutil.copy2(source, display_path)
        display_path.touch()  # Ensure fresh mtime for watchdogs

        # Ensure sidecar exists
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
    if mode not in ["Next", "Random", "Blank"]:
        return {"success": False, "error": f"Invalid display mode: {mode}. Must be 'Next', 'Random', or 'Blank'."}

    # Handle Blank mode
    if mode == "Blank":
        return publish_to_destination(
            source="",  # Source is ignored when blank=True
            publish_destination_id=publish_destination_id,
            blank=True
        )

    # Get the bucket's metadata to find favorites
    from routes.bucket_api import load_meta
    meta = load_meta(publish_destination_id)
    favorites = meta.get("favorites", [])
    
    if not favorites:
        return {"success": False, "error": f"No favorites found in bucket {publish_destination_id}."}

    # Get the currently published image
    from routes.publish_api import get_published
    published_info = get_published(publish_destination_id)
    current_image = published_info.get("filename") if published_info else None

    # Select the next image based on mode
    if mode == "Next":
        if current_image in favorites:
            # Find current position in favorites and get next favorite
            current_pos = favorites.index(current_image)
            next_pos = (current_pos + 1) % len(favorites)
            filename = favorites[next_pos]
        else:
            # If current image not in favorites, start from first favorite
            filename = favorites[0]
    else:  # Random
        # For random, we can just pick any favorite
        filename = random.choice(favorites)

    # Get the full path to the image
    from routes.bucket_api import bucket_path
    image_path = bucket_path(publish_destination_id) / filename

    # Publish the image to the destination
    return publish_to_destination(
        source=image_path,
        publish_destination_id=publish_destination_id,
        skip_bucket=True,  # Always skip bucket since we're displaying from a bucket
        silent=silent
    )
