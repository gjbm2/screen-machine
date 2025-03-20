
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import time
import random
import uuid
import json

app = Flask(__name__, static_folder='build')
CORS(app)  # Enable CORS for all routes

# In-memory dictionary to store generated images
generated_images = {}

# Sample placeholder images (replace with actual implementation)
PLACEHOLDER_IMAGES = [
    "https://images.unsplash.com/photo-1543466835-00a7907e9de1?q=80&w=1974&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1605979257913-1704eb7b6246?q=80&w=1770&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1692891873526-61e7e87ea428?q=80&w=1780&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1533134486753-c833f0ed4866?q=80&w=1770&auto=format&fit=crop"
]

# Artistic style images
ARTISTIC_IMAGES = [
    "https://images.unsplash.com/photo-1579783902614-a3fb3927b6a5?q=80&w=1945&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1549289524-06cf8837ace5?q=80&w=1974&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1591267990532-e5bdb1b0ceb8?q=80&w=1974&auto=format&fit=crop"
]

# Load workflow data from JSON files
def load_json_data(file_name):
    try:
        with open(f'src/data/{file_name}', 'r') as file:
            return json.load(file)
    except Exception as e:
        print(f"Error loading {file_name}: {e}")
        return []

workflows_data = load_json_data('workflows.json')
refiners_data = load_json_data('refiners.json')
refiner_params_data = load_json_data('refiner-params.json')
global_options_data = load_json_data('global-options.json')

@app.route('/api/generate-image', methods=['POST'])
def generate_image():
    # Get the data from the request
    data = request.get_json()
    prompt = data.get('prompt', '')
    has_reference_image = data.get('has_reference_image', False)
    workflow = data.get('workflow', 'text-to-image')
    params = data.get('params', {})
    global_params = data.get('global_params', {})
    refiner = data.get('refiner', 'none')
    refiner_params = data.get('refiner_params', {})
    
    if not prompt and not has_reference_image:
        return jsonify({"error": "Prompt or reference image is required"}), 400
    
    # Simulate processing time
    time.sleep(2)
    
    # For this mock implementation, we're returning different placeholder images based on the workflow
    if workflow == 'artistic-style-transfer':
        image_url = random.choice(ARTISTIC_IMAGES)
    else:
        image_url = random.choice(PLACEHOLDER_IMAGES)
    
    # Generate a unique ID for this image
    image_id = str(uuid.uuid4())
    
    # Store the image metadata in our dictionary
    generated_images[image_id] = {
        "id": image_id,
        "url": image_url,
        "prompt": prompt,
        "workflow": workflow,
        "timestamp": time.time(),
        "params": params,
        "global_params": global_params,
        "refiner": refiner,
        "refiner_params": refiner_params,
        "used_reference_image": has_reference_image
    }
    
    # Return the generated image data
    return jsonify({
        "success": True,
        "image_id": image_id,
        "prompt": prompt,
        "workflow": workflow,
        "params": params,
        "image_url": image_url,
        "used_reference_image": has_reference_image
    })

@app.route('/api/images', methods=['GET'])
def get_images():
    # Return all stored images, sorted by timestamp (newest first)
    sorted_images = sorted(
        list(generated_images.values()), 
        key=lambda x: x["timestamp"], 
        reverse=True
    )
    return jsonify({"images": sorted_images})

@app.route('/api/images/<image_id>', methods=['GET'])
def get_image(image_id):
    # Return a specific image by ID
    if image_id in generated_images:
        return jsonify(generated_images[image_id])
    return jsonify({"error": "Image not found"}), 404

@app.route('/api/workflows', methods=['GET'])
def get_workflows():
    return jsonify({"workflows": workflows_data})

@app.route('/api/refiners', methods=['GET'])
def get_refiners():
    return jsonify({"refiners": refiners_data})

@app.route('/api/refiner-params/<refiner_id>', methods=['GET'])
def get_refiner_params(refiner_id):
    refiner_data = next((r for r in refiner_params_data if r["id"] == refiner_id), None)
    if refiner_data:
        return jsonify(refiner_data)
    return jsonify({"error": "Refiner not found"}), 404

@app.route('/api/global-options', methods=['GET'])
def get_global_options():
    return jsonify({"global_options": global_options_data})

# Serve the React frontend
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        return send_from_directory(app.static_folder, path)
    else:
        return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
