import asyncio
import json
import ast
import os
from routes.utils import findfile, dict_substitute
from overlay_ws_server import send_overlay_to_clients
from utils.logger import log_to_console, info, error, warning, debug, console_logs
from datetime import datetime
import uuid

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