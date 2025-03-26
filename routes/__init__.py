
import os
from flask import Blueprint, jsonify

routes_blueprint = Blueprint('routes', __name__)

@routes_blueprint.route('/api/output-files', methods=['GET'])
def get_output_files():
    """Get a list of all files in the output directory."""
    output_dir = 'output'
    files = []
    
    try:
        # Check if directory exists
        if os.path.exists(output_dir) and os.path.isdir(output_dir):
            # List all files in the directory
            for filename in os.listdir(output_dir):
                file_path = os.path.join(output_dir, filename)
                # Only include files, not directories
                if os.path.isfile(file_path):
                    # Add the file path with proper formatting
                    files.append(f"/output/{filename}")
        
        return jsonify({
            'success': True,
            'files': files
        }), 200
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'files': []
        }), 500
