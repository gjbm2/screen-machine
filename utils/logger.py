
"""
Logger utility for the AI Image Generator app.
This module provides functions to log messages that will be visible
in both the server console and the frontend console view.
"""

import time
import sys
import os
import inspect
from typing import Optional, Dict, Any
from collections import defaultdict

# Initialize internal log storage (will be imported by app.py)
console_logs = []

# Track recent logs to prevent spam
_recent_logs = defaultdict(lambda: {"timestamp": 0, "count": 0})
_LOG_THROTTLE_TIME = 2  # seconds between identical log messages

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
            console_logs.append(formatted_message)
        return ""  # Return empty string for throttled messages
    
    # Reset counter if outside throttle window
    _recent_logs[log_key] = {"timestamp": current_time, "count": 1}
    
    # Format and log the message
    timestamp = time.strftime("%H:%M:%S", time.localtime())
    formatted_message = f"[{timestamp}] [{source}] {message}"
    
    # Print to server console
    print(formatted_message, file=sys.stdout)
    sys.stdout.flush()
    
    # Store for frontend retrieval (will be accessed by app.py)
    console_logs.append(formatted_message)
    
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
