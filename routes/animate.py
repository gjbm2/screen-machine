from flask import Blueprint, request, jsonify
from utils.logger import info
from utils.runtime import resolve_runtime_value
from routes.generate import handle_image_generation
import threading

animate_bp = Blueprint('animate_bp', __name__)

@animate_bp.route('/api/animate', methods=['POST'])
def animate():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        # Fetch relevant image inputs
        targets = data.get("data", {}).get("targets", []) if isinstance(data.get("data", {}).get("targets"), list) else []
        
        # Resolve the file associated with the first target
        target_image_file = resolve_runtime_value("destination", targets[0], return_key="file") if targets else None

        # Inject base64 image into result["data"]["images"]
        image_payload = get_image_from_target(target_image_file) if target_image_file else None
        data.setdefault("data", {})["images"] = [image_payload]

        info(
            f"Will address: {target_image_file}, "
            f"image present: {image_payload is not None}, "
            f"image length: {len(image_payload.get('image')) if image_payload else 'N/A'}"
        )
        
        # Ensure we're using the currently selected refiner
        data.setdefault("data", {})["refiner"] = resolve_runtime_value("refiner", "animate")
        
        # Run the refinement + generation flow in background
        threading.Thread(
            target=handle_image_generation,
            kwargs={
                "input_obj": data
            }
        ).start()

        return jsonify({"status": "animation_started"}), 202

    except Exception as e:
        return jsonify({"error": str(e)}), 500 