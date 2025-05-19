"""
Configuration settings for the Screen Machine application.
This file centralizes all configuration constants and settings.
"""

import os
from pathlib import Path

# Application paths
ROOT_DIR = Path(__file__).parent
STATIC_FOLDER = 'build'
OUTPUT_DIR = 'output'

# Server settings
HOST = "0.0.0.0"
PORT = 5000
DEBUG = True
WS_PORT = 8765

# API settings
API_PREFIX = '/api'
LOG_LIMIT = 100  # Default number of logs to return

# File settings
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'webm'}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size 

# Scheduler settings
SCHEDULER_TICK_INTERVAL = 2.0  # Seconds between scheduler trigger checks
SCHEDULER_TICK_BUFFER = 0.5    # Additional buffer time for scheduler operations 
MAX_EVENT_HISTORY = 100
# Path to the exported variables registry
VARS_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "scheduler", "_vars.json")