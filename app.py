
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import time
import random
import uuid
import json
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='build')
CORS(app)  # Enable CORS for all routes

# Create uploads directory if it doesn't exist
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

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
        data_path = os.path.join('data', file_name)
        if os.path.exists(data_path):
            with open(data_path, 'r') as file:
                return json.load(file)
        else:
            # Try fallback to src/data for development
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
    # Extract JSON data from the form
    json_data = request.form.get('data')
    if not json_data:
        return jsonify({"error": "Missing data parameter"}), 400
    
    try:
        data = json.loads(json_data)
    except Exception as e:
        return jsonify({"error": f"Invalid JSON: {str(e)}"}), 400
    
    prompt = data.get('prompt', '')
    workflow = data.get('workflow', 'text-to-image')
    params = data.get('params', {})
    global_params = data.get('global_params', {})
    refiner = data.get('refiner', 'none')
    refiner_params = data.get('refiner_params', {})
    batch_size = data.get('batch_size', 1)
    has_reference_image = data.get('has_reference_image', False)
    
    # Handle uploaded files
    uploaded_files = []
    image_files = request.files.getlist('image')
    
    # Save uploaded files if any
    for file in image_files:
        if file.filename:
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            uploaded_files.append(filepath)
            has_reference_image = True

    # Validate input
    if not prompt and not has_reference_image:
        return jsonify({"error": "Prompt or reference image is required"}), 400
    
    print(f"Generating images with prompt: {prompt}")
    print(f"Workflow: {workflow}, Batch size: {batch_size}")
    print(f"Reference images: {uploaded_files if uploaded_files else 'None'}")
    
    # Simulate processing time
    time.sleep(2)
    
    # Generate a unique ID for this batch
    batch_id = data.get('batch_id') or str(uuid.uuid4())
    
    # Generate the requested number of images (based on batch_size)
    result_images = []
    
    for i in range(int(batch_size)):
        # For this mock implementation, we're returning different placeholder images based on the workflow
        if workflow == 'artistic-style-transfer':
            image_url = random.choice(ARTISTIC_IMAGES)
        else:
            image_url = random.choice(PLACEHOLDER_IMAGES)
        
        # Generate a unique ID for this image
        image_id = str(uuid.uuid4())
        
        # Store the image metadata in our dictionary
        image_data = {
            "id": image_id,
            "url": image_url,
            "prompt": prompt,
            "workflow": workflow,
            "timestamp": time.time(),
            "params": params,
            "global_params": global_params,
            "refiner": refiner,
            "refiner_params": refiner_params,
            "used_reference_image": has_reference_image,
            "batch_id": batch_id,
            "batch_index": i
        }
        
        generated_images[image_id] = image_data
        result_images.append(image_data)
    
    return jsonify({
        "success": True,
        "images": result_images,
        "batch_id": batch_id,
        "prompt": prompt,
        "workflow": workflow
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
