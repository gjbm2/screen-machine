from flask import Flask, request, jsonify
import os
import requests
from werkzeug.utils import secure_filename
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Add new route for publishing images
@app.route('/api/publish-image', methods=['POST'])
def publish_image():
    try:
        data = request.json
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400
        
        image_url = data.get('imageUrl')
        destination = data.get('destination')
        destination_type = data.get('destinationType')
        destination_file = data.get('destinationFile')
        metadata = data.get('metadata', {})
        
        if not image_url:
            return jsonify({"success": False, "error": "No image URL provided"}), 400
        
        if not destination:
            return jsonify({"success": False, "error": "No destination provided"}), 400
        
        # Download the image
        try:
            image_response = requests.get(image_url)
            if image_response.status_code != 200:
                return jsonify({"success": False, "error": f"Failed to download image: HTTP {image_response.status_code}"}), 500
            
            image_data = image_response.content
        except Exception as e:
            logging.error(f"Error downloading image: {str(e)}")
            return jsonify({"success": False, "error": f"Failed to download image: {str(e)}"}), 500
        
        # Handle based on destination type
        if destination_type == "output_file":
            if not destination_file:
                return jsonify({"success": False, "error": "No destination file specified for output_file type"}), 400
            
            try:
                # Ensure the output directory exists
                output_dir = "published_images"
                os.makedirs(output_dir, exist_ok=True)
                
                # Secure the filename and save
                safe_filename = secure_filename(destination_file)
                output_path = os.path.join(output_dir, safe_filename)
                
                with open(output_path, 'wb') as f:
                    f.write(image_data)
                
                return jsonify({
                    "success": True, 
                    "message": f"Image published to {output_path}",
                    "path": output_path
                })
            except Exception as e:
                logging.error(f"Error saving to file: {str(e)}")
                return jsonify({"success": False, "error": f"Failed to save image: {str(e)}"}), 500
        
        elif destination_type == "s3":
            # This would be implemented with boto3/AWS SDK
            # For now, we'll just mock the response
            logging.info(f"Mock S3 upload for file: {destination_file}")
            return jsonify({
                "success": True, 
                "message": f"Image published to S3 (mock)",
                "path": f"s3://mock-bucket/{destination_file}"
            })
        
        else:
            return jsonify({"success": False, "error": f"Unsupported destination type: {destination_type}"}), 400
        
    except Exception as e:
        logging.error(f"Error in publish endpoint: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 500
