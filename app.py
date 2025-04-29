from dotenv import load_dotenv
load_dotenv()  # Load .env vars into os.environ

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import re
import time
import random
import uuid
import json
import inspect
import sys
import ast
from typing import get_origin, get_args, Union
import requests
import types
from werkzeug.utils import secure_filename
from utils.logger import log_to_console, info, error, warning, debug, console_logs
import routes.generate
from routes.generate import detect_file_type, save_jpeg_with_metadata, save_video_with_metadata
import routes.alexa
import subprocess
import logging
from threading import Thread
from overlay_ws_server import start_ws_server, send_overlay_to_clients
from routes.utils import encode_image_uploads, encode_reference_urls
from routes.publisher import publish_remote_asset, publish_to_destination

app = Flask(__name__, static_folder='build')
CORS(app)  # Enable CORS for all routes

# Make sure we can find python packages
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Create uploads directory if it doesn't exist
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# In-memory dictionary to store generated images
generated_images = {}

# Sample placeholder images (replace with actual implementation)
PLACEHOLDER_IMAGES = [
    "https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=1974&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1605979257913-1704eb7b6246?q=80&w=1770&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1692891873526-61e7e87ea428?q=80&w=1780&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1533134486753-c833f0ed4866?q=80&w=1770&auto=format&fit=crop"
]

# Artistic style images
ARTISTIC_IMAGES = [
    "https://images.unsplash.com/photo-1579783902614-a3fb3927b6a5?q=80&w=1945&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1549289524-06cf8837ace5?q=80&w=1974&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1591267990532-e5bdb1b0ceb8?q=80&w=1974&auto=format&fit=crop"
]

# Bucket API
from routes.bucket_api import buckets_bp
app.register_blueprint(buckets_bp)

# Bucket API testing
from routes.test_buckets_ui import test_buckets_bp
app.register_blueprint(test_buckets_bp)

# Scheduler API
from routes.scheduler_api import scheduler_bp
app.register_blueprint(scheduler_bp)

# Scheduler Control Panel UI
from routes.test_scheduler_ui import test_scheduler_bp
app.register_blueprint(test_scheduler_bp)

# Scheduler Simulator UI
from routes.simulate_scheduler_ui import simulate_scheduler_handler
@app.route("/simulate-scheduler", methods=["GET", "POST"])
def simulate_scheduler():
    return simulate_scheduler_handler()

# Load workflow data from JSON files
def load_json_data(file_name):
    try:
        # First try to load from src/data directory
        src_data_path = os.path.join('src', 'data', file_name)
        if os.path.exists(src_data_path):
            with open(src_data_path, 'r') as file:
                info(f"Loaded data from {src_data_path}")
                return json.load(file)
        else:
            error(f"Data file not found in src/data: {file_name}")
            return []
    except Exception as e:
        error(f"Error loading {file_name}: {e}")
        return []

workflows_data = load_json_data('workflows.json')
refiners_data = load_json_data('refiners.json')
refiner_params_data = load_json_data('refiner-params.json')
global_options_data = load_json_data('global-options.json')

# Add a new endpoint for running scripts
@app.route('/api/run-script', methods=['POST'])
def run_script():
    try:
        data = request.json
        if not data or 'filename' not in data:
            return jsonify({"success": False, "error": "No filename provided"}), 400
        
        filename = data['filename']
        # Prevent path traversal attacks
        filename = os.path.basename(filename)
        
        # Determine the script type and location
        script_path = None
        # First check in routes directory
        routes_path = os.path.join('routes', filename)
        if os.path.exists(routes_path):
            script_path = routes_path
            info(f"Found script in routes directory: {script_path}")
        
        if not script_path:
            return jsonify({"success": False, "error": f"Script not found: {filename}"}), 404
        
        # Log the script execution
        info(f"Executing script: {script_path}")
        
        # For now, we'll just read the content of the script and return it
        # In a real implementation, you would execute the script based on its type
        with open(script_path, 'r') as file:
            script_content = file.read()
        
        # Different handling based on file extension
        if filename.endswith('.bat'):
            info(f"Would execute batch file: {script_path}")
            return jsonify({
                "success": True,
                "message": f"Batch script would be executed: {filename}",
                "content_preview": script_content[:100] + '...' if len(script_content) > 100 else script_content
            })
        elif filename.endswith('.json'):
            info(f"Would process JSON workflow: {script_path}")
            return jsonify({
                "success": True,
                "message": f"JSON workflow would be processed: {filename}",
                "content_preview": script_content[:100] + '...' if len(script_content) > 100 else script_content
            })
        else:
            info(f"Would process text file: {script_path}")
            return jsonify({
                "success": True,
                "message": f"Text file would be processed: {filename}",
                "content_preview": script_content[:100] + '...' if len(script_content) > 100 else script_content
            })
            
    except Exception as e:
        error_msg = f"Error executing script: {str(e)}"
        error(error_msg)
        return jsonify({"success": False, "error": error_msg}), 500

# Add a new route for publishing images
@app.route("/api/publish-image", methods=["POST"])
def publish_image():
    data = request.get_json(force=True, silent=True) or {}

    url        = data.get("imageUrl") or data.get("url")
    dest_id    = data.get("destination")            # new way
    meta       = data.get("metadata", {})

    if not url or not dest_id:
        # ── fall-back to legacy keys ────────────────────────────────────
        dtype = data.get("destinationType")
        dfile = data.get("destinationFile")
        if not url or not (dtype and dfile):
            return jsonify({
                "success": False,
                "error": "Required fields: imageUrl + destination "
                         "or imageUrl + destinationType + destinationFile"
            }), 400

        result = publish_remote_asset(url, dtype, dfile, meta)
        return jsonify(result), (200 if result.get("success") else 500)

    # ── preferred path: destination id only ─────────────────────────────
    try:
        result = publish_to_destination(url, dest_id, meta)
    except KeyError as e:                 # unknown destination id
        return jsonify({"success": False, "error": str(e)}), 400

    return jsonify(result), 200


# Add a new endpoint to get console logs
@app.route('/api/logs', methods=['GET'])
def get_logs():
    # Return the most recent logs first, with an optional limit parameter
    limit = request.args.get('limit', default=100, type=int)
    return jsonify({"logs": console_logs[-limit:] if limit > 0 else console_logs})

# Add a new endpoint to add logs from the frontend
@app.route('/api/log', methods=['POST'])
def add_log():
    data = request.json
    message = data.get('message', '')
    source = data.get('source', 'frontend')
    
    if message:
        log_entry = log_to_console(message, source=source)
        return jsonify({"status": "success", "log": log_entry})
    
    return jsonify({"status": "error", "message": "No log message provided"}), 400

def safe_cast(val, target_type, default=None):
    if val is None:
        return default

    try:
        target_type = unwrap_optional_type(target_type)
        print(f"Trying to cast {val!r} to {target_type}")

        if target_type is bool:
            return str(val).lower() in ("true", "1", "yes", "on")
        if target_type is int:
            return int(val)
        elif target_type is float:
            return float(val)
        elif target_type is str:
            return str(val)
        elif target_type in (list, dict):
            return ast.literal_eval(val)

        return target_type(val)
    except Exception as e:
        print(f"[safe_cast] Failed to cast {val!r} to {target_type}: {e}")
        return default


def unwrap_optional_type(tp):
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):  # support both `Union` and `int | None`
        args = [t for t in get_args(tp) if t is not type(None)]
        if len(args) == 1:
            return args[0]
    return tp

@app.route('/api/generate-image', methods=['POST'])
def generate_image():
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

    # Generate a unique ID for this batch
    batch_id = data.get('batch_id') or str(uuid.uuid4())
    
    prompt = data.get('prompt', '')
    workflow = data.get('workflow', 'text-to-image')
    params = data.get('params', {})
    global_params = data.get('global_params', {})

    # Only use refiner if this is the first image in this batch
    if sum(1 for img in generated_images.values() if img["batch_id"] == batch_id) > 0:
        refiner = "None"
        refiner_params = None
    else:
        refiner = data.get('refiner', 'none')
        refiner_params = data.get('refiner_params', {})

    batch_size = data.get('batch_size', 1)
    has_reference_image = data.get('has_reference_image', False)
    publish_destination = params.get('publish_destination', None)
    
    print(f"========================================================")
    #corrected_refiner = resolve_runtime_value("refiner", refiner, return_key="system_prompt")
    #print(f"  -> corrected_refiner: {corrected_refiner}")
   
    # Get valid parameter names from generate()
    main_params = inspect.signature(routes.generate.start).parameters
    call_args = {
        k: safe_cast(v, main_params[k].annotation if main_params[k].annotation is not inspect._empty else str)
        for k, v in params.items()
        if k in main_params
    }
    
    reference_urls = data.get('referenceUrls', [])

    # 1. Encode reference URLs first
    info(f"[generate_image] Calling encode_reference_urls with {len(reference_urls)} URLs")
    images = encode_reference_urls(reference_urls, max_file_size_mb=5)
    info(f"[generate_image] Finished encode_reference_urls, got {len(images)} images")


    # 2. Then encode uploaded files and append
    image_files = request.files.getlist('image')
    images.extend(encode_image_uploads(image_files, max_file_size_mb=5))

    # Log processed image details
    for img in images:
        info(f"Processed uploaded image '{img['name']}' with length {len(img['image'])}.")
   
    info(f"Generating images with prompt: {prompt}")
    #info(f"Workflow: {workflow}, Batch size: {batch_size}")
    #info(f"Reference images: {uploaded_files if uploaded_files else 'None'}")
    info(f"DEBUG: Workflow {workflow}, Params {params}")    
    #info(f"DEBUG: Call args: {call_args}") 
    
    # Generate
    send_obj = {
        "data": {
            "prompt": prompt,
            "images": images,
            "workflow": workflow,
            "refiner": refiner,
            "targets": publish_destination
        }
    }   
    #info(f"send_obj: {send_obj}")
    
    try:   
        response = routes.alexa.handle_image_generation(
            input_obj = send_obj,
            wait = True,
            **call_args
        )
    except Exception as e:
        error(f"Image generation failed: {e}")
        return jsonify({"error": str(e)}), 500
        
    if not response:
        error("Image generation returned no results.")
        return jsonify({"error": "Image generation returned no results."}), 500
    
    # Generate the requested number of images (based on batch_size)
    result_images = []
    
    for i, r in enumerate(response):            
        
        # Generate a unique ID for this image
        image_id = str(uuid.uuid4())
        
        # Store the image metadata in our dictionary
        used_workflow = r["input"]["workflow"]
        
        for q in generated_images.values():
            info(f"Batch ID: {q['batch_id']}, Batch Index: {q['batch_index']}")

        
        batch_index = max(
            (img["batch_index"] for img in generated_images.values() if img["batch_id"] == batch_id), default=0
        ) + 1
        
        image_data = {
            "id": image_id,
            "url": r.get("message", None),
            "seed": r["seed"],
            "original_prompt": prompt,
            "prompt": r["prompt"],
            "negative_prompt": r["negative_prompt"],
            "workflow": workflow,
            "full_workflow": r["input"],
            "timestamp":  int(time.time() * 1000),
            "params": params,
            "global_params": global_params,
            "refiner": refiner,
            "refiner_params": refiner_params,
            "used_reference_image": has_reference_image,
            "batch_id": batch_id,
            "batch_index": batch_index
        }
        
        #print(f"Delivering {image_data}")
        
        generated_images[image_id] = image_data
        result_images.append(image_data)
        info(f"Generated image {i+1}/{batch_size} with ID: {image_id}")
    
    return jsonify({
        "success": True,
        "images": result_images,
        "batch_id": batch_id,
        "prompt": prompt,
        "workflow": workflow
    })

@app.route('/api/images', methods=['GET'])
def get_images():
    # Return all stored images, sorted by timestamp (newest first)
    sorted_images = sorted(
        list(generated_images.values()), 
        key=lambda x: x["timestamp"], 
        reverse=True
    )
    info(f"Returning {len(sorted_images)} images")
    return jsonify({"images": sorted_images})

@app.route('/api/images/<image_id>', methods=['GET'])
def get_image(image_id):
    # Return a specific image by ID
    if image_id in generated_images:
        info(f"Retrieved image with ID: {image_id}")
        return jsonify(generated_images[image_id])
    error(f"Image not found: {image_id}")
    return jsonify({"error": "Image not found"}), 404

@app.route('/api/workflows', methods=['GET'])
def get_workflows():
    info(f"Returning {len(workflows_data)} workflows")
    return jsonify({"workflows": workflows_data})

@app.route('/api/refiners', methods=['GET'])
def get_refiners():
    info(f"Returning {len(refiners_data)} refiners")
    return jsonify({"refiners": refiners_data})

@app.route('/api/refiner-params/<refiner_id>', methods=['GET'])
def get_refiner_params(refiner_id):
    refiner_data = next((r for r in refiner_params_data if r["id"] == refiner_id), None)
    if refiner_data:
        info(f"Returning params for refiner: {refiner_id}")
        return jsonify(refiner_data)
    error(f"Refiner not found: {refiner_id}")
    return jsonify({"error": "Refiner not found"}), 404

@app.route('/api/global-options', methods=['GET'])
def get_global_options():
    info(f"Returning {len(global_options_data)} global options")
    return jsonify({"global_options": global_options_data})
    
@app.route("/api/alexa", methods=["POST"])
def alexa_webhook():
    data = request.get_json()
    #debug(f"Data {json.dumps(data, indent=2)}")
    
    response_ssml = routes.alexa.process(data)
    
    # Deliver response to Alexa
    response = {
        "version": "1.0",
        "response": {
            "outputSpeech": {
                "type": "SSML",
                "ssml": response_ssml
            },
            "shouldEndSession": True
        }
    }
    return jsonify(response)

# Add a new endpoint for extracting image metadata
@app.route('/api/extract-metadata', methods=['GET', 'POST'])
def extract_metadata():
    try:
        # Handle both GET and POST requests
        if request.method == 'POST':
            if request.is_json:
                data = request.json
                image_url = data.get('imageUrl')
            else:
                # Try to parse form data
                image_url = request.form.get('imageUrl')
        else:  # GET request
            image_url = request.args.get('url')
        
        if not image_url:
            return jsonify({"success": False, "error": "No image URL provided"}), 400
        
        info(f"Extracting metadata for image: {image_url}")
        
        # Generate sample metadata response for development purposes
        # This would be replaced with actual metadata extraction in production
        metadata = {
            "filename": image_url.split('/')[-1] if '/' in image_url else image_url,
            "source": "Extracted by metadata service",
            "timestamp": time.time(),
            "width": "1024",
            "height": "768",
            "format": "jpeg" if ".jpg" in image_url.lower() or ".jpeg" in image_url.lower() else "png",
            "size": "Unknown"
        }
        
        # Add some specific metadata fields based on URL patterns
        if "unsplash" in image_url.lower():
            metadata["provider"] = "Unsplash"
            metadata["license"] = "Unsplash License"
        
        # Return the metadata with success status
        return jsonify({"success": True, "metadata": metadata})
    
    except Exception as e:
        error_msg = f"Error extracting metadata: {str(e)}"
        error(error_msg)
        # Make sure we always return JSON, even in error cases
        return jsonify({"success": False, "error": error_msg}), 500

@app.route("/test-overlay", methods=["POST"])
def test_overlay():
    data = request.json
    info(f"Data: {data}")
    import routes.display
    routes.display.send_overlay(
        screens = data["screens"],
        html = data["htmlFile"],
        duration = data["duration"],
        position = data["position"],
        substitutions = data["substitutions"],
        clear = data["clear"]
    )
    return {"status": "sent"}

# Serve the React frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        if path.endswith('.js'):
            return send_from_directory(app.static_folder, path, mimetype='application/javascript')
        elif path.endswith('.mjs'):
            return send_from_directory(app.static_folder, path, mimetype='application/javascript')
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    info("Starting websockets server (to listen for front end messages on localhost:8765.")
    Thread(target=start_ws_server, daemon=True).start()
    
    info("Starting Flask server on port 5000")
    app.run(host="0.0.0.0", debug=True, port=5000, use_reloader=False)
