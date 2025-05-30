from __future__ import annotations
import asyncio
import json
import ast
import os
from pathlib import Path
from flask import Blueprint, jsonify, abort
from routes.utils import findfile, dict_substitute, _load_json_once
from routes.display_utils import ambient_rgba
from overlay_ws_server import send_overlay_to_clients
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from datetime import datetime, timezone
import uuid

# Create Blueprint for mask routes
mask_bp = Blueprint("mask", __name__)


# ───────────────────── routes ─────────────────────────────────
@mask_bp.route("/<dest_id>/mask", methods=["GET"])
def mask(dest_id: str):
    """
    Example:
        GET /api/lobby_tv/mask   →  {"hex":"#FFD5B1","alpha":0.73,…}
        
    If no lat/long exists for the destination, returns a non-masking layer:
        {"hex":"#FFFFFF","alpha":0.0,"timestamp":"2023-03-14T12:00:00Z"}
    """
    dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
    dest = dests.get(dest_id)
    if not dest:
        warning(f"[mask] Destination '{dest_id}' not found in publish-destinations.json")
        abort(404, description=f"Unknown destination '{dest_id}'")

    # Check if lat/long coordinates exist for this destination
    if "lat" not in dest or "lon" not in dest:
        debug(f"[mask] No coordinates for '{dest_id}' - returning non-masking layer")
        # Return a non-masking layer (transparent white)
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        payload = {
            "hex": "#FFFFFF",
            "alpha": 0.0,
            "timestamp": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
        }
        return jsonify(payload)

    # If coordinates exist, calculate the ambient color
    lat = float(dest["lat"])
    lon = float(dest["lon"])
    debug(f"[mask] Calculating ambient color for '{dest_id}' at {lat}°N, {lon}°E")
    payload = ambient_rgba(lat, lon)
    
    # Log the debug information
    debug_info = payload.pop("_debug", {})  # Remove debug info from response
    debug(f"[mask] Ambient calculation for '{dest_id}':")
    debug(f"  • Solar elevation: {debug_info['solar_elevation']}°")
    debug(f"  • Raw index: {debug_info['raw_index']} (before smoothing)")
    debug(f"  • Smooth index: {debug_info['smooth_index']} (after smoothing)")
    debug(f"  • Color temp: {debug_info['kelvin']}K")
    debug(f"  • Alpha: {debug_info['alpha_raw']} (before rounding)")
    debug(f"  • Final color: {payload['hex']}")
    
    return jsonify(payload)

def send_overlay(
    html: str,
    screens: list[str] = None,
    duration: int = 5000,
    position: str = None,
    clear: bool = True,
    substitutions=None,
    fadein=0,
    job_id=None
):
    file_path = findfile(html)
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    else:
        html_content = html

    # JINJA2 substitution using dict_substitute
    if substitutions:
        try:
            if isinstance(substitutions, str):
                substitutions = ast.literal_eval(substitutions)
            if isinstance(substitutions, dict):
                final_html = dict_substitute(html_content, substitutions)
            else:
                warning("Substitutions is not a dict, skipping templating")
                final_html = html_content
        except Exception as e:
            error(f"Jinja2 template substitution error: {e}")
            final_html = html_content
    else:
        final_html = html_content

    data = {
        "screens": screens,
        "html": final_html,
        "duration": duration,
        "position": position,
        "substitutions": substitutions,
        "clear": clear,
        "fadein": fadein
    }

    if not job_id and not final_html:
        warning("Empty overlay message or missing job_id - skipping")
        return

    log_message = {**data}
    if "html" in log_message and isinstance(log_message["html"], str) and len(log_message["html"]) > 500:
        log_message["html"] = f"{log_message['html'][:500]}... [truncated, {len(data['html'])} chars]"
    debug(f"Sending overlay message: {log_message}")

    # Get the running event loop or create one if needed
    try:
        # Try to get the current event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # The event loop is already running, use run_coroutine_threadsafe
            future = asyncio.run_coroutine_threadsafe(send_overlay_to_clients(data), loop)
            # Optionally wait for the result if needed
            # future.result()
        else:
            # No running loop, we can use asyncio.run
            asyncio.run(send_overlay_to_clients(data))
    except RuntimeError:
        # No event loop in this thread, create a new one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(send_overlay_to_clients(data))
        loop.close()
    except Exception as e:
        error(f"Error sending overlay: {e}")