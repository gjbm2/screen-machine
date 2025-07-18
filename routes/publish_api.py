from flask import Blueprint, jsonify, request, abort
from routes.utils import _load_json_once, findfile, get_image_from_target
from routes.publisher import publish_to_destination, display_from_bucket, get_destination, get_published_info
import logging
from utils.logger import info, error, warning, debug
import json
from pathlib import Path

publish_api = Blueprint('publish_api', __name__)

# Load publish destinations from JSON file
PUBLISH_DESTINATIONS_FILE = findfile("publish-destinations.json")
publish_destinations = _load_json_once("publish_destinations", PUBLISH_DESTINATIONS_FILE)

@publish_api.route('/publish-destinations', methods=['GET'])
def get_publish_destinations():
    """Get all available publish destinations"""
    try:
        return jsonify(publish_destinations)
    except Exception as e:
        logging.error(f"Error getting publish destinations: {str(e)}")
        return jsonify({"error": "Failed to get publish destinations"}), 500

@publish_api.route('/publish', methods=['POST'])
def unified_publish_image():
    """
    Unified publish endpoint for all image sources.
    
    This endpoint handles two scenarios:
    1. Bucket-to-bucket publishing:
       - dest_bucket_id: Destination bucket ID
       - src_bucket_id: Source bucket ID
       - filename: The image filename
       
    2. External URL publishing:
       - dest_bucket_id: Destination bucket ID
       - source_url: Full URL to the image (including auth params)
       - metadata: Optional metadata about the image
       - skip_bucket: Whether to skip saving to bucket (default: false)
    """
    try:
        data = request.get_json()
        dest_bucket_id = data.get('dest_bucket_id')
        
        info(f"[unified_publish_image] RECEIVED: {data}")
        
        # Validate destination exists
        if not dest_bucket_id:
            error("[unified_publish_image] Missing dest_bucket_id")
            return jsonify({"error": "dest_bucket_id is required"}), 400
            
        if dest_bucket_id not in [d['id'] for d in publish_destinations]:
            error(f"[unified_publish_image] Invalid destination: {dest_bucket_id}")
            return jsonify({"error": f"Invalid destination: {dest_bucket_id}"}), 400
            
        # Route A: Bucket-to-bucket publishing
        if 'src_bucket_id' in data and 'filename' in data:
            src_bucket_id = data.get('src_bucket_id')
            filename = data.get('filename')
            
            info(f"[unified_publish_image] Using bucket-to-bucket publishing: {src_bucket_id}/{filename} -> {dest_bucket_id}")
            
            # Construct the local path directly
            from routes.bucket_api import bucket_path
            src_path = bucket_path(src_bucket_id) / filename
            
            if not src_path.exists():
                error(f"[unified_publish_image] File not found: {filename} in {src_bucket_id}")
                return jsonify({"error": f"File not found: {filename} in {src_bucket_id}"}), 404
                
            # Determine if this is a cross-bucket publishing or same-bucket publishing
            cross_bucket_mode = src_bucket_id != dest_bucket_id
            if cross_bucket_mode:
                info(f"[unified_publish_image] Cross-bucket publishing detected: {src_bucket_id} -> {dest_bucket_id}")
            else:
                info(f"[unified_publish_image] Same-bucket publishing detected: {src_bucket_id} -> {dest_bucket_id}")
                
            result = publish_to_destination(
                source=src_path,
                publish_destination_id=dest_bucket_id,
                # Metadata will be automatically loaded from sidecar by publish_to_destination
                skip_bucket=True,  # Always skip bucket append since image is already in a bucket
                cross_bucket_mode=cross_bucket_mode  # Set cross_bucket_mode flag based on buckets
            )
            
        # Route B: External URL publishing
        elif 'source_url' in data:
            source_url = data.get('source_url')
            metadata = data.get('metadata', {})
            
            info(f"[unified_publish_image] Using external URL publishing: {source_url[:100]}... -> {dest_bucket_id}")
            
            # External URLs should be treated as cross-bucket mode
            result = publish_to_destination(
                source=source_url,
                publish_destination_id=dest_bucket_id,
                metadata=metadata,
                skip_bucket=data.get('skip_bucket', False),
                cross_bucket_mode=True  # External URLs should use cross-bucket URL format
            )
            
        else:
            error("[unified_publish_image] Missing required parameters")
            return jsonify({"error": "Missing required parameters: either (src_bucket_id + filename) or source_url"}), 400
            
        info(f"[unified_publish_image] Result: {result}")
        return jsonify(result)
        
    except Exception as e:
        error(f"[unified_publish_image] Error publishing image: {str(e)}")
        return jsonify({"error": f"Failed to publish image: {str(e)}"}), 500

@publish_api.route('/publish/<filename>', methods=['POST'])
def publish_image(filename):
    """Publish an image to a destination"""
    try:
        data = request.get_json()
        publish_destination_id = data.get('publish_destination_id')
        generation_info = data.get('generation_info', {})
        source_url = data.get('source_url')  # URL of the image to publish
        skip_bucket = data.get('skip_bucket', False)  # Whether to skip saving to bucket

        info(f"[publish_image] RECEIVED: dest={publish_destination_id}, source={source_url}, skip_bucket={skip_bucket}")

        if not publish_destination_id:
            error("[publish_image] Missing publish_destination_id")
            return jsonify({"error": "publish_destination_id is required"}), 400

        if not source_url:
            error("[publish_image] Missing source_url")
            return jsonify({"error": "source_url is required"}), 400

        # Validate destination exists
        if publish_destination_id not in [d['id'] for d in publish_destinations]:
            error(f"[publish_image] Invalid destination: {publish_destination_id}")
            return jsonify({"error": f"Invalid destination: {publish_destination_id}"}), 400

        # Publish to the destination (this will handle bucket append if needed)
        info(f"[publish_image] Publishing to destination: {publish_destination_id}")
        result = publish_to_destination(
            source=source_url,
            publish_destination_id=publish_destination_id,
            metadata=generation_info,
            skip_bucket=skip_bucket,
            cross_bucket_mode=True  # URLs should use cross-bucket format
        )

        info(f"[publish_image] Result: {result}")
        return jsonify(result)

    except Exception as e:
        error(f"[publish_image] Error publishing image: {str(e)}")
        return jsonify({"error": f"Failed to publish image: {str(e)}"}), 500

@publish_api.route('/publish/publish', methods=['POST'])
def legacy_publish_route():
    """
    Legacy publish route for backward compatibility.
    
    This endpoint handles requests using the older API format:
    - publish_destination_id: Destination ID
    - source_url: URL to the image
    - generation_info: Optional metadata
    - skip_bucket: Whether to skip saving to bucket (default: false)
    """
    data = request.get_json()
    publish_destination_id = data.get('publish_destination_id')
    source_url = data.get('source_url')
    generation_info = data.get('generation_info', {})
    skip_bucket = data.get('skip_bucket', False)
    
    info(f"[legacy_publish_route] RECEIVED: dest={publish_destination_id}, source={source_url}, skip_bucket={skip_bucket}")
    
    # Automatically set skip_bucket=True if source_url is from a bucket
    if source_url and ('/api/buckets/' in source_url or '/output/' in source_url):
        info(f"[legacy_publish_route] Source appears to be from a bucket, setting skip_bucket=True")
        skip_bucket = True
    
    # Call publish_to_destination directly
    try:
        result = publish_to_destination(
            source=source_url,
            publish_destination_id=publish_destination_id,
            metadata=generation_info,
            skip_bucket=skip_bucket,
            cross_bucket_mode=True  # External URLs should use cross-bucket URL format
        )
        return jsonify(result)
    except Exception as e:
        error(f"[legacy_publish_route] Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@publish_api.route('/published/<publish_destination_id>', methods=['GET'])
def get_published(publish_destination_id: str):
    """
    Get published info for a destination.
    Returns JSON:
      {
        "published":    "<filename> or null",
        "published_at": "<ISO ts> or null",
        "raw_url":      "<URL> or null",
        "thumbnail":    "<base64 JPEG> or null",
        "thumbnail_url": "<URL> or null",
        "meta":         {…} or {}
      }
    Always returns 200 status code even when no published content exists.
    """
    from routes.utils import get_image_from_target
    
    # Log that we're trying to get published info
    info(f"[get_published] Getting published info for destination: {publish_destination_id}")
    
    try:
        # First get the published info
        published_info = get_published_info(publish_destination_id)
        
        if not published_info:
            # No info found, but we return a valid JSON result with nulls
            info(f"[get_published] No published info found for {publish_destination_id}, returning null values")
            return jsonify({
                "published":     None,
                "published_at":  None,
                "raw_url":       None,
                "thumbnail":     None,
                "thumbnail_url": None,
                "meta":          {},
                "filename":      None
            })
            
        # Get the thumbnail for the published image
        # get_image_from_target is the correct utility to use as it knows how to
        # find images in the output directory and bucket system
        image_result = get_image_from_target(publish_destination_id, thumbnail=True)
        
        if not image_result:
            # If we have publish info but no image could be found, log a warning but still return the info
            warning(f"[get_published] Published info exists for {publish_destination_id}, but no image was found")
        
        # Add thumbnail to the response
        result = {
            "published":     published_info.get("published"),
            "published_at":  published_info.get("published_at"),
            "raw_url":       published_info.get("raw_url"),
            "thumbnail_url": published_info.get("thumbnail_url"),
            "thumbnail":     image_result.get("image") if image_result else None,
            "meta":          published_info.get("meta", {}),
            "filename":      published_info.get("published") # Add filename for convenience
        }
        
        # Debug logging to help diagnose issues
        info(f"Published info for {publish_destination_id}: raw_url={result.get('raw_url')}, thumbnail_url={result.get('thumbnail_url')}")
            
        return jsonify(result)
        
    except Exception as e:
        # Log the error but still return a valid JSON response with nulls
        error(f"[get_published] Error getting published info for {publish_destination_id}: {e}")
        return jsonify({
            "published":     None,
            "published_at":  None,
            "raw_url":       None,
            "thumbnail":     None,
            "thumbnail_url": None,
            "meta":          {},
            "filename":      None,
            "error":         str(e)
        })

@publish_api.route("/publish/<publish_destination_id>/display", methods=["POST"])
def display_from_bucket_api(publish_destination_id: str):
    """Display an image from the bucket."""
    try:
        # Get the request data
        data = request.get_json()
        if not data or "mode" not in data:
            return jsonify({"error": "Request must include 'mode' parameter"}), 400
            
        mode = data["mode"]
        silent = data.get("silent", False)
        
        # Validate the destination exists
        get_destination(publish_destination_id)
        
        # Call display_from_bucket
        result = display_from_bucket(
            publish_destination_id=publish_destination_id,
            mode=mode,
            silent=silent
        )
        
        if not result.get("success"):
            return jsonify({"error": result.get("error")}), 400
            
        return jsonify({
            "status": "success",
            "message": f"Displayed {mode.lower()} image"
        })
        
    except KeyError as e:
        return jsonify({"error": f"Unknown publish destination: {str(e)}"}), 404
    except Exception as e:
        error(f"Error in display_from_bucket: {str(e)}")
        return jsonify({"error": str(e)}), 500


@publish_api.route('/undo/<publish_destination_id>', methods=['POST'])
def undo_publish_endpoint(publish_destination_id: str):
    """
    Undo the last publish operation for a destination.
    
    This moves the pointer back in the history stack and republishes
    the previous image without adding it as a new entry.
    
    Returns:
        JSON response with operation result and current state
    """
    from routes.publish_utils import undo_publish, get_publish_history_info
    from routes.publisher import publish_to_destination, _record_publish
    from routes.bucketer import bucket_path
    from pathlib import Path
    
    info(f"[undo] Undo request for destination: {publish_destination_id}")
    
    try:
        # Attempt to undo in history stack
        undo_result = undo_publish(publish_destination_id)
        
        if not undo_result.get("success"):
            info(f"[undo] Cannot undo for {publish_destination_id}: {undo_result.get('error')}")
            return jsonify({
                "success": False,
                "error": undo_result.get("error", "Cannot undo"),
                "can_undo": undo_result.get("can_undo", False),
                "can_redo": undo_result.get("can_redo", False)
            }), 400
        
        # Get the image we need to display
        current_image = undo_result.get("current_image")
        if not current_image:
            error(f"[undo] No current image returned from undo operation")
            return jsonify({"success": False, "error": "No image to display"}), 500
        
        filename = current_image.get("filename")
        if not filename:
            error(f"[undo] No filename in current image data")
            return jsonify({"success": False, "error": "Invalid image data"}), 500
        
        # Find the image file in the bucket
        bucket_dir = bucket_path(publish_destination_id)
        image_path = bucket_dir / filename
        
        if not image_path.exists():
            error(f"[undo] Image file not found: {image_path}")
            return jsonify({"success": False, "error": f"Image file {filename} not found"}), 404
        
        # Publish the image (this will display it but not add to history)
        info(f"[undo] Publishing previous image: {filename}")
        publish_result = publish_to_destination(
            source=image_path,
            publish_destination_id=publish_destination_id,
            skip_bucket=True,  # Don't add to bucket again
            silent=False,  # Show overlay
            metadata=current_image.get("metadata", {}),
            is_history_navigation=True  # Don't overwrite history pointer, but do update published_meta
        )
        
        if not publish_result.get("success"):
            error(f"[undo] Failed to publish previous image: {publish_result.get('error')}")
            return jsonify({"success": False, "error": "Failed to display previous image"}), 500
        
        # Note: We don't call _record_publish here because the history manager
        # already updated the pointer, and _record_publish would overwrite it
        
        info(f"[undo] Successfully undid to image: {filename}")
        return jsonify({
            "success": True,
            "current_image": {
                "filename": filename,
                "published_at": current_image.get("published_at"),
                "raw_url": current_image.get("raw_url"),
                "thumbnail_url": current_image.get("thumbnail_url")
            },
            "pointer_position": undo_result.get("pointer_position"),
            "stack_size": undo_result.get("stack_size"),
            "can_undo": undo_result.get("can_undo"),
            "can_redo": undo_result.get("can_redo")
        })
        
    except Exception as e:
        error(f"[undo] Error during undo operation for {publish_destination_id}: {e}")
        return jsonify({"success": False, "error": f"Undo operation failed: {str(e)}"}), 500


@publish_api.route('/redo/<publish_destination_id>', methods=['POST'])
def redo_publish_endpoint(publish_destination_id: str):
    """
    Redo a previously undone publish operation for a destination.
    
    This moves the pointer forward in the history stack and republishes
    the next image without adding it as a new entry.
    
    Returns:
        JSON response with operation result and current state
    """
    from routes.publish_utils import redo_publish, get_publish_history_info
    from routes.publisher import publish_to_destination, _record_publish
    from routes.bucketer import bucket_path
    from pathlib import Path
    
    info(f"[redo] Redo request for destination: {publish_destination_id}")
    
    try:
        # Attempt to redo in history stack
        redo_result = redo_publish(publish_destination_id)
        
        if not redo_result.get("success"):
            info(f"[redo] Cannot redo for {publish_destination_id}: {redo_result.get('error')}")
            return jsonify({
                "success": False,
                "error": redo_result.get("error", "Cannot redo"),
                "can_undo": redo_result.get("can_undo", False),
                "can_redo": redo_result.get("can_redo", False)
            }), 400
        
        # Get the image we need to display
        current_image = redo_result.get("current_image")
        if not current_image:
            error(f"[redo] No current image returned from redo operation")
            return jsonify({"success": False, "error": "No image to display"}), 500
        
        filename = current_image.get("filename")
        if not filename:
            error(f"[redo] No filename in current image data")
            return jsonify({"success": False, "error": "Invalid image data"}), 500
        
        # Find the image file in the bucket
        bucket_dir = bucket_path(publish_destination_id)
        image_path = bucket_dir / filename
        
        if not image_path.exists():
            error(f"[redo] Image file not found: {image_path}")
            return jsonify({"success": False, "error": f"Image file {filename} not found"}), 404
        
        # Publish the image (this will display it but not add to history)
        info(f"[redo] Publishing next image: {filename}")
        publish_result = publish_to_destination(
            source=image_path,
            publish_destination_id=publish_destination_id,
            skip_bucket=True,  # Don't add to bucket again
            silent=False,  # Show overlay
            metadata=current_image.get("metadata", {}),
            is_history_navigation=True  # Don't overwrite history pointer, but do update published_meta
        )
        
        if not publish_result.get("success"):
            error(f"[redo] Failed to publish next image: {publish_result.get('error')}")
            return jsonify({"success": False, "error": "Failed to display next image"}), 500
        
        # Note: We don't call _record_publish here because the history manager
        # already updated the pointer, and _record_publish would overwrite it
        
        info(f"[redo] Successfully redid to image: {filename}")
        return jsonify({
            "success": True,
            "current_image": {
                "filename": filename,
                "published_at": current_image.get("published_at"),
                "raw_url": current_image.get("raw_url"),
                "thumbnail_url": current_image.get("thumbnail_url")
            },
            "pointer_position": redo_result.get("pointer_position"),
            "stack_size": redo_result.get("stack_size"),
            "can_undo": redo_result.get("can_undo"),
            "can_redo": redo_result.get("can_redo")
        })
        
    except Exception as e:
        error(f"[redo] Error during redo operation for {publish_destination_id}: {e}")
        return jsonify({"success": False, "error": f"Redo operation failed: {str(e)}"}), 500


@publish_api.route('/history/<publish_destination_id>', methods=['GET'])
def get_publish_history_endpoint(publish_destination_id: str):
    """
    Get the current publish history information for a destination.
    
    Returns:
        JSON response with history stack status and navigation info
    """
    from routes.publish_utils import get_publish_history_info
    
    try:
        history_info = get_publish_history_info(publish_destination_id)
        
        return jsonify({
            "success": True,
            "current_pointer": history_info.get("current_pointer", 0),
            "stack_size": history_info.get("stack_size", 0),
            "can_undo": history_info.get("can_undo", False),
            "can_redo": history_info.get("can_redo", False),
            "current_image": history_info.get("current_image")
        })
        
    except Exception as e:
        error(f"[history] Error getting history info for {publish_destination_id}: {e}")
        return jsonify({"success": False, "error": f"Failed to get history info: {str(e)}"}), 500 