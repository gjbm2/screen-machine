
import os
import time
from flask import Blueprint, jsonify, current_app, request

routes_blueprint = Blueprint('routes', __name__)

# Keep track of last modification times to avoid unnecessary file scans
file_cache = {
    'last_check': 0,
    'files': [],
    'expiry': 60  # Increase cache expiry to 60 seconds
}

@routes_blueprint.route('/api/output-files', methods=['GET'])
def get_output_files():
    """Get a list of all files in the output directory."""
    output_dir = 'output'
    files = []
    
    try:
        # Check cache first to reduce file system operations
        current_time = int(time.time())
        cache_age = current_time - file_cache['last_check']
        
        # Return cached files if still valid
        if file_cache['files'] and cache_age < file_cache['expiry']:
            current_app.logger.info(f"Returning {len(file_cache['files'])} cached files (cache age: {cache_age}s)")
            return jsonify({
                'success': True,
                'files': file_cache['files'],
                'cached': True
            }), 200
        
        # Check if directory exists
        if os.path.exists(output_dir) and os.path.isdir(output_dir):
            # List all files in the directory
            for filename in os.listdir(output_dir):
                file_path = os.path.join(output_dir, filename)
                # Only include files, not directories
                if os.path.isfile(file_path):
                    # Add the file path with proper formatting
                    files.append(f"/output/{filename}")
            
            # Update cache
            file_cache['files'] = files
            file_cache['last_check'] = current_time
            
            current_app.logger.info(f"Found {len(files)} files in output directory")
        else:
            current_app.logger.warning(f"Output directory '{output_dir}' does not exist or is not a directory")
        
        # Always return a JSON response, never HTML
        return jsonify({
            'success': True,
            'files': files,
            'cached': False
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error accessing output directory: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'files': []
        }), 500
