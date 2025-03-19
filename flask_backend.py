
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import time
import random
import uuid

app = Flask(__name__)
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

@app.route('/generate-image', methods=['POST'])
def generate_image():
    # Get the data from the request
    data = request.get_json()
    prompt = data.get('prompt', '')
    has_reference_image = data.get('has_reference_image', False)
    workflow = data.get('workflow', 'text-to-image')
    params = data.get('params', {})
    global_params = data.get('global_params', {})
    
    if not prompt and not has_reference_image:
        return jsonify({"error": "Prompt or reference image is required"}), 400
    
    # Simulate processing time
    time.sleep(2)
    
    # In a real implementation, you would:
    # 1. Handle the uploaded image file if present
    # 2. Pass both the prompt and the image to your AI model for img2img generation
    # 3. Return the generated image
    
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

@app.route('/images', methods=['GET'])
def get_images():
    # Return all stored images, sorted by timestamp (newest first)
    sorted_images = sorted(
        generated_images.values(), 
        key=lambda x: x["timestamp"], 
        reverse=True
    )
    return jsonify({"images": sorted_images})

@app.route('/images/<image_id>', methods=['GET'])
def get_image(image_id):
    # Return a specific image by ID
    if image_id in generated_images:
        return jsonify(generated_images[image_id])
    return jsonify({"error": "Image not found"}), 404

if __name__ == '__main__':
    app.run(debug=True)
