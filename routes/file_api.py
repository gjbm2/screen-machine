from flask import Blueprint, request, jsonify, abort
import os
import json
from pathlib import Path
from typing import List, Optional

file_bp = Blueprint('file_api', __name__)

# Base directories that are allowed to be accessed
ALLOWED_BASE_DIRS = [
    os.path.join(os.getcwd(), "routes/scheduler/scripts"),  # Script templates
    os.path.join(os.getcwd(), "examples"),  # Example files
    os.path.join(os.getcwd(), "configs"),  # Configuration files
]

def resolve_path(path: str) -> str:
    """Convert a relative path to absolute path based on current working directory."""
    # If path is already absolute, return it
    if os.path.isabs(path):
        return path
    
    # Strip leading slash if present to ensure it's treated as relative
    if path.startswith('/'):
        path = path.lstrip('/')
    
    # Join with the current working directory
    return os.path.join(os.getcwd(), path)

def is_safe_path(path: str) -> bool:
    """Check if a path is safe to access (within allowed directories)."""
    # Convert to absolute path
    abs_path = os.path.abspath(resolve_path(path))
    
    # Check if it's within allowed directories
    return any(abs_path.startswith(base_dir) for base_dir in ALLOWED_BASE_DIRS)

@file_bp.route('/api/files', methods=['GET'])
def list_files():
    """List files in a directory."""
    directory = request.args.get('directory')
    if not directory:
        return jsonify({"error": "Missing directory parameter"}), 400
    
    # Resolve the directory path
    full_directory = resolve_path(directory)
        
    if not is_safe_path(full_directory):
        return jsonify({"error": f"Access to directory '{directory}' is not allowed"}), 403
    
    try:
        # Ensure directory exists
        if not os.path.exists(full_directory):
            os.makedirs(full_directory, exist_ok=True)
            
        # Get files in directory
        files = []
        for item in os.listdir(full_directory):
            item_path = os.path.join(full_directory, item)
            if os.path.isfile(item_path):
                files.append(item)
                
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": f"Error listing files: {str(e)}"}), 500

@file_bp.route('/api/files/<path:file_path>', methods=['GET'])
def read_file(file_path):
    """Read a file's contents."""
    # Resolve the file path
    full_file_path = resolve_path(file_path)
    
    if not is_safe_path(full_file_path):
        return jsonify({"error": f"Access to file '{file_path}' is not allowed"}), 403
    
    try:
        if not os.path.exists(full_file_path):
            return jsonify({"error": f"File '{file_path}' not found"}), 404
            
        # Read file
        with open(full_file_path, "r") as f:
            content = f.read()
            
        # Try to parse as JSON if it's a JSON file
        if full_file_path.endswith(".json"):
            try:
                return jsonify(json.loads(content))
            except json.JSONDecodeError:
                # Return raw content if not valid JSON
                return jsonify({"content": content})
        else:
            return jsonify({"content": content})
    except Exception as e:
        return jsonify({"error": f"Error reading file: {str(e)}"}), 500

@file_bp.route('/api/files', methods=['POST'])
def write_file():
    """Write content to a file."""
    data = request.json
    if not data or 'path' not in data or 'content' not in data:
        return jsonify({"error": "Missing path or content parameters"}), 400
        
    path = data['path']
    content = data['content']
    
    # Resolve the file path
    full_path = resolve_path(path)
    
    if not is_safe_path(full_path):
        return jsonify({"error": f"Writing to path '{path}' is not allowed"}), 403
    
    try:
        # Ensure directory exists
        directory = os.path.dirname(full_path)
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)
            
        # Write to file
        with open(full_path, "w") as f:
            f.write(content)
            
        return jsonify({"success": True, "path": path})
    except Exception as e:
        return jsonify({"error": f"Error writing file: {str(e)}"}), 500 