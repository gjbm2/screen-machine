
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

# Initialize internal log storage (will be imported by app.py)
console_logs = []

def log_to_console(message: str, source: Optional[str] = None, metadata: Optional[Dict[Any, Any]] = None) -> str:
    """
    Log a message to the console and store it for retrieval by the frontend.
    
    Args:
        message: The message to log
        source: The source of the message (defaults to the calling module)
        metadata: Optional metadata to include with the log
        
    Returns:
        The formatted log message
    """
    if source is None:
        # Try to determine the calling module
        frame = inspect.currentframe()
        if frame:
            frame = frame.f_back
            if frame:
                module = inspect.getmodule(frame)
                if module:
                    source = module.__name__
        
        if not source:
            source = "unknown"
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
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
