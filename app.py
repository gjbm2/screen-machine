"""
Main application entry point for the Screen Machine.
Handles route registration, server initialization, and core application setup.
"""

# Standard library imports
import os
import sys
import json
import logging
from threading import Thread
from pathlib import Path

# Third-party imports
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Local imports
from config import (
    STATIC_FOLDER, OUTPUT_DIR, HOST, PORT, DEBUG, WS_PORT,
    API_PREFIX, LOG_LIMIT
)
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from routes.generate import detect_file_type, save_jpeg_with_metadata, save_video_with_metadata
from routes.alexa import process as alexa_process
from routes.utils import encode_image_uploads, encode_reference_urls
from routes.publish_api import publish_api
from routes.generate_api import generate_api
from routes.bucket_api import buckets_bp
from routes.test_buckets_ui import test_buckets_bp
from routes.scheduler_api import scheduler_bp
from routes.test_scheduler_ui import test_scheduler_bp
from routes.simulate_scheduler_ui import simulate_scheduler_handler
from routes.file_api import file_bp
from routes.scheduler import initialize_schedulers_from_disk
from routes.display import send_overlay, mask_bp
from overlay_ws_server import start_ws_server, send_overlay_to_clients
from routes.lightsensor import lightsensor_bp

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__, static_folder=STATIC_FOLDER)
CORS(app)

# Add project root to Python path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

# Set app config
app.config['OUTPUT_DIR'] = OUTPUT_DIR

# Register API blueprints with consistent prefix
app.register_blueprint(publish_api, url_prefix=API_PREFIX)
app.register_blueprint(generate_api, url_prefix=API_PREFIX)
app.register_blueprint(buckets_bp, url_prefix=API_PREFIX)
app.register_blueprint(scheduler_bp, url_prefix=API_PREFIX)
app.register_blueprint(mask_bp, url_prefix=API_PREFIX)
app.register_blueprint(lightsensor_bp, url_prefix=API_PREFIX)

# Register test blueprints (no prefix needed for test routes)
app.register_blueprint(test_buckets_bp)
app.register_blueprint(test_scheduler_bp)

# Register utility blueprints (no prefix needed for utility routes)
app.register_blueprint(file_bp)

# Initialize schedulers from saved states
with app.app_context():
    initialize_schedulers_from_disk()

# API Routes
@app.route(f'{API_PREFIX}/logs', methods=['GET'])
def get_logs():
    """Retrieve recent application logs."""
    limit = request.args.get('limit', default=LOG_LIMIT, type=int)
    return jsonify({"logs": console_logs[-limit:] if limit > 0 else console_logs})

@app.route(f'{API_PREFIX}/log', methods=['POST'])
def add_log():
    """Add a log entry from the frontend."""
    data = request.json
    message = data.get('message', '')
    source = data.get('source', 'frontend')
    
    if message:
        log_entry = log_to_console(message, source=source)
        return jsonify({"status": "success", "log": log_entry})
    
    return jsonify({"status": "error", "message": "No log message provided"}), 400

@app.route(f"{API_PREFIX}/alexa", methods=["POST"])
def alexa_webhook():
    """Handle incoming Alexa webhook requests."""
    data = request.get_json()
    response_ssml = alexa_process(data)
    
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

# Test/Simulation Routes
@app.route("/test-overlay", methods=["POST"])
def test_overlay():
    """Test the overlay functionality with provided data."""
    data = request.json
    info(f"Data: {data}")
    send_overlay(
        screens=data["screens"],
        html=data["htmlFile"],
        duration=data["duration"],
        position=data["position"],
        substitutions=data["substitutions"],
        clear=data["clear"]
    )
    return {"status": "sent"}

@app.route("/simulate-scheduler", methods=["GET", "POST"])
def simulate_scheduler():
    """Handle scheduler simulation requests."""
    return simulate_scheduler_handler()

# Static File Serving
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    """Serve static files and handle React frontend routing."""
    # Handle output directory requests
    if path.startswith(f'{OUTPUT_DIR}/'):
        return send_from_directory(OUTPUT_DIR, path[len(OUTPUT_DIR)+1:])
    
    # Handle React frontend requests
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        if path.endswith('.js'):
            return send_from_directory(app.static_folder, path, mimetype='application/javascript')
        elif path.endswith('.mjs'):
            return send_from_directory(app.static_folder, path, mimetype='application/javascript')
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    info(f"Starting websockets server (to listen for front end messages on localhost:{WS_PORT}.")
    Thread(target=start_ws_server, daemon=True).start()
    
    info(f"Starting Flask server on port {PORT}")
    app.run(host=HOST, debug=DEBUG, port=PORT, use_reloader=False)
