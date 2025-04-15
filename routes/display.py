
import asyncio
import json
import ast
import os
from routes.utils import findfile
from overlay_ws_server import send_overlay_to_clients
from utils.logger import log_to_console, info, error, warning, debug, console_logs

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
    # Load from file if it exists
    file_path = findfile(html)
    if file_path and os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            html_content = f.read()
    else:
        html_content = html

    # Parse substitutions if passed
    final_html = html_content
    if substitutions:
        try:
            if isinstance(substitutions, str):
                substitutions = ast.literal_eval(substitutions)
            if isinstance(substitutions, dict):
                for key, value in substitutions.items():
                    final_html = final_html.replace(key, str(value))
        except Exception as e:
            error(f"Substitution parse error: {e}")

    # Prepare and send message
    message = {
        "screens": screens,
        "html": final_html,
        "duration": duration,
        "position": position,
        "clear": clear,
        "fadein": fadein,
        "job_id": job_id
    }

    if not job_id and not final_html:
        warning("Empty overlay message or missing job_id - skipping")
        return
        
    # Log a sanitized version of the message (to avoid huge HTML dumps in logs)
    log_message = {**message}
    if "html" in log_message and isinstance(log_message["html"], str) and len(log_message["html"]) > 50:
        log_message["html"] = f"{log_message['html'][:50]}... [truncated, {len(message['html'])} chars]"
    
    debug(f"Sending overlay message: {log_message}")
    
    asyncio.run(send_overlay_to_clients(message))
