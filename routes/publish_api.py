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
            skip_bucket=skip_bucket
        )

        info(f"[publish_image] Result: {result}")
        return jsonify(result)

    except Exception as e:
        error(f"[publish_image] Error publishing image: {str(e)}")
        return jsonify({"error": f"Failed to publish image: {str(e)}"}), 500

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
        "meta":         {…} or {}
      }
    Always 200.
    """
    from routes.utils import get_image_from_target
    
    # First get the published info
    published_info = get_published_info(publish_destination_id)
    
    if not published_info:
        return jsonify({
            "published":     None,
            "published_at":  None,
            "raw_url":       None,
            "thumbnail":     None,
            "meta":          {},
        })
        
    # Get the thumbnail for the published image
    # get_image_from_target is the correct utility to use as it knows how to
    # find images in the output directory and bucket system
    image_result = get_image_from_target(publish_destination_id, thumbnail=True)
    
    # Add thumbnail to the response
    result = {
        "published":     published_info.get("published"),
        "published_at":  published_info.get("published_at"),
        "raw_url":       published_info.get("raw_url"),
        "thumbnail":     image_result.get("image") if image_result else None,
        "meta":          published_info.get("meta", {})
    }
        
    return jsonify(result)

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