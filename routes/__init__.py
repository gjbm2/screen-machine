
import os
from flask import Blueprint, jsonify, current_app

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
            
            current_app.logger.info(f"Found {len(files)} files in output directory")
        else:
            current_app.logger.warning(f"Output directory '{output_dir}' does not exist or is not a directory")
        
        return jsonify({
            'success': True,
            'files': files
        }), 200
    except Exception as e:
        current_app.logger.error(f"Error accessing output directory: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e),
            'files': []
        }), 500
