from flask import Blueprint, jsonify, request, abort
from routes.utils import _load_json_once, findfile, get_image_from_target
from routes.publisher import publish_to_destination, display_from_bucket, get_destination
import logging
from utils.logger import info, error, warning, debug

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

        if not publish_destination_id:
            return jsonify({"error": "publish_destination_id is required"}), 400

        if not source_url:
            return jsonify({"error": "source_url is required"}), 400

        # Validate destination exists
        if publish_destination_id not in [d['id'] for d in publish_destinations]:
            return jsonify({"error": f"Invalid destination: {publish_destination_id}"}), 400

        # Publish to the destination (this will handle bucket append if needed)
        result = publish_to_destination(
            url=source_url,
            publish_destination_id=publish_destination_id,
            metadata=generation_info,
            skip_bucket=skip_bucket
        )

        return jsonify(result)

    except Exception as e:
        logging.error(f"Error publishing image: {str(e)}")
        return jsonify({"error": "Failed to publish image"}), 500

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
    # Verify this is a valid destination
    dest = next((d for d in publish_destinations if d["id"] == publish_destination_id), None)
    if not dest:
        abort(400, "Invalid publish_destination_id")
    
    # Get the published info from the destination's metadata
    pm = dest.get("published_meta") or {}
    filename = pm.get("filename")
    published_at = pm.get("published_at")

    # Get the image data
    image_result = get_image_from_target(publish_destination_id, thumbnail=True)
    image_data = image_result.get("image") if image_result else None

    if not image_data:
        return jsonify({
            "published":     None,
            "published_at":  None,
            "raw_url":       None,
            "thumbnail":     None,
            "meta":          {},
        })

    raw_url = image_result.get("raw_url")
    local_path = image_result.get("local_path")
    raw_name = image_result.get("raw_name")

    return jsonify({
        "published":     filename or raw_name,
        "published_at":  published_at,
        "raw_url":       raw_url,
        "thumbnail":     image_data,
        "meta":          pm.get("meta", {})
    })

@publish_api.route("/publish/<publish_destination_id>/display", methods=["POST"])
def api_display_from_bucket(publish_destination_id):
    """
    Display an image from a bucket based on the specified mode.
    
    Request body:
    {
        "mode": "Next" | "Random" | "Blank",
        "silent": bool (optional, defaults to false)
    }
    """
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