import json
import os
import time
from typing import Dict, List, Optional, Tuple
from flask import Blueprint, jsonify, request
import logging

# Initialize logging
logging.basicConfig(level=logging.WARNING)  # Change to WARNING level
logger = logging.getLogger(__name__)

# Initialize Blueprint
lightsensor_bp = Blueprint('lightsensor', __name__)

# Global variables for tracking lux levels
lux_history: Dict[str, List[Tuple[float, float]]] = {}  # sensor_name -> [(timestamp, lux)]
MAX_HISTORY_LENGTH = 1000  # Maximum number of entries to keep per sensor
LUX_SMOOTHING_WINDOW = 5  # Number of recent values to average

def get_smoothed_lux(sensor_name: str, new_lux: float) -> float:
    """Calculate smoothed lux value using a moving average of recent values."""
    if sensor_name not in lux_history or not lux_history[sensor_name]:
        return new_lux
    
    # Get recent values (up to LUX_SMOOTHING_WINDOW)
    recent_values = [lux for _, lux in lux_history[sensor_name][-LUX_SMOOTHING_WINDOW:]]
    recent_values.append(new_lux)
    
    # Calculate moving average
    return sum(recent_values) / len(recent_values)

class IntensityMapper:
    def __init__(self):
        self.mapping_data = None
        self.last_update = 0
        self.smoothed_values = {}  # Track smoothed values per sensor
        self.alpha = 0.2  # Smoothing factor for intensity
        self.load_mapping()

    def load_mapping(self):
        try:
            mapping_path = os.path.join(os.path.dirname(__file__), 'data', 'intensity.json')
            if os.path.exists(mapping_path):
                with open(mapping_path, 'r') as f:
                    self.mapping_data = json.load(f)
                self.last_update = os.path.getmtime(mapping_path)
                logger.debug(f"Loaded intensity mapping from {mapping_path}")  # Changed to debug
        except Exception as e:
            logger.error(f"Error loading intensity mapping: {e}")
            self.mapping_data = None

    def save_mapping(self):
        """Save the current mapping data back to the file."""
        try:
            mapping_path = os.path.join(os.path.dirname(__file__), 'data', 'intensity.json')
            with open(mapping_path, 'w') as f:
                json.dump(self.mapping_data, f, indent=2)
            self.last_update = os.path.getmtime(mapping_path)
            logger.debug(f"Saved intensity mapping to {mapping_path}")
            return True
        except Exception as e:
            logger.error(f"Error saving intensity mapping: {e}")
            return False

    def check_for_updates(self):
        mapping_path = os.path.join(os.path.dirname(__file__), 'data', 'intensity.json')
        if os.path.exists(mapping_path):
            current_mtime = os.path.getmtime(mapping_path)
            if current_mtime > self.last_update:
                self.load_mapping()

    def get_intensity(self, sensor_name: str, lux: float) -> Optional[float]:
        if not self.mapping_data or sensor_name not in self.mapping_data["sensor_mappings"]:
            return None

        # Get smoothed lux value
        smoothed_lux = get_smoothed_lux(sensor_name, lux)

        mapping = self.mapping_data["sensor_mappings"][sensor_name]
        points = mapping["lux_to_intensity"]
        
        # Sort points by lux value
        points.sort(key=lambda x: x["lux"])
        
        # Find the two points to interpolate between
        for i in range(len(points) - 1):
            if points[i]["lux"] <= smoothed_lux <= points[i + 1]["lux"]:
                # Linear interpolation
                x0, x1 = points[i]["lux"], points[i + 1]["lux"]
                y0, y1 = points[i]["intensity"], points[i + 1]["intensity"]
                interpolated = y0 + (y1 - y0) * (smoothed_lux - x0) / (x1 - x0)
                
                # Apply smoothing to intensity
                if sensor_name not in self.smoothed_values:
                    self.smoothed_values[sensor_name] = interpolated
                else:
                    self.smoothed_values[sensor_name] = (
                        self.alpha * interpolated + 
                        (1 - self.alpha) * self.smoothed_values[sensor_name]
                    )
                
                return self.smoothed_values[sensor_name]
        
        # Handle out of range values
        if smoothed_lux <= points[0]["lux"]:
            return points[0]["intensity"]
        return points[-1]["intensity"]

    def get_target_group(self, sensor_name: str) -> Optional[str]:
        if not self.mapping_data or sensor_name not in self.mapping_data["sensor_mappings"]:
            return None
        return self.mapping_data["sensor_mappings"][sensor_name]["target_group"]

    def update_mapping(self, sensor_name: str, points: List[Dict[str, float]], target_group: str) -> bool:
        """Update the mapping for a specific sensor."""
        if not self.mapping_data:
            self.mapping_data = {"sensor_mappings": {}}
        
        # Validate points
        if not points or not all("lux" in p and "intensity" in p for p in points):
            return False
        
        # Sort points by lux value
        points.sort(key=lambda x: x["lux"])
        
        # Update the mapping
        self.mapping_data["sensor_mappings"][sensor_name] = {
            "lux_to_intensity": points,
            "target_group": target_group
        }
        
        # Save to file
        return self.save_mapping()

# Initialize the intensity mapper
intensity_mapper = IntensityMapper()

@lightsensor_bp.route('/lightsensor/intensity-settings', methods=['GET'])
def get_intensity_settings():
    """Get current intensity settings for all sensors."""
    intensity_mapper.check_for_updates()
    return jsonify(intensity_mapper.mapping_data)

@lightsensor_bp.route('/lightsensor/intensity-settings/<sensor_name>', methods=['PUT'])
def update_intensity_settings(sensor_name):
    """Update intensity settings for a specific sensor."""
    try:
        data = request.json
        if not data or "points" not in data or "target_group" not in data:
            return jsonify({"error": "Missing required fields"}), 400
        
        success = intensity_mapper.update_mapping(
            sensor_name,
            data["points"],
            data["target_group"]
        )
        
        if success:
            return jsonify({"status": "success"})
        else:
            return jsonify({"error": "Failed to update settings"}), 500
            
    except Exception as e:
        logger.error(f"Error updating intensity settings: {e}")
        return jsonify({"error": str(e)}), 500

async def broadcast_lux_level(sensor_name: str, lux: float):
    """Broadcast lux level to all connected clients with sensor name."""
    from overlay_ws_server import send_overlay_to_clients

    # Check for mapping updates
    intensity_mapper.check_for_updates()
    
    # Get target intensity if mapping exists
    target_intensity = intensity_mapper.get_intensity(sensor_name, lux)
    target_group = intensity_mapper.get_target_group(sensor_name)

    # Store in history
    timestamp = time.time()
    if sensor_name not in lux_history:
        lux_history[sensor_name] = []
    lux_history[sensor_name].append((timestamp, lux))
    
    # Trim history if needed
    if len(lux_history[sensor_name]) > MAX_HISTORY_LENGTH:
        lux_history[sensor_name] = lux_history[sensor_name][-MAX_HISTORY_LENGTH:]

    # Prepare message
    message = {
        'sensor_name': sensor_name,
        'lux': lux,
        'timestamp': timestamp,
        'target_intensity': target_intensity,
        'target_group': target_group
    }

    # Broadcast to all connected clients
    await send_overlay_to_clients(message)
    logger.debug(f"Broadcast lux level for {sensor_name}: {lux}")  # Changed to debug

@lightsensor_bp.route('/lightsensor/lightsense', methods=['GET'])
def get_lightsense():
    """Get current lux levels, history, and target intensities for all sensors."""
    # Check for mapping updates
    intensity_mapper.check_for_updates()
    
    return jsonify({
        'sensors': {
            sensor_name: {
                'current': {
                    'lux': history[-1][1] if history else None,
                    'target_intensity': intensity_mapper.get_intensity(sensor_name, history[-1][1]) if history else None,
                    'target_group': intensity_mapper.get_target_group(sensor_name)
                },
                'history': [
                    {
                        'timestamp': ts,
                        'lux': lux,
                        'target_intensity': intensity_mapper.get_intensity(sensor_name, lux)
                    }
                    for ts, lux in history
                ]
            }
            for sensor_name, history in lux_history.items()
        }
    })

def init_app(app):
    """Initialize the lightsensor blueprint with the app."""
    app.register_blueprint(lightsensor_bp) 