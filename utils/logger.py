"""
Logger utility for the AI Image Generator app.
This module provides functions to log messages that will be visible
in both the server console and the frontend console view.
"""

import time
import sys
import os
import inspect
import logging
import threading
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, Any
from collections import defaultdict

# Initialize internal log storage (will be imported by app.py)
console_logs = []

# Both buffers used to grow for the life of the process (multi-GB RSS after
# months of uptime). console_logs stays a list, trimmed in place, because
# importers slice it; _recent_logs only matters inside the throttle window.
MAX_CONSOLE_LOGS = 10000
_RECENT_LOGS_PRUNE_AT = 5000
_buffer_lock = threading.Lock()

# Track recent logs to prevent spam
_recent_logs = defaultdict(lambda: {"timestamp": 0, "count": 0})
_LOG_THROTTLE_TIME = 2  # seconds between identical log messages


def _store_console_log(formatted_message: str) -> None:
    with _buffer_lock:
        console_logs.append(formatted_message)
        if len(console_logs) > MAX_CONSOLE_LOGS + MAX_CONSOLE_LOGS // 10:
            del console_logs[:-MAX_CONSOLE_LOGS]


def _prune_recent_logs(current_time: float) -> None:
    with _buffer_lock:
        if len(_recent_logs) < _RECENT_LOGS_PRUNE_AT:
            return
        stale = [k for k, v in list(_recent_logs.items())
                 if current_time - v["timestamp"] >= _LOG_THROTTLE_TIME]
        for k in stale:
            _recent_logs.pop(k, None)

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Configure file logger
file_logger = logging.getLogger("screen_machine")
file_logger.setLevel(logging.DEBUG)

# Create rotating file handler (10MB per file, keep 5 backup files)
log_file = os.path.join(LOGS_DIR, "screen_machine.log")
file_handler = RotatingFileHandler(
    log_file,
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)
file_handler.setLevel(logging.DEBUG)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Add handler to logger
file_logger.addHandler(file_handler)

# Prevent propagation to root logger to avoid double logging
file_logger.propagate = False

def log_to_console(message: str, source: Optional[str] = None, metadata: Optional[Dict[Any, Any]] = None) -> str:
    """
    Log a message to the console and store it for retrieval by the frontend.
    Implements throttling to prevent log spam.
    
    Args:
        message: The message to log
        source: The source of the message (defaults to the calling module)
        metadata: Optional metadata to include with the log
        
    Returns:
        The formatted log message or empty string if throttled
    """
    if source is None:
        # Get the name of the calling function two levels up the stack
        stack = inspect.stack()
        if len(stack) > 2:
            caller_frame = stack[2]
            source = caller_frame.function
        else:
            source = "unknown"

    # Create a key for the log message to track duplicates
    log_key = f"{source}:{message}"
    current_time = time.time()
    
    # Check if this is a duplicate message within the throttle window
    log_info = _recent_logs[log_key]
    if current_time - log_info["timestamp"] < _LOG_THROTTLE_TIME:
        log_info["count"] += 1
        # Only log every 100th duplicate during spam
        if log_info["count"] % 100 == 0:
            throttled_message = f"[THROTTLED] {message} (repeated {log_info['count']} times)"
            timestamp = time.strftime("%H:%M:%S", time.localtime())
            formatted_message = f"[{timestamp}] [{source}] {throttled_message}"
            print(formatted_message, file=sys.stdout)
            sys.stdout.flush()
            _store_console_log(formatted_message)
            # Log throttled messages to file with WARNING level
            file_logger.warning(formatted_message)
        return ""  # Return empty string for throttled messages
    
    # Reset counter if outside throttle window
    _prune_recent_logs(current_time)
    _recent_logs[log_key] = {"timestamp": current_time, "count": 1}
    
    # Format and log the message
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    formatted_message = f"[{timestamp}] [{source}] {message}"
    
    # Print to server console
    print(formatted_message, file=sys.stdout)
    sys.stdout.flush()
    
    # Store for frontend retrieval (will be accessed by app.py)
    _store_console_log(formatted_message)
    
    # Log to file based on message level
    if message.startswith("DEBUG:"):
        file_logger.debug(message[6:])  # Remove "DEBUG: " prefix
    elif message.startswith("INFO:"):
        file_logger.info(message[5:])   # Remove "INFO: " prefix
    elif message.startswith("WARNING:"):
        file_logger.warning(message[8:])  # Remove "WARNING: " prefix
    elif message.startswith("ERROR:"):
        file_logger.error(message[6:])  # Remove "ERROR: " prefix
    else:
        file_logger.info(message)
    
    return formatted_message

def debug(message: str, **kwargs):
    """Log a debug message"""
    return log_to_console(f"DEBUG: {message}", **kwargs)

def info(message: str, **kwargs):
    """Log an info message"""
    return log_to_console(f"INFO: {message}", **kwargs)

def warning(message: str, **kwargs):
    """Log a warning message"""
    return log_to_console(f"WARNING: {message}", **kwargs)

def error(message: str, **kwargs):
    """Log an error message"""
    return log_to_console(f"ERROR: {message}", **kwargs)
