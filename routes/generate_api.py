"""
Generate API - Routes for generating content from other content
"""
from flask import Blueprint, request, jsonify, current_app
from pathlib import Path
import os
import json
from urllib.parse import unquote

from routes.manage_jobs import cancel_all_jobs as cancel_jobs
from utils.logger import log_to_console, info, error, warning, debug
from routes.generate_handler import jpg_from_mp4_handler, process_generate_image_request
from routes.utils import encode_image_uploads, encode_reference_urls

# Use the existing Blueprint
generate_api = Blueprint('generate_api', __name__)


@generate_api.route('/cancel_all_jobs', methods=['GET', 'POST'])
def cancel_all_jobs():
    try:
        cancelled = cancel_jobs()
        return jsonify({
            "success": True,
            "cancelled": cancelled,
            "message": f"Cancelled {cancelled} jobs"
        })
    except Exception as e:
        error(f"Error cancelling jobs: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@generate_api.route('/generate/jpg_from_mp4', methods=['GET'])
def jpg_from_mp4():
    """
    Extract the first frame from an MP4 file and return it as a JPG.
    
    Query parameters:
        file: The path to the MP4 file (can be a local path, /output/* path, or remote URL)
        
    Returns:
        JPG image of the first frame
    """
    try:
        # Get the file path from the query parameters
        file_path = request.args.get('file', '')
        if not file_path:
            error("Missing 'file' parameter")
            return "Missing 'file' parameter", 400
        
        # Decode URL-encoded path
        file_path = unquote(file_path)
        
        # Check if this is a web URL
        is_url = file_path.startswith(('http://', 'https://'))
        
        temp_file = None  # Track temp file for cleanup
        
        if is_url:
            # Download from remote URL
            try:
                info(f"Downloading video from URL: {file_path}")
                import requests
                import tempfile
                
                response = requests.get(file_path, stream=True)
                response.raise_for_status()
                
                # Create temp file with the right extension
                temp_fd, temp_path = tempfile.mkstemp(suffix='.mp4')
                os.close(temp_fd)
                
                # Save the content to the temp file
                with open(temp_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                
                debug(f"Downloaded video to temporary file: {temp_path}")
                file_path = temp_path
                temp_file = temp_path  # Track for cleanup
                
            except Exception as e:
                error(f"Failed to download from URL: {e}")
                return f"Error downloading file: {e}", 500
        
        elif file_path.startswith('/output/'):
            # Convert /output/ path to local filesystem path
            try:
                # Remove the /output/ prefix and get the absolute path
                relative_path = file_path[8:]  # Remove '/output/'
                
                # First try the configured OUTPUT_DIR
                output_dir = current_app.config.get('OUTPUT_DIR', './output')
                local_path = os.path.join(output_dir, relative_path)
                
                if not os.path.exists(local_path):
                    # If that fails, try a fallback to ./output
                    fallback_path = os.path.join('./output', relative_path)
                    if os.path.exists(fallback_path):
                        local_path = fallback_path
                    else:
                        error(f"Could not locate file at {local_path} or {fallback_path}")
                        return f"File not found at configured paths", 404
                
                file_path = local_path
                debug(f"Resolved /output/ path to: {file_path}")
                
            except Exception as e:
                error(f"Error resolving output path: {e}")
                return f"Error resolving path: {e}", 500
        
        # Now that we have a local file path (either original, downloaded, or resolved)
        # Check if the file exists and is an MP4
        path_obj = Path(file_path)
        if not path_obj.exists():
            error(f"File not found: {file_path}")
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)  # Clean up temp file
            return f"File not found: {file_path}", 404
        
        if path_obj.suffix.lower() not in ['.mp4', '.webm', '.mov']:
            error(f"Unsupported file type: {path_obj.suffix}")
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)  # Clean up temp file
            return f"Unsupported file type: {path_obj.suffix}", 400
        
        # Call the handler to extract the first frame
        try:
            result = jpg_from_mp4_handler(path_obj)
            
            # Clean up temp file if we created one
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)
                
            return result
            
        except Exception as e:
            # Clean up temp file if we created one
            if temp_file and os.path.exists(temp_file):
                os.unlink(temp_file)
                
            error(f"Error extracting frame: {e}")
            return f"Error extracting frame: {e}", 500
        
    except Exception as e:
        error(f"Error in jpg_from_mp4 endpoint: {e}")
        return f"Error: {e}", 500

#
# *** Generate Image ***
#


@generate_api.route('/generate-image', methods=['POST'])
def generate_image_route():
    """
    Generate image(s) from a prompt, optionally with reference images.
    """
    # Extract JSON data from the form
    json_data = request.form.get('data')
    if not json_data:
        error("Missing data parameter in request")
        return jsonify({"error": "Missing data parameter"}), 400
    
    try:
        data = json.loads(json_data)
    except Exception as e:
        error(f"Invalid JSON: {str(e)}")
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400

    info(f"Full blob: {data}")
    
    # Process reference URLs
    reference_urls = data.get('referenceUrls', [])
    info(f"[generate_image_route] Calling encode_reference_urls with {len(reference_urls)} URLs")
    images = encode_reference_urls(reference_urls, max_file_size_mb=5)
    info(f"[generate_image_route] Finished encode_reference_urls, got {len(images)} images")

    # Process uploaded files
    image_files = request.files.getlist('image')
    images.extend(encode_image_uploads(image_files, max_file_size_mb=5))

    # Log processed image details
    for img in images:
        info(f"Processed uploaded image '{img['name']}' with length {len(img['image'])}.")
    
    try:
        # Call the handler function with the processed data
        result = process_generate_image_request(data, uploaded_images=images)
        return jsonify(result)
    except ValueError as e:
        error(f"Value error in image generation: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        error(f"Error in image generation: {e}")
        import traceback
        error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500
