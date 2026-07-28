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
    ROOT_DIR, STATIC_FOLDER, OUTPUT_DIR, HOST, PORT, DEBUG, WS_PORT,
    API_PREFIX, LOG_LIMIT
)
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from utils.alerts import alert, init_alerting
from utils.alerts import health as alerts_health
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
from routes.audio_utils import audio_bp
from routes.audio_api import audio_api
from routes.admin_api import admin_api

# Load environment variables
load_dotenv()

# Configure logging
logging.getLogger('werkzeug').setLevel(logging.WARNING)

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
app.register_blueprint(audio_bp)
app.register_blueprint(audio_api, url_prefix=API_PREFIX)
app.register_blueprint(admin_api, url_prefix=API_PREFIX)

# Register test blueprints (no prefix needed for test routes)
app.register_blueprint(test_buckets_bp)
app.register_blueprint(test_scheduler_bp)

# Register utility blueprints (no prefix needed for utility routes)
app.register_blueprint(file_bp)

# Alerting (Siren): dispatch worker, threading.excepthook, Flask errorhandler.
# Installed before schedulers start so their failures are observable.
init_alerting(app)

# Initialize schedulers from saved states
with app.app_context():
    initialize_schedulers_from_disk()

# API Routes
@app.route(f'{API_PREFIX}/health', methods=['GET'])
def api_health():
    """Liveness/health payload; probed by the Deadman watchdog on the media
    server (?probe=deadman) and usable for ad-hoc diagnosis."""
    if request.args.get('probe') == 'deadman':
        alerts_health.note_watchdog_probe()
    return jsonify(alerts_health.build_health())

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

# Unknown API paths must 404 as JSON for every method, never fall through to
# the SPA page; Werkzeug matches all real /api rules before this fallback
@app.route(API_PREFIX, defaults={'api_path': ''})
@app.route(f'{API_PREFIX}/<path:api_path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def api_not_found(api_path):
    return jsonify({"error": "not found", "path": f'api/{api_path}'}), 404

# Legacy build/ assets: the Android APK lives outside the Vite build and the
# frontend requests it at both /build/sdk/... and /sdk/... paths
@app.route('/build/<path:asset_path>')
def serve_build_asset(asset_path):
    return send_from_directory(str(ROOT_DIR / 'build'), asset_path)

@app.route('/sdk/<path:asset_path>')
def serve_sdk_asset(asset_path):
    return send_from_directory(str(ROOT_DIR / 'build' / 'sdk'), asset_path)

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

def _run_ws_server_supervised():
    """The WS server used to run unsupervised: if its asyncio loop died,
    Flask carried on with no overlays, no RunPod progress relay and no lux
    ingestion. Restart it and tell the operator."""
    import time as _time
    while True:
        try:
            start_ws_server()
            alert("ws.server_died",
                  "Overlay WebSocket server exited; restarting in 5s",
                  severity="critical")
        except Exception as e:
            alert("ws.server_died",
                  "Overlay WebSocket server crashed; restarting in 5s",
                  severity="critical", exc=e)
        alerts_health.note_ws_restart()
        _time.sleep(5)

if __name__ == '__main__':
    info(f"Starting websockets server (to listen for front end messages on localhost:{WS_PORT}.")
    ws_thread = Thread(target=_run_ws_server_supervised, daemon=True, name="ws-server")
    ws_thread.start()
    alerts_health.register_ws_thread(ws_thread)

    if DEBUG:
        info(f"Starting Flask dev server on port {PORT} (DEBUG)")
        app.run(host=HOST, debug=True, port=PORT, use_reloader=False)
    else:
        from waitress import serve as waitress_serve
        info(f"Starting waitress server on port {PORT}")
        # Generation requests block their worker on external APIs for minutes;
        # the pool must stay large enough that kiosk /output polls never queue
        waitress_serve(app, host=HOST, port=PORT, threads=64)
