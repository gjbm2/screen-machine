from flask import Blueprint, jsonify, request
from routes.utils import _load_json_once, findfile
from routes.publisher import publish_remote_asset, publish_to_destination
import os
import logging

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

@publish_api.route('/buckets/<bucket>/publish/<filename>', methods=['POST'])
def publish_image(bucket, filename):
    """Publish an image to a destination"""
    try:
        data = request.get_json()
        destination_id = data.get('destination_id')
        generation_info = data.get('generation_info', {})

        # Validate destination exists
        if destination_id and destination_id not in [d['id'] for d in publish_destinations]:
            return jsonify({"error": f"Invalid destination: {destination_id}"}), 400

        # Construct the image URL
        image_url = f"/buckets/{bucket}/{filename}"

        # Try the new way first (destination ID)
        if destination_id:
            try:
                result = publish_to_destination(image_url, destination_id, generation_info)
                return jsonify(result), (200 if result.get("success") else 500)
            except KeyError as e:
                return jsonify({"success": False, "error": str(e)}), 400

        # Fall back to legacy way if needed
        destination_type = data.get('destination_type')
        destination_file = data.get('destination_file')
        if destination_type and destination_file:
            result = publish_remote_asset(image_url, destination_type, destination_file, generation_info)
            return jsonify(result), (200 if result.get("success") else 500)

        return jsonify({
            "success": False,
            "error": "Required fields: destination_id or (destination_type + destination_file)"
        }), 400

    except Exception as e:
        logging.error(f"Error publishing image: {str(e)}")
        return jsonify({"error": "Failed to publish image"}), 500 