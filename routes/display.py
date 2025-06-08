from __future__ import annotations
import asyncio
import json
import ast
import os
from pathlib import Path
from flask import Blueprint, jsonify, abort
from routes.utils import findfile, dict_substitute, _load_json_once
from routes.display_utils import compute_mask
from overlay_ws_server import send_overlay_to_clients
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from datetime import datetime, timezone
import uuid

# Create Blueprint for mask routes
mask_bp = Blueprint("mask", __name__)

# Store mask state for each destination
mask_states = {}  # {dest_id: bool} - True = mask on, False = mask off

# Store last output values per screen
_last_output_by_screen = {}  # {dest_id: dict}

# Store last debug output per screen
_last_debug = {}  # {dest_id: str}

def _get_last_output(dest_id: str) -> dict:
    """Get the last output for a destination, initializing if needed."""
    if dest_id not in _last_output_by_screen:
        _last_output_by_screen[dest_id] = {
            "brightness": None,
            "warm_alpha": None,
            "warm_hex": None
        }
    return _last_output_by_screen[dest_id]

# ───────────────────── routes ─────────────────────────────────
@mask_bp.route("/<dest_id>/maskon", methods=["POST"])
def mask_on(dest_id: str):
    """Enable masking for a destination."""
    dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
    if dest_id not in dests:
        warning(f"[mask_on] Destination '{dest_id}' not found in publish-destinations.json")
        abort(404, description=f"Unknown destination '{dest_id}'")
    
    mask_states[dest_id] = True
    debug(f"[mask_on] Enabled masking for '{dest_id}'")
    return jsonify({"status": "enabled", "destination": dest_id})

@mask_bp.route("/<dest_id>/maskoff", methods=["POST"])
def mask_off(dest_id: str):
    """Disable masking for a destination."""
    dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
    if dest_id not in dests:
        warning(f"[mask_off] Destination '{dest_id}' not found in publish-destinations.json")
        abort(404, description=f"Unknown destination '{dest_id}'")
    
    mask_states[dest_id] = False
    debug(f"[mask_off] Disabled masking for '{dest_id}'")
    return jsonify({"status": "disabled", "destination": dest_id})

@mask_bp.route("/<dest_id>/maskstate", methods=["GET"])
def mask_state(dest_id: str):
    """Get the current mask state for a destination."""
    dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
    if dest_id not in dests:
        warning(f"[mask_state] Destination '{dest_id}' not found in publish-destinations.json")
        abort(404, description=f"Unknown destination '{dest_id}'")
    
    # Default to True if not explicitly set
    enabled = mask_states.get(dest_id, True)
    debug(f"[mask_state] Mask state for '{dest_id}': {enabled}")
    return jsonify({"enabled": enabled, "destination": dest_id})

@mask_bp.route("/<dest_id>/mask", methods=["GET"])
def mask(dest_id: str):
    """
    Example:
        GET /api/lobby_tv/mask   →  {"brightness":0.42,"warm_hex":"#F6D0B5",…}
        
    If intensity_cfg.adjust is False or not present, returns a non-masking layer:
        {"brightness":1.0,"warm_hex":"#FFFFFF","warm_alpha":0.0,…}
        
    If masking is disabled for the destination, returns a non-masking layer:
        {"brightness":1.0,"warm_hex":"#FFFFFF","warm_alpha":0.0,…}
    """
    dests = {dest["id"]: dest for dest in _load_json_once("destination", "publish-destinations.json")}
    dest = dests.get(dest_id)
    if not dest:
        warning(f"[mask] Destination '{dest_id}' not found in publish-destinations.json")
        abort(404, description=f"Unknown destination '{dest_id}'")

    # Check if masking is disabled for this destination
    if dest_id in mask_states and not mask_states[dest_id]:
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        payload = {
            "brightness": 1.0,  # No dimming (100% bright)
            "warm_hex": "#FFFFFF",
            "warm_alpha": 0.0,
            "timestamp": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
        }
    # Check if this destination has intensity adjustment enabled
    elif not dest.get("intensity_cfg", {}).get("adjust", False):
        now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
        payload = {
            "brightness": 1.0,  # No dimming (100% bright)
            "warm_hex": "#FFFFFF",
            "warm_alpha": 0.0,
            "timestamp": now_utc.isoformat(timespec="seconds").replace("+00:00", "Z")
        }
    else:
        # If adjustment is enabled, calculate the mask
        lat = float(dest["intensity_cfg"]["lat"])
        lon = float(dest["intensity_cfg"]["lon"])
        screen_cfg = {**dest["intensity_cfg"], "id": dest_id}  # Include screen ID in config
        payload = compute_mask(lat, lon, screen_cfg=screen_cfg)

    # Build debug output with aggressively rounded values
    debug_output = f"Mask values for {dest_id}:"
    if "_debug" in payload:
        # Round warm color to nearest 16 (one hex digit)
        r, g, b = int(payload['warm_hex'][1:3], 16), int(payload['warm_hex'][3:5], 16), int(payload['warm_hex'][5:7], 16)
        r, g, b = round(r/16)*16, round(g/16)*16, round(b/16)*16
        warm_hex = f"#{r:02x}{g:02x}{b:02x}"
        
        debug_output = f"Mask values for {dest_id}: idx={round(payload['_debug']['idx_skewed'], 2)} bright={round(payload['brightness'], 2)} warm={round(payload['warm_alpha'], 2)} bias={round(payload['_debug']['bias'], 1)} power={round(payload['_debug']['power'], 2)} elev={round(payload['_debug']['elev'], 0)} color={warm_hex}"
    else:
        debug_output = f"Mask values for {dest_id}: bright={round(payload['brightness'], 2)} warm={round(payload['warm_alpha'], 2)} color={payload['warm_hex']}"

    # Only output if changed
    if debug_output != _last_debug.get(dest_id):
        debug(debug_output)
        _last_debug[dest_id] = debug_output
    
    # Remove debug info from response
    payload.pop("_debug", None)
    
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